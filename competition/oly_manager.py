# -*- coding: utf-8 -*-
"""competition/oly_manager.py — U-23 올림픽 대표팀 감독 [2026-10 신설 — 올림픽 2단계, 신민용 확정]

성인 대표팀 감독(national_manager.py)과 **별개 직책**이다. 섞이지 않게 하는 장치:
  · 재임 기록은 oly_team_managers에 따로(national_team_managers를 건드리지 않는다).
  · 감독 풀(managers)만 클럽·대표팀과 공유한다 — 부임하면 managers.status='u23'.
    클럽 시장(ai_lifecycle._manager_turnover)과 성인 대표팀 시장(national_manager._fill_vacancy)은
    'u23' 감독을 후보로 보지 않는다(한 감독은 한 자리만). 승진 후보로 뽑힌 감독만 예외다.
  · 평가는 성인 대표팀 평가(national_objectives)와 다른 표(oly_objectives)에 남는다.

규칙(constants.OLY_MGR_* 주석과 같은 내용):
  · 자리 수준 = 대표팀 자리 수준 × 3부 배수 — "3·4부 리그 감독과 비슷하거나 조금 위".
  · 자국 감독 확률 S 70% · A 80% · B 90% · C 이하 100%. 정해진 쪽에 맞는 무직 감독이 없으면
    그쪽 국적의 신인을 새로 만든다(외국 쪽이면 감독 시장과 같은 국적 가중치로 국적을 뽑는다).
  · 계약은 항상 다음 올림픽 해까지. 선임·경질·은퇴는 시즌 전환(연말 — on_season_end)에만 하므로
    올림픽 해에는 대회가 끝난 연말에만 바뀐다. 처음 켰을 때(감독이 없는 나라)만 예선 직전에 심는다.
  · 올림픽 해 연말: 선수단(U-23) 예상 전력 대비 성과로 평가 → 명성 갱신 → 재계약 또는 교체.
    예상 전력 = 본선은 본선 명단 전력의 참가국 안 순위, 예선 탈락국은 예선 명단 전력의 연맹 안 순위가
    그 연맹 본선 티켓 수 안이었는지(성인 대표팀 예선 탈락 평가와 같은 규칙).
  · 비올림픽 해 연말: 직전 올림픽에서 목표에 못 미치고도 재계약한 감독만 성적 부진 경질 가능.
  · 승진: 올림픽 평가에서 본선에 나가 목표 이상을 했을 때만 아주 작은 확률로 "승진 후보"가 되고,
    후보 기간(2년) 동안 클럽·성인 대표팀 시장 후보에 들어간다. 실제 선임은 그 시장의 기존 조건을
    그대로 통과해야 한다. 다른 시장이 데려가면 다음 연말에 U-23 재임을 '승진'으로 닫고 후임을 세운다.

결정성: 전역 random을 쓰지 않는다 — (world_salt, 나라, 연도)에서 파생한 random.Random만 쓴다.
"""
import random
import zlib

import constants as C
from database import get_conn

STATUS = "u23"
_style_cache = {}       # 나라 이름 → (감독 id, 스타일 dict 또는 None) — 감독이 바뀔 때마다 비운다
_bonus_cache = {}       # (대회 id, 나라) → 경기 보정


def enabled():
    from competition.olympics import enabled as _en
    return _en()


def migrations():
    """olympics.migrations()가 붙인다(켜져 있을 때만)."""
    return [
        # 재임 — 성인 대표팀 national_team_managers와 같은 연도 단위 형식(start_year/end_year/
        # contract_until/contract_log "부임연도:만료연도;재계약연도:만료연도").
        # eval_* = 마지막 올림픽 평가(비올림픽 해 성적 부진 경질 판정용), promo_until = 승진 후보 기간 끝.
        "CREATE TABLE IF NOT EXISTS oly_team_managers(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "country_id INTEGER, manager_id INTEGER, start_year INTEGER, end_year INTEGER, "
        "end_reason TEXT DEFAULT '', role TEXT DEFAULT 'full', contract_until INTEGER DEFAULT 0, "
        "job_level REAL DEFAULT 0, contract_log TEXT DEFAULT '', tournaments INTEGER DEFAULT 0, "
        "best_rank INTEGER DEFAULT 0, eval_year INTEGER DEFAULT 0, eval_orank INTEGER DEFAULT 0, "
        "eval_rrank INTEGER DEFAULT 0, promo_until INTEGER DEFAULT 0)",
        # 목표·결과 — 본선은 본선 대회 id, 예선 탈락국은 그 나라가 치른 예선 대회 id로 남긴다.
        "CREATE TABLE IF NOT EXISTS oly_objectives(tournament_id INTEGER, year INTEGER, "
        "country TEXT, country_id INTEGER, squad_ovr REAL DEFAULT 0, field_rank INTEGER DEFAULT 0, "
        "objective TEXT DEFAULT '', objective_rank INTEGER DEFAULT 0, result TEXT DEFAULT '', "
        "result_rank INTEGER DEFAULT -1, manager_id INTEGER DEFAULT 0, "
        "PRIMARY KEY(tournament_id, country))",
    ]


