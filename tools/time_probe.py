# -*- coding: utf-8 -*-
"""연도전환(52주차→1주차) 구간 소요시간 실측 러너.

사용: python3 time_probe.py <run_tag> [days]

- game.db/game.history.db를 qa_runs/time_<tag>/ 로 복사해 그 사본에서만 실행.
- live_sim.log의 "[PERF] 연도전환 총 X.XXs" 줄을 그대로 뽑아 쓴다
  (게임이 이미 갖고 있는 계측이라 별도 오버헤드가 없다).
- 이번 재현성 수정으로 sorted()가 추가된 두 함수
  (_prestige_scouting / update_country_b_for_year)는 호출 횟수가 적어
  perf_counter 래핑 오버헤드가 무시할 수준이므로 개별 시간도 같이 잰다.
- 측정 노이즈가 크므로 이 스크립트는 1회분만 찍고, 반복/최소값 집계는
  호출부(run_timing.sh)에서 한다.
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import json
import os
import random
import shutil
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tag = sys.argv[1]
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 370

out_dir = os.path.abspath(os.path.join(SCRIPT_DIR, "qa_runs", f"time_{tag}"))
if os.path.exists(out_dir):
    shutil.rmtree(out_dir)
os.makedirs(out_dir)
db_path = os.path.join(out_dir, "headless.db")
shutil.copy(os.path.join(SCRIPT_DIR, "game.db"), db_path)
# [중요] history DB는 WAL 모드라 -wal/-shm 을 같이 복사하지 않으면
# 아직 체크포인트되지 않은 커밋분(실측 50MB, 3만4천행)이 통째로 빠진
# 사본으로 실행하게 된다 — 실행끼리는 일관되므로 재현성 판정 자체는
# 유지되지만, "실제 세이브와 같은 입력"이 아니게 된다.
_hist_src = os.path.join(SCRIPT_DIR, "game.history.db")
if os.path.exists(_hist_src):
    for _sfx in ("", "-wal", "-shm"):
        if os.path.exists(_hist_src + _sfx):
            shutil.copy(_hist_src + _sfx,
                        os.path.join(out_dir, "headless.history.db" + _sfx))

log_path = os.path.join(SCRIPT_DIR, "live_sim.log")
open(log_path, "w").close()   # 이번 실행분만 남기도록 비운다

sys.path.insert(0, SCRIPT_DIR)
import database

database.DB_PATH = db_path
os.chdir(out_dir)
database.init_db()
database.flush_to_disk_async = lambda: None

import game_engine as ge
import intl_engine
import ai_lifecycle
import power_ranking

marks = {"prestige_scouting": 0.0, "update_country_b": 0.0}


def _timed(mod, name, key):
    orig = getattr(mod, name)

    def w(*a, **kw):
        t = time.perf_counter()
        try:
            return orig(*a, **kw)
        finally:
            marks[key] += time.perf_counter() - t
    setattr(mod, name, w)


_timed(ai_lifecycle, "_prestige_scouting", "prestige_scouting")
_timed(power_ranking, "update_country_b_for_year", "update_country_b")

random.seed(12345)
if not ge.get_player():
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")

cur_day = ge.get_state().get("current_day") or 1
schedule = [(d, "휴식", {}) for d in range(cur_day, cur_day + DAYS + 1)]

t0 = time.perf_counter()
remaining = list(schedule)
for _guard in range(40):
    ge.advance_days(remaining)
    pending = intl_engine.get_pending_choice()
    if not pending:
        break
    for opt in pending.get("options", []):
        intl_engine.decline_national_team(opt["tournament_id"])
    cd = ge.get_state().get("current_day")
    remaining = [item for item in schedule if item[0] >= cd]
    if not remaining:
        break
total = time.perf_counter() - t0

transition = None
for line in open(log_path, encoding="utf-8", errors="replace"):
    if "[PERF] 연도전환 총" in line:
        transition = float(line.split("연도전환 총")[1].split("s")[0].strip())
print(json.dumps({"tag": tag, "total_370d": round(total, 3),
                  "transition": transition,
                  "prestige_scouting": round(marks["prestige_scouting"], 4),
                  "update_country_b": round(marks["update_country_b"], 4)}))
os.chdir(SCRIPT_DIR)
shutil.rmtree(out_dir, ignore_errors=True)
