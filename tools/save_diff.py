# -*- coding: utf-8 -*-
"""[2026-11] 두 세이브(본 DB + 기록 DB)의 모든 표를 행 단위로 비교 — "바꾸려는 상의 행 외에 다른 표 0개" 판정용.
읽기 전용(mode=ro)으로 열고 아무것도 쓰지 않는다.

  python tools/save_diff.py A/headless.db B/headless.db [--awards]

  · 같은 이름 표끼리 (A EXCEPT B) · (B EXCEPT A) 행 수를 센다(열 구성이 다르면 '열 다름'으로 따로 보고).
  · season_individual_awards는 상 종류(award_type)별로 나눠 보여 주고, --awards면 달라진 행(연도·상·순위·선수)을
    전부 찍는다.
  · 기록 DB는 X.db 옆의 X.history.db."""
import argparse
import os
import sqlite3
import sys


def _hist(p):
    return os.path.splitext(p)[0] + ".history.db"


def _tables(c, schema):
    return {r[0] for r in c.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' "
                                    "AND name NOT LIKE 'sqlite_%'")}


def _cols(c, schema, t):
    """비교할 열. AUTOINCREMENT 표의 번호 열(id)은 뺀다 — 앞에서 행이 하나만 늘거나 줄어도 뒤 번호가 전부 밀려
    내용이 같은 행까지 다르게 보이기 때문(예: season_individual_awards)."""
    sql = (c.execute(f"SELECT sql FROM {schema}.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() or [""])[0] or ""
    auto = "AUTOINCREMENT" in sql.upper()
    return [r[1] for r in c.execute(f"PRAGMA {schema}.table_info('{t}')") if not (auto and r[5] == 1)]


def compare(a, b, show_awards=False):
    c = sqlite3.connect(f"file:{a}?mode=ro", uri=True)
    for alias, path in (("ah", _hist(a)), ("bm", b), ("bh", _hist(b))):
        c.execute(f"ATTACH DATABASE 'file:{path}?mode=ro' AS {alias}")
    diffs = []
    for sa, sb, label in (("main", "bm", "본"), ("ah", "bh", "기록")):
        ta, tb = _tables(c, sa), _tables(c, sb)
        for t in sorted(ta ^ tb):
            diffs.append((label, t, "한쪽에만 있는 표", None, None))
        for t in sorted(ta & tb):
            ca, cb = _cols(c, sa, t), _cols(c, sb, t)
            if ca != cb:
                diffs.append((label, t, "열 다름", None, None))
                continue
            cl = ",".join(f'"{x}"' for x in ca)
            n_ab = c.execute(f"SELECT COUNT(*) FROM (SELECT {cl} FROM {sa}.\"{t}\" EXCEPT SELECT {cl} FROM {sb}.\"{t}\")").fetchone()[0]
            n_ba = c.execute(f"SELECT COUNT(*) FROM (SELECT {cl} FROM {sb}.\"{t}\" EXCEPT SELECT {cl} FROM {sa}.\"{t}\")").fetchone()[0]
            if n_ab or n_ba:
                diffs.append((label, t, "행 다름", n_ab, n_ba))
    print(f"A = {a}\nB = {b}")
    if not diffs:
        print("모든 표 동일")
    for label, t, kind, n_ab, n_ba in diffs:
        extra = f" — A에만 {n_ab}행 · B에만 {n_ba}행" if n_ab is not None else ""
        print(f"  [{label}] {t}: {kind}{extra}")
    # 상 종류별
    for label, sa, sb in (("본", "main", "bm"), ("기록", "ah", "bh")):
        if "season_individual_awards" not in (_tables(c, sa) & _tables(c, sb)):
            continue
        ca = _cols(c, sa, "season_individual_awards")
        cl = ",".join(f'"{x}"' for x in ca)
        rows = []
        for side, x, y in (("A에만", sa, sb), ("B에만", sb, sa)):
            for r in c.execute(f"SELECT {cl} FROM {x}.season_individual_awards EXCEPT "
                               f"SELECT {cl} FROM {y}.season_individual_awards"):
                rows.append((side, dict(zip(ca, r))))
        if not rows:
            continue
        by = {}
        for side, r in rows:
            by.setdefault(r.get("award_type"), []).append((side, r))
        print(f"\n[{label}] season_individual_awards 상 종류별 달라진 행")
        for k in sorted(by, key=str):
            print(f"  {k}: {len(by[k])}행")
            if show_awards:
                for side, r in sorted(by[k], key=lambda z: (z[1].get("year"), str(z[1].get("category")),
                                                            str(z[1].get("scope_id", "")), z[1].get("rank") or 0, z[0])):
                    print(f"     {side} {r.get('year')} {r.get('category')} rank {r.get('rank')} 선수 {r.get('player_id')} "
                          f"총점 {r.get('total_score')}")
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--awards", action="store_true")
    args = ap.parse_args()
    d = compare(os.path.abspath(args.a), os.path.abspath(args.b), args.awards)
    sys.exit(1 if d else 0)


if __name__ == "__main__":
    main()