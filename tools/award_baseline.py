# -*- coding: utf-8 -*-
"""[2026-11] 개인상 1단계 — 기존 세이브(시드 105·106 10시즌 등)에서 기준선 측정 + 70% 출전 자격 검증.

시뮬레이션은 돌리지 않는다. 세이브(game.db + .history.db)를 임시 폴더에 복사해 읽기만 하고, 원본은 건드리지 않는다.
발롱도르·야신상은 시뮬레이션 상태에 들어가지 않으므로 지난 연도를 그대로 다시 계산할 수 있다.

측정 항목
  [검증 0] 재현성 — 예전 자격(10경기)으로 다시 계산한 발롱도르 Top30·야신상 Top10이 저장값과 같은가.
           이게 맞아야 아래 비교를 믿을 수 있다(1위 일치 연도 수, Top-N 순서 완전 일치 연도 수, 점수 최대 차이).
  [필수 1] 새 자격(팀 리그 경기 수의 70%)으로 다시 계산한 야신상 Top10·발롱도르 Top30 전원 70% 이상.
           참고로 저장값(예전 자격)에서 70% 미만이 몇 명이었는지도 같이 센다.
  [필수 2] 70% 미만으로 빠진 선수 외 발롱도르 점수·순서 — 엄격 판정(점수 완전 동일 + 순서 유지)과,
           점수가 바뀐 경우 원인 분해(개인 점수 항목은 반드시 동일해야 하고, 트로피 차이는 팀·국적 내
           감쇠 순위가 바뀐 경우만 허용되는지)를 둘 다 보고한다. 감쇠(_apply_ballon_team_trophy_decay·
           _apply_ballon_intl_bonus_decay)는 같은 팀·같은 국적 후보끼리 순위를 매겨 트로피 점수를 나누므로,
           제외된 선수가 그 팀의 1순위였다면 남은 동료의 트로피 점수가 오를 수 있다.
  [기준선]  B1 발롱도르 — 1위·Top30 포지션 분포(그룹·세부), 1위 선수 수(같은 선수 연속 수상 구분), 점수 구성.
           B2 야신상 — Top10 선방률(중앙값·1위), 선방률–순위 상관, 점수 구성, OVR 순위.
           B3 리그 개인상(SS/S/A 1부) — MVP·베스트11·올해의 수비수·골든글러브·영플레이어 수상자가
              그 리그 같은 포지션 그룹 안에서 OVR 몇 위였는지(OVR 몰림 정도), MVP 소속팀 최종 순위.

사용:
  python tools/award_baseline.py --db 경로/headless.db [--out 결과폴더] [--years 2001-2010]
  (세이브 이름이 X.db면 X.history.db를 같은 폴더에서 찾는다. 시드별로 한 번씩 실행)
출력: --out 폴더에 summary.txt와 CSV 여러 개. 화면에는 핵심 표만 찍는다."""
import _path  # noqa: F401
import argparse
import copy
import csv
import os
import shutil
import statistics
import sys
import tempfile
import time

import database
import game_engine as GE
from constants import get_country_league_grade, GK_POS, DF_POS, FW_POS

_MF = set(GE.AWARD_MF_POS)
_FW = set(FW_POS) | {"SS"}
_DF = set(DF_POS)
_GK = set(GK_POS)


def pos_group(pos):
    if pos in _GK:
        return "GK"
    if pos in _DF:
        return "DF"
    if pos in _MF:
        return "MF"
    if pos in _FW:
        return "FW"
    return "?"


# ── 세이브 복사 ──────────────────────────────────────────────────────────
def prepare(db_src):
    db_src = os.path.abspath(db_src)
    if not os.path.exists(db_src):
        sys.exit(f"세이브를 찾지 못했다: {db_src}")
    base = os.path.splitext(db_src)[0]
    hist_src = base + ".history.db"
    if not os.path.exists(hist_src):
        sys.exit(f"history DB를 찾지 못했다: {hist_src}")
    tmp = tempfile.mkdtemp(prefix="award_baseline_")
    for src, name in ((db_src, "game.db"), (hist_src, "game.history.db")):
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(src + ext):
                shutil.copy(src + ext, os.path.join(tmp, name + ext))
    os.chdir(tmp)
    database.DB_PATH = os.path.join(tmp, "game.db")
    database.init_db()
    return database.get_conn(), tmp


