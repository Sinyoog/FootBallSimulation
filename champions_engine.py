"""
champions_engine.py ─ 클럽 대륙 챔피언스리그 엔진

대륙별로 독립된 클럽 토너먼트 4개를 운영한다 (국가대표 대회와 별개).
  유럽 챔피언스리그 / 아시아 챔피언스리그 /
  아프리카 챔피언스리그 / 북남미 챔피언스리그

각 대륙 '안의' 클럽끼리만 붙는다 (아시안컵·아프리카컵의 클럽판).
출전팀: 그 대륙 소속 국가들의 직전 시즌 1·2위 팀에서 32팀 선발.
  - 각국 1부 리그 1위는 무조건 출전
  - 32팀이 안 차면 리그 등급(grade) 높은 나라의 2위로 채움

진행 시점: 시즌 41~46주 (리그는 35주 종료, 36~40주 비시즌/이적).
  41주: 추첨 + 출전팀 확정 + 32강 대진
  42주: 32강
  43주: 16강
  44주: 8강
  45주: 4강
  46주: 결승
  (라운드가 매주 연속 진행 → 쉬는 주 없이 이어서 치름)

내 팀이 출전하면 내 팀 경기만 내가 출전(개인기록 반영),
나머지 대진은 AI끼리 자동 시뮬한다.
"""

import random

from database import get_conn
from constants import GRADE_TEAM_OVR  # 참고용(미사용 가능)

# ── 대회 일정 (주차) ───────────────────────────────
CL_START_WEEK = 41           # 추첨/조 편성
CL_GROUP_WEEKS = (42, 44)    # 조별리그 3경기 (42,43,44)
CL_ROUND_WEEKS = {
    "R16": 45,
    "QF":  46,
    "SF":  47,
    "F":   48,
}
CL_END_WEEK = 48

CL_TEAMS = 32                # 본선 32팀 (8조 × 4팀)
CL_GROUPS = 8

# ── entry 캐시 ─────────────────────────────────────
# cl_entries(ovr/flag/team_name/grade)는 대회 진행 중 바뀌지 않으므로
# (tournament_id, team_id) 별로 1회만 조회하고 재사용한다.
# 새 토너먼트 생성 시 _clear_entry_cache() 로 비운다.
_entry_cache = {}

def _clear_entry_cache():
    _entry_cache.clear()

STAGE_KO = {"group": "조별리그", "R16": "16강", "QF": "8강", "SF": "4강", "F": "결승"}
# 토너먼트 라운드 진행 순서 (조별리그 다음부터)
_STAGE_ORDER = ["R16", "QF", "SF", "F"]
# 조별리그 라운드 매칭 (4팀, 인덱스 기반) — 3경기
_GROUP_ROUNDS = [
    [(0, 3), (1, 2)],
    [(0, 2), (3, 1)],
    [(0, 1), (2, 3)],
]
_GROUP_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H"]

# 대륙 그룹핑: 게임 내 continent 값 → 챔스 대륙 키
#   오세아니아 → 아시아 편입, 북미/남미 → 북남미 통합
CONTINENT_MAP = {
    "유럽": "유럽",
    "아시아": "아시아",
    "오세아니아": "아시아",
    "아프리카": "아프리카",
    "북미": "북남미",
    "남미": "북남미",
}
# 대회 이름
CL_CUP_NAME = {
    "유럽": "유럽 챔피언스리그",
    "아시아": "아시아 챔피언스리그",
    "아프리카": "아프리카 챔피언스리그",
    "북남미": "북남미 챔피언스리그",
}

# 결과별 보상 (명성, 인기, 행복도) ─ 클럽 대회는 국가대표보다 약간 낮게
_REWARD = {
    "우승":       (18, 12, 16),
    "준우승":     (11,  7,  8),
    "4강":        ( 7,  4,  5),
    "8강":        ( 4,  3,  3),
    "16강":       ( 2,  2,  1),
    "32강":       ( 1,  0, -1),
    "32강 탈락":  ( 1,  0, -1),
    "16강 탈락":  ( 2,  2,  1),
    "8강 탈락":   ( 4,  3,  3),
    "4강 탈락":   ( 7,  4,  5),
}


# ─────────────────────────────────────────────
# 조회 헬퍼
# ─────────────────────────────────────────────

def get_cl_tournament(year, continent):
    """해당 연도+대륙의 챔스 row (없으면 None)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM cl_tournaments WHERE year=? AND continent=? ORDER BY id DESC LIMIT 1",
        (year, continent)).fetchone()
    conn.close()
    return dict(row) if row else None


def _my_continent(p):
    """내 소속팀이 속한 대륙(챔스 키). 팀 없으면 None."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    conn = get_conn()
    row = conn.execute(
        """SELECT cn.continent FROM teams t
           JOIN countries cn ON t.country_id = cn.id
           WHERE t.id=?""", (tid,)).fetchone()
    conn.close()
    if not row:
        return None
    return CONTINENT_MAP.get(row["continent"])


