# -*- coding: utf-8 -*-
"""
클럽 월드컵 대회 진행 엔진.

[2026-07 신설] club_world_cup.py가 "32팀이 누구인지"를 정하면, 이 파일이
그 32팀으로 실제 대회(8조×4팀 조별리그 → 16강~결승/3·4위전)를 진행한다.

캘린더: 국제대회 전용구간(43~52주) 중 "빈 해"(예: 2003,2007,2011...)에만
열린다 — intl_engine.start_intl_tournament()가 is_wc/is_cont/is_wc_qual가
전부 False인 해를 감지하면 이 모듈의 start_club_world_cup()을 대신 호출한다.

주차 배분 (10주 안에 넉넉히 들어감):
    43주: 조 추첨
    44~46주: 조별리그 (팀당 3경기, 1주 1경기)
    47주: 16강
    48주: 8강
    49주: 4강
    50주: 결승 + 3/4위전 (같은 주)
    51~52주: 여유(버퍼)

매치 시뮬레이션은 champions_engine._match_outcome/_resolve_pso(순수함수,
OVR 차이 기반)와 game_engine._gen_score(스코어 생성)를 그대로 재사용한다 —
챔스와 완전히 동일한 검증된 공식이라 새로 만들 이유가 없다.

[구현 범위 안내] 이 파일은 대회 진행(추첨~결승) 로직까지다. "내가 직접
뛰는 경기"의 인터랙티브 뷰어(match_sim_viewer.py) 연동은 아직 없고, 지금은
내 경기도 AI전과 동일하게 OVR 기반으로 자동 시뮬된다 — 실제 조작 가능한
경기로 만들려면 UI 쪽(match_flow.py/match_sim_viewer.py) 작업이 별도로
필요하다(별도 요청 시 진행).
"""

import random
from database import get_conn
from constants import generate_round_robin
from champions_engine import _match_outcome, _resolve_pso
from club_world_cup import get_club_world_cup_field, CWC_QUOTA

CWC_DRAW_WEEK = 43
CWC_GROUP_WEEKS = (44, 46)                 # 3주(팀당 3경기)
CWC_KO_WEEK = {"R16": 47, "QF": 48, "SF": 49, "F": 50, "TP": 50}
_STAGE_ORDER = ["R16", "QF", "SF", "F"]
_GROUP_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H"]

_REWARD = {   # 챔스 _REWARD와 동일 체계(명성,인기,행복) — 챔스보다 한 단계 낮게
    "우승": (16, 11, 15), "준우승": (10, 6, 7), "3위": (8, 5, 6), "4위": (5, 3, 4),
    "8강 탈락": (3, 2, 2), "16강 탈락": (2, 1, 1), "조별리그 탈락": (1, 0, -1),
}


# ─────────────────────────────────────────────
# 대회 생성 (추첨)
# ─────────────────────────────────────────────

def _entry_ovr(conn, team_id: int) -> float:
    row = conn.execute("SELECT AVG(ovr) AS v FROM ai_players WHERE team_id=?", (team_id,)).fetchone()
    base = row["v"] if row and row["v"] else 60
    return base + random.uniform(-2, 2)


def _seed_groups(all_teams: list) -> list:
    """32팀(강 순서로 이미 정렬된 리스트가 아니어도 됨 — 여기서 pts로 재정렬)을
    포트 4개(강한 8팀씩)로 나눠 8개 조에 스네이크 드래프트로 분배.
    같은 대륙 팀이 한 조에 몰리지 않도록 대륙이 겹치면 살짝 자리를 바꾼다
    (완벽한 회피는 아니고, 유럽처럼 팀이 많은 대륙은 불가피하게 겹칠 수 있음)."""
    ranked = sorted(all_teams, key=lambda t: -(1000 if t["auto"] else t["pts"]))
    pots = [ranked[i:i + 8] for i in range(0, 32, 8)]
    groups = [[] for _ in range(8)]
    for pot_idx, pot in enumerate(pots):
        order = range(8) if pot_idx % 2 == 0 else range(7, -1, -1)  # 스네이크
        for slot, team in zip(order, pot):
            groups[slot].append(team)

    # 같은 대륙 중복 완화: 조 안에서 대륙이 겹치면 다른 조의 겹치지 않는
    # 팀과 자리를 스왑 (간단한 1-pass 휴리스틱, 완벽 보장은 아님).
    for gi, g in enumerate(groups):
        conts = [t["continent"] for t in g]
        for i in range(len(g)):
            if conts.count(conts[i]) <= 1:
                continue
            for gj in range(8):
                if gj == gi:
                    continue
                for k, cand in enumerate(groups[gj]):
                    if cand["continent"] not in conts and groups[gi][i]["continent"] not in [t["continent"] for t in groups[gj]]:
                        groups[gi][i], groups[gj][k] = groups[gj][k], groups[gi][i]
                        conts = [t["continent"] for t in groups[gi]]
                        break
    return groups


