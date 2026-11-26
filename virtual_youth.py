"""[2026-11-22 클럽 선수단 운영 개편 1단계] 가상 유스 + 공급원 공통 판정 — 이 파일 한 곳에서만 판정한다.

가상 유스 = 유스 구성 계획 대상 클럽(5대 리그 1~4부 · A등급 1~2부 · B등급 이하 1부) 중 실제 산하팀이 없는 클럽에
월드 생성 시점에 한 번 붙이는 "리그 밖 팀 행"(teams.classification_status='VIRTUAL_YOUTH', league_id=NULL,
parent_team_id=모구단, country_id=모구단 나라). 선수는 일반 ai_players 행이며 1군 인원·장부·리그·시장·경기·연봉에서
빠진다(경로별 포함·제외는 ④-a 기준 문서). 공급원은 구단당 하나 — 실제 산하팀이 있으면 산하팀, 없으면 가상 유스.

다른 모듈은 SQL 조건을 직접 쓰지 말고 아래 함수만 쓴다:
  · vids(c)                    가상 팀 id 집합(없으면 빈 집합 — 스위치 OFF 월드는 항상 빈 집합)
  · and_not_vp(c, col)         " AND {col} NOT IN (가상 팀)" — 가상 팀이 없으면 "" (SQL이 원본과 글자까지 같음)
  · where_not_vp(c, col)       " WHERE {col} NOT IN (가상 팀)" — 가상 팀이 없으면 ""
  · and_not_vt(c, alias)       teams 조회용 " AND {alias}classification_status != 'VIRTUAL_YOUTH'" — 없으면 ""
  · where_not_vt(c, alias)     teams 조회용 WHERE 버전
  · is_virtual(c, tid) / drop_rows(c, rows, key)  파이썬 쪽 거르기
"""
import collections
import random

import constants as _C

STATUS = "VIRTUAL_YOUTH"
K = 4                         # 가상 유스 풀 최대 인원(정기 입단은 부족분만)
MAX_AGE = 23                  # 승격 후보 최대 나이 — 24세 이상 미승격자는 정리
PROMO_LIMIT = 2               # 모구단별 연간 승격 한도(여름+겨울 합산, 공급원 종류 무관)
PROMO_TYPES = ("유스 승격(공급원)", "유스 승격(보충)")   # 한도에 세는 이적 기록 종류
BIG5 = ("잉글랜드", "스페인", "독일", "프랑스", "이탈리아")
NAME_SUFFIX = " 유스"
STATS = collections.Counter()  # 계측(측정 도구가 읽는다)
SAL_FLOOR_LOG = []   # [2026-11-22 S6] 승격 연봉 하한이 적용된 건(리그·OVR·계약) — ai_lifecycle._promotion_salary_floor

_SQL_VT = f"(SELECT id FROM teams WHERE classification_status='{STATUS}')"


def stage1():
    """1단계 묶음 스위치 — 공통 수요 장부 full 모드에서만."""
    return bool(getattr(_C, "CLUB_SUPPLY_V2", False)) and getattr(_C, "CLUB_LEDGER_MODE", "off") == "full"


def standalone():
    """[2026-11-26 VY_STANDALONE] C2 없이(기본 게임) 가상 유스를 쓰는 모드 — C2(stage1)가 켜져 있으면 기존 1단계 규칙이
    그대로라 이 모드는 꺼진 것으로 본다. stage1()에 묶인 C2 전용 동작(26명 상한·실제 유스팀 26명 맞추기 등)은 켜지 않는다."""
    return bool(getattr(_C, "VY_STANDALONE", False)) and not stage1()


def active():
    """가상 유스 풀 관리(월드 생성·입단·24세 정리)를 하는가 — 1단계(C2) 또는 단독 모드."""
    return stage1() or standalone()


def sa_limit(cname, grade):
    """[2026-11-26 신민용 확정] 단독 모드 유스팀 보유 범위 — S(SS 포함) 1~4부 · A 1~3부 · B 1~2부 · C 이하 1부."""
    g = (grade or "").upper()
    return 4 if g in ("SS", "S") else 3 if g == "A" else 2 if g == "B" else 1


def _cur(c):
    return c.cursor() if hasattr(c, "cursor") and not hasattr(c, "fetchone") else c


def vids(c):
    """가상 팀 id 집합. 행이 없으면 빈 frozenset."""
    try:
        return frozenset(r[0] for r in _cur(c).execute(
            "SELECT id FROM teams WHERE classification_status=?", (STATUS,)).fetchall())
    except Exception:
        return frozenset()


_FAST = {"key": None, "ids": frozenset()}


def vids_fast(c=None):
    """vids의 캐시판 — 팀 표의 (최대 id, 행 수)가 같으면 다시 읽지 않는다(신인 1명마다 부르는 경로용)."""
    if c is None:
        from database import get_conn
        c = get_conn()
    cur = _cur(c)
    try:
        key = tuple(cur.execute("SELECT MAX(id), COUNT(*) FROM teams").fetchone())
    except Exception:
        return frozenset()
    if key != _FAST["key"]:
        _FAST["key"], _FAST["ids"] = key, vids(cur)
    return _FAST["ids"]


def any_virtual(c):
    try:
        return _cur(c).execute("SELECT 1 FROM teams WHERE classification_status=? LIMIT 1", (STATUS,)).fetchone() is not None
    except Exception:
        return False


def and_not_vp(c, col):
    return f" AND {col} NOT IN {_SQL_VT}" if any_virtual(c) else ""


def where_not_vp(c, col):
    return f" WHERE {col} NOT IN {_SQL_VT}" if any_virtual(c) else ""


def and_not_vt(c, alias=""):
    return f" AND {alias}classification_status != '{STATUS}'" if any_virtual(c) else ""


def where_not_vt(c, alias=""):
    return f" WHERE {alias}classification_status != '{STATUS}'" if any_virtual(c) else ""


def team_cap_union_sql(c):
    """성장 상한(team_cap) 조회에 가상 팀 행을 붙이는 UNION — 열 순서 (tid, tier, cname, continent)."""
    if not any_virtual(c):
        return ""
    return (f" UNION ALL SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname, cn.continent AS continent "
            f"FROM teams t JOIN countries cn ON cn.id = t.country_id WHERE t.classification_status='{STATUS}'")


