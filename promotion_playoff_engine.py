# promotion_playoff_engine.py — 승강 플레이오프 실행기 (2단계)
#
# [2026-07 신설] promotion_playoff.py가 정의한 PLAYOFF_RULE(Match-DAG
# 템플릿)을 실제로 돌린다. 흐름은 지금까지 합의한 그대로:
#
#   game_engine._process_promotion_relegation (43주)
#       → po_pending_slots에 PO 대상 팀 기록(자동 이동분은 그 자리에서 즉시 처리)
#   start_promotion_playoffs (44주 PLAYOFF_WEEK 진입)
#       → 나라·경계별로 po_pending_slots를 모아 PLAYOFF_RULE 인스턴스화
#       → po_tournaments/po_matches 셸 생성(월/수/금/일 요일 고정)
#   process_po_week (44주 동안 매일 호출, intl_engine.process_intl_week와
#       동일한 멱등 패턴)
#       → 그날 온 AI 매치 시뮬 → winner를 다음 match에 채워넣기
#       → boundary(승강 결정) match가 끝나면 그 자리에서 즉시 리그 이동 확정
#
# [범위 안내] 이번 단계는 AI-vs-AI 시뮬레이션까지만 구현한다. 플레이어
# 본인 팀이 PO에 걸렸을 때 "직접 뛰는 경기"로 전환하는 것과, 경기 일정
# 화면/커리어·은퇴창 표시는 4단계(플레이어 경기 연동)에서 이어서 만든다
# — 이 파일의 는 그 4단계가 그대로 얹힐 수 있도록 po_matches에 is_my/
# my_played 등 컬럼을 이미 마련해뒀다(club_world_cup_engine.cwc_matches와
# 동일한 필드 구성).

import promotion_playoff as pp
from competition.champions_engine import _match_outcome, _resolve_pso
from constants import PLAYOFF_WEEK, week_to_day, day_to_week

# 요일 고정 패턴: 월/수/금/일 (PLAYOFF_WEEK 첫날 기준 +0/+2/+4/+6일).
# round(위상정렬 깊이)가 이 리스트의 인덱스가 된다 — 라운드가 이 길이를
# 넘는 룰은(지금 프리셋은 최대 1라운드뿐이라 해당 없음) 로드 시점
# validate_playoff_rule을 통과했더라도 여기서 IndexError로 걸러진다.
PLAYOFF_MATCH_DAYS = [0, 2, 4, 6]


def _team_ovr(conn, team_id: int) -> float:
    row = conn.execute(
        "SELECT AVG(ovr) as avg_ovr FROM ai_players WHERE team_id=?", (team_id,)).fetchone()
    return row["avg_ovr"] if row and row["avg_ovr"] is not None else 50.0


def _team_name(conn, team_id: int) -> str:
    row = conn.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
    return row["name"] if row else ""


def _get_field_pos_safe(p):
    from game_engine import get_field_pos
    return get_field_pos(p)


# ─────────────────────────────────────────────
# 44주 진입: 셸 생성
# ─────────────────────────────────────────────
def start_promotion_playoffs(year: int) -> None:
    """PLAYOFF_WEEK(44주) 진입 시 1회 호출. po_pending_slots를 읽어 나라·경계별로
    PLAYOFF_RULE을 인스턴스화하고 po_tournaments/po_matches 셸을 만든다."""
    from database import get_conn
    from game_engine import get_player

    conn = get_conn()
    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    pending = conn.execute(
        "SELECT * FROM po_pending_slots WHERE year=?", (year,)).fetchall()
    if not pending:
        conn.close()
        return

    groups: dict = {}
    for r in pending:
        key = (r["upper_league_id"], r["lower_league_id"], r["rule_id"])
        groups.setdefault(key, []).append(r)

    base_day = week_to_day(PLAYOFF_WEEK)
    n_created = 0

    for (upper_lid, lower_lid, rule_id), rows in groups.items():
        rule = pp.PLAYOFF_RULES.get(rule_id)
        if not rule:
            continue  # 알 수 없는 rule_id — 데이터 오염 방지용 방어, 정상 흐름에선 안 걸림

        slot_map = {}   # (side, offset) -> {"team_id":.., "team_name":..}
        for r in rows:
            slot_map[(r["side"], r["offset_idx"])] = {
                "team_id": r["team_id"], "team_name": r["team_name"]}

        my_in = any(v["team_id"] == my_tid for v in slot_map.values())
        cur = conn.execute(
            """INSERT INTO po_tournaments(year, upper_league_id, lower_league_id, rule_id,
                                           status, my_in, my_team_id)
               VALUES(?,?,?,?,?,?,?)""",
            (year, upper_lid, lower_lid, rule_id, "pending",
             1 if my_in else 0, my_tid if my_in else 0))
        tid = cur.lastrowid
        n_created += 1

        by_id, order, origin, rounds, referenced_by = pp._resolve_origin_and_rounds(rule_id, rule)

        for mid in order:
            m = by_id[mid]
            day = base_day + PLAYOFF_MATCH_DAYS[rounds[mid]]

            def _resolve_side(field):
                src = m[field]
                if src["type"] == "standing":
                    slot = slot_map.get((src["side"], src["offset"]))
                    return slot["team_id"] if slot else 0
                return 0   # winner 참조는 아직 안 끝났으니 0(placeholder)로 두고 매일 갱신 때 채움

            home_tid = _resolve_side("home")
            away_tid = _resolve_side("away")
            is_my = 1 if my_tid in (home_tid, away_tid) else 0

            conn.execute(
                """INSERT INTO po_matches(tournament_id, match_key, day,
                                           home_team_id, away_team_id, is_boundary, is_my)
                   VALUES(?,?,?,?,?,?,?)""",
                (tid, mid, day, home_tid, away_tid,
                 1 if origin[mid] == "boundary" else 0, is_my))

    conn.execute("DELETE FROM po_pending_slots WHERE year=?", (year,))
    conn.commit()
    conn.close()
    if n_created:
        from game_engine import add_log
        add_log(f"⚖ {year}년 승강 플레이오프 대진 확정 — {n_created}개 경계", "event", year, PLAYOFF_WEEK)


