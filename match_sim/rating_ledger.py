"""경기 평점 장부 — [2026-10 신설, 신민용 확정 설계 B-2]

경기 엔진(tactical_engine)이 남긴 사건 기록(possession_log의 선수 단위 사건 + pass_log)만 읽어서
선수별 평점과 "사건마다 평점이 어떻게 변했는지"(타임라인)를 만든다.

원칙
  · 기록(엔진)과 평점(이 모듈)을 분리한다 — 기록이 정확해도 평점이 기여도를 제대로 반영한다는
    보장은 없으므로 둘을 따로 검증한다(신민용 확정). 이 모듈은 난수를 쓰지 않는다.
  · 모든 선수는 6.0에서 시작하고, 경기 중 사건마다 가감한다. 같은 사건도 포지션 역할에 따라
    무게가 다르다(수비수의 가로채기 ≠ 공격수의 가로채기). OVR은 쓰지 않는다 — 현실 평점처럼
    "그 경기에서 한 일"만 본다.
  · 경기 종료 때 팀 결과(승/무/패)와 무실점을 출전 시간 비중만큼 더한다.
  · 최종값은 4.0~10.0, 소수 첫째 자리.
  · [2026-11 평점 눈금 재조정, 신민용 확정 기준표] 10.0 역대급 / 9.5 경이로움 / 9.0 월드클래스 /
    8.5 최우수 / 8.0 탁월함 / 7.5 훌륭함 / 7.0 좋음 / 6.5 평균 이상 / 6.0 보통 / 5.5 아쉬움 /
    5.0 부진 / 4.5 매우 부진 / 4.0 최악에 가까움. "5.0 미만은 정말 못해야 뜬다" — 그래서
      ① 실점 책임: 실점으로 이어진 공격 흐름에서 뚫린(드리블 허용·공중볼 패배) 수비 선수를
        따로 깎는다(전원 공통 "실점"과 별개) — 같은 대패에서도 책임 있는 선수만 4점대로 간다.
      ② 점수 차: 패배 폭이 클수록 패배 보정이 커진다(1골 차 패배는 예전과 같음).
      ③ 눈금: 6.0 아래는 1.15배로 늘리고(부진이 5점대 초반까지 실제로 내려가게),
        4.6 아래는 절반으로 눌러 4.0에서 멈춘다(퇴장 안 당한 선수는 3점대 없음).
      ④ 퇴장: 상황에 따라 4점대~2점대 — 일찍 퇴장할수록 더 깎고(남은 시간 비중), 퇴장 뒤 팀이
        먹은 실점도 그 선수 몫으로 깎는다(수적 열세의 책임). 퇴장 선수만 바닥이 2.0이고,
        4.0 아래는 0.7배로 눌러서 2점대는 "일찍 퇴장 + PK 허용 + 대패" 같은 최악에서만 나온다.
    6.0 이상 쪽(좋은 경기·골·해트트릭)은 손대지 않았다.

반환: compute(...) → (ratings, events)
  ratings: {선수키: 최종 평점}
  events : [[분, 선수키, 변화량, 사유코드], ...] — 경기 상세 리플레이가 시간순으로 재생한다.
           패스는 사건 수가 너무 많아 15분 단위로 묶어 한 줄("pass")로 넣는다.
"""

START = 6.0
MIN_R, MAX_R = 4.0, 10.0

# 포지션 묶음: GK / DEF / MID / ATT
_GROUP = {
    "GK": "GK",
    "CB": "DEF", "LB": "DEF", "RB": "DEF", "LWB": "DEF", "RWB": "DEF", "SW": "DEF",
    "CDM": "MID", "CM": "MID", "LM": "MID", "RM": "MID", "CAM": "MID",
    "LW": "ATT", "RW": "ATT", "ST": "ATT", "CF": "ATT", "SS": "ATT",
}


def group_of(pos):
    return _GROUP.get(pos or "", "MID")


