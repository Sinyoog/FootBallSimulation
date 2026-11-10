# -*- coding: utf-8 -*-
"""[2026-11] 발롱도르 채점 모듈(ballon_award.py) 단위 테스트 — DB 없음. 수상 로직에는 아직 연결 전.

A. 계산식 — 배점 합 10, 능력 1.75·생산성 5.25, 기대치(엔진과 같은 EST_POS), 포지션별 손 계산(ST·CDM·CB·GK)
B. 소속팀 성적 — 리그 0.4 / 컵 0.1 / 대륙대회 0.5 → 최대 0.60 / 0.15 / 0.75점, 팀 안 개인 점수 순위 배분(동점 같은 순위)
C. [확인 1] 챔스 우승팀이라는 이유만으로 개인 점수가 낮은 선수가 1위가 되지 않음
D. [확인 2] 같은 팀 안에서 개인 점수 1순위가 아닌 선수는 배분 때문에 그 1순위를 역전하지 못함
E. [확인 3] FW가 골만으로 독점하지 않음 — 좋은 시즌의 CM·CDM·CB가 평범한 ST를 이길 수 있음
F. [확인 4] 내 선수와 AI 선수 — 같은 기록이면 모든 항목 점수 완전 일치, 시즌 안 순위도 같음(선수 번호만 바꿔 넣기)
G. [확인 5] GK·DF 팀 수비 기록의 최대 영향 = 개인 7.0 × 0.75 × 0.3(DF 1.575점, GK 0.7875점), 개인 기록이 좋은
   약팀 DF가 개인 기록이 나쁜 강팀 DF를 이김
H. 결정론 — 입력 순서를 섞어도 같은 결과 / 빈 값 처리
I. [2차] C안 완만한 상한 — p99.5 = 90%, 그 위도 계속 오르되 1을 넘지 않음, 45·50·70골이 구분됨
J. [2차·3차] 공식 대회 전부 — 국내컵 0.25 × 리그 수준, 대륙 대회 × 대회 가중치, 대륙 슈퍼컵 0.2 × 대륙 챔스 가중치,
   국가대표 공식 대회(월드컵 1.0 · 대륙 선수권 0.6 × 대륙 비율), 점수표에 없는 대회는 무시, 대회 득점 환경이 기대치에
   반영, 수준 낮은 대회 출전이 기대 대비를 깎지 않음, 리그 수준이 골·도움 가치에 반영
K. [2차] 리그 성적 = 순위 점수 × 리그 수준 × 우승 난이도(예상대로 0.8 → 이변 1.0), 국내컵 × 리그 수준
L. [3차·4차 확정] 리그 수준은 개인 생산성에 한 번(공격 생산량)·팀 성적에 한 번, 공격 생산량은 득점 계수^−0.5
   (리그 수준과 무관), 팀 공격력^0.5는 국내 기대치에만 — 실제 골·공격 생산량·팀 점수는 그대로
사용: python tools/test_ballon_score.py"""
import _path  # noqa: F401
import copy
import random
import sys

import ballon_award as B
from constants import EST_POS_GOAL, EST_POS_ASSIST

FAILS = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def close(a, b, eps=1e-9):
    return abs(a - b) <= eps


def P(pid, pos, g, a, m=38, ovr=92, team=1, **kw):
    """기본 후보: 38경기 리그 풀시즌, 득점 환경 1.0, 리그 수준 1.0, 다른 대회 없음, 팀 실점 1.5, 출전 100%,
    리그 성적 없음(순위 미입력)."""
    e = {"player_id": pid, "team_id": team, "position": pos, "goals": g, "assists": a, "matches": m,
         "full_season": 38, "goal_env": 1.0, "league_level": 1.0, "comps": [], "ovr": ovr, "team_gc": 1.5,
         "share": m / 38.0, "league_rank": None, "league_n": 20, "relegated": False, "league_exp": None,
         "cup_v": 0.0, "cont_v": 0.0, "intl_v": 0.0}
    e.update(kw)
    return e


def C(kind, g, a, m, level=1.0, env=1.0):
    return {"kind": kind, "goals": g, "assists": a, "matches": m, "level": level, "env": env}


REL_HI, VOL_LO, VOL_HI = B.REL_BAND[1], B.VOL_BAND[0], B.VOL_BAND[1]


def soft(u):
    """손 계산용 C안 척도(모듈과 별도로 다시 씀): u ≤ 1 → 0.9u, u > 1 → 0.9 + 0.1·(u−1)/(u−1+1)."""
    if u <= 0:
        return 0.0
    return 0.9 * u if u <= 1 else 0.9 + 0.1 * (u - 1) / u


