"""[2026-10-30] 리그 시즌 기록 집계 결함 2건 단위 테스트(두 수정 모두 확정 — 스위치 없음).

[1] 43주 시즌 기록(_snapshot_season_ratings)의 리그 부분 = 그 시즌 실제로 뛴 리그 기준(_played_league_ctx)
    · 승격·강등으로 teams.league_id를 바꾼 뒤에 계산해도 바꾸기 전과 선수 기록(출전·골)이 같다.
    · 승강 팀 선수의 출전 수가 그 팀이 실제로 치른 리그 경기 수를 넘지 않는다.
    · 팀 선수 골 합계 = 팀 득점(기존 배분 규칙 유지).
    · 승강이 없으면 예전 경로(지금 teams.league_id 기준)와 결과가 같다.
    · 민감도: 예전 경로(실제 참가 리그 맵을 비워 지금 리그로 묶음)는 승강 뒤 계산이 승강 전과 달라진다.
[2] 시즌 마감 직전 마지막 주 리그 경기 정규 처리(_season_end_last_week)
    · 마감 직전 처리 + 마감 안전망 뒤 모든 팀의 전적·득점·실점 = 경기 결과 집계.
    · 이어서 주 마지막 날의 정규 처리를 다시 불러도 팀 전적이 바뀌지 않는다(중복 반영 없음).
    · 민감도: 예전 경로(마감 직전 처리 없이 안전망만)는 마지막 주 경기가 팀 전적에서 빠진다.

사용: python tools/test_season_stats_fixes.py   (game.db를 임시 폴더에 복사해서 쓴다 — 원본은 읽기만 한다. 약 1~2분)
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


TMP = tempfile.mkdtemp(prefix="sstat_")
shutil.copy(os.path.join(_path.ROOT, "game.db"), os.path.join(TMP, "game.db"))
os.chdir(TMP)
import constants as C
import database as DB
DB.DB_PATH = os.path.join(TMP, "game.db")
DB.flush_to_disk_async = lambda: None
random.seed(20261030)
DB.init_db()
DB.reset_game_data()
import ai_lifecycle as AL
import game_engine as GE
_fl = DB.get_conn().execute("SELECT flag FROM countries WHERE name='잉글랜드'").fetchone()[0]
GE.create_player(name="Season Stat Test", position="ST", sub_role="", nationality="잉글랜드", flag=_fl)
conn = DB.get_conn()
c = conn.cursor()
ST = GE.get_state()
Y, SEASON = ST["current_year"], ST["current_season"]
P = GE.get_player()
MY_TID = P["current_team_id"]
n_mr = c.execute("SELECT COUNT(*) FROM match_results WHERE year=?", (Y,)).fetchone()[0]
check("시즌 리그 일정 존재", n_mr > 0, f"{n_mr}경기")

# 리그 경기 결과를 채운다(결정적 난수)
rng = random.Random(7)
ids = [r[0] for r in c.execute("SELECT id FROM match_results WHERE year=? ORDER BY id", (Y,))]
c.executemany("UPDATE match_results SET home_score=?, away_score=? WHERE id=?",
              [(rng.randint(0, 4), rng.randint(0, 3), i) for i in ids])
conn.commit()


def team_agg(where=""):
    """경기 결과 집계 {team: [w, d, l, gf, ga, games]}."""
    out = {}
    for h, a, hs, as_ in c.execute(f"SELECT home_team_id, away_team_id, home_score, away_score FROM match_results "
                                   f"WHERE year=? AND home_score>=0 {where}", (Y,)):
        for t, gf, ga in ((h, hs, as_), (a, as_, hs)):
            e = out.setdefault(t, [0, 0, 0, 0, 0, 0])
            e[0 if gf > ga else 1 if gf == ga else 2] += 1
            e[3] += gf; e[4] += ga; e[5] += 1
    return out


AGG = team_agg()
TGF = {t: v[3] for t, v in AGG.items()}

# ── [1] ────────────────────────────────────────────────────────────────
print("[1] 시즌 기록 리그 기준 — 승강 반영 전후")
pairs = []
for (cid,) in c.execute("SELECT DISTINCT country_id FROM leagues ORDER BY country_id").fetchall():
    lg = {r[0]: r[1] for r in c.execute("SELECT tier, id FROM leagues WHERE country_id=?", (cid,))}
    if 1 not in lg or 2 not in lg:
        continue
    n1 = c.execute("SELECT COUNT(*) FROM teams WHERE league_id=?", (lg[1],)).fetchone()[0]
    n2 = c.execute("SELECT COUNT(*) FROM teams WHERE league_id=?", (lg[2],)).fetchone()[0]
    if n1 == n2 or n1 < 4 or n2 < 4:
        continue
    a = c.execute("SELECT id FROM teams WHERE league_id=? AND id!=? ORDER BY id DESC LIMIT 1",
                  (lg[1], MY_TID)).fetchone()[0]
    b = c.execute("SELECT id FROM teams WHERE league_id=? AND id!=? ORDER BY id LIMIT 1",
                  (lg[2], MY_TID)).fetchone()[0]
    pairs.append((a, lg[1], b, lg[2]))
    if len(pairs) == 6:
        break
check("승강 시험 쌍 확보(1부·2부 팀 수가 다른 나라)", len(pairs) >= 3, f"{len(pairs)}쌍")
MOVED = {t for a, la, b, lb in pairs for t in (a, b)}


def snap(on, move):
    """on=False면 예전 경로 재현 — 실제 참가 리그 맵을 비워 지금 teams.league_id로 묶는다."""
    cap = {}
    orig = DB.history_enqueue
    orig_map = GE._team_league_id_map_for_year
    if not on:
        GE._team_league_id_map_for_year = lambda c_, y_: {}
    DB.history_enqueue = lambda kind, rows: cap.setdefault(kind, []).extend(rows)
    try:
        if move:   # 승격·강등 반영(다음 시즌 리그로 이동)
            c.executemany("UPDATE teams SET league_id=? WHERE id=?",
                          [(lb, a) for a, la, b, lb in pairs] + [(la, b) for a, la, b, lb in pairs])
        AL._snapshot_season_ratings(c, Y, team_goals_for=TGF, include_league=True, competitions=(),
                                    pos_role_by_pid={})
    finally:
        if move:
            c.executemany("UPDATE teams SET league_id=? WHERE id=?",
                          [(la, a) for a, la, b, lb in pairs] + [(lb, b) for a, la, b, lb in pairs])
        DB.history_enqueue = orig
        GE._team_league_id_map_for_year = orig_map
    return {r[0]: (r[2], r[3], r[4]) for r in cap.get("ai_player_season_stats", [])}


on_mv, on_st = snap(True, True), snap(True, False)
off_mv, off_st = snap(False, True), snap(False, False)
check("승강 뒤 계산 = 승강 전 계산(전 선수 출전·골 동일)", on_mv == on_st and len(on_mv) > 1000,
      f"{len(on_mv)}명, 다른 선수 {sum(1 for k in on_st if on_mv.get(k) != on_st[k])}")
over = [(p, v[1], AGG[v[0]][5]) for p, v in on_mv.items() if v[0] in MOVED and v[1] > AGG[v[0]][5]]
check("승강 팀 선수 출전 수 ≤ 팀 리그 경기 수", not over and any(v[0] in MOVED for v in on_mv.values()),
      str(over[:5]))
gsum = {}
for p, (t, m, g) in on_mv.items():
    gsum[t] = gsum.get(t, 0) + (g or 0)
bad = [t for t in MOVED if gsum.get(t) != TGF.get(t)]
check("승강 팀 선수 골 합계 = 팀 득점", not bad, str([(t, gsum.get(t), TGF.get(t)) for t in bad]))
check("승강 없음: 예전 경로와 결과 동일", on_st == off_st, f"다른 선수 {sum(1 for k in off_st if on_st.get(k) != off_st[k])}")
diff_off = sum(1 for p, v in off_mv.items() if v[0] in MOVED and off_st.get(p) != v)
over_off = sum(1 for p, v in off_mv.items() if v[0] in MOVED and v[1] > AGG[v[0]][5])
check("민감도: 예전 경로는 승강 뒤 계산이 승강 전과 다르다", diff_off > 0, f"승강 팀 선수 {diff_off}명 다름, 경기 수 초과 {over_off}명")

# ── [2] ────────────────────────────────────────────────────────────────
print("[2] 시즌 마감 직전 마지막 주 리그 경기")
LASTW = c.execute("SELECT MAX(week) FROM match_results WHERE year=?", (Y,)).fetchone()[0]


def prepare():
    """마지막 주 AI 경기를 미완료(-1)로 되돌리고(내 팀 경기는 이미 치른 것으로 둔다) 팀 전적을 나머지 경기로 다시 맞춘다."""
    c.execute("""UPDATE match_results SET home_score=-1, away_score=-1
                 WHERE year=? AND week=? AND home_team_id!=? AND away_team_id!=?""", (Y, LASTW, MY_TID, MY_TID))
    c.execute("UPDATE teams SET wins=0, draws=0, losses=0, goals_for=0, goals_against=0")
    c.executemany("UPDATE teams SET wins=?, draws=?, losses=?, goals_for=?, goals_against=? WHERE id=?",
                  [(v[0], v[1], v[2], v[3], v[4], t) for t, v in team_agg().items()])
    conn.commit()


def teams_now():
    return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in c.execute(
        "SELECT id, wins, draws, losses, goals_for, goals_against FROM teams")}


def mismatch():
    agg, tn = team_agg(), teams_now()
    return [t for t, v in agg.items() if tn.get(t) != tuple(v[:5])]


def run_close(on):
    """on=False면 예전 경로 재현 — 마감 직전 처리 없이 마감 안전망만."""
    prepare()
    stale0 = c.execute("SELECT COUNT(*) FROM match_results WHERE year=? AND home_score=-1", (Y,)).fetchone()[0]
    if on:
        GE._season_end_last_week(LASTW, GE.get_player(), SEASON)
    stale1 = c.execute("SELECT COUNT(*) FROM match_results WHERE year=? AND home_score=-1", (Y,)).fetchone()[0]
    GE._finish_incomplete_matches_for_season(SEASON)
    left = c.execute("SELECT COUNT(*) FROM match_results WHERE year=? AND home_score=-1", (Y,)).fetchone()[0]
    return on, stale0, stale1, left, mismatch()


did, s0, s1, left, mis = run_close(False)
check("민감도: 예전 경로는 마감 안전망이 채운 마지막 주 경기가 팀 전적에서 빠진다",
      not did and s0 > 0 and s1 == s0 and left == 0 and len(mis) > 0,
      f"미완료 {s0}경기, 불일치 {len(mis)}팀")
did, s0, s1, left, mis = run_close(True)
check("마감 직전 정규 처리가 마지막 주 미완료 경기를 모두 처리", did and s0 > 0 and s1 == 0 and left == 0,
      f"처리 전 {s0} → 뒤 {s1}")
check("모든 팀 전적·득점·실점 = 경기 결과 집계", not mis, f"불일치 {len(mis)}팀 {mis[:5]}")
before = teams_now()
GE._sim_all_ai_matches(LASTW, P.get("current_league_id", 0), SEASON)   # 주 마지막 날 정규 호출 재현
check("주 마지막 날 정규 처리를 다시 불러도 팀 전적 변화 없음(중복 반영 없음)", teams_now() == before)

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)