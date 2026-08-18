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
def search_teams(name_query=None, continent=None, country_id=None, grade=None, tier=None,
                  limit=40, sort=None):
    """[2026-07 신설] 직접 지원(팀 검색) 화면용 — search_leagues와 달리 팀을
    1건 1행으로 바로 반환한다(리그 단위가 아니라 팀 단위 선택이 필요하므로).
    name_query는 팀명/리그명/국가명 어디에든 매치된다. 팀 평균 OVR도 같이
    계산해서 반환 — 지원 화면에서 "이 팀 수준"을 가늠하는 참고용.
    이 평균 OVR은 캐시된 스냅샷이 아니라 ai_players.ovr(그 팀 선수단의
    현재 값)을 매 호출마다 그대로 집계한 것이라 항상 "지금 시점" 값이다
    (전 시즌 값이 별도로 저장돼 있지 않음).

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
    tier 파라미터를 별도로 넘긴 경우 그것과 함께(AND) 적용된다.

    [2026-08 신설, 신민용 리포트: "평균OVR 오름차순/내림차순 필터"] sort
    파라미터로 결과 정렬 기준을 고를 수 있다:
      None/"random"(기본) — 기존처럼 무작위 셔플(부수가 고르게 섞여 나옴)
      "ovr_asc"  — 평균OVR 낮은 팀부터
      "ovr_desc" — 평균OVR 높은 팀부터
    정렬을 걸면 LIMIT도 그 정렬 기준으로 상/하위 N팀을 반환한다(무작위
    30팀 중에서 다시 정렬하는 게 아니라, 조건에 맞는 전체 팀 중 실제
    상/하위 N팀). 스쿼드가 비어 avg_ovr이 NULL인 팀(방금 생성돼 아직
    선수가 안 채워졌거나 데이터 이상)은 정렬 방향과 무관하게 항상
    맨 뒤로 보낸다."""
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
    if sort == "ovr_asc":
        q += " ORDER BY (avg_ovr IS NULL), avg_ovr ASC"
    elif sort == "ovr_desc":
        q += " ORDER BY (avg_ovr IS NULL), avg_ovr DESC"
    else:
        q += " ORDER BY RANDOM()"
    q += " LIMIT ?"
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


def list_max_tier():
    """[2026-08 신설, 신민용 확정: "10부까지 늘릴 수 있게 설계"] 현재 데이터에
    실제로 존재하는 최고 부수(가장 깊은 tier)를 반환한다. 부수 필터 드롭다운
    (apply_window/world_browser_window의 '부수' 콤보)이 예전엔 range(1,7)로
    1~6부를 하드코딩하고 있었는데, 이러면 나라별 리그 깊이를 나중에
    7~10부로 늘려도 UI에서 그 이상은 아예 선택할 수 없었다 — 이제 실제
    leagues 테이블의 MAX(tier)를 그대로 써서, 데이터가 몇 부까지 있든
    드롭다운이 자동으로 맞춰진다(코드 수정 없이). 데이터가 아예 없으면
    안전하게 6을 기본값으로 폴백."""
    conn = get_conn()
    row = conn.execute("SELECT MAX(tier) AS mt FROM leagues").fetchone()
    conn.close()
    return row["mt"] if row and row["mt"] else 6


def list_countries(continent=None, grade=None, grade_type="league"):
    """대륙/등급으로 필터링한 국가 목록 (등급순 정렬).

    [버그수정 2026-07] grade는 '클럽 리그 등급'(constants.get_league_grade)
    이어야 하는데, 지금까지는 countries.grade(국가대표 등급, FIFA랭킹 기반)를
    그대로 표시/필터링했다. 그래서 국대는 강해도 클럽리그는 약한 나라
    (모로코·나이지리아·이란 등)가 세계기록실에서 실제보다 훨씬 높은 등급으로
    보였다. search_teams()가 이미 올바르게 처리하던 방식과 동일하게 맞춘다 —
    전체 국가를 조회한 뒤 파이썬에서 실제 클럽 등급을 계산해 필터/정렬한다.

    [2026-08 신설, 신민용 요청: "국가 검색 탭 등급 필터는 국가대표 등급으로
    해달라, 리그 등급이랑 별개니까"] grade_type="league"(기본, 리그/팀/컵
    검색 탭이 쓰는 기존 동작 그대로 유지)면 위 2026-07 수정대로 클럽 리그
    등급을 계산해서 쓰고, grade_type="national"이면 그 계산을 건너뛰고
    countries.grade(국가대표 등급, FIFA랭킹 기반) 원본을 그대로 쓴다 —
    "국가 검색" 탭은 국제대회 우승 기록을 보여주는 화면이라 클럽 리그보다
    국가대표 실력이 더 맞는 기준이다(예: 잉글랜드는 클럽 리그도 S급이라
    이전에도 눈에 띄는 차이가 안 보였지만, 국대 전력만 강하고 클럽 리그는
    상대적으로 약한 나라들이 이 필터에서 제자리를 찾게 된다)."""
    from constants import get_league_grade
    conn = get_conn()
    q = "SELECT id, name, flag, grade, continent FROM countries WHERE 1=1"
    params = []
    if continent:
        q += " AND continent=?"; params.append(continent)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    if grade_type == "league":
        for r in rows:
            r["grade"] = get_league_grade(r["name"], r["grade"])
    # grade_type == "national"이면 SELECT로 이미 가져온 countries.grade
    # 원본을 그대로 둔다(변환 없음).
    if grade:
        rows = [r for r in rows if r["grade"] == grade]
    _order = {g: i for i, g in enumerate(_GRADE_ORDER)}
    rows.sort(key=lambda r: (_order.get(r["grade"], 99), r["name"]))
    return rows


# 국가 등급 고정 순서(강함→약함). DB에 실제 존재하는 값만 걸러서 쓴다.
_GRADE_ORDER = ["SS", "S", "A", "B", "C", "D", "E", "F"]


def list_grades(grade_type="league"):
    """실제 존재하는 등급 목록을 정해진 순서(SS>S>A>...)로 반환.
    [버그수정 2026-07] countries.grade(국대 등급) 원본이 아니라
    get_league_grade()로 계산한 클럽 리그 등급 기준으로 바꿈 — 화면에
    실제로 표시/필터링되는 값과 일치시키기 위함.
    [2026-08 신설] grade_type="national"이면 반대로 countries.grade 원본
    (국가대표 등급) 기준 — list_countries()의 grade_type과 항상 짝 맞춰
    쓴다(필터 드롭다운 목록과 실제 필터링 기준이 다르면 안 되므로)."""
    from constants import get_league_grade
    conn = get_conn()
    rows = conn.execute("SELECT name, grade FROM countries").fetchall()
    conn.close()
    if grade_type == "national":
        existing = {r["grade"] for r in rows if r["grade"]}
    else:
        existing = {get_league_grade(r["name"], r["grade"]) for r in rows}
    return [g for g in _GRADE_ORDER if g in existing]


