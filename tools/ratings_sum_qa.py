# -*- coding: utf-8 -*-
"""라인업 평점 골/어시 합계 정합성 계측기 (2026-09 신설).

신민용 리포트: "지금 2대0인데 우측 보면 골이 3개 어시가 3개로 뜨는데?
저거 플레이어랑 겹치면 저렇게 되는거 같아"

계측 대상 불변식 — 경기 상세의 22명 개인기록(match_details.player_ratings)에서
  (1) sum(goals[home]) == home_score,  sum(goals[away]) == away_score
  (2) sum(assists[side]) <= (그 팀 득점)
  (3) 선수별 assists <= (그 팀 득점 - 그 선수 goals)   ← 자기 골에 자기 어시 금지

전 시즌을 돌리는 대신 _simulate_match()를 직접 N번 호출한다 — 이 함수가
'내가 뛰는 리그 경기' 경로 전체(전술엔진 → _player_perf → 내 슬롯 치환 →
match_details 기록)를 그대로 태우므로, 같은 버그를 수 초 안에 대량으로
재현할 수 있다.

사용:
  python3 tools/ratings_sum_qa.py --db <run.db> [--n 300] [--ovr 80] [--pos CAM]
"""
import _path  # noqa: F401
import argparse
import json
import os
import random
import shutil
import sqlite3
import sys
import time

import database


def _prep(src, work_dir, ovr, pos, team_id=None):
    os.makedirs(work_dir, exist_ok=True)
    db = os.path.join(work_dir, "ratings_qa.db")
    hist_src = os.path.splitext(src)[0] + ".history.db"
    shutil.copy(src, db)
    if os.path.exists(hist_src):
        shutil.copy(hist_src, os.path.splitext(db)[0] + ".history.db")
    c = sqlite3.connect(db)
    c.execute("UPDATE my_player SET ovr=?, position=?, injured=0, "
              "red_card_suspension=0, slump=0", (ovr, pos))
    if team_id:
        # 챔스/유로파/국가대표 경로까지 태우려면 강팀 소속이어야 한다.
        c.execute("UPDATE my_player SET current_team_id=?", (team_id,))
    c.commit()
    c.close()
    return db


def _check(pr, hs, as_):
    """불변식 위반 목록을 돌려준다."""
    bad = []
    for side, score in (("home", hs), ("away", as_)):
        lst = [r for r in (pr.get(side) or []) if r]
        g = sum(int(r.get("goals", 0) or 0) for r in lst)
        a = sum(int(r.get("assists", 0) or 0) for r in lst)
        if g != score:
            bad.append((side, "goal_sum", g, score))
        if a > score:
            bad.append((side, "assist_sum", a, score))
        for r in lst:
            rg = int(r.get("goals", 0) or 0)
            ra = int(r.get("assists", 0) or 0)
            if ra > max(0, score - rg):
                bad.append((side, "self_assist", f"{r.get('name')} g{rg} a{ra}", score))
    return bad


