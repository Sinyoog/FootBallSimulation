# -*- coding: utf-8 -*-
"""[2026-10-31] 포메이션 기준 선수단 구성 V3(constants.SQUAD_BY_FORMATION_V3) 단위 테스트.

  U1 목표표: V3면 GK 3명·합계 N·선발 이상, 4-3-3 26명 = 확정 예시표, 가용 GK 최소 3 / OFF면 작업 전과 같음
  U2 월드 생성 포지션 목록(20개 포메이션 × 등급·부수): 선발 11 = 포메이션 선발 칸, GK 3, 총원 그대로,
     상한 초과 0, 대체 포메이션 대비 인원 안 씀, 구성 능력(formable) 100%, 실제 선발 배치(헝가리안 최적 배치)
     에서 모든 칸이 그 포지션으로 채워짐
  U3 시즌 보충(_gen_topup_rows): GK 3명 팀은 GK를 더 만들지 않음, GK 1명 팀은 3명까지만 우선 보충, 총원 = need
  U4 인원 정리(_rebalance_squad_sizes): 조기은퇴가 GK를 3명 미만으로 내리지 않음, 정상 범위 GK 2명 팀은 맞바꾸기로
     3명(총원 그대로), GK 강제 재분배는 돌지 않음, GK 때문에 상한을 넘는 팀 0
  U5 포메이션 재검토: 바뀐 포메이션은 전부 선발 11명 구성 가능
  W  새 월드 생성(reset_game_data, 같은 시드): V3 — GK 3명 100%, 구성 능력 100%, 실제 선발 배치 불일치 팀 V3 ≤ OFF
     (실제 선발은 기존 AI 선발 로직 그대로 — 2026-10-31 결정), 26명 초과 0 / OFF — 작업 전 코드와 팀·선수 전 표
     해시 동일, 그 뒤 인원 정리·포메이션 재검토까지 해시 동일

사용: python tools/test_squad_formation_v3.py [U1 U2 …]   (AFF_PREV_ROOT = 작업 직전 코드 폴더 — W의 OFF 비교용)
game.db·game.history.db는 임시 폴더에 복사해서 쓴다(원본은 읽기만)."""
import collections
import hashlib
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


def check(name, cond, detail=""):
    RES["checks"].append([name, bool(cond), str(detail)[:500]])


def _boot(load_db=True, v3=False):
    tmp = tempfile.mkdtemp(prefix="sqv3_")
    for f in ("game.db", "game.history.db"):
        shutil.copy(os.path.join(DATA_ROOT, f), os.path.join(tmp, f))
    os.chdir(tmp)
    sys.path.insert(0, ROOT)
    import constants as C
    if hasattr(C, "set_squad_by_formation_v3"):
        C.set_squad_by_formation_v3(v3)
    if hasattr(C, "AFFILIATE_LADDER_RULES"):
        C.AFFILIATE_LADDER_RULES = False          # 이번 실험은 유스팀 A 규칙 끔(양쪽 같게)
    import database as DB
    DB.DB_PATH = os.path.join(tmp, "game.db")
    DB.flush_to_disk_async = lambda *a, **k: None
    if load_db:
        DB.init_db()
    return C, DB


