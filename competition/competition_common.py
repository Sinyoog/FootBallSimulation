# -*- coding: utf-8 -*-
"""
[2026-08 신설, 신민용 설계 확정: "챔스/유로파급/컨퍼런스급 3단계 대륙대항전
공통 엔진"]

champions_engine.py(1939줄)의 함수들을 A/B/C 세 그룹으로 나눈 것 중,
A그룹(완전 공통)과 B그룹(로직은 동일하고 테이블명/문자열만 대회마다 다름)을
이 모듈로 옮긴다. C그룹(대륙별 규모·슬롯 등 챔스 전용 규칙)은
champions_engine.py에 그대로 남는다 — continental_qualification.py가
참가팀 선정을 전담하게 되면서 C그룹 중 슬롯 계산 관련 함수들은 아예
삭제 대상이 된다(다음 단계에서 champions_engine.py를 정리할 때 처리).

[리팩터링 원칙 — 매우 중요] 이 모듈의 모든 함수는 champions_engine.py
원본 함수 본문을 그대로 옮기고, 아래 두 가지만 바꿨다:
  1) 테이블명 리터럴("cl_matches" 등) → cfg.xxx_table
  2) 대회명/시상명/momentum 타입 등 문자열 리터럴 → cfg.xxx
그 외의 로직·조건문·계산식·random 호출 순서는 단 한 줄도 안 바꿨다 —
RNG를 쓰는 게임이라 호출 순서가 바뀌면 같은 시드로도 결과가 달라지므로,
"동일 시드 → 동일 결과"를 검증하려면 이 원칙이 지켜져야 한다.

[아직 안 옮긴 것] _award_cl_awards(시상), _cl_team_stage_weights,
get_my_cl_* 조회 함수들은 이번 1차 이동에서 제외했다 — 핵심 대진/경기결과/
우승팀 결정 경로부터 먼저 검증한 뒤(신민용 요청 9개 항목 중 1~6번),
그다음 개인기록/시상(7~9번)을 옮기는 게 안전하다고 판단했다. 지금은
champions_engine.py가 이 부분만 자체 보유 중.
"""
import random
from dataclasses import dataclass

from database import get_conn
from constants import generate_round_robin


@dataclass(frozen=True)
class CompetitionConfig:
    """대회 하나를 이 공통 엔진에 연결하는 데 필요한 최소 설정.
    [신민용 지적 반영] 대륙별 참가 규모·직행/PO 컷 같은 복잡한 진출
    규칙은 여기 억지로 넣지 않는다 — 그건 각 대회 엔진(champions_engine.py
    등)이 자기 C그룹 상수/함수로 계산해서, _build_tournament 등을 호출할
    때 이미 확정된 값(entries, games 등)으로 넘겨준다. cfg는 순수하게
    "이 대회의 결과를 어느 테이블에, 어떤 이름으로 저장할지"만 안다."""
    match_table: str
    entry_table: str
    tournament_table: str
    history_table: str
    competition_name_by_continent: dict   # {"유럽": "UEFA 유로파리그", ...}
    award_prefix: str                     # 시상 이름 접두사 (예: "유로파리그")
    momentum_type: str                    # 우승 시 club_momentum 타입명
    stage_ko: dict                        # {"league":"리그 스테이지", "R16":"16강", ...}
    round_weeks: dict                     # {"league_start":9, "PO":18, "R32":19, ...}
    league_weeks: tuple                   # (시작주, 끝주) — CL_LEAGUE_WEEKS와 동일 형태
    end_week: int
    stage_order: list                     # ["R32","R16","QF","SF","F"]


# ─────────────────────────────────────────────
# A그룹 — 완전 공통 (원본과 100% 동일, cfg조차 필요 없음)
# ─────────────────────────────────────────────

def match_outcome(h_ovr, a_ovr):
    """중립 구장 가정. 'home'/'draw'/'away' (KO 무승부 → 승부차기).
    champions_engine._match_outcome과 완전히 동일."""
    diff = h_ovr - a_ovr
    hw = max(0.04, min(0.95, 0.46 + diff * 0.022))
    dw = max(0.05, 0.24 - abs(diff) * 0.009)
    aw = max(0.02, 1.0 - hw - dw)
    tot = hw + dw + aw
    hw, dw, aw = hw / tot, dw / tot, aw / tot
    roll = random.random()
    if roll < hw:
        return "home"
    elif roll < hw + dw:
        return "draw"
    return "away"


def resolve_pso(h_ovr, a_ovr):
    """champions_engine._resolve_pso와 완전히 동일."""
    p_home = 0.5 + max(-0.1, min(0.1, (h_ovr - a_ovr) * 0.006))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


def winner_of(m):
    """champions_engine._winner_of와 완전히 동일."""
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home_team_id"] if m["home_score"] > m["away_score"] else m["away_team_id"]


def first_stage_for(n):
    """champions_engine._first_stage_for와 완전히 동일."""
    if n >= 32:
        return "R32"
    if n >= 16:
        return "R16"
    if n >= 8:
        return "QF"
    if n >= 4:
        return "SF"
    return "F"


