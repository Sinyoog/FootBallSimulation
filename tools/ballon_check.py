# -*- coding: utf-8 -*-
"""[2026-11] 발롱도르 새 채점 모듈(ballon_award.py) — 기존 세이브 측정(수상 로직 연결 전).

1단계(수집): 세이브를 임시 폴더에 복사해 읽기만 하고(award_baseline.py의 지난 연도 보정), 연도마다 70% 자격 후보의
  모듈 입력값(골·도움·출전·시즌 OVR·대륙대회 기록·팀 실점·소속팀/국제대회 단계 점수·GK 표준 환경 선방률)과 현재 코드의
  발롱도르 순위를 모아 pickle로 저장한다. 입력값 수집은 야신상 연결(game_engine._yashin_inputs)과 같은 함수를 쓴다.
    python tools/ballon_check.py collect --db 경로/game.db --out 시드.pkl
2단계(보고): 모은 pickle(시드별)로 모듈 결과를 계산해 확인 항목과 관찰 지표를 출력한다.
    python tools/ballon_check.py report 시드105.pkl 시드106.pkl [--calib]
  --calib: 척도 상한(포지션 기대 대비 p99.5 등)을 같은 규칙으로 다시 계산해 보여 준다(값을 바꾸지는 않음).
[2026-11 연결] 모듈 입력은 이제 게임 코드(game_engine._ballon_inputs — 실제 발롱도르 저장과 같은 함수)로 만든다.
  측정 도구에만 있던 입력 계산(대회별 골·도움·리그 수준·예상 순위·팀 공격력·대회 득점 환경)은 game_engine으로 옮겼고
  예전 pickle 보강용 enrich 명령은 없앴다(같은 계산이 두 곳에 있지 않게). 읽기만 한다(세이브를 바꾸지 않음)."""
import _path  # noqa: F401
import argparse
import contextlib
import io
import os
import pickle
import random
import shutil
import statistics as S
import time


