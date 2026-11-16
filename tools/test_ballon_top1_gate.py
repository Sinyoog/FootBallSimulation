# -*- coding: utf-8 -*-
"""[2026-11 신설] 발롱도르 1위 최소 자격(ballon_award.top1_paths / apply_top1_gate) 단위 테스트 — DB 없음.
실행: python tools/test_ballon_top1_gate.py"""
import _path  # noqa: F401
import copy
import sys

import ballon_award as B

fails = []


def check(name, ok, info=""):
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" — {info}" if info else ""))
    if not ok:
        fails.append(name)


def P(**kw):
    return B.top1_paths(kw)


def wc(stage, apps, n=7, mvp=False, scorer=False):
    return {"stage": stage, "apps": apps, "n": n, "mvp": mvp, "scorer": scorer}


print("A. 유럽 챔스 4강 이상")
for st in ("4강", "준우승", "우승"):
    check(f"A 챔스 {st} → A", "A" in P(cl_stage=st))
for st in ("8강", "16강", "플레이오프", None):
    check(f"A 챔스 {st} → 자격 없음(다른 조건 없음)", P(cl_stage=st) == [])

print("B. 월드컵·유로·코파 4강 + 출전 50%")
check("B 4위 4/7 → B", P(intl=[wc("4위", 4)]) == ["B"])
check("B 우승 3/7(50% 미만) → 없음", P(intl=[wc("우승", 3)]) == [])
check("B 코파 3위 3/6(정확히 50%) → B", P(intl=[wc("3위", 3, n=6)]) == ["B"])
check("B 8강 4/7 개인상 없음 → 없음", P(intl=[wc("8강", 4)]) == [])
check("B-예외 8강 MVP 4/7 → B-예외", P(intl=[wc("8강", 4, mvp=True)]) == ["B-예외"])
check("B-예외 16강 득점왕 5/7 → B-예외", P(intl=[wc("16강", 5, scorer=True)]) == ["B-예외"])
check("B-예외 8강 MVP 3/7(출전 부족) → 없음", P(intl=[wc("8강", 3, mvp=True)]) == [])
check("B 출전 0·대회 경기 수 0 → 없음", P(intl=[wc("우승", 0, n=0)]) == [])
check("B 두 대회 중 하나만 충족 → B", P(intl=[wc("8강", 7), wc("준우승", 5, n=6)]) == ["B"])

print("C. 리그 경로 (1부 + 챔스 8강 이상)")
base = dict(tier1=True, cl_stage="8강")
check("C-1 MVP + 공격P 2위·평점 1위 → C-1", P(**base, league_awards={"MVP"}, ga_rank=2, rt_rank=1) == ["C-1"])
check("C-1 MVP + 공격P 1위·평점 5위 → C-1", P(**base, league_awards={"MVP"}, ga_rank=1, rt_rank=5) == ["C-1"])
check("C-1 MVP + 공격P 8위·평점 5위 → 없음", P(**base, league_awards={"MVP"}, ga_rank=8, rt_rank=5) == [])
check("C-1 MVP 압도 + 챔스 16강 → 없음", P(tier1=True, cl_stage="16강", league_awards={"MVP"}, ga_rank=1, rt_rank=1) == [])
check("C-1 MVP 압도 + 챔스 미참가 → 없음", P(tier1=True, cl_stage=None, league_awards={"MVP"}, ga_rank=1, rt_rank=1) == [])
check("C-1 2부 리그 → 없음", P(tier1=False, cl_stage="8강", league_awards={"MVP"}, ga_rank=1, rt_rank=1) == [])
check("C-2 득점왕 + 공격P 1위·평점 3위 → C-2", P(**base, league_awards={"득점왕"}, ga_rank=1, rt_rank=3) == ["C-2"])
check("C-2 도움왕 + 공격P 1위·평점 1위 → C-2", P(**base, league_awards={"도움왕"}, ga_rank=1, rt_rank=1) == ["C-2"])
check("C-2 득점왕 + 평점 4위 → 없음", P(**base, league_awards={"득점왕"}, ga_rank=1, rt_rank=4) == [])
check("C-2 득점왕 + 공격P 2위 → 없음", P(**base, league_awards={"득점왕"}, ga_rank=2, rt_rank=1) == [])
check("C-2 순위 정보 없음 → 없음", P(**base, league_awards={"득점왕"}) == [])
check("C 베스트11만 → 없음", P(**base, league_awards=set(), ga_rank=1, rt_rank=1) == [])
check("C 챔스 4강 + MVP 압도 → A와 C-1 둘 다", P(tier1=True, cl_stage="4강", league_awards={"MVP"}, ga_rank=1, rt_rank=1) == ["A", "C-1"])
check("빈 입력 → 없음", B.top1_paths({}) == [])

