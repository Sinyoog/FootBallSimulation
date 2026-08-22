# -*- coding: utf-8 -*-
"""골 시상 시스템 v4 — 골 생성기 단독 검증용 (DB/게임엔진 의존 없음)"""
import random

SHOT_BASE = {
    "NORMAL": 10, "HEADER": 18, "TOE_POKE": 20, "CHIP": 25,
    "DIRECT_FREEKICK": 28, "CURLED": 32, "DIRECT_CORNER": 33,
    "BACKHEEL": 38, "DIVING_HEADER": 42, "HALF_VOLLEY": 42,
    "VOLLEY": 45, "PANENKA": 50, "RABONA": 58, "OVERHEAD": 65,
    "SCORPION": 70,
}

COMMON_W = {
    "NORMAL": 30, "HEADER": 15, "TOE_POKE": 8, "CHIP": 10,
    "DIRECT_FREEKICK": 8, "CURLED": 10, "DIRECT_CORNER": 3,
    "BACKHEEL": 3, "DIVING_HEADER": 4, "HALF_VOLLEY": 3,
    "VOLLEY": 3, "PANENKA": 1, "RABONA": 0.7, "OVERHEAD": 1, "SCORPION": 0.3,
}
RARE_W = {
    "NORMAL": 5, "HEADER": 6, "TOE_POKE": 4, "CHIP": 8,
    "DIRECT_FREEKICK": 6, "CURLED": 15, "DIRECT_CORNER": 4,
    "BACKHEEL": 5, "DIVING_HEADER": 7, "HALF_VOLLEY": 6,
    "VOLLEY": 10, "PANENKA": 5, "RABONA": 6, "OVERHEAD": 10, "SCORPION": 3,
}

# feature: (가산점, p_common, p_rare)
FEATURES = {
    "LONG_RANGE":          (7,  0.15,  0.22),
    "EXTREME_LONG_RANGE":  (14, 0.04,  0.08),
    "HALF_LINE":           (18, 0.003, 0.010),
    "EXTREME_ANGLE":       (9,  0.05,  0.10),
    "SOLO_RUN":            (11, 0.08,  0.15),
    "MULTIPLE_DEFENDERS":  (14, 0.03,  0.07),
    "GK_GOAL":             (22, 0.005, 0.010),
    "LAST_MINUTE":         (6,  0.08,  0.08),
    "WINNING_GOAL":        (4,  0.15,  0.15),
    "COMEBACK_GOAL":       (6,  0.08,  0.10),
}
DISTANCE_TIER = ["LONG_RANGE", "EXTREME_LONG_RANGE", "HALF_LINE"]  # 상호배타, 뒤가 우선


# [2026-08 신설, 골 시상 시스템 v4] 리그 등급별 가중치 - opportunity_for_grade의
# grade_weight 인자로 쓴다(설계문서 5절). 리그 등급은 후보 자격을 제한하지
# 않고 "좋은 골이 나올 확률"에만 영향을 준다.
LEAGUE_GRADE_WEIGHT = {
    "SS": 1.00, "S": 0.85, "A": 0.70, "B": 0.50,
    "C": 0.30, "D": 0.18, "E": 0.10, "F": 0.05,
}

_VALID_SHOT_TYPES = frozenset(SHOT_BASE.keys())
_VALID_FEATURES = frozenset(FEATURES.keys())


def _mix(opportunity: float) -> float:
    return max(0.0, min(1.0, (opportunity - 0.05) / (1.50 - 0.05)))


# [2026-08 신설, 15번 이후 신규 리포트: "수비수가 다수 수비수 제침+발리+
# 극장골로 올해의 골을 받았다 — 포지션 구분 없이 확률이 너무 높다"]
# 지금까지 gen_goal()은 포지션과 완전히 무관하게 opportunity/context만으로
# feature 확률을 뽑았다 — 수비수든 스트라이커든 "다수 수비수 제침"
# (드리블로 여러 명을 제치는 행위)이 나올 확률이 완전히 같았다. 현실에선
# 이런 "볼 운반형" 기술은 공격진/윙어일수록 훨씬 흔하고, 중앙 수비수·
# GK일수록 극히 드물다(수비수의 화려한 골 자체는 충분히 있을 수 있지만
# 보통 헤더/세트피스 발리처럼 "제친 뒤 마무리"가 아니라 "받아서 바로
# 마무리"하는 유형).
#
# 그래서 "드리블/볼 운반" 성격의 feature(SOLO_RUN, MULTIPLE_DEFENDERS)에만
# 포지션별 배수를 곱한다 — VOLLEY 같은 슛 종류나 LAST_MINUTE/WINNING_GOAL
# 같은 상황성 feature는 포지션과 무관하게 그대로 둔다(수비수가 세트피스
# 상황에서 발리로 극장골을 넣는 건 실제로도 흔한 장면이라 건드릴 이유가
# 없음 — 신민용 확인: "수비수 발리 자체는 매우 가능").
DRIBBLE_FEATURES = frozenset({"SOLO_RUN", "MULTIPLE_DEFENDERS"})
POSITION_DRIBBLE_MULT = {
    "GK":  0.05,
    "CB":  0.12,
    "LB":  0.35, "RB": 0.35,
    "CDM": 0.30,
    "CM":  0.55,
    "CAM": 1.10,
    "LW":  1.35, "RW": 1.35,
    "CF":  0.90,
    "ST":  0.75,
}
_DEFAULT_DRIBBLE_MULT = 0.55


