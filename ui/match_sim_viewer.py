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
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton, QComboBox
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
    (None, "CB"):  (0.22, 0.06), (None, "LB"): (0.20, 0.08), (None, "RB"): (0.20, 0.08),
    (None, "LWB"): (0.24, 0.10), (None, "RWB"): (0.24, 0.10),
    (None, "CDM"): (0.16, 0.06), (None, "CM"): (0.20, 0.08), (None, "CAM"): (0.22, 0.10),
    (None, "LM"):  (0.20, 0.08), (None, "RM"): (0.20, 0.08),
    (None, "LW"):  (0.24, 0.10), (None, "RW"): (0.24, 0.10),
    (None, "CF"):  (0.24, 0.06), (None, "ST"): (0.24, 0.06),

    # 3-5-2: 윙백이 사실상 윙어를 겸함 — 전/후퇴 폭이 매우 크다.
    ("3-5-2", "LWB"): (0.34, 0.16), ("3-5-2", "RWB"): (0.34, 0.16),
    ("3-5-2", "CB"):  (0.16, 0.05),   # 스리백은 라인을 잘 안 깨고 셋이 붙어 다니되, 완전히 안 올라오진 않는다

    # 5-3-2: 기본은 백5로 확실히 눌러앉고, 공격 전환 때만 윙백이 튀어나감
    # → 후퇴폭을 더 크게(수비 의무가 더 무겁다는 뜻) 잡는다.
    ("5-3-2", "LWB"): (0.30, 0.20), ("5-3-2", "RWB"): (0.30, 0.20),
    ("5-3-2", "CB"):  (0.14, 0.04),

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
    ("3-4-3", "CB"): (0.16, 0.04),
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


