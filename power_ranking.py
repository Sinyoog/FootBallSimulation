# -*- coding: utf-8 -*-
"""
파워랭킹 시스템 (팀 파워랭킹 / 국가 파워랭킹).

[설계 v1, 2026-08 신설 — "세계 축구 기록실 > 파워랭킹" 기반 구축]
신민용과의 설계 논의를 그대로 코드로 옮긴 것. 핵심 아이디어:

    레이팅(끊임없이 움직이는 기초 체력)
        ↓
    파워랭킹(연도 경계에서 스냅샷을 찍어 발표하는 결과물)

두 층을 분리한 이유: "20위가 1위를 한 번 이겼다고 바로 1위가 되면 안 된다"는
요구사항 때문. 레이팅은 매 경기 Elo식으로 조금씩만 움직이고, 파워랭킹은 그
레이팅에 그 해의 우승 보너스를 얹은 뒤, 작년 스냅샷과 섞어(안정화) 급변을
한 번 더 눌러준다.

── 연도 표기 규칙 (신민용 확정) ──────────────────────────────
모든 대회가 1년 안에 예선~본선을 다 끝내는 이 게임의 특성상:
    evaluation_year = 실제로 경기를 치른 시즌 연도 (예: 2001)
    ranking_year    = 그 성적이 반영되어 "발표"되는 연도 (예: 2002, = eval+1)
"2002년 파워랭킹"은 "2002년에 열린 대회 성적"이 아니라 "2001 시즌 성적을
반영해 2002년 1월에 발표된 랭킹"이라는 뜻. run_year_end_power_ranking_update()
를 매년 연도 전환 시점(_end_of_season 근처)에 evaluation_year=(방금 끝난 시즌)
으로 호출하면 ranking_year는 자동으로 +1 되어 계산·저장된다.

── 지금 이 파일이 하는 것 (1단계 기반) ──────────────────────
- 팀: 국내리그(시즌 최종 순위 집계) + 컵대회/챔피언스/유로파/컨퍼런스/
  슈퍼컵/클럽월드컵(실제 경기 결과, cl_matches류 테이블)을 모아 Elo 갱신.
- 국가: 월드컵/대륙컵(대륙별 4개)/유로/지역컵(코파 아메리카 포함 14개
  지역, constants.REGION_CUP_NAME 그대로 재사용) 실제 경기 결과를 모아
  Elo 갱신.
- 연도 스냅샷 저장 + "이전 순위 조회"(순위 클릭 시 연도별 이력 보여주는
  UI)용 조회 함수.

── 아직 확정 안 한 것 (신민용 설계 노트 원문 그대로, 시뮬레이션 돌리며
   튜닝 예정 — 아래 상수들에 "TUNE LATER" 표시해둠) ──────────
- 대회별/지역컵별 정확한 가중치
- 최근 1년/3년/5년 비율(지금은 안정화 계수 하나로 단순화해둠)
- 우승/준우승/4강 등 스테이지별 정확한 점수 차이(지금은 "우승 보너스"만
  구현 — 준우승 이하 세분화는 다음 단계)
- 랭킹 변동 폭 상한

이 파일은 game_engine._end_of_season() 근처에서
    import power_ranking
    power_ranking.run_year_end_power_ranking_update(get_conn(), evaluation_year=new_year-1)
형태로 호출하는 걸 전제로 설계했다(아직 실제 훅 연결은 안 함 — 신민용이
원할 때 한 줄 추가하면 됨).
"""

from dataclasses import dataclass, field
from typing import Optional

from database import get_conn
from constants import (
    REGION_CUP_NAME, REGION_TO_CONTINENT, CONFEDERATIONS,
    CONTINENT_TO_CONF, CONF_CUP_NAME, EURO_NAME, GAME_START_YEAR,
)


# ══════════════════════════════════════════════════════════════
# 1. 대회 카테고리 / 가중치 상수 (TUNE LATER — 지금은 합리적인 추정치)
# ══════════════════════════════════════════════════════════════

# [팀 파워랭킹] 대회 카테고리별 가중치. 리그와 국제대회를 1승=1승으로
# 취급하면 안 된다는 설계 원칙을 여기 숫자로 반영한다.
TEAM_COMPETITION_WEIGHT = {
    "league":         1.0,   # 자국 리그 (경기 수가 압도적으로 많아 기준점 역할)
    "domestic_cup":   0.6,
    "champions":      1.6,
    "europa":         1.1,
    "conference":     0.8,
    "super_cup":      0.5,
    "club_world_cup": 1.8,
}

# 대회 우승 시 레이팅에 얹는 보너스(대회 가중치에 곱해짐). 준우승/4강 등
# 단계별 세분화는 2단계 작업 — 지금은 "우승했는가"만 본다.
TEAM_CHAMPION_BONUS = 40.0

# [국가 파워랭킹] Tier 가중치.
COUNTRY_TIER_WEIGHT = {
    "world_cup":   3.0,
    "continental": 1.8,   # 아시안컵 / 아프리카 네이션스컵 / 남북미 대륙컵 / 유럽 네이션스컵
    "euro":        1.9,   # 유로(EURO) — 유럽만 별도로 한 번 더 치르는 네이션스컵급 대회
    "region":      1.0,   # 지역컵 기본값 (REGIONAL_CUP_STRENGTH로 지역마다 보정)
}
COUNTRY_CHAMPION_BONUS = 50.0

# 지역컵마다 다른 위상. constants.REGION_CUP_NAME의 키(지역명)를 그대로 쓴다.
# 코파 아메리카(REGION_CUP_NAME["남미"])는 신민용이 명시적으로 "월드컵 다음
# 급"이라 했으므로 지역컵 중 유일하게 대륙컵급에 가깝게 높였다.
# 나머지는 일단 균등(1.0)에 가깝게 두고, "OO컵이 랭킹에 너무 큰/적은 영향을
# 준다" 싶으면 이 숫자만 개별 조정하면 된다 — 신민용이 설계 논의에서 말한
# "regional_competition_strength" 값이 바로 이것.
REGIONAL_CUP_STRENGTH = {
    "남미":         1.7,   # 코파 아메리카 — 지역컵이지만 사실상 대륙컵급 위상
    "동아시아":     1.1,
    "서아시아":     1.0,
    "동남아시아":   0.9,
    "남아시아":     0.7,
    "중앙아시아":   0.7,
    "북아프리카":   0.9,
    "서아프리카":   1.1,
    "동아프리카":   0.8,
    "남부아프리카": 0.9,
    "중앙아프리카": 0.7,
    "중앙아메리카": 1.0,
    "카리브":       0.7,
    "오세아니아":   0.8,
}


