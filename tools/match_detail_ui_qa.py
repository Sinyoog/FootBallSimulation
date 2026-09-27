"""match_detail_ui_qa.py — 경기 상세 3건 수정 검사 (2026-09 신설)

신민용 리포트 3건을 각각 검사한다.

  A. "왜 교체는 2번 되었는데 위에 교체되었다는건 1명만 표시되어 있어?"
     merge_my_slot이 교체로 빠진 선발 자리를 덮어쓰면 그 선수의
     subbed_out/off_min이 사라져 라인업의 ↓ 표시가 줄어든다.
     → 치환 후에도 "라인업의 ↓ 개수 == 그 팀 교체 건수"여야 한다.

  B. "교체에서 선수들도 클릭하면 선수 검색 창 들어가게"
     교체 레코드에 out_id/in_id가 실제로 실려 있어야 클릭이 가능하다.

  C. "다른 선수들이 골 넣어도 다 뜨게 해줘"
     대회 경기 타임라인에 내가 관여 안 한 우리 팀 득점이
     실제 득점자 이름과 함께 들어가야 한다.

A는 순수 단위검사(DB 불필요), B·C는 전술엔진을 실제로 돌려서 본다.

사용법:
    python tools/match_detail_ui_qa.py --db <세이브.db> [--n 40]
    python tools/match_detail_ui_qa.py            # A만 실행
"""
import _path  # noqa: F401
import argparse
import os
import random
import shutil
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


# ══════════════════════════════════════════════════════════════
# A. 슬롯 치환이 교체 표시를 지우지 않는가 (단위검사)
# ══════════════════════════════════════════════════════════════
def _entry(pid, name, pos, started=True, subbed_out=False, off_min=None, goals=0):
    e = {"id": pid, "name": name, "position": pos, "ovr": 70,
         "goals": goals, "assists": 0, "shots": 1, "shots_on": 1,
         "saves": 0, "is_gk": (pos == "GK"), "rating": 7.0,
         "started": started, "subbed_in": not started,
         "subbed_out": subbed_out, "on_min": 0 if started else 60,
         "off_min": off_min if off_min is not None else 90,
         "minutes": 90}
    return e


def _down_markers(side_list):
    """라인업 목록(선발만)에서 ↓ 표시가 붙는 항목 수 — match_detail_dialog.
    _lineup_player_row의 조건(subbed_out and off_min)과 같은 기준."""
    n = 0
    for r in side_list or []:
        if not r or r.get("started") is False:
            continue          # 교체 투입 선수는 라인업 목록에서 빠진다
        if r.get("subbed_out") and r.get("off_min"):
            n += 1
    return n


def test_slot_preserves_sub_markers():
    from competition.competition_common import merge_my_slot

    problems = []

    def _case(label, my_pos, home, n_subs):
        pr = {"home": home, "away": []}
        before = _down_markers(pr["home"])
        merge_my_slot(pr, True, my_pos,
                      {"id": None, "name": "나", "position": None, "ovr": 99,
                       "goals": 0, "assists": 0, "shots": 0, "shots_on": 0,
                       "saves": 0, "is_gk": False, "rating": 7.5, "is_me": True},
                      1, 0)
        after = _down_markers(pr["home"])
        me = [r for r in pr["home"] if r and r.get("is_me")]
        ok = (after == n_subs) and len(me) == 1
        print(f"  {label}: 교체 {n_subs}건 / ↓표시 치환전 {before} → 치환후 {after} "
              f"/ 내 슬롯 {len(me)}개  {'OK' if ok else '실패'}")
        if not ok:
            problems.append(label)

    # 케이스 1 — 신민용이 실제로 본 상황: RM 선발이 62분에 교체됐고,
    # 나도 RM이다. 예전 코드는 이 RM 자리를 골라서 ↓62'를 지워버렸다.
    home1 = [
        _entry(1, "GK", "GK"),
        _entry(2, "CB1", "CB"), _entry(3, "CB2", "CB"),
        _entry(4, "LB", "LB"), _entry(5, "RB", "RB"),
        _entry(6, "LM", "LM"), _entry(7, "CM1", "CM"), _entry(8, "CM2", "CM"),
        _entry(9, "RM_OUT", "RM", subbed_out=True, off_min=62),   # 62분 교체
        _entry(10, "ST1", "ST"),
        _entry(11, "ST_OUT", "ST", subbed_out=True, off_min=68),  # 68분 교체
        _entry(12, "IN_A", "RM", started=False),
        _entry(13, "IN_B", "ST", started=False),
    ]
    _case("① RM 선발이 교체됨 + 내가 RM", "RM", home1, n_subs=2)

    # 케이스 2 — 같은 포지션 선발이 전부 교체로 빠진 드문 경우(폴백).
    # 이때는 그 자리를 쓰되 교체 표시를 물려받아 개수가 유지돼야 한다.
    home2 = [
        _entry(1, "GK", "GK"),
        _entry(2, "CB1", "CB"), _entry(3, "CB2", "CB"),
        _entry(4, "LB", "LB"), _entry(5, "RB", "RB"),
        _entry(6, "LM", "LM"), _entry(7, "CM1", "CM"), _entry(8, "CM2", "CM"),
        _entry(9, "RM_OUT", "RM", subbed_out=True, off_min=62),
        _entry(10, "ST1", "ST"), _entry(11, "ST2", "ST"),
        _entry(12, "IN_A", "RM", started=False),
    ]
    # POSITION_COMPAT로 다른 자리에 붙을 수 있으므로, RM 외 전 필드 선발을
    # 전부 교체된 것으로 만들어 진짜 폴백만 남긴다.
    home3 = [_entry(1, "GK", "GK")] + [
        _entry(i, f"P{i}", "RM", subbed_out=True, off_min=50 + i)
        for i in range(2, 12)
    ] + [_entry(20, "IN", "RM", started=False)]
    _case("② 같은 포지션 선발이 교체됨(단일) + 대체 자리 있음", "RM", home2, n_subs=1)
    _case("③ 필드 선발 전원이 교체된 극단 폴백", "RM", home3, n_subs=10)

    return problems


