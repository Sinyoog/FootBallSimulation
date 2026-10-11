# -*- coding: utf-8 -*-
"""manager_growth_longrun.py — 6번 ④ 감독 능력 성장/하락 + 은퇴 곡선 장기 검증 (2026-10 신설)

같은 시작 세이브(새 게임 2000년)에서 전체 게임을 N시즌 돌리며, 해가 바뀔 때마다
(감독 시장이 끝난 직후) 감독 전원의 상태를 저장한다. 기준선(④ 끔)과 ④ 켬을 같은
시작 세이브·같은 시드로 따로 돌려 비교한다 — 한 번에 하나만 바꾼다.

사용법
  1) 시작 세이브 만들기(한 번):
       python tools/manager_growth_longrun.py init <base_dir>
  2) 실행(같은 base_dir을 두 모드로):
       python tools/manager_growth_longrun.py run <base_dir> <out_dir> <years> --mode base|new
     중간에 끊겨도 out_dir에 매년 세이브가 남아 같은 명령으로 이어 돈다
     (끊긴 그 해는 처음부터 다시 — 해 단위로만 저장).
  3) 비교:
       python tools/manager_growth_longrun.py report <out_dir_base> <out_dir_new>

  --mode base : MANAGER_GROWTH_ENABLED=False, MANAGER_RETIRE_CURVE="legacy" (④ 이전과 같음)
  --mode new  : constants 그대로(④ 켬)

out_dir/snap.sqlite 표 snap(year, id, birth_year, retired, status, ability, potential,
reputation, from_player) — 매년 감독 전원. 표 top(year, top50_ab, top100_ab) — 팀 OVR 상위
클럽 현직 감독 평균 능력. 표 market(year, json) — 그 해 감독 시장 통계.
"""
import _path  # noqa: F401
import argparse
import json
import os
import random
import shutil
import sqlite3
import statistics
import sys
import time

ROOT = _path.ROOT
DAYS_PER_YEAR = 364
CHUNK_DAYS = 30


def _copy_save(src_dir, dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    for f in ("game.db", "game.history.db", "game.history.db-wal", "game.history.db-shm"):
        s = os.path.join(src_dir, f)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(dst_dir, f))


def _open_game(work_dir):
    import database
    database.DB_PATH = os.path.join(work_dir, "game.db")
    os.chdir(work_dir)
    database.flush_to_disk_async = lambda: None
    database.reset_conn_pool()
    database.init_db()
    return database


def cmd_init(args):
    base = os.path.abspath(args.base_dir)
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    shutil.copy(os.path.join(ROOT, "game.db"), os.path.join(base, "game.db"))
    if getattr(args, "nat_contract_v2_off", False):
        import constants as _C
        _C.MANAGER_NAT_CONTRACT_V2 = False   # 실험 D 기준선 — 시드 계약도 예전 4~7년
    random.seed(12345)
    database = _open_game(base)
    t0 = time.time()
    database.reset_game_data()
    import game_engine as ge
    if not ge.get_player():
        ge.create_player(name="Headless Dummy", position="CM", sub_role="")
    database.flush_to_disk(final=True)
    st = ge.get_state()
    print(f"[init] 새 게임 {st.get('current_year')}년 {st.get('current_day')}일 — "
          f"{time.time() - t0:.1f}s, salt={database.get_world_salt()}", flush=True)


