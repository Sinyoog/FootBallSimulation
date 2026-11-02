"""[2026-10 신설, 신민용 확정] 내 선수 임대 — 구단 임대 추진 · 선수 임대 희망.

설계 요약(대화에서 확정한 규칙 그대로):
  - 구단 계획(핵심/육성/정리/일반)을 먼저 정하고 행동은 계획에서 나온다.
    육성 → 구단 임대 추진, 정리 → 판매 추진 우선(판매 추진이 꺼져 있을 때만 임대 추진),
    핵심 → 임대 희망 거절. 같은 선수에게 판매·임대를 동시에 추진하지 않는다.
  - 구단 임대 추진은 22주(상반기 출전)·40주(시즌 출전) 두 번 판정, 제안 창에서 수락/거부.
  - 임대 희망은 언제든 제출 → 다음 주 구단 승인/거절 → 승인되면 2~6주에 걸쳐 오퍼 0~3개.
  - 실제 이동은 예약 후 처음 오는 이동 시점(4주 상반기 시작 / 29주 하반기 시작)에만.
  - 기간은 6개월 단위(6개월~2년). 복귀는 29주(상반기 끝) 또는 시즌 끝.
  - 같은 두 구단 사이 누적 임대는 반 시즌 단위로 최대 2년, 계약 잔여 안에서만.
  - 임대 희망 거절이 두 시즌 연속이면 "임대 불만"(기존 transfer_rejection_count와 별개).
  - 정리 목적 임대를 거부하면 그 시즌·다음 시즌 벤치 확률 보정(시즌 끝 재판정으로 해제).

판정용 난수는 전역 random을 소비하지 않는 별도 Random을 쓴다 — 내 선수에게 임대 일이
없는 시나리오에서 AI 세계 결과가 수정 전과 같게 유지되도록.
"""
import json
import random
import zlib

from constants import (FIRST_HALF_START, SECOND_HALF_START, MILITARY_COUNTRY,
                       MY_LOAN_RANGE, MY_LOAN_HALVES_WEIGHTS, MY_LOAN_MAX_PAIR_HALVES,
                       MY_LOAN_MAX_OFFERS, MY_LOAN_PUSH_WEEKS, MY_LOAN_PLAY_RATIO_MAX,
                       MY_LOAN_MIN_TEAM_MATCHES, MY_LOAN_YOUTH_MAX_AGE, MY_LOAN_START_RATIO_MAX,
                       MY_LOAN_REFUSE_CLEAR_BENCH_MULT, MY_LOAN_WISH_OFFER_DELAY,
                       MY_LOAN_WISH_OFFER_COUNT_W, MY_LOAN_WISH_APPROVE,
                       MY_LOAN_FRUSTRATION_STREAK, MY_LOAN_FRUSTRATION_YEARS,
                       MY_LOAN_BUY_WINTER_WEEKS, MY_LOAN_REJUDGE_WEEK)


def _ge():
    import game_engine
    return game_engine


def _rng(*salt):
    """전역 random 상태를 읽기만 하고(소비 X) 판정마다 독립 Random을 만든다."""
    try:
        st = random.getstate()[1]
        base = (st[0], st[1], st[2], st[-1])
    except Exception:
        base = ()
    return random.Random(zlib.crc32(repr((base, salt)).encode("utf-8")))


def _loads(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False) if obj else ""


def _abs(y, w):
    return int(y) * 52 + int(w)


# ── 시간 규칙 ────────────────────────────────────────────────────
def next_window(year, week):
    """예약 시점(year, week) 이후 처음 오는 이동 시점. 그 주차의 이동 처리는 이미
    끝난 것으로 본다(29주에 예약하면 다음 해 4주)."""
    if week < FIRST_HALF_START:
        return (year, FIRST_HALF_START)
    if week < SECOND_HALF_START:
        return (year, SECOND_HALF_START)
    return (year + 1, FIRST_HALF_START)


def loan_return_point(exec_year, exec_week, halves):
    """이동 시점 + 반 시즌 수 → (복귀 연도, 복귀 반기). 반기 1 = 그 해 29주 복귀(상반기 끝),
    반기 2 = 그 해 시즌 끝 복귀."""
    h0 = exec_year * 2 + (0 if exec_week == FIRST_HALF_START else 1)
    he = h0 + int(halves) - 1
    return he // 2, (he % 2) + 1


def halves_text(halves):
    y, h = divmod(int(halves), 2)
    if y and h:
        return f"{y}년 6개월"
    if y:
        return f"{y}년"
    return "6개월"


# ── 임대 연봉 부담 (2026-10 신설, 신민용 확정) ──────────────────────
# 연봉 자체는 언제나 원 소속팀 계약 그대로다(임대처·승강과 무관). 여기서 정하는 건 "그 연봉을
# 누가 내느냐"뿐 — 나중에 구단주 모드에서 구단 지출을 계산할 때 이 값을 그대로 쓴다.
#   · 육성 목적 임대  → 원 소속팀 100%
#   · 임대처 주전급   → 임대 구단 100%
#   · 임대처 로테급   → 50% / 50%
#   · 대기급          → 임대처가 애초에 받지 않는다(find_destinations에서 이미 제외)
def wage_split(purpose, role):
    """(임대 구단 부담 %, 원 소속팀 부담 %). 대기급(받지 않음)이면 None."""
    if purpose == "육성":
        return (0, 100)
    if role == "주전":
        return (100, 0)
    if role == "주전 경쟁":
        return (50, 50)
    return None


def wage_split_text(split):
    if not split:
        return ""
    host, parent = split
    if host >= 100:
        return "연봉 부담: 임대 구단 100%"
    if parent >= 100:
        return "연봉 부담: 원 소속팀 100%"
    return f"연봉 부담: 임대 구단 {host}% · 원 소속팀 {parent}%"


def current_loan_record(p=None):
    """진행 중인 내 임대의 loan_history_json 항목(없으면 None) — 임대처 = 지금 소속팀."""
    p = p if p is not None else (_ge().get_player() or {})
    if not p.get("loan_from_team_id") or _serving(p):
        return None
    for h in reversed(_loads(p.get("loan_history_json")) or []):
        if h.get("to") == p.get("current_team_id") and h.get("from") == p.get("loan_from_team_id"):
            return h
    return None


