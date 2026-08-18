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


def gen_goal(opportunity: float, context_score: float = 1.0, rng=None):
    """opportunity: 0.05~1.50 (GOAL_OPPORTUNITY). context_score: 0.80~1.10.

    rng: None이면 전역 random 모듈(실제 경기 즉시 생성용), AI 대표골처럼
    재계산해도 같은 결과가 나와야 하면 seed 고정된 random.Random을 넘긴다
    (_make_goal_seed 참고).
    """
    _rng = rng if rng is not None else random
    context_score = max(0.80, min(1.10, context_score))
    mix = _mix(opportunity)

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