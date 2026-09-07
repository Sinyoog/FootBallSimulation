# -*- coding: utf-8 -*-
"""GK OVR별 실전 QA — Stage 1(tactical_engine) / Stage 2(_player_perf 평점) 분리 검증.

설계 근거(신민용 확정):
  - Stage1과 Stage2를 완전히 분리해서 원인(엔진 vs 평점 모델)을 구분한다.
  - OVR 70/75/80/85/90/95/100 7단계, tier2, 1000경기.
  - 평균만이 아니라 표준편차/최솟값/최댓값도 본다.
  - 실점보다 save%를 우선 지표로 본다.
  - 핵심: "실제 경기 유효슈팅(SOT)" vs "_player_perf가 평점 계산에 쓰는
    가상 SOT(_base_sot 기반 total_shots)"를 나란히 비교한다 — 두 계층이
    서로 다른 GK 실력 반영도를 갖고 있는지가 이번 QA의 핵심 포인트.

주의: Stage2의 "평점용 SOT"는 game_engine._player_perf가 saves만 반환하고
total_shots을 반환하지 않아서, 그 함수 안의 계산식을 그대로 복제해 별도로
관측한다(game_engine.py의 GK 분기, _base_sot~total_shots 블록과 동일해야
함 — 프로덕션 코드가 바뀌면 이 복제본도 같이 갱신 필요).
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 프로젝트 루트(game_engine.py 위치) — qa_gk/ 하위 폴더에서 한 단계 위로

import game_engine  # noqa: E402
from match_sim import tactical_engine  # noqa: E402

# ── tier를 실제 DB 조회 없이 강제하기 위한 트릭 ──────────────────────
# _league_tier()는 _league_tier_cache에 값이 있으면 DB(c.execute)를 아예
# 안 건드리고 캐시값을 반환한다. 가짜 league_id를 캐시에 tier=2로 미리
# 박아두면 실제 game.db 없이도 tier2 경로를 그대로 탈 수 있다.
FAKE_LEAGUE_ID = 999001
game_engine._league_tier_cache[FAKE_LEAGUE_ID] = 2
_DUMMY_CURSOR = object()  # c is not None 조건만 만족시키면 됨 (캐시 히트라 실제 호출 안 됨)

OVR_LEVELS = [70, 75, 80, 85, 90, 95, 100]
BASELINE_OUTFIELD_OVR = 80
BASELINE_OPP_GK_OVR = 80
N_MATCHES = 1000
TIER = 2
SEED_BASE = 20260802


def make_outfield(ovr):
    """필드 플레이어 1명 스탯 dict (OVR 상당의 값으로 균질하게 채움)."""
    v = min(95, ovr)  # tactical_engine 쪽 스탯엔 특별한 상한 클램프가 없어 안전하게 95캡
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
    """_FALLBACK_SLOTS 순서: GK, CB, CB, LB, RB, LM, CM, CM, RM, ST, ST"""
    gk = make_gk(gk_ovr)
    of = make_outfield(outfield_ovr)
    return [gk] + [dict(of) for _ in range(10)]


def rated_sot_replica(rng, my_ovr, dom, tier, sub_role, opp_score):
    """game_engine._player_perf GK 분기의 total_shots(평점용 SOT) 계산을
    그대로 복제. saves/rating 자체는 건드리지 않고 '평점 모델이 상정하는
    유효슈팅 수'만 별도로 관측하기 위함."""
    if tier == 1:
        sr_min, sr_max = 0.46, 0.82
        base_sot = rng.choices([1, 2, 3, 4, 5], [10, 24, 30, 22, 14])[0]
    elif tier == 2:
        sr_min, sr_max = 0.42, 0.78
        base_sot = rng.choices([1, 2, 3, 4, 5], [8, 22, 30, 24, 16])[0]
    else:
        sr_min, sr_max = 0.38, 0.74
        base_sot = rng.choices([1, 2, 3, 4, 5, 6], [6, 18, 26, 24, 16, 10])[0]

    expose = max(0.70, min(1.7, 1.45 - 0.46 * min(2.2, dom)))
    extra_sot = max(0, int(round(base_sot * expose)))
    total_shots = max(opp_score + 1, opp_score + extra_sot)
    return total_shots


def fmt_stats(vals):
    if not vals:
        return "n/a"
    return (f"평균 {statistics.mean(vals):.3f}  표준편차 {statistics.pstdev(vals):.3f}  "
            f"최소 {min(vals):.2f}  최대 {max(vals):.2f}")


