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
from database import (get_conn, calc_ovr, ALL_STATS, KEY_STATS_BY_POS)

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


def run_ai_offseason(year, verbose_log=None):
    """시즌 종료 시 1회 호출. AI 선수 생애주기 전체 처리.
    verbose_log: add_log 함수(있으면 요약 한 줄 남김)."""
    conn = get_conn()
    c = conn.cursor()

    _ensure_ai_ages(c)               # 구버전 세이브 age 보정
    grew, aged = _age_and_progress(c)
    retired    = _retire_and_replace(c, year)
    moved      = _transfer_market(c)
    formations = _shuffle_formations(c)

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
    rows = c.execute("SELECT id FROM ai_players WHERE age IS NULL OR age=0").fetchall()
    if not rows:
        return
    # [최적화] executemany로 한 번에 처리
    updates = [(int(round(random.triangular(16, 34, 25))), r["id"]) for r in rows]
    c.executemany("UPDATE ai_players SET age=? WHERE id=?", updates)


# ─────────────────────────────────────────────
# 1+2. 나이 +1, 성장/노화
# ─────────────────────────────────────────────
def _age_and_progress(c):
    """모든 AI 선수 나이 +1 후, 연령대별로 스탯 성장/노화 → ovr 재계산.
    [최적화] 개별 UPDATE → executemany 배치 처리로 DB 왕복 횟수 대폭 감소."""
    grew = aged = 0
    rows = c.execute(
        "SELECT id, position, age, " + _STAT_COLS + " FROM ai_players").fetchall()

    updates = []  # (age, s1, s2, ..., ovr, id) 튜플 목록
    phys_list = ["stamina", "speed", "jump", "strength"]

    for r in rows:
        pid = r["id"]
        pos = r["position"]
        new_age = (r["age"] or 20) + 1
        stats = {s: (r[s] or 50) for s in ALL_STATS}
        keys = KEY_STATS_BY_POS.get(pos, ALL_STATS[:5])

        if new_age <= _AI_PEAK_START:
            # 성장기: 키스탯 위주로 +1~3
            n_up = random.randint(1, 3)
            for _ in range(n_up):
                s = random.choice(keys if random.random() < 0.7 else ALL_STATS)
                stats[s] = min(99, stats[s] + random.randint(1, 3))
            grew += 1
        elif new_age <= _AI_PEAK_END:
            # 피크: 거의 정체, 미세 변동
            if random.random() < 0.3:
                s = random.choice(ALL_STATS)
                stats[s] = min(99, max(15, stats[s] + random.choice([-1, 1, 1])))
        else:
            # 노화기: 신체 스탯 위주로 하락
            decline_n = 2 + (new_age - _AI_PEAK_END) // 2
            for _ in range(decline_n):
                if random.random() < 0.65:
                    s = random.choice(phys_list)
                else:
                    s = random.choice(ALL_STATS)
                stats[s] = max(15, stats[s] - random.randint(1, 3))
            aged += 1

        new_ovr = calc_ovr(pos, stats)
        updates.append((new_age, *[stats[s] for s in ALL_STATS], new_ovr, pid))

    # [최적화] 전체를 executemany 1회로 처리 (개별 UPDATE 26000회 → 1회 배치)
    set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
    c.executemany(
        f"UPDATE ai_players SET age=?, {set_clause}, ovr=? WHERE id=?",
        updates)

    return grew, aged


