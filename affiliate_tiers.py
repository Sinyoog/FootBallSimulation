"""[2026-10-31 신설, 신민용 확정 — 유스팀 작업 A: 승강 규칙] 모구단 > 첫째 유스 > 둘째 유스 … 부수 순서.

규칙(설계 확정안 v3 A):
  · 순서: 모구단 > 첫째 유스(카스티야) > 둘째 유스(C) … — 항상 위 팀보다 낮은 부(숫자 큰 쪽). 예외는 그 나라
    최하위 리그 하나(거기서는 같이 있어도 된다 — 모구단까지 바닥에 내려와도 같다).
  · "위 팀(upper)": 첫째 유스의 위 팀은 모구단, 둘째 유스의 위 팀은 첫째 유스(같은 모구단 산하팀끼리 단계 순서).
    단계 순서는 ai_lifecycle._affiliate_ladder와 같은 기준 — 지금 부수 → 직전 오프시즌 단계 → 팀 id.
  · 승격: 그 시즌 승강이 끝난 뒤 위 팀이 있을 수 있는 가장 낮은 부(worst_tier — 위 팀이 강등 PO에 걸려 있으면
    한 단계 아래, 위 팀의 위 팀 사정까지 연쇄로 반영)보다 높은 자리로는 못 올라간다.
  · 강제 강등: 위 팀과 같은 부(또는 위 팀보다 위)가 되면 한 단계 강제 강등(최하위 리그면 그대로). 연쇄.
    강제 강등팀은 그 리그 꼴찌로 친다 — 강등 자리를 먼저 차지해 원래 강등권 중 순위 높은 팀이 잔류한다.
    강등 자리보다 많으면 강등·승격 수를 함께 늘린다(game_engine._process_promotion_relegation 경계 루프).
  · 승강 PO 뒤 충돌: PO 결과는 바꾸지 않는다. 충돌한 유스팀을 한 단계 내리고, 아래 리그에서 이번 시즌 아직
    이동하지 않았고 PO에 참가하지 않은 정상 승격 자격 팀 중 순위가 가장 높은 팀을 올리는 맞교환(resolve_after_po).
    후보가 없으면 그대로 두고 예외로 집계 — 다음 시즌 정규 처리에서 자격·정원을 다시 확인해 처리한다.
  · 시작 월드: 새 게임을 만들 때(선수 생성 전) 같은 규칙으로 맞교환해 바로잡는다(fix_start_tiers).

스위치: constants.AFFILIATE_LADDER_RULES(검증 중 비교용 — 채택 시 제거). False면 예전 동작 그대로.
난수를 쓰지 않는다(순서는 순위·팀 id로만 정한다)."""

STATS = {}   # 연도별 계측: {year: {"forced": n, "forced_extra": n, "forced_stuck": [...], "po_swaps": n, "po_stuck": [...]}}


def _stat(year):
    return STATS.setdefault(year, {"forced": 0, "forced_extra": 0, "forced_stuck": [],
                                   "promo_blocked": 0, "po_blocked": 0,
                                   "po_swaps": 0, "po_stuck": [], "start_swaps": 0})


def load_prev_levels(c):
    """직전 오프시즌에 저장된 산하팀 단계(ai_lifecycle._affiliate_ladder). 표가 없으면 빈 dict."""
    try:
        return {int(r[0]): int(r[1]) for r in c.execute("SELECT team_id, level FROM affiliate_ladder").fetchall()}
    except Exception:
        return {}


def build_upper_map(parent_of, tier_of, prev_level=None):
    """{산하팀: 위 팀}. 같은 모구단 산하팀끼리 (지금 부수, 직전 단계, 팀 id) 순으로 줄 세워
    첫째의 위 팀 = 모구단, 둘째의 위 팀 = 첫째 …"""
    prev_level = prev_level or {}
    by_parent = {}
    for ch, pa in parent_of.items():
        by_parent.setdefault(pa, []).append(ch)
    up = {}
    for pa, chs in by_parent.items():
        chs = sorted(chs, key=lambda t: (tier_of.get(t, 99), prev_level.get(t, 99), t))
        prev = pa
        for t in chs:
            up[t] = prev
            prev = t
    return up


def worst_tier(tid, tier_of, upper_of, may_drop, max_tier_of, _depth=0):
    """이번 시즌 이 팀이 최종적으로 있을 수 있는 가장 낮은 부(숫자 큰 쪽).
    may_drop: 강등 PO 등으로 한 단계 내려갈 수 있는 팀 집합. 위 팀이 내려가면 이 팀도 밀려 내려갈 수 있다(연쇄)."""
    t = tier_of.get(tid)
    if t is None:
        return 99
    w = t + (1 if tid in may_drop else 0)
    up = upper_of.get(tid)
    if up is not None and _depth < 12:
        w = max(w, worst_tier(up, tier_of, upper_of, may_drop, max_tier_of, _depth + 1) + 1)
    return min(w, max_tier_of(tid))