def regional_cup_strength(region_name: str) -> float:
    """REGIONAL_CUP_STRENGTH에 없는 지역이 나중에 추가돼도 죽지 않도록
    기본값(1.0)을 깔아준다."""
    return REGIONAL_CUP_STRENGTH.get(region_name, 1.0)


# ══════════════════════════════════════════════════════════════
# 2. Elo 엔진 (순수 함수 — DB 의존 없음)
# ══════════════════════════════════════════════════════════════

ELO_K_BASE = 24.0   # TUNE LATER: 경기당 최대 변동폭의 기준값

# 팀/국가 레이팅이 없을 때(첫 등장) 깔아줄 기본값. 실제 강함과 무관하게
# "리그 전체 평균 어딘가"에서 시작하고, 이후 경기 결과가 쌓이며 스스로
# 제자리를 찾아가게 두는 게 Elo의 기본 철학이다.
DEFAULT_TEAM_RATING = 1500.0
DEFAULT_COUNTRY_RATING = 1500.0

# 연도 스냅샷 안정화 계수: 이번 시즌 계산 결과에 몇 %를 반영하고, 작년
# 스냅샷을 몇 %나 그대로 끌고 올지. "3년 동안 꾸준한 팀"과 "한 번 이변을
# 낸 팀"을 구분하려는 장치 — 낮출수록(=작년 비중↑) 랭킹이 안정적으로 변함.
SNAPSHOT_NEW_WEIGHT = 0.65   # TUNE LATER


