# -*- coding: utf-8 -*-
"""match_sim/roles.py — 역할 = 평가함수 가중치 세트.

## 이게 무엇을 대체하는가

기존 `sim_engine`에는 두 종류의 하드코딩이 있었다.

**(1) 포메이션 × 포지션 전진폭 테이블 (`_TACTICAL_DX`)**

    _TACTICAL_DX = {
        (None,    "ST"): (0.20, 0.06),
        ("4-3-3", "CB"): (0.14, 0.10),
        ("3-4-3", "CB"): (0.24, 0.04),
        ...
    }

포메이션과 포지션의 **조합마다** 항목이 하나씩 필요하다. 포메이션을 하나
추가하면 11개 항목을 손으로 채워야 하고, 국면(빌드업/전환/수비)까지
곱하면 조합이 폭발한다. 실제로 이 표는 일부 조합만 채워져 있고 나머지는
공통 기본값으로 떨어진다 — 그래서 "포메이션에 따른 움직임"이 몇몇
포메이션에서만 존재했다.

**(2) 특정 상황 전용 `elif` 분기**

    elif is_holder_side and i == self._advancing_mid_idx:
        if formation == "4-3-3":
            _half_space_y = 0.28 if pl["hy"] < 0.5 else 0.72   # 메찰라
    elif is_holder_side and i == self._volpiana_idx and push < 0.35:
        ...                                                     # 라볼피아나

각 분기는 **미리 지정된 인덱스 한 명**에게만 적용된다. 그래서 22명 중
분기에 걸린 소수만 의미 있게 움직였다(계측: 한 프레임에 1.4명).

## 대신 무엇을 하는가

FM이 쓰는 것과 같은 분해다:

    포메이션  = 11개 기준 존(zone)의 배치. 그게 전부.
    역할      = 공간 평가함수의 가중치 세트.
    전술 지시 = 팀 전체 가중치에 곱하는 보정.

메찰라는 "4-3-3일 때 y를 0.28로 보내라"가 아니라 **`w_halfspace`가 높고
`w_progression`이 높으며 존이 넓은 가중치 세트**다. 라볼피아나는 특수
분기가 아니라 **빌드업 국면(볼이 자기 진영)에서 `w_safe_outlet`이 높은
CDM이 자연스럽게 센터백 사이 칸을 argmax로 고르는 결과**다.

그래서 "3-4-3의 CB는 더 올라간다"도 표 항목이 아니라 창발한다 — 스리백은
뒤에 사람이 적어 커버해야 할 칸이 넓고, 그 상황에서 평가함수가 다른 답을
낸다.

## 가중치의 의미

각 역할은 두 국면에 대해 서로 다른 가중치를 갖는다.

  **ATTACK (우리 팀이 볼 소유)**
    control   우리가 그 칸을 점유할 수 있는 정도를 얼마나 중시하는가
    prog      상대 골문 쪽 전진을 얼마나 중시하는가
    threat    슛 위협(거리+각도)을 얼마나 중시하는가
    pass      지금 볼에서 그 칸으로 패스가 갈 수 있는지를 얼마나 중시하는가
    halfspace 하프스페이스(y≈0.28/0.72) 선호도
    width     터치라인 쪽 폭 유지 선호도

  **DEFEND (상대가 볼 소유)**
    deny      상대에게 위험한 칸을 미리 점유하려는 정도
    press     볼 자체로 다가가려는 정도
    goalside  자기 골문과 볼 사이에 서려는 정도

  **공통**
    zone      존(포메이션 기준점) 이탈을 얼마나 꺼리는가
    sigma_x/y 존의 크기(미터). 클수록 자유롭게 돌아다닌다.
    cost      이동 비용에 대한 민감도(높으면 게을러진다)
    vmax      최고속도(m/s)

숫자는 실축 계측 지표(motion_metrics.py)가 참조 밴드에 들어오도록 맞춘
것이지, 이론적으로 유도한 값이 아니다. 튜닝의 근거는 언제나 계측이다.
"""

