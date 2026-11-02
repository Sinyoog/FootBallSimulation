"""[2026-10-27 안2] 클럽 포메이션 선택 — 선호 후보 안 실제 전력 비교(0.3점) 단위 테스트.
사용: python3 tools/test_formation_switch_margin.py"""
import _path  # noqa: F401
import importlib.util
import os
import random
import sys

import constants as C
import formation_logic as FL
from constants import FORMATION_SLOTS

FAILS = []
FN = list(FORMATION_SLOTS)


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("  FAIL:", msg)


POS = ["GK", "GK", "CB", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "CAM", "LW", "RW", "ST", "ST", "CF",
       "CB", "LM", "RM", "CM", "LB", "RB", "LWB", "RWB"]


def roster(r, n=None):
    n = n or r.randint(18, 24)
    return [{"position": p, "ovr": r.randint(55, 90), "sub_pos": r.choice([None, None, "CM", "LW", "RB"])}
            for p in POS[:n]]


def call(pre, pref, seed, sfn=None, ten=0):
    g = random.Random(seed)
    out = FL.choose_formation_prepped(pre, "4-4-2", "BALANCED", rng=g, pref=pref, tenure=ten, tag=1,
                                      strength_fn=sfn)
    return out, g.getstate()


def t_rule():
    print("[1] 규칙: 대안이 0.3점 이상 강할 때만 전환, 아니면 주 선호")
    r = random.Random(1)
    M = C.MANAGER_PREF_SWITCH_MARGIN
    check(M == 0.3, f"기준값 {M}")
    n = 0
    for k in range(400):
        pre = FL.prep_roster(roster(r, 24))
        entries = [(p[0], p[2]) for p in pre]
        ok = [f for f in FN if not FL.starter_empty_cells(entries, f)]
        if len(ok) < 5:
            continue
        main = r.choice(ok); alts = tuple(r.sample([f for f in ok if f != main], 3))
        base = 80.0
        gap = r.choice([0.0, 0.1, 0.29, 0.2999, 0.3, 0.30001, 0.5, 1.2, -0.4])
        st = {main: base}
        win = r.randrange(3)
        for i, a in enumerate(alts):
            st[a] = base + (gap if i == win else gap - 0.25)
        (name, pen), _ = call(pre, (main, alts), k, lambda f: st.get(f, 0.0))
        best = max(alts, key=lambda a: st[a])
        exp = best if st[best] - st[main] >= M else main
        check(name == exp, f"k={k} gap={gap} → {name} (기대 {exp})")
        check(abs(pen - FL.formation_fit_penalty_prepped(pre, name, FORMATION_SLOTS[name])) < 1e-12, "반환 페널티 = 선택 포메이션 페널티")
        n += 1
    print(f"  사례 {n}")


