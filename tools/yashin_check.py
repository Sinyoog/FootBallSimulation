# -*- coding: utf-8 -*-
"""[2026-11] 야신상 새 채점(yashin_award.py 연결) — 기존 세이브 10시즌 재계산 검증.

세이브를 임시 폴더에 복사해 읽기만 한다(award_baseline.py의 지난 연도 보정 사용). 연도마다 70% 자격 후보를 모아
예전 채점(발롱도르식 GK 채점 + 동점 수정)과 새 채점(game_engine._compute_yashin_top10)을 같은 후보로 비교한다.

합격 기준(2026-11 확정, 시드별):
  1 Top10 실제 선방률 중앙값 76~82%          2 1위 실제 선방률 72% 미만 0회
  3 실제 선방률–순위 상관(연도별 중앙값) ≤ −0.25   4 Top10 전원 리그 출전 70% 이상(+ 단위 테스트)
  5 개인 기록 계산 오류 0(원자료 대조·점수 합·공식 재계산)   6 동일 조건 재실행·후보 순서 섞기 완전 일치
  7 Top10 경기당 실점 중앙값 < 자격 GK 전체, Top10 클린시트율 중앙값 > 전체
  8 1위가 개인 점수(선방·실점·클린시트·출전)만의 Top10 안
  (9 발롱도르 불변·10 상태 해시는 1시즌 실제 엔진 비교로 따로 본다)
관찰: Top10 전원 80% 이상 시즌, 리그·등급별 1위/Top10, 리그별 환경 보정 평균, 1위 소속팀 순위·리그 우승팀 GK 1위
비율, 1위 OVR 순위, 백업(출전 70~85%) 1위 비율, 시즌별 주전·백업 슈팅 수, 수상자 변화표, 점수 구성.
사용: python tools/yashin_check.py --db 경로/game.db [--out 폴더]"""
import _path  # noqa: F401
import argparse
import contextlib
import io
import os
import random
import shutil
import statistics as S
import time

import database
import game_engine as GE
import yashin_award as Y
import award_baseline as AB
import award_tie_check as TC
from constants import get_country_league_grade

BACKUP_SHARE = 0.85      # 관찰용 "백업" = 리그 출전 70~85% (자격 경계 쪽)


