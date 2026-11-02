"""[2026-10-30] GK 정확히 3명 회귀 테스트(constants.WORLD_SQUAD_BY_FORMATION 켬).

1) 임대 온 GK를 포함해 명단 GK가 3명이면 보충이 GK를 더 뽑지 않는다(수정 전 경로는 뽑는다 — 테스트 민감도 확인).
2) 외국인 정원 정리처럼 GK가 3명인 팀으로 GK가 옮겨 가도, 시즌 전환 마지막 정리 뒤 모든 팀이 정확히 3명이다.
3) 시즌 전환(run_ai_offseason)을 한 번 돌린 뒤 GK 2명·4명 팀이 있으면 실패한다(검사기가 위반을 잡는지도 확인).

사용: python tools/test_gk_three.py   (game.db를 임시 폴더에 복사해서 쓴다 — 원본은 읽기만 한다. 약 2~3분)
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


TMP = tempfile.mkdtemp(prefix="gk3_")
shutil.copy(os.path.join(_path.ROOT, "game.db"), os.path.join(TMP, "game.db"))
os.chdir(TMP)
import constants as C
C.set_world_squad_by_formation(True)
import database as DB
DB.DB_PATH = os.path.join(TMP, "game.db")
DB.flush_to_disk_async = lambda: None
random.seed(20261030)
DB.init_db()
DB.reset_game_data()
import ai_lifecycle as AL
import game_engine as GE
_fl = DB.get_conn().execute("SELECT flag FROM countries WHERE name='잉글랜드'").fetchone()[0]
GE.create_player(name="GK3 Test", position="CM", sub_role="", nationality="잉글랜드", flag=_fl)
conn = DB.get_conn()
c = conn.cursor()
YEAR = GE.get_state().get("current_year")

print("[0] 월드 생성 직후")
v0 = AL.gk_count_violations(c)
check("월드 생성 직후 GK 정확히 3명", not v0, str(v0[:5]))

# ── [1] 보충: 임대 온 GK 포함 명단 3명 → GK 추가 없음 ─────────────────
print("[1] 보충 — 명단 GK 3명(임대 온 GK 포함)이면 GK를 더 뽑지 않는다")
from constants import get_country_league_grade, COUNTRY_LEAGUE_OVR_OVERRIDE
mil = set()
try:
    from military_service import get_military_team_ids
    mil = set(get_military_team_ids(c))
except Exception:
    pass
teams = [r for r in c.execute("""SELECT t.id, t.name, t.current_tier, cn.name, cn.continent, t.formation
                                 FROM teams t JOIN leagues l ON t.league_id=l.id
                                 JOIN countries cn ON l.country_id=cn.id ORDER BY t.id""")
         if r[0] not in mil][:60]
name_cache = AL._build_name_cache(c)
fixed_gk = bug_gk = tried = 0
for tid, tname, tier, cname, cont, form in teams:
    ro = [(r[0], r[1], r[2]) for r in c.execute(
        "SELECT id, position, ovr FROM ai_players WHERE team_id=? ORDER BY id", (tid,))]
    gks = [r for r in ro if r[1] == "GK"]
    if len(gks) != 3:
        continue
    loan_in = gks[0][0]
    roster_by_team = {tid: ro}
    held = [(p, None) for pid, p, _o in ro if pid != loan_in]     # 장기 보유 = 임대 온 GK 제외
    grade = get_country_league_grade(cname)
    is_ov = cname in COUNTRY_LEAGUE_OVR_OVERRIDE
    alts = AL._sq_alt_map(c, {tid: form}).get(tid)
    for k in range(5):
        tried += 1
        random.seed(f"gk3:{tid}:{k}")
        rows = AL._gen_topup_rows(c, tid, tier, cname, cont, tname, grade, 4, roster_by_team, name_cache,
                                  YEAR, is_ov, formation=form, held_positions=held, alts=alts)
        fixed_gk += sum(1 for r in rows if r[2] == "GK")
        # 수정 전 경로 재현: 부족분을 장기 보유 기준 GK로 계산(GK 집계 통일 없음)
        random.seed(f"gk3:{tid}:{k}")
        rows_b = AL._gen_topup_rows(c, tid, tier, cname, cont, tname, grade, 4, roster_by_team, name_cache,
                                    YEAR, is_ov, formation=form, held_positions=held, alts=None)
        bug_gk += sum(1 for r in rows_b if r[2] == "GK")
check(f"수정 경로: {tried}회 보충(각 4명)에서 GK 0명", fixed_gk == 0 and tried > 0, f"GK {fixed_gk}명")
check("민감도: 장기 보유 기준(수정 전) 경로는 GK를 뽑는다", bug_gk > 0, f"GK {bug_gk}명")

# ── [2] GK가 3명인 팀으로 이동 → 마지막 정리 뒤 정확히 3명 ──────────────
print("[2] 외국인 정원 정리처럼 GK가 3명 팀으로 이동한 뒤 — 마지막 정리")
pair = []
for tid, tname, tier, cname, cont, form in teams:
    if len(pair) == 4:
        break
    pair.append((tid, cont))
moved = []
for i in range(0, 4, 2):
    a, b = pair[i][0], pair[i + 1][0]
    gk_b = c.execute("SELECT id FROM ai_players WHERE team_id=? AND position='GK' ORDER BY ovr LIMIT 1",
                     (b,)).fetchone()[0]
    c.execute("UPDATE ai_players SET team_id=? WHERE id=?", (a, gk_b))
    moved.append((a, b))
v_before = dict(AL.gk_count_violations(c))
check("이동 직후 검사기가 4명·2명 팀을 잡는다",
      all(v_before.get(a) == 4 and v_before.get(b) == 2 for a, b in moved), str(v_before))
AL._sq2_reset_log()
left = AL._gk_final_balance(c, YEAR)
check("마지막 정리 뒤 전 팀 GK 정확히 3명", left == 0, f"남은 위반 {left}, 로그 {AL.SQ2_LOG}")

# ── [3] 시즌 전환 한 번 → 전 팀 GK 정확히 3명 ───────────────────────────
print("[3] 시즌 전환(run_ai_offseason) 종료 시 GK 2명·4명 팀 없음")
conn.commit()
try:
    AL.run_ai_offseason(YEAR)
    ok_run = True
except Exception as e:
    ok_run = False
    print("   시즌 전환 오류:", repr(e))
check("시즌 전환 실행", ok_run)
c = DB.get_conn().cursor()
v3 = AL.gk_count_violations(c)
check("시즌 전환 종료 시 GK 2명·4명 팀 0", not v3, f"{len(v3)}팀 {v3[:8]} / 로그 {AL.SQ2_LOG}")
# 검사기 민감도: 한 팀을 일부러 2명으로 만들면 실패로 잡혀야 한다
t0 = next(t for t, *_ in teams if t not in dict(v3))
g0 = c.execute("SELECT id FROM ai_players WHERE team_id=? AND position='GK' LIMIT 1", (t0,)).fetchone()[0]
c.execute("UPDATE ai_players SET position='CB' WHERE id=?", (g0,))
check("검사기 민감도: GK 2명 팀을 위반으로 잡는다", (t0, 2) in AL.gk_count_violations(c))
c.execute("UPDATE ai_players SET position='GK' WHERE id=?", (g0,))

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)