"""manager_turnover_qa.py — 감독 시스템 ③단계 검증 (2026-09 신설)

③은 "감독 A → 경질 → 감독 B 부임 → 포메이션 재선택"이 실제로 도는지,
그리고 그 판정이 **구단 목표 대비 성적**을 제대로 반영하는지를 본다.

  A. 무결성     — 팀당 현재 감독이 정확히 1명인가, 재임 이력이 끊기거나
                  겹치지 않는가(end_year < 다음 start_year)
  B. 구단 목표   — teams.club_ambition이 순위 분포와 맞게 채워지는가
  C. 경질 판정   — 목표를 못 지킨 감독이 더 자주 잘리는가.
                  경질된 감독과 유임된 감독의 평균 순위 백분위가 갈려야
                  한다. 안 갈리면 판정이 성적을 안 보고 있다는 뜻이다.
  D. 허니문     — 부임 1년차가 실제로 덜 잘리는가.
                  [주의] 연도끼리 비교하면 안 된다 — ①의 이관으로 첫 해엔
                  **모든 감독이 1년차**라 그 해 전체가 이미 허니문이다.
                  같은 해 안에서 재임 1년 이하 vs 2년 이상을 갈라 재야 한다.
  E. 연쇄       — 감독이 바뀐 팀은 포메이션도 바뀌는가(재검토 확률과 무관)
  F. 결정성     — _manager_turnover가 전역 random을 건드리지 않는가
  G. 다년 안정성 — 여러 시즌 돌려도 감독 수가 폭주하거나 팀이 감독을
                  잃지 않는가

[세이브 준비] 새로 시작한 세이브는 치른 경기가 0이라 순위 자체가 없다.
그래서 이 스크립트는 사본에 **전력 기반 가상 결과**를 채워 넣고 검사한다
(강한 팀이 더 자주 이기도록 — 그래야 "목표 대비 성적" 축이 의미를 갖는다).
원본 세이브는 건드리지 않는다.

사용법:
    python tools/manager_turnover_qa.py --db <세이브.db> [--years 5]
"""
import _path  # noqa: F401
import argparse
import collections
import os
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def _prep(src, work):
    os.makedirs(work, exist_ok=True)
    dst = os.path.join(work, "turn.db")
    shutil.copy(os.path.abspath(src), dst)
    hsrc = os.path.splitext(os.path.abspath(src))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(dst)[0] + ".history.db")
    return dst


def _fill_results(conn, season, year, rng, n_leagues=60):
    """리그 몇 개에 전력 기반 가상 결과를 채운다. 강팀이 더 자주 이기게
    해서 순위가 전력과 상관을 갖도록 한다 — 그래야 C(목표 대비 성적)
    검사가 의미 있다."""
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
        h = ovr.get(r[1], 50.0)
        a = ovr.get(r[2], 50.0)
        edge = (h - a) / 8.0
        hs = max(0, int(round(rng.gauss(1.35 + edge * 0.35, 1.1))))
        as_ = max(0, int(round(rng.gauss(1.15 - edge * 0.35, 1.1))))
        ups.append((hs, as_, r[0]))
    conn.executemany(
        "UPDATE match_results SET home_score=?, away_score=? WHERE id=?", ups)
    conn.commit()
    return len(ups), len(lids)