# ── A ───────────────────────────────────────────────────────────────
print("A. 계산식")
check("배점 합 10.0 (개인 7.0 + 팀 1.5 + 국제 1.0 + 출전 0.5)", close(B.W_PERSONAL + B.W_TEAM + B.W_INTL + B.W_PART, 10.0))
check("능력 1.75 · 생산성 5.25", close(B.W_PERSONAL * B.OVR_SHARE, 1.75) and close(B.W_PERSONAL * (1 - B.OVR_SHARE), 5.25))
pm = B.league_per_match("CDM", 38, 1.0)
check("리그 경기당 기대 = 엔진 EST_POS(CDM 1.4+3.0=4.4) ÷ 38", close(pm * 38, EST_POS_GOAL["CDM"] + EST_POS_ASSIST["CDM"]),
      f"{pm * 38:.3f}")
check("득점 환경 1.1 · 30경기 시즌 → 23×1.1×(30/38)^0.35 ÷ 30", close(B.league_per_match("ST", 30, 1.1),
                                                                    23 * 1.1 * (30 / 38) ** 0.35 / 30))
# ST 25골 5도움 OVR 96: rel_rate = 40/33, vol_raw = 28.75
st_rel = soft((40 / 33 - 1) / (REL_HI - 1))
st_vol = soft((28.75 - VOL_LO) / (VOL_HI - VOL_LO))
st_pers = 1.75 * 9 / 12 + 5.25 * 0.5 * (st_rel + st_vol) + 0.5
pst = B.personal(P(1, "ST", 25, 5, ovr=96))
check("ST 25골 5도움 OVR 96 손 계산(기대 23, 기대 대비 1.2121)", close(pst["personal"], st_pers, 1e-9),
      f"{pst['personal']:.6f}")
# CDM 5골 10도움: rel_rate = 25/14.4 = 1.7361 (p99.5 위 → 0.9보다 조금 큼)
pcd = B.personal(P(2, "CDM", 5, 10))
cd_prod = 0.5 * (soft((25 / 14.4 - 1) / (REL_HI - 1)) + soft((12.5 - VOL_LO) / (VOL_HI - VOL_LO)))
check("CDM 5골 10도움 손 계산: 기대 대비가 p99.5 위(0.9 초과·1 미만)", close(pcd["prod"], cd_prod)
      and 0.9 < pcd["rel"] < 1.0, f"기대 대비 {pcd['rel']:.4f} · 생산성 {pcd['prod']:.6f}")
# CB 3골 2도움, 팀 실점 0.94: rel_rate 15/12.8, vol 4.5 → 혼합 × 0.7 + 0.3 × 1.0
pcb = B.personal(P(3, "CB", 3, 2, team_gc=0.94))
cb_prod = 0.7 * 0.5 * (soft((15 / 12.8 - 1) / (REL_HI - 1)) + soft((4.5 - VOL_LO) / (VOL_HI - VOL_LO))) + 0.3
check("CB 3골 2도움·팀 실점 0.94 손 계산", close(pcb["prod"], cb_prod), f"{pcb['prod']:.6f}")
# GK: 표준 환경 선방률 0.80 → u = 0.05/0.056, 팀 실점 1.2 → 0.8/1.06
pgk = B.personal(P(4, "GK", 0, 0, gk_save=0.80, team_gc=1.2))
gk_prod = 0.5 * (0.7 * soft(0.05 / 0.056) + 0.3 * (0.8 / 1.06))
check("GK 표준 환경 선방률 80%·팀 실점 1.2 손 계산(공격 생산량 몫 0)", close(pgk["prod"], gk_prod), f"{pgk['prod']:.6f}")

# ── B ───────────────────────────────────────────────────────────────
print("B. 소속팀 성적")
check("리그 0.4 / 컵 0.1 / 대륙대회 0.5 → 최대 0.60 / 0.15 / 0.75점",
      close(B.W_TEAM * B.team_value(1, 0, 0), 0.6) and close(B.W_TEAM * B.team_value(0, 1, 0), 0.15)
      and close(B.W_TEAM * B.team_value(0, 0, 1), 0.75))
check("챔스 우승(대륙 1.0) > 리그 우승(리그 1.0)", B.team_value(0, 0, 1) > B.team_value(1, 0, 0))
team = [P(10, "ST", 25, 5, ovr=97, cont_v=1.0, league_rank=1), P(11, "CM", 6, 10, ovr=95, cont_v=1.0, league_rank=1),
        P(12, "CB", 1, 1, ovr=93, cont_v=1.0, league_rank=1), P(13, "LB", 0, 1, ovr=90, cont_v=1.0, league_rank=1),
        P(14, "RB", 0, 1, ovr=90, cont_v=1.0, league_rank=1)]