def start_club_world_cup(year: int):
    """43주차 진입 시 호출. 그 해가 클럽월드컵 해라는 판단은 호출부
    (intl_engine.start_intl_tournament의 '빈 해' 분기)에서 이미 끝난 상태."""
    import time
    from game_engine import get_player, add_log
    _t0 = time.perf_counter()

    conn = get_conn()
    existing = conn.execute("SELECT id FROM cwc_tournaments WHERE year=?", (year,)).fetchone()
    if existing:
        conn.close()
        return   # 중복 생성 방지

    field = get_club_world_cup_field(year)
    _t1 = time.perf_counter()
    print(f"[PERF] 클럽월드컵 32팀 선발(4대륙 계수 계산): {_t1-_t0:.2f}s")
    all_teams = []
    for cont, teams in field.items():
        for t in teams:
            t2 = dict(t)
            t2["continent"] = cont
            all_teams.append(t2)

    if len(all_teams) < 8:   # 게임 극초반이라 챔스 역사가 거의 없으면 스킵
        conn.close()
        return

    groups = _seed_groups(all_teams)

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    my_in = any(t["team_id"] == my_tid for g in groups for t in g)

    cur = conn.execute(
        "INSERT INTO cwc_tournaments(year, my_in, my_team_id) VALUES(?,?,?)",
        (year, 1 if my_in else 0, my_tid if my_in else 0))
    tid = cur.lastrowid

    for gi, g in enumerate(groups):
        label = _GROUP_LABELS[gi]
        for t in g:
            ovr = _entry_ovr(conn, t["team_id"])
            conn.execute(
                """INSERT INTO cwc_entries(tournament_id, team_id, team_name, flag,
                                            country, continent, grp, grade, ovr)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (tid, t["team_id"], t["team_name"], "", t["country"], t["continent"],
                 label, "", ovr))
        # 조별리그 대진 (4팀 단일 라운드로빈 = 3라운드, 라운드당 2경기)
        rounds = generate_round_robin(4)
        for r_idx, pairs in enumerate(rounds):
            week = CWC_GROUP_WEEKS[0] + r_idx
            for a, b in pairs:
                home, away = g[a]["team_id"], g[b]["team_id"]
                is_my = 1 if my_tid in (home, away) else 0
                conn.execute(
                    """INSERT INTO cwc_matches(tournament_id, stage, week,
                                                home_team_id, away_team_id, is_my, grp)
                       VALUES(?,?,?,?,?,?,?)""",
                    (tid, "group", week, home, away, is_my, label))
    conn.commit()
    conn.close()
    print(f"[PERF] 클럽월드컵 생성 총 {time.perf_counter()-_t0:.2f}s")
    add_log(f"🏆 {year}년 클럽 월드컵 개막 — 8개조 32팀 (조 추첨 완료)", "event")


# ─────────────────────────────────────────────
# 주차 진행
# ─────────────────────────────────────────────

def _sim_one(conn, m):
    h_ovr = conn.execute("SELECT ovr FROM cwc_entries WHERE tournament_id=? AND team_id=?",
                          (m["tournament_id"], m["home_team_id"])).fetchone()["ovr"]
    a_ovr = conn.execute("SELECT ovr FROM cwc_entries WHERE tournament_id=? AND team_id=?",
                          (m["tournament_id"], m["away_team_id"])).fetchone()["ovr"]
    from game_engine import _gen_score
    outcome = _match_outcome(h_ovr, a_ovr)
    if outcome == "draw" and m["stage"] != "group":
        winner_home, pso = _resolve_pso(h_ovr, a_ovr)
        hs, as_ = (1, 1)   # 스코어는 동점으로 표기, 승자는 pso_winner로 별도 기록
        conn.execute(
            """UPDATE cwc_matches SET home_score=?, away_score=?,
               pso_winner=?, pso_score=? WHERE id=?""",
            (hs, as_, m["home_team_id"] if winner_home else m["away_team_id"], pso, m["id"]))
        return
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)
    conn.execute("UPDATE cwc_matches SET home_score=?, away_score=? WHERE id=?",
                 (hs, as_, m["id"]))


def process_cwc_week(week: int):
    """게임 주간 진행 루프에서 호출. 이번 주에 해당하는 cwc_matches를 전부 시뮬."""
    conn = get_conn()
    t = conn.execute(
        "SELECT * FROM cwc_tournaments WHERE status!='done' "
        "AND id IN (SELECT DISTINCT tournament_id FROM cwc_matches WHERE week=?)",
        (week,)).fetchone()
    if not t:
        conn.close()
        return
    matches = conn.execute(
        "SELECT * FROM cwc_matches WHERE tournament_id=? AND week=? AND home_score=-1",
        (t["id"], week)).fetchall()
    for m in matches:
        _sim_one(conn, dict(m))
    conn.commit()

    if week == CWC_GROUP_WEEKS[1]:
        _finalize_group_stage(dict(t))
    elif week in CWC_KO_WEEK.values():
        stage = [s for s, w in CWC_KO_WEEK.items() if w == week and s != "F"]
        # F/TP는 같은 주라 별도 분기(둘 다 이번 주에 이미 매치가 깔려 있음)
        if week == CWC_KO_WEEK["F"]:
            _finish_tournament(dict(t))
        elif stage:
            _advance_round(dict(t), stage[0])
    conn.close()


def _group_standings(conn, tid, grp):
    entries = conn.execute("SELECT * FROM cwc_entries WHERE tournament_id=? AND grp=?",
                            (tid, grp)).fetchall()
    tbl = {e["team_id"]: {"team_id": e["team_id"], "team_name": e["team_name"],
                           "country": e["country"], "ovr": e["ovr"],
                           "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    matches = conn.execute(
        "SELECT * FROM cwc_matches WHERE tournament_id=? AND stage='group' "
        "AND home_team_id IN (SELECT team_id FROM cwc_entries WHERE tournament_id=? AND grp=?)",
        (tid, tid, grp)).fetchall()
    for m in matches:
        h, a = tbl.get(m["home_team_id"]), tbl.get(m["away_team_id"])
        if not h or not a or m["home_score"] < 0:
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
    ranked = sorted(tbl.values(), key=lambda r: (-r["pts"], -(r["gf"] - r["ga"]), -r["gf"]))
    return ranked


def _finalize_group_stage(t):
    from game_engine import add_log
    conn = get_conn()
    qualifiers = []
    for label in _GROUP_LABELS:
        standings = _group_standings(conn, t["id"], label)
        qualifiers.extend(standings[:2])   # 각 조 1·2위
    conn.execute("UPDATE cwc_tournaments SET status='ko' WHERE id=?", (t["id"],))
    conn.commit()
    _build_knockout(conn, t["id"], [q["team_id"] for q in qualifiers])
    conn.commit()
    conn.close()
    add_log(f"⚽ {t['year']}년 클럽 월드컵 조별리그 종료 — 16강 진출 16팀 확정", "event")


def _build_knockout(conn, tid, qualifier_ids):
    """16팀을 시드 순서(조 1위끼리/2위끼리 안 붙게 스네이크)로 16강 대진."""
    random.shuffle(qualifier_ids)   # 조 추첨식 랜덤 매칭(간단화)
    week = CWC_KO_WEEK["R16"]
    for i in range(0, 16, 2):
        conn.execute(
            "INSERT INTO cwc_matches(tournament_id, stage, week, home_team_id, away_team_id) "
            "VALUES(?,?,?,?,?)",
            (tid, "R16", week, qualifier_ids[i], qualifier_ids[i + 1]))


def _round_winner(m):
    if m["home_score"] > m["away_score"]:
        return m["home_team_id"]
    if m["away_score"] > m["home_score"]:
        return m["away_team_id"]
    return m["pso_winner"]   # 동점이면 PSO로 이미 승자 기록됨


def _advance_round(t, cur_stage):
    conn = get_conn()
    matches = conn.execute(
        "SELECT * FROM cwc_matches WHERE tournament_id=? AND stage=? ORDER BY id",
        (t["id"], cur_stage)).fetchall()
    winners = [_round_winner(dict(m)) for m in matches]
    nxt = _STAGE_ORDER[_STAGE_ORDER.index(cur_stage) + 1]
    week = CWC_KO_WEEK[nxt]
    for i in range(0, len(winners), 2):
        conn.execute(
            "INSERT INTO cwc_matches(tournament_id, stage, week, home_team_id, away_team_id) "
            "VALUES(?,?,?,?,?)",
            (t["id"], nxt, week, winners[i], winners[i + 1]))
    if nxt == "SF":
        pass   # 4강 결과 나오면 F/TP는 _finish_tournament 진입 직전 별도 생성 필요
    conn.commit()
    conn.close()


def _finish_tournament(t):
    """50주차: _advance_round(SF→F)가 이미 결승 대진은 만들어놨으므로,
    여기서는 3/4위전(TP, SF 패자끼리)만 추가로 만들고 F/TP를 시뮬한 뒤
    최종 결과를 확정한다.

    [2026-07 버그수정] 예전엔 "F가 아직 없으면 F+TP를 만든다"는 조건이었는데,
    _advance_round가 SF→F를 이미 만들어놔서 이 조건이 항상 거짓이 되어
    TP 자체가 한 번도 생성되지 않았다(3/4위가 계속 빈칸으로 나오던 원인).
    TP 생성은 F 존재 여부와 완전히 독립적으로 처리한다."""
    from game_engine import add_log, get_player
    conn = get_conn()
    sf = conn.execute("SELECT * FROM cwc_matches WHERE tournament_id=? AND stage='SF' ORDER BY id",
                       (t["id"],)).fetchall()
    if sf and not conn.execute(
            "SELECT 1 FROM cwc_matches WHERE tournament_id=? AND stage='TP'", (t["id"],)).fetchone():
        losers = [m["away_team_id"] if _round_winner(dict(m)) == m["home_team_id"] else m["home_team_id"]
                  for m in sf]
        conn.execute("INSERT INTO cwc_matches(tournament_id,stage,week,home_team_id,away_team_id) "
                     "VALUES(?,'TP',?,?,?)", (t["id"], CWC_KO_WEEK["TP"], losers[0], losers[1]))
        conn.commit()

    # 결승/3-4위전 중 아직 안 뛴 것만 시뮬 (멱등 — is_my 경기는 이미
    # simulate_my_cwc_match가 앞서 채워놨을 수 있으므로 그건 건드리지 않음)
    for m in conn.execute("SELECT * FROM cwc_matches WHERE tournament_id=? AND stage IN ('F','TP') "
                           "AND home_score=-1", (t["id"],)).fetchall():
        _sim_one(conn, dict(m))
    conn.commit()

    final = conn.execute("SELECT * FROM cwc_matches WHERE tournament_id=? AND stage='F'", (t["id"],)).fetchone()
    winner_id = _round_winner(dict(final))
    conn.execute("UPDATE cwc_tournaments SET status='done', winner_team_id=? WHERE id=?",
                 (winner_id, t["id"]))

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    if t["my_in"] and my_tid:
        tp = conn.execute("SELECT * FROM cwc_matches WHERE tournament_id=? AND stage='TP'", (t["id"],)).fetchone()
        if my_tid == winner_id:
            result = "우승"
        elif my_tid in (final["home_team_id"], final["away_team_id"]):
            result = "준우승"
        elif tp and my_tid == _round_winner(dict(tp)):
            result = "3위"
        elif tp and my_tid in (tp["home_team_id"], tp["away_team_id"]):
            result = "4위"
        else:
            # SF 이전에 탈락한 경우 — 가장 마지막으로 참가한 라운드로 판정
            last_stage = None
            for s in ["QF", "R16", "group"]:
                if conn.execute("SELECT 1 FROM cwc_matches WHERE tournament_id=? AND stage=? "
                                 "AND (home_team_id=? OR away_team_id=?)",
                                 (t["id"], s, my_tid, my_tid)).fetchone():
                    last_stage = s; break
            result = {"QF": "8강 탈락", "R16": "16강 탈락", "group": "조별리그 탈락"}.get(last_stage, "조별리그 탈락")
        _record_my_result(conn, t["id"], t["year"], my_tid, result)

    conn.commit()
    conn.close()
    add_log(f"🏆 {t['year']}년 클럽 월드컵 폐막", "event")


def _cwc_team_stage_weights(conn, tid):
    """클럽월드컵 참가 팀별 '진출 라운드 가중치' — champions_engine.
    _cl_team_stage_weights와 완전히 같은 설계(신민용 확정: "대회 MVP/
    베스트11에 팀 성적을 반영하자"), 클럽월드컵 스테이지 구성(조별리그→
    16강→8강→4강→결승/3·4위전)에 맞게 적용. 조별리그만=0.70, 16강=0.80,
    8강=0.90, 4강(3/4위전 포함)=0.96, 준우승=0.99, 우승=1.00."""
    t = conn.execute("SELECT winner_team_id FROM cwc_tournaments WHERE id=?", (tid,)).fetchone()
    winner_tid = t["winner_team_id"] if t else 0
    _ORDER = {"R16": 0, "QF": 1, "SF": 2}
    _TIER_W = {0: 0.80, 1: 0.90, 2: 0.96}
    furthest = {}
    runner_up_tid = None
    for m in conn.execute(
            "SELECT stage, home_team_id, away_team_id FROM cwc_matches "
            "WHERE tournament_id=? AND stage!='group' AND home_score>=0", (tid,)).fetchall():
        stg = m["stage"]
        if stg == "F":
            loser = m["away_team_id"] if m["home_team_id"] == winner_tid else m["home_team_id"]
            runner_up_tid = loser
            continue
        if stg == "TP":
            for side_tid in (m["home_team_id"], m["away_team_id"]):
                furthest[side_tid] = max(furthest.get(side_tid, -1), _ORDER["SF"])
            continue
        if stg not in _ORDER:
            continue
        idx = _ORDER[stg]
        for side_tid in (m["home_team_id"], m["away_team_id"]):
            furthest[side_tid] = max(furthest.get(side_tid, -1), idx)

    def _weight(team_id):
        if team_id == winner_tid:
            return 1.00
        if team_id == runner_up_tid:
            return 0.99
        return _TIER_W.get(furthest.get(team_id, -1), 0.70)
    return _weight


def _award_cwc_awards(conn, tid, year, my_tid):
    """[2026-07 확장, 신민용 확정] 클럽월드컵 MVP/득점왕/베스트11/영플레이어/
    골든글러브. 리그 상 시스템의 공용 함수(_position_award_score,
    _evaluate_extra_awards)를 그대로 재사용.
    [2026-07 추가 확장, 설계문서 v2 반영] 결승·준결승 빅게임 보너스(가산,
    상한 있음)와 골든글러브 세이브율·평균실점 품질 게이트를 추가한다.
    cwc_matches엔 my_conceded 컬럼이 따로 없어서(cl/intl_matches와 다름),
    home_score/away_score와 내 소속 팀 여부로 직접 실점을 계산한다."""
    from game_engine import (get_player, add_log, _estimate_ai_season, _estimate_ai_clean_sheets,
                             _position_award_score, _evaluate_extra_awards,
                             _cap_additive_bonus, _gk_quality_ok,
                             ATTACK_POS, GK_POS, DF_POS, MF_POS)
    my_row = conn.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(my_goals),0) g, COALESCE(SUM(my_assists),0) a,
                  COALESCE(AVG(my_rating),0) r, COALESCE(SUM(my_saves),0) sv,
                  COALESCE(SUM(CASE WHEN home_team_id=? THEN away_score ELSE home_score END),0) gc
           FROM cwc_matches WHERE tournament_id=? AND my_played=1""", (my_tid, tid)).fetchone()
    n_games = max(1, my_row["n"])
    p = get_player()
    my_pos = p.get("position", "ST") if p else "ST"
    my_ovr = p.get("ovr", 60) if p else 60
    my_age = p.get("age", 25) if p else 25
    my_cs = conn.execute(
        """SELECT COUNT(*) c FROM cwc_matches WHERE tournament_id=? AND my_played=1
           AND ((home_team_id=? AND away_score=0) OR (away_team_id=? AND home_score=0))""",
        (tid, my_tid, my_tid)).fetchone()["c"]

    pool = [{"position": my_pos, "goals": my_row["g"], "assists": my_row["a"], "rating": my_row["r"],
             "ovr": my_ovr, "cs": my_cs, "age": my_age, "is_mine": True, "team_id": my_tid}]

    entries = conn.execute(
        "SELECT team_id, country FROM cwc_entries WHERE tournament_id=?", (tid,)).fetchall()
    ALL_POS = GK_POS + DF_POS + MF_POS + ATTACK_POS
    ph = ",".join("?" * len(ALL_POS))
    for e in entries:
        if e["team_id"] == my_tid:
            continue
        rows = conn.execute(
            f"""SELECT ovr, position, sub_role, age FROM ai_players
                WHERE team_id=? AND position IN ({ph})""",
            (e["team_id"], *ALL_POS)).fetchall()
        for r in rows:
            g, a, rt = _estimate_ai_season(r["ovr"], r["position"], 80, 80, r["sub_role"],
                                           full_season_matches=n_games)
            cs = _estimate_ai_clean_sheets(r["position"], r["ovr"], 80, 80, n_games) if r["position"] in GK_POS else 0
            pool.append({"position": r["position"], "goals": g, "assists": a, "rating": rt,
                        "ovr": r["ovr"], "cs": cs, "age": r["age"] or 25, "is_mine": False,
                        "team_id": e["team_id"]})

    # [2026-07 신설] 팀 진출 라운드 가중치 — MVP/베스트11/영플레이어에만 적용
    _stage_w = _cwc_team_stage_weights(conn, tid)
    my_base_score = _position_award_score(my_pos, my_row["g"], my_row["a"], my_row["r"], my_ovr, my_cs)
    my_score = my_base_score * _stage_w(my_tid)

    # [2026-07 신설] 빅게임 보너스 — champions_engine._award_cl_awards와 동일 설계.
    _bg = conn.execute(
        """SELECT COUNT(*) n, COALESCE(AVG(my_rating),0) r, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a
           FROM cwc_matches WHERE tournament_id=? AND my_played=1 AND stage IN ('SF','F','TP')""",
        (tid,)).fetchone()
    if _bg and _bg["n"] > 0:
        _raw_bonus = (_bg["r"] - 6.0) * 1.2 + (_bg["g"] + _bg["a"]) * 0.8
        my_score += _cap_additive_bonus(_raw_bonus, my_base_score, cap_ratio=0.10)

    others = [x for x in pool if not x["is_mine"]]
    best_ai_scorer_g = max((x["goals"] for x in others), default=-1)
    best_ai_mvp_score = max((_position_award_score(x["position"], x["goals"], x["assists"],
                                                    x["rating"], x["ovr"], x["cs"]) * _stage_w(x["team_id"])
                              for x in others), default=-1)

    awards = []
    if my_row["g"] > 0 and my_row["g"] >= best_ai_scorer_g:
        awards.append(("클럽 월드컵 득점왕", f"{my_row['g']}골"))
    if my_score >= best_ai_mvp_score and my_row["n"] > 0:
        awards.append(("클럽 월드컵 MVP", f"{year} 클럽 월드컵 MVP"))
    for label in _evaluate_extra_awards(pool, my_pos, my_age,
                                         weight_fn=lambda x: _stage_w(x["team_id"])):
        awards.append((f"클럽 월드컵 {label}", f"{year} 클럽 월드컵 {label}"))
    if (my_pos in GK_POS and my_cs >= 2
            and _gk_quality_ok(my_row["sv"], my_row["gc"], n_games, n_games, min_play_ratio=0.0)):
        gk_group = [x for x in pool if x["position"] in GK_POS]
        best_gk = max(gk_group, key=lambda x: x["cs"]) if gk_group else None
        if best_gk and best_gk["is_mine"]:
            awards.append(("클럽 월드컵 골든글러브", f"{my_cs} 클린시트"))

    for atype, detail in awards:
        add_log(f"🏅 {atype} 수상! ({detail})", "event")
        conn.execute(
            "INSERT INTO awards(year,award_type,league_name,detail,is_mine) VALUES(?,?,?,?,1)",
            (year, atype, "클럽 월드컵", detail))
    if awards:
        conn.commit()