# ─────────────────────────────────────────────
# 매일 호출: 진행
# ─────────────────────────────────────────────
def _sim_one(conn, m) -> None:
    from game_engine import _gen_score
    h_ovr = _team_ovr(conn, m["home_team_id"])
    a_ovr = _team_ovr(conn, m["away_team_id"])
    outcome = _match_outcome(h_ovr, a_ovr)
    if outcome == "draw":
        # PO는 전부 단판(KO) 성격이라 무승부는 항상 승부차기로 간다 —
        # intl_engine/club_world_cup_engine의 KO 스테이지와 동일한 처리.
        winner_home, pso = _resolve_pso(h_ovr, a_ovr)
        conn.execute(
            """UPDATE po_matches SET home_score=?, away_score=?,
               pso_winner=?, pso_score=? WHERE id=?""",
            (1, 1, m["home_team_id"] if winner_home else m["away_team_id"], pso, m["id"]))
        return
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)
    conn.execute("UPDATE po_matches SET home_score=?, away_score=? WHERE id=?",
                 (hs, as_, m["id"]))


def _winner_of(m) -> int:
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home_team_id"] if m["home_score"] > m["away_score"] else m["away_team_id"]


def _finalize_boundary_match(conn, t, m, year: int) -> None:
    """boundary(승강 결정) match 하나가 끝났을 때, 실제 승격/강등을 확정한다.
    승자는 upper_league_id로, 패자는 lower_league_id로 — 이게 승강 PO의
    정의 그 자체라 이 방향은 항상 고정이다(춘계 회의에서 정리된 대로,
    match 자체가 이미 upper 후보 vs lower 후보로만 구성돼 있으므로 별도
    "어느 쪽이 위인지" 메타데이터가 필요 없다)."""
    from game_engine import get_player, update_player, add_log, _invalidate_team_ovr_cache

    winner = _winner_of(m)
    loser = m["away_team_id"] if winner == m["home_team_id"] else m["home_team_id"]
    upper_lid, lower_lid = t["upper_league_id"], t["lower_league_id"]

    upper_row = conn.execute("SELECT tier FROM leagues WHERE id=?", (upper_lid,)).fetchone()
    lower_row = conn.execute("SELECT tier FROM leagues WHERE id=?", (lower_lid,)).fetchone()
    upper_tier = upper_row["tier"] if upper_row else 0
    lower_tier = lower_row["tier"] if lower_row else 0

    winner_name = _team_name(conn, winner)
    loser_name = _team_name(conn, loser)
    upper_lname = conn.execute("SELECT name FROM leagues WHERE id=?", (upper_lid,)).fetchone()
    lower_lname = conn.execute("SELECT name FROM leagues WHERE id=?", (lower_lid,)).fetchone()

    # promotion_log는 "실제로 이동한" 팀만 남긴다 — 원래 upper 소속이던
    # 팀이 이겨서 그대로 upper에 남는(잔류) 경우나, 원래 lower 소속이던
    # 팀이 져서 그대로 lower에 남는(승격 실패) 경우는 "이동"이 아니므로
    # 기록하지 않는다. 참가자의 "원래 소속"은 이 함수가 teams.league_id를
    # 갱신하기 *직전*의 실제 값으로 판단한다 — po_matches 자체엔 그 정보를
    # 안 들고 있지만, 체인형이 아닌 이상(현재 프리셋 전부 그렇듯) 참가팀의
    # league_id는 PO 시작 이후 한 번도 안 바뀐 상태이므로 항상 정확하다.
    winner_prev_lid = conn.execute(
        "SELECT league_id FROM teams WHERE id=?", (winner,)).fetchone()["league_id"]
    loser_prev_lid = conn.execute(
        "SELECT league_id FROM teams WHERE id=?", (loser,)).fetchone()["league_id"]

    _log_inserts = []
    if winner_prev_lid != upper_lid:
        # lower 소속이었는데 이겨서 upper로 승격
        # [2026-07 버그수정, 실제 테스트 중 발견] promotion_log.league_name은
        # game_engine._process_promotion_relegation의 기존 관례상 "도착한
        # 리그"가 아니라 "원래 있던(떠나온) 리그"다 — 처음엔 반대로(도착
        # 리그) 써서 world_browser.get_league_champions()가 이름 문자열로
        # 조회할 때 엉뚱한 이동으로 잡히는 걸 실제 세이브로 재현·확인한
        # 뒤 고쳤다. from_league_id/to_league_id도 같이 남겨서 이름
        # 문자열(나라 간 리그명 중복 — "프리메라 디비시온" 등)에 기대지
        # 않고 확실하게 조회할 수 있게 한다.
        winner_prev_tier = conn.execute(
            "SELECT tier FROM leagues WHERE id=?", (winner_prev_lid,)).fetchone()
        _wt = winner_prev_tier["tier"] if winner_prev_tier else lower_tier
        _log_inserts.append((year, winner_name, _wt, upper_tier,
                              lower_lname["name"] if lower_lname else "",
                              winner_prev_lid, upper_lid, winner))
    if loser_prev_lid != lower_lid:
        # upper 소속이었는데 져서 lower로 강등
        loser_prev_tier = conn.execute(
            "SELECT tier FROM leagues WHERE id=?", (loser_prev_lid,)).fetchone()
        _lt = loser_prev_tier["tier"] if loser_prev_tier else upper_tier
        _log_inserts.append((year, loser_name, _lt, lower_tier,
                              upper_lname["name"] if upper_lname else "",
                              loser_prev_lid, lower_lid, loser))
    if _log_inserts:
        conn.executemany(
            """INSERT INTO promotion_log(year,team_name,from_tier,to_tier,league_name,
                                          from_league_id,to_league_id,team_id) VALUES(?,?,?,?,?,?,?,?)""",
            _log_inserts)

    # 로그용 "원래 소속" 비교가 끝난 뒤에야 실제로 옮긴다(순서 중요 —
    # 먼저 옮기면 위 winner_prev_lid/loser_prev_lid가 이미 새 값이 되어버려
    # "이동했는지"를 판단할 수 없게 된다).
    conn.execute("UPDATE teams SET league_id=?, current_tier=? WHERE id=?",
                 (upper_lid, upper_tier, winner))
    conn.execute("UPDATE teams SET league_id=?, current_tier=? WHERE id=?",
                 (lower_lid, lower_tier, loser))
    conn.commit()

    _invalidate_team_ovr_cache()

    # [2026-08 신설, 신민용 리포트: "산하팀이 모팀이랑 같은 티어로 남아있는게
    # 보인다"] 이 함수(플레이오프 boundary 확정)는 game_engine._process_
    # promotion_relegation()의 산하팀-모팀 tier 검증 시점(43주차)보다 항상
    # 늦게(44~52주) 일어난다 — 그래서 여기서 강등/승격된 팀이 모팀이면, 그
    # 산하팀과의 tier 충돌이 다음 시즌 검증 때까지 방치됐었다. winner/loser
    # 둘 다에 대해 그 즉시 재검증한다(둘 중 하나가 누군가의 모팀일 수
    # 있으므로 — 방향에 상관없이 안전한 방어적 호출, 대부분은 no-op).
    from game_engine import enforce_affiliate_children_tier
    enforce_affiliate_children_tier(winner, year)
    enforce_affiliate_children_tier(loser, year)

    # [2026-07 버그수정, 신민용 리포트: "우측 로그에 전 세계 승강전 결과가
    # 다 뜬다"] 예전엔 이 매치가 내 팀과 무관해도(전 세계 수백 개 경계 중
    # 하나일 뿐이어도) 무조건 add_log를 불렀다 — 다른 대회(국제전/CWC 등)가
    # AI끼리의 경기는 로그를 안 남기는 것과 같은 원칙으로, 내 팀이 이
    # 매치에 직접 걸렸을 때만 로그를 남긴다.
    p = get_player()
    if p and p.get("current_team_id") in (winner, loser):
        new_lid = upper_lid if p["current_team_id"] == winner else lower_lid
        won = (p["current_team_id"] == winner)
        add_log(f"⚖ {year}년  {winner_name if won else loser_name}  승강 플레이오프 "
                f"{'승리' if won else '패배'} — "
                f"{(upper_lname if won else lower_lname)['name'] if (upper_lname if won else lower_lname) else ''} 확정",
                "event", year, PLAYOFF_WEEK)
        update_player(current_league_id=new_lid)
        add_log("📋 소속 리그가 변경되었습니다", "event", year, PLAYOFF_WEEK)
        # [2026-07 버그수정, 신민용 리포트: "커리어에 리그가 1부로 그대로
        # 뜬다"] game_engine._process_promotion_relegation의 자동 이동
        # 경로와 동일한 이유로, 리그가 바뀌는 이 시점에 커리어 진행중
        # 항목을 즉시 동기화한다(다음 시즌 첫 경기까지 기다리지 않음).
        from game_engine import _update_career_stats
        _update_career_stats(get_player(), year, PLAYOFF_WEEK)


