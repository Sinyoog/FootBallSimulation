# -*- coding: utf-8 -*-
"""
연도전환(52주차→1주차) 구간을 cProfile로 실측하는 진단 스크립트.

live_sim.log의 [PERF]/[PERF-TM]/[PERF-LIFECYCLE] 등은 "이적루프 7.3초"처럼
블록 단위 합계만 보여준다. 이 블록 안에서 정확히 어느 함수가, 몇 번
불려서, 자기 시간(tottime)을 얼마나 쓰는지는 cProfile로만 알 수 있다 —
수동 print 타이머를 74,000번 도는 루프 안에 박으면 그 자체가 새 병목이
되므로(측정 오버헤드 누적) 이런 hot loop엔 cProfile이 맞는 도구다.

사용법
──────
  python3 profile_season_transition.py
      스크립트 옆의 game.db를 복사해서(원본 절대 안 건드림) qa_runs/ 아래
      새 폴더에서 실행. 현재 저장된 날짜부터 EXTRA_DAYS일만큼 전진하며,
      그 사이에 최소 한 번의 연도전환(시즌 종료→새 시즌)이 포함되도록
      기본값(200일)을 잡았다. 결과는 이 파일 옆에 profile_output.prof로
      저장된다.

  python3 -c "
import pstats
p = pstats.Stats('profile_output.prof')
p.sort_stats('tottime').print_stats(30)   # 자기 시간 기준 Top 30
p.sort_stats('cumulative').print_stats(30)  # 누적 시간(자기+하위호출) 기준
"
      결과 분석은 pstats로. tottime(자기 시간)이 큰 함수가 "진짜 범인",
      cumulative(누적 시간)이 크지만 tottime은 작은 함수는 "무거운 걸
      호출만 하는 중간 관리자"다.

주의
────
- 이 스크립트는 실제 세이브(game.db)를 절대 수정하지 않는다 — 항상
  qa_runs/ 아래 복사본에서만 동작(headless_runner.py와 동일한 원칙).
- cProfile 자체가 파이썬 함수 호출마다 감시 코드를 끼워 넣으므로 실제
  걸리는 시간의 2~3배로 늘어난다(이 실행에서는 약 2.5배 관측됨) — 절대
  시간이 아니라 "함수별 상대 비중"을 보는 용도로만 쓴다. 실제 걸리는
  시간은 live_sim.log의 [PERF] 계열 줄을 봐야 한다.
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import cProfile
import pstats
import sys
import os
import shutil
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
import game_engine as ge
import intl_engine

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTRA_DAYS = 200          # 현재 날짜부터 며칠치를 더 돌릴지(연도전환 포함시키려면 넉넉히)
OUT_PROF = os.path.join(SCRIPT_DIR, "profile_output.prof")


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.abspath(os.path.join("qa_runs", f"profcustom_{ts}"))
    os.makedirs(out_dir, exist_ok=True)

    src_db = os.path.join(SCRIPT_DIR, "game.db")
    if not os.path.exists(src_db):
        print(f"[profiler] game.db를 찾을 수 없음: {src_db}")
        return
    db_path = os.path.join(out_dir, "headless.db")
    shutil.copy(src_db, db_path)
    print(f"[profiler] 세이브 복사: {src_db} -> {db_path} (원본은 건드리지 않음)")

    os.chdir(out_dir)
    database.DB_PATH = db_path
    database.init_db()

    st = ge.get_state()
    cur_day = st.get("current_day")
    print(f"[profiler] 시작 day={cur_day}, season={st['current_season']}, year={st['current_year']}")

    schedule = [(d, "휴식", {}) for d in range(cur_day, cur_day + EXTRA_DAYS + 1)]

    pr = cProfile.Profile()
    pr.enable()

    remaining = list(schedule)
    for _guard in range(30):
        ge.advance_days(remaining)
        pending = intl_engine.get_pending_choice()
        if not pending:
            break
        # 국가대표 발탁 대기는 시뮬레이션 진행을 막으므로 자동 거절하고
        # 이어서 진행한다(헤드리스 러너와 동일한 처리).
        for opt in pending.get("options", []):
            intl_engine.decline_national_team(opt["tournament_id"])
        cd = ge.get_state().get("current_day")
        remaining = [item for item in schedule if item[0] >= cd]
        if not remaining:
            break

    pr.disable()
    database.flush_to_disk()

    st2 = ge.get_state()
    print(f"[profiler] 종료 day={st2.get('current_day')}, season={st2['current_season']}, year={st2['current_year']}")

    stats = pstats.Stats(pr)
    stats.dump_stats(OUT_PROF)
    print(f"[profiler] 프로파일 저장 완료: {OUT_PROF}")
    print("[profiler] 분석 예시:")
    print("  python3 -c \"import pstats; pstats.Stats('profile_output.prof')"
          ".sort_stats('tottime').print_stats(30)\"")


if __name__ == "__main__":
    main()