# -*- coding: utf-8 -*-
"""
클럽 월드컵 대회 진행 엔진.

[2026-07 신설] club_world_cup.py가 "32팀이 누구인지"를 정하면, 이 파일이
그 32팀으로 실제 대회(8조×4팀 조별리그 → 16강~결승/3·4위전)를 진행한다.

캘린더: 국제대회 전용구간(43~52주) 중 "빈 해"(예: 2003,2007,2011...)에만
열린다 — intl_engine.start_intl_tournament()가 is_wc/is_cont/is_wc_qual가
전부 False인 해를 감지하면 이 모듈의 start_club_world_cup()을 대신 호출한다.

주차 배분 (2026-07 v2, 월드컵(world_cup_32)과 완전히 동일한 규칙 재사용):
    43주: 조 추첨
    45~46주: 조별리그 (팀당 3경기, day 기반 4일 간격으로 2주 압축)
    47주: 16강
    47주 후반~48주: 8강
    48주 후반~49주: 4강
    49주 후반~50주: 결승 + 3/4위전 (라운드 간격 4일)
    (정확한 day는 constants.TOURNAMENT_SCHEDULE_RULES["world_cup_32"]가
    결정 — intl_engine.py의 월드컵 32강 체제와 1:1로 동일하다.)

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
from constants import (
    generate_round_robin, week_to_day, day_to_week,
    INTL_GROUP_WEEKS, TOURNAMENT_SCHEDULE_RULES,
    stage_round_start_day, assign_match_days,
)
from champions_engine import _match_outcome, _resolve_pso
from club_world_cup import get_club_world_cup_field, CWC_QUOTA

CWC_DRAW_WEEK = 43
# [2026-07 재설계 v2, 신민용 요청: "클럽 월드컵도 월드컵처럼 45주차부터
# 그룹전, 47주차부터 바로 본선으로, 하루 단위로"] v1(2026-07)에서는 CWC를
# 44주차부터 자체 상수(CWC_GROUP_START_DAY/CWC_KO_WEEK)로 독자적으로
# 운영했는데, 그러다 보니 (a) 월드컵보다 한 주 일찍 시작해 시작 시점이
# 서로 안 맞았고 (b) 토너먼트가 여전히 '그 주 첫날' 단위(요일만 수요일로
# 밀어둔 수준)라 진짜 하루 단위 압축 간격(라운드 간 4일)까지는 못 갔다.
# CWC는 32팀·8조·4팀조·16강~결승/3-4위전 구조가 world_cup_32와 완전히
# 동일하므로, 그 규칙(constants.TOURNAMENT_SCHEDULE_RULES["world_cup_32"])
# 을 그대로 재사용한다 — anchor(시작일)만 intl_engine과 똑같이
# INTL_GROUP_WEEKS[0](45주차)로 맞추면, 조별리그 day/주차부터 16강~결승
# 간격까지 월드컵과 완전히 동일해진다(그룹 2주 압축 + KO 라운드 4일 간격).
_CWC_TOURNAMENT_TYPE = "world_cup_32"
CWC_START_DAY = week_to_day(INTL_GROUP_WEEKS[0])
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


def _precreate_cwc_ko_shell(conn, tid):
    """[2026-07 신설, 신민용 설계 제안: "경기 자체는 미리 존재하고 참가팀만
    나중에 확정된다"] 조별리그를 만드는 시점에 16강~결승/3-4위전의 '빈
    대진' 행을 미리 만들어둔다 — home_team_id/away_team_id를 0(미정
    sentinel)으로 둔 placeholder다. 실제 진출팀이 정해지면(_build_knockout/
    _advance_round) 새 행을 INSERT하는 대신 이 placeholder를 UPDATE해서
    팀 ID만 채워 넣는다(intl_engine._precreate_ko_shell과 동일한 설계).
    브래킷 크기(16강 8경기→8강 4→4강 2→3/4위전 1→결승 1)는 조별리그
    결과와 무관하게 항상 고정이라 대회 시작 시점에 전부 계산 가능하다.

    [2026-07 v2, 신민용 요청: "클럽 월드컵도 월드컵처럼 하루 단위로"]
    day 계산을 CWC 전용 상수 대신 world_cup_32 규칙(TOURNAMENT_SCHEDULE_
    RULES)에서 그대로 가져온다 — 월드컵과 대회 형태(32팀/8조/16강~결승)가
    같아서 규칙을 공유해도 결과가 완전히 동일하다(라운드당 4일 간격)."""
    rows = []
    for r in TOURNAMENT_SCHEDULE_RULES.get(_CWC_TOURNAMENT_TYPE, []):
        stage = r["stage"]
        if stage == "group":
            continue
        start_day = stage_round_start_day(_CWC_TOURNAMENT_TYPE, stage, r["round"], CWC_START_DAY)
        n = r["match_count"]
        day_list = assign_match_days(start_day, n, r["cap"])
        for idx in range(n):
            d = day_list[idx]
            wk = day_to_week(d)
            slot = 999 if stage == "TP" else idx
            rows.append((tid, stage, wk, d, 0, 0, slot))
    conn.executemany(
        """INSERT INTO cwc_matches(tournament_id, stage, week, day,
                                    home_team_id, away_team_id, slot)
           VALUES(?,?,?,?,?,?,?)""", rows)


def _fill_cwc_ko_shell(conn, tid, stage, fills):
    """_precreate_cwc_ko_shell로 미리 만들어둔 그 stage의 placeholder 행에
    실제 팀 ID를 채워 넣는다(slot으로 매칭). placeholder가 없으면(과거
    세이브 호환 등 예외) 그 slot만 새로 INSERT해서 안전하게 폴백한다.
    fills: {slot: (home_team_id, away_team_id, is_my)}"""
    for slot, (home, away, is_my) in fills.items():
        cur = conn.execute(
            """UPDATE cwc_matches SET home_team_id=?, away_team_id=?, is_my=?
               WHERE tournament_id=? AND stage=? AND slot=?""",
            (home, away, is_my, tid, stage, slot))
        if cur.rowcount == 0:
            conn.execute(
                """INSERT INTO cwc_matches(tournament_id, stage, week, day,
                                            home_team_id, away_team_id, is_my, slot)
                   VALUES(?,?,1,NULL,?,?,?,?)""",
                (tid, stage, home, away, is_my, slot))


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
        # [2026-07 v2] day 계산을 world_cup_32의 조별리그 규칙에서 그대로
        # 가져온다 — 월드컵과 정확히 같은 날짜/주차(45~46주, 2주 압축)로
        # 진행된다(신민용 요청: "클럽 월드컵도 월드컵처럼").
        rounds = generate_round_robin(4)
        for r_idx, pairs in enumerate(rounds):
            day_val = stage_round_start_day(_CWC_TOURNAMENT_TYPE, "group", r_idx + 1, CWC_START_DAY)
            week = day_to_week(day_val)
            for a, b in pairs:
                home, away = g[a]["team_id"], g[b]["team_id"]
                is_my = 1 if my_tid in (home, away) else 0
                conn.execute(
                    """INSERT INTO cwc_matches(tournament_id, stage, week, day,
                                                home_team_id, away_team_id, is_my, grp)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (tid, "group", week, day_val, home, away, is_my, label))

    # [2026-07 신설] 조별리그가 끝나기도 전에 16강~결승/3-4위전의 '빈
    # 대진'을 미리 만들어둔다 — 자세한 이유는 _precreate_cwc_ko_shell 참고.
    _precreate_cwc_ko_shell(conn, tid)

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


