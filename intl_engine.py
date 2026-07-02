"""
intl_engine.py ─ 국제대회(월드컵/대륙컵) 엔진

시즌 중 17~24주 국제대회 윈도우에서 실제 경기 단위로 진행한다.
  17주차: 예선 결과 발표 + 조 추첨 + 국가대표 소집
  18~20주차: 조별리그 3경기
  월드컵:  16강(21) → 8강(22) → 4강(23) → 결승(24)
  대륙컵:  8강(21) → 4강(22) → 결승(23)

[예선 정책]
  - 월드컵 예선(wc_qual) : 내 대륙 전체 참가, 6R 홈앤어웨이, 통과국 qual_results 저장
  - 대륙컵 예선(cont_qual): 폐지 → 랜덤 선발로 바로 본선
본선 진출국은 피파 랭킹 줄세우기가 아니라
'등급 기본 점수 + 랜덤 노이즈' 예선 점수로 대륙별 쿼터만큼 선발
→ 강호도 가끔 예선 탈락, 약체도 가끔 깜짝 진출.
"""

import random

from database import get_conn

def _get_field_pos(p):
    """현재 팀 포메이션 기반 배치 포지션 계산 (순환 import 방지용 로컬 버전)."""
    if not p:
        return "CM"
    primary = p.get("position", "CM")
    team_id = p.get("current_team_id", 0)
    if not team_id:
        return primary
    try:
        from constants import POSITION_COMPAT, FORMATION_SLOTS
        conn = get_conn()
        row = conn.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
        conn.close()
        formation = (row["formation"] if row else None) or "4-4-2"
        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        compat = POSITION_COMPAT.get(primary, [primary])
        best, best_rank = primary, 999
        for slot in slots:
            if slot in compat:
                rank = compat.index(slot)
                if rank < best_rank:
                    best_rank = rank
                    best = slot
        return best
    except Exception:
        return primary

from constants import (
    WC_START_YEAR, WC_INTERVAL,
    CONTINENTAL_START_YEAR, CONTINENTAL_INTERVAL,
    INTL_CALLUP_WEEK, INTL_GROUP_WEEKS, INTL_KO_WEEKS,
    WC_TEAMS, WC_GROUPS, WC_QUOTA,
    WC_EXPAND_YEAR, WC_TEAMS_BIG, WC_GROUPS_BIG, WC_QUOTA_BIG, WC_BEST_THIRDS_BIG,
    CONT_TEAMS, CONT_GROUPS, CONT_BEST_THIRDS,
    CONFEDERATIONS, CONTINENT_TO_CONF, CONF_CUP_NAME,
    WC_QUAL_32, WC_QUAL_48,
    GRADE_TEAM_OVR, GRADE_QUAL_BASE, QUAL_NOISE,
    INTL_SELECTION_OVR, INTL_MAX_TIER, INTL_MIN_MATCHES,
    INTL_SELECTION_MARGIN,
)

STAGE_KO = {"group": "조별리그", "R32": "32강", "R16": "16강", "QF": "8강", "SF": "4강", "F": "결승", "TP": "3/4위전",
            "qual_group": "조별리그", "qual_po": "플레이오프"}

# ── entry 캐시 ─────────────────────────────────────
# intl_entries(ovr/flag/grade)는 대회 진행 중 불변 → (tid, country)별 1회 조회.
_entry_cache = {}

def _clear_entry_cache():
    _entry_cache.clear()

# 그룹 라벨
_GROUP_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H",
                 "I", "J", "K", "L", "M", "N", "O", "P"]

# 조별리그 라운드 매칭 (4팀, 인덱스)
_GROUP_ROUNDS = [
    [(0, 1), (2, 3)],
    [(0, 2), (1, 3)],
    [(0, 3), (1, 2)],
]

# 예선 조별리그: 4팀 홈앤어웨이 = 6라운드(앞 3R + 홈/원정 뒤집은 3R)
_QUAL_ROUNDS = [
    [(0, 1), (2, 3)],
    [(0, 2), (1, 3)],
    [(0, 3), (1, 2)],
    [(1, 0), (3, 2)],   # 홈/원정 반전
    [(2, 0), (3, 1)],
    [(3, 0), (2, 1)],
]


# ─────────────────────────────────────────────
# 조회 헬퍼
# ─────────────────────────────────────────────

def get_tournament(year):
    """해당 연도의 국제대회 row (없으면 None)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM intl_tournaments WHERE year=? ORDER BY id DESC LIMIT 1",
        (year,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_tournaments(year):
    """[복수대륙컵] 해당 연도의 모든 국제대회 row 리스트 (없으면 빈 리스트).
    미고정 복수국적이면 한 해에 대륙컵이 2~3개 존재할 수 있다."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM intl_tournaments WHERE year=? ORDER BY id ASC",
        (year,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_my_tournament(year=None, qual=None):
    """[복수대륙컵] '내가 실제로 출전 중/표시 대상'인 대회 1개를 선별 반환.

    qual=None  : 본선/예선 구분 없이 (기존 호환)
    qual=False : 본선 대회만 (world/continent)
    qual=True  : 예선 대회만 (wc_qual)

    우선순위:
      1) my_selected==1 (출전 확정) 대회
      2) my_selected==3 (선택 대기) 대회
      3) 그 외 — 표시용 대표 대회
    """
    from game_engine import get_state
    if year is None:
        st = get_state()
        if not st:
            return None
        year = st["current_year"]
    ts = get_tournaments(year)
    if not ts:
        return None
    # 본선/예선 필터
    if qual is True:
        ts = [t for t in ts if t.get("kind") == "wc_qual"]
    elif qual is False:
        ts = [t for t in ts if t.get("kind") in ("world", "continent")]
    if not ts:
        return None
    for t in ts:
        if t.get("my_selected") == 1:
            return t
    for t in ts:
        if t.get("my_selected") == 3:
            return t
    # [버그수정] my_selected=0(미선발)/2(탈락·미참가)인 대회는 표시용으로만 반환.
    # 기존: kind='world'/'wc_qual'이면 my_selected 무관하게 반환
    # → 예선 미선발(my_selected=0)인데도 월드컵 탭이 보이고 선택창이 다시 뜨는 버그.
    # 수정: my_selected=2(탈락/미참가) 대회만 표시용으로 반환.
    #       my_selected=0(미선발)은 반환하지 않음 — 출전 자격 자체가 없음.
    for t in ts:
        if t.get("my_selected") == 2 and t.get("kind") in ("world", "wc_qual"):
            return t
    for t in ts:
        if t.get("my_selected") == 2:
            return t
    return None


def get_pending_choice():
    """[복수국적·복수대륙컵] 대표팀 선택/동의가 필요한 대회들을 하나로 묶어 반환.

    그 해 my_selected==3(선택 대기)인 모든 대회의 후보 국적을 평탄화해
    최대 3개의 선택지로 제시한다. 각 선택지는 (국적, 대회명, tournament_id)를
    가지므로, 예를 들어 '크로아티아 → 유럽 챔피언십', '대한민국 → 아시안컵'이
    같은 발탁창에 함께 뜬다. 전부 거절도 가능하다.

    [선택 우선 원칙] 후보는 cand_nats(선발 통과국)에서 가져온다. 선택해서
    출전(choose_national_team)하면 그제서야 예선 통과/탈락이 드러나고,
    본선에 출전하면 그 나라로 영구 고정(cap-tie)된다."""
    from game_engine import get_state, get_player
    st = get_state(); p = get_player()
    if not st or not p:
        return None
    ts = [t for t in get_tournaments(st["current_year"])
          if t.get("my_selected") == 3]
    if not ts:
        return None

    conn = get_conn()
    opts = []
    seen = set()   # (nat, tournament_id) 중복 방지
    flag_cache = {}
    for t in ts:
        cand_raw = (t.get("cand_nats", "") or "")
        cand = [n for n in cand_raw.split(",") if n]
        for n in cand:
            key = (n, t["id"])
            if not n or key in seen:
                continue
            seen.add(key)
            if n not in flag_cache:
                fr = conn.execute("SELECT flag FROM countries WHERE name=?", (n,)).fetchone()
                flag_cache[n] = fr["flag"] if fr else ""
            opts.append({"nat": n, "flag": flag_cache[n],
                         "tournament_id": t["id"], "competition": t["name"]})
    conn.close()
    if len(opts) < 1:
        return None
    opts = opts[:3]   # 최대 3개 선택지
    # 대표 tournament_id(구버전 UI 호환): 첫 선택지의 대회.
    # 대회명은 여러 개일 수 있으므로 '/'로 묶어 표기.
    comp_names = []
    for o in opts:
        if o["competition"] not in comp_names:
            comp_names.append(o["competition"])
    return {"tournament_id": opts[0]["tournament_id"],
            "name": " / ".join(comp_names),
            "year": st["current_year"], "options": opts,
            "multi": True}


def choose_national_team(tournament_id, nat):
    """[복수국적] 대표팀 선택 확정 → 그 나라로 고정하고 대회 출전국 설정.
    선발 판정을 다시 수행해 my_selected를 1(선발)/0(미선발)로 갱신."""
    from game_engine import get_player, update_player
    p = get_player()
    if not p:
        return None
    conn = get_conn()
    grow = conn.execute("SELECT grade FROM countries WHERE name=?", (nat,)).fetchone()
    grade = grow["grade"] if grow else "F"
    conn.close()

    # [버그수정] 선택 시점에는 절대 고정하지 않는다.
    #   cap-tie(국적 영구 고정)는 FIFA 규정대로 '본선 A매치 실제 출전' 시점에만
    #   일어나야 한다. 본선 출전 처리는 simulate_my_match()가 담당하며,
    #   여기서는 미선발/예선탈락이어도 고정되지 않아 다음 대회에 다른 나라를
    #   다시 선택할 수 있다. (기존엔 선택 즉시 update_player(intl_committed=nat)을
    #   호출해, 본선에 못 가도 영구 고정돼버리는 버그가 있었다.)
    from game_engine import get_state
    _st = get_state() or {}

    # ── [선택 우선] 결과 공개 순서: ① 컷오프 → ② 선발 여부 → ③ 예선 통과 여부 ──
    from game_engine import add_log
    p = get_player()

    # ① 컷오프 체크: 선택한 나라가 예선 풀 자체에 없으면 예선 진출 실패
    _cut_check = False
    try:
        from constants import WC_QUAL_32, WC_QUAL_48, WC_EXPAND_YEAR, CONFEDERATIONS
        _qc2 = get_conn()
        _trow2 = _qc2.execute("SELECT year, kind, continent FROM intl_tournaments WHERE id=?",
                               (tournament_id,)).fetchone()
        _qc2.close()
        if _trow2 and _trow2["kind"] == "wc_qual":
            _tyear2 = _trow2["year"]
            _tconf2 = (_trow2["continent"] or "").strip()
            _big2 = (_tyear2 + 1) >= WC_EXPAND_YEAR
            _qcfg2 = (WC_QUAL_48 if _big2 else WC_QUAL_32).get(_tconf2, {})
            _all_rows2 = sorted(_enrich_countries(_conf_countries(_tconf2)),
                                key=lambda r: r["ovr"], reverse=True)
            _cutoff2 = _qcfg2.get("cutoff_bottom", 0)
            _cut_names2 = {r["name"] for r in _all_rows2[len(_all_rows2)-_cutoff2:]}
            _cut_check = nat in _cut_names2
    except Exception:
        pass

    if _cut_check:
        # 컷오프 → 예선 진출 실패
        conn2 = get_conn()
        _tr2 = conn2.execute("SELECT year, name FROM intl_tournaments WHERE id=?",
                              (tournament_id,)).fetchone()
        _ty2 = _tr2["year"] if _tr2 else _st.get("current_year")
        _tn2 = _tr2["name"] if _tr2 else ""
        conn2.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=2 WHERE id=?",
                      (nat, tournament_id))
        conn2.commit(); conn2.close()
        try:
            from game_engine import update_player as _upd_cut
            _upd_cut(qual_pledged_nat="")  # 컷오프: pledge 초기화
        except Exception:
            pass
        _save_trophy(_ty2, nat, _tn2, "예선 진출 실패")
        add_log(f"❌ {nat} 월드컵 예선 진출 실패 (랭킹 하위권)", "event")
        return {"nat": nat, "selected": False, "qualified": False,
                "result": "예선진출실패", "kind": "wc_qual"}

    selected = _check_selection(p, grade)

    conn = get_conn()
    trow = conn.execute("SELECT year, name, kind FROM intl_tournaments WHERE id=?",
                        (tournament_id,)).fetchone()
    tyear = trow["year"] if trow else _st.get("current_year")
    tname = trow["name"] if trow else ""
    tkind = trow["kind"] if trow else ""
    # 선택한 나라가 이번 대회 본선에 진출했는가(예선 통과 여부 — 이제야 공개)
    qrow = conn.execute(
        "SELECT 1 FROM intl_entries WHERE tournament_id=? AND country=? LIMIT 1",
        (tournament_id, nat)).fetchone()
    qualified = bool(qrow)

    if not selected:
        # ① 선발 미달 — 예선 결과와 무관하게 이번 대회 출전 없음
        my_sel = 0
        conn.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=? WHERE id=?",
                     (nat, my_sel, tournament_id))
        conn.commit(); conn.close()
        try:
            from game_engine import update_player as _upd_ms
            _upd_ms(qual_pledged_nat="")  # 미선발: pledge 초기화
        except Exception:
            pass
        _save_trophy(tyear, nat, tname, "국가대표 미선발")
        return {"nat": nat, "selected": False, "qualified": qualified, "result": "미선발", "kind": tkind}

    if not qualified:
        # ② 선발은 됐지만 그 나라가 예선 탈락 → 본선 출전 불가
        my_sel = 2
        conn.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=? WHERE id=?",
                     (nat, my_sel, tournament_id))
        conn.commit(); conn.close()
        try:
            from game_engine import update_player as _upd_et
            _upd_et(qual_pledged_nat="")  # 예선탈락: pledge 초기화
        except Exception:
            pass
        _save_trophy(tyear, nat, tname, "예선 탈락")
        return {"nat": nat, "selected": True, "qualified": False, "result": "예선탈락", "kind": tkind}

    # ③ 선발 + 본선 진출 → 정식 출전. 내 경기로 일정 재태깅.
    #   [고정 시점] 실제 영구 고정은 본선 첫 경기 출전 시 simulate_my_match()가
    #   처리한다. 여기서는 본선 확정 사실만 연혁에 commit으로 남긴다.
    my_sel = 1
    conn.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=? WHERE id=?",
                 (nat, my_sel, tournament_id))
    # 이 대회 경기들 중 선택국이 낀 경기를 내 경기로 표시(선택 전에는 후보 전체였음)
    conn.execute("UPDATE intl_matches SET is_my=0 WHERE tournament_id=?", (tournament_id,))
    conn.execute("UPDATE intl_matches SET is_my=1 WHERE tournament_id=? AND (home=? OR away=?)",
                 (tournament_id, nat, nat))
    # [복수대륙컵] 본선 출전을 확정했으므로, 같은 해 다른 '선택 대기(3)' 대회는
    #   이번엔 출전하지 않는 것으로 마감(my_selected=2). 한 해에 본선 출전(1)은
    #   동시에 1개만 존재하도록 보장한다. (명시적 거절 기록은 남기지 않음 —
    #   다른 나라를 골랐을 뿐이며, 이 나라로 cap-tie되면 다음 해부터 자동 정리됨.)
    if tyear is not None:
        conn.execute(
            "UPDATE intl_tournaments SET my_selected=2 "
            "WHERE year=? AND id<>? AND my_selected=3",
            (tyear, tournament_id))
    conn.commit(); conn.close()
    # [국적 연혁] 본선 출전 확정 → 대표 국적 commit 기록 (중복은 add_nat_history가 무시)
    #   단, 예선(wc_qual)은 영구고정이 아니므로 commit 기록하지 않는다.
    #   (예선은 cap-tie 안 됨 → 다음 예선 때 다른 나라 선택 가능)
    _is_qual = False
    try:
        _cc = get_conn()
        _kr = _cc.execute("SELECT kind FROM intl_tournaments WHERE id=?",
                          (tournament_id,)).fetchone()
        _cc.close()
        if _kr and _kr["kind"] == "wc_qual":
            _is_qual = True
    except Exception:
        pass
    if _is_qual:
        # 예선 선택: cap-tie는 안 하되, 이 사이클(예선→본선) 동안 그 나라로 출전하도록 pledge
        try:
            from game_engine import update_player as _upd2
            _upd2(qual_pledged_nat=nat)
        except Exception:
            pass
    else:
        try:
            from game_engine import add_nat_history
            _fl = ""
            for _nk, _fk in (("nationality","flag"),("nationality2","flag2"),("nationality3","flag3")):
                if (p.get(_nk,"") or "") == nat:
                    _fl = p.get(_fk,"") or ""; break
            add_nat_history("commit", nat, _fl,
                            _st.get("current_year"), _st.get("current_week"))
        except Exception:
            pass
    return {"nat": nat, "selected": True, "qualified": True, "result": "선발", "kind": tkind}


