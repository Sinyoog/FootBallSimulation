# -*- coding: utf-8 -*-
"""GK 실전 QA 1차 — '상대 공격 수준 → GK 난이도' 구조 확인.

구조수정(opp_sot 연결) 이후, GK 평점 계산에서 _tier 상수는 죽은 코드가
됐다(opp_sot가 항상 주어지는 실전 경로에선 _sr_min/_sr_max가 아예 안 쓰임).
난이도의 책임이 "리그 tier 상수"에서 "상대 필드 플레이어의 실제 OVR
(shooting 등 스탯)"로 넘어갔으므로, 이제 검증 대상도 그쪽으로 옮긴다.

고정: 홈 GK OVR85, 홈 필드플레이어 OVR80
가변: 원정 필드플레이어(=GK가 상대하는 공격진) OVR 65 / 80 / 90 (원정 GK는
      OVR80 고정 — 우리 편 공격 결과엔 관심 없고 상대 공격력만 본다)

측정:
  1) 상대 OVR ↑ → 실제 SOT ↑ (당연히 나와야 함)
  2) 상대 OVR ↑ → save% ↓ (강한 슈터 상대라 막기 어려워짐 — 자연스러운 하락)
  3) 상대 OVR ↑ → 평점이 "쉬운 경기보다 낮게 나오는지, 아니면 난이도를
     반영해서 비슷하거나 오히려 값지게 나오는지" — 평점 공식이 난이도를
     못 보고 있으면(약팀 상대 클린시트가 강팀 상대 슈퍼세이브보다 항상
     높게 나오면) 문제로 본다.
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 프로젝트 루트(game_engine.py 위치) — qa_gk/ 하위 폴더에서 한 단계 위로

import game_engine  # noqa: E402
from match_sim import tactical_engine  # noqa: E402

FAKE_LEAGUE_ID = 999002
game_engine._league_tier_cache[FAKE_LEAGUE_ID] = 2  # 이제 실전 경로엔 영향 없음(참고용)
_DUMMY_CURSOR = object()

GK_OVR = 85
MY_OUTFIELD_OVR = 80
OPP_GK_OVR = 80
OPP_OUTFIELD_LEVELS = [65, 80, 90]  # 약팀 / 평균 / 강팀 리그 대리
N_MATCHES = 1000
SEED_BASE = 20260803


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


def run_level(opp_outfield_ovr):
    real_conceded, real_sot, real_cs, real_savepct = [], [], [], []
    ratings, saves_list, mom_flags = [], [], []
    # "쉬운 경기(클린시트)" vs "어려운 경기(슈퍼세이브)" 비교용 버킷
    clean_sheet_ratings = []
    hero_save_ratings = []  # SOT>=6 이면서 실점<=1인 "고전 방어" 경기

    for i in range(N_MATCHES):
        home_lineup = build_lineup(GK_OVR, MY_OUTFIELD_OVR)
        away_lineup = build_lineup(OPP_GK_OVR, opp_outfield_ovr)
        seed = SEED_BASE * 7919 + opp_outfield_ovr * 131 + i
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
        "real_conceded": real_conceded, "real_sot": real_sot, "real_cs": real_cs,
        "real_savepct": real_savepct, "ratings": ratings, "saves": saves_list,
        "mom_flags": mom_flags, "clean_sheet_ratings": clean_sheet_ratings,
        "hero_save_ratings": hero_save_ratings,
    }


def main():
    print(f"=== GK 실전 QA 1차 — 상대 공격수준 차등, GK OVR{GK_OVR} 고정, "
          f"{N_MATCHES}경기/구간 ===\n")
    all_results = {}
    for ovr in OPP_OUTFIELD_LEVELS:
        all_results[ovr] = run_level(ovr)

    print("── 1) 상대 OVR → SOT / save% / 실점 / 클린시트 ──")
    header = f"{'상대OVR':>7} | {'SOT':>6} | {'save%':>7} | {'실점/경기':>9} | {'클린시트%':>8}"
    print(header)
    print("-" * len(header))
    for ovr in OPP_OUTFIELD_LEVELS:
        r = all_results[ovr]
        sot = statistics.mean(r["real_sot"])
        savepct = statistics.mean(r["real_savepct"]) * 100 if r["real_savepct"] else 0
        conceded = statistics.mean(r["real_conceded"])
        cs = statistics.mean(r["real_cs"]) * 100
        print(f"{ovr:>7} | {sot:6.3f} | {savepct:6.2f}% | {conceded:9.3f} | {cs:7.2f}%")

    print("\n── 2) 상대 OVR → 평점 (난이도를 평점이 보고 있는가) ──")
    header2 = f"{'상대OVR':>7} | {'평균평점':>8} | {'평균saves':>9} | {'MOM%':>6}"
    print(header2)
    print("-" * len(header2))
    for ovr in OPP_OUTFIELD_LEVELS:
        r = all_results[ovr]
        avg_rating = statistics.mean(r["ratings"])
        avg_saves = statistics.mean(r["saves"])
        mom = statistics.mean(r["mom_flags"]) * 100
        print(f"{ovr:>7} | {avg_rating:8.3f} | {avg_saves:9.3f} | {mom:5.2f}%")
        print(f"          평점분산: [{fmt_stats(r['ratings'])}]")

    print("\n── 3) 핵심 검증: '약팀 상대 클린시트' vs '강팀 상대 슈퍼세이브(SOT>=6,실점<=1)' ──")
    for ovr in OPP_OUTFIELD_LEVELS:
        r = all_results[ovr]
        cs_r = statistics.mean(r["clean_sheet_ratings"]) if r["clean_sheet_ratings"] else float("nan")
        hero_r = statistics.mean(r["hero_save_ratings"]) if r["hero_save_ratings"] else float("nan")
        print(f"  상대OVR{ovr}: 클린시트 평점 {cs_r:.3f} (n={len(r['clean_sheet_ratings'])})  |  "
              f"고전방어 평점 {hero_r:.3f} (n={len(r['hero_save_ratings'])})")

    # 약팀 상대 클린시트(가장 쉬운 상황, opp=65)와 강팀 상대 고전방어(가장 어려운 상황, opp=90) 직접 비교
    easy_cs = all_results[OPP_OUTFIELD_LEVELS[0]]["clean_sheet_ratings"]
    hard_hero = all_results[OPP_OUTFIELD_LEVELS[-1]]["hero_save_ratings"]
    if easy_cs and hard_hero:
        e, h = statistics.mean(easy_cs), statistics.mean(hard_hero)
        verdict = "✅ 난이도 반영됨 (강팀 고전방어가 약팀 클린시트 이상)" if h >= e - 0.05 \
            else "⚠️ 난이도 미반영 — 약팀 클린시트가 강팀 고전방어보다 확연히 높음"
        print(f"\n  [최종] 약팀(OVR{OPP_OUTFIELD_LEVELS[0]}) 클린시트 평점 {e:.3f}  vs  "
              f"강팀(OVR{OPP_OUTFIELD_LEVELS[-1]}) 고전방어 평점 {h:.3f}")
        print(f"  {verdict}")


if __name__ == "__main__":
    main()