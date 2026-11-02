"""[2026-10 감독 철학] 단위 테스트 — 사용: python3 tools/test_manager_philosophy.py
DB 없이 도는 테스트(확률표·선호 목록·목표 인원·인원 가중·역할 판정·월드 생성 후보·포메이션 점수식)와
작은 임시 DB로 도는 테스트(선수단 기준 포메이션 하나로 통일, 스위치 OFF 무변경)."""
import _path  # noqa: F401
import collections
import os
import random
import sqlite3
import sys
import tempfile

import constants as C
import manager_philosophy as MP
import squad_plan as SP
import squad_targets as ST

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("  FAIL:", msg)


def t_profile_distribution():
    print("[1] 철학·4축 분포(해시 20,000명)")
    N = 20000
    ph = collections.Counter(); atk = collections.Counter(); bld = collections.Counter()
    prs = collections.Counter(); mt = collections.Counter()
    un = 0
    un_by = collections.Counter(); n_by = collections.Counter()
    for i in range(N):
        p = MP.draw_profile(f"mgr:123:{i}")
        ph[p["philosophy"]] += 1; atk[p["style_attack"]] += 1; bld[p["style_buildup"]] += 1
        prs[p["style_press"]] += 1; mt[p["manager_type"]] += 1
        a = MP.ATK.index(p["style_attack"]); b = MP.BLD.index(p["style_buildup"]); r = MP.PRS.index(p["style_press"])
        u = (a >= 3 and r == 0) or (a <= 1 and r == 2) or (a == 4 and b == 0)
        un += u; un_by[p["philosophy"]] += u; n_by[p["philosophy"]] += 1
        check(p["pref_main"] in C.MANAGER_PHILOSOPHY_FORMATIONS[p["philosophy"]]["main"], "주 선호는 주력 등급")
        check(len(set(p["pref_alts"])) == len(p["pref_alts"]) == C.MANAGER_PREF_ALT_N, "대안 3개, 중복 없음")
        check(p["pref_main"] not in p["pref_alts"], "대안에 주 선호 없음")
        if FAILS:
            return
    for k, w in C.MANAGER_PHILOSOPHY_WEIGHTS.items():
        check(abs(ph[k] / N * 100 - w) < 1.0, f"철학 {k} 비율 {ph[k]/N*100:.2f} vs {w}")
    cur = {"atk": (10, 25, 30, 25, 10), "bld": (30, 45, 25), "prs": (28, 47, 25), "mgr": (22, 24, 12, 12, 15, 15)}
    for name, cnt, keys in (("atk", atk, MP.ATK), ("bld", bld, MP.BLD), ("prs", prs, MP.PRS), ("mgr", mt, MP.MT)):
        for k, base in zip(keys, cur[name]):
            check(abs(cnt[k] / N * 100 - base) <= 3.5, f"{name} {k} {cnt[k]/N*100:.2f} vs 기존 {base} (±3%p + 표본 여유)")
    check(un / N <= 0.065, f"부자연 조합 {un/N*100:.2f}% ≤ 6%(+표본 여유)")
    for k in n_by:
        check(un_by[k] / n_by[k] <= 0.085, f"철학 {k} 부자연 {un_by[k]/n_by[k]*100:.2f}% ≤ 8%(+표본 여유)")
    print(f"  부자연 조합 {un/N*100:.2f}%, 철학별 최대 {max(un_by[k]/n_by[k] for k in n_by)*100:.2f}%")
    p1 = MP.draw_profile("mgr:9:77"); p2 = MP.draw_profile("mgr:9:77")
    check(p1 == p2, "같은 키 = 같은 프로필(결정성)")


def t_targets_and_size_weight():
    print("[2] 목표 인원·인원 가중")
    check(SP.target_size("S", 1) == 26 and SP.target_size("A", 1) == 26, "SS·S·A급 1부 26")
    check(SP.target_size("B", 1) == 25 and SP.target_size("F", 1) == 25, "B~F급 1부 25")
    check(SP.target_size("S", 2) == 25 and SP.target_size("C", 3) == 24, "2부 25, 3부 24")
    check(SP.target_size("D", 4) == 23 and SP.target_size("D", 8) == 23, "4부 이하 23")
    check(SP.target_size("C", 4, youth=True) == 26, "유스 산하팀 26")
    check(SP.is_offer_vacancy_team("A", 1) and not SP.is_offer_vacancy_team("B", 1)
          and not SP.is_offer_vacancy_team("S", 2) and not SP.is_offer_vacancy_team("S", 1, youth=True),
          "오퍼 빈자리 대상 = SS·S·A급 1부(산하팀 제외)")
    check(SP.size_weight_t(25, 26) == 3.0 and SP.size_weight_t(24, 26) == 5.0 and SP.size_weight_t(20, 26) == 5.0,
          "목표 미달 1+2×부족, 최대 5")
    check(SP.size_weight_t(25, 25) == 1.0 and SP.size_weight_t(23, 23) == 1.0, "[설계 6-2] 목표 = 1")
    check(SP.size_weight_t(26, 26) == 0.0 and SP.size_weight_t(26, 25) == 0.0 and SP.size_weight_t(30, 23) == 0.0,
          "26 이상 0")
    import math
    check(abs(SP.size_weight_t(24, 23) - math.exp(-1 / 0.15)) < 1e-15
          and abs(SP.size_weight_t(25, 23) - math.exp(-2 / 0.15)) < 1e-15,
          "[설계 6-2] 목표 초과 = exp(-(n-목표)/0.15)")