def decline_national_team(tournament_id):
    """[복수국적·복수대륙컵] 이번 대표팀 발탁을 거절(보류). 영구 고정하지 않는다.

    같은 해에 선택 대기(my_selected==3)인 대회가 여러 개면(여러 대륙컵) 전부
    거절 처리한다. 즉 발탁창의 '전부 거절'에 해당한다. 다음 대회에서 다시 제안된다.

    [거절 기록] 거절도 커리어에 남긴다 (은퇴창/AI요약 표시용).
      - year       : 거절한 연도
      - team_name  : 거절한 후보 국가 전부 (대회 통합, 예: '크로아티아/대한민국')
      - league_name: '발탁 거절'
      - competition: 대회명(들)
    과거 거절은 나중에 같은 나라 대표로 뛰어도 그대로 남는다(역사 보존)."""
    conn = get_conn()
    trow = conn.execute(
        "SELECT year FROM intl_tournaments WHERE id=?", (tournament_id,)).fetchone()
    year = trow["year"] if trow else None
    # 그 해 선택 대기 대회 전부 수집 (후보 국가/대회명 통합 기록용)
    cand_all = []
    comp_all = []
    if year is not None:
        rows = conn.execute(
            "SELECT name, cand_nats FROM intl_tournaments WHERE year=? AND my_selected=3",
            (year,)).fetchall()
        for r in rows:
            if r["name"] and r["name"] not in comp_all:
                comp_all.append(r["name"])
            for n in (r["cand_nats"] or "").split(","):
                if n and n not in cand_all:
                    cand_all.append(n)
        conn.execute(
            "UPDATE intl_tournaments SET my_nat='', my_selected=2 "
            "WHERE year=? AND my_selected=3", (year,))
    else:
        conn.execute("UPDATE intl_tournaments SET my_nat='', my_selected=2 WHERE id=?",
                     (tournament_id,))
    conn.commit(); conn.close()

    # [버그수정] 거절 시 qual_pledged_nat 초기화
    # 예선 발탁 거절했는데 qual_pledged_nat가 남아있으면
    # 다음 해 월드컵 본선에서 pledged 로직으로 자동 참가하는 버그가 있었음
    try:
        from game_engine import update_player as _upd_p
        _upd_p(qual_pledged_nat="")
    except Exception:
        pass

    if year is not None:
        nat_str = "/".join(cand_all) if cand_all else "대표팀"
        comp_str = " / ".join(comp_all) if comp_all else "대륙컵"
        _save_decline(year, nat_str, comp_str)
    return True


