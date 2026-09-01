# -*- coding: utf-8 -*-
"""match_sim/positioning.py — 매 틱 22명의 목표 좌표를 공간 가치로 정한다.

## 한 줄 요약

예전:  `target = 포메이션기준점 + 포지션별_전진폭표[포메이션][포지션]`
지금:  `target = argmax_over_cells( 역할가중치 · 공간가치필드 )`

## 무엇이 달라지는가

기존 `_update_player_positions`는 22개의 `if/elif` 가지로 이뤄져 있었고,
각 가지는 미리 지정된 인덱스 한 명에게만 적용됐다(`_presser_idx`,
`_advancing_mid_idx`, `_run_behind_idx`, `_volpiana_idx`…). 어느 가지에도
안 걸린 선수는 `pl["hx"] + adv` 라는 사실상 정적인 목표를 받았고, 그래서
계측상 한 프레임에 유의미하게 움직이는 선수가 평균 1.4명이었다.

여기서는 **22명 전원이 같은 한 줄의 규칙**을 돈다. 각자 다르게 움직이는
이유는 분기가 달라서가 아니라 **가중치와 존이 달라서**다. 그래서:

  * 새 포메이션을 추가해도 코드를 안 고친다 (존 배치만 바뀐다)
  * 새 역할을 추가하려면 `roles.py`에 가중치 한 줄을 넣는다
  * 어느 선수도 "분기에 안 걸려서 얼어있는" 상태가 될 수 없다

## 탐욕적 선점 (greedy claim)

전원이 같은 가치 필드를 보면 다 같은 칸으로 몰려간다. 그래서 선수 한 명이
목표를 정할 때마다 그 주변 칸의 가치를 **다음 선수에게는 깎아서** 보여준다
(`space_model.apply_claim`). 이것 하나가 예전의 손수 지정하던 역할 분화
(`_advancing_mid_idx` vs `_holding_mid_idx`, `_check_in_idx` vs
`_run_behind_idx`)를 통째로 대체한다 — CM 둘 중 먼저 고른 쪽이 전진하면
그 칸이 깎여서 나머지는 자동으로 다른 답(후방 잔류)을 고른다.

선점 순서는 **볼에 가까운 선수부터**다. 실제 축구에서도 플레이에 가까운
선수가 먼저 자리를 정하고 나머지가 거기 맞춰 조정한다.

## 이력 현상 (hysteresis)

매 틱 argmax를 새로 뽑으면, 두 칸의 가치가 엇비슷할 때 프레임마다 목표가
튀어서 선수가 떤다. 예전 코드도 이 문제를 `_committed_ty`로 막고 있었다
(주석: "수비수들이 흐물흐물 움직인다"). 여기서는 **직전 목표의 가치에
보너스를 얹어서** 평가한다 — 새 후보가 확실히 더 좋을 때만 갈아탄다.
결과적으로 "잠깐 버티다가 한 번에 자리를 옮기는" 실제 축구의 패턴이 된다.
"""

import math

from match_sim import space_model as sm
from match_sim.space_model import (
    HAVE_NUMPY, PITCH_LEN_M, PITCH_WID_M, default_grid,
    build_field, make_claim_mask, apply_claim, cell_xy)

if HAVE_NUMPY:
    import numpy as np

# ── 파라미터 ──
CLAIM_RADIUS_M = 9.0     # 선점이 영향을 주는 반경
CLAIM_STRENGTH = 0.70    # 선점 감쇠 강도(0~1)
STICKY_BONUS = 0.05      # 직전 목표에 얹는 보너스(이력 현상)
STICKY_RADIUS_M = 7.0    # 직전 목표 주변 이 반경까지 보너스
PRESS_SIGMA_M = 9.0      # 압박 가치가 볼 주변에서 감쇠하는 스케일
HALFSPACE_SIGMA = 0.075  # 하프스페이스 밴드 폭(정규화 y)