sc = {e["player_id"]: e for e in B.score_season(copy.deepcopy(team))}
order = sorted(sc.values(), key=lambda e: -e["personal"])
check("팀 안 배분 = 개인 점수 순위 100/70/50/35%",
      [e["decay"] for e in order[:4]] == [1.0, 0.7, 0.5, 0.35])
check("소속팀 점수 = 1.5 × 팀 성적 × 배분", all(close(e["s_team"], 1.5 * e["team_v"] * e["decay"]) for e in sc.values()))
tie = B.score_season([P(20, "ST", 15, 5, team=7, league_rank=1), P(21, "ST", 15, 5, team=7, league_rank=1)])
check("개인 점수 완전 동점 두 명은 같은 배분(100%·100%)", [e["decay"] for e in tie] == [1.0, 1.0])

# ── C ───────────────────────────────────────────────────────────────
print("C. [확인 1] 챔스 우승팀이라는 이유만으로 1위가 되지 않음")
low_cl = P(30, "CM", 3, 5, ovr=90, team=2, cont_v=1.0, league_rank=1, league_exp=20, cup_v=1.0, intl_v=1.0)  # 트로피 만점
star = P(31, "ST", 30, 8, ovr=96, team=3)                                                                     # 트로피 0
res = B.score_season([low_cl, star])
check("트로피 만점(팀 1.5 + 국제 1.0)인 평범한 CM < 트로피 0인 30골 ST", res[0]["player_id"] == 31
      and close(res[1]["trophy"], 2.5), f"CM {res[1]['total']:.2f} (개인 {res[1]['personal']:.2f}+트로피 "
                                       f"{res[1]['trophy']:.2f}) · ST {res[0]['total']:.2f}")
check("트로피 최대치 2.5점 = 총점의 25%", close(B.W_TEAM + B.W_INTL, 2.5))

# ── D ───────────────────────────────────────────────────────────────
print("D. [확인 2] 팀 안 1순위를 배분으로 역전 못 함")
rng = random.Random(11)
over_same, over_part, teams = 0, 0, 2000
for _ in range(teams):
    rk, ex, cvt, ovt = rng.randint(1, 20), rng.randint(1, 20), rng.random(), rng.random()   # 팀 단위 결과
    lvl = rng.choice([1.0, 0.95, 0.8])
    tm = []
    for i in range(6):
        pc, po = rng.random(), rng.random()                          # 선수별 컵·대륙대회 출전 비율
        tm.append(P(100 + i, rng.choice(["ST", "CM", "CB", "LW", "CDM"]), rng.randint(0, 25), rng.randint(0, 12),
                    ovr=rng.randint(86, 98), team=5, league_rank=rk, league_exp=ex, league_level=lvl, cup_v=cvt,
                    cont_v=ovt, _pc=pc, _po=po))
    same = B.score_season(copy.deepcopy(tm))
    p1 = max(same, key=lambda e: e["personal"])
    over_same += any(e["total"] > p1["total"] + 1e-12 for e in same if e is not p1)
    for e in tm:
        e["cup_v"], e["cont_v"] = cvt * e["_pc"], ovt * e["_po"]
    part = B.score_season(tm)
    p1 = max(part, key=lambda e: e["personal"])
    over_part += any(e["total"] > p1["total"] + 1e-12 for e in part if e is not p1)
check("팀 결과가 같으면 팀 안 개인 점수 1순위를 동료가 총점으로 역전 0회(무작위 2000팀)", over_same == 0, f"{over_same}회")
print(f"  참고: 컵·대륙대회 출전 비율이 선수마다 다르면 역전 {over_part}/{teams}팀 — 1순위가 그 대회를 덜 뛴 경우"
      f"(실제 세이브 측정은 tools/ballon_check.py)")

# ── E ───────────────────────────────────────────────────────────────
print("E. [확인 3] FW 골 독점 아님")
avg_st = B.personal(P(40, "ST", 14, 4, ovr=93))
cm = B.personal(P(41, "CM", 9, 15, ovr=93))
cdm = B.personal(P(42, "CDM", 5, 10, ovr=93))
cb = B.personal(P(43, "CB", 5, 4, ovr=93, team_gc=0.95))
check("좋은 시즌 CM(9골 15도움) > 평범한 ST(14골 4도움)", cm["personal"] > avg_st["personal"],
      f"CM {cm['personal']:.2f} vs ST {avg_st['personal']:.2f}")
check("좋은 시즌 CDM(5골 10도움) > 평범한 ST", cdm["personal"] > avg_st["personal"], f"CDM {cdm['personal']:.2f}")
check("최상급 CB(5골 4도움·리그 최소 실점급) > 평범한 ST", cb["personal"] > avg_st["personal"], f"CB {cb['personal']:.2f}")
big_st = B.personal(P(44, "ST", 30, 6, ovr=93))
check("하지만 30골 ST는 그 CM·CDM·CB보다 위(공격 생산량 반영)",
      big_st["personal"] > max(cm["personal"], cdm["personal"], cb["personal"]), f"ST30 {big_st['personal']:.2f}")

