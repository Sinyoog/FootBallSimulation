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
def search_teams(name_query=None, continent=None, country_id=None, grade=None, tier=None, limit=40):
    """[2026-07 신설] 직접 지원(팀 검색) 화면용 — search_leagues와 달리 팀을
    1건 1행으로 바로 반환한다(리그 단위가 아니라 팀 단위 선택이 필요하므로).
    name_query는 팀명/리그명/국가명 어디에든 매치된다. 팀 평균 OVR도 같이
    계산해서 반환 — 지원 화면에서 "이 팀 수준"을 가늠하는 참고용.

    [주의] grade는 국가대표 등급(countries.grade)이 아니라 '클럽 리그 등급'
    (constants.get_league_grade)이다 — 잉글랜드처럼 둘이 다른 나라가 있어서
    (국대는 S급이어도 클럽리그는 SS급 단독) SQL의 cn.grade로 바로 필터링할
    수 없다. 전체 국가(200여 개, 부담 없는 크기)를 먼저 조회해 실제 클럽
    등급을 계산한 뒤, 그 등급에 해당하는 country_id만 골라 팀 쿼리에 건다.

    [버그수정 2026-07] 예전엔 "ORDER BY l.tier"로 정렬해서, 검색어 없이
    그냥 훑어볼 땐 1부 팀 수가 워낙 많아 LIMIT(기본 30~40)이 1부만으로
    다 채워지고 3부·5부 같은 하위 리그는 사실상 볼 수가 없었다(현실에선
    선수가 하위리그에 직접 지원하는 일이 흔한데 그게 안 보이는 문제).
    tier 파라미터로 특정 부수를 콕 집어 검색할 수 있게 하고, 기본
    정렬은 tier 우선순위 대신 랜덤으로 바꿔 여러 부수가 고르게 섞여
    나오게 한다.

    [검색어 내 부수 파싱 2026-07] "K리그 3부", "대한민국 3부", 그냥 "3부"처럼
    검색어 안에 "숫자+부" 표현이 섞여 있으면 자동으로 그 숫자를 tier로
    파싱해서 적용하고, 나머지 텍스트만 팀/리그/국가명 매칭에 쓴다.
    tier 파라미터를 별도로 넘긴 경우 그것과 함께(AND) 적용된다."""
    import re
    from constants import get_league_grade
    conn = get_conn()

    if name_query:
        m = re.search(r"(\d+)\s*부", name_query)
        if m:
            parsed_tier = int(m.group(1))
            tier = parsed_tier if tier is None else tier   # 명시적 tier 인자가 우선
            name_query = (name_query[:m.start()] + name_query[m.end():]).strip() or None

    grade_country_ids = None
    if grade:
        all_c = conn.execute("SELECT id, name, grade FROM countries").fetchall()
        grade_country_ids = [r["id"] for r in all_c if get_league_grade(r["name"], r["grade"]) == grade]
        if not grade_country_ids:
            conn.close()
            return []

    q = ("SELECT t.id, t.name, l.id as league_id, l.name as league_name, l.tier, "
         "cn.id as country_id, cn.name as country, cn.flag as flag, cn.grade as cgrade, "
         "cn.continent as continent, "
         "(SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) as avg_ovr "
         "FROM teams t JOIN leagues l ON t.league_id=l.id "
         "JOIN countries cn ON l.country_id=cn.id WHERE 1=1")
    params = []
    if continent:
        q += " AND cn.continent=?"; params.append(continent)
    if country_id:
        q += " AND cn.id=?"; params.append(country_id)
    if tier:
        q += " AND l.tier=?"; params.append(tier)
    if grade_country_ids is not None:
        q += " AND cn.id IN (%s)" % ",".join("?" * len(grade_country_ids))
        params += grade_country_ids
    if name_query:
        like = f"%{name_query}%"
        q += " AND (t.name LIKE ? OR l.name LIKE ? OR cn.name LIKE ?)"
        params += [like, like, like]
    q += " ORDER BY RANDOM() LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    for r in rows:
        r["grade"] = get_league_grade(r["country"], r["cgrade"])
    return rows


