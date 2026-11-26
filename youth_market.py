"""[2026-11-26 신설, 신민용 확정 — 유스 진로 시장(S5, constants.VY_V3_MARKET)] 유스(가상 유스팀) 선수가 전 세계 성인 구단으로
직접 진출하는 오프시즌 단계. 유스 생성 V3(VY_GEN_V3)에서만 돈다.

원칙: 유스에 들어온 선수는 실력에 맞는 성인 무대에 들어갈 기회를 충분히 얻는다. 출신 구단과 데뷔 구단은 별개다(모구단에 자리가
없으면 국내·해외 다른 구단). 실력이 부족한 선수는 올리지 않는다. 밀려난 성인 선수도 삭제하지 않는다.

시점: 오프시즌 이적시장 뒤, 24세 정리·인원 보충 앞(ai_lifecycle.run_ai_offseason) — 인원 보충이 신인으로 채울 빈자리를 유스가 먼저
가져가고, 24세가 된 유스도 정리 전에 마지막 기회를 얻는다.

대상: 18세 이상 유스 + 16~17세 특급(vy3_talent 등급 2). 이번 창구에 생긴 유스(last_transfer_year = 올해)는 제외. OVR 높은 순.

들어갈 수 있는 팀(군 팀 제외 모든 성인 팀):
  · 수준: 리그 하한 − VY_V3_MKT_LO ≤ OVR ≤ 리그 상한 + VY_V3_MKT_HI (하한·상한은 신인 생성과 같은 대륙·나라 보정)
  · 자리: 빈자리(인원 < 정원 이고 그 칸이 포메이션 목표표보다 모자람) 우선, 없으면 밀어내기(같은 칸 최약체 OVR ≤ 유스 OVR −
    VY_V3_MKT_DISP_MARGIN — 임대 온 선수·올해 들어온 선수 제외, 사용자 감독 구단은 밀어내기 없음, 내 선수는 ai_players에 없어
    애초에 대상이 아님). 밀려날 선수가 갈 곳(이적 → 임대 → 자유 이적, 현지 → 본국 → 같은 대륙 → 세계)을 먼저 찾고, 못 찾으면
    그 팀으로는 밀어내기를 하지 않는다(삭제로 해결하지 않는다).
  · 외국인 정원: 유스 국적이 그 팀 나라와 다르면 외국인 자리가 남아 있어야 한다(내 선수 구단 포함 같은 규칙).
  · 팀당 한 오프시즌 최대 VY_V3_MKT_TEAM_CAP명, 그중 밀어내기 최대 VY_V3_MKT_DISP_CAP명.
팀 고르기: 자기 구단 사다리(1군 → B팀 → C팀)에 맞으면 먼저. 아니면 점수 = 리그 하한 − 지역 감점(국내 0 / 같은 대륙 5 / 다른 대륙
10, VY_V3_MKT_PEN)이 가장 높은 팀 — 감점은 선호일 뿐 막는 규칙이 아니다(스페인에 자리가 없으면 한국 2부도 간다).
한 팀이 안 되면 다음 후보를 계속 찾는다.
계약: 이적료 0, 연봉 기존 공식(0이면 승격 연봉 하한), 계약 기간 기존 신인 공식. 기록 종류: 유스 승격(1군)/(B팀)/(C팀), 유스 외부 데뷔.
난수: 계약 기간·동점 순서만 쓰고, 유스 전용 난수 구간(virtual_youth._v3_rng) 안에서 쓴다 — 게임 난수 흐름을 소비하지 않는다."""
import bisect
import collections
import random

import constants as _C
import club_ledger as _CL
import virtual_youth as _VY

TYPE_FIRST = "유스 승격(1군)"
TYPE_B = "유스 승격(B팀)"
TYPE_C = "유스 승격(C팀)"
TYPE_EXT = "유스 외부 데뷔"
DISP_SALE = "유스 진출 밀림 이적"
DISP_LOAN = "유스 진출 밀림 임대"
DISP_FREE = "유스 진출 밀림 자유 이적"
STATS = collections.Counter()     # 계측(측정 도구가 읽는다)
EVENTS = []                       # (연도, 선수, 나이, OVR, 출신 유스팀, 받은 팀, 지역, 방식) — 측정 도구가 읽고 비운다


def on():
    return _VY.v3() and bool(getattr(_C, "VY_V3_MARKET", False))


def _special_ids(cur):
    try:
        return {r[0] for r in cur.execute("SELECT player_id FROM vy3_talent WHERE tier=2")}
    except Exception:
        return set()


