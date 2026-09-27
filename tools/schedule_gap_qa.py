# -*- coding: utf-8 -*-
"""경기 일정 최소 휴식일 실측 (2026-09 신설).

[신민용 리포트 20번] "7/20에 리그 경기가 있고 7/21에 슈퍼컵 결승이 잡힌다.
경기 사이에 최소 하루는 쉬어야 한다. 컵대회/대륙대항전도 같은 문제가
있는지 같이 확인할 것."

헤드리스로 진행된 세이브를 열어서, 한 팀이 실제로 배정받은 모든 경기
날짜(day)를 대회 구분 없이 모아 |Δday| <= 1 인 쌍을 전부 찾는다.
competition_common.DEFAULT_MATCH_DAY_GAP=2 의 의미와 동일한 기준
("두 경기는 최소 2일 떨어져 있어야 한다" = 사이에 하루 이상 휴식).

사용: python3 tools/schedule_gap_qa.py <세이브.db> [--year 2001]
"""
import argparse
import collections
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (표시이름, 매치표, 토너먼트표 or None)  — 토너먼트표가 있으면 year로 조인
SOURCES = [
    ("리그",        "match_results",      None),
    ("국내컵",      "cup_matches",        "cup_tournaments"),
    ("3·4부컵",     "lower_cup_matches",  "lower_cup_tournaments"),
    ("국내슈퍼컵",  "domestic_sc_matches", "domestic_sc_tournaments"),
    ("대륙슈퍼컵",  "sc_matches",         "sc_tournaments"),
    ("챔피언스",    "cl_matches",         "cl_tournaments"),
    ("유로파",      "el_matches",         "el_tournaments"),
    ("컨퍼런스",    "ecl_matches",        "ecl_tournaments"),
    ("클럽월드컵",  "cwc_matches",        "cwc_tournaments"),
]


def _table_exists(c, t):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                          (t,)).fetchone())


def _cols(c, t):
    return {r[1] for r in c.execute(f"PRAGMA table_info({t})")}


def load_matches(c, year=None):
    """[(year, comp, day, week, home, away, is_my)] — day가 있는 행만."""
    out = []
    for comp, mt, tt in SOURCES:
        if not _table_exists(c, mt):
            continue
        mc = _cols(c, mt)
        if "day" not in mc:
            continue
        ismy = "is_my" if "is_my" in mc else "0"
        if tt and _table_exists(c, tt) and "year" in _cols(c, tt):
            sql = (f"SELECT t.year AS y, m.day AS d, m.week AS w, m.home_team_id AS h, "
                   f"m.away_team_id AS a, m.{ismy} AS im FROM {mt} m "
                   f"JOIN {tt} t ON t.id = m.tournament_id "
                   f"WHERE m.day IS NOT NULL AND m.day > 0")
            args = []
            if year is not None:
                sql += " AND t.year=?"
                args.append(year)
        else:
            ycol = "year" if "year" in mc else "NULL"
            sql = (f"SELECT {ycol} AS y, day AS d, week AS w, home_team_id AS h, "
                   f"away_team_id AS a, {ismy} AS im FROM {mt} "
                   f"WHERE day IS NOT NULL AND day > 0")
            args = []
            if year is not None and ycol != "NULL":
                sql += " AND year=?"
                args.append(year)
        for r in c.execute(sql, args):
            out.append((r[0], comp, r[1], r[2], r[3], r[4], r[5]))
    return out


def run(db_path, year=None, gap=2, show=15):
    c = sqlite3.connect(db_path)
    rows = load_matches(c, year)
    print(f"[DB] {db_path}")
    print(f"[대상] day가 배정된 경기 {len(rows):,}건"
          + (f" (year={year})" if year else " (전 시즌)"))
    by_comp = collections.Counter(r[1] for r in rows)
    print("  대회별:", dict(by_comp))

    # 팀별 (year, day, comp, is_my)
    per_team = collections.defaultdict(list)
    for y, comp, d, w, h, a, im in rows:
        for tid in (h, a):
            if tid:
                per_team[(y, tid)].append((d, comp, im))

    pair_cnt = collections.Counter()
    pair_ex = collections.defaultdict(list)
    same_day = collections.Counter()
    teams_hit = set()
    for (y, tid), lst in per_team.items():
        lst.sort()
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                dd = lst[j][0] - lst[i][0]
                if dd >= gap:
                    break
                c1, c2 = lst[i][1], lst[j][1]
                key = tuple(sorted((c1, c2)))
                pair_cnt[key] += 1
                if dd == 0:
                    same_day[key] += 1
                teams_hit.add((y, tid))
                if len(pair_ex[key]) < show:
                    pair_ex[key].append((y, tid, lst[i][0], c1, lst[j][0], c2,
                                         lst[i][2] or lst[j][2]))

    total = sum(pair_cnt.values())
    print(f"\n[위반] |Δday| < {gap} 인 경기 쌍 {total:,}건 · 영향 팀-시즌 {len(teams_hit):,}개")
    if not total:
        print("  (없음)")
    print(f"  {'대회 쌍':<26} {'건수':>7} {'그중 같은날':>10}")
    for key, n in pair_cnt.most_common():
        print(f"  {key[0]+' + '+key[1]:<26} {n:>7,} {same_day[key]:>10,}")
    for key, n in pair_cnt.most_common():
        print(f"\n  ── {key[0]} + {key[1]} ({n:,}건) 예시")
        for y, tid, d1, c1, d2, c2, im in pair_ex[key]:
            from constants import day_to_date_str
            print(f"     {y} team#{tid:<6} {d1:>3}일({day_to_date_str(d1)}) {c1}"
                  f"  →  {d2:>3}일({day_to_date_str(d2)}) {c2}"
                  f"  Δ{d2-d1}{'  [내 팀]' if im else ''}")
    c.close()
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--gap", type=int, default=2)
    a = ap.parse_args()
    sys.exit(0 if run(a.db, a.year, a.gap) == 0 else 1)