def list_continents():
    """존재하는 대륙 목록 (countries.continent 기준, 오세아니아 등 포함)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT continent FROM countries ORDER BY continent").fetchall()
    conn.close()
    return [r["continent"] for r in rows]


def list_countries(continent=None, grade=None):
    """대륙/등급으로 필터링한 국가 목록 (등급순 정렬).

    [버그수정 2026-07] grade는 '클럽 리그 등급'(constants.get_league_grade)
    이어야 하는데, 지금까지는 countries.grade(국가대표 등급, FIFA랭킹 기반)를
    그대로 표시/필터링했다. 그래서 국대는 강해도 클럽리그는 약한 나라
    (모로코·나이지리아·이란 등)가 세계기록실에서 실제보다 훨씬 높은 등급으로
    보였다. search_teams()가 이미 올바르게 처리하던 방식과 동일하게 맞춘다 —
    전체 국가를 조회한 뒤 파이썬에서 실제 클럽 등급을 계산해 필터/정렬한다."""
    from constants import get_league_grade
    conn = get_conn()
    q = "SELECT id, name, flag, grade, continent FROM countries WHERE 1=1"
    params = []
    if continent:
        q += " AND continent=?"; params.append(continent)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    for r in rows:
        r["grade"] = get_league_grade(r["name"], r["grade"])
    if grade:
        rows = [r for r in rows if r["grade"] == grade]
    _order = {g: i for i, g in enumerate(_GRADE_ORDER)}
    rows.sort(key=lambda r: (_order.get(r["grade"], 99), r["name"]))
    return rows


# 국가 등급 고정 순서(강함→약함). DB에 실제 존재하는 값만 걸러서 쓴다.
_GRADE_ORDER = ["SS", "S", "A", "B", "C", "D", "E", "F"]


def list_grades():
    """실제 존재하는 '클럽 리그' 등급 목록을 정해진 순서(SS>S>A>...)로 반환.
    [버그수정 2026-07] countries.grade(국대 등급) 원본이 아니라
    get_league_grade()로 계산한 클럽 리그 등급 기준으로 바꿈 — 화면에
    실제로 표시/필터링되는 값과 일치시키기 위함."""
    from constants import get_league_grade
    conn = get_conn()
    rows = conn.execute("SELECT name, grade FROM countries").fetchall()
    conn.close()
    existing = {get_league_grade(r["name"], r["grade"]) for r in rows}
    return [g for g in _GRADE_ORDER if g in existing]


def search_leagues(continent=None, country_id=None, name_query=None, grade=None):
    """조건에 맞는 리그 목록. 이제 모든 리그가 시즌 시작 시 일정을 미리 받고
    매주 실시간으로 결과가 채워지므로, 예전의 '이번 시즌 시뮬 여부(simulated)'
    배지는 더 이상 의미가 없어 반환하지 않는다.

    name_query는 리그명·국가명뿐 아니라 팀명도 매치한다 — 예를 들어 "리버풀"을
    검색하면 리버풀이 뛰고 있는 리그(잉글랜드 프리미어리그)가 검색 결과에
    뜬다. 이때 결과 dict의 "matched_team"에 실제로 일치한 팀명을 담아, 화면에서
    "왜 이 리그가 나왔는지"(팀명 때문인지) 알 수 있게 한다.

    [버그수정 2026-07] grade 표시/필터를 countries.grade(국가대표 등급)
    그대로 쓰고 있었는데, 이건 search_teams()의 주석에도 명시돼 있듯 '클럽
    리그 등급'과 다르다(국대는 강해도 클럽리그는 약한 나라가 있음 — 모로코·
    나이지리아·이란 등). search_teams()와 동일하게 get_league_grade()로
    계산한 값을 쓰도록 통일한다.

    [최적화] 팀명 매칭을 리그마다 서브쿼리 2번(매치 팀명 조회 + 존재 여부
    확인)씩 따로 날리던 첫 버전은, 검색창에 한 글자 칠 때마다(textChanged로
    매번 재호출됨) 팀 테이블 전체를 리그 수만큼 반복 스캔해 체감 렉으로
    이어졌다. teams를 LEFT JOIN해서 한 번만 훑고 GROUP BY로 리그당 1행으로
    모으는 방식으로 바꿔 쿼리 1회로 끝낸다.
    """
    from constants import get_league_grade
    conn = get_conn()
    c = conn.cursor()

    # grade 필터는 클럽 리그 등급 기준이라 SQL의 cn.grade로 바로 못 거르고,
    # search_teams()와 동일하게 전체 국가를 먼저 계산해 country_id로 변환한다.
    grade_country_ids = None
    if grade:
        all_c = conn.execute("SELECT id, name, grade FROM countries").fetchall()
        grade_country_ids = [r["id"] for r in all_c if get_league_grade(r["name"], r["grade"]) == grade]
        if not grade_country_ids:
            conn.close()
            return []

    if name_query:
        like = f"%{name_query}%"
        q = ("SELECT l.id, l.name, l.tier, cn.id as country_id, cn.name as country, "
             "cn.flag as flag, cn.grade as cgrade, cn.continent as continent, "
             "MAX(CASE WHEN t.name LIKE ? THEN t.name END) as matched_team "
             "FROM leagues l JOIN countries cn ON l.country_id = cn.id "
             "LEFT JOIN teams t ON t.league_id = l.id WHERE 1=1")
        params = [like]
        if continent:
            q += " AND cn.continent=?"; params.append(continent)
        if country_id:
            q += " AND cn.id=?"; params.append(country_id)
        if grade_country_ids is not None:
            q += " AND cn.id IN (%s)" % ",".join("?" * len(grade_country_ids))
            params += grade_country_ids
        q += " AND (l.name LIKE ? OR cn.name LIKE ? OR t.name LIKE ?)"
        params += [like, like, like]
        # cn.grade(국대등급) 기준 정렬은 더 이상 의미가 없어 제거 — 클럽 등급
        # 기준 정렬은 아래에서 파이썬으로 다시 한다.
        q += " GROUP BY l.id ORDER BY cn.name, l.tier"
    else:
        q = ("SELECT l.id, l.name, l.tier, cn.id as country_id, cn.name as country, "
             "cn.flag as flag, cn.grade as cgrade, cn.continent as continent "
             "FROM leagues l JOIN countries cn ON l.country_id = cn.id WHERE 1=1")
        params = []
        if continent:
            q += " AND cn.continent=?"; params.append(continent)
        if country_id:
            q += " AND cn.id=?"; params.append(country_id)
        if grade_country_ids is not None:
            q += " AND cn.id IN (%s)" % ",".join("?" * len(grade_country_ids))
            params += grade_country_ids
        q += " ORDER BY cn.name, l.tier"

    rows = [dict(r) for r in c.execute(q, params).fetchall()]
    conn.close()
    for r in rows:
        r["grade"] = get_league_grade(r["country"], r["cgrade"])
    _order = {g: i for i, g in enumerate(_GRADE_ORDER)}
    rows.sort(key=lambda r: (_order.get(r["grade"], 99), r["country"], r["tier"]))
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
    # [2026-07 수정] archive_old_seasons()로 과거 시즌이 match_results_archive로
    # 옮겨지므로, 여기서 match_results만 세면 이미 완료·보관된 과거 시즌을
    # "일정이 없다(cnt==0)"고 오판해 그 시즌을 엉뚱하게 재생성/재시뮬레이션
    # 하게 된다 — 두 테이블을 합쳐서 세야 한다.
    cnt = conn.execute(
        """SELECT (SELECT COUNT(*) FROM match_results WHERE league_id=? AND season=?)
                 + (SELECT COUNT(*) FROM match_results_archive WHERE league_id=? AND season=?)
           AS n""",
        (league_id, season, league_id, season)).fetchone()["n"]
    conn.close()

    if cnt == 0:
        generate_season_schedule(league_id, season, year)
        _sim_league_full(league_id, season)

    return get_league_standings(league_id, season=season)


def league_has_lower_tier(league_id):
    """이 리그보다 한 단계 아래 티어가 그 나라에 존재하는지.
    최하위 티어 리그는 애초에 내려갈 곳이 없어 강등 자체가 없다.
    역대 우승팀 표에서 강등팀 목록을 표시할지 판단하는 용도.
    """
    conn = get_conn()
    row = conn.execute("SELECT country_id, tier FROM leagues WHERE id=?",
                        (league_id,)).fetchone()
    if not row:
        conn.close()
        return False
    lower = conn.execute(
        "SELECT 1 FROM leagues WHERE country_id=? AND tier=?",
        (row["country_id"], row["tier"] + 1)).fetchone()
    conn.close()
    return bool(lower)


def get_league_champions(league_id, limit=30):
    """이 리그의 시즌별 1~4위 + 실제 승격팀 전체/강등팀 전체 목록.
    실제로 경기가 진행된 시즌만 대상이며(한 번도 경기가 없었던 시즌은 제외),
    새 테이블 없이 match_results를 시즌 단위로 그때그때 집계해서 계산한다
    (승강전 처리와 동일한 방식).최신 시즌부터 최대 limit개.

    [2026-07 개편] 예전엔 승강제가 '인접 티어 사이 1-up-1-down' 고정이라
    '꼴찌 1팀 = 강등팀'이 항상 성립했지만, 이제 리그 규모별로 승강 인원이
    다르므로(game_engine._promo_releg_count) '꼴찌'가 아니라 '실제로
    강등되는 팀 전체'를, 승격도 마찬가지로 '실제로 승격되는 팀 전체'를
    game_engine._process_promotion_relegation과 동일한 기준으로 계산해
    반환한다.
    """
    from game_engine import get_league_standings, _promo_releg_count
    conn = get_conn()

    lg_row = conn.execute("SELECT country_id, tier FROM leagues WHERE id=?",
                          (league_id,)).fetchone()
    upper_lid = None
    has_lower = False
    if lg_row:
        if lg_row["tier"] > 1:
            ur = conn.execute(
                "SELECT id FROM leagues WHERE country_id=? AND tier=?",
                (lg_row["country_id"], lg_row["tier"] - 1)).fetchone()
            upper_lid = ur["id"] if ur else None
        lr = conn.execute(
            "SELECT 1 FROM leagues WHERE country_id=? AND tier=?",
            (lg_row["country_id"], lg_row["tier"] + 1)).fetchone()
        has_lower = lr is not None

    # [2026-07 수정] archive_old_seasons()로 과거 시즌이 match_results_archive로
    # 옮겨지므로, 여기서도 두 테이블을 합쳐서 시즌 목록을 뽑아야 예전처럼
    # 모든 완료 시즌이 다 나온다.
    season_rows = conn.execute(
        """SELECT DISTINCT season, year FROM match_results
           WHERE league_id=? AND home_score>=0
           UNION
           SELECT DISTINCT season, year FROM match_results_archive
           WHERE league_id=? AND home_score>=0
           ORDER BY season DESC LIMIT ?""",
        (league_id, league_id, limit)).fetchall()

    out = []
    for sr in season_rows:
        standings = get_league_standings(league_id, season=sr["season"], conn=conn)
        if not standings:
            continue
        n = len(standings)

        # 승격팀 전체: '상위 티어' 크기 기준으로 산정한 인원만큼 이 리그
        # 상위 순위부터(game_engine과 동일 로직 - 상위 티어 팀 수로 결정).
        # relegated와 동일하게 rank(실제 이 리그에서의 최종 순위)도 함께 담아
        # 화면에서 "2위(승격)"처럼 순위별 컬럼으로 보여줄 수 있게 한다.
        promoted = []
        if upper_lid:
            upper_standings = get_league_standings(upper_lid, season=sr["season"], conn=conn)
            if upper_standings:
                n_promo = min(_promo_releg_count(len(upper_standings)), n)
                promoted = [{"rank": i + 1, "name": standings[i]["name"]}
                            for i in range(n_promo)]

        # 강등팀 전체: '이 리그 자신'의 크기 기준으로 산정한 인원만큼 하위 순위부터.
        relegated = []
        if has_lower:
            n_releg = min(_promo_releg_count(n), n)
            # 팀명뿐 아니라 실제 최종 순위(예: 18위)도 함께 담아서 화면에서
            # "18위 팀명" 형태로 보여줄 수 있게 한다.
            relegated = [{"rank": n - i, "name": standings[n - 1 - i]["name"]}
                         for i in range(n_releg)]

        out.append({
            "season": sr["season"], "year": sr["year"],
            "first":  standings[0]["name"] if n > 0 else "-",
            "second": standings[1]["name"] if n > 1 else "-",
            "third":  standings[2]["name"] if n > 2 else "-",
            "fourth": standings[3]["name"] if n > 3 else "-",  # [2026-07 추가] 3위까지만 기록하던 것을 4위까지 확장
            "promoted": promoted,
            "relegated": relegated,
        })
    conn.close()
    return out


# [실시간 전환] 예전엔 유저가 열어본 리그만 '라이브' 상태였고, 그걸 다시
# '미시뮬'로 되돌리는 reset_league_simulation() / 되돌리기 대상에서 내 리그를
# 제외하는 is_my_league()가 있었다. 지금은 모든 리그가 항상 실시간으로 진행
# 중이라 되돌릴 '시뮬 이전 상태' 자체가 없으므로 두 함수 모두 제거했다.


# ─────────────────────────────────────────
# 3.5. 역대 국내 컵대회 기록 (2026-07 신설)
# ─────────────────────────────────────────
def _get_cup_placements(tournament_id, conn):
    """결승(+3·4위전) cup_matches 결과로 1~4위 team_id를 도출.
    cup_history는 '내 팀'의 결과만 기록하므로, 모든 나라/모든 시즌의
    우승/준우승을 보여주려면 이렇게 경기 결과에서 직접 뽑아야 한다.
    [주의] 대회 하나만 볼 때 쓰는 함수 — 여러 대회를 한꺼번에 나열할 때는
    _batch_cup_placements()를 써서 대회 수만큼 쿼리가 늘어나지 않게 한다."""
    from cup_engine import _winner_of
    fm = conn.execute(
        """SELECT * FROM cup_matches WHERE tournament_id=? AND round_name='결승'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tournament_id,)).fetchone()
    if not fm:
        return None
    fm = dict(fm)
    winner = _winner_of(fm)
    runner_up = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    third = fourth = None
    tp = conn.execute(
        """SELECT * FROM cup_matches WHERE tournament_id=? AND round_name='3·4위전'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tournament_id,)).fetchone()
    if tp:
        tp = dict(tp)
        third = _winner_of(tp)
        fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]

    return {"winner": winner, "runner_up": runner_up, "third": third, "fourth": fourth}


def _batch_cup_placements(tournament_ids, conn):
    """[2026-07 성능개선] _get_cup_placements를 대회마다 호출하면 대회 하나당
    SELECT 2번(결승/3·4위전)이 나가 목록 조회(최대 30개)마다 최대 60번 왕복이
    발생했다. tournament_id IN 배치 쿼리 1번으로 관련 경기를 전부 가져온 뒤
    파이썬에서 (tournament_id, round_name)별로 묶어 마지막 행(=원래
    'ORDER BY id DESC LIMIT 1'과 동일)만 취한다 — 판정 로직/반환값은
    _get_cup_placements와 완전히 동일, 반환 형태만 {tournament_id: dict}."""
    from cup_engine import _winner_of
    if not tournament_ids:
        return {}
    ph = ",".join("?" * len(tournament_ids))
    rows = conn.execute(
        f"""SELECT * FROM cup_matches WHERE tournament_id IN ({ph})
            AND round_name IN ('결승','3·4위전') AND home_score>=0
            ORDER BY id""", list(tournament_ids)).fetchall()
    # (tournament_id, round_name) -> 마지막(최대 id) 행. id ASC로 정렬해서
    # 순회하며 계속 덮어쓰면 자동으로 "그 그룹의 최대 id 행"만 남는다.
    latest = {}
    for r in rows:
        latest[(r["tournament_id"], r["round_name"])] = dict(r)

    out = {}
    for tid in tournament_ids:
        fm = latest.get((tid, "결승"))
        if not fm:
            continue
        winner = _winner_of(fm)
        runner_up = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]
        third = fourth = None
        tp = latest.get((tid, "3·4위전"))
        if tp:
            third = _winner_of(tp)
            fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]
        out[tid] = {"winner": winner, "runner_up": runner_up, "third": third, "fourth": fourth}
    return out


def get_cup_history(country_id, limit=30):
    """특정 국가의 역대 국내 컵대회(FA컵식) 우승/준우승/3·4위 기록.

    [2026-07 전체 국가 확장] 예전엔 cup_engine이 성능상 '내 팀이 속한 나라'
    한정으로만 컵대회를 생성해서, 실제로 뛰어본 나라만 기록이 쌓이고 나머지는
    항상 빈 목록이었다(신민용 리포트: "컵대회 기록이 다 없다고 뜬다"). 이제
    매 시즌 5주차에 리그가 있는 나라 전부의 컵대회가 생성/진행되므로, 어느
    나라를 검색해도 완료된 시즌부터 기록이 쌓인다.
    """
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT id, year, name FROM cup_tournaments
           WHERE country_id=? AND status='done'
           ORDER BY year DESC LIMIT ?""", (country_id, limit)).fetchall()]

    placements_by_row = []
    all_tids = set()
    tournament_ids = [r["id"] for r in rows]
    placements_map = _batch_cup_placements(tournament_ids, conn)
    for r in rows:
        pl = placements_map.get(r["id"])
        placements_by_row.append(pl)
        if pl:
            for key in ("winner", "runner_up", "third", "fourth"):
                if pl.get(key):
                    all_tids.add(r["id"])

    # [최적화] 팀명을 시즌×순위(최대 30×4=120회)마다 개별 SELECT 하던 것을,
    # 등장한 대회(tournament_id) 전체의 cup_entries를 1회 IN 쿼리로 미리
    # 읽어 {(tournament_id, team_id): team_name} 캐시로 대체. 팀명은
    # cup_entries에 대회별로 저장되므로(원본과 동일하게) 스코프는 그대로 유지.
    # [2026-07 신설] 같은 캐시에 tier(그 시즌 그 팀의 소속 부수)도 함께 담아
    # UI에서 "팀명 (N부)"로 표시할 수 있게 한다 — cup_entries.tier는 이미
    # 대회 생성 시 저장돼 있던 값이라 추가 조회 없이 같은 쿼리로 딸려온다.
    name_cache = {}
    tier_cache = {}
    if all_tids:
        ph = ",".join("?" * len(all_tids))
        for e in conn.execute(
                f"SELECT tournament_id, team_id, team_name, tier FROM cup_entries "
                f"WHERE tournament_id IN ({ph})", list(all_tids)).fetchall():
            name_cache[(e["tournament_id"], e["team_id"])] = e["team_name"]
            tier_cache[(e["tournament_id"], e["team_id"])] = e["tier"]

    def _nm(tid_, team_id_):
        if not team_id_:
            return "-"
        return name_cache.get((tid_, team_id_), "?")

    def _tier(tid_, team_id_):
        if not team_id_:
            return None
        return tier_cache.get((tid_, team_id_))

    out = []
    for r, pl in zip(rows, placements_by_row):
        if not pl:
            continue
        out.append({
            "id": r["id"],  # [2026-07 추가] 더블클릭 상세보기용 대회 id
            "year": r["year"], "name": r["name"],
            "winner": _nm(r["id"], pl["winner"]), "runner_up": _nm(r["id"], pl["runner_up"]),
            "third": _nm(r["id"], pl["third"]), "fourth": _nm(r["id"], pl["fourth"]),
            "winner_tier": _tier(r["id"], pl["winner"]), "runner_up_tier": _tier(r["id"], pl["runner_up"]),
            "third_tier": _tier(r["id"], pl["third"]), "fourth_tier": _tier(r["id"], pl["fourth"]),
        })
    conn.close()
    return out


