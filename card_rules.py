# -*- coding: utf-8 -*-
"""card_rules.py — 대회별 경고 누적·출전정지 규칙 (2026-10 신설, 신민용 확정).

[배경] 예전엔 국가대표 카드가 "월드컵 예선" / "그 밖의 국가대표 전부(친선전 포함)" 두 묶음뿐이었고,
모든 대회가 경고 5장이었으며 본선 도중 누적 초기화가 없었다. 이 모듈이 대회별 공식 규정을 표로 들고,
내 경기 직전에 (1) 끝난 대회의 징계 정리, (2) 단계 경계에서 누적 경고 초기화, (3) 이번 경기 카드 판정
기준(몇 장째 정지인지)을 정한다. 경고 누적(카운터)과 출전정지(결장 카운터)는 따로 관리한다 —
누적을 지워도 이미 확정된 정지는 그대로 소화한다.

[확인한 공식 근거]
  · FIFA 징계규정(FDC 2023) 67조: 같은 대회 서로 다른 2경기 경고 → 다음 경기 정지. 경고는 다른 대회로
    넘어가지 않음. 69조: 정지는 같은 대회 다음 라운드로 이월, 대회에서 못 채운 퇴장 정지는 대표팀 다음
    공식 경기로, 경고누적 정지는 다른 대회로 이월 금지, 친선전 정지는 다음 친선전.
  · 월드컵 2026 본선: 조별리그 후·8강 후 단일 경고 소멸. 예선→본선: 경고·경고누적 정지·2차 경고 퇴장
    정지는 소멸, 그 밖의 퇴장 정지는 이월(FIFA Council 2026-04-28).
  · 유로(UEFA 54조): 예선 3장(이후 5·7…), 본선 2장(이후 4…). 예선 조별리그 종료·예선 PO 종료·본선
    8강 종료 때 소멸, 예선 경고·경고누적 정지는 본선으로 안 넘어감.
  · UEFA 네이션스리그(60조): 2장(이후 짝수), 8강 종료·팀 탈락 때 소멸.
  · 챔스(UEFA 63조): 3장(이후 홀수), 8강 종료 때 소멸, 경고누적 정지는 시즌 끝에 소멸.
  · 아시안컵(AFC 2023 규정 52조): 예선 2장, 예선 라운드 사이·본선 시작 때 단일 경고 소멸,
    예선 경고누적 정지는 본선에 안 넘어감, 본선 단일 경고는 4강으로 안 넘어감(=8강 후 소멸).
  · AFCON(CAF 42조): 2장, 예선 경고는 본선에서 안 셈, 조별리그 종료 때 소멸.
  · 골드컵(Concacaf 2025 23.12): 2장, 조별리그 종료·8강 종료 때 소멸, 이전 대회의 퇴장 정지는 이월.
  · 코파 아메리카(2024 규정 보도): 2장, 조별리그 경고는 8강부터 안 셈.
[공식 규정을 못 확인한 대회] 남북미 대륙컵(게임 고유 대회), 그 밖의 지역컵(EAFF·AFF·SAFF·CAFA·WAFF·
  북아프리카·WAFU·중앙아프리카·CECAFA·COSAFA·오세아니아컵), 올림픽 — 기준 장수·대회 중 초기화는 예전
  그대로(5장, 초기화 없음 / 올림픽은 olympics.py 기존 처리) 두고, 확인된 일반 원칙(대회끼리 경고 분리,
  끝난 대회 징계 정리)만 적용한다.
"""
import json

# ── 대회 묶음(내 선수 카드 카운터 한 벌 = 묶음 하나) ──────────────────────
# wc_qual·oly는 예전 컬럼을 그대로 쓴다. intl_suspension(예전 "그 밖의 국가대표 전부" 결장 카운터)은
# 이제 "다음 공식 국가대표 경기에서 소화할 이월 정지" 버킷이다(FDC 69조 2항).
NEW_FAMILIES = ("wcf", "contq", "cont", "region", "friendly")
INTL_FAMILIES = ("wc_qual",) + NEW_FAMILIES          # 끝난 대회 정리 대상(올림픽은 olympics.py가 관리)
CARRY_FIELD = "intl_suspension"
CLUB_EUROPE_FIELD = "cl_suspension"


