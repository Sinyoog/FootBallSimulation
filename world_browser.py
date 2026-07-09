"""
world_browser.py — 세계 리그 검색 + 역대 챔피언스리그/월드컵 기록 조회.

[실시간 전환] 예전엔 이 게임이 675개 리그 중 내 국가 리그만 시즌 종료 로직에서
강제로 일정생성+시뮬됐고, 나머지는 이적 오퍼나 이 검색 화면에서 유저가 직접
열어봐야만 그 자리에서 지연 시뮬됐다(그래서 리그마다 '● 라이브 / ○ 미시뮬'
배지가 따로 있었다). 지금은 game_engine._generate_all_league_schedules가 매
시즌 시작 시 전 세계 모든 리그의 일정을 미리 깔아 두고, 매주 정규 흐름의
_sim_all_ai_matches가 리그 구분 없이 실시간으로 결과를 채운다. 즉 유저가 한
번도 안 열어본 리그도 항상 그 시즌 진행 상황을 그대로 갖고 있다 — '라이브'
여부를 따로 표시하거나 되돌릴 이유가 없어져서 그 배지/리셋 기능은 제거했다.
이 모듈의 검색/조회 함수들은 이제 순수 DB 읽기만 한다.
"""
from database import get_conn


# ─────────────────────────────────────────
# 1. 리그 검색 (대륙/국가별 목록)
# ─────────────────────────────────────────
def list_continents():
    """존재하는 대륙 목록 (countries.continent 기준, 오세아니아 등 포함)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT continent FROM countries ORDER BY continent").fetchall()
    conn.close()
    return [r["continent"] for r in rows]


def list_countries(continent=None, grade=None):
    """대륙/등급으로 필터링한 국가 목록 (등급순 정렬)."""
    conn = get_conn()
    q = "SELECT id, name, flag, grade, continent FROM countries WHERE 1=1"
    params = []
    if continent:
        q += " AND continent=?"; params.append(continent)
    if grade:
        q += " AND grade=?"; params.append(grade)
    q += " ORDER BY grade, name"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 국가 등급 고정 순서(강함→약함). DB에 실제 존재하는 값만 걸러서 쓴다.
_GRADE_ORDER = ["SS", "S", "A", "B", "C", "D", "E", "F"]


def list_grades():
    """실제 존재하는 국가 등급 목록을 정해진 순서(S>A>B>...)로 반환."""
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT grade FROM countries").fetchall()
    conn.close()
    existing = {r["grade"] for r in rows}
    return [g for g in _GRADE_ORDER if g in existing]


def search_leagues(continent=None, country_id=None, name_query=None, grade=None):
    """조건에 맞는 리그 목록. 이제 모든 리그가 시즌 시작 시 일정을 미리 받고
    매주 실시간으로 결과가 채워지므로, 예전의 '이번 시즌 시뮬 여부(simulated)'
    배지는 더 이상 의미가 없어 반환하지 않는다.
    """
    conn = get_conn()
    c = conn.cursor()

    q = ("SELECT l.id, l.name, l.tier, cn.id as country_id, cn.name as country, "
         "cn.flag as flag, cn.grade as grade, cn.continent as continent "
         "FROM leagues l JOIN countries cn ON l.country_id = cn.id WHERE 1=1")
    params = []
    if continent:
        q += " AND cn.continent=?"; params.append(continent)
    if country_id:
        q += " AND cn.id=?"; params.append(country_id)
    if grade:
        q += " AND cn.grade=?"; params.append(grade)
    if name_query:
        q += " AND (l.name LIKE ? OR cn.name LIKE ?)"
        like = f"%{name_query}%"
        params += [like, like]
    q += " ORDER BY cn.grade, cn.name, l.tier"

    rows = [dict(r) for r in c.execute(q, params).fetchall()]
    conn.close()
    return rows


# ─────────────────────────────────────────
# 2. 리그 순위표
# ─────────────────────────────────────────
def get_league_standings_for_browser(league_id, season=None, year=None):
    """이 리그의 이번 시즌 순위표를 반환한다. 모든 리그가 시즌 시작 시 이미
    일정을 받아 매주 실시간으로 채워지므로 평소엔 그냥 바로 조회하면 된다.
    아주 드물게(예: 구버전 세이브 마이그레이션 등) 일정이 비어 있는 리그가
    있으면 안전망으로 그 자리에서 한 번만 생성+시뮬한다.
    """
    from game_engine import (get_state, generate_season_schedule,
                             _sim_league_full, get_league_standings)
    st = get_state()
    if season is None:
        season = st["current_season"] if st else 1
    if year is None:
        year = st["current_year"] if st else 2000

    conn = get_conn()
    cnt = conn.execute(
        "SELECT COUNT(*) n FROM match_results WHERE league_id=? AND season=?",
        (league_id, season)).fetchone()["n"]
    conn.close()

    if cnt == 0:
        generate_season_schedule(league_id, season, year)
        _sim_league_full(league_id, season)

    return get_league_standings(league_id, season=season)


def get_league_champions(league_id, limit=30):
    """[신규] 이 리그의 시즌별 1~3위 + 꼴찌(강등권) 팀명 목록. 실제로 경기가
    진행된 시즌만 대상이며(한 번도 경기가 없었던 시즌은 제외), 새 테이블 없이
    match_results를 시즌 단위로 그때그때 집계해서 계산한다(승강전 처리와 동일한 방식).
    최신 시즌부터 최대 limit개.
    'last'는 그 시즌 순위표의 실제 마지막 팀(팀 수가 8개면 8위, 그보다 적으면
    있는 만큼의 마지막 순위)이다 — 항상 '8위'로 고정하지 않고 리그 규모에 맞춘다."""
    from game_engine import get_league_standings
    conn = get_conn()
    season_rows = conn.execute(
        """SELECT DISTINCT season, year FROM match_results
           WHERE league_id=? AND home_score>=0
           ORDER BY season DESC LIMIT ?""",
        (league_id, limit)).fetchall()
    out = []
    for sr in season_rows:
        standings = get_league_standings(league_id, season=sr["season"], conn=conn)
        if not standings:
            continue
        n = len(standings)
        last_rank = n  # 실제 순위표 마지막 등수 (보통 8, 팀 수가 다르면 그에 맞춰)
        out.append({
            "season": sr["season"], "year": sr["year"],
            "first":  standings[0]["name"] if n > 0 else "-",
            "second": standings[1]["name"] if n > 1 else "-",
            "third":  standings[2]["name"] if n > 2 else "-",
            "last_rank": last_rank,
            "last":   standings[-1]["name"] if n > 3 else "-",
        })
    conn.close()
    return out


# [실시간 전환] 예전엔 유저가 열어본 리그만 '라이브' 상태였고, 그걸 다시
# '미시뮬'로 되돌리는 reset_league_simulation() / 되돌리기 대상에서 내 리그를
# 제외하는 is_my_league()가 있었다. 지금은 모든 리그가 항상 실시간으로 진행
# 중이라 되돌릴 '시뮬 이전 상태' 자체가 없으므로 두 함수 모두 제거했다.


# ─────────────────────────────────────────
# 3. 역대 챔피언스리그 기록
# ─────────────────────────────────────────
def _get_cl_placements(tournament_id, conn):
    """결승(F)+3/4위전(TP) cl_matches 결과로 1~4위 team_id를 도출.
    intl_engine 쪽 _get_placements와 동일한 패턴, team_id(정수) 기준만 다름."""
    from champions_engine import _winner_of
    fm = conn.execute(
        "SELECT * FROM cl_matches WHERE tournament_id=? AND stage='F' "
        "AND home_score>=0 ORDER BY id DESC LIMIT 1", (tournament_id,)).fetchone()
    if not fm:
        return None
    fm = dict(fm)
    winner = _winner_of(fm)
    runner_up = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    third = fourth = None
    tp = conn.execute(
        "SELECT * FROM cl_matches WHERE tournament_id=? AND stage='TP' "
        "AND home_score>=0 ORDER BY id DESC LIMIT 1", (tournament_id,)).fetchone()
    if tp:
        tp = dict(tp)
        third = _winner_of(tp)
        fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]

    return {"winner": winner, "runner_up": runner_up, "third": third, "fourth": fourth}


def get_cl_history(continent=None, limit=100):
    """완료된 챔피언스리그 대회의 연도별 1~4위(팀명+국가+국기) 목록.
    cl_tournaments.winner_team_id는 대회가 실제로 끝났을 때만 채워지므로
    (champions_engine.py의 status='done' 처리 시점), 그 전까지는 표시되지 않음.
    """
    conn = get_conn(); c = conn.cursor()
    q = """SELECT t.id, t.year, t.continent, t.name
           FROM cl_tournaments t
           WHERE t.status='done' AND t.winner_team_id != 0"""
    params = []
    if continent:
        q += " AND t.continent=?"; params.append(continent)
    q += " ORDER BY t.year DESC, t.id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in c.execute(q, params).fetchall()]

    # 대회별 1~4위 team_id 도출
    placements_by_row = []
    all_tids = set()
    for r in rows:
        pl = _get_cl_placements(r["id"], conn) or {}
        placements_by_row.append(pl)
        for key in ("winner", "runner_up", "third", "fourth"):
            if pl.get(key):
                all_tids.add(pl[key])

    # [최적화] 팀마다 개별 JOIN 조회 대신, 등장한 team_id 전체를 1회 IN 쿼리로
    # 일괄 조회 (완료 대회 수만큼 팀명 조회 왕복이 늘어나지 않도록).
    team_info = {}
    if all_tids:
        ph = ",".join("?" * len(all_tids))
        for tr in c.execute(
                f"""SELECT tm.id, tm.name, cn.flag, cn.name as country
                    FROM teams tm
                    LEFT JOIN leagues l ON tm.league_id = l.id
                    LEFT JOIN countries cn ON l.country_id = cn.id
                    WHERE tm.id IN ({ph})""", list(all_tids)).fetchall():
            team_info[tr["id"]] = {"name": tr["name"], "flag": tr["flag"] or "",
                                   "country": tr["country"] or ""}
    conn.close()

    for r, pl in zip(rows, placements_by_row):
        for key in ("winner", "runner_up", "third", "fourth"):
            tid = pl.get(key)
            info = team_info.get(tid) if tid else None
            r[f"{key}_name"] = info["name"] if info else ""
            r[f"{key}_flag"] = info["flag"] if info else ""
            r[f"{key}_country"] = info["country"] if info else ""
    return rows


# ─────────────────────────────────────────
# 4. 국가대표 대회(월드컵/대륙컵) 1~4위 조회
# ─────────────────────────────────────────
def _get_placements(tournament_id, conn):
    """결승(F)+3/4위전(TP) 결과로 1~4위 도출. TP가 없던 대회(구버전 데이터 등)는
    3/4위 없이 1/2위만 채워서 반환. 결승이 아직 없으면 None.
    """
    from intl_engine import _winner_of
    fm = conn.execute(
        "SELECT * FROM intl_matches WHERE tournament_id=? AND stage='F' "
        "AND home_score>=0", (tournament_id,)).fetchone()
    if not fm:
        return None
    fm = dict(fm)
    winner = _winner_of(fm)
    runner_up = fm["away"] if winner == fm["home"] else fm["home"]

    third = fourth = None
    tp = conn.execute(
        "SELECT * FROM intl_matches WHERE tournament_id=? AND stage='TP' "
        "AND home_score>=0", (tournament_id,)).fetchone()
    if tp:
        tp = dict(tp)
        third = _winner_of(tp)
        fourth = tp["away"] if third == tp["home"] else tp["home"]

    return {"winner": winner, "runner_up": runner_up, "third": third, "fourth": fourth}


def _attach_placements_and_flags(rows, conn):
    """intl_tournaments 행 목록에 1~4위 국가명 + 국기를 채워 넣는다.
    [최적화] 국기는 대회마다 개별 조회하지 않고, 전체 대회에서 등장한
    국가명을 모아 1회 IN 쿼리로 일괄 조회한다 (완료 대회 수가 적어 원래도
    가벼운 조회지만, 여러 대회를 한 화면에 나열할 때 왕복 횟수를 줄인다)."""
    placements_by_row = []
    all_names = set()
    for r in rows:
        pl = _get_placements(r["id"], conn) or {}
        placements_by_row.append(pl)
        for key in ("winner", "runner_up", "third", "fourth"):
            if pl.get(key):
                all_names.add(pl[key])

    flag_map = {}
    if all_names:
        ph = ",".join("?" * len(all_names))
        for fr in conn.execute(
                f"SELECT name, flag FROM countries WHERE name IN ({ph})",
                list(all_names)).fetchall():
            flag_map[fr["name"]] = fr["flag"]

    for r, pl in zip(rows, placements_by_row):
        for key in ("winner", "runner_up", "third", "fourth"):
            nat = pl.get(key) or ""
            r[key] = nat
            r[f"{key}_flag"] = flag_map.get(nat, "")
    return rows


def get_wc_history(limit=100):
    """완료된 월드컵(kind='world') 대회의 연도별 1~4위 목록."""
    conn = get_conn(); c = conn.cursor()
    rows = [dict(r) for r in c.execute(
        """SELECT id, year, name
           FROM intl_tournaments
           WHERE kind='world' AND status='done' AND winner != ''
           ORDER BY year DESC, id DESC LIMIT ?""", (limit,)).fetchall()]
    rows = _attach_placements_and_flags(rows, conn)
    conn.close()
    return rows


# ─────────────────────────────────────────
# 5. 역대 대륙컵(네이션스컵) 기록
# ─────────────────────────────────────────
def list_continental_cup_names():
    """지금까지 이 세이브에서 실제로 열린 적 있는 대륙컵 이름 목록.
    [변경] 이제 대륙컵은 챔피언스리그처럼 4개 대륙 전부 매 주기 생성되므로
    (intl_engine.start_intl_tournament), 대회가 열릴 시기(4년 주기)가 아직
    안 됐을 때만 이 목록이 비어있다.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT name FROM intl_tournaments WHERE kind='continent' "
        "ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def get_continental_cup_history(name=None, limit=100):
    """완료된 대륙컵(kind='continent') 대회의 연도별 1~4위 목록.
    name을 주면 그 대회(예: '아시안컵')만, 없으면 전체(여러 대륙 섞여서) 반환.
    """
    conn = get_conn(); c = conn.cursor()
    q = ("SELECT id, year, name FROM intl_tournaments "
         "WHERE kind='continent' AND status='done' AND winner != ''")
    params = []
    if name:
        q += " AND name=?"; params.append(name)
    q += " ORDER BY year DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in c.execute(q, params).fetchall()]
    rows = _attach_placements_and_flags(rows, conn)
    conn.close()
    return rows