# ── F ───────────────────────────────────────────────────────────────
print("F. [확인 4] 내 선수 = AI 선수 (같은 기록이면 같은 점수)")
rng = random.Random(4)
same_all = True
for _ in range(500):
    pos = rng.choice(["ST", "LW", "CAM", "CM", "CDM", "CB", "LB", "GK"])
    comps = [C(k, rng.randint(0, 8), rng.randint(0, 5), rng.randint(0, 13), rng.choice([0.26, 0.52, 0.62, 1.0]),
               rng.uniform(0.9, 1.5)) for k in rng.sample(["cup", "cont", "sc", "cwc"], rng.randint(0, 4))]
    base = P(77, pos, rng.randint(0, 30), rng.randint(0, 15), m=rng.randint(27, 38), ovr=rng.randint(85, 99),
             team_gc=rng.uniform(0.9, 2.1), gk_save=rng.uniform(0.70, 0.83), comps=comps,
             league_level=rng.choice([1.0, 0.97, 0.81, 0.5]), league_rank=rng.randint(1, 20),
             league_exp=rng.randint(1, 20), cup_v=rng.random(), cont_v=rng.random(), intl_v=rng.random())
    me = copy.deepcopy(dict(base, player_id=-1))
    a, b = B.personal(base), B.personal(me)
    same_all &= all(a[k] == b[k] for k in a) and B.team_parts(base) == B.team_parts(me)
check("무작위 기록 500개(대회별 기록·리그 수준 포함): 선수 번호만 −1(내 선수)로 바꿔도 모든 항목 점수 동일", same_all)
pool = [P(200 + i, rng.choice(["ST", "CM", "CB", "LW"]), rng.randint(0, 28), rng.randint(0, 14), ovr=rng.randint(86, 98),
          team=rng.randint(1, 15), league_rank=rng.randint(1, 20), cont_v=rng.random(),
          comps=[C("cup", rng.randint(0, 6), rng.randint(0, 3), rng.randint(1, 6), 1.0, 1.43)]) for i in range(80)]
ref = B.score_season(copy.deepcopy(pool))
swap = copy.deepcopy(pool)
target = ref[0]["player_id"]
for e in swap:
    if e["player_id"] == target:
        e["player_id"] = -1
res = B.score_season(swap)
check("시즌 후보 80명 중 1위를 내 선수로 바꿔 넣어도 순위·총점 동일",
      [(-1 if e["player_id"] == target else e["player_id"]) for e in ref] == [e["player_id"] for e in res]
      and all(close(x["total"], y["total"]) for x, y in zip(ref, res)))
import inspect  # noqa: E402
src = inspect.getsource(B)
import re  # noqa: E402
check("모듈 안에 내 선수 번호·선수 종류 분기 없음(== -1 · MY_PLAYER · is_my · _SIA_ 없음)",
      not re.search(r"==\s*-1|!=\s*-1|MY_PLAYER|is_my|_SIA_", src))

# ── G ───────────────────────────────────────────────────────────────
print("G. [확인 5] 팀 수비 기록 영향 상한")
lo, hi = B.personal(P(50, "CB", 2, 2, team_gc=2.0)), B.personal(P(50, "CB", 2, 2, team_gc=0.94))
check("DF: 팀 실점 최악 ↔ 최고의 개인 점수 차 = 1.575점(개인 7.0의 22.5%)", close(hi["personal"] - lo["personal"], 1.575),
      f"{hi['personal'] - lo['personal']:.4f}")
glo = B.personal(P(51, "GK", 0, 0, gk_save=0.78, team_gc=2.0))
ghi = B.personal(P(51, "GK", 0, 0, gk_save=0.78, team_gc=0.94))
check("GK: 같은 차 = 0.7875점(개인 7.0의 11.25%)", close(ghi["personal"] - glo["personal"], 0.7875),
      f"{ghi['personal'] - glo['personal']:.4f}")
weak_good = B.personal(P(52, "CB", 6, 4, team_gc=2.0))
strong_bad = B.personal(P(53, "CB", 0, 0, team_gc=0.94))
check("개인 기록 좋은 약팀 CB(6골 4도움) > 개인 기록 없는 최강 수비팀 CB", weak_good["personal"] > strong_bad["personal"],
      f"{weak_good['personal']:.2f} vs {strong_bad['personal']:.2f}")
