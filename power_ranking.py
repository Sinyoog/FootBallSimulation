# -*- coding: utf-8 -*-
"""
파워랭킹 시스템 (팀 파워랭킹 / 국가 파워랭킹) — v2

[설계 v2, 2026-08 — 신민용 "파워랭킹 설계도 v2" 문서를 그대로 코드화]
v1(단순 Elo + 우승보너스)에서 v2로 전면 개편. 핵심 구조:

    PS(Power Score) = MatchRating(레이어A) + AchievementRating(레이어B)

- 레이어 A: 경기마다 움직이는 매치 Elo. 상대 전력 대비 결과를 반영.
- 레이어 B: 대회가 끝났을 때 1회 지급되는 성적 보너스. 대회의 "격"에
  따라 같은 우승도 배점이 다르다(설계 원칙 1).

두 값은 DB에 항상 분리 저장한다(team_power_rating.a_rating/b_rating,
country_power_rating도 동일) — 밸런스 조정 시 "경기력 문제"인지
"보너스 문제"인지 구분하기 위함(설계 문서 1장). 화면에 보여주는
PS = a_rating + b_rating, 스무딩 없음(5.3 확정 — "실제 PS = 표시 PS").

**PS에는 강제 상·하한을 두지 않는다.** 등급(SS~F, 2장)은 저장값이 아니라
grade_for_ps()로 PS를 읽을 때마다 구간 매핑해서 "표시만" 한다. 예전
v1에 있던 country seed의 1200~2000 clamp(min/max)는 이번 v2에서 완전히
제거했다 — 실력 분포가 넓어지면 PS도 그만큼 넓게 퍼지는 게 정상이다.

── 연도 표기 규칙 (변경 없음) ─────────────────────────────────
evaluation_year = 실제로 경기를 치른 시즌 연도. ranking_year = eval+1,
그 성적이 "발표"되는 연도. run_year_end_power_ranking_update()는
game_engine.py의 연도전환 훅(_advance_week)에서 evaluation_year=(방금
끝난 시즌)으로 호출된다.

── v2에서 새로 생긴 파이프라인 (연도 1회 처리 순서) ────────────
1. update_team_ratings_for_year / update_country_ratings_for_year
   ① 레이어 A: 이 해에 열린 모든 경기를 대회가중치×단계가중치×
      (동일리그면 ×0.9)를 곱한 K로 대칭 Elo 갱신(3.2). 상한 |Δ|≤25
      (결승 ≤40), 상한도 좌우 대칭.
   ② 레이어 B: 대회별 최종 도달 스테이지를 판정해 배점표(3.6/4.4)
      보너스 지급, 국내리그/지역컵 연속우승 감쇠(3.7/4.7), 예선탈락
      페널티(4.6).
2. compute_*_power_rankings: 위에서 쌓인 PS(A+B)에 시즌전환
   리그레션(5.1 클럽/4.8 국가)을 적용해 "다음 시즌 시작 PS"를 만들고,
   그 값을 A:B 원래 비율로 재분배해 저장(1장 ①~④ 공식) → 이 값이
   곧 이번 ranking_year의 발표 PS이자 다음 해 매치 갱신의 출발점.

── 아직 단순화/보류한 부분 (TUNE LATER, 실측 후 보강 대상) ────
- 리그 레이어A는 경기 단위가 아니라 league_season_standings 집계를
  '리그 평균 상대'로 근사(경기 수가 너무 많아 DB 부하 문제로 v1부터
  유지해온 절충 — match_results_archive는 시즌이 쌓이면 프루닝되어
  나중엔 조회도 안 됨).
- 개최국 홈보너스(+60) 판정은 아직 안 함 — 항상 홈팀 기준 +40만 적용.
- 리그파워(3.1b)의 국제실적보정(②)은 최근 5년 레이어B 이력을 단순
  합산해 근사(원 설계의 시즌별 정밀 감쇠 대신 저장된 team_b_history를
  그대로 5년치 훑어서 계산).
- 3.5의 "결승(패)2.0 vs 결승(승)2.5" 두 값 중, 설계 원칙 7(제로섬은
  반드시 지킨다)을 우선해 결승전 단계가중치는 2.5 하나로 통일했다
  (한 경기의 Δ_home/Δ_away가 부호만 반대인 동일 값이어야 하므로, 같은
  경기에 서로 다른 두 배수를 곱하면 그 전제가 깨진다).
"""

from dataclasses import dataclass
from typing import Optional

from database import get_conn
from constants import (
    REGION_CUP_NAME, REGION_TO_CONTINENT, CONFEDERATIONS,
    CONTINENT_TO_CONF, CONF_CUP_NAME, EURO_NAME, GAME_START_YEAR,
)


# ══════════════════════════════════════════════════════════════
# 1. 등급(SS~F) ↔ PS 매핑 (2장) — 순수 표시용, PS에 영향 없음
# ══════════════════════════════════════════════════════════════

GRADE_BANDS = [
    ("SS", 2600, None),
    ("S", 2400, 2599),
    ("A", 2200, 2399),
    ("B", 2000, 2199),
    ("C", 1800, 1999),
    ("D", 1600, 1799),
    ("E", 1300, 1599),
    ("F", None, 1299),
]


def grade_for_ps(ps: float) -> str:
    """PS를 SS~F 구간으로 환산해 표시용 등급을 돌려준다. PS 자체엔 상/하한이
    없으므로(설계 확정), 구간표 바깥(2900 초과·1300 미만)도 자연스럽게
    SS/F로 흡수한다 — 등급만 "표시"고 PS는 그대로 저장·연산된다."""
    for name, lo, hi in GRADE_BANDS:
        if lo is not None and ps < lo:
            continue
        if hi is not None and ps > hi:
            continue
        return name
    return "F"


def k_for_grade(grade: str) -> float:
    return {"SS": 8, "S": 12, "A": 16, "B": 20, "C": 24, "D": 28,
            "E": 32, "F": 32}.get(grade, 24)


# ══════════════════════════════════════════════════════════════
# 2. Elo 엔진 (3.2) — 대칭(제로섬) 매치 델타
# ══════════════════════════════════════════════════════════════

HOME_BONUS = 40.0          # 홈팀 기대승률 계산에만 반영, 저장값엔 안 남음
DELTA_CAP = 25.0           # 경기당 변동폭 상한 (5.2)
DELTA_CAP_FINAL = 40.0     # 결승/우승전 예외 상한
SAME_LEAGUE_DISCOUNT = 0.9  # 대륙대항전에서 같은 리그 팀끼리 붙으면 단계가중치에 추가 할인


def expected_score(rating_a: float, rating_b: float, home_bonus: float = 0.0) -> float:
    return 1.0 / (1.0 + 10 ** (((rating_b) - (rating_a + home_bonus)) / 400.0))


def match_delta(rating_home: float, rating_away: float, grade_home: str, grade_away: str,
                 actual_home: float, comp_weight: float, stage_weight: float,
                 is_final: bool = False, home_bonus: float = HOME_BONUS):
    """3.2 공식. actual_home: 홈팀 관점 실제결과(R값, 아래 match_result_r 참고).
    돌려주는 (delta_home, delta_away)는 반드시 부호만 반대인 동일 크기 —
    한쪽이 얻은 점수는 정확히 반대쪽이 잃는다(설계 원칙 7, 3.2 확정).
    상한을 자른 뒤에도 대칭이 유지되도록, delta_home을 먼저 자르고
    delta_away는 그 잘린 값의 부호만 반대로 계산한다."""
    e_home = expected_score(rating_home, rating_away, home_bonus)
    k_match = (k_for_grade(grade_home) + k_for_grade(grade_away)) / 2.0
    raw = k_match * comp_weight * stage_weight * (actual_home - e_home)
    cap = DELTA_CAP_FINAL if is_final else DELTA_CAP
    delta_home = max(-cap, min(cap, raw))
    return delta_home, -delta_home


