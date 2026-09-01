# -*- coding: utf-8 -*-
"""match_sim/live/live_engine.py — 자유 시뮬레이션 경기 엔진.

## 이전과 무엇이 근본적으로 다른가

기존 `sim_engine`은 **재연기**였다. `game_engine`이 확정한 스코어와
`possession_log`(몇 분에 슛/코너/파울이 있었다)를 받아서 그 순서대로 장면을
연출했다. 그래서:

  * 골이 "일어난" 게 아니라 "연출된" 것이었다 — 침투가 골을 만드는 게
    아니라 골이 침투를 지시했다
  * 코너/스로인은 확률로 발생시켰다(`random.random() < 0.22`) — 공이
    실제로 라인을 넘어서가 아니라
  * 패스는 지정한 동료에게 순간이동했다 — 중간에 끊길 수 없었다

여기서는 아무것도 미리 정해지지 않는다. 22명이 각자 판단하고, 공은 물리
법칙으로 굴러가고, **스코어는 그 결과로 나온다.** 코너킥은 공이 실제로
골라인을 넘어야 생기고, 인터셉트는 상대가 실제로 패스 경로에 있어야 생긴다.

## 세 개의 축

  1. `ball.py`     — 공은 독립 물체. 소유는 도달 경쟁의 결과.
  2. `intents.py`  — 선수는 2~4초짜리 행동을 커밋한다(위치가 아니라 의도).
  3. 여기          — 온볼 결정, 그리고 **패스와 런을 하나의 결정으로 묶는 것**

3번이 핵심이다. 패서는 동료의 *현재 위치*가 아니라 **그 동료가 지금 커밋한
런의 도달점**을 후보로 본다. 그 지점으로 차기로 정하면 리시버에게
`RECEIVE_AT` 행동을 심는다. 그래서 런이 패스를 만들고 패스가 런을 완성한다 —
예전에 서로를 모르던 두 시스템이 하나가 된다.

## 뷰어 호환

프레임 스냅샷의 키는 기존과 동일하다(clock/home/away/ball/score_*/banner_*/
last_restart_clock/possession). `ui/match_sim_viewer.py`는 프레임 로그를
재생만 하므로 **수정 없이 그대로 동작한다** — 1단계에서 엔진과 렌더러를
분리해둔 게 여기서 값을 한다.

## 시간

물리 dt = 0.12초, 프레임은 5스텝마다(0.6초). 90분 + 추가시간 ≈ 9,000프레임.
경기 시계는 실제 초 단위로 흐르고, 물리·속도·도달시간이 전부 같은 단위를
쓴다(예전 엔진의 30배 dt 불일치 같은 게 원리적으로 생길 수 없다).
"""

import math
import random

from match_sim.live import ball as B
from match_sim.live import intents as I
from match_sim.live.ball import PITCH_LEN_M, PITCH_WID_M

# ── 시간 ──
DT = 0.12                  # 물리 스텝(초)
STEPS_PER_FRAME = 5        # 프레임 간격 = 0.6초
FRAME_DT_MIN = DT * STEPS_PER_FRAME / 60.0   # 분 단위 (뷰어가 쓰는 단위)

# ── 피치 상수 ──
GOAL_HALF_W_M = 3.66       # 골대 반폭(7.32m)
GOAL_HEIGHT_M = 2.44
BOX_DEPTH_M = 16.5
GOAL_HALF_W_NORM = GOAL_HALF_W_M / PITCH_WID_M

# ── 온볼 판단 ──
DECIDE_EVERY_S = 0.30      # 캐리어가 재판단하는 주기
# [수정] 6.0m는 너무 넓었다 — 마킹 서 있는 수비수(골사이드 1.8m)만 있어도
# 압박도가 0.70이 나와서, 캐리어가 경기 내내 "강한 압박"으로 판정됐다.
# 그 결과 클리어가 경기당 400회(실축 20~40회). 실제로 "압박받는다"는 건
# 상대가 2~3m 안에 붙었을 때다.
PRESSURE_RADIUS_M = 3.5
TACKLE_RADIUS_M = 1.7
MAX_PASS_M = 42.0

_ATTACK_ROLES = {"ST", "CF", "LW", "RW", "CAM"}


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