def can_promote(tid, target_tier, tier_of, upper_of, may_drop, max_tier_of):
    """산하팀이 target_tier로 올라가도 되는가(위 팀이 갈 수 있는 가장 낮은 부보다 위가 아니어야 한다).
    산하팀이 아니면 True."""
    up = upper_of.get(tid)
    if up is None:
        return True
    return worst_tier(up, tier_of, upper_of, may_drop, max_tier_of) < target_tier


def needs_force(tid, tier_of, upper_of, max_tier_of):
    """위 팀과 같은 부이거나 위 팀보다 위에 있고, 최하위 리그가 아니면 True."""
    up = upper_of.get(tid)
    if up is None or tid not in tier_of or up not in tier_of:
        return False
    t = tier_of[tid]
    return t <= tier_of[up] and t < max_tier_of(tid)


def violations(tier_of, upper_of, max_tier_of):
    return sorted((a for a in upper_of if needs_force(a, tier_of, upper_of, max_tier_of)),
                  key=lambda a: (tier_of.get(a, 99), a))


def _season_standings(c, league_id, season):
    """그 시즌 그 리그 순위(승점→득실→득점, 같으면 팀 id 순 — game_engine 순위 계산과 같은 기준)."""
    rows = c.execute(
        "SELECT home_team_id, away_team_id, home_score, away_score FROM match_results "
        "WHERE season=? AND league_id=? AND home_score>=0", (season, league_id)).fetchall()
    agg = {}
    for h, a, hs, as_ in rows:
        for tid, gf, ga in ((h, hs, as_), (a, as_, hs)):
            s = agg.setdefault(tid, [0, 0, 0])
            s[1] += gf - ga
            s[2] += gf
            if gf > ga:
                s[0] += 3
            elif gf == ga:
                s[0] += 1
    return [t for t, _ in sorted(sorted(agg.items()), key=lambda kv: (-kv[1][0], -kv[1][1], -kv[1][2]))]


def _team_maps(c):
    from military_service import drop_military_leagues
    lrows = c.execute("SELECT id, country_id, tier FROM leagues ORDER BY id").fetchall()
    try:
        lrows = drop_military_leagues(c, lrows, 0)
    except Exception:
        pass
    league_of_ct = {(r[1], r[2]): r[0] for r in lrows}
    max_tier_c = {}
    for (cid, t) in league_of_ct:
        if t > max_tier_c.get(cid, 0):
            max_tier_c[cid] = t
    trows = c.execute("SELECT id, league_id, current_tier, country_id, classification_status, parent_team_id, name "
                      "FROM teams").fetchall()
    tier_of, country_of, status_of, name_of, league_of, parent_of = {}, {}, {}, {}, {}, {}
    for r in trows:
        tier_of[r[0]] = r[2]
        league_of[r[0]] = r[1]
        country_of[r[0]] = r[3]
        status_of[r[0]] = r[4] or "NORMAL"
        name_of[r[0]] = r[6] or ""
        if (r[4] or "") == "AFFILIATE" and r[5]:
            parent_of[r[0]] = r[5]
    return league_of_ct, max_tier_c, tier_of, country_of, status_of, name_of, league_of, parent_of


