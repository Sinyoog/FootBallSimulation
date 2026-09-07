"""
ai_lifecycle.py — AI 선수 생애 주기 시스템

시즌 종료 시(_end_of_season) 한 번 호출되어 다음을 처리한다:
  1. 나이 +1
  2. 성장(젊은 선수 OVR↑) / 노화(노쇠 선수 OVR↓)
  3. 은퇴(고령) → 신인으로 교체
  4. 이적 시장 (선수들 팀 간 이동 — 활발하게)
  5. 포메이션 변경 (일부 팀, 감독 교체 컨셉)

결과적으로 같은 팀에 오래 있어도 매 시즌 스쿼드/전력/포메가 살아 움직인다.
ai_players.ovr / team_id 가 바뀌므로 마지막에 OVR 캐시를 무효화해야 한다.

설계 메모:
  - 내(my_player)와 무관. 오직 ai_players / teams 만 건드린다.
  - calc_ovr·_gen_ai_stats·_target_ovr 등 database.py의 기존 생성 로직을 재사용.
  - 노화/성장은 '스탯' 자체를 조정하고 ovr를 재계산한다(스탯-ovr 일관성 유지).
"""
import random
import math
import bisect   # [2026-09 신설] _find_buy_replacement의 OVR 구간 탐색용
import contextlib   # [2026-08 최적화] _indexes_off_for_mass_update용
import economy as _economy   # [2026-09 최적화] 이적료 산정 배치 캐시 begin/end용
from database import (get_conn, calc_ovr, ALL_STATS, KEY_STATS_BY_POS,
                      roll_bench_position)
# [2026-08 신설, 포메이션 20개 확장 + 스쿼드 적합도 시스템] 모듈 레벨에서
# _FORMATIONS 동기화(아래)와 _shuffle_formations()에 필요.
# [2026-08 신설, 세계 축구 기록실 연도별 평점/골/도움 요약] legs_for_team_count
# 는 _snapshot_season_ratings가 리그별 실제 풀시즌 경기수를 구할 때 쓴다
# (game_engine._league_full_season_matches와 동일 공식).
from constants import FORMATION_SLOTS, FORMATION_REEVAL_PROB, legs_for_team_count

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# [2026-09 최적화, 대체자탐색] _find_buy_replacement의 "전세계 후보" 경로만
# numpy 마스크로 바꾼다. 실측(24시즌차 실제 세이브, 2024 오프시즌):
#   전세계 경로  1,867회 × 평균 6,619명 = 12,358,293회 파이썬 루프 (스캔의 98.5%)
#   자국 경로   3,718회 × 평균    50명 =    185,047회            (1.5%)
# 이 루프의 필터는 84.3%가 생존한다 — 6,619명을 훑어 5,577명 리스트를 만들고
# 거기서 1명만 뽑는 구조라, "덜 거르게" 만들 여지가 없고 반복 횟수 자체를
# 줄여야 한다. 후보의 순서·가중치·난수 소비를 그대로 두고 마스크 계산만
# C 레벨로 내린다(프로토타입 실측 15.7배, 결과는 비트 단위로 동일 —
# np.cumsum이 itertools.accumulate와 같은 순차 누적이라 부동소수점까지
# 일치하고, random.choices와 마찬가지로 random()을 정확히 1회만 쓴다).
# 문제가 생기면 이 플래그만 False로 내리면 예전 파이썬 경로로 즉시 복귀한다
# (양쪽 코드가 나란히 남아 있고, 자국 경로는 애초에 안 건드렸다).
USE_NUMPY_GLOBAL_POOL = True

# ── [2026-08 신설, 신민용 요청: "명문팀 강등 스노우볼이 실제로
#    _retire_and_replace 때문인지 시즌별로 확인하고 싶다"] ──────────
# 평소엔 완전히 꺼진 상태(오버헤드 0)이고, DEBUG_PRESTIGE_TRACKING=True로
# 켰을 때만 지정된 팀들의 "은퇴/신인 교체가 실제로 어떤 OVR을 만들어내는지"를
# 시즌마다 한 줄로 콘솔에 남긴다. DB 스키마는 안 건드리고(세션 메모리만
# 사용), game_engine.DEBUG_RELEGATION_TRACKING(강등 순간 4시점 스냅샷)과는
# 별개 도구 — 이건 "매 시즌 은퇴자 교체가 스쿼드를 어느 쪽으로 끌고
# 가는지"를 시계열로 보기 위한 것이라 상호보완적이다.
DEBUG_PRESTIGE_TRACKING = False
DEBUG_PRESTIGE_TEAMS = {
    "토트넘 홋스퍼", "맨체스터 시티", "첼시", "아스널",
    "AC 밀란", "레알 마드리드", "FC 바이에른 뮌헨",
}


# ── 나이 분포/임계값 ──────────────────────────────────────────
_AI_MIN_AGE      = 16
_AI_NEWBIE_AGE   = (16, 21)   # 신인 영입 연령대
# [2026-08 4차 재설계, 신민용 확정(GPT 협업)] constants.
# AGE_OVR_FRACTION_MATURE_AGE(나이별 성장곡선 표가 100%에 도달하는
# 나이, 26)와 맞춰 성장 종료를 25로 늦췄다 — 예전엔 22였는데, 새
# 곡선(16세70%→25세98%→26세100%)은 22~25세 구간에도 완만한 성장분이
# 남아있어야 하기 때문. database._generate_team_players/이 파일의
# 신인 생성(아래 참고) 모두 같은 표(constants.AGE_OVR_FRACTION)를
# 공유하므로 "성장 종료 나이" 하나만 여기서 어긋나지 않게 한다.
_AI_PEAK_START   = 25         # 성장 종료(피크 진입)
_AI_PEAK_END     = 29         # 노화 시작

# [2026-09 신설, 신민용 리포트: "초기 선수들 OVR이 서서히 내려오는듯? 37년
# 돌린거 기준 가장 높은 애가 95, 그것도 4명뿐"] 헤드리스 시뮬레이션으로
# 진단 완료 — _retire_and_replace가 신인에게 배정하는 성인 잠재치(target,
# 최상위 리그면 95~98까지도 나옴)와, 실제로 성장기(16~25세) 동안 도달하는
# OVR 사이에 원래도 큰 간극이 있었다(디버그: target=98, 16세 데뷔 300명
# 평균 → 25세 시점 겨우 73.5, 시즌당 성장 겨우 0.39 — 9시즌을 다 채워도
# 격차의 15%도 못 좁힌다). 피크기(25~29세)는 ±1 소폭 드리프트뿐이라 그
# 이후로도 격차가 안 좁혀지고 그대로 굳는다. 반면 최초 세계 생성
# (database._generate_team_players)은 나이와 무관하게 이미 26세 이상인
# 선수 다수를 target 그대로(스케일 없음) 심어서, 초반 세계에는 이런
# "성장 부족"을 겪지 않은 고OVR 선수가 많았다 — 그 선수들이 은퇴하며
# _retire_and_replace 신인으로 교체될 때마다 세계 전체의 상단(최고 OVR)이
# 조금씩 깎여나간 것(수십 년 누적되면 사용자가 본 것처럼 세계 최고 OVR이
# 95 근처로 계속 수렴). 아래 두 상수로 성장기의 "터치하는 스탯 수"와
# "한 번 터치할 때 남은 격차 대비 회복 비율"을 둘 다 올려서, 9시즌
# 안에서도 target 근처(수 점 이내)까지 현실적으로 도달하도록 재보정했다
# (헤드리스 시뮬레이션 300명 표본 재검증: target=98·16세 데뷔 25세 평균
# 88.9로 상승, target=95·21세 데뷔는 93.8까지 도달 — 여전히 전원이 정확히
# target을 채우진 않지만 "몇 시즌 성장하면 에이스급 근처"라는 원래 설계
# 의도에 맞게 격차가 정상 범위로 좁혀짐). _age_and_progress_np/_py 양쪽
# 모두 이 두 상수를 쓴다.
_AI_GROWTH_TOUCHES = (4, 8)     # 성장기 한 시즌에 건드리는 스탯 개수(범위)
_AI_GROWTH_CATCHUP_FRAC = 0.35  # 한 번 터치할 때 (팀 상한-현재값) 중 회복하는 비율

# [2026-09 신설, 신민용 리포트: "명문팀(성장상한99)에서 자라도 97~99가
# 거의 안 나온다"] 위 _AI_GROWTH_TOUCHES는 "70% 확률로 핵심스탯 5개 중
# 하나, 30% 확률로 15개 스탯 전체 중 하나"로 한 터치풀을 공유했다 —
# 그런데 포지션별 OVR 가중치(database.WEIGHTS)는 핵심스탯이 대략 절반,
# 나머지 10개 비핵심스탯이 나머지 절반을 차지한다. 그 결과 실측(세이브
# 검증): 성장상한99팀 24~26세 선수 핵심스탯 평균 96.1(상한 근접) vs
# 비핵심스탯 평균 81.9(초기값 근처에 정체) — OVR은 가중평균이라 비핵심
# 스탯이 못 따라오는 만큼 최종 OVR이 95~96대에서 막히고, 97~99는 사실상
# 안 나왔다(실제 세이브: 생존 25.6만명 중 97 딱 1명, 역대 71.7만명
# 통틀어도 98/97 각 1명뿐).
# 신민용이 직접 제시한 목표(명문팀 졸업생 기준): 92~94=40~50%(주전급),
# 95~96=20~30%(에이스급), 97~98=5~8%(월드클래스), 99=1~2%(역사적
# 최정상급). 이 표에 맞춰 헤드리스 시뮬레이션(디버트나이 16~20 편차+
# 도중 이적으로 성장상한99팀 합류 시점 편차까지 모델링)으로 스윕 검증한
# 결과: "핵심스탯 터치풀과 완전히 분리된, 같은 크기의 비핵심스탯 전용
# 터치풀"을 추가하는 게 가장 근접했다(<92:27.9%, 92~94:39.8%,
# 95~96:25.9%, 97~98:6.3%, 99: 극희귀하지만 도달 가능 — 나머지 버킷은
# 전부 요청 범위 안에 들어옴). 기존 "70/30 공유풀" 방식은 폐기하고,
# 핵심스탯 터치는 이제 100% 핵심스탯만(희석 없이), 비핵심스탯은 완전히
# 별도의 동일 크기 터치예산(_AI_GROWTH_TOUCHES_NONKEY)을 받는다 — 사실상
# 시즌당 총 터치수가 늘어나 핵심스탯 수렴도 더 좋아지고, 비핵심스탯도
# 처음으로 의미 있게 상한에 다가간다. 99는 "1% 조숙형 특급 유망주
# (constants.AGE_OVR_FRACTION_ELITE) + 데뷔부터 은퇴급 없이 성장상한99
# 팀에서 9년 풀타임 성장"이 동시에 맞아떨어져야 하는 극희귀 케이스로
# 남는다 — "역사적 최정상급"이라는 취지에 맞다고 판단, 별도로 손대지
# 않음(건드리면 다른 시스템에도 영향).
_AI_GROWTH_TOUCHES_NONKEY = (4, 8)  # 비핵심스탯 전용 터치 예산(핵심과 동일 크기, 완전 별도 풀)


# [2026-09 신설, 성능 진단] 아래 [PERF*] 계측은 여태 print()로만 나갔다 —
# PyQt 앱은 콘솔이 안 보이는 경우가 많고, live_sim.log에는 game_engine.
# _live_debug로 나간 줄만 담겨서 'AI생애주기 47초'라는 합계만 보이고 그
# 안의 어느 서브단계가 무거운지는 알 수 없었다(전체 시뮬의 61%가 블랙박스).
# print는 그대로 두고 같은 줄을 로그에도 남긴다 — 계측 출력만 늘 뿐
# 게임 로직·결과에는 전혀 영향이 없다.
def _perf_log(msg):
    print(msg)
    try:
        from game_engine import _live_debug   # 순환 import 회피용 지연 import
        _live_debug(msg)
    except Exception:
        pass


def _youth_target_scale(target, age):
    """[2026-08 4차 재설계] 16~24세 신인의 target(성인 잠재치)을
    constants.roll_age_ovr_fraction(나이별 명시적 표, 1% 확률 조숙형
    포함)로 낮춘다 — database._generate_team_players(최초 생성)와
    완전히 같은 표를 써서 두 생성 경로가 항상 일치하게 한다. 예전엔
    이 함수 자체가 없어서(또는 낡은 선형보간이라) 신인이 나이와
    무관하게 거의 성인 잠재치 그대로 태어났었다(실측: 명문팀 16세
    OVR89, 17세 OVR98)."""
    from constants import roll_age_ovr_fraction
    return target * roll_age_ovr_fraction(age)


# [2026-08 4차 재설계, 신민용 확정(GPT 협업): "은퇴 확률이 국가/리그
# 등급에만 의존하고 부수(tier)는 전혀 반영하지 않는다" 리포트 및
# 근본 재설계] 예전 표는 country_grade(SS~F)만 보고 tier는 완전히
# 무시했다 — 그래서 잉글랜드 1부(맨시티)와 잉글랜드 7부가 완전히
# 같은 은퇴 확률을 가졌다. 이번엔:
#   1) 국가등급 + "그 나라 안에서의 상대적 부수 깊이"를 합쳐 5단계
#      리그강도 카테고리(top/midhigh/mid/low/bottom)로 매핑
#      (_retire_league_category) — "7부까지 있는 나라는 6~7부,
#      5부까지인 나라는 5부가 그 나라의 최하위"가 되도록 절대 tier가
#      아니라 국가별 최대 tier 대비 비율(depth_ratio)을 쓴다.
#   2) 나이 구간별 은퇴 비율 표를 5개 밴드(24세 이전/25~29/30~34/
#      35~39/40~45)로 새로 설계 — "하부리그=오래 뛴다"가 아니라
#      "하부리그=은퇴 시점의 분산이 크다"(일찍 그만두는 선수도, 40대
#      까지 뛰는 선수도 둘 다 많다)는 형태로, 밴드 총합을 그대로
#      쓰고 밴드 내부만 나이별로 완만하게 배분한다.
#   3) 국가대표/월드컵 출전 경력이 있으면 30세 미만 조기 은퇴 확률에
#      배율(0.5 / 0.2)을 곱해 억제한다 — "월드컵 나갈 정도면 20대
#      후반 은퇴는 이상하다"를 반영. 해저드(조건부 확률) 모델이라
#      일부러 재분배 코드를 따로 두지 않아도, 조기 은퇴가 줄면
#      자연히 그만큼 더 오래 생존해 나이대가 뒤로 밀린다.
_RETIRE_CATEGORIES5 = ("top", "midhigh", "mid", "low", "bottom")

# 밴드별 5카테고리 은퇴 비율(%, 각 열 합계 100) — 신민용 확정표.
_RETIRE_BAND_PCT = {
    "u24":   (1.0, 3.0, 6.0, 12.0, 18.0),
    "25_29": (3.0, 7.0, 12.0, 20.0, 25.0),
    "30_34": (15.0, 20.0, 25.0, 25.0, 25.0),
    "35_39": (55.0, 50.0, 42.0, 30.0, 22.0),
    "40_45": (26.0, 20.0, 15.0, 13.0, 10.0),
}
_RETIRE_BAND_AGES = {
    "u24":   [18, 19, 20, 21, 22, 23, 24],
    "25_29": [25, 26, 27, 28, 29],
    "30_34": [30, 31, 32, 33, 34],
    "35_39": [35, 36, 37, 38, 39],
    "40_45": [40, 41, 42, 43, 44, 45],
}
# 밴드 내부 나이별 상대 가중치(완만한 굴곡만 — 밴드 합계 자체는 위 표를
# 그대로 따름). 40대는 "45세에 몰리는 인위적 벽"을 막기 위해 40세
# 쪽이 더 많고 45세로 갈수록 줄어드는 모양을 준다.
_RETIRE_BAND_SHAPE = {
    "u24":   [1.0, 1.0, 1.0, 1.3, 1.5, 1.8, 2.2],
    "25_29": [1.0, 1.1, 1.2, 1.3, 1.4],
    "30_34": [1.0, 1.05, 1.1, 1.05, 1.0],
    "35_39": [1.1, 1.05, 1.0, 0.95, 0.9],
    "40_45": [1.6, 1.4, 1.2, 1.0, 0.8, 0.6],
}


def _build_retire_pct_table():
    table = {}
    for band, ages in _RETIRE_BAND_AGES.items():
        shape = _RETIRE_BAND_SHAPE[band]
        shape_sum = sum(shape)
        for ci in range(len(_RETIRE_CATEGORIES5)):
            band_total = _RETIRE_BAND_PCT[band][ci]
            for age, w in zip(ages, shape):
                table.setdefault(age, [0.0] * len(_RETIRE_CATEGORIES5))
                table[age][ci] = band_total * (w / shape_sum)
    return {age: tuple(vals) for age, vals in table.items()}


_AI_RETIRE_PROB_PCT = _build_retire_pct_table()


def _retire_league_category(grade: str, tier: int, max_tier: int) -> str:
    """국가등급(SS~F) + 그 나라 안에서의 상대적 부수 깊이를 합쳐 5단계
    카테고리로 매핑. depth_ratio=0이면 그 나라의 1부(최상위), 1이면
    그 나라의 최심부(예: 7부까지 있으면 7부, 5부까지면 5부) — 절대
    tier 숫자가 아니라 나라별 최대 tier 대비 비율이라, "7부제 나라의
    6~7부"와 "5부제 나라의 5부"가 똑같이 '그 나라의 바닥'으로 취급된다."""
    _grade_score = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
    gscore = _grade_score.get(grade, 4)
    if max_tier and max_tier > 1:
        depth_ratio = max(0.0, min(1.0, (tier - 1) / (max_tier - 1)))
    else:
        depth_ratio = 0.0
    combined = gscore - depth_ratio * 7.0
    if combined >= 6.5:
        return "top"
    if combined >= 5.0:
        return "midhigh"
    if combined >= 3.3:
        return "mid"
    if combined >= 1.7:
        return "low"
    return "bottom"


def _build_retire_hazard_table():
    """[2026-08 신설] _AI_RETIRE_PROB_PCT(각 나이에 "은퇴할" 무조건부
    확률, 카테고리별 합계 100%)를 실제 시뮬레이션에 필요한 "그 나이까지
    살아남은 사람 중 이번 해에 은퇴할 조건부 확률"(해저드)로 변환한다 —
    무조건부 확률을 그대로 매 시즌 굴리면(이전까지 이미 은퇴한 사람
    비율을 안 빼면) 실제 은퇴 비율이 표보다 훨씬 낮게 나온다(생존자
    분모가 계속 줄어드는 걸 반영 안 하면). 표는 모듈 로드 시 한 번만
    변환해 캐싱한다."""
    ages = sorted(_AI_RETIRE_PROB_PCT.keys())
    table = {cat: {} for cat in _RETIRE_CATEGORIES5}
    for ci, cat in enumerate(_RETIRE_CATEGORIES5):
        survive_pct = 100.0
        for age in ages:
            p = _AI_RETIRE_PROB_PCT[age][ci]
            hazard = (p / survive_pct) if survive_pct > 0 else 1.0
            table[cat][age] = min(1.0, hazard)
            survive_pct -= p
    return table


_AI_RETIRE_HAZARD_TABLE = _build_retire_hazard_table()

_AI_RETIRE_AGE = 18  # 나이 기반 판정을 시작하는 나이


def _ages_well(player_id: int) -> bool:
    """[2026-08 신설, 신민용 요청: "29세 이후 바로 꺾이는 애들도 있고
    34세까진 그래도 괜찮게 꺾이는 애들이 있게 하고 싶다 — 관리를 잘하면
    99에서 92 정도로만 꺾이고 못하면 원래대로 더 내려가는데, 반반씩
    나와야 한다"] 이 선수가 "관리를 잘하는" 쪽인지 판정한다. 매 시즌
    다시 뽑으면 어느 해엔 관리를 잘하다 다음 해엔 못 하다 왔다갔다
    하게 되어 부자연스러우므로, player_id 기반 결정적 해시로 커리어
    내내 고정된 값을 쓴다 — id는 선수마다 유일하고 사실상 무작위로
    배정되므로 이 해시 결과도 자연히 정확히 반반(짝/홀)으로 갈리고,
    별도 DB 컬럼 없이 항상 같은 값이 재현된다.
    [2026-09] 노화 로직 자체는 아래 _mgmt_tier_and_mult(5단계)로
    교체됐지만, 이 함수는 다른 곳에서 참조할 수도 있어 그대로 남겨둔다."""
    return ((player_id * 2654435761) & 0xFFFFFFFF) % 2 == 0


# [2026-09 재설계, 신민용 리포트: "노화가 너무 후해 — 28세95→40세84~87은
# 5대리그급 선수가 40세에도 안 은퇴하고 버티게 만든다"] 예전 _ages_well
# (관리 잘함/못함 반반, 하락 '횟수'만 다르게)는 목표치가 아예 없는
# 방식이라 실측 40세 평균이 84.8~87.4에 그쳤다(사용자 목표 대비 15점+
# 차이, 실제 게임함수로 2,000명 헤드리스 검증 완료). "나이별 목표
# 하락률을 먼저 정하고 그 안에서 어떤 스탯을 뺄지 결정"(사용자 명시
# 요청)하는 구조로 전면 교체한다.
#
# [1단계] 기준곡선: "평균적인 자기관리" 선수가 전성기(29세) OVR 대비
# 나이별로 몇 % 하락해야 하는지의 누적비율표. 신민용이 최종 확정한
# 목표표(95 시작 기준, 구간은 중간값 사용 — 33세90~91→90.5,
# 34세88~89→88.5, 35세86~88→87, 36세84~86→85, 37세81~84→82.5,
# 38세78~81→79.5, 39세75~78→76.5, 40세72~76→74, 40세 평균 목표는
# 73~76 — "40세 69~72"였던 1차안은 "너무 극단적"이라는 본인 피드백으로
# 이번에 상향 조정됨)를 "전성기 대비 누적 하락률"로 환산했다(40세 기준
# (95-74)/95=22.1% 하락). 41세 이후는 목표표가 없어 39→40 구간 직전
# 몇 년의 평균 증가폭(연 약 2.9%p)을 그대로 이어 외삽했다 — 이 구간은
# 추정치이므로 실측 후 조정 가능.
_AGING_DECLINE_SCHEDULE = {
    29: 0.0000, 30: 0.0105, 31: 0.0211, 32: 0.0316, 33: 0.0474,
    34: 0.0684, 35: 0.0842, 36: 0.1053, 37: 0.1316, 38: 0.1632,
    39: 0.1947, 40: 0.2211, 41: 0.2495, 42: 0.2789, 43: 0.3095,
    44: 0.3411, 45: 0.3737,
}
_AGING_DECLINE_MAX_AGE = max(_AGING_DECLINE_SCHEDULE)
_AGING_DECLINE_LAST_STEP = (_AGING_DECLINE_SCHEDULE[_AGING_DECLINE_MAX_AGE]
                             - _AGING_DECLINE_SCHEDULE[_AGING_DECLINE_MAX_AGE - 1])


def _aging_base_decline_pct(age: int) -> float:
    """전성기(29세) 대비 이 나이의 "기준" 누적 하락률(자기관리 보정 전).
    45세를 넘어가는 초고령 선수는 마지막 구간 증가폭을 그대로 이어
    선형 외삽한다(표 밖은 실측 데이터가 없는 추정 구간)."""
    if age <= 29:
        return 0.0
    if age in _AGING_DECLINE_SCHEDULE:
        return _AGING_DECLINE_SCHEDULE[age]
    if age > _AGING_DECLINE_MAX_AGE:
        return (_AGING_DECLINE_SCHEDULE[_AGING_DECLINE_MAX_AGE]
                + _AGING_DECLINE_LAST_STEP * (age - _AGING_DECLINE_MAX_AGE))
    return 0.0  # 방어적 폴백(정수 나이라 이론상 도달 안 함)


# [2단계] 자기관리 등급 — 신민용이 직접 제시한 5단계 확률/보정폭.
#   "매우좋음 10%/좋음 25%/보통 45%/나쁨 15%/매우나쁨 5%"
#   각 등급 내에서도 보정폭 범위 안에서 개인별로 살짝씩 달라진다.
# direction: -1=기준 하락률을 줄임(더 완만하게 늙음), +1=늘림(더 가파르게).
_MGMT_TIERS = (
    # (등급명, 누적확률상한, (보정폭 최소,최대), 방향)
    ("매우좋음", 0.10, (0.15, 0.25), -1),
    ("좋음",     0.35, (0.05, 0.15), -1),
    ("보통",     0.80, (0.00, 0.00),  0),
    ("나쁨",     0.95, (0.10, 0.20), +1),
    ("매우나쁨", 1.00, (0.25, 0.40), +1),
)


def _mgmt_tier_and_mult(player_id: int):
    """이 선수의 자기관리 등급과 하락률 보정계수(-0.40~+0.40 범위)를
    player_id 기반 결정적 해시로 고정 배정한다(_ages_well과 같은 철학
    — 매 시즌 다시 뽑지 않고 커리어 내내 유지, 별도 DB 컬럼 불필요).
    서로 다른 두 해시를 써서 (1)등급 자체와 (2)그 등급 안에서의 구체적
    보정폭이 독립적으로 갈리게 한다. 보정계수가 음수면 기준 하락률보다
    덜 떨어지는(자기관리가 좋은) 선수, 양수면 더 가파르게 떨어지는
    선수다."""
    h1 = ((player_id * 2654435761) & 0xFFFFFFFF) / 0xFFFFFFFF
    h2 = ((player_id * 40503 + 12345) & 0xFFFFFFFF) / 0xFFFFFFFF
    for name, cum, (lo, hi), direction in _MGMT_TIERS:
        if h1 < cum:
            return name, direction * (lo + h2 * (hi - lo))
    return "보통", 0.0


def _aging_target_ovr(peak_ovr: int, age: int, player_id: int) -> int:
    """전성기 OVR·나이·개인 자기관리 보정을 종합해 "이 나이의 목표
    OVR"을 계산한다. 노화 로직은 매 시즌 이 목표치와 현재 OVR의 차이만큼만
    스탯을 깎는다(사용자 명시 요청: "연령별 목표 하락량을 먼저 정하고 그
    안에서 어떤 스탯이 떨어질지를 결정하는 구조")."""
    base_pct = _aging_base_decline_pct(age)
    _, mult = _mgmt_tier_and_mult(player_id)
    eff_pct = base_pct * (1.0 + mult)
    eff_pct = max(0.0, min(0.75, eff_pct))  # 안전 클램프(음수 하락/과도한 폭락 방지)
    return max(15, int(round(peak_ovr * (1.0 - eff_pct))))


# [2026-09 신설, 신민용 리포트: "OVR71인 26세가 은퇴하는게 최상위
# 리그면 몰라도 K리그 같은 곳은 걔가 에이스잖아 — 같은 리그·같은 부수
# 안에서도 그 팀/그 리그 기준으로 에이스인지 겨우 버티는 선수인지는
# 다른데 지금은 똑같이 취급된다"] 지금까지 _ai_retirement_probability는
# ovr 파라미터를 받아놓고도 실제로는 전혀 안 썼다(카테고리=리그등급+
# 부수깊이, 나이만 봄) — 그래서 같은 리그의 에이스와 후보가 나이만
# 같으면 은퇴 확률도 완전히 같았다. get_ovr_range(그 리그·부수의 신인
# 생성 기준 범위 — "이 리그에서 통상적인 수준"의 기존 대리 지표를 그대로
# 재사용)와 비교해, 그 범위 상단 위(확실한 에이스)면 은퇴를 줄이고 하단
# 아래(그 리그에서도 힘든 수준)면 늘린다. 나이표가 여전히 주된 축이고
# 이건 보정폭만 담당하도록 0.6~1.5배로 좁게 클램프했다 — "K리그 에이스가
# 절대 은퇴 안 함" 같은 극단이 나오면 안 되므로.
def _percentile_curve_mult(p: float) -> float:
    """[2026-09 2차 재설계, 신민용 피드백: "중앙값 +/- 선형보다 범위 내
    위치(percentile)로, 그리고 0.6~1.5는 너무 넓다"] p=(ovr-lo)/(hi-lo) —
    0이면 그 리그·부수 범위의 최하단, 1이면 최상단. 구간별 목표 배율을
    직접 점으로 찍어두고 그 사이는 선형보간, 범위를 벗어나는 값은 양
    끝점에서 클램프한다(리그를 훨씬 초월해도 무한히 계속 낮아지지 않게
    — "리그 초월 선수의 추가 감소는 강하게 주지 않는 게 좋다"는 요청)."""
    pts = ((-0.5, 1.25), (0.0, 1.20), (0.25, 1.10), (0.5, 1.00),
           (0.75, 0.92), (1.0, 0.85), (1.5, 0.78))
    if p <= pts[0][0]:
        return pts[0][1]
    if p >= pts[-1][0]:
        return pts[-1][1]
    for (p0, m0), (p1, m1) in zip(pts, pts[1:]):
        if p0 <= p <= p1:
            t = (p - p0) / (p1 - p0)
            return m0 + (m1 - m0) * t
    return 1.0


def _relative_ovr_retire_mult(ovr, grade, tier, country, age, max_tier=None) -> float:
    """[2026-09 신설, 신민용 리포트: "K리그 에이스가 벤치멤버랑 똑같은
    확률로 은퇴하는 게 이상하다"] → [2026-09 2차 재설계, 신민용 피드백
    3건 반영]
    1) 중앙값 기준 z-score 선형 대신 범위 내 위치(percentile) 곡선으로
       교체(_percentile_curve_mult), 배율 폭도 0.6~1.5→0.75~1.25로 축소.
    2) 나이·OVR 역할 분리: "나이=주 원인, 리그 내 OVR=보조 원인"이어야
       하므로 21~25세는 이 보정을 거의 무시하고(age_weight≈0), 35세
       이상에서 온전히(age_weight=1.0) 적용되도록 나이로 선형 램프를
       건다 — 어린 선수가 리그 수준 좀 낮다고 바로 은퇴 쪽으로 밀리면
       안 되고, 나이 든 선수일수록 "이 수준에서 계속 뛸 이유가 있는가"
       판단에 OVR이 더 크게 작용해야 자연스럽다는 지적.
    3) "OVR 낮음 → 은퇴"로 직결하면 안 되고 "OVR 낮음 → 하위 리그 이적"
       이 먼저이며, 은퇴는 그마저 갈 곳이 없을 때(이미 그 나라 최심부
       tier)만 강하게 반영해야 한다는 지적 — 하위권 쪽(mult>1.0)은
       max_tier 미만(아직 내려갈 하위 리그가 있음)이면 25%만 반영하고,
       이미 최심부(더 내려갈 데 없음)면 전량 반영한다. 상위권 쪽
       (에이스, mult<1.0)은 부수 무관하게 그대로 — 에이스는 어차피 은퇴
       할 이유가 적다는 결론은 부수 깊이와 무관하다."""
    from constants import get_ovr_range
    ovr_rng = get_ovr_range(grade, tier, country)
    if not ovr_rng or not ovr:
        return 1.0
    lo, hi = ovr_rng
    span = max(1.0, hi - lo)
    p = (ovr - lo) / span
    mult = _percentile_curve_mult(p)

    # 3) 하위권(mult>1.0)만 부수 깊이로 게이팅 — 아직 내려갈 하위 리그가
    # 있으면 은퇴 압력을 25%만, 이미 그 나라 최심부면 전량 반영.
    if mult > 1.0 and max_tier and max_tier > 1 and tier < max_tier:
        mult = 1.0 + (mult - 1.0) * 0.25

    # 2) 나이 램프 — 25세 이하는 사실상 무시, 35세부터 전량 반영.
    age_weight = max(0.0, min(1.0, (age - 25) / 10.0))
    mult = 1.0 + (mult - 1.0) * age_weight

    return max(0.75, min(1.25, mult))


# [2026-09 신설, 신민용 요청: "토니 크로스처럼 아직 충분히 뛸 수 있어도
# 최상위 무대에서 커리어를 마무리하고 싶어하는 선수도 있어야 한다 —
# 모든 노장에게 적용하면 사우디/MLS로 가는 현실적인 선수들이 사라지니까
# 개인 성향을 확률적으로 부여하는 게 핵심"] 대다수(약 55%)는 이 성향
# 자체가 없다(career_finish_bonus=0, 기존 로직과 100% 동일) — 나머지만
# player_id 기반 결정적 해시로 등급별 소량의 "커리어 완성형" 성향을
# 고정 배정받는다(_mgmt_tier_and_mult와 동일 철학, 매 시즌 안 바뀜).
_CAREER_FINISH_TIERS = (
    # (성향명, 누적확률상한, 32세+·최상위(S/SS,1부) 소속일 때의 연간 가산확률)
    ("무관심", 0.55, 0.000),
    ("약함",   0.80, 0.010),
    ("보통",   0.93, 0.025),
    ("강함",   1.00, 0.050),
)


def _career_finish_bonus(player_id: int, age: int, grade: str, tier: int) -> float:
    """이 성향은 경쟁력 기반 relative_mult와 완전히 독립적으로 은퇴확률에
    "더해진다"(곱하지 않음) — 그래야 OVR이 여전히 높아 relative_mult가
    은퇴를 억제 중이어도, 이 가산분만으로 "충분히 더 뛸 수 있지만 여기서
    끝낸다"가 가능해진다. 32세 미만이거나 지금 최상위(S/SS, 1부)에서
    뛰고 있지 않으면 0 — "정상급 무대에서 마무리"라는 성향의 정의상 그
    무대에 있을 때만 발동해야 하고, 어린 선수·하위 리그 선수에게 이
    보정이 붙으면 안 되므로."""
    if age < 32 or grade not in ("S", "SS") or tier != 1:
        return 0.0
    h = ((player_id * 2971215073 + 555555555) & 0xFFFFFFFF) / 0xFFFFFFFF
    for _name, cum, bonus in _CAREER_FINISH_TIERS:
        if h < cum:
            return bonus
    return 0.0


def _ai_retirement_probability(age, ovr, position, category="mid", intl_factor=1.0,
                                relative_mult=1.0, career_finish_bonus=0.0):
    """[2026-08 4차 재설계] 나이 + (국가등급×부수깊이) 카테고리 기반
    "이번 해에 은퇴할 확률". intl_factor는 국가대표/월드컵 경력에 따른
    조기 은퇴 억제 배율(호출부에서 계산, _retire_and_replace 참고) —
    30세 미만 구간에만 곱한다(국제경력은 "조기 은퇴"만 억제할 뿐 은퇴
    자체를 막는 조건이 아니어야 하므로 30세 이상은 원 표 그대로).
    [2026-09 신설] relative_mult(_relative_ovr_retire_mult 참고)는 "그
    리그 기준 에이스인지 겨우 버티는 수준인지"를 반영 — intl_factor와
    달리 나이 제한 없이 전 연령에 적용한다(에이스는 나이 들어서도 계속
    현역으로 뛰는 게 자연스러우므로 30세 이후에도 계속 억제돼야 함).
    [2026-09 신설] career_finish_bonus(_career_finish_bonus 참고)는
    "실력과 무관한 은퇴 성향"이라 곱하지 않고 더한다 — relative_mult가
    아무리 낮아도(에이스라 은퇴를 강하게 억제 중이어도) 이 가산으로
    "잘할 수 있었지만 스스로 마무리한" 케이스가 만들어질 수 있어야
    하므로."""
    if age < 18:
        return 0.0
    if age > 45:
        return 1.0
    p = _AI_RETIRE_HAZARD_TABLE.get(category, _AI_RETIRE_HAZARD_TABLE["mid"]).get(
        age, 1.0 if age >= 45 else 0.0)
    if age < 30:
        p *= intl_factor
    p *= relative_mult
    p += career_finish_bonus
    return min(1.0, p)

# [2026-08 버그수정, 신민용 확정: 포메이션 20개 확장] 예전엔 이 리스트가
# constants.FORMATION_SLOTS와 따로 하드코딩돼 있어서(7개, 심지어 그때도
# 이미 미묘하게 순서/구성이 달랐다) 하나만 고치고 잊어버리는 사고가 날 수
# 있었다 — 이제 FORMATION_SLOTS 키를 그대로 가져와 항상 동기화한다.
_FORMATIONS = list(FORMATION_SLOTS.keys())

# ALL_STATS 인덱스 선조회 (반복 list.index 방지)
_STAT_COLS = ",".join(ALL_STATS)
_PHYS_STATS = {"stamina", "speed", "jump", "strength"}
# [2026-08 신설] random.choice는 set을 못 받으므로(인덱싱 불가) 리스트
# 버전도 따로 둔다 — "관리를 잘하는" 선수의 완만한 노화 감소(옛 방식)에 사용.
_AGING_PHYS_STATS = ["stamina", "speed", "jump", "strength"]

if _HAS_NUMPY:
    from database import STAT_IDX, _WEIGHT_IDX_ITEMS, _WEIGHT_SUMS
    _N_STATS = len(ALL_STATS)
    _PHYS_IDX_NP = np.array([STAT_IDX[s] for s in ["stamina", "speed", "jump", "strength"]])
    _DEFAULT_KEY_IDX_NP = np.array([STAT_IDX[s] for s in ALL_STATS[:5]])
    _KEY_IDX_BY_POS_NP = {
        pos: np.array([STAT_IDX[s] for s in keys]) for pos, keys in KEY_STATS_BY_POS.items()
    }
    # [2026-09 신설, _AI_GROWTH_TOUCHES_NONKEY 정의부 주석 참고] 핵심스탯의
    # 여집합(그 포지션 핵심 5개를 뺀 나머지) — 비핵심스탯 전용 성장 터치풀에 사용.
    _DEFAULT_NONKEY_IDX_NP = np.array([STAT_IDX[s] for s in ALL_STATS[5:]])
    _NONKEY_IDX_BY_POS_NP = {
        pos: np.array([STAT_IDX[s] for s in ALL_STATS if s not in keys])
        for pos, keys in KEY_STATS_BY_POS.items()
    }
    # 포지션별 OVR 가중치를 (15,) 벡터로 1회 캐싱 (매 시즌 재구성 방지)
    _WEIGHT_VEC_NP = {}
    for _pos, _items in _WEIGHT_IDX_ITEMS.items():
        _wv = np.zeros(_N_STATS)
        for _idx, _wt in _items:
            _wv[_idx] = _wt
        _WEIGHT_VEC_NP[_pos] = _wv


def run_ai_offseason(year, verbose_log=None, progress_cb=None, my_team_id=None, team_goals_for=None,
                      skip_season_snapshot=False):
    """시즌 종료 시 1회 호출. AI 선수 생애주기 전체 처리.
    skip_season_snapshot: [2026-09 신설] True면 아래 _snapshot_season_ratings
    호출을 건너뛴다 — game_engine._end_of_season이 개인수상 판정
    (_process_awards)보다 먼저 이걸 이미 직접 호출해둔 경우(자연스러운
    시즌종료 경로는 항상 이렇게 호출됨) 여기서 또 하면 같은 시즌이 다른
    랜덤값으로 덮어써져 세계기록실 표시값과 수상판정값이 다시 어긋난다.
    verbose_log: add_log 함수(있으면 요약 한 줄 남김).
    my_team_id: [2026-08 신설] 넘기면 그 팀이 관여한 이적(방출/영입)을
    verbose_log에 전부 남긴다(_transfer_market으로 그대로 전달).
    [2026-08 신설, 신민용 요청: "시즌 전환 처리 중... 이거 얼마나 남았는지
    표시 안 되나"] progress_cb: callable(done:int, total:int, label:str)
    형태의 콜백(있으면 4단계 각각 시작 시 1회씩 호출) — UI 쪽(center_panel.py
    _AdvanceWorker)이 이걸로 진행률 바를 갱신한다. None이면(헤드리스 실행
    등) 그냥 무시되며 기존 동작과 완전히 동일하다."""
    import time as _time_perf
    _TOTAL_STAGES = 4
    def _report(done, label):
        if progress_cb:
            try:
                progress_cb(done, _TOTAL_STAGES, label)
            except Exception:
                pass   # UI 콜백 실패로 시즌전환 자체가 죽으면 안 됨
    conn = get_conn()
    c = conn.cursor()

    # [2026-07 계측 추가, 신민용 리포트: "AI생애주기 합계 1.93s인데 실제
    # 2.59s — 0.66s 미계측"] 기존 _ta0~_ta4는 ensure_ai_ages/ensure_ai_sub_roles
    # 이후에 시작하고 commit/캐시무효화는 범위 밖이라 이 구간들이 안 보였다.
    # 원인 확정 전이므로 로직은 그대로 두고 타이머만 촘촘히 추가한다.
    _t_start = _time_perf.perf_counter()
    _report(0, "선수 나이·성장 처리 중")
    _ensure_ai_ages(c)               # 구버전 세이브 age 보정
    _ensure_ai_sub_roles(c)          # 구버전 세이브 sub_role 보정
    _t_ensure = _time_perf.perf_counter()
    _ta0 = _t_ensure
    grew, aged = _age_and_progress(c)   # 자체적으로 전용 컬럼 SELECT (포지션 위치접근 최적화라 별도 유지)
    _ta1 = _time_perf.perf_counter()

    # [최적화] _retire_and_replace와 _transfer_market이 각자 따로 부르던
    # "SELECT ... FROM ai_players"(전체 행) 2회를 1회로 통합해 공유한다.
    # 두 함수가 필요로 하는 컬럼(id,team_id,position,age,name,ovr)이 동일
    # 상위집합이라 안전하게 합칠 수 있다 — 로직/결과는 완전히 동일, 풀스캔
    # 횟수만 3회→2회로 감소. (ovr은 _transfer_market의 실력 기반 이적 가중치용)
    shared_ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, "
        "contract_end_year, last_transfer_year FROM ai_players ORDER BY id").fetchall()
    _t_shared = _time_perf.perf_counter()

    # [2026-08 신설, 신민용 요청: "선수 검색에서 OVR이 이적 순간에만
    # 찍히던데, 1년 단위로 그 해 OVR이 다 찍혀있어야 한다"] 방금 _age_
    # and_progress로 이 해의 성장/노화가 전부 반영된 shared_ai_rows를
    # 그대로 재사용해서(추가 쿼리 없음) 전 선수 OVR을 한 번에 아카이브
    # 한다 — 은퇴 예정자도 이 시점엔 아직 ai_players에 남아있으므로
    # "은퇴하는 그 해"까지 정상적으로 기록된다.
    c.executemany(
        "INSERT OR REPLACE INTO hist.ai_player_ovr_history(player_id, year, ovr) VALUES (?,?,?)",
        [(r["id"], year, r["ovr"]) for r in shared_ai_rows])

    # [2026-08 신설, 신민용 리포트: "1년씩 진행하면 기록되는데 10년을
    # 한번에 진행하면 기록이 안 되는 경우가 있다"] 원인 추정: 이 함수
    # 전체가 맨 끝(파일 하단 conn.commit())까지 하나의 트랜잭션이라,
    # 이후 단계(은퇴/이적시장/스쿼드보정 등)에서 어쩌다 예외가 나면
    # _end_of_season의 바깥 try/except가 조용히 삼키고 넘어가면서 —
    # 이미 끝난 나이·성장 갱신과 방금 위에서 쓴 OVR 아카이브까지 전부
    # 커밋 안 된 채로 통째로 날아갔다(여러 해를 한 번에 돌릴수록 그
    # 예외가 한 번이라도 날 확률이 누적되어 높아짐 — 1년씩이면 상대적으로
    # 덜 겪었을 뿐 근본 원인은 같음). 나이·성장·이번 해 OVR 아카이브가
    # 끝난 여기서 한 번 먼저 커밋해, 이후 단계에서 뭔가 실패해도 최소한
    # "나이 +1과 이번 해 OVR 기록"만큼은 항상 살아남게 한다.
    conn.commit()

    # [2026-08 신설, 신민용 리포트: "OVR 기록이 2000/2001/2002년 다 비어있다
    # — 기록이 되는 경우도 있고 아닌 경우도 있다"] 위 아카이브(227~229줄)는
    # 이 시즌 시작 시점의 ai_players만 담고 있어서, 이 시즌 도중 새로
    # 생긴 선수(은퇴자 대신 태어난 16세 신인 — _retire_and_replace / 이적
    # 후 스쿼드 인원이 부족해 보충되는 유망주 — _rebalance_squad_sizes,
    # 둘 다 아래에서 실행됨)는 이 스냅샷에 아예 없다 — 그래서 그 선수들의
    # 데뷔 연도(year)는 영원히 archive가 안 되고 그 다음 시즌부터만
    # 기록되는 들쭉날쭉한 현상이 있었다. 이 시즌이 시작될 때의 id 집합을
    # 기억해뒀다가, 이 함수 맨 끝(모든 신규 생성이 다 끝난 뒤)에서 "그때는
    # 없었는데 지금 생긴" id만 한 번에 추려 그 선수들도 데뷔 연도로
    # archive한다(아래 "신규 선수 데뷔연도 archive" 참고).
    _season_start_ids = {r["id"] for r in shared_ai_rows}
    # [2026-09 계측] 아래 '은퇴·세대교체' 구간은 사실 (1)전 선수 OVR 이력
    # 아카이브 (2)전 선수 포지션 스냅샷 (3)평점 스냅샷 (4)진짜 은퇴 처리가
    # 뭉쳐 있어서 10~15초가 어디서 나는지 구분이 안 됐다(PERF-LIFECYCLE의
    # ms/명도 은퇴자 수로 나눠 실제보다 과대평가된 값이었다). 쪼갠다.
    _t_ovrarch = _time_perf.perf_counter()

    # [2026-08 버그수정, 신민용 리포트: "은퇴 선수 마지막 팀에서 역할이
    # -로 뜬다"] 은퇴·이적으로 로스터가 흔들리기 "전"에 이번 시즌을
    # 실제로 뛴 상태 그대로를 먼저 스냅샷한다(상세는 _snapshot_season_
    # positions 주석 참고). 맨 아래에서 이번 오프시즌 신규 선수만
    # 한 번 더 보충한다.
    _snapshot_season_positions(c, year, rows=shared_ai_rows)
    _t_snappos = _time_perf.perf_counter()
    # [2026-08 신설, 세계 축구 기록실 연도별 평점/골/도움 요약] 같은
    # 이유(로스터가 바뀌기 전, "이번 시즌을 실제로 뛴" 팀 기준)로 여기서
    # 같이 스냅샷한다 — shared_ai_rows는 sub_role/league_id가 없어 그대로
    # 재사용할 수 없으므로 이 함수 내부에서 자체 쿼리한다.
    # [2026-09 버그수정, 신민용 리포트: "run_ai_offseason() got an unexpected
    # keyword argument 'team_goals_for'"] 호출부(game_engine.py)는 이미 이번
    # 시즌 팀별 실제 goals_for를 스냅샷해서 넘기고 있었는데("Tier B 준비"
    # 주석 참고), 이 함수 시그니처에 받는 파라미터 자체가 없어서 매 시즌
    # 전환이 이 지점에서 즉시 TypeError로 죽고 있었다 — 그 뒤(은퇴/이적/
    # 포메이션 스냅샷 전부)가 통째로 스킵된 채 바깥 try/except 로그만
    # 남았다. 우선 받아서 실제로 쓰도록 연결한다.
    if not skip_season_snapshot:
        _snapshot_season_ratings(c, year, team_goals_for=team_goals_for)
    # [2026-09 신설] 국가대표 평점/골/어시 스냅샷 — 위와 완전히 같은
    # 타이밍(로스터가 은퇴/이적으로 바뀌기 전, 이 해 대회가 이미 끝난
    # 시점)에 호출한다.
    # [2026-09 버그수정, 신민용 지적: "국제대회 개인 활약을 발롱도르에
    # 반영해야 하는데 이 스냅샷이 없어서 못 쓴다"고 생각했으나, 실제로는
    # 이 스냅샷 자체가 위 _snapshot_season_ratings와 똑같은 문제를 안고
    # 있었다 — game_engine._end_of_season은 _compute_season_individual_
    # awards(발롱도르 계산)보다 먼저 _snapshot_season_ratings만 조기
    # 호출해두고 skip_season_snapshot=True로 여기서 또 안 돌게 막았는데,
    # 이 국가대표 스냅샷은 그 가드 밖에 있어서 "발롱도르 계산 시점엔
    # 아직 없고, 한참 뒤 여기서야 채워지는" 순서 문제가 있었다. 이제 위와
    # 같은 skip_season_snapshot 가드 안으로 옮긴다 — game_engine.py 쪽에도
    # 같은 조기 호출을 추가했다(아래 game_engine._end_of_season "1.4단계"
    # 참고).
    if not skip_season_snapshot:
        _snapshot_intl_season_ratings(c, year)

    # [2026-09 버그수정, 신민용 리포트: "세계 기록실에서 특정 연도만
    # 포메이션 기록이 없다 — 시작 연도만 떠있고 그 다음 해부터 사라진다"]
    # 위 나이/OVR 아카이브 직후엔 이미 조기 커밋을 해뒀는데(바로 위
    # "1년씩 진행하면 기록되는데 10년을 한번에 진행하면..." 주석 참고),
    # 그 조기 커밋 "이후"에 실행되는 이 세 스냅샷(포메이션/평점/국가대표
    # 평점)은 정작 보호 대상에서 빠져 있었다 — 이 함수 끝까지 커밋이
    # 안 되므로, 바로 아래(은퇴 처리)부터 시작하는 이적시장/스쿼드
    # 재조정/포메이션 셔플 등 훨씬 복잡한 단계들 중 어디서든 예외가 나면
    # _end_of_season 바깥 try/except가 조용히 삼키면서 이 세 스냅샷까지
    # 통째로 롤백돼 사라졌다 — 그 시즌 자체(순위/전적/컵 결과 등은 다른
    # 트랜잭션)는 멀쩡히 남는데 포메이션 기록만 유독 빠지는 게 바로 이
    # 경로다. 위와 같은 원칙으로 여기서도 한 번 커밋해 방어한다.
    conn.commit()

    _t_snaprate = _time_perf.perf_counter()
    _report(1, "은퇴 및 신인 영입 중")
    retired    = _retire_and_replace(c, year, shared_ai_rows)
    _ta2 = _time_perf.perf_counter()
    # [2026-08 버그수정] _retire_and_replace가 이제 은퇴자 행을 UPDATE가
    # 아니라 DELETE+INSERT로 처리하므로(위 함수 docstring 참고), 여기
    # shared_ai_rows(은퇴 처리 전에 떠둔 스냅샷)를 그대로 _transfer_market에
    # 넘기면 방금 삭제된 은퇴자의 옛 id가 섞여 있고 새로 태어난 신인은
    # 아예 빠져 있다 — 이적시장이 이미 사라진 행을 이적시키려 하거나
    # (조용히 무시되긴 하지만) 갓 생긴 신인은 이번 시즌 이적 후보에서
    # 통째로 누락된다. 은퇴 처리 직후 한 번 다시 조회해서 최신 상태로
    # 맞춘다.
    shared_ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, "
        "contract_end_year, last_transfer_year FROM ai_players ORDER BY id").fetchall()

    _report(2, "전세계 이적시장 처리 중")
    # [2026-09 최적화] 이적시장 루프가 도는 동안은 경기가 단 한 경기도
    # 치러지지 않아 리그 순위표가 절대 안 바뀐다 — 그 구간에서만
    # economy._team_rank_status_mult(팀 순위 조회) 결과 재사용을 허용한다.
    # 반드시 try/finally로 닫아야 한다(안 닫으면 다음 주차 경기 결과가
    # 반영 안 된 옛 순위가 계속 쓰인다).
    _economy.begin_fee_batch()
    try:
        moved  = _transfer_market(c, year, shared_ai_rows, verbose_log=verbose_log, my_team_id=my_team_id)
    finally:
        _economy.end_fee_batch()
    _ta3 = _time_perf.perf_counter()
    # [2026-09 신설, 신민용 요청: "명문팀은 상시로 좋은 선수를 스카우팅
    # 해야 한다 — 3급은 압도적인 선수, 2급/1급은 상대적으로 덜한 선수"]
    # 은퇴/이적시장과 완전히 별개인 상시 스카우팅 — _retire_and_replace는
    # 은퇴가 나야만 채우고, _transfer_market은 등급 무관 확률로 도는데,
    # 이건 "은퇴 여부와 무관하게 명문팀만 매 시즌 세계 최상위권을 노리는"
    # 전용 통로. 최신 team_id 반영이 필요해 자체 쿼리한다(위 shared_ai_rows
    # 재조회와 같은 이유).
    scouted    = _prestige_scouting(c, year)
    # [2026-09 신설, 신민용 요청: "이적 종류(이적/임대)도 구분해야 한다"]
    # _transfer_market이 이번 시즌 새로 내보낸 임대 건과는 무관하게,
    # "이전에 나가있던 임대 중 이번에 복귀할 때가 된" 선수를 원 소속팀
    # 으로 되돌린다 — 매 시즌 한 번씩 확인해야 몇 년 전 임대도 놓치지
    # 않는다.
    loan_returned = _process_loan_returns(c, year)
    # [2026-09 신설, 신민용 요청: "계약을 몇년치 했냐인건데... 기간이
    # 늘어나면 연장 이런식으로"] 이번 시즌 이적시장에서 안 팔리고 계약도
    # 만료된 선수를 재계약 처리한다 — _transfer_market 이후에 호출해야
    # "이번 시즌에 실제로 안 팔린 선수만" 대상이 된다.
    renewed = _process_contract_renewals(c, year)
    _ta3b0 = _time_perf.perf_counter()
    # [2026-08 신설, 신민용 리포트: "이적으로 인한 스쿼드 인원 불균형을
    # 보정하는 장치가 없다 — 짧은 팀엔 10대 선수를 추가하고, 자리 못 구한
    # 애들은 은퇴시키면 되잖아, 다 30대까지 뛰는 것도 아니고 20대에
    # 은퇴하는 애들도 있으니"] _do_one_transfer_cached의 강제 1:1
    # 맞트레이드를 줄인 뒤(위 참고) 생긴 부작용 — 은퇴 교체(_retire_and_
    # replace)는 기존 행을 그대로 재활용(UPDATE)할 뿐 팀별 인원수 자체를
    # 새로 늘리거나 줄이지 않으므로, 이적으로 어느 팀이 계속 순유입/
    # 순유출되면 스쿼드 크기가 영구히 벌어진다. 매 시즌 이적 직후, 인원이
    # 너무 적은 팀엔 10대 유망주를 새로 영입(INSERT)하고, 너무 많은 팀은
    # 자리를 못 구한 선수 중 가장 낮은 OVR부터 조기 은퇴(DELETE, 신인
    # 교체 없음)시켜 규모를 되돌린다.
    topped_up, forced_out = _rebalance_squad_sizes(c, year)
    _ta3b = _time_perf.perf_counter()
    _report(3, "포메이션 갱신 중")
    formations = _shuffle_formations(c)
    _t_shuffle = _time_perf.perf_counter()
    # [2026-08 신설, 신민용 요청: "이 시즌에 얘가 어디 포지션을 갔는지가
    # 중요한거야"] 방금 이번 시즌 포메이션이 확정됐으니(바로 위), 그
    # 포메이션대로 로스터를 채웠을 때 각 선수가 맡는 자리를 여기서 같이
    # 스냅샷한다 — "전술변경" 단계 시간에 합산돼 찍히지만(별도 계측 없이
    # 얹음), 실측상 팀당 계산량이 작아(선수 20~30명 vs 슬롯 11개 비교)
    # 시즌 시뮬레이션 전체에 유의미한 지연을 주지 않는다.
    # [2026-08 수정] 본 스냅샷은 이제 위(은퇴 처리 직전)에서 이미 찍었다 —
    # 여기서는 이번 오프시즌에 새로 생긴 선수만 보충한다(이미 기록된
    # 선수의 값은 덮어쓰지 않는다).
    _snapshot_season_positions(c, year, only_missing=True)
    _ta4 = _time_perf.perf_counter()
    _report(4, "시즌 전환 마무리 중")
    # [2026-07 신설, 진단용] game_engine._advance_week의 [PERF] 로그와 짝을
    # 이루는 세부 단계 측정 — "AI생애주기 N초" 중 실제로 어느 서브단계
    # (성장/은퇴·세대교체/이적시장/전술변경)가 무거운지 콘솔에서 바로 보인다.
    _perf_log(f"[PERF]     ai_offseason 세부: ensure(age/subrole) {_t_ensure-_t_start:.2f}s | "
          f"성장/노화({'numpy' if _HAS_NUMPY else 'PURE-PYTHON!'}) {_ta1-_ta0:.2f}s | "
          f"shared_ai_rows조회 {_t_shared-_ta1:.2f}s | "
          f"OVR이력아카이브 {_t_ovrarch-_t_shared:.2f}s | "
          f"포지션스냅샷 {_t_snappos-_t_ovrarch:.2f}s | "
          f"평점스냅샷 {_t_snaprate-_t_snappos:.2f}s | "
          f"은퇴·세대교체 {_ta2-_t_snaprate:.2f}s | 이적시장 {_ta3-_ta2:.2f}s | "
          f"명문팀 스카우팅 {_ta3b0-_ta3:.2f}s | "
          f"스쿼드 인원 보정 {_ta3b-_ta3b0:.2f}s | "
          f"전술셔플 {_t_shuffle-_ta3b:.2f}s | 포지션보충스냅샷 {_ta4-_t_shuffle:.2f}s")
    # [2026-08 신설, 신민용 리포트: "시즌 지날수록 은퇴·세대교체가 느려지는데
    # 처리 대상(은퇴자 수) 자체가 느는 건지 건당 비용이 느는 건지 구분이
    # 안 된다"] 위 [PERF] 줄은 이미 "은퇴·세대교체 X.XXs"를 찍고 있었지만
    # 그 시간 동안 실제로 몇 명을 처리했는지가 같이 안 찍혀서, 로그만
    # 보고는 "대상 증가에 따른 정상적인 비용 증가"인지 "건당 비용 자체가
    # 늘어난 버그"인지 구분할 수 없었다. retired/moved는 이미 계산돼 있는
    # 값이라 여기 한 줄만 추가하면 시즌별로 나란히 비교할 수 있다 —
    # 로직/결과는 전혀 안 건드리고 로그만 추가.
    _perf_log(f"[PERF-LIFECYCLE] {year}년: 은퇴/세대교체 {retired}명 · 이적 {moved}건 · "
          f"명문팀 스카우팅 {scouted}건 · 임대 복귀 {loan_returned}명 · 재계약 {renewed}명 · "
          f"소요시간 {_ta2-_t_snaprate:.3f}s"
          + (f" ({(_ta2-_t_snaprate)/retired*1000:.2f}ms/명)" if retired else ""))

    # [2026-08 신설, 위 _season_start_ids 주석 참고 — "신규 선수 데뷔연도
    # archive"] 이 시즌 동안 새로 생긴 선수 전부(은퇴 대체 신인 +
    # 스쿼드 인원 보정으로 영입된 유망주, 출처 불문)를 한 번에 archive한다
    # — 개별 생성 지점마다 따로 챙기는 대신 여기 한 곳에서 "시즌 시작
    # 때 없었는데 지금 있는 id"만 걸러내므로, 나중에 새 생성 경로가
    # 추가돼도 이 로직을 다시 손 볼 필요가 없다.
    _final_ids_rows = c.execute("SELECT id, ovr FROM ai_players").fetchall()
    _new_this_season = [r for r in _final_ids_rows if r["id"] not in _season_start_ids]
    if _new_this_season:
        c.executemany(
            "INSERT OR REPLACE INTO hist.ai_player_ovr_history(player_id, year, ovr) VALUES (?,?,?)",
            [(r["id"], year, r["ovr"]) for r in _new_this_season])

    # [2026-09 신설, 성능 감사 5위 — 선수 검색 "경력(년)" 필터 상관 서브쿼리
    # 제거] 이 시즌의 OVR 이력 기록(위 두 executemany)과 은퇴 처리
    # (_retire_and_replace)가 모두 끝난 지금 시점에, ai_players/
    # ai_players_retired의 career_years 컬럼을 실제 이력 기준으로 맞춘다.
    # 이 값이 있어야 world_browser의 경력 필터가 후보 행마다 COUNT(*)
    # 서브쿼리를 도는 대신 컬럼 비교 한 번으로 끝난다(값의 정의는 기존
    # 필터와 동일하므로 검색 결과는 바뀌지 않는다 — database.refresh_
    # career_years 주석 참고). 은퇴 선수는 '올해 은퇴한 사람'만 갱신한다.
    _t_cy0 = _time_perf.perf_counter()
    try:
        from database import refresh_career_years
        refresh_career_years(conn, retirement_year=year)
    except Exception as _e:
        _perf_log(f"[PERF-LIFECYCLE] career_years 갱신 건너뜀: {_e}")
    _t_cy1 = _time_perf.perf_counter()
    _perf_log(f"[PERF-LIFECYCLE] {year}년: career_years 갱신 {_t_cy1-_t_cy0:.2f}s")

    _t_commit0 = _time_perf.perf_counter()
    conn.commit()
    conn.close()
    _t_commit1 = _time_perf.perf_counter()

    # OVR/소속이 일괄 변경됨 → 엔진 캐시 무효화
    try:
        from game_engine import _invalidate_team_ovr_cache
        _invalidate_team_ovr_cache()
    except Exception:
        pass
    _t_cache1 = _time_perf.perf_counter()
    _perf_log(f"[PERF-AI]  commit={_t_commit1-_t_commit0:.3f}s | "
          f"cache_invalidate={_t_cache1-_t_commit1:.3f}s")

    if verbose_log:
        _rebalance_txt = f" · 스쿼드 보정(영입 {topped_up}명/조기은퇴 {forced_out}명)" if (topped_up or forced_out) else ""
        verbose_log(
            f"🔄 이적시장 마감: 이적 {moved}건 · 은퇴/세대교체 {retired}명 · "
            f"전술 변경 {formations}팀{_rebalance_txt}", "news", year, 52)

    return {"grew": grew, "aged": aged, "retired": retired,
            "moved": moved, "formations": formations,
            "squad_topped_up": topped_up, "squad_forced_out": forced_out}


def run_ai_mid_season_transfer(year, verbose_log=None, my_team_id=None):
    """[2026-08 신설, 상반기/하반기 이적 기록 분리 기능, 신민용 요청:
    "상황에 따라 중간에도 AI 선수들 이적이 가능하긴 하나 이때는 0~2명
    정도만 이적하게 해줘"] 하반기 시작 주차(SECOND_HALF_START, 겨울
    이적시장 마감 직후)에 game_engine.py._advance_week가 딱 한 번
    호출한다 — run_ai_offseason(연 1회, 시즌 완전히 끝난 뒤 은퇴·세대
    교체까지 포함하는 무거운 전체 생애주기 처리)과 달리, 이건 이적
    시장만 아주 작은 규모(volume_scale=0.15 — 리그 팀 수 기준 오프시즌의
    약 1/10 수준, 20팀 리그면 기대값 3~4건 안팎이라 대부분 팀은 0명,
    일부만 1~2명)로 딱 한 번 더 돌리는 가벼운 호출이다. 은퇴/신인 생성/
    노화·성장/포메이션 변경은 여기서 처리하지 않는다(전부 오프시즌
    전용) — 순수하게 "시즌 도중 이적 창구"만 재현한다.

    반환: 이번에 옮겨간 인원 수(moved)."""
    conn = get_conn()
    c = conn.cursor()
    ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, "
        "contract_end_year, last_transfer_year FROM ai_players ORDER BY id").fetchall()
    # [2026-09 최적화] 이적시장 루프가 도는 동안은 경기가 단 한 경기도
    # 치러지지 않아 리그 순위표가 절대 안 바뀐다 — 그 구간에서만
    # economy._team_rank_status_mult(팀 순위 조회) 결과 재사용을 허용한다.
    # 반드시 try/finally로 닫아야 한다(안 닫으면 다음 주차 경기 결과가
    # 반영 안 된 옛 순위가 계속 쓰인다).
    _economy.begin_fee_batch()
    try:
        moved = _transfer_market(c, year, ai_rows, verbose_log=verbose_log, my_team_id=my_team_id,
                                  volume_scale=0.15, is_mid_season=True)
    finally:
        _economy.end_fee_batch()
    conn.commit()
    conn.close()
    # [2026-08 신설] 이적으로 team_id가 바뀐 선수가 있으므로, 포메이션
    # 화면 캐시도 오프시즌 처리와 동일하게 무효화해야 한다(안 하면 그
    # 시즌이 끝날 때까지 새로 이적한 선수가 옛 팀 소속으로 계속 보임).
    try:
        import ui.formation_widget as _fw
        _fw._ovr_cache_invalidated = True
    except Exception:
        pass
    if verbose_log and moved:
        verbose_log(f"❄ 겨울 이적시장: 이적 {moved}건", "event", year, 32)
    return moved


# ─────────────────────────────────────────────
# 0. 나이 보정 (구버전 세이브: age=0/NULL → 랜덤 부여)
# ─────────────────────────────────────────────
def _ensure_ai_ages(c):
    """[2026-07 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"] 이 보정은
    '구버전 세이브에 남아있던 age=0/NULL'을 고치기 위한 1회성 마이그레이션인데,
    run_ai_offseason이 매 시즌 호출될 때마다 ai_players 10만+ 행을 무조건
    풀스캔하고 있었다(정상 세이브라면 매번 0건 매치라 완전히 낭비 — 실측
    103,323행 스캔에 age 0건/sub_role 0건). age는 이후 _age_and_progress가
    매 시즌 전원에게 항상 값을 채우므로, 한 번 깨끗하다고 확인되면 그
    세이브에선 다시는 더러워질 수 없다 — meta 플래그로 "이 세이브는 이미
    깨끗함"을 기록해두고, 다음 시즌부터는 쿼리 자체를 건너뛴다."""
    try:
        row = c.execute("SELECT value FROM meta WHERE key='ai_ages_clean_v1'").fetchone()
    except Exception:
        row = None
    if row:
        return
    rows = c.execute("SELECT id FROM ai_players WHERE age IS NULL OR age=0").fetchall()
    if rows:
        # [최적화] executemany로 한 번에 처리
        updates = [(int(round(random.triangular(16, 34, 25))), r["id"]) for r in rows]
        c.executemany("UPDATE ai_players SET age=? WHERE id=?", updates)
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ai_ages_clean_v1','1')")


def _ensure_ai_sub_roles(c):
    """[세부역할 2026-07] sub_role 컬럼이 새로 생겨서 기존 세이브엔 빈 값('')
    인 AI 선수가 있다 — 포지션에 맞는 SUB_ROLES 중 하나를 무작위로 채운다.
    (신규 시딩 때는 _generate_team_players가 이미 채우므로 여기선 빈 것만
    골라 보정한다.)

    [2026-07 최적화] _ensure_ai_ages와 동일한 이유로 meta 플래그 가드 추가 —
    한 번 깨끗해지면 다시 더러워질 수 없으므로 매 시즌 풀스캔할 필요가 없다."""
    try:
        row = c.execute("SELECT value FROM meta WHERE key='ai_sub_roles_clean_v1'").fetchone()
    except Exception:
        row = None
    if row:
        return
    from constants import SUB_ROLES
    rows = c.execute(
        "SELECT id, position FROM ai_players WHERE sub_role IS NULL OR sub_role=''").fetchall()
    if rows:
        updates = [(random.choice(SUB_ROLES.get(r["position"], ["기본"])), r["id"]) for r in rows]
        c.executemany("UPDATE ai_players SET sub_role=? WHERE id=?", updates)
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ai_sub_roles_clean_v1','1')")


# ─────────────────────────────────────────────
# 1+2. 나이 +1, 성장/노화
# ─────────────────────────────────────────────
def _age_and_progress(c):
    """모든 AI 선수 나이 +1 후, 연령대별로 스탯 성장/노화 → ovr 재계산.
    [2026-07 개선] numpy가 있으면 전체를 벡터 연산으로 처리(_age_and_progress_np),
    없으면 기존 순수 파이썬 배치 버전(_age_and_progress_py)으로 자동 폴백한다.
    실측(5.9만 명 기준, 52→1 시즌전환의 최대 병목이던 지점): 순수 파이썬 약
    0.35~1.2초(환경별 차이) → numpy 벡터화 약 0.15~0.2초. 팀 수/선수 수가
    늘어날수록(향후 20팀+ 확장 등) 격차가 더 벌어진다 — 파이썬 루프는 선수 수에
    선형 비례해 늘지만, 벡터화 버전은 대부분의 시간이 상수 오버헤드라 훨씬
    완만하게 늘어난다.
    [2026-08 재현성 수정, 신민용 리포트: "같은 시드로 재현해도 성장/노화
    결과가 달라진다"] 원래 이 numpy Generator를 시드 없이(np.random.
    default_rng()) 만들었는데, 이러면 매 실행마다 OS 엔트로피로 새로
    초기화돼 파이썬 random 모듈을 아무리 고정 시드로 돌려도 이 함수가
    뽑는 난수만은 매번 달라졌다 — 그 차이가 선수 OVR → 이적/은퇴 → 팀
    전력 → 리그 결과로 계속 번져나가 몇 시즌 뒤엔 완전히 다른 세계선이
    됐다(200시즌 A/B 밸런스 테스트가 PYTHONHASHSEED=0을 고정해도 완전히
    재현되지 않던 원인). 이제 이미 시드가 고정된 파이썬 random 모듈에서
    시드값을 하나 뽑아 numpy Generator를 초기화한다 — random 모듈 자체의
    시드(예: random.seed(12345))가 같으면 이 함수가 매 시즌 뽑는 난수도
    항상 똑같다. 시드 생성 자체는 사실상 공짜라 numpy 벡터화로 얻은
    속도 이득은 전혀 줄지 않는다. [주의] 이 수정 전/후로 "같은 시드"가
    만들어내는 실제 성장 결과값 자체는 달라진다(수정 전엔 애초에 미정의
    였으므로 이건 "다른 값이 됨"이 아니라 "처음으로 값이 고정됨"에
    가깝다) — 기존에 저장된 세이브의 과거 시즌 기록에는 영향 없음(그
    시점에 이미 계산·저장된 값을 다시 계산하지 않음), 이후 새로 진행하는
    시즌의 성장 난수 값만 이제 시드에 따라 고정된다."""
    from database import STAT_IDX, calc_ovr_from_list, OVR_RANGES
    from constants import CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, get_country_league_grade, get_ovr_range

    # [2026-08 계측 추가, 신민용 리포트: "numpy 쓰는데도 0.71s, 예상보다
    # 느린데?"] numpy 벡터화 버전이 실제로 도는데도 docstring이 적어둔
    # 0.15~0.2s 범위가 아니라 순수 파이썬 범위(0.35~1.2s)만큼 걸렸다 —
    # numpy 연산 자체가 아니라 그 앞뒤(team_cap 조회, 5.9만 행 fetch,
    # DB 쓰기)가 무거운 건 아닌지 구간을 쪼개서 확인한다.
    import time as _time_ap
    _ap_t0 = _time_ap.perf_counter()

    # ── team_id → 성장기 스탯 상한 사전 조회 (선수마다 매번 JOIN 방지) ──
    # 등급별 OVR_RANGES 상단에 대륙보정 + 나라별 미세조정까지 반영해서,
    # 초기 생성 때 쓰는 보정치와 항상 같은 기준으로 성장 상한을 잡는다.
    team_cap: dict = {}
    for r in c.execute(
            """SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname,
                      cn.continent AS continent
               FROM teams t JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall():
        grade = get_country_league_grade(r["cname"])
        # [2026-08] tier1은 COUNTRY_LEAGUE_OVR_OVERRIDE 등록국이면 그 값을 우선.
        rng = get_ovr_range(grade, r["tier"] or 1, r["cname"])
        top = rng[1] if rng else 43
        # [버그수정 2026-07, 신민용 리포트: "이적시장 처리 중 오류: 'float'
        # object cannot be interpreted as an integer"] COUNTRY_OVR_ADJ에
        # 대한민국(1.5)·세르비아(-1.5)·우루과이/콜롬비아/에콰도르(-0.5)처럼
        # 소수점 조정치가 섞여 있어서, 이 값이 그대로 bonus에 더해지면
        # bonus 자체가 float이 되고, 그게 OVR 상한 계산에 계속 실려
        # 내려가다가 결국 아래(신인 교체 로직)의 random.randint(mid, hi)에
        # float가 그대로 들어가 터졌다. 정수 등급 보정치라는 원래 의도대로
        # 여기서 반올림해 int로 확정한다.
        bonus = round(CONTINENT_OVR_BONUS.get(r["continent"], 0) + COUNTRY_OVR_ADJ.get(r["cname"], 0))
        if grade == "SS":
            bonus = min(bonus, 0)
        team_cap[r["tid"]] = min(99, top + bonus + 3)
    _ap_t1 = _time_ap.perf_counter()

    # JOIN에 안 잡힌 팀(league_id/country_id 연결 누락 등)의 폴백 상한.
    _ORPHAN_CAP_FALLBACK = 46

    rows = c.connection.cursor()
    rows.row_factory = None  # 위치 접근만 쓰므로 Row 래핑 생략 (5.9만 행 fetch 오버헤드 절감)
    # [2026-09 신설] ovr(하락 전 현재값)·peak_ovr(전성기 기준점) 추가 —
    # 목표OVR 기반 노화 재설계(_AGING_DECLINE_SCHEDULE 정의부 주석 참고)에 필요.
    rows = rows.execute(
        "SELECT id, position, age, team_id, " + _STAT_COLS + ", ovr, peak_ovr FROM ai_players").fetchall()
    _ap_t2 = _time_ap.perf_counter()
    if not rows:
        return 0, 0

    if _HAS_NUMPY:
        _result = _age_and_progress_np(c, rows, team_cap, _ORPHAN_CAP_FALLBACK)
    else:
        _result = _age_and_progress_py(c, rows, team_cap, _ORPHAN_CAP_FALLBACK)
    _ap_t3 = _time_ap.perf_counter()
    _perf_log(f"[PERF-AGE] _age_and_progress({'numpy' if _HAS_NUMPY else 'python'}) 세부: "
          f"team_cap조회 {_ap_t1-_ap_t0:.3f}s | ai_players fetch({len(rows)}행) {_ap_t2-_ap_t1:.3f}s | "
          f"계산+DB쓰기 {_ap_t3-_ap_t2:.3f}s")
    return _result


# [2026-08 최적화] 전 선수(26만 행) 나이/스탯/OVR 일괄 UPDATE 전용 —
# ai_players에는 ovr이 들어간 인덱스가 2개(idx_aiplayers_nat_pos_ovr,
# idx_aiplayers_ovr_id) 있어서, 한 행을 고칠 때마다 그 인덱스 B-트리에서
# 옛 항목을 지우고 새 항목을 끼워 넣는 일이 행마다 2번씩 일어난다.
# 26만 행을 한꺼번에 갱신할 때는 인덱스를 잠깐 내렸다가 끝나고 한 번에
# 다시 만드는 쪽이 훨씬 싸다(정렬 한 번으로 끝나므로).
#   · 갱신하는 컬럼(age/스탯/ovr)을 실제로 참조하는 인덱스만 내린다 —
#     team_id 인덱스(idx_aiplayers_team)는 이 UPDATE와 무관한데다, 이게
#     없으면 같은 시즌전환 안의 이적시장 팀 조회가 125초까지 폭발한다
#     (실측 확인). 절대 건드리지 않는다.
#   · 중간에 무슨 일이 생겨도 인덱스가 사라진 채로 남지 않도록 finally로
#     반드시 복구한다.
#   · 인덱스는 순수 성능용이라 이 처리로 게임 데이터·결과는 전혀 달라지지 않는다.
_MASS_UPDATE_COLS = ("ovr", "age") + tuple(ALL_STATS)


@contextlib.contextmanager
def _indexes_off_for_mass_update(c):
    dropped = []
    try:
        for r in c.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='ai_players' "
                "AND sql IS NOT NULL").fetchall():
            _name, _sql = r[0], r[1]
            _cols = _sql[_sql.find("("):].lower()
            if any(col in _cols for col in _MASS_UPDATE_COLS):
                dropped.append((_name, _sql))
        for _name, _ in dropped:
            c.execute(f"DROP INDEX IF EXISTS {_name}")
    except Exception:
        dropped = []   # 조회/삭제 실패 시엔 그냥 예전처럼 인덱스를 둔 채로 진행
    try:
        yield
    finally:
        for _name, _sql in dropped:
            try:
                c.execute(_sql)
            except Exception:
                pass   # 인덱스는 성능용이라 재생성에 실패해도 게임은 정상 동작


def _age_and_progress_np(c, rows, team_cap, orphan_fallback):
    """벡터화 버전 — 선수 5.9만 명(+향후 확장분)을 파이썬 for문 없이 numpy로 처리.
    로직(확률/증감폭/키스탯 가중치)은 순수 파이썬 버전과 동일하게 유지했다."""
    from database import _WEIGHT_SUMS
    # [2026-08 계측 추가, 신민용 리포트: "numpy 쓰는데도 예상보다 느린데?"]
    # "계산+DB쓰기" 0.49s가 numpy 벡터 연산 자체인지 executemany(현재
    # 10만+ 행)인지 갈라본다.
    import time as _time_npf
    _npf_t0 = _time_npf.perf_counter()

    N = len(rows)
    pids = [r[0] for r in rows]
    pids_arr_full = np.array(pids, dtype=np.int64)  # [2026-08 신설] _ages_well 벡터화용 — 아래서 재사용
    pos_list = [r[1] for r in rows]
    pos_arr = np.array(pos_list)
    ages = np.array([(r[2] or 20) for r in rows], dtype=np.int64)
    tids = [r[3] for r in rows]
    # [2026-09 신설] 목표OVR 기반 노화(_AGING_DECLINE_SCHEDULE 정의부 주석
    # 참고)용 — 하락 전 현재 ovr과 전성기 기준점(peak_ovr, 0이면 아직 미확정).
    cur_ovr_arr = np.array([(r[19] or 0) for r in rows], dtype=np.int64)
    peak_ovr_arr = np.array([(r[20] or 0) for r in rows], dtype=np.int64)

    # None/0 스탯은 기존과 동일하게 50으로 보정 (구버전 세이브 방어)
    # [최적화] 중첩 리스트(list-of-tuples)를 np.array로 바로 변환하는 것보다
    # 1차원으로 펼친 뒤 reshape하는 편이 실측상 더 빠름(타입 추론 오버헤드 감소).
    _flat = [v for r in rows for v in r[4:19]]
    raw = np.array(_flat, dtype=np.float64).reshape(N, _N_STATS)
    vals_arr = np.where(np.isnan(raw) | (raw == 0), 50.0, raw).astype(np.int64)

    # [2026-07 최적화, 신민용 리포트: "일정 진행이 갈수록 오래 걸린다" — 실측
    # 결과 이 함수가 "벡터화 버전"이라면서 여기 한 곳만 순수 파이썬 for문으로
    # 10만+ 회를 도는 게 남아있었다(dict.get()을 선수 수만큼 반복). team_cap은
    # 팀 수(9천여 개)만큼만 있으니, searchsorted로 완전히 벡터화한다 —
    # dict 방식 O(N) 파이썬 루프 → O(N log M) numpy 연산(M=팀 수)으로 대체.
    tids_arr = np.array(tids, dtype=np.int64)
    if team_cap:
        _cap_keys = np.array(list(team_cap.keys()), dtype=np.int64)
        _cap_vals = np.array(list(team_cap.values()), dtype=np.int64)
        _order = np.argsort(_cap_keys)
        _cap_keys_sorted = _cap_keys[_order]
        _cap_vals_sorted = _cap_vals[_order]
        _idx = np.searchsorted(_cap_keys_sorted, tids_arr)
        _idx = np.clip(_idx, 0, len(_cap_keys_sorted) - 1)
        _found = _cap_keys_sorted[_idx] == tids_arr
        cap_by_row = np.where(_found, _cap_vals_sorted[_idx], orphan_fallback).astype(np.int64)
        _orphan_team_ids = set(tids_arr[~_found].tolist())
    else:
        cap_by_row = np.full(N, orphan_fallback, dtype=np.int64)
        _orphan_team_ids = set(tids_arr.tolist())

    new_age = ages + 1
    growth_mask = new_age <= _AI_PEAK_START
    peak_mask = (new_age > _AI_PEAK_START) & (new_age <= _AI_PEAK_END)
    aging_mask = new_age > _AI_PEAK_END

    # [2026-08 재현성 수정] 파이썬 random 모듈(이미 게임 마스터 시드로
    # 고정돼 있음)에서 시드값을 하나 뽑아 numpy Generator를 초기화 —
    # _age_and_progress 함수 docstring 참고. random 모듈 시드가 같으면
    # 이 시즌의 성장/노화 난수도 항상 동일해진다.
    rng = np.random.default_rng(random.getrandbits(64))
    # [2026-08 버그수정, 전체 최적화 감사 중 발견 — 신민용이 예전에
    # "PYTHONHASHSEED=0을 고정해도 완전히 재현되지 않는다"고 했던 원인]
    # 아래 세 군데의 `for pos in unique_positions:` 루프는 순회 순서대로
    # numpy 난수를 뽑아 쓴다. 그런데 파이썬 set의 순회 순서는 원소(문자열)
    # 해시에 좌우되고, 그 해시는 프로세스마다 무작위로 바뀐다(해시 무작위화).
    # 즉 같은 시드로 돌려도 실행할 때마다 포지션 처리 순서가 달라져
    # 성장/노화 결과가 통째로 달라지고 있었다 — 시즌 결과 재현이 원천적으로
    # 불가능했던 지점. sorted()로 순서를 못박아 같은 시드면 항상 같은 결과가
    # 나오게 한다. 다루는 포지션 집합·처리 내용은 전혀 바뀌지 않고
    # (전부 처리하는 건 동일) 순서만 고정되며, 애초에 이 순서에 의미가
    # 부여된 로직도 없다(포지션별로 독립적으로 처리).
    unique_positions = sorted(set(pos_list))

    # ── 성장기: 핵심스탯 전용 터치풀 + 비핵심스탯 전용 터치풀(완전 분리),
    #    각각 팀 상한까지 격차비례 회복 ──
    # [2026-09 재설계, 위 _AI_GROWTH_TOUCHES_NONKEY 정의부 주석 참고]
    # 예전엔 "70% 핵심 / 30% 전체15개 공유풀"이라 비핵심스탯(포지션
    # 가중치의 대략 절반)이 성장기 내내 터치를 거의 못 받았다(실측 세이브:
    # 성장상한99팀 24~26세 핵심평균96.1 vs 비핵심평균81.9) — 그 결과
    # 명문팀에서 자라도 OVR이 95~96대에서 막히고 97~99는 사실상 안
    # 나왔다. 이제 핵심/비핵심을 완전히 분리된 터치예산으로 나눠 각자
    # 독립적으로 상한에 다가가게 한다(핵심 쪽은 희석이 없어져 수렴이 더
    # 빨라지고, 비핵심 쪽은 처음으로 의미 있는 성장기회를 받는다).
    for pos in unique_positions:
        idxs = np.where(growth_mask & (pos_arr == pos))[0]
        Ng = len(idxs)
        if Ng == 0:
            continue
        key_idx = _KEY_IDX_BY_POS_NP.get(pos, _DEFAULT_KEY_IDX_NP)
        nonkey_idx = _NONKEY_IDX_BY_POS_NP.get(pos, _DEFAULT_NONKEY_IDX_NP)
        n_up = rng.integers(_AI_GROWTH_TOUCHES[0], _AI_GROWTH_TOUCHES[1] + 1, size=Ng)
        for rnd in range(_AI_GROWTH_TOUCHES[1]):
            active = n_up > rnd
            if not active.any():
                continue
            act_idx = idxs[active]
            m = len(act_idx)
            chosen = key_idx[rng.integers(0, len(key_idx), size=m)]
            cur = vals_arr[act_idx, chosen]
            cap = cap_by_row[act_idx]
            gain = np.maximum(1, np.round((cap - cur) * _AI_GROWTH_CATCHUP_FRAC)).astype(np.int64)
            vals_arr[act_idx, chosen] = np.minimum(cap, cur + gain)
        n_up2 = rng.integers(_AI_GROWTH_TOUCHES_NONKEY[0], _AI_GROWTH_TOUCHES_NONKEY[1] + 1, size=Ng)
        for rnd in range(_AI_GROWTH_TOUCHES_NONKEY[1]):
            active = n_up2 > rnd
            if not active.any():
                continue
            act_idx = idxs[active]
            m = len(act_idx)
            chosen = nonkey_idx[rng.integers(0, len(nonkey_idx), size=m)]
            cur = vals_arr[act_idx, chosen]
            cap = cap_by_row[act_idx]
            gain = np.maximum(1, np.round((cap - cur) * _AI_GROWTH_CATCHUP_FRAC)).astype(np.int64)
            vals_arr[act_idx, chosen] = np.minimum(cap, cur + gain)

    # ── 피크기: 30% 확률로 전체스탯 중 1개 ±1 (승격/강등과 무관한 절대
    #    상한) ──
    # [2026-08 수정, 신민용 요청: "승격한 팀이 그거에 맞춰 팀을 개편하는
    # 식으로 가면 좋겠다 — 20대 초반은 재능등급 오르게 OVR을 올릴 수
    # 있지만, 전성기(29세)는 그렇게 오르는 시스템이 아니어도 된다"]
    # 예전엔 여기도 cap_by_row(팀의 현재 등급/tier에서 나온 상한 — 팀이
    # 방금 승격하면 이 상한도 즉시 올라감)를 썼다 — 그러면 이미 성장이
    # 끝난(24세 이하 성장기가 아닌) 25~29세 선수도 소속팀이 승격하는
    # 순간 곧바로 OVR이 슬금슬금 오를 여지가 생겼다. 성장기(위, 24세
    # 이하)는 팀 상한을 그대로 쓰게 놔둬 어린 선수는 상위 리그 이적/
    # 소속팀 승격으로 실제로 더 클 수 있게 하고(신민용이 명시적으로
    # 허용), 이 피크기 구간만 절대 상한(99)으로 바꿔서 승격/강등과 완전히
    # 무관하게 만든다 — 승격팀이 강해지는 건 이제 이적시장에서 실제로
    # 더 좋은 선수를 사 오는 쪽(카테고리별 이적 물량 확대)으로만 반영된다.
    idxs = np.where(peak_mask)[0]
    if len(idxs):
        active = rng.random(len(idxs)) < 0.3
        act_idx = idxs[active]
        m = len(act_idx)
        if m:
            chosen = rng.integers(0, _N_STATS, size=m)
            coin = rng.integers(0, 3, size=m)          # random.choice([-1,1,1])과 동일 분포
            delta = np.where(coin == 0, -1, 1)
            cur = vals_arr[act_idx, chosen]
            vals_arr[act_idx, chosen] = np.clip(cur + delta, 15, 99)

    # ── 노화기: 목표OVR까지 반복 하락(전성기 대비 나이별 목표% + 개인
    #    자기관리 등급 보정) ──
    # [2026-09 재설계, 위 _AGING_DECLINE_SCHEDULE/_MGMT_TIERS 정의부 주석
    # 참고] 예전엔 "나이 비례 하락 '횟수'"만 있고 목표치가 없어 실측
    # 28세95→40세84.8~87.4로 사용자가 원한 73~76보다 너무 완만했다.
    # 이제 전성기(peak_ovr) 대비 나이별 목표 OVR을 먼저 계산하고, 그
    # 목표에 도달할 때까지만(최대 40라운드 안전장치) 스탯을 깎는다 —
    # 스탯 선택 편향(신체스탯 위주 vs 키스탯 위주)은 기존 well/not-well
    # 구조를 그대로 재사용하되, 이제는 개인 자기관리 보정계수의 부호로
    # 결정한다(보정이 음수=완만=신체스탯 위주, 양수 이상=가파름=키스탯
    # 위주).
    idxs = np.where(aging_mask)[0]
    if len(idxs):
        # 1) 전성기(peak_ovr) 기준점 확정 — 이번이 노화 첫 시즌(29→30)이거나
        #    구버전 세이브에서 아직 못 채워진 행은 "이번 시즌 하락 전 ovr"을
        #    기준점으로 고정한다(그 값도 없는 극히 드문 경우만 즉석 계산).
        need_peak = peak_ovr_arr[idxs] <= 0
        if need_peak.any():
            _fb = idxs[need_peak]
            _zero_cur = cur_ovr_arr[_fb] <= 0
            if _zero_cur.any():
                for _j in _fb[_zero_cur]:
                    cur_ovr_arr[_j] = calc_ovr_from_list(pos_list[_j], vals_arr[_j].tolist())
            peak_ovr_arr[_fb] = cur_ovr_arr[_fb]

        # 2) 나이별 기준 하락률 × 개인 자기관리 보정계수 → 목표 OVR.
        ages_i = new_age[idxs]
        base_pct = np.array([_aging_base_decline_pct(int(a)) for a in ages_i])
        mods = np.array([_mgmt_tier_and_mult(int(p))[1] for p in pids_arr_full[idxs]])
        eff_pct = np.clip(base_pct * (1.0 + mods), 0.0, 0.75)
        target_arr = np.maximum(15, np.round(peak_ovr_arr[idxs] * (1.0 - eff_pct))).astype(np.int64)
        good_mgmt = mods <= 0

        for pos in unique_positions:
            sel = pos_arr[idxs] == pos
            if not sel.any():
                continue
            act_idx = idxs[sel]              # vals_arr 기준 행 인덱스
            tgt = target_arr[sel]
            good = good_mgmt[sel]
            key_idx = _KEY_IDX_BY_POS_NP.get(pos, _DEFAULT_KEY_IDX_NP)
            wv = _WEIGHT_VEC_NP.get(pos, _WEIGHT_VEC_NP["CM"])
            wsum = _WEIGHT_SUMS.get(pos, _WEIGHT_SUMS["CM"])

            for rnd in range(40):
                cur_ovr_now = (vals_arr[act_idx] @ wv) / wsum
                active = cur_ovr_now > (tgt + 0.5)
                if not active.any():
                    break
                a_idx = act_idx[active]
                a_good = good[active]
                m = len(a_idx)
                use_phys = a_good & (rng.random(m) < 0.65)
                use_key = (~a_good) & (rng.random(m) < 0.70)
                chosen = np.where(
                    a_good,
                    np.where(use_phys,
                             _PHYS_IDX_NP[rng.integers(0, len(_PHYS_IDX_NP), size=m)],
                             rng.integers(0, _N_STATS, size=m)),
                    np.where(use_key,
                             key_idx[rng.integers(0, len(key_idx), size=m)],
                             rng.integers(0, _N_STATS, size=m)))
                dec = rng.integers(1, 4, size=m)
                cur = vals_arr[a_idx, chosen]
                vals_arr[a_idx, chosen] = np.maximum(15, cur - dec)

    # ── OVR 재계산 (포지션별 가중치 벡터와 행렬곱, 5.9만 명 순회 없이 일괄 처리) ──
    ovr_out = np.empty(N, dtype=np.int64)
    for pos in unique_positions:
        mask = pos_arr == pos
        wv = _WEIGHT_VEC_NP.get(pos, _WEIGHT_VEC_NP["CM"])
        wsum = _WEIGHT_SUMS.get(pos, _WEIGHT_SUMS["CM"])
        total = vals_arr[mask] @ wv / wsum
        ovr_out[mask] = np.clip(np.round(total), 1, 100).astype(np.int64)

    # [최적화] (age, *stats, ovr, peak_ovr, id) 튜플을 파이썬 루프로 만드는
    # 대신 column_stack으로 한 번에 이어붙여 tolist() — sqlite3.executemany는
    # 튜플뿐 아니라 리스트 행도 그대로 받아준다. 5.9만 회 언패킹 루프 제거.
    # [2026-09] peak_ovr_arr 추가 — 노화 진입 시점에 확정된 전성기 기준점을
    # 그대로 저장해야 다음 시즌에도 같은 기준으로 목표OVR을 계산한다.
    updates = np.column_stack([new_age, vals_arr, ovr_out, peak_ovr_arr, pids_arr_full]).tolist()
    _npf_t1 = _time_npf.perf_counter()

    set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
    with _indexes_off_for_mass_update(c):
        c.executemany(
            f"UPDATE ai_players SET age=?, {set_clause}, ovr=?, peak_ovr=? WHERE id=?",
            updates)
    _npf_t2 = _time_npf.perf_counter()
    _perf_log(f"[PERF-AGE-NP]  numpy계산 {_npf_t1-_npf_t0:.3f}s | "
          f"executemany({len(updates)}건) {_npf_t2-_npf_t1:.3f}s")

    if _orphan_team_ids:
        import sys as _sys
        print(f"[⚠ ai_lifecycle 경고] team_cap 매칭 실패 팀 {len(_orphan_team_ids)}개 "
              f"(league_id/country_id 연결 확인 필요, 폴백 상한 {orphan_fallback} 적용됨): "
              f"{sorted(_orphan_team_ids)[:20]}{'...' if len(_orphan_team_ids) > 20 else ''}",
              file=_sys.stderr)

    return int(growth_mask.sum()), int(aging_mask.sum())


# [2026-09 신설] _age_and_progress_py 전용 — 포지션별 "비핵심스탯 목록"
# 캐시(매 선수마다 리스트 컴프리헨션 새로 만들지 않도록 1회 계산 후 재사용).
_NONKEY_STATS_BY_POS: dict = {}


def _age_and_progress_py(c, rows, team_cap, orphan_fallback):
    """순수 파이썬 폴백 버전 (numpy 미설치 환경용). 로직은 numpy 버전과 동일."""
    from database import STAT_IDX, calc_ovr_from_list
    grew = aged = 0
    updates = []  # (age, s1, s2, ..., ovr, id) 튜플 목록
    _default_keys = ALL_STATS[:5]
    _orphan_team_ids = set()

    _randint = random.randint
    _choice = random.choice
    _random = random.random

    for r in rows:
        pid = r[0]
        pos = r[1]
        new_age = (r[2] or 20) + 1
        tid = r[3]
        if tid in team_cap:
            _cap = team_cap[tid]
        else:
            _cap = orphan_fallback
            _orphan_team_ids.add(tid)
        vals = [v or 50 for v in r[4:19]]
        cur_ovr_val = r[19] or 0
        peak_ovr_val = r[20] or 0
        keys = KEY_STATS_BY_POS.get(pos, _default_keys)
        nonkeys = _NONKEY_STATS_BY_POS.get(pos)
        if nonkeys is None:
            nonkeys = [s for s in ALL_STATS if s not in keys]
            _NONKEY_STATS_BY_POS[pos] = nonkeys

        if new_age <= _AI_PEAK_START:
            # [2026-09 재설계] 위 _age_and_progress_np와 동일하게, 핵심/
            # 비핵심을 완전히 분리된 터치풀로 처리 — _AI_GROWTH_TOUCHES_
            # NONKEY 정의부 주석 참고(예전 70/30 공유풀은 폐기).
            n_up = _randint(*_AI_GROWTH_TOUCHES)
            for _ in range(n_up):
                s = _choice(keys)
                i = STAT_IDX[s]
                gap = _cap - vals[i]
                gain = max(1, round(gap * _AI_GROWTH_CATCHUP_FRAC))
                vals[i] = min(_cap, vals[i] + gain)
            n_up2 = _randint(*_AI_GROWTH_TOUCHES_NONKEY)
            for _ in range(n_up2):
                s = _choice(nonkeys)
                i = STAT_IDX[s]
                gap = _cap - vals[i]
                gain = max(1, round(gap * _AI_GROWTH_CATCHUP_FRAC))
                vals[i] = min(_cap, vals[i] + gain)
            grew += 1
        elif new_age <= _AI_PEAK_END:
            # [2026-08 수정, 신민용 요청: "승격/강등과 무관하게, 전성기
            # (29세)는 팀 상한을 따라 오르는 시스템이 아니어도 된다"]
            # 위 numpy 버전과 동일 — 성장기(_cap, 팀 승격 시 즉시 상승)와
            # 달리 피크기는 절대 상한(99)만 쓴다.
            if _random() < 0.3:
                s = _choice(ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = min(99, max(15, vals[i] + _choice([-1, 1, 1])))
        else:
            # [2026-09 재설계] 위 _age_and_progress_np와 동일 — 목표OVR
            # 기반 노화(_AGING_DECLINE_SCHEDULE/_MGMT_TIERS 정의부 주석
            # 참고). 전성기(peak_ovr)를 이번이 처음이면 확정하고, 나이×
            # 개인 자기관리 보정으로 목표 OVR을 계산한 뒤 거기 도달할
            # 때까지만(최대 40회 안전장치) 스탯을 깎는다.
            if not peak_ovr_val:
                peak_ovr_val = cur_ovr_val or calc_ovr_from_list(pos, vals)
            target = _aging_target_ovr(peak_ovr_val, new_age, pid)
            _, _mult = _mgmt_tier_and_mult(pid)
            _well = _mult <= 0
            for _ in range(40):
                if calc_ovr_from_list(pos, vals) <= target:
                    break
                if _well:
                    s = _choice(_AGING_PHYS_STATS) if _random() < 0.65 else _choice(ALL_STATS)
                else:
                    s = _choice(keys) if _random() < 0.7 else _choice(ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = max(15, vals[i] - _randint(1, 3))
            aged += 1

        new_ovr = calc_ovr_from_list(pos, vals)
        updates.append((new_age, *vals, new_ovr, peak_ovr_val, pid))

    set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
    with _indexes_off_for_mass_update(c):
        c.executemany(
            f"UPDATE ai_players SET age=?, {set_clause}, ovr=?, peak_ovr=? WHERE id=?",
            updates)

    if _orphan_team_ids:
        import sys as _sys
        print(f"[⚠ ai_lifecycle 경고] team_cap 매칭 실패 팀 {len(_orphan_team_ids)}개 "
              f"(league_id/country_id 연결 확인 필요, 폴백 상한 {orphan_fallback} 적용됨): "
              f"{sorted(_orphan_team_ids)[:20]}{'...' if len(_orphan_team_ids) > 20 else ''}",
              file=_sys.stderr)

    return grew, aged


# ─────────────────────────────────────────────
# 3. 은퇴 + 신인 교체
# ─────────────────────────────────────────────
def _process_loan_returns(c, year):
    """[2026-09 신설, 신민용 요청: "이적 종류(이적/임대)도 구분해야 한다"]
    on_loan_from_team_id가 설정된(0이 아닌) 선수 중 loan_return_year가
    이번 연도(또는 이미 지남)에 도달한 선수를 원 소속팀으로 복귀시킨다.
    복귀 후엔 두 필드 다 초기화(0)해서 "임대 중"이 아닌 상태로 되돌린다
    — team_id는 그대로 두면 임대처에 영구히 눌러앉는 꼴이 되므로 반드시
    on_loan_from_team_id로 되돌려야 한다. 원 소속팀이 그 사이 없어졌거나
    (극히 드문 데이터 정합성 예외) 원 소속팀 자체 스쿼드 인원 보정은
    다음 _rebalance_squad_sizes가 알아서 처리하므로 여기서는 신경 안
    쓴다.
    [2026-09 확장] 복귀도 ai_transfer_log에 한 줄 남긴다(fee=0, is_loan=0
    — 새로 임대를 나가는 게 아니라 "임대가 끝나서 원래 자리로 돌아옴") —
    안 남기면 world_browser.get_ai_player_team_timeline이 이 복귀를
    구간 경계로 못 알아채서, 세계 축구 기록실에 "임대 중"이던 팀 소속이
    실제 복귀 이후까지 계속 이어진 것처럼 잘못 표시된다.
    반환: 복귀 처리된 인원 수."""
    rows = c.execute(
        "SELECT id, name, position, age, ovr, salary, team_id, on_loan_from_team_id, "
        "contract_end_year FROM ai_players "
        "WHERE on_loan_from_team_id != 0 AND loan_return_year <= ?", (year,)).fetchall()
    if not rows:
        return 0
    updates = [(r["on_loan_from_team_id"], r["id"]) for r in rows]
    c.executemany(
        "UPDATE ai_players SET team_id=?, on_loan_from_team_id=0, loan_return_year=0 WHERE id=?",
        updates)
    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 1
    log_rows = [(
        _cur_season, year, r["id"], r["name"], r["position"], r["age"] or 25, r["ovr"],
        r["team_id"], r["on_loan_from_team_id"], 0, 0, 0.0, 0.0,
        "임대 복귀", 0, "", 0, 0, 0, r["salary"] or 0, r["contract_end_year"] or 0) for r in rows]
    c.executemany(
        """INSERT INTO ai_transfer_log(
            season, year, player_id, player_name, player_position, player_age, player_ovr,
            from_team_id, to_team_id, from_team_prestige, to_team_prestige,
            from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
            fee, is_loan, loan_return_year, salary, contract_end_year)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        log_rows)
    return len(updates)


def _process_contract_renewals(c, year):
    """[2026-09 신설, 신민용 요청: "계약을 몇년치 했냐인건데... 기간이
    늘어나면 연장 이런식으로 하고 연봉 수치도 변화하잖아"] 지금까지 AI
    선수는 이적할 때만 새 계약(=새 연봉)이 생겼다 — 그대로 한 팀에 계속
    있으면 계약이 만료돼도 아무 일도 안 일어났다. 이 시즌 계약이
    만료된(그리고 임대 중이 아닌) 선수를 대상으로 재계약 여부를 굴린다
    — _transfer_market이 이미 "계약만료 임박" 선수를 이적 후보로 더 잘
    뽑도록 가중치를 주고 있으므로, 여기는 그 이적시장이 끝난 뒤(그래서
    이번 시즌에 실제로 안 팔린 선수만 남은 상태에서) 호출하는 게 맞다
    — run_ai_offseason에서 _transfer_market 다음, _prestige_scouting과
    함께 호출.
    반환: 재계약 처리된 인원 수."""
    from constants import (AI_CONTRACT_RENEWAL_PROB, AI_CONTRACT_RENEWAL_DURATION_YEARS)
    from constants import get_country_league_grade
    rows = c.execute(
        "SELECT id, name, position, age, ovr, team_id FROM ai_players "
        "WHERE contract_end_year <= ? AND contract_end_year > 0 "
        "AND on_loan_from_team_id = 0", (year,)).fetchall()
    if not rows:
        return 0
    team_rows = c.execute(
        """SELECT t.id, t.name, t.current_tier AS tier, cn.name AS cname FROM teams t
           JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id""").fetchall()
    tinfo_by_tid = {t["id"]: (t["cname"], t["name"], t["tier"]) for t in team_rows}
    _grade_cache: dict = {}

    def _grade_of(tid_):
        cname_ = tinfo_by_tid.get(tid_, ("", "", 1))[0]
        if cname_ not in _grade_cache:
            _grade_cache[cname_] = get_country_league_grade(cname_)
        return _grade_cache[cname_]

    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 1
    updates = []
    log_rows = []
    for r in rows:
        if random.random() >= AI_CONTRACT_RENEWAL_PROB:
            continue  # 재계약 불발 — 계약 만료 상태 그대로 두면 다음 시즌
            # 이적시장에서 "계약 임박" 가중치로 계속 이적 후보가 된다.
        cname, tname, tier = tinfo_by_tid.get(r["team_id"], ("", "", 1))
        grade = _grade_of(r["team_id"])
        new_salary = _calc_ai_salary(grade, tier, r["ovr"], cname, tname, r["team_id"], year)
        # [2026-09 버그수정, 구현 직후 헤드리스 검증 중 자체 발견: "재계약
        # 기간을 2~5년으로 뽑았는데 표시되는 기간이 1~4년으로 한 해씩
        # 짧게 나온다"] world_browser의 duration 계산은 실제 발효연도
        # (effective_year — 오프시즌 이적/재계약은 다음 해부터 발효,
        # get_ai_player_salary_history 주석 참고)를 기준으로 하는데,
        # 여기서 만료연도를 셀 때는 발효 전(year)을 기준으로 셌던 게
        # 원인 — year+1(발효연도)부터 세야 의도한 기간 그대로 표시된다.
        new_cend = year + 1 + random.randint(*AI_CONTRACT_RENEWAL_DURATION_YEARS)
        updates.append((new_cend, new_salary, r["id"]))
        log_rows.append((
            _cur_season, year, r["id"], r["name"], r["position"], r["age"] or 25, r["ovr"],
            r["team_id"], r["team_id"], 0, 0, 0.0, 0.0, "연장", 0, "", 0, 0, 0,
            new_salary, new_cend))
    if updates:
        c.executemany(
            "UPDATE ai_players SET contract_end_year=?, salary=? WHERE id=?", updates)
    if log_rows:
        c.executemany(
            """INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            log_rows)
    return len(updates)


def _calc_ai_salary(grade, tier, ovr, cname, tname, team_id, year):
    """[2026-09 신설, 신민용 요청: "이적이면 연봉이 써지는거고"] AI 선수
    연봉 계산 — economy._calc_salary()를 그대로 재사용한다(talent_tier
    파라미터는 my_player 전용이라 안 넘긴다). AI 쪽에서 이 함수를 처음
    쓰기 시작하는 것이라(economy._calc_salary 자체 docstring에 "ai_
    lifecycle.py는 이 함수를 아예 호출하지 않는다"고 적혀 있던 게 이제
    바뀜), 혹시 모를 예외로 시즌 진행 전체가 멈추면 안 되므로 실패는
    조용히 0으로 흡수한다.
    [2026-09 성능수정, 구현 직후 헤드리스 검증 중 자체 발견: "이적시장
    루프(86,689명 처리) 236초"] _calc_salary에 team_id를 넘기면 내부에서
    club_strength 조회용으로 매번 새 DB 커넥션을 열고 닫는다(economy.
    _get_club_strength) — _transfer_market은 원래 이런 건별 DB 왕복을
    없애려고 팀 정보를 통째로 미리 캐싱해두는 구조인데, 여기서 그 원칙을
    깨고 건당(최대 8만 건대) 커넥션을 열어버린 게 병목이었다. team_id는
    club_strength 보정(0.95~1.10, 원래도 좁은 범위)에만 쓰이므로, 넘기지
    않고 중립(1.0)으로 흡수한다 — AI는 단순해야 한다는 이 파일의 기존
    원칙과도 맞다."""
    try:
        from economy import _calc_salary
        return _calc_salary(grade, tier, ovr, country=cname, team_name=tname, year=year)
    except Exception:
        return 0


def _build_buy_pools(rows, team_info=None):
    """[2026-09 신설] 명문팀이 은퇴자를 유스 생성 대신 시장에서 영입으로
    채울 때 쓰는 후보 풀 — 포지션별로 OVR 오름차순 정렬해서 bisect로
    원하는 구간만 빠르게 잘라 쓸 수 있게 한다. _retire_and_replace가 이미
    선조회해둔 ai_players 전체(rows)를 그대로 재사용해 별도 쿼리가 없다.
    반환: {position: (ovr오름차순 정렬된 행 리스트, 같은 순서의 ovr 리스트)}."""
    tmp: dict = {}
    for r in rows:
        tmp.setdefault(r["position"], []).append(r)
    # [2026-09 최적화] 아래 entries에 넣을 "리그 등급랭크"를 여기서 한 번만
    # 계산한다 — _find_buy_replacement의 global_scouting 가드가 후보마다
    # LEAGUE_GRADE_RANK dict를 다시 찾던 것을 없앤다(실측상 그 경로가 전체
    # 스캔의 98%라 시즌당 약 0.3초). 판정에 쓰는 표도, 기본값(1)도 원본과
    # 완전히 같은 것을 쓴다.
    _rank_of = {}
    _cty_code: dict = {}   # 국가명 -> 정수코드(numpy 비교용, 이 호출 안에서만 유효)
    if team_info is not None:
        try:
            from economy import LEAGUE_GRADE_RANK as _rank_of
        except Exception:
            _rank_of = {}
    pools = {}
    for pos, lst in tmp.items():
        lst.sort(key=lambda r: r["ovr"])
        ovrs = [r["ovr"] for r in lst]
        # [2026-09 최적화] _find_buy_replacement가 은퇴자마다 전세계 밴드
        # (포지션당 수천 명)를 파이썬으로 전수 순회하던 것을 없앤다. 실측
        # (선수 25.5만·팀 12,750, 영입시도 8,000회): 39.05s → 6.23s.
        #   - by_country : "자국 우선" 검색용 국가별 부분풀. 실측상
        #     영입 시도의 90% 이상이 이 경로다(global_scouting은 SS/S
        #     또는 prestige>=2 팀만 타므로 소수).
        #   - 원소는 sqlite3.Row가 아니라 미리 뜯어놓은 튜플
        #     (행, id, team_id, 국가, 등급랭크, 나이) — Row의 이름 조회는
        #     컬럼명 순차 비교라 비싼데 후보당 4번씩 수천만 회 돌았다.
        #     값은 시즌 내내 안 변하므로 여기서 한 번만 뜯어둔다.
        #
        # [결과 동일성] 부분풀은 전역 정렬 리스트를 앞에서부터 훑어
        # 담으므로 "부분수열"이다 — 전역 밴드를 잘라 같은 조건으로 거른
        # 것과 원소도, 그 순서도 정확히 같다. 순서가 같아야 뒤이은
        # random.choices가 같은 난수 스트림에서 같은 선수를 뽑는다.
        # 아래 _filter의 판정 조건은 한 줄도 바꾸지 않았고, 좁힌 뒤에도
        # 원래 가드를 그대로 다시 검사한다(좁히기가 틀려도 결과 불변).
        by_country: dict = {}
        entries = []
        if team_info is not None:
            for r in lst:
                _ti = team_info.get(r["team_id"])
                _cn = _ti[3] if _ti else ""
                _e = (r, r["id"], r["team_id"], _cn,
                      _rank_of.get(_ti[0] if _ti else "D", 1), r["age"])
                entries.append(_e)
                _b = by_country.get(_cn)
                if _b is None:
                    _b = by_country[_cn] = ([], [])
                _b[0].append(_e)
                _b[1].append(r["ovr"])
        # [2026-09 최적화] 전세계 경로 전용 numpy 미러(고정 배열).
        # entries와 완전히 같은 순서·같은 값이며, 시즌 내내 안 변하는
        # 것들만 담는다(팀ID/국가코드/리그등급랭크/나이). 변하는 것은
        # used(아래 bool 배열)와 팀 포지션그룹 인원수(_build_team_pos_
        # group_count의 "__ok__" 미러)뿐이라, 그 둘만 갱신하면 된다.
        _np = None
        if entries and USE_NUMPY_GLOBAL_POOL and _HAS_NUMPY:
            try:
                _np = (
                    np.fromiter((e[2] for e in entries), np.int64, len(entries)),
                    np.fromiter((_cty_code.setdefault(e[3], len(_cty_code)) for e in entries),
                                np.int32, len(entries)),
                    np.fromiter((e[4] for e in entries), np.int16, len(entries)),
                    # 원본 판정이 (age or 25)이므로 그 치환을 여기서 미리 해둔다.
                    np.fromiter(((e[5] or 25) for e in entries), np.int16, len(entries)),
                    np.zeros(len(entries), dtype=bool),          # used 마스크
                    {e[1]: i for i, e in enumerate(entries)},     # player_id -> 인덱스
                    _cty_code,
                    max(e[2] for e in entries),                   # 팀ID 최댓값(경계검사용)
                )
            except Exception:
                _np = None   # 어떤 이유로든 실패하면 조용히 기존 경로로
        pools[pos] = (lst, ovrs, by_country or None, entries or None, _np)
    return pools


def _np_mark_buy_used(pools, row):
    """[2026-09] _buy_used_ids.add()와 짝 — numpy used 마스크에도 같은
    선수를 표시한다. 미러가 없으면(플래그 off / numpy 없음) 아무것도 안 함."""
    pool = pools.get(row["position"]) if pools else None
    _np = pool[4] if (pool and len(pool) > 4) else None
    if _np is None:
        return
    i = _np[5].get(row["id"])
    if i is not None:
        _np[4][i] = True


def _np_sync_grp_ok(team_pos_group_count, grp, team_id, new_count):
    """[2026-09] _bg[tid] 갱신과 짝 — 원본 판정 `count <= 1 이면 제외`를
    그대로 bool 배열(True=쓸 수 있음)에 반영한다."""
    ok = team_pos_group_count.get("__ok__") if team_pos_group_count else None
    if not ok:
        return
    arr = ok.get(grp)
    if arr is not None and 0 <= team_id < arr.size:
        arr[team_id] = (new_count > 1)


def _build_team_pos_group_count(rows):
    """[2026-09 신설] (team_id, 포지션그룹) → 그 팀에 지금 그 그룹 선수가
    몇 명 있는지. _find_buy_replacement가 "이 선수를 팔면(=은퇴 대체용으로
    빼가면) 그 팀의 이 포지션그룹이 0명이 되는가"를 판정할 때 쓴다 —
    _do_one_transfer_cached의 "마지막 GK/마지막 CB는 판매 후보에서 제외"
    보호 원칙과 동일하다."""
    # [2026-09 최적화] 예전엔 {(team_id, 그룹): 수} 한 겹이었는데, 그러면
    # _find_buy_replacement가 후보 한 명을 볼 때마다 (team_id, grp) 튜플을
    # 새로 만들어 조회해야 한다 — 실측상 그 루프가 시즌당 1,240만 번 돌아
    # 튜플 생성만으로 0.8초가 나갔다. {그룹: {team_id: 수}} 두 겹으로 바꾸면
    # 그룹 dict를 호출당 한 번만 꺼내두고 팀 id로 바로 찾으면 된다.
    # 담기는 값과 의미는 완전히 동일하다.
    counts: dict = {}
    for r in rows:
        grp = _POS_GROUP.get(r["position"], "FW")
        _g = counts.get(grp)
        if _g is None:
            _g = counts[grp] = {}
        _tid = r["team_id"]
        _g[_tid] = _g.get(_tid, 0) + 1
    # [2026-09 최적화] 전세계 경로 전용 미러 — 원본 판정
    # `counts[grp].get(team_id, 0) <= 1 이면 제외`와 정확히 같은 뜻의
    # bool 배열(True=후보로 쓸 수 있음). 표에 없는 팀은 기본 0이므로
    # False로 남아 원본과 동일하게 제외된다. 그룹 키와 안 겹치는
    # "__ok__"에 담아 호출부 시그니처를 안 바꾼다.
    if USE_NUMPY_GLOBAL_POOL and _HAS_NUMPY and counts:
        try:
            _max_tid = max(max(g) for g in counts.values() if g)
            counts["__ok__"] = {
                grp: np.zeros(_max_tid + 1, dtype=bool) for grp in counts
            }
            for grp, g in counts.items():
                if grp == "__ok__":
                    continue
                arr = counts["__ok__"][grp]
                for _t, _n in g.items():
                    if _n > 1:
                        arr[_t] = True
        except Exception:
            counts.pop("__ok__", None)
    return counts


def _find_buy_replacement(position, target_ovr, dst_team_id, dst_cname,
                           pools, team_info, team_pos_group_count, used_ids,
                           global_scouting=False, stats=None):
    """[2026-09 신설, 신민용+GPT 협업: "명문팀은 은퇴자를 유망주 즉시
    생성으로 채우지 않고, 먼저 시장에서 검증된 선수를 영입 시도한다"]
    target_ovr(은퇴자 자리의 "성인 잠재치") 기준 BUY_REPLACEMENT_OVR_BAND
    안에 있는 같은 포지션 선수 중에서 후보를 찾는다 — 자국(어느 부수든)을
    먼저 보고, 자국에 없으면 해외로 넓힌다("자국 선수 우선 → 자국 하위
    리그 → 해외" 우선순위를 국내/해외 2단계로 단순화, "이미 있는 이적시장
    로직을 최대한 활용" 원칙에 맞춰 무거운 다단계 탐색 대신 가벼운 필터+
    가중추첨으로 처리). 어릴수록(BUY_REPLACEMENT_YOUNG_AGE 이하) 뽑힐
    확률을 높이고, 자기 팀 소속·이미 이번 시즌에 다른 은퇴자리로 뽑힌
    선수·자기 팀에서 그 포지션그룹 마지막 1명은 후보에서 제외한다.

    [2026-09 확장, 신민용 지적: "브라질처럼 선수 풀이 큰 나라는 국내
    우선 검색이 항상 1단계에서 후보를 찾아버려서, 정작 그 위의 좋은
    선수가 해외로 안 흘러나간다 — 국가 등급은 좋은 선수가 나올 확률에만
    영향을 줘야지, 일단 나온 선수가 어디로 갈지를 국적/자국 우선으로
    가둬버리면 안 된다"] 목적지 팀이 SS/S급 리그거나 prestige_level>=2
    (호출부가 판정해 global_scouting로 넘김 — 판정 기준을 이 함수 안에
    새로 만들지 않고 호출부의 기존 grade/_plvl 계산을 그대로 재사용)면
    검색 순서를 뒤집어 전세계(자국 제외) 후보를 먼저 보고, 없을 때만
    자국으로 좁힌다. 이때도 후보 평가 자체(OVR 밴드, 포지션그룹 보호,
    나이 가중치)는 전혀 건드리지 않고 "어느 순서로 국내/해외를 보는가"
    만 바꾼다 — 그리고 _prestige_scouting이 이미 쓰고 있는 "약한 리그가
    강한 리그에서 못 뺏어온다"는 동일한 가드(LEAGUE_GRADE_RANK, 후보의
    리그 등급이 목적지보다 높으면 제외)를 global_scouting 경로에만 추가로
    적용한다 — 그래야 전세계 검색으로 바뀐 게 "명문팀이 항상 세계 최고
    OVR만 쓸어간다"는 반대 방향 쏠림으로 이어지지 않는다(일반 팀의 기존
    국내/해외 2단계 동작은 이 가드 없이 그대로 유지).

    [2026-09 계측, 신민용 지적: "은퇴자는 조금 늘었는데 대체자탐색 시간은
    훨씬 더 늘었다 — 단순 후보 수 증가로 설명이 안 된다"] stats(호출부가
    공유하는 dict, None이면 그냥 아무것도 안 함 — 기존 호출부 동작·성능
    100% 그대로)를 넘기면 이 함수가 자기 호출 통계를 카운터 증가만으로
    남긴다: calls(총 호출), global_calls(global_scouting=True로 불린
    횟수), global_scan_calls/global_scanned(실제로 전세계 슬라이스
    _global_cands()를 만든 횟수와 그 합계 크기 — 여기서 나누면 평균
    슬라이스 크기). "SS/S·프레스티지팀 비율이 늘어서 전세계 탐색 비중이
    늘고, 그 슬라이스 자체도 커지고 있다"는 가설을 새 쿼리·새 반복문
    없이(정수 증가뿐) 실측 확인하기 위함.
    반환: 뽑힌 선수 행(sqlite3.Row) 또는 후보가 없으면 None."""
    from constants import BUY_REPLACEMENT_OVR_BAND, BUY_REPLACEMENT_YOUNG_AGE, BUY_REPLACEMENT_YOUNG_WEIGHT
    if stats is not None:
        stats["calls"] = stats.get("calls", 0) + 1
        if global_scouting:
            stats["global_calls"] = stats.get("global_calls", 0) + 1
    pool = pools.get(position)
    if not pool:
        return None
    rows_sorted, ovrs_sorted, by_country, entries = pool[0], pool[1], pool[2], pool[3]
    _npm = pool[4] if len(pool) > 4 else None
    lo = target_ovr - BUY_REPLACEMENT_OVR_BAND[0]
    hi = target_ovr + BUY_REPLACEMENT_OVR_BAND[1]
    i0 = bisect.bisect_left(ovrs_sorted, lo)
    i1 = bisect.bisect_right(ovrs_sorted, hi)
    if i0 >= i1:
        return None
    # [2026-09 최적화] 전역 밴드 슬라이스(수천 명 복사)는 실제로 전세계를
    # 훑어야 하는 경로에서만 만든다.
    _cands_cell: list = []
    def _global_cands():
        if not _cands_cell:
            _src = entries[i0:i1] if entries is not None else [
                (r, r["id"], r["team_id"], None, 1, r["age"])
                for r in rows_sorted[i0:i1]]
            _cands_cell.append(_src)
        return _cands_cell[0]
    # 이 풀은 position별로 만들어지므로 안에 든 행의 position은 전부 같다
    # — 후보마다 다시 구할 이유가 없어 한 번만 계산한다(결과 동일).
    _grp = _POS_GROUP.get(position, "FW")
    # 그룹별 팀 인원표를 호출당 한 번만 꺼내둔다(_build_team_pos_group_count
    # 주석 참고 — 후보마다 튜플을 만들지 않기 위함).
    _grp_counts = team_pos_group_count.get(_grp) or {}

    dst_rank = 4
    _grade_rank = None
    if global_scouting:
        from economy import LEAGUE_GRADE_RANK
        _grade_rank = LEAGUE_GRADE_RANK
        _dst_ti = team_info.get(dst_team_id)
        dst_rank = _grade_rank.get(_dst_ti[0] if _dst_ti else "D", 4)

    def _filter(same_country):
        # [2026-09 최적화] 자국 검색은 그 나라 부분풀의 밴드만 본다
        # (_build_buy_pools 주석 참고 — 부분풀은 전역 정렬 리스트의
        # 부분수열이라 원소·순서가 전역 밴드를 훑어 국적으로 거른 것과
        # 정확히 같다).
        _sub = by_country.get(dst_cname) if (same_country and by_country is not None) else None
        if _sub is not None:
            _srows, _sovrs = _sub
            if not _srows:
                return []
            scan = _srows[bisect.bisect_left(_sovrs, lo):bisect.bisect_right(_sovrs, hi)]
        elif same_country and by_country is not None:
            return []   # 그 나라 후보 자체가 없음(기존과 동일한 결과)
        else:
            scan = _global_cands()
        out = []
        # e = (행, id, team_id, 국가, 등급랭크, 나이) — 판정 조건은 원본과
        # 한 글자도 다르지 않고, 값을 어디서 읽어오는지만 다르다.
        for e in scan:
            if e[2] == dst_team_id or e[1] in used_ids:
                continue
            cname_r = e[3]
            if same_country and cname_r != dst_cname:
                continue
            if (not same_country) and cname_r == dst_cname:
                continue
            if global_scouting and e[4] > dst_rank:
                continue   # 약한 목적지가 더 강한 리그에서 못 뺏어옴
            if _grp_counts.get(e[2], 0) <= 1:
                continue
            out.append(e)
        return out

    # ── [2026-09 최적화] 전세계 경로 numpy 구현 ──────────────────
    # _filter(False)(전세계 후보 수집) + 그 뒤의 가중추첨을 하나로 합친
    # 것과 정확히 같은 일을 한다. 판정 조건은 위 _filter의 것을 그대로
    # 옮겼고(순서만 다를 뿐 전부 AND라 결과집합 동일), 후보의 순서도
    # 원본과 같은 "정렬 리스트의 부분수열"이라 뽑히는 자리도 같다.
    # random.choices(pop, weights, k=1)의 내부 구현
    #   cum = list(accumulate(weights)); total = cum[-1] + 0.0
    #   return pop[bisect(cum, random() * total, 0, n - 1)]
    # 을 numpy로 그대로 재현한다 — np.cumsum은 순차 누적이라 부동소수점
    # 결과가 accumulate와 비트 단위로 같고, random()도 정확히 1회만 쓴다.
    _ok_all = team_pos_group_count.get("__ok__") if team_pos_group_count else None
    _ok_arr = _ok_all.get(_grp) if _ok_all else None
    _np_ready = (USE_NUMPY_GLOBAL_POOL and _HAS_NUMPY and _npm is not None
                 and _ok_arr is not None and _npm[7] < _ok_arr.size)

    def _pick_global_np():
        """전세계 후보를 마스크로 걸러 바로 1명을 뽑는다.
        후보가 없으면 None(난수 소비 없음)."""
        _t, _c, _r, _a, _u, _i2, _code, _tmax = _npm
        _ts = _t[i0:i1]
        m = (_ts != dst_team_id) & (~_u[i0:i1]) & (_c[i0:i1] != _code.get(dst_cname, -1))
        m &= _ok_arr[_ts]
        if global_scouting:
            m &= (_r[i0:i1] <= dst_rank)
        idx = np.flatnonzero(m)
        n = idx.size
        if n == 0:
            return None
        w = np.where(_a[i0:i1][idx] <= BUY_REPLACEMENT_YOUNG_AGE,
                     BUY_REPLACEMENT_YOUNG_WEIGHT, 1.0)
        cum = np.cumsum(w)
        total = float(cum[-1]) + 0.0
        j = int(np.searchsorted(cum, random.random() * total, side="right"))
        if j > n - 1:
            j = n - 1
        return rows_sorted[i0 + int(idx[j])]

    def _note_scan():
        # 파이썬 경로의 _cands_cell 계측과 같은 의미(전세계 슬라이스를
        # 실제로 훑었다)를 numpy 경로에서도 그대로 남긴다.
        if stats is not None:
            stats["global_scan_calls"] = stats.get("global_scan_calls", 0) + 1
            stats["global_scanned"] = stats.get("global_scanned", 0) + (i1 - i0)

    if global_scouting:
        if _np_ready:
            _note_scan()
            _hit = _pick_global_np()
            if _hit is not None:
                return _hit
            chosen = _filter(True)   # 없으면 자국으로 폴백
        else:
            chosen = _filter(False)   # 전세계(자국 제외) 우선
            if not chosen:
                chosen = _filter(True)   # 없으면 자국으로 폴백
    else:
        chosen = _filter(True)    # 기존 동작: 자국 우선
        if not chosen:
            if _np_ready:
                _note_scan()
                _hit = _pick_global_np()
                if _hit is not None:
                    return _hit
                chosen = []
            else:
                chosen = _filter(False)   # 없으면 해외로 폴백
    # [2026-09 계측] _cands_cell은 _global_cands()가 최소 한 번이라도
    # 호출됐을 때만(같은 함수 안에서 메모이즈) 채워진다 — 즉 이 호출이
    # global_scouting=True로 시작했든, 자국 우선이 실패해 해외로 폴백
    # 했든, "실제로 전세계 슬라이스를 훑었는지"를 이걸로 정확히 판별할
    # 수 있다(추가 조건 판정 없이 이미 있는 메모이즈 캐시를 그대로 읽음).
    if stats is not None and _cands_cell:
        _n = len(_cands_cell[0])
        stats["global_scan_calls"] = stats.get("global_scan_calls", 0) + 1
        stats["global_scanned"] = stats.get("global_scanned", 0) + _n
    if not chosen:
        return None
    weights = [BUY_REPLACEMENT_YOUNG_WEIGHT if (e[5] or 25) <= BUY_REPLACEMENT_YOUNG_AGE else 1.0
               for e in chosen]
    # chosen은 튜플 목록이지만 가중치 순서·개수가 원본과 같으므로 같은
    # 난수 스트림에서 같은 자리를 뽑는다 — 행만 꺼내 돌려준다.
    return random.choices(chosen, weights=weights, k=1)[0][0]


def _prestige_scouting(c, year):
    """[2026-09 신설, 신민용 요청: "유럽 1부리그 팀들, 특히 3급은 압도적인
    선수를, 2급/1급은 상대적으로 덜한 선수를 영입하려 해야 한다 — 토트넘은
    강등은 안 당해도 16~17위인 적이 있으니"] _retire_and_replace의 "은퇴
    자리 채우기"와 달리, 은퇴와 무관하게 명문팀이 시즌마다 상시로 스쿼드의
    약한 자리를 시장에서 스카우팅해 업그레이드를 시도한다. 등급이 높을수록
    후보를 훨씬 좁고 높은 상위권에서만 찾는다(PRESTIGE_SCOUT_TOP_
    PERCENTILE). 실제 돈이 오가는 이적료 협상을 시뮬레이션하지 않으므로
    (AI는 플레이어보다 단순해야 한다는 이 파일의 기존 원칙), 영입은 항상
    "그 자리의 지금 최약체 선수와 1:1 맞교환"으로 처리한다 — 그러면 양쪽
    팀 다 그 포지션 인원이 그대로 유지돼(같은 자리에 다른 선수가 들어올
    뿐) 별도의 보호 로직 없이도 스쿼드가 비는 사고가 안 생긴다.

    [2026-09 버그수정, 구현 직후 헤드리스 검증 중 자체 발견] prestige_
    clubs.PRESTIGE_TEAMS의 "명문 등급(1~3)"은 국가별 상대 등급이지
    세계 공통 절대 등급이 아니다(각 리그마다 그 나라 안에서의 명문일
    뿐 — 잠비아 무풀리라 원더러스도, 인도네시아 PSM 마카사르도 각자
    자국 최고 명문이라 3급으로 등록돼 있다). 처음 버전은 이걸 놓치고
    "3급이면 세계 상위 0.5%"를 그대로 적용해서, 잠비아 3급 팀이
    토트넘 선수를 스카우팅해가는 등 리그 격차를 완전히 무시한 결과가
    나왔다 — economy.LEAGUE_GRADE_RANK(국가 리그 등급 서열, F=1~SS=8)
    로 후보의 리그가 목적지 리그보다 강하면 후보에서 제외하도록 고쳤다
    (약한 리그가 강한 리그에서 뺏어오는 방향은 막고, 강한 리그 명문팀은
    세계 전체가 후보 풀인 건 그대로 유지 — SS/S급은 사실상 전세계가
    후보군이라 "유럽 1부리그는 특히 잘하는 선수를 영입" 요청과도
    맞아떨어진다).

    [2026-09 2차 버그수정, 신민용 리포트: "OVR82가 설계상한74인 한국으로
    이적해 들어온다" — 원인 재조사 중 발견한 두 번째 유입 경로] 위
    grade_rank 필터(letter 등급 서열)만으로는 못 잡는 사각지대가 있었다
    — 문자등급은 같은 B라도 COUNTRY_LEAGUE_OVR_OVERRIDE로 실제 설계
    상한이 크게 낮아진 나라(대한민국 등)가 있는데, 이 나라의 자국 명문팀
    (예: FC서울=1급, 울산/전북=2급, prestige_clubs.py 등록)은 letter
    등급이 B라 rank 필터를 그대로 통과하고, top_band 자체가 "전세계
    선수 pool의 백분위"라 grade_rank<=dst_rank(B이하 전부)를 만족하는
    다른 나라의 아웃라이어(오버라이드 없는 나라라 진짜로 OVR 80~90대가
    가능한 선수)를 그대로 데려올 수 있었다 — _do_one_transfer_cached에
    추가한 것과 동일한 _dst_ceiling_penalty를 여기 후보 선택에도 적용해,
    목적지(tid) 나라의 진짜 설계 상한(오버라이드 포함)을 후보 OVR이
    초과할수록 뽑힐 확률이 급격히 낮아지게 한다 — 기존 uniform random.
    choice(cands)를 가중치 기반 random.choices로 바꾸되, 초과분이
    없으면 가중치가 1.0으로 동일해 기존 동작과 100% 같다(회귀 없음).
    반환: 성사된 스카우팅 건수."""
    from constants import (PRESTIGE_SCOUT_BAND, PRESTIGE_SCOUT_ATTEMPTS_PER_SEASON,
                           PRESTIGE_SCOUT_MIN_GAP, get_ovr_range)
    from constants import get_country_league_grade
    from economy import LEAGUE_GRADE_RANK
    from data.prestige_clubs import PRESTIGE_TEAMS

    rows = c.execute(
        "SELECT id, team_id, position, age, ovr, name, nationality FROM ai_players").fetchall()
    pools = _build_buy_pools(rows)

    team_rows = c.execute(
        """SELECT t.id, t.name, t.current_tier AS tier, cn.name AS cname FROM teams t
           JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id""").fetchall()
    tid_by_name = {(t["cname"], t["name"]): t["id"] for t in team_rows}
    tinfo_by_tid = {t["id"]: (t["cname"], t["name"], t["tier"]) for t in team_rows}
    _grade_cache: dict = {}

    def _grade_rank_of(tid_):
        cname_ = cname_by_tid.get(tid_)
        if cname_ is None:
            return 1
        if cname_ not in _grade_cache:
            _grade_cache[cname_] = LEAGUE_GRADE_RANK.get(get_country_league_grade(cname_), 1)
        return _grade_cache[cname_]

    _grade_str_cache: dict = {}

    def _grade_of(tid_):
        cname_ = cname_by_tid.get(tid_)
        if cname_ is None:
            return "F"
        if cname_ not in _grade_str_cache:
            _grade_str_cache[cname_] = get_country_league_grade(cname_)
        return _grade_str_cache[cname_]

    cname_by_tid = {t["id"]: t["cname"] for t in team_rows}

    prestige_clubs = []  # [(team_id, level), ...]
    # [2026-09 재현성 버그수정] PRESTIGE_TEAMS[국가][등급]의 값은 set이다
    # (data/prestige_clubs.py 구조 주석 참고). 파이썬 3.7+에서 dict은
    # 삽입순서를 보존하지만 set은 원소 해시 순으로 순회하고, 문자열 해시는
    # PYTHONHASHSEED에 따라 실행마다 달라진다 — 그래서 정렬 없이 그냥
    # 순회하면 prestige_clubs의 초기 순서가 실행마다 바뀌고, 바로 아래
    # random.shuffle()이 "같은 RNG 상태 + 다른 입력 순서"를 받아 서로 다른
    # 순열을 내놓는다. 그 뒤 팀별로 도는 루프에서 random.shuffle(positions)의
    # 소비량(포지션 종류 수)이 팀마다 달라 전역 RNG 스트림 자체가 갈라졌고,
    # 이 시점 이후의 모든 난수가 어긋났다(같은 세이브·같은 시드로 두 번
    # 돌렸을 때 결과가 달라지던 근본 원인). 팀명 사전순으로 고정한다 —
    # 어차피 직후에 shuffle로 균등 섞기 때문에 밸런스에는 영향이 없다
    # (정렬은 "shuffle에 들어가는 입력 순서"를 결정론적으로 만들 뿐).
    for cname, levels in PRESTIGE_TEAMS.items():
        for level, names in levels.items():
            for tname in sorted(names):
                tid = tid_by_name.get((cname, tname))
                if tid is not None:
                    prestige_clubs.append((tid, level))
    random.shuffle(prestige_clubs)  # 등록 순서에 따른 편향 방지

    team_players: dict = {}
    for r in rows:
        team_players.setdefault(r["team_id"], []).append(r)

    used_ids: set = set()
    swap_updates = []
    log_rows = []
    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 1
    n_swaps = 0

    for tid, level in prestige_clubs:
        squad = team_players.get(tid, [])
        if not squad:
            continue
        dst_rank = _grade_rank_of(tid)
        # [2026-09 신설] 이 목적지 팀이 속한 나라의 진짜 설계 OVR 상한
        # (오버라이드 포함) — tid 하나당 한 번만 계산해 이 팀이 시도하는
        # 모든 포지션 스카우팅에 재사용한다. team_grade_rank 캐시들과
        # 동일하게 못 찾으면(깊은 tier 미정의 등) 43 폴백(위 dst_ovr_
        # ceiling_by_tid와 동일 관례).
        _dst_cname_r, _dst_tname_r, _dst_tier_r = tinfo_by_tid.get(tid, ("", "", 1))
        _dst_rng = get_ovr_range(_grade_of(tid), _dst_tier_r, _dst_cname_r)
        _dst_ceiling = _dst_rng[1] if _dst_rng else 43
        lo_pct, hi_pct = PRESTIGE_SCOUT_BAND.get(level, (0.03, 0.10))
        n_attempts = PRESTIGE_SCOUT_ATTEMPTS_PER_SEASON.get(level, 1)
        # [2026-09 재현성 버그수정] 위 prestige_clubs와 같은 유형 —
        # 집합 컴프리헨션 결과를 그대로 list()로 만들면 포지션 문자열의
        # 해시 순서(=PYTHONHASHSEED 의존)로 나열된다. 정렬해 고정하되,
        # 바로 아래 shuffle이 균등하게 섞으므로 어떤 포지션이 뽑히는지의
        # 확률 분포는 기존과 완전히 동일하다(밸런스 무영향).
        positions = sorted({p["position"] for p in squad})
        random.shuffle(positions)
        for pos in positions[:n_attempts]:
            weak = min((p for p in squad if p["position"] == pos and p["id"] not in used_ids),
                       key=lambda p: p["ovr"], default=None)
            if weak is None:
                continue
            pool = pools.get(pos)
            if not pool:
                continue
            # [2026-09] _build_buy_pools는 이제 5-튜플을 돌려준다
            # (_find_buy_replacement의 검색 최적화용). 여기선 정렬된 행
            # 리스트만 쓰므로 길이에 의존하지 않게 인덱스로 꺼낸다.
            rows_sorted = pool[0]
            n = len(rows_sorted)
            # [2026-09 수정] 등급별 구간을 서로 안 겹치게 분리 — 위
            # PRESTIGE_SCOUT_BAND 정의부 주석 참고. hi_cut(구간 시작,
            # 더 상위)~lo_cut(구간 끝, 더 하위) 사이만 후보로 삼는다.
            hi_cut = n - max(1, int(n * lo_pct))
            lo_cut = max(0, n - int(n * hi_pct))
            top_band = rows_sorted[lo_cut:hi_cut]
            cands = [r for r in top_band
                     if r["team_id"] != tid and r["id"] not in used_ids
                     and r["ovr"] >= weak["ovr"] + PRESTIGE_SCOUT_MIN_GAP
                     and _grade_rank_of(r["team_id"]) <= dst_rank]
            if not cands:
                continue
            # [2026-09 신설] letter 등급 필터만으론 못 거르는 "오버라이드로
            # 설계상한이 낮아진 나라의 명문팀이 다른 나라 아웃라이어를
            # 데려오는" 사각지대 보정. 처음엔 _dst_ceiling_penalty로 가중치만
            # 낮췄는데, top_band 후보 전원이 이미 상한을 넘는 경우(약한
            # 나라의 명문팀일수록 흔함)엔 weighted choice라도 그 중 하나를
            # 반드시 뽑아버려 사실상 무의미했다(실측으로 확인) — 초과분이
            # 큰 후보는 아예 후보 목록에서 제외(_dst_ceiling_excluded)하고,
            # 남은 후보끼리만 소프트 가중치(_dst_ceiling_penalty)로 뽑는다.
            # 전원 제외되면(그 나라 수준에 맞는 업그레이드가 이 시즌엔 없다는
            # 뜻) 이번 시도는 그냥 건너뛴다.
            cands = [r for r in cands if not _dst_ceiling_excluded(r["ovr"], _dst_ceiling)]
            if not cands:
                continue
            _cw = [_dst_ceiling_penalty(r["ovr"], _dst_ceiling) for r in cands]
            if sum(_cw) <= 0:
                target = random.choice(cands)
            else:
                target = random.choices(cands, weights=_cw, k=1)[0]
            used_ids.add(weak["id"])
            used_ids.add(target["id"])
            # [2026-09 신설, 신민용 요청: "이적이면 연봉이 써지는거고"]
            # 맞바꾼 두 선수 다 새 소속팀 기준으로 연봉을 다시 계산한다.
            _tid_cname, _tid_tname, _tid_tier = tinfo_by_tid.get(tid, ("", "", 1))
            _old_cname, _old_tname, _old_tier = tinfo_by_tid.get(target["team_id"], ("", "", 1))
            _target_salary = _calc_ai_salary(_grade_of(tid), _tid_tier, target["ovr"],
                                              _tid_cname, _tid_tname, tid, year)
            _weak_salary = _calc_ai_salary(_grade_of(target["team_id"]), _old_tier, weak["ovr"],
                                            _old_cname, _old_tname, target["team_id"], year)
            from economy import estimate_transfer_fee
            _fee = estimate_transfer_fee(_grade_of(tid), _tid_tier, target["ovr"],
                                          country=_tid_cname,
                                          position=target["position"], year=year) or 0
            # [2026-09 버그수정, 신민용 리포트: "2005년에 2년 계약했는데
            # 2007년까지 그대로 뜬다"] 위 _process_contract_renewals와 같은
            # effective_year 보정 누락 버그 — 여기(명문팀 스카우팅 맞교환)도
            # 항상 오프시즌 이적(is_mid_season=0, 아래 log_rows 참고)이라
            # 실제 발효는 year+1부터인데, 만료연도는 발효 전(year) 기준으로
            # 셌다. 그 결과 "N년 계약"이 표시상 (N-1)년으로 나오는 데 그치지
            # 않고, _process_contract_renewals의 재계약 판정(contract_end_
            # year<=year)까지 한 해 늦게 걸려 그 계약이 의도한 기간보다
            # 1년 더 길게 실제로 유지되는 문제로 이어졌다 — year+1부터
            # 세도록 통일.
            _weak_cend = year + 1 + random.randint(3, 5)
            _target_cend = year + 1 + random.randint(3, 5)
            swap_updates.append((target["team_id"], _weak_cend, year,
                                  _weak_salary, weak["id"]))
            swap_updates.append((tid, _target_cend, year,
                                  _target_salary, target["id"]))
            log_rows.append((_cur_season, year, target["id"], target["name"], target["position"],
                              target["age"] or 25, target["ovr"], target["team_id"], tid,
                              0, level, 0.0, 0.0, "명문팀 스카우팅", 0, "", _fee, 0, 0,
                              _target_salary, _target_cend))
            log_rows.append((_cur_season, year, weak["id"], weak["name"], weak["position"],
                              weak["age"] or 25, weak["ovr"], tid, target["team_id"],
                              level, 0, 0.0, 0.0, "명문팀 스카우팅(반대급부)", 0, "", 0, 0, 0,
                              _weak_salary, _weak_cend))
            n_swaps += 1

    if swap_updates:
        c.executemany(
            "UPDATE ai_players SET team_id=?, contract_end_year=?, last_transfer_year=?, "
            "salary=? WHERE id=?",
            swap_updates)
    if log_rows:
        c.executemany(
            """INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            log_rows)
    return n_swaps


def _retire_and_replace(c, year, ai_rows=None):
    """고령 선수 은퇴 → 같은 팀·같은 포지션에 신인 영입.
    [버그수정] 신인 목표 OVR을 team_avg 기반 → 리그 등급/tier OVR_RANGES 기반으로 변경.
    기존: team_avg가 낮으면 낮은 신인이 들어와 리그 전체 OVR이 해마다 하락하는 버그.
    수정: OVR_RANGES[grade][tier] 범위 하단~중간값을 신인 목표로 사용 → 리그 OVR 유지.
    [최적화] 팀 info 선조회 + 이름풀 캐시로 은퇴자마다 DB 왕복 제거.
    ai_rows: 호출부(run_ai_offseason)가 이미 조회해둔 ai_players 행
      (id,team_id,position,age,name)을 넘겨받아 재사용 — 이 함수와
      _transfer_market이 각자 같은 조건의 SELECT를 또 날리던 것을 없애
      전체 스캔 횟수를 줄인다(로직/결과는 완전히 동일). None이면(단독 호출
      등 하위호환) 기존처럼 이 함수가 직접 조회한다."""
    from constants import (OVR_RANGES, CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, SUB_ROLES,
                           get_country_league_grade, get_ovr_range, COUNTRY_LEAGUE_OVR_OVERRIDE)
    from database import _pick_nationality, get_foreign_quota_range
    # [2026-09 계측, 신민용 지적: "은퇴자 +21%인데 시간 +66% — 건당 비용
    # 자체가 악화되고 있다"] 이 함수를 한 덩어리로 보면 그 원인이 누적
    # 데이터(ai_players_retired 등)에 있는지 신인 생성에 있는지 구분이
    # 안 된다. 준비/판정루프/대체자탐색/신인생성/DB쓰기로 쪼갠다.
    import time as _time_rt
    _rt0 = _time_rt.perf_counter()
    _acc_buy = 0.0     # 후계자·대체자 탐색(_find_buy_replacement)
    _acc_stats = 0.0   # 신인 능력치 생성(_gen_stats)
    _acc_name = 0.0    # 신인 이름 배정(_random_name)
    retired = 0

    # 팀 → 리그등급/tier/보정치 선조회 (은퇴자마다 JOIN 방지)
    # [2026-07 확장] 국적 재배정(_pick_nationality)에 필요한 국가명/대륙도
    # 같이 캐싱한다 — 신인이 은퇴자의 옛 국적을 그대로 물려받던 버그 수정용.
    # [2026-07 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"] 아래
    # 명문팀 가산 로직이 은퇴자마다 "SELECT name FROM teams WHERE id=?"를
    # 따로 날리고 있었다 — 이 함수 전체가 "은퇴자마다 DB 왕복 제거"를
    # 원칙으로 세워놨는데 그 원칙을 깨는 N+1 쿼리였다(은퇴자가 많을수록,
    # 세이브가 오래될수록 이 함수가 계속 느려지던 원인 중 하나 — 실측
    # 로그에서 "은퇴·세대교체" 단계가 시즌이 지날수록 조금씩 늘어나는
    # 추세를 보였음). 팀 이름도 이 아래 team_info 캐시 SELECT 한 번에
    # 같이 담아서, 이후 루프에서는 dict 조회만 하도록 고친다.
    from data.prestige_clubs import is_prestige, prestige_level, PRESTIGE_LEVEL_OVR_BONUS
    from constants import (CLUB_STRENGTH_OVR_BONUS_K, CLUB_STRENGTH_OVR_BONUS_MIN,
                           CLUB_STRENGTH_OVR_BONUS_MAX, CLUB_STRENGTH_OVR_BONUS_MODE,
                           STAGNATION_TARGET_OVR_BONUS, STAGNATION_BUY_PROB_BONUS)
    # [2026-08 신설, 은퇴 시스템 tier 연동] 국가별 "가장 깊은 부수"를
    # 미리 조회해둔다 — 7부까지 있는 나라는 6~7부, 5부까지인 나라는
    # 5부가 그 나라의 "최하위"가 되도록, tier를 국가마다 다른 절대
    # 깊이가 아니라 "그 나라 안에서의 상대적 깊이(depth_ratio)"로 써야
    # 하기 때문(_retire_league_category 참고).
    country_max_tier = {r["cid"]: r["mt"] for r in c.execute(
        "SELECT country_id AS cid, MAX(tier) AS mt FROM leagues GROUP BY country_id").fetchall()}

    team_info = {}  # {team_id: (grade, tier, bonus, cname, continent, tname, club_strength, retire_cat, max_tier, momentum_type, momentum_seasons_left)}
    for r in c.execute(
            """SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                      t.club_strength AS club_strength,
                      t.momentum_type AS momentum_type,
                      t.momentum_seasons_left AS momentum_seasons_left,
                      cn.id AS cid, cn.name AS cname, cn.continent AS continent
               FROM teams t
               JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall():
        grade = get_country_league_grade(r["cname"])
        # [버그수정 2026-07, 신민용 리포트: "이적시장 처리 중 오류: 'float'
        # object cannot be interpreted as an integer"] COUNTRY_OVR_ADJ의
        # 소수점 조정치(대한민국 1.5, 세르비아 -1.5, 우루과이/콜롬비아/
        # 에콰도르 -0.5)가 그대로 더해지면 bonus가 float이 되고, 그게
        # lo/hi/mid를 전부 float으로 오염시켜 아래 random.randint(mid, hi)
        # 에서 바로 이 예외가 났다. 정수로 반올림해서 확정한다.
        bonus = round(CONTINENT_OVR_BONUS.get(r["continent"], 0) + COUNTRY_OVR_ADJ.get(r["cname"], 0))
        if grade == "SS":
            bonus = min(bonus, 0)
        _tier = r["tier"] or 1
        _max_tier = country_max_tier.get(r["cid"], _tier)
        _retire_cat = _retire_league_category(grade, _tier, _max_tier)
        team_info[r["tid"]] = (grade, _tier, bonus, r["cname"], r["continent"], r["tname"],
                                r["club_strength"] or 0.0, _retire_cat, _max_tier,
                                r["momentum_type"] or "", r["momentum_seasons_left"] or 0)

    # [2026-08 신설, 신민용 확정(GPT 협업): "월드컵 등 국제대회에 출전할
    # 정도면 29세 이전 은퇴는 이상하잖아"] 국가대표(어느 대회든 intl_squad
    # 명단에 한 번이라도 포함) / 월드컵 출전(kind='world' 대회의 명단
    # 포함) 여부를 한 번에 조회해둔다 — 30세 미만 조기 은퇴 확률에만
    # 배율로 적용(30세 이상은 원 표 그대로, 국제경력이 은퇴 자체를 막는
    # 조건이 아니라 "조기 은퇴"만 억제하는 보정이어야 하므로).
    _natteam_ids = {r["player_id"] for r in c.execute(
        "SELECT DISTINCT player_id FROM intl_squad").fetchall()}
    _wc_ids = {r["player_id"] for r in c.execute(
        """SELECT DISTINCT s.player_id FROM intl_squad s
           JOIN intl_tournaments t ON t.id = s.tournament_id
           WHERE t.kind='world'""").fetchall()}

    # [최적화] 이름풀 전체 1회 로드 (은퇴자마다 ORDER BY RANDOM() 방지)
    name_cache = _build_name_cache(c)
    _rt1 = _time_rt.perf_counter()   # 팀정보·국대명단·이름풀 조회까지
    # 팀→국가 캐시 초기화 (오프시즌 시작 시 리셋)
    _team_country_cache.clear()

    # [2026-08 신설, 진단용] DEBUG_PRESTIGE_TRACKING이 켜져있으면 추적
    # 대상 팀들의 "은퇴 전 평균 OVR"을 미리 스냅샷해둔다(비교 기준선).
    _dbg = {}
    if DEBUG_PRESTIGE_TRACKING:
        _dbg_name_to_tid = {info[5]: tid for tid, info in team_info.items()
                             if info[5] in DEBUG_PRESTIGE_TEAMS}
        for tname, tid in _dbg_name_to_tid.items():
            row = c.execute("SELECT AVG(ovr) v, COUNT(*) n FROM ai_players WHERE team_id=?",
                             (tid,)).fetchone()
            cs_row = c.execute("SELECT club_strength FROM teams WHERE id=?", (tid,)).fetchone()
            _dbg[tid] = {
                "name": tname, "tier": team_info[tid][1],
                "before_avg": round(row["v"], 1) if row and row["v"] else 0.0,
                "squad_n": row["n"] if row else 0,
                "club_strength": round((cs_row["club_strength"] or 0.0) if cs_row else 0.0, 2),
                "retired": 0, "new_ovrs": [],
            }

    # [최적화] 이름 중복방지 캐시 + 은퇴 대상 목록을 별도 두 번 풀스캔하던 것을
    #   컬럼을 합쳐 1회 SELECT로 통합했었고(5.9만 행 전체스캔 2회 → 1회),
    #   이제 그 SELECT 자체도 호출부에서 넘겨받은 ai_rows로 재사용해
    #   _transfer_market과의 중복 스캔까지 없앤다(3회 → 2회).
    _src_rows = ai_rows if ai_rows is not None else c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality FROM ai_players").fetchall()
    team_used_names: dict = {}
    rows = []
    # [2026-07 신설] 팀별 현재 외국인 수 카운터 — 신인 국적 재배정 시
    # 쿼터(FOREIGN_QUOTA_CAP)를 그대로 지키기 위해 필요.
    foreign_count_by_team: dict = {}
    for r in _src_rows:
        team_used_names.setdefault(r["team_id"], set()).add(r["name"])
        rows.append(r)
        tinfo = team_info.get(r["team_id"])
        if tinfo and r["nationality"] and r["nationality"] != tinfo[3]:
            foreign_count_by_team[r["team_id"]] = foreign_count_by_team.get(r["team_id"], 0) + 1
    retire_deletes = []  # 은퇴자 DELETE용
    retire_archives = []  # [2026-08 신설] 은퇴자 ai_players_retired 아카이브용
    new_rows = []         # 신인 INSERT용

    # [2026-09 신설, 신민용+GPT 협업: "명문팀은 은퇴자를 유망주 즉시
    # 생성으로 채우지 않고, 먼저 시장에서 검증된 선수를 영입 시도한다"]
    # 은퇴자마다 매번 새로 스캔하면 느리므로, 이 시즌의 후보 풀(포지션별
    # OVR 정렬)과 포지션그룹 인원수를 한 번만 만들어두고 아래 루프
    # 전체에서 재사용한다. used_buy_ids는 "이번 시즌에 이미 다른 은퇴
    # 자리를 채우러 뽑힌 선수"를 걸러내는 용도(같은 선수가 한 시즌에
    # 두 번 팔려나가는 것 방지).
    from constants import BUY_REPLACEMENT_PROB_BY_GRADE, BIG_CLUB_PRESTIGE_THRESHOLD
    _rt2 = _time_rt.perf_counter()   # 선수행 전처리(이름/외국인 카운터)까지
    _buy_pools = _build_buy_pools(rows, team_info)
    _buy_pos_group_count = _build_team_pos_group_count(rows)
    _buy_used_ids: set = set()
    # [2026-09 계측, 신민용 지적: "은퇴자 +16%인데 대체자탐색 시간 +92% —
    # 후보 풀은 오히려 줄었으니 단순 O(N) 증가가 아니다"] global_scouting
    # (목적지가 SS/S급·프레스티지2+일 때 국내 우선 대신 전세계 우선으로
    # 찾는 경로 — 위 _find_buy_replacement 주석 참고)이 해가 갈수록 더
    # 자주 발동돼서(부익부로 SS/S·명문팀 비율 자체가 늘어남) 그 안에서
    # 매번 훑는 전세계 슬라이스(_global_cands, 국가 서브풀보다 훨씬 큼)
    # 비중이 늘어난 게 원인이라는 가설을 세웠다 — 이 dict 하나로 그
    # 가설을 실측 확인한다(호출 횟수 자체는 어차피 세는 거라 오버헤드는
    # 카운터 몇 개 증가뿐, 새 쿼리·새 루프 없음). 아래 _find_buy_
    # replacement 호출마다 채워지고, 함수 끝 RETIRE-PERF 로그 한 줄에
    # 그대로 붙인다.
    _buy_stats: dict = {}
    transfer_updates = []    # (new_team_id, contract_end_year, last_transfer_year, player_id)
    transfer_log_rows = []   # ai_transfer_log INSERT용
    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 1

    _rt3 = _time_rt.perf_counter()   # 후보풀 구축까지

    for r in rows:
        age = r["age"] or 25
        if age < _AI_RETIRE_AGE:
            continue
        _tinfo_r = team_info.get(r["team_id"])
        _cat_r = _tinfo_r[7] if _tinfo_r else "mid"
        _intl_factor = 0.2 if r["id"] in _wc_ids else (0.5 if r["id"] in _natteam_ids else 1.0)
        # [2026-09 신설] 그 리그 기준으로 이 선수가 에이스급인지 겨우
        # 버티는 수준인지 — _relative_ovr_retire_mult 정의부 주석 참고.
        _rel_mult = (_relative_ovr_retire_mult(r["ovr"], _tinfo_r[0], _tinfo_r[1], _tinfo_r[3],
                                                age, _tinfo_r[8])
                     if _tinfo_r else 1.0)
        # [2026-09 신설] 토니 크로스형 "커리어 완성" 은퇴 — _career_finish_bonus
        # 정의부 주석 참고, 실력(relative_mult)과 무관하게 더해지는 값.
        _finish_bonus = (_career_finish_bonus(r["id"], age, _tinfo_r[0], _tinfo_r[1])
                          if _tinfo_r else 0.0)
        p_retire = _ai_retirement_probability(age, r["ovr"], r["position"],
                                               category=_cat_r, intl_factor=_intl_factor,
                                               relative_mult=_rel_mult,
                                               career_finish_bonus=_finish_bonus)
        if p_retire <= 0 or random.random() >= p_retire:
            continue

        # [버그수정] 신인 목표 OVR: 리그 등급/tier OVR_RANGES 하단~중간 범위
        #  + 대륙/나라 보정. [조정] 예전엔 중간값+5까지 허용해서 신인이 데뷔부터
        #  거의 에이스급으로 들어왔다(A등급 기준 82~91). 하단~중간(82~86)으로
        #  좁혀서, 실제로 몇 시즌 성장해야 에이스 근처에 도달하도록 한다.
        (grade, tier, _bonus, cname, continent, _tname, _club_strength, _cat_unused, _mt_unused,
         _mom_type, _mom_left) = team_info.get(
            r["team_id"], ("D", 1, 0, "", "유럽", "", 0.0, "mid", 1, "", 0))
        # [2026-09 신설, "중위권 정체 탈출" momentum] 이 팀이 지금 그
        # momentum이 활성 상태인지 — constants.STAGNATION_TARGET_OVR_BONUS/
        # STAGNATION_BUY_PROB_BONUS 정의부 주석 참고. club_strength 보너스와
        # 별개로 대체 선수 목표 OVR·시장 영입 확률에 직접 가산한다.
        _stag_active = _mom_left > 0 and _mom_type.startswith("mid_table_stagnation")
        # [2026-08] COUNTRY_LEAGUE_OVR_OVERRIDE 등록국이면 최우선 사용 —
        # 이미 그 나라 실측에 맞춘 값이라 대륙/국가 보정(_bonus)은 중복
        # 적용하지 않는다(초기 시딩의 _tier_top_ovr(country=...)와 동일 원칙).
        # [2026-08 버그수정, 신민용 리포트: "K1 OVR을 내렸더니 K2랑 겹친다"]
        # 예전엔 이 판정이 tier==1일 때만 걸려서, tier2 이하 신인은
        # get_ovr_range()가 이미 델타-캐스케이드한 값 위에 _bonus까지 또
        # 더해지는 이중보정이 있었다 — get_ovr_range 자체가 이제 모든
        # tier에서 오버라이드를 반영하므로, 여기 판정도 tier 무관하게
        # 국가 등록 여부만 본다.
        _is_override = cname in COUNTRY_LEAGUE_OVR_OVERRIDE
        ovr_rng = get_ovr_range(grade, tier, cname)
        _plvl = 0  # [2026-08 신설] 아래 분기 중 하나에서만 채워지므로 기본값 선정의
        if ovr_rng:
            lo, hi = ovr_rng
            if not _is_override:
                lo, hi = lo + _bonus, hi + _bonus
            mid = (lo + hi) // 2
            # [2026-07 버그수정, 신민용 리포트: "명문팀이 계속 강등당한다"]
            # 예전엔 항상 '하단~중간'에서 뽑고 명문팀이면 그 위에 그냥
            # +2~5만 더했다 — 그런데 게임 초반 시딩(_generate_all_ai_players
            # → weighted_team_order)은 "명문팀은 강한 슬롯을 뽑을 확률이
            # 훨씬 높되(PRESTIGE_WEIGHT=6.0) 100%는 아니다"라는 철학이었다.
            # 신인 교체가 이 철학을 안 따르고 매번 '하단~중간 + 소폭 보정'만
            # 하다 보니, 명문팀 선수단이 은퇴로 교체될수록(대략 10~15년 후
            # 전체 세대교체) 원래 시딩 때 받았던 우위가 사라지고 리그 평균
            # 수준으로 수렴해버렸다 — 그래서 시간이 지날수록 명문팀이 점점
            # 강등권에 가까워지는 정확히 그 증상이었다. 이제 명문팀은
            # weighted_team_order와 같은 확률(PRESTIGE_WEIGHT 기반)로
            # '중간~상단'에서 뽑을 확률이 훨씬 높게 하되, 완전히 배제하진
            # 않는다(가끔은 평범한 신인도 나와야 "명문팀도 가끔 훅 간다"가
            # 재현됨).
            # [2026-07 확률 보정] 처음엔 random()**(1/PRESTIGE_WEIGHT)>=0.5 조건을
            # 썼는데, 실측 시뮬레이션해보니 98.4% 확률로 상단이 나와서 원래
            # 설계 문서(prestige_clubs.py 상단 주석)가 말하는 "대략 10~20%
            # 안팎만 하위권"이라는 의도보다 훨씬 강했다(거의 100% 고정 강세와
            # 다를 게 없어짐). 의도한 비율(상단 85%, 하위 15%)을 직접
            # 상수로 명시한다.
            _PRESTIGE_UPPER_PROB = 0.85
            _is_prestige_team = is_prestige(cname, tier, _tname)
            _use_upper = _is_prestige_team and (random.random() < _PRESTIGE_UPPER_PROB)
            if _use_upper:
                target = random.randint(mid, hi)
            else:
                target = random.randint(lo, mid)
            # [2026-08 신설] prestige_level(3/2/1) 가산 보너스 — 85/15 확률
            # 편향과 역할을 분리한다: 85/15는 "명문팀이 좋은 세대교체를 할
            # 가능성"을, 이 가산은 "3급/2급/1급 사이의 지속적인 질적 차이"를
            # 담당한다(PRESTIGE_LEVEL_OVR_BONUS 정의부 주석 참고). 강등된
            # 명문팀도 현재 tier 기준 범위(lo~hi) 위에 이 보너스만 얹힐 뿐,
            # 원래 tier로 강제 복귀되지는 않는다 — 강등의 의미는 유지된다.
            _plvl = prestige_level(cname, _tname)
            if _plvl:
                target += PRESTIGE_LEVEL_OVR_BONUS.get(_plvl, 0)
        else:
            # [버그수정 2026-07] 그 등급에 이 tier가 정의 안 돼 있으면(부수가
            # 늘었는데 표를 못 채운 경우) 고정 30~45가 아니라, 그 등급 안에서
            # 정의된 가장 깊은 부수 기준 단계별 감쇠 값을 쓴다 — database._tier_top_ovr
            # 과 동일한 감쇠 방식이라, 등급표 밖 tier라도 "한 단계 위보다는
            # 확실히 낮고, SS/S 같은 상위 등급이 갑자기 완전히 다른 등급처럼
            # 뚝 떨어지지 않는" 자연스러운 값이 된다.
            grade_ranges = OVR_RANGES.get(grade, {})
            if grade_ranges:
                deepest_tier = max(grade_ranges)
                deepest_lo, deepest_hi = grade_ranges[deepest_tier]
                STEP = 8
                extra = (tier - deepest_tier) * STEP
                lo = max(15, deepest_lo - extra) + _bonus
                hi = max(lo + 1, deepest_hi - extra) + _bonus
                target = random.randint(lo, (lo + hi) // 2)
            else:
                target = random.randint(30, 45)
                hi = target  # [방어] 이 극단적 폴백 경로엔 hi가 없어 아래 명문팀 가산에서 참조 에러 방지

        # [2026-07 수정] 명문팀 보정은 이제 위 target 산출 시점(중간~상단 확률
        # 편향)에서 이미 반영되므로, 여기서 별도로 다시 가산하지 않는다 —
        # 예전엔 여기서 +2~5를 또 더했는데, 그러면 이중 보정이 된다.
        # [2026-07 최적화] 팀 이름은 위 team_info 캐시에서 바로 꺼낸다
        # (원래 여기서 은퇴자마다 "SELECT name FROM teams WHERE id=?"를
        # 따로 날렸던 N+1 쿼리였음 — 함수 상단 주석 참고).

        # [2026-08 신설, 신민용 확정: "club_strength가 경기력엔 반영되는데
        # 정작 선수단엔 안 이어진다"] 위 PRESTIGE_LEVEL_OVR_BONUS(정적
        # 명문 리스트 전용, 강등돼도 안 바뀌는 고정값)와 별개로, "그 세이브
        # 안에서 실제로 지금 강한/약한 팀인지"를 나타내는 club_strength를
        # 신인 목표 OVR에도 반영한다. 명문 리스트에 없는 팀도 실적으로
        # club_strength를 쌓으면 똑같이 이 보정을 받는다(원래 설계 철학
        # "명문이라서가 아니라 강해서 보호"와 일치). 1차 실험이라 기존
        # PRESTIGE_LEVEL_OVR_BONUS는 그대로 두고 이 보정을 추가로 얹는다
        # — 어느 쪽 효과인지 나중에 구분해서 조정할 수 있게.
        _cs_bonus = max(CLUB_STRENGTH_OVR_BONUS_MIN,
                         min(CLUB_STRENGTH_OVR_BONUS_MAX, _club_strength * CLUB_STRENGTH_OVR_BONUS_K))
        if CLUB_STRENGTH_OVR_BONUS_MODE == "positive_only":
            _cs_bonus = max(0.0, _cs_bonus)
        elif CLUB_STRENGTH_OVR_BONUS_MODE == "off":
            _cs_bonus = 0.0
        target += _cs_bonus

        # [2026-09 신설, "중위권 정체 탈출" momentum] 위 club_strength 보정과
        # 별개로, 그 팀이 지금 이 momentum이 활성 상태면 대체 선수 목표 OVR에
        # 추가로 더한다(constants.STAGNATION_TARGET_OVR_BONUS). 순위 자체를
        # 직접 보정하는 게 아니라 "다음 세대는 조금 더 강하게 뽑아 온다"는
        # 간접 효과다.
        if _stag_active:
            target += STAGNATION_TARGET_OVR_BONUS.get(_mom_type, 0.0)

        # [2026-09 신설, 신민용+GPT 협업: "명문팀은 은퇴자를 유망주 즉시
        # 생성으로 채우지 않고, 먼저 시장에서 검증된 선수를 영입 시도한다
        # — 정말 적합한 선수가 없을 때만 자체 유스 생성을 fallback으로
        # 쓴다"] 등급이 높을수록(명문 등급이면 최소 S급 취급)
        # "영입으로 채울 확률"이 높다(BUY_REPLACEMENT_PROB_BY_GRADE). 이
        # 확률에 걸리면 target(방금 확정한 성인 잠재치)을 목표로 시장에서
        # 후보를 찾고, 찾으면 아래 유스 생성 전체를 건너뛰고 그 선수를
        # 이 팀으로 이적시킨다 — 못 찾으면(확률 미달 포함) 그대로 기존
        # 유스 생성으로 이어진다.
        _buy_grade = grade
        if _plvl >= BIG_CLUB_PRESTIGE_THRESHOLD and _buy_grade not in ("SS", "S"):
            _buy_grade = "S"
        _buy_prob = BUY_REPLACEMENT_PROB_BY_GRADE.get(_buy_grade, 0.10)
        # [2026-09 신설, "중위권 정체 탈출" momentum] 위 target 가산과 같은
        # 이유 — 시장에서 검증된 선수를 사려는 시도 자체를 더 자주 하게
        # 만든다(constants.STAGNATION_BUY_PROB_BONUS). 0.97 상한은 다른
        # 확률 캡과 동일한 관례(완전한 100%는 피함).
        if _stag_active:
            _buy_prob = min(0.97, _buy_prob + STAGNATION_BUY_PROB_BONUS.get(_mom_type, 0.0))
        if random.random() < _buy_prob:
            # [2026-09 신설, 신민용 확정: "국가 등급은 좋은 선수가 나올
            # 확률에만 영향을 줘야지, 이미 나온 좋은 선수가 어디로 갈지를
            # 국내 우선 검색으로 가둬버리면 안 된다"] 목적지가 SS/S급
            # 리그거나(원래도 여기서 이미 계산돼 있는 grade) 프레스티지
            # 2급 이상(_plvl, 위에서 이미 계산됨)이면 _find_buy_replacement가
            # 국내 우선 대신 전세계 우선으로 찾도록 플래그만 넘긴다 —
            # 판정 기준을 새로 만들지 않고 이 시점에 이미 있는 계산을
            # 재사용(brazil 등 선수 풀이 큰 나라의 폐쇄 루프 완화용).
            _global_scouting = grade in ("SS", "S") or _plvl >= BIG_CLUB_PRESTIGE_THRESHOLD
            # [2026-09 계측] "SS/S·프레스티지2+ 목적지 비율이 해마다
            # 늘어나는가"를 바로 검증할 수 있게, 실제 목적지 등급(_buy_grade
            # — grade에 프레스티지 오버라이드까지 반영된 값) 분포를 호출
            # 시점에 그대로 센다. _find_buy_replacement 내부가 아니라
            # 여기서 세는 이유: 이 함수는 grade/등급 개념을 아예 모르고
            # (position/OVR/팀ID만 받음) 그걸 위해 파라미터를 새로 늘리는
            # 것보다, 이미 계산해둔 값을 호출부에서 바로 집계하는 쪽이
            # 더 가볍고 함수 책임도 안 섞인다.
            _buy_stats.setdefault("by_grade", {})
            _buy_stats["by_grade"][_buy_grade] = _buy_stats["by_grade"].get(_buy_grade, 0) + 1
            _tb0 = _time_rt.perf_counter()
            _bought = _find_buy_replacement(
                r["position"], round(target), r["team_id"], cname,
                _buy_pools, team_info, _buy_pos_group_count, _buy_used_ids,
                global_scouting=_global_scouting, stats=_buy_stats)
            _acc_buy += _time_rt.perf_counter() - _tb0
            if _bought is not None:
                _buy_used_ids.add(_bought["id"])
                # [2026-09 최적화] 위 set과 짝을 이루는 numpy used 마스크
                # 갱신 — 둘이 어긋나면 전세계 경로가 다른 선수를 뽑게
                # 되므로 반드시 같은 자리에서 같이 갱신한다.
                _np_mark_buy_used(_buy_pools, _bought)
                _bgrp = _POS_GROUP.get(_bought["position"], "FW")
                _bg = _buy_pos_group_count.setdefault(_bgrp, {})
                _btid = _bought["team_id"]
                _bg[_btid] = max(0, _bg.get(_btid, 1) - 1)
                _np_sync_grp_ok(_buy_pos_group_count, _bgrp, _btid, _bg[_btid])
                _src_tinfo = team_info.get(_bought["team_id"])
                _src_plvl = prestige_level(_src_tinfo[3], _src_tinfo[5]) if _src_tinfo else 0
                # [2026-09 신설] 영입한 선수의 국적/이름을 이 팀의 카운터에도
                # 반영해둔다 — 안 하면 같은 팀의 다른 은퇴자리가 (자체
                # 생성으로 이어질 경우) 외국인 쿼터를 실제보다 여유있게
                # 계산하거나, 이름이 겹치는 신인을 만들 수 있다.
                _bought_nat = _bought["nationality"] if "nationality" in _bought.keys() else ""
                if _bought_nat and _bought_nat != cname:
                    foreign_count_by_team[r["team_id"]] = foreign_count_by_team.get(r["team_id"], 0) + 1
                _src_cname = _src_tinfo[3] if _src_tinfo else ""
                if _bought_nat and _bought_nat != _src_cname:
                    foreign_count_by_team[_bought["team_id"]] = max(
                        0, foreign_count_by_team.get(_bought["team_id"], 0) - 1)
                team_used_names.setdefault(r["team_id"], set()).add(_bought["name"])
                # [2026-09 신설, 신민용 요청: "이적이면 연봉이 써지는거고
                # 오퍼도 있고"] 새 소속팀 기준으로 연봉을 다시 계산하고,
                # 이적료도 계산해 로그에 남긴다(예전엔 표시용으로만 즉석
                # 계산하고 버렸는데, 이제 실제로 저장한다).
                _new_salary = _calc_ai_salary(grade, tier, _bought["ovr"], cname, _tname,
                                               r["team_id"], year)
                from economy import estimate_transfer_fee
                _fee = estimate_transfer_fee(grade, tier, _bought["ovr"], country=cname,
                                              position=_bought["position"], year=year) or 0
                # [2026-09 버그수정, 신민용 리포트: "2005년에 2년 계약했는데
                # 2007년까지 그대로 뜬다"] 위 _process_contract_renewals/
                # 명문팀 스카우팅과 같은 effective_year 보정 누락 — 여기
                # (은퇴대체 시장영입)도 항상 오프시즌(is_mid_season=0, 아래
                # transfer_log_rows 참고)이라 실제 발효는 year+1부터인데
                # 만료연도를 발효 전(year) 기준으로 셌다. year+1부터 세도록
                # 통일 — 그래야 표시 기간도 정확해지고, 재계약 판정
                # (contract_end_year<=year)도 의도한 시점에 걸린다.
                _bought_cend = year + 1 + random.randint(3, 5)
                transfer_updates.append((
                    r["team_id"], _bought_cend, year, _new_salary, _bought["id"]))
                transfer_log_rows.append((
                    _cur_season, year, _bought["id"], _bought["name"], _bought["position"],
                    _bought["age"] or 25, _bought["ovr"], _bought["team_id"], r["team_id"],
                    _src_plvl or 0, _plvl or 0, 0.0, 0.0, "은퇴대체 영입", 0, "", _fee, 0, 0,
                    _new_salary, _bought_cend))
                retire_deletes.append((r["id"],))
                retire_archives.append((r["id"], r["name"], r["position"], r["ovr"], age,
                                         r["nationality"], r["team_id"],
                                         team_info.get(r["team_id"], (None,) * 6)[5], year))
                retired += 1
                continue

        # [2026-08 버그수정, 위 _youth_target_scale 주석 참고] 나이를
        # 먼저 뽑아서, target(성인 잠재치)을 그 나이에 맞게 낮춘 뒤
        # 스탯을 생성한다 — 예전엔 new_age를 스탯 생성 이후에 뽑아서
        # 전혀 반영이 안 되고 있었다.
        new_age = random.randint(*_AI_NEWBIE_AGE)
        _scaled_target = _youth_target_scale(target, new_age)
        # [2026-08 신설, 신민용 리포트: "OVR81따리가 레알 마드리드나
        # 바르셀로나에 있을 수 있냐"] database._generate_team_players와
        # 동일한 명문팀 바닥(prestige_level>=2)을 신인 교체 경로에도
        # 적용 — 진짜 명문팀(레알/바르사급)은 유스 신인이라도 그 등급/
        # 부수 하한 대비 너무 크게 못 내려가게 한다.
        # [2026-08 재설계 — database._generate_team_players와 동일한
        # Prestige×리그등급 표로 교체(신민용 확정, GPT 협업). 산하팀 보유
        # 여부는 여기 섞지 않는다 — 별도 시스템 몫.
        if ovr_rng:
            _prestige_base = {3: 1, 2: 2, 1: 3}.get(_plvl, 4)
            _grade_adj = {"SS": 0, "S": 0, "A": 0, "B": 1, "C": 1,
                         "D": 2, "E": 2, "F": 3}.get(grade, 2)
            _young_floor_off = _prestige_base + _grade_adj
            _scaled_target = max(_scaled_target, ovr_rng[0] - _young_floor_off)
        _tg0 = _time_rt.perf_counter()
        stats = _gen_stats(r["position"], _scaled_target)
        _acc_stats += _time_rt.perf_counter() - _tg0
        new_ovr = calc_ovr(r["position"], stats)
        # [2026-08 신설, 진단용] 추적 대상 팀이면 이번에 생성된 신인 OVR을 기록.
        if DEBUG_PRESTIGE_TRACKING and r["team_id"] in _dbg:
            _dbg[r["team_id"]]["retired"] += 1
            _dbg[r["team_id"]]["new_ovrs"].append(new_ovr)
            _dbg[r["team_id"]].setdefault("cs_bonuses", []).append(round(_cs_bonus, 2))
        # [세부역할 2026-07] 새 신인은 은퇴자의 예전 세부역할을 물려받지 않고
        # 그 포지션에 맞는 SUB_ROLES 중 하나를 새로 무작위 배정한다.
        new_sub_role = random.choice(SUB_ROLES.get(r["position"], ["기본"]))
        # [2026-07 신설, 신민용 지적: "은퇴하면 새 선수 들어오는데 국적도
        # 새로 뽑아야지, 안 그러면 은퇴자 국적을 그대로 물려받는다"] 은퇴자가
        # 외국인이었으면 먼저 카운터에서 빼고, 새 국적을 다시 뽑는다.
        tid = r["team_id"]
        old_nat = r["nationality"] if "nationality" in r.keys() else ""
        cur_foreign = foreign_count_by_team.get(tid, 0)
        if old_nat and old_nat != cname:
            cur_foreign = max(0, cur_foreign - 1)
        _q_lo, quota = get_foreign_quota_range(cname, continent)
        new_nat, cur_foreign = _pick_nationality(cname, continent, grade, r["position"],
                                                  False, cur_foreign, quota)
        foreign_count_by_team[tid] = cur_foreign
        # 팀 내 중복 방지: used_in_team에 팀 현재 이름 set 전달
        used = team_used_names.setdefault(r["team_id"], set())
        _tn0 = _time_rt.perf_counter()
        name = _random_name(c, r["team_id"], name_cache, used_in_team=used)
        _acc_name += _time_rt.perf_counter() - _tn0
        # [2026-08 버그수정, 신민용 리포트: "AI5가 은퇴하면 AI5가 다시
        # 생기는 게 아니라 AI11이 나타나야 하고, AI5는 그 은퇴한 선수로
        # 남아있어야 한다"] 예전엔 은퇴 교체를 "같은 행을 UPDATE"로
        # 처리했다 — ai_player_code()가 ai_players.id를 그대로 코드로
        # 쓰는데, 같은 id를 재활용하면 "AI0005"라는 코드가 은퇴 전엔
        # 베테랑이었다가 은퇴 후엔 완전히 다른 신인을 가리키게 되어,
        # 코드가 특정 선수의 영구적인 정체성이 아니라 그냥 "로스터 자리
        # 번호"가 되어버렸다. id는 AUTOINCREMENT라 삭제해도 그 번호가
        # 재사용되지 않으므로, 이제 은퇴자 행은 그대로 DELETE하고 신인은
        # INSERT로 새 id를 받는다 — 은퇴한 선수의 코드는 그 선수에게
        # 영구히 남고, 신인은 한 번도 안 쓰인 새 코드를 받는다. team_id
        # (팀은 그대로), position(같은 자리 채움)만 은퇴자와 동일하게
        # 넣고, 나머지는 전부 새로 생성된 값.
        retire_deletes.append((r["id"],))
        # [2026-08 신설, 신민용 요청: "은퇴하면... 얘네도 차후 검색할 수
        # 있어야 해"] DELETE 전에 은퇴 직전 스냅샷(마지막 OVR/나이/포지션/
        # 국적/마지막 소속팀)을 같은 id로 아카이브 테이블에 남겨서,
        # ai_player_code(id)가 은퇴 후에도 계속 이 선수를 가리키게 한다.
        retire_archives.append((r["id"], r["name"], r["position"], r["ovr"], age,
                                 r["nationality"], r["team_id"],
                                 team_info.get(r["team_id"], (None,) * 6)[5], year))
        new_rows.append((
            r["team_id"], name, r["position"],
            *[stats[s] for s in ALL_STATS], new_ovr, new_age, new_sub_role, new_nat,
            year + random.randint(3, 5), 0, year,
            _calc_ai_salary(grade, tier, new_ovr, cname, _tname, r["team_id"], year)))
        retired += 1

    _rt4 = _time_rt.perf_counter()   # 은퇴판정+대체자탐색+신인생성 루프까지

    if retire_archives:
        c.executemany(
            """INSERT OR REPLACE INTO ai_players_retired
               (id, name, position, ovr, age, nationality, last_team_id,
                last_team_name, retirement_year)
               VALUES(?,?,?,?,?,?,?,?,?)""", retire_archives)
    _rt5 = _time_rt.perf_counter()   # ai_players_retired 아카이브 적재
    if retire_deletes:
        c.executemany("DELETE FROM ai_players WHERE id=?", retire_deletes)
    _rt6 = _time_rt.perf_counter()   # ai_players 은퇴자 DELETE
    if new_rows:
        c.executemany(
            f"""INSERT INTO ai_players
                (team_id,name,position,{_STAT_COLS},ovr,age,sub_role,nationality,
                 contract_end_year,last_transfer_year,created_year,salary)
                VALUES(?,?,?,{','.join('?' for _ in ALL_STATS)},?,?,?,?,?,?,?,?)""",
            new_rows)
    _rt7 = _time_rt.perf_counter()   # ai_players 신인 INSERT
    # [2026-09 신설] 위 "명문팀 은퇴대체 영입" 건 — 신인 INSERT(new_rows)와
    # 완전히 별개라, new_rows가 비어있어도(이번 시즌 유스 생성이 하나도
    # 없었어도) 항상 독립적으로 반영돼야 한다(예전엔 이 블록 전체가
    # `if new_rows:` 안에 있어서, 영입만 있고 유스 생성이 하나도 없는
    # 극단적인 시즌엔 은퇴자 아카이브/삭제까지 통째로 스킵될 뻔한 버그였음
    # — 위로 끌어올려 new_rows와 무관하게 항상 실행되도록 이미 고쳐둠).
    if transfer_updates:
        c.executemany(
            "UPDATE ai_players SET team_id=?, contract_end_year=?, last_transfer_year=?, "
            "salary=? WHERE id=?",
            transfer_updates)
    if transfer_log_rows:
        c.executemany(
            """INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            transfer_log_rows)
    _rt8 = _time_rt.perf_counter()   # 이적(명문팀 영입) 반영+로그
    # [2026-09 계측, 신민용 지적: "global_scouting 비율/스캔량을 연도별로
    # 남겨서 은퇴자+16%/시간+92% 불일치의 원인을 확정하자"] _buy_stats는
    # 위 _find_buy_replacement 호출부/함수 내부에서 카운터 증가만으로
    # 채워진 값이라 여기선 나눗셈 몇 번뿐 — 매 시즌 항상 켜둬도 비용이
    # 없다(다른 PERF-* 로그들과 동일한 원칙).
    _buy_calls = _buy_stats.get("calls", 0)
    _buy_global_calls = _buy_stats.get("global_calls", 0)
    _buy_global_pct = (_buy_global_calls / _buy_calls * 100) if _buy_calls else 0.0
    _buy_scan_calls = _buy_stats.get("global_scan_calls", 0)
    _buy_scanned = _buy_stats.get("global_scanned", 0)
    _buy_avg_slice = (_buy_scanned / _buy_scan_calls) if _buy_scan_calls else 0.0
    _buy_grade_txt = " ".join(
        f"{g}:{n}" for g, n in sorted(_buy_stats.get("by_grade", {}).items(),
                                        key=lambda kv: -kv[1])) or "-"
    _perf_log(
        f"[RETIRE-PERF] {year}년 은퇴·세대교체 {_rt8-_rt0:.2f}s "
        f"(은퇴 {retired}명 / 후보 {len(rows)}명) 세부: "
        f"팀정보·명단조회 {_rt1-_rt0:.2f}s | 선수행전처리 {_rt2-_rt1:.2f}s | "
        f"후보풀구축 {_rt3-_rt2:.2f}s | "
        f"판정·생성루프 {_rt4-_rt3:.2f}s (대체자탐색 {_acc_buy:.2f}s · "
        f"신인능력치 {_acc_stats:.2f}s · 신인이름 {_acc_name:.2f}s · "
        f"그 외 {(_rt4-_rt3)-_acc_buy-_acc_stats-_acc_name:.2f}s) | "
        f"retired적재 {_rt5-_rt4:.2f}s | 은퇴자DELETE {_rt6-_rt5:.2f}s | "
        f"신인INSERT {_rt7-_rt6:.2f}s | 이적반영+로그 {_rt8-_rt7:.2f}s")
    _perf_log(
        f"[BUY-SCOUT-PERF] {year}년 대체자탐색 계측: "
        f"calls={_buy_calls} | global={_buy_global_calls}({_buy_global_pct:.1f}%) | "
        f"global스캔={_buy_scan_calls}회 총{_buy_scanned}건 평균슬라이스={_buy_avg_slice:.0f} | "
        f"목적지등급분포=[{_buy_grade_txt}]")

    # [2026-08 신설, 진단용] 추적 대상 팀들의 이번 시즌 은퇴/신인 교체 요약을
    # 한 줄씩 찍는다 — "강등 → 낮은 OVR 신인 → 추가 강등" 루프가 실제로
    # 발생하는지 시즌별로 눈으로 확인하기 위함.
    if DEBUG_PRESTIGE_TRACKING:
        for tid, d in _dbg.items():
            n_new = len(d["new_ovrs"])
            new_avg = round(sum(d["new_ovrs"]) / n_new, 1) if n_new else None
            cs_bonuses = d.get("cs_bonuses", [])
            cs_bonus_avg = round(sum(cs_bonuses) / len(cs_bonuses), 2) if cs_bonuses else None
            print(f"[PRESTIGE-DEBUG] {year}년 {d['name']} (현재 {d['tier']}부): "
                  f"교체전 스쿼드평균 {d['before_avg']}({d['squad_n']}명) | "
                  f"은퇴/교체 {d['retired']}명 | 신인평균OVR {new_avg} | "
                  f"club_strength {d['club_strength']:+.2f} | "
                  f"신인OVR에 얹힌 cs보정 {cs_bonus_avg}")

    return retired


# ─────────────────────────────────────────────
# 4. 이적 시장 (활발하게)
# ─────────────────────────────────────────────
# [2026-08 신설, 15-7-3, 신민용+GPT 검토: "국제 이동(5%) 확률이 출신국
# 등급/선수 OVR과 무관하게 완전히 균일하다 — D급 리그 평균OVR58인데
# 72면 엄청난 아웃라이어인데, S급 평균88에 92는 그렇게 특별하지 않다.
# 그러니 'OVR 절대값'이 아니라 '자기 시장 대비 상대적 위치'로 유출
# 확률을 올려야 한다"] 국제 이동 분기 확률(기본 5%)에 곱하는 승수.
# 두 요인을 곱한다:
#   1) outlier_mult — 이 팀 스쿼드 최고 OVR이 팀 평균보다 얼마나 튀는가.
#      (국가 전체 평균 대신 소속팀 평균을 쓴다 — team_avg가 이미 캐싱돼
#      있어 추가 집계 없이 재사용 가능하고, 약체 리그일수록 팀 평균 자체가
#      국가 평균에 가깝다.)
#   2) market_mult — 그 팀이 속한 국가등급이 얼마나 약한가(LEAGUE_GRADE_RANK
#      1=F~8=SS, 낮을수록 약함). SS/S는 사실상 보정 없음(신민용: "거긴
#      유출이 아니라 선수의 선택 문제") — F급에 가까울수록 배율이 커진다.
# 결과는 0.05(원래 고정값)에 곱해질 배수이고, 최종 국제이동 비중은
# min(0.35, 0.05*승수)로 캡을 씌워 폭주를 막는다(아래 호출부 참고).
# 아웃라이어가 없고(gap<=0) 등급도 SS/S면 승수는 정확히 1.0 — 즉 기존
# 균일 5% 동작과 100% 동일하게 유지된다(회귀 없음 보장).
_OUTLIER_GAP_DIVISOR = 10.0
_OUTLIER_COMPONENT_CAP = 3.0
_MARKET_RANK_STEP = 0.15
# [2026-09 최적화] _outlier_intl_multiplier 메모 — 인자 세 개(스칼라)만으로
# 값이 완전히 결정되는 순수 함수라 프로세스 수명 캐시가 안전하다.
_OUTLIER_MULT_CACHE: dict = {}


def _outlier_intl_multiplier(best_ovr, team_avg_ovr, grade_rank) -> float:
    """[2026-08 재조정] 최초 버전(outlier_mult × market_mult을 각각
    독립적으로 곱함)은 gap=0(아웃라이어 없음)이어도 약체 등급이면 기본
    5%가 최대 9%대까지 올라가는 부작용이 있었다(신민용 원칙 위반: "약체
    등급이라고 평범한 선수까지 유출 확률이 오르면 안 된다 — 아웃라이어일
    때만"). market 보정을 outlier_component에 곱하는 형태로 바꿔서,
    gap=0이면 등급과 무관하게 정확히 1.0(=5% 그대로)이 나오게 한다 —
    "시장이 약할수록 아웃라이어가 더 잘 빠져나간다"이지 "약체 시장 평균
    선수도 잘 빠져나간다"가 아니기 때문."""
    # [2026-09 최적화] 이적시장 한 번에 1,520,447회 호출된다(cProfile 실측:
    # 누적 1.43s — mover 후보 한 명당 1회씩 도는 자리). 실제로 등장하는
    # (OVR, 팀평균, 등급rank) 조합 수는 팀 수 × 로스터 OVR 종류 수준이라
    # 캐시가 아주 잘 듣는다. 계산식·부동소수 연산 순서를 전혀 안 바꾸므로
    # 반환값은 비트 단위로 동일하다.
    _k = (best_ovr, team_avg_ovr, grade_rank)
    _v = _OUTLIER_MULT_CACHE.get(_k)
    if _v is not None:
        return _v
    gap = max(0.0, (best_ovr or 0) - (team_avg_ovr or 0))
    outlier_component = min(_OUTLIER_COMPONENT_CAP, gap / _OUTLIER_GAP_DIVISOR)
    rank = grade_rank if grade_rank is not None else 8   # 등급 정보 없으면 보정 없음(SS 취급)
    market_scale = 1.0 + max(0, 8 - rank) * _MARKET_RANK_STEP
    _v = 1.0 + outlier_component * market_scale
    _OUTLIER_MULT_CACHE[_k] = _v
    return _v


# [2026-09 버그수정, 신민용 리포트: "나라별로 리그 OVR 상한이 다르게
# 설계돼 있는데(constants.COUNTRY_LEAGUE_OVR_OVERRIDE — 한국 1부는
# 58~74로 재조정돼 있는데 일반 B등급 기본표는 72~82) 이적 로직이 이
# 개별 상한을 전혀 몰라서 한국보다 기준이 높은 같은 B등급 나라(폴란드
# 등) 선수가 그대로 한국 리그로 이적해 들어올 수 있다(OVR82가 상한74인
# 한국에 들어오는 식)"] 위 _outlier_intl_multiplier가 "src 팀 평균보다
# 압도적으로 튀는 선수는 더 잘 빠져나가게"(유출 쪽) 보정이라면, 이건
# 정반대 방향("dst 나라의 진짜 설계 상한을 초과하는 선수는 그 나라로
# 잘 안 들어가게", 유입 쪽) 문제라 별도 페널티로 다룬다.
#
# [2026-09 1차 시도, 헤드리스 검증에서 자체 발견한 실패] 처음엔 초과분에
# 비례해 가중치를 곱으로 깎는 소프트 감쇠(_dst_ceiling_penalty)만
# 추가했는데, 실측(같은 seed로 페널티 有/無 비교)해보니 결과가 거의
# 안 바뀌었다 — 원인은 목적지 가중치에 이미 있던 _size_weight(스쿼드
# 인원이 목표(_SQUAD_TARGET)보다 하나만 모자라도 exp(1/0.15)≈785배!)가
# 압도적으로 커서, 아무리 강하게 곱셈 감쇠를 걸어도(예: 초과분 18일 때
# 0.0045배) 최종 가중치가 여전히 다른 정상 후보보다 커지는 경우가
# 실제로 나왔다("스쿼드가 급해서" 신호가 "이 나라엔 과분한 선수다"
# 신호를 통째로 집어삼킴). 소프트 감쇠만으론 절대 이길 수 없는 구조라,
# 초과분이 일정선(HARD_EXCLUDE)을 넘으면 가중치를 깎는 게 아니라 아예
# 후보 풀에서 제외한다 — size_weight가 아무리 커도 애초에 후보 리스트에
# 없으면 뽑힐 수 없다. 초과분이 그 선 안쪽(0~10)일 때는 기존처럼
# 소프트 감쇠(_dst_ceiling_penalty)를 같이 적용해 완만하게 처리한다.
# [2026-09 재조정, 신민용 리포트: "97 OVR 선수가 J리그로 이적하거나 82
# OVR 선수가 K리그에서 뛰는 경우가 실제로 나온다 — 이런 건 아예 안
# 되게 해야 한다"] 초과분 10까지 허용 + 소프트 감쇠만으로는 실제로 걸러지지
# 않았다(예: 일본 구설계 상한91에 OVR95가 초과 4로 무사 통과, 한국
# 상한74에 OVR82가 초과 8로 무사 통과 — 둘 다 10 미만이라 하드 제외에
# 안 걸림). 하드 컷을 10→3으로 크게 좁히고, 그 안쪽 소프트 감쇠도
# 60→20으로 더 가파르게 깎아서 아주 근소한 초과(1~2)만 약한 페널티로
# 통과시키고 그 이상은 사실상 후보에서 제외되게 한다.
_DST_CEIL_EXCESS_DENOM = 20.0
_DST_CEIL_HARD_EXCLUDE = 3.0   # 이 초과분을 넘으면 가중치와 무관하게 후보에서 제외


def _dst_ceiling_penalty(mover_ovr, ceiling) -> float:
    if ceiling is None:
        return 1.0
    excess = mover_ovr - ceiling
    if excess <= 0:
        return 1.0
    return math.exp(-(excess * excess) / _DST_CEIL_EXCESS_DENOM)


def _dst_ceiling_excluded(mover_ovr, ceiling) -> bool:
    """초과분이 _DST_CEIL_HARD_EXCLUDE를 넘으면 True — 위 주석 참고,
    이 경우 소프트 감쇠(_dst_ceiling_penalty)만으론 _size_weight 같은
    다른 큰 배수를 못 이기므로 호출부가 이 후보를 아예 제외해야 한다."""
    if ceiling is None:
        return False
    return (mover_ovr - ceiling) > _DST_CEIL_HARD_EXCLUDE


def _transfer_market(c, year, ai_rows=None, verbose_log=None, my_team_id=None,
                      volume_scale=1.0, is_mid_season=False):
    """선수들이 팀 간 이동. 같은 리그 내 + 국내 다른 tier + 국제 이동.
    [최적화] ORDER BY RANDOM() 제거 → 팀별 선수 목록 선조회 후 Python shuffle.
    이적마다 DB 왕복 2회(RANDOM 쿼리) → 0회로 감소.
    ai_rows: _retire_and_replace와 공유하는 ai_players 선조회 결과
      (id,team_id,position,age,name,ovr,contract_end_year,last_transfer_year)
      — None이면 기존처럼 직접 조회.

    [2026-07 v2 신설] year 파라미터 추가 — 계약 잔여기간(길수록 이적
    확률↓) 반영과 "방금 이적한 선수는 최소 1시즌은 유지"를 위해 필요.

    [2026-07 v3 신설, 신민용+GPT 검토: "K리그는 계속 K리그 안에서만 돈다 —
    승강 시스템이랑 이적시장이 따로 논다 + 10년 지나도 세계가 닫혀있는
    느낌"] 이적 종류를 3가지로 분리한다: 87% 같은 리그(기존), 8% 국내
    다른 tier(승강 인접), 5% 국제 이동(동일 등급 ±1등급, tier1끼리만 —
    하위 tier의 "등급"은 안 매겨져 있어서 국제 이동은 tier1로 한정한다).
    스타 선수 보호·계약 반영·최소 잔류기간은 이 확장된 후보군에도 그대로
    적용된다(mover 선택 로직은 공통이고 destination 후보군만 넓어지는
    구조라 자연스럽게 유지됨).

    [2026-07 v3 신설] verbose_log — 표시용 이적료(저장 없음). 이번 호출에서
    일어난 이적 중 (OVR85 이상 또는 이적료 최고액) 조건을 만족하는 1건만
    골라 로그에 남긴다. 자금 이동은 없음 — 순수 서사/기록용.

    [2026-08 신설, 신민용 요청: "우리팀에 누가 나가고 누가 들어왔는지
    로그에 표시해달라"] my_team_id를 넘기면, 그 팀이 관여한 모든 이적
    (방출/영입)을 별도로 verbose_log에 전부 남긴다(위 "주요 이적" 1건
    필터와 무관하게 우리 팀 건은 전부). 선수 이름은 실명 대신
    constants.ai_player_code()가 만드는 "AI"+4자 코드(예: "AI73QU")를
    쓴다 — ui/formation_widget.py의 포메이션 화면과 완전히 동일한 규칙
    (ai_players.id 기반, 세이브 전체 기간 동안 절대 안 바뀜)이라 화면마다
    표기가 달라지는 일이 없다.

    [2026-08 신설, 상반기/하반기 이적 기록 분리 기능] volume_scale/
    is_mid_season — 신민용 요청("시즌 도중에도 AI 선수들 이적이 가능하긴
    하나 이때는 0~2명 정도만")으로 하반기 시작 직전(겨울 이적시장)에도
    이 함수를 한 번 더 부르기 위해 추가. 기존 오프시즌 호출(연 1회,
    팀당 1~2건 규모)은 volume_scale=1.0(기본값)으로 그대로 두고,
    시즌 도중 호출만 volume_scale을 작게 줘서(예: 0.15) 이적 건수를
    리그 전체 기준 확 줄인다 — n_transfers 계산식에 그대로 곱해지므로
    로직 변경 없이 규모만 조절된다. is_mid_season은 ai_transfer_log에
    그대로 저장돼, "선수 검색"이 그 해 기록을 상반기/하반기로 쪼갤지
    판단하는 근거가 된다.
    """
    moved = 0

    from constants import (get_country_league_grade, get_ovr_range,
                           AI_LOAN_PROBABILITY_YOUNG,
                           AI_LOAN_PROBABILITY_OLD, AI_LOAN_DURATION_YEARS)
    from economy import LEAGUE_GRADE_RANK, estimate_transfer_fee
    # [2026-08 신설, 신민용 리포트: "중간 이적한 해 상반기 팀에 역할이
    # 안 뜬다/떠도 하반기 팀이랑 똑같이 뜬다"] 아래 mover 처리 루프에서
    # is_mid_season일 때만 이적 나가기 직전 역할을 계산하는 데 쓴다 —
    # 루프 안에서 매번 import하지 않도록 함수 시작에서 한 번만 가져온다.
    from formation_logic import compute_squad_roles

    # [2026-08 계측 추가, 신민용 리포트: "이적시장 0.92s가 어디서 쓰이는지
    # 쪼개보자"] 아직 로직은 그대로 두고 구간별 시간만 찍는다 —
    # (1) teams 조회(상관 서브쿼리 AVG(ovr) 포함, 팀마다 1회 실행되므로
    #     팀 수가 많을수록 이 구간이 의심됨) (2) 그룹핑 dict 구성
    # (3) team_players dict 구성 (4) 실제 이적 루프(667개 리그 × 팀당
    #     1~2건, _do_one_transfer_cached 반복 호출 — 가장 유력한 후보)
    # (5) executemany UPDATE.
    import time as _time_tm
    _tm0 = _time_tm.perf_counter()

    # [2026-08 버그수정, 재현성 문제 추적 중 발견] ORDER BY 없이 조회하면
    # by_league/by_country_tier/tier1_by_grade 등 이 함수 전체가 쓰는
    # 팀 후보 리스트들의 순서가 실행마다 달라질 수 있고, 그 순서가
    # random.choice() 등이 뽑는 인덱스에 그대로 영향을 줘서 동일 seed로도
    # 이적 결과가 실행마다 달라지는 원인이 됐다(RNG 소비량 계측으로 확인:
    # 이 함수 진입 전까지는 완전히 동일했는데 완료 후 소비량이 갈렸음).
    teams = [dict(r) for r in c.execute(
        """SELECT t.id AS tid, t.league_id AS lid, t.current_tier AS tier,
                  t.name AS tname, cn.id AS cid, cn.name AS cname,
                  t.momentum_type AS momentum_type, t.momentum_seasons_left AS momentum_seasons_left,
                  (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) AS avg_ovr
           FROM teams t
           JOIN leagues l ON t.league_id = l.id
           JOIN countries cn ON l.country_id = cn.id
           ORDER BY t.id""").fetchall()]
    team_avg = {t["tid"]: (t["avg_ovr"] or 50) for t in teams}
    # [2026-09 신설, "중위권 정체 탈출" momentum] 이 momentum이 활성 상태인
    # 팀은 방출 쪽(_team_category)에서 "낮은 OVR 선수 정리 우선순위 ↑"를
    # 담당한다 — 아래 _team_category에서 참조.
    _stagnant_tids = {t["tid"] for t in teams
                       if (t["momentum_seasons_left"] or 0) > 0
                       and (t["momentum_type"] or "").startswith("mid_table_stagnation")}
    # [2026-08 신설, 신민용 리포트: "38~39세 OVR84~86짜리가 바르셀로나로
    # 이적하고, 유럽 5대 리그가 왜 저런 퇴물급을 영입하냐"] 목적지 선택이
    # 순수 OVR 격차·스쿼드 크기만 보고 나이는 전혀 안 봤던 게 원인 —
    # SS/S(최상위 5대 리그급) 목적지에 한해 나이 기반 페널티를 추가로
    # 곱하기 위해 팀별 등급을 미리 조회해둔다(아래 _do_one_transfer_cached
    # 참고).
    dst_grade_by_tid = {t["tid"]: get_country_league_grade(t["cname"]) for t in teams}
    # [2026-09 신설, 신민용 요청: "나이든 선수가 사우디/미국/A급 유럽 리그로
    # 가는 것처럼, 자기 조국 리그로 귀환하는 것도 약하게 선호해야 한다 —
    # 너무 세게 주면 안 됨"] 목적지 팀의 국가명을 미리 캐싱 — 아래
    # _do_one_transfer_cached가 mover 국적과 비교해 약한 가산 가중치를
    # 준다(다른 나라와 완전히 배제하는 게 아니라 살짝 더 뽑히기 쉬운 정도).
    dst_country_by_tid = {t["tid"]: t["cname"] for t in teams}
    # [2026-09 버그수정, 신민용 리포트: "OVR82가 설계상한74인 한국으로
    # 이적해 들어온다"] 팀별 "그 나라(오버라이드 포함) 설계 OVR 상한"을
    # 미리 한 번만 조회해 캐싱 — get_ovr_range가 COUNTRY_LEAGUE_OVR_
    # OVERRIDE까지 이미 반영한 정확한 상한을 주므로, 문자등급(dst_grade_
    # by_tid)만으로는 못 잡는 "같은 등급이라도 나라별로 실제 상한이 다른"
    # 경우를 이걸로 구분한다. 아래 _do_one_transfer_cached 목적지 가중치
    # 계산(_dst_ceiling_penalty)에 쓰인다 — 국제 이동뿐 아니라 모든 이적
    # 분기(같은 리그/국내 다른 tier)에 동일하게 넘겨서, 어떤 dst_pool_tids
    # 리스트 객체로 호출되든 캐싱된 pool 메타(_pool_meta_cache)가 항상
    # 같은 상한표를 참조하도록 통일한다(분기별로 다르게 넘기면 국제 풀이
    # 후보 부족으로 같은 리그 리스트로 폴백하는 드문 경우, 그 리스트가
    # 다른 분기 호출에서 이미 다른 상한 설정으로 캐싱돼 있어 값이 꼬일
    # 위험이 있다).
    # [2026-09 방어] get_ovr_range는 그 등급 표에 해당 tier가 아예 없으면
    # (오버라이드 없는 나라의 깊은 tier — A/B는 4부까지, C~F는 3~4부까지만
    # 정의돼 있음, 위 _age_and_progress의 team_cap 조회와 동일 케이스)
    # None을 반환한다 — 이미 이 코드베이스의 같은 상황(team_cap 조회,
    # 약 772번째 줄)에서 쓰는 것과 동일한 폴백(43, 최하위 깊은 tier 근사치)을
    # 그대로 재사용해 일관성을 맞춘다.
    dst_ovr_ceiling_by_tid = {}
    for t in teams:
        _rng = get_ovr_range(dst_grade_by_tid[t["tid"]], t["tier"], t["cname"])
        dst_ovr_ceiling_by_tid[t["tid"]] = _rng[1] if _rng else 43
    # [2026-08 신설, 신민용 리포트: "OVR81따리가 레알 마드리드나 바르셀로나에
    # 있을 수 있냐"] 목적지 가우시안 가중치(아래 _do_one_transfer_cached)가
    # SS/S 등급 전체에 동일한 폭을 쓰다 보니, 등급은 SS/S여도 진짜 명문
    # (레알/바르사급)이 아닌 팀과 똑같은 관용폭을 진짜 명문팀에도 줘버렸다.
    # 진짜 명문(prestige_level>=2)은 훨씬 좁은 격차만 허용하도록 목적지별
    # 명문등급도 같이 미리 조회해둔다.
    from data.prestige_clubs import prestige_level as _tm_prestige_level
    dst_prestige_by_tid = {t["tid"]: _tm_prestige_level(t["cname"], t["tname"]) for t in teams}
    # [2026-08 신설, 이적 로그용] 팀마다 prestige_level을 한 번만 계산해
    # 캐싱 — 이적마다 다시 계산하면 수천 건 반복이라 성능에 영향을 준다.
    from data.prestige_clubs import prestige_level as _prestige_level_fn
    team_prestige = {t["tid"]: (_prestige_level_fn(t["cname"], t["tname"]) or 0) for t in teams}
    # [2026-09 최적화, 이적시장 2차] 아래 _salary_cache의 키를 좁히기 위한
    # 사전 계산. economy._calc_salary는 team_name을 오직
    # prestige_salary_mult(country, team_name) 한 곳에서만 쓰고(그 외에는
    # grade/tier/ovr/country/year만 본다. _calc_ai_salary가 team_id를 일부러
    # 안 넘기므로 club_strength 보정도 항상 1.0), 그 안쪽 분기도
    # `if team_name and country:`라 여기서 그 조건까지 그대로 재현한다.
    # 즉 "같은 (등급, tier, 국가, 이 배율, OVR)"이면 연봉이 비트 단위로
    # 같으므로, 팀ID 대신 이 배율을 키에 넣으면 같은 리그의 2,600여 팀이
    # 한 칸으로 합쳐진다(배율 1.0이 대다수 — 명문팀만 값이 갈린다).
    from data.prestige_clubs import prestige_salary_mult as _prestige_salary_mult_fn
    sal_pmult_by_tid = {t["tid"]: (_prestige_salary_mult_fn(t["cname"], t["tname"])
                                    if (t["tname"] and t["cname"]) else None)
                        for t in teams}
    # [2026-08 최적화] verbose_log용 _estimate_ai_transfer_fee_display가
    # 이적마다 teams 리스트를 선형탐색(최대 2회) + 팀명 SQL SELECT 2회를
    # 추가로 날리고 있었다 — 여기서 tid→row 딕셔너리를 한 번만 만들어
    # 재사용하면 그 함수 안의 왕복이 전부 O(1) 조회로 바뀐다.
    team_row_by_tid = {t["tid"]: t for t in teams}
    _tm1 = _time_tm.perf_counter()

    # 리그별 팀 그룹 (기존, 87%용)
    by_league: dict = {}
    # 국내 다른 tier 그룹 (국가+tier 기준, 8%용)
    by_country_tier: dict = {}
    # tier1 등급별 그룹 (국제 이동, 5%용) — 등급 없는 나라는 제외
    tier1_by_grade: dict = {}
    team_tier = {}
    team_grade_rank = {}
    # [2026-08 최적화, 신민용 리포트: "이적루프 0.6~0.9s 원인 찾자"] 아래
    # 이적 루프 안에서 "국내 다른 tier" 후보군(8%)을 고를 때 src 팀의
    # cid(국가ID)가 필요한데, 예전엔 이걸 캐싱 안 하고 매번
    # `next(t["cid"] for t in teams if t["tid"]==src)`로 teams 리스트
    # 전체(전 세계 모든 리그, 수천 팀)를 선형탐색했다 — team_tier/
    # team_grade_rank는 이미 딕셔너리로 캐싱해뒀으면서 이것만 빠져있었다.
    # 이적 시도가 667개 리그에 걸쳐 수천~1만 건 발생하고 그중 8%가 이
    # 탐색을 타므로, "시도 수천 회 × teams 크기 수천"의 불필요한 반복이
    # 누적된 것으로 보인다 — 순수 O(1) 캐싱이라 결과는 완전히 동일하다.
    team_to_cid = {}
    team_lid = {}
    for t in teams:
        by_league.setdefault(t["lid"], []).append(t["tid"])
        by_country_tier.setdefault((t["cid"], t["tier"]), []).append(t["tid"])
        team_tier[t["tid"]] = t["tier"]
        team_to_cid[t["tid"]] = t["cid"]
        team_lid[t["tid"]] = t["lid"]
        if t["tier"] == 1:
            # [2026-08 grade resolution 단일화] 예전엔 COUNTRY_LEAGUE_GRADE에
            # 명시 등록 안 된 나라는 grade=None이라 "등급 없는 나라는 제외"
            # 방침으로 이 국제이동 풀(tier1_by_grade)에서 조용히 빠졌다.
            # get_country_league_grade()는 항상 유효한 등급(최소 국대 등급
            # fallback)을 반환하므로 더 이상 제외되는 나라가 없다 —
            # 등록 안 된 나라의 tier1 팀도 국제 이동 후보군에 정상 포함된다.
            grade = get_country_league_grade(t["cname"])
            rank = LEAGUE_GRADE_RANK.get(grade, 4)
            team_grade_rank[t["tid"]] = rank
            tier1_by_grade.setdefault(rank, []).append(t["tid"])
    _tm2 = _time_tm.perf_counter()

    # [2026-08 신설, 신민용 요청: "SS에서 뛰던 선수도 A로 바로 갈 수
    # 있고, 사우디·미국 1부 위주로 가는 그림을 만들어달라 — 현실에서도
    # 손흥민이 토트넘에서 미국으로 갔다"] 사우디아라비아·미국 tier1을
    # "은퇴 무대" 후보 풀로 별도 모아둔다 — 아래 국제이동 로직에서 나이
    # 든(노쇠화된) 선수가 최상위 리그를 떠날 때 이 풀을 우선적으로
    # 고려하게 한다(_do_one_transfer_cached에 전달).
    _VETERAN_DEST_COUNTRIES = {"사우디아라비아", "미국"}
    veteran_pool_tids = [t["tid"] for t in teams
                          if t["tier"] == 1 and t["cname"] in _VETERAN_DEST_COUNTRIES]

    # [2026-08 전면 재설계, 신민용 요청: "이적도 좀 더 현실적으로 —
    # 감독 성향/팀 성적에 따라 강팀은 소폭 보강, 중위권은 활발, 하위권은
    # 회전율 매우 높게, 강등팀은 대방출, 승격팀은 대보강"] 팀을 카테고리
    # (strong/mid/weak/promoted/relegated)로 분류해서, 카테고리별로 지정된
    # 범위 안에서 이번 시즌 "방출 인원 목표치"를 뽑는다 — 예전엔 리그
    # 전체 기준으로 팀 수×1~2배만큼만 총량을 굴리고 어떤 팀이 몇 명을
    # 내보낼지는 순전히 스쿼드 크기 가중치로 결정했는데, 이제 팀 성적/
    # 승강 상황이 직접 방출 규모를 결정한다.
    #
    # 승격/강등 판정: promotion_log(team_name 매칭이라 동명이팀 충돌
    # 위험이 있음)에 기대지 않고, "이번 시즌 실제로 뛴 리그"(match_results.
    # league_id, 승강 반영 전)와 "지금 teams.league_id"(승강 반영 후)를
    # 직접 비교한다 — 다르면 승강이 일어난 것이고, tier가 낮아졌으면
    # 승격/높아졌으면 강등이다. team_id 기준이라 이름 충돌 걱정이 없다.
    league_tier_by_id = {t["lid"]: t["tier"] for t in teams}
    # [2026-08 견고화] 방금 끝난 시즌의 원본 경기 데이터는 보통 아직
    # match_results에 남아있지만(archive_old_seasons가 이 함수보다
    # 나중에 실행됨 — game_engine.py의 호출 순서 참고), 혹시 이미
    # 지나간 시즌(예: 재시뮬레이션·디버그 목적의 단독 호출)을 대상으로
    # 부르는 경우까지 대비해 match_results_archive도 함께 조회한다
    # (get_team_history와 동일한 원칙).
    _std_rows = c.execute(
        "SELECT home_team_id, away_team_id, home_score, away_score, league_id "
        "FROM match_results WHERE year=? AND home_score>=0 "
        "UNION ALL "
        "SELECT home_team_id, away_team_id, home_score, away_score, league_id "
        "FROM match_results_archive WHERE year=? AND home_score>=0", (year, year)).fetchall()
    _wdl: dict = {}
    played_league_by_team: dict = {}
    for r in _std_rows:
        h, a, hs, as_, lid = r["home_team_id"], r["away_team_id"], r["home_score"], r["away_score"], r["league_id"]
        for tid in (h, a):
            _wdl.setdefault(tid, [0, 0, 0, 0, 0])
            played_league_by_team[tid] = lid
        if hs > as_:
            _wdl[h][0] += 1; _wdl[a][2] += 1
        elif hs < as_:
            _wdl[a][0] += 1; _wdl[h][2] += 1
        else:
            _wdl[h][1] += 1; _wdl[a][1] += 1
        _wdl[h][3] += hs; _wdl[h][4] += as_
        _wdl[a][3] += as_; _wdl[a][4] += hs

    rank_pct_by_team: dict = {}
    _by_played_league: dict = {}
    for tid, lid in played_league_by_team.items():
        _by_played_league.setdefault(lid, []).append(tid)
    for lid, tids_l in _by_played_league.items():
        ranked = sorted(tids_l, key=lambda t: (-(_wdl[t][0] * 3 + _wdl[t][1]),
                                                -(_wdl[t][3] - _wdl[t][4])))
        n = len(ranked)
        for i, tid in enumerate(ranked):
            rank_pct_by_team[tid] = (i + 1) / n

    promoted_ids: set = set()
    relegated_ids: set = set()
    for tid, played_lid in played_league_by_team.items():
        cur_lid = team_lid.get(tid)
        if cur_lid is None or played_lid == cur_lid:
            continue
        played_tier = league_tier_by_id.get(played_lid)
        cur_tier = team_tier.get(tid)
        if played_tier is None or cur_tier is None:
            continue
        if cur_tier < played_tier:
            promoted_ids.add(tid)
        elif cur_tier > played_tier:
            relegated_ids.add(tid)

    # (영입 하한, 영입 상한, 방출 하한, 방출 상한) — 신민용이 제시한
    # 실측 기반 구간을 그대로 적용. 영입 수는 이 함수에서 직접 강제하지
    # 않는다(목적지 선택은 기존처럼 OVR 적합도 가중 로직이 자연스럽게
    # 분산시키고, 승격팀처럼 원래도 매력적인 목적지는 자연히 더 많이
    # 받는다 — 방출 쪽만 카테고리별로 강제하면 영입 쪽은 시장 원리로
    # 따라온다). 방출 하한/상한만 실제로 쓰인다.
    _TRANSFER_QUOTA = {
        "strong":    (2, 4, 2, 4),
        "mid":       (4, 7, 5, 8),
        "weak":      (6, 10, 6, 10),
        "relegated": (5, 10, 8, 15),
        "promoted":  (8, 12, 5, 8),
    }

    # [2026-08 신설, 신민용 요청: "무작위로 바꾸지 말고 핵심 선수는
    # 상황에 따라 다르게 가야 하지 않냐"] 팀 카테고리별로 "에이스를
    # 얼마나 지키는지" 강도를 다르게 준다. 강팀은 스쿼드 뼈대를 안
    # 흔든다(높은 보호 → 에이스가 팔릴 확률 낮음), 반대로 약팀/강등팀은
    # "고주급자 스타들도 팀을 떠나려 한다"(7번 스펙 그대로) — 보호를
    # 크게 낮춰 핵심 자원도 실제로 현금화 대상이 되게 한다. mid는 기존
    # 고정값(0.85)을 그대로 유지 — 이번 변경 전과 동일하게 작동.
    _STAR_PROTECT_BY_CATEGORY = {
        "strong": 0.92, "mid": 0.85, "weak": 0.55,
        "relegated": 0.35, "promoted": 0.80,
    }

    def _team_category(tid):
        if tid in relegated_ids:
            return "relegated"
        if tid in promoted_ids:
            return "promoted"
        pct = rank_pct_by_team.get(tid)
        if pct is None:
            cat = "mid"
        elif pct <= 0.25:
            cat = "strong"
        elif pct >= 0.75:
            cat = "weak"
        else:
            cat = "mid"
        # [2026-09 신설, "중위권 정체 탈출" momentum, 신민용 확정: "낮은
        # OVR 선수 정리 우선순위 ↑"] 순위만 보면 "mid"(4~7위 정도)로 분류될
        # 명문팀이라도, 이 momentum이 활성 상태면 "weak"과 같은 강도로
        # 방출한다 — 실제 순위를 건드리지 않고 스쿼드 회전만 가속하는
        # 방식(신민용 요청: 기존 카테고리 체계를 재활용, 새 등급을 안 만듦).
        # 이미 "weak"/"relegated"인 팀은 그대로 둔다(더 강하게 만들 필요
        # 없음 — 이미 그 카테고리의 공격적인 방출 폭을 쓰고 있음).
        if cat == "mid" and tid in _stagnant_tids:
            return "weak"
        return cat

    # [최적화] 팀별 선수 목록을 _retire_and_replace와 공유된 스냅샷에서 재사용
    all_players_rows = ai_rows if ai_rows is not None else c.execute(
        "SELECT id, team_id, position, age, name, ovr, contract_end_year, last_transfer_year "
        "FROM ai_players").fetchall()
    team_players: dict = {}
    # [2026-08 최적화] 예전엔 행마다 `"name" in r.keys()` 식으로 컬럼 존재
    # 여부를 매번 확인했다. sqlite3.Row.keys()는 호출할 때마다 컬럼 이름
    # 리스트를 새로 만들어 돌려주는 메서드라, 26만 행 × 컬럼 4개 =
    # 108만 회나 리스트를 만들고 버리고 있었다(cProfile 실측). 한 결과셋
    # 안에서는 컬럼 구성이 절대 바뀌지 않으므로 첫 행에서 딱 한 번만
    # 확인하고 그 결과를 재사용한다 — 판정 결과·기본값 처리는 동일.
    if all_players_rows:
        _cols = set(all_players_rows[0].keys())
        _has_name = "name" in _cols
        _has_age = "age" in _cols
        _has_cend = "contract_end_year" in _cols
        _has_lty = "last_transfer_year" in _cols
        _has_nat = "nationality" in _cols
        for r in all_players_rows:
            _age = (r["age"] if _has_age else None) or 25
            _ovr = r["ovr"]
            team_players.setdefault(r["team_id"], []).append({
                "id": r["id"], "position": r["position"],
                "name": r["name"] if _has_name else "",
                "age": _age,
                "ovr": _ovr if _ovr is not None else 50,
                "contract_end_year": r["contract_end_year"] if _has_cend else 0,
                "last_transfer_year": r["last_transfer_year"] if _has_lty else 0,
                # [2026-09 신설] 조국 귀환 가산 가중치용 — 아래
                # _do_one_transfer_cached에서 mover["nationality"]로 참조.
                "nationality": r["nationality"] if _has_nat else "",
            })
    # [2026-08 2차 최적화] 팀별 "인원 가중치" 표를 미리 만들어둔다.
    # size_w = exp(-(인원 - _SQUAD_TARGET)/0.15)는 인원(정수)만의 함수라,
    # 예전처럼 후보를 평가할 때마다(시즌당 267만 회) len()으로 세고 exp를
    # 부르는 대신 여기서 팀당 한 번만 계산해두고 이적으로 인원이 실제로
    # 바뀔 때만(이적 1건당 2팀) 갱신하면 된다. 값 자체는 예전 식 그대로다.
    _sw_by_tid = {tid: _size_weight(len(plist)) for tid, plist in team_players.items()}
    _tm3 = _time_tm.perf_counter()

    # 이적 결과 누적 후 executemany
    # [2026-07 v2] 이적 시 새 계약(2~4년)과 이적연도를 같이 기록한다 —
    # (new_team_id, new_contract_end_year, last_transfer_year, player_id)
    transfer_updates = []
    # [2026-09 신설, 신민용 요청: "이적이면 연봉이 써지는거고, 이적 종류
    # (이적/임대)도 구분해야 한다"] transfer_updates(팀/계약/최근이적연도
    # — 위 안전장치, p_entry 조회 실패해도 항상 실행됨)와 별개로, p_entry를
    # 확실히 아는 경우에만 연봉/임대여부를 채운다.
    salary_loan_updates = []   # (salary, on_loan_from_team_id, loan_return_year, player_id)
    # [2026-08 신설, 신민용 요청: "주요 이적도 스페인/프랑스/독일/이탈리아/
    # 잉글랜드 각각 1명씩, 이름도 표시해서 각각 가장 비싼 이적료들을
    # 보여달라"] 예전엔 전세계 통틀어 딱 1건(_big_transfer)만 추적했는데,
    # 목적지 리그 국가별로 최고액 1건씩(5개국) 따로 추적하도록 확장.
    _MAJOR_TRANSFER_COUNTRIES = ("스페인", "프랑스", "독일", "이탈리아", "잉글랜드")
    _big_transfer_by_country: dict = {}   # {country_name: (fee, ovr, src_name, dst_name, player_id)}

    # [2026-08 신설, "명문팀 lifecycle 조사" 요청] AI 이적 로그 배치 —
    # season은 이 함수 호출당 한 번만 조회(이적 건마다 조회하면 수천 건
    # 반복이라 성능에 영향).
    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 0
    transfer_log_rows = []
    my_team_events = []   # [2026-08 신설] (방향, p_entry, old_tid, new_tid) — 우리 팀 관여 이적만

    # [2026-08 최적화] 이적 루프 전용 캐시 2종(이 호출 안에서만 살아있음).
    #  _intl_pool_by_rank: 국제 이동(5%) 후보군을 등급 rank별로 1회만 조립.
    #  _pool_meta_cache : 후보 풀 리스트별 (팀평균OVR / sigma분모 / SS·S여부)
    #                     배열. team_avg·dst_prestige_by_tid·dst_grade_by_tid는
    #                     이 루프 내내 불변이라 풀마다 한 번만 만들면 된다.
    # 둘 다 "매번 다시 계산하던 같은 값"을 재사용하는 것뿐이라 결과는 동일.
    _intl_pool_by_rank: dict = {}
    _pool_meta_cache: dict = {}
    # [2026-09 최적화, 신민용 리포트: "52주차→1주차 렉"] 이적 1건이 성사될
    # 때마다 도는 "후처리"(이적료+연봉 산정)가 이적루프 시간의 약 36%를
    # 차지한다 — cProfile 실측으로 economy._calc_salary 181,363회(누적
    # 3.45s), economy.estimate_transfer_fee 75,447회(누적 2.91s).
    #
    # 둘 다 이 경로에서는 완전히 결정론적이다. _calc_ai_salary는 team_id를
    # 일부러 안 넘기므로(2026-09 성능수정 주석 참고) _calc_salary의 입력이
    # (grade, tier, ovr, country, team_name, year)뿐이고, estimate_transfer_fee도
    # 이 호출부에서는 난수를 전혀 쓰지 않는다(economy에서 난수를 쓰는 건
    # my_player 오퍼 전용 offer_premium_mult 하나뿐). grade/tier/country/
    # team_name은 전부 목적지 team_id 하나로 결정되고 year는 이 호출 내내
    # 고정이므로, 실질 키는 (목적지 팀ID, OVR[, 포지션])이다.
    #
    # 캐시 수명은 이 함수 호출 1회 — year가 economy_index(year)를 통해
    # 값에 들어가므로 시즌을 넘겨 재사용하면 안 된다.
    # 실측 히트율: 이적료 34.4% / 연봉 22.9%.
    _fee_cache: dict = {}
    _salary_cache: dict = {}

    for lid, tids in by_league.items():
        if len(tids) < 2:
            continue
        for src in tids:
            cat = _team_category(src)
            _out_lo, _out_hi = _TRANSFER_QUOTA[cat][2], _TRANSFER_QUOTA[cat][3]
            out_quota = random.randint(_out_lo, _out_hi)
            # [2026-08 확장, 상반기/하반기 이적 기록 분리 기능] 시즌 도중
            # 소규모 창구 호출(volume_scale<1.0)도 같은 카테고리 로직을 그대로
            # 쓰되, 목표 인원만 비례해서 줄인다 — "0~2명 정도만"이라는 신민용
            # 요청과 일치(강팀 방출목표 2~4명 × 0.15 ≈ 0명, 약팀 6~10명 × 0.15
            # ≈ 1명 등, 카테고리가 강할수록 시즌 도중 이적도 자연히 더 적다).
            if volume_scale != 1.0:
                out_quota = max(0, int(round(out_quota * volume_scale)))
            # [2026-08 신설, 15-7-3] out_quota 루프 시작 전에 이 팀의 "국제
            # 이동 승수"를 한 번만 계산해둔다(선수 하나하나가 아니라 팀
            # 단위 슬롯 확률이라 매 반복 재계산할 필요가 없음 — cat/
            # out_quota와 동일한 패턴). 팀 스쿼드 최고 OVR을 아웃라이어
            # 신호로 쓴다.
            _src_players_ovrs = [pl["ovr"] for pl in team_players.get(src, []) if pl.get("ovr")]
            _src_best_ovr = max(_src_players_ovrs) if _src_players_ovrs else team_avg.get(src, 50)
            _intl_mult = _outlier_intl_multiplier(
                _src_best_ovr, team_avg.get(src, 50), team_grade_rank.get(src))
            # 국제이동 비중을 5%*_intl_mult로 가변화(최대 35% 캡) — 승수가
            # 정확히 1.0(아웃라이어 없음 + SS/S급)이면 0.87/0.95 그대로라
            # 기존 동작과 100% 동일하다. 국내 다른 tier(8%) 폭은 고정 유지.
            _intl_share = min(0.35, 0.05 * _intl_mult)
            _same_league_upper = 1.0 - 0.08 - _intl_share
            _domestic_other_upper = 1.0 - _intl_share
            for _ in range(out_quota):
                src_tier = team_tier.get(src, 1)
                # 후보군 결정: (같은 리그) / (국내 다른 tier, 8% 고정) /
                # (국제, 기본 5%이나 위 _intl_share로 가변)
                roll = random.random()
                if roll < _same_league_upper or src_tier != 1:
                    dst_pool_tids = tids
                elif roll < _domestic_other_upper:
                    cid = team_to_cid.get(src)
                    cand = by_country_tier.get((cid, 2), []) or by_country_tier.get((cid, src_tier + 1), [])
                    dst_pool_tids = cand if len(cand) >= 1 else tids
                else:
                    rank = team_grade_rank.get(src)
                    if rank is None:
                        dst_pool_tids = tids
                    else:
                        # [2026-08 확장, 신민용 요청: "SS에서 뛰던 선수도
                        # A로 바로 갈 수는 있다"] 예전엔 ±1등급만 후보였는데
                        # (SS→S/SS까지만), 아래로 두 단계(rank-2)까지 넓혀서
                        # 최상위(S/SS) 선수도 그 아래 A급까지 곧장 갈 수
                        # 있게 한다 — 위로는 그대로 +1까지만(상승 이적은
                        # 점진적이어야 자연스러움, 비대칭 유지).
                        # [2026-08 최적화] 이 후보군은 "등급 rank"에만 의존
                        # 하는데(rank-2 ~ rank+1의 tier1 팀 전부, 전세계
                        # 1,200팀 규모), 예전엔 이적 한 건마다 매번 리스트를
                        # 새로 이어붙이고 다시 한 번 필터해서 통째로 복사했다
                        # — 시즌당 이 경로만 3,700회쯤 타므로 440만 회분의
                        # 불필요한 리스트 생성이었다. rank별로 딱 한 번만
                        # 만들어 재사용한다(같은 리스트 객체를 계속 넘기게
                        # 되므로 _do_one_transfer_cached의 풀 메타데이터
                        # 캐시도 그대로 적중한다).
                        # src 제외는 예전엔 여기서 했지만 어차피
                        # _do_one_transfer_cached의 가중치 루프가 t != src를
                        # 한 번 더 거른다 — src는 자기 rank 풀에 반드시
                        # 포함되므로(rank가 (rank-2..rank+1) 범위 안에 있음)
                        # "src를 뺀 뒤 1개 이상"은 "빼기 전 2개 이상"과
                        # 항상 같은 조건이라 판정 결과도 동일하다.
                        cand = _intl_pool_by_rank.get(rank)
                        if cand is None:
                            cand = []
                            for r in (rank - 2, rank - 1, rank, rank + 1):
                                cand.extend(tier1_by_grade.get(r, []))
                            _intl_pool_by_rank[rank] = cand
                        dst_pool_tids = cand if len(cand) >= 2 else tids

                # [2026-08 신설, 신민용 요청: "사우디·미국 1부 위주로 가는
                # 그림"] 국제이동(87%/8% 아닌 위 else 분기)이고 src가
                # 최상위권(S/SS, rank>=7)일 때만 veteran_pool_tids를 같이
                # 넘긴다 — _do_one_transfer_cached가 실제 mover(선수)가
                # 정해진 뒤에 그 선수 나이를 보고, 나이 든 선수면 이 풀을
                # 우선 후보로 쓴다(뒤에서 구현).
                # [2026-08 수정, 15-7-3] 국제이동 분기 상한이 0.95 고정에서
                # _domestic_other_upper(가변)로 바뀌었으므로 이 판정도
                # 그에 맞춰 같이 옮긴다 — 안 옮기면 _intl_share가 커진
                # 팀에서 roll이 0.90~0.95 사이일 때 "국제 이동"인데도
                # veteran_pool 판정에서는 여전히 빠지는 불일치가 생긴다.
                _veteran_pool = (veteran_pool_tids
                                 if (roll >= _domestic_other_upper and src_tier == 1
                                     and (team_grade_rank.get(src) or 0) >= 7)
                                 else None)

                result = _do_one_transfer_cached(
                    src, dst_pool_tids, team_players, team_avg, year,
                    protect_strength=_STAR_PROTECT_BY_CATEGORY[cat],
                    veteran_pool_tids=_veteran_pool,
                    dst_grade_by_tid=dst_grade_by_tid,
                    dst_prestige_by_tid=dst_prestige_by_tid,
                    pool_cache=_pool_meta_cache, sw_by_tid=_sw_by_tid,
                    # [2026-09 신설] mover 선정 가중치의 OVR-아웃라이어
                    # 보정(_outlier_intl_multiplier)에 필요 — 위에서 국제
                    # 이동 비중 계산에 이미 쓰던 것과 같은 값을 그대로 전달.
                    src_grade_rank=team_grade_rank.get(src),
                    # [2026-09 신설] 목적지 나라 설계 OVR 상한 페널티용 —
                    # 위 dst_ovr_ceiling_by_tid 주석 참고, 모든 분기에
                    # 동일하게 전달한다.
                    dst_ovr_ceiling_by_tid=dst_ovr_ceiling_by_tid,
                    # [2026-09 신설] 조국 귀환 가산 가중치용 — 위
                    # dst_country_by_tid 주석 참고. 국내 이적 분기(같은
                    # 나라만 후보)에서는 모든 후보가 동일하게 "일치"라
                    # 상대 가중치에 영향이 없으므로 분기 구분 없이 항상
                    # 넘겨도 안전하다.
                    dst_country_by_tid=dst_country_by_tid)
                if result:
                    for new_tid, pid, old_tid in result:
                        # [2026-09 버그수정, 신민용 리포트: "2005년에 2년
                        # 계약했는데 2007년까지 그대로 뜬다"] 위
                        # _process_contract_renewals와 같은 effective_year
                        # 보정 누락 — 여기(일반 이적시장)는 is_mid_season에
                        # 따라 발효시점이 갈린다(겨울 이적은 그 해 그대로,
                        # 오프시즌 이적은 다음 해부터 — get_ai_player_
                        # salary_history 정의부 주석 참고). 오프시즌일 때만
                        # year+1부터 세야 의도한 기간이 정확히 표시되고,
                        # 재계약 판정(contract_end_year<=year)도 한 해
                        # 일찍 당겨져 의도한 시점에 걸린다.
                        new_contract_end = (year if is_mid_season else year + 1) + random.randint(2, 4)
                        transfer_updates.append((new_tid, new_contract_end, year, pid))
                        # [2026-08 성능 수정, 신민용 리포트: "52주차→1주차 렉"]
                        # 예전엔 이동한 선수를 원 소속팀 리스트에서 지울 때
                        # next()로 한 번 찾고(O(n)), 그다음 리스트 컴프리헨션으로
                        # 그 선수만 뺀 새 리스트를 통째로 다시 만들었다(O(n) 또
                        # 한 번) — 시즌당 이적 2.7만여 건마다 이 이중 O(n)이
                        # 반복되며 _transfer_market 자체 시간의 상당 부분을
                        # 차지하고 있었다(cProfile 실측: tottime 0.48s). 인덱스를
                        # 한 번만 찾아 pop()으로 바로 제거하면 한 번의 스캔으로
                        # 끝나고, 새 리스트를 통째로 재할당하지도 않는다 — 결과는
                        # 동일(같은 선수가 원 소속팀 리스트에서 빠지고 목적지
                        # 팀 리스트에 추가됨). [2026-08 추가 조사] "같은 팀을
                        # src로 다시 뽑았을 때 mover 선정 계산을 캐싱"하는 방안도
                        # 시도해봤으나, 실측 캐시 히트율이 0%였다(이적 시도의
                        # 성공률이 거의 100%에 가까워 캐시가 쌓이기도 전에 거의
                        # 매번 무효화됨) — 이득이 없어 되돌리고 이 pop() 수정만
                        # 남긴다.
                        _old_list = team_players.get(old_tid, [])
                        _idx = next((i for i, e in enumerate(_old_list) if e["id"] == pid), None)
                        # [2026-08 신설, 신민용 리포트: "중간 이적한 해에
                        # 상반기 팀엔 역할(주전/로테이션 등)이 안 뜬다 —
                        # 뜨더라도 하반기 팀이랑 완전히 똑같이 뜨는데,
                        # 실제로는 상반기 팀에서 후보였다"] world_browser.py의
                        # 반기 표시(_half_season_league_entry)는 지금까지
                        # ai_player_position_history.role(연도 하나당 한
                        # 값 — 그 해 "최종/하반기" 소속팀 스냅샷)을 상/하반기
                        # 두 줄에 그대로 같이 썼다 — 상반기(이 시점 old_tid)
                        # 팀 로스터 기준 역할이 따로 없었기 때문. pop() 하기
                        # 직전(선수 본인이 아직 이 로스터에 포함돼 있을 때)
                        # compute_squad_roles로 "나가기 직전 그 팀에서의
                        # 역할"을 계산해 이적 로그에 같이 남긴다 — 오프시즌
                        # (연 1회, 팀당 1~2건이지만 세계 전체로는 수만 건)은
                        # world_browser.py가 애초에 반기 분리 표시를 안 해서
                        # 이 값이 쓰이지도 않으므로, is_mid_season(팀당
                        # 0~2명 규모)일 때만 계산해 비용을 그 작은 물량으로
                        # 가둔다.
                        _dep_role = ""
                        if is_mid_season and _idx is not None:
                            _dep_role = compute_squad_roles(
                                [(e["id"], e.get("ovr"), e.get("age")) for e in _old_list]
                            ).get(pid, "")
                        p_entry = _old_list.pop(_idx) if _idx is not None else None
                        if p_entry is not None:
                            # 인원이 바뀐 팀만 가중치 표를 갱신(위 _sw_by_tid 주석 참고)
                            _sw_by_tid[old_tid] = _size_weight(len(_old_list))
                        if p_entry:
                            # [2026-08 신설, 이적 로그] p_entry는 아직 이적 전 값(포지션/
                            # 나이/OVR)이라 이 시점에 기록해야 정확하다 — 아래에서
                            # contract_end_year/last_transfer_year을 덮어쓰기 직전.
                            _from_lid = team_lid.get(old_tid)
                            _to_lid = team_lid.get(new_tid)
                            if _from_lid == _to_lid:
                                _actual_ttype = "리그내"
                            elif team_to_cid.get(old_tid) == team_to_cid.get(new_tid):
                                _actual_ttype = "국내 타부수"
                            else:
                                _actual_ttype = "국제 이동"
                            # [2026-09 신설, 신민용 요청: "이적 종류(이적/임대)도
                            # 구분하고, 이적이면 연봉이 써지는거고 이적료도 있어야
                            # 한다"] p_entry(포지션/나이/OVR)를 아는 이 시점에서만
                            # 계산 가능 — 어릴수록(23세 이하) 임대로 가는 비율이
                            # 높다(AI_LOAN_PROBABILITY_*, "AI는 단순해야 한다"
                            # 원칙대로 나이 하나만으로 가볍게 가른다).
                            _dst_row2s = team_row_by_tid.get(new_tid)
                            _dst_grade2 = dst_grade_by_tid.get(new_tid, "F")
                            _dst_tier2 = _dst_row2s["tier"] if _dst_row2s else 1
                            _dst_cname2 = _dst_row2s["cname"] if _dst_row2s else ""
                            _dst_tname2 = _dst_row2s["tname"] if _dst_row2s else ""
                            _p_ovr2 = p_entry.get("ovr", 50)
                            _p_age2 = p_entry.get("age", 25)
                            _is_loan = random.random() < (
                                AI_LOAN_PROBABILITY_YOUNG if _p_age2 <= 23 else AI_LOAN_PROBABILITY_OLD)
                            _loan_return2 = year + random.randint(*AI_LOAN_DURATION_YEARS) if _is_loan else 0
                            # (위 _salary_cache/_fee_cache 주석 참고 — 값은 동일,
                            # 같은 (목적지팀, OVR[, 포지션]) 조합만 재사용한다)
                            # [2026-09 최적화] 예전 키는 (목적지팀ID, OVR)이라
                            # 같은 리그의 20팀이 전부 따로 계산됐다(실측 히트율
                            # 연봉 22.9% / 이적료 34.4%). 위 sal_pmult_by_tid
                            # 주석대로 연봉은 (등급, tier, 국가, 명문배율, OVR)로,
                            # 이적료는 (등급, tier, 국가, OVR, 포지션)으로 완전히
                            # 결정되므로 키를 그쪽으로 바꾼다 — 계산식은 그대로고
                            # 반환값도 비트 단위로 같다(결과 해시 불변으로 확인).
                            # 실측: 이적루프 6.558s → 5.477s (-1.08s, -16.5%).
                            _sk = (_dst_grade2, _dst_tier2, _dst_cname2,
                                   sal_pmult_by_tid.get(new_tid), _p_ovr2)
                            _new_salary2 = _salary_cache.get(_sk)
                            if _new_salary2 is None:
                                _new_salary2 = _calc_ai_salary(_dst_grade2, _dst_tier2, _p_ovr2,
                                                                _dst_cname2, _dst_tname2, new_tid, year)
                                _salary_cache[_sk] = _new_salary2
                            # (estimate_transfer_fee는 이 호출부에서 team_name/
                            #  team_id를 안 넘기므로 목적지 팀ID는 결과에 아무
                            #  영향이 없다 — 아래 인자 목록 그대로가 키다.)
                            _fk = (_dst_grade2, _dst_tier2, _dst_cname2,
                                   _p_ovr2, p_entry.get("position"))
                            _fee2 = _fee_cache.get(_fk)
                            if _fee2 is None:
                                _fee2 = estimate_transfer_fee(_dst_grade2, _dst_tier2, _p_ovr2,
                                                               country=_dst_cname2,
                                                               position=p_entry.get("position"),
                                                               year=year) or 0
                                _fee_cache[_fk] = _fee2
                            if _is_loan:
                                # [2026-09 수정, 신민용 확정: "임대는 이적료
                                # 10~20%로 맞춰줘"] 고정 10%였던 걸 매번
                                # 10~20% 사이 무작위 비율로 바꾼다 — _fee2는
                                # 캐시(_fee_cache)에서 막 꺼낸 "완전 이적료"
                                # 원본이라, 여기서 곱해도 캐시된 원본 값은
                                # 그대로 유지된다(다음 선수가 같은 캐시키로
                                # 조회해도 다시 완전 이적료부터 시작함).
                                _fee2 = int(_fee2 * random.uniform(0.10, 0.20))
                            salary_loan_updates.append((
                                _new_salary2, old_tid if _is_loan else 0, _loan_return2, pid))
                            transfer_log_rows.append((
                                _cur_season, year, pid, p_entry.get("name", ""), p_entry.get("position", ""),
                                p_entry.get("age", 0), p_entry.get("ovr", 0),
                                old_tid, new_tid,
                                team_prestige.get(old_tid, 0), team_prestige.get(new_tid, 0),
                                round(team_avg.get(old_tid, 0), 2), round(team_avg.get(new_tid, 0), 2),
                                _actual_ttype, 1 if is_mid_season else 0, _dep_role,
                                _fee2, 1 if _is_loan else 0, _loan_return2, _new_salary2,
                                new_contract_end))
                            # [2026-08 신설, 신민용 요청: "우리팀에 누가
                            # 나가고 누가 들어왔는지"] 우리 팀이 관여한
                            # 건이면(방출 또는 영입) 별도로 모아둔다 —
                            # p_entry(포지션/나이/OVR)를 이 시점에 얕은
                            # 복사해서 남긴다(아래에서 계약 필드를 덮어쓰기
                            # 전이라 이적 전 상태 그대로).
                            if my_team_id is not None and (old_tid == my_team_id or new_tid == my_team_id):
                                direction = "out" if old_tid == my_team_id else "in"
                                my_team_events.append((direction, dict(p_entry), old_tid, new_tid))
                            p_entry["contract_end_year"] = new_contract_end
                            p_entry["last_transfer_year"] = year
                            _new_list = team_players.setdefault(new_tid, [])
                            _new_list.append(p_entry)
                            _sw_by_tid[new_tid] = _size_weight(len(_new_list))
                            # [2026-09 최적화, 신민용 "이적시장 7.4s" 2차]
                            # _estimate_ai_transfer_fee_display는 이적 건마다
                            # estimate_transfer_fee를 team_id까지 넘겨 부른다
                            # (rank/club_strength 보정 경로 포함) — 그런데 그
                            # 결과는 바로 아래 "목적지 국가가 5대 리그 나라인가"
                            # 판정을 통과한 건에서만 쓰인다. 전세계 12,750팀 중
                            # 그 5개국 비중은 한 자릿수%인데 나머지 90%+에 대해서도
                            # 매번 계산만 하고 버리고 있었다. 판정을 계산 앞으로
                            # 옮긴다 — 순수한 순서 교환이다(이 함수는 난수를 전혀
                            # 쓰지 않고 DB도 안 바꾼다. 실측: rng_calls 480,229로
                            # 동일, 결과 해시도 동일).
                            # 실측(실제 세이브 79,233건 이적 기준, min-of-3):
                            #   이적루프 7.383s → 6.558s (-0.83s, -11.2%)
                            # _dst_row2가 없을 때 예전 코드는 _dst_country=None,
                            # 지금은 _dst_cname2=""가 되는데 둘 다 5개국 튜플에
                            # 없으므로 판정 결과가 같다.
                            if verbose_log is not None and _dst_cname2 in _MAJOR_TRANSFER_COUNTRIES:
                                _fee = _estimate_ai_transfer_fee_display(p_entry, old_tid, new_tid, year, team_row_by_tid)
                                if _fee:
                                    _prev = _big_transfer_by_country.get(_dst_cname2)
                                    if _prev is None or _fee[0] > _prev[0]:
                                        _big_transfer_by_country[_dst_cname2] = (*_fee, p_entry["id"])
                    moved += 1
    _tm4 = _time_tm.perf_counter()

    if transfer_updates:
        # [2026-08 최적화] 위 스냅샷과 같은 이유 — WHERE id=? 로 8만 건을
        # 갱신하는데 순서가 뒤죽박죽이면 매번 다른 페이지를 오간다. id 순으로
        # 정렬하면 앞에서 뒤로 한 번 훑는 형태가 된다. 안정 정렬이라 같은
        # 선수가 두 번 들어 있어도(맞트레이드 등) 원래의 앞뒤 순서가 유지되므로
        # 마지막에 적용되는 값이 예전과 같다 — 최종 결과 동일.
        transfer_updates.sort(key=_tu_key)
        c.executemany(
            "UPDATE ai_players SET team_id=?, contract_end_year=?, last_transfer_year=? WHERE id=?",
            transfer_updates)
    if salary_loan_updates:
        # [2026-09 신설] 위 transfer_updates와 순서 무관 — 대상 컬럼이
        # 겹치지 않으므로(team_id/contract_end_year/last_transfer_year 대
        # salary/on_loan_from_team_id/loan_return_year) 어느 쪽이 먼저
        # 적용돼도 결과가 같다.
        c.executemany(
            "UPDATE ai_players SET salary=?, on_loan_from_team_id=?, loan_return_year=? WHERE id=?",
            salary_loan_updates)
    if transfer_log_rows:
        c.executemany(
            """INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            transfer_log_rows)
    _tm5 = _time_tm.perf_counter()
    _perf_log(f"[PERF-TM]  teams조회(서브쿼리포함) {_tm1-_tm0:.3f}s | "
          f"그룹핑 {_tm2-_tm1:.3f}s | team_players빌드 {_tm3-_tm2:.3f}s | "
          f"이적루프({len(by_league)}개리그) {_tm4-_tm3:.3f}s | "
          f"executemany({len(transfer_updates)}건) {_tm5-_tm4:.3f}s")

    # [2026-08 버그수정, 신민용 리포트: "이적 뉴스가 실제론 28주차(겨울
    # 이적시장 마감=WINTER_OFFER_END_DAY) 사건인데 52주차로 뜬다"] 이 함수는
    # 오프시즌 전체 처리(run_ai_offseason, 연 1회·시즌이 완전히 끝난 뒤라
    # 진짜 52주차)와 시즌 도중 겨울 이적시장(run_ai_mid_season_transfer,
    # is_mid_season=True로 호출)이 공유해서 부르는데, 아래 "news" 로그들은
    # 호출 맥락과 무관하게 항상 week=52를 찍고 있었다 — 오프시즌 호출은
    # 실제로 52주차라 우연히 맞았지만, 겨울 이적시장 호출 때도 그대로 52가
    # 찍혀서 실제 사건 시점(겨울 이적시장 마감 주차)과 어긋났다.
    # is_mid_season일 땐 WINTER_OFFER_END_DAY를 주차로 환산해 실제 마감
    # 시점을 쓴다.
    from constants import day_to_week, WINTER_OFFER_END_DAY
    _news_week = day_to_week(WINTER_OFFER_END_DAY) if is_mid_season else 52

    if verbose_log is not None and _big_transfer_by_country:
        from constants import ai_player_code
        from database import get_ai_player_custom_name
        # [2026-08 신설] 국가별로(5대리그) 최고액 1건씩, 이름도 같이 표시.
        # log_type="news" — ui/log_panel.py의 "뉴스" 탭 전용 필터 대상.
        for _country in _MAJOR_TRANSFER_COUNTRIES:
            _entry = _big_transfer_by_country.get(_country)
            if not _entry:
                continue
            fee, ovr, src_name, dst_name, _pid = _entry
            _tag = get_ai_player_custom_name(_pid) or ai_player_code(_pid)
            verbose_log(f"💰 주요 이적({_country}): {_tag} (OVR{ovr})  {src_name} → {dst_name}  "
                        f"예상 이적료 약 {fee/100000:.0f}억원", "news", year, _news_week)

    # [2026-08 신설, 신민용 요청: "우리팀에 누가 나가고 누가 들어왔는지
    # 로그에 표시해달라"] 위 "주요 이적"(전세계 최고액 1건)과 별개로,
    # 우리 팀이 관여한 이적은 방출/영입 전부 각각 한 줄씩 남긴다.
    if verbose_log is not None and my_team_events:
        from constants import ai_player_code
        from database import get_ai_player_custom_name
        for direction, p_entry, old_tid, new_tid in my_team_events:
            _fee = _estimate_ai_transfer_fee_display(p_entry, old_tid, new_tid, year, team_row_by_tid)
            _fee_txt = f"  (예상 이적료 약 {_fee[0]/100000:.0f}억원)" if _fee else ""
            # [2026-08 수정, 신민용 리포트: "좌측(이적 로그)엔 AI (331454)로
            # 뜨는데 포메이션엔 AI 73QU로 따로 뜬다"] 포메이션 화면
            # (ui/formation_widget.py._mask_ai_names)과 완전히 같은 코드
            # 생성 규칙(constants.ai_player_code)을 공유해서, 같은 선수는
            # 어느 화면에서 봐도 항상 같은 표기로 보이게 한다.
            # [2026-08 확장, 신민용 요청: "AICD8C 식별코드로 뜨는 선수의
            # 이름을 내가 지을 수 있게 — 이적 로그도 내가 지은 이름으로"]
            # 사용자가 지어준 이름이 있으면 코드 대신 그 이름을 쓴다.
            _tag = get_ai_player_custom_name(p_entry['id']) or ai_player_code(p_entry['id'])
            # [2026-08 신설, 신민용 요청: "우리팀이 뭔지도 표시해달라 —
            # 나중에 다른 팀으로 옮기면 저게 언제 어느 팀에서 있었던
            # 일인지 알 수가 없다"] 그때 당시의 "우리팀" 이름을 명시
            # 적으로 같이 남긴다 — my_team_id는 이 호출 시점(그 이적이
            # 실제로 일어난 그 해)의 소속팀이므로, 나중에 다른 팀으로
            # 이적해도 이 로그 한 줄만 보면 그때 어느 팀 소속으로 겪은
            # 일인지 항상 알 수 있다.
            _my_team_name = team_row_by_tid.get(my_team_id, {}).get("tname", "우리팀")
            if direction == "out":
                _dst = team_row_by_tid.get(new_tid, {}).get("tname", "?")
                verbose_log(f"📤 방출 — {_tag} ({p_entry.get('position','')} OVR{p_entry.get('ovr',0)}) "
                            f"{_my_team_name} → {_dst}{_fee_txt}", "news", year, _news_week)
            else:
                _src = team_row_by_tid.get(old_tid, {}).get("tname", "?")
                verbose_log(f"📥 영입 — {_tag} ({p_entry.get('position','')} OVR{p_entry.get('ovr',0)}) "
                            f"{_src} → {_my_team_name}{_fee_txt}", "news", year, _news_week)

    return moved


def _estimate_ai_transfer_fee_display(p_entry, old_tid, new_tid, year, team_row_by_tid):
    """[2026-07 v3 신설] 표시용 이적료 — 자금 이동 없음, DB 저장도 없음.
    이적 순간에만 즉석 계산해서 그 시즌 최고액 1건만 로그로 소비하고 버린다.
    OVR85 이상이거나 이적료 최고액인 경우에만 verbose_log에서 실제로
    출력되도록, 여기서는 조건 없이 계산만 해서 넘긴다(최종 필터는 호출부).

    team_row_by_tid: {tid: team_row_dict} — 예전엔 teams 리스트를 매번
    선형탐색(최대 2회)하고 팀명도 별도 SQL SELECT 2회로 조회했는데
    (신민용 리포트: "이적루프 렉" 조사 중 발견), 호출부에서 만든 tid→row
    딕셔너리를 그대로 받아 전부 O(1) 조회로 바꾼다 — 결과는 동일.

    [2026-08 버그수정, 신민용+GPT 리포트: "OVR99 선수가 아틀레티코 마드리드
    → 알코벤다스 CF인데 6276억이면 이상하다"] estimate_transfer_fee()에
    country/team_id를 안 넘기고 있었다 — economy.py 쪽 로직 자체는 멀쩡한데
    (country and tier)가 False가 되어 구단 지불여력 상한(affordability
    cap)이 아예 통째로 건너뛰어지고 있었다(디버그로 직접 확인:
    affordability_cap=None). 명문/체급 보정도 team_id가 없어서 전부 중립
    (1.0) 처리됐다 — 방금 승격한 약체 구단이어도 "표시용 이적료"에서는
    부자 구단과 똑같이 취급됐다는 뜻. dst_row에 이미 cname(국가명)과
    tid(팀ID)가 있으므로 그대로 넘기기만 하면 된다 — 실제 이적(어느
    팀으로 가는지, 스탯이 어떻게 바뀌는지)에는 영향 없음, 오직 이
    로그 한 줄의 "예상 이적료" 표시값만 정확해진다."""
    if p_entry.get("ovr", 0) < 70:
        return None   # 너무 낮은 OVR은 계산 자체를 생략(성능/의미 둘 다 낮음)
    try:
        from economy import estimate_transfer_fee
        from constants import get_country_league_grade
        dst_row = team_row_by_tid.get(new_tid)
        if not dst_row:
            return None
        grade = get_country_league_grade(dst_row["cname"])
        fee = estimate_transfer_fee(grade, dst_row["tier"], p_entry["ovr"],
                                    country=dst_row["cname"], team_id=new_tid,
                                    position=p_entry.get("position"), year=year)
        if not fee or (p_entry.get("ovr", 0) < 85 and fee < 5_000_000):  # 50억(천원단위) 미만이면 스킵
            return None
        src_row = team_row_by_tid.get(old_tid)
        src_name = src_row["tname"] if src_row else None
        dst_name = dst_row.get("tname")
        return (fee, p_entry["ovr"],
                src_name if src_name else "?",
                dst_name if dst_name else "?")
    except Exception:
        return None


# [2026-08 최적화] 아래 _do_one_transfer_cached 전용 순수함수 메모이즈 2종.
# 둘 다 "입력이 정수(또는 작은 정수)뿐인 수식"이라 값이 항상 같으므로
# 캐싱해도 결과가 달라질 여지가 전혀 없다 — 계산식 자체는 원본 그대로다.
_CONTRACT_DECAY = {k: 0.6 ** k for k in range(0, 13)}   # 0.6 ** 남은계약연수
from operator import itemgetter as _itemgetter
_ins_key = _itemgetter(0)   # executemany 전에 기본키 순으로 정렬할 때 쓰는 key
_tu_key  = _itemgetter(3)   # 이적 UPDATE 배치를 player_id 순으로 정렬
_SIZE_W_CACHE: dict = {}


def _size_weight(dst_size):
    """exp(-(스쿼드인원 - _SQUAD_TARGET) / 0.15) — 인원(정수)만의 함수라
    한 번 계산한 값을 그대로 재사용한다. 시즌당 math.exp 호출 약 290만 회
    감소(실측 5,771,032회 중 절반가량이 이 식이었다)."""
    w = _SIZE_W_CACHE.get(dst_size)
    if w is None:
        w = math.exp(-(dst_size - _SQUAD_TARGET) / 0.15)
        _SIZE_W_CACHE[dst_size] = w
    return w


# [2026-08 최적화] 포지션 문자열 → 판매보호 판정용 그룹키(GK/DF/MF/FW).
# 예전 코드의 _GROUP_KEY[_pos_category(pos)]를 한 단계로 미리 합쳐둔 표다
# (formation_logic._POS_CATEGORY와 같은 분류를 그대로 쓴다). 표에 없는
# 포지션(ST/LW/RW/CF/SS 등)은 예전 _pos_category의 최종 폴백 "ATK"에
# 대응하는 "FW"로 떨어지므로 결과가 완전히 같다.
_POS_GROUP = {"GK": "GK",
              "CB": "DF", "LB": "DF", "RB": "DF", "LWB": "DF", "RWB": "DF", "SW": "DF",
              "CDM": "MF", "CM": "MF", "CAM": "MF", "LM": "MF", "RM": "MF", "DM": "MF", "AM": "MF"}

# [2026-09 최적화] 위 표와 완전히 같은 분류를 인덱스(0~3)로만 표현한 판.
# _do_one_transfer_cached의 그룹 인원 집계 루프가 dict 카운터 대신 4칸짜리
# 리스트를 쓸 수 있게 하기 위한 것 — 폴백(표에 없는 포지션 → "FW")도
# 인덱스 3으로 똑같이 대응한다. 두 표는 항상 같이 고쳐야 하며, 아래
# assert가 import 시점에 불일치를 즉시 잡는다.
_GROUP_ORDER = ("GK", "DF", "MF", "FW")
_POS_GROUP_IDX = {_p: _GROUP_ORDER.index(_g) for _p, _g in _POS_GROUP.items()}
assert all(_GROUP_ORDER[_POS_GROUP_IDX[_p]] == _g for _p, _g in _POS_GROUP.items())


def _do_one_transfer_cached(src, dst_pool_tids, team_players, team_avg, year, protect_strength=0.85,
                             veteran_pool_tids=None, dst_grade_by_tid=None, dst_prestige_by_tid=None,
                             pool_cache=None, sw_by_tid=None, src_grade_rank=None,
                             dst_ovr_ceiling_by_tid=None, dst_country_by_tid=None):
    """[최적화] ORDER BY RANDOM() 없이 Python-side shuffle로 이적 처리.
    team_players: {team_id: [{"id","position","ovr","contract_end_year",
    "last_transfer_year"}, ...]} 선조회 캐시.
    src: 판매 측 팀ID(호출부에서 이미 결정해서 넘김).
    dst_pool_tids: 목적지 후보 팀ID 리스트(같은 리그/국내 다른 tier/국제
      중 호출부가 이미 결정한 풀 — src 자신은 포함 안 돼 있어도/있어도 무방,
      아래에서 다시 한번 걸러진다).

    [버그수정 2026-07] team_avg를 함수가 받기만 하고 실제로는 전혀 참조하지
    않아, 리그 내 이적이 팀 실력과 무관하게 완전 무작위로 일어나고 있었다
    (최강팀 선수가 최약팀으로 가는 것과 그 반대가 똑같은 확률). 이제 이동할
    선수(mover)의 OVR과 각 목적지 팀 평균OVR(team_avg) 차이가 작을수록
    (비슷한 수준 팀끼리, 혹은 살짝 더 좋은 팀으로) 그 팀이 목적지로 뽑힐
    확률이 높아지도록 가우시안 가중치를 준다 — 팀 간 실력차가 40 이상이면
    사실상 이적 후보에서 배제된다(가중치가 0에 수렴).

    [2026-07 v2 신설, 신민용+GPT 검토: "레알 에이스나 벤치나 완전히 같은
    확률로 이적하면 세계가 너무 흔들린다 — 스타 선수는 조금만 보호해도
    이적시장이 훨씬 현실적으로 보인다"] mover를 뽑는 단계 자체를 완전
    균등추출(random.choice)에서, "팀 내 OVR 순위가 높을수록 뽑힐 확률을
    낮추는" 가중 추출로 바꾼다. 팀 재정/감독 관계 같은 내 선수급 디테일은
    AI에겐 없으니(원칙: AI는 플레이어보다 단순해야 한다), OVR 순위 하나만
    가지고 가볍게 계산한다.

    [2026-07 v3 신설] 계약 잔여기간 반영 + 최소 잔류기간(1시즌). 둘 다
    "AI는 단순하게" 원칙에 맞춰 가벼운 규칙만 적용한다:
      - last_transfer_year가 최근(작년)이면 이적 후보에서 아예 제외.
      - 계약 미설정(0, 기존 선수)은 중립 취급, 설정돼 있으면 남은 연수가
        많을수록(0.6^연수) 뽑힐 확률이 줄어든다.

    [2026-07 v3 신설] 국내 다른 tier/국제 이동 풀이 넘어올 경우, 어린
    선수·고OVR·계약만료 임박 선수일수록 그 풀로 실제로 이동될 확률이
    붙도록(성향 보정) mover 선정 가중치에 반영한다 — "유망주 해외 진출/
    베테랑 잔류/스타 이적" 패턴이 자연스럽게 생기게.

    [2026-08 확장, 신민용 요청: "무작위로 바꾸지 말고 핵심 선수는 상황에
    따라 다르게 가야 하지 않냐"] protect_strength를 호출부(카테고리별
    _STAR_PROTECT_BY_CATEGORY)에서 넘겨받는다 — 강팀은 에이스를 거의 안
    팔고(높은 보호), 약팀/강등팀은 반대로 에이스도 현금화 대상이 된다
    ("고주급자 스타들은 팀을 떠나려 한다"는 신민용 스펙 그대로) 낮은
    보호로 실제 매각 확률이 오르게. 기본값 0.85는 예전 고정값과 동일 —
    호출부가 안 넘기면 기존과 완전히 같게 동작한다.

    [2026-08 신설, 신민용 요청: "SS에서 뛰던 선수도 A로 바로 갈 수 있고,
    사우디·미국 1부 위주로 가는 그림을 만들어달라 — 현실에서도 손흥민이
    토트넘에서 미국으로 갔다"] veteran_pool_tids(사우디·미국 tier1
    팀 목록, 호출부가 src가 S/SS급 최상위권일 때만 넘김)가 있고 이번에
    뽑힌 mover가 30세 이상이면, 60% 확률로 목적지 후보를 이 풀로 바꿔서
    고른다 — mover가 정해지기 전(위 후보군 결정 시점)엔 그 선수 나이를
    알 수 없어서, 여기 mover 선정 직후에 판단해야 한다. 30세 미만이거나
    확률에 안 걸리면 기존처럼 등급대 기반 일반 후보군을 그대로 쓴다.
    [2026-08 신설, 신민용 리포트: "38~39세 OVR84~86짜리가 바르셀로나로
    이적한다 — 유럽 5대 리그급이 왜 저런 나이의 선수를 영입하냐"] 예전엔
    mover 선정도 목적지 선정도 나이를 전혀 안 봤다(OVR/스쿼드 크기만
    반영) — 이제 (1) 33세 이상이면 mover로 뽑힐 가중치를 추가로 올려
    노쇠한 선수가 (특히 좋은 팀에서) 더 빨리 정리되게 하고, (2) 목적지가
    SS/S(5대 리그급) 등급이면 33세 이상부터 나이에 비례해 급격히 감쇠하는
    페널티를 곱한다 — 다만 OVR이 정말 레전드급(85 초과)이면 감쇠를
    완화해 "노장 슈퍼스타가 아주 가끔 빅클럽에 남는" 예외는 허용한다.

    [2026-09 버그수정, 신민용 리포트: "OVR82가 설계상한74인 한국으로
    이적해 들어온다"] dst_ovr_ceiling_by_tid(팀ID -> 그 나라 설계 OVR
    상한, 오버라이드 포함)를 넘기면, 목적지 가중치 계산에서 mover_ovr이
    그 상한을 초과하는 후보는 초과분만큼 추가로 감쇠된다(_dst_ceiling_
    penalty). 기존 gap 가중치(team_avg 기준)는 "그 팀의 지금 실제
    스쿼드 수준"만 보고 "그 나라의 구조적 설계 상한"은 전혀 몰랐던 것을
    보완 — None이면 기존과 100% 동일하게 동작(회귀 없음).
    """
    src_players = team_players.get(src, [])
    if not src_players:
        return None

    # 최소 잔류기간: 작년(또는 그 이후)에 이미 이적한 선수는 이번엔 후보 제외
    # [2026-08 최적화] team_players의 각 항목은 _transfer_market이 만들 때
    # 6개 키를 항상 전부 채우고(빠지는 경우가 구조적으로 없음), 이적으로
    # 팀을 옮겨 다니는 동안에도 같은 dict가 그대로 재사용된다 — 그래서
    # 이 함수 전체에서 .get(키, 기본값) 대신 직접 인덱싱을 쓴다. cProfile
    # 실측상 이 함수 하나가 dict.get을 2,421만 회 호출하고 있었는데(시즌당
    # 이적 7.4만 건 × 후보 선수 수 × 키 6개), 결과는 완전히 동일하면서
    # 호출당 오버헤드만 사라진다.
    eligible = [p for p in src_players if (year - p["last_transfer_year"]) >= 1]
    if not eligible:
        return None

    # [2026-08 신설, 신민용 요청: "마지막 GK/마지막 CB 같은 선수가 정상
    # 판매 후보로 들어가면 안 된다 — 그 선수를 팔면 팀에 해당 포지션
    # 그룹이 0명이 되는가만 검사해서 막아야 한다"] 원인: 위 eligible은
    # "작년에 이적했는가"만 볼 뿐 포지션은 전혀 안 봐서, 팀의 유일한
    # GK도 다른 후보와 똑같이(순위가 낮으면 오히려 더 높은 확률로) 팔려
    # 나갈 수 있었다 — 신민용 리포트: GK가 0명이라 LW 주포 선수가 GK로
    # 뛴 사례. 이 아래 필터는 그 경로 자체를 차단한다: "팔면 그 포지션
    # 그룹(GK/DF/MF/FW, _pos_category 기준)이 팀에서 0명이 되는 선수"만
    # 후보에서 제외하고, 그 외에는 기존 판매 확률 가중치(OVR순위/나이/
    # 계약)를 그대로 둔다 — 신민용 명시 요청대로 가중치 자체는 절대
    # 안 건드림. src_players(시간 필터 전 팀 전체 로스터) 기준으로
    # 그룹별 인원을 세야 정확하다(마지막 1명 판정은 "지금 이 팀에 몇
    # 명 있는가"의 문제이지 "언제 이적했는가"와는 무관하므로).
    # [2026-08 최적화] 예전엔 선수 한 명당 "함수 호출(_pos_category) →
    # 그 결과로 _GROUP_KEY.get" 2단계를 거쳤다. 시즌당 이적 7.4만 건 ×
    # 팀 로스터 23명 × 2회(집계+판정)라 이 2단계만 350만 회 넘게 돌았다.
    # 포지션 문자열 → 그룹키는 순수 대응이므로 모듈 상단에서 한 번
    # 합쳐둔 표(_POS_GROUP)로 조회 한 번에 끝낸다 — 매핑 결과는 예전과
    # 완전히 동일(표에 없는 포지션은 ATK→"FW"로 떨어지는 것까지 동일).
    # [2026-09 최적화] 여기가 이 함수에서 dict.get을 가장 많이 부르던
    # 자리다(cProfile 실측: 이 함수 하나가 이적시장 한 번에 dict.get을
    # 1,018만 회 호출, 그중 약 730만 회가 이 두 루프). 판정 결과는 그대로
    # 두고 두 가지만 없앤다:
    #  (a) 집계 루프에서 그룹 카운트를 dict.get 대신 4칸짜리 리스트
    #      누산으로 바꾼다 — 포지션→그룹 조회가 1회만 남는다.
    #  (b) "그룹 인원이 1명 이하인 그룹"이 하나도 없으면 _protected_ids는
    #      반드시 빈 집합이고 아래 if도 항상 거짓이라 eligible이 전혀
    #      바뀌지 않는다. 대다수 팀(포지션 그룹당 2명 이상)이 이 경우라
    #      두 번째 스캔 자체를 건너뛴다 — 결과는 완전히 동일하다.
    _pg = _POS_GROUP
    _gi = _POS_GROUP_IDX
    _cnt = [0, 0, 0, 0]
    for _p in src_players:
        _cnt[_gi.get(_p["position"], 3)] += 1
    if _cnt[0] <= 1 or _cnt[1] <= 1 or _cnt[2] <= 1 or _cnt[3] <= 1:
        _thin = {_GROUP_ORDER[_i] for _i in range(4) if _cnt[_i] <= 1}
        _protected_ids = {p["id"] for p in eligible
                           if _pg.get(p["position"], "FW") in _thin}
        if _protected_ids and len(_protected_ids) < len(eligible):
            eligible = [p for p in eligible if p["id"] not in _protected_ids]
    # (매우 드문 극단적 예외: 팀 전체가 포지션 그룹당 딱 1명씩이라 위
    # 필터가 eligible을 통째로 비워버리는 경우엔 적용하지 않는다 —
    # 이적 자체가 완전히 멈추는 것보다는 기존 동작이 낫다.)
    if not eligible:
        return None

    n = len(eligible)
    if n == 1:
        mover = eligible[0]
    else:
        # [2026-08 최적화] 예전엔 sorted(range(n), key=lambda i: -eligible[i].get("ovr",50))
        # 로 정렬해서 비교 한 번마다 파이썬 람다 + dict.get이 돌았다(실측
        # 람다 호출만 151만 회). OVR을 미리 한 번씩만 꺼내 리스트로 만들고
        # 그 리스트의 __getitem__을 key로 쓰면 비교 자체는 C 레벨에서 끝난다
        # — 키 값(-ovr)도, 동점자 순서(파이썬 정렬은 안정 정렬)도 예전과
        # 완전히 동일하므로 뽑히는 선수가 달라지지 않는다.
        # [2026-09 최적화] team_avg는 이 루프 내내 불변인데(아래 (a) 주석)
        # 후보 한 명마다 team_avg.get(src, 50)을 다시 부르고 있었다 —
        # 이적시장 한 번에 152만 회. 값이 같으므로 루프 밖으로 뺀다.
        _src_avg = team_avg.get(src, 50)
        # [2026-09 최적화, 이적시장 2차] _outlier_intl_multiplier를 이 루프에
        # 한해 인라인한다. 이 함수는 mover 후보 한 명당 1회 도는 자리라
        # 시즌당 1,438,098회 불리는데(실측), 안에 프로세스 수명 메모가 있어도
        # "함수 호출 + 3-튜플 생성 + dict 조회"라는 고정 오버헤드는 그대로
        # 남는다. src가 정해지면 market_scale(등급rank만의 함수)은 상수이므로
        # 루프 밖으로 빼고, gap<=0이면 원식이 정확히 1.0을 돌려주므로
        # (1.0 곱하기는 부동소수에서 항등) 곱셈 자체를 건너뛴다.
        # 남는 경우의 식·연산 순서는 원본과 한 글자도 다르지 않다 —
        # 결과 해시/rng_calls 불변으로 확인. 실측(min-of-5): 5.631s → 5.315s.
        # (원본 함수는 다른 호출부[_transfer_market의 팀 단위 계산]에서
        #  그대로 쓰이므로 삭제하지 않는다.)
        _oim_ms = 1.0 + max(0, 8 - (src_grade_rank if src_grade_rank is not None else 8)) \
                        * _MARKET_RANK_STEP
        _oim_base = _src_avg or 0
        _oim_cap = _OUTLIER_COMPONENT_CAP
        _oim_div = _OUTLIER_GAP_DIVISOR
        _neg_ovr = [-e["ovr"] for e in eligible]
        ranked = sorted(range(n), key=_neg_ovr.__getitem__)
        _inv = n - 1   # n>=2 이므로 예전의 max(1, n-1)과 항상 같은 값
        weights = [0.0] * n
        for pos_rank, i in enumerate(ranked):
            _e = eligible[i]
            w = 0.15 + protect_strength * (pos_rank / _inv)
            _cend = _e["contract_end_year"] or 0
            remain = max(0, _cend - year) if _cend else 2   # 미설정=중립(2년 취급)
            w *= _CONTRACT_DECAY.get(remain) or 0.6 ** remain
            # [2026-07 v3] 어린 선수(22세 이하)·고OVR(80+)·계약만료 임박(1년
            # 이하)일수록 이 풀(국내 다른 tier/국제 이동)로 실제 이동될
            # 성향을 살짝 높인다. dst_pool_tids가 src 포함 같은 리그 그대로면
            # (=87% 케이스) 이 보정은 사실상 의미 없이 상쇄되므로 안전하다.
            _age = _e["age"]
            if _age <= 22:
                w *= 1.3
            if _e["ovr"] >= 80:
                w *= 1.5
            if _cend and (_cend - year) <= 1:
                w *= 1.4
            # [2026-08 신설] 노쇠한 선수(33세+)는 팀 내 OVR 순위와 무관하게
            # 추가로 이동(퇴출) 확률을 높인다 — 현실 클럽은 "아직 스쿼드
            # 내 최약체는 아니어도" 나이 자체를 이유로 세대교체를 하므로.
            if _age >= 33:
                w *= 1.0 + 0.10 * (_age - 32)
            # [2026-09 버그수정, 신민용 리포트: "K리그 에이스 레벨이 70대
            # 초반인데 80대 선수가 안 팔리고 그대로 있다"] 원인: 팀 내
            # OVR 1위(pos_rank=0)는 protect_strength와 무관하게 기본
            # 가중치가 항상 0.15로 고정되고(위 w = 0.15 + protect_strength
            # * (pos_rank/_inv) 식에서 pos_rank=0이면 뒤 항이 0), 그 선수가
            # 팀을 이끌어 성적이 좋을수록(→ "strong" 카테고리) protect_
            # strength가 가장 높은 0.92로 잡혀 정작 옆 동료들 가중치만
            # 더 크게 올라간다 — 결과적으로 리그 최고 수준 에이스가 팀
            # 내에서 가장 안 팔리는 선수가 되는 역설이 생겼다. 위 "ovr>=80
            # 이면 ×1.5"는 절대 OVR 기준이라 SS급 리그(팀 평균이 이미
            # 80대)에서는 의미가 없고, 반대로 팀 평균이 60대인 K리그에서
            # 압도적으로 튀는 80대 에이스에게는 충분한 보정이 못 됐다.
            # _outlier_intl_multiplier(원래 "국제이동 비중"을 시장 대비
            # 상대적 위치로 조정하려고 만든 함수 — 위 주석 참고)를 그대로
            # 재사용해 mover 선정 가중치에도 곱한다: 팀 평균보다 압도적으로
            # 튀고 그 나라 리그 등급이 약할수록 배수가 커져서, 스타
            # 보호(protect_strength)를 뚫고서라도 뽑힐 확률이 오른다.
            # gap<=0(아웃라이어 아님)이거나 SS/S급이면 배수가 정확히 1.0
            # 이라 그런 선수들에게는 기존 동작과 100% 동일하게 유지된다.
            # (위 _oim_ms 주석 참고 — _outlier_intl_multiplier 인라인)
            _oim_gap = (_e["ovr"] or 0) - _oim_base
            if _oim_gap > 0.0:
                w *= 1.0 + min(_oim_cap, _oim_gap / _oim_div) * _oim_ms
            weights[i] = w
        mover = random.choices(eligible, weights=weights, k=1)[0]

    # [2026-08 신설, 신민용 요청: "사우디·미국 1부 위주로 가는 그림"]
    # 나이 든(30세+) 선수가 최상위권 리그를 떠나는 경우, 이 풀이 있으면
    # 60% 확률로 목적지 후보를 여기로 바꾼다 — 나머지 40%/veteran_pool
    # 자체가 비어있는 경우엔 기존 등급대 기반 일반 후보군을 그대로 쓴다
    # (은퇴 무대로 완전히 강제하지 않고 개인차/확률을 남겨둔다).
    if veteran_pool_tids and mover["age"] >= 30 and random.random() < 0.6:
        # [2026-08 최적화] 예전엔 여기서 매번 [t for t in veteran_pool_tids
        # if t != src]로 새 리스트를 만들었다 — src 제외는 아래 가중치
        # 루프가 어차피 한 번 더 하므로, 여기서는 "src를 뺐을 때 남는 팀이
        # 하나라도 있는지"만 O(1)로 판정하고 원본 리스트를 그대로 넘긴다
        # (아래 풀 메타데이터 캐시가 같은 리스트 객체를 재사용할 수 있게
        # 하는 효과도 있다). 판정 결과·이후 동작은 예전과 동일.
        if len(veteran_pool_tids) > 1 or veteran_pool_tids[0] != src:
            dst_pool_tids = veteran_pool_tids

    mover_ovr = mover["ovr"]
    # [2026-09 신설, 신민용 요청: "사우디/미국/A급 유럽처럼 조국 귀환도
    # 약하게 선호해야 한다"] 30세 이상이고 목적지 후보군에 국가 정보가
    # 있을 때만 활성화 — 어린 선수의 이적까지 조국 쪽으로 밀면 자연스러운
    # 해외 진출 흐름을 해치므로 베테랑 한정. 배율도 1.5로 약하게만 줘서
    # "33~36세면 다 고향으로" 같은 극단이 안 나오게 한다(다른 후보들도
    # 여전히 뽑힐 수 있음 — 배제가 아니라 가산일 뿐).
    _HOME_RETURN_BONUS = 1.5
    _mover_nat = mover.get("nationality") or None
    _home_bonus_on = bool(_mover_nat) and mover["age"] >= 30 and dst_country_by_tid is not None
    # 가우시안 가중치: 목적지 팀 평균OVR이 이 선수 수준과 비슷할수록(약간
    # 위쪽 포함) 가중치가 크다. sigma=15 → 격차 15면 가중치 약 0.61배,
    # 격차 30이면 약 0.14배로 실질 배제 수준까지 떨어진다.
    # [2026-08 버그수정, 신민용 리포트: "잉글랜드 같은 나라도 부족한 팀이
    # 나올 수 있는 거 아니냐"] OVR 격차만 보고 목적지를 고르면 스쿼드가
    # 이미 넘치는 팀도 계속 영입 후보가 되고, 이미 얇아진 팀은 계속
    # 배제될 이유가 없어서 순수 랜덤워크로 격차가 무한정 벌어졌다(40시즌
    # 시뮬레이션 실측: 같은 20팀 리그 안에서 6명~28명까지 벌어짐). 목적지
    # 팀의 현재 스쿼드 크기가 기준(_SQUAD_TARGET)보다 작을수록 가중치를
    # 올리고 클수록 내려서, 위 src 쪽 가중치와 함께 "커지면 팔고 작아지면
    # 사는" 복원력을 만든다. 나눔값(2.0)은 여러 배율(2/3/5/8/12)로 40~60
    # 시즌씩 돌려 비교한 값 — 12는 여전히 대부분 시즌에 어느 팀이 15명
    # 밑으로 떨어졌고, 2 정도로 좁혀야(위 src 쪽 제곱 가중치와 함께)
    # 20팀 리그 기준 60시즌 중 1~7번 수준으로 "정말 드문 예외"가 된다
    # (여러 시드·8팀 소규모 리그로도 재확인).
    # [2026-08 재수정] _SQUAD_TARGET이 18→23으로 오르면서 이 계수(2.0)도
    # 다시 튜닝 — 0.15로 훨씬 좁혀야(위 src 지수도 5로 강화) 새 정상범위
    # (22~25)에서 비슷한 수준의 안정성이 나온다. 여러 시드·리그 크기로
    # 재검증했다.
    # [2026-08 강화, 신민용 리포트: "OVR74인 37세 선수가 프리미어리그에
    # 있다가 1부/2부를 오가고, 전북현대(OVR 60후반~70대)에 OVR50짜리가
    # 뛰기도 한다 — 노련함으로 어느 정도는 인정해도 이건 너무 심하다"]
    # 분모 450(sigma≈15)은 격차 18~20에서도 가중치가 0.4~0.5로 여전히
    # 높게 남아, 이런 수준 미스매치가 드물지 않게 실제로 성사됐다.
    # 170(sigma≈9.2)으로 좁혀서 격차 10 안팎은 예전과 비슷하게 흔하되
    # (0.55 부근), 격차 20 근처부터는 급격히 희박해지게(0.09 부근) 만든다
    # — "가끔은 있어도 되지만 흔하면 안 된다"는 요청에 맞춘 튜닝.
    # [2026-08 최적화] 아래 루프가 이 함수 — 나아가 시즌 전환 전체 —
    # 에서 가장 뜨거운 지점이었다. cProfile 실측으로 math.exp가 시즌당
    # 5,771,032회 호출됐는데, 목적지 후보 하나당 2~3회씩 도는 게 원인이다
    # (특히 국제 이동(5%) 후보군은 한 번에 1,200팀 규모). 세 가지를 고친다:
    #
    #  (a) team_avg / dst_prestige_by_tid / dst_grade_by_tid는 _transfer_market이
    #      루프 시작 전에 한 번 만든 뒤 끝까지 바뀌지 않는다 — 그래서
    #      "이 후보 풀의 팀별 (평균OVR, sigma 분모, SS/S 여부)"도 불변이다.
    #      풀 리스트 객체 단위로 이 배열들을 한 번만 만들어 캐시하면
    #      (pool_cache) 이후 호출에서는 dict 조회 자체가 사라진다.
    #  (b) size_w = exp(-(스쿼드인원 - _SQUAD_TARGET)/0.15)는 인원(정수)만의
    #      순수 함수라 값을 메모이즈할 수 있다(_size_weight).
    #  (c) 나이 페널티는 후보마다 값이 똑같은데 루프 안에서 매번 다시
    #      계산하고 있었다 — 루프 밖으로 한 번만 끌어올린다.
    #
    # 계산식·상수·후보 순서는 전혀 건드리지 않았으므로 가중치 값도,
    # random.choices가 뽑는 결과도 예전과 완전히 동일하다.
    _meta = pool_cache.get(id(dst_pool_tids)) if pool_cache is not None else None
    if _meta is None or _meta[0] is not dst_pool_tids:
        _avgs = [team_avg.get(t, 50) for t in dst_pool_tids]
        if dst_prestige_by_tid:
            _dens = []
            for t in dst_pool_tids:
                _p = dst_prestige_by_tid.get(t, 0)
                _dens.append(35.0 if _p >= 3 else (60.0 if _p >= 2 else 170.0))
        else:
            _dens = [170.0] * len(dst_pool_tids)
        _tops = ([(dst_grade_by_tid.get(t) in ("SS", "S")) for t in dst_pool_tids]
                 if dst_grade_by_tid is not None else None)
        # [2026-09 신설] 목적지 나라 설계 OVR 상한 페널티용 배열 —
        # dst_ovr_ceiling_by_tid가 없으면(하위호환 경로) 전부 None이라
        # 아래 루프에서 _dst_ceiling_penalty가 항상 1.0(무영향)을 반환한다.
        _ceils = ([dst_ovr_ceiling_by_tid.get(t) for t in dst_pool_tids]
                  if dst_ovr_ceiling_by_tid is not None else [None] * len(dst_pool_tids))
        # [2026-09 신설] 조국 귀환 가산용 — dst_pool_tids 자체(어떤 팀들이
        # 후보인가)에만 의존하는 값이라(mover와 무관) 다른 배열들과 똑같이
        # 풀 단위로 캐싱해도 안전하다. mover 국적과의 실제 비교는 아래
        # 루프에서 mover 하나로 매 호출마다 다르게 이뤄진다.
        _countries = ([dst_country_by_tid.get(t) for t in dst_pool_tids]
                      if dst_country_by_tid is not None else [None] * len(dst_pool_tids))
        _meta = (dst_pool_tids, _avgs, _dens, _tops, _ceils, _countries)
        if pool_cache is not None:
            pool_cache[id(dst_pool_tids)] = _meta
        # 인원 가중치 표에 이 풀의 팀이 하나라도 빠져 있으면(= 선수 명단이
        # 아예 비어 있는 팀) 여기서 채워둔다. 예전 코드의
        # len(team_players.get(t, [])) → 0 과 같은 값이며, 풀마다 딱 한 번만
        # 돌기 때문에(위 캐시에 걸림) 후보 평가 루프에서는 조건 검사 없이
        # sw_by_tid[t] 한 번으로 끝낼 수 있다.
        if sw_by_tid is not None:
            _sw0 = _size_weight(0)
            for _t in dst_pool_tids:
                if _t not in sw_by_tid:
                    sw_by_tid[_t] = _sw0
    _, _avgs, _dens, _tops, _ceils, _countries = _meta
    # 하위호환: sw_by_tid 없이 호출되는 옛 경로(_do_one_transfer)에서는
    # 예전과 똑같이 team_players에서 그때그때 만들어 쓴다.
    _sw_by_tid = sw_by_tid if sw_by_tid is not None else {
        t: _size_weight(len(team_players.get(t, ()))) for t in dst_pool_tids}

    # 나이 페널티(후보와 무관하게 mover 하나로 결정되는 상수) 선계산.
    _age_penalty = 1.0
    _apply_age_penalty = False
    if _tops is not None and mover["age"] >= 33:
        _apply_age_penalty = True
        _age_excess = mover["age"] - 32
        _legend_relief = max(0.0, (mover_ovr - 85) / 15.0)
        _denom = 18.0 + 40.0 * _legend_relief
        _age_penalty = math.exp(-(_age_excess * _age_excess) / _denom)

    # [2026-08 2차 최적화] 이 루프는 시즌 전환 전체에서 가장 많이 도는
    # 구간(시즌당 후보 평가 267만 회)이라 "한 번당 몇 나노초"가 그대로
    # 총 시간이 된다. 1차 최적화 뒤 프로파일에 남아 있던 세 가지를 없앤다:
    #  · _size_weight()를 후보마다 호출 — 값 자체는 이미 캐시돼 있었지만
    #    파이썬 함수 호출이 267만 번이라 그 오버헤드가 계산보다 더 컸다.
    #    호출자가 넘겨주는 _sw_by_tid(팀별 인원 가중치 표)에서 dict 조회
    #    한 번으로 끝낸다 — 이 표는 팀 인원이 실제로 바뀔 때(이적 1건당
    #    2팀)만 갱신하면 되므로, 시즌당 15만 회 갱신으로 267만 회의
    #    "dict.get + len + 함수호출"을 대체하는 셈이다.
    #  · enumerate + _avgs[_i] + _dens[_i] 인덱싱 → zip으로 한 번에 꺼낸다.
    #  · 나이 페널티가 없는 대다수 경우에도 후보마다 if를 두 번씩 확인 →
    #    적용 여부는 mover 하나로 정해지므로 루프를 두 갈래로 나눈다.
    # 아래 두 갈래 모두 예전과 같은 식을 같은 순서로 계산한다:
    #   ovr_w  : 명문팀(prestige_level>=2)일수록 좁은 격차만 허용하도록
    #            sigma 분모를 줄인 값(170 일반 / 60 레벨2 / 35 레벨3) —
    #            이 분모는 팀별로 불변이라 _dens에 미리 담아둔 것이다.
    #   size_w : 목표 인원(_SQUAD_TARGET)에서 멀어질수록 급감하는 가중치.
    #   나이   : 목적지가 SS/S(5대 리그급)면 33세부터 초과분의 제곱에
    #            비례해 감쇠(OVR 85 초과는 분모를 넓혀 소폭 완화) — 값이
    #            mover 하나로 정해지므로 루프 밖에서 이미 계산해뒀다.
    _exp = math.exp
    dst_candidates = []
    weights = []
    _wsum = 0.0
    _dc_append = dst_candidates.append
    _w_append = weights.append
    if _apply_age_penalty:
        for t, _avg, _den, _top, _ceil, _cty in zip(dst_pool_tids, _avgs, _dens, _tops, _ceils, _countries):
            if t == src:
                continue
            if _ceil is not None and (mover_ovr - _ceil) > _DST_CEIL_HARD_EXCLUDE:
                # [2026-09 신설] 초과분이 크면 size_weight(스쿼드 부족팀
                # 가중치)가 아무리 커도 못 이기게, 애초에 후보에서 제외한다
                # (위 _DST_CEIL_HARD_EXCLUDE 정의부 주석 — 실측으로 확인한
                # 실패 사례 참고).
                continue
            gap = _avg - mover_ovr
            w = _exp(-(gap * gap) / _den) * _sw_by_tid[t]
            if _top:
                w *= _age_penalty
            if _ceil is not None:
                _excess = mover_ovr - _ceil
                if _excess > 0:
                    w *= _exp(-(_excess * _excess) / _DST_CEIL_EXCESS_DENOM)
            if _home_bonus_on and _cty == _mover_nat:
                w *= _HOME_RETURN_BONUS
            _dc_append(t)
            _w_append(w)
            _wsum += w
    else:
        for t, _avg, _den, _ceil, _cty in zip(dst_pool_tids, _avgs, _dens, _ceils, _countries):
            if t == src:
                continue
            if _ceil is not None and (mover_ovr - _ceil) > _DST_CEIL_HARD_EXCLUDE:
                continue
            gap = _avg - mover_ovr
            w = _exp(-(gap * gap) / _den) * _sw_by_tid[t]
            if _ceil is not None:
                _excess = mover_ovr - _ceil
                if _excess > 0:
                    w *= _exp(-(_excess * _excess) / _DST_CEIL_EXCESS_DENOM)
            if _home_bonus_on and _cty == _mover_nat:
                w *= _HOME_RETURN_BONUS
            _dc_append(t)
            _w_append(w)
            _wsum += w
    if not dst_candidates:
        return None
    if _wsum <= 0:
        dst = random.choice(dst_candidates)
    else:
        dst = random.choices(dst_candidates, weights=weights, k=1)[0]

    dst_players = team_players.get(dst, [])
    same_pos = [p for p in dst_players if p["position"] == mover["position"]
                and (year - p["last_transfer_year"]) >= 1]

    # [2026-08 버그수정, 신민용 리포트: "상대팀에서 선수가 나가면 무조건
    # 그 팀에서 한 명이 우리 쪽으로 오는 식인데 현실은 이렇게 안
    # 진행된다"] 예전엔 목적지 팀에 같은 포지션 선수가 있기만 하면(대부분
    # 팀은 포지션마다 최소 1명은 있으므로 사실상 거의 항상) 그 선수를
    # 자동으로 맞바꿔 보냈다 — 모든 이적이 사실상 "선수 대 선수 맞트레이드"
    # 가 되어버리는 구조였다. 실제 축구는 이런 1:1 맞트레이드가 오히려
    # 드문 예외(주로 같은 리그 라이벌 팀끼리 필요에 의해 성사)이고,
    # 대부분은 이적료를 매개로 한 일방적 이동(우리는 내보내기만 하거나
    # 받기만 함)이다. 이제 같은 포지션 선수가 있어도 낮은 확률
    # (SWAP_DEAL_CHANCE)로만 실제 맞트레이드가 성사되고, 나머지는 전부
    # 일반적인 일방 이적으로 처리한다 — 스쿼드 인원수는 은퇴자 즉시 충원
    # (_retire_and_replace)과 전 세계 단위로 봤을 때의 유입/유출 균형으로
    # 자연히 맞춰지므로, 매 이적마다 억지로 1:1을 맞출 필요가 없다.
    SWAP_DEAL_CHANCE = 0.12
    if same_pos and random.random() < SWAP_DEAL_CHANCE:
        swap = random.choice(same_pos)
        # (new_tid, pid, old_tid)
        return [(dst, mover["id"], src), (src, swap["id"], dst)]
    else:
        return [(dst, mover["id"], src)]


# _do_one_transfer는 하위호환용 별칭 (외부에서 직접 호출하는 경우 대비)
def _do_one_transfer(c, tids, team_avg, year=None):
    """하위호환 래퍼. 신규 코드는 _do_one_transfer_cached 사용."""
    import time as _t
    if year is None:
        year = _t.gmtime().tm_year
    players_rows = c.execute(
        "SELECT id, team_id, position, ovr, contract_end_year, last_transfer_year "
        "FROM ai_players WHERE team_id IN ({})".format(
            ",".join("?" for _ in tids)), tids).fetchall()
    tp: dict = {}
    for r in players_rows:
        tp.setdefault(r["team_id"], []).append({
            "id": r["id"], "position": r["position"],
            "ovr": r["ovr"] if r["ovr"] is not None else 50,
            "contract_end_year": r["contract_end_year"] or 0,
            "last_transfer_year": r["last_transfer_year"] or 0,
        })
    return _do_one_transfer_cached(tids[0] if tids else None, tids, tp, team_avg, year)


# ─────────────────────────────────────────────
# 4.5. 스쿼드 인원수 보정 (2026-08 신설)
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# 4.5. 스쿼드 인원수 보정 (2026-08 신설)
# ─────────────────────────────────────────────
# [2026-08 재조정, 신민용 요청: "후보는 최소 GK2/DF3/MF3/FW3(11명)~최대
# GK2/DF4/MF4/FW4(14명)로 맞춰줘"] 주전 11 + 벤치 11~14 = 22~25가 이제
# "정상 스쿼드"이므로, 붕괴 복구용 안전망 임계값도 여기 맞춰 올린다.
# 초기 생성 기준(TEAM_POSITIONS)이 이제 팀마다 벤치 길이가 다른
# 가변값이라 그 길이를 그대로 기준(18)으로 못 쓰므로, 새 정상범위의
# 중간값을 직접 상수로 못박는다 — 이 관계는 database._build_squad_positions()
# (주전11+벤치11~14)와 항상 같이 맞춰서 조정해야 한다.
_SQUAD_TARGET   = 23   # 정상범위(22~25)의 중간값
_SQUAD_MIN      = 22   # 이 밑으로 떨어지면 유망주 영입 (주전11+벤치 최소11)
_SQUAD_MAX      = 25   # 이 위로 넘어가면 조기 은퇴 (주전11+벤치 최대14)


def _archive_forced_out_players(c, ids, year):
    """[2026-08 신설, 신민용 리포트: "이름 지어준 선수(따효니)가 갑자기
    화면(세계기록실 라인업 등)에서 '(공석)'으로 사라졌다"] 원인규명:
    _rebalance_squad_sizes(포지션 균형 조정)와 apply_squad_turnover_
    after_movement(승강 후 물갈이) 둘 다 스쿼드에서 밀려난 선수를
    DELETE FROM ai_players로 곧바로 지우는데, 정상 은퇴 경로
    (_retire_and_replace)와 달리 ai_players_retired 아카이브를 전혀
    안 남겼다 — ai_player_code(id)/이름(ai_player_custom_names)이
    가리킬 실제 행이 아예 없어져서, 이름을 지어준 선수라도 이후 모든
    조회 화면(세계 기록실 라인업/선수 검색 등)에서 완전히 자취를
    감춰버렸다(사용자 세이브 실측: 커스텀 이름 62명 중 5명, 전체로는
    사상 존재했던 730,011명 중 162,105명(22%)이 이 상태였음).

    두 함수 모두 실제 DELETE 직전에 이 함수를 호출해, 삭제될 선수의
    마지막 상태(이름/포지션/OVR/나이/국적/소속팀)를 _retire_and_replace
    와 똑같은 형태로 ai_players_retired에 먼저 남긴다 — 그 다음에야
    진짜 DELETE가 실행되므로, id가 조회 불가능해지는 순간 자체가
    생기지 않는다. ids는 이미 이 시점의 ai_players에 실존하는 행이라
    (아직 지우기 전이므로) 조회가 항상 성공한다."""
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    rows = c.execute(
        f"""SELECT id, name, position, ovr, age, nationality, team_id
            FROM ai_players WHERE id IN ({placeholders})""", ids).fetchall()
    if not rows:
        return
    team_ids = {r["team_id"] for r in rows if r["team_id"]}
    team_names = {}
    if team_ids:
        tph = ",".join("?" * len(team_ids))
        team_names = {r["id"]: r["name"] for r in c.execute(
            f"SELECT id, name FROM teams WHERE id IN ({tph})", list(team_ids)).fetchall()}
    archive_rows = [
        (r["id"], r["name"], r["position"], r["ovr"], r["age"], r["nationality"],
         r["team_id"], team_names.get(r["team_id"], ""), year)
        for r in rows
    ]
    c.executemany(
        """INSERT OR REPLACE INTO ai_players_retired
           (id, name, position, ovr, age, nationality, last_team_id,
            last_team_name, retirement_year)
           VALUES(?,?,?,?,?,?,?,?,?)""", archive_rows)


def _rebalance_squad_sizes(c, year):
    """[2026-08 신설, 신민용 리포트: "이적으로 인한 스쿼드 인원 불균형을
    보정하는 장치가 없다"] 은퇴 교체(_retire_and_replace)는 기존 행을
    그대로 재활용(UPDATE)할 뿐이라 팀별 인원수를 안 바꾼다 — 이적
    (_transfer_market)이 어느 팀엔 계속 순유입, 다른 팀엔 계속 순유출을
    만들면 그 격차가 시즌이 갈수록 그대로 누적된다. 매 시즌 이적 직후
    한 번, 전 세계 팀을 훑어 초기 생성 기준 인원(18명, TEAM_POSITIONS
    길이) 대비 너무 적거나 많은 팀만 되돌린다:
      - 부족(< _SQUAD_MIN): 그 팀 리그 등급/tier에 맞는 OVR 범위에서
        10대(16~19세) 유망주를 새로 영입(INSERT)해 채운다.
      - 과다(> _SQUAD_MAX): 자리를 못 구한(=OVR이 가장 낮은) 선수부터
        조기 은퇴 처리한다 — 신인 교체 없이 그냥 명단에서 빠진다
        (신민용 지적대로, 모든 선수가 30대까지 뛰는 게 아니라 20대에
        일찌감치 접는 선수도 실제로 있다는 점을 반영).
    반환: (topped_up, forced_out) — 영입/조기은퇴된 인원수."""
    from constants import (CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, SUB_ROLES,
                           get_country_league_grade, get_ovr_range, COUNTRY_LEAGUE_OVR_OVERRIDE)
    from database import _pick_nationality, get_foreign_quota_range
    from data.prestige_clubs import prestige_level as _rebal_prestige_level
    from database import _BENCH_GROUP_WEIGHTS, _BENCH_GROUP_POOLS
    from formation_logic import _pos_category
    _GROUP_KEY = {"GK": "GK", "DEF": "DF", "MID": "MF", "ATK": "FW"}

    team_rows = c.execute(
        """SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                  cn.name AS cname, cn.continent AS continent
           FROM teams t JOIN leagues l ON t.league_id=l.id
                        JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_info = {r["tid"]: (r["tier"] or 1, r["cname"], r["continent"] or "유럽", r["tname"])
                 for r in team_rows}

    counts: dict = {}
    for r in c.execute("SELECT team_id, COUNT(*) n FROM ai_players GROUP BY team_id").fetchall():
        counts[r["team_id"]] = r["n"]
    # [2026-08 신설, 신민용 리포트: "키퍼/수비수/미드필더/공격수 비율을
    # 맞춰뒀는데 안 따르는거 같다 — 내 팀 후보 14명 중 5명이 키퍼고
    # 수비수가 0명"] 원인은 AI 이적(_transfer_market)이 포지션을 전혀
    # 안 보고 OVR/나이/계약만으로 사고팔기 때문(내 팀 소속 AI 동료도
    # 예외 없음) — 아래 그룹별 스냅샷은 이 편향을 잡아내기 위한 자료.
    roster_by_team: dict = {}
    for r in c.execute("SELECT id, team_id, position, ovr FROM ai_players").fetchall():
        roster_by_team.setdefault(r["team_id"], []).append((r["id"], r["position"], r["ovr"]))

    name_cache = _build_name_cache(c)
    topped_up = 0
    forced_out = 0
    new_rows = []       # INSERT용
    delete_ids = []     # DELETE용

    for tid, (tier, cname, continent, tname) in team_info.items():
        n = counts.get(tid, 0)
        grade = get_country_league_grade(cname)
        bonus = round(CONTINENT_OVR_BONUS.get(continent, 0) + COUNTRY_OVR_ADJ.get(cname, 0))
        is_override = cname in COUNTRY_LEAGUE_OVR_OVERRIDE

        if n < _SQUAD_MIN:
            need = _SQUAD_MIN - n
            ovr_rng = get_ovr_range(grade, tier, cname)
            if ovr_rng:
                lo, hi = ovr_rng
                if not is_override:
                    lo, hi = lo + bonus, hi + bonus
            else:
                lo, hi = 40, 55
            _plvl = _rebal_prestige_level(cname, tname)
            used = set()
            _q_lo, quota = get_foreign_quota_range(cname, continent)
            foreign_ct = 0
            for _ in range(need):
                # [2026-08 버그수정, 신민용 리포트: "지금 팀 후보 포지션
                # 비율이 이상하게 됐다(키퍼 3, 수비 3, 미드 2, 공격 5)"]
                # 예전엔 여기서 TEAM_POSITIONS(주전11+옛 고정벤치12 통짜
                # 리스트)를 균등 추첨했는데, 이 리스트의 그룹 비중(GK≈13%
                # /DF≈35%/MF≈26%/FW≈26%)이 database._build_squad_positions
                # (팀 최초 생성)가 목표로 하는 벤치 비율(GK 5~10%/DF
                # 30~35%/MF 35~40%/FW 20~25%)과 전혀 달랐다 — 이적으로
                # 얇아진 팀을 매 시즌 이 함수로 보충할 때마다 그 낡은
                # 비중 쪽으로 스쿼드가 계속 다시 끌려가, 수십 시즌이
                # 지나면 처음 생성 비율이 완전히 무너져 있었다. 이제 최초
                # 생성과 똑같은 roll_bench_position()을 써서 두 경로가
                # 항상 같은 목표 비율로 수렴하게 한다.
                pos = roll_bench_position()
                target = random.randint(lo, max(lo, (lo + hi) // 2))
                age = random.randint(*_AI_NEWBIE_AGE)
                # [2026-08 버그수정, _youth_target_scale 주석 참고] 이 경로도
                # 신인 생성인데 나이 스케일링이 빠져 있었다 — _retire_and_replace와
                # 동일하게 나이를 먼저 뽑아 target에 반영한다.
                _scaled = _youth_target_scale(target, age)
                # [2026-08 재설계 — _retire_and_replace와 동일한
                # Prestige×리그등급 표.]
                if ovr_rng:
                    _prestige_base = {3: 1, 2: 2, 1: 3}.get(_plvl, 4)
                    _grade_adj = {"SS": 0, "S": 0, "A": 0, "B": 1, "C": 1,
                                 "D": 2, "E": 2, "F": 3}.get(grade, 2)
                    _young_floor_off = _prestige_base + _grade_adj
                    _scaled = max(_scaled, ovr_rng[0] - _young_floor_off)
                stats = _gen_stats(pos, _scaled)
                ovr = calc_ovr(pos, stats)
                sub_role = random.choice(SUB_ROLES.get(pos, ["기본"]))
                nat, foreign_ct = _pick_nationality(cname, continent, grade, pos,
                                                    False, foreign_ct, quota)
                name = _random_name(c, tid, name_cache, used_in_team=used)
                new_rows.append((tid, name, pos,
                    stats["stamina"], stats["speed"], stats["jump"], stats["strength"],
                    stats["shooting"], stats["passing"], stats["dribbling"],
                    stats["tackling"], stats["heading"], stats["positioning"],
                    stats["setpiece"], stats["mental"], stats["confidence"],
                    stats["leadership"], stats["concentration"], ovr, age, sub_role, nat,
                    year + random.randint(2, 4), 0, year))
                topped_up += 1

        elif n > _SQUAD_MAX:
            excess = n - _SQUAD_MAX
            # [2026-08 신설, 신민용 요청: "강제 조기은퇴도 이적 가드와
            # 같은 문제(마지막 GK/DF 등이 최저OVR이면 그냥 잘려서 그
            # 그룹이 0명이 됨)를 가진다 — 최저OVR 우선순위는 그대로
            # 두고, '이 선수를 자르면 그 포지션 그룹이 0명이 되는가'만
            # 추가로 걸러라"] 위 이적 가드(_do_one_transfer_cached)와
            # 완전히 동일한 원칙: 정렬 기준(최저 OVR 우선)은 손대지
            # 않고, 후보 목록에서 "그 그룹의 마지막 1명"만 건너뛴다.
            # roster_by_team은 이 함수 진입 시점(=은퇴/이적이 이미 끝난
            # 뒤) 1회 조회한 스냅샷이라 지금 이 팀의 실제 구성과 일치한다.
            roster = roster_by_team.get(tid, [])
            _grp_count_max: dict = {}
            for _pid, _ppos, _povr in roster:
                _g = _GROUP_KEY.get(_pos_category(_ppos), "MF")
                _grp_count_max[_g] = _grp_count_max.get(_g, 0) + 1
            _protected_max = {_pid for _pid, _ppos, _povr in roster
                              if _grp_count_max.get(_GROUP_KEY.get(_pos_category(_ppos), "MF"), 0) <= 1}
            _candidates = sorted(roster, key=lambda t: t[2])  # 기존과 동일: OVR 오름차순
            picks = [_pid for _pid, _ppos, _povr in _candidates if _pid not in _protected_max][:excess]
            # (극단적 예외) 보호 대상을 뺀 후보만으론 목표 감축분을 못
            # 채우면(팀 전체가 그룹당 1명씩에 가까운 경우) 나머지는 기존
            # 방식대로 보호 대상에서도 채운다 — 스쿼드가 영구히 과다한
            # 상태로 남는 것보다는 이 편이 낫다(신민용 원안의 "매우 드문
            # 극단 예외" 취급과 동일한 원칙).
            if len(picks) < excess:
                _picked = set(picks)
                _rest = [_pid for _pid, _ppos, _povr in _candidates if _pid not in _picked]
                picks.extend(_rest[:excess - len(picks)])
            delete_ids.extend(picks)
            forced_out += len(picks)

        else:
            # [2026-08 신설] 총원은 22~25 정상범위라서 위 두 분기 다
            # 발동을 안 하는 팀들 — 그런데 총원이 정상이어도 그 안의
            # 포지션 그룹 구성비는 이적 편향으로 심하게 틀어져 있을 수
            # 있다(신민용 리포트 사례: 25명인데 GK 5/DF 0). 여기서는
            # 총원을 그대로 유지한 채(스왑: 가장 넘치는 그룹 최저OVR
            # 1명을 빼고 가장 부족한 그룹에 1명을 채움) _BENCH_GROUP_
            # WEIGHTS(위 database.py의 벤치 목표 비율과 동일 기준) 대비
            # "명백히 비정상"인 선(0명이거나 기대치의 40% 미만 = 부족,
            # 기대치의 2.2배 이상 = 과다)에서만 발동해서, 정상적인
            # 통계적 편차까지 억지로 깎아내리진 않는다.
            roster = roster_by_team.get(tid, [])
            if roster:
                group_players: dict = {"GK": [], "DF": [], "MF": [], "FW": []}
                for pid, ppos, povr in roster:
                    grp = _GROUP_KEY.get(_pos_category(ppos), "MF")
                    group_players[grp].append((pid, povr))
                total_n = len(roster)
                deficient, surplus = [], []
                for grp, w in _BENCH_GROUP_WEIGHTS:
                    expected = total_n * (w / 100.0)
                    actual = len(group_players[grp])
                    if actual == 0 or actual < expected * 0.4:
                        deficient.append(grp)
                    elif actual > max(expected * 2.2, expected + 3):
                        surplus.append((grp, actual - expected))
                if deficient and surplus:
                    surplus.sort(key=lambda x: -x[1])
                    ovr_rng = get_ovr_range(grade, tier, cname)
                    if ovr_rng:
                        _lo, _hi = ovr_rng
                        if not is_override:
                            _lo, _hi = _lo + bonus, _hi + bonus
                    else:
                        _lo, _hi = 40, 55
                    _plvl = _rebal_prestige_level(cname, tname)
                    _used = set()
                    _q_lo, _quota = get_foreign_quota_range(cname, continent)
                    _foreign_ct = 0
                    for si, grp in enumerate(deficient):
                        if si >= len(surplus):
                            break
                        sgrp, _ = surplus[si]
                        weakest = min(group_players[sgrp], key=lambda t: t[1])
                        delete_ids.append(weakest[0])
                        group_players[sgrp].remove(weakest)
                        _pos = random.choice(_BENCH_GROUP_POOLS[grp])
                        _target = random.randint(_lo, max(_lo, (_lo + _hi) // 2))
                        _age = random.randint(*_AI_NEWBIE_AGE)
                        _scaled = _youth_target_scale(_target, _age)
                        if ovr_rng:
                            _prestige_base = {3: 1, 2: 2, 1: 3}.get(_plvl, 4)
                            _grade_adj = {"SS": 0, "S": 0, "A": 0, "B": 1, "C": 1,
                                         "D": 2, "E": 2, "F": 3}.get(grade, 2)
                            _young_floor_off = _prestige_base + _grade_adj
                            _scaled = max(_scaled, ovr_rng[0] - _young_floor_off)
                        _stats = _gen_stats(_pos, _scaled)
                        _ovr = calc_ovr(_pos, _stats)
                        _sub_role = random.choice(SUB_ROLES.get(_pos, ["기본"]))
                        _nat, _foreign_ct = _pick_nationality(cname, continent, grade, _pos,
                                                              False, _foreign_ct, _quota)
                        _name = _random_name(c, tid, name_cache, used_in_team=_used)
                        new_rows.append((tid, _name, _pos,
                            _stats["stamina"], _stats["speed"], _stats["jump"], _stats["strength"],
                            _stats["shooting"], _stats["passing"], _stats["dribbling"],
                            _stats["tackling"], _stats["heading"], _stats["positioning"],
                            _stats["setpiece"], _stats["mental"], _stats["confidence"],
                            _stats["leadership"], _stats["concentration"], _ovr, _age, _sub_role, _nat,
                            year + random.randint(2, 4), 0, year))
                        topped_up += 1
                        forced_out += 1

    if new_rows:
        c.executemany("""INSERT INTO ai_players
            (team_id,name,position,stamina,speed,jump,strength,shooting,passing,
             dribbling,tackling,heading,positioning,setpiece,
             mental,confidence,leadership,concentration,ovr,age,sub_role,nationality,
             contract_end_year,last_transfer_year,created_year)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new_rows)
    if delete_ids:
        _archive_forced_out_players(c, delete_ids, year)
        c.executemany("DELETE FROM ai_players WHERE id=?", [(i,) for i in delete_ids])

    return topped_up, forced_out


# ─────────────────────────────────────────────
# 5. 포메이션 변경 (감독 교체 컨셉)
# ─────────────────────────────────────────────
# [2026-09 신설] hist.team_season_lineup에 my_player를 담을 때 쓰는 예약 id.
# world_browser.MY_PLAYER_ID와 같은 값이어야 한다 — 화면 쪽(선수 검색 상세,
# 포메이션 클릭, 이름 일괄변경 제외 규칙)이 전부 이 값을 기준으로 동작하기
# 때문. world_browser를 import하지 않는 이유는 순환 참조 방지(그쪽이
# ai_lifecycle을 다시 끌어옴) — 값이 바뀔 일이 없는 예약 상수라 양쪽에
# 같은 값을 두고 주석으로 묶어둔다.
_MY_LINEUP_ID = -1


def _snapshot_season_positions(c, year, only_missing=False, rows=None):
    """[2026-08 신설, 신민용 요청: "이 시즌에 얘가 어디 포지션을 갔는지가
    중요한거야 — 위(선수 검색 맨 위 요약행)는 주포라 안 변하는 게
    맞는데, 연도별 기록엔 그 시즌 실제로 어느 자리서 뛰었는지가 있어야
    한다"] 등록 포지션(ai_players.position, 안 바뀌는 "주포")과 별개로,
    이 시즌 각 팀의 실제 포메이션(teams.formation)에 로스터를 채워 넣었을
    때 이 선수가 어느 슬롯을 맡는지를 매 시즌 스냅샷으로 남긴다.

    화면(포메이션 탭)에 뜨는 것과 다른 알고리즘을 쓰면 "선수 검색은
    CB라는데 포메이션 화면은 LB"처럼 또 다른 불일치가 생기므로,
    formation_logic._greedy_fill_slots(여러 후보를 슬롯에 배정하는 바로
    그 함수 — ui/formation_widget.py도 동일 모듈에서 가져다 쓴다)를
    그대로 재사용한다 — OVR 상위 11명(베스트 XI)에 든 선수는 그 슬롯
    포지션을, 나머지(후보) 선수는 등록 포지션 그대로 기록한다(이
    게임엔 후보용 별도 포메이션 개념이 없으므로).

    [주의] ui.formation_widget에서 직접 import하지 않는다 — 그 모듈은
    PyQt6을 import하므로, headless_runner.py 등 PyQt6 없는 헤드리스
    환경에서 ai_lifecycle.py를 그냥 import하는 것만으로 죽는다.
    formation_logic.py(Qt 의존성 없는 순수 로직 전용)에서 가져온다.

    ai_player_ovr_history와 완전히 같은 타이밍(매 시즌 전환)에 호출된다.
    [한계] 이 기능 신설 이전 과거 시즌엔 소급 적용이 안 된다 — 그 이전
    연도는 세계 브라우저 쪽에서 이적 시점 등록 포지션으로 대체 표시한다."""
    from formation_logic import _greedy_fill_slots, compute_squad_roles
    from constants import FORMATION_SLOTS
    import json

    # [2026-08 확장, 신민용 요청: "그 해 주전/로테이션/대기/유망주였는지도
    # 연도별로 표시"] 역할 계산(formation_logic.compute_squad_roles)이
    # 나이도 필요해서 age를 같이 뽑는다 — 이 함수가 이미 팀별 로스터
    # 전체를 훑고 있으므로(베스트XI 슬롯 배정용) 추가 쿼리 없이 그대로
    # 재사용한다.
    # [2026-08 신설, 신민용 리포트: "은퇴 선수의 마지막 시즌 역할이 -로
    # 뜬다"] 이 함수는 원래 시즌 전환의 맨 끝(은퇴·이적·포메이션 변경이
    # 전부 끝난 뒤)에 딱 한 번만 돌았다 — 그런데 그 시점엔 이번 시즌을
    # 마지막으로 은퇴한 선수가 ai_players에서 이미 삭제된 뒤라, 그
    # 선수의 마지막 시즌만 이 표에 행이 아예 안 생겼다(그래서 화면에
    # 역할이 "-"로 떴다. OVR/포지션은 ai_player_ovr_history 쪽에서
    # 나오므로 그 줄 자체는 정상적으로 보였고 역할 칸만 비었던 것).
    # 이제 두 번 나눠 부른다:
    #   1) 은퇴·이적 처리 "전"에 한 번 (only_missing=False) — 이번 시즌을
    #      실제로 뛴 로스터 그대로가 남는다. 은퇴자도 아직 살아 있고,
    #      오프시즌 이적자도 아직 옛 팀 소속이라 연도 귀속이 정확해진다.
    #   2) 전부 끝난 뒤 한 번 더 (only_missing=True) — 이번 오프시즌에
    #      새로 생긴 선수(은퇴 대체 신인 등)만 채운다. 이 선수들은
    #      ai_player_ovr_history에도 이번 해로 기록되므로(데뷔연도
    #      archive), 여기서도 같이 채워야 화면에 역할 칸만 비지 않는다.
    # only_missing=True일 땐 아직 이 해 행이 없는 선수가 있는 팀만
    # 훑는다 — 역할(팀 내 OVR 순위)은 그 팀 로스터 전체가 있어야
    # 계산되므로 팀 단위로 가져오되, 실제로 저장하는 건 빠져 있던
    # 선수 행뿐이라 이미 1)에서 기록된 값은 절대 덮어쓰지 않는다.
    _missing_ids = None
    if only_missing:
        _missing = c.execute(
            """SELECT ap.id, ap.team_id FROM ai_players ap
               WHERE ap.team_id IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM hist.ai_player_position_history h
                                 WHERE h.player_id = ap.id AND h.year = ?)""",
            (year,)).fetchall()
        if not _missing:
            return
        _missing_ids = {r[0] for r in _missing}
        # 대상 팀만 골라 오되, 팀 id를 SQL 문자열에 몇천 개씩 나열하면
        # (IN (...)) 그 구문을 만들고 파싱하는 것만으로도 느려진다 —
        # 임시표에 넣고 JOIN으로 좁힌다. 임시표는 이 연결에서만 보이며
        # 끝나고 바로 지운다.
        c.execute("DROP TABLE IF EXISTS temp._snap_target_teams")
        c.execute("CREATE TEMP TABLE _snap_target_teams(team_id INTEGER PRIMARY KEY)")
        c.executemany("INSERT OR IGNORE INTO temp._snap_target_teams(team_id) VALUES(?)",
                      [(r[1],) for r in _missing])
        rows = c.execute(
            """SELECT ap.id AS id, ap.team_id AS team_id, ap.position AS position,
                      ap.ovr AS ovr, ap.age AS age, t.formation AS formation
               FROM ai_players ap
               JOIN temp._snap_target_teams st ON st.team_id = ap.team_id
               JOIN teams t ON ap.team_id = t.id""").fetchall()
        c.execute("DROP TABLE IF EXISTS temp._snap_target_teams")
    elif rows is None:
        rows = c.execute(
            """SELECT ap.id AS id, ap.team_id AS team_id, ap.position AS position,
                      ap.ovr AS ovr, ap.age AS age, t.formation AS formation
               FROM ai_players ap JOIN teams t ON ap.team_id = t.id
               WHERE ap.team_id IS NOT NULL""").fetchall()
    if not rows:
        return

    # [2026-08 최적화] 호출부가 이미 떠 놓은 선수 목록(rows)을 넘겨주면
    # 26만 행을 다시 JOIN해서 읽지 않는다 — 대신 그 목록엔 팀 포메이션이
    # 없으므로 teams(1만여 행, 훨씬 쌈)만 따로 읽어 팀→포메이션 표를
    # 만들어 쓴다. 결과는 JOIN해서 읽었을 때와 동일.
    _form_by_team = None
    if not only_missing and "formation" not in rows[0].keys():
        _form_by_team = {r[0]: r[1] for r in
                         c.execute("SELECT id, formation FROM teams").fetchall()}

    by_team = {}
    for r in rows:
        _tid = r["team_id"]
        if _tid is None:
            continue   # 무소속(rows를 넘겨받은 경로엔 섞여 있을 수 있음)
        by_team.setdefault(_tid, []).append(r)

    # [2026-09 버그수정, 신민용 리포트: "팀 검색에서 팀을 누르고 연도를
    # 누르면 내 팀이 뜨는데, 거기 플레이어가 들어가 있으면 플레이어가
    # 떠야 하는데 안 뜬다"] 원인: 이 함수는 ai_players만 훑고 my_player는
    # 아예 조회하지 않는다 — 그래서 아래 team_season_lineup(팀 검색
    # 연도별 스쿼드 카드가 그대로 읽는 표)에도 내 선수만 통째로 빠져
    # 있었다. 예전엔 이 함수를 안 건드리려고 snapshot_my_player_position
    # (my_player 소속팀 하나만 targeted 조회)을 따로 뒀는데, 그 함수는
    # my_player_position_history(내 포지션/역할)만 저장할 뿐 팀 스쿼드
    # 자체는 손대지 않았고, 애초에 오프시즌이 다 끝난 뒤(이적 반영 후)
    # 실행되므로 "이번 시즌을 실제로 뛴 로스터"를 기준으로 팀 라인업을
    # 다시 쓰면 오히려 다른 팀들과 기준이 어긋난다. 그래서 팀 스쿼드
    # 스냅샷만큼은 여기(정확한 타이밍)에서 같이 처리한다.
    #
    # id는 world_browser.MY_PLAYER_ID(-1)를 그대로 쓴다 — ai_players.id는
    # 항상 1 이상의 autoincrement라 충돌하지 않고, 선수 검색/포메이션
    # 클릭(open_to_player)/이름 일괄변경(id>=0만 대상) 등 화면 쪽이 이미
    # 이 예약값을 전부 알고 있어서 표시·클릭이 그대로 동작한다.
    # ai_player_position_history 쪽에는 절대 안 넣는다(내 포지션/역할은
    # my_player_position_history 전용 — snapshot_my_player_position 담당).
    _me = None
    try:
        _me_row = c.execute(
            "SELECT current_team_id, position, ovr, age FROM my_player WHERE id=1").fetchone()
        if _me_row and _me_row["current_team_id"]:
            _me = _me_row
    except Exception:
        _me = None   # my_player 표가 아직 없는 극초기/테스트 경로 방어

    inserts = []
    # [2026-08 신설, 신민용 요청: "팀 검색에서 연도를 클릭하면 그 해
    # 포메이션이 떠야 한다"] 아래 루프가 팀마다 어차피 계산하는 placed
    # (슬롯별 베스트11 배정)를 선수 단위(inserts)로 흩어 담기 직전에,
    # 팀 단위로도 그대로 한 벌 더 챙겨둔다 — 새 연산이 아니라 이미 계산된
    # 결과를 한 번 더 저장하는 것뿐이라 비용이 거의 없다.
    # [2026-09 버그수정, 신민용 리포트: "팀 검색 포메이션이랑 선수 검색
    # 소속팀 기록이 안 맞을 때가 있다 — 팀 A 연도 포메이션에 뜬 선수를
    # 눌러보면 선수 검색에선 그 해 팀 B 소속이라고 나온다"] 예전 주석은
    # "only_missing=True 두 번째 패스가 다시 채워도 INSERT OR REPLACE라
    # 나중(더 확정된) 값이 이겨서 오히려 더 정확해진다"고 판단했는데 —
    # 틀렸다. 그 두 번째 패스는 이적시장(_transfer_market)·은퇴대체·
    # 스쿼드보정이 전부 끝난 "뒤"에 돈다(호출부 순서 참고) — 즉 그 시점의
    # ap.team_id는 "이번 시즌을 실제로 뛴 로스터"가 아니라 이미 다음 해부터
    # 발효되는 오프시즌 이적까지 반영된 "확정된 다음 시즌 로스터"다.
    # only_missing=True는 원래 "이번 오프시즌에 새로 생긴 신인의 개인
    # 포지션 기록만 보충"하려는 목적이었는데(아래 _missing_ids 필터가
    # inserts엔 실제로 적용됨), team_inserts(팀 단위 포메이션 스냅샷)엔
    # 그 필터가 없어서 신인이 하나라도 낀 팀 전체 로스터가 통째로
    # 재계산되어 1차 패스(정확한 값)를 덮어썼다 — 실측 검증(헤드리스
    # 3시즌 76만 건 대조) 결과 25%가 이렇게 오염됨을 확인. 신인 한 명이
    # 있는 팀은 대부분(사실상 거의 모든 팀, 매 시즌 은퇴대체/스쿼드보정으로
    # 신인이 생기므로)이라 사실상 전세계 팀이 이 오염에 노출돼 있었다.
    # 신인의 개인 포지션 기록(ai_player_position_history)엔 애초에 team_id가
    # 없으므로 team_inserts는 이 두 번째 패스에서 아예 만들 필요가 없다 —
    # 1차 패스가 이미 그 해 모든 팀의 정확한 스냅샷을 남겼다.
    team_inserts = []
    for _team_id, players in by_team.items():
        if _form_by_team is not None:
            formation = _form_by_team.get(_team_id) or "4-4-2"
        else:
            formation = players[0]["formation"] or "4-4-2"
        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        candidates = [{"id": p["id"], "position": p["position"], "ovr": p["ovr"] or 0}
                      for p in players]
        role_pool = [(p["id"], p["ovr"], p["age"]) for p in players]
        # [2026-09 버그수정] 내 소속팀이면 나도 로스터의 일원으로 같이
        # 슬롯 배정/역할 산정에 넣는다(위 _me 주석 참고) — 그래야 팀
        # 스쿼드 카드에 내가 뜨고, "내가 주전인데 팀 라인업엔 AI가 그
        # 자리에 있다"는 불일치도 사라진다.
        if _me is not None and _team_id == _me["current_team_id"]:
            candidates.append({"id": _MY_LINEUP_ID, "position": _me["position"],
                               "ovr": _me["ovr"] or 0})
            role_pool.append((_MY_LINEUP_ID, _me["ovr"], _me["age"]))
        placed = _greedy_fill_slots(candidates, slots)
        roles = compute_squad_roles(role_pool)
        started_ids = set()
        for slot_idx, pl in enumerate(placed):
            if pl is None:
                continue
            started_ids.add(pl["id"])
            if pl["id"] == _MY_LINEUP_ID:
                continue   # 내 포지션/역할은 my_player_position_history 담당
            inserts.append((pl["id"], year, slots[slot_idx], roles.get(pl["id"], "")))
        for p in players:
            if p["id"] not in started_ids:
                inserts.append((p["id"], year, p["position"] or "", roles.get(p["id"], "")))
        slots_payload = [{"slot": slots[i], "id": (pl["id"] if pl else None)}
                          for i, pl in enumerate(placed)]
        # [2026-08 신설, 신민용 리포트: "팀도 주전 후보가 있는데 왜 안떠?"]
        # 포메이션 11자리에 못 들어간 나머지 로스터(=후보)도 OVR 내림차순으로
        # 같이 저장해둔다 — 국가대표 스쿼드 화면(get_country_tournament_squad)의
        # 주전/후보 패턴과 동일하게 맞추기 위함. 새 연산 없이 이미 위에서 구한
        # started_ids/players를 그대로 재사용.
        # [2026-09 버그수정] players(ai_players만) 대신 candidates(나 포함)를
        # 쓴다 — 원소 순서와 ovr 값이 players와 1:1로 같으므로 안정 정렬
        # 결과도 기존과 동일하고, 내가 주전에 못 들었을 때만 후보 목록에
        # 자연스럽게 합류한다.
        bench_payload = [{"id": p["id"], "position": p["position"] or ""}
                          for p in sorted(
                              (p for p in candidates if p["id"] not in started_ids),
                              key=lambda p: -(p["ovr"] or 0))]
        # [2026-09 버그수정] only_missing=True(신인 보충 2차 패스)에서는
        # team_inserts를 만들지 않는다 — 위 주석 참고, 이 시점의 로스터는
        # "그 해 실제로 뛴 팀"이 아니라 이미 이적이 반영된 확정 로스터라
        # 팀 포메이션 스냅샷 용도로 쓰면 안 된다.
        if _missing_ids is None:
            team_inserts.append((_team_id, year, formation,
                                  json.dumps(slots_payload), json.dumps(bench_payload)))

    if _missing_ids is not None:
        # 팀 로스터 전체로 슬롯·역할을 계산했지만, 실제로 저장하는 건
        # 이 해 행이 없던 선수(이번 오프시즌 신규 생성)뿐이다.
        inserts = [t for t in inserts if t[0] in _missing_ids]

    if team_inserts:
        c.executemany(
            "INSERT OR REPLACE INTO hist.team_season_lineup"
            "(team_id, year, formation, slots_json, bench_json) "
            "VALUES (?,?,?,?,?)", team_inserts)

    if inserts:
        # [2026-08 최적화] player_id 순으로 정렬해서 넣는다. 이 표의 기본키는
        # (player_id, year)이고 WITHOUT ROWID라 키 순서가 곧 저장 순서인데,
        # 위 루프는 "팀별"로 돌기 때문에 player_id가 뒤죽박죽인 채로 26만 건이
        # 들어갔다 — B-tree 입장에서는 매번 다른 페이지를 열어 중간에 끼워넣는
        # 셈이라 페이지 분할이 계속 일어난다. 키 순으로 넣으면 뒤쪽에 차곡차곡
        # 붙기만 하면 된다. 정렬은 안정 정렬이고 (player_id, year)가 이 목록
        # 안에서 유일하므로(선수 한 명당 이 해에 한 행) 저장 결과는 완전히 동일.
        inserts.sort(key=_ins_key)
        c.executemany(
            "INSERT OR REPLACE INTO hist.ai_player_position_history(player_id, year, position, role) "
            "VALUES (?,?,?,?)", inserts)


def _snapshot_team_lineup_half(c, year):
    """[2026-09 신설, 신민용 요청: "시즌 중 이적한 경우 상반기엔 있었지만
    하반기엔 없는 선수가 팀 검색 포메이션에서 아예 안 보인다 — 팀 검색을
    열면 상반기/하반기 포메이션을 버튼으로 나눠서 보여줘야 한다"]

    game_engine._advance_week가 하반기 시작 주차(SECOND_HALF_START)에
    진입해 겨울 이적시장(ai_lifecycle.run_ai_mid_season_transfer)을 열기
    "직전"에 호출된다 — 이 순간의 ap.team_id는 아직 이번 겨울 이적이
    반영되기 전이므로 "상반기까지 실제로 뛴 팀"이다. _snapshot_season_
    positions의 팀 단위 슬롯 배정과 완전히 같은 알고리즘(_greedy_fill_
    slots, formation_logic.py)을 재사용해 화면(포메이션 탭)과 어긋나지
    않게 한다 — 역할(주전/로테이션 등)은 이 표에서 안 쓰므로 계산하지
    않는다.

    hist.team_season_lineup_half(이 함수 전용)에 저장 — 기존 hist.
    team_season_lineup(시즌 끝, 오프시즌 이적 "전" 스냅샷 — 상반기 이적은
    이미 반영된 뒤라 사실상 "하반기" 로스터, database.py 주석 참고)과는
    별개 표라 기존 화면·로직엔 전혀 영향이 없다.

    [한계] 이 기능 신설 이전 과거 시즌은 소급 적용 안 됨 — 그 해는
    화면에서 "상반기 기록 없음"으로 처리한다."""
    from formation_logic import _greedy_fill_slots
    from constants import FORMATION_SLOTS
    import json

    rows = c.execute(
        """SELECT ap.id AS id, ap.team_id AS team_id, ap.position AS position,
                  ap.ovr AS ovr, t.formation AS formation
           FROM ai_players ap JOIN teams t ON ap.team_id = t.id
           WHERE ap.team_id IS NOT NULL""").fetchall()
    if not rows:
        return

    by_team = {}
    for r in rows:
        by_team.setdefault(r["team_id"], []).append(r)

    # [내 선수도 포함] _snapshot_season_positions와 동일한 이유 —
    # 그 주석 참고. 내가 상반기에 이 팀 소속이었으면 상반기 포메이션에도
    # 같이 떠야 한다.
    _me = None
    try:
        _me_row = c.execute(
            "SELECT current_team_id, position, ovr FROM my_player WHERE id=1").fetchone()
        if _me_row and _me_row["current_team_id"]:
            _me = _me_row
    except Exception:
        _me = None

    team_inserts = []
    for _team_id, players in by_team.items():
        formation = players[0]["formation"] or "4-4-2"
        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        candidates = [{"id": p["id"], "position": p["position"], "ovr": p["ovr"] or 0}
                      for p in players]
        if _me is not None and _team_id == _me["current_team_id"]:
            candidates.append({"id": _MY_LINEUP_ID, "position": _me["position"],
                               "ovr": _me["ovr"] or 0})
        placed = _greedy_fill_slots(candidates, slots)
        started_ids = {pl["id"] for pl in placed if pl is not None}
        slots_payload = [{"slot": slots[i], "id": (pl["id"] if pl else None)}
                          for i, pl in enumerate(placed)]
        bench_payload = [{"id": p["id"], "position": p["position"] or ""}
                          for p in sorted(
                              (p for p in candidates if p["id"] not in started_ids),
                              key=lambda p: -(p["ovr"] or 0))]
        team_inserts.append((_team_id, year, formation,
                              json.dumps(slots_payload), json.dumps(bench_payload)))

    if team_inserts:
        c.executemany(
            "INSERT OR REPLACE INTO hist.team_season_lineup_half"
            "(team_id, year, formation, slots_json, bench_json) "
            "VALUES (?,?,?,?,?)", team_inserts)


def _snapshot_season_ratings(c, year, team_goals_for=None):
    """[2026-08 신설, 신민용 요청: "세계 축구 기록실 연도별 기록 밑에
    그 해 평균 평점/골/도움 요약을 얇은 행으로 하나 더 보여달라"]

    AI 선수는 개별 경기를 실제로 시뮬레이션하지 않는다(세계 전역 수십만
    명을 매 경기 계산하는 건 불가능 — match_sim.tactical_engine은 오직
    '내 리그 경기' 하나만 이렇게 정교하게 돈다). 대신 game_engine.
    _estimate_ai_season(포지션/OVR/팀 강도 기반 통계 추정 — 베스트11·
    발롱도르 후보 산정에도 이미 쓰이는 그 공식)을 이번 시즌을 마친 로스터
    기준으로 한 번씩 돌려 그 결과를 hist.ai_player_season_stats에
    archive한다.

    _snapshot_season_positions와 완전히 같은 타이밍(은퇴/이적으로 로스터가
    바뀌기 전, run_ai_offseason 맨 앞)에 호출해야 team_id가 "이번 시즌을
    실제로 마친 팀"이 된다 — _collect_league_candidates가 매번 새로
    구하는 team_avg/league_avg/리그 풀시즌 경기수를 여기서는 전세계
    팀·리그를 한 번의 그룹핑으로 미리 계산해 재사용한다(팀/리그 수가
    커도 추가 쿼리 없이 단일 스캔으로 처리).

    [한계] ai_player_ovr_history와 동일 — 이 기능 신설 이후 시즌만
    정확하고, 그 이전 과거 시즌은 소급 적용이 안 된다. 시즌 중 이적한
    선수는 여기엔 "그 해를 마무리한(하반기) 팀" 기준 풀시즌 추정치
    하나만 저장되고, 상반기 팀 몫은 world_browser.get_ai_player_career_
    history가 조회 시점에 경기수 비율로 쪼개 근사한다.

    [2026-09 확장, 신민용 요청: "리그/국내컵/클럽대항전/슈퍼컵/클럽월드컵
    다 평점·골·어시를 다르게 둬야 하는데 그게 안 되어 있다"] 위 리그
    추정치에 이어서, 같은 team_avg/league_avg를 재사용해 국내컵/클럽
    대항전(CL·EL·ECL 통합)/슈퍼컵/클럽월드컵 4개 대회도 각각 별도
    추정해 hist.ai_player_season_stats_by_comp에 담는다 — full_season_
    matches만 그 대회에서 그 팀이 실제로 이번 시즌 뛴 경기수(cup_matches/
    cl·el·ecl_matches/sc_matches/cwc_matches를 팀별로 세어서 얻은 실측값,
    world_browser.py가 "국내컵 4강 탈락" 같은 진출기록 문구를 만들 때
    쓰는 것과 동일한 원본 데이터)로 바꿔서 같은 _estimate_ai_season
    공식에 넣는다. 그 팀이 그 해 그 대회에 아예 안 나갔으면(경기수 0)
    그 팀 선수들은 그 대회 행 자체가 안 생긴다."""
    from game_engine import (_estimate_ai_season, _estimate_ai_clean_sheets, _estimate_ai_gk_saves,
                              _team_goal_scale_factors, _apply_squad_depth_decay,
                              _apply_ace_concentration, _apply_team_goal_budget)
    from constants import get_goal_env_mult

    _raw_rows = c.execute(
        """SELECT ap.id AS id, ap.position AS position, ap.ovr AS ovr,
                  ap.sub_role AS sub_role, ap.team_id AS team_id, t.league_id AS league_id
           FROM ai_players ap JOIN teams t ON ap.team_id = t.id
           WHERE ap.team_id IS NOT NULL""").fetchall()
    if not _raw_rows:
        return
    # [2026-09 성능] sqlite3.Row를 문자열 키로 인덱싱하는 건 컬럼 이름
    # 목록을 매번 훑는 C 레벨 선형탐색이다. 이 함수는 26만 행을 리그 1회 +
    # 대회 5회로 반복해서 도므로 그 조회만 수백만 회가 된다 — 조회 직후
    # 한 번만 평탄한 튜플로 접어두고 이후 전 구간이 위치 인덱싱만 쓴다.
    rows = [(r["id"], r["position"], r["ovr"] or 0, r["sub_role"],
             r["team_id"], r["league_id"]) for r in _raw_rows]
    del _raw_rows

    # 팀별 평균 OVR, 리그별 평균 OVR, 리그별 소속 팀 집합(풀시즌 경기수
    # 계산용) — 전세계 선수를 한 번만 훑어서 세 집계를 동시에 만든다.
    # [2026-09 성능] 같은 패스에서 rows_by_team(팀 → 그 팀 선수의 행
    # 인덱스)도 만들어둔다 — 아래 대회별 블록이 "그 대회 참가팀 선수만"
    # 훑을 때 쓴다.
    team_ovr_sum, team_ovr_n = {}, {}
    league_ovr_sum, league_ovr_n = {}, {}
    league_teams = {}
    rows_by_team = {}
    for _i, (_pid, _pos, ovr, _sub, tid, lid) in enumerate(rows):
        team_ovr_sum[tid] = team_ovr_sum.get(tid, 0) + ovr
        team_ovr_n[tid] = team_ovr_n.get(tid, 0) + 1
        league_ovr_sum[lid] = league_ovr_sum.get(lid, 0) + ovr
        league_ovr_n[lid] = league_ovr_n.get(lid, 0) + 1
        league_teams.setdefault(lid, set()).add(tid)
        rows_by_team.setdefault(tid, []).append(_i)

    team_avg = {tid: team_ovr_sum[tid] / team_ovr_n[tid] for tid in team_ovr_sum}
    league_avg = {lid: league_ovr_sum[lid] / league_ovr_n[lid] for lid in league_ovr_sum}
    # game_engine._league_full_season_matches와 동일 공식(팀 수-1 × 다전제).
    league_matches = {}
    for lid, tids in league_teams.items():
        n = len(tids)
        league_matches[lid] = max(1, (n - 1) * legs_for_team_count(n))

    # [2026-09 신설, 신민용 요청: "국가별로 리그 득점 계수를 하나 두는 게
    # 좋다"] 리그별 국가 득점 환경 배율 — 전세계 리그를 한 번에 조회해
    # lid -> 배율 딕셔너리로 미리 만들어둔다(_estimate_ai_season 호출부가
    # 리그당 반복해서 조회할 필요 없게).
    _league_country = {r["lid"]: r["country"] for r in c.execute(
        """SELECT l.id AS lid, cn.name AS country FROM leagues l
           JOIN countries cn ON l.country_id = cn.id""").fetchall()}
    league_goal_mult = {lid: get_goal_env_mult(_league_country.get(lid))
                         for lid in league_teams}

    # [2026-09 신설, "Tier B" 실제 골 합계 보정] 위 _estimate_ai_season는
    # 선수 개개인을 OVR 기반으로 독립 추정하므로, 한 팀 전원의 추정 골을
    # 더해도 그 팀이 이번 시즌 실제로 넣은 골(team_goals_for, 시즌 종료
    # 직전 teams.goals_for 스냅샷)과 우연히만 맞아떨어진다. team_goals_for가
    # 주어지면(호출부가 안 넘기면 기존과 100% 동일하게 동작) 팀별로 추정
    # 골 합계 대비 실제 합계 비율만큼 각 선수 골을 일괄 스케일링해서
    # "그 팀 선수들 골을 다 더하면 그 팀 실제 득점과 같다"를 보장한다.
    # 도움은 team_goals_for에 대응하는 실측치가 없어 손대지 않는다(순수
    # 추정 유지) — 필요해지면 팀별 실제 도움 합계도 같은 방식으로 넘기면
    # 된다.
    raw = []
    _raw_append = raw.append
    # [2026-09 성능 2차] 예전엔 26만 행마다 league_matches/team_avg/
    # league_avg/league_goal_mult를 각각 조회해 행당 dict.get이 4번씩
    # (총 100만 회) 돌았다. tid가 정해지면 lid도 정해지므로 이 네 값은
    # 팀당 한 벌뿐이다 — 팀별로 한 번만 묶어두고 행당 조회를 1번으로
    # 줄인다(팀 수 약 1만 개). 값도 순서도 그대로다.
    _team_ctx = {}
    for _pid, _pos, _ovr, _sub, tid, lid in rows:
        _cx = _team_ctx.get(tid)
        if _cx is None:
            _cx = _team_ctx[tid] = (league_matches.get(lid, 38),
                                     team_avg.get(tid, 50.0),
                                     league_avg.get(lid, 50.0),
                                     league_goal_mult.get(lid, 1.0))
        fsm, _ta, _la, _gm = _cx
        g, a, rt = _estimate_ai_season(
            _ovr, _pos, _ta, _la, _sub, full_season_matches=fsm,
            goal_env_mult=_gm)
        # [2026-09 신설, 신민용 요청: "GK들은 골 어시보단 선방률 이런걸로
        # 표시해야 하잖아"] 골/도움과 별개로 클린시트(무실점 경기 수)도
        # 같이 추정한다 — GK가 아닌 포지션도 값 자체는 계산·저장해두지만
        # (계산 비용이 적어 굳이 분기할 필요 없음), 화면에서 GK만 이
        # 값을 골/도움 대신 보여준다(world_browser_window.py).
        cs = _estimate_ai_clean_sheets(_pos, _ovr, _ta, _la, full_season_matches=fsm)
        # [2026-09 재수정, 신민용 요청: "클린시트 말고 선방:14 실점:1
        # 선방률:93.5%로 떠야한다"] game_engine._estimate_ai_gk_saves
        # 정의부 주석 참고 — GK만 의미 있는 값이라 GK일 때만 계산한다
        # (그 외 포지션은 컬럼 기본값 0 그대로).
        saves = goals_conceded = 0
        if _pos == "GK":
            saves, goals_conceded = _estimate_ai_gk_saves(
                _ovr, _ta, _la, full_season_matches=fsm)
        _raw_append([_pid, year, tid, fsm, g, a, rt, cs, saves, goals_conceded])

    # [2026-09 신설, 신민용 리포트: "팀 골이 30개면 애들이 골고루 나눠
    # 갖는 것 같다 — 득점왕이 10골 정도밖에 안 된다"] 스쿼드 뎁스 감쇠 —
    # team_goals_for 스케일링 전에 적용해야 "팀 추정 합계"가 이미 쏠린
    # 모양이 되고, 그 다음 실제 골 합계로 스케일링해도 쏠린 모양이 그대로
    # 유지된다(game_engine._apply_squad_depth_decay 문서 참고). raw는
    # 컬럼 위치 고정 리스트(rows와 같은 순서로 1:1 대응)라, 그 자리에서
    # goals(row[4])/assists(row[5])만 덮어쓰는 얇은 dict 래퍼를 만들어
    # 공유 함수에 넘긴 뒤 결과를 다시 raw에 되돌려 쓴다.
    _depth_rows = [{"team_id": r[4], "position": r[1], "ovr": r[2],
                     "goals": row[4], "assists": row[5]} for r, row in zip(rows, raw)]
    _apply_squad_depth_decay(_depth_rows, key_fn=lambda d: (d["team_id"], d["position"]))
    # [2026-09 통일, 신민용 요청: "득점왕 판정도 세계기록실 골이랑 같은
    # 보정을 쓰게"] 팀 실제 득점 배분도 _collect_league_candidates(개인수상
    # 판정)와 완전히 같은 함수(_apply_team_goal_budget)를 공유한다.
    # allow_zero=False — 여기 team_goals_for는 teams.goals_for 전체
    # 스냅샷이라 아직 집계 안 된 팀이 0으로 섞일 수 있다(그 함수 주석 참고).
    _apply_team_goal_budget(_depth_rows, lambda d: d["team_id"], team_goals_for)
    for row, d in zip(raw, _depth_rows):
        row[4], row[5] = d["goals"], d["assists"]

    inserts = [tuple(row) for row in raw]
    inserts.sort(key=lambda t: (t[0], t[1]))
    c.executemany(
        "INSERT OR REPLACE INTO hist.ai_player_season_stats"
        "(player_id, year, team_id, matches, goals, assists, rating, clean_sheets, saves, goals_conceded) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", inserts)

    # [2026-09 신설] 대회별(국내컵/클럽대항전/슈퍼컵/클럽월드컵) 추정치 —
    # 위 리그와 완전히 같은 공식·team_avg/league_avg를 재사용하되, 이번
    # 시즌 그 팀이 그 대회에서 실제로 뛴 경기수만 대회마다 새로 센다.
    def _team_comp_match_counts(table_matches, table_tournaments):
        counts = {}
        for side in ("home_team_id", "away_team_id"):
            for row in c.execute(
                    f"""SELECT m.{side} AS tid, COUNT(*) AS n
                        FROM {table_matches} m
                        JOIN {table_tournaments} t ON m.tournament_id = t.id
                        WHERE t.year=? AND m.home_score!=-1
                        GROUP BY m.{side}""", (year,)).fetchall():
                counts[row["tid"]] = counts.get(row["tid"], 0) + row["n"]
        return counts

    # 챔스/유로파급/컨퍼런스급은 워터폴 구조상 한 팀이 한 해에 최대
    # 하나에만 속하므로(world_browser.py의 같은 전제 참고) 세 집계를
    # 그냥 합쳐도 안전하다 — "클럽대항전" 한 칸으로 통합 표시하는 UI와
    # 원칙이 동일하다.
    cl_counts = {}
    for _prefix in ("cl", "el", "ecl"):
        for tid, n in _team_comp_match_counts(f"{_prefix}_matches", f"{_prefix}_tournaments").items():
            cl_counts[tid] = cl_counts.get(tid, 0) + n

    # [2026-09 신설, 신민용 요청: "3부/4부 국내컵도 선수 평점/골/도움/
    # 선방 기록이 생겨야 하지"] 국내컵(cup)과 완전히 같은 패턴 — 이
    # 대회는 챔스/유로파/컨퍼런스처럼 리그와 겹치지 않는(3/4부 팀만
    # 참가) 별개 대회라 그냥 5번째 키로 추가하면 된다. world_browser.py
    # 쪽 _comp_stats["lower_cup"]으로 그대로 읽힌다.
    comp_match_counts = {
        "cup": _team_comp_match_counts("cup_matches", "cup_tournaments"),
        "cl":  cl_counts,
        "sc":  _team_comp_match_counts("sc_matches", "sc_tournaments"),
        "cwc": _team_comp_match_counts("cwc_matches", "cwc_tournaments"),
        "lower_cup": _team_comp_match_counts("lower_cup_matches", "lower_cup_tournaments"),
    }

    # [2026-09 버그수정, 신민용 리포트: "국내컵/챔스 등 대회 초반 탈락한
    # 선수도 그 대회에서 실제로 뛴 경기수 기준 풀시즌 기대치의 30%가량이
    # 그대로 반영돼, 팀 실제 스코어(예: 0-2 탈락)와 전혀 안 맞는 골/도움이
    # 나온다"] 위 리그(hist.ai_player_season_stats)는 team_goals_for로
    # "Tier B" 보정을 받는데, 이 대회별 블록만 그 보정이 빠져 있었다 —
    # _estimate_ai_season은 대회당 실제로 뛴 경기수(fsm)는 정확히 반영하지만
    # "그 대회에서 실제로 넣은 골 합계"는 전혀 모른 채 팀 강도만으로 독립
    # 추정하기 때문에, 한 팀이 이 대회에서 실제로 넣은 골 합계와 그 팀
    # 선수들의 추정 골 합계가 우연히만 맞아떨어진다. _team_comp_match_counts와
    # 완전히 같은 패턴으로 대회별 "실제 득점"(home_score/away_score 합)도
    # 집계해서, 리그와 동일하게 team_goals_for 스케일링을 대회별로도
    # 적용한다 — 이러면 "그 대회에서 이 팀 선수들 골을 다 더하면 그 대회
    # 그 팀 실제 득점과 같다"가 보장된다(도움은 리그와 동일 원칙으로 대응
    # 실측치가 없어 손대지 않는다).
    def _team_comp_goals_for(table_matches, table_tournaments):
        goals = {}
        for side, score_col in (("home_team_id", "home_score"), ("away_team_id", "away_score")):
            for row in c.execute(
                    f"""SELECT m.{side} AS tid, COALESCE(SUM(m.{score_col}),0) AS g
                        FROM {table_matches} m
                        JOIN {table_tournaments} t ON m.tournament_id = t.id
                        WHERE t.year=? AND m.home_score!=-1
                        GROUP BY m.{side}""", (year,)).fetchall():
                goals[row["tid"]] = goals.get(row["tid"], 0) + row["g"]
        return goals

    cl_goals = {}
    for _prefix in ("cl", "el", "ecl"):
        for tid, gsum in _team_comp_goals_for(f"{_prefix}_matches", f"{_prefix}_tournaments").items():
            cl_goals[tid] = cl_goals.get(tid, 0) + gsum

    comp_goals_for = {
        "cup": _team_comp_goals_for("cup_matches", "cup_tournaments"),
        "cl":  cl_goals,
        "sc":  _team_comp_goals_for("sc_matches", "sc_tournaments"),
        "cwc": _team_comp_goals_for("cwc_matches", "cwc_tournaments"),
        "lower_cup": _team_comp_goals_for("lower_cup_matches", "lower_cup_tournaments"),
    }

    by_comp_inserts = []
    for comp, counts in comp_match_counts.items():
        # [2026-09 성능, 신민용 리포트: "52주차→1주차 렉"] 예전엔 대회마다
        # 전세계 26만 행을 통째로 다시 훑고 루프 안에서 `fsm<=0이면
        # continue`로 걸렀다 — 5개 대회 × 26만 = 130만 회를 돌면서 실제로
        # 계산까지 가는 건 24만 회(18%)뿐, 나머지 107만 회는 순수 낭비였다.
        # 그 대회에 실제로 출전한 팀의 행 인덱스만 미리 모아서 훑는다.
        #
        # [주의] idxs.sort()는 생략하면 안 된다. _estimate_ai_season 등이
        # 난수를 소비하므로, 순회 순서가 바뀌면 저장되는 골/도움/평점이
        # 전부 달라진다 — 원본과 같은 rows 순서를 반드시 유지해야 한다.
        idxs = []
        for _tid, _n in counts.items():
            if _n > 0:
                _ridx = rows_by_team.get(_tid)
                if _ridx:
                    idxs.extend(_ridx)
        idxs.sort()
        comp_raw = []
        comp_meta = []   # comp_raw와 1:1 대응하는 (position, ovr) — 뎁스 감쇠용
        # [2026-09 성능 2차] 리그 루프와 같은 이유로 팀 단위 컨텍스트를 한
        # 번만 만든다(대회별 블록은 40만 행이라 효과가 더 크다).
        _cctx = {}
        for _i in idxs:
            _pid, _pos, _ovr, _sub, tid, lid = rows[_i]
            _cx = _cctx.get(tid)
            if _cx is None:
                _cx = _cctx[tid] = (counts[tid], team_avg.get(tid, 50.0),
                                     league_avg.get(lid, 50.0),
                                     league_goal_mult.get(lid, 1.0))
            fsm, _ta, _la, _gm = _cx
            g, a, rt = _estimate_ai_season(
                _ovr, _pos, _ta, _la, _sub, full_season_matches=fsm,
                goal_env_mult=_gm)
            cs = _estimate_ai_clean_sheets(_pos, _ovr, _ta, _la, full_season_matches=fsm)
            saves = goals_conceded = 0
            if _pos == "GK":
                saves, goals_conceded = _estimate_ai_gk_saves(
                    _ovr, _ta, _la, full_season_matches=fsm)
            # tid를 맨 뒤에 임시로 붙여둔다 — 아래 스케일링에서 팀별로
            # goals(index 4)를 찾아 덮어쓴 뒤, insert 직전에 다시 잘라낸다.
            comp_raw.append([_pid, year, comp, fsm, g, a, rt, cs, saves, goals_conceded, tid])
            comp_meta.append((_pos, _ovr))

        # [2026-09 신설, 신민용 확정: "모든 대회가 그렇게 되어야 한다"]
        # 스쿼드 뎁스 감쇠 — 리그(hist.ai_player_season_stats)와 개인상
        # 판정(_collect_league_candidates)에는 이미 들어 있는데 이 대회별
        # 블록만 빠져 있었다. 없으면 _estimate_ai_season의 포지션 기준치
        # ±20%가 같은 포지션 주전/백업에게 거의 똑같이 붙어서, 아래 실득점
        # 스케일을 걸어도 "팀 골을 로스터가 고르게 나눠 가진" 모양이 그대로
        # 유지된다(득점왕이 안 튀는 원인). 반드시 스케일 '전에' 적용해야
        # 쏠린 모양이 스케일 후에도 남는다 — 리그 쪽과 같은 순서다.
        _depth_rows = [{"team_id": row[10], "position": m[0], "ovr": m[1],
                         "goals": row[4], "assists": row[5], "matches": row[3]}
                        for row, m in zip(comp_raw, comp_meta)]
        _apply_squad_depth_decay(_depth_rows, key_fn=lambda d: (d["team_id"], d["position"]))
        # [2026-09 신설] 포지션 사이의 집중 — 뎁스 감쇠는 같은 포지션
        # 안에서만 몰아주므로, 이게 없으면 컵 13골이 [3,2,1,1,1,1]처럼
        # 흩어진다(2003 세이브 실측: 국내컵 팀 톱1 비중 37%, 대회 최다
        # 득점 7골). 짧은 대회일수록 세게 걸린다 — 국내컵(1~8경기)은
        # 강하게, CL 결승 진출팀(13경기)은 절반쯤만.
        _apply_ace_concentration(_depth_rows, lambda d: d["team_id"])
        # allow_zero=True — comp_goals_for는 실제로 치른 경기(home_score
        # !=-1) 행에서만 키를 만들므로, 값이 0이면 "경기는 했는데 한 골도
        # 못 넣은 팀"이 확실하다. 그런 팀의 선수 개인 기록도 0이어야 한다.
        _apply_team_goal_budget(_depth_rows, lambda d: d["team_id"],
                                 comp_goals_for.get(comp), allow_zero=True)
        for row, d in zip(comp_raw, _depth_rows):
            row[4], row[5] = d["goals"], d["assists"]

        by_comp_inserts.extend(tuple(row[:10]) for row in comp_raw)

    if by_comp_inserts:
        by_comp_inserts.sort(key=lambda t: (t[0], t[1], t[2]))
        c.executemany(
            "INSERT OR REPLACE INTO hist.ai_player_season_stats_by_comp"
            "(player_id, year, competition, matches, goals, assists, rating, clean_sheets, saves, goals_conceded) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)", by_comp_inserts)


def _snapshot_intl_ratings_rows(c, rows):
    """[2026-09 신설] _snapshot_intl_tournament_ratings/_snapshot_intl_season_
    ratings 공유 핵심 로직 — 주어진 intl_squad 행들(이미 tournament_id로
    필터된 상태)에 대해 team_avg/tourney_avg 집계 후 추정치를 계산해
    UPDATE한다. rows가 비어있으면 아무것도 안 함.

    [2026-09 버그수정, 신민용 리포트: "잉글랜드 전체 골이 11인데 월드컵
    골든부츠 1등이 잉글랜드 선수이며 14골"] 원인: 클럽 쪽(_snapshot_
    season_ratings / by_comp 스냅샷)은 _estimate_ai_season의 순수 OVR
    추정치를 그대로 쓰지 않고 두 단계를 더 거친다 —
      (1) _apply_squad_depth_decay: (팀, 포지션) 안에서 OVR 1등에게
          몰아주고 백업은 급감시켜 "골고루 나눠 갖는" 모양을 없앰
      (2) _team_goal_scale_factors: 팀이 실제로 넣은 골 합계에 맞춰
          전체를 비례 축소/확대
    그런데 국제대회 경로만 이 둘이 통째로 빠져 있어서, 23명 스쿼드의
    추정치가 그대로 저장됐다. 실측(2002 월드컵 세이브): 잉글랜드 실제
    12골인데 AI 추정 합계 40골(3.3배), 벨기에는 3골인데 43골(14.3배).
    개인상(_intl_award_pool)이 이 값을 그대로 읽으니 "팀 12골, 개인
    14골"이 나온 것이다. 클럽과 똑같은 순서(뎁스 감쇠 → 실득점 스케일)
    로 두 단계를 붙인다.

    [클럽과 다른 점 — 의도된 것]
    · 도움도 같은 계수로 스케일한다. 클럽 쪽은 골만 스케일하는데,
      국제대회는 왜곡 배율이 3~14배라 골만 고치면 "팀 12골인데 도움왕
      12도움" 같은 게 그대로 남는다. 골:도움 비율은 그대로 유지된다.
    · 내 선수 득점을 AI 몫에서 뺀다. intl_squad는 AI 전용이고 내
      기록은 intl_matches에서 따로 집계되므로(_intl_award_pool), 안
      빼면 내가 넣은 만큼 팀 합계가 초과된다. 클럽은 한 팀에 AI가
      20명 넘어 왜곡이 작지만 국가대표는 23명 중 1명이라 크게 티난다.
    · 그 나라가 실제로 0골이면 AI도 0으로 만든다(경기를 치른 경우에
      한해). 경기 자체가 없으면(아직 안 치른 대회) 스케일을 아예
      건너뛰어 예전과 같이 둔다."""
    from game_engine import (_estimate_ai_season, _estimate_ai_clean_sheets,
                              _estimate_ai_gk_saves, _apply_squad_depth_decay,
                              _apply_ace_concentration, _apply_team_goal_budget)
    if not rows:
        return

    # (tournament_id, country) 단위 대표팀 평균 OVR + tournament_id 단위
    # 전체 참가국 평균 OVR("이 대회 수준") — 한 번의 스캔으로 동시 집계.
    team_ovr_sum, team_ovr_n = {}, {}
    tourney_ovr_sum, tourney_ovr_n = {}, {}
    for r in rows:
        key = (r["tournament_id"], r["country"])
        ovr = r["ovr"] or 0
        team_ovr_sum[key] = team_ovr_sum.get(key, 0) + ovr
        team_ovr_n[key] = team_ovr_n.get(key, 0) + 1
        tourney_ovr_sum[r["tournament_id"]] = tourney_ovr_sum.get(r["tournament_id"], 0) + ovr
        tourney_ovr_n[r["tournament_id"]] = tourney_ovr_n.get(r["tournament_id"], 0) + 1

    team_avg = {k: team_ovr_sum[k] / team_ovr_n[k] for k in team_ovr_sum}
    tourney_avg = {k: tourney_ovr_sum[k] / tourney_ovr_n[k] for k in tourney_ovr_sum}

    # raw는 tuple이 아니라 list로 만든다 — 아래 두 보정 단계가 goals(1)/
    # assists(2) 자리를 그 자리에서 덮어써야 하기 때문(클럽 쪽과 동일 패턴).
    updates = []
    keys = []
    for r in rows:
        key = (r["tournament_id"], r["country"])
        t_avg = team_avg.get(key, r["ovr"] or 50.0)
        l_avg = tourney_avg.get(r["tournament_id"], t_avg)
        fsm = r["appearances"]
        g, a, rt = _estimate_ai_season(r["ovr"] or 0, r["position"], t_avg, l_avg,
                                        r["sub_role"], full_season_matches=fsm)
        cs = _estimate_ai_clean_sheets(r["position"], r["ovr"] or 0, t_avg, l_avg,
                                        full_season_matches=fsm)
        saves = goals_conceded = 0
        if r["position"] == "GK":
            saves, goals_conceded = _estimate_ai_gk_saves(r["ovr"] or 0, t_avg, l_avg,
                                                            full_season_matches=fsm)
        updates.append([rt, g, a, cs, saves, goals_conceded,
                        r["tournament_id"], r["country"], r["player_id"]])
        keys.append(key)

    # ── (1) 스쿼드 뎁스 감쇠 — (대회, 국가, 포지션) 그룹 안에서 OVR
    #        1등에게 몰아준다. 클럽은 (team_id, position)이 그룹 키인데,
    #        국제대회는 한 선수가 여러 대회에 나올 수 있으므로 대회까지
    #        키에 포함해야 대회별로 따로 계산된다. 반드시 아래 실득점
    #        스케일 '전에' 적용해야, 쏠린 모양이 스케일 후에도 유지된다.
    #        [2026-09 추가] "apps"(그 대회 출전수)를 같이 넘긴다 — 안
    #        넘기면 OVR만 높고 한 경기도 안 뛴 선수가 1.15배 자리를
    #        차지하고 실제 주전이 0.45배로 밀린다(2002 월드컵 실측
    #        220개 그룹 중 62개가 이 상태였다). 자세한 건 _apply_squad_
    #        depth_decay 문서 참고.
    _depth_rows = [{"key": k, "position": r["position"], "ovr": r["ovr"] or 0,
                     "goals": u[1], "assists": u[2],
                     "apps": r["appearances"] or 0, "matches": r["appearances"] or 0}
                    for r, u, k in zip(rows, updates, keys)]
    _apply_squad_depth_decay(_depth_rows, key_fn=lambda d: (d["key"], d["position"]))
    # ── (1-b) 포지션 사이의 집중 — 뎁스 감쇠만으로는 ST/LW/CAM/CM이
    #        서로 고르게 나눠 갖는 모양이 남아서 골든부츠가 4골에서
    #        멈춘다. 대회 길이(월드컵 7경기)만큼 강하게 걸린다.
    _apply_ace_concentration(_depth_rows, lambda d: d["key"])

    # ── (2) 그 나라가 이 대회에서 실제로 넣은 골에 맞춰 정수 배분 ────
    _tids = sorted({k[0] for k in keys})
    _real, _played, _mine = {}, {}, {}
    for _t in _tids:
        for _side, _sc in (("home", "home_score"), ("away", "away_score")):
            for _m in c.execute(
                    f"""SELECT {_side} AS nat, COUNT(*) AS n, COALESCE(SUM({_sc}),0) AS g
                        FROM intl_matches
                        WHERE tournament_id=? AND home_score>=0 AND away_score>=0
                        GROUP BY {_side}""", (_t,)).fetchall():
                _k = (_t, _m["nat"])
                _real[_k] = _real.get(_k, 0) + _m["g"]
                _played[_k] = _played.get(_k, 0) + _m["n"]
        # 내 선수 득점은 AI에게 나눠줄 몫에서 뺀다(위 docstring 참고).
        for _m in c.execute(
                """SELECT my_nat AS nat, COALESCE(SUM(my_goals),0) AS g
                   FROM intl_matches WHERE tournament_id=? AND my_played=1
                   GROUP BY my_nat""", (_t,)).fetchall():
            if _m["nat"]:
                _mine[(_t, _m["nat"])] = _mine.get((_t, _m["nat"]), 0) + _m["g"]

    # allow_zero=True — _budget은 실제로 치른 경기 행에서만 키를 만들므로
    # 0이면 "경기는 했는데 무득점"이 확실하다. 아직 안 치른 대회는 키
    # 자체가 없어 그대로 통과한다.
    _budget = {k: max(0, _real.get(k, 0) - _mine.get(k, 0))
               for k in set(keys) if _played.get(k, 0) > 0}
    _apply_team_goal_budget(_depth_rows, lambda d: d["key"], _budget, allow_zero=True)
    for u, d in zip(updates, _depth_rows):
        u[1], u[2] = d["goals"], d["assists"]

    updates = [tuple(u) for u in updates]
    updates.sort(key=lambda t: (t[6], t[7], t[8]))
    c.executemany(
        "UPDATE intl_squad SET rating=?, goals=?, assists=?, clean_sheets=?, saves=?, goals_conceded=? "
        "WHERE tournament_id=? AND country=? AND player_id=?", updates)


_INTL_SQUAD_ROWS_SQL = """SELECT s.tournament_id, s.country, s.player_id, s.appearances,
                                 ap.position AS position, ap.ovr AS ovr, ap.sub_role AS sub_role
                          FROM intl_squad s
                          JOIN ai_players ap ON ap.id = s.player_id
                          WHERE {where} AND s.appearances > 0"""


def _snapshot_intl_tournament_ratings(c, tournament_id):
    """[2026-09 신설, 신민용 리포트: "월드컵 등 대회 상도 이젠 (intl_squad)
    수치가 있으니 그거에 맞춰서 짜자"] 대회 하나가 막 끝난 시점(intl_engine.
    _finish_tournament, 골든볼 등 시상 직전)에 그 대회만 즉시 스냅샷한다 —
    기존엔 이 계산이 연 1회(run_ai_offseason, 아래 _snapshot_intl_season_
    ratings)에만 일어나서, 대회가 끝나 시상하는 시점엔 이 대회의 intl_squad
    저장값이 아직 없었다(그래서 시상 로직이 저장값과 무관하게 매번 새로
    즉석 추정 — 클럽 개인수상이 겪었던 것과 완전히 같은 문제). 이제 시상
    직전에 그 대회만 먼저 저장해두면, 시상 로직은 그 저장값을 그대로
    읽기만 하면 된다."""
    rows = c.execute(_INTL_SQUAD_ROWS_SQL.format(where="s.tournament_id=?"),
                      (tournament_id,)).fetchall()
    _snapshot_intl_ratings_rows(c, rows)


def _snapshot_intl_season_ratings(c, year):
    """[2026-09 신설, 신민용 요청: "국가대표에도 평점이랑 골 어시 이런걸
    넣고 싶어"] 위 _snapshot_season_ratings(클럽)와 완전히 같은 원리 —
    개별 국제경기를 실제로 시뮬레이션하지 않으므로(세계 전역 수만 명분
    불가능), game_engine._estimate_ai_season/_estimate_ai_clean_sheets로
    즉석 추정해 intl_squad(rating/goals/assists/clean_sheets 컬럼, 위
    마이그레이션 참고)에 저장한다.

    이 게임은 모든 국제대회가 "1년 단위 완결"(기존 확정 설계)이므로
    이 해(t.year=year)에 열린 대회의 intl_squad 행만 대상으로 하면
    충분하다 — run_ai_offseason 초반(로스터가 은퇴/이적으로 바뀌기 전,
    _snapshot_season_ratings와 동일 타이밍)에 호출하면 그 대회는 이미
    끝나 appearances가 최종값으로 확정돼 있다.

    [2026-09 재설계] `AND s.rating=0` 조건을 추가해, _snapshot_intl_
    tournament_ratings(대회 종료 즉시, 위 참고)로 이미 개별 스냅샷된
    대회는 자동으로 건너뛴다 — 안 그러면 같은 대회가 다른 랜덤값으로
    또 덮어써져 시상 때 읽은 값과 여기서 다시 어긋난다. rating은 절대
    정확히 0이 될 수 없는 평점 공식이라(베이스라인 5점대+) "아직 스냅샷
    안 됨" 신호로 안전하게 쓸 수 있다. 결승까지 못 가고 조별탈락 등으로
    _finish_tournament 자체를 안 거치는 대회 종류(랭킹 평가전 extra 등)는
    당연히 rating=0으로 남아있으므로 여기서 정상적으로 처리된다.

    team_avg/league_avg 대응: 클럽의 "소속팀 평균 OVR"/"리그 평균 OVR"에
    맞춰, 국제대회에서는 "이 선수의 국가대표팀(같은 대회·같은 국가)
    평균 OVR"/"이 대회 전체 참가국 평균 OVR"을 쓴다 — 상대적으로 더
    강한 대표팀 소속일수록(그 대회 평균 대비) 골/도움/평점이 소폭
    더 높게 나오는 클럽 쪽과 같은 논리. full_season_matches는 그 선수의
    실제 출전 횟수(appearances, bump_intl_squad_appearances가 경기마다
    올린 실측값)를 그대로 쓴다 — 클럽처럼 "이 팀이 몇 경기짜리 시즌을
    뛰었는지" 유추할 필요 없이 이미 정확한 값이 있다.

    [한계] intl_squad 자체가 2026-08 신설이라 그 이전 대회는 소급 불가
    (클럽 ai_player_season_stats와 동일 원칙) — appearances=0인 행(실제
    출전 없이 명단에만 있던 선수)은 저장할 통계가 없어 건너뛴다."""
    rows = c.execute(
        """SELECT s.tournament_id, s.country, s.player_id, s.appearances,
                  ap.position AS position, ap.ovr AS ovr, ap.sub_role AS sub_role
           FROM intl_squad s
           JOIN intl_tournaments t ON t.id = s.tournament_id
           JOIN ai_players ap ON ap.id = s.player_id
           WHERE t.year=? AND s.appearances > 0 AND s.rating = 0""", (year,)).fetchall()
    _snapshot_intl_ratings_rows(c, rows)


def seed_initial_position_history(year):
    """[2026-08 신설] seed_initial_ovr_history(database.py)와 같은 이유 —
    시즌 전환이 한 번도 없었던 세이브 첫 해는 _snapshot_season_positions을
    부를 계기가 없어 영구히 빈칸이 된다. 캐릭터 생성 직후(game_engine.py가
    seed_initial_ovr_history 바로 다음 자리에서 호출) 한 번 아카이브해서
    첫 해부터 정확하게 남긴다. formation_widget 의존성 때문에 database.py가
    아니라 여기(ai_lifecycle.py)에 둔다."""
    conn = get_conn()
    c = conn.cursor()
    _snapshot_season_positions(c, year)
    conn.commit()
    conn.close()


def snapshot_my_player_position(year):
    """[2026-08 신설, 신민용 요청: "세계 축구 기록실 선수 검색에서 AI는
    연도별 주전/로테이션/대기/유망주가 뜨는데 나(my_player)는 안 뜬다"]
    _snapshot_season_positions()는 ai_players만 훑고 my_player는 대상이
    아니라서 생긴 공백을 메운다.

    [설계 — 신민용+GPT 검토] _snapshot_season_positions() 자체(전세계
    ai_players 26만 건을 매 시즌 훑는 무거운 함수)를 고쳐서 my_player를
    끼워 넣는 대신, my_player가 소속된 팀 하나만 targeted 조회하는 별도
    함수로 분리했다 — 이유는 두 가지. (1) 성능: 이미 O(전세계)인 그
    함수에 로직을 더 얹기보다, my_player 소속팀 로스터(팀당 20명대)만
    보는 이 함수가 훨씬 싸다. (2) 안전성: 매 시즌 전체 AI 이력을 쌓는
    핵심 공용 함수를 건드리면 실수 시 파급 범위가 전세계 선수단이라
    커진다 — my_player 전용 로직을 완전히 분리해두면 이 기능 하나만
    독립적으로 검증·롤백할 수 있다.

    베스트11 배정은 formation_logic._greedy_fill_slots/compute_squad_roles
    를 그대로 재사용해 _snapshot_season_positions와 동일한 알고리즘으로
    맞춘다(그래야 "포메이션 화면은 주전인데 선수 검색은 후보"같은 또
    다른 불일치가 안 생김). my_player의 id는 ai_players.id와 값이
    겹칠 수 있으므로("__ME__" 같은 문자열 sentinel을 써서) 이 함수
    안에서만 쓰고 절대 저장하지 않는다 — 저장은 my_player_position_
    history(year 단일 PK, player_id 없음)에 한다.

    소속팀이 없으면(무소속) 그 해는 기록하지 않는다 — AI가 방출/은퇴로
    한 해 team_id가 없으면 그 해 role_checkpoints에 값이 없는 것과
    동일한 동작."""
    from formation_logic import _greedy_fill_slots, compute_squad_roles
    from constants import FORMATION_SLOTS

    conn = get_conn()
    c = conn.cursor()
    try:
        me = c.execute(
            "SELECT current_team_id, position, ovr, age FROM my_player WHERE id=1").fetchone()
        if not me or not me["current_team_id"]:
            return
        team_id = me["current_team_id"]
        team_row = c.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team_row:
            return
        formation = team_row["formation"] or "4-4-2"
        teammates = c.execute(
            "SELECT id, position, ovr, age FROM ai_players WHERE team_id=?", (team_id,)).fetchall()

        ME = "__ME__"
        candidates = [{"id": r["id"], "position": r["position"], "ovr": r["ovr"] or 0}
                      for r in teammates]
        candidates.append({"id": ME, "position": me["position"], "ovr": me["ovr"] or 0})
        pool = [(r["id"], r["ovr"], r["age"]) for r in teammates]
        pool.append((ME, me["ovr"], me["age"]))

        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        placed = _greedy_fill_slots(candidates, slots)
        roles = compute_squad_roles(pool)

        my_position = me["position"] or ""
        for slot_idx, pl in enumerate(placed):
            if pl is not None and pl["id"] == ME:
                my_position = slots[slot_idx]
                break
        my_role = roles.get(ME, "")

        c.execute(
            "INSERT OR REPLACE INTO my_player_position_history(year, position, role) VALUES (?,?,?)",
            (year, my_position, my_role))
        conn.commit()
    finally:
        conn.close()


def _shuffle_formations(c):
    """[2026-08 재설계, 신민용 확정: "포메이션 20개 확장 + 스쿼드 적합도/
    전술 성향 기반 선택"] 예전엔 팀의 20%가 완전 무작위로 다른 포메이션을
    뽑았다(스쿼드 구성도 감독 성향도 전혀 안 봄) — 함수 이름은 하위호환
    (rng_probe.py가 이 이름으로 monkeypatch, database.py 여러 주석이 이
    이름을 언급)을 위해 그대로 두지만 내부 로직은 완전히 새로 짰다.

    모든 팀에 대해 매 시즌:
      1) teams.formation_fit_bonus 캐시를 이번 시즌 확정된 로스터 기준
         으로 항상 갱신한다 — 포메이션이 안 바뀌어도 이적/은퇴로 로스터
         구성 자체는 매년 바뀌므로 재계산이 필요하다. 실제 매치 시뮬
         보정(game_engine._formation_bias)은 이 캐시값만 읽는다(매치마다
         재계산하지 않음).
      2) constants.FORMATION_REEVAL_PROB 확률에 걸린 팀만 포메이션 자체를
         재검토한다 — formation_logic.choose_formation()이 스쿼드 적합도
         (60%) + 전술 성향 적합도(30%) + 랜덤(10%)으로 점수를 매겨 상위
         5개 후보 중 가중 랜덤으로 고른다("수비 성향 팀이 무조건 5-4-1만
         고르지 않는다"는 신민용 요청 반영). 안 걸린 팀은 기존 포메이션
         유지.
    반환: 실제로 포메이션이 바뀐 팀 수(기존 반환값과 동일한 의미)."""
    import formation_logic as _flogic

    teams = c.execute("SELECT id, formation, tactic_tendency FROM teams").fetchall()
    ai_rows = c.execute("SELECT team_id, position, ovr FROM ai_players").fetchall()
    roster_by_team: dict = {}
    for r in ai_rows:
        roster_by_team.setdefault(r["team_id"], []).append(
            {"position": r["position"], "ovr": r["ovr"]})

    changed = 0
    formation_updates = []   # (formation, team_id)
    fit_updates = []         # (fit_bonus, team_id)
    for t in teams:
        roster = roster_by_team.get(t["id"])
        if not roster:
            continue   # 로스터가 아직 없는 극초반(부트스트랩) 상황 방어
        cur_formation = t["formation"] or "4-4-2"
        tendency = t["tactic_tendency"] or "BALANCED"

        # [2026-09 성능] 같은 로스터로 포메이션 20개를 평가하므로 정렬·
        # dict 접근을 팀당 1회로 접어두고 넘긴다(formation_logic.prep_roster
        # 주석 참고 — 결과는 예전과 비트 단위로 동일하다고 실측 확인).
        prepped = _flogic.prep_roster(roster)
        if random.random() < FORMATION_REEVAL_PROB:
            new_formation, penalty = _flogic.choose_formation_prepped(
                prepped, cur_formation, tendency)
            if new_formation != cur_formation:
                changed += 1
                formation_updates.append((new_formation, t["id"]))
                cur_formation = new_formation
        else:
            _fname = cur_formation if cur_formation in FORMATION_SLOTS else "4-4-2"
            penalty = _flogic.formation_fit_penalty_prepped(
                prepped, _fname, FORMATION_SLOTS[_fname])

        fit_updates.append((_flogic.formation_fit_bonus(penalty), t["id"]))

    if formation_updates:
        c.executemany("UPDATE teams SET formation=? WHERE id=?", formation_updates)
    if fit_updates:
        c.executemany("UPDATE teams SET formation_fit_bonus=? WHERE id=?", fit_updates)
    return changed


# ─────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────
def _gen_stats(pos, target):
    """database._gen_ai_stats 재사용 (목표 OVR→스탯 역산)."""
    try:
        from database import _gen_ai_stats
        return _gen_ai_stats(pos, target)
    except Exception:
        keys = KEY_STATS_BY_POS.get(pos, ALL_STATS[:5])
        stats = {}
        for s in ALL_STATS:
            base = target + (3 if s in keys else -3)
            stats[s] = min(99, max(15, int(round(random.gauss(base, 4)))))
        return stats


def _build_name_cache(c):
    """국가별 이름풀 전체를 1회 로드 → {country_id: [name, ...]}
    _retire_and_replace에서 한 번 호출 후 재사용. ORDER BY RANDOM() 완전 제거."""
    rows = c.execute("SELECT country_id, name FROM player_names").fetchall()
    cache: dict = {}
    for r in rows:
        cache.setdefault(r["country_id"], []).append(r["name"])
    return cache


# 팀→국가 매핑 캐시 (오프시즌 내 반복 JOIN 방지)
_team_country_cache: dict = {}


def _get_team_country(c, team_id):
    """팀 ID → country_id. 한 번 조회 후 모듈 캐시에 저장."""
    if team_id not in _team_country_cache:
        row = c.execute(
            """SELECT cn.id AS cid FROM teams t
               JOIN leagues l ON t.league_id=l.id
               JOIN countries cn ON l.country_id=cn.id
               WHERE t.id=?""", (team_id,)).fetchone()
        _team_country_cache[team_id] = row["cid"] if row else None
    return _team_country_cache[team_id]


def _random_name(c, team_id, name_cache=None, used_in_team=None):
    """팀 소속국 이름풀에서 랜덤 이름. 같은 팀 내 중복 방지.
    used_in_team: set — 이번 오프시즌에 이미 이 팀에 배정된 이름들.
    다른 팀/리그 동명이인은 허용 (현실적으로 전 세계에 동명이인 있음).
    """
    cid = _get_team_country(c, team_id)
    if cid is not None:
        pool = None
        if name_cache is not None:
            pool = name_cache.get(cid, [])
        else:
            rows = c.execute(
                "SELECT name FROM player_names WHERE country_id=?", (cid,)).fetchall()
            pool = [r["name"] for r in rows]

        if pool:
            if used_in_team:
                # 팀 내 중복 회피: 사용 안 된 이름 우선
                available = [n for n in pool if n not in used_in_team]
                if available:
                    chosen = random.choice(available)
                else:
                    # 이름풀 소진 시 어쩔 수 없이 중복 허용
                    chosen = random.choice(pool)
            else:
                chosen = random.choice(pool)
            if used_in_team is not None:
                used_in_team.add(chosen)
            return chosen
    return f"신인{random.randint(100, 999)}"


# ─────────────────────────────────────────────
# 6. 승격/강등 직후 스쿼드 개편 (일부 방출+영입)
# ─────────────────────────────────────────────
def apply_squad_turnover_after_movement(rescale_jobs, year, turnover_frac=0.25,
                                         release_frac_of_turnover=0.35):
    """[2026-08 신설, 신민용 리포트: "30년 정도 돌리면 1부가 5부로, 5부가
    1부로 가는 경우가 아예 적지는 않다 — 승격/강등하면 팀 개편(방출 포함)이
    크게 일어나는 거 맞냐"] 확인 결과 답은 "아니오"였다 — game_engine.
    _process_promotion_relegation이 승강 직후 부르는 rescale_team_to_target_
    ovr()/rescale_teams_to_target_ovr_batch()는 스쿼드 전원의 스탯에 "같은
    델타"를 더하는 평행이동만 한다(선수 구성·개인별 순위는 전혀 안 바뀜).
    그래서 몇 단계를 한꺼번에 뛰어넘는 승격/강등이 반복돼도 스쿼드는 계속
    같은 선수들이 이름만 유지한 채 통째로 오르내릴 뿐, "이 정도로 급격히
    수준이 바뀌면 스쿼드도 크게 갈아엎힌다"는 현실감이 빠져 있었다.

    이 함수는 리스케일 직후(game_engine._process_promotion_relegation이
    rescale_teams_to_target_ovr_batch 호출 바로 뒤에 호출) 그 팀에서 OVR이
    가장 낮은 turnover_frac(기본 25%)만큼을 골라, 그 중 release_frac_of_
    turnover(기본 35%)는 신인 교체 없이 그냥 방출(삭제만 — 스쿼드가
    줄어들면 다음 시즌 _rebalance_squad_sizes가 자연스럽게 채운다, 이미
    있는 "자리 못 구한 선수 조기 은퇴" 경로와 동일한 원칙), 나머지는 새
    tier/등급 수준에 맞는 신규 선수로 즉시 교체(방출+영입)한다 — 스쿼드
    전체를 다 갈아엎지는 않는다(핵심 선수단은 유지, 하위권만 물갈이).

    rescale_jobs: [(team_id, target_ovr), ...] — game_engine이 이미 만들어둔
    _rescale_jobs를 그대로 재사용(팀별 새 목표 OVR을 다시 구할 필요 없음).
    반환: (replaced, released) 인원수."""
    from constants import (get_country_league_grade, CONTINENT_OVR_BONUS,
                           COUNTRY_OVR_ADJ, SUB_ROLES)
    from database import get_ovr_range, _pick_nationality, get_foreign_quota_range

    if not rescale_jobs:
        return 0, 0

    conn = get_conn()
    c = conn.cursor()

    team_ids = [j[0] for j in rescale_jobs]
    ph = ",".join("?" * len(team_ids))
    team_rows = {r["tid"]: r for r in c.execute(
        f"""SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname,
                   cn.continent AS continent
            FROM teams t JOIN leagues l ON t.league_id=l.id
                         JOIN countries cn ON l.country_id=cn.id
            WHERE t.id IN ({ph})""", team_ids).fetchall()}

    name_cache = _build_name_cache(c)
    replaced = 0
    released = 0
    del_ids = []
    new_rows = []

    for team_id, _target_ovr in rescale_jobs:
        info = team_rows.get(team_id)
        if not info:
            continue
        grade = get_country_league_grade(info["cname"])
        tier = info["tier"] or 1
        cname = info["cname"]
        continent = info["continent"] or "유럽"
        bonus = round(CONTINENT_OVR_BONUS.get(continent, 0) + COUNTRY_OVR_ADJ.get(cname, 0))
        rng = get_ovr_range(grade, tier, cname)
        if rng:
            lo, hi = rng[0] + bonus, rng[1] + bonus
        else:
            lo, hi = 40, 55

        squad = c.execute(
            "SELECT id, position FROM ai_players WHERE team_id=? ORDER BY ovr ASC",
            (team_id,)).fetchall()
        n = len(squad)
        if n < 2:
            continue
        n_turn = min(max(1, int(round(n * turnover_frac))), n - 1)
        n_release = max(0, min(n_turn, int(round(n_turn * release_frac_of_turnover))))
        used = set()
        _q_lo, quota = get_foreign_quota_range(cname, continent)
        foreign_ct = 0

        for i, pl in enumerate(squad[:n_turn]):
            del_ids.append(pl["id"])
            if i < n_release:
                released += 1
                continue
            pos = pl["position"]
            target = random.randint(lo, max(lo, (lo + hi) // 2))
            age = random.randint(*_AI_NEWBIE_AGE)
            stats = _gen_stats(pos, _youth_target_scale(target, age))
            ovr = calc_ovr(pos, stats)
            sub_role = random.choice(SUB_ROLES.get(pos, ["기본"]))
            nat, foreign_ct = _pick_nationality(cname, continent, grade, pos,
                                                False, foreign_ct, quota)
            name = _random_name(c, team_id, name_cache, used_in_team=used)
            new_rows.append((team_id, name, pos, *[stats[s] for s in ALL_STATS], ovr, age,
                              sub_role, nat, year + random.randint(2, 4), 0, year))
            replaced += 1

    if del_ids:
        _archive_forced_out_players(c, del_ids, year)
        c.executemany("DELETE FROM ai_players WHERE id=?", [(i,) for i in del_ids])
    if new_rows:
        c.executemany(
            f"""INSERT INTO ai_players
                (team_id,name,position,{_STAT_COLS},ovr,age,sub_role,nationality,
                 contract_end_year,last_transfer_year,created_year)
                VALUES(?,?,?,{','.join('?' for _ in ALL_STATS)},?,?,?,?,?,?,?)""",
            new_rows)
    conn.commit()
    return replaced, released