def _dribble_mult(position) -> float:
    return POSITION_DRIBBLE_MULT.get(position, _DEFAULT_DRIBBLE_MULT)


# [2026-08 재조정, 신민용+GPT 검토: "OVR 차이를 선형으로 보는 건 이
# 게임의 OVR 체계와 안 맞는다 — 70vs80(K리그 에이스 vs 벤치)과
# 80vs90(K리그 에이스 vs 유럽5대리그 말단)은 둘 다 '10 차이'지만 축구적
# 의미가 다르다"] 기존엔 diff*0.035처럼 OVR 1점 차이를 항상 동일한
# 효과로 취급했다 — 구간이 올라갈수록 선수 수준 차이가 훨씬 커지는 이
# 게임의 OVR 체계와 맞지 않는다는 지적. OVR을 앵커 지점 기반 "레벨"로
# 먼저 변환하고(구간이 올라갈수록 같은 10점이라도 레벨 격차가 커짐),
# 그 레벨 차이로 매치업 배수를 계산한다.
_OVR_LEVEL_ANCHORS = [
    (40, 1.0), (50, 1.5), (60, 2.0), (70, 3.0), (80, 4.5),
    (90, 7.0), (100, 11.0), (110, 16.0),
]


def _ovr_level(ovr: float) -> float:
    """OVR → 상대적 '수준 레벨'. 구간별 선형보간(앵커 간격이 위로
    갈수록 벌어지므로 결과적으로 비선형 — 70→80과 80→90이 다른 폭)."""
    pts = _OVR_LEVEL_ANCHORS
    if ovr <= pts[0][0]:
        return pts[0][1]
    if ovr >= pts[-1][0]:
        return pts[-1][1]
    for (o1, v1), (o2, v2) in zip(pts, pts[1:]):
        if o1 <= ovr <= o2:
            t = (ovr - o1) / (o2 - o1)
            return v1 + (v2 - v1) * t
    return pts[-1][1]


def _dribble_mult(position) -> float:
    return POSITION_DRIBBLE_MULT.get(position, _DEFAULT_DRIBBLE_MULT)


def _dribble_ovr_mult(my_ovr=None, opp_ovr=None, team_ovr=None) -> float:
    """[2026-08 재설계] OVR을 그대로 빼는 대신 _ovr_level()로 변환한
    레벨 격차를 쓴다. team_ovr(내 소속팀 평균 OVR)은 "팀 전체 공격
    전개력이 좋을수록 유리한 찬스가 더 자주 만들어진다"는 근사로 소폭만
    반영(개인 스킬 격차보다 영향력을 작게 둠)."""
    if my_ovr is None or opp_ovr is None:
        return 1.0
    level_diff = _ovr_level(my_ovr) - _ovr_level(opp_ovr)
    mult = 1.0 + level_diff * 0.15
    if team_ovr is not None:
        team_level_diff = _ovr_level(team_ovr) - _ovr_level(opp_ovr)
        mult *= 1.0 + team_level_diff * 0.05
    return max(0.25, min(3.0, mult))


# [2026-08 신설, 신민용+GPT 검토: "포지션이랑 내 스탯에 따라 조금씩
# 차이도 나야 한다 — 같은 OVR 80이어도 CB인데 드리블 55인 선수와
# 드리블 72인 선수는 다르게 취급돼야"] 포지션은 "그 장면의 기본 발생
# 빈도", OVR 매치업은 "상대와의 전반적 수준 차이"이고, 여기에 실제
# 드리블/스피드 세부 스탯으로 "이 선수가 그 장면에 얼마나 적합한
# 선수인가"를 추가로 반영한다. dribbling/speed 둘 다 없으면(하위호환,
# AI 대표골 등 세부 스탯 미제공 경로) 중립 1.0.
# 기준점 60(게임 내 평균적인 스탯)을 중심으로 ±2%/점, 하한/상한을 둬서
# 아무리 특출난 선수라도 배수가 무한정 커지지 않게 한다.
def _dribble_attribute_mult(dribbling=None, speed=None) -> float:
    vals = [v for v in (dribbling, speed) if v is not None]
    if not vals:
        return 1.0
    avg = sum(vals) / len(vals)
    mult = 1.0 + (avg - 60) * 0.02
    return max(0.4, min(2.2, mult))


