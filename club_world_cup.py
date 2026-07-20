# -*- coding: utf-8 -*-
"""
클럽 월드컵(32팀) 출전팀 선발 로직.

[2026-07 신설, 신민용 확정 규칙]
1) 자동 진출: 대륙별 최근 4시즌 챔스 우승팀 (중복 우승 시 슬롯 하나만 사용,
   남는 슬롯은 랭킹으로 이월)
2) 랭킹 선발: 자동진출 제외 전 참가팀을 최근 4시즌 누적 점수로 정렬
   (우승10 / 준우승7 / 4강5 / 8강3 / 16강1 / 그 이하 0)
   동점이면 최근 시즌 성적 → 리그 스테이지 승점 → 득실차 → 다득점 순으로 비교.
3) 국가당 2팀 제한 — "그 나라가 이미 확보한 총원(자동진출 포함) >= 2"면
   랭킹 선발에서는 몇 등이든 스킵. 자동진출 자체는 이 제한의 예외
   (같은 나라가 자동진출로 3팀 이상이어도 전부 인정).
4) 대륙 쿼터: 유럽12 / 북남미8 / 아시아(+오세아니아)6 / 아프리카6 = 32팀.
   (신민용 확정안 — 실제 FIFA 32팀 대륙 배분을 그대로 쓰지 않고, 이 게임의
   4대륙 챔스 구조에 맞춰 재배분하면서 아프리카 비중을 의도적으로 더 줌.)

이 모듈은 "32팀이 누구인지"만 결정한다. 실제 대진 추첨/경기 시뮬레이션은
별도(클럽월드컵 대회 엔진)에서 이 결과를 입력으로 받아 처리한다.
"""

from database import get_conn

CWC_QUOTA = {"유럽": 12, "북남미": 8, "아시아": 6, "아프리카": 6}
CWC_SEASONS = 4          # "최근 4시즌" 누적 대상 시즌 수
CWC_COUNTRY_CAP = 2      # 랭킹 선발 국가당 최대 인원 (자동진출은 예외)

# 스테이지별 점수. cl_matches.stage 값(R32/R16/QF/SF/F) 기준.
# 우승은 cl_tournaments.winner_team_id로 별도 판정하므로 여기 없음.
_STAGE_POINTS = {"F": 7, "SF": 5, "QF": 3, "R16": 1, "R32": 0}
_STAGE_RANK = {"F": 5, "SF": 4, "QF": 3, "R16": 2, "R32": 1}   # 최고 도달 라운드 판정용
STAGE_POINTS_CHAMPION = 10


def _team_stage_points(conn, tournament_id: int, team_id: int, winner_team_id: int) -> int:
    """[호환용] 팀 1명만 필요할 때 쓰는 단건 버전 — 여러 팀을 한꺼번에
    처리해야 하면 _batch_team_stage_points를 대신 써야 N+1 쿼리를 피한다."""
    if winner_team_id and team_id == winner_team_id:
        return STAGE_POINTS_CHAMPION
    rows = conn.execute(
        """SELECT stage FROM cl_matches
           WHERE tournament_id=? AND stage IN ('F','SF','QF','R16','R32')
             AND (home_team_id=? OR away_team_id=?)""",
        (tournament_id, team_id, team_id)).fetchall()
    if not rows:
        return 0
    best_stage = max((r["stage"] for r in rows), key=lambda s: _STAGE_RANK.get(s, 0))
    return _STAGE_POINTS.get(best_stage, 0)


def _batch_team_stage_points(conn, tournament_id: int, winner_team_id: int) -> dict:
    """[2026-07 신설, 신민용 리포트: "43주차 근처에서 게임이 멈춘다"]
    _team_stage_points를 팀 수만큼 반복 호출하면(4대륙×4시즌×팀당 최대
    48개 = 최대 640회) 매번 개별 쿼리가 나가는 N+1 문제가 있었다 —
    664개 리그짜리 실제 월드 + 여러 시즌 누적된 cl_matches에서는 이게
    체감될 만큼 느려질 수 있다(클럽월드컵 선발도, 매년 도는 챔스
    슬롯 계산도 이 함수를 썼음). 이 대회(tournament_id)의 매치 전체를
    한 번만 조회해서 팀별 최고 성적을 메모리에서 계산 — 쿼리 횟수를
    대회당 1회로 줄인다."""
    rows = conn.execute(
        """SELECT stage, home_team_id, away_team_id FROM cl_matches
           WHERE tournament_id=? AND stage IN ('F','SF','QF','R16','R32')""",
        (tournament_id,)).fetchall()
    best_rank: dict = {}
    for r in rows:
        rank = _STAGE_RANK.get(r["stage"], 0)
        for tid in (r["home_team_id"], r["away_team_id"]):
            if rank > best_rank.get(tid, -1):
                best_rank[tid] = rank
    result = {tid: _STAGE_POINTS.get(
        next(s for s, rk in _STAGE_RANK.items() if rk == rank), 0)
        for tid, rank in best_rank.items()}
    if winner_team_id:
        result[winner_team_id] = STAGE_POINTS_CHAMPION
    return result


def _recent_cl_tournaments(conn, continent: str, cwc_year: int, n: int = CWC_SEASONS):
    """클럽월드컵이 열리는 해 직전 n개 시즌의 그 대륙 챔스 대회 row들
    (year 내림차순 = 최근 시즌이 먼저). 게임 초반이라 n개가 다 없으면
    있는 만큼만 반환 — 별도 예외처리 불필요(호출부가 len()으로 알아서 대응)."""
    years = [cwc_year - i for i in range(1, n + 1)]
    ph = ",".join("?" * len(years))
    rows = conn.execute(
        f"""SELECT id, year, continent, winner_team_id FROM cl_tournaments
            WHERE continent=? AND year IN ({ph})
            ORDER BY year DESC""",
        (continent, *years)).fetchall()
    return [dict(r) for r in rows]


