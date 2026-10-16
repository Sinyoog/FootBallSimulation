# -*- coding: utf-8 -*-
"""club_owner.py — 구단 구매(구단주) 기본 세팅. [2026-10 신설, 신민용 확정]

신민용 확정 방향:
  · 가격 = 그 해 선수단 추정 몸값 합 × CLUB_PRICE_MULT(1.5) — 강한 팀·큰 리그일수록 비싸고 해마다 바뀐다.
  · 현역 선수·은퇴 후 모두 살 수 있다. 사고팔기는 오프시즌(OWNER_WINDOW_WEEKS)에만.
  · [2026-10 변경, 신민용 확정] 여러 구단을 동시에 소유할 수 있다(멀티클럽 오너십). 구단마다 예산·감독
    선임·매입·매각 기록이 따로다 — 개인 자산(my_player.total_assets)에서 원하는 구단 예산으로 넣고,
    구단 예산끼리는 절대 자동으로 합치거나 옮기지 않는다. 같은 대회 소유 구단 간 거래 제한 같은
    규칙은 아직 없다(필요하면 나중에 별도로).
  · 역할 구분 — "구단주일 때는 감독만 선임, 감독일 때는 선수 영입(금액 한도 있음)".
      구단주: 구단 예산에 돈을 넣고(owner_invest), 감독을 선임한다(owner_appoint_manager).
              선수 영입은 직접 못 한다.
      사용자 감독: 선수 영입은 구단 예산 한도 안에서만 — club_budget_spend가 그 한도 검사 창구다.
              (감독 모드 화면·영입 흐름은 아직 없다. 감독 모드를 만들 때 이 함수로 붙인다.)
  · 이번 범위는 함수·저장만(UI 없음).

[게임 진행 영향] 구단을 사지 않으면 지금과 100% 같다 — 소유 행이 없으면 아래 연결부
(ai_lifecycle._manager_turnover의 구단주 팀 분기, run_ai_offseason의 선임 적용)가 전부 빈
집합이라 아무것도 안 바뀐다. 난수는 어디에서도 쓰지 않는다.

[구단주 팀의 감독 — AI 시장과의 관계]
  · AI 시장은 구단주 팀 감독을 경질하지 않는다(경질·교체는 구단주 몫). 계약이 끝나면
    OWNER_AUTO_RENEW_YEARS년 자동 재계약 — 바꾸고 싶으면 구단주가 새 감독을 선임한다.
  · 은퇴·대표팀 이동·다른 클럽 이직으로 자리가 비면 예전처럼 AI 시장이 채운다(구단주는 이후 교체 가능).
  · 구단주 선임은 바로 바뀌지 않고 "예약"이다 — 연도 전환의 감독 시장(_manager_turnover)이 그 시즌을
    마친 기존 감독을 평가한 직후에 적용한다(apply_owner_appointments). 그래야 그 시즌 실적·평판이
    시즌을 실제로 지휘한 감독에게 쌓이고, 새 감독은 다음 시즌부터 지휘한다(AI 시장 부임과 같은 규칙).

금액 단위는 게임 공통 천원(fmt_money와 같다)."""
import sqlite3

from constants import INTL_CALLUP_WEEK, INTL_OFFSEASON_WEEK_END

CLUB_PRICE_MULT = 1.5                 # 선수단 몸값 합 × 1.5
CLUB_PRICE_FLOOR = 10_000             # 최저 가격 1천만원(선수단 몸값이 거의 0인 팀 대비)
OWNER_WINDOW_WEEKS = (INTL_CALLUP_WEEK, INTL_OFFSEASON_WEEK_END)   # 45~52주 — 승강 PO 다음 주부터 연말
OWNER_AUTO_RENEW_YEARS = 2            # 구단주 팀 감독 계약 만료 시 자동 재계약 기간
OWNER_CONTRACT_YEARS = (1, 5)         # 구단주가 정할 수 있는 감독 계약 기간

DDL = (
    """CREATE TABLE IF NOT EXISTS club_ownership(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id INTEGER, start_year INTEGER, start_week INTEGER,
        end_year INTEGER, end_week INTEGER,
        price_paid INTEGER DEFAULT 0, sale_price INTEGER DEFAULT 0,
        pending_manager_id INTEGER, pending_years INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS club_budget(
        team_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS club_budget_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id INTEGER, year INTEGER, week INTEGER,
        kind TEXT, amount INTEGER, note TEXT DEFAULT '')""",
)
RESET_TABLES = ("club_ownership", "club_budget", "club_budget_log")