RESET_TABLES = ("oly_team_managers", "oly_objectives")


# ══════════════════════════════════════════════════════════════════
# 순수 도우미
# ══════════════════════════════════════════════════════════════════
def job_level(cname):
    """U-23 감독 자리 수준 = 대표팀 자리 수준 × 3부 배수."""
    from constants import national_job_level, get_country_grade
    return round(float(national_job_level(get_country_grade(cname))) * C.OLY_MGR_LEVEL_MULT, 2)


def domestic_prob(cname):
    from constants import get_country_grade
    return float(C.OLY_MGR_DOMESTIC_PROB.get(get_country_grade(cname), 1.0))


def next_olympic_year(year):
    """year보다 뒤의 첫 올림픽 해."""
    a, iv = C.OLYMPIC_ANCHOR_YEAR, C.OLYMPIC_INTERVAL
    y = int(year) + 1
    while (y - a) % iv:
        y += 1
    return y


def _is_olympic_year(year):
    return (int(year) - C.OLYMPIC_ANCHOR_YEAR) % C.OLYMPIC_INTERVAL == 0


def promo_prob(objective_rank, result_rank, result_key):
    """승진 후보 확률 — 본선에 나가 목표 이상일 때만. 목표 그대로(우승 후보의 우승 포함) 0.3%,
    초과 10단계당 +0.4%, 최대 2%."""
    if result_key == "예선탈락":
        return 0.0
    diff = float(result_rank) - float(objective_rank)
    if diff < 0:
        return 0.0
    return min(C.OLY_MGR_PROMO_MAX, C.OLY_MGR_PROMO_BASE + C.OLY_MGR_PROMO_PER10 * diff / 10.0)


def _salt():
    from database import get_world_salt
    return get_world_salt()


def _clear_caches():
    _style_cache.clear()
    _bonus_cache.clear()


def _finals_done(conn, year):
    r = conn.execute("SELECT status FROM intl_tournaments WHERE year=? AND kind='olympic' "
                     "ORDER BY id LIMIT 1", (int(year),)).fetchone()
    return bool(r) and r[0] == "done"


def _countries(conn):
    rows = [(int(r[0]), r[1]) for r in conn.execute("SELECT id, name FROM countries ORDER BY id")]
    from military_service import MILITARY_COUNTRY
    return [(i, n) for i, n in rows if n and n != MILITARY_COUNTRY]


def _coached_this_year(lk, y):
    """이 재임이 y년을 지휘했나 — 연말(시즌 전환)에 부임했으면 그 해는 지휘하지 않았다(다음 해부터).
    예외: 처음 켰을 때 올림픽 해 대회 전에 심은 감독(첫 계약이 "y:y")은 그 해를 지휘했다."""
    sy = int(lk.get("start_year") or 0)
    return sy < int(y) or (lk.get("contract_log") or "").startswith(f"{int(y)}:{int(y)}")


def _open_links(conn):
    return {int(r["country_id"]): dict(r) for r in conn.execute(
        """SELECT o.*, m.reputation, m.recent_perf, m.birth_year, m.retired,
                  m.status AS m_status
           FROM oly_team_managers o JOIN managers m ON m.id = o.manager_id
           WHERE o.end_year IS NULL ORDER BY o.id""")}


# ══════════════════════════════════════════════════════════════════
# 조회 — 명단 선발(포메이션)·경기 보정·기록실
# ══════════════════════════════════════════════════════════════════
def manager_for_country(country, conn=None):
    """지금 그 나라 U-23 감독 (id, 스타일 dict) — 없으면 (0, None)."""
    if country in _style_cache:
        return _style_cache[country]
    own = conn is None
    conn = conn or get_conn()
    try:
        r = conn.execute(
            """SELECT m.id, m.style_attack, m.style_buildup, m.style_press
               FROM oly_team_managers o JOIN countries c ON c.id = o.country_id
               JOIN managers m ON m.id = o.manager_id
               WHERE c.name = ? AND o.end_year IS NULL ORDER BY o.id DESC LIMIT 1""",
            (country,)).fetchone()
        out = ((int(r[0]), {"style_attack": r[1], "style_buildup": r[2], "style_press": r[3]})
               if r else (0, None))
    except Exception:
        out = (0, None)
    finally:
        if own:
            conn.close()
    _style_cache[country] = out
    return out


