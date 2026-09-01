# -*- coding: utf-8 -*-
"""match_sim/football_metrics.py — 축구 "사건" 기준 계측.

## 왜 지표를 갈아엎었나

이전 계측(`motion_metrics.py`)은 **변위 통계**였다 — 이동거리, 정지 비율,
팀 폭, 동시 이동 인원. 그 지표들로 튜닝했더니 19개 중 16개가 "정상"인데도
화면은 여전히 축구가 아니었다. 이유가 명확하다:

> **새 떼도 그 지표를 전부 통과한다.**

물고기 떼든 새 떼든 적당히 뭉쳐서 이동하면 팀 폭 45m, 이동거리 10km,
동시 이동 8명이 나온다. 그 지표들은 "22개 점이 그럴듯하게 움직이는가"만
보지 **축구를 하고 있는가**는 전혀 보지 않는다.

여기서는 축구에서만 나오는 사건을 센다. 패스가 몇 번 성공하는가, 어디서
끊기는가, 볼을 받고 몇 초 만에 압박당하는가, 한 번의 공격에서 몇 명이
전진 런을 시도하는가, 슛이 몇 개고 그중 몇 개가 골이 되는가. 새 떼는
패스 성공률이라는 개념 자체가 없다.

## 참조값 출처

공개된 리그 통계(EPL/UCL 계열)의 경기당 팀 평균이다. 정확한 단일 수치가
아니라 "이 범위 밖이면 축구가 아니다"라는 밴드다.
"""

import math
from collections import Counter

# ── 실축 참조 (경기 전체 = 양 팀 합산 기준) ──
REFERENCE = {
    "passes_total":      (600.0, 1100.0, "총 패스 시도"),
    "pass_acc":          (0.72, 0.88, "패스 성공률"),
    "shots_total":       (16.0, 34.0, "총 슈팅"),
    "shots_on_frac":     (0.28, 0.45, "온타깃 비율"),
    "goals_total":       (1.5, 4.5, "총 득점"),
    "conversion":        (0.06, 0.16, "슈팅 대비 득점률"),
    "corners_total":     (6.0, 16.0, "총 코너킥"),
    "throw_ins_total":   (25.0, 55.0, "총 스로인"),
    "goal_kicks_total":  (8.0, 22.0, "총 골킥"),
    "fouls_total":       (16.0, 34.0, "총 파울"),
    "turnovers_total":   (90.0, 180.0, "총 소유권 전환"),
    "possession_len_s":  (7.0, 20.0, "한 번의 소유 평균 지속(초)"),
    "passes_per_poss":   (2.2, 5.0, "소유당 평균 패스 수"),
    "time_to_pressure_s": (1.0, 3.0, "볼 받고 압박까지(초)"),
    "runs_per_poss":     (0.6, 2.5, "소유당 전진 런 시도"),
    "pass_dist_m":       (14.0, 24.0, "평균 패스 거리(m)"),
    "into_space_frac":   (0.10, 0.35, "공간으로 찌른 패스 비율"),
}