class LiveMatchEngine:
    """90분을 실제로 굴려서 결과를 만드는 경기 시뮬 엔진.

    `sim_engine.MatchSimEngine`과 같은 외부 계약(프레임 로그 + 메타데이터)을
    노출하므로 `ui/match_sim_viewer.py`가 그대로 재생할 수 있다.
    """

    TICK_MS = 20
    _FRAME_DT = FRAME_DT_MIN
    _SEC_PER_MIN = 4.0

    # ══════════════════════════════════════════════════════════
    #  구성
    # ══════════════════════════════════════════════════════════
    def __init__(self, data, seed=None):
        from match_sim.sim_engine import layout_formation, _lookup_formation, _find_my_slot

        payload = data.get("payload", {}) or {}
        self.is_home = bool(data.get("is_home"))
        self.home_name = data.get("home_name", "홈팀")
        self.away_name = data.get("away_name", "원정팀")
        self.events_raw = payload.get("events", []) or []

        self._pre_seed_rng_state = random.getstate()
        self.rng = random.Random(seed if seed is not None else random.randrange(1 << 30))

        my_team = self.home_name if self.is_home else self.away_name
        opp_team = self.away_name if self.is_home else self.home_name
        my_f = _lookup_formation(my_team)
        opp_f = _lookup_formation(opp_team)
        self.home_formation = my_f if self.is_home else opp_f
        self.away_formation = opp_f if self.is_home else my_f

        ls = payload.get("lineup_stats") or {}
        self.home_players = self._build(layout_formation(self.home_formation, True),
                                        ls.get("home") or [])
        self.away_players = self._build(layout_formation(self.away_formation, False),
                                        ls.get("away") or [])
        self.teams = {"home": self.home_players, "away": self.away_players}

        my_slots = self.home_players if self.is_home else self.away_players
        self.my_slot = _find_my_slot(my_slots, payload.get("position", "CM"))

        # 홈은 항상 +x 방향으로 공격한다(뷰어가 그렇게 그린다).
        self.atk_goal_x = {"home": 1.0, "away": 0.0}
        self.own_goal_x = {"home": 0.0, "away": 1.0}

        # ── 경기 상태 ──
        self.pball = B.Ball(0.5, 0.5)
        self.ball = {"x": 0.5, "y": 0.5}     # 뷰어 호환용 dict
        self.clock_s = 0.0
        self.score_home = 0
        self.score_away = 0
        self.banner_text = ""
        self.banner_color = "#ffffff"
        self.banner_alpha = 0
        self._last_restart_clock = -99.0
        self.possession = "home"
        self._decide_at = 0.0
        self._restart = None
        self._restart_at = -1.0
        self._shot = None
        self._last_touch_side = None
        self._pending_pass = None
        self._carry_since = 0.0

        self.stoppage1 = self.rng.randint(1, 4)
        self.stoppage2 = self.rng.randint(2, 6)
        self.match_end = 90 + self.stoppage1 + self.stoppage2
        self._half_end_s = (45 + self.stoppage1) * 60.0
        self._full_end_s = (90 + self.stoppage1 + self.stoppage2) * 60.0
        self._halftime_done = False

        self.events = []          # 축구 사건 로그 (계측/통계의 원천)
        self._frames = []
        self._poss_ticks = {"home": 0, "away": 0}

        self._kickoff("home" if self.rng.random() < 0.5 else "away")
        self.final_home = 0
        self.final_away = 0
        self.timeline = []
        self._true_match_end = 0.0

    def _build(self, layout, stats_list):
        out = []
        for i, (lab, (x, y)) in enumerate(layout):
            st = stats_list[i] if i < len(stats_list) and stats_list[i] else {}
            out.append({
                "pos": lab, "x": x, "y": y, "hx": x, "hy": y,
                "vx": 0.0, "vy": 0.0,          # 정규화/초
                "stats": st,
                "intent": None,
                "vmax": self._vmax_for(lab, st),
                # 골키퍼는 손을 쓰므로 도달 반경이 넓다(선방의 물리적 근거)
                "reach": 2.9 if lab == "GK" else 1.6,
                # 골키퍼는 손을 뻗어 높은 공도 잡는다(선방의 물리적 근거)
                "max_z": 2.7 if lab == "GK" else 2.2,
            })
        return out

    @staticmethod
    def _vmax_for(pos, st):
        base = {"GK": 5.6, "CB": 7.6, "LB": 8.0, "RB": 8.0, "LWB": 8.1, "RWB": 8.1,
                "CDM": 7.6, "CM": 7.8, "CAM": 7.9, "LM": 8.0, "RM": 8.0,
                "LW": 8.6, "RW": 8.6, "CF": 8.2, "ST": 8.3}.get(pos, 7.8)
        spd = st.get("speed", 50)
        return _clamp(base * (0.82 + spd / 100.0 * 0.36), 5.0, 9.4)

    # ══════════════════════════════════════════════════════════
    #  진행
    # ══════════════════════════════════════════════════════════
    def simulate(self):
        if self._frames:
            return self._frames
        step = 0
        guard = 0
        while self.clock_s < self._full_end_s and guard < 200000:
            self._step()
            step += 1
            guard += 1
            if step % STEPS_PER_FRAME == 0:
                self._frames.append(self._snapshot())
        if not self._frames:
            self._frames.append(self._snapshot())
        self._true_match_end = self.clock_s / 60.0
        self.final_home = self.score_home
        self.final_away = self.score_away
        self.team_stats = self._derive_stats()
        return self._frames

    @property
    def frames(self):
        return self._frames

    def _step(self):
        self.clock_s += DT
        if self.banner_alpha > 0:
            self.banner_alpha = max(0, self.banner_alpha - 5)

        if not self._halftime_done and self.clock_s >= self._half_end_s:
            self._halftime_done = True
            self._kickoff("away" if self._kick_side == "home" else "home")
            self.banner_text = "⏱ 후반 시작"
            self.banner_color = "#88ccff"
            self.banner_alpha = 255
            return

        # 재개 대기 중이면 선수만 자리 잡고 공은 멈춰 있다
        if self._restart is not None:
            if self.clock_s >= self._restart_at:
                self._execute_restart()
            else:
                self._move_players(restart_mode=True)
                self._sync_ball_dict()
                return

        b = self.pball
        # 1) 공 물리
        carrier_pos = carrier_vel = None
        if b.carrier is not None:
            side, i = b.carrier
            p = self.teams[side][i]
            carrier_pos = (p["x"], p["y"])
            carrier_vel = (p["vx"] * PITCH_LEN_M, p["vy"] * PITCH_WID_M)
        b.advance(DT, carrier_pos, carrier_vel)

        # 2) 소유권 경쟁 — **라인 아웃 판정보다 먼저** 해야 한다.
        #    [버그 수정] 예전엔 아웃을 먼저 보고 return 했다. 그런데 슛은
        #    한 스텝에 3m를 이동하므로, 골키퍼를 지나 골라인을 넘는 게
        #    같은 스텝 안에서 일어난다 — 그러면 골키퍼는 선방 기회를
        #    아예 못 받고 실점 처리된다(실측: 슛 120개에 골 119개).
        #    경합을 먼저 돌려야 "골라인 직전에 쳐냈다"가 성립한다.
        got = B.contest(b, self.teams, self.clock_s, self.rng)
        if got:
            side, idx = got
            self._on_control(side, idx)

        # 3) 라인 아웃
        kind = b.check_out(goal_half_w=GOAL_HALF_W_NORM)
        if kind:
            self._handle_out(kind)
            return

        # 4) 소유 팀 갱신 + 점유율
        if b.carrier is not None:
            self.possession = b.carrier[0]
        self._poss_ticks[self.possession] += 1

        # 5) 온볼 결정
        if b.carrier is not None and self.clock_s >= self._decide_at:
            self._decide_on_ball()
            self._decide_at = self.clock_s + DECIDE_EVERY_S

        # 6) 태클 / 파울
        if b.carrier is not None:
            self._try_tackle()

        # 7) 오프볼 행동 + 이동
        self._move_players()
        self._sync_ball_dict()

    def _sync_ball_dict(self):
        self.ball["x"] = _clamp(self.pball.x, 0.0, 1.0)
        self.ball["y"] = _clamp(self.pball.y, 0.0, 1.0)

    # ══════════════════════════════════════════════════════════
    #  국면 컨텍스트
    # ══════════════════════════════════════════════════════════
    def _phase_for(self, side):
        b = self.pball
        if b.carrier is None:
            return "loose"
        return "attack" if b.carrier[0] == side else "defend"

    def _pressure_on(self, side, idx):
        """이 선수가 받고 있는 압박 정도(0~1)."""
        p = self.teams[side][idx]
        opp = self.teams["away" if side == "home" else "home"]
        pmx, pmy = p["x"] * PITCH_LEN_M, p["y"] * PITCH_WID_M
        best = 99.0
        for q in opp:
            d = math.hypot(q["x"] * PITCH_LEN_M - pmx, q["y"] * PITCH_WID_M - pmy)
            if d < best:
                best = d
        return _clamp(1.0 - best / PRESSURE_RADIUS_M, 0.0, 1.0)

    def _build_ctx(self, side):
        b = self.pball
        opp_side = "away" if side == "home" else "home"
        phase = self._phase_for(side)
        if b.carrier is not None:
            cs, ci = b.carrier
            cp = self.teams[cs][ci]
            carrier_pos_m = (cp["x"] * PITCH_LEN_M, cp["y"] * PITCH_WID_M)
            carrier_pressure = self._pressure_on(cs, ci)
        else:
            carrier_pos_m = (b.mx, b.my)
            carrier_pressure = 0.0

        team = self.teams[side]

        # 압박 담당 = 볼에 가장 가까운 한 명
        presser = None
        if phase == "defend":
            bestd, besti = 1e9, None
            for i, p in enumerate(team):
                if p["pos"] == "GK":
                    continue
                d = math.hypot(p["x"] * PITCH_LEN_M - b.mx, p["y"] * PITCH_WID_M - b.my)
                if d < bestd:
                    bestd, besti = d, i
            if besti is not None:
                presser = (side, besti)

        # 마킹 배정 — 위험한 상대부터 가까운 우리 수비에게 탐욕적으로
        mark_assign = {}
        most_dangerous = None
        if phase == "defend":
            goal_mx = self.own_goal_x[side] * PITCH_LEN_M
            opps = [(i, q) for i, q in enumerate(self.teams[opp_side]) if q["pos"] != "GK"]
            opps.sort(key=lambda t: math.hypot(t[1]["x"] * PITCH_LEN_M - goal_mx,
                                               t[1]["y"] * PITCH_WID_M - PITCH_WID_M / 2))
            if opps:
                q = opps[0][1]
                most_dangerous = (q["x"] * PITCH_LEN_M, q["y"] * PITCH_WID_M)
            used = set()
            for oi, q in opps[:6]:
                qmx, qmy = q["x"] * PITCH_LEN_M, q["y"] * PITCH_WID_M
                bestd, besti = 1e9, None
                for i, p in enumerate(team):
                    if p["pos"] == "GK" or i in used or (side, i) == presser:
                        continue
                    d = math.hypot(p["x"] * PITCH_LEN_M - qmx, p["y"] * PITCH_WID_M - qmy)
                    if d < bestd:
                        bestd, besti = d, i
                if besti is not None and bestd < 26.0:
                    used.add(besti)
                    mark_assign[(side, besti)] = (opp_side, oi)

        # 백라인 목표 높이 — 블록이 볼을 따라 오르내린다
        fwd = 1.0 if self.atk_goal_x[side] > 0.5 else -1.0
        ball_u = b.x if fwd > 0 else (1.0 - b.x)
        if phase == "attack":
            back_u = _clamp(ball_u - 0.36, 0.13, 0.58)
        elif phase == "defend":
            back_u = _clamp(ball_u - 0.14, 0.09, 0.46)
        else:
            back_u = _clamp(ball_u - 0.24, 0.11, 0.52)
        back_x = back_u if fwd > 0 else (1.0 - back_u)

        cur_back = [p["hx"] for p in team if p["pos"] in I._BACKLINE]
        cur_back_u = 0.0
        if cur_back:
            m = sum(cur_back) / len(cur_back)
            cur_back_u = m if fwd > 0 else (1.0 - m)
        shift_u = (back_u - cur_back_u) * 0.85

        def shape_target(idx):
            p = team[idx]
            hu = p["hx"] if fwd > 0 else (1.0 - p["hx"])
            nx = hu + shift_u
            xx = nx if fwd > 0 else (1.0 - nx)
            return (_clamp(xx, 0.03, 0.97) * PITCH_LEN_M, p["hy"] * PITCH_WID_M)

        counts = {}
        for p in team:
            it = p.get("intent")
            if it is not None:
                counts[it.kind] = counts.get(it.kind, 0) + 1

        return {
            "phase": phase, "ball": b, "teams": self.teams, "side": side,
            "opp_side": opp_side, "carrier": b.carrier,
            "carrier_pos_m": carrier_pos_m, "carrier_pressure": carrier_pressure,
            "atk_goal_x": self.atk_goal_x[side], "own_goal_x": self.own_goal_x[side],
            "presser": presser, "mark_assign": mark_assign,
            "most_dangerous_opp": most_dangerous,
            "backline_target_mx": back_x * PITCH_LEN_M,
            "shape_target": shape_target, "intent_counts": counts,
        }

    # ══════════════════════════════════════════════════════════
    #  온볼 결정 — 슛 / 패스(공간으로) / 캐리 / 클리어
    # ══════════════════════════════════════════════════════════
    def _decide_on_ball(self):
        """온볼 결정 — 슛/패스/캐리/클리어를 **하나의 눈금 위에서** 비교한다.

        [수정] 처음엔 각 선택지를 독립 확률로 굴렸다(슛 조건 만족하면
        `rng.random() < shoot_q` 처럼). 캐리어가 0.3초마다 재판단하므로
        확률이 누적돼서, 골대 근처에 들어가면 사실상 무조건 슛이 나갔다
        (실측: 경기당 슛 243회, 패스 27회, 스코어 36-45). 실제 선수는
        "슛할까 말까"를 굴리는 게 아니라 **지금 가능한 선택지 중 제일 나은
        걸 고른다.** 전부 같은 가치 눈금으로 환산해서 argmax를 취한다.
        """
        b = self.pball
        side, idx = b.carrier
        team = self.teams[side]
        opp_side = "away" if side == "home" else "home"
        opp = self.teams[opp_side]
        p = team[idx]
        st = p.get("stats") or {}
        pmx, pmy = p["x"] * PITCH_LEN_M, p["y"] * PITCH_WID_M
        goal_mx = self.atk_goal_x[side] * PITCH_LEN_M
        goal_my = PITCH_WID_M / 2
        fwd = 1.0 if self.atk_goal_x[side] > 0.5 else -1.0
        pressure = self._pressure_on(side, idx)
        goal_d = math.hypot(goal_mx - pmx, goal_my - pmy)

        options = []

        # ── 슛 (xG 기반) ──
        if goal_d < 28.0:
            xg = self._xg(pmx, pmy, goal_mx, goal_my, opp)
            skill = st.get("shooting", st.get("finishing", 50)) / 100.0
            # [튜닝] 슛은 "가능하면 쏜다"가 아니라 "쏠 만할 때 쏜다".
            # 하한 없이 가치만 비교하면 박스 근처에서 계속 난사한다
            # (실측: 경기당 슛 140개 / 실축 20~30개).
            if xg > 0.035 or goal_d < 14.0:
                options.append((4.8 * xg * (0.7 + 0.6 * skill), "shoot", None))

        # ── 패스 ──
        for j, q in enumerate(team):
            if j == idx or q["pos"] == "GK":
                continue
            for target_m, is_space in self._pass_targets(q):
                tmx, tmy = target_m
                if not (0.5 < tmx < PITCH_LEN_M - 0.5 and 0.5 < tmy < PITCH_WID_M - 0.5):
                    continue
                d = math.hypot(tmx - pmx, tmy - pmy)
                if d < 3.5 or d > MAX_PASS_M:
                    continue
                # [수정] 도착 속도가 높으면 목표점을 지나 계속 굴러간다
                # (4.5 m/s면 18m 더 구른다 → 패스가 그대로 골라인 밖으로).
                # 실측: 경기당 패스가 라인을 넘어 아웃되는 게 128회였다.
                kick_v = B.kick_speed_for(d, arrive_speed=2.6 if is_space else 1.6)
                # 정지 지점이 피치 안인지 확인한다(굴러 나가면 후보 제외)
                roll = kick_v * kick_v / (2 * B.ROLL_FRICTION)
                ux, uy = (tmx - pmx), (tmy - pmy)
                ul = math.hypot(ux, uy) or 1.0
                stop_x, stop_y = pmx + ux / ul * roll, pmy + uy / ul * roll
                if not (1.0 < stop_x < PITCH_LEN_M - 1.0
                        and 0.5 < stop_y < PITCH_WID_M - 0.5):
                    continue
                t_ball = B.ball_travel_time(b, tmx, tmy, kick_v)
                if not math.isfinite(t_ball):
                    continue
                t_recv = B.time_to_reach(q["x"], q["y"], q["vx"], q["vy"],
                                         q["vmax"], tmx, tmy)
                # [수정] 예전엔 리시버가 공보다 빨리 도착해야 한다고 봤다.
                # 실제로는 공보다 조금 늦어도 **달려 들어가서** 받는다 —
                # 중요한 건 상대보다 먼저 닿느냐다.
                if t_recv > t_ball + 1.3:
                    continue
                t_opp = min((B.time_to_reach(o["x"], o["y"], o["vx"], o["vy"],
                                             o["vmax"], tmx, tmy)
                             for o in opp), default=99.0)
                if t_recv > t_opp - 0.10:
                    continue
                # [수정 — 결정적 버그] 처음엔 `t_opp - max(t_recv, t_ball)`로
                # 계산했다. 즉 "상대가 공보다 먼저 그 지점에 도달할 수 있으면
                # 위험"으로 본 것인데, 이건 틀렸다 — 리시버 발밑으로 주는
                # 패스에서 2m 옆에 선 수비수는 항상 "공보다 먼저" 그 점에
                # 갈 수 있지만, 리시버가 더 가까우므로 인터셉트가 아니다.
                # 그 결과 패스 후보의 96%가 걸러져서(실측) 캐리어가 경기
                # 내내 드리블만 했다.
                # 실제 경합은 **리시버 대 최근접 상대**의 도달 시간 경쟁이다.
                # 레인 위 차단은 아래 _lane_blockers가 따로 본다.
                safety = _clamp((t_opp - t_recv) / 1.2, 0.0, 1.0)
                lane = self._lane_blockers(pmx, pmy, tmx, tmy, opp, 1.5)
                safety *= max(0.05, 1.0 - 0.40 * lane)
                if safety < 0.06:
                    continue
                if is_space and self._is_offside(side, q, tmx):
                    continue

                prog = _clamp((tmx - pmx) * fwd / 28.0, -0.7, 1.3)
                threat = _clamp(1.0 - math.hypot(goal_mx - tmx, goal_my - tmy) / 48.0, 0, 1)
                # [튜닝 — 거리 페널티] 이게 없으면 전진 가치(prog)가 가장
                # 큰 **가장 먼** 패스가 매번 이겨서 평균 패스 거리가 34.6m가
                # 된다(실축 14~24m). 그러면 경기가 통째로 롱볼이 되고, 모든
                # 상황이 파이널서드에서 벌어져 슛 139개·골킥 178개가 나온다.
                # 실제 선수는 "가능한 것 중 가장 먼 곳"이 아니라 "충분히
                # 좋은 것 중 가장 가까운 곳"으로 준다.
                # [재조정] 페널티가 0.85면 전진 가치(0.26)를 압도해서 전진
                # 패스가 아예 선택되지 않는다 — 팀이 하프라인 부근에서
                # 볼만 돌리고 슛이 0개가 된다(실측). 페널티는 "롱볼 남발"을
                # 막을 정도만이면 되고, 전진 자체를 막으면 안 된다.
                dist_pen = 0.45 * _clamp((d - 18.0) / 24.0, 0.0, 1.0)
                val = (0.46 + 0.44 * safety + 0.42 * prog + 0.34 * threat
                       + (0.16 if is_space else 0.0) - dist_pen
                       + self.rng.uniform(-0.05, 0.05))
                options.append((val, "pass", (j, tmx, tmy, kick_v, is_space)))

        # ── 캐리 ──
        ahead = min((math.hypot(o["x"] * PITCH_LEN_M - (pmx + 10 * fwd),
                                o["y"] * PITCH_WID_M - pmy) for o in opp), default=40.0)
        dr = st.get("dribbling", 50) / 100.0
        # [중요] 볼을 오래 들고 있을수록 캐리 가치가 떨어진다. 이게 없으면
        # 캐리가 항상 패스를 이겨서 경기당 패스가 20회밖에 안 나온다(실측).
        # 실제 선수가 볼을 소유하는 시간은 평균 1~2초다.
        held = self.clock_s - self._carry_since
        # [튜닝] 실축은 경기당 총 패스 700~1000회, 즉 3~5초에 한 번이다.
        # 캐리 기본값이 낮으면 컨트롤 직후 바로 패스해서 8000회가 나온다
        # (실측). 볼을 1.5~2초 들고 있다가 내주도록 기본값과 감쇠를 잡는다.
        options.append((1.45 + 0.26 * (1.0 - pressure) + 0.18 * _clamp(ahead / 14.0, 0, 1)
                        + 0.10 * dr - 0.13 * held
                        + self.rng.uniform(-0.06, 0.06), "carry", None))

        # ── 클리어 ──
        own_d = abs(pmx - self.own_goal_x[side] * PITCH_LEN_M)
        # 클리어는 최후의 수단이다. 예전 눈금에서는 압박만 높으면 안전한
        # 패스보다 높게 나와서 경기당 280회씩 걷어냈다(그 결과 골킥 200회).
        if own_d < 28.0 and pressure > 0.72:
            options.append((0.22 + 0.34 * pressure * (1.0 - own_d / 28.0), "clear", None))

        options.sort(key=lambda o: -o[0])
        _, kind, payload = options[0]

        if kind == "carry":
            return
        if kind == "shoot":
            self._shoot(side, idx)
            return
        if kind == "clear":
            tx = pmx + 42.0 * fwd
            ty = _clamp(pmy + self.rng.uniform(-20, 20), 3.0, PITCH_WID_M - 3.0)
            b.kick(tx, ty, 22.0, loft_deg=28.0, by=(side, idx))
            self._log("clear", side, idx)
            return

        j, tmx, tmy, kick_v, is_space = payload
        loft = 0.0
        if self._lane_blockers(pmx, pmy, tmx, tmy, opp, 1.5) > 0:
            loft = self.rng.uniform(12.0, 22.0)
        acc = st.get("passing", 50) / 100.0
        err = (1.0 - acc) * 2.4 + pressure * 2.2
        tmx += self.rng.gauss(0, err)
        tmy += self.rng.gauss(0, err)
        # 조준 오차까지 반영한 뒤에도 피치 안을 향하게 한다 — 안 그러면
        # 패스가 그대로 골라인/터치라인을 넘어 아웃이 된다.
        tmx = _clamp(tmx, 4.0, PITCH_LEN_M - 4.0)
        tmy = _clamp(tmy, 2.0, PITCH_WID_M - 2.0)
        b.kick(tmx, tmy, kick_v, loft_deg=loft, by=(side, idx))
        rec = self.teams[side][j]
        rec["intent"] = I.new_intent(I.RECEIVE_AT, tmx, tmy, self.clock_s, self.rng)
        self._pending_pass = {"side": side, "from": idx, "to": j, "t": self.clock_s}
        self._log("pass", side, idx, to=j, space=is_space,
                  dist=math.hypot(tmx - pmx, tmy - pmy))

    def _xg(self, pmx, pmy, goal_mx, goal_my, opp):
        """슛 기대득점(대략). 거리 지수감쇠 × 각도 × 차단."""
        d = math.hypot(goal_mx - pmx, goal_my - pmy)
        ang = self._goal_angle(pmx, pmy, goal_mx)
        blk = self._lane_blockers(pmx, pmy, goal_mx, goal_my, opp, 1.8)
        return (math.exp(-0.115 * d) * (0.30 + 0.70 * ang)
                * max(0.15, 1.0 - 0.30 * blk))

    def _pass_targets(self, q):
        """이 동료에게 패스한다면 어디로 차야 하나.

        두 후보가 있다:
          * 발밑 — 현재 위치
          * **공간** — 그 동료가 지금 커밋한 런의 도달점

        두 번째가 이 엔진의 핵심이다. 예전엔 "동료에게 패스"만 있었고
        동료의 런은 패스 판단과 무관했다 — 그래서 어떤 침투도 아무것도
        만들어내지 못했다.
        """
        qmx, qmy = q["x"] * PITCH_LEN_M, q["y"] * PITCH_WID_M
        out = [((qmx, qmy), False)]
        it = q.get("intent")
        if it is not None and it.kind in (I.RUN_BEHIND, I.OVERLAP, I.OCCUPY_BOX,
                                          I.CHECK_TO_BALL, I.HOLD_WIDTH):
            if math.hypot(it.tmx - qmx, it.tmy - qmy) > 4.0:
                out.append(((it.tmx, it.tmy), True))
        return out

    def _goal_angle(self, pmx, pmy, goal_mx):
        """골문을 그 지점에서 바라본 시야각(0~1로 정규화)."""
        gy = PITCH_WID_M / 2
        a = math.atan2(gy + GOAL_HALF_W_M - pmy, goal_mx - pmx)
        c = math.atan2(gy - GOAL_HALF_W_M - pmy, goal_mx - pmx)
        return _clamp(abs(a - c) / 0.62, 0.0, 1.0)

    @staticmethod
    def _lane_blockers(ax, ay, bx, by, opp, radius_m):
        """선분 (a→b) 근처에 있는 상대 수. 패스/슛 차단 판정."""
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 < 1e-6:
            return 0
        n = 0
        for o in opp:
            ox, oy = o["x"] * PITCH_LEN_M, o["y"] * PITCH_WID_M
            t = ((ox - ax) * dx + (oy - ay) * dy) / L2
            if t <= 0.05 or t >= 0.98:
                continue
            px, py = ax + t * dx, ay + t * dy
            if math.hypot(ox - px, oy - py) < radius_m:
                n += 1
        return n

    def _is_offside(self, side, q, tmx):
        opp = self.teams["away" if side == "home" else "home"]
        xs = sorted(o["x"] * PITCH_LEN_M for o in opp)
        if len(xs) < 2:
            return False
        if self.atk_goal_x[side] > 0.5:
            last_def = xs[-2]
            return tmx > last_def + 0.6
        last_def = xs[1]
        return tmx < last_def - 0.6

    # ══════════════════════════════════════════════════════════
    #  슛
    # ══════════════════════════════════════════════════════════
    def _shoot(self, side, idx):
        b = self.pball
        p = self.teams[side][idx]
        st = p.get("stats") or {}
        pmx, pmy = p["x"] * PITCH_LEN_M, p["y"] * PITCH_WID_M
        goal_mx = self.atk_goal_x[side] * PITCH_LEN_M
        gy = PITCH_WID_M / 2
        d = math.hypot(goal_mx - pmx, gy - pmy)
        pressure = self._pressure_on(side, idx)

        skill = st.get("shooting", st.get("finishing", 50)) / 100.0
        # 조준 오차 — 거리·압박이 크고 실력이 낮을수록 벌어진다
        # [튜닝] 실축은 슛의 약 30~40%만 온타깃이다. 좁은 오차를 주면
        # 거의 전부 골문 안으로 가서 슛-골 전환율이 40%까지 치솟는다(실측).
        # [튜닝] 실축 온타깃 비율은 30~45%다(빗나감 + 블록 포함).
        # 오차가 좁으면 87%가 골문 안으로 가서 슛-골 전환율이 63%가 된다.
        spread = (2.6 + d * 0.20) * (1.5 - skill) * (1.0 + pressure * 0.6)
        aim_y = gy + self.rng.gauss(0, spread)
        aim_z = abs(self.rng.gauss(0.7, 0.75))
        speed = self.rng.uniform(20.0, 30.0) * (0.85 + skill * 0.3)
        loft = math.degrees(math.atan2(aim_z, max(1.0, d))) + self.rng.uniform(-1.0, 3.0)
        b.kick(goal_mx + (2.0 if self.atk_goal_x[side] > 0.5 else -2.0), aim_y,
               speed, loft_deg=max(0.0, loft), by=(side, idx))
        self._shot = {"side": side, "idx": idx, "t": self.clock_s, "dist": d}
        self._log("shot", side, idx, dist=d, angle=self._goal_angle(pmx, pmy, goal_mx))

    # ══════════════════════════════════════════════════════════
    #  태클 / 파울
    # ══════════════════════════════════════════════════════════
    def _try_tackle(self):
        b = self.pball
        side, idx = b.carrier
        opp_side = "away" if side == "home" else "home"
        p = self.teams[side][idx]
        pmx, pmy = p["x"] * PITCH_LEN_M, p["y"] * PITCH_WID_M
        for j, o in enumerate(self.teams[opp_side]):
            if o["pos"] == "GK":
                continue
            d = math.hypot(o["x"] * PITCH_LEN_M - pmx, o["y"] * PITCH_WID_M - pmy)
            if d > TACKLE_RADIUS_M:
                continue
            tk = (o.get("stats") or {}).get("tackling", 50) / 100.0
            dr = (p.get("stats") or {}).get("dribbling", 50) / 100.0
            # [수정] 예전 계수는 밀착 상태가 1초만 지속돼도 탈취 확률이
            # 65%에 달해 경기당 태클 400회가 나왔다(실축 25~45회).
            pw = _clamp(0.5 + (tk - dr) * 0.55, 0.10, 0.85) * DT * 0.55
            r = self.rng.random()
            if r < pw * 0.055:
                # 파울
                self._log("foul", opp_side, j)
                self._set_restart("free_kick", side, b.x, b.y)
                return
            if r < pw:
                # 태클 성공 — 공은 루즈볼이 된다(바로 상대 소유가 아니다)
                ang = self.rng.uniform(0, 2 * math.pi)
                b.kick(b.mx + math.cos(ang) * 8, b.my + math.sin(ang) * 8,
                       self.rng.uniform(4.0, 9.0), by=(opp_side, j))
                self._log("tackle", opp_side, j)
                return

    # ══════════════════════════════════════════════════════════
    #  아웃 / 재개
    # ══════════════════════════════════════════════════════════
    def _handle_out(self, kind):
        b = self.pball
        last = b.last_touch
        last_side = last[0] if last else self.possession
        other = "away" if last_side == "home" else "home"

        if kind == "goal":
            scorer_side = last_side
            # 자책골 방지: 공이 들어간 골문이 누구 것인지로 판정
            gx = 1.0 if b.out_x > 0.5 else 0.0
            scored_on = "home" if gx == 0.0 else "away"
            scorer_side = "away" if scored_on == "home" else "home"
            if scorer_side == "home":
                self.score_home += 1
            else:
                self.score_away += 1
            self.banner_text = "⚽ GOAL!"
            self.banner_color = "#ffd34d"
            self.banner_alpha = 255
            self._log("goal", scorer_side,
                      last[1] if last and last[0] == scorer_side else -1)
            self._set_restart("kickoff", scored_on, 0.5, 0.5, delay=2.0)
            return

        if kind == "throw_in":
            self._log("throw_in", other, -1)
            self._set_restart("throw_in", other,
                              _clamp(b.out_x, 0.02, 0.98),
                              0.005 if b.out_y < 0.5 else 0.995)
            return

        # byline — 마지막 터치가 공격팀이면 골킥, 수비팀이면 코너
        gx = 1.0 if b.out_x > 0.5 else 0.0
        defending = "home" if gx == 0.0 else "away"
        if last_side == defending:
            self._log("corner", other, -1)
            self._set_restart("corner", other, gx, 0.02 if b.out_y < 0.5 else 0.98)
        else:
            self._log("goal_kick", defending, -1)
            self._set_restart("goal_kick", defending,
                              0.06 if gx == 0.0 else 0.94, 0.5)

    def _set_restart(self, kind, side, x, y, delay=None):
        self._shot = None
        if delay is None:
            delay = {"throw_in": 1.6, "corner": 3.2, "goal_kick": 2.6,
                     "free_kick": 2.4, "kickoff": 2.0}.get(kind, 2.0)
        self._restart = {"kind": kind, "side": side, "x": x, "y": y}
        self._restart_at = self.clock_s + delay
        self._last_restart_clock = self.clock_s / 60.0
        b = self.pball
        b.reset_in_play(_clamp(x, 0.01, 0.99), _clamp(y, 0.01, 0.99))

    def _execute_restart(self):
        r = self._restart
        self._restart = None
        side = r["side"]
        b = self.pball
        b.reset_in_play(_clamp(r["x"], 0.01, 0.99), _clamp(r["y"], 0.01, 0.99))
        # 재개는 가장 가까운 우리 선수가 잡는다
        team = self.teams[side]
        besti, bestd = 0, 1e9
        for i, p in enumerate(team):
            if r["kind"] == "goal_kick":
                if p["pos"] != "GK":
                    continue
                besti = i
                break
            if p["pos"] == "GK":
                continue
            d = math.hypot(p["x"] - b.x, p["y"] - b.y)
            if d < bestd:
                bestd, besti = d, i
        team[besti]["x"], team[besti]["y"] = b.x, b.y
        b.attach(side, besti, self.clock_s)
        self.possession = side
        self._decide_at = self.clock_s + 0.15
        self._last_restart_clock = self.clock_s / 60.0

    def _kickoff(self, side):
        self._kick_side = side
        for s, team in self.teams.items():
            for p in team:
                p["x"], p["y"] = p["hx"], p["hy"]
                p["vx"] = p["vy"] = 0.0
                p["intent"] = None
        self.pball.reset_in_play(0.5, 0.5)
        team = self.teams[side]
        ci = max(range(len(team)),
                 key=lambda i: -abs(team[i]["hx"] - 0.5) if team[i]["pos"] != "GK" else -99)
        team[ci]["x"], team[ci]["y"] = 0.5, 0.5
        self.pball.attach(side, ci, self.clock_s)
        self.possession = side
        self._decide_at = self.clock_s + 0.4

    # ══════════════════════════════════════════════════════════
    #  이동
    # ══════════════════════════════════════════════════════════
    def _move_players(self, restart_mode=False):
        b = self.pball
        for side in ("home", "away"):
            ctx = self._build_ctx(side)
            team = self.teams[side]
            for i, p in enumerate(team):
                if p["pos"] == "GK":
                    self._move_gk(side, i, ctx)
                    continue
                if b.carrier == (side, i) and not restart_mode:
                    self._move_carrier(side, i, ctx)
                    continue
                it = p.get("intent")
                if restart_mode:
                    tmx, tmy = ctx["shape_target"](i)
                elif not I.still_valid(it, p, ctx, self.clock_s):
                    it = I.choose(p, i, side, ctx, self.clock_s, self.rng)
                    p["intent"] = it
                    tmx, tmy = I.live_target(it, ctx)
                else:
                    tmx, tmy = I.live_target(it, ctx)
                sprint = (it is not None and it.kind in I.SPRINT_KINDS) and not restart_mode
                self._steer(p, tmx, tmy, sprint)
            # 같은 팀 겹침 방지
            self._separate(team)

    def _move_carrier(self, side, idx, ctx):
        """캐리어는 공간으로 몰고 간다 — 상대가 적은 전방으로."""
        p = self.teams[side][idx]
        opp = self.teams[ctx["opp_side"]]
        pmx, pmy = p["x"] * PITCH_LEN_M, p["y"] * PITCH_WID_M
        fwd = 1.0 if self.atk_goal_x[side] > 0.5 else -1.0
        best, bt = None, -1e9
        for ang in range(-70, 71, 20):
            a = math.radians(ang)
            dx = math.cos(a) * fwd
            dy = math.sin(a)
            tx = pmx + dx * 12.0
            ty = pmy + dy * 12.0
            # 골라인/터치라인에 바짝 붙는 캐리 목표는 제외한다.
            if not (3.5 < tx < PITCH_LEN_M - 3.5 and 2.0 < ty < PITCH_WID_M - 2.0):
                continue
            near = min((math.hypot(o["x"] * PITCH_LEN_M - tx, o["y"] * PITCH_WID_M - ty)
                        for o in opp), default=50.0)
            v = _clamp(near / 12.0, 0, 1) * 0.65 + (dx * fwd) * 0.35
            if v > bt:
                bt, best = v, (tx, ty)
        if best is None:
            best = (_clamp(pmx + 6.0 * fwd, 6.0, PITCH_LEN_M - 6.0), pmy)
        self._steer(p, best[0], best[1], sprint=False, cap=0.72)

    def _move_gk(self, side, idx, ctx):
        p = self.teams[side][idx]
        b = self.pball
        own = self.own_goal_x[side]
        sign = 1.0 if own < 0.5 else -1.0
        ball_far = abs(b.x - own)
        # 슛이 날아오면 궤적 차단 지점으로 다이빙
        if (self._shot and self._shot["side"] != side
                and b.carrier is None and b.speed() > 8.0):
            gx = own * PITCH_LEN_M
            t = (gx - b.mx) / (b.vx if abs(b.vx) > 1e-3 else 1e-3)
            if 0 < t < 2.0:
                iy = b.my + b.vy * t
                self._steer(p, gx + sign * 1.5, _clamp(iy, 2.0, PITCH_WID_M - 2.0),
                            sprint=True)
                return
        # [수정] 예전 계수는 볼이 반대 진영일 때 GK를 골문에서 13m까지
        # 밀어냈다. 그 상태에서 역습 슛이 오면 물리적으로 복귀가 불가능해
        # 슛 117개 중 85개가 골이 됐다(실측). 실제 스위퍼 키퍼도 박스를
        # 크게 벗어나진 않는다.
        sweep = 0.02 + max(0.0, ball_far - 0.55) * 0.10
        tx = (own + sweep * sign) * PITCH_LEN_M
        ty = PITCH_WID_M / 2 + (b.y - 0.5) * PITCH_WID_M * 0.30
        self._steer(p, tx, _clamp(ty, PITCH_WID_M * 0.30, PITCH_WID_M * 0.70), sprint=False)

    def _steer(self, p, tmx, tmy, sprint, cap=None):
        """가속도 제한 운동학. 속도는 m/s, 위치는 정규화."""
        pmx, pmy = p["x"] * PITCH_LEN_M, p["y"] * PITCH_WID_M
        vx, vy = p["vx"] * PITCH_LEN_M, p["vy"] * PITCH_WID_M
        dx, dy = tmx - pmx, tmy - pmy
        d = math.hypot(dx, dy)
        vmax = p["vmax"]
        if cap is not None:
            vmax *= cap
        elif not sprint:
            # 조깅~러닝. 목표가 멀수록 빨리 간다.
            vmax *= _clamp(0.30 + d / 26.0, 0.30, 0.82)
        if d < 1e-6:
            dvx = -vx
            dvy = -vy
        else:
            speed_cap = min(vmax, d / 0.55)
            dvx = dx / d * speed_cap - vx
            dvy = dy / d * speed_cap - vy
        amag = math.hypot(dvx, dvy)
        amax = 4.2 * DT
        if amag > amax and amag > 1e-9:
            dvx *= amax / amag
            dvy *= amax / amag
        vx += dvx
        vy += dvy
        s = math.hypot(vx, vy)
        if s > p["vmax"]:
            vx *= p["vmax"] / s
            vy *= p["vmax"] / s
        p["x"] = _clamp((pmx + vx * DT) / PITCH_LEN_M, 0.005, 0.995)
        p["y"] = _clamp((pmy + vy * DT) / PITCH_WID_M, 0.005, 0.995)
        p["vx"] = vx / PITCH_LEN_M
        p["vy"] = vy / PITCH_WID_M

    @staticmethod
    def _separate(team):
        MIN_M = 1.6
        for a in range(len(team)):
            pa = team[a]
            if pa["pos"] == "GK":
                continue
            for c in range(a + 1, len(team)):
                pb = team[c]
                if pb["pos"] == "GK":
                    continue
                dx = (pa["x"] - pb["x"]) * PITCH_LEN_M
                dy = (pa["y"] - pb["y"]) * PITCH_WID_M
                d = math.hypot(dx, dy)
                if 1e-4 < d < MIN_M:
                    push = (MIN_M - d) * 0.5
                    nx, ny = dx / d, dy / d
                    pa["x"] = _clamp(pa["x"] + nx * push / PITCH_LEN_M, 0.005, 0.995)
                    pa["y"] = _clamp(pa["y"] + ny * push / PITCH_WID_M, 0.005, 0.995)
                    pb["x"] = _clamp(pb["x"] - nx * push / PITCH_LEN_M, 0.005, 0.995)
                    pb["y"] = _clamp(pb["y"] - ny * push / PITCH_WID_M, 0.005, 0.995)

    # ══════════════════════════════════════════════════════════
    #  기록
    # ══════════════════════════════════════════════════════════
    def _on_control(self, side, idx):
        prev = self._last_touch_side
        self._last_touch_side = side
        pp = self._pending_pass
        if pp is not None:
            ok = (pp["side"] == side)
            self._log("pass_result", pp["side"], pp["from"], ok=ok,
                      intended=pp["to"], got=idx,
                      by_intended=(ok and idx == pp["to"]))
            self._pending_pass = None
        self._carry_since = self.clock_s
        if self._shot is not None:
            sh = self._shot
            if side != sh["side"]:
                self._log("shot_stopped", side, idx, by_gk=(self.teams[side][idx]["pos"] == "GK"))
            self._shot = None
        self._log("control", side, idx, turnover=(prev is not None and prev != side))
        self._decide_at = self.clock_s + self.rng.uniform(0.15, 0.45)

    def _log(self, typ, side, idx, **kw):
        self.events.append(dict(t=self.clock_s, type=typ, side=side, idx=idx, **kw))

    def _snapshot(self):
        return {
            "clock": self.clock_s / 60.0,
            "home": [(p["x"], p["y"]) for p in self.home_players],
            "away": [(p["x"], p["y"]) for p in self.away_players],
            "ball": (self.ball["x"], self.ball["y"]),
            "score_home": self.score_home,
            "score_away": self.score_away,
            "banner_text": self.banner_text,
            "banner_color": self.banner_color,
            "banner_alpha": self.banner_alpha,
            "last_restart_clock": self._last_restart_clock,
            "possession": self.possession,
        }

    def _derive_stats(self):
        ev = self.events
        tot = max(1, self._poss_ticks["home"] + self._poss_ticks["away"])
        out = {}
        for side in ("home", "away"):
            shots = [e for e in ev if e["type"] == "shot" and e["side"] == side]
            goals = len([e for e in ev if e["type"] == "goal" and e["side"] == side])
            passes = [e for e in ev if e["type"] == "pass" and e["side"] == side]
            ctrls = [e for e in ev if e["type"] == "control" and e["side"] == side]
            completed = len([e for e in ctrls if not e.get("turnover")])
            out[side] = {
                "poss": round(self._poss_ticks[side] / tot * 100, 1),
                "shots": len(shots),
                "shots_on": goals + len([e for e in ev if e["type"] == "shot_stopped"
                                         and e["side"] != side]),
                "goals": goals,
                "corners": len([e for e in ev if e["type"] == "corner" and e["side"] == side]),
                "fouls": len([e for e in ev if e["type"] == "foul" and e["side"] == side]),
                "passes": len(passes),
                "pass_acc": round(completed / max(1, len(passes)) * 100, 1),
            }
        return out

    # ── 뷰어 호환 ──
    def _display_halves(self, elapsed):
        fh = 45 + self.stoppage1
        if elapsed <= 45:
            return (f"{int(elapsed)}'", "0'")
        if elapsed <= fh:
            return (f"45+{int(elapsed - 45)}'", "0'")
        second = elapsed - fh
        if second <= 45:
            return (f"45+{self.stoppage1}'", f"{int(second)}'")
        return (f"45+{self.stoppage1}'", f"45+{int(second - 45)}'")

    def _export_debug_capture(self):
        return {"ok": False, "error": "live engine: not implemented"}