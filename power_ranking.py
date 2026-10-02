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
# [2026-10 병역 시스템 2단계] 군데스리가는 팀·리그·국가 파워랭킹에 전혀 반영하지 않는다.
from military_service import (is_military_team as _is_mil_team, is_military_league as _is_mil_league,
                              is_military_country as _is_mil_country, drop_military_teams as _drop_mil_teams,
                              drop_military_leagues as _drop_mil_leagues,
                              drop_military_countries as _drop_mil_countries)
from constants import (
    REGION_CUP_NAME, REGION_TO_CONTINENT, CONFEDERATIONS,
    CONTINENT_TO_CONF, CONF_CUP_NAME, EURO_NAME,
)


# [2026-09 신설, 성능 진단] ai_lifecycle._perf_log와 완전히 동일한 패턴 —
# 연도전환 로그에는 지금까지 "파워랭킹 1.63s" 한 줄만 찍혔고, 그 안의 9개
# 하위 단계(팀 A/B값 갱신, 국가 A/B값 갱신, 리그파워 계산, 시즌회귀 2종,
# 팀/국가 랭킹 산출) 중 어디가 무거운지는 전혀 알 수 없었다 — 이 파일
# 하나만 블랙박스로 남아 있던 상태. print는 그대로 두고 같은 줄을
# live_sim.log에도 남긴다(순환 import 회피를 위해 지연 import) — 계측
# 출력만 늘 뿐 계산 결과에는 전혀 영향이 없다.
def _perf_log(msg):
    print(msg)
    try:
        from game_engine import _live_debug
        _live_debug(msg)
    except Exception:
        pass


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
# 1.5. 부진 스트릭 — "관성 붕괴" 장치 (2026-09 신설, 신민용+GPT 설계
#      "올라가는 건 느리게, 몇 시즌 연속 부진하면 그때부턴 빠르게")
# ══════════════════════════════════════════════════════════════
# 설계 원칙: k_for_grade는 "기본 관성"만 담당한다(등급 높을수록 경기
# 하나에 덜 흔들림 — 그대로 유지). 거기에 "최근 몇 시즌 연속으로 등급
# 대비 기대 이하 성적"이라는 별도 축을 얹어서, 관성 자체가 무너지는
# 효과를 낸다 — 한 시즌 못한 건 무시하되(streak 1까지는 배율 없음),
# 2~3시즌 연속이면 K가 커지기 시작해서 4~5시즌 연속이면 사실상 최하위
# 등급(F)만큼 출렁이게 된다. 반대로 상승 쪽엔 이 스트릭을 절대 적용하지
# 않는다(신민용 명시적 요청 — "약팀이 한 시즌 잘했다고 바로 강팀 취급
# 하면 안 됨", 상승은 계속 느리게).
#
# 팀은 "리그 순위 백분위"로, 국가는 "대회 도달 스테이지"로 판단 기준이
# 다르므로 각각 별도 표를 둔다(둘 다 update_team_b_for_year/update_
# country_b_for_year에서 매 시즌 1회 판정 후 DB에 저장 — 다음 시즌
# 레이어A 계산(match_delta)이 이 값을 읽어서 K를 키운다).

# 팀: 이 백분위(순위/참가팀수)까지는 "기대 이하"로 안 본다. 이걸 넘어서면
# (더 낮은 순위) 그 시즌은 부진으로 집계.
EXPECTED_PERCENTILE_CEILING = {
    "SS": 0.15, "S": 0.25, "A": 0.40, "B": 0.55,
    "C": 0.70, "D": 0.85, "E": 0.95, "F": 1.01,
}
# 국가: 대회 도달 스테이지를 서수로 변환(0=조별탈락, -1=예선 탈락으로
# 본선行 자체를 못 함). 이 서수 미만이면(더 얕은 라운드) 그 해는 부진.
_STAGE_TIER_ORDINAL = {
    "group_exit": 0, "round16": 1, "quarterfinal": 2,
    "semifinal": 3, "runner_up": 4, "champion": 5,
}
COUNTRY_EXPECTED_TIER_FLOOR = {
    "SS": 3, "S": 2, "A": 1, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0,
}

# 부진 스트릭 → 레이어A(K) 배율. streak 0~1은 배율 없음("한 시즌 못했다고
# 안 무너짐"), 2시즌째부터 서서히 커져서 5시즌째부터 고정 상한.
STREAK_K_MULTIPLIER = {0: 1.0, 1: 1.0, 2: 1.25, 3: 1.5, 4: 2.0, 5: 2.75}
# 부진 스트릭 → 레이어B 하락 페널티 배율(상승 쪽 보너스엔 절대 적용 안 함).
STREAK_PENALTY_MULTIPLIER = {1: 1.0, 2: 1.2, 3: 1.5, 4: 1.8, 5: 2.0}
# 국가 전용 — "그 등급치고 못했다"는 신호에 대한 신규 소규모 페널티.
# 기존 COUNTRY_PLACEMENT_BASE_SCORE(우승~조별탈락 배점표)는 이미 여러
# 세션에 걸쳐 튜닝된 값이라 그대로 두고 건드리지 않는다(3.6 배점표
# 근처 주석 참고) — 그 표가 못 잡아내는 "기대 이하" 신호만 이 값으로
# 별도로 추가한다.
COUNTRY_UNDERPERFORM_BASE_PENALTY = -4.0


def _streak_bucket(streak: int) -> int:
    return max(0, min(int(streak or 0), 5))


def effective_k_for_grade(grade: str, streak: int) -> float:
    """등급 기본 K에 부진 스트릭 배율을 곱하되, 최종값은 최하위 등급(F)의
    기본 K를 넘지 않게 상한을 둔다(신민용 명시적 요청 — "K가 무작정
    널뛰기하면 안 된다", SS팀이 몰락해도 딱 F팀 수준까지만 출렁임)."""
    base = k_for_grade(grade)
    mult = STREAK_K_MULTIPLIER[_streak_bucket(streak)]
    return min(k_for_grade("F"), base * mult)


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

# [2026-08 신설, 신민용+GPT 확정 — 실측(헤드리스 실제 시즌 시뮬레이션)으로
# 확인된 문제: "게임 시작 첫 시즌(2000년)에 레알/바르사/바이에른/도르트문트/
# 마르세유 등 강팀 거의 전부가 실제 성적과 무관하게 A:league -100(하한
# 캡)에 몰린다"] 처음엔 첫 시즌만 캡을 완화(40)하는 방향으로 시도했으나,
# 실제 매치엔진으로 1시즌을 통째로 돌려 raw(클램프 전 원본값)를 찍어보니
# 레알 -152.8/바이에른 -144.4/마르세유 -139.2/도르트문트 -98.3/바르사
# -55.6로 팀마다 값은 다르지만 전부 -40보다도 훨씬 더 마이너스라, 캡을
# 아무리 낮춰도 다들 그 캡 하나로 다시 뭉쳐버림을 확인했다(진단 결론:
# 캡 문제가 아니라 기대승률 계산 자체의 스케일 불일치).
#
# 원인: 초기 시드값(1400+(OVR-60)×30)이 만드는 Elo 격차가, 실제 매치
# 시뮬레이션 엔진(스탯/club_strength 기반, 전혀 다른 스케일)이 실제로
# 만들어내는 팀간 승률 격차보다 훨씬 가파르다 — 예: 레알 시드 기준
# "기대승점 36.2/38"(승률 95%)를 요구했는데 실제 매치엔진 승률은 62%
# (23.5/38)였다. 이건 레알이 그 시즌에 유난히 못한 게 아니라, 시드
# 공식이 "이 정도는 이겨야 정상"이라고 잡은 기준 자체가 실제 엔진의
# 승률 분포보다 원래 너무 높았던 것.
#
# 수정 방향(신민용+GPT 확정): team_ps 자체·grade_for_ps()·챔스/컵 Elo·
# 매치엔진·_regress_a는 전혀 건드리지 않는다 — 딱 이 함수(리그 A레이어)
# 안에서 expected_score에 넘기는 "레이팅 격차"만 압축해서, 실제 매치엔진의
# 완만한 승률 분포에 맞춘다. 목표는 "기대승점을 실제승점과 똑같이 맞추는
# 것"이 아니라(그러면 Elo가 매치엔진을 그냥 베끼는 꼴이 됨) "강팀이 우승권
# 성적을 냈는데도 기대치가 비현실적으로 높아서 하한에 박히는 현상만
# 없애고, 강팀-약팀 간 실력차 반영 자체는 유지하는 것". 압축률은 실측
# (아래 검증 결과 참고)으로 확정 — 100%(무압축)/75%/50%/40% 네 후보를
# 동일 시드·동일 시즌으로 비교해 40%가 가장 균형 잡힌 결과를 냈다.
#
# 이 압축을 적용한 뒤로는 LEAGUE_SEASON_DELTA_CAP도 다시 원래 ±100
# 그대로 쓴다(위 실험용 INITIAL_SEASON_NEGATIVE_CAP=40은 진단 전용이었고
# 최종 구현에서는 제거 — gap 압축과 낮은 캡을 동시에 걸면 이중 억제가
# 된다는 지적에 따름).
#
# [실측 확정치] 동일 시드(random.seed(999))·동일 2000시즌으로 100%/75%/
# 50%/40%/30% 다섯 후보를 실제 매치엔진으로 비교(레알/바르사/바이에른/
# 도르트문트/마르세유 5팀):
#   100%: 전원 -100 캡에 그대로 몰림(5/5)
#    75%: 4/5 여전히 캡에 몰림
#    50%: 1/5 캡에 몰림(레알-83.8/바르사-40.8/바이에른-67.4/도르트문트
#         -100.0(캡)/마르세유-97.8)
#    40%: 캡 걸리는 팀 없음(레알-57.5/바르사-17.1/바이에른-56.0/
#         도르트문트-94.3/마르세유-83.9) — 그래도 도르트문트·마르세유가
#         여전히 -80~-94대로 다소 과함
#    30%: 캡 걸리는 팀 없음, 그리고 결과 순서가 실제 승점 순서(바르사
#         28.0>레알 24.5>바이에른 21.5>마르세유 19.0>도르트문트 18.0)와
#         정확히 일치(바르사 +11.9로 소폭 플러스 — 다섯 팀 중 실제로
#         가장 잘했으니 타당, 레알 -36.2/바이에른 -33.3로 "큰 폭이지만
#         비정상적이지 않은" 음수, 도르트문트 -70.3로 실제로 제일
#         못한 팀답게 가장 큰 페널티) — 30%로 최종 확정.
LEAGUE_ELO_GAP_COMPRESSION = 0.30


def expected_score(rating_a: float, rating_b: float, home_bonus: float = 0.0) -> float:
    return 1.0 / (1.0 + 10 ** (((rating_b) - (rating_a + home_bonus)) / 400.0))


def expected_score_from_gap(gap: float) -> float:
    """[2026-08 신설] expected_score(a,b)와 완전히 같은 수식을, 이미 계산해둔
    격차(gap=a-b)로부터 바로 구한다 — LEAGUE_ELO_GAP_COMPRESSION으로 압축한
    gap을 넣기 위한 용도(_update_team_a_from_league 전용). expected_score()
    자체는 다른 곳(챔스/컵 등)에서 원본 그대로 계속 쓰이므로 손대지 않았다."""
    return 1.0 / (1.0 + 10 ** (-gap / 400.0))


