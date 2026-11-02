"""[2026-10-30] 게르트 뮐러상 공동 순위 단위 테스트(DB 없음).
사용: python tools/test_gerd_muller_rank.py"""
import _path  # noqa: F401
import sys
import game_engine as GE

FAILS = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def T(*totals):
    return {pid: {"total": t, "matches": 30, "league": t, "cup": 0, "cont": 0, "intl": 0}
            for pid, t in enumerate(totals, start=1)}


r = [(rk, pid) for rk, pid, _d in GE.gerd_muller_rank(T(52, 52, 40, 38))]
check("2명 공동 1위 → 다음은 3위", [rk for rk, _ in r] == [1, 1, 3, 4], str(r))
r = [rk for rk, _p, _d in GE.gerd_muller_rank(T(52, 52, 52, 40))]
check("3명 공동 1위 → 다음은 4위", r == [1, 1, 1, 4], str(r))
r = [rk for rk, _p, _d in GE.gerd_muller_rank(T(*range(60, 40, -1)))]
check("동점 없으면 1~10위 10명", r == list(range(1, 11)), str(r))
r = [rk for rk, _p, _d in GE.gerd_muller_rank(T(60, 59, 58, 57, 56, 55, 54, 53, 50, 50, 50, 49))]
check("9위 동점 3명 → 공동 9위 3명 모두 포함, 그 다음(12위)은 제외", r == [1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9], str(r))
r = [rk for rk, _p, _d in GE.gerd_muller_rank(T(60, 59, 58, 57, 56, 55, 54, 53, 52, 50, 50, 49))]
check("공동 10위 2명 포함, 그 다음(12위)은 제외", r == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10], str(r))
d = {1: {"total": 40, "matches": 35}, 2: {"total": 40, "matches": 30}}
r = [(rk, pid) for rk, pid, _d in GE.gerd_muller_rank(d)]
check("공동 순위 안의 표시 순서는 경기 수 적은 순(순위는 같음)", r == [(1, 2), (1, 1)], str(r))
print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
sys.exit(1 if FAILS else 0)