# -*- coding: utf-8 -*-
"""GK 실전 QA 2차 — 챔스/컵 대진 환경(팀 수준별 상대 차등이 실제로 성립하는
대회) 검증. 리그는 라운드로빈이라 모든 팀이 동일 상대 세트를 만나 이 축이
성립하지 않으므로(1차 논의에서 확인됨), 대진이 실제로 갈리는 조별리그/
토너먼트로 옮겨서 본다.

고정: GK OVR85, 우리 팀 필드플레이어 OVR80, 상대 GK OVR80
가변:
  조별리그 — 약조(72) / 평균조(82) / 죽음의조(90)
  토너먼트 — 16강(80) / 8강(85) / 4강(90) / 결승(92)  (라운드가 올라갈수록 강한 상대)
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 프로젝트 루트(game_engine.py 위치) — qa_gk/ 하위 폴더에서 한 단계 위로

import game_engine  # noqa: E402
from match_sim import tactical_engine  # noqa: E402

FAKE_LEAGUE_ID = 999003
game_engine._league_tier_cache[FAKE_LEAGUE_ID] = 1  # 참고용, 실전 경로엔 영향 없음
_DUMMY_CURSOR = object()

GK_OVR = 85
MY_OUTFIELD_OVR = 80
OPP_GK_OVR = 80
N_MATCHES = 1000
SEED_BASE = 20260804

GROUP_STAGE = {"약조": 72, "평균조": 82, "죽음의조": 90}
KNOCKOUT = {"16강": 80, "8강": 85, "4강": 90, "결승": 92}


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


def build_lineup(gk_ovr, outfield_ovr):
    gk = make_gk(gk_ovr)
    of = make_outfield(outfield_ovr)
    return [gk] + [dict(of) for _ in range(10)]


def fmt_stats(vals):
    if not vals:
        return "n/a"
    return (f"평균 {statistics.mean(vals):.3f}  표준편차 {statistics.pstdev(vals):.3f}  "
            f"최소 {min(vals):.2f}  최대 {max(vals):.2f}")


def run_level(label, opp_outfield_ovr, seed_salt):
    real_conceded, real_sot, real_cs, real_savepct = [], [], [], []
    ratings, saves_list, mom_flags = [], [], []
    clean_sheet_ratings, hero_save_ratings = [], []

    for i in range(N_MATCHES):
        home_lineup = build_lineup(GK_OVR, MY_OUTFIELD_OVR)
        away_lineup = build_lineup(OPP_GK_OVR, opp_outfield_ovr)
        seed = SEED_BASE * 7919 + seed_salt * 977 + i
        result = tactical_engine.simulate_tactical_match(
            home_lineup, away_lineup, home_adv=3.0, seed=seed)
        hs, as_ = result["home_score"], result["away_score"]
        away_stats = result["away_stats"]

        sot_faced = away_stats["shots_on"]
        conceded = as_
        real_conceded.append(conceded)
        real_sot.append(sot_faced)
        real_cs.append(1 if conceded == 0 else 0)
        if sot_faced > 0:
            real_savepct.append((sot_faced - conceded) / sot_faced)

        gk_player = home_lineup[0]
        goals, assists, saves, rating, events, detail = game_engine._player_perf(
            gk_player, "win" if hs > as_ else ("draw" if hs == as_ else "loss"),
            True, hs, as_, c=_DUMMY_CURSOR, opp_ovr=opp_outfield_ovr, opp_sot=sot_faced)
        ratings.append(rating)
        saves_list.append(saves)
        mom_flags.append(1 if rating >= 8.0 else 0)
        if conceded == 0:
            clean_sheet_ratings.append(rating)
        if sot_faced >= 6 and conceded <= 1:
            hero_save_ratings.append(rating)

    return {
        "label": label, "real_conceded": real_conceded, "real_sot": real_sot,
        "real_cs": real_cs, "real_savepct": real_savepct, "ratings": ratings,
        "saves": saves_list, "mom_flags": mom_flags,
        "clean_sheet_ratings": clean_sheet_ratings, "hero_save_ratings": hero_save_ratings,
    }


def print_table(results, levels):
    header = f"{'구간':>8} | {'상대OVR':>7} | {'SOT':>6} | {'save%':>7} | {'실점/경기':>9} | {'클린시트%':>8} | {'평균평점':>8} | {'평균saves':>9} | {'MOM%':>6}"
    print(header)
    print("-" * len(header))
    for label, ovr in levels.items():
        r = results[label]
        sot = statistics.mean(r["real_sot"])
        savepct = statistics.mean(r["real_savepct"]) * 100 if r["real_savepct"] else 0
        conceded = statistics.mean(r["real_conceded"])
        cs = statistics.mean(r["real_cs"]) * 100
        avg_rating = statistics.mean(r["ratings"])
        avg_saves = statistics.mean(r["saves"])
        mom = statistics.mean(r["mom_flags"]) * 100
        print(f"{label:>8} | {ovr:7} | {sot:6.3f} | {savepct:6.2f}% | {conceded:9.3f} | "
              f"{cs:7.2f}% | {avg_rating:8.3f} | {avg_saves:9.3f} | {mom:5.2f}%")


def main():
    print(f"=== GK 실전 QA 2차 — 챔스/컵 대진 환경, GK OVR{GK_OVR} 고정, "
          f"{N_MATCHES}경기/구간 ===\n")

    print("── 조별리그 (약조/평균조/죽음의조) ──")
    group_results = {}
    for i, (label, ovr) in enumerate(GROUP_STAGE.items()):
        group_results[label] = run_level(label, ovr, seed_salt=i + 1)
    print_table(group_results, GROUP_STAGE)

    print("\n  핵심비교 (약조 클린시트 vs 죽음의조 고전방어):")
    easy_cs = group_results["약조"]["clean_sheet_ratings"]
    hard_hero = group_results["죽음의조"]["hero_save_ratings"]
    if easy_cs and hard_hero:
        e, h = statistics.mean(easy_cs), statistics.mean(hard_hero)
        print(f"  약조 클린시트 {e:.3f} (n={len(easy_cs)})  vs  죽음의조 고전방어 {h:.3f} (n={len(hard_hero)})")

    print("\n\n── 토너먼트 (16강→결승, 라운드 올라갈수록 강한 상대) ──")
    ko_results = {}
    for i, (label, ovr) in enumerate(KNOCKOUT.items()):
        ko_results[label] = run_level(label, ovr, seed_salt=i + 100)
    print_table(ko_results, KNOCKOUT)

    print("\n  라운드별 평점 흐름:", [round(statistics.mean(ko_results[l]["ratings"]), 3) for l in KNOCKOUT])
    print("  라운드별 MOM%:     ", [round(statistics.mean(ko_results[l]["mom_flags"]) * 100, 2) for l in KNOCKOUT])


if __name__ == "__main__":
    main()