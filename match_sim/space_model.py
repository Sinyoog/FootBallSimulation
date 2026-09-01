# -*- coding: utf-8 -*-
"""match_sim/space_model.py — 피치 공간 모델 (pitch control + 공간 가치).

## 이게 무엇을 대체하는가

기존 `sim_engine._update_player_positions`는 모든 목표 좌표를

    target_x = pl["hx"] + adv        # adv = 포메이션별 전진/후퇴폭 테이블
    target_y = pl["hy"] + (볼_y - pl["hy"]) * 작은계수

형태로 만들었다. 즉 **선수가 자기 포메이션 기준점(hx, hy)에 스프링으로
묶여 있고**, 볼은 그 스프링을 살짝 흔드는 외란일 뿐이었다. 그래서:

  * 목표 좌표 자체가 거의 안 움직이니 선수도 거의 안 움직인다
    (계측: 정지 시간 74%, 한 프레임에 움직이는 선수 1.4명 / 실축 8~18명)
  * "이 선수는 지금 어디로 가야 유리한가"라는 질문이 코드에 존재하지 않았다.
    대신 "이 포지션은 이만큼 전진한다"는 상수표만 있었다.
  * 그래서 새 상황(메찰라 침투, 라볼피아나, 인버티드 윙백…)마다 `elif`를
    하나씩 붙여야 했고, 그 특수 케이스에 안 걸린 나머지 17명은 계속 얼어
    있었다.

이 모듈은 그 질문에 답한다. 피치를 격자로 나누고, 매 틱 각 칸에 대해

  1. **점유(control)** — 양 팀 중 누가 그 칸에 먼저 도달하는가 (확률)
  2. **가치(value)**  — 그 칸을 차지하면 얼마나 유리한가 (국면별로 다름)

를 계산한다. 선수의 목표는 이제 상수표가 아니라 이 필드의 argmax다.

## 왜 이 방식인가 (참고한 개념)

축구 분석에서 쓰는 **pitch control** 모델(Spearman 계열)의 단순화판이다.
"각 지점에 누가 먼저 도달하는가"를 도달시간 차이의 로지스틱으로 확률화한다.
여기에 **off-ball value**(Fernández–Bornn 계열의 공간 가치) 개념을 얹어
"도달 가능한 것"과 "도달할 가치가 있는 것"을 분리했다.

원 논문들의 정밀도를 재현하려는 게 아니다 — 이 게임은 좌표 데이터가
애초에 없고, 필요한 건 "22명이 각자 다른 이유로 계속 움직이는 그림"이다.
그래서 계산은 의도적으로 거칠고 빠르게 유지한다.

## 성능

기본 격자 32×20 = 640칸. numpy가 있으면 도달시간 행렬(22×640)을 한 번에
벡터 연산으로 처리한다. numpy가 없으면 자동으로 16×10 = 160칸 순수
파이썬 경로로 떨어진다(느리지만 동작은 동일한 성격).

## 좌표계

`sim_engine`과 동일하다 — x∈[0,1]이 피치 길이(홈팀 기준 x=0이 자기 골문),
y∈[0,1]이 피치 폭. 미터 환산은 105×68을 쓴다.
"""

import math

try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:          # numpy 없는 환경 — 거친 격자 + 순수 파이썬으로 폴백
    _np = None
    HAVE_NUMPY = False

PITCH_LEN_M = 105.0
PITCH_WID_M = 68.0

# 격자 해상도. numpy가 없으면 절반으로 떨어뜨린다(계산량 1/4).
GRID_NX = 32 if HAVE_NUMPY else 16
GRID_NY = 20 if HAVE_NUMPY else 10

# ── 도달시간 모델 파라미터 ──
REACTION_S = 0.35      # 방향 전환/반응에 드는 고정 지연(초)
CONTROL_LAMBDA = 0.55  # 도달시간 차이 → 점유확률 로지스틱의 기울기.
                       # 작을수록 점유가 흐릿해지고(모호한 공간이 많아짐),
                       # 클수록 흑백으로 갈린다. 0.55는 "1.8초쯤 빨리 도착
                       # 하면 대략 3:1로 우세"에 해당.

# ── 가치 필드 파라미터 ──
PASS_SPEED_MS = 14.0   # 패스 평균 속도. 볼에서 그 칸까지 걸리는 시간 추정용.
PASS_MAX_M = 45.0      # 이 거리를 넘는 패스는 사실상 후보에서 제외
SHOT_RANGE_M = 30.0    # 슛 위협이 의미 있는 거리
GOAL_HALF_W = 0.054    # 골대 반폭(정규화 y) — sim_engine._GOAL_HALF_HEIGHT와 동일


