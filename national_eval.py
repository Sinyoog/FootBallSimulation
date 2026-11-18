"""[2026-11 국가대표 감독 평가 v2 — 신민용 확정 '국가대표 목표 산정 명세·감독 평가 명세'] 예선 단계 목표 + 연말 종합 평가.

  · 예선 목표: 예선 생성 직후(참가국·조 확정, 경기 0개) 예선 참가국 안 전력 순위(OVR 상위 11명 평균 — 성인은
    world_squad_percentiles, U-23은 예선 명단 전력 oly_squad_meta)와 그 예선의 실제 구조(본선 티켓·직행·PO·2차)로
    굳힌다(national_qual_objectives, 멱등). 도달 단계: 3 본선 진출(경로 무관) / 2 PO 탈락 / 1 2차 탈락 / 0 1차 탈락.
    목표 단계: 3 본선 진출 / 2 PO 진출 / 1 2차 예선 진출 / 0 경쟁력 확보(다음 단계 진출 또는 조 안 전력 순위 이상).
  · 연말 종합 평가(evaluate_year): 그 해 끝난 주요 대회(월드컵 100, 네이션스 본선·유로 90)의 결과로 감독마다 한 번
    경질(계약 중) 또는 재계약(만료) 판정. 대회 주기마다 결과 하나(본선에 갔으면 본선, 못 갔으면 예선). 주 평가 하나 +
    감면 하나(다른 주요 대회 달성 우선, 없으면 지역컵 최고 성적). 지역컵·친선전은 독립 판정 없음.
  · 네이션스 예선(짝수 해)은 이듬해 네이션스 본선 해 연말 평가에서 함께 쓴다(eval_year로 표시).
결정성: 전역 random을 쓰지 않는다 — (world_salt, 나라, 연도)에서 파생한 random.Random만.
"""
import random
import zlib

import constants as C

V2_LOG = []          # [측정 관찰] 연말 평가 한 건당 한 줄 — 게임 로직은 읽지 않는다.
EURO_NAME = getattr(C, "EURO_NAME", "유로(EURO)")
QUAL_KINDS = ("wc_qual", "cont_qual", "oly_qual")


def enabled():
    return bool(getattr(C, "NAT_EVAL_V2", False))


def migrations():
    return [
        """CREATE TABLE IF NOT EXISTS national_qual_objectives(
            tournament_id INTEGER, year INTEGER, kind TEXT, scope TEXT DEFAULT 'senior', continent TEXT DEFAULT '',
            country TEXT, country_id INTEGER DEFAULT 0, squad_ovr REAL DEFAULT 0, field_rank INTEGER DEFAULT 0,
            n INTEGER DEFAULT 0, quota INTEGER DEFAULT 0, direct INTEGER DEFAULT 0, po_n INTEGER DEFAULT 0,
            g2_n INTEGER DEFAULT 0, goal_level INTEGER DEFAULT 0, grp TEXT DEFAULT '', group_seed INTEGER DEFAULT 0,
            manager_id INTEGER DEFAULT 0, result_level INTEGER DEFAULT -1, path TEXT DEFAULT '',
            group_finish INTEGER DEFAULT 0, eval_year INTEGER DEFAULT 0,
            PRIMARY KEY(tournament_id, country))""",
    ]


GOAL_NAME = {3: "본선 진출", 2: "PO 진출", 1: "2차 예선 진출", 0: "경쟁력 확보"}
LEVEL_NAME = {3: "본선 진출", 2: "PO 탈락", 1: "2차 예선 탈락", 0: "1차 예선 탈락"}