def patch_for_past_years(c):
    """지난 연도 재계산용 보정 — 결과 함수는 그대로 쓰고 '그 시즌 데이터를 어디서 읽는지'만 바꾼다.
    ① 은퇴 선수를 현역 표에 임시로 되살린다(후보 함수가 ai_players와 JOIN — 복사본 메모리 DB에서만).
    ② 팀 → 그 시즌 리그: 시즌 종료 시점엔 match_results(+아카이브)로 구했지만 지난 시즌 원본 경기는
       아카이브 정리로 대부분 지워진다 — 같은 내용을 담은 hist.league_season_standings로 읽는다.
    ③ 시즌 중 이적생: ai_transfer_log는 최근 5시즌만 원본에 남고 나머지는 아카이브로 옮겨진다 — 둘 다 읽는다."""
    n_ret = c.execute(
        """INSERT OR IGNORE INTO ai_players(id, name, position, ovr, age, nationality, team_id)
           SELECT id, name, position, ovr, age, nationality, NULL FROM ai_players_retired""").rowcount

    orig_map = GE._team_league_id_map_for_year

    def lss_map(cc, year):
        rows = cc.execute("""SELECT team_id, MIN(league_id) FROM hist.league_season_standings
                             WHERE year=? GROUP BY team_id""", (year,)).fetchall()
        return {r[0]: r[1] for r in rows} if rows else orig_map(cc, year)
    GE._team_league_id_map_for_year = lss_map

    def first_half(cc, year):
        out = {}
        for tbl in ("ai_transfer_log", "ai_transfer_log_archive"):
            try:
                for r in cc.execute(f"SELECT player_id, from_team_id FROM {tbl} "
                                    "WHERE year=? AND is_mid_season=1 ORDER BY id", (year,)).fetchall():
                    if r[1]:
                        out.setdefault(r[0], r[1])
            except Exception:
                pass
        return out
    GE._get_mid_season_first_half_teams = first_half
    return n_ret


def clear_year_caches():
    for name in ("_TEAM_LEAGUE_MAP_CACHE", "_COMP_MATCH_COUNT_CACHE", "_AWARD_IDENTITY_CACHE"):
        obj = getattr(GE, name, None)
        if isinstance(obj, dict):
            obj.clear()
    GE._AWARD_POS_ROWS_MEMO = None


# ── 재계산 ────────────────────────────────────────────────────────────
def _sort_key(x):
    return (-x["total_score"], -(x.get("rating") or 0),
            -((x.get("goals") or 0) + (x.get("assists") or 0)), x["player_id"])


def _scored(cands, gk_only):
    """_save_ballon_dor_top30 / _save_yashin_trophy_top10와 같은 순서: 전체 후보 감쇠 → (야신상은 GK만) → 채점."""
    raw = {cd["player_id"]: (cd.get("trophy_bonus", 0.0) or 0.0, cd.get("intl_bonus", 0.0) or 0.0) for cd in cands}
    cands = GE._apply_ballon_team_trophy_decay(cands)
    cands = GE._apply_ballon_intl_bonus_decay(cands)
    if gk_only:
        cands = [cd for cd in cands if cd.get("position") == "GK"]
    out = []
    for cd in cands:
        s = GE._score_ballon_candidate(cd)
        tb0, ib0 = raw[cd["player_id"]]
        out.append({**cd, **s, "_tb_raw": tb0, "_ib_raw": ib0,
                    "_tb_ratio": (round((cd.get("trophy_bonus") or 0.0) / tb0, 3) if tb0 else None),
                    "_ib_ratio": (round((cd.get("intl_bonus") or 0.0) / ib0, 3) if ib0 else None)})
    out.sort(key=_sort_key)
    return out


def _cap_top(scored, per_team, n):
    top, cnt = [], {}
    for x in scored:
        tid = x.get("team_id")
        k = cnt.get(tid, 0) if tid is not None else 0
        if tid is not None and k >= per_team:
            continue
        top.append(x)
        if tid is not None:
            cnt[tid] = k + 1
        if len(top) >= n:
            break
    return top


def recompute(c, year, new_gate):
    clear_year_caches()
    orig = GE._meets_award_league_share
    if not new_gate:
        GE._meets_award_league_share = lambda m, t: True   # 예전 자격: SQL의 10경기 하한만
    GE._begin_team_gc_run_cache(year)
    try:
        cands = GE._get_ballon_candidates(c, year)
        ycands = copy.deepcopy(cands)
        b_full = _scored(cands, gk_only=False)
        y_full = _scored(ycands, gk_only=True)
    finally:
        GE._end_team_gc_run_cache()
        GE._meets_award_league_share = orig
    return {"ballon_full": b_full, "ballon_top": _cap_top(b_full, GE._BALLON_MAX_PER_TEAM, 30),
            "yashin_full": y_full, "yashin_top": _cap_top(y_full, GE._YASHIN_MAX_PER_TEAM, GE._YASHIN_TOP_N)}