def current_wage_split(p=None):
    """진행 중인 임대의 (임대 구단 %, 원 소속팀 %) — 임대 중이 아니면 None.
    예전 세이브의 임대(부담 기록 없음)는 원 소속팀 100%로 본다."""
    rec = current_loan_record(p)
    if rec is None:
        p = p if p is not None else (_ge().get_player() or {})
        return (0, 100) if (p.get("loan_from_team_id") and not _serving(p)) else None
    if "wage_host_pct" in rec:
        h = int(rec.get("wage_host_pct") or 0)
        return (h, 100 - h)
    return (0, 100)


def planned_loan_text(p=None, team_id=None):
    """[2026-10 신민용 요청: "임대 간 기간엔 계약에 몇 년 임대인지(1년이면 1년) 떠야 한다"]
    진행 중인 임대의 예정 기간 문구. 지금 그 팀(team_id)에 임대 중이 아니면 ""."""
    p = p if p is not None else (_ge().get_player() or {})
    if not p.get("loan_from_team_id") or _serving(p):
        return ""
    if team_id is not None and team_id != p.get("current_team_id"):
        return ""
    rec = current_loan_record(p)
    if rec and rec.get("halves"):
        return halves_text(int(rec["halves"]))
    # 예전 방식 임대(반기 기록 없음) — 임대 시작 시점부터 예정 복귀 시점까지
    sy, sw = p.get("loan_start_year") or 0, p.get("loan_start_week") or 0
    ey = p.get("loan_end_year") or 0
    if sy and ey:
        h0 = sy * 2 + (0 if sw <= FIRST_HALF_START else 1)
        he = ey * 2 + (0 if (p.get("loan_end_half") or 2) == 1 else 1)
        return halves_text(max(1, he - h0 + 1))
    return ""


# ── 상태 조회 ────────────────────────────────────────────────────
def _serving(p):
    try:
        return _ge()._my_is_serving(p)
    except Exception:
        return False


def _team_league(conn, tid):
    return conn.execute(
        """SELECT t.id, t.name, t.formation, t.league_id AS lid, l.name AS lname, l.tier,
                  cn.id AS cid, cn.name AS country, cn.flag, cn.grade, cn.continent
           FROM teams t JOIN leagues l ON t.league_id=l.id
           JOIN countries cn ON l.country_id=cn.id WHERE t.id=?""", (tid,)).fetchone()


