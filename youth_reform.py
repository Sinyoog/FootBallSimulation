"""[2026-11-25 신설, 신민용 확정 — 유스 개편(constants.VY_REFORM)] 가상 유스(VIRTUAL_YOUTH)의 육성 승격·외부 데뷔·유스 전용 정원.

설계(정적 검증 yt_static 1~5 뒤 동결): constants.VY_REFORM 주석 참고. 이 모듈은 오프시즌 두 지점과 인원 재조정 한 곳에서만 불린다.
  pre_market(c, year)  — 은퇴 처리 + 임대 복귀 직후(여름 시장 전):
      A. 유스 전용 정원 선수 편입(은퇴 빈자리, C2) → B. 육성 승격(모구단마다 연 1명) → C. 외부 데뷔(은퇴 빈자리 → 기존 빈자리)
  expire(c, year)      — 시장·보충 뒤, 인원 재조정 직전: 유예(VY_REFORM_GRACE시즌)가 끝났는데 정원 밖인 육성 승격 선수 정리
  allowance_map(c, year) — 인원 재조정 상한에 더할 유스 전용 정원(유예 중인 육성 승격 선수 수, 최대 VY_REFORM_SLOTS)

유스 전용 정원은 표시(플래그) 없이 인원으로만 센다: 모구단 소속 인원(시장·인원 재조정과 같은 기준)이 일반 정원을 넘는 만큼이
전용 정원 점유다. 그래서 영구 이탈로 인원이 일반 정원까지 내려오면 시장(사는 팀 상한 = 일반 정원)이 그 자리를 다시 사지 않는다 —
전용 정원 선수가 시장 영입보다 먼저 그 자리에 들어간 것과 같다. 임대 이탈은 영구 편입이 아니다(_slot_counts 주석).
은퇴 빈자리는 이번 오프시즌 은퇴 대체 신인(retire_repl)을 지우는 방식으로 넘긴다 — 신인 생성까지는 예전과 같은 경로·난수로 돈다.
신인을 지워도 되는지(C2): 그 신인을 빼고(육성이면 유스를 넣고) 주포 부족 L1·L2가 늘지 않을 때만 — D안이 쓸 자리는 남긴다.
난수: 이 모듈 자체는 난수를 쓰지 않는다(DB 이적 반영의 계약 기간만 기존 _ledger_db_transfer 그대로)."""
import bisect
import collections

import constants as _C
import club_ledger as _CL
import virtual_youth as _VY

NURTURE_TYPE = "유스 승격(육성)"
DEBUT_TYPE = "유스 외부 데뷔"
EXPIRE_SALE_TYPE = "유스 전용 정리 이적"
EXPIRE_LOAN_TYPE = "유스 전용 정리 임대"
EXPIRE_FREE_TYPE = "자유 이적"
LOG = _CL.LOG
FIELD = [c for c in _CL.CELLS if c != "GK"]
_SEV = {"C1": 0, "C2": 1, "C3": 2, "A": 3, "B": 4}


def on():
    return bool(getattr(_C, "VY_REFORM", False)) and _VY.stage1()


def _k():
    return int(getattr(_C, "VY_REFORM_SLOTS", 2))


def _grace():
    return int(getattr(_C, "VY_REFORM_GRACE", 2))


# ── 공통 조회 ─────────────────────────────────────────────────────────────
def _slot_counts(c, me_tid=None):
    """{팀: 소속 인원(+내 선수)} — 이적시장·인원 재조정과 같은 기준(임대 온 선수 포함, 임대 보낸 선수 제외).
    임대 이탈은 영구 편입이 아니다: 임대 나간 동안엔 전용 정원 선수가 그 자리를 메운 것처럼 세지만(시장이 대체 영입을
    하지 않는다), 임대 선수가 돌아오면 인원이 다시 늘어 전용 정원 점유로 돌아간다(유예가 끝났으면 expire가 정리)."""
    n = collections.Counter(t for (t,) in c.execute("SELECT team_id FROM ai_players WHERE team_id IS NOT NULL"))
    if me_tid:
        n[me_tid] += 1
    return n


