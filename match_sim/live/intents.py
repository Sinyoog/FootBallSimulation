# -*- coding: utf-8 -*-
"""match_sim/live/intents.py — 선수는 "위치"가 아니라 "행동"을 갖는다.

## 이게 왜 핵심인가

이전 버전(공간 가치 필드)은 매 틱 **독립적으로** "지금 서 있기 제일 좋은
칸"을 다시 골랐다. 계측 수치는 좋아졌는데 화면은 여전히 이상했다. 이유가
이거다 — **어떤 동작도 끝까지 가지 않는다.** 선수가 침투를 시작했다가
0.2초 뒤 필드가 조금 바뀌면 목적지를 갈아치우고, 또 바뀌면 또 갈아친다.
결과적으로 22개의 점이 국소 최적점 주변을 계속 미끄러진다.

사람 눈은 위치를 보는 게 아니라 **의도**를 읽는다. "쟤가 뒷공간으로
파고드는구나", "내려와서 받으려는구나", "저 레인을 끊으려는구나". 그
의도가 없으면, 개별 프레임이 아무리 그럴듯해도 축구로 안 보인다.

실제 선수는 2~4초짜리 **행동을 커밋**한다. 침투를 시작하면 그게 끝나거나
(공을 받거나, 오프사이드가 되거나, 국면이 바뀌거나) 무효가 될 때까지
유지한다. 중간에 "지금 여기보다 저기가 0.3점 높네" 하고 방향을 틀지 않는다.

그래서 여기서는:

  1. 선수마다 현재 행동(Intent) 하나를 들고 있다
  2. 매 틱 하는 일은 **그 행동을 계속 수행**하는 것뿐이다
  3. 행동이 만료되거나 무효가 됐을 때만 새로 고른다

공간 가치 필드는 폐기하지 않고 **`HOLD_SHAPE`라는 하나의 행동**으로
강등된다 — "지금 특별히 할 게 없을 때 서 있을 자리".

## 행동 목록

  공격 국면(우리 팀이 볼 소유)
    RUN_BEHIND    상대 최종 수비 뒤로 침투
    CHECK_TO_BALL 볼 쪽으로 내려와 받기(발밑)
    HOLD_WIDTH    터치라인까지 벌려 폭 유지
    SUPPORT       캐리어 근처에서 패스 각도 만들기
    OVERLAP       측면에서 캐리어를 추월해 돌아나가기
    OCCUPY_BOX    박스 안 침투(크로스 대비)
    RECEIVE_AT    **패스가 나에게 왔다** — 그 도달점으로 달린다
    HOLD_SHAPE    대형 유지(폴백)

  수비 국면
    PRESS_BALL    볼 보유자 압박
    MARK          지정 상대를 골사이드로 마크
    COVER_LANE    캐리어와 위험한 상대 사이 패스 레인 차단
    HOLD_LINE     백라인 높이 유지
    RECOVER       자기 골 쪽으로 복귀
    HOLD_SHAPE    폴백

  중립(루즈볼)
    CHASE_BALL    루즈볼 경합

`RECEIVE_AT`이 특별하다 — 이건 선수가 스스로 고르는 게 아니라 **패스를
차는 순간 패서가 리시버에게 심어주는** 행동이다. 이렇게 해야 "패스"와
"런"이 같은 하나의 결정이 된다(live_engine.decide_on_ball 참조). 예전
구조에서는 오프더볼 런과 패스 선택이 서로를 모르는 두 시스템이라, 어떤
런도 아무것도 만들어내지 못했다.
"""

import math
import random

PITCH_LEN_M = 105.0
PITCH_WID_M = 68.0

