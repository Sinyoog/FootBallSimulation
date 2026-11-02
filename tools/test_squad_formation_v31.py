# -*- coding: utf-8 -*-
"""[2026-10-31] V3.1(constants.SQUAD_BY_FORMATION_V31 — 중원 배분·GK 3명 원칙) 단위 테스트.

  T1 목표표: V3.1 — 합계 N·GK 3·선발 이상, 중원 합계와 중원 밖 포지션은 V3 표와 같음(20 × 11~28),
     26명 확정표(20개 포메이션), 동률 사례(4-3-1-2 22·23 = 2/4/2, 5-3-2 24~26 = 1/5/1), 같은 입력 같은 결과 /
     OFF — 작업 직전 코드 목표표와 같음
  T2 이적시장 가중치 표: V3.1 GK 행 = 기준 인원 3(수요 3명 이상 0), 다른 포지션 행·OFF 표는 그대로
  T3 은퇴대체 영입 그룹 보호 기준: V3.1 GK 3 / 그 외 1, OFF 전부 1(인원표 미러 포함)
  T4 일반 이적시장 1회(_transfer_market): V3.1 — GK 3명 이하 팀 GK 순감 0, GK 3명 이상 팀 GK 순증 0
     (맞교환은 인원이 그대로라 허용), 오류 없음 / OFF는 같은 위반 수를 참고로 기록
  T5 상위리그 발탁(_upward_transfer_pull)·정원 정리 매각(_quota_sale_cleanup): 같은 GK 불변식
  T7 [2026-10-31 팀별 목표] 이적시장 가중치 표(목표별 수요·판매 값, 목표 0 중립), CF → FW 집계(V3.1만),
     팀 목표 벡터 = 목표표(FW 칸 = ST+CF 목표), 직접 호출(_do_one_transfer_cached): FW는 ST+CF 합산으로 보호,
     GK는 V3.1 규칙 그대로(3명 이하 판매 후보 제외, 3명 이상 목적지 제외 — 팀 목표 가중치와 섞이지 않음)
  T6 새 월드(reset_game_data, 같은 시드): V3.1 — GK 3명 100%, 선발 구성 능력 100%, 포지션 상한(목표+1) 초과 0,
     중원 묶음이 목표 합계+1 초과 0, 26명 초과 0 / OFF — 작업 직전 코드와 팀·선수 전 표 해시 동일
     (인원 정리·포메이션 재검토·이적시장 1회 뒤까지)

사용: python tools/test_squad_formation_v31.py [T1 T2 …]   (AFF_PREV_ROOT = 작업 직전 코드 폴더 — OFF 비교용)
game.db·game.history.db는 임시 폴더에 복사해서 쓴다(원본은 읽기만). 시뮬레이션과 같은 시간에 돌리지 않는다."""
import collections
import contextlib
import hashlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("SQV3_ROOT") or os.path.dirname(HERE)
DATA_ROOT = os.path.dirname(HERE)
RES = {"checks": [], "info": {}}
MID = ("CDM", "CM", "CAM")


def check(name, cond, detail=""):
    RES["checks"].append([name, bool(cond), str(detail)[:500]])


def _boot(load_db=True, v31=False):
    tmp = tempfile.mkdtemp(prefix="sqv31_")
    for f in ("game.db", "game.history.db"):
        shutil.copy(os.path.join(DATA_ROOT, f), os.path.join(tmp, f))
    os.chdir(tmp)
    sys.path.insert(0, ROOT)
    import constants as C
    if hasattr(C, "set_squad_by_formation_v3"):
        C.set_squad_by_formation_v3(False)
    if hasattr(C, "set_squad_by_formation_v31"):
        C.set_squad_by_formation_v31(v31)
    elif v31:
        raise SystemExit("V3.1 스위치 없음")
    if hasattr(C, "AFFILIATE_LADDER_RULES"):
        C.AFFILIATE_LADDER_RULES = False          # 측정 러너와 같게(양쪽 끔)
    import database as DB
    DB.DB_PATH = os.path.join(tmp, "game.db")
    DB.flush_to_disk_async = lambda *a, **k: None
    if load_db:
        DB.init_db()
    return C, DB


