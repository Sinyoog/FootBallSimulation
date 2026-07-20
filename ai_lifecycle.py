"""
ai_lifecycle.py — AI 선수 생애 주기 시스템

시즌 종료 시(_end_of_season) 한 번 호출되어 다음을 처리한다:
  1. 나이 +1
  2. 성장(젊은 선수 OVR↑) / 노화(노쇠 선수 OVR↓)
  3. 은퇴(고령) → 신인으로 교체
  4. 이적 시장 (선수들 팀 간 이동 — 활발하게)
  5. 포메이션 변경 (일부 팀, 감독 교체 컨셉)

결과적으로 같은 팀에 오래 있어도 매 시즌 스쿼드/전력/포메가 살아 움직인다.
ai_players.ovr / team_id 가 바뀌므로 마지막에 OVR 캐시를 무효화해야 한다.

설계 메모:
  - 내(my_player)와 무관. 오직 ai_players / teams 만 건드린다.
  - calc_ovr·_gen_ai_stats·_target_ovr 등 database.py의 기존 생성 로직을 재사용.
  - 노화/성장은 '스탯' 자체를 조정하고 ovr를 재계산한다(스탯-ovr 일관성 유지).
"""
import random
import math
from database import (get_conn, calc_ovr, ALL_STATS, KEY_STATS_BY_POS)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# ── 나이 분포/임계값 ──────────────────────────────────────────
_AI_MIN_AGE      = 16
_AI_NEWBIE_AGE   = (16, 21)   # 신인 영입 연령대
_AI_PEAK_START   = 24         # 성장 종료(피크 진입)
_AI_PEAK_END     = 29         # 노화 시작
_AI_RETIRE_AGE   = 33         # 이 나이 이상이면 은퇴 확률 급증

# 포메이션 후보 (감독 교체 시 랜덤 선택)
_FORMATIONS = ["4-4-2", "4-3-3", "4-2-3-1", "3-5-2", "4-1-4-1", "3-4-3", "5-3-2"]

# ALL_STATS 인덱스 선조회 (반복 list.index 방지)
_STAT_COLS = ",".join(ALL_STATS)
_PHYS_STATS = {"stamina", "speed", "jump", "strength"}

if _HAS_NUMPY:
    from database import STAT_IDX, _WEIGHT_IDX_ITEMS, _WEIGHT_SUMS
    _N_STATS = len(ALL_STATS)
    _PHYS_IDX_NP = np.array([STAT_IDX[s] for s in ["stamina", "speed", "jump", "strength"]])
    _DEFAULT_KEY_IDX_NP = np.array([STAT_IDX[s] for s in ALL_STATS[:5]])
    _KEY_IDX_BY_POS_NP = {
        pos: np.array([STAT_IDX[s] for s in keys]) for pos, keys in KEY_STATS_BY_POS.items()
    }
    # 포지션별 OVR 가중치를 (15,) 벡터로 1회 캐싱 (매 시즌 재구성 방지)
    _WEIGHT_VEC_NP = {}
    for _pos, _items in _WEIGHT_IDX_ITEMS.items():
        _wv = np.zeros(_N_STATS)
        for _idx, _wt in _items:
            _wv[_idx] = _wt
        _WEIGHT_VEC_NP[_pos] = _wv


def run_ai_offseason(year, verbose_log=None):
    """시즌 종료 시 1회 호출. AI 선수 생애주기 전체 처리.
    verbose_log: add_log 함수(있으면 요약 한 줄 남김)."""
    import time as _time_perf
    conn = get_conn()
    c = conn.cursor()

    _ensure_ai_ages(c)               # 구버전 세이브 age 보정
    _ensure_ai_sub_roles(c)          # 구버전 세이브 sub_role 보정
    _ta0 = _time_perf.perf_counter()
    grew, aged = _age_and_progress(c)   # 자체적으로 전용 컬럼 SELECT (포지션 위치접근 최적화라 별도 유지)
    _ta1 = _time_perf.perf_counter()

    # [최적화] _retire_and_replace와 _transfer_market이 각자 따로 부르던
    # "SELECT ... FROM ai_players"(전체 행) 2회를 1회로 통합해 공유한다.
    # 두 함수가 필요로 하는 컬럼(id,team_id,position,age,name,ovr)이 동일
    # 상위집합이라 안전하게 합칠 수 있다 — 로직/결과는 완전히 동일, 풀스캔
    # 횟수만 3회→2회로 감소. (ovr은 _transfer_market의 실력 기반 이적 가중치용)
    shared_ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality FROM ai_players").fetchall()

    retired    = _retire_and_replace(c, year, shared_ai_rows)
    _ta2 = _time_perf.perf_counter()
    moved      = _transfer_market(c, shared_ai_rows)
    _ta3 = _time_perf.perf_counter()
    formations = _shuffle_formations(c)
    _ta4 = _time_perf.perf_counter()
    # [2026-07 신설, 진단용] game_engine._advance_week의 [PERF] 로그와 짝을
    # 이루는 세부 단계 측정 — "AI생애주기 N초" 중 실제로 어느 서브단계
    # (성장/은퇴·세대교체/이적시장/전술변경)가 무거운지 콘솔에서 바로 보인다.
    print(f"[PERF]     ai_offseason 세부: 성장/노화 {_ta1-_ta0:.2f}s | "
          f"은퇴·세대교체 {_ta2-_ta1:.2f}s | 이적시장 {_ta3-_ta2:.2f}s | "
          f"전술변경 {_ta4-_ta3:.2f}s")

    conn.commit()
    conn.close()

    # OVR/소속이 일괄 변경됨 → 엔진 캐시 무효화
    try:
        from game_engine import _invalidate_team_ovr_cache
        _invalidate_team_ovr_cache()
    except Exception:
        pass

    if verbose_log:
        verbose_log(
            f"🔄 이적시장 마감: 이적 {moved}건 · 은퇴/세대교체 {retired}명 · "
            f"전술 변경 {formations}팀", "event", year, 52)

    return {"grew": grew, "aged": aged, "retired": retired,
            "moved": moved, "formations": formations}


