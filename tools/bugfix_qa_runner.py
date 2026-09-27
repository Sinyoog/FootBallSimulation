# -*- coding: utf-8 -*-
"""버그수정 검증용 헤드리스 러너 (2026-09 신설).

tools/headless_runner.py와 같은 패턴이지만 두 가지가 다르다:
  1) power_ranking.ensure_power_ranking_tables()를 init_db() 직후에 부른다
     (안 부르면 _sim_all_ai_matches에서 "no such table: team_power_rating").
  2) 진행이 끝난 DB 경로를 표준출력 마지막 줄에 찍는다 — 이어서
     tools/schedule_gap_qa.py / tools/gk_zero_qa.py로 같은 DB를 계측한다.

사용: python3 tools/bugfix_qa_runner.py <시즌수> [--tag 이름] [--out 폴더]
"""
import _path  # noqa: F401
import argparse
import datetime
import os
import random
import shutil
import sys
import time

import database

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_training_schedule(days=364):
    return [(d, "휴식", {}) for d in range(1, days + 1)]


def run(n_seasons, tag="bugfix", out_root=None, seed=12345, src_db=None, extra_days=0):
    random.seed(seed)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.abspath(os.path.join(out_root or "qa_runs", f"{tag}_n{n_seasons}_{ts}"))
    os.makedirs(out_dir, exist_ok=True)
    db_path = os.path.join(out_dir, "run.db")
    shutil.copy(src_db or os.path.join(_ROOT, "game.db"), db_path)
    os.chdir(out_dir)
    database.DB_PATH = db_path
    print(f"[qa] 출력 폴더: {out_dir}", flush=True)

    database.init_db()
    import power_ranking
    conn = database.get_conn()
    power_ranking.ensure_power_ranking_tables(conn)

    import game_engine as ge
    import intl_engine

    if not ge.get_player():
        ge.create_player(name="QA Dummy", position="CM", sub_role="")
    p = ge.get_player()
    print(f"[qa] 더미 플레이어 id={p['id']} team={p.get('current_team_id')}", flush=True)

    # extra_days > 0 이면 마지막에 시즌을 끝까지 돌리지 않고 그 일차까지만
    # 진행한다 — 리그 일정(match_results)과 대륙대항전/컵 토너먼트가 같은
    # 연도에 동시에 존재하는 '시즌 중' 상태를 만들어야 일정 간격 계측
    # (tools/intl_day_gap_qa.py)이 가능하기 때문. 시즌이 끝나 연도가
    # 넘어가면 지난 시즌 리그 경기는 정리(prune)돼서 사라진다.
    schedule = build_training_schedule()
    passes = [(i, 364) for i in range(n_seasons)]
    if extra_days:
        passes.append((n_seasons, int(extra_days)))
    for i, upto in passes:
        t0 = time.time()
        before = ge.get_state()["current_season"]
        remaining = [it for it in schedule if it[0] <= upto]
        for _g in range(40):
            ge.advance_days(remaining)
            pending = intl_engine.get_pending_choice()
            if not pending:
                break
            for opt in pending.get("options", []):
                intl_engine.decline_national_team(opt["tournament_id"])
            cur_day = ge.get_state().get("current_day")
            remaining = [it for it in schedule if cur_day <= it[0] <= upto]
            if not remaining:
                break
        after = ge.get_state()["current_season"]
        print(f"[qa] {i+1}/{len(passes)} 구간 완료 (~{upto}일차, season {before} -> {after}) "
              f"{time.time()-t0:.0f}s", flush=True)

    database.flush_to_disk()
    print(f"[qa] DB: {db_path}", flush=True)
    return db_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, nargs="?", default=3)
    ap.add_argument("--tag", default="bugfix")
    ap.add_argument("--out", default=None)
    ap.add_argument("--src", default=None)
    ap.add_argument("--extra-days", type=int, default=0,
                    help="마지막에 시즌을 끝내지 않고 이 일차까지만 추가 진행")
    a = ap.parse_args()
    run(a.n, a.tag, a.out, src_db=a.src, extra_days=a.extra_days)