def tier_sql(alias_t="t", alias_l="l"):
    """국대 후보 부수: 리그 부수, 리그가 없는 가상 유스 팀이면 그 팀 부수(가상 리그 수준). 가상 팀이 없으면 l.tier 그대로."""
    return f"COALESCE({alias_l}.tier, CASE WHEN {alias_t}.classification_status='{STATUS}' THEN {alias_t}.current_tier END)"


_NT_JOIN = "JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id"
_NT_JOIN_V = "LEFT JOIN leagues l ON t.league_id=l.id JOIN countries cn ON cn.id = COALESCE(l.country_id, t.country_id)"


def nt_join(c=None):
    """국가대표 후보·명단 조회의 팀→리그→나라 조인. 가상 팀이 있으면 리그 없는 가상 유스 팀도 남기는 LEFT 조인
    (나라는 팀의 나라, 부수는 teams.current_tier = 가상 리그 수준). 가상 팀이 없으면 원래 글자 그대로."""
    if c is None:
        from database import get_conn
        c = get_conn()
    return _NT_JOIN_V if any_virtual(c) else _NT_JOIN


def is_virtual(c, tid, _v=None):
    return tid in (_v if _v is not None else vids(c))


def mil_virtual(c, tid):
    """[병역] tid가 가상 유스 팀이면 True(가상 팀이 없으면 항상 False — OFF 동일).
    입대: 계약·연봉 0을 유지한 채 복귀처 = 그 가상 팀(임대 형식), 빈자리 복제 없음(풀은 intake가 채운다).
    제대: 나이와 관계없이 그 가상 팀으로 복귀(연봉·계약 0) — 같은 오프시즌 뒤 24세 정리(cleanup)가
    24세 이상을 아카이브/삭제한다(기준 문서 3장: 제대 때 23세 이하면 풀 복귀, 24세 이상이면 24세 정리 규칙)."""
    return bool(tid) and tid in vids_fast(c)


def drop_rows(c, rows, key="team_id"):
    """rows(sqlite Row/dict/tuple 인덱스)에서 가상 팀 소속 행을 뺀다. 가상 팀이 없으면 그대로 반환."""
    v = vids(c)
    if not v:
        return rows
    if isinstance(key, int):
        return [r for r in rows if r[key] not in v]
    return [r for r in rows if r[key] not in v]


def parent_map(c):
    """{가상 팀 id: 모구단 id}"""
    try:
        return {int(a): int(b) for a, b in _cur(c).execute(
            "SELECT id, parent_team_id FROM teams WHERE classification_status=?", (STATUS,)).fetchall()}
    except Exception:
        return {}


def promo_counts(c, year):
    """{모구단 id: 그 해 공급원 승격 수} — 이적 기록에서 센다(여름·겨울 합산, 세이브에 남아 창구를 넘어 유지)."""
    out = collections.Counter()
    ph = ",".join("?" * len(PROMO_TYPES))
    try:
        for t, n in _cur(c).execute(
                f"SELECT to_team_id, COUNT(*) FROM ai_transfer_log WHERE year=? AND transfer_type IN ({ph}) "
                f"GROUP BY to_team_id", (year, *PROMO_TYPES)).fetchall():
            out[t] = n
    except Exception:
        pass
    return out


# ── 월드 생성 ────────────────────────────────────────────────────────────
def compute_targets(c):
    """유스 구성 계획 대상(5대 리그 1~4부 · A등급 1~2부 · B등급 이하 1부) 중 실제 산하팀이 없는 클럽.
    월드 생성 시점 기준. 반환 [(모구단 id, 이름, 포메이션, 부수, 나라 id, 나라, 대륙)]."""
    from constants import get_country_league_grade
    import military_service as _MS
    cur = _cur(c)
    mil = set(_MS.get_military_team_ids(cur.connection if hasattr(cur, "connection") else cur))
    has_aff = {r[0] for r in cur.execute(
        "SELECT DISTINCT parent_team_id FROM teams WHERE classification_status='AFFILIATE' "
        "AND parent_team_id IS NOT NULL").fetchall()}
    out = []
    gc = {}
    for tid, name, f, tier, cid, cname, cont, status in cur.execute(
            "SELECT t.id, t.name, t.formation, l.tier, cn.id, cn.name, cn.continent, t.classification_status "
            "FROM teams t JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id "
            "ORDER BY t.id").fetchall():
        # [2026-11-26 신민용 확정] 단독 모드에서는 B팀(산하팀)이 있는 클럽도 U-21을 둔다 — U-21 → B팀 → 1군 사다리.
        if status in ("AFFILIATE", STATUS) or tid in mil or (tid in has_aff and not standalone()):
            continue
        g = gc.get(cname)
        if g is None:
            g = gc[cname] = get_country_league_grade(cname)
        lim = sa_limit(cname, g) if standalone() else (4 if cname in BIG5 else (2 if g == "A" else 1))
        if (tier or 1) <= lim:
            out.append((tid, name, f, tier or 1, cid, cname, cont))
    return out


def drop_world(c):
    """이전 판의 가상 팀·선수를 지운다(새 게임). 반환 지운 팀 수."""
    cur = _cur(c)
    v = sorted(vids(cur))
    if not v:
        return 0
    ph = ",".join("?" * len(v))
    cur.execute(f"DELETE FROM ai_players WHERE team_id IN ({ph})", v)
    cur.execute(f"DELETE FROM teams WHERE id IN ({ph})", v)
    return len(v)


def _vtier_map(cur):
    return {cid: mx for cid, mx in cur.execute("SELECT country_id, MAX(tier) FROM leagues GROUP BY country_id").fetchall()}


