"""manager_reputation_qa.py — 감독 실적/명성 + 상한/하한(③-c) 검증 (2026-09 신설)

③-b가 QA 7종을 전부 통과했는데도 프리미어리그가 자리 수준 10.0(최하위)으로
평가되고 있었다 — get_country_league_grade가 잉글랜드에만 주는 "SS"가
MANAGER_JOB_LEAGUE_GRADE_LEVEL에 없어서 .get(grade, 10.0) 폴백이 걸렸다.
그래서 여기서는 **자리 수준 자체가 맞는지**부터 본다.

  A. 자리 수준 척도가 말이 되는가 (SS 회귀 방지)
     EPL > 라리가 1부 > 포르투갈 1부 > 챔피언십 > 잉글랜드 4부 > 7부
  B. 신민용이 든 사례 1~5를 그대로 판정
     EPL→7부 차단 / EPL→4부 차단 / EPL→챔피언십 허용 /
     EPL→포르투갈1부 허용 / 국대→아프리카3부 차단 / 국대→국대 허용
  C. 명성이 실적을 구분하는가
     "A급 8시즌 리그우승 2회" vs "A급 1시즌 12위"가 달라야 한다.
  D. 추락 방지가 실제 시장에서 작동하는가
     시즌을 돌린 뒤, 고수준 경력자가 하한 아래 자리에 앉은 사례 0건.
  E. 명성이 오르내리는가 — 우승/목표달성이 쌓인 감독의 명성 분포
  F. 실적이 상한을 움직이는가 (명성 상위 감독이 더 높은 자리로 간다)
  G. 전역 random 불간섭 + 같은 상태 재실행 재현성

사용법:
    python tools/manager_reputation_qa.py --db <세이브.db> [--years 8]
"""
import _path  # noqa: F401
import argparse
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
    """전력 기반 가상 결과 주입 — manager_market_qa와 같은 방식."""
    ovr = {r[0]: r[1] for r in conn.execute(
        "SELECT team_id, AVG(ovr) FROM ai_players GROUP BY team_id")}
    lids = [r[0] for r in conn.execute(
        "SELECT DISTINCT league_id FROM match_results WHERE season=? "
        "ORDER BY league_id LIMIT ?", (season, n_leagues))]
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
    ap.add_argument("--years", type=int, default=8)
    ap.add_argument("--work", default=None)
    args = ap.parse_args()
    work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="rep_qa_"))
    os.makedirs(work, exist_ok=True)
    problems = []

    from constants import (get_country_league_grade, get_country_grade,
                          manager_job_level, national_job_level,
                          manager_career_floor, manager_max_level,
                          manager_reputation,
                          MANAGER_JOB_LEAGUE_GRADE_LEVEL)
    from data.prestige_clubs import prestige_level

    # ── A. 자리 수준 척도 (SS 누락 회귀 방지) ────────────────────
    def clv(country, tier=1, team=""):
        return manager_job_level(get_country_league_grade(country), tier,
                                 prestige_level(country, team) if team else 0)

    EPL = clv("잉글랜드", 1)
    MCI = clv("잉글랜드", 1, "맨체스터 시티")
    LALIGA = clv("스페인", 1)
    POR = clv("포르투갈", 1)
    CHAMP = clv("잉글랜드", 2)
    ENG4 = clv("잉글랜드", 4)
    ENG7 = clv("잉글랜드", 7)
    print("  A. 자리 수준 척도")
    for n, v in (("맨시티", MCI), ("EPL 1부", EPL), ("라리가 1부", LALIGA),
                 ("포르투갈 1부", POR), ("챔피언십", CHAMP),
                 ("잉글랜드 4부", ENG4), ("잉글랜드 7부", ENG7)):
        print(f"       {n:<12} {v:6.1f}")
    if "SS" not in MANAGER_JOB_LEAGUE_GRADE_LEVEL:
        problems.append("A: MANAGER_JOB_LEAGUE_GRADE_LEVEL에 'SS'가 없다 "
                        "— 잉글랜드가 폴백(10.0)으로 떨어진다")
    if not (MCI > EPL > LALIGA > POR > CHAMP > ENG4 > ENG7):
        problems.append(f"A: 자리 수준 순서가 깨졌다 "
                        f"(맨시티 {MCI:.0f} > EPL {EPL:.0f} > 라리가 {LALIGA:.0f} "
                        f"> 포르투갈 {POR:.0f} > 챔피언십 {CHAMP:.0f} "
                        f"> 4부 {ENG4:.0f} > 7부 {ENG7:.0f} 이어야 함)")
    if EPL < 60:
        problems.append(f"A: EPL 자리 수준이 {EPL:.1f} — SS 폴백 버그 재발")

    # ── B. 신민용이 든 사례 판정 ─────────────────────────────────
    KOR = national_job_level(get_country_grade("대한민국"))
    JPN = national_job_level(get_country_grade("일본"))
    THA = national_job_level(get_country_grade("태국"))
    AFR3 = clv("나이지리아", 3)

    def allowed(job_lv, recent_lv, rep=0.0, jobless=0):
        return (manager_career_floor(recent_lv, rep, jobless) <= job_lv
                <= manager_max_level(recent_lv, recent_lv, rep, 0, 0.0))

    cases = [
        # (설명, 자리수준, 최근수준, 명성, 무직시즌, 기대)
        ("EPL 감독 → 잉글랜드 7부",      ENG7,  EPL, 0.0, 0, False),
        ("EPL 감독 → 잉글랜드 4부",      ENG4,  EPL, 0.0, 0, False),
        ("EPL 감독 → 챔피언십",          CHAMP, EPL, 0.0, 0, True),
        ("EPL 감독 → 포르투갈 1부",      POR,   EPL, 0.0, 0, True),
        ("EPL 감독 → 맨시티(명성 없음)", MCI,   EPL, 0.0, 0, False),
        ("EPL 감독 → 맨시티(명장 70)",   MCI,   EPL, 70.0, 0, True),
        ("대한민국 국대 → 일본 국대",    JPN,   KOR, 40.0, 0, True),
        ("대한민국 국대 → 태국 국대",    THA,   KOR, 40.0, 0, True),
        ("대한민국 국대 → 아프리카 3부", AFR3,  KOR, 40.0, 0, False),
        ("EPL 감독 → 4부 (무직 3시즌)",  ENG4,  EPL, 0.0, 3, True),
    ]
    print()
    print("  B. 신민용 사례 판정")
    for desc, jl, rl, rep, jb, want in cases:
        got = allowed(jl, rl, rep, jb)
        mark = "OK " if got == want else "FAIL"
        print(f"     [{mark}] {desc:<30} 자리{jl:6.1f} "
              f"→ {'허용' if got else '차단'} (기대 {'허용' if want else '차단'})")
        if got != want:
            problems.append(f"B: {desc} — {'허용' if got else '차단'}됐지만 "
                            f"{'허용' if want else '차단'}이어야 함")

    # ── C. 명성이 실적을 구분하는가 ──────────────────────────────
    rep_good = manager_reputation(titles_league=2, titles_cont=1, avg_level=85,
                                  target_hit=6, target_miss=2, recent_perf=0.45)
    rep_bad = manager_reputation(avg_level=85, target_hit=0, target_miss=1,
                                 recent_perf=-0.35)
    rep_low_farm = manager_reputation(titles_league=10, avg_level=12,
                                      target_hit=10, recent_perf=0.5)
    print()
    print(f"  C. 명성 — A급8시즌(우승2+대륙1) {rep_good:.1f} vs "
          f"A급1시즌12위 {rep_bad:.1f} vs 하부리그 10연패 {rep_low_farm:.1f}")
    if rep_good <= rep_bad:
        problems.append(f"C: 실적 좋은 감독 명성({rep_good:.1f})이 "
                        f"나쁜 감독({rep_bad:.1f})보다 높지 않다")
    if rep_low_farm >= rep_good:
        problems.append(f"C: 하부 리그 연패({rep_low_farm:.1f})가 5대리그급 "
                        f"실적({rep_good:.1f})을 넘어섰다 — 명성 세탁 가능")
    # 세탁 방지는 "낮기만" 하면 되는 게 아니라 **상한을 못 뚫어야** 한다.
    # 명성이 높아도 상한이 최근 수준에 묶여 있는지 직접 본다.
    farm_ceil = manager_max_level(12.0, 12.0, rep_low_farm, 0, 0.5)
    print(f"     하부리그 연패 감독의 상한 {farm_ceil:.1f} "
          f"(EPL {EPL:.0f} 도달 {'가능 ← 문제' if farm_ceil >= EPL else '불가'})")
    if farm_ceil >= EPL:
        problems.append(f"C: 하부 리그 연패만으로 상한이 {farm_ceil:.1f}까지 올라 "
                        f"EPL({EPL:.0f})에 닿는다")

    # ── 시장 실행 ────────────────────────────────────────────────
    import database as d
    db = os.path.join(work, "rep.db")
    shutil.copy(os.path.abspath(args.db), db)
    hsrc = os.path.splitext(os.path.abspath(args.db))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")
    d.USE_MEMORY_DB = False
    d.DB_PATH = db
    os.chdir(work)
    d.reset_conn_pool()
    d.init_db()
    missing = d.verify_manager_rep_columns(d.get_conn())
    if missing:
        problems.append("스키마: ③-c 컬럼 누락 "
                        + ", ".join(f"{t}.{c}" for t, c in missing))

    import ai_lifecycle as al
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()

    # ── H. 우승 수집기 단위 검증 ─────────────────────────────────
    # 이 QA 하네스는 리그 결과만 주입하므로 컵/대륙 우승이 0으로 나온다.
    # "수집기가 고장나서 0"과 "대회가 안 열려서 0"은 겉보기가 같으므로,
    # 실제 우승 행을 심어서 수집기 자체를 따로 검증한다.
    _Y = 1900        # 실제 시즌과 겹치지 않는 연도
    _tids = [r[0] for r in c.execute("SELECT id FROM teams LIMIT 6")]
    for _sql, _a in (
            ("INSERT INTO cup_tournaments(year,country_id,name,status,winner_team_id)"
             " VALUES(?,1,'QA국내컵','done',?)", (_Y, _tids[0])),
            ("INSERT INTO domestic_sc_tournaments(year,country_id,name,status,"
             "winner_team_id) VALUES(?,1,'QA국내SC','done',?)", (_Y, _tids[1])),
            ("INSERT INTO cl_tournaments(year,continent,name,status,winner_team_id)"
             " VALUES(?,'유럽','QA챔스','done',?)", (_Y, _tids[2])),
            ("INSERT INTO el_tournaments(year,continent,name,status,winner_team_id)"
             " VALUES(?,'유럽','QA유로파','done',?)", (_Y, _tids[3])),
            ("INSERT INTO cwc_tournaments(year,name,status,winner_team_id)"
             " VALUES(?,'QA클럽월드컵','done',?)", (_Y, _tids[4])),
            # 아직 진행 중인 대회는 세면 안 된다
            ("INSERT INTO cup_tournaments(year,country_id,name,status,winner_team_id)"
             " VALUES(?,2,'QA진행중','active',?)", (_Y, _tids[5]))):
        c.execute(_sql, _a)
    conn.commit()
    _hon = al._manager_season_honours(c, _Y)
    _want = {_tids[0]: [1, 0], _tids[1]: [1, 0], _tids[2]: [0, 1],
             _tids[3]: [0, 1], _tids[4]: [0, 1]}
    print()
    print(f"  H. 우승 수집기 — 컵 2 / 대륙 3 심어서 확인: "
          f"{'일치' if _hon == _want else f'불일치 {_hon}'}")
    if _hon != _want:
        problems.append(f"H: _manager_season_honours가 심은 우승과 다르게 집계 "
                        f"({_hon} != {_want})")
    if _tids[5] in _hon:
        problems.append("H: status='done'이 아닌 대회를 우승으로 셌다")
    for _t in ("cup_tournaments", "domestic_sc_tournaments", "cl_tournaments",
               "el_tournaments", "cwc_tournaments"):
        c.execute(f"DELETE FROM {_t} WHERE year=?", (_Y,))
    conn.commit()

    season = conn.execute("SELECT MAX(season) FROM match_results").fetchone()[0]
    year0 = conn.execute("SELECT MAX(year) FROM match_results").fetchone()[0]
    n_rows, n_lg = _fill_results(conn, season, random.Random(4321))
    print(f"\n  준비: {n_lg}개 리그 {n_rows}경기에 전력 기반 가상 결과 주입")

    # ── G. 결정성 ────────────────────────────────────────────────
    random.seed(24680)
    _before = random.getstate()
    al._manager_turnover(c, year0, season)
    conn.commit()
    if random.getstate() != _before:
        problems.append("G: _manager_turnover가 전역 random 상태를 바꿨음")

    def _mgr_hash():
        h = hashlib.blake2b(digest_size=16)
        for row in conn.execute(
                "SELECT id, reputation, recent_level, career_floor, career_best_level, "
                "best_level_year, seasons_managed, level_sum, titles_league, "
                "titles_cup, titles_cont, target_hit, target_miss, recent_perf, "
                "status, contract_until FROM managers ORDER BY id"):
            h.update(repr(tuple(row)).encode())
        return h.hexdigest()

    snap = os.path.join(work, "snap.db")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(db, snap)
    y1 = year0 + 1
    _fill_results(conn, season, random.Random(4322))
    al._manager_turnover(c, y1, season)
    conn.commit()
    hA = _mgr_hash()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    d.reset_conn_pool()
    shutil.copy(snap, db)
    for sfx in ("-wal", "-shm"):
        if os.path.exists(db + sfx):
            os.remove(db + sfx)
    conn = get_conn()
    c = conn.cursor()
    _fill_results(conn, season, random.Random(4322))
    al._manager_turnover(c, y1, season)
    conn.commit()
    hB = _mgr_hash()
    os.remove(snap)
    print(f"  G. 전역 random 불간섭 {'OK' if random.getstate() == _before else 'FAIL'}"
          f" / 재실행 해시 {'일치' if hA == hB else '불일치 ← 실패'}")
    if hA != hB:
        problems.append("G: 같은 상태에서 ③-c 결과가 재현되지 않음")

    for k in range(2, args.years):
        _fill_results(conn, season, random.Random(4321 + k))
        al._manager_turnover(c, year0 + k, season)
        conn.commit()

    # ── D. 추락 방지가 시장에서 지켜졌는가 ───────────────────────
    lvl = {}
    for r in conn.execute(
            """SELECT t.id, t.name, cn.name AS country, lg.tier AS tier FROM teams t
               LEFT JOIN countries cn ON cn.id = t.country_id
               LEFT JOIN leagues lg ON lg.id = t.league_id"""):
        cty = r["country"] or ""
        lvl[r["id"]] = manager_job_level(
            get_country_league_grade(cty), r["tier"],
            prestige_level(cty, r["name"]) if cty else 0)

    # 재직자마다: 이전 임기의 자리 수준(= 직전 recent_level 근사)과 현재 자리
    # 수준을 비교해 추락 폭을 본다. 이전 임기는 team_managers에서 가져온다.
    falls = []
    bad_falls = 0
    for r in conn.execute(
            """SELECT tm.manager_id AS mid, tm.team_id AS tid, tm.start_year AS sy,
                      m.reputation AS rep
               FROM team_managers tm JOIN managers m ON m.id = tm.manager_id
               WHERE tm.end_year IS NULL"""):
        prev = conn.execute(
            """SELECT job_level FROM team_managers
               WHERE manager_id=? AND end_year IS NOT NULL
               ORDER BY end_year DESC, id DESC LIMIT 1""", (r["mid"],)).fetchone()
        if not prev or not prev["job_level"]:
            continue
        p = float(prev["job_level"])
        cur = lvl.get(r["tid"], 0.0)
        if cur < p:
            falls.append(p - cur)
            # 무직 기간을 모르므로 가장 관대한 기준(0시즌)이 아니라 실제
            # 상한인 무직 0 기준 하한으로 본다 — 여기서 걸리면 확실한 위반.
            if cur < manager_career_floor(p, float(r["rep"] or 0.0),
                                          8) - 0.5:
                bad_falls += 1
    print()
    print(f"  D. 하향 부임 {len(falls):,}건 / 평균 하락폭 "
          f"{statistics.mean(falls):.1f} · 최대 {max(falls):.1f}"
          if falls else "  D. 하향 부임 0건")
    print(f"     최대 관용 하한(무직 8시즌 기준)조차 밑도는 추락 {bad_falls}건")
    if bad_falls:
        problems.append(f"D: 하한을 명백히 밑도는 추락 부임 {bad_falls}건")

    # ── E. 명성 분포 ─────────────────────────────────────────────
    reps = [r[0] for r in conn.execute(
        "SELECT reputation FROM managers WHERE seasons_managed > 0")]
    tit = conn.execute(
        "SELECT SUM(titles_league), SUM(titles_cup), SUM(titles_cont) "
        "FROM managers").fetchone()
    print()
    print(f"  E. 실적 있는 감독 {len(reps):,}명 명성 "
          f"평균 {statistics.mean(reps):.1f} / 중위 {statistics.median(reps):.1f} "
          f"/ 최대 {max(reps):.1f}")
    print(f"     누적 우승 — 리그 {tit[0] or 0:,} · 컵 {tit[1] or 0:,} "
          f"· 대륙 {tit[2] or 0:,}")
    if max(reps) <= 0:
        problems.append("E: 명성이 아무도 0을 넘지 않았다 — 실적 누적 미작동")
    if not (tit[0] or 0):
        problems.append("E: 리그 우승이 한 건도 기록되지 않았다")
    # 컵/대륙이 0인 건 하네스가 컵 대회를 안 돌려서다 — 수집기 자체는 H가
    # 검증한다. 그래서 여기서 컵 0을 실패로 잡지 않는다.

    # ── F. 명성이 자리 수준과 상관되는가 ─────────────────────────
    pairs = []
    for r in conn.execute(
            """SELECT m.reputation AS rep, tm.team_id AS tid
               FROM team_managers tm JOIN managers m ON m.id = tm.manager_id
               WHERE tm.end_year IS NULL AND m.seasons_managed > 0"""):
        pairs.append((float(r["rep"] or 0.0), lvl.get(r["tid"], 0.0)))
    pairs.sort()
    n = len(pairs)
    if n >= 200:
        lo = statistics.mean(x[1] for x in pairs[:n // 4])
        hi = statistics.mean(x[1] for x in pairs[-n // 4:])
        print()
        print(f"  F. 명성 하위 25% 감독의 평균 자리 수준 {lo:.1f} "
              f"vs 상위 25% {hi:.1f}")
        if hi <= lo:
            problems.append(f"F: 명성이 높은 감독이 더 좋은 자리에 있지 않다 "
                            f"(상위 {hi:.1f} ≤ 하위 {lo:.1f})")
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