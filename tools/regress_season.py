# -*- coding: utf-8 -*-
"""한 시즌(겨울 이적시장 + 연도전환 포함) 전체를 헤드리스로 돌리고
결과 DB 해시 + random 소비량을 찍는다.

_transfer_market만 격리해서 재는 tools/bench_tm.py와 달리, 이건
"이 변경이 시즌 전체 어디에도 영향을 안 줬는가"를 보는 최종 회귀 검증용.
수정 전/후 각각 한 번씩 돌려 해시와 rng_calls가 같으면 순수 성능
리팩터링이 맞다.

사용: python3 tools/regress_season.py [일수(기본 370)]
"""
import _path  # noqa: F401
import hashlib
import os
import random
import shutil
import sys
import time

ROOT = _path.ROOT
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 370

out_dir = os.path.join(ROOT, "qa_runs", "regress_season")
shutil.rmtree(out_dir, ignore_errors=True)
os.makedirs(out_dir)
db_path = os.path.join(out_dir, "headless.db")
shutil.copy(os.path.join(ROOT, "game.db"), db_path)
_h = os.path.join(ROOT, "game.history.db")
for _s in ("", "-wal", "-shm"):
    if os.path.exists(_h + _s):
        shutil.copy(_h + _s, os.path.join(out_dir, "headless.history.db" + _s))

sys.path.insert(0, ROOT)
import database
database.DB_PATH = db_path
os.chdir(out_dir)
database.init_db()
database.flush_to_disk_async = lambda: None

import game_engine as ge
import intl_engine

_rng_n = [0]
for _name in ("random", "randint", "choice", "choices", "uniform", "shuffle", "sample", "gauss"):
    _fn = getattr(random, _name)

    def _mk(f):
        def _w(*a, **kw):
            _rng_n[0] += 1
            return f(*a, **kw)
        return _w
    setattr(random, _name, _mk(_fn))

random.seed(12345)
if not ge.get_player():
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")
cur = ge.get_state().get("current_day") or 1
schedule = [(d, "휴식", {}) for d in range(cur, cur + DAYS + 1)]
remaining = list(schedule)
t0 = time.perf_counter()
for _g in range(40):
    ge.advance_days(remaining)
    pending = intl_engine.get_pending_choice()
    if not pending:
        break
    for opt in pending.get("options", []):
        intl_engine.decline_national_team(opt["tournament_id"])
    cd = ge.get_state().get("current_day")
    remaining = [i for i in schedule if i[0] >= cd]
    if not remaining:
        break
t1 = time.perf_counter()

# [중요] 이 게임은 USE_MEMORY_DB=True — 실제 플레이는 인메모리 DB에서
# 돌고 디스크(headless.db)에는 flush_to_disk로만 반영된다. 위에서
# flush_to_disk_async를 무력화했으므로 디스크 사본을 직접 읽으면
# "한 번도 안 바뀐 원본"을 해싱하게 되어 검증이 통째로 무의미해진다
# (2026-09에 실제로 이 함정에 걸려 한 번 잘못 검증했다).
# 라이브 커넥션(database.get_conn)에서 바로 읽는다.
conn = database.get_conn()
h = hashlib.sha1()
for sql in (
    "SELECT id, team_id, ovr, age, salary, contract_end_year, last_transfer_year, "
    "on_loan_from_team_id, loan_return_year FROM ai_players ORDER BY id",
    "SELECT player_id, year, from_team_id, to_team_id, transfer_type, fee, is_loan, "
    "salary, contract_end_year FROM ai_transfer_log ORDER BY rowid",
    "SELECT id, league_id, current_tier FROM teams ORDER BY id",
    "SELECT id, home_team_id, away_team_id, home_score, away_score FROM match_results ORDER BY id",
):
    for r in conn.execute(sql).fetchall():
        h.update(repr(tuple(r)).encode())
print(f"[REGRESS] {DAYS}일 진행 {t1-t0:.1f}s | hash={h.hexdigest()[:24]} | rng_calls={_rng_n[0]}")