# -*- coding: utf-8 -*-
"""[2026-10-31] V3.1(constants.SQUAD_BY_FORMATION_V31) 1시즌·N시즌 측정 러너 — 게임 코드는 바꾸지 않는다.
(run_sqv3_season.py를 바탕으로 함. V3 스위치는 항상 끔 — V3는 실패 기록으로 유지.)
추가: 팀별 구성(국가 등급·tier·포메이션·인원·등록 포지션별 인원)을 SQV31_DUMP 파일로 저장 →
tools/eval_sqv31.py가 양쪽 팔을 같은 새 설계 목표표로 판정한다(목표 +1 초과·중원 묶음 초과).
추가 지표: gk4p(GK 4명+ 팀), over_grade_max(등급 최대 인원 초과 팀).

한 번 실행 = 한 팔(arm): 새 임시 폴더에 game.db·game.history.db를 복사 → 같은 시드로 새 월드 생성
(reset_game_data — V3는 월드 생성부터 다르므로 양쪽 다 새로 만든다) → 더미 선수 생성 → N시즌 진행.
월드 생성 직후와 매 시즌 끝에 지표를 재고 JSON으로 출력한다.

환경변수: SQV3_ROOT(코드 폴더, 기본=이 파일의 상위), SQV31(1/0), SQV3_SEED(기본 12345), SQV3_SEASONS(기본 1),
          SQV31_DUMP(팀 구성 저장 파일 경로, 없으면 저장 안 함)
유스팀 A 규칙(AFFILIATE_LADDER_RULES)은 항상 끈다(양쪽 같게).

지표(AI 클럽 = 군팀 제외 전 팀):
  not_formable   지금 포메이션 선발 11명을 등록 포지션·부포로(선수 중복 없이) 못 채우는 팀 수   [필수 1]
  gk_le2         GK 2명 이하 팀 수                                                         [필수 2]
  pos5_pct       CB 외 한 포지션(등록 포지션 기준)에 5명 이상인 팀 비율(%)                     [필수 3]
  under22/over26 22명 미만·26명 초과 팀 수                                                 [필수 4]
  n95            세계 AI 선수 OVR 95 이상 인원                                             [필수 5]
  season_sec     시즌 진행 시간(초, 월드 생성 제외)                                         [필수 6]
  hash           팀·선수·이적 기록·승강 기록 표 해시(OFF = 작업 직전 코드 비교용)              [필수 7]
  관찰: gk 분포, lineup_mismatch(실제 선발에서 칸 포지션과 다른 선수가 서는 팀), 포지션별 5명+ 내역"""
import collections
import contextlib
import hashlib
import io
import json
import os
import random
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("SQV3_ROOT") or os.path.dirname(HERE)
DATA_ROOT = os.path.dirname(HERE)
V3 = os.environ.get("SQV31") == "1"   # 이 러너에서 V3 = V3.1 스위치
DUMP = os.environ.get("SQV31_DUMP")
DETAIL = os.environ.get("SQV3_DETAIL") == "1"   # [진단] 26명 초과 팀 내역·GK 이동 경로·포지션 묶음 지표
_RB = {}
SEED = int(os.environ.get("SQV3_SEED", "12345"))
SEASONS = int(os.environ.get("SQV3_SEASONS", "1"))


def metrics(c, C):
    import squad_targets as ST
    import sub_positions as SP
    import team_plan as TP
    rows = c.execute("""SELECT t.id, t.formation FROM teams t JOIN leagues l ON l.id=t.league_id
                        JOIN countries cn ON cn.id=l.country_id WHERE cn.name!='군대'""").fetchall()
    by = collections.defaultdict(list)
    for r in c.execute("SELECT id, team_id, position, ovr, foot, sub_role FROM ai_players"):
        by[r[1]].append({"id": r[0], "position": r[2], "ovr": r[3], "foot": r[4], "sub_role": r[5]})
    T = lambda p: ST.REGISTERED_TO_TARGET.get(p) if p else None
    gk = collections.Counter()
    nf = mism = u22 = o26 = p5 = 0
    p5pos = collections.Counter()
    n = 0
    for tid, f in rows:
        ro = by.get(tid, [])
        if not ro:
            continue
        SP.attach(ro)
        n += 1
        held = collections.Counter(p["position"] for p in ro)
        gk[min(held["GK"], 5)] += 1
        f = f if f in C.FORMATION_SLOTS else "4-4-2"
        if not ST.formable([(p["position"], p.get("sub_pos")) for p in ro], f):
            nf += 1
        slots = C.FORMATION_SLOTS[f]
        placed = TP.optimal_lineup_slots(ro, slots)
        if any(pl is None or ST.SLOT_TO_TARGET.get(s, s) not in (T(pl.get("position")), T(pl.get("sub_pos")))
               for s, pl in zip(slots, placed)):
            mism += 1
        u22 += len(ro) < 22
        o26 += len(ro) > 26
        big = [k for k, v in held.items() if k != "CB" and v >= 5]
        if big:
            p5 += 1
            for k in big:
                p5pos[k] += 1
    n95 = c.execute("SELECT COUNT(*) FROM ai_players WHERE ovr>=95").fetchone()[0]
    gk4p = sum(v for k, v in gk.items() if k >= 4)
    return {"gk4p": gk4p, "over_grade_max": over_grade_max(c), "teams": n, "not_formable": nf, "gk_le2": gk[0] + gk[1] + gk[2],
            "gk": {k: v for k, v in sorted(gk.items())}, "pos5_pct": round(p5 / n * 100, 2), "pos5_teams": p5,
            "pos5_by_pos": dict(p5pos.most_common()), "under22": u22, "over26": o26, "n95": n95,
            "lineup_mismatch": mism}


