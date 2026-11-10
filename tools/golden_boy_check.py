# -*- coding: utf-8 -*-
"""[2026-11] 골든보이 새 채점(golden_v2)·코파 트로피(kopa_v1) 저장값 = 재계산값 확인. 시뮬레이션은 돌리지 않는다.

세이브(X.db + X.history.db)를 임시 폴더에 복사해(award_baseline.prepare) 읽기만 하고, 끝나면 임시 폴더를 지운다.
지난 연도 재계산 보정은 award_baseline.patch_for_past_years(은퇴 선수 되살리기 · 그 시즌 리그 · 이적생 상반기 팀)
그대로 쓰고, 여기서는 나이만 그 시즌 값으로 되돌린다 — 후보 조건(20세 이하)이 현역 표의 지금 나이(ai_players.age)를
읽기 때문. 그 시즌 나이 = 현역: 지금 나이 − (마지막 기록 연도 + 1 − 그 시즌) · 은퇴: 은퇴 기록 나이 − (은퇴 연도 − 1 −
그 시즌) — 단 스쿼드 정리로 빠진 선수(은퇴 연도에 시즌 기록이 있음)는 현역 식(은퇴 연도를 마지막 기록 연도로).
(시드 105·106 × 10시즌 실측: 이 규칙으로 저장된 골든보이 200/200 · 코파 트로피 100/100 재현.)
채점은 게임과 같은 함수(game_engine._compute_golden_boy_top10 · _compute_kopa_top10)를 그대로 부른다.

  python tools/golden_boy_check.py --db 경로/headless.db
종료 코드 0 = 두 상의 새 형식 행 전부 일치(순위·선수 동일, 총점 차이 ≤ 저장 반올림 0.00005)."""
import _path  # noqa: F401
import argparse
import contextlib
import io
import os
import shutil
import sys

import database
import game_engine as GE
import award_baseline as AB

TOL = 5e-5


def _age_table(c):
    c.execute("DROP TABLE IF EXISTS temp._gb_age")
    c.execute("CREATE TEMP TABLE _gb_age(id INTEGER PRIMARY KEY, age INTEGER, ref_year INTEGER, retired INTEGER)")
    last = c.execute("SELECT MAX(year) FROM hist.ai_player_season_stats").fetchone()[0]
    c.execute("INSERT INTO temp._gb_age SELECT id, age, ?, 0 FROM ai_players "
              "WHERE id NOT IN (SELECT id FROM ai_players_retired)", (last,))
    # 은퇴 기록은 두 갈래다. 보통 은퇴(_retire_and_replace)는 은퇴 연도 = 마지막 시즌 + 1, 나이 = 마지막 시즌 나이.
    # 스쿼드 정리로 빠진 선수(_archive_forced_out_players)는 은퇴 연도 = 마지막 시즌, 나이 = 나이 +1 뒤 값이라
    # 현역과 같은 식(나이 − (은퇴 연도 + 1 − 그 시즌))을 쓴다. 은퇴 연도에 시즌 기록이 있으면 뒤쪽이다.
    c.execute("""INSERT OR IGNORE INTO temp._gb_age
                 SELECT r.id, r.age, r.retirement_year,
                        CASE WHEN EXISTS (SELECT 1 FROM hist.ai_player_season_stats s
                                          WHERE s.player_id = r.id AND s.year = r.retirement_year) THEN 0 ELSE 1 END
                 FROM ai_players_retired r""")


def _set_ages(c, year):
    c.execute("""UPDATE ai_players SET age = (SELECT g.age - (g.ref_year + CASE g.retired WHEN 1 THEN -1 ELSE 1 END - ?)
                                             FROM temp._gb_age g WHERE g.id = ai_players.id)
                 WHERE id IN (SELECT id FROM temp._gb_age)""", (year,))


def _awards():
    return ((GE._GOLDEN_BOY_AWARD, GE.GOLDEN_ROW_FORMAT, GE._compute_golden_boy_top10),
            (GE._KOPA_AWARD, GE.KOPA_ROW_FORMAT, GE._compute_kopa_top10))


def check(db):
    with contextlib.redirect_stdout(io.StringIO()):
        conn, tmp = AB.prepare(db)
    c = conn.cursor()
    bad = 0
    try:
        AB.patch_for_past_years(c)
        _age_table(c)
        found = False
        for award, fmt, compute in _awards():
            years = [r[0] for r in c.execute(
                "SELECT DISTINCT year FROM season_individual_awards WHERE award_type=? AND xi_formation=? ORDER BY year",
                (award, fmt)).fetchall()]
            if not years:
                print(f"[{award}] {fmt} 행이 없다")
                continue
            found = True
            print(f"[{award}] ({fmt})")
            tot = hit = 0
            for y in years:
                stored = c.execute("SELECT rank, player_id, total_score, xi_formation FROM season_individual_awards "
                                   "WHERE award_type=? AND year=? ORDER BY rank", (award, y)).fetchall()
                _set_ages(c, y)
                AB.clear_year_caches()
                GE._begin_team_gc_run_cache(y)
                try:
                    _sc, top = compute(c, y)
                finally:
                    GE._end_team_gc_run_cache()
                n_ok = 0
                worst = 0.0
                for i, row in enumerate(stored):
                    x = top[i] if i < len(top) else None
                    same = x is not None and x["player_id"] == row[1] and row[3] == fmt
                    d = abs((x["total"] if x else 0.0) - (row[2] or 0.0))
                    worst = max(worst, d)
                    n_ok += bool(same and d <= TOL)
                tot += len(stored)
                hit += n_ok
                ok = n_ok == len(stored) == len(top)
                bad += not ok
                print(f"  {y}: {n_ok}/{len(stored)} 일치 (재계산 {len(top)}명, 총점 최대 차이 {worst:.6f})"
                      + ("" if ok else "  ← 불일치"))
            print(f"  합계 {hit}/{tot} 일치")
        print(f"불일치 연도 {bad}")
        return 0 if (found and bad == 0) else 1
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
    a = ap.parse_args()
    sys.exit(check(os.path.abspath(a.db)))


if __name__ == "__main__":
    main()