def t_role_tables_all_formations():
    print("[3] 역할 칸 — 20개 포메이션 × 11~28명")
    for f in C.FORMATION_SLOTS:
        for n in range(11, 29):
            s, sr, t = SP.role_tables(f, n)
            check(sum(t.values()) == n, f"{f} {n} 목표 합 = N")
            check(sum(sr.values()) == min(n, 22), f"{f} {n} 주전+로테이션 합 = min(N,22)")
            check(all(s[p] <= sr[p] <= t[p] for p in ST.TARGET_POSITIONS), f"{f} {n} 주전 ≤ 주전+로테 ≤ 목표")
            check(sum(s.values()) == 11, f"{f} 주전 11")


def _team(players):
    return [{"id": i + 1, "position": p, "ovr": o, "age": a} for i, (p, o, a) in enumerate(players)]


def t_role_judgement():
    print("[4] 역할 판정 — 경계·동률·우선순위")
    f = "4-4-2"     # CB 주전 2, 26명 목표에서 CB 5(2/2/1)
    players = _team([("GK", 70, 28), ("GK", 60, 24), ("CB", 70, 27), ("CB", 68, 26), ("CB", 65, 25), ("CB", 60, 22),
                     ("LB", 66, 25), ("RB", 66, 25), ("CM", 70, 26), ("CM", 69, 27), ("LW", 67, 24), ("RW", 67, 24),
                     ("ST", 71, 28), ("ST", 70, 27)])
    rc = SP.RoleCache({1: players}, {1: (f, 26)})
    mv = lambda pos, ovr, age=26, pid=999: {"id": pid, "position": pos, "ovr": ovr, "age": age}
    # CB: 보유 4(70,68,65,60), 주전 2 → 최하위 주전 68, 주전+로테 4 → 최하위 로테 60, 목표 5
    check(rc.classify(1, mv("CB", 70)) == "starter_upgrade", "최하위 주전 +2 = 주전 보강")
    check(rc.classify(1, mv("CB", 69)) == "rotation_upgrade", "최하위 주전 +1 → 주전 보강 아님, 최하위 로테(60)+2 이상 → 로테이션 보강")
    check(rc.classify(1, mv("CB", 68)) == "rotation_upgrade", "최하위 주전과 동률 → 주전 보강 아님")
    check(rc.classify(1, mv("CB", 61)) == "rotation_contest", "최하위 로테 +1 = 경쟁(보강 아님)")
    check(rc.classify(1, mv("CB", 60)) == "rotation_contest", "최하위 로테와 동률 = 경쟁")
    check(rc.classify(1, mv("CB", 59)) == "rotation_contest", "최하위 로테 −1 = 경쟁")
    check(rc.classify(1, mv("CB", 58)) == "backup", "최하위 로테 −2 = 백업(보유 4 < 목표 5)")
    check(rc.role_weight(1, mv("CB", 58)) == C.ROLE_W_BACKUP[0], "백업 자리 있음 ×1.0")
    check(rc.classify(1, mv("CB", 50, age=19)) == "youth", "21세 이하·로테 밖 = 유망주")
    check(rc.classify(1, mv("CB", 75, age=19)) == "starter_upgrade", "주전급이면 나이 무관 주전 보강")
    check(rc.role_weight(1, mv("CAM", 60)) == C.ROLE_W_BACKUP[1], "목표 0칸(4-4-2 CAM) = 백업 꽉 참 ×0.3")
    # LB: 보유 1, 주전 1, 주전+로테 2 → 로테이션 공백
    check(rc.classify(1, mv("LB", 50)) == "rotation_gap", "로테이션 공백")
    # 주전 공백 0명: CB를 다 빼면
    rc2 = SP.RoleCache({1: [p for p in players if p["position"] != "RB"]}, {1: (f, 26)})
    check(rc2.role_weight(1, mv("RB", 40)) == C.ROLE_W_STARTER_EMPTY0, "주전 칸 0명 ×4.0")
    # 부포: LB 선수가 CB 부포를 가지면 두 칸 중 높은 판정
    rc3 = SP.RoleCache({1: players}, {1: (f, 26)}, sub_of={999: "CB"})
    check(rc3.role_weight(1, mv("LB", 71)) == max(C.ROLE_W_STARTER_UPGRADE, C.ROLE_W_STARTER_UPGRADE),
          "부포 칸(CB) 판정 반영")
    # 갱신: 영입 후 같은 함수로 재판정
    rc.add(1, mv("CB", 75, pid=500))
    check(rc.classify(1, mv("CB", 70)) == "rotation_upgrade", "영입 후 최하위 주전 70 → 70은 로테이션 보강(65+2 이상)")
    rc.remove(1, {"id": 500, "position": "CB", "ovr": 75})
    check(rc.classify(1, mv("CB", 70)) == "starter_upgrade", "방출 후 원래대로")
    # 판매 가중 = 기존 함수(목표표 칸 기준)
    from formation_logic import position_sell_weight
    e = players[2]
    exp_w = 1.0 + C.POSITION_SELL_STRENGTH * (position_sell_weight(4, 5) - 1.0)
    check(abs(rc.sell_weight(1, e) - exp_w) < 1e-12, "판매 가중 = position_sell_weight(보유, 칸 목표)")


