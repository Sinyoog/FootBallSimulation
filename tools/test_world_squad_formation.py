"""[2026-10-30] 월드 생성 선수단 포메이션 기준 구성(constants.WORLD_SQUAD_BY_FORMATION) 단위 테스트.

사용: python tools/test_world_squad_formation.py   (DB 파일을 건드리지 않는다 — 메모리 DB만 쓴다)
"""
import _path  # noqa: F401
import collections
import importlib.util
import os
import random
import sqlite3
import sys

import constants as C
import database as DB
import squad_targets as ST

FAILS = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


FORMS = list(C.FORMATION_SLOTS)
REG = {"LM": "LW", "RM": "RW", "LWB": "LB", "RWB": "RB"}

# ── [1] 목표표 GK 3명 ────────────────────────────────────────────────
print("[1] 목표표(GK 3명) — 20개 포메이션 × 22~28명")
C.set_world_squad_by_formation(True)
check("스위치 ON이면 가용 GK 최소 3", C.SQUAD_TARGET_MIN_AVAILABLE_GK == 3)
bad = []
for f in FORMS:
    for n in range(22, 29):
        t = ST.position_targets(f, n)
        if sum(t.values()) != n or t["GK"] != 3:
            bad.append((f, n, "합계/GK"))
        s = ST.starter_counts(f)
        if any(t[p] < s[p] for p in ST.TARGET_POSITIONS):
            bad.append((f, n, "선발 미만"))
check("합계 N · GK 3 · 선발 이상", not bad, str(bad[:5]))
C.set_world_squad_by_formation(False)
old = {(f, n): ST.position_targets(f, n) for f in FORMS for n in range(11, 29)}
C.set_world_squad_by_formation(True)
diff = [(f, n) for f in FORMS for n in range(22, 27)
        if ST.position_targets(f, n) != dict(old[(f, n - 1)], GK=old[(f, n - 1)]["GK"] + 1)]
check("22~26명: (N−1)명 기존 표 + GK 1명과 같다(설계안 공식)", not diff, str(diff[:5]))
ex = ST.position_targets("5-4-1", 26)
check("설계안 예시 5-4-1 26명 = GK3 CB6 LB3 RB2 CDM0 CM5 CAM0 LW2 RW2 FW3",
      [ex[p] for p in ST.TARGET_POSITIONS] == [3, 6, 3, 2, 0, 5, 0, 2, 2, 3], str(ex))
mx = ST.max_targets("5-4-1", 26)
check("상한 5-4-1 26명 = GK3 CB7 LB4 RB3 CDM1 CM6 CAM1 LW3 RW3 FW4",
      [mx[p] for p in ST.TARGET_POSITIONS] == [3, 7, 4, 3, 1, 6, 1, 3, 3, 4], str(mx))

# ── [2] OFF = 원본 목표표·추첨 ───────────────────────────────────────
print("[2] 스위치 OFF — 원본 squad_targets와 표·추첨 동일")
_orig_path = os.environ.get("ORIG_SQUAD_TARGETS")
if _orig_path and os.path.exists(_orig_path):
    spec = importlib.util.spec_from_file_location("squad_targets_orig", _orig_path)
    O = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(O)
    C.set_world_squad_by_formation(False)
    same = all(ST.position_targets(f, n) == O.position_targets(f, n) for f in FORMS for n in range(11, 29))
    check("OFF 목표표 = 원본(20 × 11~28)", same)
    mism = 0
    for i in range(3000):
        f = FORMS[i % 20]
        n = 22 + i % 7
        held = collections.Counter({p: random.Random(i * 7 + k).randint(0, 4)
                                    for k, p in enumerate(ST.TARGET_POSITIONS)})
        a = ST.choose_target_position(f, n, held, random.Random(i), 0.15)
        b = O.choose_target_position(f, n, held, random.Random(i), 0.15)
        r1, r2 = random.Random(i), random.Random(i)
        ST.choose_target_position(f, n, held, r1, 0.15)
        O.choose_target_position(f, n, held, r2, 0.15)
        if a != b or r1.random() != r2.random():
            mism += 1
    check("OFF choose_target_position 결과·난수 소비 = 원본(3000건)", mism == 0, f"불일치 {mism}")
else:
    check("원본 squad_targets 경로(ORIG_SQUAD_TARGETS) 지정", False, "환경변수 없음")
C.set_world_squad_by_formation(True)