def league_phase_pairs(entries, games, my_tid):
    """champions_engine._league_phase_pairs와 완전히 동일."""
    n = len(entries)
    best_order, best_conflicts = None, None
    for _try in range(6):
        order = entries[:]
        random.shuffle(order)
        rounds = generate_round_robin(n)[:games]
        conflicts = sum(
            1 for rd in rounds for a, b in rd
            if order[a]["country"] == order[b]["country"])
        if best_conflicts is None or conflicts < best_conflicts:
            best_order, best_conflicts = order, conflicts
        if conflicts == 0:
            break

    rounds = generate_round_robin(n)[:games]
    pairs = []
    for rd_idx, rd in enumerate(rounds):
        for a, b in rd:
            home, away = (best_order[a], best_order[b]) if rd_idx % 2 == 0 \
                         else (best_order[b], best_order[a])
            pairs.append((rd_idx, home, away))
    return pairs


# ─────────────────────────────────────────────
# B그룹 — 로직 동일, cfg로 테이블명/문자열만 주입
# ─────────────────────────────────────────────

_entry_cache: dict = {}


def clear_entry_cache():
    _entry_cache.clear()


def entry(cfg, tid, team_id):
    """champions_engine._entry와 동일(테이블명만 cfg)."""
    key = (cfg.match_table, tid, team_id)
    cached = _entry_cache.get(key)
    if cached is not None:
        return cached
    conn = get_conn()
    row = conn.execute(
        f"SELECT * FROM {cfg.entry_table} WHERE tournament_id=? AND team_id=?",
        (tid, team_id)).fetchone()
    conn.close()
    result = dict(row) if row else {"team_name": "?", "flag": "", "ovr": 50}
    _entry_cache[key] = result
    return result


def entry_from(lg, standing_row, cl_rank=1):
    """champions_engine._entry_from과 완전히 동일 — 대회 무관 공용."""
    from game_engine import get_conn as _gc
    tid = standing_row["id"]
    conn = _gc()
    row = conn.execute("SELECT AVG(ovr) AS v FROM ai_players WHERE team_id=?", (tid,)).fetchone()
    conn.close()
    ovr = (row["v"] if row and row["v"] else 50) + random.uniform(-2, 2)
    return {
        "team_id": tid,
        "team_name": standing_row["name"],
        "flag": lg["flag"],
        "country": lg["country"],
        "grade": lg["grade"],
        "ovr": ovr,
        "cl_rank": cl_rank,
    }


def build_tournament(cfg, year, continent, entries, my_tid, team_cap, games):
    """champions_engine._build_tournament과 동일 — team_cap/games는 호출부
    (각 대회 엔진의 C그룹 함수)가 넘겨준다."""
    name = cfg.competition_name_by_continent.get(continent, cfg.award_prefix)

    entries.sort(key=lambda e: e["ovr"], reverse=True)
    n = len(entries) - (len(entries) % 2)
    n = min(n, team_cap)
    entries = entries[:n]
    if n < games + 1:
        return None

    my_in = 1 if (my_tid and any(e["team_id"] == my_tid for e in entries)) else 0
    my_reg_tid = my_tid if my_in else 0

    conn = get_conn()
    c = conn.cursor()
    c.execute(f"""INSERT INTO {cfg.tournament_table}(year, continent, name, status,
                    my_in, my_team_id, my_qualified)
                 VALUES(?,?,?,?,?,?,?)""",
              (year, continent, name, "league", my_in, my_reg_tid, my_in))
    tid = c.lastrowid

    entry_rows = [(tid, e["team_id"], e["team_name"], e["flag"],
                   e["country"], e["grade"], e["ovr"]) for e in entries]
    c.executemany(f"""INSERT INTO {cfg.entry_table}
                         (tournament_id, team_id, team_name, flag, country,
                          grade, ovr, alive)
                         VALUES(?,?,?,?,?,?,?,1)""", entry_rows)

    w0 = cfg.league_weeks[0]
    match_rows = []
    for rd_idx, home, away in league_phase_pairs(entries, games, my_tid):
        wk = w0 + rd_idx
        is_my = 1 if my_tid in (home["team_id"], away["team_id"]) else 0
        match_rows.append((tid, "league", wk,
                   home["team_id"], away["team_id"], is_my))
    c.executemany(f"""INSERT INTO {cfg.match_table}
                             (tournament_id, stage, week,
                              home_team_id, away_team_id,
                              home_score, away_score, is_my, slot)
                             VALUES(?,?,?,?,?,-1,-1,?,0)""", match_rows)
    c.execute(f"UPDATE {cfg.tournament_table} SET status='league', first_stage='league' WHERE id=?",
              (tid,))
    conn.commit()
    conn.close()
    return tid


