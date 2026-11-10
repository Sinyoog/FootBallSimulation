# -*- coding: utf-8 -*-
"""[2026-11] 발롱도르 새 채점 연결(game_engine._save_ballon_dor_top30) 단위 테스트.

검증
  A. 저장 함수 연결(DB 없음 — 시드 106 2001년 실제 후보 fixture)
     A1 새 채점(_compute_ballon_v2)은 감쇠 전 원본 후보를 받는다
     A2 저장되는 발롱도르 행 = _ballon_v2_entries(새 Top30) 그대로, 전부 형식 표시 ballon_v2
     A3 FIFA 올해의 선수·대륙별 올해의 선수·FIFA 베스트 11로 넘어가는 목록 = 예전 채점 목록과 완전히 같다
        (예전 저장 함수와 같은 순서로 따로 계산한 목록과 필드·순서 비교)
     A4 새 채점이 후보를 건드려도(가상 — 입력을 망가뜨리는 대역) 기존 점수 목록은 그대로
  B. 저장 칸 대응 — 모듈 결과 → 저장 행: 총점·능력·생산성·출전·팀 성적·국제대회·트로피 합·리그 골·도움
  C. (선택, --db 세이브) 실제 세이브 한 해: 새 채점이 후보 리스트를 바꾸지 않음 · 두 번 계산해도 같음 ·
     후보 순서를 섞어도 Top30·점수 같음
사용: python tools/test_ballon_wiring.py [--db 경로/game.db --year 2005]"""
import _path  # noqa: F401
import argparse
import contextlib
import copy
import gzip
import io
import json
import os
import random
import sys

import game_engine as GE
import ballon_award as B

FAILS = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def legacy_scored(cands):
    """예전 _save_ballon_dor_top30의 채점 순서 그대로(감쇠 → 채점 → 정렬) — 비교 기준."""
    cands = copy.deepcopy(cands)
    cands = GE._apply_ballon_team_trophy_decay(cands)
    cands = GE._apply_ballon_intl_bonus_decay(cands)
    out = [{**cd, **GE._score_ballon_candidate(cd)} for cd in cands]
    out.sort(key=lambda x: (-x["total_score"], -(x.get("rating") or 0),
                            -((x.get("goals") or 0) + (x.get("assists") or 0)), x["player_id"]))
    return out


def synth_entries(n=60, seed=7):
    rng = random.Random(seed)
    pos = ["ST", "LW", "RW", "CAM", "CM", "CDM", "CB", "LB", "RB", "GK"]
    out = []
    for i in range(n):
        p = pos[i % len(pos)]
        e = {"player_id": 1000 + i, "team_id": 10 + (i % 25), "position": p,
             "goals": rng.randint(0, 35), "assists": rng.randint(0, 18), "matches": rng.randint(27, 38),
             "full_season": 38, "goal_env": 1.0, "ovr": rng.randint(84, 96), "team_gc": rng.uniform(0.7, 1.6),
             "share": 1.0, "cup_v": rng.random(), "cont_v": rng.random(), "intl_v": rng.random(),
             "league_level": rng.uniform(0.7, 1.0), "league_power": 1.0, "league_rank": rng.randint(1, 20),
             "league_n": 20, "league_relegated": 3, "league_exp": rng.randint(1, 20),
             "team_attack": rng.uniform(0.7, 1.7), "nationality": "가", "comps": []}
        if p == "GK":
            e.update(gk_save=rng.uniform(0.72, 0.82), saves=rng.randint(60, 140), goals_conceded=rng.randint(20, 50))
        out.append(e)
    return out


# ── A. 저장 함수 연결 ──────────────────────────────────────────────
print("A. 저장 함수 연결 (시드 106 2001년 실제 후보)")
fx_path = os.path.join(_path.ROOT, "tools", "fixtures", "ballon_tie_seed106_2001.json.gz")
with gzip.open(fx_path, "rt", encoding="utf-8") as f:
    fx = json.load(f)
gw, gc = fx["grade_weight"], fx["gc_per_match"]
real = fx["candidates"]
fake_top = B.top30(B.score_season(synth_entries()))

_orig = {k: getattr(GE, k) for k in ("_get_team_trophy_grade_weight", "_get_team_gc_per_match",
                                     "_compute_ballon_v2", "_save_individual_award_rows",
                                     "_save_world_poty_awards")}
cap = {}


def _stub_v2(c, year, candidates, _mut=False):
    cap["v2_in"] = copy.deepcopy(candidates)
    if _mut:                                   # A4: 입력을 망가뜨리는 대역
        for cd in candidates:
            cd["v2_touched"] = True
    return list(fake_top), list(fake_top)