def collect(db, out):
    import database
    import game_engine as GE
    import award_baseline as AB
    import award_tie_check as TC
    with contextlib.redirect_stdout(io.StringIO()):
        conn, tmp = AB.prepare(db)
    c = conn.cursor()
    try:
        AB.patch_for_past_years(c)
        years = [r[0] for r in c.execute("SELECT DISTINCT year FROM season_individual_awards "
                                         "WHERE award_type='발롱도르' ORDER BY year").fetchall()]
        lg_name = {r[0]: r[1] for r in c.execute("SELECT id, name FROM leagues").fetchall()}
        data = []
        t0 = time.time()
        for y in years:
            GE._begin_team_gc_run_cache(y)
            try:
                AB.clear_year_caches()
                cands = GE._get_ballon_candidates(c, y)
                cur = TC.pipeline(cands)
                cur_rank = {x["player_id"]: i + 1 for i, x in enumerate(cur[1])}
                rows = GE._ballon_inputs(c, y, cands)          # 실제 발롱도르 저장과 같은 입력 함수
                lmap = GE._season_team_league_map(c, y)
                ranks = GE._yashin_league_ranks(c, y)
                for r, x in zip(rows, cands):                    # 측정 보고용 정보만 덧붙인다(채점에는 안 씀)
                    lid = lmap.get(r["team_id"])
                    r.update(cl_won=GE._get_club_comp_result(y, r["team_id"])[0] == "우승",
                             league=lg_name.get(lid), team_rank=(ranks.get(r["team_id"]) or (None,))[0],
                             cur_rank=cur_rank.get(r["player_id"]),
                             cl_goals=x.get("cl_goals") or 0, cl_matches=x.get("cl_matches") or 0)
                data.append((y, rows))
            finally:
                GE._end_team_gc_run_cache()
            print(f"  {y}: 후보 {len(rows)} · {time.time() - t0:.0f}s")
        with open(out, "wb") as f:
            pickle.dump(data, f)
        print(f"저장 {out}")
    finally:
        os.chdir(_path.ROOT)
        try:
            database.reset_conn_pool()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def report(paths, calib=False):
    import ballon_award as B
    import award_baseline as AB
    G = AB.pos_group
    seeds = []
    for p in paths:
        with open(p, "rb") as f:
            seeds.append((os.path.basename(p), pickle.load(f)))

    def q(v, p):
        v = sorted(v)
        return v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))]
    if calib:
        allr = [r for _n, sd in seeds for _y, d in sd for r in d]
        rel = [B.productivity(r)["rel_rate"] for r in allr]
        vol = [B.productivity(r)["vol_raw"] for r in allr]
        gk = [r["gk_save"] for r in allr if r["position"] == "GK" and r["gk_save"] is not None]
        print(f"[척도 규칙 재계산] 포지션 기대 대비 p99.5 {q(rel, .995):.3f} (모듈 값 {B.REL_BAND[1]}) · 공격 생산량 p50 "
              f"{q(vol, .5):.2f} / p99.5 {q(vol, .995):.2f} (모듈 {B.VOL_BAND}) · GK p50 {q(gk, .5):.3f} / p99.5 "
              f"{q(gk, .995):.3f} (모듈 {B.GK_SAVE_BAND}) · OVR p10 {q([r['ovr'] for r in allr], .1)}")
        return
    print("시드          1위 FW/MF/DF/GK  Top10 % FW/MF/DF/GK  Top30 %       ")
    tot = {}
    for name, sd in seeds:
        m = {k: 0 for k in ("cl_out", "cl_win", "notp1", "team_overtake", "chg", "n", "shuffle_bad")}
        w1, t10, t30, ovr_rk, ovr_cor, trop_ov, dfsh, gksh, best_md = {}, {}, {}, [], [], [], [], [], []
        rng = random.Random(20261108)
        for y, d in sd:
            d = [dict(r) for r in d]
            sc = B.score_season(d)
            top = B.top30(sc)
            for _k in range(3):
                sh = [dict(r) for r in d]
                rng.shuffle(sh)
                t2 = B.top30(B.score_season(sh))
                if [e["player_id"] for e in t2] != [e["player_id"] for e in top]:
                    m["shuffle_bad"] += 1
            w = top[0]
            m["n"] += 1
            for dct, rs in ((w1, [w]), (t10, top[:10]), (t30, top)):
                for r in rs:
                    dct[G(r["position"])] = dct.get(G(r["position"]), 0) + 1
            pers = sorted(sc, key=lambda e: (-e["personal"], e["player_id"]))
            pers10 = {e["player_id"] for e in pers[:10]}
            m["cl_win"] += w["cl_won"]
            m["cl_out"] += w["cl_won"] and w["player_id"] not in pers10
            m["notp1"] += w["player_id"] != pers[0]["player_id"]
            # 같은 팀에서 개인 점수가 더 낮은 동료가 총점으로 앞선 경우(Top30 안 팀만)
            by_t = {}
            for e in sc:
                by_t.setdefault(e["team_id"], []).append(e)
            for t in {e["team_id"] for e in top}:
                ms = by_t[t]
                p1 = max(ms, key=lambda e: (e["personal"], -e["player_id"]))
                m["team_overtake"] += sum(1 for e in ms if e is not p1 and e["total"] > p1["total"] + 1e-12)
            m["chg"] += w.get("cur_rank") != 1
            ovr_rk.append(1 + sum(1 for r in sc if (r["ovr"] or 0) > (w["ovr"] or 0)))
            cc = AB.spearman([e["ovr"] for e in top], list(range(1, len(top) + 1)))
            if cc is not None:
                ovr_cor.append(cc)
            tr10 = {e["player_id"] for e in sorted(sc, key=lambda e: (-e["trophy"], e["player_id"]))[:10]}
            trop_ov.append(len(tr10 & {e["player_id"] for e in top[:10]}))
            for e in top:
                if G(e["position"]) == "DF":
                    dfsh.append(B.W_PERSONAL * (1 - B.OVR_SHARE) * B.DEF_TEAM_SHARE * e["tdef"] / max(1e-9, e["personal"]))
                if G(e["position"]) == "GK":
                    gksh.append(B.W_PERSONAL * (1 - B.OVR_SHARE) * B.PROD_MIX * B.DEF_TEAM_SHARE * e["tdef"]
                                / max(1e-9, e["personal"]))
            md = [i + 1 for i, e in enumerate(top) if G(e["position"]) in ("MF", "DF")]
            best_md.append(md[0] if md else 31)
        n = m["n"]

        def pc(dct):
            t = sum(dct.values()) or 1
            return "/".join(f"{100 * dct.get(g, 0) / t:.0f}" for g in ("FW", "MF", "DF", "GK"))
        print(f"{name:12}  {'/'.join(str(w1.get(g, 0)) for g in ('FW', 'MF', 'DF', 'GK')):14}  {pc(t10):18}  {pc(t30)}")
        tot[name] = (m, ovr_rk, ovr_cor, trop_ov, dfsh, gksh, best_md, n)
    print()
    print("확인 항목                                              " + "  ·  ".join(n for n in tot))
    rows = [
        ("[1] 챔스 우승팀 + 개인 점수 Top10 밖인데 1위", lambda t: f"{t[0]['cl_out']}/{t[7]}"),
        ("[2] 1위가 그 시즌 개인 점수 1위가 아닌 시즌", lambda t: f"{t[0]['notp1']}/{t[7]}"),
        ("[2] Top30 팀 안에서 개인 점수 1순위를 동료가 총점으로 역전", lambda t: f"{t[0]['team_overtake']}건"),
        ("[3] Top30 안 MF·DF 최고 순위(중앙값)", lambda t: f"{S.median(t[6])}위"),
        ("[5] Top30 DF 개인 점수 중 팀 수비 몫(평균·최대)",
         lambda t: f"{100 * S.mean(t[4]):.0f}%·{100 * max(t[4]):.0f}%" if t[4] else "-"),
        ("[5] Top30 GK 개인 점수 중 팀 수비 몫(평균)", lambda t: f"{100 * S.mean(t[5]):.0f}%" if t[5] else "-"),
        ("결정론: 후보 순서 섞기 3회 불일치", lambda t: f"{t[0]['shuffle_bad']}"),
        ("관찰: 1위 챔스 우승팀", lambda t: f"{t[0]['cl_win']}/{t[7]}"),
        ("관찰: 1위 OVR 순위(후보 중) 중앙값", lambda t: f"{S.median(t[1])}"),
        ("관찰: OVR–순위 상관(Top30, 연도별 중앙값)", lambda t: f"{S.median(t[2]):.2f}"),
        ("관찰: 트로피 Top10과 최종 Top10 겹침", lambda t: f"{S.mean(t[3]):.1f}/10"),
        ("관찰: 현재 코드 대비 1위 바뀐 시즌", lambda t: f"{t[0]['chg']}/{t[7]}"),
    ]
    for lab, f in rows:
        print(f"{lab:52} " + "  ·  ".join(f(tot[n]) for n in tot))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("collect")
    a1.add_argument("--db", required=True)
    a1.add_argument("--out", required=True)
    a2 = sub.add_parser("report")
    a2.add_argument("pkl", nargs="+")
    a2.add_argument("--calib", action="store_true")
    a = ap.parse_args()
    if a.cmd == "collect":
        collect(os.path.abspath(a.db), os.path.abspath(a.out))
    else:
        report([os.path.abspath(p) for p in a.pkl], a.calib)


if __name__ == "__main__":
    main()