# -*- coding: utf-8 -*-
"""마킹/키퍼 근접/박스 밀집도 등 '전술적' 이상을 정량으로 잡아내는 진단
스크립트. headless_motion_probe.py는 순간이동/고정/팀내겹침만 보므로
이번에 제보된 '수비수가 키퍼 옆에만 있다', '마킹을 안 한다' 는
탐지하지 못한다 — 이 스크립트가 그 갭을 메운다.
"""
import os
import random
import sys

sys.path.insert(0, ".")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

from headless_motion_probe import make_scenario
from ui.match_sim_viewer import MatchSimViewer, _BACKLINE_ROLES, _ATTACK_ROLES


def analyze_tactics(viewer, label):
    frames = viewer._frames
    home_pos = [pl["pos"] for pl in viewer.home_players]
    away_pos = [pl["pos"] for pl in viewer.away_players]

    def gk_idx(positions):
        return next((i for i, p in enumerate(positions) if p == "GK"), 0)

    home_gk, away_gk = gk_idx(home_pos), gk_idx(away_pos)

    print(f"--- {label} (frames={len(frames)}) ---")

    # 1) 백라인 수비수 vs 자기 GK 거리 분포
    for side, positions, gk_i in (("home", home_pos, home_gk), ("away", away_pos, away_gk)):
        for i, pos in enumerate(positions):
            if pos not in _BACKLINE_ROLES:
                continue
            dists = []
            for f in frames:
                gx, gy = f[side][gk_i]
                px, py = f[side][i]
                dists.append(((px - gx) ** 2 + (py - gy) ** 2) ** 0.5)
            avg_d = sum(dists) / len(dists)
            close_frac = sum(1 for d in dists if d < 0.10) / len(dists)
            if close_frac > 0.5 or avg_d < 0.08:
                print(f"  [키퍼밀착의심] {side}#{i}({pos}) 평균거리={avg_d:.3f} "
                      f"0.10미만 비율={close_frac:.0%}")

    # 2) 볼이 한쪽 박스 근처(공격 3분의1)에 있을 때, 그 공을 가진/가장 가까운
    #    공격수를 상대 수비 아무도 마크(0.10 이내)하지 않는 프레임 비율
    unmarked = 0
    checked = 0
    for f in frames:
        bx, by = f["ball"]
        # 홈 골대(x=0) 근처 = 홈 수비 상황, away 골대(x=1) 근처 = 원정 수비 상황
        if bx < 0.30:
            def_side, atk_side = "home", "away"
        elif bx > 0.70:
            def_side, atk_side = "away", "home"
        else:
            continue
        checked += 1
        atk_positions = home_pos if atk_side == "home" else away_pos
        def_positions = home_pos if def_side == "home" else away_pos
        # 공과 가장 가까운 공격수(GK 제외)
        atk_pts = f[atk_side]
        nearest_atk_i = min(
            (i for i, p in enumerate(atk_positions) if p != "GK"),
            key=lambda i: (atk_pts[i][0] - bx) ** 2 + (atk_pts[i][1] - by) ** 2)
        ax, ay = atk_pts[nearest_atk_i]
        def_pts = f[def_side]
        nearest_def_dist = min(
            ((dx - ax) ** 2 + (dy - ay) ** 2) ** 0.5
            for i, (dx, dy) in enumerate(def_pts) if def_positions[i] != "GK")
        if nearest_def_dist > 0.16:
            unmarked += 1
        # [주의] "공과 가장 가까운 공격수"는 실전에서 거의 항상 홀더
        # 자신이고, 홀더는 이 스크립트가 검사하는 _breakthrough_marks가
        # 아니라 _presser_idx가 담당한다. 즉 이 비율은 "홀더 압박"까지
        # 섞여서 나오는 수치라, 이번에 고친 '오프볼 박스 침투자 마크'
        # 개선분만 따로 떼어 보여주진 못한다(홀더 압박 로직은 이번에
        # 손대지 않았으므로 수정 전후 큰 변화가 없는 게 정상). 오프볼
        # 마크 개선을 직접 확인하려면 디버그 캡처로 실제 박스 장면을
        # 눈으로 보는 쪽이 더 정확하다.
    if checked:
        frac = unmarked / checked
        flag = " <-- 마킹 부재 의심" if frac > 0.3 else ""
        print(f"  [박스근접 무마크비율] {unmarked}/{checked} = {frac:.0%}{flag}")
    return unmarked, checked


def main():
    import sys as _sys
    n_trials = int(_sys.argv[1]) if len(_sys.argv) > 1 else 5
    seed = int(_sys.argv[2]) if len(_sys.argv) > 2 else 7
    rng = random.Random(seed)
    tot_unmarked, tot_checked = 0, 0
    for trial in range(n_trials):
        data = make_scenario(rng, is_home=rng.choice([True, False]))
        viewer = MatchSimViewer(data)
        u, c = analyze_tactics(viewer, f"trial#{trial}")
        tot_unmarked += u
        tot_checked += c
    print(f"\n=== 합계: {tot_unmarked}/{tot_checked} = {tot_unmarked/tot_checked:.1%} "
          f"({n_trials}경기) ===")


if __name__ == "__main__":
    main()