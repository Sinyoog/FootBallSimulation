# -*- coding: utf-8 -*-
"""tools/user_manager_qa.py — 감독 모드 대비 최소 조치(team_plan.py) 스모크 테스트 (2026-10 신설)

새 게임(시드 12345)에서 잉글랜드 1부 한 팀에 사용자 감독을 앉히고:
  1) 사용자 감독이 없을 때 모든 팀이 AI 통제인지
  2) 부임: 전임 감독 무직 처리, 전술 4축 저장
  3) 선택 저장: 포메이션(teams.formation) + 선발 11명(없는 선수 자리는 AI가 채움), 잘못된 값 거부
  4) 선택이 결과에 닿는지: 같은 시드 300경기, 일부러 OVR 낮은 선발 vs AI 선발
  5) 한 시즌 진행 후: 감독 시장·포메이션 재검토·대표팀 후보·감독 보정이 사용자 팀/감독을 안 건드리는지
  6) 해임: 통제 주체가 AI로 돌아가고 팀이 공석이 되는지
를 확인한다. 소요 약 3분(새 게임 생성 약 35초 + 1시즌 약 2분).

사용: python tools/user_manager_qa.py [일수(기본 370)]
"""
import _path  # noqa: F401
import os, random, shutil, sys

ROOT = _path.ROOT
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 370
OUT = os.path.join(ROOT, "qa_runs", "user_manager_qa")
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
shutil.copy(os.path.join(ROOT, "game.db"), os.path.join(OUT, "game.db"))
sys.path.insert(0, ROOT)
os.chdir(OUT)
import database
database.DB_PATH = os.path.join(OUT, "game.db")
database.flush_to_disk_async = lambda: None
random.seed(12345)
database.init_db()
database.reset_game_data()
import game_engine as ge
import intl_engine
import team_plan as tp
from match_sim import tactical_engine as te
if not ge.get_player():
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")

ok_all = True
def check(name, cond, extra=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name} {extra}")

conn = database.get_conn()
year = ge.get_state().get("current_year")
my_tid = ge.get_player().get("current_team_id")
# 잉글랜드 1부에서 내 선수 팀이 아닌 팀 둘
rows = conn.execute(
    """SELECT t.id, t.name, t.formation FROM teams t JOIN leagues l ON l.id=t.league_id
       JOIN countries c ON c.id=l.country_id
       WHERE c.name='잉글랜드' AND l.tier=1 AND t.id<>? ORDER BY t.id LIMIT 2""",
    (my_tid or -1,)).fetchall()
U, OPP = rows[0]["id"], rows[1]["id"]
print(f"사용자 팀 {rows[0]['name']}({U}) / 상대 {rows[1]['name']}({OPP}) / {year}년")

print("1) 사용자 감독 없을 때")
check("user_control 비어 있음", tp.user_control() == {})
check("통제 주체 AI", tp.team_controller(U) == "AI")

print("2) 부임")
old = database.get_team_manager(U)
mid = tp.appoint_user_manager(U, year, name="플레이어 감독", nationality="대한민국", birth_year=1970,
                              tactics={"style_attack": "VERY_ATTACKING", "style_buildup": "DIRECT",
                                       "style_press": "HIGH_PRESS", "manager_type": "유스 중시"})
check("통제 주체 USER", tp.team_controller(U) == "USER")
m = database.get_team_manager(U)
check("현직 감독 = 사용자 감독", m and m["id"] == mid and m["is_user"] == 1)
check("전술 4축 저장", (m["style_attack"], m["style_buildup"], m["style_press"], m["manager_type"])
      == ("VERY_ATTACKING", "DIRECT", "HIGH_PRESS", "유스 중시"))
oldm = conn.execute("SELECT status FROM managers WHERE id=?", (old["id"],)).fetchone()
check("전임 감독 무직", oldm["status"] == "free")

print("3) 선택 저장(포메이션 + 선발 11명) — 일부러 OVR 낮은 선수 위주로")
roster = conn.execute("SELECT id, position, ovr FROM ai_players WHERE team_id=? ORDER BY ovr ASC",
                      (U,)).fetchall()
from constants import FORMATION_SLOTS
slots = FORMATION_SLOTS["3-5-2"]
picks, used = [], set()
for s in slots:
    cand = [r for r in roster if r["id"] not in used and ((r["position"] == "GK") == (s == "GK"))]
    picks.append(cand[0]["id"]); used.add(cand[0]["id"])