def _save_decline(year, nat_str, competition):
    """[거절 기록] 발탁 거절을 trophy_log(tier=0)에 남긴다.
    같은 (year, competition)에 거절 기록이 이미 있으면 중복 방지.
    선발/예선 결과 줄과 별개로 '발탁 거절' 줄을 따로 남길 수 있도록
    league_name='발탁 거절' 조건까지 함께 본다."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=? AND league_name=?",
        (year, competition, "발탁 거절")).fetchone()
    if not existing:
        conn.execute(
            """INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
               VALUES(?,?,?,0,?)""", (year, nat_str, "발탁 거절", competition))
        conn.commit()
    conn.close()


def get_forced_commit():
    """[복수국적] 22세 1~4주차(비시즌)에 아직 대표팀을 고정하지 않았다면,
    강제로 국적을 정하게 하는 정보 반환. 없으면 None.

    - 조건: 미고정(intl_committed=='') + 나이 == 22 + 현재 주차 1~4
    - 본선 진출 여부와 무관하게 '보유 국적 전부'를 선택지로 제공한다.
      (대회에 안 나가도 평생 뛸 대표팀을 이 시점에 확정)
    - 이 선택으로 intl_committed만 설정되며, 보유 국적(nationality/2/3)은
      그대로 유지된다(국적이 사라지지 않음).
    """
    from game_engine import get_state, get_player
    st = get_state(); p = get_player()
    if not st or not p:
        return None
    if (p.get("intl_committed", "") or ""):
        return None   # 이미 고정됨
    week = st.get("current_week", 0)
    if not (1 <= week <= 4):
        return None
    year = st.get("current_year", 0)
    # [버그수정] 나이는 'age' 컬럼이 정확하다. birth_year는 게임 내내 갱신되지 않아
    #   (year - birth_year)는 실제 나이보다 16 적게 나온다 → 22세 판정이 영원히 실패했었다.
    age = p.get("age", 0) or 0
    if age != 22:
        return None

    # 보유 국적 전부 (본선 진출 무관)
    pairs = [
        (p.get("nationality", "")  or "", p.get("flag", "")  or ""),
        (p.get("nationality2", "") or "", p.get("flag2", "") or ""),
        (p.get("nationality3", "") or "", p.get("flag3", "") or ""),
    ]
    opts = []
    seen = set()
    for nat, flag in pairs:
        if nat and nat not in seen:
            seen.add(nat)
            opts.append({"nat": nat, "flag": flag})
    if not opts:
        return None
    return {"forced": True, "year": year, "options": opts}


def commit_nationality(nat):
    """[복수국적] 22세 강제 선택 확정 → intl_committed만 그 나라로 설정.
    보유 국적(nationality/2/3)은 건드리지 않아 사라지지 않는다."""
    from game_engine import get_player, update_player
    p = get_player()
    if not p:
        return None
    update_player(intl_committed=nat)
    # [국적 연혁] 22세 강제확정 사건 기록
    try:
        from game_engine import add_nat_history, get_state
        _st = get_state() or {}
        _fl = ""
        for _nk, _fk in (("nationality","flag"),("nationality2","flag2"),("nationality3","flag3")):
            if (p.get(_nk,"") or "") == nat:
                _fl = p.get(_fk,"") or ""; break
        add_nat_history("commit", nat, _fl,
                        _st.get("current_year"), _st.get("current_week"))
    except Exception:
        pass
    return {"nat": nat}


def fmt_nationalities(p):
    """[복수국적] 보유 국적 전부를 '국기+이름' 문자열로 (예: '🇦🇹오스트리아 / 🇵🇦파나마')."""
    if not p:
        return ""
    pairs = [
        (p.get("nationality", "")  or "", p.get("flag", "")  or ""),
        (p.get("nationality2", "") or "", p.get("flag2", "") or ""),
        (p.get("nationality3", "") or "", p.get("flag3", "") or ""),
    ]
    seen = set(); out = []
    for nat, flag in pairs:
        if nat and nat not in seen:
            seen.add(nat)
            out.append(f"{flag}{nat}")
    return " / ".join(out)


def fmt_rep_nationality(p):
    """[복수국적] 축구 대표로 뛰는 국적(국기 포함). 미고정이면 '미정'."""
    if not p:
        return "미정"
    rep = p.get("intl_committed", "") or ""
    if not rep:
        return "미정"
    # 국기 찾기
    for nat_key, flag_key in (("nationality", "flag"),
                              ("nationality2", "flag2"),
                              ("nationality3", "flag3")):
        if (p.get(nat_key, "") or "") == rep:
            return f"{p.get(flag_key, '') or ''}{rep}"
    return rep


def _my_nat(t, p):
    """[복수국적] 이 대회에서 내가 뛰는 나라.
    대회에 저장된 my_nat 우선, 없으면(구 세이브) 주 국적으로 폴백."""
    if t:
        mn = t.get("my_nat") if isinstance(t, dict) else t["my_nat"]
        if mn:
            return mn
    return (p.get("nationality", "") if p else "") or ""


def _active_tournament():
    from game_engine import get_state
    st = get_state()
    if not st:
        return None
    t = get_my_tournament(st["current_year"])
    if t and t["status"] != "done":
        return t
    return t  # done이어도 반환 (UI 표시용) ─ 호출부에서 status 체크


def get_my_match(week):
    """이번 주차에 내가 뛸 국가대표 경기가 있으면 dict, 없으면 None."""
    from game_engine import get_player, get_state
    p = get_player()
    st = get_state()
    if not p or not st:
        return None
    t = get_my_tournament(st["current_year"])
    if not t or t["status"] == "done" or t["my_selected"] != 1:
        return None
    nat = _my_nat(t, p)
    conn = get_conn()
    m = conn.execute(
        """SELECT * FROM intl_matches
           WHERE tournament_id=? AND week=? AND home_score=-1
             AND (home=? OR away=?)""",
        (t["id"], week, nat, nat)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home"] == nat)
    opp = m["away"] if is_home else m["home"]
    fr = conn.execute("SELECT flag FROM intl_entries WHERE tournament_id=? AND country=?",
                      (t["id"], opp)).fetchone()
    conn.close()
    return {
        "intl": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],
        "stage": m["stage"],
        "stage_ko": STAGE_KO.get(m["stage"], m["stage"]),
        "grp": m["grp"],
        "opp": opp,
        "opp_flag": fr["flag"] if fr else "",
        "is_home": is_home,
        "week": week,
    }


def has_my_match_between(week_from, week_to):
    """주차 범위 내 내 국가대표 경기 존재 여부 (센터패널 표시용)."""
    for w in range(week_from, week_to + 1):
        if get_my_match(w):
            return True
    return False


# ─────────────────────────────────────────────
# 대회 생성 (17주차 진입 시)
# ─────────────────────────────────────────────

def start_intl_tournament(year):
    """17주차 진입 시 호출. 해당 연도에 대회가 있으면 생성.

    [복수국적·복수대륙컵] 미고정 선수가 서로 다른 대륙 국적을 보유하면,
    대륙컵 해에는 보유 국적이 속한 '각 대륙'의 대륙컵을 모두 생성한다.
    (예: 크로아티아(유럽)+대한민국(아시아) → 유럽 챔피언십 + 아시안컵 둘 다)
    월드컵 해에는 종전과 동일하게 단일 대회만 생성한다.
    committed(고정)면 그 나라 대륙의 대륙컵 1개만 생성한다.
    """
    from game_engine import get_player
    p = get_player()
    if not p:
        return

    is_wc = year >= WC_START_YEAR and (year - WC_START_YEAR) % WC_INTERVAL == 0
    is_cont = (not is_wc and year >= CONTINENTAL_START_YEAR
               and (year - CONTINENTAL_START_YEAR) % CONTINENTAL_INTERVAL == 0)

    # ── 예선 판정: 다음 해가 월드컵 해이면 올해는 월드컵 예선 ──
    # [정책] 대륙컵 예선(cont_qual)은 폐지. 월드컵 예선(wc_qual)만 운영.
    nxt = year + 1
    is_wc_qual = (not is_wc and not is_cont
                  and nxt >= WC_START_YEAR
                  and (nxt - WC_START_YEAR) % WC_INTERVAL == 0)

    if not is_wc and not is_cont and not is_wc_qual:
        return
    if get_tournaments(year):
        return  # 이미 그 해 대회가 하나라도 생성됨 → 중복 생성 방지

    _clear_entry_cache()   # 새 대회 → 이전 캐시 무효화

    # 보유 국적 / 고정 여부
    nat1 = p.get("nationality", "") or ""
    nat2 = p.get("nationality2", "") or ""
    nat3 = p.get("nationality3", "") or ""
    committed = p.get("intl_committed", "") or ""
    if committed:
        my_nats = [committed]
    else:
        my_nats = [n for n in (nat1, nat2, nat3) if n]

    # 각 국적의 대륙/등급 조회 ([최적화] IN 쿼리 1회로 합산)
    conn = get_conn()
    nat_info = {}
    if my_nats:
        _ph = ",".join("?" * len(my_nats))
        for r in conn.execute(
                f"SELECT name, continent, grade FROM countries WHERE name IN ({_ph})",
                my_nats).fetchall():
            nat_info[r["name"]] = {"continent": r["continent"], "grade": r["grade"]}
    conn.close()

    if is_wc:
        # 월드컵: 전 세계 단일 대회. (대륙 개념 없음)
        _create_one_tournament(year, is_wc=True, my_continent=None,
                               p=p, my_nats=my_nats, nat_info=nat_info,
                               committed=committed)
        return

    # ── 월드컵 예선: 4개 대륙 연맹 전부 예선 생성 ──
    #   각 연맹별로 _create_qual_tournament 호출.
    #   내 국적이 속한 연맹만 my_sel=1/2/3, 나머지는 my_sel=2(출전 없음).
    #   통과국은 _finalize_qual이 qual_results에 저장 → 다음 해 본선 entries 구성.
    if is_wc_qual:
        all_confs = ["유럽", "아메리카", "아시아", "아프리카"]
        for conf in all_confs:
            _create_qual_tournament(year, "wc_qual", conf,
                                    p=p, my_nats=my_nats, nat_info=nat_info,
                                    committed=committed)
        return

    # ── 대륙컵: 4개 대륙 연맹 전부 생성 ──
    #   [변경] 예전엔 '내 국적이 속한 대륙'만 만들었으나, 챔피언스리그처럼
    #   4개 대륙 전부 항상 생성하도록 확장 — 다른 대륙 대회 결과도 역대기록에서
    #   조회 가능해짐. _create_one_tournament 내부는 my_nats를 그 대륙 국적과
    #   교집합해서 참가여부(my_sel)를 판정하므로, 내 국적이 없는 대륙은 그냥
    #   'AI끼리 진행되는 배경 대회'가 되고 선택창 등 기존 동작에는 영향 없다.
    #   [성능] _qualify_continental은 countries 테이블 등급 기반 통계 계산이라
    #   club 리그 시뮬레이션처럼 무거운 의존성이 없음 — 4년에 1번, 대회 4개
    #   추가되는 정도라 챔스(매년 4대륙 32팀)보다도 가벼움.
    all_confs = ["유럽", "아메리카", "아시아", "아프리카"]
    for cont in all_confs:
        _create_one_tournament(year, is_wc=False, my_continent=cont,
                               p=p, my_nats=my_nats, nat_info=nat_info,
                               committed=committed)

    # [본선 자동출전 정리] pledge/committed로 my_sel=1 확정된 본선이 있으면,
    #   같은 해 다른 '선택 대기(3)' 본선 대회는 닫는다(한 해 본선 출전 1개 보장).
    _close_other_pending_when_committed(year)


def _close_other_pending_when_committed(year):
    """그 해 본선 중 my_selected=1(자동/확정 출전)이 있으면,
    같은 해 나머지 본선 '선택 대기(3)'를 닫는다(my_selected=2).
    예선 pledge로 본선 자동출전한 경우, 다른 대륙 본선 선택창이 또 뜨는 것 방지."""
    conn = get_conn()
    has_committed = conn.execute(
        """SELECT 1 FROM intl_tournaments
           WHERE year=? AND my_selected=1
             AND kind IN ('world','continent') LIMIT 1""", (year,)).fetchone()
    if has_committed:
        conn.execute(
            """UPDATE intl_tournaments SET my_selected=2
               WHERE year=? AND my_selected=3 AND kind IN ('world','continent')""",
            (year,))
        conn.commit()
    conn.close()


def _conf_key(continent):
    """대륙명 → 4개 통합 연맹 대표키.
    유럽/아메리카/아시아/아프리카로 정규화."""
    return CONTINENT_TO_CONF.get(continent, continent)


def _create_one_tournament(year, is_wc, my_continent, p, my_nats, nat_info, committed):
    """대회 1개를 생성(조 추첨·일정 포함)하고 로그를 남긴다.

    - is_wc=True  : 월드컵(전 세계 단일). my_continent 무시.
    - is_wc=False : my_continent 대륙컵. cand_nats는 그 대륙 소속 보유국적만.
    """
    from game_engine import add_log

    if is_wc:
        kind, name = "world", "월드컵"
        entries = _qualify_world(year)
        n_groups = WC_GROUPS_BIG if year >= WC_EXPAND_YEAR else WC_GROUPS
        # 월드컵은 대륙 무관 → 내 국적 전부가 후보 대상
        cont_nats = [n for n in my_nats if n]
    else:
        kind = "continent"
        name = CONF_CUP_NAME.get(my_continent, "대륙컵")
        entries = _qualify_continental(my_continent)
        n_groups = CONT_GROUPS
        # 이 대륙컵 후보 = 그 대륙(연맹) 소속 보유 국적만
        confs = set(CONFEDERATIONS.get(my_continent, [my_continent]))
        cont_nats = [n for n in my_nats
                     if nat_info.get(n, {}).get("continent") in confs]

    entry_names = {e["name"] for e in entries}
    # 이 대회 본선 진출한 내 국적
    qualified_nats = [n for n in cont_nats if n in entry_names]

    # 출전국/선발 결정
    my_nat = ""
    cand_nats = []
    # [정책] pledge는 wc_qual → 월드컵 본선 연계에만 사용.
    #        대륙컵은 예선이 없으므로 pledge가 있어도 대륙컵 발탁에 영향 없음.
    pledged = (p.get("qual_pledged_nat", "") or "") if is_wc else ""
    if committed:
        # [bugfix] committed path: also check wc_qual result
        #   If wc_qual existed last year, committed player who was
        #   not selected (my_selected=0) or eliminated (my_selected=2)
        #   must NOT participate in the main tournament.
        if committed not in cont_nats:
            my_sel = 2
        elif committed in qualified_nats:
            _blocked_by_qual = False
            if is_wc:
                try:
                    _bqc = get_conn()
                    _bqr = _bqc.execute(
                        """SELECT my_selected FROM intl_tournaments
                           WHERE year=? AND kind='wc_qual' AND my_nat=?
                           LIMIT 1""",
                        (year - 1, committed)).fetchone()
                    _bqc.close()
                    if _bqr is not None:
                        _blocked_by_qual = (_bqr["my_selected"] != 1)
                except Exception:
                    pass
            if _blocked_by_qual:
                my_sel = 2
            else:
                my_nat = committed
                my_sel = 1  # 예선 통과 = 본선 선발 보장, 재판정 없음
        else:
            my_sel = 2
    elif pledged and pledged in cont_nats:
        # [월드컵 예선 연계] 예선에서 pledge한 나라가 이 월드컵 후보군에 있음.
        #   본선 해엔 선택창을 띄우지 않고 그 나라로 자동 출전한다.
        if pledged in qualified_nats:
            my_nat = pledged
            my_sel = 1  # 예선 통과 = 본선 선발 보장, 재판정 없음
        else:
            my_sel = 2   # pledge한 나라가 본선 진출 실패(예선 탈락) → 출전 없음
    else:
        # 미고정 + pledge 없음.
        #   [월드컵] 작년 wc_qual 예선이 있었는데 pledge가 없다
        #            = 예선 미선발/탈락 → 본선 출전 불가.
        #   [대륙컵] 대륙컵은 예선이 없으므로 무조건 발탁창을 띄운다.
        _had_wc_qual = False
        if is_wc:
            try:
                _cc = get_conn()
                _qr = _cc.execute(
                    """SELECT 1 FROM intl_tournaments
                       WHERE year=? AND kind='wc_qual' LIMIT 1""",
                    (year - 1,)).fetchone()
                _cc.close()
                _had_wc_qual = bool(_qr)
            except Exception:
                pass

        cand_nats = [n for n in cont_nats if n]
        if is_wc and _had_wc_qual:
            # 작년 예선이 있었음 → 예선에서 내 처리 결과 확인
            # my_selected=0(미선발)/2(예선탈락·미참가) → 본선 발탁창 없음
            # my_selected=1(예선출전확정) + pledge → 본선 발탁창 제시
            _qual_results = []
            try:
                _qc = get_conn()
                _qual_results = [dict(r) for r in _qc.execute(
                    "SELECT my_selected, my_nat FROM intl_tournaments"
                    " WHERE year=? AND kind='wc_qual'",
                    (year - 1,)).fetchall()]
                _qc.close()
            except Exception:
                pass
            # 예선에서 선발 확정(my_selected=1)된 대회가 하나라도 있고
            # 그 나라가 본선에도 진출했으면 발탁창 제시
            _qual_passed_nats = [
                r["my_nat"] for r in _qual_results
                if r["my_selected"] == 1 and r["my_nat"] in entry_names
            ]
            if _qual_passed_nats:
                my_sel = 3
                cand_nats = _qual_passed_nats
            else:
                # 예선 미선발/탈락/미참가 → 본선 발탁창 없음
                my_sel = 2
                cand_nats = []
        elif cand_nats:
            my_sel = 3   # 발탁창 제시
        else:
            my_sel = 2   # 이 대회에 낄 수 있는 보유 국적 없음

    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO intl_tournaments(year, kind, name, status, my_selected, my_nat, cand_nats)
                 VALUES(?,?,?,?,?,?,?)""",
              (year, kind, name, "group", my_sel, my_nat, ",".join(cand_nats)))
    tid = c.lastrowid

    # 포트 추첨: 전력순 4개 포트 → 조마다 포트별 1팀
    entries.sort(key=lambda e: e["ovr"], reverse=True)
    pot_size = len(entries) // 4
    groups = {g: [] for g in _GROUP_LABELS[:n_groups]}
    for pot in range(4):
        pool = entries[pot * pot_size:(pot + 1) * pot_size]
        random.shuffle(pool)
        for gi, e in enumerate(pool):
            g = _GROUP_LABELS[gi]
            groups[g].append(e)
            c.execute("""INSERT INTO intl_entries
                         (tournament_id, country, flag, grade, ovr, grp, pot, alive)
                         VALUES(?,?,?,?,?,?,?,1)""",
                      (tid, e["name"], e["flag"], e["grade"], e["ovr"], g, pot + 1))

    # 조별리그 일정 (18~20주)
    w0 = INTL_GROUP_WEEKS[0]
    if my_nat:
        _my_match_nats = {my_nat}
    elif my_sel == 3:
        _my_match_nats = set(cand_nats)
    else:
        # my_sel==2: 출전 없음 → 내 경기 없음 (기존엔 cont_nats 전체가 들어가던 버그)
        _my_match_nats = set()
    for rd, pairs in enumerate(_GROUP_ROUNDS):
        wk = w0 + rd
        for g, members in groups.items():
            for hi, ai in pairs:
                home, away = members[hi], members[ai]
                is_my = 1 if (home["name"] in _my_match_nats or away["name"] in _my_match_nats) else 0
                c.execute("""INSERT INTO intl_matches
                             (tournament_id, stage, grp, week, home, away,
                              home_score, away_score, is_my, slot)
                             VALUES(?,?,?,?,?,?,-1,-1,?,0)""",
                          (tid, "group", g, wk, home["name"], away["name"], is_my))
    conn.commit()
    conn.close()

    # ── 로그 ──
    add_log("─" * 44, "sep")
    add_log(f"🌍 {year}년 {name} 개막!  본선 {len(entries)}개국", "event", year, INTL_CALLUP_WEEK)

    if my_sel == 3:
        nat_list = " / ".join(cand_nats)
        if len(cand_nats) == 1:
            add_log(f"   🌍 {nat_list} 대표팀에서 발탁을 제안합니다! 출전 여부를 선택하세요",
                    "event", year, INTL_CALLUP_WEEK)
        else:
            add_log(f"   🌍 여러 나라가 당신을 원합니다! {nat_list} 중 대표팀을 선택하세요",
                    "event", year, INTL_CALLUP_WEEK)
    elif my_nat:
        _grow = _country_flag(my_nat)
        try:
            my_g = next(g for g, ms in groups.items() if any(m["name"] == my_nat for m in ms))
            mates = [f"{m['flag']}{m['name']}" for m in groups[my_g] if m["name"] != my_nat]
            add_log(f"   {_grow}{my_nat} {my_g}조 편성  (vs {', '.join(mates)})",
                    "event", year, INTL_CALLUP_WEEK)
        except StopIteration:
            pass
        if my_sel == 1:
            add_log(f"   📣 국가대표 소집! 조별리그 {w0}~{w0+2}주차", "event", year, INTL_CALLUP_WEEK)
        else:
            add_log("   📋 국가대표 미선발... 대표팀 경기를 지켜봅니다", "event", year, INTL_CALLUP_WEEK)
            _save_trophy(year, my_nat, name, "국가대표 미선발")
    else:
        # my_sel==2
        #   committed가 이 대회 대륙이고 예선 탈락한 경우만 기록.
        #   (committed가 타 대륙이라 이 대회와 무관하면 아무 기록도 남기지 않는다.)
        if committed and committed in cont_nats:
            add_log(f"   📋 {committed} 예선 탈락 — 이번 대회 출전 없음", "event", year, INTL_CALLUP_WEEK)
            _save_trophy(year, committed, name, "예선 탈락")

