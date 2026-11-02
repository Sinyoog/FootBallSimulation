import sys, os, shutil, random, json, time, statistics as st, collections, atexit
root, seed, nseas, outdir = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
sys.path.insert(0, root)
os.makedirs(outdir, exist_ok=True); os.chdir(outdir)
for suf in (".db", ".history.db"):
    shutil.copy(f"{root}/r{seed}{suf}", f"{outdir}/w{suf}")
import database
database.DB_PATH = f"{outdir}/w.db"
random.seed(seed)
database.init_db()
from constants import COUNTRY_CONTINENT
# --- 국적 추첨 계측(난수 소비 없음) ---
PICK = collections.Counter()
_orig_pick = database._pick_nationality
def _wrapped(team_country, team_continent, grade, pos, is_star, foreign_count, quota, rank_frac=None, slot_ovr=None, youth=False, youth_top=False):
    full = quota is not None and foreign_count >= quota
    res = _orig_pick(team_country, team_continent, grade, pos, is_star, foreign_count, quota, rank_frac=rank_frac, slot_ovr=slot_ovr, youth=youth, youth_top=youth_top)
    nat = res[0]
    cat = "자국" if nat == team_country else ("유럽타국" if COUNTRY_CONTINENT.get(nat) == "유럽" else "유럽외")
    caller = sys._getframe(1).f_code.co_name
    scope = "빅5급1부" if (grade in ("SS", "S") and youth_top) else "기타"
    hi = (slot_ovr or 0) >= 88
    PICK[(caller, scope, "정원참" if full else "자리있음", "스타" if is_star else "일반", "88+" if hi else "88미만", cat)] += 1
    return res
database._pick_nationality = _wrapped
atexit.register(lambda: json.dump([list(k)+[v] for k, v in PICK.items()], open(f"{outdir}/picks.json", "w"), ensure_ascii=False))
import game_engine as ge, intl_engine
c = database.get_conn()
if not ge.get_player():
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")
NAT = ["잉글랜드","스페인","독일","프랑스","네덜란드","브라질","아르헨티나","이탈리아","포르투갈","벨기에","크로아티아","우루과이","콜롬비아","모로코","세네갈","미국","멕시코","일본","대한민국"]
B5 = ("잉글랜드","스페인","이탈리아","독일","프랑스")
def snap(tag):
    out = {"tag": tag}
    avg = {t: a for t, a in c.execute("select team_id,avg(ovr) from ai_players group by team_id")}
    lg = {}
    for lid, tier, cn, ln in c.execute("select l.id,l.tier,co.name,l.name from leagues l join countries co on co.id=l.country_id where co.name!='군대'").fetchall():
        o = sorted([avg[r[0]] for r in c.execute("select id from teams where league_id=?", (lid,)) if r[0] in avg], reverse=True)
        if not o: continue
        k = max(1, round(len(o)*.25))
        lg[lid] = {"name": f"{ln}({cn} {tier}부)", "idx": st.mean(o)*.5+st.mean(o[:k])*.3+st.mean(o[-k:])*.2, "avg": st.mean(o)}
    for i, l in enumerate(sorted(lg, key=lambda l: -lg[l]["idx"]), 1): lg[l]["ovr_rank"] = i
    out["lg"] = {str(k): v for k, v in lg.items()}
    rows = c.execute("""select p.ovr,p.age,p.true_nationality,p.nationality,co.name,co.continent,l.tier,p.created_year,p.quota_local_country,t.id
        from ai_players p join teams t on t.id=p.team_id join leagues l on l.id=t.league_id join countries co on co.id=l.country_id where co.name!='군대'""").fetchall()
    nat = {}
    by = collections.defaultdict(list)
    for r in rows: by[r[2]].append(r)
    for n in NAT:
        mine = by.get(n, [])
        o = sorted([r[0] for r in mine], reverse=True)
        nat[n] = {"t11": st.mean(o[:11]) if len(o)>=11 else None, "t23": st.mean(o[:23]) if len(o)>=23 else None,
                  "n90": sum(x>=90 for x in o), "n95": sum(x>=95 for x in o),
                  "big5": sum(1 for r in mine if r[6]==1 and r[4] in B5), "eu1": sum(1 for r in mine if r[6]==1 and r[5]=="유럽" and r[4]!=n)}
    out["nat"] = nat
    b5 = [r for r in rows if r[6]==1 and r[4] in B5]
    new85 = [r for r in b5 if r[0]>=85 and r[7] and r[7]>2000]
    out["new85_dom"] = (sum(1 for r in new85 if r[2]==r[4])/len(new85)*100) if new85 else None
    out["new85_n"] = len(new85)
    f = [r[0] for r in b5 if r[1]>=26 and r[3]!=r[4]]; d = [r[0] for r in b5 if r[1]>=26 and r[3]==r[4]]
    out["fgap"] = st.mean(f)-st.mean(d) if f and d else None
    # quota compliance
    from database import get_foreign_quota_range, is_roster_foreign
    cnt = collections.Counter(); meta = {}
    for r in rows:
        meta[r[9]] = (r[4], r[5], r[6])
        if is_roster_foreign(r[3], r[4]): cnt[r[9]] += 1
    over = 0
    for tid, (cn, cont, tier) in meta.items():
        lo, hi = get_foreign_quota_range(cn, cont, tier=tier)
        if hi is not None and cnt[tid] > hi: over += 1
    out["quota_over_teams"] = over
    return out
hist = [snap("start")]
json.dump(hist, open(f"{outdir}/hist.json", "w"), ensure_ascii=False)
for i in range(nseas):
    t = time.time()
    st0 = ge.get_state(); day0 = st0["current_day"] if i == 0 else 1
    sched = [(d, "휴식", {}) for d in range(day0, 365)]
    rem = list(sched)
    for _ in range(20):
        ge.advance_days(rem)
        pend = intl_engine.get_pending_choice()
        if not pend: break
        for opt in pend.get("options", []): intl_engine.decline_national_team(opt["tournament_id"])
        cd = ge.get_state().get("current_day"); rem = [x for x in sched if x[0] >= cd]
        if not rem: break
    s = snap(f"after{i+1}"); s["sec"] = round(time.time()-t); hist.append(s)
    print(json.dumps({"seed": seed, "i": i+1, "sec": s["sec"]}), flush=True)
    json.dump(hist, open(f"{outdir}/hist.json", "w"), ensure_ascii=False)
print("DONE")