def process_po_week(week: int, day=None) -> None:
    """intl_engine.process_intl_week와 동일한 멱등 패턴 — 매일 호출해도
    안전하며, 그날까지 온 미완료 경기만 처리한다.

    [2026-08 최적화, 신민용 리포트: "43~44주 매일 1.4~1.7초씩 걸린다" —
    [PERF-DAILYHOOK]/[PERF-PO]로 확인됨] 활성 po_tournaments가 실측
    400~450개대(전 세계 승강 경계 수만큼)나 됐다 — 예전엔 토너먼트
    "하나마다" SELECT를 5번씩(due조회/by_key재구성/boundary조회/카운트
    2번) 날려서 하루에 2000개 넘는 쿼리가 나갔다. 1차로 commit()만
    배치했지만(4×N회→1회) 여전히 느렸던 이유가 바로 이 쿼리 개수였다.

    토너먼트끼리는 서로 다른 나라/부수 경계에 묶인 완전히 독립된
    팀 집합이라(같은 팀이 두 토너먼트에 동시에 걸릴 일이 없음), "토너먼트
    하나씩 전부 처리 후 다음 토너먼트로" 순서를 "모든 토너먼트의 1단계를
    먼저 끝내고 → 모든 토너먼트의 2단계 → ..."로 바꿔도 결과가 완전히
    동일하다 — 그 덕에 각 단계를 tournament_id IN (...) 하나의 쿼리로
    전체 토너먼트에 대해 한 번에 처리할 수 있다. 쿼리 개수가 활성
    토너먼트 수(400+)에서 상수(약 5개)로 줄어든다. 판정 로직·처리
    순서·최종 결과는 원본과 완전히 동일하다."""
    from database import get_conn
    from game_engine import get_state

    st = get_state()
    year = st["current_year"] if st else 0
    conn = get_conn()

    import time as _time_po
    _po_t0 = _time_po.perf_counter()

    tournaments = conn.execute(
        "SELECT * FROM po_tournaments WHERE year=? AND status!='done' ORDER BY id", (year,)).fetchall()
    if not tournaments:
        conn.close()
        return

    t_ids = [t["id"] for t in tournaments]
    t_by_id = {t["id"]: t for t in tournaments}
    ph = ",".join("?" * len(t_ids))

    # 1) 오늘까지 도래한 미완료 경기 — 전체 활성 토너먼트를 한 번에 조회
    # [2026-08 버그수정, 재현성 문제 추적 중 발견] ORDER BY 없이 조회한
    # 순서 그대로 _sim_one()을 돌리면(_sim_one이 내부에서 random을 씀)
    # SQLite가 이 순서를 보장 안 해줘서 동일 seed로도 실행마다 결과가
    # 달라지는 원인이 됐다 — id 순으로 고정해서 매 실행 동일한 순서로
    # random을 소비하게 만든다.
    if day is not None:
        due_rows = conn.execute(
            f"""SELECT * FROM po_matches WHERE tournament_id IN ({ph})
                AND home_score=-1 AND home_team_id!=0 AND away_team_id!=0 AND day<=?
                ORDER BY id""",
            (*t_ids, day)).fetchall()
    else:
        due_rows = conn.execute(
            f"""SELECT * FROM po_matches WHERE tournament_id IN ({ph})
                AND home_score=-1 AND home_team_id!=0 AND away_team_id!=0
                ORDER BY id""",
            t_ids).fetchall()
    for m in due_rows:
        _sim_one(conn, dict(m))

    # 2) 방금 끝난 경기들의 승자를 다음 match의 placeholder(0)에 채운다 —
    #    전체 토너먼트의 po_matches를 한 번에 조회해서(방금 시뮬 결과까지
    #    반영됨) 토너먼트별로 묶은 뒤 메모리에서 처리한다.
    all_matches = conn.execute(
        f"SELECT * FROM po_matches WHERE tournament_id IN ({ph})", t_ids).fetchall()
    matches_by_tid: dict = {}
    for r in all_matches:
        matches_by_tid.setdefault(r["tournament_id"], []).append(dict(r))

    for tid, rows in matches_by_tid.items():
        t = t_by_id.get(tid)
        if not t:
            continue
        rule = pp.PLAYOFF_RULES.get(t["rule_id"])
        if not rule:
            continue
        by_key = {r["match_key"]: r for r in rows}
        for m_def in rule["matches"]:
            mid = m_def["id"]
            cur = by_key.get(mid)
            if not cur:
                continue
            changed = {}
            for field in ("home", "away"):
                src = m_def[field]
                if src["type"] != "winner":
                    continue
                ref = by_key.get(src["match"])
                if not ref or ref["home_score"] == -1:
                    continue   # 참조 대상이 아직 안 끝남
                col = "home_team_id" if field == "home" else "away_team_id"
                if cur[col] == 0:
                    changed[col] = _winner_of(ref)
            if changed:
                sets = ",".join(f"{k}=?" for k in changed)
                conn.execute(f"UPDATE po_matches SET {sets} WHERE id=?",
                             (*changed.values(), cur["id"]))

    # 3) boundary(승강 결정) match가 끝났으면 즉시 승강 확정 — placeholder가
    #    막 채워져 "완료" 상태가 된 경기까지 반영하려면 2단계 이후 새로
    #    조회해야 한다. finalized 컬럼으로 같은 match를 두 번 확정하지
    #    않는다(체인형 룰이면 boundary match들이 서로 다른 날 끝날 수
    #    있어서, 다음날 다시 호출됐을 때 이미 끝난 것까지 재확정하면
    #    리그 이동/로그가 중복된다).
    boundary_rows = conn.execute(
        f"""SELECT * FROM po_matches WHERE tournament_id IN ({ph})
            AND is_boundary=1 AND home_score!=-1 AND finalized=0 ORDER BY id""", t_ids).fetchall()
    for m in boundary_rows:
        t = t_by_id.get(m["tournament_id"])
        if not t:
            continue
        _finalize_boundary_match(conn, t, dict(m), year)
        conn.execute("UPDATE po_matches SET finalized=1 WHERE id=?", (m["id"],))

    # 4) 토너먼트별 "boundary 전부 확정됐는지" — 그룹집계 쿼리 1번으로
    #    전체 토너먼트를 한 번에 판정(예전엔 토너먼트마다 COUNT 2번씩).
    status_rows = conn.execute(
        f"""SELECT tournament_id,
                   SUM(CASE WHEN is_boundary=1 THEN 1 ELSE 0 END) AS total,
                   SUM(CASE WHEN is_boundary=1 AND finalized=1 THEN 1 ELSE 0 END) AS done
            FROM po_matches WHERE tournament_id IN ({ph})
            GROUP BY tournament_id""", t_ids).fetchall()
    done_tids = [r["tournament_id"] for r in status_rows
                 if r["total"] and r["total"] == r["done"]]
    if done_tids:
        conn.executemany(
            "UPDATE po_tournaments SET status='done' WHERE id=?",
            [(tid,) for tid in done_tids])
        # [2026-08 신설] 이번 처리에서 새로 끝난 경계가 있으면, "매치 처리
        # 순서" 문제로 되돌려진 산하팀-모팀 충돌이 없는지 한 번 더 훑는다
        # (enforce_affiliate_children_tier 개별 hook 참고 — sweep_all_
        # affiliate_conflicts 문서 참조).
        from game_engine import sweep_all_affiliate_conflicts
        sweep_all_affiliate_conflicts(year)

    conn.commit()
    conn.close()
    _po_total = _time_po.perf_counter() - _po_t0
    if _po_total >= 0.05:
        print(f"[PERF-PO]  process_po_week({week}주차, {len(tournaments)}개 활성토너먼트) "
              f"{_po_total:.3f}s")