def process_cwc_week(week: int, day=None):
    """게임 주간 진행 루프에서 호출. 이번 주(또는 day)까지의 cwc_matches를 시뮬.

    [2026-07 재설계, 신민용 요청: "클럽 월드컵도 1주 단위로, 그룹전은
    2주 안에 3경기"] 조별리그 3라운드가 이제 day 기반(4일 간격)으로
    배정돼 44주와 45주에 걸쳐 나뉠 수 있다 — 그래서 예전처럼 "week ==
    그룹 마지막 주(46)"라는 고정값으로 그룹 종료를 판정하면 더는 맞지
    않는다. intl_engine._process_one_tournament_week와 동일하게, 남은
    미완료 경기 유무로 "그 단계가 실제로 끝났는지"를 직접 판정한다 —
    몇 주에 걸치든 항상 정확하다.
    day를 넘기면 아직 실제 날짜가 안 된(day가 미래인) 경기는 건드리지
    않고, 그날이 와서 advance_days의 정상 경로가 처리하게 둔다(intl_engine
    과 동일한 이유 — process_intl_week 버그수정 주석 참고)."""
    conn = get_conn()
    t = conn.execute(
        "SELECT * FROM cwc_tournaments WHERE status!='done' "
        "AND id IN (SELECT DISTINCT tournament_id FROM cwc_matches WHERE week<=?)",
        (week,)).fetchone()
    if not t:
        conn.close()
        return
    tid = t["id"]
    # [2026-07 안전장치] home_team_id/away_team_id=0은 아직 참가팀이
    # 확정 안 된 placeholder(_precreate_cwc_ko_shell) — 팀 없는 경기를
    # 시뮬하면 크래시하므로 여기서 제외한다. 진출이 확정되면
    # _fill_cwc_ko_shell이 채워 넣은 뒤에야 이 조회에 걸린다.
    if day is not None:
        matches = conn.execute(
            """SELECT * FROM cwc_matches WHERE tournament_id=? AND home_score=-1
               AND home_team_id!=0 AND away_team_id!=0
               AND ((day IS NOT NULL AND day<=?) OR (day IS NULL AND week<=?))
               ORDER BY id""",
            (tid, day, week)).fetchall()
    else:
        matches = conn.execute(
            """SELECT * FROM cwc_matches WHERE tournament_id=? AND week<=? AND home_score=-1
               AND home_team_id!=0 AND away_team_id!=0 ORDER BY id""",
            (tid, week)).fetchall()
    for m in matches:
        _sim_one(conn, dict(m))
    conn.commit()

    status = t["status"]
    if status == "group":
        group_pending = conn.execute(
            "SELECT COUNT(*) n FROM cwc_matches WHERE tournament_id=? AND stage='group' AND home_score=-1",
            (tid,)).fetchone()["n"]
        if group_pending == 0:
            conn.close()
            _finalize_group_stage(dict(t))
            return
        conn.close()
        return

    if status != "ko":
        conn.close()
        return

    # [2026-07 재설계, KO 셸 사전생성에 맞춰 재작성] 이제 R16~결승/3-4위전
    # 전 스테이지가 대회 시작 시점에 이미 placeholder(home_team_id=0)로
    # 존재한다 — 예전처럼 "stage!='group'인 미완료 행이 있으면 할 게
    # 없다"고 판단하면 대회 내내 이 조건이 항상 참이 돼서 다음 라운드
    # 대진이 영원히 안 채워진다(intl_engine.py의 동일 버그와 같은 원인).
    # _STAGE_ORDER를 순서대로 훑으며 "채워졌는지(home_team_id!=0)"와
    # "다 끝났는지(home_score!=-1)"를 직접 확인한다.
    def _stage_rows(stage):
        return conn.execute(
            "SELECT home_team_id, home_score FROM cwc_matches WHERE tournament_id=? AND stage=?",
            (tid, stage)).fetchall()

    for i, cur_stage in enumerate(_STAGE_ORDER[:-1]):
        rows = _stage_rows(cur_stage)
        if not rows:
            continue
        if any(r["home_team_id"] == 0 for r in rows):
            conn.close()
            return
        if any(r["home_score"] == -1 for r in rows):
            conn.close()
            return
        nxt = _STAGE_ORDER[i + 1]
        next_rows = _stage_rows(nxt)
        if next_rows and all(r["home_team_id"] == 0 for r in next_rows):
            conn.close()
            _advance_round(dict(t), cur_stage)
            return

    f_rows = _stage_rows("F")
    tp_rows = _stage_rows("TP")
    conn.close()
    f_done = bool(f_rows) and all(r["home_team_id"] != 0 and r["home_score"] != -1 for r in f_rows)
    tp_done = (not tp_rows) or all(r["home_team_id"] != 0 and r["home_score"] != -1 for r in tp_rows)
    if f_done and tp_done:
        _finish_tournament(dict(t))


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
    qualifier_ids = [q["team_id"] for q in qualifiers]
    # [2026-07 버그수정, 신민용 리포트: "클럽 월드컵 탈락했는데 다음주에
    # 16강/8강 일정이 뜬다"] cwc_entries.alive가 생성 시 기본값(1)에서
    # 한 번도 갱신되지 않아, 조별리그 탈락(3·4위)팀도 계속 "생존"으로
    # 남아있었다 — get_my_pending_stage가 이 값을 못 믿고 "마지막으로
    # 뛴 토너먼트 경기에서 졌는가"로 대신 판정했는데, 조별 탈락자는
    # 애초에 토너먼트(KO) 경기 자체가 없어 그 판정도 통과 못 하고 그냥
    # 화면에 남아있는 아무 미정 placeholder를 내 경기인 것처럼 보여줬다.
    # 여기서 진출 실패팀의 alive를 0으로 확실히 내린다.
    if qualifier_ids:
        placeholders = ",".join("?" * len(qualifier_ids))
        conn.execute(
            f"UPDATE cwc_entries SET alive=0 WHERE tournament_id=? AND team_id NOT IN ({placeholders})",
            (t["id"], *qualifier_ids))
    else:
        conn.execute("UPDATE cwc_entries SET alive=0 WHERE tournament_id=?", (t["id"],))
    conn.execute("UPDATE cwc_tournaments SET status='ko' WHERE id=?", (t["id"],))
    conn.commit()
    _build_knockout(conn, t["id"], qualifier_ids)
    conn.commit()
    conn.close()
    add_log(f"⚽ {t['year']}년 클럽 월드컵 조별리그 종료 — 16강 진출 16팀 확정", "event")


