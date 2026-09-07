# -*- coding: utf-8 -*-
"""발롱도르 GK end-to-end QA — 고립 로직이 아니라 실제 seed된 게임 월드
(countries/leagues/teams/ai_players) 위에서 진행한다.

world_class(라이벌 OVR 비교)와 trophy_bonus(리그순위/CL/컵 성적)는 실제 DB
테이블을 읽으므로, 지금까지처럼 함수를 고립시켜 호출하는 방식으로는 검증이
안 된다 — 이번엔 진짜 리그 시즌(라운드로빈)을 match_results에 채워 넣고,
실제 _process_awards()가 그 데이터를 보고 어떻게 판정하는지 그대로 본다.

Case A: 우승팀 GK (리그 우승 + 챔스 우승 가정)
Case B: 중위권 GK (리그 6~8위, 대회 실적 없음)
Case C: 약팀 슈퍼세이브 GK (강등권, 개인 활약은 최상위)

측정: world_class / high_rating / trophy_bonus / season_cs / dominant / ballon
      여부를 그대로 재현해서 출력한다(내부 계산과 100% 동일한 조건으로
      호출 직전 값을 그대로 조회 — 별도 함수 재구현 없이 실제 함수가 커밋한
      awards 테이블 + 계산에 쓰인 원재료(DB row)를 읽는 방식).
"""
import itertools
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import game_engine  # noqa: E402
from match_sim import tactical_engine  # noqa: E402
from constants import FORMATION_SLOTS  # noqa: E402

SEED = 20260806
random.seed(SEED)

GK_NAME = "QA테스트GK"


def make_gk_dict(ovr, name=GK_NAME):
    return {
        "position": "GK", "ovr": ovr, "positioning": ovr, "concentration": ovr,
        "jump": ovr, "passing": max(40, ovr - 10), "sub_role": "",
        "name": name,
    }


def ai_row_to_player_dict(row):
    return {k: row[k] for k in row.keys()}


def build_team_lineup(c, team_id, override_gk=None):
    """실제 ai_players에서 그 팀의 실제 저장된 포메이션(_team_formation과 동일한
    컬럼)에 맞춰 포지션별로 OVR 최고 선수를 뽑아 라인업 구성.
    [주의] 4-4-2로 강제 고정했더니, 실 로스터가 포지션당 1명씩만 있는
    시드 데이터라(CAM/CDM/LW/RW는 있는데 LM/RM/2번째 CM은 없는 구성) 수비
    슬롯까지 엉뚱한 포지션 선수로 채워져 실점이 비정상적으로 치솟는 문제가
    있었다 — 그 팀이 실제로 쓰는 포메이션(대개 로스터 구성과 맞음)을 그대로
    쓰면 이 미스매치가 사라진다.
    override_gk가 있으면 GK 슬롯을 그걸로 교체(내 선수)."""
    row = c.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
    formation = (row["formation"] if row else None) or "4-4-2"
    if formation not in FORMATION_SLOTS:
        formation = "4-4-2"
    slots = FORMATION_SLOTS[formation]
    rows = c.execute("SELECT * FROM ai_players WHERE team_id=?", (team_id,)).fetchall()
    by_pos = {}
    for r in rows:
        by_pos.setdefault(r["position"], []).append(dict(r))
    used_ids = set()
    lineup = []
    for i, slot in enumerate(slots):
        if i == 0 and override_gk is not None:
            lineup.append(override_gk)
            continue
        cands = sorted(by_pos.get(slot, []), key=lambda x: -x["ovr"])
        cands = [x for x in cands if x.get("id") not in used_ids]
        if not cands:
            # 포지션에 안 맞으면 아무 선수나(라인업 채우기 목적, 흔치 않음)
            all_left = sorted([x for l in by_pos.values() for x in l if x.get("id") not in used_ids],
                               key=lambda x: -x["ovr"])
            cands = all_left
        if cands:
            picked = cands[0]
            used_ids.add(picked.get("id"))
            lineup.append(picked)
        else:
            lineup.append(None)
    return lineup


def simple_ai_vs_ai_score(c, home_id, away_id):
    """내 팀이 관여 안 하는 경기는 가볍게 OVR차 기반으로만 스코어 생성
    (표준 편차 로직을 재구현하지 않고, 리그 표를 그럴듯하게 채우는 용도)."""
    h = c.execute("SELECT AVG(ovr) v FROM ai_players WHERE team_id=?", (home_id,)).fetchone()["v"] or 65
    a = c.execute("SELECT AVG(ovr) v FROM ai_players WHERE team_id=?", (away_id,)).fetchone()["v"] or 65
    diff = h - a + 2  # 홈 어드밴티지
    base_h = max(0.3, 1.3 + diff * 0.05)
    base_a = max(0.3, 1.1 - diff * 0.05)
    hs = min(6, random.choices(range(0, 7), weights=[max(0.5, 3 - abs(k - base_h)) for k in range(7)])[0])
    as_ = min(6, random.choices(range(0, 7), weights=[max(0.5, 3 - abs(k - base_a)) for k in range(7)])[0])
    return hs, as_


