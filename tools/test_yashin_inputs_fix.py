# -*- coding: utf-8 -*-
"""[2026-11] 야신상 입력 버그수정 단위 테스트(가상 DB — 실제 세이브 없음).

버그
  ① 게임 중 야신상 계산 시점엔 그 해 hist 순위표(league_season_standings)가 아직 없다 → 리그 순위 점수 0, 강등 미반영.
     수정: _yashin_league_ranks가 _ballon_season_standings(순위표 없으면 match_results(+archive)로 같은 규칙 합산)를 쓴다.
  ② GK 상대 공격진(_yashin_team_attack)이 현역 표(ai_players)와 묶여, 그 사이 정리된 선수가 게임 중엔 빠지고
     측정 도구(은퇴 기록 복원)에선 들어갔다. 수정: 시즌 기록이 있는 선수 전원, 포지션·OVR은 이력 → 현역 → 은퇴 기록.

검증
  A. 순위표가 없을 때(경기 기록만) = 순위표가 있을 때 — 순위·팀 수·강등 완전 일치, 미완료 경기(-1) 제외
  B. 순위표가 있으면 그 값을 그대로 쓴다(경기 기록이 지워진 지난 시즌)
  C. 상대 공격진: 현역 표에서 빠진 선수도 시즌 기록·이력으로 들어간다 — 현역 표에 있을 때와 같은 값
  D. 이력이 없으면 현역 표 → 은퇴 기록 값으로 채운다, 시즌 기록이 없는 선수는 안 들어간다
  E. (선택, --db 세이브 --year 연도) ③ 야신상 후보 보강 — 그 해 은퇴 기록이 있는 GK 후보를 현역 표에서 지워
     (게임 중 상황) 계산해도, 현역 표에 있을 때와 야신상 Top10·점수가 완전히 같다. 발롱도르 후보 목록은 예전 그대로
사용: python tools/test_yashin_inputs_fix.py [--db 경로/game.db --year 2007]"""
import _path  # noqa: F401
import argparse
import contextlib
import io
import os
import random
import shutil
import sqlite3
import sys

import game_engine as GE

FAILS = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def make_db():
    c = sqlite3.connect(":memory:")
    c.execute("ATTACH ':memory:' AS hist")
    c.executescript("""
        CREATE TABLE match_results(id INTEGER PRIMARY KEY, league_id INTEGER, week INTEGER, home_team_id INTEGER,
            away_team_id INTEGER, home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1, season INTEGER,
            year INTEGER, day INTEGER);
        CREATE TABLE match_results_archive(id INTEGER PRIMARY KEY, league_id INTEGER, week INTEGER, home_team_id INTEGER,
            away_team_id INTEGER, home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1, season INTEGER,
            year INTEGER, day INTEGER);
        CREATE TABLE promotion_log(id INTEGER PRIMARY KEY, year INTEGER, team_name TEXT, from_tier INTEGER,
            to_tier INTEGER, league_name TEXT, from_league_id INTEGER, to_league_id INTEGER, team_id INTEGER);
        CREATE TABLE hist.league_season_standings(id INTEGER PRIMARY KEY, league_id INTEGER, season INTEGER,
            year INTEGER, team_id INTEGER, wins INTEGER, draws INTEGER, losses INTEGER, goals_for INTEGER,
            goals_against INTEGER);
        CREATE TABLE ai_players(id INTEGER PRIMARY KEY, position TEXT, ovr INTEGER);
        CREATE TABLE ai_players_retired(id INTEGER PRIMARY KEY, position TEXT, ovr INTEGER, retirement_year INTEGER);
        CREATE TABLE hist.ai_player_season_stats(player_id INTEGER, year INTEGER, team_id INTEGER, matches INTEGER,
            PRIMARY KEY(year, player_id));
        CREATE TABLE hist.ai_player_ovr_history(player_id INTEGER, year INTEGER, ovr INTEGER, PRIMARY KEY(year, player_id));
        CREATE TABLE hist.ai_player_position_history(player_id INTEGER, year INTEGER, position TEXT,
            PRIMARY KEY(year, player_id));
    """)
    return c


