# -*- coding: utf-8 -*-
"""match_sim/headless_motion_probe.py — 22명 움직임 회귀 검사 + 품질 계량.

## 무엇이 바뀌었나 (v2)

**Qt가 더 이상 필요 없다.** 예전엔 시뮬레이션이 `MatchSimViewer(QDialog)`
안에 살아서, 이 스크립트가 `QT_QPA_PLATFORM=offscreen`으로 Qt를 띄우고
다이얼로그를 생성해야만 돌았다. 시스템에 Qt 런타임(libEGL 등)이 없으면
아예 못 돌았고, CI에서도 무거웠다. 이제 시뮬은 `match_sim/sim_engine.py`의
`MatchSimEngine`이고 순수 파이썬이라 그냥 돌아간다.

## 두 가지 검사

이 스크립트는 성격이 다른 두 질문에 답한다. 둘 다 필요하다.

  **(1) 결함 검사 (--defects, 기본 켜짐)** — "버그가 없는가"
      순간이동 / 고정 / 팀 내 겹침. exit code에 반영된다.
      의도된 스냅(코너 크라우드, 하프타임 재배치, 파울 재개 등)은
      `last_restart_clock`으로 식별해 화이트리스트 처리한다.

  **(2) 품질 계량 (--metrics)** — "축구처럼 보이는가"
      motion_metrics.py로 이동거리/속도/대형/압박밀도/자율성을 실축
      단위(m, m/s, km)로 환산해 참조 밴드와 대조한다.

(1)만으로는 부족하다는 게 이 프로젝트에서 실제로 확인됐다 — 결함 0건인
채로도 "선수당 이동거리 3.2km(실축 9~12km), 한 프레임에 움직이는 선수
1.4명(실축 8~18명)"이 나온다. 결함이 없는 것과 축구처럼 보이는 것은
다른 문제다.

## 사용법

    python3 -m match_sim.headless_motion_probe --trials 12 --metrics
    python3 -m match_sim.headless_motion_probe --trials 30 --metrics --no-defects

exit code 0 = 결함 없음, 1 = 결함 발견.
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import game_engine
from match_sim import match_flow
from match_sim.sim_engine import MatchSimEngine
from match_sim.motion_metrics import (
    analyze_frames, aggregate, format_report, format_detail)

# 의도된 순간 전환으로 간주할 배너 키워드.
_WHITELIST_BANNER_KEYWORDS = (
    "코너킥", "페널티", "PK", "GOAL", "골!", "후반 시작", "전반 시작",
    "파울", "선방", "승부차기", "실점",
)

# [Phase 2] 고정 임계(0.22)는 틱 길이가 3.6초일 때 만든 값이다. 틱 길이가
# 바뀌면 "물리적으로 가능한 최대 이동"도 바뀌므로 프레임 간격에서 유도한다.
# 기준: 최고속도 11 m/s로 그 시간 동안 갈 수 있는 거리 + 여유.
def _max_jump_for(dt_min):
    dt_s = dt_min * 60.0
    return (11.0 * dt_s) / 105.0 * 1.6 + 0.02


MAX_JUMP_PER_TICK = 0.22
STATIONARY_EPS = 0.03
TEAMMATE_OVERLAP_EPS = 0.012


def make_scenario(rng, is_home=True):
    """실제 게임 엔진의 통계/이벤트 생성 공식을 그대로 써서 진짜와 동일한
    형태의 payload를 만든다."""
    hs = rng.randint(0, 5)
    as_ = rng.randint(0, 5)
    my_score = hs if is_home else as_
    opp_score = as_ if is_home else hs
    goals = rng.randint(0, min(2, my_score))
    assists = rng.randint(0, 2)
    saves = rng.randint(0, 4)
    detail = {"shots": rng.randint(0, 8), "shots_on": rng.randint(0, 4),
              "key_passes": rng.randint(0, 5), "dribbles": rng.randint(0, 5),
              "blocks": rng.randint(0, 6), "pass_acc": round(rng.uniform(0.55, 0.95), 3)}
    team_stats = game_engine._derive_match_stats(
        is_home, hs, as_, goals, assists, saves, "CM", detail)

    events, used = [], set()

    def pick_minute():
        for _ in range(20):
            m = rng.randint(1, 89)
            if m not in used:
                used.add(m)
                return float(m)
        return float(rng.randint(1, 89))

    for _ in range(goals):
        events.append((pick_minute(), "⚽ 골! 환상적인 마무리"))
    if opp_score > 0 and rng.random() < 0.6:
        events.append((pick_minute(), "😱 실점했다"))
    if saves:
        events.append((pick_minute(), "🧤 환상적인 선방!"))
    events.sort(key=lambda x: x[0])

    home_name, away_name = "테스트홈", "테스트원정"
    possession_log = match_flow.generate_possession_log(
        is_home, team_stats, events, my_score, opp_score)
    try:
        lineup_stats = match_flow.generate_lineup_stats(home_name, away_name)
    except Exception:
        lineup_stats = {}

    payload = {
        "events": [[m, t] for m, t in events],
        "position": rng.choice(["ST", "CM", "CB", "LW", "GK"]),
        "detail": detail, "team_stats": team_stats,
        "possession_log": possession_log, "lineup_stats": lineup_stats,
    }
    return {"payload": payload, "is_home": is_home,
            "home_name": home_name, "away_name": away_name,
            "home_score": hs, "away_score": as_}


def _is_whitelisted(frame_before, frame_after):
    """코드가 직접 기록하는 last_restart_clock(스로인/골킥/코너크라우드/
    파울재개/씬시작/하프타임 등 모든 의도된 스냅 지점에서 갱신됨)을 봐서,
    이번 틱 사이에 실제로 의도된 재개가 있었는지 판별한다."""
    if frame_after.get("last_restart_clock", -99.0) >= frame_before["clock"]:
        return True
    for f in (frame_before, frame_after):
        if any(k in (f.get("banner_text") or "") for k in _WHITELIST_BANNER_KEYWORDS):
            return True
    return False


def check_defects(eng, label, verbose=True):
    """결함 검사 — 순간이동 / 고정 / 팀 내 겹침."""
    frames = eng.frames
    n = len(frames)
    issues = []
    max_jump = _max_jump_for(eng._FRAME_DT)

    for side in ("home", "away"):
        team = eng.home_players if side == "home" else eng.away_players
        for i in range(len(team)):
            xs = [f[side][i][0] for f in frames]
            ys = [f[side][i][1] for f in frames]
            total_move = sum(abs(xs[k] - xs[k - 1]) + abs(ys[k] - ys[k - 1])
                             for k in range(1, n))
            if total_move < STATIONARY_EPS:
                issues.append(f"[고정] {side}#{i}({team[i]['pos']}) 전체 이동량={total_move:.4f}")
            for k in range(1, n):
                jump = abs(xs[k] - xs[k - 1]) + abs(ys[k] - ys[k - 1])
                if jump > max_jump and not _is_whitelisted(frames[k - 1], frames[k]):
                    issues.append(
                        f"[순간이동] {side}#{i}({team[i]['pos']}) frame {k} "
                        f"clock={frames[k]['clock']:.2f} 이동={jump:.3f}")

    overlap = 0
    for f in frames:
        if any(k in (f.get("banner_text") or "") for k in _WHITELIST_BANNER_KEYWORDS):
            continue
        if f["clock"] - f.get("last_restart_clock", -99.0) < 0.7:
            continue
        for pts in (f["home"], f["away"]):
            for a in range(len(pts)):
                for b in range(a + 1, len(pts)):
                    dx, dy = pts[a][0] - pts[b][0], pts[a][1] - pts[b][1]
                    if (dx * dx + dy * dy) ** 0.5 < TEAMMATE_OVERLAP_EPS:
                        overlap += 1
    if overlap > n * 0.03:
        issues.append(f"[팀내겹침과다] 오픈플레이 중 같은 팀 겹침 {overlap}건 (프레임 {n}장 중)")

    if verbose:
        print(f"--- {label} (frames={n}) ---")
        if not issues:
            print("  이상 없음")
        else:
            for iss in issues[:30]:
                print("  " + iss)
            if len(issues) > 30:
                print(f"  ... 외 {len(issues) - 30}건 더")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--metrics", action="store_true", help="움직임 품질 계량 리포트 출력")
    ap.add_argument("--no-defects", action="store_true", help="결함 검사 생략")
    ap.add_argument("--quiet", action="store_true", help="경기별 상세 출력 생략")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    total_issues = 0
    reports = []

    for trial in range(args.trials):
        data = make_scenario(rng, is_home=rng.choice([True, False]))
        eng = MatchSimEngine(data)
        eng.simulate()
        if not args.no_defects:
            total_issues += len(check_defects(eng, f"trial#{trial}", verbose=not args.quiet))
        if args.metrics:
            reports.append(analyze_frames(
                eng.frames,
                [p["pos"] for p in eng.home_players],
                [p["pos"] for p in eng.away_players],
                [(p["hx"], p["hy"]) for p in eng.home_players],
                [(p["hx"], p["hy"]) for p in eng.away_players]))

    if args.metrics and reports:
        print()
        print(format_report(aggregate(reports), title=f"움직임 품질 계량 ({len(reports)}경기)"))
        print(format_detail(reports[0]))

    if not args.no_defects:
        print(f"\n=== 결함 총 {total_issues}건 (시나리오 {args.trials}개) ===")
        sys.exit(1 if total_issues else 0)


if __name__ == "__main__":
    main()