# -*- coding: utf-8 -*-
"""match_sim/live/ball.py — 공을 독립 물리 객체로 다룬다.

## 왜 이게 바뀌어야 했나

기존 엔진에서 공은 물체가 아니라 **상태**였다.

    self.ball["x"] = holder["x"] + lead_x      # 공 = 보유자 발밑
    self.holder = new_holder                   # 패스 = 보유자 교체

즉 "누가 공을 가졌는가"가 먼저 정해지고 공은 거기 따라붙었다. 이 구조에서는
원리적으로 존재할 수 없는 것들이 있다:

  * **루즈볼** — 아무도 안 가진 공이라는 상태 자체가 없다
  * **50:50** — 두 선수가 같은 공을 향해 달리는 상황이 없다
  * **인터셉트** — 패스는 지정한 동료에게 순간이동하므로 중간에 끊길 수 없다
  * **세컨볼 / 스크램블 / 굴절**
  * **뒷공간으로 굴러가는 스루패스** — 패스는 사람에게만 갈 수 있었다

실제 축구에서 선수가 전력질주하는 이유의 상당 부분이 이 목록에 있다. 공이
물체가 아니면 그 이유들이 통째로 사라지고, 남는 건 "각자 좋은 자리에 서
있기"뿐이다 — 그게 지금까지 화면이 허전했던 물리적 원인이다.

여기서 공은 위치·속도·높이를 가진 물체이고, **소유는 상태가 아니라 결과**다.
매 틱 "이 공에 누가 먼저, 어떤 조건으로 닿는가"를 계산해서 컨트롤 여부가
정해진다. 아무도 못 잡으면 공은 그냥 굴러간다.

## 좌표계

엔진/뷰어와 동일하게 x, y는 정규화 [0,1]이다. 다만 **속도와 물리는 전부
미터 단위**로 계산한다 — 정규화 속도는 x축(105m)과 y축(68m)에서 뜻이 달라져
물리를 쓸 수 없기 때문이다. `x, y`(정규화)와 `vx, vy`(m/s)를 함께 들고
다니고, 변환은 이 파일 안에서만 한다.

z는 미터(지면 0). 높이가 있어야 로빙 패스/크로스/슛이 수비 다리를 넘어가고,
헤딩과 그라운드 인터셉트가 구분된다.
"""

import math
import random

PITCH_LEN_M = 105.0
PITCH_WID_M = 68.0

GRAVITY = 9.81
# 잔디 위 구름 마찰 감속(m/s²). 실측 대략 0.4~0.6.
ROLL_FRICTION = 0.55
# 공기 저항 계수(속도 제곱에 비례). 느슨하게.
AIR_DRAG = 0.012
# 바운스 반발계수(수직) / 수평 마찰
BOUNCE_RESTITUTION = 0.55
BOUNCE_FRICTION = 0.78

# 컨트롤 가능 반경(m) — 이 안에 들어와야 트래핑 시도 자체가 가능
CONTROL_RADIUS_M = 1.6
# 이 높이 위로 뜬 공은 발로 못 잡는다(헤딩/가슴 트래핑은 별도 취급)
CONTROL_MAX_Z = 2.2
# 컨트롤 직후 이 시간(초) 동안은 다시 뺏기지 않는다(연속 탈취 방지)
CONTROL_GRACE_S = 0.25