def _my_cl_tournament(p, year):
    """내 대륙의 이번 연도 챔스 (있으면). 내 팀이 출전했는지와 무관."""
    cont = _my_continent(p)
    if not cont:
        return None
    return get_cl_tournament(year, cont)


def get_my_cl_match(week):
    """이번 주차에 내가 뛸 챔스 경기가 있으면 dict, 없으면 None."""
    from game_engine import get_player, get_state
    p = get_player()
    st = get_state()
    if not p or not st:
        return None
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    t = _my_cl_tournament(p, st["current_year"])
    if not t or t["status"] == "done":
        return None
    # 출전 자격 체크: 대회 생성(41주) 당시 등록된 내 팀과 현재 팀이 같아야 한다.
    #   시즌 중 다른 팀으로 이적한 경우(등록 마감 후 합류)는 그 시즌 챔스에 못 뛴다.
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != tid:
        return None

    conn = get_conn()
    m = conn.execute(
        """SELECT * FROM cl_matches
           WHERE tournament_id=? AND week=? AND home_score=-1
             AND (home_team_id=? OR away_team_id=?)""",
        (t["id"], week, tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    oe = conn.execute(
        "SELECT team_name, flag FROM cl_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], opp_id)).fetchone()
    conn.close()
    return {
        "cl": True,                       # 클럽 챔스 경기 표시 플래그
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],         # 대회명 (UI 호환 위해 league_name 키 사용)
        "stage": m["stage"],
        "stage_ko": STAGE_KO.get(m["stage"], m["stage"]),
        "grp": m["grp"] if "grp" in m.keys() else "",
        "opp": oe["team_name"] if oe else "?",
        "opp_flag": oe["flag"] if oe else "",
        "is_home": is_home,
        "week": week,
    }


def has_my_cl_match_between(week_from, week_to):
    """주차 범위 내 내 챔스 경기 존재 여부 (센터패널 표시용)."""
    for w in range(week_from, week_to + 1):
        if get_my_cl_match(w):
            return True
    return False


# ─────────────────────────────────────────────
# 대회 생성 (41주차 진입 시)
# ─────────────────────────────────────────────

def start_champions_league(year, season):
    """41주차 진입 시 호출. 4개 대륙 챔스를 모두 생성.

    season 인자는 직전 시즌 순위 집계에 사용 (리그 경기는 35주에 끝남).
    """
    from game_engine import add_log, get_player
    p = get_player()
    if not p:
        return

    # 이미 만들어졌으면(어느 대륙이든) 중복 생성 방지
    if get_cl_tournament(year, "유럽"):
        return

    _clear_entry_cache()   # 새 시즌 대회 → 이전 캐시 무효화

    my_cont = _my_continent(p)
    my_tid = p.get("current_team_id", 0)

    for cont in ("유럽", "아시아", "아프리카", "북남미"):
        entries = _select_entries(cont, season)
        if len(entries) < 4:
            continue  # 출전팀 부족하면 그 대륙 대회 생략
        _build_tournament(year, cont, entries, my_tid if cont == my_cont else 0)

    # ── 내 대회 안내 로그 (출전 자격 = 내 리그 1위일 때만) ──
    if my_cont and my_tid:
        t = get_cl_tournament(year, my_cont)
        if t:
            # 출전 자격 판정: 내 팀이 '내 1부 리그의 1위'인가?
            qualified = _is_my_team_league_winner(p, my_tid)
            conn = get_conn()
            mine = conn.execute(
                "SELECT 1 FROM cl_entries WHERE tournament_id=? AND team_id=?",
                (t["id"], my_tid)).fetchone()
            conn.close()

            if mine:
                # 본선 진출 (자격도 당연히 있음)
                conn = get_conn()
                conn.execute("UPDATE cl_tournaments SET my_qualified=1 WHERE id=?",
                             (t["id"],))
                conn.commit(); conn.close()
                add_log("─" * 44, "sep")
                add_log(f"🏆 {year}년 {t['name']} 개막!  내 팀 본선 진출!",
                        "event", year, CL_START_WEEK)
                add_log(f"   조별리그 {CL_GROUP_WEEKS[0]}주차부터 시작",
                        "event", year, CL_START_WEEK)
            elif qualified:
                # 리그 1위(자격)인데 본선엔 못 들어감(32 컷 등) → '본선 진출 실패'
                conn = get_conn()
                conn.execute("UPDATE cl_tournaments SET my_qualified=1 WHERE id=?",
                             (t["id"],))
                trow = conn.execute("SELECT name FROM teams WHERE id=?",
                                    (my_tid,)).fetchone()
                conn.commit(); conn.close()
                team_name = trow["name"] if trow else ""
                _save_trophy(year, team_name, t["name"], "본선 진출 실패")
                add_log("─" * 44, "sep")
                add_log(f"🏆 {year}년 {t['name']}  리그 우승했지만 본선 진출 실패",
                        "event", year, CL_START_WEEK)
            # else: 리그 1위가 아님 → 챔스와 무관, 아무것도 안 뜸 (침묵)


def _is_my_team_league_winner(p, my_tid):
    """내 팀이 그 시즌 '내 1부 리그의 1위'인지 — 챔스 출전 자격 판정.
    챔스는 1부(tier=1) 리그 1위만 출전 자격이 있다. 2부 이하 1위는 자격 없음
    (2부 우승은 '승격'이지 챔스 진출이 아니다)."""
    if not my_tid:
        return False
    from game_engine import get_league_standings
    conn = get_conn()
    row = conn.execute(
        "SELECT league_id FROM teams WHERE id=?", (my_tid,)).fetchone()
    if not row:
        conn.close()
        return False
    lid = row["league_id"]
    tier_row = conn.execute("SELECT tier FROM leagues WHERE id=?", (lid,)).fetchone()
    conn.close()
    # 1부가 아니면 챔스 자격 없음 (2부 1위는 승격 대상일 뿐)
    if not tier_row or tier_row["tier"] != 1:
        return False
    standings = get_league_standings(lid)
    if not standings:
        return False
    return standings[0]["id"] == my_tid


def _select_entries(continent, season):
    """대륙 소속 각 1부 리그의 '1위 팀'만 선발 (최대 32).

    규칙:
      - 각국 1부 리그 1위만 출전 자격 (2위 이하는 제외)
      - 1위팀이 32개국을 넘으면 리그 등급 높은 나라 우선으로 32에서 컷
    반환: [{team_id, team_name, flag, ovr, grade, country}, ...] (최대 32)
    """
    from game_engine import get_league_standings

    game_conts = [gc for gc, ck in CONTINENT_MAP.items() if ck == continent]

    conn = get_conn()
    placeholders = ",".join("?" * len(game_conts))
    leagues = conn.execute(
        f"""SELECT l.id AS lid, cn.name AS country, cn.flag AS flag, cn.grade AS grade
            FROM leagues l JOIN countries cn ON l.country_id = cn.id
            WHERE l.tier = 1 AND cn.continent IN ({placeholders})""",
        game_conts).fetchall()
    leagues = [dict(r) for r in leagues]
    conn.close()

    # 등급 높은 나라 우선 (32 초과 시 컷 기준)
    grade_rank = {"S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
    leagues.sort(key=lambda r: -grade_rank.get(r["grade"], 0))

    picked = []
    for lg in leagues:
        rows = get_league_standings(lg["lid"])
        if not rows:
            continue
        # 1위 팀만
        picked.append(_entry_from(lg, rows[0]))
        if len(picked) >= CL_TEAMS:
            break
    return picked[:CL_TEAMS]


def _entry_from(lg, standing_row):
    """리그 + 순위표 한 행 → entry dict + 팀 전력(OVR) 계산."""
    from game_engine import get_conn as _gc
    tid = standing_row["id"]
    conn = _gc()
    row = conn.execute("SELECT AVG(ovr) AS v FROM ai_players WHERE team_id=?", (tid,)).fetchone()
    conn.close()
    ovr = (row["v"] if row and row["v"] else 50) + random.uniform(-2, 2)
    return {
        "team_id": tid,
        "team_name": standing_row["name"],
        "flag": lg["flag"],
        "country": lg["country"],
        "grade": lg["grade"],
        "ovr": ovr,
    }


def _build_tournament(year, continent, entries, my_tid):
    """대회 row + 출전팀 + 조별리그(8조×4팀) 일정 생성."""
    name = CL_CUP_NAME.get(continent, "챔피언스리그")

    # 32팀으로 정규화 (부족하면 가능한 만큼, 단 조 편성 위해 4의 배수로 컷)
    entries.sort(key=lambda e: e["ovr"], reverse=True)
    n = (len(entries) // 4) * 4
    n = min(n, CL_TEAMS)
    entries = entries[:n]
    n_groups = n // 4

    # 내 팀이 출전팀(=리그 1위)인지. = 출전 자격(my_qualified)과 동일.
    my_in = 1 if (my_tid and any(e["team_id"] == my_tid for e in entries)) else 0
    my_reg_tid = my_tid if my_in else 0

    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO cl_tournaments(year, continent, name, status,
                    my_in, my_team_id, my_qualified)
                 VALUES(?,?,?,?,?,?,?)""",
              (year, continent, name, "group", my_in, my_reg_tid, my_in))
    tid = c.lastrowid

    # ── 포트 추첨: 전력순 4포트 → 조마다 포트별 1팀 ──
    groups = {g: [] for g in _GROUP_LABELS[:n_groups]}
    pot_size = n // 4
    for pot in range(4):
        pool = entries[pot * pot_size:(pot + 1) * pot_size]
        random.shuffle(pool)
        for gi, e in enumerate(pool):
            g = _GROUP_LABELS[gi]
            groups[g].append(e)
            c.execute("""INSERT INTO cl_entries
                         (tournament_id, team_id, team_name, flag, country,
                          grade, ovr, grp, alive)
                         VALUES(?,?,?,?,?,?,?,?,1)""",
                      (tid, e["team_id"], e["team_name"], e["flag"],
                       e["country"], e["grade"], e["ovr"], g))

    # ── 조별리그 일정 (42,43,44주 = 3경기) ──
    w0 = CL_GROUP_WEEKS[0]
    for rd, pairs in enumerate(_GROUP_ROUNDS):
        wk = w0 + rd
        for g, members in groups.items():
            for hi, ai in pairs:
                if hi >= len(members) or ai >= len(members):
                    continue
                home, away = members[hi], members[ai]
                is_my = 1 if my_tid in (home["team_id"], away["team_id"]) else 0
                c.execute("""INSERT INTO cl_matches
                             (tournament_id, stage, grp, week,
                              home_team_id, away_team_id,
                              home_score, away_score, is_my, slot)
                             VALUES(?,?,?,?,?,?,-1,-1,?,0)""",
                          (tid, "group", g, wk,
                           home["team_id"], away["team_id"], is_my))
    c.execute("UPDATE cl_tournaments SET status='group', first_stage='group' WHERE id=?",
              (tid,))
    conn.commit()
    conn.close()


def _pow2_floor(n):
    """n 이하의 가장 큰 2의 거듭제곱 (최대 32). n<2면 0."""
    if n < 2:
        return 0
    p = 1
    while p * 2 <= n and p < CL_TEAMS:
        p *= 2
    return p


def _first_stage_for(n):
    """출전팀 수 n에 맞는 첫 라운드 스테이지."""
    if n >= 32:
        return "R32"
    if n >= 16:
        return "R16"
    if n >= 8:
        return "QF"
    if n >= 4:
        return "SF"
    return "F"


# ─────────────────────────────────────────────
# 주차 처리 (advance_4weeks에서 매주 호출)
# ─────────────────────────────────────────────

def process_cl_week(week):
    """이번 주차의 남은 챔스 경기(AI) 시뮬 + 라운드 진행 (모든 대륙)."""
    from game_engine import get_state
    st = get_state()
    if not st:
        return
    year = st["current_year"]

    for cont in ("유럽", "아시아", "아프리카", "북남미"):
        t = get_cl_tournament(year, cont)
        if not t or t["status"] == "done":
            continue
        _process_one(t, week)


def _process_one(t, week):
    """단일 대회: 이번 주차 이하 미진행 경기 AI 시뮬 → 라운드/조별 마감."""
    conn = get_conn()
    pending = [dict(r) for r in conn.execute(
        """SELECT * FROM cl_matches
           WHERE tournament_id=? AND week<=? AND home_score=-1""",
        (t["id"], week)).fetchall()]
    conn.close()

    for m in pending:
        _sim_ai_match(t, m)

    # 조별리그 마지막 주차(44) → 16강 진출 확정
    if week == CL_GROUP_WEEKS[1]:
        conn = get_conn()
        remain = conn.execute(
            "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage='group' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn.close()
        if remain == 0:
            _finalize_groups(t)
        return

    # 토너먼트 라운드 주차 확인
    cur_stage = None
    for stg, wk in CL_ROUND_WEEKS.items():
        if wk == week:
            cur_stage = stg
            break
    if cur_stage is None:
        return

    # 이 라운드 경기가 전부 끝났는지 확인
    conn = get_conn()
    remain = conn.execute(
        "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage=? AND home_score=-1",
        (t["id"], cur_stage)).fetchone()["n"]
    conn.close()
    if remain > 0:
        return

    if cur_stage == "F":
        _finish_tournament(t)
    else:
        nxt = _STAGE_ORDER[_STAGE_ORDER.index(cur_stage) + 1]
        _advance_round(t, cur_stage, nxt)


# ─────────────────────────────────────────────
# 경기 시뮬 (AI)
# ─────────────────────────────────────────────

def _entry(tid, team_id):
    key = (tid, team_id)
    cached = _entry_cache.get(key)
    if cached is not None:
        return cached
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM cl_entries WHERE tournament_id=? AND team_id=?",
        (tid, team_id)).fetchone()
    conn.close()
    val = dict(row) if row else {"ovr": 50, "flag": "", "team_name": "?", "grade": "F"}
    _entry_cache[key] = val
    return val


def _match_outcome(h_ovr, a_ovr):
    """중립 구장 가정. 'home'/'draw'/'away' (KO 무승부 → 승부차기)."""
    diff = h_ovr - a_ovr
    hw = max(0.08, min(0.85, 0.46 + diff * 0.014))
    dw = 0.22
    roll = random.random()
    if roll < hw:
        return "home"
    elif roll < hw + dw:
        return "draw"
    return "away"


def _resolve_pso(h_ovr, a_ovr):
    p_home = 0.5 + max(-0.1, min(0.1, (h_ovr - a_ovr) * 0.006))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


def _sim_ai_match(t, m, my_played=False):
    """AI끼리(또는 내가 결장한 내 경기) 시뮬."""
    from game_engine import add_log, get_player, _gen_score
    he = _entry(t["id"], m["home_team_id"])
    ae = _entry(t["id"], m["away_team_id"])

    outcome = _match_outcome(he["ovr"], ae["ovr"])
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "group")
    if outcome == "draw" and is_ko:
        win_home, pso_score = _resolve_pso(he["ovr"], ae["ovr"])
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome)

    conn = get_conn()
    conn.execute("""UPDATE cl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=? WHERE id=?""",
                 (hs, as_, pso_winner, pso_score, m["id"]))
    conn.commit()
    conn.close()

    # 내 팀 경기(결장 포함)면 로그. AI끼리 경기는 get_player() 불필요.
    if m["is_my"]:
        p = get_player()
        my_tid = p.get("current_team_id", 0) if p else 0
        if my_tid in (m["home_team_id"], m["away_team_id"]):
            stage_ko = STAGE_KO.get(m["stage"], "")
            pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
            add_log(f"🏆 {t['name']} {stage_ko}  "
                    f"{he['flag']}{he['team_name']} {hs}-{as_} {ae['flag']}{ae['team_name']}{pso_txt}",
                    "match")
            if not my_played:
                add_log("   🚑 부상으로 챔스 경기 결장", "match")


def _winner_of(m):
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home_team_id"] if m["home_score"] > m["away_score"] else m["away_team_id"]


# ─────────────────────────────────────────────
# 내 경기 시뮬
# ─────────────────────────────────────────────

def simulate_my_cl_match(week, p):
    """내가 출전하는 챔스 경기."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score)
    info = get_my_cl_match(week)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM cl_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM cl_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    he = _entry(t["id"], m["home_team_id"])
    ae = _entry(t["id"], m["away_team_id"])
    is_home = info["is_home"]

    # 내 출전 보너스 (클럽 리그 경기와 동일 계수)
    bonus = p.get("ovr", 40) * 0.08
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = _match_outcome(h_ovr, a_ovr)
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "group")
    if outcome == "draw" and is_ko:
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome)

    goals, assists, saves, rating, events, detail = _player_perf(p, outcome, is_home, hs, as_)
    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    conn = get_conn()
    conn.execute("""UPDATE cl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?,
                    my_played=1, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score,
                  p.get("position", ""),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  my_conceded, m["id"]))
    conn.commit()
    conn.close()

    # [세부 지표] 통산(total_*)에도 누적 → 커리어 통합 통계에 챔스 경기 반영
    update_player(
        total_shots=p.get("total_shots", 0) + detail["shots"],
        total_shots_on=p.get("total_shots_on", 0) + detail["shots_on"],
        total_key_passes=p.get("total_key_passes", 0) + detail["key_passes"],
        total_dribbles=p.get("total_dribbles", 0) + detail["dribbles"],
        total_blocks=p.get("total_blocks", 0) + detail["blocks"],
    )

    # 인기/스트레스/행복
    _update_pop(p, goals, assists, rating)
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
    my_tid = p.get("current_team_id", 0)
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == my_tid else '패'})"
        rs = "무"
    add_log("─" * 44, "sep")
    add_log(f"🏆 {t['name']} {stage_ko}  {week}주차", "match")
    add_log(f"   {he['flag']}{he['team_name']} {hs}-{as_} {ae['flag']}{ae['team_name']}  ({rs}){pso_txt}",
            "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating}  골 {goals}  어시 {assists}", "match")
    for ev in events:
        mm = random.randint(1, 90)
        add_log(f"   {mm}'  {ev}", "match")


# ─────────────────────────────────────────────
# 라운드 진행
# ─────────────────────────────────────────────

def get_cl_group_standings(tid, grp):
    """챔스 조 순위: 승점 → 득실 → 다득점 → 전력."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM cl_entries WHERE tournament_id=? AND grp=?",
        (tid, grp)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        """SELECT * FROM cl_matches WHERE tournament_id=? AND grp=?
           AND stage='group' AND home_score>=0""", (tid, grp)).fetchall()]
    conn.close()

    tbl = {e["team_id"]: {"team_id": e["team_id"], "team_name": e["team_name"],
                          "flag": e["flag"], "ovr": e["ovr"],
                          "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    for m in matches:
        h, a = tbl.get(m["home_team_id"]), tbl.get(m["away_team_id"])
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


def _finalize_groups(t):
    """챔스 조별리그 종료 → 각 조 1·2위(16팀) → 16강 대진 생성."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    n_groups = conn.execute(
        "SELECT COUNT(DISTINCT grp) AS n FROM cl_entries WHERE tournament_id=? AND grp!=''",
        (tid,)).fetchone()["n"]
    conn.close()
    labels = _GROUP_LABELS[:n_groups]

    firsts, seconds, eliminated = {}, {}, []
    for g in labels:
        rows = get_cl_group_standings(tid, g)
        if len(rows) >= 1: firsts[g] = rows[0]["team_id"]
        if len(rows) >= 2: seconds[g] = rows[1]["team_id"]
        eliminated.extend(r["team_id"] for r in rows[2:])

    conn = get_conn()
    c = conn.cursor()
    for tid_e in eliminated:
        c.execute("UPDATE cl_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                  (tid, tid_e))

    # R16 대진: 1A-2B, 1C-2D, … / 1B-2A, 1D-2C, … (월드컵 방식)
    pairs = []
    for i in range(0, n_groups, 2):
        g1, g2 = labels[i], labels[i + 1]
        if g1 in firsts and g2 in seconds:
            pairs.append((firsts[g1], seconds[g2]))
    for i in range(0, n_groups, 2):
        g1, g2 = labels[i], labels[i + 1]
        if g2 in firsts and g1 in seconds:
            pairs.append((firsts[g2], seconds[g1]))

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    next_week = CL_ROUND_WEEKS["R16"]
    for slot, (home, away) in enumerate(pairs):
        is_my = 1 if my_tid in (home, away) else 0
        c.execute("""INSERT INTO cl_matches
                     (tournament_id, stage, grp, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,?,-1,-1,?,?)""",
                  (tid, "R16", "", next_week, home, away, is_my, slot))
    c.execute("UPDATE cl_tournaments SET status='ko' WHERE id=?", (tid,))
    conn.commit()
    conn.close()

    add_log(f"🏆 {t['name']} 조별리그 종료 → 16강 진출팀 확정", "event")
    # 내 팀이 조별 탈락?
    if my_tid and my_tid in eliminated:
        _record_my_exit(t, "조별리그 탈락")


def _advance_round(t, cur_stage, next_stage):
    """현재 KO 라운드 종료 → 패자 탈락, 다음 라운드 대진 생성."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        """SELECT * FROM cl_matches WHERE tournament_id=? AND stage=?
           ORDER BY slot""", (tid, cur_stage)).fetchall()]
    conn.close()
    if not cur:
        return

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    cur_stage_ko = STAGE_KO.get(cur_stage, "")
    next_week = CL_ROUND_WEEKS[next_stage]

    winners = []
    conn = get_conn()
    c = conn.cursor()
    # 탈락 결과 라벨: 토너먼트에서 진 라운드 = '도달한 라운드'로 기록한다.
    #   16강에서 졌으면 '16강 진출'까지가 성취 → '16강 탈락'(X)이 아니라 '16강'(O).
    #   (월드컵 intl_engine 과 동일한 관례.)
    exit_label = cur_stage_ko
    for m in cur:
        w = _winner_of(m)
        loser = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
        winners.append((m["slot"], w))
        c.execute("UPDATE cl_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                  (tid, loser))
        if my_tid and loser == my_tid:
            conn.commit(); conn.close()
            _record_my_exit(t, exit_label)
            conn = get_conn(); c = conn.cursor()

    winners.sort()
    for slot in range(0, len(winners), 2):
        if slot + 1 >= len(winners):
            break
        home, away = winners[slot][1], winners[slot + 1][1]
        is_my = 1 if my_tid in (home, away) else 0
        c.execute("""INSERT INTO cl_matches
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,-1,-1,?,?)""",
                  (tid, next_stage, next_week, home, away, is_my, slot // 2))
    conn.commit()
    conn.close()
    # 진행 상황 로그: 내 팀이 참가했고, 아직 탈락 전일 때만
    if t["my_in"]:
        conn = get_conn()
        mr = conn.execute("SELECT my_result FROM cl_tournaments WHERE id=?", (tid,)).fetchone()
        conn.close()
        if not (mr and mr["my_result"]):
            add_log(f"🏆 {t['name']} {cur_stage_ko} 종료 → {STAGE_KO[next_stage]} 대진 확정", "event")


def _finish_tournament(t):
    """결승 종료 → 우승팀 확정, 내 결과 기록."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    fm = conn.execute(
        """SELECT * FROM cl_matches WHERE tournament_id=? AND stage='F'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
    conn.close()
    if not fm:
        return
    fm = dict(fm)
    winner = _winner_of(fm)
    loser = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    conn = get_conn()
    conn.execute("UPDATE cl_tournaments SET status='done', winner_team_id=? WHERE id=?",
                 (winner, tid))
    conn.execute("UPDATE cl_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                 (tid, loser))
    conn.commit()
    conn.close()

    we = _entry(tid, winner)
    add_log(f"🏆 {t['name']} 우승: {we['flag']}{we['team_name']}!", "event")

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    if my_tid == winner:
        _record_my_exit(t, "우승")
    elif my_tid == loser:
        _record_my_exit(t, "준우승")


# ─────────────────────────────────────────────
# 내 결과 확정 + 보상
# ─────────────────────────────────────────────

def _record_my_exit(t, result):
    """내 팀의 챔스 최종 성적 확정: 트로피 + 보상."""
    from game_engine import add_log, get_player, update_player
    p = get_player()
    if not p:
        return
    my_tid = p.get("current_team_id", 0)

    conn = get_conn()
    conn.execute("UPDATE cl_tournaments SET my_result=? WHERE id=?", (result, t["id"]))
    # 내 팀명
    te = conn.execute(
        "SELECT team_name FROM cl_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], my_tid)).fetchone()
    conn.commit()
    conn.close()
    team_name = te["team_name"] if te else ""

    _save_trophy(t["year"], team_name, t["name"], result)

    # 이번 대회 개인 기록 집계 → cl_history (월드컵 intl_history와 동일)
    conn = get_conn()
    agg = conn.execute(
        """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM cl_matches
           WHERE tournament_id=? AND my_played=1""", (t["id"],)).fetchone()
    # 같은 연도·대회 중복 방지
    exists = conn.execute(
        "SELECT id FROM cl_history WHERE year=? AND competition=?",
        (t["year"], t["name"])).fetchone()
    if not exists:
        conn.execute("""INSERT INTO cl_history(year, competition, team_name, result,
                                               goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (t["year"], t["name"], team_name, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()

    fame_g, pop_g, hap_g = _REWARD.get(result, (0, 0, 0))
    update_player(
        fame=min(100, p.get("fame", 0) + fame_g),
        popularity=min(100, p.get("popularity", 0) + pop_g),
        happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
    )

    icon = "🏆" if result == "우승" else "🏅"
    add_log(f"{icon} {t['year']}년 {t['name']} 최종 성적: {result}  "
            f"(명성 +{fame_g}, 인기 +{pop_g})", "event")


def _save_trophy(year, team_name, competition, result):
    """trophy_log에 챔스 결과 기록 (tier=-1로 클럽 국제대회 구분)."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=?",
        (year, competition)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,-1,?)""", (year, team_name, result, competition))
        conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# 챔스 이력 조회 (커리어창 / 은퇴창 공용)
# ─────────────────────────────────────────────

def get_my_cl_all_groups(year):
    """[UI용] 내 대륙 챔스의 '모든 조' 순위표 목록을 반환.
    반환: {"groups": [{"grp": "A", "standings": [...]}...], "my_team_id": tid} 또는 None"""
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return None
    my_tid = p["current_team_id"]
    t = _my_cl_tournament(p, year)
    if not t:
        return None
    conn = get_conn()
    grps = [r["grp"] for r in conn.execute(
        "SELECT DISTINCT grp FROM cl_entries WHERE tournament_id=? AND grp!='' ORDER BY grp",
        (t["id"],)).fetchall()]
    conn.close()
    if not grps:
        return None
    groups = [{"grp": g, "standings": get_cl_group_standings(t["id"], g)} for g in grps]
    return {"groups": groups, "my_team_id": my_tid}


def get_my_cl_group_info(year):
    """[UI용] 내 팀이 속한 조의 라벨과 순위표를 반환. 없으면 None."""
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return None
    my_tid = p["current_team_id"]
    t = _my_cl_tournament(p, year)
    if not t:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT grp FROM cl_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], my_tid)).fetchone()
    conn.close()
    if not row or not row["grp"]:
        return None
    grp = row["grp"]
    rows = get_cl_group_standings(t["id"], grp)
    return {"grp": grp, "standings": rows, "my_team_id": my_tid}