def build_world(c, year):
    """새 월드: 대상 클럽마다 가상 팀 행 1개 + 선수 K명(필드만, 모구단 나라 국적, 계약·연봉 0). 반환 (팀 수, 선수 수)."""
    if not active():
        return 0, 0
    cur = _cur(c)
    drop_world(cur)
    targets = compute_targets(cur)
    vt_max = _vtier_map(cur)
    made = []
    for pid, name, f, tier, cid, cname, cont in targets:
        vtier = min(tier + 1, vt_max.get(cid) or tier)
        cur.execute("INSERT INTO teams(league_id, country_id, name, formation, current_tier, classification_status, "
                    "parent_team_id) VALUES(NULL,?,?,?,?,?,?)", (cid, name + NAME_SUFFIX, f, vtier, STATUS, pid))
        made.append((cur.lastrowid, pid, name, f, vtier, cname, cont))
    n = 0
    ctx = _gen_ctx(cur)
    if standalone():
        # [2026-11-26 VY_STANDALONE] 클럽마다 VY_SA_POOL명(생성 나이 VY_SA_AGE) — 첫 오프시즌부터 데뷔 후보(lty = 시작 연도 − 1)
        for vt, pid, name, f, vtier, cname, cont in made:
            n += _sa_fill(cur, ctx, vt, name, f, vtier, cname, cont, year, [], lty=year - 1, mode="world")
        if v3():
            _v3_project(cur, year, "world")
        STATS["world_teams"] = len(made)
        STATS["world_players"] = n
        return len(made), n
    for vt, pid, name, f, vtier, cname, cont in made:
        # 월드 생성 선수는 첫 오프시즌부터 승격 후보(last_transfer_year = 시작 연도 − 1). 정기 입단 선수는 그 해 창구 제외.
        n += _fill_pool(cur, ctx, vt, pid, name, f, vtier, cname, cont, year, [], lty=year - 1)
    if v2():
        n += _v2_world_extra(cur, ctx, made, year)
    STATS["world_teams"] = len(made)
    STATS["world_players"] = n
    return len(made), n


# ── [2026-11-25 VY_POOL_V2] 인원 상한 없는 풀 ─────────────────────────────
_SEQ = []


def v2():
    return stage1() and bool(getattr(_C, "VY_POOL_V2", False))


def _ratio():
    import club_ledger as _CL
    r = dict(getattr(_C, "VY_POOL_V2_RATIO", {}) or {})
    return [(c, float(r.get(c, 0.0))) for c in _CL.FIELD]


def _seq():
    """목표 비율 순환표(100칸) — 앞에서부터 누적 생성 수가 목표 비율에 가장 가깝게(최대 잔여, 같으면 칸 순서). 난수 없음."""
    if not _SEQ:
        rr = _ratio()
        cnt = collections.Counter()
        for _ in range(100):
            n = sum(cnt.values()) + 1
            best = max(rr, key=lambda x: (x[1] * n - cnt[x[0]], -[c for c, _w in rr].index(x[0])))[0]
            _SEQ.append(best)
            cnt[best] += 1
    return _SEQ


def _phase(vt):
    import zlib
    return zlib.crc32(f"vyseq|{int(vt)}".encode("utf-8")) % len(_seq())


def v2_intake_cells(vt, year, n):
    """그 해 입단 n명의 칸 — 팀마다 다른 시작 위치에서 순환표를 차례로(연도마다 n칸씩 앞으로)."""
    from database import get_game_start_year
    s = _seq()
    k0 = _phase(vt) + (int(year) - int(get_game_start_year())) * n
    return [s[(k0 + j) % len(s)] for j in range(n)]


def _v2_world_extra(cur, ctx, made, year):
    """새 월드: 기존 K명 생성 뒤 VY_POOL_V2_WORLD명까지 — 전체 구성이 목표 비율에 가장 가깝게(같으면 팀별 순환 위치),
    별도 난수(팀 id 기반)로 만들고 전역 난수 상태는 되돌린다(가상 유스 밖 월드 생성 결과를 바꾸지 않게)."""
    import club_ledger as _CL
    tgt = int(getattr(_C, "VY_POOL_V2_WORLD", 16))
    rr = _ratio()
    order = [c for c, _w in rr]
    st = random.getstate()
    n = 0
    try:
        for vt, pid, name, f, vtier, cname, cont in made:
            ros = [(a, b, o) for a, b, o in cur.execute(
                "SELECT id, position, ovr FROM ai_players WHERE team_id=? ORDER BY id", (vt,)).fetchall()]
            need = tgt - len(ros)
            if need <= 0:
                continue
            cnt = collections.Counter(_CL.cells_of(p, None)[0] for _i, p, _o in ros)
            ph = _phase(vt)
            cells = []
            for j in range(need):
                m = sum(cnt.values()) + 1
                gaps = {c: w * m - cnt[c] for c, w in rr}
                best = max(gaps.values())
                tied = [c for c in order if abs(gaps[c] - best) < 1e-9]
                c = tied[(ph + j) % len(tied)]
                cells.append(c)
                cnt[c] += 1
            import zlib
            random.seed(zlib.crc32(f"vyworld|{int(vt)}|{int(year)}".encode("utf-8")))
            n += _fill_cells(cur, ctx, vt, name, f, vtier, cname, cont, year, ros, cells, lty=year - 1)
        STATS["v2_world_extra"] += n
    finally:
        random.setstate(st)
    return n


def _gen_ctx(cur):
    import ai_lifecycle as _AL
    return {"names": _AL._build_name_cache(cur)}


def _pool_cells(formation, have_cells, k):
    """풀에 넣을 필드 칸 k개 — 모구단 포메이션 목표표(26명) 필드 비율에 가장 모자란 칸부터(같으면 칸 순서). 난수 없음."""
    import club_ledger as _CL
    tg = _CL.table(formation, 26)
    field = [x for x in _CL.FIELD if tg.get(x, 0) > 0]
    tot = float(sum(tg[x] for x in field)) or 1.0
    cnt = collections.Counter(have_cells)
    out = []
    for _ in range(k):
        n = sum(cnt.values()) + 1
        best = max(field, key=lambda x: (tg[x] / tot - cnt[x] / n, -_CL.CELLS.index(x)))
        out.append(best)
        cnt[best] += 1
    return out


def _fill_pool(cur, ctx, vt, parent, pname, f, vtier, cname, cont, year, roster, lty=None):
    """가상 팀 vt를 K명까지 채운다(신인 생성 공식 _gen_topup_rows — 칸 고정·국적 모구단 나라·계약/연봉 0)."""
    import club_ledger as _CL
    need = K - len(roster)
    if need <= 0:
        return 0
    have = [_CL.cells_of(p, None)[0] for _pid, p, _o in roster]
    cells = _pool_cells(f, [x for x in have if x], need)
    return _fill_cells(cur, ctx, vt, pname, f, vtier, cname, cont, year, roster, cells, lty)