# ─────────────────────────────────────────────
# 4단계: 플레이어 본인 팀이 PO에 걸렸을 때
# ─────────────────────────────────────────────
def get_my_po_match(week: int, day=None, p=None, st=None):
    """이번 주차(또는 특정 day)에 내가 뛸 승강 플레이오프 경기가 있으면 dict,
    없으면 None. club_world_cup_engine.get_my_cwc_match와 동일한 반환 형식
    (키 이름까지) — game_engine.py의 im/cm/cw 패턴에 그대로 po로 끼워넣기
    위함. p/st를 넘기면 재조회를 생략한다(2026-07 최적화 세션과 동일 이유).

    [2026-08 버그수정, 신민용 리포트: "경기 당일에도 UI엔 '?'가 뜨는데
    뒤에서는(po_history 기록상) 결과가 정상 처리됐다"] 원인: 이 함수는
    원래 po_tournaments.my_team_id(토너먼트 "생성 시점"에 한 번 캡처해
    박아둔 캐시값)로 "내 토너먼트"를 걸러냈다. 그런데 start_promotion_
    playoffs()가 무거운 연도전환 처리와 같은 백그라운드 스레드에서 도는
    시점이라, 그 순간 get_player()가 아직 최신 상태를 못 읽어서
    my_team_id가 잘못(0 등) 캐싱될 수 있었다 — 실제 세이브에서
    my_team_id=0으로 찍힌 완료된 토너먼트를 확인함. 반면 실제 경기
    시뮬레이션(simulate_my_po_match)은 매번 최신 current_team_id를 새로
    조회해서 우연히 맞았고, 그래서 "뒤에서는 처리되는데 화면은 계속
    '?'"인 불일치가 생겼다.

    수정: my_team_id라는 캐시값을 아예 안 믿는다 — 그 해의 아직 안 끝난
    토너먼트 전부를 대상으로, po_matches에 내 team_id가 실제로
    home/away로 들어있는지 직접 뒤진다. 캐시가 틀려도 절대 어긋날 수
    없는 방식이라 근본적으로 해결된다."""
    from database import get_conn
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
    if day is not None:
        m = conn.execute(
            """SELECT pm.* FROM po_matches pm
               JOIN po_tournaments pt ON pm.tournament_id = pt.id
               WHERE pt.year=? AND pt.status!='done' AND pm.home_score=-1
               AND (pm.home_team_id=? OR pm.away_team_id=?) AND pm.day=?""",
            (st["current_year"], tid, tid, day)).fetchone()
    else:
        m = conn.execute(
            """SELECT pm.* FROM po_matches pm
               JOIN po_tournaments pt ON pm.tournament_id = pt.id
               WHERE pt.year=? AND pt.status!='done' AND pm.home_score=-1
               AND (pm.home_team_id=? OR pm.away_team_id=?)""",
            (st["current_year"], tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    opp_row = conn.execute("SELECT name FROM teams WHERE id=?", (opp_id,)).fetchone()
    conn.close()
    return {
        "po": True,                       # 승강 플레이오프 경기 표시 플래그
        "match_id": m["id"],
        "tournament_id": m["tournament_id"],
        "league_name": "승강 플레이오프",
        "stage_ko": "결정전",
        "opp": opp_row["name"] if opp_row else "?",
        "is_home": is_home,
    }


def sim_my_po_match_as_ai(week, p, reason="injury", day=None):
    """부상 등으로 내가 못 뛸 때 내 PO 경기를 AI끼리 시뮬 —
    club_world_cup_engine.sim_my_cwc_match_as_ai와 동일한 이유(안 하면
    승강 확정 자체가 멈춤 — PO는 단판이라 이 경기가 곧 boundary 매치임).

    [2026-07 버그수정, 신민용 리포트: "부상으로 결장했는데 다른 대회처럼
    (부상) 표시로도 기록이 안 남는다 — 아예 커리어에서 사라진다"] 예전엔
    _sim_one만 부르고 끝나서 po_history에 아무 것도 안 남았다 —
    simulate_my_po_match(직접 뛸 때)와 마찬가지로 결장 사유와 함께
    po_history에 기록을 남긴다(cup_matches.my_absence_reason과 동일한
    패턴, get_my_po_matches()가 그대로 읽어서 career_window.py의
    _absence_override가 "(부상)"으로 표시해준다)."""
    info = get_my_po_match(week, day=day, p=p)
    if not info:
        return
    from database import get_conn
    conn = get_conn()
    m = conn.execute("SELECT * FROM po_matches WHERE id=?", (info["match_id"],)).fetchone()
    if not m or m["home_score"] != -1:
        conn.close()
        return
    _sim_one(conn, dict(m))
    my_tid = p.get("current_team_id", 0)
    is_home = info["is_home"]
    my_team_name = _team_name(conn, my_tid)
    opp_name = info["opp"]
    conn.commit()
    conn.close()
    from game_engine import update_player, _calc_manager_rel
    update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False, not_played_penalty=2))

    conn2 = get_conn()
    conn2.execute(
        """INSERT INTO po_history(year, team_name, opp_name, result, goals, assists, rating, absence_reason)
           VALUES(?,?,?,?,?,?,?,?)""",
        (st_year_of(week), my_team_name, opp_name, "", 0, 0, 0.0, reason))
    conn2.commit()
    conn2.close()