def get_my_champions_matches(year):
    """[일정 탭용] 내 팀이 출전한 그 해 챔스의 전체 대진 목록.

    schedule_window의 챔피언스리그 탭이 기대하는 형식으로 반환:
      home_id, away_id, home_name, away_name, home_league, away_league,
      home_score, away_score, pso_winner, pso_score, stage(한글), week
    내 팀이 그 대회에 없으면 빈 리스트.
    """
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return []
    t = _my_cl_tournament(p, year)
    if not t or not t.get("my_in"):
        return []
    # 출전 자격: 등록 당시 팀과 현재 팀이 같을 때만 '내 대회'로 본다.
    #   시즌 중 이적해 들어온 팀의 챔스는 일정에 띄우지 않는다.
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != p.get("current_team_id", 0):
        return []

    conn = get_conn()
    entries = {r["team_id"]: dict(r) for r in conn.execute(
        "SELECT team_id, team_name, flag, country FROM cl_entries WHERE tournament_id=?",
        (t["id"],)).fetchall()}
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM cl_matches WHERE tournament_id=? ORDER BY week, slot",
        (t["id"],)).fetchall()]
    conn.close()

    def _name(tid):
        e = entries.get(tid, {})
        return f"{e.get('flag','')}{e.get('team_name','?')}"

    def _league(tid):
        return entries.get(tid, {}).get("country", "")

    out = []
    for m in rows:
        pso_name = ""
        if m["pso_winner"]:
            pso_name = _name(m["pso_winner"])
        out.append({
            "home_id": m["home_team_id"], "away_id": m["away_team_id"],
            "home_name": _name(m["home_team_id"]), "away_name": _name(m["away_team_id"]),
            "home_league": _league(m["home_team_id"]), "away_league": _league(m["away_team_id"]),
            "home_score": m["home_score"], "away_score": m["away_score"],
            "pso_winner": pso_name, "pso_score": m["pso_score"],
            "stage": STAGE_KO.get(m["stage"], m["stage"]), "week": m["week"],
            "stage_raw": m["stage"], "grp": m["grp"] if "grp" in m.keys() else "",
        })
    return out


