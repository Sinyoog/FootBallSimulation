"""[2026-10-30] 대항전 득점환경(competition_common.CONT_COMP_GOAL_MULT — 채택·확정) 단위 테스트.
"예전 경로"(OFF)는 대상 대회 목록(_CONT_COMP_GOAL_TABLES)을 비워 재현한다 — 그러면 모든 대회가 배율 없이 예전 호출 그대로.

[A] 표: 14개 (대륙, 대회) 배율, 예전 경로거나 대상 밖(슈퍼컵·클럽월드컵)이면 대회 행을 읽지 않고 1.0.
[B] 실제 경로(sim_ai_match): ON이면 챔스·유로파·컨퍼런스 AI 경기 스코어 생성에 그 대회 배율이 넘어가고,
    대륙 슈퍼컵은 배율 없이 예전 호출 그대로. 예전 경로(OFF)면 모든 대회가 예전 호출 그대로이고 난수 소비도 같다.
[C] 배율 효과: _gen_score 평균 득점이 배율만큼 줄고(±4%), 배율이 낮을수록 더 줄며, 승패와 스코어가 맞는다.

사용: python tools/test_comp_goal_env.py   (game.db를 임시 폴더에 복사해서 쓴다 — 원본은 읽기만 한다. 약 1분)
"""
import _path  # noqa: F401
import os
import random
import shutil
import sys
import tempfile

FAILS = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""), flush=True)
    if not cond:
        FAILS.append(name)


TMP = tempfile.mkdtemp(prefix="cgoal_")
shutil.copy(os.path.join(_path.ROOT, "game.db"), os.path.join(TMP, "game.db"))
os.chdir(TMP)
import database as DB
DB.DB_PATH = os.path.join(TMP, "game.db")
DB.flush_to_disk_async = lambda: None
import competition.competition_common as CC

# ── [A] ──────────────────────────────────────────────────────────────────
print("[A] 표·스위치")
TABLES = CC._CONT_COMP_GOAL_TABLES
check("대상 대회 = 챔스·유로파·컨퍼런스", TABLES == frozenset(("cl_matches", "el_matches", "ecl_matches")), str(sorted(TABLES)))
check("배율 14개(남미 컨퍼런스 없음), 범위 0.65~0.95", len(CC.CONT_COMP_GOAL_MULT) == 14
      and min(CC.CONT_COMP_GOAL_MULT.values()) == 0.65 and max(CC.CONT_COMP_GOAL_MULT.values()) == 0.95)
CC._CONT_COMP_GOAL_TABLES = frozenset()
check("예전 경로(OFF)면 대상 대회도 1.0", CC.cont_comp_goal_mult({"continent": "유럽"}, "ecl_matches") == 1.0)
CC._CONT_COMP_GOAL_TABLES = TABLES
check("ON: 유럽 컨퍼런스 0.65·북미 유로파 0.70·유럽 챔스 0.95",
      (CC.cont_comp_goal_mult({"continent": "유럽"}, "ecl_matches"), CC.cont_comp_goal_mult({"continent": "북미"}, "el_matches"),
       CC.cont_comp_goal_mult({"continent": "유럽"}, "cl_matches")) == (0.65, 0.70, 0.95))
try:
    ok = all(CC.cont_comp_goal_mult({}, tb) == 1.0 for tb in ("sc_matches", "cwc_matches", "domestic_sc_matches"))
except Exception as e:   # noqa: BLE001
    ok = False
check("ON이어도 대상 밖 대회는 대회 행을 읽지 않고 1.0(대륙 칸이 없어도 안전)", ok)

