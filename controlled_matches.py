# -*- coding: utf-8 -*-
"""controlled_matches.py — [G2 — 2026-10, 신민용 확정] 사용자가 통제하는 클럽의 경기를 체인 엔진으로.

대상 팀(신민용 확정 1)
  · 사용자 감독 팀(team_plan.user_team_ids)과 구단주 구단(club_owner.owned_team_ids) 전부.
  · 내 선수가 뛰는 팀의 경기는 G1 경로(내 경기 / 내가 결장한 내 팀 경기)가 이미 체인으로 처리하므로
    여기서 다시 처리하지 않는다 — 경기의 두 팀 중 하나라도 내 소속팀이면 G2 대상이 아니다.
  · 국가대표 경기는 대상이 아니다(옛 분 단위 엔진 그대로).
  · 대상 팀이 하나도 없으면(지금 모든 게임) 아무 일도 하지 않는다 — 난수도 쓰지 않으므로 예전과 비트 단위로 같다.

경기 처리
  · tactical_engine.simulate_my_match(me=None) 그대로 — 포메이션·선발·벤치·전술은 team_plan.build_team_plan이
    정한다(사용자 감독 팀은 사용자가 저장한 계획, 구단주 구단은 AI 감독 계획). 캐리 보정 없음.
  · 호출부(각 대회의 AI 경기 함수)는 여기서 받은 스코어를 G1의 forced 인자와 같은 방식으로 쓴다 — 팀 전적·순위·
    대회 진출·승부차기(연장 후 동점) 처리는 기존 AI 경로 그대로.
  · 리그 경기만 AI 선수 실제 골·도움(ai_player_realstat_season)을 체인 득점자로 누적한다(G1 내 리그 경기와 같은 규칙).

관전 기록(신민용 확정 2)
  · 내 선수 기준 저장(match_details)과 섞지 않고 spectator_match_details에 따로 저장한다 — match_details는 감독
    관계·커리어·은퇴 스토리가 "내 경기"로 읽는 표라 섞으면 안 된다.
  · 로그 헤더의 클릭 마커는 SPECTATOR_ID_BASE를 더한 id를 쓴다 → game_engine.get_match_detail이 그 범위면 이 표를
    읽는다. 경기 상세 화면은 "결장 경기의 팀 화면"과 같은 모양(통계·라인업 평점·양 팀 득점)으로 보여준다.
"""
import json
import random

SPECTATOR_ID_BASE = 1_000_000_000

DDL = """CREATE TABLE IF NOT EXISTS spectator_match_details(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER, week INTEGER, season INTEGER, day INTEGER,
    league_name TEXT, focus_team_id INTEGER, is_home INTEGER,
    home_team_id INTEGER, away_team_id INTEGER,
    home_name TEXT, away_name TEXT,
    home_score INTEGER, away_score INTEGER,
    result TEXT, rating REAL, goals INTEGER, assists INTEGER, saves INTEGER,
    detail_json TEXT, possession_log TEXT, lineup_stats TEXT, player_ratings TEXT)"""


def create_tables(c):
    c.execute(DDL)


# 검증용 기록(게임 동작에는 영향 없음)
SIM_LOG = []


def target_team_ids(conn=None):
    """G2 대상 팀 집합(사용자 감독 팀 ∪ 구단주 구단)."""
    try:
        import team_plan as _tp
        import club_owner as _co
        return set(_tp.user_team_ids(conn)) | set(_co.owned_team_ids(conn))
    except Exception:
        return set()


def my_team_id(conn=None):
    from database import get_conn
    c = conn or get_conn()
    try:
        r = c.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
        return (r[0] if r else None) or None
    except Exception:
        return None
    finally:
        if conn is None:
            c.close()