# ── 행동 종류 ──
RUN_BEHIND = "run_behind"
CHECK_TO_BALL = "check_to_ball"
HOLD_WIDTH = "hold_width"
SUPPORT = "support"
OVERLAP = "overlap"
OCCUPY_BOX = "occupy_box"
RECEIVE_AT = "receive_at"
PRESS_BALL = "press_ball"
MARK = "mark"
COVER_LANE = "cover_lane"
HOLD_LINE = "hold_line"
RECOVER = "recover"
CHASE_BALL = "chase_ball"
HOLD_SHAPE = "hold_shape"

# 행동별 (최소 지속시간, 최대 지속시간) — 초.
# 짧으면 다시 "매 틱 재선택"에 가까워지고, 너무 길면 상황 변화에 둔해진다.
DURATION = {
    RUN_BEHIND:    (1.8, 4.0),
    CHECK_TO_BALL: (1.0, 2.4),
    HOLD_WIDTH:    (2.0, 4.5),
    SUPPORT:       (1.2, 2.8),
    OVERLAP:       (2.2, 4.5),
    OCCUPY_BOX:    (1.6, 3.5),
    RECEIVE_AT:    (0.4, 3.0),
    PRESS_BALL:    (0.8, 2.5),
    MARK:          (1.5, 4.0),
    COVER_LANE:    (1.2, 3.0),
    HOLD_LINE:     (1.5, 3.5),
    RECOVER:       (1.5, 4.0),
    CHASE_BALL:    (0.5, 2.0),
    HOLD_SHAPE:    (1.2, 3.0),
}

# 전력질주로 수행하는 행동(그 외는 조깅~러닝)
SPRINT_KINDS = {RUN_BEHIND, RECEIVE_AT, CHASE_BALL, RECOVER, PRESS_BALL, OVERLAP}

_ATTACK_ROLES = {"ST", "CF", "LW", "RW", "CAM"}
_WIDE_ROLES = {"LW", "RW", "LM", "RM", "LB", "RB", "LWB", "RWB"}
_BACKLINE = {"CB", "LB", "RB", "LWB", "RWB"}


class Intent:
    """선수 한 명이 지금 하고 있는 행동."""

    __slots__ = ("kind", "tmx", "tmy", "expires", "target_ref", "meta", "started")

    def __init__(self, kind, tmx, tmy, expires, target_ref=None, meta=None, started=0.0):
        self.kind = kind
        self.tmx = tmx          # 목표 지점(미터)
        self.tmy = tmy
        self.expires = expires  # 이 시각(초)이 지나면 재선택
        self.target_ref = target_ref   # 추적 대상 (side, idx) — MARK 등
        self.meta = meta or {}
        self.started = started

    def __repr__(self):
        return f"<{self.kind} ({self.tmx:.0f},{self.tmy:.0f}) exp={self.expires:.1f}>"


def new_intent(kind, tmx, tmy, now, rng=random, target_ref=None, meta=None):
    lo, hi = DURATION.get(kind, (1.2, 3.0))
    return Intent(kind, tmx, tmy, now + rng.uniform(lo, hi),
                  target_ref=target_ref, meta=meta, started=now)