class Ball:
    """공. 소유자가 있으면 그 발밑에 붙고, 없으면 자유 물체로 굴러간다."""

    __slots__ = ("x", "y", "z", "vx", "vy", "vz",
                 "carrier", "last_touch", "free_since", "_grace",
                 "out_of_play", "out_kind", "out_x", "out_y",
                 "prev_mx", "prev_my")

    def __init__(self, x=0.5, y=0.5):
        self.x, self.y, self.z = x, y, 0.0
        self.vx = self.vy = self.vz = 0.0
        # carrier: None 이거나 (side, idx). None이면 자유공(루즈볼).
        self.carrier = None
        # last_touch: 마지막으로 건드린 (side, idx) — 아웃 판정에 필요하다
        # ("누가 마지막으로 찼는가"가 스로인/코너/골킥의 주체를 정한다).
        self.last_touch = None
        self.free_since = 0.0
        self._grace = 0.0
        self.out_of_play = False
        self.out_kind = None       # "throw_in" / "corner" / "goal_kick" / "goal"
        self.out_x = self.out_y = 0.0
        self.prev_mx, self.prev_my = self.mx, self.my

    # ── 좌표 변환 ──
    @property
    def mx(self):
        return self.x * PITCH_LEN_M

    @property
    def my(self):
        return self.y * PITCH_WID_M

    def set_m(self, mx, my):
        self.x = mx / PITCH_LEN_M
        self.y = my / PITCH_WID_M

    def speed(self):
        return math.hypot(self.vx, self.vy)

    # ── 공 차기 ──
    def kick(self, toward_mx, toward_my, speed_ms, loft_deg=0.0, by=None):
        """지점을 향해 찬다. loft_deg가 0보다 크면 떠서 날아간다."""
        dx = toward_mx - self.mx
        dy = toward_my - self.my
        d = math.hypot(dx, dy)
        if d < 1e-6:
            dx, dy, d = 1.0, 0.0, 1.0
        rad = math.radians(loft_deg)
        horiz = speed_ms * math.cos(rad)
        self.vx = dx / d * horiz
        self.vy = dy / d * horiz
        self.vz = speed_ms * math.sin(rad)
        self.carrier = None
        self.free_since = 0.0
        self._grace = 0.0
        if by is not None:
            self.last_touch = by

    def attach(self, side, idx, now=0.0):
        """선수가 공을 컨트롤했다."""
        self.carrier = (side, idx)
        self.last_touch = (side, idx)
        self.vx = self.vy = self.vz = 0.0
        self.z = 0.0
        self._grace = CONTROL_GRACE_S

    # ── 물리 진행 ──
    def advance(self, dt, carrier_pos=None, carrier_vel=None):
        """한 틱 진행.

        carrier가 있으면 물리를 안 돌리고 그 선수 발밑(진행 방향으로 살짝
        앞)에 붙인다 — 드리블. 없으면 자유 물체로 적분한다.
        """
        if self._grace > 0:
            self._grace = max(0.0, self._grace - dt)
        self.prev_mx, self.prev_my = self.mx, self.my

        if self.carrier is not None and carrier_pos is not None:
            px, py = carrier_pos
            if carrier_vel is not None:
                vmag = math.hypot(*carrier_vel)
                if vmag > 0.2:
                    # 드리블 시 공은 진행 방향 약 1.1m 앞
                    lead = 1.1
                    px += carrier_vel[0] / vmag * lead / PITCH_LEN_M
                    py += carrier_vel[1] / vmag * lead / PITCH_WID_M
            # [버그 수정 — 결정적] 드리블 중 공은 선수보다 1.1m 앞에 놓이는데
            # 여기에 경계 클램프가 없었다. 선수는 x<=0.995로 클램프되지만
            # 공은 x=1.005가 되어 **골라인을 넘고**, 중앙이면 그대로 골로
            # 기록됐다. 슛을 경기당 1개로 줄여도 골이 115개 나오던 원인이
            # 전부 이것이다(드리블러가 공을 몰고 골문 안으로 들어감).
            # 발밑에 붙어 있는 공은 정의상 인플레이다.
            self.x = min(0.997, max(0.003, px))
            self.y = min(0.997, max(0.003, py))
            self.z = 0.0
            self.vx = self.vy = self.vz = 0.0
            return

        # ── 자유공 ──
        self.free_since += dt
        sp = self.speed()

        if self.z > 1e-4 or self.vz > 1e-4:
            # 공중 — 중력 + 공기저항
            self.vz -= GRAVITY * dt
            drag = AIR_DRAG * sp * dt
            if sp > 1e-6:
                self.vx -= self.vx / sp * drag * sp
                self.vy -= self.vy / sp * drag * sp
            self.z += self.vz * dt
            if self.z <= 0.0:
                self.z = 0.0
                if self.vz < -1.0:
                    self.vz = -self.vz * BOUNCE_RESTITUTION
                    self.vx *= BOUNCE_FRICTION
                    self.vy *= BOUNCE_FRICTION
                else:
                    self.vz = 0.0
        else:
            # 지면 구름 — 마찰 감속
            if sp > 1e-6:
                dec = ROLL_FRICTION * dt
                nsp = max(0.0, sp - dec)
                self.vx *= nsp / sp
                self.vy *= nsp / sp

        self.set_m(self.mx + self.vx * dt, self.my + self.vy * dt)

    # ── 라인 아웃 판정 ──
    def check_out(self, home_atk_goal_x=1.0, goal_half_w=0.054):
        """터치라인/골라인을 넘었는지 본다. 넘었으면 out_of_play를 세운다.

        [중요] 예전 엔진은 좌표를 항상 [0.03, 0.97] × [0.05, 0.95]로 클램프
        해서 "밖으로 나간다"는 개념이 아예 없었고, 스로인/코너는 확률로
        억지로 발생시켰다(`random.random() < 0.22`). 여기서는 공이 실제로
        라인을 넘으면 나가는 것이다 — 그래서 재개 위치도 실제 나간 지점이다.
        """
        if self.out_of_play:
            return self.out_kind
        x, y = self.x, self.y
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            return None

        self.out_of_play = True
        self.out_x = min(1.0, max(0.0, x))
        self.out_y = min(1.0, max(0.0, y))

        if y < 0.0 or y > 1.0:
            self.out_kind = "throw_in"
        else:
            # 골라인. 골문 안이면 골.
            if abs(y - 0.5) <= goal_half_w and self.z <= 2.44:
                self.out_kind = "goal"
            else:
                self.out_kind = "byline"   # 코너인지 골킥인지는 마지막 터치로
        self.vx = self.vy = self.vz = 0.0
        return self.out_kind

    def reset_in_play(self, x, y):
        self.x, self.y, self.z = x, y, 0.0
        self.vx = self.vy = self.vz = 0.0
        self.carrier = None
        self.out_of_play = False
        self.out_kind = None
        self.free_since = 0.0