# ══════════════════════════════════════════════════════════════════
#  블록 라인 높이 (line height)
# ══════════════════════════════════════════════════════════════════
#
# [왜 필요한가] 처음 버전에는 이 개념이 아예 없었다. 존(zone)의 중심이
# 포메이션 기준점 hx에 **고정**돼 있어서, 우리 팀이 상대 박스에서 공격
# 중이어도 센터백은 자기 페널티박스 앞에 그대로 서 있었다("수비수가
# 하프라인 뒤에서 구경한다"는 지적 그대로). 전진 가치를 볼 기준으로
# 바꿔놨지만, 볼에서 60m 떨어진 CB의 존 안에는 애초에 전진 가치가 있는
# 칸이 하나도 없어서 필드가 평평해지고 존 중심에 눌러앉은 것이다.
#
# 실제 축구에서 팀은 **블록 전체가 볼을 따라 오르내린다**. 공격 시에는
# 백라인이 볼보다 30~40m 뒤까지 밀고 올라가 피치를 압축하고(그래야
# 세컨볼을 줍고 상대가 빠져나가지 못한다), 수비 시에는 볼과 자기 골문
# 사이로 내려앉는다.
#
# 그래서 존 중심을 고정 hx가 아니라 **"목표 백라인 + 그 선수의 고유
# 깊이"**로 계산한다. 대형의 모양(선수 간 상대적 깊이)은 그대로 유지한
# 채로 블록만 통째로 미끄러진다.
#
# 아래 숫자는 실축 트래킹의 대략적 값이다:
#   공격 시 백라인 ≈ 볼 - 34m,  하한 자기 박스 앞 / 상한 상대 진영 초입
#   수비 시 백라인 ≈ 볼 - 15m,  더 낮게 내려앉는다
ATK_BACKLINE_GAP = 0.37   # 공격 시 백라인이 볼보다 이만큼 뒤 (정규화 x ≈ 36m)
DEF_BACKLINE_GAP = 0.15   # 수비 시 (≈ 16m)
ATK_BACKLINE_MIN, ATK_BACKLINE_MAX = 0.13, 0.56
DEF_BACKLINE_MIN, DEF_BACKLINE_MAX = 0.10, 0.46
# 블록이 목표 라인으로 얼마나 따라가는가. 1.0이면 완전히 따라간다.
# 선수별 깊이는 유지되므로 대형은 안 무너진다.
LINE_FOLLOW = 0.75


# ══════════════════════════════════════════════════════════════════
#  정적 필드 — 격자에만 의존하므로 한 번 계산해서 캐시한다
# ══════════════════════════════════════════════════════════════════
class _StaticFields:
    def __init__(self, grid):
        self.grid = grid
        if HAVE_NUMPY:
            cy = grid.cy
            # 하프스페이스: y ≈ 0.28 / 0.72 두 밴드
            self.halfspace = np.maximum(
                np.exp(-0.5 * ((cy - 0.28) / HALFSPACE_SIGMA) ** 2),
                np.exp(-0.5 * ((cy - 0.72) / HALFSPACE_SIGMA) ** 2))
            # 폭: 터치라인에 가까울수록 1
            self.width = np.clip(np.abs(cy - 0.5) / 0.44, 0.0, 1.0)
        else:
            self.halfspace = [max(math.exp(-0.5 * ((v - 0.28) / HALFSPACE_SIGMA) ** 2),
                                  math.exp(-0.5 * ((v - 0.72) / HALFSPACE_SIGMA) ** 2))
                              for v in grid.cy]
            self.width = [min(1.0, abs(v - 0.5) / 0.44) for v in grid.cy]


_STATIC = None


def _static(grid):
    global _STATIC
    if _STATIC is None or _STATIC.grid is not grid:
        _STATIC = _StaticFields(grid)
    return _STATIC


_BACKLINE_POS = {"CB", "LB", "RB", "LWB", "RWB"}