def get_my_cl_matches():
    """내가 실제 출전한 챔스 경기 목록 (시간순). 결장 경기는 제외."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT m.*, t.year AS t_year, t.name AS comp
           FROM cl_matches m
           JOIN cl_tournaments t ON m.tournament_id = t.id
           WHERE m.my_played = 1
           ORDER BY t.year, m.week""").fetchall()]
    names = {(r["tournament_id"], r["team_id"]): (r["team_name"], r["flag"])
             for r in conn.execute(
                 "SELECT tournament_id, team_id, team_name, flag FROM cl_entries").fetchall()}
    conn.close()

    out = []
    from game_engine import get_player
    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    for m in rows:
        # 내 팀 식별: 그 경기 시점 소속을 알 수 없으므로 양쪽 중
        # my_played 행 기준으로 현재 팀이 끼어있으면 그쪽을, 아니면 home 기준.
        is_home = (m["home_team_id"] == my_tid)
        opp_id = m["away_team_id"] if is_home else m["home_team_id"]
        my_s = m["home_score"] if is_home else m["away_score"]
        op_s = m["away_score"] if is_home else m["home_score"]

        if m["pso_winner"]:
            won = (m["pso_winner"] == (m["home_team_id"] if is_home else m["away_team_id"]))
            result = "승(PSO)" if won else "패(PSO)"
        elif my_s > op_s:
            result = "승"
        elif my_s < op_s:
            result = "패"
        else:
            result = "무"

        my_name, my_flag = names.get((m["tournament_id"], m["home_team_id"] if is_home else m["away_team_id"]), ("", ""))
        opp_name, opp_flag = names.get((m["tournament_id"], opp_id), ("?", ""))

        out.append({
            "year": m["t_year"], "week": m["week"],
            "position": m["my_position"], "team": my_name, "team_flag": my_flag,
            "comp": m["comp"], "stage": STAGE_KO.get(m["stage"], m["stage"]),
            "opp": opp_name, "opp_flag": opp_flag,
            "goals": m["my_goals"], "assists": m["my_assists"],
            "saves": m["my_saves"], "conceded": op_s,
            "rating": m["my_rating"],
            "shots": m.get("my_shots", 0), "shots_on": m.get("my_shots_on", 0),
            "key_passes": m.get("my_key_passes", 0), "dribbles": m.get("my_dribbles", 0),
            "blocks": m.get("my_blocks", 0), "pass_acc": m.get("my_pass_acc", 0),
            "score": f"{my_s}-{op_s}", "result": result,
        })
    return out