# ══════════════════════════════════════════════════════════════════
#  소유권 경쟁 — "누가 공에 먼저, 어떤 조건으로 닿는가"
# ══════════════════════════════════════════════════════════════════
def contest(ball, teams, now, rng=random):
    """자유공에 대해 컨트롤을 시도한다. 성공하면 (side, idx), 아니면 None.

    [설계] 소유권을 미리 정해두고 공을 붙이는 게 아니라, **공 근처에 실제로
    있는 선수들 중에서** 결정된다. 그래서:

      * 두 팀 선수가 동시에 도달하면 진짜 50:50이 된다
      * 패스 경로에 상대가 서 있으면 자동으로 인터셉트가 된다
        (별도 인터셉트 로직이 필요 없다 — 그냥 그 선수가 먼저 닿는 것)
      * 아무도 못 닿으면 공은 계속 굴러간다(루즈볼)

    컨트롤 성공 확률은 (1) 공과의 거리, (2) 공의 속도(빠른 공은 트래핑이
    어렵다), (3) 공의 높이, (4) 선수의 first_touch/tackling 스탯으로 정해진다.
    실패하면 공은 살짝 굴절돼서 튄다 — 이게 세컨볼을 만든다.

    teams: {"home": [player...], "away": [player...]}
           player는 {"x","y","vx","vy","stats"} 를 갖는 dict.
    """
    if ball.carrier is not None or ball._grace > 0 or ball.out_of_play:
        return None

    # [버그 수정 — 터널링] 예전엔 **공의 현재 위치**만 보고 반경 안에
    # 선수가 있는지 판정했다. 물리 스텝이 0.12초인데 슛은 25 m/s라 한
    # 스텝에 3m를 이동한다 — 즉 골키퍼(반경 2.9m)를 그냥 **뛰어넘어**
    # 지나갈 수 있었다. 실측: 실점 순간 골키퍼가 골라인 2.1m 안, 공과
    # y거리 1.1m 안에 있었던 비율이 97%인데도 슛 100개 중 87개가 골이
    # 됐다. 골키퍼가 제자리에 있는데 공이 통과한 것이다.
    # 빠른 패스가 수비수를 그냥 지나쳐 인터셉트가 안 생기던 것도 같은 원인.
    # 이제 이번 스텝에 공이 **지나간 선분** 전체에 대해 최근접 거리를 잰다.
    ax, ay = ball.prev_mx, ball.prev_my
    bx2, by2 = ball.mx, ball.my
    sx, sy = bx2 - ax, by2 - ay
    seg2 = sx * sx + sy * sy
    cands = []
    for side, team in teams.items():
        for i, p in enumerate(team):
            reach = p.get("reach", CONTROL_RADIUS_M)
            # 빠르게 지나가는 공에는 발을 뻗어 건드릴 수 있다(인터셉트).
            # 이게 없으면 패스가 수비수 옆을 스쳐도 아무 일이 안 일어나서
            # 패스 성공률이 90%로 고정된다(실축 78~85%, 파이널서드는 더 낮다).
            if ball.speed() > 6.0:
                reach += 0.7
            if ball.z > p.get("max_z", CONTROL_MAX_Z):
                continue
            pxm, pym = p["x"] * PITCH_LEN_M, p["y"] * PITCH_WID_M
            if seg2 < 1e-9:
                dm = math.hypot(pxm - ax, pym - ay)
            else:
                t = ((pxm - ax) * sx + (pym - ay) * sy) / seg2
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                dm = math.hypot(pxm - (ax + t * sx), pym - (ay + t * sy))
            if dm <= reach:
                # 접촉 지점(선분 위)도 같이 기록한다 — 아래에서 공을 그
                # 지점으로 되돌려야 한다.
                if seg2 < 1e-9:
                    hit = (ax, ay)
                else:
                    hit = (ax + t * sx, ay + t * sy)
                cands.append((dm, side, i, p, hit))
    if not cands:
        return None

    cands.sort(key=lambda c: c[0])
    bspeed = ball.speed()

    for dm, side, i, p, hit in cands:
        st = p.get("stats") or {}

        # ── 골키퍼 선방 ──
        # [왜 별도인가] 일반 트래핑 공식은 공 속도에 강하게 페널티를 준다
        # (`-0.030 * bspeed`). 슛은 20~30 m/s라 그 공식으로는 성공 확률이
        # 사실상 0이 되어 온타깃 슛이 전부 골이 됐다(실측: 슛 48회에 골 23).
        # 실제 골키퍼는 빠른 공을 "깔끔하게 트래핑"하는 게 아니라 **막는다** —
        # 잡거나(캐치), 쳐내거나(펀칭/파리), 흘린다. 쳐낸 공은 다시 살아
        # 있는 공이 되어 리바운드/코너를 만든다.
        if p.get("pos") == "GK" and bspeed > 8.0:
            skill = st.get("reflexes", st.get("handling", 55)) / 100.0
            reach = p.get("reach", CONTROL_RADIUS_M)
            # 몸에 가까울수록, 반사신경이 좋을수록, 공이 느릴수록 유리
            # 실축 골키퍼는 온타깃 슛의 약 70%를 막는다.
            p_stop = (1.06 - 0.34 * (dm / reach) - 0.008 * bspeed) * (0.70 + 0.6 * skill)
            p_stop = max(0.05, min(0.94, p_stop))
            if rng.random() < p_stop:
                # [버그 수정 — 결정적] 접촉 지점으로 공을 되돌린다.
                # 경합은 이번 스텝에 공이 **지나간 선분**에 대해 판정하는데,
                # 판정 시점의 공 위치는 이미 그 선분 끝(= 골라인 너머)이다.
                # 되돌리지 않으면 "골키퍼가 쳐냈다"고 판정한 직후 곧바로
                # check_out이 골로 기록한다 — 실측: 골키퍼가 슛의 87%에
                # 반응했는데도 골이 97개였던 이유가 이것이다.
                ball.set_m(*hit)
                if rng.random() < 0.42:
                    ball.attach(side, i, now)          # 깔끔하게 캐치
                    return (side, i)
                # 펀칭 — 쳐낸 공은 다시 살아 있다(리바운드/코너의 원천).
                # [버그 수정] 처음엔 방향을 완전 랜덤으로 줬는데, 골문 바로
                # 앞에서 랜덤이면 40% 확률로 **자기 골대 안쪽**을 향한다.
                # 그래서 선방한 공이 그대로 굴러 들어가 경기당 50골이 나왔다.
                # 실제 선방은 공을 골문에서 **멀어지는 쪽**(측면 또는 위)으로
                # 쳐낸다.
                gx_dir = 1.0 if ball.mx < PITCH_LEN_M / 2 else -1.0
                ang = rng.uniform(-1.15, 1.15)      # 골문 반대 방향 ±66°
                mag = bspeed * rng.uniform(0.25, 0.50)
                ball.vx = math.cos(ang) * mag * gx_dir
                ball.vy = math.sin(ang) * mag
                ball.vz = rng.uniform(0.5, 3.0)
                ball.last_touch = (side, i)
                ball._grace = 0.12
                return None
            continue    # 못 막았다 — 다음 후보(보통 없음)로

        # 트래핑 능력 — first_touch가 없으면 dribbling으로 대체
        # 골키퍼는 선방 능력(reflexes/handling)으로 판정한다
        if p.get("pos") == "GK":
            skill = st.get("reflexes", st.get("handling", 55)) / 100.0
        else:
            skill = st.get("first_touch", st.get("dribbling", 50)) / 100.0
        # 거리(가까울수록 유리), 공 속도(빠를수록 불리), 높이(뜰수록 불리)
        reach = p.get("reach", CONTROL_RADIUS_M)
        p_ctrl = (0.92
                  - 0.35 * (dm / reach)
                  - 0.030 * bspeed
                  - 0.10 * min(1.0, ball.z / p.get("max_z", CONTROL_MAX_Z)))
        p_ctrl *= 0.72 + 0.55 * skill
        # 자기 팀이 마지막으로 찼으면(패스 수신) 예측하고 있었으므로 유리
        if ball.last_touch is not None and ball.last_touch[0] == side:
            p_ctrl += 0.16
        p_ctrl = max(0.03, min(0.96, p_ctrl))

        if rng.random() < p_ctrl:
            ball.set_m(*hit)          # 접촉 지점에서 잡는다(위 설명과 동일)
            ball.attach(side, i, now)
            return (side, i)
        # 실패 — 공이 굴절된다(세컨볼). 완전히 무시하고 지나가지 않는다.
        if rng.random() < 0.55:
            ball.set_m(*hit)
            ang = rng.uniform(0, 2 * math.pi)
            mag = max(1.5, bspeed * 0.45)
            ball.vx = math.cos(ang) * mag
            ball.vy = math.sin(ang) * mag
            ball.vz = max(ball.vz, rng.uniform(0.0, 1.6))
            ball.last_touch = (side, i)
            return None
    return None