def expected_score(rating_a: float, rating_b: float) -> float:
    """A가 B를 상대로 이길 '기대 승률'(0~1). 표준 Elo 공식."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def elo_delta(rating_a: float, rating_b: float, actual_score: float,
              k: float = ELO_K_BASE) -> float:
    """actual_score: 승=1.0, 무=0.5, 패=0.0 (승부차기도 무승부 90분 뒤
    패자 쪽에 0.0, 승자 쪽에 1.0으로 취급 — PSO는 '한 골 차 신승'에
    가깝다고 보는 것이 Elo 세계에서는 일반적)."""
    return k * (actual_score - expected_score(rating_a, rating_b))


def _match_scores(home_score: int, away_score: int, pso_winner=None,
                   home_id=None):
    """(home_score, away_score, pso_winner)로부터 (home측 actual_score,
    away측 actual_score)를 돌려준다. 무승부인데 pso_winner가 있으면
    승부차기로 갈린 것으로 보고 승/패로 취급."""
    if home_score is None or away_score is None or home_score < 0 or away_score < 0:
        return None  # 아직 안 치러진 경기
    if home_score > away_score:
        return (1.0, 0.0)
    if home_score < away_score:
        return (0.0, 1.0)
    # 90분 무승부
    if pso_winner:
        if home_id is not None and pso_winner == home_id:
            return (1.0, 0.0)
        return (0.0, 1.0)
    return (0.5, 0.5)


# ══════════════════════════════════════════════════════════════
# 3. 데이터 구조
# ══════════════════════════════════════════════════════════════

@dataclass
class TeamPowerEntry:
    team_id: int
    team_name: str
    continent: str
    country: str
    rating: float
    rank: int = 0
    prev_rank: Optional[int] = None
    ranking_year: int = 0
    evaluation_year: int = 0
    tier: Optional[int] = None  # 현재 소속 리그 등급(1부/2부/...) — 화면 표시용, live join


@dataclass
class CountryPowerEntry:
    country: str
    continent: str
    rating: float
    rank: int = 0
    prev_rank: Optional[int] = None
    ranking_year: int = 0
    evaluation_year: int = 0


# ══════════════════════════════════════════════════════════════
# 4. DB 스키마
# ══════════════════════════════════════════════════════════════
# [주의] 지금은 이 모듈이 처음 쓰일 때 스스로 테이블을 만든다(멱등,
# CREATE TABLE IF NOT EXISTS). 나중에 정식으로 database.py의 init 흐름에
# 편입시키고 싶으면 ensure_power_ranking_tables 호출 한 줄을 거기로
# 옮기기만 하면 된다 — 스키마 정의는 이 파일 안에 그대로 둬도 무방.

def ensure_power_ranking_tables(conn):
    c = conn.cursor()
    # 레이어 1 — 계속 움직이는 기초 레이팅(연도 구분 없이 딱 1행/팀).
    c.execute("""CREATE TABLE IF NOT EXISTS team_power_rating(
        team_id INTEGER PRIMARY KEY,
        rating REAL DEFAULT 1500,
        last_updated_year INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS country_power_rating(
        country TEXT PRIMARY KEY,
        rating REAL DEFAULT 1500,
        last_updated_year INTEGER DEFAULT 0)""")
    # 레이어 2 — 연도별 스냅샷(발표된 파워랭킹). "이전 순위 조회" UI가
    # 여기서 team_id/country로 연도별 이력을 그대로 긁어간다.
    c.execute("""CREATE TABLE IF NOT EXISTS team_power_rankings(
        ranking_year INTEGER, evaluation_year INTEGER,
        team_id INTEGER, team_name TEXT, continent TEXT, country TEXT,
        rating REAL, rank INTEGER, prev_rank INTEGER,
        PRIMARY KEY(ranking_year, team_id))""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_tpr_year_continent
        ON team_power_rankings(ranking_year, continent, rank)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_tpr_team
        ON team_power_rankings(team_id, ranking_year)""")
    c.execute("""CREATE TABLE IF NOT EXISTS country_power_rankings(
        ranking_year INTEGER, evaluation_year INTEGER,
        country TEXT, continent TEXT,
        rating REAL, rank INTEGER, prev_rank INTEGER,
        PRIMARY KEY(ranking_year, country))""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_cpr_year
        ON country_power_rankings(ranking_year, rank)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_cpr_country
        ON country_power_rankings(country, ranking_year)""")
    conn.commit()


# ══════════════════════════════════════════════════════════════
# 5. 레이팅 읽기/쓰기 헬퍼
# ══════════════════════════════════════════════════════════════

def _get_team_rating(conn, team_id: int) -> float:
    row = conn.execute(
        "SELECT rating FROM team_power_rating WHERE team_id=?", (team_id,)
    ).fetchone()
    if row is None:
        return DEFAULT_TEAM_RATING
    return row[0]


def _set_team_rating(conn, team_id: int, rating: float, year: int):
    conn.execute("""INSERT INTO team_power_rating(team_id, rating, last_updated_year)
                     VALUES(?,?,?)
                     ON CONFLICT(team_id) DO UPDATE SET
                        rating=excluded.rating,
                        last_updated_year=excluded.last_updated_year""",
                 (team_id, rating, year))


def _get_country_rating(conn, country: str) -> float:
    row = conn.execute(
        "SELECT rating FROM country_power_rating WHERE country=?", (country,)
    ).fetchone()
    if row is None:
        return _seed_country_rating(conn, country)
    return row[0]


def _fifa_rank_to_seed_rating(fifa_rank: Optional[int]) -> float:
    """countries.py의 다섯 번째 값(예: ("아르헨티나","🇦🇷","남미","스페인어",1)의
    1)을 초기 레이팅으로 환산하는 순수 함수. _seed_country_rating(DB에 쓰는
    버전)과 get_country_power_ranking_seed(DB에 안 쓰고 조회만 하는 버전)가
    같은 계산식을 공유하도록 분리해뒀다.
    TUNE LATER: 211개국 스프레드를 1200~2000 사이로 펼치는 계수는 감으로
    잡은 값 — 실측 후 조정 필요."""
    fifa_rank = fifa_rank if fifa_rank else 100
    rating = 2000.0 - (fifa_rank - 1) * 3.5
    return max(1200.0, min(2000.0, rating))


def _seed_country_rating(conn, country: str) -> float:
    """이 나라가 처음 레이팅을 받는 경우, data/countries.py의 fifa_rank를
    기반으로 초기값을 깔아준다(신민용 설계: "국가 초기 순위는 countries.py를
    seed로 사용"). fifa_rank가 낮을수록(=강할수록) 레이팅이 높다."""
    row = conn.execute(
        "SELECT fifa_rank FROM countries WHERE name=?", (country,)
    ).fetchone()
    fifa_rank = row[0] if row and row[0] else 100
    rating = _fifa_rank_to_seed_rating(fifa_rank)
    conn.execute("""INSERT INTO country_power_rating(country, rating, last_updated_year)
                     VALUES(?,?,0)
                     ON CONFLICT(country) DO NOTHING""", (country, rating))
    return rating


def _set_country_rating(conn, country: str, rating: float, year: int):
    conn.execute("""INSERT INTO country_power_rating(country, rating, last_updated_year)
                     VALUES(?,?,?)
                     ON CONFLICT(country) DO UPDATE SET
                        rating=excluded.rating,
                        last_updated_year=excluded.last_updated_year""",
                 (country, rating, year))


# ══════════════════════════════════════════════════════════════
# 6. 팀 레이어 1 — 이번 시즌 경기 결과로 레이팅 갱신
# ══════════════════════════════════════════════════════════════

# cl/el/ecl/cwc/sc는 스키마가 전부 동일(tournament_id, stage, home_team_id,
# away_team_id, home_score, away_score, pso_winner) — 테이블명만 갈아끼워
# 하나의 함수로 처리한다. cup_engine(국내컵)만 round_name/round_idx를 쓰지만
# 필요한 컬럼(팀/스코어/승부차기)은 동일해서 그대로 재사용 가능.
_CLUB_COMP_TABLES = {
    "champions":      ("cl_tournaments", "cl_matches"),
    "europa":         ("el_tournaments", "el_matches"),
    "conference":     ("ecl_tournaments", "ecl_matches"),
    "club_world_cup": ("cwc_tournaments", "cwc_matches"),
    "super_cup":      ("sc_tournaments", "sc_matches"),
    "domestic_cup":   ("cup_tournaments", "cup_matches"),
}


def _update_team_elo_from_matches(conn, matches_table: str, tournament_id: int,
                                   year: int, weight: float):
    """matches_table 하나(한 대회)의 실제 경기 전부를 순서대로 훑으며
    Elo를 갱신한다. 실제 맞대결 상대 레이팅을 그대로 쓰므로 리그 집계
    방식(6-b)보다 정확도가 높다 — 컵/대륙대항전은 경기 수가 적어(많아야
    수십 경기) 이 방식이 성능 부담 없이 가능하다."""
    rows = conn.execute(
        f"""SELECT home_team_id, away_team_id, home_score, away_score, pso_winner
            FROM {matches_table} WHERE tournament_id=?
            ORDER BY id ASC""", (tournament_id,)).fetchall()
    for r in rows:
        home_id, away_id, hs, as_, pso = r[0], r[1], r[2], r[3], r[4]
        if not home_id or not away_id:
            continue
        outcome = _match_scores(hs, as_, pso, home_id)
        if outcome is None:
            continue
        home_actual, away_actual = outcome
        rh = _get_team_rating(conn, home_id)
        ra = _get_team_rating(conn, away_id)
        dh = elo_delta(rh, ra, home_actual, k=ELO_K_BASE * weight)
        da = elo_delta(ra, rh, away_actual, k=ELO_K_BASE * weight)
        _set_team_rating(conn, home_id, rh + dh, year)
        _set_team_rating(conn, away_id, ra + da, year)


def _update_team_elo_from_league(conn, evaluation_year: int):
    """리그는 한 시즌에 수백~수천 경기가 나올 수 있어 경기 단위로 도는 대신
    league_season_standings(팀별 승/무/패 집계)를 '가상 라운드로빈'처럼
    풀어서 Elo를 갱신한다 — 실제 상대는 리그 평균 레이팅으로 근사한다.
    TUNE LATER: 원한다면 나중에 match_results_archive를 직접 순회하는
    정밀 버전으로 교체 가능(다만 리그 수가 많아 연산량이 커짐에 유의)."""
    rows = conn.execute(
        """SELECT team_id, wins, draws, losses
           FROM league_season_standings WHERE year=?""", (evaluation_year,)
    ).fetchall()
    if not rows:
        return
    league_avg = sum(_get_team_rating(conn, r[0]) for r in rows) / len(rows)
    w = TEAM_COMPETITION_WEIGHT["league"]
    for team_id, wins, draws, losses in rows:
        rating = _get_team_rating(conn, team_id)
        # 승/무/패를 각각 "리그 평균 상대와 한 경기씩 치른 것"으로 근사.
        n_games = (wins or 0) + (draws or 0) + (losses or 0)
        if n_games == 0:
            continue
        actual_points = (wins or 0) * 1.0 + (draws or 0) * 0.5
        expected_points = expected_score(rating, league_avg) * n_games
        delta = (ELO_K_BASE * w) * (actual_points - expected_points) / max(1, n_games) \
                * min(n_games, 10) / 10  # 경기 수가 적어도 한 번에 과도하게 안 튀도록 완충
        _set_team_rating(conn, team_id, rating + delta, evaluation_year)


def _team_champion_bonus_events(conn, evaluation_year: int):
    """이번 시즌 각 대회 winner_team_id를 모아 (team_id, category) 목록으로.
    trophy_log이 아니라 각 *_tournaments.winner_team_id를 직접 읽는다 —
    trophy_log은 team_name(문자열)만 있어 동명 팀 충돌 위험이 있는 반면,
    winner_team_id는 teams.id를 그대로 참조해 더 안전하다."""
    events = []
    for category, (tournaments_table, _matches_table) in _CLUB_COMP_TABLES.items():
        rows = conn.execute(
            f"""SELECT id, winner_team_id FROM {tournaments_table}
                WHERE year=? AND winner_team_id IS NOT NULL AND winner_team_id != 0""",
            (evaluation_year,)).fetchall()
        for tid, winner_team_id in rows:
            events.append((winner_team_id, category, tid))
    return events


def update_team_ratings_for_year(conn, evaluation_year: int):
    """레이어 1 갱신 진입점: evaluation_year 한 해 동안 열린 모든 팀 대회의
    실제 결과를 반영해 team_power_rating을 움직인다."""
    ensure_power_ranking_tables(conn)

    # 1) 리그 (집계 기반)
    _update_team_elo_from_league(conn, evaluation_year)

    # 2) 컵/챔피언스/유로파/컨퍼런스/슈퍼컵/클럽월드컵 (경기 기반)
    for category, (tournaments_table, matches_table) in _CLUB_COMP_TABLES.items():
        weight = TEAM_COMPETITION_WEIGHT[category]
        tids = conn.execute(
            f"SELECT id FROM {tournaments_table} WHERE year=?", (evaluation_year,)
        ).fetchall()
        for (tid,) in tids:
            _update_team_elo_from_matches(conn, matches_table, tid, evaluation_year, weight)

    # 3) 우승 보너스 (준우승 이하 세분화는 다음 단계 — 설계 노트 참고)
    for team_id, category, _tid in _team_champion_bonus_events(conn, evaluation_year):
        rating = _get_team_rating(conn, team_id)
        bonus = TEAM_CHAMPION_BONUS * TEAM_COMPETITION_WEIGHT[category]
        _set_team_rating(conn, team_id, rating + bonus, evaluation_year)

    conn.commit()


# ══════════════════════════════════════════════════════════════
# 7. 국가 레이어 1 — 이번 시즌 A매치 결과로 레이팅 갱신
# ══════════════════════════════════════════════════════════════

def _intl_tournament_category(kind: str, name: str) -> Optional[str]:
    """intl_tournaments.kind(+name)를 국가 파워랭킹용 카테고리로 매핑.
    예선(wc_qual/cont_qual/euro_qual)은 아직 반영하지 않는다(TUNE LATER —
    예선 탈락도 성적에 넣고 싶으면 여기서 별도 약한 가중치로 추가 가능)."""
    if kind == "world":
        return "world_cup"
    if kind == "region":
        return "region"
    if kind == "continent":
        # euro는 kind='continent'를 대륙컵과 공유하고 name으로만 구분된다
        # (constants.py의 world_browser._effective_kind와 동일한 판별 방식).
        if name and EURO_NAME in name:
            return "euro"
        return "continental"
    return None  # wc_qual / cont_qual / euro_qual 등 예선류는 지금은 skip


def _country_region_of(tournament_name: str) -> Optional[str]:
    """지역컵 대회명(REGION_CUP_NAME의 값, 예: '코파 아메리카')으로부터
    REGIONAL_CUP_STRENGTH 조회용 지역명(키, 예: '남미')을 역으로 찾는다."""
    for region, cup_name in REGION_CUP_NAME.items():
        if cup_name == tournament_name:
            return region
    return None


def _update_country_elo_from_matches(conn, tournament_id: int, year: int, weight: float):
    rows = conn.execute(
        """SELECT home, away, home_score, away_score, pso_winner
           FROM intl_matches WHERE tournament_id=? ORDER BY id ASC""",
        (tournament_id,)).fetchall()
    for home, away, hs, as_, pso in rows:
        if not home or not away:
            continue
        outcome = _match_scores(hs, as_, pso, home)  # pso_winner는 국가명(TEXT) 그대로 비교
        if outcome is None:
            continue
        home_actual, away_actual = outcome
        rh = _get_country_rating(conn, home)
        ra = _get_country_rating(conn, away)
        dh = elo_delta(rh, ra, home_actual, k=ELO_K_BASE * weight)
        da = elo_delta(ra, rh, away_actual, k=ELO_K_BASE * weight)
        _set_country_rating(conn, home, rh + dh, year)
        _set_country_rating(conn, away, ra + da, year)


def _country_champion_bonus_events(conn, evaluation_year: int):
    rows = conn.execute(
        """SELECT kind, name, winner FROM intl_tournaments
           WHERE year=? AND winner IS NOT NULL AND winner != ''""",
        (evaluation_year,)).fetchall()
    events = []
    for kind, name, winner in rows:
        category = _intl_tournament_category(kind, name)
        if category is None:
            continue
        if category == "region":
            region = _country_region_of(name)
            strength = regional_cup_strength(region) if region else 1.0
        else:
            strength = 1.0
        events.append((winner, category, strength))
    return events


def update_country_ratings_for_year(conn, evaluation_year: int):
    ensure_power_ranking_tables(conn)

    rows = conn.execute(
        "SELECT id, kind, name FROM intl_tournaments WHERE year=?", (evaluation_year,)
    ).fetchall()
    for tid, kind, name in rows:
        category = _intl_tournament_category(kind, name)
        if category is None:
            continue
        if category == "region":
            region = _country_region_of(name)
            weight = COUNTRY_TIER_WEIGHT["region"] * regional_cup_strength(region if region else "")
        else:
            weight = COUNTRY_TIER_WEIGHT[category]
        _update_country_elo_from_matches(conn, tid, evaluation_year, weight)

    for winner, category, strength in _country_champion_bonus_events(conn, evaluation_year):
        rating = _get_country_rating(conn, winner)
        if category == "region":
            bonus = COUNTRY_CHAMPION_BONUS * COUNTRY_TIER_WEIGHT["region"] * strength
        else:
            bonus = COUNTRY_CHAMPION_BONUS * COUNTRY_TIER_WEIGHT[category]
        _set_country_rating(conn, winner, rating + bonus, evaluation_year)

    conn.commit()


# ══════════════════════════════════════════════════════════════
# 8. 레이어 2 — 연도 스냅샷 계산 + 저장 (+ 안정화)
# ══════════════════════════════════════════════════════════════

def _prev_snapshot_rating_team(conn, team_id: int, ranking_year: int) -> Optional[float]:
    row = conn.execute(
        """SELECT rating FROM team_power_rankings
           WHERE team_id=? AND ranking_year=?""", (team_id, ranking_year - 1)
    ).fetchone()
    return row[0] if row else None


def _prev_snapshot_rating_country(conn, country: str, ranking_year: int) -> Optional[float]:
    row = conn.execute(
        """SELECT rating FROM country_power_rankings
           WHERE country=? AND ranking_year=?""", (country, ranking_year - 1)
    ).fetchone()
    return row[0] if row else None


def _prev_rank_team(conn, team_id: int, ranking_year: int) -> Optional[int]:
    row = conn.execute(
        """SELECT rank FROM team_power_rankings
           WHERE team_id=? AND ranking_year=?""", (team_id, ranking_year - 1)
    ).fetchone()
    return row[0] if row else None


def _prev_rank_country(conn, country: str, ranking_year: int) -> Optional[int]:
    row = conn.execute(
        """SELECT rank FROM country_power_rankings
           WHERE country=? AND ranking_year=?""", (country, ranking_year - 1)
    ).fetchone()
    return row[0] if row else None


def _stabilize(raw: float, prev: Optional[float]) -> float:
    """작년 스냅샷이 있으면 SNAPSHOT_NEW_WEIGHT만큼만 새 값을 반영해
    급변을 누른다(신민용 요구사항: "강팀을 한 번 잡은 약팀"이 바로
    최상위로 튀지 않게). 첫 등장이면 그대로 raw를 쓴다."""
    if prev is None:
        return raw
    return SNAPSHOT_NEW_WEIGHT * raw + (1 - SNAPSHOT_NEW_WEIGHT) * prev


def compute_team_power_rankings(conn, evaluation_year: int) -> list:
    """레이어 1 갱신이 끝난 뒤 호출. team_power_rankings에
    ranking_year = evaluation_year + 1 로 스냅샷을 저장하고, 저장된
    TeamPowerEntry 리스트(전체, 대륙 무관 정렬)를 돌려준다."""
    ranking_year = evaluation_year + 1
    ensure_power_ranking_tables(conn)

    teams = conn.execute(
        """SELECT t.id, t.name, c.continent, c.name
           FROM teams t
           JOIN countries c ON t.country_id = c.id""").fetchall()

    entries = []
    for team_id, team_name, continent, country in teams:
        raw = _get_team_rating(conn, team_id)
        prev_rating = _prev_snapshot_rating_team(conn, team_id, ranking_year)
        final_rating = _stabilize(raw, prev_rating)
        entries.append(TeamPowerEntry(
            team_id=team_id, team_name=team_name, continent=continent,
            country=country, rating=final_rating,
            ranking_year=ranking_year, evaluation_year=evaluation_year))

    entries.sort(key=lambda e: e.rating, reverse=True)
    for i, e in enumerate(entries, start=1):
        e.rank = i
        e.prev_rank = _prev_rank_team(conn, e.team_id, ranking_year)

    for e in entries:
        conn.execute("""INSERT INTO team_power_rankings
            (ranking_year, evaluation_year, team_id, team_name, continent, country,
             rating, rank, prev_rank)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ranking_year, team_id) DO UPDATE SET
                evaluation_year=excluded.evaluation_year,
                team_name=excluded.team_name, continent=excluded.continent,
                country=excluded.country, rating=excluded.rating,
                rank=excluded.rank, prev_rank=excluded.prev_rank""",
            (e.ranking_year, e.evaluation_year, e.team_id, e.team_name,
             e.continent, e.country, e.rating, e.rank, e.prev_rank))
    conn.commit()
    return entries


def compute_country_power_rankings(conn, evaluation_year: int) -> list:
    ranking_year = evaluation_year + 1
    ensure_power_ranking_tables(conn)

    countries = conn.execute("SELECT name, continent FROM countries").fetchall()

    entries = []
    for country, continent in countries:
        raw = _get_country_rating(conn, country)
        prev_rating = _prev_snapshot_rating_country(conn, country, ranking_year)
        final_rating = _stabilize(raw, prev_rating)
        entries.append(CountryPowerEntry(
            country=country, continent=continent, rating=final_rating,
            ranking_year=ranking_year, evaluation_year=evaluation_year))

    entries.sort(key=lambda e: e.rating, reverse=True)
    for i, e in enumerate(entries, start=1):
        e.rank = i
        e.prev_rank = _prev_rank_country(conn, e.country, ranking_year)

    for e in entries:
        conn.execute("""INSERT INTO country_power_rankings
            (ranking_year, evaluation_year, country, continent, rating, rank, prev_rank)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(ranking_year, country) DO UPDATE SET
                evaluation_year=excluded.evaluation_year,
                continent=excluded.continent, rating=excluded.rating,
                rank=excluded.rank, prev_rank=excluded.prev_rank""",
            (e.ranking_year, e.evaluation_year, e.country, e.continent,
             e.rating, e.rank, e.prev_rank))
    conn.commit()
    return entries


# ══════════════════════════════════════════════════════════════
# 9. 오케스트레이터 — 연도 전환 시 호출할 단일 진입점
# ══════════════════════════════════════════════════════════════

def run_year_end_power_ranking_update(conn, evaluation_year: int):
    """game_engine._advance_week()의 연도 전환 분기(_end_of_season 호출
    근처)에서 이 한 줄만 추가하면 됨:

        power_ranking.run_year_end_power_ranking_update(get_conn(), new_year - 1)

    evaluation_year는 "방금 끝난 시즌"의 연도(= new_year - 1)를 넘긴다.
    내부적으로 레이어1(레이팅) 갱신 → 레이어2(스냅샷) 계산·저장 순서로
    진행하고, 최종 ranking_year(=evaluation_year+1) 값을 돌려준다."""
    update_team_ratings_for_year(conn, evaluation_year)
    update_country_ratings_for_year(conn, evaluation_year)
    compute_team_power_rankings(conn, evaluation_year)
    compute_country_power_rankings(conn, evaluation_year)
    return evaluation_year + 1


# ══════════════════════════════════════════════════════════════
# 10. UI 조회 헬퍼
# ══════════════════════════════════════════════════════════════

def get_team_power_ranking(conn, ranking_year: int, continent: Optional[str] = None,
                            limit: int = 100) -> list:
    """파워랭킹 화면의 '전체/아시아/유럽/...' 탭용. continent=None이면 전체.
    오세아니아를 아시아에 합쳐 보여주고 싶으면 호출부에서
    continent='아시아' 조회 후 데이터에 continent='오세아니아'인 것도 별도
    쿼리해 합치거나, 이 함수에 continents 리스트를 받는 변형을 추가하면 됨
    (지금은 신민용이 말한 '내부 데이터엔 오세아니아 별도 보관' 원칙을
    지키기 위해 단일 대륙만 받는 형태로 둔다)."""
    ensure_power_ranking_tables(conn)
    ensure_initial_team_power_ranking(conn)
    if continent:
        rows = conn.execute(
            """SELECT p.ranking_year, p.evaluation_year, p.team_id, p.team_name, p.continent,
                      p.country, p.rating, p.rank, p.prev_rank, t.current_tier
               FROM team_power_rankings p LEFT JOIN teams t ON t.id = p.team_id
               WHERE p.ranking_year=? AND p.continent=?
               ORDER BY p.rank ASC LIMIT ?""", (ranking_year, continent, limit)).fetchall()
    else:
        rows = conn.execute(
            """SELECT p.ranking_year, p.evaluation_year, p.team_id, p.team_name, p.continent,
                      p.country, p.rating, p.rank, p.prev_rank, t.current_tier
               FROM team_power_rankings p LEFT JOIN teams t ON t.id = p.team_id
               WHERE p.ranking_year=?
               ORDER BY p.rank ASC LIMIT ?""", (ranking_year, limit)).fetchall()
    return [TeamPowerEntry(team_id=r[2], team_name=r[3], continent=r[4], country=r[5],
                            rating=r[6], rank=r[7], prev_rank=r[8],
                            ranking_year=r[0], evaluation_year=r[1], tier=r[9]) for r in rows]


# 화면에 보여줄 대륙 탭 ↔ 실제 continent 컬럼값(들) 매핑. 신민용 확정:
# "오세아니아는 아시아에 통합, 북미+남미는 아메리카로 통합해서 표시하되
# 내부 데이터(countries.continent)는 그대로 나눠서 보관". UI는 이 함수
# 하나만 쓰면 되고, 실제 DB의 continent 값(아시아/유럽/아프리카/북미/
# 남미/오세아니아)은 그대로 둔다.
TEAM_POWER_RANKING_TABS = ["전체", "아시아", "유럽", "아프리카", "아메리카"]
_TAB_TO_CONTINENTS = {
    "아시아":   ["아시아", "오세아니아"],
    "유럽":     ["유럽"],
    "아프리카": ["아프리카"],
    "아메리카": ["남미", "북미", "북중미"],
}


def get_team_power_ranking_grouped(conn, ranking_year: int, tab: str = "전체",
                                    limit: int = 100) -> list:
    """파워랭킹 화면의 대륙 탭(전체/아시아/유럽/아프리카/아메리카) 전용
    조회 함수. get_team_power_ranking()은 DB에 실제 저장된 continent
    값 하나만 받으므로, 오세아니아→아시아·북미+남미→아메리카로 합쳐
    보여줘야 하는 화면 쪽 요구는 여기서 처리한다.
    [주의] 국가 필터(신민용 요청, 검색창 우측)는 여기서 SQL로 거르지
    않는다 — 걸러서 다시 LIMIT을 적용하면 "그 대륙 범위 안에서의 실제
    순위"가 아니라 "그 나라 팀들 중에서의 순위"로 뜻이 바뀌어 버린다.
    화면 쪽(_apply_pr_team_search)에서 이 함수가 돌려준 '대륙 범위
    전체' 캐시를 그대로 두고 국가 조건은 필터링만 하는 방식을 쓴다."""
    ensure_power_ranking_tables(conn)
    ensure_initial_team_power_ranking(conn)
    if tab not in _TAB_TO_CONTINENTS:
        return get_team_power_ranking(conn, ranking_year, continent=None, limit=limit)
    continents = _TAB_TO_CONTINENTS[tab]
    placeholders = ",".join("?" * len(continents))
    rows = conn.execute(
        f"""SELECT p.ranking_year, p.evaluation_year, p.team_id, p.team_name, p.continent,
                   p.country, p.rating, p.rank, p.prev_rank, t.current_tier
            FROM team_power_rankings p LEFT JOIN teams t ON t.id = p.team_id
            WHERE p.ranking_year=? AND p.continent IN ({placeholders})
            ORDER BY p.rating DESC LIMIT ?""",
        (ranking_year, *continents, limit)).fetchall()
    return [TeamPowerEntry(team_id=r[2], team_name=r[3], continent=r[4], country=r[5],
                            rating=r[6], rank=r[7], prev_rank=r[8],
                            ranking_year=r[0], evaluation_year=r[1], tier=r[9]) for r in rows]


def get_countries_in_tab_group(conn, tab: str) -> list:
    """[2026-08 신설, 신민용 요청] 검색창 우측 '국가 필터' 콤보의 선택지용
    — 지금 선택된 대륙 탭(전체/아시아/유럽/아프리카/아메리카)에 속한
    나라 이름만 정렬해서 돌려준다. "아시아를 선택하면 아시아 국가들만
    필터에 뜨고" — TEAM_POWER_RANKING_TABS와 동일한 오세아니아→아시아,
    북미+남미→아메리카 통합 규칙(_TAB_TO_CONTINENTS)을 그대로 쓴다."""
    if tab not in _TAB_TO_CONTINENTS:
        rows = conn.execute("SELECT name FROM countries ORDER BY name ASC").fetchall()
    else:
        continents = _TAB_TO_CONTINENTS[tab]
        placeholders = ",".join("?" * len(continents))
        rows = conn.execute(
            f"SELECT name FROM countries WHERE continent IN ({placeholders}) ORDER BY name ASC",
            continents).fetchall()
    return [r[0] for r in rows]


def get_latest_ranking_year(conn) -> Optional[int]:
    """세계기록실 파워랭킹 탭이 처음 열릴 때 기본으로 보여줄 연도.
    아직 한 번도 계산된 적이 없어도 ensure_initial_country_power_ranking()/
    ensure_initial_team_power_ranking() 덕분에 최소 GAME_START_YEAR는
    항상 존재한다."""
    ensure_power_ranking_tables(conn)
    ensure_initial_country_power_ranking(conn)
    ensure_initial_team_power_ranking(conn)
    row = conn.execute(
        """SELECT MAX(y) FROM (
               SELECT ranking_year AS y FROM team_power_rankings
               UNION
               SELECT ranking_year AS y FROM country_power_rankings)"""
    ).fetchone()
    return row[0] if row and row[0] else GAME_START_YEAR


def get_available_ranking_years(conn) -> list:
    """[2026-08 수정, 신민용 요청: "연도 필터도 게임 시작년도부터"] 국가
    파워랭킹은 ensure_initial_country_power_ranking()이 GAME_START_YEAR
    스냅샷을 항상 보장하므로, 팀 연도만 모으던 이전 방식 대신 팀·국가
    양쪽 ranking_year를 합쳐서(중복 제거) 돌려준다 — 그래야 아직 시즌이
    한 번도 안 끝나 팀 데이터가 없는 새 게임에서도 GAME_START_YEAR가
    선택 가능한 연도로 뜬다."""
    ensure_power_ranking_tables(conn)
    ensure_initial_country_power_ranking(conn)
    ensure_initial_team_power_ranking(conn)
    rows = conn.execute(
        """SELECT ranking_year FROM team_power_rankings
           UNION
           SELECT ranking_year FROM country_power_rankings
           ORDER BY ranking_year DESC"""
    ).fetchall()
    return [r[0] for r in rows]


def get_country_power_ranking(conn, ranking_year: int, limit: int = 250) -> list:
    """limit 기본값을 250으로 둔 이유: 신민용 확정 — 국가 파워랭킹은
    상위 N등이 아니라 countries.py에 등록된 211개국 전체를 항상 보여준다.
    250이면 211개국 전체가 잘리지 않고 다 들어간다."""
    ensure_power_ranking_tables(conn)
    ensure_initial_country_power_ranking(conn)
    rows = conn.execute(
        """SELECT ranking_year, evaluation_year, country, continent, rating, rank, prev_rank
           FROM country_power_rankings
           WHERE ranking_year=? ORDER BY rank ASC LIMIT ?""", (ranking_year, limit)).fetchall()
    return [CountryPowerEntry(country=r[2], continent=r[3], rating=r[4], rank=r[5],
                               prev_rank=r[6], ranking_year=r[0], evaluation_year=r[1])
            for r in rows]


def _country_seed_entries(conn) -> list:
    """countries.py의 다섯 번째 값(fifa_rank)만으로 만든 순수 시드 목록.
    DB에는 아무것도 안 쓴다 — ensure_initial_country_power_ranking()이
    이 목록을 실제 country_power_rankings 테이블에 저장하는 쪽."""
    rows = conn.execute(
        """SELECT name, continent, fifa_rank FROM countries
           ORDER BY fifa_rank ASC, name ASC""").fetchall()
    entries = []
    for i, (name, continent, fifa_rank) in enumerate(rows, start=1):
        entries.append(CountryPowerEntry(
            country=name, continent=continent or "",
            rating=_fifa_rank_to_seed_rating(fifa_rank),
            rank=i, prev_rank=None,
            ranking_year=GAME_START_YEAR, evaluation_year=GAME_START_YEAR - 1))
    return entries


def ensure_initial_country_power_ranking(conn):
    """[2026-08 신설, 신민용 리포트: "game.db 새로 돌렸는데 전년이 안 뜬다
    — 시작할 때 순위가 있으니 전년이 표시될 수 있잖아"] 첫 시즌이 끝나
    ranking_year=GAME_START_YEAR+1 스냅샷이 계산될 때 비교할 'GAME_START_YEAR
    시점 순위'가 DB에 아예 없으면 항상 "신규"로만 뜬다 — 그래서 게임이
    시작되는 순간(=이 함수가 처음 호출되는 순간) countries.py 시드값을
    ranking_year=GAME_START_YEAR 스냅샷으로 실제 저장해둔다. 이후
    compute_country_power_rankings(evaluation_year=GAME_START_YEAR)가
    ranking_year=GAME_START_YEAR+1을 계산할 때 _prev_rank_country가 바로
    이 행을 찾아 전년 대비 화살표를 정상적으로 그려준다. 이미 저장돼
    있으면(재호출돼도) 아무 것도 하지 않는 멱등 함수."""
    ensure_power_ranking_tables(conn)
    exists = conn.execute(
        "SELECT 1 FROM country_power_rankings WHERE ranking_year=? LIMIT 1",
        (GAME_START_YEAR,)).fetchone()
    if exists:
        return
    for e in _country_seed_entries(conn):
        conn.execute("""INSERT INTO country_power_rankings
            (ranking_year, evaluation_year, country, continent, rating, rank, prev_rank)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(ranking_year, country) DO NOTHING""",
            (e.ranking_year, e.evaluation_year, e.country, e.continent,
             e.rating, e.rank, e.prev_rank))
        # 레이어1(계속 움직이는 기초 레이팅)도 같은 시드로 미리 깔아둔다 —
        # 안 그러면 첫 실제 경기 갱신 때 DEFAULT_COUNTRY_RATING(1500)에서
        # 다시 시작해 이 스냅샷 값과 어긋난다.
        conn.execute("""INSERT INTO country_power_rating(country, rating, last_updated_year)
                         VALUES(?,?,0) ON CONFLICT(country) DO NOTHING""",
                     (e.country, e.rating))
    conn.commit()


def _ovr_to_seed_rating(avg_ovr: float) -> float:
    """[2026-08 신설, 신민용 요청: "팀 순위 OVR을 기준으로 해줄 수 있어?"]
    스쿼드 평균 OVR을 팀 파워랭킹 초기 레이팅으로 환산하는 순수 함수.
    country 쪽 _fifa_rank_to_seed_rating과 같은 역할 — OVR 40~99 정도의
    실제 분포를 DEFAULT_TEAM_RATING(1500) 근방 스케일로 펼친다.
    TUNE LATER: 계수는 감으로 잡은 값 — 실측 후 조정 필요."""
    return 1000.0 + avg_ovr * 10.0


def _team_seed_entries(conn) -> list:
    """[2026-08 신설, 신민용 요청] 아직 시즌이 한 번도 안 끝나
    team_power_rankings가 텅 빈 새 게임에서 쓰는 팀 초기 시드 목록 —
    ai_players 스쿼드 평균 OVR을 기준으로 정렬하고, OVR이 완전히 같으면
    리그 등급(현재 소속 부, 낮은 tier 숫자가 더 높은 등급 = 1부가 2부보다
    위)로, 그것도 같으면 team_id 순으로 그냥 아무렇게나 둔다(신민용:
    "이정도 차이면 바로 달라질테니 대충 위치하게 해도 됨"). ai_players에
    한 명도 없는 팀(스쿼드가 아직 안 채워진 신생 팀 등)은 45로 취급."""
    rows = conn.execute(
        """SELECT t.id, t.name, cn.continent, cn.name, t.current_tier,
                  COALESCE((SELECT AVG(ap.ovr) FROM ai_players ap WHERE ap.team_id = t.id), 45)
           FROM teams t JOIN countries cn ON t.country_id = cn.id
           ORDER BY 6 DESC, COALESCE(t.current_tier, 99) ASC, t.id ASC"""
    ).fetchall()
    entries = []
    for i, (team_id, name, continent, country, tier, avg_ovr) in enumerate(rows, start=1):
        entries.append(TeamPowerEntry(
            team_id=team_id, team_name=name, continent=continent or "",
            country=country or "", rating=_ovr_to_seed_rating(avg_ovr),
            rank=i, prev_rank=None,
            ranking_year=GAME_START_YEAR, evaluation_year=GAME_START_YEAR - 1,
            tier=tier))
    return entries


def ensure_initial_team_power_ranking(conn):
    """[2026-08 신설, 신민용 요청: "팀 순위도 시작년도부터 순위를 만들 수
    있지" — OVR 기준] ensure_initial_country_power_ranking()의 팀 버전.
    GAME_START_YEAR 스냅샷이 없으면 _team_seed_entries()로 만들어 저장한다
    (멱등 — 이미 있으면 아무 것도 안 함). 이후 첫 시즌이 끝나면 실제
    경기 결과 기반 계산값이 자연스럽게 이어받는다(country 쪽과 동일한
    설계: 레이어1 레이팅도 같은 시드로 미리 깔아둬 첫 갱신이 1500에서
    다시 시작하지 않게 한다)."""
    ensure_power_ranking_tables(conn)
    exists = conn.execute(
        "SELECT 1 FROM team_power_rankings WHERE ranking_year=? LIMIT 1",
        (GAME_START_YEAR,)).fetchone()
    if exists:
        return
    for e in _team_seed_entries(conn):
        conn.execute("""INSERT INTO team_power_rankings
            (ranking_year, evaluation_year, team_id, team_name, continent, country,
             rating, rank, prev_rank)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ranking_year, team_id) DO NOTHING""",
            (e.ranking_year, e.evaluation_year, e.team_id, e.team_name,
             e.continent, e.country, e.rating, e.rank, e.prev_rank))
        conn.execute("""INSERT INTO team_power_rating(team_id, rating, last_updated_year)
                         VALUES(?,?,0) ON CONFLICT(team_id) DO NOTHING""",
                     (e.team_id, e.rating))
    conn.commit()


def get_country_power_ranking_seed(conn) -> list:
    """[하위호환용] DB에 아무것도 안 쓰는 순수 조회 버전 — 지금 UI는
    ensure_initial_country_power_ranking()으로 실제 저장된 GAME_START_YEAR
    스냅샷을 get_country_power_ranking()으로 그냥 읽어오는 쪽을 쓰지만,
    저장이 불가능한(읽기 전용 conn 등) 상황을 위해 순수 버전도 남겨둔다."""
    return _country_seed_entries(conn)


def _continent_group_for(continent: str) -> list:
    """이 팀의 원본 continent 값이 UI 대륙 탭(전체/아시아/유럽/아프리카/
    아메리카, get_team_power_ranking_grouped와 동일한 오세아니아→아시아,
    북미+남미→아메리카 통합 규칙)에서 어느 그룹에 속하는지 찾아 그 그룹의
    continent 목록을 돌려준다. "대륙 순위"가 화면의 대륙 탭 순위와
    일치해야 하므로(원본 continent 하나만으로 세면 오세아니아/북미가
    아시아/아메리카 탭과 다른 숫자가 되어 버림)."""
    for continents in _TAB_TO_CONTINENTS.values():
        if continent in continents:
            return continents
    return [continent]


def get_team_power_history(conn, team_id: int) -> list:
    """[2026-08 수정, 신민용 요청: "연도, 전체 순위, 대륙 순위 이렇게
    3개로 뜨게"] 순위 클릭 시 뜨는 '이전 순위' 창용 —
    [(ranking_year, rank, continent_rank), ...]를 최신 연도부터
    내림차순으로 돌려준다. rank는 전체 기준 글로벌 순위, continent_rank는
    화면의 대륙 탭(오세아니아→아시아, 북미+남미→아메리카로 통합된 5개
    그룹)과 같은 기준으로 계산한 그 안에서의 순위 — team_power_rankings.rank가
    이미 rating 내림차순 전체 순서이므로, 같은 대륙그룹·같은 ranking_year에서
    rank가 이 팀보다 작거나 같은 행 개수를 세면 곧 대륙 내 순위가 된다
    (전역 순서를 부분집합으로 필터링해도 상대 순서는 그대로 유지되므로)."""
    ensure_power_ranking_tables(conn)
    rows = conn.execute(
        """SELECT ranking_year, rank, continent FROM team_power_rankings
           WHERE team_id=? ORDER BY ranking_year DESC""", (team_id,)).fetchall()
    result = []
    for ranking_year, rank, continent in rows:
        group = _continent_group_for(continent)
        placeholders = ",".join("?" * len(group))
        crow = conn.execute(
            f"""SELECT COUNT(*) FROM team_power_rankings
                WHERE ranking_year=? AND continent IN ({placeholders}) AND rank<=?""",
            (ranking_year, *group, rank)).fetchone()
        result.append((ranking_year, rank, crow[0] if crow else rank))
    return result


def get_country_power_history(conn, country: str) -> list:
    ensure_power_ranking_tables(conn)
    rows = conn.execute(
        """SELECT ranking_year, rank FROM country_power_rankings
           WHERE country=? ORDER BY ranking_year DESC""", (country,)).fetchall()
    return [(r[0], r[1]) for r in rows]