def match_result_r(home_score: int, away_score: int, pso_winner=None, home_id=None,
                    is_knockout: bool = False):
    """3.2 R값 표. is_knockout=True(승부차기가 존재할 수 있는 토너먼트)이고
    정규시간 무승부 뒤 pso_winner가 있으면 0.75/0.25, 그 외 무승부는 0.5."""
    if home_score is None or away_score is None or home_score < 0 or away_score < 0:
        return None
    if home_score > away_score:
        return 1.0
    if home_score < away_score:
        return 0.0
    if is_knockout and pso_winner:
        return 0.75 if (home_id is not None and pso_winner == home_id) else 0.25
    return 0.5


# ══════════════════════════════════════════════════════════════
# 3. 팀 — 대회/단계 가중치 (3.4/3.5)
# ══════════════════════════════════════════════════════════════

TEAM_COMPETITION_WEIGHT = {
    "league": 1.0, "domestic_cup": 0.6, "super_cup": 0.5,
    "europa": 1.1, "conference": 0.8, "club_world_cup": 1.8,
    # champions는 대륙별로 다름 → TEAM_CHAMPIONS_WEIGHT_BY_CONTINENT
}
TEAM_CHAMPIONS_WEIGHT_BY_CONTINENT = {
    "유럽": 1.6, "북남미": 1.7, "아시아": 1.6, "아프리카": 1.6,
}

# stage 라벨(실제 DB에 쓰이는 값: group/league/PO/R16/QF/SF/TP/F)을
# 3.5 단계가중치로 매핑. 'league'(CL 스위스리그 페이즈)와 'group'은 조별
# 리그 취급, 'PO'(CL 플레이오프)는 16강급으로 취급, 'TP'(3/4위전)는
# SF와 같은 급으로 취급(둘 다 "4강까지 갔다"는 사실은 동일).
_STAGE_WEIGHT = {
    "group": 1.0, "league": 1.0, "PO": 1.1, "R16": 1.1,
    "QF": 1.3, "SF": 1.6, "TP": 1.6, "F": 2.5,
}
_STAGE_RANK = {  # deepest-stage 판정용 서열 (숫자가 클수록 깊은 라운드)
    "group": 0, "league": 0, "PO": 1, "R16": 1, "QF": 2, "SF": 3, "TP": 3, "F": 4,
}


def stage_weight_for(stage: str) -> float:
    return _STAGE_WEIGHT.get(stage, 1.0)


# ══════════════════════════════════════════════════════════════
# 4. 팀 — 레이어B 배점표 (3.6, 하향 조정판)
# ══════════════════════════════════════════════════════════════

PLACEMENT_BASE_SCORE = {
    "champion": 40, "runner_up": 24, "semifinal": 12,
    "quarterfinal": 6, "round16": 3, "group_exit": 1,
}
# 리그 순위 보너스는 절대순위가 아니라 백분위(최종순위/참가팀수) 기반.
LEAGUE_PLACEMENT_BANDS = [  # (백분위 상한, 배점) — 순서대로 첫 매치 채택
    (0.0, 20),   # 1위(champion) 자체는 별도 처리하지만 안전망으로 포함
    (0.10, 10),
    (0.20, 6),
    (0.25, 3),
]


def league_placement_bonus(final_rank: int, n_teams: int) -> float:
    if n_teams <= 0:
        return 0.0
    if final_rank == 1:
        return 20.0
    percentile = final_rank / n_teams
    for cutoff, score in LEAGUE_PLACEMENT_BANDS[1:]:
        if percentile <= cutoff:
            return float(score)
    return 0.0


# 국내리그/지역컵 연속우승 감쇠 (3.7/4.7)
STREAK_DECAY = {1: 1.0, 2: 0.8, 3: 0.65, 4: 0.55}
STREAK_DECAY_FLOOR = 0.5  # 5회차 이상


def streak_decay_rate(streak_count: int) -> float:
    return STREAK_DECAY.get(streak_count, STREAK_DECAY_FLOOR if streak_count >= 5 else 1.0)


# ══════════════════════════════════════════════════════════════
# 5. 국가 — 대회 Tier 가중치 (4.2/4.3)
# ══════════════════════════════════════════════════════════════

COUNTRY_TIER_WEIGHT = {
    "world_cup": 2.6, "americas_cup": 1.4, "euro": 1.3,
    "asian_cup": 1.1, "afcon": 1.1,
}
# 지역컵 5단계 세분화(4.3). REGION_CUP_NAME의 키(지역명) 기준.
REGIONAL_CUP_TIER_WEIGHT = {
    "서아시아": 1.2, "서아프리카": 1.2,
    "중앙아메리카": 1.1, "오세아니아": 1.1,
    "동아시아": 1.0, "남부아프리카": 1.0,
    "동남아시아": 0.9, "북아프리카": 0.9, "중앙아프리카": 0.9,
    "동아프리카": 0.7, "카리브": 0.8,
    "중앙아시아": 0.6, "남아시아": 0.6,
}
QUALIFIER_FAIL_BASE_PENALTY = -8  # 4.6, 대회가중치를 곱해서 적용

# intl_tournaments.kind → COUNTRY_TIER_WEIGHT 키. euro는 kind='continent'를
# 대륙컵과 공유하고 name으로만 구분(world_browser 관례와 동일).
_COUNTRY_KIND_MAP_BY_CONTINENT = {
    "아시아": "asian_cup", "아프리카": "afcon", "아메리카": "americas_cup", "유럽": None,
}


def _country_tournament_weight(kind: str, name: str) -> Optional[tuple]:
    """(카테고리키, 가중치)를 돌려준다. region이면 카테고리키='region'이고
    가중치는 지역컵 세부 테이블(regional_cup_tier_weight)에서 별도 조회."""
    if kind == "world":
        return ("world_cup", COUNTRY_TIER_WEIGHT["world_cup"])
    if kind == "region":
        return ("region", None)  # 가중치는 지역명 확인 후 결정
    if kind == "continent":
        if name and EURO_NAME in name:
            return ("euro", COUNTRY_TIER_WEIGHT["euro"])
        # continent 값 자체가 tournament 테이블엔 없으므로 이름으로 유추
        for region, key in (("아시안컵", "asian_cup"), ("아프리카", "afcon"), ("아메리카", "americas_cup")):
            if name and region in name:
                return (key, COUNTRY_TIER_WEIGHT[key])
        return ("continental_unknown", 1.1)
    return None  # 예선류(wc_qual/cont_qual/euro_qual)는 레이어B 배점 대상 아님(예선탈락 페널티만 별도 처리)


def regional_cup_tier_weight(region_name: str) -> float:
    return REGIONAL_CUP_TIER_WEIGHT.get(region_name, 0.9)


def _region_of_cup_name(tournament_name: str) -> Optional[str]:
    for region, cup_name in REGION_CUP_NAME.items():
        if cup_name == tournament_name:
            return region
    return None


