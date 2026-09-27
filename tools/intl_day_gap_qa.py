# -*- coding: utf-8 -*-
"""내 팀 경로(국내컵/챔스/유로파/컨퍼런스) 날짜 충돌 실측 (2026-09 신설).

[배경] 국내컵·챔스·유로파·컨퍼런스는 AI 경기에 day를 저장하지 않고 주차
단위로만 돌아간다 — day가 잡히는 건 "내 팀이 낀 경기"뿐이고, 그 날짜는
game_engine._week_intl_cl_day(week, p)가 그 주 안에서 하루를 골라준다.
그래서 tools/schedule_gap_qa.py(저장된 day만 보는 계측)로는 이 경로의
충돌이 아예 안 잡힌다(더미 플레이어는 소속팀이 없어 더욱).

이 스크립트는 그 경로를 전 팀에 대해 계측한다: 어떤 팀이 어떤 주에
컵/대륙대항전 경기를 갖고 있으면, 그 팀이 '내 팀'이었을 때 배정받을
날짜를 _week_intl_cl_day와 똑같은 규칙으로 계산해서, 그 팀의 다른
저장된 경기 날짜(리그·3·4부컵·슈퍼컵·클럽월드컵·승강PO 등)와
MIN_MATCH_DAY_GAP 미만으로 붙는지 본다.

사용: python3 tools/intl_day_gap_qa.py <세이브.db> [--year 2002]
"""
import _path  # noqa: F401
import argparse
import collections
import os
import sys

import database

# 내 팀일 때만 day가 잡히는 대회(= _week_intl_cl_day가 날짜를 정하는 쪽)
WEEK_ONLY = [
    ("국내컵",   "cup_matches",  "cup_tournaments"),
    ("챔피언스", "cl_matches",   "cl_tournaments"),
    ("유로파",   "el_matches",   "el_tournaments"),
    ("컨퍼런스", "ecl_matches",  "ecl_tournaments"),
]


def run(db_path, year=None):
    database.USE_MEMORY_DB = False
    database.DB_PATH = os.path.abspath(db_path)
    database.reset_conn_pool()
    import game_engine as ge
    from constants import MIN_MATCH_DAY_GAP, day_to_date_str
    from tools.schedule_gap_qa import load_matches  # noqa: E402

    conn = database.get_conn()
    # year -> season 매핑(리그 경기 기준)
    y2s = {r[0]: r[1] for r in conn.execute(
        "SELECT DISTINCT year, season FROM match_results ORDER BY year")}

    # 저장된 day가 있는 모든 경기 → {(year,tid): {day}}
    stored = collections.defaultdict(set)
    src = collections.defaultdict(set)   # (year,tid,day) -> {comp}
    for y, comp, d, w, h, a, im in load_matches(conn, year):
        for tid in (h, a):
            if tid:
                stored[(y, tid)].add(d)
                src[(y, tid, d)].add(comp)

    # 주차 단위 대회에서 (year, week, team) 목록 수집
    need = collections.defaultdict(set)   # (year, tid) -> {(week, comp)}
    for comp, mt, tt in WEEK_ONLY:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (mt,)).fetchone():
            continue
        sql = (f"SELECT t.year y, m.week w, m.home_team_id h, m.away_team_id a "
               f"FROM {mt} m JOIN {tt} t ON t.id=m.tournament_id")
        args = []
        if year is not None:
            sql += " WHERE t.year=?"
            args.append(year)
        for r in conn.execute(sql, args):
            for tid in (r[2], r[3]):
                if tid:
                    need[(r[0], tid)].add((r[1], comp))

    print(f"[DB] {db_path}")
    print(f"[대상] 주차단위 대회 경기를 가진 (시즌,팀) 조합 {len(need):,}개")

    viol = collections.Counter()
    ex = collections.defaultdict(list)
    checked = 0
    for (y, tid), wk_comps in need.items():
        season = y2s.get(y)
        if season is None:
            continue
        # [중요] current_year를 반드시 같이 넘겨야 한다 — _week_intl_cl_day의
        # "다른 대회 경기일까지 피하기"는 토너먼트 표의 year로 이번 시즌만
        # 거르므로, year가 없으면 그 블록이 통째로 스킵되고 예전(리그만
        # 보는) 동작을 계측하게 된다.
        st = {"current_season": season, "current_year": y}
        p = {"current_team_id": tid}
        for week, comp in sorted(wk_comps):
            ge._week_intl_cl_day_cache.clear()
            cand = ge._week_intl_cl_day(week, p, st=st)
            checked += 1
            for d in stored[(y, tid)]:
                if abs(cand - d) < MIN_MATCH_DAY_GAP:
                    other = "/".join(sorted(src[(y, tid, d)]))
                    key = tuple(sorted((comp, other)))
                    viol[key] += 1
                    if len(ex[key]) < 10:
                        ex[key].append((y, tid, week, cand, d, other))

    print(f"[계산] _week_intl_cl_day 호출 {checked:,}회")
    total = sum(viol.values())
    print(f"\n[위반] |Δday| < {MIN_MATCH_DAY_GAP} 충돌 {total:,}건")
    if not total:
        print("  (없음)")
    for key, n in viol.most_common():
        print(f"  {key[0]+' + '+key[1]:<30} {n:>7,}")
    for key, n in viol.most_common():
        print(f"\n  ── {key[0]} + {key[1]} ({n:,}건) 예시")
        for y, tid, week, cand, d, other in ex[key]:
            print(f"     {y} team#{tid:<6} {week:>2}주차: 대회일 {cand}일"
                  f"({day_to_date_str(cand)})  vs  {other} {d}일"
                  f"({day_to_date_str(d)})  Δ{abs(cand-d)}")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--year", type=int, default=None)
    a = ap.parse_args()
    sys.exit(0 if run(a.db, a.year) == 0 else 1)