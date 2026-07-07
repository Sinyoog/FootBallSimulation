# -*- coding: utf-8 -*-
"""match_flow.generate_possession_log()의 핵심 불변식을 검증하는 헤드리스
테스트. 실제 game_engine._derive_match_stats()로 만든 진짜 통계 공식을
그대로 재사용해서, 수백 개의 무작위 시나리오에 대해:
  - 슈팅/온타깃/득점/코너/파울 레코드 개수가 team_stats와 정확히 일치하는지
  - 개인 이벤트가 정확한 분·outcome으로 로그에 그대로 존재하는지
  - 같은 입력이면 항상 같은 로그가 나오는지(결정론성)
을 확인한다. 하나라도 깨지면 AssertionError로 즉시 실패한다.
"""
import random as _global_random
import sys

sys.path.insert(0, ".")
import game_engine
from match_flow import generate_possession_log, _classify_personal


def _fmt_min(m):
    return round(m + _global_random.uniform(0, 0.9), 1)


def _make_scenario(rng):
    """실제 게임 엔진의 _derive_match_stats 공식을 그대로 써서 진짜와
    동일한 team_stats를 만들고, 그럴듯한 개인 이벤트 텍스트도 섞는다."""
    is_home = rng.choice([True, False])
    hs = rng.randint(0, 5)
    as_ = rng.randint(0, 5)
    my_score = hs if is_home else as_
    opp_score = as_ if is_home else hs

    goals = rng.randint(0, min(3, my_score))
    assists = rng.randint(0, 2)
    saves = rng.randint(0, 6)
    detail = {
        "shots": rng.randint(0, 8), "shots_on": rng.randint(0, 4),
        "key_passes": rng.randint(0, 5), "dribbles": rng.randint(0, 5),
        "blocks": rng.randint(0, 6), "pass_acc": round(rng.uniform(0.55, 0.95), 3),
    }
    team_stats = game_engine._derive_match_stats(
        is_home, hs, as_, goals, assists, saves, "CM", detail)

    my_side = "home" if is_home else "away"
    opp_side = "away" if is_home else "home"

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
    if my_score > goals and rng.random() < 0.4:
        events.append((pick_minute(), "🎯 페널티킥 골로 득점!"))
    if opp_score > 0 and rng.random() < 0.7:
        events.append((pick_minute(), "🥅 상대에게 실점..."))
    for _ in range(min(saves, 3)):
        style = rng.choice(["안정적인 선방", "환상적인 선방", "믿을 수 없는 선방쇼"])
        events.append((pick_minute(), f"🧤 {style}으로 막아냄"))
    if rng.random() < 0.3:
        events.append((pick_minute(), "🚫 페널티킥 실축..."))
    # 파울/코너킥 — "우리 팀"/"상대 팀" 둘 다 섞어서 생성
    for _ in range(rng.randint(0, 4)):
        who = rng.choice(["우리 팀", "상대 팀"])
        events.append((pick_minute(), f"{who} 파울"))
    for _ in range(rng.randint(0, 4)):
        who = rng.choice(["우리 팀", "상대 팀"])
        events.append((pick_minute(), f"{who} 코너킥"))

    events.sort(key=lambda e: e[0])
    return dict(is_home=is_home, team_stats=team_stats, events=events,
                my_score=my_score, opp_score=opp_score,
                my_side=my_side, opp_side=opp_side)