# ══════════════════════════════════════════════════════════════
# 6. 데이터 구조
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
    tier: Optional[int] = None


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
# 7. DB 스키마
# ══════════════════════════════════════════════════════════════

def ensure_power_ranking_tables(conn):
    c = conn.cursor()
    # 레이어1(계속 움직이는 기초 레이팅) — v2: A/B 분리 저장.
    c.execute("""CREATE TABLE IF NOT EXISTS team_power_rating(
        team_id INTEGER PRIMARY KEY,
        a_rating REAL DEFAULT 0, b_rating REAL DEFAULT 0,
        last_updated_year INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS country_power_rating(
        country TEXT PRIMARY KEY,
        a_rating REAL DEFAULT 0, b_rating REAL DEFAULT 0,
        last_updated_year INTEGER DEFAULT 0)""")
    # 연도별 스냅샷(발표 파워랭킹) — 표시 PS = a_rating+b_rating, 스무딩 없음(5.3).
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
    # 연속우승 카운터 (3.7/4.7)
    c.execute("""CREATE TABLE IF NOT EXISTS team_league_streak(
        league_id INTEGER PRIMARY KEY, winner_team_id INTEGER, streak INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS country_regional_streak(
        region TEXT PRIMARY KEY, winner_country TEXT, streak INTEGER DEFAULT 0)""")
    # 리그파워(3.1b) 연도별 캐시
    c.execute("""CREATE TABLE IF NOT EXISTS league_power(
        league_id INTEGER, year INTEGER, power REAL,
        PRIMARY KEY(league_id, year))""")
    # 팀별 레이어B 획득 이력(리그파워 국제실적보정 ②용, 최근 5년 조회)
    c.execute("""CREATE TABLE IF NOT EXISTS team_b_history(
        team_id INTEGER, year INTEGER, b_gain REAL,
        PRIMARY KEY(team_id, year))""")
    conn.commit()


# ══════════════════════════════════════════════════════════════
# 8. 레이팅 읽기/쓰기 헬퍼 (A/B 분리)
# ══════════════════════════════════════════════════════════════

def _get_team_ab(conn, team_id: int):
    row = conn.execute(
        "SELECT a_rating, b_rating FROM team_power_rating WHERE team_id=?", (team_id,)
    ).fetchone()
    return (row[0], row[1]) if row else (0.0, 0.0)


def _get_team_rating(conn, team_id: int) -> float:
    a, b = _get_team_ab(conn, team_id)
    return a + b


def _get_team_grade(conn, team_id: int) -> str:
    return grade_for_ps(_get_team_rating(conn, team_id))


def _add_team_a(conn, team_id: int, delta: float, year: int):
    a, b = _get_team_ab(conn, team_id)
    conn.execute("""INSERT INTO team_power_rating(team_id, a_rating, b_rating, last_updated_year)
                     VALUES(?,?,?,?)
                     ON CONFLICT(team_id) DO UPDATE SET
                        a_rating=excluded.a_rating, last_updated_year=excluded.last_updated_year""",
                 (team_id, a + delta, b, year))


def _add_team_b(conn, team_id: int, delta: float, year: int):
    a, b = _get_team_ab(conn, team_id)
    conn.execute("""INSERT INTO team_power_rating(team_id, a_rating, b_rating, last_updated_year)
                     VALUES(?,?,?,?)
                     ON CONFLICT(team_id) DO UPDATE SET
                        b_rating=excluded.b_rating, last_updated_year=excluded.last_updated_year""",
                 (team_id, a, b + delta, year))
    conn.execute("""INSERT INTO team_b_history(team_id, year, b_gain) VALUES(?,?,?)
                     ON CONFLICT(team_id, year) DO UPDATE SET b_gain=b_gain+excluded.b_gain""",
                 (team_id, year, delta))


def _get_country_ab(conn, country: str):
    row = conn.execute(
        "SELECT a_rating, b_rating FROM country_power_rating WHERE country=?", (country,)
    ).fetchone()
    if row:
        return (row[0], row[1])
    return _seed_country_ab(conn, country)


def _get_country_rating(conn, country: str) -> float:
    a, b = _get_country_ab(conn, country)
    return a + b


def _get_country_grade(conn, country: str) -> str:
    return grade_for_ps(_get_country_rating(conn, country))


def _add_country_a(conn, country: str, delta: float, year: int):
    a, b = _get_country_ab(conn, country)
    conn.execute("""INSERT INTO country_power_rating(country, a_rating, b_rating, last_updated_year)
                     VALUES(?,?,?,?)
                     ON CONFLICT(country) DO UPDATE SET
                        a_rating=excluded.a_rating, last_updated_year=excluded.last_updated_year""",
                 (country, a + delta, b, year))


def _add_country_b(conn, country: str, delta: float, year: int):
    a, b = _get_country_ab(conn, country)
    conn.execute("""INSERT INTO country_power_rating(country, a_rating, b_rating, last_updated_year)
                     VALUES(?,?,?,?)
                     ON CONFLICT(country) DO UPDATE SET
                        b_rating=excluded.b_rating, last_updated_year=excluded.last_updated_year""",
                 (country, a, b + delta, year))


# ══════════════════════════════════════════════════════════════
# 9. 초기 시드 (3.1/3.1b/4.1) — 상/하한 없음
# ══════════════════════════════════════════════════════════════

SEED_BASE = 1400.0
SEED_OVR_COEF = 30.0
LEAGUE_POWER_ALPHA = 15.0
LEAGUE_POWER_INTL_SHARE = 0.15  # ②의 비중(85:15)
LEAGUE_POWER_INTL_CAP = 80.0
COUNTRY_CONTINENT_BONUS = {
    "유럽": 60, "남미": 60, "아프리카": 20, "북미": 20,
    "아시아": 0, "오세아니아": 0,
}


def _team_avg_ovr_seed(conn, team_id: int) -> float:
    row = conn.execute(
        "SELECT AVG(ovr) FROM ai_players WHERE team_id=?", (team_id,)).fetchone()
    return row[0] if row and row[0] else 45.0