# 사건별 변화량 — (GK, DEF, MID, ATT). 값의 근거: 공격 포인트는 수비수가 했을수록 드물어 더 크게,
# 수비 동작은 수비수의 본업이라 기본값, 공격수가 하면 조금 작게(그래도 기여는 기여).
W = {
    "goal":            (1.20, 1.20, 1.05, 0.95),
    "goal_pk":         (0.70, 0.70, 0.65, 0.60),
    "assist":          (0.70, 0.65, 0.60, 0.60),
    "key_pass":        (0.15, 0.15, 0.15, 0.15),
    "shot_on":         (0.08, 0.08, 0.08, 0.08),
    "shot_off":        (-0.03, -0.03, -0.03, -0.03),
    "big_miss":        (-0.30, -0.30, -0.30, -0.30),
    "dribble_ok":      (0.07, 0.07, 0.07, 0.07),
    "dribble_fail":    (-0.03, -0.03, -0.03, -0.03),
    "cross_ok":        (0.06, 0.06, 0.06, 0.06),
    "cross_fail":      (-0.02, -0.02, -0.02, -0.02),
    "offside":         (-0.03, -0.03, -0.03, -0.03),
    "fouled":          (0.03, 0.03, 0.03, 0.03),
    "pk_won":          (0.30, 0.30, 0.30, 0.30),
    "tackle_ok":       (0.10, 0.12, 0.09, 0.08),
    "tackle_fail":     (-0.04, -0.04, -0.03, -0.03),
    "dribbled_past":   (-0.05, -0.05, -0.04, -0.03),   # 붙었다가 드리블에 뚫림
    "dispossessed":    (-0.03, -0.03, -0.03, -0.03),   # 볼을 가진 채 태클에 뺏김
    "interception":    (0.10, 0.12, 0.09, 0.08),
    "clearance":       (0.05, 0.07, 0.04, 0.04),
    "block":           (0.12, 0.12, 0.10, 0.10),
    "aerial_won":      (0.04, 0.04, 0.04, 0.04),
    "aerial_lost":     (-0.02, -0.02, -0.02, -0.02),
    "foul":            (-0.05, -0.05, -0.05, -0.05),
    "pk_conceded":     (-0.40, -0.40, -0.40, -0.40),
    "yellow":          (-0.30, -0.30, -0.30, -0.30),
    "red":             (-0.60, -0.60, -0.60, -0.60),   # 기본 몫 — 일찍 퇴장한 만큼 RED_EARLY가 더 붙는다
    "after_red":       (-0.18, -0.18, -0.18, -0.18),   # [2026-11] 퇴장 뒤 팀 실점 1개당(퇴장 선수 본인)
    "save":            (0.15, 0.0, 0.0, 0.0),
    "save_big":        (0.30, 0.0, 0.0, 0.0),
    "conceded":        (-0.25, -0.10, -0.05, -0.02),   # 실점 순간 경기장에 있던 선수 전원
    "team_goal":       (0.03, 0.04, 0.05, 0.05),       # 득점 순간 경기장에 있던 선수 전원(득점자 제외)
    # [2026-11] 실점 책임 — 실점으로 이어진 상대 공격 흐름에서 드리블에 뚫렸거나 공중볼을 진 선수(골 1개당 1번).
    # 수비수가 가장 무겁다(본업). PK 실점은 pk_conceded가 따로 있어 여기선 세지 않는다.
    "goal_blame":      (0.0, -0.30, -0.22, -0.15),
}
# 패스(15분 묶음): 성공 1회당, 실패 1회당 — 엔진 패스량이 현실 수준(경기당 팀 400~500회)이 된 뒤
# 원래 값으로 되돌렸다(패스 많이 한 선수가 과하게 유리해지지 않게).
PASS_OK = (0.004, 0.005, 0.006, 0.005)
PASS_FAIL = (-0.012, -0.015, -0.015, -0.012)
# 경기 종료(출전 시간 비중을 곱한다)
RESULT = {"win": 0.30, "draw": 0.0, "loss": -0.30}
# [2026-11] 패배 폭 — 2골 차부터 1골 더 질 때마다 이만큼 더 깎는다(최대 LOSS_MARGIN_CAP골 차까지).
# (GK, DEF, MID, ATT) — GK는 실점 1개마다 이미 크게 깎이므로(conceded −0.25) 0.
LOSS_MARGIN_STEP = (0.0, 0.10, 0.10, 0.08)
LOSS_MARGIN_CAP = 4
# [2026-11] 실점 책임을 찾을 때 골 직전 몇 분까지 거슬러 올라가는지(상대 공격 흐름이 끊기면 거기서 멈춘다).
BLAME_WINDOW_MIN = 0.8
# [2026-11] 눈금: PIVOT 아래 편차를 LOW_STRETCH배, FLOOR_KNEE 아래는 FLOOR_SOFT배로 눌러 MIN_R에서 멈춘다.
PIVOT = 6.0
LOW_STRETCH = 1.15
FLOOR_KNEE = 4.6
FLOOR_SOFT = 0.5
# [2026-11] 퇴장 — 남은 시간 비중 × RED_EARLY를 기본 몫에 더한다(10분 퇴장 ≈ −1.6, 85분 ≈ −0.7).
RED_EARLY = -1.10
RED_MIN_R = 2.0          # 퇴장 선수만 이 바닥
RED_FLOOR_KNEE = 4.0     # 퇴장 선수는 여기 아래를 RED_FLOOR_SOFT배로 누른다
RED_FLOOR_SOFT = 0.70
CLEAN_SHEET = (0.50, 0.30, 0.10, 0.0)
CLEAN_SHEET_MIN_SHARE = 0.65   # 이만큼 이상 뛴 선수만 무실점 가산
_GI = {"GK": 0, "DEF": 1, "MID": 2, "ATT": 3}