# ─────────────────────────────────────────────────────────────────────────
def u1():
    C, DB = _boot(load_db=False, v3=True)
    import squad_targets as ST
    F = list(C.FORMATION_SLOTS)
    bad = []
    for f in F:
        for n in range(22, 29):
            t = ST.position_targets(f, n)
            s = ST.starter_counts(f)
            if sum(t.values()) != n or t["GK"] != 3 or any(t[p] < s[p] for p in ST.TARGET_POSITIONS):
                bad.append((f, n, t))
    check("V3 목표표: 20개 포메이션 × 22~28명 합계 N · GK 3 · 선발 이상", not bad, bad[:3])
    ex = ST.position_targets("4-3-3", 26)
    want = {"GK": 3, "CB": 5, "LB": 2, "RB": 2, "CDM": 2, "CM": 5, "CAM": 0, "LW": 2, "RW": 2, "FW": 3}
    check("V3 4-3-3 26명 = GK3 CB5 LB2 RB2 CDM2 CM5 LW2 RW2 ST3(확정 예시표)", ex == want, ex)
    check("V3 가용 GK 최소 3", C.SQUAD_TARGET_MIN_AVAILABLE_GK == 3)
    check("V3 켜도 기존 스위치(GK 강제 재분배)는 꺼짐", not C.WORLD_SQUAD_BY_FORMATION)
    C.set_squad_by_formation_v3(False)
    ST._targets_cached.cache_clear() if hasattr(ST._targets_cached, "cache_clear") else None
    cur = {(f, n): ST.position_targets(f, n) for f in F for n in range(11, 29)}
    prev_root = os.environ.get("AFF_PREV_ROOT")
    if prev_root:
        import importlib.util
        spec = importlib.util.spec_from_file_location("st_prev", os.path.join(prev_root, "squad_targets.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        prev = {(f, n): m.position_targets(f, n) for f in F for n in range(11, 29)}
        check("OFF 목표표 = 작업 직전 코드(20 × 11~28)", cur == prev)
    check("OFF 가용 GK 최소 2", C.SQUAD_TARGET_MIN_AVAILABLE_GK == 2)


def _target_of(pos):
    import squad_targets as ST
    return ST.REGISTERED_TO_TARGET.get(pos) if pos else None


def lineup_mismatch(roster, formation):
    """실제 선발 배치(team_plan.optimal_lineup_slots — 경기 전력과 같은 최적 배치)에서 칸 포지션과 다른
    선수(주포·부포 둘 다 안 맞음)가 서거나 빈 칸 수."""
    import squad_targets as ST
    import team_plan as TP
    import constants as C
    slots = C.FORMATION_SLOTS[formation]
    placed = TP.optimal_lineup_slots(roster, slots)
    bad = 0
    for s, pl in zip(slots, placed):
        st = ST.SLOT_TO_TARGET.get(s, s)
        if pl is None or st not in (_target_of(pl.get("position")), _target_of(pl.get("sub_pos"))):
            bad += 1
    return bad


def u2():
    C, DB = _boot(load_db=False, v3=True)
    import squad_targets as ST
    random.seed(20261031)
    F = list(C.FORMATION_SLOTS)
    combos = [("SS", 1), ("S", 1), ("S", 2), ("A", 1), ("A", 2), ("B", 1), ("C", 3), ("D", 4), ("E", 2)]
    viol = collections.Counter()
    sizes = collections.Counter()
    n_team = 0
    for f in F:
        for g, t in combos:
            for _ in range(25):
                pos = DB._build_squad_positions_by_formation(g, t, f, "BALANCED")
                n = len(pos)
                n_team += 1
                sizes[n] += 1
                st = pos[:11]
                want = [DB._FORMATION_SLOT_TO_REGISTERED.get(s, s) for s in DB._FORMATION_SLOTS_FOR_SEED[f]]
                if st != want:
                    viol["선발 11 ≠ 포메이션 선발 칸"] += 1
                held = ST.held_counter(pos)
                if held["GK"] != 3 or pos.count("GK") != 3:
                    viol["GK ≠ 3"] += 1
                mx = ST.max_targets(f, n)
                if any(held[p] > mx[p] for p in ST.TARGET_POSITIONS):
                    viol["상한 초과"] += 1
                fixed = (t == 1 and g in ("A", "S", "SS")) or (t == 2 and g in ("S", "SS"))
                if (fixed and n != 26) or (not fixed and not 22 <= n <= 25):
                    viol["총원 규칙"] += 1
                if not ST.formable([(p, None) for p in pos], f):
                    viol["구성 능력(formable) 실패"] += 1
                ro = [{"id": i, "position": p, "ovr": 70, "sub_pos": None} for i, p in enumerate(pos)]
                if lineup_mismatch(ro, f):
                    viol["실제 선발 배치 불일치"] += 1
    check(f"월드 생성 포지션 목록 {n_team}팀: 선발 칸·GK 3·상한·총원·구성 능력·실제 선발 배치 위반 0",
          not viol, dict(viol))
    check("총원 22~26 모두 나옴", set(sizes) == {22, 23, 24, 25, 26}, dict(sizes))
    # 대체 포메이션 대비 인원(2차)을 안 쓰는지 — V3만 켜면 _gen_alt_formations를 부르지 않는다
    called = []
    _orig = DB._gen_alt_formations
    DB._gen_alt_formations = lambda *a, **k: called.append(a) or _orig(*a, **k)
    DB._build_squad_positions_by_formation("A", 1, "4-3-3", "BALANCED")
    DB._gen_alt_formations = _orig
    check("V3만 켜면 대체 포메이션 대비 인원 안 씀", not called)


def _team_rows(c):
    return c.execute("""SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier, cn.name AS cname,
                               cn.continent AS continent, t.formation AS formation
                        FROM teams t JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id
                        WHERE cn.name != '군대' ORDER BY t.id""").fetchall()


def u3():
    C, DB = _boot(v3=True)
    import ai_lifecycle as AL
    from constants import get_country_league_grade
    c = DB.get_conn().cursor()
    random.seed(7)
    rows = _team_rows(c)[:400:8]
    name_cache = AL._build_name_cache(c)
    add_gk_3, add_gk_1, n_bad = collections.Counter(), collections.Counter(), 0
    for r in rows:
        ro = [(x[0], x[1], x[2]) for x in c.execute("SELECT id, position, ovr FROM ai_players WHERE team_id=?",
                                                    (r["tid"],))]
        gks = [x for x in ro if x[1] == "GK"]
        fields = [x for x in ro if x[1] != "GK"]
        g = get_country_league_grade(r["cname"])
        for want_gk, ctr in ((3, add_gk_3), (1, add_gk_1)):
            # GK 수를 want_gk로 맞춘 로스터(남는 GK는 필드로 바꾼 가상 명단 — DB는 안 바꾼다)
            base = (gks + [(i, "GK", o) for i, _p, o in fields])[:want_gk] if len(gks) < want_gk else gks[:want_gk]
            ro2 = base + fields[:22 - want_gk]
            rbt = {r["tid"]: ro2}
            new = AL._gen_topup_rows(c, r["tid"], r["tier"], r["cname"], r["continent"], r["tname"], g, 4,
                                     rbt, name_cache, 2000, False, formation=r["formation"],
                                     held_positions=[p for _i, p, _o in ro2])
            n_gk = sum(1 for x in new if x[2] == "GK")   # INSERT 튜플: (team_id, name, position, …)
            ctr[n_gk] += 1
            if len(new) != 4:
                n_bad += 1
    check("보충: GK 3명 팀은 GK를 더 만들지 않음(4명 보충 × 50팀)", set(add_gk_3) == {0}, dict(add_gk_3))
    check("보충: GK 1명 팀은 GK를 정확히 2명(3명까지) 우선 보충", set(add_gk_1) == {2}, dict(add_gk_1))
    check("보충 인원 = need(총원을 늘리지 않음)", n_bad == 0, n_bad)


def u4():
    C, DB = _boot(v3=True)
    import ai_lifecycle as AL
    c = DB.get_conn().cursor()
    from constants import get_country_league_grade
    rows = _team_rows(c)
    # A: 상한 초과 + GK 3명이 최저 OVR / B: 정상 범위 + GK 2명 / C: 하한 미만 + GK 1명
    pick = {}
    for r in rows:
        lo, hi = AL._squad_min_max(get_country_league_grade(r["cname"]), r["tier"])
        n = c.execute("SELECT COUNT(*) FROM ai_players WHERE team_id=?", (r["tid"],)).fetchone()[0]
        if "A" not in pick and lo == hi == 26:
            pick["A"] = (r["tid"], lo, hi)
        elif "B" not in pick and lo < n < hi:
            pick["B"] = (r["tid"], lo, hi)
        elif "C" not in pick and lo < hi and n > lo + 2:
            pick["C"] = (r["tid"], lo, hi)
        if len(pick) == 3:
            break
    tA, _, hiA = pick["A"]
    ids = [x[0] for x in c.execute("SELECT id FROM ai_players WHERE team_id=? ORDER BY ovr", (tA,))]
    c.execute(f"UPDATE ai_players SET position='CM' WHERE team_id=? AND position='GK'", (tA,))
    c.executemany("UPDATE ai_players SET position='GK', ovr=30 WHERE id=?", [(i,) for i in ids[:3]])
    donor = [x[0] for x in c.execute("SELECT id FROM ai_players WHERE team_id!=? AND position='CM' LIMIT 3", (tA,))]
    c.executemany("UPDATE ai_players SET team_id=? WHERE id=?", [(tA, i) for i in donor])   # 26 → 29명
    tB = pick["B"][0]
    gB = [x[0] for x in c.execute("SELECT id FROM ai_players WHERE team_id=? AND position='GK'", (tB,))]
    c.executemany("UPDATE ai_players SET position='CB' WHERE id=?", [(i,) for i in gB[2:]])
    if len(gB) < 2:
        x = c.execute("SELECT id FROM ai_players WHERE team_id=? AND position!='GK' LIMIT ?", (tB, 2 - len(gB))).fetchall()
        c.executemany("UPDATE ai_players SET position='GK' WHERE id=?", [(i[0],) for i in x])
    tC, loC, _ = pick["C"]
    gC = [x[0] for x in c.execute("SELECT id FROM ai_players WHERE team_id=? AND position='GK'", (tC,))]
    c.executemany("UPDATE ai_players SET position='CB' WHERE id=?", [(i,) for i in gC[1:]])
    keep = [x[0] for x in c.execute("SELECT id FROM ai_players WHERE team_id=? ORDER BY position='GK' DESC, id",
                                    (tC,))][:loC - 3]
    c.execute(f"UPDATE ai_players SET team_id=NULL WHERE team_id=? AND id NOT IN ({','.join('?' * len(keep))})",
              (tC, *keep))
    pre = dict(c.execute("SELECT team_id, COUNT(*) FROM ai_players GROUP BY team_id").fetchall())
    called = []
    _o1, _o2 = AL._gk_excess_redistribute, AL._gk_final_balance
    AL._gk_excess_redistribute = lambda *a, **k: called.append(1)
    AL._gk_final_balance = lambda *a, **k: called.append(2)
    random.seed(11)
    AL._rebalance_squad_sizes(c, 2000)
    AL._gk_excess_redistribute, AL._gk_final_balance = _o1, _o2
    post = dict(c.execute("SELECT team_id, COUNT(*) FROM ai_players GROUP BY team_id").fetchall())
    gk = dict(c.execute("SELECT team_id, SUM(position='GK') FROM ai_players GROUP BY team_id").fetchall())
    check("A 상한 초과(29명, GK 3명이 최저 OVR) → 26명 · GK 3명 그대로", post.get(tA) == hiA and gk.get(tA) == 3,
          (post.get(tA), gk.get(tA)))
    check("B 정상 범위 GK 2명 → GK 3명, 총원 그대로(맞바꾸기)", gk.get(tB) == 3 and post.get(tB) == pre.get(tB),
          (pre.get(tB), post.get(tB), gk.get(tB)))
    check("C 하한 미만 GK 1명 → GK 3명, 하한까지만 보충", gk.get(tC) == 3 and post.get(tC) == loC,
          (pre.get(tC), post.get(tC), gk.get(tC), loC))
    over = []
    for r in rows:
        lo, hi = AL._squad_min_max(get_country_league_grade(r["cname"]), r["tier"])
        if post.get(r["tid"], 0) > max(hi, pre.get(r["tid"], 0)):
            over.append(r["tid"])
    check("인원 정리 뒤 상한을 넘게 커진 팀 0", not over, over[:5])
    check("GK 강제 재분배·마지막 정리 안 돎(인원 정리 안에서)", not called)
    import inspect
    src = inspect.getsource(AL.run_ai_offseason)
    check("오프시즌의 GK 강제 정리 호출은 기존 스위치(_sq2_on)에만 묶여 있음",
          src.count("_gk_excess_redistribute(c, year)") == 1 and "if _sq2_on():" in src and not AL._sq2_on())


def u5():
    C, DB = _boot(v3=True)
    import ai_lifecycle as AL
    import squad_targets as ST
    import sub_positions as SP
    c = DB.get_conn().cursor()
    before = dict(c.execute("SELECT id, formation FROM teams").fetchall())
    tids = [r["tid"] for r in _team_rows(c)]
    random.seed(5)
    AL._shuffle_formations(c, forced_teams=set(tids))
    after = dict(c.execute("SELECT id, formation FROM teams").fetchall())
    changed = [t for t in tids if after[t] != before[t]]
    bad = []
    for t in changed:
        ro = [dict(r) for r in c.execute("SELECT id, position FROM ai_players WHERE team_id=?", (t,))]
        SP.attach(ro)
        if not ST.formable([(p["position"], p.get("sub_pos")) for p in ro], after[t]):
            bad.append((t, after[t]))
    check(f"포메이션 재검토(전 팀 강제): 바뀐 {len(changed)}팀 전부 선발 11명 구성 가능", changed and not bad, bad[:5])


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
    not_formable = mism = over26 = pos5 = 0
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
            not_formable += 1
        if lineup_mismatch(ro, f):
            mism += 1
        over26 += len(ro) > 26
        pos5 += any(v >= 5 for k, v in held.items() if k != "CB")
    return {"teams": n, "gk": {k: round(v / n * 100, 1) for k, v in sorted(gk.items())},
            "gk3_pct": round(gk[3] / n * 100, 2), "not_formable": not_formable, "lineup_mismatch_teams": mism,
            "over26": over26, "pos5_pct": round(pos5 / n * 100, 1)}


def table_hash(c):
    h = hashlib.sha256()
    for sql in ("SELECT id, league_id, current_tier, formation FROM teams ORDER BY id",
                "SELECT * FROM ai_players ORDER BY id"):
        for r in c.execute(sql):
            h.update(repr(tuple(r)).encode())
    return h.hexdigest()[:16]


def w_run():
    """환경변수 SQV3_V3=1/0로 V3 켜고 끈 새 월드를 같은 시드로 만들고 지표·해시를 남긴다."""
    v3 = os.environ.get("SQV3_V3") == "1"
    C, DB = _boot(v3=v3)
    import io, contextlib
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


SCEN = {"U1": u1, "U2": u2, "U3": u3, "U4": u4, "U5": u5, "WRUN": w_run}


def child(name):
    import io, contextlib, traceback
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
    want = [a for a in sys.argv[1:] if not a.startswith("-")] or ["U1", "U2", "U3", "U4", "U5", "W"]
    fails = 0
    info = {}
    for name in want:
        print(f"\n[{name}]", flush=True)
        if name == "W":
            prev = os.environ.get("AFF_PREV_ROOT")
            on = spawn("WRUN", {"SQV3_V3": "1"})
            off = spawn("WRUN", {"SQV3_V3": "0"})
            res = {"checks": on["checks"] + off["checks"], "info": {"V3": on["info"], "OFF": off["info"]}}
            m = on["info"].get("metrics", {})
            res["checks"].append(["새 월드 V3: GK 3명 팀 100%", m.get("gk3_pct") == 100.0, m.get("gk")])
            res["checks"].append(["새 월드 V3: 선발 구성 능력(formable) 실패 팀 0", m.get("not_formable") == 0,
                                  m.get("not_formable")])
            # [2026-10-31 신민용 결정: 1번] 실제 선발은 기존 AI 선발 로직(더 강한 인접 포지션 선수를 쓸 수 있음)을
            # 그대로 두므로 0팀이 아니라 승인 기준과 같은 비교(V3 ≤ OFF)로 본다. 구성 능력(formable)은 0팀 그대로.
            mo = off["info"].get("metrics", {})
            res["checks"].append(["새 월드: 실제 선발 배치에서 칸 포지션과 다른 선수가 서는 팀 V3 ≤ OFF",
                                  m.get("lineup_mismatch_teams", 10 ** 9) <= mo.get("lineup_mismatch_teams", -1),
                                  (m.get("lineup_mismatch_teams"), mo.get("lineup_mismatch_teams"))])
            res["checks"].append(["새 월드 V3: 26명 초과 팀 0", m.get("over26") == 0, m.get("over26")])
            if prev:
                pv = spawn("WRUN", {"SQV3_V3": "0"}, root=prev)
                res["info"]["PREV"] = pv["info"]
                res["checks"] += pv["checks"]
                res["checks"].append(["OFF = 작업 직전 코드: 새 월드 팀·선수 전 표 해시 동일",
                                      off["info"].get("hash_world") == pv["info"].get("hash_world"),
                                      (off["info"].get("hash_world"), pv["info"].get("hash_world"))])
                res["checks"].append(["OFF = 작업 직전 코드: 인원 정리·포메이션 재검토 뒤 해시 동일",
                                      off["info"].get("hash_after_rebalance") == pv["info"].get("hash_after_rebalance"),
                                      (off["info"].get("hash_after_rebalance"), pv["info"].get("hash_after_rebalance"))])
            else:
                res["checks"].append(["작업 직전 코드 폴더(AFF_PREV_ROOT) 지정", False, "환경변수 없음"])
            for k in ("V3", "OFF", "PREV"):
                if res["info"].get(k, {}).get("traceback_in_log"):
                    res["checks"].append([f"{k} 실행 로그에 Traceback 없음", False, ""])
        else:
            res = spawn(name, {"AFF_PREV_ROOT": os.environ.get("AFF_PREV_ROOT", "")})
            if res["info"].get("traceback_in_log"):
                res["checks"].append(["실행 로그에 Traceback 없음", False, ""])
        for nm, ok, det in res["checks"]:
            print(("  [OK] " if ok else "  [FAIL] ") + nm + (f" — {det}" if det and not ok else ""), flush=True)
            fails += (not ok)
        info[name] = res["info"]
    print("\n[INFO]", json.dumps(info, ensure_ascii=False, default=str)[:3000])
    print(f"\n실패 {fails}건")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
    else:
        main()