def run(c, year):
    if not on():
        return {}
    cur = _VY._cur(c)
    with _VY._v3_rng(cur, "market", year):
        return _run(cur, year)


def _run(cur, year):
    import ai_lifecycle as _AL
    from database import is_roster_foreign, get_foreign_quota_range
    from constants import get_country_league_grade, get_ovr_range, AI_LOAN_DURATION_YEARS
    from economy import estimate_transfer_fee
    lo_m = int(getattr(_C, "VY_V3_MKT_LO", 3))
    hi_m = int(getattr(_C, "VY_V3_MKT_HI", 3))
    hi_fl = getattr(_C, "VY_V3_MKT_HI_FLOOR", None)
    dmarg = int(getattr(_C, "VY_V3_MKT_DISP_MARGIN", 5))
    tcap = int(getattr(_C, "VY_V3_MKT_TEAM_CAP", 2))
    dcap = int(getattr(_C, "VY_V3_MKT_DISP_CAP", 1))
    pen = dict(getattr(_C, "VY_V3_MKT_PEN", {"dom": 0, "cont": 5, "world": 10}))
    res = collections.Counter()

    # ── 성인 팀 ──
    rows = cur.execute("""SELECT t.id, l.tier, cn.name, cn.continent, t.name, t.formation, t.classification_status,
                                 t.parent_team_id
                          FROM teams t JOIN leagues l ON t.league_id = l.id JOIN countries cn ON l.country_id = cn.id
                          ORDER BY t.id""").fetchall()
    mil = set()
    try:
        import military_service as _MS
        mil = set(_MS.get_military_team_ids(cur.connection if hasattr(cur, "connection") else cur))
    except Exception:
        pass
    meta = {}
    gc = {}
    kids = collections.defaultdict(list)
    for tid, tier, cn, ct, tname, f, cls, par in rows:
        if tid in mil or cls == _VY.STATUS:
            continue
        if cn not in gc:
            gc[cn] = get_country_league_grade(cn)
        gr = gc[cn]
        tier = tier or 1
        fl = _VY._v3_floor(cn, ct, tier)
        rng = get_ovr_range(gr, tier, cn)
        hi = None
        if rng:
            hi = rng[1] + (0 if cn in _C.COUNTRY_LEAGUE_OVR_OVERRIDE else
                           round(_C.CONTINENT_OVR_BONUS.get(ct, 0) + _C.COUNTRY_OVR_ADJ.get(cn, 0)))
        smax = _AL._squad_min_max(gr, tier)[1]
        hi = hi if hi is not None else 55
        if hi_fl is not None:
            # [2026-11-27 확정] 위쪽 한도 = min(리그 상한 + MKT_HI, 리그 하한 + VY_V3_MKT_HI_FLOOR)
            hi = min(hi, (fl if fl is not None else 40) + int(hi_fl) - hi_m)
        meta[tid] = dict(cname=cn, cont=ct, grade=gr, tier=tier, tname=tname, f=f,
                         floor=(fl if fl is not None else 40), hi=hi,
                         ceil=(rng[1] if rng else 43), qhi=get_foreign_quota_range(cn, ct, tier=tier)[1],
                         smax=smax, tg=_CL.table(f, smax))
        if cls == "AFFILIATE" and par:
            kids[par].append(tid)
    for p in kids:
        kids[p].sort(key=lambda t: (meta[t]["tier"], t))
    me = None
    try:
        r = cur.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
        me = r[0] if r and r[0] else None
    except Exception:
        pass
    try:
        import team_plan as _TP
        utm = set(_TP.user_control(cur.connection if hasattr(cur, "connection") else None))
    except Exception:
        utm = set()

    # ── 선수단 ──
    by_team = collections.defaultdict(list)
    for r in cur.execute("SELECT id, team_id, position, nationality, ovr, salary, on_loan_from_team_id, name, age, "
                         "contract_end_year, COALESCE(last_transfer_year, 0) FROM ai_players "
                         "WHERE team_id IS NOT NULL").fetchall():
        by_team[r[1]].append(list(r))
    size = {t: len(by_team.get(t, ())) for t in meta}
    if me in size:
        size[me] += 1
    curf = {t: sum(1 for r in by_team.get(t, ()) if is_roster_foreign(r[3], meta[t]["cname"])) for t in meta}
    cnt = {t: collections.Counter(_CL.cells_of(r[2], None)[0] for r in by_team.get(t, ())) for t in meta}
    grp = {t: collections.Counter(_AL._POS_GROUP.get(r[2], "FW") for r in by_team.get(t, ())) for t in meta}
    s11 = {}
    for t in meta:
        o = sorted(((r[4] or 0) for r in by_team.get(t, ())), reverse=True)
        s11[t] = o[min(10, len(o) - 1)] if o else 0
    rowof = {r[0]: r for v in by_team.values() for r in v}
    inbound = collections.Counter()
    dused = collections.Counter()
    cs = cur.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    season = cs[0] if cs else 0

    # 밀려난 선수 갈 곳 색인(받을 팀 주전 11번째 OVR 기준 — youth_reform.expire와 같은 방식)
    aff_all = set(_AL._youth_aff(cur))
    idx_all = sorted((s11[t], t) for t in meta if t not in aff_all and t != me and t not in utm)
    idx_cn, idx_ct = collections.defaultdict(list), collections.defaultdict(list)
    for s_, t in idx_all:
        idx_cn[meta[t]["cname"]].append((s_, t))
        idx_ct[meta[t]["cont"]].append((s_, t))
    _keys = {}

    def keys_of(ix):
        k = _keys.get(id(ix))
        if k is None:
            k = _keys[id(ix)] = [a for a, _t in ix]
        return k
    gcap = _C.YOUTH_POS_GROUP_CAP

    def d_ok(p, t, margin, avoid):
        m = meta.get(t)
        if m is None or t in avoid or size.get(t, 0) >= m["smax"]:
            return None
        gk = _AL._POS_GROUP.get(p[2], "FW")
        if grp[t].get(gk, 0) >= gcap.get(gk, 99):
            return None
        ns = is_roster_foreign(p[3], m["cname"])
        if ns and (m["qhi"] is None or curf.get(t, 0) + 1 > m["qhi"]):
            return None
        o = p[4] or 0
        if o < s11.get(t, 0) - margin or _AL._dst_ceiling_excluded(o, m["ceil"]):
            return None
        return ns

    full = set()       # 정원이 찬 팀(밀려날 선수 받을 곳 후보에서 빨리 건너뛴다)
    MARGINS = ((0, "sale"), (_C.YOUTH_CLEANUP_FIT_MARGIN, "sale"), (_C.YOUTH_CLEANUP_LOAN_MARGIN, "loan"),
               (_C.YOUTH_CLEANUP_FREE_MARGIN, "free"))

    def d_scan(p, ix, avoid, excl_cn=(), excl_ct=None):
        """한 지역 색인 ix를 위(주전 11번째 OVR ≤ 선수 OVR + 가장 큰 여유폭)에서부터 한 번 훑어, 여유폭 단계마다
        처음 맞는 팀을 고른다 → 가장 작은 여유폭 단계의 (팀, 외국인 여부, 종류). youth_reform.expire가 여유폭마다
        따로 훑던 것과 같은 결과(단계 우선, 같은 단계 안에서는 주전 11번째 OVR이 높은 팀)."""
        o = p[4] or 0
        i = bisect.bisect_right(keys_of(ix), o + MARGINS[-1][0]) - 1
        found = [None] * len(MARGINS)
        steps = 0
        while i >= 0 and steps < 3000:
            s_, t = ix[i]
            i -= 1
            if t in full or t in avoid:
                continue
            m = meta[t]
            if m["cname"] in excl_cn or (excl_ct is not None and m["cont"] == excl_ct):
                continue
            steps += 1
            for j, (mg, kind) in enumerate(MARGINS):
                if found[j] is not None or o < s_ - mg:
                    continue
                ns = d_ok(p, t, mg, avoid)
                if ns is None:
                    if size.get(t, 0) >= m["smax"]:
                        full.add(t)
                    break                 # 이 팀은 여유폭과 무관한 이유로 안 된다(정원·포지션·외국인·상한)
                found[j] = (t, ns, kind)
            if found[0] is not None:
                break                     # 가장 작은 여유폭 단계를 찾았다
        for h in found:
            if h is not None:
                return h
        return None

    def d_dest(p, S, avoid):
        """밀려날 선수 p(팀 S)의 갈 곳 → (팀, 외국인 여부, 종류) 또는 None. 지역: 현지 → 본국 → 같은 대륙 → 세계."""
        nat = p[3] or ""
        cn, ct = meta[S]["cname"], meta[S]["cont"]
        stages = [(idx_cn.get(cn, []), (), None)]
        if nat and nat != cn and nat in idx_cn:
            stages.append((idx_cn[nat], (), None))
        stages.append((idx_ct.get(ct, []), {cn, nat}, None))
        stages.append((idx_all, {cn, nat}, ct))
        for ix, ecn, ect in stages:
            h = d_scan(p, ix, avoid, ecn, ect)
            if h:
                return h
        return None

    # ── 유스 후보 ──
    closed = _VY.sa_closed(cur)
    yt = {r[0]: (r[1], r[2]) for r in cur.execute(
        "SELECT t.id, t.parent_team_id, cn.name FROM teams t JOIN countries cn ON cn.id = t.country_id "
        "WHERE t.classification_status=?", (_VY.STATUS,)).fetchall() if r[0] not in closed}
    special = _special_ids(cur)
    cand = []
    for vt in sorted(yt):
        for r in by_team.get(vt, ()):
            pid, pos, nat, ovr, age, lty = r[0], r[2], r[3], r[4] or 0, r[8] or 0, r[10]
            if pos == "GK" or lty == year or age < 16:
                continue
            if age <= 17 and pid not in special:
                continue
            cand.append((ovr, pid, vt))
    cand.sort(key=lambda x: (-x[0], x[1]))
    res["candidates"] = len(cand)

    # 받을 팀 색인: (칸, 지역 키) → {리그 하한: [팀...]} + 묶음별 상한 최대 — 유스 OVR에 안 맞는 하한 묶음은 통째로 건너뛴다.
    # 지역 키: ("c", 나라) / ("t", 대륙) — 다른 대륙은 그 대륙 키들을 함께 훑는다. 같은 하한 안 순서는 난수(동점 순서). 자리가 영구히 없어진 팀은 지운다.
    cells = [x for x in _CL.FIELD if x != "GK"]
    gap_max = max((m["hi"] - m["floor"]) for m in meta.values()) if meta else 40
    order = sorted(meta, key=lambda t: (meta[t]["floor"], random.random()))
    buckets = {}
    bhi = {}
    for x in cells:
        for t in order:
            m = meta[t]
            for key in (("c", m["cname"]), ("t", m["cont"])):
                buckets.setdefault((x, key), collections.defaultdict(list))[m["floor"]].append(t)
                bk = (x, key, m["floor"])
                bhi[bk] = max(bhi.get(bk, -1), m["hi"])
    dead_cell = {x: set() for x in cells}
    conts = sorted({m["cont"] for m in meta.values()}, key=str)
    qfull = set()      # 외국인 정원이 찬 팀(외국인 유스에게는 다시 열리지 않는다고 본다 — 이번 단계 안에서)

    _wk = {}           # (팀, 칸) → 밀어낼 수 있는 최약체 행(임대 온 선수·올해 들어온 선수 제외) 또는 None — 팀이 바뀌면 지운다

    def _weakest_row(t, cell):
        k = (t, cell)
        if k in _wk:
            return _wk[k]
        best = None
        for r in by_team.get(t, ()):
            if r[6] or r[10] == year or _CL.cells_of(r[2], None)[0] != cell:
                continue
            if best is None or (r[4] or 0) < (best[4] or 0) or ((r[4] or 0) == (best[4] or 0) and r[0] < best[0]):
                best = r
        _wk[k] = best
        return best

    def _touch(t):
        for x in cells:
            _wk.pop((t, x), None)

    def weakest(t, cell, o):
        best = _weakest_row(t, cell)
        if best is not None and (best[4] or 0) <= o - dmarg:
            return best
        return None

    blocked = set()    # (팀, 칸, 밀려날 선수) — 갈 곳이 없어 밀어내기를 못 한 조합

    def vac_ok(t, cell):
        m = meta[t]
        return size[t] < m["smax"] and cnt[t][cell] < m["tg"].get(cell, 0)

    def feasible(p_ovr, p_nat, cell, t):
        """→ ("vac", None) / ("disp", 밀려날 선수 행) / None — 밀려날 선수의 갈 곳은 아직 보지 않는다(confirm)."""
        m = meta[t]
        if inbound[t] >= tcap:
            return None
        if p_ovr < m["floor"] - lo_m or p_ovr > m["hi"] + hi_m:
            return None
        if p_nat != m["cname"] and (m["qhi"] is None or curf[t] + 1 > m["qhi"]):
            return None
        if vac_ok(t, cell):
            return ("vac", None)
        if dused[t] >= dcap or t in utm:
            return None
        w = weakest(t, cell, p_ovr)
        if w is None or (t, cell, w[0]) in blocked:
            return None
        return ("disp", w)

    def confirm(t, cell, fz):
        """밀어내기면 갈 곳을 찾아 붙인다. 못 찾으면 None(그 조합은 막아 둔다)."""
        if fz[0] == "vac":
            return fz
        dst = d_dest(fz[1], t, {t})
        if dst is None:
            blocked.add((t, cell, fz[1][0]))
            res["disp_no_dest"] += 1
            return None
        return ("disp", (fz[1], dst))

    def dead_for(t, cell, o):
        """이 칸에 다시는 못 받는 팀 — 정원 찼거나, 빈자리 없고 밀어내기도 영영 불가(유스 OVR은 내림차순이라
        지금 최약체를 못 넘으면 뒤의 유스도 못 넘는다)."""
        if inbound[t] >= tcap:
            return True
        if vac_ok(t, cell):
            return False
        if dused[t] >= dcap or t in utm:
            return True
        return weakest(t, cell, o) is None

    def apply_in(pid, vt, t, how, ttype, region, ovr, age, nat, pos):
        m = meta[t]
        sal = _AL._calc_ai_salary(m["grade"], m["tier"], ovr, m["cname"], m["tname"], t, year)
        sal, _fl = _AL._promotion_salary_floor(m, ovr, t, year, sal)
        lo_c, hi_c = _AL._ai_contract_duration_range(age or 20, ovr=ovr)
        cend = year + random.randint(lo_c, hi_c)
        cur.execute("UPDATE ai_players SET team_id=?, contract_end_year=?, last_transfer_year=?, salary=? WHERE id=?",
                    (t, cend, year, sal, pid))
        cur.execute("""INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (season, year, pid, "", pos, age, ovr, vt, t, 0, 0, 0.0, 0.0, ttype, 0, "", 0, 0, 0, sal, cend))
        row = rowof[pid]
        by_team[vt].remove(row)
        row[1] = t
        row[10] = year
        by_team[t].append(row)
        size[t] += 1
        cnt[t][_CL.cells_of(pos, None)[0]] += 1
        grp[t][_AL._POS_GROUP.get(pos, "FW")] += 1
        if is_roster_foreign(nat, m["cname"]):
            curf[t] += 1
        inbound[t] += 1
        _touch(t)
        STATS[f"in|{region}|{how}"] += 1
        res[f"in|{region}|{how}"] += 1
        res[f"age|{min(age, 24)}"] += 1
        EVENTS.append((year, pid, age, ovr, vt, t, region, how))

    def apply_out(w, S, dst):
        bt, ns, kind = dst
        bm = meta[bt]
        pid = w[0]
        nat = w[3] or ""
        size[bt] += 1
        size[S] -= 1
        if ns:
            curf[bt] += 1
        if is_roster_foreign(nat, meta[S]["cname"]):
            curf[S] = max(0, curf[S] - 1)
        cell = _CL.cells_of(w[2], None)[0]
        cnt[S][cell] -= 1
        cnt[bt][cell] += 1
        gk = _AL._POS_GROUP.get(w[2], "FW")
        grp[bt][gk] += 1
        grp[S][gk] -= 1
        sal = _AL._calc_ai_salary(bm["grade"], bm["tier"], w[4], bm["cname"], bm["tname"], bt, year)
        if kind == "loan":
            ret = year + AI_LOAN_DURATION_YEARS[0]
            cur.execute("UPDATE ai_players SET team_id=?, on_loan_from_team_id=?, loan_return_year=?, "
                        "last_transfer_year=? WHERE id=?", (bt, S, ret, year, pid))
            log = (season, year, pid, w[7] or "", w[2], w[8] or 0, w[4], S, bt, 0, 0, 0.0, 0.0, DISP_LOAN, 0, "",
                   0, 1, ret, w[5] or 0, w[9] or 0)
            w[6] = S
        else:
            if kind == "sale":
                cend = max(w[9] or 0, year + 1)
                fee = estimate_transfer_fee(bm["grade"], bm["tier"], w[4], country=bm["cname"], position=w[2],
                                            year=year, player_id=pid) or 0
                tt = DISP_SALE
            else:
                cend, fee, tt = year + 2, 0, DISP_FREE
            cur.execute("UPDATE ai_players SET team_id=?, salary=?, contract_end_year=?, last_transfer_year=? "
                        "WHERE id=?", (bt, sal, cend, year, pid))
            log = (season, year, pid, w[7] or "", w[2], w[8] or 0, w[4], S, bt, 0, 0, 0.0, 0.0, tt, 0, "", fee, 0, 0,
                   sal, cend)
        cur.execute("""INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", log)
        by_team[S].remove(w)
        w[1] = bt
        w[10] = year
        by_team[bt].append(w)
        dused[S] += 1
        _touch(S)
        _touch(bt)
        STATS[f"disp|{kind}"] += 1
        res[f"disp|{kind}"] += 1

    for ovr, pid, vt in cand:
        row = rowof.get(pid)
        if row is None or row[1] != vt:
            continue
        par, ycn = yt[vt]
        pos, nat, age = row[2], row[3] or ycn, row[8] or 0
        cell = _CL.cells_of(pos, None)[0]
        ycont = meta[par]["cont"] if par in meta else None
        done = False
        # ① 자기 구단 사다리: 1군 → B팀 → C팀
        ladder = ([par] if par in meta else []) + kids.get(par, [])
        for i, t in enumerate(ladder):
            fz = feasible(ovr, nat, cell, t)
            if fz is not None:
                fz = confirm(t, cell, fz)
            if fz is None:
                continue
            if fz[0] == "disp":
                apply_out(fz[1][0], t, fz[1][1])
            ttype = TYPE_FIRST if i == 0 else (TYPE_B if i == 1 else TYPE_C)
            apply_in(pid, vt, t, fz[0], ttype, "own", ovr, age, nat, pos)
            done = True
            break
        if done:
            continue
        # ② 전 세계: 점수 = 리그 하한 − 지역 감점. 국내 → 같은 대륙 → 다른 대륙 순으로 하한 묶음을 내려가며, 남은 묶음이
        #    지금까지 찾은 점수를 못 넘으면 멈춘다. 한 팀이 안 되면 다음 후보를 계속 찾는다.
        best = None
        dset = dead_cell[cell]
        ycn = nat
        while True:
            best = None
            best_score = None
            for rk, keys, pv in (("dom", [("c", ycn)], 0), ("cont", [("t", ycont)], pen.get("cont", 5)),
                                 ("world", [("t", c2) for c2 in conts if c2 != ycont], pen.get("world", 10))):
                bkts = [(key, buckets.get((cell, key))) for key in keys]
                bkts = [(key, b) for key, b in bkts if b]
                if not bkts:
                    continue
                f = min(120, ovr + lo_m)
                f_end = max(0, ovr - hi_m - gap_max)
                found = False
                while f >= f_end and not found:
                    if best_score is not None and f - pv <= best_score:
                        break
                    for key, bkt in bkts:
                        lst = bkt.get(f)
                        if not lst or bhi.get((cell, key, f), -1) + hi_m < ovr:
                            continue
                        k = 0
                        while k < len(lst):
                            t = lst[k]
                            m = meta[t]
                            if t in dset:
                                lst.pop(k)
                                continue
                            k += 1
                            if t in ladder:
                                continue
                            treg = "dom" if m["cname"] == ycn else ("cont" if m["cont"] == ycont else "world")
                            if treg != rk:
                                continue
                            if treg != "dom" and t in qfull:
                                continue
                            if dead_for(t, cell, ovr):
                                dset.add(t)
                                continue
                            if m["hi"] + hi_m < ovr:
                                continue
                            if treg != "dom" and (m["qhi"] is None or curf[t] + 1 > m["qhi"]):
                                qfull.add(t)
                                continue
                            fz = feasible(ovr, nat, cell, t)
                            if fz is None:
                                continue
                            best, best_score = (t, fz, treg), f - pv
                            found = True
                            break
                        if found:
                            break
                    f -= 1
            if best is None:
                break
            fz = confirm(best[0], cell, best[1])
            if fz is not None:
                best = (best[0], fz, best[2])
                break
            # 밀려날 선수 갈 곳이 없으면 그 조합을 막고(blocked) 다음 후보를 다시 찾는다
        if best is None:
            res["no_place"] += 1
            res[f"no_place_age|{min(age, 24)}"] += 1
            continue
        t, fz, reg = best
        if fz[0] == "disp":
            apply_out(fz[1][0], t, fz[1][1])
        apply_in(pid, vt, t, fz[0], TYPE_EXT, reg, ovr, age, nat, pos)
    for k, v in res.items():
        if not k.startswith(("in|", "disp|", "age|")):
            STATS[k] += v
    return dict(res)