def compute_league_power(conn, year: int) -> dict:
    """3.1b. 리그별 OVR지표(①, 85%) + 국제실적보정(②, 15%)을 합쳐
    league_id → 리그등급보정 값을 돌려주고 league_power에 캐시한다.
    "같은 부(tier)끼리만" 기준평균을 비교한다(1부는 1부끼리)."""
    leagues = conn.execute("SELECT id, tier FROM leagues").fetchall()
    if not leagues:
        return {}
    # ① 리그별 OVR지표
    ovr_index = {}
    for league_id, tier in leagues:
        teams = conn.execute("SELECT id FROM teams WHERE league_id=?", (league_id,)).fetchall()
        team_ids = [t[0] for t in teams]
        if not team_ids:
            ovr_index[league_id] = None
            continue
        ovrs = sorted((_team_avg_ovr_seed(conn, tid) for tid in team_ids), reverse=True)
        n = len(ovrs)
        top_n = max(1, round(n * 0.25))
        avg_all = sum(ovrs) / n
        avg_top = sum(ovrs[:top_n]) / top_n
        avg_bottom = sum(ovrs[-top_n:]) / top_n
        ovr_index[league_id] = avg_all * 0.5 + avg_top * 0.3 + avg_bottom * 0.2

    # 같은 tier끼리 기준 평균
    tier_of = {lid: tier for lid, tier in leagues}
    tier_baseline = {}
    for lid, idx in ovr_index.items():
        if idx is None:
            continue
        t = tier_of[lid]
        tier_baseline.setdefault(t, []).append(idx)
    tier_baseline = {t: sum(v) / len(v) for t, v in tier_baseline.items()}

    # ② 국제실적보정 — 최근 5년 team_b_history 감쇠합, 리그로 분배
    decay_by_age = {1: 1.0, 2: 0.75, 3: 0.5, 4: 0.3, 5: 0.15}
    result = {}
    for lid, idx in ovr_index.items():
        base = 0.0
        if idx is not None and tier_of[lid] in tier_baseline:
            base = (idx - tier_baseline[tier_of[lid]]) * LEAGUE_POWER_ALPHA
        teams = conn.execute("SELECT id FROM teams WHERE league_id=?", (lid,)).fetchall()
        team_ids = [t[0] for t in teams]
        intl = 0.0
        if team_ids:
            placeholders = ",".join("?" * len(team_ids))
            rows = conn.execute(
                f"""SELECT year, SUM(b_gain) FROM team_b_history
                    WHERE team_id IN ({placeholders}) AND year <= ? AND year > ?
                    GROUP BY year""",
                (*team_ids, year, year - 5)).fetchall()
            total = 0.0
            for y, gain in rows:
                age = year - y + 1
                total += (gain or 0.0) * decay_by_age.get(age, 0.0)
            intl = (total * 0.10) / len(team_ids)
        intl = max(-LEAGUE_POWER_INTL_CAP, min(LEAGUE_POWER_INTL_CAP, intl))
        final = base * (1 - LEAGUE_POWER_INTL_SHARE) + intl * LEAGUE_POWER_INTL_SHARE
        result[lid] = final
        conn.execute("""INSERT INTO league_power(league_id, year, power) VALUES(?,?,?)
                         ON CONFLICT(league_id, year) DO UPDATE SET power=excluded.power""",
                     (lid, year, final))
    conn.commit()
    return result


def _team_seed_ab(conn, team_id: int, league_power_cache: dict) -> tuple:
    """3.1: PS_초기 = 1400 + (OVR-60)×30 + 리그등급보정. 상/하한 없음.
    시드 전량은 레이어A(경기력)로 잡는다 — 아직 아무 대회 성적도 없는
    시점이므로 레이어B는 0에서 출발."""
    row = conn.execute("SELECT league_id FROM teams WHERE id=?", (team_id,)).fetchone()
    league_id = row[0] if row else None
    ovr = _team_avg_ovr_seed(conn, team_id)
    league_adj = league_power_cache.get(league_id, 0.0) if league_id else 0.0
    ps = SEED_BASE + (ovr - 60.0) * SEED_OVR_COEF + league_adj
    return (ps, 0.0)


def _seed_country_ab(conn, country: str) -> tuple:
    """[2026-08 수정, 신민용 명시적 확정 — 국가 초기 시드는 반드시
    countries.py의 다섯 번째 값(fifa_rank)을 그대로 순위로 써야 한다]
    4.1 문서 원안은 "대표팀 평균 OVR" 기반이었지만, 게임 시작 시점엔
    ai_players.nationality로 그 나라 대표팀 선수를 안정적으로 특정할 수
    없어(스쿼드가 아직 안 갖춰진 나라가 많음) 엉뚱한 순서(예: 체코 1위,
    스코틀랜드 2위)가 나왔다 — fifa_rank는 항상 존재하고 신뢰할 수 있는
    값이므로 이것을 그대로 초기 강함의 척도로 쓴다. 대륙보정은 유지."""
    row = conn.execute(
        "SELECT continent, fifa_rank FROM countries WHERE name=?", (country,)).fetchone()
    continent = row[0] if row else ""
    fifa_rank = row[1] if row and row[1] else 100
    bonus = COUNTRY_CONTINENT_BONUS.get(continent, 0)
    ps = SEED_BASE + (211 - fifa_rank) * (SEED_OVR_COEF * 39.0 / 210.0) + bonus
    # (211-fifa_rank)가 0(최하위)~210(1위)로 움직이도록 뒤집고, OVR 스케일
    # (60~99, 39점 폭)과 비슷한 체감 폭이 나오게 계수를 맞췄다. TUNE LATER.
    conn.execute("""INSERT INTO country_power_rating(country, a_rating, b_rating, last_updated_year)
                     VALUES(?,?,0,0) ON CONFLICT(country) DO NOTHING""", (country, ps))
    return (ps, 0.0)


# ══════════════════════════════════════════════════════════════
# 10. 팀 레이어 A — 매치 결과 반영
# ══════════════════════════════════════════════════════════════

_CLUB_COMP_TABLES = {
    "champions": ("cl_tournaments", "cl_matches"),
    "europa": ("el_tournaments", "el_matches"),
    "conference": ("ecl_tournaments", "ecl_matches"),
    "club_world_cup": ("cwc_tournaments", "cwc_matches"),
    "super_cup": ("sc_tournaments", "sc_matches"),
    "domestic_cup": ("cup_tournaments", "cup_matches"),
}


def _team_league_of(conn, team_id: int):
    row = conn.execute("SELECT league_id FROM teams WHERE id=?", (team_id,)).fetchone()
    return row[0] if row else None


def _team_continent_for_champions(conn, team_id: int) -> Optional[str]:
    row = conn.execute(
        """SELECT cn.continent FROM teams t JOIN countries cn ON t.country_id=cn.id
           WHERE t.id=?""", (team_id,)).fetchone()
    if not row:
        return None
    continent = row[0]
    if continent in ("아시아", "오세아니아"):
        return "아시아"
    if continent in ("북미", "남미"):
        return "북남미"
    return continent


def _update_team_a_from_matches(conn, matches_table: str, tournament_id: int, year: int,
                                 comp_weight: float, use_stage_col: bool = True,
                                 discount_same_league: bool = False):
    stage_col = "stage" if use_stage_col else "round_idx"
    rows = conn.execute(
        f"""SELECT home_team_id, away_team_id, home_score, away_score, pso_winner, {stage_col}
            FROM {matches_table} WHERE tournament_id=? ORDER BY id ASC""",
        (tournament_id,)).fetchall()
    for home_id, away_id, hs, as_, pso, stage in rows:
        if not home_id or not away_id:
            continue
        r = match_result_r(hs, as_, pso, home_id, is_knockout=True)
        if r is None:
            continue
        if use_stage_col:
            sw = stage_weight_for(stage)
            is_final = (stage == "F")
        else:
            sw = 1.2  # 국내컵은 round_idx만 있어 세부 단계가중치 대신 완만한 고정값 사용
            is_final = False
        if discount_same_league and _team_league_of(conn, home_id) == _team_league_of(conn, away_id):
            sw *= SAME_LEAGUE_DISCOUNT
        rh, ra = _get_team_rating(conn, home_id), _get_team_rating(conn, away_id)
        gh, ga = grade_for_ps(rh), grade_for_ps(ra)
        d_home, d_away = match_delta(rh, ra, gh, ga, r, comp_weight, sw, is_final=is_final)
        _add_team_a(conn, home_id, d_home, year)
        _add_team_a(conn, away_id, d_away, year)