def create_tables(c):
    for s in DDL:
        c.execute(s)


def _with_conn(conn):
    if conn is not None:
        return conn, False
    from database import get_conn
    return get_conn(), True


def _clock(conn):
    r = conn.execute("SELECT current_year, current_week FROM season_state WHERE id=1").fetchone()
    return (int(r[0]), int(r[1])) if r else (None, None)


def in_owner_window(week):
    return week is not None and OWNER_WINDOW_WEEKS[0] <= int(week) <= OWNER_WINDOW_WEEKS[1]


def _assets(conn):
    r = conn.execute("SELECT COALESCE(total_assets, 0) FROM my_player WHERE id=1").fetchone()
    return int(r[0]) if r else 0


def _log(text, year=None, week=None):
    try:
        from game_engine import add_log
        add_log(text, "salary", year=year, week=week)
    except Exception:
        pass


def _fmt(v):
    try:
        from game_engine import fmt_money
        return fmt_money(int(v))
    except Exception:
        return f"{int(v):,}천원"


def _fail(reason, **kw):
    return {"ok": False, "reason": reason, **kw}


# ════════════════════════════════════════════════════════════════
# 조회
# ════════════════════════════════════════════════════════════════
def current_ownership(team_id, conn=None):
    """team_id를 지금 소유 중이면 그 소유 행(dict), 아니면 None."""
    conn, own = _with_conn(conn)
    try:
        r = conn.execute("SELECT * FROM club_ownership WHERE team_id=? AND end_year IS NULL "
                         "ORDER BY id DESC LIMIT 1", (team_id,)).fetchone()
        return dict(r) if r else None
    except sqlite3.OperationalError:
        return None
    finally:
        if own:
            conn.close()


def current_ownerships(conn=None):
    """지금 소유 중인 구단 행 전부(구매 순)."""
    conn, own = _with_conn(conn)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM club_ownership WHERE end_year IS NULL ORDER BY id")]
    except sqlite3.OperationalError:
        return []
    finally:
        if own:
            conn.close()


def owned_team_ids(conn=None):
    """구단주 팀 집합 — 감독 시장이 경질·재계약을 건너뛸 때 쓴다. 소유가 없으면 빈 집합."""
    conn, own = _with_conn(conn)
    try:
        return {int(r[0]) for r in conn.execute(
            "SELECT team_id FROM club_ownership WHERE end_year IS NULL")}
    except sqlite3.OperationalError:
        return set()
    finally:
        if own:
            conn.close()


def club_price(team_id, year=None, conn=None):
    """구단 가격(천원) = 지금 선수단(AI 선수 + 소속이면 내 선수) 추정 몸값 합 × CLUB_PRICE_MULT.
    몸값은 재정 화면 "최고/최저 몸값"과 같은 economy.estimate_transfer_fee(난수 없음)."""
    from economy import estimate_transfer_fee
    from constants import get_country_league_grade
    conn, own = _with_conn(conn)
    try:
        if year is None:
            year = _clock(conn)[0]
        t = conn.execute(
            """SELECT t.name AS tname, cn.name AS cname, cn.grade AS ngrade, l.tier AS tier
               FROM teams t JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id WHERE t.id=?""", (team_id,)).fetchone()
        if not t:
            return None
        try:
            grade = get_country_league_grade(t["cname"], t["ngrade"])
        except Exception:
            grade = t["ngrade"]
        squad = [(r[0], r[1], r[2]) for r in conn.execute(
            "SELECT ovr, age, position FROM ai_players WHERE team_id=?", (team_id,))]
        me = conn.execute("SELECT ovr, age, position, current_team_id FROM my_player "
                          "WHERE id=1").fetchone()
        if me and me["current_team_id"] == team_id:
            squad.append((me["ovr"], me["age"], me["position"]))
        total = 0
        for ovr, age, pos in squad:
            if not ovr:
                continue
            try:
                total += int(estimate_transfer_fee(grade, t["tier"] or 1, ovr, country=t["cname"],
                                                   team_name=t["tname"], position=pos or None,
                                                   age=age, year=year) or 0)
            except Exception:
                continue
        return max(CLUB_PRICE_FLOOR, int(round(total * CLUB_PRICE_MULT)))
    finally:
        if own:
            conn.close()