def _snapshot(conn, snap, year, market):
    rows = conn.execute(
        """SELECT id, birth_year, retired, status, COALESCE(manager_ability,0),
                  COALESCE(manager_potential,0), COALESCE(reputation,0),
                  CASE WHEN from_player_id IS NULL THEN 0 ELSE 1 END
           FROM managers""").fetchall()
    snap.execute("DELETE FROM snap WHERE year=?", (year,))
    snap.executemany("INSERT INTO snap VALUES(?,?,?,?,?,?,?,?,?)",
                     [(year,) + tuple(r) for r in rows])
    import manager_ability as mab
    tovr = mab.team_ovr_map(conn)
    order = sorted(tovr, key=lambda t: -tovr[t])
    cur = {r[0]: r[1] or 0 for r in conn.execute(
        """SELECT tm.team_id, m.manager_ability FROM team_managers tm
           JOIN managers m ON m.id = tm.manager_id
           WHERE tm.end_year IS NULL AND tm.job_kind='club'""")}
    t50 = [cur[t] for t in order[:50] if t in cur]
    t100 = [cur[t] for t in order[:100] if t in cur]
    snap.execute("DELETE FROM top WHERE year=?", (year,))
    snap.execute("INSERT INTO top VALUES(?,?,?)",
                 (year, statistics.mean(t50) if t50 else None,
                  statistics.mean(t100) if t100 else None))
    snap.execute("DELETE FROM market WHERE year=?", (year,))
    snap.execute("INSERT INTO market VALUES(?,?)",
                 (year, json.dumps(market, ensure_ascii=False, default=str)))
    # [2026-10 실험 C 관찰] 지난 해(year-1) 대표팀 평가 카운터 + "평가 없이 만료" — 정식 계약이
    # 지난 해 만료(contract_until=year-1)인데 그 해 한 번도 평가받지 않고 아직 재임 중인 건수.
    import national_manager as _nmm
    _yb = int(year) - 1
    _o = dict(_nmm.NAT_OBS.get(_yb, {}) or {})
    _ev = set(_o.pop("evaluated_links", []) or [])
    _exp = [r[0] for r in conn.execute(
        """SELECT id FROM national_team_managers WHERE role='full' AND end_year IS NULL
             AND contract_until=?""", (_yb,))]
    _o["expire_no_eval"] = sum(1 for i in _exp if i not in _ev)
    _o["expire_total"] = len(_exp)
    _o["contract_len"] = [r[0] for r in conn.execute(
        "SELECT contract_log FROM national_team_managers WHERE role='full' AND contract_log LIKE ?",
        (f"%{_yb}:%",))]
    snap.execute("CREATE TABLE IF NOT EXISTS natobs(year INT, js TEXT)")
    snap.execute("DELETE FROM natobs WHERE year=?", (_yb,))
    snap.execute("INSERT INTO natobs VALUES(?,?)", (_yb, json.dumps(_o, ensure_ascii=False)))
    _o["len_obs"] = dict(getattr(_nmm, "NAT_LEN_OBS", {}) or {})
    snap.execute("DELETE FROM natobs WHERE year=?", (_yb,))
    snap.execute("INSERT INTO natobs VALUES(?,?)", (_yb, json.dumps(_o, ensure_ascii=False)))
    _nmm.NAT_OBS.clear()
    if hasattr(_nmm, "NAT_LEN_OBS"):
        _nmm.NAT_LEN_OBS.clear()
    # [2026-10] 국대 브레이크아웃 기록(관찰 전용) — 지난 시즌 동안 쌓인 것을 옮기고 비운다.
    import database as _dbm
    _bl = list(getattr(_dbm, "INTL_BREAKOUT_LOG", []) or [])
    snap.execute("CREATE TABLE IF NOT EXISTS breakout(year INT, js TEXT)")
    snap.execute("DELETE FROM breakout WHERE year=?", (year,))
    snap.execute("INSERT INTO breakout VALUES(?,?)", (year, json.dumps(_bl, ensure_ascii=False)))
    if hasattr(_dbm, "INTL_BREAKOUT_LOG"):
        _dbm.INTL_BREAKOUT_LOG.clear()
    # [2026-10 감독 시장 ② 관찰] 대표팀 부임 기록(--nt-hire-trace) — 지난 시즌분을 옮기고 비운다.
    _nh = list(getattr(_nmm, "NT_HIRE_OBS", []) or [])
    snap.execute("CREATE TABLE IF NOT EXISTS nthire(year INT, js TEXT)")
    snap.execute("DELETE FROM nthire WHERE year=?", (year,))
    snap.execute("INSERT INTO nthire VALUES(?,?)", (year, json.dumps(_nh, ensure_ascii=False)))
    if hasattr(_nmm, "NT_HIRE_OBS"):
        _nmm.NT_HIRE_OBS.clear()
    snap.commit()


_CHILD_ENV = "FBS_LONGRUN_CHILD"


