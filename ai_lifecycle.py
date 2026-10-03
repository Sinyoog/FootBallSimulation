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
from database import _squad_ovr_hard_min, _lift_stats_to_ovr
# [2026-10 병역 시스템 2단계] 군팀·복무 중인 선수는 이적/임대/재계약/스카우팅/
# 보충 생성/외국인 쿼터 조정/은퇴 대상에서 전부 빠진다(팀이 바뀌는 건 진급·강등뿐).
from military_service import drop_military_teams as _drop_mil_teams
from database import (get_conn, calc_ovr, ALL_STATS, KEY_STATS_BY_POS,
                      roll_bench_position)
# [2026-08 신설, 포메이션 20개 확장 + 스쿼드 적합도 시스템] 모듈 레벨에서
# _FORMATIONS 동기화(아래)와 _shuffle_formations()에 필요.
# [2026-08 신설, 세계 축구 기록실 연도별 평점/골/도움 요약] legs_for_team_count
# 는 _snapshot_season_ratings가 리그별 실제 풀시즌 경기수를 구할 때 쓴다
# (game_engine._league_full_season_matches와 동일 공식).
from constants import FORMATION_SLOTS, FORMATION_REEVAL_PROB, legs_for_team_count
# [2026-09 신설, 상류②] 아래 _SELL_W_TABLE/_DEMAND_W_TABLE 빌드용.
# formation_logic은 constants만 import하므로(순환 없음) 모듈 레벨에서
# 바로 가져다 쓸 수 있다.
from constants import POSITION_SELL_STRENGTH, POSITION_DEMAND_STRENGTH
from formation_logic import (_SLOT_TARGET_MAP,
                             position_sell_weight as _fl_position_sell_weight,
                             position_demand_weight as _fl_position_demand_weight)

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
# [2026-09 신설, 신민용 리포트: "AI가 16세를 생성하자마자 1군 선수로
# 등록하고 실제 경기/은퇴 사이클에 바로 집어넣는 구조라 비현실적이다 —
# 유저 선수 생성 나이(constants.PLAYER_START_AGE, 유저가 직접 고르는
# 값)와는 별개로 AI 월드 쪽만 하한을 17세로 올려야 한다"] 신인 대체
# 연령대와 최소 나이 하한을 16→17로 올린다(database._generate_team_players
# 의 최초 월드 생성 삼각분포 하한도 동일하게 17로 맞춤 — 그쪽 주석 참고).
_AI_MIN_AGE      = 17
_AI_NEWBIE_AGE   = (17, 21)   # 신인 영입 연령대
# [2026-08 4차 재설계, 신민용 확정(GPT 협업)] constants.
# AGE_OVR_FRACTION_MATURE_AGE(나이별 성장곡선 표가 100%에 도달하는
# 나이, 26)와 맞춰 성장 종료를 25로 늦췄다 — 예전엔 22였는데, 새
# 곡선(16세70%→25세98%→26세100%)은 22~25세 구간에도 완만한 성장분이
# 남아있어야 하기 때문. database._generate_team_players/이 파일의
# 신인 생성(아래 참고) 모두 같은 표(constants.AGE_OVR_FRACTION)를
# 공유하므로 "성장 종료 나이" 하나만 여기서 어긋나지 않게 한다.
# [2026-10 신민용 확정] 한국 유망주 경로 배율(database.YOUTH_PATH_BONUS)을 거는 부수 상한 —
# SS/S 리그 전 부수. 처음엔 1·2부로 좁혔는데(97+ 데뷔가 대부분 1부라서), 그건 빅5 자국 선수
# 기준이었다. 실측(22시즌): 빅5 외 국적 95+ 126명의 SS/S 1·2부 진입은 대부분 "상위리그 발탁"
# (19~24세)이고, 그 발탁의 출발은 SS/S 하위 부수(S 692·SS 175건)였다 — 아시아 B 출발은 1건.
# 게다가 SS/S 1·2부 유망주 자리는 시즌당 91건뿐이고 80%가 외국인 쿼터가 차서 자국 강제라,
# 1·2부만 걸면 배율 4.0이어도 한국 데뷔가 0.03→0.15/시즌에 그친다. 하위 부수까지 걸어야
# "어린 나이에 유럽 리그 하위 부수 → 발탁 → 고성장" 깔때기 입구가 열린다(유럽 23세 이하
# 한국인 풀 20명 vs 노르웨이 61·폴란드 111·세르비아 109).
YOUTH_PATH_MAX_TIER = 99
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
# [2026-09 재조정, 신민용+GPT 10시즌 헤드리스 검증: "potential_ovr>=97인
# 선수들이 피크 나이(24~29세)에도 평균 실제OVR이 94~96 — potential
# 평균(~98)보다 2~4점 낮다"] 0.35는 위 문단의 이전 재보정값인데, 그때는
# "몇 시즌 성장하면 에이스급 근처"면 충분했지만 지금은 potential_ovr이라는
# 개인별 목표가 새로 생겨서 "피크까지 그 목표에 최대한 다가가야" 97+
# 인구가 설계 의도(50~100명, 신민용+GPT 확정)만큼 나온다. 0.45로 올려서
# 수렴을 더 빠르게 한다 — 이 상수는 전원에게 동일하게 적용되므로(개인별
# 상한 자체는 그대로 min(team_cap,potential_ovr)) 일반 잠재력 선수가
# team_cap까지 더 빨리 도달하게 만드는 게 아니라, 각자 자기 potential에
# 더 확실히 도달하게 만드는 것 — 왕조 스노우볼(9월 초 버그)과는 무관하다.
_AI_GROWTH_CATCHUP_FRAC = 0.55  # 한 번 터치할 때 (팀 상한-현재값) 중 회복하는 비율

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
#
# [2026-09 재설계 2차, 신민용 리포트: "지금은 너무 단순히 1씩 내려가는데
# 현실은 전성기가 지나도 유지하는 경우가 많다"] 기존 표는 30~34세가
# 1.05%씩 거의 등간격으로 깎여서, 전성기 97 선수가 97→96→95→94→92로
# 정확히 "매년 1씩" 내려갔다(실측). 신민용이 확정한 새 목표 곡선은
# "고원(plateau) 뒤 급락" — 97→97→96→96→96→92→90 (29~35세):
#     29세 97(0%)   30세 97(0%)     31세 96(1.03%)
#     32세 96(1.03%) 33세 96(1.03%) 34세 92(5.15%)  35세 90(7.22%)
# 36세 이후는 40세 목표(전성기 대비 -22.1%, 예전에 확정한 "40세 73~76")를
# 그대로 유지하도록 36~39를 부드럽게 이어 붙였다 — 즉 이번 재설계로
# 실제로 바뀌는 구간은 30~35세뿐이고, 36세 이후 실측치는 기존과 거의
# 같다(예: 37세 84.2 → 84.2, 38세 81.2 → 81.1).
_AGING_DECLINE_SCHEDULE = {
    29: 0.0000, 30: 0.0000, 31: 0.0103, 32: 0.0103, 33: 0.0103,
    34: 0.0515, 35: 0.0722, 36: 0.1000, 37: 0.1320, 38: 0.1640,
    39: 0.1950, 40: 0.2211, 41: 0.2495, 42: 0.2789, 43: 0.3095,
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


# [2026-09 신설, 신민용 요청: "코드에는 선수에 따라 노화가 다르게 적용되는
# 경우도 있을 텐데, 나이대별 하락 확률/폭에 맞춰 더 조절해야 할 듯" —
# 제시된 표: 28~30 거의 없음 / 31~32 매우 낮음 / 33~34 낮음~중간 /
# 35~36 중간~높음 / 37+ 높음] 기존 개인차는 _mgmt_tier_and_mult 하나뿐
# 이었는데, 그건 커리어 내내 고정된 '배율'이라 모든 선수가 모양이 같은
# 곡선을 크기만 다르게 따라갔다 — "누구는 34세까지 96을 유지하다 한 번에
# 꺾이고, 누구는 31세부터 슬슬 내려간다" 같은 연도별 분기가 아예 없었다.
# 나이대별 폭을 가진 (선수, 나이) 해시 지터를 누적 하락률에 더한다:
#   - 부호가 대칭이라 인구 평균은 위 표 그대로 유지된다(밸런스 불변).
#   - 지터가 양수면 그 해에 더 깎이고, 음수면 목표치가 현재 OVR보다
#     높아져 그 해는 아예 안 깎인다(노화 루프는 '목표보다 높을 때만'
#     깎으므로 = 그 해를 '유지'로 넘긴다) — 이게 요청한 하락 '확률'에
#     해당한다.
#   - 폭은 나이대별 요청(28~30 거의 없음 … 37+ 높음)에 맞춰 잡았다.
#     특히 33~34세 폭(0.045)은 그 구간 기준 하락폭(+4.1%p)보다 크게
#     둬서, "34세까지 96을 그대로 유지하는 선수"와 "33세부터 먼저
#     꺾이는 선수"가 실제로 갈리게 한다(폭이 하락폭보다 좁으면 전원이
#     같은 해에 똑같이 꺾여 버린다 — 1차 시뮬에서 실제로 그랬다).
#   - player_id와 age를 같이 섞으므로 해마다 값이 달라지되, 같은 선수·
#     같은 나이면 항상 같은 값이라 재현성은 그대로다(DB 컬럼 불필요).
_AGING_JITTER_BANDS = ((30, 0.004), (32, 0.014), (34, 0.045), (36, 0.040), (99, 0.032))


def _aging_age_jitter(player_id: int, age: int) -> float:
    """(선수, 나이)별 누적 하락률 지터. 29세 이하는 항상 0."""
    if age <= 29:
        return 0.0
    width = _AGING_JITTER_BANDS[-1][1]
    for _hi, _w in _AGING_JITTER_BANDS:
        if age <= _hi:
            width = _w
            break
    h = ((player_id * 2246822519 + age * 3266489917) & 0xFFFFFFFF) / 0xFFFFFFFF
    return (h * 2.0 - 1.0) * width


def _aging_eff_decline_pct(player_id: int, age: int) -> float:
    """기준 곡선 × 자기관리 보정 + 나이대별 개인 지터 → 실제 누적 하락률.
    노화 경로(numpy/순수파이썬)가 둘 다 이 함수 하나만 쓴다."""
    base_pct = _aging_base_decline_pct(age)
    _, mult = _mgmt_tier_and_mult(player_id)
    return max(0.0, min(0.75, base_pct * (1.0 + mult) + _aging_age_jitter(player_id, age)))


def _aging_target_ovr(peak_ovr: int, age: int, player_id: int) -> int:
    """전성기 OVR·나이·개인 자기관리 보정을 종합해 "이 나이의 목표
    OVR"을 계산한다. 노화 로직은 매 시즌 이 목표치와 현재 OVR의 차이만큼만
    스탯을 깎는다(사용자 명시 요청: "연령별 목표 하락량을 먼저 정하고 그
    안에서 어떤 스탯이 떨어질지를 결정하는 구조")."""
    return max(15, int(round(peak_ovr * (1.0 - _aging_eff_decline_pct(player_id, age)))))


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


# [2026-09 신설, 신민용 리포트: "OVR 80대가 30세 이전에 은퇴하는 경우가
# 많다 — 그 정도면 어디서든 먹고 살 수 있어야 하는데, 리그 대비 퍼센타일
# 보정(_relative_ovr_retire_mult)만으로는 최대 0.75~0.78배(22~25% 감소)가
# 한계라 부족하다. 다만 'OVR 80 이상이면 무조건 은퇴확률 ×0.5'처럼 단순
# 절대값 보정은 위험하다 — 어떤 리그에 있든 그 리그와 무관하게 걸리면,
# 그 정도 OVR이 흔한 리그에서는 과보호가 되고 은퇴 시스템 전체의 리그별
# 차등이 흐려진다"] 그래서 리그 대비 퍼센타일(_relative_ovr_retire_mult가
# 이미 계산한 p, 0=그 리그 하단, 1=상단)이 1.0 이상 — 즉 자기 리그 상단을
# 이미 넘어선 선수에게만 절대 OVR 기준 "세계급인가"를 추가로 얹는다.
# 이렇게 하면 (1) 그 리그에서도 상단을 못 넘는 선수는 절대 OVR이 아무리
# 높아도(애초에 그 나라 OVR_RANGES 자체가 낮으면 일어나기 어렵지만) 이
# 층의 영향을 안 받고, (2) 리그를 이미 초월한 선수만 "그 초월 정도가
# 세계 기준으로도 진짜인지"에 따라 추가 보호를 받는다.
_ABSOLUTE_STAR_OVR_PTS = ((76, 1.00), (80, 0.88), (85, 0.72), (90, 0.60))


def _absolute_star_mult(ovr, league_relative_p: float) -> float:
    """league_relative_p(_relative_ovr_retire_mult가 계산한, 그 리그 대비
    위치 — clamp 전 원시값)가 1.0 미만이면 무조건 1.0(무영향). 1.0 이상
    (리그 상단을 이미 넘어선 선수)일 때만 절대 OVR 구간표(위 정의)를
    선형보간해 추가 배율을 적용한다 — 표 밖(76 미만/90 이상)은 양 끝
    값으로 클램프."""
    if league_relative_p < 1.0 or not ovr:
        return 1.0
    pts = _ABSOLUTE_STAR_OVR_PTS
    if ovr <= pts[0][0]:
        return pts[0][1]
    if ovr >= pts[-1][0]:
        return pts[-1][1]
    for (o0, m0), (o1, m1) in zip(pts, pts[1:]):
        if o0 <= ovr <= o1:
            t = (ovr - o0) / (o1 - o0)
            return m0 + (m1 - m0) * t
    return 1.0


# [2026-09 성능, 신민용 리포트: "52주차 연도전환"] 아래 함수 전용 메모.
# _retire_and_replace의 은퇴 판정 루프가 전세계 선수 전원(실측 264,974명)
# 에게 이 함수를 한 번씩 부르는데, 이 함수는 인자 5개만의 순수 함수다
# (안에서 부르는 get_ovr_range/_percentile_curve_mult/_absolute_star_mult도
# 전부 순수, 참조하는 OVR_RANGES/COUNTRY_LEAGUE_OVR_OVERRIDE는 런타임에
# 안 바뀌는 상수표). 실제로 들어오는 조합은 "리그 710개 × 그 리그에 실제로
# 존재하는 OVR 값"뿐이라 한 시즌에 18,926개(실측)로 수렴한다 — 즉 호출의
# 93%가 이미 계산해 둔 값을 다시 계산하고 있었다.
#
# 실측(같은 세이브의 실제 호출 인자 264,974건 재생):
#   0.614s → 0.119s (-0.495s, -81%)
#   서로 다른 조합 18,926개를 전수 대조해 반환값 불일치 0건
# 키 개수 상한도 유한하다(리그 조합 710개 × OVR 0~99 = 7만 남짓).
#
# 이 파일의 다른 메모(_MISMATCH_PENALTY_CACHE 등)와 같은 성격이라 무효화
# 시점이 없다 — 같은 인자면 게임 내내 항상 같은 값이다.
_REL_RETIRE_MULT_CACHE: dict = {}


def _relative_ovr_retire_mult(ovr, grade, tier, country, max_tier=None) -> float:
    """[2026-09 1차, 신민용 리포트: "K리그 에이스가 벤치멤버랑 똑같은
    확률로 은퇴하는 게 이상하다"] → [2026-09 2차 재설계, 신민용 피드백
    3건 반영: 퍼센타일 곡선 교체, 나이 램프 도입(21~25세 거의 무시·35세
    부터 전량 반영), tier-depth 게이팅]

    [2026-09 3차 재설계, 신민용 리포트: "20대에 OVR 높은데도 은퇴하는
    비율이 높다 — 국제대회까지 나갔던 애들도 젊을 때 은퇴하는 경우가
    있다. 젊은 애들도 OVR 높을수록 젊을 때 은퇴하는 비율이 거의 없어야
    한다(OVR 낮으면 그럴 수 있음). 그 나라 중간 리그를 기준점으로 잡는
    게 나을듯. 다만 각 나라 최하위에서 뛰는 선수들은 일찌감치 포기하는
    게 맞다"] 2차 설계의 age_weight 램프가 원인이었다 — 21~25세는
    age_weight≈0이라 relative_mult가 사실상 통째로 무시됐고(에이스든
    아니든 나이표 그대로), 26~34세도 부분적으로만 반영됐다. 그 결과
    "그 리그 기준 확실한 에이스"라도 30대 중반 전까지는 보호를 거의
    못 받았다 — 특히 약한 나라 리그(하위 카테고리)는 그 연령대 기본
    은퇴확률 자체가 낮지 않아서(예: low 카테고리 24세 기본 해저드가
    이미 2%대) 체감 영향이 컸다. 두 가지를 고친다:
      1) age_weight 램프를 완전히 제거 — "OVR 높으면 어릴 때도 보호",
         "OVR 낮으면 어릴 때도 위험 반영"을 나이 게이팅 없이 그대로
         적용한다(위험 쪽은 아래 3번 tier-depth 게이팅이 여전히 남아
         있어 "아직 내려갈 하위 리그가 있는데 무조건 은퇴로 미는" 극단은
         안 나온다).
      2) OVR 비교 기준을 "지금 뛰고 있는 그 tier"가 아니라 "그 나라의
         중간 tier"로 바꾼다((max_tier+1)//2, 신민용 제안 그대로) —
         하위 tier에 묻혀 있어도 그 나라 기준 진짜 실력자면 제대로
         에이스로 인식되게. tier 자체(아래 3번 게이팅용, "이미 최하위
         tier인가")는 그대로 실제 소속 tier를 쓴다.
      3) "OVR 낮음 → 은퇴"로 직결하면 안 되고 "OVR 낮음 → 하위 리그
         이적"이 먼저이며, 은퇴는 그마저 갈 곳이 없을 때(이미 그 나라
         최심부 tier)만 강하게 반영해야 한다는 지적 그대로 유지 —
         하위권 쪽(mult>1.0)은 max_tier 미만(아직 내려갈 하위 리그가
         있음)이면 25%만 반영하고, 이미 최심부(더 내려갈 데 없음, "일찌
         감치 포기하는 게 맞다"는 요청 그대로)면 전량 반영한다. 상위권
         쪽(에이스, mult<1.0)은 부수 무관하게 그대로.

    [2026-09 성능] 결과는 인자 5개로 완전히 결정되므로 메모이즈한다 —
    위 _REL_RETIRE_MULT_CACHE 주석에 근거와 실측을 적어뒀다."""
    _ck = (ovr, grade, tier, country, max_tier)
    _cv = _REL_RETIRE_MULT_CACHE.get(_ck)
    if _cv is not None:
        return _cv
    from constants import get_ovr_range
    ref_tier = max(1, (max_tier + 1) // 2) if max_tier and max_tier >= 1 else tier
    ovr_rng = get_ovr_range(grade, ref_tier, country)
    if not ovr_rng or not ovr:
        _REL_RETIRE_MULT_CACHE[_ck] = 1.0
        return 1.0
    lo, hi = ovr_rng
    span = max(1.0, hi - lo)
    p = (ovr - lo) / span
    mult = _percentile_curve_mult(p)

    # 하위권(mult>1.0)만 부수 깊이로 게이팅 — 아직 내려갈 하위 리그가
    # 있으면 은퇴 압력을 25%만, 이미 그 나라 최심부면 전량 반영.
    if mult > 1.0 and max_tier and max_tier > 1 and tier < max_tier:
        mult = 1.0 + (mult - 1.0) * 0.25

    # [2026-09 신설] 위 _absolute_star_mult 정의부 주석 참고 — 리그를 이미
    # 초월한 선수(p>=1.0)에 한해 절대 OVR 기준 세계급 여부를 추가로 곱한다.
    mult *= _absolute_star_mult(ovr, p)

    # [2026-09 신설] 절대 OVR 층이 새로 생기면서 하한을 0.75→0.55로
    # 낮춘다 — 리그 대비 퍼센타일 하나만으론 0.75가 한계였지만, 이제
    # "리그도 초월 + 세계급 절대 OVR"이 동시에 확인된 극소수에게만 그
    # 아래(최저 0.55, 약 45% 감소)까지 열어준다. 그 외 대다수는 여전히
    # 기존 0.75~1.25 범위 그대로.
    _out = max(0.55, min(1.25, mult))
    _REL_RETIRE_MULT_CACHE[_ck] = _out
    return _out


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


# [2026-09 신설, 신민용 리포트: "헤드리스 실측 결과(top 40+생존 1.23%,
# bottom 0.76%)를 보니 상위 리그가 오히려 40대 생존율이 더 높다 — 리그가
# 낮을수록 40대 생존을 더 허용해야 하는데 반대로 나온다. TOP은 조금 더
# 희귀하게(40+ ~1.0%/42+ ~0.10%/44+ ~0.01%), BOTTOM은 2~3배 더 흔하게
# (40+ ~2.5~3.0%/42+ ~0.4~0.5%/44+ ~0.07~0.1%), 그 사이 MIDHIGH/MID/LOW는
# 완만하게 보간"] _AI_RETIRE_BAND_PCT(18~45세 전 구간)는 그대로 두고
# (사용자가 그쪽은 이미 만족), 40~44세 구간에만 카테고리별 배율을 얹는다
# — 45세는 여전히 무조건 강제은퇴(100%)라 안 건드림.
#
# [보정 방법] 실측 자료가 "현재 상태에서의 현역 스냅샷 생존율"뿐이라,
# 순수 나이표만으로 계산한 생존곡선(실제 상대OVR/국제경력 보정 전)과
# 실측값의 비율(comp)을 먼저 구하고, 목표 실측값을 comp로 나눠 "순수
# 나이표 기준 목표치"로 환산한 뒤 그 목표치에 맞는 배율을 역산했다
# (base·growth 두 파라미터로 40~44세 배율을 age별로 완만하게 키우거나
# 줄이는 지수곡선 mult(age)=base*growth^(age-40) 형태 — 이래야 42+/44+가
# 40+와 별개로 더 가파르게/완만하게 갈릴 수 있다. 단순히 40~44세 전체에
# 같은 배율 하나만 곱하면 세 체크포인트를 동시에 못 맞춘다는 걸 먼저
# 확인했다).
#
# [한계, 신민용에게 보고] bottom/low는 35~39세 해저드가 이미 꽤 높아서
# (표 자체는 안 건드리기로 함) 40세 생존자 모수 자체가 작다 — 40~44세
# 해저드를 0으로 만들어도(이론적 최댓값) bottom은 실측 환산 기준 약
# 1.5%가 한계라 목표(2.5~3.0%)에는 못 미친다. 아래 값은 40+/42+/44+
# 세 체크포인트를 로그공간 상대오차로 동시에 맞춘 결과(실측 환산 기준
# top 0.76%/0.18%/0.009%, midhigh 0.85%/0.23%/0.017%, mid 0.98%/0.29%/
# 0.027%, low 1.30%/0.42%/0.050%, bottom 2.01%/0.70%/0.075% — 목표 대비
# top~mid~low는 1.3~1.5배, bottom은 이론적 한계에 근접해 목표엔 못
# 미치지만 최대한 끌어올린 값) — bottom을 목표치(2.5~3.0%)까지 마저
# 끌어올리려면 35~39세 해저드도 카테고리별로 같이 낮춰야 한다.
_ELDER_LEAGUE_DIAL = {
    "top": (1.48, 1.10),
    "midhigh": (1.34, 1.10),
    "mid": (1.24, 1.10),
    "low": (1.14, 1.10),
    "bottom": (0.92, 1.20),
}


def _elder_league_mult(age, category):
    """40~44세 구간에서만 적용되는 카테고리별 해저드 배율. _ELDER_LEAGUE_
    DIAL 정의부 주석 참고 — base*growth^(age-40) 형태로, base는 40세
    시점 배율(작을수록 그 나이대 생존을 더 허용), growth는 41→44세로
    갈수록 배율이 얼마나 가파르게 커지는지(=더 빨리 희귀해지는지)를
    정한다. 범위 밖(45세 포함) 또는 등록 안 된 카테고리는 배율 1.0(무영향)."""
    if age < 40 or age > 44:
        return 1.0
    base, growth = _ELDER_LEAGUE_DIAL.get(category, (1.0, 1.0))
    return base * (growth ** (age - 40))


# [2026-09 신설, 신민용 리포트 14번] 30대 중반 이후의 "팀 내 역할 →
# 은퇴" 배율. 나이가 은퇴 확률의 기본 축이라면, 이건 "그 나이에 아직
# 팀에서 자리가 있는가"라는 두 번째 축이다.
#
# 원래는 신민용이 ②에서 말한 "시즌 출전시간"을 그대로 쓰려 했는데,
# 실측 결과 이 엔진에는 출전시간 축이 존재하지 않았다(전 선수 26~49경기,
# 0경기 0명 — 자세한 근거는 아래 _load_role_index 주석 참고). 그래서
# 화면에도 그대로 보이는 "역할"(주전/로테이션/대기/전력외)을 쓴다.
# 인덱스는 역할 0~3. 주전(0)은 1.0이라 "나이 들어도 여전히 주전인 선수"는
# 전혀 손해를 안 본다 — 노장 슈퍼스타는 그대로 남는다.
_RETIRE_BENCH_AGE = 33
_RETIRE_BENCH_W = (1.0, 1.0, 1.25, 1.70, 2.10, 1.0)   # 핵심/주전/로테/대기/전력외/유망주


def _ai_retirement_probability(age, ovr, position, category="mid", intl_factor=1.0,
                                relative_mult=1.0, career_finish_bonus=0.0,
                                role_i=None):
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
    하므로.
    [2026-09 2차, 신민용 리포트: "상위 리그일수록 40대 생존율이 낮고
    하위 리그일수록 높아야 하는데 실측이 반대로 나왔다"] 나이 해저드
    직후, 40~44세 구간에서만 _elder_league_mult(카테고리별 배율)를
    곱한다 — 순서는 "기본 연령 해저드 → 리그 강도 → 상대OVR → 국제경력"
    그대로."""
    if age < 18:
        return 0.0
    if age > 45:
        return 1.0
    p = _AI_RETIRE_HAZARD_TABLE.get(category, _AI_RETIRE_HAZARD_TABLE["mid"]).get(
        age, 1.0 if age >= 45 else 0.0)
    if 40 <= age <= 44:
        p *= _elder_league_mult(age, category)
    if age < 30:
        p *= intl_factor
    p *= relative_mult
    # [2026-09 신설, 리포트 14번 — 위 _RETIRE_BENCH_W 주석 참고] 33세
    # 이상에서만 적용한다. role_i가 None(역할을 못 구한 경우 — 스쿼드가
    # 비정상이거나 구버전 호출)이면 건너뛰므로 이번 변경 전과 완전히
    # 같이 동작한다. relative_mult 다음·career_finish_bonus 앞이라는
    # 순서도 의도적이다: "리그 에이스라 은퇴를 강하게 억제 중"인 선수라도
    # 정작 팀에서 전력외라면 그 억제가 상쇄돼야 하므로 곱셈 축에 둔다.
    if role_i is not None and age >= _RETIRE_BENCH_AGE:
        p *= _RETIRE_BENCH_W[role_i]
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
    # [2026-09 신설] potential_ovr 추가 — _build_buy_pools가 이제 시장구매
    # 후보의 잠재력을 가중치에 반영한다(위 _build_buy_pools 정의부 주석
    # 참고). 이 한 줄만 넓혀서 둘 다(retire/transfer) 별도 쿼리 없이 쓴다.
    # [2026-09 신설] quota_local_country 추가 — 클럽 외국인 쿼터 판정
    # (database.is_quota_foreign)에 필요. database.py 해당 컬럼 주석 참고.
    # [2026-09 신설] on_loan_from_team_id 추가 — 임대 중인 선수는 임대처가
    # 다른 팀으로 넘길 수 없다(FIFA 재임대·재이적 금지, _build_buy_pools/
    # _transfer_market 주석 참고).
    # [2026-10 병역 시스템 4단계] 나이 +1 직후, 이적시장보다 먼저: 제대 → 면제 →
    # 유형 재판정 → 새해 입대. 제대자는 아래 공유 목록에 들어가 이적시장에 참여하고,
    # 입대자는 빠진다. 기능이 꺼져 있으면 즉시 return(아무것도 안 함).
    from military_service import process_military_new_year as _mil_new_year
    _mil_new_year(c, year)
    shared_ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, quota_local_country, "
        "contract_end_year, last_transfer_year, potential_ovr, on_loan_from_team_id "
        "FROM ai_players ORDER BY id").fetchall()
    shared_ai_rows = _drop_mil_teams(c, shared_ai_rows, "team_id")   # [2026-10] 복무 중인 선수 제외
    _t_shared = _time_perf.perf_counter()

    # [2026-08 신설, 신민용 요청: "선수 검색에서 OVR이 이적 순간에만
    # 찍히던데, 1년 단위로 그 해 OVR이 다 찍혀있어야 한다"] 방금 _age_
    # and_progress로 이 해의 성장/노화가 전부 반영된 shared_ai_rows를
    # 그대로 재사용해서(추가 쿼리 없음) 전 선수 OVR을 한 번에 아카이브
    # 한다 — 은퇴 예정자도 이 시점엔 아직 ai_players에 남아있으므로
    # "은퇴하는 그 해"까지 정상적으로 기록된다.
    # [2026-09 신설, 히스토리 비동기 writer] 이전엔 여기서 c.executemany로
    # hist에 즉시 커밋했다 — 이제 "이 순간 완성된 행 목록"만 큐에 넘기고
    # 실제 커밋은 단일 워커가 담당한다(database.py 상단 히스토리 writer
    # 설계 불변식 참고). 이 아래 conn.commit()은 main 스키마 변경분만
    # 커밋한다 — hist는 더 이상 이 트랜잭션에 안 묶인다.
    from database import history_enqueue
    history_enqueue("ai_player_ovr_history",
                     [(r["id"], year, r["ovr"]) for r in shared_ai_rows])
    # [2026-10] 은퇴 목록 "최고 OVR"용 실제 최댓값 — database.update_ai_best_ovr 참고.
    from database import update_ai_best_ovr
    update_ai_best_ovr(c)

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

    # [2026-09 리팩터, 신민용 확정: "하반기 포메이션은 2차 라운드에 실제
    # 뛴 스쿼드를 보여줘야 한다"] 이 호출은 원래 여기(연도 완전히 끝난
    # 뒤, 은퇴/이적 "전")에 있었는데, game_engine._process_promotion_
    # relegation이 그보다 훨씬 이른 시점(43주차, 승강 확정 직후)에 부르는
    # apply_squad_turnover_after_movement(승강 스쿼드 개편 — 하위권 일부
    # 방출/교체)가 이미 로스터를 흔들어놓은 "뒤"였다. 그 결과 "하반기"
    # 스냅샷(team_season_lineup)이 실제 2차 라운드에 뛴 스쿼드가 아니라
    # "방출까지 끝난 시즌 종료 후" 스쿼드를 찍고 있었다(강등팀 후보가
    # 갑자기 9명으로 급감하는 버그의 근본 원인 — 60차 이후 세션에서
    # 진단). 이제 이 스냅샷(team_season_lineup + ai_player_position_
    # history)은 _process_promotion_relegation이 apply_squad_turnover_
    # after_movement를 부르기 "직전"(43주차, 리그 경기 다 끝나고 승강만
    # 확정된 시점)에서 대신 찍는다 — 방출로 새로 생긴 선수는 여전히
    # 아래 only_missing=True 2차 패스가 커버한다(그쪽은 이미 team_
    # season_lineup을 안 건드리도록 2026-09에 고쳐져 있어 안전).
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
    # [2026-09 이동, 신민용 확정: "임대처가 다시 이적 보내는 건 없어야 하지만
    # 여러 번 임대 다니는 선수는 있잖아"] 여러 번 임대는 현실에서 "임대가
    # 끝나 원 소속팀으로 돌아온 뒤, 원 소속팀이 같은 이적시장에서 다시
    # 임대를 보내는" 방식이다(임대처가 제3의 팀으로 넘기는 재임대는 금지 —
    # _do_one_transfer_cached/_build_buy_pools 주석 참고). 예전엔 이 복귀
    # 처리가 이적시장·스카우팅 "뒤"에 돌아서, 이번 오프시즌에 끝나는 임대
    # 선수는 이적시장 동안 여전히 임대처 소속으로 남아 있었다 — 그래서
    # 원 소속팀이 바로 다시 임대 보낼 수 없었다. 임대 만기는 시즌 종료
    # 시점이므로 은퇴 처리 직후, 아래 shared_ai_rows 재조회 "전"에 복귀를
    # 끝낸다 — 그래야 이적시장이 복귀한 선수를 원 소속팀 선수로 본다.
    loan_returned = _process_loan_returns(c, year)
    _ta2 = _time_perf.perf_counter()
    # [2026-08 버그수정] _retire_and_replace가 이제 은퇴자 행을 UPDATE가
    # 아니라 DELETE+INSERT로 처리하므로(위 함수 docstring 참고), 여기
    # shared_ai_rows(은퇴 처리 전에 떠둔 스냅샷)를 그대로 _transfer_market에
    # 넘기면 방금 삭제된 은퇴자의 옛 id가 섞여 있고 새로 태어난 신인은
    # 아예 빠져 있다 — 이적시장이 이미 사라진 행을 이적시키려 하거나
    # (조용히 무시되긴 하지만) 갓 생긴 신인은 이번 시즌 이적 후보에서
    # 통째로 누락된다. 은퇴 처리 직후 한 번 다시 조회해서 최신 상태로
    # 맞춘다.
    # [2026-09] salary 추가 — 이적시장의 잔류/방출 판단이 "고임금 대비
    # 기여도"를 보려면 필요하다(_transfer_market의 sal_pct 주석 참고).
    shared_ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, quota_local_country, "
        "contract_end_year, last_transfer_year, salary, on_loan_from_team_id "
        "FROM ai_players ORDER BY id").fetchall()
    shared_ai_rows = _drop_mil_teams(c, shared_ai_rows, "team_id")   # [2026-10] 복무 중인 선수 제외

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
    # [2026-09 신설, 신민용 리포트 40번/1번] 일반 이적시장 직후 —
    # "지금 소속 리그의 설계 수준을 명백히 뛰어넘은 선수"를 상위 무대로
    # 끌어올리는 전용 통로. 반드시 _transfer_market 다음, _rebalance_
    # squad_sizes(아래)보다 앞이어야 한다: 이 통로는 일방 이적이라
    # 하위팀 스쿼드가 한 명 비는데, 그 보충을 _rebalance_squad_sizes가
    # 같은 오프시즌 안에서 처리해줘야 하기 때문이다(함수 정의부 주석 참고).
    upward = _upward_transfer_pull(c, year)
    _ta3 = _time_perf.perf_counter()
    # [2026-09 신설, 신민용 요청: "명문팀은 상시로 좋은 선수를 스카우팅
    # 해야 한다 — 3급은 압도적인 선수, 2급/1급은 상대적으로 덜한 선수"]
    # 은퇴/이적시장과 완전히 별개인 상시 스카우팅 — _retire_and_replace는
    # 은퇴가 나야만 채우고, _transfer_market은 등급 무관 확률로 도는데,
    # 이건 "은퇴 여부와 무관하게 명문팀만 매 시즌 세계 최상위권을 노리는"
    # 전용 통로. 최신 team_id 반영이 필요해 자체 쿼리한다(위 shared_ai_rows
    # 재조회와 같은 이유).
    scouted    = _prestige_scouting(c, year)
    # [2026-09 신설, 신민용 요청: "잠재력이 team_cap에 종속되면 안 된다 —
    # 한국 하위팀 유망주도 잠재력만 있으면 해외 명문팀에 발굴돼 이적할 수
    # 있어야 한다"] _prestige_scouting 바로 다음 — 위 함수와 같은
    # "은퇴/이적시장과 무관한 상시 통로"라는 성격을 공유하지만, 보는
    # 기준(potential_ovr 격차 vs 현재 OVR 격차)이 달라 완전히 분리된
    # 함수·별도 호출로 둔다(하나로 합치면 두 기준이 서로의 후보 풀을
    # 오염시킬 위험이 있다).
    potential_scouted = _prestige_potential_scouting(c, year)
    # [2026-09 이동] 임대 복귀(_process_loan_returns)는 이적시장 앞(은퇴 처리
    # 직후)으로 옮겼다 — 그쪽 주석 참고.
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
    # [2026-09 신설, 신민용 요청: "선수의 한계치를 국가별로 최대 2명으로
    # 둬서 3명 이상이 안나오게"] database._apply_intl_breakout(국제대회
    # 소집 시 낮은 확률로 딱 한 명씩 "브레이크아웃"시키는 가산 장치)만으론
    # 이 상한이 실제로 지켜지지 않는다 — 국적은 소속 클럽과 무관하게
    # 무작위 배정되므로(_pick_nationality), 등급 낮은 나라 국적이 우연히
    # 강한 클럽에서 성장해 90+를 찍는 경로가 이 브레이크아웃 장치와
    # 완전히 별개로 원래부터 존재했다(실측: 헤드리스 4시즌 기준 크로아티아
    # (B등급, 상한5) 국적 90+가 64명까지 쌓여 있었음 — 전부 이 "우연한
    # 강클럽 배정" 경로, 브레이크아웃 장치가 만든 게 아님). 성장/이적이
    # 전부 끝난 이 시점에 등급별 상한을 실제로 강제한다 — 초과분은
    # 낮은 OVR부터(에이스 자리는 최대한 안 건드림) 90 밑으로 되돌린다.
    _enforce_intl_breakout_caps(c, year)
    _ensure_top_grade_star_floor(c, year)
    # [2026-09 신설, 신민용 리포트: "K리그에 외국인만 절반 이상인 팀도
    # 나온다"] 이적 시장 예방 필터(위 dst_quota_hi_by_tid/foreign_count_
    # by_tid)만으론 이미 예전 세이브에서 쿼터를 넘긴 팀이 되돌아오지
    # 않으므로, 위 _enforce_intl_breakout_caps와 같은 타이밍(성장·이적·
    # 스쿼드 인원보정이 전부 끝난 시점)에 전세계 단위 사후 보정도 같이 돈다.
    _enforce_foreign_quota_worldwide(c, year)

    # [2026-09 신설] 이번 오프시즌에 새로 생긴 선수(은퇴 교체 / 스쿼드 보충 /
    # 물갈이)의 주발을 여기서 채운다. 생성 지점마다 넣지 않는 이유는
    # database.assign_missing_feet 주석 참고 — id가 AUTOINCREMENT라
    # INSERT 시점엔 결정적 해시의 입력(player_id)이 없다. 멱등이라 이미
    # 값이 있는 선수는 건드리지 않는다.
    #
    # [위치가 중요] 바로 아래 _snapshot_season_positions가 그 해 슬롯
    # 배정을 찍는데, 주발 보정(formation_logic._foot_swap_pass)이 그
    # 배정에 관여한다 — 백필이 스냅샷보다 뒤에 있으면 이번 시즌 신입은
    # foot='' 상태로 배정돼 보정을 못 받는다(실측: 그 순서일 때 팀당
    # 평균 2.5명, 총 22,458명이 누락됐다).
    try:
        from database import assign_missing_feet
        assign_missing_feet(c)
    except Exception as _e:
        print(f"[FOOT] 주발 배정 실패(계속 진행): {_e}")

    _report(3, "포메이션 갱신 중")
    # [2026-09 신설 — 감독 시스템 ③단계] 포메이션 갱신 **직전**에 감독
    # 경질·부임을 처리한다. 순서가 중요하다 — 여기서 바뀐 감독이 바로
    # 아래 _shuffle_formations에서 자기 성향대로 포메이션을 다시 고르고,
    # 그 결과가 다시 선수 기용(_select_lineup)까지 이어진다.
    try:
        _mgr_changed = _manager_turnover(c, year)
        if _mgr_changed:
            _perf_log(f"[MANAGER] {year}년 감독 교체 {len(_mgr_changed)}팀")
    except Exception as _e:
        # 감독 교체가 실패해도 시즌 전환 자체는 계속 돌아야 한다.
        print(f"[MANAGER] 감독 교체 처리 실패(계속 진행): {_e}")
        _mgr_changed = set()
    formations = _shuffle_formations(c, forced_teams=_mgr_changed)
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
    # ── [2026-09 신설, 신민용 확정] 다음 시즌 역할을 여기서 확정한다 ──
    # 지금 이 지점은 "그 해 시즌이 완전히 끝나고, 은퇴·이적시장·상위리그
    # 발탁·명문 스카우팅·임대복귀·재계약·스쿼드 인원 보정·전술(포메이션)
    # 셔플까지 전부 끝난" 자리다 — 즉 다음 시즌을 시작할 로스터와
    # 포메이션이 최종 확정된 시점이고, 이보다 나은 "시즌 시작" 타이밍이
    # 없다(프리시즌 1~3주차에 따로 훅을 만들 필요 없이 여기 한 번이면 된다).
    #
    # 이 한 줄이 이번 변경의 핵심이다. 여태 역할은 43주차(리그가 다 끝난
    # 뒤)에 "그 시즌 OVR 순위"를 사후 요약한 결과였고, 그래서 아무것도
    # 결정하지 못하는 라벨이었다. 이제 시즌이 시작되기 전에 먼저 정해져서
    # 그 시즌 내내 이적/잔류/은퇴 판단의 입력으로 쓰인다 — 신민용이
    # 요구한 "역할 → 출전/가치 → 다음 시즌 역할 재평가" 흐름의 첫 고리다.
    # (출전량 생성은 다음 단계 작업이며 이번 범위에 없다.)
    try:
        _snapshot_season_positions(c, year + 1)
    except Exception as _e_next:
        _perf_log(f"[다음 시즌 역할 산정 오류] {_e_next}")
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
          f"상위리그 발탁 {upward}건 · "
          f"명문팀 스카우팅 {scouted}건 · 잠재력 발굴 {potential_scouted}건 · "
          f"임대 복귀 {loan_returned}명 · 재계약 {renewed}명 · "
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
        from database import history_enqueue
        history_enqueue("ai_player_ovr_history",
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
    # [2026-09] salary 추가 — 오프시즌 쪽(run_ai_offseason의 shared_ai_rows)과
    # 같은 이유. 겨울 이적창구도 같은 _transfer_market을 쓰므로 "고임금 대비
    # 기여도" 판단에 필요하다(빠지면 sal_pct가 전부 중립이 되어 그 축만
    # 조용히 꺼진다).
    ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, quota_local_country, "
        "contract_end_year, last_transfer_year, salary, on_loan_from_team_id "
        "FROM ai_players ORDER BY id").fetchall()
    ai_rows = _drop_mil_teams(c, ai_rows, "team_id")   # [2026-10] 복무 중인 선수 제외
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
    # [2026-10 병역 시스템] 군팀은 "환경 상한"이 없다 — 복무 중에도 평소처럼
    # 개인 잠재력(potential_ovr)까지만 성장/노화한다(신민용 확정: 군대라서
    # 오르거나 깎이는 개념 없음). 군대가 없으면 빈 집합이라 아무 변화 없음.
    from military_service import get_military_team_ids as _get_mil_tids_cap
    for _mt in _get_mil_tids_cap(c):
        team_cap[_mt] = 99
    _ap_t1 = _time_ap.perf_counter()

    # JOIN에 안 잡힌 팀(league_id/country_id 연결 누락 등)의 폴백 상한.
    _ORPHAN_CAP_FALLBACK = 46

    rows = c.connection.cursor()
    rows.row_factory = None  # 위치 접근만 쓰므로 Row 래핑 생략 (5.9만 행 fetch 오버헤드 절감)
    # [2026-09 신설] ovr(하락 전 현재값)·peak_ovr(전성기 기준점) 추가 —
    # 목표OVR 기반 노화 재설계(_AGING_DECLINE_SCHEDULE 정의부 주석 참고)에 필요.
    # [2026-09 버그수정, 신민용 리포트: "OVR을 100으로 편집했더니 수치가
    # 서서히 내려가더라 — 한계 재능(피크 기준점)을 안 올리고 숫자만
    # 올린 거라 그런 거 아니냐"] 정확했다 — ovr_user_locked도 같이
    # 가져와 아래에서 잠긴 선수는 이 함수(성장/피크/노화 전부)가 아예
    # 손대지 않도록 뺀다. rescale_ai_player_to_target_ovr 정의부의
    # "나이와 무관하게 그대로 반영한다"는 원래 의도였는데, 정작 이
    # 시즌 전환 엔진이 ovr_user_locked를 전혀 안 봐서, 노화기(30세+)
    # 선수를 편집하면 다음 시즌부터 peak_ovr(편집 전에 이미 확정돼
    # 있던 옛 전성기 기준점) 대비 목표OVR로 서서히 깎여 되돌아갔다.
    # [2026-09 신설] potential_ovr 추가 — 개인별 "전성기 도달 가능 상한"
    # (database.roll_potential_ovr 정의부 주석 참고). 끝에 붙여서 기존
    # r[19]/r[20]/r[21](ovr/peak_ovr/ovr_user_locked) 인덱스는 그대로 둔다.
    rows = rows.execute(
        "SELECT id, position, age, team_id, " + _STAT_COLS +
        ", ovr, peak_ovr, ovr_user_locked, potential_ovr FROM ai_players").fetchall()
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
    # [2026-09 버그수정] calc_ovr_from_list는 순수 파이썬 버전
    # (_age_and_progress)에만 지역 import가 있었는데, 아래 노화기 폴백
    # (peak_ovr<=0 이면서 ovr<=0 인 행 — 구세이브/손상 행에서만 나오는
    # 드문 분기)에서도 쓴다. 그래서 그 분기를 타는 순간 NameError로
    # 시즌 전환이 죽었다. 평소엔 안 걸려서 실행으로는 안 잡히고 pyflakes
    # 정적 검사로만 드러난 종류의 버그다.
    from database import _WEIGHT_SUMS, calc_ovr_from_list
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
    # [2026-09 버그수정] 위 SELECT 확장 참고 — 잠긴(ovr_user_locked=1)
    # 선수는 아래 growth/peak/aging 세 분기 중 어디에도 들어가지 않게
    # 마스크에서 제외한다(나이 증가·ovr 재계산 자체는 그대로 받되,
    # 스탯 값은 전혀 안 바뀌므로 재계산해도 편집 당시 값 그대로 나옴).
    locked_arr = np.array([bool(r[21]) for r in rows])

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

    # [2026-09 신설, database.roll_potential_ovr 정의부 주석 참고] 실제
    # 성장 목표는 team_cap(팀/리그 단위 환경 상한)이 아니라
    # min(team_cap, potential_ovr)(개인별 전성기 도달 가능 상한) —
    # "어느 팀에 있느냐"가 아니라 "이 선수가 어떤 재목이냐"가 최종
    # 도달치를 가르게 한다. potential_ovr<=0(구버전 세이브가 아직 백필
    # 전이거나 이 값 자체가 없는 극히 드문 경우)이면 team_cap을 그대로
    # 써서 하위호환을 유지한다.
    potential_arr = np.array([(r[22] or 0) for r in rows], dtype=np.int64)
    cap_by_row = np.where(potential_arr > 0, np.minimum(cap_by_row, potential_arr), cap_by_row)

    new_age = ages + 1
    growth_mask = (new_age <= _AI_PEAK_START) & ~locked_arr
    peak_mask = (new_age > _AI_PEAK_START) & (new_age <= _AI_PEAK_END) & ~locked_arr
    # [2026-09 버그수정 2차, 신민용 리포트: "35살에 100을 입력하면 100이
    # 아니라 나이에 맞게 노화된 값이 자동저장되는게 맞지 않나?"] 성장기/
    # 피크기는 편집값을 계속 보호해야 하지만(그 이유는 growth_mask/
    # peak_mask 주석 및 rescale_ai_player_to_target_ovr 정의부 참고),
    # 노화기는 반대다 — rescale_ai_player_to_target_ovr이 이제 편집
    # 시점에 peak_ovr을 새 값으로 정확히 갱신해두므로, 그 peak을 기준으로
    # 이후 시즌에도 계속 자연스럽게(나이가 들수록 더) 깎여나가는 게
    # 의도된 동작이다 — aging_mask는 더 이상 잠긴 선수를 제외하지 않는다.
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
            # [2026-09 버그수정, 헤드리스 1시즌 테스트로 발견: "생성 직후엔
            # 위반이 0이었는데 1시즌 지나니 7,264명이 ovr>potential_ovr"]
            # 절대 상한 99만 쓰면(위 설계 의도상 team_cap 변동과는 무관하게
            # 유지하되) 개인별 potential_ovr보다 위로 슬금슬금 넘어갈 수
            # 있다 — potential_ovr은 team_cap과 달리 승격/강등으로 안
            # 흔들리는 개인 고유값이라, 여기서 상한으로 같이 써도 위
            # 원래 의도(피크기가 팀 사정과 무관해야 한다)를 전혀 해치지
            # 않는다.
            _peak_cap = np.minimum(99, np.where(potential_arr > 0, potential_arr, 99))[act_idx]
            vals_arr[act_idx, chosen] = np.clip(cur + delta, 15, _peak_cap)

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
        # [2026-09] _aging_eff_decline_pct 한 곳으로 통일 — 나이대별 개인
        # 지터까지 포함한다(파이썬 순회 횟수는 기존과 동일하게 2회).
        mods = np.array([_mgmt_tier_and_mult(int(p))[1] for p in pids_arr_full[idxs]])
        eff_pct = np.array([_aging_eff_decline_pct(int(p), int(a))
                            for p, a in zip(pids_arr_full[idxs], ages_i)])
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
        # [2026-09 신설] 위 numpy 버전과 동일 — database.roll_potential_ovr
        # 정의부 주석 참고. 개인별 potential_ovr이 있으면(>0) team_cap과
        # 함께 더 낮은 쪽을 실제 성장 목표로 쓴다.
        _potential = r[22] or 0
        if _potential > 0 and _potential < _cap:
            _cap = _potential
        vals = [v or 50 for v in r[4:19]]
        cur_ovr_val = r[19] or 0
        peak_ovr_val = r[20] or 0
        locked = bool(r[21])
        keys = KEY_STATS_BY_POS.get(pos, _default_keys)
        nonkeys = _NONKEY_STATS_BY_POS.get(pos)
        if nonkeys is None:
            nonkeys = [s for s in ALL_STATS if s not in keys]
            _NONKEY_STATS_BY_POS[pos] = nonkeys

        # [2026-09 버그수정 2차, 신민용 리포트: "35살에 100을 입력하면
        # 100이 아니라 나이에 맞게 노화된 값이 자동저장되는게 맞지
        # 않나?"] 성장기/피크기는 편집값을 계속 보호하지만(그 이유는
        # growth_mask/peak_mask 관련 numpy 버전 주석 및 database.
        # rescale_ai_player_to_target_ovr 정의부 참고), 노화기(else
        # 분기)는 잠금과 무관하게 그대로 돈다 — rescale_ai_player_to_
        # target_ovr이 편집 시점에 peak_ovr을 이미 정확히 갱신해두므로,
        # 그 peak 기준으로 이후 시즌에도 계속 자연스럽게 깎여나가는 게
        # 이제 의도된 동작이다.
        if locked and new_age <= _AI_PEAK_END:
            pass
        elif new_age <= _AI_PEAK_START:
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
            # [2026-09 버그수정] 위 numpy 버전과 동일 — potential_ovr(개인
            # 고유값, 승격/강등과 무관)도 같이 상한으로 써서 "잠재력보다
            # 위로 슬금슬금 넘어가는" 걸 막는다.
            _peak_cap = min(99, _potential) if _potential > 0 else 99
            if _random() < 0.3:
                s = _choice(ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = min(_peak_cap, max(15, vals[i] + _choice([-1, 1, 1])))
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
def _loan_years_by_pair(c, player_ids=None):
    """[2026-09 신설, 신민용 확정: "FIFA 규정 — 동일 클럽 간 임대는 최대
    2년"] {(player_id, 원 소속팀, 임대처): 누적 임대 연수}. ai_transfer_log의
    임대 행(is_loan=1)만 본다 — 최초 임대는 (loan_return_year - year)년,
    "임대 연장" 행은 1년씩 더한다(연장 1회 = 1시즌). 기간 단위는 loan_
    return_year와 같은 "로그 year 차이"라 오프시즌/겨울 임대 모두 같은
    기준이다. ai_transfer_log는 최근 5시즌을 보존하므로(AI_TRANSFER_LOG_
    RETENTION_SEASONS) 2년 상한 판정에는 충분하다. player_ids를 주면 그
    선수들만(500개씩 청크) 읽는다."""
    out = {}
    base = ("SELECT player_id, from_team_id, to_team_id, year, loan_return_year, transfer_type "
            "FROM ai_transfer_log WHERE is_loan=1")
    if player_ids is None:
        chunks = [None]
    else:
        _ids = list(player_ids)
        chunks = [_ids[_k:_k + 500] for _k in range(0, len(_ids), 500)]
    for _part in chunks:
        if _part is None:
            rows = c.execute(base).fetchall()
        elif not _part:
            continue
        else:
            rows = c.execute(base + f" AND player_id IN ({','.join('?' * len(_part))})",
                             _part).fetchall()
        for r in rows:
            _k = (r[0], r[1], r[2])
            if r[5] == "임대 연장":
                _yrs = 1
            else:
                _yrs = max(1, (r[4] or (r[3] + 1)) - r[3])
            out[_k] = out.get(_k, 0) + _yrs
    return out


# ─────────────────────────────────────────────
# [2026-10 신설, 신민용 확정] 임대 후 완전 이적(AI)
# ─────────────────────────────────────────────
# 임대 만기에 복귀가 확정된 선수 중, 임대처가 그대로 완전 영입하는 경로.
# 사전 조건(전부 충족해야 판정):
#   - 31세 이하
#   - 원 소속팀 비주전: 원 소속팀 로스터(본인 제외) OVR 11번째보다 낮음
#     (11명이 안 되면 바로 주전이라 원 소속팀이 데려간다)
#   - 23세 이하는 잠재력(potential_ovr)도 그 11번째보다 낮아야 함
#     (잘 크고 있는 유망주는 원 소속팀이 다시 부른다)
#   - 그 시즌 평점 기록이 있어야 함(없으면 성과를 모르므로 판정 안 함)
# 확률은 constants.loan_buy_probability — 임대 마지막 시즌 평균평점 구간 +
# 임대처 역할(로테이션 하단 / 주전 중간 / 핵심 상단, 대기·전력외·유망주는
# 평점 무관 0~5%) + 나이 보정, 최대 60%. "임대처 선발 11명" 사전 조건은
# 신민용 확정으로 빼고 역할 축이 그 역할을 맡는다.
# AI는 개인 경기 기록이 없어(출전수=팀 경기수 공통) 출전비율 대신 역할
# 라벨(hist.ai_player_position_history.role, 43주 스냅샷)을 쓴다 — 평점
# 추정치도 같은 역할에서 나오므로 두 축을 가중합하면 같은 신호를 두 번
# 세게 된다. 그래서 평점은 구간(범위)만, 역할은 범위 안 위치만 정한다.
# [2026-10 개정] AI 평점 구간은 constants.AI_LOAN_BUY_RATING_BANDS(실측 분위수
# 5.60/6.00/6.40/6.75). 역할은 판정 기준이 아니라 구간 안 위치만 정하고,
# 전력외는 0%, 대기·유망주(역할 없음 포함)는 0~1% 안에서 평점 구간 순서로 위치.


def _prepare_loan_buy_context(c, year, rows):
    """임대 만기 선수(rows)의 판정 재료를 한 번에 읽는다."""
    ctx = {"rating": {}, "role": {}, "parent_cut": {}, "team_info": {},
           "stat": {"eligible": 0, "no_rating": 0, "parent_needs": 0, "too_old": 0,
                    "band": {}, "probs": []}}
    if not rows:
        return ctx
    ids = [r["id"] for r in rows]
    try:
        from database import history_drain
        history_drain()
    except Exception:
        pass
    for _k in range(0, len(ids), 500):
        _part = ids[_k:_k + 500]
        _ph = ",".join("?" * len(_part))
        try:
            for _r in c.execute(
                    f"SELECT player_id, rating FROM hist.ai_player_season_stats "
                    f"WHERE year=? AND player_id IN ({_ph})", (year, *_part)).fetchall():
                ctx["rating"][_r[0]] = _r[1]
            for _r in c.execute(
                    f"SELECT player_id, role FROM hist.ai_player_position_history "
                    f"WHERE year=? AND player_id IN ({_ph})", (year, *_part)).fetchall():
                ctx["role"][_r[0]] = _r[1] or ""
        except Exception:
            pass
    parents = sorted({r["on_loan_from_team_id"] for r in rows if r["on_loan_from_team_id"]})
    for _k in range(0, len(parents), 500):
        _part = parents[_k:_k + 500]
        _ph = ",".join("?" * len(_part))
        _by_team = {}
        for _r in c.execute(
                f"SELECT team_id, ovr FROM ai_players WHERE team_id IN ({_ph})", _part).fetchall():
            _by_team.setdefault(_r[0], []).append(_r[1] or 0)
        for _tid in _part:
            _ovrs = sorted(_by_team.get(_tid, []), reverse=True)
            # 11명 미만이면 누구든 주전 → 원 소속팀이 데려간다(컷 = 0)
            ctx["parent_cut"][_tid] = _ovrs[10] if len(_ovrs) >= 11 else 0
    hosts = sorted({r["team_id"] for r in rows if r["team_id"]})
    for _k in range(0, len(hosts), 500):
        _part = hosts[_k:_k + 500]
        _ph = ",".join("?" * len(_part))
        for _r in c.execute(
                f"""SELECT t.id, t.name, t.current_tier AS tier, cn.name AS cname
                    FROM teams t JOIN leagues l ON t.league_id=l.id
                    JOIN countries cn ON l.country_id=cn.id WHERE t.id IN ({_ph})""",
                _part).fetchall():
            ctx["team_info"][_r["id"]] = (_r["cname"], _r["name"], _r["tier"] or 1)
    return ctx


def _loan_buy_roll(ctx, r):
    """완전 이적이면 그 확률(float), 아니면 None. 사전 조건을 통과한 선수에게만
    난수를 1회 쓴다."""
    from constants import (LOAN_BUY_MAX_AGE, LOAN_BUY_YOUNG_MAX_AGE,
                           LOAN_BUY_ROLE_POS, loan_buy_probability,
                           AI_LOAN_BUY_RATING_BANDS, AI_LOAN_BUY_BENCH_RANGE,
                           AI_LOAN_BUY_ZERO_ROLES)
    st = ctx["stat"]
    age = r["age"] or 25
    if age > LOAN_BUY_MAX_AGE:
        st["too_old"] += 1
        return None
    if r["team_id"] not in ctx["team_info"]:
        return None
    cut = ctx["parent_cut"].get(r["on_loan_from_team_id"], 0)
    if (r["ovr"] or 0) >= cut:
        st["parent_needs"] += 1
        return None
    if age <= LOAN_BUY_YOUNG_MAX_AGE and (r["potential_ovr"] or 0) >= cut:
        st["parent_needs"] += 1
        return None
    rating = ctx["rating"].get(r["id"])
    if rating is None:
        st["no_rating"] += 1
        return None
    role = ctx["role"].get(r["id"], "")
    if role in LOAN_BUY_ROLE_POS:
        prob = loan_buy_probability(rating, LOAN_BUY_ROLE_POS[role], age,
                                    bands=AI_LOAN_BUY_RATING_BANDS)
        band = f"{_rating_band_label(rating)}"
    elif role in AI_LOAN_BUY_ZERO_ROLES:
        prob = 0.0
        band = "전력외"
    else:
        # 대기·유망주: 평점 구간이 높을수록 0~1% 안에서 위로(최저 구간 0, 최고 구간 1)
        _n = len(AI_LOAN_BUY_RATING_BANDS)
        _idx = next((i for i, b in enumerate(AI_LOAN_BUY_RATING_BANDS) if (rating or 0) >= b[0]), _n - 1)
        _pos = (_n - 1 - _idx) / float(_n - 1)
        prob = loan_buy_probability(rating, _pos, age, bench=True,
                                    bench_range=AI_LOAN_BUY_BENCH_RANGE)
        band = "대기/유망주"
    st["eligible"] += 1
    _b = st["band"].setdefault(band, [0, 0])
    _b[0] += 1
    st["probs"].append(prob)
    if prob > 0 and random.random() < prob:
        _b[1] += 1
        return prob
    return None


def _rating_band_label(rating):
    from constants import AI_LOAN_BUY_RATING_BANDS
    r = rating or 0.0
    _floor = AI_LOAN_BUY_RATING_BANDS[-2][0]
    for _min, _lo, _hi in AI_LOAN_BUY_RATING_BANDS:
        if r >= _min:
            return f"평점{_min:.2f}+" if _min > 0 else f"평점{_floor:.2f}미만"
    return f"평점{_floor:.2f}미만"


def _execute_loan_buys(c, year, cur_season, bought, ctx):
    """완전 이적 확정 — 임대처와 새 계약(연봉·기간), 원 소속팀엔 이적료.
    원 소속팀 계약이 이미 끝났으면(만료 연도 <= 올해) 이적료 0(자유계약).
    ai_transfer_log엔 "완전 이적"(원 소속팀 → 임대처, is_loan=0, 오프시즌
    이라 발효 year+1)으로 남겨 세계기록실이 "임대 → 완전 이적" 구간으로
    이어 그리고 재정 집계에도 들어가게 한다."""
    from constants import (get_country_league_grade, get_ovr_range,
                           AI_CONTRACT_RENEWAL_DURATION_YEARS)
    from economy import estimate_transfer_fee
    updates, logs = [], []
    for r, _prob in bought:
        cname, tname, tier = ctx["team_info"][r["team_id"]]
        grade = get_country_league_grade(cname)
        ovr = r["ovr"] or 0
        age = r["age"] or 25
        salary = _calc_ai_salary(grade, tier, ovr, cname, tname, r["team_id"], year)
        _rng = get_ovr_range(grade, tier, cname)
        ceiling = _rng[1] if _rng else 43
        new_cend = year + random.randint(*_ai_contract_duration_range(
            age, ovr, ceiling, default=AI_CONTRACT_RENEWAL_DURATION_YEARS))
        if (r["contract_end_year"] or 0) <= year:
            fee = 0
        else:
            try:
                fee = estimate_transfer_fee(grade, tier, ovr, country=cname,
                                            position=r["position"], year=year) or 0
            except Exception:
                fee = 0
        updates.append((new_cend, salary, year, r["id"]))
        logs.append((cur_season, year, r["id"], r["name"], r["position"], age, ovr,
                     r["on_loan_from_team_id"], r["team_id"], 0, 0, 0.0, 0.0,
                     "완전 이적", 0, "", fee, 0, 0, salary, new_cend))
    c.executemany(
        "UPDATE ai_players SET on_loan_from_team_id=0, loan_return_year=0, "
        "contract_end_year=?, salary=?, last_transfer_year=? WHERE id=?", updates)
    c.executemany(
        """INSERT INTO ai_transfer_log(
            season, year, player_id, player_name, player_position, player_age, player_ovr,
            from_team_id, to_team_id, from_team_prestige, to_team_prestige,
            from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
            fee, is_loan, loan_return_year, salary, contract_end_year)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", logs)


def _log_loan_buy_stats(year, ctx, bought):
    """헤드리스 검증용 계측 — 평점 구간별 (판정 인원, 완전 이적 인원)."""
    st = ctx["stat"]
    if not (st["eligible"] or st["parent_needs"] or st["no_rating"]):
        return
    _avg_p = (sum(st["probs"]) / len(st["probs"])) if st["probs"] else 0.0
    _bands = " · ".join(f"{k} {v[1]}/{v[0]}" for k, v in sorted(st["band"].items()))
    _perf_log(f"[LOAN-BUY] {year}년 판정 {st['eligible']}명 → 완전 이적 {len(bought)}명 "
              f"(평균확률 {_avg_p*100:.1f}%) | 제외: 원소속 주전급 {st['parent_needs']} · "
              f"31세 초과 {st['too_old']} · 평점없음 {st['no_rating']} | {_bands}")


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
        "contract_end_year, nationality, potential_ovr FROM ai_players "
        "WHERE on_loan_from_team_id != 0 AND loan_return_year <= ?", (year,)).fetchall()
    rows = _drop_mil_teams(c, rows, "team_id")   # [2026-10] 복무자는 일반 임대 복귀가 아니라 제대 처리
    if not rows:
        return 0
    # [2026-09 버그수정, 신민용 리포트: "5명 한계인데 8명으로 뚫었잖아"]
    # 임대 복귀는 원 소속팀의 외국인 쿼터를 전혀 안 봤다 — 해외로 임대
    # 보낸 외국인이 한꺼번에 돌아오면 그대로 쿼터를 넘긴다(실측 1시즌
    # +167팀). 현실 축구가 그렇듯 "자리가 없으면 임대를 1년 더 연장"
    # 으로 처리한다. 단 30세 이상은 무한 임대가 어색하므로 그냥 복귀
    # 시키고(초과분은 _enforce_foreign_quota_worldwide가 사후 정리),
    # 또 이 게이트는 "돌아올 자리가 없다"만 보므로 자국 선수 복귀는
    # 예전과 100% 동일하게 그대로 처리된다.
    _fq_now, _fq_can_take, _fq_note = _build_foreign_quota_gate(c)
    # [2026-09 버그수정, 신민용 리포트: "3시즌 돌리면 GK가 아예 없는 팀이
    # 13개 생긴다"] 계측(tools/gk_zero_qa.py)으로 이 함수가 발생 지점 중
    # 가장 큰 쪽으로 확정됐다(3시즌 GK0 순증 +22). 원인: 임대 복귀는
    # 원 소속팀(_parent) 사정만 보고 임대처(r["team_id"], 선수가 지금
    # 뛰고 있는 팀)는 전혀 안 봤다 — 임대처의 유일한 GK가 임대 선수였다면
    # 복귀와 동시에 그 팀은 GK 0명이 된다(위 docstring이 "임대처 스쿼드
    # 보정은 다음 _rebalance_squad_sizes가 알아서 처리한다"고 적어둔 전제가
    # 포지션 구성에는 성립하지 않았다 — 총원이 정상범위면 그 함수는
    # 스왑 분기로 가고, 스왑은 '과다 그룹'이 있어야만 발동한다).
    # 처리 방식은 바로 아래 외국인 쿼터 게이트와 완전히 같다: 현실 축구가
    # 그렇듯 "당장 내보낼 수 없으면 임대를 1년 더 연장"한다. 임대처가
    # 다른 GK를 구하는 순간 이 조건은 저절로 풀리므로 영구 임대로 굳지
    # 않는다(그리고 _rebalance_squad_sizes의 GK 0명 절대보정도 이번에
    # 같이 넣었다).
    _loan_grp_ct: dict = {}
    _loan_pos_ct: dict = {}
    _host_ids = {r["team_id"] for r in rows if r["team_id"]}
    if _host_ids:
        _ph2 = ",".join("?" * len(_host_ids))
        for _r in c.execute(
                f"SELECT team_id, position FROM ai_players WHERE team_id IN ({_ph2})",
                tuple(_host_ids)).fetchall():
            _g = _POS_GROUP.get(_r["position"], "FW")
            _loan_grp_ct[(_r["team_id"], _g)] = _loan_grp_ct.get((_r["team_id"], _g), 0) + 1
            _loan_pos_ct[(_r["team_id"], _r["position"])] = \
                _loan_pos_ct.get((_r["team_id"], _r["position"]), 0) + 1

    # [2026-09 신설, 신민용 리포트: "39세에 2년 임대로 갔는데 41세에 임대
    # 연장 같은 것도 안 뜨고 52세까지 그 팀에 있다"] 아래 두 연장 분기는
    # loan_return_year만 조용히 +1 하고 아무 기록도 안 남겼다 — 세계
    # 기록실에서는 "임대 기간이 끝났는데 복귀도 연장도 없이 계속 남아
    # 있는" 것처럼 보였다(52세 자체의 원인은 _retire_and_replace의
    # NameError로 오프시즌 전체가 중단된 별도 버그였음). 정책(연장 조건)은
    # 그대로 두고, 연장 사유를 구분해 세고 ai_transfer_log에 "임대 연장"
    # 한 줄을 남긴다. 사유 코드: quota(원 소속팀 외국인 쿼터 부족) /
    # host_grp(임대처의 그 포지션 그룹 마지막 선수) / host_pos(그룹엔 다른
    # 선수가 있지만 정확히 같은 포지션은 마지막) — host_pos가 실제로 얼마나
    # 걸리는지 보고 조건 유지 여부를 판단하기 위한 계측용 구분이다.
    # [2026-09 신설, 신민용 확정: "FIFA 규정 — 동일 클럽 간 임대는 최대 2년,
    # 11년 동안 임대로 써진 경우도 있다"] 위 두 연장 사유(쿼터/임대처 마지막
    # 포지션)는 조건만 맞으면 매년 다시 연장돼 횟수 제한이 없었다. 이제
    # 이 선수·원 소속팀·임대처 조합의 누적 임대 연수(_loan_years_by_pair)
    # 에 1년을 더해 AI_LOAN_MAX_TOTAL_YEARS를 넘으면 사유와 무관하게
    # 복귀시킨다(사유 "cap"으로 따로 센다). 누적 기록을 못 찾으면(구세이브·
    # 보존기간 밖) 이미 오래된 임대로 보고 연장하지 않는다. 상한 때문에
    # 복귀해 비는 임대처 포지션은 _rebalance_squad_sizes(GK 0명 절대보정
    # 포함)가 채운다.
    from constants import AI_LOAN_MAX_TOTAL_YEARS
    _loan_yrs = _loan_years_by_pair(c, [r["id"] for r in rows])
    _returning, _extend = [], []
    _extend_rows = []                # (r, reason)
    _buy_ctx = _prepare_loan_buy_context(c, year, rows)
    _bought = []                     # (r, prob)
    _extend_reason_ct = {"quota": 0, "host_grp": 0, "host_pos": 0, "cap": 0}
    for r in rows:
        _parent = r["on_loan_from_team_id"]
        _ext_ok = (_loan_yrs.get((r["id"], _parent, r["team_id"]), AI_LOAN_MAX_TOTAL_YEARS) + 1
                   <= AI_LOAN_MAX_TOTAL_YEARS)
        _capped = False
        if (r["age"] or 25) < 30 and not _fq_can_take(_parent, r["nationality"], ""):
            if _ext_ok:
                _extend.append(r["id"])
                _extend_rows.append((r, "quota"))
                _extend_reason_ct["quota"] += 1
                continue
            _capped = True
        # 임대처의 마지막 GK(또는 마지막 CB 등)면 복귀 대신 임대 1년 연장
        _host = r["team_id"]
        _hg = _POS_GROUP.get(r["position"], "FW")
        if _host and (_loan_grp_ct.get((_host, _hg), 0) <= 1
                      or _loan_pos_ct.get((_host, r["position"]), 0) <= 1):
            if _ext_ok:
                _extend.append(r["id"])
                _reason = "host_grp" if _loan_grp_ct.get((_host, _hg), 0) <= 1 else "host_pos"
                _extend_rows.append((r, _reason))
                _extend_reason_ct[_reason] += 1
                continue
            _capped = True
        if _capped:
            _extend_reason_ct["cap"] += 1
        # [2026-10 신설] 임대 후 완전 이적 — 복귀가 확정된 선수(연장 아님)만
        # 판정한다. 영입되면 임대처에 그대로 남으므로 임대처 포지션 카운트도,
        # 원 소속팀 외국인 쿼터 카운트도 건드리지 않는다.
        _bp = _loan_buy_roll(_buy_ctx, r)
        if _bp is not None:
            _bought.append((r, _bp))
            continue
        if _host:
            _loan_grp_ct[(_host, _hg)] = _loan_grp_ct.get((_host, _hg), 0) - 1
            _loan_pos_ct[(_host, r["position"])] = \
                _loan_pos_ct.get((_host, r["position"]), 0) - 1
        _fq_note(_parent, r["nationality"], "")
        _returning.append(r)
    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 1
    if _extend:
        c.executemany("UPDATE ai_players SET loan_return_year=? WHERE id=?",
                      [(year + 1, _pid) for _pid in _extend])
        # 연장 기록 — 방향은 원래 임대와 같게(원 소속팀 → 임대처), is_loan=1
        # (그래야 get_ai_player_team_timeline이 다음 해도 "임대 중" 구간으로
        # 이어 그린다), 오프시즌이라 is_mid_season=0(발효 year+1 — 정확히
        # 연장된 그 한 시즌), loan_return_year는 새 복귀 예정 연도.
        # 이적료 0 — 재정 집계에선 world_browser._FINANCE_EXCLUDED_TYPES로
        # 제외한다(새 거래가 아니라 기존 임대가 이어지는 것이므로).
        c.executemany(
            """INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(_cur_season, year, _er["id"], _er["name"], _er["position"], _er["age"] or 25,
              _er["ovr"], _er["on_loan_from_team_id"], _er["team_id"], 0, 0, 0.0, 0.0,
              "임대 연장", 0, "", 0, 1, year + 1, _er["salary"] or 0,
              _er["contract_end_year"] or 0) for _er, _rsn in _extend_rows])
    if _bought:
        _execute_loan_buys(c, year, _cur_season, _bought, _buy_ctx)
    _perf_log(f"[LOAN] {year}년 임대 만기 {len(_extend) + len(_returning) + len(_bought)}명: "
              f"복귀 {len(_returning)} · 연장 {len(_extend)} · 완전 이적 {len(_bought)} "
              f"(쿼터 {_extend_reason_ct['quota']} / 임대처 그룹마지막 "
              f"{_extend_reason_ct['host_grp']} / 임대처 포지션마지막 "
              f"{_extend_reason_ct['host_pos']}) · 2년 상한으로 복귀 {_extend_reason_ct['cap']}")
    _log_loan_buy_stats(year, _buy_ctx, _bought)
    rows = _returning
    if not rows:
        return 0
    updates = [(r["on_loan_from_team_id"], r["id"]) for r in rows]
    c.executemany(
        "UPDATE ai_players SET team_id=?, on_loan_from_team_id=0, loan_return_year=0 WHERE id=?",
        updates)
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


def _ai_contract_duration_range(age, ovr=None, ovr_ceiling=None, default=(2, 5)):
    """[2026-09 1차, 신민용 요청: "35세 이상도 계약을 4년 가까이 할 때가
    있는데 얘네는 1~2년 계약을 하며 연장이 맞고 39는 1년씩... 34세 이상도
    2~3년... 뛰어난 에이스면 장기도 가능"] 나이가 들수록 계약 기간이
    짧아져야 하는데, 재계약도 이적 신규계약도 나이와 무관하게 고정
    범위에서만 뽑아 39세가 4~5년 계약을 맺는 일이 있었다 — 1차로 34세
    이상 구간만 나이별로 낮췄다.

    [2026-09 2차 전면 재설계, 신민용 확정 — 현실 축구 계약 관행 매트릭스
    그대로: "젊을수록 길게, 나이 들수록 짧게(단년 위주)가 불문율. 20~25세
    저~중 OVR 3~4년/에이스 4~5년, 26~30세 주전급 3년/에이스 4~5년,
    31~34세는 품질 무관 1~2년(에이징 커브 리스크), 35세 이상은 품질
    무관 무조건 1년 — 39세 OVR78처럼 리그 기준 에이스라도 절대 예외
    없음"] 1차 버전의 "에이스는 나이 무관 장기 가능" 예외를 35세 이상
    에서는 완전히 제거하고(신민용이 직전 테스트 결과였던 "39세 OVR78
    에이스 → 5년"을 직접 반례로 들며 명시적으로 뒤집음), 20~30세 구간
    으로 옮겨서 "에이스면 더 길게" 효과를 그쪽에서만 낸다. 31~34세는
    품질 무관 일괄 1~2년으로 확장(1차 버전은 34세만 2~3년이었다).

    "에이스" 판정은 매트릭스 원안의 절대 OVR 수치(45~60 등) 대신 그대로
    유지: ovr_ceiling(그 팀 등급/tier/국가 설계 OVR 상한, database.
    get_ovr_range) 이상인 선수 — 이 게임은 나라마다 OVR 체계 자체가
    다르게 설계돼 있어서(예: 대한민국 1부 목표 OVR대 vs 브라질 1부
    목표 OVR대가 전혀 다름) 절대 수치 기준은 안 맞고, "그 리그 기준
    으로" 봐야 한다는 원래 의도(1차 설계 그대로)를 유지한다.

    나이 구간(위에서부터 먼저 걸리는 걸로 판정, 서로 안 겹침):
      - 35세 이상: 품질 무관 무조건 1년(철칙 — 에이스 예외 없음)
      - 31~34세: 품질 무관 1~2년
      - 26~30세: 에이스면 4~5년, 아니면 3년(고정)
      - 20~25세: 에이스면 4~5년, 아니면 3~4년
      - 20세 미만(유스 등, 이 매트릭스가 다루지 않는 구간): 호출부
        기본값(default) 그대로 — 재계약은 2~5년, 이적 신규계약은 2~4년."""
    is_ace = ovr is not None and ovr_ceiling is not None and ovr >= ovr_ceiling
    if age >= 35:
        return (1, 1)
    if age >= 31:
        return (1, 2)
    if age >= 26:
        return (4, 5) if is_ace else (3, 3)
    if age >= 20:
        return (4, 5) if is_ace else (3, 4)
    return default


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
    from constants import get_country_league_grade, get_ovr_range
    rows = c.execute(
        "SELECT id, name, position, age, ovr, team_id, salary FROM ai_players "
        "WHERE contract_end_year <= ? AND contract_end_year > 0 "
        "AND on_loan_from_team_id = 0", (year,)).fetchall()
    if not rows:
        return 0
    team_rows = c.execute(
        """SELECT t.id, t.name, t.current_tier AS tier, cn.name AS cname FROM teams t
           JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_rows = _drop_mil_teams(c, team_rows, "id")   # [2026-10] 군팀 제외
    tinfo_by_tid = {t["id"]: (t["cname"], t["name"], t["tier"]) for t in team_rows}
    _grade_cache: dict = {}

    def _grade_of(tid_):
        cname_ = tinfo_by_tid.get(tid_, ("", "", 1))[0]
        if cname_ not in _grade_cache:
            _grade_cache[cname_] = get_country_league_grade(cname_)
        return _grade_cache[cname_]

    # [2026-09 신설] 위 _ai_contract_duration_range의 "리그 기준 에이스"
    # 판정용 — 그 팀 등급/tier/국가의 설계 OVR 상한. (등급,tier,국가) 조합
    # 단위로 캐싱(팀 수보다 조합 수가 훨씬 적음). get_ovr_range가 그 조합에
    # 대한 표를 못 찾으면(깊은 tier 등) _transfer_market의 dst_ovr_ceiling_
    # by_tid와 동일한 폴백(43)을 쓴다.
    _ceiling_cache: dict = {}

    def _ceiling_of(tid_, grade_):
        cname_, _tname_, tier_ = tinfo_by_tid.get(tid_, ("", "", 1))
        key = (grade_, tier_, cname_)
        if key not in _ceiling_cache:
            _rng = get_ovr_range(grade_, tier_, cname_)
            _ceiling_cache[key] = _rng[1] if _rng else 43
        return _ceiling_cache[key]


    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 1
    updates = []
    stay_updates = []   # [2026-10] 재계약 불발 + 미판매 → 1년 단기 연장 (아래 주석 참고)
    log_rows = []
    for r in rows:
        cname, tname, tier = tinfo_by_tid.get(r["team_id"], ("", "", 1))
        grade = _grade_of(r["team_id"])
        # [2026-09 신설, 신민용 지적] 예전엔 전 선수 동일한 0.75 고정이라
        # 소속 리그 수준에 한참 못 미치게 된 선수도 75%로 재계약됐다 —
        # 구단이 스스로 스쿼드 수준을 유지하려는 압력이 아예 없는 상태.
        # 미달 폭만큼 재계약 확률을 깎는다(즉시 방출이 아니라 계약 만료
        # 상태로 남아 다음 이적시장에서 "계약 임박" 가중치를 받는다 —
        # 아래 continue 주석 참고).
        _renew_p = AI_CONTRACT_RENEWAL_PROB
        _rsf = _league_shortfall(r["ovr"], grade, tier, cname)
        if _rsf > 0.0:
            _renew_p *= _interp_pts(_SHORTFALL_RENEW_PTS, _rsf)
        if random.random() >= _renew_p:
            # 재계약 불발 — 계약 만료 상태 그대로 두면 다음 시즌
            # 이적시장에서 "계약 임박" 가중치로 계속 이적 후보가 된다.
            # [2026-10 버그수정, 신민용 리포트: "2040년에 계약 3년이면
            # 2040~2042년까지 뛴 건데 왜 2043년까지 되어있어?"] 이 함수는
            # 이적시장이 끝난 뒤에 돌기 때문에, 여기서 불발된 선수는 이미
            # "이번 오프시즌에 아무 데도 안 팔린" 선수다 — AI에는 FA(무소속)
            # 상태가 없어서 그대로 같은 팀에서 다음 시즌을 뛴다. 그런데 그
            # 시즌이 계약도 로그도 없이 지나가서, 선수 검색에선 직전 계약
            # "(계약: 3년)"이 한 해 더 이어진 것처럼 보였다(10시즌 헤드리스
            # 실측: 계약의 약 4.5%가 이렇게 만료 후 1년 이상 더 머묾, 2010년
            # 시작 시점 현역 8,057명이 만료된 계약으로 뛰는 중). 실제로 뛰는
            # 그 1시즌을 "연장 1년"으로 기록한다 — 만료연도만 year+1로
            # 당겨 적고 연봉은 그대로 둔다. 게임 진행에는 영향이 없다:
            # 이적 가중치는 남은 계약 max(0, 만료-올해)라 다음 시장에서 둘 다
            # 0이고, 이적료 면제(만료<=올해)와 다음 오프시즌 재계약 판정
            # (만료<=올해)도 둘 다 똑같이 걸리며, 난수도 추가로 쓰지 않는다.
            _stay_cend = year + 1
            stay_updates.append((_stay_cend, r["id"]))
            log_rows.append((
                _cur_season, year, r["id"], r["name"], r["position"], r["age"] or 25, r["ovr"],
                r["team_id"], r["team_id"], 0, 0, 0.0, 0.0, "연장", 0, "", 0, 0, 0,
                r["salary"] or 0, _stay_cend))
            continue
        new_salary = _calc_ai_salary(grade, tier, r["ovr"], cname, tname, r["team_id"], year)
        # [2026-09 버그수정, 구현 직후 헤드리스 검증 중 자체 발견: "재계약
        # 기간을 2~5년으로 뽑았는데 표시되는 기간이 1~4년으로 한 해씩
        # 짧게 나온다"] world_browser의 duration 계산은 실제 발효연도
        # (effective_year — 오프시즌 이적/재계약은 다음 해부터 발효,
        # get_ai_player_salary_history 주석 참고)를 기준으로 하는데,
        # 여기서 만료연도를 셀 때는 발효 전(year)을 기준으로 셌던 게
        # 원인 — year+1(발효연도)부터 세야 의도한 기간 그대로 표시된다.
        new_cend = year + random.randint(
            *_ai_contract_duration_range(
                r["age"] or 25, r["ovr"], _ceiling_of(r["team_id"], grade),
                default=AI_CONTRACT_RENEWAL_DURATION_YEARS))
        updates.append((new_cend, new_salary, r["id"]))
        log_rows.append((
            _cur_season, year, r["id"], r["name"], r["position"], r["age"] or 25, r["ovr"],
            r["team_id"], r["team_id"], 0, 0, 0.0, 0.0, "연장", 0, "", 0, 0, 0,
            new_salary, new_cend))
    if updates:
        c.executemany(
            "UPDATE ai_players SET contract_end_year=?, salary=? WHERE id=?", updates)
    if stay_updates:
        c.executemany(
            "UPDATE ai_players SET contract_end_year=? WHERE id=?", stay_updates)
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
    # [2026-09 신설] 임대 중인 선수는 시장 구매 대상에서 뺀다 — 임대처가
    # 원 소속팀 선수를 제3의 팀에 넘기는 셈이 된다(FIFA 규정 위반, 위
    # _do_one_transfer_cached의 같은 주석 참고). 컬럼이 없는 행(구 호출부)은
    # 예전과 동일하게 전부 후보로 둔다.
    _has_loan_col = bool(rows) and "on_loan_from_team_id" in rows[0].keys()
    for r in rows:
        if _has_loan_col and r["on_loan_from_team_id"]:
            continue
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
                # [2026-09 신설, database.roll_potential_ovr 정의부 주석
                # 참고, 10시즌 헤드리스 검증으로 발견: "은퇴대체 유스 생성
                # 쪽은 star_kind로 고쳤는데도 97+가 계속 준다"] 원인은 여기
                # — 명문팀 은퇴자리는 유스 생성보다 이 시장구매 경로로
                # 채워지는 비중이 더 큰데, 이 경로는 position/OVR/나이만
                # 보고 potential_ovr을 전혀 안 본다. 그 결과 은퇴한
                # 월드클래스 선수 자리가 "OVR만 맞고 잠재력은 평범한" 후보로
                # 채워지면서 명문팀의 고잠재력 비중이 세대를 거듭할수록
                # 계속 희석됐다. potential_ovr을 entries에 추가해서
                # _find_buy_replacement의 가중치 계산에 쓸 수 있게 한다
                # (없으면 ovr로 폴백 — 구버전 세이브 하위호환).
                _pot = r["potential_ovr"] if ("potential_ovr" in r.keys() and r["potential_ovr"]) else r["ovr"]
                # [2026-09 신설, 신민용 리포트: "5명 한계인데 8명으로
                # 뚫었잖아"] 후보의 진짜 국적을 e[7]에 같이 담는다 —
                # _find_buy_replacement의 domestic_nat_only(목적지 팀의
                # 외국인 쿼터가 꽉 찼을 때 자국 국적자만 보는 모드)가
                # 후보마다 Row 이름 조회를 하지 않도록, 시즌 내내 안 변하는
                # 값을 여기서 한 번만 뜯어둔다(_cn은 "지금 뛰는 나라"이고
                # 이건 "국적"으로, 둘은 다른 값이다).
                _nat = r["nationality"] if ("nationality" in r.keys()) else ""
                _e = (r, r["id"], r["team_id"], _cn,
                      _rank_of.get(_ti[0] if _ti else "D", 1), r["age"], _pot, _nat)
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
                    # [2026-09 신설] potential_ovr 미러 — _find_buy_replacement
                    # 가중치 계산용(위 entries의 potential 추가 주석 참고).
                    np.fromiter((e[6] for e in entries), np.int16, len(entries)),
                    # [2026-09 신설] 국적 코드 미러 — domestic_nat_only
                    # (쿼터 꽉 찬 팀은 자국 국적자만) 필터용. 위 e[3](지금
                    # 뛰는 나라) 코드표 _cty_code를 그대로 공유하므로,
                    # dst_cname의 코드 하나만 찾아 == 비교하면 된다.
                    np.fromiter((_cty_code.setdefault(e[7], len(_cty_code)) for e in entries),
                                np.int32, len(entries)),
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
                           global_scouting=False, stats=None, dst_prestige_level=0,
                           domestic_nat_only=False):
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
    [2026-09 신설, 신민용 리포트: "8명이 왜 나와? 5명 한계인데 8명으로
    뚫었잖아"] domestic_nat_only=True면 "진짜 국적이 dst_cname인 후보"만
    본다 — 목적지 팀의 외국인 쿼터(database.FOREIGN_QUOTA_RANGE)가 이미
    꽉 찼을 때 호출부가 켠다. 이 경로가 쿼터를 전혀 안 보던 게 초과팀이
    시즌마다 수백 팀씩 늘던 최대 원인이었다(호출부 _retire_and_replace의
    _quota_full 주석 참고). 후보가 없으면 None을 돌려주므로, 호출부는
    자연히 기존 "자체 유스 생성"(국적 추첨에 쿼터 하드스톱이 걸린 경로)로
    폴백한다 — 즉 자리는 반드시 채워지고, 쿼터만 안 깨진다.
    주의: by_country는 "지금 뛰는 나라"로 묶인 표이고 이 필터는 "국적"
    기준이라 서로 다른 축이다 — 해외에서 뛰는 자국 국적자(예: 유럽파
    한국인)도 후보에 포함돼야 하므로, 이 모드에선 국가별 부분풀로 좁히지
    않고 전역 밴드를 국적으로 거른다.

    반환: 뽑힌 선수 행(sqlite3.Row) 또는 후보가 없으면 None."""
    from constants import BUY_REPLACEMENT_OVR_BAND, BUY_REPLACEMENT_YOUNG_AGE, BUY_REPLACEMENT_YOUNG_WEIGHT
    # [2026-09 버그수정, 신민용 리포트: "레알/바르사가 97+ 0명 — 명문팀이
    # 명문선수를 영입한다는 원래 목적이 안 지켜진다"] 아래 잠재력 가중치
    # 계수(0.1)가 목적지 레벨과 무관하게 고정이었다 — 즉 명문팀이든
    # 비명문팀이든 "같은 OVR대에서 잠재력 높은 후보를 선호하는 정도"가
    # 완전히 같았다. 3급일수록 이 선호를 훨씬 세게 걸어서, 같은 후보군
    # 안에서도 명문팀이 고잠재력 후보를 확실히 더 많이 가져가게 한다.
    _pot_pref_coef = {3: 0.35, 2: 0.2, 1: 0.12}.get(dst_prestige_level, 0.05)
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
                (r, r["id"], r["team_id"], None, 1, r["age"],
                 r["potential_ovr"] if ("potential_ovr" in r.keys() and r["potential_ovr"]) else r["ovr"])
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
        # [2026-09 예외] domestic_nat_only는 "국적" 기준이라 "지금 뛰는
        # 나라" 부분풀로 좁히면 해외파 자국 선수를 놓친다(docstring 참고)
        # — 이 모드에선 전역 밴드를 훑고 아래 국적 조건으로 거른다.
        _sub = (by_country.get(dst_cname)
                if (same_country and by_country is not None and not domestic_nat_only)
                else None)
        if _sub is not None:
            _srows, _sovrs = _sub
            if not _srows:
                return []
            scan = _srows[bisect.bisect_left(_sovrs, lo):bisect.bisect_right(_sovrs, hi)]
        elif same_country and by_country is not None and not domestic_nat_only:
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
            if domestic_nat_only:
                # 쿼터가 꽉 찬 팀 — "국적"이 자국인 후보만(어느 나라에서
                # 뛰든). same_country 인자는 이 모드에선 의미가 없으므로
                # 국내/해외 구분을 아예 건너뛴다(같은 결과가 두 번 나와도
                # 위 폴백이 첫 호출에서 이미 성공하므로 중복 탐색 없음).
                if (e[7] if len(e) > 7 else "") != dst_cname:
                    continue
            else:
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
        _t, _c, _r, _a, _u, _i2, _code, _tmax, _p = _npm[:9]
        _nat = _npm[9] if len(_npm) > 9 else None
        _ts = _t[i0:i1]
        m = (_ts != dst_team_id) & (~_u[i0:i1])
        # [2026-09] domestic_nat_only에선 "자국 국적자만" — 원래의 "지금
        # 뛰는 나라가 자국이 아닌 후보만"(전세계 경로 정의)은 정반대
        # 조건이라 같이 걸면 결과가 항상 빈다. 국적 미러가 없으면(구버전
        # 풀) 이 경로를 포기하고 파이썬 _filter로 넘긴다.
        if domestic_nat_only:
            if _nat is None:
                return None
            m &= (_nat[i0:i1] == _code.get(dst_cname, -1))
        else:
            m &= (_c[i0:i1] != _code.get(dst_cname, -1))
        m &= _ok_arr[_ts]
        if global_scouting:
            m &= (_r[i0:i1] <= dst_rank)
        idx = np.flatnonzero(m)
        n = idx.size
        if n == 0:
            return None
        w = np.where(_a[i0:i1][idx] <= BUY_REPLACEMENT_YOUNG_AGE,
                     BUY_REPLACEMENT_YOUNG_WEIGHT, 1.0)
        # [2026-09 신설, _build_buy_pools의 potential_ovr 추가 주석 참고]
        # "잠재력이 target_ovr보다 남는 만큼" 가중치를 더 준다 — 같은 OVR
        # 밴드 안에서도 아직 성장 여지가 있는(월드클래스/엘리트급) 후보가
        # 이미 다 큰(잠재력=현재OVR인) 후보보다 우선 뽑히게 해서, 명문팀
        # 은퇴자리가 시장구매로 채워질 때도 고잠재력 비중이 유지되게 한다.
        w = w * (1.0 + np.maximum(0, _p[i0:i1][idx].astype(np.float64) - target_ovr) * _pot_pref_coef)
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

    if domestic_nat_only:
        # [2026-09 신설] 이 모드에선 _filter(True)/_filter(False)가 같은
        # 집합(자국 국적자)을 돌려주므로 국내/해외 2단계 구분이 의미가
        # 없다 — numpy 경로가 있으면 그걸로 한 번에 뽑고, 없으면 파이썬
        # 필터를 한 번만 돈다(같은 스캔을 두 번 하지 않게 분기를 분리).
        if _np_ready:
            _note_scan()
            _hit = _pick_global_np()
            if _hit is not None:
                return _hit
            chosen = []
        else:
            chosen = _filter(True)
    elif global_scouting:
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
    # [2026-09 신설] 위 numpy 경로(_pick_global_np)와 동일한 잠재력 가중치.
    weights = [(BUY_REPLACEMENT_YOUNG_WEIGHT if (e[5] or 25) <= BUY_REPLACEMENT_YOUNG_AGE else 1.0)
               * (1.0 + max(0, (e[6] if len(e) > 6 else target_ovr) - target_ovr) * _pot_pref_coef)
               for e in chosen]
    # chosen은 튜플 목록이지만 가중치 순서·개수가 원본과 같으므로 같은
    # 난수 스트림에서 같은 자리를 뽑는다 — 행만 꺼내 돌려준다.
    return random.choices(chosen, weights=weights, k=1)[0][0]


def _build_foreign_quota_gate(c):
    """[2026-09 신설, 신민용 리포트: "8명이 왜 나와? 5명 한계인데 8명으로
    뚫었잖아"] 외국인 쿼터를 예방적으로 지켜야 하는 여러 경로(_prestige_
    scouting / _prestige_potential_scouting의 1:1 맞교환 등)가 공유하는
    게이트를 한 번에 만들어 돌려준다. 각 경로가 자기만의 카운터를 따로
    들고 있다 보니 어느 한 곳만 빠져도 쿼터가 새는 게 이 버그의 구조적
    원인이었으므로, "지금 몇 명인지 + 넣어도 되는지" 판정을 한 군데로
    모은다.

    반환: (foreign_now, can_take, note_swap)
      foreign_now[tid]  : 그 팀의 현재 외국인 수(진짜 국적 기준)
      can_take(tid, in_nat, out_nat) -> bool
          out_nat 선수를 내보내고 in_nat 선수를 받아도 쿼터 안에 있는가.
          쿼터가 정의되지 않은 팀/국가는 항상 True(기존 동작 유지).
      note_swap(tid, in_nat, out_nat)
          실제로 맞교환이 성사됐을 때 카운터를 갱신한다(호출 필수 —
          안 하면 같은 팀이 한 시즌에 여러 번 쿼터를 뚫는다).
    """
    from database import get_foreign_quota_range, is_roster_foreign
    info: dict = {}
    for r in c.execute(
            """SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname,
                      cn.continent AS continent
               FROM teams t JOIN leagues l ON t.league_id=l.id
                            JOIN countries cn ON l.country_id=cn.id""").fetchall():
        _q_lo, _hi = get_foreign_quota_range(r["cname"], r["continent"], tier=r["tier"])
        info[r["tid"]] = (r["cname"], _hi)
    foreign_now: dict = {}
    for r in c.execute(
            """SELECT ap.team_id AS tid, COUNT(*) AS n FROM ai_players ap
               JOIN teams t ON ap.team_id = t.id
               JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id
               WHERE ap.nationality != '' AND ap.nationality != cn.name
               GROUP BY ap.team_id""").fetchall():
        foreign_now[r["tid"]] = r["n"]

    def _delta(tid, in_nat, out_nat):
        meta = info.get(tid)
        if not meta:
            return None, None
        cname, quota = meta
        d = 0
        if is_roster_foreign(in_nat, cname):
            d += 1
        if is_roster_foreign(out_nat, cname):
            d -= 1
        return d, quota

    def can_take(tid, in_nat, out_nat=""):
        d, quota = _delta(tid, in_nat, out_nat)
        if d is None or quota is None:
            return True
        if d <= 0:
            return True   # 외국인이 늘지 않는 교환은 항상 허용(초과 팀 복구도 겸함)
        return foreign_now.get(tid, 0) + d <= quota

    def note_swap(tid, in_nat, out_nat=""):
        d, _quota = _delta(tid, in_nat, out_nat)
        if d:
            foreign_now[tid] = max(0, foreign_now.get(tid, 0) + d)

    return foreign_now, can_take, note_swap


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

    # [2026-09 신설] on_loan_from_team_id — 임대 중인 선수는 스카우팅 대상
    # (_build_buy_pools가 제외)도, 반대급부로 내보낼 선수(아래 weak)도 될 수
    # 없다(임대처가 남의 선수를 넘기는 셈 — FIFA 재임대·재이적 금지).
    rows = c.execute(
        "SELECT id, team_id, position, age, ovr, name, nationality, on_loan_from_team_id "
        "FROM ai_players").fetchall()
    rows = _drop_mil_teams(c, rows, "team_id")   # [2026-10] 복무 중인 선수는 스카우팅 대상 아님
    pools = _build_buy_pools(rows)

    team_rows = c.execute(
        """SELECT t.id, t.name, t.current_tier AS tier, cn.name AS cname FROM teams t
           JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_rows = _drop_mil_teams(c, team_rows, "id")   # [2026-10] 군팀 제외
    tid_by_name = {(t["cname"], t["name"]): t["id"] for t in team_rows}
    tinfo_by_tid = {t["id"]: (t["cname"], t["name"], t["tier"]) for t in team_rows}
    # [2026-09 버그수정, 신민용 리포트: "5명 한계인데 8명으로 뚫었잖아"]
    # 이 함수의 1:1 맞교환은 국적을 전혀 안 봤다 — 자국 선수를 내보내고
    # 외국인을 받으면 그 팀 외국인 수가 그대로 1 늘어난다. 실측(1시즌
    # 계측 하니스): 이 함수 한 번이 초과팀을 617 → 717팀(+100)으로 늘렸다.
    _fq_now, _fq_can_take, _fq_note = _build_foreign_quota_gate(c)
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
            weak = min((p for p in squad if p["position"] == pos and p["id"] not in used_ids
                        and not p["on_loan_from_team_id"]),   # [2026-09] 임대 중 제외
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
                     and _grade_rank_of(r["team_id"]) <= dst_rank
                     # [2026-09] 외국인 쿼터 게이트 — 내보낼 선수(weak)를
                     # 빼고 이 후보를 받았을 때 쿼터 안에 있어야 한다.
                     # 상대 팀 쪽은 weak가 그 팀 자국민일 수도/아닐 수도
                     # 있으므로 같은 게이트로 한 번 더 본다(양쪽 다 안
                     # 깨지는 교환만 성사).
                     and _fq_can_take(tid, r["nationality"], weak["nationality"])
                     and _fq_can_take(r["team_id"], weak["nationality"], r["nationality"])]
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
            # [2026-09 버그수정, 신민용 리포트 1번 재조사 중 발견] 자매
            # 함수 _prestige_potential_scouting에는 이미 들어가 있는
            # "반대급부 방향" 가드가 여기엔 이식되지 않았다 — 위
            # cands 필터의 _dst_ceiling_excluded는 **들어오는 방향**
            # (target → 명문팀)만 본다. 나가는 weak가 target 팀 나라·부수의
            # 설계 OVR 상한에 맞는지는 전혀 안 봐서, SS급 명문팀 벤치
            # (OVR 70대)가 그 상한을 한참 넘는 하위 리그 팀으로 그대로
            # 밀려 들어갈 수 있었다. 자매 함수와 완전히 같은 기준·같은
            # 폴백(맞교환이 안 되면 "이적료만 받는 영입"으로 처리하되,
            # 그러면 원 소속팀의 그 포지션이 0명이 되는 경우엔 건너뛴다).
            _tgt_t = tinfo_by_tid.get(target["team_id"])
            if _tgt_t is not None:
                _tgt_rng = get_ovr_range(_grade_of(target["team_id"]), _tgt_t[2] or 1, _tgt_t[0])
                _tgt_ceiling = _tgt_rng[1] if _tgt_rng else 43
            else:
                _tgt_ceiling = 43
            _swap_ok = not _dst_ceiling_excluded(weak["ovr"] or 0, _tgt_ceiling)
            if not _swap_ok:
                _tgt_pos_n = sum(1 for _p in team_players.get(target["team_id"], ())
                                 if _p["position"] == target["position"])
                if _tgt_pos_n < 2:
                    continue    # 맞교환도 안 되고, 빼면 그 포지션이 비는 팀
            used_ids.add(target["id"])
            if _swap_ok:
                used_ids.add(weak["id"])
            # [2026-09] 성사된 교환을 쿼터 카운터에 반영 — 안 하면 같은
            # 팀이 한 시즌에 여러 번 같은 쿼터 자리를 쓴다.
            _fq_note(tid, target["nationality"], weak["nationality"] if _swap_ok else "")
            _fq_note(target["team_id"], weak["nationality"] if _swap_ok else "",
                     target["nationality"])
            # [2026-09 신설, 신민용 요청: "이적이면 연봉이 써지는거고"]
            # 맞바꾼 두 선수 다 새 소속팀 기준으로 연봉을 다시 계산한다.
            _tid_cname, _tid_tname, _tid_tier = tinfo_by_tid.get(tid, ("", "", 1))
            _old_cname, _old_tname, _old_tier = tinfo_by_tid.get(target["team_id"], ("", "", 1))
            _target_salary = _calc_ai_salary(_grade_of(tid), _tid_tier, target["ovr"],
                                              _tid_cname, _tid_tname, tid, year)
            _weak_salary = (_calc_ai_salary(_grade_of(target["team_id"]), _old_tier, weak["ovr"],
                                            _old_cname, _old_tname, target["team_id"], year)
                            if _swap_ok else 0)
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
            # [주의] 난수 소비는 맞교환 여부와 무관하게 항상 2회 그대로
            # 둔다 — 조건부로 만들면 이 시점 이후의 전역 난수 스트림이
            # 통째로 갈라진다(자매 함수 _prestige_potential_scouting의
            # 같은 주석 참고).
            _weak_cend = year + random.randint(3, 5)
            _target_cend = year + random.randint(3, 5)
            if _swap_ok:
                swap_updates.append((target["team_id"], _weak_cend, year,
                                      _weak_salary, weak["id"]))
            swap_updates.append((tid, _target_cend, year,
                                  _target_salary, target["id"]))
            log_rows.append((_cur_season, year, target["id"], target["name"], target["position"],
                              target["age"] or 25, target["ovr"], target["team_id"], tid,
                              0, level, 0.0, 0.0, "명문팀 스카우팅", 0, "", _fee, 0, 0,
                              _target_salary, _target_cend))
            if _swap_ok:
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


# [2026-09 신설, 신민용 요청: "잠재력이 team_cap에 종속되면 안 된다 — 한국
# 하위팀 유망주도 잠재력만 있으면 해외 명문팀에 발굴돼 이적할 수 있어야
# 한다"] 위 _prestige_scouting과 자매 함수. _prestige_scouting은 "지금
# 당장의 실력 업그레이드"만 보므로(후보를 현재 OVR 상위 퍼센타일에서
# 찾음), potential_ovr은 높지만 team_cap에 막혀 현재 OVR이 낮은 선수는
# 그 후보 풀에 아예 못 들어온다 — 이 함수는 그 사각지대 전용으로,
# "potential_ovr − 소속팀 team_cap" 격차만 보고 후보를 찾는다(현재 OVR
# 격차는 아예 안 본다 — 데려오는 시점엔 벤치보다 약해도 상관없다는 게
# 이 통로의 핵심). _prestige_scouting과 동일하게 "그 자리 최약체와 1:1
# 맞교환" 방식을 그대로 재사용해 스쿼드 인원수가 안 흔들리게 한다.
def _prestige_potential_scouting(c, year):
    """potential_ovr 기반 유망주 발굴 스카우팅. 반환: 성사된 이동 건수."""
    from constants import (POTENTIAL_SCOUT_MIN_POTENTIAL_BY_LEVEL, POTENTIAL_SCOUT_MIN_OVR_FRAC_BY_AGE,
                            POTENTIAL_SCOUT_MIN_ABS_OVR_BY_LEVEL, POTENTIAL_SCOUT_MIN_GAP,
                            POTENTIAL_SCOUT_MAX_AGE, POTENTIAL_SCOUT_PROB_BY_LEVEL, get_country_league_grade)
    from constants import get_ovr_range
    from economy import LEAGUE_GRADE_RANK, estimate_transfer_fee
    from data.prestige_clubs import PRESTIGE_TEAMS
    from database import compute_ai_growth_cap

    def _min_ovr_frac_for_age(age):
        if age in POTENTIAL_SCOUT_MIN_OVR_FRAC_BY_AGE:
            return POTENTIAL_SCOUT_MIN_OVR_FRAC_BY_AGE[age]
        _oldest = max(POTENTIAL_SCOUT_MIN_OVR_FRAC_BY_AGE)
        if age and age > _oldest:
            return POTENTIAL_SCOUT_MIN_OVR_FRAC_BY_AGE[_oldest]
        _youngest = min(POTENTIAL_SCOUT_MIN_OVR_FRAC_BY_AGE)
        return POTENTIAL_SCOUT_MIN_OVR_FRAC_BY_AGE[_youngest]

    # 1단계: 전세계에서 "잠재력은 높은데 아직 어린" 선수만 먼저 SQL로
    # 좁힌다(267,737명 전수 스캔을 피하기 위한 값싼 사전 필터 — 실제
    # team_cap 격차·나이별 현재실력 하한 계산은 이 좁힌 후보에 대해서만
    # 한다). SQL 문턱은 레벨별 문턱 중 가장 낮은 값(1급 기준)을 써서
    # 절대 후보를 놓치지 않게 하고, 레벨별 실제 컷은 3단계에서 건다.
    _min_potential_floor = min(POTENTIAL_SCOUT_MIN_POTENTIAL_BY_LEVEL.values())
    rows = c.execute(
        "SELECT id, team_id, position, age, ovr, potential_ovr, name, nationality "
        "FROM ai_players WHERE potential_ovr >= ? AND age <= ? AND age > 0 "
        # [2026-09 신설] 임대 중인 원석은 임대처 소속이 아니다(원 소속팀만 권리)
        "AND on_loan_from_team_id = 0",
        (_min_potential_floor, POTENTIAL_SCOUT_MAX_AGE)).fetchall()
    rows = _drop_mil_teams(c, rows, "team_id")   # [2026-10] 복무 중인 선수는 원석 발굴 대상 아님
    if not rows:
        return 0

    team_rows = c.execute(
        """SELECT t.id, t.name, t.current_tier AS tier, cn.name AS cname,
                  cn.continent AS continent
           FROM teams t JOIN leagues l ON t.league_id=l.id
           JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_rows = _drop_mil_teams(c, team_rows, "id")   # [2026-10] 군팀 제외
    tinfo_by_tid = {t["id"]: t for t in team_rows}
    tid_by_name = {(t["cname"], t["name"]): t["id"] for t in team_rows}

    _cap_cache: dict = {}

    def _team_cap(tid_):
        if tid_ not in _cap_cache:
            t = tinfo_by_tid.get(tid_)
            if t is None:
                _cap_cache[tid_] = 43
            else:
                grade = get_country_league_grade(t["cname"])
                _cap_cache[tid_] = compute_ai_growth_cap(
                    grade, t["tier"] or 1, t["cname"], t["continent"])
        return _cap_cache[tid_]

    # 2단계: 좁혀진 후보 중에서도 "소속팀 환경이 실제로 이 선수의 발목을
    # 잡고 있는" 경우만 남긴다(격차가 안 크면 이미 자기 팀에서 잘 크고
    # 있으므로 발굴할 이유가 없다). [2026-09 추가, 신민용 지적: "22세
    # OVR60이 곧장 리버풀 가는 건 말이 안 된다"] 나이별로 "자기 team_cap
    # 대비 이 정도는 이미 와 있어야 한다"는 상대적 하한도 같이 요구한다
    # (절대 OVR 숫자가 아니라 team_cap 대비 비율 — 위 상수 정의부 주석
    # 참고, 그래야 한국처럼 team_cap 자체가 낮은 나라의 원석도 계속
    # 후보로 남는다).
    gems_by_pos: dict = {}
    for r in rows:
        _cap = _team_cap(r["team_id"])
        if (r["potential_ovr"] - _cap >= POTENTIAL_SCOUT_MIN_GAP
                and r["ovr"] >= round(_cap * _min_ovr_frac_for_age(r["age"]))):
            gems_by_pos.setdefault(r["position"], []).append(r)
    if not gems_by_pos:
        return 0

    _grade_cache: dict = {}

    def _grade_rank_of(tid_):
        t = tinfo_by_tid.get(tid_)
        if t is None:
            return 1
        cname_ = t["cname"]
        if cname_ not in _grade_cache:
            _grade_cache[cname_] = LEAGUE_GRADE_RANK.get(get_country_league_grade(cname_), 1)
        return _grade_cache[cname_]

    def _grade_of(tid_):
        t = tinfo_by_tid.get(tid_)
        return get_country_league_grade(t["cname"]) if t else "F"

    prestige_clubs = []
    for cname, levels in PRESTIGE_TEAMS.items():
        for level, names in levels.items():
            for tname in sorted(names):
                tid = tid_by_name.get((cname, tname))
                if tid is not None:
                    prestige_clubs.append((tid, level))
    random.shuffle(prestige_clubs)

    # 명문팀(잠재 영입팀) 스쿼드만 필요한 만큼 조회 — 267,737명 전체를
    # 다시 불러올 필요 없이 IN절로 좁힌다.
    _dst_tids = [tid for tid, _lvl in prestige_clubs]
    squad_by_tid: dict = {}
    if _dst_tids:
        _CHUNK = 500
        for i in range(0, len(_dst_tids), _CHUNK):
            chunk = _dst_tids[i:i + _CHUNK]
            qmarks = ",".join("?" * len(chunk))
            for p in c.execute(
                    f"SELECT id, team_id, position, ovr, name, age, nationality, on_loan_from_team_id "
                    f"FROM ai_players WHERE team_id IN ({qmarks})", chunk).fetchall():
                squad_by_tid.setdefault(p["team_id"], []).append(p)

    # [2026-09 신설, 아래 반대급부 검증용] 팀·포지션별 인원 — "맞교환이
    # 성립 안 해서 원석만 데려갈 때, 그 팀의 그 포지션이 0명이 되지 않게"
    # 확인하는 데만 쓴다(이 파일이 이적시장에서 지키는 것과 같은 불변식).
    # 한 번의 그룹 조회로 끝나고, 이 함수 안에서 일어나는 이동은 팀당
    # 최대 1건이라 루프 중 갱신이 필요 없다.
    _pos_count_by_tid: dict = {}
    for _r in c.execute(
            "SELECT team_id, position, COUNT(*) AS n FROM ai_players "
            "WHERE team_id IS NOT NULL GROUP BY team_id, position").fetchall():
        _pos_count_by_tid[(_r["team_id"], _r["position"])] = _r["n"]
    # [2026-09 버그수정, 신민용 리포트: "5명 한계인데 8명으로 뚫었잖아"]
    # _prestige_scouting과 같은 이유 — 이 통로도 국적을 안 봐서 명문팀이
    # 외국인 원석을 쿼터 위로 계속 쌓았다(실측 1시즌 +15~20팀). 맞교환이
    # 성립하지 않는 "이적료만 받는 영입"일 때는 나가는 선수가 없으므로
    # 그 경우까지 같은 게이트로 본다(out_nat="").
    _fq_now, _fq_can_take, _fq_note = _build_foreign_quota_gate(c)

    used_ids: set = set()
    swap_updates = []
    log_rows = []
    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 1
    n_moves = 0

    for tid, level in prestige_clubs:
        if random.random() >= POTENTIAL_SCOUT_PROB_BY_LEVEL.get(level, 0.0):
            continue
        squad = squad_by_tid.get(tid, [])
        if not squad:
            continue
        dst_rank = _grade_rank_of(tid)
        positions_here = sorted({p["position"] for p in squad})
        random.shuffle(positions_here)
        for pos in positions_here:
            _min_pot = POTENTIAL_SCOUT_MIN_POTENTIAL_BY_LEVEL.get(level, 96)
            _min_abs_ovr = POTENTIAL_SCOUT_MIN_ABS_OVR_BY_LEVEL.get(level, 60)
            # [2026-09] 쿼터 게이트용으로 "내보낼 최약체"를 후보 추첨보다
            # 먼저 구한다 — min()은 난수를 안 쓰므로 아래 random.choices의
            # 난수 스트림 위치는 예전과 완전히 같다(재현성 유지).
            _weak_pre = min((p for p in squad if p["position"] == pos and p["id"] not in used_ids
                        and not p["on_loan_from_team_id"]),   # [2026-09] 임대 중 제외
                            key=lambda p: p["ovr"], default=None)
            _weak_nat = (_weak_pre["nationality"] if _weak_pre is not None else "") or ""
            cands = [g for g in gems_by_pos.get(pos, [])
                     if g["id"] not in used_ids and g["team_id"] != tid
                     and g["potential_ovr"] >= _min_pot
                     and g["ovr"] >= _min_abs_ovr
                     and _grade_rank_of(g["team_id"]) <= dst_rank
                     # [2026-09] 외국인 쿼터 게이트. 목적지는 "아무도 안
                     # 나가는 최악의 경우"(맞교환이 성립 안 하는 영입)로
                     # 보수적으로 판정한다 — 그러면 아래에서 _swap_ok가
                     # 어느 쪽으로 갈라져도 쿼터가 안 깨진다.
                     and _fq_can_take(tid, g["nationality"], "")
                     and _fq_can_take(g["team_id"], _weak_nat, g["nationality"])]
            if not cands:
                continue
            # 격차(=발굴 가치)가 클수록, 그리고 잠재력 자체가 높을수록
            # 더 자주 뽑히도록 가중 — 소소한 원석보다 진짜 대어를 우선
            # 발굴하는 쪽이 "명문팀의 스카우트 네트워크"라는 서사에 맞는다.
            weights = [max(1, g["potential_ovr"]) for g in cands]
            gem = random.choices(cands, weights=weights, k=1)[0]
            weak = min((p for p in squad if p["position"] == pos and p["id"] not in used_ids
                        and not p["on_loan_from_team_id"]),   # [2026-09] 임대 중 제외
                       key=lambda p: p["ovr"], default=None)
            if weak is None:
                continue
            # [2026-09 버그수정, 신민용 리포트: "아스널에서 뛰며 발롱도르까지
            # 받아본 선수가 31세에 잠재력 발굴 스카우트로 이탈리아 4부로 갔다"]
            # 원인: 이 통로는 "원석을 데려가고 그 자리 최약체를 1:1로 돌려준다"
            # 구조인데, 되돌려보내는 쪽(반대급부)에 아무 조건이 없었다. 원석은
            # 정의상 "잠재력 대비 소속팀 수준이 한참 낮은"(POTENTIAL_SCOUT_
            # MIN_GAP=15) 선수라 약팀에서 나오는데, 그 약팀으로 명문팀 선수가
            # 그대로 떨어진 것. 실측(3시즌 114건): OVR96 맨유 → 브라질 5부,
            # OVR90 발렌시아 → 우즈베키스탄 2부, OVR90 AT마드리드 → 오스트리아
            # 4부 등. 후보(원석) 필터가 _grade_rank_of(국가 리그 등급)만 봐서
            # "브라질 5부"도 브라질 등급으로 통과한 것이 화근이었다(부수를
            # 전혀 안 봄).
            #
            # 이적시장(_do_one_transfer_cached)이 이미 쓰는 것과 같은 기준을
            # 그대로 가져온다 — 목적지 나라·부수의 설계 OVR 상한을 크게
            # 넘으면(_DST_CEIL_HARD_EXCLUDE=3.0) 그 이적 자체가 성립하지
            # 않는다고 본다. 맞교환이 성립 안 하면 기능을 죽이는 대신
            # "이적료만 받는 영입"으로 처리한다 — 현실에서도 빅클럽이 약팀
            # 유망주를 살 때 돈을 주지, 주전급을 끼워 보내지 않는다.
            # 다만 그 경우 원석 팀의 그 포지션이 0명이 되면 안 되므로
            # (이 파일이 이적시장 쪽에서 이미 지키는 "마지막 GK/마지막 CB"
            # 불변식과 같은 원칙) 2명 이상일 때만 허용하고, 아니면 건너뛴다.
            _gem_t = tinfo_by_tid.get(gem["team_id"])
            if _gem_t is not None:
                _gem_rng = get_ovr_range(get_country_league_grade(_gem_t["cname"]),
                                          _gem_t["tier"] or 1, _gem_t["cname"])
                _gem_ceiling = _gem_rng[1] if _gem_rng else 43
            else:
                _gem_ceiling = 43
            _swap_ok = (weak["ovr"] or 0) - _gem_ceiling <= _DST_CEIL_HARD_EXCLUDE
            if not _swap_ok and _pos_count_by_tid.get(
                    (gem["team_id"], gem["position"]), 0) < 2:
                continue        # 맞교환도 안 되고, 빼면 그 포지션이 비는 팀 — 건너뛴다
            used_ids.add(gem["id"])
            if _swap_ok:
                used_ids.add(weak["id"])
            # [2026-09] 성사된 이동을 쿼터 카운터에 반영(_prestige_scouting
            # 과 같은 이유 — 안 하면 한 시즌에 같은 자리를 여러 번 쓴다).
            _fq_note(tid, gem["nationality"], weak["nationality"] if _swap_ok else "")
            _fq_note(gem["team_id"], weak["nationality"] if _swap_ok else "",
                     gem["nationality"])
            _tid_cname, _tid_tname, _tid_tier = (
                tinfo_by_tid[tid]["cname"], tinfo_by_tid[tid]["name"], tinfo_by_tid[tid]["tier"])
            _old_t = tinfo_by_tid.get(gem["team_id"])
            _old_cname, _old_tname, _old_tier = (
                (_old_t["cname"], _old_t["name"], _old_t["tier"]) if _old_t else ("", "", 1))
            _gem_salary = _calc_ai_salary(_grade_of(tid), _tid_tier, gem["ovr"],
                                           _tid_cname, _tid_tname, tid, year)
            _weak_salary = (_calc_ai_salary(_grade_of(gem["team_id"]), _old_tier, weak["ovr"],
                                             _old_cname, _old_tname, gem["team_id"], year)
                            if _swap_ok else 0)
            # [설계] 이적료는 "지금 실력"이 아니라 "현재+잠재력 평균"을
            # 기준으로 산정한다 — 현실에서도 유스 대어의 이적료는 지금
            # 당장의 기량보다 장래성을 훨씬 크게 반영하기 때문(그대로
            # gem["ovr"]만 쓰면 이 통로로 나가는 모든 이적료가 사실상
            # 0에 수렴해 "명문팀이 거액에 유망주를 사간다"는 현실감이
            # 사라진다).
            _fee_ovr = round((gem["ovr"] + gem["potential_ovr"]) / 2)
            _fee = estimate_transfer_fee(_grade_of(tid), _tid_tier, _fee_ovr,
                                          country=_tid_cname,
                                          position=gem["position"], year=year) or 0
            # [주의] 난수 소비는 맞교환 여부와 무관하게 항상 2회 그대로 둔다 —
            # 두 계약기간 추첨을 조건부로 만들면 이 시점 이후의 전역 난수
            # 스트림이 통째로 갈라진다(이 파일의 다른 재현성 주석들과 같은 이유).
            _gem_cend = year + random.randint(3, 5)
            _weak_cend = year + random.randint(3, 5)
            swap_updates.append((tid, _gem_cend, year, _gem_salary, gem["id"]))
            log_rows.append((_cur_season, year, gem["id"], gem["name"], gem["position"],
                              gem["age"] or 20, gem["ovr"], gem["team_id"], tid,
                              0, level, 0.0, 0.0, "잠재력 발굴 스카우팅", 0, "", _fee, 0, 0,
                              _gem_salary, _gem_cend))
            if _swap_ok:
                swap_updates.append((gem["team_id"], _weak_cend, year, _weak_salary, weak["id"]))
                log_rows.append((_cur_season, year, weak["id"], weak["name"], weak["position"],
                                  weak["age"] or 25, weak["ovr"], tid, gem["team_id"],
                                  level, 0, 0.0, 0.0, "잠재력 발굴 스카우팅(반대급부)", 0, "", 0, 0, 0,
                                  _weak_salary, _weak_cend))
            n_moves += 1
            break  # 이 팀은 이번 시즌 한 자리만 — 명문팀도 한 시즌에 원석을 여럿 발굴하진 않는다

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
    return n_moves


# [2026-09 신설, database.roll_potential_ovr 정의부 주석 참고] 은퇴대체
# 신인 생성(_retire_and_replace)은 _generate_team_players처럼 "이 슬롯이
# 월드클래스/엘리트 슬롯"이라는 명시적 배정이 없다(은퇴는 포지션 단위로만
# 일어나지, 팀의 11자리 스타 배치를 다시 돌리지 않는다) — 그래서 슬롯 대신
# 리그 등급별 확률로 "이 신인이 월드클래스/엘리트/일반 중 어디에 해당하는
# 재목인지"를 뽑는다.
# [2026-09 버그수정, 10시즌 헤드리스 검증으로 발견: "생성 직후 97+가
# 58명인데 시즌1엔 34명, 시즌5엔 13명 — 계속 줄어들기만 한다"] 최초
# 배포값(SS:0.15/0.35 등)은 대충 잡은 값이라 실제
# database._star_counts()가 만드는 밀도보다 훨씬 낮았다 — 최초 생성 때는
# SS/S 등급 스쿼드의 스타 비율이 훨씬 높은데, 세대가 교체될 때마다 그보다
# 낮은 이 확률표로 대체되면서 스타 비율 자체가 시즌마다 희석되고 있었다.
# 이제 _star_counts(grade, team_strength=0.5)를 그 등급 평균 스쿼드
# 크기(빅5리그 1부 실측 약 23명)로 나눠 실제 밀도에 맞췄다 — retiree가
# 주전인지 벤치인지 구분하지 않고 "이 등급 스쿼드 전체에서 스타가 차지하는
# 비율"로 근사한다(완벽하진 않지만 기존 0.15/0.35보다 훨씬 실제에 가깝다).
_REPLACEMENT_STAR_PROB = {
    "SS": (0.20, 0.45), "S": (0.14, 0.50), "A": (0.0, 0.14),
}

# [2026-09 버그수정, 신민용 리포트: "레알/바르사가 97+ 0명, 리버풀 3명,
# 아스널 0명 — 명문 레벨을 아예 안 본다"] 위 _REPLACEMENT_STAR_PROB는
# grade(그 나라 리그 등급)로만 갈려서, 같은 나라 안에서는 3급 명문팀과
# 비명문팀이 완전히 동일한 확률을 썼다 — "명문팀이 명문선수를 영입한다"는
# 이번 작업의 원래 목적을 정작 새로 만든 두 경로(은퇴대체/시장구매)가
# 전혀 반영 못 하고 있었다. prestige_level(1~3, 비명문은 0)별 배수를
# 곱해서 같은 grade 안에서도 3급이 확실히 더 유리하게 재분배한다 —
# 비명문(기본 0.5배)에서 줄인 만큼을 3급(4배)/2급(2.5배)/1급(1.5배)에
# 몰아주는 구조라, grade 전체 평균은 크게 안 바뀌면서 "어느 팀에 쏠리는가"
# 만 명문 순서대로 재배치된다.
_PRESTIGE_STAR_MULT = {3: 4.0, 2: 2.5, 1: 1.5}  # 비명문(레벨0)은 기본 0.5


def _prestige_star_prob(grade, plvl):
    """_REPLACEMENT_STAR_PROB를 prestige_level 배수까지 적용해서 반환."""
    p_world, p_elite = _REPLACEMENT_STAR_PROB.get(grade, (0.0, 0.0))
    mult = _PRESTIGE_STAR_MULT.get(plvl, 0.5)
    return min(1.0, p_world * mult), min(1.0, p_elite * mult)


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
    from database import (_pick_nationality, get_foreign_quota_range,
                          compute_ai_growth_cap, roll_potential_ovr)
    # [2026-09 계측, 신민용 지적: "은퇴자 +21%인데 시간 +66% — 건당 비용
    # 자체가 악화되고 있다"] 이 함수를 한 덩어리로 보면 그 원인이 누적
    # 데이터(ai_players_retired 등)에 있는지 신인 생성에 있는지 구분이
    # 안 된다. 준비/판정루프/대체자탐색/신인생성/DB쓰기로 쪼갠다.
    import time as _time_rt
    _rt0 = _time_rt.perf_counter()
    _acc_buy = 0.0     # 후계자·대체자 탐색(_find_buy_replacement)
    _acc_stats = 0.0   # 신인 능력치 생성(_gen_stats)
    _acc_name = 0.0    # [2026-09] AI 실명 폐지로 항상 0 — 계측 라인 형식만 유지
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
    from military_service import get_military_team_ids as _get_mil_tids
    _mil_tids_rr = _get_mil_tids(c)
    for r in c.execute(
            """SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                      t.club_strength AS club_strength,
                      t.momentum_type AS momentum_type,
                      t.momentum_seasons_left AS momentum_seasons_left,
                      cn.id AS cid, cn.name AS cname, cn.continent AS continent
               FROM teams t
               JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall():
        if r["tid"] in _mil_tids_rr:   # [2026-10] 군팀 제외
            continue
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
    # 명단에 한 번이라도 포함) 여부를 조회해둔다 — 30세 미만 조기 은퇴
    # 확률에만 배율로 적용(30세 이상은 원 표 그대로, 국제경력이 은퇴
    # 자체를 막는 조건이 아니라 "조기 은퇴"만 억제하는 보정이어야 하므로).
    # [2026-09 2차, 신민용 확정: "월드컵/월드컵 외로 나누지 말고 국가대표
    # 경력 있음/없음 2단계로"] 예전엔 월드컵(kind='world') 출전 여부로
    # 0.2배를 한 단계 더 따로 뒀는데, 헤드리스 실측(월드컵 출전자 평균
    # 은퇴나이 35.7 vs 국가대표만 33.6 — 이미 충분히 차이가 크고 자연
    # 스럽다는 판단)에 따라 하나로 합친다. intl_squad에 한 번이라도
    # 포함되면(월드컵 포함, 월드컵도 이 표에 똑같이 기록됨) 전부 0.5배.
    _natteam_ids = {r["player_id"] for r in c.execute(
        "SELECT DISTINCT player_id FROM intl_squad").fetchall()}

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
        "SELECT id, team_id, position, age, name, ovr, nationality, quota_local_country, "
        "potential_ovr FROM ai_players").fetchall()
    # [2026-09 제거] AI 실명 폐지로 팀 내 이름 중복 관리 자체가 불필요해졌다
    # (_build_name_cache 주석 참고) — 26만 행 전체를 돌며 팀별 이름 set을
    # 만들던 비용이 사라진다.
    rows = []
    # [2026-07 신설] 팀별 현재 외국인 수 카운터 — 신인 국적 재배정 시
    # 쿼터(FOREIGN_QUOTA_CAP)를 그대로 지키기 위해 필요.
    # [2026-09 수정] 외국인 판정은 database.is_quota_foreign — 진짜 국적이
    # 달라도 그 나라에 자국 선수로 등록(quota_local_country)됐으면 안 센다.
    # 호출부가 넘긴 행에 그 컬럼이 없으면(구버전 호출/툴) ''로 본다.
    from database import is_quota_foreign, is_roster_foreign
    _src_has_qlc = bool(_src_rows) and "quota_local_country" in _src_rows[0].keys()
    foreign_count_by_team: dict = {}
    for r in _src_rows:
        rows.append(r)
        tinfo = team_info.get(r["team_id"])
        # [2026-09 수정] 로스터 인원 제한은 진짜 국적 기준(is_roster_
        # foreign) — 아래 신인 국적 추첨/영입 판정이 전부 이 카운터를
        # 보므로, 자국 등록 전환자를 빼고 세면 그 팀엔 외국인 칸이
        # 남아 있는 것처럼 보여 계속 외국인이 더 들어온다(database.
        # FOREIGN_NATURALIZE_MAX_PER_TEAM 정의부 실측 참고).
        if tinfo and is_roster_foreign(r["nationality"], tinfo[3]):
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
    # [2026-09 신설, 신민용 리포트 14번 + 요청 ②("나이 + OVR + 출전시간 +
    # 역할 + 대체자 + 임금 + 팀 수준을 같이 봐야 한다")] 은퇴 판정이
    # 여태 "나이 + 그 리그 기준 상대 OVR"만 봤다 — 팀에서 전력외로 밀린
    # 39세와 아직 주전인 39세를 완전히 똑같이 취급했고, 상대 OVR이
    # 그럭저럭이면(_relative_ovr_retire_mult가 은퇴를 억제) 자리가 없어도
    # 계속 현역으로 남았다.
    #
    # 원래 "시즌 출전시간"을 쓰려 했으나 이 엔진에는 그 축이 없다
    # (전 선수 26~49경기, 0경기 0명 — 근거는 _load_role_index 주석).
    # 대신 이미 저장돼 있고 화면에도 그대로 보이는 팀 내 역할을 **읽어서**
    # 쓴다(새로 계산하지 않는다). 이 값은 은퇴 판정의 여러 축 중 하나이지
    # 단독 결정 요인이 아니다 — 나이·리그 상대 OVR·국가대표 경력·커리어
    # 완성 보너스와 함께 곱해진다(_ai_retirement_probability 참고).
    _retire_role = _load_role_index(c, year)
    transfer_updates = []    # (new_team_id, contract_end_year, last_transfer_year, player_id)
    transfer_log_rows = []   # ai_transfer_log INSERT용
    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 1

    _rt3 = _time_rt.perf_counter()   # 후보풀 구축까지

    # [2026-09 신설] compute_ai_growth_cap(grade,tier,cname,continent)은
    # 팀마다 항상 같은 값이므로, 은퇴자마다 다시 계산하지 않고 팀 단위로
    # 캐싱한다 — roll_potential_ovr 호출부(아래 루프) 참고.
    _replacement_growth_cap_cache: dict = {}

    # [2026-10 병역, 신민용 확정: "복무 중 은퇴가 되더라도 복무는 다 하고 은퇴"]
    # (1) 지금 복무 중인 선수(군팀 소속이라 위 rows엔 없음)도 은퇴 판정은 한다 — 단
    #     확률은 일반 × MILITARY_RETIRE_PROB_MULT(0.5), 걸려도 바로 은퇴하지 않고
    #     예약(military_retire_pending=1)만 한다. 리그 수준·상대 OVR은 군팀이 아니라
    #     입대 전 소속팀 기준(커리어 판단의 기준점이 그쪽이므로).
    # (2) 예약된 채 이번 오프시즌에 제대한 선수(process_military_new_year가 이 함수보다
    #     먼저 돌아 이미 원소속팀/FA 행선지로 옮겨져 rows에 들어 있음)는 아래 루프에서
    #     확률 1로 은퇴시킨다 — 일반 은퇴와 같은 경로라 아카이브·대체 신인도 같다.
    _mil_retire_now: set = set()
    try:
        from constants import MILITARY_STATUS_SERVING as _MSS_RT
        from military_service import MILITARY_RETIRE_PROB_MULT as _MIL_RT_MULT
        _mil_retire_now = {row[0] for row in c.execute(
            "SELECT id FROM ai_players WHERE military_retire_pending=1 AND military_status!=?",
            (_MSS_RT,)).fetchall()}
        _mil_flag = []
        for row in c.execute(
                "SELECT id, age, ovr, position, military_pre_team_id FROM ai_players "
                "WHERE military_status=? AND COALESCE(military_retire_pending,0)=0", (_MSS_RT,)).fetchall():
            _sa = row[1] or 25
            if _sa < _AI_RETIRE_AGE:
                continue
            _ti = team_info.get(row[4])
            _p_mil = _ai_retirement_probability(
                _sa, row[2], row[3], category=(_ti[7] if _ti else "mid"), intl_factor=1.0,
                relative_mult=(_relative_ovr_retire_mult(row[2], _ti[0], _ti[1], _ti[3], _ti[8])
                               if _ti else 1.0)) * _MIL_RT_MULT
            if _p_mil > 0 and random.random() < _p_mil:
                _mil_flag.append((row[0],))
        if _mil_flag:
            c.executemany("UPDATE ai_players SET military_retire_pending=1 WHERE id=?", _mil_flag)
        if _mil_flag or _mil_retire_now:
            print(f"[MILITARY] {year} 복무 중 은퇴 예약 {len(_mil_flag)}명 | 제대 후 예약 은퇴 "
                  f"{len(_mil_retire_now)}명", flush=True)
    except Exception as _e_mrt:
        print(f"[MILITARY] 복무 중 은퇴 처리 건너뜀: {_e_mrt}", flush=True)
        _mil_retire_now = set()

    for r in rows:
        age = r["age"] or 25
        if r["id"] in _mil_retire_now:
            pass   # 예약 은퇴 — 아래에서 확률 1로 처리(나이 하한도 무시)
        elif age < _AI_RETIRE_AGE:
            continue
        _tinfo_r = team_info.get(r["team_id"])
        _cat_r = _tinfo_r[7] if _tinfo_r else "mid"
        _intl_factor = 0.5 if r["id"] in _natteam_ids else 1.0
        # [2026-09 신설] 그 리그 기준으로 이 선수가 에이스급인지 겨우
        # 버티는 수준인지 — _relative_ovr_retire_mult 정의부 주석 참고.
        _rel_mult = (_relative_ovr_retire_mult(r["ovr"], _tinfo_r[0], _tinfo_r[1], _tinfo_r[3],
                                                _tinfo_r[8])
                     if _tinfo_r else 1.0)
        # [2026-09 신설] 토니 크로스형 "커리어 완성" 은퇴 — _career_finish_bonus
        # 정의부 주석 참고, 실력(relative_mult)과 무관하게 더해지는 값.
        _finish_bonus = (_career_finish_bonus(r["id"], age, _tinfo_r[0], _tinfo_r[1])
                          if _tinfo_r else 0.0)
        p_retire = _ai_retirement_probability(age, r["ovr"], r["position"],
                                               category=_cat_r, intl_factor=_intl_factor,
                                               relative_mult=_rel_mult,
                                               career_finish_bonus=_finish_bonus,
                                               role_i=_retire_role.get(r["id"]))
        # [2026-09 신설, 신민용 지적: "82까지 내려왔으면 더 아래로 이적하든가
        # 은퇴를 하던가 그런 시기"] 소속 리그 기준선 미달 폭을 은퇴 확률에
        # 얹는다. 단 32세 이상 한정 — 미달은 1차적으로 하향 이적으로 풀려야
        # 하고(_SHORTFALL_DECLINE_PTS), 은퇴 가속은 더 내려갈 단계가 얼마
        # 안 남은 나이대에만 붙인다(_SHORTFALL_RETIRE_AGE 주석 참고).
        if _tinfo_r and age >= _SHORTFALL_RETIRE_AGE:
            _rsf2 = _league_shortfall(r["ovr"], _tinfo_r[0], _tinfo_r[1], _tinfo_r[3])
            if _rsf2 > 0.0:
                p_retire = min(1.0, p_retire * _interp_pts(_SHORTFALL_RETIRE_PTS, _rsf2))
        if r["id"] in _mil_retire_now:
            p_retire = 1.0
        if p_retire <= 0 or random.random() >= p_retire:
            continue

        # [버그수정] 신인 목표 OVR: 리그 등급/tier OVR_RANGES 하단~중간 범위
        #  + 대륙/나라 보정. [조정] 예전엔 중간값+5까지 허용해서 신인이 데뷔부터
        #  거의 에이스급으로 들어왔다(A등급 기준 82~91). 하단~중간(82~86)으로
        #  좁혀서, 실제로 몇 시즌 성장해야 에이스 근처에 도달하도록 한다.
        (grade, tier, _bonus, cname, continent, _tname, _club_strength, _cat_unused, _mt_unused,
         _mom_type, _mom_left) = team_info.get(
            r["team_id"], ("D", 1, 0, "", "유럽", "", 0.0, "mid", 1, "", 0))
        # [2026-09 신설, database.roll_potential_ovr 정의부 주석 참고] 이
        # 신인의 개인별 "전성기 도달 가능 상한" — team_cap(팀/리그 단위
        # 환경 상한)은 팀마다 한 번만 계산해 캐시한다(은퇴자마다 다시
        # 계산하면 낭비 — 같은 팀 은퇴자가 한 시즌에 여러 명일 수 있음).
        _new_growth_cap = _replacement_growth_cap_cache.get(r["team_id"])
        if _new_growth_cap is None:
            _new_growth_cap = compute_ai_growth_cap(grade, tier, cname, continent)
            _replacement_growth_cap_cache[r["team_id"]] = _new_growth_cap
        _p_world, _p_elite = _prestige_star_prob(grade, prestige_level(cname, _tname))
        # [2026-09] 97~99 포지션 가중치 — 총 스타 확률은 그대로 두고
        # 월드클래스/엘리트 배분만 포지션별로 옮긴다(database.
        # split_star_prob_by_position 주석 참고).
        from database import split_star_prob_by_position as _split_star
        _p_world, _p_elite = _split_star(_p_world, _p_elite, r["position"], "starter")
        _star_roll = random.random()
        if _star_roll < _p_world:
            _new_star_kind = "worldclass"
        elif _star_roll < _p_world + _p_elite:
            _new_star_kind = "elite"
        else:
            _new_star_kind = None
        _new_potential_ovr = roll_potential_ovr(_new_growth_cap, _new_star_kind)
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
        # [2026-09 버그수정, 신민용 리포트: "prestige_clubs.py 안에 레벨3인
        # 애들은 (소속 리그 등급과 무관하게) 다 똑같은 원리로 가야 한다"]
        # 기존엔 _plvl>=BIG_CLUB_PRESTIGE_THRESHOLD(2)면 "S 미만일 때만" S로
        # 끌어올리는 하한선 방식이었다 — 그래서 이미 SS/S 리그에 있는
        # 레벨3 명문팀은 이 보정을 건너뛰고 원래 리그 등급(SS면 0.90,
        # S면 0.85)을 그대로 썼다. 레벨3은 리그등급을 그대로 물려받는 게
        # 아니라 무조건 최상위(SS, 0.90)로 통일한다 — "레알/바르사가 어느
        # 리그에 있든 명문도는 같다"는 원칙을 레벨2 하한선 로직과 분리.
        _buy_grade = grade
        if _plvl == 3:
            _buy_grade = "SS"
        elif _plvl >= BIG_CLUB_PRESTIGE_THRESHOLD and _buy_grade not in ("SS", "S"):
            _buy_grade = "S"
        _buy_prob = BUY_REPLACEMENT_PROB_BY_GRADE.get(_buy_grade, 0.10)
        # [2026-09 신설, "중위권 정체 탈출" momentum] 위 target 가산과 같은
        # 이유 — 시장에서 검증된 선수를 사려는 시도 자체를 더 자주 하게
        # 만든다(constants.STAGNATION_BUY_PROB_BONUS). 0.97 상한은 다른
        # 확률 캡과 동일한 관례(완전한 100%는 피함).
        if _stag_active:
            _buy_prob = min(0.97, _buy_prob + STAGNATION_BUY_PROB_BONUS.get(_mom_type, 0.0))
        # [2026-09 버그수정, 신민용 리포트: "8명이 왜 나와? 5명 한계인데
        # 8명으로 뚫었잖아"] 실측(1시즌 계측 하니스)에서 이 함수 한 번이
        # 외국인 초과팀을 651 → 1,266팀(+615)으로 늘렸다 — 원인은 바로
        # 아래 시장영입(_find_buy_replacement) 경로가 쿼터를 아예 안 본다는
        # 것이었다. 은퇴자 자리를 "자체 유스 생성"으로 채우는 경로는
        # _pick_nationality가 쿼터에서 하드 스톱을 걸지만, 이 경로는
        # position/OVR/나이만 보고 전세계 최적 후보를 데려오므로 이미
        # 5명이 찬 팀에도 6번째 외국인이 그대로 들어왔다.
        #
        # 여기서 쿼터가 꽉 찬 팀은 "자국 국적자만" 후보로 보도록
        # domestic_nat_only 플래그를 넘긴다(후보가 없으면 None → 기존
        # 유스 생성 폴백으로 자연히 이어진다). 아래 youth 경로가 쓰는
        # cur_foreign/quota 계산을 그대로 여기로 끌어올려 재사용한다
        # (같은 값을 두 번 계산하지 않도록 아래에서는 이 값을 쓴다).
        tid = r["team_id"]
        _r_keys = r.keys()
        old_nat = r["nationality"] if "nationality" in _r_keys else ""
        _old_qlc = r["quota_local_country"] if "quota_local_country" in _r_keys else ""
        cur_foreign = foreign_count_by_team.get(tid, 0)
        if is_roster_foreign(old_nat, cname):
            cur_foreign = max(0, cur_foreign - 1)
        _q_lo, quota = get_foreign_quota_range(cname, continent, tier=tier)
        _quota_full = quota is not None and cur_foreign >= quota
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
                global_scouting=_global_scouting, stats=_buy_stats,
                dst_prestige_level=_plvl, domestic_nat_only=_quota_full)
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
                _bought_keys = _bought.keys()
                _bought_nat = _bought["nationality"] if "nationality" in _bought_keys else ""
                _bought_qlc = _bought["quota_local_country"] if "quota_local_country" in _bought_keys else ""
                if is_roster_foreign(_bought_nat, cname):
                    foreign_count_by_team[r["team_id"]] = foreign_count_by_team.get(r["team_id"], 0) + 1
                _src_cname = _src_tinfo[3] if _src_tinfo else ""
                if is_roster_foreign(_bought_nat, _src_cname):
                    foreign_count_by_team[_bought["team_id"]] = max(
                        0, foreign_count_by_team.get(_bought["team_id"], 0) - 1)
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
                _bought_cend = year + random.randint(3, 5)
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
        # [2026-09 신설, 신민용 요청 — database._squad_ovr_hard_min 주석 참고]
        # 5대리그 1부의 리그/명문 절대 최저선을 이 경로(은퇴 자리를 메우는
        # 시즌 중 신인 생성)에도 적용한다. 실측: 최초 생성에만 하한을 걸고
        # 여기를 빼두면 2시즌 만에 5대리그 1부 하한 미달이 453명 생기고
        # 그중 142명이 정확히 이 경로였다(나머지는 노쇠 후 리그 내 이적).
        # 명문 하한은 성인 기준값이라 나이 스케일 비율을 그대로 곱한다.
        _lhm, _phm = _squad_ovr_hard_min(cname, tier, _tname)
        if _lhm is not None:
            _afrac = (_scaled_target / target) if target else 1.0
            stats, new_ovr = _lift_stats_to_ovr(
                r["position"], stats, new_ovr, max(_lhm, (_phm or 0) * _afrac))
        # [2026-09 버그수정, 헤드리스 스모크테스트로 발견 — database.py
        # _generate_team_players의 동일 버그와 같은 원인] potential_ovr은
        # target(기존 곡선)과 독립적으로 미리 굴려뒀으므로, 방금 생성된
        # new_ovr이 우연히 그보다 높을 수 있다 — "잠재력이 지금 실력보다
        # 낮다"는 모순이므로 최소한 new_ovr만큼은 항상 보장한다.
        # [2026-09 신설, database._generate_team_players의 같은 보정과
        # 동일한 이유(신민용 리포트 23번)] 이 신인이 자라서 도달해야 할
        # 성인 기준 목표(target)보다 잠재력이 낮으면 설계 수준에조차
        # 못 간다 — 최소한 자기 목표까지는 클 수 있게 바닥을 건다.
        _new_potential_ovr = max(new_ovr, int(round(target)), _new_potential_ovr)
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
        # [2026-09 수정] tid/old_nat/cur_foreign/quota는 위 시장영입
        # 쿼터 게이트(_quota_full)에서 이미 계산해뒀다 — 같은 값을 두 번
        # 구하지 않고 그대로 이어서 쓴다.
        # [2026-09 신설, database._nat_ceiling_penalty 정의부 주석 참고]
        # 이 신인이 자라서 도달할 성인 기준 목표(target)를 국적 추첨에
        # 같이 넘긴다 — 그 나라 등급의 브레이크아웃 기준선을 넘기는
        # 국적은 가중치가 크게 깎여, 나중에 _enforce_intl_breakout_caps가
        # 명문팀 주전을 65~69로 깎아내리는 일이 안 생긴다.
        new_nat, cur_foreign = _pick_nationality(cname, continent, grade, r["position"],
                                                  False, cur_foreign, quota,
                                                  slot_ovr=target, youth=(tier or 1) <= YOUTH_PATH_MAX_TIER)
        foreign_count_by_team[tid] = cur_foreign
        name = ""      # [2026-09] AI 실명 폐지 — _build_name_cache 주석 참고
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
            *[stats[s] for s in ALL_STATS], new_ovr, new_age, new_sub_role, new_nat, new_nat,
            year + random.randint(3, 5), 0, year,
            _calc_ai_salary(grade, tier, new_ovr, cname, _tname, r["team_id"], year),
            _new_potential_ovr,
            # [2026-09 신설] database.CREATION_SOURCES 참고.
            "retire_repl"))
        retired += 1

    _rt4 = _time_rt.perf_counter()   # 은퇴판정+대체자탐색+신인생성 루프까지

    if retire_archives:
        c.executemany(
            """INSERT OR REPLACE INTO ai_players_retired
               (id, name, position, ovr, age, nationality, last_team_id,
                last_team_name, retirement_year)
               VALUES(?,?,?,?,?,?,?,?,?)""", retire_archives)
        # [2026-10] 최고 OVR·병역 상태도 같이 남긴다(아래 DELETE 전이어야 함).
        from database import fill_retired_extra_fields
        fill_retired_extra_fields(c, [a[0] for a in retire_archives])
    _rt5 = _time_rt.perf_counter()   # ai_players_retired 아카이브 적재
    if retire_deletes:
        c.executemany("DELETE FROM ai_players WHERE id=?", retire_deletes)
    _rt6 = _time_rt.perf_counter()   # ai_players 은퇴자 DELETE
    if new_rows:
        # [2026-09 신설, 위 은퇴대체 국적 재배정 주석 참고] new_nat를
        # nationality/true_nationality 둘 다에 넣는다 — 은퇴대체로 새로
        # 태어나는 신인의 "진짜 국적"은 이 시점에 딱 한 번 정해지고, 이후
        # 클럽 쿼터 전환으로 nationality만 바뀌어도 true_nationality는
        # 그대로 남는다(database.true_nationality 컬럼 주석 참고).
        c.executemany(
            f"""INSERT INTO ai_players
                (team_id,name,position,{_STAT_COLS},ovr,age,sub_role,nationality,
                 true_nationality,contract_end_year,last_transfer_year,created_year,
                 salary,potential_ovr,creation_source)
                VALUES(?,?,?,{','.join('?' for _ in ALL_STATS)},?,?,?,?,?,?,?,?,?,?,?)""",
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

# ─────────────────────────────────────────────────────────────
# [2026-09 신설] 위 상한(ceiling)의 거울 — 목적지 리그 설계 **하한**(lo)에
# 못 미치는 선수의 유입을 막는다. 상한 쪽 실패 경험(소프트 감쇠만으로는
# _size_weight 785배에 통째로 먹힌다)을 그대로 반영해 여기도 소프트 감쇠 +
# 하드 제외를 함께 둔다. 실측 근거는 dst_ovr_floor_by_tid 정의부 주석.
#
# 상한보다 기준을 훨씬 관대하게(3 → 12) 잡는 이유 — 하한 미달은 상한
# 초과와 달리 **정상적인 경우가 실제로 많다**:
#   · lo는 "그 리그 최약팀 벤치" 수준이라, 스쿼드 필러로 그보다 조금
#     낮은 선수가 들어오는 건 자연스럽다(실측 부족분 0~4가 39.9%).
#   · 노쇠로 OVR이 내려간 베테랑은 소속 리그 lo 밑으로 내려간 채 이적한다.
#   · 승격팀은 2부 기준으로 짜인 스쿼드를 그대로 1부로 데려온다(이 경로는
#     이적이 아니라 팀 승격이라 이 체크와 무관하지만, 그 선수들이 그 뒤
#     1부 안에서 이적할 때 하한 미달 상태다).
# 그래서 "챔피언십 OVR84 → 토트넘(lo 92, 부족분 8)"처럼 현실에서도 흔한
# 이적은 그대로 허용하고, "독일 4부 OVR63 → 토트넘(부족분 29)"처럼
# 설명이 안 되는 것만 잘라낸다.
_DST_FLOOR_DEFICIT_DENOM = 120.0   # 부족분 5→0.81배, 10→0.43배, 12→0.30배
_DST_FLOOR_HARD_EXCLUDE = 12.0     # 이 부족분을 넘으면 후보에서 제외

# 명문 등급 -> 목적지 가우시안 분모(작을수록 "우리 팀 수준에 맞는 선수만
# 받는다"). data/prestige_clubs.PRESTIGE_TEAMS의 3/2/1 등급 그대로.
# 등록 안 된 등급(=0, 일반팀)은 기본값 170.0.
_PRESTIGE_DST_DENOM = {3: 35.0, 2: 60.0, 1: 100.0}


# ─────────────────────────────────────────────────────────────
# [2026-09 신설, 신민용 요청 ①②] "AI 잔류/방출 판단" 보조 테이블.
#
# 기존 mover 선정 가중치는 (팀 내 OVR 순위, 나이, 계약, 포지션 과잉)
# 네 가지만 봤다 — 즉 "그 선수가 이번 시즌에 실제로 뛰었는지, 잘했는지,
# 연봉을 얼마나 먹는지"를 전혀 몰랐다. 신민용 리포트 14번("S리그 1부가
# 39세 OVR78을 계속 보유")이 정확히 그 구멍이다: 나이 배수(×1.7)만으로는
# 팀 내 OVR 순위가 중간만 돼도 다른 후보에 밀려 안 뽑힌다.
#
# 아래 세 배수는 전부 "데이터가 없으면 1.0"이 되도록 설계했다 — 갓
# 생성된 신인이나 구버전 세이브에서는 이번 변경 전과 완전히 같이 동작한다.
# ─────────────────────────────────────────────────────────────

# ── 팀 내 역할 ───────────────────────────────────────────────
# [2026-09, 헤드리스 계측으로 설계 변경] 처음엔 신민용이 ②에서 말한
# "시즌 출전시간"을 그대로 쓰려고 hist.ai_player_season_stats.matches를
# 팀 내 최다출전자 대비 비율(play_frac)로 정규화해 넣었는데, 실측해 보니
# **이 엔진에는 출전시간이라는 축 자체가 없었다**:
#     matches 분위 p1=26 p5=27 p10=28 p50=34 max=49, 0경기 0명
#     play_frac 분위 p10=0.76 p25=1.0 p50=1.0 p75=1.0 p90=1.0
# 즉 26명 스쿼드 전원이 사실상 모든 경기를 뛴 것으로 기록된다(AI 경기
# 시뮬레이션이 로테이션/벤치를 모델링하지 않는다). 그래서 play_frac 기반
# 판정은 전부 "전원 1.0"이라 아무 일도 하지 않는다 — 실제로 그렇게
# 넣었다가 12시즌 A/B에서 노장 지표만 개선이 안 되는 것으로 확인했다.
#
# 대신 이 엔진이 실제로 가지고 있는 "팀 내 역할"을 쓴다 — formation_logic.
# compute_squad_roles가 매 시즌 계산해 ai_player_position_history.role에
# 남기고 화면에도 그대로 보이는 그 값(주전/로테이션/대기/전력외/유망주)이다.
# 판정 기준을 그 함수와 똑같이 맞춘다: "같은 포지션군 안에서 OVR 순위
# 백분위"를 _ROLE_TIER_WEIGHTS(40/30/25/15)의 누적 경계로 자른다. 과거
# 기록을 읽는 대신 그 자리에서 현재 로스터로 계산하므로(정렬은 이미 하고
# 있던 것을 그대로 활용) 추가 조회도, 한 시즌 묵은 값을 쓰는 문제도 없다.
# 저장된 role 문자열 → 인덱스. compute_squad_roles가 내는 다섯 값이
# 전부다(실측 분포: 주전 128,903 / 로테이션 63,697 / 대기 53,724 /
# 전력외 35,275 / 유망주 10,815).
_ROLE_INDEX = {"핵심": 0, "주전": 1, "로테이션": 2, "대기": 3, "전력외": 4, "유망주": 5}

# ─────────────────────────────────────────────────────────────
# [2026-09 신설, 신민용 확정: "팀 경기수 × 역할별 출전률로 시즌 통계
# 생성 단계에서 한 번 계산하면 된다 — 경기 하나하나를 시뮬레이션할
# 필요 없다"] 역할 → 시즌 출전률.
#
# 이 엔진은 전세계 AI 경기를 팀 평균 OVR로만 시뮬레이션하므로(game_engine.
# _sim_all_ai_matches) 선수별 출전 기록이 생길 자리가 없다. 그래서
# "경기마다 누가 뛰었나"를 만드는 대신 시즌 통계를 만드는 그 자리에서
# 한 번만 배분한다 — 선수 26만 명 규모에서 유일하게 현실적인 방식이다.
#
# (중심값, 반폭) — 실제 출전률은 [중심-반폭, 중심+반폭] 균등분포에서
# 뽑는다. 정확히 70%/40%를 강제하지 않고 분포를 만드는 게 핵심이다
# (신민용 명시: 부상·정지·일정·컵대회·체력 등으로 주전도 64%를 뛸 수
# 있어야 한다). 38경기 기준 대략:
#   주전 27~34 / 로테이션 15~26 / 대기 6~15 / 전력외 0~7
_ROLE_PLAY_RATIO = {
    # [2026-09 신설] 핵심 — 약 84~96%. 상한은 100%(클리핑)로 열어둔다.
    "핵심":     (0.90, 0.06),
    "주전":     (0.80, 0.09),
    "로테이션": (0.54, 0.145),
    "대기":     (0.28, 0.12),
    "전력외":   (0.09, 0.09),
    # 유망주는 10대 육성 대상 — 간간이 출전하는 정도로 따로 잡는다.
    "유망주":   (0.14, 0.12),
}
# 역할을 모르는 경우(이 기능 신설 이전 과거 시즌, 역할 스냅샷이 아직
# 없는 경로)는 배분하지 않고 예전처럼 팀 경기수를 그대로 쓴다 — 과거
# 시즌 기록이 소급해서 바뀌면 안 되므로.
_ROLE_PLAY_NONE = 1.0

# ── 출전 교란 이벤트 ─────────────────────────────────────────
# [2026-09 신설, 신민용 확정: "실제 축구에서는 주전이 부상·징계·국가대표
# 차출·컨디션·경쟁자의 성장·겨울 이적·전술 변화 등으로 시즌의 60%대만
# 출전할 수도 있다 — 다만 주전 하한을 그냥 낮추는 게 아니라 평균은
# 유지하면서 하방 꼬리를 만드는 게 맞다"]
#
# 위 균등분포만 쓰면 주전은 구조적으로 [0.71, 0.89]에 갇혀 70% 밑이
# 사실상 안 나온다(실측: 주전 중 70% 미만 0.7%). 그래서 일정 확률로
# "그 시즌에 무슨 일이 있었다"를 한 번 굴려 출전률을 크게 깎는다 —
# 원인을 따로 모델링하지 않고 하나의 사건으로 추상화한다(이 엔진은
# 애초에 경기 단위 부상/징계를 시뮬레이션하지 않는다).
#
# [평균 보존] 교란을 그냥 얹으면 역할별 평균이 통째로 내려가 앞서
# 15시즌으로 검증한 0.90/0.80/0.54 값이 무너진다. 그래서 중심값을
# 교란으로 잃는 기대값만큼 미리 올려둔다:
#     보존계수 = 1 - p × (1 - E[감쇠])
#     보정중심 = 원래중심 / 보존계수
# 이러면 분포만 바뀌고 평균은 그대로다.
#
# [적용 대상] 핵심/주전만. 처음엔 로테이션까지 넣었는데, 평균 보존을
# 위해 중심값을 올리면 밴드 상단도 같이 올라가서(0.54+0.145 → 0.568+
# 0.145 = 0.713) "로테이션인데 70% 이상"이 0.6% → 5.3%로 뛰었다 —
# 신민용이 감시 지표로 꼽은 항목이라, 이번 변경의 목적(주전 하한)과
# 무관한 부작용을 만들지 않도록 제외한다. 로테이션에도 꼬리를 주는 건
# 그 자체로 타당하지만(주전 이탈로 로테이션이 갑자기 많이 뛰는 일은
# 실제로 있다) 별도 변경·별도 A/B로 다루는 게 맞다.
# 대기/전력외/유망주는 이미 바닥 근처라 하방 꼬리를 더해봐야 0에
# 눌려 분포가 찌그러지기만 한다.
_PLAY_DISRUPT_ROLES = frozenset(("핵심", "주전"))
_PLAY_DISRUPT_PROB = 0.14      # 이 확률로 시즌 교란 발생
_PLAY_DISRUPT_LO = 0.45        # 발생 시 출전률에 곱할 감쇠 구간
_PLAY_DISRUPT_HI = 0.85
_PLAY_DISRUPT_KEEP = 1.0 - _PLAY_DISRUPT_PROB * (
    1.0 - (_PLAY_DISRUPT_LO + _PLAY_DISRUPT_HI) / 2.0)


def _roll_play_ratios(role_names, rng):
    """역할 이름 리스트 → 그 시즌 출전률(0.0~1.0) 리스트.

    난수는 선수당 정확히 3개를 **항상** 소비한다(교란이 안 걸려도) —
    조건부로 뽑으면 이 시점 이후 전역 난수 스트림이 갈라져 재현이
    깨진다(이 파일의 다른 재현성 주석들과 같은 원칙).
    """
    n = len(role_names)
    if n == 0:
        return []
    u = rng.random((n, 3))
    out = []
    _get = _ROLE_PLAY_RATIO.get
    _keep = _PLAY_DISRUPT_KEEP
    _dis_roles = _PLAY_DISRUPT_ROLES
    _dlo, _dspan = _PLAY_DISRUPT_LO, (_PLAY_DISRUPT_HI - _PLAY_DISRUPT_LO)
    for i in range(n):
        _name = role_names[i]
        _spec = _get(_name)
        if _spec is None:
            out.append(_ROLE_PLAY_NONE)
            continue
        _c, _h = _spec
        if _name in _dis_roles:
            _c = _c / _keep          # 교란으로 잃을 기대값만큼 미리 보정
            r = _c + (u[i, 0] * 2.0 - 1.0) * _h
            if u[i, 1] < _PLAY_DISRUPT_PROB:
                r *= _dlo + u[i, 2] * _dspan
        else:
            r = _c + (u[i, 0] * 2.0 - 1.0) * _h
        if r < 0.0:
            r = 0.0
        elif r > 1.0:
            r = 1.0
        out.append(r)
    return out
# 역할별 방출 가중 배수. 주전은 지키고 전력외는 가장 먼저 정리하되,
# "유망주"(10대 육성 대상)는 최하위 구간이어도 오히려 약하게 보호한다 —
# 안 그러면 10대가 전부 방출 1순위가 되어 육성 서사가 사라진다.
#
# [범위 제한 — 신민용 명시 조건] 이 값은 **AI 이적/잔류 판단에서만**
# 쓰는 보조 지표다. 경기 엔진의 출전 기록에도, 선수 평가(OVR/잠재력)
# 체계에도 손대지 않는다. 그리고 이 배수 하나로 이적·잔류가 결정되지
# 않는다 — 아래 mover 가중치에서 나이·OVR순위·계약·포지션 과잉·평점·
# 연봉과 **함께** 곱해지는 여러 축 중 하나일 뿐이다.
# [2026-09 확장] 맨 앞이 "핵심". 신민용 확정대로 이번 A/B에서는 이적
# 로직을 크게 건드리지 않는다 — 핵심을 세게 보호하면 앞서 고친 "리그를
# 씹어먹는 에이스가 오히려 안 팔리는 역설"(리포트 40번)이 되살아난다.
# 주전(0.70)보다 아주 조금만 낮은 값으로 시작하고, 실제로 "핵심이라서
# 너무 안 팔린다"가 측정되면 그때 조정한다.
_ROLE_SELL_W = (0.66, 0.70, 1.00, 1.45, 2.00, 0.85)


def _load_role_index(c, year):
    """{player_id: 역할인덱스} — hist.ai_player_position_history에서 **읽기만**
    한다(새로 계산하지 않는다).

    [왜 이 표인가 — 신민용 리포트 14번] 원래는 "시즌 출전시간"으로 잔류를
    판단하려 했는데, 헤드리스 실측 결과 이 엔진에는 그 축이 아예 없었다:
    전 선수가 26~49경기를 뛴 것으로 기록되고 0경기 선수는 한 명도 없다
    (AI 경기 시뮬레이션이 로테이션/벤치를 모델링하지 않는다). 그래서
    출전시간 기반 판정은 전원 동일값이라 아무 일도 하지 않는다.
    반면 이 표의 role은 실제로 잘 갈린다(주전 12.9만 / 로테이션 6.4만 /
    대기 5.4만 / 전력외 3.5만 / 유망주 1.1만) — 화면에 보이는 값과도
    같으므로, 출전시간이 생기기 전까지의 대체 지표로 이걸 쓴다.

    연도: 그 해(year) 행을 먼저 본다(game_engine이 43주차에
    _snapshot_season_positions로 이미 채운다). 아직 없으면 직전 시즌으로
    떨어진다 — 오프시즌 판단에 "지난 시즌 팀 내 역할"을 쓰는 건 자연스럽고,
    겨울 이적창구(run_ai_mid_season_transfer)에서는 애초에 그쪽이 맞다.
    둘 다 없으면 빈 dict를 돌려주고, 호출부는 전부 "역할 모름"(중립)으로
    동작한다 — 구버전 세이브에서도 이번 변경 전과 같이 동작한다."""
    out: dict = {}
    _mi = _ROLE_INDEX
    try:
        for _y in (year, year - 1):
            for _r in c.execute(
                    "SELECT player_id, role FROM hist.ai_player_position_history "
                    "WHERE year=?", (_y,)).fetchall():
                _i = _mi.get(_r["role"])
                if _i is not None:
                    out[_r["player_id"]] = _i
            if out:
                break
    except Exception:
        return {}
    return out
# 팀 평균 대비 평점차 → 방출 가중 배수(비대칭). 못한 선수는 확실히
# 정리하되, 잘한 선수를 "안 팔린다"까지 만들면 리그 최고 선수가 영원히
# 그 팀에 박히는 기존 역설(아래 _outlier_intl_multiplier 주석 참고)이
# 오히려 심해지므로 보호 쪽은 약하게만 건다.
_RATING_W_BAD = 0.45      # 평점 -1.0 → ×1.45
_RATING_W_GOOD = 0.18     # 평점 +1.0 → ×0.82
# "고임금인데 주전이 아니다" — 현실 구단이 가장 먼저 정리하는 유형.
_HIGH_WAGE_PCT = 0.25     # 팀 내 연봉 상위 25% 이내
_HIGH_WAGE_ROLE = 3       # 그런데 역할이 "대기"(3) 이하
_HIGH_WAGE_MULT = 1.6
# 노장 + 비주전 조합(리포트 14번 직격). 34세부터 나이에 비례해 가속.
_VET_BENCH_AGE = 34
_VET_BENCH_ROLE = 3       # 역할 인덱스가 이 값 이상(대기/전력외)이면 해당
_VET_BENCH_STEP = 0.25
# 노장 하향 이동: 34세 이상이고 거의 못 뛴 선수는 같은 등급 리그 안에서
# 자리를 찾기보다 한 단계 아래 무대로 내려가는 게 자연스럽다. 이 확률로
# 목적지 후보군을 "하향 풀"로 바꾼다(_do_one_transfer_cached 참고).
_VET_DECLINE_CHANCE = 0.55

# ══════════════════════════════════════════════════════════════
# [2026-09 신설, 신민용 확정 — "베테랑 은퇴 무대" 경로 재설계]
# 정의: 은퇴하지 않은 노장 중 이적시장에 들어온 선수의 "목적지"만 더
# 현실적으로 만드는 패치다. 은퇴 판정(_retire_and_replace, 이적시장보다
# 먼저 돈다)·팀별 방출 인원·해외 이적 갈래 비율(5%~35%)은 전혀 안 건드린다.
# 기존 장치: 5대리그(S/SS) 1부 → 해외 갈래 → 30세+면 60% 확률로 사우디·
# 미국 1부 풀로 전환. 실측(14시즌 헤드리스, 2010~13): 5대리그 1부를 떠난
# 30세+ OVR80+ 연 223~264명 중 사우디 0~1명, 카타르·중국 0명, A급 유럽
# 1부 8~16명(B급 28~43명보다 적음) — 하향 경로가 너무 좁았다(원인: 사우디
# 설계상한 79+3=82를 넘는 선수는 후보에서 전부 제외, 카타르·중국은 풀에
# 아예 없음, A급 유럽은 출발지가 아니라 2단계 경로가 끊김).
# 새 규칙:
#   자격   30세+, 현재 OVR 80~89(90+는 유럽 잔류 — 이 경로 제외),
#          전성기(peak_ovr, 없으면 현재 OVR) 86+
#   출발지 S/SS 1부 + A급 유럽 1부(B급은 출발지 아님)
#   시도   기존 60% → 전성기 등급에 따라 최대 70%(실패하면 기존 흐름 그대로)
#   목적지 전성기 등급(이름값) × 현재 OVR 구간(지금 뛸 수 있는 수준) 표로
#          A급 유럽/B급 유럽/미국/사우디/카타르/중국 중 하나를 가중 추첨
#   상한   이 경로에서만 도착 리그 설계상한 초과 허용폭을 일반 +3 대신
#          사우디 +6(최대 89 — 사우디 90+ 금지 유지)/카타르 +9/중국 +13
#   인원   팀당 "고급 베테랑"(30세+·전성기 88+·외국인) 상한 — 리그 평균이
#          방금 맞춘 아시아 서열(J1>사우디>K1…)을 넘지 않도록
# ══════════════════════════════════════════════════════════════
_VR_MIN_AGE = 30
_VR_CUR_RANGE = (80, 89)
_VR_MIN_PEAK = 86
_VR_HIGH_PEAK = 88            # 팀당 인원 상한을 셀 때의 "고급 베테랑" 기준
_VR_GROUPS = ("A_EU", "B_EU", "US", "SA", "QA", "CN")
_VR_CEIL_EXTRA = {"SA": 3, "QA": 6, "CN": 10}   # 일반 +3 위에 더하는 폭(합계 6/9/13)
# 팀당 고급 베테랑 상한 (lo, hi) — 팀마다 이 범위에서 고정값(팀 id 기반)
_VR_TEAM_CAP = {"US": (3, 4), "SA_BIG": (4, 4), "SA": (2, 3), "QA": (2, 3), "CN": (1, 2)}
# 전성기 등급별 시도 확률(기존 60%에서 최대 70%까지만)
_VR_ATTEMPT_P = {"T96": 0.70, "T93": 0.67, "T90": 0.64, "T88": 0.62, "T86": 0.60}
# 전성기 등급 × 현재 OVR 구간 → 목적지 가중치 (A_EU, B_EU, US, SA, QA, CN)
#   C87=87~89(A급 유럽 우선), C84=84~86, C80=80~83
_VR_DEST_W = {
    ("T96", "C87"): (60, 0, 20, 20, 0, 0),
    ("T96", "C84"): (35, 0, 30, 30, 5, 0),
    ("T96", "C80"): (0, 10, 35, 35, 20, 0),
    ("T93", "C87"): (55, 0, 20, 20, 5, 0),
    ("T93", "C84"): (30, 5, 25, 25, 15, 0),
    ("T93", "C80"): (0, 15, 25, 25, 25, 10),
    ("T90", "C87"): (50, 10, 15, 15, 10, 0),
    ("T90", "C84"): (25, 20, 15, 20, 20, 0),
    ("T90", "C80"): (0, 30, 10, 10, 30, 20),
    ("T88", "C87"): (40, 30, 5, 5, 20, 0),
    ("T88", "C84"): (0, 40, 5, 10, 30, 15),
    ("T88", "C80"): (0, 40, 0, 0, 30, 30),
    ("T86", "C87"): (30, 40, 0, 0, 20, 10),
    ("T86", "C84"): (0, 45, 0, 0, 30, 25),
    ("T86", "C80"): (0, 45, 0, 0, 25, 30),
}


def _vr_peak_tier(peak):
    if peak >= 96:
        return "T96"
    if peak >= 93:
        return "T93"
    if peak >= 90:
        return "T90"
    if peak >= 88:
        return "T88"
    return "T86"


def _vr_cur_band(cur):
    if cur >= 87:
        return "C87"
    if cur >= 84:
        return "C84"
    return "C80"


def _vr_pick_group(mover, vr):
    """베테랑 경로 자격·시도 판정 후 목적지 그룹 키를 돌려준다(None=기존
    흐름 그대로). vr: _transfer_market이 만든 컨텍스트(groups/group_max/
    peak_by_pid 등)."""
    cur = mover.get("ovr") or 0
    if mover.get("age", 0) < _VR_MIN_AGE:
        return None
    if not (_VR_CUR_RANGE[0] <= cur <= _VR_CUR_RANGE[1]):
        return None
    peak = max(vr["peak_by_pid"].get(mover["id"], 0) or 0, cur)
    if peak < _VR_MIN_PEAK:
        return None
    ptier = _vr_peak_tier(peak)
    if random.random() >= _VR_ATTEMPT_P[ptier]:
        return None
    base = _VR_DEST_W[(ptier, _vr_cur_band(cur))]
    keys, ws = [], []
    for g, w in zip(_VR_GROUPS, base):
        if w <= 0 or len(vr["groups"].get(g, ())) == 0:
            continue
        if cur > vr["group_max"].get(g, 0):
            continue   # 그 무대의 (특례 포함) 상한으로도 못 받는 수준이면 후보에서 뺀다
        keys.append(g)
        ws.append(w)
    if not keys:
        return None
    return random.choices(keys, weights=ws, k=1)[0]

# [2026-09 신설, 신민용 리포트 40번] 1부가 아닌 팀의 이적 목적지 분기
# 비율. 기존에는 하위 tier 선수의 목적지가 100% 같은 리그였다
# (_transfer_market의 해당 분기 주석 참고). 국내 상/하위 tier를 각각
# 6%씩 열어둔다 — 폭을 크게 잡을 필요가 없는 이유는, 진짜로 리그 수준을
# 뛰어넘은 선수는 별도 통로(_upward_transfer_pull)가 훨씬 강하게 끌어
# 올리기 때문이다. 이 분기는 "평범한 한 계단 이동"만 담당한다.
_LOWER_TIER_SAME_UPPER = 0.88
_LOWER_TIER_UP_UPPER = 0.94

# ══════════════════════════════════════════════════════════════
# [2026-09 신설, 신민용 지적] 리그 수준 미달 선수의 배출 경로
# ══════════════════════════════════════════════════════════════
# 원문: "82까지 내려왔으면 이 선수는 애초에 더 아래로 이적하든가 은퇴를
# 하던가 그런 시기인데 이게 뭔말임?"
#
# 기존 구조에 있던 공백 4개:
#   · 생성 하한(COUNTRY_LEAGUE_OVR_HARD_MIN)은 **신규 생성 선수만** 다룬다.
#   · 노쇠화(_aging_target_ovr)는 peak_ovr 대비 절대 비율이라 "지금 소속
#     리그 수준과 얼마나 벌어졌는지"를 아예 참조하지 않는다.
#   · 하향 이동 통로(_decline_pool)는 34세 + 대기/전력외 게이트여서, 28세에
#     OVR이 무너진 선수는 어느 경로에도 안 걸린다(1부 팀만 풀이 만들어짐).
#   · 상향 통로(_upward_transfer_pull)는 `ovr - 설계상한`(breakout)으로
#     판정하는데 그 거울인 하향 판정이 아예 없었다.
#
# 그래서 "미달 폭(shortfall)"을 한 번만 정의하고, **OVR을 강제 보정하지
# 않고**(신민용 지정: "강제 OVR 보정으로 해결하면 안 돼 — 노쇠화 시스템이
# 망가져") 아래 네 곳에 입력으로만 넣는다:
#   ① 매물(mover) 선정 가중치      _SHORTFALL_SELL_PTS
#   ② 하향 목적지 풀 전환 확률      _SHORTFALL_DECLINE_PTS
#   ③ 재계약 확률                  _SHORTFALL_RENEW_PTS
#   ④ 은퇴 확률(32세 이상 한정)     _SHORTFALL_RETIRE_PTS
#
# 기준선(_league_ovr_floor_ref): 등록된 리그는 COUNTRY_LEAGUE_OVR_HARD_MIN
# 그대로, 미등록 리그는 설계 lo(≈리그 평균) - _SHORTFALL_SLACK. 갓 시딩
# 실측에서 리그 최저선이 전 리그 예외 없이 lo-5~7로 나오므로(등급 무관)
# 그 바닥을 슬랙으로 쓴다 — 즉 판정 기준은 "그 리그에서 **새로 태어날 수
# 있는 가장 약한 선수**보다도 더 약해졌는가"다. 리그 평균(lo)을 그대로
# 기준선으로 쓰면 리그의 절반이 미달로 잡혀 정상 스쿼드가 붕괴한다.
_SHORTFALL_SLACK = 6.0
# ①~④ 각 배율/확률의 (미달폭, 값) 꺾은선. 미달 2 정도는 "그 리그 최약체와
# 비슷" 수준이라 거의 건드리지 않고, 두 자리로 벌어지면 강하게 민다.
_SHORTFALL_SELL_PTS = ((0.0, 1.0), (2.0, 1.35), (5.0, 2.0), (9.0, 3.0), (15.0, 4.0))
_SHORTFALL_DECLINE_MIN = 2.0
_SHORTFALL_DECLINE_PTS = ((2.0, 0.20), (5.0, 0.45), (9.0, 0.70), (15.0, 0.85))
_SHORTFALL_RENEW_PTS = ((0.0, 1.0), (3.0, 0.72), (7.0, 0.45), (12.0, 0.25))
# 은퇴는 나이 게이트를 남긴다 — 신민용 지정: "34세에 갑자기 은퇴시킬 필요는
# 없어. 현실적인 흐름은 PSG → 중위권 → 2부 → 은퇴처럼 단계적으로 내려가는
# 것." 즉 미달은 1차적으로 **하향 이적**으로 풀려야 하고, 은퇴 가속은
# 내려갈 단계가 얼마 안 남은 나이대에만 얹는다.
_SHORTFALL_RETIRE_AGE = 32
_SHORTFALL_RETIRE_PTS = ((0.0, 1.0), (4.0, 1.20), (8.0, 1.55), (14.0, 2.00))

_SHORTFALL_REF_CACHE: dict = {}


def _interp_pts(pts, x) -> float:
    """(x, y) 꺾은선 선형보간. 양 끝은 클램프."""
    if x <= pts[0][0]:
        return pts[0][1]
    for i in range(1, len(pts)):
        x1, y1 = pts[i]
        if x <= x1:
            x0, y0 = pts[i - 1]
            _sp = x1 - x0
            return y0 if _sp <= 0 else y0 + (y1 - y0) * (x - x0) / _sp
    return pts[-1][1]


def _league_ovr_floor_ref(grade, tier, country) -> float:
    """그 리그에서 "새로 태어날 수 있는 가장 약한 선수"의 OVR.
    (등급, tier, 국가) 단위 캐싱 — 팀 수보다 조합 수가 훨씬 적다."""
    _ck = (grade, tier, country)
    _hit = _SHORTFALL_REF_CACHE.get(_ck)
    if _hit is not None:
        return _hit
    from constants import get_ovr_range, COUNTRY_LEAGUE_OVR_HARD_MIN
    _ent = COUNTRY_LEAGUE_OVR_HARD_MIN.get(country)
    if isinstance(_ent, dict):
        _hard = _ent.get(tier)
    else:
        _hard = _ent if tier == 1 else None
    if _hard is not None:
        _out = float(_hard)
    else:
        _rng = get_ovr_range(grade, tier, country)
        _out = (float(_rng[0]) - _SHORTFALL_SLACK) if _rng else 0.0
    _SHORTFALL_REF_CACHE[_ck] = _out
    return _out


def _league_shortfall(ovr, grade, tier, country) -> float:
    """소속 리그 기준선 대비 미달 폭(0 이상). 0이면 정상."""
    if not ovr:
        return 0.0
    return max(0.0, _league_ovr_floor_ref(grade, tier, country) - float(ovr))


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
                  t.name AS tname, cn.id AS cid, cn.name AS cname, cn.continent AS continent,
                  t.momentum_type AS momentum_type, t.momentum_seasons_left AS momentum_seasons_left,
                  (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) AS avg_ovr
           FROM teams t
           JOIN leagues l ON t.league_id = l.id
           JOIN countries cn ON l.country_id = cn.id
           ORDER BY t.id""").fetchall()]
    teams = _drop_mil_teams(c, teams, "tid")   # [2026-10] 군팀 제외
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
    # [2026-09 신설, 신민용 리포트: "K리그에 외국인만 절반 이상인 팀도
    # 나온다"] database.FOREIGN_QUOTA_RANGE는 팀 생성/은퇴교체/내 선수
    # 입단 때만 지켜지고, 정작 매 시즌 도는 이 AI 이적 시장은 국적을
    # 전혀 안 봐서 시즌을 거듭할수록 한도 없이 외국인이 쌓일 수 있었다.
    # 팀별 상한(quota_hi)은 국가/대륙/tier로만 정해지는 정적인 값이라
    # 여기서 한 번만 조회해둔다 — _do_one_transfer_cached가 목적지 후보를
    # 고를 때 이 상한을 넘는 팀은 제외한다(아래 dst_quota_hi_by_tid 전달부
    # 참고).
    from database import get_foreign_quota_range, is_quota_foreign, is_roster_foreign
    dst_quota_hi_by_tid = {
        t["tid"]: get_foreign_quota_range(t["cname"], t.get("continent"), tier=t["tier"])[1]
        for t in teams}
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
    # [2026-09 신설, 신민용 리포트: "OVR63인 선수가 토트넘에 왜 들어와 —
    # 각 리그 팀별로 최소치가 정해져 있을 텐데 그 기준도 못 맞춘 애들이
    # 들어오니 이상해지지"] 위 상한(ceiling)의 **거울 방향**. 지금까지
    # 목적지 판정은 "이 나라에 과분한 선수"만 막고, "이 나라 설계 하한에
    # 한참 못 미치는 선수"는 전혀 안 봤다 — get_ovr_range의 lo를 같은
    # 방식으로 캐싱해 _do_one_transfer_cached에 넘긴다.
    # 실측(2시즌, 이적 184,446건의 "목적지 리그 lo − 이적 시점 OVR"):
    #     미달 아님   44.17%
    #     부족분 0~4  39.91%   ← 스쿼드 필러·노쇠 베테랑. 정상 범위로 본다
    #     부족분 5~9  10.99%
    #     부족분10~14  3.57%
    #     부족분15+    1.37%(2,519건) ← 명백히 이상. 최악은 OVR31 → 아르헨 2부(lo75)
    # 그 결과 1·2부 선수 135,414명 중 41.1%가 소속 리그 설계 하한 미달이고,
    # 잉글랜드 1부(lo=92)에 OVR 56·70·76이 섞여 있었다(신민용 리포트의
    # 토트넘 OVR63 사례가 정확히 이 경로).
    dst_ovr_floor_by_tid = {}
    for t in teams:
        _rng = get_ovr_range(dst_grade_by_tid[t["tid"]], t["tier"], t["cname"])
        dst_ovr_ceiling_by_tid[t["tid"]] = _rng[1] if _rng else 43
        # 하한도 같은 폴백 관례(등급표에 그 tier가 없으면 근사치)를 쓴다.
        dst_ovr_floor_by_tid[t["tid"]] = _rng[0] if _rng else 30
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
    # [2026-09 신설] 베테랑 은퇴 무대 경로(_pick_vet_route 정의부 주석 참고)
    # 컨텍스트 — 목적지 그룹(A/B급 유럽·미국·사우디·카타르·중국 1부), 출발지
    # 집합(S/SS 1부 + A급 유럽 1부), 선수별 전성기, 팀별 고급 베테랑 인원/상한.
    global _LAST_VET_ROUTE_TALLY
    _vet_groups = {g: [] for g in _VET_ROUTE_GROUPS}
    _vet_origin_tids = set()
    _vet_lg_cache: dict = {}
    for t in teams:
        if t["tier"] != 1:
            continue
        _cn = t["cname"]
        _lg = _vet_lg_cache.get(_cn)
        if _lg is None:
            _lg = _vet_lg_cache[_cn] = get_country_league_grade(_cn)
        if _lg in ("SS", "S"):
            _vet_origin_tids.add(t["tid"])
        _grp = _VET_ROUTE_COUNTRY_GROUP.get(_cn)
        if _grp is None and t.get("continent") == "유럽":
            if _lg == "A":
                _grp = "A_EU"
                _vet_origin_tids.add(t["tid"])
            elif _lg == "B":
                _grp = "B_EU"
        if _grp is not None:
            _vet_groups[_grp].append(t["tid"])
    _vet_group_max = {}
    for _g, _tl in _vet_groups.items():
        _allow = _VET_ROUTE_CEIL_ALLOW.get(_g, _DST_CEIL_HARD_EXCLUDE)
        _cs = [dst_ovr_ceiling_by_tid.get(_t) for _t in _tl]
        _cs = [x for x in _cs if x is not None]
        _vet_group_max[_g] = (max(_cs) + _allow) if _cs else 99
    _vet_peak: dict = {}
    _vet_cnt: dict = {}
    for _r in c.execute(
            "SELECT id, team_id, peak_ovr, nationality FROM ai_players "
            "WHERE age>=? AND peak_ovr>=?", (_VET_ROUTE_MIN_AGE, _VET_ROUTE_MIN_PEAK)).fetchall():
        _vet_peak[_r["id"]] = _r["peak_ovr"]
        if (_r["peak_ovr"] >= _VET_ROUTE_HIGH_PEAK and _r["nationality"]
                and _r["nationality"] != dst_country_by_tid.get(_r["team_id"])):
            _vet_cnt[_r["team_id"]] = _vet_cnt.get(_r["team_id"], 0) + 1
    _vet_cap = {}
    for _g in ("US", "SA", "QA", "CN"):
        for _t in _vet_groups[_g]:
            _vet_cap[_t] = _vet_team_cap(_t, _g, dst_prestige_by_tid.get(_t))
    _vet_ctx = {"groups": _vet_groups, "group_max_ovr": _vet_group_max,
                "peak": _vet_peak, "cnt": _vet_cnt, "cap": _vet_cap, "tally": {}}
    _LAST_VET_ROUTE_TALLY = _vet_ctx["tally"]
    # [2026-10 신설] 브라질 수출 경로 컨텍스트 — constants.BRAZIL_EXPORT_* 주석
    # 참고. 목적지 후보는 A~F급 나라(브라질 제외)의 1~2부 팀이고, 실제 후보
    # 리스트는 선수 OVR별로 "상한·하한 하드 제외를 통과하는 팀"만 묶어
    # 처음 쓰일 때 만들어 이 호출 동안 재사용한다(_try_brazil_export).
    global _LAST_BRA_EXPORT_TALLY
    from constants import (BRAZIL_EXPORT_ROUTE_P, BRAZIL_EXPORT_DEST_TIERS,
                           BRAZIL_EXPORT_DEST_BY_TIER)
    _bra_dest_grades = {g for gs in BRAZIL_EXPORT_DEST_BY_TIER.values() for g in gs}
    _bra_base = [(t["tid"], dst_grade_by_tid.get(t["tid"]),
                  dst_ovr_floor_by_tid[t["tid"]], dst_ovr_ceiling_by_tid[t["tid"]])
                 for t in teams
                 if t["tier"] in BRAZIL_EXPORT_DEST_TIERS and t["cname"] != "브라질"
                 and dst_grade_by_tid.get(t["tid"]) in _bra_dest_grades]
    _bra_ctx = ({"p": BRAZIL_EXPORT_ROUTE_P, "dest": BRAZIL_EXPORT_DEST_BY_TIER,
                 "base": _bra_base, "band": {}, "tier": team_tier, "tally": {}}
                if _bra_base else None)
    _LAST_BRA_EXPORT_TALLY = _bra_ctx["tally"] if _bra_ctx is not None else {}
    # [2026-09 신설] 베테랑 은퇴 무대 경로 컨텍스트 — 모듈 상단 _VR_* 주석
    # 참고. 위 veteran_pool_tids(사우디·미국)는 이제 이 컨텍스트가 대체한다.
    _vr_groups = {g: [] for g in _VR_GROUPS}
    _vr_origin = set()
    _vr_lg_cache: dict = {}
    _vr_team_group: dict = {}
    for t in teams:
        if t["tier"] != 1:
            continue
        _cn = t["cname"]
        if _cn not in _vr_lg_cache:
            _vr_lg_cache[_cn] = get_country_league_grade(_cn)
        _lg = _vr_lg_cache[_cn]
        _eu = (t.get("continent") == "유럽")
        if _lg in ("SS", "S"):
            _vr_origin.add(t["tid"])
        elif _lg == "A" and _eu:
            _vr_origin.add(t["tid"])
            _vr_groups["A_EU"].append(t["tid"]); _vr_team_group[t["tid"]] = "A_EU"
        elif _lg == "B" and _eu:
            _vr_groups["B_EU"].append(t["tid"]); _vr_team_group[t["tid"]] = "B_EU"
        if _cn == "미국":
            _vr_groups["US"].append(t["tid"]); _vr_team_group[t["tid"]] = "US"
        elif _cn == "사우디아라비아":
            _vr_groups["SA"].append(t["tid"]); _vr_team_group[t["tid"]] = "SA"
        elif _cn == "카타르":
            _vr_groups["QA"].append(t["tid"]); _vr_team_group[t["tid"]] = "QA"
        elif _cn == "중국":
            _vr_groups["CN"].append(t["tid"]); _vr_team_group[t["tid"]] = "CN"
    _vr_peak_by_pid: dict = {}
    _vr_cnt: dict = {}
    _vr_tcountry = {t["tid"]: t["cname"] for t in teams}
    for _r in c.execute(
            "SELECT id, team_id, peak_ovr, nationality FROM ai_players "
            "WHERE age>=? AND peak_ovr>=?", (_VR_MIN_AGE, _VR_MIN_PEAK)).fetchall():
        _vr_peak_by_pid[_r["id"]] = _r["peak_ovr"]
        if (_r["peak_ovr"] >= _VR_HIGH_PEAK and _r["nationality"]
                and _r["nationality"] != _vr_tcountry.get(_r["team_id"])):
            _vr_cnt[_r["team_id"]] = _vr_cnt.get(_r["team_id"], 0) + 1

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
        "SELECT id, team_id, position, age, name, ovr, contract_end_year, last_transfer_year, "
        "salary, on_loan_from_team_id FROM ai_players").fetchall()
    team_players: dict = {}
    # [2026-08 최적화] 예전엔 행마다 `"name" in r.keys()` 식으로 컬럼 존재
    # 여부를 매번 확인했다. sqlite3.Row.keys()는 호출할 때마다 컬럼 이름
    # 리스트를 새로 만들어 돌려주는 메서드라, 26만 행 × 컬럼 4개 =
    # 108만 회나 리스트를 만들고 버리고 있었다(cProfile 실측). 한 결과셋
    # [2026-09 신설, 신민용 요청 ①②: "AI가 선수의 현재 가치와 커리어
    # 상황을 제대로 판단하고 있느냐 — OVR/나이만 볼 게 아니라 최근
    # 경기력·시즌 출전시간·팀 내 역할·임금까지 같이 봐야 한다"]
    # 여태 이 함수가 선수에 대해 아는 건 (OVR, 나이, 포지션, 계약,
    # 국적)뿐이었다 — "7~8골 넣었으니까 유지" 같은 판단은커녕 그 선수가
    # 그 시즌에 한 경기라도 뛰었는지조차 몰랐다. 그래서 39세 OVR78이
    # 한 경기도 못 뛰고도 계속 남아 있고, 반대로 리그를 씹어먹은
    # 에이스가 팀 내 OVR 1위라는 이유만으로 오히려 안 팔렸다.
    #
    # 성과 데이터는 이미 hist.ai_player_season_stats에 그 해 것까지
    # 전부 들어 있다(_snapshot_season_ratings가 이 함수보다 먼저 돈다 —
    # run_ai_offseason 호출 순서 참고). 선수 한 명씩 조회하면 26만 회
    # 왕복이라 불가능하므로, 여기서 **한 번의 쿼리**로 전부 읽어
    # 팀 단위로 정규화한 뒤 team_players 항목에 실어둔다. 아래 mover
    # 선정 루프는 dict 조회 없이 항목에서 바로 꺼내 쓴다(이 파일의
    # 기존 최적화 관례와 동일).
    #
    # 정규화를 팀 기준으로 하는 이유: 리그마다 경기 수도 평점 분포도
    # 달라서 절대값은 비교가 안 된다. "그 팀에서 가장 많이 뛴 선수 대비
    # 그 팀 평균 평점보다 얼마나 잘했나(rel_rating)", "그 팀에서 연봉이
    # 상위 몇 %인가(sal_pct)"로 잡으면 리그 수준과 무관하게 같은 의미를
    # 갖는다(세 번째 축인 팀 내 역할은 _do_one_transfer_cached가 그
    # 자리에서 쓴다 — _load_role_index 주석 참고).
    #
    # [연도 선택] 오프시즌 호출 시점에는 그 해(year) 기록이 이미 들어와
    # 있지만, 겨울 이적창구(run_ai_mid_season_transfer, 하반기 시작 주차)에서
    # 부를 때는 그 해 시즌이 아직 안 끝나서 year 행이 통째로 없다 — 그때는
    # 직전 시즌(year-1) 기록으로 판단하는 게 맞다(현실의 겨울 이적시장도
    # "지난 시즌 + 이번 시즌 전반기"를 보고 움직인다). 빈 결과일 때만
    # 한 번 더 조회하므로 오프시즌 경로에는 추가 비용이 없다.
    # [2026-09 신설] 팀 내 역할 — hist.ai_player_position_history에서
    # 읽기만 한다(_load_role_index 주석 참고). 이적 루프가 후보마다
    # dict를 다시 뒤지지 않도록 아래에서 team_players 항목에 실어둔다.
    _role_by_pid = _load_role_index(c, year)
    _perf_log(f"[PERF-ROLE] {year}년 이적시장 역할 조회 {len(_role_by_pid)}명")
    _stat_by_pid: dict = {}
    try:
        for _sr in c.execute(
                "SELECT player_id, rating FROM hist.ai_player_season_stats "
                "WHERE year=?", (year,)).fetchall():
            _stat_by_pid[_sr["player_id"]] = _sr["rating"] or 0.0
        if not _stat_by_pid:
            for _sr in c.execute(
                    "SELECT player_id, rating FROM hist.ai_player_season_stats "
                    "WHERE year=?", (year - 1,)).fetchall():
                _stat_by_pid[_sr["player_id"]] = _sr["rating"] or 0.0
    except Exception:
        # 구버전 세이브 등 표가 없으면 전부 "데이터 없음"(중립) 취급 —
        # 아래 가중치들이 전부 1.0이 되어 이번 변경 전과 똑같이 동작한다.
        _stat_by_pid = {}

    # 안에서는 컬럼 구성이 절대 바뀌지 않으므로 첫 행에서 딱 한 번만
    # 확인하고 그 결과를 재사용한다 — 판정 결과·기본값 처리는 동일.
    if all_players_rows:
        _cols = set(all_players_rows[0].keys())
        _has_name = "name" in _cols
        _has_age = "age" in _cols
        _has_cend = "contract_end_year" in _cols
        _has_lty = "last_transfer_year" in _cols
        _has_nat = "nationality" in _cols
        _has_qlc = "quota_local_country" in _cols
        _has_sal = "salary" in _cols
        _has_loan = "on_loan_from_team_id" in _cols
        _sget = _stat_by_pid.get
        _rget = _role_by_pid.get
        for r in all_players_rows:
            _age = (r["age"] if _has_age else None) or 25
            _ovr = r["ovr"]
            _st = _sget(r["id"])
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
                # [2026-09 신설] 클럽 쿼터용 자국 선수 등록 나라(database.
                # quota_local_country 컬럼 주석 참고) — 외국인 카운터와
                # 목적지 쿼터 필터가 함께 본다.
                "quota_local_country": (r["quota_local_country"] or "") if _has_qlc else "",
                # [2026-09 신설, 위 _stat_by_pid 주석 참고] 아래 팀 단위
                # 정규화 루프가 채운다 — 여기서는 원시값만 실어둔다.
                "salary": (r["salary"] or 0) if _has_sal else 0,
                "_rt": (_st if _st is not None else None),   # 그 시즌 평점
                "rel_rating": None,                  # 팀 평균 대비 평점차
                "sal_pct": None,                     # 팀 내 연봉 상위 백분위(0=최고)
                # 팀 내 역할 인덱스(0=주전 … 3=전력외, 4=유망주).
                # 기록이 없으면 None = "역할 모름"(중립).
                "role_i": _rget(r["id"]),
                # [2026-09 신설] 임대 중 여부 — _do_one_transfer_cached가 mover
                # 후보에서 뺀다(임대처는 남의 선수를 팔거나 재임대할 수 없다).
                "on_loan": (r["on_loan_from_team_id"] or 0) if _has_loan else 0,
            })
    # [2026-09 신설, 신민용 요청 ②] 팀 단위 정규화 — 두 지표를 여기서
    # 한 번만 만든다. 팀당 로스터 한 번씩만 훑으므로(전세계 26만 명 1회)
    # 이적 루프 성능에는 영향이 없다. 세 번째 축인 "팀 내 역할"은 과거
    # 기록을 읽지 않고 _do_one_transfer_cached가 그 자리에서 현재
    # 기록에서 읽어 쓴다(_load_role_index 주석 참고).
    #
    # rel_rating: 그 팀 평균 평점과의 차이. 기록이 없는 선수(갓 생성된
    #             신인, 구버전 세이브)는 None으로 남겨 "모르니까 중립"
    #             취급한다 — 0으로 두면 신인이 전부 방출 1순위가 된다.
    # sal_pct   : 팀 내 연봉 내림차순 백분위(0.0=최고연봉, 1.0=최저).
    for _tid_n, _plist_n in team_players.items():
        _rated = [p for p in _plist_n if p["_rt"] is not None]
        if _rated:
            _rmean = sum(p["_rt"] for p in _rated) / len(_rated)
            for p in _rated:
                p["rel_rating"] = p["_rt"] - _rmean
        _sals = sorted((p["salary"] for p in _plist_n), reverse=True)
        if _sals and _sals[0] > 0:
            _n_s = len(_sals)
            _rank_of = {}
            for _i_s, _v_s in enumerate(_sals):
                if _v_s not in _rank_of:
                    _rank_of[_v_s] = _i_s      # 동일 연봉은 가장 높은 순위로
            _den_s = max(1, _n_s - 1)
            for p in _plist_n:
                p["sal_pct"] = _rank_of[p["salary"]] / _den_s
    # [2026-08 2차 최적화] 팀별 "인원 가중치" 표를 미리 만들어둔다.
    # size_w = exp(-(인원 - _SQUAD_TARGET)/0.15)는 인원(정수)만의 함수라,
    # 예전처럼 후보를 평가할 때마다(시즌당 267만 회) len()으로 세고 exp를
    # 부르는 대신 여기서 팀당 한 번만 계산해두고 이적으로 인원이 실제로
    # 바뀔 때만(이적 1건당 2팀) 갱신하면 된다. 값 자체는 예전 식 그대로다.
    _sw_by_tid = {tid: _size_weight(len(plist)) for tid, plist in team_players.items()}
    # [2026-09 신설, 외국인 쿼터 예방] team_players 각 선수 dict엔 이미
    # nationality가 실려 있으므로(위 로딩 블록 참고) 추가 조회 없이
    # 팀별 "현재 외국인 수"를 한 번만 센다. 이적이 실제로 일어날 때마다
    # (아래 old_tid/new_tid 처리부) 살아있는 값으로 증감시켜, 매 후보
    # 평가마다 다시 세지 않고도 항상 최신 값을 참조한다.
    foreign_count_by_tid = {}
    for tid, plist in team_players.items():
        _cn = dst_country_by_tid.get(tid)
        # [2026-09 수정, 신민용 리포트: "외국인이 팀에 10명 넘게 있을
        # 때도 있다"] 예전엔 is_quota_foreign(자국 등록 전환자를 제외)로
        # 셌다 — 전환이 쌓인 팀일수록 외국인 칸이 비어 보여서 새 외국인을
        # 또 받는 되먹임이 생겼다(database.FOREIGN_NATURALIZE_MAX_PER_TEAM
        # 정의부의 실측 참고). 로스터 인원 제한은 화면에 보이는 국적
        # 그대로 세는 게 맞으므로 is_roster_foreign으로 바꾼다.
        foreign_count_by_tid[tid] = sum(
            1 for p in plist if is_roster_foreign(p.get("nationality"), _cn))
    # [2026-09 성능실험, cProfile 실측: dict.get 984만 회 중 foreign_count_by_tid.
    # get(t, 0)이 단독 최대 기여자(샘플 추정 약 164만 회)] 위 루프는 team_players에
    # 선수가 있는 팀만 채운다 — 선수단이 텅 빈 팀(드묾)은 여기 없어서, 아래
    # _do_one_transfer_cached의 목적지 후보 루프가 매 후보마다 어쩔 수 없이
    # .get(t, 0)을 불러야 했다. 여기서 teams의 모든 팀ID에 대해 딱 한 번만
    # 기본값 0을 채워두면(이미 있는 값은 안 건드림) 그 이후엔 항상 키가
    # 존재하므로 직접 인덱싱 foreign_count_by_tid[t]로 바꿀 수 있다 — 값은
    # .get(t, 0)이 주던 것과 완전히 동일(선수 없는 팀=외국인 0명), _sw_by_tid도
    # 이미 같은 이유로 동일 패턴(빈 팀은 0으로 채움)을 쓰고 있다(위 pool_cache
    # 빌드부의 "if _t not in sw_by_tid" 참고) — 이번 실험이 그 패턴을 그대로
    # 따르는 것뿐이다. 이적이 성사될 때마다의 증감(old_tid/new_tid 처리부)은
    # 이미 대상 팀이 team_players에 있던 팀이라 키가 항상 있었으므로 안 건드림.
    for _t in teams:
        if _t["tid"] not in foreign_count_by_tid:
            foreign_count_by_tid[_t["tid"]] = 0
    # [2026-09 신설, 상류②] 팀별 세부 포지션 인원표 — 목적지 수요
    # 가중치(position_demand_weight)가 후보 평가마다 참조한다. 위
    # foreign_count_by_tid와 완전히 같은 패턴이다: 여기서 한 번만 세고,
    # 선수가 없는 팀까지 0으로 채워두고(후보 루프에서 .get 대신 직접
    # 인덱싱), 이적이 실제로 성사될 때마다 그 두 팀만 증감시킨다.
    pos_count_by_tid = {}
    for tid, plist in team_players.items():
        _pcl = [0] * _N_SLOT_POS
        for p in plist:
            _pi = _SLOT_POS_IDX.get(p["position"], -1)
            if _pi >= 0:
                _pcl[_pi] += 1
        pos_count_by_tid[tid] = _pcl
    for _t in teams:
        if _t["tid"] not in pos_count_by_tid:
            pos_count_by_tid[_t["tid"]] = [0] * _N_SLOT_POS
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
    # [2026-09 신설, 신민용 확정: "FIFA 규정 — 동일 클럽 간 임대는 최대 2년,
    # 한 시즌에 한 구단이 보낼 수 있는 임대 선수 수에도 엄격한 제한"]
    # (1) 시즌별 인원 상한: 같은 "시즌"(= 발효 시즌 — 오프시즌 창은 year+1,
    # 겨울 창은 year, 계약 발효 규칙과 같은 기준)에 이미 성사된 새 임대를
    # 먼저 세고(오프시즌 창에서 쓴 몫이 그 시즌 겨울 창까지 이어진다), 이번
    # 호출에서 성사되는 임대도 같이 센다. "임대 연장"은 새 거래가 아니라 제외.
    # (2) 같은 선수·같은 두 구단 누적 연수: 원 소속팀이 복귀한 선수를 같은
    # 임대처로 또 보내 상한을 우회하지 못하게, 남은 연수만큼만 임대한다.
    # 둘 중 하나라도 걸리면 거래는 임대 대신 완전 이적으로 성사된다.
    from constants import (AI_LOAN_MAX_OUT_PER_SEASON, AI_LOAN_MAX_IN_PER_SEASON,
                           AI_LOAN_MAX_PER_CLUB_PAIR, AI_LOAN_MAX_TOTAL_YEARS)
    _loan_season = year if is_mid_season else year + 1
    _loans_out, _loans_in, _loans_pair = {}, {}, {}
    for _lr in c.execute(
            "SELECT from_team_id, to_team_id FROM ai_transfer_log "
            "WHERE is_loan=1 AND transfer_type != '임대 연장' AND "
            "((is_mid_season=1 AND year=?) OR (is_mid_season=0 AND year=?))",
            (_loan_season, _loan_season - 1)).fetchall():
        _loans_out[_lr[0]] = _loans_out.get(_lr[0], 0) + 1
        _loans_in[_lr[1]] = _loans_in.get(_lr[1], 0) + 1
        _loans_pair[(_lr[0], _lr[1])] = _loans_pair.get((_lr[0], _lr[1]), 0) + 1
    _loan_yrs_all = _loan_years_by_pair(c)
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
    # [2026-09 신설, 리포트 14번] 노장 하향 이동 후보군 캐시 — 등급 rank
    # 하나당 한 번만 조립한다(_intl_pool_by_rank와 완전히 같은 패턴,
    # 같은 리스트 객체를 계속 넘기므로 _pool_meta_cache도 그대로 적중).
    _decline_pool_by_rank: dict = {}
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
                if src_tier != 1:
                    # [2026-09 신설, 신민용 리포트 40번: "벨기에 3부 24세가
                    # 국대+발롱도르 후보인데 계속 3부에서 뛴다"] 여태
                    # 이 조건은 `roll < _same_league_upper or src_tier != 1`
                    # 이었다 — 즉 **1부가 아닌 팀의 선수는 100% 같은 리그
                    # 안에서만** 움직였다. 위로도 아래로도, 국내에도 해외에도
                    # 나갈 길이 구조적으로 아예 없었던 것이다(실측: 5시즌째
                    # "tier2 이하인데 자국 1부 수준 OVR"인 선수가 8,126명까지
                    # 누적, 그중 2,022명은 3시즌 넘게 그 상태 그대로).
                    #
                    # 이제 하위 tier 팀도 국내 상/하위 tier 후보군을 갖는다.
                    # 상향 쪽에 별도의 실력 필터를 두지 않아도 되는 이유:
                    # 목적지 가중치가 이미 가우시안 gap(선수 OVR과 목적지
                    # 팀 평균의 차이)을 보므로, 3부 평균급 선수가 2부로
                    # 올라가는 건 자연히 희박하고 3부를 씹어먹는 선수만
                    # 실제로 뽑힌다 — 기존 로직이 그대로 필터 역할을 한다.
                    # (한 계단씩만 움직이는 것으로는 느리므로, 리그 수준을
                    #  크게 뛰어넘은 선수 전용 통로는 _upward_transfer_pull
                    #  에 따로 만들었다. 이 분기는 "평범한 커리어 상승/하강"
                    #  담당이다.)
                    cid = team_to_cid.get(src)
                    if roll < _LOWER_TIER_SAME_UPPER:
                        dst_pool_tids = tids
                    elif roll < _LOWER_TIER_UP_UPPER:
                        cand = by_country_tier.get((cid, src_tier - 1), [])
                        dst_pool_tids = cand if cand else tids
                    else:
                        cand = by_country_tier.get((cid, src_tier + 1), [])
                        dst_pool_tids = cand if cand else tids
                elif roll < _same_league_upper:
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
                # [2026-09] 기존 "S/SS 출발 30세+ → 60% 사우디·미국" 전환은
                # 베테랑 경로(_vet_ctx)가 대체한다 — 출발지에 A급 유럽 1부
                # 추가, 대상·목적지·상한 특례는 _pick_vet_route 참고.
                _veteran_pool = None
                _vet_ctx_here = (_vet_ctx
                                 if (roll >= _domestic_other_upper and src_tier == 1
                                     and src in _vet_origin_tids)
                                 else None)
                # [2026-09 신설, 신민용 리포트 14번: "S리그 1부가 39세
                # OVR78을 계속 보유"] 기존 나이 페널티(_age_penalty)는
                # "목적지가 SS/S면 감쇠"인데, 같은 리그 안에서만 도는
                # 87% 경로에서는 **모든 후보가 똑같이 감쇠돼 상대 가중치가
                # 전혀 안 바뀐다** — 즉 노장이 같은 등급 리그 안에서 팀만
                # 바꿔가며 영원히 남을 수 있었다. 34세 이상이면서 거의
                # 못 뛴 선수에게는 "한 단계 아래 무대" 후보군을 따로
                # 넘겨서, 실제로 내려갈 길을 만들어 준다(전환 여부는
                # mover가 정해진 뒤에야 나이·출전을 알 수 있으므로
                # _do_one_transfer_cached 안에서 판단한다 — veteran_pool과
                # 완전히 같은 패턴).
                _rank_src = team_grade_rank.get(src)
                _decline_pool = None
                if src_tier == 1 and _rank_src is not None:
                    _decline_pool = _decline_pool_by_rank.get(_rank_src)
                    if _decline_pool is None:
                        _dp = []
                        for _r in (_rank_src - 3, _rank_src - 2, _rank_src - 1):
                            _dp.extend(tier1_by_grade.get(_r, []))
                        _decline_pool_by_rank[_rank_src] = _dp
                        _decline_pool = _dp
                    if len(_decline_pool) < 2:
                        _decline_pool = None
                else:
                    # [2026-09 신설] 예전엔 1부 팀에만 하향 풀을 만들어서,
                    # 2부 이하 팀의 노쇠/미달 선수는 내려갈 곳 자체가 없었다
                    # (같은 리그 87% + 상향 6% + 하향 6%의 무작위 주사위뿐).
                    # 같은 나라 한 단계 아래 부수를 하향 풀로 쓴다.
                    _cid_src = team_to_cid.get(src)
                    _dp2 = by_country_tier.get((_cid_src, src_tier + 1))
                    if _dp2 and len(_dp2) >= 2:
                        _decline_pool = _dp2

                # [2026-09 신설] 이 팀이 속한 리그의 기준선(그 리그에서 새로
                # 태어날 수 있는 가장 약한 선수). mover가 정해진 뒤 미달 폭을
                # 재는 데 쓴다 — _league_ovr_floor_ref 정의부 주석 참고.
                _src_cn = dst_country_by_tid.get(src)
                _src_gr = dst_grade_by_tid.get(src)
                _src_floor_ref = (_league_ovr_floor_ref(_src_gr, src_tier, _src_cn)
                                   if _src_cn and _src_gr else None)

                result = _do_one_transfer_cached(
                    src, dst_pool_tids, team_players, team_avg, year,
                    protect_strength=_STAR_PROTECT_BY_CATEGORY[cat],
                    veteran_pool_tids=_veteran_pool,
                    vet_route=_vet_ctx_here,
                    decline_pool_tids=_decline_pool,
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
                    # [2026-09 신설] 목적지 리그 설계 OVR 하한 체크용 —
                    # 위 dst_ovr_floor_by_tid 주석 참고(상한과 같은 방식으로
                    # 모든 분기에 동일하게 전달).
                    dst_ovr_floor_by_tid=dst_ovr_floor_by_tid,
                    # [2026-09 신설] 조국 귀환 가산 가중치용 — 위
                    # dst_country_by_tid 주석 참고. 국내 이적 분기(같은
                    # 나라만 후보)에서는 모든 후보가 동일하게 "일치"라
                    # 상대 가중치에 영향이 없으므로 분기 구분 없이 항상
                    # 넘겨도 안전하다.
                    dst_country_by_tid=dst_country_by_tid,
                    # [2026-09 신설] 외국인 쿼터 예방 — 위 dst_quota_hi_by_tid/
                    # foreign_count_by_tid 주석 참고. 국내 이적 분기도 마찬가지로
                    # 항상 넘긴다(같은 나라끼리는 애초에 외국인 판정 자체가 안 걸림).
                    dst_quota_hi_by_tid=dst_quota_hi_by_tid,
                    foreign_count_by_tid=foreign_count_by_tid,
                    # [2026-09 신설, 상류②] 목적지 포지션 수요 가중치용 —
                    # 위 pos_count_by_tid 주석 참고.
                    pos_count_by_tid=pos_count_by_tid,
                    # [2026-09 신설] 리그 기준선 미달 판정용 — 위
                    # _src_floor_ref 주석 참고.
                    src_floor_ref=_src_floor_ref,
                    # [2026-10 신설] 브라질 수출 경로 — 브라질 클럽에서
                    # 출발할 때만 넘긴다(그 외 팀은 None이라 기존과 동일).
                    bra_export=(_bra_ctx if dst_country_by_tid.get(src) == "브라질"
                                else None))
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
                        # [2026-09 신설, 신민용 요청: "35세 이상은 계약
                        # 기간이 짧아져야 한다"] 나이/OVR 기반 계약기간
                        # 상한 계산에 mover 정보(_old_list에서 pop하기
                        # 전 나이/OVR)가 필요해서, 원래 pop 직전에 하던
                        # 조회(_old_list/_idx)를 여기로 끌어올렸다 — 아래
                        # 기존 pop 로직은 이 _old_list/_idx를 그대로
                        # 재사용하고 중복 조회하지 않는다.
                        _old_list = team_players.get(old_tid, [])
                        _idx = next((i for i, e in enumerate(_old_list) if e["id"] == pid), None)
                        _mover_for_cend = _old_list[_idx] if _idx is not None else None
                        _cend_lo, _cend_hi = _ai_contract_duration_range(
                            (_mover_for_cend.get("age") if _mover_for_cend else None) or 25,
                            _mover_for_cend.get("ovr") if _mover_for_cend else None,
                            dst_ovr_ceiling_by_tid.get(new_tid) if dst_ovr_ceiling_by_tid is not None else None,
                            default=(2, 4))
                        # [2026-09 버그수정, 신민용 확정: "2026 입단 2년 = 2026·2027만
                        # 뛰고 2028부터 재계약/이적"] 오프시즌(year) 체결 N년 계약의
                        # 만료연도는 year+N(마지막 시즌 = year+N, 그 오프시즌에
                        # 재계약 판정) — 예전 year+1+N은 한 시즌을 더 뛰게 했다.
                        # 겨울 이적도 year+N(=N년 6개월, 기존과 동일).
                        new_contract_end = year + random.randint(_cend_lo, _cend_hi)
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
                                [(e["id"], e.get("position"), e.get("ovr"), e.get("age"))
                                 for e in _old_list]
                            ).get(pid, "")
                        p_entry = _old_list.pop(_idx) if _idx is not None else None
                        if p_entry is not None:
                            # 인원이 바뀐 팀만 가중치 표를 갱신(위 _sw_by_tid 주석 참고)
                            _sw_by_tid[old_tid] = _size_weight(len(_old_list))
                            # [2026-09 신설, 상류②] 포지션 인원표도 같이 감소.
                            _pi_out = _SLOT_POS_IDX.get(p_entry["position"], -1)
                            if _pi_out >= 0:
                                pos_count_by_tid[old_tid][_pi_out] -= 1
                            # [2026-09 신설, 외국인 쿼터 예방] 나가는 선수가
                            # 원 소속팀 기준 외국인이었으면 그 팀 카운터를 뺀다.
                            if is_roster_foreign(p_entry.get("nationality"),
                                                 dst_country_by_tid.get(old_tid)):
                                foreign_count_by_tid[old_tid] = foreign_count_by_tid.get(old_tid, 0) - 1
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
                            # [2026-09 신설] 임대 규정(위 _loans_*/_loan_yrs_all 주석).
                            _loan_left = AI_LOAN_MAX_TOTAL_YEARS - _loan_yrs_all.get((pid, old_tid, new_tid), 0)
                            if _is_loan and (
                                    _loan_left <= 0
                                    or _loans_out.get(old_tid, 0) >= AI_LOAN_MAX_OUT_PER_SEASON
                                    or _loans_in.get(new_tid, 0) >= AI_LOAN_MAX_IN_PER_SEASON
                                    or _loans_pair.get((old_tid, new_tid), 0) >= AI_LOAN_MAX_PER_CLUB_PAIR):
                                _is_loan = False
                            _loan_return2 = (year + min(random.randint(*AI_LOAN_DURATION_YEARS), _loan_left)
                                             if _is_loan else 0)
                            if _is_loan:
                                _loans_out[old_tid] = _loans_out.get(old_tid, 0) + 1
                                _loans_in[new_tid] = _loans_in.get(new_tid, 0) + 1
                                _loans_pair[(old_tid, new_tid)] = _loans_pair.get((old_tid, new_tid), 0) + 1
                                _loan_yrs_all[(pid, old_tid, new_tid)] = (
                                    _loan_yrs_all.get((pid, old_tid, new_tid), 0) + (_loan_return2 - year))
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
                            # [2026-09 신설, 상류②] 포지션 인원표도 같이 증가.
                            _pi_in = _SLOT_POS_IDX.get(p_entry["position"], -1)
                            if _pi_in >= 0:
                                pos_count_by_tid[new_tid][_pi_in] += 1
                            # [2026-09 신설, 외국인 쿼터 예방] 들어오는 선수가
                            # 새 소속팀 기준 외국인이면 그 팀 카운터를 올린다 —
                            # 위 예방 필터가 이미 상한 도달 팀은 후보에서
                            # 뺐지만, 스왑 딜(같은 건에서 두 선수가 동시에
                            # 오가는 경우)처럼 같은 이적 건 안에서 두 번째
                            # 선수가 반영될 때를 위해 항상 실측값으로 갱신한다.
                            if is_roster_foreign(p_entry.get("nationality"),
                                                 dst_country_by_tid.get(new_tid)):
                                foreign_count_by_tid[new_tid] = foreign_count_by_tid.get(new_tid, 0) + 1
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


# ─────────────────────────────────────────────
# 4.4. 상위리그 발탁 (2026-09 신설)
# ─────────────────────────────────────────────
# [신민용 리포트 40번: "벨기에 3부 24세가 국대에 뽑히고 발롱도르 후보까지
# 갔는데 계속 3부에서 뛰다가 30세에 포르투갈 1부 한 번 가고 은퇴한다"]
# 신민용 본인의 진단이 정확했다 — 이건 "3부 선수가 국대에 뽑힌 게
# 이상하다"는 문제가 아니라 **그 선수를 원하는 상위 리그 수요가 시스템에
# 아예 없다**는 문제다. 리포트 1번("이집트 OVR86 → … → 계속 하위리그")도
# 같은 계열이다.
#
# 기존 통로들이 왜 이 선수를 못 끌어올렸는지:
#   · _transfer_market — 2026-09 이번 수정 전까지 하위 tier 선수의 목적지는
#     100% 같은 리그였다. 수정 후에도 "한 계단"씩만 움직이므로 3부 →
#     1부는 최소 두 시즌이고 매 시즌 6% 확률이라 사실상 안 일어난다.
#   · _prestige_scouting — 명문팀(prestige_clubs 등록팀)만, 그것도 팀당
#     연 1~3회 시도에 "그 포지션 최약체보다 MIN_GAP 이상 좋은 후보"라는
#     제약이 있다. 명문이 아닌 상위 리그 팀들은 리그 밖을 아예 안 본다.
#   · _prestige_potential_scouting — potential_ovr 기반이라 이미 완성된
#     (현재 OVR이 높은) 선수는 대상이 아니다.
#
# 그래서 "지금 소속 리그의 설계 수준을 명백히 뛰어넘은 선수"만 따로
# 골라, 그 실력을 실제로 소화할 수 있는 상위 무대로 보내는 전용 통로를
# 만든다. 명문 여부를 안 보는 게 이 통로의 핵심이다 — 현실에서도
# 하위리그 특급 유망주를 데려가는 건 빅클럽만이 아니다.
#
# 이 통로는 일방 이적이다(맞교환 없음). 하위팀 스쿼드가 한 명 비는 건
# 같은 오프시즌 뒤쪽에서 도는 _rebalance_squad_sizes가 유망주로 채운다
# — "리그 최고 선수가 상위 무대로 팔려가고 그 자리에 유망주가 온다"는
# 자연스러운 서사가 그대로 나온다(호출 순서는 run_ai_offseason 참고).

_UPWARD_MAX_AGE = 32          # 이 나이를 넘으면 상승 커리어 대상이 아니다
_UPWARD_MIN_BREAKOUT = 2      # 소속팀 나라·부수 설계 OVR 상한 초과분 최소치
# [2026-09 튜닝, 헤드리스 계측으로 확정] 처음엔 "후보의 30%, 시즌당
# 최대 900명"이라는 단순 상한을 썼는데, 4시즌차에 후보가 3,025명까지
# 늘면서 상한에 그대로 걸렸다([PERF-UPWARD] 로그로 확인) — 리그 수준을
# 크게 뛰어넘은 선수가 "대기줄 뒤"로 밀려 2~3시즌씩 하위 리그에 남는,
# 고치려던 증상이 완화된 형태로 그대로 재현됐다.
#
# 그래서 두 갈래로 나눈다: 초과분이 확실히 큰 선수(_UPWARD_ALWAYS_
# BREAKOUT 이상 — "누가 봐도 이 리그 수준이 아닌" 선수)는 대기줄과
# 무관하게 전원 시도하고, 애매한 초과(2~7)만 비율로 천천히 소화한다.
# 전체 상한은 세계가 한꺼번에 뒤집히지 않게 남겨두되, 일반 이적시장이
# 이미 시즌당 7만 건(선수의 28%)을 돌리는 것에 비하면 아주 작은 규모다.
_UPWARD_MOVES_FRAC = 0.30     # 애매한 초과 구간에서 시즌당 소화할 비율
_UPWARD_ALWAYS_BREAKOUT = 8   # 이 초과분 이상이면 비율과 무관하게 전원 시도
_UPWARD_MOVES_CAP = 4000      # 시즌당 전세계 상한(안전장치)
_UPWARD_GAP_DENOM = 260.0     # 목적지 가우시안 폭(이적시장 기본 170보다 관대)
_UPWARD_GAP_CUTOFF = 45       # 이 이상 벌어진 팀은 가중치가 4e-4 이하 — 후보에서 제외
_UPWARD_MIN_DST_TIER = 2      # 목적지는 1~2부만(상승 이적의 정의상)
# [2026-10 신설, 신민용 확정] "상위 무대 수준" 발탁 문턱. 예전엔 발탁 후보가
# "소속 리그 설계 상한 + _UPWARD_MIN_BREAKOUT 이상"뿐이라, 상한이 높은 A급 1부
# (네덜란드 94·포르투갈 96 등)의 90~95 선수는 "리그 수준 안"으로 판정돼
# 구조적으로 못 올라갔다(원본 10시즌: A급 1부 90+·29세 이하의 빅클럽 이동
# 시즌당 약 6%, 빅클럽 외부 외국인 영입 OVR 중앙값 84 vs 같은 창 외부 최고
# 후보 95). 이제 OVR이 이 값 이상이면 리그 상한과 무관하게 후보로 인정한다.
# 단 이미 빅클럽(SS·S 1부)에 있는 선수는 예전 조건 그대로 — 빅클럽끼리의
# 이동(특히 EPL 쏠림)을 새로 만들지 않기 위해서. 점수·목적지("더 높은 무대",
# 목적지 상한 ≥ OVR)·쿼터·시즌 규모(30% 비율·상한)는 그대로다.
_UPWARD_STAGE_OVR = 92
_UPWARD_STAGE_EXCLUDE_GRADES = ("SS", "S")   # 이 등급 1부 소속은 새 문턱 미적용


def _upward_transfer_pull(c, year):
    """리그 수준을 뛰어넘은 선수를 상위 무대가 데려간다. 반환: 성사 건수.

    후보 선정 = "소속팀 설계 OVR 상한을 얼마나 넘었는가(breakout)"를
    축으로 하고, 신민용이 ①에서 요구한 나머지 축(최근 경기력·시즌
    출전시간·나이·포지션)을 점수에 같이 반영한다. 목적지 선정은
    이적시장과 같은 원리(가우시안 OVR 적합도 × 스쿼드 여유)에
    "지금보다 확실히 높은 무대"라는 조건만 추가한 것이다.
    """
    from constants import get_country_league_grade, get_ovr_range
    from economy import LEAGUE_GRADE_RANK, estimate_transfer_fee
    from database import is_roster_foreign

    team_rows = c.execute(
        """SELECT t.id AS tid, t.current_tier AS tier, t.name AS tname,
                  cn.name AS cname, cn.continent AS continent,
                  (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) AS avg_ovr,
                  (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) AS n_squad
           FROM teams t JOIN leagues l ON t.league_id=l.id
           JOIN countries cn ON l.country_id=cn.id
           ORDER BY t.id""").fetchall()
    team_rows = _drop_mil_teams(c, team_rows, "tid")   # [2026-10] 군팀 제외
    if not team_rows:
        return 0

    _grade_cache: dict = {}
    meta: dict = {}
    for r in team_rows:
        _cn = r["cname"]
        _g = _grade_cache.get(_cn)
        if _g is None:
            _g = get_country_league_grade(_cn)
            _grade_cache[_cn] = _g
        _tier = r["tier"] or 1
        _rng = get_ovr_range(_g, _tier, _cn)
        _rk = LEAGUE_GRADE_RANK.get(_g, 4)
        meta[r["tid"]] = {
            "tier": _tier, "cname": _cn, "tname": r["tname"], "grade": _g,
            "rank": _rk,
            "ceil": (_rng[1] if _rng else 43),
            # [2026-09 신설] 그 리그 설계 OVR 하한 — 아래 후보 루프에서
            # "하한에 크게 못 미치는 선수" 유입을 막는다(_DST_FLOOR_* 참고).
            "floor": (_rng[0] if _rng else 30),
            "avg": (r["avg_ovr"] or 50.0),
            "n": (r["n_squad"] or 0),
            "continent": r["continent"] or "유럽",
            # "무대 높이"를 정수 하나로 — 아래 후보 루프가 수백만 번
            # 도는 자리라 (rank, -tier) 튜플을 매번 만들면 그 비용만으로
            # 초 단위가 나온다. tier는 1~12 수준이라 100 단위로 충분히
            # 분리되고, 대소 관계는 튜플 비교와 완전히 동일하다.
            "lvl": _rk * 100 - _tier,
        }

    # ── 목적지 후보 색인 ────────────────────────────────────────
    # "설계 상한 오름차순"으로 한 줄로 세워두면, OVR X인 선수를 소화할 수
    # 있는 팀(상한 >= X)은 항상 이 리스트의 접미부(suffix)가 된다 —
    # bisect 한 번으로 후보군이 나온다. 1~2부만 담는다(상승 이적의 정의).
    _dst = sorted(((m["ceil"], tid, m["floor"]) for tid, m in meta.items()
                   if m["tier"] <= _UPWARD_MIN_DST_TIER),
                  key=lambda t: (t[0], t[1]))
    _dst_ceils = [t[0] for t in _dst]
    _dst_tids = [t[1] for t in _dst]
    # [2026-09 신설, 신민용 리포트: "OVR63인 선수가 토트넘에 왜 들어와"]
    # 이 통로(상위리그 발탁)는 설계 **상한**(_dst_ceils)만 보고 하한은 전혀
    # 안 봤다 — 실측에서 이 경로가 "부족분 15 이상" 이적의 주범이었다
    # (3,956건 중 889건 = 22.5%, 전체 이상 이적의 89%). 일반 이적시장에
    # 넣은 것과 같은 기준(_DST_FLOOR_HARD_EXCLUDE)을 여기도 적용한다.
    _dst_floors = [t[2] for t in _dst]
    if not _dst_tids:
        return 0
    # [성능] 아래 후보 루프는 (후보 선수 수천 × 목적지 후보 수천) 규모라
    # 이 파일의 이적시장 루프 다음으로 뜨거운 자리가 된다. 팀 메타를
    # 후보마다 dict로 다시 꺼내지 않도록 배열로 펼쳐둔다 — 값은 위
    # meta와 완전히 같고, 루프 중 바뀌는 것은 스쿼드 인원(_dst_n)뿐이라
    # 그것만 meta와 함께 갱신한다(둘 다 같은 값을 유지).
    _dst_lvl = [meta[t]["lvl"] for t in _dst_tids]
    _dst_avg = [meta[t]["avg"] for t in _dst_tids]
    _dst_n = [meta[t]["n"] for t in _dst_tids]
    _dst_idx = {t: i for i, t in enumerate(_dst_tids)}
    _n_dst = len(_dst_tids)

    # ── 후보 선수 ──────────────────────────────────────────────
    # 최근 경기력(평점)과 팀 내 역할 — 둘 다 이미 저장된 표에서 읽기만
    # 한다. 출전 경기수는 이 엔진에서 전원 사실상 동일하므로 쓰지 않는다
    # (_load_role_index 주석 참고).
    _stat: dict = {}
    try:
        for _sr in c.execute(
                "SELECT player_id, rating FROM hist.ai_player_season_stats "
                "WHERE year=?", (year,)).fetchall():
            _stat[_sr["player_id"]] = _sr["rating"] or 0.0
    except Exception:
        _stat = {}
    _role_up = _load_role_index(c, year)

    rows = c.execute(
        "SELECT id, team_id, position, age, ovr, name, nationality, quota_local_country, "
        "last_transfer_year, on_loan_from_team_id FROM ai_players ORDER BY id").fetchall()

    # 팀별 포지션 인원 — "빼면 그 포지션이 0명이 되는 선수"는 안 건드린다
    # (이 파일이 이적시장에서 이미 지키는 불변식과 같은 원칙).
    pos_n: dict = {}
    for r in rows:
        _k = (r["team_id"], r["position"])
        pos_n[_k] = pos_n.get(_k, 0) + 1

    cands = []
    _n_stage_cand = 0   # [2026-10] 상위 무대 문턱으로 새로 후보가 된 인원(계측)
    for r in rows:
        m = meta.get(r["team_id"])
        if m is None:
            continue
        _age = r["age"] or 25
        if _age > _UPWARD_MAX_AGE:
            continue
        if r["on_loan_from_team_id"]:
            continue        # 임대 중인 선수는 원 소속팀 소관
        if (year - (r["last_transfer_year"] or 0)) < 1:
            continue        # 방금 이적한 선수는 최소 한 시즌 유지(이적시장과 동일)
        _ovr = r["ovr"] or 0
        breakout = _ovr - m["ceil"]
        _brk_eff = breakout
        if breakout < _UPWARD_MIN_BREAKOUT:
            # [2026-10] 상위 무대 문턱 — 위 _UPWARD_STAGE_OVR 정의부 참고.
            if (_ovr < _UPWARD_STAGE_OVR
                    or (m["tier"] == 1 and m["grade"] in _UPWARD_STAGE_EXCLUDE_GRADES)):
                continue
            # 정렬 점수의 기준값: 문턱(92)에 막 닿은 선수를 "상한을 막 넘은
            # 선수(초과분 _UPWARD_MIN_BREAKOUT)"와 같은 출발점으로 둔다 — 실제
            # 초과분(음수)을 그대로 쓰면 30% 비율 컷에서 전부 잘려 문턱을 연
            # 의미가 없어진다. "무조건 시도"(_UPWARD_ALWAYS_BREAKOUT) 판정은
            # 실제 초과분 그대로라 예전과 같다.
            _brk_eff = _UPWARD_MIN_BREAKOUT + (_ovr - _UPWARD_STAGE_OVR)
            _n_stage_cand += 1
        if pos_n.get((r["team_id"], r["position"]), 0) < 2:
            continue
        # 점수: 초과분이 주축이고, 거기에 ①이 요구한 나머지 축을 얹는다.
        #  · 출전시간/경기력 — 실제로 뛰면서 증명한 선수를 먼저 데려간다.
        #    기록이 아예 없으면 중립(0) — 갓 생성된 신인을 벌주지 않는다.
        #  · 나이 — 같은 실력이면 어릴수록 상위 무대가 더 원한다.
        score = float(_brk_eff)
        _rt = _stat.get(r["id"])
        if _rt:
            score += max(-4.0, min(6.0, (_rt - 6.5) * 4.0))
        # 팀 내 역할 — 하위 리그에서 주전으로 증명한 선수를 먼저 데려간다.
        # (0=주전 … 3=전력외, 4=유망주). 기록이 없으면 가산 없음(중립).
        _ru = _role_up.get(r["id"])
        if _ru is not None:
            score += (4.0, 3.0, 1.0, -1.0, -3.0, 1.5)[_ru]
        score += max(0.0, (26 - _age) * 0.45)          # 18세 +3.6 / 26세 이상 0
        cands.append((score, r["id"], r, breakout))

    if not cands:
        return 0
    cands.sort(key=lambda t: (-t[0], t[1]))
    _n_cand_all = len(cands)
    # 점수는 초과분이 지배하므로 정렬 후 "확실히 큰 초과"는 앞쪽에 몰려
    # 있지만, 출전/평점 보너스(최대 +12) 때문에 순서가 완전히 일치하지는
    # 않는다 — 개수는 직접 센다.
    _n_always = sum(1 for t in cands if t[3] >= _UPWARD_ALWAYS_BREAKOUT)
    n_try = min(_UPWARD_MOVES_CAP,
                max(_n_always, int(len(cands) * _UPWARD_MOVES_FRAC) + 1))
    cands = cands[:n_try]
    # [진단] 왜 성사가 안 됐는지 구분해 세어둔다 — 상한(_UPWARD_MOVES_CAP)에
    # 걸린 건지, 목적지가 없어서인지를 로그만 보고 구분할 수 있어야
    # 튜닝할 수 있다(이 파일의 다른 [PERF-*] 계측들과 같은 목적).
    _fail_no_ceiling = 0     # 그 OVR을 소화할 팀이 세계에 없음
    _fail_no_dst = 0         # 상한은 되는데 "더 높은 무대" 조건/쿼터에서 전멸

    # ── 외국인 쿼터 게이트(이미 있는 공용 헬퍼 재사용) ─────────
    _fq_now, _fq_can_take, _fq_note = _build_foreign_quota_gate(c)

    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 0

    import time as _time_up
    _up_t0 = _time_up.perf_counter()
    updates = []
    log_rows = []
    _taken_dst: dict = {}   # tid -> 이번 시즌 이 통로로 받은 인원(한 팀에 몰리지 않게)
    n_moved = 0
    _exp = math.exp
    _bisect_left = bisect.bisect_left

    _n_stage_moved = 0
    for score, pid, r, _brk in cands:
        src_tid = r["team_id"]
        sm = meta[src_tid]
        _ovr = r["ovr"] or 0
        _nat = r["nationality"] or ""
        # 이 선수를 소화할 수 있는 팀 = 설계 상한이 선수 OVR 이상.
        i0 = _bisect_left(_dst_ceils, _ovr)
        if i0 >= _n_dst:
            _fail_no_ceiling += 1
            continue
        _src_lvl = sm["lvl"]
        # 가중치가 사실상 0인 후보는 애초에 안 담는다 — exp(-(gap²)/260)은
        # 격차 45에서 이미 4e-4, 60이면 1e-6이라 뽑힐 일이 없는데 리스트에만
        # 쌓여 random.choices의 누적합을 무겁게 만든다. 상한을 정수 비교로
        # 미리 걸러 exp 호출 자체를 건너뛴다(뽑히는 결과에는 영향 없음).
        _lo_avg = _ovr - _UPWARD_GAP_CUTOFF
        _hi_avg = _ovr + _UPWARD_GAP_CUTOFF
        pool = []
        weights = []
        _wsum = 0.0
        for _i in range(i0, _n_dst):
            # "지금보다 확실히 높은 무대"만 — 같은 등급·같은 부수는 일반
            # 이적시장 소관이다(이 통로는 커리어 상승 전용).
            if _dst_lvl[_i] <= _src_lvl:
                continue
            _avg = _dst_avg[_i]
            if _avg < _lo_avg or _avg > _hi_avg:
                continue
            if (_dst_floors[_i] - _ovr) > _DST_FLOOR_HARD_EXCLUDE:
                continue        # 그 리그 설계 하한에 크게 못 미침(위 _dst_floors 주석)
            t = _dst_tids[_i]
            if t == src_tid:
                continue
            if _taken_dst.get(t, 0) >= 2:
                continue        # 한 팀이 이 통로로 한 시즌에 3명 이상은 안 받는다
            if _nat and not _fq_can_take(t, _nat):
                continue
            gap = _avg - _ovr
            w = _exp(-(gap * gap) / _UPWARD_GAP_DENOM) * _size_weight(_dst_n[_i])
            if w <= 0.0:
                continue
            pool.append(t)
            weights.append(w)
            _wsum += w
        if not pool or _wsum <= 0.0:
            _fail_no_dst += 1
            continue
        dst_tid = random.choices(pool, weights=weights, k=1)[0]
        dm = meta[dst_tid]

        _fq_note(dst_tid, _nat)
        _taken_dst[dst_tid] = _taken_dst.get(dst_tid, 0) + 1
        dm["n"] += 1
        _dst_n[_dst_idx[dst_tid]] = dm["n"]
        sm["n"] = max(0, sm["n"] - 1)
        _si = _dst_idx.get(src_tid)
        if _si is not None:
            _dst_n[_si] = sm["n"]
        pos_n[(src_tid, r["position"])] -= 1

        _sal = _calc_ai_salary(dm["grade"], dm["tier"], _ovr,
                               dm["cname"], dm["tname"], dst_tid, year)
        _fee = estimate_transfer_fee(dm["grade"], dm["tier"], _ovr, country=dm["cname"],
                                     position=r["position"], year=year) or 0
        # 계약 기간은 이적시장과 같은 규칙(_ai_contract_duration_range)을 쓴다.
        _lo_c, _hi_c = _ai_contract_duration_range(r["age"] or 25, ovr=_ovr)
        _cend = year + random.randint(_lo_c, _hi_c)
        updates.append((dst_tid, _cend, year, _sal, pid))
        log_rows.append((_cur_season, year, pid, r["name"] or "", r["position"],
                         r["age"] or 25, _ovr, src_tid, dst_tid,
                         0, 0, round(sm["avg"], 1), round(dm["avg"], 1),
                         "상위리그 발탁", 0, "", _fee, 0, 0, _sal, _cend))
        n_moved += 1
        if _brk < _UPWARD_MIN_BREAKOUT:
            _n_stage_moved += 1

    _perf_log(f"[PERF-UPWARD] {year}년 상위리그 발탁: 후보 {_n_cand_all}명"
              f"(큰초과 {_n_always}) 중 {n_try}명 시도 → 성사 {n_moved} / "
              f"상한초과불가 {_fail_no_ceiling} / 목적지없음 {_fail_no_dst} / "
              f"{_time_up.perf_counter()-_up_t0:.2f}s "
              f"(평점 {len(_stat)}명 · 역할 {len(_role_up)}명) | "
              f"상위무대 문턱({_UPWARD_STAGE_OVR}+) 후보 {_n_stage_cand} → 성사 {_n_stage_moved}")
    if updates:
        c.executemany(
            "UPDATE ai_players SET team_id=?, contract_end_year=?, last_transfer_year=?, "
            "salary=? WHERE id=?", updates)
    if log_rows:
        c.executemany(
            """INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            log_rows)
    return n_moved


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

# [2026-09 신설, 신민용 리포트: "LB/RB 0명 팀이 왜 생기냐 — 말이 안 된다"]
# 위 그룹(GK/DF/MF/FW) 기준 판매 보호는 "DF가 0명이 되는가"만 본다. 그래서
# CB가 5명 남아 있으면 팀의 마지막 LB도, 마지막 RB도 그냥 팔려 나간다 —
# GK만 멀쩡했던 이유가 GK 그룹에 GK 하나뿐이라 자동으로 보호받았기
# 때문이다(실측 7시즌 월드: GK 0명 34팀 vs LB 0명 731팀 / RB 0명 758팀).
#
# 그래서 같은 보호를 세부 포지션 층에도 둔다 — formation_logic._SLOT_TARGETS
# 의 10개 핵심 포지션에 한해, 팀에 그 포지션이 1명뿐이면 그 선수는 판매
# 후보에서 빠진다. 2명 이상이면 건드리지 않는다(신민용 확정: "LB 2명 ->
# 1명 판매 가능 / LB 1명 -> 보호 / LB 0명 -> 영입 수요").
#
# [성능] 이 판정이 들어가는 루프는 이 파일에서 가장 뜨거운 자리다
# (시즌당 이적 7.4만 건 x 로스터 23명). 그래서 문자열 dict 카운터가 아니라
# 위 _POS_GROUP_IDX와 똑같은 방식의 인덱스 표 + 고정 길이 리스트를 쓴다.
_SLOT_POS_ORDER = ("GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST")
_SLOT_POS_IDX = {_p: _i for _i, _p in enumerate(_SLOT_POS_ORDER)}
_N_SLOT_POS = len(_SLOT_POS_ORDER)

# [2026-09 신설, 상류② — 신민용 확정: "매수만 막으면 CB 8명인 팀이 계속
# CB를 들고 있으면서 다른 포지션을 못 사는 문제가 생긴다"]
# formation_logic에 정의만 돼 있고 저장소 전체 참조가 0건이던
# position_sell_weight / position_demand_weight를 여기서 연결한다.
#
# 바로 위 _SLOT_POS_ORDER 주석의 보호가 "팔면 0명이 되는 선수"를 후보에서
# 통째로 빼는 절대 보호라면, 이 두 표는 그 위층의 확률 조정이다 —
# 보호를 통과한 후보들 사이에서 과잉 포지션은 더 잘 팔리게(sell), 그리고
# 그 포지션이 부족한 팀이 더 적극적으로 사러 가게(demand) 만든다.
#
# [성능] 두 함수를 후보마다 부르면 mover 선정과 목적지 평가(시즌당 각각
# 백만 회 단위)에 파이썬 함수 호출이 그대로 얹힌다. 입력이 (슬롯 인덱스,
# 그 팀의 그 포지션 인원) 두 정수뿐인 순수 함수라 값을 통째로 미리
# 펼쳐둘 수 있으므로, [슬롯][인원] 2차원 표로 만들어 조회 한 번으로
# 끝낸다. 인원은 _POS_CNT_CAP에서 자른다(그 위는 단조 구간이라 값 차이가
# 미미하고, 한 포지션에 13명 이상인 팀은 사실상 없다).
# 마지막 행(인덱스 _N_SLOT_POS)은 _SLOT_TARGETS 밖 포지션(LM/RM/DM/AM/
# LWB/RWB/SW/CF 등)용 중립 행이다 — dict.get의 기본값이 이 행을 가리키게
# 해서 루프 안에서 분기 없이 1.0이 곱해지게 한다.
_POS_CNT_CAP = 12


def _build_pos_weight_table(fn, strength):
    """fn(count, target) 값을 [슬롯][인원] 표로 펼친다. strength는
    constants의 희석 계수 — 1.0 + s x (raw - 1.0) 형태라 s=0이면 전부
    1.0(기능 꺼짐, 기존과 100% 동일)이고 s=1이면 원 함수 그대로다."""
    tbl = []
    for _pos in _SLOT_POS_ORDER:
        _tg = _SLOT_TARGET_MAP.get(_pos, 0)
        tbl.append([1.0 + strength * (fn(_c, _tg) - 1.0)
                    for _c in range(_POS_CNT_CAP + 1)])
    tbl.append([1.0] * (_POS_CNT_CAP + 1))   # 비대상 포지션용 중립 행
    return tbl


_SELL_W_TABLE = _build_pos_weight_table(_fl_position_sell_weight,
                                        POSITION_SELL_STRENGTH)
_DEMAND_W_TABLE = _build_pos_weight_table(_fl_position_demand_weight,
                                          POSITION_DEMAND_STRENGTH)


# ══════════════════════════════════════════════════════════════
# [2026-09 신설, 신민용 확정 — 베테랑 은퇴 무대 경로 확장]
# 실측(14시즌차 헤드리스, 연간): 5대 리그 1부를 떠난 30세+ OVR80+ 약 240명
# 중 A급 유럽 8~16 / B급 28~43 / 미국 4~9 / 사우디 0~1 / 카타르·중국 0.
# 기존 장치(해외 갈래 + S/SS 출발 + 30세 이상이면 60% 확률로 사우디·미국
# 1부로 전환)는 목적지가 둘뿐이고, 목적지 상한 +3 규칙에 걸려 사우디
# (당시 상한 79)·카타르·중국에는 사실상 못 들어갔다. 은퇴 판정·방출 인원·
# 해외 갈래 비율은 그대로 두고 "은퇴하지 않은 노장 중 해외 갈래로 나온
# 선수의 목적지"만 바꾼다:
#   1) 대상: 30세+, 현재 OVR 80~89, 전성기(peak_ovr, 없으면 현재 OVR) 86+
#      — 현재 90+는 이 경로에서 제외(일반 이적시장 그대로).
#   2) 출발지: 기존 S/SS 1부 + A급 유럽 1부(5대 리그→A급→은퇴 무대 2단계).
#   3) 목적지: A급/B급 유럽, 미국, 사우디, 카타르, 중국 1부 — 전성기 급
#      (이름값) × 현재 OVR 구간(지금 뛸 수 있는 수준) 표로 가중 추첨.
#      시도 확률은 기존 60%에서 급에 따라 최대 70%.
#   4) 이 경로에서만 목적지 상한 초과 허용폭 확대: 사우디 +6(최대 89 —
#      사우디 90+ 금지 유지), 카타르 +9, 중국 +13(일반 이적은 +3 그대로).
#   5) 팀당 고급 베테랑(30세+, 전성기 88+, 그 팀 기준 외국인) 인원 상한.
# ══════════════════════════════════════════════════════════════
_VET_ROUTE_MIN_AGE = 30
_VET_ROUTE_OVR_RANGE = (80, 89)
_VET_ROUTE_MIN_PEAK = 86
_VET_ROUTE_GROUPS = ("A_EU", "B_EU", "US", "SA", "QA", "CN")
_VET_ROUTE_COUNTRY_GROUP = {"미국": "US", "사우디아라비아": "SA", "카타르": "QA", "중국": "CN"}
_VET_ROUTE_CEIL_ALLOW = {"SA": 6, "QA": 9, "CN": 13}
_VET_ROUTE_TRY_P = {"T96": 0.70, "T93": 0.67, "T90": 0.64, "T88": 0.62, "T86": 0.60}
# (전성기 급, 현재 OVR 구간) → 목적지 그룹 가중치. 전성기 = 이름값(어느 급의
# 은퇴 무대인가), 현재 OVR = 지금 뛸 수 있는 층(어디까지 내려가는가).
_VET_ROUTE_WEIGHTS = {
    ("T96", "C87"): {"A_EU": 60, "US": 20, "SA": 20},
    ("T96", "C84"): {"A_EU": 35, "US": 30, "SA": 30, "QA": 5},
    ("T96", "C80"): {"B_EU": 10, "US": 35, "SA": 35, "QA": 20},
    ("T93", "C87"): {"A_EU": 55, "US": 20, "SA": 20, "QA": 5},
    ("T93", "C84"): {"A_EU": 30, "B_EU": 5, "US": 25, "SA": 25, "QA": 15},
    ("T93", "C80"): {"B_EU": 15, "US": 25, "SA": 25, "QA": 25, "CN": 10},
    ("T90", "C87"): {"A_EU": 50, "B_EU": 10, "US": 15, "SA": 15, "QA": 10},
    ("T90", "C84"): {"A_EU": 25, "B_EU": 20, "US": 15, "SA": 20, "QA": 20},
    ("T90", "C80"): {"B_EU": 30, "US": 10, "SA": 10, "QA": 30, "CN": 20},
    ("T88", "C87"): {"A_EU": 40, "B_EU": 30, "US": 5, "SA": 5, "QA": 20},
    ("T88", "C84"): {"B_EU": 40, "US": 5, "SA": 10, "QA": 30, "CN": 15},
    ("T88", "C80"): {"B_EU": 40, "QA": 30, "CN": 30},
    ("T86", "C87"): {"A_EU": 30, "B_EU": 40, "QA": 20, "CN": 10},
    ("T86", "C84"): {"B_EU": 45, "QA": 30, "CN": 25},
    ("T86", "C80"): {"B_EU": 45, "QA": 25, "CN": 30},
}
# 팀당 고급 베테랑(30세+, 전성기 88+, 그 팀 기준 외국인) 인원 상한(lo, hi) —
# 팀마다 tid로 고정된 값 하나(매 호출 난수 X, 결과 재현성 유지).
_VET_ROUTE_TEAM_CAP = {"US": (3, 4), "SA": (2, 3), "SA_BIG": (4, 4), "QA": (2, 3), "CN": (1, 2)}
_VET_ROUTE_HIGH_PEAK = 88


def _vet_peak_tier(peak):
    if peak >= 96:
        return "T96"
    if peak >= 93:
        return "T93"
    if peak >= 90:
        return "T90"
    if peak >= 88:
        return "T88"
    return "T86"


def _vet_ovr_band(ovr):
    if ovr >= 87:
        return "C87"
    if ovr >= 84:
        return "C84"
    return "C80"


def _vet_team_cap(tid, grp, prestige):
    key = "SA_BIG" if (grp == "SA" and (prestige or 0) >= 2) else grp
    lo, hi = _VET_ROUTE_TEAM_CAP.get(key, (99, 99))
    return lo + (tid % (hi - lo + 1))


def _pick_vet_route(mover, src, ctx):
    """[2026-09 신설] 위 베테랑 경로 블록 주석 참고. 대상이고 시도 확률을
    통과하면 목적지 그룹 이름을, 아니면 None(호출부는 기존 목적지 풀 그대로 —
    잔류·일반 이적은 기존과 동일)."""
    age = mover.get("age") or 0
    ovr = mover.get("ovr") or 0
    lo, hi = _VET_ROUTE_OVR_RANGE
    if age < _VET_ROUTE_MIN_AGE or ovr < lo or ovr > hi:
        return None
    peak = max(ovr, ctx["peak"].get(mover["id"], 0) or 0)
    if peak < _VET_ROUTE_MIN_PEAK:
        return None
    ptier = _vet_peak_tier(peak)
    if random.random() >= _VET_ROUTE_TRY_P[ptier]:
        return None
    table = _VET_ROUTE_WEIGHTS[(ptier, _vet_ovr_band(ovr))]
    names, ws = [], []
    for g, w in table.items():
        grp_tids = ctx["groups"].get(g) or []
        if not grp_tids or (len(grp_tids) == 1 and grp_tids[0] == src):
            continue
        if ovr > ctx["group_max_ovr"].get(g, 0):   # 그 그룹 어디에도 못 들어가는 OVR
            continue
        names.append(g)
        ws.append(w)
    if not names:
        return None
    return random.choices(names, weights=ws, k=1)[0]


_LAST_VET_ROUTE_TALLY: dict = {}   # 마지막 _transfer_market 호출의 경로별 이적 수(계측용)
_LAST_BRA_EXPORT_TALLY: dict = {}  # 마지막 _transfer_market 호출의 브라질 수출 경로 집계(계측용)


def _try_brazil_export(mover, src, ctx, quota_hi_by_tid, foreign_count_by_tid):
    """[2026-10 신설] 브라질 수출 경로(constants.BRAZIL_EXPORT_* 주석 참고).
    출발 부수 확률을 통과하면, 그 부수에 허용된 목적지 등급(BRAZIL_EXPORT_DEST_
    BY_TIER) 안에서 이 선수 OVR로 들어갈 수 있는 수출 후보 리스트를,
    아니면 None을 돌려준다(호출부는 기존 목적지 풀 그대로).
    · 확률이 0인 부수(1부)는 난수를 소비하지 않는다.
    · 후보 리스트는 아래 목적지 루프의 상한(+_DST_CEIL_HARD_EXCLUDE)·하한
      (−_DST_FLOOR_HARD_EXCLUDE) 하드 제외와 같은 기준으로 미리 거른 것이라,
      루프 결과는 전체 수출 풀을 넘긴 것과 같다.
    · 남은 후보가 전부 외국인 정원이 찬 팀이면 None — 원래 풀로 정상 이적한다
      (이 경로 때문에 이적 자체가 사라지지 않게)."""
    tier = ctx["tier"].get(src, 1)
    rec = ctx["tally"].get(tier)
    if rec is None:
        rec = ctx["tally"][tier] = {"대상": 0, "발동": 0, "성공": 0, "실패_OVR범위": 0,
                                    "실패_정원": 0, "목적지": {}, "ovr_sum": 0}
    rec["대상"] += 1
    p = ctx["p"].get(tier, 0.0)
    if p <= 0.0 or random.random() >= p:
        return None
    rec["발동"] += 1
    ovr = mover.get("ovr") or 0
    band = ctx["band"].get((tier, ovr))
    if band is None:
        grades = ctx["dest"].get(tier, ())
        band = [t for t, g, flr, ceil in ctx["base"]
                if g in grades
                and (flr - ovr) <= _DST_FLOOR_HARD_EXCLUDE
                and (ovr - ceil) <= _DST_CEIL_HARD_EXCLUDE]
        ctx["band"][(tier, ovr)] = band
    if not band:
        rec["실패_OVR범위"] += 1
        return None
    if foreign_count_by_tid is not None and quota_hi_by_tid is not None:
        for t in band:
            qhi = quota_hi_by_tid.get(t)
            if qhi is None or foreign_count_by_tid.get(t, 0) < qhi:
                break
        else:
            rec["실패_정원"] += 1
            return None
    return band


def _do_one_transfer_cached(src, dst_pool_tids, team_players, team_avg, year, protect_strength=0.85,
                             veteran_pool_tids=None, decline_pool_tids=None,
                             dst_grade_by_tid=None, dst_prestige_by_tid=None,
                             pool_cache=None, sw_by_tid=None, src_grade_rank=None,
                             dst_ovr_ceiling_by_tid=None, dst_country_by_tid=None,
                             dst_quota_hi_by_tid=None, foreign_count_by_tid=None,
                             pos_count_by_tid=None, dst_ovr_floor_by_tid=None,
                             src_floor_ref=None, vet_route=None, bra_export=None):
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

    # [2026-09 성능] 예전엔 src_players를 두 번(자격자 추림 / 포지션군 카운트)
    # 돌았다. 한 번만 돌면서 둘 다 만든다 — 값·순서·이후 로직은 그대로다.
    # 최소 잔류기간: 작년(또는 그 이후)에 이미 이적한 선수는 이번엔 후보 제외
    # [2026-08 최적화] team_players의 각 항목은 _transfer_market이 만들 때
    # 6개 키를 항상 전부 채우고(빠지는 경우가 구조적으로 없음), 이적으로
    # 팀을 옮겨 다니는 동안에도 같은 dict가 그대로 재사용된다 — 그래서
    # 이 함수 전체에서 .get(키, 기본값) 대신 직접 인덱싱을 쓴다. cProfile
    # 실측상 이 함수 하나가 dict.get을 2,421만 회 호출하고 있었는데(시즌당
    # 이적 7.4만 건 × 후보 선수 수 × 키 6개), 결과는 완전히 동일하면서
    # 호출당 오버헤드만 사라진다.
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
    # [2026-09 신설] 세부 포지션 인원 — 위 _SLOT_POS_ORDER 주석 참고.
    _spi = _SLOT_POS_IDX
    _scnt = [0] * _N_SLOT_POS
    eligible = []
    _elig_append = eligible.append
    for _p in src_players:
        _ps = _p["position"]
        _cnt[_gi.get(_ps, 3)] += 1
        _si2 = _spi.get(_ps, -1)
        if _si2 >= 0:
            _scnt[_si2] += 1
        # [2026-09 신설, 신민용 확정: "FIFA 임대 규정을 지켜야 한다 — 11년
        # 동안 임대로 써진 경우도 있다"] 임대 중인 선수는 임대처가 이적·
        # 재임대시킬 수 없다(원 소속팀만 권리가 있다). 예전엔 임대처가 이
        # 선수를 다시 임대 보내거나 팔 수 있어서 "A→B 임대 → B가 C로 재임대"
        # 같은 사슬이 생기고 원 소속팀 복귀가 계속 밀렸다(5시즌 헤드리스
        # 실측 2만여 건). 스쿼드 인원·포지션 수 집계(위 _cnt/_scnt)에는
        # 그대로 포함한다 — 그 팀에서 실제로 뛰는 선수이므로.
        if (year - _p["last_transfer_year"]) >= 1 and not _p.get("on_loan"):
            _elig_append(_p)
    if not eligible:
        return None
    if _cnt[0] <= 1 or _cnt[1] <= 1 or _cnt[2] <= 1 or _cnt[3] <= 1:
        _thin = {_GROUP_ORDER[_i] for _i in range(4) if _cnt[_i] <= 1}
        _protected_ids = {p["id"] for p in eligible
                           if _pg.get(p["position"], "FW") in _thin}
        if _protected_ids and len(_protected_ids) < len(eligible):
            eligible = [p for p in eligible if p["id"] not in _protected_ids]
    # [2026-09 신설] 세부 포지션 보호 — 그 포지션이 팀에 1명뿐이면 판다고
    # 0명이 된다. 그룹 보호와 같은 원칙을 한 층 아래에 적용할 뿐이며,
    # 판매 확률 가중치는 여기서도 전혀 건드리지 않는다(그룹 보호와 동일).
    # eligible을 통째로 비우게 되는 극단적 경우엔 적용하지 않는 것도 같다.
    if eligible:
        _thin_pos = {_SLOT_POS_ORDER[_i] for _i in range(_N_SLOT_POS)
                     if _scnt[_i] == 1}
        if _thin_pos:
            _prot_pos_ids = {p["id"] for p in eligible if p["position"] in _thin_pos}
            if _prot_pos_ids and len(_prot_pos_ids) < len(eligible):
                eligible = [p for p in eligible if p["id"] not in _prot_pos_ids]
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
        # [2026-09 신설, 상류②] 이 팀의 포지션별 인원(_scnt)은 이 호출
        # 내내 불변이라, 후보마다 표를 두 단계로 타는 대신 여기서 슬롯별
        # 배율을 한 번(10칸)만 펼쳐둔다. 맨 뒤 칸은 중립(1.0)이라
        # _SLOT_TARGETS 밖 포지션은 아래 dict.get 기본값으로 바로 온다.
        _sellw = [_SELL_W_TABLE[_i][_scnt[_i] if _scnt[_i] <= _POS_CNT_CAP
                                    else _POS_CNT_CAP]
                  for _i in range(_N_SLOT_POS)]
        _sellw.append(1.0)
        _neg_ovr = [-e["ovr"] for e in eligible]
        ranked = sorted(range(n), key=_neg_ovr.__getitem__)
        _inv = n - 1   # n>=2 이므로 예전의 max(1, n-1)과 항상 같은 값
        weights = [0.0] * n
        for pos_rank, i in enumerate(ranked):
            _e = eligible[i]
            w = 0.15 + protect_strength * (pos_rank / _inv)
            # 팀 내 역할(0=주전 … 3=전력외, 4=유망주). 호출부가
            # hist.ai_player_position_history에서 읽어 실어둔 값이며
            # (_load_role_index 참고) 여기서 새로 계산하지 않는다.
            _role_i = _e["role_i"]
            _cend = _e["contract_end_year"] or 0
            remain = max(0, _cend - year) if _cend else 2   # 미설정=중립(2년 취급)
            w *= _CONTRACT_DECAY.get(remain) or 0.6 ** remain
            # [2026-07 v3] 어린 선수(22세 이하)·고OVR(80+)·계약만료 임박(1년
            # 이하)일수록 이 풀(국내 다른 tier/국제 이동)로 실제 이동될
            # 성향을 살짝 높인다. dst_pool_tids가 src 포함 같은 리그 그대로면
            # (=87% 케이스) 이 보정은 사실상 의미 없이 상쇄되므로 안전하다.
            _age = _e["age"]
            _eovr = _e["ovr"]   # [2026-09 성능] 아래에서 두 번 하던 dict 조회를 한 번으로
            if _age <= 22:
                w *= 1.3
            if _eovr >= 80:
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
            _oim_gap = (_eovr or 0) - _oim_base
            if _oim_gap > 0.0:
                w *= 1.0 + min(_oim_cap, _oim_gap / _oim_div) * _oim_ms
            # [2026-09 신설, 상류②] 과잉 포지션이면 1보다 크고,
            # 부족하거나 목표에 딱 맞으면 1보다 작다(보호).
            w *= _sellw[_spi.get(_e["position"], _N_SLOT_POS)]
            # ── [2026-09 신설, 신민용 요청 ②: "나이 + OVR + 출전시간 +
            # 역할 + 대체자 + 임금 + 팀 수준을 같이 봐야 한다"] ──────
            # 위까지가 기존 네 축(OVR순위=역할 근사, 나이, 계약,
            # 포지션 과잉=대체자)이고, 여기부터가 이번에 새로 들어온
            # 세 축(팀 내 역할, 경기력, 임금)이다. 팀 수준은 호출부가
            # 넘기는 protect_strength(_STAR_PROTECT_BY_CATEGORY)가
            # 이미 담당한다. 평점·연봉은 "데이터 없으면 통과"라 기존
            # 세이브에서도 회귀가 없다(_load_role_index 주석 참고 —
            # 원래 쓰려던 "출전시간"은 이 엔진에 존재하지 않아 역할로
            # 대체했다).
            if _role_i is not None:
                w *= _ROLE_SELL_W[_role_i]
                # 노장인데 비주전 — 리포트 14번(39세 OVR78 잔류) 직격.
                # "주전이면 무조건 유지"가 아니라, 역할이 나이 배수를
                # 얼마나 키울지를 정할 뿐이다(주전이면 이 항이 안 걸리고
                # 대신 위 나이 배수 ×1.0+0.10×(나이-32)는 그대로 적용된다).
                if (_age >= _VET_BENCH_AGE and _VET_BENCH_ROLE <= _role_i <= 4):
                    w *= 1.0 + _VET_BENCH_STEP * (_age - _VET_BENCH_AGE + 1)
                # 고임금인데 비주전 — 현실 구단이 가장 먼저 정리하는 유형.
                _sp = _e["sal_pct"]
                if (_sp is not None and _sp <= _HIGH_WAGE_PCT
                        and _HIGH_WAGE_ROLE <= _role_i <= 4):
                    w *= _HIGH_WAGE_MULT
            _rr = _e["rel_rating"]
            if _rr is not None:
                if _rr < 0.0:
                    w *= 1.0 + min(0.6, -_rr * _RATING_W_BAD)
                else:
                    w *= 1.0 - min(0.25, _rr * _RATING_W_GOOD)
            # ── [2026-09 신설] 소속 리그 기준선 미달 → 매물 우선도 ─────
            # 위 축들은 전부 "팀 내 상대 위치"(OVR 순위/역할/임금)나 나이여서,
            # 팀 전체가 같이 노쇠해 리그 수준에서 동반 미달한 경우에는 아무도
            # 안 팔린다 — 팀 내 순위는 그대로이기 때문. 리그 기준선과의
            # 절대 격차를 따로 곱해야 그 구간이 풀린다.
            if src_floor_ref:
                _sf = src_floor_ref - (_eovr or 0)
                if _sf > 0.0:
                    w *= _interp_pts(_SHORTFALL_SELL_PTS, _sf)
            weights[i] = w
        mover = random.choices(eligible, weights=weights, k=1)[0]

    # [2026-08 신설, 신민용 요청: "사우디·미국 1부 위주로 가는 그림"]
    # 나이 든(30세+) 선수가 최상위권 리그를 떠나는 경우, 이 풀이 있으면
    # 60% 확률로 목적지 후보를 여기로 바꾼다 — 나머지 40%/veteran_pool
    # 자체가 비어있는 경우엔 기존 등급대 기반 일반 후보군을 그대로 쓴다
    # (은퇴 무대로 완전히 강제하지 않고 개인차/확률을 남겨둔다).
    _pool_switched = False
    _vet_grp = None
    if vet_route is not None:
        _vet_grp = _pick_vet_route(mover, src, vet_route)
        if _vet_grp is not None:
            dst_pool_tids = vet_route["groups"][_vet_grp]
            _pool_switched = True
            veteran_pool_tids = None   # 새 경로가 기존 60% 전환을 대체
    # [2026-10 신설] 브라질 수출 경로 — 브라질 클럽 소속 브라질 국적 선수만.
    # 전환되면 아래 노장 하향 풀은 건너뛴다(_pool_switched).
    _bra_rec = None
    if (bra_export is not None and not _pool_switched
            and (mover.get("nationality") or "") == "브라질"):
        _bra_pool = _try_brazil_export(mover, src, bra_export,
                                       dst_quota_hi_by_tid, foreign_count_by_tid)
        if _bra_pool is not None:
            dst_pool_tids = _bra_pool
            _pool_switched = True
            _bra_rec = bra_export["tally"][bra_export["tier"].get(src, 1)]
    if veteran_pool_tids and mover["age"] >= 30 and random.random() < 0.6:
        # [2026-08 최적화] 예전엔 여기서 매번 [t for t in veteran_pool_tids
        # if t != src]로 새 리스트를 만들었다 — src 제외는 아래 가중치
        # 루프가 어차피 한 번 더 하므로, 여기서는 "src를 뺐을 때 남는 팀이
        # 하나라도 있는지"만 O(1)로 판정하고 원본 리스트를 그대로 넘긴다
        # (아래 풀 메타데이터 캐시가 같은 리스트 객체를 재사용할 수 있게
        # 하는 효과도 있다). 판정 결과·이후 동작은 예전과 동일.
        if len(veteran_pool_tids) > 1 or veteran_pool_tids[0] != src:
            dst_pool_tids = veteran_pool_tids
            _pool_switched = True
    # [2026-09 신설, 신민용 리포트 14번] 위 사우디/미국 "은퇴 무대"에
    # 안 걸린 노장 중에서도, 34세 이상이면서 이번 시즌 거의 못 뛴 선수는
    # 같은 등급 리그에 계속 남는 대신 한 단계 아래 무대로 내려간다.
    # (호출부 _decline_pool 주석 참고 — 같은 리그 안에서만 도는 87%
    #  경로에서는 기존 나이 페널티가 상대 가중치에 전혀 영향을 못 준다.)
    # [2026-09 확장] 위 게이트(34세 + 대기/전력외)는 **나이만** 보기 때문에,
    # 28세에 OVR이 무너진 선수는 어느 경로에도 안 걸렸다. 리그 기준선 미달
    # 폭으로도 같은 전환이 일어나게 하고, 둘 중 높은 확률을 쓴다
    # (나이 경로는 기존 동작 그대로 보존 — 미달이 0이어도 그대로 작동).
    if (not _pool_switched) and decline_pool_tids:
        _mri = mover.get("role_i")
        _dp_chance = 0.0
        if (mover["age"] >= _VET_BENCH_AGE and _mri is not None
                and _mri >= _VET_BENCH_ROLE):
            _dp_chance = _VET_DECLINE_CHANCE
        if src_floor_ref:
            _msf = src_floor_ref - (mover["ovr"] or 0)
            if _msf >= _SHORTFALL_DECLINE_MIN:
                _dp_chance = max(_dp_chance, _interp_pts(_SHORTFALL_DECLINE_PTS, _msf))
        if _dp_chance > 0.0 and random.random() < _dp_chance:
            if len(decline_pool_tids) > 1 or decline_pool_tids[0] != src:
                dst_pool_tids = decline_pool_tids

    mover_ovr = mover["ovr"]
    # [2026-09 신설, 신민용 요청: "사우디/미국/A급 유럽처럼 조국 귀환도
    # 약하게 선호해야 한다"] 30세 이상이고 목적지 후보군에 국가 정보가
    # 있을 때만 활성화 — 어린 선수의 이적까지 조국 쪽으로 밀면 자연스러운
    # 해외 진출 흐름을 해치므로 베테랑 한정. 배율도 1.5로 약하게만 줘서
    # "33~36세면 다 고향으로" 같은 극단이 안 나오게 한다(다른 후보들도
    # 여전히 뽑힐 수 있음 — 배제가 아니라 가산일 뿐).
    _HOME_RETURN_BONUS = 1.5
    _mover_nat = mover.get("nationality") or None
    # [2026-09 수정] 예전엔 "목적지가 이 선수가 자국 선수로 등록된
    # 나라면 쿼터 외국인이 아니다"로 보고 목적지 필터에서 면제했는데,
    # 그러면 전환자가 상한이 찬 팀으로 자유롭게 옮겨다니며 진짜 외국인
    # 수를 다시 불린다 — 이제 로스터 제한은 등록 신분과 무관하게 진짜
    # 국적으로만 센다(database.is_roster_foreign). 이 값은 다른 용도
    # (연봉/기록 등)로 남겨두되 목적지 필터에서는 더 이상 안 본다.
    _mover_qlc = mover.get("quota_local_country") or ""
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
    # [2026-09] len(_meta) 확인 추가 — 하한(_floors)이 늘어나 튜플 형태가
    # 바뀌었으므로, 예전 형태로 캐시된 항목이 남아 있어도 안전하게 재계산된다.
    if _meta is None or _meta[0] is not dst_pool_tids or len(_meta) != 8:
        _avgs = [team_avg.get(t, 50) for t in dst_pool_tids]
        if dst_prestige_by_tid:
            _dens = []
            for t in dst_pool_tids:
                _p = dst_prestige_by_tid.get(t, 0)
                # [2026-09 수정, 신민용 지적: "명문팀들은 prestige_clubs.py의
                # 3·2·1 기준으로 3이 가장 좋은 선수들을 데리고 오고 2·1
                # 순으로"] 이 분모가 작을수록 "팀 평균과 비슷한 수준의
                # 선수만 받는다"는 뜻이라, 명문팀일수록 작아야 한다.
                # 예전엔 3급 35 / 2급 60 / **1급과 일반이 똑같이 170**이라
                # 명문1(토트넘·로마·나폴리·리옹·모나코·레버쿠젠·발렌시아·
                # 세비야)이 이 통로에서 아무런 우대를 못 받고 있었다 —
                # 실측에서도 명문1의 성인 90미만 비율(29.7%)이 일반팀
                # (34.1%)과 거의 차이가 없었다. 3 > 2 > 1 > 일반 서열이
                # 실제로 보이게 1급에 중간값을 준다.
                _dens.append(_PRESTIGE_DST_DENOM.get(_p, 170.0))
        else:
            _dens = [170.0] * len(dst_pool_tids)
        _tops = ([(dst_grade_by_tid.get(t) in ("SS", "S")) for t in dst_pool_tids]
                 if dst_grade_by_tid is not None else None)
        # [2026-09 신설] 목적지 나라 설계 OVR 상한 페널티용 배열 —
        # dst_ovr_ceiling_by_tid가 없으면(하위호환 경로) 전부 None이라
        # 아래 루프에서 _dst_ceiling_penalty가 항상 1.0(무영향)을 반환한다.
        _ceils = ([dst_ovr_ceiling_by_tid.get(t) for t in dst_pool_tids]
                  if dst_ovr_ceiling_by_tid is not None else [None] * len(dst_pool_tids))
        # [2026-09 신설] 하한(floor) 배열 — 상한과 완전히 같은 방식·같은
        # 캐시 수명(_DST_FLOOR_* 정의부 주석 참고). None이면 이 체크 자체가
        # 꺼져 기존 동작과 100% 동일하다(하위호환 경로).
        _floors = ([dst_ovr_floor_by_tid.get(t) for t in dst_pool_tids]
                   if dst_ovr_floor_by_tid is not None else [None] * len(dst_pool_tids))
        # [2026-09 신설] 조국 귀환 가산용 — dst_pool_tids 자체(어떤 팀들이
        # 후보인가)에만 의존하는 값이라(mover와 무관) 다른 배열들과 똑같이
        # 풀 단위로 캐싱해도 안전하다. mover 국적과의 실제 비교는 아래
        # 루프에서 mover 하나로 매 호출마다 다르게 이뤄진다.
        _countries = ([dst_country_by_tid.get(t) for t in dst_pool_tids]
                      if dst_country_by_tid is not None else [None] * len(dst_pool_tids))
        # [2026-09 신설, 신민용 리포트: "K리그에 외국인만 절반 이상인 팀도
        # 나온다 — database.FOREIGN_QUOTA_RANGE가 있는데 왜 안 지켜지냐"]
        # 팀 생성/은퇴교체/내 선수 입단 때는 쿼터를 지키는데, 정작 매
        # 시즌 도는 이 AI 이적 시장만 국적을 전혀 안 봐서 시즌을 거듭할
        # 수록 특정 팀에 외국인이 한도 없이 쌓일 수 있었다. dst_quota_
        # hi_by_tid(팀별 상한, 국가/대륙/tier로 정해지는 값이라 풀과
        # 무관하게 불변)도 다른 배열들과 같은 방식으로 풀 단위 캐싱.
        # 상한 위반 여부 실제 판정(foreign_count_by_tid, 이적마다 실시간
        # 갱신)은 mover와 무관하게 dst_pool_tids에만 의존하므로 이 배열도
        # 풀 캐시에 넣어도 안전 — "지금 몇 명인가"는 매번 다르지만 그건
        # 캐시 밖(foreign_count_by_tid 자체)에서 조회한다.
        _quota_his = ([dst_quota_hi_by_tid.get(t) for t in dst_pool_tids]
                      if dst_quota_hi_by_tid is not None else [None] * len(dst_pool_tids))
        _meta = (dst_pool_tids, _avgs, _dens, _tops, _ceils, _countries, _quota_his,
                 _floors)
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
    _, _avgs, _dens, _tops, _ceils, _countries, _quota_his, _floors = _meta
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
    # [2026-09 신설] 외국인 쿼터 체크 활성 여부 — 두 인자가 다 있고
    # (foreign_count_by_tid는 살아있는 팀별 외국인 수 표, dst_quota_
    # hi_by_tid는 위 _quota_his로 이미 풀 단위 배열이 됨) mover 국적이
    # 있을 때만 켠다. 하위호환 경로(_do_one_transfer, 옛 테스트 등)는
    # 두 인자를 안 넘기므로 이 블록 자체가 항상 꺼져 있어 기존 동작과
    # 100% 동일하다.
    # [2026-09 신설, 상류②] 목적지 포지션 수요 — mover 포지션이
    # _SLOT_TARGETS 대상이고 호출부가 팀별 포지션 인원표를 넘겼을 때만
    # 켠다. 하위호환 경로(인자를 안 넘기는 옛 호출)는 _dw가 None이라
    # 기존과 100% 동일하게 동작한다.
    _mv_si = _SLOT_POS_IDX.get(mover["position"], -1)
    _dw = None
    _pc = None
    _dw_cap = _POS_CNT_CAP
    if pos_count_by_tid is not None and _mv_si >= 0:
        _dw = _DEMAND_W_TABLE[_mv_si]
        _pc = pos_count_by_tid
    _quota_check_on = foreign_count_by_tid is not None and bool(_mover_nat)
    # [2026-09 베테랑 경로] 이 경로로 전환된 이적에서만 목적지 상한 초과
    # 허용폭을 넓힌다(_ceil_shift만큼 상한을 올려 보는 것과 같음 — 하드 제외와
    # 소프트 감쇠 둘 다 같은 기준으로 이동). 일반 이적은 _ceil_shift=0.
    _ceil_shift = (max(0, _VET_ROUTE_CEIL_ALLOW.get(_vet_grp, _DST_CEIL_HARD_EXCLUDE)
                       - _DST_CEIL_HARD_EXCLUDE) if _vet_grp else 0)
    _vet_cap_on = False
    _vcnt = _vcap = None
    if _vet_grp in ("US", "SA", "QA", "CN"):
        _mpeak = max(mover_ovr or 0, vet_route["peak"].get(mover["id"], 0) or 0)
        _vet_cap_on = _mpeak >= _VET_ROUTE_HIGH_PEAK
        _vcnt = vet_route["cnt"]
        _vcap = vet_route["cap"]
    if _apply_age_penalty:
        for t, _avg, _den, _top, _ceil, _cty, _qhi, _flr in zip(
                dst_pool_tids, _avgs, _dens, _tops, _ceils, _countries, _quota_his,
                _floors):
            if t == src:
                continue
            if _flr is not None and (_flr - mover_ovr) > _DST_FLOOR_HARD_EXCLUDE:
                # [2026-09 신설] 목적지 리그 설계 하한에 크게 못 미치는 선수는
                # 후보에서 제외 — _DST_FLOOR_* 정의부 주석 참고.
                continue
            if _ceil is not None and (mover_ovr - _ceil - _ceil_shift) > _DST_CEIL_HARD_EXCLUDE:
                # [2026-09 신설] 초과분이 크면 size_weight(스쿼드 부족팀
                # 가중치)가 아무리 커도 못 이기게, 애초에 후보에서 제외한다
                # (위 _DST_CEIL_HARD_EXCLUDE 정의부 주석 — 실측으로 확인한
                # 실패 사례 참고).
                continue
            # [2026-09 신설, 외국인 쿼터 예방] 이 선수가 목적지 팀 기준
            # 외국인(국적≠목적지 나라)이고, 그 팀이 이미 상한(quota_hi)에
            # 도달했으면 후보에서 아예 뺀다 — 자국 선수 영입이나 쿼터
            # 여유가 있는 팀은 전혀 영향 없다.
            # [2026-09 수정] _cty != _mover_qlc(자국 등록 전환자는 면제)
            # 예외를 뺀다 — 면제해두면 전환자가 상한 찬 팀으로 자유롭게
            # 옮겨다니며 진짜 외국인 수를 다시 불린다(위 집계 수정과
            # 같은 이유). 등록 신분과 무관하게 "국적이 다르면 한 자리".
            if (_vet_cap_on and _cty != _mover_nat
                    and _vcnt.get(t, 0) >= _vcap.get(t, 99)):
                continue
            if (_quota_check_on and _qhi is not None and _cty and _cty != _mover_nat
                    and foreign_count_by_tid[t] >= _qhi):
                continue
            gap = _avg - mover_ovr
            w = _exp(-(gap * gap) / _den) * _sw_by_tid[t]
            if _top:
                w *= _age_penalty
            if _ceil is not None:
                _excess = mover_ovr - _ceil - _ceil_shift
                if _excess > 0:
                    w *= _exp(-(_excess * _excess) / _DST_CEIL_EXCESS_DENOM)
            if _flr is not None:
                _deficit = _flr - mover_ovr
                if _deficit > 0:
                    w *= _exp(-(_deficit * _deficit) / _DST_FLOOR_DEFICIT_DENOM)
            if _home_bonus_on and _cty == _mover_nat:
                w *= _HOME_RETURN_BONUS
            if _dw is not None:
                _pcv = _pc[t][_mv_si]
                w *= _dw[_pcv if _pcv <= _dw_cap else _dw_cap]
            _dc_append(t)
            _w_append(w)
            _wsum += w
    else:
        for t, _avg, _den, _ceil, _cty, _qhi, _flr in zip(
                dst_pool_tids, _avgs, _dens, _ceils, _countries, _quota_his, _floors):
            if t == src:
                continue
            if _flr is not None and (_flr - mover_ovr) > _DST_FLOOR_HARD_EXCLUDE:
                continue
            if _ceil is not None and (mover_ovr - _ceil - _ceil_shift) > _DST_CEIL_HARD_EXCLUDE:
                continue
            # [2026-09 수정] _cty != _mover_qlc(자국 등록 전환자는 면제)
            # 예외를 뺀다 — 면제해두면 전환자가 상한 찬 팀으로 자유롭게
            # 옮겨다니며 진짜 외국인 수를 다시 불린다(위 집계 수정과
            # 같은 이유). 등록 신분과 무관하게 "국적이 다르면 한 자리".
            if (_vet_cap_on and _cty != _mover_nat
                    and _vcnt.get(t, 0) >= _vcap.get(t, 99)):
                continue
            if (_quota_check_on and _qhi is not None and _cty and _cty != _mover_nat
                    and foreign_count_by_tid[t] >= _qhi):
                continue
            gap = _avg - mover_ovr
            w = _exp(-(gap * gap) / _den) * _sw_by_tid[t]
            if _ceil is not None:
                _excess = mover_ovr - _ceil - _ceil_shift
                if _excess > 0:
                    w *= _exp(-(_excess * _excess) / _DST_CEIL_EXCESS_DENOM)
            if _flr is not None:
                _deficit = _flr - mover_ovr
                if _deficit > 0:
                    w *= _exp(-(_deficit * _deficit) / _DST_FLOOR_DEFICIT_DENOM)
            if _home_bonus_on and _cty == _mover_nat:
                w *= _HOME_RETURN_BONUS
            if _dw is not None:
                _pcv = _pc[t][_mv_si]
                w *= _dw[_pcv if _pcv <= _dw_cap else _dw_cap]
            _dc_append(t)
            _w_append(w)
            _wsum += w
    if not dst_candidates:
        return None
    if _wsum <= 0:
        dst = random.choice(dst_candidates)
    else:
        dst = random.choices(dst_candidates, weights=weights, k=1)[0]
    if _bra_rec is not None:
        _bra_rec["성공"] += 1
        _bra_rec["ovr_sum"] += mover_ovr or 0
        _bk = ((dst_grade_by_tid.get(dst) if dst_grade_by_tid is not None else None),
               bra_export["tier"].get(dst))
        _bra_rec["목적지"][_bk] = _bra_rec["목적지"].get(_bk, 0) + 1
    if _vet_grp is not None:
        vet_route["tally"][_vet_grp] = vet_route["tally"].get(_vet_grp, 0) + 1
        if _vet_cap_on and dst_country_by_tid is not None and dst_country_by_tid.get(dst) != _mover_nat:
            _vcnt[dst] = _vcnt.get(dst, 0) + 1
    dst_players = team_players.get(dst, [])
    # [2026-09 버그수정] 맞트레이드로 되돌려 보내는 선수도 임대 중이면 안 된다
    # — 목적지 팀이 원 소속팀의 선수를 넘기는 셈(위 eligible 필터와 같은 규칙,
    # 5시즌 헤드리스 실측: 남아 있던 재이적 1,135건이 전부 이 경로였다).
    same_pos = [p for p in dst_players if p["position"] == mover["position"]
                and (year - p["last_transfer_year"]) >= 1 and not p.get("on_loan")]

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
        "SELECT id, team_id, position, ovr, age, name, nationality, quota_local_country, "
        "contract_end_year, last_transfer_year, salary "
        "FROM ai_players WHERE team_id IN ({})".format(
            ",".join("?" for _ in tids)), tids).fetchall()
    tp: dict = {}
    for r in players_rows:
        # [2026-09] _do_one_transfer_cached는 이 dict의 키를 .get 없이 직접
        # 인덱싱한다(성능 주석 참고) — 그쪽이 읽는 키를 전부 채워야 한다.
        # 성과 지표 3종은 이 하위호환 경로에서 조회하지 않으므로 None
        # (="데이터 없음", 가중치 중립)으로 둔다.
        tp.setdefault(r["team_id"], []).append({
            "id": r["id"], "position": r["position"],
            "name": r["name"] or "",
            "age": (r["age"] or 25),
            "ovr": r["ovr"] if r["ovr"] is not None else 50,
            "contract_end_year": r["contract_end_year"] or 0,
            "last_transfer_year": r["last_transfer_year"] or 0,
            "nationality": r["nationality"] or "",
            "quota_local_country": r["quota_local_country"] or "",
            "salary": r["salary"] or 0,
            "_rt": None, "rel_rating": None, "sal_pct": None, "role_i": None,
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
_SQUAD_TARGET   = 23   # 정상범위(22~25)의 중간값 — 아래 등급별 표가 없는
                        # 곳(예: _transfer_market의 exp 가중치 함수 — 이미
                        # 세밀하게 튜닝돼 있어 이번엔 등급별로 안 건드림)의
                        # 기존 기본값. 그대로 둔다.
_SQUAD_MIN      = 22   # 위와 동일한 이유로 기존 값 유지(등급별 미적용 폴백).
_SQUAD_MAX      = 25   # 위와 동일.

# [2026-09 신설, 신민용 확정: "S 25~28 / A 24~27 / B 23~26 / C~F 22~25로
# 등급별 로스터 범위를 나누자"] _rebalance_squad_sizes의 부족/과다 판정
# 임계값만 이 표로 등급별 차등화한다(위 _SQUAD_MIN/_SQUAD_MAX는 다른
# 곳에서 계속 쓰이므로 그대로 둠 — 이번 변경 범위 밖). SS는 신민용이
# 표에 안 넣었지만 S와 같은 최상위 구간이라 S와 동일하게 둔다.
_SQUAD_SIZE_BY_GRADE = {
    "SS": (25, 28), "S": (25, 28), "A": (24, 27), "B": (23, 26),
    "C": (22, 25), "D": (22, 25), "E": (22, 25), "F": (22, 25),
}


def _is_fixed26(grade, tier) -> bool:
    """SS/S(1·2부)·A(1부) "26명 고정" 대상 등급·부수인지. [2026-09 신설,
    _squad_min_max에서 분리] 강팀 오퍼 빈자리 로또(_roll_offer_vacancy_teams/
    _fill_offer_vacancies)와 game_engine.generate_offers의 오퍼 후보 제외
    판정도 정확히 같은 기준이 필요해져서 조건 자체를 별도 함수로 뺐다 —
    이제 _squad_min_max도 이 함수를 그대로 쓴다."""
    return tier is not None and (
        (tier == 1 and grade in ("A", "S", "SS")) or (tier == 2 and grade in ("S", "SS")))


def _squad_min_max(grade, tier=None):
    """등급별 (최소, 최대) 로스터 인원 — 표에 없는 등급은 기존 전세계
    공통값(_SQUAD_MIN, _SQUAD_MAX)으로 안전하게 폴백한다.

    [2026-09 리팩터] "A급 이상 1부(+S/SS는 2부까지) 26명 고정" 조건이
    원래 _rebalance_squad_sizes 안에 인라인으로만 있었는데, 이제
    database._build_squad_positions(최초 월드 생성)와 game_engine.
    join_team(내가 입단할 때 정원 초과분 방출)도 정확히 같은 기준이
    필요해져서 여기 한 곳으로 모은다 — tier를 안 주면(기존 호출부 호환)
    이 특례 없이 등급 표만 본다."""
    if _is_fixed26(grade, tier):
        return (26, 26)
    return _SQUAD_SIZE_BY_GRADE.get(grade, (_SQUAD_MIN, _SQUAD_MAX))


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
    # [2026-09 버그수정, 정적감사+실측: "여기 IN 절만 청크가 없다"] 이 함수는
    # 팀 하나가 아니라 _rebalance_squad_sizes / apply_squad_turnover_after_
    # movement가 전 세계에서 모은 삭제 대상을 "한 번에" 받는다 — 6시즌 실측
    # 결과 한 호출에 7,293명이 들어왔다. 그런데 바인딩 변수를 len(ids)만큼
    # 그대로 펼치고 있어서, SQLITE_MAX_VARIABLE_NUMBER가 999인 빌드(SQLite
    # 3.32 미만)에서는 "too many SQL variables"로 이 함수 전체가 실패한다.
    # 그러면 아카이브 없이 DELETE만 실행돼, 바로 이 함수가 막으려고 만들어진
    # 버그(이름 지어준 선수가 조회 화면에서 "(공석)"으로 증발)가 그대로
    # 재발한다. 프로젝트 관례대로 500개씩 끊는다.
    #
    # [동일성] ai_players.id는 INTEGER PRIMARY KEY(=rowid)라 기존 단일
    # IN 조회는 항상 id 오름차순으로 돌아왔다 — 청크를 id 오름차순으로
    # 돌고 그 순서대로 이어 붙이면 rows 순서가 기존과 정확히 같다.
    _CHUNK = 500
    _uniq_ids = sorted(set(ids))
    rows = []
    for _i in range(0, len(_uniq_ids), _CHUNK):
        _part = _uniq_ids[_i:_i + _CHUNK]
        placeholders = ",".join("?" * len(_part))
        rows.extend(c.execute(
            f"""SELECT id, name, position, ovr, age, nationality, team_id
                FROM ai_players WHERE id IN ({placeholders})""", _part).fetchall())
    if not rows:
        return
    team_ids = sorted({r["team_id"] for r in rows if r["team_id"]})
    team_names = {}
    for _i in range(0, len(team_ids), _CHUNK):
        _part = team_ids[_i:_i + _CHUNK]
        tph = ",".join("?" * len(_part))
        team_names.update({r["id"]: r["name"] for r in c.execute(
            f"SELECT id, name FROM teams WHERE id IN ({tph})", _part).fetchall()})
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
    # [2026-10] 최고 OVR·병역 상태도 같이(호출부가 DELETE 직전에 부르므로 행이 아직 있음).
    from database import fill_retired_extra_fields
    fill_retired_extra_fields(c, [a[0] for a in archive_rows])


def _gen_topup_rows(c, tid, tier, cname, continent, tname, grade, need,
                     roster_by_team, name_cache, year, is_override,
                     foreign_ct0=0, src_tag="squad_topup"):
    """[2026-09 신설, _rebalance_squad_sizes에서 분리] 특정 팀에 유망주
    `need`명을 새로 만들어 ai_players INSERT용 row 튜플 리스트로 돌려준다
    — 로직은 원래 _rebalance_squad_sizes의 "n < _lo_size" 분기와 완전히
    동일(포지션 결핍 우선 채움, Prestige×등급 하한, 월드클래스/엘리트
    확률 등). game_engine.join_team 쪽 강팀 오퍼 빈자리를 4주차에 강제로
    채우는 _fill_offer_vacancies도 이 팀당 1명짜리 보충과 완전히 같은
    방식이어야 해서(안 그러면 "연말 정기 보충"과 "빈자리 마감 보충"의
    선수 질/포지션 분포가 미묘하게 달라짐) 공용 함수로 뺐다."""
    from constants import get_ovr_range, CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, SUB_ROLES
    from data.prestige_clubs import prestige_level as _rebal_prestige_level
    from formation_logic import compute_slot_deficiencies
    from database import _pick_nationality, get_foreign_quota_range, compute_ai_growth_cap, roll_potential_ovr

    rows = []
    ovr_rng = get_ovr_range(grade, tier, cname)
    bonus = round(CONTINENT_OVR_BONUS.get(continent, 0) + COUNTRY_OVR_ADJ.get(cname, 0))
    if ovr_rng:
        lo, hi = ovr_rng
        if not is_override:
            lo, hi = lo + bonus, hi + bonus
    else:
        lo, hi = 40, 55
    _plvl = _rebal_prestige_level(cname, tname)
    used = set()
    _q_lo, quota = get_foreign_quota_range(cname, continent, tier=tier)
    # [2026-09 버그수정, 신민용 리포트: "5명 한계인데 8명으로 뚫었잖아"]
    # 예전엔 0에서 시작했다 — 이 함수는 "인원이 부족한 팀에 몇 명 더
    # 얹는" 보충 경로라 그 팀엔 이미 외국인이 있는데, 카운터가 0이면
    # _pick_nationality가 쿼터(5명)를 처음부터 다시 다 써버린다. 호출부가
    # 그 팀의 현재 외국인 수(진짜 국적 기준)를 foreign_ct0로 넘긴다.
    foreign_ct = max(0, int(foreign_ct0 or 0))
    _topup_growth_cap = compute_ai_growth_cap(grade, tier, cname, continent)
    _pos_queue = []
    for _def_pos, _def_n in compute_slot_deficiencies(
            [p for _pid, p, _povr in roster_by_team.get(tid, [])]):
        _pos_queue.extend([_def_pos] * _def_n)
    _pos_queue = _pos_queue[:need]
    for _i in range(need):
        pos = _pos_queue[_i] if _i < len(_pos_queue) else roll_bench_position()
        target = random.randint(lo, max(lo, (lo + hi) // 2))
        age = random.randint(*_AI_NEWBIE_AGE)
        _scaled = _youth_target_scale(target, age)
        if ovr_rng:
            _prestige_base = {3: 1, 2: 2, 1: 3}.get(_plvl, 4)
            _grade_adj = {"SS": 0, "S": 0, "A": 0, "B": 1, "C": 1,
                         "D": 2, "E": 2, "F": 3}.get(grade, 2)
            _young_floor_off = _prestige_base + _grade_adj
            _scaled = max(_scaled, ovr_rng[0] - _young_floor_off)
        stats = _gen_stats(pos, _scaled)
        ovr = calc_ovr(pos, stats)
        # [2026-09 신설] 5대리그 1부 절대 최저선 — database._squad_ovr_hard_min
        # 주석 참고. 신인 생성 경로가 네 군데(_retire_and_replace / 이 topup /
        # _rebalance_squad_sizes / 승강 직후 물갈이)라, 실측에서 두 곳만
        # 막았을 때 5대리그 1부 하한 미달이 153명 남았다 — 전부 덮는다.
        _lhm3, _phm3 = _squad_ovr_hard_min(cname, tier, tname)
        if _lhm3 is not None:
            _af3 = (_scaled / target) if target else 1.0
            stats, ovr = _lift_stats_to_ovr(
                pos, stats, ovr, max(_lhm3, (_phm3 or 0) * _af3))
        sub_role = random.choice(SUB_ROLES.get(pos, ["기본"]))
        # [2026-09] database._nat_ceiling_penalty 정의부 주석 참고.
        nat, foreign_ct = _pick_nationality(cname, continent, grade, pos,
                                            False, foreign_ct, quota, slot_ovr=target, youth=(tier or 1) <= YOUTH_PATH_MAX_TIER)
        name = ""      # [2026-09] AI 실명 폐지
        _p_world, _p_elite = _prestige_star_prob(grade, _plvl)
        # [2026-09] 스쿼드 보충은 벤치 자리라 context='bench'.
        from database import split_star_prob_by_position as _split_star
        _p_world, _p_elite = _split_star(_p_world, _p_elite, pos, "bench")
        _star_roll = random.random()
        if _star_roll < _p_world:
            _topup_kind = "worldclass"
        elif _star_roll < _p_world + _p_elite:
            _topup_kind = "elite"
        else:
            _topup_kind = None
        rows.append((tid, name, pos,
            stats["stamina"], stats["speed"], stats["jump"], stats["strength"],
            stats["shooting"], stats["passing"], stats["dribbling"],
            stats["tackling"], stats["heading"], stats["positioning"],
            stats["setpiece"], stats["mental"], stats["confidence"],
            stats["leadership"], stats["concentration"], ovr, age, sub_role,
            nat, nat,
            year + random.randint(2, 4), 0, year,
            # [2026-09] 위 _retire_and_replace와 동일 — 자기 목표 바닥.
            max(ovr, int(round(target)), roll_potential_ovr(_topup_growth_cap, _topup_kind)),
            # [2026-09 신설] 생성 시점에 연봉을 매긴다 — 예전엔 이 경로로
            # 태어난 선수가 salary=0인 채로 남아, 이적하기 전까지 연봉이
            # 없었다(database._seed_salary 주석 참고). 이적/은퇴대체가
            # 이미 쓰는 _calc_ai_salary를 그대로 써서 산식을 통일한다.
            _calc_ai_salary(grade, tier, ovr, cname, tname, tid, year),
            # [2026-09 신설] 호출자가 지정한 출처 태그 — 이 함수는 "연말 정기
            # 보충"과 "오퍼 빈자리 마감 보충" 두 경로가 공유하므로, 사유는
            # 함수가 아니라 호출자가 안다(database.CREATION_SOURCES 참고).
            src_tag))
    return rows


def _roll_offer_vacancy_teams(c, year) -> set:
    """[2026-09 신설, 신민용+GPT 협업 확정: "강팀 오퍼가 너무 잦다"] 매
    시즌 전환 시점에 한 번, SS/S(1·2부)·A(1부) 26명 고정 팀 중 "이번
    시즌 오퍼 후보가 될 수 있는(=1자리 빈) 팀"을 리그별로 골라 team_id
    집합으로 돌려준다. 이 함수는 팀을 실제로 25명으로 만들지 않는다 —
    호출부(_rebalance_squad_sizes)가 이 집합에 속한 팀만 정원(_lo_size/
    _hi_size)을 26→25로 낮춰서, "부족분 채움"이 자연스럽게 25에서
    멈추게 만든다.

    리그마다: (1) 이번 시즌 빈자리 비율을 OFFER_VACANCY_LEAGUE_PCT_RANGE
    에서 랜덤으로 하나 뽑아 목표 개수를 정하고, (2) 팀을 상위/중위/하위
    3그룹으로 나눠(상위=명문팀 OR 전 시즌 그 리그 상위 OFFER_VACANCY_
    TOP_PCT, 나머지 중 앞쪽 OFFER_VACANCY_MID_PCT=중위, 그 다음=하위)
    OFFER_VACANCY_RANK_WEIGHT 가중치로 목표 개수만큼 중복없이 추첨한다.
    전 시즌 순위 기록이 없는 리그(신규 게임 첫 시즌 등)는 club_strength
    내림차순으로 대신 랭킹을 매긴다."""
    from constants import (get_country_league_grade, OFFER_VACANCY_LEAGUE_PCT_RANGE,
                            OFFER_VACANCY_RANK_WEIGHT, OFFER_VACANCY_TOP_PCT,
                            OFFER_VACANCY_MID_PCT)
    from data.prestige_clubs import is_prestige as _is_prestige_club

    team_rows = c.execute(
        """SELECT t.id AS tid, t.name AS tname, t.league_id AS lid, t.current_tier AS tier,
                  t.club_strength AS cs, cn.name AS cname
           FROM teams t JOIN leagues l ON t.league_id=l.id
                        JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_rows = _drop_mil_teams(c, team_rows, "tid")   # [2026-10] 군팀 제외

    by_league: dict = {}
    for r in team_rows:
        grade = get_country_league_grade(r["cname"])
        if not _is_fixed26(grade, r["tier"]):
            continue
        by_league.setdefault(r["lid"], []).append(r)
    if not by_league:
        return set()

    # 전 시즌(=이 함수를 부르는 시점의 year, 방금 끝난 시즌) 리그별 순위 —
    # update_club_strength_after_season과 동일한 승점/득실차 기준.
    standings_rows = c.execute(
        """SELECT league_id, team_id, wins, draws, losses, goals_for, goals_against
           FROM league_season_standings WHERE year=?""", (year,)).fetchall()
    standings_by_league: dict = {}
    for r in standings_rows:
        standings_by_league.setdefault(r["league_id"], []).append(r)

    chosen: set = set()
    for lid, teams in by_league.items():
        n_teams = len(teams)
        if n_teams < 2:
            continue
        _rows = standings_by_league.get(lid)
        if _rows:
            def _key(r):
                pts = r["wins"] * 3 + r["draws"]
                gd = r["goals_for"] - r["goals_against"]
                return (-pts, -gd)
            ranked_ids = [r["team_id"] for r in sorted(_rows, key=_key)]
        else:
            # 전 시즌 기록이 없는 리그(신규 게임 등) — club_strength로 대체.
            ranked_ids = [r["tid"] for r in sorted(teams, key=lambda r: -(r["cs"] or 0.0))]
        rank_of = {tid: i for i, tid in enumerate(ranked_ids)}  # 0=1위

        top_cut = max(1, round(n_teams * OFFER_VACANCY_TOP_PCT))
        mid_cut = top_cut + max(0, round(n_teams * OFFER_VACANCY_MID_PCT))

        weights = []
        pool = []
        for r in teams:
            pool.append(r["tid"])
            rk = rank_of.get(r["tid"], n_teams - 1)
            if _is_prestige_club(r["cname"], r["tier"], r["tname"]) or rk < top_cut:
                bucket = "top"
            elif rk < mid_cut:
                bucket = "mid"
            else:
                bucket = "bottom"
            weights.append(OFFER_VACANCY_RANK_WEIGHT[bucket])

        pct = random.uniform(*OFFER_VACANCY_LEAGUE_PCT_RANGE)
        target_n = round(n_teams * pct)
        if target_n <= 0:
            continue
        target_n = min(target_n, n_teams)

        _pool = list(pool)
        _weights = list(weights)
        for _ in range(target_n):
            if not _pool or sum(_weights) <= 0:
                break
            pick = random.choices(_pool, _weights, k=1)[0]
            idx = _pool.index(pick)
            _pool.pop(idx)
            _weights.pop(idx)
            chosen.add(pick)
    return chosen


def _fill_offer_vacancies(year):
    """[2026-09 신설] 4주차(FIRST_HALF_START, 프리시즌 오퍼 구간이 끝나고
    정규시즌이 시작되는 시점) 진입 시 game_engine에서 호출. _roll_offer_
    vacancy_teams가 만들어둔 "강팀 오퍼용 빈자리"가 이 시점까지도 안
    채워졌으면(=플레이어가 그 팀에 안 들어갔으면) AI 유망주 1명으로
    강제 보충해서 26명을 맞춘다 — 자리가 시즌 내내 방치되면 그 팀
    스쿼드가 실제로 얇은 채로 남기 때문에, 오퍼 구간(1~3주차)이 끝나는
    시점을 마감 기한으로 못박는다. 반환: 새로 채워진 팀 수."""
    from constants import get_country_league_grade, COUNTRY_LEAGUE_OVR_OVERRIDE
    conn = get_conn()
    c = conn.cursor()
    team_rows = c.execute(
        """SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                  cn.name AS cname, cn.continent AS continent
           FROM teams t JOIN leagues l ON t.league_id=l.id
                        JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_rows = _drop_mil_teams(c, team_rows, "tid")   # [2026-10] 군팀 제외
    counts: dict = {}
    for r in c.execute("SELECT team_id, COUNT(*) n FROM ai_players GROUP BY team_id").fetchall():
        counts[r["team_id"]] = r["n"]
    roster_by_team: dict = {}
    for r in c.execute("SELECT id, team_id, position, ovr FROM ai_players").fetchall():
        roster_by_team.setdefault(r["team_id"], []).append((r["id"], r["position"], r["ovr"]))
    # [2026-09 신설, 신민용 리포트: "외국인이 팀에 10명 넘게 있을 때도
    # 있다"] 아래 인원 보충 루프가 _foreign_ct를 0에서 시작해서, 그 팀이
    # 이미 외국인을 몇 명 데리고 있든 무시하고 쿼터만큼 또 외국인을
    # 만들어 넣고 있었다(매 시즌 반복) — 이적시장 쪽 예방 필터를 고쳐도
    # 이 경로로 계속 새 외국인이 생겨서 초과 팀 수가 안 줄었다. 팀별
    # 현재 외국인 수(진짜 국적 기준)를 한 번에 세어 시작값으로 쓴다.
    foreign_now: dict = {}
    for r in c.execute(
            """SELECT ap.team_id AS tid, COUNT(*) AS n FROM ai_players ap
               JOIN teams t ON ap.team_id = t.id
               JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id
               WHERE ap.nationality != '' AND ap.nationality != cn.name
               GROUP BY ap.team_id""").fetchall():
        foreign_now[r["tid"]] = r["n"]
    name_cache = _build_name_cache(c)

    new_rows = []
    filled = 0
    for r in team_rows:
        grade = get_country_league_grade(r["cname"])
        if not _is_fixed26(grade, r["tier"]):
            continue
        _, ceiling = _squad_min_max(grade, r["tier"])
        n = counts.get(r["tid"], 0)
        if n >= ceiling:
            continue
        need = ceiling - n
        new_rows.extend(_gen_topup_rows(c, r["tid"], r["tier"], r["cname"], r["continent"],
                                         r["tname"], grade, need, roster_by_team, name_cache,
                                         year, r["cname"] in COUNTRY_LEAGUE_OVR_OVERRIDE,
                                         foreign_ct0=foreign_now.get(r["tid"], 0),
                                         src_tag="offer_vacancy"))
        filled += 1

    if new_rows:
        c.executemany("""INSERT INTO ai_players
            (team_id,name,position,stamina,speed,jump,strength,shooting,passing,
             dribbling,tackling,heading,positioning,setpiece,
             mental,confidence,leadership,concentration,ovr,age,sub_role,nationality,
             true_nationality,contract_end_year,last_transfer_year,created_year,potential_ovr,
             salary,creation_source)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new_rows)
        conn.commit()
    # [2026-09 신설] 여기서 새로 만든 선수의 주발도 같은 함수로 채운다
    # (run_ai_offseason과 동일한 이유 — 위 주석 참고).
    if new_rows:
        try:
            from database import assign_missing_feet
            assign_missing_feet()
        except Exception as _e:
            print(f"[FOOT] 주발 배정 실패(계속 진행): {_e}")
    conn.close()
    return filled


def _rebalance_squad_sizes(c, year):
    """[2026-08 신설, 신민용 리포트: "이적으로 인한 스쿼드 인원 불균형을
    보정하는 장치가 없다"] 은퇴 교체(_retire_and_replace)는 기존 행을
    그대로 재활용(UPDATE)할 뿐이라 팀별 인원수를 안 바꾼다 — 이적
    (_transfer_market)이 어느 팀엔 계속 순유입, 다른 팀엔 계속 순유출을
    만들면 그 격차가 시즌이 갈수록 그대로 누적된다. 매 시즌 이적 직후
    한 번, 전 세계 팀을 훑어 등급별 정상범위(_squad_min_max, 아래 참고)
    대비 너무 적거나 많은 팀만 되돌린다:
      - 부족(< 그 등급의 최소): 그 팀 리그 등급/tier에 맞는 OVR 범위에서
        10대(16~19세) 유망주를 새로 영입(INSERT)해 채운다.
      - 과다(> 그 등급의 최대): 자리를 못 구한(=OVR이 가장 낮은) 선수부터
        조기 은퇴 처리한다 — 신인 교체 없이 그냥 명단에서 빠진다
        (신민용 지적대로, 모든 선수가 30대까지 뛰는 게 아니라 20대에
        일찌감치 접는 선수도 실제로 있다는 점을 반영).

    [2026-09 신설, 신민용+GPT 협업: "DF가 부족하다고 LB만 계속 영입하면
    안 된다 — CB/LB/RB 각각 실제로 부족한 포지션만 봐야 한다"] 부족분을
    채울 때 예전엔 roll_bench_position()(그룹만 가중치, 그 안 구체
    포지션은 완전 무작위)을 그대로 need번 굴렸다 — 팀이 이미 LB만
    여러 명이고 RB가 0명이어도 전혀 신경 안 쓰고 계속 굴렸으므로,
    시즌을 거듭할수록 그런 편중이 실제로 쌓일 수 있었다. 이제 먼저
    formation_logic.compute_slot_deficiencies로 그 팀 현재 로스터
    (곧 은퇴할 선수 제외 없이 이 함수 진입 시점 그대로) 기준 진짜
    부족한 구체 포지션(_SLOT_TARGETS 대비, POSITION_COMPAT로 대체
    가능한 선수는 이미 채운 것으로 인정)만 우선 채우고, 그래도 need가
    남으면(필수 슬롯은 이미 다 찼는데 총원 자체가 등급 최소치보다
    적은 경우) 기존 roll_bench_position()으로 나머지를 채운다.
    반환: (topped_up, forced_out) — 영입/조기은퇴된 인원수."""
    from constants import (CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, SUB_ROLES,
                           get_country_league_grade, get_ovr_range, COUNTRY_LEAGUE_OVR_OVERRIDE)
    from database import _pick_nationality, get_foreign_quota_range, compute_ai_growth_cap, roll_potential_ovr
    from data.prestige_clubs import prestige_level as _rebal_prestige_level
    from database import _BENCH_GROUP_WEIGHTS, _BENCH_GROUP_POOLS
    from formation_logic import (_pos_category, compute_slot_deficiencies,
                                 _SLOT_TARGET_MAP)
    _GROUP_KEY = {"GK": "GK", "DEF": "DF", "MID": "MF", "ATK": "FW"}

    team_rows = c.execute(
        """SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                  cn.name AS cname, cn.continent AS continent
           FROM teams t JOIN leagues l ON t.league_id=l.id
                        JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_rows = _drop_mil_teams(c, team_rows, "tid")   # [2026-10] 군팀 제외
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
    # [2026-09 버그수정, 신민용 리포트: "5명 한계인데 8명으로 뚫었잖아"]
    # 팀별 현재 외국인 수(진짜 국적 기준) — 아래 인원 보충(_gen_topup_rows)
    # 과 포지션 교체 생성이 이 값에서 이어 세도록 넘긴다. 0에서 시작하면
    # 그 팀에 이미 외국인이 있어도 쿼터를 처음부터 다시 다 써버린다.
    # (_fill_offer_vacancies의 같은 이름 주석 참고 — 그쪽과 완전히 같은 쿼리)
    foreign_now: dict = {}
    for r in c.execute(
            """SELECT ap.team_id AS tid, COUNT(*) AS n FROM ai_players ap
               JOIN teams t ON ap.team_id = t.id
               JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id
               WHERE ap.nationality != '' AND ap.nationality != cn.name
               GROUP BY ap.team_id""").fetchall():
        foreign_now[r["tid"]] = r["n"]

    name_cache = _build_name_cache(c)
    topped_up = 0
    forced_out = 0
    new_rows = []       # INSERT용
    delete_ids = []     # DELETE용

    # [2026-09 신설, 신민용 지적: "내가 입단할 때 자리가 없으면 27명이
    # 되는 거 아니냐 — 원래 있던 애는 어떻게 되는 건데"] 정확한 지적이라
    # 진짜 해결책(입단 그 순간 정원 초과분을 즉시 방출)은 game_engine.
    # join_team 쪽에 새로 넣었다(아래쪽 참고) — 그게 실행되면 이 함수가
    # 다시 돌 때쯤엔 이미 AI 카운트가 "등급별 목표 - 1"로 맞춰져 있어
    # 여기선 사실상 손댈 일이 없다. 이 아래 블록은 그 즉시-방출 이전에
    # 이미 입단했던(구버전 세이브 등) 경우를 위한 안전망으로만 남긴다 —
    # 아래 counts는 ai_players만 세므로, 이 안전망이 없으면 내가 있는
    # 팀도 "AI만 정확히 목표치"로 보여 아무 조치가 없다.
    _my_team_id = None
    _me_row = c.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
    if _me_row and _me_row["current_team_id"]:
        _my_team_id = _me_row["current_team_id"]

    # [2026-09 신설, 신민용+GPT 협업 확정: "강팀 오퍼가 너무 잦다"] 이번
    # 시즌 "강팀 오퍼 빈자리" 로또 결과 — 뽑힌 팀만 정원을 26→25로 낮춰서
    # 부족분 보충이 25에서 멈추게 한다(자세한 원리는 _roll_offer_vacancy_
    # teams 참고). 4주차가 되면 game_engine이 _fill_offer_vacancies를 불러
    # 이 시점까지 안 채워진 자리를 강제로 26까지 채운다.
    _vacancy_team_ids = _roll_offer_vacancy_teams(c, year)

    for tid, (tier, cname, continent, tname) in team_info.items():
        n = counts.get(tid, 0)
        grade = get_country_league_grade(cname)
        bonus = round(CONTINENT_OVR_BONUS.get(continent, 0) + COUNTRY_OVR_ADJ.get(cname, 0))
        is_override = cname in COUNTRY_LEAGUE_OVR_OVERRIDE
        # [2026-09 신설, 신민용 요청: "A급 이상 1부 리그 팀은 26명을
        # 맞춰서 가지고 있어야 한다 — 이적 과정에서 후보가 13~14명까지
        # 남기도 하는데 그만큼 영입을 해야지"] 등급별 범위(_SQUAD_SIZE_
        # BY_GRADE)는 A등급이 24~27이라, 24~25명이어도 "정상"으로 보고
        # 이 함수가 아무 조치를 안 했다 — 범위가 아니라 26명 고정으로
        # 못박는다(모자라면 즉시 영입, 넘치면 조기은퇴로 26을 맞춤).
        # [2026-09 확장, 신민용 요청: "S급 SS급은 2부도 포함해줘 — 3부로
        # 가면 26이 될 수도 있지만 26 미만으로 내려가도 괜찮다"] A등급은
        # 1부만 고정 대상이고, S/SS등급은 2부까지 고정 대상에 포함한다
        # (최상위 리그는 2부도 스쿼드가 두꺼운 게 자연스러우므로). 3부
        # 이하는 등급 불문 기존 범위 로직 그대로 둬서 자연스럽게 26
        # 미만으로 내려갈 수 있게 둔다. [2026-09 리팩터] 이 조건은 이제
        # _squad_min_max(grade, tier)에 그대로 옮겨졌다 — game_engine.
        # join_team(내가 입단하는 순간 정원 초과분을 바로 방출)도 같은
        # 기준을 써야 해서 한 곳으로 모았다(아래 참고).
        _lo_size, _hi_size = _squad_min_max(grade, tier)
        if tid == _my_team_id:
            _lo_size -= 1
            _hi_size -= 1
        if tid in _vacancy_team_ids:
            _lo_size -= 1
            _hi_size -= 1

        if n < _lo_size:
            need = _lo_size - n
            new_rows.extend(_gen_topup_rows(c, tid, tier, cname, continent, tname, grade,
                                             need, roster_by_team, name_cache, year,
                                             is_override,
                                             foreign_ct0=foreign_now.get(tid, 0)))
            topped_up += need

        elif n > _hi_size:
            excess = n - _hi_size
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
                    # [2026-09] 세부 포지션 스왑(아래)에서 "넘치는 그룹"이
                    # 아니라 "넘치는 구체 포지션"의 최저 OVR을 골라야 하므로
                    # 포지션까지 같이 들고 있는다.
                    group_players[grp].append((pid, povr, ppos))
                total_n = len(roster)
                deficient, surplus = [], []
                for grp, w in _BENCH_GROUP_WEIGHTS:
                    expected = total_n * (w / 100.0)
                    actual = len(group_players[grp])
                    if actual == 0 or actual < expected * 0.4:
                        deficient.append((grp, None))
                    elif actual > max(expected * 2.2, expected + 3):
                        surplus.append((grp, actual - expected, None))

                # [2026-09 신설, 신민용 리포트: "LB/RB 0명 팀이 왜 생기냐"]
                # 위 판정은 그룹(GK/DF/MF/FW) 단위라, DF가 CB 5명 + LB 0명 +
                # RB 0명이어도 DF 숫자 자체는 정상이라 영영 발동하지 않았다.
                # 실측(7시즌 월드): 좌우 슬롯에 최대 미스매치 선수가 서는
                # 팀 330개 중 159개가 compute_slot_deficiencies로는 부족이
                # 정확히 잡히는데도 보충 경로가 아예 안 열렸다 — 보충
                # (n < _lo_size)과 방출(n > _hi_size)은 총원 기준이고,
                # 330팀 전부 총원은 정상범위였다(0/330).
                # 그룹 단위로 잡히는 게 없을 때만, 같은 1:1 스왑을 구체
                # 포지션 기준으로 한 번 더 본다(그룹 문제가 더 큰 문제라
                # 그쪽이 있으면 그쪽을 먼저 처리한다).
                if not deficient:
                    _pos_cnt: dict = {}
                    for _pid, _ppos, _povr in roster:
                        _pos_cnt[_ppos] = _pos_cnt.get(_ppos, 0) + 1
                    _defs = compute_slot_deficiencies([r[1] for r in roster])
                    if _defs:
                        _need_pos = _defs[0][0]
                        # 넘치는 구체 포지션: _SLOT_TARGETS 목표 대비 초과분이
                        # 가장 큰 자리(GK와 부족 포지션 자신은 제외). 초과가
                        # 2명 이상일 때만 건드려 정상 편차는 그대로 둔다.
                        _best, _best_ex = None, 1
                        for _p, _n in _pos_cnt.items():
                            if _p == "GK" or _p == _need_pos:
                                continue
                            _ex = _n - _SLOT_TARGET_MAP.get(_p, 1)
                            if _ex > _best_ex:
                                _best, _best_ex = _p, _ex
                        if _best:
                            deficient.append((_GROUP_KEY.get(_pos_category(_need_pos), "MF"),
                                              _need_pos))
                            surplus.append((_GROUP_KEY.get(_pos_category(_best), "MF"),
                                            _best_ex, _best))
                # [2026-09 신설, 신민용 리포트 "GK 0명 팀" 계측 후속]
                # 위 스왑은 deficient와 surplus가 '둘 다' 있어야 발동한다 —
                # surplus 기준(기대치의 2.2배 초과 또는 +3명 초과)이 엄격해서,
                # "GK 0명인데 어느 그룹도 그만큼 과다하지는 않은" 팀은 아무
                # 보정도 못 받고 그대로 남았다(실측: 임대복귀로 생긴 GK 0명
                # 22팀 중 6팀이 이 함수를 통과해도 안 고쳐졌다).
                # GK 0명은 "통계적 편차"가 아니라 경기가 성립하지 않는
                # 데이터 오류라, 이 경우만은 surplus 기준을 무시하고 가장
                # 인원이 많은 그룹(2명 이상)에서 한 자리를 떼어 무조건
                # 채운다. 총원은 그대로 유지된다(1:1 스왑).
                if not surplus:
                    _zero_grps = [g for g, _x in deficient if not group_players.get(g)]
                    if _zero_grps:
                        # 기부할 그룹은 "인원이 가장 많고, 그 안에 2명 이상인
                        # 구체 포지션이 실제로 있는" 그룹 — 아래 스왑이 마지막
                        # CB 같은 선수를 빼지 못하게 필터링하므로, 그 필터를
                        # 통과할 후보가 없는 그룹을 고르면 보정이 그냥 무산된다.
                        def _donatable(g):
                            _pc: dict = {}
                            for _t in group_players.get(g, []):
                                _pc[_t[2]] = _pc.get(_t[2], 0) + 1
                            return any(v > 1 for v in _pc.values())
                        _don = max((g for g in ("GK", "DF", "MF", "FW")
                                    if g not in _zero_grps
                                    and len(group_players.get(g, [])) >= 2
                                    and _donatable(g)),
                                   key=lambda g: len(group_players[g]), default=None)
                        if _don:
                            deficient = [(g, x) for g, x in deficient if g in _zero_grps]
                            surplus = [(_don, 99, None)]
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
                    _q_lo, _quota = get_foreign_quota_range(cname, continent, tier=tier)
                    # [2026-09 버그수정, 위 foreign_now 주석 참고] 0이 아니라
                    # 그 팀의 실제 현재 외국인 수에서 시작한다.
                    _foreign_ct = foreign_now.get(tid, 0)
                    for si, (grp, _need_exact) in enumerate(deficient):
                        if si >= len(surplus):
                            break
                        sgrp, _, _sur_exact = surplus[si]
                        # 구체 포지션 모드면 그 포지션 안에서만 최저 OVR을
                        # 고른다(그룹 전체에서 고르면 엉뚱한 자리가 빠진다).
                        _pool_for_cut = ([t for t in group_players[sgrp] if t[2] == _sur_exact]
                                         if _sur_exact else group_players[sgrp])
                        # [2026-09 신설, 신민용 리포트 "GK 0명 팀" 계측 후속]
                        # 스왑으로 빼는 쪽도 "마지막 GK/마지막 CB" 불변식을
                        # 지켜야 한다. 그룹 모드(_sur_exact=None)에서는 그룹
                        # 전체에서 최저 OVR을 골랐기 때문에, 예를 들어 DF
                        # 그룹이 CB 1명 + LB/RB 여러 명일 때 그 유일한 CB가
                        # 최저 OVR이면 그대로 빠져 CB 0명이 됐다(실측: 3시즌
                        # 후 CB 0명 팀 4개 — GK 0명을 고친 뒤에도 남아 있었다).
                        # 정렬 기준(최저 OVR 우선)은 그대로 두고 후보에서만
                        # 그 포지션의 마지막 1명을 제외한다 — 이적시장·강제
                        # 조기은퇴 가드와 완전히 같은 원칙. 후보가 통째로
                        # 비어버리면(그 그룹 전원이 각자 유일한 포지션) 스왑
                        # 자체를 건너뛴다.
                        _pos_cnt_swap: dict = {}
                        for _t in group_players[sgrp]:
                            _pos_cnt_swap[_t[2]] = _pos_cnt_swap.get(_t[2], 0) + 1
                        _pool_for_cut = [t for t in _pool_for_cut
                                         if _pos_cnt_swap.get(t[2], 0) > 1]
                        if not _pool_for_cut:
                            continue
                        weakest = min(_pool_for_cut, key=lambda t: t[1])
                        delete_ids.append(weakest[0])
                        group_players[sgrp].remove(weakest)
                        _pos = _need_exact or random.choice(_BENCH_GROUP_POOLS[grp])
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
                        # [2026-09 신설] 위 _retire_and_replace의 같은 보정과
                        # 동일 — 이 경로(스쿼드 인원 보충)도 5대리그 1부
                        # 절대 최저선을 지킨다.
                        _lhm2, _phm2 = _squad_ovr_hard_min(cname, tier, tname)
                        if _lhm2 is not None:
                            _af2 = (_scaled / _target) if _target else 1.0
                            _stats, _ovr = _lift_stats_to_ovr(
                                _pos, _stats, _ovr, max(_lhm2, (_phm2 or 0) * _af2))
                        _sub_role = random.choice(SUB_ROLES.get(_pos, ["기본"]))
                        # [2026-09] database._nat_ceiling_penalty 참고.
                        _nat, _foreign_ct = _pick_nationality(cname, continent, grade, _pos,
                                                              False, _foreign_ct, _quota,
                                                              slot_ovr=_target, youth=(tier or 1) <= YOUTH_PATH_MAX_TIER)
                        _name = ""      # [2026-09] AI 실명 폐지
                        # [2026-09 신설, database.roll_potential_ovr 정의부
                        # 주석 참고] 이 자리도 같은 확률표로 잠재력을 정한다.
                        _swap_growth_cap = compute_ai_growth_cap(grade, tier, cname, continent)
                        _p_world, _p_elite = _prestige_star_prob(grade, _plvl)
                        # [2026-09] 97~99 포지션 가중치.
                        from database import split_star_prob_by_position as _split_star
                        _p_world, _p_elite = _split_star(_p_world, _p_elite, _pos, "starter")
                        _star_roll = random.random()
                        if _star_roll < _p_world:
                            _swap_kind = "worldclass"
                        elif _star_roll < _p_world + _p_elite:
                            _swap_kind = "elite"
                        else:
                            _swap_kind = None
                        new_rows.append((tid, _name, _pos,
                            _stats["stamina"], _stats["speed"], _stats["jump"], _stats["strength"],
                            _stats["shooting"], _stats["passing"], _stats["dribbling"],
                            _stats["tackling"], _stats["heading"], _stats["positioning"],
                            _stats["setpiece"], _stats["mental"], _stats["confidence"],
                            _stats["leadership"], _stats["concentration"], _ovr, _age, _sub_role,
                            _nat, _nat,
                            year + random.randint(2, 4), 0, year,
                            # [2026-09] 위와 동일 — 자기 목표 바닥.
                            max(_ovr, int(round(_target)),
                                roll_potential_ovr(_swap_growth_cap, _swap_kind)),
                            # [2026-09 신설] 위 _gen_topup_rows와 같은 이유 —
                            # 이 경로(포지션 뎁스 보충 직접 생성)도 같은 INSERT를
                            # 쓰므로 반드시 같은 컬럼 수여야 한다.
                            _calc_ai_salary(grade, tier, _ovr, cname, tname, tid, year),
                            # [2026-09 신설] 같은 INSERT를 쓰는 _gen_topup_rows와
                            # 컬럼 수를 맞춘다. 다만 사유는 "최소인원 미달 보충"이
                            # 아니라 "포지션 뎁스 교체"라 태그를 구분한다.
                            "pos_swap"))
                        topped_up += 1
                        forced_out += 1

    if new_rows:
        # [2026-09 신설, database.true_nationality 컬럼 주석 참고] 포지션
        # 뎁스 보충으로 새로 태어나는 선수도 은퇴대체와 동일하게 nationality/
        # true_nationality를 함께 채운다.
        c.executemany("""INSERT INTO ai_players
            (team_id,name,position,stamina,speed,jump,strength,shooting,passing,
             dribbling,tackling,heading,positioning,setpiece,
             mental,confidence,leadership,concentration,ovr,age,sub_role,nationality,
             true_nationality,contract_end_year,last_transfer_year,created_year,potential_ovr,
             salary,creation_source)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new_rows)
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


def _snapshot_season_positions(c, year, only_missing=False, rows=None,
                                preserve_role=False):
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

    # [2026-09 신설, 신민용 확정: "역할은 시즌 시작에 정해지고 그에 맞춰
    # 출전이 나가야 한다 — 지금은 순서가 반대"] preserve_role=True면
    # 그 해 역할이 **이미 정해져 있는** 선수는 그 값을 그대로 유지하고,
    # 슬롯 포지션만 새로 기록한다.
    #
    # 왜 필요한가: 역할은 이제 직전 오프시즌 끝(= 이 시즌 시작 직전,
    # 로스터가 완전히 확정된 시점)에 한 번 정해진다. 그런데 43주차
    # 스냅샷은 "하반기에 실제로 뛴 스쿼드 모양"을 team_season_lineup에
    # 남기려고 여전히 돌아야 하고(game_engine 쪽 주석 참고), 그때
    # 역할까지 다시 계산해버리면 시즌 시작에 정한 값이 덮여서 "역할이
    # 원인"이라는 구조가 그대로 무너진다. 그래서 43주차 호출만
    # preserve_role=True로 부른다 — 겨울 이적으로 새로 들어와 아직
    # 역할이 없는 선수는 여기서 처음 부여받는다.
    _kept_roles = {}
    if preserve_role:
        try:
            from database import history_drain
            history_drain()
            _kept_roles = {
                r[0]: r[1] for r in c.execute(
                    "SELECT player_id, role FROM hist.ai_player_position_history "
                    "WHERE year=? AND role!=''", (year,)).fetchall()}
        except Exception:
            _kept_roles = {}
        # [2026-10 버그수정, 신민용 리포트 6번: "OVR 76인 선수가 선수 검색에선
        # 아틀레티코 '핵심'인데, 팀 검색 그 해 포메이션에선 후보 맨 아래
        # (전력외 자리)에 있다"] 위 설계는 "겨울 이적으로 새로 들어와 아직
        # 역할이 없는 선수"만 여기서 새로 역할을 받는다고 가정했는데, 실제로
        # 겨울 이적자는 시즌 시작 때 원래 팀에서 정한 역할 행이 이미 있어서
        # 그대로 보존됐다 — 예: 약팀에서 핵심이던 선수가 겨울에 아틀레티코로
        # 가면, 하반기 줄(아틀레티코)에 원래 팀 역할 "핵심"이 붙고 포메이션은
        # OVR순 벤치 맨 끝. 10시즌 헤드리스 실측: 벤치에 있는 핵심/주전 겨울
        # 이적자 8,466명 중 8,465명이 상반기(원래 팀) 역할과 똑같았다. 이
        # 역할은 표시만이 아니라 개인상 자격(대기·전력외 제외), 시즌 평점
        # 추정, 국가대표 선발, 임대 후 완전 이적, 오프시즌 이적 판단에도
        # 그대로 쓰이므로, 새 팀에서 실제로 맡은 자리 기준으로 다시 정한다.
        # 상반기 역할은 ai_player_position_history_half(겨울 창구 직전
        # 스냅샷)에 따로 남아 있어 상반기 줄 표시는 그대로다. 겨울에 팀을
        # 안 옮긴 선수는 예전처럼 시즌 시작 역할을 그대로 유지한다.
        try:
            for _r in c.execute(
                    "SELECT player_id FROM ai_transfer_log "
                    "WHERE year=? AND is_mid_season=1 AND from_team_id != to_team_id",
                    (year,)).fetchall():
                _kept_roles.pop(_r[0], None)
        except Exception:
            pass

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
        # [2026-09 신설, 히스토리 비동기 writer] 이 NOT EXISTS 체크는 "이
        # 해 hist.ai_player_position_history에 아직 행이 없는 선수"를
        # 찾는다 — 그런데 그 표의 실제 쓰기가 이제 비동기 큐를 거치므로,
        # 이 시점(같은 시즌의 1차 전체패스가 끝난 지 몇 주 뒤, only_missing
        # 2차 패스)에 워커가 아직 그 1차 패스를 커밋 안 했다면 전원이
        # "없는 것"으로 잘못 잡혀 26만 행이 통째로 다시 계산·저장된다
        # (read-after-write 의존성 — 3+8이 5로 보이는 바로 그 종류의
        # 버그). 여기서 drain해 hist.db가 지금까지 큐에 들어간 내용과
        # 확실히 같은 상태임을 보장한 뒤 읽는다 — 1차 패스와 이 시점
        # 사이엔 보통 최소 한 번의 오토세이브(그 안에서 이미 drain)가
        # 끼어 있어 실제로는 거의 항상 즉시 반환된다.
        from database import history_drain
        history_drain()
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
                      ap.ovr AS ovr, ap.age AS age, ap.salary AS salary,
                      ap.foot AS foot, ap.sub_role AS sub_role,
                      t.formation AS formation
               FROM ai_players ap
               JOIN temp._snap_target_teams st ON st.team_id = ap.team_id
               JOIN teams t ON ap.team_id = t.id""").fetchall()
        c.execute("DROP TABLE IF EXISTS temp._snap_target_teams")
    elif rows is None:
        # [2026-09 성능, 신민용 리포트: "28·43·52주차 딜레이"] NOT INDEXED에
        # 대한 설명은 _snapshot_season_ratings의 같은 쿼리 주석 참고
        # (전세계 로스터 26만 행을 통째로 읽는 쿼리 3개가 전부 동일한 문제).
        rows = c.execute(
            """SELECT ap.id AS id, ap.team_id AS team_id, ap.position AS position,
                      ap.ovr AS ovr, ap.age AS age, ap.salary AS salary,
                      ap.foot AS foot, ap.sub_role AS sub_role,
                      t.formation AS formation
               FROM ai_players ap NOT INDEXED JOIN teams t ON ap.team_id = t.id
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
    # [2026-09 신설] rows를 호출부가 넘겨준 경로엔 salary가 없을 수 있다 —
    # 바로 위 formation 방어와 같은 이유·같은 패턴(없으면 None으로 저장).
    _has_salary = "salary" in rows[0].keys()
    _has_foot = "foot" in rows[0].keys()

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
            "SELECT current_team_id, position, ovr, age, salary, foot, sub_role "
            "FROM my_player WHERE id=1").fetchone()
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
        # [2026-09 신설] 그 해 연봉을 스냅샷에 같이 남긴다 — ai_players.salary는
        # 현재값만 갖고 있어 과거 연도의 연봉을 나중에 복원할 수 없다(은퇴하면
        # ai_players에서 사라지고 ai_players_retired엔 salary 컬럼이 아예 없다).
        # 여기서 보존해야 "2005년 최고 연봉"을 그 당시 값으로 보여줄 수 있다.
        # 호출부가 rows를 넘겨 salary가 없는 경우는 formation 때와 같은 방어
        # 패턴으로 None 처리한다.
        candidates = [{"id": p["id"], "position": p["position"], "ovr": p["ovr"] or 0,
                       "salary": (p["salary"] if _has_salary else None),
                       # [2026-09 신설] 주발 보정(formation_logic._foot_swap_pass)이
                       # 쓰는 두 값. 호출부가 rows를 넘겨 컬럼이 없을 수 있으니
                       # salary와 같은 방어 패턴으로 빈 문자열 처리한다 —
                       # foot=''이면 주발 배율이 1.0이라 기존과 동일하게 동작.
                       "foot": (p["foot"] if _has_foot else ""),
                       "sub_role": (p["sub_role"] if _has_foot else ""),
                       # [2026-09 신설] 역할/뎁스 산정 전용 나이 보정을
                       # 켜는 키(formation_logic.role_age_penalty 주석 참고).
                       # 이 키가 실린 후보에만 보정이 걸린다 — UI 포메이션
                       # 화면·국가대표 소집 등 다른 호출부는 영향 없다.
                       "role_age": p["age"]}
                      for p in players]
        role_pool = [(p["id"], p["position"], p["ovr"], p["age"]) for p in players]
        # [2026-09 버그수정] 내 소속팀이면 나도 로스터의 일원으로 같이
        # 슬롯 배정/역할 산정에 넣는다(위 _me 주석 참고) — 그래야 팀
        # 스쿼드 카드에 내가 뜨고, "내가 주전인데 팀 라인업엔 AI가 그
        # 자리에 있다"는 불일치도 사라진다.
        if _me is not None and _team_id == _me["current_team_id"]:
            candidates.append({"id": _MY_LINEUP_ID, "position": _me["position"],
                               "ovr": _me["ovr"] or 0,
                               "salary": _me["salary"],
                               "foot": (_me["foot"] if "foot" in _me.keys() else ""),
                               "sub_role": (_me["sub_role"] if "sub_role" in _me.keys() else ""),
                               "role_age": _me["age"]})
            role_pool.append((_MY_LINEUP_ID, _me["position"], _me["ovr"], _me["age"]))
        placed = _greedy_fill_slots(candidates, slots)
        started_ids = {pl["id"] for pl in placed if pl is not None}
        # [2026-09 재설계] roles는 이제 실제 슬롯 배정(started_ids)을 그대로
        # "주전" 판정에 쓴다 — formation_logic.compute_squad_roles 주석 참고.
        roles = compute_squad_roles(role_pool, started_ids)
        for slot_idx, pl in enumerate(placed):
            if pl is None:
                continue
            if pl["id"] == _MY_LINEUP_ID:
                continue   # 내 포지션/역할은 my_player_position_history 담당
            inserts.append((pl["id"], year, slots[slot_idx],
                            _kept_roles.get(pl["id"]) or roles.get(pl["id"], "")))
        for p in players:
            if p["id"] not in started_ids:
                inserts.append((p["id"], year, p["position"] or "",
                                _kept_roles.get(p["id"]) or roles.get(p["id"], "")))
        slots_payload = [{"slot": slots[i], "id": (pl["id"] if pl else None),
                          "salary": (pl.get("salary") if pl else None)}
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
        bench_payload = [{"id": p["id"], "position": p["position"] or "",
                          "salary": p.get("salary")}
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

    # [2026-09 신설, 히스토리 비동기 writer] 이전엔 여기서 c.executemany로
    # hist에 즉시 커밋했다 — 이제 큐에 넘기기만 한다(database.py 상단
    # 설계 불변식 참고). 정렬 등 "행 내용을 확정하는" 처리는 전부 이
    # 함수(메인 스레드) 안에서 이미 끝난 뒤이므로, 큐에 들어가는 건 완성된
    # 불변(immutable) 스냅샷이다.
    from database import history_enqueue
    if team_inserts:
        history_enqueue("team_season_lineup", team_inserts)

    if inserts:
        # [2026-08 최적화] player_id 순으로 정렬해서 넣는다. 이 표의 기본키는
        # (player_id, year)이고 WITHOUT ROWID라 키 순서가 곧 저장 순서인데,
        # 위 루프는 "팀별"로 돌기 때문에 player_id가 뒤죽박죽인 채로 26만 건이
        # 들어갔다 — B-tree 입장에서는 매번 다른 페이지를 열어 중간에 끼워넣는
        # 셈이라 페이지 분할이 계속 일어난다. 키 순으로 넣으면 뒤쪽에 차곡차곡
        # 붙기만 하면 된다. 정렬은 안정 정렬이고 (player_id, year)가 이 목록
        # 안에서 유일하므로(선수 한 명당 이 해에 한 행) 저장 결과는 완전히 동일.
        inserts.sort(key=_ins_key)
        history_enqueue("ai_player_position_history", inserts)

    # [2026-09 신설, 히스토리 비동기 writer] _snapshot_season_ratings가
    # 바로 이어서(같은 호출 시퀀스 안에서) 이 해의 role/실질포지션을
    # 다시 읽어가는데, 방금 큐에 넣은 hist 쓰기가 아직 워커에 의해
    # 커밋되기 전일 수 있다(이 둘은 game_engine._process_promotion_
    # relegation에서 거의 곧바로 연달아 호출됨 — history_drain()으로
    # 기다리면 26만행 커밋을 그 자리에서 그대로 기다리는 꼴이라 애초에
    # 비동기화한 의미가 없어진다). 대신 이 함수가 이미 메모리에 들고
    # 있는 값을 그대로 반환해서, 호출부가 DB 왕복 없이 직접 넘겨줄 수
    # 있게 한다 — game_engine._process_promotion_relegation의 호출부
    # 수정 참고.
    return {t[0]: (t[2], t[3]) for t in inserts}


_SNAPSHOT_EQP_LOGGED: set = set()   # [2026-09 신설, 진단용] _snapshot_team_lineup_half의
                                      # 실행계획 로그를 연도당 한 번만 찍기 위한 중복방지 집합


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
    않게 한다.

    hist.team_season_lineup_half(이 함수 전용)에 저장 — 기존 hist.
    team_season_lineup(시즌 끝, 오프시즌 이적 "전" 스냅샷 — 상반기 이적은
    이미 반영된 뒤라 사실상 "하반기" 로스터, database.py 주석 참고)과는
    별개 표라 기존 화면·로직엔 전혀 영향이 없다.

    [2026-09 확장, 신민용 요청: "세계 선수 검색에서 상반기/하반기를 다
    나눠야 한다 — 상반기엔 주전이었다가 하반기엔 로테이션으로 가는
    경우도 떠야 하니까. 같은 팀이라도 그 해가 2번 뜨는 거지, 이때
    주전/로테 변경이 될 수 있으니"] 처음엔 "역할은 이 표에서 안
    쓰므로 계산하지 않는다"였는데, 이제 선수 검색 쪽에서도 이 시점의
    역할이 필요해졌다 — _snapshot_season_positions(하반기/최종
    스냅샷)와 완전히 같은 방식(formation_logic.compute_squad_roles)으로
    이 시점(상반기) 역할도 같이 계산해 hist.ai_player_position_history_half
    (player_id, year, position, role)에 저장한다. world_browser.
    get_ai_player_career_history가 이 표와 ai_player_position_history를
    비교해서, 팀은 안 바뀌었는데 역할만 바뀐 해를 (기존의 "시즌 중
    이적한 해"와 완전히 동일한 방식으로) 상반기/하반기 두 줄로 나눠
    보여준다.

    [한계] 이 기능 신설 이전 과거 시즌은 소급 적용 안 됨 — 그 해는
    화면에서 "상반기 기록 없음"으로 처리한다."""
    from formation_logic import _greedy_fill_slots, compute_squad_roles
    from constants import FORMATION_SLOTS
    import json
    import time as _t_snap

    # [2026-09 신설, 진단용] "스냅샷INSERT" 버킷이 15년간 +125%(1.5s→3.4s)
    # 늘었는데 원인을 못 찾았다 — lower_cup_matches 때처럼 SELECT/파이썬
    # 처리/INSERT 2종을 각각 갈라서 재고, growing table(WITHOUT ROWID,
    # (entity_id, year) PK)인 hist.team_season_lineup_half/hist.ai_player_
    # position_history_half의 실행계획도 연도당 한 번씩 확인한다. 로직은
    # 전혀 안 바꾼다 — 계측만 추가.
    _sn0 = _t_snap.perf_counter()
    if year not in _SNAPSHOT_EQP_LOGGED:
        _SNAPSHOT_EQP_LOGGED.add(year)
        try:
            _plan = c.execute(
                """EXPLAIN QUERY PLAN SELECT ap.id AS id, ap.team_id AS team_id,
                       ap.position AS position, ap.ovr AS ovr, ap.age AS age,
                       ap.salary AS salary,
                       t.formation AS formation
                   FROM ai_players ap NOT INDEXED JOIN teams t ON ap.team_id = t.id
                   WHERE ap.team_id IS NOT NULL""").fetchall()
            _cnt_lineup = c.execute(
                "SELECT COUNT(*) FROM hist.team_season_lineup_half").fetchone()[0]
            _cnt_role = c.execute(
                "SELECT COUNT(*) FROM hist.ai_player_position_history_half").fetchone()[0]
            _perf_log(f"[PERF-SNAPSHOT-EQP] {year}년 ai_players/teams 조회: "
                      f"{' / '.join(r[-1] for r in _plan)} | "
                      f"team_season_lineup_half(전체{_cnt_lineup}행) | "
                      f"ai_player_position_history_half(전체{_cnt_role}행)")
        except Exception as _e:
            _perf_log(f"[PERF-SNAPSHOT-EQP] {year}년 실행계획 조회 실패: {_e}")

    # [2026-09 성능] NOT INDEXED 설명은 _snapshot_season_ratings의 같은 쿼리
    # 주석 참고(위 [PERF-SNAPSHOT-EQP] 로그가 해마다 실행계획이 뒤집히는 걸
    # 이미 관찰하고 있던 바로 그 쿼리다 — 이제 항상 테이블 스캔으로 고정된다).
    rows = c.execute(
        """SELECT ap.id AS id, ap.team_id AS team_id, ap.position AS position,
                  ap.ovr AS ovr, ap.age AS age, ap.salary AS salary,
                  ap.foot AS foot, ap.sub_role AS sub_role,
                  t.formation AS formation
           FROM ai_players ap NOT INDEXED JOIN teams t ON ap.team_id = t.id
           WHERE ap.team_id IS NOT NULL""").fetchall()
    _sn1 = _t_snap.perf_counter()   # [진단용] ai_players SELECT 끝
    if not rows:
        return
    # [2026-09 신설] 이 함수는 항상 위 SELECT로 rows를 직접 뜨므로 salary가
    # 늘 있지만, _snapshot_season_positions와 같은 형태를 유지해 둔다
    # (나중에 rows 주입 경로가 생겨도 조용히 KeyError가 나지 않게).
    _has_salary = "salary" in rows[0].keys()
    _has_foot = "foot" in rows[0].keys()

    by_team = {}
    for r in rows:
        by_team.setdefault(r["team_id"], []).append(r)

    # [내 선수도 포함] _snapshot_season_positions와 동일한 이유 —
    # 그 주석 참고. 내가 상반기에 이 팀 소속이었으면 상반기 포메이션에도
    # 같이 떠야 한다(역할 계산 풀에도 포함해야 다른 선수들 서열이
    # 정확해진다 — 단, 아래 저장은 _snapshot_season_positions와 동일하게
    # 내 몫은 제외한다. 내 포지션/역할은 my_player_position_history 전용).
    _me = None
    try:
        _me_row = c.execute(
            "SELECT current_team_id, position, ovr, age, salary, foot, sub_role "
            "FROM my_player WHERE id=1").fetchone()
        if _me_row and _me_row["current_team_id"]:
            _me = _me_row
    except Exception:
        _me = None

    team_inserts = []
    role_inserts = []
    for _team_id, players in by_team.items():
        formation = players[0]["formation"] or "4-4-2"
        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        # [2026-09 신설] 그 해 연봉을 스냅샷에 같이 남긴다 — ai_players.salary는
        # 현재값만 갖고 있어 과거 연도의 연봉을 나중에 복원할 수 없다(은퇴하면
        # ai_players에서 사라지고 ai_players_retired엔 salary 컬럼이 아예 없다).
        # 여기서 보존해야 "2005년 최고 연봉"을 그 당시 값으로 보여줄 수 있다.
        # 호출부가 rows를 넘겨 salary가 없는 경우는 formation 때와 같은 방어
        # 패턴으로 None 처리한다.
        candidates = [{"id": p["id"], "position": p["position"], "ovr": p["ovr"] or 0,
                       "salary": (p["salary"] if _has_salary else None),
                       # [2026-09 신설] 주발 보정(formation_logic._foot_swap_pass)이
                       # 쓰는 두 값. 호출부가 rows를 넘겨 컬럼이 없을 수 있으니
                       # salary와 같은 방어 패턴으로 빈 문자열 처리한다 —
                       # foot=''이면 주발 배율이 1.0이라 기존과 동일하게 동작.
                       "foot": (p["foot"] if _has_foot else ""),
                       "sub_role": (p["sub_role"] if _has_foot else ""),
                       # [2026-09 신설] 역할/뎁스 산정 전용 나이 보정을
                       # 켜는 키(formation_logic.role_age_penalty 주석 참고).
                       # 이 키가 실린 후보에만 보정이 걸린다 — UI 포메이션
                       # 화면·국가대표 소집 등 다른 호출부는 영향 없다.
                       "role_age": p["age"]}
                      for p in players]
        role_pool = [(p["id"], p["position"], p["ovr"], p["age"]) for p in players]
        if _me is not None and _team_id == _me["current_team_id"]:
            candidates.append({"id": _MY_LINEUP_ID, "position": _me["position"],
                               "ovr": _me["ovr"] or 0,
                               "salary": _me["salary"],
                               "foot": (_me["foot"] if "foot" in _me.keys() else ""),
                               "sub_role": (_me["sub_role"] if "sub_role" in _me.keys() else ""),
                               "role_age": _me["age"]})
            role_pool.append((_MY_LINEUP_ID, _me["position"], _me["ovr"], _me["age"]))
        placed = _greedy_fill_slots(candidates, slots)
        started_ids = {pl["id"] for pl in placed if pl is not None}
        # [2026-09 재설계] roles는 이제 실제 슬롯 배정(started_ids)을 그대로
        # "주전" 판정에 쓴다 — formation_logic.compute_squad_roles 주석 참고.
        roles = compute_squad_roles(role_pool, started_ids)
        for slot_idx, pl in enumerate(placed):
            if pl is None or pl["id"] == _MY_LINEUP_ID:
                continue
            role_inserts.append((pl["id"], year, slots[slot_idx], roles.get(pl["id"], "")))
        for p in players:
            if p["id"] not in started_ids:
                role_inserts.append((p["id"], year, p["position"] or "", roles.get(p["id"], "")))
        slots_payload = [{"slot": slots[i], "id": (pl["id"] if pl else None),
                          "salary": (pl.get("salary") if pl else None)}
                          for i, pl in enumerate(placed)]
        bench_payload = [{"id": p["id"], "position": p["position"] or "",
                          "salary": p.get("salary")}
                          for p in sorted(
                              (p for p in candidates if p["id"] not in started_ids),
                              key=lambda p: -(p["ovr"] or 0))]
        team_inserts.append((_team_id, year, formation,
                              json.dumps(slots_payload), json.dumps(bench_payload)))

    _sn2 = _t_snap.perf_counter()   # [진단용] 파이썬 처리(팀별 슬롯배정+역할계산) 끝

    # [2026-09 신설, 히스토리 비동기 writer] 이전엔 c.executemany로 즉시
    # 커밋했다 — 이제 큐에 넘기기만 한다. 이 표들(team_season_lineup_half/
    # ai_player_position_history_half)은 이후 같은 시즌 안에서 다른
    # 게임로직이 다시 읽어가는 지점이 없음을 확인했으므로(월드 브라우저
    # UI만 읽음 — 그쪽은 약간의 지연 반영이어도 무방) drain 없이 그대로
    # 큐에만 넣는다.
    from database import history_enqueue
    if team_inserts:
        history_enqueue("team_season_lineup_half", team_inserts)
    _sn3 = _t_snap.perf_counter()   # [진단용] team_season_lineup_half INSERT 끝

    if role_inserts:
        # [2026-08 최적화] _snapshot_season_positions와 동일한 이유 —
        # (player_id, year) WITHOUT ROWID 기본키라 player_id 순으로
        # 넣어야 B-tree 페이지 분할이 안 생긴다.
        role_inserts.sort(key=_ins_key)
        _sn3b = _t_snap.perf_counter()   # [진단용] 정렬 끝 / INSERT 시작
        history_enqueue("ai_player_position_history_half", role_inserts)
    else:
        _sn3b = _sn3
    _sn4 = _t_snap.perf_counter()   # [진단용] ai_player_position_history_half INSERT 끝

    _perf_log(f"[PERF-SNAPSHOT] {year}년 _snapshot_team_lineup_half 세부: "
              f"ai_players SELECT {_sn1-_sn0:.3f}s({len(rows)}행) | "
              f"파이썬처리 {_sn2-_sn1:.3f}s({len(by_team)}팀) | "
              f"lineup_half INSERT {_sn3-_sn2:.3f}s({len(team_inserts)}건) | "
              f"position_history_half 정렬 {_sn3b-_sn3:.3f}s + INSERT {_sn4-_sn3b:.3f}s"
              f"({len(role_inserts)}건)")


def _snapshot_season_ratings(c, year, team_goals_for=None, include_league=True, competitions=None,
                              pos_role_by_pid=None):
    """[2026-08 신설, 신민용 요청: "세계 축구 기록실 연도별 기록 밑에
    그 해 평균 평점/골/도움 요약을 얇은 행으로 하나 더 보여달라"]

    AI 선수는 개별 경기를 실제로 시뮬레이션하지 않는다(세계 전역 수십만
    명을 매 경기 계산하는 건 불가능 — match_sim.tactical_engine은 오직
    '내 리그 경기' 하나만 이렇게 정교하게 돈다). 대신 game_engine.
    _estimate_ai_season(포지션/OVR/팀 강도 기반 통계 추정 — 베스트11·
    발롱도르 후보 산정에도 이미 쓰이는 그 공식)을 이번 시즌을 마친 로스터
    기준으로 한 번씩 돌려 그 결과를 hist.ai_player_season_stats에
    archive한다.

    [2026-09 리팩터, 신민용 확정: "포메이션 스냅샷처럼 이것도 43주차로
    옮기자"] 팀 포메이션 스냅샷(_snapshot_season_positions)과 완전히 같은
    문제였다 — 원래 이 함수 전체를 run_ai_offseason 맨 앞(시즌 완전종료
    후)에서 한 번에 불렀는데, game_engine._process_promotion_relegation
    (43주차, 승강 확정 직후)이 부르는 apply_squad_turnover_after_movement
    (하위권 일부 방출/교체)가 이미 로스터를 흔들어놓은 "뒤"였다 — 방출된
    선수는 그 시즌 마지막 골/도움/평점 기록이 통째로 안 남고, 반대로
    교체로 새로 들어온 선수는 뛰지도 않은 시즌 기록을 갖는 문제가 있었다.

    다만 리그/국내컵/클럽대항전(CL·EL·ECL)/슈퍼컵은 전부 23주차 이전에
    끝나 43주차로 옮겨도 안전하지만(champions_engine.CL_END_WEEK=23 등),
    **클럽월드컵(CWC)만 45주차부터 열려서**(club_world_cup_engine.
    CWC_START_DAY, 월드컵과 동일한 방식으로 "국제대회 전용 기간"인
    44~52주 안에 스케줄) 43주차 시점엔 아직 대회 자체가 시작도 안 했다
    — 그래서 이 함수를 두 조각으로 쓸 수 있게 쪼갰다:
      (1) include_league=True(기본값), competitions에 "cwc" 제외 —
          game_engine._process_promotion_relegation이 43주차(승강 확정
          직후, apply_squad_turnover_after_movement 호출 "직전")에 호출.
          team_id가 "이번 시즌을 실제로 뛴(승강 개편 전) 팀"이 된다.
      (2) include_league=False, competitions=("cwc",)만 — CWC는 이
          시점 로스터(43주차 이후 개편·교체된 팀, 즉 "실제로 CWC를 뛴
          로스터")를 써야 맞으므로, game_engine._end_of_season "1.4단계"
          (52→1주 진입, CWC 경기가 다 끝난 뒤)에서 원래 타이밍 그대로
          호출한다 — 리그/국내컵/CL/SC는 이미 (1)에서 archive됐으므로
          여기선 다시 안 건드린다(include_league=False로 hist.ai_player_
          season_stats 자체는 재작성 안 함, by_comp의 cwc 행만 추가).
    competitions=None(둘 다 안 넘기는 기존 호출부·헤드리스 테스트 등
    하위호환용)이면 예전처럼 5개 대회(cup/cl/sc/cwc/lower_cup) 전부를
    한 번에 처리한다 — 동작이 예전과 100% 동일하다.

    _collect_league_candidates가 매번 새로 구하는 team_avg/league_avg/리그
    풀시즌 경기수를 여기서는 전세계 팀·리그를 한 번의 그룹핑으로 미리
    계산해 재사용한다(팀/리그 수가 커도 추가 쿼리 없이 단일 스캔으로 처리).

    [한계] ai_player_ovr_history와 동일 — 이 기능 신설 이후 시즌만
    정확하고, 그 이전 과거 시즌은 소급 적용이 안 된다.

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

    if competitions is None:
        competitions = ("cup", "cl", "sc", "cwc", "lower_cup", "dsc")

    # [2026-09 성능, 신민용 리포트: "43주차(승강제)·52주차 딜레이가 심하다"]
    # NOT INDEXED가 붙은 이유 — 이 쿼리는 "팀이 있는 전세계 선수 전부"(26만
    # 행)를 읽는다. 즉 걸러내는 게 아무것도 없어서 ai_players를 그냥 순서대로
    # 훑는 게 최선인데, SQLite는 WHERE ap.team_id IS NOT NULL을 보고
    # idx_aiplayers_team을 범위 스캔(team_id>?)으로 타버린다 — 인덱스를 훑으며
    # 26만 번 rowid로 본문 페이지를 랜덤 액세스하는 꼴이라, 그냥 테이블 스캔
    # 보다 5배 이상 느리다(게임 내 43주차 실측: 2.15s → 0.40s). NOT INDEXED는
    # "이 쿼리에서 ap의 인덱스는 쓰지 말라"는 뜻이고, t는 여전히 INTEGER
    # PRIMARY KEY로 조회되므로 조인 자체는 그대로다.
    #
    # [결과 불변 확인] 이 rows의 '순서'는 아래 estimate_ai_season_batch의
    # 난수 소비 순서라 절대 바뀌면 안 된다 — 게임 안에서 두 쿼리 결과의
    # id 목록이 완전히 일치하는지 직접 비교해 확인했고(순서동일=1),
    # 370일 헤드리스 재현성 검사(91개 테이블 전체 해시)도 통과했다.
    _raw_rows = c.execute(
        """SELECT ap.id AS id, ap.position AS position, ap.ovr AS ovr,
                  ap.sub_role AS sub_role, ap.team_id AS team_id, t.league_id AS league_id
           FROM ai_players ap NOT INDEXED JOIN teams t ON ap.team_id = t.id
           WHERE ap.team_id IS NOT NULL""").fetchall()
    if not _raw_rows:
        return

    # [2026-09 버그수정, 신민용 리포트: "포메이션상 분명 주전 ST가 있는데
    # 왜 ST 뎁스 그룹엔 대기/전력외뿐이야"] 원인 확정: 아래에서 만드는
    # rows는 지금까지 ap.position(고정 등록 포지션, 예: LW)만 썼는데,
    # 실제로 그 시즌 포메이션 ST 슬롯을 채운 선수가 "원래는 LW 등록"인
    # 경우(윙어의 스트라이커 기용 등) 이 함수도, 뎁스 감쇠 그룹도 전부
    # 그 선수를 "LW"로 취급해버려 ST 그룹에서 통째로 빠진다 — 정작 ST
    # 슬롯의 벤치(대기/전력외)들끼리만 남아 "주전이 없는" 것처럼 보인다.
    # hist.ai_player_position_history(year, position, role)는 role
    # 스냅샷 때 이미 "그 시즌 실제로 채운 슬롯"(주전이면 슬롯명, 대기면
    # 원래 등록 포지션)을 함께 저장해두므로, 이걸 그대로 이번 시즌의
    # "실질 포지션"으로 재사용한다 — role과 완전히 같은 출처라 항상 서로
    # 맞아떨어진다. 스냅샷이 없는 선수(과거 세이브 등)는 기존처럼
    # ap.position(고정 등록값) 그대로 폴백한다.
    #
    # [2026-09 신설, 히스토리 비동기 writer] 이 값은 원래 여기서 hist.
    # ai_player_position_history를 직접 SELECT해서 구했다 — 그런데
    # _snapshot_season_positions의 실제 hist 쓰기가 이제 비동기 큐를
    # 거치고, 이 함수는 보통 그 직후(같은 호출 시퀀스 안, game_engine.
    # _process_promotion_relegation) 바로 불린다. 여기서 drain해 기다리면
    # 방금 넣은 26만행 커밋을 그 자리에서 그대로 기다리는 꼴이라 애초에
    # 비동기화한 의미가 없어진다 — 그래서 hist를 다시 읽는 대신, 호출부가
    # _snapshot_season_positions의 반환값을 pos_role_by_pid로 그대로
    # 넘겨받아 쓴다(DB 왕복 자체가 없어지므로 오히려 더 빠르다). 이
    # 인자가 없을 때만(예: skip_season_snapshot=False 경로로 이 함수가
    # 단독 호출되는 예전 방식, 또는 하위호환) 기존처럼 hist에서 직접
    # 읽는다 — 이 경우엔 그 값이 이미 충분히 오래 전(과거 시즌 등)에
    # 커밋됐다고 보는 게 합리적이므로 drain 후 조회한다.
    if pos_role_by_pid is not None:
        _pos_role_by_pid = pos_role_by_pid
    else:
        from database import history_drain
        history_drain()
        _pos_role_by_pid = {r["player_id"]: (r["position"], r["role"]) for r in c.execute(
            "SELECT player_id, position, role FROM hist.ai_player_position_history WHERE year=?",
            (year,)).fetchall()}
    # [2026-09 성능] sqlite3.Row를 문자열 키로 인덱싱하는 건 컬럼 이름
    # 목록을 매번 훑는 C 레벨 선형탐색이다. 이 함수는 26만 행을 리그 1회 +
    # 대회 5회로 반복해서 도므로 그 조회만 수백만 회가 된다 — 조회 직후
    # 한 번만 평탄한 튜플로 접어두고 이후 전 구간이 위치 인덱싱만 쓴다.
    rows = [(r["id"], _pos_role_by_pid.get(r["id"], (r["position"], None))[0] or r["position"],
             r["ovr"] or 0, r["sub_role"], r["team_id"], r["league_id"]) for r in _raw_rows]
    del _raw_rows

    # [2026-09 신설, 신민용 리포트: "OVR 같은 주전과 대기 중 대기가 골을
    # 더 넣었다"] 아래 뎁스 감쇠(_apply_squad_depth_decay)가 클럽 경로
    # 에서는 "apps"가 없어 OVR만으로 순위를 매기는데, OVR이 같거나
    # 비슷하면 이게 사실상 라벨(주전/대기)과 무관해진다. 이 시점엔 이미
    # 그 해 역할 스냅샷(_snapshot_season_positions류, formation_logic.
    # compute_squad_roles 기반)이 hist.ai_player_position_history에
    # 저장돼 있으므로, 그걸 그대로 읽어와 뎁스 감쇠 정렬의 1순위로 쓴다
    # — 없는 선수(아직 스냅샷 안 된 과거 세이브 등)는 빈 문자열로 두면
    # _apply_squad_depth_decay가 자동으로 기존 OVR-only 방식으로 폴백한다.
    role_by_pid = {pid: pr[1] for pid, pr in _pos_role_by_pid.items()}

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
    if include_league:
        raw = []
        _raw_append = raw.append
        # [2026-09 성능 2차] 예전엔 26만 행마다 league_matches/team_avg/
        # league_avg/league_goal_mult를 각각 조회해 행당 dict.get이 4번씩
        # (총 100만 회) 돌았다. tid가 정해지면 lid도 정해지므로 이 네 값은
        # 팀당 한 벌뿐이다 — 팀별로 한 번만 묶어두고 행당 조회를 1번으로
        # 줄인다(팀 수 약 1만 개). 값도 순서도 그대로다.
        _team_ctx = {}
        # [2026-09 성능] 위 대회별 루프와 같은 이유로 묶음 계산으로 바꿨다.
        # 아래는 원래 루프에 있던 설명 주석이다.
        from game_engine import estimate_ai_season_batch, _make_season_estimate_rng
        _fsm_l = []; _ta_l = []; _la_l = []; _gm_l = []
        for _pid, _pos, _ovr, _sub, tid, lid in rows:
            _cx = _team_ctx.get(tid)
            if _cx is None:
                _cx = _team_ctx[tid] = (league_matches.get(lid, 38),
                                         team_avg.get(tid, 50.0),
                                         league_avg.get(lid, 50.0),
                                         league_goal_mult.get(lid, 1.0))
            _fsm_l.append(_cx[0]); _ta_l.append(_cx[1])
            _la_l.append(_cx[2]); _gm_l.append(_cx[3])
        # [2026-09 신설, NumPy 난수 결정화] 난수원을 (월드 salt, 연도, scope)
        # 기반 결정론적 Generator로 고정한다 — 예전엔 시드 없는 numpy 전역
        # 난수라 같은 세이브를 다시 돌려도 이 추정치만 매번 달라졌다
        # (game_engine._make_season_estimate_rng 주석 참고). 리그와 각 대회는
        # 반드시 서로 다른 scope를 써야 같은 난수열을 공유하지 않는다.
        _gs, _as, _rts, _css, _svs, _gcs = estimate_ai_season_batch(
            [r[2] or 0 for r in rows], [r[1] for r in rows], [r[3] for r in rows],
            _ta_l, _la_l, _fsm_l, _gm_l,
            rng=_make_season_estimate_rng("league", year))
            # [2026-09 신설, 신민용 요청: "GK들은 골 어시보단 선방률 이런걸로
            # 표시해야 하잖아"] 골/도움과 별개로 클린시트(무실점 경기 수)도
            # 같이 추정한다 — GK가 아닌 포지션도 값 자체는 계산·저장해두지만
            # (계산 비용이 적어 굳이 분기할 필요 없음), 화면에서 GK만 이
            # 값을 골/도움 대신 보여준다(world_browser_window.py).
            # [2026-09 재수정, 신민용 요청: "클린시트 말고 선방:14 실점:1
            # 선방률:93.5%로 떠야한다"] game_engine._estimate_ai_gk_saves
            # 정의부 주석 참고 — GK만 의미 있는 값이라 GK일 때만 계산한다
            # (그 외 포지션은 컬럼 기본값 0 그대로).
        # ── [2026-09 신설, 신민용 확정 2차] 역할 → 실제 출전수 ──────
        # 여태 matches 컬럼에는 _fsm_l(그 팀이 치른 경기수)이 로스터
        # 전원에게 똑같이 들어갔다 — 실측하면 전 선수 26~49경기, 0경기
        # 0명이라 "출전"이라는 축이 아예 없었다. 이제 시즌 시작에 확정된
        # 역할(_ROLE_PLAY_RATIO)로 선수마다 출전률을 뽑아 실제 출전수를
        # 만든다.
        #
        # 골/도움/클린시트/선방도 같은 비율로 곱한다. estimate_ai_season_
        # batch의 fsm 스케일은 (fsm/38)**0.35로 **의도적으로 완만한** 값이라
        # (리그 규모 차이를 반영하는 용도이지 출전시간용이 아니다) 거기에
        # 선수 출전수를 넣으면 34경기 vs 10경기가 1.5배밖에 안 갈린다 —
        # 그래서 fsm에는 팀 경기수를 그대로 넘기고, 출전 비례는 여기서
        # 선형으로 따로 곱한다. 평점은 경기당 평균이므로 건드리지 않는다.
        #
        # 난수는 (월드 salt, 연도, scope) 기반 결정론적 Generator —
        # 리그/대회 추정치와 같은 관례이며 scope만 다르다(같은 난수열을
        # 공유하면 안 됨).
        _pr_list = _roll_play_ratios(
            [role_by_pid.get(r[0]) or "" for r in rows],
            _make_season_estimate_rng("playtime", year))
        for _i2, (_pid, _pos, _ovr, _sub, tid, lid) in enumerate(rows):
            _ratio = _pr_list[_i2]
            _appear = int(round(_fsm_l[_i2] * _ratio))
            _raw_append([_pid, year, tid, _appear,
                         int(round(_gs[_i2] * _ratio)), int(round(_as[_i2] * _ratio)),
                         _rts[_i2],
                         int(round(_css[_i2] * _ratio)), int(round(_svs[_i2] * _ratio)),
                         int(round(_gcs[_i2] * _ratio))])

        # [2026-09 신설, 신민용 리포트: "팀 골이 30개면 애들이 골고루 나눠
        # 갖는 것 같다 — 득점왕이 10골 정도밖에 안 된다"] 스쿼드 뎁스 감쇠 —
        # team_goals_for 스케일링 전에 적용해야 "팀 추정 합계"가 이미 쏠린
        # 모양이 되고, 그 다음 실제 골 합계로 스케일링해도 쏠린 모양이 그대로
        # 유지된다(game_engine._apply_squad_depth_decay 문서 참고). raw는
        # 컬럼 위치 고정 리스트(rows와 같은 순서로 1:1 대응)라, 그 자리에서
        # goals(row[4])/assists(row[5])만 덮어쓰는 얇은 dict 래퍼를 만들어
        # 공유 함수에 넘긴 뒤 결과를 다시 raw에 되돌려 쓴다.
        _depth_rows = [{"team_id": r[4], "position": r[1], "ovr": r[2],
                         "role": role_by_pid.get(r[0], ""),
                         "goals": row[4], "assists": row[5],
                         "clean_sheets": row[7], "saves": row[8], "goals_conceded": row[9]}
                        for r, row in zip(rows, raw)]
        _apply_squad_depth_decay(_depth_rows, key_fn=lambda d: (d["team_id"], d["position"]))
        # [2026-09 통일, 신민용 요청: "득점왕 판정도 세계기록실 골이랑 같은
        # 보정을 쓰게"] 팀 실제 득점 배분도 _collect_league_candidates(개인수상
        # 판정)와 완전히 같은 함수(_apply_team_goal_budget)를 공유한다.
        # allow_zero=False — 여기 team_goals_for는 teams.goals_for 전체
        # 스냅샷이라 아직 집계 안 된 팀이 0으로 섞일 수 있다(그 함수 주석 참고).
        _apply_team_goal_budget(_depth_rows, lambda d: d["team_id"], team_goals_for)
        for row, d in zip(raw, _depth_rows):
            row[4], row[5] = d["goals"], d["assists"]
            row[7], row[8], row[9] = d["clean_sheets"], d["saves"], d["goals_conceded"]

        inserts = [tuple(row) for row in raw]
        inserts.sort(key=lambda t: (t[0], t[1]))
        # [2026-09 신설, 히스토리 비동기 writer] 이전엔 여기서 즉시 커밋했다
        # — 이제 큐에 넘기기만 한다(database.py 상단 설계 불변식 참고).
        from database import history_enqueue
        history_enqueue("ai_player_season_stats", inserts)

    # [2026-09 신설] 대회별(국내컵/클럽대항전/슈퍼컵/클럽월드컵) 추정치 —
    # 위 리그와 완전히 같은 공식·team_avg/league_avg를 재사용하되, 이번
    # 시즌 그 팀이 그 대회에서 실제로 뛴 경기수만 대회마다 새로 센다.
    # [2026-09 성능, 43주차 스냅샷] 예전엔 경기수(_team_comp_match_counts)와
    # 그 대회 실제 득점(_team_comp_goals_for)을 따로 물어서, 같은 표를 같은
    # 조건(WHERE t.year=? AND m.home_score!=-1)·같은 GROUP BY로 홈/원정
    # 2번씩 = 대회당 4번 훑었다. 두 집계는 필터도 그룹도 완전히 같으므로
    # 한 쿼리에서 COUNT(*)와 SUM(점수)을 같이 받으면 대회당 2번으로 줄어든다
    # (표 스캔 절반). cl/el/ecl/sc/cup/cwc/lower_cup_matches 7개 표는
    # prune이 없어 해마다 쌓이기만 하고, 그래서 "대상 경기수는 그대로인데
    # 조회시간만 15년새 10배"가 되는 표다(database.py 상단 캐시 주석 참고)
    # — 스캔 횟수 자체를 줄이는 게 장기 세이브에서 특히 크다. 반환값은
    # 예전 두 함수를 각각 부른 것과 완전히 같다(키 순서까지 동일).
    def _team_comp_counts_and_goals(table_matches, table_tournaments):
        counts, goals = {}, {}
        for side, score_col in (("home_team_id", "home_score"),
                                 ("away_team_id", "away_score")):
            for row in c.execute(
                    f"""SELECT m.{side} AS tid, COUNT(*) AS n,
                               COALESCE(SUM(m.{score_col}),0) AS g
                        FROM {table_matches} m
                        JOIN {table_tournaments} t ON m.tournament_id = t.id
                        WHERE t.year=? AND m.home_score!=-1
                        GROUP BY m.{side}""", (year,)).fetchall():
                _tid = row["tid"]
                counts[_tid] = counts.get(_tid, 0) + row["n"]
                goals[_tid] = goals.get(_tid, 0) + row["g"]
        return counts, goals

    # 챔스/유로파급/컨퍼런스급은 워터폴 구조상 한 팀이 한 해에 최대
    # 하나에만 속하므로(world_browser.py의 같은 전제 참고) 세 집계를
    # 그냥 합쳐도 안전하다 — "클럽대항전" 한 칸으로 통합 표시하는 UI와
    # 원칙이 동일하다.
    # [2026-09 신설] competitions에 없는 대회는 아예 쿼리도 안 돈다 —
    # 43주차 호출(cwc 제외)이 굳이 cwc_matches를 스캔할 필요가 없고,
    # 52주차 cwc 전용 호출도 이미 (1)에서 끝난 cup/cl/sc/lower_cup을
    # 다시 스캔할 필요가 없다.
    # [2026-09 신설, 신민용 요청: "3부/4부 국내컵도 선수 평점/골/도움/
    # 선방 기록이 생겨야 하지"] 국내컵(cup)과 완전히 같은 패턴 — 이
    # 대회는 챔스/유로파/컨퍼런스처럼 리그와 겹치지 않는(3/4부 팀만
    # 참가) 별개 대회라 그냥 5번째 키로 추가하면 된다. world_browser.py
    # 쪽 _comp_stats["lower_cup"]으로 그대로 읽힌다.
    # [2026-09 신설, 신민용 리포트: "국내슈퍼컵도 평점/골/어시 단판
    # 기록이 있어야 하는데 아예 없다"] lower_cup을 5번째 키로 추가한
    # 것과 완전히 같은 이유·같은 패턴 — domestic_sc_matches/
    # domestic_sc_tournaments도 다른 대회들과 컬럼 구성이 100% 같아서
    # (home_team_id/away_team_id/home_score/away_score/tournament_id,
    # tournaments.year) 헬퍼 함수 수정 없이 표 이름만 바꿔 끼우면 된다.
    _COMP_TABLES = {
        "cup": ("cup_matches", "cup_tournaments"),
        "cl":  None,     # cl/el/ecl 셋을 합친다(아래) — 위 워터폴 주석 참고
        "sc":  ("sc_matches", "sc_tournaments"),
        "cwc": ("cwc_matches", "cwc_tournaments"),
        "lower_cup": ("lower_cup_matches", "lower_cup_tournaments"),
        "dsc": ("domestic_sc_matches", "domestic_sc_tournaments"),
    }
    # [주의] comp_match_counts의 키 순서(cup→cl→sc→cwc→lower_cup→dsc)는
    # 아래 대회별 루프의 처리 순서이자 _apply_ace_concentration의 난수
    # 소비 순서다 — _COMP_TABLES 정의 순서를 바꾸면 결과가 달라진다.
    comp_match_counts = {}
    comp_goals_for = {}
    for _comp, _tbl in _COMP_TABLES.items():
        if _comp not in competitions:
            continue
        if _comp == "cl":
            _counts, _goals = {}, {}
            for _prefix in ("cl", "el", "ecl"):
                _cn, _gl = _team_comp_counts_and_goals(
                    f"{_prefix}_matches", f"{_prefix}_tournaments")
                for tid, n in _cn.items():
                    _counts[tid] = _counts.get(tid, 0) + n
                for tid, gsum in _gl.items():
                    _goals[tid] = _goals.get(tid, 0) + gsum
        else:
            _counts, _goals = _team_comp_counts_and_goals(*_tbl)
        comp_match_counts[_comp] = _counts
        comp_goals_for[_comp] = _goals

    # [2026-09 버그수정, 신민용 리포트: "국내컵/챔스 등 대회 초반 탈락한
    # 선수도 그 대회에서 실제로 뛴 경기수 기준 풀시즌 기대치의 30%가량이
    # 그대로 반영돼, 팀 실제 스코어(예: 0-2 탈락)와 전혀 안 맞는 골/도움이
    # 나온다"] 위 리그(hist.ai_player_season_stats)는 team_goals_for로
    # "Tier B" 보정을 받는데, 이 대회별 블록만 그 보정이 빠져 있었다 —
    # _estimate_ai_season은 대회당 실제로 뛴 경기수(fsm)는 정확히 반영하지만
    # "그 대회에서 실제로 넣은 골 합계"는 전혀 모른 채 팀 강도만으로 독립
    # 추정하기 때문에, 한 팀이 이 대회에서 실제로 넣은 골 합계와 그 팀
    # 선수들의 추정 골 합계가 우연히만 맞아떨어진다. 경기수와 완전히 같은
    # 패턴으로 대회별 "실제 득점"(home_score/away_score 합)도 집계해서
    # (지금은 _team_comp_counts_and_goals가 경기수와 한 쿼리에서 같이
    # 받아온다), 리그와 동일하게 team_goals_for 스케일링을 대회별로도
    # 적용한다 — 이러면 "그 대회에서 이 팀 선수들 골을 다 더하면 그 대회
    # 그 팀 실제 득점과 같다"가 보장된다(도움은 리그와 동일 원칙으로 대응
    # 실측치가 없어 손대지 않는다).
    # (위 _team_comp_counts_and_goals가 경기수와 함께 이미 집계해뒀다 —
    # comp_goals_for도 같은 루프에서 채워진다.)

    # [2026-09 버그수정, 신민용 리포트: "2012년 상반기 데포르티보 알라베스에
    # 있던 키퍼가 하반기에 비야레알로 이적했다 — 챔스가 다 끝난 뒤 하반기에
    # 왔고 개인 커리어에도 그 챔스를 뛴 적이 없는데, 챔스 뛴 선수의 점수를
    # 받아 야신상을 탔다"] 이 함수는 43주차 로스터(ap.team_id)로 대회별
    # 기록을 추정한다. 그런데 클럽 대항전(cl = CL/EL/ECL, 8~23주차)과
    # 국내 슈퍼컵(dsc, 4주차)은 시즌 중 이적 창(29주차)보다 먼저 끝나는
    # 대회라, 그 창에서 팀을 옮긴 선수는 43주차 팀이 아니라 "상반기 팀"
    # 소속으로 그 대회를 뛰었다(또는 상반기 팀이 안 나갔으면 아예 안 뛰었다).
    # 이 두 대회만 이적자를 상반기 팀으로 되돌려 추정한다:
    #   · 상반기 팀이 그 대회에 나갔으면 → 그 팀 경기수·팀 평균·팀 실득점
    #     기준으로 그 팀 선수들과 함께 추정(역할도 상반기 스냅샷 역할)
    #   · 상반기 팀이 안 나갔으면       → 그 대회 행 자체가 안 생긴다
    # 국내컵(cup)은 상/하반기에 걸쳐 진행돼 한쪽 팀으로 자를 수 없어 그대로
    # 두고, 슈퍼컵(sc, 29주차)·3·4부컵(32주차~)·클럽월드컵(45주차~)은 원래
    # 하반기 대회라 43주차 팀 기준이 맞다.
    # 이적자가 없거나 이 두 대회가 이번 호출 대상이 아니면 아래 루프는
    # 예전과 완전히 같은 rows/rows_by_team/role_by_pid를 그대로 쓴다.
    _FIRST_HALF_COMPS = ("cl", "dsc")
    _mover_first_team = {}   # rows 인덱스 → 상반기 팀 id
    _mover_first_lid = {}    # 상반기 팀 id → 그 팀 league_id
    _mover_half_role = {}    # player_id → 상반기 스냅샷 역할
    if any(_k in comp_match_counts for _k in _FIRST_HALF_COMPS):
        _first_by_pid = {}
        for _r in c.execute(
                "SELECT player_id, from_team_id FROM ai_transfer_log "
                "WHERE year=? AND is_mid_season=1 ORDER BY id", (year,)).fetchall():
            if _r[1]:
                _first_by_pid.setdefault(_r[0], _r[1])
        if _first_by_pid:
            for _i, _row in enumerate(rows):
                _f = _first_by_pid.get(_row[0])
                if _f is not None and _f != _row[4]:
                    _mover_first_team[_i] = _f
        if _mover_first_team:
            _ftids = sorted(set(_mover_first_team.values()))
            for _k in range(0, len(_ftids), 500):   # SQLite 변수 한도 대비 청크
                _part = _ftids[_k:_k + 500]
                for _r in c.execute(
                        f"SELECT id, league_id FROM teams WHERE id IN ({','.join('?' * len(_part))})",
                        _part).fetchall():
                    _mover_first_lid[_r[0]] = _r[1]
            # 상반기 역할 — 29주차 _snapshot_team_lineup_half가 이미 오래전에
            # (14주 전) 큐로 넘겨 커밋된 값이라 drain 없이 읽는다. 없으면
            # (구세이브 등) 43주차 역할로 폴백한다.
            _mpids = sorted(rows[_i][0] for _i in _mover_first_team)
            try:
                for _k in range(0, len(_mpids), 500):
                    _part = _mpids[_k:_k + 500]
                    for _r in c.execute(
                            "SELECT player_id, role FROM hist.ai_player_position_history_half "
                            f"WHERE year=? AND player_id IN ({','.join('?' * len(_part))})",
                            (year, *_part)).fetchall():
                        if _r[1]:
                            _mover_half_role[_r[0]] = _r[1]
            except Exception:
                _mover_half_role = {}

    by_comp_inserts = []
    for comp, counts in comp_match_counts.items():
        # [2026-09 버그수정] 위 _FIRST_HALF_COMPS 주석 참고 — 상반기 대회면
        # 이적자만 상반기 팀으로 옮긴 사본을 쓴다(다른 선수 행은 그대로).
        if comp in _FIRST_HALF_COMPS and _mover_first_team:
            _rows_src = list(rows)
            _rbt_src = dict(rows_by_team)
            for _i, _f in _mover_first_team.items():
                _r0 = rows[_i]
                _rows_src[_i] = (_r0[0], _r0[1], _r0[2], _r0[3], _f,
                                 _mover_first_lid.get(_f, _r0[5]))
                _rbt_src[_r0[4]] = [_j for _j in _rbt_src.get(_r0[4], ()) if _j != _i]
                _rbt_src[_f] = list(_rbt_src.get(_f, ())) + [_i]
            _mover_pids = {rows[_i][0] for _i in _mover_first_team}

            def _role_src(pid, default=None, _mp=_mover_pids):
                if pid in _mp and pid in _mover_half_role:
                    return _mover_half_role[pid]
                return role_by_pid.get(pid, default)
        else:
            _rows_src, _rbt_src = rows, rows_by_team

            def _role_src(pid, default=None):
                return role_by_pid.get(pid, default)
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
                _ridx = _rbt_src.get(_tid)
                if _ridx:
                    idxs.extend(_ridx)
        idxs.sort()
        comp_raw = []
        comp_meta = []   # comp_raw와 1:1 대응하는 (position, ovr) — 뎁스 감쇠용
        # [2026-09 성능 2차] 리그 루프와 같은 이유로 팀 단위 컨텍스트를 한
        # 번만 만든다(대회별 블록은 40만 행이라 효과가 더 크다).
        _cctx = {}
        # [2026-09 성능] 선수 한 명씩 돌며 난수를 뽑던 것을 묶음 계산으로 바꿨다
        # (game_engine.estimate_ai_season_batch — 계산식·계수·상하한은 그대로,
        # 난수를 뽑는 순서만 다르다). 아래는 원래 루프에 있던 설명 주석이다.
        from game_engine import estimate_ai_season_batch, _make_season_estimate_rng
        _sel = [_rows_src[_i] for _i in idxs]
        _fsm_c = []; _ta_c = []; _la_c = []; _gm_c = []
        for _pid, _pos, _ovr, _sub, tid, lid in _sel:
            _cx = _cctx.get(tid)
            if _cx is None:
                _cx = _cctx[tid] = (counts[tid], team_avg.get(tid, 50.0),
                                     league_avg.get(lid, 50.0),
                                     league_goal_mult.get(lid, 1.0))
            _fsm_c.append(_cx[0]); _ta_c.append(_cx[1])
            _la_c.append(_cx[2]); _gm_c.append(_cx[3])
        # [2026-09 신설, NumPy 난수 결정화] 리그 블록과 같은 이유 — 대회마다
        # scope를 달리해서(comp:cup / comp:cl / ...) 서로 다른 난수열을 받게 한다.
        _gs, _as, _rts, _css, _svs, _gcs = estimate_ai_season_batch(
            [r[2] or 0 for r in _sel], [r[1] for r in _sel], [r[3] for r in _sel],
            _ta_c, _la_c, _fsm_c, _gm_c,
            rng=_make_season_estimate_rng(f"comp:{comp}", year))
            # tid를 맨 뒤에 임시로 붙여둔다 — 아래 스케일링에서 팀별로
            # goals(index 4)를 찾아 덮어쓴 뒤, insert 직전에 다시 잘라낸다.
        # [2026-09 신설, 2차] 대회별 기록에도 리그와 같은 역할 기반 출전
        # 배분을 적용한다(위 리그 블록 주석 참고) — 안 하면 "리그는 대기인데
        # 컵에서는 주전급 생산"이라는 모순이 생긴다. 대회마다 scope를
        # 달리해 리그·다른 대회와 같은 난수열을 공유하지 않게 한다.
        # (현실 축구에서는 로테이션 자원이 컵에서 더 뛰지만, 그 비대칭은
        #  이번 범위 밖이다 — 같은 비율을 그대로 쓴다.)
        _pr_list_c = _roll_play_ratios(
            [_role_src(r[0]) or "" for r in _sel],
            _make_season_estimate_rng(f"playtime:{comp}", year))
        for _i2, (_pid, _pos, _ovr, _sub, tid, lid) in enumerate(_sel):
            _ratio = _pr_list_c[_i2]
            comp_raw.append([_pid, year, comp, int(round(_fsm_c[_i2] * _ratio)),
                             int(round(_gs[_i2] * _ratio)), int(round(_as[_i2] * _ratio)),
                             _rts[_i2],
                             int(round(_css[_i2] * _ratio)), int(round(_svs[_i2] * _ratio)),
                             int(round(_gcs[_i2] * _ratio)), tid])
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
                         "role": _role_src(row[0], ""),
                         "goals": row[4], "assists": row[5], "matches": row[3],
                         "clean_sheets": row[7], "saves": row[8], "goals_conceded": row[9]}
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
            row[7], row[8], row[9] = d["clean_sheets"], d["saves"], d["goals_conceded"]

        by_comp_inserts.extend(tuple(row[:10]) for row in comp_raw)

    if by_comp_inserts:
        by_comp_inserts.sort(key=lambda t: (t[0], t[1], t[2]))
        from database import history_enqueue
        history_enqueue("ai_player_season_stats_by_comp", by_comp_inserts)


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
                     "apps": r["appearances"] or 0, "matches": r["appearances"] or 0,
                     "clean_sheets": u[3], "saves": u[4], "goals_conceded": u[5]}
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
        u[3], u[4], u[5] = d["clean_sheets"], d["saves"], d["goals_conceded"]

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
        candidates = [{"id": r["id"], "position": r["position"], "ovr": r["ovr"] or 0,
                       "role_age": r["age"]}
                      for r in teammates]
        candidates.append({"id": ME, "position": me["position"], "ovr": me["ovr"] or 0,
                           "role_age": me["age"]})
        pool = [(r["id"], r["position"], r["ovr"], r["age"]) for r in teammates]
        pool.append((ME, me["position"], me["ovr"], me["age"]))

        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        placed = _greedy_fill_slots(candidates, slots)
        started_ids = {pl["id"] for pl in placed if pl is not None}
        # [2026-09 재설계] roles는 이제 실제 슬롯 배정(started_ids)을 그대로
        # "주전" 판정에 쓴다 — formation_logic.compute_squad_roles 주석 참고.
        roles = compute_squad_roles(pool, started_ids)

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


def _enforce_intl_breakout_caps(c, year):
    """[2026-09 신설, 신민용 요청: "선수의 한계치를 국가별로 최대 2명으로
    둬서 3명 이상이 안나오게 하고"] database._apply_intl_breakout(국제대회
    소집 시점에만 낮은 확률로 딱 한 명씩 가산으로 "브레이크아웃"시키는
    장치) 정의부 주석 참고 — 그 장치만으론 이 상한이 실제로 지켜지지
    않는다. 국적은 소속 클럽과 완전히 무관하게 무작위 배정되고
    (database._pick_nationality), OVR 성장 상한도 국적이 아니라 소속팀
    등급으로만 정해지므로(constants.OVR_RANGES 기반 team_cap), 등급 낮은
    나라 국적 선수가 우연히 강한 클럽으로 흘러들어가 성장하면서 그
    등급의 기준선을 넘는 경로가 브레이크아웃 장치와 완전히 별개로
    원래부터 있었다(database.get_country_avg_squad_ovr 정의부의 2026-07
    리포트가 이미 같은 현상을 다른 맥락에서 지적한 바 있다 — 실측:
    헤드리스 4시즌 기준 크로아티아(B등급, 상한5) 국적 90+가 64명까지
    쌓여 있었는데 전부 이 "우연한 강클럽 배정" 경로였다).

    [2026-09 재설계, 신민용 확정 — database._INTL_BREAKOUT_FLOOR 정의부
    주석 참고] "인원 상한"을 셀 기준선이 이제 등급마다 다르다(B=90,
    C=85, D=80, E=75, F=70) — 그래서 표에 등록된 등급 중 가장 낮은
    기준선(현재 F=70)으로 일단 넓게 조회한 뒤, 나라별로 그 나라
    등급의 실제 기준선 이상인 선수만 추려서 상한과 비교한다. 트리밍
    목표 OVR도 예전엔 등급 무관하게 "85~89 사이"로 고정돼 있었는데
    (D~F처럼 기준선이 85보다 낮은 등급은 이 범위가 오히려 "그 등급
    기준으로도 과분한" 값이라 트리밍이 사실상 무의미했다), 이제 그
    나라 등급의 기준선 바로 아래(기준선-5 ~ 기준선-1)로 되돌린다 —
    "상한을 넘겨서 걸러진 선수는 다시 그 등급 기준 평범한 수준으로"
    라는 원래 취지에 맞게.

    성장·이적·스쿼드 인원보정이 전부 끝난 이 시점(run_ai_offseason의
    _rebalance_squad_sizes 직후)에 전세계를 한 번 훑어, database.
    _INTL_BREAKOUT_MAX_COUNT에 등록된 등급(B~F만 — SS/S/A는 애초에
    tier1 OVR_RANGES 자체가 90대를 정상적으로 포함하므로 상한이 없다)
    마다 그 나라 국적 인원이 상한을 넘으면, 초과분만 낮은 OVR부터
    (그 나라 안에서 가장 확실한 에이스들 — 97~99 "천재 예외"가 있다면
    그 선수도 포함 — 은 절대 안 건드림, 아래 `-t[1]` 내림차순 정렬로
    항상 가장 높은 OVR부터 보호됨) 되돌린다 — rescale_ai_player_to_
    target_ovr(기존 함수, 스탯을 평행이동시켜 그 선수 고유의 강약
    분포는 유지)를 그대로 재사용한다.

    [2026-09 신설, 신민용 리포트: "OVR 한도에 사용자가 변경한 경우는
    예외처리 했나?"] "쉬움 난이도"에서 사용자가 직접 OVR을 맞춘 선수
    (ai_players.ovr_user_locked=1)는 이 강제 트리밍에서 완전히 제외한다
    — 상한 인원을 셀 때도 locked 인원은 아예 빼고(그래서 locked만으로
    이미 상한을 넘어도 더는 안 건드림), 남는 자리 안에서만 unlocked
    (자연 성장으로 우연히 기준선을 넘긴 선수) 중 낮은 OVR부터 트리밍한다.

    [2026-09 버그수정, 신민용 확정: "이 등급은 리그 등급이 아닌 국가
    등급을 말하는거고"] database._apply_intl_breakout과 동일한 이유로
    get_country_league_grade가 아니라 get_country_grade(FIFA 랭킹 기반
    국대 등급)를 써야 한다 — 이 체계엔 SS가 없다(S/A/B/C/D/E/F 7단계)."""
    from database import _INTL_BREAKOUT_MAX_COUNT, _INTL_BREAKOUT_FLOOR, rescale_ai_player_to_target_ovr
    from constants import get_country_grade
    _lowest_floor = min(_INTL_BREAKOUT_FLOOR.values())
    rows = c.execute(
        "SELECT id, nationality, ovr, ovr_user_locked FROM ai_players "
        "WHERE ovr>=? AND nationality!=''", (_lowest_floor,)).fetchall()
    if not rows:
        return
    by_nat: dict = {}
    for r in rows:
        by_nat.setdefault(r["nationality"], []).append((r["id"], r["ovr"], bool(r["ovr_user_locked"])))
    for nat, lst in by_nat.items():
        grade = get_country_grade(nat)
        cap = _INTL_BREAKOUT_MAX_COUNT.get(grade)
        if cap is None:
            continue
        floor = _INTL_BREAKOUT_FLOOR.get(grade, 90)
        lst = [(pid, ovr, lk) for pid, ovr, lk in lst if ovr >= floor]
        if len(lst) <= cap:
            continue
        # [2026-09 신설, 신민용 요청: "OVR 한도에 사용자가 변경한 경우는
        # 예외처리 했나?"] 사용자가 "쉬움 난이도"에서 직접 맞춘 선수
        # (locked)는 절대 안 건드린다 — 상한 계산에서도 빼서, locked
        # 인원이 이미 상한을 넘겨도(그 이상 손대지 않음) 나머지(자연
        # 성장으로 우연히 기준선을 넘긴 unlocked)만 상한에 맞춰 트리밍한다.
        locked = [(pid, ovr) for pid, ovr, lk in lst if lk]
        unlocked = [(pid, ovr) for pid, ovr, lk in lst if not lk]
        remaining_slots = max(0, cap - len(locked))
        if len(unlocked) <= remaining_slots:
            continue
        unlocked.sort(key=lambda t: -t[1])   # 높은 OVR부터 — 남는 자리만큼은 그대로 둔다
        for pid, _ovr in unlocked[remaining_slots:]:
            rescale_ai_player_to_target_ovr(
                pid, random.randint(max(15, floor - 5), floor - 1), conn=c)


# [2026-09 신설, 신민용 확정: "A급 이상 국가 = 95+ 최소 1명 — S급은 굳이 안
# 해도 무조건 들어가는데 A급은 없을 때도 있으니"] _enforce_intl_breakout_caps
# (B~F 등급 90+ 인원 "상한")의 반대 방향 장치 — S/A 등급 국가는 상한이 없는
# 대신 "최소 1명"을 보장한다. 국적(true_nationality) 기준으로 95+가 한 명도
# 없으면, 그 나라 국적 선수 중 전성기 연령대(22세~_AI_PEAK_END) 최고 OVR
# 선수 한 명을 95~96으로 끌어올린다. 전성기 연령으로 제한하는 이유:
# 노화기 선수는 rescale 함수가 목표치를 나이만큼 깎아 95에 못 닿고, 너무
# 어린 선수는 성장 곡선상 이미 "특급 유망주" 취급이 되어 부자연스럽다.
# peak_ovr/potential_ovr도 같이 올려서 다음 시즌 성장·노화 로직이 그 선수를
# 원래 한계치로 되끌어내리지 않게 한다. 사용자가 쉬움 난이도에서 직접 OVR을
# 맞춘 선수(ovr_user_locked)는 후보에서 뺀다.
_TOP_GRADE_STAR_FLOOR = 95
_TOP_GRADE_STAR_TARGET = (95, 96)


def _ensure_top_grade_star_floor(c, year):
    from database import rescale_ai_player_to_target_ovr
    from constants import get_country_grade
    names = [r["name"] for r in c.execute("SELECT name FROM countries").fetchall()]
    targets = [n for n in names if get_country_grade(n) in ("S", "A")]
    if not targets:
        return 0
    have = {r["n"] for r in c.execute(
        "SELECT DISTINCT true_nationality AS n FROM ai_players WHERE ovr>=?",
        (_TOP_GRADE_STAR_FLOOR,)).fetchall()}
    promoted = 0
    for nat in targets:
        if nat in have:
            continue
        row = c.execute(
            "SELECT id, ovr, peak_ovr, potential_ovr FROM ai_players "
            "WHERE true_nationality=? AND age BETWEEN 22 AND ? "
            "AND COALESCE(ovr_user_locked,0)=0 "
            "ORDER BY ovr DESC, age ASC LIMIT 1", (nat, _AI_PEAK_END)).fetchone()
        if not row:
            continue
        tgt = random.randint(*_TOP_GRADE_STAR_TARGET)
        rescale_ai_player_to_target_ovr(row["id"], tgt, conn=c)
        new_ovr = c.execute("SELECT ovr FROM ai_players WHERE id=?", (row["id"],)).fetchone()["ovr"]
        c.execute("UPDATE ai_players SET peak_ovr=MAX(COALESCE(peak_ovr,0), ?), "
                  "potential_ovr=MAX(COALESCE(potential_ovr,0), ?) WHERE id=?",
                  (new_ovr, new_ovr, row["id"]))
        promoted += 1
    if promoted:
        _perf_log(f"[STAR-FLOOR] {year}년 S/A 국가 95+ 최소 1명 보장: {promoted}개국 승격")
    return promoted


def _enforce_foreign_quota_worldwide(c, year):
    """[2026-09 신설 → 2026-09 전면 재설계] 전세계에서 외국인 쿼터
    (database.FOREIGN_QUOTA_RANGE)를 넘긴 팀을 실제로 되돌린다.

    [왜 재설계했나 — 신민용 리포트: "8명이 왜 나와? 5명 한계인데 8명으로
    뚫었잖아"] 1차 버전의 유일한 수단은 "quota_local_country를 그 나라로
    걸어 자국 등록으로 전환"이었다. 그런데 사용자가 스쿼드에서 세는 건
    진짜 국적(is_roster_foreign)이고, 전환은 nationality를 건드리지
    않으므로 이 수단으로는 화면에 보이는 외국인 수가 단 한 명도 줄지
    않았다 — 게다가 팀당 전환 상한(FOREIGN_NATURALIZE_MAX_PER_TEAM=2)을
    다 쓰면 그 뒤로는 아무 일도 하지 않았다. 실측 계측 하니스에서 이
    함수가 초과팀 수를 한 번도 줄이지 못한 게 그래서였다.

    [새 수단 — 자국 선수 역이민 맞교환] 초과분 외국인을 "그 팀 나라 국적인데
    지금 해외에서 뛰는 선수"와 1:1로 맞바꾼다. 이 교환은 어느 팀의 외국인
    수도 늘리지 않는다는 불변식을 가진다:
      - 초과팀(나라 C): 외국인 1명 내보내고 C 국적자 1명 받음 → -1
      - 상대팀(나라 X): C 국적자(그 팀에선 외국인) 내보내고 초과팀에서 온
        선수(국적 N)를 받음 → N≠X면 ±0, N==X면 -1
    현실 축구에서도 "쿼터가 꽉 찬 클럽이 용병을 정리하고 해외파 자국
    선수를 데려오는" 바로 그 움직임이라 서사도 맞는다. 같은 포지션끼리만
    교환하므로 스쿼드 구성(포지션 인원)도 안 깨지고, 양 팀 인원수도
    그대로다(_rebalance_squad_sizes를 추가로 흔들지 않는다).

    내보낼 순서는 OVR 낮은 외국인부터 — 에이스급 용병은 최대한 보호한다.
    맞교환 상대가 없으면(그 나라 해외파가 그 포지션에 아무도 없을 때)
    예전 수단인 자국 등록 전환(팀당 FOREIGN_NATURALIZE_MAX_PER_TEAM까지)
    으로 한 자리씩 메운다 — 그래도 안 되면 그 팀은 이번 시즌 그대로 두고
    다음 시즌에 다시 시도한다(은퇴/이적으로 자연히 줄어든다).

    [강등이 쿼터를 깎는다] get_foreign_quota_range는 2부부터 상한을 1씩
    깎으므로(1부5 → 2부4 → …), 1부에서 외국인 5명을 데리고 강등된 팀은
    선수 이동이 하나도 없어도 그 순간 초과 상태가 된다 — 이 함수가 매
    시즌 도는 진짜 이유의 절반이 그것이다.

    반환: (맞교환 건수, 자국등록 전환 인원 수).
    """
    from database import (get_foreign_quota_range, is_roster_foreign,
                          FOREIGN_NATURALIZE_MAX_PER_TEAM)
    # [2026-09 버그수정, 신민용 리포트 1번: "이집트 OVR86 → … → 갑자기
    # 대한민국 5부"] 아래 맞교환이 목적지 팀의 수준을 전혀 안 봤다 —
    # 자세한 내용은 아래 _team_ceiling / _ceil_ok 주석 참고. 판정에
    # 쓸 등급/상한/팀명을 team_rows에서 한 번에 뽑기 위해 import한다.
    from constants import get_country_league_grade, get_ovr_range

    team_rows = c.execute(
        """SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname,
                  cn.continent AS continent, t.name AS tname
           FROM teams t JOIN leagues l ON t.league_id=l.id
                        JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_rows = _drop_mil_teams(c, team_rows, "tid")   # [2026-10] 군팀 제외
    team_country = {}
    team_quota_hi = {}
    # [2026-09 신설] 팀별 (등급, tier, 팀명, 설계 OVR 상한) — 맞교환 가드와
    # 연봉 재계산에 쓴다. 상한을 못 찾는 깊은 tier는 이 파일의 다른
    # 곳들과 같은 폴백(43)을 쓴다(dst_ovr_ceiling_by_tid 주석 참고).
    team_meta: dict = {}
    _grade_by_cname: dict = {}
    for r in team_rows:
        team_country[r["tid"]] = r["cname"]
        _q_lo, _hi = get_foreign_quota_range(r["cname"], r["continent"], tier=r["tier"])
        team_quota_hi[r["tid"]] = _hi
        _cn = r["cname"]
        _g = _grade_by_cname.get(_cn)
        if _g is None:
            _g = get_country_league_grade(_cn)
            _grade_by_cname[_cn] = _g
        _tier = r["tier"] or 1
        _rng = get_ovr_range(_g, _tier, _cn)
        team_meta[r["tid"]] = (_g, _tier, r["tname"], (_rng[1] if _rng else 43))

    def _ceil_ok(ovr, dst_tid):
        """[2026-09 신설, 신민용 리포트 1번] 이 함수의 역이민 맞교환은
        상대팀(in_tid)의 tier·리그 등급·설계 OVR 상한을 **하나도** 보지
        않았다 — "그 나라 국적 해외파가 지금 뛰고 있는 팀"이라는 이유만으로
        목적지가 정해지므로, 잉글랜드 1부에서 쿼터에 밀린 OVR 93 용병이
        "한국 국적 선수가 한 명 뛰고 있다"는 이유만으로 대한민국 5부
        (설계 상한 33)로 그대로 밀려 들어갈 수 있었다. 게다가 이 함수는
        ai_transfer_log에 아무 기록도 안 남겨서, 화면에는 "이적 기록 없이
        소속만 갑자기 바뀐" 것처럼 보인다(실측 하니스: 시즌당 약 520건의
        무기록 이동이 전부 이 경로).

        이적시장(_do_one_transfer_cached)·잠재력 스카우팅이 이미 쓰는
        것과 **완전히 같은 기준**(_DST_CEIL_HARD_EXCLUDE)을 여기에도
        적용한다 — 두 방향(나가는 외국인 → 상대팀, 들어오는 자국선수 →
        초과팀) 모두 검사한다."""
        _m = team_meta.get(dst_tid)
        if _m is None:
            return True
        return not _dst_ceiling_excluded(ovr or 0, _m[3])

    # [2026-09 수정] 임대로 들어와 있는 선수도 그 팀 로스터에 있으므로
    # 외국인 수에는 **센다**(사용자가 스쿼드를 볼 때 그대로 보이는 인원).
    # 다만 그 선수를 이 함수가 다른 팀으로 옮길 수는 없다(원 소속팀이
    # 따로 있고, 임대 복귀 처리가 _process_loan_returns 소관이다) — 그래서
    # 아래에서 "내보낼 후보"와 "역이민 맞교환 상대 후보"에서만 제외한다.
    # 처음엔 이 선수들을 아예 조회에서 뺐는데, 그러면 세는 쪽(측정)과
    # 고치는 쪽(이 함수)의 기준이 어긋나서 초과팀 123개가 영구히 안
    # 고쳐졌다(실측: 잔존 123팀 전부가 임대 영입 보유 팀, 122팀이 정확히
    # 1명 초과).
    player_rows = c.execute(
        "SELECT id, team_id, position, nationality, quota_local_country, ovr, salary, "
        "on_loan_from_team_id, name, age, contract_end_year FROM ai_players "
        "WHERE nationality!=''").fetchall()

    by_team: dict = {}
    # [해외파 색인] (국적, 포지션) -> [(ovr, player_id, team_id), ...]
    # "지금 뛰는 나라가 자기 국적과 다른" 선수만 담는다 — 이들이 역이민
    # 맞교환의 상대 후보다. 한 번의 전체 순회로 만들고, 아래에서 뽑을
    # 때마다 pop하므로 같은 선수가 두 번 쓰이지 않는다.
    abroad: dict = {}
    # [2026-09] 같은 구체 포지션에 해외파 자국 선수가 아무도 없을 때를 위한
    # 2차 색인 — 포지션 그룹(GK/DF/MF/FW)까지만 맞춘다. 실측: 구체 포지션
    # 만으로는 초과팀 851 → 149까지만 줄고 그 149팀은 "그 포지션 해외파가
    # 세계에 한 명도 없는" 경우였다. 그룹까지 허용하면 스쿼드의 그룹별
    # 인원 비율은 그대로 유지되므로(_rebalance_squad_sizes가 보는 불변식)
    # 안전하게 남은 초과분을 정리할 수 있다.
    abroad_grp: dict = {}
    # [2026-09 3차 색인] 해외파 자국 선수가 바닥났을 때의 마지막 수단 —
    # "자국 리그에서 뛰는 자국 선수". 이 교환은 상대팀(같은 나라)의 외국인
    # 수를 1 늘리므로, 상대팀에 쿼터 여유가 있을 때만 쓴다(아래 cur_foreign
    # 실시간 추적). 실측: 게임 초반(해외 이적이 아직 안 쌓인 1시즌차)에는
    # 해외파 풀만으로는 초과팀 799 → 490까지만 줄었다. 자국 리그 풀까지
    # 열면 "쿼터 찬 팀이 용병을 쿼터 여유 있는 같은 리그 팀에 넘기고 그
    # 팀의 자국 선수를 받는" 현실적인 정리가 가능해진다.
    home_grp: dict = {}
    for r in player_rows:
        tid = r["team_id"]
        by_team.setdefault(tid, []).append(r)
        _tc = team_country.get(tid)
        if not _tc or r["on_loan_from_team_id"]:
            continue
        _ent = (r["ovr"] or 0, r["id"], tid, r["position"])
        if is_roster_foreign(r["nationality"], _tc):
            abroad.setdefault((r["nationality"], r["position"]), []).append(_ent)
            abroad_grp.setdefault(
                (r["nationality"], _POS_GROUP.get(r["position"], "FW")), []).append(_ent)
        else:
            home_grp.setdefault(
                (r["nationality"], _POS_GROUP.get(r["position"], "FW")), []).append(_ent)
    # OVR 높은 쪽이 먼저 뽑히도록(초과팀은 내보내는 선수보다 나은 자국
    # 선수를 데려오려 한다) 내림차순 정렬 — pop()이 끝에서 빼므로 오름차순
    # 으로 저장한다.
    for _k in abroad:
        abroad[_k].sort()
    for _k in abroad_grp:
        abroad_grp[_k].sort()
    for _k in home_grp:
        home_grp[_k].sort()
    _taken: set = set()   # 여러 색인에 같은 선수가 들어 있으므로 중복 사용 방지
    # 팀별 현재 외국인 수(실시간) — 3차 색인(자국 리그 교환)이 상대팀
    # 쿼터를 깨지 않는지 매 교환마다 확인하고 즉시 갱신한다.
    cur_foreign: dict = {}
    for _tid, _plist in by_team.items():
        _tc = team_country.get(_tid)
        if not _tc:
            continue
        cur_foreign[_tid] = sum(1 for _r in _plist
                                if is_roster_foreign(_r["nationality"], _tc))

    moves = []        # (새 team_id, player_id)
    naturalize = []   # (quota_local_country, player_id)
    n_swap = 0
    # [2026-09 신설, 신민용 리포트 1번] 이동 기록/연봉 재계산용 —
    # 위 맞교환 블록 주석 참고.
    _row_by_pid = {r["id"]: r for r in player_rows}
    _salary_updates = []   # (salary, player_id)
    _qlog_rows = []
    _cs_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season_q = _cs_row["current_season"] if _cs_row else 0

    # 초과가 큰 팀부터 처리한다 — 해외파 풀이 한정돼 있으므로, 가장 심하게
    # 깨진 팀이 먼저 자리를 쓰게 한다(8명 초과 팀이 1명 초과 팀 때문에
    # 못 고쳐지는 일이 없게).
    _pending = []
    for tid, plist in by_team.items():
        cname = team_country.get(tid)
        quota_hi = team_quota_hi.get(tid)
        if cname is None or quota_hi is None:
            continue
        foreigners = [r for r in plist if is_roster_foreign(r["nationality"], cname)]
        excess = len(foreigners) - quota_hi
        if excess > 0:
            _pending.append((excess, tid, cname, quota_hi, foreigners))
    _pending.sort(key=lambda t: -t[0])

    for excess, tid, cname, quota_hi, foreigners in _pending:
        # 내보낼 순서: OVR 낮은 외국인부터(에이스 용병 보호). 임대로 와
        # 있는 선수는 이 함수가 옮길 수 없으므로 후보에서 뺀다(위 조회부
        # 주석 참고) — 대신 그 팀의 "자기 소유" 외국인을 한 명 더 정리해서
        # 같은 수를 맞춘다.
        _movable = [r for r in foreigners if not r["on_loan_from_team_id"]]
        _movable.sort(key=lambda r: r["ovr"] or 0)
        left = excess
        for out_p in _movable:
            if left <= 0:
                break
            picked = None
            _grp = _POS_GROUP.get(out_p["position"], "FW")
            # [2026-09 신설] 이번 out_p 기준으로만 부적합한(=목적지 수준
            # 가드에 걸린) 후보를 담아뒀다가 풀에 되돌린다 — 다른 초과팀의
            # 다른 out_p에는 멀쩡히 쓸 수 있는 후보이므로 그냥 버리면 안
            # 된다. out_p는 OVR 오름차순이라 뒤로 갈수록 더 많이 걸린다.
            _deferred = []
            _out_ovr = out_p["ovr"] or 0
            # 1순위: 해외파 자국 선수 중 같은 구체 포지션
            # 2순위: 해외파 자국 선수 중 같은 포지션 그룹
            # (여기까지는 어느 팀의 외국인 수도 늘지 않는다 — docstring 참고)
            for _pool in (abroad.get((cname, out_p["position"])),
                          abroad_grp.get((cname, _grp))):
                while _pool:
                    _ent = _pool.pop()
                    _ovr, _pid, _ptid, _ppos = _ent
                    if _ptid == tid or _pid in _taken:
                        continue     # 같은 팀 안에서의 교환/이미 쓴 선수는 건너뛴다
                    # [2026-09 신설, 위 _ceil_ok 주석 참고] 양방향 수준 가드.
                    if not _ceil_ok(_out_ovr, _ptid) or not _ceil_ok(_ovr, tid):
                        _deferred.append((_pool, _ent))
                        continue
                    picked = (_pid, _ptid)
                    break
                if picked is not None:
                    break
            # 3순위: 자국 리그의 자국 선수 — 이 교환만 상대팀 외국인 수를
            # 1 늘리므로, 쿼터 여유가 있는 팀만 상대로 삼는다.
            if picked is None:
                _pool = home_grp.get((cname, _grp))
                while _pool:
                    _ent = _pool.pop()
                    _ovr, _pid, _ptid, _ppos = _ent
                    if _ptid == tid or _pid in _taken:
                        continue
                    _pq = team_quota_hi.get(_ptid)
                    if _pq is None or cur_foreign.get(_ptid, 0) + 1 > _pq:
                        continue     # 그 팀도 자리가 없다
                    if not _ceil_ok(_out_ovr, _ptid) or not _ceil_ok(_ovr, tid):
                        _deferred.append((_pool, _ent))
                        continue
                    cur_foreign[_ptid] = cur_foreign.get(_ptid, 0) + 1
                    picked = (_pid, _ptid)
                    break
            for _dp, _de in _deferred:
                _dp.append(_de)
            if picked is None:
                continue
            _taken.add(picked[0])
            cur_foreign[tid] = max(0, cur_foreign.get(tid, 0) - 1)
            in_pid, in_tid = picked
            moves.append((tid, in_pid))          # 자국 선수 → 초과팀
            moves.append((in_tid, out_p["id"]))  # 초과 외국인 → 상대팀
            # [2026-09 신설, 신민용 리포트 1번] 이 함수는 소속을 실제로
            # 바꾸면서도 ai_transfer_log에 아무것도 안 남겨서 "이적 기록
            # 없이 소속만 갑자기 바뀐" 것처럼 보였고, 연봉도 옛 팀 기준
            # 그대로 따라다녔다. 다른 이동 통로(_prestige_scouting 등)와
            # 같은 방식으로 양쪽 다 기록하고 연봉을 새 소속 기준으로
            # 다시 계산한다. 난수는 쓰지 않는다(계약 기간은 그대로 둔다 —
            # 이 이동은 쿼터 정리이지 새 계약 협상이 아니다. 여기서
            # randint를 쓰면 이 시점 이후 전역 RNG 스트림이 갈라진다).
            # [2026-10 버그수정, 신민용 리포트: "쿼터 조정으로 옮긴 선수는
            # 계약 기간이 안 뜬다"] 계약을 그대로 이어받는데도 로그의
            # contract_end_year 칸엔 0을 적어서, 선수 검색이 이 이동 이후
            # 계약 기간을 계산할 수 없었다(10시즌 실측 10,258건 전부 0).
            # 이어받은 실제 만료연도를 그대로 적는다 — 이 함수는
            # _process_contract_renewals 다음에 돌므로 임대 중이 아닌
            # 선수(임대 선수는 위에서 교환 후보에서 빠짐)는 전부 만료연도가
            # 올해보다 뒤라, "(계약: 남은 N년)"으로 정상 표시된다.
            _qm_in = team_meta.get(tid)
            _qm_out = team_meta.get(in_tid)
            _in_row = _row_by_pid.get(in_pid)
            if _qm_in is not None and _in_row is not None:
                _sal_in = _calc_ai_salary(_qm_in[0], _qm_in[1], _in_row["ovr"],
                                          cname, _qm_in[2], tid, year)
                _salary_updates.append((_sal_in, in_pid))
                _qlog_rows.append((_cur_season_q, year, in_pid, _in_row["name"] or "",
                                   _in_row["position"],
                                   _in_row["age"] or 0, _in_row["ovr"], _in_row["team_id"], tid,
                                   0, 0, 0.0, 0.0, "쿼터 조정(자국 복귀)", 0, "",
                                   0, 0, 0, _sal_in, _in_row["contract_end_year"] or 0))
            if _qm_out is not None:
                _sal_out = _calc_ai_salary(_qm_out[0], _qm_out[1], out_p["ovr"],
                                           team_country.get(in_tid, ""), _qm_out[2],
                                           in_tid, year)
                _salary_updates.append((_sal_out, out_p["id"]))
                _qlog_rows.append((_cur_season_q, year, out_p["id"], out_p["name"] or "",
                                   out_p["position"],
                                   out_p["age"] or 0, out_p["ovr"], tid, in_tid,
                                   0, 0, 0.0, 0.0, "쿼터 조정(용병 정리)", 0, "",
                                   0, 0, 0, _sal_out, out_p["contract_end_year"] or 0))
            n_swap += 1
            left -= 1
        if left <= 0:
            continue
        # 맞교환 상대가 없어 남은 초과분은 예전 수단(자국 등록 전환)으로
        # 팀당 상한까지만 메운다 — 진짜 국적은 절대 안 건드린다
        # (database.quota_local_country 컬럼 주석 참고).
        _already = sum(1 for r in foreigners if (r["quota_local_country"] or "") == cname)
        _budget = FOREIGN_NATURALIZE_MAX_PER_TEAM - _already
        if _budget <= 0:
            continue
        _moved = {pid for _t, pid in moves}
        _not_yet = [r for r in _movable
                    if (r["quota_local_country"] or "") != cname and r["id"] not in _moved]
        _not_yet.sort(key=lambda r: r["ovr"] or 0)
        for r in _not_yet[:min(left, _budget)]:
            naturalize.append((cname, r["id"]))

    if moves:
        c.executemany("UPDATE ai_players SET team_id=?, last_transfer_year=? WHERE id=?",
                      [(t, year, pid) for t, pid in moves])
    # [2026-09 신설] 새 소속 기준 연봉 반영 + 이동 기록 — 위 맞교환 블록
    # 주석 참고. 반드시 team_id UPDATE 뒤에 실행한다(순서 자체는 서로
    # 독립이지만, 실패 시 "소속만 바뀌고 연봉은 옛 값"이 남는 게
    # "연봉만 바뀌고 소속은 그대로"보다 덜 이상하다).
    if _salary_updates:
        c.executemany("UPDATE ai_players SET salary=? WHERE id=?", _salary_updates)
    if _qlog_rows:
        c.executemany(
            """INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            _qlog_rows)
    if naturalize:
        c.executemany("UPDATE ai_players SET quota_local_country=? WHERE id=?", naturalize)
    return n_swap, len(naturalize)


def _league_standings(c, season):
    """방금 끝난 시즌의 리그별 순위를 한 번에 계산한다 — {team_id: (rank, total)}.

    [왜 teams.wins를 안 쓰는가] game_engine._end_of_season이
    run_ai_offseason보다 **먼저** teams의 전적을 0으로 밀어버린다(그 리셋
    코드 주석 참고). 그래서 이 시점에 살아있는 유일한 근거는
    match_results다. 리그·팀별로 한 번만 훑으므로(시즌당 17만 행 수준)
    팀마다 따로 계산하는 _infer_team_ambition을 11,000번 부르는 것과는
    비용이 비교가 안 된다."""
    stats = {}
    league_of = {}
    for row in c.execute(
            """SELECT league_id, home_team_id, away_team_id, home_score, away_score
               FROM match_results WHERE season=? AND home_score>=0""", (season,)):
        lid = row["league_id"]
        for tid, gf, ga in ((row["home_team_id"], row["home_score"], row["away_score"]),
                            (row["away_team_id"], row["away_score"], row["home_score"])):
            league_of[tid] = lid
            s = stats.get(tid)
            if s is None:
                s = stats[tid] = [0, 0, 0]      # [pts, gd, gf]
            if gf > ga:
                s[0] += 3
            elif gf == ga:
                s[0] += 1
            s[1] += gf - ga
            s[2] += gf

    by_league = {}
    for tid, lid in league_of.items():
        by_league.setdefault(lid, []).append(tid)

    out = {}
    for lid, tids in by_league.items():
        if len(tids) < 4:
            continue        # 표본이 너무 작으면 순위 백분위가 의미 없다
        tids.sort(key=lambda t: (-stats[t][0], -stats[t][1], -stats[t][2], t))
        total = len(tids)
        for i, tid in enumerate(tids):
            out[tid] = (i + 1, total)
    return out


def _manager_job_level(league_grade, tier, prestige):
    """[2026-09 신설 — 감독 시스템 ③-b] "이 자리가 얼마나 높은 수준인가"를
    0~100 하나로 접는다.

    신민용 지적: "티어는 어차피 S A 이런식으로 되어있어 리그도 국가도."
    그래서 새 티어 체계를 만들지 않고 이미 있는 세 값을 그대로 쓴다 —
    리그 등급(5대 리그가 S), 리그 부수(1부/2부/3부), 구단 위상(맨시티와
    승격팀을 같은 자리로 보면 안 된다). 값의 근거는 constants의
    MANAGER_JOB_* 주석 참고."""
    # 식 자체는 constants.manager_job_level 한 곳에만 있다 — 예전엔 이 식이
    # database.py에도 복사돼 있어서 같은 버그를 두 번 고쳐야 했다.
    from constants import manager_job_level
    return manager_job_level(league_grade, tier, prestige)


def _manager_job_fit(job_level, job_kind, mgr, year, country=None,
                     base_level=None, jobless_years=0, ignore_ceiling=False):
    """[2026-09 신설 — 감독 시스템 ③-c] 감독 한 명이 자리 하나에 맞는지
    판정한다. (통과여부, 확률계수, 점수보정) 세 값을 돌려준다.

    신민용 도식 그대로 — 후보 전체 → 상한 검사(너무 높은가) + 하한
    검사(너무 낮은가) → 적합 후보 → 확률:

        · 상한 = 명성 + (감가된) 최고점 + 최근 수준 + 최근 성적
        · 하한 = 최근 수준 × 비율(명성↑ 덜 내려감, 무직↑ 더 내려감)
        · 통과해도 하향 폭만큼 확률이 깎인다(1단계 0.85 / 2단계 0.35 …)

    [클럽 ↔ 대표팀] 같은 종류끼리는 같은 축에서 바로 비교하고, 종류를
    바꾸는 부임에만 MANAGER_KIND_SWITCH_MULT를 곱한다 — 신민용 확정:
    "다만 이건 절대적인 장벽이 아니라 후보 점수/확률의 차이로 두는 게
    좋음." 대표팀 자리 수준은 countries.grade(국대 강도)로 재고, 클럽은
    리그 등급으로 재므로 두 축의 숫자를 비슷한 범위에 맞춰 두었다
    (constants.MANAGER_NATIONAL_GRADE_LEVEL 주석 참고).

    [국적] 자국 대표팀이면 점수에 약한 가산점만 준다. 필터가 아니다 —
    "감독 국적 ≠ 대표팀 국적"이고, 유럽 감독이 다른 나라 대표팀을 맡는
    건 자연스러운 커리어다.

    mgr: managers 행(dict). base_level: 하향 폭을 재는 기준(현직이면 지금
    자리 수준, 무직이면 recent_level). country: 대표팀 자리의 국가명."""
    from constants import (manager_max_level, manager_career_floor,
                           manager_step_down_mult, MANAGER_KIND_SWITCH_MULT,
                           MANAGER_NAT_PREF_BONUS)
    rep = float(mgr.get("reputation") or 0.0)
    rlv = float(mgr.get("recent_level") or 0.0)
    cbl = float(mgr.get("career_best_level") or 0.0)
    bly = int(mgr.get("best_level_year") or 0)
    rperf = float(mgr.get("recent_perf") or 0.0)
    ysb = max(0, int(year) - bly) if bly else 0
    base = float(base_level if base_level is not None else rlv)

    # ignore_ceiling: 상위 공석에 아무도 상한을 통과하지 못한 마지막 패스.
    # 신인을 만드는 것보다 한 단계 아래에서 끌어올리는 게 현실적이다.
    if not ignore_ceiling and job_level > manager_max_level(cbl, rlv, rep, ysb, rperf):
        return (False, 0.0, 0.0)          # 상한 초과 — 너무 높은 자리
    if job_level < manager_career_floor(base, rep, jobless_years):
        return (False, 0.0, 0.0)          # 하한 미달 — 추락 방지
    mult = manager_step_down_mult(base, job_level)
    # 종류 전환(클럽↔대표팀)은 확률을 낮춘다. 막지는 않는다.
    prev_kind = (mgr.get("_job_kind") or "club")
    if (job_kind or "club") != prev_kind:
        mult *= MANAGER_KIND_SWITCH_MULT
    bonus = 0.0
    if job_kind == "nation" and country and (mgr.get("nationality") or "") == country:
        bonus = MANAGER_NAT_PREF_BONUS    # 자국 선호 — 약한 가산점
    return (True, mult, bonus)


def _manager_season_honours(c, year):
    """[2026-09 신설 — 감독 시스템 ③-c] 그 해 우승한 팀을 컵/대륙 두 갈래로
    모아 {team_id: [cup수, cont수]}로 돌려준다.

    [왜 이 시점에 모으는가] 나중에 다시 계산할 수가 없다. AI 클럽의 경기
    행은 아카이빙(_summarize_and_prune_archive)이 지우고, 리그 우승은
    애초에 어디에도 저장되지 않는다 — trophy_log는 플레이어 소속팀만
    넣는다(game_engine의 champ_team_id 게이트). 그래서 실적은 발생한
    시즌에 감독 행으로 옮겨 담는 수밖에 없다.

    리그 우승은 여기서 세지 않는다 — 시장이 이미 갖고 있는 순위
    (_league_standings의 rank==1)로 판정하므로 질의를 아낀다.

    표가 없는 세이브(하부컵처럼 나라별 지연 생성)는 건너뛴다. 시즌당
    질의 8회로 끝나므로 감독 수와 무관하게 싸다."""
    import sqlite3
    out = {}
    # (표, 어느 갈래) — 국내컵/하부컵/국내슈퍼컵은 cup, 대륙대회+클럽월드컵은 cont
    for tbl, slot in (("cup_tournaments", 0), ("lower_cup_tournaments", 0),
                      ("domestic_sc_tournaments", 0),
                      ("cl_tournaments", 1), ("el_tournaments", 1),
                      ("ecl_tournaments", 1), ("sc_tournaments", 1),
                      ("cwc_tournaments", 1)):
        try:
            rows = c.execute(
                f"SELECT winner_team_id AS w FROM {tbl} "
                f"WHERE year=? AND status='done' AND winner_team_id IS NOT NULL "
                f"AND winner_team_id != 0", (year,)).fetchall()
        except sqlite3.OperationalError:
            continue        # 이 세이브에 아직 없는 표
        for r in rows:
            tid = int(r["w"] or 0)
            if tid:
                out.setdefault(tid, [0, 0])[slot] += 1
    return out


# 마지막 _manager_turnover 호출의 시장 통계 — QA(tools/manager_market_qa.py)가
# 읽는다. 게임 로직은 쓰지 않는다.
_LAST_MANAGER_MARKET = {}


def _manager_turnover(c, year, season=None):
    """[2026-09 재설계 — 감독 시스템 ③-b] 시즌 종료 시 감독 **직업 시장**을
    한 번 돌린다.

    신민용 확정: "경질은 감독 이동의 한 종류일 뿐이어야 해. 감독 이동을
    '경질 시스템'이 아니라 '감독 커리어 이동 시스템'으로 보는 게 맞다."

    ③까지는 이동 사유가 경질 하나뿐이었다. 그래서 감독이 계속 잘리기만
    하고 아무도 다른 팀으로 못 갔다 — 6시즌 실측에서 13,320명 전원이 재임
    이력 1건, 즉 두 번째 팀을 맡아본 감독이 **0명**이었고 무직 1,923명이
    그대로 방치됐다. 이제 신민용이 제시한 순서 그대로 돈다:

        ① 계약 상태 확인 → ② 계약 종료인가 → ③ 구단이 재계약을 원하는가
        → ④ 경질 압박이 있는가 → ⑤ 공석이 있는가 → ⑥ 현직이 영입 후보인가
        → ⑦ 이직/잔류 → ⑧ 남은 공석만 무직 풀·신인으로 채움

    [목표 미달 ≠ 즉시 경질] 신민용 강조: "우승 목표인데 2위면 굳이 경질할
    이유가 없다. 9위면 압박이 매우 커진다." 그래서 경질은 **계약 중 + 목표
    대비 미달 폭**으로만 굴리고, 계약이 끝난 감독은 경질이 아니라 재계약
    협상으로 간다.

    [티어] 새 체계를 만들지 않는다 — _manager_job_level 주석 참고.

    [결정성] ①~③과 같은 원칙. 전역 random을 건드리지 않고 (salt, 연도,
    팀)에서 파생한 random.Random만 쓴다.

    반환: 이번 시즌에 감독이 바뀐 team_id 집합.
    """
    from constants import (MANAGER_AMBITION_TARGET_PCT, MANAGER_SACK_BASE,
                           MANAGER_SACK_MISS_SLOPE, MANAGER_SACK_HONEYMOON_YEARS,
                           MANAGER_SACK_HONEYMOON_MULT, MANAGER_SACK_TENURE_SAFE_YEARS,
                           MANAGER_SACK_TENURE_SAFE_MULT, MANAGER_SACK_MIN,
                           MANAGER_SACK_MAX, BIG_CLUB_PRESTIGE_THRESHOLD,
                           MANAGER_CONTRACT_YEARS_MIN, MANAGER_CONTRACT_YEARS_MAX,
                           MANAGER_RENEW_BASE, MANAGER_RENEW_PERF_SLOPE,
                           MANAGER_RENEW_MIN, MANAGER_RENEW_MAX,
                           MANAGER_MOVE_MIN_GAIN, MANAGER_MOVE_ACCEPT_BASE,
                           MANAGER_MOVE_ACCEPT_SLOPE, MANAGER_MOVE_ACCEPT_MAX,
                           MANAGER_MOVE_MAX_SHARE, MANAGER_HIRE_MAX_UNDERQUALIFIED,
                           MANAGER_HIRE_NOISE, MANAGER_HIRE_ROOKIE_SHARE,
                           MANAGER_HIRE_STRETCH_EXTRA, MANAGER_ROOKIE_FULL_LEVEL,
                           MANAGER_ROOKIE_MAX_LEVEL, MANAGER_ROOKIE_AGE_MIN,
                           MANAGER_ROOKIE_AGE_MAX,
                           MANAGER_RETIRE_AGE_START, MANAGER_RETIRE_AGE_SLOPE,
                           MANAGER_RETIRE_AGE_HARD, MANAGER_RETIRE_JOBLESS_YEARS,
                           MANAGER_RETIRE_JOBLESS_SLOPE,
                           get_country_league_grade,
                           # ③-c 실적/명성 + 상한/하한
                           MANAGER_CAND_WINDOW, MANAGER_REP_RECENT_ALPHA,
                           MANAGER_KIND_SWITCH_MULT,
                           manager_reputation, manager_max_level,
                           manager_career_floor, manager_step_down_mult)
    from database import build_manager_row, MANAGER_INSERT_SQL, get_world_salt
    from data.prestige_clubs import prestige_level
    import zlib

    global _LAST_MANAGER_MARKET
    year = int(year)
    if season is None:
        row = c.execute(
            "SELECT MAX(season) AS s FROM match_results WHERE year=?", (year,)).fetchone()
        season = row["s"] if row else None
        if season is None:
            return set()

    ranks = _league_standings(c, season)
    if not ranks:
        return set()

    promo = {}
    try:
        for r in c.execute(
                "SELECT team_name, from_tier, to_tier FROM promotion_log WHERE year=?",
                (year,)):
            promo[r["team_name"]] = (r["from_tier"], r["to_tier"])
    except Exception:
        promo = {}

    # ── 팀 메타 + 직장 수준 ──────────────────────────────────────
    teams = {}
    for r in c.execute(
            """SELECT t.id, t.name, t.club_ambition, cn.name AS country,
                      lg.tier AS tier
               FROM teams t
               LEFT JOIN countries cn ON cn.id = t.country_id
               LEFT JOIN leagues lg ON lg.id = t.league_id"""):
        country = r["country"] or ""
        lvl = _manager_job_level(get_country_league_grade(country), r["tier"],
                                 prestige_level(country, r["name"]) if country else 0)
        teams[r["id"]] = {"id": r["id"], "name": r["name"], "country": country,
                          "amb": (r["club_ambition"] or "").strip(), "level": lvl}

    cur_mgr = {r["team_id"]: dict(r) for r in c.execute(
        """SELECT tm.id AS link_id, tm.team_id, tm.manager_id, tm.start_year
           FROM team_managers tm WHERE tm.end_year IS NULL AND tm.job_kind='club'""")}
    mgrs = {r["id"]: dict(r) for r in c.execute(
        """SELECT id, birth_year, contract_until, status, jobless_since,
                  career_best_level, clubs_managed, retired, nationality,
                  reputation, recent_level, career_floor, best_level_year,
                  seasons_managed, level_sum, titles_league, titles_cup,
                  titles_cont, titles_intl, target_hit, target_miss, recent_perf
           FROM managers WHERE retired=0""")}
    honours = _manager_season_honours(c, year)

    salt = get_world_salt()
    mrng = random.Random(zlib.crc32(f"mkt:{salt}:{year}".encode("utf-8")))

    amb_updates = []        # (ambition, team_id)
    tenure_updates = []     # (이번시즌 우승수, 리그순위, link_id) — ③-c 임기 누적
    ends = []               # (end_year, end_reason, link_id)
    renews = []             # (contract_until, manager_id)
    vacancies = []          # team_id — 채워야 할 자리
    stats = {"sacked": 0, "contract_end": 0, "renewed": 0, "moved": 0,
             "rehired": 0, "rookie": 0, "retired_age": 0, "retired_jobless": 0,
             "stretched": 0, "rookie_forced": 0}

    # ══ ①~④ 현직 평가: 재계약 / 계약 종료 / 경질 / 잔류 ══════════
    for tid, (rank, total) in ranks.items():
        t = teams.get(tid)
        if not t:
            continue
        pct = rank / total

        pl = promo.get(t["name"])
        if pl:
            amb = "강등 회피" if pl[1] < pl[0] else "우승 도전"
        elif (t["country"]
              and (prestige_level(t["country"], t["name"]) or 0) >= BIG_CLUB_PRESTIGE_THRESHOLD):
            amb = "우승 도전"
        elif pct <= 1 / 3:
            amb = "우승 도전"
        elif pct <= 2 / 3:
            amb = "상위권 도전"
        else:
            amb = "중위권 안정"
        amb_updates.append((amb, tid))

        link = cur_mgr.get(tid)
        if not link:
            vacancies.append(tid)          # 감독이 없는 팀도 공석이다
            continue
        m = mgrs.get(link["manager_id"])
        if not m:
            continue

        # 평가 기준은 "시즌에 들어갈 때 갖고 있던 목표"(③에서 잡은 설계).
        judge_amb = t["amb"] if t["amb"] in MANAGER_AMBITION_TARGET_PCT else amb
        target = MANAGER_AMBITION_TARGET_PCT.get(judge_amb, 0.65)
        rng = random.Random(zlib.crc32(f"job:{salt}:{tid}:{year}".encode("utf-8")))

        # ══ ③-c 실적 누적 — 거취 판정보다 먼저 한다 ══════════════
        # 이 시즌을 마친 건 사실이므로 경질되든 남든 실적은 쌓인다.
        # 여기서만 누적하면 되는 이유: 재직 중인 감독은 전부 ranks에
        # 들어오고(리그 소속 팀), 무직 감독은 쌓을 실적이 없다.
        m["seasons_managed"] = int(m.get("seasons_managed") or 0) + 1
        m["level_sum"] = float(m.get("level_sum") or 0.0) + t["level"]
        if pct <= target:
            m["target_hit"] = int(m.get("target_hit") or 0) + 1
        else:
            m["target_miss"] = int(m.get("target_miss") or 0) + 1
        # 최근 성적 EWMA — 목표 대비 초과 달성이 +, 미달이 −(−1~+1).
        perf = max(-1.0, min(1.0, (target - pct) / max(0.15, target)))
        m["recent_perf"] = ((1.0 - MANAGER_REP_RECENT_ALPHA)
                            * float(m.get("recent_perf") or 0.0)
                            + MANAGER_REP_RECENT_ALPHA * perf)
        # 우승 — 리그는 순위로, 컵/대륙은 _manager_season_honours로.
        t_titles = 0
        if rank == 1:
            m["titles_league"] = int(m.get("titles_league") or 0) + 1
            t_titles += 1
        hon = honours.get(tid)
        if hon:
            m["titles_cup"] = int(m.get("titles_cup") or 0) + hon[0]
            m["titles_cont"] = int(m.get("titles_cont") or 0) + hon[1]
            t_titles += hon[0] + hon[1]
        # 명성 재계산 — 누적값에서 매번 다시 접는다(증분 가산이 아니라
        # 순수 함수라, 가중치를 바꾸면 다음 시즌부터 전원 같은 기준이 된다).
        seasons = max(1, int(m["seasons_managed"]))
        m["reputation"] = manager_reputation(
            titles_league=m.get("titles_league") or 0,
            titles_cup=m.get("titles_cup") or 0,
            titles_cont=m.get("titles_cont") or 0,
            titles_intl=m.get("titles_intl") or 0,
            avg_level=float(m["level_sum"]) / seasons,
            target_hit=m.get("target_hit") or 0,
            target_miss=m.get("target_miss") or 0,
            recent_perf=m["recent_perf"])
        # 재직 중이므로 최근 수준은 이 자리다. 최고점도 여기서 갱신한다.
        m["recent_level"] = t["level"]
        if t["level"] > float(m.get("career_best_level") or 0.0):
            m["career_best_level"] = t["level"]
            m["best_level_year"] = year
        m["_dirty"] = True
        # 임기 누적 — 커리어 화면용(우승 수 / 최고 순위).
        tenure_updates.append((t_titles, rank, link["link_id"]))

        contract_until = int(m.get("contract_until") or 0)
        if contract_until and contract_until > year:
            # ── 계약 중 → 경질 판정만 ────────────────────────────
            miss = max(0.0, pct - target)
            prob = MANAGER_SACK_BASE.get(judge_amb, 0.06) + miss * MANAGER_SACK_MISS_SLOPE
            prob = max(MANAGER_SACK_MIN, min(MANAGER_SACK_MAX, prob))
            tenure = max(0, year - int(link["start_year"] or year))
            if tenure <= MANAGER_SACK_HONEYMOON_YEARS:
                prob *= MANAGER_SACK_HONEYMOON_MULT
            elif tenure >= MANAGER_SACK_TENURE_SAFE_YEARS:
                prob *= MANAGER_SACK_TENURE_SAFE_MULT
            prob = min(MANAGER_SACK_MAX, prob)
            if rng.random() < prob:
                ends.append((year, "sacked", link["link_id"]))
                vacancies.append(tid)
                stats["sacked"] += 1
                m["_left"] = True
            continue

        # ── 계약 만료 → 재계약 협상 ──────────────────────────────
        # 목표보다 잘했으면(target - pct > 0) 재계약 확률이 오르고,
        # 못했으면 내려간다. 경질과 달리 여기선 성적이 나빠도 "재계약을
        # 안 할 뿐"이라 감독은 경질 낙인 없이 자유계약이 된다.
        rprob = MANAGER_RENEW_BASE + (target - pct) * MANAGER_RENEW_PERF_SLOPE
        rprob = max(MANAGER_RENEW_MIN, min(MANAGER_RENEW_MAX, rprob))
        if rng.random() < rprob:
            renews.append((year + rng.randint(MANAGER_CONTRACT_YEARS_MIN,
                                              MANAGER_CONTRACT_YEARS_MAX),
                           link["manager_id"]))
            stats["renewed"] += 1
        else:
            ends.append((year, "contract_end", link["link_id"]))
            vacancies.append(tid)
            stats["contract_end"] += 1
            m["_left"] = True

    # ══ 은퇴 — 고령 / 장기 실직 ═══════════════════════════════════
    retire_ids = []
    for mid, m in mgrs.items():
        # [2026-09 — ③-d] 현직 대표팀 감독은 여기서 은퇴시키지 않는다.
        # 여기서 retired=1로 만들면 national_team_managers의 재임 행은
        # end_year가 NULL로 남아 **은퇴한 사람이 대표팀 감독으로 계속 등재**
        # 된다(실측 45건). 대표팀 감독의 은퇴는 대회 평가 시점에
        # national_manager가 처리한다 — 거기서 재임을 닫고 같은 패스에서
        # 후임까지 세우므로 "감독 없는 대표팀"이 생기지 않는다.
        if (m.get("status") or "") == "nation":
            continue
        in_job = (m.get("status") == "club" and not m.get("_left"))
        age = year - int(m.get("birth_year") or year)
        rr = random.Random(zlib.crc32(f"ret:{salt}:{mid}:{year}".encode("utf-8")))
        p_age = 0.0
        if age >= MANAGER_RETIRE_AGE_HARD:
            p_age = 1.0
        elif age > MANAGER_RETIRE_AGE_START:
            p_age = (age - MANAGER_RETIRE_AGE_START) * MANAGER_RETIRE_AGE_SLOPE
        p_ret = p_age
        js = m.get("jobless_since")
        if js and not in_job:
            jl = year - int(js)
            if jl >= MANAGER_RETIRE_JOBLESS_YEARS:
                p_ret = max(p_ret,
                            (jl - MANAGER_RETIRE_JOBLESS_YEARS + 1) * MANAGER_RETIRE_JOBLESS_SLOPE)
        if p_ret > 0 and rr.random() < p_ret:
            # 재직 중인 감독이 은퇴하면 그 자리도 공석이 된다.
            if in_job and not m.get("_left"):
                for _tid, _lk in cur_mgr.items():
                    if _lk["manager_id"] == mid:
                        ends.append((year, "retired", _lk["link_id"]))
                        vacancies.append(_tid)
                        break
            retire_ids.append(mid)
            m["_retired"] = True
            # 사유는 확률을 만든 항으로 나눈다 — 나이 확률이 0인데 은퇴했다면
            # 장기 실직 쪽이다(예전엔 age>=START로 갈라서 62세 실직 은퇴가
            # 고령 은퇴로 잡혔다).
            if p_age > 0:
                stats["retired_age"] += 1
            else:
                stats["retired_jobless"] += 1

    # ══ ⑤~⑧ 공석 채우기 ══════════════════════════════════════════
    # 좋은 자리부터 채운다 — 실제 시장도 빅클럽이 먼저 움직이고, 그
    # 연쇄로 아래 자리가 비는 순서다.
    left_link = {int(l) for (_y, _r, l) in ends}
    busy = {}          # manager_id -> 현재 팀(아직 안 떠난 현직)
    for tid, lk in cur_mgr.items():
        if lk["link_id"] not in left_link:
            busy[lk["manager_id"]] = tid

    # [2026-09 — ③-d] 현직 대표팀 감독은 클럽 후보가 아니다. 신민용 확정:
    # "한 감독의 active job은 하나만 존재하게 하는 거야." 이 감독들은
    # team_managers에 현직 행이 없으므로 busy에 안 들어가고, 그대로 두면
    # **무직으로 오인되어 클럽에 부임한다**(그러면 한 감독이 대표팀과 클럽을
    # 동시에 맡는다). status로 걸러낸다.
    free_ids = [mid for mid, m in mgrs.items()
                if not m.get("_retired") and mid not in busy
                and (m.get("status") or "") != "nation"]
    # 경력 수준으로 버킷 — 공석마다 전체 풀을 훑으면 11,000×수천이라 느리다.
    BIN = 5.0
    free_bins = {}
    for mid in free_ids:
        b = int((mgrs[mid].get("career_best_level") or 0.0) // BIN)
        free_bins.setdefault(b, []).append(mid)
    for b in free_bins:
        free_bins[b].sort()
    # 현직 이직 후보도 같은 방식으로 버킷(현재 자리 수준 기준).
    move_bins = {}
    for mid, tid in busy.items():
        lv = teams.get(tid, {}).get("level", 0.0)
        move_bins.setdefault(int(lv // BIN), []).append(mid)
    for b in move_bins:
        move_bins[b].sort()

    move_budget = int(len(busy) * MANAGER_MOVE_MAX_SHARE)
    taken = set()
    new_rows, new_links, new_contracts = [], [], []
    hires = []          # (manager_id, team_id, contract_until)
    changed = set()
    tend_updates = []

    def _pick_from(bins, lo_bin, hi_bin, limit=24):
        out = []
        for b in range(lo_bin, hi_bin + 1):
            for mid in bins.get(b, ()):
                if mid in taken:
                    continue
                out.append(mid)
                if len(out) >= limit:
                    return out
        return out

    # [2026-10 병역 시스템] 군팀 감독은 일반 감독 시장과 분리한다(신민용 확정:
    # 군대 감독은 무조건 한국인). 군팀 공석은 무직인 한국인 감독만 후보(현직
    # 이직 불가), 일반 클럽 공석은 군팀 현직 감독을 빼 간다. 후보가 없으면
    # 아래 신인 생성으로 가고, 그 국적은 manager_nationality_for로 대한민국.
    # 군대가 없으면 빈 집합이라 아래 필터가 아예 안 돈다(결과 불변).
    from military_service import get_military_team_ids as _get_mil_tids_mgr
    from constants import MILITARY_TARGET_NATIONALITY as _MIL_NAT
    _mil_tids_mgr = _get_mil_tids_mgr(c)
    queue = list(dict.fromkeys(vacancies))     # 중복 제거, 순서 유지
    queue.sort(key=lambda t: -teams.get(t, {}).get("level", 0.0))
    guard = 0
    while queue and guard < len(teams) * 2:
        guard += 1
        tid = queue.pop(0)
        t = teams.get(tid)
        if not t:
            continue
        lv = t["level"]
        vr = random.Random(zlib.crc32(f"hire:{salt}:{tid}:{year}".encode("utf-8")))

        # 신인을 데뷔시킬 수 있는 자리인가 — 하위 자리는 자유롭고, 높은
        # 자리는 경력자만 간다. 이 게이트가 없으면 5대 리그 공석이 신인으로
        # 채워진다(실측: 10시즌간 1부 데뷔 45명).
        if lv <= MANAGER_ROOKIE_FULL_LEVEL:
            rookie_p = MANAGER_HIRE_ROOKIE_SHARE
        elif lv >= MANAGER_ROOKIE_MAX_LEVEL:
            rookie_p = 0.0
        else:
            rookie_p = MANAGER_HIRE_ROOKIE_SHARE * (
                (MANAGER_ROOKIE_MAX_LEVEL - lv)
                / (MANAGER_ROOKIE_MAX_LEVEL - MANAGER_ROOKIE_FULL_LEVEL))

        chosen = None
        if vr.random() >= rookie_p:
            # 후보 창은 상한과 같다. 한 바퀴에서 아무도 못 구하면(후보가
            # 없거나 전원이 이직을 거절) 상한을 STRETCH만큼 넓혀 다시 훑는다
            # — "한 단계 아래에서 끌어올리기".
            # 3패스. 1) 정상 2) 끌어올리기(상한·창 넓힘) 3) 상한 무시.
            # 3패스가 필요한 이유: 상위 공석에서 아무도 상한을 통과하지
            # 못하면 예전 코드는 **신인을 만들었다**(실측: 데뷔 상한 초과
            # 571명, 그중 9명은 빅클럽). 현실에선 그럴 때 신인을 데려오는 게
            # 아니라 한 단계 아래에서 될 만한 사람을 끌어올린다. 하한은
            # 3패스에서도 계속 지킨다 — 추락 방지가 ③-c의 핵심이라.
            _caps = [MANAGER_CAND_WINDOW,
                     MANAGER_CAND_WINDOW + MANAGER_HIRE_STRETCH_EXTRA]
            if lv >= MANAGER_ROOKIE_MAX_LEVEL:
                _caps.append(None)          # None = 상한 검사 생략
            for cap in _caps:
                no_ceiling = cap is None
                if no_ceiling:
                    cap = MANAGER_CAND_WINDOW + MANAGER_HIRE_STRETCH_EXTRA
                # 두 번째 패스에서는 상한도 같이 넓힌다(끌어올리기).
                cap_slack = cap - MANAGER_CAND_WINDOW
                lo = int((lv - cap) // BIN)
                hi = int((lv + 25.0) // BIN)
                cands = []
                # 현직 이직 후보 — 지금 자리보다 충분히 좋은 자리일 때만.
                if move_budget > 0:
                    for mid in _pick_from(move_bins, lo, hi, limit=12):
                        cur_lv = teams.get(busy.get(mid), {}).get("level", 0.0)
                        if lv - cur_lv >= MANAGER_MOVE_MIN_GAIN:
                            cands.append((mid, True))
                for mid in _pick_from(free_bins, lo, hi, limit=24):
                    cands.append((mid, False))
                if _mil_tids_mgr:
                    if tid in _mil_tids_mgr:
                        cands = [(m_, mv_) for (m_, mv_) in cands
                                 if not mv_ and (mgrs[m_].get("nationality") or "") == _MIL_NAT]
                    else:
                        cands = [(m_, mv_) for (m_, mv_) in cands
                                 if not (mv_ and busy.get(m_) in _mil_tids_mgr)]
                # ══ ③-c 상한/하한 검사 ══════════════════════════
                # 신민용 도식: 후보 전체 → 상한 검사(너무 높은가) +
                # 하한 검사(너무 낮은가) → 적합 후보 → 확률.
                # ③-b의 "경력이 자리보다 22 넘게 낮으면 제외"를 이것으로
                # 대체한다 — 그 규칙은 상한만 있고 하한이 없어서 EPL
                # 감독이 7부로 떨어지는 걸 못 막았다.
                scored = []
                for mid, is_move in cands:
                    mm = mgrs[mid]
                    # 현직(이직)은 지금 자리가 하향 기준이고, 무직은
                    # recent_level 기준에 무직 기간만큼 눈을 낮춘다.
                    if is_move:
                        jl_years = 0
                        base_lv = teams.get(busy.get(mid), {}).get(
                            "level", float(mm.get("recent_level") or 0.0))
                    else:
                        js = mm.get("jobless_since")
                        jl_years = max(0, year - int(js)) if js else 0
                        base_lv = float(mm.get("recent_level") or 0.0)
                    ok, step, bonus = _manager_job_fit(
                        lv - cap_slack, "club", mm, year,
                        base_level=base_lv, jobless_years=jl_years,
                        ignore_ceiling=no_ceiling)
                    if not ok:
                        continue
                    # 자리 수준과 경력 수준이 가까울수록 좋은 후보 +
                    # 명성이 높으면 좋은 자리에서 먼저 뽑힌다.
                    rep = float(mm.get("reputation") or 0.0)
                    cbl = float(mm.get("career_best_level") or 0.0)
                    rlv = float(mm.get("recent_level") or 0.0)
                    score = (-abs(max(cbl, rlv) - lv) + rep * 0.12 + bonus
                             + vr.uniform(0, MANAGER_HIRE_NOISE))
                    scored.append((score, mid, is_move, base_lv))

                # 점수 순으로 **차례대로** 시도한다. 1순위만 보고 끝내면 그
                # 후보가 현직이고 이직을 거절했을 때 곧바로 신인 생성으로
                # 떨어진다 — 그래서 빅클럽 공석이 신인에게 갔다(실측: 데뷔
                # 상한 초과 31명, 그 시즌 끌어올림 0건. 후보가 없어서가 아니라
                # 거절당해서였다).
                scored.sort(key=lambda x: -x[0])
                for _s, mid, is_move, base_lv in scored:
                    # ③-c 하향 폭 계수 — 하한 안쪽이라도 많이 내려가는
                    # 자리는 잘 안 간다(같은 수준 1.0 / 1단계 0.85 /
                    # 2단계 0.35 / 3단계 0.10).
                    step = manager_step_down_mult(base_lv, lv)
                    if not is_move:
                        # 무직의 재취업 — 하향 폭만 본다(무직은 이미
                        # 실직 기간만큼 하한이 내려가 있다).
                        if step < 1.0 and vr.random() >= step:
                            continue
                        chosen = mid
                        stats["rehired"] += 1
                        break
                    cur_lv = teams.get(busy[mid], {}).get("level", 0.0)
                    acc = min(MANAGER_MOVE_ACCEPT_MAX,
                              MANAGER_MOVE_ACCEPT_BASE
                              + (lv - cur_lv) * MANAGER_MOVE_ACCEPT_SLOPE) * step
                    if vr.random() >= acc:
                        continue               # 거절 — 다음 후보로
                    old_tid = busy.pop(mid)
                    old_link = cur_mgr.get(old_tid)
                    if old_link:
                        ends.append((year, "moved", old_link["link_id"]))
                    queue.append(old_tid)      # 떠난 자리가 새 공석
                    queue.sort(key=lambda x: -teams.get(x, {}).get("level", 0.0))
                    move_budget -= 1
                    stats["moved"] += 1
                    chosen = mid
                    break

                if chosen is not None:
                    if cap_slack > 0:
                        stats["stretched"] += 1
                    break
                # 넓혀도 의미가 없는 자리(신인이 가도 되는 낮은 자리)면
                # 두 번째 패스를 돌지 않는다.
                if lv < MANAGER_ROOKIE_MAX_LEVEL:
                    break

        if chosen is None and lv >= MANAGER_ROOKIE_MAX_LEVEL:
            stats["rookie_forced"] += 1

        if chosen is not None:
            taken.add(chosen)
            cu = year + vr.randint(MANAGER_CONTRACT_YEARS_MIN, MANAGER_CONTRACT_YEARS_MAX)
            # 4번째는 **그 자리의 수준 그대로**다(예전엔 career_best와의 max를
            # 넣었다). career_best는 아래 SQL이 MAX로 올리고, recent_level과
            # 임기 job_level은 실제 자리 수준이어야 하므로 원값이 필요하다.
            hires.append((chosen, tid, cu, lv))
        else:
            # 신인 — 새 감독을 만든다.
            # [2026-10] 군팀 감독은 국적을 "군대"가 아니라 대한민국으로.
            from military_service import manager_nationality_for as _mil_mgr_nat
            row = build_manager_row(vr, _mil_mgr_nat(t["country"]), year,
                                    age_range=(MANAGER_ROOKIE_AGE_MIN,
                                               MANAGER_ROOKIE_AGE_MAX))
            new_rows.append(row)
            new_links.append((tid, year + vr.randint(MANAGER_CONTRACT_YEARS_MIN,
                                                     MANAGER_CONTRACT_YEARS_MAX), lv))
            tend_updates.append((row[5], tid))
            stats["rookie"] += 1
        changed.add(tid)

    # ══ DB 반영 ═══════════════════════════════════════════════════
    if amb_updates:
        c.executemany("UPDATE teams SET club_ambition=? WHERE id=?", amb_updates)
    # [③-c] 이번 시즌 실적/명성 누적. 거취(경질·이직·재계약)와 무관하게
    # 먼저 써야 한다 — 경질된 감독도 그 시즌을 치렀고, 그 실적이 다음
    # 직장을 구하는 근거가 된다.
    _rep_rows = [(m["reputation"], m["recent_level"], m["career_best_level"],
                  m["best_level_year"], m["seasons_managed"], m["level_sum"],
                  m["titles_league"], m["titles_cup"], m["titles_cont"],
                  m["target_hit"], m["target_miss"], m["recent_perf"],
                  manager_career_floor(m["recent_level"], m["reputation"], 0),
                  mid)
                 for mid, m in mgrs.items() if m.get("_dirty")]
    if _rep_rows:
        c.executemany(
            """UPDATE managers SET reputation=?, recent_level=?,
                      career_best_level=?, best_level_year=?, seasons_managed=?,
                      level_sum=?, titles_league=?, titles_cup=?, titles_cont=?,
                      target_hit=?, target_miss=?, recent_perf=?, career_floor=?
               WHERE id=?""", _rep_rows)
    if tenure_updates:
        c.executemany(
            "UPDATE team_managers SET titles=COALESCE(titles,0)+?, "
            "best_rank=CASE WHEN COALESCE(best_rank,0)=0 OR ? < best_rank "
            "               THEN ? ELSE best_rank END WHERE id=?",
            [(tt, rk, rk, lid) for (tt, rk, lid) in tenure_updates])
    if ends:
        c.executemany(
            "UPDATE team_managers SET end_year=?, end_reason=? WHERE id=?", ends)
    if renews:
        c.executemany(
            "UPDATE managers SET contract_until=?, status='club', jobless_since=NULL "
            "WHERE id=?", renews)
    if retire_ids:
        c.executemany("UPDATE managers SET retired=1, status='retired' WHERE id=?",
                      [(i,) for i in retire_ids])
    # 떠났는데 새 자리를 못 구한 감독 → 무직 처리
    hired_ids = {h[0] for h in hires}
    # [2026-09 — ③-d] 현직 대표팀 감독을 여기서 'free'로 덮으면 안 된다.
    # 이들은 team_managers에 현직 행이 없어 busy에도 hired_ids에도 없으므로,
    # 안 걸러내면 **매 시즌 무직으로 강등**되어 위 free_ids 제외가 무력화되고
    # 대표팀 감독이 클럽에 이중 부임한다.
    became_free = [mid for mid in mgrs
                   if mid not in busy and mid not in hired_ids
                   and not mgrs[mid].get("_retired")
                   and (mgrs[mid].get("status") or "") != "nation"]
    if became_free:
        c.executemany(
            "UPDATE managers SET status='free', contract_until=0, "
            "jobless_since=COALESCE(jobless_since, ?) WHERE id=? AND retired=0",
            [(year, mid) for mid in became_free])
    if hires:
        c.executemany(
            """INSERT INTO team_managers(team_id, manager_id, start_year, end_year,
                                          job_kind, country_id, job_level)
               VALUES(?,?,?,NULL,'club',NULL,?)""",
            [(tid, mid, year, lv) for (mid, tid, _cu, lv) in hires])
        # [③-c] recent_level과 best_level_year도 같이 갱신한다. CASE는 SET의
        # 다른 항목에 영향받지 않고 **갱신 전** career_best_level을 본다.
        c.executemany(
            "UPDATE managers SET contract_until=?, status='club', jobless_since=NULL, "
            "best_level_year=CASE WHEN ? > COALESCE(career_best_level,0) "
            "                     THEN ? ELSE best_level_year END, "
            "career_best_level=MAX(COALESCE(career_best_level,0), ?), "
            "recent_level=?, "
            "clubs_managed=COALESCE(clubs_managed,0)+1 WHERE id=?",
            [(cu, lv, year, lv, lv, mid) for (mid, _tid, cu, lv) in hires])
        # 재취업/이직한 감독의 전술 성향을 그 팀의 레거시 컬럼에 반영.
        _hired_tend = c.execute(
            "SELECT id, style_attack FROM managers WHERE id IN (%s)"
            % ",".join("?" * len(hires)), [h[0] for h in hires]).fetchall()
        _tend_by_mid = {r["id"]: r["style_attack"] for r in _hired_tend}
        tend_updates += [(_tend_by_mid.get(mid, "BALANCED"), tid)
                         for (mid, tid, _cu, _lv) in hires]
    if new_rows:
        base_id = c.execute("SELECT COALESCE(MAX(id), 0) FROM managers").fetchone()[0]
        c.executemany(MANAGER_INSERT_SQL, new_rows)
        from database import assign_manager_codes
        assign_manager_codes(c)   # [2026-09] 감독 코드(MG…) 백필
        new_ids = [r[0] for r in c.execute(
            "SELECT id FROM managers WHERE id > ? ORDER BY id", (base_id,)).fetchall()]
        if len(new_ids) == len(new_links):
            c.executemany(
                """INSERT INTO team_managers(team_id, manager_id, start_year, end_year,
                                              job_kind, country_id)
                   VALUES(?,?,?,NULL,'club',NULL)""",
                [(tid, new_ids[i], year) for i, (tid, _cu, _lv) in enumerate(new_links)])
            c.executemany(
                "UPDATE managers SET contract_until=?, status='club', "
                "career_best_level=?, clubs_managed=1 WHERE id=?",
                [(cu, lv, new_ids[i]) for i, (_tid, cu, lv) in enumerate(new_links)])
        else:
            changed = set()
    if tend_updates:
        c.executemany("UPDATE teams SET tactic_tendency=? WHERE id=?", tend_updates)

    stats["vacancies"] = len(set(vacancies))
    stats["free_pool"] = len(became_free)
    _LAST_MANAGER_MARKET = stats
    return changed


def _shuffle_formations(c, forced_teams=None):
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

    # [2026-09 — 감독 시스템 ②단계] 성향의 출처를 팀에서 감독으로 옮긴다.
    # teams.tactic_tendency는 지우지 않고 그대로 두지만(①단계 원칙),
    # 포메이션 재검토는 이제 그 팀 **현재 감독**의 3축을 본다 — 감독이
    # 바뀌면 포메이션 선호도 같이 바뀌는 구조가 여기서 시작된다.
    # 감독이 아직 없는 팀(구세이브의 극초반 등)은 style이 비어 그대로
    # tactic_tendency 한 축 폴백을 탄다(formation_logic.manager_style_fit).
    teams = c.execute("SELECT id, formation, tactic_tendency FROM teams").fetchall()
    try:
        style_by_team = {
            r["team_id"]: {"style_attack": r["style_attack"],
                           "style_buildup": r["style_buildup"],
                           "style_press": r["style_press"]}
            for r in c.execute(
                """SELECT tm.team_id, m.style_attack, m.style_buildup, m.style_press
                   FROM team_managers tm JOIN managers m ON m.id = tm.manager_id
                   WHERE tm.end_year IS NULL""")}
    except Exception:
        style_by_team = {}   # 감독 표가 아직 없는 세이브 — 폴백 경로
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
        # [2026-09 — 감독 시스템 ③단계] 이번 시즌에 감독이 바뀐 팀은 재검토
        # 확률과 무관하게 **반드시** 다시 고른다. 새 감독이 부임했는데
        # 70% 확률로 전임자의 포메이션을 그대로 쓰면 "감독 교체가 게임
        # 이벤트"가 되지 않는다.
        # [난수 주의] 확률 굴림(random.random())은 forced 여부와 상관없이
        # 항상 먼저 소비한다 — 순서를 건너뛰면 그 뒤 난수열이 팀마다 다르게
        # 밀려서 같은 세이브를 다시 돌렸을 때 재현이 안 된다.
        _roll = random.random()
        if _roll < FORMATION_REEVAL_PROB or (forced_teams and t["id"] in forced_teams):
            new_formation, penalty = _flogic.choose_formation_prepped(
                prepped, cur_formation, tendency,
                style=style_by_team.get(t["id"]))
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
    """[2026-09 폐지, 신민용 확정: "names.py는 플레이어 이름 선택용이고,
    AI 선수 이름으로 들어가면 제거해야 해"] 예전엔 player_names 전체를
    {country_id: [name,...]}로 로드해 신인마다 실명을 뽑아 줬다. 그 값은
    ai_players.name에 저장만 되고 화면엔 단 한 번도 안 나온다 — 모든 표시
    경로가 custom_name or ai_player_code(id)로 덮어쓴다(match_sim/
    tactical_engine._display_name 주석 참고). 조회·추첨·중복관리 비용만
    남으므로 폐지하고, 호출부 시그니처는 그대로 둔 채 빈 dict를 준다."""
    return {}


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
    """[2026-09 폐지] AI 선수 실명 배정 폐지 — _build_name_cache 주석 참고.
    항상 빈 문자열을 돌려준다(화면 표시는 ai_player_code(id)/커스텀 이름).
    호출부를 한 번에 다 고치지 않아도 되게 시그니처는 그대로 둔다."""
    return ""


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
    from database import (get_ovr_range, _pick_nationality, get_foreign_quota_range,
                          compute_ai_growth_cap, roll_potential_ovr,
                          is_roster_foreign)

    if not rescale_jobs:
        return 0, 0

    conn = get_conn()
    c = conn.cursor()

    team_ids = [j[0] for j in rescale_jobs]
    ph = ",".join("?" * len(team_ids))
    team_rows = {r["tid"]: r for r in c.execute(
        f"""SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname,
                   cn.continent AS continent, t.name AS tname
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
        # [2026-09 신설, database.roll_potential_ovr 정의부 주석 참고] 이
        # 경로는 승강 직후 스쿼드 교체용이라 star 슬롯 개념이 없다 — 팀
        # 단위 성장 상한만 한 번 구해 일반(normal) 밴드로 배정한다.
        _turnover_growth_cap = compute_ai_growth_cap(grade, tier, cname, continent)

        squad = c.execute(
            "SELECT id, position, nationality FROM ai_players WHERE team_id=? ORDER BY ovr ASC",
            (team_id,)).fetchall()
        n = len(squad)
        if n < 2:
            continue
        n_turn = min(max(1, int(round(n * turnover_frac))), n - 1)
        n_release = max(0, min(n_turn, int(round(n_turn * release_frac_of_turnover))))
        used = set()
        _q_lo, quota = get_foreign_quota_range(cname, continent, tier=tier)
        # [2026-09 버그수정, 신민용 리포트: "5명 한계인데 8명으로 뚫었잖아"]
        # 예전엔 foreign_ct를 0으로 시작했다 — 이 함수는 스쿼드의 하위
        # turnover_frac(25%)만 교체하고 나머지 75%는 그대로 남기는데,
        # 카운터가 0이면 _pick_nationality는 "이 팀엔 외국인이 아직
        # 없다"고 보고 쿼터(5명)를 새로 다 써버린다. 남아 있는 선수의
        # 외국인 수를 세서 그 위에서 이어 센다. 실측(1시즌 계측 하니스):
        # 이 함수 한 번이 초과팀을 869 → 1,270팀(+401)으로 늘리던 최대
        # 단일 원인이었다.
        foreign_ct = sum(1 for _p in squad[n_turn:]
                         if is_roster_foreign(_p["nationality"], cname))

        # [2026-09 버그수정, 신민용 리포트: "3시즌 돌리면 GK가 아예 없는 팀이
        # 13개 생긴다"] 계측(tools/gk_zero_qa.py)으로 이 함수가 발생 지점 중
        # 하나로 확정됐다(3시즌 GK0 순증 +12). 원인: 아래 루프의 방출
        # (release) 분기는 "그냥 삭제"라 대체자가 안 생기는데, 방출 대상은
        # 스쿼드 최저 OVR 순이고 포지션은 전혀 안 본다 — 팀의 유일한 GK가
        # 백업이라 OVR이 낮으면 그대로 방출돼 GK 0명이 된다.
        # (교체 분기는 pos를 그대로 물려주므로 원래부터 문제가 없다.)
        # 이적시장(_do_one_transfer_cached)·강제 조기은퇴(_rebalance_squad_
        # sizes)가 이미 지키는 "마지막 GK/마지막 CB" 불변식을 이 경로에도
        # 똑같이 넣는다. 다만 여기선 '후보에서 제외'가 아니라 '방출 대신
        # 같은 포지션 신인으로 교체'로 처리한다 — 물갈이 인원수(n_turn)는
        # 그대로 유지하면서 포지션 구성만 보존되므로, 승강 직후 스쿼드
        # 개편이라는 이 함수의 목적을 전혀 훼손하지 않는다.
        _grp_ct: dict = {}
        _pos_ct: dict = {}
        for _p in squad:
            _g = _POS_GROUP.get(_p["position"], "FW")
            _grp_ct[_g] = _grp_ct.get(_g, 0) + 1
            _pos_ct[_p["position"]] = _pos_ct.get(_p["position"], 0) + 1

        for i, pl in enumerate(squad[:n_turn]):
            del_ids.append(pl["id"])
            pos = pl["position"]
            _g0 = _POS_GROUP.get(pos, "FW")
            # 이 선수를 (대체자 없이) 방출하면 그룹이나 구체 포지션이
            # 0명이 되는가 — 되면 방출 대신 교체로 돌린다.
            _last_one = (_grp_ct.get(_g0, 0) <= 1 or _pos_ct.get(pos, 0) <= 1)
            if i < n_release and not _last_one:
                _grp_ct[_g0] = _grp_ct.get(_g0, 0) - 1
                _pos_ct[pos] = _pos_ct.get(pos, 0) - 1
                released += 1
                continue
            target = random.randint(lo, max(lo, (lo + hi) // 2))
            age = random.randint(*_AI_NEWBIE_AGE)
            _scaled4 = _youth_target_scale(target, age)
            stats = _gen_stats(pos, _scaled4)
            ovr = calc_ovr(pos, stats)
            # [2026-09 신설] 위 topup과 동일 — 5대리그 1부 절대 최저선.
            _lhm4, _phm4 = _squad_ovr_hard_min(cname, tier, info["tname"])
            if _lhm4 is not None:
                _af4 = (_scaled4 / target) if target else 1.0
                stats, ovr = _lift_stats_to_ovr(
                    pos, stats, ovr, max(_lhm4, (_phm4 or 0) * _af4))
            sub_role = random.choice(SUB_ROLES.get(pos, ["기본"]))
            # [2026-09] database._nat_ceiling_penalty 정의부 주석 참고.
            nat, foreign_ct = _pick_nationality(cname, continent, grade, pos,
                                                False, foreign_ct, quota, slot_ovr=target, youth=(tier or 1) <= YOUTH_PATH_MAX_TIER)
            name = ""      # [2026-09] AI 실명 폐지
            new_rows.append((team_id, name, pos, *[stats[s] for s in ALL_STATS], ovr, age,
                              sub_role, nat, nat, year + random.randint(2, 4), 0, year,
                              # [2026-09] 위와 동일 — 자기 목표 바닥.
                              max(ovr, int(round(target)),
                                  roll_potential_ovr(_turnover_growth_cap)),
                              # [2026-09 신설] 위 _gen_topup_rows와 같은 이유.
                              _calc_ai_salary(grade, tier, ovr, cname,
                                              info["tname"], team_id, year),
                              # [2026-09 신설] database.CREATION_SOURCES 참고.
                              "promo_rebuild"))
            replaced += 1

    if del_ids:
        _archive_forced_out_players(c, del_ids, year)
        c.executemany("DELETE FROM ai_players WHERE id=?", [(i,) for i in del_ids])
    if new_rows:
        # [2026-09 신설, database.true_nationality 컬럼 주석 참고] 승강
        # 직후 스쿼드 교체로 새로 태어나는 선수도 동일하게 채운다.
        c.executemany(
            f"""INSERT INTO ai_players
                (team_id,name,position,{_STAT_COLS},ovr,age,sub_role,nationality,
                 true_nationality,contract_end_year,last_transfer_year,created_year,potential_ovr,
                 salary,creation_source)
                VALUES(?,?,?,{','.join('?' for _ in ALL_STATS)},?,?,?,?,?,?,?,?,?,?,?)""",
            new_rows)
    conn.commit()
    return replaced, released