def detail(c, C, year):
    """[진단 전용] 26명 초과 팀 내역, GK 이동 경로, 포지션 묶음 지표(후보 지표 — 판정에 쓰지 않음)."""
    import ai_lifecycle as AL
    import squad_targets as ST
    from constants import get_country_league_grade
    out = {}
    me = c.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
    my_tid = me[0] if me else None
    rows = c.execute("""SELECT t.id, t.name, cn.name, t.current_tier, t.classification_status, t.formation
                        FROM teams t JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id
                        WHERE cn.name!='군대'""").fetchall()
    size = dict(c.execute("SELECT team_id, COUNT(*) FROM ai_players GROUP BY team_id").fetchall())
    loan_in = dict(c.execute("SELECT team_id, COUNT(*) FROM ai_players WHERE on_loan_from_team_id IS NOT NULL "
                             "AND on_loan_from_team_id!=0 GROUP BY team_id").fetchall())
    o26 = []
    for tid, name, cn, tier, cls, f in rows:
        n = size.get(tid, 0)
        if n <= 26:
            continue
        g = get_country_league_grade(cn)
        lo, hi = AL._squad_min_max(g, tier)
        after_rb = _RB.get("after", {}).get(tid)
        inflow = [tuple(r) for r in c.execute(
            "SELECT id, transfer_type, is_mid_season, is_loan FROM ai_transfer_log WHERE to_team_id=? AND year=? "
            "AND id>? ORDER BY id", (tid, year, _RB.get("max_log", 0))).fetchall()]
        o26.append({"tid": tid, "name": name, "country": cn, "grade": g, "tier": tier, "affiliate": cls == "AFFILIATE",
                    "my_team": tid == my_tid, "size": n, "lo_hi": [lo, hi], "over_hi": n > hi,
                    "size_after_rebalance": after_rb, "loan_in": loan_in.get(tid, 0),
                    "inflow_after_rebalance": collections.Counter(r[1] for r in inflow).most_common()})
    out["over26"] = o26
    gk_moves = collections.Counter(r[0] for r in c.execute(
        "SELECT transfer_type FROM ai_transfer_log WHERE year=? AND player_position='GK'", (year,)))
    out["gk_moves_by_type"] = dict(gk_moves.most_common())
    gkc = dict(c.execute("SELECT team_id, SUM(position='GK') FROM ai_players GROUP BY team_id").fetchall())
    gk4 = {t for t, v in gkc.items() if v and v >= 4}
    inflow4 = collections.Counter(r[0] for r in c.execute(
        "SELECT transfer_type, to_team_id FROM ai_transfer_log WHERE year=? AND player_position='GK'", (year,))
        if r[1] in gk4)
    out["gk4_teams"] = len(gk4 & {r[0] for r in rows})
    out["gk_inflow_into_gk4_teams_by_type"] = dict(inflow4.most_common())
    # 포지션 묶음(후보 지표): 중원 CDM+CM+CAM, 측면 수비 LB+RB, 측면 공격 LW+RW, 최전방 FW
    by = collections.defaultdict(list)
    for r in c.execute("SELECT team_id, position FROM ai_players"):
        by[r[0]].append(r[1])
    over_max = grp_over = pos5_nonmid = 0
    over_max_pos = collections.Counter()
    for tid, name, cn, tier, cls, f in rows:
        ps = by.get(tid, [])
        if not ps:
            continue
        f = f if f in C.FORMATION_SLOTS else "4-4-2"
        held = ST.held_counter(ps)
        tgt = ST.position_targets(f, len(ps))
        bad = [p for p in ST.TARGET_POSITIONS if held[p] > tgt[p] + 1]
        if bad:
            over_max += 1
            over_max_pos.update(bad)
        mid_h = held["CDM"] + held["CM"] + held["CAM"]
        mid_t = tgt["CDM"] + tgt["CM"] + tgt["CAM"]
        if mid_h > mid_t + 1:
            grp_over += 1
        if any(v >= 5 for k, v in held.items() if k not in ("CB", "CDM", "CM", "CAM")):
            pos5_nonmid += 1
    out["teams_pos_over_target_plus1"] = over_max
    out["over_target_plus1_by_pos"] = dict(over_max_pos.most_common())
    out["teams_mid_group_over_target_plus1"] = grp_over
    out["teams_pos5_excluding_CB_and_mid"] = pos5_nonmid
    return out


