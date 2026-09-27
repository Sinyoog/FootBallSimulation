"""po_extra_time_qa.py — 승강 PO 단판 연장 E2E 검사 (2026-09 신설)

[신민용 확정] "승격 PO도 이제 연장 가야 돼, 단판전이라."

simulate_my_po_match를 **실제로 호출해서** 다음을 확인한다. 이 경로는
승강 PO에 내 팀이 걸린 시즌에만 실행되므로 평소 플레이로는 거의 안 밟힌다
— 그래서 fixture(po_tournaments + po_matches 한 쌍)를 직접 심어서 부른다.

확인 항목:
  1. 크래시 없이 끝나는가 (전술엔진 연결 + import 전부)
  2. po_matches에 home_score/away_score가 채워지는가
  3. 90분 스코어 3컬럼이 정합하게 기록되는가
       · went_extra_time=1 → 최종 >= 90분, 90분 시점 동점
       · went_extra_time=0 → 최종 == 90분 (또는 폴백이면 -1/-1/0)
  4. 연장까지 갔는데도 동점이면 pso_winner가 찍히는가
  5. 경기 상세(match_details)에 player_ratings / possession_log /
     score_90·went_extra_time·subs가 실제로 들어갔는가
     (여태 PO만 이 4개가 비어 라인업+평점 섹션이 안 떴다)
  6. po_history에 커리어 기록이 남는가

사용법:
    python tools/po_extra_time_qa.py --db <세이브.db> [--n 30]
"""
import _path  # noqa: F401
import argparse
import json
import os
import random
import shutil
import sys


