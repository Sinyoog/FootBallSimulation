# -*- coding: utf-8 -*-
"""competition_common.merge_my_slot 불변식 속성 테스트 (2026-09 신설).

tools/ratings_sum_qa.py가 '실제 경기 경로'를 계측하는 계측기라면, 이 쪽은
헬퍼 자체를 무작위 입력 수만 건으로 두드려서 모든 모양(0-0, 대량 득점,
내가 GK, 내가 팀 득점 전부, 빈 슬롯 포함 등)에서 불변식 3개가 깨지지
않는지 확인한다 — 7개 엔진이 전부 이 함수 하나를 부르므로 이 함수가
안전하면 전 대회가 안전하다.

불변식
  (1) sum(goals[side])   == 그 팀 득점
  (2) sum(assists[side]) <= 그 팀 득점
  (3) 선수별 assists <= 득점 - 본인 goals
  (4) 내 슬롯의 goals/assists/rating은 입력값 그대로 (공식 기록 불변)
  (5) 각 슬롯 goals <= shots_on <= shots

사용: python3 tools/ratings_reconcile_unit.py [--n 20000]
"""
import _path  # noqa: F401
import argparse
import random

from competition.competition_common import merge_my_slot

_LABELS = ["GK", "LB", "CB", "CB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]


def _make_side(rng, score, n_slots=11):
    """전술엔진 출력과 같은 모양: 골 합계가 정확히 score인 11명."""
    lst = []
    for i in range(n_slots):
        lst.append({"id": 1000 + i, "name": "AI%02d" % i, "position": _LABELS[i],
                    "ovr": rng.randint(40, 95),
                    "goals": 0, "assists": 0,
                    "shots": rng.randint(0, 4), "shots_on": 0,
                    "saves": rng.randint(0, 6) if i == 0 else 0,
                    "is_gk": (i == 0), "rating": round(rng.uniform(5.0, 8.0), 1)})
    # 빈 슬롯(라인업 구멍) 재현
    if rng.random() < 0.1:
        lst[rng.randint(1, n_slots - 1)] = None
    live = [i for i, r in enumerate(lst) if r]
    for _ in range(score):
        i = rng.choice([j for j in live if not lst[j]["is_gk"]] or live)
        lst[i]["goals"] += 1
        lst[i]["shots_on"] = max(lst[i]["shots_on"], lst[i]["goals"])
        lst[i]["shots"] = max(lst[i]["shots"], lst[i]["shots_on"])
        if rng.random() < 0.62:
            pool = [j for j in live if j != i]
            if pool:
                lst[rng.choice(pool)]["assists"] += 1
    return lst


def _check(lst, score, my_idx, my_entry):
    bad = []
    live = [r for r in lst if r]
    g = sum(int(r.get("goals", 0)) for r in live)
    a = sum(int(r.get("assists", 0)) for r in live)
    if g != score:
        bad.append("goal_sum=%d != score=%d" % (g, score))
    if a > score:
        bad.append("assist_sum=%d > score=%d" % (a, score))
    for r in live:
        rg, ra = int(r.get("goals", 0)), int(r.get("assists", 0))
        if ra > max(0, score - rg):
            bad.append("self_assist %s g%d a%d" % (r.get("name"), rg, ra))
        if not (rg <= int(r.get("shots_on", 0)) <= int(r.get("shots", 0))):
            bad.append("shots %s g%d on%d sh%d"
                       % (r.get("name"), rg, r.get("shots_on", 0), r.get("shots", 0)))
    if my_idx is not None and lst[my_idx] is not None:
        me = lst[my_idx]
        for k in ("goals", "assists", "rating"):
            if me.get(k) != my_entry.get(k):
                bad.append("my %s changed %r -> %r" % (k, my_entry.get(k), me.get(k)))
        if not me.get("is_me"):
            bad.append("my slot lost is_me")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    fails = 0
    no_slot = 0
    for it in range(args.n):
        hs = rng.choice([0, 0, 1, 1, 1, 2, 2, 3, 4, 5, 7])
        as_ = rng.choice([0, 0, 1, 1, 1, 2, 2, 3, 4, 6])
        is_home = rng.random() < 0.5
        my_score = hs if is_home else as_
        my_pos = rng.choice(_LABELS + ["SS", "LWB", "RM"])
        my_goals = rng.randint(0, my_score)
        my_assists = rng.randint(0, max(0, my_score - my_goals))
        pr = {"home": _make_side(rng, hs), "away": _make_side(rng, as_)}
        _my_son = my_goals + rng.randint(0, 1)
        my_entry = {"id": None, "name": "나", "position": None, "ovr": rng.randint(40, 99),
                    "goals": my_goals, "assists": my_assists,
                    "shots_on": _my_son,
                    "shots": _my_son + rng.randint(0, 2),
                    "saves": rng.randint(0, 8) if my_pos == "GK" else 0,
                    "is_gk": (my_pos == "GK"), "rating": 7.2, "is_me": True}
        side, idx = merge_my_slot(pr, is_home, my_pos, dict(my_entry), hs, as_)
        if idx is None:
            no_slot += 1
        bad = []
        bad += _check(pr["home"], hs, idx if (side == "home") else None, my_entry)
        bad += _check(pr["away"], as_, idx if (side == "away") else None, my_entry)
        if bad:
            fails += 1
            if fails <= 5:
                print("[FAIL] it=%d %d-%d is_home=%s pos=%s my=%dG%dA"
                      % (it, hs, as_, is_home, my_pos, my_goals, my_assists))
                for b in bad:
                    print("   ", b)

    print("\n[결과] %d건 중 위반 %d건 (슬롯 미발견 %d건)" % (args.n, fails, no_slot))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())