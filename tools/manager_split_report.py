# -*- coding: utf-8 -*-
"""manager_split_report.py — ⑤-2(무직/현직 후보 정원) 실험 비교 (2026-10 신설)

manager_growth_longrun.py run ... --trace 로 돈 out_dir들의 snap.sqlite(market.ps_log /
moves_log)를 읽어 상위 50·자리 수준 100+ 공석의 부임 종류(무직/현직/신인), 부임자 질
(능력 − 팀 OVR), 후보 풀(첫 패스 현직/무직 풀·적합 수·1순위 종류·거절), 연쇄 이동
(목적지 수준별), 신인·무직 수를 JSON으로 낸다.

  python tools/manager_split_report.py <out_a> <out_b> [...]

ps_log 한 줄: [팀, 팀 OVR 순위, 자리 수준, 팀 OVR, 요구 능력, 부임 종류, 능력, 평판, 자국인가,
부임 패스, p0 현직 풀, p0 무직 풀, p0 현직 적합, p0 무직 적합, p0 1순위 종류, p0 첫 현직 순번,
거절 수, 하향 건너뜀 수, 패스 수]
"""
import sqlite3, json, sys, os, statistics as st
def load(out):
    s = sqlite3.connect(os.path.join(out, "snap.sqlite"))
    mk = {y: json.loads(j) for y, j in s.execute("SELECT year, js FROM market ORDER BY year")}
    mk = {y: v for y, v in mk.items() if v}
    top = {y: (a, b) for y, a, b in s.execute("SELECT year, top50, top100 FROM top")}
    jobless = {y: n for y, n in s.execute(
        "SELECT year, SUM(CASE WHEN retired=0 AND status='free' THEN 1 ELSE 0 END) FROM snap GROUP BY year")}
    active = {y: n for y, n in s.execute("SELECT year, SUM(1-retired) FROM snap GROUP BY year")}
    n90 = {y: n for y, n in s.execute("SELECT year, SUM(CASE WHEN retired=0 AND ability>=90 THEN 1 ELSE 0 END) FROM snap GROUP BY year")}
    return mk, top, jobless, active, n90
def pct(a, b): return f"{a/b*100:.1f}%" if b else "-"
def m(x): return round(st.mean(x), 2) if x else None
def summ(out):
    mk, top, jl, act, n90 = load(out)
    R = {}
    ps = [r for y in mk for r in mk[y].get("ps_log", [])]
    mv = [r for y in mk for r in mk[y].get("moves_log", [])]
    for name, sel in (("top50", [r for r in ps if r[1] <= 50]), ("lv100", ps)):
        k = {"free": 0, "move": 0, "rookie": 0}
        for r in sel: k[r[5]] += 1
        n = len(sel)
        hired = [r for r in sel if r[6] is not None and r[3] is not None]
        d = [r[6] - r[3] for r in hired]
        R[name] = dict(
            n=n, free=k["free"], move=k["move"], rookie=k["rookie"],
            move_share=pct(k["move"], n), free_share=pct(k["free"], n),
            gap_mean=m(d), gap_le_m10=pct(sum(1 for x in d if x <= -10), len(d)),
            ab90=sum(1 for r in hired if r[6] >= 90), ab_lt85=sum(1 for r in hired if r[6] < 85),
            ab_mean=m([r[6] for r in hired]),
            move_ab_mean=m([r[6] for r in hired if r[5] == "move"]),
            free_ab_mean=m([r[6] for r in hired if r[5] == "free"]),
            foreign=pct(sum(1 for r in hired if r[8] is False), len(hired)),
            p0_mv_pool=m([r[10] for r in sel if r[10] is not None]),
            p0_fr_pool=m([r[11] for r in sel if r[11] is not None]),
            p0_mv_fit=m([r[12] for r in sel if r[12] is not None]),
            p0_fr_fit=m([r[13] for r in sel if r[13] is not None]),
            p0_mv_fit_any=pct(sum(1 for r in sel if (r[12] or 0) > 0), n),
            p0_top_move=pct(sum(1 for r in sel if r[14] == "move"), n),
            first_mv_rank=m([r[15] for r in sel if r[15] is not None]),
            decl=sum(r[16] for r in sel), skip=sum(r[17] for r in sel),
            # 첫 패스에 적합 현직이 있었는데 무직이 간 경우: 1순위가 무직(점수에서 밀림) / 1순위가 현직인데 거절
            fit_but_free_outscored=sum(1 for r in sel if (r[12] or 0) > 0 and r[5] == "free" and r[14] == "free"),
            fit_but_free_declined=sum(1 for r in sel if (r[12] or 0) > 0 and r[5] == "free" and r[14] == "move"),
            later_pass=sum(1 for r in sel if (r[9] or 0) > 0),
        )
    yrs = sorted(mk)
    R["market"] = dict(
        years=yrs,
        vacancies=[mk[y].get("vacancies") for y in yrs],
        moved=[mk[y].get("moved") for y in yrs],
        rehired=[mk[y].get("rehired") for y in yrs],
        rookie=[mk[y].get("rookie") for y in yrs],
        rookie_forced=[mk[y].get("rookie_forced") for y in yrs],
        free_pool=[mk[y].get("free_pool") for y in yrs],
        sacked=[mk[y].get("sacked") for y in yrs],
        hire_foreign=[mk[y].get("hire_foreign") for y in yrs],
    )
    for k in ("vacancies", "moved", "rehired", "rookie", "rookie_forced", "free_pool", "sacked", "hire_foreign"):
        R["market"][k + "_sum"] = sum(x or 0 for x in R["market"][k])
    R["market"]["total_hires"] = R["market"]["vacancies_sum"] + R["market"]["moved_sum"]
    # 현직 이동 목적지 수준
    R["moves"] = dict(n=len(mv), dest_ge100=sum(1 for r in mv if r[6] >= 100),
                      dest_60_100=sum(1 for r in mv if 60 <= r[6] < 100),
                      dest_lt60=sum(1 for r in mv if r[6] < 60),
                      raw_gain_lt8=sum(1 for r in mv if r[4]),
                      from_ge100=sum(1 for r in mv if r[6] - r[2] >= 100))
    R["state"] = dict(jobless={y: jl[y] for y in sorted(jl)}, active={y: act[y] for y in sorted(act)},
                      n90={y: n90[y] for y in sorted(n90)},
                      top50_club={y: round(top[y][0] or 0, 2) for y in sorted(top)})
    return R
outs = sys.argv[1:]
res = {o: summ(o) for o in outs}
print(json.dumps(res, ensure_ascii=False, indent=1))