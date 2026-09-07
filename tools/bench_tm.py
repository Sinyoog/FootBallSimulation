# -*- coding: utf-8 -*-
"""_transfer_market(오프시즌 경로)만 격리해서 wall-clock + 결과 해시를 낸다.

[2026-09 신설] 이적시장 2차 최적화용. 전체 370일 헤드리스 런은 4분+가 걸려
before/after 반복 비교에 못 쓴다. 여기서는 실제 game.db 사본에서
ai_players 스냅샷만 떠서 _transfer_market을 직접 1회 호출하고,
  · 이적루프 구간 wall-clock (PERF-TM 로그와 동일한 구간)
  · 결과 해시 (transfer_updates / salary_loan / transfer_log 전량)
  · random 호출 횟수
를 찍는다. DB는 사본이고 커밋하지 않는다(rollback).

사용: python3 tools/bench_tm.py [반복횟수]
"""
import _path  # noqa: F401
import hashlib
import os
import random
import shutil
import sys
import time

ROOT = _path.ROOT
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

out_dir = os.path.join(ROOT, "qa_runs", "bench_tm")
shutil.rmtree(out_dir, ignore_errors=True)
os.makedirs(out_dir)
db_path = os.path.join(out_dir, "headless.db")
shutil.copy(os.path.join(ROOT, "game.db"), db_path)
_h = os.path.join(ROOT, "game.history.db")
for _s in ("", "-wal", "-shm"):
    if os.path.exists(_h + _s):
        shutil.copy(_h + _s, os.path.join(out_dir, "headless.history.db" + _s))

import database
database.DB_PATH = db_path
os.chdir(out_dir)
database.init_db()
database.flush_to_disk_async = lambda: None

import ai_lifecycle
import economy as _economy


class _RngCounter:
    """random 모듈 호출 횟수 계측(순수 성능 리팩터링이면 이 값이 같아야 한다)."""

    def __init__(self):
        self.n = 0
        self._orig = {}

    def __enter__(self):
        for name in ("random", "randint", "choice", "choices", "uniform", "shuffle", "sample"):
            fn = getattr(random, name)
            self._orig[name] = fn

            def _mk(f):
                def _w(*a, **kw):
                    self.n += 1
                    return f(*a, **kw)
                return _w
            setattr(random, name, _mk(fn))
        return self

    def __exit__(self, *a):
        for name, fn in self._orig.items():
            setattr(random, name, fn)


def run_once():
    conn = database.get_conn() if hasattr(database, "get_conn") else None
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    year = c.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()[0]
    rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, "
        "contract_end_year, last_transfer_year FROM ai_players ORDER BY id").fetchall()

    # 실게임과 동일하게 verbose_log를 켠 상태로 잰다 —
    # _estimate_ai_transfer_fee_display가 이적 건마다 도는 경로가 포함된다.
    _logs = []

    def _vlog(*a, **kw):
        _logs.append(a)

    random.seed(12345)
    cnt = _RngCounter()
    t0 = time.perf_counter()
    with cnt:
        _economy.begin_fee_batch()
        try:
            ai_lifecycle._transfer_market(c, year, rows, verbose_log=_vlog, my_team_id=None)
        finally:
            _economy.end_fee_batch()
    t1 = time.perf_counter()

    h = hashlib.sha1()
    for r in c.execute("SELECT id, team_id, contract_end_year, last_transfer_year, "
                       "salary, on_loan_from_team_id, loan_return_year "
                       "FROM ai_players ORDER BY id"):
        h.update(repr(tuple(r)).encode())
    for r in c.execute("SELECT player_id, from_team_id, to_team_id, transfer_type, fee, "
                       "is_loan, loan_return_year, salary, contract_end_year "
                       "FROM ai_transfer_log WHERE year=? ORDER BY rowid", (year,)):
        h.update(repr(tuple(r)).encode())
    conn.rollback()
    conn.close()
    return t1 - t0, h.hexdigest()[:24], cnt.n


times = []
res = None
for i in range(N):
    dt, digest, rng = run_once()
    times.append(dt)
    print(f"  run {i+1}: {dt:.3f}s  hash={digest}  rng_calls={rng}", flush=True)
    res = (digest, rng)
print(f"[BENCH-TM] min={min(times):.3f}s  med={sorted(times)[len(times)//2]:.3f}s  "
      f"hash={res[0]}  rng={res[1]}")