def get_cup_tournament_detail(tournament_id):
    """[2026-07 신설] 국내 컵대회 한 대회의 라운드별 대진 상세 — 조별리그가
    없는 순수 토너먼트라 챔피언스리그의 knockout 부분과 같은 형식으로만
    반환한다(groups는 항상 빈 dict). world_browser_window.TournamentDetailDialog가
    이미 이 형식(team_based=True)을 그대로 그려줄 수 있어 UI는 재사용한다."""
    conn = get_conn(); c = conn.cursor()
    rows = c.execute(
        """SELECT round_name, round_idx, slot, home_team_id, away_team_id,
                  home_score, away_score, pso_winner, pso_score
           FROM cup_matches WHERE tournament_id=? AND home_score>=0
           ORDER BY round_idx, slot""", (tournament_id,)).fetchall()
    entry_rows = c.execute(
        "SELECT team_id, team_name, tier FROM cup_entries WHERE tournament_id=?",
        (tournament_id,)).fetchall()
    conn.close()
    # [2026-07 신설, 신민용 요청] 팀명 옆에 "(몇부)"를 붙이되, 지금 소속이 아니라
    # 이 컵대회 당시(cup_entries.tier — 참가 시점에 고정 저장돼 이후 강등/
    # 승격과 무관) 티어를 보여준다.
    name_by_id = {r["team_id"]: f"{r['team_name']} ({r['tier']}부)" for r in entry_rows}

    by_round = {}
    order = []
    for m in rows:
        key = (m["round_idx"], m["round_name"])
        if key not in by_round:
            by_round[key] = []
            order.append(key)
        by_round[key].append({
            "home_info": {"team_name": name_by_id.get(m["home_team_id"], "?"),
                          "flag": "", "team_id": m["home_team_id"]},
            "away_info": {"team_name": name_by_id.get(m["away_team_id"], "?"),
                          "flag": "", "team_id": m["away_team_id"]},
            "home_score": m["home_score"], "away_score": m["away_score"],
            "pso_winner": m["pso_winner"],
        })
    knockout = [{"stage": key[1], "stage_ko": key[1], "matches": by_round[key]} for key in order]
    return {"groups": {}, "knockout": knockout}