def run_season_for_league(c, league_id, my_team_id, my_gk_ovr, season, year, my_dominant_team=True):
    """my_team_id를 포함한 리그의 라운드로빈(홈/원정 2연전)을 전부
    match_results에 채운다. 내 팀이 등장하는 경기는 전술엔진+_player_perf로
    진짜 개인 기록을 만들고, 나머지는 간단 스코어로 채운다.
    my_dominant_team=True면 내 팀 라인업이 리그 최강이 되도록 outfield도
    올려친다(우승 시나리오용)."""
    teams = [r["id"] for r in c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,)).fetchall()]
    if my_team_id not in teams:
        teams.append(my_team_id)

    season_saves = 0
    season_goals_against = 0
    ratings = []
    season_cs = 0
    season_matches = 0

    my_gk = make_gk_dict(my_gk_ovr)

    pairs = list(itertools.permutations(teams, 2))  # 홈/원정 각 1번씩 (완전 라운드로빈)
    for home_id, away_id in pairs:
        if home_id == my_team_id or away_id == my_team_id:
            is_home = (home_id == my_team_id)
            opp_id = away_id if is_home else home_id
            home_lineup = build_team_lineup(c, my_team_id, override_gk=my_gk) if is_home \
                else build_team_lineup(c, opp_id)
            away_lineup = build_team_lineup(c, opp_id) if is_home \
                else build_team_lineup(c, my_team_id, override_gk=my_gk)
            seed = SEED * 7919 + league_id * 131 + home_id * 7 + away_id * 3 + season * 97
            result = tactical_engine.simulate_tactical_match(
                home_lineup, away_lineup, home_adv=3.0, seed=seed)
            hs, as_ = result["home_score"], result["away_score"]
            my_conceded = as_ if is_home else hs
            my_sot = (result["away_stats"]["shots_on"] if is_home
                      else result["home_stats"]["shots_on"])
            opp_ovr = sum(x["ovr"] for x in (away_lineup if is_home else home_lineup) if x) / 10.0
            goals, assists, sv, rating, events, detail = game_engine._player_perf(
                my_gk, "win" if (hs > as_) == is_home else ("draw" if hs == as_ else "loss"),
                is_home, hs, as_, c=c, opp_ovr=opp_ovr, opp_sot=my_sot)
            season_saves += sv
            season_goals_against += my_conceded
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
        "season_saves": season_saves, "season_goals_against": season_goals_against,
        "season_rating": season_rating, "season_cs": season_cs, "season_matches": season_matches,
    }


def setup_world():
    print("월드 시딩 중(국가/리그/팀/전세계 선수단)... 1~2분 정도 걸릴 수 있음")
    database.init_db()
    database.seed_initial_data()
    print("시딩 완료.\n")


def find_ss_league_and_team(c):
    """SS등급(=COUNTRY_LEAGUE_GRADE 기준 '잉글랜드') tier1 리그 하나와
    그 안의 한 팀(내 소속팀으로 쓸)을 고른다. countries.grade DB 컬럼이
    아니라 get_league_grade()가 실제로 참조하는 COUNTRY_LEAGUE_GRADE
    딕셔너리 기준이라 국가명으로 직접 찾는다."""
    row = c.execute("""SELECT l.id as lid, t.id as tid, l.name as lname, t.name as tname
        FROM leagues l JOIN countries cn ON l.country_id=cn.id
        JOIN teams t ON t.league_id=l.id
        WHERE cn.name='잉글랜드' AND l.tier=1
        ORDER BY t.id LIMIT 1""").fetchone()
    return row["lid"], row["tid"], row["lname"], row["tname"]


def diagnose_ballon(c, p, year, tid, season_rating, season_cs, season_matches):
    """발롱도르 판정에 실제로 쓰이는 값들을 그대로(재구현 없이) 조회해서 보여준다.
    trophy_bonus는 game_engine._get_ballon_trophy_bonus()를 그대로 호출 —
    프로덕션 로직과 100% 동일. world_class 비교용 rival_ovr도 _process_awards
    내부와 동일한 쿼리를 그대로 재현(GK_POS, grade IN ('SS','S'), tier=1)."""
    from constants import GK_POS
    other = c.execute("""SELECT MAX(a.ovr) as mo FROM ai_players a
        JOIN teams t ON a.team_id=t.id
        JOIN leagues l ON t.league_id=l.id
        JOIN countries cn ON l.country_id=cn.id
        WHERE cn.grade IN ('SS','S') AND l.tier=1 AND a.position IN ({})
        """.format(",".join("'%s'" % pp for pp in GK_POS))).fetchone()
    rival_ovr = other["mo"] if other and other["mo"] else 90
    my_ovr = p.get("ovr", 0)
    world_class = my_ovr >= rival_ovr - 2
    trophy_bonus = game_engine._get_ballon_trophy_bonus(year, tid)
    high_rating = season_rating >= 6.7  # GK 전용 게이트(2차 재보정값)
    cs_needed = max(10, round(0.316 * season_matches))
    print(f"  [진단] 내 OVR {my_ovr}  라이벌GK최고OVR {rival_ovr}  world_class={world_class}")
    print(f"  [진단] trophy_bonus={trophy_bonus:.2f} (게이트 2.0 이상 필요)  "
          f"high_rating({season_rating}>=6.7)={high_rating}  "
          f"season_cs {season_cs} vs 필요 {cs_needed} → {'충족' if season_cs >= cs_needed else '미달'}")