gk_save_hi = B.personal(P(54, "GK", 0, 0, gk_save=0.806, team_gc=2.0))
gk_save_lo = B.personal(P(55, "GK", 0, 0, gk_save=0.750, team_gc=0.94))
check("선방 최고 약팀 GK > 선방 중앙값 최강팀 GK", gk_save_hi["personal"] > gk_save_lo["personal"])

# ── H ───────────────────────────────────────────────────────────────
print("H. 결정론·빈 값")
rng = random.Random(9)
cands = [P(300 + i, rng.choice(["ST", "CM", "CB", "LW", "GK", "CDM"]), rng.randint(0, 28), rng.randint(0, 14),
           ovr=rng.randint(86, 98), team=rng.randint(1, 20), gk_save=rng.uniform(0.72, 0.82),
           league_rank=rng.randint(1, 20), league_exp=rng.randint(1, 20), cont_v=rng.random(), intl_v=rng.random(),
           comps=[C("cont", rng.randint(0, 6), rng.randint(0, 4), rng.randint(0, 13), 1.0)]) for i in range(150)]
cands.append(dict(cands[0], player_id=999))
ref = [e["player_id"] for e in B.top30(B.score_season(copy.deepcopy(cands)))]
same = True
for _ in range(30):
    sh = copy.deepcopy(cands)
    rng.shuffle(sh)
    same &= [e["player_id"] for e in B.top30(B.score_season(sh))] == ref
check("입력 순서 30번 섞어도 Top30 동일", same)
top = B.top30(B.score_season(copy.deepcopy(cands)))
cnt = {}
for e in top:
    cnt[e["team_id"]] = cnt.get(e["team_id"], 0) + 1
check("Top30 팀당 최대 3명", max(cnt.values()) <= 3 and len(top) == 30)
empty = B.personal({"player_id": 1, "position": "ST", "matches": 0})
check("기록 없음(0경기·값 비어 있음)도 오류 없이 0점대", empty["personal"] >= 0 and empty["s_part"] == 0.0)
check("리그 성적 입력 없음 → 리그 0점(오류 없음)", B.team_parts({"player_id": 1}) == (0.0, 0.0, 0.0))

# ── I ───────────────────────────────────────────────────────────────
print("I. [2차] C안 완만한 상한")
lo_, hi_ = 4.0, 36.2
check("p99.5 지점 = 0.9, 그 아래는 직선(중간값 → 0.45)",
      close(B.soft_band(hi_, lo_, hi_), 0.9) and close(B.soft_band((lo_ + hi_) / 2, lo_, hi_), 0.45))
check("초과분이 척도 폭 하나만큼이면 0.95, 아무리 커도 1 미만",
      close(B.soft_band(hi_ + (hi_ - lo_), lo_, hi_), 0.95) and B.soft_band(1e9, lo_, hi_) < 1.0)
xs = [hi_ + k for k in range(0, 200, 5)]
vals = [B.soft_band(x, lo_, hi_) for x in xs]
check("p99.5 위에서도 값이 계속 오르고 증가 폭은 줄어든다",
      all(b > a for a, b in zip(vals, vals[1:])) and all((c - b) < (b - a) for a, b, c in zip(vals, vals[1:], vals[2:])))
st45, st50, st70 = (B.personal(P(60, "ST", g, 8, ovr=97)) for g in (45, 50, 70))
check("ST 45골 < 50골 < 70골(도움 8, OVR 97) — 개인 점수가 서로 다름",
      st45["personal"] < st50["personal"] < st70["personal"],
      f"{st45['personal']:.3f} · {st50['personal']:.3f} · {st70['personal']:.3f}")
mx = B.personal(P(61, "ST", 500, 300, ovr=99))
check("극단값(500골 300도움 OVR 99)도 능력+생산성 7.0 · 생산성 5.25를 넘지 않음(출전 0.5는 별도)",
      mx["s_ovr"] + mx["s_prod"] < 7.0 + 1e-12 and mx["s_prod"] < 5.25,
      f"능력+생산성 {mx['s_ovr'] + mx['s_prod']:.4f} · 생산성 {mx['s_prod']:.4f}")
check("70골 0도움과 50골 20도움이 이제 다른 점수", not close(B.personal(P(62, "ST", 70, 0, ovr=97))["personal"],
                                                   B.personal(P(62, "ST", 50, 20, ovr=97))["personal"], 1e-6))

# ── J ───────────────────────────────────────────────────────────────
print("J. [2차] 공식 대회 전부 · 대회 수준 · 리그 수준")
base = P(70, "ST", 20, 5)
cup = P(70, "ST", 20, 5, comps=[C("cup", 6, 2, 5, 1.0, 1.0)])
pb, pc = B.productivity(base), B.productivity(cup)
check("국내컵 6골 2도움(5경기) — 공격 생산량에 0.25 × 리그 수준으로 더해짐",
      close(pc["vol_raw"] - pb["vol_raw"], 0.25 * (6 + 0.75 * 2)), f"+{pc['vol_raw'] - pb['vol_raw']:.3f}")