def _nurtured(c, year):
    """[(선수, 모구단, 승격 연도, 지금 소속 여부)] — 육성 승격 기록 중 지금도 그 모구단 소속(또는 그 모구단에서 임대 중)인 선수.
    같은 선수 기록이 여럿이면 마지막 기록."""
    last = {}
    for pid, to, y in c.execute("SELECT player_id, to_team_id, year FROM ai_transfer_log WHERE transfer_type=? "
                                "ORDER BY id", (NURTURE_TYPE,)):
        last[pid] = (to, y)
    if not last:
        return []
    out = []
    ids = sorted(last)
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        ph = ",".join("?" * len(part))
        for pid, tid, lf in c.execute(f"SELECT id, team_id, on_loan_from_team_id FROM ai_players WHERE id IN ({ph})", part):
            to, y = last[pid]
            if tid == to or lf == to:
                out.append((pid, to, y, tid == to))
    return out


def allowance_map(c, year):
    """{모구단: 인원 재조정 상한에 더할 수} — 유예 중(승격 연도 ≥ year − 유예 + 1)인 육성 승격 선수 수, 최대 정원."""
    if not on():
        return {}
    g = collections.Counter(to for pid, to, y, here in _nurtured(c, year) if here and y >= year - _grace() + 1)
    return {t: min(_k(), n) for t, n in g.items()}


def fresh_ids(c, year):
    """이번 오프시즌 육성 승격·외부 데뷔로 들어온 선수 — 같은 오프시즌 조기 은퇴·보충 정리에서 올해 만든 신인처럼 보호한다."""
    if not on():
        return set()
    return {i for (i,) in c.execute("SELECT player_id FROM ai_transfer_log WHERE year=? AND transfer_type IN (?,?)",
                                    (year, NURTURE_TYPE, DEBUT_TYPE))}


def _me_team(c):
    try:
        r = c.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
        return r[0] if r and r[0] else None
    except Exception:
        return None


def _user_teams(c):
    try:
        import team_plan as _TP
        return set(_TP.user_control(c))
    except Exception:
        return set()


def _drop_newbie(c, LG, pid):
    """이번 오프시즌 은퇴 대체 신인 1명을 지운다(아직 다른 기록이 없는 선수) — 그 자리를 유스가 받는다."""
    for t, col in _VY._DEL_TABLES:
        try:
            c.execute(f"DELETE FROM {t} WHERE {col}=?", (pid,))
        except Exception:
            pass
    c.execute("DELETE FROM ai_players WHERE id=?", (pid,))
    LG.remove_player(pid)


def _removable(b, nbs, add=None):
    """b(장부 팀)에서 신인 nbs 중 빼도(add=(pid, pos, sub, ovr)이면 넣고) 주포 부족 L1·L2가 늘지 않는 신인 — 없으면 None.
    add가 있으면 같은 칸 신인부터 본다. 판정 뒤 장부는 원래대로 돌린다."""
    base = b.dm()
    cell = _CL.main_cell(add[1]) if add else None
    order = sorted(nbs, key=lambda x: (0 if (cell is not None and _CL.main_cell(x[1]) == cell) else 1, x[0]))
    for x in order:
        rec = b.players.get(x[0])
        if rec is None:
            continue
        b.remove(x[0])
        if add:
            b.add(add[0], add[1], add[2], add[3])
        nd = b.dm()
        if add:
            b.remove(add[0])
        b.add(x[0], rec[0], rec[1], rec[2])
        if nd[0] <= base[0] and nd[1] <= base[1]:
            return x
    return None