def susp_field(fam):
    return f"{fam}_suspension"


def season_yellow_field(fam):
    return f"season_yellow_{fam}"


def progress_field(fam):
    return f"yellow_susp_progress_{fam}"


def migrations():
    """database.init_db() 마이그레이션 루프에 붙일 ALTER(멱등)."""
    out = []
    for fam in NEW_FAMILIES:
        for col in (susp_field(fam), season_yellow_field(fam), progress_field(fam)):
            out.append(f"ALTER TABLE my_player ADD COLUMN {col} INTEGER DEFAULT 0")
    return out


def yellow_field_maps():
    """game_engine.YELLOW_GROUP_FIELD / YELLOW_PROGRESS_FIELD에 더할 항목."""
    g = {susp_field(f): season_yellow_field(f) for f in NEW_FAMILIES}
    pr = {susp_field(f): progress_field(f) for f in NEW_FAMILIES}
    return g, pr


# ── 단계 → 순번 (초기화 경계 판정용) ─────────────────────────────────
PH_QUAL1, PH_QUAL2, PH_QUAL_PO = 0, 1, 2
PH_GROUP, PH_KO_TO_QF, PH_SF_ON = 10, 11, 12
_STAGE_PHASE = {"qual_group": PH_QUAL1, "qual_stage1": PH_QUAL1, "qual_group2": PH_QUAL2,
                "qual_stage2": PH_QUAL2, "qual_po": PH_QUAL_PO,
                "group": PH_GROUP, "league": PH_GROUP,
                "R32": PH_KO_TO_QF, "R16": PH_KO_TO_QF, "QF": PH_KO_TO_QF, "PO": PH_KO_TO_QF,
                "KPO": PH_KO_TO_QF,
                "SF": PH_SF_ON, "F": PH_SF_ON, "TP": PH_SF_ON}


def stage_phase(stage):
    return _STAGE_PHASE.get(stage or "", PH_GROUP)


# ── 규칙 ─────────────────────────────────────────────────────────────
# first/step: first장째에 정지, 그 뒤 step장마다 다시 정지(2,2 = 2·4·6…, 3,2 = 3·5·7…).
# resets: 이 순번 단계에 처음 들어설 때 누적 경고를 0으로(이미 걸린 정지는 유지).
# legacy=True: 공식 규정 미확인 — 예전 방식(5장째 정지 후 카운터 0) 그대로.
def _r(name, first=2, step=2, resets=(), legacy=False, no_accum=False, verified=True):
    return {"name": name, "first": first, "step": step, "resets": frozenset(resets),
            "legacy": legacy, "no_accum": no_accum, "verified": verified}


RULE_WC_QUAL = _r("월드컵 예선 (FIFA 징계규정 67조)")
RULE_WC = _r("월드컵 본선 2026", resets=(PH_KO_TO_QF, PH_SF_ON))
RULE_EURO_QUAL = _r("유로 예선 (UEFA 54조)", first=3, step=2, resets=(PH_QUAL_PO,))
RULE_EURO = _r("유로 본선 (UEFA 54조)", resets=(PH_SF_ON,))
RULE_NL = _r("네이션스리그 (UEFA 60조)", resets=(PH_SF_ON,))
RULE_ASIA_QUAL = _r("아시안컵 예선 (AFC 52조)", resets=(PH_QUAL2, PH_QUAL_PO))
RULE_ASIA = _r("아시안컵 본선 (AFC 52조)", resets=(PH_SF_ON,))
RULE_AFCON_QUAL = _r("AFCON 예선 (CAF 42조)")
RULE_AFCON = _r("AFCON 본선 (CAF 42조)", resets=(PH_KO_TO_QF,))
RULE_GOLD = _r("골드컵 (Concacaf 23.12)", resets=(PH_KO_TO_QF, PH_SF_ON))
RULE_COPA = _r("코파 아메리카", resets=(PH_KO_TO_QF,))
RULE_UNVERIFIED = _r("공식 규정 미확인 — 예전 기준(5장)", legacy=True, verified=False)
RULE_FRIENDLY = _r("친선전 (FDC 69조 2항 h)", no_accum=True)
RULE_UEFA_CLUB = _r("챔스·유로파·컨퍼런스 (UEFA 63조)", first=3, step=2, resets=(PH_SF_ON,))