# ══════════════════════════════════════════════════════════════
# B·C. 전술엔진 실데이터로 교체 id / 동료 골 이벤트 확인
# ══════════════════════════════════════════════════════════════
def test_with_engine(db_path, n):
    import database
    database.USE_MEMORY_DB = False
    work = os.path.abspath("match_detail_ui_qa")
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "ui.db")
    shutil.copy(os.path.abspath(db_path), db)
    hsrc = os.path.splitext(os.path.abspath(db_path))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")
    database.DB_PATH = db
    os.chdir(work)
    database.reset_conn_pool()
    database.init_db()

    from database import get_conn
    from match_sim.tactical_engine import simulate_my_match
    from competition.competition_common import augment_team_goal_events
    import game_engine as ge

    conn = get_conn()
    teams = [r["id"] for r in conn.execute(
        """SELECT t.id FROM teams t
           WHERE (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) >= 18
           ORDER BY t.id LIMIT 12""")]
    c = conn.cursor()
    forms = {t: ge._team_formation(c, t) for t in teams}
    conn.close()
    p = ge.get_player() or {"name": "테스트선수", "position": "CM", "ovr": 70}

    random.seed(20260927)
    problems = []
    n_subs_total = 0
    n_subs_with_ids = 0
    n_team_goal_cases = 0
    n_team_goal_named = 0
    n_marker_ok = 0
    n_marker_cases = 0
    n_sub_of_sub = 0

    for i in range(n):
        h, a = teams[i % len(teams)], teams[(i + 3) % len(teams)]
        if h == a:
            continue
        sim = simulate_my_match(h, a, forms[h], forms[a],
                                home_adv=0.0, extra_time=True)
        pr = {"home": sim.get("home_player_ratings") or [],
              "away": sim.get("away_player_ratings") or []}
        subs = {"home": sim.get("home_subs") or [], "away": sim.get("away_subs") or []}
        hs, as_ = sim["home_score"], sim["away_score"]

        # 참고 집계 — 교체로 들어왔다가 다시 교체된 선수(별개 표시 이슈).
        for side in ("home", "away"):
            n_sub_of_sub += sum(
                1 for r in pr[side]
                if r and r.get("started") is False and r.get("subbed_out"))

        # ── B. 교체 레코드에 클릭용 id가 있는가 ──────────────────
        for side in ("home", "away"):
            for s in subs[side]:
                n_subs_total += 1
                if s.get("out_id") is not None and s.get("in_id") is not None:
                    n_subs_with_ids += 1
                else:
                    problems.append(f"[{i}] 교체 레코드에 out_id/in_id 없음: {s}")

        # ── C. 동료 골이 타임라인에 들어가는가 ──────────────────
        # 내가 골도 어시도 없는데 우리 팀이 득점한 경기만 의미가 있다.
        if hs >= 1:
            events_in = [(30, "🛡 몸을 던진 태클")]   # 내 개인 이벤트 하나만
            out = augment_team_goal_events(
                p, True, hs, as_, 0, 0, True, events_in,
                sim["possession_log"], pr)
            n_team_goal_cases += 1
            goal_evs = [e for e in out
                        if isinstance(e, tuple) and "⚽" not in str(e[1])
                        and e not in events_in]
            added = len(out) - len(events_in)
            if added < hs:
                problems.append(
                    f"[{i}] 우리 팀 {hs}골인데 타임라인에 {added}개만 추가됨")
            # 이름이 실제로 붙었는지(괄호 안에 뭔가 있는지)
            if any("(" in str(e[1]) for e in out[len(events_in):]):
                n_team_goal_named += 1

        # ── A(실데이터). 치환이 ↓ 표시를 "지우지 않는가" ────────────
        #
        # [기준 주의] "↓ 개수 == 교체 건수"는 틀린 불변식이다. 교체로
        # 들어온 선수가 나중에 또 교체되는 경우(전술 변경·연장)가 실제로
        # 생기는데, 그 선수는 started=False라 라인업 목록(선발 11명)에
        # 애초에 안 나온다 — 그건 이 수정과 무관한 별개의 표시 설계
        # 문제라 여기서 섞어 재면 진짜 회귀를 못 본다.
        #
        # 여기서 볼 것은 딱 하나: **merge_my_slot 치환이 원래 있던 ↓
        # 표시를 줄이지 않는가**. 치환 전후를 직접 비교한다.
        from competition.competition_common import merge_my_slot
        import copy
        for side, is_home in (("home", True), ("away", False)):
            if not subs[side]:
                continue
            pr2 = {"home": copy.deepcopy(pr["home"]), "away": copy.deepcopy(pr["away"])}
            before = _down_markers(pr2[side])
            merge_my_slot(pr2, is_home, p.get("position", "CM"),
                          {"id": None, "name": "나", "position": None, "ovr": 70,
                           "goals": 0, "assists": 0, "shots": 0, "shots_on": 0,
                           "saves": 0, "is_gk": False, "rating": 7.0, "is_me": True},
                          hs, as_)
            after = _down_markers(pr2[side])
            n_marker_cases += 1
            if after == before:
                n_marker_ok += 1
            else:
                problems.append(
                    f"[{i}] {side}: 치환으로 ↓표시가 {before} → {after}로 줄었음")

    print(f"  B. 교체 레코드 {n_subs_total}건 중 out_id/in_id 있는 것 {n_subs_with_ids}건")
    print(f"  C. 우리 팀 득점 경기 {n_team_goal_cases}건 중 득점자 이름이 붙은 것 {n_team_goal_named}건")
    print(f"  A(실데이터). 치환이 ↓표시를 안 지움 {n_marker_ok}/{n_marker_cases}")
    if n_sub_of_sub:
        print(f"  [참고] 교체 투입 뒤 다시 교체된 선수 {n_sub_of_sub}명 — 2026-09부터"
              f" 라인업 목록의 '교체 투입' 구간에 ↑분 ↓분이 함께 표시된다.")
    return problems



