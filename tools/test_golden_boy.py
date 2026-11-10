# -*- coding: utf-8 -*-
"""[2026-11] 골든보이 새 채점 단위 테스트.

A. 세이브 없이(항상 실행)
  A1 발롱도르 채점 불변 — ballon_award.score_season 기본값 결과가 수정 전 모듈(git HEAD의 ballon_award.py)과 완전히 같다
  A2 골든보이 출전 점수 — 리그 출전 50% → 0, 75% → 0.25, 100% → 0.5 (발롱도르는 70% 미만 0)
  A3 골든보이 리그 성적 × 리그 출전 비율 — 국내컵·대륙대회 몫은 그대로
  A4 50% 자격 경계(정수 비교) — 38경기 19/18, 34경기 17/16, 팀 경기 수 없음 = 38경기
B. 세이브로(--db를 주면) — 복사본에서만, 끝나면 지운다
  B1 후보 자격 — 전원 20세 이하 · 유럽 · 1부 · 리그 등급 SS/S/A · 출전 50% 이상 · 대기/전력외 아님
  B2 이전 수상자 제외 — 그 해 1위를 전년도 수상자로 꾸미면 그 선수가 빠진다
  B3 초기 리그 파워 — league_power_initial 행이 있고, league_power(시작연도−1)를 다시 계산해 덮어쓰거나
     파워랭킹 첫 조회(ensure_initial_team_power_ranking)를 해도 표와 첫 시즌 골든보이 결과가 그대로
  B4 내 선수 — AI 후보 한 명과 같은 기록·OVR·역할로 꾸민 내 선수가 같은 자격 판정·같은 점수(선수 번호만 다름),
     출전 40%면 빠지고 유럽 밖 리그면 빠진다
  B5 코파 트로피 — 후보 = 전 대륙 같은 조건(골든보이 후보는 그중 유럽), 유럽 밖 후보가 있다, 전년도 수상자로 꾸며도
     빠지지 않는다(반복 수상), 이전 골든보이 수상자도 빠지지 않는다
  python tools/test_golden_boy.py [--db 경로/headless.db]"""
import _path  # noqa: F401
import argparse
import contextlib
import copy
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile

import ballon_award as B

FAILS = []


def ok(cond, msg):
    print(("  통과 " if cond else "  실패 ") + msg)
    if not cond:
        FAILS.append(msg)


def _entry(pid, pos="ST", g=10, a=5, m=30, share=0.8, team=1, rank=3, **kw):
    e = dict(player_id=pid, team_id=team, position=pos, goals=g, assists=a, matches=m, full_season=38,
             goal_env=1.0, team_attack=1.0, team_gc=1.2, ovr=90, share=share, league_level=0.9, league_rank=rank,
             league_n=20, relegated=False, league_exp=5, cup_v=0.3, cont_v=0.4, intl_v=0.2,
             comps=[{"kind": "cup", "goals": 2, "assists": 1, "matches": 4, "level": None, "env": 1.0},
                    {"kind": "cont", "goals": 3, "assists": 1, "matches": 8, "level": 1.0, "env": 1.0}],
             saves=None, goals_conceded=None, gk_save=None)
    e.update(kw)
    return e


def _batch():
    out = []
    pos = ["ST", "LW", "CAM", "CM", "CDM", "CB", "LB", "GK"]
    for i in range(40):
        p = pos[i % len(pos)]
        out.append(_entry(i + 1, p, g=(i * 7) % 25, a=(i * 5) % 14, m=20 + i % 19, share=0.55 + (i % 9) * 0.05,
                          team=i % 6, rank=1 + i % 12, ovr=80 + i % 18, league_level=0.6 + (i % 5) * 0.1,
                          gk_save=0.78 if p == "GK" else None))
    return out