def intl_family_and_rule(t):
    """intl_tournaments 행 → (묶음, 규칙). 올림픽은 (None, None) — olympics.py 기존 처리."""
    kind = (t or {}).get("kind") or ""
    cont = (t or {}).get("continent") or ""
    name = (t or {}).get("name") or ""
    year = (t or {}).get("year")
    if kind in ("oly_qual", "olympic"):
        return None, None
    if kind == "wc_qual":
        return "wc_qual", RULE_WC_QUAL
    if kind == "world":
        return "wcf", RULE_WC
    if kind.startswith("power_eval") or kind == "friendly":
        return "friendly", RULE_FRIENDLY
    if kind == "cont_qual":
        if cont == "유럽" and _is_euro_qual(t):
            return "contq", RULE_EURO_QUAL
        if cont == "유럽":
            return "contq", RULE_NL
        if cont == "아시아":
            return "contq", RULE_ASIA_QUAL
        if cont == "아프리카":
            return "contq", RULE_AFCON_QUAL
        return "contq", RULE_UNVERIFIED
    if kind == "continent":
        from constants import EURO_NAME
        if name == EURO_NAME:
            return "cont", RULE_EURO
        if cont == "유럽":
            return "cont", RULE_NL
        if cont in ("아시아", "오세아니아"):
            return "cont", RULE_ASIA
        if cont == "아프리카":
            return "cont", RULE_AFCON
        return "cont", RULE_UNVERIFIED
    if kind == "region":
        if "골드컵" in name:
            return "region", RULE_GOLD
        if "코파 아메리카" in name:
            return "region", RULE_COPA
        return "region", RULE_UNVERIFIED
    return "region", RULE_UNVERIFIED


def _is_euro_qual(t):
    try:
        from intl_engine import _is_euro_qual_year, _is_nations_qual_t
        if _is_nations_qual_t(t):
            return False
        return bool(_is_euro_qual_year(t.get("year")))
    except Exception:
        return False


def _nl_inherits(t, prev_state):
    """네이션스리그: 예선(리그 페이즈)과 본선이 한 대회라 경고·경고누적 정지가 이어진다(UEFA 60조)."""
    return ((t or {}).get("kind") == "continent" and (t or {}).get("continent") == "유럽"
            and intl_family_and_rule(t)[1] is RULE_NL and prev_state
            and prev_state.get("rule") == RULE_NL["name"])


# ── 이번 경기 카드 판정 기준(game_engine 카드 함수가 읽는다) ─────────────────
_CTX = {}


def ctx_rule(field):
    return _CTX.get(field)


def set_ctx(field, rule):
    if rule is None or rule.get("legacy"):
        _CTX.pop(field, None)
    else:
        _CTX[field] = rule


def suspends_at(rule, count):
    first, step = rule["first"], rule["step"]
    return count == first or (count > first and (count - first) % step == 0)


# ── 묶음 상태(meta) ─────────────────────────────────────────────────
def _state_key(field):
    return f"card_state:{field}"


def _get_state(conn, field):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (_state_key(field),)).fetchone()
    if not row or not row["value"]:
        return None
    try:
        return json.loads(row["value"])
    except Exception:
        return None


def _set_state(conn, field, st):
    if st is None:
        conn.execute("DELETE FROM meta WHERE key=?", (_state_key(field),))
    else:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                     (_state_key(field), json.dumps(st, ensure_ascii=False)))


