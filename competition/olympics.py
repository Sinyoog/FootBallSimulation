# -*- coding: utf-8 -*-
"""competition/olympics.py — U-23 올림픽 (남자) [2026-10 신설, 신민용 확정]

설계 요약(constants.py "U-23 올림픽" 섹션과 같은 내용):
  · 2000·2004·2008…(OLYMPIC_ANCHOR_YEAR 위상, 4년 주기 = 네이션스 예선 해)에 연다.
  · 겨울 휴식기(25~28주): 대륙별 예선(kind='oly_qual') — 월드컵 예선 엔진을 그대로 쓴다.
    조 편성·일정·마감(intl_engine._finalize_qual*)·통과국 저장(_save_qual_results)이 전부
    월드컵 예선과 같은 함수이고, 대륙별 설정만 항상 32팀 체제(WC_QUAL_32 / WC_QUOTA)다.
  · 연말(45~52주): 본선(kind='olympic') — 항상 32팀, 8조×4 → 16강 → 8강 → 4강 →
    결승·동메달 결정전. 일정은 월드컵 32팀 본선 규칙("world_cup_32") 그대로.
  · 개최국·자동 진출·와일드카드 없음.

명단(이 모듈의 핵심):
  · 자격: 그 해 게임 나이 23세 이하(최소 나이 없음), 복무 중 선수 불가(국대와 같은
    database.get_country_nationals_by_position — 군팀 소속은 이미 빠져 있다).
  · 23세 이하로 18명·GK 2명을 못 채우면 모자란 자리만 24 → 25 → 26세… 순으로 보충한다
    (GK가 모자라면 GK 자리만, 전체가 모자라면 모자란 인원만). 특정 포지션 부족은 나이를
    넓히지 않고 포지션 적합도로 대체한다.
  · 선발 순서: 후보 풀 → 포메이션 결정 → 포메이션 자리에 자연 포지션으로 주전 11명 →
    주전과 비교해 OLYMPIC_SQUAD_TABLE에서 모자란 포지션을 채워 교체 7명 → 후보 탈락 4명.
  · 선발 점수는 성인 대표팀과 같은 intl_selection_score_v3(성인 A매치 경험 포함).
    올림픽 출전은 그 경험에 들어가지 않는다(database.get_player_total_intl_appearances).
  · 본선 명단은 본선 직전(대회 생성 시점)에 다시 뽑는다.

국적·발탁(플레이어):
  · 성인 대표팀 발탁창(수락/거절)을 그대로 쓴다. 예선 발탁은 예선 생성 때, 본선 발탁은
    본선 명단을 다시 뽑을 때 뜬다.
  · 성인 A매치로 국적이 고정된 선수는 그 나라 올림픽에만 나간다. 올림픽은 국적을
    고정하지 않는다(cap-tie·캡 집계 제외 — intl_engine.simulate_my_match).
  · 같은 올림픽 안에서는 예선에 실제로 뽑힌 나라 = 본선 나라.

기록·병역:
  · 국제대회 기록(국제전·AI 국제대회 기록·국가 기록)에는 남기고 A매치(캡·A매치 골·
    파워랭킹·국적 고정·감독 평가·발롱 국대 점수)에서는 뺀다.
  · 성적 표시: 금메달/은메달/동메달/4위, 나머지는 "8강 탈락" 식, 예선 탈락은 "예선 탈락".
  · 후보 탈락 4명: 출전·대회 기록·메달·병역 혜택 없음. 플레이어는 커리어에
    "U-23 올림픽 대표팀 후보 탈락"만 남는다. 그 밖의 후보 풀 선수는 기록 없음.
  · 한국 최종 18명(출전 0분 포함, AI·플레이어 공통)이 금·은·동메달을 따면 병역 혜택
    (기존 "면제" 상태와 효과가 같고, oly_military_benefit 표에 남겨 화면에 "체육요원"으로
    구분한다).
  · 징계는 성인 대표팀과 따로 센다(oly_suspension). 예선 퇴장으로 남은 출전정지는 본선으로
    넘어가고, 경고 누적은 본선 시작 때 초기화한다.

스위치: constants.OLYMPICS_ENABLED. 끄면 대회·표·컬럼이 하나도 안 만들어지고, 기존 코드의
조회 문장도 바뀌지 않는다(올림픽 대회가 하나도 없을 때는 원래 SQL을 그대로 쓴다 —
교훈 20: 쿼리 문자열이 바뀌면 실행계획·행 순서·난수 소비 순서가 바뀔 수 있다).
"""
import random
import zlib

import constants as C
from database import get_conn

OLY_QUAL_KIND = "oly_qual"
OLY_KIND = "olympic"
OLY_KINDS = (OLY_QUAL_KIND, OLY_KIND)
OLY_KINDS_SQL = "('oly_qual','olympic')"
CONFS = ["유럽", "남미", "북미", "아시아", "아프리카"]
_ME = 0   # 후보 dict의 id — 내 선수(AI id는 1부터)


# ══════════════════════════════════════════════════════════════════
# 판정 도우미
# ══════════════════════════════════════════════════════════════════
def enabled():
    return bool(getattr(C, "OLYMPICS_ENABLED", False))


def is_oly_kind(kind):
    return kind in OLY_KINDS


def is_oly_qual_kind(kind):
    return kind == OLY_QUAL_KIND


def is_olympic_year(year):
    return (enabled() and year is not None
            and (int(year) - C.OLYMPIC_ANCHOR_YEAR) % C.OLYMPIC_INTERVAL == 0)


def is_olympic_name(name):
    """intl_history·trophy_log처럼 kind 없이 대회 이름만 남는 표에서 올림픽 행 판정."""
    return bool(name) and C.OLYMPIC_NAME in str(name)


def finals_name(year):
    """화면 표시용 "2000 U-23 올림픽". 대회 행(intl_tournaments.name)에는 월드컵("월드컵")·
    유로처럼 연도 없이 OLYMPIC_NAME만 저장한다(연도는 year 칸 — 기존 본선 대회와 같은 규칙)."""
    return f"{year} {C.OLYMPIC_NAME}"


def qual_name(year, conf):
    return f"{year} {C.OLYMPIC_NAME} {conf} 예선"


def result_label(result):
    """intl_engine의 내부 결과("우승"/"8강" 등) → 올림픽 표시("금메달"/"8강 탈락")."""
    return C.OLYMPIC_RESULT_LABEL.get(result, result)


def qual_cfg(continent):
    """올림픽 예선 대륙 설정 — 연도와 무관하게 항상 32팀 월드컵 체제."""
    return C.WC_QUAL_32.get(continent, {})


def olympic_tournament_ids(conn=None, year=None):
    """올림픽(예선·본선) 대회 id 집합. 올림픽이 한 번도 안 열렸으면 빈 집합."""
    own = conn is None
    conn = conn or get_conn()
    try:
        if year is None:
            rows = conn.execute(
                f"SELECT id FROM intl_tournaments WHERE kind IN {OLY_KINDS_SQL}").fetchall()
        else:
            rows = conn.execute(
                f"SELECT id FROM intl_tournaments WHERE kind IN {OLY_KINDS_SQL} AND year=?",
                (year,)).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()
    finally:
        if own:
            conn.close()