cupw = P(70, "ST", 20, 5, league_level=0.8, comps=[C("cup", 6, 2, 5, 0.8, 1.0)])
check("리그 수준 0.8이면 국내컵 몫도 0.8배", close(B.productivity(cupw)["vol_raw"], 0.8 * (20 + 3.75) + 0.25 * 0.8 * 7.5))
cl = P(70, "ST", 20, 5, comps=[C("cont", 6, 2, 10, 1.0)])
el = P(70, "ST", 20, 5, comps=[C("cont", 6, 2, 10, 0.62)])
asia = P(70, "ST", 20, 5, comps=[C("cont", 6, 2, 10, 0.26)])
v_cl, v_el, v_as = (B.productivity(x)["vol_raw"] - pb["vol_raw"] for x in (cl, el, asia))
check("대륙 대회 6골 2도움 — 유럽 챔스 1.0 > 유로파 0.62 > 아시아 챔스 0.26 배",
      close(v_cl, 7.5) and close(v_el, 0.62 * 7.5) and close(v_as, 0.26 * 7.5), f"{v_cl:.2f}/{v_el:.2f}/{v_as:.2f}")
scp = P(70, "ST", 20, 5, comps=[C("sc", 1, 0, 1, 1.0)])
check("대륙 슈퍼컵 1골 = 0.2 × 대륙 챔스 가중치", close(B.productivity(scp)["vol_raw"] - pb["vol_raw"], 0.2))
ign = P(70, "ST", 20, 5, comps=[C("cwc", 5, 2, 4), C("dsc", 2, 0, 1), C("lower_cup", 3, 1, 2)])
check("점수표에 없는 대회(클럽 월드컵·국내 슈퍼컵·3·4부컵)는 무시",
      close(B.productivity(ign)["rel_rate"], pb["rel_rate"]) and close(B.productivity(ign)["vol_raw"], pb["vol_raw"]))
c_env1 = B.productivity(P(70, "ST", 20, 5, comps=[C("cup", 4, 1, 5, 1.0, 1.0)]))
c_env143 = B.productivity(P(70, "ST", 20, 5, comps=[C("cup", 4, 1, 5, 1.0, 1.43)]))
check("대회 득점 환경(컵 1.43 — 골이 쉬운 대회)이면 같은 컵 기록의 기대 대비가 낮아짐",
      c_env143["rel_rate"] < c_env1["rel_rate"], f"{c_env1['rel_rate']:.4f} → {c_env143['rel_rate']:.4f}")
c_exact = B.productivity(P(70, "ST", 23, 0, comps=[C("cont", 23 * 10 / 38, 0, 10, 1.0, 1.0)]))
check("리그·대륙 대회 모두 기대치 그대로면 기대 대비 = 1.0", close(c_exact["rel_rate"], 1.0))
ab = P(70, "ST", 25, 5)
d_cl, d_el, d_as = (B.productivity(P(70, "ST", 25, 5, comps=[C("cont", 23 * 10 / 38, 0, 10, lv)]))["rel_rate"]
                    for lv in (1.0, 0.62, 0.26))
check("[3차 수정] 기대치만큼 한 대륙 대회는 수준이 낮을수록 기대 대비를 덜 끌어내림(수준 낮다고 더 깎이지 않음)",
      B.productivity(ab)["rel_rate"] > d_as > d_el > d_cl, f"{d_cl:.4f} < {d_el:.4f} < {d_as:.4f}")
asia = B.personal(P(70, "ST", 25, 5, comps=[C("intl", 5, 1, 7, 0.6 * 0.45, 1.2)]))
euro = B.personal(P(70, "ST", 25, 5, comps=[C("intl", 5, 1, 7, 0.6, 1.2)]))
wc8 = B.personal(P(70, "ST", 25, 5, comps=[C("intl", 8, 1, 7, 1.0, 1.2)]))
no_i = B.personal(ab)
check("국가대표 기대 이상(7경기 5골 1도움)이면 오름 — 월드컵 8골 > 유로 5골 > 아시안컵 5골 > 없음",
      wc8["personal"] > euro["personal"] > asia["personal"] > no_i["personal"],
      f"+{wc8['personal'] - no_i['personal']:.2f} / +{euro['personal'] - no_i['personal']:.2f} / "
      f"+{asia['personal'] - no_i['personal']:.2f}")
