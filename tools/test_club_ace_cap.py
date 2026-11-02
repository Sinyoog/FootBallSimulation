"""[2026-10-30] 클럽 대항전 득점 배분 상한(game_engine._club_comp_ace_share_cap — 채택·확정) 단위 테스트.
"예전 경로"(OFF)는 클럽 대항전 대상 목록(_CLUB_ACE_COMPS)을 비워 재현한다 — 그러면 모든 대회가 기존 _ace_share_cap을 쓴다.

[A] 합성 데이터
  · 곡선: 8경기 이하 42% / 9경기 41.7% / 12경기 40.9% / 38경기 이상 34%.
  · 클럽 대항전('cl'·'sc'·'cwc')은 ON에서 1위 지분이 상한 이하(3% 상한 무시 확률만큼 예외), 8경기 이하도 적용.
  · 게임 난수 소비 횟수가 OFF와 같다(9경기+·8경기 이하 모두) — 그래서 그 뒤 경기 결과가 안 바뀐다.
  · 국내컵·하부 리그컵·국내 슈퍼컵·국가대표(comp 없음)는 ON에서도 OFF와 결과가 같다.
[B] 1시즌 이상 치른 세이브(인자)에서 그 시즌 대회 기록 배분 전체(_snapshot_season_ratings)를 예전 경로/지금으로 다시 계산
  · 예전 경로/지금 같은 시드: 난수 상태 동일, 클럽 대항전 외 대회 행 동일.
  · ON 클럽 대항전(cl·sc·cwc): 팀별 선수 골 합 = 팀 득점, 팀별 도움 합 ≤ 팀 골, 1위 지분이 상한(+반올림 1골) 이하인 팀 비율.
  · 민감도: OFF에는 새 상한을 넘는 팀이 있다.

사용: python tools/test_club_ace_cap.py [1시즌 이상 치른 세이브 폴더 [시즌 연도]]
  (game.db를 임시 폴더에 복사해서 쓴다 — 원본은 읽기만 한다. [B]는 세이브 폴더를 줄 때만, 약 1분)
"""
import _path  # noqa: F401
import collections
import math
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


TMP = tempfile.mkdtemp(prefix="cace_")
shutil.copy(os.path.join(_path.ROOT, "game.db"), os.path.join(TMP, "game.db"))
os.chdir(TMP)
import database as DB
DB.DB_PATH = os.path.join(TMP, "game.db")
DB.flush_to_disk_async = lambda: None
import game_engine as GE

# ── [A] 합성 ─────────────────────────────────────────────────────────────
print("[A] 합성 데이터")
cv = {m: round(GE._club_comp_ace_share_cap(m) * 100, 1) for m in (1, 8, 9, 12, 38, 45)}
check("곡선 값(8↓ 42 / 9 41.7 / 12 40.9 / 38↑ 34)", cv == {1: 42.0, 8: 42.0, 9: 41.7, 12: 40.9, 38: 34.0, 45: 34.0}, str(cv))
CLUB = GE._CLUB_ACE_COMPS
check("클럽 대항전 대상 = cl·sc·cwc", CLUB == ("cl", "sc", "cwc"), str(CLUB))


def groups(n_team, m, seed):
    rng = random.Random(seed)
    rows = []
    for t in range(n_team):
        for i in range(18):
            w = rng.random() ** 3 * (12 if i == 0 else 3)
            rows.append({"team": t, "goals": w, "assists": rng.random() * 2, "matches": m})
    return rows


def run(on, comp, m, seed=5):
    GE._CLUB_ACE_COMPS = CLUB if on else ()
    rows = groups(400, m, 1)
    random.seed(seed)
    GE._apply_ace_concentration(rows, lambda d: d["team"], comp=comp, year=2001)
    st = random.getstate()
    GE._CLUB_ACE_COMPS = CLUB
    by = collections.defaultdict(list)
    for r in rows:
        by[r["team"]].append(r["goals"])
    share = [max(v) / sum(v) for v in by.values() if sum(v) > 0]
    return rows, st, share


for m in (12, 6):
    off_rows, off_st, off_sh = run(False, "cl", m)
    on_rows, on_st, on_sh = run(True, "cl", m)
    capv = GE._club_comp_ace_share_cap(m)
    over_on = sum(s > capv + 1e-9 for s in on_sh) / len(on_sh)
    over_off = sum(s > capv + 1e-9 for s in off_sh) / len(off_sh)
    check(f"{m}경기: 게임 난수 소비가 OFF와 같다", on_st == off_st)
    check(f"{m}경기: ON 상한 초과 팀 비율 ≈ 상한 무시 확률(≤6%), OFF는 더 많다",
          over_on <= 0.06 and over_off > over_on, f"ON {over_on * 100:.1f}% / OFF {over_off * 100:.1f}% (상한 {capv * 100:.1f}%)")
for comp in ("cup", "lower_cup", "dsc", None):
    a = run(False, comp, 12)
    b = run(True, comp, 12)
    check(f"'{comp}' 대회는 ON에서도 OFF와 같다", a[0] == b[0] and a[1] == b[1])