def scale(raw, sent_off=False):
    """장부 합계(raw) → 화면 평점. 6.0 이상은 그대로, 아래는 늘리고 바닥 근처는 누른다.
    퇴장 안 당한 선수는 4.0에서 멈추고, 퇴장 선수는 2.0까지 내려갈 수 있다."""
    v = float(raw)
    if v < PIVOT:
        v = PIVOT - (PIVOT - v) * LOW_STRETCH
    if sent_off:
        if v < RED_FLOOR_KNEE:
            v = RED_FLOOR_KNEE - (RED_FLOOR_KNEE - v) * RED_FLOOR_SOFT
        return round(max(RED_MIN_R, min(MAX_R, v)), 1)
    if v < FLOOR_KNEE:
        v = FLOOR_KNEE - (FLOOR_KNEE - v) * FLOOR_SOFT
    return round(max(MIN_R, min(MAX_R, v)), 1)


def compute(plog, pass_log, appear, total_minutes, score):
    """plog: tactical_engine possession_log(선수 사건 포함). pass_log: [[분, 팀, [[id, n, ok], ...]], ...].
    appear: {"home": {선수키: {"slot", "on", "off", "sent_off"}}, "away": {...}} — 키는 선수 id.
    score: {"home": 득점, "away": 득점}."""
    tot = float(max(1, total_minutes))
    side_of, grp, on_min, off_min = {}, {}, {}, {}
    sent_off, red_min = set(), {}
    for sk in ("home", "away"):
        for k, a in (appear.get(sk) or {}).items():
            if a.get("sent_off"):
                sent_off.add(k)
            side_of[k] = sk
            grp[k] = group_of(a.get("slot"))
            on_min[k] = float(a.get("on") or 0)
            off_min[k] = float(a.get("off") if a.get("off") is not None else tot)
    rating = {k: START for k in side_of}
    events = []

    def add(minute, k, code, w=None):
        if k is None or k not in rating:
            return
        g = _GI[grp.get(k, "MID")]
        d = (w if w is not None else W[code][g])
        if not d:
            return
        rating[k] += d
        events.append([round(float(minute), 1), k, round(d, 3), code])

    def on_pitch(sk, minute):
        return [k for k, s in side_of.items()
                if s == sk and on_min[k] <= minute <= off_min[k]]

    _plog = list(plog or [])

    def blame_for_goal(gi):
        """gi번째 골 직전, 같은 공격 흐름에서 뚫린 수비 선수들(중복 없이)."""
        g = _plog[gi]
        gs, gm = g.get("team"), float(g.get("min", 0))
        out = []
        j = gi - 1
        while j >= 0:
            y = _plog[j]
            if gm - float(y.get("min", 0)) > BLAME_WINDOW_MIN:
                break
            yo = y.get("outcome")
            if y.get("team") != gs and yo not in ("foul", "yellow", "red", "sub"):
                break                                   # 상대(수비 팀)가 공을 가졌다 — 흐름 끊김
            if y.get("team") == gs and yo == "buildup":
                if y.get("def_action") == "beaten" and y.get("def_actor_id") is not None:
                    out.append(y.get("def_actor_id"))
                ae = y.get("aerial")
                if ae and ae.get("won_by") == gs and ae.get("def_id") is not None:
                    out.append(ae.get("def_id"))
            j -= 1
        seen, res = set(), []
        for k in out:
            if k not in seen:
                seen.add(k)
                res.append(k)
        return res

    for xi, x in enumerate(_plog):
        o = x.get("outcome")
        m = float(x.get("min", 0))
        sk = x.get("team")
        if o == "goal":
            sc = x.get("scorer_id")
            add(m, sc, "goal_pk" if x.get("kind") == "pk" else "goal")
            add(m, x.get("assist_id"), "assist")
            for k in on_pitch(sk, m):
                if k != sc:
                    add(m, k, "team_goal")
            opp = "away" if sk == "home" else "home"
            for k in on_pitch(opp, m):
                add(m, k, "conceded")
            for k, rm in red_min.items():                # 퇴장 뒤 실점 — 퇴장당한 그 팀 선수 몫
                if side_of.get(k) == opp and m >= rm:
                    add(m, k, "after_red")
            if x.get("kind") != "pk":
                for k in blame_for_goal(xi):
                    if side_of.get(k) == opp:
                        add(m, k, "goal_blame")
        elif o == "save":
            add(m, x.get("shooter_id"), "big_miss" if x.get("big_chance") else "shot_on")
            add(m, x.get("gk_id"), "save_big" if x.get("big_chance") else "save")
            add(m, x.get("blocker_id"), "block")
            add(m, x.get("key_pass_id"), "key_pass")
        elif o == "shot_off":
            add(m, x.get("shooter_id"), "big_miss" if (x.get("xg") or 0) >= 0.35 else "shot_off")
        elif o == "blocked":
            # [2026-10 점유 체인 엔진] 수비수 몸에 막힌 슈팅 — 슈터는 빗나간 슈팅과 같게, 막은 선수는 블록.
            add(m, x.get("shooter_id"), "big_miss" if x.get("big_chance") else "shot_off")
            add(m, x.get("blocker_id"), "block")
            add(m, x.get("key_pass_id"), "key_pass")
        elif o == "offside":
            add(m, x.get("player_id"), "offside")
        elif o == "foul":
            add(m, x.get("player_id"), "foul")
            add(m, x.get("victim_id"), "fouled")
            if x.get("pk"):
                add(m, x.get("victim_id"), "pk_won")
                add(m, x.get("player_id"), "pk_conceded")
        elif o == "yellow":
            add(m, x.get("player_id"), "yellow")
        elif o == "red":
            rk = x.get("player_id")
            if rk in rating and rk not in red_min:
                red_min[rk] = m
                add(m, rk, "red")
                add(m, rk, "red_early", round(RED_EARLY * max(0.0, min(1.0, (tot - m) / tot)), 3))
        elif o == "interception":
            add(m, x.get("player_id"), "interception")      # 실패한 패스를 직접 끊은 수비수
        elif o == "buildup":
            # [재시도] 공격 시도와 그것을 막은(뚫린) 수비수가 한 기록에 같이 있다.
            act, aid, ok = x.get("action"), x.get("actor_id"), x.get("ok")
            dact, did = x.get("def_action"), x.get("def_actor_id")
            if act == "dribble":
                add(m, aid, "dribble_ok" if ok else ("dispossessed" if dact == "tackle" else "dribble_fail"))
            elif act == "cross":
                add(m, aid, "cross_ok" if ok else "cross_fail")
            elif act == "duel" and dact == "tackle":
                add(m, aid, "dispossessed")
            if dact == "tackle":
                add(m, did, "tackle_ok")
            elif dact == "beaten":
                add(m, did, "dribbled_past")
            elif dact == "clearance":
                add(m, did, "clearance")
            ae = x.get("aerial")
            if ae:
                won_def = ae.get("won_by") != sk
                add(m, ae.get("def_id"), "aerial_won" if won_def else "aerial_lost")
                add(m, ae.get("att_id"), "aerial_lost" if won_def else "aerial_won")
    # 패스 — 15분 단위로 묶어 한 줄씩
    buckets = {}
    for m, sk, lst in pass_log or []:
        b = int(float(m) // 15)
        for pid, n, ok in lst:
            e = buckets.setdefault((b, pid), [0, 0])
            e[0] += n
            e[1] += ok
    for (b, pid), (n, ok) in sorted(buckets.items()):
        if pid not in rating:
            continue
        g = _GI[grp.get(pid, "MID")]
        d = ok * PASS_OK[g] + (n - ok) * PASS_FAIL[g]
        add(min(tot, (b + 1) * 15), pid, "pass", round(d, 3))

    # 경기 종료 — 팀 결과·무실점(출전 비중)
    for k, sk in side_of.items():
        share = max(0.0, min(1.0, (off_min[k] - on_min[k]) / tot))
        gf, ga = score.get(sk, 0), score.get("away" if sk == "home" else "home", 0)
        res = "win" if gf > ga else ("loss" if gf < ga else "draw")
        r_d = RESULT[res]
        if res == "loss":
            r_d -= LOSS_MARGIN_STEP[_GI[grp.get(k, "MID")]] * (min(ga - gf, LOSS_MARGIN_CAP) - 1)
        add(tot, k, "result", round(r_d * share, 3))
        if ga == 0 and share >= CLEAN_SHEET_MIN_SHARE:
            add(tot, k, "clean_sheet", CLEAN_SHEET[_GI[grp.get(k, "MID")]] * share)

    # 눈금 — 바뀐 몫은 경기 끝 "scale" 한 줄로 타임라인에 넣는다(사건 합계 = 최종 평점 − 6.0 유지).
    final = {}
    for k, v in rating.items():
        f = scale(v, sent_off=(k in sent_off or k in red_min))
        if abs(f - v) >= 0.0005:
            events.append([round(tot, 1), k, round(f - v, 3), "scale"])
        final[k] = f
    events.sort(key=lambda e: e[0])
    return final, events