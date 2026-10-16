# -*- coding: utf-8 -*-
"""match_sim/chain_engine.py — 점유 체인 경기 엔진 [2026-10, 신민용 확정 설계 v1.3]

기존 tactical_engine(1분에 결과 하나)과 나란히 두는 새 엔진. 검증이 끝날 때까지 게임 어디에도
연결하지 않는다 — 채택되면 tactical_engine이 이 엔진만 부르고 분 단위 루프는 지운다(스위치 없음),
기각되면 이 모듈을 지운다.

원리(설계 v1.3)
  · 경기 = 점유의 연속, 점유 = 행동(패스·드리블·크로스·슈팅 …)의 사슬.
  · 행동마다 걸리는 시간이 다르고, 경기 재개(스로인·골킥·코너·프리킥·득점 후 킥오프·교체·카드)
    시간도 시계에 더해진다. 하프는 경기 시계 45:00(연장 15:00)에 정규시간이 끝나고, 그 하프에서
    쌓인 지연으로 정한 추가시간이 그 뒤에 이어진다.
  · 모든 경합은 p = sigmoid(logit(기본 성공률) + k·(공격 합성값 − 수비 합성값)/10 + 보정).
    능력치가 같으면 정확히 기본 성공률이 나온다.
  · 수비 기록은 실제 경합에서만 생긴다(태클 ↔ 드리블·볼 뺏김, 가로채기 ↔ 패스 실패, 클리어 ↔
    크로스·롱볼, 블록 ↔ 슈팅, 공중볼 승 ↔ 공중볼 패).
  · 파울·카드·PK·오프사이드·코너는 기존 설계 A 규칙(확률 상수)을 그대로 쓰고, "언제 판정하느냐"만
    체인의 실제 장면으로 바뀐다. 오프사이드는 침투 수신 패스에서만, 코너는 블록·선방·크로스 클리어
    에서만, PK는 박스 안 파울에서만 나온다.
  · 기록 → 평점: 평점 장부(rating_ledger)는 이 엔진의 사건 기록만 읽는다(가중치 불변).

출력은 tactical_engine.simulate_tactical_match와 같은 키(+ "time_info")라 화면·저장·평점 코드를
고치지 않고 그대로 쓸 수 있다. 사건마다 하프(half)·하프 안 경과 초(sec)를 함께 남긴다 — 45+N 같은
추가시간 표시는 별도 UI 작업에서 이 값을 읽어 만든다. "min"(내부 분)은 기존 규칙 그대로:
  전반 1~45(전반 추가시간은 45.99대로 압축) / 후반 46~90, 91~96 = 90+1~6 / 연장 97~112, 113~128.
"""
import math
import random

from match_sim.tactical_engine import (
    LANES, REG_MINUTES, ET_HALF_MINUTES, _FALLBACK_SLOTS, _POS_XY, _third_of, _assign_zones, _pkey,
    _pstat, _new_stats_detail, _Stamina, _build_player_ratings, effective_stat,
    ATT_LANE_AFFINITY, DEF_LANE_AFFINITY, _boost_weights_for, _DEFAULT_BOOST_WEIGHTS,
    STAMINA_MODEL, SUBS_MAX_PLAYERS, SUBS_MAX_WINDOWS, SUBS_EXTRA_TIME_WINDOWS, SUB_STAMINA_TRIGGER,
    YELLOW_PER_FOUL, YELLOW_TACTICAL_BONUS, RED_PER_FOUL, RED_DOGSO, BOOKED_CAUTION,
    OFFSIDE_LINE_MULT, _MATCH_FORM_SIGMA, display_minute)

D3, M3, A3, BOX = 0, 1, 2, 3          # 공격팀 기준 구역
_MIRROR = {D3: A3, M3: M3, A3: D3, BOX: D3}
_ZNAME = {D3: "def", M3: "mid", A3: "att", BOX: "att"}

# ── 행동 선택 기본 비율(설계 3-1) ──────────────────────────────────────
ACTIONS = ("short", "fwd", "long", "through", "drib", "cross", "shot")
MIX = {
    D3:  {"short": 70, "fwd": 20, "long": 8, "drib": 2},
    M3:  {"short": 58, "fwd": 26, "long": 4, "through": 0.8, "drib": 3.0, "shot": 0.12},
    A3:  {"short": 38, "fwd": 12, "through": 2, "drib": 5.5, "cross": 28, "shot": 1.7},
    BOX: {"short": 40, "drib": 12, "shot": 16},
}
BUILDUP_MULT = {   # 감독 빌드업 성향 → 행동 비율 배수
    "POSSESSION": {"short": 1.25, "fwd": 0.95, "long": 0.5},
    "MIXED": {},
    "DIRECT": {"short": 0.75, "fwd": 1.2, "long": 1.9},
}
ATTACK_MULT = {    # 감독 공격 성향 → 행동 비율 배수(약하게)
    "VERY_ATTACKING": {"shot": 1.12, "through": 1.2, "drib": 1.1, "cross": 1.05},
    "ATTACKING": {"shot": 1.06, "through": 1.1, "drib": 1.05},
    "BALANCED": {},
    "DEFENSIVE": {"through": 0.9, "drib": 0.92, "short": 1.05},
    "VERY_DEFENSIVE": {"through": 0.8, "drib": 0.85, "short": 1.1},
}
COUNTER_MULT = {"short": 0.45, "fwd": 1.6, "through": 2.2, "drib": 1.4, "long": 0.8}
GK_SHORT_P = {"POSSESSION": 0.80, "MIXED": 0.60, "DIRECT": 0.30}

# ── 기본 성공률·기울기(설계 3-2, 5-1) ─────────────────────────────────
B_SHORT, B_FWD, B_LONG_ACC, B_THROUGH, B_DRIB, B_CROSS_ACC = 0.90, 0.74, 0.80, 0.33, 0.55, 0.50
B_RECV_TACKLE = 0.42          # 압박 속 수신 경합에서 수비가 이길 기본 확률
K_PASS, K_DUEL, K_SHOT, K_GK = 0.15, 0.25, 0.15, 0.15
PRESS_PASS_K = 1.2            # 압박 0.5 기준 ±만큼 패스 logit 감소
ZONE_PASS_ADJ = {D3: 0.3, M3: 0.0, A3: -0.5, BOX: -0.7}   # 상대 진영일수록 공간이 좁다(logit)
MAN_ADV = 0.18                # 인원 차 1명당 공격 경합 logit
COUNTER_ADV = 0.35            # 역습(수비 정렬 안 됨) 공격 경합 logit
AERIAL_DEF_EDGE = 0.25        # 공중볼 — 자리 잡은 수비 쪽 유리

# 압박 강도(수비팀 성향 × 공격팀 기준 구역)
PRESS_TAB = {
    "HIGH_PRESS": {D3: 0.75, M3: 0.60, A3: 0.50, BOX: 0.60},
    "MID_BLOCK":  {D3: 0.35, M3: 0.60, A3: 0.55, BOX: 0.60},
    "LOW_BLOCK":  {D3: 0.15, M3: 0.40, A3: 0.65, BOX: 0.70},
}
RECV_DUEL = 0.005              # 패스를 받는 순간 수비가 태클 경합을 거는 확률(× 압박)
RECV_FOUL = 0.038             # 받는 순간 밀기·잡아채기 파울(× 압박) — 태클 없는 파울
# 패스 실패의 결과 — (가로채기, 걷어내기, 볼 회수(기록 없음), 루즈볼, 아웃). 목표 구역별.
FAIL_SPLIT = {"low": (0.18, 0.00, 0.57, 0.10, 0.15), "high": (0.18, 0.18, 0.39, 0.05, 0.20)}

# ── 사건 확률 ─────────────────────────────────────────────────────────
FOUL_TACKLE = 0.20            # 태클 경합(드리블·수신 압박)에서 수비가 지거나 무리할 때 파울
FOUL_AERIAL = 0.05
TACTICAL_FOUL = 0.06          # 역습 저지용 전술 파울(역습 행동마다)
BOX_FOUL_MULT = 0.15          # 박스 안에서는 PK를 피하려고 훨씬 조심한다
OFF_FWD_BOX, OFF_LONG, OFF_THROUGH = 0.035, 0.08, 0.13
ADV_AFTER_DRIB = {D3: 0.45, M3: 0.45, A3: 0.35}
BACK_PASS = {M3: 0.12, A3: 0.15}
GK_CLAIM_CROSS = 0.08
LONG_AERIAL = 0.40            # 정확한 롱볼 중 공중 경합이 되는 비율
BOX_PASS_PEN = 0.6            # 박스 안으로 넣는 패스는 수비가 촘촘해 더 어렵다(logit)
CROSS_AIR_DEF_EDGE = 0.20     # 크로스 공중볼 — 박스 안 수비·GK 위치 이점(추가 logit)
HEADER_SHOT_P = 0.50          # 크로스 공중볼을 이긴 뒤 바로 헤더 슈팅(나머지는 떨궈 이어감)
SHORT_CORNER = 0.15
INJURY_PER_MIN = 0.004

# ── 슈팅(설계 3-2 슈팅) ──────────────────────────────────────────────
XG_BASE = {"one_v_one": 0.36, "cutback": 0.16, "box_dribble": 0.16, "box": 0.11, "header": 0.10,
           "corner_header": 0.08, "rebound": 0.28, "long": 0.035, "far": 0.015, "fk": 0.065,
           "pk": 0.76}
BLOCK_P = {"one_v_one": 0.05, "cutback": 0.15, "box_dribble": 0.18, "box": 0.22, "header": 0.05,
           "corner_header": 0.06, "rebound": 0.20, "long": 0.30, "far": 0.30, "fk": 0.22, "pk": 0.0}
