"""[2026-11 구단 목표 2단계 — 신민용 확정 '클럽 목표 산정 명세'] 시즌 시작 구단 목표.

목표는 시즌이 시작될 때 딱 한 번 정해지고 시즌 중엔 바뀌지 않는다.
  · 리그 목표: 새 게임(_snapshot_expected_strength(_start_year) 직후)과 연도 전환
    (_snapshot_expected_strength(new_year) 직후) 두 곳에서만 plan_season_goals를 부른다.
    세이브 불러오기·시즌 중에는 절대 부르지 않는다.
  · 대륙대회 목표: 7주차 추첨(start_all_continental_competitions)이 참가팀을 확정한 직후
    plan_cont_goals를 부른다.
저장은 "없을 때만 넣기"(INSERT OR IGNORE) — 같은 해에 두 번 불려도 이미 있는 목표와
teams.club_ambition은 바뀌지 않는다. 그해 'plan' 목표가 없는 팀(기존 세이브의 진행 중인
시즌)은 레거시 시즌으로 끝까지 간다(ai_lifecycle._manager_turnover_run 주석).

전력 기준은 teams.exp_strength(시즌 시작 시점 주전 11명 실효 OVR, 명성 보너스 없음),
순위는 내림차순·동률이면 team_id 오름차순. 난수를 쓰지 않는다.

[이 단계의 범위] 목표 산정·생성·저장만. 감독 평가(경질) 공식은 다음 단계에서 바꾸며,
그 전까지 기존 공식은 기존 방식으로 산정한 목표(teams.legacy_ambition)를 그대로 쓴다.
"""
import math

PLAN_BASIS = "plan"
LEGACY_BASIS = "legacy"
CONT_KINDS = ("cl", "el", "ecl")

# 리그 종류별 목표 사다리(아래 → 위). 명문 상향은 한 칸, 맨 위를 넘지 않는다.
LADDER = {
    "top":    ("강등 회피", "중위권 안정", "상위권 도전", "우승 도전"),
    "mid":    ("잔류", "중위권 안정", "상위권 도전", "승격 도전"),
    "bottom": ("하위권 탈출", "중위권 안정", "상위권 도전", "승격 도전"),
    "single": ("하위권 탈출", "중위권 안정", "상위권 도전", "우승 도전"),
}
GOAL_LABELS = ("우승 도전", "승격 도전", "상위권 도전", "중위권 안정", "강등 회피", "잔류", "하위권 탈출")

# 기존 소비처(기존 4단계 이름만 아는 곳 — 협상 보정 등)에 새 이름을 넘길 때의 대응.
LEGACY_EQUIV = {"승격 도전": "우승 도전", "잔류": "강등 회피", "하위권 탈출": "강등 회피"}


def legacy_equiv(label):
    return LEGACY_EQUIV.get(label, label)


# 대륙대회 목표 — 대회 참가팀 안의 전력 순위 비율(32·36팀 같은 규칙).
CONT_GOAL_LEVEL = {"4강 진출": 4, "8강 진출": 3, "16강 진출": 2, "리그 스테이지 통과": 1}


# ── 순수 함수(DB 없이 테스트) ─────────────────────────────────────
def promotion_policy(n):
    """game_engine._get_promotion_policy와 같은 표 → (자동, PO 자리 수)."""
    from game_engine import _get_promotion_policy
    pol = _get_promotion_policy(int(n))
    return int(pol["auto"]), (1 if pol["po"] else 0)


def po_bracket(n):
    from game_engine import _get_po_bracket_size
    return int(_get_po_bracket_size(int(n)))