def _pick_opponent(conn, my_tid):
    row = conn.execute(
        """SELECT t.id FROM teams t
           WHERE t.id <> ?
             AND (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) >= 18
           ORDER BY t.id LIMIT 1""", (my_tid,)).fetchone()
    return row["id"] if row else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--n", type=int, default=30, help="반복 경기 수")
    ap.add_argument("--work", default=None)
    args = ap.parse_args()

    work = os.path.abspath(args.work or "po_extra_time_qa")
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "po.db")
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

    from database import get_conn
    import game_engine as ge
    import promotion_playoff_engine as ppe
    from constants import PLAYOFF_WEEK, week_to_day

    p = ge.get_player()
    if not p:
        print("실패: 이 세이브에 my_player가 없습니다 — PO 경기를 부를 수 없습니다.")
        return 1
    st = ge.get_state()
    my_tid = p.get("current_team_id", 0)
    if not my_tid:
        # 새로 시작한 세이브(무소속 유망주)로도 이 경로를 검사할 수 있게,
        # 사본에서만 팀을 하나 배정한다 — 원본 세이브는 건드리지 않는다
        # (이 스크립트는 항상 --db의 사본에서 작업한다).
        conn = get_conn()
        row = conn.execute(
            """SELECT t.id FROM teams t
               WHERE (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) >= 18
               ORDER BY t.id LIMIT 1""").fetchone()
        conn.close()
        if not row:
            print("실패: 선수단이 채워진 팀이 없습니다.")
            return 1
        ge.update_player(current_team_id=row["id"])
        p = ge.get_player()
        my_tid = p.get("current_team_id", 0)
        print(f"[fixture] 무소속 선수라 검사용으로 팀 배정 (team_id={my_tid}) — 사본에서만 적용")
    if not my_tid:
        print("실패: 팀 배정에 실패했습니다.")
        return 1

    conn = get_conn()
    opp_tid = _pick_opponent(conn, my_tid)
    year = st["current_year"]
    my_name = conn.execute("SELECT name FROM teams WHERE id=?", (my_tid,)).fetchone()["name"]
    opp_name = conn.execute("SELECT name FROM teams WHERE id=?", (opp_tid,)).fetchone()["name"]
    conn.close()
    if not opp_tid:
        print("실패: 상대로 쓸 팀을 못 찾았습니다.")
        return 1

    print(f"내 팀 {my_name}(id={my_tid}) vs {opp_name}(id={opp_tid}), {year}년")
    print(f"선수: {p.get('name')} / {p.get('position')} / OVR {p.get('ovr')}")
    print()

    random.seed(20260927)
    base_day = week_to_day(PLAYOFF_WEEK)
    n_et = 0
    n_pso = 0
    n_ratings = 0
    n_extra = 0
    n_hist = 0
    n_played = 0
    violations = []

    for i in range(args.n):
        day = base_day
        conn = get_conn()
        # fixture: 그 해의 진행 중 PO 토너먼트 + 내 팀이 들어간 단판 경기 하나.
        # is_boundary=0으로 둬서 _finalize_boundary_match(실제 승강 확정 →
        # teams.league_id 변경)까지 타지 않게 한다 — 이 검사는 경기 시뮬과
        # 기록 저장만 보기 때문이고, 세이브 사본의 리그 구성을 흔들지 않는다.
        cur = conn.execute(
            """INSERT INTO po_tournaments(year, upper_league_id, lower_league_id,
                                          rule_id, status, my_in, my_team_id)
               VALUES(?,?,?,?,?,?,?)""",
            (year, 0, 0, "bracket2", "pending", 1, my_tid))
        t_id = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO po_matches(tournament_id, match_key, day,
                                      home_team_id, away_team_id, is_boundary, is_my)
               VALUES(?,?,?,?,?,?,?)""",
            (t_id, "F", day, my_tid, opp_tid, 0, 1))
        m_id = cur.lastrowid
        n_hist_before = conn.execute("SELECT COUNT(*) c FROM po_history").fetchone()["c"]
        n_detail_before = conn.execute("SELECT COUNT(*) c FROM match_details").fetchone()["c"]
        conn.commit()
        conn.close()

        try:
            ppe.simulate_my_po_match(PLAYOFF_WEEK, ge.get_player(), day=day)
        except Exception as e:
            import traceback
            violations.append(f"[{i}] simulate_my_po_match 예외 {type(e).__name__}: {e}")
            traceback.print_exc()
            break

        conn = get_conn()
        row = conn.execute("SELECT * FROM po_matches WHERE id=?", (m_id,)).fetchone()
        hs, as_ = row["home_score"], row["away_score"]
        h90, a90, wet = row["home_score_90"], row["away_score_90"], row["went_extra_time"]

        # 2. 스코어가 채워졌는가
        if hs == -1 or as_ == -1:
            violations.append(f"[{i}] 스코어가 안 채워짐: {hs}-{as_}")

        # 3. 90분 스코어 정합성
        if wet:
            n_et += 1
            if h90 < 0 or a90 < 0:
                violations.append(f"[{i}] 연장인데 90분 스코어가 미기록: {h90}-{a90}")
            else:
                if hs < h90 or as_ < a90:
                    violations.append(
                        f"[{i}] 연장인데 최종 {hs}-{as_} < 90분 {h90}-{a90}")
                if h90 != a90:
                    violations.append(
                        f"[{i}] 연장인데 90분 시점이 동점 아님: {h90}-{a90}")
        else:
            if h90 >= 0 and (hs, as_) != (h90, a90):
                violations.append(
                    f"[{i}] 연장 아닌데 최종 {hs}-{as_} != 90분 {h90}-{a90}")

        # 4. 최종까지 동점이면 승부차기
        if hs == as_:
            if not row["pso_winner"]:
                violations.append(f"[{i}] 최종 {hs}-{as_} 동점인데 pso_winner가 없음")
            else:
                n_pso += 1
        if row["my_played"]:
            n_played += 1

        # 5. 경기 상세에 4종 페이로드가 들어갔는가
        det = conn.execute(
            "SELECT * FROM match_details ORDER BY id DESC LIMIT 1").fetchone()
        n_detail_after = conn.execute("SELECT COUNT(*) c FROM match_details").fetchone()["c"]
        if n_detail_after > n_detail_before and det is not None:
            keys = det.keys()
            pr_raw = det["player_ratings"] if "player_ratings" in keys else ""
            try:
                pr = json.loads(pr_raw) if pr_raw else {}
            except Exception:
                pr = {}
            if pr.get("home") and pr.get("away"):
                n_ratings += 1
            try:
                payload = json.loads(det["detail_json"]) if det["detail_json"] else {}
            except Exception:
                payload = {}
            if payload.get("score_90") is not None and "went_extra_time" in payload:
                n_extra += 1
            # 벤치/출전정지가 아니었는데 평점이 비었으면 문제
            if row["my_played"] and not (pr.get("home") and pr.get("away")):
                violations.append(f"[{i}] 출전했는데 경기 상세 player_ratings가 비었음")

        # 6. po_history 기록
        n_hist_after = conn.execute("SELECT COUNT(*) c FROM po_history").fetchone()["c"]
        if n_hist_after == n_hist_before + 1:
            n_hist += 1
        else:
            violations.append(f"[{i}] po_history가 안 늘었음 "
                              f"({n_hist_before} → {n_hist_after})")

        # fixture 정리 — 검사용 행은 남기지 않는다
        conn.execute("DELETE FROM po_matches WHERE tournament_id=?", (t_id,))
        conn.execute("DELETE FROM po_tournaments WHERE id=?", (t_id,))
        conn.commit()
        conn.close()

    print(f"경기 {args.n}회 — 출전 {n_played}회, 연장 진입 {n_et}회, 승부차기 {n_pso}회")
    print(f"경기 상세: player_ratings 채워짐 {n_ratings}회, "
          f"score_90/went_extra_time 채워짐 {n_extra}회")
    print(f"po_history 기록 {n_hist}회")
    print()
    if violations:
        print("=== 실패 ===")
        for v in violations[:30]:
            print(f"  {v}")
        if len(violations) > 30:
            print(f"  ... 외 {len(violations) - 30}건")
        return 1
    print("=== 전체 통과: 승강 PO 단판 연장 + 평점/교체/90분 스코어가 모두 기록됨 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())