def _drive_children(args):
    """[2026-10] 연 1프로세스 드라이버 — 해마다 자식 프로세스를 새로 띄워 1시즌씩 돌린다.

    왜: 게임은 시즌 전환 때만 비우는 메모리 캐시(_team_ovr_cache 등)를 들고 있어서,
    한 프로세스로 여러 해를 도는 런과 중간에 끊겨 디스크에서 이어 돈 런의 결과가
    갈라졌다(실측: 이어 돈 지점 뒤 57일째 리그 경기 4개 점수부터 다름 — 난수 상태와
    선수·팀·감독 표는 그때까지 같았다). 매년 디스크에서 새로 시작하면 안 끊긴 런과
    끊겼다 이어 돈 런이 구조적으로 같아진다. 덤으로 해마다 메모리가 초기화되고(한
    프로세스로 7시즌이면 최대 약 1.27GB), 한 번에 한 시즌(약 2분)씩만 돈다.
    자식은 같은 명령줄 + 환경변수로 실행하고, 시작 때 random.seed(seed + 완료 연수)를
    잡는다(cmd_run 그대로). 이 구조라 2년차부터는 예전 단일 프로세스 러너와 결과가
    다르다 — 기준선과 실험군은 같은 방식으로 돌릴 것(예전 방식: --single-process)."""
    import subprocess
    out = os.path.abspath(args.out_dir)
    progress = os.path.join(out, "progress.json")
    env = dict(os.environ)
    env[_CHILD_ENV] = "1"
    cmd = [sys.executable, os.path.abspath(__file__)] + sys.argv[1:]
    while True:
        done = json.load(open(progress))["years_done"] if os.path.exists(progress) else 0
        if done >= args.years:
            break
        rc = subprocess.call(cmd, env=env)
        if rc != 0:
            print(f"[run] 자식 프로세스 실패(코드 {rc}) — {done}년 완료 상태에서 중단. "
                  f"같은 명령으로 다시 실행하면 이어 돈다", flush=True)
            sys.exit(rc)
        after = json.load(open(progress))["years_done"] if os.path.exists(progress) else 0
        if after <= done:
            print("[run] 자식 프로세스가 한 해도 진행하지 못함 — 중단", flush=True)
            sys.exit(1)
    print("[run] 끝(연 1프로세스)", flush=True)