def t_world_gen_bench():
    print("[5] 월드 생성 후보 포지션 — 선발 11자리 유지, 목표 인원")
    import database as D
    D._PHIL_GEN.clear()
    D._PHIL_GEN.update(on=True, size=True, seed=4242, aff={7})
    starters = ["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
    for tid, cname, tier, n_exp in ((1, "잉글랜드", 1, 26), (2, "대한민국", 1, 25), (3, "잉글랜드", 2, 25),
                                     (4, "잉글랜드", 3, 24), (5, "잉글랜드", 5, 23), (7, "잉글랜드", 4, 26)):
        full = D._build_squad_positions(None, tier, {"tid": tid, "cname": cname, "tier": tier})
        bench = full[11:]
        check(full[:11] == starters, f"팀 {tid} 선발 11자리 유지")
        check(len(full) == n_exp, f"팀 {tid} 인원 {len(full)} = 목표 {n_exp} (A·B 켬)")
        check("GK" in bench, f"팀 {tid} 후보 GK 포함")
    D._PHIL_GEN.clear()
    check(D._build_squad_positions("S", 1, {"tid": 1, "cname": "잉글랜드", "tier": 1})[:11] == starters,
          "OFF면 기존 경로(선발 목록 동일)")


class _CountRng(random.Random):
    def __init__(self, seed):
        super().__init__(seed)
        self.n = 0

    def random(self):
        self.n += 1
        return super().random()


def t_formation_choice():
    print("[6] 시즌 포메이션 선택 — 난수 소비 동일, 선호 반영")
    from formation_logic import prep_roster, choose_formation_prepped
    roster = [{"position": p, "ovr": 70} for p in
              ["GK", "GK", "CB", "CB", "CB", "CB", "CB", "LB", "RB", "LB", "RB", "CDM", "CM", "CM", "CM", "CAM",
               "LW", "RW", "LW", "RW", "ST", "ST", "ST"]]
    pr = prep_roster(roster)
    r1 = _CountRng(1); choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=r1)
    r2 = _CountRng(1); choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=r2, pref=("3-4-3", ("5-2-3",)), tenure=2)
    check(r1.n == r2.n, f"random() 호출 수 같음 {r1.n} vs {r2.n}")
    hits = collections.Counter()
    for s in range(400):
        name, _ = choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=random.Random(s),
                                           pref=("4-3-3", ("4-2-3-1", "4-1-4-1", "4-3-2-1")), tenure=2)
        hits[name] += 1
    share = sum(hits[f] for f in ("4-3-3", "4-2-3-1", "4-1-4-1", "4-3-2-1")) / 400
    check(share >= 0.8, f"선호(주+대안) 선택 비율 {share:.2f} ≥ 0.8 (3시즌+)")
    check(MP.tenure_index(2005, 2005) == 0 and MP.tenure_index(2006, 2005) == 1 and MP.tenure_index(2010, 2005) == 2,
          "부임 연차 인덱스 0/1/2")


def t_db_plan_and_off():
    print("[7] 임시 DB — 선수단 기준 포메이션 통일, 스위치 OFF 무변경")
    tmp = tempfile.mkdtemp()
    c = sqlite3.connect(os.path.join(tmp, "t.db"))
    c.execute("CREATE TABLE teams(id INTEGER PRIMARY KEY, formation TEXT)")
    c.execute("CREATE TABLE team_managers(team_id INTEGER, manager_id INTEGER, start_year INTEGER, end_year INTEGER)")
    c.executemany("INSERT INTO teams VALUES(?,?)", [(1, "4-4-2"), (2, "4-3-3"), (3, "5-4-1")])
    c.executemany("INSERT INTO team_managers VALUES(?,?,?,?)", [(1, 10, 2000, None), (2, 11, 2000, None),
                                                               (3, 12, 1999, 2000)])
    MP.ensure_table(c)
    c.execute(f"INSERT INTO {MP.TABLE} VALUES(10,'WINGBACK_3','3-4-3','3-5-2','3-4-2-1','5-2-3')")
    plan = MP.team_plan_formations(c)
    check(plan == {1: "3-4-3", 2: "4-3-3", 3: "5-4-1"}, f"주 선호 / 철학 없음 → 현재 / 공석 → 현재: {plan}")
    pp = MP.team_plan_prefs(c)
    check(pp == plan and pp[1].alts == ("3-5-2", "3-4-2-1", "5-2-3") and pp[2].alts == () and pp[3].alts == (),
          f"[R2] 보충·캐시·맞바꾸기 입력 = 같은 주 선호 + 대안: {[(k, str(v), v.alts) for k, v in pp.items()]}")
    import ai_lifecycle as AL
    old = C.SQUAD_SIZE_RULES_ENABLED
    try:
        C.SQUAD_SIZE_RULES_ENABLED = False
        check(AL._squad_min_max("S", 1) == (26, 26) and AL._squad_min_max("B", 1) == (23, 26)
              and AL._squad_min_max("S", 3) == (25, 28), "OFF: 기존 등급표 그대로")
        check(AL._is_fixed26("S", 2) and not AL._is_fixed26("B", 1), "OFF: 기존 26 고정 판정")
        C.SQUAD_SIZE_RULES_ENABLED = True
        check(AL._squad_min_max("S", 1) == (26, 26) and AL._squad_min_max("B", 1) == (25, 26)
              and AL._squad_min_max("S", 2) == (25, 26) and AL._squad_min_max("D", 6) == (23, 26)
              and AL._squad_min_max("D", 6, True) == (26, 26), "B 켬: (목표, 26)")
        check(not AL._is_fixed26("S", 2) and AL._is_fixed26("A", 1) and not AL._is_fixed26("B", 1),
              "B 켬: 오퍼 빈자리 대상 SS·S·A급 1부")
    finally:
        C.SQUAD_SIZE_RULES_ENABLED = old