# ─────────────────────────────────────────
# 6. 대회 상세(조별리그 순위 + 토너먼트 대진) — 월드컵/네이션스컵
# ─────────────────────────────────────────
# [성능] 아래 함수들은 전부 이미 끝난 대회의 intl_matches/cl_matches를
# 그대로 읽기만 한다. 새로 시뮬레이션하는 게 전혀 없으므로(대회당 매치 수는
# 많아야 수십 개 고정) 리그 검색의 지연시뮬과 달리 트리거할 것 자체가 없다
# — 순수 조회라 몇 밀리초 수준.

_INTL_KO_STAGE_ORDER = ["R32", "R16", "QF", "SF", "TP", "F"]


def get_intl_tournament_detail(tournament_id):
    """월드컵/네이션스컵 한 대회의 조별리그 순위표 + 토너먼트(녹아웃) 대진."""
    from intl_engine import STAGE_KO
    conn = get_conn(); c = conn.cursor()

    entries = [dict(r) for r in c.execute(
        "SELECT country, flag, grade, grp FROM intl_entries "
        "WHERE tournament_id=? AND grp != ''", (tournament_id,)).fetchall()]
    groups = {}
    for e in entries:
        groups.setdefault(e["grp"], []).append({
            "country": e["country"], "flag": e["flag"], "grade": e["grade"],
            "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0})
    idx = {(g, t["country"]): t for g, teams in groups.items() for t in teams}

    for m in c.execute(
            "SELECT grp, home, away, home_score, away_score FROM intl_matches "
            "WHERE tournament_id=? AND stage='group' AND home_score>=0",
            (tournament_id,)).fetchall():
        h, a = idx.get((m["grp"], m["home"])), idx.get((m["grp"], m["away"]))
        if not h or not a:
            continue
        h["gf"] += m["home_score"]; h["ga"] += m["away_score"]
        a["gf"] += m["away_score"]; a["ga"] += m["home_score"]
        if m["home_score"] > m["away_score"]:   h["wins"] += 1;  a["losses"] += 1
        elif m["home_score"] < m["away_score"]: a["wins"] += 1;  h["losses"] += 1
        else:                                   h["draws"] += 1; a["draws"] += 1
    for teams in groups.values():
        for t in teams:
            t["pts"] = t["wins"] * 3 + t["draws"]
            t["gd"] = t["gf"] - t["ga"]
        teams.sort(key=lambda t: (-t["pts"], -t["gd"], -t["gf"]))

    ko_rows = c.execute(
        "SELECT stage, home, away, home_score, away_score, pso_winner, pso_score "
        "FROM intl_matches WHERE tournament_id=? AND stage NOT IN "
        "('group','qual_group','qual_po') AND home_score>=0 ORDER BY id",
        (tournament_id,)).fetchall()
    conn.close()

    ko_by_stage = {}
    for m in ko_rows:
        ko_by_stage.setdefault(m["stage"], []).append(dict(m))
    knockout = [{"stage": s, "stage_ko": STAGE_KO.get(s, s), "matches": ko_by_stage[s]}
                for s in _INTL_KO_STAGE_ORDER if s in ko_by_stage]

    return {"groups": groups, "knockout": knockout}


def get_wc_qualifier_summary(wc_year):
    """이 월드컵(연도)의 대륙별 예선 통과국 목록 (qual_results 기반 요약).
    [주의] 조별리그 단위 상세가 아니라 '최종 통과국 명단'까지만 제공한다.
    예선 자체도 intl_matches에 그룹별로 남아있긴 하지만, 본선처럼
    (대회→그룹→경기) 관계가 깔끔히 안 갈라져 있어 상세 재구성 비용 대비
    실익이 적어 요약 수준으로 뒀다.
    """
    conn = get_conn(); c = conn.cursor()
    rows = [dict(r) for r in c.execute(
        "SELECT continent, country, flag, grade FROM qual_results "
        "WHERE target_year=? AND kind='world' ORDER BY continent, country",
        (wc_year,)).fetchall()]
    conn.close()
    by_conf = {}
    for r in rows:
        by_conf.setdefault(r["continent"] or "기타", []).append(r)
    return by_conf


# ─────────────────────────────────────────
# 7. 대회 상세 — 챔피언스리그
# ─────────────────────────────────────────
_CL_KO_STAGE_ORDER = ["R32", "R16", "QF", "SF", "TP", "F"]


def get_cl_tournament_detail(tournament_id):
    """챔피언스리그 한 대회의 조별리그 순위표 + 토너먼트(녹아웃) 대진."""
    from champions_engine import STAGE_KO
    conn = get_conn(); c = conn.cursor()

    entries = [dict(r) for r in c.execute(
        "SELECT team_id, team_name, flag, country, grade, grp FROM cl_entries "
        "WHERE tournament_id=? AND grp != ''", (tournament_id,)).fetchall()]
    groups = {}
    for e in entries:
        groups.setdefault(e["grp"], []).append({
            "team_id": e["team_id"], "name": e["team_name"], "flag": e["flag"],
            "country": e["country"], "grade": e["grade"],
            "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0})
    idx = {(g, t["team_id"]): t for g, teams in groups.items() for t in teams}

    for m in c.execute(
            "SELECT grp, home_team_id, away_team_id, home_score, away_score "
            "FROM cl_matches WHERE tournament_id=? AND stage='group' "
            "AND home_score>=0", (tournament_id,)).fetchall():
        h, a = idx.get((m["grp"], m["home_team_id"])), idx.get((m["grp"], m["away_team_id"]))
        if not h or not a:
            continue
        h["gf"] += m["home_score"]; h["ga"] += m["away_score"]
        a["gf"] += m["away_score"]; a["ga"] += m["home_score"]
        if m["home_score"] > m["away_score"]:   h["wins"] += 1;  a["losses"] += 1
        elif m["home_score"] < m["away_score"]: a["wins"] += 1;  h["losses"] += 1
        else:                                   h["draws"] += 1; a["draws"] += 1
    for teams in groups.values():
        for t in teams:
            t["pts"] = t["wins"] * 3 + t["draws"]
            t["gd"] = t["gf"] - t["ga"]
        teams.sort(key=lambda t: (-t["pts"], -t["gd"], -t["gf"]))

    ko_rows = c.execute(
        "SELECT stage, home_team_id, away_team_id, home_score, away_score, "
        "pso_winner, pso_score FROM cl_matches WHERE tournament_id=? AND "
        "stage != 'group' AND home_score>=0 ORDER BY id",
        (tournament_id,)).fetchall()

    # 팀명/국기 매핑 (entries가 이 대회의 전체 출전팀을 이미 담고 있으므로
    # 녹아웃 단계에 나오는 team_id는 항상 여기서 찾아진다)
    team_info = {e["team_id"]: e for e in entries}
    conn.close()

    ko_by_stage = {}
    for m in ko_rows:
        m = dict(m)
        m["home_info"] = team_info.get(m["home_team_id"], {})
        m["away_info"] = team_info.get(m["away_team_id"], {})
        ko_by_stage.setdefault(m["stage"], []).append(m)
    knockout = [{"stage": s, "stage_ko": STAGE_KO.get(s, s), "matches": ko_by_stage[s]}
                for s in _CL_KO_STAGE_ORDER if s in ko_by_stage]

    return {"groups": groups, "knockout": knockout}