def _select_continent_field(conn, continent: str, cwc_year: int) -> list[dict]:
    """대륙 하나의 클럽월드컵 출전팀 리스트를 반환.
    각 원소: {"team_id", "team_name", "country", "auto": bool, "pts": int}"""
    quota = CWC_QUOTA.get(continent, 0)
    tournaments = _recent_cl_tournaments(conn, continent, cwc_year)
    if not tournaments:
        return []   # 아직 챔스가 한 번도 안 열렸으면(게임 극초반) 이 대륙은 스킵

    # ── 1) 자동 진출: 시즌별 우승팀, 중복 제거(먼저 나온 = 더 최근 시즌 우선) ──
    auto: list[dict] = []
    auto_ids: set = set()
    for t in tournaments:
        wid = t["winner_team_id"]
        if not wid or wid in auto_ids:
            continue
        entry = conn.execute(
            "SELECT team_name, country FROM cl_entries WHERE tournament_id=? AND team_id=?",
            (t["id"], wid)).fetchone()
        if not entry:
            continue
        auto_ids.add(wid)
        auto.append({"team_id": wid, "team_name": entry["team_name"],
                      "country": entry["country"], "auto": True, "pts": STAGE_POINTS_CHAMPION})
        if len(auto) >= quota:
            break   # 이론상 4시즌<쿼터라 거의 안 걸리지만 방어적으로 캡

    # ── 2) 랭킹 풀 집계: 자동진출 제외 전 참가팀의 4시즌 누적 점수 ──
    #     동점 타이브레이크용으로 "가장 최근 시즌 점수"도 같이 기록.
    pool: dict = {}   # team_id -> {"team_name","country","pts","latest_pts"}
    for i, t in enumerate(tournaments):
        entries = conn.execute(
            "SELECT team_id, team_name, country FROM cl_entries WHERE tournament_id=?",
            (t["id"],)).fetchall()
        stage_pts = _batch_team_stage_points(conn, t["id"], t["winner_team_id"])
        for e in entries:
            tid = e["team_id"]
            if tid in auto_ids:
                continue
            pts = stage_pts.get(tid, 0)
            rec = pool.setdefault(tid, {"team_name": e["team_name"], "country": e["country"],
                                         "pts": 0, "latest_pts": 0})
            rec["pts"] += pts
            if i == 0:   # tournaments[0] = 가장 최근 시즌
                rec["latest_pts"] = pts

    # 리그 스테이지 승점/득실도 최근 시즌 기준으로 타이브레이크 보강.
    if tournaments:
        from champions_engine import get_cl_league_standings
        latest_standings = {r["team_id"]: r for r in get_cl_league_standings(tournaments[0]["id"])}
    else:
        latest_standings = {}

    def _sort_key(item):
        tid, rec = item
        ls = latest_standings.get(tid, {})
        return (-rec["pts"], -rec["latest_pts"],
                -ls.get("pts", 0), -(ls.get("gf", 0) - ls.get("ga", 0)), -ls.get("gf", 0))

    ranking = sorted(pool.items(), key=_sort_key)

    # ── 3) 국가당 2팀 제한 적용하며 남은 자리 채우기 ──
    country_count: dict = {}
    for a in auto:
        country_count[a["country"]] = country_count.get(a["country"], 0) + 1

    remaining = max(0, quota - len(auto))
    selected: list[dict] = []
    for cap in (CWC_COUNTRY_CAP, CWC_COUNTRY_CAP + 1):
        # [2026-07 신설, 신민용 확정] 최후의 수단 폴백 — 국가당 2팀 제한을
        # 지키면서는 쿼터를 못 채우는 극단적 상황(참가국 다양성이 부족한
        # 경우)에만, 캡을 2→3으로 한 단계 완화해서 마저 채운다. 정상적인
        # 상황(대륙마다 20개국 이상 참가)에서는 1차 시도(cap=2)에서 항상
        # remaining이 다 채워지므로 이 루프는 사실상 한 번만 돈다.
        if len(selected) >= remaining:
            break
        for tid, rec in ranking:
            if len(selected) >= remaining:
                break
            cn = rec["country"]
            if any(s["team_id"] == tid for s in selected):
                continue   # 1차 시도에서 이미 뽑힌 팀 중복 방지
            if country_count.get(cn, 0) >= cap:
                continue   # 이번 캡 기준으로도 제한 걸림 — 다음 순위로
            selected.append({"team_id": tid, "team_name": rec["team_name"], "country": cn,
                              "auto": False, "pts": rec["pts"]})
            country_count[cn] = country_count.get(cn, 0) + 1

    return auto + selected


def get_club_world_cup_field(cwc_year: int) -> dict:
    """클럽월드컵 출전 32팀 전체를 대륙별로 묶어 반환.
    반환: {"유럽": [...], "북남미": [...], "아시아": [...], "아프리카": [...]}
    각 대륙 리스트 원소는 _select_continent_field 참고.

    [주의] 대륙별 쿼터(12/10/6/4)를 다 못 채우는 경우(게임 극초반이라
    챔스 역사가 짧음)는 그 대륙 자리가 비어있는 채로 반환된다 — 호출부
    (실제 대진 추첨)에서 부전승 처리하거나, 다음 등급 국가로 채우는 등
    별도 보강 로직을 붙일 수 있다(이 함수의 책임 범위 밖)."""
    conn = get_conn()
    try:
        return {cont: _select_continent_field(conn, cont, cwc_year)
                for cont in CWC_QUOTA}
    finally:
        conn.close()