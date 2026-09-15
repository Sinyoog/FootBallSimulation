# -*- coding: utf-8 -*-
"""연도전환(52주차→1주차) 소요시간을 "여러 해에 걸쳐" 실측하는 러너.

[왜 별도 스크립트인가] 기존 time_probe.py는 스케줄을
`range(cur_day, cur_day + DAYS + 1)`로 한 번에 만든다 — 이 값이 절대
누적치라, 1년(364일, 52주×7일)을 넘기는 순간 게임 내부의 실제 day(매년
1~364로 리셋됨, game_engine._advance_days_impl 참고: "day는 연중
일자(1~364)")와 어긋난다. 그 결과 두 번째 해로 못 넘어가고
"[ADVANCE] EXIT reason=day_mismatch requested_day=365 cur_day=1 ..."
로 첫 해 끝에서 조용히 멈춘다(디버그 로그는 남지만 진행은 안 됨) —
DAYS를 아무리 크게 줘도 실제로는 1년치만 돌고 끝난다.

이 스크립트는 "한 번에 긴 스케줄을 미리 만들어두기"가 아니라, **매
루프마다 게임에 현재 날짜를 직접 물어보고, 그 시점부터 한 달(30일)치만
그때그때 새로 만들어 넘기는** 방식으로 이 문제를 피한다 — 연도가
넘어가 day가 1로 리셋돼도 다음 루프가 그 값을 그대로 읽어서 다시
만들기 때문에 절대 어긋나지 않는다.

사용법
──────
  python3 tools/time_probe_longrun.py <run_tag> [years] [sync_mode] [cache_mb]
      기본 15년, sync_mode 기본값은 게임이 원래 쓰는 NORMAL(생략하면
      건드리지 않음 — 기존 동작과 100% 동일). sync_mode를 FULL이나
      OFF로 주면 이번 실행(사본 DB, 이 프로세스 안에서만) 한정으로
      PRAGMA synchronous를 그 값으로 덮어써서 비교 실험한다 —
      database.py의 영구 초기화 코드는 전혀 안 건드리므로 실제
      게임이나 원본 세이브는 항상 NORMAL 그대로다.
      cache_mb를 주면(예: 2048 = 2GB) 이번 실행 한정으로 PRAGMA
      cache_size를 그 크기(MB)로 덮어쓴다 — [2026-09 신설, 신민용
      요청: "B-tree 탐색 깊이가 원인이면 hist.db 전체가 캐시에 다
      올라갈 만큼 cache_size를 키우면 사라져야 한다"] 이것도 연결
      단위 세션 설정이라 이 프로세스가 끝나면 사라지고, database.py의
      영구 값(현재 얼마든)은 안 건드린다.
      game.db/game.history.db를 qa_runs/longrun_<tag>/로 복사해 그
      사본에서만 실행(time_probe.py와 동일한 원칙 — 원본 세이브
      절대 안 건드림). 국가대표 소집 등 선택이 필요한 이벤트는
      전부 자동 거절하고 계속 진행한다(사람 개입 불필요).

  실행이 끝나면 이 파일 옆에 두 가지를 만든다:
    - live_sim.log            : 게임이 원래 남기는 전체 로그(그대로 보존)
    - timing_longrun_<tag>.json : 해마다 "[PERF] 연도전환 총"·
      "개인수상산정"·28→29주차 "그 commit" 줄만 뽑아 연도 순으로 정리한
      요약(연차별/조건별 비교용) — commit 평균·최댓값도 같이 넣는다.

주의
────
- 여러 해를 도는 만큼 시간이 걸린다(1년 전환이 약 25~30초라면 15년이면
  대략 6~8분 내외 — PC 사양에 따라 다르다). 콘솔에 매년 끝날 때마다
  진행 상황을 한 줄씩 찍는다.
- cProfile을 쓰지 않으므로(profile_season_transition.py와 달리) 오버헤드
  없이 실제 걸리는 시간 그대로 측정된다.
- 실행할 때마다 이전 태그로 만들어졌던 qa_runs/longrun_*/ 사본과
  timing_longrun_*.json을 전부 지우고 시작한다(아래 참고) — 이전 결과를
  남겨두고 싶으면 timing_longrun_<태그>.json을 다른 이름으로 미리
  복사해두거나, live_sim.log를 따로 백업해둘 것(이 파일은 실행마다
  덮어써지는 건 기존과 동일 — 여기서 새로 지우는 대상이 아니다).
- [2026-09 신설] sync_mode=OFF는 진단 전용이다 — 크래시/정전 시 DB
  손상 위험이 커지는 설정이라, 사본 위에서 도는 이 스크립트 밖(진짜
  세이브)에서는 절대 쓰지 않는다. 이 스크립트는 애초에 원본을 안
  건드리므로 안전하지만, 혹시 이 파일을 참고해 다른 곳에 적용할 땐
  주의할 것.
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import glob
import json
import os
import random
import re
import shutil
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tag = sys.argv[1] if len(sys.argv) > 1 else "run"
YEARS = int(sys.argv[2]) if len(sys.argv) > 2 else 15
SYNC_MODE = sys.argv[3].upper() if len(sys.argv) > 3 else None   # None=건드리지 않음(기존과 동일)
if SYNC_MODE is not None and SYNC_MODE not in ("NORMAL", "FULL", "OFF", "EXTRA"):
    print(f"[longrun] 알 수 없는 sync_mode='{SYNC_MODE}' — NORMAL/FULL/OFF 중 하나여야 합니다.")
    sys.exit(1)
CACHE_MB = int(sys.argv[4]) if len(sys.argv) > 4 else None   # None=건드리지 않음(기존과 동일)
CHUNK_DAYS = 30   # 매 루프 새로 만드는 스케줄 길이(약 한 달) — 연도 경계를
                   # 넘나들어도 다음 루프가 항상 그 시점의 실제 day로 다시
                   # 만들므로 안전하다.

# [2026-09 신설] 실행마다 태그(diag7, diag8, ...)를 바꿔 쓰다 보니 남은
# qa_runs/longrun_<이전태그>/(사본 game.db/game.history.db 포함)와
# timing_longrun_<이전태그>.json이 실행할 때마다 하나씩 쌓였다 — 특히
# 이전 실행이 도중에 죽으면(게임.db 없음, 테이블 없음 등) 맨 끝의 정리
# 코드까지 못 가서 qa_runs 쪽이 통째로 남는다. 매 실행 시작 시 이전
# 태그가 뭐였든 전부 지우고 새로 시작한다 — 이 스크립트가 만드는
# "qa_runs/longrun_*" 사본과 "timing_longrun_*.json" 요약만 지운다
# (원본 game.db/game.history.db나 live_sim.log 등 다른 파일은 안 건드림).
for _old_dir in glob.glob(os.path.join(SCRIPT_DIR, "qa_runs", "longrun_*")):
    shutil.rmtree(_old_dir, ignore_errors=True)
for _old_json in glob.glob(os.path.join(SCRIPT_DIR, "timing_longrun_*.json")):
    try:
        os.remove(_old_json)
    except OSError:
        pass

out_dir = os.path.abspath(os.path.join(SCRIPT_DIR, "qa_runs", f"longrun_{tag}"))
if os.path.exists(out_dir):
    shutil.rmtree(out_dir)
os.makedirs(out_dir)
db_path = os.path.join(out_dir, "headless.db")
shutil.copy(os.path.join(SCRIPT_DIR, "game.db"), db_path)
# [time_probe.py와 동일한 이유] history DB는 WAL 모드라 -wal/-shm을 같이
# 복사하지 않으면 아직 체크포인트되지 않은 최근 커밋분이 빠진 사본이 된다.
_hist_src = os.path.join(SCRIPT_DIR, "game.history.db")
if os.path.exists(_hist_src):
    for _sfx in ("", "-wal", "-shm"):
        if os.path.exists(_hist_src + _sfx):
            shutil.copy(_hist_src + _sfx,
                        os.path.join(out_dir, "headless.history.db" + _sfx))

log_path = os.path.join(SCRIPT_DIR, "live_sim.log")
open(log_path, "w").close()   # 이번 실행분만 남기도록 비운다

# [2026-09 신설, 진단용 — 신민용 요청: "Defender 실시간검사 영향을 보려면
# 제외 전후를 비교해야 하는데, 지금 실제로 제외가 걸려있는 상태로
# 테스트를 돌리고 있는지부터 확인해야 한다"] Windows Defender 상태를
# 조회만 하는 PowerShell 명령(Get-MpComputerStatus/Get-MpPreference —
# 둘 다 읽기 전용, 아무 설정도 안 바꿈)으로 실시간보호 여부와 현재
# 제외 경로 목록을 실행 시작 시 한 번 로그에 남긴다. 제외 목록에
# 이 프로젝트 폴더를 추가/제거하는 건 이 스크립트가 하지 않는다 —
# Windows 보안 설정 UI에서 사용자가 직접 켜고 끄고, 이 스크립트는
# "지금 그 설정이 실제로 어떤 상태인지"만 확인해서 결과 비교 시
# 착각하지 않게 해준다. Windows가 아니거나 권한이 없으면 조용히
# 건너뛴다(테스트 자체를 막지 않음).
try:
    import subprocess as _subprocess_defender
    _dfd_status = _subprocess_defender.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-MpComputerStatus).RealTimeProtectionEnabled"],
        capture_output=True, text=True, timeout=10)
    _dfd_excl = _subprocess_defender.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-MpPreference).ExclusionPath -join ';'"],
        capture_output=True, text=True, timeout=10)
    print(f"[longrun-DEFENDER] 실시간보호={_dfd_status.stdout.strip() or '조회실패'} | "
          f"현재 제외경로={_dfd_excl.stdout.strip() or '(없음)'} | "
          f"이 프로젝트 폴더({SCRIPT_DIR})가 위 목록에 포함돼 있는지 직접 확인할 것",
          flush=True)
except Exception as _e:
    print(f"[longrun-DEFENDER] 상태 조회 실패(Windows Defender가 아니거나 "
          f"PowerShell 권한 문제일 수 있음, 테스트 자체엔 영향 없음): {_e}", flush=True)

sys.path.insert(0, SCRIPT_DIR)
import database

database.DB_PATH = db_path
os.chdir(out_dir)
database.init_db()
database.flush_to_disk_async = lambda: None   # 사본이라 디스크 flush 불필요

# [2026-09 신설, 진단용 — 신민용 요청: "synchronous=NORMAL/FULL/OFF를
# 비교해서 fsync/동기화 비용이 snapshot commit 증가의 원인인지 보자"]
# database.init_db()가 이미 main/hist 둘 다 synchronous=NORMAL로 맞춰
# 놓은 뒤이므로, 여기서 "같은 연결" 위에 원하는 값으로 한 번 더
# 덮어쓴다 — 연결이 끊기면(이 프로세스가 끝나면) 사라지는 세션 단위
# 설정이라 database.py의 영구 초기화 코드도, 실제 세이브 파일도
# 전혀 안 바뀐다. sync_mode를 안 줬으면(기본값 None) 아무것도 안
# 건드리고 기존과 완전히 동일하게 NORMAL로 돈다.
if SYNC_MODE is not None:
    _sync_conn = database.get_conn()
    _sync_conn.execute(f"PRAGMA synchronous={SYNC_MODE}")
    _sync_conn.execute(f"PRAGMA hist.synchronous={SYNC_MODE}")
    _sync_conn.commit()
# 실제로 적용됐는지 반드시 확인해서 로그에 남긴다(신민용 요청) —
# SQLite는 synchronous를 정수(0=OFF,1=NORMAL,2=FULL,3=EXTRA)로 돌려준다.
_sync_check_conn = database.get_conn()
_sync_main_val = _sync_check_conn.execute("PRAGMA synchronous").fetchone()[0]
_sync_hist_val = _sync_check_conn.execute("PRAGMA hist.synchronous").fetchone()[0]
_SYNC_NAMES = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
print(f"[longrun-SYNC] 요청값={SYNC_MODE or '(안 바꿈, 기본 NORMAL)'} | "
      f"실제 적용값: main={_SYNC_NAMES.get(_sync_main_val, _sync_main_val)}"
      f"({_sync_main_val}) hist={_SYNC_NAMES.get(_sync_hist_val, _sync_hist_val)}"
      f"({_sync_hist_val})", flush=True)

# [2026-09 신설, 진단용 — 신민용 요청: "B-tree 탐색 깊이가 원인이면
# hist.db 전체가 캐시에 다 올라갈 만큼 cache_size를 키우면 사라져야
# 한다 — 지난번 256MB는 hist.db가 이미 1GB를 넘겨서 애초에 부족했을
# 수 있다"] synchronous와 완전히 같은 방식 — 이 연결 위에서만 한 번
# 덮어쓰고, database.py의 영구 값과 실제 세이브는 안 건드린다.
# PRAGMA cache_size는 음수를 주면 "그 절댓값 KB"로 해석된다(양수는
# 페이지 개수 — 게임이 원래 쓰는 표기와 동일한 음수·KB 방식으로 맞춤).
if CACHE_MB is not None:
    _cache_kb = -(CACHE_MB * 1024)
    _cache_conn = database.get_conn()
    _cache_conn.execute(f"PRAGMA cache_size={_cache_kb}")
    _cache_conn.execute(f"PRAGMA hist.cache_size={_cache_kb}")
    _cache_conn.commit()
# 실제로 적용됐는지 확인해서 로그에 남긴다 — PRAGMA cache_size 조회는
# 음수(KB 모드)면 그대로, 양수(페이지 모드)면 페이지 수를 돌려준다.
_cache_check_conn = database.get_conn()
_cache_main_val = _cache_check_conn.execute("PRAGMA cache_size").fetchone()[0]
_cache_hist_val = _cache_check_conn.execute("PRAGMA hist.cache_size").fetchone()[0]
print(f"[longrun-CACHE] 요청값={f'{CACHE_MB}MB' if CACHE_MB is not None else '(안 바꿈, 기본값 유지)'} | "
      f"실제 적용값: main={_cache_main_val} hist={_cache_hist_val} "
      f"(음수면 |값|KB, 양수면 페이지 개수 — page_size 곱해야 바이트)", flush=True)

import game_engine as ge
import intl_engine
from constants import DAYS_PER_WEEK

DAYS_PER_YEAR = DAYS_PER_WEEK * 52   # 364 — game_engine._advance_days_impl의
                                       # "day는 연중 일자(1~364)" 정의와 동일

random.seed(12345)
if not ge.get_player():
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")


def _build_chunk(start_day: int, n: int) -> list:
    """start_day부터 n일치 (day, "휴식", {}) 스케줄을 만든다. 364를 넘으면
    자동으로 1로 감아준다(실제 게임의 연중 일자 규칙과 동일)."""
    out = []
    d = start_day
    for _ in range(n):
        out.append((d, "휴식", {}))
        d = d + 1 if d < DAYS_PER_YEAR else 1
    return out


start_year = ge.get_state().get("current_year")
years_done = 0
t0 = time.perf_counter()
guard = 0
guard_limit = YEARS * (DAYS_PER_YEAR // CHUNK_DAYS + 4) + 10   # 연도당 기대 루프수+여유

while years_done < YEARS and guard < guard_limit:
    guard += 1
    year_before = ge.get_state().get("current_year")
    cur_day = ge.get_state().get("current_day") or 1
    chunk = _build_chunk(cur_day, CHUNK_DAYS)
    ge.advance_days(chunk)

    # 국가대표 소집 등 선택이 필요한 이벤트는 전부 자동 거절 — 사람 개입 불필요.
    pending = intl_engine.get_pending_choice()
    while pending:
        for opt in pending.get("options", []):
            intl_engine.decline_national_team(opt["tournament_id"])
        # 거절 처리로 그 시점부터 조금 더 진행이 필요할 수 있어 같은 청크를
        # 현재 시점 기준으로 다시 만들어 이어준다.
        cur_day2 = ge.get_state().get("current_day") or 1
        ge.advance_days(_build_chunk(cur_day2, CHUNK_DAYS))
        pending = intl_engine.get_pending_choice()

    year_after = ge.get_state().get("current_year")
    if year_after != year_before:
        years_done += 1
        elapsed = time.perf_counter() - t0
        print(f"[longrun] {year_before}→{year_after}년 완료 "
              f"({years_done}/{YEARS}년째, 누적 {elapsed:.1f}s)", flush=True)

total = time.perf_counter() - t0
if guard >= guard_limit:
    print(f"[longrun] 경고: 예상보다 많이 돌아 {guard}회에서 중단(guard_limit 도달) "
          f"— 실제로 {years_done}년만 진행됐을 수 있음", flush=True)

# ── live_sim.log에서 연도전환/개인수상산정/스냅샷commit 줄만 뽑아 연차별·조건별 비교용 요약 생성 ──
_transition_re = re.compile(r"\[PERF\] 연도전환 총 ([\d.]+)s")
_award_re = re.compile(r"\[AWARD-PERF\] (\d+)년 개인수상산정 ([\d.]+)s")
# [2026-09 신설, 진단용] "[PERF-SNAPSHOT] 2001년 스냅샷INSERT 버킷 세부:
# standings_half INSERT+commit 0.031s (11393건) | _snapshot_team_lineup_half
# 1.453s | 그 commit 0.219s | close() 0.005s" 형태에서 연도·snapshot함수
# 시간·그 commit 시간을 같이 뽑는다. "[PERF-HALF] ...년 28주차 하반기
# 진입 총 X.XXXs (...)"에서 28→29주차 전체 구간 시간도 같이 뽑는다.
_snapshot_re = re.compile(
    r"\[PERF-SNAPSHOT\] (\d+)년 스냅샷INSERT 버킷 세부: .*?"
    r"_snapshot_team_lineup_half ([\d.]+)s.*?그 commit(?:\(.*?\))? ([\d.]+)s")
_half_total_re = re.compile(r"\[PERF-HALF\] (\d+)년 28주차 하반기 진입 총 ([\d.]+)s")
transitions, awards, snapshots = [], [], []
half_totals = {}
for line in open(log_path, encoding="utf-8", errors="replace"):
    m = _transition_re.search(line)
    if m:
        transitions.append(float(m.group(1)))
    m2 = _award_re.search(line)
    if m2:
        awards.append({"year": int(m2.group(1)), "awards_s": float(m2.group(2))})
    m3 = _snapshot_re.search(line)
    if m3:
        snapshots.append({"year": int(m3.group(1)),
                           "snapshot_func_s": float(m3.group(2)),
                           "commit_s": float(m3.group(3))})
    m4 = _half_total_re.search(line)
    if m4:
        half_totals[int(m4.group(1))] = float(m4.group(2))
for _row in snapshots:
    _row["half_total_s"] = half_totals.get(_row["year"])

_commit_values = [r["commit_s"] for r in snapshots]
commit_stats = {
    "avg_s": round(sum(_commit_values) / len(_commit_values), 3) if _commit_values else None,
    "max_s": max(_commit_values) if _commit_values else None,
    "min_s": min(_commit_values) if _commit_values else None,
}

summary = {
    "tag": tag, "sync_mode": SYNC_MODE or "NORMAL(기본, 안 바꿈)",
    "cache_mb": CACHE_MB if CACHE_MB is not None else "(기본, 안 바꿈)",
    "start_year": start_year, "years_requested": YEARS,
    "years_completed": years_done, "wall_time_s": round(total, 1),
    "transitions_s": transitions,           # 연도전환 총 소요시간, 연차 순
    "individual_awards_s": awards,          # 개인수상산정만 따로, year 라벨 포함
    "snapshot_commit_by_year": snapshots,   # 연도별 스냅샷함수/commit/28→29주차 전체
    "snapshot_commit_stats": commit_stats,  # commit 시간 평균/최댓값/최솟값(조건 간 비교용)
}
out_json = os.path.join(SCRIPT_DIR, f"timing_longrun_{tag}.json")
json.dump(summary, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps(summary, ensure_ascii=False))
print(f"[longrun] 요약 저장: {out_json}  (live_sim.log는 그대로 보존됨)")

os.chdir(SCRIPT_DIR)
shutil.rmtree(out_dir, ignore_errors=True)