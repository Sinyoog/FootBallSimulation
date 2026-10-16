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
  · 최종값은 3.0~10.0, 소수 첫째 자리.

반환: compute(...) → (ratings, events)
  ratings: {선수키: 최종 평점}
  events : [[분, 선수키, 변화량, 사유코드], ...] — 경기 상세 리플레이가 시간순으로 재생한다.
           패스는 사건 수가 너무 많아 15분 단위로 묶어 한 줄("pass")로 넣는다.
"""

START = 6.0
MIN_R, MAX_R = 3.0, 10.0

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
    "red":             (-1.30, -1.30, -1.30, -1.30),
    "save":            (0.15, 0.0, 0.0, 0.0),
    "save_big":        (0.30, 0.0, 0.0, 0.0),
    "conceded":        (-0.25, -0.10, -0.05, -0.02),   # 실점 순간 경기장에 있던 선수 전원
    "team_goal":       (0.03, 0.04, 0.05, 0.05),       # 득점 순간 경기장에 있던 선수 전원(득점자 제외)
}
# 패스(15분 묶음): 성공 1회당, 실패 1회당 — 엔진 패스량이 현실 수준(경기당 팀 400~500회)이 된 뒤
# 원래 값으로 되돌렸다(패스 많이 한 선수가 과하게 유리해지지 않게).
PASS_OK = (0.004, 0.005, 0.006, 0.005)
PASS_FAIL = (-0.012, -0.015, -0.015, -0.012)
# 경기 종료(출전 시간 비중을 곱한다)
RESULT = {"win": 0.30, "draw": 0.0, "loss": -0.30}
CLEAN_SHEET = (0.50, 0.30, 0.10, 0.0)
CLEAN_SHEET_MIN_SHARE = 0.65   # 이만큼 이상 뛴 선수만 무실점 가산
_GI = {"GK": 0, "DEF": 1, "MID": 2, "ATT": 3}


def compute(plog, pass_log, appear, total_minutes, score):
    """plog: tactical_engine possession_log(선수 사건 포함). pass_log: [[분, 팀, [[id, n, ok], ...]], ...].
    appear: {"home": {선수키: {"slot", "on", "off", "sent_off"}}, "away": {...}} — 키는 선수 id.
    score: {"home": 득점, "away": 득점}."""
    tot = float(max(1, total_minutes))
    side_of, grp, on_min, off_min = {}, {}, {}, {}
    for sk in ("home", "away"):
        for k, a in (appear.get(sk) or {}).items():
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

    for x in plog or []:
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
            add(m, x.get("player_id"), "red")
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
        add(tot, k, "result", round(RESULT[res] * share, 3))
        if ga == 0 and share >= CLEAN_SHEET_MIN_SHARE:
            add(tot, k, "clean_sheet", CLEAN_SHEET[_GI[grp.get(k, "MID")]] * share)

    final = {k: round(max(MIN_R, min(MAX_R, v)), 1) for k, v in rating.items()}
    events.sort(key=lambda e: e[0])
    return final, events