# ══════════════════════════════════════════════════════════════════
#  격자
# ══════════════════════════════════════════════════════════════════
class Grid:
    """피치 격자. 셀 중심 좌표를 미리 계산해 캐시해 둔다(매 틱 재생성 금지)."""

    __slots__ = ("nx", "ny", "cx", "cy", "n", "xs", "ys", "gx", "gy")

    def __init__(self, nx=GRID_NX, ny=GRID_NY):
        self.nx, self.ny = nx, ny
        self.n = nx * ny
        # 셀 중심(정규화). 가장자리 밖으로 목표가 잡히지 않도록 안쪽으로 넣는다.
        self.xs = [(i + 0.5) / nx for i in range(nx)]
        self.ys = [(j + 0.5) / ny for j in range(ny)]
        cx, cy = [], []
        for j in range(ny):
            for i in range(nx):
                cx.append(self.xs[i])
                cy.append(self.ys[j])
        if HAVE_NUMPY:
            self.cx = _np.asarray(cx, dtype=_np.float32)
            self.cy = _np.asarray(cy, dtype=_np.float32)
            # 미터 환산 좌표(거리 계산용)
            self.gx = self.cx * PITCH_LEN_M
            self.gy = self.cy * PITCH_WID_M
        else:
            self.cx, self.cy = cx, cy
            self.gx = [v * PITCH_LEN_M for v in cx]
            self.gy = [v * PITCH_WID_M for v in cy]


_DEFAULT_GRID = Grid()


def default_grid():
    return _DEFAULT_GRID


# ══════════════════════════════════════════════════════════════════
#  도달시간 / 점유
# ══════════════════════════════════════════════════════════════════
def _arrival_times(players, grid):
    """각 선수가 각 셀에 도달하는 데 걸리는 시간(초) 행렬 (n_players × n_cells).

    현재 속도를 반영한다 — 달리던 방향으로는 빨리, 반대 방향으로는 느리게
    도달한다. 이게 없으면 "전속력으로 반대편으로 달리던 선수가 즉시 방향을
    틀어 똑같이 빨리 도착"하는 비현실적인 점유도가 나온다.

    players: [{"x","y","vx","vy","vmax_ms"}] — vmax_ms는 정규화 좌표 기준 최고속도가
             아니라 **m/s**다(호출자가 환산해서 넘긴다).
    """
    if HAVE_NUMPY:
        px = _np.asarray([p["x"] for p in players], dtype=_np.float32) * PITCH_LEN_M
        py = _np.asarray([p["y"] for p in players], dtype=_np.float32) * PITCH_WID_M
        vx = _np.asarray([p["vx"] for p in players], dtype=_np.float32) * PITCH_LEN_M
        vy = _np.asarray([p["vy"] for p in players], dtype=_np.float32) * PITCH_WID_M
        vmax = _np.asarray([max(1e-3, p.get("vmax_ms", 7.4)) for p in players], dtype=_np.float32)

        # 반응시간 동안은 현재 속도로 계속 흘러간다고 본다(관성).
        sx = px + vx * REACTION_S
        sy = py + vy * REACTION_S
        dx = grid.gx[None, :] - sx[:, None]
        dy = grid.gy[None, :] - sy[:, None]
        dist = _np.sqrt(dx * dx + dy * dy)
        return REACTION_S + dist / vmax[:, None]

    # ── 순수 파이썬 폴백 ──
    out = []
    for p in players:
        px = p["x"] * PITCH_LEN_M + p["vx"] * PITCH_LEN_M * REACTION_S
        py = p["y"] * PITCH_WID_M + p["vy"] * PITCH_WID_M * REACTION_S
        vmax = max(1e-3, p.get("vmax_ms", 7.4))
        row = []
        for k in range(grid.n):
            dx = grid.gx[k] - px
            dy = grid.gy[k] - py
            row.append(REACTION_S + math.hypot(dx, dy) / vmax)
        out.append(row)
    return out


def _min_over_players(tt, n_cells):
    """행렬에서 셀별 최소 도달시간."""
    if HAVE_NUMPY:
        return tt.min(axis=0)
    return [min(tt[i][k] for i in range(len(tt))) for k in range(n_cells)]