ON_BASE = {"one_v_one": 0.60, "cutback": 0.48, "box_dribble": 0.48, "box": 0.46, "header": 0.37,
           "corner_header": 0.35, "rebound": 0.47, "long": 0.36, "far": 0.28, "fk": 0.37, "pk": 0.85}
XG_SCALE = 0.78
HOME_EDGE = 1.2               # home_adv(기본 3.0) × 이 값만큼 홈 선수 합성값 +
FORM_SCALE = 0.6

# ── 시간(초) — 설계 4장 ──────────────────────────────────────────────
T_ACT = {"short": (2, 4.5), "fwd": (3, 6), "long": (4, 7), "through": (3, 6), "drib": (2, 5),
         "cross": (2, 4), "shot": (1, 2), "loose": (2, 4), "gk": (4, 9)}
T_DEAD = {"throw": (12, 20), "goalkick": (18, 28), "corner": (25, 35), "fk": (20, 40),
          "fk_danger": (40, 60), "pk": (45, 70), "goal": (50, 70), "sub": (20, 30), "card": (15, 30),
          "offside": (15, 25), "injury": (60, 120)}
HALF_SEC, ET_SEC = 45 * 60, 15 * 60

_SUB_DECISION = (56, 62, 68, 74, 80, 86, REG_MINUTES + 8, REG_MINUTES + ET_HALF_MINUTES + 8)
_SLOT_PEN = (0, 5, 9, 13, 13)
_WIDE = ("LB", "RB", "LWB", "RWB", "LM", "RM", "LW", "RW")

# 행동 주체 고르기 — (구역, 역할) 가중치. 역할은 GK/DEF/MID/ATT.
ATT_ROLE_W = {D3: {"GK": 0.25, "DEF": 1.0, "MID": 0.8, "ATT": 0.15},
              M3: {"GK": 0.0, "DEF": 0.55, "MID": 1.0, "ATT": 0.45},
              A3: {"GK": 0.0, "DEF": 0.25, "MID": 0.9, "ATT": 1.0},
              BOX: {"GK": 0.0, "DEF": 0.12, "MID": 0.6, "ATT": 1.0}}
DEF_ROLE_W = {D3: {"GK": 0.0, "DEF": 0.15, "MID": 0.7, "ATT": 1.0},
              M3: {"GK": 0.0, "DEF": 0.5, "MID": 1.0, "ATT": 0.45},
              A3: {"GK": 0.0, "DEF": 1.0, "MID": 0.8, "ATT": 0.15},
              BOX: {"GK": 0.0, "DEF": 1.0, "MID": 0.45, "ATT": 0.08}}


def _logit(p):
    p = min(0.999, max(0.001, p))
    return math.log(p / (1.0 - p))


def _sig(x):
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


L_SHORT, L_FWD, L_LONG_ACC, L_THROUGH = _logit(B_SHORT), _logit(B_FWD), _logit(B_LONG_ACC), _logit(B_THROUGH)
L_DRIB, L_CROSS_ACC, L_RECV_TACKLE = _logit(B_DRIB), _logit(B_CROSS_ACC), _logit(B_RECV_TACKLE)


def _g(p, k):
    v = p.get(k)
    return 50.0 if v is None else float(v)


class _PE:
    """경기장 위 선수 한 명(그 순간의 합성 능력치 포함)."""
    __slots__ = ("p", "pos", "lane", "third", "role", "pas", "prg", "run", "drb", "tck", "prs", "air",
                 "crs", "sht", "hed", "blk", "gkq", "lin", "fk", "wide")

    def __init__(self, z, perf, a_add, d_add, g_add):
        p = self.p = z["player"]
        self.pos, self.lane, self.third = z["pos"], z["lane"], z["third"]
        self.role = "GK" if self.pos == "GK" else self.third
        self.wide = self.pos in _WIDE
        f = perf
        pas, posi, conc, conf, ment = _g(p, "passing"), _g(p, "positioning"), _g(p, "concentration"), \
            _g(p, "confidence"), _g(p, "mental")
        spd, strg, jump, head = _g(p, "speed"), _g(p, "strength"), _g(p, "jump"), _g(p, "heading")
        drb, tck, sho, sp = _g(p, "dribbling"), _g(p, "tackling"), _g(p, "shooting"), _g(p, "setpiece")
        self.pas = (pas * 0.70 + conc * 0.15 + conf * 0.15) * f + a_add
        self.prg = (pas * 0.60 + ment * 0.20 + posi * 0.20) * f + a_add
        self.run = (posi * 0.60 + spd * 0.25 + conc * 0.15) * f + a_add
        self.drb = (drb * 0.55 + spd * 0.25 + conf * 0.10 + strg * 0.10) * f + a_add
        self.crs = (pas * 0.50 + sp * 0.30 + conc * 0.20) * f + a_add
        self.sht = (sho * 0.60 + ment * 0.20 + conf * 0.20) * f + a_add
        self.hed = (head * 0.70 + jump * 0.30) * f + a_add
        self.fk = sp * f + a_add
        self.tck = (tck * 0.50 + posi * 0.25 + spd * 0.25) * f + d_add
        self.prs = (posi * 0.50 + tck * 0.30 + spd * 0.20) * f + d_add
        self.blk = (posi * 0.60 + conc * 0.20 + strg * 0.20) * f + d_add
        self.lin = (posi * 0.60 + conc * 0.40) * f + d_add
        self.air = (head * 0.40 + jump * 0.40 + strg * 0.20) * f + (a_add + d_add) * 0.5
        self.gkq = (posi * 0.5 + conc * 0.3 + jump * 0.2) * f + g_add


class _Side:
    def __init__(self, name, lineup, slots, bench, boost, bpos, style, form, home_add):
        self.name = name
        self.is_home = name == "home"
        self.cur = list(lineup)
        self.slots = slots
        self.bench = [p for p in (bench or []) if p]
        self.subs, self.used, self.windows, self.appear = [], 0, SUBS_MAX_WINDOWS, {}
        self.sent = []
        self.ps = {}
        self.stats = {"shots": 0, "shots_on": 0, "corners": 0, "fouls": 0, "offsides": 0,
                      "yellow_cards": 0, "red_cards": 0, "saves": 0}
        self.detail = _new_stats_detail()
        self.boost, self.bpos = boost, bpos
        self.style = style or {}
        self.buildup = self.style.get("style_buildup") or "MIXED"
        self.press_style = self.style.get("style_press") or "MID_BLOCK"
        self.attack = self.style.get("style_attack") or "BALANCED"
        self.form = form
        self.home_add = home_add
        self.ents, self.gk = [], None
        self.att_tab, self.def_tab = {}, {}
        self.n_field, self.perf_avg, self.line_q, self.mid_q = 10, 1.0, 50.0, 50.0
        self.inplay = 0.0
        self.score = 0

    def labels(self):
        return self.slots or _FALLBACK_SLOTS


def _build_tab(ents, role_w, aff_table, gk_lane_c=0.0):
    tab = {}
    for z in (D3, M3, A3, BOX):
        rw = role_w[z]
        for ln in LANES:
            pop, cum, s = [], [], 0.0
            for e in ents:
                w = rw.get(e.role, 0.0)
                if w <= 0:
                    continue
                if e.role == "GK":
                    lw = 1.0 if ln == "C" else gk_lane_c
                else:
                    lw = aff_table.get(e.pos, {}).get(ln, 0.05) + 0.12
                w *= lw
                if w <= 0:
                    continue
                s += w
                pop.append(e)
                cum.append(s)
            if not pop:
                pop = [e for e in ents if e.role != "GK"] or list(ents)
                cum = [float(i + 1) for i in range(len(pop))]
            tab[(z, ln)] = (pop, cum)
    return tab