def _country_flag(name):
    """국가 국기 조회 (없으면 빈 문자열)."""
    conn = get_conn()
    r = conn.execute("SELECT flag FROM countries WHERE name=?", (name,)).fetchone()
    conn.close()
    return r["flag"] if r else ""


def _vet_bonus(age):
    """베테랑 가산점: 노련함 프리미엄. 최대 +5. (절대 하한은 별도 보장)"""
    if age >= 36:
        return 5
    if age >= 33:
        return 4
    if age >= 30:
        return 2
    return 0


def _check_selection(p, my_grade):
    """국가대표 선발 판정 — 자국 등급(국대 평균 전력) 대비 상대평가.

    선발 기준을 클럽 선수 풀이 아니라 GRADE_TEAM_OVR(국대 경기 시뮬에 쓰는
    그 나라 평균 전력)에 정합시킨다. 선발 스케일 = 경기 스케일.
      임계 = 국대평균 - 마진(톱권)
      유효OVR = 내 OVR + 베테랑 보너스(최대 +5)
      → 유효OVR이 임계 이상이면 선발. 단 절대 하한 미달은 보너스로도 구제 불가.
    """
    nat_avg = GRADE_TEAM_OVR.get(my_grade, 45)
    threshold = nat_avg - INTL_SELECTION_MARGIN
    my_ovr = p.get("ovr", 0)
    eff_ovr = my_ovr + _vet_bonus(p.get("age", 25))

    # 절대 하한: 베테랑 보너스로도 이 밑이면 탈락 (36세 60 같은 경우 차단)
    if my_ovr < threshold - INTL_SELECTION_MARGIN:
        return False
    if eff_ovr < threshold:
        return False
    if p.get("total_matches", 0) < INTL_MIN_MATCHES:
        return False
    tid = p.get("current_team_id", 0)
    if not tid:
        return False
    # 티어 보조 가드(완화): 등급 기준 티어보다 한 단계까지는 허용
    #   → 노쇠해 하위 리그로 내려간 베테랑도 OVR이 충분하면 막지 않는다.
    conn = get_conn()
    row = conn.execute(
        """SELECT l.tier FROM teams t JOIN leagues l ON t.league_id=l.id
           WHERE t.id=?""", (tid,)).fetchone()
    conn.close()
    my_tier = row["tier"] if row else 99
    return my_tier <= INTL_MAX_TIER.get(my_grade, 3) + 1


def _qualify_world(year=0):
    """4개 대륙 연맹의 예선 결과(qual_results)를 조합해 본선 진출국 확정.

    - 예선 결과가 있는 연맹: qual_results에서 읽어 그대로 사용
    - 예선 결과가 없는 연맹(이전 세이브 호환 등): 등급 기반 랜덤 선발
    - 쿼터 합산이 본선 팀 수(32 or 48)와 맞지 않으면 부족분을 랜덤으로 보충
    """
    big = (year >= WC_EXPAND_YEAR)
    quota_map = WC_QUOTA_BIG if big else WC_QUOTA
    n_teams   = WC_TEAMS_BIG  if big else WC_TEAMS

    # 예선 통과국 로드
    conn = get_conn()
    qual_rows = [dict(r) for r in conn.execute(
        "SELECT country, flag, grade, ovr, continent FROM qual_results WHERE target_year=? AND kind='world'",
        (year,)).fetchall()]
    all_countries = [dict(r) for r in conn.execute(
        "SELECT name, flag, continent, grade FROM countries").fetchall()]
    conn.close()

    # 오세아니아 → 아시아, 북중미/북미 → 아메리카 정규화
    for r in all_countries:
        r["conf"] = _conf_key(r["continent"])
        r["ovr"]  = GRADE_TEAM_OVR.get(r["grade"], 45) + random.uniform(-3, 3)
        r["qual"] = GRADE_QUAL_BASE.get(r["grade"], 0.2) + random.uniform(-QUAL_NOISE, QUAL_NOISE)

    # 예선 결과가 있는 연맹 추적
    qual_by_conf = {}   # conf_key → [국가dict]
    for q in qual_rows:
        ck = _conf_key(q.get("continent", ""))
        if not ck:
            continue
        qual_by_conf.setdefault(ck, []).append({
            "name": q["country"], "flag": q["flag"],
            "continent": ck, "grade": q["grade"], "ovr": q["ovr"], "qual": 1.0
        })

    picked = []
    picked_names = set()

    # 1) 예선 결과 있는 연맹: 그대로 사용 (쿼터만큼)
    for conf, quota in quota_map.items():
        ck = _conf_key(conf)
        if ck in qual_by_conf:
            teams = qual_by_conf[ck][:quota]
            picked.extend(teams)
            picked_names.update(t["name"] for t in teams)

    # 2) 예선 결과 누락 연맹 → 오류 로그 + 예외 발생
    # 예선 시스템이 정상 작동한다면 모든 연맹에 qual_results가 있어야 한다.
    # 랜덤 보충은 "예선이 없던 시대" 호환용이었으나, 현재는 예선 버그를
    # 숨기는 역할만 하므로 제거하고 명시적 오류를 발생시킨다.
    missing_confs = []
    for conf in quota_map:
        ck = _conf_key(conf)
        if ck not in qual_by_conf:
            missing_confs.append(conf)
    if missing_confs:
        from game_engine import add_log
        msg = f"[오류] {year}년 월드컵 본선: 예선 결과 누락 — {missing_confs}"
        add_log(msg, "event")
        raise RuntimeError(msg)

    # 각 연맹 쿼터 충족 여부 검증 (초과는 허용, 미달만 오류)
    for conf, quota in quota_map.items():
        ck = _conf_key(conf)
        actual = len(qual_by_conf.get(ck, []))
        if actual < quota:
            from game_engine import add_log
            msg = (f"[오류] {year}년 월드컵 본선: {conf} 예선 통과국 {actual}팀 < 쿼터 {quota}팀"
                   f" — 예선이 완료되지 않았거나 qual_results가 불완전합니다")
            add_log(msg, "event")
            raise RuntimeError(msg)

    return picked[:n_teams]


def _qualify_continental(my_continent):
    """내 대륙 연맹의 대륙컵 24개국 선발 (남북미 통합, 오세아니아→아시아).
    작년 예선 결과(qual_results)가 있으면 우선 사용, 없으면 랜덤 계산."""
    from game_engine import get_state
    st = get_state() or {}
    year = st.get("current_year", 0)
    cont_key = _conf_key(my_continent)

    conn = get_conn()
    qual_rows = [dict(r) for r in conn.execute(
        """SELECT country, flag, grade, ovr FROM qual_results
           WHERE target_year=? AND kind='continent' AND continent=?""",
        (year, cont_key)).fetchall()]
    conn.close()

    if qual_rows:
        # 예선 통과국 사용
        result = [{"name": q["country"], "flag": q["flag"], "grade": q["grade"],
                   "ovr": q["ovr"]} for q in qual_rows]
        return result[:CONT_TEAMS]

    # 폴백: 기존 랜덤 방식
    confs = CONFEDERATIONS.get(my_continent, [my_continent])
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"SELECT name, flag, continent, grade FROM countries WHERE continent IN ({','.join('?'*len(confs))})",
        confs).fetchall()]
    conn.close()
    for r in rows:
        r["qual"] = GRADE_QUAL_BASE.get(r["grade"], 0.2) + random.uniform(-QUAL_NOISE, QUAL_NOISE)
        r["ovr"] = GRADE_TEAM_OVR.get(r["grade"], 45) + random.uniform(-3, 3)
    rows.sort(key=lambda r: r["qual"], reverse=True)
    return rows[:CONT_TEAMS]


# ─────────────────────────────────────────────
# 예선 대회 생성
# ─────────────────────────────────────────────

def _continent_qual_quota(qual_kind, continent, year):
    """이 대륙(연맹)이 다음 해 본선에서 차지하는 진출 쿼터(장수)."""
    if qual_kind == "wc_qual":
        big = (year + 1) >= WC_EXPAND_YEAR
        quota_map = WC_QUOTA_BIG if big else WC_QUOTA
        ck = _conf_key(continent)
        return quota_map.get(ck, 4)
    else:
        return CONT_TEAMS


def _conf_countries(conf_key):
    """연맹 대표키 → 해당 연맹 전체 국가 목록 (DB 조회)."""
    confs = CONFEDERATIONS.get(conf_key, [conf_key])
    conn = get_conn()
    placeholders = ",".join("?" * len(confs))
    rows = [dict(r) for r in conn.execute(
        f"SELECT name, flag, continent, grade FROM countries WHERE continent IN ({placeholders})",
        confs).fetchall()]
    conn.close()
    return rows


def _enrich_countries(rows):
    """국가 목록에 ovr/qual 점수 추가."""
    for r in rows:
        r["ovr"]  = GRADE_TEAM_OVR.get(r["grade"], 45) + random.uniform(-3, 3)
        r["qual"] = GRADE_QUAL_BASE.get(r["grade"], 0.2) + random.uniform(-QUAL_NOISE, QUAL_NOISE)
    return rows


def _sim_single_match_ai(home, away):
    """단판 AI vs AI 시뮬. 전력 기반 확률로 승패 결정. 승자 dict 반환."""
    h_str = home.get("ovr", 50); a_str = away.get("ovr", 50)
    total = h_str + a_str
    h_win_p = (h_str / total) * 0.6 + 0.2   # 홈 어드밴티지 포함
    r = random.random()
    return home if r < h_win_p else away


