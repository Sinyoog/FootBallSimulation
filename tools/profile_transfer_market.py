# -*- coding: utf-8 -*-
"""이적시장(_transfer_market) 구간만 cProfile로 잰다.

전체 370일을 cProfile로 감싸면 2.5배 부풀려진 프로파일에서 이적시장이
묻힌다. 여기서는 _transfer_market 진입/퇴출 시점에만 프로파일러를
켰다 끄므로, 나머지 구간은 정상 속도로 돌고 이적루프 안의 함수별
tottime만 정확히 뽑힌다. game.db/game.history.db(-wal/-shm 포함)는
qa_runs/ 아래 사본으로만 쓴다.
"""
import _path  # noqa: F401
import cProfile, os, pstats, random, shutil, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 370
OUT = os.path.join(ROOT, "profile_tm.prof")

out_dir = os.path.abspath(os.path.join(ROOT, "qa_runs", "prof_tm"))
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
import ai_lifecycle

pr = cProfile.Profile()
_orig = ai_lifecycle._transfer_market


def _wrapped(*a, **kw):
    pr.enable()
    try:
        return _orig(*a, **kw)
    finally:
        pr.disable()


ai_lifecycle._transfer_market = _wrapped

random.seed(12345)
if not ge.get_player():
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")
cur = ge.get_state().get("current_day") or 1
schedule = [(d, "휴식", {}) for d in range(cur, cur + DAYS + 1)]
remaining = list(schedule)
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

pstats.Stats(pr).dump_stats(OUT)
os.chdir(ROOT)
s = pstats.Stats(OUT)
s.sort_stats("tottime").print_stats(28)