print("D. 1위 선정(apply_top1_gate)")
top = [{"player_id": i, "ok": ok} for i, ok in enumerate([False, False, True, True, False])]
keep = copy.deepcopy(top)
paths_of = lambda e: ["X"] if e["ok"] else []
new, p = B.apply_top1_gate(top, paths_of)
check("D 3번째(자격자 중 최고점)가 1위, 나머지 순서 유지", [e["player_id"] for e in new] == [2, 0, 1, 3, 4] and p == ["X"],
      str([e["player_id"] for e in new]))
check("D 입력 리스트 불변", top == keep)
top2 = [{"player_id": i, "ok": ok} for i, ok in enumerate([True, False, True])]
new2, p2 = B.apply_top1_gate(top2, paths_of)
check("D 1위가 자격자면 그대로", [e["player_id"] for e in new2] == [0, 1, 2] and p2 == ["X"])
top3 = [{"player_id": i, "ok": False} for i in range(30)]
new3, p3 = B.apply_top1_gate(top3, paths_of)
check("D 자격자 없음 → 기존 순서 그대로(공석 없음)", [e["player_id"] for e in new3] == list(range(30)) and p3 == [])
check("D 빈 목록", B.apply_top1_gate([], paths_of) == ([], []))
check("D 길이·구성 불변", len(new) == len(top) and {e["player_id"] for e in new} == {e["player_id"] for e in top})

print("E. [2026-11-15 개정] A 확장 · B 대회별 차등 · D 신설")
check("A 유로파 우승 → A", P(el_win=True) == ["A"])
check("A 남미 챔스 우승 → A", P(sa_cl_win=True) == ["A"])
check("A 유로파 준우승(el_win 아님) → 없음", P(el_win=False, cl_stage=None) == [])
def it(name, stage, apps=7, n=7, mvp=False, scorer=False):
    return {"name": name, "stage": stage, "apps": apps, "n": n, "mvp": mvp, "scorer": scorer}
check("B 월드컵 4위 → B", P(intl=[it("월드컵", "4위")]) == ["B"])
check("B 유로 4강 → B", P(intl=[it("유로(EURO)", "4강")]) == ["B"])
check("B 코파 3위 → 없음(결승 필요)", P(intl=[it("코파 아메리카", "3위")]) == [])
check("B 코파 준우승 → B", P(intl=[it("코파 아메리카", "준우승")]) == ["B"])
for nm in ("남북미 대륙컵", "아시안컵", "아프리카 네이션스컵", "골드컵"):
    check(f"B {nm} 준우승 → 없음", P(intl=[it(nm, "준우승")]) == [])
    check(f"B {nm} 우승 → B", P(intl=[it(nm, "우승")]) == ["B"])
    check(f"B {nm} 8강 MVP → 없음(예외는 월드컵·유로·코파만)", P(intl=[it(nm, "8강", mvp=True)]) == [])
check("B 아시안컵 우승 출전 3/7 → 없음", P(intl=[it("아시안컵", "우승", apps=3)]) == [])
check("B 유럽 네이션스컵 우승 → 없음(대상 아님)", P(intl=[it("유럽 네이션스컵", "우승")]) == [])
check("B 오세아니아컵 우승 → 없음(대상 아님)", P(intl=[it("오세아니아컵", "우승")]) == [])
check("B-예외 코파 8강 득점왕 → B-예외", P(intl=[it("코파 아메리카", "8강", scorer=True)]) == ["B-예외"])
check("B-예외 유로 16강 MVP → B-예외", P(intl=[it("유로(EURO)", "16강", mvp=True)]) == ["B-예외"])
check("D 5대 리그 우승 + MVP → D", P(tier1=True, big5_champ=True, league_awards={"MVP"}) == ["D"])
check("D 5대 리그 우승 + 득점왕 → D", P(tier1=True, big5_champ=True, league_awards={"득점왕"}) == ["D"])
check("D 5대 리그 우승 + 도움왕만 → 없음", P(tier1=True, big5_champ=True, league_awards={"도움왕"}) == [])
check("D 우승 아님 + MVP → 없음", P(tier1=True, big5_champ=False, league_awards={"MVP"}) == [])
check("D 유로파 우승 + 리그 우승 MVP → A와 D", P(el_win=True, tier1=True, big5_champ=True, league_awards={"MVP"}) == ["A", "D"])
check("2004 임민준형(유로파 우승·리그 MVP·공격P 1위, 챔스 없음) → A·D",
      P(el_win=True, tier1=True, big5_champ=True, league_awards={"MVP", "득점왕"}, ga_rank=1, rt_rank=1) == ["A", "D"])

print()
if fails:
    print(f"실패 {len(fails)}건: {fails}")
    sys.exit(1)
print("전부 통과")