# ─────────────────────────────────────────────
# 3. 은퇴 + 신인 교체
# ─────────────────────────────────────────────
def _retire_and_replace(c, year):
    """고령 선수 은퇴 → 같은 팀·같은 포지션에 신인 영입.
    [버그수정] 신인 목표 OVR을 team_avg 기반 → 리그 등급/tier OVR_RANGES 기반으로 변경.
    기존: team_avg가 낮으면 낮은 신인이 들어와 리그 전체 OVR이 해마다 하락하는 버그.
    수정: OVR_RANGES[grade][tier] 범위 하단~중간값을 신인 목표로 사용 → 리그 OVR 유지.
    [최적화] 팀 info 선조회 + 이름풀 캐시로 은퇴자마다 DB 왕복 제거."""
    from constants import OVR_RANGES, COUNTRY_LEAGUE_GRADE
    retired = 0

    # 팀 → 리그등급/tier 선조회 (은퇴자마다 JOIN 방지)
    team_info = {}  # {team_id: (grade, tier)}
    for r in c.execute(
            """SELECT t.id AS tid, t.current_tier AS tier,
                      cn.name AS cname
               FROM teams t
               JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall():
        grade = COUNTRY_LEAGUE_GRADE.get(r["cname"], "D")
        team_info[r["tid"]] = (grade, r["tier"] or 1)

    # [최적화] 이름풀 전체 1회 로드 (은퇴자마다 ORDER BY RANDOM() 방지)
    name_cache = _build_name_cache(c)
    # 팀→국가 캐시 초기화 (오프시즌 시작 시 리셋)
    _team_country_cache.clear()

    # 팀별 현재 선수 이름 선조회 → 팀 내 중복 방지용
    # {team_id: set(name, ...)} — 같은 팀 안에서만 중복 방지, 다른 팀/리그는 무관
    team_used_names: dict = {}
    for r2 in c.execute("SELECT team_id, name FROM ai_players").fetchall():
        team_used_names.setdefault(r2["team_id"], set()).add(r2["name"])

    rows = c.execute("SELECT id, team_id, position, age FROM ai_players").fetchall()
    replace_updates = []  # executemany용

    for r in rows:
        age = r["age"] or 25
        if age < _AI_RETIRE_AGE:
            continue
        p_retire = min(0.95, 0.25 + (age - _AI_RETIRE_AGE) * 0.15)
        if random.random() >= p_retire:
            continue

        # [버그수정] 신인 목표 OVR: 리그 등급/tier OVR_RANGES 하단~중간 범위
        grade, tier = team_info.get(r["team_id"], ("D", 1))
        ovr_rng = OVR_RANGES.get(grade, {}).get(tier)
        if ovr_rng:
            lo, hi = ovr_rng
            # 신인은 리그 하단~중간 수준으로 진입 (하단+0 ~ 중간+5 범위)
            mid = (lo + hi) // 2
            target = random.randint(lo, mid + 5)
        else:
            target = random.randint(30, 45)

        stats = _gen_stats(r["position"], target)
        new_ovr = calc_ovr(r["position"], stats)
        new_age = random.randint(*_AI_NEWBIE_AGE)
        # 팀 내 중복 방지: used_in_team에 팀 현재 이름 set 전달
        used = team_used_names.setdefault(r["team_id"], set())
        name = _random_name(c, r["team_id"], name_cache, used_in_team=used)
        replace_updates.append(
            (name, new_age, *[stats[s] for s in ALL_STATS], new_ovr, r["id"]))
        retired += 1

    if replace_updates:
        set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
        c.executemany(
            f"UPDATE ai_players SET name=?, age=?, {set_clause}, ovr=? WHERE id=?",
            replace_updates)

    return retired


# ─────────────────────────────────────────────
# 4. 이적 시장 (활발하게)
# ─────────────────────────────────────────────
def _transfer_market(c):
    """선수들이 팀 간 이동. 같은 리그 내 + 일부 리그 간 이적.
    [최적화] ORDER BY RANDOM() 제거 → 팀별 선수 목록 선조회 후 Python shuffle.
    이적마다 DB 왕복 2회(RANDOM 쿼리) → 0회로 감소."""
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

    # [최적화] 팀별 선수 목록 전체를 단일 쿼리로 선조회 (ORDER BY RANDOM 방지)
    # _do_one_transfer 내부 루프에서 매번 SELECT 날리던 것 → Python dict 조회로 교체
    all_players_rows = c.execute(
        "SELECT id, team_id, position FROM ai_players").fetchall()
    # {team_id: [{"id":..., "position":...}, ...]}
    team_players: dict = {}
    for r in all_players_rows:
        team_players.setdefault(r["team_id"], []).append({"id": r["id"], "position": r["position"]})

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
                for new_tid, pid in result:
                    transfer_updates.append((new_tid, pid))
                    # 캐시 업데이트: 이전 팀에서 제거, 새 팀에 추가
                    for p_entry in list(team_players.get(new_tid ^ new_tid, [])):  # no-op placeholder
                        pass
                moved += 1

    if transfer_updates:
        c.executemany("UPDATE ai_players SET team_id=? WHERE id=?", transfer_updates)

    return moved


def _do_one_transfer_cached(tids, team_players, team_avg):
    """[최적화] ORDER BY RANDOM() 없이 Python-side shuffle로 이적 처리.
    team_players: {team_id: [{"id", "position"}, ...]} 선조회 캐시."""
    src = random.choice(tids)
    dst_candidates = [t for t in tids if t != src]
    if not dst_candidates:
        return None
    dst = random.choice(dst_candidates)

    src_players = team_players.get(src, [])
    if not src_players:
        return None
    # random.choice → ORDER BY RANDOM() LIMIT 1과 동일 효과, DB 왕복 없음
    mover = random.choice(src_players)

    dst_players = team_players.get(dst, [])
    same_pos = [p for p in dst_players if p["position"] == mover["position"]]

    if same_pos:
        swap = random.choice(same_pos)
        return [(dst, mover["id"]), (src, swap["id"])]
    else:
        return [(dst, mover["id"])]


# _do_one_transfer는 하위호환용 별칭 (외부에서 직접 호출하는 경우 대비)
def _do_one_transfer(c, tids, team_avg):
    """하위호환 래퍼. 신규 코드는 _do_one_transfer_cached 사용."""
    players_rows = c.execute(
        "SELECT id, team_id, position FROM ai_players WHERE team_id IN ({})".format(
            ",".join("?" for _ in tids)), tids).fetchall()
    tp: dict = {}
    for r in players_rows:
        tp.setdefault(r["team_id"], []).append({"id": r["id"], "position": r["position"]})
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