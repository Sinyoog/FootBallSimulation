# -*- coding: utf-8 -*-
"""national_manager.py — 대표팀 감독 시장 (감독 시스템 ③-d, 2026-09 신설)

신민용 확정 파이프라인. 한 번 계산한 값을 아래 단계가 공유한다:

    national_power_snapshot → national_objective → tournament_result
    → objective_overachievement → manager_reputation → next-job market

[왜 이 순서인가 — 성능] "211개국 × 감독 후보 × 선수 계산" 같은 중첩을
피하는 게 핵심이다. 국가 전력과 목표를 **대회 시작 시점에 한 번** 굳혀
national_objectives에 박아두고, 이후 단계(결과 평가·명성·후보 평가)는
그 행을 읽기만 한다. 실측: 211개국 best-XI OVR 22ms vs 클럽 감독 시장
매 시즌 508ms — 굳혀두면 사실상 공짜다.

[대회 주기, 시즌 주기가 아니다] 신민용: "대표팀은 시즌마다 공석 판정하면
안 될 것 같아. 대회 주기 중심으로 보는 게 더 자연스러워." 그래서 이
모듈의 진입점은 두 개뿐이고 둘 다 intl_engine의 대회 생명주기에 붙는다:

    fix_national_objectives(tid)       ← 대회 생성 직후(경기 0개 시점)
    evaluate_national_tournament(tid)  ← _finish_tournament 직후

[teams에 가짜 행을 만들지 않는다] 신민용 확정: 대표팀 재임 이력은
national_team_managers에 따로 두고, 감독 **풀**(managers)은 클럽과
공유한다. 그래야 한 감독의 커리어가
    2024~2027 포르투갈 클럽 → 2028~2030 대한민국 대표팀 → 2031~ 일본 클럽
처럼 하나로 이어진다.

[한 감독은 한 자리만] 대표팀 감독으로 부임하면 managers.status='nation'이
되고, 클럽 시장(ai_lifecycle._manager_turnover)은 그 상태를 후보에서
제외한다. 물러나면 'free'가 되어 양쪽 시장의 후보가 된다.

[결정성] 클럽 시장과 같은 원칙 — 전역 random을 건드리지 않고
(world_salt, 연도, 국가)에서 파생한 random.Random 인스턴스만 쓴다.
"""
import random
import sqlite3
import zlib

from constants import (MANAGER_NAT_STAGE_RANK, MANAGER_NAT_CONTRACT_YEARS_MIN,
                       MANAGER_NAT_CONTRACT_YEARS_MAX, MANAGER_NAT_RENEW_BASE,
                       MANAGER_NAT_RENEW_SLOPE, MANAGER_NAT_RENEW_MIN,
                       MANAGER_NAT_RENEW_MAX, MANAGER_NAT_CARETAKER_CONTRACT,
                       MANAGER_NAT_CARETAKER_PROMOTE_BONUS,
                       MANAGER_NAT_CHANGE_MAX_SHARE, MANAGER_REP_RECENT_ALPHA,
                       MANAGER_HIRE_NOISE, MANAGER_ROOKIE_AGE_MIN,
                       MANAGER_ROOKIE_AGE_MAX, MANAGER_RETIRE_AGE_START,
                       MANAGER_RETIRE_AGE_SLOPE, MANAGER_RETIRE_AGE_HARD,
                       get_country_grade, national_job_level,
                       national_objective_for, national_result_rep,
                       national_sack_pressure, manager_reputation,
                       manager_career_floor)

# 마지막 evaluate_national_tournament 호출의 통계 — QA가 읽는다.
# 게임 로직은 쓰지 않는다(클럽 쪽 _LAST_MANAGER_MARKET과 같은 용도).
LAST_NATIONAL_MARKET = {}