def _record_my_result(conn, tid, year, my_tid, result):
    from game_engine import add_log, update_player, get_player
    team_row = conn.execute("SELECT name FROM teams WHERE id=?", (my_tid,)).fetchone()
    team_name = team_row["name"] if team_row else ""
    conn.execute("UPDATE cwc_tournaments SET my_result=? WHERE id=?", (result, tid))

    # trophy_log: tier=-3로 클럽월드컵 구분 (get_my_trophies()가 tier!=0을
    # 전부 career_entries로 자동 필터링해주므로 여기서 별도 처리 불필요)
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=?", (year, "클럽 월드컵")).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,-3,?)""", (year, team_name, result, "클럽 월드컵"))

    # cl_history 재사용 — competition="클럽 월드컵"으로 구분, 개인 기록 집계
    agg = conn.execute(
        """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM cwc_matches WHERE tournament_id=? AND my_played=1""", (tid,)).fetchone()
    exists2 = conn.execute(
        "SELECT id FROM cl_history WHERE year=? AND competition=?", (year, "클럽 월드컵")).fetchone()
    if not exists2:
        conn.execute("""INSERT INTO cl_history(year, competition, team_name, result,
                                               goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (year, "클럽 월드컵", team_name, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))

    fame_g, pop_g, hap_g = _REWARD.get(result, (0, 0, 0))
    p = get_player()
    if p:
        update_player(
            fame=min(100, p.get("fame", 0) + fame_g),
            popularity=min(100, p.get("popularity", 0) + pop_g),
            happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
        )
    add_log(f"🏆 클럽 월드컵 최종 성적: {result}", "event")

    # [2026-07 신설] 클럽월드컵 MVP/득점왕 판정
    _award_cwc_awards(conn, tid, year, my_tid)


# ─────────────────────────────────────────────
# 조회 헬퍼 (UI/표시용)
# ─────────────────────────────────────────────

def get_my_cwc_matches():
    """내가 실제 출전(또는 결장)한 클럽월드컵 경기 목록 (시간순) —
    champions_engine.get_my_cl_matches()와 완전히 동일한 반환 형식.
    career_window.py/retire_window.py가 cl/cup과 나란히 이 함수도
    호출하도록 연결하면 그대로 표시된다."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT m.*, t.year AS t_year, t.name AS comp
           FROM cwc_matches m
           JOIN cwc_tournaments t ON m.tournament_id = t.id
           WHERE m.my_played = 1 OR m.my_absence_reason IS NOT NULL
           ORDER BY t.year, m.week""").fetchall()]
    names = {(r["tournament_id"], r["team_id"]): (r["team_name"], r["country"])
             for r in conn.execute(
                 "SELECT tournament_id, team_id, team_name, country FROM cwc_entries").fetchall()}
    conn.close()

    out = []
    from game_engine import get_player
    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    stage_ko = {"group": "조별리그", "R16": "16강", "QF": "8강", "SF": "4강",
                "F": "결승", "TP": "3/4위전"}

    for m in rows:
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

        my_name, _my_country = names.get(
            (m["tournament_id"], m["home_team_id"] if is_home else m["away_team_id"]), ("", ""))
        opp_name, opp_country = names.get((m["tournament_id"], opp_id), ("?", ""))
        opp_disp = team_display(opp_name, opp_country) if opp_country else opp_name

        from constants import week_to_iso_date_str
        date_str = week_to_iso_date_str(m["t_year"], m["week"])

        out.append({
            "year": m["t_year"], "week": m["week"], "date": date_str,
            "position": m["my_position"], "team": my_name, "team_flag": "",
            "comp": m["comp"], "stage": stage_ko.get(m["stage"], m["stage"]),
            "opp": opp_disp, "opp_flag": "",
            "goals": m["my_goals"], "assists": m["my_assists"],
            "saves": m["my_saves"], "conceded": op_s,
            "rating": m["my_rating"],
            "shots": 0, "shots_on": 0, "key_passes": 0, "dribbles": 0,
            "blocks": 0, "pass_acc": 0,
            "score": f"{my_s}-{op_s}", "result": result,
            "absence_reason": m.get("my_absence_reason"),
        })
    return out


def get_cwc_group_standings(tournament_id, grp):
    """조 순위 계산: 승점 → 득실 → 다득점 → 팀 등급(grade).
    [2026-07 신설, 신민용 리포트: "클럽월드컵 경기 일정 화면이 컵대회처럼
    단순 표로 뜨는데, 국제대회 예선처럼 좌측엔 조별 순위표 / 우측엔 조별
    일정으로 나눠서 보고 싶다"] intl_engine.get_group_standings와 완전히
    같은 패턴이며, 국가 대신 클럽팀(team_id/team_name/country) 기준으로
    집계한다는 점만 다르다."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM cwc_entries WHERE tournament_id=? AND grp=?",
        (tournament_id, grp)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        """SELECT * FROM cwc_matches WHERE tournament_id=? AND grp=?
           AND stage='group' AND home_score>=0""", (tournament_id, grp)).fetchall()]
    conn.close()

    _GRADE_RANK = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
    tbl = {e["team_id"]: {"team_id": e["team_id"], "team_name": e["team_name"],
                           "country": e["country"], "continent": e["continent"],
                           "grade_rank": _GRADE_RANK.get(e["grade"], 0),
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
    rows.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["grade_rank"]), reverse=True)
    return rows