def focus_of(hid, aid, targets=None, my_tid=None, conn=None):
    """G2 대상 경기면 관전 기준 팀(대상 팀, 둘 다면 홈) id, 아니면 None.
    내 소속팀이 끼면 G1 몫이라 None."""
    if targets is None:
        targets = target_team_ids(conn)
    if not targets:
        return None
    if my_tid is None:
        my_tid = my_team_id(conn)
    if my_tid and my_tid in (hid, aid):
        return None
    if hid in targets:
        return hid
    if aid in targets:
        return aid
    return None


def sim_target_match(hid, aid, *, home_adv, extra_time=False, comp_name="", kind="league", week=None,
                     day=None, is_league=False, agg_home=0, agg_away=0, targets=None, my_tid=None):
    """G2 대상 경기면 체인 엔진으로 돌리고 관전 기록을 저장한다.
    반환: None(대상 아님 — 호출부는 기존 AI 공식) 또는 {"hs","as","detail_id","sim"}.
    엔진 예외는 game_engine._chain_note_error로 남기고 None(호출부 공식 폴백)."""
    focus = focus_of(hid, aid, targets, my_tid)
    if focus is None:
        return None
    import game_engine as ge
    try:
        from match_sim.tactical_engine import simulate_my_match
        from database import get_conn
        c0 = get_conn()
        try:
            cur = c0.cursor()
            hf, af = ge._team_formation(cur, hid), ge._team_formation(cur, aid)
        finally:
            c0.close()
        sim = simulate_my_match(hid, aid, hf, af, home_adv=home_adv, extra_time=extra_time,
                                agg_home=agg_home, agg_away=agg_away, me=None)
    except Exception as e:
        ge._chain_note_error("g2", e)
        return None
    hs, as_ = sim["home_score"], sim["away_score"]
    did = None
    try:
        if is_league:
            _accum_realstat(sim)
        did = _save_spectator(sim, hid, aid, focus, comp_name, kind, week, day)
    except Exception as e:
        ge._chain_note_error("g2-save", e)
    SIM_LOG.append({"h": hid, "a": aid, "focus": focus, "hs": hs, "as": as_, "detail_id": did,
                    "comp": comp_name, "kind": kind, "et": bool(sim.get("went_extra_time"))})
    return {"hs": hs, "as": as_, "detail_id": did, "sim": sim}


def note_pso(res, winner_home, pso_score):
    """연장 후 동점 → 호출부가 정한 승부차기 결과를 관전 기록에 덧붙인다."""
    if not res or not res.get("detail_id"):
        return
    try:
        from database import get_conn
        conn = get_conn()
        row = conn.execute("SELECT detail_json, focus_team_id, home_team_id FROM spectator_match_details "
                           "WHERE id=?", (res["detail_id"],)).fetchone()
        if row:
            pl = json.loads(row[0] or "{}")
            focus_home = (row[1] == row[2])
            pl["pso"] = {"won": bool(winner_home) == focus_home, "score": pso_score}
            conn.execute("UPDATE spectator_match_details SET detail_json=? WHERE id=?",
                         (json.dumps(pl, ensure_ascii=False), res["detail_id"]))
            conn.commit()
        conn.close()
    except Exception:
        pass


def _accum_realstat(sim):
    import game_engine as ge
    from database import get_conn
    year = ge.get_state()["current_year"]
    conn = get_conn()
    c = conn.cursor()
    for side in ("home", "away"):
        for e in sim.get(side + "_player_ratings") or []:
            if not e:
                continue
            pid = e.get("id")
            if pid is None or pid < 0:
                continue
            c.execute(
                """INSERT INTO ai_player_realstat_season(player_id, year, goals, assists, matches)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(player_id, year) DO UPDATE SET
                       goals = goals + excluded.goals,
                       assists = assists + excluded.assists,
                       matches = matches + 1""",
                (pid, year, e.get("goals", 0) or 0, e.get("assists", 0) or 0))
    conn.commit()
    conn.close()