def archive_standings(c, year):
    """database.archive_old_seasons의 순위표 요약과 같은 규칙(리그·팀별 합계, home_score >= 0)."""
    c.execute("""INSERT INTO hist.league_season_standings(league_id, season, year, team_id, wins, draws, losses,
                     goals_for, goals_against)
                 SELECT league_id, season, MAX(year), team_id, SUM(gf>ga), SUM(gf=ga), SUM(gf<ga), SUM(gf), SUM(ga) FROM (
                     SELECT league_id, season, year, home_team_id team_id, home_score gf, away_score ga
                       FROM match_results WHERE home_score >= 0
                     UNION ALL
                     SELECT league_id, season, year, away_team_id, away_score, home_score
                       FROM match_results WHERE home_score >= 0)
                 WHERE year=? GROUP BY league_id, season, team_id""", (year,))


# ── A·B. 순위표 대체 경로 ──────────────────────────────────────────
print("A. 순위표 없을 때 = 있을 때")
rng = random.Random(11)
c = make_db()
mid = 0
for lid, teams in ((1, list(range(100, 110))), (2, list(range(200, 220)))):
    for h in teams:
        for a in teams:
            if h == a:
                continue
            mid += 1
            hs, as_ = (rng.randint(0, 4), rng.randint(0, 3)) if mid % 97 else (-1, -1)    # 몇 경기는 미완료
            c.execute("INSERT INTO match_results VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (mid, lid, 1, h, a, hs, as_, 2005, 2005, 1))
c.executemany("INSERT INTO promotion_log(year, from_tier, to_tier, team_id) VALUES(2005,?,?,?)",
              [(1, 2, 105), (1, 2, 109), (2, 1, 201)])
before = GE._yashin_league_ranks(c, 2005)
n_hist_before = c.execute("SELECT COUNT(*) FROM hist.league_season_standings").fetchone()[0]
archive_standings(c, 2005)
after = GE._yashin_league_ranks(c, 2005)
check("순위표 없을 때 hist 행 0개 (게임 중 상황 재현)", n_hist_before == 0)
check("30팀 전부 순위가 매겨짐", len(before) == 30, f"{len(before)}팀")
check("순위·팀 수·강등 = 순위표가 생긴 뒤와 완전히 같음", before == after)
check("강등 2팀 반영(위→아래 부만)", sorted(t for t, v in before.items() if v[2]) == [105, 109])
print("B. 순위표가 있으면 그 값")
c.execute("DELETE FROM match_results")                      # 지난 시즌 — 원본 경기 기록은 지워짐
check("경기 기록이 지워져도 순위표로 같은 결과", GE._yashin_league_ranks(c, 2005) == after)
st = GE._ballon_season_standings(c, 2005)
check("_ballon_season_standings 7칸 행", st and all(len(r) == 7 for r in st), f"{len(st)}행")

# ── C·D. GK 상대 공격진 ────────────────────────────────────────────
print("C. 상대 공격진 — 현역 표에서 빠진 선수")
c = make_db()
players = []
pid = 0
for tid in (100, 101):
    for pos, ovr in (("ST", 90), ("LW", 86), ("RW", 84), ("CAM", 83), ("CM", 80), ("CB", 82), ("GK", 85)):
        pid += 1
        ovr += tid - 100
        players.append((pid, tid, pos, ovr))
        c.execute("INSERT INTO hist.ai_player_season_stats VALUES(?,?,?,30)", (pid, 2005, tid))
        c.execute("INSERT INTO hist.ai_player_ovr_history VALUES(?,?,?)", (pid, 2005, ovr))
        c.execute("INSERT INTO hist.ai_player_position_history VALUES(?,?,?)", (pid, 2005, pos))
        c.execute("INSERT INTO ai_players VALUES(?,?,?)", (pid, pos, ovr + 2))   # 지금 OVR(이력과 다름)
full = GE._yashin_team_attack(c, 2005, [100, 101])
c.execute("INSERT INTO ai_players_retired VALUES(1, 'ST', 91, 2005)")
c.execute("DELETE FROM ai_players WHERE id=1")             # 100팀 주포가 43주차에 정리됨
gone = GE._yashin_team_attack(c, 2005, [100, 101])
check("정리된 선수 포함 — 현역 표에 있을 때와 같은 값", gone == full, f"{full} / {gone}")
c.execute("DELETE FROM hist.ai_player_season_stats WHERE player_id=1")
check("시즌 기록까지 없으면 빠짐 — 주포가 빠진 만큼 값이 낮아짐",
      GE._yashin_team_attack(c, 2005, [100])[100] < full[100])
c.execute("INSERT INTO hist.ai_player_season_stats VALUES(1, 2005, 100, 30)")
print("D. 이력이 없을 때 채우는 순서")
c.execute("DELETE FROM hist.ai_player_ovr_history WHERE player_id IN (1, 2)")
c.execute("DELETE FROM hist.ai_player_position_history WHERE player_id IN (1, 2)")
d = GE._yashin_team_attack(c, 2005, [100])
from ai_lifecycle import _gk_attack_rating
# 1번(정리됨) → 은퇴 기록 ST 91, 2번(현역) → 현역 표 LW 88, 나머지는 이력
exp = _gk_attack_rating([91, 88, 84, 83])
check("이력 없음 → 현역 표 → 은퇴 기록 순", abs(d[100] - exp) < 1e-12, f"{d[100]} / {exp}")
c.execute("INSERT INTO ai_players VALUES(999, 'ST', 99)")   # 시즌 기록 없는 선수
check("시즌 기록이 없는 선수는 안 들어감", GE._yashin_team_attack(c, 2005, [100]) == d)

ap = argparse.ArgumentParser()
ap.add_argument("--db", default=None)
ap.add_argument("--year", type=int, default=None)
a = ap.parse_args()
if a.db:
    print("E. 야신상 후보 보강(실제 세이브)")
    import award_baseline as AB
    with contextlib.redirect_stdout(io.StringIO()):
        conn, tmp = AB.prepare(os.path.abspath(a.db))
    cc = conn.cursor()
    try:
        AB.patch_for_past_years(cc)                    # 은퇴 기록을 현역 표로 되살림(측정 도구와 같은 상태)
        y = a.year

        def run():
            AB.clear_year_caches()
            GE._begin_team_gc_run_cache(y)
            try:
                cands = GE._get_ballon_candidates(cc, y)
                _al, top = GE._compute_yashin_top10(cc, y)
            finally:
                GE._end_team_gc_run_cache()
            return cands, [(x["player_id"], round(x["total"], 12), round(x["shrunk"], 12)) for x in top], \
                [(x["player_id"], round(x["shrunk"], 12)) for x in _al]
        cands0, top0, all0 = run()
        gone = [r[0] for r in cc.execute(
            "SELECT id FROM ai_players_retired WHERE retirement_year=?", (y,)).fetchall()]
        gk_gone = [x["player_id"] for x in cands0 if x.get("position") == "GK" and x["player_id"] in set(gone)]
        q = ",".join(map(str, gk_gone)) or "-1"
        cc.execute(f"DELETE FROM ai_players WHERE id IN ({q})")   # 게임 중: 상 계산 전에 정리돼 현역 표에 없음
        cands1, top1, all1 = run()
        check(f"{y}: 지운 GK 후보 {len(gk_gone)}명", len(gk_gone) > 0, str(gk_gone))
        check("발롱도르 후보 목록에서는 빠짐(기본 후보 함수 불변)",
              not ({x["player_id"] for x in cands1} & set(gk_gone)))
        check("야신상 GK 후보 전원·선방률 보정값 완전히 같음", sorted(all0) == sorted(all1), f"{len(all0)}명")
        check("야신상 Top10·총점 완전히 같음", top0 == top1)
    finally:
        conn.close()
        os.chdir(_path.ROOT)
        shutil.rmtree(tmp, ignore_errors=True)

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
sys.exit(1 if FAILS else 0)