# ── [3] 월드 생성 포지션 목록 ────────────────────────────────────────
print("[3] _build_squad_positions_by_formation — 20개 포메이션 × 22~26명")
viol = collections.Counter()
ncover = collections.defaultdict(set)
bench_idx = collections.defaultdict(list)
fw_bench = collections.Counter()
gk_only_ok = True
for f in FORMS:
    slots = [REG.get(s, s) for s in C.FORMATION_SLOTS[f]]
    for k in range(400):
        random.seed(f"{f}:{k}")
        grade, tier = ("A", 1) if k % 5 == 0 else ("C", 3)
        pos = DB._build_squad_positions_by_formation(grade, tier, f)
        n = len(pos)
        ncover[f].add(n)
        if pos[:11] != slots:
            viol["선발≠포메이션"] += 1
        held = ST.held_counter(pos)
        _alts = DB._gen_alt_formations(f, n, None)
        t, m = ST.team_targets(f, n, _alts), ST.team_max(f, n, _alts)
        if held["GK"] != 3:
            viol["GK≠3"] += 1
        if any(held[p] > m[p] for p in ST.TARGET_POSITIONS):
            viol["최대 초과"] += 1
        if any(held[p] < ST.starter_counts(f)[p] for p in ST.TARGET_POSITIONS):
            viol["선발 칸 부족"] += 1
        if not (22 <= n <= 26) or (grade == "A" and n != 26):
            viol["총원"] += 1
        if any(held[p] > 0 for p in ST.TARGET_POSITIONS if t[p] == 0):
            viol["목표0 칸 생성"] += 1
        for j, p in enumerate(pos[11:]):
            bench_idx[ST.REGISTERED_TO_TARGET[p]].append(j / (n - 12))
            if p in ("ST", "CF"):
                fw_bench[p] += 1
check("선발 11 = 포메이션 슬롯(등록 포지션) · GK 3 · 최대 초과 0 · 선발 칸 공백 0 · 총원",
      not viol, dict(viol))
check("모든 포메이션에서 22~26명이 다 나옴", all(ncover[f] == {22, 23, 24, 25, 26} for f in FORMS))
check("목표(대비 인원 포함) 0인 포지션은 0명", viol["목표0 칸 생성"] == 0)
means = {p: sum(v) / len(v) for p, v in bench_idx.items() if len(v) > 200}
check("후보 순번이 포지션과 무관(포지션별 평균 상대 순번 0.5±0.03)",
      all(abs(v - 0.5) < 0.03 for v in means.values()), {p: round(v, 3) for p, v in means.items()})
cf = fw_bench["CF"] / max(1, fw_bench["CF"] + fw_bench["ST"])
check("후보 FW 중 CF 25%±2%p", abs(cf - 0.25) < 0.02, f"{cf:.3f}")

# ── [4] 월드클래스 보정 = 확정표 ─────────────────────────────────────
print("[4] 월드클래스 포지션 보정 — 세계 기대 비율 = STAR_POSITION_WEIGHT 확정표")
pf = collections.Counter()
tw = sum(C.TACTIC_TENDENCY_WEIGHTS)
for tend, wt in zip(C.TACTIC_TENDENCIES, C.TACTIC_TENDENCY_WEIGHTS):
    lean = C.TACTIC_TENDENCY_LEAN[tend]
    ws = [max(0.05, 1.5 - abs(lean - C.FORMATION_STYLE[x])) for x in FORMS]
    for f, w in zip(FORMS, ws):
        pf[f] += wt / tw * w / sum(ws)