def _create_qual_tournament(year, qual_kind, continent, p, my_nats, nat_info, committed):
    """월드컵 예선 대회 생성 (4개 대륙 전부).

    단계:
      18주차 — 1차 예선 (하위국 단판, DB에 qual_r1 스테이지로 저장)
      19~24주차 — 조별리그 (qual_group, 홈앤어웨이 6경기)
      25주차 — 플레이오프 (qual_po, 단판 / 32팀 체제만)

    내 국적이 이 연맹에 속하면 is_my=1, 아니면 0.
    통과국은 _finalize_qual이 qual_results에 저장.
    """
    from game_engine import add_log

    big = (year + 1) >= WC_EXPAND_YEAR
    qual_cfg = WC_QUAL_48.get(continent) if big else WC_QUAL_32.get(continent)
    if not qual_cfg:
        return

    # 예선 사이클 리셋 (pledge 해제)
    if not committed:
        try:
            from game_engine import update_player as _upd0
            _upd0(qual_pledged_nat="")
        except Exception:
            pass

    # 연맹 전체 국가 조회
    all_rows = _enrich_countries(_conf_countries(continent))
    if len(all_rows) < 4:
        return

    # FIFA 랭크(ovr) 기준 정렬
    all_rows.sort(key=lambda r: r["ovr"], reverse=True)

    # ─── 1차 예선 컷오프: 실제 경기 없이 하위국 제외 ───
    # 하위국은 조별리그 진출 실패로 성적에만 기록 (대륙컵 예선 미참가 방식과 동일)
    cutoff_n  = qual_cfg["cutoff_bottom"]
    top_rows  = all_rows[: len(all_rows) - cutoff_n]   # 조별리그 진출
    cut_rows  = all_rows[len(all_rows) - cutoff_n:]    # 예선 진출 실패
    cut_names = {r["name"] for r in cut_rows}

    # 내 나라 컷오프 여부는 my_nats/committed 기준으로만 판정 (sel_cand 계산 전)
    my_cut_nat = ""
    check_nats = [committed] if committed else [n for n in my_nats if n]
    for cand in check_nats:
        if cand in cut_names:
            my_cut_nat = cand
            break

    pool = list(top_rows)
    random.shuffle(pool)

    # ─── 내 국적 처리 ───
    cont_set = set(CONFEDERATIONS.get(continent, [continent]))
    if committed:
        cand_nats = [committed] if nat_info.get(committed, {}).get("continent") in cont_set else []
    else:
        cand_nats = [n for n in my_nats
                     if n and nat_info.get(n, {}).get("continent") in cont_set]

    # OVR 조건 통과 여부 (choose_national_team에서 미선발 처리에 사용)
    sel_cand = [n for n in cand_nats
                if _check_selection(p, nat_info.get(n, {}).get("grade", "F"))]

    # ─── my_sel 결정 ───
    # [핵심 설계] 순서: 국적 선택 → 예선 진출 실패 → 예선 탈락 → 조별리그 탈락 → 토너먼트
    # 컷오프 걸린 국적도 선택창 먼저 띄우고, choose_national_team에서 결과 처리.
    if my_cut_nat:
        # 컷오프 걸린 국적이 있어도 선택창 제시 (committed면 자동처리)
        if committed:
            # 이미 고정된 나라가 컷오프 → 바로 예선 진출 실패 기록
            failed_nat = committed
            _save_trophy(year, failed_nat, f"{year + 1} 월드컵 {continent} 예선", "예선 진출 실패")
            try:
                conn_fc = get_conn()
                conn_fc.execute("""INSERT INTO intl_history(year, competition, team_name, result,
                                                            goals, assists, caps, rating)
                                   VALUES(?,?,?,?,?,?,?,?)""",
                                (year, f"{year + 1} 월드컵 {continent} 예선",
                                 failed_nat, "예선 진출 실패", 0, 0, 0, 0.0))
                conn_fc.commit(); conn_fc.close()
            except Exception:
                pass
            from game_engine import add_log
            add_log(f"❌ {failed_nat} 월드컵 {continent} 예선 진출 실패 (랭킹 하위권)", "event")
            my_sel = 2; my_nat = ""; cand_nats_final = []
        else:
            # 미고정: 컷오프 국적 포함해서 선택창 제시
            # choose_national_team에서 컷오프 여부 확인 후 "예선 진출 실패" 처리
            my_sel = 3; my_nat = ""; cand_nats_final = cand_nats
    elif committed:
        my_sel = 1 if committed in sel_cand else 2
        my_nat = committed if my_sel == 1 else ""
        cand_nats_final = []
    else:
        # 미고정: 해당 연맹 소속 국적 있으면 선택창 (OVR 무관)
        if cand_nats:
            my_sel = 3; my_nat = ""; cand_nats_final = cand_nats
        else:
            my_sel = 2; my_nat = ""; cand_nats_final = []

    # ─── 조별리그 편성 ───
    n_groups   = qual_cfg["n_groups"]
    group_size = qual_cfg["group_size"]

    pool.sort(key=lambda r: r["ovr"], reverse=True)
    glabels = _qual_group_labels(n_groups)
    groups  = {g: [] for g in glabels}
    for pot in range(group_size):
        segment = pool[pot * n_groups:(pot + 1) * n_groups]
        random.shuffle(segment)
        for gi, e in enumerate(segment):
            if gi < n_groups:
                groups[glabels[gi]].append(e)

    # ─── DB 저장 ───
    name = f"{year + 1} 월드컵 {continent} 예선"
    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO intl_tournaments(year, kind, name, status, my_selected, my_nat, cand_nats, continent)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (year, qual_kind, name, "qual_group", my_sel, my_nat,
               ",".join(cand_nats_final), continent))
    tid = c.lastrowid

    # entries 저장
    my_grp = None
    for g, members in groups.items():
        for e in members:
            is_my = 0
            if my_nat and e["name"] == my_nat:
                is_my = 1; my_grp = g
            elif my_sel == 3 and any(e["name"] == n for n in cand_nats_final):
                is_my = 1; my_grp = g
            c.execute("""INSERT INTO intl_entries
                         (tournament_id, country, flag, grade, ovr, grp, is_my, continent)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (tid, e["name"], e.get("flag",""), e.get("grade","F"),
                       round(e["ovr"], 1), g, is_my, e.get("continent","")))

    # 홈앤어웨이 6라운드 일정 생성 (19~24주차)
    # 라운드로빈 알고리즘: 1팀 고정 + 나머지 회전 → 정방향 3R + 역방향 3R
    base_week = 18

    def _round_robin_schedule(names):
        """n팀 라운드로빈. 각 라운드는 (홈, 원정) 쌍의 리스트."""
        t = list(names)
        if len(t) % 2 == 1:
            t.append(None)  # 홀수 더미
        nt = len(t)
        rounds = []
        for _ in range(nt - 1):
            pairs = []
            for i in range(nt // 2):
                h, a = t[i], t[nt - 1 - i]
                if h is not None and a is not None:
                    pairs.append((h, a))
            rounds.append(pairs)
            t = [t[0]] + [t[-1]] + t[1:-1]
        return rounds

    for g, members in groups.items():
        names = [e["name"] for e in members]
        fwd_rounds = _round_robin_schedule(names)
        # 역방향: 홈↔원정 반전
        rev_rounds = [[(a, h) for h, a in rnd] for rnd in fwd_rounds]
        all_rounds = fwd_rounds + rev_rounds  # 6라운드

        cand_set = set(cand_nats_final)
        for rnd_idx, rnd_pairs in enumerate(all_rounds):
            wk = base_week + rnd_idx   # 18, 19, 20, 21, 22, 23주
            wk = min(wk, 23)
            for home_nat, away_nat in rnd_pairs:
                is_my_match = (
                    (my_nat and (home_nat == my_nat or away_nat == my_nat)) or
                    (my_sel == 3 and (home_nat in cand_set or away_nat in cand_set))
                )
                c.execute("""INSERT INTO intl_matches
                             (tournament_id, week, stage, grp, home, away, is_my, my_played)
                             VALUES(?,?,?,?,?,?,?,?)""",
                          (tid, wk, "qual_group", g, home_nat, away_nat,
                           1 if is_my_match else 0, 0))

    conn.commit(); conn.close()

    # 플레이오프 주차(25주차) 레코드는 _finalize_qual이 조별 완료 시 생성
    # (qual_cfg["po_teams"] > 0 인 체제에서만)

    # 로그
    add_log(f"🌐 {name} 예선 조 추첨 완료 ({n_groups}개 조)", "event")
    if my_sel == 3:
        add_log(f"   발탁 후보: {', '.join(cand_nats_final)}", "event")
    elif my_sel == 1:
        add_log(f"   {my_nat} 대표로 출전 확정", "event")

    # 미선발 로그
    if my_sel == 2 and cand_nats:
        for n2 in cand_nats:
            conn2 = get_conn()
            conn2.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?",
                          ("예선 미선발", tid))
            conn2.commit(); conn2.close()


def _qual_group_labels(n):
    """예선 조가 8개를 넘을 수 있으므로 A~Z, 그 이상은 A1,A2... 로 확장."""
    base = [chr(ord("A") + i) for i in range(26)]
    if n <= 26:
        return base[:n]
    labels = list(base)
    i = 0
    while len(labels) < n:
        labels.append(f"{base[i % 26]}{i // 26 + 1}")
        i += 1
    return labels[:n]




def process_intl_week(week):
    """이번 주차의 남은 국제대회 경기(AI) 시뮬 + 라운드 진행.
    [복수대륙컵] 그 해 열린 모든 대회를 각각 진행한다."""
    from game_engine import get_state
    st = get_state()
    if not st:
        return
    for t in get_tournaments(st["current_year"]):
        if t.get("status") == "done":
            continue
        _process_one_tournament_week(t, week)


def _process_one_tournament_week(t, week):
    """대회 1개의 이번 주차 경기 시뮬 + 라운드 진행."""
    conn = get_conn()
    pending = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches
           WHERE tournament_id=? AND week<=? AND home_score=-1""",
        (t["id"], week)).fetchall()]
    conn.close()

    for m in pending:
        _sim_ai_match(t, m)

    # ── 예선 진행 ──
    if t["kind"] == "wc_qual":
        # [버그수정] t는 루프 시작 시의 스냅샷이므로 status를 DB에서 재조회한다.
        # _finalize_qual 내부에서 status='qual_po' or 'done'으로 갱신되기 때문에
        # 스냅샷 값으로 판정하면 PO 체제에서 다음 주차 호출이 누락될 수 있다.
        conn2 = get_conn()
        cur_row = conn2.execute(
            "SELECT status FROM intl_tournaments WHERE id=?", (t["id"],)).fetchone()
        conn2.close()
        status = cur_row["status"] if cur_row else t.get("status", "qual_group")

        # 조별리그: 완료 주차(23주)에 마감
        if status == "qual_group":
            qual_last_week = 23
            if week >= qual_last_week:
                _finalize_qual(t)   # 직행 확정 or qual_po 생성
        # 플레이오프: 25주차 완료 시 마감
        elif status == "qual_po":
            if week >= 25:
                _finalize_qual_po(t)  # 플레이오프 승자 → qual_results
        return

    # 라운드 진행
    last_group_week = INTL_GROUP_WEEKS[1]
    if t["kind"] == "world":
        # 48개국(2002~)은 32강부터, 32개국은 16강부터
        big = (t["year"] >= WC_EXPAND_YEAR)
        if big:
            plan = {last_group_week: ("R32", 21), 21: ("R16", 22),
                    22: ("QF", 23), 23: ("SF", 24), 24: ("F", 25), 25: (None, None)}
        else:
            plan = {last_group_week: ("R16", 21), 21: ("QF", 22),
                    22: ("SF", 23), 23: ("F", 24), 24: (None, None)}
    else:
        # 대륙컵 24개국: 조별(6조) → 16강 → 8강 → 4강 → 결승
        plan = {last_group_week: ("R16", 21), 21: ("QF", 22),
                22: ("SF", 23), 23: ("F", 24), 24: (None, None)}

    if week not in plan:
        return
    next_stage, next_week = plan[week]

    if week == last_group_week:
        _finalize_groups(t, next_stage, next_week)
    elif next_stage is None:
        # 결승 주차: F와 TP(3/4위전) 둘 다 완료됐을 때 종료 처리
        conn_chk = get_conn()
        tp_remain = conn_chk.execute(
            "SELECT COUNT(*) AS n FROM intl_matches WHERE tournament_id=? AND stage='TP' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn_chk.close()
        if tp_remain == 0:
            _finish_tournament(t, week)
        # tp_remain > 0이면 TP 완료 시 다시 호출됨
    elif week in plan and plan[week][0] == "TP":
        # TP 완료 확인 후 결승도 끝났으면 종료
        conn_chk = get_conn()
        f_remain = conn_chk.execute(
            "SELECT COUNT(*) AS n FROM intl_matches WHERE tournament_id=? AND stage='F' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn_chk.close()
        if f_remain == 0:
            _finish_tournament(t, week)
    else:
        _advance_knockout(t, week, next_stage, next_week)


# ─────────────────────────────────────────────
# 경기 시뮬 (AI)
# ─────────────────────────────────────────────

def _entry(tid, country):
    key = (tid, country)
    cached = _entry_cache.get(key)
    if cached is not None:
        return cached
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=? AND country=?",
        (tid, country)).fetchone()
    conn.close()
    val = dict(row) if row else {"ovr": 50, "flag": "", "grade": "F"}
    _entry_cache[key] = val
    return val


def _match_outcome(h_ovr, a_ovr, knockout):
    """중립 구장 가정. 'home'/'draw'/'away' 반환 (KO는 무승부 → 승부차기)."""
    diff = h_ovr - a_ovr
    hw = max(0.08, min(0.85, 0.46 + diff * 0.014))
    dw = 0.22
    aw = max(0.05, 1.0 - hw - dw)
    roll = random.random()
    if roll < hw:
        return "home"
    elif roll < hw + dw:
        return "draw"
    return "away"


def _gen_intl_score(outcome):
    from game_engine import _gen_score
    return _gen_score(outcome)


def _resolve_pso(h_ovr, a_ovr):
    """승부차기: 전력이 살짝 유리하게."""
    p_home = 0.5 + max(-0.1, min(0.1, (h_ovr - a_ovr) * 0.006))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


def _sim_ai_match(t, m, my_played=False):
    """AI끼리(또는 내가 결장한 내 경기) 시뮬."""
    from game_engine import add_log, get_player
    he = _entry(t["id"], m["home"])
    ae = _entry(t["id"], m["away"])
    knockout = m["stage"] not in ("group", "qual_group")  # [버그수정] 예선 조별도 무승부 허용

    outcome = _match_outcome(he["ovr"], ae["ovr"], knockout)
    pso_winner, pso_score = "", ""
    if knockout and outcome == "draw":
        win_home, pso_score = _resolve_pso(he["ovr"], ae["ovr"])
        pso_winner = m["home"] if win_home else m["away"]
    hs, as_ = _gen_intl_score(outcome)

    conn = get_conn()
    conn.execute("""UPDATE intl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=? WHERE id=?""",
                 (hs, as_, pso_winner, pso_score, m["id"]))
    conn.commit()
    conn.close()

    # 내 국가 경기(결장 포함)는 로그 출력. AI끼리 경기는 get_player() 불필요.
    if m["is_my"]:
        p = get_player()
        nat = _my_nat(t, p)
        if nat in (m["home"], m["away"]):
            stage_ko = STAGE_KO.get(m["stage"], "")
            pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
            add_log(f"🌍 {t['name']} {stage_ko}  "
                    f"{he['flag']}{m['home']} {hs}-{as_} {ae['flag']}{m['away']}{pso_txt}", "match")
            if t["my_selected"] == 1 and not my_played:
                add_log("   🚑 부상으로 대표팀 경기 결장", "match")


def _winner_of(m):
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home"] if m["home_score"] > m["away_score"] else m["away"]


# ─────────────────────────────────────────────
# 내 경기 시뮬
# ─────────────────────────────────────────────

def simulate_my_match(week, p):
    """내가 출전하는 국가대표 경기."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail)
    info = get_my_match(week)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM intl_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM intl_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    nat = _my_nat(t, p)
    # 예선(wc_qual)은 cap-tie 대상이 아니다 → 국적 고정 안 함.
    _is_qual = t.get("kind") == "wc_qual"
    # [복수국적] A매치 첫 출전 → 그 나라로 영구 고정(cap-tie). (본선만)
    # 이후 대회부터는 이 나라로만 차출된다.
    if (not _is_qual) and nat and not (p.get("intl_committed", "") or ""):
        from game_engine import update_player as _upd
        _upd(intl_committed=nat)
        # [국적 연혁] A매치 첫 출전으로 자동 고정된 경우도 commit 기록
        try:
            from game_engine import add_nat_history, get_state
            _st = get_state() or {}
            _fl = ""
            for _nk, _fk in (("nationality","flag"),("nationality2","flag2"),("nationality3","flag3")):
                if (p.get(_nk,"") or "") == nat:
                    _fl = p.get(_fk,"") or ""; break
            add_nat_history("commit", nat, _fl,
                            _st.get("current_year"), _st.get("current_week"))
        except Exception:
            pass
    he = _entry(t["id"], m["home"])
    ae = _entry(t["id"], m["away"])
    is_home = info["is_home"]
    knockout = m["stage"] not in ("group", "qual_group")  # [버그수정] 예선 조별도 무승부 허용

    # 내 출전 보너스 (격차 기반 에이스 영향력)
    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = _my_ovr - _team_ovr
    bonus = min(max(0.0, _gap) * 0.32 + _my_ovr * 0.05, 14.0)
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = _match_outcome(h_ovr, a_ovr, knockout)
    pso_winner, pso_score = "", ""
    if knockout and outcome == "draw":
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home"] if win_home else m["away"]
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    # [수정] 국제대회 개인 경기력은 '상대 국가대표 평균 OVR'을 dom 기준으로 삼는다.
    #   내가 홈이면 상대는 ae(원정), 원정이면 he(홈). 강팀 상대면 개인도 고전,
    #   약체국 상대면 골·평점 폭발 — 클럽 리그 기준이 아니라 상대 국가 강함 반영.
    _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
    goals, assists, saves, rating, events, detail = _player_perf(
        p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr)
    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    conn = get_conn()
    conn.execute("""UPDATE intl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?,
                    my_played=1, my_nat=?, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score,
                  nat, _get_field_pos(p),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  my_conceded, m["id"]))
    conn.commit()
    conn.close()

    # 국가대표 개인 기록 (클럽 시즌 통계와 분리)
    #  [cap-tie] 본선 무대를 밟으면 그 나라로 영구 고정. 단 예선은 고정 안 함.
    if _is_qual:
        # 예선: caps/goals/assists 누적만, capped/committed 미설정 (국적 변경 자유)
        update_player(
            intl_caps=p.get("intl_caps", 0) + 1,
            intl_goals=p.get("intl_goals", 0) + goals,
            intl_assists=p.get("intl_assists", 0) + assists,
        )
    else:
        update_player(
            intl_caps=p.get("intl_caps", 0) + 1,
            intl_goals=p.get("intl_goals", 0) + goals,
            intl_assists=p.get("intl_assists", 0) + assists,
            intl_capped=1,
            intl_committed=(p.get("intl_committed", "") or nat),
            qual_pledged_nat="",   # 본선 출전으로 영구고정됐으니 pledge 정리
        )
    # [세부 지표] 통산(total_*)에도 누적 → 커리어 통합 통계에 A매치 반영
    p2 = get_player()
    update_player(
        total_shots=p2.get("total_shots", 0) + detail["shots"],
        total_shots_on=p2.get("total_shots_on", 0) + detail["shots_on"],
        total_key_passes=p2.get("total_key_passes", 0) + detail["key_passes"],
        total_dribbles=p2.get("total_dribbles", 0) + detail["dribbles"],
        total_blocks=p2.get("total_blocks", 0) + detail["blocks"],
    )

    # 인기/스트레스/행복
    p2 = get_player()
    _update_pop(p2, goals, assists, rating)
    p2 = get_player()
    ns = min(100, p2["stress"] + 8)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    # ── 로그 ──
    stage_ko = STAGE_KO.get(m["stage"], "")
    grp_txt = f" {m['grp']}조" if m["stage"] == "group" else ""
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == nat else '패'})"
        rs = "무"
    comp_name = f"{t['name']} {stage_ko}{grp_txt}".strip()
    home_disp = f"{he['flag']}{m['home']}"
    away_disp = f"{ae['flag']}{m['away']}"
    detail_id = _save_match_detail(
        p, week, comp_name, is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, True, False, detail)
    marker = f" [match:{detail_id}]" if detail_id else ""

    add_log("─" * 44, "sep")
    add_log(f"🌍 {comp_name}  {week}주차{marker}", "match")
    add_log(f"   {home_disp} {hs}-{as_} {away_disp}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")
    from game_engine import _log_highlight, _min_sortkey
    _timed = sorted([(int(e[0]), e[1]) if isinstance(e, tuple) else
                     (random.randint(1, 90), str(e)) for e in events],
                    key=lambda x: _min_sortkey(x[0]))
    hi = _log_highlight(goals, assists, _timed)
    if hi:
        add_log(f"   {hi}", "match")