def is_oly_tid(tournament_id, conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        r = conn.execute("SELECT kind FROM intl_tournaments WHERE id=?",
                         (tournament_id,)).fetchone()
        return bool(r) and r[0] in OLY_KINDS
    except Exception:
        return False
    finally:
        if own:
            conn.close()


def first_non_olympic(rows, key="kind"):
    """fetchall() 결과에서 올림픽 행을 건너뛴 첫 행(없으면 None) — 기존 fetchone() 자리에
    쓴다. 올림픽 행이 없으면 fetchone()과 같은 행이다(같은 SQL·같은 순서)."""
    for r in rows or ():
        try:
            k = r[key]
        except (KeyError, IndexError, TypeError):
            k = None
        if k not in OLY_KINDS:
            return r
    return None


# ══════════════════════════════════════════════════════════════════
# 스키마 — 켜져 있을 때만 만든다(끄면 표·컬럼이 생기지 않아 기존 세이브와 완전히 같다)
# ══════════════════════════════════════════════════════════════════
MY_PLAYER_COLS = [
    ("oly_suspension", "INTEGER DEFAULT 0"),          # 올림픽 출전정지(예선·본선 공통 — 퇴장 이월)
    ("season_yellow_oly", "INTEGER DEFAULT 0"),       # 올림픽 누적 경고(표시)
    ("yellow_susp_progress_oly", "INTEGER DEFAULT 0"),  # 올림픽 경고 누적 진행(정지 판정)
]


def migrations():
    """database.init_db() 마이그레이션 루프에 붙일 문장(멱등)."""
    if not enabled():
        return []
    out = [
        # 후보 탈락 4명 — player_id 0 = 내 선수. category = 18명 표 약어(CB/FB/…).
        "CREATE TABLE IF NOT EXISTS oly_reserve_cut(tournament_id INTEGER, country TEXT, "
        "rank INTEGER, player_id INTEGER, score REAL DEFAULT 0, position TEXT DEFAULT '', "
        "category TEXT DEFAULT '', PRIMARY KEY(tournament_id, country, rank))",
        # 나라별 명단 메타 — 포메이션, 실제로 쓴 최고 나이(23 초과면 나이 확대), 팀 전력.
        "CREATE TABLE IF NOT EXISTS oly_squad_meta(tournament_id INTEGER, country TEXT, "
        "formation TEXT DEFAULT '', max_age INTEGER DEFAULT 23, extended INTEGER DEFAULT 0, "
        "ovr REAL DEFAULT 0, me_in INTEGER DEFAULT 0, PRIMARY KEY(tournament_id, country))",
        # 최종 18명 역할 — 약어(CB/FB/…/GK), 주전 여부·자리, 자연 포지션 여부. player_id 0 = 내 선수.
        # 18명 구성 검사·화면(다음 단계)용. 경기 엔진은 intl_squad를 그대로 쓴다.
        "CREATE TABLE IF NOT EXISTS oly_squad_roles(tournament_id INTEGER, country TEXT, "
        "player_id INTEGER, category TEXT DEFAULT '', starter INTEGER DEFAULT 0, "
        "slot TEXT DEFAULT '', natural INTEGER DEFAULT 1, "
        "PRIMARY KEY(tournament_id, country, player_id))",
        # 병역 혜택(체육요원) 기록 — 효과는 military_status='exempt'와 같고 화면 구분용.
        # player_id 0 = 내 선수.
        "CREATE TABLE IF NOT EXISTS oly_military_benefit(player_id INTEGER, year INTEGER, "
        "tournament_id INTEGER, result TEXT DEFAULT '', PRIMARY KEY(player_id))",
    ]
    from competition.oly_manager import migrations as _mgr_migrations   # [2026-10 2단계] U-23 감독
    out += _mgr_migrations()
    out += [f"ALTER TABLE my_player ADD COLUMN {n} {t}" for n, t in MY_PLAYER_COLS]
    return out


RESET_TABLES = ("oly_reserve_cut", "oly_squad_meta", "oly_squad_roles", "oly_military_benefit",
                "oly_team_managers", "oly_objectives")   # [2026-10 2단계] U-23 감독


def military_benefit_label(player_id, conn=None):
    """병역 혜택(체육요원) 여부 — 화면 표시 구분용. 표가 없으면(꺼짐) 빈 문자열."""
    own = conn is None
    conn = conn or get_conn()
    try:
        r = conn.execute("SELECT 1 FROM oly_military_benefit WHERE player_id=?",
                         (player_id,)).fetchone()
        return "체육요원" if r else ""
    except Exception:
        return ""
    finally:
        if own:
            conn.close()


# ══════════════════════════════════════════════════════════════════
# 포메이션 자리 → 18명 표 약어
# ══════════════════════════════════════════════════════════════════
_SLOT_CAT = {"GK": "GK", "CB": "CB", "LB": "FB", "RB": "FB", "LWB": "WB", "RWB": "WB",
             "CDM": "DM", "CM": "CM", "CAM": "AM", "LM": "WM", "RM": "WM",
             "LW": "W", "RW": "W", "ST": "ST", "CF": "ST"}
# 교체·후보 탈락을 채울 때 "그 약어의 자연 포지션"(등록 포지션 기준).
CAT_NATURAL = {"GK": ("GK",), "CB": ("CB",), "FB": ("LB", "RB"), "WB": ("LB", "RB"),
               "DM": ("CDM",), "CM": ("CM",), "AM": ("CAM",), "WM": ("LW", "RW"),
               "W": ("LW", "RW"), "ST": ("ST", "CF")}
# 자연 포지션 후보가 없을 때 적합도를 잴 대표 자리.
CAT_REP_SLOT = {"GK": "GK", "CB": "CB", "FB": "LB", "WB": "LWB", "DM": "CDM", "CM": "CM",
                "AM": "CAM", "WM": "LM", "W": "LW", "ST": "ST"}
_REG_POSITIONS = ("GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "CF", "ST")
_DF_CATS = ("CB", "FB", "WB")
_MF_CATS = ("DM", "CM", "AM", "WM")
_FW_CATS = ("ST", "W")


def slot_category(formation, slot):
    """백3·백5에서 LWB/RWB가 없는 포메이션(3-4-x)의 LM/RM은 WB로 본다."""
    slots = C.FORMATION_SLOTS.get(formation) or []
    if slot in ("LM", "RM") and slots.count("CB") >= 3 and not any(
            s in ("LWB", "RWB") for s in slots):
        return "WB"
    return _SLOT_CAT.get(slot, "CM")


def formation_categories(formation):
    return [slot_category(formation, s) for s in C.FORMATION_SLOTS[formation]]


_NATURAL_FOR_SLOT = {}


def natural_positions_for_slot(slot):
    """그 자리의 자연 포지션 = 게임의 자리 적합 순위(formation_logic._mismatch_rank)가
    가장 좋은 등록 포지션들. LM/RM은 LW/RW, LWB/RWB는 LB/RB가 된다."""
    v = _NATURAL_FOR_SLOT.get(slot)
    if v is None:
        from formation_logic import _mismatch_rank
        ranks = {p: _mismatch_rank(p, slot) for p in _REG_POSITIONS}
        best = min(ranks.values())
        v = frozenset(p for p, r in ranks.items() if r == best)
        _NATURAL_FOR_SLOT[slot] = v
    return v


def validate_squad_tables():
    """[게임 시작 시 자동 검사] 20개 포메이션 모두 주전 11 + 교체 7 = 표 합계(18, GK 2 포함),
    주전 자리 구성이 표 안에 들어가는지, DF·MF·FW 합계 규칙(백4 6·6·4, 4-2-4 6·5·5,
    백3·백5 7(CB4 WB3)·5·4)을 확인한다. 어긋나면 AssertionError."""
    from collections import Counter
    tbl = C.OLYMPIC_SQUAD_TABLE
    assert set(tbl) == set(C.FORMATION_SLOTS), \
        f"올림픽 18명 표와 포메이션 목록 불일치: {set(tbl) ^ set(C.FORMATION_SLOTS)}"
    for f, t in tbl.items():
        cats = Counter(c for c in formation_categories(f) if c != "GK")
        gk_starters = C.FORMATION_SLOTS[f].count("GK")
        assert gk_starters == 1, f"{f}: 주전 GK {gk_starters}"
        assert sum(t.values()) + C.OLYMPIC_GK == C.OLYMPIC_SQUAD_SIZE, \
            f"{f}: 표 합계 {sum(t.values())} + GK {C.OLYMPIC_GK} != {C.OLYMPIC_SQUAD_SIZE}"
        for c, n in cats.items():
            assert t.get(c, 0) >= n, f"{f}: 주전 {c} {n}명 > 표 {t.get(c, 0)}"
        subs = (sum(t.values()) - sum(cats.values())) + (C.OLYMPIC_GK - gk_starters)
        assert 11 + subs == C.OLYMPIC_SQUAD_SIZE, f"{f}: 주전 11 + 교체 {subs} != 18"
        df = sum(t.get(c, 0) for c in _DF_CATS)
        mf = sum(t.get(c, 0) for c in _MF_CATS)
        fw = sum(t.get(c, 0) for c in _FW_CATS)
        if C.FORMATION_SLOTS[f].count("CB") >= 3:
            want = (7, 5, 4)
            assert t.get("CB", 0) == 4 and t.get("WB", 0) == 3, f"{f}: 백3·백5는 CB4 WB3"
        elif f == "4-2-4":
            want = (6, 5, 5)
        else:
            want = (6, 6, 4)
        assert (df, mf, fw) == want, f"{f}: DF·MF·FW {df}·{mf}·{fw} != {want}"
    return True


# 모듈 로드(= 게임 시작) 때 한 번 검사 — 표를 잘못 고치면 게임이 바로 알려 준다.
validate_squad_tables()


# ══════════════════════════════════════════════════════════════════
# 후보 풀 · 점수
# ══════════════════════════════════════════════════════════════════
def _score_candidates(cands, year):
    """성인 대표팀과 같은 선발 점수(intl_selection_score_v3). 반환: 포지션 통계(내 선수용)."""
    if not cands:
        return {}
    from intl_engine import (_intl_candidate_metrics, intl_selection_score_v3,
                             _INTL_SEL_SHORTLIST_PER_POS, _INTL_POS_STAT_KEY)
    from database import get_player_total_intl_appearances
    by_pos = {}
    for c in cands:
        by_pos.setdefault(c["position"], []).append(c)
    shortlist = []
    for _pos in sorted(by_pos):
        lst = sorted(by_pos[_pos], key=lambda c: (-(c["ovr"] or 0), c["id"]))
        shortlist.extend(lst[:_INTL_SEL_SHORTLIST_PER_POS])
    metrics = _intl_candidate_metrics(shortlist, year=year) if shortlist else {}
    apps = get_player_total_intl_appearances([c["id"] for c in cands])
    for c in cands:
        c["_score"] = intl_selection_score_v3(c, metrics.get(c["id"]), apps.get(c["id"], 0))
    return metrics.get(_INTL_POS_STAT_KEY) or {}


def _my_candidate(p, country, year, pos_stat):
    from intl_engine import (_my_selection_metrics, intl_selection_score_v3,
                             _my_total_intl_appearances, _my_club_country)
    cand = {"id": _ME, "is_me": True, "name": p.get("name") or "나",
            "position": p.get("position", "CM"), "ovr": p.get("ovr", 0) or 0,
            "age": p.get("age", 0) or 0, "club_tier": p.get("current_tier", 1),
            "club_country": _my_club_country(p)}
    penalty = 0.0
    if p.get("injured"):
        penalty += 15.0
    rel = p.get("manager_relation", 50)
    if rel < 30:
        penalty += 8.0
    elif rel < 50:
        penalty += 3.0
    cand["_score"] = intl_selection_score_v3(
        cand, _my_selection_metrics(p, pos_stat, year=year),
        appearances=_my_total_intl_appearances()) - penalty
    return cand


def my_eligible(p, year=None):
    """플레이어 발탁창 자격(나이 상한만, 최소 나이 없음). 복무 중은 _mil_block_sel이 막는다."""
    return bool(p) and (p.get("age", 0) or 0) <= C.OLYMPIC_MAX_AGE


def build_pool(country, year, p=None, include_me=False):
    """올림픽 후보 풀. 반환 (pool, max_age_used, pos_stat).
    23세 이하로 GK 2명·전체 18명이 안 되면 모자란 자리만 24 → 25 → 26세… 순으로 보충."""
    from database import get_country_nationals_by_position
    nat_by_pos = get_country_nationals_by_position(country)
    all_rows = [c for _pos in sorted(nat_by_pos) for c in nat_by_pos[_pos]]
    max_age = C.OLYMPIC_MAX_AGE
    pool = [c for c in all_rows if (c.get("age") or 0) <= max_age]
    pos_stat = _score_candidates(pool, year)
    me = None
    if include_me and p:
        me = _my_candidate(p, country, year, pos_stat)
        if (me["age"] or 0) <= max_age:
            pool.append(me)
            me = None
    used_age = max_age
    n_gk = sum(1 for c in pool if c["position"] == "GK")
    if n_gk < C.OLYMPIC_GK or len(pool) < C.OLYMPIC_SQUAD_SIZE:
        older = [c for c in all_rows if (c.get("age") or 0) > max_age]
        if me is not None:
            older.append(me)
        age = max_age + 1
        max_seen = max([c.get("age") or 0 for c in older], default=age)
        while (n_gk < C.OLYMPIC_GK or len(pool) < C.OLYMPIC_SQUAD_SIZE) and age <= max_seen:
            at_age = [c for c in older if (c.get("age") or 0) == age]
            if at_age:
                _score_candidates([c for c in at_age if not c.get("is_me")], year)
                at_age.sort(key=lambda c: (-c["_score"], c["id"]))
                added = []
                need_gk = max(0, C.OLYMPIC_GK - n_gk)
                for c in [c for c in at_age if c["position"] == "GK"][:need_gk]:
                    added.append(c)
                n_gk += len(added)
                need_tot = max(0, C.OLYMPIC_SQUAD_SIZE - len(pool) - len(added))
                rest = [c for c in at_age if c not in added and c["position"] != "GK"]
                added += rest[:need_tot]
                if added:
                    pool.extend(added)
                    used_age = age
            age += 1
    return pool, used_age, pos_stat


# ══════════════════════════════════════════════════════════════════
# 선발 — 포메이션 → 주전 11 → 교체 7 → 후보 탈락 4
# ══════════════════════════════════════════════════════════════════
def _formation_rng(year, kind, country, mgr_id=0):
    """[2026-10 2단계] 난수 = (월드, 연도, 나라, U-23 감독) — 같은 올림픽에서 같은 감독이면 예선·본선이
    같은 난수라, 포메이션이 바뀌는 건 명단(후보 풀)이 달라졌을 때뿐이다(예전엔 대회 종류까지 섞어
    예선·본선이 서로 다른 난수였다). kind는 호출부 호환용으로만 받는다."""
    from database import get_world_salt
    return random.Random(zlib.crc32(
        f"olyform:{get_world_salt()}:{year}:{country}:{int(mgr_id or 0)}".encode("utf-8")))


def _choose_formation(pool, year, kind, country, style=None, mgr_id=0):
    """후보 풀 상위 26명(선발 점수)과 감독 성향으로 포메이션을 고른다 — 클럽·대표팀과 같은
    선택기(choose_formation_prepped). 전역 난수 불사용(나라·연도별 고정 난수)."""
    from formation_logic import prep_roster, choose_formation_prepped
    top = sorted(pool, key=lambda c: (-c["_score"], c["id"]))[:26]
    rng = _formation_rng(year, kind, country, mgr_id)
    name, _pen = choose_formation_prepped(
        prep_roster(top), "4-4-2", (style or {}).get("style_attack") or "BALANCED",
        rng=rng, style=style)
    return name


def _eff(c, slot):
    from formation_logic import _mismatch_penalty
    return c["_score"] * (1.0 - _mismatch_penalty(c["position"], slot))


def _pick_starters(pool, formation):
    """포메이션 자리에 자연 포지션으로 주전 11명. 자연 포지션 후보가 모자란 자리만
    포지션 적합도(선발 점수 × (1 − 자리 적합 패널티))로 대체한다."""
    slots = C.FORMATION_SLOTS[formation]
    filled = [None] * len(slots)
    used = set()
    ordered = sorted(pool, key=lambda c: (-c["_score"], c["id"]))
    for c in ordered:
        for i, s in enumerate(slots):
            if filled[i] is None and c["position"] in natural_positions_for_slot(s):
                filled[i] = c
                used.add(id(c))
                break
        if all(x is not None for x in filled):
            break
    for i, s in enumerate(slots):
        if filled[i] is not None:
            continue
        is_gk_slot = (s == "GK")
        cands = [c for c in ordered if id(c) not in used
                 and ((c["position"] == "GK") == is_gk_slot)]
        if not cands:
            cands = [c for c in ordered if id(c) not in used]
        if not cands:
            continue
        best = max(cands, key=lambda c: (_eff(c, s), -c["id"]))
        filled[i] = best
        used.add(id(best))
    return filled


def _pick_subs(pool, formation, starters):
    """교체 7명 — 표(OLYMPIC_SQUAD_TABLE + GK 2)와 비교해 주전에서 모자란 약어를 채운다.
    약어마다 자연 포지션 후보 중 선발 점수가 높은 순, 없으면 적합도로 대체."""
    cats = formation_categories(formation)
    need = dict(C.OLYMPIC_SQUAD_TABLE[formation])
    need["GK"] = C.OLYMPIC_GK
    for i, c in enumerate(starters):
        if c is not None:
            need[cats[i]] = need.get(cats[i], 0) - 1
    used = {id(c) for c in starters if c is not None}
    ordered = sorted(pool, key=lambda c: (-c["_score"], c["id"]))
    subs = []   # (cand, category, natural?)
    # GK 먼저
    while need.get("GK", 0) > 0:
        gks = [c for c in ordered if id(c) not in used and c["position"] == "GK"]
        if not gks:
            break
        subs.append((gks[0], "GK", True))
        used.add(id(gks[0]))
        need["GK"] -= 1
    while True:
        open_cats = [k for k, v in need.items() if v > 0 and k != "GK"]
        if not open_cats:
            break
        best = None
        for k in sorted(open_cats):
            nat = CAT_NATURAL[k]
            for c in ordered:
                if id(c) in used or c["position"] not in nat:
                    continue
                if best is None or (c["_score"], -c["id"]) > (best[0]["_score"], -best[0]["id"]):
                    best = (c, k)
                break
        if best is not None:
            c, k = best
            subs.append((c, k, True))
            used.add(id(c))
            need[k] -= 1
            continue
        # 자연 포지션 후보가 없는 약어 — 적합도로 대체(GK 제외)
        k = sorted(open_cats)[0]
        rep = CAT_REP_SLOT[k]
        cands = [c for c in ordered if id(c) not in used and c["position"] != "GK"] \
            or [c for c in ordered if id(c) not in used]
        if not cands:
            break
        c = max(cands, key=lambda c: (_eff(c, rep), -c["id"]))
        subs.append((c, k, False))
        used.add(id(c))
        need[k] -= 1
    return subs


def _pick_reserve(pool, formation, starters, subs):
    """후보 탈락 4명 — 최종 18명에서 떨어진 선수 중 상위 4명. 18명에서 부족했던 포지션
    (자연 포지션이 아닌 선수로 메운 약어, 메운 수가 많은 순)부터 그 약어의 자연 포지션
    후보를 먼저 고르고, 남은 자리는 선발 점수 순."""
    cats = formation_categories(formation)
    short = {}
    for i, c in enumerate(starters):
        if c is not None and c["position"] not in natural_positions_for_slot(
                C.FORMATION_SLOTS[formation][i]):
            short[cats[i]] = short.get(cats[i], 0) + 1
    for c, k, natural in subs:
        if not natural:
            short[k] = short.get(k, 0) + 1
    used = {id(c) for c in starters if c is not None} | {id(c) for c, _k, _n in subs}
    ordered = sorted(pool, key=lambda c: (-c["_score"], c["id"]))
    out = []   # (cand, category)
    for k in sorted(short, key=lambda k: (-short[k], k)):
        if len(out) >= C.OLYMPIC_RESERVE_CUT:
            break
        for c in ordered:
            if id(c) not in used and c["position"] in CAT_NATURAL[k]:
                out.append((c, k))
                used.add(id(c))
                break
    for c in ordered:
        if len(out) >= C.OLYMPIC_RESERVE_CUT:
            break
        if id(c) not in used:
            out.append((c, _natural_cat_of(c["position"], formation)))
            used.add(id(c))
    return out


def _natural_cat_of(position, formation):
    tbl = C.OLYMPIC_SQUAD_TABLE.get(formation, {})
    if position == "GK":
        return "GK"
    for k in ("CB", "FB", "WB", "DM", "CM", "AM", "W", "WM", "ST"):
        if position in CAT_NATURAL[k] and tbl.get(k):
            return k
    return _SLOT_CAT.get(position, "CM")


class SquadPlan(dict):
    """select_squad 결과. 키:
       formation, max_age, extended, ovr,
       starters [(cand, slot)], subs [(cand, category)], reserve [(cand, category)],
       me_in(bool), me_starter(bool), me_reserve(bool)."""


def _top11_ovr(cands):
    vals = sorted(((c.get("ovr") or 0) for c in cands), reverse=True)[:11]
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def select_squad(country, year, kind, p=None, include_me=False, allow_flip=False, style=None):
    """한 나라 올림픽 명단을 짠다(전역 난수는 allow_flip일 때 내 선수 경계선 판정에만 1회)."""
    pool, used_age, _pos_stat = build_pool(country, year, p=p, include_me=include_me)
    # [2026-10 2단계] 감독 성향 — 그 나라 U-23 감독(competition/oly_manager). 감독이 없으면 None
    # (예전처럼 기본 성향). 감독 id는 intl_formations에 같이 남긴다(기록실·평가가 읽는다).
    mgr_id = 0
    if style is None:
        from competition.oly_manager import manager_for_country
        mgr_id, style = manager_for_country(country)
    plan = SquadPlan(formation="", max_age=used_age,
                     extended=int(used_age > C.OLYMPIC_MAX_AGE), ovr=0.0,
                     starters=[], subs=[], reserve=[], me_in=False, me_starter=False,
                     me_reserve=False, pool_size=len(pool), manager_id=mgr_id)
    if not pool:
        return plan
    formation = _choose_formation(pool, year, kind, country, style=style, mgr_id=mgr_id)
    starters = _pick_starters(pool, formation)
    subs = _pick_subs(pool, formation, starters)
    if allow_flip and include_me:
        starters, subs = _maybe_flip_me(pool, formation, starters, subs)
    reserve = _pick_reserve(pool, formation, starters, subs)
    slots = C.FORMATION_SLOTS[formation]
    plan["formation"] = formation
    plan["starters"] = [(c, slots[i]) for i, c in enumerate(starters) if c is not None]
    plan["subs"] = [(c, k) for c, k, _n in subs]
    plan["sub_natural"] = {id(c): n for c, _k, n in subs}
    plan["reserve"] = reserve
    eighteen = [c for c, _s in plan["starters"]] + [c for c, _k in plan["subs"]]
    plan["ovr"] = _top11_ovr(eighteen)
    plan["me_in"] = any(c.get("is_me") for c in eighteen)
    plan["me_starter"] = any(c.get("is_me") for c, _s in plan["starters"])
    plan["me_reserve"] = any(c.get("is_me") for c, _k in reserve)
    return plan


def _maybe_flip_me(pool, formation, starters, subs):
    """[경계선 25% — 성인 대표팀 _check_selection과 같은 규칙] 내 선수가 같은 약어의
    경계(마지막 선발 ↔ 첫 탈락)에서 INTL_SELECTION_MARGIN 이내면 25% 확률로 뒤집힌다.
    같은 약어(GK는 GK끼리) 안에서만 맞바꾸므로 포지션 정원·GK 2명은 그대로 지켜진다."""
    margin = C.INTL_SELECTION_MARGIN
    cats = formation_categories(formation)
    slots = C.FORMATION_SLOTS[formation]
    me = next((c for c in pool if c.get("is_me")), None)
    if me is None:
        return starters, subs
    chosen = {id(c) for c in starters if c is not None} | {id(c) for c, _k, _n in subs}
    ordered = sorted(pool, key=lambda c: (-c["_score"], c["id"]))
    if id(me) in chosen:
        # 내 약어와 자리
        if any(c is me for c in starters):
            i = next(i for i, c in enumerate(starters) if c is me)
            k, nat = cats[i], natural_positions_for_slot(slots[i])
        else:
            k = next(kk for c, kk, _n in subs if c is me)
            nat = CAT_NATURAL[k]
        nxt = next((c for c in ordered if id(c) not in chosen and c["position"] in nat
                    and (c["position"] == "GK") == (me["position"] == "GK")), None)
        if nxt is None or me["_score"] - nxt["_score"] > margin:
            return starters, subs
        if random.random() < 0.25:
            starters = [nxt if c is me else c for c in starters]
            subs = [(nxt, kk, nn) if c is me else (c, kk, nn) for c, kk, nn in subs]
        return starters, subs
    # 미선발 — 내 포지션이 자연 포지션인 약어의 가장 약한 교체 선수와 비교
    cands = [(c, kk, nn) for c, kk, nn in subs
             if me["position"] in CAT_NATURAL.get(kk, ()) and not c.get("is_me")]
    if not cands:
        return starters, subs
    weakest = min(cands, key=lambda t: (t[0]["_score"], -t[0]["id"]))
    if weakest[0]["_score"] - me["_score"] > margin:
        return starters, subs
    if random.random() < 0.25:
        subs = [(me, kk, nn) if c is weakest[0] else (c, kk, nn) for c, kk, nn in subs]
    return starters, subs


# ══════════════════════════════════════════════════════════════════
# 명단 저장
# ══════════════════════════════════════════════════════════════════
def _write_squad(tournament_id, country, plan):
    """intl_squad(AI만 — 내 선수는 intl_squad에 안 들어간다)·intl_formations·후보 탈락·메타.
    같은 대회·나라 명단이 이미 있으면 통째로 바꾼다(아직 경기 전 — 발탁 수락 직후 재선발)."""
    import database as _db
    from database import set_intl_squad_starters
    conn = get_conn()
    _db._INTL_APPS_SUPPRESS = True   # 올림픽 행은 A매치 통산 출전 캐시와 무관(아래 주석)
    try:
        conn.execute("DELETE FROM intl_squad WHERE tournament_id=? AND country=?",
                     (tournament_id, country))
        rows = [(tournament_id, country, c["id"], c.get("position") or "")
                for c, _s in plan["starters"] if not c.get("is_me")]
        rows += [(tournament_id, country, c["id"], c.get("position") or "")
                 for c, _k in plan["subs"] if not c.get("is_me")]
        conn.executemany(
            "INSERT OR IGNORE INTO intl_squad(tournament_id, country, player_id, appearances, "
            "position) VALUES (?,?,?,0,?)", rows)
        conn.execute("INSERT OR REPLACE INTO intl_formations(tournament_id, country, formation, "
                     "manager_id) VALUES(?,?,?,?)", (tournament_id, country, plan["formation"],
                                                    int(plan.get("manager_id") or 0)))
        conn.execute("DELETE FROM oly_reserve_cut WHERE tournament_id=? AND country=?",
                     (tournament_id, country))
        conn.executemany(
            "INSERT INTO oly_reserve_cut(tournament_id, country, rank, player_id, score, position, "
            "category) VALUES(?,?,?,?,?,?,?)",
            [(tournament_id, country, rk, c["id"], round(float(c["_score"]), 2),
              c.get("position") or "", k) for rk, (c, k) in enumerate(plan["reserve"], 1)])
        conn.execute("DELETE FROM oly_squad_roles WHERE tournament_id=? AND country=?",
                     (tournament_id, country))
        role_rows = []
        for c, s in plan["starters"]:
            role_rows.append((tournament_id, country, c["id"], slot_category(plan["formation"], s),
                              1, s, int(c["position"] in natural_positions_for_slot(s))))
        for c, k in plan["subs"]:
            role_rows.append((tournament_id, country, c["id"], k, 0, "",
                              int(plan.get("sub_natural", {}).get(id(c), True))))
        conn.executemany("INSERT OR REPLACE INTO oly_squad_roles(tournament_id, country, player_id, "
                         "category, starter, slot, natural) VALUES(?,?,?,?,?,?,?)", role_rows)
        conn.execute("INSERT OR REPLACE INTO oly_squad_meta(tournament_id, country, formation, "
                     "max_age, extended, ovr, me_in) VALUES(?,?,?,?,?,?,?)",
                     (tournament_id, country, plan["formation"], plan["max_age"],
                      plan["extended"], plan["ovr"], int(plan["me_in"])))
        conn.commit()
    finally:
        _db._INTL_APPS_SUPPRESS = False
        conn.close()
    # 주전 11명(AI) 고정 — 내 선수가 주전이면 그 자리는 AI 교체 중 그 자리에 가장 맞는
    # 선수로 채워 둔다(성인 대표팀과 같다: AI 주전 11 + 내 경기에서만 merge_my_slot이 치환).
    ai_starters = [(c, s) for c, s in plan["starters"] if not c.get("is_me")]
    if plan["me_starter"]:
        my_slot = next(s for c, s in plan["starters"] if c.get("is_me"))
        bench = [c for c, _k in plan["subs"] if not c.get("is_me")]
        is_gk = (my_slot == "GK")
        bench = [c for c in bench if (c["position"] == "GK") == is_gk] or bench
        if bench:
            rep = max(bench, key=lambda c: (_eff(c, my_slot), -c["id"]))
            ai_starters.append((rep, my_slot))
    if ai_starters:
        set_intl_squad_starters(tournament_id, country, [c["id"] for c, _s in ai_starters],
                                slots={c["id"]: s for c, s in ai_starters})
    try:
        from intl_engine import _intl_formation_cache
        _intl_formation_cache.pop((tournament_id, country), None)
    except Exception:
        pass


def store_squad(tournament_id, country, plan=None):
    """명단 저장. plan이 None이면 AI만으로 그 자리에서 다시 짠다 — 대회 생성 시점 명단은
    항상 AI만이다(내 발탁은 발탁창 수락 뒤 choose_national_team이 나를 넣어 다시 뽑는다)."""
    if plan is None:
        conn = get_conn()
        r = conn.execute("SELECT year, kind FROM intl_tournaments WHERE id=?",
                         (tournament_id,)).fetchone()
        conn.close()
        plan = select_squad(country, r["year"], r["kind"], include_me=False)
    _write_squad(tournament_id, country, plan)
    return plan


# ══════════════════════════════════════════════════════════════════
# 대회 생성 — 예선(겨울) · 본선(연말)
# ══════════════════════════════════════════════════════════════════
def start_qual_if_needed(year):
    """game_engine 훅 — 월드컵 예선과 같은 시점(INTL_QUAL_WEEK − 1 진입)에 호출."""
    if not is_olympic_year(year):
        return
    from game_engine import get_player, update_player, add_log
    from intl_engine import get_tournaments, _clear_entry_cache, _gather_nat_context
    p = get_player()
    if not p:
        return
    if [t for t in get_tournaments(year) if t["kind"] == OLY_QUAL_KIND]:
        return
    # 새 올림픽 사이클 — 올림픽 징계·경고 카운터를 비운다(성인 대표팀 카운터와 별개).
    try:
        update_player(oly_suspension=0, season_yellow_oly=0, yellow_susp_progress_oly=0)
    except Exception:
        pass
    _clear_entry_cache()
    try:
        from competition.oly_manager import ensure_seeded   # [2026-10 2단계] U-23 감독
        ensure_seeded(year)
    except Exception as e:
        print(f"[U23-MGR] 감독 선임 실패(감독 없이 진행): {e}")
    my_nats, nat_info, committed = _gather_nat_context(p)
    for conf in CONFS:
        try:
            _create_qual(year, conf, p, my_nats, nat_info, committed)
        except Exception as e:
            import traceback
            traceback.print_exc()
            add_log(f"⚠ {qual_name(year, conf)} 생성 오류: {e}", "event")


def _my_cand_nats(p, nats_in_scope, committed):
    if committed:
        return [committed] if committed in nats_in_scope else []
    return [n for n in nats_in_scope]


def _create_qual(year, conf, p, my_nats, nat_info, committed):
    from game_engine import add_log
    from intl_engine import (_conf_countries, _qual_group_labels, _qual_round_day,
                             _mil_block_sel, _save_trophy)
    from constants import CONFEDERATIONS, day_to_week
    cfg = qual_cfg(conf)
    if not cfg:
        return
    name = qual_name(year, conf)
    two_stage = ("stage2" in cfg)
    rows = _conf_countries(conf)
    if len(rows) < 4:
        return
    cont_set = set(CONFEDERATIONS.get(conf, [conf]))
    in_scope = [n for n in my_nats if n and nat_info.get(n, {}).get("continent") in cont_set]
    cand_nats = _my_cand_nats(p, in_scope, committed) if my_eligible(p, year) else []
    plans = {}
    for r in rows:
        mine = r["name"] in cand_nats
        plan = select_squad(r["name"], year, OLY_QUAL_KIND, p=p if mine else None,
                            include_me=mine, allow_flip=False)
        # 내가 후보인 나라: 내가 뽑힐 실력이면 그 나라 전력에 내 OVR이 들어간다(성인 예선의
        # _apply_my_ovr_to_qual_rows와 같은 취지). 실제 명단은 발탁 수락 때 다시 뽑는다.
        r["ovr"] = plan["ovr"]
        plans[r["name"]] = plan
    rows.sort(key=lambda r: r["ovr"], reverse=True)
    cutoff_n = cfg.get("cutoff_bottom", 0)
    top_rows = rows[: len(rows) - cutoff_n]
    cut_names = {r["name"] for r in rows[len(rows) - cutoff_n:]}

    # 내 발탁 — 성인 예선과 같은 흐름: 고정 국적이 컷오프면 바로 "예선 진출 실패",
    # 아니면 발탁창(수락하면 그때 컷오프·선발 판정).
    my_sel, my_nat, cand_final = 2, "", []
    if cand_nats:
        if committed and committed in cut_names:
            _save_trophy(year, committed, name, "예선 진출 실패")
            cc = get_conn()
            cc.execute("""INSERT INTO intl_history(year, competition, team_name, result,
                                                   goals, assists, caps, rating)
                          VALUES(?,?,?,?,?,?,?,?)""",
                       (year, name, committed, "예선 진출 실패", 0, 0, 0, 0.0))
            cc.commit()
            cc.close()
            add_log(f"❌ {committed} {name} 진출 실패 (전력 하위권)", "event")
        else:
            my_sel, cand_final = 3, list(cand_nats)

    pool = list(top_rows)
    random.shuffle(pool)
    n_groups = cfg["n_groups"]
    group_size = cfg["group_size"]
    legs = cfg.get("legs", 2)
    pool.sort(key=lambda r: r["ovr"], reverse=True)
    glabels = _qual_group_labels(n_groups)
    groups = {g: [] for g in glabels}
    for pot in range(group_size):
        segment = pool[pot * n_groups:(pot + 1) * n_groups]
        random.shuffle(segment)
        for gi, e in enumerate(segment):
            if gi < n_groups:
                groups[glabels[gi]].append(e)

    status0 = "qual_stage1" if two_stage else "qual_group"
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO intl_tournaments(year, kind, name, status, my_selected, my_nat,
                 cand_nats, continent) VALUES(?,?,?,?,?,?,?,?)""",
              (year, OLY_QUAL_KIND, name, status0, _mil_block_sel(my_sel), my_nat,
               ",".join(cand_final), conf))
    tid = c.lastrowid
    cand_set = set(cand_final)
    for g, members in groups.items():
        for e in members:
            c.execute("""INSERT INTO intl_entries
                         (tournament_id, country, flag, grade, ovr, grp, is_my, continent)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (tid, e["name"], e.get("flag", ""), e.get("grade", "F"),
                       round(e["ovr"], 1), g, 1 if e["name"] in cand_set else 0,
                       e.get("continent", "")))

    def _rr(names):
        t = list(names)
        if len(t) % 2 == 1:
            t.append(None)
        nt = len(t)
        out = []
        for _ in range(nt - 1):
            out.append([(t[i], t[nt - 1 - i]) for i in range(nt // 2)
                        if t[i] is not None and t[nt - 1 - i] is not None])
            t = [t[0]] + [t[-1]] + t[1:-1]
        return out

    for g, members in groups.items():
        fwd = _rr([e["name"] for e in members])
        rounds = fwd + ([[(a, h) for h, a in rnd] for rnd in fwd] if legs >= 2 else [])
        for rnd_idx, pairs in enumerate(rounds):
            day = _qual_round_day(year, OLY_QUAL_KIND, rnd_idx)
            for h, a in pairs:
                c.execute("""INSERT INTO intl_matches
                             (tournament_id, week, day, stage, grp, home, away, is_my, my_played)
                             VALUES(?,?,?,?,?,?,?,?,?)""",
                          (tid, day_to_week(day), day, "qual_group", g, h, a,
                           1 if (h in cand_set or a in cand_set) else 0, 0))
    conn.commit()
    conn.close()
    for r in top_rows:
        # 내가 후보였던 나라는 AI만으로 다시 짜서 저장(plans 쪽은 내 OVR 반영 전력 계산용).
        store_squad(tid, r["name"], None if r["name"] in cand_nats else plans[r["name"]])
    add_log(f"🏅 {name} 조 추첨 완료 ({n_groups}개 조)", "event")
    if my_sel == 3:
        add_log(f"   🏅 U-23 올림픽 대표팀 발탁 후보: {', '.join(cand_final)}", "event")


def start_finals_if_needed(year):
    """game_engine 훅 — 월드컵 본선과 같은 시점(INTL_CALLUP_WEEK − 1 진입)에 호출.
    본선 명단은 여기서 다시 뽑는다(예선 명단 그대로 아님)."""
    if not is_olympic_year(year):
        return
    from game_engine import get_player, add_log
    from intl_engine import get_tournaments, _clear_entry_cache
    p = get_player()
    if not p:
        return
    if [t for t in get_tournaments(year) if t["kind"] == OLY_KIND]:
        return
    try:
        from competition.oly_manager import ensure_seeded   # [2026-10 2단계] 예선 뒤에 켠 세이브
        ensure_seeded(year)
    except Exception as e:
        print(f"[U23-MGR] 감독 선임 실패(감독 없이 진행): {e}")
    _clear_entry_cache()
    try:
        _create_finals(year, p)
    except Exception as e:
        import traceback
        traceback.print_exc()
        add_log(f"⚠ {finals_name(year)} 생성 오류: {e}", "event")


def _qualified_entries(year):
    from game_engine import add_log
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT country, flag, grade, ovr, continent FROM qual_results "
        "WHERE target_year=? AND kind=? ORDER BY id", (year, OLY_KIND)).fetchall()]
    conn.close()
    picked, seen = [], set()
    for conf in CONFS:
        q = C.WC_QUOTA.get(conf, 0)
        lst = []
        for r in rows:
            if r["continent"] == conf and r["country"] not in seen:
                lst.append(r)
                seen.add(r["country"])
        if len(lst) < q:
            add_log(f"[오류] {finals_name(year)}: {conf} 예선 통과 {len(lst)}팀 < 쿼터 {q}팀", "event")
        picked.extend(lst[:q])
    return picked


