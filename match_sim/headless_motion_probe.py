# -*- coding: utf-8 -*-
"""match_sim_viewer.py의 22명 움직임 품질을 자동으로 검사하는 헤드리스
회귀 테스트. 매번 눈으로 재생해서 확인할 필요 없이, 아래 세 가지를 자동
으로 잡아낸다:

  1. 순간이동  — 한 틱 사이 비정상적으로 큰 좌표 이동
  2. 고정      — 경기 내내(또는 아주 긴 구간) 사실상 안 움직이는 선수
  3. 팀 내 겹침 — 같은 팀 선수 두 명이 완전히 같은 자리에 뭉치는 경우
     (상대 팀과의 밀착 마크는 실제 축구에서도 정상이라 검사 대상에서 뺀다)

[중요] 코너킥/PK 크라우드 스냅, 하프타임 재배치, 골/파울 재개처럼 게임이
"의도적으로" 순간 배치하는 상황이 실제로 존재한다(신민용 확인 완료 —
"애들 이동하는데 시간 빼기 싫어서 넣은 것"). 이런 의도된 스냅까지 버그로
잡아내면 오탐만 늘어나서 검증 자체가 무의미해지므로, 해당 프레임의 배너
텍스트로 "의도된 전환 구간"을 식별해 화이트리스트 처리한다 — 이 스크립트를
쓸 때 새로 배너 문구를 추가했다면 _WHITELIST_BANNER_KEYWORDS도 같이 확인할 것.

사용법:
    QT_QPA_PLATFORM=offscreen python3 tests/headless_motion_probe.py
    (또는 --trials N 으로 시나리오 개수 조절, --seed N 으로 재현)

exit code 0 = 이상 없음, 1 = 이슈 발견(내용은 표준출력에 상세히 찍힘).
"""
import argparse
import os
import random
import sys

sys.path.insert(0, ".")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import game_engine
import match_flow
from ui.match_sim_viewer import MatchSimViewer

# 의도된 순간 전환으로 간주할 배너 키워드. 이 문구가 현재 또는 직전 프레임
# 배너에 있으면 그 틱의 이동은 "게임이 일부러 스냅한 것"으로 보고 넘어간다.
_WHITELIST_BANNER_KEYWORDS = (
    "코너킥", "페널티", "PK", "GOAL", "골!", "후반 시작", "전반 시작",
    "파울", "선방", "승부차기", "실점",
)

MAX_JUMP_PER_TICK = 0.22    # 화이트리스트 안 걸린 틱에서 이 이상 이동하면 이상
STATIONARY_EPS = 0.03       # 경기 전체 이동거리 합이 이 이하면 "고정"
TEAMMATE_OVERLAP_EPS = 0.012


def make_scenario(rng, is_home=True):
    """실제 게임 엔진의 통계/이벤트 생성 공식을 그대로 써서 진짜와 동일한
    형태의 payload를 만든다(test_match_flow.py의 시나리오 생성기와 동일한
    패턴)."""
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
    team_stats = game_engine._derive_match_stats(is_home, hs, as_, goals, assists, saves, "CM", detail)

    events = []
    used = set()

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
        "detail": detail,
        "team_stats": team_stats,
        "possession_log": possession_log,
        "lineup_stats": lineup_stats,
    }
    return {
        "payload": payload, "is_home": is_home,
        "home_name": home_name, "away_name": away_name,
        "home_score": hs, "away_score": as_,
    }


def _is_whitelisted(frame_before, frame_after):
    # [수정] 배너 텍스트 추측 방식은 스로인처럼 원래 배너가 안 뜨는 의도된
    # 스냅을 실제 버그로 오탐했다(실측: 실제 세이브 데이터의 match#3에서
    # away#9 RW의 스로인 스냅이 "순간이동 버그"로 잘못 잡힘). 이제 코드가
    # 직접 기록하는 last_restart_clock(스로인/골킥/코너크라우드/파울재개/
    # 씬시작/하프타임 등 모든 의도된 스냅 지점에서 갱신됨)을 봐서, 이번
    # 틱 사이에 실제로 의도된 재개가 있었는지 정확히 판별한다.
    if frame_after.get("last_restart_clock", -99.0) >= frame_before["clock"]:
        return True
    for f in (frame_before, frame_after):
        text = f.get("banner_text") or ""
        if any(k in text for k in _WHITELIST_BANNER_KEYWORDS):
            return True
    return False


def analyze(viewer, label, verbose=True):
    frames = viewer._frames
    n = len(frames)
    issues = []

    for side in ("home", "away"):
        team = viewer.home_players if side == "home" else viewer.away_players
        for i in range(len(team)):
            xs = [f[side][i][0] for f in frames]
            ys = [f[side][i][1] for f in frames]
            total_move = sum(abs(xs[k] - xs[k - 1]) + abs(ys[k] - ys[k - 1]) for k in range(1, n))
            if total_move < STATIONARY_EPS:
                issues.append(f"[고정] {side}#{i}({team[i]['pos']}) 전체 이동량={total_move:.4f}")
            for k in range(1, n):
                jump = abs(xs[k] - xs[k - 1]) + abs(ys[k] - ys[k - 1])
                if jump > MAX_JUMP_PER_TICK and not _is_whitelisted(frames[k - 1], frames[k]):
                    issues.append(
                        f"[순간이동] {side}#{i}({team[i]['pos']}) frame {k} clock={frames[k]['clock']:.2f} "
                        f"이동={jump:.3f} banner_before={frames[k-1]['banner_text']!r} "
                        f"banner_after={frames[k]['banner_text']!r}")

    same_team_overlap = 0
    for f in frames:
        if any(k in (f.get("banner_text") or "") for k in _WHITELIST_BANNER_KEYWORDS):
            continue  # 의도된 크라우드/수비벽 밀집 구간은 겹침 검사에서 제외
        if f["clock"] - f.get("last_restart_clock", -99.0) < 0.7:
            continue  # 재개 직후 짧은 정렬 구간(수비벽 등)도 제외
        for pts in (f["home"], f["away"]):
            for a in range(len(pts)):
                for b in range(a + 1, len(pts)):
                    dx = pts[a][0] - pts[b][0]
                    dy = pts[a][1] - pts[b][1]
                    if (dx * dx + dy * dy) ** 0.5 < TEAMMATE_OVERLAP_EPS:
                        same_team_overlap += 1
    if same_team_overlap > n * 0.03:
        issues.append(f"[팀내겹침과다] 오픈플레이 중 같은 팀 겹침 {same_team_overlap}건 (프레임 {n}장 중)")

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
    args = ap.parse_args()

    rng = random.Random(args.seed)
    total_issues = 0
    for trial in range(args.trials):
        data = make_scenario(rng, is_home=rng.choice([True, False]))
        viewer = MatchSimViewer(data)
        issues = analyze(viewer, f"trial#{trial}")
        total_issues += len(issues)

    print(f"\n=== 총 이슈 {total_issues}건 (시나리오 {args.trials}개) ===")
    sys.exit(1 if total_issues else 0)


if __name__ == "__main__":
    main()