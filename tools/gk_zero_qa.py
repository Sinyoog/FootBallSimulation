# -*- coding: utf-8 -*-
"""GK 0명 팀 발생 지점 계측 (2026-09 신설).

[신민용 리포트] 3시즌 헤드리스를 돌리면 골키퍼가 아예 없는 팀이 생긴다.
스쿼드 11명 미만 팀은 0개이므로 인원수 문제가 아니라 포지션 구성 문제다.

ai_players에서 선수를 빼거나(DELETE) 팀을 옮기는(UPDATE team_id) 모든
함수를 감싸서, 호출 전/후의 "GK가 0명인 팀 수"를 찍는다 — 어느 함수가
몇 개를 만들어내는지 계측으로 특정한다(추측 금지).

사용: python3 tools/gk_zero_qa.py [시즌수] [--out 폴더]
"""
import _path  # noqa: F401
import argparse
import collections
import datetime
import os
import random
import shutil
import sys
import time

import database

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GK0_SQL = """SELECT COUNT(*) FROM teams t WHERE NOT EXISTS
             (SELECT 1 FROM ai_players p WHERE p.team_id=t.id AND p.position='GK')"""

# (모듈명, 함수명) — ai_players 행을 지우거나 팀을 옮기는 모든 경로
TARGETS = [
    ("ai_lifecycle", "_process_loan_returns"),
    ("ai_lifecycle", "_prestige_scouting"),
    ("ai_lifecycle", "_prestige_potential_scouting"),
    ("ai_lifecycle", "_retire_and_replace"),
    ("ai_lifecycle", "_transfer_market"),
    ("ai_lifecycle", "_rebalance_squad_sizes"),
    ("ai_lifecycle", "_enforce_foreign_quota_worldwide"),
    ("ai_lifecycle", "apply_squad_turnover_after_movement"),
    ("game_engine", "_affiliate_callup_from_child"),
    ("game_engine", "_make_room_on_join"),
]

DELTA = collections.Counter()
CALLS = collections.Counter()
TRACE = []


def _gk0():
    try:
        return database.get_conn().execute(GK0_SQL).fetchone()[0]
    except Exception:
        return -1


def _wrap(mod, name):
    import importlib
    m = importlib.import_module(mod)
    fn = getattr(m, name, None)
    if fn is None:
        print(f"[경고] {mod}.{name} 없음 — 건너뜀")
        return
    label = f"{mod}.{name}"

    def wrapper(*a, **kw):
        b = _gk0()
        out = fn(*a, **kw)
        af = _gk0()
        CALLS[label] += 1
        if af != b:
            DELTA[label] += (af - b)
            TRACE.append((label, b, af))
        return out

    wrapper.__name__ = name
    wrapper.__wrapped__ = fn
    setattr(m, name, wrapper)
    # 다른 모듈이 `from ai_lifecycle import X` 로 직접 들고 있는 참조도 교체
    for other in list(sys.modules.values()):
        if other is None or other is m:
            continue
        try:
            if getattr(other, name, None) is fn:
                setattr(other, name, wrapper)
        except Exception:
            pass


def run(n_seasons=3, out_root=None, seed=12345):
    random.seed(seed)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.abspath(os.path.join(out_root or "qa_runs", f"gk0_n{n_seasons}_{ts}"))
    os.makedirs(out_dir, exist_ok=True)
    db_path = os.path.join(out_dir, "run.db")
    shutil.copy(os.path.join(_ROOT, "game.db"), db_path)
    os.chdir(out_dir)
    database.DB_PATH = db_path
    database.init_db()
    import power_ranking
    power_ranking.ensure_power_ranking_tables(database.get_conn())

    import ai_lifecycle  # noqa: F401
    import game_engine as ge
    import intl_engine
    for mod, name in TARGETS:
        _wrap(mod, name)

    if not ge.get_player():
        ge.create_player(name="GK0 QA", position="CM", sub_role="")
    print(f"[gk0] 시작 GK0={_gk0()} 팀 / 출력 {out_dir}", flush=True)

    schedule = [(d, "휴식", {}) for d in range(1, 365)]
    for i in range(n_seasons):
        t0 = time.time()
        remaining = list(schedule)
        for _g in range(40):
            ge.advance_days(remaining)
            pending = intl_engine.get_pending_choice()
            if not pending:
                break
            for opt in pending.get("options", []):
                intl_engine.decline_national_team(opt["tournament_id"])
            cur_day = ge.get_state().get("current_day")
            remaining = [it for it in schedule if it[0] >= cur_day]
            if not remaining:
                break
        print(f"[gk0] {i+1}/{n_seasons}시즌 완료 {time.time()-t0:.0f}s "
              f"→ GK0={_gk0()}팀", flush=True)

    conn = database.get_conn()
    print("\n" + "=" * 70)
    print(f"[결과] 최종 GK 0명 팀: {_gk0()}개")
    print(f"        스쿼드 11명 미만 팀: "
          f"{conn.execute('SELECT COUNT(*) FROM (SELECT t.id,(SELECT COUNT(*) FROM ai_players p WHERE p.team_id=t.id) n FROM teams t) WHERE n<11').fetchone()[0]}개")
    print(f"\n{'함수':<52} {'호출':>6} {'GK0 순증':>9}")
    for label, _n in CALLS.most_common():
        d = DELTA.get(label, 0)
        mark = "   ← 원인" if d > 0 else ""
        print(f"  {label:<50} {CALLS[label]:>6} {d:>+9}{mark}")
    if TRACE:
        print("\n[변화 추적] (함수, 전, 후)")
        for t in TRACE[:60]:
            print(f"  {t[0]:<50} {t[1]:>4} → {t[2]:>4}  ({t[2]-t[1]:+d})")
    database.flush_to_disk()
    print(f"\n[gk0] DB: {db_path}")
    return db_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, nargs="?", default=3)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    run(a.n, a.out)