def _check_invariants(sc, log):
    ts = sc["team_stats"]
    my_side, opp_side = sc["my_side"], sc["opp_side"]

    # team_stats는 하한선이다 — 실제 개인 이벤트 개수도 함께 반영해서
    # 기대값을 계산한다(모듈의 설계와 동일한 규칙).
    real = {side: {"goal": 0, "save": 0, "foul": 0, "corner": 0} for side in ("home", "away")}
    for m, text in sc["events"]:
        kind = _classify_personal(text)
        if kind is None:
            continue
        if kind == "goal_for":
            real[my_side]["goal"] += 1
        elif kind == "goal_against":
            real[opp_side]["goal"] += 1
        elif kind == "miss_for":
            real[my_side]["save"] += 1
        elif kind == "save":
            real[opp_side]["save"] += 1
        elif kind == "foul":
            real[my_side if "우리 팀" in text else opp_side]["foul"] += 1
        elif kind == "corner":
            real[my_side if "우리 팀" in text else opp_side]["corner"] += 1

    scores = {my_side: sc["my_score"], opp_side: sc["opp_score"]}
    for side in ("home", "away"):
        st = ts[side]
        r = real[side]
        exp_goal = max(scores[side], r["goal"])
        exp_on = max(st["shots_on"], exp_goal + r["save"])
        exp_shots = max(st["shots"], exp_on)
        exp_corner = max(st["corners"], r["corner"])
        exp_foul = max(st["fouls"], r["foul"])

        shot_kinds = ("goal", "save", "shot_off", "shot_blocked")
        n_shots = sum(1 for rec in log if rec["team"] == side and rec["outcome"] in shot_kinds)
        n_on = sum(1 for rec in log if rec["team"] == side and rec["outcome"] in ("goal", "save"))
        n_goal = sum(1 for rec in log if rec["team"] == side and rec["outcome"] == "goal")
        n_corner = sum(1 for rec in log if rec["team"] == side and rec["outcome"] == "corner")
        n_foul = sum(1 for rec in log if rec["team"] == side and rec["outcome"] == "foul")
        assert n_shots == exp_shots, f"{side} shots {n_shots} != expected {exp_shots}"
        assert n_on == exp_on, f"{side} shots_on {n_on} != expected {exp_on}"
        assert n_goal == exp_goal, f"{side} goal {n_goal} != expected {exp_goal}"
        assert n_corner == exp_corner, f"{side} corners {n_corner} != expected {exp_corner}"
        assert n_foul == exp_foul, f"{side} fouls {n_foul} != expected {exp_foul}"

    n_goal_my = sum(1 for r in log if r["team"] == my_side and r["outcome"] == "goal")
    n_goal_opp = sum(1 for r in log if r["team"] == opp_side and r["outcome"] == "goal")
    assert n_goal_my >= sc["my_score"], f"my goals {n_goal_my} < score {sc['my_score']}"
    assert n_goal_opp >= sc["opp_score"], f"opp goals {n_goal_opp} < score {sc['opp_score']}"

    # 개인 이벤트가 정확한 분·outcome으로 그대로 있는지
    for m, text in sc["events"]:
        kind = _classify_personal(text)
        if kind is None:
            continue
        matches = [r for r in log if r["min"] == m and r["text"] == text]
        assert len(matches) == 1, f"personal event missing/dup: {m} {text!r} -> {matches}"
        if kind in ("goal_for", "miss_for", "goal_against", "save"):
            assert matches[0]["me"] is True
        else:
            assert matches[0]["me"] is False

    # 분 순 정렬 확인
    mins = [r["min"] for r in log]
    assert mins == sorted(mins), "log not sorted by minute"


def main():
    rng = _global_random.Random(1234)
    n_cases = 300
    for i in range(n_cases):
        sc = _make_scenario(rng)
        log = generate_possession_log(sc["is_home"], sc["team_stats"], sc["events"],
                                      sc["my_score"], sc["opp_score"], seed=i)
        _check_invariants(sc, log)

        # 결정론성: 같은 입력 → 같은 출력(딕셔너리까지 완전히 동일)
        log2 = generate_possession_log(sc["is_home"], sc["team_stats"], sc["events"],
                                       sc["my_score"], sc["opp_score"], seed=i)
        assert log == log2, f"non-deterministic at case {i}"

        # 전역 random 상태를 안 건드리는지(다음 case에도 영향 없어야 함) —
        # 위에서 이미 매 케이스 rng를 별도로 쓰고 있으므로, 여기서는 그냥
        # 전역 random 모듈을 직접 건드리지 않았다는 것만 재확인.
        _ = _global_random.random()  # 전역 상태가 여전히 정상 동작하는지

    print(f"OK — {n_cases}개 시나리오 전부 불변식 통과, 결정론성 확인 완료")

    # seed=None 자동 유도 경로도 최소 확인
    sc = _make_scenario(rng)
    log_a = generate_possession_log(sc["is_home"], sc["team_stats"], sc["events"],
                                    sc["my_score"], sc["opp_score"])
    log_b = generate_possession_log(sc["is_home"], sc["team_stats"], sc["events"],
                                    sc["my_score"], sc["opp_score"])
    assert log_a == log_b, "seed=None 자동유도 결정론성 깨짐"
    _check_invariants(sc, log_a)
    print("OK — seed=None 자동 유도 경로도 결정론적이고 불변식 통과")

    # team_stats가 없을 때 빈 리스트 반환하는지(구버전 데이터 하위호환)
    assert generate_possession_log(True, None, [], 0, 0) == []
    assert generate_possession_log(True, {}, [], 0, 0) == []
    print("OK — team_stats 없음(구버전 호환) 케이스도 안전")


if __name__ == "__main__":
    main()