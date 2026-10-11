# -*- coding: utf-8 -*-
"""nt_bf_paired_check.py — 감독 시장 ② S5 판정 도구 (2026-10 신설)

같은 세이브 상태에서 B~F 대표팀 공석 선임 결과를 수정안 OFF/ON으로 비교한다.
B~F 나라 하나하나를 "지금 공석"으로 가정하고 national_manager._fill_vacancy를 부른 뒤,
선임된 감독(id·정식/임시)을 기록하고 SAVEPOINT로 되돌린다 — 나라끼리 서로 영향 없음.
세이브 파일은 건드리지 않는다(인메모리 DB만 쓰고 디스크에 저장하지 않음).

사용:
  python tools/nt_bf_paired_check.py <save_dir> --on NAME=VALUE [NAME=VALUE ...]
    <save_dir>: game.db(+game.history.db)가 있는 폴더 — 임시 폴더로 복사해서 연다.
    --on: ON 쪽에서 constants에 넣을 값(파이썬 리터럴). OFF 쪽은 constants 그대로.
판정: 모든 B~F 나라의 선임 결과가 OFF/ON 100% 같으면 통과.
"""
import _path  # noqa: F401
import argparse
import ast
import os
import random
import shutil
import sys
import tempfile

ROOT = _path.ROOT


def _open(src_dir):
    tmp = tempfile.mkdtemp(prefix="ntbf_")
    for f in ("game.db", "game.history.db", "game.history.db-wal", "game.history.db-shm"):
        s = os.path.join(src_dir, f)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(tmp, f))
    import database
    database.DB_PATH = os.path.join(tmp, "game.db")
    os.chdir(tmp)
    database.flush_to_disk_async = lambda: None
    database.reset_conn_pool()
    database.init_db()
    return database, tmp


def _run_pass(database, overrides):
    import constants as C
    import national_manager as nm
    import manager_ability
    saved = {k: getattr(C, k) for k in overrides}
    for k, v in overrides.items():
        setattr(C, k, v)
    try:
        conn = database.get_conn()
        import game_engine as ge
        year = int(ge.get_state().get("current_year"))
        salt = nm._world_salt()
        job_ab = manager_ability.JobAbility(conn)
        conn.commit()
        rows = conn.execute("SELECT id, name FROM countries ORDER BY id").fetchall()
        out = {}
        for r in rows:
            g = C.get_country_grade(r["name"])
            if g in ("S", "A"):
                continue
            n0 = conn.execute("SELECT COUNT(*) FROM national_team_managers").fetchone()[0]
            conn.execute("SAVEPOINT ntbf")
            random.seed(0)          # _fill_vacancy는 자체 난수(vr)만 쓰지만 혹시 모를 전역 난수 고정
            stats = {}
            nm._fill_vacancy(conn, r["id"], year, salt, stats, job_ab=job_ab)
            got = conn.execute(
                """SELECT manager_id, role FROM national_team_managers
                   WHERE country_id=? AND start_year=? ORDER BY id DESC LIMIT 1""",
                (r["id"], year)).fetchone()
            out[r["name"]] = (g, (int(got[0]), got[1]) if got else None)
            conn.execute("ROLLBACK TO ntbf")
            conn.execute("RELEASE ntbf")
            n1 = conn.execute("SELECT COUNT(*) FROM national_team_managers").fetchone()[0]
            if n1 != n0:
                raise RuntimeError(f"되돌리기 실패({r['name']}): {n0} → {n1} — 중간 commit 의심")
        return year, out
    finally:
        for k, v in saved.items():
            setattr(C, k, v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("save_dir")
    ap.add_argument("--on", nargs="+", required=True)
    a = ap.parse_args()
    over = {}
    for kv in a.on:
        k, v = kv.split("=", 1)
        over[k] = ast.literal_eval(v)
    src = os.path.abspath(a.save_dir)
    database, tmp = _open(src)
    try:
        y, off = _run_pass(database, {})
        y2, on = _run_pass(database, over)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    diff = [(c, off[c], on.get(c)) for c in off if off[c] != on.get(c)]
    from collections import Counter
    print(f"[S5] {src} — {y}년 상태, B~F {len(off)}개국, ON={over}")
    print(f"[S5] 등급별 나라 수: {dict(sorted(Counter(v[0] for v in off.values()).items()))}")
    print(f"[S5] 선임 결과 다른 나라: {len(diff)}  → {'통과' if not diff else '실패'}")
    for c, o, n in diff[:20]:
        print(f"    {c}: OFF {o} / ON {n}")
    sys.exit(0 if not diff else 1)


if __name__ == "__main__":
    main()