"""
ui/match_sim_viewer.py — 경기 상세의 "▶ 시뮬 보기" 버튼으로 여는 2D 시뮬 뷰어.

[중요 - 데이터의 한계]
  이 게임의 매치 엔진은 확률 기반 통계 시뮬레이션이라, 22명 선수의 실제 좌표나
  패스 궤적 같은 데이터는 애초에 존재하지 않는다. 있는 건:
    - 내 개인 이벤트 타임라인 (몇 분에 내가 골/도움/선방/실점했는지, 텍스트+분)
    - 양팀 포메이션(포지션 배치)
    - 최종 스코어
  그래서 이 뷰어는 "실제 시뮬을 재생"하는 게 아니라, 이 진짜 데이터(스코어가
  언제 나왔는지)에 맞춰 포메이션 기준으로 그럴듯한 움직임을 절차적으로
  연출하는 것이다. 평상시엔 대략적인 점유율 흐름(공이 이리저리 움직이고
  선수들이 포메이션 주변에서 반응)을 보여주다가, 실제로 골/선방 이벤트가
  있었던 그 분(分)이 되면 그 결과에 맞는 짧은 장면(공격 전개→골 또는 막힘)을
  연출한다.

[성능] 점 23개(양팀 22 + 공) 정도를 60~200ms 간격 QTimer로 갱신하는 수준이라
  실측해도 CPU 부담이 거의 없다. 창을 닫으면(closeEvent) 타이머를 확실히
  멈춰서 백그라운드에 남지 않게 처리했다.
"""
import random
import math
import hashlib
import time
import json
import os
from datetime import datetime
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton, QComboBox,
    QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont

from constants import FORMATION_SLOTS, POSITION_COMPAT


# ─────────────────────────────────────────
# 포지션 라벨 → 피치 위 기준 좌표 (0~1, 홈팀 기준: x=0 자기 골, x=1 상대 골)
# ─────────────────────────────────────────
_POS_XY = {
    "GK":  (0.05, 0.50),
    "CB":  (0.16, 0.50), "LB": (0.18, 0.14), "RB": (0.18, 0.86),
    "LWB": (0.28, 0.12), "RWB": (0.28, 0.88),
    "CDM": (0.34, 0.50), "CM": (0.44, 0.50), "CAM": (0.48, 0.50),
    "LM":  (0.44, 0.16), "RM": (0.44, 0.84),
    "LW":  (0.49, 0.14), "RW": (0.49, 0.86),
    "CF":  (0.50, 0.50), "ST": (0.50, 0.50),
}

# 골 장면에서 실제로 전진해야 할 '공격 라인' 포지션(공격수/윙/공미).
# 수비수·수비형 미드필더·골키퍼는 절대 이 목록에 안 들어감 — 골 장면에
# 수비수가 같이 튀어나가는 걸 막는 핵심 로직.
_ATTACK_ROLES = {"ST", "CF", "LW", "RW", "CAM"}
_SUPPORT_ROLES = {"CM", "LM", "RM"}  # 공격수가 부족한 포메이션에서 보충용
_DEFENSE_ROLES = {"CB", "LB", "RB", "LWB", "RWB", "CDM"}
# [신규] 오프사이드 트랩 라인 동조 대상 — CDM은 포함하지 않는다(CDM은
# 최종 수비 라인이 아니라 그 앞에서 스크린 역할을 하는 포지션이라, 백4와
# 같은 줄로 묶으면 라인이 한 덩어리로 뭉개져 보인다).
_BACKLINE_ROLES = {"CB", "LB", "RB", "LWB", "RWB"}
# [신규] 수비수가 압박이 길어져도 자기 골라인/골키퍼 옆까지 완전히
# 밀려나지 않도록 하는 최소 깊이(골라인 기준). 실제 축구에서 아무리
# 로우블록이어도 페널티박스 언저리는 유지한다.
_MIN_DEFENSIVE_DEPTH = 0.12

# [현실성 보정] 실제 골대 폭은 7.32m로, 68m 폭 피치 기준 약 0.108(피치 폭의
# 10.8%)에 불과하다. 페널티박스(폭 40.32m, 약 0.59)보다 훨씬 좁다 — 박스
# 전체를 "골대"로 착각하기 쉬워서 시각적으로도 별도 표시가 필요하다.
_GOAL_HALF_HEIGHT = 0.054

# [현실성 보정] 오픈플레이 마무리 슛은 "빌드업이 끝난 그 자리"에서 그대로
# 쏘게 했더니, 빌드업이 우연히 자기 진영 근처에서 끝나면 골대까지 피치를
# 거의 다 가로지르는 말도 안 되는 궤적(중거리도 아니고 하프라인급 슈팅)이
# 나왔다. 실제로 마무리 슛은 항상 상대 골대에서 이 거리 이내(대략
# 파이널서드)에서 나오므로, 씬이 시작되는 시점의 공 위치를 이 범위 안으로
# 당겨준다. 스루패스(through)는 좀 더 깊은 침투를 표현해야 하니 범위를
# 넉넉하게 둔다.
_SHOT_ZONE_NORMAL = 0.38   # 골대까지 거리(피치 길이 비율) — 일반 마무리
_SHOT_ZONE_THROUGH = 0.55  # 스루패스 마무리 — 좀 더 깊은 곳에서 출발 허용


def _clamp_shot_start_x(x, atk_goal_x, max_dist):
    """공격 방향 골대(atk_goal_x)까지의 거리가 max_dist를 넘지 않도록 x를
    당겨준다. 이미 그 안에 있으면 건드리지 않는다(더 가까이서 시작하는
    건 자연스러우니 그대로 둔다)."""
    if atk_goal_x >= 0.5:
        return max(atk_goal_x - max_dist, min(0.95, x))
    return min(atk_goal_x + max_dist, max(0.05, x))


# 점유 시 각 포지션이 앞으로 밀고 올라가는 정도(피치 길이 기준 0~1 스케일).
# 수비수·미드필더는 많이 전진하고, 최전방은 원래 높은 위치라 조금만 더 간다.
# [신규] 포메이션별 전술 전진/후퇴 폭 ──────────────────────────
# 예전엔 "공격 시 얼마나 전진하는가" 하나의 값만 라벨(포지션)
# 당 있었고, 수비 시(push→0)엔 그냥 항상 중립 위치(hx)에 그대로 머물러서
# "로우블록으로 물러난다" 같은 그림이 아예 없었다. 또 같은 라벨이라도
# 포메이션에 따라 전술적 역할이 완전히 다른데(3-5-2 윙백 vs 4-4-2 풀백)
# 구분이 안 됐다. (전진폭, 후퇴폭) 쌍을 (포메이션, 라벨)별로 따로 둬서
# 둘 다 해결한다 — 포메이션 지정이 없으면(None) 공통 기본값을 쓴다.
_TACTICAL_DX = {
    (None, "GK"):  (0.03, 0.02),
    (None, "CB"):  (0.26, 0.06), (None, "LB"): (0.20, 0.08), (None, "RB"): (0.20, 0.08),
    (None, "LWB"): (0.24, 0.10), (None, "RWB"): (0.24, 0.10),
    # [현실성 보정 — 근본 원인] 예전 후퇴폭(CDM 0.06/CM 0.08/LM,RM 0.08)은
    # 너무 작아서, 수비할 때도 이 포지션들은 거의 자기 기준위치(hx)에
    # 그대로 머물렀다. 문제는 CDM/CM/LM/RM의 기준위치 자체가 이미 피치
    # 중앙~공격 쪽(0.44~0.49대)이라, "거의 안 움직인다"는 게 곧 "수비할
    # 때도 계속 공격 지역 근처에 남아있다"는 뜻이 됐다. 실측 디버그
    # 캡처에서 백라인은 자기 골문 근처까지 물러났는데 중원은 하프라인
    # 코앞에 그대로 남아, 그 사이(약 0.25~0.3 폭)가 완전히 텅 비어버리는
    # 게 그대로 재현됐다("전방은 뚫렸는데 수비 3명만 남았다"는 지적,
    # 그리고 그 텅 빈 구간 때문에 전진 패스 상대가 없어서 "다 뚫린
    # 상황에서 뒤로 패스한다"는 지적 둘 다 이 한 가지 원인에서 갈라져
    # 나온 것). 후퇴폭을 전진폭과 비슷한 수준으로 올려서, 수비할 때
    # 중원도 백라인 바로 앞까지 확실히 내려오게 한다.
    (None, "CDM"): (0.16, 0.18), (None, "CM"): (0.20, 0.18), (None, "CAM"): (0.22, 0.12),
    (None, "LM"):  (0.22, 0.16), (None, "RM"): (0.22, 0.16),
    (None, "LW"):  (0.24, 0.10), (None, "RW"): (0.24, 0.10),
    (None, "CF"):  (0.24, 0.06), (None, "ST"): (0.24, 0.06),

    # [현실성 보정 — 근본 원인] 예전 값(0.16)은 push=1.0(팀 전체가 완전히
    # 밀어붙인 상태)이어도 CB가 기준위치+0.16까지밖에 못 가서, 실측 디버그
    # 캡처에서 ST/CM은 이미 0.6~0.7까지 전진했는데 백3는 0.22 근처에
    # 그대로 남아있는 게 확인됐다("6명이 뒤에서 구경한다"는 지적의 정확한
    # 원인 — 수렴 속도가 아니라 전진폭 "상한" 자체가 낮았던 것). 실제
    # 축구에서 3-5-2/back-3는 팀이 완전히 공격에 쏠리면 CB도 하프라인
    # 근처까지는 따라 올라간다 — 그 정도까지 갈 수 있도록 상향한다.
    ("3-5-2", "LWB"): (0.34, 0.16), ("3-5-2", "RWB"): (0.34, 0.16),
    ("3-5-2", "CB"):  (0.30, 0.05),

    # 5-3-2: 기본은 백5로 확실히 눌러앉고, 공격 전환 때만 윙백이 튀어나감
    # → 후퇴폭을 더 크게(수비 의무가 더 무겁다는 뜻) 잡는다. 다만 백3와
    # 마찬가지로 완전 공격 전개 시엔 어느 정도는 같이 따라 올라가야
    # 한다(3-5-2보다는 보수적으로).
    ("5-3-2", "LWB"): (0.30, 0.20), ("5-3-2", "RWB"): (0.30, 0.20),
    ("5-3-2", "CB"):  (0.22, 0.04),

    # 4-3-3: 윙어는 늘 폭을 넓게 벌리는 게 특징이라 전진폭 자체는 크지
    # 않아도 되고(이미 넓은 자리에서 시작), 대신 풀백이 오버래핑으로
    # 크게 전진한다.
    ("4-3-3", "LB"): (0.28, 0.06), ("4-3-3", "RB"): (0.28, 0.06),
    ("4-3-3", "LW"): (0.18, 0.04), ("4-3-3", "RW"): (0.18, 0.04),

    # 4-2-3-1: 더블 피벗은 공격 시에도 거의 전진하지 않고 항상 후방을
    # 지킨다. 대신 CAM이 훨씬 자유롭게(전/후퇴 폭 모두 크게) 움직인다.
    ("4-2-3-1", "CDM"): (0.08, 0.04),
    ("4-2-3-1", "CAM"): (0.26, 0.12),

    # 4-1-4-1: 단일 앵커는 거의 붙박이, 나머지 넷이 넓게 셔틀런.
    ("4-1-4-1", "CDM"): (0.06, 0.03),
    ("4-1-4-1", "LM"): (0.24, 0.10), ("4-1-4-1", "RM"): (0.24, 0.10),

    # 3-4-3: 스리백 뒤에 공간이 열리는 리스크를 감수하는 대신 전방
    # 압박이 정체성 — 수비 시에도 라인을 크게 안 내린다(후퇴폭 작음).
    # 하이프레스가 정체성인 만큼 공격 전개 시 CB도 다른 포메이션보다
    # 오히려 더 적극적으로 따라 올라가야 자연스럽다.
    ("3-4-3", "CB"): (0.24, 0.04),
    ("3-4-3", "ST"): (0.22, 0.02), ("3-4-3", "LW"): (0.22, 0.02), ("3-4-3", "RW"): (0.22, 0.02),
}


def _tactical_dx(formation, label):
    """(전진폭, 후퇴폭) 조회 — 포메이션 전용값이 없으면 공통 기본값으로."""
    return _TACTICAL_DX.get((formation, label)) or _TACTICAL_DX.get((None, label), (0.20, 0.08))

# ── [움직임 리얼리즘] 포지션별 최고 스프린트 속도(정규화 좌표/초) ──
# 윙어·풀백이 제일 빠르고, GK·CB가 제일 느리다. _steer_toward 가속 시스템에서 사용.
# [신규] 폭을 유지해야 하는(스트레치런) 측면 자원.
_WIDE_ROLES = {"LW", "RW", "LB", "RB", "LWB", "RWB", "LM", "RM"}

_MAX_SPEED = {
    "GK": 0.30,
    "CB": 0.55, "LB": 0.70, "RB": 0.70, "LWB": 0.78, "RWB": 0.78,
    "CDM": 0.62, "CM": 0.65, "CAM": 0.72,
    "LM": 0.75, "RM": 0.75,
    "LW": 0.88, "RW": 0.88,
    "CF": 0.72, "ST": 0.75,
}
_SMOOTH_TIME = 0.30    # 목표까지 도달하는 데 걸리는 대략적 시간(작을수록 반응 빠름)


def _smooth_damp(current, target, velocity, smooth_time, dt, max_speed):
    """임계감쇠(critically-damped) 스프링 방식으로 current를 target까지
    부드럽게 이동시킨다(Game Programming Gems 4의 SmoothDamp 알고리즘).

    [왜 이 방식으로 바꿨나] 이전엔 '목표 방향으로 속도를 가속시키는' 방식을
    썼는데, 이건 수학적으로 언더댐핑(underdamped) 스프링과 같아서 목표
    지점 근처에서 살짝 지나쳤다가 되돌아오는 진동이 생길 수 있다 — 실측
    결과 실제로 이게 "떨림"의 주 원인이었다. SmoothDamp는 파라미터를 어떻게
    잡아도 절대 오버슈트(목표를 지나쳤다가 되돌아옴)가 생기지 않도록 설계된
    공식이라, 정지된 목표 근처에서 선수가 미세하게 떠는 현상이 원천적으로
    불가능하다."""
    smooth_time = max(1e-4, smooth_time)
    omega = 2.0 / smooth_time
    x = omega * dt
    exp_ = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
    change = current - target
    orig_to = target
    max_change = max_speed * smooth_time
    change = max(-max_change, min(max_change, change))
    target = current - change
    temp = (velocity + omega * change) * dt
    new_vel = (velocity - omega * temp) * exp_
    output = target + (change + temp) * exp_
    # 오버슈트 방지: 원래 목표를 넘어서 버렸다면 그냥 목표에 딱 고정
    if (orig_to - current > 0) == (output > orig_to):
        output = orig_to
        new_vel = (output - orig_to) / dt if dt > 1e-6 else 0.0
    return output, new_vel


def _steer_toward(pl, tx, ty, dt, max_speed, smooth_time=_SMOOTH_TIME):
    # [신규] 관성(inertia) — 완전히 새로운 가속도/회전속도 물리를 얹는
    # 대신, 기존 SmoothDamp 위에 최소한의 변형만 준다: 현재 진행 방향과
    # 새 목표 방향이 크게 어긋날수록(급격한 역방향 전환) smooth_time을
    # 살짝 늘려서 잠깐 더 굼뜨게 반응하게 한다 — 전속력으로 달리다 급하게
    # 반대로 꺾을 때 순간적으로 살짝 밀리는 느낌이 생긴다(외부 검토에서
    # 지적된 "관성 부족" 문제에 대한 가벼운 보정).
    speed = math.hypot(pl["vx"], pl["vy"])
    if speed > 0.05:
        dx, dy = tx - pl["x"], ty - pl["y"]
        dist = math.hypot(dx, dy)
        if dist > 1e-6:
            dot = (pl["vx"] * dx + pl["vy"] * dy) / (speed * dist)
            if dot < 0:
                smooth_time *= 1.0 + (-dot) * 0.6  # 역방향일수록 최대 1.6배 굼뜨게
    pl["x"], pl["vx"] = _smooth_damp(pl["x"], tx, pl["vx"], smooth_time, dt, max_speed)
    pl["y"], pl["vy"] = _smooth_damp(pl["y"], ty, pl["vy"], smooth_time, dt, max_speed)
    pl["x"] = min(0.97, max(0.03, pl["x"]))
    pl["y"] = min(0.95, max(0.05, pl["y"]))


def _spread(n, base_y, step=0.22):
    if n <= 1:
        return [base_y]
    start = base_y - (n - 1) * step / 2
    return [min(0.94, max(0.06, start + i * step)) for i in range(n)]


def _corner_slots(atk_goal_x, corner_y, n_atk, n_def):
    """[신규] 코너킥 박스 크라우드 좌표를 "실전형 고정 슬롯"으로 정한다.
    예전엔 박스 안 아무 데나 매번 random.uniform으로 흩뿌려서, 상황마다
    구도가 그럴듯한 이유 없이 뒤죽박죽 바뀌는 "너무 랜덤으로 이동하는
    시뮬"처럼 보였다(사용자 지적 그대로). 실제 코너킥에서 흔히 쓰이는
    역할(니어포스트/식스야드 중앙/파포스트/박스 엣지) 슬롯을 고정해두고
    인원 수만큼 그 슬롯에 채워 넣는다 — 매번 같은 논리로 정렬되니
    "구조를 가진 정렬"처럼 보인다. 슬롯이 인원보다 적으면 순환 재사용.
    """
    goal_line_x = 1.0 if atk_goal_x >= 0.5 else 0.0
    field_dir = -1.0 if atk_goal_x >= 0.5 else 1.0   # 골라인에서 필드 안쪽으로
    near_y, far_y = corner_y, 1.0 - corner_y

    def pt(depth, y, jitter=0.02):
        x = goal_line_x + field_dir * depth + random.uniform(-jitter, jitter)
        y = y + random.uniform(-jitter, jitter)
        return (max(0.02, min(0.98, x)), max(0.06, min(0.94, y)))

    atk_base = [
        (0.05, near_y * 0.65 + 0.5 * 0.35),   # 니어포스트 쇄도
        (0.03, 0.5),                          # 식스야드 중앙
        (0.05, far_y * 0.65 + 0.5 * 0.35),    # 파포스트 쇄도
        (0.15, 0.5 - 0.14),                   # 박스 엣지(세컨볼) 좌
        (0.15, 0.5 + 0.14),                   # 박스 엣지(세컨볼) 우
    ]
    def_base = [
        (0.02, near_y * 0.55 + 0.5 * 0.45),
        (0.02, 0.5),
        (0.02, far_y * 0.55 + 0.5 * 0.45),
        (0.11, 0.5 - 0.16),
        (0.11, 0.5 + 0.16),
        (0.18, 0.5),
    ]
    atk_pts = [pt(*atk_base[i % len(atk_base)]) for i in range(n_atk)]
    def_pts = [pt(*def_base[i % len(def_base)]) for i in range(n_def)]
    return atk_pts, def_pts