# ─────────────────────────────────────────────
# 0. 나이 보정 (구버전 세이브: age=0/NULL → 랜덤 부여)
# ─────────────────────────────────────────────
def _ensure_ai_ages(c):
    """[2026-07 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"] 이 보정은
    '구버전 세이브에 남아있던 age=0/NULL'을 고치기 위한 1회성 마이그레이션인데,
    run_ai_offseason이 매 시즌 호출될 때마다 ai_players 10만+ 행을 무조건
    풀스캔하고 있었다(정상 세이브라면 매번 0건 매치라 완전히 낭비 — 실측
    103,323행 스캔에 age 0건/sub_role 0건). age는 이후 _age_and_progress가
    매 시즌 전원에게 항상 값을 채우므로, 한 번 깨끗하다고 확인되면 그
    세이브에선 다시는 더러워질 수 없다 — meta 플래그로 "이 세이브는 이미
    깨끗함"을 기록해두고, 다음 시즌부터는 쿼리 자체를 건너뛴다."""
    try:
        row = c.execute("SELECT value FROM meta WHERE key='ai_ages_clean_v1'").fetchone()
    except Exception:
        row = None
    if row:
        return
    rows = c.execute("SELECT id FROM ai_players WHERE age IS NULL OR age=0").fetchall()
    if rows:
        # [최적화] executemany로 한 번에 처리
        updates = [(int(round(random.triangular(16, 34, 25))), r["id"]) for r in rows]
        c.executemany("UPDATE ai_players SET age=? WHERE id=?", updates)
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ai_ages_clean_v1','1')")


def _ensure_ai_sub_roles(c):
    """[세부역할 2026-07] sub_role 컬럼이 새로 생겨서 기존 세이브엔 빈 값('')
    인 AI 선수가 있다 — 포지션에 맞는 SUB_ROLES 중 하나를 무작위로 채운다.
    (신규 시딩 때는 _generate_team_players가 이미 채우므로 여기선 빈 것만
    골라 보정한다.)

    [2026-07 최적화] _ensure_ai_ages와 동일한 이유로 meta 플래그 가드 추가 —
    한 번 깨끗해지면 다시 더러워질 수 없으므로 매 시즌 풀스캔할 필요가 없다."""
    try:
        row = c.execute("SELECT value FROM meta WHERE key='ai_sub_roles_clean_v1'").fetchone()
    except Exception:
        row = None
    if row:
        return
    from constants import SUB_ROLES
    rows = c.execute(
        "SELECT id, position FROM ai_players WHERE sub_role IS NULL OR sub_role=''").fetchall()
    if rows:
        updates = [(random.choice(SUB_ROLES.get(r["position"], ["기본"])), r["id"]) for r in rows]
        c.executemany("UPDATE ai_players SET sub_role=? WHERE id=?", updates)
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ai_sub_roles_clean_v1','1')")