picks[5] = 999999999   # 없는 선수 → AI가 그 자리만 채워야 함
tp.save_user_plan(U, formation="3-5-2", lineup_ids=picks)
check("teams.formation 갱신", conn.execute("SELECT formation FROM teams WHERE id=?", (U,)).fetchone()[0] == "3-5-2")
plan = tp.build_team_plan(U, "3-5-2")
got = [p["id"] if p else None for p in plan["lineup"]]
check("선발: 지정 10명 그대로", all(got[i] == picks[i] for i in range(11) if i != 5))
check("선발: 무효 자리는 AI가 채움", got[5] is not None and got[5] not in picks, f"→ {got[5]}")
check("벤치 7명(미지정 → AI)", len(plan["bench"]) == 7)
ai_plan = tp.build_team_plan(OPP, "4-4-2")
check("상대 팀은 AI 계획", ai_plan["controller"] == "AI")
try:
    tp.save_user_plan(U, formation="9-9-9"); check("잘못된 포메이션 거부", False)
except ValueError:
    check("잘못된 포메이션 거부", True)
try:
    tp.save_user_plan(OPP, formation="4-4-2"); check("AI 팀 저장 거부", False)
except ValueError:
    check("AI 팀 저장 거부", True)

print("4) 선택이 결과에 닿는가 — 같은 시드, 사용자 계획 vs 계획 삭제(AI 선발)")
def run(n=300):
    gf = ga = 0
    for s in range(n):
        r = te.simulate_my_match(U, OPP, "3-5-2", "4-4-2", seed=s)
        gf += r["home_score"]; ga += r["away_score"]
    return gf / n, ga / n
u_gf, u_ga = run()
tp.clear_user_plan(U)
a_gf, a_ga = run()
print(f"     사용자 선발(저OVR) 득 {u_gf:.2f} 실 {u_ga:.2f} | AI 선발 득 {a_gf:.2f} 실 {a_ga:.2f}")
check("저OVR 선발이 AI 선발보다 실점 많음", u_ga > a_ga)
tp.save_user_plan(U, lineup_ids=picks)   # 다시 저장해서 시즌 진행

print(f"5) {DAYS}일 진행(시즌 종료·감독 시장·포메이션 재검토 통과)")
cur = ge.get_state().get("current_day") or 1
schedule = [(d, "휴식", {}) for d in range(cur, cur + DAYS + 1)]
remaining = list(schedule)
for _g in range(60):
    ge.advance_days(remaining)
    pending = intl_engine.get_pending_choice()
    if not pending:
        break
    for opt in pending.get("options", []):
        intl_engine.decline_national_team(opt["tournament_id"])
    cd = ge.get_state().get("current_day")
    remaining = [i for i in schedule if i[0] >= cd]
    if not remaining:
        break
conn = database.get_conn()
st = ge.get_state()
print(f"     → {st.get('current_year')}년 {st.get('current_day')}일")
m2 = database.get_team_manager(U)
check("시즌 후에도 사용자 감독 재임", m2 and m2["id"] == mid)
check("포메이션 AI가 안 바꿈", conn.execute("SELECT formation FROM teams WHERE id=?", (U,)).fetchone()[0] == "3-5-2")
nat = conn.execute("SELECT COUNT(*) FROM national_team_managers WHERE manager_id=?", (mid,)).fetchone()[0]
check("대표팀 감독으로 안 뽑힘", nat == 0)
links = conn.execute("SELECT COUNT(*) FROM team_managers WHERE manager_id=?", (mid,)).fetchone()[0]
check("다른 클럽으로 안 옮겨짐(재임 1건)", links == 1)
check("감독 보정 캐시에 사용자 팀 없음", U not in ge._mgr_match_bonus_cache or not ge._mgr_match_bonus_cache.get("__built__"))
ge._build_mgr_match_bonus_cache(None)
check("감독 보정 캐시 재생성 후에도 없음", U not in ge._mgr_match_bonus_cache)
n_sacked = conn.execute(
    "SELECT COUNT(*) FROM team_managers WHERE start_year=? AND end_year IS NOT NULL", (year,)).fetchone()[0]
print(f"     (참고: 그 해 시작한 재임 중 끝난 건수 {n_sacked})")

print("6) 해임")
r = tp.release_user_manager(U, st.get("current_year"))
check("해임 → 통제 주체 AI", r == mid and tp.team_controller(U) == "AI")
check("선발 계획 삭제", tp.get_user_plan(U) is None)
check("팀에 현직 감독 없음(다음 시장에서 공석)", database.get_team_manager(U) is None)
print("RESULT", "PASS" if ok_all else "FAIL")