def match_delta(rating_home: float, rating_away: float, grade_home: str, grade_away: str,
                 actual_home: float, comp_weight: float, stage_weight: float,
                 is_final: bool = False, home_bonus: float = HOME_BONUS,
                 streak_home: int = 0, streak_away: int = 0):
    """3.2 공식. actual_home: 홈팀 관점 실제결과(R값, 아래 match_result_r 참고).
    돌려주는 (delta_home, delta_away)는 반드시 부호만 반대인 동일 크기 —
    한쪽이 얻은 점수는 정확히 반대쪽이 잃는다(설계 원칙 7, 3.2 확정).
    상한을 자른 뒤에도 대칭이 유지되도록, delta_home을 먼저 자르고
    delta_away는 그 잘린 값의 부호만 반대로 계산한다.
    [2026-09 신설] streak_home/streak_away: 각 팀/국가의 부진 스트릭 —
    k_for_grade 대신 effective_k_for_grade로 K를 구해서, 부진이 누적된
    쪽은 이 경기의 결과가 실력 재평가에 더 크게 반영되게 한다(제로섬은
    그대로 유지 — 델타 자체는 여전히 부호만 반대인 동일 크기)."""
    e_home = expected_score(rating_home, rating_away, home_bonus)
    k_match = (effective_k_for_grade(grade_home, streak_home)
               + effective_k_for_grade(grade_away, streak_away)) / 2.0
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
    # [2026-09 하향, 신민용 리포트: "클럽 월드컵 우승했다고 62위 팀이 세계
    # 1위가 되는 건 이상하다"] 1.8(유럽 챔스와 동률)에서 내린다. 실측으로
    # 확인된 우승 시 상승폭은 B 72.0 + A 약 131.7 = 약 204 PS였고, 이는
    # 팀평균 OVR 6.8점어치(EPL 전체 팀 OVR 스프레드가 7.56점)다.
    # 1.2는 "유로파(1.3)와 비슷하고 유럽 챔스(1.8)보다 확실히 낮다"는
    # 자리다 — 4년마다 열리는 단일 대회이고 전 대륙이 참가해 유럽 최상위
    # 비중이 압도적이지도 않으므로, 중요한 성과이되 챔스 우승급은 아니다.
    "club_world_cup": 1.2,
    # [2026-09 신설, 신민용 요청: "3부/4부 팀들은 서로 안 붙으니 이 대회로
    # 상대전적이 생기는 거니까 파워랭킹에도 반영해야 한다"] 국내컵(0.6)
    # 보다 격은 낮지만(3/4부 한정, 결승까지 가도 국내컵 우승만큼의 무게는
    # 아님), 이 대회가 사실상 이 티어 팀들의 유일한 교차 데이터라 0으로
    # 두면 의미가 없다 — 국내컵의 약 3/4 수준으로 설정.
    "lower_cup": 0.4,
    # champions/europa/conference는 대륙별로 다름 →
    # TEAM_CHAMPIONS_WEIGHT_BY_CONTINENT / TEAM_EUROPA_WEIGHT_BY_CONTINENT /
    # TEAM_CONFERENCE_WEIGHT_BY_CONTINENT
}
# [2026-09 정정, 신민용 확정: "우승할 때 점수 순위는 챔스: 유럽 > 남미 >
# 아프리카 > 아시아 > 북미 이렇게 가야돼, 유로파급이나 컨퍼런스급도
# 마찬가지"] 예전엔 유럽/북미/아시아/아프리카가 전부 1.6으로 동률이고
# 남미만 1.7로 튀는 애매한 표였다 — 5개 대륙을 확정된 서열대로 전부
# 다른 값으로 촘촘히 벌려놓는다. 유로파/컨퍼런스도 같은 대륙 서열을
# 그대로 따르되, 대회 자체의 격차(챔스 1.4~1.8 > 유로파 0.9~1.3 >
# 컨퍼런스 0.6~1.0)는 유지한다 — 즉 "북미 챔스"가 "유럽 컨퍼런스"보다
# 낮아지는 일은 없다(각 티어 내에서만 대륙 서열이 갈림).
# [2026-09 재조정, 신민용 확정: "챔스는 유럽 2.0 남미 1.5 아프리카 1.3
# 아시아 1.1 북미 1.0 이렇게 가는 게 좋은 것 같고, 이건 컨퍼런스 유로파도
# 마찬가지로 이 정도의 비율로 간다"] 예전 표(1.8/1.7/1.6/1.5/1.4)는 대륙
# 사이가 6%씩밖에 안 벌어져서, 남미 챔스 우승이 유럽 챔스 우승의 94%
# 가치를 가졌다 — 서열은 지켜졌지만 간격이 사실상 없었다.
#
# 유로파/컨퍼런스는 각 티어의 유럽 기준값(1.3 / 1.0)을 그대로 두고 챔스와
# 같은 대륙 비율([1.00, 0.75, 0.65, 0.55, 0.50])을 적용한다.
#
# [알려진 변화] 예전 주석은 "북미 챔스가 유럽 컨퍼런스보다 낮아지는 일은
# 없다"는 불변식을 명시했는데, 새 값에서는 둘이 정확히 1.00으로 동률이
# 된다(낮아지지는 않음). 대륙 간 간격을 벌리라는 지시를 그대로 따른 결과다.
_CLUB_CONTINENT_RATIO = {
    "유럽": 1.00, "남미": 0.75, "아프리카": 0.65, "아시아": 0.55, "북미": 0.50,
}


def _by_continent(europe_anchor: float) -> dict:
    """유럽 기준값 하나로 대륙별 표를 만든다 — 세 티어가 같은 대륙 비율을
    쓰게 해서, 표를 따로 손대다 서열이 어긋나는 일을 막는다."""
    return {k: round(europe_anchor * r, 3) for k, r in _CLUB_CONTINENT_RATIO.items()}


TEAM_CHAMPIONS_WEIGHT_BY_CONTINENT = _by_continent(2.0)
TEAM_EUROPA_WEIGHT_BY_CONTINENT = _by_continent(1.3)
TEAM_CONFERENCE_WEIGHT_BY_CONTINENT = _by_continent(1.0)
_CLUB_CONTINENT_WEIGHT_TABLE = {
    "champions": TEAM_CHAMPIONS_WEIGHT_BY_CONTINENT,
    "europa": TEAM_EUROPA_WEIGHT_BY_CONTINENT,
    "conference": TEAM_CONFERENCE_WEIGHT_BY_CONTINENT,
}


# [2026-09 신설, 신민용+GPT 확정: "B레이어 배점과 A레이어 경기 결과
# 상승폭을 각각 분리해서 조정해야 한다"] 같은 대회가 A(실력 재추정)와
# B(업적)에서 서로 다른 가중치를 가질 수 있게 한다. 여기 없는 대회는
# TEAM_COMPETITION_WEIGHT / 대륙별 표의 값을 A에도 그대로 쓴다(기존 동작).
#
# 클럽월드컵만 A를 B보다 더 낮춘 이유: A레이어는 "이 팀의 진짜 실력이
# 얼마인가"를 다시 추정하는 층인데, 클럽월드컵은 (1) 7경기 남짓의 작은
# 표본이고 (2) 서로 거의 만나지 않는 대륙 간 대결이라 PS 격차 자체가
# 가장 검증이 덜 된 축이다. 그래서 업셋 한 번이 만드는 |실제-기대| 오차가
# 구조적으로 가장 크다(실측: 62위권 팀이 최상위권을 이길 때 경기당
# 13.9~34.6). 업적(B)으로는 충분히 보상하되, 그 7경기로 실력 자체를
# 크게 재평가하지는 않는다.
#
# [제로섬 보존] 이 값은 match_delta에 comp_weight로 들어가 양쪽 팀에
# 똑같이 곱해지므로, "한쪽이 얻은 만큼 반대쪽이 잃는다"(설계 원칙 7)는
# 그대로 유지된다 — 누적값에 상한을 걸어 제로섬을 깨는 방식은 쓰지 않았다.
TEAM_COMPETITION_A_WEIGHT = {
    "club_world_cup": 0.9,
}


def _club_comp_weight(category: str, continent, layer: str = "B") -> float:
    """category(champions/europa/conference/domestic_cup/super_cup/...)와
    continent(챔스/유로파/컨퍼런스만 의미 있음, 그 외는 무시)로 팀 대회
    가중치를 돌려준다. 대륙별 표가 있는 3개 대회는 그 표에서, 없으면
    TEAM_COMPETITION_WEIGHT의 고정값을 쓴다."""
    if layer == "A":
        _a = TEAM_COMPETITION_A_WEIGHT.get(category)
        if _a is not None:
            return _a
    table = _CLUB_CONTINENT_WEIGHT_TABLE.get(category)
    if table is not None:
        # champions 기본값 1.6 유지(과거 세이브의 미분류/구표기 continent
        # 대비 안전한 중간값), europa/conference도 각 표의 중간값을 기본값으로.
        default = {"champions": 1.3, "europa": 0.85, "conference": 0.65}[category]
        return table.get(continent, default)
    return TEAM_COMPETITION_WEIGHT[category]

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
    # [2026-09 수정] group_exit 1 -> 0. 신민용 리포트: "잘한 건 전부
    # 더해지는데 못한 건 거의 안 빠진다 — 국내컵에서 4라운드 탈락했는데도
    # +0.6~1.8이 붙는다." 조별/본선 이전 탈락에 가점을 주지 않는다.
    # 다만 모든 탈락을 똑같이 감점하지도 않는다 — "그 등급치고 못했다"는
    # 신호만 아래 TEAM_EXPECTED_TIER_FLOOR로 따로 감점한다(약체 팀의
    # 조별 탈락은 기대치대로이므로 0점 그대로).
    "champion": 40, "runner_up": 24, "semifinal": 12,
    "quarterfinal": 6, "round16": 3, "group_exit": 0,
}
# [2026-09 신설, 신민용+GPT 확정: "좋은 결과는 가점 / 나쁜 결과는 감점이라는
# 대칭성이 중요하다. 다만 모든 탈락을 똑같이 감점하면 안 되고 대회 중요도 x
# 기대치 x 실제 성적을 같이 봐야 한다"] 국가 쪽에는 이미 같은 장치가 있었다
# (COUNTRY_EXPECTED_TIER_FLOOR / COUNTRY_UNDERPERFORM_BASE_PENALTY) — 팀에는
# 리그 순위 기대치(EXPECTED_PERCENTILE_CEILING)만 있고 대회 기대치가 전무해서,
# SS등급 팀이 챔스·국내컵에서 조기 탈락해도 감점이 하나도 없었다.
# 값은 _STAGE_TIER_ORDINAL 기준(group_exit 0 ~ champion 5).
TEAM_EXPECTED_TIER_FLOOR = {
    "SS": 3,   # 4강 이상은 가야 정상
    "S": 2,    # 8강
    "A": 1,    # 16강
    "B": 0, "C": 0, "D": 0, "E": 0, "F": 0,
}
TEAM_UNDERPERFORM_BASE_PENALTY = -4.0   # 미달 1단계당, 대회가중치를 곱해서 적용
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

# [2026-09 신설, 진단으로 발견한 누락] 리그/국내컵 B 보너스가 부(tier)만
# 보고 국가 리그 등급은 전혀 안 봤다 — 그래서 EPL 우승과 K리그1 우승이
# B레이어에서 완전히 동점이었다. 국내 대회에만 적용한다.
#
# 대륙 대항전(챔스/유로파/컨퍼런스/클럽월드컵)에는 적용하지 않는다:
# "챔스 우승 가치는 포르투갈이든 잉글랜드든 같아야 한다"는 확정 원칙이
# 있고(game_engine 트로피 가중치 주석), 그쪽은 이미 대륙별 표로 갈린다.
#
# 값은 game_engine._GRADE_VALUE_RANGE(발롱도르 리그 강도) 각 등급 구간의
# 중앙값을 그대로 옮긴 것이다 — 두 시스템이 같은 서열 감각을 쓰게 하려는
# 것이고, game_engine을 import하지 않는 이유는 power_ranking을 독립적으로
# 유지하기 위해서다(등급 판정은 constants.get_country_league_grade 하나만 씀).
COUNTRY_LEAGUE_STRENGTH_WEIGHT = {
    "SS": 1.00, "S": 0.95, "A": 0.84, "B": 0.67,
    "C": 0.51, "D": 0.375, "E": 0.275, "F": 0.20,
}
COUNTRY_LEAGUE_STRENGTH_DEFAULT = 0.51   # 등급 판정 실패 시 중간값(C)

# [2026-09 재설계, 신민용 확정: "리그 우승을 단일 28로 고정하는 것 자체를
# 다시 봐야 해 — 같은 리그 우승이라도 S급 리그 우승과 A급 리그 우승의
# 가치가 달라야 함"] 전역 상수 하나(34냐 28이냐)로 모든 리그를 처리하는
# 대신, 리그 등급별로 우승 기본점을 직접 정의한다. 그러면 "인데펜디엔테가
# 28을 받는 게 이상한 것"이 아니라 "아르헨티나 리그가 A급이라서 28"이라고
# 정의되고, 대륙대항전 서열(위 _CLUB_CONTINENT_RATIO)과는 완전히 독립적인
# 축이 된다.
#   SS 36 / S 34 / A 28 / B 22 / C 16 / D 10   (E·F는 같은 기울기로 이어감)
# 우승 외 순위(2위·상위10%·하위권)는 league_placement_bonus의 밴드 모양을
# 그대로 두고 이 기본점 비율(base / 34)만 곱해 스케일한다 — 이미 튜닝된
# 밴드 사이 비율을 건드리지 않는다.
LEAGUE_TITLE_BASE_BY_GRADE = {
    "SS": 36.0, "S": 34.0, "A": 28.0, "B": 22.0,
    "C": 16.0, "D": 10.0, "E": 6.0, "F": 4.0,
}
_LEAGUE_BAND_REFERENCE = 34.0   # league_placement_bonus가 쓰는 기준 기본점


def league_title_scale(country_name, national_grade=None) -> float:
    """국가명 -> league_placement_bonus에 곱할 배율(기본점 / 34)."""
    grade = None
    if country_name:
        try:
            from constants import get_country_league_grade
            grade = get_country_league_grade(country_name, national_grade)
        except Exception:
            grade = None
    base = LEAGUE_TITLE_BASE_BY_GRADE.get(grade, LEAGUE_TITLE_BASE_BY_GRADE["C"])
    return base / _LEAGUE_BAND_REFERENCE