def search_leagues(continent=None, country_id=None, name_query=None, grade=None, tier=None):
    """조건에 맞는 리그 목록. 이제 모든 리그가 시즌 시작 시 일정을 미리 받고
    매주 실시간으로 결과가 채워지므로, 예전의 '이번 시즌 시뮬 여부(simulated)'
    배지는 더 이상 의미가 없어 반환하지 않는다.

    name_query는 리그명·국가명뿐 아니라 팀명도 매치한다 — 예를 들어 "리버풀"을
    검색하면 리버풀이 뛰고 있는 리그(잉글랜드 프리미어리그)가 검색 결과에
    뜬다. 이때 결과 dict의 "matched_team"에 실제로 일치한 팀명을 담아, 화면에서
    "왜 이 리그가 나왔는지"(팀명 때문인지) 알 수 있게 한다.

    [2026-08 신설, 신민용 요청] tier — 1부~N부로 좁히는 필터. None이면 전체.

    [2026-08 신설, 신민용 요청: "리그 목록에 참가 팀 수도 보여줘"] 반환
    dict마다 "team_count"(그 리그에 소속된 팀 수)를 같이 담아준다.

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
             "MAX(CASE WHEN t.name LIKE ? THEN t.name END) as matched_team, "
             "COUNT(DISTINCT t.id) as team_count "
             "FROM leagues l JOIN countries cn ON l.country_id = cn.id "
             "LEFT JOIN teams t ON t.league_id = l.id WHERE 1=1")
        params = [like]
        if continent:
            q += " AND cn.continent=?"; params.append(continent)
        if country_id:
            q += " AND cn.id=?"; params.append(country_id)
        if tier:
            q += " AND l.tier=?"; params.append(tier)
        if grade_country_ids is not None:
            q += " AND cn.id IN (%s)" % ",".join("?" * len(grade_country_ids))
            params += grade_country_ids
        q += " AND (l.name LIKE ? OR cn.name LIKE ? OR t.name LIKE ?)"
        params += [like, like, like]
        # cn.grade(국대등급) 기준 정렬은 더 이상 의미가 없어 제거 — 클럽 등급
        # 기준 정렬은 아래에서 파이썬으로 다시 한다.
        q += " GROUP BY l.id ORDER BY cn.name, l.tier"
    else:
        # [2026-08 신설, 신민용 요청: "리그 목록에 참가 팀 수도 보여줘"]
        # 팀 수를 리그마다 따로 조회하면 리그 개수만큼 쿼리가 늘어나므로
        # (N+1), name_query 분기와 동일하게 teams를 LEFT JOIN해서 한 번에
        # GROUP BY로 리그당 1행씩 모은다 — 검색어가 없을 때도 매번 이
        # 함수가 다시 불리므로(필터 바뀔 때마다) 여기도 똑같이 최적화해야
        # 체감 렉이 안 생긴다.
        q = ("SELECT l.id, l.name, l.tier, cn.id as country_id, cn.name as country, "
             "cn.flag as flag, cn.grade as cgrade, cn.continent as continent, "
             "COUNT(DISTINCT t.id) as team_count "
             "FROM leagues l JOIN countries cn ON l.country_id = cn.id "
             "LEFT JOIN teams t ON t.league_id = l.id WHERE 1=1")
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
        q += " GROUP BY l.id ORDER BY cn.name, l.tier"

    rows = [dict(r) for r in c.execute(q, params).fetchall()]
    conn.close()
    for r in rows:
        r["grade"] = get_league_grade(r["country"], r["cgrade"])
    _order = {g: i for i, g in enumerate(_GRADE_ORDER)}
    rows.sort(key=lambda r: (_order.get(r["grade"], 99), r["country"], r["tier"]))
    return rows


def list_league_tiers():
    """실제 DB에 존재하는 리그 티어(부수) 목록을 오름차순으로 반환 —
    [2026-08 신설, 신민용 요청] 리그 검색 탭에 "1부~N부" 필터를 만들기
    위함. 나라마다 리그 깊이가 달라(4부까지인 나라, 7부까지인 나라 등)
    고정된 상수 대신 실제 존재하는 값만 조회해서 필터 목록을 만든다."""
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT tier FROM leagues ORDER BY tier").fetchall()
    conn.close()
    return [r["tier"] for r in rows if r["tier"]]


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
    # [2026-08 버그수정, 신민용 리포트: "역대 우승팀이 2000년부터 다 안
    # 나온다"] match_results_archive 압축으로 내 커리어와 무관한 팀들의
    # 원본 경기가 지워진 뒤로는, cnt==0이 "정말 일정이 없다"가 아니라
    # "이미 끝나서 league_season_standings로만 요약돼 있다"는 뜻일 수도
    # 있다 — 그 경우까지 재생성 대상으로 오판하지 않도록 먼저 확인한다.
    if cnt == 0:
        summarized = conn.execute(
            "SELECT 1 FROM league_season_standings WHERE league_id=? AND season=? LIMIT 1",
            (league_id, season)).fetchone()
        cnt = 1 if summarized else 0
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


def league_has_upper_tier(league_id):
    """[2026-08 신설] 이 리그보다 한 단계 위 티어가 그 나라에 존재하는지.
    1부 리그는 애초에 올라갈 곳이 없어 승격 자체가 없다 — league_has_lower_tier
    와 대칭 관계. '최다 순위' 팝업에서 최다 승격팀 열을 보여줄지 판단하는
    용도(1부면 숨김)."""
    conn = get_conn()
    row = conn.execute("SELECT country_id, tier FROM leagues WHERE id=?",
                        (league_id,)).fetchone()
    if not row:
        conn.close()
        return False
    if row["tier"] <= 1:
        conn.close()
        return False
    upper = conn.execute(
        "SELECT 1 FROM leagues WHERE country_id=? AND tier=?",
        (row["country_id"], row["tier"] - 1)).fetchone()
    conn.close()
    return bool(upper)


def get_league_champions(league_id, limit=999):
    """이 리그의 시즌별 1~4위 + 실제 승격팀 전체/강등팀 전체 목록.
    실제로 경기가 진행된 시즌만 대상이며(한 번도 경기가 없었던 시즌은 제외),
    새 테이블 없이 match_results를 시즌 단위로 그때그때 집계해서 계산한다
    (승강전 처리와 동일한 방식).최신 시즌부터 최대 limit개.

    [2026-08 수정, 신민용 리포트: "역대 우승팀이 2000년부터 다 안 나온다"]
    limit 기본값을 30 → 999(사실상 전체)로 올렸다. 예전엔 시즌마다 원본
    경기 수백~수천 건을 매번 다시 훑어 집계했어서 30개로 막아뒀는데,
    이제 get_league_standings()가 league_season_standings 요약을 먼저
    보므로 시즌 하나당 비용이 훨씬 가벼워졌다 — 실측: 21시즌 전체 조회에
    0.17초, 체감 렉 없음.

    [2026-07 승강 플레이오프 도입, 재수정] 예전엔 승격/강등 명단을 순위
    위치만으로 재계산했다("하위 N명 = 강등") — 그런데 이제 그 N명 중
    일부는 PO로 결정되므로, 순위가 강등권(또는 PO권)이어도 실제로는
    PO에서 이겨 잔류/승격할 수 있다. 이제는 재계산 대신 promotion_log
    (실제로 확정된 이동만 기록되는 원장)를 그대로 읽는다 — PO가 있든
    없든 promotion_log는 항상 '진짜로 일어난 일'만 담고 있으므로 이
    함수는 PO 존재 여부와 무관하게 항상 정확하다.

    [2026-07 버그수정, 실제 테스트 중 발견] 처음엔 promotion_log.league_name
    문자열로 필터링했는데, "프리메라 디비시온"처럼 여러 나라가 리그 이름을
    그대로 공유하는 경우가 실제로 있어서(아르헨티나/안도라/칠레 등) 다른
    나라의 승강 기록이 섞여 들어오는 걸 실제 세이브로 재현했다. 지금은
    promotion_log.from_league_id/to_league_id(league_id 그대로 저장)로
    필터링해서 이름 충돌과 완전히 무관하게 조회한다.
    """
    from game_engine import get_league_standings
    conn = get_conn()

    lg_row = conn.execute("SELECT tier FROM leagues WHERE id=?", (league_id,)).fetchone()
    my_tier = lg_row["tier"] if lg_row else 0

    # [2026-07 수정] archive_old_seasons()로 과거 시즌이 match_results_archive로
    # 옮겨지므로, 여기서도 두 테이블을 합쳐서 시즌 목록을 뽑아야 예전처럼
    # 모든 완료 시즌이 다 나온다.
    # [2026-08 버그수정, 신민용 리포트: "역대 우승팀이 2000년부터 다 안
    # 나온다"] match_results_archive 압축(내 커리어와 무관한 팀들의 원본
    # 경기 삭제) 이후로는 원본만 보면 시즌 자체가 있었는지조차 알 수 없는
    # 경우가 생겼다 — league_season_standings(압축돼도 절대 안 지워지는
    # 요약)도 함께 봐야 모든 완료 시즌이 예전처럼 다 나온다.
    season_rows = conn.execute(
        """SELECT DISTINCT season, year FROM match_results
           WHERE league_id=? AND home_score>=0
           UNION
           SELECT DISTINCT season, year FROM match_results_archive
           WHERE league_id=? AND home_score>=0
           UNION
           SELECT DISTINCT season, year FROM league_season_standings
           WHERE league_id=?
           ORDER BY season DESC LIMIT ?""",
        (league_id, league_id, league_id, limit)).fetchall()

    out = []
    for sr in season_rows:
        standings = get_league_standings(league_id, season=sr["season"], conn=conn)
        if not standings:
            continue
        n = len(standings)
        rank_of = {s["name"]: i + 1 for i, s in enumerate(standings)}

        # 두 경우 모두 "이 리그(league_id)에서 원래 뛰다가 떠난 팀"이라
        # from_league_id=league_id는 공통이고, to_tier만 위/아래로 갈린다.
        promoted = []
        if my_tier > 1:
            rows_p = conn.execute(
                """SELECT team_name FROM promotion_log
                   WHERE year=? AND from_league_id=? AND to_tier=?""",
                (sr["year"], league_id, my_tier - 1)).fetchall()
            promoted = [{"rank": rank_of.get(r["team_name"], 0), "name": r["team_name"]}
                        for r in rows_p if r["team_name"] in rank_of]
            promoted.sort(key=lambda x: x["rank"])

        relegated = []
        rows_r = conn.execute(
            """SELECT team_name FROM promotion_log
               WHERE year=? AND from_league_id=? AND to_tier=?""",
            (sr["year"], league_id, my_tier + 1)).fetchall()
        relegated = [{"rank": rank_of.get(r["team_name"], 0), "name": r["team_name"]}
                     for r in rows_r if r["team_name"] in rank_of]
        relegated.sort(key=lambda x: x["rank"])

        # [2026-08 신설, 신민용 요청: "역대 우승팀 화면에서 이 리그로
        # 승격해서 온 팀도 팀명이랑 그때(출신 리그) 순위를 같이 보여줘"]
        # promoted/relegated는 "이 리그에서 나간 팀"만 다뤘는데, 이건
        # 반대로 "이 리그로 들어온 팀"이다 — to_league_id/to_tier가
        # 이 리그를 가리키는 promotion_log 행을 찾고, 각 팀의 출신 리그
        # (from_league_id)에서 그 해 몇 등이었는지를 별도로 계산한다.
        # season 번호는 리그마다 다를 수 있어(승강 등으로 리그 생성 시점이
        # 다르면 어긋날 수 있음), 출신 리그·해당 연도 조합으로 실제 season을
        # match_results/아카이브에서 직접 찾아 매칭한다 — GAME_START_YEAR
        # 기반 계산식에 의존하지 않아 더 안전하다.
        #
        # [2026-08 버그수정, 신민용 리포트: "K2 리그 기록실에 K1에서 바로
        # 강등당해 내려온 팀이 승격팀으로 표시된다"] to_league_id=이 리그,
        # to_tier=이 리그 등급 조건만으로는 "이 리그로 들어온 팀 전부"가
        # 잡히는데, 그 안엔 아래 티어에서 진짜 승격해 온 팀(from_tier가
        # 더 큰 수 = 더 하위 리그)뿐 아니라 위 티어에서 강등당해 내려온
        # 팀(from_tier가 더 작은 수 = 더 상위 리그)도 섞여 있었다 — 방향
        # 구분 없이 전부 "승격팀"에 몰아넣은 게 원인. from_tier로 두 방향을
        # 갈라서 promoted_in(아래→위, 진짜 승격)과 relegated_in(위→아래,
        # 강등되어 들어옴)을 분리한다.
        def _incoming_team_info(team_name, src_lid):
            src_rank, src_name = 0, ""
            if src_lid:
                src_season_row = conn.execute(
                    """SELECT season FROM (
                           SELECT season FROM match_results WHERE league_id=? AND year=?
                           UNION
                           SELECT season FROM match_results_archive WHERE league_id=? AND year=?
                           UNION
                           SELECT season FROM league_season_standings WHERE league_id=? AND year=?
                       ) LIMIT 1""",
                    (src_lid, sr["year"], src_lid, sr["year"], src_lid, sr["year"])).fetchone()
                if src_season_row:
                    src_standings = get_league_standings(src_lid, season=src_season_row["season"], conn=conn)
                    for idx, s in enumerate(src_standings):
                        if s["name"] == team_name:
                            src_rank = idx + 1
                            break
                src_lg_row = conn.execute("SELECT name FROM leagues WHERE id=?", (src_lid,)).fetchone()
                src_name = src_lg_row["name"] if src_lg_row else ""
            return {"name": team_name, "from_rank": src_rank, "from_league": src_name}

        rows_in = conn.execute(
            """SELECT team_name, from_league_id, from_tier FROM promotion_log
               WHERE year=? AND to_league_id=? AND to_tier=?""",
            (sr["year"], league_id, my_tier)).fetchall()
        promoted_in = [_incoming_team_info(r["team_name"], r["from_league_id"])
                       for r in rows_in if r["from_tier"] > my_tier]
        relegated_in = [_incoming_team_info(r["team_name"], r["from_league_id"])
                        for r in rows_in if r["from_tier"] < my_tier]
        promoted_in.sort(key=lambda x: (x["from_rank"] == 0, x["from_rank"]))
        relegated_in.sort(key=lambda x: (x["from_rank"] == 0, x["from_rank"]))

        out.append({
            "season": sr["season"], "year": sr["year"],
            "first":  standings[0]["name"] if n > 0 else "-",
            "second": standings[1]["name"] if n > 1 else "-",
            "third":  standings[2]["name"] if n > 2 else "-",
            "fourth": standings[3]["name"] if n > 3 else "-",  # [2026-07 추가] 3위까지만 기록하던 것을 4위까지 확장
            "promoted": promoted,
            "relegated": relegated,
            "promoted_in": promoted_in,
            "relegated_in": relegated_in,
        })
    conn.close()
    return out


# [2026-08 신설, 신민용 요청: "리그 역대 우승팀 화면에 1등/2등을 가장 많이
# 한 팀 순위를 보여주는 창을 만들어달라" → 이후 "챔스/유로파/컨퍼런스/
# 클럽월드컵/네이션스컵/지역컵/컵대회에도 역대 1~4위를 가장 많이 차지한
# 팀/국가 순위를 보여달라"] 처음엔 리그 전용으로 first/second만 세는
# 함수였는데, 같은 요청이 6개 대회 탭에 더 필요해져서 자리(key) 개수·
# 필드 이름 패턴만 다를 뿐 로직은 완전히 동일한 걸 범용 함수로 뺐다 —
# 클럽 대항전(팀명+국가 분리 필요, name_key_fmt="{key}_name"+country_key_fmt
# ="{key}_country")과 리그/국가대표 대회/국내컵(이름 하나로 충분,
# name_key_fmt="{key}")을 모두 이 함수 하나로 처리한다.
def _rank_leaders_from_rows(rows, keys, name_key_fmt, country_key_fmt=None):
    """rows(대회/시즌별 결과 목록)에서 keys에 지정한 자리(예: "first"/
    "second" 또는 "winner"/"runner_up"/"third"/"fourth")별로 이름을 세어
    많이 나온 순으로 정렬한다.
    반환: {key: [{"name":, "country":(있으면), "count":}, ...]}
    각각 횟수 많은 순(동률이면 이름 가나다순)으로 정렬."""
    from collections import Counter
    counters = {k: Counter() for k in keys}
    for r in rows:
        for k in keys:
            name = r.get(name_key_fmt.format(key=k))
            if not name or name in ("-", "?"):
                continue
            country = r.get(country_key_fmt.format(key=k)) if country_key_fmt else None
            counters[k][(name, country or "")] += 1
    out = {}
    for k in keys:
        out[k] = [{"name": n, "country": (c or None), "count": cnt}
                  for (n, c), cnt in sorted(counters[k].items(), key=lambda x: (-x[1], x[0]))]
    return out


def _promo_relegation_leader_counts(rows):
    """[2026-08 신설, 신민용 요청: "최다 순위에서 4위 옆에 가장 많이
    승격한 팀/가장 많이 강등한 팀도 넣어달라"] get_league_champions()가
    시즌별로 담아주는 promoted/relegated(그 시즌에 실제로 이 리그에서
    나간 팀 '목록')를 전 시즌에 걸쳐 팀명별로 세어 누적 횟수 순으로
    정렬한다.

    1~4위(_rank_leaders_from_rows)와 다른 점: 그쪽은 시즌당 한 자리에
    팀 하나만 들어가는 단일 이름 필드(row["first"] 등)를 세지만, 여기는
    시즌당 승격/강등팀이 여러 명일 수 있는 리스트(row["promoted"] =
    [{"name":...}, ...])를 세야 해서 별도 함수로 뺐다.

    반환: (most_promoted, most_relegated) — 각각 [{"name":, "count":}, ...]
    횟수 많은 순(동률이면 이름 가나다순)."""
    from collections import Counter
    promo_counter = Counter()
    releg_counter = Counter()
    for r in rows:
        for item in r.get("promoted") or []:
            name = item.get("name")
            if name and name not in ("-", "?"):
                promo_counter[name] += 1
        for item in r.get("relegated") or []:
            name = item.get("name")
            if name and name not in ("-", "?"):
                releg_counter[name] += 1
    most_promoted = [{"name": n, "count": cnt}
                      for n, cnt in sorted(promo_counter.items(), key=lambda x: (-x[1], x[0]))]
    most_relegated = [{"name": n, "count": cnt}
                       for n, cnt in sorted(releg_counter.items(), key=lambda x: (-x[1], x[0]))]
    return most_promoted, most_relegated


def get_league_rank_leaders(league_id):
    """이 리그에서 시즌 1~4위를 가장 많이 한 팀 순위, 그리고 가장 많이
    승격/강등한 팀 순위.
    반환: {"first": [...], "second": [...], "third": [...], "fourth": [...],
           "most_promoted": [...], "most_relegated": [...]}
    최상위 리그는 승격 자체가 없어 most_promoted가 항상 빈 리스트이고,
    최하위 리그는 강등 자체가 없어 most_relegated가 항상 빈 리스트다
    (get_league_champions가 애초에 그렇게 채워주므로 여기선 그대로 반영될
    뿐 — 어느 쪽을 화면에 보여줄지는 호출부에서 league_has_lower_tier /
    tier==1 여부로 판단한다)."""
    rows = get_league_champions(league_id)
    out = _rank_leaders_from_rows(rows, ("first", "second", "third", "fourth"), "{key}")
    most_promoted, most_relegated = _promo_relegation_leader_counts(rows)
    out["most_promoted"] = most_promoted
    out["most_relegated"] = most_relegated
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
    from competition.cup_engine import _winner_of
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
    from competition.cup_engine import _winner_of
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

    # [2026-08 신설, 신민용 설계 확정: "대회명과 우승 사이에 참여팀 수를
    # 넣자"] 그 시즌 컵대회에 실제로 등록된 전체 참가팀 수(cup_entries
    # 행 수) — 대회 초반에 몇 팀으로 시작했는지 보여주는 규모 지표라서,
    # "왜 이 대회는 16강부터 시작하고 저 대회는 8강부터 시작하는지"가
    # 한눈에 설명된다. 우승/준우승 등과 달리 대회가 있었으면(완료 여부와
    # 무관하게) 항상 구할 수 있으므로 all_tids가 아니라 rows 전체 기준으로
    # 조회한다.
    n_teams_cache = {}
    if tournament_ids:
        ph2 = ",".join("?" * len(tournament_ids))
        for e in conn.execute(
                f"SELECT tournament_id, COUNT(*) AS n FROM cup_entries "
                f"WHERE tournament_id IN ({ph2}) GROUP BY tournament_id",
                tournament_ids).fetchall():
            n_teams_cache[e["tournament_id"]] = e["n"]

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
            "n_teams": n_teams_cache.get(r["id"], 0),
            "winner": _nm(r["id"], pl["winner"]), "runner_up": _nm(r["id"], pl["runner_up"]),
            "third": _nm(r["id"], pl["third"]), "fourth": _nm(r["id"], pl["fourth"]),
            "winner_tier": _tier(r["id"], pl["winner"]), "runner_up_tier": _tier(r["id"], pl["runner_up"]),
            "third_tier": _tier(r["id"], pl["third"]), "fourth_tier": _tier(r["id"], pl["fourth"]),
        })
    conn.close()
    return out


# [2026-08 신설, 신민용 요청: "컵대회에도 역대 1~4위를 가장 많이 차지한
# 팀 순위를 보여달라"] 국내 컵대회는 한 나라 안에서만 열려서 팀명이
# 그 나라 안에서는 유일하다 — 국가를 따로 붙일 필요가 없어 name_key_fmt
# ="{key}"만으로 충분(get_continental_cup_rank_leaders와 같은 이유).
def get_cup_rank_leaders(country_id):
    rows = get_cup_history(country_id, limit=999999)
    return _rank_leaders_from_rows(rows, ("winner", "runner_up", "third", "fourth"), "{key}")


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
    # [2026-08 버그수정, 신민용 리포트: "대진표에서 팀명 복사하면 (3부)까지
    # 같이 복사된다"] 예전엔 "(N부)"를 team_name 문자열에 미리 구워넣어서
    # (f"{team_name} ({tier}부)") UI가 복사할 때 순수 팀명만 따로 꺼낼 방법이
    # 없었다 — team_name은 순수하게 두고 tier를 별도 필드로 분리해서, UI가
    # 화면 표시는 "팀명 (N부)"로 조합하되 복사는 team_name만 쓸 수 있게 한다
    # (world_browser_window._build_stage_box가 CL/CWC의 country와 동일한
    # 방식으로 tier를 접미사 취급).
    name_by_id = {r["team_id"]: (r["team_name"], r["tier"]) for r in entry_rows}

    by_round = {}
    order = []
    for m in rows:
        key = (m["round_idx"], m["round_name"])
        if key not in by_round:
            by_round[key] = []
            order.append(key)
        _hn, _ht = name_by_id.get(m["home_team_id"], ("?", None))
        _an, _at = name_by_id.get(m["away_team_id"], ("?", None))
        by_round[key].append({
            "home_info": {"team_name": _hn, "tier": _ht,
                          "flag": "", "team_id": m["home_team_id"]},
            "away_info": {"team_name": _an, "tier": _at,
                          "flag": "", "team_id": m["away_team_id"]},
            "home_score": m["home_score"], "away_score": m["away_score"],
            "pso_winner": m["pso_winner"],
        })
    knockout = [{"stage": key[1], "stage_ko": key[1], "matches": by_round[key]} for key in order]
    return {"groups": {}, "knockout": knockout}


def has_cup_data(country_id):
    """이 나라에 생성된 컵대회 기록이 하나라도 있는지(검색 목록 배지용).
    [주의] 국가 목록 전체를 훑을 땐 이 함수를 나라마다 반복 호출하지 말고
    has_cup_data_bulk()를 쓸 것 — 아래 참고."""
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM cup_tournaments WHERE country_id=?",
        (country_id,)).fetchone()["n"]
    conn.close()
    return n > 0


def has_cup_data_bulk():
    """[2026-08 최적화, 신민용 리포트: "세계기록실도 클릭할 때 렉있어"]
    컵대회 검색 탭이 국가 목록(전 세계 150~200개국)을 그릴 때, 나라마다
    has_cup_data()를 따로 호출해서 SELECT COUNT(*)를 나라 수만큼(N+1) 날리고
    있었다 — 컵대회 기록이 있는 country_id 집합을 통째로 1번의 쿼리로
    가져와서, 호출부는 `cid in result_set`으로 O(1) 판정하면 된다.
    결과(어느 나라가 '기록 있음'으로 뜨는지)는 기존과 완전히 동일하다."""
    conn = get_conn()
    ids = {r["country_id"] for r in conn.execute(
        "SELECT DISTINCT country_id FROM cup_tournaments").fetchall()}
    conn.close()
    return ids


# ─────────────────────────────────────────
# 3. 역대 챔피언스리그/유로파리그/컨퍼런스리그 기록
# [2026-08 확장, 신민용 요청: "세계기록실에 역대 유로파/컨퍼런스도"]
# 아래 함수들은 챔스 전용이었던 걸 match_table/tournament_table 매개변수로
# 일반화했다 — el_*/ecl_* 스키마가 cl_*와 완전히 동일하므로 로직은
# 한 글자도 안 바뀐다.
# ─────────────────────────────────────────
def _get_cl_placements(tournament_id, conn, match_table="cl_matches"):
    """결승(F)+3/4위전(TP) 결과로 1~4위 team_id를 도출.
    intl_engine 쪽 _get_placements와 동일한 패턴, team_id(정수) 기준만 다름.
    [주의] 대회 하나만 볼 때 쓰는 함수 — 여러 대회를 한꺼번에 나열할 때는
    _batch_cl_placements()를 써서 대회 수만큼 쿼리가 늘어나지 않게 한다."""
    from competition.champions_engine import _winner_of
    fm = conn.execute(
        f"SELECT * FROM {match_table} WHERE tournament_id=? AND stage='F' "
        f"AND home_score>=0 ORDER BY id DESC LIMIT 1", (tournament_id,)).fetchone()
    if not fm:
        return None
    fm = dict(fm)
    winner = _winner_of(fm)
    runner_up = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    third = fourth = None
    tp = conn.execute(
        f"SELECT * FROM {match_table} WHERE tournament_id=? AND stage='TP' "
        f"AND home_score>=0 ORDER BY id DESC LIMIT 1", (tournament_id,)).fetchone()
    if tp:
        tp = dict(tp)
        third = _winner_of(tp)
        fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]

    return {"winner": winner, "runner_up": runner_up, "third": third, "fourth": fourth}


def _batch_cl_placements(tournament_ids, conn, match_table="cl_matches"):
    """[2026-07 성능개선] _get_cl_placements의 대회당 SELECT 2번을
    tournament_id IN 배치 쿼리 1번으로 통합 (get_cl_history의 limit=100
    기본값 기준 최대 200회 왕복 → 1회). 판정 로직은 완전히 동일하고
    반환 형태만 {tournament_id: dict}."""
    from competition.champions_engine import _winner_of
    if not tournament_ids:
        return {}
    ph = ",".join("?" * len(tournament_ids))
    rows = conn.execute(
        f"""SELECT * FROM {match_table} WHERE tournament_id IN ({ph})
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


def get_cl_history(continent=None, limit=100, tournament_table="cl_tournaments",
                    match_table="cl_matches"):
    """완료된 챔피언스리그(또는 tournament_table로 지정한 다른 클럽대항전)
    대회의 연도별 1~4위(팀명+국가+국기) 목록.
    winner_team_id는 대회가 실제로 끝났을 때만 채워지므로(각 엔진의
    finish_tournament 처리 시점), 그 전까지는 표시되지 않음.
    """
    conn = get_conn(); c = conn.cursor()
    q = f"""SELECT t.id, t.year, t.continent, t.name
           FROM {tournament_table} t
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
    placements_map = _batch_cl_placements(tournament_ids, conn, match_table=match_table)
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


def get_el_history(continent=None, limit=100):
    """[2026-08 신설] 역대 유로파리그 기록 — get_cl_history를 el_* 테이블로."""
    return get_cl_history(continent=continent, limit=limit,
                           tournament_table="el_tournaments", match_table="el_matches")


def get_ecl_history(continent=None, limit=100):
    """[2026-08 신설] 역대 컨퍼런스리그 기록 — get_cl_history를 ecl_* 테이블로."""
    return get_cl_history(continent=continent, limit=limit,
                           tournament_table="ecl_tournaments", match_table="ecl_matches")


# [2026-08 신설, 신민용 요청: "챔스/유로파/컨퍼런스에도 대륙 필터 옆에
# 역대 1~4위를 가장 많이 차지한 팀 순위를 보여달라"] get_cl_history가
# 이미 연도별 1~4위를 팀명+국가까지 채워서 주므로, _rank_leaders_from_rows
# 로 그 결과를 그대로 집계만 하면 된다 — limit은 999999(사실상 전체)로
# 올려서 필터에 걸리는 그 대회 전체 역사를 다 반영한다(화면 표
# get_cl_history의 limit=100과 달리, 순위 집계는 최근 100개로 잘릴
# 이유가 없다).
def get_cl_style_rank_leaders(continent=None, tournament_table="cl_tournaments",
                               match_table="cl_matches"):
    rows = get_cl_history(continent=continent, limit=999999,
                           tournament_table=tournament_table, match_table=match_table)
    return _rank_leaders_from_rows(rows, ("winner", "runner_up", "third", "fourth"),
                                    "{key}_name", "{key}_country")


def get_el_rank_leaders(continent=None):
    return get_cl_style_rank_leaders(continent, "el_tournaments", "el_matches")


def get_ecl_rank_leaders(continent=None):
    return get_cl_style_rank_leaders(continent, "ecl_tournaments", "ecl_matches")


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


# [2026-08 신설, 신민용 요청: "월드컵에도 네이션스컵처럼 역대 1~4위를
# 가장 많이 차지한 국가 순위를 보여달라"] 월드컵은 대회명이 하나뿐이라
# (네이션스컵/지역컵과 달리 여러 이름으로 안 갈림) 별도 필터 없이 전체
# 역사를 그대로 집계한다.
def get_wc_rank_leaders():
    rows = get_wc_history(limit=999999)
    return _rank_leaders_from_rows(rows, ("winner", "runner_up", "third", "fourth"), "{key}")


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


# [2026-08 신설, 신민용 요청: "네이션스컵에도 필터 옆에 역대 1~4위를 가장
# 많이 차지한 국가 순위를 보여달라"] 국가대표 대회는 참가자 자체가
# 국가라 팀/국가를 분리할 필요가 없다 — name_key_fmt="{key}"만으로 충분
# (country_key_fmt 없음, RankLeadersDialog가 country=None이면 괄호를
# 안 붙인다).
def get_continental_cup_rank_leaders(name=None):
    rows = get_continental_cup_history(name=name, limit=999999)
    return _rank_leaders_from_rows(rows, ("winner", "runner_up", "third", "fourth"), "{key}")


# [2026-08 신설] 3단계 지역컵 — 대륙컵 함수들과 완전히 같은 패턴이다.
# 국가 검색/트로피 집계는 kind로 그룹만 하는 범용 구조라 이미 지역컵도
# 자동으로 잡히지만(get_country_trophy_summary 등), "세계기록실 → 역대
# 지역컵" 탭 전용으로 (1) 지역별 대회명 목록 (2) 특정 지역의 연도별
# 1~4위 이력을 뽑는 함수 두 개만 새로 필요하다.
def list_region_cup_names():
    """지금까지 이 세이브에서 실제로 열린 적 있는 지역컵 이름 목록
    (대회명, 예: 'EAFF E-1 챔피언십') — 왼쪽 목록에 쓴다."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT name FROM intl_tournaments WHERE kind='region' "
        "ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def get_region_cup_history(name=None, limit=100):
    """완료된 지역컵(kind='region') 대회의 연도별 1~4위 목록.
    name을 주면 그 대회(예: 'EAFF E-1 챔피언십')만."""
    conn = get_conn(); c = conn.cursor()
    q = ("SELECT id, year, name FROM intl_tournaments "
         "WHERE kind='region' AND status='done' AND winner != ''")
    params = []
    if name:
        q += " AND name=?"; params.append(name)
    q += " ORDER BY year DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in c.execute(q, params).fetchall()]
    rows = _attach_placements_and_flags(rows, conn)
    conn.close()
    return rows


# [2026-08 신설, 신민용 요청: "지역컵에도 역대 1~4위를 가장 많이 차지한
# 국가 순위를 보여달라"] get_continental_cup_rank_leaders와 완전히 같은
# 이유·구조 — 지역컵도 참가자가 국가 자체다.
def get_region_cup_rank_leaders(name=None):
    rows = get_region_cup_history(name=name, limit=999999)
    return _rank_leaders_from_rows(rows, ("winner", "runner_up", "third", "fourth"), "{key}")


# ─────────────────────────────────────────
# 5.5. 국가 검색 (2026-08 신설, 신민용 확정: "월드컵/대륙컵 우승 기록실")
# ─────────────────────────────────────────
# [설계 원칙] intl_tournaments.winner에 우승국이 이미 문자열로 저장돼
# 있으므로, kind별로 GROUP BY만 하면 국가별 집계가 나온다. 대회 종류가
# 늘어나도(새 kind 값 추가) 이 쿼리들은 코드 수정 없이 자동으로 포함한다
# — 화면 라벨만 constants.INTL_TOURNAMENT_KIND_LABELS에 추가하면 됨.

def _is_euro_cycle_year(year):
    """[2026-08 신설] cont_qual(대륙컵 예선) 대회 하나가 그 해에 어느
    본선(유럽 네이션스컵 vs 유로(EURO))으로 이어지는 예선인지 구분하는
    헬퍼. intl_engine.py의 start_qualifying_if_needed가 cont_qual을
    만드는 조건이 두 가지 서로 다른 해에 걸쳐 있다 — ① 대륙컵 해
    (CONTINENTAL_START_YEAR 주기, 2000/04/08..)엔 '유럽 네이션스컵'
    예선, ② 지역컵/유로 해(REGIONAL_CUP_START_YEAR 주기, 2001/05/09..)엔
    '유로(EURO)' 예선 — 같은 kind='cont_qual'로 저장되지만 실제로는
    완전히 다른 두 대회의 예선이다. 판정식은 intl_engine.py의
    is_euro_cycle 계산과 완전히 동일하게 맞춘다(그쪽이 원본 판정)."""
    # [2026-08 버그수정] 정적 상수 대신, 실제로 선택된 시작 연도 기준으로
    # 매번 재계산한다(intl_engine._tournament_start_years와 동일한 이유).
    from constants import REGIONAL_CUP_INTERVAL
    from intl_engine import _tournament_start_years
    _regional_start = _tournament_start_years()["regional"]
    return (year >= _regional_start
            and (year - _regional_start) % REGIONAL_CUP_INTERVAL == 0)


def _effective_kind(kind, name, year=None):
    """[2026-08 신설, 신민용 요청: "우승 기록에 유로/대륙컵 필터를 따로
    만들어달라"] DB상 유로(EURO)는 일반 대륙컵과 똑같이 kind='continent'로
    저장된다(intl_engine._create_one_tournament — 이름만 EURO_NAME으로
    다르게 줌, 조편성/선발 로직은 100% 동일하다는 설계 의도). 그래서
    kind 하나만으로는 "이 우승이 유로인지 일반 대륙컵(네이션스컵)인지"
    구분이 안 됐다 — 이름까지 같이 봐서 필터/집계용 "유효 종류"를
    따로 판정한다. world/region은 그대로, continent만 이름으로 갈린다.

    [2026-08 수정, 신민용 리포트: "2000 유럽 네이션스컵 예선이 2000 유로
    예선으로 뜬다 — 이땐 유로가 아니라 유럽 네이션스컵인데"] cont_qual을
    무조건 유로 예선으로 판정했던 게 오판이었다 — _is_euro_cycle_year
    참고: cont_qual은 대륙컵 해(유럽 네이션스컵 예선)와 유로 해(유로
    예선) 두 군데서 다 만들어진다. year가 주어지면 그 해가 어느 주기인지
    실제로 계산해서 판정하고, year를 모르면(우승 집계처럼 GROUP BY로
    year가 없는 호출부) cont_qual은 애초에 우승 개념이 없어서 그런
    호출부에는 나타나지 않으므로 안전하게 원래 kind 그대로 둔다."""
    from constants import EURO_NAME
    if kind == "continent" and name == EURO_NAME:
        return "euro"
    if kind == "cont_qual" and year is not None and _is_euro_cycle_year(year):
        return "euro_qual"
    return kind


# [2026-08 신설] 필터 콤보에 쓸 "대회 종류" 선택지 — 표시 라벨과 내부
# effective_kind 값 매핑. 순서대로 콤보박스에 나열된다.
COUNTRY_TROPHY_KIND_OPTIONS = [
    ("월드컵", "world"),
    ("유로", "euro"),
    ("대륙컵", "continent"),
    ("지역컵", "region"),
]


# [2026-08 신설] 순위 필터(우승/준우승/3위/4위)에서 쓰는 표시 라벨.
COUNTRY_PLACEMENT_RANK_OPTIONS = [
    ("🥇 우승", 1),
    ("🥈 준우승", 2),
    ("🥉 3위", 3),
    ("4위", 4),
]


def get_all_countries_placement_counts():
    """국가명 → {순위(1~4): 총 횟수} 매핑을 한 번에 집계.
    [2026-08 신설, 신민용 요청: "1등만 필터되는데 1~4등까지 나눠서 필터하고
    싶다"] 기존 get_all_countries_trophy_counts()는 intl_tournaments.winner
    컬럼만 보므로 우승(1위)만 잡힌다 — 준우승/3위/4위는 결승·3·4위전
    매치(F/TP stage)를 직접 봐야 하므로 _batch_placements를 재사용해서
    월드컵/대륙컵/유로/지역컵을 통틀어 전 대회를 한 번에 훑는다.
    [성능] 나라 수(200+)만큼 개별 조회하지 않고, 완료된 전체 대회 목록을
    한 번만 가져와 _batch_placements(이미 배치 쿼리 1회로 처리)에 넘긴다."""
    conn = get_conn()
    tids = [r["id"] for r in conn.execute(
        """SELECT id FROM intl_tournaments
           WHERE status='done' AND winner != ''
             AND kind IN ('world','continent','region')""").fetchall()]
    placements = _batch_placements(tids, conn)
    conn.close()
    out = {}
    for pl in placements.values():
        for rank, key in ((1, "winner"), (2, "runner_up"), (3, "third"), (4, "fourth")):
            nat = pl.get(key)
            if not nat:
                continue
            out.setdefault(nat, {})[rank] = out.setdefault(nat, {}).get(rank, 0) + 1
    return out


def get_all_countries_trophy_counts():
    """국가명 → [{kind, name, n}, ...] 매핑을 한 번에 집계 — (kind, name)
    조합별로 나눠서, 대회명이 다르면(예: 유로 vs 유럽 네이션스컵) 별도
    항목으로 잡히게 한다.
    [성능] 국가 검색 리스트를 채울 때 국가마다 개별 쿼리하면 국가 수(200+)
    만큼 왕복이 생기므로, GROUP BY 한 번으로 전체를 미리 다 구해둔다."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT winner, kind, name, COUNT(*) as n FROM intl_tournaments
           WHERE status='done' AND winner != '' GROUP BY winner, kind, name""").fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["winner"], []).append(
            {"kind": r["kind"], "name": r["name"], "n": r["n"],
             "effective_kind": _effective_kind(r["kind"], r["name"])})
    return out