# ══════════════════════════════════════════════════════════════
# D. 연장 구간 타임라인 + 라인업이 교체를 전부 담는가
# ══════════════════════════════════════════════════════════════
def _lineup_arrow_count(side_all):
    """다이얼로그가 실제로 그리는 좌우 목록(선발 + 교체 투입) 기준
    화살표 개수. ↑와 ↓를 따로 센다(한 선수가 둘 다 가질 수 있다)."""
    up = down = 0
    for r in side_all or []:
        if not r:
            continue
        if r.get("subbed_in") and r.get("on_min"):
            up += 1
        if r.get("subbed_out") and r.get("off_min"):
            down += 1
    return up, down


def test_extra_time_and_bench(db_path, n):
    import database
    database.USE_MEMORY_DB = False
    work = os.path.abspath("match_detail_ui_qa")
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "et.db")
    shutil.copy(os.path.abspath(db_path), db)
    hsrc = os.path.splitext(os.path.abspath(db_path))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")
    database.DB_PATH = db
    os.chdir(work)
    database.reset_conn_pool()
    database.init_db()

    from database import get_conn
    from match_sim.tactical_engine import (simulate_my_match, timeline_minute,
                                           TIMELINE_ET1_BASE, TIMELINE_ET2_BASE)
    from competition.competition_common import augment_team_goal_events
    import game_engine as ge

    conn = get_conn()
    teams = [r["id"] for r in conn.execute(
        """SELECT t.id FROM teams t
           WHERE (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) >= 18
           ORDER BY t.id LIMIT 10""")]
    c = conn.cursor()
    forms = {t: ge._team_formation(c, t) for t in teams}
    conn.close()
    p = ge.get_player() or {"name": "테스트선수", "position": "CM", "ovr": 70}

    random.seed(4242)
    problems = []
    n_et_goals = 0
    n_et_matches = 0
    n_arrow_ok = 0
    n_arrow_cases = 0
    n_bench_rows = 0

    for i in range(n):
        h, a = teams[i % len(teams)], teams[(i + 6) % len(teams)]
        if h == a:
            continue
        sim = simulate_my_match(h, a, forms[h], forms[a],
                                home_adv=0.0, extra_time=True)
        pr = {"home": sim.get("home_player_ratings") or [],
              "away": sim.get("away_player_ratings") or []}
        subs = {"home": sim.get("home_subs") or [], "away": sim.get("away_subs") or []}
        if sim.get("went_extra_time"):
            n_et_matches += 1

        # D-1. 라인업(선발 + 교체 투입)이 교체 건수를 전부 담는가.
        for side in ("home", "away"):
            if not subs[side]:
                continue
            n_arrow_cases += 1
            up, down = _lineup_arrow_count(pr[side])
            n_bench_rows += sum(1 for r in pr[side] if r and r.get("started") is False)
            if up == len(subs[side]) and down == len(subs[side]):
                n_arrow_ok += 1
            else:
                problems.append(
                    f"[{i}] {side}: 교체 {len(subs[side])}건인데 라인업 화살표 "
                    f"↑{up} ↓{down}")

        # D-2. 연장에서 난 골이 타임라인에서 연장 구간으로 분류되는가.
        if sim["home_score"] >= 1:
            out = augment_team_goal_events(
                p, True, sim["home_score"], sim["away_score"], 0, 0, True,
                [(30, "🛡 몸을 던진 태클")], sim["possession_log"], pr)
            for m, _t in [e for e in out if isinstance(e, tuple)]:
                if m >= TIMELINE_ET1_BASE:
                    n_et_goals += 1
        # 내부 분 → 코드 변환이 구간을 안 잃는가(엔진 쪽 단위검사).
        for r in sim["possession_log"]:
            if r.get("outcome") != "goal":
                continue
            mi = int(r.get("min", 0))
            code = timeline_minute(mi)
            if mi <= 96 and code != mi:
                problems.append(f"[{i}] 정규시간 {mi}분이 코드 {code}로 바뀜")
            if 97 <= mi <= 112 and not (TIMELINE_ET1_BASE <= code < TIMELINE_ET2_BASE):
                problems.append(f"[{i}] 연장전반 {mi}분이 ET1 코드가 아님: {code}")
            if mi >= 113 and code < TIMELINE_ET2_BASE:
                problems.append(f"[{i}] 연장후반 {mi}분이 ET2 코드가 아님: {code}")

    print(f"  D-1. 라인업 화살표 == 교체 건수 {n_arrow_ok}/{n_arrow_cases} "
          f"(교체 투입 행 {n_bench_rows}개가 목록에 포함됨)")
    print(f"  D-2. 연장 간 경기 {n_et_matches}건, 연장 구간으로 분류된 골 이벤트 {n_et_goals}개")
    return problems



