# -*- coding: utf-8 -*-
"""tools/plan_strength_longrun.py — ⑦ 포메이션 기준 선발 11명 전력 장기 검증 (2026-10 신설)

같은 시작(새 게임, 시드 고정)에서 기준선(PLAN_STRENGTH_ENABLED=False)과 ⑦(True)을 N시즌씩
돌리고, 해마다 감독 시장 직전에 그 시즌 리그 결과를 per_year.jsonl에 남긴다.
  · 시즌 시작 스냅샷: 팀별 OVR 상위 11명 평균(중립 지표), 최적 배치 선발 11명 전력,
    포메이션, 명문 등급
  · 시즌 끝: 리그 승점/득실, 리그별 득점, 5점+ 약팀 승(이변), 승강 기록
보고·판정은 tools/plan_strength_report.py.

사용: python tools/plan_strength_longrun.py <runs_dir> <seed> <base|new> <years>
      → <runs_dir>/e7_<base|new>_<seed>/per_year.jsonl (10시즌 약 25분, 2개 병렬 기준)
"""
import _path  # noqa: F401
import json, os, random, shutil, statistics as st, sys, time

ROOT = _path.ROOT
SEED, MODE, YEARS = int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
OUT = os.path.abspath(os.path.join(sys.argv[1], f"e7_{MODE}_{SEED}"))
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
shutil.copy(os.path.join(ROOT, "game.db"), os.path.join(OUT, "game.db"))
sys.path.insert(0, ROOT); os.chdir(OUT)
import database
database.DB_PATH = os.path.join(OUT, "game.db")
database.flush_to_disk_async = lambda: None
import constants as C
C.PLAN_STRENGTH_ENABLED = (MODE == "new")
random.seed(SEED)
database.init_db(); database.reset_game_data()
import game_engine as ge, intl_engine, ai_lifecycle as al, team_plan as tp
from constants import FORMATION_STYLE, get_country_league_grade
from data.prestige_clubs import prestige_level
if not ge.get_player():
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")

LOG = open(os.path.join(OUT, "per_year.jsonl"), "a")
SNAP = {}          # 시즌 시작 스냅샷 {tid: {...}}


def season_start_snapshot():
    conn = database.get_conn()
    rows = conn.execute(
        """SELECT t.id, t.name, t.league_id, l.tier, cn.name AS country, t.formation,
                  COALESCE(t.club_strength,0) AS cs
           FROM teams t JOIN leagues l ON l.id=t.league_id
           JOIN countries cn ON cn.id=l.country_id""").fetchall()
    ro = {}
    for r in conn.execute("SELECT * FROM ai_players WHERE team_id IS NOT NULL").fetchall():
        d = dict(r); ro.setdefault(d["team_id"], []).append(d)
    out = {}
    for r in rows:
        pl = ro.get(r["id"]) or []
        if len(pl) < 11:
            continue
        f = r["formation"] or "4-4-2"
        t11 = st.mean(sorted((p["ovr"] for p in pl), reverse=True)[:11])
        xi = tp.lineup_strength(tp.optimal_lineup(pl, f), f)
        out[r["id"]] = {"name": r["name"], "lg": r["league_id"], "tier": r["tier"],
                        "country": r["country"], "grade": get_country_league_grade(r["country"]),
                        "form": f, "style": FORMATION_STYLE.get(f, 0.0), "t11": t11, "xi": xi,
                        "pres": prestige_level(r["country"], r["name"]) or 0, "cs": r["cs"]}
    return out


_orig_turnover = al._manager_turnover


def _wrapped_turnover(c, year, season=None):
    try:
        collect(c, year, season)
    except Exception as e:
        print(f"[EXP7] 수집 실패 {year}: {e}", flush=True)
    return _orig_turnover(c, year, season)


al._manager_turnover = _wrapped_turnover