def _events(sim, focus_side):
    """관전 타임라인 — 양 팀 득점(관전 팀 ⚽ / 상대 🥅)과 퇴장."""
    from match_sim.tactical_engine import timeline_minute
    names = {}
    for side in ("home", "away"):
        for e in sim.get(side + "_player_ratings") or []:
            if e and e.get("id") is not None:
                names[(side, e["id"])] = e.get("name") or ""
    ev = []
    for r in sim.get("possession_log") or []:
        o, t = r.get("outcome"), r.get("team")
        m = timeline_minute(r.get("min", 0))
        if o == "goal":
            nm = names.get((t, r.get("scorer_id")), "")
            pk = " (PK)" if r.get("kind") == "pk" else ""
            if t == focus_side:
                ev.append((m, f"⚽ 골{pk} ({nm})" if nm else f"⚽ 골{pk}"))
            else:
                ev.append((m, f"🥅 실점{pk} ({nm})" if nm else f"🥅 실점{pk}"))
        elif o == "red":
            nm = names.get((t, r.get("player_id")), "")
            who = "우리 팀" if t == focus_side else "상대"
            ev.append((m, f"🟥 퇴장 — {who} {nm}".rstrip()))
    return ev


def _save_spectator(sim, hid, aid, focus, comp_name, kind, week, day, absence_reason="spectator"):
    import game_engine as ge
    from database import get_conn
    from match_sim import match_flow
    st = ge.get_state() or {}
    week = week if week is not None else st.get("current_week")
    hs, as_ = sim["home_score"], sim["away_score"]
    focus_home = (focus == hid)
    side = "home" if focus_home else "away"
    gf, ga = (hs, as_) if focus_home else (as_, hs)
    result = "win" if gf > ga else ("loss" if gf < ga else "draw")
    conn = get_conn()
    c = conn.cursor()
    hn, an = ge._team_name(c, hid, "홈팀"), ge._team_name(c, aid, "원정팀")
    conn.close()
    engine_stats = {"home": sim["home_stats"], "away": sim["away_stats"]}
    try:
        team_stats = ge._derive_match_stats(focus_home, hs, as_, 0, 0, 0, "", {}, engine_stats=engine_stats)
    except Exception:
        team_stats = None
    events = sorted(_events(sim, side), key=lambda x: ge._min_sortkey(x[0]))
    _rng = random.getstate()          # 관전 저장은 게임 난수 흐름을 건드리지 않는다
    try:
        lineup_stats = match_flow.generate_lineup_stats(hn, an)
    except Exception:
        lineup_stats = {}
    random.setstate(_rng)
    payload = {
        "events": [[m, t] for m, t in events], "verdict": "", "played": False, "benched": False,
        "absence_reason": absence_reason, "spectator": absence_reason == "spectator",
        "focus_team_id": focus, "position": "",
        "pso": None, "detail": {"shots": 0, "shots_on": 0, "key_passes": 0, "dribbles": 0, "blocks": 0,
                                "pass_acc": 0.0},
        "team_stats": team_stats,
        "team_stats_detail": {"home": sim.get("home_stats_detail"), "away": sim.get("away_stats_detail")},
        "score_90": [sim.get("home_score_90", hs), sim.get("away_score_90", as_)],
        "went_extra_time": bool(sim.get("went_extra_time")),
        "subs": {"home": sim.get("home_subs") or [], "away": sim.get("away_subs") or []},
    }
    pr = {"home": sim.get("home_player_ratings") or [], "away": sim.get("away_player_ratings") or []}
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO spectator_match_details
           (year, week, season, day, league_name, focus_team_id, is_home, home_team_id, away_team_id,
            home_name, away_name, home_score, away_score, result, rating, goals, assists, saves,
            detail_json, possession_log, lineup_stats, player_ratings)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (st.get("current_year"), week, st.get("current_season"), day, comp_name, focus,
         1 if focus_home else 0, hid, aid, hn, an, hs, as_, result, None, 0, 0, 0,
         json.dumps(payload, ensure_ascii=False),
         json.dumps(sim.get("possession_log") or [], ensure_ascii=False),
         json.dumps(lineup_stats, ensure_ascii=False), json.dumps(pr, ensure_ascii=False)))
    did = cur.lastrowid
    conn.commit()
    conn.close()
    # 로그 — 클릭하면 관전 기록이 열리도록 SPECTATOR_ID_BASE를 더한 id를 마커에 쓴다.
    loc = "홈" if focus_home else "원정"
    _head = {"spectator": "👀 관전", "injury": "🚑 부상 결장"}.get(absence_reason, "결장")
    ge.add_log("─" * 44, "sep")
    ge.add_log(f"{_head}  [{comp_name}]  {ge._day_label(week, day)}  ({loc}) "
               f"[match:{SPECTATOR_ID_BASE + did}:{kind}]", "match")
    ge.add_log(f"   {hn} {hs}-{as_} {an}  ({ {'win': '승', 'draw': '무', 'loss': '패'}[result] })", "match")
    try:
        sc = ge._match_scorer_summary(sim.get("possession_log"), pr, hn, an)
        if sc:
            ge.add_log(f"   {sc}", "match")
    except Exception:
        pass
    return did