def compute_control(team_a, team_b, grid=None):
    """셀별로 team_a가 점유할 확률(0~1)을 돌려준다.

    도달시간 차이 Δt = t_B_min - t_A_min 에 로지스틱을 씌운다.
    Δt > 0 이면 A가 먼저 도착 → A 우세.
    """
    grid = grid or _DEFAULT_GRID
    ta = _min_over_players(_arrival_times(team_a, grid), grid.n)
    tb = _min_over_players(_arrival_times(team_b, grid), grid.n)
    if HAVE_NUMPY:
        d = _np.clip((tb - ta) * CONTROL_LAMBDA, -30.0, 30.0)
        return 1.0 / (1.0 + _np.exp(-d)), ta, tb
    ctrl = []
    for k in range(grid.n):
        d = max(-30.0, min(30.0, (tb[k] - ta[k]) * CONTROL_LAMBDA))
        ctrl.append(1.0 / (1.0 + math.exp(-d)))
    return ctrl, ta, tb


# ══════════════════════════════════════════════════════════════════
#  가치 필드
# ══════════════════════════════════════════════════════════════════
def _threat(grid, atk_goal_x):
    """셀에서의 슛 위협 — 골대까지 거리와 각도를 함께 본다.

    거리만 쓰면 골라인 구석(각도 0)이 최고 가치로 잡혀서 선수들이 코너
    플래그로 몰려간다. 실제로 위험한 곳은 "가깝고 + 각이 열린" 곳이다.
    """
    gx_norm = atk_goal_x * PITCH_LEN_M
    gy_norm = 0.5 * PITCH_WID_M
    if HAVE_NUMPY:
        dx = grid.gx - gx_norm
        dy = grid.gy - gy_norm
        dist = _np.sqrt(dx * dx + dy * dy)
        # 각도: 골문 폭을 그 지점에서 바라본 시야각(근사)
        goal_w = GOAL_HALF_W * 2 * PITCH_WID_M
        angle = _np.arctan2(goal_w * _np.abs(dx) + 1e-6,
                            dx * dx + dy * dy - (goal_w / 2) ** 2 + 1e-6)
        angle = _np.abs(angle)
        prox = _np.clip(1.0 - dist / SHOT_RANGE_M, 0.0, 1.0)
        return prox * _np.clip(angle / 0.7, 0.0, 1.0)
    out = []
    goal_w = GOAL_HALF_W * 2 * PITCH_WID_M
    for k in range(grid.n):
        dx = grid.gx[k] - gx_norm
        dy = grid.gy[k] - gy_norm
        dist = math.hypot(dx, dy)
        angle = abs(math.atan2(goal_w * abs(dx) + 1e-6,
                               dx * dx + dy * dy - (goal_w / 2) ** 2 + 1e-6))
        prox = max(0.0, min(1.0, 1.0 - dist / SHOT_RANGE_M))
        out.append(prox * max(0.0, min(1.0, angle / 0.7)))
    return out


def _progression(grid, atk_goal_x, ball_x):
    """전진 가치 — **볼보다 앞선 칸**일수록 높다.

    [수정 — Phase 2] 처음엔 절대 위치(상대 골문에 가까울수록 높음)로
    만들었는데, 그러면 이 항이 경기 내내 변하지 않는 정적 기울기가 된다.
    존 가우시안(역시 정적)과 곱해지면 argmax가 "내 존의 가장 앞쪽" 한
    점에 못박히고, 선수는 거기 도착한 뒤 영원히 서 있는다(실측: 틱당
    이동 중앙값 0.1m, 정지 시간 84%).

    실제 축구에서 "전진"은 골대까지의 절대 거리가 아니라 **볼 라인을
    넘어서는 것**이다. 볼이 올라가면 팀 전체의 전진 목표도 같이 올라간다.
    이렇게 바꾸면 볼이 움직일 때마다 22명의 가치 필드가 통째로 따라
    움직인다 — "다 같이 오르내리는 블록"이 공짜로 나온다.
    """
    if HAVE_NUMPY:
        rel = (grid.cx - ball_x) if atk_goal_x > 0.5 else (ball_x - grid.cx)
        return _np.clip((rel + 0.12) / 0.34, 0.0, 1.0)
    out = []
    for v in grid.cx:
        rel = (v - ball_x) if atk_goal_x > 0.5 else (ball_x - v)
        out.append(max(0.0, min(1.0, (rel + 0.12) / 0.34)))
    return out