def _zone_aff_dyn(grid, pl, r, dyn_hx):
    """존 가중치 — 중심 x는 매 틱 바뀌고(블록 라인 높이), 중심 y는 고정이다.

    [성능] 격자가 (ny × nx) 순서로 평탄화돼 있으므로 x 성분은 nx개(32),
    y 성분은 ny개(20)만 계산하면 된다. y 성분은 경기 내내 안 변하니
    선수 dict에 캐시하고, x 성분만 매 틱 32개 지수 연산으로 다시 만든다.
    640칸을 통째로 다시 계산하는 것보다 훨씬 싸다.
    """
    if HAVE_NUMPY:
        key = (id(grid), pl["hy"], r["sigma_y"], r["zone"])
        c = pl.get("_zone_y_cache")
        if c is None or c[0] != key:
            dy = (np.asarray(grid.ys, dtype=np.float32) - pl["hy"]) * PITCH_WID_M / r["sigma_y"]
            zy = np.exp(-0.5 * dy * dy) ** r["zone"]
            c = (key, np.repeat(zy, grid.nx))
            pl["_zone_y_cache"] = c
        dx = (np.asarray(grid.xs, dtype=np.float32) - dyn_hx) * PITCH_LEN_M / r["sigma_x"]
        zx = np.tile(np.exp(-0.5 * dx * dx) ** r["zone"], grid.ny)
        return zx * c[1]
    out = []
    for k in range(grid.n):
        dx = (grid.cx[k] - dyn_hx) * PITCH_LEN_M / r["sigma_x"]
        dy = (grid.cy[k] - pl["hy"]) * PITCH_WID_M / r["sigma_y"]
        out.append(math.exp(-0.5 * (dx * dx + dy * dy)) ** r["zone"])
    return out


def _zone_aff_cached(grid, pl, r):
    """[성능] 존 가중치 `zone_affinity ** zone_weight` 는 선수의 포메이션
    기준점(hx, hy)과 역할 상수에만 의존한다 — 경기 내내 변하지 않는다.
    그런데 매 틱 22명분을 새로 계산하면 640칸짜리 지수 연산이 경기당
    22 × 4801 = 10만 번 돈다(전체 시간의 큰 몫). 선수 dict에 캐시한다.
    """
    key = (id(grid), pl["hx"], pl["hy"], r["sigma_x"], r["sigma_y"], r["zone"])
    c = pl.get("_zone_cache")
    if c is not None and c[0] == key:
        return c[1]
    z = sm._zone_affinity(grid, pl["hx"], pl["hy"], r["sigma_x"], r["sigma_y"])
    if HAVE_NUMPY:
        z = z ** r["zone"]
    else:
        z = [v ** r["zone"] for v in z]
    pl["_zone_cache"] = (key, z)
    return z


def _gauss_around(grid, x, y, sigma_m):
    """(x, y) 주변의 가우시안 — 압박/골사이드 계산용."""
    gx, gy = x * PITCH_LEN_M, y * PITCH_WID_M
    if HAVE_NUMPY:
        dx = grid.gx - gx
        dy = grid.gy - gy
        return np.exp(-0.5 * (dx * dx + dy * dy) / (sigma_m * sigma_m))
    return [math.exp(-0.5 * ((grid.gx[k] - gx) ** 2 + (grid.gy[k] - gy) ** 2)
                     / (sigma_m * sigma_m)) for k in range(grid.n)]


def _goalside(grid, ball_x, own_goal_x):
    """볼과 자기 골문 사이의 칸일수록 높다 — 수비 시 '골 쪽에 서기'."""
    if HAVE_NUMPY:
        if own_goal_x < 0.5:
            v = (ball_x - grid.cx) / 0.30
        else:
            v = (grid.cx - ball_x) / 0.30
        return np.clip(v, 0.0, 1.0)
    out = []
    for k in range(grid.n):
        v = ((ball_x - grid.cx[k]) if own_goal_x < 0.5 else (grid.cx[k] - ball_x)) / 0.30
        out.append(max(0.0, min(1.0, v)))
    return out


REFINE_RADIUS_M = 7.0