def _update_team_a_from_league(conn, evaluation_year: int):
    """리그는 경기 단위 대신 league_season_standings 집계를 '리그 평균
    상대'로 근사(TUNE LATER, 상단 docstring 참고). 단계가중치는 1.0 고정."""
    rows = conn.execute(
        """SELECT team_id, wins, draws, losses
           FROM league_season_standings WHERE year=?""", (evaluation_year,)).fetchall()
    if not rows:
        return
    ratings = {tid: _get_team_rating(conn, tid) for tid, *_ in rows}
    league_avg = sum(ratings.values()) / len(ratings)
    w = TEAM_COMPETITION_WEIGHT["league"]
    for team_id, wins, draws, losses in rows:
        rating = ratings[team_id]
        grade = grade_for_ps(rating)
        avg_grade = grade_for_ps(league_avg)
        n_games = (wins or 0) + (draws or 0) + (losses or 0)
        if n_games == 0:
            continue
        actual_points = (wins or 0) * 1.0 + (draws or 0) * 0.5
        e = expected_score(rating, league_avg)
        expected_points = e * n_games
        k_match = (k_for_grade(grade) + k_for_grade(avg_grade)) / 2.0
        raw = k_match * w * (actual_points - expected_points) / max(1, n_games) \
              * min(n_games, 10) / 10
        delta = max(-DELTA_CAP, min(DELTA_CAP, raw))
        _add_team_a(conn, team_id, delta, evaluation_year)


def update_team_ratings_for_year(conn, evaluation_year: int):
    ensure_power_ranking_tables(conn)
    _update_team_a_from_league(conn, evaluation_year)
    for category, (tournaments_table, matches_table) in _CLUB_COMP_TABLES.items():
        tids_rows = conn.execute(
            f"SELECT id, continent FROM {tournaments_table} WHERE year=?"
            if category == "champions" else
            f"SELECT id, NULL FROM {tournaments_table} WHERE year=?",
            (evaluation_year,)).fetchall()
        for tid, continent in tids_rows:
            if category == "champions":
                weight = TEAM_CHAMPIONS_WEIGHT_BY_CONTINENT.get(continent, 1.6)
            else:
                weight = TEAM_COMPETITION_WEIGHT[category]
            _update_team_a_from_matches(
                conn, matches_table, tid, evaluation_year, weight,
                use_stage_col=(category != "domestic_cup"),
                discount_same_league=(category in ("champions", "europa", "conference")))
    conn.commit()


# ══════════════════════════════════════════════════════════════
# 11. 팀 레이어 B — 대회 성적 보너스 (3.6/3.7)
# ══════════════════════════════════════════════════════════════

def _deepest_stage_participants(conn, matches_table: str, tournament_id: int, use_stage_col=True):
    """토너먼트의 실제 경기를 훑어 참가자별 '도달한 최고 단계'를 판정한다.
    반환: {team_id: tier_label}, tier_label ∈
    champion/runner_up/semifinal/quarterfinal/round16/group_exit"""
    stage_col = "stage" if use_stage_col else "round_idx"
    rows = conn.execute(
        f"""SELECT home_team_id, away_team_id, home_score, away_score, pso_winner, {stage_col}
            FROM {matches_table} WHERE tournament_id=?""", (tournament_id,)).fetchall()
    if not rows:
        return {}
    if use_stage_col:
        best_rank = {}
        final_match = None
        for home_id, away_id, hs, as_, pso, stage in rows:
            if hs is None or as_ is None or hs < 0 or as_ < 0:
                continue
            rank = _STAGE_RANK.get(stage, 0)
            for tid in (home_id, away_id):
                if tid:
                    best_rank[tid] = max(best_rank.get(tid, -1), rank)
            if stage == "F":
                final_match = (home_id, away_id, hs, as_, pso)
        result = {}
        for tid, rank in best_rank.items():
            if rank == 4:
                if final_match and tid in final_match[:2]:
                    home_id, away_id, hs, as_, pso = final_match
                    winner_is_home = (hs > as_) or (hs == as_ and pso == home_id)
                    won = (tid == home_id) == winner_is_home
                    result[tid] = "champion" if won else "runner_up"
            elif rank == 3:
                result[tid] = "semifinal"
            elif rank == 2:
                result[tid] = "quarterfinal"
            elif rank == 1:
                result[tid] = "round16"
            else:
                result[tid] = "group_exit"
        return result
    else:
        # 국내컵: round_idx 상대 서열(가장 큰 값=결승)로 근사
        idxs = sorted({r[5] for r in rows if r[5] is not None}, reverse=True)
        rank_of_idx = {idx: min(i, 4) for i, idx in enumerate(idxs)}  # 0=F,1=SF,2=QF,3=R16,4+=조별
        best_rank = {}
        final_match = None
        for home_id, away_id, hs, as_, pso, idx in rows:
            if hs is None or as_ is None or hs < 0 or as_ < 0 or idx is None:
                continue
            rank = 4 - rank_of_idx.get(idx, 4)  # 뒤집어서 크게=깊은 라운드로 통일
            for tid in (home_id, away_id):
                if tid:
                    best_rank[tid] = max(best_rank.get(tid, -1), rank)
            if rank_of_idx.get(idx) == 0:
                final_match = (home_id, away_id, hs, as_, pso)
        result = {}
        for tid, rank in best_rank.items():
            if rank == 4 and final_match and tid in final_match[:2]:
                home_id, away_id, hs, as_, pso = final_match
                winner_is_home = (hs > as_) or (hs == as_ and pso == home_id)
                won = (tid == home_id) == winner_is_home
                result[tid] = "champion" if won else "runner_up"
            elif rank == 3:
                result[tid] = "semifinal"
            elif rank == 2:
                result[tid] = "quarterfinal"
            elif rank == 1:
                result[tid] = "round16"
            else:
                result[tid] = "group_exit"
        return result


def _apply_team_league_streak(conn, league_id: int, champion_team_id: int) -> float:
    """3.7 — 리그 우승 연속 감쇠율을 돌려주고 카운터를 갱신한다."""
    row = conn.execute(
        "SELECT winner_team_id, streak FROM team_league_streak WHERE league_id=?",
        (league_id,)).fetchone()
    if row and row[0] == champion_team_id:
        streak = row[1] + 1
    else:
        streak = 1
    conn.execute("""INSERT INTO team_league_streak(league_id, winner_team_id, streak)
                     VALUES(?,?,?)
                     ON CONFLICT(league_id) DO UPDATE SET
                        winner_team_id=excluded.winner_team_id, streak=excluded.streak""",
                 (league_id, champion_team_id, streak))
    return streak_decay_rate(streak)