# ── 오프시즌: 은퇴 뒤·시장 전 ───────────────────────────────────────────
def pre_market(c, year):
    """A. 전용 정원 편입(은퇴 빈자리) → B. 육성 승격 → C. 외부 데뷔(은퇴 빈자리 → 기존 빈자리). 반환: 계측 dict."""
    if not on():
        return {}
    import ai_lifecycle as _AL
    import time as _time
    from database import is_roster_foreign
    _t0 = _time.perf_counter()
    LG = _CL.Ledger(c, year)
    _t1 = _time.perf_counter()
    me = _me_team(c)
    uc = _user_teams(c)
    vp = _VY.parent_map(c)
    vt_of = {}
    for vt, p in sorted(vp.items()):
        vt_of.setdefault(p, vt)
    aff = set(LG.aff)
    parents = sorted(p for p in vt_of if p in LG.books and p != me and p not in uc and not LG.meta[p]["youth"])
    smax = {t: _AL._squad_min_max(m["grade"], m["tier"])[1] for t, m in LG.meta.items()}
    nslot = _slot_counts(c, me)
    pot, cend = {}, {}
    for pid, po, ce in c.execute("SELECT id, potential_ovr, contract_end_year FROM ai_players"):
        pot[pid] = po or 0
        cend[pid] = ce or 0
    nur = _nurtured(c, year)
    held = collections.Counter(to for pid, to, y, _h in nur)                                  # 육성 승격 선수(모구단별)
    grace = collections.Counter(to for pid, to, y, _h in nur if y >= year - _grace() + 1)     # 유예 중
    # 이번 오프시즌 은퇴 대체 신인(필드) — 그 자리가 은퇴 빈자리
    NB = collections.defaultdict(list)
    nb_all = set()
    for pid, tid, pos, ovr, nat in c.execute(
            "SELECT id, team_id, position, ovr, nationality FROM ai_players WHERE creation_source='retire_repl' "
            "AND created_year=? ORDER BY id", (year,)).fetchall():
        nb_all.add(pid)
        if pos == "GK" or tid not in LG.books or LG.meta[tid]["youth"] or tid in aff:
            continue
        NB[tid].append((pid, pos, ovr or 0, nat or ""))
    res = collections.Counter()
    res["vacancies"] = sum(len(v) for v in NB.values())

    # ── A. 전용 정원 선수 편입: 은퇴 빈자리(C2) ──
    for S in parents:
        e = min(nslot[S] - smax[S], held[S])
        if e <= 0 or not NB.get(S):
            continue
        b = LG.books[S]
        while e > 0 and NB[S]:
            x = _removable(b, NB[S])
            if x is None:
                LOG["yr|integ_ret_left_for_D"] += 1
                break
            _drop_newbie(c, LG, x[0])
            NB[S].remove(x)
            nslot[S] -= 1
            e -= 1
            res["integ_ret"] += 1
            LOG["yr|integ_ret"] += 1

    # ── B. 육성 승격 ──
    pm = int(getattr(_C, "VY_REFORM_POT_MARGIN", 3))
    om = int(getattr(_C, "VY_REFORM_OVR_MARGIN", 9))
    lim = int(getattr(_C, "VY_REFORM_NURTURE_LIMIT", 1))
    done_n = collections.Counter(to for pid, to, y, _h in nur if y == year)
    promoted = set()
    for S in parents:
        if done_n[S] >= lim:
            continue
        b = LG.books[S]
        ids, cnt, amap, d = b.calc()
        rl = b.roles()
        md = {cc: lv for lv, amt, cc in b.m_deficits()}
        info = {}
        cands = []
        for p in sorted(LG.vpool.get(S, ())):
            pi = LG.pinfo.get(p)
            if pi is None or pi[6] or pi[1] == "GK":
                continue
            age = pi[7] or 0
            if not 19 <= age <= 23:
                continue
            cc = _CL.main_cell(pi[1])
            if cc not in FIELD:
                continue
            if cc not in info:
                sm = b.starter_min(cc)
                st = [i for i in ids if amap[i] == cc and rl[i] == "S" and i in LG.pinfo]
                tr = set()
                if any((LG.pinfo[i][7] or 0) >= 31 for i in st):
                    tr.add("A")
                if any(cend.get(i, 9999) - year <= 1 for i in st):
                    tr.add("B")
                if cc in md:
                    tr.add(f"C{md[cc]}")
                info[cc] = (sm, min([_SEV[x] for x in tr], default=9), tr)
            sm, sev, tr = info[cc]
            if sm is None or not tr or pot.get(p, 0) < sm - pm or pi[3] < sm - om:
                continue
            cands.append((sev, -pot.get(p, 0), -pi[3], p))
        if not cands:
            continue
        res["nurture_cand_parents"] += 1
        res["nurture_cands"] += len(cands)
        sev, _np, _no, p = min(cands)
        pi = LG.pinfo[p]
        add = (p, pi[1], pi[2], pi[3])
        route = None
        x = _removable(b, NB[S], add) if NB.get(S) else None
        if x is not None:
            route = "vac"
        elif nslot[S] < smax[S]:
            route = "free"
        elif nslot[S] + 1 - smax[S] <= min(_k(), grace[S] + 1):
            route = "ded"
        if NB.get(S) and x is None:
            LOG["yr|nurture_vac_left_for_D"] += 1
        LOG[f"yr|nurture|{route or 'blocked'}"] += 1
        res[f"nurture|{route or 'blocked'}"] += 1
        if route is None:
            continue
        if route == "vac":
            _drop_newbie(c, LG, x[0])
            NB[S].remove(x)
        else:
            nslot[S] += 1
        vt = vt_of[S]
        _AL._ledger_db_transfer(c, LG, p, vt, S, year, NURTURE_TYPE, fee_zero=True, promo_floor=True)
        LG.vpool[S].discard(p)
        LG.move(p, vt, S)
        promoted.add(p)
        grace[S] += 1
        held[S] += 1
        LOG[f"yr|nurture_cell|{_CL.main_cell(pi[1])}"] += 1

    # ── C. 외부 데뷔 대상(모구단 계획 밖) ──
    reserve = set()
    targets = []
    for S in sorted(vt_of):
        if S not in LG.books:
            continue
        b = LG.books[S]
        pool = [p for p in sorted(LG.vpool.get(S, ())) if p not in promoted and LG.pinfo.get(p) and not LG.pinfo[p][6]]
        for lv, amt, cc in b.m_deficits():
            if lv > 2:
                continue
            lst = sorted((p for p in pool if _CL.main_cell(LG.pinfo[p][1]) == cc and (LG.pinfo[p][7] or 0) <= _VY.MAX_AGE),
                         key=lambda p: (-LG.pinfo[p][3], p))
            reserve.update(lst[:amt])
        smc = {}
        for p in pool:
            pi = LG.pinfo[p]
            age = pi[7] or 0
            if age < 19 or age > _VY.MAX_AGE + 1 or p in reserve:
                continue
            cc = _CL.main_cell(pi[1]) or "GK"
            if age <= _VY.MAX_AGE and cc != "GK":
                if cc not in smc:
                    smc[cc] = b.starter_min(cc)
                sm = smc[cc]
                if sm is not None and pot.get(p, 0) >= sm - pm:
                    continue   # 모구단 계획 안(육성 후보 잠재력) — 풀에 남는다
            targets.append(p)
    res["ext_targets"] = len(targets)
    res["ext_targets_24"] = sum(1 for p in targets if (LG.pinfo[p][7] or 0) > _VY.MAX_AGE)
    key = lambda p: (-(LG.pinfo[p][7] or 0), -pot.get(p, 0), -LG.pinfo[p][3], p)
    targets.sort(key=key)
    par_of = {}
    for S in vt_of:
        for p in LG.vpool.get(S, ()):
            par_of[p] = S

    # 받을 팀 지표: 이번 신인을 뺀 명단의 OVR 11번째, 실제 경기 선발 11명 최저 OVR
    recv = [t for t in sorted(LG.books) if not LG.meta[t]["youth"] and t not in aff and t != me and t not in uc]
    recv_set = set(recv)
    s11 = {}
    for t in recv:
        o = sorted((r[2] for p, r in LG.books[t].players.items() if p not in nb_all), reverse=True)
        s11[t] = o[min(10, len(o) - 1)] if o else 0
    _t2 = _time.perf_counter()
    xg = _xi_min_game(c, recv_set, nb_all)
    _t3 = _time.perf_counter()
    cap = int(getattr(_C, "VY_REFORM_DEBUT_CAP", 3))
    cont_of = dict(c.execute("SELECT name, continent FROM countries").fetchall())
    curf = {t: _AL_foreign(LG, t) for t in recv}
    used = set()

    def _move_out(p, T):
        S = par_of[p]
        vt = vt_of[S]
        _AL._ledger_db_transfer(c, LG, p, vt, T, year, DEBUT_TYPE, fee_zero=True, promo_floor=True)
        LG.vpool[S].discard(p)
        LG.move(p, vt, T)
        used.add(p)

    # C-1. 은퇴 빈자리: 같은 칸 주포 신인 대신. 후보 색인 = (범위, 키, 칸) → OVR → 고르는 순서대로 줄 세운 선수
    #      (범위: 국적국 'n' / 대륙 'c' / 세계 'w'). 한 자리마다 OVR 구간의 줄 앞머리 중 고르는 순서가 가장 빠른 선수.
    BK = collections.defaultdict(lambda: collections.defaultdict(collections.deque))
    for p in targets:
        pi = LG.pinfo[p]
        cc = _CL.main_cell(pi[1]) or "GK"
        nat = pi[4] or ""
        for sc, kv in (("n", nat), ("c", cont_of.get(nat)), ("w", None)):
            BK[(sc, kv, cc)][pi[3]].append(p)
    slots = []
    for T, lst in NB.items():
        if T not in recv_set:
            continue
        for x in lst:
            slots.append((-s11[T], T, x[0], x))
    slots.sort()
    for _k_, T, _xid, x in slots:
        mt = LG.meta[T]
        cc = _CL.main_cell(x[1])
        nb_f = is_roster_foreign(x[3], mt["cname"])
        lo = max(s11[T] - 8, x[2] - 3)
        hi = xg.get(T, 0) + cap
        best = None
        for rk, (sc, kv) in enumerate((("n", mt["cname"]), ("c", mt["cont"]), ("w", None))):
            bk = BK.get((sc, kv, cc))
            if not bk or lo > hi:
                continue
            for o in range(int(lo), int(hi) + 1):
                q = bk.get(o)
                if not q:
                    continue
                while q and q[0] in used:
                    q.popleft()
                for p in q:
                    if p in used:
                        continue
                    if par_of[p] == T or _AL._dst_ceiling_excluded(o, mt["ceil"]):
                        continue
                    nat = LG.pinfo[p][4] or ""
                    yf = is_roster_foreign(nat, mt["cname"])
                    if yf and not nb_f and (mt["qhi"] is None or curf[T] + 1 > mt["qhi"]):
                        continue
                    if best is None or key(p) < key(best[1]):
                        best = (rk, p, yf and not nb_f)
                    break
            if best:
                break
        if best is None:
            continue
        rk, p, addf = best
        _drop_newbie(c, LG, x[0])
        NB[T].remove(x)
        _move_out(p, T)
        if addf:
            curf[T] += 1
        res[f"ext_vac|{('국적국', '대륙', '세계')[rk]}"] += 1
        LOG["yr|ext_vac"] += 1
        LOG[f"yr|ext_vac_age|{LG.pinfo[p][7]}"] += 1

    _t4 = _time.perf_counter()
    # C-2. 기존 빈자리: 그 칸 주포 부족 팀 먼저(D1) → 조건만(D0)
    grp = {t: collections.Counter(_AL._POS_GROUP.get(r[0], "FW") for r in LG.books[t].players.values()) for t in recv}
    mdl = {t: {cc: amt for lv, amt, cc in LG.books[t].m_deficits()} for t in recv}
    room = [t for t in recv if nslot[t] < smax[t]]
    IX = {}
    tkeys = collections.defaultdict(list)
    for t in room:
        mt = LG.meta[t]
        for kd, kv in (("cn", mt["cname"]), ("ct", mt["cont"]), ("all", None)):
            for cl in [None] + [cc for cc in FIELD if mdl[t].get(cc, 0) > 0]:
                IX.setdefault((kd, kv, cl), []).append((s11[t], t))
                tkeys[t].append((kd, kv, cl))
    for v in IX.values():
        v.sort()

    def _drop_ix(t, cell=None):
        for kk in tkeys[t]:
            if cell is not None and kk[2] != cell:
                continue
            lst = IX[kk]
            i = bisect.bisect_left(lst, (s11[t], t))
            if i < len(lst) and lst[i] == (s11[t], t):
                lst.pop(i)

    gcap = _C.YOUTH_POS_GROUP_CAP
    for demand in (True, False):
        for p in targets:
            if p in used:
                continue
            pi = LG.pinfo[p]
            cc = _CL.main_cell(pi[1]) or "GK"
            if demand and cc == "GK":
                continue
            S = par_of[p]
            ms = LG.meta[S]
            nat = pi[4] or ""
            o = pi[3]
            gk = _AL._POS_GROUP.get(pi[1], "FW")
            cl = cc if demand else None
            stages = [(("cn", nat, cl), (), None)]
            if ms["cname"] != nat:
                stages.append((("cn", ms["cname"], cl), (), None))
            stages.append((("ct", ms["cont"], cl), {nat, ms["cname"]}, None))
            stages.append((("all", None, cl), {nat, ms["cname"]}, ms["cont"]))
            hit = None
            for kk, ecn, ect in stages:
                ix = IX.get(kk, [])
                prev = -1
                for mg in (0, 2, 8):
                    hi = bisect.bisect_right(ix, (o + mg, 10 ** 9)) - 1
                    lo = prev + 1
                    prev = max(prev, hi)
                    i = hi
                    while i >= lo:
                        if o > ix[i][0] + cap:
                            break   # 선발 11명 최저 ≤ OVR 11번째 — 더 아래 팀은 모두 상한 초과
                        t = ix[i][1]
                        i -= 1
                        if t == S:
                            continue
                        mt = LG.meta[t]
                        if mt["cname"] in ecn or (ect is not None and mt["cont"] == ect):
                            continue
                        if grp[t][gk] >= gcap.get(gk, 99):
                            continue
                        yf = is_roster_foreign(nat, mt["cname"])
                        if yf and (mt["qhi"] is None or curf[t] + 1 > mt["qhi"]):
                            continue
                        if o > xg.get(t, 0) + cap or _AL._dst_ceiling_excluded(o, mt["ceil"]):
                            continue
                        hit = (t, yf, mg)
                        break
                    if hit:
                        break
                if hit:
                    break
            if hit is None:
                continue
            t, yf, mg = hit
            _move_out(p, t)
            nslot[t] += 1
            grp[t][gk] += 1
            curf[t] += yf
            if mdl[t].get(cc, 0) > 0:
                mdl[t][cc] -= 1
                if mdl[t][cc] == 0:
                    _drop_ix(t, cc)
            if nslot[t] >= smax[t]:
                _drop_ix(t)
            res[f"ext_free|{'D1' if demand else 'D0'}"] += 1
            LOG[f"yr|ext_free|{'D1' if demand else 'D0'}"] += 1
            LOG[f"yr|ext_free_age|{pi[7]}"] += 1
    res["ext_total"] = len(used)
    _t5 = _time.perf_counter()
    res["t"] = f"ledger {_t1 - _t0:.1f} / 육성 {_t2 - _t1:.1f} / 선발 {_t3 - _t2:.1f} / 은퇴빈자리 {_t4 - _t3:.1f} / 기존빈자리 {_t5 - _t4:.1f}"
    res["newbies_left"] = sum(len(v) for v in NB.values())
    LOG["yr|pre_market_runs"] += 1
    return dict(res)


