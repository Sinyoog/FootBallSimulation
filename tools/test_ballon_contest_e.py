# -*- coding: utf-8 -*-
"""[2026-11-16 신설] 발롱도르 상위 3명 경쟁(설계안 E — ballon_award.contest_top3) 단위 테스트 — DB 없음.
실행: python tools/test_ballon_contest_e.py"""
import _path  # noqa: F401
import sys

import ballon_award as B

fails = []


def check(name, ok, info=""):
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" — {info}" if info else ""))
    if not ok:
        fails.append(name)


def P(pid, team, personal, intl=0.0, rank=5, stage=None, wp=1.0, n=20, cup=0.0, lvl=1.0):
    return {"player_id": pid, "team_id": team, "personal": personal, "s_intl": intl, "league_rank": rank,
            "league_n": n, "relegated": False, "league_level": lvl, "cup_v": cup, "cont_stage": stage, "cont_wp": wp}


def ids(lst):
    return [e["player_id"] for e in lst]


print("A. 표·클럽 성과")
check("리그 1위 1.0 / 2위 0.65 / 3위 0.5 / 4위 0.4", [B.e_league_rank_value(r, 20) for r in (1, 2, 3, 4)] == [1.0, 0.65, 0.5, 0.4])
check("리그 상위 30%·50%·75%·그 아래", [B.e_league_rank_value(r, 20) for r in (6, 10, 15, 16)] == [0.3, 0.2, 0.1, 0.05])
check("강등 0", B.e_league_rank_value(18, 20, True) == 0.0)
v = B.e_club_value(P(1, 1, 5.0, rank=1, stage="우승", wp=1.0, cup=1.0))
check("리그 우승 + 컵 우승 + 챔스 우승(가중치·출전 1) = 1.0", abs(v - 1.0) < 1e-9, f"{v}")
v2 = B.e_club_value(P(1, 1, 5.0, rank=2, stage="8강", wp=0.8, lvl=0.9))
check("리그 2위·8강·출전 0.8·수준 0.9 = 0.4×0.65×0.9 + 0.5×0.35×0.8", abs(v2 - (0.4 * 0.65 * 0.9 + 0.5 * 0.35 * 0.8)) < 1e-9)
check("단계 없음(대륙대회 미참가) → 대륙 0", abs(B.e_club_value(P(1, 1, 5.0, rank=1)) - 0.4) < 1e-9)

print("B. 상위 3명 경쟁")
# 기존 순서(총점순): 1(개인 7.0, 무관) 2(6.8) 3(6.6) 4(6.2, 큰 성과 에이스) 5(6.09, 에이스) 6(팀1 조연, 6.5) ...
top = [P(1, 10, 7.0, rank=3), P(2, 20, 6.8, rank=4), P(3, 30, 6.6, rank=5), P(4, 40, 6.2, rank=1, stage="우승"),
       P(5, 50, 6.09, rank=1, stage="우승"), P(6, 10, 6.5, rank=3, stage=None), P(7, 70, 5.0)] + [P(100 + i, 100 + i, 4.0) for i in range(20)]
new = B.contest_top3(top)
check("Top30 구성 불변", sorted(ids(new)) == sorted(ids(top)))
check("11위 이후 순서 불변", ids(new)[10:] == ids(top)[10:])
check("개인 차 0.4(≤0.5) 큰 성과 에이스(4) 도전 → 3위 안 진입", 4 in ids(new)[:3], str(ids(new)[:5]))
check("개인 차 0.51 에이스(5) 도전자 아님 → 3위 밖", 5 not in ids(new)[:3])
check("같은 팀 조연(6, 팀 10의 2순위)은 도전자 아님", 6 not in ids(new)[:3])
check("밀려난 기존 3위 이내 선수는 4위부터 기존 총점 순서", ids(new)[3:6] == [x for x in ids(top)[:7] if x not in ids(new)[:3]][:3], str(ids(new)[:7]))
check("순위 바뀐 선수만 contest_moved", all(e["contest_moved"] == (e is not top[i]) for i, e in enumerate(new)))
check("입력 리스트 순서 불변", ids(top)[:3] == [1, 2, 3])
# 경계: 정확히 0.5
top_b = [P(1, 10, 7.0), P(2, 20, 6.8), P(3, 30, 6.6), P(4, 40, 6.1, rank=1, stage="우승")] + [P(100 + i, 100 + i, 4.0) for i in range(10)]
check("개인 차 정확히 0.5 → 도전자(진입)", 4 in ids(B.contest_top3(top_b))[:3])
# 팀 안 배분: 상위 3명에 같은 팀 두 명 — 2순위는 클럽 성과 50%
t2 = [P(1, 10, 6.6, rank=1, stage="우승", cup=1.0), P(2, 10, 6.5, rank=1, stage="우승", cup=1.0), P(3, 30, 6.4, rank=2)] + [P(100 + i, 100 + i, 4.0) for i in range(10)]
n2 = B.contest_top3(t2)
c1, c2 = n2[[e["player_id"] for e in n2].index(1)]["contest"], n2[[e["player_id"] for e in n2].index(2)]["contest"]
check("팀 2순위 경쟁 점수 = 개인 + 클럽 2.5×1.0×50%", abs(c2 - (6.5 + 2.5 * 1.0 * 0.5)) < 1e-9 and abs(c1 - (6.6 + 2.5)) < 1e-9, f"{c1:.2f} {c2:.2f}")
# 3순위(상위 10명 안 같은 팀 3번째) → 0%
t3 = [P(1, 10, 6.9, rank=1, stage="우승"), P(2, 10, 6.8, rank=1, stage="우승"), P(3, 10, 6.7, rank=1, stage="우승")] + [P(100 + i, 100 + i, 4.0) for i in range(10)]
n3 = B.contest_top3(t3)
check("팀 3순위 클럽 성과 0%", abs(n3[[e["player_id"] for e in n3].index(3)]["contest"] - 6.7) < 1e-9)
# 개인 격차가 크면 개인 우선(클럽 성과 최대 2.5×차이로만 뒤집음)
t4 = [P(1, 10, 7.5, rank=8), P(2, 20, 6.0, rank=1, stage="우승"), P(3, 30, 5.9)] + [P(100 + i, 100 + i, 4.0) for i in range(10)]
check("클럽 성과 차가 개인 차보다 크면 역전(2.5×(0.9−0.4·0.2) > 1.5)", ids(B.contest_top3(t4))[0] == 2)
t5 = [P(1, 10, 8.6, rank=8), P(2, 20, 6.0, rank=1, stage="우승"), P(3, 30, 5.9)] + [P(100 + i, 100 + i, 4.0) for i in range(10)]
check("개인 차 2.6 > 클럽 성과 최대 차 → 개인 우선", ids(B.contest_top3(t5))[0] == 1)
check("1명·빈 목록", B.contest_top3([P(1, 1, 5.0)])[0]["player_id"] == 1 and B.contest_top3([]) == [])

print()
if fails:
    print(f"실패 {len(fails)}건: {fails}")
    sys.exit(1)
print("전부 통과")