# ── 포지션 분류 ──
GK = "GK"
BACKLINE = {"CB", "LB", "RB", "LWB", "RWB"}
WIDE = {"LW", "RW", "LB", "RB", "LWB", "RWB", "LM", "RM"}
FORWARD = {"ST", "CF", "LW", "RW"}
CENTRAL_MID = {"CDM", "CM", "CAM"}


def _r(**kw):
    """기본값 위에 덮어쓰는 역할 정의 헬퍼."""
    base = dict(
        # ATTACK
        control=0.30, prog=0.50, threat=0.30, pass_=0.55,
        halfspace=0.0, width=0.0,
        # DEFEND
        deny=0.60, press=0.25, goalside=0.45,
        # 공통
        zone=1.00, sigma_x=16.0, sigma_y=14.0, cost=0.32, vmax=7.4,
    )
    base.update(kw)
    return base


# ══════════════════════════════════════════════════════════════════
#  역할 정의
#
#  sigma_x/sigma_y가 이 파일에서 가장 중요한 숫자다. 예전 스프링 모델의
#  실효 sigma는 사실상 0에 가까웠고(목표가 hx에 고정), 그래서 선수가
#  포메이션 기준점에서 못 벗어났다. 실축에서 선수는 자기 "포지션"에서
#  20~30m씩 예사로 벗어난다 — 그 스케일을 여기서 준다.
# ══════════════════════════════════════════════════════════════════
ROLES = {
    # ── 골키퍼 ──
    # 별도 스위퍼 로직이 엔진에 있으므로 존을 아주 좁게 잡는다.
    "GK": _r(control=0.10, prog=0.05, threat=0.0, pass_=0.20,
             deny=0.20, press=0.0, goalside=1.20,
             zone=3.00, sigma_x=6.0, sigma_y=5.0, cost=0.60, vmax=5.2),

    # ── 센터백 ──
    # 전진 가치를 거의 안 보고, 위험한 칸을 막는 것과 골사이드를 중시.
    # 존이 좁아서 라인을 유지하지만 볼이 오면 나갈 수 있을 만큼은 넓다.
    # [튜닝 근거] zone 1.55 / sigma 13m로 시작했더니 수비진 이동거리가
    # 6.0km(실축 8~9.5km), 정지 시간 42%로 나왔다 — 백라인만 예전 스프링
    # 모델에 가깝게 묶여 있었던 것. 존을 풀어서 볼을 따라 라인을 올리고
    # 내리게 한다. goalside/deny가 충분히 높아 뒷공간은 여전히 지킨다.
    "CB": _r(control=0.45, prog=0.22, threat=0.02, pass_=0.46,
             deny=0.95, press=0.24, goalside=0.85,
             zone=1.10, sigma_x=18.0, sigma_y=15.0, cost=0.26, vmax=7.5),

    # ── 풀백 ──
    # 폭을 담당하고 전진 가담도 한다. 존이 세로로 길다(오버래핑).
    "LB": _r(control=0.38, prog=0.58, threat=0.10, pass_=0.58,
             width=0.45, deny=0.72, press=0.34, goalside=0.58,
             zone=0.95, sigma_x=25.0, sigma_y=15.0, cost=0.22, vmax=7.9),
    "RB": _r(control=0.38, prog=0.58, threat=0.10, pass_=0.58,
             width=0.45, deny=0.72, press=0.34, goalside=0.58,
             zone=0.95, sigma_x=25.0, sigma_y=15.0, cost=0.22, vmax=7.9),

    # ── 윙백 — 풀백보다 훨씬 공격적. 스리백 포메이션의 폭 담당. ──
    "LWB": _r(control=0.35, prog=0.70, threat=0.16, pass_=0.55,
              width=0.60, deny=0.60, press=0.35, goalside=0.48,
              zone=0.95, sigma_x=26.0, sigma_y=14.0, cost=0.26, vmax=8.1),
    "RWB": _r(control=0.35, prog=0.70, threat=0.16, pass_=0.55,
              width=0.60, deny=0.60, press=0.35, goalside=0.48,
              zone=0.95, sigma_x=26.0, sigma_y=14.0, cost=0.26, vmax=8.1),

    # ── 수비형 미드필더 ──
    # 전진을 억제하고 "안전한 출구"가 되는 게 일. deny가 높아서 백라인
    # 앞 공간을 스크린한다. 빌드업 때 센터백 사이로 내려가는 이른바
    # 라볼피아나는 이 가중치 조합의 자연스러운 결과다 — 볼이 자기 진영
    # 깊은 곳에 있으면 pass_ 가 높은 칸이 그쪽에 생기기 때문.
    "CDM": _r(control=0.60, prog=0.28, threat=0.05, pass_=0.72,
              deny=0.90, press=0.40, goalside=0.68,
              zone=1.05, sigma_x=21.0, sigma_y=17.0, cost=0.24, vmax=7.5),

    # ── 중앙 미드필더 ──
    # 가장 균형잡힌 역할. 존이 넓어서 박스투박스로 오르내린다.
    # CM이 둘일 때 한 명은 전진/한 명은 잔류가 되는 것은 space_model의
    # 탐욕적 선점(apply_claim)이 자동으로 만들어낸다 — 예전처럼
    # _advancing_mid_idx / _holding_mid_idx 를 손으로 지정하지 않는다.
    "CM": _r(control=0.45, prog=0.50, threat=0.20, pass_=0.75,
             halfspace=0.30, deny=0.75, press=0.42, goalside=0.55,
             zone=0.90, sigma_x=24.0, sigma_y=18.0, cost=0.28, vmax=7.6),

    # ── 공격형 미드필더 ──
    "CAM": _r(control=0.35, prog=0.62, threat=0.55, pass_=0.85,
              halfspace=0.45, deny=0.45, press=0.40, goalside=0.30,
              zone=0.75, sigma_x=25.0, sigma_y=20.0, cost=0.24, vmax=7.7),

    # ── 측면 미드필더 ──
    "LM": _r(control=0.35, prog=0.58, threat=0.28, pass_=0.65,
             width=0.50, deny=0.65, press=0.38, goalside=0.45,
             zone=0.95, sigma_x=23.0, sigma_y=15.0, cost=0.27, vmax=7.9),
    "RM": _r(control=0.35, prog=0.58, threat=0.28, pass_=0.65,
             width=0.50, deny=0.65, press=0.38, goalside=0.45,
             zone=0.95, sigma_x=23.0, sigma_y=15.0, cost=0.27, vmax=7.9),

    # ── 윙어 ──
    # width가 높아 볼이 자기 쪽에 있으면 터치라인까지 벌리고, 반대쪽에
    # 있으면 pass_ 가 낮아져 자연스럽게 안으로 좁혀 들어온다(인버팅).
    # 예전엔 이 두 동작이 각각 별도의 if/else 분기였다.
    "LW": _r(control=0.30, prog=0.70, threat=0.55, pass_=0.70,
             width=0.65, halfspace=0.25, deny=0.40, press=0.36, goalside=0.22,
             zone=0.80, sigma_x=24.0, sigma_y=18.0, cost=0.27, vmax=8.5),
    "RW": _r(control=0.30, prog=0.70, threat=0.55, pass_=0.70,
             width=0.65, halfspace=0.25, deny=0.40, press=0.36, goalside=0.22,
             zone=0.80, sigma_x=24.0, sigma_y=18.0, cost=0.27, vmax=8.5),

    # ── 스트라이커 / 센터포워드 ──
    # threat 최대. 존이 가장 넓어서 라인 사이/뒷공간을 자유롭게 찾는다.
    "ST": _r(control=0.28, prog=0.72, threat=0.95, pass_=0.72,
             deny=0.28, press=0.40, goalside=0.12,
             zone=0.72, sigma_x=23.0, sigma_y=20.0, cost=0.26, vmax=8.1),
    "CF": _r(control=0.34, prog=0.62, threat=0.85, pass_=0.85,
             halfspace=0.30, deny=0.34, press=0.40, goalside=0.18,
             zone=0.66, sigma_x=25.0, sigma_y=21.0, cost=0.20, vmax=8.0),
}

