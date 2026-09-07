# -*- coding: utf-8 -*-
"""재현성 조사용 헤드리스 러너 + RNG 계측.

사용:
  PYTHONHASHSEED=0 python3 det_probe.py <run_tag> <days> [--rng-log] [--detail A B]

- game.db / game.history.db 를 qa_runs/det_<run_tag>/ 로 복사해서만 동작.
- random 모듈 전 진입점을 감싸 (호출순번, 호출지점, 결과) 를 계측.
  기본은 4096콜마다 롤링 해시 체크포인트만 기록(메모리/디스크 절약).
  --detail A B 를 주면 [A,B) 구간만 상세 라인을 남긴다.
- 종료 후 주요 테이블 해시를 출력.
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import hashlib
import json
import os
import random
import shutil
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

run_tag = sys.argv[1]
DAYS = int(sys.argv[2])
args = sys.argv[3:]
DETAIL = None
if "--detail" in args:
    i = args.index("--detail")
    DETAIL = (int(args[i + 1]), int(args[i + 2]))
CHUNK = 4096

out_dir = os.path.abspath(os.path.join(SCRIPT_DIR, "qa_runs", f"det_{run_tag}"))
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

# ── random 계측 ────────────────────────────────────────────────
_NAMES = ["random", "randint", "choice", "choices", "shuffle", "uniform",
          "gauss", "sample", "randrange", "triangular", "betavariate",
          "expovariate", "getrandbits", "normalvariate", "vonmisesvariate",
          "paretovariate", "weibullvariate", "lognormvariate"]
_orig = {}
for n in _NAMES:
    if hasattr(random, n):
        _orig[n] = getattr(random, n)

_ADDR_RE = __import__("re").compile(r"0x[0-9a-fA-F]+")
FULL = "--full" in args
STATEWIN = None
if "--statewin" in args:
    _i = args.index("--statewin")
    STATEWIN = (int(args[_i+1]), int(args[_i+2]))
_getstate = random.getstate

state = {"n": 0, "roll": hashlib.blake2b(digest_size=8)}
checkpoints = []
detail_lines = []
_fh = open(os.path.join(out_dir, "rng_full.log"), "w", buffering=1<<20) if FULL else None


def _site():
    f = sys._getframe(2)
    return f"{os.path.basename(f.f_code.co_filename)}:{f.f_code.co_name}:{f.f_lineno}"


def _wrap(name):
    orig = _orig[name]

    def wrapper(*a, **kw):
        _pre = None
        if STATEWIN and STATEWIN[0] <= state["n"] < STATEWIN[1]:
            _pre = hashlib.blake2b(repr(_getstate()).encode(), digest_size=6).hexdigest()
        r = orig(*a, **kw)
        st = state
        st["n"] += 1
        rec = f"{name}|{_site()}|{r!r}"
        if len(rec) > 400:
            rec = rec[:400]
        rec = _ADDR_RE.sub("0xX", rec)
        st["roll"].update(rec.encode("utf-8", "replace"))
        if _pre is not None:
            _post = hashlib.blake2b(repr(_getstate()).encode(), digest_size=6).hexdigest()
            rec = rec + f"|S{_pre}->{_post}"
        if FULL:
            _fh.write(f"{st['n']}\t{rec}\n")
        elif DETAIL and DETAIL[0] <= st["n"] < DETAIL[1]:
            detail_lines.append(f"{st['n']}\t{rec}")
        if st["n"] % CHUNK == 0:
            checkpoints.append((st["n"], st["roll"].hexdigest()))
        return wrapper_done(r)

    def wrapper_done(r):
        return r

    wrapper.__name__ = name
    return wrapper


for n in _orig:
    setattr(random, n, _wrap(n))

# ── 게임 구동 ──────────────────────────────────────────────────
sys.path.insert(0, SCRIPT_DIR)
import database

database.DB_PATH = db_path
os.chdir(out_dir)
database.init_db()
database.flush_to_disk_async = lambda: None  # 백그라운드 저장 비활성(결정론)

import game_engine as ge
import intl_engine

random.seed(12345)

p = ge.get_player()
if not p:
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")

st0 = ge.get_state()
cur_day = st0.get("current_day") or 1
sys.stderr.write(f"[det] start day={cur_day} season={st0['current_season']} "
                 f"year={st0['current_year']} phase={st0.get('phase')}\n")

schedule = [(d, "휴식", {}) for d in range(cur_day, cur_day + DAYS + 1)]
t0 = time.time()
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
elapsed = time.time() - t0

st1 = ge.get_state()
sys.stderr.write(f"[det] end day={st1.get('current_day')} season={st1['current_season']} "
                 f"year={st1['current_year']} elapsed={elapsed:.1f}s rng_calls={state['n']}\n")

database.flush_to_disk()

# ── 결과 해시 ──────────────────────────────────────────────────
conn = database.get_conn()
c = conn.cursor()
TABLES = ["ai_players", "teams", "ai_transfer_log", "cl_tournaments",
          "intl_tournaments", "team_power_rating", "country_power_rating",
          "trophy_log", "match_results", "awards"]
res = {}
for t in TABLES:
    try:
        c.execute(f"SELECT * FROM {t}")
        h = hashlib.blake2b(digest_size=8)
        cnt = 0
        for row in c:
            cnt += 1
            h.update(repr(tuple(row)).encode("utf-8", "replace"))
        res[t] = (cnt, h.hexdigest())
    except Exception as e:
        res[t] = ("ERR", str(e)[:60])

out = {
    "tag": run_tag,
    "hashseed": os.environ.get("PYTHONHASHSEED"),
    "rng_calls": state["n"],
    "rng_final": state["roll"].hexdigest(),
    "elapsed": elapsed,
    "tables": res,
    "checkpoints": checkpoints,
}
with open(os.path.join(SCRIPT_DIR, f"det_{run_tag}.json"), "w") as f:
    json.dump(out, f)
if _fh:
    _fh.flush()
if detail_lines:
    with open(os.path.join(SCRIPT_DIR, f"det_{run_tag}.detail.txt"), "w") as f:
        f.write("\n".join(detail_lines))

print(json.dumps({k: v for k, v in out.items() if k != "checkpoints"},
                 ensure_ascii=False, indent=1))