def run_level(gk_ovr):
    rng = random.Random(SEED_BASE + gk_ovr)
    real_conceded, real_sot, real_cs, real_savepct = [], [], [], []
    rating_saves, ratings, mom_flags, model_sot = [], [], [], []

    for i in range(N_MATCHES):
        home_lineup = build_lineup(gk_ovr, BASELINE_OUTFIELD_OVR)
        away_lineup = build_lineup(BASELINE_OPP_GK_OVR, BASELINE_OUTFIELD_OVR)
        seed = SEED_BASE * 7919 + gk_ovr * 131 + i
        result = tactical_engine.simulate_tactical_match(
            home_lineup, away_lineup, home_adv=3.0, seed=seed)
        hs, as_ = result["home_score"], result["away_score"]
        away_stats = result["away_stats"]  # 원정팀이 홈 GK를 상대로 만든 슈팅

        # ── Stage 1: 순수 엔진 지표 (여기선 _player_perf 절대 호출 안 함) ──
        sot_faced = away_stats["shots_on"]
        conceded = as_
        real_conceded.append(conceded)
        real_sot.append(sot_faced)
        real_cs.append(1 if conceded == 0 else 0)
        if sot_faced > 0:
            real_savepct.append((sot_faced - conceded) / sot_faced)

        # ── Stage 2: 같은 경기 결과 + 실제 SOT를 _player_perf에 흘려서 평점만 관측 ──
        # [구조수정 검증] opp_sot=sot_faced로 실제 전술엔진 SOT를 그대로 넘김
        # (call site 패치와 동일한 경로). 이게 없으면 이전처럼 자체 랜덤 SOT로 폴백.
        gk_player = home_lineup[0]
        goals, assists, saves, rating, events, detail = game_engine._player_perf(
            gk_player, "win" if hs > as_ else ("draw" if hs == as_ else "loss"),
            True, hs, as_, c=_DUMMY_CURSOR, opp_ovr=BASELINE_OPP_GK_OVR, opp_sot=sot_faced)
        rating_saves.append(saves)
        ratings.append(rating)
        mom_flags.append(1 if rating >= 8.0 else 0)
        model_sot.append(sot_faced)  # 구조수정 후엔 정의상 실제SOT와 항상 같아야 함

    return {
        "real_conceded": real_conceded, "real_sot": real_sot, "real_cs": real_cs,
        "real_savepct": real_savepct, "rating_saves": rating_saves, "ratings": ratings,
        "mom_flags": mom_flags, "model_sot": model_sot,
    }


def main():
    print(f"=== GK OVR QA — tier{TIER}, {N_MATCHES}경기/구간, "
          f"필드/상대 GK 고정 OVR{BASELINE_OUTFIELD_OVR} ===\n")
    all_results = {}
    for ovr in OVR_LEVELS:
        all_results[ovr] = run_level(ovr)

    print("── Stage 1: Tactical QA (엔진, _player_perf 미호출) ──")
    header = f"{'OVR':>4} | {'save%':>7} | {'실점/경기':>9} | {'클린시트%':>8} | {'실제SOT':>8}"
    print(header)
    print("-" * len(header))
    for ovr in OVR_LEVELS:
        r = all_results[ovr]
        savepct = statistics.mean(r["real_savepct"]) * 100 if r["real_savepct"] else 0
        conceded = statistics.mean(r["real_conceded"])
        cs = statistics.mean(r["real_cs"]) * 100
        sot = statistics.mean(r["real_sot"])
        print(f"{ovr:>4} | {savepct:6.2f}% | {conceded:9.3f} | {cs:7.2f}% | {sot:8.3f}")

    print("\n  (실점/경기, 실제SOT 분산)")
    for ovr in OVR_LEVELS:
        r = all_results[ovr]
        print(f"  OVR{ovr}: 실점 [{fmt_stats(r['real_conceded'])}]")

    print("\n── Stage 2: Rating QA (같은 경기결과 → _player_perf) ──")
    header2 = f"{'OVR':>4} | {'평균평점':>7} | {'평균saves':>9} | {'MOM%':>6} | {'평점용SOT':>9} | {'실제SOT':>8} | {'SOT차이':>8}"
    print(header2)
    print("-" * len(header2))
    for ovr in OVR_LEVELS:
        r = all_results[ovr]
        avg_rating = statistics.mean(r["ratings"])
        avg_saves = statistics.mean(r["rating_saves"])
        mom = statistics.mean(r["mom_flags"]) * 100
        m_sot = statistics.mean(r["model_sot"])
        real_sot = statistics.mean(r["real_sot"])
        print(f"{ovr:>4} | {avg_rating:7.3f} | {avg_saves:9.3f} | {mom:5.2f}% | "
              f"{m_sot:9.3f} | {real_sot:8.3f} | {m_sot - real_sot:+8.3f}")

    print("\n  (평점 분산)")
    for ovr in OVR_LEVELS:
        r = all_results[ovr]
        print(f"  OVR{ovr}: 평점 [{fmt_stats(r['ratings'])}]")

    # ── 단조성 체크 ──────────────────────────────────────────────
    print("\n── 단조성 체크 ──")
    def monotonic_report(name, series, higher_is_better=True):
        ok = True
        for a, b in zip(series, series[1:]):
            if higher_is_better and b < a - 1e-9:
                ok = False
            if not higher_is_better and b > a + 1e-9:
                ok = False
        status = "✅ 단조" if ok else "⚠️ 역전 있음"
        print(f"  {name}: {status}  {['%.3f' % x for x in series]}")

    savepct_series = [statistics.mean(all_results[o]["real_savepct"]) for o in OVR_LEVELS]
    conceded_series = [statistics.mean(all_results[o]["real_conceded"]) for o in OVR_LEVELS]
    rating_series = [statistics.mean(all_results[o]["ratings"]) for o in OVR_LEVELS]
    monotonic_report("save% (증가해야 함)", savepct_series, True)
    monotonic_report("실점 (감소해야 함)", conceded_series, False)
    monotonic_report("평점 (증가해야 함)", rating_series, True)


if __name__ == "__main__":
    main()