def sim_ai_match(cfg, t, m, my_played=False, conn=None, reason="injury", batch=None):
    """champions_engine._sim_ai_match와 동일(테이블명·로그 접두사만 cfg)."""
    from game_engine import add_log, get_player, _gen_score, _week_intl_cl_day
    he = entry(cfg, t["id"], m["home_team_id"])
    ae = entry(cfg, t["id"], m["away_team_id"])

    outcome = match_outcome(he["ovr"], ae["ovr"])
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "league")
    if outcome == "draw" and is_ko:
        win_home, pso_score = resolve_pso(he["ovr"], ae["ovr"])
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, he["ovr"] - ae["ovr"])

    day = _week_intl_cl_day(m["week"], get_player() or {}) if m["is_my"] else m.get("day")

    _absence = reason if m["is_my"] else None
    _row = (hs, as_, pso_winner, pso_score, day, _absence, m["id"])
    if batch is not None:
        batch.append(_row)
    else:
        _own = conn is None
        if _own:
            conn = get_conn()
        conn.execute(f"""UPDATE {cfg.match_table} SET home_score=?, away_score=?,
                        pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
                     _row)
        if _own:
            conn.commit()
            conn.close()

    if m["is_my"]:
        p = get_player()
        my_tid = p.get("current_team_id", 0) if p else 0
        if my_tid in (m["home_team_id"], m["away_team_id"]):
            stage_ko = cfg.stage_ko.get(m["stage"], "")
            pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
            add_log(f"🏆 {t['name']} {stage_ko}  "
                    f"{he['flag']}{he['team_name']} {hs}-{as_} {ae['flag']}{ae['team_name']}{pso_txt}",
                    "match")
            if not my_played:
                _reason_ko = {"injury": "부상", "suspension": "출전정지", "bench": "벤치"}.get(reason, reason)
                add_log(f"   🚑 {_reason_ko}(으)로 {cfg.award_prefix} 경기 결장", "match")


def get_league_standings(cfg, tid):
    """champions_engine.get_cl_league_standings와 동일(테이블명만 cfg)."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        f"SELECT * FROM {cfg.entry_table} WHERE tournament_id=?", (tid,)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        f"""SELECT * FROM {cfg.match_table} WHERE tournament_id=?
           AND stage='league' AND home_score>=0""", (tid,)).fetchall()]
    conn.close()

    tbl = {e["team_id"]: {"team_id": e["team_id"], "team_name": e["team_name"],
                          "flag": e["flag"], "ovr": e["ovr"],
                          "country": e["country"] if "country" in e.keys() else "",
                          "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    for m in matches:
        h, a = tbl.get(m["home_team_id"]), tbl.get(m["away_team_id"])
        if not h or not a:
            continue
        hs, as_ = m["home_score"], m["away_score"]
        h["p"] += 1; a["p"] += 1
        h["gf"] += hs; h["ga"] += as_
        a["gf"] += as_; a["ga"] += hs
        if hs > as_:
            h["w"] += 1; h["pts"] += 3; a["l"] += 1
        elif hs < as_:
            a["w"] += 1; a["pts"] += 3; h["l"] += 1
        else:
            h["d"] += 1; a["d"] += 1; h["pts"] += 1; a["pts"] += 1
    rows = list(tbl.values())
    rows.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]), reverse=True)
    return rows


def finalize_league_phase(cfg, t, direct_cut, po_pool, playoff_week, start_knockout_fn):
    """champions_engine._finalize_league_phase와 동일 — direct_cut/po_pool/
    playoff_week은 호출부(각 대회 엔진)가 자기 C그룹 상수로 계산해 넘긴다.
    start_knockout_fn: 이 모듈의 start_knockout을 그대로 넘기되, 호출부가
    자기 team_cap 등을 이미 partial로 바인딩해서 넘긴다(순환 의존 회피)."""
    from game_engine import add_log, get_player
    tid = t["id"]

    rows = get_league_standings(cfg, tid)
    direct = rows[:direct_cut]
    playoff_teams = rows[direct_cut:direct_cut + po_pool]
    eliminated = rows[direct_cut + po_pool:]

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    conn = get_conn(); c = conn.cursor()
    for r in eliminated:
        c.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?",
                  (tid, r["team_id"]))

    half = len(playoff_teams) // 2
    seeded, unseeded = playoff_teams[:half], playoff_teams[half:]
    po_pairs = list(zip(seeded, reversed(unseeded)))

    if po_pairs:
        for slot, (home, away) in enumerate(po_pairs):
            is_my = 1 if my_tid in (home["team_id"], away["team_id"]) else 0
            c.execute(f"""INSERT INTO {cfg.match_table}
                         (tournament_id, stage, week, home_team_id, away_team_id,
                          home_score, away_score, is_my, slot)
                         VALUES(?,?,?,?,?,-1,-1,?,?)""",
                      (tid, "PO", playoff_week,
                       home["team_id"], away["team_id"], is_my, slot))
        c.execute(f"UPDATE {cfg.tournament_table} SET status='playoff' WHERE id=?", (tid,))
        conn.commit()
        conn.close()
    else:
        conn.commit()
        conn.close()
        start_knockout_fn(t, [r["team_id"] for r in direct])

    add_log(f"🏆 {t['name']} 리그 스테이지 종료 → 1~{direct_cut}위 직행, "
            f"{direct_cut+1}~{direct_cut+po_pool}위 플레이오프", "event")
    if my_tid and any(r["team_id"] == my_tid for r in eliminated):
        my_rank = next((i + 1 for i, r in enumerate(rows) if r["team_id"] == my_tid), 0)
        my_row = next((r for r in rows if r["team_id"] == my_tid), None)
        if my_rank and my_row:
            result_txt = (f"리그 스테이지 {my_rank}위 "
                          f"({my_row['w']}승{my_row['d']}무{my_row['l']}패, {my_row['pts']}점)")
        else:
            result_txt = "리그 스테이지"
        record_my_exit(cfg, t, result_txt)


def finalize_playoff(cfg, t, start_knockout_fn):
    """champions_engine._finalize_playoff와 동일."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    po_matches = [dict(r) for r in conn.execute(
        f"SELECT * FROM {cfg.match_table} WHERE tournament_id=? AND stage='PO' ORDER BY slot",
        (tid,)).fetchall()]
    direct_ids = [r["team_id"] for r in conn.execute(
        f"SELECT team_id FROM {cfg.entry_table} WHERE tournament_id=? AND alive=1", (tid,)).fetchall()]
    conn.close()

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    winners, losers = [], []
    conn = get_conn(); c = conn.cursor()
    for m in po_matches:
        w = winner_of(m)
        l = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
        winners.append(w)
        losers.append(l)
        c.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?", (tid, l))
        if my_tid and l == my_tid:
            conn.commit(); conn.close()
            record_my_exit(cfg, t, "플레이오프")
            conn = get_conn(); c = conn.cursor()
    conn.commit(); conn.close()

    po_team_ids = {m["home_team_id"] for m in po_matches} | {m["away_team_id"] for m in po_matches}
    direct_only = [tid_ for tid_ in direct_ids if tid_ not in po_team_ids]
    qualifiers = direct_only + winners

    add_log(f"🏆 {t['name']} 플레이오프 종료 → {cfg.stage_ko.get(first_stage_for(len(qualifiers)), '')} 진출팀 확정", "event")
    start_knockout_fn(t, qualifiers, direct_ids=direct_only, winner_ids=winners)