def analyze(engine):
    """LiveMatchEngine 한 판의 사건 로그를 분석한다."""
    ev = engine.events
    c = Counter(e["type"] for e in ev)

    passes = [e for e in ev if e["type"] == "pass"]
    presults = [e for e in ev if e["type"] == "pass_result"]
    shots = [e for e in ev if e["type"] == "shot"]
    goals = [e for e in ev if e["type"] == "goal"]
    controls = [e for e in ev if e["type"] == "control"]
    turnovers = [e for e in controls if e.get("turnover")]

    ok = sum(1 for e in presults if e.get("ok"))
    pass_acc = ok / max(1, len(presults))

    # 소유 구간 — 소유권 전환 사이의 구간
    seqs, cur = [], None
    for e in controls:
        if cur is None or e.get("turnover"):
            if cur:
                seqs.append(cur)
            cur = {"side": e["side"], "t0": e["t"], "t1": e["t"], "passes": 0, "runs": 0}
        cur["t1"] = e["t"]
    if cur:
        seqs.append(cur)
    for pe in passes:
        for s in seqs:
            if s["t0"] <= pe["t"] <= s["t1"] + 3.0 and s["side"] == pe["side"]:
                s["passes"] += 1
                if pe.get("space"):
                    s["runs"] += 1
                break
    durs = [max(0.1, s["t1"] - s["t0"]) for s in seqs] or [1.0]

    # 온타깃 = 골 + 골키퍼가 막은 슛
    saves = [e for e in ev if e["type"] == "shot_stopped" and e.get("by_gk")]
    on_target = len(goals) + len(saves)

    out = {
        "passes_total": len(passes),
        "pass_acc": pass_acc,
        "shots_total": len(shots),
        "shots_on_frac": on_target / max(1, len(shots)),
        "goals_total": len(goals),
        "conversion": len(goals) / max(1, len(shots)),
        "corners_total": c.get("corner", 0),
        "throw_ins_total": c.get("throw_in", 0),
        "goal_kicks_total": c.get("goal_kick", 0),
        "fouls_total": c.get("foul", 0),
        "turnovers_total": len(turnovers),
        "possession_len_s": sum(durs) / len(durs),
        "passes_per_poss": len(passes) / max(1, len(seqs)),
        "runs_per_poss": sum(s["runs"] for s in seqs) / max(1, len(seqs)),
        "pass_dist_m": (sum(e.get("dist", 0.0) for e in passes) / max(1, len(passes))),
        "into_space_frac": sum(1 for e in passes if e.get("space")) / max(1, len(passes)),
        "time_to_pressure_s": _time_to_pressure(engine),
        "_n_sequences": len(seqs),
    }
    return out


def _time_to_pressure(engine):
    """볼을 받고 상대가 3.5m 안으로 붙기까지 걸린 시간의 평균.

    이 값이 크면 아무도 압박하지 않는 것이고(= 수비가 없는 축구), 너무
    작으면 항상 밀착 상태라 빌드업이 성립하지 않는다.
    """
    frames = engine.frames
    if len(frames) < 3:
        return float("nan")
    # 프레임에는 소유 팀만 있고 개별 보유자는 없으므로, 소유 팀이 바뀐
    # 시점부터 "볼 3.5m 안에 상대가 들어올 때까지"를 잰다.
    times = []
    prev_poss = None
    start = None
    for f in frames:
        poss = f.get("possession")
        if poss != prev_poss:
            prev_poss = poss
            start = f["clock"] * 60.0
            continue
        if start is None:
            continue
        bx, by = f["ball"]
        opp = "away" if poss == "home" else "home"
        near = min((math.hypot((px - bx) * 105.0, (py - by) * 68.0)
                    for px, py in f[opp]), default=99.0)
        if near < 3.5:
            times.append(f["clock"] * 60.0 - start)
            start = None
    return (sum(times) / len(times)) if times else float("nan")


def aggregate(reports):
    keys = reports[0].keys()
    agg = {}
    for k in keys:
        if k.startswith("_"):
            continue
        vals = [r[k] for r in reports if not (isinstance(r[k], float) and math.isnan(r[k]))]
        if vals:
            agg[k] = sum(vals) / len(vals)
    return agg


def format_report(agg, title="축구 사건 계측"):
    lines = ["=" * 76, f"  {title}", "=" * 76,
             f"{'지표':<24}{'측정값':>12}{'  실축 참조':>18}   판정", "-" * 76]
    bad = []
    for key, (lo, hi, label) in REFERENCE.items():
        if key not in agg:
            continue
        v = agg[key]
        if v < lo:
            mark = f"낮음 ({v/lo*100:.0f}%)"
            bad.append((label, v, lo, hi))
        elif v > hi:
            mark = f"높음 ({v/hi:.1f}배)"
            bad.append((label, v, lo, hi))
        else:
            mark = "정상"
        lines.append(f"{label:<24}{v:>12.2f}{f'{lo:g} ~ {hi:g}':>18}   {mark}")
    lines.append("-" * 76)
    if bad:
        lines.append("  ▸ 범위 밖")
        for label, v, lo, hi in bad:
            lines.append(f"    {label:<22} {v:9.2f}   (기대 {lo:g}~{hi:g})")
    else:
        lines.append("  전 항목 참조 범위 내.")
    lines.append("=" * 76)
    return "\n".join(lines)