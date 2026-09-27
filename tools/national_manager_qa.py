"""national_manager_qa.py — 대표팀 감독 시장(③-d) 검증 (2026-09 신설)

신민용 지적("③-b에서 EPL=10 버그가 상대 비교 테스트를 통과했다")을 받아
**절대 기준값 검사**를 A에 넣었다. 상대 순서만 보면 모든 판단이 일관되게
틀린 버그를 못 잡는다.

  A. 절대 기준값 — 주요 리그/구단/대표팀 자리 수준이 정해진 범위 안인가
     (EPL=10 같은 폴백 버그 재발 방지)
  B. 목표 산정 — 스쿼드 OVR 백분위 → 목표 단계. 신민용 기준점(한국 16강).
  C. 목표 고정 — 대회 도중 선수 OVR이 변해도 목표가 안 바뀐다.
  D. 단계 판정 — 우승/준우승/3위/4위/8강/16강/조별리그가 정확한가.
  E. 시장 흐름 — 재계약/계약종료/경질/부임/임시가 모두 발생하는가.
     특히 경질이 0이면 실패다(계약 기간이 대회 주기보다 짧으면 구조적으로
     경질이 불가능해진다 — 실측으로 한 번 겪었다).
  F. 배타성 — 한 감독이 두 자리를 동시에 갖지 않는가.
     · 대표팀 두 곳 동시 재직 0
     · 대표팀 + 클럽 동시 재직 0
     · 현직 대표팀 감독의 status는 전부 'nation'
     · 감독 없는 대표팀 0개국
  G. 약팀 초과 달성 — 같은 성적이면 약팀 감독이 더 크게 오르는가.
     그리고 대륙컵이 월드컵보다 크게 움직이지 않는가(양방향).
  H. 양방향 이동 — 클럽 → 대표팀 → 클럽이 실제로 일어나는가.
  I. 결정성 — 전역 random 불간섭 + 같은 상태 재실행 재현성.

사용법:
    python tools/national_manager_qa.py --db <세이브.db> [--cycles 4]
"""
import _path  # noqa: F401
import argparse
import hashlib
import os
import random
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def _mk_tournament(conn, c, nm, k, pct, year, teams, kind="world", continent="",
                   seed=0):
    """가상 대회 하나를 만들고 대진까지 채운 뒤 tid를 돌려준다."""
    c.execute("INSERT INTO intl_tournaments(year,kind,name,status,continent) "
              "VALUES(?,?,?,'ko',?)", (year, kind, f"QA {year} {kind}", continent))
    tid = c.execute("SELECT MAX(id) FROM intl_tournaments").fetchone()[0]
    for cn in teams:
        c.execute("INSERT INTO intl_entries(tournament_id,country,flag,grade,ovr,alive) "
                  "VALUES(?,?,'',?,?,1)",
                  (tid, cn, k.get_country_grade(cn), pct.get(cn, (0, 1))[0]))
    conn.commit()
    nm.fix_national_objectives(tid, conn=conn)
    rng = random.Random(seed)
    rnd = list(teams[:16])
    rng.shuffle(rnd)

    def add(stage, h, a, hs, as_):
        c.execute("INSERT INTO intl_matches(tournament_id,stage,week,home,away,"
                  "home_score,away_score,day) VALUES(?,?,49,?,?,?,?,0)",
                  (tid, stage, h, a, hs, as_))

    for st in ("R16", "QF", "SF"):
        nxt = []
        for i in range(0, len(rnd), 2):
            w = rnd[i] if rng.random() < 0.5 else rnd[i + 1]
            lo = rnd[i + 1] if w == rnd[i] else rnd[i]
            add(st, w, lo, 2, 0)
            nxt.append(w)
        rnd = nxt
    add("F", rnd[0], rnd[1], 2, 1)
    add("TP", teams[14], teams[15], 1, 0)
    c.execute("UPDATE intl_tournaments SET status='done', winner=? WHERE id=?",
              (rnd[0], tid))
    conn.commit()
    return tid