def _get_cause(conn, field):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (f"susp_cause:{field}",)).fetchone()
    return row["value"] if row else None


def _set_cause(conn, field, cause):
    if cause is None:
        conn.execute("DELETE FROM meta WHERE key=?", (f"susp_cause:{field}",))
    else:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (f"susp_cause:{field}", cause))


def _tournament_over_for_me(conn, tid, cur_tid):
    """그 대회가 끝났으면(또는 같은 묶음의 다른 대회가 시작됐으면) True."""
    if tid == cur_tid:
        return False
    row = conn.execute("SELECT status FROM intl_tournaments WHERE id=?", (tid,)).fetchone()
    return (row is None) or (row["status"] == "done")


def _finalize(conn, p, fam, upd, next_t=None):
    """끝난 대회의 카드 정리 — 경고는 소멸(FDC 67조 2항), 경고누적 정지는 소멸(69조 4항),
    퇴장 정지는 다음 공식 경기 버킷으로(69조 2항). 월드컵 예선→본선의 2차 경고 퇴장 정지는 소멸
    (월드컵 2026 규정 10조 2항)."""
    sf, pf = susp_field(fam), progress_field(fam)
    upd[pf] = 0
    pending = int(upd.get(sf, p.get(sf, 0)) or 0)
    if pending > 0:
        cause = _get_cause(conn, sf)
        drop = (cause == "yellow_accum") or (
            fam == "wc_qual" and cause == "second_yellow" and (next_t or {}).get("kind") == "world")
        upd[sf] = 0
        if not drop:
            upd[CARRY_FIELD] = int(upd.get(CARRY_FIELD, p.get(CARRY_FIELD, 0)) or 0) + pending
            _set_cause(conn, CARRY_FIELD, cause or "carry")
        _set_cause(conn, sf, None)
    _set_state(conn, sf, None)


def finalize_family(fam, next_t=None):
    """외부 호출용(예: 새 월드컵 예선 사이클 시작) — 그 묶음의 지난 대회를 정리한다."""
    from game_engine import get_player, update_player
    from database import get_conn
    p = get_player()
    if not p:
        return
    conn = get_conn()
    upd = {}
    _finalize(conn, p, fam, upd, next_t=next_t)
    conn.commit()
    update_player(**upd)


def prepare_intl_match(p, t, stage):
    """내 국가대표 경기 직전. 반환: (카드 판정에 쓸 결장 필드, 정지 여부, 로그 문구 또는 None).
    올림픽이면 None을 돌려준다(호출부가 예전 경로 사용)."""
    fam, rule = intl_family_and_rule(t)
    if fam is None:
        return None
    from game_engine import get_player, update_player, suspension_log_text
    from database import get_conn
    _migrate_legacy(p, t)
    p = get_player() or p
    conn = get_conn()
    upd = {}
    sf, pf = susp_field(fam), progress_field(fam)
    cur_tid = t.get("id")
    ph = stage_phase(stage)
    # 1) 다른 묶음 중 끝난 대회 정리(같은 묶음은 아래 3)에서)
    for g in INTL_FAMILIES:
        if g == fam or g == "friendly":
            continue
        gst = _get_state(conn, susp_field(g))
        if (gst is None and g == "wc_qual" and fam == "wcf"
                and (p.get(susp_field(g)) or p.get(progress_field(g)))):
            # 예전 세이브(상태 기록 전)의 월드컵 예선 카운터 — 본선이 시작됐으면 예선은 확실히 끝났다.
            _finalize(conn, p, g, upd, next_t=t)
            continue
        if gst and _tournament_over_for_me(conn, gst.get("tid"), cur_tid):
            if g == "contq" and fam == "cont" and _nl_inherits(t, gst):
                # 네이션스리그 예선 → 본선: 같은 대회라 경고·정지 승계
                upd[pf] = int(p.get(progress_field(g), 0) or 0)
                _pend = int(p.get(susp_field(g), 0) or 0)
                if _pend:
                    upd[sf] = int(p.get(sf, 0) or 0) + _pend
                    _set_cause(conn, sf, _get_cause(conn, susp_field(g)))
                upd[progress_field(g)] = 0
                upd[susp_field(g)] = 0
                _set_cause(conn, susp_field(g), None)
                _set_state(conn, susp_field(g), None)
                _set_state(conn, sf, {"tid": cur_tid, "phase": ph, "rule": rule["name"]})
            else:
                _finalize(conn, p, g, upd, next_t=t)
    # 2) 같은 묶음: 새 대회면 지난 대회 정리, 같은 대회면 단계 경계 초기화
    st = _get_state(conn, sf)
    if fam != "friendly":
        if st and st.get("tid") != cur_tid:
            _finalize(conn, p, fam, upd, next_t=t)
            st = None
        if st is None:
            if pf not in upd:
                upd[pf] = 0
            st = {"tid": cur_tid, "phase": ph, "rule": rule["name"]}
        else:
            old = st.get("phase", ph)
            if ph > old and any(old < b <= ph for b in rule["resets"]):
                upd[pf] = 0
            st["phase"] = max(old, ph)
        _set_state(conn, sf, st)
    conn.commit()
    if upd:
        update_player(**upd)
    p = get_player() or p
    set_ctx(sf, rule)
    # 3) 출전정지 판정 — 공식 경기는 이월 버킷 먼저, 친선전은 친선전 정지만
    log = None
    for f in ((sf,) if fam == "friendly" else (CARRY_FIELD, sf)):
        cur = int(p.get(f, 0) or 0)
        if cur > 0:
            update_player(**{f: cur - 1})
            log = suspension_log_text(f, cur - 1)
            return sf, True, log
    return sf, False, None