def update_team_b_for_year(conn, evaluation_year: int):
    # 1) 국내리그 순위 보너스(백분위 기반) + 연속우승 감쇠
    rows = conn.execute(
        """SELECT l.id, s.team_id, s.wins, s.draws, s.losses
           FROM league_season_standings s JOIN leagues l ON s.league_id = l.id
           WHERE s.year=?""", (evaluation_year,)).fetchall()
    by_league = {}
    for league_id, team_id, wins, draws, losses in rows:
        pts = (wins or 0) * 3 + (draws or 0)
        by_league.setdefault(league_id, []).append((team_id, pts))
    for league_id, standings in by_league.items():
        standings.sort(key=lambda x: x[1], reverse=True)
        n = len(standings)
        champion_id = standings[0][0] if standings else None
        decay = _apply_team_league_streak(conn, league_id, champion_id) if champion_id else 1.0
        for rank, (team_id, _pts) in enumerate(standings, start=1):
            bonus = league_placement_bonus(rank, n)
            if bonus <= 0:
                continue
            if rank == 1:
                bonus *= decay
            _add_team_b(conn, team_id, bonus * TEAM_COMPETITION_WEIGHT["league"], evaluation_year)

    # 2) 국제/국내컵 계열 대회 성적 보너스 (deepest-stage 판정)
    for category, (tournaments_table, matches_table) in _CLUB_COMP_TABLES.items():
        rows = conn.execute(
            f"SELECT id, {'continent' if category == 'champions' else 'NULL'} "
            f"FROM {tournaments_table} WHERE year=?", (evaluation_year,)).fetchall()
        for tid, continent in rows:
            weight = (TEAM_CHAMPIONS_WEIGHT_BY_CONTINENT.get(continent, 1.6)
                      if category == "champions" else TEAM_COMPETITION_WEIGHT[category])
            placements = _deepest_stage_participants(
                conn, matches_table, tid, use_stage_col=(category != "domestic_cup"))
            for team_id, tier in placements.items():
                base = PLACEMENT_BASE_SCORE[tier]
                _add_team_b(conn, team_id, base * weight, evaluation_year)


# ══════════════════════════════════════════════════════════════
# 12. 국가 레이어 A/B
# ══════════════════════════════════════════════════════════════

def _intl_tournament_weight_key(kind: str, name: str):
    return _country_tournament_weight(kind, name)


def _update_country_a_from_matches(conn, tournament_id: int, year: int, weight: float,
                                    stage_weight_override: Optional[float] = None):
    rows = conn.execute(
        """SELECT home, away, home_score, away_score, pso_winner, stage
           FROM intl_matches WHERE tournament_id=? ORDER BY id ASC""",
        (tournament_id,)).fetchall()
    for home, away, hs, as_, pso, stage in rows:
        if not home or not away:
            continue
        r = match_result_r(hs, as_, pso, home, is_knockout=True)
        if r is None:
            continue
        sw = stage_weight_override if stage_weight_override is not None else stage_weight_for(stage)
        is_final = (stage == "F")
        rh, ra = _get_country_rating(conn, home), _get_country_rating(conn, away)
        gh, ga = grade_for_ps(rh), grade_for_ps(ra)
        d_home, d_away = match_delta(rh, ra, gh, ga, r, weight, sw, is_final=is_final)
        _add_country_a(conn, home, d_home, year)
        _add_country_a(conn, away, d_away, year)


def update_country_ratings_for_year(conn, evaluation_year: int):
    ensure_power_ranking_tables(conn)
    rows = conn.execute(
        "SELECT id, kind, name FROM intl_tournaments WHERE year=?", (evaluation_year,)
    ).fetchall()
    for tid, kind, name in rows:
        wk = _intl_tournament_weight_key(kind, name)
        if wk is None:
            # 예선류 — 개별 경기는 그래도 레이어A로 실시간 반영(4.6)
            if kind and kind.endswith("_qual"):
                base_kind = kind[:-5]
                base_weight = {"wc": 2.6, "cont": 1.1, "euro": 1.3}.get(base_kind, 1.0)
                is_playoff_final = False  # 세부 판별은 TUNE LATER, 기본 1.0으로 처리
                _update_country_a_from_matches(
                    conn, tid, evaluation_year, base_weight,
                    stage_weight_override=1.2 if is_playoff_final else 1.0)
            continue
        category, weight = wk
        if category == "region":
            region = _region_of_cup_name(name)
            weight = regional_cup_tier_weight(region) if region else 0.9
        _update_country_a_from_matches(conn, tid, evaluation_year, weight)
    conn.commit()


def _apply_country_regional_streak(conn, region: str, champion_country: str) -> float:
    row = conn.execute(
        "SELECT winner_country, streak FROM country_regional_streak WHERE region=?",
        (region,)).fetchone()
    streak = row[1] + 1 if (row and row[0] == champion_country) else 1
    conn.execute("""INSERT INTO country_regional_streak(region, winner_country, streak)
                     VALUES(?,?,?)
                     ON CONFLICT(region) DO UPDATE SET
                        winner_country=excluded.winner_country, streak=excluded.streak""",
                 (region, champion_country, streak))
    return streak_decay_rate(streak)


def update_country_b_for_year(conn, evaluation_year: int):
    rows = conn.execute(
        "SELECT id, kind, name FROM intl_tournaments WHERE year=?", (evaluation_year,)
    ).fetchall()
    # 이 해에 열린 예선 목록(4.6 페널티 판정용) — kind가 *_qual인 것들
    qual_rows = [(tid, kind, name) for tid, kind, name in rows if kind and kind.endswith("_qual")]
    main_rows = [(tid, kind, name) for tid, kind, name in rows if not (kind and kind.endswith("_qual"))]

    for tid, kind, name in main_rows:
        wk = _intl_tournament_weight_key(kind, name)
        if wk is None:
            continue
        category, weight = wk
        region = None
        if category == "region":
            region = _region_of_cup_name(name)
            weight = regional_cup_tier_weight(region) if region else 0.9
        placements = _deepest_stage_participants(conn, "intl_matches", tid, use_stage_col=True) \
            if False else _country_deepest_stage(conn, tid)
        champion = None
        for country, tier in placements.items():
            base = PLACEMENT_BASE_SCORE[tier]
            decay = 1.0
            if tier == "champion":
                champion = country
                if category == "region" and region:
                    decay = _apply_country_regional_streak(conn, region, country)
            _add_country_b(conn, country, base * weight * decay, evaluation_year)

    # 예선 탈락 페널티(4.6): 예선에 참가했지만 같은 해 본선 entries에 없는 국가
    for qtid, qkind, qname in qual_rows:
        base_kind = qkind[:-5]
        main_key, base_weight = {"wc": ("world_cup", 2.6), "cont": (None, 1.1),
                                  "euro": ("euro", 1.3)}.get(base_kind, (None, 1.0))
        qual_countries = {r[0] for r in conn.execute(
            "SELECT DISTINCT country FROM intl_entries WHERE tournament_id=?", (qtid,)).fetchall()}
        # 같은 해, 같은 계열의 본선 entries
        main_countries = set()
        for tid, kind, name in main_rows:
            if (base_kind == "wc" and kind == "world") or \
               (base_kind in ("cont", "euro") and kind == "continent"):
                main_countries |= {r[0] for r in conn.execute(
                    "SELECT DISTINCT country FROM intl_entries WHERE tournament_id=?",
                    (tid,)).fetchall()}
        failed = qual_countries - main_countries
        for country in failed:
            _add_country_b(conn, country, QUALIFIER_FAIL_BASE_PENALTY * base_weight, evaluation_year)
    conn.commit()


def _country_deepest_stage(conn, tournament_id: int) -> dict:
    rows = conn.execute(
        """SELECT home, away, home_score, away_score, pso_winner, stage
           FROM intl_matches WHERE tournament_id=?""", (tournament_id,)).fetchall()
    if not rows:
        return {}
    best_rank = {}
    final_match = None
    for home, away, hs, as_, pso, stage in rows:
        if hs is None or as_ is None or hs < 0 or as_ < 0:
            continue
        rank = _STAGE_RANK.get(stage, 0)
        for c in (home, away):
            if c:
                best_rank[c] = max(best_rank.get(c, -1), rank)
        if stage == "F":
            final_match = (home, away, hs, as_, pso)
    result = {}
    for country, rank in best_rank.items():
        if rank == 4:
            if final_match and country in final_match[:2]:
                home, away, hs, as_, pso = final_match
                winner_is_home = (hs > as_) or (hs == as_ and pso == home)
                won = (country == home) == winner_is_home
                result[country] = "champion" if won else "runner_up"
        elif rank == 3:
            result[country] = "semifinal"
        elif rank == 2:
            result[country] = "quarterfinal"
        elif rank == 1:
            result[country] = "round16"
        else:
            result[country] = "group_exit"
    return result