def recompute_ballon_v2(c, year):
    """[2026-11 연결] 새 채점으로 저장된 발롱도르 행(xi_formation == "ballon_v2") 재현 — 실제 저장과 같은
    game_engine._compute_ballon_v2를 70% 자격 후보로 다시 돌린다. total_score 키로 맞춰 돌려준다."""
    clear_year_caches()
    GE._begin_team_gc_run_cache(year)
    try:
        cands = GE._get_ballon_candidates(c, year)
        _scored_all, top = GE._compute_ballon_v2(c, year, cands)
    finally:
        GE._end_team_gc_run_cache()
    return [dict(x, total_score=x["total"]) for x in top]


# ── 출전 비율 ─────────────────────────────────────────────────────────
def share_info(c, year, pid, team_id):
    """(리그 출전, 팀 리그 경기 수(자격 계산 분모), 실제 순위표 경기 수(교차 확인), 비율%)"""
    if pid == GE._SIA_MY_PLAYER_ID:
        r = c.execute("SELECT matches, team_id FROM my_player_season_stats WHERE year=?", (year,)).fetchone()
    else:
        r = c.execute("SELECT matches, team_id FROM hist.ai_player_season_stats WHERE player_id=? AND year=?",
                      (pid, year)).fetchone()
    m = r[0] if r else None
    tid = (r[1] if r and r[1] else team_id)
    tlm = GE._season_team_league_matches(c, year).get(tid)
    g = c.execute("SELECT wins+draws+losses FROM hist.league_season_standings WHERE year=? AND team_id=?",
                  (year, tid)).fetchone()
    pct = round(100.0 * m / tlm, 1) if (m is not None and tlm) else None
    return m, tlm, (g[0] if g else None), pct


# ── 보조 ─────────────────────────────────────────────────────────────
def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                rk[order[k]] = (i + j) / 2.0 + 1
            i = j + 1
        return rk
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(cov / (vx * vy), 3) if vx and vy else None


def save_rate(sv, gc):
    if sv is None or gc is None or (sv + gc) <= 0:
        return None
    return sv / (sv + gc)


def pct_s(v):
    return "-" if v is None else f"{100 * v:.1f}%"


def write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(k) for k in cols])


