# -*- coding: utf-8 -*-
"""manager_ability_market_qa.py — 6번 ① 감독 시장 능력 적합도 검증 (2026-10 신설)

같은 세이브 사본에서 감독 시장만 N시즌 돌려 능력 적합도 OFF(기준선)와 ON을
비교한다. 경기 엔진은 돌리지 않는다 — 전력 기반 가상 결과를 리그 경기에
주입하고(manager_market_qa와 같은 방식) 클럽 시장(_manager_turnover)을 돌린다.
대표팀은 대회 평가 없이 매 시즌 일부 대표팀 자리를 비우고 _fill_vacancy로 채운다.
선수 OVR은 이 사이 변하지 않으므로 "시장 매칭만" 비교된다.

보는 것
  1. 신규 부임(재취업·이직)의 (능력 − 요구 능력) 차이 분포 / 상관
  2. 팀 OVR 상위 50·100 클럽 현직 감독의 평균 능력, 90+ 감독 수
  3. 능력 90+ 감독 중 무직 / 하위 자리에 있는 수
  4. 상한 유지 — 평판 60 미만이 상위 50 클럽에 새로 부임한 수(OFF와 비교)
  5. 시장 통계(공석·신인·이직·재취업·외국인 부임)가 크게 흔들리지 않는가
  6. 결정성 — 전역 random 불변, 같은 입력 재실행 해시 일치(--hash)

사용법:
    python tools/manager_ability_market_qa.py --db <세이브.db> --years 8 --fit on|off
"""
import _path  # noqa: F401
import argparse
import collections
import hashlib
import json
import os
import random
import shutil
import statistics
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def _fill_results(conn, season, rng):
    ovr = {r[0]: r[1] for r in conn.execute(
        """SELECT team_id, AVG(ovr) FROM (
               SELECT team_id, ovr, ROW_NUMBER() OVER (
                   PARTITION BY team_id ORDER BY ovr DESC) rn
               FROM ai_players WHERE team_id IS NOT NULL) WHERE rn<=11
           GROUP BY team_id""")}
    ups = []
    for r in conn.execute(
            "SELECT id, home_team_id, away_team_id FROM match_results WHERE season=? "
            "ORDER BY id", (season,)):
        edge = (ovr.get(r[1], 50.0) - ovr.get(r[2], 50.0)) / 8.0
        ups.append((max(0, int(round(rng.gauss(1.35 + edge * 0.35, 1.1)))),
                    max(0, int(round(rng.gauss(1.15 - edge * 0.35, 1.1)))), r[0]))
    conn.executemany("UPDATE match_results SET home_score=?, away_score=? WHERE id=?", ups)
    conn.commit()


class _FallbackJobAbility:
    """원본 코드(JobAbility 없음) 실행용 — 같은 정의로 요구 능력을 잰다."""
    def __init__(self, mab):
        self.mab = mab

    def __call__(self, conn):
        from constants import national_job_level, get_country_grade
        o = type("JA", (), {})()
        o.tovr = self.mab.team_ovr_map(conn)
        f = self.mab._level_to_ovr_fn(self.mab._team_meta(conn), o.tovr)
        o.club = lambda tid, lv=None: o.tovr.get(int(tid)) if int(tid) in o.tovr else f(lv or 0)
        o.nation = lambda cn: f(national_job_level(get_country_grade(cn)))
        return o