_FALLBACK = _r()


def role_for(pos):
    return ROLES.get(pos, _FALLBACK)


# ══════════════════════════════════════════════════════════════════
#  듀티(duty) — 같은 포지션 두 명을 서로 다르게 만든다
# ══════════════════════════════════════════════════════════════════
#
# 탐욕적 선점(space_model.apply_claim)만으로도 CM 둘이 다른 칸을 고르긴
# 하지만, 매 틱 누가 먼저 고르냐에 따라 역할이 뒤바뀔 수 있다. 실제
# 축구에서 "누가 전진형이고 누가 홀딩인지"는 경기 내내 대체로 고정이다
# (예전 코드도 `_pick_fixed_striker_roles`로 이 점을 인정하고 있었다).
#
# 그래서 경기 시작 시 한 번, 같은 라벨을 공유하는 선수들 사이에 듀티를
# 배분한다. 배분 기준은 스탯이다 — 손으로 지정하지 않는다.

DUTY_ATTACK = "attack"
DUTY_SUPPORT = "support"
DUTY_DEFEND = "defend"

_DUTY_MULT = {
    DUTY_ATTACK:  {"prog": 1.35, "threat": 1.30, "deny": 0.75,
                   "goalside": 0.70, "sigma_x": 1.15, "zone": 0.85},
    DUTY_SUPPORT: {},
    DUTY_DEFEND:  {"prog": 0.60, "threat": 0.55, "deny": 1.30,
                   "goalside": 1.25, "sigma_x": 0.85, "zone": 1.20},
}