def manager_for_tournament(tournament_id, country, conn=None):
    """그 올림픽 대회(예선·본선)에서 그 나라를 맡은 U-23 감독 id(없으면 0) — 명단을 뽑을 때
    intl_formations에 박아 둔 값을 먼저 보고, 없으면 그 해 재임 규칙으로 찾는다."""
    own = conn is None
    conn = conn or get_conn()
    try:
        try:
            r = conn.execute("SELECT manager_id FROM intl_formations WHERE tournament_id=? "
                             "AND country=?", (tournament_id, country)).fetchone()
            if r and r[0]:
                return int(r[0])
        except Exception:
            pass
        try:
            t = conn.execute("SELECT year FROM intl_tournaments WHERE id=?",
                             (tournament_id,)).fetchone()
            if not t:
                return 0
            r = conn.execute(
                """SELECT o.manager_id FROM oly_team_managers o JOIN countries c
                   ON c.id = o.country_id WHERE c.name=? AND o.start_year <= ?
                   AND (o.end_year IS NULL OR o.end_year >= ?) ORDER BY o.id LIMIT 1""",
                (country, int(t[0]), int(t[0]))).fetchone()
            return int(r[0]) if r else 0
        except Exception:
            return 0
    finally:
        if own:
            conn.close()


def match_bonus(tournament_id, country, squad_ovr):
    """U-23 감독 능력 경기 보정 — 성인 대표팀(intl_engine._nat_mgr_bonus)과 같은 식
    (constants.manager_match_bonus)을 그 대회 U-23 명단 전력(intl_entries.ovr)에 건다."""
    from constants import MANAGER_MATCH_BONUS_ENABLED, manager_match_bonus
    if not MANAGER_MATCH_BONUS_ENABLED or not country:
        return 0.0
    key = (int(tournament_id), country)
    if key not in _bonus_cache:
        b = 0.0
        conn = get_conn()
        try:
            mid = manager_for_tournament(tournament_id, country, conn)
            if mid:
                r = conn.execute("SELECT COALESCE(manager_ability,0) FROM managers WHERE id=?",
                                 (mid,)).fetchone()
                ab = int(r[0] or 0) if r else 0
                if ab:
                    b = manager_match_bonus(ab, float(squad_ovr or 0.0))
        except Exception:
            b = 0.0
        finally:
            conn.close()
        _bonus_cache[key] = b
    return _bonus_cache[key]


# ══════════════════════════════════════════════════════════════════
# 목표 — 본선 생성 직후 고정
# ══════════════════════════════════════════════════════════════════
def _default_stages():
    return {"group", "R16", "QF", "SF", "TP", "F"}