# ─────────────────────────────────────────────
# 조별리그 마감 / 토너먼트 진행
# ─────────────────────────────────────────────

def _qual_group_standings(tid, grp):
    """예선 조 순위 (stage='qual_group' 기준)."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=? AND grp=?",
        (tid, grp)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND grp=?
           AND stage='qual_group' AND home_score>=0""", (tid, grp)).fetchall()]
    conn.close()

    tbl = {e["country"]: {"country": e["country"], "flag": e["flag"], "ovr": e["ovr"],
                          "grade": e["grade"], "p": 0, "w": 0, "d": 0, "l": 0,
                          "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    for m in matches:
        h, a = tbl.get(m["home"]), tbl.get(m["away"])
        if not h or not a:
            continue
        hs, as_ = m["home_score"], m["away_score"]
        h["p"] += 1; a["p"] += 1
        h["gf"] += hs; h["ga"] += as_
        a["gf"] += as_; a["ga"] += hs
        if hs > as_:
            h["pts"] += 3; h["w"] += 1; a["l"] += 1
        elif hs < as_:
            a["pts"] += 3; a["w"] += 1; h["l"] += 1
        else:
            h["pts"] += 1; a["pts"] += 1; h["d"] += 1; a["d"] += 1
    rows = list(tbl.values())
    rows.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]), reverse=True)
    return rows


def _finalize_qual(t):
    """예선 조별 종료 → 플레이오프 생성 or 통과국 확정.

    [32팀 체제] po_teams>0 이면 25주차 플레이오프 경기를 생성하고 status='qual_po'로 전환.
                플레이오프 완료 후 _finalize_qual_po()가 최종 진출국 확정.
    [48팀 체제] 조 1위(+와일드카드) 직행 → 즉시 qual_results 저장.
    """
    from game_engine import add_log, get_player
    from constants import WC_QUAL_32, WC_QUAL_48

    tid = t["id"]
    conn = get_conn()
    grps = [r["grp"] for r in conn.execute(
        "SELECT DISTINCT grp FROM intl_entries WHERE tournament_id=? ORDER BY grp", (tid,)).fetchall()]
    conn.close()

    continent = _conf_key((t.get("continent") or "").strip() or "유럽")
    big = (t["year"] + 1) >= WC_EXPAND_YEAR
    qual_cfg = (WC_QUAL_48 if big else WC_QUAL_32).get(continent, {})

    # 조별 1위/2위 수집
    winners = []
    runners = []
    for g in grps:
        standings = _qual_group_standings(tid, g)
        if not standings:
            continue
        if len(standings) >= 1: winners.append(standings[0])
        if len(standings) >= 2: runners.append(standings[1])

    direct_n  = qual_cfg.get("direct", len(winners))
    po_teams  = qual_cfg.get("po_teams", 0)
    wildcard  = qual_cfg.get("wildcard", 0)
    quota     = qual_cfg.get("quota", direct_n)

    # ─── 직행 확정 ───
    direct_teams = winners[:direct_n]

    # 와일드카드 (아메리카 48팀 체제: 조 2위 중 상위 N팀)
    if wildcard > 0:
        runners.sort(key=lambda r: (r["pts"], r["gf"]-r["ga"], r["gf"], r["ovr"]), reverse=True)
        direct_teams = direct_teams + runners[:wildcard]

    # ─── 플레이오프 필요한 체제 ───
    # 32팀: 유럽(직행12+PO2→1), 아시아(PO10→5), 아프리카(PO12→6)
    # 48팀: 유럽(직행12+PO8→4), 아시아(PO10→10), 아프리카(PO12→9)
    if po_teams > 0:
        # [버그수정] 직행팀을 먼저 저장할 때 set_done=False를 전달해
        # _save_qual_results가 status='done'으로 설정하는 것을 막는다.
        # status='done'이 되면 process_intl_week가 다음 주차에 이 대회를 스킵해
        # _finalize_qual_po가 호출되지 않고, PO 종료 로그도 출력되지 않는 버그 원인.
        if direct_teams:
            _save_qual_results(t, continent, direct_teams, set_done=False)

        # PO 대상: direct_n==0이면 조 1위 전원, 아니면 조 2위 중 상위 po_teams팀
        if direct_n == 0:
            po_pool = winners[:po_teams]
        else:
            # 유럽: 조 2위 중 성적 상위 po_teams팀
            runners.sort(key=lambda r: (r["pts"], r["gf"]-r["ga"], r["gf"], r["ovr"]), reverse=True)
            po_pool = runners[:po_teams]

        random.shuffle(po_pool)
        conn = get_conn(); c = conn.cursor()
        p = get_player()
        my_nat = _my_nat(t, p) if p else ""
        for i in range(0, len(po_pool)-1, 2):
            home = po_pool[i]; away = po_pool[i+1]
            is_my = 1 if my_nat and (home["country"] == my_nat or away["country"] == my_nat) else 0
            c.execute("""INSERT INTO intl_matches
                         (tournament_id, week, stage, home, away, is_my, my_played)
                         VALUES(?,?,?,?,?,?,?)""",
                      (tid, 25, "qual_po", home["country"], away["country"], is_my, 0))
        c.execute("UPDATE intl_tournaments SET status='qual_po' WHERE id=?", (tid,))
        conn.commit(); conn.close()
        add_log(f"🏆 {t['name']} 플레이오프 시작! (25주차)", "event")
        return  # 플레이오프 완료 후 _finalize_qual_po가 처리

    # ─── 즉시 확정 (플레이오프 없는 체제) ───
    _save_qual_results(t, continent, direct_teams)


