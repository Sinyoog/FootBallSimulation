"""
world_browser.py — 세계 리그 검색 + 역대 챔피언스리그/월드컵 기록 조회.

[설계 원칙 - 지연(lazy) 시뮬레이션]
  이 게임은 671개국(정확히는 211개국) 675개 리그 중 내 국가 리그만 시즌 종료
  로직에서 강제로 일정생성+시뮬된다. 나머지는 이적 오퍼(generate_offers)에
  뜬 팀의 리그만 그 자리에서 지연 시뮬된다 — 이미 있는 패턴.
  이 모듈은 그 지연 패턴을 '검색'에도 그대로 적용한다:
    - 검색/목록 조회 자체는 DB 읽기만 함 (일정·시뮬 트리거 없음 → 가볍다).
    - 사용자가 특정 리그를 '선택'해서 순위표를 열 때만, 그 리그에 이번 시즌
      경기기록이 없으면 그 자리에서 1회 생성+시뮬한다 (해당 리그 팀 수만큼만
      비용 발생 — 보통 8~20팀, 전체 675리그를 매주 도는 것과는 비교가 안 되게 쌈).
    - 한번 시뮬된 리그는 이후 검색부터는 이미 있는 순위표를 그대로 보여준다.
  이렇게 하면 "전체 상시 시뮬레이션"으로 갈 때 생기는 주간 틱 비용 폭증
  (플레이어 리그 ~4경기/주 → 전세계 ~2500경기/주) 없이, 유저가 실제로 들여다본
  리그만 그때그때 살아난다.
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
    """조건에 맞는 리그 목록. 각 리그에 이번 시즌 실제 경기기록이 있는지(simulated)
    여부를 함께 반환한다 — 목록 조회 자체는 시뮬레이션을 트리거하지 않는다.
    """
    conn = get_conn()
    c = conn.cursor()

    st_row = conn.execute(
        "SELECT current_season FROM season_state WHERE id=1").fetchone()
    season = st_row["current_season"] if st_row else 1

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

    # [최적화] 리그마다 개별 COUNT 쿼리 대신, 이번 시즌 경기기록 있는 league_id를
    # 1회 SELECT로 모아 set으로 조회 (검색 결과가 수십~수백 개여도 쿼리 1회).
    sim_ids = {r["league_id"] for r in c.execute(
        "SELECT DISTINCT league_id FROM match_results WHERE season=?",
        (season,)).fetchall()}
    conn.close()

    for r in rows:
        r["simulated"] = r["id"] in sim_ids
    return rows


# ─────────────────────────────────────────
# 2. 리그 순위표 (지연 시뮬레이션 트리거)
# ─────────────────────────────────────────
def get_or_simulate_league_standings(league_id, season=None, year=None):
    """이 리그에 이번 시즌 경기기록이 없으면 그 자리에서 일정생성+시뮬 후
    순위표를 반환한다. 이미 있으면 추가 시뮬레이션 없이 바로 반환
    (매번 재시뮬하지 않음 — 한 번 살아난 리그는 계속 그 결과를 유지).
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

    newly_simulated = False
    if cnt == 0:
        generate_season_schedule(league_id, season, year)
        _sim_league_full(league_id, season)
        newly_simulated = True

    standings = get_league_standings(league_id, season=season)
    return standings, newly_simulated


def is_my_league(league_id):
    """지금 내가 뛰고 있는 리그인지 여부 (리셋 버튼 노출 여부 판단용)."""
    from game_engine import get_player
    p = get_player()
    return bool(p and p.get("current_league_id") == league_id)


def reset_league_simulation(league_id, season=None):
    """이 리그의 이번 시즌 경기기록을 전부 지워서 '미시뮬' 상태로 되돌린다
    (라이브 배지를 다시 끄는 기능). get_league_standings는 항상 match_results
    에서 그때그때 직접 집계하므로(teams 테이블 누적 컬럼을 안 씀), 이 삭제
    한 번으로 안전하게 원상복구된다 — 다른 데이터는 건드릴 필요가 없다.

    [안전장치] 내가 지금 뛰고 있는 리그는 리셋 대상에서 제외한다. 그 리그는
    이 브라우저 밖에서도(스케줄 화면, 승강제, 시즌종료 처리 등) 실시간으로
    쓰이고 있어서, 지우면 내 커리어 진행 자체가 꼬일 수 있다. 다른 675개
    '구경만 하는' 리그와는 성격이 다르다.
    """
    from game_engine import get_state, get_player
    st = get_state()
    if season is None:
        season = st["current_season"] if st else 1

    p = get_player()
    if p and p.get("current_league_id") == league_id:
        return False  # 내 리그는 리셋 불가

    conn = get_conn()
    conn.execute(
        "DELETE FROM match_results WHERE league_id=? AND season=?",
        (league_id, season))
    conn.commit()
    conn.close()
    return True


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