def t_tie_and_excluded():
    print("[2] 대안 동률 = 대안 순서 앞쪽 / 주 선호 제외 → 대안 중 최고 전력 / 선호 전부 제외 → 기존 추첨")
    r = random.Random(2)
    seen = {"tie": 0, "main_ex": 0, "all_ex": 0}
    NOWING = ["GK", "GK", "CB", "CB", "CB", "CB", "CB", "LB", "RB", "CDM", "CDM", "CM", "CM", "CM", "CM",
              "CAM", "CAM", "ST", "ST", "ST"]
    for k in range(3000):
        pre = FL.prep_roster([{"position": p, "ovr": r.randint(55, 90), "sub_pos": None} for p in NOWING]
                             if k % 2 else roster(r))
        entries = [(p[0], p[2]) for p in pre]
        ex = [f for f in FN if FL.starter_empty_cells(entries, f)]
        ok = [f for f in FN if f not in ex]
        if not ex or not ok or len(ex) == len(FN):
            continue
        if len(ok) >= 3 and seen["tie"] < 50:
            main = ok[0]; alts = tuple(ok[1:3]) + ((ex[0],) if ex else ())
            st = {main: 70.0, alts[0]: 71.0, alts[1]: 71.0}
            (name, _), _ = call(pre, (main, alts), k, lambda f: st.get(f, 0.0))
            check(name == alts[0], f"동률 → 앞쪽 대안 {alts[0]}, 실제 {name}")
            seen["tie"] += 1
        if len(ok) >= 2 and seen["main_ex"] < 50:
            main = ex[0]; alts = (ok[0], ok[1], ex[-1] if len(ex) > 1 else ok[0])
            st = {ok[0]: 60.0, ok[1]: 60.1}
            (name, _), _ = call(pre, (main, alts), k, lambda f: st.get(f, 0.0))
            check(name == ok[1], f"주 선호 제외 → 최고 전력 대안 {ok[1]}, 실제 {name}")
            seen["main_ex"] += 1
        if len(ex) >= 4 and seen["all_ex"] < 50:
            pref = (ex[0], tuple(ex[1:4]))
            a, sa = call(pre, pref, k, lambda f: 99.0)
            b, sb = call(pre, pref, k, None)
            check(a == b and sa == sb, "선호 전부 제외 → 기존 점수식 추첨과 같음")
            seen["all_ex"] += 1
    print(f"  {seen}")
    check(all(v > 0 for v in seen.values()), f"사례 부족 {seen}")


def t_rng_same():
    print("[3] 난수 소비: strength_fn 유무와 무관하게 같음(포메이션당 1회 + choices 1회)")
    r = random.Random(3); bad = 0
    for k in range(300):
        pre = FL.prep_roster(roster(r))
        pref = (r.choice(FN), tuple(r.sample(FN, 3)))
        _, s1 = call(pre, pref, k, lambda f: r.random() * 0 + 75.0)
        _, s2 = call(pre, pref, k, None)
        bad += s1 != s2
    check(bad == 0, f"난수 상태 불일치 {bad}")


def _load_old():
    p = os.environ.get("OLD_FORMATION_LOGIC")
    if not p or not os.path.exists(p):
        return None
    spec = importlib.util.spec_from_file_location("fl_old", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def t_unchanged_without_fn():
    print("[4] strength_fn 없음(대표팀·U-23·스위치 OFF 경로) = 수정 전 함수와 결과·난수 상태 동일")
    old = _load_old()
    if old is None:
        print("  (OLD_FORMATION_LOGIC 미지정 — 건너뜀)")
        return
    r = random.Random(4); bad = 0
    for k in range(500):
        pre = FL.prep_roster(roster(r))
        pref = (r.choice(FN), tuple(r.sample(FN, 3))) if k % 2 else None
        ten = r.randint(0, 2)
        g1 = random.Random(k); g2 = random.Random(k)
        a = FL.choose_formation_prepped(pre, "4-4-2", "BALANCED", rng=g1, pref=pref, tenure=ten, tag=("NT", "x"))
        b = old.choose_formation_prepped(pre, "4-4-2", "BALANCED", rng=g2, pref=pref, tenure=ten, tag=("NT", "x"))
        bad += (a != b) or (g1.getstate() != g2.getstate())
    check(bad == 0, f"수정 전과 다름 {bad}/500")


def t_callers():
    print("[5] 호출부: 클럽 시즌 선택만 strength_fn을 넘김(대표팀·U-23은 안 넘김)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for f, want in (("ai_lifecycle.py", 1), ("intl_engine.py", 0), (os.path.join("competition", "olympics.py"), 0)):
        n = open(os.path.join(root, f), encoding="utf-8").read().count("strength_fn=")
        check(n == want, f"{f}: strength_fn= {n}회(기대 {want})")


if __name__ == "__main__":
    t_rule(); t_tie_and_excluded(); t_rng_same(); t_unchanged_without_fn(); t_callers()
    print("\n결과:", "전부 통과" if not FAILS else f"실패 {len(FAILS)}건")
    sys.exit(1 if FAILS else 0)