# ── v2.1 R1~R4 ───────────────────────────────────────────────
class _ChoiceRng(random.Random):
    def __init__(self, seed):
        super().__init__(seed)
        self.n = 0
        self.weights = None

    def random(self):
        self.n += 1
        return super().random()

    def choices(self, population, weights=None, **kw):
        self.weights = list(weights)
        return super().choices(population, weights, **kw)


def t_r1_formation():
    print("[8] R1 — 4-2-4 대안 제외 · 빈 칸 포메이션 제외(held_assign) · 예외 기록 · 점수^4")
    import formation_logic as FL
    from formation_logic import prep_roster, choose_formation_prepped, starter_empty_cells
    n424_alt = n424_main = 0
    for i in range(20000):
        p = MP.draw_profile(f"mgr:r1:{i}")
        n424_alt += "4-2-4" in p["pref_alts"]
        n424_main += p["pref_main"] == "4-2-4"
    check(n424_alt == 0, f"대안에 4-2-4 {n424_alt}건 = 0")
    check(n424_main > 0, f"주 선호 4-2-4는 남음({n424_main}건)")
    check(all("4-2-4" not in F["alt"] for F in C.MANAGER_PHILOSOPHY_FORMATIONS.values()), "대안 목록에 4-2-4 없음")
    # 중복 배정 없음: LB(부포 CB) 1명은 LB·CB 두 칸 중 하나만 채운다
    e1 = [("GK", None), ("LB", "CB")]
    em = starter_empty_cells(e1, "4-4-2", 11)
    check(("CB" in em) != ("LB" in em), f"부포 보유자 1명은 1칸만: 빈 칸 {em}")
    e2 = [("GK", None), ("LB", "CB"), ("LB", None)]
    em2 = starter_empty_cells(e2, "4-4-2", 11)
    check("CB" not in em2 and "LB" not in em2, f"LB 2명(1명 부포 CB) → CB·LB 둘 다 채움: 빈 칸 {em2}")
    # 판정 배정이 squad_targets.held_assign과 같다
    import squad_targets as _st
    ent = [("GK", None), ("CB", None), ("CB", "RB"), ("CM", "CDM"), ("CM", None), ("LW", "LM"), ("ST", None)]
    h = _st.held_assign(ent, _st.position_targets("4-3-3", len(ent)))
    exp_empty = tuple(c for c in _st.TARGET_POSITIONS if _st.starter_counts("4-3-3")[c] > 0 and h[c] == 0)
    check(starter_empty_cells(ent, "4-3-3") == exp_empty, "빈 칸 판정 = held_assign(현재 인원, 그 포메이션 목표표)")
    # 빈 칸 포메이션 제외: RW 칸을 채울 선수 없음(RW·RM·RWB 등록·부포 없음)
    roster = [{"position": p, "ovr": 70} for p in
              ["GK", "GK", "CB", "CB", "CB", "CB", "CB", "LB", "RB", "LB", "RB", "CDM", "CDM", "CM", "CM", "CM",
               "CAM", "CAM", "LW", "ST", "ST", "ST"]]
    pr = prep_roster(roster)
    ent = [(x[0], x[2]) for x in pr]
    bad = {f for f in C.FORMATION_SLOTS if starter_empty_cells(ent, f)}
    check(0 < len(bad) < len(C.FORMATION_SLOTS), f"빈 칸 포메이션 {len(bad)}개(일부만)")
    picked_bad = 0
    for sd in range(300):
        name, _ = choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=random.Random(sd),
                                           pref=("4-4-2", ("4-2-3-1", "4-3-3")), tenure=0)
        picked_bad += name in bad
    check(picked_bad == 0, f"빈 칸 포메이션 선택 {picked_bad}회 = 0")
    r1 = _CountRng(5); choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=r1)
    r2 = _CountRng(5); choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=r2, pref=("4-4-2", ("4-3-3",)))
    check(r1.n == r2.n, f"제외가 있어도 random() 호출 수 같음 {r1.n} vs {r2.n}")
    # 예외: 모든 후보가 빈 칸 → 제외하지 않고 기록
    FL.PHIL_FORM_STATS.update(calls=0, excluded=0, exception=0, exception_tags=[])
    gk = prep_roster([{"position": "GK", "ovr": 60}] * 12)
    name, _ = choose_formation_prepped(gk, "4-4-2", "BALANCED", rng=random.Random(1),
                                       pref=("4-4-2", ()), tag=777)
    check(name in C.FORMATION_SLOTS, "예외여도 포메이션은 고른다")
    check(FL.PHIL_FORM_STATS["exception"] == 1 and FL.PHIL_FORM_STATS["exception_tags"] == [777],
          f"예외 기록(횟수·태그): {FL.PHIL_FORM_STATS}")
    # 점수^4: 같은 시드에서 지수 1의 가중을 4제곱한 값과 같다
    old = C.MANAGER_PREF_CHOICE_POW
    try:
        C.MANAGER_PREF_CHOICE_POW = 1
        ra = _ChoiceRng(9); choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=ra, pref=("4-4-2", ("4-3-3",)))
        C.MANAGER_PREF_CHOICE_POW = 4
        rb = _ChoiceRng(9); choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=rb, pref=("4-4-2", ("4-3-3",)))
    finally:
        C.MANAGER_PREF_CHOICE_POW = old
    check(len(ra.weights) == len(rb.weights) and all(abs(b - a ** 4) < 1e-12 for a, b in zip(ra.weights, rb.weights)),
          "상위 후보 가중 = 점수^4")


