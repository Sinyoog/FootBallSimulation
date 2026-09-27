# -*- coding: utf-8 -*-
"""전술엔진 득점자 분산 계측 (2026-09 신설).

tools/ratings_sum_qa.py --no-reconcile 실측에서 "수정 전엔 우리 팀 골 합계가
0으로 뜨는 경기가 대부분"이라는 결과가 나왔다 — 치환된 슬롯 하나가 그 팀
골을 거의 다 갖고 있었다는 뜻이다. 그게 사실인지(= 전술엔진이 골을 한
선수에게 몰아주는지) 따로 확인한다.

내 슬롯 치환과 무관하게, tactical_engine이 만든 원본 11명 기록에서
  · 팀 득점이 2골 이상인 경기에서 최다 득점 슬롯이 몇 골을 가졌는지
  · 득점자가 몇 명인지
를 집계한다.

사용: python3 tools/scorer_spread_qa.py --db <run.db> [--n 200] [--team 1033]
"""
import _path  # noqa: F401
import argparse
import collections
import os
import random
import shutil
import sqlite3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--team", type=int, default=0)
    ap.add_argument("--work", default=None)
    ap.add_argument("--no-stamina", action="store_true",
                    help="경기 내부 체력/교잴를 끄고 예전 동작을 재현(A/B)")
    ap.add_argument("--et", action="store_true", help="단판 KO처럼 연장 허용")
    ap.add_argument("--legacy", action="store_true",
                    help="넓혀진 슈터 풀을 끄고 예전 동작을 재현(A/B)")
    args = ap.parse_args()

    work = os.path.abspath(args.work or "scorer_spread_qa")
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "spread.db")
    shutil.copy(os.path.abspath(args.db), db)
    hsrc = os.path.splitext(os.path.abspath(args.db))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")

    import database
    database.DB_PATH = db
    os.chdir(work)
    database.init_db()
    import power_ranking
    conn = database.get_conn()
    power_ranking.ensure_power_ranking_tables(conn)

    import match_sim.tactical_engine as _te
    if args.legacy:
        _te.WIDE_SHOOTER_POOL = False
        print('[qa] --legacy: WIDE_SHOOTER_POOL=False')
    if args.no_stamina:
        _te.STAMINA_MODEL = False
        print('[qa] --no-stamina: STAMINA_MODEL=False')
    from match_sim.tactical_engine import simulate_my_match
    import game_engine as ge

    tid = args.team or (ge.get_player() or {}).get("current_team_id") or 1
    row = conn.execute("SELECT id, name, league_id FROM teams WHERE id=?", (tid,)).fetchone()
    opps = [r["id"] for r in conn.execute(
        "SELECT id FROM teams WHERE league_id=? AND id<>? ORDER BY id",
        (row["league_id"], tid))]
    c = conn.cursor()
    fm_me = ge._team_formation(c, tid)
    conn.close()

    random.seed(31337)
    top_share = collections.Counter()
    n_scorers = collections.Counter()
    tot = 0
    goals_tot = 0
    top_tot = 0
    n_matches = 0
    match_goals = 0
    assists_tot = 0
    subs_tot = 0
    et_n = 0
    g90 = 0
    for i in range(args.n):
        opp = opps[i % len(opps)]
        conn = database.get_conn()
        fm_opp = ge._team_formation(conn.cursor(), opp)
        conn.close()
        try:
            sim = simulate_my_match(tid, opp, fm_me, fm_opp,
                                     extra_time=bool(args.et))
        except Exception as e:
            print("sim 실패", type(e).__name__, e)
            break
        n_matches += 1
        match_goals += sim["home_score"] + sim["away_score"]
        g90 += sim.get("home_score_90", sim["home_score"]) + sim.get("away_score_90", sim["away_score"])
        subs_tot += len(sim.get("home_subs") or []) + len(sim.get("away_subs") or [])
        et_n += 1 if sim.get("went_extra_time") else 0
        for _sd in ("home", "away"):
            assists_tot += sum(int(r.get("assists", 0) or 0)
                               for r in (sim.get(_sd + "_player_ratings") or []) if r)
        for side, score in (("home", sim["home_score"]), ("away", sim["away_score"])):
            if score < 2:
                continue
            lst = [r for r in (sim.get(side + "_player_ratings") or []) if r]
            gs = sorted((int(r.get("goals", 0) or 0) for r in lst), reverse=True)
            if not gs or gs[0] == 0:
                continue
            tot += 1
            goals_tot += score
            top_tot += gs[0]
            top_share[gs[0]] += 1
            n_scorers[sum(1 for g in gs if g > 0)] += 1

    print(f"\n[결과] 팀={row['name']} 총 {n_matches}경기")
    if n_matches:
        print(f"  경기당 총 득점: {match_goals/n_matches:.2f}골 "
              f"(90분만 {g90/n_matches:.2f}골, 도움 {assists_tot/max(1,n_matches):.2f}/경기)")
        print(f"  경기당 교체: {subs_tot/n_matches:.2f}명(양팀 합), 연장 경기 {et_n}/{n_matches}")
    print(f"  2골 이상 낸 팀-경기 {tot}건")
    if tot:
        print(f"  최다득점 슬롯이 가진 골 비중: {top_tot}/{goals_tot} = "
              f"{top_tot*100.0/goals_tot:.1f}%")
        print("  최다득점 슬롯의 골 수 분포:", dict(sorted(top_share.items())))
        print("  경기당 득점자 수 분포:", dict(sorted(n_scorers.items())))


if __name__ == "__main__":
    main()