def start_knockout(cfg, t, qualifier_ids, round_weeks, direct_ids=None, winner_ids=None):
    """champions_engine._start_knockout과 동일 — round_weeks는 호출부의
    CL_ROUND_WEEKS 상당 딕셔너리를 그대로 넘긴다."""
    from game_engine import get_player
    tid = t["id"]
    conn = get_conn()
    infos = {r["team_id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM {cfg.entry_table} WHERE tournament_id=?", (tid,)).fetchall()}
    conn.close()

    if direct_ids and winner_ids and len(direct_ids) == len(winner_ids):
        d_sorted = sorted(direct_ids, key=lambda tid_: infos.get(tid_, {}).get("ovr", 0), reverse=True)
        w_sorted = sorted(winner_ids, key=lambda tid_: infos.get(tid_, {}).get("ovr", 0))
        pairs = list(zip(d_sorted, w_sorted))
    else:
        ranked = sorted(qualifier_ids, key=lambda tid_: infos.get(tid_, {}).get("ovr", 0), reverse=True)
        half = len(ranked) // 2
        top, bottom = ranked[:half], ranked[half:]
        pairs = list(zip(top, reversed(bottom)))

    first_stage = first_stage_for(len(qualifier_ids))
    next_week = round_weeks.get(first_stage, round_weeks["R16"])

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    conn = get_conn(); c = conn.cursor()
    for slot, (home, away) in enumerate(pairs):
        is_my = 1 if my_tid in (home, away) else 0
        c.execute(f"""INSERT INTO {cfg.match_table}
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,-1,-1,?,?)""",
                  (tid, first_stage, next_week, home, away, is_my, slot))
    c.execute(f"UPDATE {cfg.tournament_table} SET status='ko', first_stage=? WHERE id=?",
              (first_stage, tid))
    conn.commit()
    conn.close()


