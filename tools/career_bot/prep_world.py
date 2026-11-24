"""커리어 봇 시작 월드 만들기(원본 코드로 1회 — 두 코드가 같은 시작 상태에서 출발하게).
사용: PYTHONHASHSEED=0 python3 prep_world.py <코드폴더> <시드> <출력폴더>"""
import sys, os, shutil, random
CODE, SEED, OUT = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sys.path.insert(0, CODE); os.makedirs(OUT, exist_ok=True); os.chdir(OUT)
shutil.copy(os.path.join(CODE, "game.db"), "world.db")
import database; database.DB_PATH = os.path.abspath("world.db"); database.init_db()
random.seed(SEED)
import game_engine as ge
from constants import GAME_START_YEAR
database.reset_game_data(start_year=GAME_START_YEAR)
kor = database.get_conn().execute("SELECT name, flag FROM countries WHERE name='대한민국'").fetchone()
ge.create_player(name="Career Bot", position="ST", sub_role="", nationality=kor["name"], flag=kor["flag"], start_age=23)
c = database.get_conn()
rows = c.execute("""SELECT t.id FROM teams t JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id
    WHERE cn.name='독일' AND l.tier=1 AND COALESCE(t.classification_status,'')!='AFFILIATE'
    ORDER BY (SELECT AVG(ovr) FROM (SELECT ovr FROM ai_players WHERE team_id=t.id ORDER BY ovr DESC LIMIT 11)), t.id""").fetchall()
tid = rows[len(rows) // 2][0]
from constants import ALL_STATS
_pp = ge.get_player()
for _x in range(60, 100):     # 능력치를 고르게 _x로 두고 OVR이 90이 되는 값을 찾는다(OVR은 능력치로 다시 계산되므로)
    _d = dict(_pp, **{s_: _x for s_ in ALL_STATS})
    if ge.recalc_ovr(_d) >= 90:
        break
ge.update_player(age=24, talent_cap=94, agent_grade="B", fame=20, **{s_: _x for s_ in ALL_STATS})
ge.update_player(ovr=ge.recalc_ovr(ge.get_player()), peak_ovr=ge.recalc_ovr(ge.get_player()))
p = ge.get_player()
row = c.execute("""SELECT t.id, t.name, l.id lid, l.name lname, l.tier, cn.name country, cn.flag, cn.grade
    FROM teams t JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id WHERE t.id=?""", (tid,)).fetchone()
from constants import get_league_grade
g = get_league_grade(row["country"], row["grade"])
sal = ge._calc_offer_salary(p, g, row["tier"], 90, row["country"], row["name"], year=p.get("current_year"), team_id=tid,
                            rand_range=(1.0, 1.0))
offer = ge._build_offer(row, g, row["tier"], sal)
offer["contract_years"] = 3
ge.join_team(tid, sal, transfer_type="입단", offer=offer)
p = ge.get_player()
print("prep", SEED, "team", row["name"], "ovr", p["ovr"], "age", p["age"], "contract_end", p.get("contract_end_year"),
      "week", ge.get_state()["current_week"])
database.wait_for_pending_flush(timeout=600); database.history_drain(); database.flush_to_disk(final=True)
