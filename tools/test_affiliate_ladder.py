# -*- coding: utf-8 -*-
"""[2026-10-31] 유스팀 작업 A — 산하팀 부수 순서 승강 규칙(affiliate_tiers.py, constants.AFFILIATE_LADDER_RULES) 단위 테스트.

시나리오마다 game.db·game.history.db를 임시 폴더에 복사한 새 프로세스에서 돈다(원본은 읽기만).
스페인 리그(1부 20 / 2부 22 / 3~6부 40 / 7부 50팀)에 레알 마드리드(1군)·카스티야·C를 원하는 부수로 옮겨 두고,
그 시즌 순위표를 합성(팀마다 자기 자신과 1경기 — 승점·득실은 같고 득점으로만 순위가 갈리게)한 뒤
실제 game_engine._process_promotion_relegation을 돌려 결과를 확인한다. PO는 실제 promotion_playoff_engine
(start_promotion_playoffs / _finalize_boundary_match / sweep)으로 결과를 정해 넣는다.

  S1  예시: 1군 4부 꼴찌(강등) + 카스티야 5부 1위 + C 6부 → 카스티야 승격 취소·강제 강등, C 연쇄 강등, 39위 잔류
  S2  최하위 리그: 카스티야 6부 꼴찌 → 7부(C와 같이), 1군까지 7부로 내려와도 같이 있음
  S3  승격 가능/불가: 1군 1부면 카스티야 3부 1위 → 2부, 1군 2부면 막히고 다음 정상 팀이 승격
  S4  형제 순서: C는 카스티야를 못 넘는다 / 카스티야가 같은 시즌 먼저 올라가면 C도 한 칸 올라갈 수 있음
  S5  강제 강등팀이 자동 강등 자리보다 많으면 강등·승격 수 확장(승격 쪽도 자격 규칙 적용)
  S6  PO 자격: 1군이 강등 PO에 걸리면 카스티야는 PO 후보에서 빠짐 / 1군이 안전하면 PO 후보가 됨
  S7  PO 뒤 맞교환: 1군이 강등 PO에서 져 카스티야와 같은 부 → 카스티야·C 연쇄 맞교환, PO 결과 그대로
  S8  맞교환 후보 없음(작은 합성 DB): 예외 집계, 팀 수 유지, 자격 없는 팀을 올리지 않음
  S9  기존 세이브 첫 시즌 정리: 전 세계 순위 합성 → 시작 월드의 위반 정리, 리그 팀 수 불변, 무자격 승격 0
  S10 스위치 OFF = 작업 전 코드(원본 압축본)와 같은 결과(S1·S9 배치로 팀 소속·승강 기록·PO 대진 비교)
  S11 시작 월드 정리(fix_start_tiers): 위반 0, 리그 팀 수 불변, 두 번 돌려도 같은 결과, 새 게임 생성 경로 연결

사용: python tools/test_affiliate_ladder.py            (전체, 약 몇 분)
      python tools/test_affiliate_ladder.py S1 S7     (일부)
환경변수 AFF_TEST_ROOT로 다른 코드 폴더(작업 전 원본)를 지정할 수 있다(S10 내부용)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("AFF_TEST_ROOT") or os.path.dirname(HERE)
DATA_ROOT = os.path.dirname(HERE)   # 테스트용 game.db는 항상 작업 폴더의 것을 쓴다
ESP, RM, CAS, RMC = 3, 775, 833, 909
YEAR = 2000


# ═══════════════════════════════════════════════════════════════════════
# 자식 프로세스: 시나리오 하나
# ═══════════════════════════════════════════════════════════════════════
def _boot(switch=True):
    tmp = tempfile.mkdtemp(prefix="affl_")
    for f in ("game.db", "game.history.db"):
        src = os.path.join(DATA_ROOT, f)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(tmp, f))
    os.chdir(tmp)
    sys.path.insert(0, ROOT)
    import database as DB
    DB.DB_PATH = os.path.join(tmp, "game.db")
    DB.flush_to_disk_async = lambda *a, **k: None
    DB.init_db()
    import constants
    if hasattr(constants, "AFFILIATE_LADDER_RULES"):
        constants.AFFILIATE_LADDER_RULES = switch
    import game_engine as GE
    GE._live_debug = lambda *a, **k: None
    return DB, GE, tmp


def _conn(DB):
    return DB.get_conn()


def league_ids(c, cid):
    return {r[1]: r[0] for r in c.execute("SELECT id, tier FROM leagues WHERE country_id=?", (cid,)).fetchall()}


def members(c, lid):
    return [r[0] for r in c.execute("SELECT id FROM teams WHERE league_id=? ORDER BY id", (lid,)).fetchall()]


def place(c, tid, tier, cid=ESP):
    """tid를 그 나라 tier 리그로 — 그 리그의 일반 팀(산하팀·모구단 아님) 하나와 자리를 맞바꿔 팀 수 유지."""
    L = league_ids(c, cid)
    cur = c.execute("SELECT league_id, current_tier FROM teams WHERE id=?", (tid,)).fetchone()
    if cur[1] == tier:
        return
    parents = {r[0] for r in c.execute("SELECT DISTINCT parent_team_id FROM teams WHERE parent_team_id IS NOT NULL")}
    special = {RM, CAS, RMC} | parents
    cand = [t for t in members(c, L[tier])
            if t not in special and c.execute("SELECT classification_status FROM teams WHERE id=?",
                                              (t,)).fetchone()[0] == "NORMAL"]
    other = cand[-1]
    c.execute("UPDATE teams SET league_id=?, current_tier=? WHERE id=?", (L[tier], tier, tid))
    c.execute("UPDATE teams SET league_id=?, current_tier=? WHERE id=?", (cur[0], cur[1], other))


def standings(c, cid, ranks, season=1, all_countries=False):
    """ranks: {team_id: 순위(1부터, 음수면 끝에서)}. 나머지는 팀 id 순으로 채운다. 득점으로만 순위가 갈린다."""
    c.execute("DELETE FROM match_results")
    cids = [r[0] for r in c.execute("SELECT DISTINCT country_id FROM leagues")] if all_countries else [cid]
    rows = []
    for _cid in cids:
        for lid in league_ids(c, _cid).values():
            ms = members(c, lid)
            n = len(ms)
            fixed = {}
            for t, rk in ranks.items():
                if t in ms:
                    fixed[t] = rk if rk > 0 else n + 1 + rk
            order = [None] * n
            for t, pos in fixed.items():
                order[pos - 1] = t
            rest = iter(sorted(t for t in ms if t not in fixed))
            order = [o if o is not None else next(rest) for o in order]
            for pos, t in enumerate(order, start=1):
                rows.append((lid, 1, t, t, n - pos + 1, 0, season, YEAR))
    c.executemany("INSERT INTO match_results(league_id, week, home_team_id, away_team_id, home_score, away_score, "
                  "season, year) VALUES(?,?,?,?,?,?,?,?)", rows)


def order_of(c, lid):
    """합성 순위표 순서(1위부터)."""
    return [r[0] for r in c.execute("SELECT home_team_id FROM match_results WHERE league_id=? "
                                    "ORDER BY home_score DESC, home_team_id", (lid,)).fetchall()]


def snapshot(c):
    return {r[0]: (r[1], r[2]) for r in c.execute("SELECT id, league_id, current_tier FROM teams").fetchall()}


def league_sizes(c):
    return dict(c.execute("SELECT league_id, COUNT(*) FROM teams GROUP BY league_id").fetchall())


def ladder_violations(c, cid=None):
    """(산하팀, 위 팀, 산하팀 부, 위 팀 부) — 최하위 리그 동거는 위반 아님. cid를 주면 그 나라만(순위를 합성한 나라)."""
    import affiliate_tiers as A
    L = A._team_maps(c)
    league_of_ct, max_tier_c, tier_of, country_of = L[0], L[1], L[2], L[3]
    parent_of = L[7]
    up = A.build_upper_map(parent_of, tier_of, A.load_prev_levels(c))
    mt = lambda t: max_tier_c.get(country_of.get(t), 99)
    return [(a, up[a], tier_of[a], tier_of[up[a]]) for a in A.violations(tier_of, up, mt)
            if cid is None or country_of.get(a) == cid]


RES = {"checks": [], "info": {}}


def check(name, cond, detail=""):
    RES["checks"].append([name, bool(cond), str(detail)[:400]])


def run_main(GE):
    GE._process_promotion_relegation(YEAR)


def plog(c):
    return [tuple(r) for r in c.execute("SELECT team_id, from_tier, to_tier FROM promotion_log WHERE year=? "
                                         "ORDER BY id", (YEAR,)).fetchall()]


def po_slots(c):
    return [tuple(r) for r in c.execute("SELECT upper_league_id, lower_league_id, side, offset_idx, team_id "
                                         "FROM po_pending_slots WHERE year=? ORDER BY id", (YEAR,)).fetchall()]


def sc_S1(DB, GE):
    c = _conn(DB)
    place(c, RM, 4); place(c, CAS, 5); place(c, RMC, 6)
    L = league_ids(c, ESP)
    standings(c, ESP, {RM: -1, CAS: 1, RMC: 5})
    o5, o6 = order_of(c, L[5]), order_of(c, L[6])
    sizes0 = league_sizes(c)
    run_main(GE)
    s = snapshot(c)
    check("1군 4부 꼴찌 → 5부 강등", s[RM][1] == 5, s[RM])
    check("카스티야 5부 1위지만 승격 안 됨 → 6부 강제 강등", s[CAS][1] == 6, s[CAS])
    check("C 6부 → 7부 연쇄 강등", s[RMC][1] == 7, s[RMC])
    check("5부 2위(정상 팀)가 대신 4부 승격", s[o5[1]][1] == 4, (o5[1], s[o5[1]]))
    # 5부 자동 강등 3자리 중 1자리를 카스티야가 차지 → 원래 38위(끝에서 3번째)는 잔류, 39·40위만 강등
    check("5부: 카스티야가 강등 자리 차지 → 원래 38위 잔류(PO 후보로 이동)", s[o5[-3]][1] == 5, (o5[-3], s[o5[-3]]))
    check("5부: 39·40위 강등", s[o5[-2]][1] == 6 and s[o5[-1]][1] == 6, [s[o5[-2]], s[o5[-1]]])
    slots = po_slots(c)
    up5 = [x[4] for x in slots if x[0] == L[5] and x[2] == "upper"]
    check("5부 잔류 PO 후보 = 원래 38위(강제 강등팀 제외 순위표 기준 자동 강등 바로 위)", up5 == [o5[-3]], (up5, o5[-4:]))
    check("6부: C가 강등 자리 차지 → 원래 38위 잔류", s[o6[-3]][1] == 6, (o6[-3], s[o6[-3]]))
    check("리그 팀 수 불변", league_sizes(c) == sizes0)
    v = ladder_violations(c, ESP)
    check("순서 위반 0건(PO 진행 중 팀 제외 없음)", not v, v)
    RES["info"]["stats"] = _stats()


def _stats():
    import affiliate_tiers as A
    st = A.STATS.get(YEAR, {})
    return {k: (v if not isinstance(v, list) else v[:10]) for k, v in st.items()}


def sc_S2(DB, GE):
    c = _conn(DB)
    place(c, CAS, 6); place(c, RMC, 7)
    standings(c, ESP, {CAS: -1, RMC: -1})
    sizes0 = league_sizes(c)
    run_main(GE)
    s = snapshot(c)
    check("카스티야 6부 꼴찌 → 7부", s[CAS][1] == 7, s[CAS])
    check("C는 7부(최하위) 그대로 — 같이 있어도 됨", s[RMC][1] == 7, s[RMC])
    check("리그 팀 수 불변", league_sizes(c) == sizes0)
    # 2단계: 다음 시즌 1군까지 6부 꼴찌로 7부에 내려오는 경우
    c.execute("DELETE FROM promotion_log"); c.execute("DELETE FROM po_pending_slots")
    place(c, RM, 6)
    standings(c, ESP, {RM: -1, CAS: 1, RMC: 2})
    run_main(GE)
    s = snapshot(c)
    check("1군도 7부로 내려옴", s[RM][1] == 7, s[RM])
    check("카스티야 7부 1위여도 승격 안 됨(1군과 같은 부)", s[CAS][1] == 7, s[CAS])
    check("C도 7부 그대로", s[RMC][1] == 7, s[RMC])
    check("순서 위반 0건", not ladder_violations(c, ESP), ladder_violations(c, ESP))


def sc_S3(DB, GE):
    c = _conn(DB)
    L = league_ids(c, ESP)
    standings(c, ESP, {CAS: 1, RM: 10})
    o3 = order_of(c, L[3])
    run_main(GE)
    s = snapshot(c)
    check("1군 1부 → 카스티야 3부 1위 → 2부 승격", s[CAS][1] == 2, s[CAS])
    check("3부 2위도 정상 승격", s[o3[1]][1] == 2, s[o3[1]])
    # 1군이 2부(중위권)면 막힘
    c2 = c
    c2.execute("DELETE FROM promotion_log"); c2.execute("DELETE FROM po_pending_slots")
    place(c2, CAS, 3); place(c2, RM, 2)
    standings(c2, ESP, {CAS: 1, RM: 10})
    o3 = order_of(c2, L[3])
    run_main(GE)
    s = snapshot(c2)
    check("1군 2부 → 카스티야 3부 1위여도 승격 불가", s[CAS][1] == 3, s[CAS])
    check("대신 3부 2·3위 정상 팀 승격(자동 2자리)", s[o3[1]][1] == 2 and s[o3[2]][1] == 2, [s[o3[1]], s[o3[2]]])
    slots = po_slots(c2)
    check("카스티야는 3부 PO 후보에도 없음", CAS not in [x[4] for x in slots], slots[:8])
    check("순서 위반 0건", not ladder_violations(c2, ESP), ladder_violations(c2, ESP))


def sc_S4(DB, GE):
    c = _conn(DB)
    place(c, RMC, 4)
    standings(c, ESP, {RMC: 1, CAS: 15})
    run_main(GE)
    s = snapshot(c)
    check("카스티야 3부(중위권) → C 4부 1위여도 승격 불가", s[RMC][1] == 4, s[RMC])
    c.execute("DELETE FROM promotion_log"); c.execute("DELETE FROM po_pending_slots")
    place(c, CAS, 2); place(c, RMC, 4)
    standings(c, ESP, {RMC: 1, CAS: 10})
    run_main(GE)
    s = snapshot(c)
    check("카스티야 2부 → C 4부 1위 → 3부 승격", s[RMC][1] == 3, s[RMC])
    c.execute("DELETE FROM promotion_log"); c.execute("DELETE FROM po_pending_slots")
    place(c, CAS, 3); place(c, RMC, 4)
    standings(c, ESP, {RMC: 1, CAS: 1, RM: 5})
    run_main(GE)
    s = snapshot(c)
    check("같은 시즌 카스티야 3부 1위 → 2부, C 4부 1위 → 3부(카스티야가 먼저 올라감)",
          s[CAS][1] == 2 and s[RMC][1] == 3, [s[CAS], s[RMC]])
    check("순서 위반 0건", not ladder_violations(c, ESP), ladder_violations(c, ESP))


def sc_S5(DB, GE):
    c = _conn(DB)
    L = league_ids(c, ESP)
    # 5부 일반 팀 4개를 "모구단도 5부에 있는 산하팀"으로 만든다(시작 월드 위반처럼 이월된 충돌) — 강제 강등 4 > 자동 3
    m5 = [t for t in members(c, L[5]) if c.execute("SELECT classification_status FROM teams WHERE id=?",
                                                     (t,)).fetchone()[0] == "NORMAL"]
    pars, kids = m5[:4], m5[4:8]
    for p_, k in zip(pars, kids):
        c.execute("UPDATE teams SET classification_status='AFFILIATE', parent_team_id=? WHERE id=?", (p_, k))
    # 6부 1위는 산하팀(모구단 1부)이지만 자격 있음, 6부 2위는 모구단이 5부인 산하팀(자격 없음)
    m6 = [t for t in members(c, L[6]) if c.execute("SELECT classification_status FROM teams WHERE id=?",
                                                     (t,)).fetchone()[0] == "NORMAL"]
    ok_kid, bad_kid = m6[0], m6[1]
    c.execute("UPDATE teams SET classification_status='AFFILIATE', parent_team_id=? WHERE id=?", (783, ok_kid))
    c.execute("UPDATE teams SET classification_status='AFFILIATE', parent_team_id=? WHERE id=?", (m5[9], bad_kid))
    ranks = {k: i + 1 for i, k in enumerate(kids)}   # 산하팀 4개가 5부 1~4위(순위와 무관하게 강등돼야)
    ranks.update({p_: 20 + i for i, p_ in enumerate(pars)})   # 모구단 4팀은 5부 중위권(승격·강등 없음)
    ranks.update({m5[9]: 25})
    ranks.update({ok_kid: 1, bad_kid: 2})
    standings(c, ESP, ranks)
    o5, o6 = order_of(c, L[5]), order_of(c, L[6])
    sizes0 = league_sizes(c)
    run_main(GE)
    s = snapshot(c)
    check("강제 강등 4팀 전부 6부로", all(s[k][1] == 6 for k in kids), [s[k] for k in kids])
    nat = [t for t in o5 if t not in kids]
    check("5부 순위표 끝 팀들은 잔류(강제 강등이 자동 3자리 + 1자리 확장)", all(s[t][1] == 5 for t in nat[-3:]),
          [s[t] for t in nat[-3:]])
    up6 = [t for t in o6 if s[t][1] == 5]
    check("6부에서 4팀 승격(강등 수 확장만큼)", len(up6) == 4, up6)
    check("6부 1위 산하팀(모구단 1부)은 자격 있어 승격", s[ok_kid][1] == 5, s[ok_kid])
    check("6부 2위 산하팀(모구단 5부)은 자격 없어 승격 안 됨", s[bad_kid][1] == 6, s[bad_kid])
    check("리그 팀 수 불변", league_sizes(c) == sizes0)
    check("순서 위반 0건", not ladder_violations(c, ESP), ladder_violations(c, ESP))
    RES["info"]["stats"] = _stats()


def sc_S6(DB, GE):
    c = _conn(DB)
    L = league_ids(c, ESP)
    place(c, RM, 2)
    # 2부 22팀: 자동 2(21·22위), PO 위쪽 20위. 3부 40팀: 자동 2 + PO 4팀(3~6위)
    standings(c, ESP, {RM: 20, CAS: 3})
    run_main(GE)
    slots = po_slots(c)
    up2 = [x[4] for x in slots if x[0] == L[2] and x[2] == "upper"]
    lo3 = [x[4] for x in slots if x[0] == L[2] and x[2] == "lower"]
    check("1군 2부 20위 → 강등 PO(위쪽)", up2 == [RM], up2)
    check("카스티야 3부 3위지만 1군이 지면 2부라 PO 후보 제외", CAS not in lo3, lo3)
    check("3부 PO 후보는 4팀으로 채워짐", len(lo3) == 4, lo3)
    s = snapshot(c)
    check("카스티야 3부 그대로", s[CAS][1] == 3, s[CAS])
    c.execute("DELETE FROM promotion_log"); c.execute("DELETE FROM po_pending_slots")
    place(c, RM, 1)
    standings(c, ESP, {RM: 5, CAS: 3})
    run_main(GE)
    lo3 = [x[4] for x in po_slots(c) if x[0] == L[2] and x[2] == "lower"]
    check("1군 1부(안전) → 카스티야 3부 3위는 PO 후보", CAS in lo3, lo3)


def sc_S7(DB, GE):
    c = _conn(DB)
    L = league_ids(c, ESP)
    place(c, CAS, 2); place(c, RMC, 3)
    # 1부 20팀: 자동 2(19·20위), PO 위쪽 18위
    standings(c, ESP, {RM: 18, CAS: 10, RMC: 20})
    run_main(GE)
    import promotion_playoff_engine as PE
    PE.start_promotion_playoffs(YEAR)
    t = c.execute("SELECT * FROM po_tournaments WHERE year=? AND upper_league_id=?", (YEAR, L[1])).fetchone()
    check("1부/2부 PO 대진 생성", t is not None)
    lower_team = c.execute("SELECT team_id FROM po_pending_slots WHERE year=?", (YEAR,)).fetchone()
    # PO 최종전: 1군(위쪽) vs 2부 PO 후보 중 하나 — 1군 패배로 결과를 정해 실제 확정 함수로 처리
    lo = [r[0] for r in c.execute(
        "SELECT home_team_id FROM po_matches WHERE tournament_id=? AND home_team_id NOT IN (0, ?) "
        "UNION SELECT away_team_id FROM po_matches WHERE tournament_id=? AND away_team_id NOT IN (0, ?)",
        (t["id"], RM, t["id"], RM)).fetchall()]
    winner = sorted(lo)[0]
    m = {"home_team_id": RM, "away_team_id": winner, "home_score": 0, "away_score": 1, "pso_winner": 0}
    for k in ("home_et_score", "away_et_score"):
        m[k] = -1
    conn = DB.get_conn()
    PE._finalize_boundary_match(conn, dict(t), m, YEAR)
    c.execute("UPDATE po_matches SET home_score=0, away_score=0, finalized=1 WHERE tournament_id=?", (t["id"],))
    c.execute("UPDATE po_tournaments SET status='done' WHERE id=?", (t["id"],))
    sizes0 = league_sizes(c)
    pre = snapshot(c)
    GE.sweep_all_affiliate_conflicts(YEAR)
    s = snapshot(c)
    check("PO 결과 그대로: 1군 2부, 승자 1부", s[RM][1] == 2 and s[winner][1] == 1, [s[RM], s[winner]])
    check("카스티야 2부 → 3부 맞교환", s[CAS][1] == 3, s[CAS])
    check("C 3부 → 4부 연쇄 맞교환", s[RMC][1] == 4, s[RMC])
    ups = [t_ for t_ in s if pre[t_][1] - s[t_][1] == 1 and t_ not in (winner,)]
    check("맞교환으로 올라간 팀 2개(3부→2부 1, 4부→3부 1)", len(ups) == 2, ups)
    parts = {r[0] for r in c.execute("SELECT home_team_id FROM po_matches UNION SELECT away_team_id FROM po_matches")}
    check("맞교환 승격 팀은 PO 참가팀이 아님", not (set(ups) & parts), set(ups) & parts)
    o3 = order_of(c, L[3])
    moved_before = {r[0] for r in c.execute("SELECT team_id FROM promotion_log WHERE year=?", (YEAR,))}
    check("리그 팀 수 불변", league_sizes(c) == sizes0)
    v = [x for x in ladder_violations(c, ESP) if x[0] not in parts]
    check("순서 위반 0건(진행 중 PO 참가팀 제외)", not v, v)
    RES["info"]["stats"] = _stats()


def sc_S8(DB, GE):
    """작은 합성 DB에서 resolve_after_po의 '후보 없음'·자격 판정만 본다(실제 세이브 불필요)."""
    import sqlite3
    import affiliate_tiers as A
    db = sqlite3.connect(":memory:")
    db.executescript("""
      CREATE TABLE leagues(id INTEGER PRIMARY KEY, country_id INT, tier INT, name TEXT);
      CREATE TABLE teams(id INTEGER PRIMARY KEY, league_id INT, current_tier INT, country_id INT,
                         classification_status TEXT, parent_team_id INT, name TEXT);
      CREATE TABLE match_results(league_id INT, home_team_id INT, away_team_id INT, home_score INT, away_score INT,
                                 season INT);
      CREATE TABLE promotion_log(year INT, team_name TEXT, from_tier INT, to_tier INT, league_name TEXT,
                                 from_league_id INT, to_league_id INT, team_id INT);
      CREATE TABLE po_pending_slots(year INT, team_id INT);
      CREATE TABLE po_tournaments(id INTEGER PRIMARY KEY, year INT, status TEXT);
      CREATE TABLE po_matches(tournament_id INT, home_team_id INT, away_team_id INT);
      CREATE TABLE season_state(id INT, current_season INT);
      INSERT INTO season_state VALUES(1, 1);
      INSERT INTO leagues VALUES(1, 9, 1, 'A1'), (2, 9, 2, 'A2'), (3, 9, 3, 'A3');
    """)
    # 1부: P(모구단), X ; 2부: K(산하팀, 위반 — 모구단이 PO로 2부에 내려왔다고 가정), Y ; 3부: Z1(이번 시즌 이미 이동), Z2(PO 참가)
    T = [(10, 1, 1, "NORMAL", None, "X"), (11, 2, 2, "NORMAL", None, "P"), (12, 2, 2, "AFFILIATE", 11, "K"),
         (13, 3, 3, "NORMAL", None, "Z1"), (14, 3, 3, "NORMAL", None, "Z2"), (15, 3, 3, "AFFILIATE", 12, "KK")]
    db.executemany("INSERT INTO teams VALUES(?,?,?,9,?,?,?)", T)
    for tid, lid in ((13, 3), (14, 3), (15, 3)):
        db.execute("INSERT INTO match_results VALUES(?,?,?,?,?,1)", (lid, tid, tid, 20 - tid, 0))
    db.execute("INSERT INTO promotion_log VALUES(2000,'Z1',2,3,'A2',2,3,13)")   # Z1은 이번 시즌 이미 이동
    db.execute("INSERT INTO po_tournaments VALUES(1, 2000, 'done')")
    db.execute("INSERT INTO po_matches VALUES(1, 14, 10)")                       # Z2는 PO 참가팀
    sizes0 = dict(db.execute("SELECT league_id, COUNT(*) FROM teams GROUP BY league_id").fetchall())
    moves = A.resolve_after_po(db.cursor(), 2000)
    s = {r[0]: r[1] for r in db.execute("SELECT id, current_tier FROM teams")}
    st = A.STATS.get(2000, {})
    check("후보 없음: 아무도 옮기지 않음", moves == [], moves)
    check("후보 없음: 예외로 집계", any(x[0] == 12 for x in st.get("po_stuck", [])), st.get("po_stuck"))
    check("이미 이동한 팀·PO 참가팀·자격 없는 산하팀(KK — 위 팀 K가 2부)을 올리지 않음",
          s[13] == 3 and s[14] == 3 and s[15] == 3, s)
    check("팀 수 그대로", dict(db.execute("SELECT league_id, COUNT(*) FROM teams GROUP BY league_id")) == sizes0)
    # 정상 후보가 생기면(다음 시즌 재검사와 같은 상황) 그때 처리
    db.execute("INSERT INTO teams VALUES(16, 3, 3, 9, 'NORMAL', NULL, 'Z3')")
    db.execute("UPDATE teams SET league_id=2, current_tier=2 WHERE id=10")       # 팀 수 맞추기용(1부 X → 2부)
    db.execute("UPDATE teams SET league_id=1, current_tier=1 WHERE id=11")       # 모구단은 1부로 복귀했다고 가정
    db.execute("INSERT INTO match_results VALUES(3, 16, 16, 1, 0, 1)")
    A.STATS.clear()
    moves = A.resolve_after_po(db.cursor(), 2000)
    check("모구단이 위에 있으면 위반 아님 → 옮기지 않음(재검사 시 자격·상황 다시 확인)", moves == [], moves)


def sc_S9(DB, GE):
    c = _conn(DB)
    v0 = ladder_violations(c)
    RES["info"]["start_violations"] = v0
    standings(c, None, {}, all_countries=True)
    sizes0 = league_sizes(c)
    pre = snapshot(c)
    run_main(GE)
    s = snapshot(c)
    slots_ids = {x[4] for x in po_slots(c)}
    v = [x for x in ladder_violations(c) if x[0] not in slots_ids and x[1] not in slots_ids]
    check("시작 월드 위반 있음(테스트 전제)", len(v0) > 0, len(v0))
    check("정규 처리 뒤 순서 위반 0건(PO 진행 중 팀 관련 제외)", not v, v[:10])
    check("리그 팀 수 불변(전 세계)", league_sizes(c) == sizes0)
    import affiliate_tiers as A
    Lm = A._team_maps(c)
    aff = Lm[7]
    up = A.build_upper_map(aff, {t: pre[t][1] for t in pre}, A.load_prev_levels(c))
    promoted = [t for t in aff if s[t][1] < pre[t][1]]
    bad = [t for t in promoted if not (s[up[t]][1] < s[t][1] or up[t] in slots_ids)]
    check("산하팀 승격 발생(일괄 제외 해제)", len(promoted) > 0, len(promoted))
    check("무자격 승격 0건(승격 후 위 팀보다 위·같은 부 아님)", not bad, bad[:10])
    RES["info"]["promoted_affiliates"] = len(promoted)
    RES["info"]["stats"] = _stats()


def sc_OFFDUMP(DB, GE):
    """S10 내부용: 배치 하나(S1·S9)를 스위치 OFF(또는 원본 코드)로 돌려 결과 덤프."""
    c = _conn(DB)
    kind = os.environ.get("AFF_DUMP_KIND", "S1")
    if kind == "S1":
        place(c, RM, 4); place(c, CAS, 5); place(c, RMC, 6)
        standings(c, ESP, {RM: -1, CAS: 1, RMC: 5})
    else:
        standings(c, None, {}, all_countries=True)
    run_main(GE)
    RES["info"]["dump"] = {"teams": sorted(snapshot(c).items()), "plog": plog(c), "po": po_slots(c)}


def sc_S11(DB, GE):
    c = _conn(DB)
    import affiliate_tiers as A
    v0 = ladder_violations(c)
    sizes0 = league_sizes(c)
    pre = snapshot(c)
    n = A.fix_start_tiers(c)
    s1 = snapshot(c)
    check("시작 월드 위반 있음(전제)", len(v0) > 0, v0)
    check("정리 후 순서 위반 0건", not ladder_violations(c), ladder_violations(c))
    check("리그 팀 수 불변", league_sizes(c) == sizes0)
    moved = [t for t in s1 if s1[t] != pre[t]]
    check("옮겨진 팀 = 맞교환 수 × 2", len(moved) == 2 * n, (n, len(moved)))
    n2 = A.fix_start_tiers(c)
    check("한 번 더 돌려도 변화 없음", n2 == 0 and snapshot(c) == s1, n2)
    RES["info"]["start_swaps"] = n
    RES["info"]["moved"] = [(t, pre[t][1], s1[t][1]) for t in moved]
    # 새 게임 생성 경로(reset_game_data — 선수 재생성은 건너뜀): 원본 배치 복원 직후 같은 정리가 도는지
    DB.reset_game_data(skip_ai_regen=True)
    c = _conn(DB)
    check("새 게임 생성 경로에서도 위반 0건", not ladder_violations(c), ladder_violations(c))
    check("새 게임 생성 경로: 산하팀 단계표 비움", c.execute("SELECT COUNT(*) FROM affiliate_ladder").fetchone()[0] == 0
          if c.execute("SELECT name FROM sqlite_master WHERE name='affiliate_ladder'").fetchone() else True)


SCEN = {"S1": sc_S1, "S2": sc_S2, "S3": sc_S3, "S4": sc_S4, "S5": sc_S5, "S6": sc_S6, "S7": sc_S7,
        "S8": sc_S8, "S9": sc_S9, "S11": sc_S11, "OFFDUMP": sc_OFFDUMP}


def child(name):
    import io, contextlib
    switch = os.environ.get("AFF_SWITCH", "1") == "1"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            DB, GE, tmp = _boot(switch)
            SCEN[name](DB, GE)
    except Exception:
        import traceback
        RES["checks"].append(["예외 없이 실행", False, traceback.format_exc()[-1500:]])
    RES["info"]["traceback_in_log"] = "Traceback" in buf.getvalue()
    print("@@RESULT@@" + json.dumps(RES, ensure_ascii=False, default=str))


# ═══════════════════════════════════════════════════════════════════════
# 부모 프로세스
# ═══════════════════════════════════════════════════════════════════════
def spawn(name, env_extra=None, root=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    if root:
        env["AFF_TEST_ROOT"] = root
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", name], capture_output=True,
                       text=True, env=env, timeout=1800)
    out = p.stdout
    i = out.rfind("@@RESULT@@")
    if i < 0:
        return {"checks": [["자식 프로세스 결과", False, (p.stderr or out)[-1500:]]], "info": {}}
    return json.loads(out[i + len("@@RESULT@@"):].strip().splitlines()[0])


def main():
    want = [a for a in sys.argv[1:] if not a.startswith("-")] or \
        ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"]
    fails = 0
    summary = {}
    for name in want:
        print(f"\n[{name}]", flush=True)
        if name == "S10":
            orig = os.environ.get("AFF_ORIG_ROOT")
            res = {"checks": [], "info": {}}
            if not orig:
                res["checks"].append(["작업 전 원본 폴더(AFF_ORIG_ROOT) 지정", False, "환경변수 없음"])
            else:
                for kind in ("S1", "S9"):
                    a = spawn("OFFDUMP", {"AFF_SWITCH": "0", "AFF_DUMP_KIND": kind})
                    b = spawn("OFFDUMP", {"AFF_SWITCH": "0", "AFF_DUMP_KIND": kind}, root=orig)
                    da, db_ = a["info"].get("dump"), b["info"].get("dump")
                    for r in a["checks"] + b["checks"]:
                        res["checks"].append(r)
                    ok = da is not None and da == db_
                    diff = ""
                    if da and db_ and not ok:
                        for key in ("teams", "plog", "po"):
                            if da[key] != db_[key]:
                                diff += f"{key} 다름 "
                    res["checks"].append([f"스위치 OFF = 작업 전 코드 ({kind} 배치: 팀 소속·승강 기록·PO 대진)", ok, diff])
                    res["info"][f"{kind}_counts"] = (len(da["plog"]) if da else None, len(da["po"]) if da else None)
        else:
            res = spawn(name)
        for nm, ok, det in res["checks"]:
            print(("  [OK] " if ok else "  [FAIL] ") + nm + (f" — {det}" if (det and not ok) else ""), flush=True)
            fails += (not ok)
        if res["info"].get("traceback_in_log"):
            print("  [FAIL] 실행 로그에 Traceback 있음")
            fails += 1
        summary[name] = res["info"]
    print("\n[INFO]", json.dumps(summary, ensure_ascii=False, default=str)[:4000])
    print(f"\n실패 {fails}건")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
    else:
        main()