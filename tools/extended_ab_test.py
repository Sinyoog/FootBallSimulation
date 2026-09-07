"""
20/100/200시즌 확장 검증용 — AUTO/PO 경로별 통계까지 남기는 헤드리스 러너.

promotion_log 스키마는 안 건드리고(요청대로), 시즌을 두 구간으로 나눠서
진행하는 것만으로 AUTO/PO를 구분한다:
  - day 1~301   : CLUB_SEASON_END_DAY(300)의 자동 승강까지 포함
  - day 302~364 : PLAYOFF_WEEK(44, day 302~) 승강 PO 포함

각 구간 종료 시점의 promotion_log 행 수 차이로 "이번 구간에 새로 추가된
행"만 골라, to_tier==1(1부로 승격)인 것들의 team_name을 teams와 join해서
classification_status를 조회한다(팀명 매칭이라 동명 팀 케이스는 진단
목적의 근사치임을 감안 — 정합성 자체는 이미 tier1 무결성 검사가 별도로
확정한다).

사용: python3 extended_ab_test.py <off|on> <시작DB> <시즌수> <seed>
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import datetime
import os
import random
import shutil
import sys

import database
import game_engine as ge
from affiliate_integrity import run_all_checks


def _promotion_log_new_rows(c, since_id):
    c.execute(
        "SELECT id, team_name, to_tier FROM promotion_log WHERE id > ? AND to_tier = 1",
        (since_id,),
    )
    return c.fetchall()


def _classify_team_name(c, team_name):
    """team_name으로 classification_status 조회(동명 팀이면 첫 매치, 진단용 근사치)."""
    c.execute(
        "SELECT classification_status FROM teams WHERE name=? LIMIT 2", (team_name,)
    )
    rows = c.fetchall()
    if not rows:
        return "UNKNOWN(팀명매칭실패)"
    if len(rows) > 1:
        return f"AMBIGUOUS({rows[0][0]}?)"
    return rows[0][0]


def _max_promotion_log_id(c):
    c.execute("SELECT COALESCE(MAX(id),0) FROM promotion_log")
    return c.fetchone()[0]


def _advance_with_intl_autodecline(schedule, max_guard=20):
    """advance_days가 국가대표 발탁 대기로 멈추면 자동으로 전부 거절하고
    이어서 진행한다. 반환: (완료 여부, 마지막 current_day).

    완료 판정: 요청한 마지막 day까지 도달했거나(cur_day >= target_last_day),
    그 사이에 시즌이 실제로 넘어갔으면(season/year가 호출 전보다 증가)
    day 넘버링이 다음 시즌 1일차로 리셋된 것뿐이므로 정상 완료로 본다.
    둘 다 아니고 발탁 대기도 없는데 더 진행이 안 되면(day_mismatch 등
    다른 이유) 진짜 정체로 판정한다."""
    import intl_engine
    remaining = list(schedule)
    st0 = ge.get_state()
    season0, year0 = st0.get("current_season"), st0.get("current_year")
    for _ in range(max_guard):
        if not remaining:
            return True, ge.get_state().get("current_day")
        target_last_day = remaining[-1][0]
        ge.advance_days(remaining)
        st = ge.get_state()
        cur_day = st.get("current_day")
        season_now, year_now = st.get("current_season"), st.get("current_year")
        rolled_over = (season_now, year_now) != (season0, year0)
        if rolled_over or (cur_day is not None and cur_day >= target_last_day):
            return True, cur_day
        pending = intl_engine.get_pending_choice()
        if not pending:
            # 발탁 대기도 아니고 목표에도 못 미쳤다면 진짜 정체
            return False, cur_day
        for opt in pending.get("options", []):
            intl_engine.decline_national_team(opt["tournament_id"])
        remaining = [item for item in schedule if item[0] >= cur_day]
    return False, ge.get_state().get("current_day")


def run_extended(mode: str, src_db: str, n_seasons: int, seed: int = 12345):
    # [2026-08 신설, 신민용 리포트: "실행할 때마다 결과 파일이 뒤죽박죽된다"]
    # 예전엔 work_db(ext_{mode}_{seed}.db)가 스크립트를 실행한 위치에 그대로
    # 생겨서, 여러 번 돌리면 이전 결과와 섞이거나 파일명이 겹쳐 지워질 수
    # 있었다. 실행마다 qa_runs/ 아래 타임스탬프 폴더를 새로 만들어 그 안에서만
    # 동작한다 — chdir 덕에 이 함수가 직접 만드는 work_db뿐 아니라, 만약
    # AFFILIATE_PROMOTION_RESTRICTION 보정 로직이 tier_audit.jsonl 같은
    # 상대경로 로그를 남기더라도 전부 이 폴더 안으로 떨어진다.
    src_db = os.path.abspath(src_db)   # os.chdir() 전에 절대경로로 먼저 고정
    restriction = mode == "on"

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.abspath(os.path.join("qa_runs", f"extended_{mode}_n{n_seasons}_seed{seed}_{ts}"))
    os.makedirs(out_dir, exist_ok=True)
    os.chdir(out_dir)
    print(f"[extended_ab_test] 출력 폴더: {out_dir}")

    work_db = f"ext_{mode}_{seed}.db"
    for ext in ("", "-wal", "-shm", "-journal"):
        p = work_db + ext
        if os.path.exists(p):
            os.remove(p)
    shutil.copy(src_db, work_db)

    database.DB_PATH = os.path.abspath(work_db)
    database.init_db()
    # [2026-08 재현성 조사에서 확정] 헤드리스 결정론 모드에서는 배경
    # 자동저장 스레드를 반드시 비활성화한다 — 실행 타이밍(OS 스케줄링)에
    # 따라 advance_days가 조용히 멈추는 현상의 원인 중 하나였다.
    database.flush_to_disk_async = lambda: None
    conn = database.get_conn()
    c = conn.cursor()

    import constants
    constants.AFFILIATE_PROMOTION_RESTRICTION = restriction
    random.seed(seed)

    p = ge.get_player()
    if not p:
        ge.create_player(name="Headless Dummy", position="CM", sub_role="")

    schedule_auto = [(d, "휴식", {}) for d in range(1, 302)]     # 1~301 (자동승격 포함)
    schedule_po_rest = [(d, "휴식", {}) for d in range(302, 365)]  # 302~364 (PO + 나머지)

    print(f"\n===== mode={mode} seed={seed} restriction={restriction} seasons={n_seasons} =====")
    header = (f"{'season':>6} | {'auto_aff':>8} {'auto_rev':>8} | {'po_aff':>7} {'po_rev':>7} | "
              f"{'tier1_viol':>10} {'excused':>7} {'self_par':>8} {'crash':>5} {'stalled':>7}")
    print(header)

    rows_out = []

    for s in range(1, n_seasons + 1):
        crashed = False
        stalled = False
        id_before_auto = _max_promotion_log_id(c)
        try:
            ok1, _ = _advance_with_intl_autodecline(schedule_auto)
            if not ok1:
                stalled = True
        except Exception as e:
            print(f"[season {s}] AUTO 구간 크래시: {e}")
            crashed = True

        id_after_auto = _max_promotion_log_id(c)
        auto_rows = _promotion_log_new_rows(c, id_before_auto)

        if not crashed:
            try:
                ok2, _ = _advance_with_intl_autodecline(schedule_po_rest)
                if not ok2:
                    stalled = True
            except Exception as e:
                print(f"[season {s}] PO/나머지 구간 크래시: {e}")
                crashed = True

        id_after_po = _max_promotion_log_id(c)
        po_rows = _promotion_log_new_rows(c, id_after_auto)

        auto_aff = auto_rev = po_aff = po_rev = 0
        for _id, tname, _tier in auto_rows:
            st = _classify_team_name(c, tname)
            if st == "AFFILIATE":
                auto_aff += 1
            elif st == "REVIEW":
                auto_rev += 1
        for _id, tname, _tier in po_rows:
            st = _classify_team_name(c, tname)
            if st == "AFFILIATE":
                po_aff += 1
            elif st == "REVIEW":
                po_rev += 1

        result = run_all_checks(c, verbose=False)
        viol = len(result["tier1_violations"])
        exc = len(result["tier1_excused"])
        selfp = len(result["self_parent"])

        print(f"{s:>6} | {auto_aff:>8} {auto_rev:>8} | {po_aff:>7} {po_rev:>7} | "
              f"{viol:>10} {exc:>7} {selfp:>8} {int(crashed):>5} {int(stalled):>7}")

        rows_out.append({
            "season": s, "auto_aff": auto_aff, "auto_rev": auto_rev,
            "po_aff": po_aff, "po_rev": po_rev,
            "tier1_violations": viol, "excused": exc, "self_parent": selfp,
            "crashed": crashed, "stalled": stalled,
        })

        if crashed:
            print(f"[season {s}] 크래시로 중단")
            break
        if stalled:
            print(f"[season {s}] 진행 중단(advance_days가 목표 day까지 못 감) — 중단")
            break

    database.flush_to_disk()
    return rows_out


if __name__ == "__main__":
    mode = sys.argv[1]
    src_db = sys.argv[2]
    n_seasons = int(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 12345
    run_extended(mode, src_db, n_seasons, seed)