# ── 메인 ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--years", default=None, help="예: 2001-2010 (기본: 저장된 발롱도르 연도 전부)")
    a = ap.parse_args()
    out_dir = os.path.abspath(a.out or os.path.join(
        os.path.dirname(os.path.abspath(a.db)), "award_baseline_" + time.strftime("%Y%m%d_%H%M%S")))
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    conn, tmp = prepare(a.db)
    c = conn.cursor()
    lines = []

    def say(s=""):
        print(s)
        lines.append(s)

    try:
        n_ret = patch_for_past_years(c)
        years = [r[0] for r in c.execute("SELECT DISTINCT year FROM season_individual_awards "
                                         "WHERE award_type='발롱도르' ORDER BY year").fetchall()]
        if a.years:
            lo, hi = (int(x) for x in a.years.split("-"))
            years = [y for y in years if lo <= y <= hi]
        say(f"세이브: {os.path.abspath(a.db)}")
        say(f"연도: {years[0] if years else '-'}~{years[-1] if years else '-'} ({len(years)}시즌), 은퇴 선수 복원 {n_ret}명")
        if not years:
            say("저장된 발롱도르가 없다 — 종료")
            return

        def stored(year, award):
            return c.execute("""SELECT rank, player_id, team_id, position, total_score, score_trophy, team_trophy,
                                       national_trophy, individual_trophy, score_rating, score_goals_assists,
                                       score_position_adj, stat_saves, stat_goals_conceded, xi_formation
                                FROM season_individual_awards WHERE year=? AND award_type=? ORDER BY rank""",
                             (year, award)).fetchall()

        elig_rows, fid_rows, r2_rows = [], [], []
        fid = {"발롱도르": [0, 0, 0.0], "야신상": [0, 0, 0.0]}     # [1위 일치, 순서 완전 일치, 점수 최대 차이]
        req1_fail, old_under = 0, {"발롱도르": 0, "야신상": 0}
        new_under_list = []
        r2 = {"same": 0, "decay": 0, "unexplained": 0, "order_moves": 0, "strict_years_ok": 0}
        excluded_total = 0
        fid_v2_years = []
        for y in years:
            ty = time.time()
            old = recompute(c, y, new_gate=False)
            new = recompute(c, y, new_gate=True)
            # 검증 0 — 재현성
            for award, key in (("발롱도르", "ballon_top"), ("야신상", "yashin_top")):
                st = stored(y, award)
                st_ids = [r["player_id"] for r in st]
                re_top = old[key]
                # [2026-11 연결] 새 채점으로 저장된 발롱도르 해는 새 채점으로 재현한다(아래 필수 1·2는 예전 채점 비교 그대로).
                if award == "발롱도르" and st and all(r["xi_formation"] == GE.BALLON_ROW_FORMAT for r in st):
                    re_top = recompute_ballon_v2(c, y)
                    fid_v2_years.append(y)
                re_ids = [x["player_id"] for x in re_top]
                re_sc = {x["player_id"]: x["total_score"] for x in re_top}
                if st_ids[:1] == re_ids[:1]:
                    fid[award][0] += 1
                if st_ids == re_ids:
                    fid[award][1] += 1
                diffs = [abs((r["total_score"] or 0) - re_sc[r["player_id"]]) for r in st if r["player_id"] in re_sc]
                md = max(diffs) if diffs else 0.0
                fid[award][2] = max(fid[award][2], md)
                fid_rows.append({"year": y, "award": award, "stored_n": len(st_ids), "recomputed_n": len(re_ids),
                                 "rank1_same": st_ids[:1] == re_ids[:1], "order_same": st_ids == re_ids,
                                 "overlap": len(set(st_ids) & set(re_ids)), "max_score_diff": round(md, 3),
                                 "stored_ids": " ".join(map(str, st_ids)), "recomputed_ids": " ".join(map(str, re_ids))})
                # 저장값(예전 자격)과 새 자격 결과의 출전 비율
                for src, rows in (("저장값", [(r["rank"], r["player_id"], r["team_id"], r["position"]) for r in st]),
                                  ("새자격", [(i + 1, x["player_id"], x["team_id"], x.get("position"))
                                           for i, x in enumerate(new[key])])):
                    for rk, pid, tid, pos in rows:
                        m, tlm, g, p = share_info(c, y, pid, tid)
                        ok = GE._meets_award_league_share(m, tlm)
                        elig_rows.append({"year": y, "award": award, "source": src, "rank": rk, "player_id": pid,
                                          "position": pos, "league_matches": m, "team_league_matches": tlm,
                                          "standings_games": g, "share_pct": p, "meets_70": ok})
                        if src == "저장값" and not ok:
                            old_under[award] += 1
                        if src == "새자격" and not ok:
                            req1_fail += 1
                            new_under_list.append((y, award, rk, pid, m, tlm))
            # 필수 2 — 발롱도르: 새 자격에서도 남은 선수
            o_by = {x["player_id"]: x for x in old["ballon_full"]}
            n_ids = {x["player_id"] for x in new["ballon_full"]}
            excluded = [pid for pid in o_by if pid not in n_ids]
            excluded_total += len(excluded)
            year_strict = True
            for x in new["ballon_full"]:
                o = o_by.get(x["player_id"])
                if o is None:
                    cls = "unexplained"          # 새 자격에만 있는 후보 — 있으면 안 됨
                else:
                    ind_same = all(abs((x[k] or 0) - (o[k] or 0)) < 1e-9 for k in
                                   ("score_rating", "score_goals_assists", "score_position_adj"))
                    tot_same = abs(x["total_score"] - o["total_score"]) < 1e-9
                    decay_changed = (x["_tb_ratio"] != o["_tb_ratio"]) or (x["_ib_ratio"] != o["_ib_ratio"])
                    cls = "same" if tot_same else ("decay" if (ind_same and decay_changed) else "unexplained")
                r2[cls] += 1
                if cls != "same":
                    year_strict = False
                    r2_rows.append({"year": y, "player_id": x["player_id"], "team_id": x["team_id"],
                                    "position": x.get("position"), "class": cls,
                                    "old_total": o["total_score"] if o else None, "new_total": x["total_score"],
                                    "old_trophy": o["score_trophy"] if o else None, "new_trophy": x["score_trophy"],
                                    "old_team_decay": o["_tb_ratio"] if o else None, "new_team_decay": x["_tb_ratio"],
                                    "old_nat_decay": o["_ib_ratio"] if o else None, "new_nat_decay": x["_ib_ratio"]})
            # 순서: 새 Top30을 예전 점수로 다시 줄 세웠을 때 순서가 바뀌는 선수 수
            n_top_ids = [x["player_id"] for x in new["ballon_top"]]
            by_old = sorted((o_by[p] for p in n_top_ids if p in o_by), key=_sort_key)
            moves = sum(1 for a_, b_ in zip(n_top_ids, [x["player_id"] for x in by_old]) if a_ != b_)
            r2["order_moves"] += moves
            if year_strict and moves == 0:
                r2["strict_years_ok"] += 1
            print(f"  {y}: 후보 {len(old['ballon_full'])} → {len(new['ballon_full'])} "
                  f"(제외 {len(excluded)}) · {time.time() - ty:.1f}s")

        say()
        say("[검증 0] 재현성 — 예전 자격으로 다시 계산 vs 저장값")
        for award in ("발롱도르", "야신상"):
            f = fid[award]
            say(f"  {award}: 1위 일치 {f[0]}/{len(years)} · 순서 완전 일치 {f[1]}/{len(years)} · 점수 최대 차이 {f[2]:.3f}")
        if fid_v2_years:
            say(f"  발롱도르 새 채점(ballon_v2)으로 재현한 해: {len(fid_v2_years)}시즌 ({fid_v2_years[0]}~{fid_v2_years[-1]})")
        say("  (1위가 전부 맞지 않으면 아래 결과는 참고용 — fidelity.csv에서 어긋난 연도부터 확인)")
        say()
        say("[필수 1] 새 자격 Top 명단 전원 리그 출전 70% 이상")
        say(f"  위반 {req1_fail}명 → {'통과' if req1_fail == 0 else '실패'}")
        for t in new_under_list[:10]:
            say(f"    {t[0]} {t[1]} {t[2]}위 pid={t[3]} {t[4]}/{t[5]}")
        say(f"  참고 — 저장값(예전 자격) 중 70% 미만: 발롱도르 Top30 {old_under['발롱도르']}명 · "
            f"야신상 Top10 {old_under['야신상']}명 ({len(years)}시즌 합)")
        say()
        say("[필수 2] 70% 미만으로 빠진 선수 외 발롱도르 점수·순서 (엄격 기준은 참고)")
        say(f"  제외된 후보 {excluded_total}명(시즌 합). 남은 후보 점수: 동일 {r2['same']} · "
            f"감쇠 순위 변화로 설명됨 {r2['decay']} · 설명 안 됨 {r2['unexplained']}")
        say(f"  Top30 순서 이동 {r2['order_moves']}건 · 엄격 기준(점수 완전 동일+순서 유지) 통과 연도 "
            f"{r2['strict_years_ok']}/{len(years)}")
        say(f"  판정(수정안, 2026-11 확정: 개인 점수 100% 동일 + 트로피 차이는 감쇠 순위 변화로만 + 설명 안 됨 0) → "
            f"{'통과' if r2['unexplained'] == 0 else '실패'}")
        say("  (설명 안 됨이 0이 아니면 구조 문제 — rule2_diff.csv)")

        # ── 기준선 B1: 발롱도르 ─────────────────────────────────────
        say()
        say("[B1] 발롱도르(저장값) 포지션 분포")
        b_rows = c.execute("""SELECT year, rank, player_id, position, total_score, score_trophy, team_trophy,
                                     national_trophy, individual_trophy, score_rating, score_goals_assists,
                                     score_position_adj
                              FROM season_individual_awards WHERE award_type='발롱도르' AND year IN (%s)
                              ORDER BY year, rank""" % ",".join(map(str, years))).fetchall()
        b_list = [dict(r) for r in b_rows]
        for d in b_list:
            d["group"] = pos_group(d["position"])

        def dist(rows, key):
            out = {}
            for d in rows:
                out[d[key]] = out.get(d[key], 0) + 1
            return out
        w1 = [d for d in b_list if d["rank"] == 1]
        for label, rows in (("1위", w1), ("Top10", [d for d in b_list if d["rank"] <= 10]), ("Top30", b_list)):
            g = dist(rows, "group")
            n = len(rows) or 1
            say(f"  {label:5} (n={len(rows)}): " + " · ".join(
                f"{k} {g.get(k, 0)} ({100 * g.get(k, 0) / n:.0f}%)" for k in ("FW", "MF", "DF", "GK")))
        dpos = dist(b_list, "position")
        say("  Top30 세부: " + " · ".join(f"{k} {v}" for k, v in sorted(dpos.items(), key=lambda t: -t[1])))
        wins_by_player = {}
        for d in w1:
            wins_by_player.setdefault((d["group"], d["player_id"]), 0)
            wins_by_player[(d["group"], d["player_id"])] += 1
        for g in ("FW", "MF", "DF", "GK"):
            ws = [v for (gg, _p), v in wins_by_player.items() if gg == g]
            if ws:
                say(f"  1위 {g}: {sum(ws)}회 / 선수 {len(ws)}명 (최다 {max(ws)}회)")
        say("  Top30 그룹별 평균 점수 구성(평점 · 생산성 · 포지션 · 트로피[팀/국가/개인상] · 합 · 트로피 비중)")
        comp_rows = []
        for g in ("FW", "MF", "DF", "GK"):
            rs = [d for d in b_list if d["group"] == g]
            if not rs:
                continue

            def av(k):
                return round(statistics.mean((d[k] or 0) for d in rs), 2)
            share = av("score_trophy") / av("total_score") if av("total_score") else 0
            say(f"    {g} n={len(rs):3}: {av('score_rating'):5} · {av('score_goals_assists'):5} · "
                f"{av('score_position_adj'):4} · {av('score_trophy'):5}[{av('team_trophy')}/{av('national_trophy')}/"
                f"{av('individual_trophy')}] · {av('total_score'):5} · {100 * share:.0f}%")
            comp_rows.append({"group": g, "n": len(rs), "rating": av("score_rating"),
                              "productivity": av("score_goals_assists"), "position_adj": av("score_position_adj"),
                              "trophy": av("score_trophy"), "team_trophy": av("team_trophy"),
                              "national_trophy": av("national_trophy"), "individual_trophy": av("individual_trophy"),
                              "total": av("total_score"), "trophy_share": round(share, 3)})
        write_csv(os.path.join(out_dir, "ballon_top30_stored.csv"), b_list,
                  ["year", "rank", "player_id", "position", "group", "total_score", "score_rating",
                   "score_goals_assists", "score_position_adj", "score_trophy", "team_trophy", "national_trophy",
                   "individual_trophy"])
        write_csv(os.path.join(out_dir, "ballon_components_by_group.csv"), comp_rows, list(comp_rows[0].keys())
                  if comp_rows else ["group"])

        # ── 기준선 B2: 야신상 ──────────────────────────────────────
        say()
        say("[B2] 야신상(저장값) Top10")
        y_rows = [dict(r) for r in c.execute(
            """SELECT year, rank, player_id, team_id, total_score, score_trophy, score_rating, score_goals_assists,
                      stat_saves, stat_goals_conceded
               FROM season_individual_awards WHERE award_type='야신상' AND year IN (%s) ORDER BY year, rank"""
            % ",".join(map(str, years))).fetchall()]
        ovr_rank_rows = []
        sr_all, sr_1, corr = [], [], []
        _top_c = GE._country_ids_by_league_grade(c, ("SS", "S", "A"))
        _lg_c = {r[0]: r[1] for r in c.execute("SELECT id, country_id FROM leagues WHERE tier=1").fetchall()}

        def top_tier1_team_ids(y):
            """그 시즌 SS/S/A 리그 1부에서 뛴 팀(야신상 후보 리그와 같은 범위)."""
            m = GE._season_team_league_map(c, y)
            return {t for t, l in m.items() if _lg_c.get(l) in _top_c}
        for y in years:
            yr = [d for d in y_rows if d["year"] == y]
            for d in yr:
                d["save_rate"] = save_rate(d["stat_saves"], d["stat_goals_conceded"])
                d["ovr"] = (c.execute("SELECT ovr FROM hist.ai_player_ovr_history WHERE player_id=? AND year=?",
                                      (d["player_id"], y)).fetchone() or [None])[0]
            srs = [d["save_rate"] for d in yr if d["save_rate"] is not None]
            sr_all += srs
            if yr and yr[0]["save_rate"] is not None:
                sr_1.append(yr[0]["save_rate"])
            pairs = [(d["save_rate"], d["rank"]) for d in yr if d["save_rate"] is not None]
            cc = spearman([p[0] for p in pairs], [p[1] for p in pairs])
            if cc is not None:
                corr.append(cc)
            # OVR 순위 — 그 해 SS/S/A 1부 GK(10경기+) 안에서
            top_tids = top_tier1_team_ids(y)
            gk_pool = [r[1] for r in c.execute(
                """SELECT s.team_id, o.ovr FROM hist.ai_player_ovr_history o JOIN hist.ai_player_season_stats s
                     ON s.player_id=o.player_id AND s.year=o.year
                   JOIN hist.ai_player_position_history ph ON ph.player_id=o.player_id AND ph.year=o.year
                   WHERE o.year=? AND ph.position='GK' AND s.matches>=10""", (y,)).fetchall() if r[0] in top_tids]
            gk_pool.sort(reverse=True)
            for d in yr:
                if d["ovr"] is not None and gk_pool:
                    d["ovr_rank_top_gk"] = 1 + sum(1 for v in gk_pool if v > d["ovr"])
                    ovr_rank_rows.append(d["ovr_rank_top_gk"])
        say(f"  Top10 선방률 중앙값 {pct_s(statistics.median(sr_all) if sr_all else None)} · "
            f"1위 선방률 중앙값 {pct_s(statistics.median(sr_1) if sr_1 else None)} "
            f"(최저 {pct_s(min(sr_1) if sr_1 else None)}) · 1위 72% 미만 {sum(1 for v in sr_1 if v < 0.72)}회")
        say(f"  선방률–순위 상관(연도별 중앙값) {statistics.median(corr) if corr else '-'} "
            f"(음수일수록 선방률 높은 GK가 위)")
        if y_rows:
            def avy(k):
                return round(statistics.mean((d[k] or 0) for d in y_rows), 2)
            say(f"  평균 점수 구성: 평점 {avy('score_rating')} · 생산성 {avy('score_goals_assists')} · "
                f"트로피 {avy('score_trophy')} · 합 {avy('total_score')} "
                f"(트로피 비중 {100 * avy('score_trophy') / avy('total_score'):.0f}%)" if avy('total_score') else "")
        if ovr_rank_rows:
            w1r = [d.get("ovr_rank_top_gk") for d in y_rows if d["rank"] == 1 and d.get("ovr_rank_top_gk")]
            say(f"  Top10의 SS/S/A 1부 GK(10경기+) 중 OVR 순위 중앙값 {statistics.median(ovr_rank_rows)} · "
                f"1위의 OVR 순위 중앙값 {statistics.median(w1r) if w1r else '-'}")
        write_csv(os.path.join(out_dir, "yashin_top10_stored.csv"), y_rows,
                  ["year", "rank", "player_id", "team_id", "total_score", "score_rating", "score_goals_assists",
                   "score_trophy", "stat_saves", "stat_goals_conceded", "save_rate", "ovr", "ovr_rank_top_gk"])

        # ── 기준선 B3: 리그 개인상 OVR 몰림 ─────────────────────────
        say()
        say("[B3] 리그 개인상(SS/S/A 1부) — 수상자가 리그 같은 포지션 그룹에서 OVR 몇 위였나")
        grade_of = {}
        for r in c.execute("SELECT name, grade FROM countries").fetchall():
            grade_of[r[0]] = get_country_league_grade(r[0], r[1])
        lg_rows = [dict(r) for r in c.execute(
            """SELECT year, award_kind, rank, player_id, team_id, position, competition, league_country, xi_slot
               FROM season_individual_awards
               WHERE category='league' AND league_tier=1 AND player_id>=0
                 AND award_kind IN ('MVP','베스트11','올해의 수비수','골든글러브','영플레이어') AND year IN (%s)"""
            % ",".join(map(str, years))).fetchall()]
        lg_rows = [d for d in lg_rows if grade_of.get(d["league_country"]) in ("SS", "S", "A")]
        pool_cache = {}

        def league_pool(y, team_id):
            lid = GE._season_league_id_of_team(c, y, team_id)
            key = (y, lid)
            if key not in pool_cache:
                rows = c.execute(
                    """SELECT s.player_id, s.team_id, o.ovr,
                              COALESCE(ph.position, ap.position) AS pos
                       FROM hist.league_season_standings l
                       JOIN hist.ai_player_season_stats s ON s.team_id=l.team_id AND s.year=l.year
                       JOIN hist.ai_player_ovr_history o ON o.player_id=s.player_id AND o.year=s.year
                       LEFT JOIN hist.ai_player_position_history ph ON ph.player_id=s.player_id AND ph.year=s.year
                       LEFT JOIN ai_players ap ON ap.id=s.player_id
                       WHERE l.year=? AND l.league_id=? AND s.matches>=10""", (y, lid)).fetchall()
                pts = {r[0]: (r[1] * 3 + r[2], r[3]) for r in c.execute(
                    """SELECT team_id, wins, draws, goals_for-goals_against FROM hist.league_season_standings
                       WHERE year=? AND league_id=?""", (y, lid)).fetchall()}
                order = sorted(pts, key=lambda t: (-pts[t][0], -pts[t][1]))
                pool_cache[key] = ([{"pid": r[0], "tid": r[1], "ovr": r[2], "grp": pos_group(r[3])} for r in rows],
                                   {t: i + 1 for i, t in enumerate(order)}, len(order))
            return pool_cache[key]

        res = {}
        detail = []
        for d in lg_rows:
            pool, tranks, nteams = league_pool(d["year"], d["team_id"])
            me = next((p for p in pool if p["pid"] == d["player_id"]), None)
            if me is None:
                continue
            grp = pos_group(d["position"])
            if d["award_kind"] == "MVP":
                peers = pool
            else:
                peers = [p for p in pool if p["grp"] == grp]
            rk = 1 + sum(1 for p in peers if p["ovr"] > me["ovr"])
            d["ovr_rank"], d["peer_n"], d["group"] = rk, len(peers), grp
            d["team_rank"], d["n_teams"] = tranks.get(d["team_id"]), nteams
            detail.append(d)
        # 베스트11: 그룹별 뽑힌 수(k) 안에 OVR 순위가 들었나
        xi_k = {}
        for d in detail:
            if d["award_kind"] == "베스트11":
                kk = (d["year"], d["competition"], d["group"])
                xi_k[kk] = xi_k.get(kk, 0) + 1
        for d in detail:
            kind = d["award_kind"]
            key = kind if kind != "베스트11" else f"베스트11-{d['group']}"
            k = xi_k.get((d["year"], d["competition"], d["group"]), 1) if kind == "베스트11" else 1
            e = res.setdefault(key, {"n": 0, "topk": 0, "ranks": [], "team_top4": 0, "team_ranks": []})
            e["n"] += 1
            e["topk"] += 1 if d["ovr_rank"] <= k else 0
            e["ranks"].append(d["ovr_rank"])
            if d.get("team_rank"):
                e["team_ranks"].append(d["team_rank"])
                e["team_top4"] += 1 if d["team_rank"] <= 4 else 0
        say("  상            n   OVR 상위k 안   OVR순위 중앙값  소속팀 순위 중앙값  팀 4위 이내")
        b3_rows = []
        for key in ("MVP", "베스트11-GK", "베스트11-DF", "베스트11-MF", "베스트11-FW", "올해의 수비수", "골든글러브", "영플레이어"):
            e = res.get(key)
            if not e:
                continue
            row = {"award": key, "n": e["n"], "ovr_topk_pct": round(100 * e["topk"] / e["n"], 1),
                   "ovr_rank_median": statistics.median(e["ranks"]),
                   "team_rank_median": statistics.median(e["team_ranks"]) if e["team_ranks"] else None,
                   "team_top4_pct": round(100 * e["team_top4"] / len(e["team_ranks"]), 1) if e["team_ranks"] else None}
            b3_rows.append(row)
            say(f"  {key:12} {row['n']:4}   {row['ovr_topk_pct']:5}%        {row['ovr_rank_median']:>5}"
                f"            {row['team_rank_median']!s:>5}          {row['team_top4_pct']}%")
        say("  (OVR 상위k 안 = 그 리그 같은 그룹 k자리(MVP·단일 상은 1자리) 안에 OVR 순위가 든 비율 — 높을수록 OVR 몰림)")
        write_csv(os.path.join(out_dir, "league_awards_ovr.csv"), detail,
                  ["year", "competition", "award_kind", "rank", "player_id", "team_id", "position", "group",
                   "ovr_rank", "peer_n", "team_rank", "n_teams"])
        write_csv(os.path.join(out_dir, "league_awards_ovr_summary.csv"), b3_rows,
                  ["award", "n", "ovr_topk_pct", "ovr_rank_median", "team_rank_median", "team_top4_pct"])

        write_csv(os.path.join(out_dir, "eligibility.csv"), elig_rows,
                  ["year", "award", "source", "rank", "player_id", "position", "league_matches",
                   "team_league_matches", "standings_games", "share_pct", "meets_70"])
        write_csv(os.path.join(out_dir, "fidelity.csv"), fid_rows,
                  ["year", "award", "stored_n", "recomputed_n", "rank1_same", "order_same", "overlap",
                   "max_score_diff", "stored_ids", "recomputed_ids"])
        write_csv(os.path.join(out_dir, "rule2_diff.csv"), r2_rows,
                  ["year", "player_id", "team_id", "position", "class", "old_total", "new_total", "old_trophy",
                   "new_trophy", "old_team_decay", "new_team_decay", "old_nat_decay", "new_nat_decay"])
        mism = [r for r in elig_rows if r["standings_games"] is not None and r["team_league_matches"] is not None
                and r["standings_games"] != r["team_league_matches"]]
        say()
        say(f"교차 확인: 자격 분모(공식) ≠ 순위표 실제 경기 수 {len(mism)}건 / {len(elig_rows)}건")
        say(f"소요 {time.time() - t0:.0f}s · 결과 폴더 {out_dir}")
    finally:
        with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chdir(_path.ROOT)
        try:
            database.reset_conn_pool()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()