def st_year_of(week):
    """po_history 기록용 — 현재 연도를 가져오는 짧은 헬퍼."""
    from game_engine import get_state
    return get_state()["current_year"]


def simulate_my_po_match(week, p, day=None):
    """내가 직접 뛰는 승강 플레이오프 경기 — club_world_cup_engine.
    simulate_my_cwc_match와 동일한 패턴(개인 스탯 반영 + 클릭 가능한 매치
    로그 + po_history 커리어 기록). po_matches는 cwc_matches보다 얕은
    스탯 컬럼만 갖고 있어서(슛/드리블 등 세부 컬럼 없음 — intl_matches와
    동일한 수준) 그 부분만 덜어냈고, 나머지 흐름은 동일하다."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _roll_red_card, _apply_red_card_dismissal)
    from database import get_conn
    info = get_my_po_match(week, day=day, p=p)
    if not info:
        return
    conn = get_conn()
    m = dict(conn.execute("SELECT * FROM po_matches WHERE id=?", (info["match_id"],)).fetchone())
    is_home = info["is_home"]
    my_tid = p.get("current_team_id", 0)
    opp_tid = m["away_team_id"] if is_home else m["home_team_id"]
    my_ovr_team = _team_ovr(conn, my_tid)
    opp_ovr_team = _team_ovr(conn, opp_tid)
    conn.close()

    _suspended, _new_susp = _check_suspended(p, field="po_suspension")
    if _suspended:
        update_player(po_suspension=_new_susp)
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event")

    # 내 출전 보너스 — cwc/챔스와 완전히 동일한 공식.
    _my_ovr = p.get("ovr", 40)
    _gap = max(0.0, _my_ovr - my_ovr_team)
    _star = 1.0 + max(0.0, (_my_ovr - 60) / 40.0) ** 1.8 * 3.0
    bonus = _gap * 0.30 * _star + max(0.0, _my_ovr - 50) * 0.08
    bonus = _soft_cap(bonus, 30.0)
    from constants import PERSONALITY_EFFECTS
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if "team_win_bonus" in _pe:
        bonus *= (1.0 + _pe["team_win_bonus"])
    if _suspended:
        bonus = 0.0
    if is_home:
        h_ovr, a_ovr = my_ovr_team + bonus, opp_ovr_team
    else:
        h_ovr, a_ovr = opp_ovr_team, my_ovr_team + bonus

    outcome = _match_outcome(h_ovr, a_ovr)
    pso_winner, pso_score = 0, ""
    if outcome == "draw":
        # PO는 전부 단판(KO)이라 무승부는 항상 승부차기.
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    if _suspended:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
    else:
        _opp_ovr = a_ovr if is_home else h_ovr
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr)
        if _roll_red_card(p):
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(p, field="po_suspension")
    if not _suspended and "big_match_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["big_match_rating"], 1)))
    my_result = _my_result(outcome, is_home)

    conn = get_conn()
    conn.execute(
        """UPDATE po_matches SET home_score=?, away_score=?, pso_winner=?, pso_score=?,
           is_my=1, my_played=?, my_position=?, my_saves=?, my_goals=?, my_assists=?, my_rating=?
           WHERE id=?""",
        (hs, as_, pso_winner, pso_score,
         0 if _suspended else 1, _get_field_pos_safe(p),
         saves, goals, assists, rating, m["id"]))
    conn.commit()

    _update_pop(p, goals, assists, rating)
    p2 = get_player()
    ns = min(100, p2["stress"] + 20)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    opp_name = info["opp"]
    my_team_name = _team_name(conn, my_tid)
    conn.close()
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == my_tid else '패'})"
        rs = "무"

    home_disp = my_team_name if is_home else opp_name
    away_disp = opp_name if is_home else my_team_name
    pso = {"won": pso_winner == my_tid, "score": pso_score} if pso_winner else None
    detail_id = _save_match_detail(
        p, week, "승강 플레이오프", is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, True, False, detail, pso=pso)
    marker = f" [match:{detail_id}]" if detail_id else ""

    add_log("─" * 44, "sep")
    add_log(f"⚖ 승강 플레이오프  {week}주차{marker}", "match")
    add_log(f"   {home_disp} {hs}-{as_} {away_disp}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")

    # 커리어/은퇴창 "🌍 국제전"과 같은 톤으로 개인 PO 경기 기록을 영구 보존
    # (po_matches/po_tournaments는 매년 새로 생기고 지워질 수 있는 대회
    # 데이터라, 커리어 기록은 별도 영구 테이블에 남긴다 — cl_history와
    # 동일한 이유).
    # [2026-08 수정] detail(슈팅/유효/기회창출/드리블/차단/패스%)과
    # saves/conceded(GK용)도 함께 저장 — cl_matches/intl_matches와 같은
    # 필드셋을 갖춰야 career_window의 승강 PO 탭도 다른 대회 탭처럼
    # 스탯 컬럼을 보여줄 수 있다.
    conceded = as_ if is_home else hs
    my_score = hs if is_home else as_
    conn2 = get_conn()
    conn2.execute(
        """INSERT INTO po_history(year, team_name, opp_name, result, goals, assists, rating,
                                   shots, shots_on, key_passes, dribbles, blocks, pass_acc,
                                   saves, conceded, score)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (st_year_of(week), my_team_name, opp_name, rs, goals, assists, rating,
         detail.get("shots", 0), detail.get("shots_on", 0), detail.get("key_passes", 0),
         detail.get("dribbles", 0), detail.get("blocks", 0), detail.get("pass_acc", 0.0),
         saves, conceded, f"{my_score}-{conceded}"))
    conn2.commit()
    conn2.close()