class _ChainMatch:
    def __init__(self, home_lineup, away_lineup, home_boost, away_boost, home_boost_position,
                 away_boost_position, home_adv, seed, home_formation, away_formation, home_bench,
                 away_bench, extra_time, agg_home, agg_away, home_style, away_style, player_mods=None):
        # 시드가 없으면 전역 random에서 시드를 하나 뽑는다 — 게임 전체 난수 흐름(세이브 재현성)과 이어지게.
        self.rng = random.Random(seed) if seed is not None else random.Random(random.getrandbits(64))
        # [G1] 선수별 카드 성향 보정 {선수 id: {"yellow_add", "red_add"}} — 내 선수 성격('폭력적') 등.
        self.pmods = player_mods or {}
        r = self.rng
        from constants import FORMATION_SLOTS
        hs = FORMATION_SLOTS.get(home_formation) if home_formation else None
        as_ = FORMATION_SLOTS.get(away_formation) if away_formation else None
        hf, af = r.gauss(0, _MATCH_FORM_SIGMA), r.gauss(0, _MATCH_FORM_SIGMA)
        self.H = _Side("home", home_lineup, hs, home_bench, home_boost, home_boost_position, home_style,
                       hf, HOME_EDGE * float(home_adv or 0.0))
        self.A = _Side("away", away_lineup, as_, away_bench, away_boost, away_boost_position, away_style,
                       af, 0.0)
        self.extra_time, self.agg_home, self.agg_away = extra_time, agg_home, agg_away
        self.stam = _Stamina(enabled=bool(STAMINA_MODEL))
        self.plog, self.pass_acc = [], {}
        self.half, self.sec, self.added = 1, 0.0, None
        self.reg, self.end, self.whistle, self.half_len = HALF_SEC, None, False, {}
        self.is_extra = False
        self.half_delays = {"goals": 0, "subs": 0, "cards": 0, "inj": 0}
        self.pending_sub_minutes = []
        self.pending_dead = 0.0
        self.last_min_int = 0
        self.bip, self.dead = 0.0, 0.0
        self.added_log = {}
        self.next_rebuild_min = 5
        for sd in (self.H, self.A):
            labels = sd.labels()
            for i, p in enumerate(sd.cur):
                if p is None:
                    continue
                self.stam.ensure(p)
                sd.appear[_pkey(p)] = {"player": p, "slot": (labels[i] if i < len(labels) else
                                                            p.get("position", "CM")),
                                       "slot_idx": i, "on": 0, "off": None, "started": True}
            for p in sd.bench:
                self.stam.ensure(p)
        self.rebuild(self.H)
        self.rebuild(self.A)
        # 점유 상태
        self.att = self.H
        self.zone, self.lane, self.car = M3, "C", None
        self.counter, self.ctx, self.assist_cand = 0, None, None
        self.dead_ball = True

    # ── 모델(합성 능력치·선택표) ──────────────────────────────────────
    def other(self, sd):
        return self.A if sd is self.H else self.H

    def rebuild(self, sd):
        w = _boost_weights_for(sd.bpos) if sd.boost else _DEFAULT_BOOST_WEIGHTS
        base = sd.form * FORM_SCALE + sd.home_add
        a_add = base + sd.boost * w["att"] + sd.boost * w["mid"] * 0.5
        d_add = base + sd.boost * w["dfn"]
        g_add = sd.form * 0.5 + sd.home_add + sd.boost * w["gk"]
        zoned = _assign_zones(sd.cur, sd.is_home, sd.slots)
        perf = self.stam.perf
        ents = [_PE(z, perf(z["player"]), a_add, d_add, g_add) for z in zoned if z["player"] is not None]
        sd.ents = ents
        sd.gk = next((e for e in ents if e.pos == "GK"), None)
        field = [e for e in ents if e.pos != "GK"]
        sd.n_field = len(field)
        sd.perf_avg = (sum(perf(e.p) for e in field) / len(field)) if field else 1.0
        dl = [e.lin for e in field if e.third == "DEF"] or [e.lin for e in field] or [50.0]
        sd.line_q = sum(dl) / len(dl)
        ml = [e.prs for e in field if e.third == "MID"] or [e.prs for e in field] or [50.0]
        sd.mid_q = sum(ml) / len(ml)
        sd.att_tab = _build_tab(ents, ATT_ROLE_W, ATT_LANE_AFFINITY, 0.3)
        sd.def_tab = _build_tab(ents, DEF_ROLE_W, DEF_LANE_AFFINITY, 0.0)

    def pick(self, tab, z, ln, exclude=None):
        pop, cum = tab[(z, ln)]
        r = self.rng
        e = r.choices(pop, cum_weights=cum, k=1)[0]
        if exclude is not None and e is exclude and len(pop) > 1:
            for _ in range(3):
                e = r.choices(pop, cum_weights=cum, k=1)[0]
                if e is not exclude:
                    break
        return e

    def pick_att(self, sd, z, ln, exclude=None):
        return self.pick(sd.att_tab, z, ln, exclude)

    def pick_def(self, sd, z, ln):
        return self.pick(sd.def_tab, z, ln)

    def pick_air(self, sd, z, ln, defending):
        tab = sd.def_tab if defending else sd.att_tab
        pop, cum = tab[(z, ln)]
        ws = [max(1.0, e.air) ** 2 for e in pop]
        return self.rng.choices(pop, weights=ws, k=1)[0]

    # ── 시계 ──────────────────────────────────────────────────────────
    def cur_min(self):
        """(하프, 경과초) → 내부 분. 정수부는 축구 표기(경과 0:00~0:59 = 1분)와 같다.
        전반 추가시간은 기존 규칙상 45분 안에 있어야 하므로 45분째(44:00~45:00)를 45.0~45.5,
        추가시간을 45.5~46으로 압축한다(순서 보존). 연장 후반 추가시간도 같은 방식(127.5~128).
        휘슬 시각(정규+추가 끝) 그 자체는 마지막 분에 넣는다."""
        s, h = self.sec, self.half
        if self.end is not None:
            s = min(s, self.end - 0.01)
        if h == 1:
            if s < HALF_SEC - 60:
                return 1.0 + s / 60.0
            if s < HALF_SEC:
                return 45.0 + (s - (HALF_SEC - 60)) / 120.0
            return 45.5 + 0.49 * min(1.0, (s - HALF_SEC) / max(1.0, self.added or 1.0))
        if h == 2:
            return 46.0 + s / 60.0 if s < HALF_SEC else min(96.999, 91.0 + (s - HALF_SEC) / 60.0)
        if h == 3:
            return 97.0 + s / 60.0 if s < ET_SEC else 112.0 + 0.999 * min(1.0, (s - ET_SEC) / max(1.0, self.added or 1.0))
        if s < ET_SEC - 60:
            return 113.0 + s / 60.0
        if s < ET_SEC:
            return 127.0 + (s - (ET_SEC - 60)) / 120.0
        return 127.5 + 0.49 * min(1.0, (s - ET_SEC) / max(1.0, self.added or 1.0))

    def tick(self, secs, inplay=True):
        """경기 시계를 secs초 진행. 정규시간(45:00/15:00)에 닿는 순간 그 하프의 추가시간을 정하고,
        정규+추가시간에 닿으면 거기서 시계를 멈춘다(그 뒤 사건은 종료 시각에 찍힌다 — 휘슬)."""
        if secs <= 0 or self.whistle:
            return
        if self.added is None and self.sec + secs >= self.reg:
            am = self.added_minutes(self.half)
            self.added = am * 60.0
            self.added_log[self.half] = am
            self.end = self.reg + self.added
        if self.end is not None and self.sec + secs >= self.end:
            secs = max(0.0, self.end - self.sec)
            self.whistle = True
        self.sec += secs
        if inplay:
            self.bip += secs
            self.att.inplay += secs
        else:
            self.dead += secs
        m = int(self.cur_min())
        while self.last_min_int < m:
            self.last_min_int += 1
            self.on_minute(self.last_min_int)

    def on_minute(self, m):
        """경기 1분마다 — 체력, 교체 판단 예약, 부상 지연, 모델 갱신."""
        if self.stam.enabled:
            ext = self.half >= 3
            for sd in (self.H, self.A):
                zoned = [{"player": e.p, "pos": e.pos, "third": e.third} for e in sd.ents]
                self.stam.drain_minute(zoned, self.att is sd, ext)
        if m in _SUB_DECISION:
            self.pending_sub_minutes.append(m)
        if self.rng.random() < INJURY_PER_MIN:
            self.pending_dead += self.rng.uniform(*T_DEAD["injury"])
            self.half_delays["inj"] += 1
        if m >= self.next_rebuild_min:
            self.next_rebuild_min = m + 5
            self.rebuild(self.H)
            self.rebuild(self.A)

    def rec(self, outcome, team, zone=None, lane=None, **kw):
        d = {"min": round(self.cur_min(), 4), "team": team.name,
             "zone": _ZNAME.get(self.zone if zone is None else zone, "mid"),
             "lane": self.lane if lane is None else lane,
             "outcome": outcome, "me": False, "text": None, "half": self.half, "sec": round(self.sec, 1)}
        d.update(kw)
        self.plog.append(d)
        return d

    def add_pass(self, sd, e, ok):
        k = (sd.name, int(self.cur_min()))
        bucket = self.pass_acc.get(k)
        if bucket is None:
            bucket = self.pass_acc[k] = {}
        pid = e.p.get("id")
        v = bucket.get(pid)
        if v is None:
            v = bucket[pid] = [pid, 0, 0]
        v[1] += 1
        ps = _pstat(sd.ps, e.p)
        ps["passes"] += 1
        sd.detail["passes"] += 1
        if ok:
            v[2] += 1
            ps["passes_ok"] += 1
            sd.detail["passes_ok"] += 1

    # ── 상황 값 ───────────────────────────────────────────────────────
    def pressure(self, D, z):
        pr = PRESS_TAB.get(D.press_style, PRESS_TAB["MID_BLOCK"])[z] * D.perf_avg * (D.n_field / 10.0)
        if self.counter > 0:
            pr *= 0.6
        sc_d, sc_a = D.score, self.other(D).score
        if sc_d < sc_a and self.cur_min() >= 70:
            pr += 0.08
        return max(0.05, min(0.95, pr))

    def att_mod(self, A, D):
        m = MAN_ADV * (A.n_field - D.n_field)
        if self.counter > 0:
            m += COUNTER_ADV
        return m

    # ── 경기 재개 ─────────────────────────────────────────────────────
    def dead_time(self, kind):
        t = self.rng.uniform(*T_DEAD[kind]) + self.pending_dead
        self.pending_dead = 0.0
        self.tick(t, inplay=False)
        self.dead_ball = True
        if self.pending_sub_minutes:
            self.pending_sub_minutes = []
            did = self.try_subs(int(self.cur_min()), False)
            if did:
                self.tick(self.rng.uniform(*T_DEAD["sub"]), inplay=False)

    def start_poss(self, sd, z, ln, carrier=None, counter=0, ctx=None):
        self.att = sd
        self.zone, self.lane = z, ln
        self.car = carrier if carrier is not None else self.pick_att(sd, z, ln)
        self.counter = counter
        self.ctx = ctx
        self.assist_cand = None

    def kickoff(self, sd):
        self.start_poss(sd, M3, "C")

    def throw_in(self, sd, z, ln):
        self.dead_time("throw")
        if ln == "C":
            ln = self.rng.choice(("L", "R"))
        self.start_poss(sd, z, ln)

    def goal_kick(self, sd):
        self.dead_time("goalkick")
        self.gk_ball(sd, from_restart=True)

    def gk_ball(self, sd, from_restart=False):
        """GK가 볼을 잡았다 — 짧게 굴리거나 길게 찬다(감독 빌드업 성향)."""
        if sd.gk is None:
            self.start_poss(sd, D3, "C")
            return
        if not from_restart:
            self.tick(self.rng.uniform(*T_ACT["gk"]))
        self.start_poss(sd, D3, "C", carrier=sd.gk, ctx="gk")

    def loose_ball(self, z, ln, p_att=0.5):
        """루즈볼 — 공격팀 기준 구역 z에서 누가 따내나."""
        A, D = self.att, self.other(self.att)
        self.tick(self.rng.uniform(*T_ACT["loose"]))
        # 그 구역 양 팀 압박·중원 능력 차로 약간 기울인다
        diff = (A.mid_q - D.mid_q) / 10.0 * 0.3
        if self.rng.random() < _sig(_logit(p_att) + diff):
            self.start_poss(A, z, ln)
        else:
            self.start_poss(D, _MIRROR[z], ln)

    def turnover(self, new_sd, old_zone, ln, carrier=None, how=None):
        nz = _MIRROR[old_zone]
        cnt = 3 if (how in ("interception", "tackle") and old_zone >= A3 and nz <= M3) else 0
        if how in ("interception", "tackle", "recovery"):
            new_sd.detail["turnovers_won"] += 1
            self.other(new_sd).detail["turnovers_lost"] += 1
        self.start_poss(new_sd, nz, ln, carrier=carrier, counter=cnt)

    # ── 파울·카드·퇴장 ────────────────────────────────────────────────
    def clumsy(self, D, d):
        c = 1.0 + max(-0.3, min(0.5, (60.0 - _g(d.p, "tackling")) / 80.0))
        if _pstat(D.ps, d.p).get("yellow"):
            c *= BOOKED_CAUTION
        return c

    def foul(self, D, d, A, v, z, ln, tactical=False, dogso=False):
        """수비 D의 선수 d가 공격 A의 선수 v에게 파울 — 카드 판정 후 프리킥/PK로 재개."""
        D.stats["fouls"] += 1
        _pstat(D.ps, d.p)["fouls"] += 1
        _pstat(A.ps, v.p)["fouled"] += 1
        pk = (z == BOX)
        fr = self.rec("foul", D, zone=_MIRROR[z], lane=ln, player_id=d.p.get("id"),
                      victim_id=v.p.get("id"))
        if pk:
            fr["pk"] = True
            _pstat(A.ps, v.p)["pk_won"] += 1
            _pstat(D.ps, d.p)["pk_conceded"] += 1
        r = self.rng.random()
        p_red = RED_PER_FOUL + (RED_DOGSO if dogso else 0.0)
        p_y = YELLOW_PER_FOUL + (YELLOW_TACTICAL_BONUS if (tactical or z >= A3) else 0.0)
        _pm = self.pmods.get(d.p.get("id"))
        if _pm:
            p_red += _pm.get("red_add", 0.0)
            p_y += _pm.get("yellow_add", 0.0)
        if r < p_red:
            self.send_off(D, d.p, "red")
            self.half_delays["cards"] += 1
            self.pending_dead += self.rng.uniform(*T_DEAD["card"])
        elif r < p_red + p_y:
            self.book(D, d.p)
            self.half_delays["cards"] += 1
            self.pending_dead += self.rng.uniform(*T_DEAD["card"])
        if pk:
            A.detail["penalties"] += 1
            self.dead_time("pk")
            taker = max((e for e in A.ents if e.pos != "GK"), key=lambda e: (e.fk, e.sht), default=None)
            if taker is not None:
                self.start_poss(A, BOX, "C", carrier=taker)
                self.shot(A, self.other(A), taker, "pk", kind="pk")
            return
        A.detail["free_kicks"] += 1
        if z == A3:
            self.dead_time("fk_danger")
            self.free_kick_danger(A, ln, v)
        else:
            self.dead_time("fk")
            self.start_poss(A, z, ln, carrier=v)

    def free_kick_danger(self, A, ln, v):
        r = self.rng.random()
        p_shot = 0.30 if ln == "C" else 0.06
        p_cross = 0.25 if ln == "C" else 0.60
        taker = max((e for e in A.ents if e.pos != "GK"), key=lambda e: (e.fk, e.sht), default=v)
        if r < p_shot:
            self.start_poss(A, A3, ln, carrier=taker)
            self.shot(A, self.other(A), taker, "fk", kind="fk")
        elif r < p_shot + p_cross:
            self.start_poss(A, A3, "L" if ln == "C" else ln, carrier=taker)
            self.act_cross(set_piece=True)
        else:
            self.start_poss(A, A3, ln, carrier=v)

    def book(self, sd, p):
        ps = _pstat(sd.ps, p)
        ps["yellow"] += 1
        sd.stats["yellow_cards"] += 1
        self.rec("yellow", sd, player_id=p.get("id"))
        if ps["yellow"] >= 2:
            self.send_off(sd, p, "second_yellow")

    def send_off(self, sd, p, reason):
        idx = next((i for i, q in enumerate(sd.cur) if q is p), None)
        if idx is None:
            return
        labels = sd.labels()
        slot = labels[idx] if idx < len(labels) else p.get("position", "CM")
        _pstat(sd.ps, p)["red"] += 1
        sd.stats["red_cards"] += 1
        m = self.cur_min()
        self.rec("red", sd, player_id=p.get("id"), reason=reason)
        sd.cur[idx] = None
        ap = sd.appear.get(_pkey(p))
        if ap is not None:
            ap["off"] = m
            ap["sent_off"] = True
        sd.sent.append(p)
        if slot == "GK":
            outs = [(i, q) for i, q in enumerate(sd.cur)
                    if q is not None and (labels[i] if i < len(labels) else "") != "GK"]
            bgk = next((b for b in sd.bench if b.get("position") == "GK"), None)
            if outs and bgk is not None and sd.used < SUBS_MAX_PLAYERS and sd.windows > 0:
                def _att_rank(t):
                    _sl = labels[t[0]] if t[0] < len(labels) else "CM"
                    return (_third_of(_POS_XY.get(_sl, (0.3, 0.5))[0]) == "ATT", -self.stam.get(t[1]))
                oi, op = max(outs, key=_att_rank)
                sd.cur[oi] = None
                oap = sd.appear.get(_pkey(op))
                if oap is not None:
                    oap["off"] = m
                sd.cur[idx] = bgk
                sd.bench.remove(bgk)
                sd.used += 1
                sd.windows -= 1
                sd.appear[_pkey(bgk)] = {"player": bgk, "slot": "GK", "slot_idx": idx,
                                         "on": m, "off": None, "started": False}
                sd.subs.append({"min": int(m), "disp": display_minute(int(m)), "slot": "GK",
                                "out_id": op.get("id"), "out_name": op.get("name"),
                                "out_stamina": round(self.stam.get(op), 1),
                                "in_id": bgk.get("id"), "in_name": bgk.get("name"),
                                "reason": "gk_sent_off", "extra_time": m > REG_MINUTES})
                self.rec("sub", sd, zone=M3, lane="C", in_id=bgk.get("id"), out_id=op.get("id"))
                self.half_delays["subs"] += 1
            elif outs:
                oi, op = max(outs, key=lambda t: ((t[1].get("jump") or 0) + (t[1].get("positioning") or 0)))
                sd.cur[oi] = None
                sd.cur[idx] = op
        self.rebuild(sd)
        # 퇴장으로 볼 가진 선수가 사라졌으면 다시 고른다
        if self.att is sd and (self.car is None or self.car.p is p):
            self.car = self.pick_att(sd, self.zone, self.lane)

    # ── 교체(기존 _try_subs와 같은 판단, 실행은 데드볼에서만) ────────────
    def slot_fit(self, p, slot):
        from constants import POSITION_COMPAT
        compat = POSITION_COMPAT.get(p.get("position"), [p.get("position")])
        try:
            idx = 0 if (p.get("sub_pos") and p.get("sub_pos") == slot) else compat.index(slot)
        except ValueError:
            idx = len(_SLOT_PEN) - 1
        return (p.get("ovr") or 50) - _SLOT_PEN[min(idx, len(_SLOT_PEN) - 1)]

    def try_subs(self, minute, free_window):
        did = False
        for sd in (self.H, self.A):
            if not sd.bench or sd.used >= SUBS_MAX_PLAYERS:
                continue
            if not free_window and sd.windows <= 0:
                continue
            labels = sd.labels()
            diff = sd.score - self.other(sd).score
            want = None
            if diff < 0 and minute >= 60:
                want = "attack"
            elif diff > 0 and minute >= 75:
                want = "defend"
            cands = []
            for i, p in enumerate(sd.cur):
                if p is None:
                    continue
                slot = labels[i] if i < len(labels) else p.get("position", "CM")
                if slot == "GK" or p.get("position") == "GK":
                    continue
                ap = sd.appear.get(_pkey(p)) or {}
                if not ap.get("started", True) and minute - (ap.get("on") or 0) < 20:
                    continue
                st = self.stam.get(p)
                third = _third_of(_POS_XY.get(slot, (0.3, 0.5))[0])
                tired = st < SUB_STAMINA_TRIGGER
                if not tired:
                    if not ((want == "attack" and third in ("DEF", "MID")) or (want == "defend" and third == "ATT")):
                        continue
                urge = SUB_STAMINA_TRIGGER - st
                if (want == "attack" and third in ("DEF", "MID")) or (want == "defend" and third == "ATT"):
                    urge += 10.0
                cands.append((urge, st, i, p, slot, third, tired))
            cands.sort(key=lambda t: (-t[0], t[1], t[2]))
            n_this = 0
            for urge, st, idx, out_p, slot, third, tired in cands:
                if n_this >= 2 or sd.used >= SUBS_MAX_PLAYERS:
                    break
                out_eff = self.slot_fit(out_p, slot) * self.stam.perf(out_p)
                best_in, best_sc = None, -1e9
                for cand in sd.bench:
                    if cand.get("position") == "GK":
                        continue
                    sc = self.slot_fit(cand, slot)
                    if want == "attack" and _third_of(_POS_XY.get(cand.get("position"), (0.3, 0.5))[0]) == "ATT":
                        sc += 3.0
                    if sc > best_sc:
                        best_in, best_sc = cand, sc
                if best_in is None:
                    continue
                ok = (best_sc >= out_eff - 3.0) if tired else (best_sc > out_eff - 6.0)
                if not ok:
                    continue
                m = minute
                sd.cur[idx] = best_in
                sd.bench.remove(best_in)
                sd.used += 1
                n_this += 1
                ap = sd.appear.get(_pkey(out_p))
                if ap is not None:
                    ap["off"] = m
                sd.appear[_pkey(best_in)] = {"player": best_in, "slot": slot, "slot_idx": idx,
                                             "on": m, "off": None, "started": False}
                sd.subs.append({"min": int(m), "disp": display_minute(int(m)), "slot": slot,
                                "out_id": out_p.get("id"), "out_name": out_p.get("name"),
                                "out_stamina": round(st, 1), "in_id": best_in.get("id"),
                                "in_name": best_in.get("name"),
                                "reason": ("tired" if tired else (want or "quality")),
                                "extra_time": bool(self.half >= 3)})
                self.rec("sub", sd, zone=M3, lane="C", in_id=best_in.get("id"), out_id=out_p.get("id"))
                self.half_delays["subs"] += 1
                did = True
            if n_this and not free_window:
                sd.windows -= 1
            if n_this:
                self.rebuild(sd)
                if self.att is sd and self.car is not None and self.car.p not in sd.cur:
                    self.car = self.pick_att(sd, self.zone, self.lane)
        return did

    # ── 행동 ──────────────────────────────────────────────────────────
    def choose_action(self):
        A, z = self.att, self.zone
        if self.ctx == "gk":
            return "short" if self.rng.random() < GK_SHORT_P.get(A.buildup, 0.6) else "long"
        if self.ctx == "one_v_one":
            return "shot"
        base = MIX[z]
        # [2026-10 안 A, 신민용 승인] 빌드업 성향은 볼을 전진시키는 구간(자기 진영·중원)에만 적용한다.
        # 상대 진영·박스의 공격 선택은 공격 성향 축(ATTACK_MULT)의 몫 — 예전엔 두 축이 같은 장면에
        # 겹쳐 걸려 DIRECT가 박스 투입을 과하게 늘렸다(동전력 승률 +10.2%p).
        bm = BUILDUP_MULT.get(A.buildup, {}) if z <= M3 else {}
        am = ATTACK_MULT.get(A.attack, {})
        late = self.cur_min() >= 75
        diff = A.score - self.other(A).score
        acts, ws = [], []
        for a, w in base.items():
            if a == "cross" and self.lane == "C":
                continue
            w = w * bm.get(a, 1.0) * am.get(a, 1.0)
            if self.counter > 0:
                w *= COUNTER_MULT.get(a, 1.0)
            if late and diff < 0:
                w *= {"long": 1.5, "cross": 1.4, "shot": 1.25, "short": 0.85}.get(a, 1.0)
            elif late and diff > 0 and self.cur_min() >= 80:
                w *= {"short": 1.25, "shot": 0.85, "through": 0.8}.get(a, 1.0)
            if a == "shot" and self.ctx in ("cutback", "box_dribble"):
                w *= 1.6
            acts.append(a)
            ws.append(w)
        if z == A3 and self.lane == "C":
            # 중앙 레인엔 크로스가 없다 — 그 몫을 짧은 패스·스루패스에 나눈다(설계 3-1)
            pass
        return self.rng.choices(acts, weights=ws, k=1)[0]

    def step(self):
        """현재 점유에서 행동 하나."""
        self.dead_ball = False
        A, D = self.att, self.other(self.att)
        if self.car is None or self.car.p not in A.cur:
            self.car = self.pick_att(A, self.zone, self.lane)
        # 역습을 끊는 전술 파울
        if self.counter > 0 and self.zone in (M3, A3) and self.rng.random() < TACTICAL_FOUL:
            d = self.pick_def(D, self.zone, self.lane)
            self.tick(self.rng.uniform(1, 3))
            self.foul(D, d, A, self.car, self.zone, self.lane, tactical=True,
                      dogso=(self.zone >= A3 and self.counter > 0))
            return
        a = self.choose_action()
        getattr(self, "act_" + a)()
        if self.counter > 0:
            self.counter -= 1

    def after_receive(self, recv, z):
        """패스를 받은 순간의 압박. True면 볼을 지켰다."""
        A, D = self.att, self.other(self.att)
        pr = self.pressure(D, z)
        u = self.rng.random()
        bm = BOX_FOUL_MULT if z == BOX else 1.0
        if u < RECV_FOUL * pr * 2.0 * bm:
            d = self.pick_def(D, z, self.lane)
            if self.rng.random() < self.clumsy(D, d):
                self.foul(D, d, A, recv, z, self.lane)
                return False
            return True
        if u >= (RECV_FOUL + RECV_DUEL) * pr * 2.0:
            return True
        d = self.pick_def(D, z, self.lane)
        p_def = _sig(L_RECV_TACKLE + K_DUEL * (d.tck - recv.drb) / 10.0 - self.att_mod(A, D))
        _pstat(D.ps, d.p)["tackles"] += 1
        D.detail["tackles"] += 1
        if self.rng.random() < FOUL_TACKLE * self.clumsy(D, d) * 0.5 * bm:
            self.rec("buildup", A, action="duel", actor_id=recv.p.get("id"), ok=True,
                     def_action="foul", def_actor_id=d.p.get("id"))
            _pstat(D.ps, d.p)["tackles"] -= 1
            D.detail["tackles"] -= 1
            self.foul(D, d, A, recv, z, self.lane)
            return False
        if self.rng.random() < p_def:
            _pstat(D.ps, d.p)["tackles_ok"] += 1
            D.detail["tackles_ok"] += 1
            self.rec("buildup", A, action="duel", actor_id=recv.p.get("id"), ok=False,
                     def_action="tackle", def_actor_id=d.p.get("id"), def_ok=True)
            self.turnover(D, z, self.lane, carrier=d, how="tackle")
            return False
        self.rec("buildup", A, action="duel", actor_id=recv.p.get("id"), ok=True,
                 def_action="beaten", def_actor_id=d.p.get("id"), def_ok=False)
        return True

    def offside_check(self, base, recv):
        A, D = self.att, self.other(self.att)
        line = OFFSIDE_LINE_MULT.get(D.press_style, 1.0)
        timing = 2.0 * _sig(K_DUEL * (D.line_q - recv.run) / 10.0)
        if self.rng.random() < base * line * timing:
            A.stats["offsides"] += 1
            _pstat(A.ps, recv.p)["offsides"] += 1
            self.rec("offside", A, zone=A3, player_id=recv.p.get("id"))
            self.dead_time("offside")
            D.detail["free_kicks"] += 1
            self.start_poss(D, D3, self.lane)
            return True
        return False

    def fail_pass(self, z_target, ln, d):
        """패스 실패의 결과 — 가로채기(기록) / 걷어내기(기록, 자기 진영) / 볼 회수(기록 없음) /
        루즈볼 / 아웃. 수비 기록은 수비수가 직접 끊거나 걷어낸 경우에만 남는다."""
        A, D = self.att, self.other(self.att)
        p_int, p_clr, p_rec, p_loose, _p_out = FAIL_SPLIT["high" if z_target >= A3 else "low"]
        r = self.rng.random()
        if r < p_int:
            D.detail["interceptions"] += 1
            _pstat(D.ps, d.p)["interceptions"] += 1
            self.rec("interception", D, zone=_MIRROR[z_target], lane=ln, player_id=d.p.get("id"))
            self.turnover(D, z_target, ln, carrier=d, how="interception")
        elif r < p_int + p_clr:
            D.detail["clearances"] += 1
            _pstat(D.ps, d.p)["clearances"] += 1
            self.rec("buildup", A, zone=z_target, lane=ln, def_action="clearance", def_actor_id=d.p.get("id"))
            self.zone, self.lane = z_target, ln
            self.loose_ball(M3, ln, 0.45)
        elif r < p_int + p_clr + p_rec:
            self.turnover(D, z_target, ln, carrier=d, how="recovery")
        elif r < p_int + p_clr + p_rec + p_loose:
            self.loose_ball(z_target, ln, 0.4)
        else:
            if z_target == BOX:
                self.goal_kick(D)
            else:
                self.throw_in(D, _MIRROR[z_target], ln)

    def act_short(self):
        A, D = self.att, self.other(self.att)
        z, c, r = self.zone, self.car, self.rng
        tz = z
        if z in BACK_PASS and r.random() < BACK_PASS[z]:
            tz = z - 1
        ln = self.lane
        if r.random() < 0.3:
            ln = r.choice(("L", "R")) if ln == "C" else "C"
        self.tick(r.uniform(*T_ACT["short"]))
        d = self.pick_def(D, z, self.lane)
        pr = self.pressure(D, z)
        gk_bonus = 0.8 if self.ctx == "gk" else 0.0
        p = _sig(L_SHORT + K_PASS * (c.pas - d.prs) / 10.0 - PRESS_PASS_K * (pr - 0.5)
                 + self.att_mod(A, D) + gk_bonus + ZONE_PASS_ADJ[z])
        ok = r.random() < p
        self.add_pass(A, c, ok)
        if ok:
            recv = self.pick_att(A, tz, ln, exclude=c)
            prev_ctx = self.ctx
            self.zone, self.lane, self.car = tz, ln, recv
            self.assist_cand = c
            # 컷백 = 측면에서 박스 안으로 꺾어 준 패스(중앙 레인 패스는 일반 박스 슈팅)
            self.ctx = "cutback" if (z == BOX and self.lane != "C" and r.random() < 0.6) else None
            if prev_ctx == "gk":
                self.ctx = None
            self.after_receive(recv, tz)
        else:
            self.fail_pass(z, self.lane, d)

    def act_fwd(self):
        A, D = self.att, self.other(self.att)
        z, c, r = self.zone, self.car, self.rng
        if z >= BOX:
            return self.act_short()
        tz = z + 1
        self.tick(r.uniform(*T_ACT["fwd"]))
        recv = self.pick_att(A, tz, self.lane, exclude=c)
        if tz == BOX and self.offside_check(OFF_FWD_BOX, recv):
            self.add_pass(A, c, False)
            return
        d = self.pick_def(D, tz, self.lane)
        pr = self.pressure(D, z)
        p = _sig(L_FWD + K_PASS * (c.prg - d.prs) / 10.0 - PRESS_PASS_K * 0.7 * (pr - 0.5)
                 + self.att_mod(A, D) - (BOX_PASS_PEN if tz == BOX else 0.0) + ZONE_PASS_ADJ[z])
        ok = r.random() < p
        self.add_pass(A, c, ok)
        if ok:
            if tz == A3:
                A.detail["final_third_entries"] += 1
            elif tz == BOX:
                A.detail["box_entries"] += 1
            self.zone, self.car, self.assist_cand, self.ctx = tz, recv, c, None
            self.after_receive(recv, tz)
        else:
            self.fail_pass(tz, self.lane, d)

    def act_long(self):
        A, D = self.att, self.other(self.att)
        z, c, r = self.zone, self.car, self.rng
        if z >= A3:
            return self.act_fwd()
        tz = A3 if (z == M3 or r.random() < 0.6) else M3
        ln = r.choice(LANES)
        self.tick(r.uniform(*T_ACT["long"]))
        self.ctx = None
        recv = self.pick_air(A, tz, ln, defending=False)
        if tz == A3 and r.random() < 0.5 and self.offside_check(OFF_LONG, recv):
            self.add_pass(A, c, False)
            return
        acc = r.random() < _sig(L_LONG_ACC + K_PASS * (c.prg - 60.0) / 10.0)
        if not acc:
            self.add_pass(A, c, False)
            if r.random() < 0.45:
                if tz == A3:
                    self.goal_kick(D)
                else:
                    self.throw_in(D, _MIRROR[tz], ln)
            else:
                self.start_poss(D, _MIRROR[tz], ln)
            return
        if r.random() >= LONG_AERIAL:
            # 땅볼·가슴 트래핑 — 공중 경합 없이 받는다(수비가 먼저 닿으면 가로채기)
            d = self.pick_def(D, tz, ln)
            ok = r.random() < _sig(_logit(0.62) + K_PASS * (recv.run - d.prs) / 10.0)
            self.add_pass(A, c, ok)
            if ok:
                if tz == A3:
                    A.detail["final_third_entries"] += 1
                self.zone, self.lane, self.car, self.assist_cand = tz, ln, recv, c
            else:
                self.fail_pass(tz, ln, d)
            return
        dd = self.pick_air(D, tz, ln, defending=True)
        won = self.aerial(A, recv, D, dd, tz, ln)
        if won is None:
            self.add_pass(A, c, False)
            return
        self.add_pass(A, c, won)
        if won:
            if tz == A3:
                A.detail["final_third_entries"] += 1
            self.zone, self.lane, self.car, self.assist_cand = tz, ln, recv, c
            if r.random() < 0.5:   # 머리로 떨궈 동료에게
                self.car = self.pick_att(A, tz, ln, exclude=recv)
        else:
            D.detail["clearances"] += 1
            _pstat(D.ps, dd.p)["clearances"] += 1
            self.rec("buildup", A, zone=tz, lane=ln, def_action="clearance", def_actor_id=dd.p.get("id"))
            self.zone, self.lane = tz, ln
            self.loose_ball(M3, ln, 0.45)

    def aerial(self, A, ea, D, ed, z, ln, extra_edge=0.0):
        """공중볼 경합. True=공격팀 승, False=수비팀 승, None=파울(재개됨)."""
        A.detail["aerial_duels"] += 1
        D.detail["aerial_duels"] += 1
        _pstat(A.ps, ea.p)["aerials"] += 1
        _pstat(D.ps, ed.p)["aerials"] += 1
        if self.rng.random() < FOUL_AERIAL:
            # 경합 중 파울 — 수비 반칙이면 공격 프리킥, 공격 반칙이면 수비 프리킥. 박스 안 공중볼은
            # 대개 공격수의 밀기 반칙으로 불린다(수비 반칙 = PK라 수비가 조심한다).
            if self.rng.random() < (0.15 if z == BOX else 0.55):
                self.rec("buildup", A, zone=z, lane=ln,
                         aerial={"def_id": ed.p.get("id"), "att_id": ea.p.get("id"), "won_by": A.name})
                A.detail["aerial_duels_ok"] += 1
                _pstat(A.ps, ea.p)["aerials_won"] += 1
                self.zone, self.lane = z, ln
                self.foul(D, ed, A, ea, z, ln)
            else:
                self.rec("buildup", A, zone=z, lane=ln,
                         aerial={"def_id": ed.p.get("id"), "att_id": ea.p.get("id"), "won_by": D.name})
                D.detail["aerial_duels_ok"] += 1
                _pstat(D.ps, ed.p)["aerials_won"] += 1
                self.zone, self.lane = _MIRROR[z], ln
                self.foul(A, ea, D, ed, _MIRROR[z], ln)
            return None
        p = _sig(K_DUEL * (ea.air - ed.air) / 10.0 - AERIAL_DEF_EDGE - extra_edge)
        won = self.rng.random() < p
        win_sd, win_e = (A, ea) if won else (D, ed)
        win_sd.detail["aerial_duels_ok"] += 1
        _pstat(win_sd.ps, win_e.p)["aerials_won"] += 1
        self.rec("buildup", A, zone=z, lane=ln,
                 aerial={"def_id": ed.p.get("id"), "att_id": ea.p.get("id"), "won_by": win_sd.name})
        return won

    def act_through(self):
        A, D = self.att, self.other(self.att)
        z, c, r = self.zone, self.car, self.rng
        if z < M3:
            return self.act_fwd()
        self.tick(r.uniform(*T_ACT["through"]))
        pop = [e for e in A.ents if e.role in ("ATT", "MID") and e is not c] or [e for e in A.ents if e is not c]
        recv = r.choices(pop, weights=[max(1.0, e.run) * (2.0 if e.role == "ATT" else 0.8)
                                       * (1.5 if e.lane == self.lane else 1.0) for e in pop], k=1)[0]
        if self.offside_check(OFF_THROUGH, recv):
            self.add_pass(A, c, False)
            return
        p = _sig(L_THROUGH + K_PASS * (c.prg - D.line_q) / 10.0 + self.att_mod(A, D) * 1.5)
        ok = r.random() < p
        self.add_pass(A, c, ok)
        if ok:
            A.detail["box_entries"] += 1
            self.zone, self.car, self.assist_cand, self.ctx = BOX, recv, c, "one_v_one"
            # 뒤따라온 수비의 마지막 태클(실패하면 파울 → 득점 기회 저지)
            if r.random() < 0.25:
                d = self.pick_def(D, BOX, self.lane)
                _pstat(D.ps, d.p)["tackles"] += 1
                D.detail["tackles"] += 1
                if r.random() < FOUL_TACKLE * self.clumsy(D, d):
                    _pstat(D.ps, d.p)["tackles"] -= 1
                    D.detail["tackles"] -= 1
                    self.rec("buildup", A, zone=BOX, action="duel", actor_id=recv.p.get("id"), ok=True,
                             def_action="foul", def_actor_id=d.p.get("id"))
                    self.foul(D, d, A, recv, BOX if r.random() < 0.6 else A3, self.lane,
                              tactical=True, dogso=True)
                    return
                if r.random() < _sig(L_RECV_TACKLE + K_DUEL * (d.tck - recv.drb) / 10.0 - 0.4):
                    _pstat(D.ps, d.p)["tackles_ok"] += 1
                    D.detail["tackles_ok"] += 1
                    self.rec("buildup", A, zone=BOX, action="duel", actor_id=recv.p.get("id"), ok=False,
                             def_action="tackle", def_actor_id=d.p.get("id"), def_ok=True)
                    self.turnover(D, BOX, self.lane, carrier=d, how="tackle")
                    return
                self.rec("buildup", A, zone=BOX, action="duel", actor_id=recv.p.get("id"), ok=True,
                         def_action="beaten", def_actor_id=d.p.get("id"), def_ok=False)
        else:
            u = r.random()
            if u < 0.25 and D.gk is not None:
                self.gk_ball(D)
            elif u < 0.80:
                d = self.pick_def(D, BOX, self.lane)
                D.detail["interceptions"] += 1
                _pstat(D.ps, d.p)["interceptions"] += 1
                self.rec("interception", D, zone=D3, player_id=d.p.get("id"))
                self.turnover(D, BOX, self.lane, carrier=d, how="interception")
            else:
                self.goal_kick(D)

    def act_drib(self):
        A, D = self.att, self.other(self.att)
        z, c, r = self.zone, self.car, self.rng
        self.tick(r.uniform(*T_ACT["drib"]))
        d = self.pick_def(D, z, self.lane)
        pr = self.pressure(D, z)
        p = _sig(L_DRIB + K_DUEL * (c.drb - d.tck) / 10.0 + self.att_mod(A, D) - 0.3 * (pr - 0.5))
        A.detail["dribbles"] += 1
        D.detail["tackles"] += 1
        _pstat(A.ps, c.p)["dribbles"] += 1
        _pstat(D.ps, d.p)["tackles"] += 1
        if r.random() < p:
            A.detail["dribbles_ok"] += 1
            _pstat(A.ps, c.p)["dribbles_ok"] += 1
            self.rec("buildup", A, action="dribble", actor_id=c.p.get("id"), ok=True,
                     def_action="beaten", def_actor_id=d.p.get("id"), def_ok=False)
            if z < BOX and r.random() < ADV_AFTER_DRIB.get(z, 0.4):
                self.zone = z + 1
                if self.zone == A3:
                    A.detail["final_third_entries"] += 1
                elif self.zone == BOX:
                    A.detail["box_entries"] += 1
            self.ctx = "box_dribble" if self.zone == BOX else None
            self.assist_cand = None
            return
        # 실패 — 무리한 태클이면 파울, 아니면 뺏김(일부는 터치 실수로 아웃)
        if r.random() < FOUL_TACKLE * self.clumsy(D, d) * (1.0 + 0.6 * (pr - 0.5)) * (BOX_FOUL_MULT if z == BOX else 1.0):
            # 파울은 태클 기록이 아니다(태클 시도 = 볼에 닿은 태클·뚫린 태클)
            D.detail["tackles"] -= 1
            _pstat(D.ps, d.p)["tackles"] -= 1
            self.rec("buildup", A, action="dribble", actor_id=c.p.get("id"), ok=True,
                     def_action="foul", def_actor_id=d.p.get("id"))
            A.detail["dribbles_ok"] += 1
            _pstat(A.ps, c.p)["dribbles_ok"] += 1
            self.foul(D, d, A, c, z, self.lane, tactical=self.counter > 0,
                      dogso=(self.counter > 0 and z >= A3))
            return
        if r.random() < 0.88:
            D.detail["tackles_ok"] += 1
            _pstat(D.ps, d.p)["tackles_ok"] += 1
            self.rec("buildup", A, action="dribble", actor_id=c.p.get("id"), ok=False,
                     def_action="tackle", def_actor_id=d.p.get("id"), def_ok=True)
            self.turnover(D, z, self.lane, carrier=d, how="tackle")
        else:
            # 수비와 닿기 전에 터치가 길었다 — 태클 시도 아님
            D.detail["tackles"] -= 1
            _pstat(D.ps, d.p)["tackles"] -= 1
            self.rec("buildup", A, action="dribble", actor_id=c.p.get("id"), ok=False)
            if z == BOX:
                self.goal_kick(D)
            else:
                self.throw_in(D, _MIRROR[z], self.lane)

    def act_cross(self, set_piece=False):
        A, D = self.att, self.other(self.att)
        c, r = self.car, self.rng
        ln = self.lane if self.lane != "C" else r.choice(("L", "R"))
        self.zone, self.lane = A3, ln
        if not set_piece:
            self.tick(r.uniform(*T_ACT["cross"]))
        d = self.pick_def(D, A3, ln)
        A.detail["crosses"] += 1
        _pstat(A.ps, c.p)["crosses"] += 1
        acc = r.random() < _sig(L_CROSS_ACC + K_PASS * (c.crs - d.blk) / 10.0 + self.att_mod(A, D))
        if acc and D.gk is not None and r.random() < GK_CLAIM_CROSS:
            acc = False
            self.add_pass(A, c, False)
            self.rec("buildup", A, action="cross", actor_id=c.p.get("id"), ok=False)
            self.gk_ball(D)
            return
        if acc:
            ra = self.pick_air(A, BOX, "C", defending=False)
            dd = self.pick_air(D, BOX, "C", defending=True)
            won = self.aerial(A, ra, D, dd, BOX, "C", extra_edge=CROSS_AIR_DEF_EDGE)
            if won is None:
                self.add_pass(A, c, False)
                return
            self.add_pass(A, c, won)
            if won:
                A.detail["crosses_ok"] += 1
                A.detail["box_entries"] += 1
                _pstat(A.ps, c.p)["crosses_ok"] += 1
                self.rec("buildup", A, action="cross", actor_id=c.p.get("id"), ok=True)
                self.zone, self.lane, self.car, self.assist_cand = BOX, "C", ra, c
                if r.random() < HEADER_SHOT_P:
                    self.shot(A, D, ra, "header")
                else:
                    self.car = self.pick_att(A, BOX, "C", exclude=ra)
                    self.assist_cand, self.ctx = ra, "cutback"
                return
            D.detail["clearances"] += 1
            _pstat(D.ps, dd.p)["clearances"] += 1
            self.rec("buildup", A, action="cross", actor_id=c.p.get("id"), ok=False,
                     def_action="clearance", def_actor_id=dd.p.get("id"), def_ok=True)
            if r.random() < 0.20:
                self.corner(A, ln)
            else:
                self.loose_ball(M3, ln, 0.45)
            return
        self.add_pass(A, c, False)
        u = r.random()
        if u < 0.70:
            D.detail["clearances"] += 1
            _pstat(D.ps, d.p)["clearances"] += 1
            self.rec("buildup", A, action="cross", actor_id=c.p.get("id"), ok=False,
                     def_action="clearance", def_actor_id=d.p.get("id"), def_ok=True)
            if r.random() < 0.25:
                self.corner(A, ln)
            else:
                self.loose_ball(M3, ln, 0.4)
        elif u < 0.85 and D.gk is not None:
            self.rec("buildup", A, action="cross", actor_id=c.p.get("id"), ok=False)
            self.gk_ball(D)
        else:
            self.rec("buildup", A, action="cross", actor_id=c.p.get("id"), ok=False)
            self.goal_kick(D)

    def corner(self, A, ln):
        D = self.other(A)
        r = self.rng
        A.stats["corners"] += 1
        A.detail["final_third_entries"] += 1
        self.rec("corner", A, zone=A3, lane=ln)
        self.dead_time("corner")
        self.att, self.zone, self.lane, self.counter = A, A3, ln, 0
        if r.random() < SHORT_CORNER:
            self.start_poss(A, A3, ln)
            return
        taker = max((e for e in A.ents if e.pos != "GK"), key=lambda e: (e.crs, e.fk), default=None)
        if D.gk is not None and r.random() < 0.15:
            self.gk_ball(D)
            return
        ra = self.pick_air(A, BOX, "C", defending=False)
        dd = self.pick_air(D, BOX, "C", defending=True)
        self.tick(r.uniform(2, 4))
        won = self.aerial(A, ra, D, dd, BOX, "C", extra_edge=CROSS_AIR_DEF_EDGE)
        if won is None:
            return
        if won:
            self.start_poss(A, BOX, "C", carrier=ra)
            self.assist_cand = taker
            self.shot(A, D, ra, "corner_header", kind="corner")
        else:
            D.detail["clearances"] += 1
            _pstat(D.ps, dd.p)["clearances"] += 1
            self.rec("buildup", A, zone=BOX, lane="C", def_action="clearance", def_actor_id=dd.p.get("id"))
            self.zone = BOX
            self.loose_ball(M3, ln, 0.4)

    def act_shot(self):
        A, D = self.att, self.other(self.att)
        z = self.zone
        if self.ctx in XG_BASE and self.ctx not in ("pk", "fk", "header", "corner_header"):
            ctx = self.ctx
        else:
            ctx = "box" if z == BOX else ("long" if z == A3 else "far")
        self.shot(A, D, self.car, ctx)

    def shot(self, A, D, shooter, ctx, kind="open"):
        r = self.rng
        self.tick(r.uniform(*T_ACT["shot"]))
        z = M3 if ctx == "far" else (A3 if ctx in ("long", "fk") else BOX)
        self.zone = z
        pr = self.pressure(D, z) if ctx not in ("pk", "fk") else 0.5
        stat = shooter.hed if ctx in ("header", "corner_header") else (shooter.fk if ctx in ("fk", "pk") else shooter.sht)
        gkq = D.gk.gkq if D.gk is not None else 30.0
        xg = _sig(_logit(XG_BASE[ctx] * XG_SCALE) + K_SHOT * (effective_stat(stat) - 60.0) / 10.0
                  - K_GK * (effective_stat(gkq) - 60.0) / 10.0 - 0.35 * (pr - 0.5)
                  + (0.0 if ctx in ("pk",) else 0.0))
        A.stats["shots"] += 1
        ps = _pstat(A.ps, shooter.p)
        ps["shots"] += 1
        A.detail["xg"] += xg
        big = xg >= 0.35
        if big:
            A.detail["big_chances"] += 1
        sid = shooter.p.get("id")
        gk_id = D.gk.p.get("id") if D.gk is not None else None
        kp = self.assist_cand if (self.assist_cand is not None and self.assist_cand is not shooter
                                 and kind not in ("pk", "fk")) else None
        kp_id = kp.p.get("id") if kp is not None else None
        bp = BLOCK_P[ctx] * (1.0 + 0.8 * pr)
        u = r.random()
        if u < bp:
            bl = self.pick_def(D, z, self.lane)
            D.detail["blocks"] += 1
            _pstat(D.ps, bl.p)["blocks"] += 1
            if big:
                A.detail["big_chances_missed"] += 1
                ps["big_chances_missed"] += 1
            if kp is not None:
                _pstat(A.ps, kp.p)["key_passes"] += 1
                A.detail["xa"] += xg
            self.rec("blocked", A, zone=A3, kind=kind, ctx=ctx, shooter_id=sid, blocker_id=bl.p.get("id"),
                     xg=round(xg, 4), big_chance=big, key_pass_id=kp_id)
            if r.random() < 0.45:
                self.corner(A, self.lane if self.lane != "C" else r.choice(("L", "R")))
            else:
                self.loose_ball(BOX if z == BOX else A3, self.lane, 0.35)
            return
        pg = min(0.97, xg / max(0.05, 1.0 - bp))
        on_p = max(pg + 0.02, ON_BASE[ctx])
        v = r.random()
        if v < pg:
            A.stats["shots_on"] += 1
            ps["shots_on"] += 1
            ps["goals"] += 1
            if D.gk is not None:
                _pstat(D.ps, D.gk.p)["goals_conceded"] += 1
            g = self.rec("goal", A, zone=A3, kind=kind, ctx=ctx, scorer_id=sid, gk_id=gk_id, xg=round(xg, 4))
            if kp is not None and kind not in ("pk", "fk"):
                g["assist_id"] = kp_id
                kps = _pstat(A.ps, kp.p)
                kps["assists"] += 1
                kps["key_passes"] += 1
                A.detail["xa"] += xg
            A.score += 1
            self.half_delays["goals"] += 1
            self.dead_time("goal")
            self.kickoff(D)
            return
        if big:
            A.detail["big_chances_missed"] += 1
            ps["big_chances_missed"] += 1
        if kp is not None:
            _pstat(A.ps, kp.p)["key_passes"] += 1
            A.detail["xa"] += xg
        if v < on_p:
            A.stats["shots_on"] += 1
            ps["shots_on"] += 1
            if D.gk is not None:
                _pstat(D.ps, D.gk.p)["saves"] += 1
            self.rec("save", A, zone=A3, kind=kind, ctx=ctx, shooter_id=sid, gk_id=gk_id, xg=round(xg, 4),
                     big_chance=big, blocker_id=None, key_pass_id=kp_id)
            w = r.random()
            if w < 0.30:
                self.corner(A, self.lane if self.lane != "C" else r.choice(("L", "R")))
            elif w < 0.45:
                # 리바운드 — 공격이 먼저 닿으면 바로 재슈팅
                self.tick(r.uniform(1, 2))
                if r.random() < 0.45:
                    reb = self.pick_att(A, BOX, "C")
                    self.start_poss(A, BOX, "C", carrier=reb)
                    self.shot(A, D, reb, "rebound")
                else:
                    self.start_poss(D, D3, "C")
            else:
                self.gk_ball(D)
            return
        if r.random() < 0.05:
            A.detail["woodwork"] += 1
        self.rec("shot_off", A, zone=A3, kind=kind, ctx=ctx, shooter_id=sid, xg=round(xg, 4))
        self.goal_kick(D)

    # ── 하프 진행 ─────────────────────────────────────────────────────
    def added_minutes(self, h):
        d = self.half_delays
        ev = d["goals"] * 0.5 + d["subs"] * 0.25 + d["cards"] * 0.25 + d["inj"] * 1.0
        u = self.rng.uniform(-0.5, 0.5)
        if h == 1:
            return max(0, min(5, int(round(0.6 + ev + u))))
        if h == 2:
            return max(1, min(6, int(round(1.3 + ev + u))))
        return max(0, min(3, int(round(0.3 + ev * 0.6 + u))))

    def play_half(self, h, kick_side):
        self.half, self.sec, self.added, self.end, self.whistle = h, 0.0, None, None, False
        self.is_extra = h >= 3
        self.reg = HALF_SEC if h <= 2 else ET_SEC
        self.half_delays = {"goals": 0, "subs": 0, "cards": 0, "inj": 0}
        self.last_min_int = int(self.cur_min()) - 1
        self.kickoff(kick_side)
        guard = 0
        while not self.whistle and guard < 20000:
            guard += 1
            self.step()
        self.half_len[h] = self.sec
        self.whistle = False      # 하프 사이 교체 기록은 다음 하프 시각으로
        self.pending_dead = 0.0
        self.pending_sub_minutes = []

    def run(self):
        H, A = self.H, self.A
        self.play_half(1, H)
        self.half, self.sec = 2, 0.0          # 하프타임 교체 — 46분으로 기록
        self.try_subs(46, True)
        self.play_half(2, A)
        hs90, as90 = H.score, A.score
        total = REG_MINUTES
        went_et = False
        if self.extra_time and (int(self.agg_home or 0) + H.score == int(self.agg_away or 0) + A.score):
            went_et = True
            total = REG_MINUTES + ET_HALF_MINUTES * 2
            for sd in (H, A):
                sd.windows += SUBS_EXTRA_TIME_WINDOWS
            self.half, self.sec = 3, 0.0
            self.try_subs(REG_MINUTES + 1, True)
            first = H if self.rng.random() < 0.5 else A
            self.play_half(3, first)
            self.half, self.sec = 4, 0.0
            self.try_subs(REG_MINUTES + ET_HALF_MINUTES + 1, True)
            self.play_half(4, self.other(first))
        return self.finish(hs90, as90, went_et, total)

    def finish(self, hs90, as90, went_et, total):
        H, A = self.H, self.A
        end_min = max(float(total), max((x["min"] for x in self.plog), default=0.0))
        for sd in (H, A):
            for ap in sd.appear.values():
                if ap["off"] is None:
                    ap["off"] = total
        tot_in = H.inplay + A.inplay
        hp = round(100.0 * H.inplay / tot_in) if tot_in else 50
        H.stats["poss"], A.stats["poss"] = hp, 100 - hp
        for sd in (H, A):
            d = sd.detail
            sd.stats["pass_acc"] = round(d["passes_ok"] / d["passes"], 3) if d["passes"] else 0.0
            sd.stats["saves"] = sum(v.get("saves", 0) for v in sd.ps.values())
        for sd in (H, A):
            conc = self.other(sd).score
            sv = sd.stats["saves"]
            sd.detail["save_pct"] = round(sv / (sv + conc), 3) if (sv + conc) else 0.0
            sd.detail["xg"] = round(sd.detail["xg"], 3)
            sd.detail["xa"] = round(sd.detail["xa"], 3)
        self.plog.sort(key=lambda x: x["min"])
        pass_log = []
        for (sk, m), bucket in sorted(self.pass_acc.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            pass_log.append([float(m), sk, list(bucket.values())])
        from match_sim import rating_ledger as _rl
        ap = {sd.name: {k: {"slot": a.get("slot"), "on": a.get("on"), "off": a.get("off"),
                            "sent_off": a.get("sent_off")} for k, a in sd.appear.items()}
              for sd in (H, A)}
        ledger, rating_events = _rl.compute(self.plog, pass_log, ap, total,
                                            {"home": H.score, "away": A.score})
        out_r = {}
        for sd in (H, A):
            lineup = [None] * len(sd.labels())
            for a in sd.appear.values():
                if a.get("started") and a["slot_idx"] < len(lineup):
                    lineup[a["slot_idx"]] = a["player"]
            gk0 = next((a["player"] for a in sd.appear.values() if a.get("started") and a["slot"] == "GK"), None)
            out_r[sd.name] = _build_player_ratings(lineup, sd.ps, sd.score, self.other(sd).score, gk0, self.rng,
                                                   sd.slots, appear=sd.appear, total_minutes=total,
                                                   ledger=ledger)
        for sd in (H, A):
            nm = {r_["id"]: r_["name"] for r_ in out_r[sd.name] if r_ and r_.get("id") is not None}
            rt = {r_["id"]: r_.get("rating") for r_ in out_r[sd.name] if r_ and r_.get("id") is not None}
            for s in sd.subs:
                s["out_name"] = nm.get(s["out_id"]) or s.get("out_name") or "?"
                s["in_name"] = nm.get(s["in_id"]) or s.get("in_name") or "?"
                s["out_rating"] = rt.get(s["out_id"])
                s["in_rating"] = rt.get(s["in_id"])
        return {
            "home_score": H.score, "away_score": A.score,
            "home_score_90": hs90, "away_score_90": as90,
            "went_extra_time": went_et,
            "home_subs": H.subs, "away_subs": A.subs,
            "total_minutes": total,
            "home_stats": H.stats, "away_stats": A.stats,
            "home_stats_detail": H.detail, "away_stats_detail": A.detail,
            "possession_log": self.plog,
            "pass_log": pass_log,
            "rating_events": rating_events,
            "home_player_ratings": out_r["home"],
            "away_player_ratings": out_r["away"],
            "time_info": {"ball_in_play_s": round(self.bip, 1), "dead_s": round(self.dead, 1),
                          "added_min": dict(self.added_log), "half_len_s": dict(self.half_len),
                          "end_min": round(end_min, 3)},
        }


def simulate_chain_match(home_lineup, away_lineup, home_boost=0.0, away_boost=0.0,
                         home_boost_position=None, away_boost_position=None,
                         home_adv=3.0, seed=None,
                         home_formation=None, away_formation=None,
                         home_bench=None, away_bench=None,
                         extra_time=False, agg_home=0, agg_away=0,
                         home_style=None, away_style=None, player_mods=None):
    """tactical_engine.simulate_tactical_match과 같은 인자·같은 반환 키(+ time_info).
    player_mods: {선수 id: {"yellow_add", "red_add"}} — 파울 1건당 카드 확률 가산(선수 성격)."""
    m = _ChainMatch(home_lineup, away_lineup, home_boost, away_boost, home_boost_position,
                    away_boost_position, home_adv, seed, home_formation, away_formation, home_bench,
                    away_bench, extra_time, agg_home, agg_away, home_style, away_style, player_mods)
    return m.run()