def _integrity(conn):
    """A. team_managers 무결성."""
    problems = []
    dup = conn.execute(
        """SELECT COUNT(*) FROM (SELECT team_id FROM team_managers
           WHERE end_year IS NULL GROUP BY team_id HAVING COUNT(*)>1)""").fetchone()[0]
    if dup:
        problems.append(f"A: 현재 감독이 2명 이상인 팀 {dup}개")
    none_ = conn.execute(
        """SELECT COUNT(*) FROM teams t WHERE NOT EXISTS
           (SELECT 1 FROM team_managers tm WHERE tm.team_id=t.id AND tm.end_year IS NULL)"""
    ).fetchone()[0]
    if none_:
        problems.append(f"A: 현재 감독이 없는 팀 {none_}개")
    # 같은 팀의 재임 구간이 겹치지 않는가 (end_year <= 다음 start_year)
    overlap = conn.execute(
        """SELECT COUNT(*) FROM team_managers a JOIN team_managers b
           ON a.team_id=b.team_id AND a.id<>b.id
           WHERE a.end_year IS NOT NULL AND b.start_year < a.start_year
             AND (b.end_year IS NULL OR b.end_year > a.start_year)""").fetchone()[0]
    if overlap:
        problems.append(f"A: 재임 구간이 겹치는 이력 {overlap}건")
    return problems, dup, none_, overlap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--work", default=None)
    args = ap.parse_args()
    work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="turnover_qa_"))

    import database as d
    db = _prep(args.db, work)
    d.USE_MEMORY_DB = False
    d.DB_PATH = db
    os.chdir(work)
    d.reset_conn_pool()
    d.init_db()          # ① 이관으로 감독이 생긴다

    import ai_lifecycle as al
    from database import get_conn

    conn = get_conn()
    season = conn.execute("SELECT MAX(season) FROM match_results").fetchone()[0]
    year0 = conn.execute("SELECT MAX(year) FROM match_results").fetchone()[0]
    fill_rng = random.Random(9090)
    n_rows, n_lg = _fill_results(conn, season, year0, fill_rng)
    print(f"  준비: {n_lg}개 리그 {n_rows}경기에 전력 기반 가상 결과 주입")

    problems = []
    c = conn.cursor()

    # ── F. 결정성 ────────────────────────────────────────────────
    random.seed(31337)
    _before = random.getstate()
    changed = al._manager_turnover(c, year0, season)
    _after = random.getstate()
    conn.commit()
    if _before != _after:
        problems.append("F: _manager_turnover가 전역 random 상태를 바꿨음")
    print(f"  F. 전역 random 상태 변화: {'없음' if _before == _after else '있음 ← 실패'}")

    # ── B. 구단 목표 ─────────────────────────────────────────────
    amb = collections.Counter(
        r[0] for r in conn.execute(
            "SELECT club_ambition FROM teams WHERE club_ambition<>''"))
    print(f"  B. 구단 목표 분포(채워진 팀 {sum(amb.values())}개): "
          + ", ".join(f"{k} {v}" for k, v in amb.most_common()))
    if not amb:
        problems.append("B: club_ambition이 하나도 안 채워짐")

    # ── C. 경질 판정이 성적을 보는가 ─────────────────────────────
    # [판정 시점 주의] 첫 회차는 teams.club_ambition이 비어 있어서 "시즌
    # 진입 시점 목표"가 존재하지 않는다(그때만 이번 시즌 결과로 폴백).
    # 그래서 상관은 목표가 실제로 박혀 있는 **다음 회차 이후**에서 잰다 —
    # 아래 다년 루프에서 누적해 마지막에 판정한다.
    ranks = al._league_standings(c, season)
    rate = len(changed) / max(1, len(ranks))
    print(f"  C. 첫 회차 경질 {len(changed)}팀 / 판정 대상 {len(ranks)}팀 "
          f"= {rate * 100:.1f}% (목표 미설정 상태라 상관은 아래에서 측정)")
    if not (0.03 <= rate <= 0.45):
        problems.append(f"C: 경질률 {rate * 100:.1f}%가 비현실적 (3~45% 기대)")
    c_sacked, c_stayed = [], []

    # ── D·E·G. 다년 반복 ─────────────────────────────────────────
    print(f"  E·G. {args.years}시즌 반복")
    hist = []
    hny_young = hny_young_sacked = hny_old = hny_old_sacked = 0
    for k in range(1, args.years + 1):
        y = year0 + k
        # 새 시즌 결과를 다시 채운다(같은 경기 행을 재사용 — 순위만 바뀌면 됨).
        _fill_results(conn, season, y, random.Random(9090 + k))
        # D — 판정 직전에 각 팀 감독의 재임 기간을 기록해둔다.
        ten_before = {r[0]: (y - (r[1] or y)) for r in conn.execute(
            "SELECT team_id, start_year FROM team_managers WHERE end_year IS NULL")}
        ch = al._manager_turnover(c, y, season)
        conn.commit()
        cur_forms = {r[0]: r[1] for r in conn.execute("SELECT id, formation FROM teams")}
        hist.append((y, len(ch)))
        yr_ranks = al._league_standings(c, season)
        for t, ten in ten_before.items():
            if t not in ranks:
                continue          # 순위가 없으면 애초에 판정 대상이 아니다
            if ten <= 1:
                hny_young += 1
                hny_young_sacked += (t in ch)
            else:
                hny_old += 1
                hny_old_sacked += (t in ch)
            # C — 그 해 순위 백분위를 경질/유임으로 나눠 누적
            if t in yr_ranks:
                _pct = yr_ranks[t][0] / yr_ranks[t][1]
                (c_sacked if t in ch else c_stayed).append(_pct)
    print(f"       연도별 교체 팀 수: " + ", ".join(f"{y}:{n}" for y, n in hist))

    # C 판정 — 경질된 팀이 실제로 더 부진했는가(목표가 박힌 회차 누적)
    if c_sacked and c_stayed:
        ms, mt = statistics.mean(c_sacked), statistics.mean(c_stayed)
        print(f"  C. 평균 순위 백분위 — 경질 {ms:.3f} (n={len(c_sacked)}) vs "
              f"유임 {mt:.3f} (n={len(c_stayed)})   1.0=최하위")
        if not (ms > mt + 0.03):
            problems.append(f"C: 경질된 팀이 더 부진하지 않음 ({ms:.3f} vs {mt:.3f})")

    # D 판정 — 같은 해 안에서 재임 1년 이하 vs 2년 이상
    if hny_young and hny_old:
        r_young = hny_young_sacked / hny_young
        r_old = hny_old_sacked / hny_old
        print(f"  D. 허니문 — 재임 1년 이하 {r_young * 100:.1f}% "
              f"(n={hny_young}) vs 2년 이상 {r_old * 100:.1f}% (n={hny_old})")
        if not (r_young < r_old):
            problems.append(
                f"D: 1년차가 더 안 잘림이 성립 안 함 "
                f"({r_young * 100:.1f}% vs {r_old * 100:.1f}%)")
    else:
        print("  D. 허니문 — 표본 부족으로 판정 제외")

    # ── A. 무결성 (다년 반복 후) ─────────────────────────────────
    ip, dup, none_, ov = _integrity(conn)
    problems += ip
    n_mgr = conn.execute("SELECT COUNT(*) FROM managers").fetchone()[0]
    n_link = conn.execute("SELECT COUNT(*) FROM team_managers").fetchone()[0]
    print(f"  A. 감독 {n_mgr}명 / 재임 이력 {n_link}건 "
          f"(현재 감독 중복 {dup}, 감독 없는 팀 {none_}, 구간 겹침 {ov})")

    # 재임 기간 분포
    ten = [r[0] for r in conn.execute(
        "SELECT end_year - start_year FROM team_managers WHERE end_year IS NOT NULL")]
    if ten:
        print(f"  A. 종료된 재임 {len(ten)}건 — 평균 {statistics.mean(ten):.2f}시즌 "
              f"/ 최대 {max(ten)}시즌")

    # ── E. 감독 교체 → 포메이션 연쇄 ─────────────────────────────
    # 마지막 회차에서 감독이 바뀐 팀이 포메이션도 바뀌었는지
    al_changed = ch
    if al_changed:
        forced = al._shuffle_formations(c, forced_teams=al_changed)
        conn.commit()
        after = {r[0]: r[1] for r in conn.execute("SELECT id, formation FROM teams")}
        moved = sum(1 for t in al_changed if after.get(t) != cur_forms.get(t))
        print(f"  E. 감독 바뀐 {len(al_changed)}팀 중 포메이션이 실제로 바뀐 팀 {moved}개")
        if moved == 0:
            problems.append("E: 감독이 바뀌었는데 포메이션이 한 팀도 안 바뀜")
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