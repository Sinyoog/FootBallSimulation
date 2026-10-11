# -*- coding: utf-8 -*-
"""tools/plan_strength_report.py — ⑦ 장기 검증 판정 보고 (2026-10 신설)

사용: python tools/plan_strength_report.py <runs_dir> [seed ...]   (기본 시드 12345 777)
      <runs_dir>/e7_{base,new}_{seed}/per_year.jsonl을 읽는다(tools/plan_strength_longrun.py).

판정 기준(2026-10 신민용 사전 확정, 결과 보고 바꾸지 않음)
  1 실력 반영: 1부 순위↔OVR 상위11 상관이 기준선 −0.03 이상(안전장치)
  2 득점 환경: 리그 경기당 총 득점 ±2%
  3 이변 비율: 5점+ 약팀 승률 상대 ±10%
  4 강팀 안정: 명문 우승 점유율 ±10%p, OVR 상위6 강등 기준선 이하
  관찰(핵심): 포메이션 성향별 승점 잔차 — 공격형 포메이션이 이름만으로 얻던 이득이 사라지는가
"""
import json, os, statistics as st, sys
from collections import defaultdict

RUNS = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "qa_runs"
SEEDS = [int(s) for s in (sys.argv[2:] or ["12345", "777"])]


def load(mode, seed):
    p = os.path.join(RUNS, f"e7_{mode}_{seed}", "per_year.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def spearman(a, b):
    n = len(a)
    if n < 3:
        return None
    ra = {i: r for r, i in enumerate(sorted(range(n), key=lambda i: a[i]))}
    rb = {i: r for r, i in enumerate(sorted(range(n), key=lambda i: b[i]))}
    return 1 - 6 * sum((ra[i] - rb[i]) ** 2 for i in range(n)) / (n * (n * n - 1))


def metrics(Y, skip_first=False):
    out = defaultdict(list)
    goals = [0, 0]; up = defaultdict(int)
    pres_t = [0, 0]; top6_rel = 0; t_year = []
    resid = defaultdict(list); resid_form = defaultdict(list)
    for yi, y in enumerate(Y):
        if skip_first and yi == 0:
            continue
        t_year.append(y["t"])
        for g in y["lg_goals"].values():
            goals[0] += g[0]; goals[1] += g[1]
        for k, v in y["upsets"].items():
            up[k] += v
        by_lg = defaultdict(list)
        for tid, r in y["teams"].items():
            lg, tier, country, grade, form, style, t11, xi, pres, name, pts, gp, gf, ga = r
            by_lg[lg].append(dict(tid=tid, tier=tier, form=form, style=style, t11=t11, xi=xi,
                                  pres=pres, name=name, pts=pts, gp=gp, gd=gf - ga, gf=gf))
        rel = {(n_, ) for (n_, ft, tt, ln) in y["promo"] if ft == 1 and tt and tt > ft}
        # 포메이션 잔차용: 리그 안 t11 편차 → ppg
        xs, ys, meta = [], [], []
        for lg, L in by_lg.items():
            if len(L) < 6:
                continue
            m = st.mean(x["t11"] for x in L)
            for x in L:
                if x["gp"]:
                    xs.append(x["t11"] - m); ys.append(x["pts"] / x["gp"]); meta.append(x)
            if L[0]["tier"] != 1 or len(L) < 8:
                continue
            order = sorted(L, key=lambda x: (-x["pts"], -x["gd"], -x["gf"]))
            rank = {x["tid"]: i for i, x in enumerate(order)}
            s = spearman([-rank[x["tid"]] for x in L], [x["t11"] for x in L])
            if s is not None:
                out["rank_corr_t11"].append(s)
            s2 = spearman([-rank[x["tid"]] for x in L], [x["xi"] for x in L])
            if s2 is not None:
                out["rank_corr_xi"].append(s2)
            if any(x["pres"] for x in L):
                pres_t[1] += 1; pres_t[0] += 1 if order[0]["pres"] else 0
            top6 = sorted(L, key=lambda x: -x["t11"])[:6]
            top6_rel += sum(1 for x in top6 if (x["name"],) in rel)
        if len(xs) > 10:
            mx, my = st.mean(xs), st.mean(ys)
            b = sum((a - mx) * (c - my) for a, c in zip(xs, ys)) / sum((a - mx) ** 2 for a in xs)
            for a, c, x in zip(xs, ys, meta):
                r_ = c - (my + b * (a - mx))
                grp = "공격형(+1.0 이상)" if x["style"] >= 1.0 else ("수비형(−1.0 이하)" if x["style"] <= -1.0 else "중립")
                resid[grp].append(r_); resid_form[x["form"]].append(r_)
    return {"rank_corr_t11": st.mean(out["rank_corr_t11"]) if out["rank_corr_t11"] else None,
            "rank_corr_xi": st.mean(out["rank_corr_xi"]) if out["rank_corr_xi"] else None,
            "goals": goals[0] / goals[1] if goals[1] else None,
            "upset5": up["w5"] / up["n5"] if up["n5"] else None, "n5": up["n5"],
            "upset5_t1": up["w5_t1"] / up["n5_t1"] if up["n5_t1"] else None,
            "pres_share": pres_t[0] / pres_t[1] if pres_t[1] else None, "pres_n": pres_t[1],
            "top6_rel": top6_rel, "t_year": st.mean(t_year) if t_year else None,
            "resid": {k: (st.mean(v), len(v)) for k, v in resid.items()},
            "resid_form": {k: (st.mean(v), len(v)) for k, v in resid_form.items()}}


def pooled(mode, skip_first=False):
    Ys = [load(mode, s) for s in SEEDS]
    per = [metrics(Y, skip_first) for Y in Ys if Y]
    return per, [len(Y) for Y in Ys]


for skip in (False, True):
    print("=" * 70)
    print("전체 10시즌" if not skip else "첫해(2000) 제외")
    B, nb = pooled("base", skip); N, nn = pooled("new", skip)
    print("완료 시즌 수 base", nb, "new", nn)
    def avg(L, k):
        v = [x[k] for x in L if x[k] is not None]
        return st.mean(v) if v else None
    rows = [("1 순위↔OVR 상위11 상관", "rank_corr_t11"), ("  (참고) 순위↔선발11 전력 상관", "rank_corr_xi"),
            ("2 리그 경기당 총 득점", "goals"), ("3 이변률(5점+ 약팀 승)", "upset5"),
            ("  (참고) 이변률 1부", "upset5_t1"), ("4a 명문 우승 점유율", "pres_share"),
            ("4b 상위6 강등(합계)", "top6_rel"), ("  연간 처리 시간(s)", "t_year")]
    for label, k in rows:
        bs = [x[k] for x in B]; ns = [x[k] for x in N]
        ba, na = avg(B, k), avg(N, k)
        if k == "top6_rel":
            ba, na = sum(bs), sum(ns)
        print(f"{label:28s} base {['%.4f' % v if isinstance(v, float) else v for v in bs]} → 평균 {ba if not isinstance(ba, float) else round(ba, 4)} | "
              f"new {['%.4f' % v if isinstance(v, float) else v for v in ns]} → 평균 {na if not isinstance(na, float) else round(na, 4)}")
    ba, na = avg(B, "rank_corr_t11"), avg(N, "rank_corr_t11")
    c1 = na >= ba - 0.03
    g = avg(N, "goals") / avg(B, "goals") - 1
    c2 = abs(g) <= 0.02
    u = avg(N, "upset5") / avg(B, "upset5")
    c3 = 0.9 <= u <= 1.1
    ps = avg(N, "pres_share") - avg(B, "pres_share")
    rb, rn = sum(x["top6_rel"] for x in B), sum(x["top6_rel"] for x in N)
    c4 = abs(ps) <= 0.10 and rn <= rb
    print(f"판정 1 실력 반영  Δ={na - ba:+.4f} (기준 ≥ −0.03) → {'통과' if c1 else '실패'}")
    print(f"판정 2 득점 환경  {g * 100:+.2f}% (기준 ±2%) → {'통과' if c2 else '실패'}")
    print(f"판정 3 이변 비율  ×{u:.3f} (기준 0.90~1.10) → {'통과' if c3 else '실패'}")
    print(f"판정 4 강팀 안정  명문 우승 Δ{ps * 100:+.1f}%p (±10%p), 상위6 강등 {rb}→{rn} (≤기준선) → {'통과' if c4 else '실패'}")
    print("관찰: 포메이션 성향별 승점 잔차(경기당, 같은 OVR 상위11 대비)")
    for mode, L in (("base", B), ("new", N)):
        agg = defaultdict(lambda: [0.0, 0])
        for x in L:
            for k, (m, n) in x["resid"].items():
                agg[k][0] += m * n; agg[k][1] += n
        print(f"  {mode}: " + " | ".join(f"{k} {v[0] / v[1]:+.4f} (n={v[1]})" for k, v in sorted(agg.items())))
    for mode, L in (("base", B), ("new", N)):
        agg = defaultdict(lambda: [0.0, 0])
        for x in L:
            for k, (m, n) in x["resid_form"].items():
                agg[k][0] += m * n; agg[k][1] += n
        print(f"  {mode} 포메이션별: " + " ".join(f"{k}:{v[0] / v[1]:+.3f}" for k, v in sorted(agg.items(), key=lambda kv: -kv[1][0] / kv[1][1])))