def country_league_strength(country_name, national_grade=None) -> float:
    """국가명 -> 국내 대회 B 보너스에 곱할 리그 강도(0.20~1.00)."""
    if not country_name:
        return COUNTRY_LEAGUE_STRENGTH_DEFAULT
    try:
        from constants import get_country_league_grade
        grade = get_country_league_grade(country_name, national_grade)
    except Exception:
        return COUNTRY_LEAGUE_STRENGTH_DEFAULT
    return COUNTRY_LEAGUE_STRENGTH_WEIGHT.get(grade, COUNTRY_LEAGUE_STRENGTH_DEFAULT)


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
    """[2026-09 재조정, 신민용 확정: "1위 +20 vs 최하위 -8은 방향이 이상하다
    — 상승은 느리게, 하락은 부진이 쌓일수록 빨라지는 구조가 맞다"] 상승 쪽
    폭을 확실히 줄이고(+20→+10), 하락 쪽은 기본 폭 자체도 살짝 늘렸다
    (-8→-9) — 실제 "부진 스트릭에 따른 가속"은 이 함수가 아니라 호출부
    (update_team_b_for_year)에서 STREAK_PENALTY_MULTIPLIER로 마이너스
    값에만 추가로 곱해진다(상승 쪽엔 그 배율을 절대 적용 안 함)."""
    if n_teams <= 0:
        return 0.0
    # [2026-09 리스케일 x3.4, 신민용 확정 중요도: 챔스 > 리그 > 국내컵 >
    # 슈퍼컵] 예전 값(1위 +10)은 B레이어에서 리그 우승을 국내컵 우승(24)
    # 은 물론 3·4부컵 우승(16)보다도 낮게 만들어, 확정된 중요도 서열과
    # 정면으로 어긋났다. 밴드 사이의 비율(1위:꼴찌 = 10:-9)은 이미 여러
    # 세션에 걸쳐 튜닝된 모양이므로 그대로 두고 전체를 x3.4 배만 한다
    # (새 비율 34:-30 = -0.88, 기존 -0.90과 거의 동일) — "상승은 느리게,
    # 하락은 부진이 쌓일수록 빠르게"라는 구조도 그대로다(가속은 이 함수가
    # 아니라 호출부의 STREAK_PENALTY_MULTIPLIER가 마이너스에만 곱한다).
    if final_rank == 1:
        return 34.0   # mutually exclusive — 아래 상위10% 밴드와 안 겹침
    if final_rank == n_teams:
        return -30.0  # 리터럴 꼴찌 — 아래 하위10% 밴드보다 한 단계 더
    percentile = final_rank / n_teams
    if percentile <= 0.10:
        return 17.0
    if percentile <= 0.25:
        return 7.0
    if percentile <= 0.75:
        return 0.0
    if percentile <= 0.90:
        return -10.0
    return -20.0  # 91~99% (리터럴 꼴찌는 위에서 이미 처리됨) — 강등팀도
                  # 이 밴드에 자연히 포함(별도 행 없음, 실제 강등 이벤트
                  # 페널티는 RELEGATION_BASE_PENALTY로 별개 처리)


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
# [2026-09 정정, 신민용: "골드컵도 지역컵으로 가는거고... 코파보다 낮은
# 가중치"] 골드컵(북미)은 대륙컵(CONF) 티어가 아니라 여기 지역컵(REGION)
# 티어 소속이다 — 남미(코파 아메리카)는 이 표에 없어 기본값(0.9)을 쓰므로,
# "코파보다 낮게"를 만족하도록 0.9보다 낮은 값을 명시한다.
# [2026-09 정정, 신민용 확정: "골드컵은 동남아같은 지역컵보단 높지만
# 코파보단 낮은거고"] 코파 아메리카(남미)가 이 표에 없어서 기본값(0.9)을
# 쓰고 있었는데, 그러면 동남아시아(0.9)와 동률이라 "골드컵 > 동남아,
# 골드컵 < 코파"를 만족시킬 자리가 없었다 — 코파를 최상위로 명시하고
# (지역컵 중 가장 격이 높다는 설계 의도), 골드컵을 동남아(0.9)보다
# 위·코파(1.3)보다 아래인 1.0으로 둔다.
REGIONAL_CUP_TIER_WEIGHT = {
    "남미": 1.3,  # 코파 아메리카 — 지역컵 중 최상위
    "서아시아": 1.2, "서아프리카": 1.2,
    "오세아니아": 1.1,
    "북미": 1.0,  # 골드컵 — 동남아 등 일반 지역컵보단 위, 코파보단 아래
    "동아시아": 1.0, "남부아프리카": 1.0,
    "동남아시아": 0.9, "북아프리카": 0.9, "중앙아프리카": 0.9,
    "동아프리카": 0.7,
    "중앙아시아": 0.6, "남아시아": 0.6,
}
QUALIFIER_FAIL_BASE_PENALTY = -8  # 4.6, 대회가중치를 곱해서 적용


def _country_tournament_weight(kind: str, name: str) -> Optional[tuple]:
    """(카테고리키, 가중치)를 돌려준다. region이면 카테고리키='region'이고
    가중치는 지역컵 세부 테이블(regional_cup_tier_weight)에서 별도 조회."""
    if kind == "world":
        return ("world_cup", COUNTRY_TIER_WEIGHT["world_cup"])
    if kind == "region":
        return ("region", None)  # 가중치는 지역명 확인 후 결정(골드컵 포함)
    if kind in ("power_eval", "power_eval_extra"):
        # [2026-09 신설] 랭킹 평가전 — 레이어A(MatchRating)엔 반영하되
        # (그래서 여기 등록함) 레이어B(성적 보너스)는 update_country_b_
        # for_year에서 이 kind를 명시적으로 건너뛰어 완전히 배제한다
        # (신민용 확정: "MatchRating만, AchievementRating 없음" 원칙).
        # 가중치는 지역컵 최하단(0.6)보다는 높고 일반 예선 기본값(1.0)과
        # 같은 중립값 — 실력이 비슷한 팀끼리 붙는 대회라 결과 자체의
        # "이변 정보량"은 충분해서 굳이 낮출 필요는 없다고 판단.
        return ("power_eval", 1.0)
    if kind == "continent":
        if name and EURO_NAME in name:
            return ("euro", COUNTRY_TIER_WEIGHT["euro"])
        # continent 값 자체가 tournament 테이블엔 없으므로 이름으로 유추.
        # [2026-09 정정] 대륙컵(네이션스컵) 티어는 다시 유럽/남북미 통합
        # ("남북미 대륙컵")/아시아/아프리카 4개뿐이다("대륙컵"이라는 단어가
        # 이 4개 이름 중 "남북미 대륙컵"에만 들어있어 substring으로 안전하게
        # 구분됨). 골드컵은 이제 kind='region'이라 여기 안 걸린다.
        for region, key in (("아시안컵", "asian_cup"), ("아프리카", "afcon"),
                             ("대륙컵", "americas_cup")):
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


