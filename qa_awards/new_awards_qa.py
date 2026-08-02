# -*- coding: utf-8 -*-
"""올해의 수비수(Defender of the Year) / 구단 올해의 선수(Club Player of the
Year) 신설 어워드 QA — GK 발롱도르 E2E와 같은 방식(실제 seed된 game.db,
진짜 EPL 로스터+라운드로빈)으로 검증한다.

확인할 것:
  1) 올해의 수비수 — CB/LB 둘 다 받을 수 있는지, GK는 구조적으로(포지션
     게이트) 후보조차 못 되는지.
  2) 구단 올해의 선수 — ST/CAM 같은 공격 포지션이 아니어도(CM/CB/GK)
     내 팀 로스터 안에서는 핵심으로 평가받아 수상 가능한지. 포지션별
     승률을 찍어서 "공격수 독식"이 아닌지 확인.

내 팀(맨체스터 시티)은 SS등급 설계상 로스터 전원이 엘리트~월드클래스라
경쟁이 치열하다는 점을 감안하고 결과를 해석할 것 — GK QA 세션에서 이미
확인된 이 세계의 특성.
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qa_gk"))

import database  # noqa: E402
import game_engine  # noqa: E402
from match_sim import tactical_engine  # noqa: E402
from constants import FORMATION_SLOTS  # noqa: E402

from ballon_e2e_qa import (setup_world, find_ss_league_and_team,  # noqa: E402
                            build_team_lineup, simple_ai_vs_ai_score)

SEED = 20260807
MY_NAME = "QA테스트선수"


def make_field_player(position, ovr, name=MY_NAME):
    v = min(95, ovr)
    return {
        "position": position, "ovr": ovr, "shooting": v, "passing": v,
        "dribbling": v, "tackling": v, "heading": v, "setpiece": v,
        "speed": v, "positioning": v, "stamina": v, "sub_role": "",
        "name": name, "personality": "",
    }


def slot_index_for(formation, position):
    slots = FORMATION_SLOTS[formation]
    for i, s in enumerate(slots):
        if s == position:
            return i
    return None


def run_field_season(c, league_id, my_team_id, my_pos, my_ovr, season, year):
    """my_team_id 로스터에서 my_pos 슬롯 하나를 내 선수로 교체하고 라운드
    로빈 풀시즌(38경기)을 실제로 시뮬레이션 — 팀의 실제 저장 포메이션에
    my_pos가 없으면 스킵(None 반환)."""
    row = c.execute("SELECT formation FROM teams WHERE id=?", (my_team_id,)).fetchone()
    formation = (row["formation"] if row else None) or "4-4-2"
    if formation not in FORMATION_SLOTS:
        formation = "4-4-2"
    idx = slot_index_for(formation, my_pos)
    if idx is None:
        return None

    teams = [r["id"] for r in c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,)).fetchall()]
    if my_team_id not in teams:
        teams.append(my_team_id)

    my_player = make_field_player(my_pos, my_ovr)
    season_goals = season_assists = 0
    ratings = []
    season_cs = 0
    season_matches = 0

    pairs = list(itertools.permutations(teams, 2))
    for home_id, away_id in pairs:
        if home_id == my_team_id or away_id == my_team_id:
            is_home = (home_id == my_team_id)
            opp_id = away_id if is_home else home_id

            def _build(team_id, mine):
                lu = build_team_lineup(c, team_id)
                if mine:
                    lu[idx] = my_player
                return lu

            home_lineup = _build(my_team_id, True) if is_home else _build(opp_id, False)
            away_lineup = _build(opp_id, False) if is_home else _build(my_team_id, True)
            seed = SEED * 7919 + league_id * 131 + home_id * 7 + away_id * 3 + season * 97
            result = tactical_engine.simulate_tactical_match(
                home_lineup, away_lineup, home_adv=3.0, seed=seed)
            hs, as_ = result["home_score"], result["away_score"]
            my_conceded = as_ if is_home else hs
            opp_lineup = away_lineup if is_home else home_lineup
            opp_ovr = sum(x["ovr"] for x in opp_lineup if x) / max(1, len([x for x in opp_lineup if x]))
            outcome = "win" if (hs > as_) == is_home else ("draw" if hs == as_ else "loss")
            goals, assists, sv, rating, events, detail = game_engine._player_perf(
                my_player, outcome, is_home, hs, as_, c=c, opp_ovr=opp_ovr)
            season_goals += goals
            season_assists += assists
            ratings.append(rating)
            season_matches += 1
            if my_conceded == 0:
                season_cs += 1
        else:
            hs, as_ = simple_ai_vs_ai_score(c, home_id, away_id)
        c.execute("""INSERT INTO match_results(league_id, season, home_team_id, away_team_id,
                     home_score, away_score) VALUES(?,?,?,?,?,?)""",
                  (league_id, season, home_id, away_id, hs, as_))

    season_rating = round(sum(ratings) / len(ratings), 2) if ratings else 6.0
    return {
        "season_goals": season_goals, "season_assists": season_assists,
        "season_rating": season_rating, "season_cs": season_cs, "season_matches": season_matches,
    }


def run_position_trials(label, position, ovr, n_seasons):
    conn = database.get_conn()
    c = conn.cursor()
    league_id, team_id, lname, tname = find_ss_league_and_team(c)

    doty_count = 0
    club_poty_count = 0
    skipped = 0
    print(f"── {label}: {position} OVR{ovr}, {n_seasons}시즌 ──")
    for s in range(n_seasons):
        year = 2040 + (hash(label) % 500) + s
        season = s + 1

        game_engine.create_player(MY_NAME, position, "", talent_tier="worldclass")
        game_engine.update_player(ovr=ovr, current_team_id=team_id, current_league_id=league_id,
                                   current_year=year, position=position, age=27)

        stats = run_field_season(c, league_id, team_id, position, ovr, season, year)
        if stats is None:
            print(f"  [시즌{season}] 이 팀 포메이션엔 {position} 슬롯이 없음 — 스킵")
            skipped += 1
            c.execute("DELETE FROM my_player")
            continue
        game_engine.update_player(season_matches=stats["season_matches"])
        conn.commit()

        game_engine._process_awards(
            game_engine.get_player(), year,
            season_goals=stats["season_goals"], season_assists=stats["season_assists"],
            season_rating=stats["season_rating"], season_cs=stats["season_cs"],
            season_goals_against=0)

        rows = c.execute("SELECT award_type FROM awards WHERE year=? AND is_mine=1", (year,)).fetchall()
        types = [r["award_type"] for r in rows]
        doty = "올해의 수비수" in types
        club_poty = "구단 올해의 선수" in types
        if doty:
            doty_count += 1
        if club_poty:
            club_poty_count += 1
        print(f"  [시즌{season}] G{stats['season_goals']} A{stats['season_assists']} "
              f"평점{stats['season_rating']} CS{stats['season_cs']}  → {types or '(수상없음)'}")

        c.execute("DELETE FROM my_player")
        c.execute("DELETE FROM match_results WHERE league_id=? AND season=?", (league_id, season))
        c.execute("DELETE FROM awards WHERE year=? AND is_mine=1", (year,))
        conn.commit()

    n_ran = n_seasons - skipped
    if n_ran:
        print(f"  === {label} 요약: 올해의수비수 {doty_count}/{n_ran}  "
              f"구단올해의선수 {club_poty_count}/{n_ran} ===\n")
    else:
        print(f"  === {label}: 전 시즌 스킵(포메이션에 해당 포지션 없음) ===\n")
    return doty_count, club_poty_count, n_ran


def main():
    setup_world()
    print("=== 올해의 수비수 / 구단 올해의 선수 QA ===\n")

    N = 5
    results = {}
    for pos, ovr in [("CB", 99), ("LB", 93), ("CM", 95), ("CAM", 96), ("ST", 96), ("GK", 95)]:
        results[pos] = run_position_trials(f"포지션 {pos}", pos, ovr, N)

    print("── 요약 ──")
    print(f"{'포지션':>6} | {'올해의수비수':>10} | {'구단올해의선수':>10} | 시즌수")
    for pos, (doty, club, n) in results.items():
        print(f"{pos:>6} | {doty:>10} | {club:>10} | {n}")


if __name__ == "__main__":
    main()