def run(src_db, n, ovr, pos, work_dir, seed=4242, team_id=None):
    random.seed(seed)
    db = _prep(src_db, work_dir, ovr, pos, team_id)
    database.DB_PATH = db
    os.chdir(work_dir)
    database.init_db()
    import power_ranking
    conn = database.get_conn()
    power_ranking.ensure_power_ranking_tables(conn)
    conn.close()

    import game_engine as ge

    # [계측 목적] 이 계측기가 보려는 건 "내가 실제로 뛴 경기"의 슬롯 치환
    # 결과뿐이다. 벤치/징계 판정은 확률이라 그대로 두면 100경기 중 10여
    # 경기만 played가 되어 표본이 너무 적다 — 그래서 결장 판정만 강제로
    # 끈다(경기 결과/개인기록 계산식 자체는 전혀 건드리지 않는다).
    ge._check_bench = lambda _p, team_avg_ovr=None: False
    ge._check_suspended = lambda _p: (False, 0)

    p = ge.get_player()
    my_tid = p.get("current_team_id") or 0
    conn = database.get_conn()
    row = conn.execute("SELECT id, name, league_id FROM teams WHERE id=?", (my_tid,)).fetchone()
    lg_id = row["league_id"]
    lg_name = conn.execute("SELECT name FROM leagues WHERE id=?", (lg_id,)).fetchone()["name"]
    opps = [r["id"] for r in conn.execute(
        "SELECT id FROM teams WHERE league_id=? AND id<>? ORDER BY id", (lg_id, my_tid))]
    st = ge.get_state()
    week = st["current_week"]
    season = st["current_season"]
    conn.close()

    print(f"[qa] db={db}")
    print(f"[qa] 내 팀={row['name']}(id={my_tid}) 리그={lg_name}(id={lg_id}) "
          f"포지션={pos} OVR={ovr} 상대 후보={len(opps)}팀", flush=True)

    conn = database.get_conn()
    before = conn.execute("SELECT COALESCE(MAX(id),0) FROM match_details").fetchone()[0]
    conn.close()

    total = 0
    viol = {}
    samples = []
    for i in range(n):
        opp = opps[i % len(opps)]
        is_home = (i % 2 == 0)
        info = {"home_id": my_tid if is_home else opp,
                "away_id": opp if is_home else my_tid,
                "is_home": is_home,
                "league_id": lg_id, "league_name": lg_name,
                "season": season}
        try:
            ge.update_player(injured=0, injury_weeks=0, red_card_suspension=0)
        except Exception:
            pass
        p = ge.get_player()
        try:
            ge._simulate_match(p, week, info, day=st.get("current_day") or 100)
        except Exception as e:
            print(f"[qa] 경기 {i} 예외: {type(e).__name__}: {e}")
            continue
        p = ge.get_player()

    conn = database.get_conn()
    rows = conn.execute(
        "SELECT id, home_name, away_name, home_score, away_score, player_ratings "
        "FROM match_details WHERE id>? ORDER BY id", (before,)).fetchall()
    conn.close()

    _no_pr = 0
    _no_me = 0
    for r in rows:
        raw = r["player_ratings"] or ""
        if not raw:
            _no_pr += 1
            continue
        try:
            pr = json.loads(raw)
        except Exception:
            continue
        total += 1
        if not any(e and e.get("is_me") for side in ("home", "away")
                   for e in (pr.get(side) or [])):
            _no_me += 1
        bad = _check(pr, r["home_score"], r["away_score"])
        for side, kind, got, want in bad:
            viol[kind] = viol.get(kind, 0) + 1
            if len(samples) < 12:
                samples.append(
                    f"  #{r['id']} {r['home_name']} {r['home_score']}-{r['away_score']} "
                    f"{r['away_name']} | {side} {kind}: {got} (기대 {want})")

    print(f"\n[결과] 검사한 경기 {total}건 "
          f"(player_ratings 없음 {_no_pr}건, is_me 슬롯 없음 {_no_me}건)")
    if not viol:
        print("  위반 0건 — 불변식 만족")
    else:
        for k in ("goal_sum", "assist_sum", "self_assist"):
            if k in viol:
                print(f"  {k}: {viol[k]}건 ({viol[k]*100.0/max(1,total):.1f}%)")
    if samples:
        print("\n[샘플]")
        for s in samples:
            print(s)
    return total, viol