@dataclass
class LeaguePowerEntry:
    """[2026-09 신설, 신민용 확정: "지금 파워 랭킹은 있는데 리그 랭킹은
    없더라. 이 랭킹 계산은 (팀 전체 순위 합)/(그 리그 팀 합), 이 값이
    적을수록 높은 걸로 하는 게 나은 것 같은데 — 프리미어리그를 예시로
    두면 20개 팀의 순위 합 / 20을 하는 거지"]

    avg_rank가 그 "평균 전체 순위"이고(작을수록 강한 리그), rank는 그
    avg_rank를 오름차순으로 매긴 리그 순위다. n_teams는 그 해 실제로
    집계에 들어간 팀 수(= 나눈 값)로, 화면에 그대로 보여준다 — 리그마다
    팀 수가 다르고(18/20/22팀 등) 승강으로 해마다 바뀔 수도 있어서
    "무엇으로 나눴는지"가 보이는 게 맞다.
    """
    league_id: int
    league_name: str
    tier: Optional[int]
    country: str
    continent: str
    avg_rank: float
    n_teams: int
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
    # [2026-09 신설, 부진 스트릭] 기존 세이브엔 이 컬럼이 없으므로 ALTER
    # TABLE로 추가 — 이미 있으면(신규 세이브 또는 재실행) SQLite가
    # "duplicate column" OperationalError를 던지는데, 다른 마이그레이션들과
    # 동일하게 조용히 무시한다.
    for _ddl in (
        "ALTER TABLE team_power_rating ADD COLUMN underperform_streak INTEGER DEFAULT 0",
        "ALTER TABLE country_power_rating ADD COLUMN underperform_streak INTEGER DEFAULT 0",
    ):
        try:
            c.execute(_ddl)
        except Exception:
            pass
    # 연도별 스냅샷(발표 파워랭킹) — 표시 PS = a_rating+b_rating, 스무딩 없음(5.3).
    # [2026-09 DB 분리 2차, 성능 감사 1위] 이 표는 매년 전 세계 팀 수만큼
    # (실측 11,393행) 한 번 쌓이고 그 뒤로는 절대 안 바뀌는 순수 연도별
    # 스냅샷인데(실측 15시즌 182,288행), 여태 main(game.db)에 살면서
    # 자동저장(flush_to_disk의 전체 backup)·시작 로딩·주기 VACUUM 비용에
    # 매년 영구히 가산되고 있었다. ai_player_ovr_history와 같은 해법으로
    # hist(별도 파일 history.db)에 둔다 — 데이터는 그대로 두고 backup
    # 대상('main' 스키마)에서만 빠진다. 무자격 테이블 이름은 SQLite가
    # main → 붙은 DB 순으로 찾으므로 이 파일의 다른 SELECT/INSERT는
    # 한 글자도 안 고쳐도 그대로 동작한다. 기존 세이브는 database.
    # _migrate_history_db_split_v2()가 1회 이전한다.
    c.execute("""CREATE TABLE IF NOT EXISTS hist.team_power_rankings(
        ranking_year INTEGER, evaluation_year INTEGER,
        team_id INTEGER, team_name TEXT, continent TEXT, country TEXT,
        rating REAL, rank INTEGER, prev_rank INTEGER,
        PRIMARY KEY(ranking_year, team_id))""")
    c.execute("""CREATE INDEX IF NOT EXISTS hist.idx_tpr_year_continent
        ON team_power_rankings(ranking_year, continent, rank)""")
    c.execute("""CREATE INDEX IF NOT EXISTS hist.idx_tpr_team
        ON team_power_rankings(team_id, ranking_year)""")
    # [2026-09 신설, 성능 감사] get_team_power_history의 '국가 내 순위'
    # 계산은 (ranking_year, country, rank) 조건으로 세는데, 여태 이 조합을
    # 커버하는 인덱스가 없어서(idx_tpr_year_continent는 continent 기준)
    # 매번 그 해 전체 팀(실측 11,393행)을 훑어야 했다. 이 인덱스가 있으면
    # 그 나라 팀들(보통 수십 개)만 범위 스캔한다.
    c.execute("""CREATE INDEX IF NOT EXISTS hist.idx_tpr_year_country
        ON team_power_rankings(ranking_year, country, rank)""")
    # [2026-09 신설, 성능] 리그 파워랭킹 집계(_aggregate_league_ranks)가 쓰는
    # (ranking_year → team_id/rank/evaluation_year) 조회를 인덱스만으로
    # 끝내는 커버링 인덱스. PK(ranking_year, team_id)는 rank/evaluation_year를
    # 안 담아서 행마다 테이블을 한 번 더 찾아야 했다 — 그 해 행이 11,397개라
    # 호출당 2만 번 이상(올해+작년) 랜덤 페이지 접근이 발생했다.
    c.execute("""CREATE INDEX IF NOT EXISTS hist.idx_tpr_year_team_rank
        ON team_power_rankings(ranking_year, team_id, rank, evaluation_year)""")
    # [2026-09 신설, 신민용 리포트: "팀이랑 국가는 클릭하면 바로 이전순위가
    # 뜨는데 리그는 클릭하면 딜레이가 2초정도 있고 켜져"] 리그 순위를
    # 팀/국가와 **같은 방식으로 저장**한다. 여태 리그 순위만 저장 테이블이
    # 없어서, 표를 열 때마다(그리고 이력창은 연도마다 한 번씩) "그 리그
    # 팀들의 전체순위 평균"을 다시 집계했다 — 팀/국가는 rank가 이미
    # 저장돼 있어 인덱스로 읽으면 끝이라 즉시 떴고, 리그만 느렸다.
    # 실측(60년 규모, team_power_rankings 683,820행): 이력창 1,860ms.
    # ensure_league_power_rankings()가 team_power_rankings에는 있는데
    # 여기 없는 연도를 찾아 한 번에 채운다(기존 세이브도 최초 1회만 비용).
    c.execute("""CREATE TABLE IF NOT EXISTS hist.league_power_rankings(
        ranking_year INTEGER, league_id INTEGER, evaluation_year INTEGER,
        avg_rank REAL, n_teams INTEGER, rank INTEGER, continent_rank INTEGER,
        PRIMARY KEY(ranking_year, league_id))""")
    c.execute("""CREATE INDEX IF NOT EXISTS hist.idx_lpr_year_rank
        ON league_power_rankings(ranking_year, rank)""")
    c.execute("""CREATE INDEX IF NOT EXISTS hist.idx_lpr_league
        ON league_power_rankings(league_id, ranking_year)""")
    # [2026-09 신설] 위 all-years 집계가 쓰는 (team_id, year) 그룹화를
    # 인덱스만으로 끝내기 위한 인덱스 — 기존 idx_lss_team은 team_id
    # 하나뿐이라 연도별 묶기에 정렬이 필요했다.
    c.execute("""CREATE INDEX IF NOT EXISTS hist.idx_lss_team_year
        ON league_season_standings(team_id, year, league_id)""")
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
    # [2026-09 DB 분리 2차] 위 team_power_rankings와 같은 이유·같은 규모
    # (실측 168,525행, 매년 +11,393)로 같이 hist에 둔다.
    c.execute("""CREATE TABLE IF NOT EXISTS hist.team_b_history(
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
_EQP_LOGGED: set = set()   # [2026-09 신설, 진단용] _update_team_a_from_matches의
                            # 실행계획 로그를 (표,연도) 조합당 한 번만 찍기 위한 중복방지 집합
# [2026-09 신설, 부진 스트릭] 위 _team_ab_cache와 완전히 같은 패턴/이유
# (연 1회 배치 동안만 유효, run_year_end_power_ranking_update 시작 시
# 매번 비움) — a_rating/b_rating과 별개 축이라 별도 캐시로 둔다.
_team_streak_cache: dict = {}
_country_streak_cache: dict = {}

# [2026-08 신설, 신민용 요청: "레알 23위→2위/바르셀로나 4위→14위 같은
# 급변동을 감으로 재계산하지 말고 실제 로그로 바로 판별하고 싶다"]
# _RELEGATION_DEBUG_TRACKING(game_engine.py)과 완전히 같은 패턴 — 평소엔
# 완전히 꺼진 상태로 오버헤드 0, 이 플래그를 켜고 추적하고 싶은 팀
# id들을 _POWER_DEBUG_TEAM_IDS에 넣으면 그 팀들에 한해 _add_team_a/
# _add_team_b가 불릴 때마다 "어느 소스(리그/챔스/국내컵/클럽월드컵/
# 강등페널티/시즌전환회귀 등)에서 얼마씩 A/B가 변했는지"를 콘솔+파일
# 로그에 남긴다. DB 스키마·컬럼은 전혀 안 늘어난다(팀당 A/B가 시즌마다
# 그대로 덮어써지는 현재 구조 특성상 사후 복원이 불가능하므로, 다음에
# 같은 문제가 또 생겼을 때 이 로그부터 켜고 재현하면 된다).
# [2026-08 신설, 신민용 요청: "레알 23위→2위/바르셀로나 4위→14위 같은
# 급변동을 감으로 재계산하지 말고 실제 로그로 바로 판별하고 싶다"]
# _RELEGATION_DEBUG_TRACKING(game_engine.py)과 완전히 같은 패턴 — 평소엔
# 완전히 꺼진 상태로 오버헤드 0, 이 플래그를 켜고 추적하고 싶은 팀
# id들을 _POWER_DEBUG_TEAM_IDS에 넣으면 그 팀들에 한해 _add_team_a/
# _add_team_b가 불릴 때마다 "어느 소스(리그/챔스/국내컵/클럽월드컵/
# 강등페널티/시즌전환회귀 등)에서 얼마씩 A/B가 변했는지"를 콘솔+파일
# 로그에 남긴다. DB 스키마·컬럼은 전혀 안 늘어난다(팀당 A/B가 시즌마다
# 그대로 덮어써지는 현재 구조 특성상 사후 복원이 불가능하므로, 다음에
# 같은 문제가 또 생겼을 때 이 로그부터 켜고 재현하면 된다).
#
# [사용법 — 파이썬 콘솔 필요 없음] 아래 두 줄만 고치고 게임을 실행하면
# 끝난다:
#   1) DEBUG_POWER_RANKING_TRACKING = False → True로 바꾼다.
#   2) _POWER_DEBUG_TEAM_NAMES 리스트에 추적하고 싶은 팀 이름을 정확히
#      적는다(게임 안에 실제로 등록된 팀명과 철자까지 똑같아야 함).
# 그 상태로 게임을 켜고 시즌을 한 번(연도전환 1회) 넘기면, 게임을 실행한
# 폴더(game.db가 있는 폴더와 같은 곳)에 power_ranking_debug.log 파일이
# 자동으로 생기거나 이어서 쌓인다 — 그 파일을 열어보면 됨. 다 보고 나면
# 다시 False로 꺼두는 걸 권장(계속 켜두면 매 시즌마다 파일이 계속
# 불어남).
DEBUG_POWER_RANKING_TRACKING = True
_POWER_DEBUG_TEAM_NAMES = [] #파워랭킹 오류 검사하고 싶음 넣으셈
_POWER_DEBUG_TEAM_IDS: set = set()
_POWER_DEBUG_LOG_PATH = "power_ranking_debug.log"


def set_power_debug_teams_by_name(conn, names) -> list:
    """이름(들)으로 team_id를 찾아 _POWER_DEBUG_TEAM_IDS에 등록한다.
    편의 함수 — DEBUG_POWER_RANKING_TRACKING=True로 켜둔 뒤 이 함수를
    한 번 호출해두면 그 뒤 run_year_end_power_ranking_update가 돌 때마다
    자동으로 추적된다. 반환값: 실제로 찾아서 등록된 (team_id, name) 목록
    (오타 등으로 못 찾은 이름은 조용히 빠짐 — 호출부에서 반환값 길이로
    확인 가능)."""
    global _POWER_DEBUG_TEAM_IDS
    if isinstance(names, str):
        names = [names]
    found = []
    for name in names:
        row = conn.execute("SELECT id, name FROM teams WHERE name=?", (name,)).fetchone()
        if row:
            _POWER_DEBUG_TEAM_IDS.add(row[0])
            found.append((row[0], row[1]))
    return found


def _power_debug_log(conn, team_id: int, layer: str, source: str, delta: float):
    """DEBUG_POWER_RANKING_TRACKING이 꺼져 있거나 이 팀이 추적 대상이
    아니면 즉시 리턴(오버헤드 없음). 켜져 있으면 delta가 0이어도(변화
    없음을 확인하고 싶을 수 있어) 그대로 기록한다."""
    if not DEBUG_POWER_RANKING_TRACKING or team_id not in _POWER_DEBUG_TEAM_IDS:
        return
    row = conn.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
    name = row[0] if row else f"team#{team_id}"
    line = f"[{name}] {layer} / {source}: {delta:+.2f}"
    print(f"[POWER-DEBUG] {line}")
    try:
        with open(_POWER_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _power_debug_snapshot(conn, label: str):
    """DEBUG_POWER_RANKING_TRACKING 켜져있을 때, 추적 대상 팀 전원의
    현재 a_rating/b_rating을 한 줄로 남긴다 — run_year_end_power_ranking_
    update 시작 직전(시즌 시작 값)/끝난 직후(시즌 종료 값) 두 번 호출하면
    "그 시즌 동안 A/B가 각각 얼마나 움직였는지" 최종 합계도 자연히
    비교 가능해진다."""
    if not DEBUG_POWER_RANKING_TRACKING or not _POWER_DEBUG_TEAM_IDS:
        return
    for team_id in _POWER_DEBUG_TEAM_IDS:
        row = conn.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
        name = row[0] if row else f"team#{team_id}"
        a, b = _get_team_ab(conn, team_id)
        line = f"[{name}] === {label}: A={a:.2f} B={b:.2f} PS={a+b:.2f} ==="
        print(f"[POWER-DEBUG] {line}")
        try:
            with open(_POWER_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


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


def _get_team_streak(conn, team_id: int) -> int:
    """직전 시즌까지 누적된 부진 스트릭. _team_ab_cache와 동일한 패턴 —
    이번 배치 동안 이미 읽었거나 쓴 값은 다시 SELECT하지 않는다."""
    cached = _team_streak_cache.get(team_id)
    if cached is not None:
        return cached
    row = conn.execute(
        "SELECT underperform_streak FROM team_power_rating WHERE team_id=?", (team_id,)
    ).fetchone()
    result = (row[0] or 0) if row else 0
    _team_streak_cache[team_id] = result
    return result


def _set_team_streak(conn, team_id: int, streak: int):
    """update_team_b_for_year가 이번 시즌 판정을 끝낸 뒤 1회 호출 —
    team_power_rating 행은 이 시점엔 이미 레이어A(update_team_ratings_
    for_year)에서 그 팀의 a_rating이 갱신되며 존재가 보장된다."""
    if _is_mil_team(conn, team_id):   # [2026-10] 군팀은 파워랭킹 제외
        return
    conn.execute("UPDATE team_power_rating SET underperform_streak=? WHERE team_id=?",
                 (streak, team_id))
    _team_streak_cache[team_id] = streak


def _add_team_a(conn, team_id: int, delta: float, year: int, source: str = ""):
    if _is_mil_team(conn, team_id):   # [2026-10] 군팀은 파워랭킹 제외
        return
    a, b = _get_team_ab(conn, team_id)
    conn.execute("""INSERT INTO team_power_rating(team_id, a_rating, b_rating, last_updated_year)
                     VALUES(?,?,?,?)
                     ON CONFLICT(team_id) DO UPDATE SET
                        a_rating=excluded.a_rating, last_updated_year=excluded.last_updated_year""",
                 (team_id, a + delta, b, year))
    _team_ab_cache[team_id] = (a + delta, b)
    _power_debug_log(conn, team_id, "A", source or "?", delta)


def _add_team_b(conn, team_id: int, delta: float, year: int, source: str = ""):
    if _is_mil_team(conn, team_id):   # [2026-10] 군팀은 파워랭킹 제외
        return
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
    _power_debug_log(conn, team_id, "B", source or "?", delta)


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


def _get_country_streak(conn, country: str) -> int:
    cached = _country_streak_cache.get(country)
    if cached is not None:
        return cached
    row = conn.execute(
        "SELECT underperform_streak FROM country_power_rating WHERE country=?", (country,)
    ).fetchone()
    result = (row[0] or 0) if row else 0
    _country_streak_cache[country] = result
    return result


def _set_country_streak(conn, country: str, streak: int):
    if _is_mil_country(country):   # [2026-10] 군대는 국가 파워랭킹 제외
        return
    conn.execute("UPDATE country_power_rating SET underperform_streak=? WHERE country=?",
                 (streak, country))
    _country_streak_cache[country] = streak


def _add_country_a(conn, country: str, delta: float, year: int):
    if _is_mil_country(country):   # [2026-10] 군대는 국가 파워랭킹 제외
        return
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
    if _is_mil_country(country):   # [2026-10] 군대는 국가 파워랭킹 제외
        return
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
    leagues = _drop_mil_leagues(conn, leagues)   # [2026-10] 군 리그는 리그 파워 계산 제외
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

# [2026-09 신설] 국가 리그 강도를 곱할 "국내" 대회 집합.
_DOMESTIC_B_CATEGORIES = frozenset(("domestic_cup", "lower_cup"))

_CLUB_COMP_TABLES = {
    "champions": ("cl_tournaments", "cl_matches"),
    "europa": ("el_tournaments", "el_matches"),
    "conference": ("ecl_tournaments", "ecl_matches"),
    "club_world_cup": ("cwc_tournaments", "cwc_matches"),
    "super_cup": ("sc_tournaments", "sc_matches"),
    "domestic_cup": ("cup_tournaments", "cup_matches"),
    "lower_cup": ("lower_cup_tournaments", "lower_cup_matches"),
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
    # [2026-09 개편] "북남미" 통합 폐지 — champions_engine.CONTINENT_MAP과
    # 동일한 규칙(오세아니아→아시아, 남미/북미는 각자 독립)으로 맞춘다.
    if continent in ("아시아", "오세아니아"):
        return "아시아"
    if continent in ("북미", "남미"):
        return continent
    return continent


def _update_team_a_from_matches(conn, matches_table: str, tournament_id: int, year: int,
                                 comp_weight: float, use_stage_col: bool = True,
                                 discount_same_league: bool = False, source: str = ""):
    # [2026-09 신설, 진단용] SQL조회 시간이 계산량(경기수) 불변인데도
    # 15년간 10배 늘었고, cache_size를 64MB→256MB로 올려도 전혀 개선이
    # 없었다(=단순 캐시압박이 아니다) — 추측을 그만하고 실제 실행계획을
    # 표별·연도별로 한 번씩 직접 찍어서 SEARCH인지 SCAN인지 확정한다.
    global _EQP_LOGGED
    _eqp_key = (matches_table, year)
    if _eqp_key not in _EQP_LOGGED:
        _EQP_LOGGED.add(_eqp_key)
        try:
            _plan = conn.execute(
                f"EXPLAIN QUERY PLAN SELECT home_team_id, away_team_id, home_score, "
                f"away_score, pso_winner, {'stage' if use_stage_col else 'round_idx'} "
                f"FROM {matches_table} WHERE tournament_id=? ORDER BY id ASC",
                (tournament_id,)).fetchall()
            _cnt = conn.execute(f"SELECT COUNT(*) FROM {matches_table}").fetchone()[0]
            _perf_log(f"[PERF-EQP] {year}년 {matches_table}(전체{_cnt}행) 실행계획: "
                      f"{' / '.join(r[-1] for r in _plan)}")
        except Exception as _e:
            _perf_log(f"[PERF-EQP] {year}년 {matches_table} 실행계획 조회 실패: {_e}")
    import time as _t_uam
    _m0 = _t_uam.perf_counter()
    stage_col = "stage" if use_stage_col else "round_idx"
    rows = conn.execute(
        f"""SELECT home_team_id, away_team_id, home_score, away_score, pso_winner, {stage_col}
            FROM {matches_table} WHERE tournament_id=? ORDER BY id ASC""",
        (tournament_id,)).fetchall()
    _m1 = _t_uam.perf_counter()
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
        sh, sa = _get_team_streak(conn, home_id), _get_team_streak(conn, away_id)
        d_home, d_away = match_delta(rh, ra, gh, ga, r, comp_weight, sw, is_final=is_final,
                                      streak_home=sh, streak_away=sa)
        _add_team_a(conn, home_id, d_home, year, source=source or f"match:{matches_table}#{tournament_id}")
        _add_team_a(conn, away_id, d_away, year, source=source or f"match:{matches_table}#{tournament_id}")
    _m2 = _t_uam.perf_counter()
    return len(rows), _m1 - _m0, _m2 - _m1   # [진단용] 경기수, SQL조회시간, 파이썬루프시간


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
    # [2026-09 신설, 진단용] 아래 쿼리(idx_lss_year 추가 대상)와 그 뒤
    # 팀별 루프 중 실제로 어느 쪽이 무거운지 구분 — 합성 벤치마크로는
    # 쿼리 자체가 빨라짐을 확인했는데 실측 총시간은 안 줄어서, 나머지
    # (팀별 루프)를 의심 중이다.
    import time as _t_utfl
    _l0 = _t_utfl.perf_counter()
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
    _l1 = _t_utfl.perf_counter()
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
        # [2026-08 신설, 신민용+GPT 확정 — gap 압축] 시드 기반 레이팅 격차가
        # 실제 매치엔진의 완만한 승률 분포보다 훨씬 가파른 문제(위
        # LEAGUE_ELO_GAP_COMPRESSION 정의부 설명 참고) — expected_score()를
        # 그대로 쓰는 대신, gap(=rating-avg_opponent_ps) 자체를 압축한 뒤
        # 그 압축된 gap으로 기대승률을 계산한다. rating/avg_opponent_ps
        # 값 자체나 grade_for_ps 판정에는 전혀 영향 없음 — 이 기대승점
        # 계산 한 곳에만 적용.
        gap = rating - avg_opponent_ps
        e = expected_score_from_gap(gap * LEAGUE_ELO_GAP_COMPRESSION)
        expected_points = e * n_games
        # [2026-09 신설] 상대(avg_grade)는 실제 팀이 아니라 '그 시즌 만난
        # 상대들의 평균'이라는 가상값이라 스트릭 개념이 없다 — team_id
        # 자신의 스트릭만 자기 쪽 K에 반영한다.
        k_match = (effective_k_for_grade(grade, _get_team_streak(conn, team_id))
                   + k_for_grade(avg_grade)) / 2.0
        raw = k_match * w * (actual_points - expected_points)
        delta = max(-LEAGUE_SEASON_DELTA_CAP, min(LEAGUE_SEASON_DELTA_CAP, raw))
        # [2026-08 신설, 진단용] 클램프 전 raw값도 함께 남긴다 — 다음에
        # 비슷한 문제가 생기면 이 값으로 바로 원인(캡 문제 vs calibration
        # 문제)을 구분할 수 있다.
        _power_debug_log(conn, team_id, "A", f"league_raw(actual={actual_points:.1f},expected={expected_points:.1f})", raw)
        _add_team_a(conn, team_id, delta, evaluation_year, source="A:league")
    _l2 = _t_utfl.perf_counter()
    _perf_log(f"[PERF-POWER-A]   _update_team_a_from_league 세부: "
              f"조회(standings+opp_strength) {_l1-_l0:.3f}s | "
              f"팀별루프({len(rows)}팀) {_l2-_l1:.3f}s")


def update_team_ratings_for_year(conn, evaluation_year: int):
    # [2026-09 신설, 진단용] 15년 장기실측에서 이 함수(팀A값) 총시간이
    # +92~105% 늘었는데, league_season_standings에 year 인덱스를 추가해도
    # 개선이 거의 안 보였다(합성 벤치마크로 그 쿼리 자체는 실제로 빨라짐을
    # 확인함 — 즉 이 함수 총시간의 대부분이 그 쿼리가 아니라는 뜻). 아래
    # 두 구간(국내리그 처리 vs 대회 처리)과 대회/경기 건수를 같이 찍어서
    # 다음 실측에서 어느 쪽이 실제로 자라는지 숫자로 가른다 — 로직은
    # 전혀 안 건드림.
    import time as _t_ptr
    _tr0 = _t_ptr.perf_counter()
    ensure_power_ranking_tables(conn)
    _update_team_a_from_league(conn, evaluation_year)
    _tr1 = _t_ptr.perf_counter()
    _n_tournaments = 0
    _n_matches = 0
    _sql_total = 0.0
    _loop_total = 0.0
    for category, (tournaments_table, matches_table) in _CLUB_COMP_TABLES.items():
        # [2026-09 정정] 유로파/컨퍼런스도 챔스처럼 continent별로 가중치가
        # 갈리므로(대륙 서열: 유럽>남미>아프리카>아시아>북미) 이 셋 다
        # continent 컬럼을 읽는다 — domestic_cup/super_cup만 대륙 무관.
        _has_continent = category in _CLUB_CONTINENT_WEIGHT_TABLE
        tids_rows = conn.execute(
            f"SELECT id, continent FROM {tournaments_table} WHERE year=?"
            if _has_continent else
            f"SELECT id, NULL FROM {tournaments_table} WHERE year=?",
            (evaluation_year,)).fetchall()
        for tid, continent in tids_rows:
            _n_tournaments += 1
            weight = _club_comp_weight(category, continent, layer="A")
            _n_m, _sql_t, _loop_t = _update_team_a_from_matches(
                conn, matches_table, tid, evaluation_year, weight,
                use_stage_col=(category not in ("domestic_cup", "lower_cup")),
                discount_same_league=(category in ("champions", "europa", "conference")),
                source=f"A:{category}")
            _n_matches += _n_m
            _sql_total += _sql_t
            _loop_total += _loop_t
    _tr2 = _t_ptr.perf_counter()
    conn.commit()
    _perf_log(f"[PERF-POWER-A] {evaluation_year}년 팀A값 세부: "
              f"국내리그처리 {_tr1-_tr0:.3f}s | "
              f"대회처리 {_tr2-_tr1:.3f}s ({_n_tournaments}개 토너먼트·{_n_matches}경기, "
              f"└SQL조회 {_sql_total:.3f}s · 파이썬루프 {_loop_total:.3f}s)")


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
    if _is_mil_league(conn, league_id):   # [2026-10] 군 리그는 연속우승 감쇠 대상 아님
        return 1.0
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
    # [2026-09 신설, 진단용] update_team_ratings_for_year의 [PERF-POWER-A]와
    # 짝을 이루는 계측 — 팀B값도 같은 15년 장기실측에서 +148~155% 늘었는데
    # 원인이 아직 안 갈려서 같은 방식으로 3구간을 나눠 찍는다.
    import time as _t_utby
    _b0 = _t_utby.perf_counter()
    # 1) 국내리그 순위 보너스(백분위 기반, 리그 부(tier)로 가중치 조정) + 연속우승 감쇠
    # [2026-09 확장] 국가명/국대등급을 같이 읽어 국가 리그 강도
    # (country_league_strength)를 리그 순위 보너스에 곱한다 — 예전엔
    # 부(tier)만 봐서 EPL 우승과 K리그1 우승이 B에서 동점이었다.
    rows = conn.execute(
        """SELECT l.id, l.tier, s.team_id, s.wins, s.draws, s.losses,
                  cn.name AS cname, cn.grade AS cgrade
           FROM league_season_standings s JOIN leagues l ON s.league_id = l.id
           JOIN countries cn ON l.country_id = cn.id
           WHERE s.year=?""", (evaluation_year,)).fetchall()
    _n_teams_layer1 = len(rows)
    by_league = {}
    tier_of_league = {}
    strength_of_league = {}
    for league_id, tier, team_id, wins, draws, losses, _cname, _cgrade in rows:
        pts = (wins or 0) * 3 + (draws or 0)
        by_league.setdefault(league_id, []).append((team_id, pts))
        tier_of_league[league_id] = tier
        if league_id not in strength_of_league:
            # [2026-09 재설계] 리그 순위 보너스는 연속 배율(country_league_
            # strength)이 아니라 등급별 우승 기본점(league_title_scale)으로
            # 스케일한다 — 국내컵 쪽은 그대로 country_league_strength를 쓴다.
            strength_of_league[league_id] = league_title_scale(_cname, _cgrade)
    for league_id, standings in by_league.items():
        standings.sort(key=lambda x: x[1], reverse=True)
        n = len(standings)
        champion_id = standings[0][0] if standings else None
        decay = _apply_team_league_streak(conn, league_id, champion_id) if champion_id else 1.0
        tier_w = (league_tier_weight(tier_of_league.get(league_id))
                   * strength_of_league.get(league_id, COUNTRY_LEAGUE_STRENGTH_DEFAULT))
        for rank, (team_id, _pts) in enumerate(standings, start=1):
            bonus = league_placement_bonus(rank, n)
            # [2026-09 신설, 부진 스트릭] "이 등급이면 이 순위까지는 정상"
            # 이라는 기대치(EXPECTED_PERCENTILE_CEILING)를 그 시즌 실제
            # 백분위와 비교해서, 기대 이하면 streak+1·아니면 streak=0으로
            # 리셋한다. 등급은 이 시점(레이어A가 이미 반영된 뒤) 기준 —
            # "이번 시즌 실력 대비 이번 시즌 순위"로 근사한다(TUNE LATER,
            # 정밀하게 하려면 시즌 시작 시점 등급을 별도 스냅샷해야 함).
            grade = _get_team_grade(conn, team_id)
            percentile = (rank / n) if n else 1.0
            is_underperform = percentile > EXPECTED_PERCENTILE_CEILING.get(grade, 1.01)
            old_streak = _get_team_streak(conn, team_id)
            new_streak = old_streak + 1 if is_underperform else 0
            # [2026-08 v3.2] 하위권 밴드가 마이너스를 돌려주므로 더 이상
            # "bonus<=0이면 스킵"하면 안 된다 — 정확히 0(중위권)일 때만
            # 스킵(어차피 더할 게 없음), 마이너스는 그대로 적용해야
            # "우승만 보정되고 부진은 반영 안 되는" 예전 비대칭이 안 생긴다.
            if bonus < 0 and is_underperform:
                # 상승 쪽(양수 bonus)엔 이 배율을 절대 적용하지 않는다 —
                # "기대 이하가 아닌데 순위표상 마이너스 밴드"(낮은 등급이
                # 원래 하위권인 경우)에도 적용 안 됨, 딱 "그 등급치고
                # 부진"일 때만 가속.
                bucket = min(new_streak, 5)
                bonus *= STREAK_PENALTY_MULTIPLIER.get(bucket, STREAK_PENALTY_MULTIPLIER[5])
            if rank == 1:
                bonus *= decay
            if bonus != 0:
                _add_team_b(conn, team_id, bonus * TEAM_COMPETITION_WEIGHT["league"] * tier_w,
                            evaluation_year, source="B:league_placement")
            else:
                # bonus가 0이라 _add_team_b를 안 거쳤어도 team_power_rating
                # 행 자체는 있어야 스트릭을 저장할 수 있다(레이어A에서 이미
                # _add_team_a가 이 팀에 호출됐어야 정상이지만, 만에 하나를
                # 대비해 델타 0으로 안전하게 존재를 보장).
                _add_team_a(conn, team_id, 0.0, evaluation_year, source="streak-touch")
            _set_team_streak(conn, team_id, new_streak)
    _b1 = _t_utby.perf_counter()

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
        _add_team_b(conn, team_id, penalty, evaluation_year, source="B:relegation_penalty")
    _b2 = _t_utby.perf_counter()

    # 2) 국제/국내컵 계열 대회 성적 보너스 (deepest-stage 판정)
    _n_tournaments = 0
    # [2026-09 신설] 국내 대회는 국가 리그 강도를 곱한다(리그와 같은 이유).
    # 대륙 대항전은 대륙별 표로 이미 갈리므로 제외 — "챔스 우승 가치는
    # 포르투갈이든 잉글랜드든 같아야 한다"는 확정 원칙.
    _team_country = None
    for category, (tournaments_table, matches_table) in _CLUB_COMP_TABLES.items():
        _has_continent = category in _CLUB_CONTINENT_WEIGHT_TABLE
        _is_domestic = category in _DOMESTIC_B_CATEGORIES
        if _is_domestic and _team_country is None:
            _team_country = {r[0]: (r[1], r[2]) for r in conn.execute(
                """SELECT t.id, cn.name, cn.grade FROM teams t
                   JOIN countries cn ON t.country_id = cn.id""").fetchall()}
        rows = conn.execute(
            f"SELECT id, {'continent' if _has_continent else 'NULL'} "
            f"FROM {tournaments_table} WHERE year=?", (evaluation_year,)).fetchall()
        for tid, continent in rows:
            _n_tournaments += 1
            weight = _club_comp_weight(category, continent)
            placements = _deepest_stage_participants(
                conn, matches_table, tid, use_stage_col=(category not in ("domestic_cup", "lower_cup")))
            for team_id, tier in placements.items():
                base = PLACEMENT_BASE_SCORE[tier]
                w = weight
                if _is_domestic:
                    _cn, _cg = (_team_country or {}).get(team_id, (None, None))
                    w *= country_league_strength(_cn, _cg)
                if base:
                    _add_team_b(conn, team_id, base * w, evaluation_year, source=f"B:{category}")
                # [2026-09 신설] "그 등급치고 못했다"는 신호만 감점한다 —
                # 약체 팀의 조별 탈락은 기대치대로이므로 0점 그대로 두고,
                # 기대 단계(TEAM_EXPECTED_TIER_FLOOR)에 미달한 만큼만
                # 대회 중요도를 곱해서 깎는다(국가 레이어와 동일한 설계).
                _floor = TEAM_EXPECTED_TIER_FLOOR.get(_get_team_grade(conn, team_id), 0)
                _short = _floor - _STAGE_TIER_ORDINAL.get(tier, 0)
                if _short > 0:
                    _add_team_b(conn, team_id,
                                 TEAM_UNDERPERFORM_BASE_PENALTY * _short * w,
                                 evaluation_year, source=f"B:{category}_underperform")
    _b3 = _t_utby.perf_counter()
    _perf_log(f"[PERF-POWER-B] {evaluation_year}년 팀B값 세부: "
              f"국내리그순위보너스({_n_teams_layer1}팀) {_b1-_b0:.3f}s | "
              f"강등페널티 {_b2-_b1:.3f}s | "
              f"대회보너스({_n_tournaments}개 토너먼트) {_b3-_b2:.3f}s")


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
        sh, sa = _get_country_streak(conn, home), _get_country_streak(conn, away)
        d_home, d_away = match_delta(rh, ra, gh, ga, r, weight, sw, is_final=is_final,
                                      streak_home=sh, streak_away=sa)
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

    # [2026-09 신설, 부진 스트릭] 이 해에 이 나라가 실제로 뭔가(본선 또는
    # 예선탈락)에 관여했는지, 관여했다면 그중 "최고" 도달 스테이지가
    # 뭐였는지를 먼저 다 모은다 — 같은 해에 여러 대회(예: 대륙컵+지역컵)에
    # 나갔으면 더 잘한 쪽 기준으로 그 해를 평가한다(가장 후한 해석).
    # 예선 탈락(본선 진출 실패)은 group_exit보다도 아래인 -1로 취급.
    country_best_tier_this_year: dict = {}
    country_qual_fail_weight: dict = {}

    for tid, kind, name in main_rows:
        if kind in ("power_eval", "power_eval_extra"):
            # [2026-09 신설] 레이어B(성적 보너스) 완전 배제 — 위
            # _country_tournament_weight 등록 주석 참고. 레이어A는 이미
            # update_country_ratings_for_year에서 정상 반영됨. 스트릭
            # 판정 대상에서도 마찬가지로 제외(순위 판별 전용 대회라
            # "부진"의 증거로 쓰지 않는다).
            continue
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
            ordinal = _STAGE_TIER_ORDINAL[tier]
            country_best_tier_this_year[country] = max(
                country_best_tier_this_year.get(country, -1), ordinal)

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
        # [2026-09 재현성 버그수정] failed는 국가명(문자열) 집합이라
        # 순회 순서가 PYTHONHASHSEED에 좌우된다. 여기서 채우는
        # country_best_tier_this_year는 아래에서 .items()로 다시 순회하며
        # DB에 streak/페널티를 기록하므로, 삽입 순서가 흔들리면 기록 순서와
        # (부동소수 누적 순서까지) 실행마다 달라진다. 국가명 사전순으로
        # 고정한다 — 각 국가에 들어가는 값 자체는 서로 독립이라(max/합)
        # 순서를 바꿔도 결과값은 동일, 밸런스 무영향.
        for country in sorted(failed):
            country_best_tier_this_year[country] = max(
                country_best_tier_this_year.get(country, -1), -1)
            country_qual_fail_weight[country] = country_qual_fail_weight.get(country, 0.0) + base_weight

    # [2026-09 신설, 부진 스트릭] 위에서 모은 "이 해 최고 성적"을 등급별
    # 기대 스테이지(COUNTRY_EXPECTED_TIER_FLOOR)와 비교해 streak를
    # 갱신하고, 기대 이하인 나라에는 (a) 예선탈락 페널티가 있으면 그
    # 페널티에 스트릭 배율을 곱하고, (b) 예선탈락이 없어도(본선은
    # 나갔지만 그 등급치고 초라한 성적) 신규 소규모 페널티를 스트릭
    # 배율로 곱해 적용한다. 상승 방향에는 이 로직이 아예 관여하지 않는다
    # (country_best_tier_this_year에 없는, 즉 이 해에 아무 증거도 없는
    # 나라는 streak를 건드리지 않고 그냥 넘어간다).
    for country, best_tier in country_best_tier_this_year.items():
        grade = _get_country_grade(conn, country)
        floor = COUNTRY_EXPECTED_TIER_FLOOR.get(grade, 0)
        is_underperform = best_tier < floor
        old_streak = _get_country_streak(conn, country)
        new_streak = old_streak + 1 if is_underperform else 0
        qual_w = country_qual_fail_weight.get(country)
        if is_underperform:
            bucket = min(new_streak, 5)
            mult = STREAK_PENALTY_MULTIPLIER.get(bucket, STREAK_PENALTY_MULTIPLIER[5])
            if qual_w:
                _add_country_b(conn, country, QUALIFIER_FAIL_BASE_PENALTY * qual_w * mult,
                                evaluation_year)
            else:
                _add_country_b(conn, country, COUNTRY_UNDERPERFORM_BASE_PENALTY * mult,
                                evaluation_year)
        elif qual_w:
            # 이론상 예선탈락(best_tier=-1)은 항상 floor(최소 0) 밑이라
            # is_underperform=True가 되므로 이 분기는 실제로는 안 타지만,
            # 방어적으로 남겨둔다(배율 없이 기본 페널티만).
            _add_country_b(conn, country, QUALIFIER_FAIL_BASE_PENALTY * qual_w, evaluation_year)
        # streak 저장 전, 이 나라 country_power_rating 행이 반드시 있어야
        # 한다(위에서 _add_country_b를 한 번도 안 거쳤을 수 있는 "기대
        # 이상을 해낸" 케이스 방어용 — 델타 0 터치로 존재만 보장).
        _add_country_a(conn, country, 0.0, evaluation_year)
        _set_country_streak(conn, country, new_streak)

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

# [2026-09 조정, 신민용 확정 — "한 시즌 잘한 게 여러 시즌 랭킹에 눌러앉는다"
# 리포트] 기존 0.85(=매년 15%만 시드로 회귀)는 지금처럼 리그 내 OVR
# 스프레드가 좁은 환경(1부 안에서도 명문과 중하위팀 시드 차이가 작음)에서는
# 한 번의 반짝 시즌이 남긴 초과분을 다 걷어내는 데 여러 해가 걸린다 —
# 실측(노팅엄 포레스트: 2006년 한 시즌 만에 26위→6위로 급등한 뒤 2007~2011
# 5시즌 내내 10~19위에 눌러앉음, 원래 수준인 30~40위대로 돌아오지 못함)로
# 확인. 급변(로스터 OVR 8%/15%↑) 트리거의 0.25/0.35 회귀비율은 그대로 두고
# (사용자 지시: "한 번에 하나만 바꿔서 전후 비교"), 기본값만 0.85→0.78
# (=매년 22% 회귀)로 보수적으로 낮춘다 — 사용자가 "0.85→0.7처럼 확 낮추는
# 건 비추천, 우선 0.78로 테스트"라고 확정한 값. 실제 헤드리스 3시즌
# 시뮬레이션(같은 세이브 사본, 이 값만 바꿔 비교)으로 검증 완료 — 아래
# 검증 기록 참고.
REGRESSION_BASE = 0.78          # 클럽 기본 (1-수렴비율) [2026-09: 0.85→0.78]
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
    teams = _drop_mil_teams(conn, teams)   # [2026-10] 군팀 제외
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
        _power_debug_log(conn, team_id, "A", f"regress(seed={seed_ps:.1f},conv={convergence:.2f})", new_a - a)
        _power_debug_log(conn, team_id, "B", f"decay(rate={CLUB_B_DECAY_RATE:.2f})", new_b - b)
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
    countries = _drop_mil_countries(countries)   # [2026-10] 군대 제외
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
    teams = _drop_mil_teams(conn, teams)   # [2026-10] 군팀 제외
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
    # [2026-09 신설] 리그 순위는 "그 리그 팀들의 전체순위 평균"이라 팀 순위가
    # 확정된 직후가 유일하게 정확한 계산 시점이다 — 여기서 그 해분만 저장해
    # 두면 화면 쪽은 저장된 값을 읽기만 한다(ensure_league_power_rankings /
    # hist.league_power_rankings 정의부 주석 참고). 실패해도 시즌 전환 자체를
    #막지는 않는다(다음 조회 때 ensure_league_power_rankings가 다시 시도).
    try:
        ensure_league_power_rankings(conn)
    except Exception as _e:
        print(f"[LPR] 리그 파워랭킹 저장 실패(계속 진행): {_e}")
    return entries


def compute_country_power_rankings(conn, evaluation_year: int) -> list:
    ranking_year = evaluation_year + 1
    ensure_power_ranking_tables(conn)
    countries = conn.execute("SELECT name, continent FROM countries").fetchall()
    countries = _drop_mil_countries(countries)   # [2026-10] 군대 제외
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
    import time as _time_pr
    _p0 = _time_pr.perf_counter()
    # [2026-08 신설, 성능] 이번 배치(연 1회) 동안만 유효한 _team_ab_cache를
    # 매번 새로 비운다 — 위 _team_ab_cache 선언부 설명 참고.
    _team_ab_cache.clear()
    _team_streak_cache.clear()
    _country_streak_cache.clear()

    ensure_power_ranking_tables(conn)
    ensure_initial_team_power_ranking(conn)
    ensure_initial_country_power_ranking(conn)

    # [2026-08 신설] 파이썬 콘솔에서 set_power_debug_teams_by_name()을 따로
    # 호출할 필요 없이, 켜져 있으면(DEBUG_POWER_RANKING_TRACKING=True) 여기서
    # _POWER_DEBUG_TEAM_NAMES를 자동으로 team_id로 변환해 등록한다 —
    # 파일 상단 이름 목록만 고치고 게임을 실행하면 그대로 동작.
    if DEBUG_POWER_RANKING_TRACKING and not _POWER_DEBUG_TEAM_IDS:
        found = set_power_debug_teams_by_name(conn, _POWER_DEBUG_TEAM_NAMES)
        missing = set(_POWER_DEBUG_TEAM_NAMES) - {n for _, n in found}
        if missing:
            print(f"[POWER-DEBUG] 경고: 이 이름들을 못 찾았습니다(철자 확인): {missing}")

    _power_debug_snapshot(conn, f"{evaluation_year}시즌 시작(계산 전)")
    _p1 = _time_pr.perf_counter()

    update_team_ratings_for_year(conn, evaluation_year)
    _p2 = _time_pr.perf_counter()
    update_team_b_for_year(conn, evaluation_year)
    _p3 = _time_pr.perf_counter()
    update_country_ratings_for_year(conn, evaluation_year)
    _p4 = _time_pr.perf_counter()
    update_country_b_for_year(conn, evaluation_year)
    _p5 = _time_pr.perf_counter()

    league_power_cache = compute_league_power(conn, evaluation_year)
    _p6 = _time_pr.perf_counter()
    apply_team_season_regression(conn, evaluation_year, league_power_cache)
    _p7 = _time_pr.perf_counter()
    apply_country_season_regression(conn, evaluation_year)
    _p8 = _time_pr.perf_counter()

    _power_debug_snapshot(conn, f"{evaluation_year}시즌 종료(회귀/감쇠 후)")

    compute_team_power_rankings(conn, evaluation_year)
    _p9 = _time_pr.perf_counter()
    compute_country_power_rankings(conn, evaluation_year)
    _p10 = _time_pr.perf_counter()

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
    _p11 = _time_pr.perf_counter()

    # [2026-09 신설, 성능 진단] game_engine.py의 "파워랭킹 N초" 한 줄이
    # 이 함수 하나를 통째로 감싸고 있어서, 예전엔 이 안의 9단계(팀/국가
    # A값·B값 갱신, 리그파워 계산, 시즌회귀 2종, 팀/국가 랭킹 산출) 중
    # 무엇이 무거운지 전혀 구분이 안 됐다 — ai_lifecycle의 [PERF] 계측과
    # 같은 목적, 같은 방식(체크포인트 사이 델타)으로 세분화한다.
    _perf_log(
        f"[PERF-POWER] {evaluation_year}년 세부: 초기화+디버그스냅샷 {_p1-_p0:.3f}s | "
        f"팀A값 {_p2-_p1:.3f}s | 팀B값 {_p3-_p2:.3f}s | "
        f"국가A값 {_p4-_p3:.3f}s | 국가B값 {_p5-_p4:.3f}s | "
        f"리그파워계산 {_p6-_p5:.3f}s | 팀시즌회귀 {_p7-_p6:.3f}s | "
        f"국가시즌회귀 {_p8-_p7:.3f}s | 팀랭킹산출 {_p9-_p8:.3f}s | "
        f"국가랭킹산출 {_p10-_p9:.3f}s | opp_strength정리 {_p11-_p10:.3f}s")

    return evaluation_year + 1


# ══════════════════════════════════════════════════════════════
# 16. 초기(게임 시작연도) 시드 저장 — countries.py/OVR 기반, DB에 실제 저장
# ══════════════════════════════════════════════════════════════

def _country_seed_entries(conn) -> list:
    _gsy = get_game_start_year()
    countries = conn.execute("SELECT name, continent FROM countries").fetchall()
    countries = _drop_mil_countries(countries)   # [2026-10] 군대 제외
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
    rows = _drop_mil_teams(conn, rows)   # [2026-10] 군팀 제외
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


# ══════════════════════════════════════════════════════════════
# 리그 파워랭킹 (2026-09 신설)
#
# 신민용 확정 산식: (그 리그 팀들의 '전체 순위' 합) / (그 리그 팀 수).
# 값이 작을수록 강한 리그 — 프리미어리그면 20개 팀의 전체 순위를 더해
# 20으로 나눈다. 팀/국가 파워랭킹처럼 별도 테이블에 적립하지 않고,
# 이미 저장된 team_power_rankings(연도별 전체 순위)에서 그때그때 집계한다
# — 산식이 순위의 평균일 뿐이라 따로 저장할 상태가 없고, 저장해두면
# 오히려 team_power_rankings와 어긋날 위험만 생긴다.
#
# [그 해 어느 리그였나] 팀은 승강으로 리그를 옮기므로 "현재 소속 리그"
# (teams.league_id)로 과거 연도를 집계하면 틀린다. 그 시즌 순위표
# 스냅샷(hist.league_season_standings: league_id·year·team_id)이 정확한
# 근거라 그것을 1순위로 쓰고, 그 해 스냅샷이 없는 팀만 현재 소속으로
# 폴백한다(최신 연도는 대개 폴백 경로이고, 그 경우엔 현재 소속이 곧
# 정답이다).
# ══════════════════════════════════════════════════════════════

# [2026-09 성능, 신민용 리포트: "팀이랑 국가는 클릭하면 바로 이전순위가
# 뜨는데 리그는 딜레이가 2초정도 있고 켜져"] 실측(4시즌 세이브, team_power_
# rankings 45,588행): 팀 파워랭킹 8ms vs 리그 파워랭킹 90ms — 11배 차이.
# 원인은 팀/국가 랭킹은 저장 테이블을 rank 순으로 그냥 읽으면 끝인데,
# 리그 순위는 "그 리그 팀들의 전체순위 평균"이라 매번 집계를 다시 하고,
# 그것도 올해분과 작년분(prev_rank용) 두 번 한다는 것이었다. 한 번 집계에
# team_power_rankings 그 해 전체(11,397행) + 팀→리그 소속표(hist 스냅샷
# 11,397행 + teams 11,397행)를 파이썬으로 훑는다. 60년 세이브면 그 해
# 행 수는 그대로여도 테이블이 68만 행이 되어 인덱스 없이는 스캔 비용이
# 그대로 커진다 — 실제로 사용자가 본 2초가 그 구간이다.
#
# 두 가지로 고친다:
#   1) 아래 두 집계 함수에 세션 캐시를 건다. 지난 연도의 집계 결과는
#      한 번 확정되면 절대 안 바뀌고, 올해분도 그 해 행 수가 그대로면
#      안 바뀐다 — 그래서 캐시 키에 "그 해 행 수"를 넣어 새 랭킹이
#      기록되면 자동으로 무효화된다(수동 무효화 호출이 필요 없다).
#   2) team_power_rankings에 (ranking_year, team_id, rank, evaluation_year)
#      커버링 인덱스를 추가한다(ensure_power_ranking_tables 참고) — 기존
#      PK(ranking_year, team_id)는 rank/evaluation_year를 안 담고 있어
#      행마다 테이블을 한 번 더 찾아야 했다.
_LEAGUE_AGG_CACHE: dict = {}
_LEAGUE_MEMBERSHIP_CACHE: dict = {}
_LEAGUE_AGG_CACHE_MAX = 80   # 연도 수만큼 쌓이므로 상한을 둔다(60년+여유)


def _league_cache_put(cache: dict, key, value):
    if len(cache) >= _LEAGUE_AGG_CACHE_MAX:
        cache.clear()
    cache[key] = value
    return value


def clear_league_power_cache():
    """[2026-09 신설] 세션 캐시 비우기 — 세이브 로드/새 게임처럼 DB 자체가
    바뀌는 시점에 부르면 안전하다(부르지 않아도 캐시 키의 행 수가 달라져
    자동 무효화되지만, 다른 세이브가 우연히 같은 행 수를 가질 수 있으므로
    명시적으로 비울 수 있는 통로를 남긴다)."""
    _LEAGUE_AGG_CACHE.clear()
    _LEAGUE_MEMBERSHIP_CACHE.clear()


def _league_membership_for_year(conn, evaluation_year: int) -> dict:
    """{team_id: league_id} — evaluation_year 시즌에 그 팀이 뛴 리그.
    hist 스냅샷 우선, 없으면 teams.league_id 폴백.
    [2026-09 성능] 결과를 세션 캐시에 담는다 — 위 캐시 주석 참고. 키에
    "그 해 스냅샷 행 수 + 현재 teams 행 수"를 넣어, 스냅샷이 새로 쓰이거나
    팀이 생기면 자동으로 다시 계산된다."""
    try:
        _sig = (
            conn.execute("SELECT COUNT(*) FROM hist.league_season_standings WHERE year=?",
                         (evaluation_year,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM teams WHERE league_id IS NOT NULL").fetchone()[0],
        )
    except Exception:
        _sig = None
    if _sig is not None:
        _hit = _LEAGUE_MEMBERSHIP_CACHE.get((evaluation_year, _sig))
        if _hit is not None:
            return _hit
    membership = {}
    try:
        for tid, lid in conn.execute(
                "SELECT team_id, league_id FROM hist.league_season_standings WHERE year=?",
                (evaluation_year,)).fetchall():
            if lid:
                membership[tid] = lid
    except Exception:
        pass   # hist가 아직 없는 새 세이브 등 — 아래 폴백만 쓴다
    for tid, lid in conn.execute(
            "SELECT id, league_id FROM teams WHERE league_id IS NOT NULL").fetchall():
        membership.setdefault(tid, lid)
    if _sig is not None:
        _league_cache_put(_LEAGUE_MEMBERSHIP_CACHE, (evaluation_year, _sig), membership)
    return membership


def _aggregate_league_ranks(conn, ranking_year: int) -> dict:
    """{league_id: (avg_rank, n_teams, evaluation_year)} — 위 산식 그대로.
    team_power_rankings에 그 해 행이 없으면 빈 dict.
    [2026-09 성능] 결과를 세션 캐시에 담는다 — 위 캐시 주석 참고. 키의
    "그 해 행 수"가 달라지면(새 시즌 랭킹이 기록되면) 자동 무효화된다."""
    try:
        _n_rows = conn.execute(
            "SELECT COUNT(*) FROM team_power_rankings WHERE ranking_year=?",
            (ranking_year,)).fetchone()[0]
    except Exception:
        _n_rows = None
    if _n_rows == 0:
        return {}
    if _n_rows is not None:
        _hit = _LEAGUE_AGG_CACHE.get((ranking_year, _n_rows))
        if _hit is not None:
            return _hit
    rows = conn.execute(
        """SELECT team_id, rank, evaluation_year FROM team_power_rankings
           WHERE ranking_year=?""", (ranking_year,)).fetchall()
    if not rows:
        return {}
    evaluation_year = rows[0][2]
    membership = _league_membership_for_year(conn, evaluation_year)
    acc = {}
    for tid, rank, _ev in rows:
        lid = membership.get(tid)
        if not lid or rank is None:
            continue
        s, n = acc.get(lid, (0, 0))
        acc[lid] = (s + rank, n + 1)
    _out = {lid: (s / n, n, evaluation_year) for lid, (s, n) in acc.items() if n}
    if _n_rows is not None:
        _league_cache_put(_LEAGUE_AGG_CACHE, (ranking_year, _n_rows), _out)
    return _out


def _league_meta(conn) -> dict:
    """{league_id: (league_name, tier, country_name, continent)}."""
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in conn.execute(
        """SELECT l.id, l.name, l.tier, cn.name, cn.continent
           FROM leagues l JOIN countries cn ON l.country_id = cn.id""").fetchall()}


LEAGUE_POWER_RANKING_TABS = TEAM_POWER_RANKING_TABS


def ensure_league_power_rankings(conn, force: bool = False) -> int:
    """[2026-09 신설] hist.league_power_rankings를 team_power_rankings와
    같은 연도까지 채운다(팀/국가 랭킹이 저장 테이블을 쓰는 것과 같은 구조로
    맞춘 것 — 위 테이블 정의부 주석 참고).

    team_power_rankings에는 있는데 이 표엔 없는 ranking_year만 계산하므로,
    새 시즌이 기록될 때는 그 한 해만 채우고, 기존 세이브는 최초 1회에만
    전 연도를 채운다(60년 규모 실측 1초 남짓, 그 뒤로는 영구히 즉시 조회).
    force=True면 전 연도를 다시 계산한다(산식이 바뀐 경우용).
    반환: 새로 채운 (연도 × 리그) 행 수."""
    ensure_power_ranking_tables(conn)
    have = set()
    if not force:
        have = {r[0] for r in conn.execute(
            "SELECT DISTINCT ranking_year FROM league_power_rankings").fetchall()}
    want = {r[0] for r in conn.execute(
        "SELECT DISTINCT ranking_year FROM team_power_rankings").fetchall()}
    todo = sorted(want - have)
    if not todo:
        return 0
    meta = _league_meta(conn)
    # 빠진 해가 하나면 그 해만, 여러 해면 한 번의 전체 집계가 훨씬 싸다.
    if len(todo) == 1:
        by_year = {todo[0]: _aggregate_league_ranks(conn, todo[0])}
    else:
        by_year = _aggregate_league_ranks_all_years(conn)
    rows = []
    for y in todo:
        agg = by_year.get(y) or {}
        items = [(avg, meta[lid][0], lid, n, ev)
                 for lid, (avg, n, ev) in agg.items() if lid in meta]
        items.sort(key=lambda x: (x[0], x[1]))   # 평균 오름차순, 동률은 이름순
        # 대륙 순위: 같은 해, 같은 대륙 그룹 안에서 다시 센다(팀 쪽
        # get_team_power_history의 대륙순위와 같은 방식).
        cont_ct: dict = {}
        for i, (avg, _nm, lid, n, ev) in enumerate(items, start=1):
            grp = tuple(_continent_group_for(meta[lid][3]))
            cont_ct[grp] = cont_ct.get(grp, 0) + 1
            rows.append((y, lid, ev, avg, n, i, cont_ct[grp]))
    if rows:
        conn.executemany(
            """INSERT INTO league_power_rankings
                 (ranking_year, league_id, evaluation_year, avg_rank, n_teams,
                  rank, continent_rank)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(ranking_year, league_id) DO UPDATE SET
                 evaluation_year=excluded.evaluation_year, avg_rank=excluded.avg_rank,
                 n_teams=excluded.n_teams, rank=excluded.rank,
                 continent_rank=excluded.continent_rank""", rows)
        conn.commit()
    return len(rows)


def get_league_power_ranking(conn, ranking_year: int, tab: str = "전체",
                             limit: int = 400) -> list:
    """리그 파워랭킹 한 해분. tab이 대륙이면 그 대륙 리그만 남기고 그 안에서
    순위를 다시 매긴다(팀 쪽 get_team_power_ranking_grouped와 같은 원칙 —
    평균값 자체는 항상 '전체 순위' 기준이고, 순번만 선택된 범위 안에서
    다시 센다). prev_rank도 같은 범위에서 작년 순번으로 계산한다."""
    ensure_power_ranking_tables(conn)
    ensure_initial_team_power_ranking(conn)
    # [2026-09 성능] 매번 집계하던 것을 저장 테이블 조회로 바꾼다 —
    # ensure_league_power_rankings/테이블 정의부 주석 참고. 팀/국가 랭킹과
    # 완전히 같은 구조(저장된 rank를 인덱스로 읽기)가 됐다.
    ensure_league_power_rankings(conn)
    meta = _league_meta(conn)
    conts = _TAB_TO_CONTINENTS.get(tab)

    def _ranked(year):
        """[(avg, n, ev, lid, meta), ...] — 저장된 rank 순. tab이 대륙이면
        그 대륙만 남긴다(순번은 아래에서 이 범위 안에서 다시 센다 — 팀 쪽
        get_team_power_ranking_grouped와 같은 원칙)."""
        items = []
        for lid, avg, n, ev in conn.execute(
                """SELECT league_id, avg_rank, n_teams, evaluation_year
                     FROM league_power_rankings WHERE ranking_year=?
                    ORDER BY rank""", (year,)).fetchall():
            m = meta.get(lid)
            if not m:
                continue
            if conts and m[3] not in conts:
                continue
            items.append((avg, n, ev, lid, m))
        return items

    prev_rank_by_lid = {lid: i + 1 for i, (_a, _n, _e, lid, _m)
                        in enumerate(_ranked(ranking_year - 1))}
    out = []
    for i, (avg, n, ev, lid, m) in enumerate(_ranked(ranking_year)):
        if i >= limit:
            break
        out.append(LeaguePowerEntry(
            league_id=lid, league_name=m[0], tier=m[1], country=m[2], continent=m[3],
            avg_rank=avg, n_teams=n, rank=i + 1,
            prev_rank=prev_rank_by_lid.get(lid),
            ranking_year=ranking_year, evaluation_year=ev))
    return out


def _aggregate_league_ranks_all_years(conn) -> dict:
    """[2026-09 신설, 신민용 리포트: "팀이랑 국가는 클릭하면 바로 이전순위가
    뜨는데 리그는 딜레이가 2초정도 있고 켜져"] 모든 연도분 리그 평균순위를
    **쿼리 한 번**으로 집계한다 — {ranking_year: {league_id: (avg, n, ev)}}.

    get_league_power_history가 예전엔 연도마다 _aggregate_league_ranks를
    한 번씩 불렀다(60년이면 60번, 매번 그 해 11,397행 + 소속표 전체를
    파이썬으로 훑음). 실측(60년 규모로 부풀린 세이브, team_power_rankings
    683,820행): 리그 이력창 1,860ms — 사용자가 본 2초가 정확히 이 구간이다.
    팀/국가 이력창은 저장 테이블을 GROUP BY로 한 번 세는 것뿐이라 즉시
    떴으므로(그쪽은 예전에 같은 N+1을 이미 고쳐놨다) 리그만 느렸다.

    팀→리그 소속 판정은 _aggregate_league_ranks와 같다: 그 해(evaluation_
    year) hist 스냅샷 우선, 없으면 teams.league_id 폴백. 스냅샷에 같은 팀이
    한 해에 두 줄 있으면(승강 등 이례적 경우) id가 큰 쪽을 쓰는데, 이는
    파이썬 판본이 "나중에 읽은 행이 덮어쓴다"로 동작했던 것과 같은 결과다.
    """
    rows = conn.execute(
        """SELECT t.ranking_year AS ry,
                  COALESCE(m.league_id, tm.league_id) AS lid,
                  SUM(t.rank) AS s, COUNT(*) AS n, MIN(t.evaluation_year) AS ev
           FROM team_power_rankings t
           LEFT JOIN (SELECT team_id, year, MAX(id) AS _mx, league_id
                        FROM hist.league_season_standings
                       GROUP BY team_id, year) m
                  ON m.team_id = t.team_id AND m.year = t.evaluation_year
           LEFT JOIN teams tm ON tm.id = t.team_id
          WHERE t.rank IS NOT NULL AND COALESCE(m.league_id, tm.league_id) IS NOT NULL
          GROUP BY ry, lid""").fetchall()
    out: dict = {}
    for ry, lid, ssum, n, ev in rows:
        if not n:
            continue
        out.setdefault(ry, {})[lid] = (ssum / n, n, ev)
    # 연도별 행 수를 같이 구해 단일연도 캐시(_LEAGUE_AGG_CACHE)에도 심어둔다
    # — 이력창을 한 번 열면 그 뒤의 파워랭킹 표 조회가 전부 캐시에 걸린다.
    try:
        for ry, n_rows in conn.execute(
                "SELECT ranking_year, COUNT(*) FROM team_power_rankings "
                "GROUP BY ranking_year").fetchall():
            if ry in out:
                _league_cache_put(_LEAGUE_AGG_CACHE, (ry, n_rows), out[ry])
    except Exception:
        pass
    return out


def get_league_power_history(conn, league_id: int) -> list:
    """[2026-09 신설, 신민용 요청: "새롭게 만들 리그도 연도 | 전체 순위 |
    대륙 순위 이렇게 표시되게"] (ranking_year, 전체순위, 대륙순위) 최신
    연도부터. 리그 순위는 저장 테이블이 아니라 집계값이라 연도마다 한 번씩
    집계해야 한다 — 연도 수만큼 반복되지만 team_power_rankings 조회 한 번 +
    파이썬 합산이고, 이력 창은 더블클릭할 때 한 번만 열린다."""
    ensure_power_ranking_tables(conn)
    meta = _league_meta(conn)
    m = meta.get(league_id)
    if not m:
        return []
    my_conts = _continent_group_for(m[3])
    # [2026-09 성능] 연도마다 전세계 집계를 다시 하던 N+1(실측 60년 규모
    # 1,860ms)을 저장 테이블 조회 한 번으로 바꾼다 — 팀/국가 이력창과
    # 완전히 같은 구조가 됐다(ensure_league_power_rankings 주석 참고).
    ensure_league_power_rankings(conn)
    return [(y, rk, crk) for y, rk, crk in conn.execute(
        """SELECT ranking_year, rank, continent_rank FROM league_power_rankings
            WHERE league_id=? ORDER BY ranking_year DESC""", (league_id,)).fetchall()]


def get_country_power_history_with_continent(conn, country: str) -> list:
    """[2026-09 신설, 신민용 요청: "국가에도 대륙순위 추가"]
    (ranking_year, 전체순위, 대륙순위) 최신 연도부터 — 대륙 순위는 팀 쪽
    get_team_power_history과 완전히 같은 방식(같은 해, 같은 대륙 범위에서
    rank <= 내 rank인 국가 수)으로 세며, 연도 수와 무관하게 쿼리 2번으로
    끝낸다(그쪽의 N+1 수정과 같은 GROUP BY 집계)."""
    ensure_power_ranking_tables(conn)
    ensure_initial_country_power_ranking(conn)
    rows = conn.execute(
        """SELECT ranking_year, rank, continent FROM country_power_rankings
           WHERE country=? ORDER BY ranking_year DESC""", (country,)).fetchall()
    if not rows:
        return []
    years_by_group = {}
    for r in rows:
        years_by_group.setdefault(tuple(_continent_group_for(r[2])), []).append(r[0])
    cont_rank = {}
    for group in years_by_group:
        placeholders = ",".join("?" * len(group))
        for y, cnt in conn.execute(
                f"""SELECT p.ranking_year, COUNT(*)
                    FROM country_power_rankings p
                    JOIN country_power_rankings me
                      ON me.country=? AND me.ranking_year=p.ranking_year
                    WHERE p.continent IN ({placeholders}) AND p.rank<=me.rank
                    GROUP BY p.ranking_year""",
                (country, *group)).fetchall():
            cont_rank[y] = cnt
    return [(r[0], r[1], cont_rank.get(r[0], 1)) for r in rows]


def get_team_power_history(conn, team_id: int) -> list:
    """[2026-08 확장, 신민용 요청: "파워랭킹에서 팀 클릭하면 뜨는 전체
    순위/대륙 순위에 국가 내 순위도 추가해달라"] 대륙 순위(continent
    범위 안에서 rank<= 자기 rank인 팀 수)와 같은 방식으로, 같은 나라
    (country) 안에서의 순위도 같이 계산해 반환한다 — 반환 튜플이
    (ranking_year, rank, continent_rank)에서 (ranking_year, rank,
    continent_rank, country_rank) 4개로 늘어난다."""
    ensure_power_ranking_tables(conn)
    rows = conn.execute(
        """SELECT ranking_year, rank, continent, country FROM team_power_rankings
           WHERE team_id=? ORDER BY ranking_year DESC""", (team_id,)).fetchall()
    if not rows:
        return []

    # [2026-09 성능 수정, 감사 항목 4위 "연차 수에 비례하는 N+1"] 예전엔
    # 위에서 가져온 연도 행을 파이썬 for문으로 돌면서 연도마다 COUNT(*)를
    # 2번(대륙 순위 1 + 국가 순위 1)씩 개별 실행했다 — 즉 쿼리 수가
    # '연도 수 × 2'로, 게임을 오래 할수록 팀 하나 클릭할 때마다 선형으로
    # 계속 늘어났다(15년차 최대 30개, 150년차면 300개). 세계기록실에서
    # 팀 상세를 열 때마다 매번 발생하는 UI 경로라 체감이 크다.
    #
    # 같은 계산을 '연도별 GROUP BY 집계' 2개로 바꿔, 쿼리 횟수를 연도 수와
    # 무관하게 고정한다(총 3회: 본문 1 + 대륙 1 + 국가 1). 계산식 자체는
    # 완전히 동일하다 — "그 해, 같은 범위 안에서 rank가 나보다 작거나 같은
    # 팀의 수"를 세는 것이고, 자기 자신도 항상 포함되므로(rank<=rank) 결과
    # 값은 예전과 100% 같다. 반환 튜플 형식/순서도 그대로.
    #
    # 대륙 그룹(_continent_group_for)이 연도마다 다를 수 있으므로(팀의
    # continent 값이 바뀐 아주 드문 경우) 그룹별로 나눠 집계한다 — 실제로는
    # 거의 항상 그룹이 1개라 쿼리도 1번이다.
    years_by_group = {}
    for r in rows:
        group = tuple(_continent_group_for(r[2]))
        years_by_group.setdefault(group, []).append(r[0])

    cont_rank = {}
    for group, _years in years_by_group.items():
        placeholders = ",".join("?" * len(group))
        for y, cnt in conn.execute(
                f"""SELECT p.ranking_year, COUNT(*)
                    FROM team_power_rankings p
                    JOIN team_power_rankings me
                      ON me.team_id=? AND me.ranking_year=p.ranking_year
                    WHERE p.continent IN ({placeholders}) AND p.rank<=me.rank
                    GROUP BY p.ranking_year""",
                (team_id, *group)).fetchall():
            cont_rank[y] = cnt

    country_rank = {}
    for y, cnt in conn.execute(
            """SELECT p.ranking_year, COUNT(*)
               FROM team_power_rankings p
               JOIN team_power_rankings me
                 ON me.team_id=? AND me.ranking_year=p.ranking_year
               WHERE p.country=me.country AND p.rank<=me.rank
               GROUP BY p.ranking_year""", (team_id,)).fetchall():
        country_rank[y] = cnt

    # 집계에서 빠진 연도(이론상 없음 — 자기 자신이 항상 한 건 잡힌다)는
    # 예전 코드와 똑같이 전체 rank를 그대로 대체값으로 쓴다.
    return [(ranking_year, rank,
             cont_rank.get(ranking_year, rank),
             country_rank.get(ranking_year, rank))
            for ranking_year, rank, continent, country in rows]


def get_country_power_history(conn, country: str) -> list:
    ensure_power_ranking_tables(conn)
    rows = conn.execute(
        """SELECT ranking_year, rank FROM country_power_rankings
           WHERE country=? ORDER BY ranking_year DESC""", (country,)).fetchall()
    return [(r[0], r[1]) for r in rows]