# ── [B] ──────────────────────────────────────────────────────────────────
print("[B] 실제 경로(sim_ai_match)")
random.seed(20261030)
DB.init_db()
DB.reset_game_data()
import game_engine as GE
from competition.champions_engine import CHAMPIONS_CFG
from competition.europa_engine import EUROPA_CFG
from competition.conference_engine import CONFERENCE_CFG
from competition.super_cup_engine import SC_CFG
_fl = DB.get_conn().execute("SELECT flag FROM countries WHERE name='잉글랜드'").fetchone()[0]
GE.create_player(name="Comp Goal Test", position="ST", sub_role="", nationality="잉글랜드", flag=_fl)
MY = GE.get_player()["current_team_id"]
H, A = [r[0] for r in DB.get_conn().execute(
    "SELECT t.id FROM teams t JOIN leagues l ON l.id=t.league_id WHERE l.tier=1 AND t.id!=? ORDER BY t.id LIMIT 2", (MY,))]
CALLS = []
_orig = GE._gen_score


def _spy(outcome, diff=0.0, goal_mult=1.0, allow_extreme=False):
    CALLS.append(goal_mult)
    return _orig(outcome, diff, goal_mult=goal_mult, allow_extreme=allow_extreme)


def run(cfg, cont, on, seed=7):
    CC._CONT_COMP_GOAL_TABLES = TABLES if on else frozenset()
    CALLS.clear()
    GE._gen_score = _spy
    random.seed(seed)
    try:
        t = {"id": -1, "name": "테스트 대회", "continent": cont}
        m = {"id": -1, "stage": "league", "home_team_id": H, "away_team_id": A, "week": 10, "day": 0, "is_my": 0}
        batch = []
        CC.sim_ai_match(cfg, t, m, batch=batch)
    finally:
        GE._gen_score = _orig
        CC._CONT_COMP_GOAL_TABLES = TABLES
    return list(CALLS), random.getstate(), batch


for cfg, label in ((CHAMPIONS_CFG, "챔스"), (EUROPA_CFG, "유로파"), (CONFERENCE_CFG, "컨퍼런스")):
    for cont in ("유럽", "아시아", "북미"):
        exp = CC.CONT_COMP_GOAL_MULT.get((cont, cfg.match_table), 1.0)
        on_calls, _s, on_b = run(cfg, cont, True)
        off_calls, off_st, off_b = run(cfg, cont, False)
        check(f"{cont} {label}: ON 배율 {exp} 전달 / OFF 1.0", on_calls == [exp] and off_calls == [1.0], f"{on_calls} / {off_calls}")
sc_on, sc_st_on, sc_b_on = run(SC_CFG, "유럽", True)
sc_off, sc_st_off, sc_b_off = run(SC_CFG, "유럽", False)
check("대륙 슈퍼컵: ON이어도 배율 없음·결과와 난수 소비가 OFF와 같다", sc_on == sc_off == [1.0] and sc_st_on == sc_st_off and sc_b_on == sc_b_off)

# ── [C] ──────────────────────────────────────────────────────────────────
print("[C] 배율 효과(_gen_score 20,000경기)")


def mean_goals(f, diff, seed=11):
    random.seed(seed)
    tot = bad = 0
    for i in range(20000):
        out = ("home", "away", "draw")[i % 3]
        hs, as_ = GE._gen_score(out, diff, goal_mult=f) if f != 1.0 else GE._gen_score(out, diff)
        tot += hs + as_
        bad += (out == "home" and hs <= as_) or (out == "away" and as_ <= hs) or (out == "draw" and hs != as_)
    return tot / 20000, bad


for diff in (3.0, 25.0):
    m1, _b = mean_goals(1.0, diff)
    prev = m1
    for f in (0.95, 0.80, 0.65):
        mf, bad = mean_goals(f, diff)
        check(f"배율 {f}, 전력차 {diff:.0f}: 평균 득점 비 ≈ 배율(±4%), 배율이 낮을수록 감소, 승패와 스코어 일치",
              abs(mf / m1 - f) <= 0.04 and mf < prev and bad == 0, f"{m1:.2f} → {mf:.2f} (비 {mf / m1:.3f}), 불일치 {bad}")
        prev = mf

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)