def cmd_run(args):
    if not getattr(args, "single_process", False) and os.environ.get(_CHILD_ENV) != "1":
        return _drive_children(args)
    base = os.path.abspath(args.base_dir)
    out = os.path.abspath(args.out_dir)
    progress = os.path.join(out, "progress.json")
    done = 0
    if os.path.exists(progress):
        done = json.load(open(progress))["years_done"]
        print(f"[run] 이어 돌림 — 이미 {done}년 완료", flush=True)
    else:
        shutil.rmtree(out, ignore_errors=True)
        _copy_save(base, out)
    import constants as C
    if args.mode == "base":
        C.MANAGER_GROWTH_ENABLED = False
        C.MANAGER_RETIRE_CURVE = "legacy"
    if args.perf_basis:
        C.MANAGER_GROWTH_PERF_BASIS = args.perf_basis
    if args.trace:
        C.MANAGER_MOVE_TRACE = True     # ⑤ 계측(순수 관찰)
    if args.extra:
        C.MANAGER_EXTRA_SLOT_ENABLED = True   # ⑤-1 상위 자리 국적 무관 추가 칸
        C.MANAGER_EXTRA_SLOT_MODE = args.extra_mode
    if args.gain:
        C.MANAGER_EFFECTIVE_GAIN_ENABLED = True   # ⑤-3 실질 이득
    if getattr(args, "split", False):
        C.MANAGER_POOL_SPLIT_ENABLED = True       # ⑤-2 상위 자리 후보 정원 무직/현직
    if getattr(args, "jobless_window", False):
        C.MANAGER_JOBLESS_WINDOW_ENABLED = True   # ⑤-4 1차 무직 기간별 후보 창 완화
    if getattr(args, "above_slot", False):
        C.MANAGER_ABOVE_SLOT_ENABLED = True       # ⑤-4 무직 풀 위에서 내려오기 예약 칸
    if getattr(args, "breakout_persist", False):
        C.INTL_BREAKOUT_PERSIST_ENABLED = True    # 국대 브레이크아웃 영구화
    if getattr(args, "nt_mixed", False):
        C.MANAGER_NT_MIXED_POOL_ENABLED = True    # ⑤-4-② 1차 S/A급 대표팀 무직+현직 한 풀
    if getattr(args, "nt_grade_fit", False):
        C.MANAGER_NT_GRADE_FIT_ENABLED = True     # ⑤-4-② 2차 대표팀 등급별 능력 선호
    if getattr(args, "nat_contract_v2_off", False):
        C.MANAGER_NAT_CONTRACT_V2 = False           # 실험 D 기준선(예전 동작)
    if getattr(args, "nat_contract_legacy", False):
        C.MANAGER_NAT_CONTRACT_EVAL_ALIGN = False   # 실험 C 기준선(예전 동작)
    if getattr(args, "rookie_best_year_off", False):
        C.MANAGER_ROOKIE_SET_BEST_YEAR = False    # 실험 B 기준선(예전 동작)
    if getattr(args, "nt_club_accept", None):
        # 감독 시장 ② S/A 현직 수락 판정 — 채택 후 기본값은 walk, "off"는 채택 전 기준선
        C.MANAGER_NT_CLUB_ACCEPT_MODE = None if args.nt_club_accept == "off" else args.nt_club_accept
    if getattr(args, "lineup_unify", False):
        C.LINEUP_UNIFY_ENABLED = True             # ⑦-c 시즌 주전 기록·화면 선발 = 경기 전력 최적 배치
    if getattr(args, "lineup_unify_off", False):
        C.LINEUP_UNIFY_ENABLED = False            # ⑦-c 채택 전 기준선(예전 그리디 + 나이 보정)
    if getattr(args, "nt_hire_trace", False):
        C.MANAGER_NT_HIRE_TRACE = True            # 감독 시장 ② 관찰(순수 기록)
    if getattr(args, "nt_off", False):
        # ⑤-4-② 확정안(기본 켬) 이전 기준선 — 혼합 풀·등급 선호·현직 정원·굴림 끔 전부 끔
        C.MANAGER_NT_MIXED_POOL_ENABLED = False
        C.MANAGER_NT_GRADE_FIT_ENABLED = False
        C.MANAGER_NT_MIXED_CLUB_CAP = 0
        C.MANAGER_NT_MIXED_SKIP_DOWN_ROLL = False
    print(f"[run] mode={args.mode} perf_basis={getattr(C, 'MANAGER_GROWTH_PERF_BASIS', '-')} "
          f"trace={getattr(C, 'MANAGER_MOVE_TRACE', False)} "
          f"extra={getattr(C, 'MANAGER_EXTRA_SLOT_ENABLED', False)}"
          f"({getattr(C, 'MANAGER_EXTRA_SLOT_MODE', '-')}) "
          f"gain={getattr(C, 'MANAGER_EFFECTIVE_GAIN_ENABLED', False)} "
          f"split={getattr(C, 'MANAGER_POOL_SPLIT_ENABLED', False)}"
          f"({getattr(C, 'MANAGER_POOL_SPLIT_FREE', '-')}/{getattr(C, 'MANAGER_POOL_SPLIT_MOVE', '-')}) "
          f"jobless_window={getattr(C, 'MANAGER_JOBLESS_WINDOW_ENABLED', False)} "
          f"above_slot={getattr(C, 'MANAGER_ABOVE_SLOT_ENABLED', False)} "
          f"nt_mixed={getattr(C, 'MANAGER_NT_MIXED_POOL_ENABLED', False)} "
          f"nt_grade_fit={getattr(C, 'MANAGER_NT_GRADE_FIT_ENABLED', False)} "
          f"nt_cap={getattr(C, 'MANAGER_NT_MIXED_CLUB_CAP', '-')} "
          f"nt_skip_roll={getattr(C, 'MANAGER_NT_MIXED_SKIP_DOWN_ROLL', '-')} "
          f"breakout_persist={getattr(C, 'INTL_BREAKOUT_PERSIST_ENABLED', False)} "
          f"rookie_bly={getattr(C, 'MANAGER_ROOKIE_SET_BEST_YEAR', '-')} "
          f"nat_align={getattr(C, 'MANAGER_NAT_CONTRACT_EVAL_ALIGN', '-')} "
          f"nat_v2={getattr(C, 'MANAGER_NAT_CONTRACT_V2', '-')}", flush=True)
    random.seed(int(getattr(args, "seed", 12345)) + done)
    database = _open_game(out)
    import game_engine as ge
    import intl_engine
    import ai_lifecycle as al

    snap = sqlite3.connect(os.path.join(out, "snap.sqlite"))
    snap.execute("""CREATE TABLE IF NOT EXISTS snap(year INT, id INT, birth_year INT,
                    retired INT, status TEXT, ability INT, potential INT, reputation REAL,
                    from_player INT)""")
    snap.execute("CREATE TABLE IF NOT EXISTS top(year INT, top50 REAL, top100 REAL)")
    snap.execute("CREATE TABLE IF NOT EXISTS market(year INT, js TEXT)")
    conn = database.get_conn()
    if done == 0:
        _snapshot(conn, snap, ge.get_state().get("current_year"), {})

    def chunk(start, n):
        o, d = [], start
        for _ in range(n):
            o.append((d, "휴식", {}))
            d = d + 1 if d < DAYS_PER_YEAR else 1
        return o

    t0 = time.time()
    while done < args.years:
        yb = ge.get_state().get("current_year")
        ge.advance_days(chunk(ge.get_state().get("current_day") or 1, CHUNK_DAYS))
        pending = intl_engine.get_pending_choice()
        while pending:
            for opt in pending.get("options", []):
                intl_engine.decline_national_team(opt["tournament_id"])
            ge.advance_days(chunk(ge.get_state().get("current_day") or 1, CHUNK_DAYS))
            pending = intl_engine.get_pending_choice()
        ya = ge.get_state().get("current_year")
        if ya != yb:
            done += 1
            conn = database.get_conn()
            _snapshot(conn, snap, ya, dict(getattr(al, "_LAST_MANAGER_MARKET", {}) or {}))
            database.flush_to_disk(final=True)
            json.dump({"years_done": done, "year": ya, "mode": args.mode}, open(progress, "w"))
            if os.environ.get(_CHILD_ENV) == "1":
                print(f"[run:{args.mode}] {yb}→{ya} 완료 ({done}/{args.years}, "
                      f"{time.time() - t0:.0f}s, 연 1프로세스)", flush=True)
                break
            print(f"[run:{args.mode}] {yb}→{ya} 완료 ({done}/{args.years}, 누적 "
                  f"{time.time() - t0:.0f}s)", flush=True)
    snap.close()
    print("[run] 끝", flush=True)