def part_a():
    print("A1 발롱도르 채점 불변(수정 전 모듈과 비교)")
    try:
        _old_fp = os.environ.get("GB_OLD_BALLON")    # 저장소 밖(테스트용 사본)에서 돌릴 때 수정 전 파일 경로
        if _old_fp:
            with open(_old_fp, encoding="utf-8") as f:
                old_src = f.read()
        else:
            old_src = subprocess.run(["git", "-C", _path.ROOT, "show", "HEAD:ballon_award.py"], capture_output=True,
                                     text=True, check=True).stdout
        tmpd = tempfile.mkdtemp(prefix="gbtest_")
        try:
            fp = os.path.join(tmpd, "ballon_award_old.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(old_src)
            spec = importlib.util.spec_from_file_location("ballon_award_old", fp)
            OLD = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(OLD)
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
        a, b = OLD.score_season(_batch()), B.score_season(_batch())
        ok([(x["player_id"], x["total"]) for x in a] == [(x["player_id"], x["total"]) for x in b]
           and all(x[k] == y[k] for x, y in zip(a, b) for k in ("personal", "s_team", "s_part", "s_prod", "s_ovr")),
           f"score_season 기본값 = 수정 전 모듈 (후보 {len(a)}명, 순서·총점·항목 완전 동일)")
        ok([x["player_id"] for x in OLD.top30(a)] == [x["player_id"] for x in B.top30(b)], "top30 동일")
    except subprocess.CalledProcessError:
        print("  건너뜀 — 수정 전 ballon_award.py를 읽지 못했다(저장소 밖이면 GB_OLD_BALLON=파일 경로)")

    print("A2 골든보이 출전 점수")
    for sh, want_g, want_b in ((0.40, 0.0, 0.0), (0.50, 0.0, 0.0), (0.60, 0.1, 0.0), (0.75, 0.25, 0.0833333),
                               (1.00, 0.5, 0.5)):
        g = B.score_golden([_entry(1, share=sh)])[0]["s_part"]
        b = B.score_season([_entry(1, share=sh)])[0]["s_part"]
        ok(abs(g - want_g) < 1e-9 and abs(b - want_b) < 1e-6, f"출전 {sh:.0%}: 골든보이 {g:.4f}(기대 {want_g}) · 발롱 {b:.4f}")

    print("A3 골든보이 리그 성적 × 리그 출전 비율")
    for sh in (0.55, 0.8, 1.0):
        g = B.score_golden([_entry(1, share=sh)])[0]
        b = B.score_season([_entry(1, share=sh)])[0]
        ok(abs(g["league_v"] - b["league_v"] * sh) < 1e-12 and g["cup_lv"] == b["cup_lv"] and g["cont_lv"] == b["cont_lv"],
           f"출전 {sh:.0%}: 리그 {g['league_v']:.4f} = 발롱 {b['league_v']:.4f} × {sh} · 컵·대륙 그대로")

    print("A4 50% 자격 경계")
    import game_engine as GE
    f = GE._meets_golden_boy_league_share
    ok(f(19, 38) and not f(18, 38), "38경기: 19 자격 · 18 불가")
    ok(f(17, 34) and not f(16, 34), "34경기: 17 자격 · 16 불가")
    ok(f(19, None) and not f(18, None), "팀 경기 수 없음 = 38경기")
    ok(f(15, 30) and not f(14, 30), "30경기: 15 자격 · 14 불가")


def part_b(db):
    import database
    import game_engine as GE
    import power_ranking as PR
    import award_baseline as AB
    from constants import get_country_league_grade
    with contextlib.redirect_stdout(io.StringIO()):
        conn, tmp = AB.prepare(db)
    c = conn.cursor()
    try:
        gsy = database.get_game_start_year()
        year = c.execute("SELECT MAX(year) FROM hist.ai_player_season_stats").fetchone()[0]
        print(f"B 세이브 {db} (시작 {gsy}, 마지막 시즌 {year})")
        # 지난 시즌 재계산 보정(은퇴 선수·그 시즌 리그·이적생)과 그 시즌 나이 — golden_boy_check와 같다
        import golden_boy_check as GC
        AB.patch_for_past_years(c)
        GC._age_table(c)
        GC._set_ages(c, year)
        AB.clear_year_caches()
        GE._begin_team_gc_run_cache(year)
        try:
            cands = GE._get_golden_boy_candidates(c, year)
            tlm = GE._season_team_league_matches(c, year)
            lmap = GE._season_team_league_map(c, year)
            info = {r[0]: (r[1], r[2], r[3]) for r in c.execute(
                "SELECT l.id, l.tier, cn.name, cn.continent FROM leagues l JOIN countries cn ON cn.id=l.country_id")}
            bad = []
            for x in cands:
                tier, cname, cont = info[lmap.get(x["team_id"])]
                age = c.execute("SELECT age FROM ai_players WHERE id=?", (x["player_id"],)).fetchone()[0]
                if not (age <= 20 and cont == "유럽" and tier == 1 and get_country_league_grade(cname) in ("SS", "S", "A")
                        and x["matches"] * 2 >= tlm[x["team_id"]] and x.get("role") not in GE._AWARD_EXCLUDED_ROLES):
                    bad.append(x["player_id"])
            print("B1 후보 자격")
            ok(cands and not bad, f"후보 {len(cands)}명 전원 조건 충족 (위반 {len(bad)})")
            _, top = GE._compute_golden_boy_top10(c, year)
            ok(len(top) == min(10, len(cands)), f"Top10 {len(top)}명")

            print("B5 코파 트로피")
            kc = GE._get_golden_boy_candidates(c, year, continent=None)
            bad_k = []
            for x in kc:
                tier, cname, cont = info[lmap.get(x["team_id"])]
                age = c.execute("SELECT age FROM ai_players WHERE id=?", (x["player_id"],)).fetchone()[0]
                if not (age <= 20 and tier == 1 and get_country_league_grade(cname) in ("SS", "S", "A")
                        and x["matches"] * 2 >= tlm[x["team_id"]] and x.get("role") not in GE._AWARD_EXCLUDED_ROLES):
                    bad_k.append(x["player_id"])
            eu = {x["player_id"] for x in kc if info[lmap.get(x["team_id"])][2] == "유럽"}
            ok(kc and not bad_k, f"후보 {len(kc)}명 전원 조건 충족 (위반 {len(bad_k)})")
            ok(eu == {x["player_id"] for x in cands}, f"그중 유럽 {len(eu)}명 = 골든보이 후보와 같음")
            ok(len(kc) > len(eu), f"유럽 밖 후보 {len(kc) - len(eu)}명")
            _, ktop = GE._compute_kopa_top10(c, year)
            kw = ktop[0]["player_id"]
            # 꾸민 수상 기록은 상 기록이 하나도 없는 해에 넣는다(여러 시즌 세이브는 전년도 1위 자리가 이미 차 있다)
            fy = min(c.execute("SELECT MIN(year) FROM season_individual_awards").fetchone()[0] or gsy, gsy) - 1
            for aw in (GE._KOPA_AWARD, GE._GOLDEN_BOY_AWARD):
                c.execute("""INSERT INTO season_individual_awards(year, award_type, rank, player_id, category)
                             VALUES(?,?,1,?,'world')""", (fy, aw, kw))
            _, ktop2 = GE._compute_kopa_top10(c, year)
            ok([(x["player_id"], x["total"]) for x in ktop2] == [(x["player_id"], x["total"]) for x in ktop],
               f"이전 코파·골든보이 수상자로 꾸민 {kw}도 그대로 1위(반복 수상 허용)")
            c.execute("DELETE FROM season_individual_awards WHERE year=? AND player_id=? AND award_type IN (?,?)",
                      (fy, kw, GE._KOPA_AWARD, GE._GOLDEN_BOY_AWARD))

            print("B2 이전 수상자 제외")
            w = top[0]["player_id"]
            c.execute("""INSERT INTO season_individual_awards(year, award_type, rank, player_id, category)
                         VALUES(?,?,1,?,'world')""", (fy, GE._GOLDEN_BOY_AWARD, w))
            _, top2 = GE._compute_golden_boy_top10(c, year)
            ok(w not in {x["player_id"] for x in top2} and top2[0]["player_id"] == top[1]["player_id"],
               f"이전 수상자로 꾸민 {w} 제외 → 1위가 원래 2위({top[1]['player_id']})")
            c.execute("DELETE FROM season_individual_awards WHERE year=? AND award_type=? AND player_id=?",
                      (fy, GE._GOLDEN_BOY_AWARD, w))
        finally:
            GE._end_team_gc_run_cache()

        print("B3 초기 리그 파워")
        init0 = PR.get_initial_league_power(c, gsy - 1)
        ok(len(init0) > 0, f"league_power_initial {gsy - 1}년 {len(init0)}개 리그")
        if year == gsy:
            AB.clear_year_caches()
            GE._begin_team_gc_run_cache(year)
            try:
                _, t_a = GE._compute_golden_boy_top10(c, year)
                PR.compute_league_power(conn, gsy - 1)         # 연말·화면 조회가 하는 덮어쓰기
                c.execute("DELETE FROM hist.team_power_rankings WHERE ranking_year=?", (gsy,))
                PR.ensure_initial_team_power_ranking(conn)      # 파워랭킹 화면 첫 조회
                AB.clear_year_caches()
                _, t_b = GE._compute_golden_boy_top10(c, year)
            finally:
                GE._end_team_gc_run_cache()
            ok(PR.get_initial_league_power(c, gsy - 1) == init0, "다시 계산·첫 조회 뒤에도 초기 리그 파워 표 그대로")
            ok([(x["player_id"], x["total"]) for x in t_a] == [(x["player_id"], x["total"]) for x in t_b],
               "첫 시즌 골든보이 결과 그대로(화면을 열었는지와 무관)")
            lv = {x["league_level"] for x in t_a}
            ok(lv != {1.0}, f"첫 시즌 리그 수준이 1.0 고정이 아님 (Top10 리그 수준 {sorted(round(v, 2) for v in lv)})")
        else:
            print("  (첫 시즌 세이브가 아니라 결과 비교는 건너뜀 — 1시즌 세이브로 실행)")

        print("B4 내 선수")
        AB.clear_year_caches()
        GE._begin_team_gc_run_cache(year)
        try:
            base = GE._get_golden_boy_candidates(c, year)
            pick = None
            # 국내컵 말고 다른 대회 기록이 없는 AI 후보 — 내 선수는 대회 기록을 경기 표(my_played)에서 읽으므로, 그 팀
            # 국내컵 경기에 같은 출전·골·도움을 표시해 맞춘다(아래 set_me).
            for x in base:
                comps = c.execute("""SELECT COUNT(*) FROM hist.ai_player_season_stats_by_comp
                                     WHERE player_id=? AND year=? AND competition != 'cup'""",
                                  (x["player_id"], year)).fetchone()[0]
                intl = c.execute("""SELECT COUNT(*) FROM intl_squad s JOIN intl_tournaments t ON t.id=s.tournament_id
                                    WHERE s.player_id=? AND t.year=? AND s.appearances>0""",
                                 (x["player_id"], year)).fetchone()[0]
                # 상·하반기 포지션이 같은 선수(내 선수는 하반기 기록 없이 시즌 포지션으로만 판정한다)
                half = c.execute("""SELECT h.position = p.position AND COALESCE(h.role,'') = COALESCE(p.role,'')
                                    FROM hist.ai_player_position_history_half h
                                    JOIN hist.ai_player_position_history p ON p.player_id=h.player_id AND p.year=h.year
                                    WHERE h.player_id=? AND h.year=?""", (x["player_id"], year)).fetchone()
                if not comps and not intl and (half is None or half[0]) and x["position"] != "GK" \
                        and not x.get("cl_matches"):
                    pick = x
                    break
            if pick is None:
                print("  건너뜀 — 국내컵 외 대회 기록이 없는 AI 후보가 없다")
                return
            pid = pick["player_id"]
            ovr = c.execute("SELECT ovr FROM hist.ai_player_ovr_history WHERE player_id=? AND year=?", (pid, year)).fetchone()[0]
            age = c.execute("SELECT age FROM ai_players WHERE id=?", (pid,)).fetchone()[0]
            ph = c.execute("SELECT position, role FROM hist.ai_player_position_history WHERE player_id=? AND year=?",
                           (pid, year)).fetchone()

            def set_me(matches, team_id):
                c.execute("DELETE FROM my_player_season_stats WHERE year=?", (year,))
                c.execute("""INSERT INTO my_player_season_stats(year, team_id, matches, goals, assists, rating,
                             saves, goals_conceded, clean_sheets) VALUES(?,?,?,?,?,?,?,?,?)""",
                          (year, team_id, matches, pick["goals"], pick["assists"], pick["rating"], pick["saves"],
                           pick["goals_conceded"], pick["clean_sheets"]))
                c.execute("UPDATE my_player SET position=?, nationality=?, age=? WHERE id=1",
                          (pick["position"], pick["nationality"], age))
                c.execute("DELETE FROM my_player_position_history WHERE year=?", (year,))
                c.execute("INSERT INTO my_player_position_history(year, position, role) VALUES(?,?,?)",
                          (year, ph[0] if ph else pick["position"], ph[1] if ph else None))
                c.execute("INSERT OR REPLACE INTO my_player_ovr_history(year, ovr) VALUES(?,?)", (year, ovr))
                c.execute("""UPDATE cup_matches SET my_played=0, my_goals=0, my_assists=0
                             WHERE tournament_id IN (SELECT id FROM cup_tournaments WHERE year=?)""", (year,))
                if cup and team_id == pick["team_id"]:
                    ids = [r[0] for r in c.execute(
                        """SELECT m.id FROM cup_matches m JOIN cup_tournaments t ON t.id=m.tournament_id
                           WHERE t.year=? AND (m.home_team_id=? OR m.away_team_id=?) ORDER BY m.id""",
                        (year, team_id, team_id)).fetchall()][:cup[0]]
                    for k, mid in enumerate(ids):
                        c.execute("UPDATE cup_matches SET my_played=1, my_goals=?, my_assists=? WHERE id=?",
                                  (cup[1] if k == 0 else 0, cup[2] if k == 0 else 0, mid))

            cup = c.execute("""SELECT matches, goals, assists FROM hist.ai_player_season_stats_by_comp
                               WHERE player_id=? AND year=? AND competition='cup'""", (pid, year)).fetchone()
            set_me(pick["matches"], pick["team_id"])
            AB.clear_year_caches()
            cs = GE._get_golden_boy_candidates(c, year)
            me = [x for x in cs if x["player_id"] == GE._SIA_MY_PLAYER_ID]
            ok(len(me) == 1, f"같은 기록의 내 선수가 후보에 들어감 (AI {pid}: {pick['position']} {pick['matches']}경기 "
                             f"{pick['goals']}골 {pick['assists']}도움, 국내컵 {tuple(cup) if cup else None})")
            if me:
                ok(me[0]["position"] == pick["position"], f"포지션 판정 같음 ({me[0]['position']})")
            if me:
                import ballon_award as BA
                rows = GE._ballon_inputs(c, year, [pick, me[0]], initial_first_season=True)
                for r in rows:
                    r["team_id"] = (r["team_id"], r["player_id"])    # 팀 안 배분이 서로 영향 주지 않게 따로
                s = BA.score_golden(copy.deepcopy(rows))
                ai = next(x for x in s if x["player_id"] == pid)
                my = next(x for x in s if x["player_id"] == GE._SIA_MY_PLAYER_ID)
                ok(abs(ai["total"] - my["total"]) < 1e-9 and abs(ai["personal"] - my["personal"]) < 1e-9,
                   f"AI {pid} 총점 {ai['total']:.4f} = 내 선수 {my['total']:.4f}")
            tl = tlm[pick["team_id"]]
            set_me((tl * 2) // 5, pick["team_id"])                                 # 40%
            AB.clear_year_caches()
            ok(not any(x["player_id"] == GE._SIA_MY_PLAYER_ID for x in GE._get_golden_boy_candidates(c, year)),
               f"내 선수 출전 {(tl * 2) // 5}/{tl}(40%) → 후보 아님")
            far = c.execute("""SELECT t.id FROM teams t JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id
                               WHERE l.tier=1 AND cn.continent!='유럽' LIMIT 1""").fetchone()[0]
            set_me(pick["matches"], far)
            AB.clear_year_caches()
            ok(not any(x["player_id"] == GE._SIA_MY_PLAYER_ID for x in GE._get_golden_boy_candidates(c, year)),
               "내 선수 유럽 밖 1부 → 후보 아님")
        finally:
            GE._end_team_gc_run_cache()
    finally:
        os.chdir(_path.ROOT)
        try:
            database.reset_conn_pool()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    a = ap.parse_args()
    part_a()
    if a.db:
        part_b(os.path.abspath(a.db))
    print(f"\n결과: {'전부 통과' if not FAILS else f'실패 {len(FAILS)}건'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()