def qual_structure(cfg):
    """(본선 티켓, 직행, PO 참가, 2차 진출) — WC_QUAL_*(direct_top·stage2)와 EURO_QUAL(direct·wildcard) 두 형식."""
    cfg = cfg or {}
    if "stage2" in cfg:
        s2 = cfg["stage2"]
        g2 = int(cfg.get("advance_per_group1", 2)) * int(cfg.get("n_groups", 0))
        direct = int(s2.get("direct_top", 1)) * int(s2.get("n_groups", 0)) + int(s2.get("wildcard_count", 0))
        po = int(s2.get("po_teams", 0)); pw = int(s2.get("po_winners", 0))
    else:
        g2 = 0
        if "direct" in cfg:
            direct = int(cfg.get("direct", 0)) + int(cfg.get("wildcard", 0))
        else:
            direct = int(cfg.get("direct_top", 1)) * int(cfg.get("n_groups", 0)) + int(cfg.get("wildcard_count", 0))
        po = int(cfg.get("po_teams", 0)); pw = int(cfg.get("po_winners", 0))
    quota = int(cfg.get("quota", direct + pw))
    return quota, direct, po, g2


def goal_level(rank, quota, direct, po_n, g2_n):
    """예선 참가국 안 전력 순위 → 목표 단계. 그 예선에 실제로 있는 단계만."""
    if rank <= quota:
        return 3
    if po_n and rank <= direct + po_n:
        return 2
    if g2_n and rank <= g2_n:
        return 1
    return 0


def _qual_cfg_for(t):
    kind, cont, year = t["kind"], t["continent"] or "", int(t["year"])
    if kind == "oly_qual":
        return C.WC_QUAL_32.get(cont, {})
    if kind == "wc_qual":
        return C.WC_QUAL_BY_TIER[C.wc_tier(year)].get(cont, {})
    if kind == "cont_qual":
        if cont == "북미":          # 골드컵 예선 = 지역컵(보조 대회) — 주요 대회 예선이 아니다
            return None
        return C.EURO_QUAL.get(cont, {})
    return None


