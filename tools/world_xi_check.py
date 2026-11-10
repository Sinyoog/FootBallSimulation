# -*- coding: utf-8 -*-
"""[2026-11] 세계 베스트 11(FIFA·대륙별, best_xi_award) 저장값 = 재계산값 확인. 시뮬레이션은 돌리지 않는다.

세이브(X.db + X.history.db)를 임시 폴더에 복사해(award_baseline.prepare) 읽기만 하고, 끝나면 지운다. 지난 연도 보정은
award_baseline.patch_for_past_years 그대로. 재계산은 게임과 같은 함수(_compute_world_best_xi)를 그대로 부른다.
[알려진 한계] 지난 연도 재계산은 은퇴 기록을 되살려 후보를 만든다 — 게임 중 후보와 몇 명 달라지면 척도 분포(p10·p99.5)와
자리 기준(상위 50명)이 조금 움직여 총점이 미세하게 달라진다(발롱도르 재계산과 같은 한계, 2026-11 시드 106 3시즌: 18개 상
명단·자리·포메이션 전부 같음, 총점 최대 차이 0.0094). 그래서 총점은 SCORE_TOL까지 허용하고, 같은 자리 종류 안의 순서
(예: 투톱 ST 두 명의 순서)는 보지 않는다.

  python tools/world_xi_check.py --db 경로/headless.db [--from-year 연도]
--from-year: 그 해부터만 본다(이 채점으로 바뀌기 전 해의 저장값은 예전 점수라 다르다).
종료 코드 0 = 본 해 전부 일치(상마다 명단·자리·포메이션 같음, 총점 차이 ≤ SCORE_TOL)."""
import _path  # noqa: F401
import argparse
import collections
import contextlib
import io
import os
import shutil
import sys

import database
import game_engine as GE
import award_baseline as AB

SCORE_TOL = 0.05     # 총점(OVR 단위, 약 100) 허용 차이 — 위 [알려진 한계]


def check(db, from_year=None):
    with contextlib.redirect_stdout(io.StringIO()):
        conn, tmp = AB.prepare(db)
    c = conn.cursor()
    bad = 0
    try:
        AB.patch_for_past_years(c)
        years = [r[0] for r in c.execute("SELECT DISTINCT year FROM season_individual_awards WHERE award_type=? ORDER BY year",
                                         (GE._FIFA_BEST_XI_AWARD,)).fetchall() if from_year is None or r[0] >= from_year]
        if not years:
            print("세계 베스트 11 행이 없다")
            return 1
        tot = hit = 0
        for y in years:
            AB.clear_year_caches()
            GE._begin_team_gc_run_cache(y)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    res = GE._compute_world_best_xi(c, y)
            finally:
                GE._end_team_gc_run_cache()
            msgs = []
            n_aw = n_ok = 0
            worst = 0.0
            for aw, (form, picks, sc) in res.items():
                stored = c.execute("SELECT rank, player_id, total_score, xi_formation, xi_slot FROM season_individual_awards "
                                   "WHERE year=? AND award_type=? ORDER BY rank", (y, aw)).fetchall()
                want = [(e["player_id"], s if form else None) for s, e in picks]
                st_tot = {r[1]: r[2] or 0.0 for r in stored}
                diff = max((abs(st_tot.get(e["player_id"], 0.0) - sc[e["player_id"]]["total"]) for _s, e in picks), default=0.0)
                worst = max(worst, diff)
                same = (collections.Counter((r[1], r[4]) for r in stored) == collections.Counter(want)
                        and all(r[3] == (form if form else None) for r in stored) and diff <= SCORE_TOL)
                n_aw += 1
                n_ok += same
                if not same:
                    msgs.append(f"{aw}: 저장 {[r[1] for r in stored]} / 재계산 {[w[0] for w in want]}")
            tot += n_aw
            hit += n_ok
            bad += n_ok != n_aw
            print(f"  {y}: {n_ok}/{n_aw} 상 일치 (총점 최대 차이 {worst:.4f})" + ("" if n_ok == n_aw else "  ← 불일치 " + "; ".join(msgs)))
        print(f"합계 {hit}/{tot} 상 일치 · 불일치 연도 {bad}")
        return 0 if bad == 0 else 1
    finally:
        os.chdir(_path.ROOT)
        try:
            database.reset_conn_pool()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--from-year", type=int)
    a = ap.parse_args()
    sys.exit(check(os.path.abspath(a.db), a.from_year))


if __name__ == "__main__":
    main()