# ── 보고 ────────────────────────────────────────────────────────────
def _load(out):
    s = sqlite3.connect(os.path.join(out, "snap.sqlite"))
    years = [r[0] for r in s.execute("SELECT DISTINCT year FROM snap ORDER BY year")]
    by = {}
    for y in years:
        by[y] = {r[0]: r[1:] for r in s.execute(
            "SELECT id, birth_year, retired, status, ability, potential, reputation, from_player "
            "FROM snap WHERE year=?", (y,))}
    top = {r[0]: (r[1], r[2]) for r in s.execute("SELECT year, top50, top100 FROM top")}
    mk = {r[0]: json.loads(r[1]) for r in s.execute("SELECT year, js FROM market")}
    return years, by, top, mk


def _summary(out):
    years, by, top, mk = _load(out)
    res = {"per_year": [], "rookie": [], "cohort60": None}
    y0 = years[0]
    first_seen = {}
    for y in years:
        for mid in by[y]:
            first_seen.setdefault(mid, y)
    cohort = [mid for mid, v in by[y0].items()
              if not v[1] and 60 <= y0 - int(v[0] or y0) <= 61]
    for i, y in enumerate(years):
        cur = by[y]
        act = {m: v for m, v in cur.items() if not v[1]}
        abil = sorted((v[3] for v in act.values()), reverse=True)
        ages = [y - int(v[0] or y) for v in act.values()]
        row = {"year": y, "active": len(act),
               "top50_club": round(top.get(y, (None, None))[0] or 0, 2),
               "top100_club": round(top.get(y, (None, None))[1] or 0, 2),
               "world_top50": round(statistics.mean(abil[:50]), 2) if len(abil) >= 50 else None,
               "n90": sum(1 for a in abil if a >= 90), "n85": sum(1 for a in abil if a >= 85),
               "n80": sum(1 for a in abil if a >= 80), "n95": sum(1 for a in abil if a >= 95),
               "age60p": sum(1 for a in ages if a >= 60), "age70p": sum(1 for a in ages if a >= 70),
               "age_mean": round(statistics.mean(ages), 2) if ages else None}
        row["share70p"] = round(row["age70p"] / max(1, len(act)) * 100, 2)
        if i > 0:
            prev = by[years[i - 1]]
            new_ids = [m for m in cur if m not in prev]
            rk = [cur[m] for m in new_ids]
            row["rookies"] = len(rk)
            row["rk_ab_mean"] = round(statistics.mean(v[3] for v in rk), 2) if rk else None
            row["rk_80"] = sum(1 for v in rk if v[3] >= 80)
            row["rk_85"] = sum(1 for v in rk if v[3] >= 85)
            row["rk_90"] = sum(1 for v in rk if v[3] >= 90)
            row["rk_p90"] = sum(1 for v in rk if v[4] >= 90)
            # 은퇴 — 작년 현역 → 올해 은퇴
            ret = [(m, y - int(prev[m][0] or y)) for m in prev
                   if not prev[m][1] and m in cur and cur[m][1]]
            row["retired"] = len(ret)
            row["ret_age_mean"] = round(statistics.mean(a for _m, a in ret), 2) if ret else None
            # 나이대별 한 해 능력 변화(작년·올해 모두 현역)
            dlt = {}
            for m, v in act.items():
                if m in prev and not prev[m][1]:
                    a = (y - 1) - int(prev[m][0] or y)
                    band = ("<45" if a < 45 else "45-54" if a < 55 else "55-59" if a < 60
                            else "60-64" if a < 65 else "65-69" if a < 70 else "70-74"
                            if a < 75 else "75+")
                    dlt.setdefault(band, []).append(v[3] - prev[m][3])
            row["delta_by_age"] = {b: round(statistics.mean(x), 3) for b, x in sorted(dlt.items())}
        mkt = mk.get(y) or {}
        row["market"] = {k: mkt.get(k) for k in ("vacancies", "rookie", "moved", "rehired",
                                                  "sacked", "retired_age", "retired_jobless")}
        if isinstance(mkt.get("ability_prog"), dict):
            row["prog"] = mkt["ability_prog"]
        if cohort:
            alive = sum(1 for m in cohort if m in cur and not cur[m][1])
            row["cohort60_active_pct"] = round(alive / len(cohort) * 100, 1)
        res["per_year"].append(row)
    res["cohort60_n"] = len(cohort)
    # 잠재 90+ 감독의 90+ 도달
    last = years[-1]
    pot90 = {}
    for y in years:
        for m, v in by[y].items():
            if v[4] >= 90 and not v[1]:
                pot90.setdefault(m, y)
    reach = {}
    for m, y_in in pot90.items():
        for y in years:
            if y >= y_in and m in by[y] and by[y][m][3] >= 90:
                reach[m] = y - y_in
                break
    already = sum(1 for m, d in reach.items() if d == 0)
    res["pot90_n"] = len(pot90)
    res["pot90_reached"] = len(reach)
    res["pot90_reached_new"] = len(reach) - already
    later = [d for d in reach.values() if d > 0]
    res["pot90_seasons_to_reach_mean"] = round(statistics.mean(later), 2) if later else None
    # 은퇴 감독 마지막 능력 최고치 등 참고
    res["last_year"] = last
    return res