def prepare_club_europe_match(p, tournament_key, stage):
    """챔스·유로파·컨퍼런스 내 경기 직전 — 8강 종료 후 누적 소멸(UEFA 63조), 3장(이후 홀수) 기준."""
    from game_engine import update_player
    from database import get_conn
    conn = get_conn()
    sf = CLUB_EUROPE_FIELD
    st = _get_state(conn, sf)
    ph = stage_phase(stage)
    if st is None or st.get("tid") != tournament_key:
        st = {"tid": tournament_key, "phase": ph, "rule": RULE_UEFA_CLUB["name"]}
    else:
        old = st.get("phase", ph)
        if ph > old and any(old < b <= ph for b in RULE_UEFA_CLUB["resets"]):
            update_player(yellow_susp_progress_europe=0)
        st["phase"] = max(old, ph)
    _set_state(conn, sf, st)
    conn.commit()
    set_ctx(sf, RULE_UEFA_CLUB)


def season_end_club_cleanup(conn=None):
    """시즌 종료 — 챔스류 경고누적 정지는 시즌 끝에 소멸(UEFA 63조 5항). 퇴장 정지는 남긴다."""
    from game_engine import get_player, update_player
    from database import get_conn
    p = get_player()
    if not p:
        return
    conn = conn or get_conn()
    if int(p.get(CLUB_EUROPE_FIELD, 0) or 0) > 0 and _get_cause(conn, CLUB_EUROPE_FIELD) == "yellow_accum":
        update_player(**{CLUB_EUROPE_FIELD: 0})
        _set_cause(conn, CLUB_EUROPE_FIELD, None)
    _set_state(conn, CLUB_EUROPE_FIELD, None)
    conn.commit()


# ── 기존 세이브 복구(1회) ───────────────────────────────────────────
_LEGACY_KEY = "card_rules_legacy_v1"