def advance_round(cfg, t, cur_stage, next_stage, round_weeks):
    """champions_engine._advance_round와 동일."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        f"""SELECT * FROM {cfg.match_table} WHERE tournament_id=? AND stage=?
           ORDER BY slot""", (tid, cur_stage)).fetchall()]
    conn.close()
    if not cur:
        return

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    cur_stage_ko = cfg.stage_ko.get(cur_stage, "")
    next_week = round_weeks[next_stage]

    is_sf = (cur_stage == "SF")

    winners = []
    losers  = []
    conn = get_conn()
    c = conn.cursor()
    exit_label = cur_stage_ko
    for m in cur:
        w = winner_of(m)
        loser = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
        winners.append((m["slot"], w))
        if not is_sf:
            c.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?",
                      (tid, loser))
            if my_tid and loser == my_tid:
                conn.commit(); conn.close()
                record_my_exit(cfg, t, exit_label)
                conn = get_conn(); c = conn.cursor()
        else:
            losers.append(loser)

    winners.sort()
    for slot in range(0, len(winners), 2):
        if slot + 1 >= len(winners):
            break
        home, away = winners[slot][1], winners[slot + 1][1]
        is_my = 1 if my_tid in (home, away) else 0
        c.execute(f"""INSERT INTO {cfg.match_table}
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,-1,-1,?,?)""",
                  (tid, next_stage, next_week, home, away, is_my, slot // 2))

    if is_sf and len(losers) == 2:
        tp_home, tp_away = losers[0], losers[1]
        tp_week = round_weeks["TP"]
        is_my_tp = 1 if my_tid in (tp_home, tp_away) else 0
        c.execute(f"""INSERT INTO {cfg.match_table}
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,-1,-1,?,999)""",
                  (tid, "TP", tp_week, tp_home, tp_away, is_my_tp))
        te_h = entry(cfg, tid, tp_home); te_a = entry(cfg, tid, tp_away)
        add_log(f"🥉 {t['name']} 3/4위전: {te_h['team_name']} vs {te_a['team_name']} ({tp_week}주차)", "event")

    conn.commit()
    conn.close()
    if t["my_in"]:
        conn = get_conn()
        mr = conn.execute(f"SELECT my_result FROM {cfg.tournament_table} WHERE id=?", (tid,)).fetchone()
        conn.close()
        if not (mr and mr["my_result"]):
            add_log(f"🏆 {t['name']} {cur_stage_ko} 종료 → {cfg.stage_ko[next_stage]} 대진 확정", "event")


def finish_tournament(cfg, t, award_fn=None):
    """champions_engine._finish_tournament과 동일. award_fn: 시상 함수(2차
    이동 전까지는 None으로 넘겨 시상 단계만 건너뛸 수 있음)."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    fm = conn.execute(
        f"""SELECT * FROM {cfg.match_table} WHERE tournament_id=? AND stage='F'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
    tp = conn.execute(
        f"""SELECT * FROM {cfg.match_table} WHERE tournament_id=? AND stage='TP'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
    conn.close()
    if not fm:
        return
    fm = dict(fm)
    winner = winner_of(fm)
    runner = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    third = fourth = None
    if tp:
        tp = dict(tp)
        third  = winner_of(tp)
        fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]

    conn = get_conn()
    conn.execute(f"UPDATE {cfg.tournament_table} SET status='done', winner_team_id=? WHERE id=?",
                 (winner, tid))
    conn.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?",
                 (tid, runner))
    if fourth:
        conn.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?",
                     (tid, fourth))
    from constants import MOMENTUM_START_BY_TYPE
    conn.execute("UPDATE teams SET momentum_type=?, momentum_seasons_left=? WHERE id=?",
                 (cfg.momentum_type, MOMENTUM_START_BY_TYPE[cfg.momentum_type], winner))
    conn.commit()
    conn.close()

    we = entry(cfg, tid, winner)
    add_log(f"🏆 {t['name']} 우승: {we['flag']}{we['team_name']}!", "event")
    if third:
        te = entry(cfg, tid, third)
        add_log(f"🥉 {t['name']} 3위: {te['flag']}{te['team_name']}", "event")

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    if my_tid == winner:
        record_my_exit(cfg, t, "우승")
    elif my_tid == runner:
        record_my_exit(cfg, t, "준우승")
    elif my_tid == third:
        record_my_exit(cfg, t, "3위")
    elif my_tid == fourth:
        record_my_exit(cfg, t, "4위")

    if award_fn:
        award_fn(t, my_tid)


# 결과별 보상(명성/인기/행복) — champions_engine._REWARD와 동일 값을
# 그대로 재사용한다(대회 등급이 달라도 "우승/준우승/..." 보상 체계 자체는
# 공용). champions_engine에 이미 정의돼 있으므로 여기서는 import.
def _get_reward_table():
    from competition.champions_engine import _REWARD
    return _REWARD