def _stint_start(conn, p, year):
    """현재 팀에서 이번 시즌 출전 집계가 시작된 시점 — 이번 시즌 중 합류했으면 합류 주차."""
    tid = p.get("current_team_id") or 0
    row = conn.execute(
        "SELECT start_year, start_week FROM career_entries WHERE team_id=? AND end_year=0 "
        "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if row and row["start_year"] == year:
        return year, int(row["start_week"] or 1)
    return year, 1


def season_play_ratio(p, year, week):
    """(출전 경기 / 그 기간 팀 리그 경기 수, 팀 리그 경기 수)."""
    ge = _ge()
    tid = p.get("current_team_id") or 0
    if not tid:
        return 0.0, 0
    conn = ge.get_conn()
    try:
        tr = _team_league(conn, tid)
        sy, sw = _stint_start(conn, p, year)
    finally:
        conn.close()
    if not tr:
        return 0.0, 0
    n = ge.team_matches_played_in_window(tid, tr["lname"], sy, sw, year, week) or 0
    if not n:
        return 0.0, 0
    return min(1.0, (p.get("season_matches") or 0) / float(n)), n


def club_plan(p, year, week):
    """구단 계획 — "핵심" / "육성" / "정리" / "일반" (판정 불가면 ""). 읽기 전용."""
    ge = _ge()
    if not p or not p.get("current_team_id") or p.get("loan_from_team_id") or _serving(p):
        return "", {}
    role = p.get("contract_role") or ""
    rank_cat = ge._my_team_rank_category(p)
    info = {"rank_cat": rank_cat, "role": role}
    if role in ("핵심 선수", "주전 보장") or rank_cat in ("에이스", "핵심"):
        return "핵심", info
    ratio, n = season_play_ratio(p, year, week)
    info.update(ratio=ratio, team_matches=n)
    if n < MY_LOAN_MIN_TEAM_MATCHES:
        return "일반", info
    # 육성(③, 신민용 확정): 23세 이하 + 리그 선발 비율 30% 미만 + 선발권 밖. 선발 비율의 분모는
    # 출전 비율과 같은 기간의 팀 리그 경기 수(이번 시즌·이 팀 합류 이후). 엘리트 OVR 제외와
    # 팀 수준 격차 조건은 없앴다 — 절대 OVR이 아니라 이 팀 안의 실제 선발 경쟁으로 본다.
    starts = p.get("season_league_starts") or 0
    start_ratio = min(1.0, starts / float(n))
    comp = own_slot_competition(p)
    outside = bool(comp) and comp[0] >= comp[1]
    info.update(starts=starts, start_ratio=start_ratio, slot_better=(comp or (None, None))[0],
                slot_count=(comp or (None, None))[1], outside=outside)
    if ((p.get("age") or 30) <= MY_LOAN_YOUTH_MAX_AGE and start_ratio < MY_LOAN_START_RATIO_MAX
            and outside):
        return "육성", info
    if rank_cat == "후보" and ratio < MY_LOAN_PLAY_RATIO_MAX:
        return "정리", info
    return "일반", info


def _busy_reason(p):
    """새 임대 진행(제출·추진)을 막는 상태. 없으면 ""."""
    if not p.get("current_team_id"):
        return "소속팀이 없습니다."
    if p.get("loan_from_team_id"):
        return "이미 임대 중입니다."
    if _serving(p):
        return "복무 중에는 임대할 수 없습니다."
    if p.get("pending_join_transfer_json") or p.get("pending_sale_transfer_json"):
        return "이미 예약된 이적이 있습니다."
    if p.get("pending_loan_json"):
        return "이미 예약된 임대가 있습니다."
    if p.get("club_loan_proposal_json"):
        return "구단의 임대 제안에 먼저 답해야 합니다."
    if p.get("sale_push_proposal_json"):
        return "구단의 판매 제안에 먼저 답해야 합니다."
    if p.get("loan_wish_json"):
        w = _loads(p.get("loan_wish_json")) or {}
        if w.get("status") == "denied":
            return "임대 희망이 거절돼 다음 이동 시점까지 다시 낼 수 없습니다."
        return "이미 임대 희망을 냈습니다."
    return ""


def loan_push_active(p):
    """판매 추진과 겹치지 않게 막는 상태 — 구단 임대 제안 대기 또는 임대 예약."""
    return bool(p and (p.get("club_loan_proposal_json") or p.get("pending_loan_json")))


def bench_mult(p):
    """정리 목적 임대 거부 → 그 시즌·다음 시즌 벤치 확률 보정(같은 팀에 있을 때만)."""
    ry = (p or {}).get("loan_refused_year") or 0
    if not ry:
        return 1.0
    if (p.get("current_team_id") or 0) != (p.get("loan_refused_tid") or 0):
        return 1.0
    if (p.get("current_year") or 0) > ry + 1:
        return 1.0
    return MY_LOAN_REFUSE_CLEAR_BENCH_MULT


def loan_frustration_active(p, year):
    fy = (p or {}).get("loan_frustration_year") or 0
    return bool(fy) and (int(year) - fy) < MY_LOAN_FRUSTRATION_YEARS


# ── 행선지 탐색 ──────────────────────────────────────────────────
def _pair_halves(p, parent, dest):
    hist = _loads(p.get("loan_history_json")) or []
    return sum(int(h.get("halves") or 0) for h in hist
               if h.get("from") == parent and h.get("to") == dest)


def slot_competition(conn, team_id, formation, p):
    """그 팀의 실제 포메이션에서 내 포지션 선발 경쟁 — (나보다 OVR 높은 같은 포지션 선수 수,
    내 포지션 슬롯 수). 내 포지션 = 주포 + 부포(부포 슬롯도 내 자리), 포메이션에 둘 다 없으면
    호환 포지션 상위 2개(POSITION_COMPAT). 슬롯이 하나도 없으면 None.
    선발권 밖 = 앞의 수 ≥ 뒤의 수(내 자리 슬롯을 나보다 강한 선수들이 이미 다 채움)."""
    from constants import FORMATION_SLOTS, POSITION_COMPAT
    slots = FORMATION_SLOTS.get(formation or "4-4-2", FORMATION_SLOTS["4-4-2"])
    primary = p.get("position") or "CM"
    try:
        import sub_positions as _subp
        sub = _subp.my_sub()
    except Exception:
        sub = None
    mine = {primary} | ({sub} if sub else set())
    my_slots = [s for s in slots if s in mine]
    if not my_slots:
        mine = set(POSITION_COMPAT.get(primary, [primary])[:2])
        my_slots = [s for s in slots if s in mine]
    if not my_slots:
        return None
    ovr = p.get("ovr") or 40
    q = ",".join("?" for _ in mine)
    n_better = conn.execute(
        f"SELECT COUNT(*) FROM ai_players WHERE team_id=? AND position IN ({q}) AND ovr>?",
        (team_id, *mine, ovr)).fetchone()[0]
    return n_better, len(my_slots)


def own_slot_competition(p):
    """지금 소속팀에서의 선발 경쟁(slot_competition). 소속팀이 없으면 None."""
    tid = (p or {}).get("current_team_id") or 0
    if not tid:
        return None
    conn = _ge().get_conn()
    try:
        r = conn.execute("SELECT formation FROM teams WHERE id=?", (tid,)).fetchone()
        return slot_competition(conn, tid, r["formation"] if r else None, p)
    finally:
        conn.close()


def _position_role(conn, team_row, p):
    """임대처에서 주전으로 뛸 수 있는지(slot_competition과 같은 기준).
    반환: "주전" / "주전 경쟁" / None(부적합 — 내 자리 슬롯이 이미 더 강한 선수로 참)."""
    comp = slot_competition(conn, team_row["id"], team_row["formation"], p)
    if not comp or comp[0] >= comp[1]:
        return None
    return "주전" if comp[0] == 0 else "주전 경쟁"


def _pick_halves(p, parent, dest, exec_y, exec_w, rng):
    weights = MY_LOAN_HALVES_WEIGHTS[exec_w]
    used = _pair_halves(p, parent, dest)
    cend = p.get("contract_end_year") or 0
    allowed = [n for n in sorted(weights)
               if used + n <= MY_LOAN_MAX_PAIR_HALVES
               and cend and loan_return_point(exec_y, exec_w, n)[0] <= cend]
    if not allowed:
        return None
    return rng.choices(allowed, weights=[weights[n] for n in allowed], k=1)[0]


def find_destinations(p, purpose, exec_y, exec_w, rng, k=MY_LOAN_MAX_OFFERS):
    """목적별 팀 평균 범위 + 포지션 조건 + 기간 조건을 통과한 임대처 최대 k곳.
    국내 → 같은 대륙 → 전세계 순서(기존 임대처 탐색과 같은 순서)."""
    ge = _ge()
    parent = p.get("current_team_id") or 0
    ovr = p.get("ovr") or 40
    lo, hi = MY_LOAN_RANGE[purpose]
    conn = ge.get_conn()
    try:
        origin = _team_league(conn, parent)
        rows = conn.execute(
            """SELECT t.id, t.name, t.formation, t.league_id AS lid, l.name AS lname, l.tier,
                      cn.id AS cid, cn.name AS country, cn.flag, cn.grade, cn.continent,
                      AVG(a.ovr) AS avg_ovr
               FROM teams t JOIN leagues l ON t.league_id=l.id
               JOIN countries cn ON l.country_id=cn.id
               JOIN ai_players a ON a.team_id=t.id
               WHERE t.id != ? AND cn.name != ?
               GROUP BY t.id HAVING AVG(a.ovr) BETWEEN ? AND ?
               ORDER BY t.id""",
            (parent, MILITARY_COUNTRY, ovr + lo, ovr + hi)).fetchall()
        if origin:
            stages = [[r for r in rows if r["cid"] == origin["cid"]],
                      [r for r in rows if r["cid"] != origin["cid"]
                       and r["continent"] == origin["continent"]],
                      [r for r in rows if r["continent"] != origin["continent"]]]
        else:
            stages = [list(rows)]
        found = []
        for stage in stages:
            stage = list(stage)
            rng.shuffle(stage)
            for r in stage[:300]:
                if len(found) >= k:
                    break
                role = _position_role(conn, r, p)
                if not role:
                    continue
                halves = _pick_halves(p, parent, r["id"], exec_y, exec_w, rng)
                if not halves:
                    continue
                ry, rh = loan_return_point(exec_y, exec_w, halves)
                split = wage_split(purpose, role)
                if not split:
                    continue   # 대기급 — 임대처가 받지 않는다
                found.append({
                    "team_id": r["id"], "team_name": r["name"], "league_id": r["lid"],
                    "league_name": r["lname"], "tier": r["tier"], "country": r["country"],
                    "flag": r["flag"] or "", "team_avg": round(r["avg_ovr"] or 0, 1),
                    "role": role, "halves": halves, "return_year": ry, "return_half": rh,
                    "exec_year": exec_y, "exec_week": exec_w, "purpose": purpose,
                    "wage_host_pct": split[0],
                })
            if len(found) >= k:
                break
        return found
    finally:
        conn.close()


# ── 구단 임대 추진 ───────────────────────────────────────────────
def _club_push_check(p, year, week):
    ge = _ge()
    if week not in MY_LOAN_PUSH_WEEKS:
        return
    key = year * 100 + week
    if (p.get("club_loan_push_last") or 0) == key:
        return
    ge.update_player(club_loan_push_last=key)
    if _busy_reason(p):
        return   # 임대 불가 상태(임대 중·복무·이적/임대 예약·판매 제안 대기 등) — 판매 추진도 그대로
    plan, info = club_plan(p, year, week)
    sale_active = bool(p.get("sale_push_active"))
    sale_shown = bool(p.get("allow_club_sale_push", 1))
    loan_shown = bool(p.get("allow_club_loan_push", 1))
    if plan == "정리":
        if sale_active and sale_shown:
            return   # 정리 목적은 판매 우선 — 판매 추진 중이면 임대를 추진하지 않는다
        # [2026-10 신민용 확정] 판매추진 OFF(판매 제안을 안 보겠다고 함)면 숨은 판매 추진
        # 상태보다 정리 임대가 우선 — 아래에서 임대 제안을 실제로 만들 때 판매 추진을 푼다.
        purpose = "정리"
    elif plan == "육성":
        purpose = "육성"   # 육성은 임대 우선(신민용 확정) — 단, 실제 임대 제안이 만들어질 때만
    else:
        return
    ex_y, ex_w = next_window(year, week)
    offers = find_destinations(p, purpose, ex_y, ex_w, _rng("push", year, week))
    if not offers:
        return   # 조건을 통과한 임대처가 없으면 임대를 추진하지 않고, 판매 추진 상태도 건드리지 않는다
    # 판매 추진을 푸는 경우: 육성(임대 우선) 또는 판매추진 OFF 상태의 정리(위 분기 참고).
    release_sale = sale_active and (purpose == "육성" or not sale_shown)
    kw = {}
    if release_sale:
        # 임대 제안을 실제로 만드는 이 순간에만 판매 추진을 해제한다(동시 추진 금지).
        # 임대 제안 대기·예약 중엔 판매 추진 판정이 멈추고, 거부·만료되면 기존 판매 판정이 다시 돈다.
        kw.update(sale_push_active=0, sale_push_low_score_weeks=0, sale_push_refused_count=0)
    if not loan_shown:
        # [2026-10 신민용 확정] 임대추진 OFF — 판매추진 토글과 같은 "알림만 숨김" 방식. 구단은
        # 판정·임대처 탐색·제안을 똑같이 하지만 창·로그 없이 자동 거부로 처리한다. 거부 효과는
        # 직접 거부와 같다(육성 영향 없음, 정리는 출전 우선순위 하락) — 메인 화면에 표시된다.
        if purpose == "정리":
            kw.update(loan_refused_year=year, loan_refused_tid=p.get("current_team_id") or 0)
        if kw:
            ge.update_player(**kw)
        return
    kw["club_loan_proposal_json"] = _dumps({
        "purpose": purpose, "offers": offers, "made": [year, week],
        "exec": [ex_y, ex_w], "plan_info": {k: info.get(k) for k in ("ratio", "rank_cat")},
        "sale_push_released": release_sale})
    ge.update_player(**kw)
    if release_sale and sale_shown:
        ge.add_log("📋 구단은 판매 대신 육성 임대를 택했습니다 — 판매 추진을 멈춥니다.", "event", year, week)
    why = ("출전 경험을 쌓게 하려고" if purpose == "육성"
           else "다음 시즌 계획에서 비중이 낮아")
    ge.add_log(f"📋 구단이 {why} 임대를 추진합니다 — 임대처 후보 {len(offers)}곳 "
               f"(이동: {ex_y}년 {ex_w}주)", "event", year, week)


def accept_club_loan_offer(idx):
    ge = _ge()
    p = ge.get_player() or {}
    prop = _loads(p.get("club_loan_proposal_json"))
    if not prop or not (0 <= idx < len(prop.get("offers") or [])):
        return False, "대기 중인 구단 임대 제안이 없습니다."
    return _reserve(p, dict(prop["offers"][idx]), "club_loan_proposal_json")


def reject_club_loan_proposal():
    """구단 임대 추진 거부 — 육성 목적이면 영향 없음, 정리 목적이면 출전 우선순위 하락."""
    ge = _ge()
    p = ge.get_player() or {}
    prop = _loads(p.get("club_loan_proposal_json"))
    if not prop:
        return False
    st = ge.get_state() or {}
    kw = {"club_loan_proposal_json": ""}
    if prop.get("purpose") == "정리":
        kw.update(loan_refused_year=st.get("current_year") or 0,
                  loan_refused_tid=p.get("current_team_id") or 0)
        ge.add_log("🚫 임대를 거부했습니다 — 구단은 당신을 계획 밖으로 보고 있어 출전 기회가 "
                   "줄어들 수 있습니다.", "event")
    else:
        ge.add_log("🚫 임대를 거부하고 팀에 남습니다 — 출전은 실력과 경쟁 상황에 따라 정해집니다.",
                   "event")
    ge.update_player(**kw)
    return True


# ── 선수 임대 희망 ───────────────────────────────────────────────
def can_submit_wish(p=None):
    p = p or _ge().get_player() or {}
    r = _busy_reason(p)
    return (not r), r


def submit_loan_wish():
    ge = _ge()
    p = ge.get_player() or {}
    ok, why = can_submit_wish(p)
    if not ok:
        return False, why
    st = ge.get_state() or {}
    y, w = st.get("current_year"), st.get("current_week")
    ge.update_player(loan_wish_json=_dumps({"status": "submitted", "submit": [y, w]}))
    ge.add_log("📝 구단에 임대 희망을 전달했습니다 — 다음 주에 구단의 답이 옵니다.", "event", y, w)
    return True, ""


def _decide_wish(p, wish, year, week):
    ge = _ge()
    rng = _rng("wish", year, week)
    plan, info = club_plan(p, year, week)
    count_denial = True
    if p.get("sale_push_active"):
        ok, reason, count_denial = False, "구단은 임대가 아니라 판매를 추진하고 있습니다.", False
    elif plan == "핵심":
        ok, reason = False, "구단은 당신을 핵심 전력으로 보고 있어 임대를 허락하지 않습니다."
    elif plan in ("육성", "정리"):
        ok, reason = True, ""
    else:
        ratio = info.get("ratio") or 0.0
        base, ratio_w, rel_w, pmin, pmax = MY_LOAN_WISH_APPROVE
        prob = base + (0.5 - ratio) * ratio_w + ((p.get("manager_relation") or 50) - 50) * rel_w
        prob = max(pmin, min(pmax, prob))
        ok = rng.random() < prob
        reason = "" if ok else "구단은 지금은 당신이 팀에 필요하다고 판단했습니다."
    if not ok:
        tgt = next_window(year, week)
        ge.update_player(loan_wish_json=_dumps({"status": "denied", "submit": wish.get("submit"),
                                                "decided": [year, week], "target": list(tgt),
                                                "reason": reason}),
                         **({"loan_wish_denied_season": year} if count_denial else {}))
        ge.add_log(f"🚫 임대 희망 거절 — {reason}", "event", year, week)
        return
    now = _abs(year, week)
    tgt = next_window(year, week)
    if _abs(*tgt) - now < 2:
        tgt = next_window(*tgt)
    found = find_destinations(p, "희망", tgt[0], tgt[1], rng)
    n = min(len(found), rng.choices([1, 2, 3], weights=MY_LOAN_WISH_OFFER_COUNT_W, k=1)[0])
    offers = []
    for o in found[:n]:
        arr = now + rng.randint(*MY_LOAN_WISH_OFFER_DELAY)
        o["arrive"] = max(now + 1, min(_abs(*tgt) - 1, arr))
        o["announced"] = False
        offers.append(o)
    ge.update_player(loan_wish_json=_dumps({"status": "approved", "submit": wish.get("submit"),
                                            "decided": [year, week], "target": list(tgt),
                                            "offers": offers}))
    ge.add_log("✅ 구단이 임대를 허락했습니다 — 관심 구단의 임대 오퍼를 기다립니다"
               + ("" if offers else " (지금은 관심 구단이 없습니다)"), "event", year, week)


def visible_wish_offers(p=None, year=None, week=None):
    ge = _ge()
    p = p or ge.get_player() or {}
    wish = _loads(p.get("loan_wish_json")) or {}
    if wish.get("status") != "approved":
        return []
    if year is None:
        st = ge.get_state() or {}
        year, week = st.get("current_year"), st.get("current_week")
    now = _abs(year, week)
    return [o for o in (wish.get("offers") or []) if o.get("arrive", 0) <= now]


def accept_wish_offer(idx):
    ge = _ge()
    p = ge.get_player() or {}
    vis = visible_wish_offers(p)
    if not (0 <= idx < len(vis)):
        return False, "수락할 수 있는 임대 오퍼가 없습니다."
    return _reserve(p, dict(vis[idx]), "loan_wish_json")


def cancel_loan_wish():
    """승인 대기·오퍼 대기 중인 임대 희망을 스스로 거둔다(불이익 없음). 거절된 희망은 못 거둠."""
    ge = _ge()
    p = ge.get_player() or {}
    wish = _loads(p.get("loan_wish_json")) or {}
    if not wish or wish.get("status") == "denied":
        return False
    ge.update_player(loan_wish_json="")
    ge.add_log("↩ 임대 희망을 거뒀습니다.", "event")
    return True


# ── 예약 · 실행 ──────────────────────────────────────────────────
def _reserve(p, offer, clear_field):
    """오퍼 수락 → 처음 오는 이동 시점으로 예약. 수락 시점이 바뀌었으면 이동 시점과
    복귀 시점을 다시 계산하고, 계약·누적 조건을 다시 확인한다."""
    ge = _ge()
    if p.get("pending_join_transfer_json") or p.get("pending_sale_transfer_json"):
        return False, "이미 예약된 이적이 있습니다."
    st = ge.get_state() or {}
    y, w = st.get("current_year"), st.get("current_week")
    ex_y, ex_w = next_window(y, w)
    parent = p.get("current_team_id") or 0
    halves = int(offer.get("halves") or 2)
    ry, rh = loan_return_point(ex_y, ex_w, halves)
    if not p.get("contract_end_year") or ry > p.get("contract_end_year"):
        return False, "남은 계약 기간 안에 임대 기간이 들어가지 않습니다."
    if _pair_halves(p, parent, offer["team_id"]) + halves > MY_LOAN_MAX_PAIR_HALVES:
        return False, "같은 구단 사이 임대는 누적 2년을 넘을 수 없습니다."
    offer.update(exec_year=ex_y, exec_week=ex_w, return_year=ry, return_half=rh,
                 parent_tid=parent, accepted=[y, w])
    offer.pop("arrive", None)
    offer.pop("announced", None)
    ge.update_player(pending_loan_json=_dumps(offer), **{clear_field: ""})
    ge.add_log(f"✅ {offer['team_name']} 임대 수락 — {ex_y}년 {ex_w}주에 합류합니다 "
               f"(기간 {halves_text(halves)})", "event", y, w)
    return True, ""


def cancel_pending_loan_for_transfer(team_name=""):
    """다른 이적을 예약하면 임대 예약은 취소된다(가장 마지막 결정이 우선)."""
    ge = _ge()
    p = ge.get_player() or {}
    raw = _loads(p.get("pending_loan_json"))
    if not raw:
        return False
    ge.update_player(pending_loan_json="")
    ge.add_log(f"↩ 이적 예약으로 {raw.get('team_name', '')} 임대 예약이 취소됐습니다.", "event")
    return True


_SEASON_STAT_RESET = dict(
    season_matches=0, season_goals=0, season_assists=0,
    season_saves=0, season_rating_sum=0.0, season_rating_cnt=0,
    season_goals_against=0,
    season_shots=0, season_shots_on=0, season_key_passes=0,
    season_dribbles=0, season_blocks=0,
    season_pass_acc_sum=0, season_pass_acc_cnt=0,
    season_red_cards_league=0,
    season_yellow_league=0,
    yellow_susp_progress_league=0,
    season_injury_matches_missed=0,
    season_suspension_matches_missed=0,
    season_bench_matches_missed=0,
    season_league_starts=0)


def _field_pos_for(formation, p):
    """join_team과 같은 규칙으로 새 팀 포메이션 기준 배치 포지션 계산."""
    try:
        from constants import POSITION_COMPAT, FORMATION_SLOTS
        slots = FORMATION_SLOTS.get(formation or "4-4-2", FORMATION_SLOTS["4-4-2"])
        primary = p.get("position", "CM")
        compat = POSITION_COMPAT.get(primary, [primary])
        import sub_positions as _subp
        my_sub = _subp.my_sub()
        from constants import SUB_POS_RANK as _SPR_l   # [2026-10] 부포 = OVR −1 → 순위 0과 1 사이
        best_pos, best_rank, best_found = primary, 0, 999
        for slot in slots:
            if my_sub and slot == my_sub:
                rank = _SPR_l
            elif slot in compat:
                rank = compat.index(slot)
            else:
                continue
            if rank < best_found:
                best_found, best_pos, best_rank = rank, slot, rank
        return best_pos, best_rank
    except Exception:
        return p.get("position", "CM"), 0


def _state_time(year, week):
    st = _ge().get_state() or {}
    return st.get("current_year") or year, st.get("current_week") or week


def _sync_new_league(team_row, team_id, year, week):
    ge = _ge()
    st = ge.get_state() or {}
    season = st.get("current_season", 1)
    ge._generate_adjacent_schedules(team_row["lid"], season, year)
    ge._backfill_past_matches(team_row["lid"], season, week, team_id)
    ge._sync_teams_from_results(team_row["lid"], season)


def _execute_pending_loan(year, week):
    ge = _ge()
    p = ge.get_player() or {}
    offer = _loads(p.get("pending_loan_json"))
    if not offer:
        return False
    if _abs(offer.get("exec_year", 0), offer.get("exec_week", 0)) > _abs(year, week):
        return False
    ge.update_player(pending_loan_json="")
    name = offer.get("team_name", "")
    if _serving(p):
        ge.add_log(f"🪖 복무 중이라 {name} 임대 예약이 취소됐습니다.", "event", year, week)
        return False
    if p.get("pending_join_transfer_json"):
        ge.add_log(f"↩ 이적 예약이 있어 {name} 임대 예약이 취소됐습니다.", "event", year, week)
        return False
    if (p.get("current_team_id") or 0) != offer.get("parent_tid") or p.get("loan_from_team_id"):
        ge.add_log(f"⚠️ 소속이 바뀌어 {name} 임대 예약이 취소됐습니다.", "event", year, week)
        return False
    conn = ge.get_conn()
    try:
        host = _team_league(conn, offer["team_id"])
        parent = _team_league(conn, offer["parent_tid"])
    finally:
        conn.close()
    if not host or not parent:
        ge.add_log(f"⚠️ {name} 임대 예약이 취소됐습니다(팀 정보 없음).", "event", year, week)
        return False
    halves = int(offer.get("halves") or 2)
    ry, rh = loan_return_point(year, week, halves)   # 실제 이동 시점 기준으로 확정
    st = ge.get_state() or {}
    if ge._pending_transfer_type:
        ge._ensure_career_entry(p, st)
    # 커리어 행은 기존 예약 이적(join_team)과 같이 "지금 상태 주차"로 닫는다(이동 시점 직전 주).
    cy, cw = _state_time(year, week)
    ge._save_career_entry(p, cy, cw, force_new=True, exit_type="임대",
                          allow_insert=False, loan_partner_team=host["name"])
    ge._pending_transfer_type = "임대"
    fpos, mrank = _field_pos_for(host["formation"], p)
    hist = _loads(p.get("loan_history_json")) or []
    # [2026-10 신민용 확정] 연봉 부담 비율 — 오퍼에 없으면(이 버전 이전에 수락한 예약) 목적·역할로 다시 정한다.
    _split = (((int(offer["wage_host_pct"]), 100 - int(offer["wage_host_pct"])))
              if offer.get("wage_host_pct") is not None
              else (wage_split(offer.get("purpose"), offer.get("role")) or (0, 100)))
    hist.append({"from": offer["parent_tid"], "to": host["id"], "halves": halves,
                 "start": [year, week], "purpose": offer.get("purpose") or "",
                 "role": offer.get("role") or "", "wage_host_pct": _split[0]})
    ge.update_player(current_team_id=host["id"], current_league_id=host["lid"],
                     current_tier=host["tier"], manager_relation=50,
                     # D(나): 복귀 때 되돌릴 원 소속팀 감독 관계(임대 직전 값)
                     loan_parent_relation=int(p.get("manager_relation") if p.get("manager_relation") is not None else 50),
                     loan_from_team_id=parent["id"], loan_from_league_id=parent["lid"],
                     loan_from_tier=parent["tier"], loan_end_year=ry, loan_end_half=rh,
                     loan_start_year=year, loan_start_week=week, loan_start_tid=host["id"],
                     loan_history_json=json.dumps(hist, ensure_ascii=False),
                     field_pos=fpos, mismatch_rank=mrank,
                     loan_wish_denial_streak=0, loan_frustration_year=0,
                     loan_refused_year=0, loan_refused_tid=0,
                     **_SEASON_STAT_RESET, **ge._my_manager_type_kw(host["id"]))
    ge._make_room_on_join(host["id"], host["country"], host["tier"], year, log_week=week)
    # [2026-10] 외국인 쿼터는 정원 정리 뒤에(나 포함) — game_engine.join_team과 같은 순서.
    ge._enforce_foreign_quota_on_join(host["id"], host["country"], p.get("nationality", ""))
    _sync_new_league(host, host["id"], year, week)
    ge.add_log(f"🔄 {host['name']}로 임대!  {host['lname']}({host['tier']}부)  |  연봉·계약은 "
               f"원소속팀 그대로  |  기간 {halves_text(halves)} "
               f"({ry}년 {'29주' if rh == 1 else '시즌 끝'} 복귀)  |  {wage_split_text(_split)}",
               "event", year, week)
    return True


def _is_new_loan(p):
    return bool(p.get("loan_from_team_id") and p.get("loan_end_half")
                and p.get("loan_start_tid") == p.get("current_team_id"))


def _midseason_loan_end(year, week):
    """29주: 상반기 끝에 끝나는 임대(반기 1) — 완전 영입 수락분은 이적, 아니면 복귀."""
    ge = _ge()
    p = ge.get_player() or {}
    if not (_is_new_loan(p) and p.get("loan_end_half") == 1
            and (p.get("loan_end_year") or 0) <= year) or _serving(p):
        return False
    if p.get("loan_buy_proposal_json"):
        ge.update_player(loan_buy_proposal_json="")   # 결정 안 한 제안은 소멸
    raw = _loads(p.get("pending_loan_buy_json"))
    if raw:
        ge.update_player(pending_loan_buy_json="")
        if p.get("current_team_id") == raw.get("team_id"):
            return _midseason_loan_buy(p, year, week, raw)
    return _midseason_return(p, year, week)


def _midseason_return(p, year, week):
    ge = _ge()
    conn = ge.get_conn()
    try:
        parent = _team_league(conn, p["loan_from_team_id"])
    finally:
        conn.close()
    clear = dict(loan_from_team_id=0, loan_from_league_id=0, loan_from_tier=0,
                 loan_end_year=0, loan_end_half=0, loan_start_year=0, loan_start_week=0,
                 loan_start_tid=0, loan_parent_relation=-1)
    # D(나): 임대처에서 쌓인 감독 관계 대신 임대 직전 원 소속팀 값으로 복원(저장값 없으면 그대로).
    _pr = p.get("loan_parent_relation")
    if _pr is not None and _pr >= 0:
        clear["manager_relation"] = int(_pr)
    if not parent:
        ge.update_player(**clear)
        return False
    cy, cw = _state_time(year, week)
    ge._save_career_entry(p, cy, cw, force_new=True, exit_type="임대 종료",
                          allow_insert=False, loan_partner_team=parent["name"])
    ge._pending_transfer_type = "임대 복귀"
    fpos, mrank = _field_pos_for(parent["formation"], p)
    ge.update_player(current_team_id=parent["id"], current_league_id=parent["lid"],
                     current_tier=parent["tier"], field_pos=fpos, mismatch_rank=mrank,
                     **clear, **_SEASON_STAT_RESET, **ge._my_manager_type_kw(parent["id"]))
    ge._make_room_on_return(parent["id"], year, "내 임대 복귀로 자리를 마련했습니다.")
    _sync_new_league(parent, parent["id"], year, week)
    ge.add_log(f"🔄 임대 종료 → {parent['name']}로 복귀!  ({parent['lname']} {parent['tier']}부)",
               "event", year, week)
    return True


def _midseason_loan_buy(p, year, week, proposal):
    ge = _ge()
    c_yrs = int(proposal.get("contract_years") or 1)
    fee = int(proposal.get("transfer_fee") or 0)
    cy, cw = _state_time(year, week)
    ge._save_career_entry(p, cy, cw, force_new=True, exit_type="", allow_insert=False)
    ge._pending_transfer_type = "완전 이적"
    ge._pending_transfer_fee = fee
    # 시즌 중 계약 = 이번 시즌 하반기 + c_yrs시즌(join_team의 시즌 중 계약 규칙과 같음)
    # [2026-10] 해외 구단 + 미필이면 30세 시즌까지(game_engine.my_clamp_contract_end)
    try:
        conn = ge.get_conn()
        try:
            _hr = _team_league(conn, p.get("current_team_id") or 0)
        finally:
            conn.close()
        _end = max(year, ge.my_clamp_contract_end(year + c_yrs, p, _hr["country"] if _hr else None))
        c_yrs = max(0, _end - year)
    except Exception:
        pass
    ge.update_player(salary=int(proposal.get("salary") or p.get("salary") or 0),
                     contract_years=c_yrs, contract_end_year=year + c_yrs,
                     loan_from_team_id=0, loan_from_league_id=0, loan_from_tier=0,
                     loan_end_year=0, loan_end_half=0, loan_start_year=0, loan_start_week=0,
                     loan_start_tid=0, loan_parent_relation=-1, transfer_requested=0,
                     club_tenure_seasons=1, **_SEASON_STAT_RESET)
    ge.add_log(f"✍ {proposal.get('team_name', '')} 완전 이적 확정!  |  이적료 {ge.fmt_money(fee)}  "
               f"|  {c_yrs}년 6개월 계약", "event", year, week)
    return True


def loan_end_text(p):
    """임대가 끝나는 시점 문구 — 29주 복귀 새 임대는 "하반기가 시작되면", 그 외 "시즌이 끝나면"."""
    if _is_new_loan(p or {}) and (p or {}).get("loan_end_half") == 1:
        return "하반기가 시작되면(29주)"
    return "시즌이 끝나면"


def loan_buy_window_ok(p, year, week):
    """임대처 완전 영입 판정 시점 — 반기 1 복귀(29주)는 겨울 휴식기, 그 외는 기존 규칙."""
    if _is_new_loan(p) and p.get("loan_end_half") == 1:
        lo, hi = MY_LOAN_BUY_WINTER_WEEKS
        return (p.get("loan_end_year") or 0) == year and lo <= week <= hi
    return None   # 기존 규칙(마지막 시즌 44주 이후) 그대로


def loan_buy_ratio(p, year, week):
    """새 임대는 합류 이후(이번 시즌 안) 팀 리그 경기 수를 분모로 쓴다. 기존 임대는 None."""
    if not _is_new_loan(p):
        return None
    ge = _ge()
    host = p.get("current_team_id") or 0
    conn = ge.get_conn()
    try:
        tr = _team_league(conn, host)
    finally:
        conn.close()
    if not tr:
        return 0.0
    if (p.get("loan_start_year") or 0) == year:
        sy, sw = year, int(p.get("loan_start_week") or 1)
    else:
        sy, sw = year, 1
    n = ge.team_matches_played_in_window(host, tr["lname"], sy, sw, year, week) or 0
    if not n:
        return 0.0
    return min(1.0, (p.get("season_matches") or 0) / float(n))


def _expire(year, week):
    """이동 시점: 수락 안 한 오퍼·답 안 한 제안·거절 후 재제출 금지를 정리한다."""
    ge = _ge()
    p = ge.get_player() or {}
    now = _abs(year, week)
    kw = {}
    prop = _loads(p.get("club_loan_proposal_json"))
    if prop and _abs(*prop.get("exec", [0, 0])) <= now:
        kw["club_loan_proposal_json"] = ""
    wish = _loads(p.get("loan_wish_json"))
    if wish:
        tgt = wish.get("target")
        if tgt and _abs(*tgt) <= now:
            kw["loan_wish_json"] = ""
            if wish.get("status") == "approved":
                ge.add_log("⌛ 이번 이동 시점까지 수락한 임대 오퍼가 없어 임대 희망이 마감됐습니다.",
                           "event", year, week)
    if kw:
        ge.update_player(**kw)


# ── 엔진 연결 지점 ───────────────────────────────────────────────
def window_hook(year, week):
    """_advance_week가 4주·29주에 진입할 때, 기존 예약 이적 실행보다 먼저 부른다.
    순서: 반기 1 임대 종료(완전 영입/복귀) → 새 임대 실행 → 만료 정리."""
    if week == SECOND_HALF_START:
        _midseason_loan_end(year, week)
    _execute_pending_loan(year, week)
    _expire(year, week)


def year_start_hook(year):
    """새해 1주: 지난 시즌 임대 희망 거절 여부로 연속 시즌 수·임대 불만 갱신."""
    ge = _ge()
    p = ge.get_player() or {}
    last = year - 1
    if (p.get("loan_wish_denied_season") or 0) == last:
        streak = (p.get("loan_wish_denial_streak") or 0) + 1
    else:
        streak = 0
    kw = {"loan_wish_denial_streak": streak}
    if streak >= MY_LOAN_FRUSTRATION_STREAK:
        kw["loan_frustration_year"] = last
    if streak != (p.get("loan_wish_denial_streak") or 0) or "loan_frustration_year" in kw:
        ge.update_player(**kw)


def weekly_hook(year, week):
    """매주(소속팀 있음·복무 중 아님): 구단 임대 추진 판정, 임대 희망 처리, 시즌 끝 재판정."""
    ge = _ge()
    p = ge.get_player() or {}
    if not p.get("current_team_id") or _serving(p):
        return
    # 임대 희망 — 제출 다음 주에 결정, 승인 후 오퍼 도착 알림
    wish = _loads(p.get("loan_wish_json"))
    if wish:
        if wish.get("status") == "submitted" and _abs(year, week) > _abs(*wish.get("submit", [year, week])):
            _decide_wish(p, wish, year, week)
            p = ge.get_player() or {}
        elif wish.get("status") == "approved":
            now = _abs(year, week)
            changed = False
            for o in wish.get("offers") or []:
                if not o.get("announced") and o.get("arrive", 0) <= now:
                    o["announced"] = True
                    changed = True
                    ge.add_log(f"📨 {o['team_name']}({o['league_name']})가 임대를 제안했습니다 — "
                               f"기간 {halves_text(o['halves'])}", "event", year, week)
            if changed:
                ge.update_player(loan_wish_json=_dumps(wish))
                p = ge.get_player() or {}
    _club_push_check(p, year, week)
    # 시즌 끝(리그 종료 다음 주) 재판정 — 정리 목적 거부 보정은 계획이 바뀌었으면 해제
    if week == MY_LOAN_REJUDGE_WEEK and p.get("loan_refused_year"):
        p = ge.get_player() or {}
        if (p.get("current_team_id") or 0) != (p.get("loan_refused_tid") or 0):
            ge.update_player(loan_refused_year=0, loan_refused_tid=0)
        else:
            plan, _ = club_plan(p, year, week)
            if plan != "정리":
                ge.update_player(loan_refused_year=0, loan_refused_tid=0)


def status(p=None):
    """UI용 현재 임대 상태 요약."""
    ge = _ge()
    p = p or ge.get_player() or {}
    ok, why = can_submit_wish(p)
    return {
        "can_submit": ok, "block_reason": why,
        "wish": _loads(p.get("loan_wish_json")),
        "wish_offers": visible_wish_offers(p),
        "club_proposal": _loads(p.get("club_loan_proposal_json")),
        "pending": _loads(p.get("pending_loan_json")),
        "on_loan": bool(p.get("loan_from_team_id")) and not _serving(p),
        "serving": _serving(p),
    }