def has_cup_data(country_id):
    """이 나라에 생성된 컵대회 기록이 하나라도 있는지(검색 목록 배지용)."""
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM cup_tournaments WHERE country_id=?",
        (country_id,)).fetchone()["n"]
    conn.close()
    return n > 0


# ─────────────────────────────────────────
# 3. 역대 챔피언스리그 기록
# ─────────────────────────────────────────
def _get_cl_placements(tournament_id, conn):
    """결승(F)+3/4위전(TP) cl_matches 결과로 1~4위 team_id를 도출.
    intl_engine 쪽 _get_placements와 동일한 패턴, team_id(정수) 기준만 다름.
    [주의] 대회 하나만 볼 때 쓰는 함수 — 여러 대회를 한꺼번에 나열할 때는
    _batch_cl_placements()를 써서 대회 수만큼 쿼리가 늘어나지 않게 한다."""
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


def _batch_cl_placements(tournament_ids, conn):
    """[2026-07 성능개선] _get_cl_placements의 대회당 SELECT 2번을
    tournament_id IN 배치 쿼리 1번으로 통합 (get_cl_history의 limit=100
    기본값 기준 최대 200회 왕복 → 1회). 판정 로직은 완전히 동일하고
    반환 형태만 {tournament_id: dict}."""
    from champions_engine import _winner_of
    if not tournament_ids:
        return {}
    ph = ",".join("?" * len(tournament_ids))
    rows = conn.execute(
        f"""SELECT * FROM cl_matches WHERE tournament_id IN ({ph})
            AND stage IN ('F','TP') AND home_score>=0
            ORDER BY id""", list(tournament_ids)).fetchall()
    latest = {}
    for r in rows:
        latest[(r["tournament_id"], r["stage"])] = dict(r)

    out = {}
    for tid in tournament_ids:
        fm = latest.get((tid, "F"))
        if not fm:
            continue
        winner = _winner_of(fm)
        runner_up = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]
        third = fourth = None
        tp = latest.get((tid, "TP"))
        if tp:
            third = _winner_of(tp)
            fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]
        out[tid] = {"winner": winner, "runner_up": runner_up, "third": third, "fourth": fourth}
    return out


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
    tournament_ids = [r["id"] for r in rows]
    placements_map = _batch_cl_placements(tournament_ids, conn)
    for r in rows:
        pl = placements_map.get(r["id"]) or {}
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


