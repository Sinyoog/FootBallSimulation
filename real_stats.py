# -*- coding: utf-8 -*-
"""real_stats.py — 내 선수가 참가한 경기의 AI 선수 실측 기록 (2026-10 신설, 신민용 확정).

[배경] AI 선수의 시즌·대회 기록(골·도움·평점·선방)은 전부 시즌/대회 끝에 포지션·OVR·팀 강도로 만든
추정치다(ai_lifecycle._snapshot_season_ratings / _snapshot_intl_ratings_rows). 그런데 내 경기는 경기
엔진이 22명 전원의 진짜 기록을 내고 경기 상세에도 그대로 보여서, "경기 상세엔 5골인데 선수 검색엔
다른 숫자"가 났다.

[규칙] 내 경기(리그·국내컵·클럽 대항전·슈퍼컵·클럽월드컵·3·4부컵·국내 슈퍼컵·승강 PO·국가대표·
올림픽 — 내가 결장한 우리 팀 경기 포함)는 경기 상세에 저장되는 선수별 기록을 그대로 여기 쌓는다.
시즌·대회 기록을 저장할 때 선수마다
    최종 = 실측(내 경기) + 추정치 × (그 팀이 치른 경기 중 내 경기가 아닌 비율)
로 합친다 — 우리 팀 선수는 그 대회 경기가 전부 내 경기라 100% 실측, 상대 팀 선수는 나와 붙은 경기만
실측이고 나머지는 지금처럼 추정치. 내가 참가하지 않은 대회·리그는 바뀌지 않는다.
개인상(리그 개인상·발롱도르 등은 시즌 기록 표를, 국가대표 대회상은 intl_squad를, 챔스·클럽월드컵
대회상은 apply_to_award_pool을) 이 합친 값으로 판정한다.
"""

MY_ID = -1


def migrations():
    return [
        # [2026-10 이적생 기록 분리] team_key = 그 경기에서 뛴 팀(클럽 team_id·국가명). 시즌 중 이적한
        # 선수의 실측을 팀별로 나눠 둔다 — 예전 표(team_key 없음)는 upgrade()가 team_key=''로 옮긴다.
        "CREATE TABLE IF NOT EXISTS real_match_stats(player_id INTEGER, year INTEGER, comp TEXT, "
        "tkey TEXT, team_key TEXT DEFAULT '', apps INTEGER DEFAULT 0, goals INTEGER DEFAULT 0, "
        "assists INTEGER DEFAULT 0, rating_sum REAL DEFAULT 0, saves INTEGER DEFAULT 0, "
        "conceded INTEGER DEFAULT 0, clean_sheets INTEGER DEFAULT 0, "
        "PRIMARY KEY(player_id, year, comp, tkey, team_key)) WITHOUT ROWID",
        "CREATE TABLE IF NOT EXISTS real_team_matches(team_key TEXT, year INTEGER, comp TEXT, tkey TEXT, "
        "matches INTEGER DEFAULT 0, PRIMARY KEY(team_key, year, comp, tkey)) WITHOUT ROWID",
        "CREATE INDEX IF NOT EXISTS idx_rms_comp_year ON real_match_stats(comp, year)",
    ]