# ══════════════════════════════════════════════════════════════
# 13. 시즌 전환 리그레션 (5.1 클럽 / 4.8 국가) — PS 전체에 적용 후 A:B 재분배
# ══════════════════════════════════════════════════════════════

REGRESSION_BASE = 0.85          # 클럽 기본 (1-수렴비율)
SOFT_RESET_TRIGGER_1 = 0.08     # 로스터 OVR 변화율 8%↑ → 수렴비율 0.25
SOFT_RESET_TRIGGER_2 = 0.15     # 15%↑ → 0.35


def _team_ovr_change_rate(conn, team_id: int, prev_year_ovr: Optional[float]) -> float:
    if not prev_year_ovr:
        return 0.0
    cur = _team_avg_ovr_seed(conn, team_id)
    if prev_year_ovr == 0:
        return 0.0
    return abs(cur - prev_year_ovr) / prev_year_ovr


def _regress_and_split(ps: float, seed_ps: float, a: float, b: float, convergence: float):
    """1장 ①~④ 공식: PS 전체에 회귀 적용 후 원래 A:B 비율로 재분배."""
    new_ps = ps * (1 - convergence) + seed_ps * convergence
    if ps == 0:
        return new_ps, 0.0
    ratio_a = a / ps
    return new_ps * ratio_a, new_ps * (1 - ratio_a)


def apply_team_season_regression(conn, evaluation_year: int, league_power_cache: dict):
    teams = conn.execute("SELECT id FROM teams").fetchall()
    for (team_id,) in teams:
        a, b = _get_team_ab(conn, team_id)
        ps = a + b
        seed_ps, _ = _team_seed_ab(conn, team_id, league_power_cache)
        convergence = 1 - REGRESSION_BASE
        # 소프트 리셋: 직전 연도 OVR 기록이 team_b_history에는 없으므로,
        # 간단화: 이번 연도 시드 OVR과 seed_ps 계산에 쓰인 OVR을 직접 비교하는
        # 대신, 현재 OVR 자체를 다시 조회해 큰 변화가 있었는지는 시드값과
        # 현재 PS의 괴리 크기로 근사 판단한다(TUNE LATER — 정밀 전년 OVR
        # 스냅샷 테이블은 다음 단계에서 추가 가능).
        if seed_ps and ps:
            drift = abs(ps - seed_ps) / max(abs(seed_ps), 1.0)
            if drift >= SOFT_RESET_TRIGGER_2:
                convergence = 0.35
            elif drift >= SOFT_RESET_TRIGGER_1:
                convergence = 0.25
        new_a, new_b = _regress_and_split(ps, seed_ps, a, b, convergence)
        conn.execute("""UPDATE team_power_rating SET a_rating=?, b_rating=?, last_updated_year=?
                         WHERE team_id=?""", (new_a, new_b, evaluation_year, team_id))
    conn.commit()


def _country_last_intl_year(conn, country: str, upto_year: int) -> Optional[int]:
    row = conn.execute(
        """SELECT MAX(t.year) FROM intl_entries e JOIN intl_tournaments t ON e.tournament_id=t.id
           WHERE e.country=? AND t.year<=?""", (country, upto_year)).fetchone()
    return row[0] if row and row[0] else None


def apply_country_season_regression(conn, evaluation_year: int):
    countries = conn.execute("SELECT name FROM countries").fetchall()
    for (country,) in countries:
        a, b = _get_country_ab(conn, country)
        ps = a + b
        seed_ps, _ = _seed_country_ab(conn, country)
        last_year = _country_last_intl_year(conn, country, evaluation_year)
        if last_year is None:
            convergence = 0.45
        else:
            gap = evaluation_year - last_year
            convergence = {0: 0.15, 1: 0.25, 2: 0.35}.get(gap, 0.45 if gap >= 3 else 0.15)
        new_a, new_b = _regress_and_split(ps, seed_ps, a, b, convergence)
        conn.execute("""UPDATE country_power_rating SET a_rating=?, b_rating=?, last_updated_year=?
                         WHERE country=?""", (new_a, new_b, evaluation_year, country))
    conn.commit()


# ══════════════════════════════════════════════════════════════
# 14. 스냅샷 계산/저장 (5.3 — 스무딩 없음, 리그레션 후 값 그대로 표시)
# ══════════════════════════════════════════════════════════════

def _prev_rank_team(conn, team_id: int, ranking_year: int) -> Optional[int]:
    row = conn.execute(
        "SELECT rank FROM team_power_rankings WHERE team_id=? AND ranking_year=?",
        (team_id, ranking_year - 1)).fetchone()
    return row[0] if row else None


def _prev_rank_country(conn, country: str, ranking_year: int) -> Optional[int]:
    row = conn.execute(
        "SELECT rank FROM country_power_rankings WHERE country=? AND ranking_year=?",
        (country, ranking_year - 1)).fetchone()
    return row[0] if row else None