# ══════════════════════════════════════════════════════════════
# E. 타임라인 득점자 == 라인업 골 배지
# ══════════════════════════════════════════════════════════════
def test_scorer_consistency(db_path, n):
    """신민용 리포트: "좌측엔 AI00QI랑 AI00RL가 넣었다고 뜨는데 왜
    오른쪽엔 AI00RL이 2골 넣었다고 표시돼?"

    타임라인 득점자는 전술엔진 원본(치환 전)에서 나오고, 라인업 골 배지는
    치환+reconcile 후 값에서 나온다. 둘이 어긋나지 않으려면 치환이 득점
    분포를 바꾸지 않아야 한다 — 그게 여기서 보는 불변식이다."""
    import copy
    import database
    database.USE_MEMORY_DB = False
    work = os.path.abspath("match_detail_ui_qa")
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "sc.db")
    shutil.copy(os.path.abspath(db_path), db)
    hsrc = os.path.splitext(os.path.abspath(db_path))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")
    database.DB_PATH = db
    os.chdir(work)
    database.reset_conn_pool()
    database.init_db()

    from database import get_conn
    from match_sim.tactical_engine import simulate_my_match
    from competition.competition_common import merge_my_slot
    import game_engine as ge

    conn = get_conn()
    teams = [r["id"] for r in conn.execute(
        """SELECT t.id FROM teams t
           WHERE (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) >= 18
           ORDER BY t.id LIMIT 10""")]
    c = conn.cursor()
    forms = {t: ge._team_formation(c, t) for t in teams}
    conn.close()

    def goal_map(lst):
        """id → 골 수 (나 슬롯 제외)."""
        out = {}
        for r in lst or []:
            if not r or r.get("is_me"):
                continue
            g = int(r.get("goals", 0) or 0)
            if g:
                out[r.get("id")] = g
        return out

    random.seed(777)
    problems = []
    cases = same_dist = clean_slot = sum_ok = 0
    sub_got_extra = 0

    for i in range(n):
        h, a = teams[i % len(teams)], teams[(i + 7) % len(teams)]
        if h == a:
            continue
        sim = simulate_my_match(h, a, forms[h], forms[a],
                                home_adv=0.0, extra_time=True)
        hs, as_ = sim["home_score"], sim["away_score"]
        if hs < 1:
            continue            # 우리 팀 득점이 있어야 의미가 있다
        pr0 = {"home": sim["home_player_ratings"], "away": sim["away_player_ratings"]}
        before = goal_map(pr0["home"])

        pr = copy.deepcopy(pr0)
        # 내가 0골 0어시인 가장 흔한 경우(신민용 스크린샷과 같은 상황).
        side_key, idx = merge_my_slot(
            pr, True, "ST",
            {"id": None, "name": "나", "position": None, "ovr": 99,
             "goals": 0, "assists": 0, "shots": 1, "shots_on": 0,
             "saves": 0, "is_gk": False, "rating": 7.5, "is_me": True},
            hs, as_)
        cases += 1

        # E-1. 1순위가 잡혔는가 — 치환된 AI가 골/어시 0이었는가.
        if idx is not None:
            old = pr0["home"][idx]
            if int(old.get("goals", 0) or 0) == 0 and int(old.get("assists", 0) or 0) == 0:
                clean_slot += 1

        # E-2. 치환 후 득점 분포가 그대로인가(= 타임라인과 일치).
        after = goal_map(pr["home"])
        if after == before:
            same_dist += 1
        else:
            gained = {k: after[k] - before.get(k, 0) for k in after
                      if after[k] != before.get(k, 0)}
            problems.append(f"[{i}] 치환으로 득점 분포가 바뀜: {gained}")
            # 교체 투입 선수가 추가 골을 받았는가(가장 이상해 보이는 형태)
            for r in pr["home"]:
                if (r and not r.get("is_me") and r.get("started") is False
                        and after.get(r.get("id"), 0) > before.get(r.get("id"), 0)):
                    sub_got_extra += 1

        # E-3. 팀 골 합계 == 스코어 (기존 불변식이 안 깨졌는가)
        tot = sum(int(r.get("goals", 0) or 0) for r in pr["home"] if r)
        if tot == hs:
            sum_ok += 1
        else:
            problems.append(f"[{i}] 팀 골 합계 {tot} != 스코어 {hs}")

    print(f"  E-1. 득점에 관여 안 한 슬롯으로 치환됨 {clean_slot}/{cases}")
    print(f"  E-2. 치환 후에도 득점 분포 동일(타임라인과 일치) {same_dist}/{cases}")
    print(f"  E-3. 팀 골 합계 == 스코어 {sum_ok}/{cases}")
    if sub_got_extra:
        print(f"  [참고] 교체 투입 선수가 추가 골을 받은 경우 {sub_got_extra}건")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()

    print("A. 슬롯 치환이 교체 표시를 지우지 않는가 (단위검사)")
    problems = test_slot_preserves_sub_markers()
    print()

    if args.db:
        print("B·C. 전술엔진 실데이터 검사")
        problems += test_with_engine(args.db, args.n)
        print()
        print("D. 연장 구간 타임라인 + 라인업이 교체를 전부 담는가")
        problems += test_extra_time_and_bench(args.db, args.n)
        print()
        print("E. 타임라인 득점자 == 라인업 골 배지")
        problems += test_scorer_consistency(args.db, args.n)
        print()
    else:
        print("B·C 건너뜀 — --db <세이브.db>를 주면 실데이터로도 검사합니다.\n")

    if problems:
        print("=== 실패 ===")
        for x in problems[:25]:
            print(f"  {x}")
        if len(problems) > 25:
            print(f"  ... 외 {len(problems) - 25}건")
        return 1
    print("=== 전체 통과 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())