def med(v):
    return S.median(v) if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out_dir = os.path.abspath(a.out or os.path.join(os.path.dirname(os.path.abspath(a.db)),
                                                    "yashin_check_" + time.strftime("%Y%m%d_%H%M%S")))
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        conn, tmp = AB.prepare(a.db)
    c = conn.cursor()
    lines = []

    def say(s=""):
        print(s)
        lines.append(s)
    try:
        AB.patch_for_past_years(c)
        years = [r[0] for r in c.execute("SELECT DISTINCT year FROM season_individual_awards "
                                         "WHERE award_type='발롱도르' ORDER BY year").fetchall()]
        lname = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT l.id, l.name, cn.name FROM leagues l JOIN countries cn ON cn.id=l.country_id").fetchall()}
        cgrade = {r[0]: get_country_league_grade(r[0], r[1]) for r in c.execute("SELECT name, grade FROM countries")}
        rng = random.Random(20261107)
        top_all, pool_all, rows_csv = [], [], []
        sr_t10, sr_1, cors, ovr_cors = [], [], [], []
        c5_bad = c6_bad = c4_bad = c8_bad = 0
        all80 = 0
        changes = []
        shots_by_year = []
        for y in years:
            GE._begin_team_gc_run_cache(y)
            try:
                AB.clear_year_caches()
                cands = GE._get_ballon_candidates(c, y)
                old = TC.pipeline(cands)[2]                       # 예전 야신상(발롱도르식 채점, 동점 수정 반영)
                pool, top = GE._compute_yashin_top10(c, y, cands)
                # 6 — 재실행·섞기
                for _k in range(3):
                    sh = cands[:]
                    rng.shuffle(sh)
                    _p2, t2 = GE._compute_yashin_top10(c, y, sh)
                    if [x["player_id"] for x in t2] != [x["player_id"] for x in top] or \
                            any(abs(x2["total"] - x["total"]) > 1e-9 for x2, x in zip(t2, top)):
                        c6_bad += 1
                AB.clear_year_caches()
                _p3, t3 = GE._compute_yashin_top10(c, y, GE._get_ballon_candidates(c, y))
                if [x["player_id"] for x in t3] != [x["player_id"] for x in top]:
                    c6_bad += 1
            finally:
                GE._end_team_gc_run_cache()
            tlm = GE._season_team_league_matches(c, y)
            lmap = GE._season_team_league_map(c, y)
            ranks = GE._yashin_league_ranks(c, y)
            for x in pool:
                x["raw_sr"] = AB.save_rate(x.get("saves"), x.get("goals_conceded"))
                x["lid"] = lmap.get(x["team_id"])
                x["year"] = y
            # 5 — 계산 오류: 원자료 대조 + 공식 재계산 + 점수 합
            for x in top:
                if x["player_id"] >= 0:
                    r = c.execute("""SELECT matches, saves, goals_conceded, clean_sheets FROM hist.ai_player_season_stats
                                     WHERE player_id=? AND year=?""", (x["player_id"], y)).fetchone()
                    if not r or (r[0], r[1], r[2], r[3]) != (x["matches"], x["saves"], x["goals_conceded"],
                                                              x["clean_sheets"]):
                        c5_bad += 1
                if x["shots"] != (x["saves"] or 0) + (x["goals_conceded"] or 0):
                    c5_bad += 1
                if x["std_rate"] is not None and abs(x["std_rate"] - (x["raw_sr"] - x["env_shift"])) > 1e-9:
                    c5_bad += 1
                parts = x["s_save"] + x["s_gc"] + x["s_cs"] + x["s_team"] + x["s_intl"] + x["s_part"]
                if abs(parts - x["total"]) > 1e-3:
                    c5_bad += 1
                if not GE._meets_award_league_share(x["matches"], tlm.get(x["team_id"])):
                    c4_bad += 1
            mean = sum(e["std_rate"] * e["shots"] for e in pool if e["std_rate"] is not None) / \
                max(1, sum(e["shots"] for e in pool if e["std_rate"] is not None))
            for x in pool:
                if x["std_rate"] is not None and abs(x["shrunk"] - (x["std_rate"] * x["shots"] + mean * Y.SHRINK_SHOTS)
                                                      / (x["shots"] + Y.SHRINK_SHOTS)) > 1e-9:
                    c5_bad += 1
            # 1·2·3
            srs = [x["raw_sr"] for x in top if x["raw_sr"] is not None]
            sr_t10 += srs
            if top and top[0]["raw_sr"] is not None:
                sr_1.append(top[0]["raw_sr"])
            cc = AB.spearman(srs, list(range(1, len(srs) + 1)))
            if cc is not None:
                cors.append(cc)
            oc = AB.spearman([x["yashin_ovr"] for x in top], list(range(1, len(top) + 1)))
            if oc is not None:
                ovr_cors.append(oc)
            if srs and all(v >= 0.80 for v in srs):
                all80 += 1
            # 8 — 개인 점수만의 Top10
            pers = sorted(pool, key=lambda x: (-x["personal"], x["player_id"]))
            pers_ids = {x["player_id"] for x in AB._cap_top(pers, GE._YASHIN_MAX_PER_TEAM, 10)} if pers else set()
            if top and top[0]["player_id"] not in pers_ids:
                c8_bad += 1
            # 관찰
            st_sh = [x["shots"] for x in pool if x["share"] >= BACKUP_SHARE and x["shots"]]
            bk_sh = [x["shots"] for x in pool if x["share"] < BACKUP_SHARE and x["shots"]]
            shots_by_year.append((y, med(st_sh), med(bk_sh), len(st_sh), len(bk_sh)))
            w = top[0] if top else None
            if w:
                w["ovr_rank"] = 1 + sum(1 for x in pool if x["yashin_ovr"] > w["yashin_ovr"])
                w["team_rank"] = ranks.get(w["team_id"], (None,))[0]
            changes.append((y, old[0]["player_id"] if old else None, w["player_id"] if w else None,
                            len({x["player_id"] for x in old} & {x["player_id"] for x in top})))
            for i, x in enumerate(top):
                x["rank"] = i + 1
                rows_csv.append(x)
            top_all.append(top)
            pool_all += pool
            print(f"  {y}: GK 후보 {len(pool)} · 1위 {w['player_id'] if w else '-'} · {time.time() - t0:.0f}s")

        n = len(years)
        t10 = [x for t in top_all for x in t]
        winners = [t[0] for t in top_all if t]
        gc_pool = med([x["gc_per_match"] for x in pool_all if x["gc_per_match"] is not None])
        cs_pool = med([x["cs_per_match"] for x in pool_all if x["cs_per_match"] is not None])
        gc_t10 = med([x["gc_per_match"] for x in t10 if x["gc_per_match"] is not None])
        cs_t10 = med([x["cs_per_match"] for x in t10 if x["cs_per_match"] is not None])
        say(f"세이브: {os.path.abspath(a.db)} · {years[0]}~{years[-1]} ({n}시즌) · 자격 GK {len(pool_all)}명·시즌")
        say()
        say("합격 기준                                      결과")
        v1 = med(sr_t10)
        say(f"1 Top10 실제 선방률 중앙값 76~82%             {AB.pct_s(v1)} → {'통과' if v1 and 0.76 <= v1 <= 0.82 else '실패'}")
        b2 = sum(1 for v in sr_1 if v < 0.72)
        say(f"2 1위 실제 선방률 72% 미만 0회                 {b2}회 (1위 중앙값 {AB.pct_s(med(sr_1))}, 최저 "
            f"{AB.pct_s(min(sr_1) if sr_1 else None)}) → {'통과' if b2 == 0 else '실패'}")
        v3 = med(cors)
        say(f"3 선방률–순위 상관 ≤ −0.25                    {v3} → {'통과' if v3 is not None and v3 <= -0.25 else '실패'}")
        say(f"4 Top10 전원 출전 70% 이상                    위반 {c4_bad} → {'통과' if c4_bad == 0 else '실패'}")
        say(f"5 개인 기록 계산 오류                          {c5_bad}건 → {'통과' if c5_bad == 0 else '실패'}")
        say(f"6 재실행·순서 섞기 3회                         불일치 {c6_bad} → {'통과' if c6_bad == 0 else '실패'}")
        ok7 = gc_t10 < gc_pool and cs_t10 > cs_pool
        say(f"7 Top10 실점 {gc_t10:.2f} < 전체 {gc_pool:.2f} · 클린시트 {cs_t10:.3f} > 전체 {cs_pool:.3f}   "
            f"→ {'통과' if ok7 else '실패'}")
        say(f"8 1위가 개인 점수만의 Top10 안                 위반 {c8_bad} → {'통과' if c8_bad == 0 else '실패'}")
        say()
        say("관찰")
        say(f"  Top10 전원 선방률 80% 이상인 시즌 {all80}/{n}")
        lg1, lg10, gr1, gr10 = {}, {}, {}, {}
        for x in winners:
            k = lname.get(x["lid"], ("?", "?"))
            lg1[k[0]] = lg1.get(k[0], 0) + 1
            gr1[cgrade.get(k[1], "?")] = gr1.get(cgrade.get(k[1], "?"), 0) + 1
        for x in t10:
            k = lname.get(x["lid"], ("?", "?"))
            lg10[k[0]] = lg10.get(k[0], 0) + 1
            gr10[cgrade.get(k[1], "?")] = gr10.get(cgrade.get(k[1], "?"), 0) + 1
        lgp = {}
        for x in pool_all:
            k = lname.get(x["lid"], ("?", "?"))[0]
            lgp[k] = lgp.get(k, 0) + 1
        say("  리그별 1위: " + " · ".join(f"{k} {v}" for k, v in sorted(lg1.items(), key=lambda t: -t[1])))
        say("  리그별 Top10 진입(후보 비중): " + " · ".join(
            f"{k} {v} ({100 * lgp.get(k, 0) / len(pool_all):.0f}%)" for k, v in sorted(lg10.items(), key=lambda t: -t[1])))
        say("  등급별 1위 / Top10: " + " · ".join(f"{g} {gr1.get(g, 0)}/{gr10.get(g, 0)}" for g in ("SS", "S", "A")))
        envl = {}
        for x in pool_all:
            envl.setdefault(lname.get(x["lid"], ("?",))[0], []).append(x)
        say("  리그별 실제 → 표준 환경 선방률(후보 평균): " + " · ".join(
            f"{k} {S.mean(e['raw_sr'] for e in v):.3f}→{S.mean(e['std_rate'] for e in v):.3f}"
            for k, v in sorted(envl.items(), key=lambda t: -len(t[1]))[:7] if v))
        trk = [w.get("team_rank") for w in winners if w.get("team_rank")]
        say(f"  1위 소속팀 리그 순위: {sorted(trk)} · 리그 우승팀 GK 1위 {sum(1 for r in trk if r == 1)}/{n}")
        say(f"  1위 OVR 순위(그 시즌 GK 후보 중): {sorted(w.get('ovr_rank') for w in winners)} · "
            f"Top10 OVR–순위 상관 중앙값 {med(ovr_cors)}")
        bk_pool = sum(1 for x in pool_all if x["share"] < BACKUP_SHARE)
        say(f"  백업(출전 70~85%) — 후보 비중 {100 * bk_pool / len(pool_all):.0f}% · 1위 "
            f"{sum(1 for w in winners if w['share'] < BACKUP_SHARE)}/{n} · Top10 "
            f"{sum(1 for x in t10 if x['share'] < BACKUP_SHARE)}/{len(t10)}")
        say("  시즌별 슈팅 중앙값 주전(85%+) / 백업(70~85%) [인원]: " + " · ".join(
            f"{y % 100:02d}: {s1}/{s2} [{n1}/{n2}]" for y, s1, s2, n1, n2 in shots_by_year))
        rat = [s2 / s1 for _y, s1, s2, _a, _b in shots_by_year if s1 and s2]
        say(f"  백업/주전 슈팅 비율 범위 {min(rat):.2f}~{max(rat):.2f} (단위 테스트 조건 0.55~0.72)" if rat else "")
        say("  수상자 변화(예전 → 새, Top10 겹침): " + " · ".join(
            f"{y % 100:02d}: {o}→{nw}" + ("" if o == nw else "*") + f" ({ov})" for y, o, nw, ov in changes))
        say(f"  1위 바뀐 시즌 {sum(1 for _y, o, nw, _ov in changes if o != nw)}/{n}")
        comp = {k: S.mean(x[k] for x in t10) for k in ("s_save", "s_gc", "s_cs", "s_team", "s_intl", "s_part", "total")}
        say("  Top10 평균 점수 구성: " + " · ".join(
            f"{lab} {comp[k]:.2f} ({100 * comp[k] / comp['total']:.0f}%)" for k, lab in
            (("s_save", "선방"), ("s_gc", "실점"), ("s_cs", "클린시트"), ("s_team", "팀"), ("s_intl", "국제"),
             ("s_part", "출전"))) + f" · 합 {comp['total']:.2f}")
        say(f"소요 {time.time() - t0:.0f}s")
        AB.write_csv(os.path.join(out_dir, "yashin_top10_new.csv"), rows_csv,
                     ["year", "rank", "player_id", "team_id", "yashin_ovr", "matches", "share", "saves", "goals_conceded",
                      "clean_sheets", "raw_sr", "env_shift", "std_rate", "shots", "shrunk", "s_save", "s_gc", "s_cs",
                      "s_team", "s_intl", "s_part", "total", "personal"])
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