def club_budget(team_id, conn=None):
    conn, own = _with_conn(conn)
    try:
        r = conn.execute("SELECT balance FROM club_budget WHERE team_id=?", (team_id,)).fetchone()
        return int(r[0]) if r else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        if own:
            conn.close()


def _budget_add(conn, team_id, amount, kind, note, year, week):
    conn.execute("INSERT OR IGNORE INTO club_budget(team_id, balance) VALUES(?, 0)", (team_id,))
    conn.execute("UPDATE club_budget SET balance = balance + ? WHERE team_id=?", (int(amount), team_id))
    conn.execute("INSERT INTO club_budget_log(team_id, year, week, kind, amount, note) "
                 "VALUES(?,?,?,?,?,?)", (team_id, year, week, kind, int(amount), note))


# ════════════════════════════════════════════════════════════════
# 구단주 행동 — 반환은 {"ok": bool, "reason": 실패 사유, ...}
# ════════════════════════════════════════════════════════════════
def buy_club(team_id, conn=None):
    """오프시즌에 구단을 산다. 내 총자산에서 가격을 뺀다. 여러 구단 소유 가능, 산하(B)팀·군팀 불가."""
    conn, own = _with_conn(conn)
    try:
        year, week = _clock(conn)
        if not in_owner_window(week):
            return _fail(f"구단 매매는 오프시즌({OWNER_WINDOW_WEEKS[0]}~{OWNER_WINDOW_WEEKS[1]}주)에만 가능")
        if current_ownership(team_id, conn):
            return _fail("이미 소유한 구단")
        t = conn.execute("SELECT id, name, parent_team_id FROM teams WHERE id=?", (team_id,)).fetchone()
        if not t:
            return _fail("없는 팀")
        if t["parent_team_id"]:
            return _fail("산하팀은 따로 살 수 없음")
        try:
            from military_service import is_military_team
            if is_military_team(conn, team_id):
                return _fail("군팀은 살 수 없음")
        except ImportError:
            pass
        price = club_price(team_id, year, conn)
        assets = _assets(conn)
        if price is None:
            return _fail("가격 계산 불가")
        if assets < price:
            return _fail("자산 부족", price=price, assets=assets)
        conn.execute("UPDATE my_player SET total_assets = total_assets - ? WHERE id=1", (price,))
        conn.execute("INSERT INTO club_ownership(team_id, start_year, start_week, price_paid) "
                     "VALUES(?,?,?,?)", (team_id, year, week, price))
        conn.execute("INSERT OR IGNORE INTO club_budget(team_id, balance) VALUES(?, 0)", (team_id,))
        conn.commit()
        _log(f"🏟️ 구단 구매: {t['name']}  -{_fmt(price)}  (총자산: {_fmt(assets - price)})", year, week)
        return {"ok": True, "price": price, "assets": assets - price}
    finally:
        if own:
            conn.close()


def sell_club(team_id, conn=None):
    """오프시즌에 소유 구단 하나를 지금 가격으로 판다. 그 구단의 남은 예산도 돌려받는다(다른 소유 구단
    예산은 그대로). 그 구단에 예약된 감독 선임은 취소."""
    conn, own = _with_conn(conn)
    try:
        year, week = _clock(conn)
        if not in_owner_window(week):
            return _fail(f"구단 매매는 오프시즌({OWNER_WINDOW_WEEKS[0]}~{OWNER_WINDOW_WEEKS[1]}주)에만 가능")
        o = current_ownership(team_id, conn)
        if not o:
            return _fail("소유한 구단이 아님")
        tid = o["team_id"]
        price = club_price(tid, year, conn) or 0
        left = club_budget(tid, conn)
        if left:
            _budget_add(conn, tid, -left, "withdraw", "매각 정산", year, week)
        conn.execute("UPDATE my_player SET total_assets = total_assets + ? WHERE id=1", (price + left,))
        conn.execute("UPDATE club_ownership SET end_year=?, end_week=?, sale_price=?, "
                     "pending_manager_id=NULL, pending_years=0 WHERE id=?", (year, week, price, o["id"]))
        conn.commit()
        name = conn.execute("SELECT name FROM teams WHERE id=?", (tid,)).fetchone()
        _log(f"🏟️ 구단 매각: {name[0] if name else tid}  +{_fmt(price)}"
             + (f" · 남은 예산 +{_fmt(left)}" if left else ""), year, week)
        return {"ok": True, "price": price, "budget_returned": left}
    finally:
        if own:
            conn.close()