def _prev_module(name):
    prev_root = os.environ.get("AFF_PREV_ROOT")
    if not prev_root:
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(name + "_prev", os.path.join(prev_root, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ─────────────────────────────────────────────────────────────────────────
WANT26 = {"4-4-2": (1, 3, 1), "4-4-1-1": (2, 3, 2), "4-3-3": (2, 3, 2), "4-3-3-ATT": (2, 3, 2),
          "4-1-4-1": (2, 3, 2), "4-2-3-1": (3, 2, 2), "4-2-2-2": (3, 3, 3), "4-3-1-2": (3, 3, 3),
          "4-3-2-1": (3, 3, 3), "3-4-3": (1, 3, 1), "3-4-2-1": (3, 3, 3), "3-4-1-2": (2, 3, 2),
          "3-5-2": (2, 3, 2), "3-5-1-1": (2, 4, 3), "5-3-2": (1, 5, 1), "5-4-1": (1, 3, 1),
          "5-2-3": (3, 1, 1), "4-2-4": (1, 3, 1), "4-1-2-1-2": (2, 1, 2), "4-5-1": (2, 3, 2)}


def t1():
    C, DB = _boot(load_db=False, v31=False)
    import squad_targets as ST
    F = list(C.FORMATION_SLOTS)
    off = {(f, n): ST.position_targets(f, n) for f in F for n in range(11, 29)}
    pv = _prev_module("squad_targets")
    if pv is not None:
        prev = {(f, n): pv.position_targets(f, n) for f in F for n in range(11, 29)}
        check("OFF 목표표 = 작업 직전 코드(20 × 11~28)", off == prev)
    else:
        check("작업 직전 코드 폴더(AFF_PREV_ROOT) 지정", False, "환경변수 없음")
    check("OFF 가용 GK 최소 2", C.SQUAD_TARGET_MIN_AVAILABLE_GK == 2)
    C.set_squad_by_formation_v3(True)
    v3 = {(f, n): ST.position_targets(f, n) for f in F for n in range(11, 29)}
    C.set_squad_by_formation_v3(False)
    C.set_squad_by_formation_v31(True)
    bad_sum, bad_nonmid, bad_m, bad_st = [], [], [], []
    for f in F:
        s = ST.starter_counts(f)
        for n in range(11, 29):
            t = ST.position_targets(f, n)
            a = v3[(f, n)]
            if sum(t.values()) != n or (n >= 13 and t["GK"] != 3):
                bad_sum.append((f, n))
            if any(t[p] != a[p] for p in ST.TARGET_POSITIONS if p not in MID):
                bad_nonmid.append((f, n))
            if sum(t[p] for p in MID) != sum(a[p] for p in MID):
                bad_m.append((f, n))
            if any(t[p] < s[p] for p in ST.TARGET_POSITIONS):
                bad_st.append((f, n))
    check("V3.1 목표표: 합계 N · GK 3(13명+)", not bad_sum, bad_sum[:5])
    check("V3.1 목표표: 중원 밖 포지션 = V3 표(20 × 11~28)", not bad_nonmid, bad_nonmid[:5])
    check("V3.1 목표표: 중원 합계 M = V3 표", not bad_m, bad_m[:5])
    check("V3.1 목표표: 모든 포지션 선발 이상", not bad_st, bad_st[:5])
    got26 = {f: tuple(ST.position_targets(f, 26)[p] for p in MID) for f in F}
    diff = {f: (got26[f], WANT26[f]) for f in F if got26[f] != WANT26[f]}
    check("V3.1 26명 중원(CDM/CM/CAM) = 확정표(20개 포메이션)", not diff, diff)
    ties = {("4-3-1-2", 22): (2, 4, 2), ("4-3-1-2", 23): (2, 4, 2),
            ("5-3-2", 24): (1, 5, 1), ("5-3-2", 25): (1, 5, 1), ("5-3-2", 26): (1, 5, 1)}
    tb = {k: tuple(ST.position_targets(*k)[p] for p in MID) for k in ties}
    check("V3.1 동률 사례: 1자리 남으면 CM(4-3-1-2 22·23 = 2/4/2, 5-3-2 24~26 = 1/5/1)", tb == ties, tb)
    check("V3.1 동률 사례: 4-3-1-2 24~26은 양쪽 1명씩 = 3/3/3",
          all(tuple(ST.position_targets("4-3-1-2", n)[p] for p in MID) == (3, 3, 3) for n in (24, 25, 26)))
    again = {(f, n): ST.position_targets(f, n) for f in F for n in range(11, 29)}
    ST._mid_alloc.cache_clear()
    ST._targets_v31_cached.cache_clear()
    check("V3.1 같은 입력 같은 결과(캐시 비우고 다시 계산)",
          again == {(f, n): ST.position_targets(f, n) for f in F for n in range(11, 29)})
    check("V3.1 가용 GK 최소 3", C.SQUAD_TARGET_MIN_AVAILABLE_GK == 3)
    C.set_squad_by_formation_v31(False)
    check("V3.1 끄면 목표표 원래대로", {(f, n): ST.position_targets(f, n) for f in F for n in range(11, 29)} == off)


def t2():
    C, DB = _boot(load_db=False, v31=False)
    import ai_lifecycle as AL
    from formation_logic import position_sell_weight as sw, position_demand_weight as dw
    cap = AL._POS_CNT_CAP
    s_gk = [1.0 + C.POSITION_SELL_STRENGTH * (sw(c, 3) - 1.0) for c in range(cap + 1)]
    d_gk = [(0.0 if c >= 3 else 1.0 + C.POSITION_DEMAND_STRENGTH * (dw(c, 3) - 1.0)) for c in range(cap + 1)]
    check("V3.1 판매 표 GK 행 = 기준 인원 3", AL._SELL_W_TABLE_V31[0] == s_gk, AL._SELL_W_TABLE_V31[0][:6])
    check("V3.1 수요 표 GK 행 = 기준 인원 3, 3명 이상 0", AL._DEMAND_W_TABLE_V31[0] == d_gk,
          AL._DEMAND_W_TABLE_V31[0][:6])
    check("V3.1 표: GK 밖 행은 OFF 표와 같음",
          AL._SELL_W_TABLE_V31[1:] == AL._SELL_W_TABLE[1:] and AL._DEMAND_W_TABLE_V31[1:] == AL._DEMAND_W_TABLE[1:])
    pv = _prev_module("ai_lifecycle") if os.environ.get("AFF_PREV_ROOT") else None
    if pv is not None:
        check("OFF 표 = 작업 직전 코드", AL._SELL_W_TABLE == pv._SELL_W_TABLE and AL._DEMAND_W_TABLE == pv._DEMAND_W_TABLE)


def t3():
    C, DB = _boot(load_db=False, v31=False)
    import ai_lifecycle as AL
    rows = [{"position": "GK", "team_id": 1}] * 3 + [{"position": "GK", "team_id": 2}] * 4 \
        + [{"position": "CB", "team_id": 1}] * 2 + [{"position": "CB", "team_id": 2}]
    off = AL._build_team_pos_group_count(rows)
    check("OFF 그룹 보호 기준 전부 1", AL._grp_keep_min("GK") == 1 and AL._grp_keep_min("DF") == 1)
    C.set_squad_by_formation_v31(True)
    on = AL._build_team_pos_group_count(rows)
    check("V3.1 그룹 보호 기준 GK 3 · 그 외 1", AL._grp_keep_min("GK") == 3 and AL._grp_keep_min("DF") == 1)
    ok_on = on.get("__ok__")
    if ok_on:
        gk = ok_on.get("GK")
        check("V3.1 인원표 미러: GK 3명 팀 제외 · 4명 팀 후보", (not gk[1]) and bool(gk[2]), (bool(gk[1]), bool(gk[2])))
        ok_off = off.get("__ok__")
        check("OFF 인원표 미러: GK 3명 팀도 후보", bool(ok_off["GK"][1]) and bool(ok_off["GK"][2]))
    else:
        RES["info"]["numpy_mirror"] = "없음(미러 검사 생략)"


def _gk_counts(c):
    return dict(c.execute("SELECT team_id, SUM(position='GK') FROM ai_players GROUP BY team_id").fetchall())


def _gk_violations(before, after):
    """GK 3명 이하 팀의 순감, GK 3명 이상 팀의 순증(맞교환은 인원이 그대로라 걸리지 않는다)."""
    drop = [(t, b, after.get(t, 0)) for t, b in before.items() if b <= 3 and after.get(t, 0) < b]
    rise = [(t, b, after.get(t, 0)) for t, b in before.items() if b >= 3 and after.get(t, 0) > b]
    return drop, rise


def _year(c):
    r = c.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()
    return (r[0] if r else 2000) or 2000


def _arm():
    return os.environ.get("SQV31", "1") == "1"


def t4():
    v31 = _arm()
    C, DB = _boot(v31=v31)
    import ai_lifecycle as AL
    c = DB.get_conn().cursor()
    random.seed(31031)
    y = _year(c)
    before = _gk_counts(c)
    n0 = c.execute("SELECT COALESCE(MAX(id),0) FROM ai_transfer_log").fetchone()[0]
    with contextlib.redirect_stdout(io.StringIO()):
        moved = AL._transfer_market(c, y)
    after = _gk_counts(c)
    drop, rise = _gk_violations(before, after)
    gk_moves = c.execute("SELECT COUNT(*) FROM ai_transfer_log WHERE id>? AND player_position='GK'",
                         (n0,)).fetchone()[0]
    RES["info"].update({"moved": moved, "gk_moves": gk_moves, "gk_le3_drop": len(drop), "gk_ge3_rise": len(rise),
                        "examples": (drop[:3], rise[:3])})
    if v31:
        check("V3.1 이적시장: GK 3명 이하 팀 GK 순감 0", not drop, drop[:5])
        check("V3.1 이적시장: GK 3명 이상 팀 GK 순증 0", not rise, rise[:5])
        check("V3.1 이적시장: 이적이 일어남(멈추지 않음)", (moved or 0) > 0, moved)


def t5():
    v31 = _arm()
    C, DB = _boot(v31=v31)
    import ai_lifecycle as AL
    c = DB.get_conn().cursor()
    random.seed(31032)
    y = _year(c)
    for nm, fn in (("상위리그 발탁", lambda: AL._upward_transfer_pull(c, y)),
                   ("정원 정리 매각", lambda: AL._quota_sale_cleanup(c, y, allow_me=False))):
        before = _gk_counts(c)
        with contextlib.redirect_stdout(io.StringIO()):
            r = fn()
        drop, rise = _gk_violations(before, _gk_counts(c))
        RES["info"][nm] = {"result": r, "gk_le3_drop": len(drop), "gk_ge3_rise": len(rise)}
        if v31:
            check(f"V3.1 {nm}: GK 3명 이하 팀 GK 순감 0", not drop, drop[:5])
            check(f"V3.1 {nm}: GK 3명 이상 팀 GK 순증 0", not rise, rise[:5])


def _mk(pid, pos, ovr=70, age=26):
    return {"id": pid, "position": pos, "name": f"p{pid}", "age": age, "ovr": ovr, "contract_end_year": 2003,
            "last_transfer_year": 1990, "nationality": "잉글랜드", "quota_local_country": "", "salary": 100,
            "_rt": None, "rel_rating": None, "sal_pct": None, "role_i": None}


def t7():
    C, DB = _boot(v31=True)
    import ai_lifecycle as AL
    import squad_targets as ST
    S, D = C.POSITION_SELL_STRENGTH, C.POSITION_DEMAND_STRENGTH
    exp_d = {2: 2.5, 3: 1.75, 4: 1.0 / (1 + 1.5 / 4), 5: 1.0 / (1 + 3.0 / 4), 6: 1.0 / (1 + 4.5 / 4)}
    exp_s = {2: 0.5, 3: 0.75, 4: 1.0, 5: 1.25, 6: 1.5}
    ok_d = all(abs(AL._DEMAND_BY_TGT_V31[4][c] - (1 + D * (v - 1))) < 1e-9 for c, v in exp_d.items())
    ok_s = all(abs(AL._SELL_BY_TGT_V31[4][c] - (1 + S * (v - 1))) < 1e-9 for c, v in exp_s.items())
    check("팀 목표 표: 목표 4 수요(2.5·1.75·0.73·0.57·0.47)", ok_d, AL._DEMAND_BY_TGT_V31[4][2:7])
    check("팀 목표 표: 목표 4 판매(0.5·0.75·1.0·1.25·1.5)", ok_s, AL._SELL_BY_TGT_V31[4][2:7])
    check("팀 목표 표: 목표 0 = 수요·판매 모두 중립 1.0",
          set(AL._DEMAND_BY_TGT_V31[0]) == {1.0} and set(AL._SELL_BY_TGT_V31[0]) == {1.0})
    check("CF → FW 집계표: CF = ST 칸, 나머지는 기존 표와 같음",
          AL._SLOT_POS_IDX_V31["CF"] == AL._SLOT_POS_IDX["ST"]
          and all(AL._SLOT_POS_IDX_V31[k] == v for k, v in AL._SLOT_POS_IDX.items()) and "CF" not in AL._SLOT_POS_IDX)
    bad = []
    for f in C.FORMATION_SLOTS:
        for n in (22, 24, 26):
            t = ST.position_targets(f, n)
            v = AL._v31_team_target_vec(f, n)
            want = tuple(t["FW" if k == "ST" else k] for k in AL._SLOT_POS_ORDER)
            if v != want or v[0] != 3:
                bad.append((f, n, v, want))
    check("팀 목표 벡터 = 목표표(ST 칸 = FW 목표, GK 3)", not bad, bad[:3])
    # 직접 호출 — 팀 3개(판매 팀 A/B, 목적지 X/Y)
    base = ["GK", "GK", "GK", "CB", "CB", "LB", "LB", "RB", "RB", "CDM", "CDM", "CM", "CM", "CAM", "CAM",
            "LW", "LW", "RW", "RW"]

    def team(start, extra):
        return [_mk(start + i, p) for i, p in enumerate(base + extra)]

    def run(team_players, src, trials, tgt=True):
        pc = {t: [0] * AL._N_SLOT_POS for t in team_players}
        for t, pl in team_players.items():
            for q in pl:
                i = AL._SLOT_POS_IDX_V31.get(q["position"], -1)
                if i >= 0:
                    pc[t][i] += 1
        tg = {t: AL._v31_team_target_vec("4-4-2", len(pl)) for t, pl in team_players.items()} if tgt else None
        pool = list(team_players)
        avg = {t: 70 for t in team_players}
        moved = collections.Counter()
        for k in range(trials):
            random.seed(1000 + k)
            r = AL._do_one_transfer_cached(src, pool, team_players, avg, 2001, pos_count_by_tid=pc, tgt_by_tid=tg)
            if r:
                pid = r[0][1]
                pos = next(q["position"] for q in team_players[src] if q["id"] == pid)
                moved[(pos, r[0][0])] += 1
        return moved
    tp = {1: team(100, ["ST"]), 2: team(200, ["ST", "CF"]), 3: team(300, ["ST", "ST"])}
    m1 = run(tp, 1, 400)
    check("FW 합계 1명(ST 1·CF 0) 팀: ST는 판매 후보 아님", sum(v for (p, _), v in m1.items() if p == "ST") == 0,
          {f"{k[0]}->{k[1]}": v for k, v in m1.items()})
    m2 = run(tp, 2, 400)
    st_cf = sum(v for (p, _), v in m2.items() if p in ("ST", "CF"))
    check("FW 합계 2명(ST 1·CF 1) 팀: ST·CF가 판매 후보(합산 보호 — 따로 1명씩 보호하지 않음)", st_cf > 0, {f"{k[0]}->{k[1]}": v for k, v in m2.items()})
    check("GK 3명 팀: GK는 일반 판매 후보 아님", sum(v for (p, _), v in m1.items() if p == "GK") == 0
          and sum(v for (p, _), v in m2.items() if p == "GK") == 0)
    tp2 = {1: team(100, ["GK", "ST", "ST"]), 2: team(200, ["ST"]), 3: [q for q in team(300, ["ST"]) if q["id"] != 300]}
    m3 = run(tp2, 1, 1500)
    gk_dst = collections.Counter(d for (p, d), v in m3.items() if p == "GK" for _ in range(v))
    check("GK 4명 팀의 GK: GK 3명 팀(2번)으로는 안 가고 GK 2명 팀(3번)으로만 감", gk_dst.get(2, 0) == 0
          and gk_dst.get(3, 0) > 0, dict(gk_dst))
    RES["info"]["direct"] = {"m1": {f"{k[0]}->{k[1]}": v for k, v in m1.items()},
                             "m2": {f"{k[0]}->{k[1]}": v for k, v in m2.items()}, "gk_dst": dict(gk_dst)}


def table_hash(c):
    h = hashlib.sha256()
    for sql in ("SELECT id, league_id, current_tier, formation FROM teams ORDER BY id",
                "SELECT * FROM ai_players ORDER BY id"):
        for r in c.execute(sql):
            h.update(repr(tuple(r)).encode())
    return h.hexdigest()[:16]


def world_metrics(c):
    import squad_targets as ST
    import sub_positions as SP
    import constants as C
    rows = c.execute("""SELECT t.id, t.formation FROM teams t JOIN leagues l ON l.id=t.league_id
                        JOIN countries cn ON cn.id=l.country_id WHERE cn.name!='군대'""").fetchall()
    by = collections.defaultdict(list)
    for r in c.execute("SELECT id, team_id, position, ovr, foot, sub_role FROM ai_players"):
        by[r[1]].append({"id": r[0], "position": r[2], "ovr": r[3], "foot": r[4], "sub_role": r[5]})
    gk = collections.Counter()
    nf = over_max = mid_over = over26 = n = 0
    for tid, f in rows:
        ro = by.get(tid, [])
        if not ro:
            continue
        SP.attach(ro)
        n += 1
        held = ST.held_counter([p["position"] for p in ro])
        gk[min(held["GK"], 5)] += 1
        f = f if f in C.FORMATION_SLOTS else "4-4-2"
        if not ST.formable([(p["position"], p.get("sub_pos")) for p in ro], f):
            nf += 1
        mx = ST.max_targets(f, len(ro))
        over_max += any(held[p] > mx[p] for p in ST.TARGET_POSITIONS)
        t = ST.position_targets(f, len(ro))
        mid_over += sum(held[p] for p in MID) > sum(t[p] for p in MID) + 1
        over26 += len(ro) > 26
    return {"teams": n, "gk3_pct": round(gk[3] / n * 100, 2), "gk": dict(sorted(gk.items())),
            "not_formable": nf, "over_max": over_max, "mid_over": mid_over, "over26": over26}


def w_run():
    v31 = os.environ.get("SQV31") == "1"
    C, DB = _boot(v31=v31)
    random.seed(424242)
    with contextlib.redirect_stdout(io.StringIO()):
        DB.reset_game_data()
    c = DB.get_conn().cursor()
    RES["info"]["hash_world"] = table_hash(c)
    RES["info"]["metrics"] = world_metrics(c)
    import ai_lifecycle as AL
    random.seed(99)
    with contextlib.redirect_stdout(io.StringIO()):
        AL._rebalance_squad_sizes(c, 2000)
        AL._shuffle_formations(c)
    RES["info"]["hash_after_rebalance"] = table_hash(c)
    random.seed(98)
    with contextlib.redirect_stdout(io.StringIO()):
        AL._transfer_market(c, 2000)
    RES["info"]["hash_after_market"] = table_hash(c)


SCEN = {"T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5": t5, "T7": t7, "WRUN": w_run}


def child(name):
    import traceback
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            SCEN[name]()
    except Exception:
        RES["checks"].append(["예외 없이 실행", False, traceback.format_exc()[-1500:]])
    RES["info"]["traceback_in_log"] = "Traceback" in buf.getvalue()
    print("@@RESULT@@" + json.dumps(RES, ensure_ascii=False, default=str))


def spawn(name, env_extra=None, root=None):
    env = dict(os.environ, PYTHONHASHSEED="0")
    env.update(env_extra or {})
    if root:
        env["SQV3_ROOT"] = root
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", name], capture_output=True,
                       text=True, env=env, timeout=3600)
    i = p.stdout.rfind("@@RESULT@@")
    if i < 0:
        return {"checks": [["자식 프로세스 결과", False, (p.stderr or p.stdout)[-1500:]]], "info": {}}
    return json.loads(p.stdout[i + len("@@RESULT@@"):].strip().splitlines()[0])


def main():
    want = [a for a in sys.argv[1:] if not a.startswith("-")] or ["T1", "T2", "T3", "T4", "T5", "T7", "T6"]
    fails = 0
    info = {}
    for name in want:
        print(f"\n[{name}]", flush=True)
        if name == "T6":
            prev = os.environ.get("AFF_PREV_ROOT")
            on = spawn("WRUN", {"SQV31": "1"})
            off = spawn("WRUN", {"SQV31": "0"})
            res = {"checks": on["checks"] + off["checks"], "info": {"V3.1": on["info"], "OFF": off["info"]}}
            m = on["info"].get("metrics", {})
            res["checks"].append(["새 월드 V3.1: GK 3명 팀 100%", m.get("gk3_pct") == 100.0, m.get("gk")])
            res["checks"].append(["새 월드 V3.1: 선발 구성 능력(formable) 실패 팀 0", m.get("not_formable") == 0,
                                  m.get("not_formable")])
            res["checks"].append(["새 월드 V3.1: 포지션 상한(목표+1, GK 3) 초과 팀 0", m.get("over_max") == 0,
                                  m.get("over_max")])
            res["checks"].append(["새 월드 V3.1: 중원 묶음 목표 합계+1 초과 팀 0", m.get("mid_over") == 0,
                                  m.get("mid_over")])
            res["checks"].append(["새 월드 V3.1: 26명 초과 팀 0", m.get("over26") == 0, m.get("over26")])
            if prev:
                pv = spawn("WRUN", {"SQV31": "0"}, root=prev)
                res["info"]["PREV"] = pv["info"]
                res["checks"] += pv["checks"]
                for k, nm in (("hash_world", "새 월드"), ("hash_after_rebalance", "인원 정리·포메이션 재검토 뒤"),
                              ("hash_after_market", "이적시장 1회 뒤")):
                    res["checks"].append([f"OFF = 작업 직전 코드: {nm} 팀·선수 전 표 해시 동일",
                                          off["info"].get(k) == pv["info"].get(k),
                                          (off["info"].get(k), pv["info"].get(k))])
            else:
                res["checks"].append(["작업 직전 코드 폴더(AFF_PREV_ROOT) 지정", False, "환경변수 없음"])
            for k in ("V3.1", "OFF", "PREV"):
                if res["info"].get(k, {}).get("traceback_in_log"):
                    res["checks"].append([f"{k} 실행 로그에 Traceback 없음", False, ""])
        elif name in ("T4", "T5"):
            on = spawn(name, {"SQV31": "1"})
            off = spawn(name, {"SQV31": "0"})      # OFF는 같은 위반 수를 참고로만 기록
            res = {"checks": on["checks"] + off["checks"], "info": {"V3.1": on["info"], "OFF": off["info"]}}
            for k in ("V3.1", "OFF"):
                if res["info"][k].get("traceback_in_log"):
                    res["checks"].append([f"{k} 실행 로그에 Traceback 없음", False, ""])
        else:
            res = spawn(name)
            if res["info"].get("traceback_in_log"):
                res["checks"].append(["실행 로그에 Traceback 없음", False, ""])
        for nm, ok, det in res["checks"]:
            print(("  [OK] " if ok else "  [FAIL] ") + nm + (f" — {det}" if det and not ok else ""), flush=True)
            fails += (not ok)
        info[name] = res["info"]
    print("\n[INFO]", json.dumps(info, ensure_ascii=False, default=str)[:4000])
    print(f"\n실패 {fails}건")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
    else:
        main()