def collect(c, year, season):
    if season is None:
        row = c.execute("SELECT MAX(season) AS s FROM match_results WHERE year=?", (year,)).fetchone()
        season = row["s"] if row else None
    rows = c.execute("""SELECT league_id, home_team_id AS h, away_team_id AS a, home_score AS hs,
                               away_score AS as_ FROM match_results
                        WHERE season=? AND home_score>=0""", (season,)).fetchall()
    team = {}
    lg_goals = {}
    up = {"n5": 0, "w5": 0, "d5": 0, "n10": 0, "w10": 0, "n5_t1": 0, "w5_t1": 0}
    for r in rows:
        h, a, hs, as_ = r["h"], r["a"], r["hs"], r["as_"]
        g = lg_goals.setdefault(r["league_id"], [0, 0]); g[0] += hs + as_; g[1] += 1
        for tid, gf, ga in ((h, hs, as_), (a, as_, hs)):
            s = team.setdefault(tid, [0, 0, 0, 0]); s[1] += 1; s[2] += gf; s[3] += ga
            s[0] += 3 if gf > ga else (1 if gf == ga else 0)
        sh, sa = SNAP.get(h), SNAP.get(a)
        if sh and sa:
            d = sh["t11"] - sa["t11"]
            if abs(d) >= 5:
                weak_won = (hs < as_) if d > 0 else (hs > as_)
                up["n5"] += 1; up["w5"] += weak_won; up["d5"] += (hs == as_)
                if sh["tier"] == 1:
                    up["n5_t1"] += 1; up["w5_t1"] += weak_won
            if abs(d) >= 10:
                weak_won = (hs < as_) if d > 0 else (hs > as_)
                up["n10"] += 1; up["w10"] += weak_won
    promo = [(r["team_name"], r["from_tier"], r["to_tier"], r["league_name"]) for r in c.execute(
        "SELECT team_name, from_tier, to_tier, league_name FROM promotion_log WHERE year=?",
        (year,)).fetchall()]
    teams_out = {}
    for tid, s in SNAP.items():
        res = team.get(tid)
        if not res:
            continue
        teams_out[tid] = [s["lg"], s["tier"], s["country"], s["grade"], s["form"], s["style"],
                          round(s["t11"], 3), round(s["xi"] or 0, 3), s["pres"], s["name"],
                          res[0], res[1], res[2], res[3]]
    LOG.write(json.dumps({"year": year, "season": season, "teams": teams_out,
                          "lg_goals": lg_goals, "upsets": up, "promo": promo,
                          "t": round(time.time() - T_YEAR, 1)}, ensure_ascii=False) + "\n")
    LOG.flush()


DAYS_PER_YEAR, CHUNK = 364, 30


def chunk(start, n):
    o, d = [], start
    for _ in range(n):
        o.append((d, "휴식", {})); d = d + 1 if d < DAYS_PER_YEAR else 1
    return o


T0 = time.time()
done = 0
while done < YEARS:
    SNAP.clear(); SNAP.update(season_start_snapshot())
    T_YEAR = time.time()
    yb = ge.get_state().get("current_year")
    while ge.get_state().get("current_year") == yb:
        _cd = ge.get_state().get("current_day") or 1
        ge.advance_days(chunk(_cd, max(1, min(CHUNK, DAYS_PER_YEAR + 1 - _cd))))
        pending = intl_engine.get_pending_choice()
        while pending:
            for opt in pending.get("options", []):
                intl_engine.decline_national_team(opt["tournament_id"])
            _cd = ge.get_state().get("current_day") or 1
            ge.advance_days(chunk(_cd, max(1, min(CHUNK, DAYS_PER_YEAR + 1 - _cd))))
            pending = intl_engine.get_pending_choice()
    done += 1
    print(f"[EXP7 {MODE} s{SEED}] {yb}→{ge.get_state().get('current_year')} "
          f"{time.time() - T_YEAR:.0f}s (누적 {time.time() - T0:.0f}s)", flush=True)
print("[EXP7] 끝", flush=True)