def cmd_report(args):
    sa = _summary(os.path.abspath(args.base_out))
    sb = _summary(os.path.abspath(args.new_out))
    out = {"base": sa, "new": sb}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(out, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("init")
    a.add_argument("base_dir")
    a.add_argument("--nat-contract-v2-off", dest="nat_contract_v2_off", action="store_true",
                   help="실험 D 기준선 — 시드 대표팀 계약을 예전 4~7년으로")
    b = sp.add_parser("run")
    b.add_argument("base_dir")
    b.add_argument("out_dir")
    b.add_argument("years", type=int)
    b.add_argument("--mode", choices=("base", "new"), required=True)
    b.add_argument("--perf-basis", choices=("recent", "season"), default=None,
                   help="④ 성적 보정 기준(기본: constants 값)")
    b.add_argument("--trace", action="store_true", help="⑤ 상위 50 이동 경로 계측 켬")
    b.add_argument("--extra", action="store_true", help="⑤-1 상위 자리 국적 무관 추가 칸 켬")
    b.add_argument("--gain", action="store_true", help="⑤-3 실질 이득(팀 OVR 차 반영) 켬")
    b.add_argument("--single-process", dest="single_process", action="store_true",
                   help="[2026-10 이전 방식] 한 프로세스로 여러 해를 돈다 — 끊겼다 이어 돌면 "
                        "안 끊긴 런과 결과가 달라진다(_drive_children 주석)")
    b.add_argument("--seed", type=int, default=12345,
                   help="게임 진행 난수 시드(기본 12345 = 예전과 같음). 시드 2개 검증용")
    b.add_argument("--split", action="store_true",
                   help="⑤-2 상위 자리(≥100) 후보 정원 무직 24/현직 12 → 18/18")
    b.add_argument("--breakout-persist", action="store_true",
                   help="국대 브레이크아웃 때 potential_ovr·peak_ovr도 max(기존, 목표)로")
    b.add_argument("--nt-grade-fit", action="store_true",
                   help="⑤-4-② 2차 대표팀 국대 등급별 능력 적합도 배율(S 3.0 … E/F 0.6)")
    b.add_argument("--nat-contract-v2-off", dest="nat_contract_v2_off", action="store_true",
                   help="실험 D 기준선 — 대표팀 계약 4~7년·재계약 0.68 식(예전 동작)")
    b.add_argument("--nat-contract-legacy", dest="nat_contract_legacy", action="store_true",
                   help="실험 C 기준선 — 대표팀 계약 4~7년·예선 탈락 마지막 해 재계약 경로(예전 동작)")
    b.add_argument("--rookie-best-year-off", dest="rookie_best_year_off", action="store_true",
                   help="실험 B 기준선 — 신인 첫 부임 best_level_year 기록 끔(예전 동작)")
    b.add_argument("--nt-off", action="store_true",
                   help="⑤-4-② 확정안 이전 기준선(대표팀 혼합 풀·등급 선호·현직 정원·굴림 끔 전부 끔)")
    b.add_argument("--nt-club-accept", dest="nt_club_accept", choices=("walk", "precap", "off"), default=None,
                   help="감독 시장 ② S/A 대표팀 현직 후보 수락 판정(기본 walk, off = 채택 전 기준선)")
    b.add_argument("--lineup-unify", dest="lineup_unify", action="store_true",
                   help="⑦-c 시즌 주전 기록·화면 선발을 경기 전력 최적 배치로 통일(채택 후 기본값)")
    b.add_argument("--lineup-unify-off", dest="lineup_unify_off", action="store_true",
                   help="⑦-c 끔 — 채택 전 기준선")
    b.add_argument("--nt-hire-trace", dest="nt_hire_trace", action="store_true",
                   help="감독 시장 ② 대표팀 부임 후보·선임 기록(관찰 전용)")
    b.add_argument("--nt-mixed", action="store_true",
                   help="⑤-4 2차 S/A급 대표팀에서만 무직+클럽 현직을 한 후보 풀에서 비교")
    b.add_argument("--above-slot", action="store_true",
                   help="⑤-4 무직 풀 24칸 중 4칸을 최고 경력 ≥ 공석인 무직(평판 순)에게 예약")
    b.add_argument("--jobless-window", action="store_true",
                   help="⑤-4 1차 무직 기간별 career_best 후보 창 완화(25/30/35/40)")
    b.add_argument("--extra-mode", choices=("foreign", "all"), default="foreign",
                   help="--extra 방식: foreign = 외국인 쪽 공석만(⑤-1 변형), all = 원안")
    c = sp.add_parser("report")
    c.add_argument("base_out")
    c.add_argument("new_out")
    c.add_argument("--json", default=None)
    args = ap.parse_args()
    {"init": cmd_init, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()