# ══════════════════════════════════════════════════════════════════
#  유효성 — 언제 행동을 버리는가
# ══════════════════════════════════════════════════════════════════
def still_valid(intent, pl, ctx, now):
    """행동이 아직 유효한가.

    시간 만료 외에도 **국면이 바뀌면 즉시 무효**다. 우리가 공을 잃었는데
    계속 뒷공간으로 침투하고 있으면 안 된다. 반대로 시간이 남았는데 상황이
    그대로면 절대 갈아치우지 않는다 — 그게 이 시스템의 요점이다.
    """
    if intent is None:
        return False
    if now >= intent.expires:
        return False

    phase = ctx["phase"]        # "attack" / "defend" / "loose"
    k = intent.kind

    if phase == "attack" and k in (PRESS_BALL, MARK, COVER_LANE, HOLD_LINE, RECOVER):
        return False
    if phase == "defend" and k in (RUN_BEHIND, CHECK_TO_BALL, HOLD_WIDTH,
                                   SUPPORT, OVERLAP, OCCUPY_BOX, RECEIVE_AT):
        return False
    if phase == "loose" and k in (SUPPORT, OVERLAP, OCCUPY_BOX):
        return False

    # 패스가 나한테 온 게 아니게 됐으면(다른 사람이 먼저 닿음) 무효
    if k == RECEIVE_AT:
        if ctx["ball"].carrier is not None:
            return False
        if ctx["ball"].out_of_play:
            return False

    # 압박 대상이 바뀌었으면 무효
    if k == PRESS_BALL and ctx["carrier"] != intent.target_ref:
        return False

    # 목표에 이미 도달했고 추적 대상이 없으면(정적 목표) 조기 종료
    if intent.target_ref is None and k not in (HOLD_SHAPE, HOLD_WIDTH, HOLD_LINE):
        d = math.hypot(pl["x"] * PITCH_LEN_M - intent.tmx,
                       pl["y"] * PITCH_WID_M - intent.tmy)
        if d < 1.8:
            return False
    return True


def live_target(intent, ctx):
    """행동의 현재 목표 지점(미터). 추적 대상이 있으면 매 틱 갱신된다."""
    k = intent.kind
    if k == PRESS_BALL:
        b = ctx["ball"]
        return b.mx, b.my
    if k == CHASE_BALL:
        b = ctx["ball"]
        # 공의 예상 지점으로 간다(현재 위치가 아니라) — 이게 있어야
        # "공을 쫓아가는" 게 아니라 "공을 가로채는" 그림이 나온다.
        lead = min(1.2, 0.35 + b.speed() * 0.10)
        return b.mx + b.vx * lead, b.my + b.vy * lead
    if k == MARK and intent.target_ref is not None:
        side, i = intent.target_ref
        opp = ctx["teams"][side][i]
        ogx = ctx["own_goal_x"] * PITCH_LEN_M
        omx, omy = opp["x"] * PITCH_LEN_M, opp["y"] * PITCH_WID_M
        dx = ogx - omx
        d = abs(dx) + 1e-6
        # 골사이드로 4.5m 치우쳐 선다.
        # [수정] 1.8m로 붙여놨더니 모든 공격수가 항상 압박 반경(3.5m) 안에
        # 있게 되어 캐리어가 경기 내내 "강한 압박" 판정을 받았다. 그러면
        # 전진 옵션의 안전도가 전부 낮아져 팀이 계속 뒤로만 돌린다.
        return omx + dx / d * 4.5, omy
    return intent.tmx, intent.tmy


# ══════════════════════════════════════════════════════════════════
#  행동 선택
# ══════════════════════════════════════════════════════════════════
def _opp_backline_mx(ctx):
    """상대 최종 수비 라인의 x(미터, 우리 공격 방향 기준)."""
    opp = ctx["teams"][ctx["opp_side"]]
    xs = sorted(p["x"] for p in opp if p["pos"] != "GK")
    if not xs:
        return 0.5 * PITCH_LEN_M
    # 공격 방향이 +x면 상대 최종수비는 가장 작은 x쪽... 이 아니라
    # 우리 골 반대쪽에서 볼 때 "가장 뒤"에 있는 두 명의 평균을 쓴다.
    if ctx["atk_goal_x"] > 0.5:
        deep = sorted(xs)[:2]        # 우리 골에 가까운 쪽 = 그들의 수비 라인
    else:
        deep = sorted(xs)[-2:]
    return (sum(deep) / len(deep)) * PITCH_LEN_M