def upgrade(conn):
    """[2026-10] 예전 실측 표(team_key 없음, 이번 달 첫 배포본)를 새 모양으로 옮긴다 — 행은 그대로
    team_key=''(팀 미상 → 시즌을 마친 팀 몫으로 본다)로 보존. 이미 새 모양이면 아무것도 안 한다(멱등)."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(real_match_stats)").fetchall()]
    except Exception:
        return
    if not cols or "team_key" in cols:
        return
    conn.execute("ALTER TABLE real_match_stats RENAME TO real_match_stats_v1")
    for sql in migrations():
        if "real_match_stats(" in sql and "CREATE TABLE" in sql:
            conn.execute(sql)
    conn.execute(
        "INSERT INTO real_match_stats(player_id, year, comp, tkey, team_key, apps, goals, assists, rating_sum, "
        "saves, conceded, clean_sheets) SELECT player_id, year, comp, tkey, '', apps, goals, assists, "
        "rating_sum, saves, conceded, clean_sheets FROM real_match_stats_v1")
    conn.execute("DROP TABLE real_match_stats_v1")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rms_comp_year ON real_match_stats(comp, year)")
    conn.commit()


def reset_tables():
    return ("real_match_stats", "real_team_matches")


def _gk_index(lst):
    """골키퍼 중 가장 오래 뛴 선수의 인덱스(실점·무실점을 그 선수에게)."""
    best, best_min = None, -1
    for i, e in enumerate(lst):
        if e and e.get("is_gk"):
            m = e.get("minutes")
            m = 90 if m is None else m
            if m > best_min:
                best, best_min = i, m
    return best


def record(conn, ctx, player_ratings, hs, as_, year):
    """경기 하나 기록. ctx = {comp, tkey, home, away}(home/away = 팀 키 — 클럽 team_id, 국가대표 국가명)."""
    if not ctx or not player_ratings or year is None:
        return
    comp, tkey = ctx.get("comp"), str(ctx.get("tkey") or "")
    for side, team, gf, ga in (("home", ctx.get("home"), hs, as_), ("away", ctx.get("away"), as_, hs)):
        lst = player_ratings.get(side) or []
        if not lst or team is None:
            continue
        conn.execute(
            """INSERT INTO real_team_matches(team_key, year, comp, tkey, matches) VALUES(?,?,?,?,1)
               ON CONFLICT(team_key, year, comp, tkey) DO UPDATE SET matches = matches + 1""",
            (str(team), year, comp, tkey))
        gk_i = _gk_index(lst)
        for i, e in enumerate(lst):
            if not e or e.get("is_me"):
                continue
            pid = e.get("id")
            if pid is None or not isinstance(pid, int) or pid < 0:
                continue
            mins = e.get("minutes")
            if mins is not None and mins <= 0:
                continue
            try:
                rt = float(e.get("rating") or 0.0)
            except (TypeError, ValueError):
                rt = 0.0
            is_gk_main = (i == gk_i)
            conn.execute(
                """INSERT INTO real_match_stats(player_id, year, comp, tkey, team_key, apps, goals, assists,
                                                rating_sum, saves, conceded, clean_sheets)
                   VALUES(?,?,?,?,?,1,?,?,?,?,?,?)
                   ON CONFLICT(player_id, year, comp, tkey, team_key) DO UPDATE SET
                       apps = apps + 1, goals = goals + excluded.goals, assists = assists + excluded.assists,
                       rating_sum = rating_sum + excluded.rating_sum, saves = saves + excluded.saves,
                       conceded = conceded + excluded.conceded,
                       clean_sheets = clean_sheets + excluded.clean_sheets""",
                (pid, year, comp, tkey, str(team), int(e.get("goals") or 0), int(e.get("assists") or 0), rt,
                 int(e.get("saves") or 0) if is_gk_main else 0,
                 int(ga or 0) if is_gk_main else 0,
                 1 if (is_gk_main and not ga) else 0))


def load(conn, comp, year, tkey=None):
    """{player_id: dict(apps, goals, assists, rating_sum, saves, conceded, clean_sheets)} — tkey 없으면 합산."""
    q = ("SELECT player_id, SUM(apps) a, SUM(goals) g, SUM(assists) s, SUM(rating_sum) r, "
         "SUM(saves) sv, SUM(conceded) gc, SUM(clean_sheets) cs FROM real_match_stats "
         "WHERE comp=? AND year=?" + (" AND tkey=?" if tkey is not None else "") + " GROUP BY player_id")
    args = (comp, year) + ((str(tkey),) if tkey is not None else ())
    try:
        rows = conn.execute(q, args).fetchall()
    except Exception:
        return {}
    return {r[0]: {"apps": r[1] or 0, "goals": r[2] or 0, "assists": r[3] or 0, "rating_sum": r[4] or 0.0,
                   "saves": r[5] or 0, "conceded": r[6] or 0, "clean_sheets": r[7] or 0} for r in rows}


def load_by_team(conn, comp, year):
    """{player_id: {team_key: dict(...)}} — 시즌 중 이적한 선수의 실측을 팀별로(team_key '' = 팀 미상,
    예전 표에서 옮긴 행)."""
    try:
        rows = conn.execute(
            "SELECT player_id, team_key, SUM(apps), SUM(goals), SUM(assists), SUM(rating_sum), SUM(saves), "
            "SUM(conceded), SUM(clean_sheets) FROM real_match_stats WHERE comp=? AND year=? "
            "GROUP BY player_id, team_key", (comp, year)).fetchall()
    except Exception:
        return {}
    out = {}
    for r in rows:
        out.setdefault(r[0], {})[r[1] or ""] = {
            "apps": r[2] or 0, "goals": r[3] or 0, "assists": r[4] or 0, "rating_sum": r[5] or 0.0,
            "saves": r[6] or 0, "conceded": r[7] or 0, "clean_sheets": r[8] or 0}
    return out


def merge(*parts):
    """실측 dict 여러 개 합치기(None은 건너뜀)."""
    out = None
    for p in parts:
        if not p:
            continue
        if out is None:
            out = dict(p)
        else:
            for k, v in p.items():
                out[k] = out.get(k, 0) + v
    return out


def load_tkey(conn, comp, tkey):
    """한 대회(tkey)의 실측 — 연도 무관(국가대표 대회는 tkey가 곧 대회 하나)."""
    try:
        rows = conn.execute(
            "SELECT player_id, SUM(apps), SUM(goals), SUM(assists), SUM(rating_sum), SUM(saves), "
            "SUM(conceded), SUM(clean_sheets) FROM real_match_stats WHERE comp=? AND tkey=? GROUP BY player_id",
            (comp, str(tkey))).fetchall()
    except Exception:
        return {}
    return {r[0]: {"apps": r[1] or 0, "goals": r[2] or 0, "assists": r[3] or 0, "rating_sum": r[4] or 0.0,
                   "saves": r[5] or 0, "conceded": r[6] or 0, "clean_sheets": r[7] or 0} for r in rows}


def team_counts_tkey(conn, comp, tkey):
    try:
        return {r[0]: r[1] or 0 for r in conn.execute(
            "SELECT team_key, SUM(matches) FROM real_team_matches WHERE comp=? AND tkey=? GROUP BY team_key",
            (comp, str(tkey))).fetchall()}
    except Exception:
        return {}


def team_counts(conn, comp, year, tkey=None):
    """{팀 키(str): 기록된 내 경기 수}."""
    q = ("SELECT team_key, SUM(matches) FROM real_team_matches WHERE comp=? AND year=?"
         + (" AND tkey=?" if tkey is not None else "") + " GROUP BY team_key")
    args = (comp, year) + ((str(tkey),) if tkey is not None else ())
    try:
        return {r[0]: r[1] or 0 for r in conn.execute(q, args).fetchall()}
    except Exception:
        return {}


def rest_fraction(team_real, team_total):
    """그 팀 경기 중 내 경기가 아닌(추정으로 채울) 비율."""
    if not team_total or team_total <= 0:
        return 0.0 if team_real else 1.0
    return max(0.0, 1.0 - float(team_real) / float(team_total))


def blend(apps, goals, assists, rating, cs, saves, gc, real, frac):
    """추정치(그 대회 전체 기준) × frac + 실측. rating은 출전 경기 가중 평균. 반환: 같은 7개."""
    e_apps = apps * frac
    r = real or {}
    r_apps = r.get("apps", 0)
    out_apps = int(round(r_apps + e_apps))
    if out_apps <= 0:
        return 0, 0, 0, rating, 0, 0, 0
    if r_apps + e_apps > 0:
        out_rt = (r.get("rating_sum", 0.0) + (rating or 0.0) * e_apps) / (r_apps + e_apps)
    else:
        out_rt = rating
    return (out_apps,
            int(round(r.get("goals", 0) + goals * frac)),
            int(round(r.get("assists", 0) + assists * frac)),
            round(out_rt, 2) if out_rt else out_rt,
            int(round(r.get("clean_sheets", 0) + cs * frac)),
            int(round(r.get("saves", 0) + saves * frac)),
            int(round(r.get("conceded", 0) + gc * frac)))


def apply_to_award_pool(conn, pool, comp, tkey, match_table, tournament_id, my_tid, gk_pos, all_pos):
    """챔스·클럽월드컵 대회상 후보(pool) — 추정 후보에 실측을 합치고, 우리 팀 AI 선수(예전엔 후보에서
    빠져 있었다)를 실측 기록으로 추가한다. pool의 AI 항목은 "pid"를 갖고 있어야 한다."""
    _yr = _year_of(conn, match_table, tournament_id)
    real = load(conn, comp, _yr, tkey=tkey)
    tcnt = team_counts(conn, comp, _yr, tkey=tkey)
    if not real and not tcnt:
        return pool
    totals = {}
    for side in ("home_team_id", "away_team_id"):
        for r in conn.execute(
                f"SELECT {side} AS t, COUNT(*) AS n FROM {match_table} WHERE tournament_id=? AND home_score>=0 "
                f"GROUP BY {side}", (tournament_id,)).fetchall():
            totals[r["t"]] = totals.get(r["t"], 0) + r["n"]
    for x in pool:
        if x.get("is_mine") or x.get("pid") is None:
            continue
        frac = rest_fraction(tcnt.get(str(x["team_id"]), 0), totals.get(x["team_id"], 0))
        rr = real.get(x["pid"])
        if frac >= 1.0 and not rr:
            continue
        n_est = totals.get(x["team_id"], 0) or 1
        _a, g, a, rt, cs, _sv, _gc = blend(n_est, x["goals"], x["assists"], x["rating"], x.get("cs", 0),
                                           0, 0, rr, frac)
        x["goals"], x["assists"], x["cs"] = g, a, cs
        if _a > 0:
            x["rating"] = rt
    if my_tid:
        ph = ",".join("?" * len(all_pos))
        for r in conn.execute(
                f"SELECT id, ovr, position, age FROM ai_players WHERE team_id=? AND position IN ({ph}) ORDER BY id",
                (my_tid, *all_pos)).fetchall():
            rr = real.get(r["id"])
            if not rr or not rr["apps"]:
                continue
            pool.append({"position": r["position"], "goals": rr["goals"], "assists": rr["assists"],
                         "rating": rr["rating_sum"] / rr["apps"], "ovr": r["ovr"],
                         "cs": rr["clean_sheets"] if r["position"] in gk_pos else 0,
                         "age": r["age"] or 25, "is_mine": False, "team_id": my_tid, "pid": r["id"]})
    return pool


def _year_of(conn, match_table, tournament_id):
    ttable = match_table.replace("_matches", "_tournaments")
    try:
        row = conn.execute(f"SELECT year FROM {ttable} WHERE id=?", (tournament_id,)).fetchone()
        return row["year"] if row else None
    except Exception:
        return None