def record_my_exit(cfg, t, result):
    """champions_engine._record_my_exit과 동일."""
    from game_engine import add_log, get_player, update_player
    p = get_player()
    if not p:
        return
    my_tid = p.get("current_team_id", 0)

    conn = get_conn()
    conn.execute(f"UPDATE {cfg.tournament_table} SET my_result=? WHERE id=?", (result, t["id"]))
    te = conn.execute(
        f"SELECT team_name, country FROM {cfg.entry_table} WHERE tournament_id=? AND team_id=?",
        (t["id"], my_tid)).fetchone()
    conn.commit()
    conn.close()
    _raw_name = te["team_name"] if te else ""
    team_name = _raw_name

    save_trophy(cfg, t["year"], team_name, t["name"], result)

    conn = get_conn()
    agg = conn.execute(
        f"""SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM {cfg.match_table}
           WHERE tournament_id=? AND my_played=1""", (t["id"],)).fetchone()
    exists = conn.execute(
        f"SELECT id FROM {cfg.history_table} WHERE year=? AND competition=?",
        (t["year"], t["name"])).fetchone()
    if not exists:
        conn.execute(f"""INSERT INTO {cfg.history_table}(year, competition, team_name, result,
                                               goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (t["year"], t["name"], team_name, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()

    _REWARD = _get_reward_table()
    fame_g, pop_g, hap_g = _REWARD.get(result, (0, 0, 0))
    update_player(
        fame=min(100, p.get("fame", 0) + fame_g),
        popularity=min(100, p.get("popularity", 0) + pop_g),
        happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
    )

    icon = "🏆" if result == "우승" else "🏅"
    add_log(f"{icon} {t['year']}년 {t['name']} 최종 성적: {result}  "
            f"(명성 +{fame_g}, 인기 +{pop_g})", "event")


def process_one(cfg, t, week, league_end_week, playoff_week, round_weeks, stage_order,
                 finalize_league_phase_fn, finalize_playoff_fn,
                 advance_round_fn, finish_tournament_fn):
    """[2026-08 이동] champions_engine._process_one과 완전히 동일한 로직 —
    단일 대회의 이번 주차 이하 미진행 경기를 AI로 시뮬레이션한 뒤, 그
    주차가 리그 스테이지 마감/플레이오프 마감/토너먼트 라운드 마감이면
    그에 맞는 마무리 함수를 호출한다.

    league_end_week/playoff_week/round_weeks: 호출부(각 대회 엔진)가
    자기 C그룹 상수로 계산해 넘긴다.
    finalize_league_phase_fn 등 4개: 각 엔진의 얇은 위임 함수(_finalize_
    league_phase 등)를 그대로 넘긴다 — cfg가 이미 그 함수들 내부에
    바인딩돼 있으므로 여기서는 t/week만 알면 된다."""
    conn = get_conn()
    pending = [dict(r) for r in conn.execute(
        f"""SELECT * FROM {cfg.match_table}
           WHERE tournament_id=? AND week<=? AND home_score=-1 ORDER BY id""",
        (t["id"], week)).fetchall()]

    _batch = []
    for m in pending:
        sim_ai_match(cfg, t, m, batch=_batch)
    if _batch:
        conn.executemany(
            f"""UPDATE {cfg.match_table} SET home_score=?, away_score=?,
               pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
            _batch)
    conn.commit()
    conn.close()

    if week == league_end_week:
        conn = get_conn()
        remain = conn.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='league' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn.close()
        if remain == 0:
            finalize_league_phase_fn(t)
        return

    if week == playoff_week:
        conn = get_conn()
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='PO'",
            (t["id"],)).fetchone()["n"]
        remain = conn.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='PO' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn.close()
        if total > 0 and remain == 0:
            finalize_playoff_fn(t)
        return

    cur_stage = None
    for stg, wk in round_weeks.items():
        if wk == week:
            cur_stage = stg
            break
    if cur_stage is None:
        return

    conn = get_conn()
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage=?",
        (t["id"], cur_stage)).fetchone()["n"]
    if total == 0:
        conn.close()
        return
    remain = conn.execute(
        f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage=? AND home_score=-1",
        (t["id"], cur_stage)).fetchone()["n"]
    conn.close()
    if remain > 0:
        return

    if cur_stage == "F":
        conn2 = get_conn()
        tp_remain = conn2.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='TP' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn2.close()
        if tp_remain == 0:
            finish_tournament_fn(t)
    elif cur_stage == "TP":
        conn2 = get_conn()
        f_remain = conn2.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='F' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn2.close()
        if f_remain == 0:
            finish_tournament_fn(t)
    else:
        nxt = stage_order[stage_order.index(cur_stage) + 1]
        advance_round_fn(t, cur_stage, nxt)