def apply_duty(role, duty):
    """역할 가중치에 듀티 배수를 적용한 새 dict를 돌려준다."""
    mult = _DUTY_MULT.get(duty)
    if not mult:
        return role
    out = dict(role)
    for k, m in mult.items():
        out[k] = out[k] * m
    return out


def _attacking_score(stats):
    """스탯에서 '전진 성향' 점수를 뽑는다. 드리블/스피드가 높고
    태클/포지셔닝이 낮을수록 전진형."""
    g = stats.get
    return (g("dribbling", 50) + g("speed", 50)) - (g("tackling", 50) + g("positioning", 50))


def assign_duties(team):
    """같은 포지션 라벨을 공유하는 선수들에게 듀티를 배분한다.

    team: sim_engine의 선수 dict 리스트 (pos / stats 필요)
    반환: 인덱스별 duty 문자열 리스트

    한 명뿐인 라벨은 support(중립). 두 명이면 전진성향이 높은 쪽이 attack,
    낮은 쪽이 defend. 셋 이상이면 상위 1/3 attack, 하위 1/3 defend.
    """
    duties = [DUTY_SUPPORT] * len(team)
    groups = {}
    for i, p in enumerate(team):
        if p["pos"] == GK:
            continue
        groups.setdefault(p["pos"], []).append(i)

    for pos, idxs in groups.items():
        if len(idxs) < 2:
            continue
        ranked = sorted(idxs, key=lambda i: _attacking_score(team[i].get("stats") or {}),
                        reverse=True)
        if len(ranked) == 2:
            duties[ranked[0]] = DUTY_ATTACK
            duties[ranked[1]] = DUTY_DEFEND
        else:
            k = max(1, len(ranked) // 3)
            for i in ranked[:k]:
                duties[i] = DUTY_ATTACK
            for i in ranked[-k:]:
                duties[i] = DUTY_DEFEND
    return duties


def build_role_table(team):
    """팀 전체의 (역할 가중치, 듀티) 를 경기 시작 시 한 번 계산해 둔다.

    [주의] 매 틱 호출하면 안 된다 — dict 복사가 22×1600회 발생한다.
    """
    duties = assign_duties(team)
    return [apply_duty(role_for(p["pos"]), d) for p, d in zip(team, duties)], duties