try:
    GE._get_team_trophy_grade_weight = lambda tid: gw.get(str(tid), 1.0)
    GE._get_team_gc_per_match = lambda year, tid: gc.get(str(tid))
    GE._save_individual_award_rows = lambda c, y, cat, comp, entries, **kw: cap.setdefault("rows", []).append(
        (y, cat, comp, copy.deepcopy(entries)))
    GE._save_world_poty_awards = lambda c, y, scored: cap.__setitem__("poty", copy.deepcopy(scored))
    GE._compute_ballon_v2 = _stub_v2

    expect = legacy_scored(real)
    cands = copy.deepcopy(real)
    GE._save_ballon_dor_top30(None, 2001, cands)
    check("A1 새 채점은 감쇠 전 원본 후보를 받는다", cap["v2_in"] == real)
    rows = cap.get("rows") or []
    check("A2 발롱도르 저장 호출 1번(world, 대회 없음)", len(rows) == 1 and rows[0][1:3] == ("world", None))
    ent = rows[0][3] if rows else []
    check("A2 저장 행 = _ballon_v2_entries(새 Top30)", ent == GE._ballon_v2_entries(fake_top))
    check("A2 전부 발롱도르 · 순위 1..N · 형식 표시 ballon_v2",
          [e[0] for e in ent] == ["발롱도르"] * len(ent) and [e[1] for e in ent] == list(range(1, len(ent) + 1))
          and all(e[2]["xi_formation"] == GE.BALLON_ROW_FORMAT for e in ent), f"{len(ent)}행")
    poty = cap.get("poty")
    check("A3 FIFA·대륙 올해의 선수·베스트 11 목록 = 예전 채점 목록(필드·순서 완전 일치)", poty == expect,
          f"{len(poty or [])}명")
    check("A3 그 목록 1위 = 예전 발롱 채점 1위(18590)", bool(poty) and poty[0]["player_id"] == 18590,
          str(poty[0]["player_id"]) if poty else "-")

    cap.clear()
    GE._compute_ballon_v2 = lambda c, y, cd: _stub_v2(c, y, cd, _mut=True)
    GE._save_ballon_dor_top30(None, 2001, copy.deepcopy(real))
    poty2 = [{k: v for k, v in x.items() if k != "v2_touched"} for x in cap["poty"]]
    check("A4 새 채점 쪽 변화와 무관하게 기존 점수 목록 동일", poty2 == expect)
finally:
    for k, v in _orig.items():
        setattr(GE, k, v)

# ── B. 저장 칸 대응 ────────────────────────────────────────────────
print("B. 저장 칸 대응")
scored = B.score_season(synth_entries(seed=11))
top = B.top30(scored)
ent = GE._ballon_v2_entries(top)
ok_map = ok_sum = True
for (_k, _r, e), x in zip(ent, top):
    ok_map &= (e["player_id"] == x["player_id"] and e["team_id"] == x["team_id"]
               and abs(e["total_score"] - x["total"]) < 1e-4 and abs(e["score_rating"] - x["s_ovr"]) < 1e-4
               and abs(e["score_goals_assists"] - x["s_prod"]) < 1e-4 and abs(e["score_position_adj"] - x["s_part"]) < 1e-4
               and abs(e["team_trophy"] - x["s_team"]) < 1e-4 and abs(e["national_trophy"] - x["s_intl"]) < 1e-4
               and e["individual_trophy"] is None and e["stat_goals"] == x["goals"] and e["stat_assists"] == x["assists"])
    ok_sum &= abs(e["score_trophy"] - (e["team_trophy"] + e["national_trophy"])) < 2e-4 and abs(
        e["total_score"] - (e["score_rating"] + e["score_goals_assists"] + e["score_position_adj"]
                            + e["team_trophy"] + e["national_trophy"])) < 5e-4
check("B1 각 칸 = 모듈 값(총점·능력·생산성·출전·팀·국제·리그 골도움, 개인상 칸 비움)", ok_map, f"{len(ent)}행")
check("B2 총점 = 능력+생산성+출전+팀+국제, 트로피 = 팀+국제", ok_sum)
check("B3 저장 순위 = 모듈 정렬 순서", [e[2]["player_id"] for e in ent] == [x["player_id"] for x in top])
check("B4 팀당 3명 이하", max(sum(1 for x in top if x["team_id"] == t) for t in {x["team_id"] for x in top}) <= 3)

# ── C. 실제 세이브(선택) ──────────────────────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("--db", default=None)
ap.add_argument("--year", type=int, default=None)
a = ap.parse_args()
if a.db:
    print("C. 실제 세이브")
    import award_baseline as AB
    with contextlib.redirect_stdout(io.StringIO()):
        conn, tmp = AB.prepare(os.path.abspath(a.db))
    c = conn.cursor()
    try:
        AB.patch_for_past_years(c)
        years = [r[0] for r in c.execute("SELECT DISTINCT year FROM season_individual_awards "
                                         "WHERE award_type='발롱도르' ORDER BY year").fetchall()]
        y = a.year or years[0]
        AB.clear_year_caches()
        GE._begin_team_gc_run_cache(y)
        try:
            cands = GE._get_ballon_candidates(c, y)
            snap = copy.deepcopy(cands)
            s1, t1 = GE._compute_ballon_v2(c, y, cands)
            check(f"C1 {y}: 새 채점이 후보 리스트를 바꾸지 않음", cands == snap, f"후보 {len(cands)}명")
            s2, t2 = GE._compute_ballon_v2(c, y, cands)
            same = lambda p, q: [(x["player_id"], round(x["total"], 9)) for x in p] == \
                [(x["player_id"], round(x["total"], 9)) for x in q]
            check(f"C2 {y}: 두 번 계산 결과 같음", same(t1, t2) and same(s1, s2))
            sh = cands[:]
            random.Random(105).shuffle(sh)
            _s3, t3 = GE._compute_ballon_v2(c, y, sh)
            check(f"C3 {y}: 후보 순서를 섞어도 Top30·점수 같음", same(t1, t3), f"1위 {t1[0]['player_id']}")
        finally:
            GE._end_team_gc_run_cache()
    finally:
        conn.close()
        os.chdir(_path.ROOT)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
sys.exit(1 if FAILS else 0)