# -*- coding: utf-8 -*-
"""manager_jobless_report.py — ⑤-4 계측: 85+ 무직 감독이 공석마다 어디서 멈추나 (2026-10 신설)

manager_growth_longrun.py run ... --trace 로 돈 out_dir의 snap.sqlite(market.jtrace)를 읽는다.

  python tools/manager_jobless_report.py <out_dir> [...]

대상 = 그 해 감독 시장 시작 시점 무직 & 능력 85+. 공석마다 대상이 그 자리에 적합(상한·하한
통과)하면, 막힌 첫 단계를 센다: ban(재부임 금지) → rookie_slot(신인 자리로 정해짐) → nat(어느
국적 계획에도 없음) → nat_fallback(반대쪽 대체 계획에만 있는데 앞 계획에서 이미 뽑혀 안 돌았음) →
window(최고 경력이 후보 창 밖) → nat_order(같은 계획 앞 국적들만으로 무직 정원이 참) →
pool_cut(자기 국적 안 순서에서 잘림) → unfit_in_pass → step_skip(하향 굴림) → outscored → hired.
combo = 국적/창/금지를 각각 따로 판정한 병렬 결과.
"""
import collections
import json
import os
import sqlite3
import statistics as st
import sys


def summarize(out):
    c = sqlite3.connect(os.path.join(out, "snap.sqlite"))
    stage, combo, unfit = collections.Counter(), collections.Counter(), collections.Counter()
    T, lost, byab = [], [], collections.defaultdict(lambda: [0, 0])
    nat_of = collections.Counter()
    for _y, j in c.execute("SELECT year, js FROM market ORDER BY year"):
        mk = json.loads(j)
        if not mk or "jtrace" not in mk:
            continue
        jt = mk["jtrace"]
        T.append(jt["T"])
        stage.update(jt["stage"]); combo.update(jt["combo"]); unfit.update(jt["unfit"])
        lost += jt["lost_to"]
        for _m, p in jt["per_mgr"].items():
            b = "90+" if p["ab"] >= 90 else "85-89"
            byab[b][0] += 1
            byab[b][1] += 1 if p.get("hired") else 0
            nat_of[p["nation"]] += 1
    tot = sum(stage.values()) or 1
    ok_pairs = sum(stage.get(k, 0) for k in ("nat_order", "pool_cut", "unfit_in_pass", "step_skip", "outscored", "hired"))
    ct = sum(combo.values()) or 1
    res = {
        "targets_by_year": T,
        "hired_rate_by_ability": {k: f"{v[1]}/{v[0]}" for k, v in byab.items()},
        "pairs": tot,
        "first_stop": {k: f"{v} ({v / tot * 100:.1f}%)" for k, v in stage.most_common()},
        "after_nat_window_ok": {k: f"{stage.get(k, 0)} ({stage.get(k, 0) / max(1, ok_pairs) * 100:.1f}%)"
                                for k in ("nat_order", "pool_cut", "unfit_in_pass", "step_skip",
                                          "outscored", "hired")},
        "parallel": {k: f"{v} ({v / ct * 100:.1f}%)" for k, v in combo.most_common()},
        "unfit_pairs": dict(unfit),
        "target_nat_top": nat_of.most_common(8),
    }
    if lost:
        res["outscored"] = {
            "n": len(lost),
            "target_ab": round(st.mean(x[0] for x in lost), 1),
            "winner_ab": round(st.mean(x[1] for x in lost), 1),
            "winner_free": sum(1 for x in lost if x[2] == "free"),
            "winner_move": sum(1 for x in lost if x[2] == "move"),
            "winner_lower_ab": sum(1 for x in lost if x[1] < x[0]),
            "seat_lv": round(st.mean(x[3] for x in lost), 1),
            "target_cb": round(st.mean(x[4] for x in lost), 1),
            "winner_cb": round(st.mean(x[5] for x in lost), 1),
        }
    return res


if __name__ == "__main__":
    print(json.dumps({o: summarize(os.path.abspath(o)) for o in sys.argv[1:]},
                     ensure_ascii=False, indent=1))