def owner_invest(team_id, amount, conn=None):
    """구단주가 내 총자산에서 team_id 구단 예산으로 돈을 넣는다(현역 선수여도 가능, 기간 제한 없음).
    넣은 돈은 그 구단 예산에만 쌓인다."""
    conn, own = _with_conn(conn)
    try:
        amount = int(amount)
        o = current_ownership(team_id, conn)
        if not o:
            return _fail("소유한 구단이 아님")
        if amount <= 0:
            return _fail("금액은 0보다 커야 함")
        assets = _assets(conn)
        if assets < amount:
            return _fail("자산 부족", assets=assets)
        year, week = _clock(conn)
        conn.execute("UPDATE my_player SET total_assets = total_assets - ? WHERE id=1", (amount,))
        _budget_add(conn, o["team_id"], amount, "invest", "구단주 투자", year, week)
        conn.commit()
        _log(f"🏟️ 구단 예산 투자  -{_fmt(amount)}  (구단 예산: {_fmt(club_budget(o['team_id'], conn))})",
             year, week)
        return {"ok": True, "balance": club_budget(o["team_id"], conn)}
    finally:
        if own:
            conn.close()


def club_budget_spend(team_id, amount, note="", conn=None):
    """구단 예산에서 지출 — 사용자 감독의 선수 영입 등 "금액 한도" 검사 창구. 잔액이 모자라면 실패하고
    아무것도 바꾸지 않는다. (구단주는 이 함수로 선수를 사지 않는다 — 구단주는 감독 선임만.)"""
    conn, own = _with_conn(conn)
    try:
        amount = int(amount)
        if amount <= 0:
            return _fail("금액은 0보다 커야 함")
        bal = club_budget(team_id, conn)
        if bal < amount:
            return _fail("구단 예산 한도 초과", balance=bal)
        year, week = _clock(conn)
        _budget_add(conn, team_id, -amount, "spend", note, year, week)
        conn.commit()
        return {"ok": True, "balance": bal - amount}
    finally:
        if own:
            conn.close()


def owner_appoint_manager(team_id, manager_id, contract_years=3, conn=None):
    """구단주가 team_id 구단에 무직 AI 감독을 선임(예약)한다 — 오프시즌에만. 연도 전환 감독 시장 직후
    적용된다. 같은 구단에 다시 부르면 예약을 바꾼다. 한 감독은 소유 구단 하나에만 예약할 수 있다.
    사용자 본인을 감독으로 앉히는 건 감독 모드(team_plan.appoint_user_manager) 몫."""
    conn, own = _with_conn(conn)
    try:
        year, week = _clock(conn)
        if not in_owner_window(week):
            return _fail(f"감독 선임은 오프시즌({OWNER_WINDOW_WEEKS[0]}~{OWNER_WINDOW_WEEKS[1]}주)에만 가능")
        o = current_ownership(team_id, conn)
        if not o:
            return _fail("소유한 구단이 아님")
        lo, hi = OWNER_CONTRACT_YEARS
        if not lo <= int(contract_years) <= hi:
            return _fail(f"계약 기간은 {lo}~{hi}년")
        m = conn.execute("SELECT id, name, retired, status, COALESCE(is_user,0) AS is_user "
                         "FROM managers WHERE id=?", (manager_id,)).fetchone()
        if not m or m["retired"]:
            return _fail("없는 감독이거나 은퇴한 감독")
        if m["is_user"]:
            return _fail("사용자 감독은 감독 모드에서 부임")
        if (m["status"] or "") != "free":
            return _fail("무직 감독만 선임 가능")
        if conn.execute("SELECT 1 FROM club_ownership WHERE end_year IS NULL AND pending_manager_id=? "
                        "AND team_id<>?", (manager_id, team_id)).fetchone():
            return _fail("이미 다른 소유 구단에 예약된 감독")
        conn.execute("UPDATE club_ownership SET pending_manager_id=?, pending_years=? WHERE id=?",
                     (manager_id, int(contract_years), o["id"]))
        conn.commit()
        _log(f"🏟️ 감독 선임 예약: {m['name']} ({int(contract_years)}년) — 시즌 종료 후 부임", year, week)
        return {"ok": True}
    finally:
        if own:
            conn.close()


