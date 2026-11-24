"""재계약 동등성 시험: 같은 강제 상태(재계약 제안 있음)에서 ① 메인 화면 코드(center_panel._show_renew_dialog, 가짜 PyQt로
'계약 수락' 클릭) ② 봇(career_bot.bot_renew)을 각각 돌려 결과 상태와 난수 소비를 비교한다. 사용: renew_eq.py <ui|bot> <early 0|1> <출력json> <코드폴더> <시작월드폴더>"""
import sys, os, json, shutil, random, ast, textwrap
MODE, EARLY, OUTJ = sys.argv[1], int(sys.argv[2]), sys.argv[3]
CODE, WORLD = os.path.abspath(sys.argv[4]), os.path.abspath(sys.argv[5])
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, CODE)
OUTJ = os.path.abspath(OUTJ)
wd = os.path.join(HERE, f"wd_renew_{MODE}_{EARLY}"); os.makedirs(wd, exist_ok=True); os.chdir(wd)
for f in os.listdir(WORLD):
    if f.startswith("world."): shutil.copy(os.path.join(WORLD, f), f.replace("world.", "t."))
import database; database.DB_PATH = os.path.abspath("t.db"); database.init_db()
import game_engine as ge
st = ge.get_state()
p = ge.get_player()
# 강제 상태: 재계약 제안(정기 = 계약 만료 시점 / 조기 = 계약 남음)
ge.update_player(_contract_renew_offer=int(p["salary"] * 1.1), _contract_renew_years=3, _contract_renew_early=EARLY,
                 contract_end_year=(st["current_year"] - 1) if not EARLY else st["current_year"] + 1)
p = ge.get_player()
c = database.get_conn()
log0 = len(ge._log_buffer)
random.seed(777)
if MODE == "ui":
    import pyqt_stub; pyqt_stub.install()
    import ui.center_panel as cp
    cp.show_toast = lambda *a, **k: None
    class _F: main_win = None
    cp.CenterPanel._show_renew_dialog(_F(), p)
else:
    src = open(os.path.join(HERE, "career_bot.py"), encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "bot_renew")
    ns = {"ge": ge, "database": database, "LOG": {"renewals": []}}
    exec(textwrap.dedent(ast.get_source_segment(src, fn)), ns)
    ns["bot_renew"](p)
after = ge.get_player()
logs = list(ge._log_buffer[log0:])
meta = sorted(tuple(r) for r in c.execute("SELECT key, value FROM meta").fetchall())
out = {"player": {k: after[k] for k in sorted(after)}, "logs": [repr(x) for x in logs], "meta": [repr(x) for x in meta],
       "next_random": random.random()}
json.dump(out, open(OUTJ, "w"), ensure_ascii=False, default=str)
print(MODE, EARLY, "contract", after["contract_years"], after["contract_end_year"], "salary", after["salary"], "renew_offer", after["_contract_renew_offer"], "logs", len(logs), "next", out["next_random"])
