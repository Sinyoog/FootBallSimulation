"""manager_market_qa.py — 감독 직업 시장(③-b) 검증 (2026-09 신설)

신민용 지적: "③-b 적용 후에는 단순히 감독 총원이 늘어나는지를 보는 게
아니라, 시즌별 신규 감독 생성 수 / 재취업 수 / 은퇴 수 / 무직 풀 규모 /
평균 실직 기간 / 감독별 팀 경험 수를 측정해서 효과를 확인하는 게 좋습니다."
그대로 잰다.

  A. 이동 사유가 네 갈래로 나뉘는가
     경질 / 계약 종료 / 자발적 이직 / 재계약 — ③까지는 경질 하나뿐이었다.
  B. 감독이 **여러 팀**을 거치는가
     ③ 실측에서 13,320명 전원이 재임 이력 1건이었다(두 번째 팀 0명).
     이게 ③-b의 존재 이유라, 여기가 안 되면 나머지는 의미가 없다.
  C. 무직 풀이 순환하는가
     쌓이기만 하지 않고 재취업·은퇴로 빠져야 한다.
  D. 감독 풀이 발산하지 않는가
     ③에서는 매 시즌 신규 생성만 해서 총원이 단조 증가했다.
  E. 매칭이 말이 되는가
     신민용: "너무 어울리지 않는 이직만 방지." 하위 리그 경력 감독이
     5대 리그 상위팀에 가는 일이 없어야 한다.
  F. 결정성 — 전역 random을 건드리지 않는가

세이브에 치른 경기가 없으면 전력 기반 가상 결과를 주입해서 순위를 만든다
(manager_turnover_qa와 같은 방식, 원본은 안 건드림).

사용법:
    python tools/manager_market_qa.py --db <세이브.db> [--years 10]
"""
import _path  # noqa: F401
import argparse
import collections
import hashlib
import os
import random
import shutil
import statistics
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def _fill_results(conn, season, rng, n_leagues=60):
    ovr = {r[0]: r[1] for r in conn.execute(
        "SELECT team_id, AVG(ovr) FROM ai_players GROUP BY team_id")}
    lids = [r[0] for r in conn.execute(
        "SELECT DISTINCT league_id FROM match_results WHERE season=? ORDER BY league_id LIMIT ?",
        (season, n_leagues))]
    ups = []
    for r in conn.execute(
            """SELECT id, home_team_id, away_team_id FROM match_results
               WHERE season=? AND league_id IN (%s)""" % ",".join("?" * len(lids)),
            (season, *lids)):
        edge = (ovr.get(r[1], 50.0) - ovr.get(r[2], 50.0)) / 8.0
        ups.append((max(0, int(round(rng.gauss(1.35 + edge * 0.35, 1.1)))),
                    max(0, int(round(rng.gauss(1.15 - edge * 0.35, 1.1)))), r[0]))
    conn.executemany(
        "UPDATE match_results SET home_score=?, away_score=? WHERE id=?", ups)
    conn.commit()
    return len(ups), len(lids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--work", default=None)
    args = ap.parse_args()
    work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="market_qa_"))
    os.makedirs(work, exist_ok=True)

    import database as d
    db = os.path.join(work, "market.db")
    shutil.copy(os.path.abspath(args.db), db)
    hsrc = os.path.splitext(os.path.abspath(args.db))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")
    d.USE_MEMORY_DB = False
    d.DB_PATH = db
    os.chdir(work)
    d.reset_conn_pool()
    d.init_db()

    import ai_lifecycle as al
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    season = conn.execute("SELECT MAX(season) FROM match_results").fetchone()[0]
    year0 = conn.execute("SELECT MAX(year) FROM match_results").fetchone()[0]
    n_rows, n_lg = _fill_results(conn, season, random.Random(4321))
    print(f"  준비: {n_lg}개 리그 {n_rows}경기에 전력 기반 가상 결과 주입")

    problems = []
    hist = []

    # ── F. 결정성 ────────────────────────────────────────────────
    random.seed(24680)
    _before = random.getstate()
    al._manager_turnover(c, year0, season)
    conn.commit()
    if random.getstate() != _before:
        problems.append("F: _manager_turnover가 전역 random 상태를 바꿨음")
    hist.append((year0, dict(al._LAST_MANAGER_MARKET)))
    print(f"  F. 전역 random 상태 변화: "
          f"{'없음' if random.getstate() == _before else '있음 ← 실패'}")

    # ── G. 재현성 — 같은 DB 상태에서 두 번 돌리면 같은 결과 ──────
    # F(전역 random 불간섭)만으로는 부족하다. 시장 내부가 dict 순회 순서나
    # 미시드 난수에 의존하면 같은 세이브에서도 결과가 갈린다. 상태를
    # 되돌려 두 번 돌린 뒤 감독 표를 해시해 비교한다.
    def _mgr_hash():
        h = hashlib.blake2b(digest_size=16)
        for row in conn.execute(
                "SELECT id, birth_year, contract_until, status, jobless_since, "
                "career_best_level, clubs_managed, retired FROM managers ORDER BY id"):
            h.update(repr(tuple(row)).encode())
        for row in conn.execute(
                "SELECT team_id, manager_id, start_year, end_year, end_reason "
                "FROM team_managers ORDER BY id"):
            h.update(repr(tuple(row)).encode())
        return h.hexdigest()

    h1 = _mgr_hash()
    snap = os.path.join(work, "snap.db")
    conn.commit()
    # WAL 모드라 파일만 복사하면 -wal 안의 최신 내용이 빠진다. 체크포인트로
    # 본체에 밀어넣고, 복원할 때는 남은 -wal/-shm을 지운다.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(db, snap)
    y1 = year0 + 1
    _fill_results(conn, season, random.Random(4321 + 1))
    al._manager_turnover(c, y1, season)
    conn.commit()
    hA, sA = _mgr_hash(), dict(al._LAST_MANAGER_MARKET)
    # 되돌리고 같은 조건으로 재실행
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    d.reset_conn_pool()
    shutil.copy(snap, db)
    for _sfx in ("-wal", "-shm"):
        if os.path.exists(db + _sfx):
            os.remove(db + _sfx)
    conn = get_conn()
    c = conn.cursor()
    assert _mgr_hash() == h1, "스냅샷 복원 실패 — G 판정 불가"
    _fill_results(conn, season, random.Random(4321 + 1))
    al._manager_turnover(c, y1, season)
    conn.commit()
    hB, sB = _mgr_hash(), dict(al._LAST_MANAGER_MARKET)
    os.remove(snap)
    print(f"  G. 같은 상태 2회 실행 해시 {'일치' if hA == hB else '불일치 ← 실패'}"
          f" ({hA[:12]} / {hB[:12]})")
    if hA != hB or sA != sB:
        problems.append("G: 같은 DB 상태에서 감독 시장 결과가 재현되지 않음")
    hist.append((y1, sB))

    for k in range(2, args.years):
        y = year0 + k
        _fill_results(conn, season, random.Random(4321 + k))
        al._manager_turnover(c, y, season)
        conn.commit()
        hist.append((y, dict(al._LAST_MANAGER_MARKET)))

    # ── A. 이동 사유 분포 ────────────────────────────────────────
    print()
    print("  A. 시즌별 시장 (경질 / 계약종료 / 이직 / 재계약 / 재취업 / 신인 / 은퇴)")
    for y, st in hist:
        print(f"     {y}  경질 {st.get('sacked',0):>4} · 계약종료 {st.get('contract_end',0):>4}"
              f" · 이직 {st.get('moved',0):>4} · 재계약 {st.get('renewed',0):>4}"
              f" · 재취업 {st.get('rehired',0):>4} · 신인 {st.get('rookie',0):>4}"
              f" · 은퇴 {st.get('retired_age',0)+st.get('retired_jobless',0):>4}"
              f"  (끌어올림 {st.get('stretched',0):>3})")
    tot = collections.Counter()
    for _y, st in hist:
        tot.update(st)
    for key, label in (("sacked", "경질"), ("contract_end", "계약 종료"),
                       ("moved", "자발적 이직"), ("renewed", "재계약")):
        if not tot.get(key):
            problems.append(f"A: '{label}'가 {args.years}시즌 동안 0건 — 갈래가 안 나뉨")

    # ── B. 여러 팀을 거치는가 ────────────────────────────────────
    dist = collections.Counter()
    for r in conn.execute(
            "SELECT n, COUNT(*) c FROM (SELECT manager_id, COUNT(*) n "
            "FROM team_managers GROUP BY manager_id) GROUP BY n ORDER BY n"):
        dist[r[0]] = r[1]
    multi = sum(v for k, v in dist.items() if k >= 2)
    print()
    print("  B. 감독별 재임 팀 수: " + ", ".join(f"{k}팀 {v:,}명" for k, v in sorted(dist.items())))
    if multi == 0:
        problems.append("B: 두 팀 이상 맡아본 감독이 0명 — ③-b의 핵심이 작동 안 함")
    else:
        print(f"     → 두 팀 이상 경험 {multi:,}명")

    # ── C. 무직 풀 순환 ──────────────────────────────────────────
    free = conn.execute(
        "SELECT COUNT(*) FROM managers WHERE retired=0 AND status='free'").fetchone()[0]
    retired = conn.execute("SELECT COUNT(*) FROM managers WHERE retired=1").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM managers").fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM team_managers WHERE end_year IS NULL").fetchone()[0]
    jl = [r[0] for r in conn.execute(
        "SELECT ? - jobless_since FROM managers WHERE retired=0 AND status='free' "
        "AND jobless_since IS NOT NULL", (year0 + args.years - 1,))]
    print()
    print(f"  C. 감독 총원 {total:,} = 재직 {active:,} + 무직 {free:,} + 은퇴 {retired:,}")
    if jl:
        print(f"     평균 실직 기간 {statistics.mean(jl):.2f}시즌 / 최대 {max(jl)}시즌")
    if retired == 0:
        problems.append("C: 은퇴한 감독이 0명 — 풀이 빠져나가지 않는다")

    # ── D. 총원 발산 여부 ────────────────────────────────────────
    n_teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    ratio = total / max(1, n_teams)
    print(f"  D. 팀 {n_teams:,}개 대비 감독 총원 배수 {ratio:.2f}× "
          f"(신인 누적 {tot.get('rookie',0):,}명)")
    if ratio > 2.5:
        problems.append(f"D: 감독 총원이 팀 수의 {ratio:.2f}배 — 풀이 발산한다")

    # ── E. 매칭 타당성 ───────────────────────────────────────────
    # career_best_level은 **부임 시점에 그 자리 수준으로 올려 박는** 값이라
    # (MAX(기존, 자리수준)), 재직자의 현재 값과 자리를 비교하면 신인이 어떤
    # 자리에 앉았든 언제나 통과한다 — 첫 실행에서 실제로 5대 리그 1부 데뷔
    # 45명을 놓쳤다. 그래서 **데뷔 자리 수준**으로 본다.
    from constants import (get_country_league_grade, MANAGER_ROOKIE_MAX_LEVEL,
                           MANAGER_HIRE_MAX_UNDERQUALIFIED,
                           MANAGER_HIRE_STRETCH_EXTRA)
    from data.prestige_clubs import prestige_level
    lvl = {}
    for r in conn.execute(
            """SELECT t.id, t.name, cn.name AS country, lg.tier AS tier FROM teams t
               LEFT JOIN countries cn ON cn.id = t.country_id
               LEFT JOIN leagues lg ON lg.id = t.league_id"""):
        cty = r["country"] or ""
        lvl[r["id"]] = al._manager_job_level(
            get_country_league_grade(cty), r["tier"],
            prestige_level(cty, r["name"]) if cty else 0)

    debut = collections.Counter()
    over = 0
    for r in conn.execute(
            """SELECT tm.team_id AS tid, tm.start_year AS sy FROM team_managers tm
               JOIN (SELECT manager_id, MIN(start_year) AS f FROM team_managers
                     GROUP BY manager_id) x
                 ON x.manager_id = tm.manager_id AND x.f = tm.start_year
               WHERE tm.start_year > ?""", (year0,)):
        L = lvl.get(r["tid"], 0.0)
        debut[int(L // 20) * 20] += 1
        if L > MANAGER_ROOKIE_MAX_LEVEL:
            over += 1
    n_debut = sum(debut.values())
    print()
    print(f"  E. 시장에서 데뷔한 감독 {n_debut:,}명의 데뷔 자리 수준")
    for k in sorted(debut):
        print(f"       {k:>3}~{k+19:<3}: {debut[k]:>6,}명")
    print(f"     신인 데뷔 상한({MANAGER_ROOKIE_MAX_LEVEL:.0f}) 초과 자리 데뷔 {over}명 "
          f"/ 끌어올림 부임 누적 {tot.get('stretched',0):,}건")
    if over:
        problems.append(f"E: 신인이 상한을 넘는 자리에 데뷔한 사례 {over}명")

    # 재직자 중 경력이 자리보다 과하게 모자란 배치 — 끌어올림 폭까지만 허용.
    cap = MANAGER_HIRE_MAX_UNDERQUALIFIED + MANAGER_HIRE_STRETCH_EXTRA
    bad = conn.execute(
        """SELECT COUNT(*) FROM team_managers tm JOIN managers m ON m.id=tm.manager_id
           WHERE tm.end_year IS NULL""").fetchone()[0]
    checked, bad = bad, 0
    for r in conn.execute(
            """SELECT tm.team_id AS tid, m.career_best_level AS cl
               FROM team_managers tm JOIN managers m ON m.id = tm.manager_id
               WHERE tm.end_year IS NULL"""):
        if lvl.get(r["tid"], 0.0) - (r["cl"] or 0) > cap + 0.5:
            bad += 1
    print(f"     재직 {checked:,}건 중 경력이 자리보다 {cap:.0f} 넘게 낮은 배치 {bad}건")
    if bad:
        problems.append(f"E: 경력 대비 과한 자리에 앉은 감독 {bad}건")
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