def _penalty_arc_slots(atk_goal_x, spot_x, n_atk, n_def):
    """[신규] 페널티킥일 때 키커/GK를 뺀 나머지 20명을 박스·아크 바깥으로
    빼서 실제 PK 장면처럼 보이게 한다. 예전엔 이 인원 전체가 씬에 아예
    포함이 안 돼서 평소 오픈플레이 포메이션 그대로 남아 있었다 — "수비수
    들이 이상한 데(예: 자기 골키퍼 옆)서 안 움직인다"는 지적의 원인.
    아크 바로 바깥쪽 세로선을 따라 두 팀을 어긋나게 줄 세운다."""
    arc_x = spot_x + (0.09 if atk_goal_x >= 0.5 else -0.09)
    ys = [0.14, 0.24, 0.34, 0.44, 0.56, 0.66, 0.76, 0.86, 0.20, 0.80]

    def pts(n, x_bias):
        out = []
        for i in range(n):
            y = ys[i % len(ys)] + random.uniform(-0.02, 0.02)
            x = arc_x + x_bias + random.uniform(-0.02, 0.02)
            out.append((max(0.02, min(0.98, x)), max(0.05, min(0.95, y))))
        return out

    return pts(n_atk, 0.02), pts(n_def, -0.02)


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
        coords = [(1 - x, y) for x, y in coords]
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

        self.home_players = [{"pos": lab, "x": x, "y": y, "hx": x, "hy": y, "vx": 0.0, "vy": 0.0}
                             for lab, (x, y) in layout_formation(home_formation, True)]
        self.away_players = [{"pos": lab, "x": x, "y": y, "hx": x, "hy": y, "vx": 0.0, "vy": 0.0}
                             for lab, (x, y) in layout_formation(away_formation, False)]

        my_team_slots = self.home_players if self.is_home else self.away_players
        self.my_slot = _find_my_slot(my_team_slots, my_pos)

        self.ball = {"x": 0.5, "y": 0.5}

        # [세부지표 반영] 슈팅/키패스/드리블/차단/패스성공률 — 평상시 플레이
        # 흐름(턴오버 확률, 내가 공에 관여하는 빈도)에 실제 반영한다.
        self.detail = payload.get("detail", {}) or {}
        self.team_stats = payload.get("team_stats") or {}  # [신규] 점유율 등 통계 연동용

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
        self._info_freeze_until = -1.0  # [신규] 파울/코너킥 등 정지 상황 잠깐 멈춤용
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
            _pl["reaction"] = random.uniform(0.82, 1.22)

        # [신규 - 전환 순간] 턴오버가 일어난 그 직후 잠깐 동안 양팀 모두
        # 평소보다 빠르게 반응하게 만드는 부스트. 예전엔 공수 전환이
        # shape_push가 서서히(계수 0.04) 넘어가는 것뿐이라, 실제 축구에서
        # 가장 역동적인 "뺏자마자 질주 / 뺏기자마자 전력 복귀" 장면이 전혀
        # 안 살았다. _do_pass_or_turnover에서 턴오버가 나는 순간 이 값을
        # 채워두면 아래 _update_player_positions가 몇 틱에 걸쳐 서서히
        # 원래 속도로 감쇠시킨다.
        self._transition_timer = 0.0
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
        self._breakthrough_marks = {}    # [신규] 라인 브레이크 침투 마크
        self._breakthrough_atk_side = None
        self._assign_roles()

        # [신규 — 죽어있던 통계 살리기] team_stats["shots"]는 계산은 되는데
        # 지금까지 화면엔 전혀 반영이 안 됐다. 실제 타임라인엔 "내가 관여한"
        # 사건(내 골/도움/PK 실축/내 세이브)만 있어서, 팀 전체 슈팅 수(예:
        # 13개)와 실제로 재생에 나오는 장면 수(2~3개) 사이에 큰 괴리가
        # 있었다. 부족한 만큼 "⚡ [팀] 슈팅" 필러 이벤트를 시간대를 겹치지
        # 않게 골고루 끼워 넣어서, 통계와 영상이 서로 맞아떨어지게 한다.
        # 스코어/개인 기록에는 전혀 영향을 안 준다 — 순수하게 "이 팀이
        # 실제로 이만큼 슈팅을 시도했다"를 화면에도 보여주는 연출용 채움.
        if self.team_stats:
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
               or self.banner_alpha > 0) and _tail_guard < 300:
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
        self._info_freeze_until = -1.0
        self._info_glide_until = -1.0
        self._corner_lock_home = set()
        self._corner_lock_away = set()
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

        # [버그 수정] 예전엔 "홀더보다 앞선 후보 중 가장 가까운 2명"을
        # 순수 거리순으로만 뽑아서, 어쩌다 둘 다 홀더와 같은 쪽(예: 둘 다
        # 위쪽 채널)에 있으면 반대쪽엔 패스 받을 사람이 아무도 없이 볼
        # 옆에 다닥다닥 붙어있는 것처럼 보였다("사이드로 안 벌려서 패스각을
        # 안 준다"는 지적 그대로). 위/아래 채널에서 각각 한 명씩(가능하면)
        # 뽑아서, 최소한 좌우 양쪽에 패스 받을 옵션이 하나씩은 생기게 한다.
        _support_pool = [i for i, pl in enumerate(holder_team)
                         if i != self.holder and (pl["x"] - holder["x"]) * sign_holder > -0.06]
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
        st_pool = [i for i, pl in enumerate(holder_team)
                   if pl["pos"] in ("ST", "CF") and i != self.holder]
        if len(st_pool) >= 2:
            st_pool.sort(key=lambda i: abs(holder_team[i]["x"] - holder["x"])
                                       + abs(holder_team[i]["y"] - holder["y"]))
            self._check_in_idx = st_pool[0]
            self._run_behind_idx = st_pool[-1]
        else:
            self._check_in_idx = None
            self._run_behind_idx = None

        # [신규 - 돌파 마크] 지금까지는 "홀더 근처"만 반응했다(프레서 1명+
        # 커버 2명). 그래서 공을 갖고 있지 않은 채 이미 수비 라인 뒤까지
        # 파고든 위험한 침투 선수는 완전히 무방비였다(사용자가 캡처한
        # "미드필더 라인 뚫었는데 아무도 안 막는다"가 이 증상 그대로).
        # 공격측 선수 중 상대의 최종 수비 라인(오프사이드 클램프와 동일한
        # 기준선)보다 더 깊숙이 들어온 선수를 찾아서, 프레서/커버로 이미
        # 반응 중이지 않은 나머지 수비수 중 가장 가까운 사람을 하나씩
        # 붙인다(맨마킹). 최대 2명까지만 — 전원이 쫓아가면 이번엔 반대로
        # 뒷공간이 텅 빈다.
        self._breakthrough_marks = {}
        self._breakthrough_atk_side = holder_side
        opp_def_xs = [p["x"] for p in opp_team if p["pos"] != "GK"]
        if opp_def_xs:
            if sign_holder > 0:
                last_line = max(opp_def_xs)
                breakers = [i for i, p in enumerate(holder_team)
                            if i != self.holder and p["x"] > last_line + 0.03]
            else:
                last_line = min(opp_def_xs)
                breakers = [i for i, p in enumerate(holder_team)
                            if i != self.holder and p["x"] < last_line - 0.03]
            breakers.sort(key=lambda i: abs(holder_team[i]["x"] - last_line))
            used_defs = {self._presser_idx} | set(self._cover_idx)
            avail_defs = [i for i in range(len(opp_team))
                          if i not in used_defs and opp_team[i]["pos"] != "GK"]
            for atk_i in breakers[:2]:
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
                e["done"] = True
                self.banner_text = e["text"]
                self.banner_color = "#ffcc55"
                self.banner_alpha = 255
                # [버그 수정] 파울/코너킥처럼 실제로 휘슬이 불려 플레이가
                # 멈추는 상황은, 배너만 띄우고 밑에서는 평소처럼 드리블이
                # 계속되면 안 된다("파울인데 그대로 드리블하는" 부자연스러운
                # 장면 그대로). 잠깐 선수 이동 갱신을 멈춰서(SmoothDamp가
                # 자연히 감속해 멈춤) 세트피스 준비 자세처럼 보이게 한다.
                if any(k in e["text"] for k in _STOPPAGE_KEYWORDS):
                    # [버그 수정 — 근본 원인] 예전엔 "누가 반칙을 했는지/누가
                    # 코너킥을 얻었는지"라는 실제 텍스트 정보("우리 팀"/
                    # "상대 팀")를 전혀 안 보고, 그냥 공이 지금 어느 쪽
                    # 절반에 있는지(x>=0.5?)만으로 재개팀을 추측했다. 이건
                    # 완전히 별개의 정보라서(예: 우리가 상대 진영 깊숙이서
                    # 파울을 당해도 공이 x>=0.5에 있으면 엉뚱하게 홈팀이
                    # 재개하는 것으로 잘못 판정될 수 있었다), "우리 팀
                    # 파울"인데 레드팀이 자기 골대 쪽으로 움직이는 것처럼
                    # 보이는 등 방향이 뒤죽박죽되는 원인이었다. 이제 텍스트
                    # 자체("우리 팀"/"상대 팀")로 실제 재개팀을 직접 판정한다.
                    my_side = "home" if self.is_home else "away"
                    opp_side = "away" if self.is_home else "home"
                    if "파울" in e["text"]:
                        # "우리 팀 파울" = 우리가 반칙 → 상대가 프리킥 재개.
                        # "상대 팀 파울" = 상대가 반칙 → 우리가 프리킥 재개.
                        restart_side = opp_side if "우리 팀" in e["text"] else my_side
                    elif "코너킥" in e["text"]:
                        # "우리 팀 코너킥" = 우리가 코너를 얻어 우리가 참.
                        # "상대 팀 코너킥" = 상대가 코너를 얻어 상대가 참.
                        restart_side = my_side if "우리 팀" in e["text"] else opp_side
                    else:
                        restart_side = "home" if self.ball["x"] >= 0.5 else "away"
                    self.possession = restart_side
                    # [버그 수정 — 근본 원인] self.possession만 바꾸고
                    # self.holder는 그대로 둬서, 이전 팀에서 쓰던 holder
                    # 인덱스를 새 팀 배열에 그대로 대입해버리는 사고가 났다
                    # (예: 예전 holder=5가 새 팀에선 전혀 다른 엉뚱한
                    # 선수를 가리킴). 그 선수가 실제 공 위치와 무관한
                    # 자리에 있으면 재개 후 드리블/패스 전개가 산으로 갔다.
                    # 재개팀에서 지금 공과 가장 가까운 필드 플레이어(GK
                    # 제외)를 새 holder로 정하고, 역할(서포트/프레서/커버
                    # 등)도 이 시점 기준으로 다시 배정한다.
                    restart_team = self.home_players if restart_side == "home" else self.away_players
                    self.holder = min(
                        (i for i, pl in enumerate(restart_team) if pl["pos"] != "GK"),
                        key=lambda i: abs(restart_team[i]["x"] - self.ball["x"])
                                     + abs(restart_team[i]["y"] - self.ball["y"]))
                    self._assign_roles()
                    # [버그 수정] 예전엔 shape_push(공격 전개도)가 소유권을
                    # 오래 유지해야만 서서히 올라가는 값이라, 우리 팀이
                    # 방금 상대 진영까지 밀고 올라간 상태에서 파울이 나도
                    # shape_push가 미처 못 따라와 있으면 수비수들이 여전히
                    # 자기 진영에 남은 옛 대형으로 스냅됐다("파울 땄는데 왜
                    # 다 뒤에 있냐"는 지적 그대로). 스냅 직전에 실제 공
                    # 위치를 기준으로 두 팀의 shape_push를 즉시 맞춰서,
                    # "지금 이 순간 실제로 어디까지 밀고 올라가 있는지"가
                    # 바로 반영되게 한다.
                    _bx = self.ball["x"]
                    self.shape_push["home"] = max(0.0, min(1.0, _bx))
                    self.shape_push["away"] = max(0.0, min(1.0, 1.0 - _bx))
                    # [버그 수정 — 근본 원인] 예전엔 "12번 반복 호출"이
                    # 전부 이 한 틱 안에서 끝나버려서, 사전계산 프레임에는
                    # 결과 딱 1장만 찍혔다. 즉 몇 번을 반복 계산하든 화면
                    # 상으로는 "그 프레임에서 다음 프레임으로 순간이동"으로
                    # 보일 수밖에 없었다(사용자가 지적한 "부드럽지 않다"의
                    # 정체). 이제는 즉시 수렴시키지 않고, 앞으로 여러 실제
                    # 틱(각각 프레임 한 장씩 찍힘)에 걸쳐 정상보다 살짝 빠른
                    # 속도로 서서히 정렬되도록 "글라이드 구간"만 설정한다.
                    if "코너킥" in e["text"]:
                        self._snap_corner_crowd(restart_side)
                    self._info_glide_until = self.clock + 0.6
                    self._info_freeze_until = -1.0
                elif e.get("_filler_side") and self._scene_kind is None:
                    # [신규 — 죽어있던 통계 살리기] team_stats["shots"]를
                    # 맞추려고 끼워 넣은 필러 이벤트. 실제 "씬"까지는 안
                    # 만들고(그러면 골/선방 로직과 얽혀 리스크가 커진다),
                    # 이미 검증된 패스 비행(_start_pass_flight)만 재사용해서
                    # 그 팀 선수 하나가 골대 쪽으로 슛을 시도하는 궤적을
                    # 그린다. 유효슈팅/빗나감 비율은 그 팀의 실제
                    # shots_on/shots 비율을 그대로 따른다 — 통계와 영상이
                    # 서로 어긋나지 않게.
                    _fside = e["_filler_side"]
                    _fteam = self.home_players if _fside == "home" else self.away_players
                    _fgoal_x = 1.0 if _fside == "home" else 0.0
                    _fpool = [i for i, pl in enumerate(_fteam)
                              if pl["pos"] in (_ATTACK_ROLES | _SUPPORT_ROLES)]
                    _ftaker = random.choice(_fpool) if _fpool else random.randrange(len(_fteam))
                    _fsx = _clamp_shot_start_x(_fteam[_ftaker]["x"], _fgoal_x, _SHOT_ZONE_NORMAL)
                    _fsy = _fteam[_ftaker]["y"]
                    _fstat = (self.team_stats or {}).get(_fside, {})
                    _on_ratio = 0.35
                    if _fstat.get("shots"):
                        _on_ratio = max(0.15, min(0.75,
                            (_fstat.get("shots_on", 0) or 0) / max(1, _fstat["shots"])))
                    if random.random() < _on_ratio:
                        _fty = 0.5 + random.uniform(-1, 1) * _GOAL_HALF_HEIGHT * 0.8
                    else:
                        _fsign = random.choice([-1, 1])
                        _fty = 0.5 + _fsign * random.uniform(
                            _GOAL_HALF_HEIGHT * 1.3, _GOAL_HALF_HEIGHT * 3.0)
                    _fty = max(0.04, min(0.96, _fty))
                    self.possession = _fside
                    self.holder = _ftaker
                    self._assign_roles()
                    self.ball["x"], self.ball["y"] = _fsx, _fsy
                    self.ball_trail = []
                    self._start_pass_flight((_fsx, _fsy), (_fgoal_x, _fty))
                    self.pass_clock = 0.9  # 슛 직후 바로 다음 판정으로 안 끊기게 살짝 여유

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
        elif self.clock < self._info_glide_until:
            # [신규] 대형 정렬 글라이드 — 예전엔 정렬 계산을 한 틱 안에서
            # 몇 번을 반복하든 사전계산 프레임엔 결과가 딱 1장만 찍혀서
            # "부드럽지 않고 순간이동한다"는 지적 그대로였다(반복 횟수와
            # 무관하게 프레임 한 장짜리 컷이었으니 당연히 그랬다). 이제는
            # 매 실제 틱(=프레임 한 장)마다 이 분기가 한 번씩만 불려서,
            # 정상 속도보다 살짝 빠른 정도로(순간이동은 아니게) 여러
            # 프레임에 걸쳐 서서히 대형을 잡아가는 게 눈에 보인다.
            self._update_player_positions(2.4)
        elif self.clock < self._info_freeze_until:
            # [신규] 파울/코너킥 등으로 잠깐 정지된 구간 — 선수 이동 갱신을
            # 건너뛰어서(SmoothDamp가 자연히 감속해 멈춤) 휘슬 불린 순간
            # 그대로 자세를 잡고 서 있는 것처럼 보이게 한다.
            pass
        else:
            self._update_possession(speed_scale)

        if self.banner_alpha > 0 and (self._scene_kind is None):
            # [버그 수정] 예전엔 페이드 속도가 고정값(-8/틱)이라, 재생 배속을
            # 올리면(이벤트 간격은 배속에 비례해 빨리 다가오는데) 배너는 그대로
            # 느리게 사라져서 다음 장면 배너와 겹쳐 보였다(사용자가 말한 "충돌").
            # 배속에 비례해서 같이 빨리 사라지도록 맞췄다.
            # [버그 수정] speed(float)를 곱하면서 banner_alpha가 float이 돼서
            # PyQt6의 setAlpha(int 전용)에서 TypeError가 났었다. int로 고정.
            self.banner_alpha = int(max(0, self.banner_alpha - 8 * max(1.0, speed_scale)))

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

        # 팀 형태 밀기(공격측 1.0, 수비측은 약하게 라인만 유지) — 서서히 전환
        for side in ("home", "away"):
            target = 1.0 if side == self.possession else 0.0
            self.shape_push[side] += (target - self.shape_push[side]) * 0.04

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
            atk_skip = set(self._scene_atk_idx) | {
                i for i, (s, _, _) in self._scene_crowd.items() if s == "atk"}
            def_skip = set(self._scene_def_idx) | {
                i for i, (s, _, _) in self._scene_crowd.items() if s == "def"}

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

        for side, team in (("home", self.home_players), ("away", self.away_players)):
            push = self.shape_push[side]
            sign = 1 if side == "home" else -1
            is_holder_side = (side == holder_side)
            formation = self.home_formation if side == "home" else self.away_formation
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
                # [개선] 포메이션별 전진/후퇴 폭 — 공격 시(push→1) hx보다
                # fwd만큼 더 전진하고, 수비 시(push→0) back만큼 물러난다.
                # 예전엔 후퇴가 아예 없어서 수비할 때도 항상 중립 위치에만
                # 머물렀다(로우블록으로 내려앉는 그림이 없었음).
                fwd, back = _tactical_dx(formation, pl["pos"])
                adv = (fwd * push - back * (1 - push)) * sign
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
                    # [신규] 측면 자원(윙어/풀백/윙백)은 팀이 공격 중일 때
                    # 볼 쪽(대개 중앙)으로 쏠리면 안 되고 오히려 터치라인
                    # 폭을 유지해서 공간을 벌려줘야 한다(스트레치런). 예전엔
                    # 다른 선수들과 똑같은 공식이라 볼이 중앙에 있으면
                    # 윙어도 같이 중앙으로 몰려 폭이 사라졌다.
                    target_y = pl["hy"] + (perc_by - pl["hy"]) * 0.04
                else:
                    target_y = pl["hy"] + (perc_by - pl["hy"]) * (0.16 if push > 0.3 else 0.07)

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
                    target_x = pl["hx"] + adv * max(push, 0.6) + 0.06 * sign
                    holder_lean_y = holder["y"] + lane_side * 0.18
                    target_y = pl["hy"] * 0.6 + holder_lean_y * 0.4
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

                target_x = min(0.97, max(0.03, target_x))
                target_y = min(0.95, max(0.05, target_y))
                # [개인화 + 전환 부스트] 기본 최고속도에 (1) 턴오버 직후
                # 감쇠 중인 전환 부스트(전원 공통)와 (2) 선수별 반응성
                # 편차를 함께 곱한다. 반응성이 높을수록 목표 지점까지
                # smooth_time도 짧게 줘서(=더 기민하게 붙는다) 속도뿐
                # 아니라 "반응 타이밍" 자체도 선수마다 달라지게 한다.
                reaction = pl.get("reaction", 1.0)
                max_speed = _MAX_SPEED.get(pl["pos"], 0.6) * transition_mult * (
                    0.9 + 0.1 * reaction)
                smooth_time = _SMOOTH_TIME / reaction
                _steer_toward(pl, target_x, target_y, dt, max_speed, smooth_time)

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

    def _do_pass_or_turnover(self):
        side = self.possession
        team = self.home_players if side == "home" else self.away_players
        opp_team = self.away_players if side == "home" else self.home_players
        sign = 1 if side == "home" else -1
        holder_x = team[self.holder]["x"]
        start_xy = (self.ball["x"], self.ball["y"])

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
        _MAX_PASS_DIST = 0.42
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
            # 박스·아크 밖으로 빠져 나가는 대형을 잡는다.
            _atk_others = [i for i, pl in enumerate(atk_team) if i != self.my_slot]
            _def_others = [i for i, pl in enumerate(def_team) if pl["pos"] != "GK"]
            random.shuffle(_atk_others)
            random.shuffle(_def_others)
            _atk_pts, _def_pts = _penalty_arc_slots(atk_goal_x, spot_x, len(_atk_others), len(_def_others))
            for i, (jx, jy) in zip(_atk_others, _atk_pts):
                self._scene_crowd[i] = ("atk", jx, jy)
            for i, (jx, jy) in zip(_def_others, _def_pts):
                self._scene_crowd[i] = ("def", jx, jy)

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
            _atk_others = [i for i, pl in enumerate(atk_team) if i != taker]
            _def_others = [i for i, pl in enumerate(def_team) if pl["pos"] != "GK"]
            random.shuffle(_atk_others)
            random.shuffle(_def_others)
            _atk_pts, _def_pts = _penalty_arc_slots(atk_goal_x, spot_x, len(_atk_others), len(_def_others))
            for i, (jx, jy) in zip(_atk_others, _atk_pts):
                self._scene_crowd[i] = ("atk", jx, jy)
            for i, (jx, jy) in zip(_def_others, _def_pts):
                self._scene_crowd[i] = ("def", jx, jy)

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
                self._scene_crowd[i] = ("atk", jx, jy)
            for i, (jx, jy) in zip(_crowd_def, _def_pts):
                self._scene_crowd[i] = ("def", jx, jy)

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
                self._scene_crowd[i] = ("atk", jx, jy)
            for i, (jx, jy) in zip(_crowd_def, _def_pts):
                self._scene_crowd[i] = ("def", jx, jy)

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
                self._scene_crowd[i] = ("def", wall_x, wy)

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
        for _ci, (_side, _tx, _ty) in self._scene_crowd.items():
            _cteam = atk_team if _side == "atk" else def_team
            self._scene_crowd_start[_ci] = (_cteam[_ci]["x"], _cteam[_ci]["y"])
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
            pl["x"] = sx + (target_x - sx) * ease
            pl["y"] = sy + (target_y - sy) * ease

        # [신규] 세트피스(코너킥) 크라우드 — 씬에 직접 포함 안 된 나머지
        # 선수 대부분을 박스 주변 지정 좌표로 이동시킨다. ease를 그대로
        # 재사용해서 크로스가 올라가는 동안 서서히 몰려드는 것처럼 보이게
        # 한다(다 몰린 채로 시작하면 순간이동처럼 보이니).
        if self._scene_crowd:
            for i, (side, tx, ty) in self._scene_crowd.items():
                cteam = atk_team if side == "atk" else def_team
                pl = cteam[i]
                sx, sy = self._scene_crowd_start.get(i, (pl["x"], pl["y"]))
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