def _run_club_market(conn, c, year):
    """클럽 감독 시장을 한 번 돌린다 — 대표팀에서 물러난 감독이 클럽으로
    재진입하는지 확인하기 위한 것. 리그 결과가 없으면 전력 기반 가상
    결과를 주입한다(manager_market_qa와 같은 방식)."""
    import ai_lifecycle as al
    season = conn.execute("SELECT MAX(season) FROM match_results").fetchone()[0]
    if season is None:
        return
    ovr = {r[0]: r[1] for r in conn.execute(
        "SELECT team_id, AVG(ovr) FROM ai_players GROUP BY team_id")}
    lids = [r[0] for r in conn.execute(
        "SELECT DISTINCT league_id FROM match_results WHERE season=? "
        "ORDER BY league_id LIMIT 60", (season,))]
    if not lids:
        return
    rng = random.Random(year)
    ups = []
    for r in conn.execute(
            "SELECT id, home_team_id, away_team_id FROM match_results "
            "WHERE season=? AND league_id IN (%s)" % ",".join("?" * len(lids)),
            (season, *lids)):
        e = (ovr.get(r[1], 50.0) - ovr.get(r[2], 50.0)) / 8.0
        ups.append((max(0, int(round(rng.gauss(1.35 + e * 0.35, 1.1)))),
                    max(0, int(round(rng.gauss(1.15 - e * 0.35, 1.1)))), r[0]))
    conn.executemany(
        "UPDATE match_results SET home_score=?, away_score=? WHERE id=?", ups)
    conn.commit()
    al._manager_turnover(c, year, season)
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--cycles", type=int, default=4, help="치를 메이저 대회 수")
    ap.add_argument("--work", default=None)
    args = ap.parse_args()
    work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="nat_qa_"))
    os.makedirs(work, exist_ok=True)
    problems = []

    import constants as k
    from data.prestige_clubs import prestige_level

    # ── A. 절대 기준값 ───────────────────────────────────────────
    # 신민용: "앞으로도 감독 시장 QA에는 절대 기준값 검사가 하나 있어야 해.
    # 주요 대표적인 구단/리그의 실제 계산값 범위를 고정해서 검증하는 방식."
    def clv(country, tier=1, team=""):
        return k.manager_job_level(k.get_country_league_grade(country), tier,
                                   prestige_level(country, team) if team else 0)

    checks = [
        ("맨체스터 시티",      clv("잉글랜드", 1, "맨체스터 시티"), 118, 130),
        ("EPL 1부(무위상)",    clv("잉글랜드", 1),                 100, 112),
        ("라리가 1부(무위상)", clv("스페인", 1),                    95, 105),
        ("포르투갈 1부",       clv("포르투갈", 1),                  70,  85),
        ("챔피언십(잉글 2부)", clv("잉글랜드", 2),                  60,  75),
        ("잉글랜드 4부",       clv("잉글랜드", 4),                   25,  40),
        ("잉글랜드 7부",       clv("잉글랜드", 7),                   15,  30),
        ("S급 대표팀",  k.national_job_level("S"),                 100, 112),
        ("B급 대표팀",  k.national_job_level("B"),                  70,  82),
        ("F급 대표팀",  k.national_job_level("F"),                  38,  50),
    ]
    print("  A. 절대 기준값 (범위 고정 — EPL=10 같은 폴백 버그 재발 방지)")
    for name, got, lo, hi in checks:
        ok = lo <= got <= hi
        print(f"     [{'OK ' if ok else 'FAIL'}] {name:<20} {got:6.1f}  "
              f"(허용 {lo}~{hi})")
        if not ok:
            problems.append(f"A: {name} 자리 수준 {got:.1f} — 허용 범위 "
                            f"{lo}~{hi} 밖")

    # ── 세이브 준비 ──────────────────────────────────────────────
    import database as d
    db = os.path.join(work, "nat.db")
    shutil.copy(os.path.abspath(args.db), db)
    hsrc = os.path.splitext(os.path.abspath(args.db))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")
    d.USE_MEMORY_DB = False
    d.DB_PATH = db
    os.chdir(work)
    d.reset_conn_pool()
    d.init_db()
    miss = d.verify_national_manager_tables(d.get_conn())
    if miss:
        problems.append("스키마: ③-d 표/컬럼 누락 "
                        + ", ".join(f"{t}.{c}" for t, c in miss))

    import national_manager as nm
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    n_seed = conn.execute(
        "SELECT COUNT(*) FROM national_team_managers WHERE end_year IS NULL"
    ).fetchone()[0]
    n_country = conn.execute("SELECT COUNT(*) FROM countries").fetchone()[0]
    print(f"\n     대표팀 감독 시드 {n_seed}/{n_country}개국")
    if n_seed < n_country:
        problems.append(f"시드: {n_country - n_seed}개국에 대표팀 감독이 없다")

    pct = nm.world_squad_percentiles(conn)
    srt = [cn for cn, _ in sorted(pct.items(), key=lambda x: x[1][1])]

    # ── B. 목표 산정 ─────────────────────────────────────────────
    print("\n  B. 목표 산정 (스쿼드 OVR 백분위 → 목표)")
    anchors = [("대한민국", "16강"), ("독일", "우승")]
    for cn in ["독일", "브라질", "일본", "사우디아라비아", "대한민국", "태국", "중국"]:
        if cn not in pct:
            continue
        ovr, wp = pct[cn]
        name, rank = k.national_objective_for(wp)
        print(f"     {cn:<10} OVR {ovr:5.1f} 백분위 {wp:.3f} → {name} (rank {rank})")
    for cn, want in anchors:
        if cn in pct:
            got = k.national_objective_for(pct[cn][1])[0]
            if got != want:
                problems.append(f"B: {cn} 목표가 '{got}' — 기준점은 '{want}'")

    # ── C/D. 목표 고정 + 단계 판정 ───────────────────────────────
    year0 = 2000
    tid = _mk_tournament(conn, c, nm, k, pct, year0, srt[:32], seed=1)
    before = conn.execute(
        "SELECT objective, objective_rank FROM national_objectives "
        "WHERE tournament_id=? AND country=?", (tid, srt[0])).fetchone()
    c.execute("UPDATE ai_players SET ovr=MIN(99, ovr+12) WHERE true_nationality=?",
              (srt[31],))
    conn.commit()
    nm.fix_national_objectives(tid, conn=conn)       # 멱등이어야 한다
    after = conn.execute(
        "SELECT objective, objective_rank FROM national_objectives "
        "WHERE tournament_id=? AND country=?", (tid, srt[0])).fetchone()
    same = tuple(before) == tuple(after)
    n_obj = conn.execute(
        "SELECT COUNT(*) FROM national_objectives WHERE tournament_id=?",
        (tid,)).fetchone()[0]
    print(f"\n  C. 목표 고정 — 대회 중 OVR 변경 후에도 불변 {same} / 행 {n_obj}개 (중복 없음)")
    if not same:
        problems.append("C: 대회 도중 목표가 바뀌었다 — 고정 실패")
    if n_obj != 32:
        problems.append(f"C: 목표 행이 {n_obj}개 — 참가국 32개와 다르다(멱등성 실패)")

    stages = nm.country_stage_results(conn, tid)
    winner = conn.execute("SELECT winner FROM intl_tournaments WHERE id=?",
                          (tid,)).fetchone()["winner"]
    keys = {}
    for cn, (key, rank) in stages.items():
        keys[key] = keys.get(key, 0) + 1
    print(f"  D. 단계 판정 분포: {keys}")
    if stages.get(winner, ("", 0))[0] != "우승":
        problems.append("D: 우승국이 '우승'으로 판정되지 않았다")
    for need in ("우승", "준우승", "8강", "16강"):
        if not keys.get(need):
            problems.append(f"D: '{need}' 판정이 0건 — 단계 판정이 빠졌다")

    # ── I. 결정성 ────────────────────────────────────────────────
    random.seed(13579)
    _before = random.getstate()

    def _hash():
        h = hashlib.blake2b(digest_size=16)
        for row in conn.execute(
                "SELECT country_id, manager_id, start_year, end_year, end_reason, "
                "role, contract_until, tournaments, best_rank "
                "FROM national_team_managers ORDER BY id"):
            h.update(repr(tuple(row)).encode())
        for row in conn.execute(
                "SELECT id, reputation, recent_perf, titles_intl, status "
                "FROM managers WHERE status='nation' OR titles_intl>0 ORDER BY id"):
            h.update(repr(tuple(row)).encode())
        return h.hexdigest()

    snap = os.path.join(work, "snap.db")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(db, snap)
    nm.evaluate_national_tournament(tid, conn=conn)
    conn.commit()
    hA = _hash()
    st1 = dict(nm.LAST_NATIONAL_MARKET)
    if random.getstate() != _before:
        problems.append("I: evaluate_national_tournament가 전역 random을 건드렸다")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    d.reset_conn_pool()
    shutil.copy(snap, db)
    for sfx in ("-wal", "-shm"):
        if os.path.exists(db + sfx):
            os.remove(db + sfx)
    conn = get_conn()
    c = conn.cursor()
    nm.evaluate_national_tournament(tid, conn=conn)
    conn.commit()
    hB = _hash()
    os.remove(snap)
    print(f"  I. 전역 random 불간섭 {'OK' if random.getstate() == _before else 'FAIL'}"
          f" / 재실행 해시 {'일치' if hA == hB else '불일치 ← 실패'}")
    if hA != hB:
        problems.append("I: 같은 상태에서 대표팀 평가가 재현되지 않음")

    # ── E. 시장 흐름 (여러 대회 주기) ────────────────────────────
    print("\n  E. 시장 흐름 (평가 / 재계약 / 계약종료 / 경질 / 부임 / 임시 / 클럽서영입)")
    totals = {}
    hist = [(year0, st1)]
    for i in range(1, max(1, args.cycles)):
        y = year0 + 4 * i
        kind = "world" if i % 2 == 0 else "continent"
        cont = "" if kind == "world" else (conn.execute(
            "SELECT continent FROM countries WHERE name=?", (srt[0],)
        ).fetchone()["continent"] or "")
        pool = srt[:32] if kind == "world" else [
            r["name"] for r in conn.execute(
                "SELECT name FROM countries WHERE continent=? LIMIT 32", (cont,))]
        if len(pool) < 16:
            pool = srt[:32]
            kind, cont = "world", ""
        t2 = _mk_tournament(conn, c, nm, k, pct, y, pool, kind=kind,
                            continent=cont, seed=100 + i)
        nm.evaluate_national_tournament(t2, conn=conn)
        hist.append((y, dict(nm.LAST_NATIONAL_MARKET)))
        # 대표팀에서 물러난 감독이 실제로 클럽 시장에 재진입하는지 보려면
        # 클럽 시장도 같이 돌려야 한다 — 신민용 설계의 핵심 연결부
        # ("대표팀에서 잘하면 다시 좋은 클럽으로")라 QA에서 실제로 굴린다.
        _run_club_market(conn, c, y)
    for y, st in hist:
        print(f"     {y}  평가{st.get('evaluated',0):>4} · 재계약{st.get('renewed',0):>4}"
              f" · 계약종료{st.get('contract_end',0):>4} · 경질{st.get('sacked',0):>4}"
              f" · 부임{st.get('appointed',0):>4} · 임시{st.get('caretaker',0):>3}"
              f" · 클럽서영입{st.get('promoted',0):>3}")
        for key, v in st.items():
            totals[key] = totals.get(key, 0) + (v if isinstance(v, int) else 0)
    for key, label in (("renewed", "재계약"), ("contract_end", "계약 종료"),
                       ("sacked", "경질"), ("appointed", "부임")):
        if not totals.get(key):
            problems.append(f"E: '{label}'가 {args.cycles}개 대회 동안 0건 — "
                            f"흐름이 안 나뉜다")

    # ── F. 배타성 ────────────────────────────────────────────────
    dup = conn.execute(
        "SELECT COUNT(*) FROM (SELECT manager_id FROM national_team_managers "
        "WHERE end_year IS NULL GROUP BY manager_id HAVING COUNT(*)>1)").fetchone()[0]
    both = conn.execute(
        """SELECT COUNT(*) FROM national_team_managers n
           JOIN team_managers t ON t.manager_id = n.manager_id AND t.end_year IS NULL
           WHERE n.end_year IS NULL""").fetchone()[0]
    wrong = conn.execute(
        """SELECT COUNT(*) FROM managers m
           JOIN national_team_managers n ON n.manager_id = m.id AND n.end_year IS NULL
           WHERE m.status != 'nation'""").fetchone()[0]
    empty = conn.execute(
        """SELECT COUNT(*) FROM countries c WHERE NOT EXISTS
           (SELECT 1 FROM national_team_managers n
            WHERE n.country_id = c.id AND n.end_year IS NULL)""").fetchone()[0]
    print(f"\n  F. 배타성 — 대표팀 중복 {dup} · 대표팀+클럽 동시 {both} · "
          f"status 불일치 {wrong} · 감독 없는 대표팀 {empty}개국")
    if dup:
        problems.append(f"F: 한 감독이 두 대표팀을 동시에 맡았다 {dup}건")
    if both:
        problems.append(f"F: 대표팀과 클럽을 동시에 맡은 감독 {both}건")
    if wrong:
        problems.append(f"F: 현직 대표팀 감독인데 status!='nation' {wrong}건")
    if empty:
        problems.append(f"F: 감독 없는 대표팀 {empty}개국")

    # ── G. 약팀 초과 달성 + 대회 비중 ────────────────────────────
    strong = k.national_result_rep("4강", 60, 60, 0.05, True)
    weak = k.national_result_rep("4강", 40, 60, 0.45, True)
    wc = k.national_result_rep("조별리그", 100, 20, 0.02, True)
    cc = k.national_result_rep("조별리그", 100, 20, 0.02, False)
    print(f"  G. 같은 '4강' — 강팀(목표4강) {strong:+.1f} vs 약팀(목표16강) {weak:+.1f}")
    print(f"     실패 폭 — 월드컵 {wc:+.1f} vs 대륙컵 {cc:+.1f}")
    if weak <= strong:
        problems.append(f"G: 약팀 초과 달성({weak:+.1f})이 강팀 목표달성"
                        f"({strong:+.1f})보다 크지 않다")
    if abs(cc) > abs(wc):
        problems.append(f"G: 대륙컵 실패({cc:+.1f})가 월드컵 실패({wc:+.1f})보다 "
                        f"크다 — 대회 비중이 역전됐다")

    # ── H. 양방향 이동 ───────────────────────────────────────────
    club_to_nat = totals.get("promoted", 0)
    nat_then_club = conn.execute(
        """SELECT COUNT(*) FROM (
             SELECT n.manager_id FROM national_team_managers n
             JOIN team_managers t ON t.manager_id = n.manager_id
             WHERE n.end_year IS NOT NULL AND t.start_year >= n.end_year
             GROUP BY n.manager_id)""").fetchone()[0]
    ended = conn.execute(
        "SELECT COUNT(*) FROM national_team_managers WHERE end_year IS NOT NULL"
    ).fetchone()[0]
    free_after = conn.execute(
        """SELECT COUNT(*) FROM managers m WHERE m.status='free' AND EXISTS
           (SELECT 1 FROM national_team_managers n
            WHERE n.manager_id=m.id AND n.end_year IS NOT NULL)""").fetchone()[0]
    print(f"  H. 양방향 — 클럽→대표팀 {club_to_nat}건 / 대표팀 퇴임 {ended}건 "
          f"(그중 무직 전환 {free_after}명) / 퇴임 후 클럽 부임 {nat_then_club}건")
    if not club_to_nat:
        problems.append("H: 클럽 감독이 대표팀으로 간 사례가 0건")
    if ended and not free_after and not nat_then_club:
        problems.append("H: 대표팀에서 퇴임한 감독이 무직도 클럽 부임도 안 됐다 "
                        "— 시장 재진입이 막혀 있다")
    if ended and not nat_then_club:
        problems.append("H: 대표팀 퇴임 후 클럽에 부임한 감독이 0건 — "
                        "대표팀↔클럽 왕복이 닫혀 있다")

    # ── J. 임시 감독 경로 ────────────────────────────────────────
    # 평상시엔 정식 부임이 성공해서 임시 감독이 안 걸린다(실측 0건). 폴백이
    # 실제로 동작하는지는 강제로 태워서 확인한다 — 안 그러면 한 번도 안
    # 돌아본 코드가 남는다(③-b의 끌어올림 패스에서 같은 교훈을 얻었다).
    cid = conn.execute("SELECT id FROM countries LIMIT 1").fetchone()["id"]
    conn.execute("UPDATE national_team_managers SET end_year=?, end_reason='sacked' "
                 "WHERE country_id=? AND end_year IS NULL", (year0 + 90, cid))
    # 후보가 아무도 없도록 전원을 대표팀 현직으로 위장 → 정식 부임 실패 유도
    conn.execute("UPDATE managers SET status='nation' WHERE retired=0")
    conn.commit()
    st = {}
    ok_ct = nm._fill_vacancy(conn, cid, year0 + 90, "qa-salt", st)
    conn.commit()
    got = conn.execute(
        "SELECT role FROM national_team_managers WHERE country_id=? "
        "AND end_year IS NULL", (cid,)).fetchone()
    role = got["role"] if got else None
    print(f"  J. 임시 감독 폴백 — 후보 없음 상태에서 부임 {ok_ct} / role={role} "
          f"(신규 생성 {st.get('caretaker_created', 0)}건)")
    if not ok_ct or role != "caretaker":
        problems.append(f"J: 후보가 없을 때 임시 감독이 세워지지 않았다 "
                        f"(부임={ok_ct}, role={role})")
    conn.close()

    print()
    if problems:
        print("=== 실패 ===")
        for x in problems:
            print(f"  {x}")
        return 1
    print("=== 전체 통과 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())