def t_r2_plan_targets():
    print("[9] R2 — 대안 칸 보장 목표표(불변조건·우선순위·보장 실패 기록·전 경로 같은 표)")
    import itertools
    forms = list(C.FORMATION_SLOTS)
    bad = fail = combos = 0
    cells = 0
    for main in forms:
        s = ST.starter_counts(main)
        rest = [f for f in forms if f != main]
        for alts in itertools.combinations(rest, 3):
            for n in (22, 23, 24, 25, 26):     # A 단독이면 N이 기존 보충선(22~26)
                t, seq, failed = SP.plan_targets_full(main, alts, n)
                combos += 1
                fail += len(failed)
                if (sum(t.values()) != n or min(t.values()) < 0 or any(t[c] < s[c] for c in ST.TARGET_POSITIONS)
                        or t["GK"] < 2):
                    bad += 1
                if collections.Counter([ST.SLOT_TO_TARGET[x] for x in C.FORMATION_SLOTS[main]] + list(seq)) \
                        != collections.Counter({k: v for k, v in t.items() if v}):
                    bad += 1
                for a in alts:
                    sa = ST.starter_counts(a)
                    for c in ST.TARGET_POSITIONS:
                        if sa[c] > 0 and t[c] == 0 and c not in failed:
                            bad += 1
                cells += sum(1 for c in ST.TARGET_POSITIONS if t[c] > 0 and ST.position_targets(main, n)[c] == 0)
    print(f"  조합 {combos:,} · 위반 {bad} · 보장 실패 {fail} · 평균 보장 칸 {cells/combos:.2f}")
    check(combos == 96900, f"조합 수 {combos}(20 × C(19,3) × N 22~26)")
    check(bad == 0, f"불변조건·자리순서·보장 위반 {bad} = 0")
    check(fail == 0, f"N 22~26 보장 실패 {fail} = 0")
    # 우선순위·기증 칸: 4-4-2 + (4-2-3-1, 3-5-2), N=23 → CDM·CAM 보장, CB(5→3)에서 기증
    t, seq, failed = SP.plan_targets_full("4-4-2", ("4-2-3-1", "3-5-2"), 23)
    base = ST.position_targets("4-4-2", 23)
    check(t["CDM"] == 1 and t["CAM"] == 1 and t["CB"] == base["CB"] - 2 and not failed,
          f"보장 CDM·CAM, 기증 CB: {t}")
    # 보장 실패: 기증할 곳이 없으면(N이 작음) 실패로 남기고 기록
    SP.PLAN_STATS.update(calls=0, fail_calls=0, fail_cells=0, samples=[])
    t13 = SP.plan_targets("4-4-2", ("4-2-3-1",), 13)
    _t, _s, f13 = SP.plan_targets_full("4-4-2", ("4-2-3-1",), 13)
    check(len(f13) > 0 and sum(t13.values()) == 13, f"N=13 보장 실패 {f13}")
    check(SP.PLAN_STATS["fail_calls"] == 1 and SP.PLAN_STATS["fail_cells"] == len(f13)
          and SP.PLAN_STATS["samples"][0][3] == f13, f"보장 실패 기록: {SP.PLAN_STATS}")
    # 대안 없음 / 그냥 문자열 = 기존 목표표(OFF 경로 동일)
    for f in forms:
        for n in (11, 18, 23, 26, 28):
            check(SP.targets_for(f, n) == ST.position_targets(f, n), f"문자열 {f} {n} = 기존 표")
            check(SP.targets_for(SP.PlanFormation(f, ()), n) == ST.position_targets(f, n), f"대안 없음 {f} {n} = 기존 표")
    pf = SP.PlanFormation("4-4-2", ("4-2-3-1", "3-5-2", None))
    check(pf == "4-4-2" and str(pf) == "4-4-2" and pf.alts == ("4-2-3-1", "3-5-2") and hash(pf) == hash("4-4-2"),
          "PlanFormation은 문자열로는 주 선호")
    # 역할 캐시(판매 가중 포함)가 같은 표를 쓴다
    rc = SP.RoleCache({1: []}, {1: (pf, 23)})
    check(rc.tab[1][2] == SP.plan_targets("4-4-2", pf.alts, 23), "역할 캐시 칸 목표 = 선수단 목표표")
    s_, sr_, t_ = rc.tab[1]
    check(all(s_[c] <= sr_[c] <= t_[c] for c in ST.TARGET_POSITIONS), "주전 ≤ 주전+로테 ≤ 목표(보장 표)")
    # 보충 신인 포지션 추첨이 같은 표를 쓴다: 부족이 보장 칸(CAM)에만 있으면 CAM
    have = collections.Counter({k: v for k, v in t.items() if k != "CAM"})
    picks = collections.Counter(ST.choose_target_position(pf, 23, have, random.Random(i), 0.0,
                                                          tgt=SP.targets_for(pf, 23), cap=SP.targets_for(pf, 28))
                                for i in range(50))
    check(picks == collections.Counter({"CAM": 50}), f"보충: 보장 칸 부족이면 그 칸 {picks}")
    picks0 = collections.Counter(ST.choose_target_position("4-4-2", 23, have, random.Random(i), 0.0) for i in range(50))
    check("CAM" not in picks0, "표를 안 넘기면(OFF) 예전 표 그대로")
    # 월드 생성 후보도 같은 표(자리 순서)
    import database as D
    D._PHIL_GEN.clear()
    D._PHIL_GEN.update(on=True, seed=4242, aff=set())
    starters = ["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
    bench = D._phil_bench_positions(starters, {"tid": 3, "cname": "잉글랜드", "tier": 2}, 25)
    prof = MP.team_seed_profile(4242, 3)
    log = D._PHIL_GEN["plan_log"][-1]
    check(log[:4] == (3, prof["pref_main"], tuple(prof["pref_alts"]), 25), f"월드 생성 표 입력 기록 {log}")
    tt = SP.plan_targets(prof["pref_main"], prof["pref_alts"], 25)
    got = ST.held_counter(starters + bench)
    over = sum(max(0, ST.held_counter(starters)[c] - tt[c]) for c in ST.TARGET_POSITIONS)
    short = sum(max(0, tt[c] - got[c]) for c in ST.TARGET_POSITIONS)
    check(short == over, f"월드 생성: 표 대비 부족 {short} = 고정 선발 목록의 표 밖 초과 {over}")
    # 여러 팀·시드: 표 대비 부족 = 고정 선발 목록의 표 밖 초과(그만큼만 못 채움), 인원 = 목표
    worst = 0
    for sd in (1, 77, 4242):
        D._PHIL_GEN.update(on=True, seed=sd, aff=set())
        for tid in range(1, 301):
            bench = D._phil_bench_positions(starters, {"tid": tid, "cname": "잉글랜드", "tier": 1}, 26)
            pr_ = MP.team_seed_profile(sd, tid)
            tt = SP.plan_targets(pr_["pref_main"], pr_["pref_alts"], 26)
            got = ST.held_counter(starters + bench)
            over = sum(max(0, ST.held_counter(starters)[c] - tt[c]) for c in ST.TARGET_POSITIONS)
            short = sum(max(0, tt[c] - got[c]) for c in ST.TARGET_POSITIONS)
            worst = max(worst, abs(short - over))
            check(len(starters) + len(bench) == 26, "월드 생성 인원 = 목표")
    check(worst == 0, f"월드 생성 900팀: |부족 − 표 밖 초과| 최대 {worst} = 0")
    D._PHIL_GEN.clear()


def t_r3_paths():
    print("[10] B 인원 가중 — 이적시장·상위 발탁이 같은 함수(설계 6-2), B 스위치로만")
    import inspect
    import ai_lifecycle as AL
    src_tm = inspect.getsource(AL._transfer_market)
    src_up = inspect.getsource(AL._upward_transfer_pull)
    check("size_weight_t(" in src_tm and "size_weight_t(" in src_up, "두 경로 모두 squad_plan.size_weight_t")
    check("if _size_on():" in src_tm and "if _size_on():" in src_up, "두 경로 모두 B 스위치로 켜짐")


def _r4_roster(spec):
    """spec: [(등록 포지션, OVR, 부포)] → (roster, sub_of)"""
    roster, sub_of = [], {}
    for i, (p, o, sb) in enumerate(spec):
        roster.append((i + 1, p, o))
        if sb:
            sub_of[i + 1] = sb
    return roster, sub_of


def t_r4_swap():
    print("[11] R4 — 맞바꾸기: 같은 목표표 · 최저 OVR · 선발 공백 방지 · 전부 막히면 기록")
    import ai_lifecycle as AL
    pf = SP.PlanFormation("4-4-2", ("4-2-3-1",))
    t = SP.targets_for(pf, 23)
    spec = []
    for c in ST.TARGET_POSITIONS:
        if c == "CAM":
            continue
        reg = "ST" if c == "FW" else c
        spec += [(reg, 60 + k, None) for k in range(t[c])]
    spec += [("ST", 50, None), ("ST", 55, None)]          # FW 초과 2(+ CAM 몫)
    roster, sub_of = _r4_roster(spec)
    AL.PHIL_R4_STATS.clear()
    sw = AL._phil_r4_plan_swaps(roster, pf, "4-4-2", 23, sub_of)
    low = min((r for r in roster if r[1] == "ST"), key=lambda r: r[2])
    check(sw == [(low[0], "CAM")], f"보장 칸 CAM 0명 → FW 최저 OVR과 맞바꿈: {sw}")
    check(AL._phil_r4_plan_swaps(roster, "4-4-2", "4-4-2", 23, sub_of) == [],
          "대안 없는 표(CAM 목표 0)면 맞바꾸지 않음")
    # 공백 방지: 3-5-2 표(LW 목표 0) · 현재 4-2-3-1. LW 5명 중 최저 2명은 현재 포메이션 CAM/RW의 유일한 부포 보유자
    base = [("GK", 60, None)] * 2 + [("CB", 60, None)] * 6 + [("LB", 60, None)] * 2 + [("RB", 60, None)] * 2 \
        + [("CM", 60, None)] * 5 + [("ST", 60, None)] * 4
    lw5 = [("LW", 50, "CAM"), ("LW", 55, "RW"), ("LW", 60, None), ("LW", 65, None), ("LW", 70, None)]
    roster, sub_of = _r4_roster(base + lw5)
    ids = {r[2]: r[0] for r in roster if r[1] == "LW"}
    AL.PHIL_R4_STATS.clear()
    sw = AL._phil_r4_plan_swaps(roster, SP.PlanFormation("3-5-2", ()), "4-2-3-1", 23, sub_of)
    check(sw == [(ids[60], "CDM")], f"CAM·RW 공백 만드는 후보 건너뜀 → OVR 60 LW: {sw}")
    check(AL.PHIL_R4_STATS.get("skipped_cand") == 2 and AL.PHIL_R4_STATS.get("swaps") == 1,
          f"건너뜀 2 · 맞바꿈 1: {AL.PHIL_R4_STATS}")
    # 전부 막힘: LW 3명뿐(각자 CAM/RW/LW 유일)
    roster, sub_of = _r4_roster(base + [("LW", 50, "CAM"), ("LW", 55, "RW"), ("LW", 60, None)])
    AL.PHIL_R4_STATS.clear()
    sw = AL._phil_r4_plan_swaps(roster, SP.PlanFormation("3-5-2", ()), "4-2-3-1", 23, sub_of)
    check(sw == [] and AL.PHIL_R4_STATS.get("blocked") == 1, f"전부 공백 → 안 바꾸고 기록: {sw} {AL.PHIL_R4_STATS}")
    # 과잉 2 미만이면 안 바꿈
    roster, sub_of = _r4_roster(base + [("LW", 60, None)])
    AL.PHIL_R4_STATS.clear()
    sw = AL._phil_r4_plan_swaps(roster, SP.PlanFormation("3-5-2", ()), "3-5-2", 23, sub_of)
    check(sw == [] and AL.PHIL_R4_STATS.get("no_surplus") == 1, f"과잉 칸 없음 → 안 바꾸고 기록 {AL.PHIL_R4_STATS}")
    # 부족 판정은 held_assign(부포 포함): CDM 부포 보유자가 있으면 CDM은 부족이 아니다
    roster, sub_of = _r4_roster(base + [("CM", 60, "CDM")] + lw5)    # CM 6명(목표 5) 중 1명 부포 CDM
    AL.PHIL_R4_STATS.clear()
    sw = AL._phil_r4_plan_swaps(roster, SP.PlanFormation("3-5-2", ()), "3-5-2", 23, sub_of)
    t352 = SP.targets_for("3-5-2", 23)
    h = ST.held_assign([(r[1], sub_of.get(r[0])) for r in roster], t352)
    check(h["CDM"] == 1 and all(c != "CDM" for _p, c in sw), f"부포 CDM 보유자 → CDM 부족 아님: {sw}")


def t_retry2():
    print("[12] #1 미드필드 장악형 수비 라인 표(A) · 재시도 2 보정 5개가 제거됐는지")
    import itertools, inspect
    import ai_lifecycle as AL
    check(C.MANAGER_PHILOSOPHY_PRS["MIDFIELD_CONTROL"] == (6, 82, 12), "미드필드 장악형 수비 라인 (6, 82, 12)")
    A, B, R = (C.MANAGER_PHILOSOPHY_ATK["MIDFIELD_CONTROL"], C.MANAGER_PHILOSOPHY_BLD["MIDFIELD_CONTROL"],
               C.MANAGER_PHILOSOPHY_PRS["MIDFIELD_CONTROL"])
    p = sum(A[a] / sum(A) * B[b] / sum(B) * R[r] / sum(R) for a, b, r in itertools.product(range(5), range(3), range(3))
            if (a >= 3 and r == 0) or (a <= 1 and r == 2) or (a == 4 and b == 0))
    check(abs(p * 100 - 5.75) < 0.01, f"미드필드 장악형 부자연 기대값 {p*100:.2f}% = 5.75%(종전 6.97%)")
    n_mc = un_mc = 0
    for i in range(40000):
        pr = MP.draw_profile(f"mgr:r2:{i}")
        if pr["philosophy"] == "MIDFIELD_CONTROL":
            n_mc += 1
            a = MP.ATK.index(pr["style_attack"]); b = MP.BLD.index(pr["style_buildup"]); r = MP.PRS.index(pr["style_press"])
            un_mc += (a >= 3 and r == 0) or (a <= 1 and r == 2) or (a == 4 and b == 0)
    print(f"  미드필드 장악형 표본 {n_mc}명 부자연 {un_mc/n_mc*100:.2f}%")
    check(un_mc / n_mc <= 0.07, "표본 부자연 ≤ 7%")
    # 재시도 2 보정(목표 미달 팀 우선 3경로·목표 초과 팀 매각·R3 0.02)은 원래 설계 기준으로 제거됨 — 남아 있지 않은지 확인
    import squad_plan as _spx
    check(not hasattr(_spx, "prefer_under") and not hasattr(AL, "_phil_surplus_sale")
          and not hasattr(AL, "_phil_surplus_pick") and not hasattr(C, "SIZE_W_AT_TARGET")
          and not hasattr(C, "SURPLUS_SALE_GAP_CUTOFF"), "보정 5개 제거")
    for fn in (AL._do_one_transfer_cached, AL._upward_transfer_pull, AL._quota_sale_cleanup, AL.run_ai_offseason):
        src = inspect.getsource(fn)
        check("prefer_under" not in src and "_pu_c" not in src and "under_only" not in src
              and "_phil_surplus" not in src, f"{fn.__name__}: 보정 코드 없음")


def t_ab_separation():
    print("[13] A·B 분리 — B를 끄면 인원 경로 전부 기존 값, A를 끄면 철학 표 안 만듦")
    import ai_lifecycle as AL
    import database as D
    oldA, oldB = C.MANAGER_PHILOSOPHY_ENABLED, C.SQUAD_SIZE_RULES_ENABLED
    grades = ("SS", "S", "A", "B", "C", "D", "E", "F")
    try:
        C.MANAGER_PHILOSOPHY_ENABLED, C.SQUAD_SIZE_RULES_ENABLED = False, False
        base = {(g, t, y): (AL._squad_min_max(g, t, y), AL._is_fixed26(g, t), AL._plan_n(g, t, y))
                for g in grades for t in (1, 2, 3, 4, 6) for y in (False, True)}
        base_sw = [AL._size_weight(n) for n in range(18, 30)]
        C.MANAGER_PHILOSOPHY_ENABLED, C.SQUAD_SIZE_RULES_ENABLED = True, False
        a_only = {(g, t, y): (AL._squad_min_max(g, t, y), AL._is_fixed26(g, t), AL._plan_n(g, t, y))
                  for g in grades for t in (1, 2, 3, 4, 6) for y in (False, True)}
        check(a_only == base, "A만 켬: _squad_min_max·_is_fixed26·N(보충선)이 전부 기존 값")
        check([AL._size_weight(n) for n in range(18, 30)] == base_sw, "A만 켬: 기존 인원 가중 함수 그대로")
        check(not AL._size_on() and AL._phil_on(), "A만 켬: 스위치 상태")
        check(sorted({v[2] for v in a_only.values()}) == [22, 23, 24, 25, 26], f"A만 켬: N 범위 22~26")
        # 월드 생성: B 끔 → 후보 인원은 기존 규칙(같은 난수 소비), A 켬 → 포지션만 목표표
        D._PHIL_GEN.clear(); D._PHIL_GEN.update(on=True, seed=4242, aff=set())
        team = {"tid": 11, "cname": "잉글랜드", "tier": 1}
        random.seed(99); a = D._build_squad_positions("S", 1, team); ra = random.random()
        D._PHIL_GEN.clear()
        random.seed(99); o = D._build_squad_positions("S", 1, team)
        check(len(a) == len(o) == 26, f"A만 켬: 26 고정 팀 인원 기존 그대로 {len(a)} / {len(o)}")
        D._PHIL_GEN.update(on=True, seed=4242, aff=set())
        random.seed(5); a2 = D._build_squad_positions("C", 3, {"tid": 12, "cname": "잉글랜드", "tier": 3}); ra2 = random.random()
        D._PHIL_GEN.clear()
        random.seed(5); o2 = D._build_squad_positions("C", 3, {"tid": 12, "cname": "잉글랜드", "tier": 3})
        random.seed(5); _n_exp = 11 + random.randint(11, 14)
        check(len(a2) == _n_exp == len(o2), f"A만 켬: 일반 팀 후보 인원 = 기존 randint(11,14) {len(a2)} / {_n_exp}")
        C.MANAGER_PHILOSOPHY_ENABLED, C.SQUAD_SIZE_RULES_ENABLED = False, True
        check(AL._squad_min_max("S", 1) == (26, 26) and AL._squad_min_max("B", 1) == (25, 26)
              and AL._squad_min_max("D", 6, True) == (26, 26) and AL._is_fixed26("A", 1) and not AL._is_fixed26("S", 2),
              "B만 켬: C안 목표·오퍼 빈자리 대상")
        check(not MP.on(), "B만 켬: 철학 스위치 꺼짐(manager_philosophy 함수가 DB를 건드리지 않음)")
        import sqlite3 as _sq
        _c = _sq.connect(":memory:")
        _c.execute("CREATE TABLE managers(id INTEGER PRIMARY KEY)")
        check(MP.assign_missing(_c) == 0 and not _c.execute(
            "SELECT name FROM sqlite_master WHERE name='manager_philosophy'").fetchall(), "B만 켬: 철학 표 생성 안 함")
        D._PHIL_GEN.clear(); D._PHIL_GEN.update(size=True, aff=set())
        b_only = D._build_squad_positions("B", 1, {"tid": 13, "cname": "대한민국", "tier": 1})
        check(len(b_only) == 25 and "GK" in b_only[11:], f"B만 켬: 후보 인원 = 목표 − 11, 포지션은 기존 추첨 {len(b_only)}")
    finally:
        C.MANAGER_PHILOSOPHY_ENABLED, C.SQUAD_SIZE_RULES_ENABLED = oldA, oldB
        D._PHIL_GEN.clear()


if __name__ == "__main__":
    for fn in (t_profile_distribution, t_targets_and_size_weight, t_role_tables_all_formations, t_role_judgement,
               t_world_gen_bench, t_formation_choice, t_db_plan_and_off,
               t_r1_formation, t_r2_plan_targets, t_r3_paths, t_r4_swap, t_retry2, t_ab_separation):
        fn()
    print("\n결과:", "전부 통과" if not FAILS else f"실패 {len(FAILS)}건")
    sys.exit(1 if FAILS else 0)