def owner_cancel_appointment(team_id, conn=None):
    conn, own = _with_conn(conn)
    try:
        o = current_ownership(team_id, conn)
        if not o or not o.get("pending_manager_id"):
            return _fail("예약된 선임 없음")
        conn.execute("UPDATE club_ownership SET pending_manager_id=NULL, pending_years=0 WHERE id=?",
                     (o["id"],))
        conn.commit()
        return {"ok": True}
    finally:
        if own:
            conn.close()


# ════════════════════════════════════════════════════════════════
# 연도 전환 연결부 — ai_lifecycle.run_ai_offseason이 _manager_turnover 직후 부른다
# ════════════════════════════════════════════════════════════════
def apply_owner_appointments(c, year):
    """예약된 구단주 선임을 적용한다. 반환: 감독이 바뀐 team_id 집합(포메이션 재검토 대상).
    예약이 없으면 빈 집합 — 아무것도 안 바꾼다. 감독이 그 사이 다른 자리에 가거나 은퇴했으면 취소."""
    try:
        rows = c.execute("SELECT id, team_id, pending_manager_id, pending_years FROM club_ownership "
                         "WHERE end_year IS NULL AND pending_manager_id IS NOT NULL ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return set()
    if not rows:
        return set()
    from constants import manager_job_level, get_country_league_grade
    from data.prestige_clubs import prestige_level
    changed = set()
    for o in rows:
        tid, mid, yrs = int(o[1]), int(o[2]), int(o[3] or 3)
        c.execute("UPDATE club_ownership SET pending_manager_id=NULL, pending_years=0 WHERE id=?", (o[0],))
        m = c.execute("SELECT name, retired, status FROM managers WHERE id=?", (mid,)).fetchone()
        if not m or m[1] or (m[2] or "") != "free":
            _log(f"🏟️ 감독 선임 취소: {m[0] if m else mid} — 그 사이 다른 자리로 가거나 은퇴", year)
            continue
        t = c.execute("""SELECT t.name, cn.name, l.tier FROM teams t JOIN leagues l ON l.id=t.league_id
                         JOIN countries cn ON cn.id=l.country_id WHERE t.id=?""", (tid,)).fetchone()
        if not t:
            continue
        lv = manager_job_level(get_country_league_grade(t[1]), t[2], prestige_level(t[1], t[0]) or 0)
        for lk in c.execute("SELECT id, manager_id FROM team_managers WHERE team_id=? AND end_year IS NULL "
                            "AND COALESCE(job_kind,'club')='club'", (tid,)).fetchall():
            c.execute("UPDATE team_managers SET end_year=?, end_reason='sacked' WHERE id=?", (year, lk[0]))
            c.execute("UPDATE managers SET status='free', contract_until=0, "
                      "jobless_since=COALESCE(jobless_since, ?) WHERE id=? AND retired=0", (year, lk[1]))
        until = year + yrs
        c.execute("""INSERT INTO team_managers(team_id, manager_id, start_year, end_year,
                                               job_kind, country_id, job_level, contract_log)
                     VALUES(?,?,?,NULL,'club',NULL,?,?)""", (tid, mid, year, lv, f"{year}:{until}"))
        c.execute("UPDATE managers SET contract_until=?, status='club', jobless_since=NULL, "
                  "best_level_year=CASE WHEN ? > COALESCE(career_best_level,0) THEN ? ELSE best_level_year END, "
                  "career_best_level=MAX(COALESCE(career_best_level,0), ?), recent_level=?, "
                  "clubs_managed=COALESCE(clubs_managed,0)+1 WHERE id=?", (until, lv, year, lv, lv, mid))
        c.execute("UPDATE teams SET tactic_tendency=(SELECT style_attack FROM managers WHERE id=?) "
                  "WHERE id=?", (mid, tid))
        changed.add(tid)
        _log(f"🏟️ 감독 부임: {m[0]} → {t[0]} ({yrs}년)", year)
    return changed