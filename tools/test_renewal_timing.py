"""[2026-10-30] 재계약 시기(만료 2년·1년 전 조기 제안)·새 계약 기간(재계약 시점부터 새로 N년) 단위 테스트.

사용: python tools/test_renewal_timing.py   (game.db를 임시 폴더에 복사해서 쓴다 — 원본은 읽기만 한다. 약 1분)
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


TMP = tempfile.mkdtemp(prefix="renew_")
shutil.copy(os.path.join(_path.ROOT, "game.db"), os.path.join(TMP, "game.db"))
os.chdir(TMP)
import constants as C
import database as DB
DB.DB_PATH = os.path.join(TMP, "game.db")
DB.flush_to_disk_async = lambda: None
random.seed(7)
DB.init_db()
DB.reset_game_data()
import game_engine as GE
_fl = DB.get_conn().execute("SELECT flag FROM countries WHERE name='잉글랜드'").fetchone()[0]
GE.create_player(name="Renew Test", position="ST", sub_role="", nationality="잉글랜드", flag=_fl)
conn = DB.get_conn()
Y = GE.get_state().get("current_year")
tid, lid = conn.execute("""SELECT t.id, t.league_id FROM teams t JOIN leagues l ON l.id=t.league_id
                           JOIN countries cn ON cn.id=l.country_id WHERE cn.name='잉글랜드' AND l.tier=1
                           ORDER BY t.id LIMIT 1""").fetchone()
opp = conn.execute("SELECT id FROM teams WHERE league_id=? AND id!=? LIMIT 1", (lid, tid)).fetchone()[0]
# 시즌 Y 리그 34경기 결과(풀시즌 경기 수 분모)
conn.execute("DELETE FROM match_results WHERE year=? AND (home_team_id=? OR away_team_id=?)", (Y, tid, tid))
for k in range(34):
    conn.execute("""INSERT INTO match_results(league_id, week, home_team_id, away_team_id, home_score, away_score,
                    season, year, day) VALUES(?,?,?,?,?,?,?,?,?)""", (lid, k + 1, tid, opp, 1, 0, 1, Y, k + 1))
conn.commit()


def setup(end, matches, ovr, age, loan=0):
    GE.update_player(current_team_id=tid, current_league_id=lid, contract_end_year=end, ovr=ovr, age=age,
                     loan_from_team_id=loan, salary=1000)
    c = DB.get_conn()
    c.execute("DELETE FROM my_player_season_stats WHERE year=?", (Y,))
    c.execute("INSERT INTO my_player_season_stats(year, team_id, matches, goals, assists, rating) VALUES(?,?,?,?,?,?)",
              (Y, tid, matches, 5, 2, 7.0))
    c.commit()
    return GE.get_player()


print("[1] 조기 제안 대상 판정(시즌 Y가 끝난 시점, 남은 시즌 = 계약 마지막 해 − Y)")
C.CONTRACT_EARLY_RENEWAL = True
r = GE.my_early_renewal_check(setup(Y + 2, 30, 99, 26), Y, age=26)
check("2년 전 · 주전(30/34) · 팀 OVR 1위 → 제안(핵심 주전)", r and r["reason"] == "핵심 주전", str(r))
r = GE.my_early_renewal_check(setup(Y + 2, 30, 40, 26), Y, age=26)
check("2년 전 · 주전이지만 팀 OVR 상위 3명 밖 · 26세 → 제안 없음", r is None, str(r))
r = GE.my_early_renewal_check(setup(Y + 2, 15, 40, 22), Y, age=22)
check("2년 전 · 22세 · 로테이션(15/34) → 제안(장기 육성 대상)", r and r["reason"] == "장기 육성 대상", str(r))
r = GE.my_early_renewal_check(setup(Y + 2, 10, 40, 22), Y, age=22)
check("2년 전 · 22세 · 대기(10/34) → 제안 없음", r is None, str(r))
r = GE.my_early_renewal_check(setup(Y + 1, 15, 40, 28), Y, age=28)
check("1년 전 · 로테이션 → 제안", r and r["remaining"] == 1 and r["role"] == "로테이션", str(r))
r = GE.my_early_renewal_check(setup(Y + 1, 10, 40, 28), Y, age=28)
check("1년 전 · 대기 → 제안 없음", r is None, str(r))
check("3년 남음 → 제안 없음", GE.my_early_renewal_check(setup(Y + 3, 30, 99, 26), Y, age=26) is None)
check("만료(0년) → 조기 제안 경로 아님(기존 만료 규칙)", GE.my_early_renewal_check(setup(Y, 30, 99, 26), Y, age=26) is None)
check("임대 중 → 제안 없음", GE.my_early_renewal_check(setup(Y + 1, 30, 99, 26, loan=opp), Y, age=26) is None)
C.CONTRACT_EARLY_RENEWAL = False
check("스위치 OFF → 제안 없음", GE.my_early_renewal_check(setup(Y + 2, 30, 99, 26), Y, age=26) is None)
C.CONTRACT_EARLY_RENEWAL = True

print("[2] 새 계약 기간 — 재계약 시점부터 새로 N년(남은 기간에 더하지 않음)")
# 사용자 예시: 2025년 5년 계약 = 2025~2029시즌(마지막 해 2029, '2025~2030' 표기). 2027시즌이 끝나면 2년 남음.
p = {"contract_end_year": 2029}
check("2028 프리시즌 5년 재계약 → 마지막 해 2032(2028~2032시즌, '2028~2033' 표기)",
      GE.renewal_contract_end(2028, 1, 5) == 2032)
check("남은 2년(2028·2029)일 때 최소 기간 3년(기존 계약보다 길게)", GE.renewal_min_years(p, 2028, 1) == 3)
check("만료 재계약(남은 기간 없음) 최소 1년", GE.renewal_min_years({"contract_end_year": 2027}, 2028, 1) == 1)
check("시즌 중(5주~) 수락이면 다음 시즌이 1년차", GE.renewal_contract_end(2028, 10, 3) == 2031)

print("[3] 조기 제안 거절 → 기존 계약 유지")
setup(Y + 2, 30, 99, 26)
GE.update_player(_contract_renew_offer=1500, _contract_renew_years=4, _contract_renew_early=1)
GE.decline_early_renewal()
pp = GE.get_player()
check("거절 뒤 소속·계약 마지막 해 그대로, 제안 플래그 0",
      pp["current_team_id"] == tid and pp["contract_end_year"] == Y + 2
      and not pp["_contract_renew_offer"] and not pp["_contract_renew_early"], str({k: pp[k] for k in (
          "current_team_id", "contract_end_year", "_contract_renew_offer", "_contract_renew_early")}))

print("[4] 선수 쪽 재계약 협상 — 덮어쓰기 + 기존 계약보다 짧아지지 않음")
ok = False
for k in range(60):
    setup(Y + 2, 30, 99, 26)
    GE.update_player(manager_relation=100, season_rating_cnt=10, season_rating_sum=80.0, renewal_negotiated_season=0)
    random.seed(1000 + k)
    st = GE.get_state()
    res = GE.negotiate_renewal()
    if res.get("success"):
        pp = GE.get_player()
        exp_y = max(C.RENEW_NEGOTIATE["extend_years"], GE.renewal_min_years({"contract_end_year": Y + 2},
                                                                             st["current_year"], st["current_week"]))
        exp_end = GE.renewal_contract_end(st["current_year"], st["current_week"], exp_y)
        ok = pp["contract_end_year"] == exp_end and pp["contract_end_year"] > Y + 2
        check("성공 시 마지막 해 = 지금부터 새로 N년, 기존 마지막 해보다 뒤",
              ok, f"{Y + 2} → {pp['contract_end_year']} (예상 {exp_end})")
        break
if not ok and not any("성공 시" in f for f in FAILS):
    check("협상 성공 사례 확보", False, "60회 모두 실패")

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)