def _AL_foreign(LG, t):
    from database import is_roster_foreign
    cn = LG.meta[t]["cname"]
    return sum(1 for p in LG.books[t].players if p in LG.pinfo and is_roster_foreign(LG.pinfo[p][4], cn))


def _xi_min_game(c, teams, skip):
    """{팀: 실제 경기 AI 선발(team_plan.optimal_lineup — 부포 붙임, teams.formation) 11명 최저 OVR} — skip 선수는 뺀다."""
    import team_plan as _TP
    import sub_positions as _SP
    form = dict(c.execute("SELECT id, formation FROM teams").fetchall())
    rr = collections.defaultdict(list)
    for pid, tid, pos, ovr, foot, sr in c.execute(
            "SELECT id, team_id, position, ovr, foot, sub_role FROM ai_players WHERE team_id IS NOT NULL ORDER BY id"):
        if tid in teams and pid not in skip:
            rr[tid].append({"id": pid, "position": pos, "ovr": ovr, "foot": foot, "sub_role": sr})
    out = {}
    for t in teams:
        ro = rr.get(t, [])
        _SP.attach(ro)
        xi = _TP.optimal_lineup(ro, form.get(t))
        o = [p["ovr"] or 0 for p in xi if p]
        out[t] = min(o) if o else 0
    return out