def save_absent_match(sim, home_id, away_id, is_home, comp_name, kind, week=None, day=None,
                      reason="injury", real_ctx=None):
    """[2026-10 신설, 신민용 요청: "내가 부상을 입었어도 경기 상세는 뜨게 — 다만 라인업에 내가 없어야"]
    내가 결장한 내 팀 경기(각 대회의 sim_my_*_as_ai)는 이미 체인 엔진으로(나 없이) 결과를 낸다 — 그 경기
    기록(sim)을 관전 기록 표(spectator_match_details)에 같은 모양으로 저장하고, 로그에 클릭 마커를 남긴다.
    내 경기 표(match_details)에는 넣지 않는다(감독 관계·커리어·은퇴 스토리가 "내가 뛴 경기"로 읽는 표).
    경기 상세 화면은 "🚑 부상 결장" + 팀 기록(통계·라인업 평점·양 팀 득점)으로 보여준다(내 선수 없음).
    저장 실패는 경기 진행에 영향을 주지 않도록 삼킨다. 게임 난수는 쓰지 않는다(_save_spectator 참고)."""
    if sim is None:
        return None
    # [2026-10 신설] 내가 결장한 우리 팀 경기도 실측 기록(real_stats) — 우리 팀 선수 기록이 100% 실측이 되게.
    if real_ctx:
        record_sim(sim, real_ctx.get("comp"), real_ctx.get("tkey"), home_id, away_id)
    focus = home_id if is_home else away_id
    try:
        return _save_spectator(sim, home_id, away_id, focus, comp_name, kind, week, day,
                               absence_reason=reason or "injury")
    except Exception as e:
        print(f"[ABSENT-DETAIL] 결장 경기 상세 저장 실패(건너뜀): {e}")
        return None

# ══════════════════════════════════════════════════════════════
# [2026-10 신설, 신민용 확정 A안] 내가 참가하는 대회의 AI 경기도 경기 엔진으로 — 실측 기록용
# ══════════════════════════════════════════════════════════════
# 내 리그, 우리 팀이 출전한 컵·클럽 대회의 AI 팀끼리 경기를 내 경기와 같은 엔진(simulate_my_match, me=None)으로
# 돌리고, 그 경기 선수 기록을 real_stats에 쌓는다 — 그 대회 모든 선수의 골·도움·평점이 실측이 된다
# (real_stats 모듈 docstring). 관전 기록·로그는 남기지 않는다(한 주에 수십 경기라 로그가 넘친다).
# 스코어는 호출부가 G2와 같은 방식(forced)으로 쓴다 — 팀 전적·순위·진출·승부차기는 기존 경로 그대로.
_COVER_CACHE = {}

# 대회 종류 → (real_stats comp 키, tkey 접두사) — 내 경기(_save_match_detail real_ctx)와 같은 키여야 한다.
COVER_KEYS = {
    "league": ("league", None), "cup": ("cup", "cup"), "lower_cup": ("lower_cup", "lower_cup"),
    "domestic_sc": ("dsc", "dsc"), "po": ("po", "po"), "cwc": ("cwc", "cwc_tournaments"),
}