def _penalty_arc_slots(atk_goal_x, spot_x, atk_team, atk_indices, def_team, def_indices):
    """[재설계 — 실제 PK 역할 배치] 예전엔 키커/GK를 뺀 20명을 그냥 두
    팀 평행한 세로줄로 늘어세웠다 — 포지션과 무관하게 다 똑같이 취급돼서
    "논리적으로 축구를 하는" 그림이 아니었다(신민용이 제공한 실제 PK
    포지셔닝 자료와 비교해서 지적된 그대로). 그 자료 기준으로 역할을
    나눈다:
      - 공격(블루): 세컨볼 침투조(ST/CF/LW/RW/CAM — 박스라인/아크 정면에
        바짝 붙어 키커의 발이 닿는 순간 튀어들 준비) + 역습 대비조
        (CB/CDM — 하프라인 쪽으로 처져서 레드의 역습에 대비) + 나머지는
        그 사이 박스 옆 라인.
      - 수비(레드): 블루 차단조(CB/LB/RB/LWB/RWB/CDM — 박스라인에서
        블루 세컨볼조보다 반 보 앞선/골대에 더 가까운 위치, 세컨볼을
        먼저 걷어낼 수 있게) + 카운터 대기조(ST/CF/LW/RW — 아크 정면
        에서 골키퍼 선방/클리어링 후 바로 치고 나갈 준비) + 나머지는
        박스 옆 라인.
    반환은 {선수idx: (x,y)} 딕셔너리(팀별) — 포지션 라벨로 역할을
    갈랐으니 굳이 순서를 맞출 필요가 없어져서 zip 대신 dict로 바꿨다.
    """
    arc_dir = -1 if atk_goal_x >= 0.5 else 1        # 골대 반대쪽(하프라인) 방향
    box_line_x = spot_x + arc_dir * 0.09            # 아크/박스 라인 바로 바깥
    # 레드 차단조는 블루 세컨볼조보다 "반 보 앞서"(골대 쪽으로 더 붙게) —
    # arc_dir 반대 방향으로 살짝 더 당긴다.
    def_block_x = box_line_x - arc_dir * 0.015
    halfway_x = 0.5 + arc_dir * 0.06                # 역습 대비조 — 하프라인 안쪽

    _ATK_CRASH = {"ST", "CF", "LW", "RW", "CAM"}
    _ATK_COVER = {"CB", "CDM"}
    _DEF_BLOCK = {"CB", "LB", "RB", "LWB", "RWB", "CDM"}
    _DEF_COUNTER = {"ST", "CF", "LW", "RW"}
    _crash_ys = [0.30, 0.70, 0.40, 0.60, 0.50, 0.34, 0.66]
    _side_ys = [0.14, 0.86, 0.20, 0.80, 0.10, 0.90]

    def _jit(v, j=0.02):
        return v + random.uniform(-j, j)

    atk_pts = {}
    _crash = [i for i in atk_indices if atk_team[i]["pos"] in _ATK_CRASH]
    _cover = [i for i in atk_indices if atk_team[i]["pos"] in _ATK_COVER]
    _rest_a = [i for i in atk_indices if i not in _crash and i not in _cover]
    random.shuffle(_crash); random.shuffle(_cover); random.shuffle(_rest_a)
    for k, i in enumerate(_crash):
        atk_pts[i] = (max(0.02, min(0.98, _jit(box_line_x, 0.015))),
                      max(0.05, min(0.95, _jit(_crash_ys[k % len(_crash_ys)]))))
    for k, i in enumerate(_cover):
        y = 0.5 + (0.10 if k % 2 == 0 else -0.10) * (k // 2 + 1)
        atk_pts[i] = (max(0.02, min(0.98, _jit(halfway_x))), max(0.05, min(0.95, y)))
    for k, i in enumerate(_rest_a):
        atk_pts[i] = (max(0.02, min(0.98, _jit(box_line_x + 0.05))),
                      max(0.05, min(0.95, _jit(_side_ys[k % len(_side_ys)]))))

    def_pts = {}
    _block = [i for i in def_indices if def_team[i]["pos"] in _DEF_BLOCK]
    _counter = [i for i in def_indices if def_team[i]["pos"] in _DEF_COUNTER]
    _rest_d = [i for i in def_indices if i not in _block and i not in _counter]
    random.shuffle(_block); random.shuffle(_counter); random.shuffle(_rest_d)
    for k, i in enumerate(_block):
        def_pts[i] = (max(0.02, min(0.98, _jit(def_block_x, 0.015))),
                      max(0.05, min(0.95, _jit(_crash_ys[k % len(_crash_ys)]))))
    for k, i in enumerate(_counter):
        y = 0.5 + (0.16 if k % 2 == 0 else -0.16) * (k // 2 + 1)
        def_pts[i] = (max(0.02, min(0.98, _jit(box_line_x))), max(0.05, min(0.95, y)))
    for k, i in enumerate(_rest_d):
        def_pts[i] = (max(0.02, min(0.98, _jit(def_block_x + 0.04))),
                      max(0.05, min(0.95, _jit(_side_ys[k % len(_side_ys)]))))

    return atk_pts, def_pts


def _find_my_slot(team_slots, my_pos):
    """[버그 수정] 내 캐릭터가 배치된 슬롯 인덱스를 찾는다. 예전엔 포지션
    라벨이 정확히 일치하는 선수만 찾다가 못 찾으면 무조건 0번(GK) 슬롯으로
    떨어져서, 예전 저장 데이터나 라벨 표기가 살짝 다른 경우(예: CB인데
    GK로 보임) 실제와 다른 포지션에 내가 표시되는 문제가 있었다.

    1) 정확히 일치하는 라벨을 먼저 찾고
    2) 없으면 POSITION_COMPAT(게임 엔진이 실제 배치 포지션을 정할 때 쓰는
       것과 동일한 호환성 순위)로 가장 가까운 슬롯을 찾고
    3) 그래도 못 찾으면 최소한 GK가 아닌 아무 필드 플레이어로는 폴백한다
       (내가 실제 GK가 아닌 이상 절대 GK로 잘못 표시되지 않도록).
    """
    for i, pl in enumerate(team_slots):
        if pl["pos"] == my_pos:
            return i
    for want in POSITION_COMPAT.get(my_pos, [my_pos]):
        for i, pl in enumerate(team_slots):
            if pl["pos"] == want:
                return i
    for i, pl in enumerate(team_slots):
        if pl["pos"] != "GK":
            return i
    return 0


def layout_formation(formation, is_home):
    """포메이션 문자열 → 11명의 (x,y) 정규화 좌표 리스트(포지션 라벨 포함)."""
    slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
    groups = {}
    for i, lab in enumerate(slots):
        groups.setdefault(lab, []).append(i)
    coords = [None] * len(slots)
    for lab, idxs in groups.items():
        base_x, base_y = _POS_XY.get(lab, (0.5, 0.5))
        ys = _spread(len(idxs), base_y)
        n = len(idxs)
        for k, idx in enumerate(idxs):
            x = base_x
            if n >= 3:
                # [버그 수정] 같은 라벨(백3/백5, 스리톱 등)이 전부 base_x
                # 하나만 공유해서 완전한 일직선(수비 5명이 담벼락처럼 한
                # 줄)으로 서 있었다. 실제 라인은 살짝 활 모양(가운데가
                # 미세하게 더 깊거나 얕음)을 이루므로, 그룹 안에서의
                # 상대위치(k/n-1)로 아주 작은 곡률을 준다. 수비 라인(CB류)
                # 은 중앙이 살짝 더 물러나고(자기 골 쪽), 공격 라인(전방 3
                # 등)은 중앙이 살짝 더 전진하도록 라벨 성격에 따라 부호를
                # 다르게 준다.
                frac = k / (n - 1)
                bulge = 4 * frac * (1 - frac)  # 0(양끝)~1(중앙) 포물선
                curve = 0.028
                sign = -1 if lab in ("CB", "LWB", "RWB") else (1 if lab in ("ST", "CF") else 0)
                x = base_x + sign * curve * bulge
            coords[idx] = (x, ys[k])
    if not is_home:
        # [버그 수정 — 근본 원인] X만 뒤집으면 공격 방향은 맞아지지만
        # 좌/우가 안 바뀐다. _POS_XY의 L/R 라벨(LW/RW, LB/RB, LWB/RWB,
        # LM/RM)은 전부 y=0.5 기준 대칭으로 설계돼 있어서, "왼쪽으로
        # 공격하는 팀 기준 왼쪽 측면은 화면 아래쪽"이 되려면 y도 같이
        # 뒤집어야 한다(피치를 통째로 180도 회전하는 것과 동치) — 실측:
        # 왼쪽으로 공격하는 원정팀의 LW가 화면 위쪽(y=0.14)에, RW가
        # 화면 아래쪽(y=0.86)에 서 있어서 완전히 뒤바뀌어 있었다(신민용
        # 지적). X만이 아니라 Y도 같이 뒤집는다.
        coords = [(1 - x, 1 - y) for x, y in coords]
    return list(zip(slots, coords))


def _lookup_formation(team_name):
    """팀 이름으로 실제 포메이션을 찾아본다. 못 찾으면(다른 나라 동명팀 등)
    기본값으로 흔한 포메이션 하나를 랜덤 선택 — 어차피 연출용이라 무방."""
    try:
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT formation FROM teams WHERE name=? LIMIT 1", (team_name,)).fetchone()
        conn.close()
        if row and row["formation"]:
            return row["formation"]
    except Exception:
        pass
    # [버그 수정] 예전엔 여기서 random.choice()를 그대로 썼는데, 이 함수는
    # __init__에서 아직 _match_seed를 걸기도 전에 호출되고, 심지어 걸린
    # 뒤라도 "이 경기 고유 시드"와 무관한 전역 소비라서 열 때마다 결과가
    # 달랐다(DB에 없는 팀 한정이라 드물지만, 걸리면 포메이션 자체가
    # 바뀌어 장면 전체가 완전히 달라 보였다). 팀 이름 자체로 안정적으로
    # 고정해서, 같은 팀은 항상 같은 포메이션이 나오게 한다.
    return ["4-4-2", "4-3-3", "4-2-3-1"][_stable_seed(team_name) % 3]


# 실제 이벤트 분(minute) 몇 분 전부터 그 팀 쪽으로 점유를 유도할지.
# 이 구간 동안 팀 전체 대형이 서서히 상대 진영으로 밀고 올라가는 게 보인다.
_BUILDUP_LEAD = 3.0


# 이벤트 텍스트 → 장면 종류 분류 (사용자가 강조한 "골/차단" 위주)
_GOAL_MARKERS = ("⚽", "🎯 페널티킥 골", "세트피스", "프리킥 골")
_CONCEDE_MARKERS = ("🥅",)
_SAVE_MARKERS = ("🧤",)
_MISS_MARKERS = ("페널티킥 실축", "🚫")  # [텍스트-영상 싱크] 놓친 찬스도 장면으로 살림
# [신규] 이 키워드가 들어간 "info" 이벤트는 실제로 휘슬이 불려 플레이가
# 멈추는 상황이라, 잠깐 선수 이동을 멈춰서(_info_freeze_until) 세트피스
# 준비 자세처럼 보이게 한다. 그 외 info(차단/드리블 하이라이트 등)는
# 계속 진행 중인 오픈플레이의 일부라 안 멈춘다.
_STOPPAGE_KEYWORDS = ("파울", "코너킥")


def _classify_event(text):
    if any(m in text for m in _MISS_MARKERS):
        return "miss_for"      # 내 팀이 공격했지만 득점 실패(PK 실축 등)
    if any(m in text for m in _GOAL_MARKERS):
        return "goal_for"      # 내 팀 득점
    if any(m in text for m in _CONCEDE_MARKERS):
        return "goal_against"  # 상대 득점(실점)
    if any(m in text for m in _SAVE_MARKERS):
        return "save"          # 우리 골키퍼 선방
    # [버그 수정] 예전엔 위 4종류에 안 걸리면 그냥 통째로 버려졌다(파울,
    # 차단, 드리블 돌파, 키패스 하이라이트 등 — 경기 통계엔 잡히는데
    # 재생 화면엔 하나도 안 나오던 원인). 이런 텍스트도 최소한 화면 위
    # 배너로는 잠깐 보여준다("info" — 선수/공 애니메이션 없이 문구만).
    if text.strip():
        return "info"
    return None


# [텍스트-영상 싱크] 실제 게임 엔진(game_engine.py/constants.py)이 만들어내는
# 문구 안에서만 스타일을 판별한다 — "측면 돌파"/"헤더" 같은 실제로 존재하지
# 않는 키워드를 지어내지 않고, GOAL_PHRASES·세트피스·PK·선방 등급 문구처럼
# 실제로 생성되는 표현에 맞춰 장면을 분기한다.
def _detect_style(text):
    if "페널티킥 실축" in text:
        return "penalty_miss"
    if "페널티킥 골" in text:
        return "penalty"
    # [신규] 상대의 PK 실점("🥅 실점 (PK)" — game_engine._roll_is_pk_concede가
    # 확률적으로 붙인 꼬리표). 이게 없으면 이 텍스트는 "🥅 실점"이라는
    # 정보 없는 문구뿐이라 항상 "normal"(오픈플레이 빌드업+슛)로 떨어져서
    # PK인데 그냥 드리블해서 슛 넣는 것처럼 보였다 — 이제 이미 만들어져
    # 있는 penalty 분기(스팟킥 1:1 + 20명 클리어 대형)를 그대로 태운다.
    if "실점" in text and "(PK)" in text:
        return "penalty"
    if "세트피스" in text:
        return "setpiece"          # CB/LB/RB 코너킥 헤더 등
    if "프리킥" in text:
        return "freekick"          # [신규] 직접 프리킥 — 코너와 별개(수비벽 형성)
    if any(k in text for k in ("극장골", "버저비터", "추가시간")):
        return "late"               # 후반 막판 극장골 — 박스 안이 북적여야 자연스러움
    if any(k in text for k in ("키패스", "침투 패스")):
        return "through"            # 어시스트가 스루패스 계열 — 더 긴 침투 런
    if "믿을 수 없는 선방쇼" in text:
        return "save_great"
    if "환상적인 선방" in text:
        return "save_good"
    if "안정적인 선방" in text:
        return "save_normal"
    return "normal"


class _Pitch(QWidget):
    """피치 배경 + 점(선수)+공을 그리는 캔버스. 상태는 부모(MatchSimViewer)가
    들고 있고, 이 위젯은 매 프레임 그 상태를 읽어서 그리기만 한다."""

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.setMinimumSize(640, 420)
        # [신규] 유튜브처럼 화면 가운데(피치 아무 곳)를 클릭하면 재생/일시정지 토글.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        self.viewer._toggle_play()
        super().mousePressEvent(ev)

    def paintEvent(self, _ev):
        v = self.viewer
        w, h = self.width(), self.height()
        pad = 20
        pw, ph = w - pad * 2, h - pad * 2

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 피치
        p.fillRect(0, 0, w, h, QColor("#0d2b12"))
        p.setPen(QPen(QColor("#2f7a3f"), 2))
        p.drawRect(pad, pad, pw, ph)
        p.drawLine(pad + pw // 2, pad, pad + pw // 2, pad + ph)
        p.drawEllipse(pad + pw // 2 - 45, pad + ph // 2 - 45, 90, 90)
        box_w, box_h = int(pw * 0.12), int(ph * 0.5)
        p.drawRect(pad, pad + (ph - box_h) // 2, box_w, box_h)
        p.drawRect(pad + pw - box_w, pad + (ph - box_h) // 2, box_w, box_h)

        # [현실성 보정] 예전엔 페널티박스만 그려서, 박스 전체가 마치 골대인
        # 것처럼 보였다(실제 골대 폭은 박스 폭의 약 1/5밖에 안 됨). 골라인
        # 위에 진짜 골대 크기(_GOAL_HALF_HEIGHT 기준, 득점/노골 판정에
        # 쓰는 값과 동일)로 별도의 골문을 짧게 튀어나오도록 그린다.
        goal_depth = 6
        goal_h = int(ph * _GOAL_HALF_HEIGHT * 2)
        goal_y = pad + (ph - goal_h) // 2
        p.setPen(QPen(QColor("#eaffea"), 3))
        p.drawRect(pad - goal_depth, goal_y, goal_depth, goal_h)
        p.drawRect(pad + pw, goal_y, goal_depth, goal_h)
        p.setPen(QPen(QColor("#2f7a3f"), 2))

        def to_px(x, y):
            return pad + x * pw, pad + y * ph

        # 선수 점
        label_font = QFont()
        label_font.setPixelSize(8)
        label_font.setBold(True)
        for team_players, color, my_idx in (
                (v.home_players, QColor("#4488ff"), v.my_slot if v.is_home else -1),
                (v.away_players, QColor("#ff5555"), v.my_slot if not v.is_home else -1)):
            for i, pl in enumerate(team_players):
                x, y = to_px(pl["x"], pl["y"])
                r = 8
                if i == my_idx:
                    p.setPen(QPen(QColor("#ffee55"), 2))
                    p.setBrush(QBrush(color))
                    r = 10
                else:
                    p.setPen(QPen(QColor("#000000"), 1))
                    p.setBrush(QBrush(color))
                p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)
                # [신규] 레퍼런스(피파 온라인 모바일 감독모드)처럼 원 안에
                # 식별 텍스트를 넣는다. 실제 등번호 데이터는 없어서(스쿼드
                # 번호 필드 자체가 없음) 대신 포지션 라벨(GK/CB/ST 등)을
                # 축약해서 넣는다 — 그냥 색깔 점이었던 예전보다 "이게
                # 누구인지" 훨씬 읽기 쉬워진다.
                p.setPen(QPen(QColor("#ffffff")))
                p.setFont(label_font)
                p.drawText(QRectF(x - r, y - r, r * 2, r * 2),
                           Qt.AlignmentFlag.AlignCenter, pl["pos"][:2])

        # 패스 궤적 잔상(공이 날아온 경로를 옅어지는 선으로 표시)
        if len(v.ball_trail) >= 2:
            pts = [to_px(x, y) for x, y, _a in v.ball_trail]
            for k in range(1, len(pts)):
                alpha = v.ball_trail[k][2]
                if alpha <= 0:
                    continue
                p.setPen(QPen(QColor(255, 255, 255, int(alpha * 0.7)), 2))
                p.drawLine(int(pts[k - 1][0]), int(pts[k - 1][1]),
                           int(pts[k][0]), int(pts[k][1]))

        # 공
        bx, by = to_px(v.ball["x"], v.ball["y"])
        p.setPen(QPen(QColor("#222"), 1))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(int(bx - 5), int(by - 5), 10, 10)

        # 배너(주요 장면 텍스트)
        if v.banner_text and v.banner_alpha > 0:
            font = QFont()
            font.setPointSize(15)
            font.setBold(True)
            p.setFont(font)
            col = QColor(v.banner_color)
            col.setAlpha(int(v.banner_alpha))
            p.setPen(col)
            p.drawText(QRectF(0, h * 0.08, w, 40),
                      Qt.AlignmentFlag.AlignCenter, v.banner_text)

        p.end()


class _SeekBar(QWidget):
    """[재생바] 클릭/드래그로 경기의 아무 시점이나 바로 이동할 수 있는
    커스텀 시크바. 원하는 순간에 멈추려면 일시정지 타이밍을 정확히 맞춰야
    했던 불편함을 없애준다. 전/후반 경계 지점(하프타임)에 세로선을 그려서
    지금 보고 있는 게 전반인지 후반인지 한눈에 알 수 있게 한다."""

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _ev):
        v = self.viewer
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track_y = h // 2 - 2
        track_h = 4

        # 배경 트랙
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2a2a2a"))
        p.drawRoundedRect(0, track_y, w, track_h, 2, 2)

        match_end = max(1e-6, v.match_end)
        frac = max(0.0, min(1.0, v.clock / match_end))

        # 진행도
        p.setBrush(QColor("#3a7fd5"))
        p.drawRoundedRect(0, track_y, round(w * frac), track_h, 2, 2)

        # 전/후반 경계선(하프타임 지점) — 전반 종료+전반 추가시간 위치
        half_frac = max(0.0, min(1.0, (45 + v.stoppage1) / match_end))
        hx = round(w * half_frac)
        p.setPen(QPen(QColor("#888888"), 1))
        p.drawLine(hx, 1, hx, h - 1)

        # 핸들(현재 위치)
        knob_x = round(w * frac)
        p.setPen(QPen(QColor("#0a0a0a"), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(knob_x - 6, h // 2 - 6, 12, 12)
        p.end()

    def _seek_from_x(self, x):
        frac = max(0.0, min(1.0, x / max(1, self.width())))
        self.viewer._seek_to(frac * self.viewer.match_end)

    def mousePressEvent(self, ev):
        self._seek_from_x(ev.position().x())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self._seek_from_x(ev.position().x())


def _stable_seed(*parts) -> int:
    """[안정 시드] 파이썬 내장 hash()는 문자열에 한해 프로세스마다 값이
    달라진다(PYTHONHASHSEED 랜덤화). 그래서 hash()로 시드를 잡으면 앱을
    껐다 켤 때마다(또는 다른 프로세스에서) 같은 입력이어도 다른 시드가
    나와, "같은 경기/같은 이벤트인데 재생할 때마다 장면이 달라지는" 문제가
    생긴다. md5는 프로세스와 무관하게 항상 같은 값을 내므로 이걸로 대체한다."""
    key_str = "|".join(str(p) for p in parts)
    digest = hashlib.md5(key_str.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


class MatchSimViewer(QDialog):
    # [개선] 화면 갱신 주기(TICK_MS)만 60ms→20ms로 낮춰 실제 렌더링을
    #   더 자주 한다(약 16.7fps → 약 50fps). 시뮬레이션 자체를 굽는 해상도
    #   (_FRAME_DT, 아래)는 그대로 0.06분 유지 — 여기 얽힌 세부 튜닝 상수들
    #   (SmoothDamp dt, 장면 진행 속도, 배너 감쇠 등이 전부 "_FRAME_DT당
    #   한 번" 기준으로 맞춰져 있음)을 건드리면 도미노로 다 다시 맞춰야 해서
    #   위험하다. 대신 재생 시점에 두 인접 프레임 사이를 보간(_apply_frame_at)
    #   해서, 굽는 해상도는 그대로 두고 화면에 보여주는 중간 경유점만
    #   늘리는 방식으로 매끄러움을 확보했다(전체 재생 속도/소요시간은 불변).
    TICK_MS = 20
    # [결정론적 재생] 실제 시간(QTimer 간격)과 무관하게, 미리 계산되는
    # 모든 프레임은 항상 이 고정된 가상 시간 간격(분 단위)으로 찍힌다.
    _FRAME_DT = 0.06
    # [수정] "1분=15초"로 순수 물리(최고속도 9m/s) 기준으로 맞췄었는데,
    # 이건 잘못된 축을 조정한 거였다. FC 온라인(실제 3D 축구 게임) 기준
    # 90분 경기가 인게임 6분(전후반 3+3분)에 끝나는 게 실증된 비율이고,
    # 이는 1분≈4초로 오히려 원래(3초)에 훨씬 가깝다. FC 온라인이 자연스러워
    # 보이는 건 재생 속도가 아니라 모션캡처 애니메이션·3D 물리 덕분이지,
    # 시간 배율 때문이 아니다 — "재생 속도(페이싱)"와 "움직임의 질
    # (애니메이션/물리)"은 서로 다른 축인데, 페이싱만 3배 느려서는
    # 같은 패턴의 움직임이 그냥 더 느리게 재생될 뿐 체감 차이가 거의
    # 없었다(실제로 그렇다는 지적을 받음). 이 엔진은 SmoothDamp로 목표
    # 좌표를 쫓는 구조라 "발을 내딛는" 개념 자체가 없고, 그건 페이싱을
    # 아무리 조절해도 해결되지 않는 구조적 한계다. 실증된 FC 온라인
    # 비율로 되돌린다.
    _SEC_PER_MIN = 4.0

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("경기 시뮬 보기")
        self.setStyleSheet("QDialog{background:#161616;color:#ccc;}")
        self.resize(760, 560)

        payload = data.get("payload", {}) or {}
        self.events_raw = payload.get("events", []) or []
        self.is_home = bool(data.get("is_home"))
        self.home_name = data.get("home_name", "홈팀")
        self.away_name = data.get("away_name", "원정팀")
        self.final_home = data.get("home_score", 0)
        self.final_away = data.get("away_score", 0)
        my_pos = payload.get("position", "CM")

        my_team_name = self.home_name if self.is_home else self.away_name
        opp_team_name = self.away_name if self.is_home else self.home_name
        my_formation = _lookup_formation(my_team_name)
        opp_formation = _lookup_formation(opp_team_name)

        home_formation = my_formation if self.is_home else opp_formation
        away_formation = opp_formation if self.is_home else my_formation
        # [신규] 전술 전진/후퇴 폭(_tactical_dx) 조회에 쓰려고 저장해둔다.
        self.home_formation = home_formation
        self.away_formation = away_formation
        # [신규] 22명 중 나(my_slot)를 뺀 21명은 지금까지 실제 로스터
        # 스탯과 완전히 무관하게 움직였다. game_engine.py가 저장해둔
        # "포메이션 슬롯 순서대로 뽑은 11명의 스탯"을 선수 생성 전에
        # 먼저 읽어둔다(아래에서 인덱스로 그대로 붙여야 하므로).
        self.lineup_stats = payload.get("lineup_stats") or {}

        self.home_players = [{"pos": lab, "x": x, "y": y, "hx": x, "hy": y, "vx": 0.0, "vy": 0.0}
                             for lab, (x, y) in layout_formation(home_formation, True)]
        self.away_players = [{"pos": lab, "x": x, "y": y, "hx": x, "hy": y, "vx": 0.0, "vy": 0.0}
                             for lab, (x, y) in layout_formation(away_formation, False)]

        # [신규] game_engine.py가 저장해둔 라인업 스탯을 포메이션 슬롯
        # 순서(인덱스)로 그대로 붙인다 — home_players[i]/away_players[i]가
        # lineup_stats["home"/"away"][i]와 대응하도록 저장 시점에 맞춰
        # 뒀다(둘 다 같은 FORMATION_SLOTS[formation] 순서를 씀). 스탯이
        # 없는 슬롯(구버전 세이브, 로스터 없음 등)은 빈 dict로 둬서, 아래
        # 소비하는 쪽이 항상 .get(key, 기본값) 형태로 안전하게 폴백한다.
        _lineup_home = (self.lineup_stats.get("home") or [])
        _lineup_away = (self.lineup_stats.get("away") or [])
        for i, pl in enumerate(self.home_players):
            pl["stats"] = (_lineup_home[i] if i < len(_lineup_home) and _lineup_home[i] else {})
        for i, pl in enumerate(self.away_players):
            pl["stats"] = (_lineup_away[i] if i < len(_lineup_away) and _lineup_away[i] else {})

        # [신규 — 고정 타겟맨/세컨드 스트라이커] 지금까진 ST 2명 중 "지금
        # 이 순간 볼과 가까운 쪽"이 매 홀더 교체마다 새로 체크인/침투런을
        # 나눠 맡았다 — 그래서 같은 선수가 어떤 공격 땐 타겟맨처럼, 다음
        # 공격 땐 세컨드 스트라이커처럼 계속 바뀌었다("고정된 역할"이 아님
        # — 신민용 지적). 진짜 투톱 체제(ST/CF 정확히 2명)에서는 이 정체성을
        # 경기 시작 시 한 번만 정해서 끝까지 유지한다 — 포지셔닝 스탯이
        # 높고 드리블이 낮을수록 "버텨주는" 타겟맨 성향으로 판정.
        def _pick_fixed_striker_roles(team):
            pool = [i for i, p in enumerate(team) if p["pos"] in ("ST", "CF")]
            if len(pool) != 2:
                return None, None
            a, b = pool
            score_a = (team[a]["stats"].get("positioning", 50)
                       - team[a]["stats"].get("dribbling", 50))
            score_b = (team[b]["stats"].get("positioning", 50)
                       - team[b]["stats"].get("dribbling", 50))
            target_man = a if score_a >= score_b else b
            second_striker = b if target_man == a else a
            return target_man, second_striker

        _tm_h, _ss_h = _pick_fixed_striker_roles(self.home_players)
        _tm_a, _ss_a = _pick_fixed_striker_roles(self.away_players)
        self._fixed_target_man_idx = {"home": _tm_h, "away": _tm_a}
        self._fixed_second_striker_idx = {"home": _ss_h, "away": _ss_a}

        my_team_slots = self.home_players if self.is_home else self.away_players
        self.my_slot = _find_my_slot(my_team_slots, my_pos)

        self.ball = {"x": 0.5, "y": 0.5}

        # [세부지표 반영] 슈팅/키패스/드리블/차단/패스성공률 — 평상시 플레이
        # 흐름(턴오버 확률, 내가 공에 관여하는 빈도)에 실제 반영한다.
        self.detail = payload.get("detail", {}) or {}
        self.team_stats = payload.get("team_stats") or {}  # [신규] 점유율 등 통계 연동용
        # [신규 — 구조 변경] match_flow.generate_possession_log()가 만든
        # 포제션 로그. 있으면(신규 경기) 이 로그가 파울/코너킥/필러 슈팅의
        # "언제·누가·어디서"를 전부 결정하고, 뷰어는 텍스트를 다시 파싱해서
        # 추측하지 않는다. 없으면(구버전 세이브) 기존 사후-추측 로직으로
        # 자동 폴백한다 — 하위호환 100% 보장.
        self.possession_log = payload.get("possession_log") or []

        # [승부차기] 애니메이션까지는 안 만들고, 경기 종료 시점에 결과
        # 배너만 보여준다(효율적으로 가자는 요청에 맞춤).
        self.pso = payload.get("pso")
        self._pso_shown = False

        # 실제 이벤트(내 골/도움/실점/선방)를 가상 시계에 배치.
        # [추가시간 반영] 원본 분(m)은 전반 추가시간=146~155(45+1~45+10),
        # 후반 추가시간=91~100(90+1~90+10)로 인코딩되어 있다(최근 축구
        # 트렌드상 후반 추가시간이 10분까지도 나오므로 그만큼 지원). 예전엔
        # 이걸 그냥 45/90으로 뭉개서 추가시간이 아예 없는 것처럼 보였다.
        # 이제는 이벤트에서 실제 추가시간 길이를 역산하고(없으면 현실적인
        # 평균 기본값), "45+2'" 표기와 시계 총 길이 모두에 반영한다.
        stoppage1 = 0  # 전반 추가시간(분)
        stoppage2 = 0  # 후반 추가시간(분)
        for m, _t in self.events_raw:
            if 146 <= m <= 155:
                stoppage1 = max(stoppage1, m - 145)
            elif 91 <= m <= 100:
                stoppage2 = max(stoppage2, m - 90)
        # 이벤트에 추가시간 기록이 없어도 실제 경기엔 보통 추가시간이 있으므로
        # 기본값을 준다(전반 평균 2분, 후반 평균 4분 — 현실적인 평균치).
        self.stoppage1 = stoppage1 or 2
        self.stoppage2 = stoppage2 or 4
        self.match_end = 90 + self.stoppage1 + self.stoppage2  # 가상 시계 총 길이
        # [버그 수정 — 근본 원인] tail 프레임(마지막 이벤트가 match_end에
        # 걸릴 때 배너/글라이드를 마저 보여주려고 추가로 굽는 프레임들)이
        # 전부 clock=match_end로 똑같이 고정된 채 저장돼서, 재생 시
        # _apply_frame_at(clock/_FRAME_DT)가 항상 같은 인덱스만 계산해
        # 그 tail 프레임들에 절대 도달하지 못했다("배너 뜨자마자 그
        # 자리에서 멈춘다"는 증상의 진짜 원인). 이제 tail 구간에서는
        # clock을 match_end에 묶어두지 않고 계속 흐르게 해서 각 tail
        # 프레임이 서로 다른(점점 커지는) clock 값을 갖게 하고, 재생 쪽
        # 경계값은 이 _true_match_end(=실제 마지막으로 구워진 clock)를
        # 쓰도록 분리한다. 화면에 보여주는 "전/후반 X'" 표시는 여전히
        # match_end 기준 그대로라 실제 축구 시간 표기는 안 바뀐다.
        self._true_match_end = self.match_end

        def _map_minute(m):
            """원본 이벤트 분(m) → 가상 시계 경과값으로 변환. 전/후반
            추가시간 구간을 실제로 '끼워넣어' 그만큼 시간이 흐르게 한다."""
            if 146 <= m <= 155:                # 전반 추가시간(최대 45+10)
                return 45 + (m - 145)
            if 91 <= m <= 100:                 # 후반 추가시간(최대 90+10)
                return 90 + self.stoppage1 + (m - 90)
            if m <= 45:                        # 전반 정규시간
                return m
            if m <= 100:                       # 후반 정규시간(46~90) — 전반 추가시간만큼 밀림
                return m + self.stoppage1
            return self.match_end              # 안전망(알 수 없는 값)

        self.timeline = []
        for m, t in self.events_raw:
            kind = _classify_event(t)
            if not kind:
                continue
            minute = _map_minute(m)
            self.timeline.append({"minute": minute, "kind": kind, "text": t,
                                   "style": _detect_style(t), "done": False})
        self.timeline.sort(key=lambda e: e["minute"])

        self.clock = 0.0        # 가상 분
        self.speed = 1.0
        self.playing = False
        self.score_home = 0
        self.score_away = 0
        self.banner_text = ""
        self.banner_color = "#ffcc00"
        self.banner_alpha = 0
        self._scene_until = 0.0   # 스크립트 장면이 끝나는 가상시각
        self._scene_kind = None
        self._scene_side = None   # "home"/"away" 공격측
        self._scene_style = "normal"
        self._scene_atk_idx = []
        self._scene_def_idx = []
        self._scene_crowd = {}
        self._scene_crowd_start = {}
        self._scene_ball_start = {"x": 0.5, "y": 0.5}
        self._pre_event = None    # 다가오는 예정 이벤트(빌드업 구간 진입 시 설정)
        # [신규 — 검증용 계측] 헤드리스 검증 스크립트가 배너 텍스트만으로
        # "의도된 순간이동"을 추측하다가, 스로인처럼 원래 배너가 안 뜨는
        # 의도된 스냅을 실제 버그로 오탐하는 일이 실측으로 확인됐다(예:
        # match#3 away#9 RW가 스로인으로 터치라인에 스냅됐는데 배너가
        # 비어있어서 "순간이동 버그"로 잘못 잡힘). 이제 스로인/골킥/코너
        # 크라우드/파울 재개/킥오프/하프타임처럼 코드가 실제로 좌표를
        # 의도적으로 스냅하는 모든 지점에서 이 시각을 직접 기록해두고,
        # 프레임 스냅샷에 실어서 검증 스크립트가 배너 텍스트 추측 없이
        # 정확히 판별할 수 있게 한다.
        self._last_restart_clock = -99.0
        self._info_glide_until = -1.0   # [신규] 대형 정렬을 여러 프레임에 걸쳐 보여주는 구간
        # [신규] "상대 팀 코너킥" 등 info 이벤트로 스냅한 키커/크라우드
        # 인덱스를 기록해둔다 — 글라이드 구간 동안 _update_player_positions가
        # 이 선수들을 건드리지 않도록(=스냅한 자리에 그대로 있도록) 하는
        # 용도(_snap_corner_crowd 참고).
        self._corner_lock_home = set()
        self._corner_lock_away = set()

        # [결정론적 재생의 핵심] 이 경기 하나에 고정된 시드를 하나 뽑아서
        # 저장해둔다. 이후 킥오프팀 결정부터 모든 틱의 패스/턴오버까지,
        # 이 시드 하나로 전체 경기가 재현 가능해진다 — 재생바로 아무 시점에
        # 시크해도 "처음부터 이 시드로 다시 재생"하면 실제 재생과 완전히
        # 동일한 결과가 나온다(_seek_to 참고).
        #
        # [버그 수정] 예전엔 매번 random.getrandbits(32)로 새 시드를 뽑아서,
        # 창을 닫았다가 "같은 경기"를 다시 열면 점유율/패스/턴오버 연출이
        # 매번 딴판으로 보였다(내 골/도움/스코어 등 진짜 데이터는 고정이라
        # 안 바뀌지만, 그 사이 연출 전체가 랜덤이라 다른 경기처럼 느껴짐).
        # 이제는 이 경기 고유값(match_details.id, 없으면 팀명+스코어+
        # 이벤트로 대체)에서 시드를 유도해서, 같은 경기는 몇 번을 다시 열어도
        # 항상 동일한 연출이 재생되도록 고정한다.
        match_id = data.get("id")
        if match_id:
            self._match_seed = int(match_id) & 0xFFFFFFFF
        else:
            # id가 없는 예외 케이스: 팀명+스코어+이벤트로 안정 시드를 만든다.
            self._match_seed = _stable_seed(
                self.home_name, self.away_name, self.final_home,
                self.final_away, self.events_raw)

        self._pre_seed_rng_state = random.getstate()
        random.seed(self._match_seed)

        # [점유·패스 엔진] 공은 더 이상 혼자 떠다니지 않고, 항상 "이 선수가
        # 갖고 있다"는 상태(possession/holder)를 따라간다. 일정 간격마다
        # 다른 선수에게 패스하거나(전진 방향 우선) 상대에게 뺏긴다(턴오버).
        self.possession = random.choice(["home", "away"])
        self.holder = self._kickoff_holder_index(self.possession)  # 실제 킥오프처럼 필드 플레이어가 시작
        self._kickoff_side = self.possession  # 후반 킥오프는 반대팀이 하므로 기억해둠
        self._halftime_reset_done = False
        self.pass_clock = 0.6
        # 팀 전체가 밀어올리는 정도(0=수비 대형, 1=완전 공격 대형) — 팀별로
        # 서서히 전환되어 "공격 전개 과정"이 눈에 보이게 한다.
        self.shape_push = {"home": 0.0, "away": 0.0}
        # [신규] 공에서 먼 선수들이 공 위치에 "즉시" 반응하지 않고 약간
        # 지연되어 인식하게 만드는 값(반응 지연). 이게 없으면 22명 전원이
        # 매 프레임 공의 실시간 좌표에 동시에 반응해서 "다같이 우르르
        # 몰려가는" 인위적인 느낌이 난다(외부 코드 리뷰에서도 지적된
        # 부분). 실제 공보다 훨씬 느리게 쫓아가는 좌표를 따로 두고, 공과
        # 가까운(=이미 플레이에 관여한) 선수만 실시간 값을, 먼 선수는 이
        # 지연값을 섞어 쓰게 한다.
        self._lagged_ball_x, self._lagged_ball_y = 0.5, 0.5

        # [신규 - 개인화] 예전엔 팀 전체가 _lagged_ball_x/y 하나만 공유해서
        # 반응 지연이 "팀 단위"로만 걸렸다. 그 결과 같은 팀 선수 21명이
        # 사실상 위상(phase)이 완전히 같은 하나의 덩어리처럼 움직여서,
        # 개별 선수가 상황을 각자 읽고 반응하는 게 아니라 "떼로 몰려다니는"
        # 인상을 줬다(사용자가 지적한 "상황에 맞춰 움직이는 게 부족하다"의
        # 핵심 원인 중 하나). 이제 각 선수에게 고유한 반응성(reaction)
        # 계수를 하나씩 심어둔다 — 0.82(둔감/느긋)~1.22(민첩/기민) 사이로,
        # 매치 시드에 종속된 결정론적 난수라 같은 경기는 항상 같은 선수가
        # 항상 같은 반응성을 갖는다(재현성 유지). 이 값은 아래
        # _update_player_positions에서 (a) 공 인식 가중치와 (b) 이동
        # 반응속도(smooth_time) 둘 다에 쓰여서, 같은 팀이라도 선수마다
        # 살짝 다른 타이밍으로 반응하는 "따로 노는" 자연스러움을 만든다.
        for _pl in self.home_players + self.away_players:
            _base_reaction = random.uniform(0.82, 1.22)
            # [신규 — 로스터 스탯 반영] positioning 스탯(50이 평균)이 있으면
            # 순수 랜덤 대신 절반은 스탯 기반으로 결정한다 — 포지셔닝
            # 좋은 선수는 계속 민첩하게, 나쁜 선수는 계속 굼뜨게(경기마다
            # 무작위로만 바뀌지 않고 실제 능력치를 반영).
            _positioning = _pl.get("stats", {}).get("positioning")
            if _positioning is not None:
                _stat_reaction = 0.82 + (_positioning / 100.0) * 0.40
                _pl["reaction"] = _base_reaction * 0.5 + _stat_reaction * 0.5
            else:
                _pl["reaction"] = _base_reaction

        # [신규 - 전환 순간] 턴오버가 일어난 그 직후 잠깐 동안 양팀 모두
        # 평소보다 빠르게 반응하게 만드는 부스트. 예전엔 공수 전환이
        # shape_push가 서서히(계수 0.04) 넘어가는 것뿐이라, 실제 축구에서
        # 가장 역동적인 "뺏자마자 질주 / 뺏기자마자 전력 복귀" 장면이 전혀
        # 안 살았다. _do_pass_or_turnover에서 턴오버가 나는 순간 이 값을
        # 채워두면 아래 _update_player_positions가 몇 틱에 걸쳐 서서히
        # 원래 속도로 감쇠시킨다.
        self._transition_timer = 0.0
        self._last_throwin_clock = -99.0  # [신규] 스로인 연속 발생 방지용 쿨다운 기준 시각
        self._last_byline_clock = -99.0   # [신규] 골라인 아웃 연속 발생 방지용 쿨다운 기준 시각
        # [재설계 — 데드볼 상태 머신] 예전엔 파울/코너킥/스로인/골킥/
        # 골라인아웃/필러슛결과 처리가 서로의 존재를 전혀 모른 채 각자
        # 독립적으로 공/possession을 스냅했고(경계 충돌 버그의 근본 원인),
        # 그걸 "정해진 시간 동안 다른 재개 금지"라는 고정 락으로 막아보려
        # 했더니 이번엔 락 시간이 필러 이벤트 간격(평균 1분 내외)보다 길어서
        # 경기 전체가 멈춰버렸다(실측 확인 — 80개 넘는 예정 이벤트가 전부
        # 처리되지 못하고 유실됨). 근본적인 문제는 "시간"으로 흉내낸 것 —
        # 필요한 건 "지금 오픈플레이인지, 데드볼이면 정확히 어떤 종류의
        # 몇 번째 단계인지"를 추적하는 진짜 상태 하나다. self._db가 None이면
        # 오픈플레이(_do_pass_or_turnover가 정상 작동), None이 아니면 그
        # 안의 kind/phase가 지금 어떤 재개를 처리 중인지 정확히 말해준다.
        # 각 kind는 "완료 조건"이 실제 애니메이션 완료(_pass_flight가
        # 끝났다/글라이드 타이머가 끝났다)에 묶여 있어서, 임의의 시간
        # 추측이 아니라 실제로 그 재개가 끝났을 때만 오픈플레이로
        # 복귀한다(_enter_dead_ball/_advance_dead_ball 참고).
        self._db = None
        self._target_lane_y = 0.5   # [신규] 전술 엔진 로그의 실제 레인 힌트
        self._TRANSITION_DURATION = 1.1   # 이 시간(가상 초) 동안 부스트가 감쇠
        self._TRANSITION_BOOST = 0.55     # 최대 부스트 시 속도 +55%

        # [패스 궤적] 공이 홀더→홀더로 순간이동하지 않고, 실제로 일정 시간에
        # 걸쳐 날아가도록 하는 상태. 진행 중엔 ball_trail에 잔상 좌표가 쌓인다.
        self._pass_flight = None
        self.ball_trail = []

        # [떨림 방지] 지원런/압박 대상 캐시 — 홀더가 바뀔 때만 _assign_roles()로
        # 갱신됨(자세한 이유는 _assign_roles 참고).
        self._support_idx = []
        self._presser_idx = None
        self._cover_idx = []   # [신규] 압박수비 다음으로 커버하는 2명
        self._advancing_mid_idx = None   # [신규] 박스투박스 전진 가담 CM
        self._holding_mid_idx = None     # [신규] 홀딩 CM
        self._check_in_idx = None        # [신규] 체크인하는 ST
        self._run_behind_idx = None      # [신규] 침투런하는 ST
        self._volpiana_idx = None        # [신규] 라 볼피아나 단독 CDM
        self._breakthrough_marks = {}    # [신규] 라인 브레이크 침투 마크
        self._breakthrough_atk_side = None
        self._assign_roles()

        # [신규 — 죽어있던 통계 살리기 / 이제는 구버전 전용 폴백] team_stats
        # ["shots"]는 계산은 되는데 예전엔 화면에 전혀 반영이 안 됐다.
        # match_flow.generate_possession_log()가 이 역할을 이제 훨씬 정확히
        # 대체한다(team_stats를 하한선으로 삼아 실제 개인 이벤트와 절대
        # 어긋나지 않게 채움 + 로그 자체에 필러 슈팅 레코드가 이미 있음).
        # possession_log가 있으면 이 블록은 완전히 건너뛴다 — 안 그러면
        # 로그의 필러 슈팅 + 이 블록의 필러 슈팅이 중복으로 끼어들어서
        # 슈팅 개수가 두 배로 뻥튀기된다. 로그가 없는 구버전 세이브에서만
        # 살아서 기존처럼 동작한다.
        if not self.possession_log and self.team_stats:
            _used_minutes = {e["minute"] for e in self.timeline}
            for _side, _side_name in (("home", self.home_name), ("away", self.away_name)):
                _stat = self.team_stats.get(_side) or {}
                _target = _stat.get("shots")
                if not _target:
                    continue
                _real_kinds = (("goal_for", "miss_for") if _side == ("home" if self.is_home else "away")
                               else ("goal_against", "save"))
                _real_count = sum(1 for e in self.timeline if e["kind"] in _real_kinds)
                _need = max(0, int(_target) - _real_count)
                if not _need:
                    continue
                _candidates = list(range(3, 88))
                random.shuffle(_candidates)
                _picked = []
                for m in _candidates:
                    if len(_picked) >= _need:
                        break
                    if all(abs(m - um) > 1 for um in _used_minutes):
                        _picked.append(m)
                        _used_minutes.add(m)
                for m in _picked:
                    self.timeline.append({
                        "minute": float(m), "kind": "info",
                        "text": f"⚡ {_side_name} 슈팅", "style": "normal", "done": False,
                        "_filler_side": _side,
                    })
            self.timeline.sort(key=lambda e: e["minute"])

        # [신규] possession_log 기반 필러 레코드 준비. 텍스트가 있는
        # 레코드(text is not None)는 이미 self.timeline에 진짜 개인
        # 이벤트로 존재하므로 여기서는 건드리지 않는다 — 여기 담기는 건
        # "텍스트 없는" 필러(팀 전체 슈팅/코너/파울/그냥 빌드업)뿐이다.
        self._plog = [dict(r, done=False) for r in self.possession_log if r.get("text") is None]

        # [결정론적 재생의 핵심] 재생 배속이나 실시간 QTimer 성능과 완전히
        # 무관하게, 가상 시계를 처음부터 끝까지 고정 dt(_FRAME_DT)로 단
        # 한 번에 전부 미리 계산해서 self._frames에 저장해둔다. 실제
        # 재생과 재생바 시크는 이후로 이 배열을 "읽기만" 하므로, 몇 번을
        # 다시 보든 어떤 배속으로 보든 같은 시각 = 항상 같은 화면이 100%
        # 보장된다.
        #
        # [예전 버그의 진짜 원인] 시드는 고정했었지만, 시뮬레이션을 실시간
        # QTimer 틱마다 그때그때 진행시켰다. 컴퓨터 성능/렌더링 부하 때문에
        # 타이머가 정확히 60ms마다 안 찍히고 밀리거나 스킵될 수 있는데,
        # 그러면 "재생 시작 후 3초" 같은 같은 실제 시간이라도 그 안에서
        # 시뮬레이션 루프가 실제로 몇 번 돌았는지가 매번 달라지고, 그
        # 안에서 뽑는 난수(패스할지/뺏길지 등) 소비 횟수도 따라서 달라져서
        # 그 이후 흐름 전체가 갈라졌다. 지금처럼 실시간과 완전히 분리해서
        # 한 번에 다 계산해두면 이 문제 자체가 원천적으로 사라진다.
        self._frames = []
        guard = 0
        while self.clock < self.match_end and guard < 20000:
            self._advance_one_tick(1.0)
            self._frames.append(self._snapshot_frame())
            guard += 1
        # [버그수정] 마지막 이벤트가 추가시간 끝자락(예: 90+6')에 걸리면
        #   stoppage2가 그 이벤트의 분(分)으로 정확히 산출되고 match_end도
        #   똑같이 그 값으로 정해져서, "이벤트 시각 == match_end"가 된다.
        #   위 while 루프는 clock이 match_end에 닿는 순간 바로 멈추는데,
        #   골 장면은 그 시각에 '시작'만 하고 완주(빌드업→슈팅→득점 반영)까지
        #   최소 1초 이상의 틱이 더 필요하다. 그 틱이 통째로 안 돌아서
        #   마지막 골이 스코어에 반영되지 않은 채로 영상이 뚝 끊기는 버그가
        #   있었다(3-3 경기인데 영상은 2-3에서 멈춤). 아직 진행 중인 장면이나
        #   아직 트리거 안 된 이벤트가 남아 있으면, 그게 다 끝날 때까지
        #   (clock은 match_end에 고정한 채로) 계속 프레임을 더 뽑는다.
        _tail_guard = 0
        while (self._scene_kind is not None
               or any(not e["done"] for e in self.timeline)
               # [버그 수정] "상대 팀 코너킥"처럼 씬 없이 배너만 뜨는
               # info 이벤트가 match_end 바로 그 순간에 걸리면, done=True로
               # 처리되자마자(배너를 막 띄운 그 프레임에서) 위의 두 조건이
               # 곧장 거짓이 되어 루프가 그 즉시 멈췄다 — 배너가 뜬 걸
               # 보여줄 시간도 없이 영상이 끝나버리는 문제. 배너가 아직
               # 옅어지는(감쇠) 중이면 그것도 마저 보여준 뒤에 끝낸다.
               or self.banner_alpha > 0
               # [버그 수정 — possession_log 필러 유실 방지] 필러 슈팅이
               # match_end 바로 직전에 트리거되면, 비행이 끝나 골/선방을
               # 반영하는 시점(self.clock+dur)이 match_end를 살짝 넘길 수
               # 있다. 이 조건이 없으면 그 골이 스코어에 반영되기 전에
               # 굽기가 멈춰버려서 "실제 최종 스코어보다 뷰어 스코어가
               # 하나 모자란" 채로 영상이 끝나는 사고가 난다(실측으로
               # 재현됨 — 4-3 경기가 4-2로 끝나는 프레임이 나왔었음).
               or bool(self._db is not None)
               or any(not r["done"] for r in self._plog)) and _tail_guard < 900:
            self._advance_one_tick(1.0)
            self._frames.append(self._snapshot_frame())
            _tail_guard += 1
        if not self._frames:
            self._frames.append(self._snapshot_frame())
        # [버그 수정 — 근본 원인] 이제 clock이 match_end에 묶여있지 않고
        # tail 구간에서도 계속 흘렀으므로, 실제로 마지막까지 구워진 clock
        # 값(=이 시점의 self.clock)을 재생 경계값으로 따로 기록해둔다.
        # _apply_frame_at/_tick/_seek_to는 이제 이 값을 재생 상한으로
        # 쓴다 — match_end(전/후반 표시용 "고정된" 경기 시간)는 그대로
        # 두고, 실제로 보여줄 수 있는 마지막 지점만 넉넉하게 확장한다.
        self._true_match_end = self.clock

        # 미리 계산하느라 끝까지 돌려버린 라이브 상태를, 실제 화면 표시를
        # 시작할 킥오프 시점(프레임 0)으로 되돌린다.
        self._reset_live_state()

        self._build_ui()
        self._frame_idx = 0
        self._apply_frame(0)
        # [버그 수정] 실제 경과시간 측정용. 아래 _tick()에서 "TICK_MS만큼
        # 지났다"고 가정하는 대신 실측한다 — 렌더링/시스템 부하로 콜백이
        # 늦게 불려도 재생 속도(페이싱)가 밀리지 않게 하기 위함.
        self._last_tick_perf = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(self.TICK_MS)

    def _snapshot_frame(self):
        """현재 라이브 상태(선수/공 위치, 스코어, 배너)를 프레임 하나로
        캡처한다. _precompute 루프가 매 스텝 후 이걸 호출해 self._frames에
        쌓는다."""
        return {
            "clock": self.clock,
            "home": [(pl["x"], pl["y"]) for pl in self.home_players],
            "away": [(pl["x"], pl["y"]) for pl in self.away_players],
            "ball": (self.ball["x"], self.ball["y"]),
            "score_home": self.score_home,
            "score_away": self.score_away,
            "banner_text": self.banner_text,
            "banner_color": self.banner_color,
            "banner_alpha": self.banner_alpha,
            "last_restart_clock": self._last_restart_clock,
        }

    def _reset_live_state(self):
        """모든 라이브 상태를 킥오프 시점(프레임 0)으로 되돌린다. 프레임
        사전 계산 직후, 화면 표시를 처음부터 시작하기 위해 호출한다."""
        for team in (self.home_players, self.away_players):
            for pl in team:
                pl["x"], pl["y"] = pl["hx"], pl["hy"]
                pl["vx"] = pl["vy"] = 0.0
        self.ball["x"], self.ball["y"] = 0.5, 0.5
        self.clock = 0.0
        self.score_home = 0
        self.score_away = 0
        self.banner_text = ""
        self.banner_alpha = 0
        self._pso_shown = False
        self._scene_kind = None
        self._scene_side = None
        self._scene_style = "normal"
        self._scene_atk_idx = []
        self._scene_def_idx = []
        self._scene_crowd = {}
        self._scene_crowd_start = {}
        self._pre_event = None
        self._last_restart_clock = -99.0
        self._info_glide_until = -1.0
        self._corner_lock_home = set()
        self._corner_lock_away = set()
        self._plog = [dict(r, done=False) for r in self.possession_log if r.get("text") is None]
        self._last_throwin_clock = -99.0
        self._last_byline_clock = -99.0
        self._db = None
        self._target_lane_y = 0.5
        self._pass_flight = None
        self.ball_trail = []
        self._halftime_reset_done = False
        self.possession = self._kickoff_side
        self.holder = self._kickoff_holder_index(self.possession)
        self.pass_clock = 0.6
        self.shape_push = {"home": 0.0, "away": 0.0}
        self._lagged_ball_x, self._lagged_ball_y = 0.5, 0.5
        for e in self.timeline:
            e["done"] = False
        self._assign_roles()

    def _apply_frame(self, idx):
        """[재생바 시크 전용] 미리 계산된 self._frames[idx]를 화면 표시
        상태에 그대로(보간 없이) 반영한다. 드래그 중엔 정확히 그 프레임을
        보여주는 게 맞아서 보간하지 않는다."""
        idx = max(0, min(len(self._frames) - 1, idx))
        f = self._frames[idx]
        for pl, (x, y) in zip(self.home_players, f["home"]):
            pl["x"], pl["y"] = x, y
        for pl, (x, y) in zip(self.away_players, f["away"]):
            pl["x"], pl["y"] = x, y
        self.ball["x"], self.ball["y"] = f["ball"]
        self.clock = f["clock"]
        self._finish_apply(idx)

    def _apply_frame_at(self, clock_value):
        """[재생 전용] 임의의 연속적인 clock 값에 대해, 인접한 두 사전계산
        프레임 사이를 선형보간해서 표시한다. 프레임을 굽는 해상도
        (_FRAME_DT)는 그대로 두고(내부 튜닝 상수들과 얽혀 있어 안 건드림),
        화면 갱신 주기(TICK_MS)만 낮춰서 그 사이 경유점을 보간으로 채워
        넣는 방식 — 실제 재생 속도(전체 소요시간)는 전혀 바뀌지 않으면서
        움직임만 더 매끄럽게 보인다. 스코어/배너처럼 불연속적인 값은
        보간하지 않고 앞쪽(idx0) 프레임 값을 그대로 쓴다(득점 반영 시점이
        어긋나면 안 되므로)."""
        clock_value = max(0.0, min(self._true_match_end, clock_value))
        self.clock = clock_value
        float_idx = clock_value / self._FRAME_DT
        last = len(self._frames) - 1
        idx0 = max(0, min(last, int(float_idx)))
        idx1 = min(last, idx0 + 1)
        frac = 0.0 if idx1 == idx0 else (float_idx - idx0)
        f0, f1 = self._frames[idx0], self._frames[idx1]

        for pl, (x0, y0), (x1, y1) in zip(self.home_players, f0["home"], f1["home"]):
            pl["x"], pl["y"] = x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac
        for pl, (x0, y0), (x1, y1) in zip(self.away_players, f0["away"], f1["away"]):
            pl["x"], pl["y"] = x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac
        bx0, by0 = f0["ball"]
        bx1, by1 = f1["ball"]
        self.ball["x"] = bx0 + (bx1 - bx0) * frac
        self.ball["y"] = by0 + (by1 - by0) * frac
        self._finish_apply(idx0)

    def _finish_apply(self, idx):
        """[재생/시크 공용 마무리] 스코어·배너·패스잔상·시계 라벨처럼
        보간하지 않는(또는 보간할 필요 없는) 상태들을 idx 프레임 기준으로
        갱신한다. _apply_frame과 _apply_frame_at이 공유한다."""
        self._frame_idx = idx
        f = self._frames[idx]
        self.score_home = f["score_home"]
        self.score_away = f["score_away"]
        new_score_text = (f"⚽ {self.home_name}  {self.score_home} - "
                           f"{self.score_away}  {self.away_name}")
        if self.score_lbl.text() != new_score_text:
            self.score_lbl.setText(new_score_text)
        self.banner_text = f["banner_text"]
        self.banner_color = f["banner_color"]
        self.banner_alpha = f["banner_alpha"]
        # 패스 궤적 잔상: 직전 몇 프레임의 공 위치를 옅어지는 흔적으로
        # 재구성한다(예전엔 매 틱 누적/감쇠시키는 별도 상태였지만, 이제
        # 프레임 자체가 기록이므로 과거 프레임에서 그때그때 다시 뽑아내면
        # 된다).
        trail = []
        for back in range(8, 0, -1):
            j = idx - back
            if j < 0:
                continue
            bx, by = self._frames[j]["ball"]
            alpha = 255 - back * 28
            if alpha > 0:
                trail.append([bx, by, alpha])
        self.ball_trail = trail
        self.clock_lbl.setText(
            "전반 {}   후반 {}".format(*self._display_halves(self.clock)))

    def _gk_index(self, side):
        team = self.home_players if side == "home" else self.away_players
        for i, pl in enumerate(team):
            if pl["pos"] == "GK":
                return i
        return 0

    def _kickoff_holder_index(self, side):
        """[킥오프] 실제 축구에서 킥오프는 골키퍼가 아니라 센터서클의 필드
        플레이어(보통 스트라이커나 중앙 미드필더)가 한다. ST/CF → CAM/CM
        순서로 찾고, 아무도 없으면 GK가 아닌 아무 필드 플레이어로 폴백."""
        team = self.home_players if side == "home" else self.away_players
        for want in ("ST", "CF", "CAM", "CM"):
            for i, pl in enumerate(team):
                if pl["pos"] == want:
                    return i
        for i, pl in enumerate(team):
            if pl["pos"] != "GK":
                return i
        return self._gk_index(side)

    def _snap_corner_crowd(self, attacking_side):
        """[신규] 코너킥 발생 시 박스 주변으로 대부분의 선수를 즉시 스냅
        시킨다. 세트피스 씬(우리 팀 코너킥)은 자체 크라우드 로직이 있지만,
        "상대 팀 코너킥"은 배너만 뜨는 info라 이 함수로 대신 처리한다."""
        self._last_restart_clock = self.clock
        atk_goal_x = 1.0 if attacking_side == "home" else 0.0
        box_x = 0.88 if atk_goal_x == 1.0 else 0.12
        box_dir = 1 if atk_goal_x == 1.0 else -1
        atk_team = self.home_players if attacking_side == "home" else self.away_players
        def_team = self.away_players if attacking_side == "home" else self.home_players

        # [버그 수정] 예전엔 코너 키커를 아예 안 두고 공만 순간이동시켜서
        # "코너 근처에 아무도 없이 공만 있다"는 지적 그대로였다. 실제
        # 코너킥을 차는 선수(측면 자원 우선)를 정해서 코너 플래그에 세운다.
        wide_pool = [i for i, pl in enumerate(atk_team)
                     if pl["pos"] in ("LW", "RW", "LB", "RB", "LWB", "RWB")]
        taker = random.choice(wide_pool) if wide_pool else random.choice(
            [i for i, pl in enumerate(atk_team) if pl["pos"] != "GK"])
        taker_pos = atk_team[taker]["pos"]
        corner_y = 0.04 if taker_pos in ("LW", "LB", "LWB") else 0.96
        corner_x = max(0.01, min(0.99, box_x + box_dir * 0.10))
        atk_team[taker]["x"], atk_team[taker]["y"] = corner_x, corner_y
        atk_team[taker]["vx"] = atk_team[taker]["vy"] = 0.0

        crowd_atk_pool = [i for i, pl in enumerate(atk_team) if pl["pos"] != "GK" and i != taker]
        crowd_def_pool = [i for i, pl in enumerate(def_team) if pl["pos"] != "GK"]
        random.shuffle(crowd_atk_pool)
        random.shuffle(crowd_def_pool)
        # [버그 수정] 예전엔 각각 5명/6명으로 캡을 걸어서, 그보다 인원이
        # 많은 포메이션이면 남는 수비수/공격수가 아예 박스로 안 오고
        # 원래 있던 자리(종종 자기 골키퍼 옆 등 엉뚱한 곳)에 그대로
        # 남아있었다("수비수들이 안 움직인다"는 지적 그대로). 이제 역습/
        # 아웃볼 대비로 딱 1명씩만 남기고 나머지는 전부 박스로 보낸다.
        atk_stay = crowd_atk_pool[-1:] if len(crowd_atk_pool) > 3 else []
        def_stay = crowd_def_pool[-1:] if len(crowd_def_pool) > 3 else []
        crowd_atk = [i for i in crowd_atk_pool if i not in atk_stay]
        crowd_def = [i for i in crowd_def_pool if i not in def_stay]

        # [버그 수정] 예전엔 박스 안 좌표를 매번 random.uniform으로 완전히
        # 무작위 흩뿌려서, 정렬 논리가 안 보이는 "그냥 랜덤한 잡음"처럼
        # 보였다("너무 랜덤으로 이동하는 시뮬이 반복된다"는 지적 그대로).
        # 니어포스트/식스야드/파포스트/박스 엣지 같은 실전 슬롯에 채워
        # 넣어서 매번 같은 논리로 정렬되는 "구조를 가진" 그림을 만든다.
        atk_pts, def_pts = _corner_slots(atk_goal_x, corner_y, len(crowd_atk), len(crowd_def))
        for i, (tx, ty) in zip(crowd_atk, atk_pts):
            atk_team[i]["x"], atk_team[i]["y"] = tx, ty
            atk_team[i]["vx"] = atk_team[i]["vy"] = 0.0
        for i, (tx, ty) in zip(crowd_def, def_pts):
            def_team[i]["x"], def_team[i]["y"] = tx, ty
            def_team[i]["vx"] = def_team[i]["vy"] = 0.0

        # [버그 수정 — 근본 원인] 여기서 스냅한 좌표(키커+크라우드)가, 바로
        # 다음 줄 이후 같은 틱/이어지는 글라이드 구간에서 호출되는
        # _update_player_positions()에 의해 즉시 덮어써지고 있었다. 그
        # 함수는 실제 "씬"(골/슛) 중일 때만 _scene_atk_idx/_scene_crowd를
        # 보고 해당 선수를 건너뛰는데, "상대 팀 코너킥"은 씬이 아니라
        # info라서 그 보호를 하나도 못 받았다 — 그래서 여기서 아무리 박스
        # 주변으로 잘 모아놔도 바로 다음 틱에 평소 포메이션 공식이 다시
        # 계산되어 원래 자리로 끌려가버렸다(사용자가 캡처한 화면의 "코너
        # 근처에 아무도 없다"/"수비 라인이 그대로다"가 이 증상 그대로).
        # 이제 이 선수들의 인덱스를 락(lock)으로 기록해두고,
        # _update_player_positions가 글라이드 구간(_info_glide_until) 동안
        # 이 락에 걸린 선수는 손대지 않도록 건너뛴다.
        def_side = "away" if attacking_side == "home" else "home"
        atk_locked = set(crowd_atk) | {taker}
        def_locked = set(crowd_def)
        if attacking_side == "home":
            self._corner_lock_home, self._corner_lock_away = atk_locked, def_locked
        else:
            self._corner_lock_away, self._corner_lock_home = atk_locked, def_locked

        # [버그 수정] 공을 목표 지점으로 순간이동시키지 않고, 이미 있는
        # 패스 궤적 시스템(_start_pass_flight)을 그대로 재사용해서 코너
        # 플래그 → 박스 안(크라우드가 모인 지점 근처)으로 실제 아치형
        # 크로스 궤적을 그린다. 예전엔 공이 그냥 순간이동한 뒤 평소
        # 로직(홀더 따라가기)이 이어받아서 "중앙으로 던지는" 것처럼
        # 보였는데, 이제 실제 크로스처럼 날아간다.
        # [버그 수정 — 부호 반전] box_dir는 "박스 안쪽(골라인 방향)으로
        # 더 들어가는 방향"이 이미 +부호로 정의돼 있다(오른쪽 박스면 +1=
        # 오른쪽/골라인 쪽, 왼쪽 박스면 -1=왼쪽/골라인 쪽). 그런데 여기선
        # box_dir에 음수(-0.06)를 곱해서 정확히 반대 방향(박스 밖, 오히려
        # 하프라인 쪽)으로 밀어버리고 있었다 — 그래서 크로스가 박스를
        # 그대로 통과해서 한참 바깥의 선수에게 배달되는 것처럼 보였다
        # (사용자가 캡처한 그 대각선 그대로). 부호를 바로잡아 박스 안쪽
        # (골문 근처)으로 도착하게 한다.
        target_y = 0.5 + random.uniform(-0.16, 0.16)
        target_x = max(0.02, min(0.98, box_x + box_dir * 0.06))
        self.ball["x"], self.ball["y"] = corner_x, corner_y
        self.ball_trail = []
        self._start_pass_flight((corner_x, corner_y), (target_x, target_y))

        # [버그 수정 — 근본 원인] 이 함수는 크로스 궤적만 만들고
        # self.possession/self.holder는 한 번도 안 바꿨다. 그러면 크로스가
        # 도착한 뒤 평소 흐름(_update_possession)이 재개될 때, 코너킥 전에
        # 남아있던 "예전 홀더"가 그대로 이어져서 — 팀도 위치도 이번 코너
        # 상황과 전혀 무관한 선수한테 공이 붙어버렸다. 그 선수가 하필
        # 자기 팀 진영 쪽에 있었다면 "코너킥 받은 팀이 갑자기 자기 골대
        # 쪽으로 달리는" 것처럼 보였다(정확히 지적된 그 버그). 크로스를
        # 받는 쪽(공격팀 크라우드 중 낙하 지점에 가장 가까운 선수 = 헤더
        # 경합에서 이겨 첫 터치한 선수 역할)을 명시적으로 새 홀더로 지정
        # 한다.
        if crowd_atk:
            new_holder = min(crowd_atk, key=lambda i: abs(atk_team[i]["x"] - target_x)
                                                      + abs(atk_team[i]["y"] - target_y))
        else:
            new_holder = taker
        self.possession = attacking_side
        self.holder = new_holder
        self._assign_roles()

    def _assign_roles(self):
        """[떨림 방지] 홀더가 바뀔 때(패스/턴오버/초기화 시점)만 호출.
        지원런 2명과 압박 수비 1명을 정해서 self._support_idx/_presser_idx에
        고정해둔다 — 매 틱 재계산하면 근소한 거리차로 대상이 프레임마다
        뒤바뀌며 목표 좌표가 순간 점프해 떨림처럼 보이기 때문에, 볼 소유
        국면이 유지되는 동안은 값을 바꾸지 않는다."""
        holder_side = self.possession
        holder_team = self.home_players if holder_side == "home" else self.away_players
        opp_team = self.away_players if holder_side == "home" else self.home_players
        holder = holder_team[self.holder]
        sign_holder = 1 if holder_side == "home" else -1

        # [버그 수정 — 겹침 방지] LW/RW/LB/RB(_WIDE_ROLES)는 이제 별도의
        # "오버래핑 와이드런"(터치라인까지 적극적으로 전진) 로직을 갖고
        # 있다. 이 서포트런 풀에 같이 들어가면, 아래 elif 체인에서 서포트
        # 분기가 와이드런 분기보다 먼저 평가돼 target_x/y를 통째로
        # 덮어써버려서 "터치라인으로 안 뛰고 홀더 쪽으로 당겨지는" 충돌이
        # 났다(실측 확인됨). 측면 자원은 여기서 제외해 자기 고유의 와이드
        # 런을 그대로 유지하게 한다.
        _support_pool = [i for i, pl in enumerate(holder_team)
                         if i != self.holder and pl["pos"] not in _WIDE_ROLES
                         and (pl["x"] - holder["x"]) * sign_holder > -0.06]
        _upper = [i for i in _support_pool if holder_team[i]["y"] < holder["y"] - 0.04]
        _lower = [i for i in _support_pool if holder_team[i]["y"] > holder["y"] + 0.04]

        def _nearest_of(pool):
            return min(pool, key=lambda i: abs(holder_team[i]["x"] - holder["x"])
                                          + abs(holder_team[i]["y"] - holder["y"])) if pool else None

        _picks = [i for i in (_nearest_of(_upper), _nearest_of(_lower)) if i is not None]
        if len(_picks) < 2:
            _rest = sorted((i for i in _support_pool if i not in _picks),
                           key=lambda i: abs(holder_team[i]["x"] - holder["x"])
                                        + abs(holder_team[i]["y"] - holder["y"]))
            _picks += _rest[:2 - len(_picks)]
        self._support_idx = _picks[:2]

        self._presser_idx = min(
            range(len(opp_team)),
            key=lambda i: abs(opp_team[i]["hx"] - holder["x"]) + abs(opp_team[i]["hy"] - holder["y"]))

        # [신규] 압박수비(presser) 다음으로 가까운 2명을 "커버"로 지정한다.
        # 예전엔 압박수비 한 명만 반응하고 나머지 수비는 전부 똑같은
        # 일반 공식만 써서, 한 명이 튀어나가도 뒤가 안 메워지는(=유기적
        # 커버 회전이 없는) 문제가 있었다. GK는 커버 대상에서 제외.
        self._cover_idx = sorted(
            (i for i in range(len(opp_team))
             if i != self._presser_idx and opp_team[i]["pos"] != "GK"),
            key=lambda i: abs(opp_team[i]["hx"] - holder["x"]) + abs(opp_team[i]["hy"] - holder["y"])
        )[:2]

        # [신규] 미드필드 로테이션(박스투박스). CM이 2명 이상이면 다 같은
        # 공식으로 똑같이 움직이는 대신, 볼과 가까운 한 명은 "전진 가담"
        # (박스까지 올라감), 먼 한 명은 "홀딩"(뒤에 남아 balance/pivot
        # 역할)으로 역할을 나눈다. 홀더 바뀔 때만 재배정해 떨림을 막는다.
        cm_pool = [i for i, pl in enumerate(holder_team)
                   if pl["pos"] == "CM" and i != self.holder]
        if len(cm_pool) >= 2:
            cm_pool.sort(key=lambda i: abs(holder_team[i]["x"] - holder["x"])
                                       + abs(holder_team[i]["y"] - holder["y"]))
            self._advancing_mid_idx = cm_pool[0]
            self._holding_mid_idx = cm_pool[-1]
        else:
            self._advancing_mid_idx = None
            self._holding_mid_idx = None

        # [신규] 스트라이커 체크인/침투런 분화. ST가 2명이면 다 같은 자리에
        # 나란히 서 있는 대신, 볼과 가까운 한 명은 "체크인"(볼을 받으러
        # 내려옴), 먼 한 명은 "침투런"(최전방에 남아 뒷공간을 노림)으로
        # 나눈다.
        # [버그 수정 — 범위 확장, 그리고 겹침 방지] 예전엔 ST/CF가 2명
        # 있는 포메이션(투톱)에서만 체크인/침투런이 발동해서, ST가 1명뿐인
        # 흔한 원톱 포메이션(4-2-3-1 등)에서는 단 한 번도 안 나왔다. CAM을
        # 후보에 추가해 원톱에서도(ST+CAM 조합으로) 나오게 했다. 단, LW/RW
        # 는 이 풀에서 제외한다 — 이 둘은 이제 별도의 "오버래핑 와이드런"
        # 로직(터치라인까지 적극적으로 전진)을 갖고 있는데, 이 체크인/
        # 침투런 분기가 아래 elif 체인에서 그 로직보다 나중에 평가돼서
        # target_x/y를 통째로 덮어써버리는 충돌이 있었다(실측 확인 —
        # 와이드런 수식을 넣었는데도 LW/RW가 여전히 중앙 쪽에 머물렀음).
        st_pool = [i for i, pl in enumerate(holder_team)
                   if pl["pos"] in (_ATTACK_ROLES - {"LW", "RW"}) and i != self.holder]
        _fixed_tm = self._fixed_target_man_idx.get(holder_side)
        _fixed_ss = self._fixed_second_striker_idx.get(holder_side)
        if (_fixed_tm is not None and _fixed_ss is not None
                and _fixed_tm != self.holder and _fixed_ss != self.holder):
            # [신규 — 고정 역할] 진짜 투톱 체제는 매번 다시 계산하지 않고
            # 경기 시작 때 정한 정체성을 그대로 쓴다 — 세컨드 스트라이커는
            # 항상 체크인(내려와서 연계), 타겟맨은 항상 침투런(최전방 유지).
            self._check_in_idx = _fixed_ss
            self._run_behind_idx = _fixed_tm
        elif len(st_pool) >= 2:
            st_pool.sort(key=lambda i: abs(holder_team[i]["x"] - holder["x"])
                                       + abs(holder_team[i]["y"] - holder["y"]))
            self._check_in_idx = st_pool[0]
            self._run_behind_idx = st_pool[-1]
        else:
            self._check_in_idx = None
            self._run_behind_idx = None

        # [신규 — 라 볼피아나] 단독 CDM(더블 피벗이 아닌 원볼란치 체제
        # — 4-1-4-1/3-5-2/4-3-3 등)만 대상으로 한다. CDM이 2명인
        # 포메이션(4-2-3-1)은 이미 홀딩/앵커로 역할이 나뉘어 있어
        # 이 움직임이 필요 없다.
        cdm_pool = [i for i, pl in enumerate(holder_team)
                    if pl["pos"] == "CDM" and i != self.holder]
        self._volpiana_idx = cdm_pool[0] if len(cdm_pool) == 1 else None

        # [신규 - 돌파 마크] 지금까지는 "홀더 근처"만 반응했다(프레서 1명+
        # 커버 2명). 그래서 공을 갖고 있지 않은 채 이미 수비 라인 뒤까지
        # 파고든 위험한 침투 선수는 완전히 무방비였다(사용자가 캡처한
        # "미드필더 라인 뚫었는데 아무도 안 막는다"가 이 증상 그대로).
        # 공격측 선수 중 상대의 최종 수비 라인(오프사이드 클램프와 동일한
        # 기준선)보다 더 깊숙이 들어온 선수를 찾아서, 프레서/커버로 이미
        # 반응 중이지 않은 나머지 수비수 중 가장 가까운 사람을 하나씩
        # 붙인다(맨마킹).
        #
        # [버그 수정 — 근본 원인] "최종라인보다 더 뒤"라는 조건 하나만
        # 쓰다 보니, 백라인이 이미 로우블록으로 내려앉아 있으면(수비진
        # 자체가 박스 안 깊숙이 있음) 그보다 "더 뒤"까지 파고든 선수만
        # 마크 대상이 되고, 정작 박스 안(수비 라인 앞이라도)에 서 있는
        # 위험한 공격수는 라인을 못 넘었다는 이유로 계속 무방비로
        # 방치됐다(실측: 박스 근접 상황 중 14~23%가 무마크). "라인
        # 돌파" 조건과 별개로 "상대 진영 박스 안(골라인 기준 0.20 이내)"
        # 조건을 추가해 두 기준을 합집합으로 마크 후보에 넣는다. 최대
        # 3명까지만 — 전원이 쫓아가면 이번엔 반대로 뒷공간이 텅 빈다.
        self._breakthrough_marks = {}
        self._breakthrough_atk_side = holder_side
        _BOX_DEPTH = 0.20
        own_goal_x_def = 1.0 if sign_holder > 0 else 0.0
        breakers = set()
        opp_def_xs = [p["x"] for p in opp_team if p["pos"] != "GK"]
        if opp_def_xs:
            if sign_holder > 0:
                last_line = max(opp_def_xs)
                breakers |= {i for i, p in enumerate(holder_team)
                             if i != self.holder and p["x"] > last_line + 0.03}
            else:
                last_line = min(opp_def_xs)
                breakers |= {i for i, p in enumerate(holder_team)
                             if i != self.holder and p["x"] < last_line - 0.03}
        if sign_holder > 0:
            breakers |= {i for i, p in enumerate(holder_team)
                         if i != self.holder and p["x"] > own_goal_x_def - _BOX_DEPTH}
        else:
            breakers |= {i for i, p in enumerate(holder_team)
                         if i != self.holder and p["x"] < own_goal_x_def + _BOX_DEPTH}
        if breakers:
            breakers = sorted(breakers, key=lambda i: abs(holder_team[i]["x"] - own_goal_x_def))
            used_defs = {self._presser_idx} | set(self._cover_idx)
            avail_defs = [i for i in range(len(opp_team))
                          if i not in used_defs and opp_team[i]["pos"] != "GK"]
            for atk_i in breakers[:3]:
                if not avail_defs:
                    break
                atk_pl = holder_team[atk_i]
                nearest = min(avail_defs, key=lambda i: abs(opp_team[i]["x"] - atk_pl["x"])
                                                        + abs(opp_team[i]["y"] - atk_pl["y"]))
                self._breakthrough_marks[nearest] = atk_i
                avail_defs.remove(nearest)

    # ── UI ──────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)

        hdr = QLabel(f"⚽ {self.home_name}  {self.score_home} - {self.score_away}  {self.away_name}")
        hdr.setStyleSheet("color:#fff;font-size:15px;font-weight:bold;")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.score_lbl = hdr
        root.addWidget(hdr)

        self.clock_lbl = QLabel("전반 0'   후반 0'")
        self.clock_lbl.setStyleSheet("color:#888;font-size:12px;")
        self.clock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.clock_lbl)

        self.pitch = _Pitch(self)
        root.addWidget(self.pitch, 1)

        self.seek_bar = _SeekBar(self)
        root.addWidget(self.seek_bar)

        ctrl = QHBoxLayout()
        self.play_btn = QPushButton("▶ 재생")
        self.play_btn.clicked.connect(self._toggle_play)
        ctrl.addWidget(self.play_btn)

        self.speed_combo = QComboBox()
        for s in ["1x", "2x", "4x"]:
            self.speed_combo.addItem(s)
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)
        ctrl.addWidget(self.speed_combo)

        # [신규] 디버그 캡처 — 재생 중 "이거 이상한데?" 싶은 순간 누르면,
        # 그 앞뒤 몇 초 구간의 22명+공 좌표/배너를 통째로 JSON 파일로
        # 저장한다. 말로 "전반 O분쯤 이상했다"고 설명하는 것보다 훨씬
        # 정확하게, 정확한 프레임 단위로 재현/진단할 수 있다.
        debug_btn = QPushButton("🐛 디버그 캡처")
        debug_btn.setToolTip("방금 화면이 이상해 보였다면 눌러주세요 — "
                              "현재 시점 앞뒤 몇 초 구간을 파일로 저장합니다.")
        debug_btn.clicked.connect(self._export_debug_capture)
        ctrl.addWidget(debug_btn)

        ctrl.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        ctrl.addWidget(close_btn)
        root.addLayout(ctrl)

        note = QLabel("※ 실제 좌표 데이터가 없는 통계 시뮬이라, 득점/선방 '시점'은 실제 기록대로이고 "
                      "움직임 자체는 포메이션 기준 연출입니다.  (화면을 클릭해도 재생/일시정지됩니다)")
        note.setStyleSheet("color:#555;font-size:10px;")
        note.setWordWrap(True)
        root.addWidget(note)

    def _toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            # [버그 수정] 일시정지해뒀다가 다시 재생하면, 그 사이(정지해
            # 있던 실제 시간)가 "경과시간"으로 한꺼번에 잡혀서 시계가 확
            # 튀는 문제를 막는다 — 재생 재개 시점을 기준으로 다시 잰다.
            self._last_tick_perf = time.perf_counter()
        self.play_btn.setText("⏸ 일시정지" if self.playing else "▶ 재생")

    def _on_speed_changed(self, text):
        self.speed = float(text.replace("x", ""))

    def _export_debug_capture(self):
        """[신규] 재생 화면이 이상해 보이는 순간 이 버튼을 누르면, 현재
        시점 앞뒤 몇 초 구간의 22명+공 좌표와 배너 텍스트를 통째로 JSON
        파일로 저장한다. "전반 O분쯤 이상했다" 같은 말보다 훨씬 정확하게
        정확한 프레임 단위로 재현/진단할 수 있다 — 이 창을 만든 사람(개발
        자)에게 이 파일만 전달하면, 그 순간 어떤 씬/상황이었는지, 어떤
        선수가 어디서 어디로 움직였는지를 코드 없이도 그대로 들여다볼 수
        있다.
        """
        try:
            idx = max(0, min(len(self._frames) - 1, int(self.clock / self._FRAME_DT)))
            # [주의] self.clock은 "실제 초"가 아니라 가상 경기 시계(분)다.
            # 앞뒤 1.5분(경기 시계 기준)씩 잘라내면 충분히 상황을 파악할
            # 수 있으면서도 파일이 너무 커지지 않는다.
            window_frames = int(1.5 / self._FRAME_DT)
            lo = max(0, idx - window_frames)
            hi = min(len(self._frames), idx + window_frames + 1)

            capture = {
                "captured_at_clock": self.clock,
                "home_name": self.home_name,
                "away_name": self.away_name,
                "score_at_capture": [self.score_home, self.score_away],
                "home_positions": [pl["pos"] for pl in self.home_players],
                "away_positions": [pl["pos"] for pl in self.away_players],
                "frame_dt": self._FRAME_DT,
                "clicked_frame_index": idx,
                "window_start_index": lo,
                "frames": self._frames[lo:hi],
            }

            out_dir = os.path.join(os.getcwd(), "debug_captures")
            os.makedirs(out_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"debug_capture_{ts}_clock{self.clock:.1f}.json"
            fpath = os.path.join(out_dir, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(capture, f, ensure_ascii=False, indent=1)

            QMessageBox.information(
                self, "디버그 캡처 저장됨",
                f"저장 완료:\n{fpath}\n\n이 파일을 그대로 보내주시면 정확히 이 순간을 재현해서 확인할게요."
            )
        except Exception as ex:
            QMessageBox.warning(self, "디버그 캡처 실패", f"저장 중 오류가 발생했습니다:\n{ex}")

    def closeEvent(self, event):
        self.timer.stop()
        # 이 창이 열려있는 동안 시드를 고정해뒀던 전역 random 상태를 원래대로
        # 되돌린다 — 창을 닫은 뒤엔 앱의 다른 랜덤 로직에 영향이 없어야 한다.
        random.setstate(self._pre_seed_rng_state)
        super().closeEvent(event)

    # ── 재생 (미리 계산된 프레임을 읽기만 함) ──────────────
    def _tick(self):
        # [버그 수정] 예전엔 여기서 매 틱 시뮬레이션을 직접 진행시켰다.
        # 실시간 QTimer 틱 수가 실행마다 미세하게 달라질 수 있어서(시스템
        # 성능/렌더링 부하), 그 안에서 소비되는 난수도 매번 달라져 "같은
        # 실제 시간이지만 다른 장면"이 나오는 원인이 됐다. 이제는 전체
        # 경기가 __init__에서 이미 다 계산되어 있으므로, 여기서는 그냥
        # "다음에 보여줄 시점"만 계산해서 읽어 보여준다 — 배속이나
        # 프레임 드랍과 무관하게 내용 자체는 항상 동일하다.
        #
        # [버그 수정 — 핵심] 예전엔 "이 콜백은 항상 TICK_MS(20ms)만큼 지난
        # 뒤에 불린다"고 가정했다. 근데 실제로는 시스템/렌더링 부하로 콜백이
        # 그보다 훨씬 늦게(느린 환경에서는 70~80ms씩) 불릴 수 있는데, 그래도
        # "20ms만 지났다"고 착각하고 그만큼만 시계를 전진시켰다. 그 결과
        # "1x=4초/분"으로 설정해놔도 실제로는 렌더링이 느린 만큼 체감 배속이
        # 밀려서 "15초나 걸린다"는 증상이 났다(제보 내용 그대로 재현되는
        # 원인). 이제 time.perf_counter()로 실제 경과시간을 재서 그 값을
        # 쓴다 — 프레임이 얼마나 자주 그려지든, 실제 흐른 시간만큼만
        # 정확하게 전진하므로 페이싱이 렌더링 성능과 무관해진다.
        now = time.perf_counter()
        real_elapsed = now - self._last_tick_perf
        self._last_tick_perf = now
        # 앱이 오래 멈췄다 돌아온 경우(창 최소화 등)처럼 극단적으로 큰
        # 값만 막는다. 너무 타이트하게(예: 0.5초) 잡으면 정작 이 함수가
        # 고치려는 "렌더링이 느려서 콜백이 늦게 불리는" 정상적인 보정
        # 상황까지 잘라버려서 배속이 다시 밀리는 원인이 된다.
        real_elapsed = max(0.0, min(2.0, real_elapsed))

        if self.playing:
            speed_mult = self.speed
            target_clock = self.clock + speed_mult * real_elapsed / self._SEC_PER_MIN
            if target_clock >= self._true_match_end:
                target_clock = self._true_match_end
                self.playing = False
                self.play_btn.setText("▶ 재생")
            self._apply_frame_at(target_clock)

        self.pitch.update()
        self.seek_bar.update()

    def _advance_one_tick(self, speed_scale):
        """[사전 계산 전용] __init__의 프레임 사전 계산 루프에서만 호출된다
        (항상 speed_scale=1.0, 고정 dt). 실제 재생/시크는 여기서 만들어진
        self._frames를 읽기만 하고 이 함수를 다시 호출하지 않으므로,
        실시간 타이밍이 시뮬레이션 내용에 영향을 줄 수 없다.
        """
        # [타이밍] TICK_MS/_FRAME_DT 비율로 "실제 1초 = 경기 1분"(1x 기준)이
        # 유지된다. 예전엔 이 증가폭이 0.06으로 하드코딩되어 있어서 클래스
        # 상수 _FRAME_DT(프레임 굽기/재생 양쪽에서 참조)와 별개로 관리됐다
        # — 한쪽만 바꾸면 재생 인덱스 계산과 어긋나는 취약점이 있어 통일함.
        self.clock += speed_scale * self._FRAME_DT

        # [후반 킥오프] 예전엔 전/후반 구분 없이 그냥 흐름이 이어져서
        # "축구에 킥오프 개념이 없다"는 느낌을 줬다. 이제 전반(추가시간
        # 포함)이 끝나는 순간 딱 한 번, 선수들을 자기 진영 홈 포지션으로
        # 되돌리고, 킥오프는 전반에 안 찼던 팀이 하도록 공격권을 넘기고
        # (실제 규칙과 동일), 홀더도 GK가 아니라 센터서클의 필드
        # 플레이어로 다시 세팅한다.
        fh_end = 45 + self.stoppage1
        if not self._halftime_reset_done and self.clock >= fh_end:
            self._halftime_reset_done = True
            self._last_restart_clock = self.clock
            for team in (self.home_players, self.away_players):
                for pl in team:
                    pl["x"], pl["y"] = pl["hx"], pl["hy"]
                    pl["vx"] = pl["vy"] = 0.0
            self.shape_push = {"home": 0.0, "away": 0.0}
            self._lagged_ball_x, self._lagged_ball_y = 0.5, 0.5
            self.possession = "away" if self._kickoff_side == "home" else "home"
            self.holder = self._kickoff_holder_index(self.possession)
            self.ball["x"], self.ball["y"] = 0.5, 0.5
            self._pass_flight = None
            self.pass_clock = 0.6
            self._assign_roles()
            # [신규] 골 배너처럼 "후반 시작"도 잠깐 화면에 띄운다.
            #   재개팀이 바뀌었다는 걸 명확히 보여줘야(배너 없이는 그냥
            #   자연스럽게 이어지는 것처럼 보여서 "전반이랑 킥오프팀이
            #   똑같다"는 오해가 생김) — 실제로는 이미 반대팀으로 정상
            #   교대되고 있었지만 화면상 티가 안 났던 것.
            self.banner_text = "⏱ 후반 시작"
            self.banner_color = "#88ccff"
            self.banner_alpha = 255

        if self.clock >= self.match_end:
            # [버그 수정 — 근본 원인] 예전엔 여기서 self.clock을 match_end로
            # 못박았다. 그러면 이 시점 이후(tail 프레임들) 전부 clock 값이
            # 완전히 똑같아져서, 재생 시 _apply_frame_at이 항상 같은
            # 인덱스만 계산해 tail 프레임(배너 소멸, 글라이드 완주)에 절대
            # 도달하지 못했다 — "파울 배너 뜨고 준비하다가 그 자리에서
            # 멈춘다"는 증상의 진짜 원인이었다. 이제 clock을 묶어두지 않고
            # 계속 흐르게 둬서 tail 프레임마다 서로 다른 clock 값을 갖게
            # 한다(재생 쪽 경계는 _true_match_end로 별도 관리 — 아래 참고).
            self.playing = False
            # [버그 수정] 예전엔 여기서 play_btn.setText까지 직접 건드렸는데,
            # 이 함수는 __init__의 프레임 사전 계산 단계에서도 호출되고
            # 그 시점엔 아직 UI가 없다(play_btn 자체가 존재하지 않음).
            # 실제 재생 중 종료 시점의 버튼 텍스트 갱신은 _tick()이 이미
            # 담당하므로 여기서는 상태 플래그만 정리하면 된다.
            # [승부차기] 결과만 배너로. 실제 키커 애니메이션은 안 만들고
            # 승/패와 스코어(예: 5-4)만 경기 종료 시점에 한 번 보여준다.
            if self.pso and not self._pso_shown:
                self._pso_shown = True
                pso_score = self.pso.get("score", "")
                if self.pso.get("won"):
                    self.banner_text = f"🎯 승부차기 승리!  ({pso_score})"
                    self.banner_color = "#44ff88"
                else:
                    self.banner_text = f"😤 승부차기 패배  ({pso_score})"
                    self.banner_color = "#ff6666"
                self.banner_alpha = 255

        # [신규] "info" 이벤트(파울/차단/드리블 등 — 배너만). 골/슛처럼
        # 빌드업→마무리 전체 씬이 필요 없는 짧은 플레이 순간들이라, 씬
        # 상태(_scene_kind/_pre_event)를 건드리지 않고 그 순간 배너만
        # 잠깐 띄운다. 예전엔 이런 텍스트가 전부 버려져서 통계(파울 10개
        # 등)엔 잡히는데 재생 화면엔 하나도 안 보였다.
        for e in self.timeline:
            if not e["done"] and e["kind"] == "info" and self.clock >= e["minute"]:
                is_stoppage = any(k in e["text"] for k in _STOPPAGE_KEYWORDS)
                is_filler_shot = bool(e.get("_filler_side")) and self._scene_kind is None
                if is_stoppage:
                    # [버그 수정 — 근본 원인] 예전엔 "누가 반칙을 했는지/누가
                    # 코너킥을 얻었는지"라는 실제 텍스트 정보("우리 팀"/
                    # "상대 팀")를 전혀 안 보고, 그냥 공이 지금 어느 쪽
                    # 절반에 있는지(x>=0.5?)만으로 재개팀을 추측했다. 이제
                    # 텍스트 자체("우리 팀"/"상대 팀")로 실제 재개팀을 직접
                    # 판정한다.
                    my_side = "home" if self.is_home else "away"
                    opp_side = "away" if self.is_home else "home"
                    if "파울" in e["text"]:
                        restart_side = opp_side if "우리 팀" in e["text"] else my_side
                        kind = "foul"
                    else:  # "코너킥"
                        restart_side = my_side if "우리 팀" in e["text"] else opp_side
                        kind = "corner"
                    # [재설계 — 데드볼 상태 머신] 이미 다른 데드볼이 진행
                    # 중이면 이 이벤트는 한 틱 미룬다(done을 안 찍고 다음
                    # 틱에 재시도) — 서로 다른 두 재개가 같은 순간에 겹쳐
                    # 공을 두 번 스냅해버리는 충돌을 상태 하나로 원천 차단.
                    if not self._enter_dead_ball(kind, side=restart_side):
                        continue
                elif is_filler_shot:
                    # [신규 — 죽어있던 통계 살리기, 구버전 세이브 전용
                    # 폴백] team_stats["shots"]를 맞추려고 끼워 넣은 필러
                    # 이벤트(possession_log가 있는 신규 세이브는 아래 plog
                    # 루프가 대신 처리하므로 이 경로를 안 탐). 유효슈팅/
                    # 빗나감 비율은 그 팀의 실제 shots_on/shots 비율을
                    # 따르고, 실제 결과 처리는 "shot" 데드볼 kind가
                    # 전담한다(골킥/코너로 이어지는 것까지 전부 상태
                    # 머신이 알아서 한다 — 예전처럼 결과 처리를 깜빡할
                    # 여지가 없다).
                    _fside = e["_filler_side"]
                    _fstat = (self.team_stats or {}).get(_fside, {})
                    _on_ratio = 0.35
                    if _fstat.get("shots"):
                        _on_ratio = max(0.15, min(0.75,
                            (_fstat.get("shots_on", 0) or 0) / max(1, _fstat["shots"])))
                    _foutcome = "save" if random.random() < _on_ratio else "shot_off"
                    if not self._enter_dead_ball("shot", side=_fside, outcome=_foutcome):
                        continue
                e["done"] = True
                self.banner_text = e["text"]
                self.banner_color = "#ffcc55"
                self.banner_alpha = 255

        # [신규 — possession_log 기반, 구조 변경의 핵심] 텍스트 없는 필러
        # 레코드(팀 전체 슈팅/코너/파울/그냥 빌드업)를 처리한다. 텍스트가
        # 있는 진짜 개인 이벤트는 이미 위 self.timeline 루프에서 처리되니
        # 여기서는 안 건드린다 — self._plog는 __init__에서 text가 None인
        # 레코드만 골라 담아뒀다. 예전 파울/코너킥 처리와 결정적으로 다른
        # 점: "team" 필드가 이미 정확한 주체를 담고 있어서, 텍스트("우리
        # 팀"/"상대 팀")를 다시 파싱해서 재개팀을 추측할 필요가 없다 —
        # 그 추측 로직 자체가 반복된 버그의 원인이었다.
        for _pr in self._plog:
            if _pr["done"] or self.clock < _pr["min"]:
                continue
            # [버그 수정 -- 근본 원인] 예전엔 여기서 바로 done=True를 찍은
            # 뒤 "씬 진행 중이면 그냥 continue"했다. 그러면 그 필러는
            # done 처리만 되고 실제로는 아무 것도 안 한 채 영원히
            # 사라졌다 -- 특히 outcome=="goal"인 필러가 하필 실제 개인
            # 이벤트 씬(빌드업~마무리, 몇 초간 지속)과 겹치면 그 골이
            # 통째로 유실돼서 최종 스코어가 실제보다 낮게 끝났다(실측
            # 재현됨). done 표시는 "실제로 처리했을 때만" 찍고, 씬이
            # 끝날 때까지는 매 틱 다시 시도하게 한다.
            if self._scene_kind is not None:
                continue
            _outcome = _pr["outcome"]
            _team_name = self.home_name if _pr["team"] == "home" else self.away_name

            if _outcome == "buildup":
                # [신규] 배너/정지 없이 "이 구간엔 이 팀이 이 구역에서
                # 공을 갖고 있었다"는 부드러운 힌트만 준다 -- 공/possession을
                # 직접 스냅하는 게 아니라 데드볼과 절대 충돌하지 않으므로
                # 상태 머신을 거치지 않고 항상 즉시 처리한다.
                _pr["done"] = True
                # [버그 방지] possession만 바꾸고 holder를 안 바꾸면,
                # 이전 팀에서 쓰던 holder 인덱스가 새 팀 배열의 엉뚱한
                # 선수를 가리키게 된다. 팀이 실제로 바뀔 때만 다시 잡는다.
                if _pr["team"] != self.possession:
                    self.possession = _pr["team"]
                    _bteam = self.home_players if _pr["team"] == "home" else self.away_players
                    self.holder = min(
                        (i for i, pl in enumerate(_bteam) if pl["pos"] != "GK"),
                        key=lambda i: abs(_bteam[i]["x"] - self.ball["x"])
                                     + abs(_bteam[i]["y"] - self.ball["y"]))
                    self._assign_roles()
                _zone_push = {"def": 0.2, "mid": 0.5, "att": 0.8}.get(_pr["zone"], 0.5)
                if _pr["team"] == "home":
                    self.shape_push["home"] = _zone_push
                    self.shape_push["away"] = 1.0 - _zone_push
                else:
                    self.shape_push["away"] = _zone_push
                    self.shape_push["home"] = 1.0 - _zone_push
                # [신규 — 진짜 레인 반영] 전술 엔진이 만든 로그는 "이 분에
                # 실제로 어느 레인(좌/중/우)에서 우세했는가"까지 담고
                # 있다. 이 힌트를 다음 패스 타겟 선정(_do_pass_or_turnover)
                # 이 참고할 목표 y로 저장해둔다 — 포메이션 매치업 계산이
                # 실제로 화면에서 "그 쪽으로 공이 몰린다"는 그림으로
                # 이어지게 하는 마지막 연결고리. 없는 로그(구버전 필러)는
                # lane이 없으므로 그냥 건드리지 않는다(중립 유지).
                _lane = _pr.get("lane")
                if _lane:
                    self._target_lane_y = {"L": 0.18, "C": 0.5, "R": 0.82}.get(_lane, 0.5)
                continue

            # [버그 수정 -- 근본 원인] 파울은 지금까지 "team_stats에 맞춰
            # 예약된 시각이 되면 무조건 발동"이었다 -- 실제로 그 시각에
            # 반칙팀 선수가 홀더 근처에 있는지는 전혀 안 봤다. 그래서
            # 실측 디버그 캡처에서 반칙팀 최근접 선수가 홀더에서 정규화
            # 거리 0.08(피치폭 기준 약 57px)이나 떨어진 채로 파울 배너가
            # 뜨는 게 그대로 재현됐다("아무도 안 닿았는데 파울이 터진다"는
            # 지적의 정확한 원인). 반칙팀 중 홀더와 충분히 가까운 선수가
            # 실제로 있을 때만 발동시키고, 아직 아무도 안 붙었으면 몇 틱
            # 더 기다린다 -- 단, team_stats와의 정합성(파울 개수)이 깨지면
            # 안 되므로 예정 시각에서 1.2분 넘게 지나도 아무도 안 붙으면
            # (극단적으로 안 붙는 경우 대비) 그냥 발동시켜 유실을 막는다.
            if _outcome == "foul":
                _foul_team = self.home_players if _pr["team"] == "home" else self.away_players
                _near = min((abs(pl["x"] - self.ball["x"]) + abs(pl["y"] - self.ball["y"])
                             for pl in _foul_team if pl["pos"] != "GK"), default=99.0)
                if _near > 0.12 and (self.clock - _pr["min"]) < 1.2:
                    continue

            # [재설계 -- 데드볼 상태 머신] foul/corner/goal/save/shot_off/
            # shot_blocked 전부 이 하나의 진입점을 거친다. 이미 다른
            # 데드볼이 진행 중이면 False가 돌아오고, done을 안 찍은 채
            # continue해서 다음 틱에 재시도한다 -- 두 재개가 겹쳐서
            # 공을 두 번 스냅하는 충돌이 상태 하나로 원천 차단된다.
            if _outcome == "foul":
                ok = self._enter_dead_ball("foul", side=("away" if _pr["team"] == "home" else "home"))
            elif _outcome == "corner":
                ok = self._enter_dead_ball("corner", side=_pr["team"])
            else:  # goal / save / shot_off / shot_blocked
                ok = self._enter_dead_ball("shot", side=_pr["team"], outcome=_outcome)
            if not ok:
                continue
            _pr["done"] = True
            if _outcome == "foul":
                self.banner_text = f"\U0001f7e8 {_team_name} \ud30c\uc6b8"
                self.banner_color = "#ffcc55"
                self.banner_alpha = 255
            # corner/shot은 배너를 즉시 안 띄운다 -- corner는 코너 근처로
            # 굴러나가는 전조가 끝나야, shot(goal/save)은 슛 비행이
            # 끝나야 실제 결과가 나오므로, 그 시점에 상태 머신
            # (_db_step_corner/_db_step_shot)이 알아서 배너를 띄운다.

        # 다가오는 실제 이벤트를 미리 감지 → 그 팀 쪽으로 점유를 유도해서
        # "골 넣기 전에 상대 진영으로 밀고 들어가는" 빌드업 구간을 만든다.
        if self._scene_kind is None and self._pre_event is None:
            for e in self.timeline:
                if (not e["done"] and e["kind"] != "info"
                        and self.clock >= e["minute"] - _BUILDUP_LEAD):
                    self._pre_event = e
                    side = self._event_side(e)
                    self.possession = side
                    break

        # 실제 이벤트 시각 도달 → 마무리(슛) 장면 시작
        if self._scene_kind is None and self._pre_event is not None:
            e = self._pre_event
            if self.clock >= e["minute"]:
                self._start_scene(e)
                e["done"] = True
                self._pre_event = None

        if self._scene_kind:
            self._advance_scene(speed_scale)
            # [버그 수정 — 근본 원인] 씬(골/슛/코너킥)이 진행되는 동안 이
            # 호출이 없어서, 씬에 직접 포함 안 된 나머지 선수 대부분이
            # 씬 시작 직전 위치 그대로 얼어붙어 있었다. 지금까지 나온 여러
            # 문제(크라우드 안 뭉침, 파울 대형 안 잡힘 등)가 사실 이
            # 한 가지 구조적 이음매에서 갈라져 나온 증상들이었다. 씬이
            # 직접 통제하는 선수는 _update_player_positions 안에서
            # 알아서 건너뛰므로, 매 틱 같이 호출해도 씬 좌표를 안 건드리고
            # 나머지만 계속 자연스럽게 움직인다.
            self._update_player_positions(speed_scale)
        elif self._db is not None:
            # [재설계 핵심] 예전엔 여기가 글라이드/프리즈/코너대기/그 외
            # 4갈래로 흩어져 있었고("글라이드 중엔 possession 로직 멈춤",
            # "코너 대기 중엔 또 다르게 멈춤" 등 조각마다 규칙이 달랐다),
            # 그 경계에서 계속 새 버그가 나왔다. 이제 "데드볼 진행 중이냐
            # 아니냐" 단 하나의 분기로 통일한다 — 데드볼의 모든 세부 단계
            # (스로인/골킥/파울/코너의 각 phase)는 _advance_dead_ball
            # 안에서 하나의 상태 머신으로 처리된다.
            self._advance_dead_ball(speed_scale)
        else:
            self._update_possession(speed_scale)

        if self.banner_alpha > 0 and (self._scene_kind is None):
            # [버그 수정] 예전엔 페이드 속도가 고정값(-8/틱)이라, 재생 배속을
            # 올리면(이벤트 간격은 배속에 비례해 빨리 다가오는데) 배너는 그대로
            # 느리게 사라져서 다음 장면 배너와 겹쳐 보였다(사용자가 말한 "충돌").
            # 배속에 비례해서 같이 빨리 사라지도록 맞췄다.
            # [버그 수정] speed(float)를 곱하면서 banner_alpha가 float이 돼서
            # PyQt6의 setAlpha(int 전용)에서 TypeError가 났었다. int로 고정.
            # [버그 수정] 예전엔 감쇠가 -8/틱이라 완전히 사라지기까지 약
            # 1.9분(경기 시계 기준, 실제 재생으로는 약 7~8초)이 걸렸다.
            # 반면 코너킥 크라우드는 0.6분(약 2.4초) 만에 정상 대형으로
            # 흩어지므로, 그 사이 5초 넘게 "이미 정상 플레이가 재개됐는데
            # 배너만 화면에 계속 떠 있는" 구간이 생겼다 — 뒤이어 다른
            # 코너킥이 짧은 간격으로 또 발생하면 배너가 계속 갱신되며
            # 안 사라지는 것처럼 보이기도 했다. 감쇠를 -18로 올려서
            # 크라우드가 흩어지는 속도와 비슷하게(약 0.85분≈3.4초) 맞췄다.
            self.banner_alpha = int(max(0, self.banner_alpha - 18 * max(1.0, speed_scale)))

        # 패스 잔상(궤적) 서서히 페이드아웃
        if self.ball_trail:
            for pt in self.ball_trail:
                pt[2] = max(0, pt[2] - 22)
            self.ball_trail = [pt for pt in self.ball_trail if pt[2] > 0]

    def _display_halves(self, elapsed):
        """[전/후반 분리 표시] 글로벌 분(1~90+추가시간) 하나로 죽 이어서
        보여주는 대신, "전반 X' / 후반 Y'"로 나눠서 동시에 보여준다.

        전반 진행 중엔 전반 카운터만 0→45→45+추가시간으로 올라가고 후반은
        "0'"로 대기. 후반에 들어가면 전반은 최종값(예: 45+3')에 고정되고,
        후반이 '자기 킥오프 시점부터' 독립적으로 0→45→45+추가시간을 새로
        센다(전반 46분째... 식으로 이어지는 게 아니라 후반도 전반과 똑같은
        패턴으로 처음부터 다시 카운트) — 실제 스코어보드에서 절반씩 나눠
        보여주는 방식과 같다.
        """
        e = min(elapsed, self.match_end)
        fh_end = 45 + self.stoppage1  # 전반 종료(추가시간 포함) 시점

        if e <= fh_end:
            if e <= 45:
                fh_str = f"{int(e)}'"
            else:
                over = int(round(e - 45))
                fh_str = f"45+{over}/{self.stoppage1}'" if over > 0 else "45'"
            return fh_str, "0'"

        fh_str = f"45+{self.stoppage1}'" if self.stoppage1 > 0 else "45'"
        since_second = e - fh_end  # 후반 자체 킥오프 기준 독립 카운트
        if since_second <= 45:
            sh_str = f"{int(since_second)}'"
        else:
            over = int(round(since_second - 45))
            sh_str = f"45+{over}/{self.stoppage2}'" if over > 0 else "45'"
        return fh_str, sh_str

    def _seek_to(self, new_clock):
        """[재생바] 원하는 시점으로 즉시 이동. 전체 경기가 이미 __init__에서
        고정 시드로 단 한 번에 미리 계산되어 self._frames에 저장되어
        있으므로, 그 시점에 해당하는 프레임을 찾아 그대로 보여주기만 하면
        된다. 재생/시크/몇 번을 다시 열어보든 전부 같은 소스(같은 프레임
        배열)를 읽으므로 100% 같은 화면이 나온다. (예전엔 이 함수가 매번
        처음부터 다시 빨리감기 시뮬레이션을 돌리는 방식이라 무거웠고,
        실시간 재생과 미묘하게 어긋나는 경우도 있었다 — 이제는 단순 배열
        인덱싱이라 더 빠르고 항상 정확히 일치한다.)"""
        target = max(0.0, min(self.match_end, new_clock))
        idx = int(round(target / self._FRAME_DT))
        self._apply_frame(idx)
        self.pitch.update()
        self.seek_bar.update()

    def _event_side(self, e):
        my_side = "home" if self.is_home else "away"
        opp_side = "away" if self.is_home else "home"
        return my_side if e["kind"] in ("goal_for", "miss_for") else opp_side

    # ── 점유·패스 엔진 (장면이 없을 때 평상시 흐름) ──────────
    def _update_player_positions(self, speed_scale=1.0):
        """[신규] 22명 전원의 위치 갱신만 담당한다(공/패스/턴오버 로직은
        제외 — 그건 _update_possession이 이어서 처리). 이렇게 분리한 이유:
        예전엔 씬(골/슛/코너킥)이 진행되는 동안 이 위치 갱신 자체가 통째로
        멈춰서, 씬에 직접 포함 안 된 나머지 14~19명이 씬 시작 직전 위치
        그대로 얼어붙어 있었다. 지금까지 나온 문제(크라우드 안 뭉침, 파울
        때 대형 안 잡힘, 크로서 위치 어긋남 등)가 사실 전부 이 한 가지
        구조적 이음매에서 갈라져 나온 증상들이었다 — 씬과 오픈플레이가
        서로의 존재를 모른 채 따로 도는 두 시스템이었던 것.
        이제 씬 진행 중에도(_advance_scene과 별개로) 이 함수를 계속 호출해
        나머지 선수들이 계속 움직이게 한다. 씬이 직접 통제하는 선수
        (_scene_atk_idx/_scene_def_idx/_scene_crowd)는 건드리지 않고
        건너뛴다 — 안 그러면 씬의 정교한 좌표를 이 일반 공식이 다시
        덮어써버린다."""
        dt = 0.12 * max(1.0, speed_scale)

        # [재설계 — 라인 하이트를 공의 절대 위치에 연동] 예전엔 "우리 팀이
        # 공을 갖고 있냐 아니냐"라는 이진값 하나로만 push를 정했다(공격
        # 1.0, 수비 0.0 고정). 그래서 수비할 때는 공이 하프라인 부근이든
        # 우리 골문 코앞이든 상관없이 항상 최대치로 물러나 골키퍼 옆에
        # 뭉쳤고("무지성으로 키퍼 주위에"), 반대로 상대가 자기 진영 안에서
        # 공을 돌리고 있을 때도 우리는 그냥 낮게 웅크린 채 구경만 했다
        # ("상대 진영에 가까우면 멀리서 구경"). 실제 축구는 볼이 내 골문에서
        # 얼마나 먼가에 따라 수비 라인 높이가 연속적으로 바뀐다 — 공이
        # 멀리 있으면(상대 진영) 라인을 높이 올려 압박하고, 가까이 오면
        # (내 진영) 확실히 내려앉아 박스를 지킨다. 공을 갖고 있을 땐 여전히
        # 최대 전진(1.0)이고, 안 갖고 있을 때만 이 연속값을 쓴다.
        for side in ("home", "away"):
            if side == self.possession:
                target = 1.0
            else:
                own_goal_x = 0.0 if side == "home" else 1.0
                ball_dist_from_own_goal = abs(self.ball["x"] - own_goal_x)
                # 0(내 골문 코앞) → 라인 낮게(0.12), 1(상대 골문 코앞)
                # → 라인 높게(0.68). 완전한 로우블록도, 완전한 하이프레스도
                # 아닌 중간 지대를 부드럽게 이어준다.
                target = 0.12 + 0.56 * ball_dist_from_own_goal
            # [현실성 보정] 예전 계수(0.04)는 지수감쇠 시간상수가 약 25틱
            # 이라, 소유권이 그보다 짧게(패스 주기 0.5~1.2초마다 갱신)
            # 바뀌는 실제 경기 흐름에서는 이 목표치 근처에도 못 가보고
            # 계속 소유팀이 바뀌었다 — 위에서 전진폭 상한을 올려도 정작
            # 그 상한까지 "도달"할 시간 자체가 없었던 것. 0.09로 올려서
            # 시간상수를 약 11틱으로 줄이면, 몇 초만 같은 팀이 소유해도
            # 실제로 그 팀의 전진폭 상한 근처까지 체감상 도달한다.
            self.shape_push[side] += (target - self.shape_push[side]) * 0.09

        # [신규] 반응 지연용 "인식된 공 위치"를 실제 공보다 훨씬 느리게
        # 따라가게 한다(0.03 vs shape_push의 0.04보다도 느림 — 대략 실시간
        # 대비 1초 안팎 뒤처짐). 아래 루프에서 공과 먼 선수일수록 이 값을
        # 더 많이 섞어서, 반대편 선수까지 공 움직임에 즉각 동기화되어
        # "다같이 몰려다니는" 부자연스러움을 줄인다.
        self._lagged_ball_x += (self.ball["x"] - self._lagged_ball_x) * 0.03
        self._lagged_ball_y += (self.ball["y"] - self._lagged_ball_y) * 0.03

        # [신규 - 전환 부스트 감쇠] 턴오버 직후 잠깐 부여된 스피드 부스트를
        # 시간에 따라 선형으로 줄여나간다. 0이 되면 평소 속도로 완전히
        # 복귀한다.
        if self._transition_timer > 0:
            self._transition_timer = max(0.0, self._transition_timer - dt)
        transition_mult = 1.0 + self._TRANSITION_BOOST * (
            self._transition_timer / self._TRANSITION_DURATION)

        holder_side = self.possession
        holder_team = self.home_players if holder_side == "home" else self.away_players
        holder = holder_team[self.holder]
        support_idx = self._support_idx
        presser_idx = self._presser_idx
        cover_idx = self._cover_idx

        # [신규] 씬이 진행 중이면, 씬이 직접 통제하는 선수는 이 일반
        # 공식으로 건드리지 않고 건너뛴다.
        scene_active = bool(self._scene_kind)
        if scene_active:
            # [버그 수정 — 근본 원인] _scene_crowd가 예전엔 팀 구분 없이
            # 순수 인덱스(0~10)만 키로 썼다. 그런데 양 팀 다 인덱스가
            # 0~10 범위라서, 공격측 크라우드를 다 채운 "다음에" 수비측
            # 크라우드를 채우면 같은 인덱스에서 값이 덮어써져 공격측
            # 항목 대부분이 통째로 사라졌다(PK에서 키커 빼고 나머지
            # 공격팀 선수 9명 중 1명만 배치되고 나머지는 그냥 평소
            # 오픈플레이 로직에 방치된 원인 — "왜 2명 정도만 공 주변에서
            # 반응하고 나머지는 그냥 직진/후진만 하냐"는 지적이 여기서도
            # 나온 것). 키를 (side, i) 튜플로 바꿔서 충돌을 없앴다.
            atk_skip = set(self._scene_atk_idx) | {
                i for (s, i) in self._scene_crowd.keys() if s == "atk"}
            def_skip = set(self._scene_def_idx) | {
                i for (s, i) in self._scene_crowd.keys() if s == "def"}

        # [버그 수정 — 근본 원인] "상대 팀 코너킥" 같은 info 이벤트로
        # _snap_corner_crowd()가 스냅해둔 키커/크라우드도 위 씬 스킵과
        # 똑같은 이유로 보호해야 한다. 이게 없으면 스냅한 바로 다음 틱에
        # 이 함수가 그 선수들 목표를 다시 hx/hy 기준으로 계산해버려서,
        # 박스로 몰아넣은 대형이 글라이드 구간 안에서 곧장 원래 자리로
        # 되끌려가 버렸다("코너 근처에 아무도 없다"는 지적의 원인). 글라이드
        # 구간이 끝나면(더 이상 보호할 필요 없는 시점) 락을 비워서 평소
        # 흐름으로 자연스럽게 복귀시킨다.
        corner_lock_active = self.clock < self._info_glide_until
        if not corner_lock_active and (self._corner_lock_home or self._corner_lock_away):
            self._corner_lock_home = set()
            self._corner_lock_away = set()

        # [신규 — 필러 슛 GK 다이빙] "필러 슛"(_db_start_shot, 이름 없는
        # 팀 통계용 슈팅 — 실제 경기의 대다수 슈팅이 여기 해당)이 진행
        # 중일 때, 막는 팀 GK를 이 일반 공식(전/후퇴 스위퍼 라인, y는
        # 0.05 가중치로만 살짝 따라감)에 맡기면 슛이 골이든 선방이든
        # 골문 근처에서 사실상 안 움직였다("막든 안막든 모션 자체가
        # 없다"는 지적 그대로). _db_start_shot이 결과(골/선방/노골)에
        # 맞춰 미리 계산해둔 다이빙 목표(self._db["gk_target"])로
        # _db_step_shot이 직접 보간할 동안, 이 GK 한 명만 일반 공식에서
        # 빼둔다.
        shot_gk_lock = None
        if (self._db is not None and self._db.get("kind") == "shot"
                and self._db.get("phase") == "flight"):
            shot_gk_lock = (self._db["gk_side"], self._db["gk_idx"])

        for side, team in (("home", self.home_players), ("away", self.away_players)):
            push = self.shape_push[side]
            sign = 1 if side == "home" else -1
            is_holder_side = (side == holder_side)
            formation = self.home_formation if side == "home" else self.away_formation
            # [신규 — 깊이(depth) 컴팩트니스] 예전엔 포지션별 전진/후퇴폭
            # (_tactical_dx)이 고정 비율표라서, ST는 항상 크게 전진하고
            # CB는 항상 크게 후퇴하는 식으로 "각자 따로" 움직였다 — 팀이
            # 수비할 때조차 라인 간 간격(공격진-수비진 사이 거리)이 줄지
            # 않고 늘어진 채로 유지됐다("폭"은 지난번에 고쳤지만 "깊이"는
            # 그대로였음). 실제 압박 축구는 수비 시 라인 간 간격 자체를
            # 좁혀서 한 덩어리 블록처럼 움직인다. 이 팀의 실제 라인업
            # 기준 평균 전진/후퇴값을 먼저 구해두고, 아래에서 각 선수를
            # 그 평균 쪽으로 blend한다 — 튀어나온 공격수는 덜 튀어나오고,
            # 처진 수비수는 덜 처지게 만들어서 라인 간격이 줄어든다.
            _outfield_advs = [
                (_tactical_dx(formation, _p["pos"])[0] * push
                 - _tactical_dx(formation, _p["pos"])[1] * (1 - push)) * sign
                for _p in team if _p["pos"] != "GK"
            ]
            _team_avg_adv = (sum(_outfield_advs) / len(_outfield_advs)) if _outfield_advs else 0.0
            # [신규 — 22명 역할 확장 1단계: 오프사이드 트랩 라인 동조]
            # 지금까진 수비 시 "팀 전체" 평균(공격수/미드필더까지 섞임)
            # 쪽으로 30%만 당겨서, 정작 한 줄로 움직여야 할 백4(CB/LB/RB/
            # LWB/RWB)끼리도 서로 다른 타이밍에 오르내려 들쭉날쭉해
            # 보였다. 백4만 따로 모아 "백라인 전용" 평균을 구해두고,
            # 아래에서 백4에게는 이 값 쪽으로 훨씬 강하게(60%) 당겨서
            # 실제 오프사이드 트랩처럼 다 같이 한 줄로 오르내리게 한다.
            _backline_advs = [
                (_tactical_dx(formation, _p["pos"])[0] * push
                 - _tactical_dx(formation, _p["pos"])[1] * (1 - push)) * sign
                for _p in team if _p["pos"] in _BACKLINE_ROLES
            ]
            _backline_avg_adv = (sum(_backline_advs) / len(_backline_advs)
                                  if _backline_advs else _team_avg_adv)
            # [신규] 이 팀의 GK 인덱스를 루프 진입 전에 한 번만 찾아둔다.
            # 포메이션상 거의 항상 인덱스 0이지만, 확정적으로 보장되진
            # 않으므로(예: 커스텀 라인업) 매 프레임 22번 안전하게 탐색
            # 해두고 아래 최소 간격 클램프에서 재사용한다.
            _gk_idx_this_side = next((k for k, _p in enumerate(team) if _p["pos"] == "GK"), 0)
            # [신규 — 서포트런 긴급도] 지금까진 서포트런 2명이 홀더가
            # 압박을 받든 안 받든 항상 똑같은 정적인 레인 위치를 유지했다.
            # 실제 축구는 홀더가 압박당해 급하면 동료들이 더 적극적으로
            # 다가붙어 짧고 안전한 출구를 만들어준다. 이 사이드가 홀더
            # 쪽이면, 상대 프레서가 홀더한테 얼마나 바짝 붙었는지를 미리
            # 구해서 0(여유)~1(급박) 긴급도로 환산해둔다.
            _support_urgency = 0.0
            if side == holder_side and self._presser_idx is not None:
                _opp_for_holder = self.away_players if side == "home" else self.home_players
                if self._presser_idx < len(_opp_for_holder):
                    _pp = _opp_for_holder[self._presser_idx]
                    _pdist = abs(_pp["x"] - holder["x"]) + abs(_pp["y"] - holder["y"])
                    _support_urgency = max(0.0, min(1.0, (0.22 - _pdist) / 0.22))
            if scene_active:
                is_scene_atk_side = (side == self._scene_side)
                skip_set = atk_skip if is_scene_atk_side else def_skip
            corner_lock = (self._corner_lock_home if side == "home"
                           else self._corner_lock_away) if corner_lock_active else ()
            for i, pl in enumerate(team):
                if scene_active and i in skip_set:
                    continue
                if corner_lock and i in corner_lock:
                    continue
                if shot_gk_lock is not None and shot_gk_lock == (side, i):
                    continue
                # [개선] 포메이션별 전진/후퇴 폭 — 공격 시(push→1) hx보다
                # fwd만큼 더 전진하고, 수비 시(push→0) back만큼 물러난다.
                # 예전엔 후퇴가 아예 없어서 수비할 때도 항상 중립 위치에만
                # 머물렀다(로우블록으로 내려앉는 그림이 없었음).
                fwd, back = _tactical_dx(formation, pl["pos"])
                adv = (fwd * push - back * (1 - push)) * sign
                if not is_holder_side and pl["pos"] != "GK":
                    # [신규 — 깊이 컴팩트니스 + 오프사이드 트랩] 수비 중일
                    # 때만 당긴다. 백4는 백라인 전용 평균 쪽으로 강하게
                    # (60%) 당겨 한 줄로 동조시키고, 그 외(CDM/CM 등)는
                    # 기존처럼 팀 전체 평균 쪽으로 30%만 당긴다(GK는 별도
                    # 스위퍼 로직이 있으니 여기서는 제외).
                    if pl["pos"] in _BACKLINE_ROLES:
                        adv = adv * 0.40 + _backline_avg_adv * 0.60
                    else:
                        # [현실성 보정] 후퇴폭 자체를 올린 것과 별개로,
                        # 팀 평균 쪽으로 당기는 비중도 30%→45%로 올려서
                        # 중원이 백라인과 따로 노는 정도를 한 번 더
                        # 줄인다(이중 안전장치 — 포지션별 표 값만으로는
                        # 포메이션별 편차가 있어 완전히 못 잡는 경우 대비).
                        adv = adv * 0.55 + _team_avg_adv * 0.45
                target_x = pl["hx"] + adv
                # [개선] "인식된 공 위치" — 홀더 근처처럼 플레이에 실제로
                # 관여한 선수는 실시간 공 좌표를, 멀리 떨어진 선수는 위에서
                # 갱신한 지연값(_lagged_ball_*)을 섞어서 쓴다. 예전엔 22명
                # 전원이 똑같이 실시간 공 좌표에 반응해서 "다같이 우르르
                # 몰려다니는" 부자연스러움이 있었다(외부 검토에서도 지적된
                # 부분) — 이제 공과 먼 선수일수록 반응이 굼떠서 대형이
                # 자연스럽게 벌어진다.
                _hy_dist_ball = math.hypot(pl["hx"] - self.ball["x"], pl["hy"] - self.ball["y"])
                # [개인화] 같은 거리라도 반응성(reaction)이 높은 선수는 공을
                # 더 빨리 "알아채고", 둔한 선수는 좀 더 늦게 반응한다 —
                # 팀 전체가 완전히 같은 타이밍으로 반응하던 것을 깨는 핵심.
                _live_w = max(0.0, min(1.0,
                    (1.0 - _hy_dist_ball * 1.4) * pl.get("reaction", 1.0)))
                perc_bx = self.ball["x"] * _live_w + self._lagged_ball_x * (1 - _live_w)
                perc_by = self.ball["y"] * _live_w + self._lagged_ball_y * (1 - _live_w)
                # [개선] 공에서 먼 선수들도 라인 전체가 공의 좌우 위치에
                # 반응하도록 한다. 예전엔 y만 아주 약하게(0.03~0.10)
                # 따라가고 x는 전혀 안 움직여서, 공을 갖고 있지 않은
                # 대다수 선수는 사실상 안 움직이는 것처럼 보였다(관찰된
                # 문제 그대로). y축으로 공과 먼 선수일수록 살짝 더
                # 물러나게(커버 형태) 해서 라인이 완전히 평평하지 않고
                # 공 반대쪽이 자연스럽게 처지는 대각선을 만든다.
                y_dist = abs(pl["hy"] - perc_by)
                target_x -= min(0.05, y_dist * 0.12) * sign
                if is_holder_side and pl["pos"] in _WIDE_ROLES:
                    # [재설계 — 폭 "유지"만으론 부족했다] 예전엔 target_y만
                    # hy 쪽으로 살짝 당기는 정도라, "터치라인 폭을 안
                    # 잃는다" 정도지 실제로 "달려 들어간다"는 느낌이 전혀
                    # 없었다. 게다가 hy 자체가 이미 진짜 터치라인(0.06/
                    # 0.94)보다 한참 안쪽(0.14/0.86)이라, 거기 머무는 것만
                    # 으론 절대 터치라인까지 안 붙는다 — "윙어가 사이드로
                    # 안 달린다"는 지적의 실제 원인이 X축(전진)도 Y축
                    # (터치라인 밀착)도 둘 다 없었다는 것.
                    #
                    # [버그 수정 — 근본 원인] 그런데 위 수정은 자기 원래
                    # 진영(hy) 기준으로만 터치라인을 잡아서, 공이 반대쪽
                    # 플랭크로 넘어가도 전혀 반응하지 않고 그냥 자기 쪽
                    # 라인에서 push(공격/수비 전환)에 따라서만 오르내렸다
                    # — "윙이 무지성으로 사이드에서 왔다갔다한다"는 지적의
                    # 정확한 원인. 실제 축구에서는 공이 자기 플랭크에 있을
                    # 때만 터치라인까지 벌려 오버래핑하고, 반대쪽(약측)
                    # 플랭크에 있을 때는 안으로 좁혀 들어와 박스 안 옵션이
                    # 되거나 스위치 패스를 받을 준비를 한다. 지연된 인식
                    # 좌표(perc_by)로 볼이 실제 어느 플랭크에 있는지 먼저
                    # 판정한다.
                    _own_upper = pl["hy"] < 0.5
                    _ball_upper = perc_by < 0.5
                    _ball_side_match = (_own_upper == _ball_upper)
                    if _ball_side_match:
                        _touch_y = 0.06 if _own_upper else 0.94
                        target_y = pl["hy"] + (_touch_y - pl["hy"]) * min(1.0, push * 1.3)
                        target_x = pl["hx"] + adv * max(push, 0.85) * 1.3
                    else:
                        # [신규 — 약측 인버팅/투크인] 반대쪽 플랭크에 볼이
                        # 있을 때는 터치라인에 붙어있을 이유가 없다 — 중앙
                        # 쪽으로 좁혀 들어와 박스 침투/스위치 수신 옵션이
                        # 되게 한다. 전진은 하되 오버래핑만큼 적극적이진
                        # 않다.
                        target_y = pl["hy"] + (0.5 - pl["hy"]) * 0.38
                        target_x = pl["hx"] + adv * max(push, 0.55) * 0.95
                    # 오프사이드 라인(상대 최종 수비 라인) 클램프도 같이
                    # 걸어서 터무니없이 튀어나가진 않게 한다.
                    _opp_team_now = self.away_players if side == "home" else self.home_players
                    _opp_def_xs = [p["x"] for p in _opp_team_now if p["pos"] != "GK"]
                    if _opp_def_xs:
                        if sign > 0:
                            target_x = min(target_x, max(_opp_def_xs) + 0.05)
                        else:
                            target_x = max(target_x, min(_opp_def_xs) - 0.05)
                elif is_holder_side:
                    # [버그 수정 — 방향이 거꾸로였음] 예전엔 "push>0.3(=공격
                    # 중)일 때 강하게(0.16), 수비 중일 때 약하게(0.07)"
                    # 볼 쪽으로 당겼다. 이건 실제 축구 전술과 정반대다 —
                    # 공격 팀은 폭을 넓게 유지해서 상대 수비를 벌려놔야
                    # 하고, 오히려 수비 팀이 볼 쪽으로 블록을 조여야
                    # (컴팩트니스) 한다. 그래서 정작 압축이 필요한 수비
                    # 라인이 제일 느슨하게 반응하고 있었다 — "22명 중 몇
                    # 명만 반응하고 나머지는 얼어있다"는 인상의 핵심 원인
                    # 중 하나. 공격 중에는 약하게만 당겨서 폭을 유지한다.
                    _raw_ty = pl["hy"] + (perc_by - pl["hy"]) * 0.08
                    _prev_ty = pl.get("_committed_ty")
                    if _prev_ty is None or abs(_raw_ty - _prev_ty) > 0.035:
                        pl["_committed_ty"] = _raw_ty
                        _prev_ty = _raw_ty
                    target_y = _prev_ty
                else:
                    # [신규 — 블록 컴팩트니스] 수비 중인 나머지 8~9명
                    # 전원이 이제 볼 쪽으로 뚜렷하게 압축된다. push(=우리
                    # 팀의 전개도, 수비 중이라 낮음)가 낮을수록(=로우블록,
                    # 우리 진영 깊이 웅크린 상황)일수록 오히려 더 바짝
                    # 조여야 실제 "로우블록으로 좁게 밀집"한 그림이 나온다.
                    #
                    # [신규 — "흐물흐물" 방지] 예전엔 매 프레임 perc_by가
                    # 아주 살짝만 바뀌어도 target_y가 그만큼 따라 바뀌어서,
                    # 실제 축구처럼 "버티다가 결정적으로 움직이는" 게 아니라
                    # 끝없이 미세하게 잔진동하는 것처럼 보였다("수비수들이
                    # 흐물흐물 움직인다"는 지적의 실제 원인). 직전에 확정한
                    # 목표(_committed_ty)에서 일정 폭(0.035) 이상 벗어났을
                    # 때만 목표를 갱신하고, 그 전까진 같은 목표를 계속
                    # 유지한다 — 그 결과 "잠깐 버티다 한 번에 자리를
                    # 잡는" 자연스러운 패턴이 된다.
                    _compact = 0.22 + (1.0 - push) * 0.10
                    _raw_ty = pl["hy"] + (perc_by - pl["hy"]) * _compact
                    _prev_ty = pl.get("_committed_ty")
                    if _prev_ty is None or abs(_raw_ty - _prev_ty) > 0.035:
                        pl["_committed_ty"] = _raw_ty
                        _prev_ty = _raw_ty
                    target_y = _prev_ty

                if is_holder_side and i == self.holder:
                    # 홀더는 공과 무관하게 대형 전진 목표를 향해 일반 선수처럼
                    # 이동한다(=드리블 전개). 공은 아래에서 홀더 발밑에 얹혀서
                    # 따라가기만 하므로, 홀더↔공이 서로를 쫓는 순환참조가 없다.
                    target_x = pl["hx"] + adv * max(push, 1.0) * 1.15
                    target_y = pl["hy"]
                elif is_holder_side and i in support_idx:
                    # [버그 수정] 예전엔 target_y를 홀더 위치(+오프셋)로
                    # 완전히 덮어써서, 패스가 다른 레인의 동료에게 넘어갈
                    # 때마다 지원런 선수가 자기 원래 채널(hy)을 벗어나 그
                    # 홀더 위치로 순간 방향을 크게 트는 것처럼 보였다(몇 분
                    # 사이 박스 반대편까지 지그재그로 오가는 부자연스러운
                    # 움직임 — 사용자가 캡처한 화면이 이 경우). 실제 지원런은
                    # 자기 레인을 유지하면서 공 쪽으로 살짝만 쏠리는 정도이므로,
                    # 자기 원래 y(hy)를 더 많이 반영해서 블렌딩한다.
                    lane_side = 1 if pl["hy"] < holder["y"] else -1
                    # [신규 — 서포트런 긴급도 반영] 홀더가 상대 프레서한테
                    # 바짝 쫓기고 있으면(_support_urgency↑), 서포트도 더
                    # 적극적으로 다가붙어서(레인 오프셋을 좁히고 홀더 쪽
                    # 블렌드 비중을 높여서) 짧고 안전한 출구를 만들어준다.
                    # 여유 있을 때는 예전처럼 자기 레인을 유지한다.
                    _lean_w = 0.4 + _support_urgency * 0.35
                    _lane_gap = 0.18 - _support_urgency * 0.07
                    target_x = pl["hx"] + adv * max(push, 0.6) + 0.06 * sign
                    holder_lean_y = holder["y"] + lane_side * _lane_gap
                    target_y = pl["hy"] * (1 - _lean_w) + holder_lean_y * _lean_w
                elif (not is_holder_side) and i == presser_idx:
                    target_x = pl["hx"] + (self.ball["x"] - pl["hx"]) * 0.45
                    target_y = pl["hy"] + (self.ball["y"] - pl["hy"]) * 0.45
                elif (not is_holder_side) and i in cover_idx:
                    # [신규] 커버 — 압박수비 혼자 튀어나가고 나머지는 완전히
                    # 무관심하게 제자리인 문제를 없앤다. 일반 선수보다 볼에
                    # 더 적극적으로 반응하고(0.30 vs 일반 0.07~0.16),
                    # 압박수비가 비운 중앙 공간 쪽으로도 살짝 좁혀 들어가서
                    # "한 명이 나가면 옆이 채워주는" 유기적 커버를 만든다.
                    _presser_pl = team[presser_idx]
                    target_x = pl["hx"] + (self.ball["x"] - pl["hx"]) * 0.30
                    target_y = (pl["hy"] + (self.ball["y"] - pl["hy"]) * 0.30
                                + (_presser_pl["hy"] - pl["hy"]) * 0.15)
                elif is_holder_side and i == self._advancing_mid_idx:
                    # [신규] 박스투박스 전진 가담 — CM 2명이 똑같이 움직이지
                    # 않도록, 볼과 가까운 쪽은 박스까지 적극적으로 전진한다.
                    if formation == "4-3-3":
                        # [신규 — 메찰라 하프스페이스 침투] 4-3-3의 전진 CM
                        # (메찰라)은 그냥 볼 쪽으로 붙는 게 아니라, 풀백/윙어
                        # 채널(y≈0.14/0.86)과 중앙 채널(y=0.5) 사이의 특정
                        # 통로(하프스페이스, y≈0.28/0.72)를 대각선으로 파고
                        # 든다 — 지금까진 그냥 "볼 쪽으로 30%만 쏠리는"
                        # 범용 공식이라 이 채널 개념 자체가 없었다.
                        _half_space_y = 0.28 if pl["hy"] < 0.5 else 0.72
                        target_x = pl["hx"] + adv * max(push, 0.85) * 1.45
                        target_y = pl["hy"] + (_half_space_y - pl["hy"]) * 0.55
                    else:
                        target_x = pl["hx"] + adv * max(push, 0.8) * 1.35
                        target_y = pl["hy"] + (self.ball["y"] - pl["hy"]) * 0.30
                elif is_holder_side and i == self._holding_mid_idx:
                    # [신규] 홀딩 — 반대쪽 CM은 전진을 억제하고 뒤에 남아
                    # 역습 대비/후방 안정판(피벗) 역할을 한다.
                    target_x = pl["hx"] + adv * 0.35
                    target_y = pl["hy"] + (self.ball["y"] - pl["hy"]) * 0.08
                elif is_holder_side and i == self._check_in_idx:
                    # [신규] 체크인 — ST 2명이 나란히 붙어있지 않도록, 볼과
                    # 가까운 쪽은 내려와서 볼을 받는(연계) 움직임을 한다.
                    target_x = pl["hx"] + (self.ball["x"] - pl["hx"]) * 0.35
                    target_y = pl["hy"] + (self.ball["y"] - pl["hy"]) * 0.30
                elif is_holder_side and i == self._run_behind_idx:
                    # [신규] 침투런 — 반대쪽 ST는 최전방에 남아 상대 최종
                    # 수비라인 뒷공간을 노린다(더 적극적으로 전진).
                    target_x = pl["hx"] + adv * max(push, 0.9) * 1.25
                    target_y = pl["hy"] + (self.ball["y"] - pl["hy"]) * 0.06
                    # [신규] 오프사이드 라인(간이). 완전한 오프사이드 판정
                    # 로직은 아니지만, 상대 수비 전원보다 훨씬 앞서 정적으로
                    # 서 있는 부자연스러운 장면은 막는다 — 상대 최종 수비
                    # 라인보다 살짝만 앞설 수 있게 클램프한다.
                    opp_team_now = self.away_players if side == "home" else self.home_players
                    opp_def_xs = [p["x"] for p in opp_team_now if p["pos"] != "GK"]
                    if opp_def_xs:
                        if sign > 0:
                            target_x = min(target_x, max(opp_def_xs) + 0.03)
                        else:
                            target_x = max(target_x, min(opp_def_xs) - 0.03)
                elif (is_holder_side and i == self._volpiana_idx and push < 0.35):
                    # [신규 — 라 볼피아나] 단독 CDM이 우리 진영 깊숙이서
                    # 빌드업 중(push 낮음 = 자기 진영)일 때, 센터백 사이로
                    # 내려가 임시 스리백을 만든다. 문서에서 말하는 "양
                    # 풀백이 올라갔을 때 센터백 사이로 내려가는" 그 움직임 —
                    # 지금까진 CDM이 포메이션별 전/후퇴폭표 하나로만
                    # 움직여서 이 특정 빌드업 패턴 자체가 없었다. 어느
                    # 풀백이 전진했는지(hx 대비 실제 x가 더 나갔는지)를
                    # 보고, 그 풀백이 비운 반대쪽으로 치우쳐 내려가
                    # 실제 라볼피아나처럼 좌우 비대칭 형태를 만든다.
                    _fb_pool = [p for p in holder_team
                                if p["pos"] in ("LB", "RB", "LWB", "RWB")]
                    _cb_pool = [p for p in holder_team if p["pos"] == "CB"]
                    if _fb_pool and _cb_pool:
                        _adv_fb = max(_fb_pool, key=lambda p: (p["x"] - p["hx"]) * sign)
                        _lean = -1 if _adv_fb["hy"] < 0.5 else 1
                        _cb_avg_x = sum(p["x"] for p in _cb_pool) / len(_cb_pool)
                        target_x = _cb_avg_x - 0.03 * sign
                        target_y = 0.5 + _lean * 0.14
                    else:
                        target_x = pl["hx"] + adv
                        target_y = pl["hy"]
                elif (not is_holder_side) and i in self._breakthrough_marks:
                    # [신규] 돌파 마크 — 프레서/커버는 홀더 근처만 신경써서,
                    # 공 없이 라인 뒤까지 파고든 침투 선수는 무방비였다.
                    # 그 선수를 실시간으로 쫓아가되(맨마킹), 완전히 같은
                    # 자리가 아니라 자기 골 쪽으로 살짝 걸치듯 서서(커버
                    # 섀도잉) 슛 각도까지 좁혀준다.
                    _atk_side_team = (self.home_players if self._breakthrough_atk_side == "home"
                                      else self.away_players)
                    _mark_pl = _atk_side_team[self._breakthrough_marks[i]]
                    _own_goal_x = 0.0 if sign > 0 else 1.0
                    target_x = _mark_pl["x"] + (_own_goal_x - _mark_pl["x"]) * 0.12
                    target_y = _mark_pl["y"]
                elif pl["pos"] == "GK":
                    # [신규 — GK 스위퍼 라인] 예전엔 GK 전/후퇴폭이 표에서
                    # 사실상 0(0.03/0.02)이라, 팀이 하이프레스로 라인을
                    # 완전히 밀어 올려도 GK는 항상 자기 골문 바로 앞에
                    # 얼어붙어 있었다("GK가 상황과 무관하게 안 움직인다"는
                    # 지적 그대로). 실제로는 수비 라인을 높이 올릴수록
                    # GK도 같이 전진해서 뒷공간(라인 너머로 뚫고 들어오는
                    # 침투패스)을 커버해야 한다 — push(우리 팀 전개도)에
                    # 비례해서 전진폭을 준다.
                    _sweep = 0.04 + push * 0.11   # 로우블록 0.04 ~ 하이프레스 0.15
                    target_x = pl["hx"] + _sweep * sign
                    # [주의] 위 일반 공식의 강한 폭 컴팩트니스(0.22~0.32)는
                    # GK한테 그대로 적용하면 안 된다 — 골문 중앙에서 크게
                    # 벗어나면 안 되므로 아주 약하게만 볼 쪽으로 따라간다.
                    target_y = pl["hy"] + (self.ball["y"] - pl["hy"]) * 0.05

                if pl["pos"] != "GK":
                    # [버그 수정 — 근본 원인] 처음엔 이 클램프를 훨씬 앞
                    # (일반 수비 공식 직후)에 걸었는데, 그 뒤에 있는 "커버
                    # 형태로 더 물러나기"(target_x -= ... * sign) 조정이
                    # 클램프 이후에 다시 최대 0.05만큼 더 깎아버려서 무용지
                    # 물이 됐었다(실측: CB/LB가 골키퍼 옆 간격 0.00001
                    # 수준까지 붙는 게 그대로 재현됨). 이 함수 안에서
                    # target_x에 손대는 모든 갈래(일반 수비/WIDE_ROLES/
                    # 커버/GK스위퍼 등) 다음, 최종 피치 경계 클램프
                    # 직전인 여기서 한 번만 걸어야 실제로 안 뚫린다.
                    _own_goal_x = 0.0 if sign > 0 else 1.0
                    _min_x = _own_goal_x + _MIN_DEFENSIVE_DEPTH * sign
                    target_x = max(target_x, _min_x) if sign > 0 else min(target_x, _min_x)
                    # [버그 수정 — 2차 원인] 골라인 기준 절대 클램프만으론
                    # 부족했다 — GK 스위퍼 전진폭(_sweep)이 하이프레스일 때
                    # 0.15까지 커지는데, 그러면 GK 자신이 오히려 이 절대
                    # 클램프(0.12)보다 얕은 곳까지 나가 있어서 CB가 절대
                    # 클램프를 지키고 있어도 GK와 겹쳐버렸다(실측: 간격
                    # -0.0015, 즉 CB가 GK보다도 골대에 가까움). GK는 항상
                    # 이 루프에서 인덱스 0으로 먼저 처리되므로, 그 실시간
                    # 위치를 직접 기준 삼아 "GK보다는 항상 앞에 있어야
                    # 한다"는 걸 추가로 강제한다.
                    _gk_x = team[_gk_idx_this_side]["x"]
                    _min_gap_from_gk = 0.06
                    if sign > 0:
                        target_x = max(target_x, _gk_x + _min_gap_from_gk)
                    else:
                        target_x = min(target_x, _gk_x - _min_gap_from_gk)

                target_x = min(0.97, max(0.03, target_x))
                target_y = min(0.95, max(0.05, target_y))
                # [개인화 + 전환 부스트] 기본 최고속도에 (1) 턴오버 직후
                # 감쇠 중인 전환 부스트(전원 공통)와 (2) 선수별 반응성
                # 편차를 함께 곱한다. 반응성이 높을수록 목표 지점까지
                # smooth_time도 짧게 줘서(=더 기민하게 붙는다) 속도뿐
                # 아니라 "반응 타이밍" 자체도 선수마다 달라지게 한다.
                reaction = pl.get("reaction", 1.0)
                # [신규 — 로스터 스탯 반영] speed 스탯(0~100대, 50이 평균)을
                # 최고속도에 곱한다 — 지금까진 포지션만 같으면 스피드
                # 스탯과 무관하게 전원이 완전히 똑같은 속도로 뛰었다.
                _speed_stat = pl.get("stats", {}).get("speed", 50)
                _speed_mult = 0.75 + (_speed_stat / 100.0) * 0.5
                max_speed = (_MAX_SPEED.get(pl["pos"], 0.6) * transition_mult
                             * (0.9 + 0.1 * reaction) * _speed_mult)
                smooth_time = _SMOOTH_TIME / reaction
                _steer_toward(pl, target_x, target_y, dt, max_speed, smooth_time)

        # [신규 — 겹침 방지] 파울 재개처럼 여러 선수(같은 팀 CM 2명, 체크인/
        # 침투런 ST 2명 등)의 목표 좌표가 한 틱 안에서 거의 똑같이 계산되면,
        # 완전히 겹쳐서 화면상 점 하나로 보이는 경우가 있었다(헤드리스
        # 검증에서 파울 재개 구간에 겹침이 몰려있는 게 확인됨). 씬/코너킥
        # 크라우드처럼 "의도적으로" 밀집해야 하는 선수는 건드리면 안 되므로
        # 그쪽은 제외하고, 일반 이동 중인 같은 팀 선수끼리만 아주 살짝
        # 밀어내서 최소 간격을 확보한다. 상대 팀과의 거리는 건드리지 않는다
        # — 실제 축구에서도 밀착 마크는 정상이라 겹침 판정 대상이 아니다.
        _MIN_TEAMMATE_DIST = 0.02
        for side, team in (("home", self.home_players), ("away", self.away_players)):
            if scene_active:
                is_scene_side = (side == self._scene_side)
                sk = (atk_skip if is_scene_side else def_skip)
            else:
                sk = set()
            cl = ((self._corner_lock_home if side == "home" else self._corner_lock_away)
                  if corner_lock_active else set())
            free_idx = [i for i, pl in enumerate(team)
                        if pl["pos"] != "GK" and i not in sk and i not in cl]
            for a in range(len(free_idx)):
                ia = free_idx[a]
                pa = team[ia]
                for b in range(a + 1, len(free_idx)):
                    ib = free_idx[b]
                    pb = team[ib]
                    dx = pa["x"] - pb["x"]
                    dy = pa["y"] - pb["y"]
                    dist = math.hypot(dx, dy)
                    if 0.0001 < dist < _MIN_TEAMMATE_DIST:
                        push = (_MIN_TEAMMATE_DIST - dist) * 0.5
                        nx, ny = dx / dist, dy / dist
                        pa["x"] = min(0.97, max(0.03, pa["x"] + nx * push))
                        pa["y"] = min(0.95, max(0.05, pa["y"] + ny * push))
                        pb["x"] = min(0.97, max(0.03, pb["x"] - nx * push))
                        pb["y"] = min(0.95, max(0.05, pb["y"] - ny * push))
                    elif dist <= 0.0001:
                        # 완전히 같은 좌표(0으로 나누기 방지) — 인덱스 기반
                        # 고정 방향으로 살짝 떼어놓는다.
                        push = _MIN_TEAMMATE_DIST * 0.5
                        pa["x"] = min(0.97, max(0.03, pa["x"] + push))
                        pb["x"] = min(0.97, max(0.03, pb["x"] - push))

        return dt

    def _update_possession(self, speed_scale=1.0):
        """공은 항상 누군가의 '소유' 상태를 따라가고(패스로만 이동), 점유한
        팀 전체 대형이 서서히 앞으로 밀고 올라간다. 일정 주기마다 전진 방향
        패스 또는 상대에게 턴오버되며, 이 과정에서 자연스럽게 공격 전개가
        만들어진다.

        [움직임 리얼리즘] 모든 선수는 SmoothDamp(임계감쇠) 방식으로 움직여서
        오버슈트(진동)가 나지 않는다. 그중에서도:
          - 홀더: 대형 전진 목표를 향해 공을 몰고 감(드리블 전개)
          - 지원런 2명: 홀더보다 앞선 가까운 동료가 좌우로 벌려 패스 각도를 만듦
          - 압박 수비 1명: 상대팀에서 홀더와 가장 가까운 선수가 실제로 다가붙음
          - 나머지: 기존처럼 대형(shape_push) 기준 위치 유지

        [떨림 방지] 지원런/압박 대상(support_idx/presser_idx)은 이 함수에서
        매 틱 재계산하지 않는다. 매 틱 재계산하면 두 후보의 거리가 엇비슷할
        때 프레임마다 대상이 뒤바뀌면서 목표 좌표가 순간 점프해 떨림처럼
        보인다. 대신 _assign_roles()가 '홀더가 바뀔 때'(패스/턴오버 시점)만
        호출되어 self._support_idx/_presser_idx를 갱신하고, 그 사이(공을
        잡고 있는 동안)엔 값이 고정된다 — 실제 축구에서도 마킹/지원 위치는
        매 순간 바뀌는 게 아니라 볼 소유 국면 단위로 정해진다.
        """
        dt = self._update_player_positions(speed_scale)
        holder_team = self.home_players if self.possession == "home" else self.away_players

        if self._pass_flight:
            self._advance_pass_flight(dt)
        else:
            # [떨림 수정] 공을 홀더 쪽으로 매 프레임 lerp(수렴)시키지 않는다.
            # 그렇게 하면 "홀더는 공을 쫓고, 공은 홀더를 쫓는" 순환참조가
            # 생겨 서로 가까워질수록 방향이 불안정해지며 떨렸다. 이제는 그냥
            # 홀더 발밑에 딸린 좌표로 못박고, 홀더 이동 방향으로 살짝만
            # 리드시켜서(드리블하며 공을 살짝 앞에 두는 느낌) 붙인다.
            holder_now = holder_team[self.holder]
            speed_mag = math.hypot(holder_now["vx"], holder_now["vy"])
            # [버그 수정] 예전엔 리드 거리가 0.045라 실제 화면(피치 폭
            # 기준)에서 약 30px씩 떨어져 보였다 — 선수 반지름(8px)+공
            # 반지름(5px)을 합쳐도 13px인데 그보다 훨씬 멀어서, 홀더 위에
            # 공이 얹힌 게 아니라 둘이 따로 노는 것처럼 보였다(사용자
            # 지적 그대로). 0.014로 줄여서 화면상 10px 안팎(대략 선수
            # 반지름 정도)만 앞으로 나가게 해 "선수 발밑/약간 앞"으로
            # 보이도록 했다.
            if speed_mag > 1e-4:
                lead_x = holder_now["vx"] / speed_mag * 0.014
                lead_y = holder_now["vy"] / speed_mag * 0.014
            else:
                lead_x = lead_y = 0.0
            self.ball["x"] = min(0.99, max(0.01, holder_now["x"] + lead_x))
            self.ball["y"] = min(0.99, max(0.01, holder_now["y"] + lead_y))

            self.pass_clock -= dt
            if self.pass_clock <= 0:
                self._do_pass_or_turnover()
                # [개선] 예전엔 1.0~2.2(평균 약 1.6) → dt=0.12 기준 약 13틱
                # (=0.8 실제초 @1x)마다 한 번만 패스/턴오버가 나서, 그 사이엔
                # 다들 목표에 빠르게 도달해 거의 멈춰 있다가 이 주기마다만
                # "확" 바뀌는 것처럼 보였다(체감상 "1초에 2번 툭툭 끊김").
                # 0.5~1.2(평균 약 0.85)로 줄여 패스가 대략 2배 더 잦아지도록
                # 해서 끊김 없이 계속 전개되는 느낌을 살렸다.
                self.pass_clock = random.uniform(0.5, 1.2)

    # ══════════════════════════════════════════════════════════════
    # [재설계 — 데드볼 상태 머신]
    # 예전엔 파울/코너킥/스로인/골킥/필러슛(골·선방·오프타깃·블록) 처리가
    # 7개의 독립된 코드 조각으로 흩어져 있었고, 서로의 존재를 몰랐다.
    # 그래서 두 재개가 타이밍이 겹치면 공을 두 번 스냅하거나, 하나가
    # 끝나기도 전에 오픈플레이가 끼어들어 궤적을 덮어쓰는 등, "조각들
    # 경계"에서 계속 새로운 버그가 튀어나왔다(파울 텍스트+필러 파울 중복
    # 발동, 코너 전조 중 오픈플레이 틈입, 필러슛 결과 미처리 등 — 전부
    # 실측 디버그 캡처로 재현됨).
    #
    # 이 문제를 시간 기반 락(_dead_ball_until)으로 막아보려 했으나,
    # 필러 이벤트 평균 간격(약 1분)보다 락 시간이 길어서 경기 전체가
    # 멈추는 새로운 회귀가 생겼다(실측: 80개 넘는 예정 이벤트가 전부
    # 유실됨). 근본 문제는 "시간"이 아니라 "지금 정확히 무슨 상태인가"를
    # 추적하는 단일 상태가 없었다는 것 — 그래서 시간 대신 상태로 다시
    # 짠다.
    #
    # self._db가 None이면 오픈플레이(_update_possession이 정상 작동).
    # None이 아니면 {"kind": "throw_in"/"goal_kick"/"foul"/"corner"/
    # "shot", "phase": ..., ...그 kind에 필요한 부가정보}이고, 매 틱
    # _advance_dead_ball()이 그 kind의 _db_step_<kind>()를 호출해 진행시킨다.
    # 각 kind의 종료 조건은 전부 "실제로 그 애니메이션이 끝났는가"
    # (_pass_flight가 None이 됐는가, 글라이드 타이머가 지났는가)에 묶여
    # 있어서, 임의의 시간 추측이 끼어들 자리가 없다. shot처럼 결과에 따라
    # 다른 데드볼로 이어져야 하면(save→골킥, 블록→코너) 오픈플레이를 한
    # 틱도 거치지 않고 kind만 바꿔치기해서 바로 이어간다 — 그 사이에
    # 오픈플레이가 끼어들 타이밍 자체가 존재하지 않는다.
    # ══════════════════════════════════════════════════════════════

    def _enter_dead_ball(self, kind, **kw):
        """데드볼 진입점. 이미 다른 데드볼이 진행 중이면 아무 것도 바꾸지
        않고 False를 돌려준다 — 호출자는 그 틱엔 스킵하고(done 처리하지
        않고 continue 등) 다음 틱에 다시 시도해야 한다. 성공하면 True."""
        if self._db is not None:
            return False
        self._db = {"kind": kind, "phase": "start", **kw}
        self._last_restart_clock = self.clock
        getattr(self, f"_db_start_{kind}")()
        return True

    def _advance_dead_ball(self, speed_scale=1.0):
        """매 틱 호출 — 지금 진행 중인 데드볼 kind의 스텝 함수 하나만
        실행한다. 오픈플레이(_update_possession)와는 절대 동시에 안
        불린다(_advance_one_tick의 단일 분기에서 양자택일)."""
        getattr(self, f"_db_step_{self._db['kind']}")(speed_scale)

    def _db_exit(self):
        self._db = None

    # ── 스로인 ──────────────────────────────────────────────
    def _db_start_throw_in(self):
        d = self._db
        out_side, x, y = d["out_side"], d["x"], d["y"]
        throw_side = "away" if out_side == "home" else "home"
        throw_team = self.home_players if throw_side == "home" else self.away_players
        line_y = 0.02 if y < 0.5 else 0.98
        thrower = min(range(len(throw_team)),
                      key=lambda i: abs(throw_team[i]["x"] - x) + abs(throw_team[i]["y"] - line_y))
        throw_team[thrower]["x"], throw_team[thrower]["y"] = x, line_y
        throw_team[thrower]["vx"] = throw_team[thrower]["vy"] = 0.0
        recv_pool = [i for i, pl in enumerate(throw_team) if i != thrower and pl["pos"] != "GK"]
        receiver = (min(recv_pool, key=lambda i: abs(throw_team[i]["x"] - x)
                                                + abs(throw_team[i]["y"] - line_y))
                    if recv_pool else thrower)
        self.possession = throw_side
        self.holder = receiver
        self._assign_roles()
        self.ball["x"], self.ball["y"] = x, line_y
        self.ball_trail = []
        self._start_pass_flight((x, line_y), (throw_team[receiver]["x"], throw_team[receiver]["y"]))
        self.pass_clock = 0.7
        self._last_throwin_clock = self.clock

    def _db_step_throw_in(self, speed_scale):
        dt = self._update_player_positions(speed_scale)
        if self._pass_flight:
            self._advance_pass_flight(dt)
        else:
            self._db_exit()

    # ── 골킥 ────────────────────────────────────────────────
    def _db_start_goal_kick(self):
        defending_side = self._db["side"]
        def_team = self.home_players if defending_side == "home" else self.away_players
        gk_idx = self._gk_index(defending_side)
        goal_x = 0.03 if defending_side == "home" else 0.97
        def_team[gk_idx]["x"], def_team[gk_idx]["y"] = goal_x, 0.5
        def_team[gk_idx]["vx"] = def_team[gk_idx]["vy"] = 0.0
        recv_pool = [i for i, pl in enumerate(def_team) if pl["pos"] != "GK"]
        receiver = (min(recv_pool, key=lambda i: abs(def_team[i]["x"] - goal_x))
                    if recv_pool else gk_idx)
        self.possession = defending_side
        self.holder = receiver
        self._assign_roles()
        self.ball["x"], self.ball["y"] = goal_x, 0.5
        self.ball_trail = []
        self._start_pass_flight((goal_x, 0.5), (def_team[receiver]["x"], def_team[receiver]["y"]))
        self.pass_clock = 0.8

    def _db_step_goal_kick(self, speed_scale):
        dt = self._update_player_positions(speed_scale)
        if self._pass_flight:
            self._advance_pass_flight(dt)
        else:
            self._db_exit()

    # ── 파울(프리킥) ────────────────────────────────────────
    def _snap_foul_crowd(self, attacking_side, ball_x, ball_y, taker):
        """[신규 — 위험 지역 파울 크라우드] 상대 진영 슈팅권(_SHOT_ZONE_NORMAL
        이내)에서 파울이 나면, 지금까진 반칙 지점 최근접자(키커)와 그
        즉시 패스를 받는 동료 딱 2명만 자리를 잡고, 나머지 20명은 그
        순간에도 평소 오픈플레이 공식(홀더 근처 지원런/프레서/커버만
        반응)에 맡겨져 있었다. 그 공식은 "움직이는 공"이 전제라 세트피스
        상황(다들 정해진 자리에 서야 함)을 전혀 반영 못 해서, 나머지가
        목적 없이 근처를 배회하는 것처럼 보였다("파울 잡은 애 빼고
        나머지는 주변 굴러다닌다"는 지적 그대로 — PK 때 겪었던 것과
        같은 종류의 공백).

        코너킥과 같은 락 메커니즘(_corner_lock_home/away,
        _info_glide_until)을 재사용한다 — 수비는 반칙 지점과 자기 골
        사이에 벽을 세우고 나머지는 박스 수비 슬롯으로, 공격은 박스 크로스
        슬롯(니어포스트/식스야드/파포스트/박스 엣지, _corner_slots 재사용)
        으로 채운다.
        """
        atk_goal_x = 1.0 if attacking_side == "home" else 0.0
        atk_team = self.home_players if attacking_side == "home" else self.away_players
        def_team = self.away_players if attacking_side == "home" else self.home_players

        # ── 수비 벽: 반칙 지점과 자기 골문 사이, 직선 각도를 막는 자리 ──
        wall_dir = -1 if atk_goal_x >= 0.5 else 1
        wall_x = max(0.03, min(0.97, ball_x + wall_dir * 0.10))
        n_wall = 3 if abs(ball_x - atk_goal_x) < 0.25 else 2
        def_pool = [i for i, pl in enumerate(def_team) if pl["pos"] != "GK"]
        random.shuffle(def_pool)
        wall_idx = def_pool[:n_wall]
        rest_def_pool = def_pool[n_wall:]
        wall_center_y = ball_y + (0.5 - ball_y) * 0.5
        for k, i in enumerate(wall_idx):
            _off = (k - (n_wall - 1) / 2) * 0.035
            def_team[i]["x"] = wall_x
            def_team[i]["y"] = max(0.06, min(0.94, wall_center_y + _off))
            def_team[i]["vx"] = def_team[i]["vy"] = 0.0

        # ── 나머지는 박스 크로스 슬롯으로(코너킥과 동일한 슬롯 재사용) ──
        atk_pool = [i for i, pl in enumerate(atk_team) if pl["pos"] != "GK" and i != taker]
        random.shuffle(atk_pool)
        atk_stay = atk_pool[-1:] if len(atk_pool) > 3 else []
        crowd_atk = [i for i in atk_pool if i not in atk_stay]
        crowd_def = rest_def_pool[:-1] if len(rest_def_pool) > 3 else rest_def_pool
        _corner_y_proxy = 0.04 if ball_y < 0.5 else 0.96
        atk_pts, def_pts = _corner_slots(atk_goal_x, _corner_y_proxy, len(crowd_atk), len(crowd_def))
        for i, (tx, ty) in zip(crowd_atk, atk_pts):
            atk_team[i]["x"], atk_team[i]["y"] = tx, ty
            atk_team[i]["vx"] = atk_team[i]["vy"] = 0.0
        for i, (tx, ty) in zip(crowd_def, def_pts):
            def_team[i]["x"], def_team[i]["y"] = tx, ty
            def_team[i]["vx"] = def_team[i]["vy"] = 0.0

        atk_locked = set(crowd_atk) | {taker}
        def_locked = set(wall_idx) | set(crowd_def)
        if attacking_side == "home":
            self._corner_lock_home, self._corner_lock_away = atk_locked, def_locked
        else:
            self._corner_lock_away, self._corner_lock_home = atk_locked, def_locked

    def _snap_foul_general(self, restart_side, exclude_idx):
        """[신규] 위험 지역이 아닌 파울(중원/수비 지역)도 코너킥처럼
        즉시 자리를 잡게 한다. 지금까진 위험 지역(_SHOT_ZONE_NORMAL
        이내) 파울만 크라우드/벽을 세웠고, 그 밖의 모든 파울은 키커+
        즉시 패스받는 동료 딱 2명만 자리 잡고 나머지 20명은 여전히
        평소 오픈플레이 공식(움직이는 공을 전제로 한 홀더 추종형 공식)
        에 맡겨진 채였다 — "코너킥처럼 각자 포지션이 바로 잡혀야
        하는데 안 된다"는 지적 그대로. 위험 지역처럼 벽/침투조 같은
        정교한 역할까지는 필요 없지만, 최소한 각 포지션의 포메이션별
        전/후진표(_tactical_dx)가 가리키는 목표로 즉시 스냅해서 "다들
        멈춰서 자리를 잡는" 정지 구간을 만든다. 코너킥과 같은 락
        (_corner_lock_home/away, _info_glide_until)을 재사용해서, 이
        글라이드 구간 동안은 평소 공식이 다시 끌고 가지 않게 보호한다.
        """
        for side, team in (("home", self.home_players), ("away", self.away_players)):
            formation = self.home_formation if side == "home" else self.away_formation
            push = self.shape_push[side]
            sign = 1 if side == "home" else -1
            locked = set()
            for i, pl in enumerate(team):
                if pl["pos"] == "GK":
                    continue
                if side == restart_side and i == exclude_idx:
                    continue
                fwd, back = _tactical_dx(formation, pl["pos"])
                adv = (fwd * push - back * (1 - push)) * sign
                # [버그 수정] hx/hy 그대로 스냅하면 양 팀 같은 포지션
                # 라벨(예: 양쪽 ST, 양쪽 LW)이 서로 정확히 같은 y에 자석
                # 처럼 마주 붙어 서서 "로봇 같은 데칼코마니 배치"로 보였다
                # (신민용 지적). 작은 지터를 줘서 실제로 각자 마크맨을
                # 찾아 선 것처럼 자연스러운 편차를 만든다.
                pl["x"] = max(0.03, min(0.97, pl["hx"] + adv + random.uniform(-0.015, 0.015)))
                pl["y"] = max(0.04, min(0.96, pl["hy"] + random.uniform(-0.03, 0.03)))
                pl["vx"] = pl["vy"] = 0.0
                locked.add(i)
            if side == "home":
                self._corner_lock_home = locked
            else:
                self._corner_lock_away = locked

    def _db_start_foul(self):
        d = self._db
        restart_side = d["side"]
        restart_team = self.home_players if restart_side == "home" else self.away_players
        self.possession = restart_side
        self.holder = min(
            (i for i, pl in enumerate(restart_team) if pl["pos"] != "GK"),
            key=lambda i: abs(restart_team[i]["x"] - self.ball["x"])
                         + abs(restart_team[i]["y"] - self.ball["y"]))
        self._assign_roles()
        self.ball["x"] = restart_team[self.holder]["x"]
        self.ball["y"] = restart_team[self.holder]["y"]
        self.ball_trail = []
        self._pass_flight = None
        _bx = self.ball["x"]
        self.shape_push["home"] = max(0.0, min(1.0, _bx))
        self.shape_push["away"] = max(0.0, min(1.0, 1.0 - _bx))
        self._info_glide_until = self.clock + 0.35
        d["glide_until"] = self._info_glide_until
        taker = self.holder
        # [버그 수정 — 근본 원인] 위험 지역(슈팅권 이내) 파울은 지금까지도
        # 그냥 "가장 가까운 동료에게 짧은 탭 패스"로 처리됐다 — 실제로는
        # 이 정도 거리면 직접 슈팅이나 박스 안으로의 크로스가 정상인데,
        # 크라우드 정렬도 없이 짧은 탭 패스만 나가니 나머지 20명은 목적
        # 없는 오픈플레이 공식에 맡겨진 채였다. 위험 지역이면 크라우드를
        # 정렬(_snap_foul_crowd)하고 박스 안으로 크로스를 올린다 — 도착
        # 후에는 방금 만든 "이벤트 당김" 레이어가 있어서, 근처에 예정된
        # 슛이 있으면 자연스럽게 이어질 수도 있다.
        _opp_goal_x = 1.0 if restart_side == "home" else 0.0
        if abs(self.ball["x"] - _opp_goal_x) < _SHOT_ZONE_NORMAL:
            self._snap_foul_crowd(restart_side, self.ball["x"], self.ball["y"], taker)
            _box_x = 0.90 if _opp_goal_x >= 0.5 else 0.10
            _target_y = 0.5 + random.uniform(-0.16, 0.16)
            start_xy = (self.ball["x"], self.ball["y"])
            self._start_pass_flight(start_xy, (_box_x, _target_y))
            self.pass_clock = 0.9
            _crowd_atk = [i for i in (self._corner_lock_home if restart_side == "home"
                                       else self._corner_lock_away) if i != taker]
            if _crowd_atk:
                new_holder = min(_crowd_atk, key=lambda i: abs(restart_team[i]["x"] - _box_x)
                                                          + abs(restart_team[i]["y"] - _target_y))
                self.holder = new_holder
                self._assign_roles()
            return
        # [신규 — 프리킥 즉시 배급] 위험 지역이 아니면(중원/수비 지역
        # 파울) 지금까지처럼 반칙 지점에서 가장 가까운 동료에게 즉시
        # 짧은 프리킥 패스를 날려서 "그 자리에서 바로 찼다"는 것을
        # 보여준다(스로인/골킥과 동일한 패턴). 그 전에 나머지 20명을
        # 먼저 스냅해서(_snap_foul_general), 즉시 패스받을 동료도
        # 그 순간 실제로 있어야 할 자리에서 받게 한다.
        self._snap_foul_general(restart_side, taker)
        recv_pool = [i for i, pl in enumerate(restart_team) if i != taker and pl["pos"] != "GK"]
        if recv_pool:
            receiver = min(recv_pool, key=lambda i: abs(restart_team[i]["x"] - self.ball["x"])
                                                    + abs(restart_team[i]["y"] - self.ball["y"]))
            start_xy = (self.ball["x"], self.ball["y"])
            self.holder = receiver
            self._assign_roles()
            self._start_pass_flight(start_xy, (restart_team[receiver]["x"], restart_team[receiver]["y"]))
            self.pass_clock = 0.8

    def _db_step_foul(self, speed_scale):
        glide_active = self.clock < self._db["glide_until"]
        dt = self._update_player_positions(2.4 if glide_active else speed_scale)
        if self._pass_flight:
            self._advance_pass_flight(dt)
        elif glide_active:
            _team = self.home_players if self.possession == "home" else self.away_players
            _holder = _team[self.holder]
            self.ball["x"], self.ball["y"] = _holder["x"], _holder["y"]
        if not self._pass_flight and not glide_active:
            self._db_exit()

    # ── 코너킥 ──────────────────────────────────────────────
    def _db_start_corner(self):
        d = self._db
        attacking_side = d["side"]
        cgoal_x = 1.0 if attacking_side == "home" else 0.0
        corner_x_lead = 0.98 if cgoal_x >= 0.5 else 0.02
        corner_y_lead = 0.04 if self.ball["y"] < 0.5 else 0.96
        self._start_pass_flight((self.ball["x"], self.ball["y"]), (corner_x_lead, corner_y_lead))
        d["phase"] = "approach"

    def _db_step_corner(self, speed_scale):
        d = self._db
        if d["phase"] == "approach":
            # 공이 라인 쪽으로 굴러나가는 전조 비행 구간.
            dt = self._update_player_positions(speed_scale)
            if self._pass_flight:
                self._advance_pass_flight(dt)
            else:
                _team_name = self.home_name if d["side"] == "home" else self.away_name
                self.banner_text = f"🚩 {_team_name} 코너킥"
                self.banner_color = "#ffcc55"
                self.banner_alpha = 255
                self._snap_corner_crowd(d["side"])
                self._info_glide_until = self.clock + 0.35
                d["glide_until"] = self._info_glide_until
                d["phase"] = "crowd"
        else:
            # 크라우드가 박스 대형으로 정렬되는 글라이드 구간.
            glide_active = self.clock < d["glide_until"]
            dt = self._update_player_positions(2.4 if glide_active else speed_scale)
            if self._pass_flight:
                self._advance_pass_flight(dt)
            if not glide_active:
                self._db_exit()

    # ── 필러 슛(골/선방/오프타깃/블록) ──────────────────────
    def _db_start_shot(self):
        d = self._db
        fside, outcome = d["side"], d["outcome"]
        fteam = self.home_players if fside == "home" else self.away_players
        fgoal_x = 1.0 if fside == "home" else 0.0
        fpool = [i for i, pl in enumerate(fteam) if pl["pos"] in (_ATTACK_ROLES | _SUPPORT_ROLES)]
        ftaker = random.choice(fpool) if fpool else random.randrange(len(fteam))
        fsx = _clamp_shot_start_x(fteam[ftaker]["x"], fgoal_x, _SHOT_ZONE_NORMAL)
        fsy = fteam[ftaker]["y"]
        if outcome in ("goal", "save"):
            fty = 0.5 + random.uniform(-1, 1) * _GOAL_HALF_HEIGHT * 0.8
        else:
            fsign = random.choice([-1, 1])
            fty = 0.5 + fsign * random.uniform(_GOAL_HALF_HEIGHT * 1.3, _GOAL_HALF_HEIGHT * 3.0)
        fty = max(0.04, min(0.96, fty))
        self.possession = fside
        self.holder = ftaker
        self._assign_roles()
        self.ball["x"], self.ball["y"] = fsx, fsy
        self.ball_trail = []
        self._start_pass_flight((fsx, fsy), (fgoal_x, fty))
        self.pass_clock = 0.9

        # [버그 수정 — 근본 원인] shape_push(팀 전체 전진/후퇴 폭을 정하는
        # 스칼라)는 tactical_engine의 "그 분에 예정된 buildup 존" 힌트나
        # 느린 연속 수렴으로만 갱신됐지, 슛이 시작되는 이 순간(ftaker가
        # 실제로 얼마나 전진해 있었는지)은 전혀 반영하지 않았다. 그래서
        # 슈터 본인은 슛 궤적 계산으로 확실히 상대 박스 근처까지 가
        # 있는데, 나머지 10명(수비수 제외 CM/윙백 등)은 이 stale한
        # push값 기준으로만 전/후진 목표를 잡아서 슈터 혼자만 전진해
        # 있고 나머지는 안 들어와 있는 것처럼 보였다(신민용 지적 —
        # "수비수는 그렇다치고 왜 나머지는 안 들어와 있냐"의 정확한
        # 원인). 파울 재개(_db_start_foul)가 이미 하는 것과 같은 방식
        # (공 x좌표 기준 직접 설정)으로 슈팅 시작 시점에도 즉시 반영한다.
        self.shape_push[fside] = max(0.0, min(1.0, fsx if fgoal_x >= 0.5 else 1.0 - fsx))
        self.shape_push["away" if fside == "home" else "home"] = 1.0 - self.shape_push[fside]
        d["phase"] = "flight"

        # [버그 수정 — 근본 원인] 이 함수(필러 슛 — 실제 경기 슈팅의 대다수가
        # 여기 해당)는 지금까지 배너를 전혀 안 건드렸다. 그래서 코너킥/파울/
        # 선방 배너가 뜬 채로 몇 초~몇 분이 지나 완전히 무관한 새 슈팅이
        # 시작돼도 화면엔 예전 배너가 그대로 남아있었다 — "왜 이런지 이유도
        # 안 보이는데 공이 갑자기 튕긴다"는 지적(디버그 캡처로 실측: 코너킥
        # 배너가 뜬 채로 10초 넘게 지나도 안 바뀌고, 그 사이 새 슈팅이 시작
        # 되면서 공이 무관한 위치로 순간이동)이 정확히 이 공백 때문이었다.
        # 슈팅이 시작되는 바로 이 순간 배너를 갱신해서, 결과가 나오기 전에도
        # "지금 새로운 슈팅 상황"이라는 게 화면에 바로 드러나게 한다.
        _fteam_name = self.home_name if fside == "home" else self.away_name
        self.banner_text = f"⚡ {_fteam_name} 슈팅!"
        self.banner_color = "#ffcc55"
        self.banner_alpha = 255

        # [신규 — GK 다이빙 목표] 막는 팀 GK가 이 슛의 결과(골/선방/노골)에
        # 맞춰 실제로 반응하는 것처럼 보이도록, 비행이 끝났을 때 도달해
        # 있을 목표 좌표를 미리 정해둔다. _db_step_shot이 비행 진행률에
        # 맞춰 이 목표로 부드럽게 보간한다(공과 같은 이징 곡선 재사용).
        #   - save: 정확히 fty(공이 오는 지점)까지 도달 — 실제로 막아냄.
        #   - goal: fty 쪽으로 몸은 던지지만 절반~2/3 정도만 좁혀서(못
        #     미친 느낌) 최종적으로 골이 되는 것과 시각적으로 모순 안 남.
        #   - shot_off/shot_blocked: 애초에 골문 밖으로 향하는 슛이라
        #     전력 다이빙까지는 필요 없음 — 몸만 살짝 기울이는 정도.
        def_side = "away" if fside == "home" else "home"
        def_team = self.away_players if fside == "home" else self.home_players
        gk_idx = self._gk_index(def_side)
        gk = def_team[gk_idx]
        d["gk_side"] = def_side
        d["gk_idx"] = gk_idx
        d["gk_start"] = (gk["x"], gk["y"])
        _dive_x_off = 0.03 if fgoal_x < 0.5 else -0.03
        gk_dive_x = fgoal_x + _dive_x_off
        if outcome == "save":
            gk_dive_y = fty
        elif outcome == "goal":
            gk_dive_y = 0.5 + (fty - 0.5) * random.uniform(0.45, 0.7)
        else:
            gk_dive_y = 0.5 + (fty - 0.5) * 0.18
        d["gk_target"] = (gk_dive_x, max(0.06, min(0.94, gk_dive_y)))

    def _db_step_shot(self, speed_scale):
        d = self._db
        dt = self._update_player_positions(speed_scale)
        if self._pass_flight:
            pf = self._pass_flight  # advance_pass_flight may null self._pass_flight below
            self._advance_pass_flight(dt)
            # [신규 — GK 다이빙] 공 비행과 같은 진행률(t/dur, 같은 smoothstep
            # 이징)로 GK를 시작 위치에서 다이빙 목표까지 보간한다 — 공이
            # 골문에 도달하는 순간과 GK가 도달 지점에 닿는 순간이 항상
            # 정확히 같은 프레임에 맞아떨어진다.
            _t = min(1.0, pf["t"] / pf["dur"])
            _ease = _t * _t * (3 - 2 * _t)
            _gk_team = self.home_players if d["gk_side"] == "home" else self.away_players
            _gk = _gk_team[d["gk_idx"]]
            _sx, _sy = d["gk_start"]
            _tx, _ty = d["gk_target"]
            _gk["x"] = _sx + (_tx - _sx) * _ease
            _gk["y"] = _sy + (_ty - _sy) * _ease
            return
        # 비행 완료 — 결과를 반영하고, 결과에 따라 다음 데드볼로 그 자리에서
        # 바로 이어간다(오픈플레이를 한 틱도 거치지 않음 — 끼어들 틈 자체가
        # 없다).
        fside, outcome = d["side"], d["outcome"]
        team_name = self.home_name if fside == "home" else self.away_name
        if outcome == "goal":
            if fside == "home":
                self.score_home += 1
            else:
                self.score_away += 1
            self.banner_text = f"⚽ GOAL! {team_name}"
            self.banner_color = "#55ff88"
            self.banner_alpha = 255
            concede_side = "away" if fside == "home" else "home"
            self.possession = concede_side
            self.holder = self._kickoff_holder_index(concede_side)
            self._assign_roles()
            self.ball["x"], self.ball["y"] = 0.5, 0.5
            self.ball_trail = []
            self._pass_flight = None
            self.shape_push["home"] = 0.5
            self.shape_push["away"] = 0.5
            self.pass_clock = random.uniform(0.5, 1.2)
            self._db_exit()
        elif outcome == "save":
            self.banner_text = f"🧤 {team_name} 슈팅, 선방에 막힘"
            self.banner_color = "#ffcc55"
            self.banner_alpha = 255
            d["kind"] = "goal_kick"
            d["side"] = "away" if fside == "home" else "home"
            self._db_start_goal_kick()
        elif outcome == "shot_off":
            # [버그 수정] save/goal은 결과 배너가 있는데 shot_off만 없어서,
            # 골킥으로 조용히 넘어가는 동안 화면엔 예전 배너가 계속 남아
            # 있었다 — 위 "⚡ {team} 슈팅!" 시작 배너로 상황 자체는 이제
            # 보이지만, 결과(빗나감)까지 명확히 알려주는 게 일관성 있다.
            self.banner_text = f"🚫 {team_name} 슈팅, 골대를 벗어남"
            self.banner_color = "#888888"
            self.banner_alpha = 255
            d["kind"] = "goal_kick"
            d["side"] = "away" if fside == "home" else "home"
            self._db_start_goal_kick()
        elif outcome == "shot_blocked":
            # [효율화] 블록된 슛은 이미 골문 근처까지 날아온 상태라,
            # 코너킥의 "접근 굴러나가는" 전조 비행을 또 한 번 반복할
            # 필요가 없다 — 곧장 코너 근처로 스냅하고 크라우드 단계부터
            # 시작한다(이벤트가 많은 경기에서 데드볼 처리 시간을 아껴,
            # 90분 안에 모든 예정 이벤트가 실제로 화면에 나올 시간을
            # 확보한다).
            d["kind"] = "corner"
            d["side"] = fside
            _cgoal_x = 1.0 if fside == "home" else 0.0
            _corner_x_lead = 0.98 if _cgoal_x >= 0.5 else 0.02
            _corner_y_lead = 0.04 if self.ball["y"] < 0.5 else 0.96
            self.ball["x"], self.ball["y"] = _corner_x_lead, _corner_y_lead
            self.ball_trail = []
            self._pass_flight = None
            _team_name2 = self.home_name if fside == "home" else self.away_name
            self.banner_text = f"🚩 {_team_name2} 코너킥"
            self.banner_color = "#ffcc55"
            self.banner_alpha = 255
            self._snap_corner_crowd(fside)
            self._info_glide_until = self.clock + 0.35
            d["glide_until"] = self._info_glide_until
            d["phase"] = "crowd"

    def _start_pass_flight(self, start_xy, end_xy):
        """[패스 궤적] 공이 홀더→홀더로 순간이동하지 않고, 거리 비례 시간
        동안 부드럽게 날아가도록 비행 상태를 만든다. 이동 중 좌표가
        ball_trail에 쌓여 화면에 잔상(궤적)으로 그려진다."""
        dist = math.hypot(end_xy[0] - start_xy[0], end_xy[1] - start_xy[1])
        self._pass_flight = {
            "sx": start_xy[0], "sy": start_xy[1],
            "ex": end_xy[0], "ey": end_xy[1],
            "t": 0.0, "dur": max(0.16, min(0.55, dist * 1.05)),
        }

    def _advance_pass_flight(self, dt):
        pf = self._pass_flight
        pf["t"] += dt
        t = min(1.0, pf["t"] / pf["dur"])
        ease = t * t * (3 - 2 * t)  # smoothstep — 출발/도착이 부드러움
        self.ball["x"] = pf["sx"] + (pf["ex"] - pf["sx"]) * ease
        self.ball["y"] = pf["sy"] + (pf["ey"] - pf["sy"]) * ease
        self.ball_trail.append([self.ball["x"], self.ball["y"], 230])
        if len(self.ball_trail) > 9:
            self.ball_trail.pop(0)
        if t >= 1.0:
            self._pass_flight = None

    def _evaluate_lane_advantage(self, side):
        """[신규 — 오픈플레이 공간평가] tactical_engine의 사후 힌트
        (self._target_lane_y — "그 분에 어느 레인이 우세했는가", 1개
        스칼라값)와 별개로, "지금 이 순간" 실제 22명의 좌표를 갖고
        전술 엔진과 같은 레인 경계(0.34/0.67)로 상대 진영에서의 수적
        우위를 계산한다. tactical_engine의 결과(그 분에 슛/코너가
        터지는지)는 이미 포메이션 매치업을 반영하지만, 그 우위가
        화면에서 "그 구역으로 공이 몰리고 침투가 일어나는" 그림으로는
        전혀 이어지지 않았다 — 이 함수가 그 연결 고리다.

        반환: {"L": adv, "C": adv, "R": adv}
          adv = (그 레인·상대 진영에 있는 우리 팀 수)
              - (그 레인·자기 진영에 있는 상대 수비 수)
          즉 순수 카운트 기반의 가벼운 실시간 스냅샷이며,
          tactical_engine처럼 스탯까지 반영한 정밀 계산은 아니다
          (매 패스 판정마다 도는 함수라 가벼워야 함).
        """
        team = self.home_players if side == "home" else self.away_players
        opp = self.away_players if side == "home" else self.home_players
        sign = 1 if side == "home" else -1

        def _lane_of(y):
            if y < 0.34:
                return "L"
            if y > 0.67:
                return "R"
            return "C"

        adv = {"L": 0, "C": 0, "R": 0}
        for p in team:
            if p["pos"] == "GK":
                continue
            if (p["x"] - 0.5) * sign > 0:   # 상대 진영(전진)에 있는 우리 선수만
                adv[_lane_of(p["y"])] += 1
        for p in opp:
            if p["pos"] == "GK":
                continue
            if (p["x"] - 0.5) * sign > 0:   # 같은 구역(우리 전진 진영)에 내려와 있는 상대 수비
                adv[_lane_of(p["y"])] -= 1
        return adv

    def _try_pull_forward_scheduled_event(self, side, holder_x, holder_y):
        """[신규 — 이벤트 당김] 오픈플레이가 실제로 상대 박스 근처까지
        만들어낸 '찬스'를, 그 상황과 무관하게 예정된 시각까지 그냥
        기다리는 대신 최대한 자연스럽게 이어붙인다.

        절대 새 슛/코너/파울을 만들어내지 않는다 — tactical_engine이
        이미 확정해둔 이번 경기 전체 슛/골/코너/파울 개수(team_stats)는
        한 글자도 안 바뀐다. self._plog(뷰어 로컬 사본, DB에 저장된
        원본 possession_log와 무관)에 이미 있는, 아직 처리 안 된 그
        팀의 다음 예정 이벤트 하나를 찾아서 "몇 분에 터지는가"라는
        타이밍만 지금 이 순간으로 당겨올 뿐이다. 룩어헤드 안에 당길
        게 없으면 아무 것도 만들지 않고 그냥 오픈플레이가 계속된다.
        """
        _opp_goal_x = 1.0 if side == "home" else 0.0
        # [조정] 처음엔 골문 정면 폭(_GOAL_HALF_HEIGHT*2.5)으로 좁게
        # 잡았더니 실측(합성 5경기, 총 570틱)에서 x조건조차 1%(114번 중
        # 1번)만 걸렸다 — 사실상 발동 안 하는 셈이라 무의미했다. 필러
        # 슛 자체가 쓰는 "일반 슈팅 거리" 기준(_SHOT_ZONE_NORMAL)과
        # 그보다 조금 넓은 각도 기준으로 맞춰서, 실제로 "슈팅할 만한
        # 위치"에 들어왔을 때 의미 있는 빈도로 작동하게 한다.
        _in_chance_area = (abs(holder_x - _opp_goal_x) < _SHOT_ZONE_NORMAL
                            and abs(holder_y - 0.5) < _GOAL_HALF_HEIGHT * 4.0)
        if not _in_chance_area:
            return
        _LOOKAHEAD_MIN = 2.5
        _best = None
        for _pr in self._plog:
            if _pr["done"] or _pr["team"] != side or _pr["outcome"] == "buildup":
                continue
            if _pr["min"] <= self.clock:
                continue  # 이미 처리 대상 -- 일반 plog 루프가 다음 틱에 알아서 처리
            if _pr["min"] - self.clock > _LOOKAHEAD_MIN:
                continue
            if _best is None or _pr["min"] < _best["min"]:
                _best = _pr
        if _best is not None:
            _best["min"] = self.clock

    def _do_pass_or_turnover(self):
        # [재설계 핵심] 다른 재개(파울/코너킥/스로인/골킥/골라인아웃/필러슛
        # 결과처리)가 진행 중이면 일반 패스/턴오버 판정 자체를 아예
        # 안 돌린다. 예전엔 이 함수가 그런 상태와 무관하게 항상 돌아서,
        # 재개 시퀀스 사이 짧은 틈에 오픈플레이가 끼어들어 재개를 밀어내
        # 버리는 충돌이 반복됐다 — 이제 이 한 줄이 그 충돌 클래스 전체를
        # 막는다. (실제로는 _advance_one_tick의 분기 자체가 데드볼 중엔
        # 이 함수를 아예 안 부르므로 이 체크는 이중 안전장치다.)
        if self._db is not None:
            return
        side = self.possession
        team = self.home_players if side == "home" else self.away_players
        opp_team = self.away_players if side == "home" else self.home_players
        sign = 1 if side == "home" else -1
        holder_x = team[self.holder]["x"]
        holder_y = team[self.holder]["y"]
        start_xy = (self.ball["x"], self.ball["y"])

        # [신규 — 이벤트 당김] 여기서 판정만 하고(다음 틱에 실제 처리),
        # 아래 패스/턴오버 로직은 평소대로 계속 진행한다 — 당겨졌든
        # 아니든 이번 틱의 오픈플레이 흐름 자체는 끊기지 않아야 자연스럽다.
        self._try_pull_forward_scheduled_event(side, holder_x, holder_y)

        # [신규 — 스로인] 실제 축구는 공이 터치라인 밖으로 자주 나가서
        # 스로인으로 재개되는데, 지금까진 y좌표가 항상 0.05~0.95로
        # 클램프돼서 "밖으로 나간다"는 개념 자체가 없었다. 홀더가 터치라인
        # 가까이서 드리블하고 있으면 일정 확률로 공이 그대로 라인을 넘어가
        # 상대팀 스로인이 된다. 요청대로 배너 문구는 안 띄운다 — 공이
        # 라인 바로 위에 잠깐 멈췄다가 가까운 동료에게 낮고 짧게(코너킥
        # 크로스의 아치와는 다르게) 날아드는 움직임 자체가 스로인임을
        # 보여준다.
        # [주의] 실제 렌더링 좌표계의 터치라인은 y=0.05/0.95다(모든 위치가
        # 이 범위로 클램프됨). 윙어/풀백의 홈 y도 폭이 넓어야 0.14/0.86
        # 수준이라, 기준을 0/1 대신 0.05/0.95로 잡아야 실제로 도달 가능한
        # 값이 된다.
        _line_dist = min(holder_y - 0.05, 0.95 - holder_y)
        if (_line_dist < 0.13 and random.random() < 0.22
                and self.clock - self._last_throwin_clock > 1.5):
            self._enter_dead_ball("throw_in", out_side=side, x=holder_x, y=holder_y)
            return

        # [버그 수정 — 근본 원인] 터치라인(y)엔 스로인 체크가 있었지만
        # 골라인(x)엔 아무 체크도 없었다. 그래서 드리블/패스가 우연히
        # 골문 폭 바깥으로 골라인 근처까지 흘러가도, 그 사실을 감지해서
        # 멈추는 로직이 전혀 없어 그냥 클램프된 채로 오픈플레이가 계속
        # 이어졌다 — 실측 디버그 캡처에서 홀더가 골라인 코앞(x<0.02)까지
        # 간 뒤에도 아무 판정 없이 진행되다가, 우연히 타이밍이 맞은 다른
        # 예정 이벤트(코너킥 등)가 뒤늦게 겹쳐서야 정리되는 게 그대로
        # 재현됐다("좌표를 전혀 못 잡는다"는 지적의 정확한 원인). 골대
        # 폭 바깥에서 홀더가 골라인에 바짝 붙으면, 어느 쪽 골라인이냐에
        # 따라 상대 골킥(내가 공격하다 흘림) 또는 상대 코너킥(내가 수비
        # 하다 자책성으로 흘림)으로 정리한다.
        _own_goal_x = 0.0 if side == "home" else 1.0
        _opp_goal_x = 1.0 if side == "home" else 0.0
        _outside_goal_mouth = abs(holder_y - 0.5) > _GOAL_HALF_HEIGHT * 1.4
        if (_outside_goal_mouth and self.clock - self._last_byline_clock > 1.5
                and random.random() < 0.22):
            _dist_to_opp_byline = abs(_opp_goal_x - holder_x)
            _dist_to_own_byline = abs(_own_goal_x - holder_x)
            if _dist_to_opp_byline < 0.07:
                # 공격 중 골라인 밖으로 흘림(빗나간 크로스/드리블) → 상대 골킥
                self._last_byline_clock = self.clock
                self._enter_dead_ball("goal_kick", side=("away" if side == "home" else "home"))
                return
            if _dist_to_own_byline < 0.07:
                # 수비 중 자기 골라인 밖으로 흘림(클리어링 실패) → 상대 코너킥
                self._last_byline_clock = self.clock
                self._enter_dead_ball("corner", side=("away" if side == "home" else "home"))
                return

        my_team_side = "home" if self.is_home else "away"
        is_my_team = (side == my_team_side)

        TURNOVER_CHANCE = 0.22
        # [신규] 팀 통계(team_stats)에 기록된 점유율에 맞춰 기본 턴오버
        # 확률을 보정한다. 예전엔 두 팀이 거의 같은 기본값을 써서 통계상
        # "점유율 63%-37%"라고 떠도 재생 화면에서는 거의 50:50으로 주고
        # 받는 것처럼 보였다 — 이제 점유율이 높은 팀일수록 공을 더 오래
        # 갖고(턴오버 확률↓), 낮은 팀은 반대로 더 자주 빼앗기게 한다.
        if self.team_stats:
            poss_pct = self.team_stats.get(side, {}).get("poss")
            if poss_pct is not None:
                TURNOVER_CHANCE = max(0.08, min(0.45,
                    TURNOVER_CHANCE - (poss_pct - 50) / 100 * 0.9))
        if is_my_team and self.holder == self.my_slot:
            # [세부지표 반영] 내 패스 성공률이 높을수록 공을 뺏길 확률이
            # 낮아진다(반대로 낮으면 더 자주 뺏김). 72%(평균 근사치)를
            # 기준으로 삼아 그보다 높고 낮음에 비례해 가감한다. 위에서 이미
            # 팀 점유율로 보정된 기준값에 추가로 얹는다.
            pass_acc = self.detail.get("pass_acc") or 0.72
            TURNOVER_CHANCE = max(0.06, min(0.5, TURNOVER_CHANCE - (pass_acc - 0.72) * 0.5))
        else:
            # [신규 — 로스터 스탯 반영] 나 말고 나머지 홀더 21명도 지금까진
            # 완전히 똑같은 확률로 공을 지켰다(포지션·개인 능력과 무관).
            # 홀더의 dribbling 스탯이 높을수록(50이 평균) 덜 뺏기게 한다.
            _dribbling = team[self.holder].get("stats", {}).get("dribbling", 50)
            TURNOVER_CHANCE = max(0.06, min(0.5,
                TURNOVER_CHANCE - (_dribbling - 50) / 100 * 0.3))
        # [신규 — 로스터 스탯 반영] 압박 중인 수비수(presser)의 tackling
        # 스탯이 높을수록 실제로 더 잘 뺏어야 한다 — 예전엔 거리만 보고
        # 확률이 정해져서, 수비력 약한 선수나 강한 선수나 압박 성공률이
        # 완전히 같았다.
        if self._presser_idx is not None:
            _opp_for_press = self.away_players if side == "home" else self.home_players
            if self._presser_idx < len(_opp_for_press):
                _tackling = _opp_for_press[self._presser_idx].get("stats", {}).get("tackling", 50)
                TURNOVER_CHANCE = max(0.05, min(0.55,
                    TURNOVER_CHANCE + (_tackling - 50) / 100 * 0.25))

        if random.random() < TURNOVER_CHANCE:
            # 상대에게 턴오버 — 공 근처(수비 라인 포함) 상대 선수가 인터셉트
            opp_side = "away" if side == "home" else "home"
            # [버그 수정] 예전엔 매번 새로 "홀더와 가장 가까운 상대 선수"를
            # 다시 계산해서 인터셉트 주체로 썼다. 근데 그 선수가 화면상
            # 실제로 압박해오던 선수(presser)와 다를 수 있어서, "누가 다가와
            # 차단하는" 그림 없이 갑자기 엉뚱한 곳에 있던 선수한테 공이
            # 순간이동하는 것처럼 보였다(캡처된 화면 그대로). presser는
            # 이미 몇 틱 전부터 볼 쪽으로 계속 다가가고 있던 선수이므로,
            # 그 선수가 여전히 충분히 가까우면(=실제로 따라붙은 상태) 그
            # 선수가 인터셉트하게 하고, 너무 멀면(아직 못 따라붙었으면)만
            # 예외적으로 가장 가까운 선수로 대체한다.
            if (self._presser_idx is not None
                    and abs(opp_team[self._presser_idx]["x"] - self.ball["x"])
                    + abs(opp_team[self._presser_idx]["y"] - self.ball["y"]) < 0.18):
                new_holder = self._presser_idx
            else:
                new_holder = min(
                    range(len(opp_team)),
                    key=lambda i: abs(opp_team[i]["x"] - holder_x) + abs(opp_team[i]["y"] - self.ball["y"]))
            # [신규 — 죽어있던 지표 살리기] detail["blocks"](내 수비 기여
            # 기록)가 지금까지 완전히 버려져 있었다. 공격 쪽은 key_passes/
            # dribbles/shots가 높으면 내가 볼에 자주 관여하게 가중치를 주는데
            # (아래 참고), 수비 쪽엔 그런 게 하나도 없어서 내가 실제로
            # 수비형 포지션에 활약이 많아도 화면에서는 전혀 티가 안 났다.
            # 지금 수비 중인 팀이 내 팀이고, 나(my_slot)도 공과 충분히
            # 가까이 있으면, blocks 기록에 비례해서 이 인터셉트를 내가
            # 직접 해낸 것으로 바꿔치기할 확률을 준다.
            my_team_side = "home" if self.is_home else "away"
            if opp_side == my_team_side and self.my_slot != new_holder:
                my_dist = (abs(opp_team[self.my_slot]["x"] - self.ball["x"])
                          + abs(opp_team[self.my_slot]["y"] - self.ball["y"]))
                if my_dist < 0.20:
                    steal_chance = min(0.55, self.detail.get("blocks", 0) * 0.05)
                    if steal_chance > 0 and random.random() < steal_chance:
                        new_holder = self.my_slot
            self.possession = opp_side
            self.holder = new_holder
            self._assign_roles()
            # [신규] 턴오버 = 경기에서 가장 급박한 순간. 이 타이머를 채워
            # 두면 _update_player_positions가 몇 틱에 걸쳐 서서히 감쇠시키며
            # 양팀 전원의 최고속도를 잠깐 끌어올린다(뺏은 팀은 역습 질주,
            # 뺏긴 팀은 전력 복귀하는 것처럼 보이게).
            self._transition_timer = self._TRANSITION_DURATION
            self._start_pass_flight(start_xy, (opp_team[new_holder]["x"], opp_team[new_holder]["y"]))
            return

        # 전진 방향 패스: 현재 위치보다 앞선(공격 방향) 동료를 우선 후보로,
        # 없으면 아무 동료에게(백패스/횡패스)라도 연결
        candidates = [i for i in range(len(team)) if i != self.holder]
        if not candidates:
            return
        # [버그 수정] 예전엔 후보를 거리 상관없이 아무나 골랐다. 그래서
        # 가끔 자기 진영 깊숙한 곳에 있는 홀더가 상대 골대 근처 최전방
        # 선수에게 "패스"하는, 피치를 거의 다 가로지르는 비현실적인 대각선
        # 패스가 나왔다(사용자가 캡처한 화면의 긴 대각선이 이 경우). 실제
        # 축구는 대부분 근~중거리 패스이므로, 홀더와 가까운 후보들만 남기고
        # (그런 후보가 아예 없을 때만 예외적으로 전체 허용) 고른다.
        # [현실성 보정] 0.42는 중원과 최전방 격차가 벌어지는 상황(방금
        # 후퇴폭 조정으로 격차 자체는 줄였지만 여전히 벌어질 수 있음)에서
        # 실제로 뚫려 있는 전진 패스 상대까지도 걸러버려서, "완전히
        # 뚫렸는데 전진 패스가 하나도 안 잡혀 뒤로/옆으로만 돈다"는
        # 지적의 원인이 됐다. 0.55로 늘려서 스루패스 수준의 진짜 전진
        # 옵션은 잡히게 하면서도, 피치를 통째로 가로지르는 수준의
        # 비현실적인 패스는 여전히 걸러진다.
        _MAX_PASS_DIST = 0.55
        near_candidates = [
            i for i in candidates
            if abs(team[i]["x"] - holder_x) + abs(team[i]["y"] - self.ball["y"]) <= _MAX_PASS_DIST]
        candidates = near_candidates or candidates
        forward = [i for i in candidates if (team[i]["x"] - holder_x) * sign > 0.02]
        pool = forward if (forward and random.random() < 0.75) else candidates

        # [세부지표 반영] 키패스·드리블·슈팅이 많을수록(=그 경기에서 활약도가
        # 높을수록) 다음 홀더로 내가 뽑힐 확률에 가중치를 준다 — 실제 기록만큼
        # 화면에서도 공에 자주 관여하는 것처럼 보이게.
        if is_my_team and self.my_slot in pool:
            involvement = (self.detail.get("key_passes", 0) + self.detail.get("dribbles", 0)
                          + self.detail.get("shots", 0))
            extra = min(3, involvement // 3)
            if extra:
                pool = pool + [self.my_slot] * int(extra)

        new_holder = random.choice(pool)
        # [버그 수정] 예전엔 pool 안에서 완전히 균등 확률로 뽑았다. 그래서
        # 홀더가 상대 진영 깊숙이 있어서(=자기보다 앞선 동료가 없어서)
        # forward 후보가 비어버리면, 가까운 동료든 0.42만큼 멀리 뒤처진
        # 동료든 똑같은 확률로 뽑혀서 "공격수가 갑자기 저 뒤로 공을
        # 던지는" 부자연스러운 장면이 나왔다(지적된 그대로). 거리가
        # 가까울수록 더 잘 뽑히도록 가중치를 줘서, 뒤로 갈 땐 그나마
        # 가까운 동료 위주로 짧게 내주도록 했다.
        if len(pool) > 1:
            dists = [abs(team[i]["x"] - holder_x) + abs(team[i]["y"] - self.ball["y"])
                     for i in pool]
            weights = [1.0 / (0.10 + d) for d in dists]
            # [신규 — 진짜 레인 반영] 전술 엔진이 "이 순간 실제로 이
            # 레인이 우세했다"고 계산해둔 힌트(self._target_lane_y)가
            # 있으면, 그 레인에 가까운 동료를 살짝 더 선호한다 — 포메이션
            # 매치업 계산이 화면에서도 "그 쪽으로 공이 몰린다"는 그림으로
            # 실제로 이어지게 하는 부분. 거리 기반 가중치를 뒤집는 게
            # 아니라 그 위에 완만하게 얹는 정도(과하면 그냥 레인 안에서
            # 핑퐁하는 부자연스러움이 생기므로).
            _lane_w = [1.0 / (0.12 + abs(team[i]["y"] - self._target_lane_y)) for i in pool]
            weights = [w * (lw ** 1.6) for w, lw in zip(weights, _lane_w)]

            # [신규 — 실시간 공간평가] 위 _target_lane_y는 "그 분에" 어느
            # 레인이 우세했는지에 대한 사후 힌트 하나뿐이라, 조용한 시간
            # (그 분에 예정된 이벤트가 없는 틱)엔 아무 방향성이 없었다
            # ("CM↔ST를 왕복하며 진전이 없다"는 지적의 원인). 지금 이
            # 순간 실제 좌표 기준 수적 우위가 있는 레인 쪽을 추가로
            # 선호하게 해서, 조용한 시간에도 "그 쪽이 뚫려서 공이
            # 몰린다"는 목적성 있는 흐름을 만든다.
            _lane_adv = self._evaluate_lane_advantage(side)
            _best_lane = max(_lane_adv, key=_lane_adv.get)
            if _lane_adv[_best_lane] > 0:
                _lane_center = {"L": 0.18, "C": 0.5, "R": 0.82}[_best_lane]
                _space_w = [1.0 / (0.14 + abs(team[i]["y"] - _lane_center)) for i in pool]
                weights = [w * (sw ** 1.3) for w, sw in zip(weights, _space_w)]

            # [신규 — 침투런 우선순위] _breakthrough_marks(라인을 이미
            # 돌파해 뒷공간에 있는 동료 → 마크 중인 수비수)는 지금까지
            # 수비 마킹에만 쓰이고 공격 쪽 패스 판정엔 전혀 재사용되지
            # 않았다. 뒷공간에 있는 동료가 전진 후보 풀에 있으면 스루
            # 패스처럼 우선 연결되도록 크게 가중치를 준다.
            _breakers = set(self._breakthrough_marks.values())
            if _breakers:
                weights = [w * (4.0 if i in _breakers else 1.0)
                           for w, i in zip(weights, pool)]

            # [버그 수정 — 근본 원인] 홀더가 이미 상대 진영 깊숙이
            # 들어와 있어도(예: 상대 수비 라인 코앞의 ST), 후보 가중치가
            # 순수 거리 기반이라 근처에 전진 옵션이 없으면 그냥 가장
            # "가까운" 후보를 골랐다 — 그런데 그 팀 나머지 대다수(CB/
            # CDM/CM)가 원래 훨씬 뒤에 자리하고 있으므로, "가장 가까운"
            # 후보조차 홀더 기준으로는 한참 처진 동료인 경우가 흔했다.
            # 그 결과 애써 만든 전진 위치를 스스로 반납하는 패스가
            # 나왔다("전진해 있는 ST가 왜 뒤로 패스하냐"는 지적 그대로).
            # 홀더가 확실히 상대 진영에 들어와 있을 때만, 자기보다 많이
            # (0.25 이상) 처진 후보에게 페널티를 줘서 완전 포기성
            # 백패스 빈도를 낮춘다 — 짧은 안전 백패스(템포 조절)는
            # 페널티 없이 그대로 허용한다.
            _holder_adv = (holder_x - 0.5) * sign
            if _holder_adv > 0.15:
                _back_pen = [0.35 if (team[i]["x"] - holder_x) * sign < -0.25 else 1.0
                             for i in pool]
                weights = [w * bp for w, bp in zip(weights, _back_pen)]

            new_holder = random.choices(pool, weights=weights, k=1)[0]
        self.holder = new_holder
        self._assign_roles()
        self._start_pass_flight(start_xy, (team[new_holder]["x"], team[new_holder]["y"]))

    def _start_scene(self, e):
        # [결정론적 재생] 예전엔 씬 구성(누가 크로스를 올리는지, 박스에
        # 누가 들어가는지 등)에 random.choice/shuffle을 그대로 썼다. 그래서
        # 재생바로 같은 골 장면을 여러 번 돌려봐도 매번 다른 상황이
        # 나왔다 — "다시 보기"가 아니라 "매번 새로 만들어지는" 느낌이었다.
        # 이벤트 자체(분+문구)로 시드를 고정해서, 같은 이벤트는 몇 번을
        # 다시 봐도 항상 똑같은 장면이 재생되도록 했다.
        _rng_state = random.getstate()
        random.seed(_stable_seed(round(e["minute"] * 100), e["text"]))
        try:
            self._start_scene_body(e)
        finally:
            random.setstate(_rng_state)

    def _start_scene_body(self, e):
        # [검증용 계측] 씬 시작 시 크로서/키커/수비벽을 강제 위치로 스냅
        # (_scene_force_start, _scene_crowd)하는데, 이 시점 배너는 아직
        # ""(공백)이라 배너 텍스트로는 이 스냅을 식별할 수 없었다. 헤드리스
        # 검증이 오탐 없이 정확히 판별하도록 여기서도 기록해둔다.
        self._last_restart_clock = self.clock
        # 공격측: goal_for/miss_for(=내가 관여한 이벤트라서 항상 내팀 공격),
        # goal_against/save는 상대 공격
        my_side = "home" if self.is_home else "away"
        opp_side = "away" if self.is_home else "home"
        self._scene_side = my_side if e["kind"] in ("goal_for", "miss_for") else opp_side
        self._scene_kind = e["kind"]
        self._scene_style = e.get("style", "normal")
        self._scene_progress = 0.0
        self._scene_event_text = e["text"]
        self.banner_text = ""
        self.banner_alpha = 0
        self._scene_ball_start = dict(self.ball)  # 기본값: 빌드업 종료 위치
        # [버그 수정] 코너킥/크로스는 공 시작점을 코너 플래그·바이라인 쪽
        # 특수 좌표로 강제 고정하는데(아래), 정작 그 공을 차는 선수(크로서)
        # 는 씬 시작 시점의 "실제 평소 위치"(대개 코너와는 거리가 먼 곳)에
        # 그대로 남아 있었다. 그래서 공은 코너에서 뚝 튀어나오는데 정작
        # 거기엔 아무도 없는 것처럼 보였다(캡처 화면 그대로). 이 딕셔너리에
        # {선수idx: (x,y)}를 채워두면, 아래 공통 코드에서 그 선수의 씬
        # 시작 위치를 공 시작점과 맞춰 강제로 옮겨준다.
        self._scene_force_start = {}
        # [신규] 세트피스(코너킥) 전용 — 박스 주변으로 몰려드는 "군중"
        # 선수들의 목표 좌표. 기본은 비워두고(=일반 씬에선 아무도 안 몰림),
        # 세트피스 분기에서만 채운다.
        self._scene_crowd = {}
        atk_goal_x = 1.0 if self._scene_side == "home" else 0.0

        atk_team = self.home_players if self._scene_side == "home" else self.away_players
        def_team = self.away_players if self._scene_side == "home" else self.home_players
        my_team_is_atk = self._scene_side == ("home" if self.is_home else "away")
        style = self._scene_style

        # ── [텍스트-영상 싱크] 실제 이벤트 문구 스타일별 분기 ──────────
        if style in ("penalty", "penalty_miss") and my_team_is_atk:
            # PK는 이벤트 당사자(나)가 항상 키커 — 박스는 비우고 키커 vs GK
            # 1:1만 보여준다(다른 선수는 관여 안 함, 실제 PK 장면과 동일).
            atk_idx = [self.my_slot]
            def_idx = [i for i, pl in enumerate(def_team) if pl["pos"] == "GK"]
            spot_x = 0.83 if atk_goal_x == 1.0 else 0.17
            self._scene_ball_start = {"x": spot_x, "y": 0.5}
            # [버그 수정] 키커/GK를 뺀 나머지 20명은 이 씬에 아예 포함이
            # 안 돼서, 평소 오픈플레이 포메이션 공식을 계속 따라가느라
            # 킥 순간에도 다들 엉뚱한 자리(예: 자기 골키퍼 옆)에 흩어져
            # 있었다("현실적이지 않다"는 지적 그대로). 실제 PK처럼 다들
            # 박스·아크 밖으로 빠져 나가는 대형을 잡는다 — 포지션별
            # 역할(세컨볼 침투조/역습 대비조, 블루 차단조/카운터 대기조)
            # 까지 반영한다.
            _atk_others = [i for i, pl in enumerate(atk_team)
                           if i != self.my_slot and pl["pos"] != "GK"]
            _def_others = [i for i, pl in enumerate(def_team) if pl["pos"] != "GK"]
            _atk_pts, _def_pts = _penalty_arc_slots(
                atk_goal_x, spot_x, atk_team, _atk_others, def_team, _def_others)
            for i, (jx, jy) in _atk_pts.items():
                self._scene_crowd[("atk", i)] = (jx, jy)
            for i, (jx, jy) in _def_pts.items():
                self._scene_crowd[("def", i)] = (jx, jy)

        elif style in ("penalty", "penalty_miss") and not my_team_is_atk:
            # [버그 수정] 위 분기는 "우리 팀이 얻은 PK"만 다뤄서, "상대 팀
            # PK"로 실점(goal_against)하거나 우리 GK가 막는(save) 경우는
            # 이 조건(my_team_is_atk)에 안 걸려 그냥 오픈플레이 기본
            # 분기로 떨어졌다 — 그러면 스팟킥 특유의 구도(키커 vs GK
            # 1:1, 나머지 전원 박스 밖)가 전혀 안 나오고 일반 슛처럼
            # 보였다. 키커만 특정 못 할 뿐(득점자 포지션 정보가 없음)
            # 나머지 구도는 동일하게 적용한다.
            fin_pool = [i for i, pl in enumerate(atk_team) if pl["pos"] in _ATTACK_ROLES]
            taker = self.holder if 0 <= self.holder < len(atk_team) else (fin_pool[0] if fin_pool else 0)
            atk_idx = [taker]
            def_idx = [i for i, pl in enumerate(def_team) if pl["pos"] == "GK"]
            spot_x = 0.83 if atk_goal_x == 1.0 else 0.17
            self._scene_ball_start = {"x": spot_x, "y": 0.5}
            _atk_others = [i for i, pl in enumerate(atk_team)
                           if i != taker and pl["pos"] != "GK"]
            _def_others = [i for i, pl in enumerate(def_team) if pl["pos"] != "GK"]
            _atk_pts, _def_pts = _penalty_arc_slots(
                atk_goal_x, spot_x, atk_team, _atk_others, def_team, _def_others)
            for i, (jx, jy) in _atk_pts.items():
                self._scene_crowd[("atk", i)] = (jx, jy)
            for i, (jx, jy) in _def_pts.items():
                self._scene_crowd[("def", i)] = (jx, jy)

        elif style == "setpiece" and my_team_is_atk:
            # [버그 수정] 예전엔 ATTACK_ROLES(ST/CF/WG/CAM)만 씬에 넣어서,
            # 실제로 게임 엔진이 CB/LB/RB의 코너킥 헤더골로 만든 이벤트인데도
            # 정작 득점자인 '나'는 화면에 안 나타나는 문제가 있었다. 세트피스
            # 골의 마무리는 항상 이벤트 당사자(나)이므로 포지션과 무관하게
            # 반드시 포함시킨다. 크로스를 올리는 측면 자원을 따로 정해서,
            # 그 위치(코너 플래그 근처)에서 공이 출발해 박스로 대각선으로
            # 휘어져 들어가는 궤적을 만든다.
            wide_pool = [i for i, pl in enumerate(atk_team)
                         if pl["pos"] in ("LW", "RW", "LB", "RB", "LWB", "RWB")
                         and i != self.my_slot]
            # [버그 수정] 예전엔 크로서를 완전히 무작위로 뽑아서, 방금까지
            # 화면에서 공을 몰던 선수와 전혀 다른 사람이 뜬금없이 코너에서
            # 나타나는 것처럼 보였다("왜 갑자기 이 선수가 차?"). 지금
            # 홀더(직전까지 실제로 공을 갖고 있던 선수)가 측면 자원이면
            # 그 사람을 그대로 크로서로 쓴다 — 연속성이 생긴다.
            if self.holder in wide_pool:
                crosser = self.holder
            else:
                crosser = random.choice(wide_pool) if wide_pool else None
            support_pool = [i for i, pl in enumerate(atk_team)
                            if pl["pos"] in _ATTACK_ROLES and i != self.my_slot]
            random.shuffle(support_pool)
            atk_idx = [self.my_slot] + support_pool[:1]
            if crosser is not None and crosser not in atk_idx:
                atk_idx.append(crosser)
            atk_idx = atk_idx[:3]
            if crosser is not None:
                # [버그 수정] 예전엔 크로서의 "포메이션 홈 슬롯"(hx/hy)을 그대로
                # 크로스 시작점으로 썼다. 문제는 LB/RB의 홈 슬롯이 자기
                # 진영 깊숙한 곳(약 0.18~0.24)이라, "코너킥"이라면서 실제로는
                # 자기 골대 근처에서 상대 골대까지 피치를 거의 다 가로지르는
                # 크로스가 나왔다(사용자가 캡처한 화면의 그 긴 대각선이 바로
                # 이 경우). 코너킥은 항상 상대 진영 코너 플래그 근처에서
                # 올라오므로, 크로서의 원래 포지션(왼쪽/오른쪽)만 참고해서
                # 실제 코너 위치로 고정한다.
                crosser_pos = atk_team[crosser]["pos"]
                corner_y = 0.04 if crosser_pos in ("LW", "LB", "LWB") else 0.96
                corner_x = 0.95 if atk_goal_x == 1.0 else 0.05
                self._scene_ball_start = {"x": corner_x, "y": corner_y}
                # [버그 수정] 크로서의 실제 직전 위치에서 코너 쪽으로 70%만
                # 블렌드한다(완전히 코너로 순간이동시키지 않음). 예전엔
                # 100% 스냅이라 "끝에서 끝으로" 순간이동한 것처럼 보였다.
                # 위에서 홀더 연속성을 이미 확보했으니, 여기선 남은 거리를
                # 자연스럽게 좁혀주는 정도로만 보정한다.
                _cross_now_x, _cross_now_y = atk_team[crosser]["x"], atk_team[crosser]["y"]
                _blend = 0.7
                _fx = _cross_now_x + (corner_x - _cross_now_x) * _blend
                _fy = _cross_now_y + (corner_y - _cross_now_y) * _blend
                self._scene_force_start[crosser] = (
                    max(0.01, min(0.99, _fx)), max(0.01, min(0.99, _fy)))
            else:
                corner_y = 0.5  # [버그 수정] 측면 자원이 아예 없는 예외 케이스 대비 기본값
            def_idx = [i for i, pl in enumerate(def_team) if pl["pos"] == "GK"]
            cb_idx = [i for i, pl in enumerate(def_team) if pl["pos"] in ("CB", "LB", "RB")]
            if cb_idx:
                def_idx.append(random.choice(cb_idx))

            # [신규] 코너킥은 실제로는 거의 전원이 박스 안팎으로 몰린다.
            # 지금까지는 딱 3명(공격)+2명(수비)만 씬에 포함되고 나머지는
            # 화면이 멈춰 있는 동안(_update_possession이 안 돌아서) 평소
            # 진영에 흩어진 채 그대로 정지해 있었다("다들 뭉쳐있어야 하는데
            # 하나도 안 뭉쳤다"는 지적 그대로). GK를 제외한 나머지 선수
            # 대부분을 박스 주변으로 몰아넣는다(전부는 아니고, 일부는 역습
            # 대비로 하프라인 쪽에 남겨서 완전히 부자연스럽게 11명이 다
            # 몰리진 않게 한다).
            _crowd_atk = [i for i, pl in enumerate(atk_team)
                          if pl["pos"] != "GK" and i not in atk_idx]
            _crowd_def = [i for i, pl in enumerate(def_team)
                          if pl["pos"] != "GK" and i not in def_idx]
            random.shuffle(_crowd_atk)
            random.shuffle(_crowd_def)
            # [버그 수정] 예전엔 5명/6명 캡을 넘는 인원은 그냥 박스로 안
            # 왔고, 좌표도 random.uniform 완전 무작위였다("수비수들이 안
            # 움직인다"/"너무 랜덤하다"는 지적 그대로). 역습 대비로 1명씩만
            # 남기고 나머지는 전부, 니어포스트/식스야드/파포스트/박스
            # 엣지 같은 실전 슬롯에 채워 넣는다.
            _atk_stay = _crowd_atk[-1:] if len(_crowd_atk) > 3 else []
            _def_stay = _crowd_def[-1:] if len(_crowd_def) > 3 else []
            _crowd_atk = [i for i in _crowd_atk if i not in _atk_stay]
            _crowd_def = [i for i in _crowd_def if i not in _def_stay]
            _atk_pts, _def_pts = _corner_slots(atk_goal_x, corner_y, len(_crowd_atk), len(_crowd_def))
            for i, (jx, jy) in zip(_crowd_atk, _atk_pts):
                self._scene_crowd[("atk", i)] = (jx, jy)
            for i, (jx, jy) in zip(_crowd_def, _def_pts):
                self._scene_crowd[("def", i)] = (jx, jy)

        elif style == "setpiece" and not my_team_is_atk:
            # [버그 수정] 위 분기는 "우리 팀이 얻은 코너킥"만 다뤄서, "상대
            # 팀 코너킥"으로 실점하거나(goal_against) 우리 GK가 막는(save)
            # 경우는 이 조건(my_team_is_atk)에 안 걸려 그냥 아래 오픈플레이
            # 기본 분기로 떨어졌다. 그러면 텍스트는 분명 "코너킥"인데 정작
            # 공은 코너 플래그가 아니라 박스 언저리 아무 데서나 시작해서
            # "코너킥이라면서 그냥 슛 장면"처럼 보였다. 위와 동일한 코너
            # 플래그·크라우드 로직을 상대 관점으로 그대로 적용한다.
            wide_pool = [i for i, pl in enumerate(atk_team)
                         if pl["pos"] in ("LW", "RW", "LB", "RB", "LWB", "RWB")]
            crosser = (self.holder if self.holder in wide_pool
                       else (random.choice(wide_pool) if wide_pool else None))
            fin_pool = [i for i, pl in enumerate(atk_team)
                        if pl["pos"] in _ATTACK_ROLES and i != crosser]
            atk_idx = ([crosser] if crosser is not None else [])
            atk_idx += random.sample(fin_pool, min(2, len(fin_pool))) if fin_pool else []
            atk_idx = atk_idx[:3]
            if crosser is not None:
                crosser_pos = atk_team[crosser]["pos"]
                corner_y = 0.04 if crosser_pos in ("LW", "LB", "LWB") else 0.96
                corner_x = 0.95 if atk_goal_x == 1.0 else 0.05
                self._scene_ball_start = {"x": corner_x, "y": corner_y}
                _cx0, _cy0 = atk_team[crosser]["x"], atk_team[crosser]["y"]
                _blend = 0.7
                self._scene_force_start[crosser] = (
                    max(0.01, min(0.99, _cx0 + (corner_x - _cx0) * _blend)),
                    max(0.01, min(0.99, _cy0 + (corner_y - _cy0) * _blend)))
            else:
                corner_y = 0.5  # [버그 수정] 측면 자원이 아예 없는 예외 케이스 대비 기본값
            def_idx = [i for i, pl in enumerate(def_team) if pl["pos"] == "GK"]
            cb_idx = [i for i, pl in enumerate(def_team) if pl["pos"] in ("CB", "LB", "RB")]
            if cb_idx:
                def_idx.append(random.choice(cb_idx))
            _crowd_atk = [i for i, pl in enumerate(atk_team)
                          if pl["pos"] != "GK" and i not in atk_idx]
            _crowd_def = [i for i, pl in enumerate(def_team)
                          if pl["pos"] != "GK" and i not in def_idx]
            random.shuffle(_crowd_atk)
            random.shuffle(_crowd_def)
            _atk_stay = _crowd_atk[-1:] if len(_crowd_atk) > 3 else []
            _def_stay = _crowd_def[-1:] if len(_crowd_def) > 3 else []
            _crowd_atk = [i for i in _crowd_atk if i not in _atk_stay]
            _crowd_def = [i for i in _crowd_def if i not in _def_stay]
            _atk_pts, _def_pts = _corner_slots(atk_goal_x, corner_y, len(_crowd_atk), len(_crowd_def))
            for i, (jx, jy) in zip(_crowd_atk, _atk_pts):
                self._scene_crowd[("atk", i)] = (jx, jy)
            for i, (jx, jy) in zip(_crowd_def, _def_pts):
                self._scene_crowd[("def", i)] = (jx, jy)

        elif style == "freekick" and my_team_is_atk:
            # [신규] 직접 프리킥. 예전엔 "세트피스"가 전부 코너킥(코너
            # 플래그) 취급이라 직접 프리킥도 코너에서 차는 것처럼 보였다.
            # 실제 직접 프리킥은 박스 바로 앞(반원 지점)에서 상대 수비벽을
            # 마주보고 차는, 코너와는 완전히 다른 위치·구도다.
            fk_dist = random.uniform(0.20, 0.28)
            fk_x = (atk_goal_x - fk_dist) if atk_goal_x >= 0.5 else (atk_goal_x + fk_dist)
            fk_y = 0.5 + random.uniform(-0.16, 0.16)
            self._scene_ball_start = {"x": fk_x, "y": fk_y}
            atk_idx = [self.my_slot]
            support_pool = [i for i, pl in enumerate(atk_team)
                            if pl["pos"] in _ATTACK_ROLES and i != self.my_slot]
            if support_pool:
                atk_idx.append(random.choice(support_pool))
            atk_idx = atk_idx[:3]
            # 키커(나)를 공 바로 뒤(도움닫기 자세)에 세운다.
            _kicker_back = -0.03 if atk_goal_x >= 0.5 else 0.03
            self._scene_force_start[self.my_slot] = (
                max(0.01, min(0.99, fk_x + _kicker_back)), fk_y)

            def_idx = [i for i, pl in enumerate(def_team) if pl["pos"] == "GK"]
            # 수비벽은 _scene_crowd(고정 목표)로 처리한다 — 일반 def_idx
            # 공식을 쓰면 공을 따라 흩어져서 벽이 무너져 보인다. 실제
            # 프리킥 수비벽처럼 공-골문 사이, 공에서 골문 쪽으로 살짝
            # 떨어진 지점에 3~4명을 나란히 세운다.
            wall_x = max(0.02, min(0.98, fk_x + (0.09 if atk_goal_x >= 0.5 else -0.09)))
            wall_n = random.randint(3, 4)
            wall_pool = [i for i, pl in enumerate(def_team) if pl["pos"] != "GK"]
            random.shuffle(wall_pool)
            for k, i in enumerate(wall_pool[:wall_n]):
                wy = max(0.05, min(0.95, fk_y + (k - (wall_n - 1) / 2) * 0.035))
                self._scene_crowd[("def", i)] = (wall_x, wy)

        else:
            # 기존 오픈플레이 로직 — 일반 골/실점/선방/역전골/동점골 등 공통 기본값.
            atk_idx = [i for i, pl in enumerate(atk_team) if pl["pos"] in _ATTACK_ROLES]
            if len(atk_idx) < 2:  # 공격수가 적은 포메이션 대비 보조 라인으로 보충
                atk_idx += [i for i, pl in enumerate(atk_team) if pl["pos"] in _SUPPORT_ROLES]
            random.shuffle(atk_idx)
            atk_idx = atk_idx[:3]
            if my_team_is_atk and self.my_slot not in atk_idx \
                    and atk_team[self.my_slot]["pos"] in (_ATTACK_ROLES | _SUPPORT_ROLES):
                atk_idx = ([self.my_slot] + atk_idx)[:3]

            if style == "late" and my_team_is_atk:
                # 극장골: 후방 자원까지 박스로 올라오는 '올인' 그림 — 평소엔
                # 씬에 안 들어가는 CB/CDM 한 명을 추가로 투입해 북적이게 한다.
                extra_pool = [i for i, pl in enumerate(atk_team)
                              if pl["pos"] in ("CB", "CDM") and i not in atk_idx]
                if extra_pool:
                    atk_idx.append(random.choice(extra_pool))

            # [신규] 오픈플레이 크로스. 예전엔 세트피스(코너킥)일 때만
            # 크로스 장면이 나오고, 일반 골은 전부 중앙 돌파/슛 그림뿐이라
            # 단조로웠다. 그렇다고 아무 골에나 크로스를 붙이면 "무지성
            # 크로스"가 되므로(윙어가 직접 넣은 골에 굳이 크로스를 받는
            # 그림을 붙이면 어색함), 다음 조건을 모두 만족할 때만 확률적
            # (40%)으로 크로스 장면을 쓴다:
            #   - 스타일이 "normal"(스루패스/극장골처럼 이미 고유한 연출이
            #     있는 경우는 제외)
            #   - 득점자가 중앙 자원(ST/CF/CAM) — 크로스를 받아 마무리하는
            #     그림이 자연스러운 포지션. 윙어가 직접 넣은 골은 제외.
            #   - 팀에 실제로 크로스를 올릴 측면 자원(LW/RW/LB/RB)이 있을 때
            is_cross = False
            if style == "normal":
                if my_team_is_atk:
                    central_finisher = atk_team[self.my_slot]["pos"] in ("ST", "CF", "CAM")
                    wide_pool = [i for i, pl in enumerate(atk_team)
                                 if pl["pos"] in ("LW", "RW", "LB", "RB", "LWB", "RWB")
                                 and i != self.my_slot]
                else:
                    # 상대 득점(goal_against/save)은 득점자 포지션을 알 수
                    # 없으니 중앙 자원 여부로 배제하지 않는다.
                    central_finisher = True
                    wide_pool = [i for i, pl in enumerate(atk_team)
                                 if pl["pos"] in ("LW", "RW", "LB", "RB", "LWB", "RWB")]
                is_cross = central_finisher and bool(wide_pool) and random.random() < 0.40

            if is_cross:
                # [버그 수정] 세트피스와 동일한 이유 — 홀더가 측면 자원이면
                # 그대로 크로서로 써서 연속성을 만든다.
                crosser = self.holder if self.holder in wide_pool else random.choice(wide_pool)
                crosser_pos = atk_team[crosser]["pos"]
                # 크로스는 코너킥과 달리 바이라인 바로 안쪽(피치 안)에서
                # 올라온다 — 코너 플래그(설피스 코드)보다는 덜 극단적인
                # 폭 위치.
                cross_y = 0.10 if crosser_pos in ("LW", "LB", "LWB") else 0.90
                cross_dist = 0.12
                cross_x = (atk_goal_x - cross_dist) if atk_goal_x >= 0.5 \
                    else (atk_goal_x + cross_dist)
                self._scene_ball_start = {"x": cross_x, "y": cross_y}
                # [버그 수정] 코너킥과 동일한 이유로, 크로서의 실제 직전
                # 위치에서 크로스 지점 쪽으로 70%만 블렌드한다(완전 순간이동
                # 방지 — "끝에서 끝으로" 이동하는 것처럼 보이던 문제).
                _cross_now_x, _cross_now_y = atk_team[crosser]["x"], atk_team[crosser]["y"]
                _blend = 0.7
                self._scene_force_start[crosser] = (
                    max(0.01, min(0.99, _cross_now_x + (cross_x - _cross_now_x) * _blend)),
                    max(0.01, min(0.99, _cross_now_y + (cross_y - _cross_now_y) * _blend)))
                if crosser not in atk_idx:
                    atk_idx = ([crosser] + atk_idx)[:3]
            else:
                # [버그 수정] "빌드업이 끝난 그 자리"를 그대로 슛 시작점으로
                # 쓰면, 어쩌다 빌드업이 자기 진영 근처에서 끝났을 때 골대까지
                # 피치를 거의 다 가로지르는 비현실적인 슛(사용자가 캡처한
                # 화면의 그 긴 대각선)이 나왔다. 실제 마무리 슛은 항상
                # 파이널서드 이내에서 나오므로 그 범위 안으로 당긴다
                # (스루패스는 좀 더 깊은 침투를 표현해야 하니 범위를 넉넉히
                # 둔다).
                shot_zone = _SHOT_ZONE_THROUGH if (style == "through" and my_team_is_atk) \
                    else _SHOT_ZONE_NORMAL
                self._scene_ball_start["x"] = _clamp_shot_start_x(
                    self._scene_ball_start["x"], atk_goal_x, shot_zone)

                if style == "through" and my_team_is_atk:
                    # 스루패스성 어시스트: 마무리 선수가 더 먼 거리를 침투해
                    # 들어오는 것처럼 보이도록 시작 지점을 자기 진영 쪽으로
                    # 살짝 더 당긴다(위에서 이미 현실적인 범위로 고정해뒀으므로
                    # 이 조정을 더해도 피치를 다 가로지르는 일은 없다).
                    back_x = -0.10 if self._scene_side == "home" else 0.10
                    self._scene_ball_start["x"] = _clamp_shot_start_x(
                        self._scene_ball_start["x"] + back_x, atk_goal_x, shot_zone)

            def_idx = [i for i, pl in enumerate(def_team) if pl["pos"] == "GK"]
            cb_idx = [i for i, pl in enumerate(def_team) if pl["pos"] in ("CB", "LB", "RB")]
            if cb_idx:
                def_idx.append(random.choice(cb_idx))

        self._scene_atk_idx = atk_idx
        self._scene_def_idx = def_idx
        # [개선/버그 수정] 공-선수 물리적 연결. 예전엔 슛 궤적이
        # (_scene_ball_start → 골대) 수식만으로 독립적으로 계산되고, 공격측
        # 선수 전원이 "공 쪽으로 55%만" 따라가는 느슨한 공식 하나만 썼다.
        # 그러다 보니 슛이 마무리되는 그 순간(t=1)에도 정작 어떤 선수도
        # 정확히 공 위치에 있지 않아서 "아무도 없는데 공이 꺾여 들어가는"
        # 것처럼 보였다(사용자가 캡처한 화면 그대로). 이제 "실제로 마무리
        # 하는 선수"를 명시적으로 하나 지정해서, 그 선수만은 슛이 진행될
        # 수록(_advance_scene에서 progress 비례) 공에 훨씬 강하게 달라붙게
        # 만든다 — t=1(득점/막힘 순간)엔 거의 정확히 공 위치와 겹친다.
        if my_team_is_atk and self.my_slot in atk_idx:
            self._scene_finisher_idx = self.my_slot   # 이벤트 당사자(나)가 최우선
        else:
            self._scene_finisher_idx = atk_idx[-1] if atk_idx else None
        # [버그 수정] 씬이 시작되는 순간 선수들의 실제 현재 위치를 저장해둔다.
        # 예전엔 씬 진행 계산이 (ease=0일 때) hx(홈 포지션)에서 시작하도록
        # 짜여 있어서, 평상시 플레이 중 실제로 어디 있었든 상관없이 씬이
        # 시작되자마자 홈 포지션으로 순간이동한 뒤 거기서부터 움직이기
        # 시작했다. 그게 "랜덤하게 움직이다가 슛 넣을 때만 억지로 자세를
        # 맞추는" 부자연스러움의 정체였다. 이제 실제 현재 위치에서부터
        # 부드럽게 이어지도록 시작점을 기록한다.
        atk_team = self.home_players if self._scene_side == "home" else self.away_players
        def_team = self.away_players if self._scene_side == "home" else self.home_players
        self._scene_atk_start = {i: (atk_team[i]["x"], atk_team[i]["y"]) for i in atk_idx}
        self._scene_def_start = {i: (def_team[i]["x"], def_team[i]["y"]) for i in def_idx}
        # [신규] 코너킥 박스 크라우드 시작 좌표도 기록해둔다(현재 실제
        # 위치에서 자연스럽게 이어지도록).
        self._scene_crowd_start = {}
        for (_side, _ci), (_tx, _ty) in self._scene_crowd.items():
            _cteam = atk_team if _side == "atk" else def_team
            self._scene_crowd_start[(_side, _ci)] = (_cteam[_ci]["x"], _cteam[_ci]["y"])
        # [버그 수정] 코너킥/크로스처럼 특수 시작 좌표가 강제된 선수(크로서)는
        # 실제 평소 위치 대신 그 강제 좌표에서 씬을 시작하도록 덮어쓴다.
        for _idx, _pos in self._scene_force_start.items():
            if _idx in self._scene_atk_start:
                self._scene_atk_start[_idx] = _pos
                atk_team[_idx]["x"], atk_team[_idx]["y"] = _pos  # 첫 프레임부터 바로 반영

        # [현실성 보정] 예전엔 도착 지점이 y=0.5 근처에서 사인파로 흔들리기만
        # 해서, 골대 표시(페널티박스 폭)보다 훨씬 좁은 실제 골대 폭을
        # 벗어난 위치에서 "골"이 되거나, 반대로 노골/선방인데 골대 한복판을
        # 뚫고 들어가는 것처럼 보였다. 씬 종류에 맞춰 도착 목표를 미리
        # 정해둔다 — 득점은 반드시 골대 안, 노골(빗맞음)은 골대 밖,
        # 선방은 골대 안이지만 GK가 막아내는 지점으로.
        if self._scene_kind in ("goal_for", "goal_against"):
            self._scene_shot_target_y = 0.5 + random.uniform(-1, 1) * _GOAL_HALF_HEIGHT * 0.75
        elif self._scene_kind == "miss_for" and (
                "골키퍼" in self._scene_event_text or "선방" in self._scene_event_text):
            # [버그 수정] "상대 골키퍼 선방에 막혔다"인데 궤적은 '빗나간
            # 슈팅'(골대 밖) 공식을 그대로 써서, 텍스트는 GK가 막았다는데
            # 정작 GK는 거의 안 움직이고 공은 그냥 골대 밖으로 날아가는
            # 것처럼 보였다("GK가 반응해서 막아내는 장면이 아예 없다"는
            # 지적 그대로). 이 경우엔 유효슈팅(골대 안)으로 보내고, 아래
            # GK 반응 계수도 따로 키운다.
            self._scene_shot_target_y = 0.5 + random.uniform(-1, 1) * _GOAL_HALF_HEIGHT * 0.80
        elif self._scene_kind == "miss_for":
            side_sign = random.choice([-1, 1])
            self._scene_shot_target_y = 0.5 + side_sign * random.uniform(
                _GOAL_HALF_HEIGHT * 1.4, _GOAL_HALF_HEIGHT * 3.2)
        else:  # save
            self._scene_shot_target_y = 0.5 + random.uniform(-1, 1) * _GOAL_HALF_HEIGHT * 0.85
        self._scene_shot_target_y = max(0.06, min(0.94, self._scene_shot_target_y))

        # [신규 - 코너킥 2단계 궤적] 예전엔 코너 플래그(_scene_ball_start)에서
        # 골대까지를 단 하나의 이어진 곡선(ease)으로 처리해서, "크로스가
        # 날아옴"과 "그걸 헤더/발리로 맞혀 골대로 보냄"이 시각적으로 전혀
        # 구분이 안 됐다 — 공이 코너에 나타나자마자 곧바로 골대 쪽으로
        # 휘어 들어가서 마치 "튕기자마자 골대로 가는" 것처럼 보인 원인이
        # 이것이다(사용자 지적 그대로). 세트피스(코너킥)일 때만, 크로스가
        # 실제로 도달하는 박스 안 접점(6야드 박스 부근)을 미리 정해두고,
        # _advance_scene에서 "코너→접점"과 "접점→골대"를 서로 다른 두
        # 구간으로 나눠 재생한다 — 접점에서 방향이 꺾이는 게 눈에 보여야
        # "헤더로 맞혀서 골대로 보냈다"는 인과관계가 보인다.
        self._scene_is_corner = (style == "setpiece")
        if self._scene_is_corner:
            _contact_x = 0.94 if atk_goal_x >= 0.5 else 0.06
            _contact_y = max(0.10, min(0.90,
                self._scene_shot_target_y + random.uniform(-0.10, 0.10)))
            self._scene_contact_xy = (_contact_x, _contact_y)

    def _advance_scene(self, speed_scale=1.0):
        style = self._scene_style
        self._scene_progress += 0.05 * max(1.0, speed_scale)
        # 공: 빌드업이 끝난 실제 위치 → 상대 골 쪽으로 이동 (ease-out)
        atk_goal_x = 1.0 if self._scene_side == "home" else 0.0
        t = min(1.0, self._scene_progress)
        start_x, start_y = self._scene_ball_start["x"], self._scene_ball_start["y"]
        target_y = self._scene_shot_target_y
        if getattr(self, "_scene_is_corner", False):
            # [신규] 1단계(크로스, 0~PHASE): 코너 플래그 → 박스 안 접점.
            # 2단계(헤더/슈팅, PHASE~1): 접점 → 골대. 접점에서 명확히
            # 방향이 꺾여서 "여기서 맞혀 보냈다"가 눈에 보인다.
            PHASE = 0.55
            cx, cy = self._scene_contact_xy
            if t < PHASE:
                t1 = t / PHASE
                ease1 = 1 - (1 - t1) ** 2
                self.ball["x"] = start_x + (cx - start_x) * ease1
                self.ball["y"] = start_y + (cy - start_y) * ease1
            else:
                t2 = (t - PHASE) / (1 - PHASE)
                ease2 = 1 - (1 - t2) ** 2
                self.ball["x"] = cx + (atk_goal_x - cx) * ease2
                self.ball["y"] = cy + (target_y - cy) * ease2
            ease = 1 - (1 - t) ** 2  # 아래 선수 추종 계산에서 재사용
        else:
            ease = 1 - (1 - t) ** 2
            self.ball["x"] = start_x + (atk_goal_x - start_x) * ease
            # [현실성 보정] 도착 지점을 _start_scene_body에서 미리 정해둔
            # self._scene_shot_target_y(득점=골대 안 / 노골=골대 밖 / 선방=
            # 골대 안이지만 GK가 처리)로 정확히 수렴시킨다. sin(t*π)는 t=0과
            # t=1에서 정확히 0이 되므로, 흔들림을 더해도 시작점과 도착점은
            # 항상 의도한 값 그대로 유지된다(중간 궤적만 살짝 휘어 보이게 함).
            if style in ("penalty", "penalty_miss"):
                # PK는 흔들림 없이 스팟→목표 지점(골대 안/밖)까지 일직선. 오픈
                # 플레이 특유의 드리블성 흔들림을 넣지 않아야 "이건 PK다"라는
                # 게 시각적으로 구분된다.
                self.ball["y"] = start_y + (target_y - start_y) * ease
            else:
                wobble = math.sin(t * math.pi) * 0.10
                self.ball["y"] = start_y + (target_y - start_y) * ease + wobble

        atk_team = self.home_players if self._scene_side == "home" else self.away_players
        def_team = self.away_players if self._scene_side == "home" else self.home_players

        for i in self._scene_atk_idx:
            pl = atk_team[i]
            sx, sy = self._scene_atk_start[i]
            if i == self._scene_finisher_idx:
                # [개선] 마무리하는 선수는 진행도(t)가 오를수록 공에 훨씬
                # 강하게 달라붙는다 — 0.35(초반, 아직 쇄도 중) → 0.92(막판,
                # 실제로 발/머리에 맞는 순간)까지 계수를 올려서, 득점/막힘
                # 순간엔 이 선수가 거의 정확히 공이 있는 자리에 있게 된다.
                follow_coef = 0.35 + 0.57 * t
            else:
                follow_coef = 0.55
            target_x = pl["hx"] + (self.ball["x"] - pl["hx"]) * follow_coef
            target_y = pl["hy"] + (self.ball["y"] - pl["hy"]) * (0.35 if i != self._scene_finisher_idx else follow_coef)
            pl["x"] = sx + (target_x - sx) * ease
            pl["y"] = sy + (target_y - sy) * ease

        # 수비측 반응: GK는 골문 라인 안에서 공쪽으로, 커버 수비수는 약간 좁혀줌.
        # 선방 등급(save_great/good/normal)이 높을수록 GK가 더 크게 반응해서
        # "더 화려한 선방처럼" 보이게 한다.
        goal_x = 1 - atk_goal_x
        # [버그 수정] 기존엔 save_great/save_good 두 스타일에만 반응 계수를
        # 주고, 나머지(=save_normal 포함, 그리고 실제 "골"인 goal_for/
        # goal_against까지)는 전부 기본값 0.10으로 떨어졌다. 골을 먹힌
        # 상황조차 키퍼가 거의 안 움직이는 것처럼 보여서 "공이 오는 걸
        # 신경도 안 쓴다"는 지적 그대로였다. 골이니 결과적으로는 못
        # 막아야 맞지만, 몸을 날려 반응하는 시도 자체는 보여야
        # 자연스럽다 — save류보다는 낮지만(끝내 못 미치는 느낌은 유지)
        # 예전 0.10보다는 훨씬 크게 키운다. save_normal도 이제 별도
        # 값을 받는다(예전엔 정의가 아예 빠져서 골 장면과 똑같이 취급됐다).
        gk_follow = {
            "save_great": 0.32, "save_good": 0.24, "save_normal": 0.18,
        }.get(style, 0.40 if self._scene_kind in ("goal_for", "goal_against") else 0.10)
        if self._scene_kind == "miss_for" and (
                "골키퍼" in self._scene_event_text or "선방" in self._scene_event_text):
            # [버그 수정] 위에서 유효슈팅(골대 안)으로 보내기로 한 경우,
            # 상대 GK도 실제로 반응해서 막아내는 것처럼 크게 움직여야
            # "선방" 텍스트와 장면이 맞아떨어진다.
            gk_follow = 0.30
        for i in self._scene_def_idx:
            pl = def_team[i]
            sx, sy = self._scene_def_start[i]
            is_gk = pl["pos"] == "GK"
            follow = gk_follow if is_gk else 0.25
            target_x = pl["hx"] + (goal_x - pl["hx"]) * (follow * 0.3)
            target_y = pl["hy"] + (self.ball["y"] - pl["hy"]) * follow
            if not is_gk:
                # [버그 수정] 일반 오픈플레이 공식과 같은 문제 — 여기서도
                # goal_x(자기 골) 쪽으로 당기기만 하고 하한이 없어서, 골/
                # 세이브 장면이 반복되면 이 공식을 타는 수비수도 골키퍼
                # 옆까지 밀릴 수 있었다. 같은 최소 깊이를 적용한다.
                _dir = 1 if goal_x < 0.5 else -1
                _min_x = goal_x + _MIN_DEFENSIVE_DEPTH * _dir
                target_x = max(target_x, _min_x) if _dir > 0 else min(target_x, _min_x)
            pl["x"] = sx + (target_x - sx) * ease
            pl["y"] = sy + (target_y - sy) * ease

        # [신규] 세트피스(코너킥) 크라우드 — 씬에 직접 포함 안 된 나머지
        # 선수 대부분을 박스 주변 지정 좌표로 이동시킨다. ease를 그대로
        # 재사용해서 크로스가 올라가는 동안 서서히 몰려드는 것처럼 보이게
        # 한다(다 몰린 채로 시작하면 순간이동처럼 보이니).
        if self._scene_crowd:
            for (side, i), (tx, ty) in self._scene_crowd.items():
                cteam = atk_team if side == "atk" else def_team
                pl = cteam[i]
                sx, sy = self._scene_crowd_start.get((side, i), (pl["x"], pl["y"]))
                pl["x"] = sx + (tx - sx) * ease
                pl["y"] = sy + (ty - sy) * ease

        if t >= 1.0:
            # [버그 수정] 예전엔 아래서 self._scene_side를 None으로 지운
            # '뒤에' restart_side 계산에 그 값을 다시 참조해서 항상 None이
            # 되어 재개팀이 매번 "home"으로 고정되는 버그가 있었다. 지우기
            # 전에 로컬 변수로 먼저 저장해서 실제 실점/피점 팀 기준으로
            # 정확히 재개되도록 고쳤다.
            scene_side = self._scene_side
            if self._scene_kind in ("goal_for", "goal_against"):
                if scene_side == "home":
                    self.score_home += 1
                else:
                    self.score_away += 1
                # [버그 수정] 예전엔 여기서 score_lbl을 직접 setText했는데,
                # 이 함수는 이제 __init__의 프레임 사전 계산 단계에서도
                # 호출된다(그 시점엔 아직 UI가 안 만들어져 score_lbl 자체가
                # 없어서 AttributeError가 났다). 화면 라벨 갱신은 이제
                # _apply_frame()이 프레임을 재생할 때마다 전담하므로, 여기서는
                # 숫자 상태(score_home/away)만 바꾸면 충분하다.
                self.banner_text = f"⚽ GOAL!  {self._scene_event_text}"
                self.banner_color = "#ffcc00"
            elif self._scene_kind == "miss_for":
                # [텍스트-영상 싱크 신규] 페널티 실축 등 "공격은 했지만 득점
                # 실패" — 예전엔 이런 이벤트 자체가 아예 무시되고 사라졌다.
                self.banner_text = f"😤 노골!  {self._scene_event_text}"
                self.banner_color = "#ff8844"
            else:  # save
                self.banner_text = f"🧤 SAVE!  {self._scene_event_text}"
                self.banner_color = "#44ccff"
            self.banner_alpha = 255
            self._scene_kind = None
            self._scene_side = None
            self._scene_style = "normal"
            self._scene_atk_idx = []
            self._scene_def_idx = []
            # 원위치 복귀 트리거 — 다음 drift 틱에서 자연스럽게 홈포지션으로 당겨짐
            # 장면 종료 후: 골/선방/실축 어느 쪽이든 실점(수비)측 GK가 공을 잡고
            # 다시 시작하는 게 자연스러움 (골킥/센터서클 재개 느낌)
            restart_side = "away" if scene_side == "home" else "home"
            self.possession = restart_side
            self.holder = self._gk_index(restart_side)
            self._assign_roles()
            self.pass_clock = 0.6
            # [버그 수정] 예전엔 여기서 공 좌표를 안 건드리고 다음 틱에
            # _update_possession()이 "공은 항상 홀더 발밑"이라는 규칙으로
            # 곧바로 GK 위치로 스냅시켜버렸다. 골대 근처까지는 부드럽게
            # 흘러가다가 그 직후 GK 자리로 순간이동하는 것처럼 보이는 게
            # 이 버그였다. 이제 골대 근처(현재 공 위치)에서 GK 위치까지도
            # 짧은 패스 비행(_start_pass_flight)으로 부드럽게 이어지게 한다.
            restart_team = self.home_players if restart_side == "home" else self.away_players
            gk = restart_team[self.holder]
            self._start_pass_flight((self.ball["x"], self.ball["y"]), (gk["x"], gk["y"]))