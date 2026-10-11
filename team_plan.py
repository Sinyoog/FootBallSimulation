# -*- coding: utf-8 -*-
"""team_plan.py — 팀 경기 계획(포메이션·선발·전술)의 단일 창구.

[2026-10 신설, 신민용 확정 방향: "나중에 플레이어가 감독(→ 구단주)이 된다.
감독은 경기마다 포메이션 | 선수 선택 | 전술(공격 성향·빌드업·수비 라인·관리
성향)을 고르고, 그 선택에 따라 경기 결과가 달라져야 한다. 지금 당장 만들진
않지만 들어갈 자리는 미리 만들어 둔다."]

── 왜 이 모듈이 필요한가 ────────────────────────────────────────
지금까지 "이 팀이 이번 경기에 어떤 모양으로 나오는가"는 엔진마다 따로 정했다.
  · 포메이션    : teams.formation (AI가 시즌마다 _shuffle_formations로 재검토)
  · 선발 11명   : 전술엔진(내 경기)은 match_flow._select_lineup, 빠른 시뮬(AI
                  경기)은 선발 개념 없이 OVR 상위 11명 평균
  · 전술 성향   : 전술엔진이 database.get_team_manager로 직접 조회
결정 주체가 언제나 AI 하나라서 문제가 없었지만, 플레이어 감독이 들어오면
"누가 정했나"를 엔진마다 다시 물어야 한다. 그래서 결정을 이 모듈 하나로
모은다 — 엔진은 build_team_plan()이 돌려주는 계획만 받고, 그 계획을 AI가
짰는지 사용자가 짰는지는 몰라도 된다.

    결정 주체(controller) ─┬─ "AI"   : 감독 성향 + 기존 AI 로직(지금과 100% 동일)
                           └─ "USER" : 사용자가 저장한 선택(user_team_plans)
                                         ↓
                              build_team_plan(team_id) → 경기 엔진

── 저장 위치(중복 저장 없음) ───────────────────────────────────────
  · 포메이션    : teams.formation        — AI·사용자 공통. 사용자가 바꾸면 여기에 쓴다.
  · 전술 4축    : managers.style_attack / style_buildup / style_press / manager_type
                  — 사용자 감독도 감독 행 하나를 갖는다(managers.is_user=1). 그래서
                  세계 기록실·감독 검색·경질/계약 같은 기존 감독 화면·로직이 사용자
                  감독도 그대로 다룬다.
  · 선발/벤치   : user_team_plans(team_id PK) — 사용자 팀만. AI 팀은 저장하지 않고
                  매번 계산한다(지금과 동일).

── 지금(최소 조치) 범위 ──────────────────────────────────────────
  · 사용자 감독이 없으면(=지금 모든 게임) 모든 경로가 예전과 비트 단위로 같다.
  · 사용자 감독 부임/해임 진입점(appoint_user_manager / release_user_manager)만
    있고, 화면(UI)·감독 모드 게임 진행 루프는 아직 없다.
  · AI 결정부(포메이션 재검토, 감독 시장의 경질·재계약·이직·은퇴, 대표팀 후보,
    감독 능력 경기 보정)는 사용자 팀/감독을 건드리지 않는다 — 각 호출부 주석 참고.
  · 사용자 선택이 실제로 결과에 닿는 곳: 전술엔진(tactical_engine.simulate_my_match).
    포메이션·선발 11명·빌드업·수비 라인은 이미 경기 결과를 바꾸고, 공격 성향은
    자리만 연결돼 있다(constants.MANAGER_ATTACK_* — 전부 0). 관리 성향은 경기 중
    전술이 아니라 "선수 기용·컨디션"으로 작용해야 해서 이 계획에 실어 보내기만 한다.
  · 빠른 시뮬(AI끼리 경기): ⑦(constants.PLAN_STRENGTH_ENABLED)이 켜지면 전력 =
    이 계획의 선발 11명 유효 OVR 평균(quick_strength) + 클럽 강도 + 명문 보너스.
    사용자 감독 팀은 사용자가 고른 11명이 그대로 전력이 된다.
"""
import json
import sqlite3

