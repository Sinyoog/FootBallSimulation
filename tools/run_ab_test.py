"""
사용: python3 run_ab_test.py <off|on> <시작DB> <시즌수> <seed>

[2026-08 신설, 신민용 리포트: "실행할 때마다 결과 파일이 뒤죽박죽된다"]
예전엔 work_db(ab_off.db/ab_on.db)가 항상 스크립트를 실행한 위치(프로젝트
루트 등)에 그대로 생겼다 — 여러 번 돌리면 이전 결과와 같은 파일명이라
덮어써지거나 off/on 결과가 서로 뒤섞여 뭐가 최신인지 알기 어려웠다.
이제 실행마다 qa_runs/ 아래 타임스탬프 폴더를 새로 만들어 그 안에서만
work_db를 만든다.
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import datetime
import os
import shutil
import sys

target = sys.argv[1]      # 'off' or 'on'
src_db = sys.argv[2]      # 시작 DB 경로
n_seasons = int(sys.argv[3])
seed = int(sys.argv[4])

src_db = os.path.abspath(src_db)   # os.chdir() 전에 절대경로로 먼저 고정

_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = os.path.abspath(os.path.join("qa_runs", f"abtest_{target}_n{n_seasons}_seed{seed}_{_ts}"))
os.makedirs(out_dir, exist_ok=True)
os.chdir(out_dir)
print(f"[ab_test] 출력 폴더: {out_dir}")

work_db = f"ab_{target}.db"
shutil.copy(src_db, work_db)

import database
database.DB_PATH = os.path.abspath(work_db)

import headless_runner
# standalone_output=False: 출력 폴더/DB_PATH는 위에서 이미 이 스크립트가
# 직접 관리했으므로, headless_runner가 또 다른 폴더를 만들어 work_db를
# 덮어쓰지 않도록 막는다.
result = headless_runner.run(n_seasons, seed=seed, restriction=(target == "on"),
                              standalone_output=False)
print(f"\n[AB-TEST:{target}] passed={result['passed']} "
      f"violations={len(result['tier1_violations'])} "
      f"excused={len(result['tier1_excused'])} "
      f"self_parent={len(result['self_parent'])}")