def gen_goal(opportunity: float, context_score: float = 1.0, rng=None, position=None,
             my_ovr=None, opp_ovr=None, team_ovr=None, dribbling=None, speed=None):
    """opportunity: 0.05~1.50 (GOAL_OPPORTUNITY). context_score: 0.80~1.10.

    rng: None이면 전역 random 모듈(실제 경기 즉시 생성용), AI 대표골처럼
    재계산해도 같은 결과가 나와야 하면 seed 고정된 random.Random을 넘긴다
    (_make_goal_seed 참고).
    position: [2026-08 신설] 득점자 포지션(GK/CB/.../ST). None이면(하위
    호환) 배수 1.0(중앙 미드필더 근처)으로 취급한다. SOLO_RUN/
    MULTIPLE_DEFENDERS 확률에만 영향을 준다 — POSITION_DRIBBLE_MULT 표
    참고.
    my_ovr, opp_ovr, team_ovr: [2026-08 신설, 재조정] 드리블 계열
    feature에 OVR 매치업(선형이 아니라 _ovr_level 기반 구간별 격차)을
    추가로 반영. my_ovr/opp_ovr 둘 다 있어야 작동. team_ovr은 선택적
    보조 반영.
    dribbling, speed: [2026-08 신설] 득점자의 실제 드리블/스피드 스탯 —
    같은 포지션·같은 OVR이어도 "이 장면에 얼마나 특화된 선수인가"를
    반영한다. 자세한 설계 의도는 _dribble_attribute_mult() 참고.
    """
    _rng = rng if rng is not None else random
    context_score = max(0.80, min(1.10, context_score))
    mix = _mix(opportunity)
    _dmult = (_dribble_mult(position)
              * _dribble_ovr_mult(my_ovr, opp_ovr, team_ovr)
              * _dribble_attribute_mult(dribbling, speed))

    # 1) shot_type 선택 (COMMON_W ↔ RARE_W 보간)
    types = list(SHOT_BASE.keys())
    weights = [(1 - mix) * COMMON_W[t] + mix * RARE_W[t] for t in types]
    shot_type = _rng.choices(types, weights=weights, k=1)[0]

    # 2) features 선택 (거리계열은 상호배타, 나머지는 독립 베르누이)
    chosen = []
    # 거리계열: 순서대로 확률 체크, 마지막에 성공한(가장 강한) 것만 채택
    dist_pick = None
    for feat in DISTANCE_TIER:
        _, pc, pr = FEATURES[feat]
        p = (1 - mix) * pc + mix * pr
        if _rng.random() < p:
            dist_pick = feat  # 더 강한 등급이 나오면 계속 덮어씀
    if dist_pick:
        chosen.append(dist_pick)
    # 나머지 독립 feature
    for feat, (pts, pc, pr) in FEATURES.items():
        if feat in DISTANCE_TIER:
            continue
        p = (1 - mix) * pc + mix * pr
        if feat in DRIBBLE_FEATURES:
            p *= _dmult
        if _rng.random() < p:
            chosen.append(feat)

    # 3) SHOT_SCORE
    feat_sum = sum(FEATURES[f][0] for f in chosen)
    feat_sum = min(30, feat_sum)
    shot_score = min(100, SHOT_BASE[shot_type] + feat_sum)

    # 4) FINAL_GOAL_SCORE
    final_score = min(100, shot_score * context_score)

    result = {
        "shot_type": shot_type, "features": chosen,
        "shot_score": shot_score, "context_score": context_score,
        "final_score": final_score,
    }
    # [3번 보강: 방어 로직] 이론상 나올 수 없는 값이 생기면 저장 전에 즉시 예외.
    assert result["shot_type"] in _VALID_SHOT_TYPES
    assert all(f in _VALID_FEATURES for f in result["features"])
    assert 0 <= result["shot_score"] <= 100
    assert 0 <= result["final_score"] <= 100
    return result


def opportunity_for_grade(grade_weight: float, goal_ratio: float) -> float:
    import math
    return max(0.05, min(1.50, grade_weight * math.sqrt(goal_ratio)))