# ─────────────────────────────────────────────
# 1+2. 나이 +1, 성장/노화
# ─────────────────────────────────────────────
def _age_and_progress(c):
    """모든 AI 선수 나이 +1 후, 연령대별로 스탯 성장/노화 → ovr 재계산.
    [2026-07 개선] numpy가 있으면 전체를 벡터 연산으로 처리(_age_and_progress_np),
    없으면 기존 순수 파이썬 배치 버전(_age_and_progress_py)으로 자동 폴백한다.
    실측(5.9만 명 기준, 52→1 시즌전환의 최대 병목이던 지점): 순수 파이썬 약
    0.35~1.2초(환경별 차이) → numpy 벡터화 약 0.15~0.2초. 팀 수/선수 수가
    늘어날수록(향후 20팀+ 확장 등) 격차가 더 벌어진다 — 파이썬 루프는 선수 수에
    선형 비례해 늘지만, 벡터화 버전은 대부분의 시간이 상수 오버헤드라 훨씬
    완만하게 늘어난다.
    [주의] numpy 버전은 numpy의 자체 난수 생성기(Generator)를 쓰기 때문에,
    파이썬 random 모듈과 호출 순서가 달라 '같은 시드에서 완전히 동일한 결과'는
    아니다. 다만 각 확률/분포(성장 확률, 상승폭, 키스탯 가중치 등)는 원본과
    동일하게 유지했으므로 밸런스·통계적 결과는 동등하다."""
    from database import STAT_IDX, calc_ovr_from_list, OVR_RANGES
    from constants import COUNTRY_LEAGUE_GRADE, CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ

    # ── team_id → 성장기 스탯 상한 사전 조회 (선수마다 매번 JOIN 방지) ──
    # 등급별 OVR_RANGES 상단에 대륙보정 + 나라별 미세조정까지 반영해서,
    # 초기 생성 때 쓰는 보정치와 항상 같은 기준으로 성장 상한을 잡는다.
    team_cap: dict = {}
    for r in c.execute(
            """SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname,
                      cn.continent AS continent
               FROM teams t JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall():
        grade = COUNTRY_LEAGUE_GRADE.get(r["cname"], "F")
        rng = OVR_RANGES.get(grade, {}).get(r["tier"] or 1)
        top = rng[1] if rng else 43
        bonus = CONTINENT_OVR_BONUS.get(r["continent"], 0) + COUNTRY_OVR_ADJ.get(r["cname"], 0)
        if grade == "SS":
            bonus = min(bonus, 0)
        team_cap[r["tid"]] = min(99, top + bonus + 3)

    # JOIN에 안 잡힌 팀(league_id/country_id 연결 누락 등)의 폴백 상한.
    _ORPHAN_CAP_FALLBACK = 46

    rows = c.connection.cursor()
    rows.row_factory = None  # 위치 접근만 쓰므로 Row 래핑 생략 (5.9만 행 fetch 오버헤드 절감)
    rows = rows.execute(
        "SELECT id, position, age, team_id, " + _STAT_COLS + " FROM ai_players").fetchall()
    if not rows:
        return 0, 0

    if _HAS_NUMPY:
        return _age_and_progress_np(c, rows, team_cap, _ORPHAN_CAP_FALLBACK)
    return _age_and_progress_py(c, rows, team_cap, _ORPHAN_CAP_FALLBACK)


def _age_and_progress_np(c, rows, team_cap, orphan_fallback):
    """벡터화 버전 — 선수 5.9만 명(+향후 확장분)을 파이썬 for문 없이 numpy로 처리.
    로직(확률/증감폭/키스탯 가중치)은 순수 파이썬 버전과 동일하게 유지했다."""
    from database import _WEIGHT_SUMS

    N = len(rows)
    pids = [r[0] for r in rows]
    pos_list = [r[1] for r in rows]
    pos_arr = np.array(pos_list)
    ages = np.array([(r[2] or 20) for r in rows], dtype=np.int64)
    tids = [r[3] for r in rows]

    # None/0 스탯은 기존과 동일하게 50으로 보정 (구버전 세이브 방어)
    # [최적화] 중첩 리스트(list-of-tuples)를 np.array로 바로 변환하는 것보다
    # 1차원으로 펼친 뒤 reshape하는 편이 실측상 더 빠름(타입 추론 오버헤드 감소).
    _flat = [v for r in rows for v in r[4:]]
    raw = np.array(_flat, dtype=np.float64).reshape(N, _N_STATS)
    vals_arr = np.where(np.isnan(raw) | (raw == 0), 50.0, raw).astype(np.int64)

    # [2026-07 최적화, 신민용 리포트: "일정 진행이 갈수록 오래 걸린다" — 실측
    # 결과 이 함수가 "벡터화 버전"이라면서 여기 한 곳만 순수 파이썬 for문으로
    # 10만+ 회를 도는 게 남아있었다(dict.get()을 선수 수만큼 반복). team_cap은
    # 팀 수(9천여 개)만큼만 있으니, searchsorted로 완전히 벡터화한다 —
    # dict 방식 O(N) 파이썬 루프 → O(N log M) numpy 연산(M=팀 수)으로 대체.
    tids_arr = np.array(tids, dtype=np.int64)
    if team_cap:
        _cap_keys = np.array(list(team_cap.keys()), dtype=np.int64)
        _cap_vals = np.array(list(team_cap.values()), dtype=np.int64)
        _order = np.argsort(_cap_keys)
        _cap_keys_sorted = _cap_keys[_order]
        _cap_vals_sorted = _cap_vals[_order]
        _idx = np.searchsorted(_cap_keys_sorted, tids_arr)
        _idx = np.clip(_idx, 0, len(_cap_keys_sorted) - 1)
        _found = _cap_keys_sorted[_idx] == tids_arr
        cap_by_row = np.where(_found, _cap_vals_sorted[_idx], orphan_fallback).astype(np.int64)
        _orphan_team_ids = set(tids_arr[~_found].tolist())
    else:
        cap_by_row = np.full(N, orphan_fallback, dtype=np.int64)
        _orphan_team_ids = set(tids_arr.tolist())

    new_age = ages + 1
    growth_mask = new_age <= _AI_PEAK_START
    peak_mask = (new_age > _AI_PEAK_START) & (new_age <= _AI_PEAK_END)
    aging_mask = new_age > _AI_PEAK_END

    rng = np.random.default_rng()
    unique_positions = set(pos_list)

    # ── 성장기: 키스탯 70% / 전체스탯 30%, 1~3회, +1~3, 팀 상한까지 ──
    for pos in unique_positions:
        idxs = np.where(growth_mask & (pos_arr == pos))[0]
        Ng = len(idxs)
        if Ng == 0:
            continue
        key_idx = _KEY_IDX_BY_POS_NP.get(pos, _DEFAULT_KEY_IDX_NP)
        n_up = rng.integers(1, 4, size=Ng)  # 1~3
        for rnd in range(3):
            active = n_up > rnd
            if not active.any():
                continue
            act_idx = idxs[active]
            m = len(act_idx)
            use_key = rng.random(m) < 0.7
            chosen = np.where(
                use_key,
                key_idx[rng.integers(0, len(key_idx), size=m)],
                rng.integers(0, _N_STATS, size=m))
            inc = rng.integers(1, 4, size=m)  # 1~3
            cur = vals_arr[act_idx, chosen]
            cap = cap_by_row[act_idx]
            vals_arr[act_idx, chosen] = np.minimum(cap, cur + inc)

    # ── 피크기: 30% 확률로 전체스탯 중 1개 ±1 (팀 상한까지) ──
    idxs = np.where(peak_mask)[0]
    if len(idxs):
        active = rng.random(len(idxs)) < 0.3
        act_idx = idxs[active]
        m = len(act_idx)
        if m:
            chosen = rng.integers(0, _N_STATS, size=m)
            coin = rng.integers(0, 3, size=m)          # random.choice([-1,1,1])과 동일 분포
            delta = np.where(coin == 0, -1, 1)
            cap = cap_by_row[act_idx]
            cur = vals_arr[act_idx, chosen]
            vals_arr[act_idx, chosen] = np.clip(cur + delta, 15, cap)

    # ── 노화기: 신체스탯 65% / 전체스탯 35%, 나이 비례 하락 (상한 없음) ──
    idxs = np.where(aging_mask)[0]
    if len(idxs):
        decline_n = 2 + (new_age[idxs] - _AI_PEAK_END) // 2
        max_rounds = int(decline_n.max())
        for rnd in range(max_rounds):
            active = decline_n > rnd
            if not active.any():
                continue
            act_idx = idxs[active]
            m = len(act_idx)
            use_phys = rng.random(m) < 0.65
            chosen = np.where(
                use_phys,
                _PHYS_IDX_NP[rng.integers(0, 4, size=m)],
                rng.integers(0, _N_STATS, size=m))
            dec = rng.integers(1, 4, size=m)
            cur = vals_arr[act_idx, chosen]
            vals_arr[act_idx, chosen] = np.maximum(15, cur - dec)

    # ── OVR 재계산 (포지션별 가중치 벡터와 행렬곱, 5.9만 명 순회 없이 일괄 처리) ──
    ovr_out = np.empty(N, dtype=np.int64)
    for pos in unique_positions:
        mask = pos_arr == pos
        wv = _WEIGHT_VEC_NP.get(pos, _WEIGHT_VEC_NP["CM"])
        wsum = _WEIGHT_SUMS.get(pos, _WEIGHT_SUMS["CM"])
        total = vals_arr[mask] @ wv / wsum
        ovr_out[mask] = np.clip(np.round(total), 1, 100).astype(np.int64)

    # [최적화] (age, *stats, ovr, id) 튜플을 파이썬 루프로 만드는 대신
    # column_stack으로 한 번에 이어붙여 tolist() — sqlite3.executemany는
    # 튜플뿐 아니라 리스트 행도 그대로 받아준다. 5.9만 회 언패킹 루프 제거.
    pids_arr = np.array(pids, dtype=np.int64)
    updates = np.column_stack([new_age, vals_arr, ovr_out, pids_arr]).tolist()

    set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
    c.executemany(
        f"UPDATE ai_players SET age=?, {set_clause}, ovr=? WHERE id=?",
        updates)

    if _orphan_team_ids:
        import sys as _sys
        print(f"[⚠ ai_lifecycle 경고] team_cap 매칭 실패 팀 {len(_orphan_team_ids)}개 "
              f"(league_id/country_id 연결 확인 필요, 폴백 상한 {orphan_fallback} 적용됨): "
              f"{sorted(_orphan_team_ids)[:20]}{'...' if len(_orphan_team_ids) > 20 else ''}",
              file=_sys.stderr)

    return int(growth_mask.sum()), int(aging_mask.sum())


def _age_and_progress_py(c, rows, team_cap, orphan_fallback):
    """순수 파이썬 폴백 버전 (numpy 미설치 환경용). 로직은 numpy 버전과 동일."""
    from database import STAT_IDX, calc_ovr_from_list
    grew = aged = 0
    updates = []  # (age, s1, s2, ..., ovr, id) 튜플 목록
    phys_list = ["stamina", "speed", "jump", "strength"]
    _default_keys = ALL_STATS[:5]
    _orphan_team_ids = set()

    _randint = random.randint
    _choice = random.choice
    _random = random.random

    for r in rows:
        pid = r[0]
        pos = r[1]
        new_age = (r[2] or 20) + 1
        tid = r[3]
        if tid in team_cap:
            _cap = team_cap[tid]
        else:
            _cap = orphan_fallback
            _orphan_team_ids.add(tid)
        vals = [v or 50 for v in r[4:]]
        keys = KEY_STATS_BY_POS.get(pos, _default_keys)

        if new_age <= _AI_PEAK_START:
            n_up = _randint(1, 3)
            for _ in range(n_up):
                s = _choice(keys if _random() < 0.7 else ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = min(_cap, vals[i] + _randint(1, 3))
            grew += 1
        elif new_age <= _AI_PEAK_END:
            if _random() < 0.3:
                s = _choice(ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = min(_cap, max(15, vals[i] + _choice([-1, 1, 1])))
        else:
            decline_n = 2 + (new_age - _AI_PEAK_END) // 2
            for _ in range(decline_n):
                if _random() < 0.65:
                    s = _choice(phys_list)
                else:
                    s = _choice(ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = max(15, vals[i] - _randint(1, 3))
            aged += 1

        new_ovr = calc_ovr_from_list(pos, vals)
        updates.append((new_age, *vals, new_ovr, pid))

    set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
    c.executemany(
        f"UPDATE ai_players SET age=?, {set_clause}, ovr=? WHERE id=?",
        updates)

    if _orphan_team_ids:
        import sys as _sys
        print(f"[⚠ ai_lifecycle 경고] team_cap 매칭 실패 팀 {len(_orphan_team_ids)}개 "
              f"(league_id/country_id 연결 확인 필요, 폴백 상한 {orphan_fallback} 적용됨): "
              f"{sorted(_orphan_team_ids)[:20]}{'...' if len(_orphan_team_ids) > 20 else ''}",
              file=_sys.stderr)

    return grew, aged


# ─────────────────────────────────────────────
# 3. 은퇴 + 신인 교체
# ─────────────────────────────────────────────
def _retire_and_replace(c, year, ai_rows=None):
    """고령 선수 은퇴 → 같은 팀·같은 포지션에 신인 영입.
    [버그수정] 신인 목표 OVR을 team_avg 기반 → 리그 등급/tier OVR_RANGES 기반으로 변경.
    기존: team_avg가 낮으면 낮은 신인이 들어와 리그 전체 OVR이 해마다 하락하는 버그.
    수정: OVR_RANGES[grade][tier] 범위 하단~중간값을 신인 목표로 사용 → 리그 OVR 유지.
    [최적화] 팀 info 선조회 + 이름풀 캐시로 은퇴자마다 DB 왕복 제거.
    ai_rows: 호출부(run_ai_offseason)가 이미 조회해둔 ai_players 행
      (id,team_id,position,age,name)을 넘겨받아 재사용 — 이 함수와
      _transfer_market이 각자 같은 조건의 SELECT를 또 날리던 것을 없애
      전체 스캔 횟수를 줄인다(로직/결과는 완전히 동일). None이면(단독 호출
      등 하위호환) 기존처럼 이 함수가 직접 조회한다."""
    from constants import OVR_RANGES, COUNTRY_LEAGUE_GRADE, CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, SUB_ROLES
    from database import _pick_nationality, FOREIGN_QUOTA_CAP
    retired = 0

    # 팀 → 리그등급/tier/보정치 선조회 (은퇴자마다 JOIN 방지)
    # [2026-07 확장] 국적 재배정(_pick_nationality)에 필요한 국가명/대륙도
    # 같이 캐싱한다 — 신인이 은퇴자의 옛 국적을 그대로 물려받던 버그 수정용.
    # [2026-07 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"] 아래
    # 명문팀 가산 로직이 은퇴자마다 "SELECT name FROM teams WHERE id=?"를
    # 따로 날리고 있었다 — 이 함수 전체가 "은퇴자마다 DB 왕복 제거"를
    # 원칙으로 세워놨는데 그 원칙을 깨는 N+1 쿼리였다(은퇴자가 많을수록,
    # 세이브가 오래될수록 이 함수가 계속 느려지던 원인 중 하나 — 실측
    # 로그에서 "은퇴·세대교체" 단계가 시즌이 지날수록 조금씩 늘어나는
    # 추세를 보였음). 팀 이름도 이 아래 team_info 캐시 SELECT 한 번에
    # 같이 담아서, 이후 루프에서는 dict 조회만 하도록 고친다.
    from data.prestige_clubs import is_prestige
    team_info = {}  # {team_id: (grade, tier, bonus, cname, continent, tname)}
    for r in c.execute(
            """SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                      cn.name AS cname, cn.continent AS continent
               FROM teams t
               JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall():
        grade = COUNTRY_LEAGUE_GRADE.get(r["cname"], "D")
        bonus = CONTINENT_OVR_BONUS.get(r["continent"], 0) + COUNTRY_OVR_ADJ.get(r["cname"], 0)
        if grade == "SS":
            bonus = min(bonus, 0)
        team_info[r["tid"]] = (grade, r["tier"] or 1, bonus, r["cname"], r["continent"], r["tname"])

    # [최적화] 이름풀 전체 1회 로드 (은퇴자마다 ORDER BY RANDOM() 방지)
    name_cache = _build_name_cache(c)
    # 팀→국가 캐시 초기화 (오프시즌 시작 시 리셋)
    _team_country_cache.clear()

    # [최적화] 이름 중복방지 캐시 + 은퇴 대상 목록을 별도 두 번 풀스캔하던 것을
    #   컬럼을 합쳐 1회 SELECT로 통합했었고(5.9만 행 전체스캔 2회 → 1회),
    #   이제 그 SELECT 자체도 호출부에서 넘겨받은 ai_rows로 재사용해
    #   _transfer_market과의 중복 스캔까지 없앤다(3회 → 2회).
    _src_rows = ai_rows if ai_rows is not None else c.execute(
        "SELECT id, team_id, position, age, name, nationality FROM ai_players").fetchall()
    team_used_names: dict = {}
    rows = []
    # [2026-07 신설] 팀별 현재 외국인 수 카운터 — 신인 국적 재배정 시
    # 쿼터(FOREIGN_QUOTA_CAP)를 그대로 지키기 위해 필요.
    foreign_count_by_team: dict = {}
    for r in _src_rows:
        team_used_names.setdefault(r["team_id"], set()).add(r["name"])
        rows.append(r)
        tinfo = team_info.get(r["team_id"])
        if tinfo and r["nationality"] and r["nationality"] != tinfo[3]:
            foreign_count_by_team[r["team_id"]] = foreign_count_by_team.get(r["team_id"], 0) + 1
    replace_updates = []  # executemany용

    for r in rows:
        age = r["age"] or 25
        if age < _AI_RETIRE_AGE:
            continue
        p_retire = min(0.95, 0.25 + (age - _AI_RETIRE_AGE) * 0.15)
        if random.random() >= p_retire:
            continue

        # [버그수정] 신인 목표 OVR: 리그 등급/tier OVR_RANGES 하단~중간 범위
        #  + 대륙/나라 보정. [조정] 예전엔 중간값+5까지 허용해서 신인이 데뷔부터
        #  거의 에이스급으로 들어왔다(A등급 기준 82~91). 하단~중간(82~86)으로
        #  좁혀서, 실제로 몇 시즌 성장해야 에이스 근처에 도달하도록 한다.
        grade, tier, _bonus, cname, continent, _tname = team_info.get(
            r["team_id"], ("D", 1, 0, "", "유럽", ""))
        ovr_rng = OVR_RANGES.get(grade, {}).get(tier)
        if ovr_rng:
            lo, hi = ovr_rng
            lo, hi = lo + _bonus, hi + _bonus
            mid = (lo + hi) // 2
            target = random.randint(lo, mid)
        else:
            # [버그수정 2026-07] 그 등급에 이 tier가 정의 안 돼 있으면(부수가
            # 늘었는데 표를 못 채운 경우) 고정 30~45가 아니라, 그 등급 안에서
            # 정의된 가장 깊은 부수 기준 단계별 감쇠 값을 쓴다 — database._tier_top_ovr
            # 과 동일한 감쇠 방식이라, 등급표 밖 tier라도 "한 단계 위보다는
            # 확실히 낮고, SS/S 같은 상위 등급이 갑자기 완전히 다른 등급처럼
            # 뚝 떨어지지 않는" 자연스러운 값이 된다.
            grade_ranges = OVR_RANGES.get(grade, {})
            if grade_ranges:
                deepest_tier = max(grade_ranges)
                deepest_lo, deepest_hi = grade_ranges[deepest_tier]
                STEP = 8
                extra = (tier - deepest_tier) * STEP
                lo = max(15, deepest_lo - extra) + _bonus
                hi = max(lo + 1, deepest_hi - extra) + _bonus
                target = random.randint(lo, (lo + hi) // 2)
            else:
                target = random.randint(30, 45)
                hi = target  # [방어] 이 극단적 폴백 경로엔 hi가 없어 아래 명문팀 가산에서 참조 에러 방지

        # [2026-07 신설, 신민용 확정: "명문팀 가중치가 게임 시작할 때
        # 한 번뿐이라 몇 시즌 지나면 사라진다"] 은퇴자 교체 때마다 매번
        # 명문팀(data/prestige_clubs.py)이면 신인 목표 OVR을 소폭
        # 상향한다 — "신인은 처음부터 에이스급이면 안 된다"는 기존 설계는
        # 유지하되(범위를 통째로 올리지 않고 소폭 가산), 명문팀은 유스/
        # 스카우팅 인프라가 좋아서 평균적으로 조금 더 나은 신인을 계속
        # 채운다는 현실을 반영한다. 확정 버프가 아니라 +2~+5 정도의
        # 완만한 가산이라 "가끔은 명문팀도 평범한 신인이 들어온다"는
        # 여지는 그대로 남는다.
        # [2026-07 최적화] 팀 이름은 위 team_info 캐시에서 바로 꺼낸다
        # (원래 여기서 은퇴자마다 "SELECT name FROM teams WHERE id=?"를
        # 따로 날렸던 N+1 쿼리였음 — 함수 상단 주석 참고).
        if is_prestige(cname, tier, _tname):
            target = min(hi, target + random.randint(2, 5))

        stats = _gen_stats(r["position"], target)
        new_ovr = calc_ovr(r["position"], stats)
        new_age = random.randint(*_AI_NEWBIE_AGE)
        # [세부역할 2026-07] 새 신인은 은퇴자의 예전 세부역할을 물려받지 않고
        # 그 포지션에 맞는 SUB_ROLES 중 하나를 새로 무작위 배정한다.
        new_sub_role = random.choice(SUB_ROLES.get(r["position"], ["기본"]))
        # [2026-07 신설, 신민용 지적: "은퇴하면 새 선수 들어오는데 국적도
        # 새로 뽑아야지, 안 그러면 은퇴자 국적을 그대로 물려받는다"] 은퇴자가
        # 외국인이었으면 먼저 카운터에서 빼고, 새 국적을 다시 뽑는다.
        tid = r["team_id"]
        old_nat = r["nationality"] if "nationality" in r.keys() else ""
        cur_foreign = foreign_count_by_team.get(tid, 0)
        if old_nat and old_nat != cname:
            cur_foreign = max(0, cur_foreign - 1)
        quota = FOREIGN_QUOTA_CAP.get(cname)
        new_nat, cur_foreign = _pick_nationality(cname, continent, grade, r["position"],
                                                  False, cur_foreign, quota)
        foreign_count_by_team[tid] = cur_foreign
        # 팀 내 중복 방지: used_in_team에 팀 현재 이름 set 전달
        used = team_used_names.setdefault(r["team_id"], set())
        name = _random_name(c, r["team_id"], name_cache, used_in_team=used)
        replace_updates.append(
            (name, new_age, *[stats[s] for s in ALL_STATS], new_ovr, new_sub_role, new_nat, r["id"]))
        retired += 1

    if replace_updates:
        set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
        c.executemany(
            f"UPDATE ai_players SET name=?, age=?, {set_clause}, ovr=?, sub_role=?, nationality=? WHERE id=?",
            replace_updates)

    return retired


# ─────────────────────────────────────────────
# 4. 이적 시장 (활발하게)
# ─────────────────────────────────────────────
def _transfer_market(c, ai_rows=None):
    """선수들이 팀 간 이동. 같은 리그 내 + 일부 리그 간 이적.
    [최적화] ORDER BY RANDOM() 제거 → 팀별 선수 목록 선조회 후 Python shuffle.
    이적마다 DB 왕복 2회(RANDOM 쿼리) → 0회로 감소.
    ai_rows: _retire_and_replace와 공유하는 ai_players 선조회 결과
      (id,team_id,position,age,name) — 이 함수는 id/team_id/position만 쓰므로
      그대로 재사용 가능(은퇴 처리는 team_id/position/id를 바꾸지 않으므로
      은퇴 처리 이전에 뜬 스냅샷이어도 유효하다). None이면 기존처럼 직접 조회."""
    moved = 0

    teams = [dict(r) for r in c.execute(
        """SELECT t.id AS tid, t.league_id AS lid,
                  (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) AS avg_ovr
           FROM teams t""").fetchall()]
    team_avg = {t["tid"]: (t["avg_ovr"] or 50) for t in teams}

    # 리그별 팀 그룹
    by_league: dict = {}
    for t in teams:
        by_league.setdefault(t["lid"], []).append(t["tid"])

    # [최적화] 팀별 선수 목록을 _retire_and_replace와 공유된 스냅샷에서 재사용
    # (기존엔 여기서 "SELECT id, team_id, position FROM ai_players"를 또 날렸음)
    all_players_rows = ai_rows if ai_rows is not None else c.execute(
        "SELECT id, team_id, position, ovr FROM ai_players").fetchall()
    # {team_id: [{"id":..., "position":..., "ovr":...}, ...]}
    team_players: dict = {}
    for r in all_players_rows:
        team_players.setdefault(r["team_id"], []).append(
            {"id": r["id"], "position": r["position"], "ovr": r["ovr"] if r["ovr"] is not None else 50})

    # 이적 결과 누적 후 executemany
    transfer_updates = []  # (new_team_id, player_id)

    for lid, tids in by_league.items():
        if len(tids) < 2:
            continue
        n_transfers = int(len(tids) * random.uniform(1.0, 2.0))
        for _ in range(n_transfers):
            result = _do_one_transfer_cached(tids, team_players, team_avg)
            if result:
                # team_players 캐시도 즉시 반영 (같은 시즌 내 연속 이적 일관성)
                for new_tid, pid, old_tid in result:
                    transfer_updates.append((new_tid, pid))
                    # [bugfix] cache update: remove from old team, add to new team
                    p_entry = next((e for e in team_players.get(old_tid, []) if e["id"] == pid), None)
                    if p_entry:
                        team_players[old_tid] = [e for e in team_players[old_tid] if e["id"] != pid]
                        team_players.setdefault(new_tid, []).append(p_entry)
                moved += 1

    if transfer_updates:
        c.executemany("UPDATE ai_players SET team_id=? WHERE id=?", transfer_updates)

    return moved


def _do_one_transfer_cached(tids, team_players, team_avg):
    """[최적화] ORDER BY RANDOM() 없이 Python-side shuffle로 이적 처리.
    team_players: {team_id: [{"id", "position", "ovr"}, ...]} 선조회 캐시.

    [버그수정 2026-07] team_avg를 함수가 받기만 하고 실제로는 전혀 참조하지
    않아, 리그 내 이적이 팀 실력과 무관하게 완전 무작위로 일어나고 있었다
    (최강팀 선수가 최약팀으로 가는 것과 그 반대가 똑같은 확률). 이제 이동할
    선수(mover)의 OVR과 각 목적지 팀 평균OVR(team_avg) 차이가 작을수록
    (비슷한 수준 팀끼리, 혹은 살짝 더 좋은 팀으로) 그 팀이 목적지로 뽑힐
    확률이 높아지도록 가우시안 가중치를 준다 — 팀 간 실력차가 40 이상이면
    사실상 이적 후보에서 배제된다(가중치가 0에 수렴)."""
    src = random.choice(tids)
    src_players = team_players.get(src, [])
    if not src_players:
        return None
    # random.choice → ORDER BY RANDOM() LIMIT 1과 동일 효과, DB 왕복 없음
    mover = random.choice(src_players)

    dst_candidates = [t for t in tids if t != src]
    if not dst_candidates:
        return None

    mover_ovr = mover.get("ovr", 50)
    # 가우시안 가중치: 목적지 팀 평균OVR이 이 선수 수준과 비슷할수록(약간
    # 위쪽 포함) 가중치가 크다. sigma=15 → 격차 15면 가중치 약 0.61배,
    # 격차 30이면 약 0.14배로 실질 배제 수준까지 떨어진다.
    weights = []
    for t in dst_candidates:
        gap = team_avg.get(t, 50) - mover_ovr
        weights.append(math.exp(-(gap * gap) / 450.0))
    if sum(weights) <= 0:
        dst = random.choice(dst_candidates)
    else:
        dst = random.choices(dst_candidates, weights=weights, k=1)[0]

    dst_players = team_players.get(dst, [])
    same_pos = [p for p in dst_players if p["position"] == mover["position"]]

    if same_pos:
        swap = random.choice(same_pos)
        # (new_tid, pid, old_tid)
        return [(dst, mover["id"], src), (src, swap["id"], dst)]
    else:
        return [(dst, mover["id"], src)]


# _do_one_transfer는 하위호환용 별칭 (외부에서 직접 호출하는 경우 대비)
def _do_one_transfer(c, tids, team_avg):
    """하위호환 래퍼. 신규 코드는 _do_one_transfer_cached 사용."""
    players_rows = c.execute(
        "SELECT id, team_id, position, ovr FROM ai_players WHERE team_id IN ({})".format(
            ",".join("?" for _ in tids)), tids).fetchall()
    tp: dict = {}
    for r in players_rows:
        tp.setdefault(r["team_id"], []).append(
            {"id": r["id"], "position": r["position"], "ovr": r["ovr"] if r["ovr"] is not None else 50})
    return _do_one_transfer_cached(tids, tp, team_avg)


# ─────────────────────────────────────────────
# 5. 포메이션 변경 (감독 교체 컨셉)
# ─────────────────────────────────────────────
def _shuffle_formations(c):
    """일부 팀의 포메이션 변경. 시즌마다 ~20% 팀이 전술 교체.
    [최적화] executemany로 일괄 UPDATE."""
    changed = 0
    teams = c.execute("SELECT id, formation FROM teams").fetchall()
    updates = []
    for t in teams:
        if random.random() < 0.20:
            new_f = random.choice([f for f in _FORMATIONS if f != t["formation"]])
            updates.append((new_f, t["id"]))
            changed += 1
    if updates:
        c.executemany("UPDATE teams SET formation=? WHERE id=?", updates)
    return changed


# ─────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────
def _gen_stats(pos, target):
    """database._gen_ai_stats 재사용 (목표 OVR→스탯 역산)."""
    try:
        from database import _gen_ai_stats
        return _gen_ai_stats(pos, target)
    except Exception:
        keys = KEY_STATS_BY_POS.get(pos, ALL_STATS[:5])
        stats = {}
        for s in ALL_STATS:
            base = target + (3 if s in keys else -3)
            stats[s] = min(99, max(15, int(round(random.gauss(base, 4)))))
        return stats


def _build_name_cache(c):
    """국가별 이름풀 전체를 1회 로드 → {country_id: [name, ...]}
    _retire_and_replace에서 한 번 호출 후 재사용. ORDER BY RANDOM() 완전 제거."""
    rows = c.execute("SELECT country_id, name FROM player_names").fetchall()
    cache: dict = {}
    for r in rows:
        cache.setdefault(r["country_id"], []).append(r["name"])
    return cache


# 팀→국가 매핑 캐시 (오프시즌 내 반복 JOIN 방지)
_team_country_cache: dict = {}


def _get_team_country(c, team_id):
    """팀 ID → country_id. 한 번 조회 후 모듈 캐시에 저장."""
    if team_id not in _team_country_cache:
        row = c.execute(
            """SELECT cn.id AS cid FROM teams t
               JOIN leagues l ON t.league_id=l.id
               JOIN countries cn ON l.country_id=cn.id
               WHERE t.id=?""", (team_id,)).fetchone()
        _team_country_cache[team_id] = row["cid"] if row else None
    return _team_country_cache[team_id]


def _random_name(c, team_id, name_cache=None, used_in_team=None):
    """팀 소속국 이름풀에서 랜덤 이름. 같은 팀 내 중복 방지.
    used_in_team: set — 이번 오프시즌에 이미 이 팀에 배정된 이름들.
    다른 팀/리그 동명이인은 허용 (현실적으로 전 세계에 동명이인 있음).
    """
    cid = _get_team_country(c, team_id)
    if cid is not None:
        pool = None
        if name_cache is not None:
            pool = name_cache.get(cid, [])
        else:
            rows = c.execute(
                "SELECT name FROM player_names WHERE country_id=?", (cid,)).fetchall()
            pool = [r["name"] for r in rows]

        if pool:
            if used_in_team:
                # 팀 내 중복 회피: 사용 안 된 이름 우선
                available = [n for n in pool if n not in used_in_team]
                if available:
                    chosen = random.choice(available)
                else:
                    # 이름풀 소진 시 어쩔 수 없이 중복 허용
                    chosen = random.choice(pool)
            else:
                chosen = random.choice(pool)
            if used_in_team is not None:
                used_in_team.add(chosen)
            return chosen
    return f"신인{random.randint(100, 999)}"