def covered(entry_table, match_table, tournament_id, my_tid=None):
    """우리 팀이 이 대회(tournament_id)에 출전했는가 — 참가 명단(entry_table) 기준, 없으면 경기표 기준."""
    if my_tid is None:
        my_tid = my_team_id()
    if not my_tid or tournament_id is None:
        return False
    key = (entry_table, match_table, tournament_id, my_tid)
    if key in _COVER_CACHE:
        return _COVER_CACHE[key]
    from database import get_conn
    conn = get_conn()
    v = False
    try:
        if entry_table:
            v = bool(conn.execute(f"SELECT 1 FROM {entry_table} WHERE tournament_id=? AND team_id=? LIMIT 1",
                                  (tournament_id, my_tid)).fetchone())
        else:
            raise LookupError
    except Exception:
        try:
            v = bool(conn.execute(f"SELECT 1 FROM {match_table} WHERE tournament_id=? AND "
                                  "(home_team_id=? OR away_team_id=?) LIMIT 1",
                                  (tournament_id, my_tid, my_tid)).fetchone())
        except Exception:
            v = False
    # '참가 아님'은 캐시하지 않는다 — 국내컵(cup_entries)은 상위 부 팀이 뒤 라운드에서 명단에 들어오고,
    # 경기표 기준은 다음 라운드에 생길 수 있다. 참가가 확인된 뒤(True)만 캐시.
    if v:
        _COVER_CACHE[key] = v
    return v


def sim_cover_match(hid, aid, *, home_adv, extra_time=False, agg_home=0, agg_away=0, comp, tkey,
                    my_tid=None, targets=None, prior=None):
    """내가 참가하는 대회의 AI 경기 — 엔진으로 돌리고 실측 기록. 반환: None(대상 아님·엔진 실패 → 호출부 공식)
    또는 {"hs","as","detail_id": None,"sim"}. 내 소속팀 경기(G1)·G2 대상 경기는 여기서 다루지 않는다."""
    if prior is not None:
        # G2(감독 팀·구단주 구단) 경기는 이미 엔진으로 돌았다 — 그 결과를 실측으로만 쌓는다.
        if prior.get("sim"):
            record_sim(prior["sim"], comp, tkey, hid, aid)
        return prior
    if my_tid is None:
        my_tid = my_team_id()
    if my_tid and my_tid in (hid, aid):
        return None
    if targets and (hid in targets or aid in targets):
        return None
    import game_engine as ge
    try:
        from match_sim.tactical_engine import simulate_my_match
        from database import get_conn
        c0 = get_conn()
        try:
            cur = c0.cursor()
            hf, af = ge._team_formation(cur, hid), ge._team_formation(cur, aid)
        finally:
            c0.close()
        sim = simulate_my_match(hid, aid, hf, af, home_adv=home_adv, extra_time=extra_time,
                                agg_home=agg_home, agg_away=agg_away, me=None)
    except Exception as e:
        ge._chain_note_error("cover", e)
        return None
    record_sim(sim, comp, tkey, hid, aid)
    return {"hs": sim["home_score"], "as": sim["away_score"], "detail_id": None, "sim": sim}


def record_sim(sim, comp, tkey, home_key, away_key):
    """엔진 결과(sim)의 양 팀 선수 기록을 실측 표에 쌓는다. 실패는 경기 진행에 영향 주지 않는다."""
    if not sim:
        return
    try:
        import real_stats as _rs
        import game_engine as ge
        from database import get_conn
        conn = get_conn()
        _rs.record(conn, {"comp": comp, "tkey": tkey, "home": home_key, "away": away_key},
                   {"home": sim.get("home_player_ratings") or [], "away": sim.get("away_player_ratings") or []},
                   sim.get("home_score"), sim.get("away_score"), (ge.get_state() or {}).get("current_year"))
        conn.commit()
    except Exception as e:
        print("real_stats 기록 오류(건너뜀):", e)