# ════════════════════════════════════════════════════════════════
# 국가 전력 스냅샷
# ════════════════════════════════════════════════════════════════
def world_squad_percentiles(conn=None):
    """전 세계 국가의 best-XI OVR을 재서 {국가명: (squad_ovr, world_pct)}를
    돌려준다. world_pct는 0.0(최강)~1.0(최약).

    [백분위를 쓰는 이유] 신민용: "한국의 선수 OVR이 세계 20위 수준이면
    16강 목표가 자연스럽고, 황금세대가 와서 10위권으로 올라가면 목표도
    올라갈 수 있음." 절대 OVR 값에 밴드를 걸면 세계 전체 OVR이 인플레될 때
    목표가 같이 밀려버린다. 순위(백분위)로 재면 그게 자동으로 보정된다.

    [전력의 근거] database.get_country_best_xi_ovr — ai_players.
    true_nationality 기준 상위 11명 평균이다. 이게 intl_engine._nat_team_ovr가
    쓰는 값이고, 따라서 경기 시뮬(_match_outcome)이 쓰는 값과 같다.
    목표를 FIFA 등급 같은 다른 지표로 잡으면 구조적으로 달성 불가능한
    목표가 생긴다(③의 자기실현적 목표 함정과 같은 계열)."""
    from database import get_conn, get_country_best_xi_ovr
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        names = [r["name"] for r in conn.execute("SELECT name FROM countries")]
    finally:
        if close:
            conn.close()
    vals = []
    for cn in names:
        v = get_country_best_xi_ovr(cn)
        if v:
            vals.append((float(v), cn))
    if not vals:
        return {}
    vals.sort(reverse=True)            # 강한 순
    n = len(vals)
    out = {}
    for i, (v, cn) in enumerate(vals, 1):
        out[cn] = (v, i / n)
    return out


def _country_ids(conn):
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM countries")}