def my_continent(p):
    """[2026-08 이동] champions_engine._my_continent와 완전히 동일 —
    대회 무관 공용(테이블 조회 없음, teams/countries만 봄)."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    from competition.champions_engine import CONTINENT_MAP
    conn = get_conn()
    row = conn.execute(
        """SELECT cn.continent FROM teams t
           JOIN countries cn ON t.country_id = cn.id
           WHERE t.id=?""", (tid,)).fetchone()
    conn.close()
    if not row:
        return None
    return CONTINENT_MAP.get(row["continent"])


def get_tournament(cfg, year, continent):
    """[2026-08 이동] champions_engine.get_cl_tournament과 동일(테이블명만 cfg)."""
    conn = get_conn()
    row = conn.execute(
        f"SELECT * FROM {cfg.tournament_table} WHERE year=? AND continent=? ORDER BY id DESC LIMIT 1",
        (year, continent)).fetchone()
    conn.close()
    return dict(row) if row else None


def my_tournament(cfg, p, year):
    """[2026-08 신설] 내 대륙의 이번 연도 대회(있으면)."""
    cont = my_continent(p)
    if not cont:
        return None
    return get_tournament(cfg, year, cont)


def get_my_match(cfg, week, day=None, p=None, st=None):
    """[2026-08 이동] champions_engine.get_my_cl_match와 완전히 동일."""
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if st is None:
        st = get_state()
    if not p or not st:
        return None
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    t = my_tournament(cfg, p, st["current_year"])
    if not t or t["status"] == "done":
        return None
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != tid:
        return None

    conn = get_conn()
    if day is not None:
        m = conn.execute(
            f"""SELECT * FROM {cfg.match_table}
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?) AND (day=? OR day IS NULL OR day=0)""",
            (t["id"], week, tid, tid, day)).fetchone()
    else:
        m = conn.execute(
            f"""SELECT * FROM {cfg.match_table}
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?)""",
            (t["id"], week, tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    oe = conn.execute(
        f"SELECT team_name, flag FROM {cfg.entry_table} WHERE tournament_id=? AND team_id=?",
        (t["id"], opp_id)).fetchone()
    conn.close()
    return {
        "cl": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],
        "stage": m["stage"],
        "stage_ko": cfg.stage_ko.get(m["stage"], m["stage"]),
        "grp": m["grp"] if "grp" in m.keys() else "",
        "opp": oe["team_name"] if oe else "?",
        "opp_flag": oe["flag"] if oe else "",
        "is_home": is_home,
        "week": week,
    }


def has_my_match_between(cfg, week_from, week_to):
    """[2026-08 이동] champions_engine.has_my_cl_match_between과 동일."""
    for w in range(week_from, week_to + 1):
        if get_my_match(cfg, w):
            return True
    return False


def sim_my_match_as_ai(cfg, week, p, get_my_match_fn, reason="injury", day=None):
    """[2026-08 이동] champions_engine.sim_my_cl_match_as_ai와 완전히 동일 로직."""
    info = get_my_match_fn(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute(f"SELECT * FROM {cfg.tournament_table} WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute(f"SELECT * FROM {cfg.match_table} WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()
    if m["home_score"] != -1:
        return
    sim_ai_match(cfg, t, m, my_played=False, reason=reason)
    from game_engine import update_player, _calc_manager_rel
    update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False, not_played_penalty=2))


def simulate_my_match(cfg, week, p, get_my_match_fn, day=None):
    """[2026-08 이동] champions_engine.simulate_my_cl_match와 완전히 동일 로직.

    [설계 결정] 출전정지 카운터는 cl_suspension 컬럼 하나를 챔스/유로파/
    컨퍼런스가 공유한다 — 세 대회는 참가팀이 겹치지 않으므로(워터폴 구조상
    한 팀은 항상 셋 중 하나에만 속함) 한 시즌에 한 선수가 두 대회를 동시에
    뛸 일이 없어 별도 컬럼을 만들 실익이 없다."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _roll_red_card, _apply_red_card_dismissal,
                             _week_intl_cl_day, _log_highlight, _min_sortkey)
    from competition.champions_engine import _get_field_pos
    info = get_my_match_fn(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute(f"SELECT * FROM {cfg.tournament_table} WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute(f"SELECT * FROM {cfg.match_table} WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    he = entry(cfg, t["id"], m["home_team_id"])
    ae = entry(cfg, t["id"], m["away_team_id"])
    is_home = info["is_home"]

    _suspended, _new_susp = _check_suspended(p, field="cl_suspension")
    if _suspended:
        update_player(cl_suspension=_new_susp)
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event")

    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = max(0.0, _my_ovr - _team_ovr)
    _star = 1.0 + max(0.0, (_my_ovr - 60) / 40.0) ** 1.8 * 3.0
    bonus = _gap * 0.30 * _star + max(0.0, _my_ovr - 50) * 0.08
    bonus = _soft_cap(bonus, 30.0)
    from constants import PERSONALITY_EFFECTS
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if "team_win_bonus" in _pe:
        bonus *= (1.0 + _pe["team_win_bonus"])
    if _suspended:
        bonus = 0.0
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = match_outcome(h_ovr, a_ovr)
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "league")
    if outcome == "draw" and is_ko:
        win_home, pso_score = resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    if _suspended:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
        _absence_reason = "suspension"
    else:
        _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr)
        _absence_reason = None
        if _roll_red_card(p):
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(p, field="cl_suspension")
            _absence_reason = "red_card"
    if not _suspended and "big_match_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["big_match_rating"], 1)))
    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    day_val = _week_intl_cl_day(m["week"], p)

    conn = get_conn()
    conn.execute(f"""UPDATE {cfg.match_table} SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?,
                    my_played=?, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?,
                    day=?, my_absence_reason=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score,
                  0 if _suspended else 1, _get_field_pos(p),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  my_conceded, day_val, _absence_reason, m["id"]))
    conn.commit()
    conn.close()

    update_player(
        total_shots=p.get("total_shots", 0) + detail["shots"],
        total_shots_on=p.get("total_shots_on", 0) + detail["shots_on"],
        total_key_passes=p.get("total_key_passes", 0) + detail["key_passes"],
        total_dribbles=p.get("total_dribbles", 0) + detail["dribbles"],
        total_blocks=p.get("total_blocks", 0) + detail["blocks"],
    )

    _update_pop(p, goals, assists, rating)
    p2 = get_player()
    ns = min(100, p2["stress"] + 20)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    stage_ko = cfg.stage_ko.get(m["stage"], "")
    my_tid = p.get("current_team_id", 0)
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == my_tid else '패'})"
        rs = "무"

    comp_name = f"{t['name']} {stage_ko}".strip()
    home_disp = f"{he['flag']}{he['team_name']}({he.get('country','?')})"
    away_disp = f"{ae['flag']}{ae['team_name']}({ae.get('country','?')})"
    pso = {"won": pso_winner == my_tid, "score": pso_score} if pso_winner else None
    detail_id = _save_match_detail(
        p, week, comp_name, is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, True, False, detail, pso=pso)
    marker = f" [match:{detail_id}]" if detail_id else ""

    add_log("─" * 44, "sep")
    add_log(f"🏆 {comp_name}  {week}주차{marker}", "match")
    add_log(f"   {home_disp} {hs}-{as_} {away_disp}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")
    _timed = sorted([(int(e[0]), e[1]) if isinstance(e, tuple) else
                     (random.randint(1, 90), str(e)) for e in events],
                    key=lambda x: _min_sortkey(x[0]))
    hi = _log_highlight(goals, assists, _timed)
    if hi:
        add_log(f"   {hi}", "match")


def get_my_league_standings(cfg, year, direct_cut, playoff_pool):
    """[2026-08 이동] champions_engine.get_my_cl_league_standings와 동일."""
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return None
    my_tid = p["current_team_id"]
    t = my_tournament(cfg, p, year)
    if not t:
        return None
    rows = get_league_standings(cfg, t["id"])
    if not rows:
        return None
    return {
        "standings": rows, "my_team_id": my_tid,
        "direct_cut": direct_cut,
        "playoff_cut": direct_cut + playoff_pool,
    }


def get_my_matches_for_schedule(cfg, year):
    """[2026-08 이동] champions_engine.get_my_champions_matches와 동일."""
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return []
    t = my_tournament(cfg, p, year)
    if not t or not t.get("my_in"):
        return []
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != p.get("current_team_id", 0):
        return []

    conn = get_conn()
    entries = {r["team_id"]: dict(r) for r in conn.execute(
        f"SELECT team_id, team_name, flag, country FROM {cfg.entry_table} WHERE tournament_id=?",
        (t["id"],)).fetchall()}
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM {cfg.match_table} WHERE tournament_id=? ORDER BY week, slot",
        (t["id"],)).fetchall()]
    conn.close()

    def _name(tid):
        e = entries.get(tid, {})
        return f"{e.get('flag','')}{e.get('team_name','?')}"

    def _league(tid):
        return entries.get(tid, {}).get("country", "")

    out = []
    for m in rows:
        pso_name = ""
        if m["pso_winner"]:
            pso_name = _name(m["pso_winner"])
        out.append({
            "home_id": m["home_team_id"], "away_id": m["away_team_id"],
            "home_name": _name(m["home_team_id"]), "away_name": _name(m["away_team_id"]),
            "home_league": _league(m["home_team_id"]), "away_league": _league(m["away_team_id"]),
            "home_score": m["home_score"], "away_score": m["away_score"],
            "pso_winner": pso_name, "pso_score": m["pso_score"],
            "stage": cfg.stage_ko.get(m["stage"], m["stage"]), "week": m["week"],
            "stage_raw": m["stage"], "grp": m["grp"] if "grp" in m.keys() else "",
        })
    return out


def get_my_matches(cfg):
    """[2026-08 이동] champions_engine.get_my_cl_matches와 동일."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"""SELECT m.*, t.year AS t_year, t.name AS comp, t.my_team_id AS t_my_tid
           FROM {cfg.match_table} m
           JOIN {cfg.tournament_table} t ON m.tournament_id = t.id
           WHERE m.is_my = 1 AND m.home_score >= 0
           ORDER BY t.year, m.week""").fetchall()]
    _tids = {r["tournament_id"] for r in rows}
    names = {}
    if _tids:
        _ph = ",".join("?" * len(_tids))
        names = {(r["tournament_id"], r["team_id"]): (r["team_name"], r["flag"], r["country"])
                 for r in conn.execute(
                     f"SELECT tournament_id, team_id, team_name, flag, country "
                     f"FROM {cfg.entry_table} WHERE tournament_id IN ({_ph})",
                     tuple(_tids)).fetchall()}
    conn.close()

    out = []
    for m in rows:
        my_tid = m["t_my_tid"]
        is_home = (m["home_team_id"] == my_tid)
        opp_id = m["away_team_id"] if is_home else m["home_team_id"]
        my_s = m["home_score"] if is_home else m["away_score"]
        op_s = m["away_score"] if is_home else m["home_score"]

        if m["pso_winner"]:
            won = (m["pso_winner"] == (m["home_team_id"] if is_home else m["away_team_id"]))
            result = "승(PSO)" if won else "패(PSO)"
        elif my_s > op_s:
            result = "승"
        elif my_s < op_s:
            result = "패"
        else:
            result = "무"

        my_name, my_flag, _my_country = names.get(
            (m["tournament_id"], m["home_team_id"] if is_home else m["away_team_id"]), ("", "", ""))
        opp_name, opp_flag, opp_country = names.get((m["tournament_id"], opp_id), ("?", "", ""))
        if opp_country:
            opp_name = f"{opp_name}({opp_country})"

        from constants import day_to_iso_date_str, week_to_iso_date_str
        date_str = (day_to_iso_date_str(m["t_year"], m["day"]) if m.get("day")
                    else week_to_iso_date_str(m["t_year"], m["week"]))

        out.append({
            "year": m["t_year"], "week": m["week"], "date": date_str,
            "position": m["my_position"], "team": my_name, "team_flag": my_flag,
            "comp": m["comp"], "stage": cfg.stage_ko.get(m["stage"], m["stage"]),
            "opp": opp_name, "opp_flag": opp_flag,
            "goals": m["my_goals"], "assists": m["my_assists"],
            "saves": m["my_saves"], "conceded": op_s,
            "rating": m["my_rating"],
            "shots": m.get("my_shots", 0), "shots_on": m.get("my_shots_on", 0),
            "key_passes": m.get("my_key_passes", 0), "dribbles": m.get("my_dribbles", 0),
            "blocks": m.get("my_blocks", 0), "pass_acc": m.get("my_pass_acc", 0),
            "score": f"{my_s}-{op_s}", "result": result,
            "absence_reason": m.get("my_absence_reason"),
            "my_played": m.get("my_played", 0),
        })
    return out


def save_trophy(cfg, year, team_name, competition, result):
    """champions_engine._save_trophy와 동일(tier=-1로 클럽 국제대회 구분 —
    대회 등급 무관 공용 규칙)."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=?",
        (year, competition)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,-1,?)""", (year, team_name, result, competition))
        conn.commit()
    conn.close()