def get_country_trophy_summary(country_name):
    """한 국가의 (kind,name)별 우승 횟수 요약(대회명 그대로 노출 —
    예: "남북미 대륙컵 1회", "유로(EURO) 2회"). 대회명이 대륙/대회
    종류에 따라 고정이라(CONF_CUP_NAME 등) 이름 기준으로 묶어도 항상
    하나의 실제 대회를 가리킨다.
    반환: [{"kind":, "name":, "titles":, "label":, "effective_kind":}, ...]
    우승 많은 순."""
    from constants import INTL_TOURNAMENT_KIND_GLYPHS, INTL_TOURNAMENT_KIND_FALLBACK_LABEL
    conn = get_conn()
    rows = conn.execute(
        """SELECT kind, name, COUNT(*) as titles FROM intl_tournaments
           WHERE status='done' AND winner=? GROUP BY kind, name
           ORDER BY titles DESC""", (country_name,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        ek = _effective_kind(r["kind"], r["name"])
        glyph = INTL_TOURNAMENT_KIND_GLYPHS.get(ek, INTL_TOURNAMENT_KIND_FALLBACK_LABEL)
        out.append({"kind": r["kind"], "name": r["name"], "titles": r["titles"],
                    "label": f"{glyph} {r['name']}", "effective_kind": ek})
    return out


def get_country_title_list(country_name, limit=200):
    """한 국가가 우승한 대회를 연도 최신순으로 나열 — 국가 검색 탭
    상세 패널의 하단 목록용(더블클릭하면 get_intl_tournament_detail로 열림)."""
    conn = get_conn(); c = conn.cursor()
    rows = [dict(r) for r in c.execute(
        """SELECT id, year, kind, name FROM intl_tournaments
           WHERE status='done' AND winner=?
           ORDER BY year DESC, id DESC LIMIT ?""",
        (country_name, limit)).fetchall()]
    conn.close()
    return rows


# 국제대회 토너먼트 스테이지 순서(낮을수록 이른 탈락). F/TP는 스테이지상
# SF보다 뒤지만, 거기 도달한 나라는 항상 placements(우승/준우승/3위/4위)로
# 먼저 잡히므로 이 순서엔 안 넣는다 — F/TP 매치에 이름이 있는데 placements
# 4자리 중 아무 데도 안 걸리는 경우는 정상적으로 없다.
_STAGE_ORDER = ["group", "R32", "R16", "QF", "SF"]
_STAGE_ORDER_IDX = {s: i for i, s in enumerate(_STAGE_ORDER)}


def get_country_tournament_results(country_name, limit=200):
    """한 국가가 '참가'한 모든 완료된 국제대회의 최종 성적을 연도 최신순으로
    반환 - 우승/준우승/3위/4위는 물론, 그 밑이면 몇강에서 떨어졌는지까지
    (예: "8강 탈락", "조별리그 탈락")를 계산해서 담는다.
    [2026-08 신설, 신민용 리포트: "우승 기록만 있고 몇강 갔는지는 안 보인다"]
    intl_entries(참가 여부) + intl_matches(스테이지) + _batch_placements
    (준우승 이상 4자리)를 조합 - 새 kind가 생겨도 stage 이름 체계만 같으면
    그대로 동작한다."""
    from intl_engine import STAGE_KO
    conn = get_conn(); c = conn.cursor()

    tids = [r["tournament_id"] for r in c.execute(
        "SELECT DISTINCT tournament_id FROM intl_entries WHERE country=?",
        (country_name,)).fetchall()]
    if not tids:
        conn.close()
        return []
    ph = ",".join("?" * len(tids))
    tours = [dict(r) for r in c.execute(
        f"""SELECT id, year, kind, name, continent FROM intl_tournaments
            WHERE id IN ({ph}) AND status='done'
            ORDER BY year DESC, id DESC LIMIT ?""", tids + [limit]).fetchall()]
    if not tours:
        conn.close()
        return []
    t_ids = [t["id"] for t in tours]
    placements = _batch_placements(t_ids, conn)

    ph2 = ",".join("?" * len(t_ids))
    # [2026-08] stage뿐 아니라 스코어까지 같이 받아서 대회별 전적(승무패)도
    # 여기서 함께 집계한다 — "결과(몇강 탈락)"와 "상세기록(몇승 몇무 몇패)"을
    # 분리해서 보여달라는 요청 대응.
    match_rows = c.execute(
        f"""SELECT tournament_id, stage, home, away, home_score, away_score
            FROM intl_matches
            WHERE tournament_id IN ({ph2}) AND (home=? OR away=?)
              AND home_score>=0""",
        t_ids + [country_name, country_name]).fetchall()
    stages_by_tid = {}
    record_by_tid = {}
    for r in match_rows:
        tid = r["tournament_id"]
        stages_by_tid.setdefault(tid, set()).add(r["stage"])
        is_home = (r["home"] == country_name)
        my_score = r["home_score"] if is_home else r["away_score"]
        opp_score = r["away_score"] if is_home else r["home_score"]
        rec = record_by_tid.setdefault(tid, {"w": 0, "d": 0, "l": 0})
        if my_score > opp_score:   rec["w"] += 1
        elif my_score < opp_score: rec["l"] += 1
        else:                      rec["d"] += 1

    # [2026-08 신설] 월드컵 예선(wc_qual)은 결승/3·4위전이 없어 _batch_placements로
    # 못 잡는다 — 본선 진출 여부는 qual_results(최종 통과국 명단)로 따로 확인.
    qual_tids = [t["id"] for t in tours if t["kind"] == "wc_qual"]
    qualified_by_tid = {}
    if qual_tids:
        for t in tours:
            if t["kind"] != "wc_qual":
                continue
            qr = c.execute(
                "SELECT 1 FROM qual_results WHERE target_year=? AND kind='world' "
                "AND country=? LIMIT 1", (t["year"], country_name)).fetchone()
            qualified_by_tid[t["id"]] = bool(qr)

    # [2026-08 신설, 신민용 리포트: "유로 예선이 종류=cont_qual 그대로 뜨고
    # 결과가 '기록 없음'으로만 뜬다"] 유로 예선(cont_qual)도 wc_qual과 완전히
    # 같은 성격(결승 없이 '본선 진출 여부'만 의미 있음)인데 이 특수 처리가
    # wc_qual만 잡고 있었다 — qual_results(kind='continent')로 통과 여부를
    # 확인하는 동일한 로직을 cont_qual에도 적용한다.
    cont_qual_tids = [t["id"] for t in tours if t["kind"] == "cont_qual"]
    cont_qualified_by_tid = {}
    if cont_qual_tids:
        for t in tours:
            if t["kind"] != "cont_qual":
                continue
            _cont = (t.get("continent") or "").strip() or "유럽"
            qr = c.execute(
                "SELECT 1 FROM qual_results WHERE target_year=? AND kind='continent' "
                "AND continent=? AND country=? LIMIT 1",
                (t["year"], _cont, country_name)).fetchone()
            cont_qualified_by_tid[t["id"]] = bool(qr)
    conn.close()

    out = []
    for t in tours:
        pl = placements.get(t["id"], {})
        rec = record_by_tid.get(t["id"], {"w": 0, "d": 0, "l": 0})
        record_str = f"{rec['w']}승 {rec['d']}무 {rec['l']}패"
        if t["kind"] == "wc_qual":
            # 예선은 우승/준우승 개념이 없다 — "본선 진출" 여부와 탈락 시
            # 어느 라운드(조별리그/플레이오프)에서 떨어졌는지만 의미가 있다.
            if qualified_by_tid.get(t["id"]):
                result, tier = "🎫 본선 진출", 2
            else:
                stages = stages_by_tid.get(t["id"], set())
                if "qual_po" in stages:
                    result, tier = "플레이오프 탈락", 1
                elif "qual_group" in stages:
                    result, tier = "조별리그 탈락", 1
                else:
                    result, tier = "기록 없음", 0
        elif t["kind"] == "cont_qual":
            # [2026-08 신설] 유로 예선도 wc_qual과 동일한 성격 — 본선
            # 진출/탈락만 의미 있다.
            if cont_qualified_by_tid.get(t["id"]):
                result, tier = "🎫 본선 진출", 2
            else:
                stages = stages_by_tid.get(t["id"], set())
                if "qual_group" in stages:
                    result, tier = "조별리그 탈락", 1
                else:
                    result, tier = "기록 없음", 0
        elif pl.get("winner") == country_name:
            result, tier = "🥇 우승", 5
        elif pl.get("runner_up") == country_name:
            result, tier = "🥈 준우승", 4
        elif pl.get("third") == country_name:
            result, tier = "🥉 3위", 3
        elif pl.get("fourth") == country_name:
            result, tier = "4위", 2
        else:
            stages = stages_by_tid.get(t["id"], set())
            reached = [s for s in stages if s in _STAGE_ORDER_IDX]
            if reached:
                best = max(reached, key=lambda s: _STAGE_ORDER_IDX[s])
                result, tier = f"{STAGE_KO.get(best, best)} 탈락", 1
            else:
                result, tier = "기록 없음", 0
        # [2026-08 버그수정, 신민용 리포트: "태국이 예선 조별리그까지 들어갔는데
        # 연도만 뜨고 대회/종류/결과가 통째로 비어있다"] intl_tournaments.name/
        # kind 컬럼엔 NOT NULL 제약이 없어서, 과거 세이브에 섞여 있던 일부
        # 레거시 행은 name이 NULL인 채로 저장돼 있었다 — QTableWidgetItem(None)에
        # PyQt가 예외를 던지면서 그 행의 연도 칸만 채워지고 나머지가 통째로
        # 빈 채로 멈췄다(콘솔에 트레이스백만 조용히 찍히고 앱은 안 죽는 PyQt
        # 특성상 원인이 안 보였음). 여기서 한 번 걸러서 항상 유효한 문자열을
        # 보장한다 — 데이터 자체가 비정상인 레거시 행이라도 화면은 안 깨지게.
        out.append({"id": t["id"], "year": t["year"],
                     "kind": t["kind"] or "?",
                     "effective_kind": _effective_kind(t["kind"] or "", t["name"] or "", t.get("year")),
                     "name": _country_result_name(t),
                     "result": result, "tier": tier,
                     "record": record_str})
    return out


def _country_result_name(t):
    """[2026-08 신설, 신민용 리포트: "국가별 기록에 지역컵 대회명만 뜨는데
    어느 지역인지도 같이 보여줘"] 지역컵(kind='region')은 대회명만 봐서는
    무슨 지역인지 바로 안 보인다(예: 'WAFF 챔피언십') — REGION_CUP_NAME을
    거꾸로 뒤져서 "WAFF 챔피언십(서아시아)"처럼 지역명을 괄호로 붙인다.

    [2026-08 수정, 신민용 리포트: "유로 예선인데 대회명이 '2013 유럽
    네이션스컵 예선'으로 뜬다. '유로(EURO) 예선'으로 떠야 한다" → 이후
    "2000 유럽 네이션스컵 예선이 2000 유로 예선으로 잘못 바뀌었다,
    이땐 유로가 아니라 유럽 네이션스컵"] cont_qual은 두 가지 서로 다른
    본선(유럽 네이션스컵/유로(EURO))의 예선을 겸한다 — _is_euro_cycle_year
    로 그 해가 어느 주기인지 실제로 판정해서, 유로 해면 연도 없이
    "유로(EURO) 예선"(본선 "유로(EURO)"와 표기 통일, 연도는 이미 별도
    컬럼에 있음), 대륙컵 해면 원래 저장된 이름("{year} 유럽 네이션스컵
    예선")을 그대로 쓴다. DB에 이미 저장된 과거 기록도(재시뮬레이션
    없이) 이 함수 하나로 즉시 올바르게 보인다."""
    if t["kind"] == "cont_qual" and _is_euro_cycle_year(t["year"]):
        from constants import EURO_NAME
        return f"{EURO_NAME} 예선"
    name = t["name"] or f"{t['year']}년 대회(이름 없음)"
    if t["kind"] == "region":
        from constants import REGION_CUP_NAME
        cup_to_region = {v: k for k, v in REGION_CUP_NAME.items()}
        region = cup_to_region.get(name)
        if region:
            return f"{name}({region})"
    return name


def search_countries(name_query=None, continent=None, grade=None, grade_type="national"):
    """list_countries()에 이름 검색만 얹은 래퍼 — 팀 검색 탭의 search_teams()와
    같은 UX(대륙/등급 필터 + 자유 검색어)를 국가 검색 탭에도 제공하기 위함.
    기존 list_countries() 시그니처/동작은 그대로 둬서 다른 호출부(리그 검색
    탭의 국가 콤보 등)에 영향이 없다.

    [2026-08 신설, 신민용 요청] 이 함수만 grade_type 기본값을 "national"로
    둔다 — search_countries()는 오직 "국가 검색"(국제대회 우승 기록) 탭
    에서만 쓰이는데, 그 화면은 클럽 리그가 아니라 국가대표 실력이 맞는
    기준이라 list_countries()의 기본값(league)과 다르게 오버라이드한다."""
    rows = list_countries(continent=continent, grade=grade, grade_type=grade_type)
    if name_query:
        q = name_query.strip().lower()
        rows = [r for r in rows if q in r["name"].lower()]
    return rows


# ─────────────────────────────────────────
# 6. 대회 상세(조별리그 순위 + 토너먼트 대진) — 월드컵/네이션스컵
# ─────────────────────────────────────────
# [성능] 아래 함수들은 전부 이미 끝난 대회의 intl_matches/cl_matches를
# 그대로 읽기만 한다. 새로 시뮬레이션하는 게 전혀 없으므로(대회당 매치 수는
# 많아야 수십 개 고정) 리그 검색의 지연시뮬과 달리 트리거할 것 자체가 없다
# — 순수 조회라 몇 밀리초 수준.

_INTL_KO_STAGE_ORDER = ["qual_po", "R32", "R16", "QF", "SF", "TP", "F"]


def get_intl_tournament_detail(tournament_id):
    """월드컵/네이션스컵 한 대회의 조별리그 순위표 + 토너먼트(녹아웃) 대진."""
    from intl_engine import STAGE_KO
    conn = get_conn(); c = conn.cursor()

    t_row = c.execute(
        "SELECT id, year, kind, continent FROM intl_tournaments WHERE id=?",
        (tournament_id,)).fetchone()

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
            "WHERE tournament_id=? AND stage IN ('group','qual_group') AND home_score>=0",
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
        "('group','qual_group') AND home_score>=0 ORDER BY id",
        (tournament_id,)).fetchall()
    conn.close()

    ko_by_stage = {}
    for m in ko_rows:
        ko_by_stage.setdefault(m["stage"], []).append(dict(m))
    knockout = [{"stage": s, "stage_ko": STAGE_KO.get(s, s), "matches": ko_by_stage[s]}
                for s in _INTL_KO_STAGE_ORDER if s in ko_by_stage]

    # [2026-08 버그수정, 신민용 리포트: "세계 축구 기록실에서 조 2위까지
    # 다 흰색(진출)으로 칠해지는데, 이건 실제로 2위한테 진출 경로가 아예
    # 없는 대회(예: 아시아 월드컵 예선 — 조 1위만 직행, 와일드카드/PO
    # 없음)에서도 똑같이 그런다 — 버그 맞다"] 바로 아래 "대진표 등장
    # 팀 = 진출"이라는 원래 로직은 예선(wc_qual/cont_qual) 중에서도
    # 플레이오프 자체가 없는 체제(조 1위 전원 직행, 2위는 기회 0)에서는
    # knockout이 통째로 비어있어 qualified가 빈 set으로 남고, 그러면
    # world_browser_window._build_groups_grid가 "qualified가 비어있으면
    # 순위<2로 잠정 표시"라는 폴백을 타 버려서 — 이미 완전히 끝난
    # 대회인데도 2위까지 흰색으로 뜬다. 예선은 knockout 등장 여부로
    # 추측하지 않고, intl_engine.get_qual_advance_status(실제 쿼터/
    # 와일드카드/PO 설정을 그대로 재현하는 함수, schedule_window가 쓰는
    # 것과 동일)로 확정된 진출 여부를 직접 계산한다 — 이 함수를 호출하는
    # 시점엔 항상 status='done'인 완료된 예선만 대상이므로(get_country_
    # tournament_results 등 호출부가 이미 done만 필터링) 'direct'/'po_ok'
    # 인 나라만 확정 진출로 본다.
    qualified = set()
    if t_row and t_row["kind"] in ("wc_qual", "cont_qual"):
        from intl_engine import get_qual_advance_status
        status_map = get_qual_advance_status(dict(t_row))
        qualified = {nat for nat, s in status_map.items() if s in ("direct", "po_ok")}
    elif knockout:
        first_stage_matches = knockout[0]["matches"]
        for m in first_stage_matches:
            if m.get("home"):
                qualified.add(m["home"])
            if m.get("away"):
                qualified.add(m["away"])

    return {"groups": groups, "knockout": knockout, "qualified": qualified}


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


def get_cl_tournament_detail(tournament_id, entry_table="cl_entries",
                              match_table="cl_matches", tournament_table="cl_tournaments"):
    """챔피언스리그(또는 매개변수로 지정한 다른 클럽대항전) 한 대회의 리그
    스테이지 순위표 + 토너먼트(녹아웃) 대진.
    [2026-07 스위스 방식 개편] 조별리그가 없어져서 groups 대신 단일
    league_standings 리스트를 반환한다. 기존 groups 키를 참조하던 옛
    UI가 있다면 빈 dict로라도 동작하도록 groups=[]는 계속 넣어둔다."""
    from competition.champions_engine import STAGE_KO
    conn = get_conn(); c = conn.cursor()

    entries = [dict(r) for r in c.execute(
        f"SELECT team_id, team_name, flag, country, grade FROM {entry_table} "
        f"WHERE tournament_id=?", (tournament_id,)).fetchall()]
    # [2026-07 신설, 신민용 요청] 리그 스테이지 순위표 색칠(직행/플레이오프)에
    # 대륙별 컷 라인이 필요해서 continent도 같이 조회한다.
    t_row = c.execute(f"SELECT continent FROM {tournament_table} WHERE id=?", (tournament_id,)).fetchone()
    continent = t_row["continent"] if t_row else None
    league_tbl = {e["team_id"]: {
        "team_id": e["team_id"], "name": e["team_name"], "flag": e["flag"],
        "country": e["country"], "grade": e["grade"],
        "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0} for e in entries}

    for m in c.execute(
            f"SELECT home_team_id, away_team_id, home_score, away_score "
            f"FROM {match_table} WHERE tournament_id=? AND stage='league' "
            f"AND home_score>=0", (tournament_id,)).fetchall():
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
        f"SELECT stage, home_team_id, away_team_id, home_score, away_score, "
        f"pso_winner, pso_score FROM {match_table} WHERE tournament_id=? AND "
        f"stage NOT IN ('league') AND home_score>=0 ORDER BY id",
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
            "continent": continent, "comp_kind": _comp_kind_from_entry_table(entry_table)}


def _comp_kind_from_entry_table(entry_table: str) -> str:
    """[2026-08 신설, 신민용 리포트: "역대 기록 상세의 직행/플레이오프
    범례가 유로파/컨퍼런스도 챔스 컷(북남미 1~16/17~48)을 그대로 쓰고
    있다"] get_cl_tournament_detail이 챔스/유로파/컨퍼런스 셋 다 처리
    하는 공용 함수라, entry_table로 실제 어느 대회인지 구분해 UI(world_
    browser_window._build_league_standings_table)에 넘겨준다."""
    return {"cl_entries": "champions", "el_entries": "europa",
            "ecl_entries": "conference"}.get(entry_table, "champions")


def get_el_tournament_detail(tournament_id):
    """[2026-08 신설] 유로파리그 대회 상세 — get_cl_tournament_detail을 el_* 테이블로."""
    return get_cl_tournament_detail(tournament_id, entry_table="el_entries",
                                     match_table="el_matches", tournament_table="el_tournaments")


def get_ecl_tournament_detail(tournament_id):
    """[2026-08 신설] 컨퍼런스리그 대회 상세 — get_cl_tournament_detail을 ecl_* 테이블로."""
    return get_cl_tournament_detail(tournament_id, entry_table="ecl_entries",
                                     match_table="ecl_matches", tournament_table="ecl_tournaments")


# ─────────────────────────────────────────
# 7. 역대 클럽 월드컵 기록 (2026-07 신설)
# ─────────────────────────────────────────
_CWC_KO_STAGE_ORDER = ["R16", "QF", "SF", "TP", "F"]


def _batch_cwc_placements(tournament_ids, conn):
    """get_cl_history의 _batch_cl_placements와 완전히 동일한 패턴 —
    cwc_matches는 cl_matches와 스키마가 같으므로 champions_engine._winner_of를
    그대로 재사용한다."""
    from competition.champions_engine import _winner_of
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


# [2026-08 신설, 신민용 요청: "클럽 월드컵에도 역대 1~4위를 가장 많이
# 차지한 팀 순위를 보여달라"] 대륙 필터가 없는 대회라(원래부터 전
# 대륙이 섞여 참가) continent 인자 없이 전체 역사를 그대로 집계한다.
def get_cwc_rank_leaders():
    rows = get_cwc_history(limit=999999)
    return _rank_leaders_from_rows(rows, ("winner", "runner_up", "third", "fourth"),
                                    "{key}_name", "{key}_country")


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

def get_po_results(league_id, year, direction="relegation"):
    """[2026-07 신설, 확장] 이 리그가 관련된 승강 플레이오프 결과를 그 해
    기준으로 반환한다. direction으로 어느 쪽 경계를 볼지 고른다:
      - "relegation": 이 리그가 위(upper)인 경계 — 강등 플레이오프
        (예: 2부 페이지에서 "2부→3부" 경계)
      - "promotion":  이 리그가 아래(lower)인 경계 — 승급 플레이오프
        (예: 2부 페이지에서 "1부→2부" 경계, 즉 2부팀이 1부로 올라가려는 쪽)
    [2026-07 신민용 확정: "칸을 두 개로 나눠서 좌측엔 승급, 우측엔 강등을
    같이 보여줘야 한다"] 예전엔 upper만 봐서 맨 위/맨 아래 리그가 아닌
    중간 리그(2부/3부 등)는 자기 위쪽 경계(승급 PO)가 아예 안 보였다 —
    같은 화면에 두 방향을 나란히 보여주려면 이 함수를 양방향으로 호출할
    수 있어야 한다.

    시즌 상세(연도 클릭) 화면 하단에 "승강전 어떻게 진행됐는지"를 보여주기
    위한 용도 — po_matches/po_tournaments는 promotion_log와 마찬가지로
    별도 정리 없이 계속 남아있으므로 과거 시즌도 그대로 조회된다.

    홈/원정 각각의 소속 부수(tier)도 함께 반환한다 — 승강 PO는 정의상
    서로 다른 부수 팀끼리 붙으므로("리서(4부) vs UNA(4부)"처럼 같은
    부수로 잘못 보이면 혼란스럽다), 화면에서 "팀명(N부)"로 명확히
    구분해서 보여줄 수 있게 한다."""
    conn = get_conn()
    _match_field = "t.upper_league_id" if direction == "relegation" else "t.lower_league_id"
    rows = conn.execute(
        f"""SELECT m.match_key, m.home_team_id, m.away_team_id,
                  m.home_score, m.away_score, m.pso_score, m.pso_winner, m.is_boundary,
                  t.upper_league_id, t.lower_league_id
           FROM po_matches m JOIN po_tournaments t ON m.tournament_id=t.id
           WHERE t.year=? AND {_match_field}=?
             AND m.home_score!=-1
           ORDER BY m.id""",
        (year, league_id)).fetchall()
    out = []
    _STAGE_KO = {"Q1": "하위리그 예선", "SF1": "준결승", "SF2": "준결승",
                 "LF": "하위리그 결승", "F": "최종 승강전"}
    for r in rows:
        upper_tier = conn.execute("SELECT tier FROM leagues WHERE id=?",
                                   (r["upper_league_id"],)).fetchone()
        lower_tier = conn.execute("SELECT tier FROM leagues WHERE id=?",
                                   (r["lower_league_id"],)).fetchone()
        upper_tier = upper_tier["tier"] if upper_tier else 0
        lower_tier = lower_tier["tier"] if lower_tier else 0
        ht = conn.execute("SELECT name, league_id FROM teams WHERE id=?",
                           (r["home_team_id"],)).fetchone()
        at = conn.execute("SELECT name, league_id FROM teams WHERE id=?",
                           (r["away_team_id"],)).fetchone()
        # [주의] teams.league_id는 이미 PO 결과가 반영된 "현재" 소속이라,
        # PO 시작 시점의 소속(위/아래 어느 쪽 대표였는지)과 다를 수 있다
        # (이겨서 이미 위 리그로 옮겨간 뒤일 수 있음). 그래서 teams.league_id
        # 로 tier를 되짚지 않고, 이 매치가 속한 tournament의 upper/lower_tier
        # 값 자체를 그대로 쓴다 — 다만 예선(Q1/SF/LF)은 양쪽 다 lower 소속
        # 이므로 그 경우엔 둘 다 lower_tier로 표시한다.
        _is_boundary = bool(r["is_boundary"])
        out.append({
            "stage": _STAGE_KO.get(r["match_key"], r["match_key"]),
            "home": ht["name"] if ht else "?", "away": at["name"] if at else "?",
            "home_tier": upper_tier if _is_boundary else lower_tier,
            "away_tier": lower_tier,
            "home_score": r["home_score"], "away_score": r["away_score"],
            "pso_score": r["pso_score"], "is_boundary": _is_boundary,
            "home_won": (r["pso_winner"] == r["home_team_id"]) if r["pso_winner"]
                        else (r["home_score"] > r["away_score"]),
        })
    conn.close()
    return out


def get_team_history(team_id: int):
    """[2026-07 신설] "팀 검색" → 팀 클릭 시 보여줄 연도별 기록.
    리그 성적(순위+승격/강등 여부), 국내컵 도달 라운드, 챔피언스리그
    도달 스테이지를 연도 내림차순으로 묶어 반환한다.

    감독 이름 등은 아예 추적하는 시스템이 없어서(신민용 확정: 필요 없음)
    안 넣는다 — 순수 "그 해 성적 기록"만.

    [자료 출처] 리그 성적은 match_results(+archive)에서 직접 집계(항상
    정확, 앞서 get_league_champions와 동일한 원칙). 승격/강등 여부는
    promotion_log를 팀 이름+연도로 조회(팀 이름 충돌 가능성은 낮지만
    있을 수 있어 from_league_id로 한 번 더 좁힌다). 컵대회/챔스는
    cup_matches/cl_matches가 전 세계 모든 팀의 경기를 매년 계속
    쌓아두는(삭제 안 함) 원본 데이터라, 그 팀의 마지막 경기 라운드를
    "도달한 곳"으로 역산한다."""
    from game_engine import get_league_standings
    conn = get_conn()

    trow = conn.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
    if not trow:
        conn.close()
        return {"awards": {"league": 0, "cup": 0, "cl": 0, "cwc": 0}, "years": []}
    team_name = trow["name"]

    yr_rows = conn.execute(
        """SELECT DISTINCT season, year, league_id FROM match_results
           WHERE (home_team_id=? OR away_team_id=?) AND home_score>=0
           UNION
           SELECT DISTINCT season, year, league_id FROM match_results_archive
           WHERE (home_team_id=? OR away_team_id=?) AND home_score>=0
           UNION
           SELECT DISTINCT season, year, league_id FROM league_season_standings
           WHERE team_id=?""",
        (team_id, team_id, team_id, team_id, team_id)).fetchall()
    by_year = {}
    for r in yr_rows:
        by_year[(r["year"], r["season"])] = r["league_id"]

    _CL_STAGE_KO = {"league": "리그 스테이지", "PO": "플레이오프",
                    "R32": "32강", "R16": "16강", "QF": "8강", "SF": "4강",
                    "F": "결승", "TP": "3/4위전"}
    _CL_STAGE_ORDER = ["league", "PO", "R32", "R16", "QF", "SF", "F"]
    # [2026-08 신설, 신민용 리포트: "팀 검색 이후 기록에 클럽 월드컵 기록이
    # 없는거 같은데?"] 리그/컵/챔스는 있는데 클럽 월드컵만 빠져 있었다 —
    # club_world_cup_engine.py 실제 stage 값(group/SF/TP/F)에 맞춘 매핑.
    _CWC_STAGE_KO = {"group": "조별리그", "R16": "16강", "QF": "8강",
                     "SF": "4강", "TP": "3/4위전", "F": "결승"}
    _CWC_STAGE_ORDER = ["group", "R16", "QF", "SF", "TP", "F"]

    def _cwc_match_winner(m):
        """그 경기의 승자 team_id. 무승부면 PSO 승자, PSO도 없으면 None."""
        if m["home_score"] == m["away_score"]:
            return m["pso_winner"] or None
        return m["home_team_id"] if m["home_score"] > m["away_score"] else m["away_team_id"]

    def _wdl_record(matches, team_id):
        """[2026-08 신설, 신민용 요청: "팀 검색에서 리그뿐 아니라 국내컵/
        챔스/클럽월드컵도 각자 승무패를 보여달라"] 경기 목록(홈/원정/스코어,
        토너먼트면 pso_winner도)을 받아 그 팀 기준 "W승 D무 L패" 문자열을
        만든다. 토너먼트는 무승부면 승부차기로 갈리므로(pso_winner 존재),
        그 경우는 무승부가 아니라 승/패로 센다 — 리그처럼 pso_winner가 없는
        진짜 무승부만 "무"로 집계된다.
        """
        w = d = l = 0
        for m in matches:
            is_home = m["home_team_id"] == team_id
            my_score = m["home_score"] if is_home else m["away_score"]
            opp_score = m["away_score"] if is_home else m["home_score"]
            if my_score > opp_score:
                w += 1
            elif my_score < opp_score:
                l += 1
            else:
                pso_winner = m["pso_winner"] if "pso_winner" in m.keys() else None
                if pso_winner:
                    if pso_winner == team_id:
                        w += 1
                    else:
                        l += 1
                else:
                    d += 1
        return f"{w}승 {d}무 {l}패"

    out = []
    for (year, season), league_id in sorted(by_year.items(), key=lambda x: -x[0][0]):
        entry = {"year": year, "league": None, "cup": None, "cl": None, "cwc": None,
                  "league_record": None, "cup_record": None, "cl_record": None, "cwc_record": None,
                  # [2026-08 신설, 신민용 요청: "우승했으면 원래 챔스 표시색(금색)으로
                  # 하이라이트"] UI가 텍스트("[1등]"/"[우승]")를 다시 파싱하지
                  # 않도록, 여기서 판정한 결과를 명시적 bool로 같이 내려준다.
                  "league_champion": False, "cup_champion": False, "cwc_champion": False,
                  "cl_champion": False, "cl_kind": None}

        # ── 리그 성적 ──────────────────────────────────────
        lg = conn.execute("SELECT name, tier FROM leagues WHERE id=?", (league_id,)).fetchone()
        standings = get_league_standings(league_id, season=season, conn=conn)
        rank = next((i + 1 for i, s in enumerate(standings) if s["id"] == team_id), None)
        my_row = next((s for s in standings if s["id"] == team_id), None)
        if my_row:
            entry["league_record"] = f"{my_row['wins']}승 {my_row['draws']}무 {my_row['losses']}패"
        if lg and rank:
            move = conn.execute(
                """SELECT pl.to_tier, l2.name as dest_name FROM promotion_log pl
                   JOIN leagues l2 ON pl.to_league_id=l2.id
                   WHERE pl.year=? AND pl.team_name=? AND pl.from_league_id=?""",
                (year, team_name, league_id)).fetchone()
            if not move:
                # [2026-07 버그수정, 신민용 리포트로 발견] 새 게임 시작 시
                # 초기 역사(2000~2006년 등)를 미리 채워 넣는 "시드" 과정이
                # from_league_id/to_league_id가 생기기 전 코드로 만들어진
                # 것이라(실측: promotion_log 9688건 전부 0), 정확 매칭이
                # 안 통한다. 이 경우엔 원산지 리그 이름(league_name, 이
                # 컬럼의 관례상 "떠난" 리그 이름)과 국가(country_id로
                # 리그를 좁혀서)로 대체 매칭한다 — 나라별로 흔히 겹치는
                # 이름("프리메라 디비시온" 등) 문제를 국가로 한 번 더
                # 걸러서 피한다.
                _country_row = conn.execute(
                    "SELECT country_id FROM leagues WHERE id=?", (league_id,)).fetchone()
                _cid = _country_row["country_id"] if _country_row else None
                move = conn.execute(
                    """SELECT pl.to_tier, l2.name as dest_name FROM promotion_log pl
                       JOIN leagues l2 ON l2.country_id=? AND l2.tier=pl.to_tier
                       WHERE pl.year=? AND pl.team_name=? AND pl.from_tier=?
                         AND pl.league_name=?""",
                    (_cid, year, team_name, lg["tier"], lg["name"])).fetchone()
            move_txt = ""
            if move:
                kind = "승격" if move["to_tier"] < lg["tier"] else "강등"
                move_txt = f"  [{move['dest_name']}({move['to_tier']}부)로 {kind}]"
            entry["league"] = f"{lg['name']}({lg['tier']}부) [{rank}등/{len(standings)}팀]{move_txt}"
            # [2026-08 신설] 1부 우승만 "리그 우승"으로 친다 — 하위 부수
            # 1등은 보통 승격이라 이미 승격색(파란색)이 우선 표시되고,
            # 신민용 확정: "1부에서 1등만 금색".
            entry["league_champion"] = (lg["tier"] == 1 and rank == 1)

        # ── 국내컵 도달 라운드 ─────────────────────────────
        # [2026-08 버그수정, 신민용 리포트: "팀 커리어에 결승 이렇게 뜨는데
        # 이거 결승이 아니라 준우승 이런식으로 자세하게 떠야 한다"] 예전엔
        # 결승에서 졌어도 그냥 라운드 이름("결승")만 그대로 보여줘서 우승과
        # 구분이 안 갔다 — 챔스/클럽월드컵 블록은 이미 진작에 "[준우승]"/
        # "[3위]"/"[4위]"로 승패를 구분해서 보여주고 있었는데 컵대회만
        # 빠져 있었다. 같은 패턴(_cwc_match_winner로 승패 판정)으로 통일한다.
        cup_t = conn.execute(
            """SELECT ct.id, ct.name, ct.winner_team_id
               FROM cup_tournaments ct JOIN cup_entries ce ON ce.tournament_id=ct.id
               WHERE ct.year=? AND ce.team_id=?""", (year, team_id)).fetchone()
        if cup_t:
            cup_matches = conn.execute(
                """SELECT round_name, round_idx, home_team_id, away_team_id,
                          home_score, away_score, pso_winner
                   FROM cup_matches
                   WHERE tournament_id=? AND (home_team_id=? OR away_team_id=?)
                     AND home_score!=-1
                   ORDER BY round_idx DESC""",
                (cup_t["id"], team_id, team_id)).fetchall()
            if cup_matches:
                last_m = cup_matches[0]
                last_round = last_m["round_name"]
                if cup_t["winner_team_id"] == team_id:
                    entry["cup"] = f"{cup_t['name']} [우승]"
                    entry["cup_champion"] = True
                elif last_round == "결승":
                    entry["cup"] = f"{cup_t['name']} [준우승]"
                elif last_round == "3·4위전":
                    won_tp = _cwc_match_winner(dict(last_m)) == team_id
                    entry["cup"] = f"{cup_t['name']} [{'3위' if won_tp else '4위'}]"
                else:
                    entry["cup"] = f"{cup_t['name']} [{last_round} 탈락]"
                entry["cup_record"] = _wdl_record(cup_matches, team_id)

        # ── 클럽 대항전(챔스/유로파/컨퍼런스) 도달 스테이지 ────────
        # [2026-08 신설, 신민용 확정: "챔피언스리그 칸을 '클럽 대항전'으로
        # 통합하고, 그 해 참가한 대회가 챔스면 파랑/유로파면 주황/컨퍼런스면
        # 초록으로 표시"] 워터폴 구조상 한 해에 한 팀은 셋 중 최대 하나에만
        # 속하므로(같은 나라 리그 순위표에서 챔스/유로파/컨퍼런스 슬롯이
        # 겹치지 않게 위에서부터 떼어감), 세 테이블을 순서대로 확인해
        # 실제로 걸린 것 하나만 채운다.
        for _kind, _tt, _et, _mt in (
                ("champions", "cl_tournaments", "cl_entries", "cl_matches"),
                ("europa", "el_tournaments", "el_entries", "el_matches"),
                ("conference", "ecl_tournaments", "ecl_entries", "ecl_matches")):
            cl_t = conn.execute(
                f"""SELECT clt.id, clt.name, clt.winner_team_id
                   FROM {_tt} clt JOIN {_et} cle ON cle.tournament_id=clt.id
                   WHERE clt.year=? AND cle.team_id=?""", (year, team_id)).fetchone()
            if not cl_t:
                continue
            cl_matches = conn.execute(
                f"""SELECT stage, home_team_id, away_team_id, home_score, away_score, pso_winner
                   FROM {_mt}
                   WHERE tournament_id=? AND (home_team_id=? OR away_team_id=?)
                     AND home_score!=-1""",
                (cl_t["id"], team_id, team_id)).fetchall()
            if not cl_matches:
                continue
            reached = max((m["stage"] for m in cl_matches),
                          key=lambda s: _CL_STAGE_ORDER.index(s) if s in _CL_STAGE_ORDER else -1)
            stage_ko = _CL_STAGE_KO.get(reached, reached)
            if cl_t["winner_team_id"] == team_id:
                entry["cl"] = f"{cl_t['name']} [우승]"
                entry["cl_champion"] = True
            elif reached == "F":
                entry["cl"] = f"{cl_t['name']} [준우승]"
            else:
                entry["cl"] = f"{cl_t['name']} [{stage_ko} 탈락]"
            entry["cl_record"] = _wdl_record(cl_matches, team_id)
            entry["cl_kind"] = _kind   # "champions"/"europa"/"conference" — UI가 색 정할 때 씀
            break

        # ── 클럽 월드컵 도달 스테이지 ─────────────────────────
        # [2026-08 신설, 신민용 리포트: "팀 검색 이후 기록에 클럽 월드컵
        # 기록이 없다"] 챔피언스리그 블록과 완전히 같은 패턴(entries로
        # 그해 출전 여부 확인 → matches로 도달한 가장 깊은 스테이지 역산).
        # 4년 주기 대회라 대부분의 연도엔 cwc_t 자체가 없어서 entry["cwc"]가
        # None으로 남는 게 정상 — UI(world_browser_window.py)도 None이면
        # 그 줄 자체를 안 그린다.
        cwc_t = conn.execute(
            """SELECT cwt.id, cwt.name, cwt.winner_team_id
               FROM cwc_tournaments cwt JOIN cwc_entries cwe ON cwe.tournament_id=cwt.id
               WHERE cwt.year=? AND cwe.team_id=?""", (year, team_id)).fetchone()
        if cwc_t:
            cwc_matches = conn.execute(
                """SELECT stage, home_team_id, away_team_id, home_score, away_score, pso_winner
                   FROM cwc_matches
                   WHERE tournament_id=? AND (home_team_id=? OR away_team_id=?)
                     AND home_score!=-1""",
                (cwc_t["id"], team_id, team_id)).fetchall()
            if cwc_matches:
                reached = max((m["stage"] for m in cwc_matches),
                              key=lambda s: _CWC_STAGE_ORDER.index(s) if s in _CWC_STAGE_ORDER else -1)
                if cwc_t["winner_team_id"] == team_id:
                    entry["cwc"] = f"{cwc_t['name']} [우승]"
                    entry["cwc_champion"] = True
                elif reached == "F":
                    entry["cwc"] = f"{cwc_t['name']} [준우승]"
                elif reached == "TP":
                    # 3/4위전은 다음 스테이지가 없어서(챔스의 'F'처럼) 승패로
                    # 3위/4위를 직접 갈라줘야 의미가 있다.
                    tp = next(m for m in cwc_matches if m["stage"] == "TP")
                    won_tp = _cwc_match_winner(tp) == team_id
                    entry["cwc"] = f"{cwc_t['name']} [{'3위' if won_tp else '4위'}]"
                else:
                    stage_ko = _CWC_STAGE_KO.get(reached, reached)
                    entry["cwc"] = f"{cwc_t['name']} [{stage_ko} 탈락]"
                entry["cwc_record"] = _wdl_record(cwc_matches, team_id)

        if entry["league"] or entry["cup"] or entry["cl"] or entry["cwc"]:
            out.append(entry)

    conn.close()

    # [2026-08 신설, 신민용 요청: "팀 검색 우측 기록 맨 위에 '수상' 칸을
    # 만들어서 리그/컵/챔스/클럽WC 우승 횟수를 한눈에 보여달라"] 방금
    # 만든 연도별 entry 리스트를 그대로 훑어서 "[1등]"(리그 우승) /
    # "[우승]"(컵·챔스·클럽WC 전부 이 표기 통일) 개수만 세면 된다 —
    # 새 쿼리 없이 문자열만 확인하는 거라 비용이 사실상 0에 가깝다.
    # [2026-08 확장, 신민용 확정: "수상 합계는 챔스/유로파/컨퍼런스를
    # 하나로 합치지 않고 각자 색으로 따로"] 기존 awards["cl"]은 하나로
    # 뭉뚱그렸는데, 이제 cl_kind로 구분해서 champions/europa/conference
    # 우승 횟수를 따로 센다. 기존 awards["cl"](합계)도 하위 호환을 위해
    # 그대로 계산은 해두되(옛 UI가 참조할 수 있으므로), UI는 새 필드
    # (cl_champions/el_champions/ecl_champions)를 우선 쓴다.
    awards = {"league": 0, "cup": 0, "cl": 0, "cwc": 0,
              "cl_champions": 0, "el_champions": 0, "ecl_champions": 0}
    # [2026-08 신설, 신민용 요청: "[1등] 뒤에 팀 수도 붙게 해달라"] 위에서
    # entry["league"]가 "[1등]" → "[1등/N팀]"으로 바뀌면서, 고정 문자열
    # 매칭으로는 더 이상 못 찾는다 — 정규식으로 "[1등"으로 시작하는 걸
    # 잡는다(팀 수 붙어있든 없든 상관없이 매칭). league_champion 필드는
    # "1부 우승"만(신민용 확정) 별도로 좁혀놓은 값이라 이 집계(모든 부수의
    # 1등 횟수)와는 의미가 달라 그대로 못 쓴다.
    import re as _re
    _rank1_re = _re.compile(r"\[1등(?:/\d+팀)?\]")
    for e in out:
        if e["league"] and _rank1_re.search(e["league"]):
            awards["league"] += 1
        if e["cup"] and "[우승]" in e["cup"]:
            awards["cup"] += 1
        if e["cl"] and "[우승]" in e["cl"]:
            awards["cl"] += 1
            if e.get("cl_kind") == "champions":
                awards["cl_champions"] += 1
            elif e.get("cl_kind") == "europa":
                awards["el_champions"] += 1
            elif e.get("cl_kind") == "conference":
                awards["ecl_champions"] += 1
        if e["cwc"] and "[우승]" in e["cwc"]:
            awards["cwc"] += 1
    return {"awards": awards, "years": out}