def resolve_after_po(c, year):
    """승강 PO가 끝난 경계가 생길 때마다(promotion_playoff_engine → game_engine.sweep_all_affiliate_conflicts)
    남은 충돌을 맞교환으로 정리한다. PO가 아직 안 끝난 참가팀은 건드리지 않는다(그 PO가 끝난 뒤 다시 돈다).
    반환: [(team_id, 새 league_id, 새 tier), ...] — 내 팀 리그 갱신 등은 호출부가 한다."""
    st = _stat(year)
    league_of_ct, max_tier_c, tier_of, country_of, status_of, name_of, league_of, parent_of = _team_maps(c)
    if not parent_of:
        return []
    upper_of = build_upper_map(parent_of, tier_of, load_prev_levels(c))

    def max_tier_of(t):
        return max_tier_c.get(country_of.get(t), tier_of.get(t, 1))

    # 올해 PO 참가팀(진행 중 + 끝남) — 끝나지 않은 참가팀은 아직 결과가 없어 건드리지 않고,
    # 참가팀 전체는 맞교환 승격 후보에서 뺀다(PO에서 진 팀을 맞교환으로 올리면 PO 결과를 뒤집는 셈).
    pending, participants = set(), set()
    try:
        for r in c.execute("SELECT team_id FROM po_pending_slots WHERE year=?", (year,)).fetchall():
            pending.add(r[0])
            participants.add(r[0])
        for r in c.execute(
                "SELECT m.home_team_id, m.away_team_id, t.status FROM po_matches m "
                "JOIN po_tournaments t ON t.id=m.tournament_id WHERE t.year=?", (year,)).fetchall():
            for tid in (r[0], r[1]):
                if tid:
                    participants.add(tid)
                    if (r[2] or "") != "done":
                        pending.add(tid)
    except Exception:
        pass
    moved = {r[0] for r in c.execute("SELECT team_id FROM promotion_log WHERE year=? AND team_id IS NOT NULL",
                                     (year,)).fetchall()}
    sr = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    season = sr[0] if sr else 1
    stuck = set()
    moves = []
    standings_cache = {}
    for _pass in range(20):
        viol = [a for a in violations(tier_of, upper_of, max_tier_of) if a not in pending and a not in stuck]
        if not viol:
            break
        changed = False
        for a in viol:
            if not needs_force(a, tier_of, upper_of, max_tier_of):
                continue
            t = tier_of[a]
            cid = country_of.get(a)
            up_lid, lo_lid = league_of_ct.get((cid, t)), league_of_ct.get((cid, t + 1))
            if up_lid is None or lo_lid is None:
                stuck.add(a)
                st["po_stuck"].append((a, "리그 없음"))
                continue
            if lo_lid not in standings_cache:
                standings_cache[lo_lid] = _season_standings(c, lo_lid, season)
            cand = None
            for b in standings_cache[lo_lid]:
                if (b in moved or b in participants or b == a or league_of.get(b) != lo_lid
                        or status_of.get(b) not in ("NORMAL", "AFFILIATE")):
                    continue
                if status_of.get(b) == "AFFILIATE" and (
                        b not in upper_of
                        or not can_promote(b, t, tier_of, upper_of, pending, max_tier_of)):
                    continue
                cand = b
                break
            if cand is None:
                stuck.add(a)
                st["po_stuck"].append((a, "맞교환 후보 없음"))
                continue
            c.execute("UPDATE teams SET league_id=?, current_tier=? WHERE id=?", (lo_lid, t + 1, a))
            c.execute("UPDATE teams SET league_id=?, current_tier=? WHERE id=?", (up_lid, t, cand))
            lname = {lid: n for lid, n in c.execute(
                "SELECT id, name FROM leagues WHERE id IN (?,?)", (up_lid, lo_lid)).fetchall()}
            c.executemany(
                """INSERT INTO promotion_log(year,team_name,from_tier,to_tier,league_name,
                                              from_league_id,to_league_id,team_id) VALUES(?,?,?,?,?,?,?,?)""",
                [(year, name_of.get(a, ""), t, t + 1, lname.get(up_lid, ""), up_lid, lo_lid, a),
                 (year, name_of.get(cand, ""), t + 1, t, lname.get(lo_lid, ""), lo_lid, up_lid, cand)])
            tier_of[a], league_of[a] = t + 1, lo_lid
            tier_of[cand], league_of[cand] = t, up_lid
            moved.update((a, cand))
            moves += [(a, lo_lid, t + 1), (cand, up_lid, t)]
            st["po_swaps"] += 1
            changed = True
        if not changed:
            break
    return moves


def fix_start_tiers(c):
    """새 게임 월드(선수 생성 전)의 순서 위반을 바로잡는다 — 위반 유스팀을 한 단계 내리고, 아래 리그의 일반 팀
    중 리그 데이터 순서(팀 id)가 가장 앞인 팀을 올리는 맞교환. 리그 팀 수는 그대로. 반환: 맞교환 수."""
    league_of_ct, max_tier_c, tier_of, country_of, status_of, name_of, league_of, parent_of = _team_maps(c)
    if not parent_of:
        return 0

    def max_tier_of(t):
        return max_tier_c.get(country_of.get(t), tier_of.get(t, 1))

    n = 0
    used = set()
    for _pass in range(20):
        upper_of = build_upper_map(parent_of, tier_of)
        viol = violations(tier_of, upper_of, max_tier_of)
        if not viol:
            break
        changed = False
        for a in viol:
            if not needs_force(a, tier_of, upper_of, max_tier_of):
                continue
            t = tier_of[a]
            cid = country_of.get(a)
            up_lid, lo_lid = league_of_ct.get((cid, t)), league_of_ct.get((cid, t + 1))
            if up_lid is None or lo_lid is None:
                continue
            cands = sorted(b for b, lid in league_of.items()
                           if lid == lo_lid and status_of.get(b) == "NORMAL" and b not in used)
            if not cands:
                continue
            b = cands[0]
            c.execute("UPDATE teams SET league_id=?, current_tier=? WHERE id=?", (lo_lid, t + 1, a))
            c.execute("UPDATE teams SET league_id=?, current_tier=? WHERE id=?", (up_lid, t, b))
            tier_of[a], league_of[a] = t + 1, lo_lid
            tier_of[b], league_of[b] = t, up_lid
            used.update((a, b))
            n += 1
            changed = True
        if not changed:
            break
    return n