P = ["GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
tab = {p: C.star_position_weight(p) for p in P}
T = sum(tab.values())
agg_cal, agg_raw = collections.Counter(), collections.Counter()
for f, pr in pf.items():
    slots = [REG.get(s, s) for s in C.FORMATION_SLOTS[f]]
    for agg, fn in ((agg_cal, DB.star_slot_weights_calibrated), (agg_raw, DB.star_slot_weights)):
        w = fn(slots)
        s = sum(w)
        for p, x in zip(slots, w):
            agg[p] += pr * x / s
worst = max(abs(agg_cal[p] - tab[p] / T) for p in P) * 100
check("보정 후 기대 비율 = 확정표(오차 0.01%p 이내)", worst < 0.01,
      " ".join(f"{p}{agg_cal[p] * 100:.2f}" for p in P))
print("      보정 전:", " ".join(f"{p}{agg_raw[p] * 100:.1f}" for p in P))
print("      보정 계수:", {p: round(v, 3) for p, v in DB._star_formation_calibration().items()})
# 실제 추첨 함수로 몬테카를로 교차 확인(팀당 1명)
rng_state = random.getstate()
random.seed(7)
mc = collections.Counter()
NMC = 300000
for _ in range(NMC):
    f = random.choices(list(pf), weights=list(pf.values()))[0]
    slots = [REG.get(s, s) for s in C.FORMATION_SLOTS[f]]
    i = DB._weighted_sample_no_replacement(range(11), DB.star_slot_weights_calibrated(slots), 1)[0]
    mc[slots[i]] += 1
random.setstate(rng_state)
worst_mc = max(abs(mc[p] / NMC - tab[p] / T) for p in P) * 100
check("실제 추첨 함수 몬테카를로(30만) = 확정표(±0.3%p)", worst_mc < 0.3, f"최대 차 {worst_mc:.2f}%p")

# ── [5] 시드 포메이션 읽기 ───────────────────────────────────────────
print("[5] _seed_formation_map — team_formation_seed 우선")
m = sqlite3.connect(":memory:")
m.execute("CREATE TABLE teams(id INTEGER, formation TEXT)")
m.execute("CREATE TABLE team_formation_seed(team_id INTEGER, formation TEXT)")
m.executemany("INSERT INTO teams VALUES(?,?)", [(1, "4-4-2"), (2, "3-5-2"), (3, "4-3-3")])
m.executemany("INSERT INTO team_formation_seed VALUES(?,?)", [(1, "5-4-1"), (2, "4-2-3-1")])
check("시드 있으면 시드, 없으면 teams.formation",
      DB._seed_formation_map(m) == {1: "5-4-1", 2: "4-2-3-1", 3: "4-3-3"}, str(DB._seed_formation_map(m)))

# ── [6] 스위치 OFF 경로 ──────────────────────────────────────────────
print("[6] 스위치 OFF")
C.set_world_squad_by_formation(False)
check("OFF면 가용 GK 최소 2", C.SQUAD_TARGET_MIN_AVAILABLE_GK == 2)
check("OFF면 생성 판정 False", not DB._world_squad_by_formation_on())
C.set_world_squad_by_formation(True)

# ── [7] 2차: 대체 포메이션 대비 인원 ────────────────────────────────
print("[7] 2차 — 대체 포메이션 대비 인원(상위 8) · 목표표")
import formation_logic as FL
C.set_world_squad_by_formation(True)
bad = []
for f in FORMS:
    for tend in C.TACTIC_TENDENCIES:
        for n in range(22, 29):
            alts = DB._gen_alt_formations(f, min(n, 26), tend)
            t = ST.team_targets(f, n, alts)
            s = ST.starter_counts(f)
            if sum(t.values()) != n or t["GK"] != 3 or any(t[p] < s[p] for p in ST.TARGET_POSITIONS):
                bad.append((f, tend, n, "합계/GK/선발"))
            if any(t[p] < 1 for p in ST.mandatory_cover(f, alts)):
                bad.append((f, tend, n, "필수 대비 0"))
            if len(alts) != 8 or f in alts:
                bad.append((f, tend, n, "대체 8개"))
check("대비 목표표: 합계 N · GK 3 · 선발 이상 · 필수 대비 ≥1 · 대체 8개(현재 제외)", not bad, str(bad[:5]))
check("대체 없음이면 GK3 기본 목표표와 같다",
      all(ST.team_targets(f, n, ()) == ST.position_targets(f, n) for f in FORMS for n in range(22, 27)))
viol2 = collections.Counter()
form_ok = form_all = 0
for f in FORMS:
    for tend in C.TACTIC_TENDENCIES:
        for k in range(40):
            random.seed(f"{f}:{tend}:{k}")
            grade, tier = ("A", 1) if k % 5 == 0 else ("C", 3)
            pos = DB._build_squad_positions_by_formation(grade, tier, f, tend)
            n = len(pos)
            alts = DB._gen_alt_formations(f, n, tend)
            held = ST.held_counter(pos)
            mx = ST.team_max(f, n, alts)
            if held["GK"] != 3: viol2["GK≠3"] += 1
            if any(held[p] > mx[p] for p in ST.TARGET_POSITIONS): viol2["최대 초과"] += 1
            if any(held[p] < ST.starter_counts(f)[p] for p in ST.TARGET_POSITIONS): viol2["선발 칸 부족"] += 1
            if any(held[p] == 0 for p in ST.mandatory_cover(f, alts)): viol2["필수 대비 0"] += 1
            ent = [(p, None) for p in pos]
            if not ST.formable(ent, f): viol2["주 포메이션 선발 불가"] += 1
            for a in alts:
                form_all += 1; form_ok += ST.formable(ent, a)
check("월드 생성(2차): GK 3 · 최대 초과 0 · 선발 칸 부족 0 · 필수 대비 ≥1 · 주 포메이션 선발 가능", not viol2, dict(viol2))
print(f"      [관찰] 대체 포메이션 실제 선발 구성 가능 비율 {form_ok / form_all * 100:.1f}% ({form_ok}/{form_all})")

print("[8] formable — 중복 없는 실제 선발 구성")
e442 = [("GK", None), ("CB", None), ("CB", None), ("LB", None), ("RB", None), ("LW", None), ("CM", None),
        ("CM", None), ("RW", None), ("ST", None), ("CF", None)]
check("4-4-2 정확히 11명 → 가능", ST.formable(e442, "4-4-2"))
check("4-2-3-1(CDM 2·CAM 1)은 CM으로 못 채움 → 불가", not ST.formable(e442, "4-2-3-1"))
e2 = e442[:6] + [("CM", "CDM"), ("CM", "CDM"), ("RW", None), ("ST", "CAM"), ("CF", None)]
check("부포(CM→CDM 2, ST→CAM)로 4-2-3-1 가능", ST.formable(e2, "4-2-3-1"))
e3 = e442[:6] + [("CM", "CDM"), ("CM", None), ("RW", None), ("ST", None), ("CF", None)]
check("한 선수를 두 칸에 중복 배치하지 않음(CDM 부포 1명뿐 → 4-2-3-1 불가)", not ST.formable(e3, "4-2-3-1"))
check("LWB 칸은 LB가 채움(5-4-1 LB·RB 대신 LB·RB 등록)",
      ST.formable([("GK", None)] + [("CB", None)] * 3 + [("LB", None), ("RB", None), ("LW", None), ("CM", None),
                                                          ("CM", None), ("RW", None), ("ST", None)], "5-4-1"))

print("[9] choose_formation_prepped — allowed")
class _R:
    def __init__(s, seed): s.r = random.Random(seed)
    def random(s): return s.r.random()
    def choices(s, pop, w): return s.r.choices(pop, w)
ro = [{"position": p, "ovr": 60 + i % 9} for i, p in enumerate([x for x, _ in e2] + ["GK", "GK", "CB", "CM"])]
pr = FL.prep_roster(ro)
cur = FL.choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=_R(1), allowed=set())
check("후보가 하나도 없으면 현재 포메이션 유지", cur[0] == "4-4-2")
okset = {"4-2-3-1", "4-4-2"}
picks = {FL.choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=_R(i), allowed=okset)[0] for i in range(200)}
check("허용 집합 밖은 고르지 않음", picks <= okset, str(picks))
_orig_fl_path = os.environ.get("ORIG_FORMATION_LOGIC")
if _orig_fl_path and os.path.exists(_orig_fl_path):
    spec = importlib.util.spec_from_file_location("formation_logic_orig", _orig_fl_path)
    OF = importlib.util.module_from_spec(spec); spec.loader.exec_module(OF)
    same = True
    for i in range(300):
        tend = C.TACTIC_TENDENCIES[i % 5]
        a = FL.choose_formation_prepped(pr, FORMS[i % 20], tend, rng=_R(i), family=("back3" if i % 3 else None), family_bonus=0.1)
        b = OF.choose_formation_prepped(pr, FORMS[i % 20], tend, rng=_R(i), family=("back3" if i % 3 else None), family_bonus=0.1)
        same &= (a == b)
    check("allowed 없으면 원본 choose_formation_prepped와 결과 같음(300건)", same)
else:
    check("원본 formation_logic 경로(ORIG_FORMATION_LOGIC) 지정", False)
class _Half:
    def random(s): return 0.5
    def choices(s, pop, w): _Half.top = [t[1] for t in pop]; return [pop[0]]
FL.choose_formation_prepped(pr, "4-4-2", "BALANCED", rng=_Half(), family="back4", family_bonus=0.1)
alt = FL.alt_formations(pr, "4-4-2", "BALANCED", family="back4", family_bonus=0.1)
check("대체 포메이션 순위 = 재검토 점수식(무작위 0.5)의 상위 순위(현재 제외)",
      [x for x in _Half.top if x != "4-4-2"] == list(alt[:len([x for x in _Half.top if x != "4-4-2"])]),
      f"{_Half.top} / {alt}")

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
sys.exit(1 if FAILS else 0)