def get_my_po_matches():
    """커리어/은퇴창 '🏆 승강 플레이오프' 탭용 — intl_engine.get_my_intl_matches
    와 동일한 목적(내가 실제 뛴 경기만, 시간순)."""
    from database import get_conn
    conn = get_conn()
    rows = conn.execute("SELECT * FROM po_history ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_my_po_tournament(team_id: int, year: int):
    """[2026-07 신설, 신민용 리포트: "경기 일정 창에 승강전 탭이 안 뜬다"]
    schedule_window.py의 다른 대회 탭들(클럽월드컵 등)과 동일한 패턴 —
    내 팀이 이번 해 승강 플레이오프에 걸렸으면(브래킷 어느 자리든, 위
    리그 대표든 아래 리그 예선 참가팀이든) 그 tournament 행 전체를
    반환한다.

    [2026-08 버그수정, 신민용 리포트: "경기 당일에도 UI엔 '?'가 뜨는데
    뒤에서는 결과가 정상 처리됐다"] po_tournaments.my_team_id는
    start_promotion_playoffs가 생성 시점에 딱 한 번 캡처해 박아두는
    캐시값인데, 이 함수가 무거운 연도전환과 같은 백그라운드 스레드에서
    도는 타이밍 때문에 get_player()가 최신 상태를 못 읽어 캐시가 잘못
    (0 등) 찍힐 수 있었다(get_my_po_match와 동일한 원인, 실제 세이브로
    확인됨). my_team_id를 믿지 않고 po_matches를 직접 뒤져서 내 팀이
    home/away 어느 쪽으로든 실제로 들어있는지 확인한다 — 캐시가 틀려도
    절대 어긋나지 않는다."""
    from database import get_conn
    conn = get_conn()
    t = conn.execute(
        """SELECT pt.* FROM po_tournaments pt
           WHERE pt.year=? AND EXISTS (
               SELECT 1 FROM po_matches pm
               WHERE pm.tournament_id = pt.id
               AND (pm.home_team_id=? OR pm.away_team_id=?)
           )""",
        (year, team_id, team_id)).fetchone()
    conn.close()
    return dict(t) if t else None


def get_po_bracket_matches(tournament_id: int):
    """그 승강 플레이오프 tournament의 전체 라운드 매치를 화면 표시용
    형태로 반환한다 — ui.bracket_widget.build_rounds_from_matches가
    기대하는 dict 형태(stage/week/home/away/hs/as_/winner/pso/my_side)로
    이미 맞춰서 준다."""
    from database import get_conn
    from constants import day_to_week
    conn = get_conn()
    t = conn.execute("SELECT * FROM po_tournaments WHERE id=?", (tournament_id,)).fetchone()
    if not t:
        conn.close()
        return []
    my_tid = t["my_team_id"]
    rows = conn.execute(
        "SELECT * FROM po_matches WHERE tournament_id=? ORDER BY id", (tournament_id,)).fetchall()
    _STAGE_KO = {"Q1": "예선", "SF1": "준결승", "SF2": "준결승",
                 "LF": "하위리그 결승", "F": "최종 승강전"}
    out = []
    for m in rows:
        home_id, away_id = m["home_team_id"], m["away_team_id"]
        hname = _team_name(conn, home_id) if home_id else "미정"
        aname = _team_name(conn, away_id) if away_id else "미정"
        hs, as_ = m["home_score"], m["away_score"]
        played = hs is not None and hs >= 0
        winner = ""
        if played:
            winner_id = m["pso_winner"] or (home_id if hs > as_ else away_id)
            winner = hname if winner_id == home_id else aname
        my_side = "home" if home_id == my_tid else ("away" if away_id == my_tid else None)
        out.append({
            "stage": _STAGE_KO.get(m["match_key"], m["match_key"]),
            "week": day_to_week(m["day"]) if m["day"] else 0,
            "home": hname, "away": aname, "home_flag": "", "away_flag": "",
            "hs": hs if played else -1, "as_": as_ if played else -1,
            "winner": winner, "pso": m["pso_score"] if m["pso_winner"] else "",
            "my_side": my_side,
        })
    conn.close()
    return out