def _batch_placements(tournament_ids, conn):
    """[2026-07 성능개선] _get_placements의 대회당 SELECT 2번을 tournament_id
    IN 배치 쿼리 1번으로 통합 (get_wc_history/get_continental_cup_history의
    limit=100 기본값 기준 최대 200회 왕복 → 1회). _get_placements는 ORDER BY
    없이 fetchone()으로 '첫 매치'를 취했으므로, 여기서도 id 오름차순으로
    순회하며 그룹당 처음 나온 행만 남겨 동일한 결과를 보장한다."""
    from intl_engine import _winner_of
    if not tournament_ids:
        return {}
    ph = ",".join("?" * len(tournament_ids))
    rows = conn.execute(
        f"""SELECT * FROM intl_matches WHERE tournament_id IN ({ph})
            AND stage IN ('F','TP') AND home_score>=0
            ORDER BY id""", list(tournament_ids)).fetchall()
    first = {}
    for r in rows:
        key = (r["tournament_id"], r["stage"])
        if key not in first:
            first[key] = dict(r)

    out = {}
    for tid in tournament_ids:
        fm = first.get((tid, "F"))
        if not fm:
            continue
        winner = _winner_of(fm)
        runner_up = fm["away"] if winner == fm["home"] else fm["home"]
        third = fourth = None
        tp = first.get((tid, "TP"))
        if tp:
            third = _winner_of(tp)
            fourth = tp["away"] if third == tp["home"] else tp["home"]
        out[tid] = {"winner": winner, "runner_up": runner_up, "third": third, "fourth": fourth}
    return out