def _refine(grid, val, k):
    """[연속 목표] argmax 칸의 **중심**을 그대로 목표로 쓰면, 목표가 3.3m
    격자에 양자화된다 — 필드가 조금 변해도 목표가 안 움직이다가 갑자기 한
    칸씩 튄다(계측: 정지 시간 90%). 최고 칸 주변의 가치 가중 중심을 써서
    목표가 연속적으로 흐르게 한다."""
    if HAVE_NUMPY:
        dx = grid.gx - grid.gx[k]
        dy = grid.gy - grid.gy[k]
        near = (dx * dx + dy * dy) <= REFINE_RADIUS_M ** 2
        w = val[near]
        w = w - w.min()
        tot = w.sum()
        if tot <= 1e-9:
            return float(grid.cx[k]), float(grid.cy[k])
        return (float((grid.cx[near] * w).sum() / tot),
                float((grid.cy[near] * w).sum() / tot))
    num_x = num_y = tot = 0.0
    vmin = 1e30
    idxs = []
    for j in range(grid.n):
        if (grid.gx[j] - grid.gx[k]) ** 2 + (grid.gy[j] - grid.gy[k]) ** 2 <= REFINE_RADIUS_M ** 2:
            idxs.append(j)
            vmin = min(vmin, val[j])
    for j in idxs:
        w = val[j] - vmin
        num_x += grid.cx[j] * w
        num_y += grid.cy[j] * w
        tot += w
    if tot <= 1e-9:
        return grid.cx[k], grid.cy[k]
    return num_x / tot, num_y / tot


def _argmax(vals):
    if HAVE_NUMPY:
        return int(np.argmax(vals))
    best, bi = -1e30, 0
    for k, v in enumerate(vals):
        if v > best:
            best, bi = v, k
    return bi


