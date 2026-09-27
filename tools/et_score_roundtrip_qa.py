"""et_score_roundtrip_qa.py — 연장 스코어 왕복 검사 (2026-09 신설)

스키마 검사(et_score_schema_qa)와 바인딩 검사(et_score_binding_qa)가
"컬럼이 있는가 / ? 개수가 맞는가"만 보는 데 비해, 이 스크립트는 실제
값이 **엔진 → DB → 다시 읽기**까지 뜻이 안 변한 채로 왕복하는지 본다.
확인하는 불변식은 네 가지다:

  1. went_extra_time=0 이면 최종 스코어 == 90분 스코어
     (연장에 안 갔으니 당연 — 깨지면 어딘가에서 값이 섞인 것)
  2. went_extra_time=1 이면 최종 스코어 >= 90분 스코어 (양 팀 각각)
     연장 득점은 더해질 뿐 깎이지 않는다
  3. went_extra_time=1 이면 90분 시점엔 반드시 동점
     연장은 "정규시간 종료 시 동점"일 때만 들어간다
  4. DB에 넣고 다시 읽은 값이 엔진이 준 값과 완전히 같다
     (et_score_values의 -1 폴백 규칙 포함)

교체 시스템·평점 생성도 같이 붙었는지 확인한다(국내 슈퍼컵에 새로
연결한 부분 — player_ratings가 22명 채워지는지).

사용법:
    python tools/et_score_roundtrip_qa.py --db <세이브.db> [--n 60]
"""
import _path  # noqa: F401
import argparse
import os
import random
import shutil
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="기준으로 쓸 세이브 파일(읽기만, 사본으로 작업)")
    ap.add_argument("--n", type=int, default=60, help="시뮬레이션할 경기 수")
    ap.add_argument("--work", default=None)
    args = ap.parse_args()

    work = os.path.abspath(args.work or "et_score_roundtrip_qa")
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "rt.db")
    shutil.copy(os.path.abspath(args.db), db)
    hsrc = os.path.splitext(os.path.abspath(args.db))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")

    import database
    database.USE_MEMORY_DB = False
    database.DB_PATH = db
    os.chdir(work)
    database.reset_conn_pool()
    database.init_db()

    from database import (get_conn, ET_SCORE_SET_SQL, et_score_values,
                         ET_SCORE_MATCH_TABLES)
    from match_sim.tactical_engine import simulate_my_match
    from competition.competition_common import merge_my_slot
    import game_engine as ge

    conn = get_conn()
    # 같은 리그의 실제 팀들로 대진을 만든다(라인업이 실제로 채워져 있는 팀).
    teams = [r["id"] for r in conn.execute(
        """SELECT t.id FROM teams t
           WHERE (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) >= 18
           ORDER BY t.id LIMIT 40""")]
    if len(teams) < 2:
        print("실패: 선수단이 채워진 팀이 2개 미만 — 이 세이브로는 검사할 수 없습니다.")
        return 1
    c = conn.cursor()
    forms = {tid: ge._team_formation(c, tid) for tid in teams[:2] + teams[2:8]}
    conn.close()

    random.seed(20260927)
    n_et = 0
    n_ratings_ok = 0
    n_subs = 0
    violations = []

    # 왕복 검사용 임시 표 — 실제 대회 표와 같은 컬럼 구성을 쓰되, 검사가
    # 세이브 데이터를 건드리지 않도록 별도 표에 넣고 읽는다.
    conn = get_conn()
    conn.execute("DROP TABLE IF EXISTS _et_rt_probe")
    conn.execute(f"""CREATE TABLE _et_rt_probe(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        {database._ET_SCORE_DDL})""")
    conn.commit()
    conn.close()

    home_id = teams[0]
    for i in range(args.n):
        away_id = teams[1 + (i % (len(teams) - 1))]
        if away_id == home_id:
            continue
        conn = get_conn()
        fm_h = forms.get(home_id) or ge._team_formation(conn.cursor(), home_id)
        fm_a = forms.get(away_id) or ge._team_formation(conn.cursor(), away_id)
        conn.close()
        try:
            sim = simulate_my_match(home_id, away_id, fm_h, fm_a,
                                    home_adv=0.0, extra_time=True)
        except Exception as e:
            violations.append(f"[{i}] 시뮬 실패 {type(e).__name__}: {e}")
            break

        hs, as_ = sim["home_score"], sim["away_score"]
        hs90 = sim.get("home_score_90", hs)
        as90 = sim.get("away_score_90", as_)
        went_et = bool(sim.get("went_extra_time"))
        if went_et:
            n_et += 1

        # ── 불변식 1~3: 엔진 출력 자체의 정합성 ──────────────────
        if not went_et:
            if (hs, as_) != (hs90, as90):
                violations.append(
                    f"[{i}] 연장 안 갔는데 최종 {hs}-{as_} != 90분 {hs90}-{as90}")
        else:
            if hs < hs90 or as_ < as90:
                violations.append(
                    f"[{i}] 연장 갔는데 최종 {hs}-{as_} < 90분 {hs90}-{as90}")
            if hs90 != as90:
                violations.append(
                    f"[{i}] 연장 갔는데 90분 시점이 동점이 아님: {hs90}-{as90}")

        # 평점·교체가 실제로 생성됐는지(국내 슈퍼컵에 새로 붙인 부분).
        #
        # [기준 주의] "양 팀 11명씩 22명"이 아니다 — 교체 시스템이 붙은
        # 뒤로 이 배열은 선발 11명 + 그 경기에 실제로 투입된 교체 선수까지
        # 담는다(각 항목에 minutes/on_min/off_min이 있다). 그래서 팀당
        # 11명 이상이면 정상이고, 오히려 정확히 11명만 나오면 교체가 하나도
        # 반영되지 않은 것이므로 의심해야 한다.
        pr = {"home": sim.get("home_player_ratings") or [],
              "away": sim.get("away_player_ratings") or []}
        _h = [x for x in pr["home"] if x]
        _a = [x for x in pr["away"] if x]
        _need = {"rating", "goals", "assists", "minutes", "position"}
        if (len(_h) >= 11 and len(_a) >= 11
                and all(_need <= set(r.keys()) for r in _h + _a)):
            n_ratings_ok += 1
        else:
            violations.append(
                f"[{i}] 평점 배열 이상: home {len(_h)}명 / away {len(_a)}명 "
                f"(11명 이상이어야 하고 {sorted(_need)} 키를 전부 가져야 함)")
        n_subs += len(sim.get("home_subs") or []) + len(sim.get("away_subs") or [])

        # merge_my_slot이 폴백(None)에서도, 정상 경로에서도 안 깨지는지
        merge_my_slot(pr, True, "CM",
                      {"id": None, "name": "나", "position": None, "ovr": 70,
                       "goals": 0, "assists": 0, "shots": 1, "shots_on": 0,
                       "saves": 0, "is_gk": False, "rating": 7.0, "is_me": True},
                      hs, as_)
        merge_my_slot(None, True, "CM", {}, hs, as_)   # 폴백 경로 no-op 확인

        # ── 불변식 4: DB 왕복 ───────────────────────────────────
        vals = et_score_values(hs90, as90, went_et)
        conn = get_conn()
        cur = conn.execute("INSERT INTO _et_rt_probe(home_score, away_score) VALUES(?,?)",
                           (hs, as_))
        rid = cur.lastrowid
        conn.execute(f"UPDATE _et_rt_probe SET {ET_SCORE_SET_SQL} WHERE id=?",
                     (*vals, rid))
        conn.commit()
        back = conn.execute(
            "SELECT home_score_90, away_score_90, went_extra_time FROM _et_rt_probe WHERE id=?",
            (rid,)).fetchone()
        conn.close()
        got = (back["home_score_90"], back["away_score_90"], back["went_extra_time"])
        if got != vals:
            violations.append(f"[{i}] DB 왕복 불일치: 넣은 값 {vals} != 읽은 값 {got}")

    # et_score_values의 폴백 규칙 단독 검사
    if et_score_values(None, None, False) != (-1, -1, 0):
        violations.append("et_score_values(None, None, False)가 (-1,-1,0)이 아님")
    if et_score_values(None, 2, True) != (-1, -1, 0):
        violations.append("한쪽만 None일 때도 (-1,-1,0)으로 떨어져야 함")
    if et_score_values(1, 1, True) != (1, 1, 1):
        violations.append("정상 값이 그대로 통과하지 않음")

    conn = get_conn()
    conn.execute("DROP TABLE IF EXISTS _et_rt_probe")
    conn.commit()
    conn.close()

    print(f"검사 대상 표 {len(ET_SCORE_MATCH_TABLES)}개 (스키마는 et_score_schema_qa 담당)")
    print(f"시뮬레이션 {args.n}경기 — 연장 진입 {n_et}경기, "
          f"평점 배열 정상(팀당 11명+교체) {n_ratings_ok}경기, 교체 합계 {n_subs}명")
    print()
    if violations:
        print("=== 실패 ===")
        for v in violations[:30]:
            print(f"  {v}")
        if len(violations) > 30:
            print(f"  ... 외 {len(violations) - 30}건")
        return 1
    print("=== 전체 통과: 90분/최종 스코어 불변식 4개와 DB 왕복이 모두 일치 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())