def _finalize_qual_po(t):
    """25주차 플레이오프 완료 → 승자를 qual_results에 저장."""
    from game_engine import add_log, get_player
    tid = t["id"]
    continent = _conf_key((t.get("continent") or "").strip() or "유럽")

    conn = get_conn()
    po_matches = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_matches WHERE tournament_id=? AND stage='qual_po'", (tid,)).fetchall()]
    conn.close()

    p = get_player()
    my_nat = _my_nat(t, p) if p else ""
    winners = []
    po_logs = []  # 경기 결과 로그 버퍼 (헤더와 함께 출력)
    for m in po_matches:
        home_row = _entry(tid, m["home"])
        away_row = _entry(tid, m["away"])
        home = {"country": m["home"], "flag": home_row.get("flag",""),
                "grade": home_row.get("grade","F"), "ovr": home_row.get("ovr",50)}
        away = {"country": m["away"], "flag": away_row.get("flag",""),
                "grade": away_row.get("grade","F"), "ovr": away_row.get("ovr",50)}
        if m.get("home_score", -1) >= 0:
            hs, as_ = m["home_score"], m["away_score"]
            if hs > as_:
                winner = home
            elif as_ > hs:
                winner = away
            else:
                winner = home if random.random() > 0.5 else away
        else:
            winner = _sim_single_match_ai(home, away)
        winners.append(winner)
        loser = away if winner["country"] == home["country"] else home
        my_marker = ""
        if my_nat:
            if winner["country"] == my_nat:
                my_marker = " ← 우리팀 통과 ✅"
            elif loser["country"] == my_nat:
                my_marker = " ← 우리팀 탈락 ❌"
        po_logs.append(f"   {home['flag']}{m['home']} vs {away['flag']}{m['away']}"
                       f" → {winner['flag']}{winner['country']} 통과{my_marker}")

    # PO 경기 결과 로그 출력 (경기 시뮬 완료 후)
    if po_logs:
        add_log("─" * 44, "sep")
        add_log(f"🏆 {t['name']} 플레이오프 결과", "event")
        for _line in po_logs:
            add_log(_line, "event")

    # [버그수정] PO 승자만 넘기면 _save_qual_results의 DELETE가
    # 기존 직행팀을 지워버린다. 직행팀을 미리 읽어 PO 승자와 합쳐서
    # 전체를 한 번에 저장한다.
    target_year = t["year"] + 1
    target_kind = "world" if t["kind"] == "wc_qual" else "continent"
    conn_pre = get_conn()
    existing_rows = [dict(r) for r in conn_pre.execute(
        "SELECT country, flag, grade, ovr FROM qual_results"
        " WHERE target_year=? AND kind=? AND continent=?",
        (target_year, target_kind, continent)).fetchall()]
    conn_pre.close()
    existing_names = {r["country"] for r in existing_rows}
    # 직행팀 dict를 _save_qual_results 형식에 맞게 변환
    direct_teams = [{"country": r["country"], "flag": r["flag"],
                     "grade": r["grade"], "ovr": r["ovr"]} for r in existing_rows]
    # po_winners 값만큼만 PO 승자 반영 (유럽: 4팀 PO → 승자 2팀이지만 po_winners=1)
    from constants import WC_QUAL_32, WC_QUAL_48, WC_EXPAND_YEAR
    big = (t["year"] + 1) >= WC_EXPAND_YEAR
    qual_cfg = (WC_QUAL_48 if big else WC_QUAL_32).get(continent, {})
    po_winners_n = qual_cfg.get("po_winners", len(winners))
    po_new = [w for w in winners if w["country"] not in existing_names][:po_winners_n]
    all_qualified = direct_teams + po_new
    _save_qual_results(t, continent, all_qualified)


def _save_qual_results(t, continent, qualified_list, set_done=True):
    """통과국 목록을 qual_results에 저장 + 내 성적 기록.

    set_done=False: status를 'done'으로 갱신하지 않는다.
    PO 체제(유럽/아시아/아프리카 32팀)에서 직행팀 먼저 저장 시 사용.
    PO 완료(_finalize_qual_po) 시에는 True(기본값)로 호출해 'done' 처리.
    """
    from game_engine import add_log, get_player

    tid = t["id"]
    target_year = t["year"] + 1
    target_kind = "world" if t["kind"] == "wc_qual" else "continent"
    qualified_names = {q["country"] for q in qualified_list}

    conn = get_conn(); c = conn.cursor()
    if set_done:
        # 최종 저장(PO 완료 or 직행 전원 확정): 기존 행 전부 지우고 새로 씀
        c.execute("DELETE FROM qual_results WHERE target_year=? AND kind=? AND continent=?",
                  (target_year, target_kind, continent))
    # set_done=True(최종): DELETE 후 qualified_list 전체를 새로 저장.
    # set_done=False(직행팀 중간 저장): _finalize_qual_po가 나중에
    #   직행팀+PO승자를 합쳐 set_done=True로 한 번에 덮어씀.
    for q in qualified_list:
        c.execute("""INSERT INTO qual_results
                     (target_year, kind, continent, country, flag, grade, ovr)
                     VALUES(?,?,?,?,?,?,?)""",
                  (target_year, target_kind, continent,
                   q["country"], q.get("flag",""), q.get("grade","F"), q.get("ovr",50)))
    if set_done:
        c.execute("UPDATE intl_tournaments SET status='done' WHERE id=?", (tid,))
    conn.commit(); conn.close()

    # 내 나라 성적 기록
    p = get_player()
    my_nat = _my_nat(t, p) if p else ""
    if my_nat and t["my_selected"] == 1:
        passed = my_nat in qualified_names
        result = "예선 통과" if passed else "예선 탈락"
        conn = get_conn()
        conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?", (result, tid))
        agg = conn.execute(
            """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                      COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
               FROM intl_matches WHERE tournament_id=? AND my_played=1""", (tid,)).fetchone()
        conn.execute("""INSERT INTO intl_history(year, competition, team_name, result,
                                                 goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (t["year"], t["name"], my_nat, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
        conn.commit(); conn.close()
        _save_trophy(t["year"], my_nat, t["name"], result)
        icon = "✅" if passed else "❌"
        add_log("─" * 44, "sep")
        add_log(f"{icon} {t['name']} 결과: {my_nat} {result}", "event")
        if passed:
            add_log(f"   → {target_year}년 월드컵 본선 진출!", "event")
        else:
            # [버그수정] 예선 탈락 시 pledge 초기화
            # qual_pledged_nat가 살아있으면 다음 해 본선 _create_one_tournament에서
            # pledged 경로로 진입해 오동작할 수 있음
            try:
                from game_engine import update_player as _upd_pledged
                _upd_pledged(qual_pledged_nat="")
            except Exception:
                pass
    else:
        conn = get_conn()
        conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?", ("예선 미참가", tid))
        conn.commit(); conn.close()

    # 각 대륙 예선 완료 시 무조건 진출국 목록 출력
    if set_done:
        add_log("─" * 44, "sep")
        add_log(f"🌐 {t['name']} 예선 완료 — {len(qualified_list)}개국 본선 진출", "event")
        for _q in qualified_list:
            add_log(f"   ✈️  {_q.get('flag','')} {_q['country']}", "event")


def get_group_standings(tid, grp):
    """조 순위 계산: 승점 → 득실 → 다득점 → 팀 전력."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=? AND grp=?",
        (tid, grp)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND grp=?
           AND stage='group' AND home_score>=0""", (tid, grp)).fetchall()]
    conn.close()

    tbl = {e["country"]: {"country": e["country"], "flag": e["flag"], "ovr": e["ovr"],
                          "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    for m in matches:
        h, a = tbl.get(m["home"]), tbl.get(m["away"])
        if not h or not a:
            continue
        hs, as_ = m["home_score"], m["away_score"]
        h["p"] += 1; a["p"] += 1
        h["gf"] += hs; h["ga"] += as_
        a["gf"] += as_; a["ga"] += hs
        if hs > as_:
            h["w"] += 1; h["pts"] += 3; a["l"] += 1
        elif hs < as_:
            a["w"] += 1; a["pts"] += 3; h["l"] += 1
        else:
            h["d"] += 1; a["d"] += 1; h["pts"] += 1; a["pts"] += 1
    rows = list(tbl.values())
    rows.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]), reverse=True)
    return rows


def _finalize_groups(t, next_stage, next_week):
    """조별리그 종료 → 진출국 확정, 다음 라운드 대진 생성.
    - 월드컵 32개국(8조): 각 조 1·2위 = 16팀 → R16
    - 월드컵 48개국(12조): 각 조 1·2위(24팀) + 3위 중 상위 8팀 = 32팀 → R32
    - 대륙컵(6조): 각 조 1·2위(12팀) + 3위 중 상위 4팀 = 16팀 → R16
    """
    from game_engine import add_log, get_player
    from constants import WC_BEST_THIRDS_BIG
    tid = t["id"]
    is_wc = (t["kind"] == "world")
    is_big = is_wc and t["year"] >= WC_EXPAND_YEAR   # 48개국 시대

    if is_wc:
        n_groups = WC_GROUPS_BIG if is_big else WC_GROUPS
    else:
        n_groups = CONT_GROUPS
    labels = _GROUP_LABELS[:n_groups]

    firsts, seconds = {}, {}
    thirds = []      # (조라벨, row) — best-3rd 후보
    eliminated = []
    for g in labels:
        rows = get_group_standings(tid, g)
        if len(rows) < 2:
            continue
        firsts[g]  = rows[0]["country"]
        seconds[g] = rows[1]["country"]
        if is_wc and not is_big:
            # 32개국 월드컵: 3위 이하 전부 탈락
            eliminated.extend(r["country"] for r in rows[2:])
        else:
            # 48개국 월드컵 / 대륙컵: 3위는 best-3rd 경쟁, 4위는 탈락
            if len(rows) >= 3:
                thirds.append((g, rows[2]))
            eliminated.extend(r["country"] for r in rows[3:])

    # 3위 팀 진출 처리 (48개국 월드컵 & 대륙컵 공통)
    best_thirds = []
    n_best = WC_BEST_THIRDS_BIG if is_big else (CONT_BEST_THIRDS if not is_wc else 0)
    if n_best > 0 and thirds:
        thirds.sort(key=lambda gr: (gr[1]["pts"], gr[1]["gf"] - gr[1]["ga"],
                                    gr[1]["gf"], gr[1]["ovr"]), reverse=True)
        adv = thirds[:n_best]
        best_thirds = [(g, r["country"]) for g, r in adv]
        eliminated.extend(r["country"] for _, r in thirds[n_best:])

    conn = get_conn()
    c = conn.cursor()
    for nat_e in eliminated:
        c.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                  (tid, nat_e))

    # ── 다음 라운드 대진 생성 ──
    if is_wc and not is_big:
        # 32개국: 1A-2B, 1C-2D, … / 1B-2A, 1D-2C, … → 16강
        # [버그수정] firsts/seconds에 없는 조 라벨 접근 시 KeyError 방지
        pairs = []
        for i in range(0, n_groups - 1, 2):
            if i + 1 >= len(labels): break
            g1, g2 = labels[i], labels[i + 1]
            if g1 not in firsts or g2 not in seconds: continue
            pairs.append((firsts[g1], seconds[g2]))
        for i in range(0, n_groups - 1, 2):
            if i + 1 >= len(labels): break
            g1, g2 = labels[i], labels[i + 1]
            if g2 not in firsts or g1 not in seconds: continue
            pairs.append((firsts[g2], seconds[g1]))

    elif is_big:
        # 48개국: 조 1·2위(24팀) + 3위 8팀 = 32팀 → 32강
        # 시드 배치: 1위(12) > 2위(12) > 3위(8) 순서로 페어링
        firsts_list  = [firsts[g]  for g in labels if g in firsts]
        seconds_list = [seconds[g] for g in labels if g in seconds]
        thirds_list  = [nat for _, nat in best_thirds]
        # 32팀 시드 배치: 상위 시드가 하위 시드와 만나도록
        # 1위 12팀 → 2위/3위 중 상대 배정, 나머지 2위끼리
        strong = list(firsts_list)                      # 12
        weak   = thirds_list + seconds_list             # 8+12=20 → 상위 12만 사용
        pairs = []
        for s in strong:
            opp = weak.pop(0) if weak else None
            if opp is not None:
                pairs.append((s, opp))
        # 남은 2위끼리 (12-8=4팀 남음 → 2경기)
        while len(weak) >= 2:
            pairs.append((weak.pop(0), weak.pop(0)))

    else:
        # 대륙컵 24개국: 1위6 + 2위6 + 3위4 = 16팀 → 16강
        firsts_list  = [firsts[g]  for g in labels if g in firsts]
        seconds_list = [seconds[g] for g in labels if g in seconds]
        thirds_list  = [nat for _, nat in best_thirds]
        strong = list(firsts_list)
        weak   = thirds_list + seconds_list
        pairs = []
        for s in strong:
            opp = weak.pop(0) if weak else None
            if opp is not None:
                pairs.append((s, opp))
        while len(weak) >= 2:
            pairs.append((weak.pop(0), weak.pop(0)))

    p = get_player()
    nat = _my_nat(t, p)
    for slot, (home, away) in enumerate(pairs):
        is_my = 1 if nat in (home, away) else 0
        c.execute("""INSERT INTO intl_matches
                     (tournament_id, stage, grp, week, home, away,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,?,-1,-1,?,?)""",
                  (tid, next_stage, "", next_week, home, away, is_my, slot))
    c.execute("UPDATE intl_tournaments SET status='ko' WHERE id=?", (tid,))
    conn.commit()
    conn.close()

    add_log(f"🌍 {t['name']} 조별리그 종료 → {STAGE_KO[next_stage]} 진출국 확정",
            "event")
    # 내 국가가 조별 탈락했으면 결과 확정
    if nat and nat in eliminated:
        _record_my_exit(t, "조별리그 탈락")


def _advance_knockout(t, week, next_stage, next_week):
    """현재 KO 라운드 종료 → 패자 탈락, 다음 라운드 생성."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND week=?
           AND stage!='group' ORDER BY slot""", (tid, week)).fetchall()]
    conn.close()
    if not cur:
        return

    p = get_player()
    nat = _my_nat(t, p)
    cur_stage_ko = STAGE_KO.get(cur[0]["stage"], "")

    winners = []
    is_sf = cur and cur[0]["stage"] == "SF"

    conn = get_conn()
    c = conn.cursor()
    for m in cur:
        w = _winner_of(m)
        loser = m["away"] if w == m["home"] else m["home"]
        winners.append((m["slot"], w))
        # SF 패자는 3/4위전을 뛰므로 alive=0으로 즉시 탈락 처리하지 않는다.
        # (탈락은 _finish_tournament에서 4위 확정 후 처리)
        if not is_sf:
            c.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                      (tid, loser))
        if nat and loser == nat and not is_sf:
            conn.commit()
            conn.close()
            _record_my_exit(t, cur_stage_ko)
            conn = get_conn()
            c = conn.cursor()

    # 4강(SF) 종료 시: 패자 2팀으로 3/4위전(TP) 생성 (결승과 같은 주차)
    losers = []
    if is_sf:
        for m in cur:
            w = _winner_of(m)
            loser = m["away"] if w == m["home"] else m["home"]
            losers.append(loser)

    winners.sort()
    for slot in range(0, len(winners), 2):
        if slot + 1 >= len(winners):
            break
        home, away = winners[slot][1], winners[slot + 1][1]
        is_my = 1 if nat in (home, away) else 0
        c.execute("""INSERT INTO intl_matches
                     (tournament_id, stage, grp, week, home, away,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,?,-1,-1,?,?)""",
                  (tid, next_stage, "", next_week, home, away, is_my, slot // 2))

    # 3/4위전: SF 패자 2팀, 결승과 같은 주차
    if len(losers) == 2:
        tp_home, tp_away = losers[0], losers[1]
        is_my_tp = 1 if nat in (tp_home, tp_away) else 0
        c.execute("""INSERT INTO intl_matches
                     (tournament_id, stage, grp, week, home, away,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,?,-1,-1,?,?)""",
                  (tid, "TP", "", next_week, tp_home, tp_away, is_my_tp, 999))
        add_log(f"🥉 {t['name']} 3/4위전 대진: {tp_home} vs {tp_away} ({next_week}주차)", "event")

    conn.commit()
    conn.close()
    add_log(f"🌍 {t['name']} {cur_stage_ko} 종료 → {STAGE_KO[next_stage]} 대진 확정", "event")


def _finish_tournament(t, final_week):
    """결승 + 3/4위전 종료 → 우승국·3위 확정, 내 결과 기록."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    fm = conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND stage='F'
           AND home_score>=0""", (tid,)).fetchone()
    tp = conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND stage='TP'
           AND home_score>=0""", (tid,)).fetchone()
    conn.close()
    if not fm:
        return
    fm = dict(fm)
    winner  = _winner_of(fm)
    runner  = fm["away"] if winner == fm["home"] else fm["home"]

    # 3/4위전 결과
    third = fourth = None
    if tp:
        tp = dict(tp)
        third  = _winner_of(tp)
        fourth = tp["away"] if third == tp["home"] else tp["home"]

    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET status='done', winner=? WHERE id=?",
                 (winner, tid))
    conn.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                 (tid, runner))
    if fourth:
        conn.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                     (tid, fourth))
    conn.commit()
    conn.close()

    we = _entry(tid, winner)
    add_log(f"🏆 {t['name']} 우승: {we['flag']}{winner}!", "event")
    if third:
        te = _entry(tid, third)
        add_log(f"🥉 {t['name']} 3위: {te['flag']}{third}", "event")

    p = get_player()
    nat = _my_nat(t, p)
    if nat == winner:
        _record_my_exit(t, "우승")
    elif nat == runner:
        _record_my_exit(t, "준우승")
    elif nat == third:
        _record_my_exit(t, "3위")
    elif nat == fourth:
        _record_my_exit(t, "4위")