# ══════════════════════════════════════════════════════════════════
#  메인 — 한 틱 분의 목표 좌표
# ══════════════════════════════════════════════════════════════════
def compute_targets(home_players, away_players, ball_x, ball_y,
                    possession, home_roles, away_roles,
                    skip_home=(), skip_away=(), grid=None,
                    home_atk_goal_x=1.0):
    """22명의 목표 좌표를 계산해 {"home": [(x,y)|None], "away": [...]} 로 돌려준다.

    skip_* 에 든 인덱스는 None을 돌려준다(씬/코너 크라우드/GK락처럼 다른
    코드가 좌표를 직접 통제하는 선수 — 여기서 덮어쓰면 안 된다).

    선수 dict에는 `vmax_ms`(m/s)와 `_tgt_cell`(직전 목표 셀, 이력 현상용)이
    있어야 한다. 없으면 기본값으로 동작한다.
    """
    grid = grid or default_grid()
    st = _static(grid)
    field = build_field(home_players, away_players, ball_x, ball_y,
                        home_atk_goal_x=home_atk_goal_x, grid=grid)
    press_field = _gauss_around(grid, ball_x, ball_y, PRESS_SIGMA_M)

    out = {}
    for side in ("home", "away"):
        team = home_players if side == "home" else away_players
        roles = home_roles if side == "home" else away_roles
        skip = set(skip_home if side == "home" else skip_away)
        in_poss = (side == possession)

        atk_goal_x = home_atk_goal_x if side == "home" else (1.0 - home_atk_goal_x)
        own_goal_x = 1.0 - atk_goal_x

        if side == "home":
            ctrl_own, threat_own, prog_own, pass_own = (
                field.ctrl_home, field.threat_home, field.prog_home, field.pass_feas_home)
            threat_opp, pass_opp = field.threat_away, field.pass_feas_away
        else:
            ctrl_own, threat_own, prog_own, pass_own = (
                field.ctrl_away, field.threat_away, field.prog_away, field.pass_feas_away)
            threat_opp, pass_opp = field.threat_home, field.pass_feas_home

        goalside = _goalside(grid, ball_x, own_goal_x)

        # ── 블록 라인 높이 ──
        # 공격 방향을 +로 하는 좌표 u로 바꿔서 계산한 뒤 되돌린다.
        # (원정팀은 x가 감소하는 쪽이 공격 방향이라 그대로 쓰면 부호가 뒤집힌다.)
        _fwd = 1.0 if atk_goal_x > 0.5 else -1.0
        def _to_u(x):
            return x if _fwd > 0 else (1.0 - x)
        def _from_u(u):
            return u if _fwd > 0 else (1.0 - u)

        _back_idx = [i for i, p in enumerate(team) if p["pos"] in _BACKLINE_POS]
        if _back_idx:
            _cur_back_u = sum(_to_u(team[i]["hx"]) for i in _back_idx) / len(_back_idx)
            _ball_u = _to_u(ball_x)
            if in_poss:
                _tgt_back_u = min(ATK_BACKLINE_MAX, max(ATK_BACKLINE_MIN,
                                                        _ball_u - ATK_BACKLINE_GAP))
            else:
                _tgt_back_u = min(DEF_BACKLINE_MAX, max(DEF_BACKLINE_MIN,
                                                        _ball_u - DEF_BACKLINE_GAP))
            _shift_u = (_tgt_back_u - _cur_back_u) * LINE_FOLLOW
        else:
            _shift_u = 0.0

        # 수비 국면의 "위험한 칸" — 상대에게 가치가 높은데 우리가 아직
        # 점유하지 못한 칸. 이걸 미리 밟는 게 수비 포지셔닝이다.
        if HAVE_NUMPY:
            danger = threat_opp * (0.35 + 0.65 * pass_opp) * (1.0 - ctrl_own)
        else:
            danger = [threat_opp[k] * (0.35 + 0.65 * pass_opp[k]) * (1.0 - ctrl_own[k])
                      for k in range(grid.n)]

        mask = make_claim_mask(grid)

        # 선점 순서: 볼에 가까운 선수부터.
        order = sorted(
            (i for i in range(len(team)) if i not in skip),
            key=lambda i: (team[i]["x"] - ball_x) ** 2 + (team[i]["y"] - ball_y) ** 2)

        targets = [None] * len(team)
        for i in order:
            pl = team[i]
            if pl["pos"] == "GK":
                continue          # GK는 엔진의 스위퍼 로직이 따로 처리한다
            r = roles[i]
            vmax = pl.get("vmax_ms", r["vmax"])

            # 블록 이동을 반영한 동적 존 중심. 대형의 상대적 깊이는
            # 그대로 유지되고 블록만 통째로 미끄러진다.
            dyn_hx = min(0.93, max(0.05, _from_u(_to_u(pl["hx"]) + _shift_u)))
            zone = _zone_aff_dyn(grid, pl, r, dyn_hx)
            travel = sm._travel_cost(grid, pl, vmax)

            if in_poss:
                if HAVE_NUMPY:
                    base = (r["control"] * ctrl_own
                            + r["prog"] * prog_own
                            + r["threat"] * threat_own
                            + r["pass_"] * pass_own
                            + r["halfspace"] * st.halfspace
                            + r["width"] * st.width)
                else:
                    base = [(r["control"] * ctrl_own[k] + r["prog"] * prog_own[k]
                             + r["threat"] * threat_own[k] + r["pass_"] * pass_own[k]
                             + r["halfspace"] * st.halfspace[k] + r["width"] * st.width[k])
                            for k in range(grid.n)]
            else:
                if HAVE_NUMPY:
                    base = (r["deny"] * danger
                            + r["press"] * press_field
                            + r["goalside"] * goalside * (0.4 + 0.6 * ctrl_own))
                else:
                    base = [(r["deny"] * danger[k] + r["press"] * press_field[k]
                             + r["goalside"] * goalside[k] * (0.4 + 0.6 * ctrl_own[k]))
                            for k in range(grid.n)]

            # 존은 곱셈으로 — 가중치가 지수라서, 낮으면 자유롭고 높으면
            # 기준점에 붙는다. (덧셈으로 하면 존 밖 가치가 아무리 낮아도
            # 존 안 가치와 더해져 버려서 "존"의 의미가 사라진다.)
            if HAVE_NUMPY:
                val = base * mask * zone - r["cost"] * travel
            else:
                val = [base[k] * mask[k] * zone[k] - r["cost"] * travel[k]
                       for k in range(grid.n)]

            # 이력 현상 — 직전 목표 근처에 보너스
            prev = pl.get("_tgt_cell")
            if prev is not None and 0 <= prev < grid.n:
                if HAVE_NUMPY:
                    dx = grid.gx - grid.gx[prev]
                    dy = grid.gy - grid.gy[prev]
                    d = np.sqrt(dx * dx + dy * dy)
                    val = val + STICKY_BONUS * np.clip(1.0 - d / STICKY_RADIUS_M, 0.0, 1.0)
                else:
                    for k in range(grid.n):
                        d = math.hypot(grid.gx[k] - grid.gx[prev], grid.gy[k] - grid.gy[prev])
                        val[k] += STICKY_BONUS * max(0.0, min(1.0, 1.0 - d / STICKY_RADIUS_M))

            k = _argmax(val)
            pl["_tgt_cell"] = k
            targets[i] = _refine(grid, val, k)
            mask = apply_claim(grid, mask, k, CLAIM_RADIUS_M, CLAIM_STRENGTH)

        out[side] = targets

    return out, field