def run_case(label, gk_ovr, make_dominant, cl_won, cup_won, add_intl_win, n_seasons=1):
    conn = database.get_conn()
    c = conn.cursor()
    league_id, team_id, lname, tname = find_ss_league_and_team(c)

    print(f"── {label} (GK OVR{gk_ovr}, {lname}/{tname}, {n_seasons}시즌) ──")
    pass_count = 0
    golden_count = 0
    for s in range(n_seasons):
        year = 2030 + (hash(label) % 500) + s  # 케이스/시즌별로 겹치지 않는 연도
        season = s + 1

        game_engine.create_player(GK_NAME, "GK", "전통형", talent_tier="worldclass")
        game_engine.update_player(ovr=gk_ovr, current_team_id=team_id, current_league_id=league_id,
                                   current_year=year, position="GK", age=27,
                                   positioning=gk_ovr, concentration=gk_ovr, jump=gk_ovr,
                                   passing=max(40, gk_ovr - 10))

        stats = run_season_for_league(c, league_id, team_id, gk_ovr, season, year)
        # [버그수정] _process_awards가 sm(=p["season_matches"])을 읽어서
        # MVP/올해의 수비수/구단 올해의 선수 출전비율 게이트, 그리고 발롱도르
        # play_ratio/_played_equiv 계산에도 쓴다 — 시즌 시뮬레이션 결과를
        # 실제 my_player 행에 반영 안 하면 이 값이 항상 0으로 읽혀서 게이트
        # 계산 전체가 왜곡된다(발롱도르 cs_needed/min_ga가 부당하게 낮아짐).
        game_engine.update_player(season_matches=stats["season_matches"])

        if cl_won:
            c.execute("INSERT INTO cl_tournaments(year, my_in, my_result) VALUES(?,1,?)", (year, "우승"))
        if cup_won:
            c.execute("INSERT INTO cup_tournaments(year, my_in, my_result) VALUES(?,1,?)", (year, "우승"))
        if add_intl_win:
            c.execute("""INSERT INTO intl_tournaments(year, my_selected, my_result, kind, continent)
                         VALUES(?,1,?,?,?)""", (year, "우승", "world", ""))
        conn.commit()

        print(f"  [시즌{season}] 평점 {stats['season_rating']}  saves {stats['season_saves']}  "
              f"CS {stats['season_cs']}/{stats['season_matches']}  실점 {stats['season_goals_against']}")
        diagnose_ballon(c, game_engine.get_player(), year, team_id,
                         stats["season_rating"], stats["season_cs"], stats["season_matches"])

        try:
            game_engine._process_awards(
                game_engine.get_player(), year,
                season_goals=0, season_assists=0,
                season_rating=stats["season_rating"], season_cs=stats["season_cs"],
                season_goals_against=stats["season_goals_against"])
        except Exception as e:
            print(f"  ⚠️ _process_awards 예외: {e!r}")

        rows = c.execute("SELECT award_type, detail FROM awards WHERE year=? AND is_mine=1", (year,)).fetchall()
        for r in rows:
            print(f"    🏅 {r['award_type']}: {r['detail']}")
        ballon = any(r["award_type"] == "발롱도르" for r in rows)
        golden_glove = any(r["award_type"] == "골든글러브" for r in rows)
        if ballon:
            pass_count += 1
        if golden_glove:
            golden_count += 1
        print(f"    → 발롱도르 {'✅' if ballon else '❌'}  골든글러브 {'✅' if golden_glove else '❌'}")

        # 다음 시즌을 위해 내 선수 데이터만 정리(월드는 유지)
        c.execute("DELETE FROM my_player")
        c.execute("DELETE FROM match_results WHERE league_id=? AND season=?", (league_id, season))
        c.execute("DELETE FROM awards WHERE year=? AND is_mine=1", (year,))
        conn.commit()

    print(f"  === {label} 요약: 발롱도르 {pass_count}/{n_seasons}  골든글러브 {golden_count}/{n_seasons} ===\n")


def main():
    setup_world()
    print("=== 발롱도르 GK End-to-End QA ===\n")

    run_case("Case A: 우승팀 GK, 진짜 세계 최고 OVR99 (리그+챔스 우승)", gk_ovr=99, make_dominant=True,
              cl_won=True, cup_won=False, add_intl_win=False, n_seasons=8)
    run_case("Case B: 중위권 GK (대회 실적 없음)", gk_ovr=95, make_dominant=False,
              cl_won=False, cup_won=False, add_intl_win=False, n_seasons=3)
    run_case("Case C: 무관 GK (개인 OVR만 최상위, 트로피 0)", gk_ovr=90, make_dominant=False,
              cl_won=False, cup_won=False, add_intl_win=False, n_seasons=3)


if __name__ == "__main__":
    main()