def time_to_reach(px, py, pvx, pvy, vmax_ms, tmx, tmy, reaction_s=0.25):
    """선수가 (tmx, tmy)[미터]에 도달하는 데 걸리는 시간(초).

    현재 속도를 반영한다 — 이미 그쪽으로 달리고 있으면 빨리, 반대로 달리고
    있으면 느리게. 패스 판단과 소유권 예측의 기본 단위다.
    """
    sx = px * PITCH_LEN_M + pvx * PITCH_LEN_M * reaction_s
    sy = py * PITCH_WID_M + pvy * PITCH_WID_M * reaction_s
    d = math.hypot(tmx - sx, tmy - sy)
    return reaction_s + d / max(1e-3, vmax_ms)


def ball_travel_time(ball, tmx, tmy, kick_speed):
    """공을 kick_speed로 찼을 때 그 지점까지 걸리는 시간(초).

    구름 마찰로 감속하므로 등속이 아니다. v(t) = v0 - a·t 를 적분해서
    거리 d를 만족하는 t를 구한다. 공이 멈춰버리면 무한대(도달 불가).
    """
    d = math.hypot(tmx - ball.mx, tmy - ball.my)
    a = ROLL_FRICTION
    v0 = kick_speed
    d_max = v0 * v0 / (2 * a)
    if d >= d_max:
        return float("inf")
    disc = v0 * v0 - 2 * a * d
    if disc < 0:
        return float("inf")
    return (v0 - math.sqrt(disc)) / a


def kick_speed_for(distance_m, arrive_speed=4.0):
    """그 거리를 arrive_speed로 도착하게 차려면 초기 속도가 얼마여야 하나."""
    return math.sqrt(arrive_speed ** 2 + 2 * ROLL_FRICTION * distance_m)