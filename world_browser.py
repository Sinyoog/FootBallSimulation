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
# 5.5. 국가 검색 (2026-08 신설, 신민용 확정: "월드컵/대륙컵 우승 기록실")
# ─────────────────────────────────────────
# [설계 원칙] intl_tournaments.winner에 우승국이 이미 문자열로 저장돼
# 있으므로, kind별로 GROUP BY만 하면 국가별 집계가 나온다. 대회 종류가
# 늘어나도(새 kind 값 추가) 이 쿼리들은 코드 수정 없이 자동으로 포함한다
# — 화면 라벨만 constants.INTL_TOURNAMENT_KIND_LABELS에 추가하면 됨.

def get_all_countries_trophy_counts():
    """국가명 → {kind: 우승횟수} 매핑을 한 번에 집계.
    [성능] 국가 검색 리스트를 채울 때 국가마다 개별 쿼리하면 국가 수(200+)
    만큼 왕복이 생기므로, GROUP BY 한 번으로 전체를 미리 다 구해둔다."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT winner, kind, COUNT(*) as n FROM intl_tournaments
           WHERE status='done' AND winner != '' GROUP BY winner, kind""").fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["winner"], {})[r["kind"]] = r["n"]
    return out


def get_country_trophy_summary(country_name):
    """한 국가의 kind별 우승 횟수 요약(월드컵 N회 / 대륙컵 N회 / ...).
    반환: [{"kind":, "titles":, "label":}, ...] 우승 많은 순."""
    from constants import INTL_TOURNAMENT_KIND_LABELS as _LBL
    conn = get_conn()
    rows = conn.execute(
        """SELECT kind, COUNT(*) as titles FROM intl_tournaments
           WHERE status='done' AND winner=? GROUP BY kind
           ORDER BY titles DESC""", (country_name,)).fetchall()
    conn.close()
    return [{"kind": r["kind"], "titles": r["titles"],
              "label": _LBL.get(r["kind"], r["kind"])} for r in rows]


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
        f"""SELECT id, year, kind, name FROM intl_tournaments
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
                     "name": t["name"] or f"{t['year']}년 대회(이름 없음)",
                     "result": result, "tier": tier,
                     "record": record_str})
    return out


def search_countries(name_query=None, continent=None, grade=None):
    """list_countries()에 이름 검색만 얹은 래퍼 — 팀 검색 탭의 search_teams()와
    같은 UX(대륙/등급 필터 + 자유 검색어)를 국가 검색 탭에도 제공하기 위함.
    기존 list_countries() 시그니처/동작은 그대로 둬서 다른 호출부(리그 검색
    탭의 국가 콤보 등)에 영향이 없다."""
    rows = list_countries(continent=continent, grade=grade)
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
                  "league_record": None, "cup_record": None, "cl_record": None, "cwc_record": None}

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
            entry["league"] = f"{lg['name']}({lg['tier']}부) [{rank}등]{move_txt}"

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
                elif last_round == "결승":
                    entry["cup"] = f"{cup_t['name']} [준우승]"
                elif last_round == "3·4위전":
                    won_tp = _cwc_match_winner(dict(last_m)) == team_id
                    entry["cup"] = f"{cup_t['name']} [{'3위' if won_tp else '4위'}]"
                else:
                    entry["cup"] = f"{cup_t['name']} [{last_round} 탈락]"
                entry["cup_record"] = _wdl_record(cup_matches, team_id)

        # ── 챔피언스리그 도달 스테이지 ───────────────────────
        cl_t = conn.execute(
            """SELECT clt.id, clt.name, clt.winner_team_id
               FROM cl_tournaments clt JOIN cl_entries cle ON cle.tournament_id=clt.id
               WHERE clt.year=? AND cle.team_id=?""", (year, team_id)).fetchone()
        if cl_t:
            cl_matches = conn.execute(
                """SELECT stage, home_team_id, away_team_id, home_score, away_score, pso_winner
                   FROM cl_matches
                   WHERE tournament_id=? AND (home_team_id=? OR away_team_id=?)
                     AND home_score!=-1""",
                (cl_t["id"], team_id, team_id)).fetchall()
            if cl_matches:
                reached = max((m["stage"] for m in cl_matches),
                              key=lambda s: _CL_STAGE_ORDER.index(s) if s in _CL_STAGE_ORDER else -1)
                stage_ko = _CL_STAGE_KO.get(reached, reached)
                if cl_t["winner_team_id"] == team_id:
                    entry["cl"] = f"{cl_t['name']} [우승]"
                elif reached == "F":
                    entry["cl"] = f"{cl_t['name']} [준우승]"
                else:
                    entry["cl"] = f"{cl_t['name']} [{stage_ko} 탈락]"
                entry["cl_record"] = _wdl_record(cl_matches, team_id)

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
    awards = {"league": 0, "cup": 0, "cl": 0, "cwc": 0}
    for e in out:
        if e["league"] and "[1등]" in e["league"]:
            awards["league"] += 1
        if e["cup"] and "[우승]" in e["cup"]:
            awards["cup"] += 1
        if e["cl"] and "[우승]" in e["cl"]:
            awards["cl"] += 1
        if e["cwc"] and "[우승]" in e["cwc"]:
            awards["cwc"] += 1
    return {"awards": awards, "years": out}