def fix_objectives(tournament_id, conn=None):
    """본선 참가국의 U-23 전력 순위로 목표를 굳힌다(멱등). 전력 = 본선 명단 전력(oly_squad_meta.ovr).
    목표 규칙은 성인 대표팀 본선과 같다(national_manager.national_objective_in_field)."""
    from national_manager import national_objective_in_field
    own = conn is None
    conn = conn or get_conn()
    try:
        t = conn.execute("SELECT id, year, kind FROM intl_tournaments WHERE id=?",
                         (tournament_id,)).fetchone()
        if not t or t["kind"] != "olympic":
            return 0
        if conn.execute("SELECT 1 FROM oly_objectives WHERE tournament_id=? LIMIT 1",
                        (tournament_id,)).fetchone():
            return 0
        ovr = {r[0]: float(r[1] or 0.0) for r in conn.execute(
            "SELECT country, ovr FROM oly_squad_meta WHERE tournament_id=?", (tournament_id,))}
        entries = [r[0] for r in conn.execute(
            "SELECT country FROM intl_entries WHERE tournament_id=? ORDER BY country",
            (tournament_id,))]
        if not entries:
            return 0
        if any(cn not in ovr for cn in entries):
            for r in conn.execute("SELECT country, ovr FROM intl_entries WHERE tournament_id=?",
                                  (tournament_id,)):
                ovr.setdefault(r[0], float(r[1] or 0.0))
        stages = {r[0] for r in conn.execute(
            "SELECT DISTINCT stage FROM intl_matches WHERE tournament_id=?", (tournament_id,))}
        stages = stages or _default_stages()
        order = sorted(entries, key=lambda cn: (-ovr.get(cn, 0.0), cn))
        cids = {r[1]: int(r[0]) for r in conn.execute("SELECT id, name FROM countries")}
        rows = []
        for rank, cn in enumerate(order, 1):
            name, orank = national_objective_in_field(rank, stages)
            mid = manager_for_tournament(tournament_id, cn, conn)
            rows.append((tournament_id, int(t["year"]), cn, cids.get(cn, 0), ovr.get(cn, 0.0),
                         rank, name, orank, mid))
        conn.executemany(
            """INSERT OR IGNORE INTO oly_objectives(tournament_id, year, country, country_id,
                   squad_ovr, field_rank, objective, objective_rank, manager_id)
               VALUES(?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        return len(rows)
    finally:
        if own:
            conn.close()


def _olympic_results(conn, year, salt_stats):
    """올림픽 해 평가 재료 — {나라: (목표, 목표rank, 결과키, 결과rank, 약팀 보정용 백분위)}.
    본선 참가국 + 예선 탈락국. 예선 명단에도 못 든 나라(예선 진출 실패)는 없다(호출부가 중립 처리)."""
    from national_manager import country_stage_results, national_objective_in_field
    from constants import MANAGER_NAT_STAGE_RANK
    t = conn.execute("SELECT id FROM intl_tournaments WHERE year=? AND kind='olympic' "
                     "AND status='done' ORDER BY id LIMIT 1", (int(year),)).fetchone()
    if not t:
        return {}
    tid = int(t[0])
    fix_objectives(tid, conn)
    objs = {r["country"]: dict(r) for r in conn.execute(
        "SELECT * FROM oly_objectives WHERE tournament_id=?", (tid,))}
    stages = country_stage_results(conn, tid)
    n_main = max(1, len(objs))
    out, upd = {}, []
    for cn, ob in objs.items():
        key, rr = stages.get(cn, ("조별리그", MANAGER_NAT_STAGE_RANK["조별리그"]))
        out[cn] = (ob["objective"], int(ob["objective_rank"] or 0), key, int(rr),
                   float(ob["field_rank"] or n_main) / n_main)
        upd.append((key, int(rr), tid, cn))
    if upd:
        conn.executemany("UPDATE oly_objectives SET result=?, result_rank=? "
                         "WHERE tournament_id=? AND country=?", upd)
    # 예선 탈락국 — 예선 명단 전력의 연맹 안 순위가 그 연맹 본선 티켓 수 안이면 본선 목표,
    # 아니면 "예선 경쟁력"(떨어져도 목표 미달 아님).
    quals = conn.execute("SELECT id, continent FROM intl_tournaments WHERE year=? "
                         "AND kind='oly_qual' ORDER BY id", (int(year),)).fetchall()
    q_ovr, q_conf, q_tid = {}, {}, {}
    for q in quals:
        for r in conn.execute("SELECT country, ovr FROM oly_squad_meta WHERE tournament_id=?",
                              (q["id"],)):
            q_ovr[r[0]] = float(r[1] or 0.0)
            q_conf[r[0]] = q["continent"] or ""
            q_tid[r[0]] = int(q["id"])
    if q_ovr:
        main_stages = {r[0] for r in conn.execute(
            "SELECT DISTINCT stage FROM intl_matches WHERE tournament_id=?", (tid,))} or _default_stages()
        tickets = {}
        for cn in objs:
            cf = q_conf.get(cn)
            if cf is not None:
                tickets[cf] = tickets.get(cf, 0) + 1
        world = sorted(q_ovr, key=lambda cn: (-q_ovr[cn], cn))
        w_rank = {cn: i for i, cn in enumerate(world, 1)}
        c_rank = {}
        for cf in set(q_conf.values()):
            mem = sorted((cn for cn in q_ovr if q_conf[cn] == cf), key=lambda cn: (-q_ovr[cn], cn))
            for i, cn in enumerate(mem, 1):
                c_rank[cn] = i
        cids = {r[1]: int(r[0]) for r in conn.execute("SELECT id, name FROM countries")}
        ins = []
        for cn in sorted(q_ovr):
            if cn in objs:
                continue
            if c_rank.get(cn, 10 ** 6) <= tickets.get(q_conf[cn], 0):
                oname, orank = national_objective_in_field(w_rank[cn], main_stages)
            else:
                oname, orank = "예선 경쟁력", MANAGER_NAT_STAGE_RANK["예선탈락"]
            rr = MANAGER_NAT_STAGE_RANK["예선탈락"]
            out[cn] = (oname, int(orank), "예선탈락", int(rr), w_rank[cn] / float(len(world)))
            ins.append((q_tid[cn], int(year), cn, cids.get(cn, 0), q_ovr[cn], c_rank.get(cn, 0),
                        oname, int(orank), "예선탈락", int(rr),
                        manager_for_tournament(q_tid[cn], cn, conn)))
        conn.executemany(
            """INSERT OR REPLACE INTO oly_objectives(tournament_id, year, country, country_id,
                   squad_ovr, field_rank, objective, objective_rank, result, result_rank, manager_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""", ins)
    return out


# ══════════════════════════════════════════════════════════════════
# 부임 · 퇴임
# ══════════════════════════════════════════════════════════════════
def _appoint(conn, country_id, manager_id, year, lv, contract_until):
    from constants import manager_career_floor
    conn.execute(
        """INSERT INTO oly_team_managers(country_id, manager_id, start_year, end_year, end_reason,
               role, contract_until, job_level, contract_log)
           VALUES(?,?,?,NULL,'','full',?,?,?)""",
        (country_id, manager_id, int(year), int(contract_until), lv,
         f"{int(year)}:{int(contract_until)}"))
    conn.execute(
        """UPDATE managers SET status=?, jobless_since=NULL, recent_level=?,
                  best_level_year=CASE WHEN ? > COALESCE(career_best_level,0)
                                       THEN ? ELSE best_level_year END,
                  career_best_level=MAX(COALESCE(career_best_level,0), ?),
                  career_floor=?
           WHERE id=?""",
        (STATUS, lv, lv, int(year), lv, manager_career_floor(lv, 0, 0), manager_id))
    _clear_caches()


def _close(conn, link_id, year, reason, make_free):
    conn.execute("UPDATE oly_team_managers SET end_year=?, end_reason=? WHERE id=?",
                 (int(year), reason, link_id))
    if make_free:
        conn.execute(
            "UPDATE managers SET status='free', jobless_since=COALESCE(jobless_since,?) "
            "WHERE id=(SELECT manager_id FROM oly_team_managers WHERE id=?) "
            "AND retired=0 AND status=?", (int(year), link_id, STATUS))
    _clear_caches()


class _Ctx:
    """한 번의 패스(심기·연말 처리)에서 공석 여러 개를 채울 때 같이 쓰는 재료."""

    def __init__(self, conn, year, stats):
        import team_plan as _tplan
        self.conn = conn
        self.year = int(year)
        self.salt = _salt()
        self.stats = stats
        self.taken = set()
        self.nat_cache = {}
        u_mids = _tplan.user_manager_ids(conn)
        by_nat = {}
        for r in conn.execute(
                """SELECT id, nationality, reputation, recent_level, career_best_level,
                          best_level_year, recent_perf, status, jobless_since, birth_year,
                          COALESCE(manager_ability, 0) AS manager_ability
                   FROM managers WHERE retired=0 AND status='free' ORDER BY id"""):
            if int(r["id"]) in u_mids:
                continue
            mm = dict(r)
            mm["_job_kind"] = "free"
            by_nat.setdefault(mm.get("nationality") or "", []).append(mm)
        self.free_by_nat = by_nat


def _banned(conn, country_id, year):
    ban = getattr(C, "MANAGER_REHIRE_BAN_YEARS", {}) or {}
    if not ban:
        return set()
    out = set()
    for r in conn.execute(
            "SELECT manager_id, end_year, end_reason FROM oly_team_managers "
            "WHERE country_id=? AND end_year IS NOT NULL AND end_year > ?",
            (country_id, int(year) - max(ban.values()))):
        b = ban.get(r[2] or "")
        if b and int(year) - int(r[1]) < b:
            out.add(int(r[0]))
    return out


def _fill(ctx, country_id, cname, contract_until):
    """U-23 공석 하나를 채운다. 정해진 쪽(자국/외국)의 무직 감독 → 없으면 그쪽 국적 신인."""
    import ai_lifecycle as al
    from constants import (manager_retire_hard_age, manager_nt_foreign_nat_weight,
                           MANAGER_HIRE_NOISE)
    conn, year = ctx.conn, ctx.year
    lv = job_level(cname)
    # 난수 키에 그 나라의 지금까지 재임 수를 넣는다 — 같은 해에 두 번 뽑는 경우(처음 심은 해 연말 교체)도
    # 서로 다른 난수가 되게(예전엔 같은 연도면 같은 쪽·같은 잡음이 나왔다).
    n_prev = conn.execute("SELECT COUNT(*) FROM oly_team_managers WHERE country_id=?",
                          (country_id,)).fetchone()[0]
    vr = random.Random(zlib.crc32(f"olymgr:{ctx.salt}:{country_id}:{year}:{n_prev}".encode("utf-8")))
    dom = vr.random() < domestic_prob(cname)
    banned = _banned(conn, country_id, year)
    hard = manager_retire_hard_age() - 1

    def _score(mm):
        if int(mm["id"]) in ctx.taken or int(mm["id"]) in banned:
            return None
        if mm.get("birth_year") and year - int(mm["birth_year"]) >= hard:
            return None
        js = mm.get("jobless_since")
        jl = max(0, year - int(js)) if js else 0
        ok, step, bonus = al._manager_job_fit(lv, "nation", mm, year, country=cname,
                                              base_level=float(mm.get("recent_level") or 0.0),
                                              jobless_years=jl)
        if not ok:
            return None
        s = (float(mm.get("reputation") or 0.0) * 0.5 + bonus
             - abs(float(mm.get("recent_level") or 0.0) - lv) * 0.2
             + vr.uniform(0, MANAGER_HIRE_NOISE))
        return (s, step, mm)

    pool = []
    if dom:
        pool = [x for x in (_score(m) for m in ctx.free_by_nat.get(cname, [])) if x]
        pool.sort(key=lambda x: (-x[0], int(x[2]["id"])))
    else:
        # 외국 — 국적 순서를 감독 시장과 같은 가중치(지역·언어·수출력·수준)로 뽑고, 앞 국적부터
        # 맞는 후보를 모은다(성인 대표팀 외국 쪽 정렬과 같은 효과: 국적 순서 → 점수).
        nats = sorted(n for n in ctx.free_by_nat if n and n != cname)
        wl = [(n, manager_nt_foreign_nat_weight(cname, n, lv)) for n in nats]
        wl = [(n, w) for n, w in wl if w > 0]
        while wl and len(pool) < 24:
            tot = sum(w for _n, w in wl)
            x = vr.random() * tot
            for i, (n, w) in enumerate(wl):
                x -= w
                if x <= 0:
                    break
            n = wl.pop(i)[0]
            got = [x for x in (_score(m) for m in ctx.free_by_nat.get(n, [])) if x]
            got.sort(key=lambda x: (-x[0], int(x[2]["id"])))
            pool.extend(got)
    pick = None
    for s, step, mm in pool[:24]:
        if step < 1.0 and vr.random() >= step:
            continue
        pick = mm
        break
    if pick is None and pool:
        pick = pool[0][2]
    if pick is not None:
        ctx.taken.add(int(pick["id"]))
        _appoint(conn, country_id, int(pick["id"]), year, lv, contract_until)
        ctx.stats["appointed_free"] = ctx.stats.get("appointed_free", 0) + 1
        ctx.stats["dom" if (pick.get("nationality") or "") == cname else "frn"] = \
            ctx.stats.get("dom" if (pick.get("nationality") or "") == cname else "frn", 0) + 1
        return int(pick["id"])
    # 맞는 무직 감독이 없다 → 정해진 쪽 국적의 신인을 만든다.
    from database import (build_manager_row, MANAGER_INSERT_SQL, assign_manager_codes,
                          seed_manager_nationality)
    nat = cname
    if not dom:
        nat = seed_manager_nationality(conn, cname, lv, ctx.salt, f"oly:{country_id}:{year}", 0.0,
                                       manager_nt_foreign_nat_weight, ctx.nat_cache) or cname
    row = build_manager_row(vr, nat, year, age_range=tuple(C.OLY_MGR_ROOKIE_AGE))
    base = conn.execute("SELECT COALESCE(MAX(id),0) FROM managers").fetchone()[0]
    conn.execute(MANAGER_INSERT_SQL, row)
    assign_manager_codes(conn)
    new = conn.execute("SELECT id FROM managers WHERE id > ? ORDER BY id LIMIT 1", (base,)).fetchone()
    if not new:
        return 0
    _appoint(conn, country_id, int(new[0]), year, lv, contract_until)
    ctx.stats["created"] = ctx.stats.get("created", 0) + 1
    ctx.stats["dom" if nat == cname else "frn"] = ctx.stats.get("dom" if nat == cname else "frn", 0) + 1
    return int(new[0])


def _finish_pass(conn, year):
    try:
        import manager_ability
        manager_ability.assign_missing_manager_abilities(conn, year=year, mode="rookie")
    except Exception as e:
        print(f"[U23-MGR] 감독 능력치 채움 실패(계속 진행): {e}")
    conn.commit()
    _clear_caches()


def ensure_seeded(year):
    """감독이 없는 나라에 U-23 감독을 세운다(처음 켰을 때·새 게임). 올림픽 해 대회 전이면
    계약은 올해 올림픽까지, 아니면 다음 올림픽까지. 반환: 세운 수."""
    if not enabled():
        return 0
    _clear_caches()          # 새 게임(같은 프로세스)에서 대회 id가 다시 쓰이므로 예선마다 비운다
    conn = get_conn()
    try:
        links = _open_links(conn)
        todo = [(cid, cn) for cid, cn in _countries(conn) if cid not in links]
        if not todo:
            return 0
        y = int(year)
        cu = y if (_is_olympic_year(y) and not _finals_done(conn, y)) else next_olympic_year(y)
        stats = {}
        ctx = _Ctx(conn, y, stats)
        for cid, cn in todo:
            _fill(ctx, cid, cn, cu)
        _finish_pass(conn, y)
        print(f"[U23-MGR] {y} U-23 감독 {len(todo)}개국 선임 (계약 {cu}까지) {stats}")
        return len(todo)
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 연말 처리 — game_engine 시즌 전환(클럽 감독 시장 뒤)에서 한 번
# ══════════════════════════════════════════════════════════════════
LAST_PASS = {}      # 마지막 연말 처리 통계(QA가 읽는다 — 게임 로직은 안 읽음)


def on_season_end(year):
    """year = 방금 끝난 시즌. 순서: ① 다른 시장이 데려간 감독 정리 → ② 올림픽 해면 평가·재계약,
    아니면 성적 부진 경질 → ③ 나이 은퇴 → ④ 공석 채우기(계약 = 다음 올림픽 해까지)."""
    global LAST_PASS
    if not enabled():
        return {}
    from constants import (national_result_rep, national_sack_pressure, manager_retire_prob,
                           MANAGER_REP_RECENT_ALPHA,
                           MANAGER_NAT_STAGE_RANK)
    from national_manager import nat_renew_prob
    y = int(year)
    stats = {"year": y, "reconciled": 0, "evaluated": 0, "renewed": 0, "contract_end": 0,
             "sacked_offyear": 0, "retired": 0, "promo_candidates": 0, "vacancies": 0}
    conn = get_conn()
    try:
        salt = _salt()
        names = dict(_countries(conn))
        links = _open_links(conn)
        # ① 정리 — 클럽·성인 대표팀 시장이 데려갔거나(승진) 다른 경로로 은퇴한 감독.
        for cid, lk in list(links.items()):
            st = lk.get("m_status") or ""
            if st == STATUS and not int(lk.get("retired") or 0):
                continue
            reason = ("retired" if (st == "retired" or int(lk.get("retired") or 0)) else
                      "promoted" if st in ("club", "nation") else "left")
            _close(conn, lk["id"], y, reason, make_free=False)
            links.pop(cid)
            stats["reconciled"] += 1
            stats[reason] = stats.get(reason, 0) + 1
        # 대표팀이 없는 나라(가상 국가)의 재임은 닫는다(정상 흐름에선 없음).
        for cid in [c for c in links if c not in names]:
            _close(conn, links[cid]["id"], y, "left", make_free=True)
            links.pop(cid)
        mgr_upd, ten_upd = [], []
        ends = []          # (link_id, reason)
        if _is_olympic_year(y):
            res = _olympic_results(conn, y, stats) if _finals_done(conn, y) else {}
            for cid in sorted(links):
                lk = links[cid]
                cn = names[cid]
                if int(lk.get("eval_year") or 0) == y or not _coached_this_year(lk, y):
                    continue        # 이미 이 해 평가를 받았거나(같은 해 두 번 처리) 대회 뒤 연말에 막 부임
                if cn in res:
                    oname, orank, rkey, rrank, pct = res[cn]
                else:   # 예선 진출 실패(또는 대회가 없던 해) — 중립
                    oname, orank, rkey, rrank, pct = ("예선 경쟁력", MANAGER_NAT_STAGE_RANK["예선탈락"],
                                                      "예선탈락", MANAGER_NAT_STAGE_RANK["예선탈락"], 1.0)
                stats["evaluated"] += 1
                rng = random.Random(zlib.crc32(f"olyeval:{salt}:{cid}:{y}".encode("utf-8")))
                if res:
                    delta = national_result_rep(rkey, orank, rrank, pct, is_world_cup=False) \
                        * C.OLY_MGR_REP_MULT
                    rep = max(0.0, min(100.0, float(lk.get("reputation") or 0.0) + delta))
                    perf = max(-1.0, min(1.0, (rrank - orank) / 40.0))
                    rperf = ((1.0 - MANAGER_REP_RECENT_ALPHA) * float(lk.get("recent_perf") or 0.0)
                             + MANAGER_REP_RECENT_ALPHA * perf)
                    mgr_upd.append((rep, rperf, lk["manager_id"]))
                p_promo = promo_prob(orank, rrank, rkey) if res else 0.0
                promo_until = int(lk.get("promo_until") or 0)
                if p_promo > 0 and rng.random() < p_promo:
                    promo_until = y + C.OLY_MGR_PROMO_YEARS
                    stats["promo_candidates"] += 1
                ten_upd.append((rrank, rrank, y, orank, rrank, promo_until, lk["id"]))
                if int(lk.get("contract_until") or 0) <= y:
                    if rng.random() < nat_renew_prob(orank, rrank):
                        nu = next_olympic_year(y)
                        conn.execute("UPDATE oly_team_managers SET contract_until=?, "
                                     "contract_log=COALESCE(contract_log,'')||? WHERE id=?",
                                     (nu, f";{y}:{nu}", lk["id"]))
                        stats["renewed"] += 1
                    else:
                        ends.append((lk["id"], "contract_end"))
                        stats["contract_end"] += 1
        else:
            # 비올림픽 해 — 직전 올림픽에서 목표에 못 미치고도 남은 감독만 경질 판정.
            for cid in sorted(links):
                lk = links[cid]
                ey = int(lk.get("eval_year") or 0)
                k = y - ey
                if not ey or not (1 <= k <= len(C.OLY_MGR_OFFYEAR_SACK_MULT)):
                    continue
                orank, rrank = int(lk.get("eval_orank") or 0), int(lk.get("eval_rrank") or 0)
                if rrank >= orank:
                    continue
                tenure = max(0, y - int(lk.get("start_year") or y))
                p = (national_sack_pressure(orank, rrank, float(lk.get("reputation") or 0.0), tenure)
                     * C.OLY_MGR_OFFYEAR_SACK_MULT[k - 1])
                rng = random.Random(zlib.crc32(f"olysack:{salt}:{cid}:{y}".encode("utf-8")))
                if rng.random() < p:
                    ends.append((lk["id"], "sacked"))
                    stats["sacked_offyear"] += 1
        if mgr_upd:
            conn.executemany("UPDATE managers SET reputation=?, recent_perf=? WHERE id=?", mgr_upd)
        if ten_upd:
            conn.executemany(
                "UPDATE oly_team_managers SET tournaments=COALESCE(tournaments,0)+1, "
                "best_rank=CASE WHEN COALESCE(best_rank,0) < ? THEN ? ELSE best_rank END, "
                "eval_year=?, eval_orank=?, eval_rrank=?, promo_until=? WHERE id=?", ten_upd)
        ended = {lid for lid, _r in ends}
        for lid, reason in ends:
            _close(conn, lid, y, reason, make_free=True)
        # ③ 나이 은퇴 — 클럽 시장은 U-23 감독을 은퇴시키지 않는다(재임이 열린 채 남지 않게 여기서).
        for cid in sorted(links):
            lk = links[cid]
            if lk["id"] in ended or not _coached_this_year(lk, y):
                continue            # 막 부임한 감독은 그 해 은퇴 판정에서 뺀다(성인 대표팀과 같은 규칙)
            age = y - int(lk.get("birth_year") or y)
            p_ret = manager_retire_prob(age)
            if p_ret <= 0:
                continue
            rr = random.Random(zlib.crc32(f"olyret:{salt}:{lk['manager_id']}:{y}".encode("utf-8")))
            if rr.random() < p_ret:
                _close(conn, lk["id"], y, "retired", make_free=False)
                conn.execute("UPDATE managers SET retired=1, status='retired', retired_year=? "
                             "WHERE id=?", (y, lk["manager_id"]))
                stats["retired"] += 1
        conn.commit()
        # ④ 공석 채우기 — 계약은 다음 올림픽 해까지.
        links = _open_links(conn)
        todo = [(cid, cn) for cid, cn in _countries(conn) if cid not in links]
        stats["vacancies"] = len(todo)
        if todo:
            ctx = _Ctx(conn, y, stats)
            cu = next_olympic_year(y)
            for cid, cn in todo:
                _fill(ctx, cid, cn, cu)
        _finish_pass(conn, y)
    finally:
        conn.close()
    _bonus_cache.clear()
    LAST_PASS = stats
    print(f"[U23-MGR] {y} 연말 {stats}")
    return stats


# ══════════════════════════════════════════════════════════════════
# 승진 후보 — 클럽·성인 대표팀 시장이 읽는다(없으면 빈 값 — 시장 동작이 예전과 같다)
# ══════════════════════════════════════════════════════════════════
def promo_candidate_ids(conn, year):
    """이번 시장에서 후보로 볼 U-23 감독 id 집합(승진 후보 기간 안). 꺼져 있거나 없으면 빈 집합."""
    if not enabled():
        return set()
    try:
        return {int(r[0]) for r in conn.execute(
            "SELECT o.manager_id FROM oly_team_managers o JOIN managers m ON m.id = o.manager_id "
            "WHERE o.end_year IS NULL AND o.promo_until >= ? AND m.status=? AND m.retired=0",
            (int(year), STATUS))}
    except Exception:
        return set()


def promo_candidate_rows(conn, year):
    """성인 대표팀 공석 채우기용 후보 행(national_manager._fill_vacancy의 조회와 같은 키).
    현직(클럽 현직과 같은 2순위)으로 넣는다 — status='club'로 보이게 해서 무직 우선 규칙을 지킨다."""
    ids = promo_candidate_ids(conn, year)
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    out = []
    for r in conn.execute(
            f"""SELECT m.id, m.nationality, m.reputation, m.recent_level,
                       m.career_best_level, m.best_level_year, m.recent_perf,
                       m.status, m.jobless_since, m.birth_year,
                       COALESCE(m.manager_ability, 0) AS manager_ability
                FROM managers m WHERE m.id IN ({ph}) ORDER BY m.id""", sorted(ids)):
        d = dict(r)
        d["status"] = "club"
        out.append(d)
    return out