# ─────────────────────────────────────────────
# 내 결과 확정 + 보상
# ─────────────────────────────────────────────

_REWARD = {  # 결과: (명성, 인기, 행복도) ─ 월드컵 기준
    "우승":         (25, 15, 20),
    "준우승":       (15,  8, 10),
    "3위":          (12,  6,  8),
    "4위":          ( 9,  4,  5),
    "4강":          (10,  5,  6),   # 3/4위전 없는 대회(대륙컵 등) 호환
    "8강":          ( 6,  3,  3),
    "16강":         ( 3,  2,  1),
    "32강":         ( 2,  1,  0),   # 48팀 체제 전용 (32팀 체제엔 없는 라운드)
    "조별리그 탈락": ( 1,  0, -2),
}


def _record_my_exit(t, result):
    """내 국가의 최종 성적 확정: 트로피/이력 기록 + 보상 (선발됐을 때만)."""
    from game_engine import add_log, get_player, update_player
    p = get_player()
    if not p:
        return
    nat = _my_nat(t, p)

    # 미선발(또는 출전 보류)이면 이 대회 성적은 내 경력이 아니다.
    #  - my_result는 'XX 미선발'로만 표시(대회 화면 일관성용)
    #  - trophy_log / intl_history(개인기록) / 보상은 일절 기록하지 않는다.
    #    (선발 안 됐는데 대표팀이 우승했다고 내 우승 트로피로 박히던 버그 방지)
    if t["my_selected"] != 1:
        conn = get_conn()
        conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?",
                     (f"{result} (미선발)", t["id"]))
        conn.commit()
        conn.close()
        return

    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?", (result, t["id"]))
    conn.commit()
    conn.close()

    _save_trophy(t["year"], nat, t["name"], result)

    fame_g, pop_g, hap_g = _REWARD.get(result, (0, 0, 0))
    if t["kind"] != "world":  # 대륙컵은 60% 스케일
        fame_g = round(fame_g * 0.6)
        pop_g = round(pop_g * 0.6)
        hap_g = round(hap_g * 0.6)

    update_player(
        fame=min(100, p.get("fame", 0) + fame_g),
        popularity=min(100, p.get("popularity", 0) + pop_g),
        happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
    )

    # 이번 대회 개인 기록 집계 → intl_history (대회 단위)
    conn = get_conn()
    agg = conn.execute(
        """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM intl_matches
           WHERE tournament_id=? AND my_played=1""", (t["id"],)).fetchone()
    conn.execute("""INSERT INTO intl_history(year, competition, team_name, result,
                                             goals, assists, caps, rating)
                    VALUES(?,?,?,?,?,?,?,?)""",
                 (t["year"], t["name"], nat, result,
                  agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()

    icon = "🏆" if result == "우승" else "🌍"
    add_log(f"{icon} {t['year']}년 {t['name']} 최종 성적: {result}  "
            f"(명성 +{fame_g}, 인기 +{pop_g})", "event")


def _save_trophy(year, nat, competition, result):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=?",
        (year, competition)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,0,?)""", (year, nat, result, competition))
        conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# 국제전 이력 조회 (커리어창 / 은퇴창 공용)
# ─────────────────────────────────────────────

def get_my_intl_matches(only_qual=False):
    """내가 실제 출전한 A매치 목록 (시간순). 결장 경기는 제외.

    only_qual=False: 본선 경기만 (world/continent)
    only_qual=True : 예선 경기만 (wc_qual)

    반환 dict: year, week, position, nat, nat_flag, comp, stage,
               opp, opp_flag, goals, assists, saves, conceded,
               rating, score, result(승/무/패, PSO 표기 포함)
    """
    if only_qual:
        kind_filter = "t.kind = 'wc_qual'"
    else:
        kind_filter = "t.kind IN ('world','continent')"
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"""SELECT m.*, t.year AS t_year, t.name AS comp
           FROM intl_matches m
           JOIN intl_tournaments t ON m.tournament_id = t.id
           WHERE m.my_played = 1 AND {kind_filter}
           ORDER BY t.year, m.week""").fetchall()]
    flags = {(r["tournament_id"], r["country"]): r["flag"]
             for r in conn.execute(
                 "SELECT tournament_id, country, flag FROM intl_entries").fetchall()}
    conn.close()

    out = []
    for m in rows:
        nat = m["my_nat"]
        is_home = (m["home"] == nat)
        opp  = m["away"] if is_home else m["home"]
        my_s = m["home_score"] if is_home else m["away_score"]
        op_s = m["away_score"] if is_home else m["home_score"]

        if m["pso_winner"]:
            result = "승(PSO)" if m["pso_winner"] == nat else "패(PSO)"
        elif my_s > op_s:
            result = "승"
        elif my_s < op_s:
            result = "패"
        else:
            result = "무"

        stage = STAGE_KO.get(m["stage"], m["stage"])
        if m["stage"] in ("group", "qual_group") and m["grp"]:
            stage = f"조별 {m['grp']}조"

        out.append({
            "year": m["t_year"], "week": m["week"],
            "position": m["my_position"], "nat": nat,
            "nat_flag": flags.get((m["tournament_id"], nat), ""),
            "comp": m["comp"], "stage": stage,
            "opp": opp, "opp_flag": flags.get((m["tournament_id"], opp), ""),
            "goals": m["my_goals"], "assists": m["my_assists"],
            "saves": m["my_saves"], "conceded": op_s,
            "rating": m["my_rating"],
            "shots": m.get("my_shots", 0), "shots_on": m.get("my_shots_on", 0),
            "key_passes": m.get("my_key_passes", 0), "dribbles": m.get("my_dribbles", 0),
            "blocks": m.get("my_blocks", 0), "pass_acc": m.get("my_pass_acc", 0),
            "score": f"{my_s}-{op_s}", "result": result,
        })
    return out


def get_my_qual_matches():
    """내가 출전한 예선 경기만 반환 (커리어/은퇴 '국제전(예선)' 탭용)."""
    return get_my_intl_matches(only_qual=True)