def team_display(team_name: str, country: str) -> str:
    """챔스와 동일한 표시 포맷: '팀명(나라)'."""
    return f"{team_name}({country})"


def has_my_cwc_match_between(week_from, week_to):
    """주차 범위 내 내 클럽월드컵 경기 존재 여부 (센터패널 표시용).
    [2026-07 버그수정, 리뷰 중 발견] intl_engine.has_my_match_between /
    champions_engine.has_my_cl_match_between / cup_engine.has_my_cup_match_between와
    똑같은 용도의 함수가 클럽월드컵(신설 기능)에만 없었다 — ui/center_panel.py의
    _check_match()가 리그/국제대회/챔스/컵대회만 확인하고 클럽월드컵은 아예
    확인을 안 해서, 43~52주(클럽월드컵 진행 구간이자 클럽 시즌이 쉬는
    국제대회 전용구간)에 클럽월드컵 경기만 있으면 실제로는 경기가 있는데도
    "이번 주 경기 없음" 배너가 잘못 떴다."""
    for w in range(week_from, week_to + 1):
        if get_my_cwc_match(w):
            return True
    return False


def get_my_cwc_match(week: int):
    """이번 주차에 내가 뛸 클럽월드컵 경기가 있으면 dict, 없으면 None.
    champions_engine.get_my_cl_match와 동일한 반환 형식(키 이름까지)으로
    맞췄다 — game_engine.py의 im/cm 패턴에 그대로 cw로 끼워넣기 위함."""
    from game_engine import get_player, get_state
    p = get_player()
    st = get_state()
    if not p or not st:
        return None
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    conn = get_conn()
    t = conn.execute(
        "SELECT * FROM cwc_tournaments WHERE year=? AND status!='done'",
        (st["current_year"],)).fetchone()
    if not t:
        conn.close()
        return None
    reg_tid = t["my_team_id"]
    if not reg_tid or reg_tid != tid:
        conn.close()
        return None
    m = conn.execute(
        """SELECT * FROM cwc_matches
           WHERE tournament_id=? AND week=? AND home_score=-1
             AND (home_team_id=? OR away_team_id=?)""",
        (t["id"], week, tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    oe = conn.execute(
        "SELECT team_name, country FROM cwc_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], opp_id)).fetchone()
    conn.close()
    stage_ko = {"group": "조별리그", "R16": "16강", "QF": "8강", "SF": "4강",
                "F": "결승", "TP": "3/4위전"}.get(m["stage"], m["stage"])
    return {
        "cwc": True,                      # 클럽 월드컵 경기 표시 플래그
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": "클럽 월드컵",
        "stage": m["stage"],
        "stage_ko": stage_ko,
        "grp": m["grp"] if "grp" in m.keys() else "",
        "opp": oe["team_name"] if oe else "?",
        "opp_country": oe["country"] if oe else "?",
        "opp_flag": "",
        "is_home": is_home,
        "week": week,
    }


def sim_my_cwc_match_as_ai(week, p, reason="injury"):
    """부상 등으로 내가 못 뛸 때 내 클럽월드컵 경기를 AI끼리 시뮬 —
    champions_engine.sim_my_cl_match_as_ai와 동일한 이유(안 하면 대회
    진행이 멈춤)."""
    info = get_my_cwc_match(week)
    if not info:
        return
    conn = get_conn()
    m = conn.execute("SELECT * FROM cwc_matches WHERE id=?", (info["match_id"],)).fetchone()
    if not m or m["home_score"] != -1:
        conn.close()
        return
    _sim_one(conn, dict(m))
    conn.commit()
    conn.close()


def simulate_my_cwc_match(week, p):
    """내가 직접 뛰는 클럽월드컵 경기 — champions_engine.simulate_my_cl_match와
    동일한 패턴(개인 스탯 반영 + 클릭 가능한 매치 로그)을 그대로 따른다."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _roll_red_card, _apply_red_card_dismissal)
    info = get_my_cwc_match(week)
    if not info:
        return
    conn = get_conn()
    m = dict(conn.execute("SELECT * FROM cwc_matches WHERE id=?", (info["match_id"],)).fetchone())
    he = dict(conn.execute("SELECT * FROM cwc_entries WHERE tournament_id=? AND team_id=?",
                            (m["tournament_id"], m["home_team_id"])).fetchone())
    ae = dict(conn.execute("SELECT * FROM cwc_entries WHERE tournament_id=? AND team_id=?",
                            (m["tournament_id"], m["away_team_id"])).fetchone())
    conn.close()
    is_home = info["is_home"]

    _suspended, _new_susp = _check_suspended(p, field="cwc_suspension")
    if _suspended:
        update_player(cwc_suspension=_new_susp)
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event")

    # 내 출전 보너스 — 챔스(simulate_my_cl_match)와 완전히 동일한 공식.
    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = max(0.0, _my_ovr - _team_ovr)
    _star = 1.0 + max(0.0, (_my_ovr - 60) / 40.0) ** 1.8 * 3.0
    bonus = _gap * 0.30 * _star + max(0.0, _my_ovr - 50) * 0.08
    bonus = _soft_cap(bonus, 30.0)
    from constants import PERSONALITY_EFFECTS
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if "team_win_bonus" in _pe:
        bonus *= (1.0 + _pe["team_win_bonus"])
    if _suspended:
        bonus = 0.0
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = _match_outcome(h_ovr, a_ovr)
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "group")
    if outcome == "draw" and is_ko:
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    if _suspended:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
        _absence_reason = "suspension"
    else:
        _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr)
        _absence_reason = None
        if _roll_red_card(p):
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(p, field="cwc_suspension")
            _absence_reason = "red_card"
    if not _suspended and "big_match_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["big_match_rating"], 1)))
    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    conn = get_conn()
    conn.execute("""UPDATE cwc_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?,
                    my_played=?, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?,
                    my_absence_reason=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score,
                  0 if _suspended else 1, _get_field_pos_safe(p),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  _absence_reason, m["id"]))
    conn.commit()
    conn.close()

    update_player(
        total_shots=p.get("total_shots", 0) + detail["shots"],
        total_shots_on=p.get("total_shots_on", 0) + detail["shots_on"],
        total_key_passes=p.get("total_key_passes", 0) + detail["key_passes"],
        total_dribbles=p.get("total_dribbles", 0) + detail["dribbles"],
        total_blocks=p.get("total_blocks", 0) + detail["blocks"],
    )

    _update_pop(p, goals, assists, rating)
    p2 = get_player()
    ns = min(100, p2["stress"] + 20)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    stage_ko = info["stage_ko"]
    my_tid = p.get("current_team_id", 0)
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == my_tid else '패'})"
        rs = "무"

    comp_name = f"클럽 월드컵 {stage_ko}".strip()
    home_disp = team_display(he["team_name"], he["country"])
    away_disp = team_display(ae["team_name"], ae["country"])
    pso = {"won": pso_winner == my_tid, "score": pso_score} if pso_winner else None
    detail_id = _save_match_detail(
        p, week, comp_name, is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, True, False, detail, pso=pso)
    marker = f" [match:{detail_id}]" if detail_id else ""

    add_log("─" * 44, "sep")
    add_log(f"🏆 {comp_name}  {week}주차{marker}", "match")
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


def _get_field_pos_safe(p):
    from game_engine import get_field_pos
    return get_field_pos(p)