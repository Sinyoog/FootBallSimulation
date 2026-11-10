# -*- coding: utf-8 -*-
"""[2026-11] 세계 베스트 11(FIFA·대륙별, best_xi_award) 단위 테스트.

A. 세이브 없이(항상 실행)
  A1 선정 함수 기본값 불변 — _select_best_xi에 slot_value를 안 주면, 예전 식(점수, 제자리 아니면 × 0.9)을 slot_value로
     직접 준 것과 포메이션·명단·자리가 같다(리그·컵·대회 베스트 11은 그대로)
  A2 자리 기준 값 — 제자리면 그 자리 종류 기준 정규화, 다른 자리면 min(자리 기준, 자기 포지션 기준) − 10% 감점
  A3 채점은 입력을 바꾸지 않고, 입력 순서를 섞어도 점수가 같다
  A4 K = 5 — OVR이 같으면 활약으로 최대 5까지 갈리고, OVR(+평점) 6 차이는 활약 만점으로도 못 뒤집는다
  A5 출전 50% 미만은 후보에서 빠진다
  A6 기준이 낮은 자리로 옮겨 점수가 부풀지 않는다 — 풀백 기준이 낮아도 풀백이 CB 자리에서 CB 기준보다 높게 평가되지 않는다
B. 세이브로(--db) — 복사본에서만, 끝나면 지운다
  B1 한 번 채점 — _compute_world_best_xi 두 번 = 같은 결과, 대륙별 명단 = 같은 점수로 그 대륙 국적만 걸러 따로 뽑은 결과
     (FIFA를 먼저 뽑든 대륙을 먼저 뽑든 같음)
  B2 대륙별 명단 국적 = 그 대륙(오세아니아는 아시아, 북중미는 북미), 전원 리그 출전 50% 이상, 같은 선수 중복 수상 허용
  B3 포메이션을 채운 상은 11명, 자리마다 _best_xi_allowed 안
  B4 후보 줄이기(_world_xi_prefilter) — 줄여 계산한 결과 = 1부 전체로 계산한 결과(명단·자리·포메이션·총점 완전 동일), 시간 비교
  python tools/test_best_xi.py [--db 경로/headless.db]"""
import _path  # noqa: F401
import argparse
import contextlib
import copy
import io
import os
import random
import shutil
import sys

with contextlib.redirect_stdout(io.StringIO()):
    import game_engine as GE
import best_xi_award as BX

FAILS = []


def ok(cond, msg):
    print(("  통과 " if cond else "  실패 ") + msg)
    if not cond:
        FAILS.append(msg)


POS = ["GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM", "LM", "RM", "LW", "RW", "ST", "CF"]


def _ent(pid, pos, ovr, share=0.9, g=5, a=3, rank=5, lvl=0.95, tgc=1.1, intl=0.2, gk=None):
    return dict(player_id=pid, team_id=100 + pid % 17, position=pos, ovr=ovr, share=share, goals=g, assists=a, matches=30,
                full_season=38, goal_env=1.0, team_attack=1.0, team_gc=tgc, league_level=lvl, league_rank=rank,
                league_n=20, relegated=False, league_exp=6, cup_v=0.2, cont_v=0.3, intl_v=intl,
                gk_save=(gk if gk is not None else (0.78 if pos == "GK" else None)), comps=[], nationality="X")


def _pool(n=400, seed=3):
    rnd = random.Random(seed)
    return [_ent(i + 1, rnd.choice(POS), rnd.randint(70, 99), share=rnd.uniform(0.3, 1.0), g=rnd.randint(0, 25),
                 a=rnd.randint(0, 14), rank=rnd.randint(1, 20), lvl=rnd.uniform(0.5, 1.0), tgc=rnd.uniform(0.7, 2.0),
                 intl=rnd.random() * 0.6, gk=rnd.uniform(0.70, 0.84)) for i in range(n)]