def _attach_placements_and_flags(rows, conn):
    """intl_tournaments 행 목록에 1~4위 국가명 + 국기를 채워 넣는다.
    [최적화] 국기는 대회마다 개별 조회하지 않고, 전체 대회에서 등장한
    국가명을 모아 1회 IN 쿼리로 일괄 조회한다. [2026-07] 대회별 결승/3·4위전
    조회 자체도 _batch_placements()로 배치 처리해 대회 수만큼 왕복이
    늘어나지 않게 한다."""
    placements_by_row = []
    all_names = set()
    tournament_ids = [r["id"] for r in rows]
    placements_map = _batch_placements(tournament_ids, conn)
    for r in rows:
        pl = placements_map.get(r["id"]) or {}
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
_CL_KO_STAGE_ORDER = ["PO", "R32", "R16", "QF", "SF", "TP", "F"]


def get_cl_tournament_detail(tournament_id):
    """챔피언스리그 한 대회의 리그 스테이지 순위표 + 토너먼트(녹아웃) 대진.
    [2026-07 스위스 방식 개편] 조별리그가 없어져서 groups 대신 단일
    league_standings 리스트를 반환한다. 기존 groups 키를 참조하던 옛
    UI가 있다면 빈 dict로라도 동작하도록 groups=[]는 계속 넣어둔다."""
    from champions_engine import STAGE_KO
    conn = get_conn(); c = conn.cursor()

    entries = [dict(r) for r in c.execute(
        "SELECT team_id, team_name, flag, country, grade FROM cl_entries "
        "WHERE tournament_id=?", (tournament_id,)).fetchall()]
    # [2026-07 신설, 신민용 요청] 리그 스테이지 순위표 색칠(직행/플레이오프)에
    # 대륙별 컷 라인이 필요해서 continent도 같이 조회한다.
    t_row = c.execute("SELECT continent FROM cl_tournaments WHERE id=?", (tournament_id,)).fetchone()
    continent = t_row["continent"] if t_row else None
    league_tbl = {e["team_id"]: {
        "team_id": e["team_id"], "name": e["team_name"], "flag": e["flag"],
        "country": e["country"], "grade": e["grade"],
        "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0} for e in entries}

    for m in c.execute(
            "SELECT home_team_id, away_team_id, home_score, away_score "
            "FROM cl_matches WHERE tournament_id=? AND stage='league' "
            "AND home_score>=0", (tournament_id,)).fetchall():
        h, a = league_tbl.get(m["home_team_id"]), league_tbl.get(m["away_team_id"])
        if not h or not a:
            continue
        h["gf"] += m["home_score"]; h["ga"] += m["away_score"]
        a["gf"] += m["away_score"]; a["ga"] += m["home_score"]
        if m["home_score"] > m["away_score"]:   h["wins"] += 1;  a["losses"] += 1
        elif m["home_score"] < m["away_score"]: a["wins"] += 1;  h["losses"] += 1
        else:                                   h["draws"] += 1; a["draws"] += 1
    league_standings = list(league_tbl.values())
    for r in league_standings:
        r["pts"] = r["wins"] * 3 + r["draws"]
        r["gd"] = r["gf"] - r["ga"]
    league_standings.sort(key=lambda r: (-r["pts"], -r["gd"], -r["gf"]))

    ko_rows = c.execute(
        "SELECT stage, home_team_id, away_team_id, home_score, away_score, "
        "pso_winner, pso_score FROM cl_matches WHERE tournament_id=? AND "
        "stage NOT IN ('league') AND home_score>=0 ORDER BY id",
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

    return {"groups": {}, "league_standings": league_standings, "knockout": knockout,
            "continent": continent}


# ─────────────────────────────────────────
# 7. 역대 클럽 월드컵 기록 (2026-07 신설)
# ─────────────────────────────────────────
_CWC_KO_STAGE_ORDER = ["R16", "QF", "SF", "TP", "F"]


def _batch_cwc_placements(tournament_ids, conn):
    """get_cl_history의 _batch_cl_placements와 완전히 동일한 패턴 —
    cwc_matches는 cl_matches와 스키마가 같으므로 champions_engine._winner_of를
    그대로 재사용한다."""
    from champions_engine import _winner_of
    if not tournament_ids:
        return {}
    ph = ",".join("?" * len(tournament_ids))
    rows = conn.execute(
        f"""SELECT * FROM cwc_matches WHERE tournament_id IN ({ph})
            AND stage IN ('F','TP') AND home_score>=0
            ORDER BY id""", list(tournament_ids)).fetchall()
    latest = {}
    for r in rows:
        latest[(r["tournament_id"], r["stage"])] = dict(r)

    out = {}
    for tid in tournament_ids:
        fm = latest.get((tid, "F"))
        if not fm:
            continue
        winner = _winner_of(fm)
        runner_up = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]
        third = fourth = None
        tp = latest.get((tid, "TP"))
        if tp:
            third = _winner_of(tp)
            fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]
        out[tid] = {"winner": winner, "runner_up": runner_up, "third": third, "fourth": fourth}
    return out


def get_cwc_history(limit=100):
    """완료된 클럽 월드컵 대회의 연도별 1~4위(팀명+국가) 목록.
    get_cl_history와 완전히 동일한 구조 — 4년에 한 번뿐이라 limit 기본값
    100이면 사실상 게임 전체 기간을 다 담는다."""
    conn = get_conn(); c = conn.cursor()
    rows = [dict(r) for r in c.execute(
        """SELECT t.id, t.year, t.name
           FROM cwc_tournaments t
           WHERE t.status='done' AND t.winner_team_id != 0
           ORDER BY t.year DESC, t.id DESC LIMIT ?""", (limit,)).fetchall()]

    placements_by_row = []
    all_tids = set()
    tournament_ids = [r["id"] for r in rows]
    placements_map = _batch_cwc_placements(tournament_ids, conn)
    for r in rows:
        pl = placements_map.get(r["id"]) or {}
        placements_by_row.append(pl)
        for key in ("winner", "runner_up", "third", "fourth"):
            if pl.get(key):
                all_tids.add(pl[key])

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


def get_cwc_tournament_detail(tournament_id):
    """클럽 월드컵 한 대회의 8개조 조별리그 순위표 + 토너먼트(녹아웃) 대진.
    get_cl_tournament_detail과 같은 반환 형태를 쓰되, '리그 스테이지'
    대신 '조별리그'(8조×4팀)이므로 groups 키를 실제로 채워서 반환한다."""
    conn = get_conn(); c = conn.cursor()

    entries = [dict(r) for r in c.execute(
        "SELECT team_id, team_name, country, grp, grade FROM cwc_entries "
        "WHERE tournament_id=?", (tournament_id,)).fetchall()]
    team_info = {e["team_id"]: e for e in entries}

    groups_tbl = {}
    for e in entries:
        g = e["grp"] or "?"
        groups_tbl.setdefault(g, {})[e["team_id"]] = {
            "team_id": e["team_id"], "name": e["team_name"], "flag": "",
            "country": e["country"], "grade": e["grade"],
            "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0}

    for m in c.execute(
            "SELECT home_team_id, away_team_id, home_score, away_score "
            "FROM cwc_matches WHERE tournament_id=? AND stage='group' "
            "AND home_score>=0", (tournament_id,)).fetchall():
        for g, tbl in groups_tbl.items():
            h, a = tbl.get(m["home_team_id"]), tbl.get(m["away_team_id"])
            if not h or not a:
                continue
            h["gf"] += m["home_score"]; h["ga"] += m["away_score"]
            a["gf"] += m["away_score"]; a["ga"] += m["home_score"]
            if m["home_score"] > m["away_score"]:   h["wins"] += 1;  a["losses"] += 1
            elif m["home_score"] < m["away_score"]: a["wins"] += 1;  h["losses"] += 1
            else:                                   h["draws"] += 1; a["draws"] += 1
            break   # 이 매치는 한 조에만 속하므로 찾으면 바로 중단

    groups = {}
    for g, tbl in sorted(groups_tbl.items()):
        rows = list(tbl.values())
        for r in rows:
            r["pts"] = r["wins"] * 3 + r["draws"]
            r["gd"] = r["gf"] - r["ga"]
        rows.sort(key=lambda r: (-r["pts"], -r["gd"], -r["gf"]))
        groups[g] = rows

    ko_rows = c.execute(
        "SELECT stage, home_team_id, away_team_id, home_score, away_score, "
        "pso_winner, pso_score FROM cwc_matches WHERE tournament_id=? AND "
        "stage!='group' AND home_score>=0 ORDER BY id",
        (tournament_id,)).fetchall()
    conn.close()

    _CWC_STAGE_KO = {"R16": "16강", "QF": "8강", "SF": "4강", "F": "결승", "TP": "3/4위전"}
    ko_by_stage = {}
    for m in ko_rows:
        m = dict(m)
        m["home_info"] = team_info.get(m["home_team_id"], {})
        m["away_info"] = team_info.get(m["away_team_id"], {})
        ko_by_stage.setdefault(m["stage"], []).append(m)
    knockout = [{"stage": s, "stage_ko": _CWC_STAGE_KO.get(s, s), "matches": ko_by_stage[s]}
                for s in _CWC_KO_STAGE_ORDER if s in ko_by_stage]

    return {"groups": groups, "league_standings": [], "knockout": knockout}