def choose(pl, idx, side, ctx, now, rng=random):
    """이 선수의 다음 행동을 고른다.

    선택은 (1) 국면, (2) 역할 성향, (3) 팀 내 쿼터(같은 행동을 몇 명이나
    하고 있는가), (4) 상황(볼 위치/거리)으로 정해진다. 한 번 고르면 위
    `still_valid`가 거짓이 될 때까지 유지된다.
    """
    phase = ctx["phase"]
    pos = pl["pos"]
    pmx, pmy = pl["x"] * PITCH_LEN_M, pl["y"] * PITCH_WID_M
    b = ctx["ball"]
    atk_fwd = 1.0 if ctx["atk_goal_x"] > 0.5 else -1.0
    counts = ctx["intent_counts"]

    if pos == "GK":
        return new_intent(HOLD_SHAPE, *ctx["shape_target"](idx), now, rng)

    # ── 루즈볼: 가까우면 무조건 쫓는다 ──
    if phase == "loose":
        d = math.hypot(pmx - b.mx, pmy - b.my)
        if d < 22.0 and counts.get(CHASE_BALL, 0) < 3:
            return new_intent(CHASE_BALL, b.mx, b.my, now, rng)
        return new_intent(HOLD_SHAPE, *ctx["shape_target"](idx), now, rng)

    # ══════════════════ 공격 ══════════════════
    if phase == "attack":
        carrier = ctx["carrier"]
        is_carrier = (carrier == (side, idx))
        if is_carrier:
            # 캐리어의 행동은 live_engine이 온볼 결정으로 따로 정한다
            return new_intent(HOLD_SHAPE, *ctx["shape_target"](idx), now, rng)

        cmx, cmy = ctx["carrier_pos_m"]
        dist_to_ball = math.hypot(pmx - cmx, pmy - cmy)
        back_mx = _opp_backline_mx(ctx)
        # 내가 상대 수비 라인보다 얼마나 뒤에 있나(양수면 아직 뒤)
        behind_gap = (back_mx - pmx) * atk_fwd

        opts = []

        # 침투 — 공격 자원이고, 라인 근처에 있고, 이미 침투 중인 동료가
        # 많지 않을 때. 실축에서도 동시에 뛰어드는 건 보통 1~3명이다.
        if pos in _ATTACK_ROLES and counts.get(RUN_BEHIND, 0) < 3:
            w = 1.0 if pos in ("ST", "CF") else 0.6
            if -6.0 < behind_gap < 16.0:
                w *= 1.8
            if dist_to_ball < 40.0:
                w *= 1.3
            opts.append((RUN_BEHIND, w))

        # 내려와서 받기 — 캐리어가 압박받고 있거나 내가 중원일 때
        if dist_to_ball < 32.0 and counts.get(CHECK_TO_BALL, 0) < 2:
            w = 0.9
            if ctx["carrier_pressure"] > 0.5:
                w *= 2.2
            if pos in ("CM", "CAM", "CDM", "CF"):
                w *= 1.4
            opts.append((CHECK_TO_BALL, w))

        # 폭 유지 — 측면 자원
        if pos in _WIDE_ROLES:
            w = 1.4
            # 볼이 반대쪽 플랭크면 폭 유지 가치가 더 크다(스위치 대비)
            if (pmy - PITCH_WID_M / 2) * (cmy - PITCH_WID_M / 2) < 0:
                w *= 1.5
            opts.append((HOLD_WIDTH, w))

        # 오버랩 — 풀백/윙백이 같은 쪽 캐리어를 추월
        if pos in ("LB", "RB", "LWB", "RWB") and counts.get(OVERLAP, 0) < 1:
            same_flank = (pmy - PITCH_WID_M / 2) * (cmy - PITCH_WID_M / 2) > 0
            if same_flank and dist_to_ball < 30.0:
                opts.append((OVERLAP, 1.6))

        # 박스 침투 — 볼이 파이널서드 측면에 있을 때
        ball_final_third = ((b.mx - PITCH_LEN_M / 2) * atk_fwd) > 12.0
        if ball_final_third and pos in _ATTACK_ROLES | {"CM"}:
            if counts.get(OCCUPY_BOX, 0) < 3:
                opts.append((OCCUPY_BOX, 1.7 if pos in ("ST", "CF") else 0.9))

        # 지원 각도
        if dist_to_ball < 28.0 and counts.get(SUPPORT, 0) < 3:
            opts.append((SUPPORT, 1.2))

        # 대형 유지 — 항상 후보(특히 수비진)
        w_shape = 1.0
        if pos in _BACKLINE:
            w_shape = 4.0
        elif pos == "CDM":
            w_shape = 2.2
        opts.append((HOLD_SHAPE, w_shape))

        kind = _weighted_pick(opts, rng)
        return _make_attack_intent(kind, pl, idx, side, ctx, now, rng,
                                   back_mx, atk_fwd)

    # ══════════════════ 수비 ══════════════════
    carrier = ctx["carrier"]
    cmx, cmy = ctx["carrier_pos_m"]
    dist_to_ball = math.hypot(pmx - cmx, pmy - cmy)
    own_goal_mx = ctx["own_goal_x"] * PITCH_LEN_M

    # 압박 — 볼에 가장 가까운 한 명(팀당 1명, 가끔 2명)
    if ctx["presser"] == (side, idx):
        return new_intent(PRESS_BALL, cmx, cmy, now, rng, target_ref=carrier)

    # 복귀 — 내가 볼보다 앞에 나가 있으면(역습 당하는 중) 무조건 돌아온다
    ahead_of_ball = (pmx - cmx) * (-1 if own_goal_mx < PITCH_LEN_M / 2 else 1) < 0
    if ahead_of_ball and dist_to_ball > 18.0:
        tx = own_goal_mx + (cmx - own_goal_mx) * 0.45
        return new_intent(RECOVER, tx, PITCH_WID_M / 2 + (pmy - PITCH_WID_M / 2) * 0.5,
                          now, rng)

    opts = []
    mark_ref = ctx["mark_assign"].get((side, idx))
    if mark_ref is not None:
        w = 1.6
        if pos in _BACKLINE:
            w *= 1.5
        opts.append((MARK, w))
    if dist_to_ball < 30.0:
        opts.append((COVER_LANE, 1.3))
    if pos in _BACKLINE:
        opts.append((HOLD_LINE, 2.0))
    opts.append((HOLD_SHAPE, 1.4))

    kind = _weighted_pick(opts, rng)
    if kind == MARK:
        return new_intent(MARK, pmx, pmy, now, rng, target_ref=mark_ref)
    if kind == COVER_LANE:
        # 캐리어와 "가장 위험한 상대" 사이 레인 위에 선다
        tgt = ctx["most_dangerous_opp"]
        if tgt is None:
            return new_intent(HOLD_SHAPE, *ctx["shape_target"](idx), now, rng)
        omx, omy = tgt
        return new_intent(COVER_LANE, (cmx + omx) / 2, (cmy + omy) / 2, now, rng)
    if kind == HOLD_LINE:
        lx = ctx["backline_target_mx"]
        return new_intent(HOLD_LINE, lx, pmy + (cmy - pmy) * 0.25, now, rng)
    return new_intent(HOLD_SHAPE, *ctx["shape_target"](idx), now, rng)