def _add_retirees(conn, year, n, rng):
    """[선출 감독 QA] 시장만 도는 QA라 실제 은퇴가 안 생긴다 — 은퇴 선수 아카이브에
    가상 은퇴자 n명(국적은 현역 분포에서 뽑음, 은퇴 나이 32~38)을 그 해 은퇴로 넣는다.
    id는 현역/은퇴 최대 id 뒤에서 새로 잡는다(현역 선수와 겹치지 않게)."""
    if n <= 0:
        return
    nats = [r[0] for r in conn.execute(
        "SELECT nationality FROM ai_players WHERE nationality IS NOT NULL ORDER BY id")]
    base = max(conn.execute("SELECT COALESCE(MAX(id),0) FROM ai_players").fetchone()[0],
               conn.execute("SELECT COALESCE(MAX(id),0) FROM ai_players_retired").fetchone()[0])
    rows = [(base + i + 1, "", "CM", 70, rng.randint(32, 38), nats[rng.randrange(len(nats))],
             None, "", year) for i in range(n)]
    conn.executemany(
        """INSERT INTO ai_players_retired(id, name, position, ovr, age, nationality,
               last_team_id, last_team_name, retirement_year) VALUES(?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()


def _hash(conn):
    h = hashlib.blake2b(digest_size=16)
    for row in conn.execute(
            "SELECT id, status, contract_until, jobless_since, career_best_level, "
            "recent_level, reputation, retired, manager_ability FROM managers ORDER BY id"):
        h.update(repr(tuple(row)).encode())
    for row in conn.execute(
            "SELECT team_id, manager_id, start_year, end_year, end_reason "
            "FROM team_managers ORDER BY id"):
        h.update(repr(tuple(row)).encode())
    for row in conn.execute(
            "SELECT country_id, manager_id, start_year, end_year, role "
            "FROM national_team_managers ORDER BY id"):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--years", type=int, default=8)
    ap.add_argument("--fit", choices=("on", "off", "absent"), default="on",
                    help="absent = 스위치가 없는 원본 코드(기준선 해시 대조용)")
    ap.add_argument("--nat-share", type=float, default=0.15)
    ap.add_argument("--out", default=None)
    ap.add_argument("--retirees", type=int, default=0,
                    help="해마다 넣을 가상 은퇴자 수(선출 감독 전환 확인용, 0=안 넣음)")
    args = ap.parse_args()

    if args.out:
        args.out = os.path.abspath(args.out)
    import constants as C
    if args.fit != "absent":
        C.MANAGER_ABILITY_FIT_ENABLED = (args.fit == "on")

    work = tempfile.mkdtemp(prefix="mab_qa_")
    db = os.path.join(work, "qa.db")
    shutil.copy(os.path.abspath(args.db), db)
    hsrc = os.path.splitext(os.path.abspath(args.db))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")
    import database as d
    d.USE_MEMORY_DB = False
    d.DB_PATH = db
    os.chdir(work)
    d.reset_conn_pool()
    d.init_db()

    import ai_lifecycle as al
    import national_manager as nm
    import manager_ability as mab
    conn = d.get_conn()
    c = conn.cursor()
    JA = getattr(mab, "JobAbility", None) or _FallbackJobAbility(mab)
    has_job_ab = "job_ab" in nm._fill_vacancy.__code__.co_varnames
    meta = mab._team_meta(conn)
    season = conn.execute("SELECT MAX(season) FROM match_results").fetchone()[0]
    year0 = conn.execute("SELECT MAX(year) FROM match_results").fetchone()[0]

    if args.retirees:
        _rr = random.Random(777)
        for _back in range(14, 0, -1):          # 지난 14년치 은퇴자(데뷔 가능 풀)
            _add_retirees(conn, year0 - _back, args.retirees, _rr)
    random.seed(24680)
    g_before = random.getstate()
    per_year = []
    hires_all = []          # (year, kind, ability, req, rep, cbl_before, team_rank)
    nat_hires = []

    for k in range(args.years):
        y = year0 + k
        _fill_results(conn, season, random.Random(4321 + k))
        if args.retirees:
            _add_retirees(conn, y, args.retirees, random.Random(9100 + k))
        before = {r[0]: (r[1], r[2], r[3], r[4]) for r in conn.execute(
            "SELECT id, COALESCE(career_best_level,0), COALESCE(reputation,0), status, "
            "COALESCE(recent_level,0) FROM managers")}
        mx_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM managers").fetchone()[0]
        al._manager_turnover(c, y, season)
        conn.commit()
        st = dict(al._LAST_MANAGER_MARKET)
        rk = [tuple(r) for r in conn.execute(
            "SELECT manager_ability, manager_potential, from_player_id, birth_year "
            "FROM managers WHERE id>?", (mx_id,))]

        ja = JA(conn)
        order = sorted(ja.tovr, key=lambda t: -ja.tovr[t])
        trank = {t: i + 1 for i, t in enumerate(order)}
        for r in conn.execute(
                """SELECT tm.team_id, tm.manager_id, m.manager_ability, m.clubs_managed
                   FROM team_managers tm JOIN managers m ON m.id = tm.manager_id
                   WHERE tm.start_year=? AND tm.job_kind='club'""", (y,)):
            b = before.get(r[1])
            kind = "rookie" if b is None else ("move" if b[2] == "club" else "rehire")
            jl = meta.get(r[0], ("", 0.0, 0))[1]
            prev = max(b[0], b[3]) if b else 0.0
            hires_all.append((y, kind, r[2], ja.club(r[0]), b[1] if b else 0.0,
                              b[0] if b else 0.0, trank.get(r[0], 99999), jl - prev))

        # ── 대표팀: 일부 자리를 비우고 다시 채운다 ──
        nrng = random.Random(9001 + k)
        cur = [tuple(r) for r in conn.execute(
            "SELECT id, country_id, manager_id FROM national_team_managers "
            "WHERE end_year IS NULL ORDER BY id")]
        pick = [x for x in cur if nrng.random() < args.nat_share]
        if pick:
            conn.executemany("UPDATE national_team_managers SET end_year=?, "
                             "end_reason='qa' WHERE id=?", [(y, x[0]) for x in pick])
            conn.executemany("UPDATE managers SET status='free', jobless_since=? "
                             "WHERE id=?", [(y, x[2]) for x in pick])
            nst = {}
            nja = JA(conn)
            for x in pick:
                if has_job_ab:
                    nm._fill_vacancy(conn, x[1], y, "qa-salt", nst, job_ab=nja)
                else:
                    nm._fill_vacancy(conn, x[1], y, "qa-salt", nst)
            conn.commit()
            cname = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM countries")}
            for r in conn.execute(
                    """SELECT n.country_id, m.manager_ability, m.reputation, n.role
                       FROM national_team_managers n JOIN managers m ON m.id=n.manager_id
                       WHERE n.start_year=? AND n.end_year IS NULL""", (y,)):
                nat_hires.append((y, r[3], r[1], nja.nation(cname[r[0]]), r[2]))
            try:
                mab.assign_missing_manager_abilities(conn, year=y, mode="rookie")
            except TypeError:
                mab.assign_missing_manager_abilities(conn, year=y)
            conn.commit()

        # ── 연말 상태 ──
        top = {}
        for r in conn.execute(
                """SELECT tm.team_id, m.manager_ability FROM team_managers tm
                   JOIN managers m ON m.id=tm.manager_id
                   WHERE tm.end_year IS NULL AND tm.job_kind='club'"""):
            top[r[0]] = r[1] or 0
        t50 = [top[t] for t in order[:50] if t in top]
        t100 = [top[t] for t in order[:100] if t in top]
        elite = [tuple(r) for r in conn.execute(
            "SELECT id, status FROM managers WHERE retired=0 AND manager_ability>=90")]
        club_of = {r[1]: r[0] for r in conn.execute(
            "SELECT team_id, manager_id FROM team_managers WHERE end_year IS NULL")}
        elite_low = sum(1 for mid, s in elite
                        if s == "club" and trank.get(club_of.get(mid), 99999) > 300)
        act = [tuple(r) for r in conn.execute(
            "SELECT manager_ability, manager_potential FROM managers WHERE retired=0")]
        per_year.append({
            "rk_n": len(rk), "rk_xp": sum(1 for x in rk if x[2]),
            "rk_ab80": sum(1 for x in rk if x[0] >= 80),
            "rk_ab85": sum(1 for x in rk if x[0] >= 85),
            "rk_ab90": sum(1 for x in rk if x[0] >= 90),
            "rk_p85": sum(1 for x in rk if x[1] >= 85),
            "rk_p90": sum(1 for x in rk if x[1] >= 90),
            "rk_p95": sum(1 for x in rk if x[1] >= 95),
            "w_ab85": sum(1 for a, _p in act if a >= 85),
            "w_ab90": sum(1 for a, _p in act if a >= 90),
            "w_p90": sum(1 for _a, p in act if p >= 90),
            "year": y, "vacancies": st.get("vacancies", 0), "rookie": st.get("rookie", 0),
            "moved": st.get("moved", 0), "rehired": st.get("rehired", 0),
            "foreign": st.get("hire_foreign", 0), "stretched": st.get("stretched", 0),
            "top50_ab": round(statistics.mean(t50), 2) if t50 else None,
            "top100_ab": round(statistics.mean(t100), 2) if t100 else None,
            "top100_90plus": sum(1 for v in t100 if v >= 90),
            "elite_n": len(elite),
            "elite_free": sum(1 for _m, s in elite if s == "free"),
            "elite_below300": elite_low,
        })
        print(f"[{y}] " + " ".join(f"{k_}={v}" for k_, v in per_year[-1].items()
                                   if k_ != "year"), flush=True)

    g_same = random.getstate() == g_before
    exp = [h for h in hires_all if h[1] != "rookie" and h[2]]
    gaps = [h[2] - h[3] for h in exp]
    buckets = collections.Counter()
    for g in gaps:
        b = "<=-15" if g <= -15 else "-15~-5" if g < -5 else "-5~+5" if g <= 5 \
            else "+5~+15" if g < 15 else ">=+15"
        buckets[b] += 1
    corr = statistics.correlation([h[2] for h in exp], [h[3] for h in exp]) \
        if len(exp) > 2 else None
    top50_hires = [h for h in exp if h[6] <= 50]
    nat_ok = [h for h in nat_hires if h[2]]
    xp_rows = [tuple(r) for r in conn.execute(
        """SELECT m.birth_year, r.age, r.retirement_year, m.nationality, r.nationality
           FROM managers m JOIN ai_players_retired r ON r.id = m.from_player_id""")]
    dup = conn.execute("SELECT COUNT(*) FROM (SELECT from_player_id FROM managers "
                       "WHERE from_player_id IS NOT NULL GROUP BY from_player_id "
                       "HAVING COUNT(*)>1)").fetchone()[0]
    summary = {
        "xp_total": len(xp_rows),
        "xp_birth_mismatch": sum(1 for b, a, ry, _n1, _n2 in xp_rows if b != ry - a),
        "xp_nat_mismatch": sum(1 for _b, _a, _ry, n1, n2 in xp_rows if n1 != n2),
        "xp_dup_player": dup,
        "fit": args.fit, "years": args.years, "global_random_unchanged": g_same,
        "hash": _hash(conn),
        "club_exp_hires": len(exp),
        "gap_mean": round(statistics.mean(gaps), 3) if gaps else None,
        "gap_abs_mean": round(statistics.mean(abs(g) for g in gaps), 3) if gaps else None,
        "gap_buckets": dict(buckets),
        "corr_ability_req": round(corr, 4) if corr is not None else None,
        "top50_hires": len(top50_hires),
        "top50_hire_ab_mean": round(statistics.mean(h[2] for h in top50_hires), 2)
        if top50_hires else None,
        "top50_hire_rep_mean": round(statistics.mean(h[4] for h in top50_hires), 2)
        if top50_hires else None,
        "top50_hire_jump_mean": round(statistics.mean(h[7] for h in top50_hires), 2)
        if top50_hires else None,
        "hire_jump_gt5": sum(1 for h in exp if h[7] > 5),
        "hire_jump_gt10": sum(1 for h in exp if h[7] > 10),
        "hire_ab_ge_req10": sum(1 for h in exp if h[2] - h[3] >= 10),
        "rookie_hires": sum(1 for h in hires_all if h[1] == "rookie"),
        "nat_hires": len(nat_ok),
        "nat_gap_abs_mean": round(statistics.mean(abs(h[2] - h[3]) for h in nat_ok), 3)
        if nat_ok else None,
        "nat_corr": round(statistics.correlation([h[2] for h in nat_ok],
                                                  [h[3] for h in nat_ok]), 4)
        if len(nat_ok) > 2 else None,
        "per_year": per_year,
    }
    print(json.dumps({k_: v for k_, v in summary.items() if k_ != "per_year"},
                     ensure_ascii=False))
    if args.out:
        json.dump(summary, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    conn.close()
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()