def _fill_cells(cur, ctx, vt, pname, f, vtier, cname, cont, year, roster, cells, lty=None, ages=None):
    """정해진 칸 목록대로 가상 팀 vt에 선수를 만든다(_fill_pool 본문 — 칸 선택만 분리). 반환 생성 인원.
    ages: [2026-11-26 VY_STANDALONE] (최소, 최대) 생성 나이 — 주면 선수마다 그 범위에서 뽑아 생성 공식에 넘긴다."""
    import ai_lifecycle as _AL
    from constants import get_country_league_grade, COUNTRY_LEAGUE_OVR_OVERRIDE
    g = get_country_league_grade(cname)
    made = 0
    ros = list(roster)
    for cell in cells:
        rows = _AL._gen_topup_rows(cur, vt, vtier, cname, cont, pname, g, 1, {vt: ros}, ctx["names"], year,
                                   cname in COUNTRY_LEAGUE_OVR_OVERRIDE, foreign_ct0=999, src_tag="virtual_youth",
                                   formation=f, held_positions=[(p, None) for _i, p, _o in ros], force_cell=cell,
                                   age_override=(random.randint(*ages) if ages else None))
        if not rows:
            STATS["gen_fail"] += 1
            continue
        r = list(rows[0])
        if r[2] == "GK":          # 방어: 풀에는 GK를 두지 않는다
            STATS["gen_gk_skip"] += 1
            continue
        r[21] = cname             # nationality
        r[22] = cname             # true_nationality
        r[23] = 0                 # contract_end_year — 승격 전 계약 없음
        r[24] = year if lty is None else lty   # last_transfer_year
        r[27] = 0                 # salary — 승격 전 0
        nid = _AL._ledger_insert_row(cur, tuple(r))
        ros.append((nid, r[2], r[18]))
        made += 1
    return made


# ── 오프시즌 ────────────────────────────────────────────────────────────
def intake(c, year):
    """정기 입단: 가상 팀마다 K명까지(부족분만). 반환 생성 인원."""
    cur = _cur(c)
    v = parent_map(cur)
    if not v:
        return 0
    ph = ",".join("?" * len(v))
    roster = collections.defaultdict(list)
    for pid, tid, pos, ovr in cur.execute(
            f"SELECT id, team_id, position, ovr FROM ai_players WHERE team_id IN ({ph}) ORDER BY id", sorted(v)).fetchall():
        roster[tid].append((pid, pos, ovr))
    info = {r[0]: r for r in cur.execute(
        f"SELECT t.id, p.name, p.formation, t.current_tier, cn.name, cn.continent FROM teams t "
        f"JOIN teams p ON p.id = t.parent_team_id JOIN countries cn ON cn.id = t.country_id "
        f"WHERE t.id IN ({ph})", sorted(v)).fetchall()}
    ctx = _gen_ctx(cur)
    n = 0
    if v2():
        # [2026-11-25 VY_POOL_V2] 인원 상한·빈자리 보충 없이 해마다 고정 인원, 칸은 순환표
        k = int(getattr(_C, "VY_POOL_V2_INTAKE", 4))
        for vt in sorted(v):
            r = info.get(vt)
            if r is None:
                continue
            n += _fill_cells(cur, ctx, vt, r[1], r[2], r[3], r[4], r[5], year, roster[vt], v2_intake_cells(vt, year, k))
        STATS["intake"] += n
        STATS["v2_intake"] += n
        return n
    for vt in sorted(v):
        r = info.get(vt)
        if r is None or len(roster[vt]) >= K:
            continue
        n += _fill_pool(cur, ctx, vt, v[vt], r[1], r[2], r[3], r[4], r[5], year, roster[vt])
    STATS["intake"] += n
    return n


# 보존 판정 11가지(기준 문서 3장) — (테이블, 열)
_KEEP_TABLES = (("intl_squad", "player_id"), ("ai_nt_career", "player_id"), ("oly_squad_roles", "player_id"),
                ("oly_military_benefit", "player_id"), ("goal_events", "player_id"),
                ("hist.season_individual_awards", "player_id"), ("hist.ai_player_season_stats_by_comp", "player_id"),
                ("real_match_stats", "player_id"), ("ai_transfer_log", "player_id"),
                ("managers", "from_player_id"), ("injury_history", "player_id"))
# 완전 삭제 때 같이 지우는 참조(기준 문서 2·3장)
_DEL_TABLES = (("ai_player_custom_names", "player_id"), ("ai_player_sub_positions", "player_id"),
               # [2026-11-25 SUB_POS_V2] 새 부포 표·생성 판정 완료 표(V2를 켠 적 없는 세이브엔 표가 없어 무동작)
               ("ai_player_sub_positions_v2", "player_id"), ("ai_player_sub_innate_done", "player_id"),
               ("intl_callup_pool", "player_id"), ("intl_callup_role_pool", "player_id"),
               ("oly_reserve_cut", "player_id"), ("ai_player_realstat_season", "player_id"),
               ("hist.ai_player_ovr_history", "player_id"), ("hist.ai_player_position_history", "player_id"),
               ("hist.ai_player_position_history_half", "player_id"), ("hist.ai_player_season_stats", "player_id"),
               ("hist.ai_player_season_team_stats", "player_id"))


