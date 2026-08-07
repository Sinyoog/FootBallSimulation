# -*- coding: utf-8 -*-
"""
analyze_relegation.py — "전 시즌 순위 -> 다음 시즌 결과" 전이표 분석

목적: "전 시즌 2위인데 다음 시즌 강등"이 실제로 얼마나 자주 나오는지,
      감이 아니라 game.db에 이미 쌓인 실제 시뮬레이션 결과로 확인한다.

게임 코드(database.py/game_engine.py)에 전혀 의존하지 않는다 — 순수 sqlite3만
써서 game.db를 읽기 전용으로 열기 때문에, 게임이 켜져 있어도(인메모리 라이브
DB와는 별개로 디스크의 game.db 스냅샷을 보는 것이므로) 안전하게 실행 가능하다.
단, 방금 몇 주를 진행하고 아직 자동저장 전이면 그만큼은 game.db에 안 반영돼
있을 수 있다 — 정확도를 높이려면 저장 직후에 돌리는 걸 추천.

사용법:
    python analyze_relegation.py "C:\\Users\\admin\\Desktop\\FootBallSimulation-main\\game.db"
    (인자를 안 주면 스크립트와 같은 폴더의 game.db를 찾는다)
"""
import sqlite3
import sys
import os
from collections import defaultdict


def _bucket(rank, n):
    """순위를 사람이 읽기 좋은 구간으로 묶는다. 리그마다 팀 수(n)가 달라서
    절대순위 1~4위는 그대로, 그 밑은 리그 크기 대비 백분율로 나눈다."""
    if rank == 1:
        return "1위"
    if rank == 2:
        return "2위"
    if rank in (3, 4):
        return "3~4위"
    pct = rank / n if n else 1.0
    if pct <= 6 / 18:
        return "5~6위대(챔스권)"
    if pct <= 10 / 18:
        return "중위권"
    if pct <= 14 / 18:
        return "하위권"
    return "강등권"


def main(db_path):
    if not os.path.exists(db_path):
        print(f"파일을 못 찾음: {db_path}")
        return

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    league_tier = {r["id"]: r["tier"] for r in c.execute("SELECT id, tier FROM leagues")}
    team_name = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM teams")}

    rows = c.execute("""
        SELECT season, league_id, team_id, wins, draws, losses, goals_for, goals_against
        FROM league_season_standings
    """).fetchall()
    if not rows:
        print("league_season_standings가 비어있음 — 아직 시즌이 한 번도 아카이브 안 된 상태일 수 있음.")
        conn.close()
        return

    by_season_league = defaultdict(list)
    for r in rows:
        pts = r["wins"] * 3 + r["draws"]
        gd = r["goals_for"] - r["goals_against"]
        by_season_league[(r["season"], r["league_id"])].append(
            {"team_id": r["team_id"], "pts": pts, "gd": gd, "gf": r["goals_for"]})

    # {(season, team_id): (rank, n_teams, tier, league_id)}
    team_rank = {}
    for (season, league_id), teams in by_season_league.items():
        teams.sort(key=lambda t: (-t["pts"], -t["gd"], -t["gf"]))
        n = len(teams)
        tier = league_tier.get(league_id)
        for i, t in enumerate(teams, start=1):
            team_rank[(season, t["team_id"])] = (i, n, tier, league_id)

    seasons = sorted({s for s, _tid in team_rank.keys()})
    if len(seasons) < 2:
        print("season이 1개뿐이라 전이표를 못 뽑음 — 최소 2시즌 이상 진행 필요.")
        conn.close()
        return

    # bucket -> {count, next_rank_pct_sum, releg_count}
    stats = defaultdict(lambda: {"n": 0, "next_pct_sum": 0.0, "releg": 0})
    two_to_releg = []   # 2위인데 다음 시즌 강등된 사례 상세

    for season in seasons:
        nxt = season + 1
        for (s, tid), (rank, n, tier, lid) in team_rank.items():
            if s != season:
                continue
            nxt_info = team_rank.get((nxt, tid))
            if nxt_info is None:
                continue  # 그 팀이 다음 시즌 기록이 없음(폐지 등, 드묾) — 스킵
            nxt_rank, nxt_n, nxt_tier, nxt_lid = nxt_info
            b = _bucket(rank, n)
            st = stats[b]
            st["n"] += 1
            st["next_pct_sum"] += nxt_rank / nxt_n if nxt_n else 1.0
            is_releg = nxt_tier is not None and tier is not None and nxt_tier > tier
            if is_releg:
                st["releg"] += 1
            if rank == 2 and is_releg:
                two_to_releg.append({
                    "team": team_name.get(tid, f"id{tid}"), "season": season,
                    "next_season": nxt, "prev_tier": tier, "next_tier": nxt_tier,
                    "next_rank": nxt_rank, "next_n": nxt_n,
                })

    print(f"분석 대상: {seasons[0]}~{seasons[-1]}시즌 (총 {len(seasons)}개), "
          f"team-season 페어 {sum(v['n'] for v in stats.values())}건\n")

    print(f"{'전 시즌 순위':<16}{'표본수':>8}{'다음시즌 평균순위(백분율)':>26}{'강등률':>10}")
    print("-" * 62)
    order = ["1위", "2위", "3~4위", "5~6위대(챔스권)", "중위권", "하위권", "강등권"]
    for b in order:
        st = stats.get(b)
        if not st or st["n"] == 0:
            continue
        avg_pct = st["next_pct_sum"] / st["n"] * 100
        releg_rate = st["releg"] / st["n"] * 100
        print(f"{b:<16}{st['n']:>8}{avg_pct:>24.1f}%{releg_rate:>9.1f}%")

    print(f"\n'2위 -> 다음 시즌 강등' 사례: {len(two_to_releg)}건 "
          f"(2위 전체 표본 {stats['2위']['n']}건 중)")
    if two_to_releg:
        print("\n상세 (팀 / 시즌 / 이전 티어->다음 티어 / 다음 시즌 순위):")
        for e in two_to_releg[:30]:
            print(f"  {e['team']:<20} {e['season']}->{e['next_season']}시즌  "
                  f"{e['prev_tier']}부->{e['next_tier']}부  "
                  f"다음시즌 {e['next_rank']}/{e['next_n']}위")
        if len(two_to_releg) > 30:
            print(f"  ... 외 {len(two_to_releg)-30}건 더")

    conn.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "game.db")
    main(path)