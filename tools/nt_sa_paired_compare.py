# -*- coding: utf-8 -*-
"""nt_sa_paired_compare.py — 감독 시장 ② 수정안 짝 비교 (2026-10 신설)

같은 세이브 상태에서 S/A 대표팀 하나하나를 "지금 공석"으로 가정하고, 수정안(모드)마다
national_manager._fill_vacancy를 같은 잡음 시드 N개로 불러 선임 결과를 비교한다.
매번 SAVEPOINT로 되돌린다 — 나라·반복끼리 서로 영향 없음, 세이브 파일은 건드리지 않는다.
(⑤-4-② "2005년 상태 짝 비교"와 같은 방식 — 장기런 전에 수정안을 고르는 단계.)

사용:
  python tools/nt_sa_paired_compare.py <save_dir> [--reps 30] [--modes none walk precap]
지표(나라 × 반복 합산):
  P1  선임 감독이 팀 OVR 세계 50위 안 클럽 현직
  P2  P1 중 그 클럽 재임 2년 미만(게임 시작 배정 재임 제외)
  ab  정식 선임 평균 능력 · frn 외국인 비율 · club 현직 비율 · ct 임시 감독 비율
"""
import _path  # noqa: F401
import argparse
import os
import random
import shutil
import statistics
import tempfile
from collections import defaultdict

from nt_bf_paired_check import _open


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("save_dir")
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--modes", nargs="+", default=["none", "walk", "precap"])
    a = ap.parse_args()
    src = os.path.abspath(a.save_dir)
    database, tmp = _open(src)
    try:
        import constants as C
        import national_manager as nm
        import manager_ability
        import game_engine as ge
        conn = database.get_conn()
        year = int(ge.get_state().get("current_year"))
        salt = nm._world_salt()
        job_ab = manager_ability.JobAbility(conn)
        conn.commit()
        rank = nm._nt_obs_team_rank(conn, year)
        y_start = conn.execute("SELECT MIN(start_year) FROM team_managers").fetchone()[0]
        cur = {}
        for r in conn.execute("""SELECT manager_id, team_id, start_year FROM team_managers
                                 WHERE end_year IS NULL AND job_kind='club'"""):
            cur[int(r[0])] = (int(r[1]), int(r[2] or year))
        ab = {int(r[0]): (int(r[1] or 0), r[2] or "") for r in conn.execute(
            "SELECT id, manager_ability, nationality FROM managers")}
        countries = [(r["id"], r["name"]) for r in conn.execute("SELECT id, name FROM countries ORDER BY id")
                     if C.get_country_grade(r["name"]) in ("S", "A")]
        res = {}
        old_mode = C.MANAGER_NT_CLUB_ACCEPT_MODE
        for mode in a.modes:
            C.MANAGER_NT_CLUB_ACCEPT_MODE = None if mode == "none" else mode
            acc = defaultdict(list)
            for cid, cname in countries:
                g = C.get_country_grade(cname)
                for k in range(a.reps):
                    conn.execute("SAVEPOINT ntsa")
                    random.seed(k)
                    nm._fill_vacancy(conn, cid, year, f"{salt}:{k}", {}, job_ab=job_ab)
                    got = conn.execute(
                        """SELECT manager_id, role FROM national_team_managers
                           WHERE country_id=? AND start_year=? ORDER BY id DESC LIMIT 1""",
                        (cid, year)).fetchone()
                    conn.execute("ROLLBACK TO ntsa")
                    conn.execute("RELEASE ntsa")
                    if not got:
                        continue
                    mid, role = int(got[0]), got[1]
                    acc[g + ":ct"].append(1.0 if role == "caretaker" else 0.0)
                    if role != "full":
                        continue
                    a_, nat = ab.get(mid, (0, ""))
                    c = cur.get(mid)
                    is_club = c is not None
                    p1 = is_club and (rank.get(c[0]) or 9999) <= 50
                    p2 = p1 and (year - c[1]) < 2 and c[1] != y_start
                    for key in (g, "SA"):
                        acc[key + ":ab"].append(a_)
                        acc[key + ":frn"].append(100.0 if nat != cname else 0.0)
                        acc[key + ":club"].append(100.0 if is_club else 0.0)
                        acc[key + ":p1"].append(100.0 if p1 else 0.0)
                        acc[key + ":p2"].append(100.0 if p2 else 0.0)
                    acc["SA:ct"].append(0.0)
            res[mode] = acc
        C.MANAGER_NT_CLUB_ACCEPT_MODE = old_mode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    m = lambda xs: round(statistics.mean(xs), 2) if xs else None
    print(f"[S/A 짝 비교] {src} — {year}년 상태, S/A {len(countries)}개국 × {a.reps}회")
    for grp in ("SA", "S", "A"):
        print(f"  [{grp}]")
        for mode in a.modes:
            d = res[mode]
            print(f"    {mode:<7} P1 {m(d[grp + ':p1'])}% · P2 {m(d[grp + ':p2'])}% · 능력 {m(d[grp + ':ab'])} · "
                  f"외국인 {m(d[grp + ':frn'])}% · 현직 {m(d[grp + ':club'])}% · "
                  f"임시 {m(d.get(grp + ':ct', []) if grp != 'SA' else d['S:ct'] + d['A:ct'])}%")


if __name__ == "__main__":
    main()