def part_a():
    print("A1 선정 함수 기본값 불변")
    rnd = random.Random(5)
    same = n = 0
    for t in range(40):
        cands = [{"player_id": i, "position": rnd.choice(POS), "s": rnd.uniform(-2, 10)} for i in range(rnd.randint(15, 60))]
        cands.sort(key=lambda x: (-x["s"], x["player_id"]))
        sc = lambda x: x["s"]   # noqa: E731
        old = lambda x, s, p: sc(x) if p == s else sc(x) - GE._BEST_XI_ADJ_PENALTY * abs(sc(x))   # noqa: E731
        f1, p1 = GE._select_best_xi(cands, sc, pos_of=lambda x: x["position"])
        f2, p2 = GE._select_best_xi(cands, sc, pos_of=lambda x: x["position"], slot_value=old)
        n += 1
        same += f1 == f2 and [(s, x["player_id"]) for s, x in p1] == [(s, x["player_id"]) for s, x in p2]
    ok(same == n, f"무작위 후보 {n}묶음 — 기본값 = 예전 식을 직접 준 결과 ({same}/{n})")

    print("A2 자리 기준 값")
    st = {"CB": (100.0, 2.0), "FB": (97.0, 1.0), "W": (98.0, 1.5), "WM": (96.0, 2.0)}
    ok(abs(BX.slot_value(102.0, "CB", "CB", st) - 1.0) < 1e-12, "CB가 CB 자리: (102 − 100) / 2 = 1.0")
    v = BX.slot_value(99.0, "LB", "CB", st)
    want = min((99 - 100) / 2, (99 - 97) / 1) * 1.0
    want = want - 0.10 * abs(want)
    ok(abs(v - want) < 1e-12, f"LB가 CB 자리: min(CB 기준 −0.5, 풀백 기준 2.0) − 10% = {v:.3f}")
    v2 = BX.slot_value(101.0, "LW", "LM", st)
    want2 = min((101 - 96) / 2, (101 - 98) / 1.5)
    want2 -= 0.10 * abs(want2)
    ok(abs(v2 - want2) < 1e-12, f"윙어가 측면 MF 자리: min(측면 MF 기준 2.5, 윙어 기준 2.0) − 10% = {v2:.3f}")

    print("A3 입력 불변·순서 무관")
    pool = _pool()
    before = copy.deepcopy(pool)
    rat = {e["player_id"]: 6.0 + (e["ovr"] - 60) / 35.0 + 0.02 * e["goals"] for e in pool}
    p1, s1, st1 = BX.score_pool(pool, rat)
    ok(pool == before, "score_pool 후 입력 그대로")
    sh = list(pool)
    random.Random(9).shuffle(sh)
    p2, s2, st2 = BX.score_pool(sh, rat)
    ok(all(abs(s1[k]["total"] - s2[k]["total"]) < 1e-12 for k in s1) and st1 == st2, "입력 순서를 섞어도 점수·자리 기준 같음")

    print("A4 K = 5")
    base = _pool(300, 4)
    a = _ent(9001, "ST", 90, g=0, a=0, rank=20, intl=0.0, share=0.5, tgc=2.2, lvl=0.5)
    b = _ent(9002, "ST", 90, g=40, a=20, rank=1, intl=1.0, share=1.0, tgc=0.6, lvl=1.0)
    c = _ent(9003, "ST", 96, g=0, a=0, rank=20, intl=0.0, share=0.5, tgc=2.2, lvl=0.5)
    rt = {9001: 6.0 + 30 / 35, 9002: 6.0 + 30 / 35, 9003: 6.0 + 36 / 35}
    _p, sc, _st = BX.score_pool(base + [a, b, c], rt)
    d_same = sc[9002]["total"] - sc[9001]["total"]
    ok(0 < d_same <= BX.K + 1e-9, f"같은 OVR 90: 활약 최저 vs 최고 차이 {d_same:.2f} (≤ K {BX.K})")
    ok(sc[9003]["total"] > sc[9002]["total"], f"OVR 96 활약 최저 {sc[9003]['total']:.2f} > OVR 90 활약 최고 {sc[9002]['total']:.2f}")

    print("A5 출전 50% 미만 제외")
    p, _s, _t = BX.score_pool([_ent(1, "CB", 90, share=0.49), _ent(2, "CB", 90, share=0.50)], {})
    ok([e["player_id"] for e in p] == [2], "출전 49% 제외 · 50% 포함")

    print("A6 기준 낮은 자리로 옮겨 부풀지 않음")
    st = {"CB": (100.0, 1.0), "FB": (95.0, 1.0)}
    ok(BX.slot_value(99.0, "LB", "CB", st) <= BX.slot_value(99.0, "CB", "CB", st),
       "같은 총점이면 LB가 CB 자리에서 받는 값 ≤ CB가 받는 값(풀백 기준이 5 낮아도)")