def run_season(src_db, days, ovr, pos, work_dir, seed=9191, team_id=None,
               no_reconcile=False):
    """리그뿐 아니라 국내컵/3·4부컵/챔스·유로파·컨퍼런스/클럽월드컵/
    국가대표까지 전부 태우기 위해 실제로 하루씩 진행한다 — 각 대회 엔진이
    자기 경로로 merge_my_slot을 부르는지, 그 결과가 정합적인지 확인한다.
    대회별로 나눠서 집계한다(match_details.league_name 기준)."""
    random.seed(seed)
    db = _prep(src_db, work_dir, ovr, pos, team_id)
    database.DB_PATH = db
    os.chdir(work_dir)
    database.init_db()
    import power_ranking
    conn = database.get_conn()
    power_ranking.ensure_power_ranking_tables(conn)
    conn.close()

    import game_engine as ge
    import intl_engine

    # [계측 목적] 결장 판정(벤치/징계)만 강제로 끈다 — 경기 결과/개인기록
    # 계산식은 전혀 안 건드린다. 각 대회 엔진은 game_engine에서 이 두 함수를
    # `from game_engine import ...`로 이름 바인딩해 가지고 있으므로, 엔진
    # 모듈들을 먼저 import한 뒤 그 모듈들의 이름까지 같이 덮어써야 한다.
    _patch_targets = [ge, intl_engine]
    for _mod in ("competition.champions_engine", "competition.europa_engine",
                 "competition.conference_engine", "competition.cup_engine",
                 "competition.lower_cup_engine", "competition.club_world_cup_engine",
                 "competition.super_cup_engine", "competition.domestic_super_cup_engine",
                 "competition.competition_common", "promotion_playoff_engine"):
        try:
            _patch_targets.append(__import__(_mod, fromlist=["*"]))
        except Exception as _e:
            print(f"[qa] {_mod} import 실패: {_e}")
    _nobench = lambda *_a, **_k: False            # noqa: E731
    _nosusp = lambda *_a, **_k: (False, 0)        # noqa: E731
    for _t in _patch_targets:
        if hasattr(_t, "_check_bench"):
            _t._check_bench = _nobench
        if hasattr(_t, "_check_suspended"):
            _t._check_suspended = _nosusp

    if no_reconcile:
        # [A/B] 수정 전 동작 재현 — merge_my_slot의 정합성 복원 단계만 끈다
        # (치환 자체는 그대로). 이게 예전 코드와 정확히 같은 동작이다.
        import competition.competition_common as _cc
        _cc.reconcile_side_stats = lambda *_a, **_k: None
        print("[qa] --no-reconcile: 정합성 복원 OFF (수정 전 동작)")

    p = ge.get_player()
    st = ge.get_state()
    print(f"[qa] db={db}")
    print(f"[qa] 시작 연도={st['current_year']} 주차={st['current_week']} "
          f"일차={st.get('current_day')} 포지션={pos} OVR={ovr}", flush=True)

    conn = database.get_conn()
    before = conn.execute("SELECT COALESCE(MAX(id),0) FROM match_details").fetchone()[0]
    conn.close()

    # tools/bugfix_qa_runner.py와 같은 패턴 — 국가대표 소집 선택이 걸리면
    # 거절하고 남은 일정으로 이어서 진행한다.
    # [핵심] 전 시즌을 '휴식'만으로 돌리면 내 경기가 한 건도 안 생긴다 —
    # advance_days는 그 날 일정이 ("경기", detail)일 때만 각 대회의
    # simulate_my_* 경로를 태우기 때문이다(아니면 sim_my_*_as_ai로 대체).
    # 실제 게임에서 그 일정을 만드는 건 UI의 CenterPanel._get_match_for_day
    # 인데, 이 메서드는 self를 전혀 안 쓰므로 언바운드로 그대로 재사용할 수
    # 있다 — 화면과 완전히 같은 규칙으로 "그 날 내 경기"를 찾는다.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from ui.center_panel import CenterPanel
        _match_for_day = CenterPanel._get_match_for_day
    except Exception as e:
        print(f"[qa] center_panel import 실패({e}) — 휴식 일정으로 폴백")
        _match_for_day = None

    def _week_sched(day0):
        out = []
        _st = ge.get_state()
        _p = ge.get_player()
        for i in range(7):
            d = day0 + i
            if d > 364:
                break
            mi = None
            if _match_for_day is not None:
                try:
                    mi = _match_for_day(None, d, _p, st=_st)
                except Exception:
                    mi = None
                if mi and mi.get("pending"):
                    mi = None
            out.append((d, "경기", mi) if mi else (d, "휴식", {}))
        return out

    start_day = int(st.get("current_day") or 1)
    done = 0
    t0 = time.time()
    n_match_days = 0
    # 1) 현재 연도를 끝까지 넘겨 새 시즌으로 진입 → 2) 새 시즌 days일까지.
    for phase, (lo, hi) in enumerate([(start_day, 364), (1, min(364, days))]):
        cur = max(lo, int(ge.get_state().get("current_day") or lo))
        if cur > hi:
            continue
        while cur <= hi:
            sched = [it for it in _week_sched(cur) if it[0] <= hi]
            if not sched:
                break
            n_match_days += sum(1 for it in sched if it[1] == "경기")
            try:
                ge.advance_days(sched)
            except Exception as e:
                print(f"[qa] advance_days 예외 @day{cur}: {type(e).__name__}: {e}")
                break
            pending = intl_engine.get_pending_choice()
            if pending:
                for opt in pending.get("options", []):
                    intl_engine.decline_national_team(opt["tournament_id"])
            nxt = int(ge.get_state().get("current_day") or (cur + 7))
            if nxt <= cur:
                nxt = cur + 7
            done += nxt - cur
            cur = nxt
        _s = ge.get_state()
        print(f"[qa] 구간{phase+1} ~{hi}일차 완료 (year {_s['current_year']} "
              f"season {_s['current_season']} day {_s.get('current_day')}) "
              f"내 경기일 누적 {n_match_days} / {time.time()-t0:.0f}s", flush=True)

    conn = database.get_conn()
    rows = conn.execute(
        "SELECT id, league_name, home_name, away_name, home_score, away_score, "
        "player_ratings, detail_json FROM match_details WHERE id>? ORDER BY id",
        (before,)).fetchall()
    conn.close()

    per = {}
    samples = []
    et_n = 0
    subs_n = 0
    sub_entry_n = 0
    et_samples = []
    for r in rows:
        raw = r["player_ratings"] or ""
        key = r["league_name"] or "(무명)"
        d = per.setdefault(key, {"n": 0, "nopr": 0, "bad": 0})
        if not raw:
            d["nopr"] += 1
            continue
        try:
            pr = json.loads(raw)
        except Exception:
            continue
        d["n"] += 1
        try:
            _pl = json.loads(r["detail_json"] or "{}")
        except Exception:
            _pl = {}
        _sb = _pl.get("subs") or {}
        _cnt = len(_sb.get("home") or []) + len(_sb.get("away") or [])
        subs_n += _cnt
        if _cnt:
            sub_entry_n += 1
        if _pl.get("went_extra_time"):
            et_n += 1
            if len(et_samples) < 4:
                _s90 = _pl.get("score_90") or []
                et_samples.append(
                    f"  [{key}] {r['home_name']} 정규 {_s90[0] if _s90 else '?'}"
                    f"-{_s90[1] if len(_s90)>1 else '?'} → 연장 "
                    f"{r['home_score']}-{r['away_score']} {r['away_name']}")
        # 교체 선수도 평점표에 들어왔는지
        for _side in ("home", "away"):
            for _e in (pr.get(_side) or []):
                if _e and _e.get("started") is False:
                    d["subs_in_ratings"] = d.get("subs_in_ratings", 0) + 1
        bad = _check(pr, r["home_score"], r["away_score"])
        if bad:
            d["bad"] += 1
            if len(samples) < 12:
                side, kind, got, want = bad[0]
                samples.append(f"  [{key}] #{r['id']} {r['home_name']} "
                               f"{r['home_score']}-{r['away_score']} {r['away_name']} "
                               f"| {side} {kind}: {got} (기대 {want})")

    print(f"\n[결과] {done}일 진행, 새 경기상세 {len(rows)}건")
    print(f"{'대회':<26}{'검사':>6}{'평점없음':>9}{'위반':>6}")
    tot_n = tot_bad = 0
    for key in sorted(per, key=lambda k: -per[k]["n"]):
        d = per[key]
        tot_n += d["n"]
        tot_bad += d["bad"]
        print(f"{key[:26]:<26}{d['n']:>6}{d['nopr']:>9}{d['bad']:>6}")
    print(f"{'합계':<26}{tot_n:>6}{'':>9}{tot_bad:>6}")
    print(f"\n[교체/연장] 교체 기록이 있는 경기 {sub_entry_n}/{tot_n}, "
          f"교체 총 {subs_n}건, 연장 경기 {et_n}건")
    _sir = sum(d.get("subs_in_ratings", 0) for d in per.values())
    print(f"[평점표] 교체 투입 선수 항목 {_sir}건")
    if et_samples:
        print("[연장 샘플]")
        for s_ in et_samples:
            print(s_)
    if samples:
        print("\n[샘플]")
        for s in samples:
            print(s)
    return tot_n, tot_bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--ovr", type=int, default=80)
    ap.add_argument("--pos", default="CAM")
    ap.add_argument("--work", default=None)
    ap.add_argument("--no-reconcile", action="store_true",
                    help="정합성 복원만 끄고 수정 전 동작을 재현(A/B 비교용)")
    ap.add_argument("--team", type=int, default=0, help="내 팀 team_id 강제 지정")
    ap.add_argument("--season-days", type=int, default=0,
                    help=">0이면 _simulate_match 직접 호출 대신 그만큼 실제로 "
                         "하루씩 진행해서 전 대회 경로를 계측한다")
    args = ap.parse_args()
    work = args.work or os.path.join(os.path.dirname(os.path.abspath(args.db)), "ratings_qa")
    if args.season_days > 0:
        run_season(os.path.abspath(args.db), args.season_days, args.ovr, args.pos,
                   os.path.abspath(work), team_id=args.team or None,
                   no_reconcile=args.no_reconcile)
    else:
        run(os.path.abspath(args.db), args.n, args.ovr, args.pos, os.path.abspath(work),
            team_id=args.team or None)


if __name__ == "__main__":
    main()