_k = B.SOFT_KNEE
B.SOFT_KNEE = 1.0
_hard = B.soft_band(36.2, 4.0, 36.2)
B.SOFT_KNEE = _k
check("척도 계수는 호출할 때 모듈 값을 읽음(비교 실험용)", close(_hard, 1.0) and close(B.soft_band(36.2, 4.0, 36.2), 0.9))
pl, ll, pt = (B.personal(P(71, "ST", 30, 6, league_level=lv)) for lv in (1.0, 0.968, 0.807))
check("같은 30골 6도움: 리그 수준 1.0(PL) > 0.968(라리가) > 0.807(포르투갈)",
      pl["personal"] > ll["personal"] > pt["personal"], f"{pl['personal']:.3f} > {ll['personal']:.3f} > {pt['personal']:.3f}")
plcl = B.personal(P(72, "ST", 20, 5, league_level=1.0, comps=[C("cont", 6, 2, 10, 1.0)]))
ptcl = B.personal(P(72, "ST", 20, 5, league_level=0.807, comps=[C("cont", 6, 2, 10, 1.0)]))
gap_l = B.productivity(P(72, "ST", 20, 5, league_level=1.0))["vol_raw"] - \
    B.productivity(P(72, "ST", 20, 5, league_level=0.807))["vol_raw"]
gap_c = B.productivity(P(72, "ST", 20, 5, league_level=1.0, comps=[C("cont", 6, 2, 10, 1.0)]))["vol_raw"] - \
    B.productivity(P(72, "ST", 20, 5, league_level=0.807, comps=[C("cont", 6, 2, 10, 1.0)]))["vol_raw"]
check("리그 수준은 리그·국내컵 기록에만 — 같은 챔스 기록의 몫은 소속 리그와 무관",
      close(gap_l, gap_c) and plcl["personal"] > ptcl["personal"])

# ── K ───────────────────────────────────────────────────────────────
print("K. [2차] 리그 성적 = 순위 점수 × 리그 수준 × 우승 난이도")
check("예상 1위 팀 우승 = 0.8 · 예상 6위(20팀) 우승 = 1.0 · 예상 3위 우승 = 0.88",
      close(B.difficulty(1, 1, 20), 0.8) and close(B.difficulty(1, 6, 20), 1.0) and close(B.difficulty(1, 3, 20), 0.88))
check("예상보다 낮은 순위·예상 모름 → 0.8", close(B.difficulty(5, 2, 20), 0.8) and close(B.difficulty(1, None, 20), 0.8))
check("리그 수준(league_power 비율): 502/502 = 1.0 · 405/502 = 0.807 · 아주 약한 리그 하한 0.5 · 값 없음 1.0",
      close(B.league_level(502, 502), 1.0) and close(B.league_level(405, 502), 405 / 502)
      and close(B.league_level(-80, 502), 0.5) and close(B.league_level(None, 502), 1.0))
check("리그 성적 = 순위 점수 × 수준 × 난이도 (PL 예상 1위 우승 0.8 · 포르투갈 예상 1위 우승 0.646)",
      close(B.league_value(1, 20, False, 1.0, 1), 0.8) and close(B.league_value(1, 18, False, 405 / 502, 1), 0.8 * 405 / 502))
check("마인츠형 이변(예상 12위 → 우승) 1.0 > 바이에른형(예상 1위 → 우승) 0.8",
      B.league_value(1, 18, False, 0.94, 12) > B.league_value(1, 18, False, 0.94, 1))
check("강등 = 0 · 2위 예상대로 = 0.85 × 0.8", close(B.league_value(17, 18, True, 1.0, 10), 0.0)
      and close(B.league_value(2, 20, False, 1.0, 2), 0.68))
tp = B.team_parts(P(73, "ST", 0, 0, league_rank=1, league_n=20, league_exp=1, league_level=0.807, cup_v=1.0, cont_v=0.5))
check("국내컵 = 단계 점수 × 리그 수준, 대륙 대회는 그대로", close(tp[1], 0.807) and close(tp[2], 0.5)
      and close(tp[0], 0.8 * 0.807))

# ── L ───────────────────────────────────────────────────────────────
print("L. [3차 확정] 리그 수준 1회 · 득점 환경 보정")
r1 = B.productivity(P(80, "ST", 30, 6, league_level=1.0))
r2 = B.productivity(P(80, "ST", 30, 6, league_level=0.807))
check("포지션 기대 대비에는 리그 수준이 없음(수준 1.0과 0.807의 기대 대비 같음)", close(r1["rel_rate"], r2["rel_rate"]))
check("공격 생산량에만 리그 수준 1회: 0.807배", close(r2["vol_raw"], 0.807 * r1["vol_raw"]))
pt = B.productivity(P(81, "ST", 30, 6, league_level=0.807, goal_env=0.72))
check("[4차] 공격 생산량 = 실제 골·도움 × 득점 계수^−0.5 × 리그 수준 (포르투갈 30골 6도움 → 34.5 ÷ √0.72 × 0.807)",
      close(pt["vol_raw"], 34.5 / 0.72 ** 0.5 * 0.807), f"{pt['vol_raw']:.3f}")