def league_bands(kind, n, upper_n=None):
    """구간 크기. z = 승강 정책표를 자기 리그 팀 수로 조회한 (자동 + PO) — 실제 강등 여부와 무관하게
    모든 리그에 같은 방식(최하위·군 리그도 '아래 리그가 있었다면 강등권이었을 크기'가 하위권)."""
    a, po = promotion_policy(n)
    ctx = {"n": int(n), "z": a + po, "U": min(3, max(1, int(n) // 6)), "up": 0, "po_cut": 0}
    if kind in ("mid", "bottom") and upper_n:
        ua, upo = promotion_policy(upper_n)
        ctx["up"] = ua + (1 if upo else 0)
        ctx["po_cut"] = ua + (po_bracket(n) if upo else 0)
    return ctx


def base_goal(kind, ctx, r):
    """전력 순위 r의 기본 구간. 위 구간부터 판정(작은 리그에서 겹치면 위 구간 우선)."""
    n, z = ctx["n"], ctx["z"]
    lad = LADDER[kind]
    if kind in ("top", "single"):
        if r <= ctx["U"]:
            return lad[3]
        if r <= 4:
            return lad[2]
    else:
        if ctx["up"] and r <= ctx["up"]:
            return lad[3]
        if ctx["po_cut"] and r <= ctx["po_cut"]:
            return lad[2]
    if r > n - z:
        return lad[0]
    return lad[1]


def adjust_goal(kind, goal, prestige, promo_ok, prestige_min=2):
    """보정 순서(명세 5절): ① 명문 한 칸 상향(맨 위 초과 없음) → ② 승격 자격이 없으면 승격 도전 → 상위권 도전.
    반환 (최종 목표, 표시용 플래그 문자열)."""
    lad = LADDER[kind]
    flags = []
    if (prestige or 0) >= prestige_min:
        i = lad.index(goal)
        if i < 3:
            goal = lad[i + 1]
            flags.append("prestige")
    if goal == "승격 도전" and not promo_ok:
        goal = "상위권 도전"
        flags.append("promo_blocked")
    return goal, ",".join(flags)


def target_rank(kind, ctx, goal):
    n, z = ctx["n"], ctx["z"]
    if goal == "우승 도전":
        return 1
    if goal == "승격 도전":
        return max(1, ctx["up"])
    if goal == "상위권 도전":
        return 4 if kind in ("top", "single") else max(1, ctx["po_cut"])
    if goal == "중위권 안정":
        return int(math.ceil(2 * n / 3))
    return n - z                      # 강등 회피·잔류·하위권 탈출


def cont_goal(rank, n):
    if rank <= n // 8:
        return "4강 진출"
    if rank <= n // 4:
        return "8강 진출"
    if rank <= n // 2:
        return "16강 진출"
    if rank <= (2 * n) // 3:
        return "리그 스테이지 통과"
    return ""                         # 목표 없음(미달 판정 대상 아님)


# ── 스키마 ───────────────────────────────────────────────────────
def club_goal_migrations():
    """database.init_db 마이그레이션 루프에 붙는다(멱등 — 중복 컬럼 오류는 루프가 무시)."""
    return [
        # 기존 감독 평가 공식이 쓰던 '지난 시즌 결과로 정한 목표'. 평가 공식 교체(다음 단계) 전까지
        # 기존 공식의 입력을 그대로 지키기 위한 숨은 칸 — 화면에는 안 나온다.
        "ALTER TABLE teams ADD COLUMN legacy_ambition TEXT DEFAULT ''",
        # plan 목표의 산정 근거(리그 종류·팀 수·전력 순위·목표 순위·보정 전 목표·보정 플래그).
        "ALTER TABLE team_season_ambition ADD COLUMN plan_kind TEXT DEFAULT ''",
        "ALTER TABLE team_season_ambition ADD COLUMN plan_n INTEGER DEFAULT 0",
        "ALTER TABLE team_season_ambition ADD COLUMN plan_srank INTEGER DEFAULT 0",
        "ALTER TABLE team_season_ambition ADD COLUMN plan_target INTEGER DEFAULT 0",
        "ALTER TABLE team_season_ambition ADD COLUMN plan_raw TEXT DEFAULT ''",
        "ALTER TABLE team_season_ambition ADD COLUMN plan_flags TEXT DEFAULT ''",
        """CREATE TABLE IF NOT EXISTS team_season_cont_goal(
            year INTEGER, team_id INTEGER, comp TEXT, tournament_id INTEGER,
            goal TEXT DEFAULT '', srank INTEGER DEFAULT 0, n INTEGER DEFAULT 0,
            PRIMARY KEY(year, team_id, comp))""",
    ]


# ── DB ───────────────────────────────────────────────────────────
def has_plan_year(conn, year):
    try:
        return conn.execute("SELECT 1 FROM team_season_ambition WHERE year=? AND basis=? LIMIT 1",
                            (int(year), PLAN_BASIS)).fetchone() is not None
    except Exception:
        return False


def current_goal(conn, team_id, year):
    """그해 plan 목표(없으면 None)."""
    try:
        r = conn.execute("SELECT ambition FROM team_season_ambition WHERE year=? AND team_id=? AND basis=?",
                         (int(year), int(team_id), PLAN_BASIS)).fetchone()
    except Exception:
        return None
    return (r[0] or None) if r else None


def _league_kinds(conn):
    """{league_id: (종류, 위 리그 id)} — game_engine._process_promotion_relegation과 같은 구조 정보
    (_league_map[(국가, 부)], 군 리그 제외)로 판정. 군 리그는 승강 없는 사다리(single)."""
    from military_service import get_military_league_ids
    mil = set(get_military_league_ids(conn) or ())
    rows = conn.execute("SELECT id, country_id, tier FROM leagues ORDER BY id").fetchall()
    lmap = {(r[1], r[2]): r[0] for r in rows if r[0] not in mil}
    out = {}
    for lid, cid, tier in rows:
        if lid in mil:
            out[lid] = ("single", None)
            continue
        lower = lmap.get((cid, tier + 1))
        upper = lmap.get((cid, tier - 1))
        if tier == 1:
            out[lid] = ("top" if lower else "single", None)
        else:
            out[lid] = ("mid" if lower else "bottom", upper)
    return out


def _promo_ok_map(conn, team_rows):
    """game_engine._process_promotion_relegation의 _promo_ok와 같은 규칙을 시즌 시작 시점 부수로 적용
    (이번 시즌 강등 PO 결과는 아직 모르므로 may_drop은 비운다). {team_id: 한 부 위로 갈 수 있는가}."""
    import constants
    aff_on = bool(getattr(constants, "AFFILIATE_LADDER_RULES", False)) and bool(
        getattr(constants, "AFFILIATE_PROMOTION_RESTRICTION", False))
    tier_of = {r["id"]: (r["current_tier"] or r["ltier"]) for r in team_rows}
    cls_of = {r["id"]: (r["classification_status"] or "NORMAL") for r in team_rows}
    country_of = {r["id"]: r["country_id"] for r in team_rows}
    max_tier = {}
    for r in team_rows:
        max_tier[r["country_id"]] = max(max_tier.get(r["country_id"], 0), r["ltier"] or 0)
    upper_of = {}
    aff = None
    if aff_on:
        import affiliate_tiers as aff
        parent_of = {r["id"]: r["parent_team_id"] for r in team_rows
                     if r["parent_team_id"] and cls_of[r["id"]] == "AFFILIATE"}
        upper_of = aff.build_upper_map(parent_of, tier_of, aff.load_prev_levels(conn))
    out = {}
    for r in team_rows:
        tid = r["id"]
        st = cls_of[tid]
        if st == "NORMAL":
            out[tid] = True
        elif not aff_on or st != "AFFILIATE" or tid not in upper_of:
            out[tid] = False
        else:
            out[tid] = bool(aff.can_promote(tid, (tier_of[tid] or 1) - 1, tier_of, upper_of, set(),
                                            lambda t: max_tier.get(country_of.get(t), tier_of.get(t, 1))))
    return out


def plan_season_goals(conn, year):
    """그해 리그 목표를 정해 team_season_ambition(basis='plan')에 넣고, 새로 넣은 팀만 teams.club_ambition을
    같은 값으로 쓴다. 이미 그해 행이 있는 팀은 건드리지 않는다. 스냅샷(exp_strength_year==year)이 빠진 팀이
    있는 리그는 통째로 건너뛴다(순위가 틀어지므로) — 그 팀들은 레거시 경로로 평가된다. 반환: 통계 dict."""
    import constants
    from data.prestige_clubs import prestige_level
    year = int(year)
    pmin = int(getattr(constants, "BIG_CLUB_PRESTIGE_THRESHOLD", 2))
    rows = conn.execute(
        """SELECT t.id, t.name, t.league_id, t.country_id AS tcid, t.current_tier, t.classification_status,
                  t.parent_team_id, t.exp_strength, t.exp_strength_year,
                  l.tier AS ltier, l.country_id, cn.name AS country
           FROM teams t JOIN leagues l ON l.id = t.league_id
           LEFT JOIN countries cn ON cn.id = t.country_id
           WHERE t.league_id IS NOT NULL ORDER BY t.id""").fetchall()
    existing = {r[0] for r in conn.execute("SELECT team_id FROM team_season_ambition WHERE year=?", (year,))}
    kinds = _league_kinds(conn)
    by_l = {}
    for r in rows:
        by_l.setdefault(r["league_id"], []).append(r)
    size = {lid: len(v) for lid, v in by_l.items()}
    promo_ok = _promo_ok_map(conn, rows)
    stats = {"inserted": 0, "existing": 0, "skipped_no_strength": 0, "leagues": 0, "by_goal": {}}
    ins, amb = [], []
    for lid in sorted(by_l):
        trs = by_l[lid]
        if any(r["exp_strength_year"] != year or r["exp_strength"] is None for r in trs):
            stats["skipped_no_strength"] += len(trs)
            continue
        kind, upper = kinds.get(lid, ("single", None))
        ctx = league_bands(kind, size[lid], size.get(upper) if upper else None)
        order = sorted(trs, key=lambda r: (-float(r["exp_strength"]), r["id"]))
        stats["leagues"] += 1
        for i, r in enumerate(order):
            tid = r["id"]
            if tid in existing:
                stats["existing"] += 1
                continue
            raw = base_goal(kind, ctx, i + 1)
            pres = (prestige_level(r["country"], r["name"]) or 0) if r["country"] else 0
            goal, flags = adjust_goal(kind, raw, pres, promo_ok.get(tid, True), pmin)
            ins.append((year, tid, goal, PLAN_BASIS, kind, ctx["n"], i + 1, target_rank(kind, ctx, goal), raw, flags))
            amb.append((goal, tid))
            stats["by_goal"][goal] = stats["by_goal"].get(goal, 0) + 1
    if ins:
        conn.executemany(
            "INSERT OR IGNORE INTO team_season_ambition(year, team_id, ambition, basis, plan_kind, plan_n, "
            "plan_srank, plan_target, plan_raw, plan_flags) VALUES (?,?,?,?,?,?,?,?,?,?)", ins)
        conn.executemany("UPDATE teams SET club_ambition=? WHERE id=?", amb)
        stats["inserted"] = len(ins)
    return stats


def plan_cont_goals(conn, year):
    """그해 대륙대회(cl/el/ecl) 참가팀별 목표를 team_season_cont_goal에 '없을 때만 넣기'. 그해 리그 plan이
    없으면(레거시 시즌) 만들지 않는다. 목표 없음도 ''로 한 줄 남긴다(참가 기록 겸)."""
    year = int(year)
    if not has_plan_year(conn, year):
        return {"inserted": 0, "legacy": True}
    strength = {r[0]: (r[1] if r[2] == year else None) for r in conn.execute(
        "SELECT id, exp_strength, exp_strength_year FROM teams")}
    ins = []
    for kind in CONT_KINDS:
        try:
            tours = conn.execute(f"SELECT id FROM {kind}_tournaments WHERE year=? ORDER BY id", (year,)).fetchall()
        except Exception:
            continue
        for (tour_id,) in tours:
            ents = sorted({r[0] for r in conn.execute(f"SELECT team_id FROM {kind}_entries WHERE tournament_id=?",
                                                      (tour_id,))})
            n = len(ents)
            order = sorted(ents, key=lambda t: (-(strength.get(t) or 0.0), t))
            for i, t in enumerate(order):
                ins.append((year, t, kind, tour_id, cont_goal(i + 1, n), i + 1, n))
    if ins:
        conn.executemany("INSERT OR IGNORE INTO team_season_cont_goal(year, team_id, comp, tournament_id, goal, "
                         "srank, n) VALUES (?,?,?,?,?,?,?)", ins)
    return {"inserted": len(ins), "legacy": False}


def cont_goal_texts(conn, team_id, years):
    """{연도: 표시 문구} — 그 해 클럽 대항전 목표. 예: '16강 진출 (유럽 챔피언스리그)', 참가했는데 목표 구간 밖이면
    '목표 없음 (UEFA 유로파리그)'. 기록이 없는 해(미참가·추첨 전·목표 기능 이전 시즌)는 넣지 않는다 — 호출부가 '--'."""
    ys = sorted({int(y) for y in years})
    if not ys:
        return {}
    out = {}
    try:
        rows = conn.execute("SELECT year, comp, tournament_id, goal FROM team_season_cont_goal "
                            "WHERE team_id=? AND year BETWEEN ? AND ?", (int(team_id), ys[0], ys[-1])).fetchall()
    except Exception:
        return {}
    for y, comp, tour_id, goal in rows:
        name = ""
        if comp in CONT_KINDS:
            try:
                r = conn.execute(f"SELECT name FROM {comp}_tournaments WHERE id=?", (tour_id,)).fetchone()
                name = (r[0] if r else "") or ""
            except Exception:
                name = ""
        txt = goal or "목표 없음"
        out[int(y)] = f"{txt} ({name})" if name else txt
    return out


def cont_goal_text(conn, team_id, year):
    return cont_goal_texts(conn, team_id, [year]).get(int(year), "--")