def _migrate_legacy(p, t_now=None):
    """예전 공용 카운터(intl_suspension / yellow_susp_progress_intl)를 대회별로 옮긴다.
      · 누적 경고: 내가 마지막으로 뛴(또는 결장한) 국가대표 경기의 대회가 아직 진행 중이면 그 대회 묶음으로,
        끝났으면 소멸(경고는 다른 대회로 안 넘어감).
      · 걸려 있는 정지: 그 대회가 진행 중이면 그 대회 묶음으로(원인과 무관하게 같은 대회 다음 경기에서
        소화 — 규정과 같다). 끝났으면 원인을 그 경기 기록(내 경고 장수)으로 판별 — 친선전에서 생긴 건
        친선전 묶음, 2차 경고 퇴장은 이월 버킷, 판별 불가는 이월 버킷(징계를 지우지 않는 쪽).
    한 번만 돈다(meta 플래그)."""
    from game_engine import update_player
    from database import get_conn
    conn = get_conn()
    if conn.execute("SELECT 1 FROM meta WHERE key=?", (_LEGACY_KEY,)).fetchone():
        return
    prog = int(p.get("yellow_susp_progress_intl", 0) or 0)
    pend = int(p.get(CARRY_FIELD, 0) or 0)
    upd = {}
    if prog or pend:
        row = conn.execute(
            """SELECT m.stage, m.my_yellow_cards, m.my_played, m.my_absence_reason,
                      t.id AS tid, t.kind, t.continent, t.name, t.year, t.status
               FROM intl_matches m JOIN intl_tournaments t ON t.id = m.tournament_id
               WHERE m.is_my=1 AND m.home_score >= 0
                 AND (m.my_played=1 OR m.my_absence_reason IS NOT NULL)
                 AND t.kind NOT IN ('wc_qual', 'oly_qual', 'olympic')
               ORDER BY t.year DESC, m.week DESC, m.day DESC, m.id DESC LIMIT 1""").fetchone()
        t = dict(row) if row else None
        fam = intl_family_and_rule(t)[0] if t else None
        live = bool(t) and t.get("status") != "done" and fam is not None
        if prog:
            if live and fam != "friendly":
                upd[progress_field(fam)] = prog
                _set_state(conn, susp_field(fam), {"tid": t["tid"], "phase": stage_phase(t["stage"]),
                                                   "rule": intl_family_and_rule(t)[1]["name"]})
            upd["yellow_susp_progress_intl"] = 0
        if pend:
            # 원인 판별: 마지막 경기를 내가 뛰었다면 남은 정지는 그 경기에서 생긴 것이다(정지 소화는 경기
            # 전에 하므로). 국가대표 카드는 다이렉트 퇴장 판정이 경고보다 먼저라 같은 경기에 경고와
            # 다이렉트 퇴장이 같이 나오지 않는다 → 경고 0장 = 다이렉트 퇴장, 1장 = 경고 누적, 2장 = 2차 경고.
            cause = None
            if t and t.get("my_played"):
                cause = {0: "red_card", 1: "yellow_accum"}.get(int(t.get("my_yellow_cards") or 0), "second_yellow")
            if live:
                upd[susp_field(fam)] = pend
                upd[CARRY_FIELD] = 0
                if cause:
                    _set_cause(conn, susp_field(fam), cause)
                if fam != "friendly":
                    _set_state(conn, susp_field(fam), {"tid": t["tid"], "phase": stage_phase(t["stage"]),
                                                       "rule": intl_family_and_rule(t)[1]["name"]})
            elif fam == "friendly" and cause:
                upd["friendly_suspension"] = pend
                upd[CARRY_FIELD] = 0
                _set_cause(conn, "friendly_suspension", cause)
            elif cause == "yellow_accum":
                # 끝난 대회의 경고누적 정지 — 다른 대회로 안 넘어간다(FDC 69조 4항)
                upd[CARRY_FIELD] = 0
            else:
                # 끝난 공식 대회의 퇴장 정지(또는 판별 불가) — 다음 공식 경기 버킷에 그대로 둔다.
                _set_cause(conn, CARRY_FIELD, cause or "carry")
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, '1')", (_LEGACY_KEY,))
    conn.commit()
    if upd:
        update_player(**upd)


def reset_keys_sql():
    """새 게임 초기화 때 지울 meta 키."""
    return ["DELETE FROM meta WHERE key LIKE 'card_state:%'",
            f"DELETE FROM meta WHERE key='{_LEGACY_KEY}'"]