def _pass_feasibility(grid, ball_x, ball_y, opp_players):
    """볼에서 그 칸으로 패스가 실제로 갈 수 있는가 (0~1).

    두 가지를 본다:
      * 거리 — 너무 멀면 0
      * 레인 차단 — 볼과 그 칸을 잇는 직선 근처에 상대가 있으면 감점

    이게 없으면 선수들이 "가치는 높지만 볼이 절대 갈 수 없는 곳"(예: 상대
    진영 반대편 구석)으로 몰려간다.
    """
    bx, by = ball_x * PITCH_LEN_M, ball_y * PITCH_WID_M
    if HAVE_NUMPY:
        dx = grid.gx - bx
        dy = grid.gy - by
        dist = _np.sqrt(dx * dx + dy * dy)
        reach = _np.clip(1.0 - dist / PASS_MAX_M, 0.0, 1.0)
        if not opp_players:
            return reach
        ox = _np.asarray([p["x"] for p in opp_players], dtype=_np.float32) * PITCH_LEN_M
        oy = _np.asarray([p["y"] for p in opp_players], dtype=_np.float32) * PITCH_WID_M
        # 볼→셀 선분에 대한 상대 선수의 수직거리(투영이 선분 안에 있을 때만)
        seg_x, seg_y = dx, dy                       # (n_cells,)
        seg_len2 = _np.maximum(seg_x ** 2 + seg_y ** 2, 1e-6)
        rx = ox[:, None] - bx                       # (n_opp, 1)
        ry = oy[:, None] - by
        t = (rx * seg_x[None, :] + ry * seg_y[None, :]) / seg_len2[None, :]
        t = _np.clip(t, 0.0, 1.0)
        px = t * seg_x[None, :]
        py = t * seg_y[None, :]
        perp = _np.sqrt((rx - px) ** 2 + (ry - py) ** 2)
        # 레인에서 3m 안이면 강하게, 8m까지 약하게 차단
        block = _np.clip(1.0 - (perp - 3.0) / 5.0, 0.0, 1.0).max(axis=0)
        return reach * (1.0 - 0.85 * block)
    out = []
    for k in range(grid.n):
        dx = grid.gx[k] - bx
        dy = grid.gy[k] - by
        dist = math.hypot(dx, dy)
        reach = max(0.0, min(1.0, 1.0 - dist / PASS_MAX_M))
        block = 0.0
        seg_len2 = max(dx * dx + dy * dy, 1e-6)
        for p in opp_players:
            rx = p["x"] * PITCH_LEN_M - bx
            ry = p["y"] * PITCH_WID_M - by
            t = max(0.0, min(1.0, (rx * dx + ry * dy) / seg_len2))
            perp = math.hypot(rx - t * dx, ry - t * dy)
            block = max(block, max(0.0, min(1.0, 1.0 - (perp - 3.0) / 5.0)))
        out.append(reach * (1.0 - 0.85 * block))
    return out


class SpaceField:
    """한 틱 분의 공간 계산 결과. 두 팀이 같은 격자/도달시간을 공유하므로
    한 번만 계산해서 양쪽이 나눠 쓴다."""

    __slots__ = ("grid", "ctrl_home", "ctrl_away", "t_home", "t_away",
                 "threat_home", "threat_away", "prog_home", "prog_away",
                 "pass_feas_home", "pass_feas_away", "ball_x", "ball_y")

    def __init__(self, grid):
        self.grid = grid


def build_field(home_players, away_players, ball_x, ball_y,
                home_atk_goal_x=1.0, grid=None):
    """한 틱 분의 공간 필드를 통째로 계산한다.

    home_players / away_players 의 각 원소는 최소한
    {"x","y","vx","vy","vmax_ms"} 를 갖고 있어야 한다(vmax 단위 m/s).
    """
    grid = grid or _DEFAULT_GRID
    f = SpaceField(grid)
    f.ball_x, f.ball_y = ball_x, ball_y

    ctrl_home, t_home, t_away = compute_control(home_players, away_players, grid)
    f.ctrl_home = ctrl_home
    f.t_home, f.t_away = t_home, t_away
    if HAVE_NUMPY:
        f.ctrl_away = 1.0 - ctrl_home
    else:
        f.ctrl_away = [1.0 - c for c in ctrl_home]

    away_atk_goal_x = 1.0 - home_atk_goal_x
    f.threat_home = _threat(grid, home_atk_goal_x)
    f.threat_away = _threat(grid, away_atk_goal_x)
    f.prog_home = _progression(grid, home_atk_goal_x, ball_x)
    f.prog_away = _progression(grid, away_atk_goal_x, ball_x)
    f.pass_feas_home = _pass_feasibility(grid, ball_x, ball_y, away_players)
    f.pass_feas_away = _pass_feasibility(grid, ball_x, ball_y, home_players)
    return f