def team_dump(c):
    """팀별 구성 — [team_id, 국가, 등급, tier, 포메이션, 인원, {등록 포지션(부포 있으면 "주포|부포"): 인원}] (군팀 제외).
    [2026-10-31] 부포를 같이 저장한다 — tools/eval_sqv31.py가 주포·부포 기준(held_assign)으로 판정."""
    from constants import get_country_league_grade
    import sub_positions as SP
    rows = [{"id": r[0], "team_id": r[1], "position": r[2]} for r in c.execute("SELECT id, team_id, position FROM ai_players")]
    SP.attach(rows)
    pos = collections.defaultdict(collections.Counter)
    for d in rows:
        pos[d["team_id"]][d["position"] + ("|" + d["sub_pos"] if d.get("sub_pos") else "")] += 1
    out = []
    for tid, cn, tier, f in c.execute("""SELECT t.id, cn.name, t.current_tier, t.formation FROM teams t
                                         JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id
                                         WHERE cn.name!='군대' ORDER BY t.id""").fetchall():
        if pos.get(tid):
            out.append([tid, cn, get_country_league_grade(cn), tier, f, sum(pos[tid].values()), dict(pos[tid])])
    return out


def over_grade_max(c):
    """등급별 최대 인원(ai_lifecycle._squad_min_max)을 넘는 팀 수."""
    import ai_lifecycle as AL
    return sum(1 for r in team_dump(c) if r[5] > AL._squad_min_max(r[2], r[3])[1])


def table_hash(c):
    h = hashlib.sha256()
    for sql in ("SELECT id, league_id, current_tier, formation FROM teams ORDER BY id",
                "SELECT * FROM ai_players ORDER BY id",
                "SELECT * FROM ai_transfer_log ORDER BY id",
                "SELECT * FROM promotion_log ORDER BY id"):
        try:
            for r in c.execute(sql):
                h.update(repr(tuple(r)).encode())
        except Exception as e:
            h.update(repr(e).encode())
    return h.hexdigest()[:16]


def main():
    tmp = tempfile.mkdtemp(prefix=f"sqv31run_{'on' if V3 else 'off'}_")
    for f in ("game.db", "game.history.db"):
        shutil.copy(os.path.join(DATA_ROOT, f), os.path.join(tmp, f))
    os.chdir(tmp)
    sys.path.insert(0, ROOT)
    import constants as C
    if hasattr(C, "set_squad_by_formation_v3"):
        C.set_squad_by_formation_v3(False)          # V3(실패 기록)는 항상 끈다
    if hasattr(C, "set_squad_by_formation_v31"):
        C.set_squad_by_formation_v31(V3)
    elif V3:
        raise SystemExit("이 코드 폴더에는 V3.1 스위치가 없다")
    if hasattr(C, "AFFILIATE_LADDER_RULES"):
        C.AFFILIATE_LADDER_RULES = False
    import database as DB
    DB.DB_PATH = os.path.join(tmp, "game.db")
    DB.flush_to_disk_async = lambda *a, **k: None
    log = io.StringIO()
    out = {"arm": "ON" if V3 else "OFF", "root": ROOT, "seed": SEED, "seasons": []}
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        DB.init_db()
        random.seed(SEED)
        DB.reset_game_data(start_year=2000)
        import game_engine as ge
        import intl_engine
        ge.create_player(name="Headless Dummy", position="CM", sub_role="")
    c = DB.get_conn().cursor()
    if DETAIL:
        import ai_lifecycle as AL
        _orig_rb = AL._rebalance_squad_sizes

        def _rb_wrap(cc, year):
            r = _orig_rb(cc, year)
            _RB["after"] = dict(cc.execute("SELECT team_id, COUNT(*) FROM ai_players GROUP BY team_id").fetchall())
            _RB["max_log"] = cc.execute("SELECT COALESCE(MAX(id),0) FROM ai_transfer_log").fetchone()[0]
            return r
        AL._rebalance_squad_sizes = _rb_wrap
    out["start"] = metrics(c, C)
    out["start"]["hash"] = table_hash(c)
    dump = {"seed": SEED, "arm": out["arm"], "start": team_dump(c), "seasons": []}
    schedule = [(d, "휴식", {}) for d in range(1, 365)]
    for i in range(SEASONS):
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            remaining = list(schedule)
            for _g in range(30):
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
        sec = time.perf_counter() - t0
        c = DB.get_conn().cursor()
        m = metrics(c, C)
        m["season_sec"] = round(sec, 1)
        m["year"] = ge.get_state().get("current_year")
        m["hash"] = table_hash(c)
        dump["seasons"].append(team_dump(c))
        if DETAIL:
            m["detail"] = detail(c, C, m["year"] - 1)
        out["seasons"].append(m)
    txt = log.getvalue()
    out["traceback_count"] = txt.count("Traceback")
    out["error_lines"] = [l for l in txt.splitlines() if "Traceback" in l or "Error" in l][:20]
    with open(os.path.join(tmp, "run_log.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt)
    out["log_path"] = os.path.join(tmp, "run_log.txt")
    if DUMP:
        with open(DUMP, "w", encoding="utf-8") as fh:
            json.dump(dump, fh, ensure_ascii=False)
        out["dump_path"] = DUMP
    print("@@RESULT@@" + json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()