def _create_finals(year, p):
    from game_engine import add_log, update_player
    from intl_engine import (_GROUP_LABELS, _GROUP_ROUNDS, _precreate_ko_shell,
                             _mil_block_sel, get_tournaments)
    from constants import (get_stage_rule, stage_round_start_day, assign_match_days,
                           week_to_day, day_to_week, INTL_GROUP_WEEKS, INTL_CALLUP_WEEK)
    entries = _qualified_entries(year)
    if len(entries) < 4:
        add_log(f"⚠ {finals_name(year)}: 본선 진출국 부족({len(entries)}) — 대회를 열지 않습니다", "event")
        return
    names = {e["country"] for e in entries}
    for e in entries:
        e["name"] = e["country"]

    # 내 발탁 후보 — 예선에 실제로 뽑힌 나라가 있으면 그 나라만(같은 올림픽 안 일관성).
    played = [t for t in get_tournaments(year)
              if t["kind"] == OLY_QUAL_KIND and t.get("my_selected") == 1 and t.get("my_nat")]
    committed = (p.get("intl_committed") or "").strip()
    if not my_eligible(p, year):
        cand = []
    elif played:
        cand = [played[0]["my_nat"]] if played[0]["my_nat"] in names else []
    elif committed:
        cand = [committed] if committed in names else []
    else:
        mine = [n for n in (p.get("nationality"), p.get("nationality2"), p.get("nationality3"),
                            p.get("nationality4")) if n]
        cand = [n for n in mine if n in names]
    my_sel = 3 if cand else 2

    plans = {}
    for e in entries:
        mine = e["country"] in cand
        plan = select_squad(e["country"], year, OLY_KIND, p=p if mine else None,
                            include_me=mine, allow_flip=False)
        e["ovr"] = plan["ovr"]
        plans[e["country"]] = plan

    # 본선 시작 — 경고 누적만 초기화(예선 퇴장으로 남은 출전정지 oly_suspension은 넘긴다).
    try:
        update_player(season_yellow_oly=0, yellow_susp_progress_oly=0)
    except Exception:
        pass

    name = C.OLYMPIC_NAME   # 대회 행 이름(연도 없음) — 표시는 finals_name(year)
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO intl_tournaments(year, kind, name, status, my_selected, my_nat,
                 cand_nats, continent) VALUES(?,?,?,?,?,?,?,?)""",
              (year, OLY_KIND, name, "group", _mil_block_sel(my_sel), "", ",".join(cand), ""))
    tid = c.lastrowid
    entries.sort(key=lambda e: e["ovr"], reverse=True)
    n_groups = 8
    groups = {g: [] for g in _GROUP_LABELS[:n_groups]}
    pot_size = len(entries) // 4
    for pot in range(4):
        pool = entries[pot * pot_size:(pot + 1) * pot_size]
        random.shuffle(pool)
        for gi, e in enumerate(pool):
            g = _GROUP_LABELS[gi]
            groups[g].append(e)
            c.execute("""INSERT INTO intl_entries
                         (tournament_id, country, flag, grade, ovr, grp, pot, alive, is_my, continent)
                         VALUES(?,?,?,?,?,?,?,1,?,?)""",
                      (tid, e["country"], e.get("flag", ""), e.get("grade", "F"), e["ovr"], g,
                       pot + 1, 1 if e["country"] in cand else 0, e.get("continent", "")))
    tournament_type = "world_cup_32"
    w0 = INTL_GROUP_WEEKS[0]
    start_day = week_to_day(w0)
    my_set = set(cand)
    for rd in range(len(_GROUP_ROUNDS)):
        round_matches = [(g, hi, ai) for g in groups for hi, ai in _GROUP_ROUNDS[rd]]
        rule = get_stage_rule(tournament_type, "group", rd + 1)
        if rule:
            rs = stage_round_start_day(tournament_type, "group", rd + 1, start_day)
            day_list = assign_match_days(rs, len(round_matches), rule["cap"])
        else:
            day_list = [None] * len(round_matches)
        for (g, hi, ai), day_val in zip(round_matches, day_list):
            home, away = groups[g][hi], groups[g][ai]
            wk = day_to_week(day_val) if day_val is not None else (w0 + rd)
            c.execute("""INSERT INTO intl_matches
                         (tournament_id, stage, grp, week, day, home, away,
                          home_score, away_score, is_my, slot)
                         VALUES(?,?,?,?,?,?,?,-1,-1,?,0)""",
                      (tid, "group", g, wk, day_val, home["country"], away["country"],
                       1 if (home["country"] in my_set or away["country"] in my_set) else 0))
    _precreate_ko_shell(conn, c, tid, tournament_type, start_day)
    conn.commit()
    conn.close()
    for e in entries:
        store_squad(tid, e["country"], None if e["country"] in cand else plans[e["country"]])
    try:
        from competition.oly_manager import fix_objectives   # [2026-10 2단계] U-23 감독 평가 목표
        fix_objectives(tid)
    except Exception as e:
        print(f"[U23-MGR] 목표 고정 실패(평가 때 다시): {e}")
    add_log("─" * 44, "sep")
    add_log(f"🏅 {finals_name(year)} 개막!  본선 {len(entries)}개국", "event", year, INTL_CALLUP_WEEK)
    if my_sel == 3:
        add_log(f"   🏅 {' / '.join(cand)} U-23 올림픽 대표팀에서 발탁을 제안합니다! 출전 여부를 선택하세요",
                "event", year, INTL_CALLUP_WEEK)


# ══════════════════════════════════════════════════════════════════
# 발탁 수락(성인 대표팀 발탁창 재사용 — intl_engine.choose_national_team이 위임)
# ══════════════════════════════════════════════════════════════════
def choose_national_team(tournament_id, nat):
    from game_engine import get_player, add_log
    from intl_engine import _save_trophy, _entry_cache
    p = get_player()
    if not p:
        return None
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM intl_tournaments WHERE id=?", (tournament_id,)).fetchone())
    in_entries = bool(conn.execute(
        "SELECT 1 FROM intl_entries WHERE tournament_id=? AND country=? LIMIT 1",
        (tournament_id, nat)).fetchone())
    conn.close()
    year, name, kind = t["year"], t["name"], t["kind"]

    def _set(sel, my_nat=nat):
        cc = get_conn()
        cc.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=? WHERE id=?",
                   (my_nat, sel, tournament_id))
        cc.commit()
        cc.close()

    if not in_entries:
        # 예선 컷오프(경기 없이 탈락) — 본선은 진출국만 후보라 여기 안 온다.
        _set(2)
        _save_trophy(year, nat, name, "예선 진출 실패")
        add_log(f"❌ {nat} {name} 진출 실패 (전력 하위권)", "event")
        return {"nat": nat, "selected": False, "qualified": False, "result": "예선진출실패",
                "kind": kind}

    plan = select_squad(nat, year, kind, p=p, include_me=True, allow_flip=True)
    if not plan["me_in"]:
        _set(0)
        if plan["me_reserve"]:
            # 후보 탈락 4명 안에 내가 들었다 — 그 나라 후보 탈락 명단도 나를 넣어 다시 저장한다
            # (최종 18명은 내가 없을 때와 같다).
            store_squad(tournament_id, nat, plan)
            _save_trophy(year, nat, name, "U-23 올림픽 대표팀 후보 탈락")
            add_log(f"📋 {nat} U-23 올림픽 대표팀 후보 탈락", "event")
        return {"nat": nat, "selected": False, "qualified": True, "result": "미선발", "kind": kind,
                "reserve": bool(plan["me_reserve"])}

    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=1 WHERE id=?",
                 (nat, tournament_id))
    conn.execute("UPDATE intl_matches SET is_my=0 WHERE tournament_id=?", (tournament_id,))
    conn.execute("UPDATE intl_matches SET is_my=1 WHERE tournament_id=? AND (home=? OR away=?)",
                 (tournament_id, nat, nat))
    conn.execute("UPDATE intl_entries SET is_my=CASE WHEN country=? THEN 1 ELSE 0 END "
                 "WHERE tournament_id=?", (nat, tournament_id))
    conn.execute("UPDATE intl_entries SET ovr=? WHERE tournament_id=? AND country=?",
                 (plan["ovr"], tournament_id, nat))
    # 같은 해 다른 올림픽 발탁 대기만 닫는다(다른 대회 발탁창은 그대로).
    closed = [r[0] for r in conn.execute(
        f"SELECT id FROM intl_tournaments WHERE year=? AND id<>? AND my_selected=3 "
        f"AND kind IN {OLY_KINDS_SQL}", (year, tournament_id)).fetchall()]
    if closed:
        ph = ",".join("?" * len(closed))
        conn.execute(f"UPDATE intl_tournaments SET my_selected=2 WHERE id IN ({ph})", closed)
        conn.execute(f"UPDATE intl_matches SET is_my=0 WHERE tournament_id IN ({ph})", closed)
    conn.commit()
    conn.close()
    _entry_cache.pop((tournament_id, nat), None)
    store_squad(tournament_id, nat, plan)
    add_log(f"🏅 {nat} U-23 올림픽 대표팀 발탁! ({year} {name})" if kind == OLY_KIND else
            f"🏅 {nat} U-23 올림픽 대표팀 발탁! ({name})", "event")
    return {"nat": nat, "selected": True, "qualified": True, "result": "선발", "kind": kind}


# ══════════════════════════════════════════════════════════════════
# 대회 종료 · 병역
# ══════════════════════════════════════════════════════════════════
def medalists(conn, tid):
    """(금, 은, 동) 나라 — 결승·동메달 결정전 결과."""
    from intl_engine import _winner_of
    out = [None, None, None]
    f = conn.execute("SELECT * FROM intl_matches WHERE tournament_id=? AND stage='F' "
                     "AND home_score>=0 ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if f:
        f = dict(f)
        w = _winner_of(f)
        out[0], out[1] = w, (f["away"] if w == f["home"] else f["home"])
    tp = conn.execute("SELECT * FROM intl_matches WHERE tournament_id=? AND stage='TP' "
                      "AND home_score>=0 ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if tp:
        out[2] = _winner_of(dict(tp))
    return out


_MEDAL = ("금메달", "은메달", "동메달")


def check_my_exemption(p=None):
    """내 선수 — 한국 U-23 올림픽 최종 18명으로 메달이면 미필 → 병역 혜택(효과 = 면제)."""
    from game_engine import get_player, update_player, add_log, get_state
    from constants import MILITARY_STATUS_UNSERVED, MILITARY_STATUS_EXEMPT
    from military_service import military_enabled, MILITARY_TARGET_NATIONALITY as _K
    p = p or get_player()
    if not p or not enabled() or not military_enabled():
        return p
    from game_engine import _my_mil_status
    if _my_mil_status(p) != MILITARY_STATUS_UNSERVED:
        return p
    conn = get_conn()
    hit = None
    try:
        for tid, name, my_nat in conn.execute(
                "SELECT id, name, COALESCE(my_nat,'') FROM intl_tournaments WHERE kind=? "
                "AND my_selected=1 AND status='done' ORDER BY year, id", (OLY_KIND,)).fetchall():
            if my_nat != _K:
                continue
            med = medalists(conn, tid)
            if _K in med:
                hit = (tid, name, _MEDAL[med.index(_K)])
                break
        if hit:
            conn.execute("INSERT OR REPLACE INTO oly_military_benefit(player_id, year, tournament_id, "
                         "result) VALUES(?,?,?,?)",
                         (_ME, (get_state() or {}).get("current_year"), hit[0], hit[2]))
            conn.commit()
    finally:
        conn.close()
    if not hit:
        return p
    update_player(military_status=MILITARY_STATUS_EXEMPT)
    try:
        st = get_state() or {}
        add_log(f"🎖️ 병역 혜택! {hit[1]} {hit[2]} — 체육요원으로 편입됩니다.", "event",
                st.get("current_year"), st.get("current_week"))
    except Exception:
        pass
    return get_player() or p


def apply_ai_exemptions(conn, finished_year, stats):
    """military_service._apply_exemptions(새해 처리) 훅 — 그 해 끝난 올림픽 본선에서 한국이
    메달이면 한국 최종 명단(intl_squad, 출전 0분 포함) 중 미필 AI를 병역 혜택(면제)으로."""
    if not enabled():
        return
    from constants import MILITARY_STATUS_UNSERVED, MILITARY_STATUS_EXEMPT
    from military_service import MILITARY_TARGET_NATIONALITY as K
    for (tid,) in conn.execute(
            "SELECT t.id FROM intl_tournaments t JOIN intl_entries e ON e.tournament_id=t.id "
            "WHERE t.year=? AND t.kind=? AND t.status='done' AND e.country=?",
            (finished_year, OLY_KIND, K)).fetchall():
        med = medalists(conn, tid)
        if K not in med:
            continue
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM ai_players WHERE military_status=? AND id IN "
            "(SELECT player_id FROM intl_squad WHERE tournament_id=? AND country=?) ORDER BY id",
            (MILITARY_STATUS_UNSERVED, tid, K)).fetchall()]
        if not ids:
            continue
        ph = ",".join("?" * len(ids))
        conn.execute(f"UPDATE ai_players SET military_status=? WHERE id IN ({ph})",
                     (MILITARY_STATUS_EXEMPT, *ids))
        conn.executemany("INSERT OR REPLACE INTO oly_military_benefit(player_id, year, tournament_id, "
                         "result) VALUES(?,?,?,?)",
                         [(i, finished_year, tid, _MEDAL[med.index(K)]) for i in ids])
        stats["exempt"] = stats.get("exempt", 0) + len(ids)
        stats["oly_benefit"] = stats.get("oly_benefit", 0) + len(ids)


def on_season_end(year):
    """game_engine 시즌 전환 훅(클럽 감독 시장 뒤) — U-23 감독 정리·평가·선임(competition/oly_manager)."""
    if not enabled():
        return
    try:
        from competition.oly_manager import on_season_end as _mgr_season_end
        _mgr_season_end(year)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[U23-MGR] 연말 처리 실패(계속 진행): {e}")



def on_finish(t):
    """intl_engine._finish_tournament 훅(올림픽 본선) — 성인 대표팀 감독 평가·시상은 하지 않는다
    (U-23 감독 평가는 연말 oly_manager). 내 선수 병역 혜택을 판정하고, 올림픽 개인상을 정한다."""
    try:
        check_my_exemption()
    except Exception:
        pass
    try:
        compute_awards(t)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[OLY-AWARD] 올림픽 개인상 계산 실패(계속 진행): {e}")


# ══════════════════════════════════════════════════════════════════
# 3단계 — 올림픽 개인상과 발롱도르 [2026-10 신민용 확정]
#   · 상: 골든볼·실버볼·브론즈볼, 골든부트, 도움왕, 베스트11, 올해의 수비수, 골든글러브.
#     영플레이어상 없음. 실버·브론즈부트 없음. 본선 기록만으로 정한다.
#   · 선정 기준은 월드컵 개인상과 같은 공용 함수(참가도×평점×진출 단계)다. 본선이 끝나는
#     즉시 한 번 계산해 세계 기록(season_individual_awards, category='intl', 대회명
#     "U-23 올림픽")에 남기고, 내 선수 수상도 그 결과에서 그대로 꺼낸다 — 세계 기록실과
#     내 수상 목록이 어긋나지 않는다(월드컵·대륙컵은 예전 방식 그대로).
#   · 발롱도르: 대회 성적 점수 = 유럽 컨퍼런스리그(대륙 무관 기준)의 절반,
#     개인상 점수 = 컨퍼런스리그 개인상(클럽 대항전 MVP·베스트11)의 절반.
#     대회 성적은 국제대회 가산점(intl_bonus, 국적 내 순위 감쇠 적용) 경로로 들어가고,
#     A급 리그 "국제무대 증명" 게이트에는 인정하지 않는다.
# ══════════════════════════════════════════════════════════════════
OLY_AWARD_RENAME = {"MVP": "골든볼", "득점왕": "골든부트"}
_BALL_NAMES = ("골든볼", "실버볼", "브론즈볼")
# 올림픽 성적 → 클럽 대항전 진출 단계 라벨(_CLUB_COMP_STAGE_WEIGHT의 키). 동메달·4위는
# 클럽 대항전과 같이 "4강" 하나로 본다(클럽 대항전에도 3위 구분이 없다).
_STAGE_OF_RESULT = {"금메달": "우승", "은메달": "준우승", "동메달": "4강", "4위": "4강",
                    "8강 탈락": "8강", "16강 탈락": "16강", "조별리그 탈락": "조별탈락"}


def _finals_row(conn, year):
    return conn.execute("SELECT id, name, winner, my_selected, my_result, my_nat FROM intl_tournaments "
                        "WHERE year=? AND kind=? AND status='done' ORDER BY id LIMIT 1",
                        (year, OLY_KIND)).fetchone()


def country_stage(conn, tid, country):
    """그 나라가 올림픽 본선에서 간 단계 — 클럽 대항전 단계 라벨('우승'…'조별탈락') 또는 None."""
    gold, silver, _bronze = medalists(conn, tid)
    if country == gold:
        return "우승"
    if country == silver:
        return "준우승"
    stages = {r[0] for r in conn.execute(
        "SELECT DISTINCT stage FROM intl_matches WHERE tournament_id=? AND (home=? OR away=?) "
        "AND home_score>=0", (tid, country, country)).fetchall()}
    for st, label in (("TP", "4강"), ("SF", "4강"), ("QF", "8강"), ("R16", "16강")):
        if st in stages:
            return label
    return "조별탈락" if stages else None


def ballon_result_points(stage_label):
    """올림픽 성적의 발롱도르 대회 점수 = 유럽 컨퍼런스리그 같은 단계 점수 × 0.5."""
    if not stage_label:
        return 0.0
    import game_engine as G
    comp_w = G._CLUB_COMP_WEIGHT.get(C.OLYMPIC_BALLON_REF, 0.0) * C.OLYMPIC_BALLON_RATIO
    return comp_w * G._CLUB_COMP_STAGE_WEIGHT.get(stage_label, 0.0) * 10.0


_BALLON_CTX = {}   # (DB 경로, 연도, 대회 id, 우승국, 경기 결과 요약) → {나라: (단계, 그 나라 본선 경기 수)}


def _ballon_ctx(conn, year):
    import database
    t = _finals_row(conn, year)
    if not t:
        return None, None
    # 경기 결과 요약까지 키에 넣는다 — 같은 경로의 다른 세이브를 불러와 같은 해를 다시 치러도
    # 예전 결과를 재사용하지 않게(후보마다 한 번 도는 가벼운 집계 한 줄).
    sig = tuple(conn.execute("SELECT COUNT(*), COALESCE(SUM(home_score*37+away_score),0) FROM intl_matches "
                             "WHERE tournament_id=? AND home_score>=0", (t["id"],)).fetchone())
    key = (database.DB_PATH, int(year), t["id"], t["winner"], sig)
    ctx = _BALLON_CTX.get(key)
    if ctx is None:
        ctx = {}
        for (country,) in conn.execute("SELECT country FROM intl_entries WHERE tournament_id=?",
                                       (t["id"],)).fetchall():
            n = conn.execute("SELECT COUNT(*) FROM intl_matches WHERE tournament_id=? AND (home=? OR "
                             "away=?) AND home_score>=0", (t["id"], country, country)).fetchone()[0]
            ctx[country] = (country_stage(conn, t["id"], country), n)
        _BALLON_CTX[key] = ctx
    return t, ctx


def ballon_intl_bonus(conn, year, player_id, my_id):
    """game_engine._get_player_intl_bonus 훅 — 그 해 올림픽 본선 성적 점수 × 개인 기여.
    개인 기여 = 국제대회와 같은 출전 비율 3단계(_intl_participation_factor) × 평점 배율이고,
    평점 배율 상한은 컨퍼런스리그와 같은 값(_CLUB_COMP_QUALITY_CAP)이다. 나라는 국적이 아니라
    실제로 뛴 올림픽 나라(복수국적 미고정 선수). 올림픽이 없거나 꺼져 있으면 0."""
    if not enabled():
        return 0.0
    t, ctx = _ballon_ctx(conn, year)
    if not t:
        return 0.0
    if player_id == my_id:
        if t["my_selected"] != 1:
            return 0.0
        country = t["my_nat"] or ""
        row = conn.execute("SELECT COUNT(*) AS m, AVG(my_rating) AS r FROM intl_matches "
                           "WHERE tournament_id=? AND my_played=1", (t["id"],)).fetchone()
        matches, rating = (row["m"] or 0), row["r"]
    else:
        row = conn.execute("SELECT country, appearances, rating FROM intl_squad "
                           "WHERE tournament_id=? AND player_id=?", (t["id"], player_id)).fetchone()
        if not row:
            return 0.0
        country, matches, rating = row["country"], (row["appearances"] or 0), row["rating"]
    if not matches:
        return 0.0
    stage, team_n = ctx.get(country, (None, matches))
    pts = ballon_result_points(stage)
    if not pts:
        return 0.0
    import game_engine as G
    part = G._intl_participation_factor(matches, team_n or matches)
    cap = G._CLUB_COMP_QUALITY_CAP.get(C.OLYMPIC_BALLON_REF, 1.0)
    quality = max(0.2, min(cap, 1.0 + ((rating if rating is not None else 0.0) - 6.0) / 1.5))
    return pts * part * quality


def my_ballon_trophy_points(year):
    """game_engine._get_ballon_trophy_bonus(내 선수 예전 판정 경로) 훅 — 그 경로가 클럽 대항전을
    단계 점수 그대로 더하는 것과 같이, 내 올림픽 성적 점수를 그대로 돌려준다(뽑혔을 때만)."""
    if not enabled():
        return 0.0
    conn = get_conn()
    try:
        t = _finals_row(conn, year)
    finally:
        conn.close()
    if not t or t["my_selected"] != 1:
        return 0.0
    return ballon_result_points(_STAGE_OF_RESULT.get((t["my_result"] or "").strip()))


def ballon_award_bonus(c, year):
    """game_engine._get_award_recognition_bonus_map 훅 — 올림픽 개인상 가산점
    {player_id: 점수}. 컨퍼런스리그 개인상(클럽 대항전 MVP·베스트11) 점수의 절반이고, 실버볼·브론즈볼은
    골든볼의 0.5·0.2배(월드컵과 같은 비율). 득점·도움·골든글러브·올해의 수비수는 클럽 대항전과 같이 0."""
    out = {}
    if not enabled():
        return out
    import game_engine as G
    for r in c.execute(
            """SELECT sia.player_id AS player_id, sia.award_kind AS award_kind, sia.rank AS rank
               FROM season_individual_awards sia
               JOIN intl_tournaments it ON it.year = sia.year AND it.name = sia.competition
               WHERE sia.year=? AND sia.category='intl' AND it.kind=?
               ORDER BY sia.award_type, sia.rank""", (year, OLY_KIND)).fetchall():
        kind = {"골든볼": "MVP"}.get(r["award_kind"], r["award_kind"])
        base = G._BALLON_AWARD_BONUS.get(("club", kind), 0.0) * C.OLYMPIC_BALLON_RATIO
        if kind == "MVP":
            base *= G._BALLON_BALL_RANK_RATIO.get(r["rank"], 0.0)   # 실버볼 0.5 · 브론즈볼 0.2(월드컵과 같은 비율)
        if base:
            out[r["player_id"]] = round(out.get(r["player_id"], 0.0) + base, 4)
    return out


def compute_awards(t):
    """올림픽 본선이 끝난 직후 — 세계 개인상을 정해 저장하고, 내 선수 수상도 같은 결과로 남긴다.
    같은 대회를 두 번 처리해도 세계 기록은 INSERT OR IGNORE, 내 수상은 이미 있으면 건너뛴다."""
    if not enabled():
        return
    import game_engine as G
    tid = t["id"]
    G._clear_comp_match_count_cache()
    G._clear_award_identity_cache()
    conn = get_conn()
    c = conn.cursor()
    try:
        trow = c.execute("SELECT id, name, year, winner FROM intl_tournaments WHERE id=?", (tid,)).fetchone()
        if not trow:
            return
        year, name = trow["year"], trow["name"]
        pool = G._intl_award_pool(c, tid)
        if not pool:
            return
        stage_f = G._tournament_stage_factor_for(c, "intl_matches", tid, trow["winner"])
        denom = G._country_intl_match_counts(c, tid)
        mvp, scorer = G._pick_club_comp_mvp_and_scorer(pool, stage_f, denom, "stage_key")
        extra = G._pick_comp_extra_winners(pool, stage_factor=stage_f, part_denom=denom,
                                           part_key="stage_key")
        extra["young"] = None                     # 영플레이어상 없음
        balls = G._pick_top_n_mvp(pool, 3, stage_f, denom, "stage_key")
        G._save_club_comp_award_rows(c, year, name, mvp, scorer, category="intl", extra=extra,
                                     kind_rename=OLY_AWARD_RENAME, mvp_extra=balls[1:],
                                     stage_factor=stage_f, part_denom=denom, part_key="stage_key")
        conn.commit()
        # ── 내 선수 수상(세계 결과에서 그대로) ──
        me = G._SIA_MY_PLAYER_ID
        disp = finals_name(year)
        mine = []
        for i, b in enumerate(balls):
            if b["player_id"] == me:
                mine.append((f"{name} {_BALL_NAMES[i]}", disp))
        if scorer and scorer["player_id"] == me:
            mine.append((f"{name} 골든부트", f"{scorer['goals']}골"))
        a = extra.get("assist")
        if a and a["player_id"] == me:
            mine.append((f"{name} 도움왕", f"{a['assists']}도움"))
        if any(x["player_id"] == me for x in extra.get("best11") or []):
            mine.append((f"{name} 베스트11", f"{disp} 베스트11"))
        d = extra.get("defender")
        if d and d["player_id"] == me:
            mine.append((f"{name} 올해의 수비수", disp))
        g = extra.get("goalkeeper")
        if g and g["player_id"] == me:
            mine.append((f"{name} 골든글러브", f"{g.get('cs') or 0} 클린시트"))
        new = []
        for atype, detail in mine:
            if c.execute("SELECT 1 FROM awards WHERE year=? AND award_type=? AND is_mine=1",
                         (year, atype)).fetchone():
                continue
            c.execute("INSERT INTO awards(year,award_type,league_name,detail,is_mine) VALUES(?,?,?,?,1)",
                      (year, atype, name, detail))
            new.append((atype, detail))
        if new:
            conn.commit()
    finally:
        conn.close()
    if new:
        from game_engine import add_log
        for atype, detail in new:
            add_log(f"🏅 {atype} 수상! ({detail})", "event")