# ── 오프시즌: 시장·보충 뒤, 인원 재조정 직전 ──────────────────────────────
def expire(c, year):
    """유예가 끝났는데(승격 연도 ≤ year − 유예) 모구단 인원이 일반 정원 + 유예 중 전용 정원을 넘으면, 넘는 만큼
    오래된 승격 선수부터 이적(여유폭 0 → 2) → 임대(5) → 자유 이적(8) — 산하팀 정리와 같은 받을 팀 조건·지역 순서
    (지금 나라 → 본국 → 같은 대륙 → 세계). 모두 실패하면 그대로 둔다(다음 오프시즌에 다시 시도)."""
    if not on():
        return {}
    import ai_lifecycle as _AL
    from database import is_roster_foreign, get_foreign_quota_range
    from constants import get_country_league_grade, get_ovr_range, AI_LOAN_DURATION_YEARS
    from economy import estimate_transfer_fee
    nur = _nurtured(c, year)
    if not nur:
        return {}
    me = _me_team(c)
    g = _grace()
    old = collections.defaultdict(list)
    grace = collections.Counter()
    for pid, to, y, _h in nur:
        if y >= year - g + 1:
            grace[to] += 1
        else:
            old[to].append((y, pid))
    if not old:
        return {}
    nslot = _slot_counts(c, me)
    rows = c.execute("""SELECT t.id, t.current_tier, cn.name, cn.continent, t.name
                        FROM teams t JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id""").fetchall()
    rows = _AL._drop_mil_teams(c, [{"tid": r[0], "tier": r[1], "cname": r[2], "cont": r[3], "tname": r[4]} for r in rows], "tid")
    aff = set(_AL._youth_aff(c))
    meta = {}
    gc = {}
    for r in rows:
        cn = r["cname"]
        if cn not in gc:
            gc[cn] = get_country_league_grade(cn)
        gr = gc[cn]
        tier = r["tier"] or 1
        rng = get_ovr_range(gr, tier, cn)
        meta[r["tid"]] = dict(cname=cn, cont=r["cont"], grade=gr, tier=tier, tname=r["tname"],
                              ceil=(rng[1] if rng else 43), qhi=get_foreign_quota_range(cn, r["cont"], tier=tier)[1],
                              smax=_AL._squad_min_max(gr, tier)[1])
    res = collections.Counter()
    todo = []
    for S in sorted(old):
        if S not in meta:
            continue
        over = nslot[S] - meta[S]["smax"] - min(_k(), grace[S])
        if over <= 0:
            continue
        for y, pid in sorted(old[S])[:over]:
            todo.append((S, pid))
    res["expired_over"] = len(todo)
    if not todo:
        return dict(res)
    by_team = collections.defaultdict(list)
    for r in c.execute("SELECT id, team_id, position, nationality, ovr, salary, on_loan_from_team_id, name, age, "
                       "contract_end_year FROM ai_players WHERE team_id IS NOT NULL").fetchall():
        if r[1] in meta:
            by_team[r[1]].append(r)
    size = {t: len(v) for t, v in by_team.items()}
    if me in size:
        size[me] += 1
    curf = {t: sum(1 for r in v if is_roster_foreign(r[3], meta[t]["cname"])) for t, v in by_team.items()}
    grp = {t: collections.Counter(_AL._POS_GROUP.get(r[2], "FW") for r in v) for t, v in by_team.items()}
    s11 = {}
    for t, v in by_team.items():
        o = sorted(((r[4] or 0) for r in v), reverse=True)
        s11[t] = o[min(10, len(o) - 1)] if o else 0
    idx_all = sorted((s11[t], t) for t in by_team if t not in aff and t != me)
    idx_cn, idx_ct = collections.defaultdict(list), collections.defaultdict(list)
    for s_, t in idx_all:
        idx_cn[meta[t]["cname"]].append((s_, t))
        idx_ct[meta[t]["cont"]].append((s_, t))
    gcap = _C.YOUTH_POS_GROUP_CAP
    rowof = {r[0]: r for v in by_team.values() for r in v}
    cs = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    season = cs[0] if cs else 0

    def ok(p, t, margin):
        m = meta.get(t)
        if m is None or size.get(t, 0) >= m["smax"]:
            return None
        gk = _AL._POS_GROUP.get(p[2], "FW")
        if grp.get(t, {}).get(gk, 0) >= gcap.get(gk, 99):
            return None
        ns = is_roster_foreign(p[3], m["cname"])
        if ns and (m["qhi"] is None or curf.get(t, 0) + 1 > m["qhi"]):
            return None
        o = p[4] or 0
        if o < s11.get(t, 0) - margin or _AL._dst_ceiling_excluded(o, m["ceil"]):
            return None
        return ns

    def find(p, seller, margin, ix, excl_cn=(), excl_ct=None):
        keys = [k for k, _t in ix]
        i = bisect.bisect_right(keys, (p[4] or 0) + margin) - 1
        steps = 0
        while i >= 0 and steps < 3000:
            _s, t = ix[i]
            i -= 1
            if t == seller:
                continue
            m = meta[t]
            if m["cname"] in excl_cn or (excl_ct is not None and m["cont"] == excl_ct):
                continue
            steps += 1
            ns = ok(p, t, margin)
            if ns is not None:
                return t, ns
        return None

    FIT = _C.YOUTH_CLEANUP_FIT_MARGIN
    LOANM = _C.YOUTH_CLEANUP_LOAN_MARGIN
    FREE = _C.YOUTH_CLEANUP_FREE_MARGIN
    for S, pid in todo:
        p = rowof.get(pid)
        if p is None or p[1] != S:
            res["expired_not_here"] += 1   # 임대 중 — 돌아온 뒤 다시
            continue
        nat = p[3] or ""
        cn, ct = meta[S]["cname"], meta[S]["cont"]
        stages = [("현지", idx_cn.get(cn, []), (), None)]
        if nat and nat != cn and nat in idx_cn:
            stages.append(("본국", idx_cn[nat], (), None))
        stages.append(("대륙", idx_ct.get(ct, []), {cn, nat}, None))
        stages.append(("세계", idx_all, {cn, nat}, ct))
        hit = kind = None
        for sn, ix, ecn, ect in stages:
            h = find(p, S, 0, ix, ecn, ect) or find(p, S, FIT, ix, ecn, ect)
            if h:
                hit, kind = h, "sale"
                break
            h = find(p, S, LOANM, ix, ecn, ect)
            if h:
                hit, kind = h, "loan"
                break
            h = find(p, S, FREE, ix, ecn, ect)
            if h:
                hit, kind = h, "free"
                break
        if hit is None:
            res["expired_held"] += 1
            LOG["yr|expired_held"] += 1
            continue
        bt, ns = hit
        bm = meta[bt]
        size[bt] = size.get(bt, 0) + 1
        size[S] -= 1
        if ns:
            curf[bt] = curf.get(bt, 0) + 1
        if is_roster_foreign(nat, cn):
            curf[S] = max(0, curf.get(S, 0) - 1)
        gk = _AL._POS_GROUP.get(p[2], "FW")
        grp.setdefault(bt, collections.Counter())[gk] += 1
        grp[S][gk] -= 1
        sal = _AL._calc_ai_salary(bm["grade"], bm["tier"], p[4], bm["cname"], bm["tname"], bt, year)
        if kind == "loan":
            ret = year + AI_LOAN_DURATION_YEARS[0]
            c.execute("UPDATE ai_players SET team_id=?, on_loan_from_team_id=?, loan_return_year=?, last_transfer_year=? "
                      "WHERE id=?", (bt, S, ret, year, pid))
            log = (season, year, pid, p[7] or "", p[2], p[8] or 0, p[4], S, bt, 0, 0, 0.0, 0.0, EXPIRE_LOAN_TYPE, 0, "",
                   0, 1, ret, p[5] or 0, p[9] or 0)
        else:
            if kind == "sale":
                cend = max(p[9] or 0, year + 1)
                fee = estimate_transfer_fee(bm["grade"], bm["tier"], p[4], country=bm["cname"], position=p[2],
                                            year=year, player_id=pid) or 0
                tt = EXPIRE_SALE_TYPE
            else:
                cend, fee, tt = year + 2, 0, EXPIRE_FREE_TYPE
            c.execute("UPDATE ai_players SET team_id=?, salary=?, contract_end_year=?, last_transfer_year=? WHERE id=?",
                      (bt, sal, cend, year, pid))
            log = (season, year, pid, p[7] or "", p[2], p[8] or 0, p[4], S, bt, 0, 0, 0.0, 0.0, tt, 0, "", fee, 0, 0,
                   sal, cend)
        c.execute("""INSERT INTO ai_transfer_log(
                    season, year, player_id, player_name, player_position, player_age, player_ovr,
                    from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                    from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                    fee, is_loan, loan_return_year, salary, contract_end_year)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", log)
        res[f"expired|{kind}"] += 1
        LOG[f"yr|expired|{kind}"] += 1
    return dict(res)