CARRY_MIN_M = 8.0     # 홀더 목표의 최소 거리 — 이보다 가까우면 "도착해서 멈춤"이 된다
CARRY_MAX_M = 18.0    # 최대 거리 — 이보다 멀면 순간이동처럼 보인다


def dribble_target(holder, field, grid, atk_goal_x):
    """볼 보유자 전용 — 자기 주변 **고리(annulus)** 안에서 가장 좋은 칸으로
    몰고 간다.

    [왜 고리인가] 처음엔 "반경 14m 안에서 argmax"로 만들었는데, 그러면
    지금 서 있는 자리가 이미 최적일 때 목표가 자기 자신이 되어 홀더가
    **그 자리에 선다**. 홀더가 서면 볼이 서고, 볼이 서면 볼 기준으로
    만들어지는 22명의 가치 필드 전체가 정지한다(실측: 볼 중앙값 속도
    0.98 m/s, 볼 정지 시간 38%). 실제 축구에서 볼은 거의 항상 움직인다.

    최소 거리를 강제하면 홀더는 항상 "어디론가 가는 중"이 되고, 그 결과
    필드가 매 틱 흐르면서 나머지 21명도 계속 재조정하게 된다.

    홀더는 존(포메이션 기준점)을 무시한다 — 볼을 가진 선수는 어디로든
    갈 수 있다.
    """
    grid = grid or default_grid()
    px, py = holder["x"] * PITCH_LEN_M, holder["y"] * PITCH_WID_M
    gx = atk_goal_x * PITCH_LEN_M
    gy = 0.5 * PITCH_WID_M

    if HAVE_NUMPY:
        d = np.sqrt((grid.gx - px) ** 2 + (grid.gy - py) ** 2)
        ring = (d >= CARRY_MIN_M) & (d <= CARRY_MAX_M)
        if not ring.any():
            return holder["x"], holder["y"]
        forward = grid.cx if atk_goal_x > 0.5 else (1.0 - grid.cx)
        goal_d = np.sqrt((grid.gx - gx) ** 2 + (grid.gy - gy) ** 2)
        near_goal = np.clip(1.0 - goal_d / 50.0, 0.0, 1.0)
        # 중앙 쏠림 방지: 터치라인 바로 옆으로 몰고 가는 건 감점
        edge = np.clip(1.0 - np.abs(grid.cy - 0.5) / 0.46, 0.0, 1.0)
        val = np.where(ring, 0.45 * forward + 0.40 * near_goal + 0.15 * edge, -1e9)
        k = int(np.argmax(val))
    else:
        best, k = -1e30, None
        for j in range(grid.n):
            dj = math.hypot(grid.gx[j] - px, grid.gy[j] - py)
            if dj < CARRY_MIN_M or dj > CARRY_MAX_M:
                continue
            forward = grid.cx[j] if atk_goal_x > 0.5 else (1.0 - grid.cx[j])
            goal_d = math.hypot(grid.gx[j] - gx, grid.gy[j] - gy)
            near = max(0.0, min(1.0, 1.0 - goal_d / 50.0))
            edge = max(0.0, min(1.0, 1.0 - abs(grid.cy[j] - 0.5) / 0.46))
            v = 0.45 * forward + 0.40 * near + 0.15 * edge
            if v > best:
                best, k = v, j
        if k is None:
            return holder["x"], holder["y"]
    return cell_xy(grid, k)