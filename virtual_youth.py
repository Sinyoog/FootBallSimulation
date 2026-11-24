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
        if status in ("AFFILIATE", STATUS) or tid in mil or tid in has_aff:
            continue
        g = gc.get(cname)
        if g is None:
            g = gc[cname] = get_country_league_grade(cname)
        lim = 4 if cname in BIG5 else (2 if g == "A" else 1)
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
    if not stage1():
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
    for vt, pid, name, f, vtier, cname, cont in made:
        # 월드 생성 선수는 첫 오프시즌부터 승격 후보(last_transfer_year = 시작 연도 − 1). 정기 입단 선수는 그 해 창구 제외.
        n += _fill_pool(cur, ctx, vt, pid, name, f, vtier, cname, cont, year, [], lty=year - 1)
    STATS["world_teams"] = len(made)
    STATS["world_players"] = n
    return len(made), n


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
    import ai_lifecycle as _AL
    import club_ledger as _CL
    from constants import get_country_league_grade, COUNTRY_LEAGUE_OVR_OVERRIDE
    need = K - len(roster)
    if need <= 0:
        return 0
    g = get_country_league_grade(cname)
    have = [_CL.cells_of(p, None)[0] for _pid, p, _o in roster]
    cells = _pool_cells(f, [x for x in have if x], need)
    made = 0
    ros = list(roster)
    for cell in cells:
        rows = _AL._gen_topup_rows(cur, vt, vtier, cname, cont, pname, g, 1, {vt: ros}, ctx["names"], year,
                                   cname in COUNTRY_LEAGUE_OVR_OVERRIDE, foreign_ct0=999, src_tag="virtual_youth",
                                   formation=f, held_positions=[(p, None) for _i, p, _o in ros], force_cell=cell)
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