def fix_national_objectives(tournament_id, conn=None):
    """[진입점 1 — 대회 생성 직후] 참가국의 전력·목표를 굳혀
    national_objectives에 쓴다. 이미 있으면 아무것도 하지 않는다.

    신민용 강조: "목표는 대회 시작 전에 고정. 대회 도중 선수 OVR이 변했다고
    목표까지 바뀌면 이상해짐." 그래서 이 함수는 **멱등**이고, 한 번 쓴
    행은 결과 평가 때 말고는 절대 다시 안 건드린다.

    반환: 새로 쓴 목표 행 수."""
    from database import get_conn
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        t = conn.execute(
            "SELECT id, year, kind, name FROM intl_tournaments WHERE id=?",
            (tournament_id,)).fetchone()
        if not t:
            return 0
        # 예선은 목표 대상이 아니다 — 본선 진출 여부 자체가 결과라
        # 본선 대회 쪽에서 평가한다(_finish_tournament도 예선에선 안 돈다).
        if (t["kind"] or "") in ("wc_qual", "cont_qual", "power_eval",
                                 "power_eval_extra"):
            return 0
        have = conn.execute(
            "SELECT COUNT(*) FROM national_objectives WHERE tournament_id=?",
            (tournament_id,)).fetchone()[0]
        if have:
            return 0                   # 이미 굳었다 — 다시 계산하지 않는다
        entries = [r["country"] for r in conn.execute(
            "SELECT country FROM intl_entries WHERE tournament_id=?",
            (tournament_id,)).fetchall()]
        if not entries:
            return 0
        pct = world_squad_percentiles(conn)
        cids = _country_ids(conn)
        cur_mgr = {r["country_id"]: r["manager_id"] for r in conn.execute(
            "SELECT country_id, manager_id FROM national_team_managers "
            "WHERE end_year IS NULL")}
        rows = []
        for cn in entries:
            ovr, wp = pct.get(cn, (0.0, 1.0))
            name, rank = national_objective_for(wp)
            cid = cids.get(cn, 0)
            rows.append((tournament_id, int(t["year"]), cn, cid, ovr, wp,
                         name, rank, cur_mgr.get(cid, 0)))
        conn.executemany(
            """INSERT OR IGNORE INTO national_objectives(
                   tournament_id, year, country, country_id, squad_ovr,
                   world_pct, objective, objective_rank, manager_id)
               VALUES(?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        return len(rows)
    finally:
        if close:
            conn.close()


# ════════════════════════════════════════════════════════════════
# 대회 결과 → 국가별 도달 단계
# ════════════════════════════════════════════════════════════════
def country_stage_results(conn, tournament_id):
    """참가국별 도달 단계를 {국가명: (결과키, rank)}로 돌려준다.
    결과키는 MANAGER_NAT_STAGE_RANK의 키와 같다.

    [자체 구현한 이유] world_browser.get_country_tournament_results는
    한 번에 한 나라만 보고 호출마다 5질의쯤 쓴다 — 참가국 32~48개면
    150~250질의다. intl_engine._intl_country_stage_weights는 배치지만
    float 가중치만 주고 3·4위를 0.96으로 합친다. 그래서 같은 눈금
    (world_browser._STAGE_ORDER / _PLACEMENT_RANK / _STAGE_RANK)을 쓰면서
    질의 2회로 끝내는 버전을 여기 둔다."""
    winner = conn.execute(
        "SELECT winner FROM intl_tournaments WHERE id=?",
        (tournament_id,)).fetchone()
    winner = (winner["winner"] if winner else "") or ""
    _ORDER = {"R32": 0, "R16": 1, "QF": 2, "SF": 3}
    _KEY = {0: "32강", 1: "16강", 2: "8강", 3: "4강"}
    furthest, runner, third, fourth = {}, None, None, None
    for m in conn.execute(
            "SELECT stage, home, away, home_score, away_score, pso_winner "
            "FROM intl_matches WHERE tournament_id=? "
            "AND stage IN ('R32','R16','QF','SF','F','TP') AND home_score>=0",
            (tournament_id,)):
        stg = m["stage"]
        if stg == "F":
            runner = m["away"] if m["home"] == winner else m["home"]
            continue
        if stg == "TP":
            w = _tp_winner(m)
            third = w
            fourth = m["away"] if w == m["home"] else m["home"]
            continue
        idx = _ORDER.get(stg)
        if idx is None:
            continue
        for nat in (m["home"], m["away"]):
            if nat:
                furthest[nat] = max(furthest.get(nat, -1), idx)
    out = {}
    for r in conn.execute(
            "SELECT country FROM intl_entries WHERE tournament_id=?",
            (tournament_id,)):
        cn = r["country"]
        if cn == winner:
            key = "우승"
        elif cn == runner:
            key = "준우승"
        elif cn == third:
            key = "3위"
        elif cn == fourth:
            key = "4위"
        else:
            key = _KEY.get(furthest.get(cn, -1), "조별리그")
        out[cn] = (key, MANAGER_NAT_STAGE_RANK.get(key, 20))
    return out


def _tp_winner(m):
    """3/4위전 승자 — 승부차기까지 본다(intl_engine._winner_of와 같은 규칙,
    import 순환을 피하려고 여기 최소 구현만 둔다)."""
    if m["pso_winner"]:
        return m["pso_winner"]
    if (m["home_score"] or 0) >= (m["away_score"] or 0):
        return m["home"]
    return m["away"]


# ════════════════════════════════════════════════════════════════
# 대회 종료 평가 + 시장
# ════════════════════════════════════════════════════════════════
def evaluate_national_tournament(tournament_id, conn=None):
    """[진입점 2 — _finish_tournament 직후] 목표 대비 성적을 평가해서
    감독 명성을 갱신하고, 거취(재계약/계약종료/경질)를 정하고, 공석을
    채운다.

    신민용이 못박은 두 가지를 그대로 지킨다:
      · "계약 종료 = 경질로 만들지 않는 게 중요" — 월드컵 8강 후 계약
        만료로 물러나는 건 실패가 아니다. 계약이 남아 있는데 목표를 크게
        밑돌았을 때만 경질이다.
      · "국가 목표는 감독 개인의 경질 여부와 1:1로 연결하면 안 돼" —
        목표 미달은 **확률(압력)**만 올리고, 계약·명성·재임 기간이 함께
        판정에 들어간다.

    반환: 감독이 바뀐 country_id 집합."""
    from database import get_conn
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        return _evaluate(conn, tournament_id)
    finally:
        if close:
            conn.close()


def _evaluate(conn, tid):
    global LAST_NATIONAL_MARKET
    t = conn.execute(
        "SELECT id, year, kind, name, winner FROM intl_tournaments WHERE id=?",
        (tid,)).fetchone()
    if not t:
        return set()
    year = int(t["year"])
    is_wc = (t["kind"] or "") == "world"
    objs = {r["country"]: dict(r) for r in conn.execute(
        "SELECT * FROM national_objectives WHERE tournament_id=?", (tid,))}
    if not objs:
        # 목표가 안 굳어 있으면(구세이브에서 대회가 이미 진행 중이었던 경우)
        # 지금 굳혀서 평가한다 — 목표 없이 평가하면 전원 미달이 된다.
        fix_national_objectives(tid, conn=conn)
        objs = {r["country"]: dict(r) for r in conn.execute(
            "SELECT * FROM national_objectives WHERE tournament_id=?", (tid,))}
        if not objs:
            return set()
    stages = country_stage_results(conn, tid)

    # [예선 탈락국도 평가한다] 신민용이 든 경질 사례가 바로 이것이다:
    # "월드컵 예선 탈락 → 계약기간 남음 → 중도 경질은 경질." 그런데 예선에서
    # 떨어진 나라는 본선 intl_entries에 없어서 위 objs에 안 들어온다 —
    # 그대로 두면 **예선 탈락 감독은 평생 평가를 안 받는다**. 그래서 본선에
    # 못 온 나라를 '예선탈락'(rank 10)으로 같이 평가한다.
    #   · 월드컵(kind='world')은 전 세계가 대상이다.
    #   · 대륙컵/지역컵은 그 대륙 나라만 대상이다(유럽 국가를 아시안컵에
    #     못 왔다고 평가하면 안 된다).
    absent = []
    kind = (t["kind"] or "")
    if kind in ("world", "continent", "region"):
        if kind == "world":
            q = ("SELECT c.id, c.name FROM countries c "
                 "WHERE c.id IN (SELECT country_id FROM national_team_managers "
                 "               WHERE end_year IS NULL)")
            args = ()
        else:
            cont = conn.execute(
                "SELECT continent FROM intl_tournaments WHERE id=?", (tid,)
            ).fetchone()
            cont = (cont["continent"] if cont else "") or ""
            q = ("SELECT c.id, c.name FROM countries c "
                 "WHERE c.continent=? AND c.id IN "
                 "  (SELECT country_id FROM national_team_managers "
                 "   WHERE end_year IS NULL)")
            args = (cont,)
        pct_all = None
        for r in conn.execute(q, args):
            if r["name"] in objs:
                continue
            if pct_all is None:
                pct_all = world_squad_percentiles(conn)
            ovr, wp = pct_all.get(r["name"], (0.0, 1.0))
            oname, orank = national_objective_for(wp)
            absent.append({"id": 0, "country": r["name"], "country_id": r["id"],
                           "world_pct": wp, "objective": oname,
                           "objective_rank": orank, "_absent": True})

    salt = _world_salt()
    stats = {"evaluated": 0, "renewed": 0, "contract_end": 0, "sacked": 0,
             "resigned": 0, "appointed": 0, "caretaker": 0, "promoted": 0,
             "rep_up": 0, "rep_down": 0, "retired": 0, "caretaker_created": 0}

    cur = {}       # country_id -> national_team_managers 행
    for r in conn.execute(
            """SELECT ntm.*, m.reputation, m.recent_perf, m.recent_level,
                      m.career_best_level, m.best_level_year, m.titles_intl,
                      m.birth_year, m.retired
               FROM national_team_managers ntm
               JOIN managers m ON m.id = ntm.manager_id
               WHERE ntm.end_year IS NULL"""):
        cur[r["country_id"]] = dict(r)

    # ── 은퇴 / 정합성 정리 ───────────────────────────────────────
    # [왜 여기서 하나] 클럽 시장(_manager_turnover)은 대표팀 감독을 은퇴
    # 대상에서 제외한다 — 거기서 retired=1을 박으면 재임 행의 end_year가
    # NULL로 남아 "은퇴한 사람이 대표팀 감독으로 등재"되는 상태가 된다
    # (실측 45건). 그래서 대표팀 감독의 은퇴는 이 함수가 맡는다: 재임을
    # 닫고 공석으로 올려서, 같은 패스의 공석 채우기가 후임을 세운다.
    _salt_r = _world_salt()
    gone = []          # 이 대회 시점에 자리를 비우는 (재임id, country_id, 은퇴여부)
    for cid, link in list(cur.items()):
        if int(link.get("retired") or 0):
            # 어딘가에서 이미 은퇴 처리된 사람 — 재임 행만 열려 있던 상태.
            gone.append((link["id"], cid, False))
            continue
        age = year - int(link.get("birth_year") or year)
        p_ret = 0.0
        if age >= MANAGER_RETIRE_AGE_HARD:
            p_ret = 1.0
        elif age > MANAGER_RETIRE_AGE_START:
            p_ret = (age - MANAGER_RETIRE_AGE_START) * MANAGER_RETIRE_AGE_SLOPE
        if p_ret <= 0:
            continue
        rr = random.Random(zlib.crc32(
            f"natret:{_salt_r}:{link['manager_id']}:{year}".encode("utf-8")))
        if rr.random() < p_ret:
            gone.append((link["id"], cid, True))
    for link_id, cid, do_retire in gone:
        conn.execute("UPDATE national_team_managers SET end_year=?, "
                     "end_reason='retired' WHERE id=?", (year, link_id))
        if do_retire:
            mid = cur[cid]["manager_id"]
            conn.execute(
                "UPDATE managers SET retired=1, status='retired' WHERE id=?", (mid,))
        cur.pop(cid, None)
    retired_cids = [cid for (_l, cid, _r) in gone]
    stats["retired"] = sum(1 for (_l, _c, r) in gone if r)
    obj_result_updates, mgr_updates, tenure_updates = [], [], []
    ends, vacancies = [], []
    changed = set()

    # 본선 참가국 + 예선 탈락국을 한 흐름으로 평가한다. 이탈 상한은
    # 참가국 수가 아니라 실제 평가 대상 수로 잡는다.
    eval_list = list(objs.values()) + absent
    leave_budget = max(1, int(len(eval_list) * MANAGER_NAT_CHANGE_MAX_SHARE))
    leaving = 0

    for ob in eval_list:
        cn = ob["country"]
        if ob.get("_absent"):
            key, rrank = "예선탈락", MANAGER_NAT_STAGE_RANK["예선탈락"]
        else:
            key, rrank = stages.get(cn, ("조별리그", 20))
            obj_result_updates.append((key, rrank, ob["id"]))
        cid = int(ob["country_id"] or 0)
        link = cur.get(cid)
        if not link:
            if cid:
                vacancies.append(cid)
            continue
        stats["evaluated"] += 1
        orank = int(ob["objective_rank"] or 0)
        wp = float(ob["world_pct"] or 1.0)
        rng = random.Random(zlib.crc32(f"nat:{salt}:{cid}:{year}:{tid}".encode("utf-8")))

        # ── 명성 갱신 ────────────────────────────────────────────
        delta = national_result_rep(key, orank, rrank, wp, is_world_cup=is_wc)
        rep = max(0.0, min(100.0, float(link["reputation"] or 0.0) + delta))
        if delta > 0:
            stats["rep_up"] += 1
        elif delta < 0:
            stats["rep_down"] += 1
        # 최근 성적 EWMA — 목표 대비 편차를 -1~+1로 눌러 넣는다. 클럽과
        # 같은 축이라야 클럽 복귀 판정(상한)에 그대로 쓰인다.
        perf = max(-1.0, min(1.0, (rrank - orank) / 40.0))
        rperf = ((1.0 - MANAGER_REP_RECENT_ALPHA) * float(link["recent_perf"] or 0.0)
                 + MANAGER_REP_RECENT_ALPHA * perf)
        titles_intl = int(link["titles_intl"] or 0) + (1 if key == "우승" else 0)
        mgr_updates.append((rep, rperf, titles_intl, link["manager_id"]))
        tenure_updates.append((rrank, link["id"]))

        # ── 거취 ─────────────────────────────────────────────────
        contract_until = int(link["contract_until"] or 0)
        tenure_years = max(0, year - int(link["start_year"] or year))
        role = (link["role"] or "full")
        if role == "caretaker":
            # 임시 감독은 대회를 치르면 임기 종료. 성과가 좋았으면 정식
            # 후보 평가에서 가산점을 받는다(아래 _fill_vacancy).
            ends.append((year, "caretaker_end", link["id"]))
            vacancies.append(cid)
            changed.add(cid)
            continue
        if contract_until and contract_until > year:
            # 계약 중 → 경질 판정만. 목표 미달이 압력을 올리지만 1:1이 아니다.
            prob = national_sack_pressure(orank, rrank, rep, tenure_years)
            if leaving < leave_budget and rng.random() < prob:
                ends.append((year, "sacked", link["id"]))
                vacancies.append(cid)
                stats["sacked"] += 1
                leaving += 1
                changed.add(cid)
            continue
        # 계약 만료 → 재계약 협상. **경질이 아니다.** 신민용 강조: "월드컵
        # 8강 후 계약 종료로 재계약하지 않고 무직이 되는 건 실패가 아니야."
        rprob = MANAGER_NAT_RENEW_BASE + (rrank - orank) * MANAGER_NAT_RENEW_SLOPE
        rprob = max(MANAGER_NAT_RENEW_MIN, min(MANAGER_NAT_RENEW_MAX, rprob))
        if leaving >= leave_budget or rng.random() < rprob:
            nu = year + rng.randint(MANAGER_NAT_CONTRACT_YEARS_MIN,
                                    MANAGER_NAT_CONTRACT_YEARS_MAX)
            conn.execute(
                "UPDATE national_team_managers SET contract_until=? WHERE id=?",
                (nu, link["id"]))
            stats["renewed"] += 1
        else:
            ends.append((year, "contract_end", link["id"]))
            vacancies.append(cid)
            stats["contract_end"] += 1
            leaving += 1
            changed.add(cid)

    if obj_result_updates:
        conn.executemany(
            "UPDATE national_objectives SET result=?, result_rank=? WHERE id=?",
            obj_result_updates)
    if mgr_updates:
        conn.executemany(
            "UPDATE managers SET reputation=?, recent_perf=?, titles_intl=? "
            "WHERE id=?", mgr_updates)
    if tenure_updates:
        conn.executemany(
            "UPDATE national_team_managers SET tournaments=COALESCE(tournaments,0)+1, "
            "best_rank=CASE WHEN COALESCE(best_rank,0) < ? THEN ? ELSE best_rank END "
            "WHERE id=?", [(r, r, i) for (r, i) in tenure_updates])
    if ends:
        conn.executemany(
            "UPDATE national_team_managers SET end_year=?, end_reason=? WHERE id=?",
            ends)
        # 물러난 감독은 무직이 된다 — 이제 클럽/대표팀 양쪽 시장의 후보다.
        left_ids = [r["manager_id"] for r in conn.execute(
            "SELECT manager_id FROM national_team_managers WHERE id IN (%s)"
            % ",".join("?" * len(ends)), [e[2] for e in ends])]
        conn.executemany(
            "UPDATE managers SET status='free', jobless_since=COALESCE(jobless_since,?) "
            "WHERE id=? AND retired=0", [(year, m) for m in left_ids])

    # ── 공석 채우기 ──────────────────────────────────────────────
    # 상한은 위에서 **이탈**에만 걸었다. 여기서는 생긴 공석을 전부 채운다 —
    # 부임 쪽에 상한을 걸었더니 감독 없는 대표팀이 9개국 남았다.
    for cid in dict.fromkeys(list(retired_cids) + vacancies):
        if _fill_vacancy(conn, cid, year, salt, stats):
            changed.add(cid)
    conn.commit()
    stats["vacancies"] = len(set(vacancies))
    LAST_NATIONAL_MARKET = stats
    return changed


def _world_salt():
    from database import get_world_salt
    return get_world_salt()


def _fill_vacancy(conn, country_id, year, salt, stats):
    """대표팀 공석 하나를 채운다. 같은 managers 풀에서 뽑고, 정식 감독을
    못 구하면 임시 감독을 세운다.

    신민용:
      · "대표팀 감독 선임에서 국적을 강한 제한 조건으로 쓰면 안 됨. 대신
        국적은 선호도 정도의 가중치로만" — _manager_job_fit의 약한 가산점.
      · "새로운 감독을 하나 생성하는 게 아니라 기존 무직 감독을 임시로
        배정하는 게 좋음" — 임시 감독도 풀에서 뽑는다.
      · "국가 티어가 낮다 = 감독 수준도 낮아야 한다는 식으로 만들면 안 돼"
        — 대표팀 축(MANAGER_NATIONAL_GRADE_LEVEL)이 이미 폭이 좁다."""
    import ai_lifecycle as al
    row = conn.execute("SELECT name FROM countries WHERE id=?",
                       (country_id,)).fetchone()
    if not row:
        return False
    cname = row["name"]
    lv = national_job_level(get_country_grade(cname))
    vr = random.Random(zlib.crc32(f"natfill:{salt}:{country_id}:{year}".encode("utf-8")))

    # 후보 풀 — 무직 + 클럽 현직(대표팀 제안을 받을 수 있다).
    # 현직 대표팀 감독은 제외한다(한 감독은 한 자리).
    cands = []
    for r in conn.execute(
            """SELECT m.id, m.nationality, m.reputation, m.recent_level,
                      m.career_best_level, m.best_level_year, m.recent_perf,
                      m.status, m.jobless_since
               FROM managers m
               WHERE m.retired=0 AND m.status IN ('free','club')
                 AND COALESCE(m.recent_level,0) >= ?
               LIMIT 4000""", (max(0.0, lv * 0.55),)):
        mm = dict(r)
        mm["_job_kind"] = "club" if mm["status"] == "club" else "free"
        js = mm.get("jobless_since")
        jl = max(0, year - int(js)) if (js and mm["status"] == "free") else 0
        ok, step, bonus = al._manager_job_fit(
            lv, "nation", mm, year, country=cname,
            base_level=float(mm.get("recent_level") or 0.0), jobless_years=jl)
        if not ok:
            continue
        # 임시 감독으로 성과를 낸 이력이 있으면 가산점.
        promo = 0.0
        if conn.execute(
                "SELECT 1 FROM national_team_managers WHERE manager_id=? "
                "AND role='caretaker' AND best_rank >= 40 LIMIT 1",
                (mm["id"],)).fetchone():
            promo = MANAGER_NAT_CARETAKER_PROMOTE_BONUS
        score = (float(mm.get("reputation") or 0.0) * 0.5 + bonus + promo
                 - abs(float(mm.get("recent_level") or 0.0) - lv) * 0.2
                 + vr.uniform(0, MANAGER_HIRE_NOISE))
        cands.append((score, step, mm))
    cands.sort(key=lambda x: -x[0])

    for _s, step, mm in cands[:24]:
        if step < 1.0 and vr.random() >= step:
            continue
        _appoint(conn, country_id, mm["id"], year, lv, "full",
                 year + vr.randint(MANAGER_NAT_CONTRACT_YEARS_MIN,
                                   MANAGER_NAT_CONTRACT_YEARS_MAX),
                 vacate_club=(mm["status"] == "club"))
        stats["appointed"] = stats.get("appointed", 0) + 1
        if mm["status"] == "club":
            stats["promoted"] = stats.get("promoted", 0) + 1
        return True

    # 정식 감독을 못 구했다 → 임시 감독. 신민용: "새로운 감독을 하나
    # 생성하는 게 아니라 기존 무직 감독을 임시로 배정하는 게 좋음."
    ct = conn.execute(
        """SELECT id FROM managers WHERE retired=0 AND status='free'
           ORDER BY ABS(COALESCE(recent_level,0) - ?) LIMIT 1""", (lv,)).fetchone()
    if ct:
        _appoint(conn, country_id, ct["id"], year, lv, "caretaker",
                 year + MANAGER_NAT_CARETAKER_CONTRACT, vacate_club=False)
        stats["caretaker"] = stats.get("caretaker", 0) + 1
        return True
    # 무직 풀이 통째로 비었을 때만(세계 최초 시즌 등) 새로 만든다 — 대표팀이
    # 감독 없이 남는 것보다는 낫다. 평상시엔 위에서 끝난다.
    from database import build_manager_row, MANAGER_INSERT_SQL
    row = build_manager_row(vr, cname, year,
                            age_range=(MANAGER_ROOKIE_AGE_MIN + 6,
                                       MANAGER_ROOKIE_AGE_MAX + 8))
    base = conn.execute("SELECT COALESCE(MAX(id),0) FROM managers").fetchone()[0]
    conn.execute(MANAGER_INSERT_SQL, row)
    new = conn.execute("SELECT id FROM managers WHERE id > ? ORDER BY id LIMIT 1",
                       (base,)).fetchone()
    if not new:
        return False
    _appoint(conn, country_id, new["id"], year, lv, "caretaker",
             year + MANAGER_NAT_CARETAKER_CONTRACT, vacate_club=False)
    stats["caretaker"] = stats.get("caretaker", 0) + 1
    stats["caretaker_created"] = stats.get("caretaker_created", 0) + 1
    return True


def _appoint(conn, country_id, manager_id, year, job_level, role,
             contract_until, vacate_club=False):
    """부임 기록 + managers.status='nation'. 클럽 현직이었으면 그 자리를
    비운다 — 신민용 확정: "대표팀 감독으로 부임하면 클럽 감독직은 비워야
    함. 한 감독의 active job은 하나만 존재하게."

    비워진 클럽 자리는 end_reason='moved_to_nation'으로 남고, 다음 시즌
    클럽 시장(_manager_turnover)이 감독 없는 팀으로 보고 채운다."""
    if vacate_club:
        conn.execute(
            "UPDATE team_managers SET end_year=?, end_reason='moved_to_nation' "
            "WHERE manager_id=? AND end_year IS NULL AND job_kind='club'",
            (year, manager_id))
    conn.execute(
        """INSERT INTO national_team_managers(
               country_id, manager_id, start_year, end_year, end_reason,
               role, contract_until, job_level)
           VALUES(?,?,?,NULL,'',?,?,?)""",
        (country_id, manager_id, year, role, contract_until, job_level))
    conn.execute(
        """UPDATE managers SET status='nation', jobless_since=NULL,
                  recent_level=?,
                  best_level_year=CASE WHEN ? > COALESCE(career_best_level,0)
                                       THEN ? ELSE best_level_year END,
                  career_best_level=MAX(COALESCE(career_best_level,0), ?),
                  career_floor=?
           WHERE id=?""",
        (job_level, job_level, year, job_level,
         manager_career_floor(job_level, 0, 0), manager_id))


# ════════════════════════════════════════════════════════════════
# 최초 시드
# ════════════════════════════════════════════════════════════════
def seed_national_managers(conn=None, verbose=True):
    """대표팀 감독이 없는 나라에 초기 감독을 만든다(클럽 쪽
    database._migrate_managers와 같은 역할).

    [왜 새로 만드는가] 세계 최초 시점엔 무직 풀이 비어 있다(전원이 클럽에
    취업 중). 여기서 클럽 감독을 빼오면 첫 시즌에 클럽 211자리가 동시에
    비어버린다. 그래서 시드만 새로 만들고, 이후 교체는 공유 풀에서 뽑는다.

    반환: 새로 부임시킨 수."""
    from database import (get_conn, build_manager_row, MANAGER_INSERT_SQL,
                         get_world_salt, get_game_start_year)
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        row = conn.execute(
            "SELECT current_year FROM season_state WHERE id=1").fetchone()
        year = int(row["current_year"]) if (row and row["current_year"]) \
            else get_game_start_year()
        todo = [dict(r) for r in conn.execute(
            """SELECT c.id, c.name FROM countries c
               WHERE NOT EXISTS (SELECT 1 FROM national_team_managers n
                                 WHERE n.country_id = c.id AND n.end_year IS NULL)""")]
        if not todo:
            return 0
        salt = get_world_salt()
        mgr_rows, extras = [], []
        for r in todo:
            rng = random.Random(zlib.crc32(f"natmgr:{salt}:{r['id']}".encode("utf-8")))
            mgr_rows.append(build_manager_row(
                rng, r["name"], year,
                age_range=(MANAGER_ROOKIE_AGE_MIN + 6, MANAGER_ROOKIE_AGE_MAX + 12)))
            extras.append((r["id"],
                           national_job_level(get_country_grade(r["name"])),
                           year + rng.randint(MANAGER_NAT_CONTRACT_YEARS_MIN,
                                              MANAGER_NAT_CONTRACT_YEARS_MAX)))
        cur = conn.cursor()
        base = cur.execute("SELECT COALESCE(MAX(id),0) FROM managers").fetchone()[0]
        cur.executemany(MANAGER_INSERT_SQL, mgr_rows)
        new_ids = [r[0] for r in cur.execute(
            "SELECT id FROM managers WHERE id > ? ORDER BY id", (base,)).fetchall()]
        if len(new_ids) != len(extras):
            conn.rollback()
            if verbose:
                print(f"[NAT] 대표팀 감독 시드 중단 — 생성 {len(new_ids)} vs "
                      f"국가 {len(extras)} 불일치")
            return 0
        cur.executemany(
            """INSERT INTO national_team_managers(
                   country_id, manager_id, start_year, end_year, end_reason,
                   role, contract_until, job_level)
               VALUES(?,?,?,NULL,'','full',?,?)""",
            [(extras[i][0], new_ids[i], year, extras[i][2], extras[i][1])
             for i in range(len(new_ids))])
        cur.executemany(
            "UPDATE managers SET status='nation', career_best_level=?, "
            "recent_level=?, best_level_year=?, career_floor=? WHERE id=?",
            [(extras[i][1], extras[i][1], year,
              manager_career_floor(extras[i][1], 0, 0), new_ids[i])
             for i in range(len(new_ids))])
        conn.commit()
        if verbose:
            print(f"[NAT] 대표팀 감독 시드 {len(new_ids)}개국")
        return len(new_ids)
    except sqlite3.OperationalError as e:
        if verbose:
            print(f"[NAT] 대표팀 감독 시드 건너뜀({e})")
        return 0
    finally:
        if close:
            conn.close()


def get_national_manager(country_id, conn=None):
    """현재 그 나라 대표팀 감독 행(없으면 None). UI/조회용."""
    from database import get_conn
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        return conn.execute(
            """SELECT ntm.*, m.name, m.nationality, m.reputation, m.manager_type,
                      m.style_attack, m.style_buildup, m.style_press
               FROM national_team_managers ntm
               JOIN managers m ON m.id = ntm.manager_id
               WHERE ntm.country_id=? AND ntm.end_year IS NULL LIMIT 1""",
            (country_id,)).fetchone()
    finally:
        if close:
            conn.close()