check("[4차] 포르투갈(0.72) 30골 = PL 35.4골(절반 보정)", close(30 / 0.72 ** 0.5, 35.355, 1e-3)
      and close(B.productivity(P(82, "ST", 30, 0, goal_env=0.72))["vol_raw"], 30 / 0.72 ** 0.5))
eq_pl = B.personal(P(82, "ST", 30, 6, league_level=1.0, goal_env=1.0))
eq_pt = B.personal(P(82, "ST", 30 * 0.72, 6 * 0.72, league_level=1.0, goal_env=0.72))
check("득점 환경 대비 같은 활약(포르투갈 21.6골 = PL 30골)·리그 수준 같음 → 기대 대비 같음, 공격 생산량은 절반 보정이라 낮음",
      close(eq_pl["rel"], eq_pt["rel"]) and eq_pt["vol"] < eq_pl["vol"])
eq_pt2 = B.personal(P(82, "ST", 30 * 0.72, 6 * 0.72, league_level=0.807, goal_env=0.72))
check("같은 활약에 리그 수준만 0.807 → 차이는 공격 생산량 쪽에서만",
      close(eq_pt2["rel"], eq_pl["rel"]) and eq_pt2["vol"] < eq_pl["vol"])
cupg = B.productivity(P(83, "ST", 20, 5, goal_env=0.8, comps=[C("cup", 4, 0, 5, 1.0, 1.43)]))
nocup = B.productivity(P(83, "ST", 20, 5, goal_env=0.8))
check("국내컵 몫 = 0.25 × 리그 수준 × 골 ÷ (리그 득점 계수^0.5 × 컵 환경)",
      close(cupg["vol_raw"] - nocup["vol_raw"], 0.25 * 4 / (0.8 ** 0.5 * 1.43)))
clg = B.productivity(P(83, "ST", 20, 5, goal_env=0.8, league_level=0.8, comps=[C("cont", 4, 0, 10, 1.0, 0.98)]))
nocl = B.productivity(P(83, "ST", 20, 5, goal_env=0.8, league_level=0.8))
check("대륙 대회 몫은 리그 득점 환경·리그 수준과 무관(그 대회 환경만): 4 ÷ 0.98",
      close(clg["vol_raw"] - nocl["vol_raw"], 4 / 0.98))
ta1 = B.productivity(P(84, "ST", 30, 6))
ta2 = B.productivity(P(84, "ST", 30, 6, team_attack=1.7))
pm38 = 23 / 38 * 38
check("[5차] 팀 공격력 1.7 → 국내 기대치 × 1.7^0.25(1.14)만, 실제 골·공격 생산량은 그대로",
      close(ta2["rel_rate"], (36 + 10) / (pm38 * 1.7 ** 0.25 + 10)) and close(ta2["vol_raw"], ta1["vol_raw"])
      and ta2["rel_rate"] < ta1["rel_rate"], f"{ta1['rel_rate']:.4f} → {ta2['rel_rate']:.4f}")
c_a = B.productivity(P(84, "ST", 0, 0, matches=0, comps=[C("cont", 6, 0, 10, 1.0)]))
c_b = B.productivity(P(84, "ST", 0, 0, matches=0, team_attack=1.7, comps=[C("cont", 6, 0, 10, 1.0)]))
check("[4차] 대륙 대회 기대치에는 팀 공격력을 곱하지 않음", close(c_a["rel_rate"], c_b["rel_rate"]))
k_a = B.productivity(P(84, "ST", 0, 0, matches=0, comps=[C("cup", 6, 0, 5, 1.0)]))
k_b = B.productivity(P(84, "ST", 0, 0, matches=0, team_attack=1.7, comps=[C("cup", 6, 0, 5, 1.0)]))
check("[4차] 국내컵 기대치에는 곱함(국내 대회)", k_b["rel_rate"] < k_a["rel_rate"])
tm1 = B.score_season([P(85, "ST", 30, 6, league_rank=1, league_exp=1)])[0]
tm2 = B.score_season([P(85, "ST", 30, 6, league_rank=1, league_exp=1, team_attack=1.7)])[0]
check("[4차] 팀 성적·우승 점수는 팀 공격력과 무관", close(tm1["s_team"], tm2["s_team"]))
check("리그 수준 비교 설정(A/B/B+C)이 모듈에서 지워짐", not any(hasattr(B, k) for k in ("PERS_LEVEL_K", "TEAM_LEVEL_K",
                                                                                     "LEVEL_IN_REL", "eff_level")))

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
sys.exit(1 if FAILS else 0)