CONTROLLER_AI = "AI"
CONTROLLER_USER = "USER"

# 계획에 실어 보내는 전술 키 — 감독 행의 컬럼 이름 그대로.
TACTIC_KEYS = ("style_attack", "style_buildup", "style_press", "manager_type")


# ════════════════════════════════════════════════════════════════
# 통제 주체
# ════════════════════════════════════════════════════════════════
def _with_conn(conn):
    if conn is not None:
        return conn, False
    from database import get_conn
    return get_conn(), True


def user_control(conn=None):
    """{team_id: manager_id} — 지금 사용자 감독이 맡고 있는 클럽.

    사용자 감독이 없으면 빈 dict다(지금 모든 게임). managers.is_user에는 부분 인덱스
    (is_user=1 행만)가 걸려 있어 감독 수와 무관하게 조회가 싸다. 컬럼이 아직 없는
    구세이브(init_db 이전)에서도 빈 dict."""
    conn, own = _with_conn(conn)
    try:
        rows = conn.execute(
            """SELECT tm.team_id, m.id FROM managers m
               JOIN team_managers tm ON tm.manager_id = m.id
               WHERE m.is_user = 1 AND tm.end_year IS NULL
                 AND COALESCE(tm.job_kind, 'club') = 'club'""").fetchall()
        return {int(r[0]): int(r[1]) for r in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        if own:
            conn.close()


def user_team_ids(conn=None):
    return set(user_control(conn))


def user_manager_ids(conn=None):
    """사용자 감독 행 전부(무직 상태 포함). AI 감독 시장 후보에서 뺄 때 쓴다."""
    conn, own = _with_conn(conn)
    try:
        return {int(r[0]) for r in conn.execute(
            "SELECT id FROM managers WHERE is_user = 1").fetchall()}
    except sqlite3.OperationalError:
        return set()
    finally:
        if own:
            conn.close()


def team_controller(team_id, conn=None):
    return CONTROLLER_USER if team_id in user_control(conn) else CONTROLLER_AI


# ════════════════════════════════════════════════════════════════
# 사용자 계획 저장/조회
# ════════════════════════════════════════════════════════════════
def _valid_tactic(key, value):
    from constants import (TACTIC_TENDENCIES, MANAGER_BUILDUP_STYLES,
                           MANAGER_PRESS_STYLES, MANAGER_TYPES)
    allowed = {"style_attack": TACTIC_TENDENCIES, "style_buildup": MANAGER_BUILDUP_STYLES,
               "style_press": MANAGER_PRESS_STYLES, "manager_type": list(MANAGER_TYPES)}[key]
    return value in allowed


def get_user_plan(team_id, conn=None):
    """사용자 팀의 저장된 선발/벤치 — {"lineup_ids": [...], "bench_ids": [...]} 또는 None."""
    conn, own = _with_conn(conn)
    try:
        row = conn.execute(
            "SELECT lineup_json, bench_json FROM user_team_plans WHERE team_id=?",
            (team_id,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        if own:
            conn.close()
    if not row:
        return None
    try:
        lineup = json.loads(row[0] or "[]")
        bench = json.loads(row[1] or "[]")
    except (TypeError, ValueError):
        return None
    return {"lineup_ids": lineup, "bench_ids": bench}


def save_user_plan(team_id, formation=None, lineup_ids=None, bench_ids=None,
                   tactics=None, conn=None):
    """사용자 감독의 선택을 저장한다. 사용자 팀이 아니면 ValueError.

    formation : FORMATION_SLOTS 키. teams.formation에 쓰고 그 팀의 스쿼드 적합도
                (formation_fit_bonus)도 새 포메이션 기준으로 다시 계산한다 — 빠른 시뮬
                (_formation_bias)도 사용자가 고른 포메이션을 그대로 반영한다.
    lineup_ids: 슬롯 순서의 선수 id 11개(None 허용 = 그 자리는 AI가 채움).
    bench_ids : 교체 명단 선수 id 목록(없으면 AI가 채움).
    tactics   : {"style_attack"|"style_buildup"|"style_press"|"manager_type": 값} —
                사용자 감독 행에 쓴다. 허용되지 않는 값은 ValueError.
    """
    from constants import FORMATION_SLOTS
    conn, own = _with_conn(conn)
    try:
        mid = user_control(conn).get(team_id)
        if mid is None:
            raise ValueError(f"team {team_id}는 사용자 감독 팀이 아니다")
        if formation is not None:
            if formation not in FORMATION_SLOTS:
                raise ValueError(f"알 수 없는 포메이션: {formation}")
            _set_team_formation(conn, team_id, formation)
        if tactics:
            sets, vals = [], []
            for k, v in tactics.items():
                if k not in TACTIC_KEYS or not _valid_tactic(k, v):
                    raise ValueError(f"허용되지 않는 전술 값: {k}={v}")
                sets.append(f"{k}=?")
                vals.append(v)
            conn.execute(f"UPDATE managers SET {', '.join(sets)} WHERE id=?", vals + [mid])
            if "style_attack" in tactics:   # 레거시 컬럼도 같이(시장 hires 경로와 같은 원칙)
                conn.execute("UPDATE teams SET tactic_tendency=? WHERE id=?",
                             (tactics["style_attack"], team_id))
        if lineup_ids is not None or bench_ids is not None:
            old = get_user_plan(team_id, conn) or {}
            lu = list(lineup_ids) if lineup_ids is not None else old.get("lineup_ids", [])
            bn = list(bench_ids) if bench_ids is not None else old.get("bench_ids", [])
            conn.execute(
                """INSERT INTO user_team_plans(team_id, lineup_json, bench_json)
                   VALUES(?,?,?)
                   ON CONFLICT(team_id) DO UPDATE SET lineup_json=excluded.lineup_json,
                                                      bench_json=excluded.bench_json""",
                (team_id, json.dumps(lu), json.dumps(bn)))
        conn.commit()
    finally:
        if own:
            conn.close()


def clear_user_plan(team_id, conn=None):
    conn, own = _with_conn(conn)
    try:
        conn.execute("DELETE FROM user_team_plans WHERE team_id=?", (team_id,))
        conn.commit()
    finally:
        if own:
            conn.close()


def _set_team_formation(conn, team_id, formation):
    """포메이션 + 그 포메이션 기준 스쿼드 적합도를 같이 갱신하고 엔진 캐시를 그 팀만 비운다."""
    from constants import FORMATION_SLOTS
    import formation_logic as _fl
    import sub_positions as _subp
    if _subp.active():          # [2026-10 주포·부포] 부포 보유자가 있을 때만 id까지 읽어 부포를 붙인다
        roster = _subp.attach([{"position": r[0], "ovr": r[1], "id": r[2]} for r in conn.execute(
            "SELECT position, ovr, id FROM ai_players WHERE team_id=?", (team_id,)).fetchall()])
    else:
        roster = [{"position": r[0], "ovr": r[1]} for r in conn.execute(
            "SELECT position, ovr FROM ai_players WHERE team_id=?", (team_id,)).fetchall()]
    fit = _fl.formation_fit_bonus(
        _fl.formation_fit_penalty(roster, FORMATION_SLOTS[formation])) if roster else 0.0
    conn.execute("UPDATE teams SET formation=?, formation_fit_bonus=? WHERE id=?",
                 (formation, fit, team_id))
    _drop_engine_cache(team_id)


def _drop_engine_cache(team_id):
    """game_engine 세션 캐시에서 이 팀 항목만 뺀다(전체를 비우면 시즌 중 다른 팀 값까지
    다시 계산돼 결과가 바뀔 수 있다)."""
    try:
        import game_engine as _ge
    except Exception:
        return
    for name in ("_team_formation_cache", "_team_formation_fit_cache", "_mgr_match_bonus_cache"):
        cache = getattr(_ge, name, None)
        if isinstance(cache, dict):
            cache.pop(team_id, None)


# ════════════════════════════════════════════════════════════════
# 계획 조립 — 경기 엔진이 부르는 곳
# ════════════════════════════════════════════════════════════════
def _style_of(team_id, conn=None):
    """감독 행에서 전술 4축만. 감독이 없으면 None(전술엔진이 예전처럼 보정 0)."""
    try:
        from database import get_team_manager
        m = get_team_manager(team_id, conn)
    except Exception:
        return None
    if not m:
        return None
    return {k: m.get(k) for k in TACTIC_KEYS}


def _resolve_lineup(team_id, formation, ids):
    """저장된 id 목록 → 슬롯 순서 선수 dict. 지금 그 팀에 없는 선수(이적·은퇴)나 빈
    자리는 AI 선발 로직이 남은 선수로 채운다 — 사용자 선택이 깨져도 경기는 열린다."""
    from match_sim.match_flow import _fetch_roster, _select_lineup
    by_id = {p.get("id"): p for p in _fetch_roster(team_id)}
    fixed, seen = [], set()
    for pid in list(ids or [])[:11]:
        p = by_id.get(pid)
        if p is None or pid in seen:
            fixed.append(None)
        else:
            fixed.append(p)
            seen.add(pid)
    return _select_lineup(team_id, formation, fixed=fixed)


def _resolve_bench(team_id, lineup, ids):
    from match_sim.match_flow import _fetch_roster, select_bench
    if not ids:
        return select_bench(team_id, lineup)
    used = {p.get("id") for p in lineup if p}
    by_id = {p.get("id"): p for p in _fetch_roster(team_id)}
    out = []
    for pid in ids:
        p = by_id.get(pid)
        if p is not None and pid not in used:
            out.append(p)
            used.add(pid)
    return out


def build_team_plan(team_id, formation, conn=None, with_lineup=True):
    """이 팀이 이번 경기에 나오는 모양.

    반환: {"team_id", "controller", "formation", "lineup", "bench", "style"}
      formation : 호출부가 정한 포메이션(지금은 모든 호출부가 teams.formation을 넘긴다
                  — 사용자 팀은 save_user_plan이 그 값을 바꿔 둔다).
      lineup    : FORMATION_SLOTS 순서 선수 dict 11개(with_lineup=False면 None).
      bench     : 교체 명단(with_lineup=False면 None).
      style     : {style_attack, style_buildup, style_press, manager_type} 또는 None.

    AI 팀은 예전 tactical_engine.simulate_my_match 안에서 하던 호출과 똑같은 함수를
    똑같은 인자로 부른다(_select_lineup → select_bench, get_team_manager) — 난수를
    쓰지 않는 함수들이라 결과가 비트 단위로 같다."""
    from match_sim.match_flow import select_bench
    ctrl = team_controller(team_id, conn)
    lineup = bench = None
    if with_lineup:
        up = get_user_plan(team_id, conn) if ctrl == CONTROLLER_USER else None
        if up and up.get("lineup_ids"):
            lineup = _resolve_lineup(team_id, formation, up["lineup_ids"])
            bench = _resolve_bench(team_id, lineup, up.get("bench_ids"))
        else:
            lineup = ai_lineup(team_id, formation)
            bench = select_bench(team_id, lineup)
    return {"team_id": team_id, "controller": ctrl, "formation": formation,
            "lineup": lineup, "bench": bench, "style": _style_of(team_id, conn)}


# ════════════════════════════════════════════════════════════════
# [⑦ 2026-10 신설, 신민용 확정] 포메이션 기준 선발 11명 전력
#
#   AI 포메이션 → 20개 슬롯 → 최적 배치(헝가리안) → 포지션 미스매치 + 주발
#   → 실제 선발 11명 유효 OVR 평균 → (+클럽 강도 + 명문 보너스, game_engine)
#   → AI 경기 전력
#
# · 선수-슬롯 점수 = formation_logic._slot_value(OVR × (1 − 포지션 미스매치) × 주발
#   배율) — 화면의 스쿼드 적합도와 같은 "유효 적합도"다. GK 슬롯엔 GK만, 필드 슬롯엔
#   필드 선수만(둘 다 후보가 없을 때만 큰 비용으로 넘나든다 — _select_lineup과 같은 원칙).
# · 나이는 넣지 않는다(신민용 확정) — 나이 선호는 관리 성향(유스 중시/베테랑 신뢰)을
#   AI 선발에 넣을 때 따로 다룬다. OVR이 곧 실력이므로 전력 계산에서 깎지 않는다.
# · 순차 배정(_greedy_fill_slots/_select_lineup)은 같은 선수단이라도 배정 순서 때문에
#   평균 0.4, 최대 3.2 손해를 봤다(정적 측정, 11,415팀). 최적 배치는 전 팀 3.5초.
# · 스위치 constants.PLAN_STRENGTH_ENABLED — 꺼져 있으면 아래 함수들은 아무 데서도
#   경기 결과에 쓰이지 않는다(예전과 비트 단위 동일).
# ════════════════════════════════════════════════════════════════
_INF = 1e18
_CROSS_GK_COST = 1e6     # GK↔필드 경계를 넘는 배치(후보가 없을 때만 일어나게)


def _hungarian(cost):
    """n행 ≤ m열 최소 비용 배정(헝가리안, O(n²m)). 반환: 행별 열 인덱스."""
    n, m = len(cost), len(cost[0])
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [_INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = _INF
            j1 = 0
            ci = cost[i0 - 1]
            ui0 = u[i0]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = ci[j - 1] - ui0 - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    res = [None] * n
    for j in range(1, m + 1):
        if p[j]:
            res[p[j] - 1] = j - 1
    return res


def optimal_lineup(roster, formation):
    """roster(선수 dict 목록)에서 formation 슬롯 순서대로 최적 11명. 선수가 모자라면
    남는 슬롯은 None. 같은 입력이면 항상 같은 결과(로스터를 id 순으로 정렬해서 푼다)."""
    from constants import FORMATION_SLOTS
    return optimal_lineup_slots(roster, FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"]))


def _roster_sort_key(p):
    """id 순 정렬 키 — 정수 id는 예전 키 (id is None, id or 0)와 같은 순서. 내 선수처럼
    정수가 아닌 id(시즌 기록의 "__ME__" 등)는 정수 뒤로 보낸다(섞여도 비교 오류 없음)."""
    i = p.get("id")
    if i is None:
        return (2, 0, "")
    if isinstance(i, int):
        return (0, i, "")
    return (1, 0, str(i))


def optimal_lineup_slots(roster, slots):
    """optimal_lineup과 같은 최적 배치를 슬롯 이름 목록(slots)으로 받는다."""
    import formation_logic as _fl
    ro = sorted((p for p in roster if p), key=_roster_sort_key)
    if not ro:
        return [None] * len(slots)
    n_dummy = max(0, len(slots) - len(ro))
    cost = []
    for s in slots:
        s_gk = (s == "GK")
        row = []
        for pl in ro:
            c = -_fl._slot_value(pl, s)
            if s_gk != ((pl.get("position") or "") == "GK"):
                c += _CROSS_GK_COST
            row.append(c)
        row.extend([0.0] * n_dummy)        # 선수 부족분 — 빈 자리
        cost.append(row)
    assign = _hungarian(cost)
    return [ro[j] if (j is not None and j < len(ro)) else None for j in assign]


def club_fill_slots(candidates, slots_only):
    """[⑦-c 2026-10, 신민용 확정 A안 — 채택] 클럽 시즌 기록·포메이션 화면의 선발 11명 배정.

    constants.LINEUP_UNIFY_ENABLED(와 ⑦ PLAN_STRENGTH_ENABLED)가 켜져 있으면 경기 전력과
    같은 최적 배치(optimal_lineup_slots — 나이 없음)로 고르고, 꺼져 있으면 예전
    formation_logic._greedy_fill_slots 그대로다(비트 단위 동일). 반환 형식도
    _greedy_fill_slots와 같다 — 슬롯 순서 리스트(빈 자리 None), 배정된 선수 dict에
    "_slot_idx" 태깅. 후보 dict에 실린 "role_age"는 최적 배치에서 쓰지 않는다(나이 선호는
    관리 성향 단계에서 감독별로). 주전 안의 "핵심"·주전 밖 티어 순서는 호출부의
    compute_squad_roles가 예전처럼 정한다."""
    from constants import LINEUP_UNIFY_ENABLED, PLAN_STRENGTH_ENABLED
    import formation_logic as _fl
    if not (LINEUP_UNIFY_ENABLED and PLAN_STRENGTH_ENABLED):
        return _fl._greedy_fill_slots(candidates, slots_only)
    placed = optimal_lineup_slots(candidates, slots_only)
    for i, pl in enumerate(placed):
        if pl is not None:
            pl["_slot_idx"] = i
    return placed


def lineup_strength(lineup, formation):
    """선발 11명의 유효 OVR 평균(빈 자리는 제외). 선수가 하나도 없으면 None."""
    from constants import FORMATION_SLOTS
    import formation_logic as _fl
    slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
    vals = [_fl._slot_value(p, s) for p, s in zip(lineup or [], slots) if p]
    return (sum(vals) / len(vals)) if vals else None


def plan_strength(plan):
    """계획(build_team_plan 결과)의 선발 11명 전력 — 클럽 강도·명문 보너스 제외."""
    return lineup_strength(plan.get("lineup"), plan.get("formation"))


def ai_lineup(team_id, formation):
    """AI 감독의 선발 11명. ⑦ 스위치가 켜지면 최적 배치, 꺼져 있으면 예전 _select_lineup."""
    from constants import PLAN_STRENGTH_ENABLED
    from match_sim.match_flow import _fetch_roster, _select_lineup
    if PLAN_STRENGTH_ENABLED:
        return optimal_lineup(_fetch_roster(team_id), formation)
    return _select_lineup(team_id, formation)


def quick_strength(c, team_id, formation):
    """빠른 시뮬용 선발 11명 전력(game_engine._team_avg_ovr가 스위치 켬일 때 부른다).
    사용자 감독 팀이 선발을 저장해 뒀으면 그 11명, 아니면 AI 최적 배치. 호출부 커서 c로
    로스터를 읽는다(_team_avg_ovr의 기존 질의와 같은 방식). 선수가 없으면 None."""
    if team_id in user_control():
        up = get_user_plan(team_id)
        if up and up.get("lineup_ids"):
            return lineup_strength(_resolve_lineup(team_id, formation, up["lineup_ids"]),
                                   formation)
    roster = [dict(r) for r in c.execute(
        "SELECT * FROM ai_players WHERE team_id=?", (team_id,)).fetchall()]
    import sub_positions as _subp
    _subp.attach(roster)        # [2026-10 주포·부포] 부포 슬롯은 미스매치 0 — 부포가 없으면 무동작
    return lineup_strength(optimal_lineup(roster, formation), formation)


# ════════════════════════════════════════════════════════════════
# 사용자 감독 부임 / 해임 — "플레이어가 감독이 될 때 들어가는" 진입점
# ════════════════════════════════════════════════════════════════
def appoint_user_manager(team_id, year, name=None, nationality=None, birth_year=None,
                         manager_id=None, tactics=None, contract_years=3, conn=None):
    """사용자를 team_id 클럽의 감독으로 앉힌다. 반환: 사용자 감독 id.

    manager_id를 주면 그 감독 행을 사용자 감독으로 바꾼다(예: 은퇴한 내 선수가 이미
    선수 출신 감독 행을 갖고 있는 경우). 없으면 새 행을 만든다(name·nationality·
    birth_year 필요).

    기존 현직 감독은 이번 해 "replaced"로 재임을 닫고 무직이 된다 — 그 뒤로는 일반
    감독과 똑같이 시장에서 움직인다. 사용자 감독은 AI 시장(경질·재계약·이직·은퇴)과
    대표팀 후보에서 빠진다(ai_lifecycle._manager_turnover, national_manager 주석).
    경질·제의·계약 만료 같은 사용자 감독 커리어 이벤트는 감독 모드를 만들 때 붙인다."""
    from constants import (TACTIC_TENDENCIES, MANAGER_BUILDUP_STYLES,
                           MANAGER_PRESS_STYLES)
    conn, own = _with_conn(conn)
    try:
        year = int(year)
        if team_id in user_control(conn):
            raise ValueError(f"team {team_id}는 이미 사용자 감독 팀이다")
        # 사용자 감독은 한 번에 한 팀만.
        if manager_id is None:
            if not name or not nationality or not birth_year:
                raise ValueError("새 사용자 감독에는 name/nationality/birth_year가 필요하다")
            conn.execute(
                """INSERT INTO managers(name, nationality, birth_year, retired, from_player_id,
                                         style_attack, style_buildup, style_press, manager_type)
                   VALUES(?,?,?,0,NULL,?,?,?,?)""",
                (name, nationality, int(birth_year), TACTIC_TENDENCIES[2],
                 MANAGER_BUILDUP_STYLES[1], MANAGER_PRESS_STYLES[1], "뚝심형"))
            manager_id = conn.execute("SELECT MAX(id) FROM managers").fetchone()[0]
        else:
            row = conn.execute("SELECT retired FROM managers WHERE id=?",
                               (manager_id,)).fetchone()
            if not row:
                raise ValueError(f"감독 {manager_id} 없음")
            busy = conn.execute(
                "SELECT team_id FROM team_managers WHERE manager_id=? AND end_year IS NULL",
                (manager_id,)).fetchone()
            if busy and int(busy[0]) != team_id:
                raise ValueError(f"감독 {manager_id}는 다른 팀(team {busy[0]}) 재임 중")
        # 기존 현직 감독 → 무직
        for lk in conn.execute(
                """SELECT id, manager_id FROM team_managers
                   WHERE team_id=? AND end_year IS NULL AND COALESCE(job_kind,'club')='club'""",
                (team_id,)).fetchall():
            if int(lk[1]) == int(manager_id):
                continue
            conn.execute("UPDATE team_managers SET end_year=?, end_reason='replaced' WHERE id=?",
                         (year, lk[0]))
            conn.execute(
                "UPDATE managers SET status='free', contract_until=0, "
                "jobless_since=COALESCE(jobless_since, ?) WHERE id=? AND retired=0",
                (year, lk[1]))
        until = year + max(1, int(contract_years))
        if not conn.execute(
                "SELECT 1 FROM team_managers WHERE team_id=? AND manager_id=? AND end_year IS NULL",
                (team_id, manager_id)).fetchone():
            conn.execute(
                """INSERT INTO team_managers(team_id, manager_id, start_year, end_year,
                                              job_kind, country_id, job_level, contract_log)
                   VALUES(?,?,?,NULL,'club',NULL,0,?)""",
                (team_id, manager_id, year, f"{year}:{until}"))
        conn.execute(
            "UPDATE managers SET is_user=1, retired=0, status='club', contract_until=?, "
            "jobless_since=NULL, clubs_managed=COALESCE(clubs_managed,0)+1 WHERE id=?",
            (until, manager_id))
        conn.commit()
        if tactics:
            save_user_plan(team_id, tactics=tactics, conn=conn)
        _drop_engine_cache(team_id)
        return int(manager_id)
    finally:
        if own:
            conn.close()


def release_user_manager(team_id, year, reason="resigned", conn=None):
    """사용자 감독이 team_id를 떠난다(사임 등). 팀은 다음 시즌 시장에서 AI가 공석으로
    채운다(현직 재임 행이 없는 팀 = 공석 — _manager_turnover). 사용자 감독 행은
    is_user=1인 채 무직으로 남는다. 반환: 떠난 감독 id 또는 None."""
    conn, own = _with_conn(conn)
    try:
        mid = user_control(conn).get(team_id)
        if mid is None:
            return None
        conn.execute(
            """UPDATE team_managers SET end_year=?, end_reason=?
               WHERE team_id=? AND manager_id=? AND end_year IS NULL""",
            (int(year), reason, team_id, mid))
        conn.execute("UPDATE managers SET status='free', contract_until=0, jobless_since=? "
                     "WHERE id=?", (int(year), mid))
        conn.execute("DELETE FROM user_team_plans WHERE team_id=?", (team_id,))
        conn.commit()
        _drop_engine_cache(team_id)
        return mid
    finally:
        if own:
            conn.close()