def _weighted_pick(opts, rng):
    tot = sum(w for _, w in opts)
    if tot <= 0:
        return HOLD_SHAPE
    r = rng.random() * tot
    acc = 0.0
    for k, w in opts:
        acc += w
        if r <= acc:
            return k
    return opts[-1][0]


def _in_pitch(mx, my, margin_x=5.0, margin_y=2.5):
    """[중요] 침투/오버랩 목표가 피치 밖으로 나가면, 그쪽으로 찔러준 패스가
    그대로 골라인을 넘어간다(실측: 경기당 패스가 골라인 밖으로 139회 →
    골킥 180회). 뒷공간은 '골라인 너머'가 아니라 '수비 라인과 골라인 사이'다.
    """
    return (min(PITCH_LEN_M - margin_x, max(margin_x, mx)),
            min(PITCH_WID_M - margin_y, max(margin_y, my)))


def _make_attack_intent(kind, pl, idx, side, ctx, now, rng, back_mx, atk_fwd):
    pmx, pmy = pl["x"] * PITCH_LEN_M, pl["y"] * PITCH_WID_M
    cmx, cmy = ctx["carrier_pos_m"]
    goal_mx = ctx["atk_goal_x"] * PITCH_LEN_M
    half_w = PITCH_WID_M / 2

    if kind == RUN_BEHIND:
        # 상대 라인 뒤 8~18m. 자기 채널을 유지하되 안쪽으로 살짝 대각선.
        depth = rng.uniform(8.0, 18.0)
        tx = back_mx + depth * atk_fwd
        ty = pmy + (half_w - pmy) * rng.uniform(0.10, 0.40)
        # 골라인을 넘지 않게. 뒷공간 침투의 최대 깊이는 골라인 6m 앞이다.
        goal_mx = ctx["atk_goal_x"] * PITCH_LEN_M
        if atk_fwd > 0:
            tx = min(tx, goal_mx - 6.0)
        else:
            tx = max(tx, goal_mx + 6.0)
        return new_intent(RUN_BEHIND, *_in_pitch(tx, ty), now, rng)

    if kind == CHECK_TO_BALL:
        # 캐리어 쪽으로 6~12m 다가가되, 캐리어 정면이 아니라 옆으로 비껴서
        d = math.hypot(cmx - pmx, cmy - pmy) + 1e-6
        step = min(d - 6.0, rng.uniform(6.0, 12.0))
        ux, uy = (cmx - pmx) / d, (cmy - pmy) / d
        # 수직 방향으로 살짝 어긋나게(패스 각도 확보)
        px, py = -uy, ux
        off = rng.choice((-1.0, 1.0)) * rng.uniform(3.0, 7.0)
        return new_intent(CHECK_TO_BALL,
                          *_in_pitch(pmx + ux * step + px * off,
                                     pmy + uy * step + py * off), now, rng)

    if kind == HOLD_WIDTH:
        ty = 3.5 if pmy < half_w else PITCH_WID_M - 3.5
        tx = pmx + (back_mx - pmx) * rng.uniform(0.25, 0.6)
        return new_intent(HOLD_WIDTH, *_in_pitch(tx, ty), now, rng)

    if kind == OVERLAP:
        ty = 4.0 if pmy < half_w else PITCH_WID_M - 4.0
        tx = cmx + rng.uniform(8.0, 20.0) * atk_fwd
        goal_mx = ctx["atk_goal_x"] * PITCH_LEN_M
        tx = min(tx, goal_mx - 6.0) if atk_fwd > 0 else max(tx, goal_mx + 6.0)
        return new_intent(OVERLAP, *_in_pitch(tx, ty), now, rng)

    if kind == OCCUPY_BOX:
        # 박스 안 6~16m 지점, 파포스트/니어포스트/페널티스팟 중 하나
        spot = rng.choice((-7.0, 0.0, 7.0))
        tx = goal_mx - 11.0 * atk_fwd
        return new_intent(OCCUPY_BOX, *_in_pitch(tx, half_w + spot), now, rng)

    if kind == SUPPORT:
        ang = rng.uniform(0, 2 * math.pi)
        r = rng.uniform(11.0, 18.0)
        tx = cmx + math.cos(ang) * r
        ty = cmy + math.sin(ang) * r
        # 캐리어보다 뒤로 너무 처지지 않게
        if (tx - cmx) * atk_fwd < -8.0:
            tx = cmx - 8.0 * atk_fwd
        return new_intent(SUPPORT, *_in_pitch(tx, ty), now, rng)

    return new_intent(HOLD_SHAPE, *ctx["shape_target"](idx), now, rng)