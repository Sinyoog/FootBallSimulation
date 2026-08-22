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

from database import get_conn, get_game_start_year
from constants import (
    REGION_CUP_NAME, REGION_TO_CONTINENT, CONFEDERATIONS,
    CONTINENT_TO_CONF, CONF_CUP_NAME, EURO_NAME,
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
# [2026-08 신설] 리그는 경기 단위가 아니라 시즌 전체를 한 번에 집계하는
# 근사식(_update_team_a_from_league)을 쓰므로, '경기 1건' 상한(DELTA_CAP)을
# 그대로 씌우면 시즌 전체 성과가 사실상 경기 1건 취급을 받아 지나치게
# 눌린다. 시즌 전체용 상한은 그보다 훨씬 크게(대략 딥런 대륙대항전 한 번
# 우승과 맞먹는 수준) 잡는다. TUNE LATER — 실측 후 조정 대상.
LEAGUE_SEASON_DELTA_CAP = 100.0


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
# [2026-08 신설, 신민용 확정: "국가 파워랭킹이 대회 성적 하나로 너무
# 쉽게 뒤집힌다"] 예전엔 국가도 위 PLACEMENT_BASE_SCORE(클럽과 공용, 우승
# 40)를 그대로 쓰고 COUNTRY_TIER_WEIGHT(월드컵 2.6)만 곱했다 — 그 결과
# 월드컵 준우승 한 번(24×2.6=62.4)이 실측 상위10개국 PS 스프레드(96점)의
# 65%를 즉시 잡아먹어, 통산 우승 0회인 나라도 대회 하나로 즉시 세계
# 4위권까지 튀어오르는 문제가 실측으로 확인됐다. 클럽 쪽(PLACEMENT_BASE_
# SCORE)은 이미 여러 세션에 걸쳐 이 값 기준으로 명문팀 우승비율 등이
# 촘촘히 튜닝돼 있어(prestige_clubs.py 등) 건드리지 않고, 국가 전용
# 배점표를 따로 분리한다 — "B는 A(경기 기반 Elo)를 뒤집는 게 아니라
# 살짝 보정하는 역할"이라는 설계 원칙(신민용 확정)에 맞춰 월드컵 기준
# 최종 B 기여량을 우승25/준우승18/4강13/8강9/16강5/조별탈락0으로 하향
# — COUNTRY_TIER_WEIGHT(월드컵 2.6)를 곱해서 역산한 배점이다. 같은
# 배점표에 다른 대회(아메리카컵/유로/아시안컵/AFCON/지역컵)의 기존
# 가중치를 그대로 곱하면 자동으로 비례 축소된다(예: 유로 우승 12.5,
# 지역컵 최상위 우승도 40→9.6 수준으로 함께 낮아짐) — 대회별로 따로
# 손볼 필요 없이 기존 가중치 서열(월드컵>아메리카컵>유로/아시안컵/AFCON
# >지역컵)이 그대로 유지된다.
COUNTRY_PLACEMENT_BASE_SCORE = {
    "champion": 9.6, "runner_up": 6.9, "semifinal": 5.0,
    "quarterfinal": 3.5, "round16": 1.9, "group_exit": 0.0,
}

# 리그 순위 보너스는 절대순위가 아니라 백분위(최종순위/참가팀수) 기반.
# [2026-08 v3.2] 실제 밴드 값은 아래 league_placement_bonus() 함수 안에
# if/elif로 직접 명시(mutually exclusive 보장) — 이 상수 테이블은 더 이상
# 쓰지 않는다(하위권 마이너스 밴드까지 추가되며 함수 안에 직접 넣는 쪽이
# 더 명확해짐).

# [2026-08 신설, 신민용 버그 리포트: "2부리그 1등한 게 1부리그 1등급으로
# 오는 거 같다"] league_placement_bonus/리그 A레이어 가중치가 리그의
# 부(tier)를 전혀 안 보고 있었다 — 같은 백분위(예: 1위)면 1부든 6부든
# 배점이 완전히 동일했다. 부가 낮을수록(하위 리그) 배점·A레이어 가중치를
# 깎는다. 1부=100%, 2부=65%, 3부=45%, 4부=30%, 5부 이하=20% 바닥.
LEAGUE_TIER_WEIGHT = {1: 1.0, 2: 0.65, 3: 0.45, 4: 0.30}
LEAGUE_TIER_WEIGHT_FLOOR = 0.20


def league_tier_weight(tier: Optional[int]) -> float:
    if not tier or tier <= 0:
        return 1.0
    return LEAGUE_TIER_WEIGHT.get(tier, LEAGUE_TIER_WEIGHT_FLOOR)


# [2026-08 신설, 신민용 버그 리포트: "챔스 우승했는데 강등당해도 파워랭킹이
# 별로 안 떨어진다 — 승리(우승)에 대한 보정만 있고 패배(강등)에 대한 보정이
# 없는 게 설계 미숙이다"] 강등 자체에 레이어B 페널티가 전혀 없었다(리그
# 순위 보너스는 잘해야 0점이지, 못하면 마이너스가 되는 구조가 아니었음).
# 강등 1단계당, 그리고 강등 직전 소속 리그의 등급(league_tier_weight)이
# 높을수록(명문 리그일수록 강등이 더 뼈아픔) 더 크게 깎는다.
RELEGATION_BASE_PENALTY = -14.0  # 강등 1단계당 기본 페널티(리그가중치 곱하기 전)


# [2026-08 v3.2 재설계, GPT 피드백 반영: "1위는 상위10% 밴드와도 겹치니
# if/elif로 반드시 배타 처리해야 한다" + "하위권도 이제 마이너스가 있어야
# 한다"] 리그 순위 보너스 — 백분위 기반, 우승 하나만 챙기던 예전과 달리
# 이제 중~하위권도 세분화된다. 백분위 비교는 <= 부등호라 자동으로 ceil()과
# 동일한 효과를 낸다(예: 18팀 리그 상위10% 컷오프는 1.8 → 2위까지 포함).
def league_placement_bonus(final_rank: int, n_teams: int) -> float:
    if n_teams <= 0:
        return 0.0
    if final_rank == 1:
        return 20.0   # mutually exclusive — 아래 상위10% 밴드와 안 겹침
    percentile = final_rank / n_teams
    if percentile <= 0.10:
        return 10.0
    if percentile <= 0.20:
        return 6.0
    if percentile <= 0.25:
        return 3.0
    if percentile <= 0.50:
        return 0.0
    if percentile <= 0.75:
        return -2.0
    if percentile <= 0.90:
        return -5.0
    return -8.0   # 91~100% — 강등팀도 이 밴드에 자연히 포함(별도 행 없음,
                  # 실제 강등 이벤트 페널티는 RELEGATION_BASE_PENALTY로 별개 처리)


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
    # [2026-08 v3.2 신설, 리그 상대강도] 그 시즌에 실제로 만난 상대들의
    # PS를 승/무/패별로 누적 — "5승 5패"가 강팀 상대인지 약팀 상대인지
    # 구분하기 위함(_update_team_a_from_league 참고). match_results_archive가
    # 예전에 무한정 쌓여서(21시즌차 346만행/195MB) 저장 지연을 일으켰던
    # 사고를 반복하지 않기 위해, 이 테이블은 "그 시즌 계산용 임시 데이터"로
    # 취급한다 — run_year_end_power_ranking_update가 그 시즌 계산을 끝내면
    # 바로 해당 season 행을 삭제한다(영구 이력 아님).
    c.execute("""CREATE TABLE IF NOT EXISTS team_season_opp_strength(
        team_id INTEGER, season INTEGER,
        win_opp_ps_sum REAL DEFAULT 0, win_n INTEGER DEFAULT 0,
        draw_opp_ps_sum REAL DEFAULT 0, draw_n INTEGER DEFAULT 0,
        loss_opp_ps_sum REAL DEFAULT 0, loss_n INTEGER DEFAULT 0,
        PRIMARY KEY(team_id, season))""")
    conn.commit()


# ══════════════════════════════════════════════════════════════
# 8. 레이팅 읽기/쓰기 헬퍼 (A/B 분리)
# ══════════════════════════════════════════════════════════════

# [2026-08 신설, 성능] run_year_end_power_ranking_update 한 번(연 1회
# 배치)이 진행되는 동안 같은 team_id의 a_rating/b_rating을 경기 결과·대회
# 성적·리그레션 단계에서 반복해서 읽고 쓴다(실측: 10시즌 헤드리스
# cProfile — _get_team_ab만 이 배치 한 번에 117,524회 호출, team 수
# 11,329개 대비 팀당 평균 10회 이상 중복 조회). get_team_ps_map()이
# "시즌 내내 유효한 캐시"(위 주석 참고)로 이미 이 패턴을 쓰고 있는데,
# 정작 그 값을 실제로 갱신하는 이 배치 자체에는 캐시가 없었다.
# 이 배치는 단일 스레드로 순서대로만 실행되고(동시에 두 시즌이 같이
# 도는 경우 없음), 아래 쓰기 함수들(_add_team_a/_add_team_b/
# apply_team_season_regression의 직접 UPDATE)이 전부 DB에 그대로 쓰면서
# 캐시도 같이 갱신하므로(write-through) DB는 항상 즉시 최신 상태 —
# 캐시는 순전히 "방금 그 배치 안에서 이미 읽었거나 쓴 값을 또 SELECT
# 하지 않기 위함"이다. run_year_end_power_ranking_update 시작 시 매번
# 비워서(다음 시즌엔 무조건 새로 읽음) 시즌 간 데이터가 섞일 일이 없다.
_team_ab_cache: dict = {}


def _get_team_ab(conn, team_id: int):
    cached = _team_ab_cache.get(team_id)
    if cached is not None:
        return cached
    row = conn.execute(
        "SELECT a_rating, b_rating FROM team_power_rating WHERE team_id=?", (team_id,)
    ).fetchone()
    result = (row[0], row[1]) if row else (0.0, 0.0)
    _team_ab_cache[team_id] = result
    return result


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
    _team_ab_cache[team_id] = (a + delta, b)


def _add_team_b(conn, team_id: int, delta: float, year: int):
    a, b = _get_team_ab(conn, team_id)
    conn.execute("""INSERT INTO team_power_rating(team_id, a_rating, b_rating, last_updated_year)
                     VALUES(?,?,?,?)
                     ON CONFLICT(team_id) DO UPDATE SET
                        b_rating=excluded.b_rating, last_updated_year=excluded.last_updated_year""",
                 (team_id, a, b + delta, year))
    _team_ab_cache[team_id] = (a, b + delta)
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
    """[2026-08 수정, 신민용 확정: "월드컵+대륙컵을 연달아 우승해도 B가
    무한히 안 쌓이게"] 상한(COUNTRY_B_MAX)만 건다 — 하한은 그대로 열어둔다
    (예선탈락 페널티(QUALIFIER_FAIL_BASE_PENALTY)가 그 해 b_rating을
    일시적으로 마이너스로 만들 수는 있게 두고, 0 바닥은 다음 해
    apply_country_season_regression의 _decay_b가 처리)."""
    from constants import COUNTRY_B_MAX
    a, b = _get_country_ab(conn, country)
    new_b = min(COUNTRY_B_MAX, b + delta)
    conn.execute("""INSERT INTO country_power_rating(country, a_rating, b_rating, last_updated_year)
                     VALUES(?,?,?,?)
                     ON CONFLICT(country) DO UPDATE SET
                        b_rating=excluded.b_rating, last_updated_year=excluded.last_updated_year""",
                 (country, a, new_b, year))


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


def get_team_ps_map(conn, team_ids) -> dict:
    """[2026-08 v3.2 신설] 상대강도 누적용 — 여러 팀 PS를 한 번에 조회해
    dict로 돌려준다(game_engine.py의 주간 매치 시뮬 루프가 매치마다 개별
    SELECT 하지 않고 이 dict를 세션 캐시로 재사용하기 위함 — 기존
    _team_ovr_cache와 동일한 패턴). 팀 PS는 연 1회 연도전환 배치에서만
    갱신되므로(리그 경기는 시즌 내내 이 값에 영향을 못 줌), 같은 시즌
    동안은 이 캐시가 계속 유효하다."""
    if not team_ids:
        return {}
    placeholders = ",".join("?" * len(team_ids))
    rows = conn.execute(
        f"SELECT team_id, a_rating, b_rating FROM team_power_rating "
        f"WHERE team_id IN ({placeholders})", list(team_ids)).fetchall()
    found = {r[0]: (r[1] or 0.0) + (r[2] or 0.0) for r in rows}
    # 시드가 아직 한 번도 안 된 팀(이론상 거의 없음, 게임 시작 시 전 팀
    # 시드됨)은 team_power_rating 행 자체가 없을 수 있어 폴백 seed 계산.
    missing = [tid for tid in team_ids if tid not in found]
    for tid in missing:
        ps, _ = _team_seed_ab(conn, tid, {})
        found[tid] = ps
    return found


def flush_opp_strength(conn, season: int, acc: dict):
    """[2026-08 v3.2 신설] game_engine.py의 주간 리그 매치 루프가
    acc: {team_id: [win_sum,win_n,draw_sum,draw_n,loss_sum,loss_n]}
    형태로 파이썬 dict에 모아둔 걸 한 번의 executemany UPSERT로 반영한다
    (경기당 개별 쓰기 없음 — _flush_team_rec과 동일한 배치 관례)."""
    if not acc:
        return
    conn.executemany(
        """INSERT INTO team_season_opp_strength
               (team_id, season, win_opp_ps_sum, win_n, draw_opp_ps_sum, draw_n,
                loss_opp_ps_sum, loss_n)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(team_id, season) DO UPDATE SET
               win_opp_ps_sum=win_opp_ps_sum+excluded.win_opp_ps_sum,
               win_n=win_n+excluded.win_n,
               draw_opp_ps_sum=draw_opp_ps_sum+excluded.draw_opp_ps_sum,
               draw_n=draw_n+excluded.draw_n,
               loss_opp_ps_sum=loss_opp_ps_sum+excluded.loss_opp_ps_sum,
               loss_n=loss_n+excluded.loss_n""",
        [(tid, season, v[0], v[1], v[2], v[3], v[4], v[5]) for tid, v in acc.items()])
    conn.commit()


def record_league_opp_strength(acc: dict, home_id, away_id, home_ps, away_ps, outcome):
    """[2026-08 v3.2 신설] game_engine.py 쪽에서 _accum_team_rec과 나란히
    호출 — 결과 하나를 양 팀 관점에서 acc(파이썬 dict, DB접근 없음)에
    누적한다. outcome은 game_engine._roll_outcome()이 돌려주는 값과 동일
    (\"home\"/\"away\"/그 외=무승부)."""
    def _get(tid):
        if tid not in acc:
            acc[tid] = [0.0, 0, 0.0, 0, 0.0, 0]  # win_sum,win_n,draw_sum,draw_n,loss_sum,loss_n
        return acc[tid]

    h = _get(home_id); a = _get(away_id)
    if outcome == "home":
        h[0] += away_ps; h[1] += 1
        a[4] += home_ps; a[5] += 1
    elif outcome == "away":
        h[4] += away_ps; h[5] += 1
        a[0] += home_ps; a[1] += 1
    else:
        h[2] += away_ps; h[3] += 1
        a[2] += home_ps; a[3] += 1


def _update_team_a_from_league(conn, evaluation_year: int):
    """리그는 경기 단위 대신 league_season_standings 집계를 '리그 평균
    상대'로 근사(TUNE LATER, 상단 docstring 참고). 단계가중치는 1.0 고정.

    [2026-08 재설계, 신민용 버그 리포트: "우승(대회 성적)에 대한 보정만
    잘 되고, 패배·부진(특히 강등급 성적)에 대한 보정이 너무 약하다"]
    예전 공식은 raw = k*w*(actual-expected)/n_games * min(n_games,10)/10
    이었는데, 이건 "한 시즌 전체 성과"를 사실상 "경기 1개 분량"으로
    압축해버리는 구조였다 — 게다가 그 결과에 경기당 상한(DELTA_CAP=25,
    원래 '개별 경기 1건'의 변동폭 상한이지 시즌 전체 상한이 아님)을 그대로
    씌워서, 시즌 내내 최하위(강등권)를 했든 우승을 했든 리그에서 받을 수
    있는 A레이어 변동폭이 최대 ±25로 묶여 있었다. 반면 대륙대항전 한 번
    우승하면 레이어A만으로도 여러 경기(조별~결승)가 각각 최대 ±25~40씩
    누적되고 레이어B 우승 보너스(40×대회가중치, 최대 70+)까지 더해져서
    수백 점이 오른다 — 그래서 "우승은 잘 반영되는데 강등은 안 반영된다"는
    비대칭이 생겼다(챔스 우승하고 같은 해 강등해도 PS가 거의 안 떨어지는
    버그의 근본 원인). 시즌을 실제 매치 단위로 순차 반영하면 정확하겠지만
    DB 부하 문제로 이 근사식을 유지해야 하므로, 대신 (a) n_games로 나눴다가
    다시 곱하는 이중 압축을 없애 시즌 전체 승점 격차(actual-expected)가
    그대로 델타에 실리게 하고, (b) 상한을 '경기 1건' 상한이 아니라 시즌
    전체용으로 별도로 크게(LEAGUE_SEASON_DELTA_CAP) 잡는다. (c) 리그
    부(tier)가 낮을수록(하위 리그) league_tier_weight로 가중치를 깎는다.

    [2026-08 v3.2 재설계, 상대강도 반영] "기대승점"의 기준을 리그 전체
    평균(league_avg)에서 그 팀이 그 시즌 실제로 만난 상대들의 평균 PS
    (avg_opponent_ps)로 바꾼다 — 5승5패라도 "강팀 5승/약팀 5패"와
    "약팀 5승/강팀 5패"는 전혀 다른 시즌인데 리그 평균 하나로는 이
    차이가 사라졌다(신민용+GPT 합의). team_season_opp_strength가 그
    시즌 매주 실시간으로 쌓아둔 값을 쓰고, 정상적으로는 나오면 안 되는
    예외(0경기)만 조용히 league_avg로 폴백하지 않고 경고 로그를 남긴다."""
    rows = conn.execute(
        """SELECT s.team_id, s.wins, s.draws, s.losses, l.tier, s.season
           FROM league_season_standings s JOIN leagues l ON s.league_id = l.id
           WHERE s.year=?""", (evaluation_year,)).fetchall()
    if not rows:
        return
    team_ids = [r[0] for r in rows]
    season = rows[0][5]
    ratings = {tid: _get_team_rating(conn, tid) for tid in team_ids}
    league_avg = sum(ratings.values()) / len(ratings)
    opp_rows = conn.execute(
        """SELECT team_id, win_opp_ps_sum, win_n, draw_opp_ps_sum, draw_n,
                  loss_opp_ps_sum, loss_n
           FROM team_season_opp_strength WHERE season=?""", (season,)).fetchall()
    opp_by_team = {r[0]: r[1:] for r in opp_rows}
    base_w = TEAM_COMPETITION_WEIGHT["league"]
    for team_id, wins, draws, losses, tier, _season in rows:
        rating = ratings[team_id]
        grade = grade_for_ps(rating)
        n_games = (wins or 0) + (draws or 0) + (losses or 0)
        if n_games == 0:
            continue
        os_row = opp_by_team.get(team_id)
        opp_n = (os_row[1] + os_row[3] + os_row[5]) if os_row else 0
        if os_row and opp_n > 0:
            opp_sum = os_row[0] + os_row[2] + os_row[4]
            avg_opponent_ps = opp_sum / opp_n
        else:
            print(f"[WARN][power_ranking] team_id={team_id} season={season}: "
                  f"리그 상대강도 데이터 0건 — league_avg_ps로 폴백")
            avg_opponent_ps = league_avg
        avg_grade = grade_for_ps(avg_opponent_ps)
        w = base_w * league_tier_weight(tier)
        actual_points = (wins or 0) * 1.0 + (draws or 0) * 0.5
        e = expected_score(rating, avg_opponent_ps)   # [2026-08 v3.2] 두 번째 인자만
                                                        # league_avg → avg_opponent_ps로
                                                        # 교체, expected_score() 함수
                                                        # 자체는 손대지 않음(GPT 합의사항)
        expected_points = e * n_games
        k_match = (k_for_grade(grade) + k_for_grade(avg_grade)) / 2.0
        raw = k_match * w * (actual_points - expected_points)
        delta = max(-LEAGUE_SEASON_DELTA_CAP, min(LEAGUE_SEASON_DELTA_CAP, raw))
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
    # 1) 국내리그 순위 보너스(백분위 기반, 리그 부(tier)로 가중치 조정) + 연속우승 감쇠
    rows = conn.execute(
        """SELECT l.id, l.tier, s.team_id, s.wins, s.draws, s.losses
           FROM league_season_standings s JOIN leagues l ON s.league_id = l.id
           WHERE s.year=?""", (evaluation_year,)).fetchall()
    by_league = {}
    tier_of_league = {}
    for league_id, tier, team_id, wins, draws, losses in rows:
        pts = (wins or 0) * 3 + (draws or 0)
        by_league.setdefault(league_id, []).append((team_id, pts))
        tier_of_league[league_id] = tier
    for league_id, standings in by_league.items():
        standings.sort(key=lambda x: x[1], reverse=True)
        n = len(standings)
        champion_id = standings[0][0] if standings else None
        decay = _apply_team_league_streak(conn, league_id, champion_id) if champion_id else 1.0
        tier_w = league_tier_weight(tier_of_league.get(league_id))
        for rank, (team_id, _pts) in enumerate(standings, start=1):
            bonus = league_placement_bonus(rank, n)
            # [2026-08 v3.2] 하위권 밴드가 마이너스를 돌려주므로 더 이상
            # "bonus<=0이면 스킵"하면 안 된다 — 정확히 0(중위권, 26~50%)일
            # 때만 스킵(어차피 더할 게 없음), 마이너스는 그대로 적용해야
            # "우승만 보정되고 부진은 반영 안 되는" 예전 비대칭이 안 생긴다.
            if bonus == 0:
                continue
            if rank == 1:
                bonus *= decay
            _add_team_b(conn, team_id, bonus * TEAM_COMPETITION_WEIGHT["league"] * tier_w,
                        evaluation_year)

    # 1b) [2026-08 신설, 신민용 버그 리포트: "우승 보정만 있고 강등(패배)
    # 보정이 없다"] 강등은 그 자체로 레이어B 페널티 — 강등 단계 수 ×
    # 강등 직전 리그의 tier_weight(명문 리그일수록 더 아프게)만큼 깎는다.
    # 이게 있어야 "챔스 우승 + 강등"처럼 성적이 완전히 엇갈린 시즌에도
    # PS가 실제로 크게 떨어질 수 있다(우승 보너스가 강등 페널티를 압도할
    # 수는 있지만, 최소한 반대 방향 힘 자체는 존재해야 한다).
    relegations = conn.execute(
        """SELECT team_id, from_tier, to_tier FROM promotion_log
           WHERE year=? AND to_tier > from_tier AND team_id > 0""",
        (evaluation_year,)).fetchall()
    for team_id, from_tier, to_tier in relegations:
        levels = max(1, (to_tier or 0) - (from_tier or 0))
        penalty = RELEGATION_BASE_PENALTY * levels * league_tier_weight(from_tier)
        _add_team_b(conn, team_id, penalty, evaluation_year)

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
            base = COUNTRY_PLACEMENT_BASE_SCORE[tier]
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


def _regress_a(a: float, seed_ps: float, convergence: float) -> float:
    """[2026-08 v3.2 재설계, GPT 피드백: "ratio_a는 시드를 다시 나누는
    구식 잔재라 빼는 게 맞다"] A(현재 실력)만 스쿼드 수준(seed_ps, 시드는
    전량 A로 잡히므로 그대로 기준점으로 쓸 수 있음) 쪽으로 회귀시킨다.
    B는 여기서 손대지 않고 _decay_b()가 완전히 별도로 처리한다."""
    return a * (1 - convergence) + seed_ps * convergence


def _decay_b(b: float, decay_rate: float) -> float:
    """[2026-08 v3.2 재설계] B(과거 업적)는 seed와 무관하게 그냥 이
    비율만큼 매년 옅어진다. 0 미만으로는 안 내려간다(GPT 지적: "누적
    업적 자산"이 개념적으로 음수가 되면 안 됨 — 팀이 정말 약하다는 사실은
    A쪽에서 계속 반영되므로 B에서 중복 표현할 필요가 없다). 시즌 중
    하위권/강등 페널티가 이번 해 b_rating을 일시적으로 마이너스로 만들 수는
    있지만(그건 "이번 시즌 성적 변동"이라 정상), 다음 해 이 감쇠 계산에서
    는 항상 0으로 바닥을 친다."""
    return max(0.0, b * (1 - decay_rate))


def apply_team_season_regression(conn, evaluation_year: int, league_power_cache: dict):
    from constants import CLUB_B_DECAY_RATE
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
        new_a = _regress_a(a, seed_ps, convergence)
        new_b = _decay_b(b, CLUB_B_DECAY_RATE)
        conn.execute("""UPDATE team_power_rating SET a_rating=?, b_rating=?, last_updated_year=?
                         WHERE team_id=?""", (new_a, new_b, evaluation_year, team_id))
        # [2026-08 신설] 이 UPDATE는 _add_team_a/_add_team_b를 거치지 않는
        # 직접 쓰기라, 위 _team_ab_cache write-through 대상에서 빠진다 —
        # 여기서 직접 갱신 안 하면 이 함수 뒤에 오는 compute_team_power_
        # rankings()가 _get_team_rating()으로 이 팀을 다시 읽을 때 방금
        # 리그레션 적용 전(stale) 값을 캐시에서 돌려주게 된다.
        _team_ab_cache[team_id] = (new_a, new_b)
    conn.commit()


def _country_last_intl_year(conn, country: str, upto_year: int) -> Optional[int]:
    row = conn.execute(
        """SELECT MAX(t.year) FROM intl_entries e JOIN intl_tournaments t ON e.tournament_id=t.id
           WHERE e.country=? AND t.year<=?""", (country, upto_year)).fetchone()
    return row[0] if row and row[0] else None


def apply_country_season_regression(conn, evaluation_year: int):
    from constants import COUNTRY_B_DECAY_RATE
    countries = conn.execute("SELECT name FROM countries").fetchall()
    for (country,) in countries:
        a, b = _get_country_ab(conn, country)
        seed_ps, _ = _seed_country_ab(conn, country)
        last_year = _country_last_intl_year(conn, country, evaluation_year)
        if last_year is None:
            convergence = 0.45
        else:
            gap = evaluation_year - last_year
            convergence = {0: 0.15, 1: 0.25, 2: 0.35}.get(gap, 0.45 if gap >= 3 else 0.15)
        new_a = _regress_a(a, seed_ps, convergence)
        new_b = _decay_b(b, COUNTRY_B_DECAY_RATE)
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
    # [2026-08 신설, 성능] 이번 배치(연 1회) 동안만 유효한 _team_ab_cache를
    # 매번 새로 비운다 — 위 _team_ab_cache 선언부 설명 참고.
    _team_ab_cache.clear()

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

    # [2026-08 v3.2 신설] team_season_opp_strength는 이번 시즌 계산 전용
    # 임시 데이터 — 위 계산(특히 update_team_ratings_for_year의 리그
    # 상대강도 조회)이 전부 성공적으로 끝나고 스냅샷까지 저장된 뒤에만
    # 지운다(GPT 지적: 계산 실패 시 임시데이터만 사라지는 상태 불일치를
    # 피하기 위해 "계산 성공 → 삭제" 순서를 지킴). match_results_archive가
    # 예전에 무한정 쌓였던 사고를 이 테이블에서 반복하지 않기 위한 필수
    # 조치 — 절대 생략하지 말 것.
    season_row = conn.execute(
        "SELECT DISTINCT season FROM league_season_standings WHERE year=?",
        (evaluation_year,)).fetchone()
    if season_row:
        conn.execute("DELETE FROM team_season_opp_strength WHERE season=?", (season_row[0],))
        conn.commit()

    return evaluation_year + 1


# ══════════════════════════════════════════════════════════════
# 16. 초기(게임 시작연도) 시드 저장 — countries.py/OVR 기반, DB에 실제 저장
# ══════════════════════════════════════════════════════════════

def _country_seed_entries(conn) -> list:
    _gsy = get_game_start_year()
    countries = conn.execute("SELECT name, continent FROM countries").fetchall()
    entries = []
    for name, continent in countries:
        ps, _ = _seed_country_ab(conn, name)
        entries.append(CountryPowerEntry(
            country=name, continent=continent or "", rating=ps, rank=0, prev_rank=None,
            ranking_year=_gsy, evaluation_year=_gsy - 1))
    entries.sort(key=lambda e: e.rating, reverse=True)
    for i, e in enumerate(entries, start=1):
        e.rank = i
    return entries


def ensure_initial_country_power_ranking(conn):
    ensure_power_ranking_tables(conn)
    # [2026-08 v3.3 버그수정, 신민용 리포트: "파워랭킹 첫 연도가 2000
    # 고정이라 1999년으로 시작하면 그해엔 세계 랭킹 자체가 없다"] 이
    # 시드는 "게임이 실제로 시작한 연도"에 맞춰야 하는데, 여기서 계속
    # GAME_START_YEAR(constants.py의 고정 상수, 항상 2000)를 썼다 —
    # 커스텀 시작 연도(예: 1999)를 골라도 시드는 여전히 2000년으로
    # 저장돼서, 실제 게임이 도는 1999년엔 world_power_rankings에 그
    # 해당 연도 행 자체가 없었다. database.get_game_start_year()(플레이어가
    # 실제로 고른 시작 연도, meta 테이블에 저장됨 — 안 골랐으면 기존처럼
    # GAME_START_YEAR로 폴백)로 교체한다.
    _gsy = get_game_start_year()
    exists = conn.execute(
        "SELECT 1 FROM country_power_rankings WHERE ranking_year=? LIMIT 1",
        (_gsy,)).fetchone()
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
    _gsy = get_game_start_year()
    league_power_cache = compute_league_power(conn, _gsy - 1)
    rows = conn.execute(
        """SELECT t.id, t.name, cn.continent, cn.name, t.current_tier
           FROM teams t JOIN countries cn ON t.country_id = cn.id""").fetchall()
    entries = []
    for team_id, name, continent, country, tier in rows:
        ps, _ = _team_seed_ab(conn, team_id, league_power_cache)
        entries.append(TeamPowerEntry(
            team_id=team_id, team_name=name, continent=continent or "",
            country=country or "", rating=ps, rank=0, prev_rank=None,
            ranking_year=_gsy, evaluation_year=_gsy - 1, tier=tier))
    # OVR로 이미 산출된 PS 기준 정렬 + tier 타이브레이크(1부가 2부보다 위)
    entries.sort(key=lambda e: (-e.rating, e.tier if e.tier else 99, e.team_id))
    for i, e in enumerate(entries, start=1):
        e.rank = i
    return entries


def ensure_initial_team_power_ranking(conn):
    ensure_power_ranking_tables(conn)
    _gsy = get_game_start_year()
    exists = conn.execute(
        "SELECT 1 FROM team_power_rankings WHERE ranking_year=? LIMIT 1",
        (_gsy,)).fetchone()
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
    return row[0] if row and row[0] else get_game_start_year()


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