def compute_team_power_rankings(conn, evaluation_year: int) -> list:
    ranking_year = evaluation_year + 1
    ensure_power_ranking_tables(conn)
    teams = conn.execute(
        """SELECT t.id, t.name, c.continent, c.name
           FROM teams t JOIN countries c ON t.country_id = c.id""").fetchall()
    entries = []
    for team_id, team_name, continent, country in teams:
        rating = _get_team_rating(conn, team_id)
        entries.append(TeamPowerEntry(
            team_id=team_id, team_name=team_name, continent=continent,
            country=country, rating=rating,
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
                evaluation_year=excluded.evaluation_year, team_name=excluded.team_name,
                continent=excluded.continent, country=excluded.country,
                rating=excluded.rating, rank=excluded.rank, prev_rank=excluded.prev_rank""",
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
        rating = _get_country_rating(conn, country)
        entries.append(CountryPowerEntry(
            country=country, continent=continent, rating=rating,
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
                evaluation_year=excluded.evaluation_year, continent=excluded.continent,
                rating=excluded.rating, rank=excluded.rank, prev_rank=excluded.prev_rank""",
            (e.ranking_year, e.evaluation_year, e.country, e.continent,
             e.rating, e.rank, e.prev_rank))
    conn.commit()
    return entries


# ══════════════════════════════════════════════════════════════
# 15. 오케스트레이터
# ══════════════════════════════════════════════════════════════

def run_year_end_power_ranking_update(conn, evaluation_year: int):
    """game_engine.py 연도전환 훅에서 호출하는 단일 진입점. 순서:
    레이어A(경기결과) → 레이어B(대회성적) → 시즌전환 리그레션(A:B 재분배)
    → 스냅샷 저장. evaluation_year=방금 끝난 시즌, ranking_year=eval+1."""
    ensure_power_ranking_tables(conn)
    ensure_initial_team_power_ranking(conn)
    ensure_initial_country_power_ranking(conn)

    update_team_ratings_for_year(conn, evaluation_year)
    update_team_b_for_year(conn, evaluation_year)
    update_country_ratings_for_year(conn, evaluation_year)
    update_country_b_for_year(conn, evaluation_year)

    league_power_cache = compute_league_power(conn, evaluation_year)
    apply_team_season_regression(conn, evaluation_year, league_power_cache)
    apply_country_season_regression(conn, evaluation_year)

    compute_team_power_rankings(conn, evaluation_year)
    compute_country_power_rankings(conn, evaluation_year)
    return evaluation_year + 1


# ══════════════════════════════════════════════════════════════
# 16. 초기(게임 시작연도) 시드 저장 — countries.py/OVR 기반, DB에 실제 저장
# ══════════════════════════════════════════════════════════════

def _country_seed_entries(conn) -> list:
    countries = conn.execute("SELECT name, continent FROM countries").fetchall()
    entries = []
    for name, continent in countries:
        ps, _ = _seed_country_ab(conn, name)
        entries.append(CountryPowerEntry(
            country=name, continent=continent or "", rating=ps, rank=0, prev_rank=None,
            ranking_year=GAME_START_YEAR, evaluation_year=GAME_START_YEAR - 1))
    entries.sort(key=lambda e: e.rating, reverse=True)
    for i, e in enumerate(entries, start=1):
        e.rank = i
    return entries


def ensure_initial_country_power_ranking(conn):
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
        conn.execute("""INSERT INTO country_power_rating(country, a_rating, b_rating, last_updated_year)
                         VALUES(?,?,0,0) ON CONFLICT(country) DO NOTHING""",
                     (e.country, e.rating))
    conn.commit()


def get_country_power_ranking_seed(conn) -> list:
    return _country_seed_entries(conn)


def _team_seed_entries(conn) -> list:
    league_power_cache = compute_league_power(conn, GAME_START_YEAR - 1)
    rows = conn.execute(
        """SELECT t.id, t.name, cn.continent, cn.name, t.current_tier
           FROM teams t JOIN countries cn ON t.country_id = cn.id""").fetchall()
    entries = []
    for team_id, name, continent, country, tier in rows:
        ps, _ = _team_seed_ab(conn, team_id, league_power_cache)
        entries.append(TeamPowerEntry(
            team_id=team_id, team_name=name, continent=continent or "",
            country=country or "", rating=ps, rank=0, prev_rank=None,
            ranking_year=GAME_START_YEAR, evaluation_year=GAME_START_YEAR - 1, tier=tier))
    # OVR로 이미 산출된 PS 기준 정렬 + tier 타이브레이크(1부가 2부보다 위)
    entries.sort(key=lambda e: (-e.rating, e.tier if e.tier else 99, e.team_id))
    for i, e in enumerate(entries, start=1):
        e.rank = i
    return entries


def ensure_initial_team_power_ranking(conn):
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
        conn.execute("""INSERT INTO team_power_rating(team_id, a_rating, b_rating, last_updated_year)
                         VALUES(?,?,0,0) ON CONFLICT(team_id) DO NOTHING""",
                     (e.team_id, e.rating))
    conn.commit()


# ══════════════════════════════════════════════════════════════
# 17. UI 조회 헬퍼 (기존 시그니처 유지)
# ══════════════════════════════════════════════════════════════

def get_team_power_ranking(conn, ranking_year: int, continent: Optional[str] = None,
                            limit: int = 100) -> list:
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


TEAM_POWER_RANKING_TABS = ["전체", "아시아", "유럽", "아프리카", "오세아니아", "북미", "남미"]
_TAB_TO_CONTINENTS = {
    # [2026-08 수정, 신민용 요청: "전체/아시아/유럽/아프리카/아메리카"
    # 5개 탭을 "전체/아시아/유럽/아프리카/오세아니아/북미/남미" 6개
    # 원시 대륙 탭으로 확장 — 더 이상 오세아니아를 아시아에, 북미·남미를
    # 아메리카로 합치지 않는다. countries.continent에 실제 저장된 값과
    # 탭이 1:1로 대응한다.
    "아시아": ["아시아"],
    "유럽": ["유럽"],
    "아프리카": ["아프리카"],
    "오세아니아": ["오세아니아"],
    "북미": ["북미", "북중미"],  # DB에 두 표기가 혼재할 수 있어 둘 다 받는다
    "남미": ["남미"],
}


def get_team_power_ranking_grouped(conn, ranking_year: int, tab: str = "전체",
                                    limit: int = 100) -> list:
    """[2026-08 버그수정, 신민용 리포트: "아시아 탭에서 대륙순위 1등인데
    전년 대비가 전체순위 243등이랑 비교해서 계산된다"] 예전엔 이 함수가
    team_power_rankings.prev_rank(글로벌 순위 기준)를 그대로 돌려주면서
    화면에선 rank만 대륙 범위 순번으로 바꿔치기해서 rank/prev_rank의
    기준이 서로 어긋났다 — 이제 prev_rank도 '같은 대륙 범위 안에서'의
    작년 순번으로 다시 계산해서 돌려준다(기준을 rank와 통일)."""
    ensure_power_ranking_tables(conn)
    ensure_initial_team_power_ranking(conn)
    if tab not in _TAB_TO_CONTINENTS:
        return get_team_power_ranking(conn, ranking_year, continent=None, limit=limit)
    continents = _TAB_TO_CONTINENTS[tab]
    placeholders = ",".join("?" * len(continents))
    rows = conn.execute(
        f"""SELECT p.ranking_year, p.evaluation_year, p.team_id, p.team_name, p.continent,
                   p.country, p.rating, p.rank, t.current_tier
            FROM team_power_rankings p LEFT JOIN teams t ON t.id = p.team_id
            WHERE p.ranking_year=? AND p.continent IN ({placeholders})
            ORDER BY p.rating DESC LIMIT ?""",
        (ranking_year, *continents, limit)).fetchall()
    # 작년(ranking_year-1) 같은 대륙 범위 순번 맵 — 전체 팀(limit 제한 없이)을
    # 대상으로 만들어야 100위 밖에서 올라온 팀도 정확히 잡힌다.
    prev_rows = conn.execute(
        f"""SELECT team_id FROM team_power_rankings
            WHERE ranking_year=? AND continent IN ({placeholders})
            ORDER BY rating DESC""",
        (ranking_year - 1, *continents)).fetchall()
    prev_local_rank = {tid: i + 1 for i, (tid,) in enumerate(prev_rows)}
    return [TeamPowerEntry(team_id=r[2], team_name=r[3], continent=r[4], country=r[5],
                            rating=r[6], rank=r[7], prev_rank=prev_local_rank.get(r[2]),
                            ranking_year=r[0], evaluation_year=r[1], tier=r[8]) for r in rows]


def get_countries_in_tab_group(conn, tab: str) -> list:
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
    ensure_power_ranking_tables(conn)
    ensure_initial_country_power_ranking(conn)
    rows = conn.execute(
        """SELECT ranking_year, evaluation_year, country, continent, rating, rank, prev_rank
           FROM country_power_rankings
           WHERE ranking_year=? ORDER BY rank ASC LIMIT ?""", (ranking_year, limit)).fetchall()
    return [CountryPowerEntry(country=r[2], continent=r[3], rating=r[4], rank=r[5],
                               prev_rank=r[6], ranking_year=r[0], evaluation_year=r[1])
            for r in rows]


def _continent_group_for(continent: str) -> list:
    for continents in _TAB_TO_CONTINENTS.values():
        if continent in continents:
            return continents
    return [continent]


def get_team_power_history(conn, team_id: int) -> list:
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