# ══════════════════════════════════════════════════════════════════
#  선수별 목표 선택
# ══════════════════════════════════════════════════════════════════
def _zone_affinity(grid, hx, hy, sigma_x, sigma_y):
    """역할 존 — 포메이션 기준점 주변의 가우시안.

    [핵심] 이게 예전 `hx + adv` 스프링을 대체하는 부분이다. 차이는:
      * 스프링은 **목표를 지정**했다 → 선수가 거기서 못 벗어난다.
      * 존은 **가중치**다 → 가치가 충분히 높으면 존 밖으로도 나간다.
    sigma를 크게 잡을수록 자유도가 높다. 실축에서 선수가 자기 "포지션"에서
    20~30m씩 벗어나는 걸 허용하려면 sigma가 그 스케일이어야 한다.
    """
    if HAVE_NUMPY:
        dx = (grid.cx - hx) * PITCH_LEN_M / sigma_x
        dy = (grid.cy - hy) * PITCH_WID_M / sigma_y
        return _np.exp(-0.5 * (dx * dx + dy * dy))
    out = []
    for k in range(grid.n):
        dx = (grid.cx[k] - hx) * PITCH_LEN_M / sigma_x
        dy = (grid.cy[k] - hy) * PITCH_WID_M / sigma_y
        out.append(math.exp(-0.5 * (dx * dx + dy * dy)))
    return out


TRAVEL_REF_S = 6.0   # 이 시간 안에 갈 수 있으면 비용 부담이 거의 없다


def _travel_cost(grid, player, vmax_ms):
    """그 칸까지 가는 비용 — 도달시간을 기준시간으로 나눠 [0,1]로 정규화.

    [수정 — Phase 2] 처음엔 초 단위 원값을 그대로 썼는데, 30m 이동이면
    약 4초 → 다른 항(전부 [0,1])보다 스케일이 4배 커서, 가중치를 0.2로
    줘도 사실상 "제자리에 있어라"가 최우선 항이 됐다. 정규화해서 다른
    항과 같은 눈금 위에 올린다.
    """
    px = player["x"] * PITCH_LEN_M
    py = player["y"] * PITCH_WID_M
    if HAVE_NUMPY:
        dx = grid.gx - px
        dy = grid.gy - py
        t = _np.sqrt(dx * dx + dy * dy) / max(1e-3, vmax_ms)
        return _np.clip(t / TRAVEL_REF_S, 0.0, 1.6)
    out = []
    for k in range(grid.n):
        t = math.hypot(grid.gx[k] - px, grid.gy[k] - py) / max(1e-3, vmax_ms)
        out.append(min(1.6, t / TRAVEL_REF_S))
    return out


def argmax_cell(values):
    if HAVE_NUMPY:
        return int(_np.argmax(values))
    best, bi = -1e30, 0
    for k, v in enumerate(values):
        if v > best:
            best, bi = v, k
    return bi


def cell_xy(grid, k):
    return float(grid.cx[k]), float(grid.cy[k])


def make_claim_mask(grid):
    """탐욕적 선점용 감쇠 마스크(전부 1로 시작)."""
    if HAVE_NUMPY:
        return _np.ones(grid.n, dtype=_np.float32)
    return [1.0] * grid.n


def apply_claim(grid, mask, k, radius_m=9.0, strength=0.75):
    """한 선수가 셀 k를 목표로 잡으면, 그 주변 셀의 가치를 다음 선수에게는
    깎아서 보여준다.

    [왜 필요한가] 이게 없으면 같은 팀 선수 여럿이 "지금 제일 좋은 칸"
    하나로 전부 몰려간다. 예전 코드가 `_advancing_mid_idx`/`_holding_mid_idx`,
    `_check_in_idx`/`_run_behind_idx` 처럼 **어느 선수가 어떤 역할인지를
    손으로 지정**해서 이 문제를 막았는데, 그러다 보니 지정 안 된 선수는
    아무 행동도 안 하게 됐다. 탐욕적 선점은 같은 규칙을 모두에게 적용하면서
    자연스럽게 역할을 분화시킨다 — CM 두 명 중 먼저 고른 쪽이 전진하면
    나머지는 그 칸이 깎여서 다른 선택(후방 커버)을 하게 된다.
    """
    if HAVE_NUMPY:
        dx = grid.gx - grid.gx[k]
        dy = grid.gy - grid.gy[k]
        d = _np.sqrt(dx * dx + dy * dy)
        mask *= 1.0 - strength * _np.clip(1.0 - d / radius_m, 0.0, 1.0)
        return mask
    for j in range(grid.n):
        d = math.hypot(grid.gx[j] - grid.gx[k], grid.gy[j] - grid.gy[k])
        mask[j] *= 1.0 - strength * max(0.0, min(1.0, 1.0 - d / radius_m))
    return mask