# ── [B] 실제로 1시즌을 치른 세이브 ─────────────────────────────────────────
print("[B] 1시즌을 치른 세이브 — 그 시즌 대회 기록 배분 예전 경로/지금")
SAVE = sys.argv[1] if len(sys.argv) > 1 else None
if not SAVE:
    print("  (건너뜀) 1시즌 이상 치른 세이브 폴더(game.db·game.history.db)를 인자로 주면 검사한다 — 새 월드엔 대항전 일정이 없다")
else:
    for f in ("game.db", "game.history.db", "game.history.db-wal", "game.history.db-shm"):
        if os.path.exists(os.path.join(SAVE, f)):
            shutil.copy(os.path.join(SAVE, f), os.path.join(TMP, f))
    DB.init_db()
    import ai_lifecycle as AL
    conn = DB.get_conn()
    c = conn.cursor()
    Y = int(sys.argv[2]) if len(sys.argv) > 2 else GE.get_state()["current_year"] - 1   # 연도 지정 가능(클럽 월드컵 열린 해 등)
    print(f"  대상 시즌 {Y}")
    COMPS = ("cup", "cl", "sc", "cwc", "lower_cup", "dsc")
    CUR = {"comp": None}
    REC = {}
    _orig_ace, _orig_bud = GE._apply_ace_concentration, GE._apply_team_goal_budget

    def _w_ace(rows, key_fn, comp=None, year=None):
        CUR["comp"] = comp
        return _orig_ace(rows, key_fn, comp=comp, year=year)

    def _w_bud(rows, key_fn, tgf, allow_zero=False):
        out = _orig_bud(rows, key_fn, tgf, allow_zero=allow_zero)
        if CUR["comp"] is not None:
            REC[CUR["comp"]] = ([(key_fn(r), r["goals"], r["assists"], r["matches"]) for r in rows], dict(tgf or {}))
        CUR["comp"] = None
        return out

    def snap(on):
        GE._CLUB_ACE_COMPS = CLUB if on else ()
        REC.clear()
        cap = {}
        orig = DB.history_enqueue
        DB.history_enqueue = lambda kind, rows: cap.setdefault(kind, []).extend(rows)
        GE._apply_ace_concentration, GE._apply_team_goal_budget = _w_ace, _w_bud
        random.seed(99)
        try:
            AL._snapshot_season_ratings(c, Y, team_goals_for=None, include_league=False, competitions=COMPS,
                                        pos_role_by_pid={})
        finally:
            DB.history_enqueue = orig
            GE._apply_ace_concentration, GE._apply_team_goal_budget = _orig_ace, _orig_bud
            GE._CLUB_ACE_COMPS = CLUB
        return sorted(cap.get("ai_player_season_stats_by_comp", [])), random.getstate(), dict(REC)

    off_rows, off_st, off_rec = snap(False)
    on_rows, on_st, on_rec = snap(True)
    check("대회 기록 존재(클럽 대항전·국내컵)", "cl" in on_rec and "cup" in on_rec, str(sorted(on_rec)))
    check("배분 뒤 게임 난수 상태 OFF = ON", off_st == on_st)
    for comp in COMPS:
        if comp not in CLUB:
            a = [r for r in off_rows if r[2] == comp]; b = [r for r in on_rows if r[2] == comp]
            check(f"'{comp}' 행 OFF = ON", a == b, f"{len(b)}행")

    def per_team(rec):
        rows, tgf = rec
        t = collections.defaultdict(lambda: [0, 0, 0, 0])
        for k, g_, a_, m_ in rows:
            e = t[k]; e[0] += g_; e[1] += a_; e[2] = max(e[2], g_); e[3] = max(e[3], m_ or 0)
        return t, tgf

    for comp in CLUB:
        if comp not in on_rec:
            continue
        (pt_on, tgf), (pt_off, _t) = per_team(on_rec[comp]), per_team(off_rec[comp])
        bad = [k for k, v in pt_on.items() if k in tgf and v[0] != tgf[k]]
        check(f"'{comp}' ON 팀별 선수 골 합 = 팀 득점", not bad, f"{len(pt_on)}팀, 불일치 {bad[:5]}")
        bad = [k for k, v in pt_on.items() if v[1] > v[0]]
        check(f"'{comp}' ON 팀별 도움 합 ≤ 팀 골", not bad, str(bad[:5]))

        def over(pt):
            tot = o = 0
            for k, (gs, a_, top, m_) in pt.items():
                if gs >= 8:
                    tot += 1
                    o += top > GE._club_comp_ace_share_cap(m_) * gs + 1
            return o, tot
        (o_on, t_on), (o_off, t_off) = over(pt_on), over(pt_off)
        if t_on:
            check(f"'{comp}' ON 상한(+반올림 1골) 초과 팀 ≤ 6%", o_on / t_on <= 0.06, f"{o_on}/{t_on}")
            check(f"'{comp}' 민감도: OFF는 새 상한 초과 팀이 더 많다", o_off > o_on, f"OFF {o_off}/{t_off}")
        a_on = sum(v[1] for v in pt_on.values()); a_off = sum(v[1] for v in pt_off.values())
        g_on = sum(v[0] for v in pt_on.values())
        print(f"  참고 '{comp}': 팀 골 합 {g_on}, 팀 도움 합 OFF {a_off} → ON {a_on}")

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)