def _ids_in(cur, table, col, ids):
    out = set()
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        ph = ",".join("?" * len(part))
        try:
            out.update(r[0] for r in cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IN ({ph})", part))
        except Exception:
            pass
    return out


def cleanup(c, year):
    """24세 이상 미승격 가상 유스 정리 — 보존 기록이 있으면 은퇴 아카이브, 없으면 참조까지 완전 삭제(한 SAVEPOINT).
    자유 이적 등록·대체 신인 없음. 반환 (삭제, 아카이브)."""
    cur = _cur(c)
    v = vids(cur)
    if not v:
        return 0, 0
    ph = ",".join("?" * len(v))
    ids = [r[0] for r in cur.execute(
        f"SELECT id FROM ai_players WHERE team_id IN ({ph}) AND age > ? ORDER BY id", (*sorted(v), MAX_AGE)).fetchall()]
    return _purge(cur, ids, year)


def _purge(cur, ids, year):
    """[2026-11-26 cleanup에서 분리] 유스 선수 정리 — 보존 기록이 있으면 은퇴 아카이브, 없으면 참조까지 완전 삭제
    (한 SAVEPOINT). 24세 정리와 단독 모드 유스팀 폐쇄가 같이 쓴다. 반환 (삭제, 아카이브)."""
    if not ids:
        return 0, 0
    try:
        from database import history_sync_for_read
        history_sync_for_read()
    except Exception:
        pass
    keep = set()
    for t, col in _KEEP_TABLES:
        hit = _ids_in(cur, t, col, ids)
        if hit:
            STATS[f"archive_reason|{t}"] += len(hit)
        keep |= hit
    arch = [i for i in ids if i in keep]
    dele = [i for i in ids if i not in keep]
    cur.execute("SAVEPOINT vy_cleanup")
    try:
        if arch:
            import ai_lifecycle as _AL
            _AL._archive_forced_out_players(cur, arch, year)
        for k in range(0, len(dele), 500):
            part = dele[k:k + 500]
            pp = ",".join("?" * len(part))
            for t, col in _DEL_TABLES:
                try:
                    n0 = cur.execute(f"DELETE FROM {t} WHERE {col} IN ({pp})", part).rowcount
                    if n0 and n0 > 0:
                        STATS[f"del_ref|{t}"] += n0
                except Exception:
                    pass
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            pp = ",".join("?" * len(part))
            cur.execute(f"DELETE FROM ai_players WHERE id IN ({pp})", part)
        cur.execute("RELEASE vy_cleanup")
    except Exception:
        cur.execute("ROLLBACK TO vy_cleanup")
        cur.execute("RELEASE vy_cleanup")
        raise
    STATS["deleted"] += len(dele)
    STATS["archived"] += len(arch)
    return len(dele), len(arch)



# ── [2026-11-26 VY_STANDALONE] 기본 게임(C2 끔) 유스팀 — 보유 자격 재평가·입단·폐쇄 ─────────────────────────────
# 상태 표 vy_grace(vt_id, since_year, closed): 자격을 잃은 해(since_year)부터 유예, VY_SA_GRACE시즌이 지나도 못 되찾으면
# 폐쇄(closed=1 — 팀 행은 classification_status 그대로 두어 모든 "가상 유스 제외" 조회가 계속 이 팀을 빼게 하고, 기록 화면의
# "모구단 U-21" 표시도 유지). 자격을 되찾으면 유예 취소(행 삭제), 폐쇄된 팀이면 다시 열어 VY_SA_POOL명을 채운다.
_SA_CLOSING = set()   # 이번 오프시즌 끝에 닫을 유스팀(재평가가 정하고 sa_close가 닫는다 — 그 사이 신인 공급은 계속 쓴다)


def _sa_table(cur):
    cur.execute("CREATE TABLE IF NOT EXISTS vy_grace(vt_id INTEGER PRIMARY KEY, since_year INTEGER NOT NULL, "
                "closed INTEGER NOT NULL DEFAULT 0)")


def _sa_state(cur):
    """{유스팀 id: (since_year, closed)}"""
    _sa_table(cur)
    return {int(a): (int(b), int(cl)) for a, b, cl in cur.execute("SELECT vt_id, since_year, closed FROM vy_grace")}


def sa_closed(c):
    """폐쇄된 유스팀 id 집합(단독 모드)."""
    try:
        return {v for v, (_s, cl) in _sa_state(_cur(c)).items() if cl}
    except Exception:
        return set()


def _sa_fill(cur, ctx, vt, pname, f, vtier, cname, cont, year, roster, lty=None, mode="intake"):
    """유스팀 vt를 VY_SA_POOL명까지 채운다 — 칸은 모구단 포메이션 목표 비율에 가장 모자란 칸부터, 나이 VY_SA_AGE.
    mode: "intake"(정기 입단) / "world"(월드 생성) / "create"(새로 만들거나 다시 연 유스팀) — VY_GEN_V3에서만 쓴다."""
    if v3():
        return _v3_fill(cur, ctx, vt, pname, f, vtier, cname, cont, year, roster, lty, mode)
    import club_ledger as _CL
    need = int(getattr(_C, "VY_SA_POOL", 20)) - len(roster)
    if need <= 0:
        return 0
    have = [_CL.cells_of(p, None)[0] for _pid, p, _o in roster]
    cells = _pool_cells(f, [x for x in have if x], need)
    return _fill_cells(cur, ctx, vt, pname, f, vtier, cname, cont, year, roster, cells, lty,
                       ages=tuple(getattr(_C, "VY_SA_AGE", (15, 21))))


def sa_reeval(c, year):
    """오프시즌 시작 — 보유 자격 재평가. 새 자격 → 바로 생성(또는 폐쇄 팀 재개) 후 VY_SA_POOL명, 자격 상실 → 유예 시작,
    유예 VY_SA_GRACE시즌 경과 → 이번 오프시즌 끝에 폐쇄 예약, 자격 회복 → 유예 취소. 열린 유스팀의 가상 부수(current_tier)는
    모구단 현재 부수 + 1로 맞춘다. 반환 집계 dict."""
    res = collections.Counter()
    if not standalone():
        return dict(res)
    cur = _cur(c)
    st = _sa_state(cur)
    grace_n = int(getattr(_C, "VY_SA_GRACE", 2))
    elig = {t[0]: t for t in compute_targets(cur)}
    pm = parent_map(cur)
    open_by_parent, closed_by_parent = {}, {}
    for vt, par in pm.items():
        (closed_by_parent if st.get(vt, (0, 0))[1] else open_by_parent)[par] = vt
    vt_max = _vtier_map(cur)
    _SA_CLOSING.clear()
    for par, vt in sorted(open_by_parent.items()):
        if par in elig:
            if vt in st:
                cur.execute("DELETE FROM vy_grace WHERE vt_id=?", (vt,))
                res["grace_cancel"] += 1
            _pid, _n, _f, tier, cid, _cn, _co = elig[par]
            cur.execute("UPDATE teams SET current_tier=? WHERE id=?", (min(tier + 1, vt_max.get(cid) or tier), vt))
        elif vt not in st:
            cur.execute("INSERT INTO vy_grace(vt_id, since_year, closed) VALUES(?,?,0)", (vt, year))
            res["grace_start"] += 1
        elif year - st[vt][0] >= grace_n:
            _SA_CLOSING.add(vt)
            res["closing"] += 1
        else:
            res["grace_keep"] += 1
    ctx = None
    for par, (pid, name, f, tier, cid, cname, cont) in sorted(elig.items()):
        if par in open_by_parent:
            continue
        vtier = min(tier + 1, vt_max.get(cid) or tier)
        if par in closed_by_parent:
            vt = closed_by_parent[par]
            cur.execute("UPDATE teams SET formation=?, current_tier=? WHERE id=?", (f, vtier, vt))
            cur.execute("DELETE FROM vy_grace WHERE vt_id=?", (vt,))
            res["reopen"] += 1
        else:
            cur.execute("INSERT INTO teams(league_id, country_id, name, formation, current_tier, classification_status, "
                        "parent_team_id) VALUES(NULL,?,?,?,?,?,?)", (cid, name + NAME_SUFFIX, f, vtier, STATUS, pid))
            vt = cur.lastrowid
            res["create"] += 1
        if ctx is None:
            ctx = _gen_ctx(cur)
        # 이번에 생긴 유스는 이번 창구에서는 데뷔하지 않는다(정기 입단과 같은 기준 — last_transfer_year = 올해)
        res["create_players"] += _sa_fill(cur, ctx, vt, name, f, vtier, cname, cont, year, [], lty=year, mode="create")
    if v3():
        _v3_project(cur, year, "create")
    _FAST["key"] = None
    for k, v in res.items():
        STATS[f"sa_{k}"] += v
    return dict(res)


def sa_close(c, year):
    """오프시즌 끝 — 재평가가 폐쇄 예약한 유스팀을 닫는다(남은 선수 정리, 팀 행은 남김). 반환 (팀 수, 삭제, 아카이브)."""
    if not standalone() or not _SA_CLOSING:
        _SA_CLOSING.clear()
        return 0, 0, 0
    cur = _cur(c)
    _sa_table(cur)
    vts = sorted(_SA_CLOSING)
    ph = ",".join("?" * len(vts))
    ids = [r[0] for r in cur.execute(f"SELECT id FROM ai_players WHERE team_id IN ({ph}) ORDER BY id", vts).fetchall()]
    d, a = _purge(cur, ids, year)
    cur.executemany("UPDATE vy_grace SET closed=1 WHERE vt_id=?", [(v,) for v in vts])
    _SA_CLOSING.clear()
    STATS["sa_closed"] += len(vts)
    return len(vts), d, a


def sa_intake(c, year):
    """오프시즌 끝 — 유예·폐쇄 중이 아닌 유스팀을 VY_SA_POOL명까지 채운다(생성 나이 VY_SA_AGE). 반환 생성 인원."""
    if not standalone():
        return 0
    cur = _cur(c)
    st = _sa_state(cur)
    v = {vt: par for vt, par in parent_map(cur).items() if vt not in st}
    if not v:
        return 0
    ph = ",".join("?" * len(v))
    roster = collections.defaultdict(list)
    for pid, tid, pos, ovr in cur.execute(
            f"SELECT id, team_id, position, ovr FROM ai_players WHERE team_id IN ({ph}) ORDER BY id", sorted(v)).fetchall():
        roster[tid].append((pid, pos, ovr))
    info = {r[0]: r for r in cur.execute(
        f"SELECT t.id, p.name, p.formation, t.current_tier, cn.name, cn.continent FROM teams t "
        f"JOIN teams p ON p.id = t.parent_team_id JOIN countries cn ON cn.id = t.country_id "
        f"WHERE t.id IN ({ph})", sorted(v)).fetchall()}
    ctx = _gen_ctx(cur)
    n = 0
    for vt in sorted(v):
        r = info.get(vt)
        if r is None:
            continue
        n += _sa_fill(cur, ctx, vt, r[1], r[2], r[3], r[4], r[5], year, roster[vt], lty=year)
    STATS["sa_intake"] += n
    return n

# ── [2026-11-26 VY_GEN_V3 — 신민용 확정 S1] 유스 생성 V3: U-18/U-21 분리, 15세 생성, 현재 OVR과 잠재력 분리 ──────────
# constants.VY_GEN_V3 정의부 주석 참고. 성장 공식·데뷔(youth_supply)·24세 정리·유예·폐쇄는 바꾸지 않는다.
_V3_PLAN = []          # 월드 생성·새 유스팀 생성 뒤 성장 적용할 (선수 id, 적용 연수)
_V3_ROW_STATS = ("stamina", "speed", "jump", "strength", "shooting", "passing", "dribbling", "tackling", "heading",
                 "positioning", "setpiece", "mental", "confidence", "leadership", "concentration")   # INSERT 행 3~17 순서
V3_LOG = collections.Counter()   # 계측(동작과 무관)


def v3():
    return standalone() and bool(getattr(_C, "VY_GEN_V3", False))


def _v3_key(cur):
    """월드 고유 키 — 처음 쓸 때 팀 id·포메이션으로 만들어 저장(게임 난수를 쓰지 않는다). 이후 같은 값."""
    import zlib
    cur.execute("CREATE TABLE IF NOT EXISTS vy3_meta(k TEXT PRIMARY KEY, v INTEGER NOT NULL)")
    r = cur.execute("SELECT v FROM vy3_meta WHERE k='world_key'").fetchone()
    if r is not None:
        return int(r[0])
    h = 0
    for tid, f in cur.execute("SELECT id, COALESCE(formation, '') FROM teams ORDER BY id").fetchall():
        h = zlib.crc32(f"{tid}:{f};".encode(), h)
    cur.execute("INSERT INTO vy3_meta(k, v) VALUES('world_key', ?)", (int(h),))
    return int(h)


class _v3_rng:
    """유스 V3 전용 난수 구간 — 들어갈 때 게임 random 상태를 보관하고 (월드 키, 태그)로 시드, 나올 때 되돌린다.
    그 안에서 쓰는 random·(random에서 시드를 받는) numpy 난수는 게임 난수 흐름을 소비하지 않는다."""

    def __init__(self, cur, *tag):
        import zlib
        self.seed = zlib.crc32("|".join(map(str, (_v3_key(cur),) + tag)).encode())

    def __enter__(self):
        self.state = random.getstate()
        random.seed(self.seed)
        return random

    def __exit__(self, *exc):
        random.setstate(self.state)
        return False


def _v3_floor(cname, cont, tier):
    """리그 OVR 하한 — 신인 생성(_gen_topup_rows)과 같은 대륙·나라 보정(오버라이드 나라는 보정 없음). 표에 없으면 None."""
    from constants import (get_country_league_grade, get_ovr_range, CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ,
                           COUNTRY_LEAGUE_OVR_OVERRIDE)
    rng = get_ovr_range(get_country_league_grade(cname), tier or 1, cname)
    if not rng:
        return None
    lo = rng[0]
    if cname not in COUNTRY_LEAGUE_OVR_OVERRIDE:
        lo += round(CONTINENT_OVR_BONUS.get(cont, 0) + COUNTRY_OVR_ADJ.get(cname, 0))
    return int(lo)


def _v3_median(cur, tid, fb):
    o = sorted(r[0] for r in cur.execute("SELECT ovr FROM ai_players WHERE team_id=? AND ovr IS NOT NULL", (tid,)))
    if not o:
        return int(fb)
    m = len(o) // 2
    return int(round(o[m] if len(o) % 2 else (o[m - 1] + o[m]) / 2.0))


def _v3_club(cur, vt, vtier, cname, cont):
    """유스팀 vt의 생성 기준 — 산하팀 여부, 15세 현재 OVR 중앙, 잠재력 3단계 범위. 난수 없음."""
    par = cur.execute("SELECT parent_team_id FROM teams WHERE id=?", (vt,)).fetchone()[0]
    r = cur.execute("SELECT l.tier FROM teams t JOIN leagues l ON l.id = t.league_id WHERE t.id=?", (par,)).fetchone()
    ptier = int(r[0]) if r and r[0] else max(1, int(vtier or 2) - 1)
    affs = cur.execute(
        "SELECT t.id, l.tier, cn.name, cn.continent FROM teams t JOIN leagues l ON l.id = t.league_id "
        "JOIN countries cn ON cn.id = l.country_id WHERE t.classification_status='AFFILIATE' AND t.parent_team_id=? "
        "ORDER BY l.tier, t.id", (par,)).fetchall()
    f1 = _v3_floor(cname, cont, ptier) or 40
    m1 = _v3_median(cur, par, f1 + 1)
    if affs:
        low, top = affs[-1], affs[0]
        fl = _v3_floor(low[2], low[3], low[1]) or 40
        ml = _v3_median(cur, low[0], fl + 1)
        fb = _v3_floor(top[2], top[3], top[1]) or 40
        mb = _v3_median(cur, top[0], fb + 1)
        center = fl - int(getattr(_C, "VY_V3_OFF_AFF", 11))
        if low[0] == top[0]:
            # [2026-11-26 신민용 확정] 산하팀이 하나뿐(가장 아래 = B팀)이면 일반·우수가 같은 팀을 가리키므로 도달 수준으로
            # 나눈다 — 일반 B팀 하한−6 ~ B팀 하한, 우수 B팀 하한−2 ~ B팀 중앙값+3(B·C팀 둘 다 있는 구단은 기존 범위).
            bands = ((fb - 6, fb), (fb - 2, mb + 3), (f1 - 3, m1))
        else:
            bands = ((fl - 2, ml + 3), (fb - 2, mb + 3), (f1 - 3, m1))
    else:
        fy = _v3_floor(cname, cont, vtier) or 40
        center = fy - int(getattr(_C, "VY_V3_OFF_NOAFF", 12))
        bands = ((fy, fy + 4), (fy + 4, max(fy + 4, f1)), (f1 - 3, max(f1 - 3, m1)))
    if getattr(_C, "VY_V3_POT_NORMAL_LOW", False):
        # [2026-11-26 A안, 신민용+GPT 확정] 일반 유스(75%) 잠재력을 구단의 가장 아래 팀 수준이 보장되지 않게 낮춘다 —
        # 산하팀 있는 구단 '가장 아래 산하팀 하한 −10 ~ 하한 +2'(B팀만 있으면 B팀 기준), 없는 구단은 기존 범위를 8 내림.
        # 빅클럽 일반 유스가 모두 C팀 수준까지 커서 자리가 모자라고, 남는 유스가 진로 시장으로 하위 리그에 그 리그보다
        # 강하게 들어가던 문제(S6 실측: 강한 나라 하위 리그 데뷔 OVR 하한 +10~12, 잠재>성장 상한 33~59%) 대응.
        # 우수·특급 범위와 15세 현재 OVR 생성은 그대로.
        lo_off = int(getattr(_C, "VY_V3_NORMAL_LO", 10))
        hi_off = int(getattr(_C, "VY_V3_NORMAL_HI", 2))
        if affs:
            base = fl if low[0] != top[0] else fb
            nb = (base - lo_off, base + hi_off)
        else:
            sh = int(getattr(_C, "VY_V3_NORMAL_NOAFF_SHIFT", 8))
            nb = (bands[0][0] - sh, bands[0][1] - sh)
        bands = (nb,) + tuple(bands[1:])
    bands = tuple((int(min(99, a)), int(min(99, max(a, b)))) for a, b in bands)
    return {"par": par, "aff": bool(affs), "center": int(center), "bands": bands}


def _v3_spread(lo, hi, k):
    """lo~hi 나이를 k명에게 고르게(난수 없음)."""
    if k <= 0:
        return []
    w = hi - lo + 1
    return sorted(lo + (i % w) for i in range(k))


def _v3_make(cur, ctx, vt, pname, f, vtier, cname, cont, year, roster, cells, ages, lty, club):
    """칸·나이 목록대로 15세 유스를 만든다(신인 생성 공식으로 포지션·부역할 등을 정하고 능력치·OVR·잠재력만 V3 기준으로
    바꾼다). 반환 [(선수 id, 성장 적용 연수)]. 호출 쪽이 _v3_rng 안에서 부른다."""
    import ai_lifecycle as _AL
    from database import calc_ovr
    from constants import get_country_league_grade, COUNTRY_LEAGUE_OVR_OVERRIDE
    g = get_country_league_grade(cname)
    e = int(getattr(_C, "VY_V3_ENTRY_AGE", 15))
    sd = float(getattr(_C, "VY_V3_OVR_SD", 4))
    split = tuple(getattr(_C, "VY_V3_POT_SPLIT", (0.75, 0.20, 0.05)))
    c0 = club["center"]
    ros = list(roster)
    out = []
    for cell, age in zip(cells, ages):
        rows = _AL._gen_topup_rows(cur, vt, vtier, cname, cont, pname, g, 1, {vt: ros}, ctx["names"], year,
                                   cname in COUNTRY_LEAGUE_OVR_OVERRIDE, foreign_ct0=999, src_tag="virtual_youth",
                                   formation=f, held_positions=[(p, None) for _i, p, _o in ros], force_cell=cell,
                                   age_override=e)
        if not rows:
            STATS["gen_fail"] += 1
            continue
        r = list(rows[0])
        if r[2] == "GK":
            STATS["gen_gk_skip"] += 1
            continue
        pos = r[2]
        tgt = int(round(random.gauss(c0, sd)))
        tgt = max(15, max(int(c0 - 2 * sd), min(int(c0 + 2 * sd), tgt)))
        stats = _AL._gen_stats(pos, tgt)
        ovr = calc_ovr(pos, stats)
        for _try in range(3):                 # 능력치 역산의 OVR 오차(보통 +1~3)를 목표로 되돌린다
            if abs(ovr - tgt) <= 1:
                break
            stats = _AL._gen_stats(pos, max(10, tgt - (ovr - tgt)))
            ovr = calc_ovr(pos, stats)
        u = random.random()
        k = 0 if u < split[0] else (1 if u < split[0] + split[1] else 2)
        lo, hi = club["bands"][k]
        sampled = random.randint(lo, hi)
        pot = min(99, max(sampled, ovr + 1))
        gap = int(getattr(_C, "VY_V3_NORMAL_MIN_GAP", 3)) if getattr(_C, "VY_V3_POT_NORMAL_LOW", False) else 0
        if k == 0 and gap > 0 and pot < ovr + gap:
            # [2026-11-27 A안 보정] 일반 유스 최소 성장 여지 — 난수 추가 소비 없음. 보정된 선수는 따로 기록(검증용:
            # 추첨값·범위 상한 대비 얼마나 올렸는지)
            pot = min(99, ovr + gap)
            cur.execute("CREATE TABLE IF NOT EXISTS vy3_pot_lift(player_id INTEGER PRIMARY KEY, sampled INTEGER, "
                        "band_hi INTEGER, ovr15 INTEGER)")
            V3_LOG["pot_lift"] += 1
            lift = (sampled, hi, ovr)
        else:
            lift = None
        for j, sname in enumerate(_V3_ROW_STATS):
            r[3 + j] = stats[sname]
        r[18] = ovr
        r[19] = e
        r[21] = cname
        r[22] = cname
        r[23] = 0
        r[24] = year if lty is None else lty
        r[26] = pot
        r[27] = 0
        nid = _AL._ledger_insert_row(cur, tuple(r))
        # [2026-11-26 S4] 잠재력 단계 기록(0 일반 · 1 우수 · 2 특급) — 조기 데뷔 판단용, 현재 OVR과 별개
        cur.execute("CREATE TABLE IF NOT EXISTS vy3_talent(player_id INTEGER PRIMARY KEY, tier INTEGER NOT NULL)")
        cur.execute("INSERT OR REPLACE INTO vy3_talent(player_id, tier) VALUES(?,?)", (nid, k))
        if lift:
            cur.execute("INSERT OR REPLACE INTO vy3_pot_lift(player_id, sampled, band_hi, ovr15) VALUES(?,?,?,?)", (nid,) + lift)
        ros.append((nid, pos, ovr))
        out.append((nid, max(0, int(age) - e)))
        V3_LOG[f"pot_{('normal', 'good', 'special')[k]}"] += 1
    return out


def _v3_fill(cur, ctx, vt, pname, f, vtier, cname, cont, year, roster, lty, mode):
    """VY_GEN_V3 입단·생성. intake: 15세만, 산하팀 있는 구단 20 − 인원, 없는 구단 min(10 − U-18, 20 − 인원) — 정원은 목표일
    뿐 채울 의무가 아니고 넘기지 않는다. world/create(빈 팀): 나이를 고르게 나눠 만든 뒤 _v3_project가 성장 적용."""
    import club_ledger as _CL
    pool_aff = int(getattr(_C, "VY_SA_POOL", 20))
    pool_noaff = int(getattr(_C, "VY_V3_NOAFF_POOL", pool_aff))
    split_n = int(getattr(_C, "VY_V3_SPLIT_TARGET", 10))
    u18 = int(getattr(_C, "VY_V3_U18_MAX_AGE", 18))
    e = int(getattr(_C, "VY_V3_ENTRY_AGE", 15))
    with _v3_rng(cur, mode, year, vt):
        club = _v3_club(cur, vt, vtier, cname, cont)
        pool = pool_aff if club["aff"] else pool_noaff
        ages_now = [int(a or 0) for (a,) in cur.execute("SELECT age FROM ai_players WHERE team_id=?", (vt,)).fetchall()]
        n = len(ages_now)
        n18 = sum(1 for a in ages_now if a <= u18)
        if mode == "intake":
            need = (pool - n) if club["aff"] else min(split_n - n18, pool - n)
            ages = [e] * max(0, need)
        elif club["aff"]:
            ages = _v3_spread(e, u18, pool - n)
        else:
            k18 = max(0, min(split_n - n18, pool - n))
            ages = _v3_spread(e, u18, k18) + _v3_spread(u18 + 1, 21, max(0, pool - n - k18))
        if not ages:
            V3_LOG[f"{mode}_none"] += 1
            return 0
        have = [_CL.cells_of(p, None)[0] for _pid, p, _o in roster]
        cells = _pool_cells(f, [x for x in have if x], len(ages))
        made = _v3_make(cur, ctx, vt, pname, f, vtier, cname, cont, year, roster, cells, ages, lty, club)
    V3_LOG[f"{mode}_players"] += len(made)
    V3_LOG[f"{mode}_{'aff' if club['aff'] else 'noaff'}_teams"] += 1
    if mode != "intake":
        _V3_PLAN.extend(made)
    return len(made)


def _v3_project(cur, year, tag):
    """_V3_PLAN의 선수에게 기존 성장 공식(_age_and_progress_np — 나이 +1과 성장)을 적용 연수만큼 반복한다. 팀 상한은
    _age_and_progress와 같은 함수(_growth_team_caps)로 구한다. 별도 난수 구간. 반환 대상 인원."""
    import sqlite3
    import ai_lifecycle as _AL
    plan = [(p, k) for p, k in _V3_PLAN if k > 0]
    _V3_PLAN.clear()
    if not plan:
        return 0
    conn = cur.connection
    rc = conn.cursor()
    rc.row_factory = sqlite3.Row
    with _v3_rng(cur, "grow", tag, year):
        team_cap = _AL._growth_team_caps(rc)
        for step in range(1, max(k for _p, k in plan) + 1):
            ids = sorted(p for p, k in plan if k >= step)
            tc = conn.cursor()
            tc.row_factory = None
            rows = []
            for i in range(0, len(ids), 900):
                part = ids[i:i + 900]
                rows += tc.execute(_AL._AGE_PROGRESS_SQL + f" WHERE id IN ({','.join('?' * len(part))})",
                                   part).fetchall()
            rows.sort(key=lambda x: x[0])
            if _AL._HAS_NUMPY:
                _AL._age_and_progress_np(cur, rows, team_cap, 46)
            else:
                _AL._age_and_progress_py(cur, rows, team_cap, 46)
    V3_LOG[f"project_{tag}"] += len(plan)
    return len(plan)