def _build_knockout(conn, tid, qualifier_ids):
    """16팀을 시드 순서(조 1위끼리/2위끼리 안 붙게 스네이크)로 16강 대진.
    [2026-07 재설계] 새 행을 INSERT하는 대신, 대회 생성 시점에
    _precreate_cwc_ko_shell이 미리 만들어둔 R16 placeholder를 slot
    번호로 채운다("경기는 미리 존재, 참가팀만 나중에 확정")."""
    random.shuffle(qualifier_ids)   # 조 추첨식 랜덤 매칭(간단화)
    fills = {}
    for slot, i in enumerate(range(0, 16, 2)):
        fills[slot] = (qualifier_ids[i], qualifier_ids[i + 1], 0)
    _fill_cwc_ko_shell(conn, tid, "R16", fills)


def _round_winner(m):
    if m["home_score"] > m["away_score"]:
        return m["home_team_id"]
    if m["away_score"] > m["home_score"]:
        return m["away_team_id"]
    return m["pso_winner"]   # 동점이면 PSO로 이미 승자 기록됨


def _advance_round(t, cur_stage):
    conn = get_conn()
    matches = conn.execute(
        "SELECT * FROM cwc_matches WHERE tournament_id=? AND stage=? ORDER BY slot",
        (t["id"], cur_stage)).fetchall()
    winners = [_round_winner(dict(m)) for m in matches]
    # [2026-07 버그수정] 조별리그 탈락뿐 아니라 16강/8강/4강에서 져도
    # 그 즉시 alive=0으로 내려야, 다음 라운드 placeholder가 아직
    # my_pending_stage에 잘못 걸리는 일이 없다(last_ko 판정에만 기대지
    # 않고 alive 플래그로 한 번 더 확실히 고정).
    losers_this_round = [m["away_team_id"] if _round_winner(dict(m)) == m["home_team_id"] else m["home_team_id"]
                          for m in matches]
    if losers_this_round:
        placeholders = ",".join("?" * len(losers_this_round))
        conn.execute(
            f"UPDATE cwc_entries SET alive=0 WHERE tournament_id=? AND team_id IN ({placeholders})",
            (t["id"], *losers_this_round))
    nxt = _STAGE_ORDER[_STAGE_ORDER.index(cur_stage) + 1]
    # [2026-07 재설계] day/week는 더 이상 여기서 계산하지 않는다 —
    # _precreate_cwc_ko_shell이 대회 생성 시점에 이미 배정해뒀다. 여기서는
    # 승자 팀 ID만 slot 번호로 매칭해 placeholder를 채운다.
    fills = {}
    for slot, i in enumerate(range(0, len(winners), 2)):
        fills[slot] = (winners[i], winners[i + 1], 0)
    _fill_cwc_ko_shell(conn, t["id"], nxt, fills)
    # [2026-07 버그수정, 신민용 리포트: "결승전은 상대가 미리 보이는데
    # 3/4위전은 경기 끝나야 나온다"] 예전엔 TP 매치 생성을 _finish_tournament
    # (F/TP를 곧바로 시뮬하는 함수) 진입 시점까지 미뤄서, TP 행이 DB에
    # 생기는 순간 이미 결과까지 같이 확정돼버렸다 — 그래서 대진표에
    # "매치업은 정해졌지만 아직 안 뛴" 상태가 존재할 틈이 없었다.
    # champions_engine.py(챔스)는 SF가 끝나는 즉시 F와 TP를 둘 다
    # -1,-1(미완료) 매치로 만들어둬서 그 사이에 대진표가 미리 보인다 —
    # 여기도 SF→F 전환 시점에 TP를 똑같이 즉시 채운다(패자끼리).
    if cur_stage == "SF":
        losers = [m["away_team_id"] if _round_winner(dict(m)) == m["home_team_id"] else m["home_team_id"]
                  for m in matches]
        if len(losers) == 2:
            _fill_cwc_ko_shell(conn, t["id"], "TP", {999: (losers[0], losers[1], 0)})
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
    # [2026-07 버그수정] TP는 이제 _advance_round(SF→F 전환 시점)에서
    # 이미 채워져 있는 게 정상 경로다 — 여기선 방어적으로만(과거 세이브
    # 호환 등 예외 상황 대비) 아직 안 채워져 있으면(팀ID가 placeholder인
    # 0) 채운다. [2026-07 재설계] TP 행 자체는 대회 생성 시점에
    # _precreate_cwc_ko_shell이 이미 만들어뒀으므로 "행이 없으면"이 아니라
    # "아직 안 채워졌으면"으로 조건을 바꿨다.
    sf = conn.execute("SELECT * FROM cwc_matches WHERE tournament_id=? AND stage='SF' ORDER BY slot",
                       (t["id"],)).fetchall()
    tp_row = conn.execute(
        "SELECT * FROM cwc_matches WHERE tournament_id=? AND stage='TP'", (t["id"],)).fetchone()
    if sf and (not tp_row or tp_row["home_team_id"] == 0):
        losers = [m["away_team_id"] if _round_winner(dict(m)) == m["home_team_id"] else m["home_team_id"]
                  for m in sf]
        _fill_cwc_ko_shell(conn, t["id"], "TP", {999: (losers[0], losers[1], 0)})
        conn.commit()

    # 결승/3-4위전 중 아직 안 뛴 것만 시뮬 (멱등 — is_my 경기는 이미
    # simulate_my_cwc_match가 앞서 채워놨을 수 있으므로 그건 건드리지 않음)
    # [2026-07 안전장치] home_team_id=0(아직 안 채워진 placeholder)은
    # 시뮬 대상에서 제외 — team_id 0으로 cwc_entries를 조회하면 크래시.
    for m in conn.execute("SELECT * FROM cwc_matches WHERE tournament_id=? AND stage IN ('F','TP') "
                           "AND home_score=-1 AND home_team_id!=0", (t["id"],)).fetchall():
        _sim_one(conn, dict(m))
    conn.commit()

    final = conn.execute("SELECT * FROM cwc_matches WHERE tournament_id=? AND stage='F'", (t["id"],)).fetchone()
    winner_id = _round_winner(dict(final))
    conn.execute("UPDATE cwc_tournaments SET status='done', winner_team_id=? WHERE id=?",
                 (winner_id, t["id"]))
    # [2026-08 신설, 신민용 확정: club_momentum 확장] 클럽월드컵(4년 주기,
    # "세계 최강 클럽" 타이틀) 우승팀에도 momentum을 건다 — 챔스보다도 조금
    # 더 강한 cwc_champion 스케줄(constants.MOMENTUM_SCHEDULES)을 쓴다.
    from constants import MOMENTUM_START_BY_TYPE
    conn.execute("UPDATE teams SET momentum_type=?, momentum_seasons_left=? WHERE id=?",
                 ("cwc_champion", MOMENTUM_START_BY_TYPE["cwc_champion"], winner_id))

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
        """SELECT m.*, t.year AS t_year, t.name AS comp, t.my_team_id AS t_my_tid
           FROM cwc_matches m
           JOIN cwc_tournaments t ON m.tournament_id = t.id
           -- [2026-07 재수정, 신민용 지적: "다친 게 아니라 그냥
           -- 벤치라 안 뛴 경기도 있는데 그건 빠진다"] my_played=1
           -- 이거나 absence_reason이 있는 것만 걸렀더니, "건강한데
           -- 로테이션으로 그냥 안 뛴" 경기(my_played=0이면서
           -- absence_reason도 NULL)가 통째로 빠졌다 — 그 팀 소속으로
           -- 치러진 경기는 전부 보여주고(결과가 난 것만), 뛰었는지
           -- 안 뛰었는지는 화면에서 my_played/absence_reason으로
           -- 구분한다.
           WHERE m.is_my = 1 AND m.home_score >= 0
           ORDER BY t.year, m.week""").fetchall()]
    # [2026-08 성능 수정, 신민용 리포트: "재능 좋은 선수로 오래 뛰면
    # 은퇴/커리어창이 심하게 렉걸린다"] cwc_entries 전체 대신 내 경기가
    # 걸쳐있는 tournament_id만 걸러서 가져온다.
    _tids = {r["tournament_id"] for r in rows}
    names = {}
    if _tids:
        _ph = ",".join("?" * len(_tids))
        names = {(r["tournament_id"], r["team_id"]): (r["team_name"], r["country"])
                 for r in conn.execute(
                     f"SELECT tournament_id, team_id, team_name, country "
                     f"FROM cwc_entries WHERE tournament_id IN ({_ph})",
                     tuple(_tids)).fetchall()}
    conn.close()

    out = []

    stage_ko = {"group": "조별리그", "R16": "16강", "QF": "8강", "SF": "4강",
                "F": "결승", "TP": "3/4위전"}

    for m in rows:
        # [2026-07 버그수정, champions_engine.get_my_cl_matches와 동일한
        # 버그 발견/수정] "현재" 소속팀 대신 cwc_tournaments.my_team_id
        # (그 대회 시작 시점에 고정 저장된 내 팀)를 쓴다 — 안 그러면 그
        # 이후 이적한 경우 과거 경기의 상대가 그때의 내 팀 이름으로
        # 뒤바뀌어 표시되고 스코어/승패도 뒤집힌다.
        my_tid = m["t_my_tid"]
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
            "my_played": m.get("my_played", 0),
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


