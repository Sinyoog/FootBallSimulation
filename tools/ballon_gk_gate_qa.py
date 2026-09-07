# -*- coding: utf-8 -*-
"""발롱도르 GK 게이트 사전 점검 — BALLON_MIN_RATING(7.4)에 실제로 도달
가능한지부터 확인한다. `_process_awards`의 `_def_dominant_stats` 경로는
season_cs 문턱과 별개로 `high_rating`(시즌 평균 평점 >= 7.4)을 반드시
통과해야 하는데, GK QA에서 확인된 "환경이 어려울수록 평균평점이 낮아지는"
현상과 정면으로 부딪히는 지점이라 이것부터 확인.

시나리오: SS/S급 엘리트 리그를 흉내낸 시즌(38경기) — 상대 필드 OVR을
매 경기 정규분포(평균82, 표준편차6, 65~95 클램프)로 뽑아 리그 내 팀별
전력차를 재현. GK OVR은 90/92/95 세 구간(발롱도르 논의 대상이 될 만한
최상위권)으로 비교. ST(비교용, 같은 조건)도 같이 찍어서 상대적 격차를 본다.
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 프로젝트 루트(game_engine.py 위치) — qa_gk/ 하위 폴더에서 한 단계 위로

import game_engine  # noqa: E402
from match_sim import tactical_engine  # noqa: E402

FAKE_LEAGUE_ID = 999004
game_engine._league_tier_cache[FAKE_LEAGUE_ID] = 1
_DUMMY_CURSOR = object()

MY_OUTFIELD_OVR = 85          # 내 팀도 SS/S급 강팀이라 필드플레이어도 높게
OPP_GK_OVR = 85
GAMES_PER_SEASON = 38
N_SEASONS = 200                # 시즌 200회 = 7600경기, 분산 충분히 안정적
SEED_BASE = 20260805
BALLON_MIN_RATING = 7.4        # game_engine.py의 실제 상수와 동일 (ST 등 비-GK용)
GK_BALLON_MIN_RATING = 6.7     # [2차 재보정] E2E 검증 후 6.9→6.7


def sample_opp_ovr(rng):
    v = rng.gauss(82, 6)
    return max(65, min(95, v))


def make_outfield(ovr):
    v = min(95, ovr)
    return {
        "shooting": v, "dribbling": v, "passing": v, "tackling": v,
        "positioning": v, "strength": v, "stamina": v, "heading": v,
        "speed": v, "concentration": v, "jump": v, "ovr": ovr,
    }


def make_gk(ovr):
    return {
        "position": "GK", "ovr": ovr, "positioning": ovr, "concentration": ovr,
        "jump": ovr, "passing": max(40, ovr - 10), "sub_role": "",
        "current_league_id": FAKE_LEAGUE_ID, "current_team_id": 0,
    }


def build_lineup(is_gk_test, my_ovr, opp_outfield_ovr):
    if is_gk_test:
        gk = make_gk(my_ovr)
        of = make_outfield(MY_OUTFIELD_OVR)
        home = [gk] + [dict(of) for _ in range(10)]
    else:
        of_me = make_outfield(MY_OUTFIELD_OVR)  # 팀은 SS/S급 평균으로 고정
        home = [make_gk(OPP_GK_OVR)] + [dict(of_me) for _ in range(10)]
    away = [make_gk(OPP_GK_OVR)] + [make_outfield(opp_outfield_ovr) for _ in range(10)]
    return home, away


def run_gk_seasons(gk_ovr):
    rng = random.Random(SEED_BASE + gk_ovr)
    season_avg_ratings, season_cs_list, season_saves_list = [], [], []
    for s in range(N_SEASONS):
        ratings, cs, saves_sum = [], 0, 0
        for g in range(GAMES_PER_SEASON):
            opp_ovr = sample_opp_ovr(rng)
            home_lineup, away_lineup = build_lineup(True, gk_ovr, opp_ovr)
            seed = SEED_BASE * 7919 + gk_ovr * 131 + s * 97 + g
            result = tactical_engine.simulate_tactical_match(
                home_lineup, away_lineup, home_adv=3.0, seed=seed)
            hs, as_ = result["home_score"], result["away_score"]
            sot = result["away_stats"]["shots_on"]
            gk_player = home_lineup[0]
            goals, assists, sv, rating, events, detail = game_engine._player_perf(
                gk_player, "win" if hs > as_ else ("draw" if hs == as_ else "loss"),
                True, hs, as_, c=_DUMMY_CURSOR, opp_ovr=opp_ovr, opp_sot=sot)
            ratings.append(rating)
            saves_sum += sv
            if as_ == 0:
                cs += 1
        season_avg_ratings.append(statistics.mean(ratings))
        season_cs_list.append(cs)
        season_saves_list.append(saves_sum)
    return season_avg_ratings, season_cs_list, season_saves_list


def run_st_seasons(st_ovr):
    """비교 기준용 — ST가 같은 조건(SS/S 리그, 38경기)에서 시즌 평균평점이
    어느 정도 나오는지. game_engine._player_perf 필드플레이어 분기는
    outcome/hs/as_ 기반으로 이미 완결돼 있어 opp_sot 불필요."""
    rng = random.Random(SEED_BASE + 500 + st_ovr)
    season_avg_ratings = []
    for s in range(N_SEASONS):
        ratings = []
        for g in range(GAMES_PER_SEASON):
            opp_ovr = sample_opp_ovr(rng)
            hs = rng.randint(0, 4)
            as_ = rng.randint(0, 3)
            st_player = {
                "position": "ST", "ovr": st_ovr, "shooting": min(95, st_ovr),
                "positioning": min(95, st_ovr), "heading": min(95, st_ovr),
                "dribbling": min(95, st_ovr), "passing": min(95, st_ovr - 5),
                "sub_role": "", "current_league_id": FAKE_LEAGUE_ID,
                "current_team_id": 0,
            }
            goals, assists, sv, rating, events, detail = game_engine._player_perf(
                st_player, "win" if hs > as_ else ("draw" if hs == as_ else "loss"),
                True, hs, as_, c=_DUMMY_CURSOR, opp_ovr=opp_ovr)
            ratings.append(rating)
        season_avg_ratings.append(statistics.mean(ratings))
    return season_avg_ratings


def main():
    print(f"=== 발롱도르 GK 게이트 사전점검 — BALLON_MIN_RATING={BALLON_MIN_RATING} ===")
    print(f"(SS/S급 리그 흉내: 상대 필드 OVR ~N(82,6), {GAMES_PER_SEASON}경기/시즌, "
          f"{N_SEASONS}시즌)\n")

    print("── GK ──")
    for gk_ovr in (90, 92, 95):
        ratings, cs_list, saves_list = run_gk_seasons(gk_ovr)
        avg = statistics.mean(ratings)
        std = statistics.pstdev(ratings)
        over_gate_old = sum(1 for r in ratings if r >= BALLON_MIN_RATING) / len(ratings) * 100
        over_gate_new = sum(1 for r in ratings if r >= GK_BALLON_MIN_RATING) / len(ratings) * 100
        avg_cs = statistics.mean(cs_list)
        print(f"  GK OVR{gk_ovr}: 시즌평균평점 {avg:.3f} (표준편차 {std:.3f}, "
              f"최대 {max(ratings):.3f})  |  구게이트(7.4) {over_gate_old:.1f}%  |  "
              f"신게이트(6.9) {over_gate_new:.1f}%  |  평균 클린시트 {avg_cs:.1f}/{GAMES_PER_SEASON}")

    print("\n── ST (비교 기준) ──")
    for st_ovr in (90, 92, 95):
        ratings = run_st_seasons(st_ovr)
        avg = statistics.mean(ratings)
        std = statistics.pstdev(ratings)
        over_gate = sum(1 for r in ratings if r >= BALLON_MIN_RATING) / len(ratings) * 100
        print(f"  ST OVR{st_ovr}: 시즌평균평점 {avg:.3f} (표준편차 {std:.3f}, "
              f"최대 {max(ratings):.3f})  |  7.4 게이트 통과 시즌비율 {over_gate:.1f}%")


if __name__ == "__main__":
    main()