def fix_qual_objectives(conn, tournament_id):
    """[예선 생성 직후] 예선 목표를 굳힌다. 이미 있으면 아무것도 안 한다(멱등). 반환: 새로 쓴 행 수."""
    t = conn.execute("SELECT id, year, kind, name, continent FROM intl_tournaments WHERE id=?",
                     (tournament_id,)).fetchone()
    if not t or t["kind"] not in QUAL_KINDS:
        return 0
    if conn.execute("SELECT 1 FROM national_qual_objectives WHERE tournament_id=? LIMIT 1",
                    (tournament_id,)).fetchone():
        return 0
    cfg = _qual_cfg_for(t)
    if not cfg:
        return 0
    quota, direct, po_n, g2_n = qual_structure(cfg)
    grp = {}
    for r in conn.execute("SELECT home, away, grp FROM intl_matches WHERE tournament_id=? AND stage='qual_group'",
                          (tournament_id,)):
        grp.setdefault(r[0], r[2]); grp.setdefault(r[1], r[2])
    parts = sorted(grp)
    if not parts:
        parts = sorted(r[0] for r in conn.execute("SELECT country FROM intl_entries WHERE tournament_id=?",
                                                  (tournament_id,)))
    if not parts:
        return 0
    scope = "u23" if t["kind"] == "oly_qual" else "senior"
    if scope == "u23":
        ovr = {r[0]: float(r[1] or 0.0) for r in conn.execute(
            "SELECT country, ovr FROM oly_squad_meta WHERE tournament_id=?", (tournament_id,))}
        wpct = {}
    else:
        from national_manager import world_squad_percentiles
        pct = world_squad_percentiles(conn)
        ovr = {cn: v[0] for cn, v in pct.items()}
        wpct = {cn: v[1] for cn, v in pct.items()}
    order = sorted(parts, key=lambda cn: (-ovr.get(cn, 0.0), wpct.get(cn, 1.0), cn))
    rank = {cn: i for i, cn in enumerate(order, 1)}
    seed = {}
    for g in set(grp.values()):
        mem = sorted((cn for cn in parts if grp.get(cn) == g), key=lambda cn: rank[cn])
        for i, cn in enumerate(mem, 1):
            seed[cn] = i
    cids = {r[1]: int(r[0]) for r in conn.execute("SELECT id, name FROM countries")}
    if scope == "u23":
        try:
            from competition.oly_manager import manager_for_country
            mgr = {cn: int((manager_for_country(cn, conn) or (0, None))[0] or 0) for cn in parts}
        except Exception:
            mgr = {}
    else:
        cur = {r[0]: r[1] for r in conn.execute(
            "SELECT country_id, manager_id FROM national_team_managers WHERE end_year IS NULL")}
        mgr = {cn: cur.get(cids.get(cn, 0), 0) for cn in parts}
    rows = []
    for cn in parts:
        rows.append((tournament_id, int(t["year"]), t["kind"], scope, t["continent"] or "", cn, cids.get(cn, 0),
                     ovr.get(cn, 0.0), rank[cn], len(parts), quota, direct, po_n, g2_n,
                     goal_level(rank[cn], quota, direct, po_n, g2_n), grp.get(cn, ""), seed.get(cn, 0),
                     int(mgr.get(cn) or 0)))
    conn.executemany(
        """INSERT OR IGNORE INTO national_qual_objectives(tournament_id, year, kind, scope, continent, country,
               country_id, squad_ovr, field_rank, n, quota, direct, po_n, g2_n, goal_level, grp, group_seed, manager_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    return len(rows)


def main_for_qual(conn, q):
    """예선 → 짝 본선 대회 id(없으면 None). 월드컵 = 같은 해 world, 유로 예선 = 같은 해 유로, 네이션스 예선 =
    이듬해 같은 연맹 네이션스 본선, 올림픽 예선 = 같은 해 olympic."""
    k, y, cont, nm = q["kind"], int(q["year"]), q["continent"] or "", q["name"] or ""
    if k == "wc_qual":
        r = conn.execute("SELECT id FROM intl_tournaments WHERE kind='world' AND year=?", (y,)).fetchone()
    elif k == "oly_qual":
        r = conn.execute("SELECT id FROM intl_tournaments WHERE kind='olympic' AND year=?", (y,)).fetchone()
    elif k == "cont_qual" and EURO_NAME in nm:
        r = conn.execute("SELECT id FROM intl_tournaments WHERE kind='continent' AND name=? AND year=?",
                         (EURO_NAME, y)).fetchone()
    elif k == "cont_qual":
        r = conn.execute("SELECT id FROM intl_tournaments WHERE kind='continent' AND name<>? AND year=? AND continent=?",
                         (EURO_NAME, y + 1, cont)).fetchone()
    else:
        r = None
    return int(r[0]) if r else None


def settle_qual(conn, qid, main_tid):
    """예선 결과(도달 단계·경로·조 최종 순위)를 기록하고 {나라: 행 dict}를 돌려준다."""
    rows = {r["country"]: dict(r) for r in conn.execute(
        "SELECT * FROM national_qual_objectives WHERE tournament_id=?", (qid,))}
    if not rows:
        return {}
    ents = {r[0] for r in conn.execute("SELECT country FROM intl_entries WHERE tournament_id=?", (main_tid,))} \
        if main_tid else set()
    st, tab = {}, {}
    for m in conn.execute("SELECT stage, grp, home, away, home_score, away_score FROM intl_matches "
                          "WHERE tournament_id=? AND home_score>=0", (qid,)):
        for n in (m["home"], m["away"]):
            st.setdefault(n, set()).add(m["stage"])
        if m["stage"] in ("qual_group", "qual_group2"):
            key = (m["stage"], m["grp"])
            for a, ga, gb in ((m["home"], m["home_score"], m["away_score"]), (m["away"], m["away_score"], m["home_score"])):
                r = tab.setdefault(key, {}).setdefault(a, [0, 0, 0])
                r[0] += 3 if ga > gb else (1 if ga == gb else 0); r[1] += ga - gb; r[2] += ga
    fin = {}
    for key, teams in tab.items():
        if key[0] != "qual_group":
            continue
        order = sorted(teams, key=lambda x: (-teams[x][0], -teams[x][1], -teams[x][2], x))
        for i, n in enumerate(order, 1):
            fin[n] = i
    upd = []
    for cn, r in rows.items():
        s = st.get(cn, set())
        q = cn in ents
        lvl = 3 if q else 2 if "qual_po" in s else 1 if "qual_group2" in s else 0
        path = ("PO" if "qual_po" in s else "직행") if q else ""
        r.update(result_level=lvl, path=path, group_finish=fin.get(cn, 0))
        upd.append((lvl, path, fin.get(cn, 0), qid, cn))
    conn.executemany("UPDATE national_qual_objectives SET result_level=?, path=?, group_finish=? "
                     "WHERE tournament_id=? AND country=?", upd)
    return rows


def qual_outcome(r):
    """예선 행 → (달성?, 미달 단계, 경쟁력 목표 미달?, 조 편차(+ = 조 전력 순위보다 못함))."""
    g, lv = int(r["goal_level"]), int(r["result_level"])
    dev = (int(r["group_finish"] or 0) - int(r["group_seed"] or 0)) if r.get("group_finish") else 0
    if g == 0:
        met = lv >= 1 or (r.get("group_finish") and r["group_finish"] <= r["group_seed"])
        return bool(met), (0 if met else 1), (not met), dev
    met = lv >= g
    return met, max(0, g - lv), False, dev


def case_prob(components, region_relief, rep, tenure):
    """연말 종합 판정 확률(경질 압력). components: [{w, src('본선'/'예선'), met, steps, compete_miss, dev, goal}].
    반환 (p, 사례 설명, 주 평가 dict 또는 None, 감면 종류)."""
    met_p = float(C.NAT_V2_MET_P)
    missed = [c for c in components if not c["met"]]
    relief = ""
    # [2026-11-18 신민용 확정] 그해 주요 대회(월드컵·대륙컵) 우승 감독은 그해 경질하지 않는다 — 같은 해 다른 주요
    # 대회 목표를 못 채웠어도(constants.NAT_V2_TITLE_NO_SACK 주석). 다음 해부터는 평소처럼 평가.
    if bool(getattr(C, "NAT_V2_TITLE_NO_SACK", False)) and any(c.get("won") for c in components):
        return 0.0, "주요 대회 우승", None, ""
    if not missed:
        p, case, prim = met_p, "주요 대회 목표 달성", None
        # [2026-11-18 신민용 확정] 초과 달성은 그해 경질 확률을 줄인다(constants.NAT_V2_MET_P_EXCEED 주석).
        _ex = max((int(c.get("exceed", 0) or 0) for c in components), default=0)
        _ex_tbl = getattr(C, "NAT_V2_MET_P_EXCEED", None)
        if _ex_tbl is not None:
            p = met_p * float(_ex_tbl.get(_ex, 0.0))
            if _ex > 0:
                case = f"주요 대회 목표 {_ex}단계 초과"
    else:
        prim = sorted(missed, key=lambda c: (-c["w"], -c["steps"], -c["dev"]))[0]
        s = float(prim["steps"])
        slope = C.NAT_V2_FINALS_SLOPE if prim["src"] == "본선" else C.NAT_V2_QUAL_SLOPE
        full = float(C.NAT_V2_K) * (float(C.NAT_V2_BASE) + slope * s)
        ramp = 0.5 if s <= 1 else 1.0
        if prim.get("compete_miss"):
            ramp *= float(C.NAT_V2_COMPETE_MISS_MULT)   # 경쟁력 목표국 미달 — 미달로 추가되는 위험만 절반
        p = met_p + ramp * (full - met_p)
        case = ("예선 경쟁력 미달" if prim.get("compete_miss") else
                f"{prim['src']} 미달 {int(s)}단계")
        others = [c for c in components if c is not prim and c["met"]]
        if others:
            p *= float(C.NAT_V2_MAJOR_RELIEF); relief = "주요 대회 달성"
        elif region_relief is not None and region_relief < 1.0:
            rv = region_relief
            if prim["src"] == "예선" and prim.get("goal") == 3 and s >= 3:
                rv = max(rv, float(C.NAT_V2_SEVERE_REGION_FLOOR))
            p *= rv; relief = "지역컵"
        elif prim["src"] == "예선" and prim.get("has_group"):
            if prim["dev"] <= 0:
                p *= float(C.NAT_V2_GROUP_GOOD); case += " · 조 전력 대비 양호"
            elif prim["dev"] >= 2:
                p *= float(C.NAT_V2_GROUP_BAD); case += " · 조 전력 대비 부진"
    rep_v = max(0.0, min(100.0, float(rep or 0.0)))
    p *= (1.0 - C.MANAGER_NAT_SACK_REP_RELIEF * rep_v / 100.0)
    lo = C.MANAGER_NAT_SACK_MIN if missed else 0.0
    p = max(lo, min(C.MANAGER_NAT_SACK_MAX, p))
    if int(tenure or 0) <= C.MANAGER_NAT_SACK_HONEYMOON_YEARS:
        p *= float(C.NAT_V2_HONEYMOON)
    return max(lo, min(C.MANAGER_NAT_SACK_MAX, p)), case, prim, relief


def renew_diff(prim, relief, components):
    """재계약 판정용 단계 차이(현행 MANAGER_NAT_RENEW_BY_DIFF 눈금, 10 = 한 단계) — 경질과 대칭."""
    if prim is None:
        ex = max((c.get("exceed", 0) for c in components), default=0)
        return 10 * min(ex, 2)
    d = -10 * min(3, int(round(prim["steps"])))
    if prim.get("compete_miss"):
        d = -5
    if relief == "주요 대회 달성":
        d += 10
    return min(0, d)


def _region_relief(conn, country, year):
    best = None
    for r in conn.execute(
            """SELECT o.result FROM national_objectives o JOIN intl_tournaments t ON t.id=o.tournament_id
               WHERE t.kind='region' AND t.year=? AND o.country=?""", (int(year), country)):
        v = C.NAT_V2_REGION_RELIEF.get(r[0] or "", 1.0)
        best = v if best is None else min(best, v)
    return best


def evaluate_year(year):
    """[연말 — 클럽 감독 시장보다 먼저] 성인 대표팀 감독 연말 종합 평가. 그 해 끝난 주요 대회가 없으면 계약 만료
    재임만 처리한다. 반환: 감독이 바뀐 country_id 집합."""
    if not enabled():
        return set()
    import national_manager as NM
    from database import get_conn
    y = int(year)
    conn = get_conn()
    try:
        majors = [dict(r) for r in conn.execute(
            "SELECT id, kind, name, continent, status FROM intl_tournaments WHERE year=? AND kind IN ('world','continent')",
            (y,)) if r["status"] == "done"]
        # 주요 대회별 예선 묶음
        qual_of = {}
        for q in conn.execute("SELECT id, year, kind, name, continent FROM intl_tournaments "
                              "WHERE kind IN ('wc_qual','cont_qual') AND year IN (?,?)", (y, y - 1)):
            mid = main_for_qual(conn, q)
            if mid and any(m["id"] == mid for m in majors):
                qual_of.setdefault(mid, []).append(int(q["id"]))
        qrows = {}        # (main tid, country) -> 예선 행
        for mid, qids in qual_of.items():
            for qid in qids:
                for cn, r in settle_qual(conn, qid, mid).items():
                    qrows[(mid, cn)] = r
        objs = {}
        ents = {}
        for m in majors:
            ents[m["id"]] = {r[0] for r in conn.execute("SELECT country FROM intl_entries WHERE tournament_id=?", (m["id"],))}
            for r in conn.execute("SELECT * FROM national_objectives WHERE tournament_id=?", (m["id"],)):
                objs[(m["id"], r["country"])] = dict(r)
        cur = {}
        for r in conn.execute(
                """SELECT ntm.*, m.reputation, m.recent_perf, m.birth_year, m.retired,
                          COALESCE(m.manager_ability, 0) AS manager_ability
                   FROM national_team_managers ntm JOIN managers m ON m.id = ntm.manager_id
                   WHERE ntm.end_year IS NULL"""):
            cur[r["country_id"]] = dict(r)
        names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM countries")}
        pct = NM.world_squad_percentiles(conn) if majors else {}
        from database import get_game_start_year
        try:
            gsy = int(get_game_start_year())
        except Exception:
            gsy = None
        first_ids = {r[0]: r[1] for r in conn.execute(
            "SELECT country_id, MIN(id) FROM national_team_managers GROUP BY country_id")}
        salt = NM._world_salt()
        ends, vacancies, mgr_upd, changed = [], [], [], set()
        evaluated = [cid for cid in sorted(cur) if (cur[cid].get("role") or "full") != "caretaker"]
        leave_budget = max(1, int(len(evaluated) * C.MANAGER_NAT_CHANGE_MAX_SHARE))
        leaving = 0
        job_ab = None
        used_q = []
        for cid in evaluated:
            link = cur[cid]
            cn = names.get(cid)
            if not cn:
                continue
            fresh = int(link.get("start_year") or 0) == y and not (first_ids.get(cid) == link["id"] and gsy == y)
            if fresh:
                continue
            mid_now = int(link["manager_id"])
            comps = []
            for m in majors:
                w = 100 if m["kind"] == "world" else 90
                if m["name"] == EURO_NAME and cn not in ents[m["id"]] and (m["id"], cn) not in qrows:
                    continue
                if cn in ents[m["id"]]:
                    o = objs.get((m["id"], cn))
                    if not o or o.get("result_rank") is None or int(o["result_rank"]) < 0:
                        continue
                    if int(o.get("manager_id") or 0) and int(o["manager_id"]) != mid_now:
                        continue
                    orank, rrank = int(o["objective_rank"]), int(o["result_rank"])
                    comps.append(dict(w=w, src="본선", met=rrank >= orank, steps=max(0.0, (orank - rrank) / 10.0),
                                      exceed=max(0, (rrank - orank) // 10), dev=orank - rrank, goal=None,
                                      compete_miss=False, has_group=False, tname=m["name"],
                                      won=rrank >= int(C.MANAGER_NAT_STAGE_RANK["우승"])))
                elif (m["id"], cn) in qrows:
                    r = qrows[(m["id"], cn)]
                    if int(r.get("manager_id") or 0) and int(r["manager_id"]) != mid_now:
                        continue
                    met, steps, cm, dev = qual_outcome(r)
                    comps.append(dict(w=w, src="예선", met=met, steps=float(steps), exceed=max(0, int(r["result_level"]) - int(r["goal_level"])),
                                      dev=dev, goal=int(r["goal_level"]), compete_miss=cm,
                                      has_group=bool(r.get("group_finish")), tname=m["name"],
                                      qgoal=GOAL_NAME[int(r["goal_level"])], qlevel=LEVEL_NAME[int(r["result_level"])]))
                    used_q.append((r["tournament_id"], cn))
                    # 예선 결과 명성(본선에 못 간 경우만 — 본선 진출국은 본선 평가가 명성을 준다)
                    if int(r["result_level"]) < 3:
                        orank_q = 20 - (3 - int(r["goal_level"])) * 10
                        rrank_q = 20 - (3 - int(r["result_level"])) * 10 - (5 if cm else 0)
                        delta = NM.national_result_rep("예선탈락", orank_q, rrank_q, pct.get(cn, (0, 1.0))[1],
                                                       is_world_cup=(m["kind"] == "world"))
                        rep = max(0.0, min(100.0, float(link["reputation"] or 0.0) + delta))
                        link["reputation"] = rep
                        mgr_upd.append((rep, link["manager_id"]))
            contract_until = int(link.get("contract_until") or 0)
            tenure = max(0, y - int(link.get("start_year") or y))
            if not comps and not (contract_until and contract_until <= y):
                continue
            region = _region_relief(conn, cn, y)
            p, case, prim, relief = case_prob(comps, region, link.get("reputation"), tenure) if comps else \
                (0.0, "주요 대회 없음", None, "")
            rng = random.Random(zlib.crc32(f"natv2:{salt}:{cid}:{y}".encode("utf-8")))
            rec = dict(year=y, cid=cid, country=cn, link=link["id"], mid=mid_now, tenure=tenure, case=case, p=p,
                       relief=relief, region=region, contract_until=contract_until,
                       comps=[{k: c[k] for k in ("tname", "src", "met", "steps", "goal", "compete_miss")} for c in comps],
                       branch="", sacked=False, renewed=False, ended=False)
            if contract_until and contract_until > y:
                rec["branch"] = "sack"
                if comps and leaving < leave_budget and rng.random() < p:
                    ends.append((y, "sacked", link["id"])); vacancies.append(cid); changed.add(cid)
                    leaving += 1; rec["sacked"] = True
            else:
                rec["branch"] = "renew"
                rprob = NM.nat_renew_prob(0, renew_diff(prim, relief, comps))
                try:
                    import manager_ability as _mab
                    if job_ab is None:
                        job_ab = _mab.JobAbility(conn)
                    rprob *= _mab.renew_ability_mult(link.get("manager_ability"), job_ab.nation(cn),
                                                     link.get("recent_perf"))
                except Exception:
                    pass
                if leaving >= leave_budget or rng.random() < rprob:
                    nu = NM.nat_contract_until(conn, cid, y, rng)
                    conn.execute("UPDATE national_team_managers SET contract_until=?, "
                                 "contract_log = COALESCE(contract_log,'') || ? WHERE id=?",
                                 (nu, f";{y}:{nu}", link["id"]))
                    rec["renewed"] = True
                else:
                    ends.append((y, "contract_end", link["id"])); vacancies.append(cid); changed.add(cid)
                    leaving += 1; rec["ended"] = True
            V2_LOG.append(rec)
        if used_q:
            conn.executemany("UPDATE national_qual_objectives SET eval_year=? WHERE tournament_id=? AND country=?",
                             [(y, t_, c_) for t_, c_ in used_q])
        if mgr_upd:
            conn.executemany("UPDATE managers SET reputation=? WHERE id=?", mgr_upd)
        if ends:
            conn.executemany("UPDATE national_team_managers SET end_year=?, end_reason=? WHERE id=?", ends)
            left = [r[0] for r in conn.execute(
                "SELECT manager_id FROM national_team_managers WHERE id IN (%s)" % ",".join("?" * len(ends)),
                [e[2] for e in ends])]
            conn.executemany("UPDATE managers SET status='free', jobless_since=COALESCE(jobless_since,?) "
                             "WHERE id=? AND retired=0", [(y, m) for m in left])
        stats = {"sacked": sum(1 for e in ends if e[1] == "sacked"), "contract_end": sum(1 for e in ends if e[1] == "contract_end")}
        if vacancies:
            import manager_ability
            jab = manager_ability.JobAbility(conn)
            for cid in dict.fromkeys(vacancies):
                if NM._fill_vacancy(conn, cid, y, salt, stats, job_ab=jab):
                    changed.add(cid)
            try:
                manager_ability.assign_missing_manager_abilities(conn, year=y, mode="rookie")
            except Exception:
                pass
        conn.commit()
        if changed:
            try:
                import intl_engine
                intl_engine._nat_mgr_bonus_cache.clear()
            except Exception:
                pass
        return changed
    finally:
        conn.close()


def u23_absent_ranks(conn, year):
    """[U-23] 올림픽 예선 탈락국의 (목표 이름, 목표 rank, 결과 rank) — 예선 단계 목표를 U-23 기존 평가 눈금(본선
    단계 rank, 10 = 한 단계)으로 옮긴다. 예선 한 단계 = NAT_V2_QUAL_SLOPE / 0.009 rank(성인 예선 기울기와 같은 무게),
    경쟁력 목표국 미달은 그 절반. 본선 진출 = 20(조별리그)."""
    per = float(C.NAT_V2_QUAL_SLOPE) / float(C.MANAGER_NAT_SACK_MISS_SLOPE)
    out = {}
    for q in conn.execute("SELECT id, year, kind, name, continent FROM intl_tournaments WHERE kind='oly_qual' AND year=?",
                          (int(year),)):
        mid = main_for_qual(conn, q)
        for cn, r in settle_qual(conn, int(q["id"]), mid).items():
            if int(r["result_level"]) >= 3:
                continue
            met, steps, cm, dev = qual_outcome(r)
            g = int(r["goal_level"])
            orank = 20 - (3 - g) * per
            rrank = orank - (0 if met else (steps * per * (float(C.NAT_V2_COMPETE_MISS_MULT) if cm else 1.0)))
            out[cn] = (GOAL_NAME[g], orank, rrank)
    return out