def get_my_cwc_match(week: int, day=None, p=None, st=None):
    """이번 주차(또는 특정 day)에 내가 뛸 클럽월드컵 경기가 있으면 dict, 없으면 None.
    champions_engine.get_my_cl_match와 동일한 반환 형식(키 이름까지)으로
    맞췄다 — game_engine.py의 im/cm 패턴에 그대로 cw로 끼워넣기 위함.

    [2026-07 최적화, 신민용 리포트: "일 단위 전환 후 전체적으로 렉"] p를
    넘기면 get_player() 재조회를 생략한다."""
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if st is None:
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
    if day is not None:
        m = conn.execute(
            """SELECT * FROM cwc_matches
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?) AND (day=? OR day IS NULL)""",
            (t["id"], week, tid, tid, day)).fetchone()
    else:
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


def get_my_pending_stage(week, day=None, p=None, st=None):
    """[2026-07 신설] intl_engine.get_my_pending_stage와 동일한 목적 —
    클럽월드컵도 이제 대회 시작 시점에 16강~결승/3-4위전 전체가
    placeholder(home_team_id=0)로 미리 생성돼 있다
    (_precreate_cwc_ko_shell 참고). get_my_cwc_match는 내 팀이 실제로
    배정된 행만 찾으므로, 아직 대진이 안 정해진 날엔 이 함수가 "미정"
    표시용 정보를 대신 돌려준다.

    탈락 판정: [2026-07 버그수정, 신민용 리포트: "탈락했는데 다음주에
    16강/8강 일정이 뜬다"] 예전엔 cwc_entries.alive를 아무도 갱신 안 해서
    (항상 기본값 1) 못 믿는다고 보고 "내 팀이 참여한 가장 최근 완료된
    KO 경기에서 졌는가"로만 판정했는데, 조별리그에서 탈락(3·4위)한
    팀은 애초에 KO 경기 자체가 없어(last_ko가 None) 이 판정을 그냥
    통과해버렸다 — 그 결과 화면엔 나와 무관한 남의 미정 경기가 계속
    "내 다음 일정"으로 떴다. 지금은 _finalize_group_stage/_advance_round
    양쪽에서 탈락 즉시 alive=0을 확실히 내려주므로, alive를 1차
    판정으로 쓰고 last_ko는 방어적 보조 판정으로만 남긴다.

    [2026-07 최적화] p를 넘기면 get_player() 재조회를 생략한다."""
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if st is None:
        st = get_state()
    if not p or not st or day is None:
        return None
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    conn = get_conn()
    t = conn.execute(
        "SELECT * FROM cwc_tournaments WHERE year=? AND status!='done'",
        (st["current_year"],)).fetchone()
    if not t or t["my_team_id"] != tid:
        conn.close()
        return None
    alive_row = conn.execute(
        "SELECT alive FROM cwc_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], tid)).fetchone()
    if alive_row and alive_row["alive"] == 0:
        conn.close()
        return None   # 탈락 확정(조별리그 미진출 포함) — 더 표시 안 함
    # [2026-07 버그수정, 구버전 세이브 호환] 이 수정 이전에 생성된
    # 세이브는 alive가 예전 방식대로 계속 1로 남아있을 수 있으므로,
    # alive만 믿지 않고 한 번 더 직접 확인한다: 조별리그가 이미
    # 끝났는데(status != 'group') 내 팀이 R16 매치 어디에도(홈/어웨이)
    # 안 나온다면 애초에 진출을 못 한 것 — 조별 탈락으로 간주한다.
    if t["status"] != "group":
        in_r16 = conn.execute(
            """SELECT 1 FROM cwc_matches WHERE tournament_id=? AND stage='R16'
               AND (home_team_id=? OR away_team_id=?) LIMIT 1""",
            (t["id"], tid, tid)).fetchone()
        if not in_r16:
            conn.close()
            return None   # 16강 명단에 없음 = 조별리그 탈락, 더 표시 안 함
    last_ko = conn.execute(
        """SELECT * FROM cwc_matches WHERE tournament_id=? AND stage!='group'
           AND home_score!=-1 AND (home_team_id=? OR away_team_id=?)
           ORDER BY day DESC LIMIT 1""",
        (t["id"], tid, tid)).fetchone()
    if last_ko and _round_winner(dict(last_ko)) != tid:
        conn.close()
        return None   # 가장 최근 KO 경기에서 짐 = 탈락, 더 표시 안 함
    m = conn.execute(
        """SELECT * FROM cwc_matches WHERE tournament_id=? AND day=?
           AND stage!='group' AND (home_team_id=0 OR away_team_id=0) LIMIT 1""",
        (t["id"], day)).fetchone()
    if m and m["stage"] in ("F", "TP"):
        # 결승/3-4위전은 서로 배타적 — intl_engine과 동일 이유.
        other_stage = "TP" if m["stage"] == "F" else "F"
        other_row = conn.execute(
            "SELECT home_team_id, away_team_id FROM cwc_matches WHERE tournament_id=? AND stage=?",
            (t["id"], other_stage)).fetchone()
        if other_row and tid in (other_row["home_team_id"], other_row["away_team_id"]):
            conn.close()
            return None
    conn.close()
    if not m:
        return None
    stage_ko = {"R16": "16강", "QF": "8강", "SF": "4강",
                "F": "결승", "TP": "3/4위전"}.get(m["stage"], m["stage"])
    return {
        "cwc": True,
        "pending": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": "클럽 월드컵",
        "stage": m["stage"],
        "stage_ko": stage_ko,
        "week": week,
    }


def sim_my_cwc_match_as_ai(week, p, reason="injury", day=None):
    """부상 등으로 내가 못 뛸 때 내 클럽월드컵 경기를 AI끼리 시뮬 —
    champions_engine.sim_my_cl_match_as_ai와 동일한 이유(안 하면 대회
    진행이 멈춤).

    [2026-07 버그수정, 신민용 리포트: "부상으로 경기 못 나갔는데 감독관계가
    그대로다"] game_engine._sim_my_team_match_as_ai와 동일한 이유로,
    이 CWC AI-대체 경로도 결장 페널티(manager_relation -1)를 적용한다."""
    info = get_my_cwc_match(week, day=day)
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
    from game_engine import update_player, _calc_manager_rel
    update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False, not_played_penalty=2))


def simulate_my_cwc_match(week, p, day=None):
    """내가 직접 뛰는 클럽월드컵 경기 — champions_engine.simulate_my_cl_match와
    동일한 패턴(개인 스탯 반영 + 클릭 가능한 매치 로그)을 그대로 따른다."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _roll_red_card, _apply_red_card_dismissal)
    info = get_my_cwc_match(week, day=day)
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