def part_b(db):
    import database
    import award_baseline as AB
    with contextlib.redirect_stdout(io.StringIO()):
        conn, tmp = AB.prepare(db)
    c = conn.cursor()
    try:
        AB.patch_for_past_years(c)
        y = c.execute("SELECT MAX(year) FROM season_individual_awards WHERE award_type='발롱도르'").fetchone()[0]
        AB.clear_year_caches(); GE._begin_team_gc_run_cache(y)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                r1 = GE._compute_world_best_xi(c, y)
                r2 = GE._compute_world_best_xi(c, y)
                cands = GE._get_ballon_candidates(c, y, any_league=True)
                ents = GE._ballon_inputs(c, y, cands)
        finally:
            GE._end_team_gc_run_cache()
        ids = lambda r: {k: (v[0], [(s, e["player_id"]) for s, e in v[1]]) for k, v in r.items()}   # noqa: E731
        print(f"B1 한 번 채점 ({y}년)")
        ok(ids(r1) == ids(r2), "두 번 계산 = 같은 결과")
        pool, sc, stats = BX.score_pool(ents, {x["player_id"]: x.get("rating") for x in cands})
        nat = GE._nationality_continent_map(c)
        tot = lambda e: sc[e["player_id"]]["total"]   # noqa: E731
        sv = lambda e, s, p: BX.slot_value(tot(e), p, s, stats)   # noqa: E731
        sep = {}
        for aw in reversed(list(r1)):   # 대륙 먼저, FIFA 마지막
            P = pool if aw == GE._FIFA_BEST_XI_AWARD else [e for e in pool if GE._CONTINENT_BEST_XI_AWARDS.get(nat.get(e.get("nationality"))) == aw]
            order = sorted(P, key=lambda e: (-tot(e), e["player_id"]))
            f, pk = GE._select_best_xi(order, tot, pos_of=lambda e: e.get("position") or "", slot_value=sv)
            sep[aw] = (f, [(s, e["player_id"]) for s, e in pk])
        ok(all(sep[aw] == ids(r1)[aw] for aw in r1 if r1[aw][0]), "대륙 먼저·FIFA 마지막으로 따로 뽑아도 같은 명단")
        print("B2 대륙·출전·중복 수상")
        okn = all(GE._CONTINENT_BEST_XI_AWARDS.get(nat.get(e.get("nationality"))) == aw
                  for aw, (_f, pk, _s) in r1.items() if aw != GE._FIFA_BEST_XI_AWARD for _sl, e in pk)
        ok(okn, "대륙별 명단 전원 국적 대륙 = 상의 대륙")
        ok(all((e.get("share") or 0) >= 0.5 for _aw, (_f, pk, _s) in r1.items() for _sl, e in pk), "전원 리그 출전 50% 이상")
        fifa = {e["player_id"] for _s, e in r1[GE._FIFA_BEST_XI_AWARD][1]}
        dup = sum(1 for aw, (_f, pk, _s) in r1.items() if aw != GE._FIFA_BEST_XI_AWARD for _sl, e in pk if e["player_id"] in fifa)
        ok(dup > 0, f"FIFA·대륙 동시 수상 {dup}명(중복 허용)")
        print("B3 명단 모양")
        good = all(len(pk) == 11 and all(s in GE._best_xi_allowed(e.get("position")) for s, e in pk)
                   for _aw, (f, pk, _s) in r1.items() if f)
        ok(good, "포메이션을 채운 상: 11명, 자리마다 설 수 있는 포지션: " + ", ".join(f"{aw} {f}" for aw, (f, _p, _s) in r1.items()))
        print("B4 후보 줄이기 = 전체 계산")
        import time
        for yy in sorted({y, c.execute("SELECT MIN(year) FROM season_individual_awards WHERE award_type='발롱도르'").fetchone()[0]}):
            AB.clear_year_caches(); GE._begin_team_gc_run_cache(yy)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    t0 = time.time(); fast = GE._compute_world_best_xi(c, yy); t1 = time.time()
                    full = GE._compute_world_best_xi(c, yy, prefilter=False); t2 = time.time()
                    n_all = len(GE._get_ballon_candidates(c, yy, any_league=True))
                    n_kept = len(GE._world_xi_prefilter(c, yy, GE._get_ballon_candidates(c, yy, any_league=True),
                                                        GE._nationality_continent_map(c)))
            finally:
                GE._end_team_gc_run_cache()
            same = all(fast[aw][0] == full[aw][0]
                       and [(s_, e["player_id"]) for s_, e in fast[aw][1]] == [(s_, e["player_id"]) for s_, e in full[aw][1]]
                       and all(fast[aw][2][e["player_id"]] == full[aw][2][e["player_id"]] for _s, e in full[aw][1])
                       for aw in full) and set(fast) == set(full)
            ok(same, f"{yy}년: 6개 상 명단·자리·포메이션·총점 완전 동일 · 입력 계산 {n_kept}/{n_all}명 · {t1 - t0:.1f}초 (전체 {t2 - t1:.1f}초)")
    finally:
        os.chdir(_path.ROOT)
        try:
            database.reset_conn_pool()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    a = ap.parse_args()
    part_a()
    if a.db:
        part_b(os.path.abspath(a.db))
    print("\n결과: " + ("전부 통과" if not FAILS else f"실패 {len(FAILS)}개"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()