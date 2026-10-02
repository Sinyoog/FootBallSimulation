# -*- coding: utf-8 -*-
"""병역 시스템 / 군데스리가 — 공용 판정·스키마 정의 (2026-10 신설, 신민용 확정)

[1단계 범위] 이 모듈은 아직 "기반"만 담는다: 병역 컬럼 정의(+마이그레이션),
군 리그/팀 판정 함수, 국적·상태 헬퍼. 실제 입대/제대/승강/일정/UI는
다음 단계들에서 이 모듈 위에 얹는다. constants.MILITARY_ENABLED가 False인
동안에는 월드에 '군대' 국가 자체가 없으므로 아래 판정 함수들은 항상
"군대 아님"을 돌려주고, 기존 게임 결과는 하나도 바뀌지 않는다.

[확정 설계 요약 — 구현 단계에서 기준으로 삼을 것]
- 대상: 국적이 대한민국인 선수. 내 선수는 nationality~nationality4 중 하나라도
  한국이면 대상(어느 나라 대표팀을 골랐든 상관없음).
- 군데스리가: 가상 국가 "군대", 등급 B(로스터 23~26). 1부 육군 군단 6팀
  20경기, 2부 육군 사단 8팀 14경기. 2부 상위 4팀 단판 토너먼트 승자 vs 1부
  6위 단판 승강전 — 이기면 두 팀의 (1년차) 선수단을 맞교환(진급/강등),
  팀 자체는 고정. 순서: 시즌 종료 → 2년차 제대 → 맞교환 → 새해 입대.
  컵대회 이름은 "국군컵". 리그 개인상은 있지만 발롱도르엔 미반영,
  클럽대항전·파워랭킹·국대 선발(군인은 국대 안 뽑힘)에서 전부 제외.
- 입대: 매년 새해(1주차)에만, 2년, 평생 1번, 연봉 0원, 다른 이적/임대 없음.
  31세가 되는 새해까지 미필이면 강제 입대. 원 소속 계약이 2년 이상 남았으면
  "2년 임대" 형태(계약 기간은 복무 중에도 흐름), 짧으면 계약 해지 후 FA로
  입대 → 제대 후 FA. FA 선수는 이적료 없이 입단.
- 유형(입대 연령 범위): 바닥19~22 / 세미20~24 / 평범23~26 / 애매함24~28 /
  엘리트27~30. 매년 현재 소속 리그 등급·부수 × 역할 라벨(+국대)로 재판정.
  국대 단골(최근 2년 본선 최종 명단 1회+)은 30세까지 연기.
- 배정: K1 이상 수준이면 1부 우선(자리 없으면 2부). 군팀은 보충 생성 금지.
- 면제: 월드컵·아시안컵 준우승 이상, 동아시안컵 우승 — 최종 명단에만 들면 됨.
- 게임 시작: 월드를 평소대로 만든 뒤 "초기 입대"를 한 번 돌린다. 31세 이상
  군필, 그 아래는 유형 범위에서 가상 입대 나이를 뽑아 미필/복무1·2년차/군필
  판정. 복무 인원 364명(14×26) 초과분은 미루고, 구단당 최대 4명. 빈자리는
  일반 생성 경로로 같은 슬롯(포지션·역할 순번·목표 OVR, 한국 국적)을 채운다.
- 국적 변경: 한국→타국은 미필만 가능(복무중·군필·면제 불가)하며 "미필 이탈"
  플래그를 남긴다. 타국→한국은 귀화가 아니라 "원래 한국인" 취급 — 30세
  이하면 미필로 스케줄 재생성, 31세 이상이면 군필(단 미필 이탈 플래그가
  있으면 미필 → 다음 새해 강제 입대).
- 내 선수: 20세부터 오퍼 창 아래 군팀 2곳 표시, 31세 1주차 강제 입대 창
  (닫아도 강제 입단). 제대 후 평가절하 0.8(거의 못 뛰었으면 0.7), 제대 후
  10경기에 걸쳐 1.0 회복. 오퍼의 "직전 리그·평점"은 입대 전 기준.
- 화면: 커리어 표 국가=군대, 연봉 0원, 이적 칸 "입대"/"진급"/"강등",
  계약 칸은 입대 때만 "2년". 선수검색 상세 표 9번째 칸 "군대"(한국 선수만
  보임, 어떤 난이도에서도 편집 불가): 면제/미필/복무중/군필.
"""
from constants import (
    MILITARY_ENABLED, MILITARY_COUNTRY, MILITARY_TARGET_NATIONALITY,
    MILITARY_STATUS_NONE, MILITARY_STATUS_LABELS,
)

# ── 스키마: 병역 컬럼 (정의는 여기 한 곳에만) ─────────────────────────
# database.init_db()의 마이그레이션 루프가 military_migrations()로 ALTER를
# 받아 간다(_ET_SCORE_COLS / manager_rep_migrations와 같은 방식).
# ai_players_seed는 database의 컬럼 동기화 루프가 자동으로 따라온다.
# reset_game_data는 ai_players를 통째로 재생성하고 my_player를 DELETE 후
# 다시 만들므로 새 게임에서 이 값들은 전부 기본값으로 돌아간다.
#
#   military_status         병역 상태 코드(constants.MILITARY_STATUS_*), ""=대상 아님
#   military_enlist_age     가상 입대 나이(0=아직 미정)
#   military_enlist_year    실제 입대한 해(0=입대 전). 제대 = +MILITARY_SERVICE_YEARS
#   military_left_unserved  미필 상태로 한국 국적을 떠난 적 있음(1) — 타국→한국
#                           복귀 시 "31세 이상 군필" 규칙을 막는 플래그
#   military_pre_team_id    입대 직전 소속팀(0=FA로 입대)
#   military_pre_league_id  입대 직전 소속 리그 — 제대 후 "직전 리그" 기준
MILITARY_COMMON_COLS = (
    ("military_status", "TEXT DEFAULT ''"),
    ("military_enlist_age", "INTEGER DEFAULT 0"),
    ("military_enlist_year", "INTEGER DEFAULT 0"),
    ("military_left_unserved", "INTEGER DEFAULT 0"),
    ("military_pre_team_id", "INTEGER DEFAULT 0"),
    ("military_pre_league_id", "INTEGER DEFAULT 0"),
    ("military_pre_salary", "INTEGER DEFAULT 0"),   # 입대 직전 연봉(복무 중엔 0원) — 제대 때 복구
    # [2026-10 신민용 확정: "복무 중 은퇴가 되더라도 복무는 다 하고 은퇴"] 복무 중 은퇴가
    # 정해지면 1(예약). AI는 제대한 오프시즌의 은퇴 처리에서, 내 선수는 제대 때 은퇴.
    # 내 선수만 2를 쓴다(제대 처리 끝 — UI가 은퇴 창을 띄울 차례, 앱을 껐다 켜도 유지).
    ("military_retire_pending", "INTEGER DEFAULT 0"),
)
# [2026-10 신민용 확정] 복무 중 AI 은퇴 확률 = 일반 은퇴 확률 × 이 값. 군 복무 중엔 커리어
# 판단이 멈춘 상태라 군대에 갔다고 은퇴가 늘면 안 된다 — 낮추는 방향의 시작값.
MILITARY_RETIRE_PROB_MULT = 0.5
# 내 선수 전용(제대 후 평가절하):
#   military_devalue        제대 시점에 정해진 배율(1.0=평가절하 없음)
#   military_post_matches   제대 후 뛴 공식 경기 수(회복 진행도)
MILITARY_MY_PLAYER_COLS = MILITARY_COMMON_COLS + (
    ("military_devalue", "REAL DEFAULT 1.0"),
    ("military_post_matches", "INTEGER DEFAULT 0"),
)


def military_migrations():
    """init_db() 마이그레이션 루프에 붙일 ALTER 문 목록(멱등 — 이미 있으면
    duplicate column으로 실패하고 루프가 무시한다)."""
    # 승강 플레이오프 기록(연도·단계·홈·원정·승자). reset_game_data의 DELETE 목록에도 있음.
    out = ["CREATE TABLE IF NOT EXISTS military_po(id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER, "
           "stage TEXT, home_team_id INTEGER, away_team_id INTEGER, winner_team_id INTEGER, pso TEXT DEFAULT '', "
           "home_score INTEGER DEFAULT 0, away_score INTEGER DEFAULT 0, pso_score TEXT DEFAULT '')",
           "ALTER TABLE military_po ADD COLUMN home_score INTEGER DEFAULT 0",
           "ALTER TABLE military_po ADD COLUMN away_score INTEGER DEFAULT 0",
           "ALTER TABLE military_po ADD COLUMN pso_score TEXT DEFAULT ''"]
    out += [f"ALTER TABLE ai_players ADD COLUMN {n} {t}" for n, t in MILITARY_COMMON_COLS]
    out += [f"ALTER TABLE my_player ADD COLUMN {n} {t}" for n, t in MILITARY_MY_PLAYER_COLS]
    return out


# ── 군 리그/팀 판정 ────────────────────────────────────────────────
# 군팀 소속 리그는 바뀌지 않는다(진급/강등은 선수만 옮김)라서 id 집합을 한 번
# 읽어 재사용한다. DB가 바뀌는 시점(load_from_disk/reset_game_data)에 반드시
# invalidate_military_cache()로 버린다 — 이전 판의 id가 남으면 엉뚱한 팀을
# 군팀으로 볼 수 있다(team_id 재사용 구조).
_CACHE = {"country_id": None, "league_ids": None, "team_ids": None, "league_tier": None}


def invalidate_military_cache():
    _CACHE["country_id"] = None
    _CACHE["league_ids"] = None
    _CACHE["team_ids"] = None
    _CACHE["league_tier"] = None


def military_enabled() -> bool:
    # 테스트 하네스가 import 이후에 constants.MILITARY_ENABLED를 바꿔도 따라가도록
    # 매번 모듈 속성을 다시 읽는다(from-import 값은 import 시점에 고정됨).
    import constants as _c
    return bool(getattr(_c, "MILITARY_ENABLED", MILITARY_ENABLED))


def is_military_country(name) -> bool:
    return bool(name) and name == MILITARY_COUNTRY


def _load(conn):
    row = conn.execute("SELECT id FROM countries WHERE name=?", (MILITARY_COUNTRY,)).fetchone()
    cid = row[0] if row else 0
    if cid:
        _lt = {r[0]: r[1] for r in conn.execute(
            "SELECT id, tier FROM leagues WHERE country_id=?", (cid,)).fetchall()}
        lids = frozenset(_lt)
        tids = frozenset(r[0] for r in conn.execute(
            "SELECT id FROM teams WHERE country_id=?", (cid,)).fetchall())
    else:
        _lt = {}
        lids = frozenset()
        tids = frozenset()
    _CACHE["league_tier"] = _lt
    _CACHE["country_id"] = cid
    _CACHE["league_ids"] = lids
    _CACHE["team_ids"] = tids


def get_military_country_id(conn) -> int:
    """'군대' 국가 id. 월드에 없으면(기능 꺼짐/구세이브) 0."""
    if _CACHE["country_id"] is None:
        _load(conn)
    return _CACHE["country_id"]


def get_military_league_ids(conn) -> frozenset:
    if _CACHE["league_ids"] is None:
        _load(conn)
    return _CACHE["league_ids"]


def get_military_team_ids(conn) -> frozenset:
    if _CACHE["team_ids"] is None:
        _load(conn)
    return _CACHE["team_ids"]


def is_military_league(conn, league_id) -> bool:
    return bool(league_id) and league_id in get_military_league_ids(conn)


def is_military_team(conn, team_id) -> bool:
    return bool(team_id) and team_id in get_military_team_ids(conn)


# ── 국적·상태 헬퍼 ────────────────────────────────────────────────
def my_player_nationalities(row) -> list:
    """my_player 행(sqlite3.Row/dict)에서 비어 있지 않은 국적 목록."""
    out = []
    for key in ("nationality", "nationality2", "nationality3", "nationality4"):
        try:
            v = row[key]
        except (KeyError, IndexError):
            v = None
        if v and v not in out:
            out.append(v)
    return out


def is_service_target(nationalities) -> bool:
    """국적(문자열 하나 또는 목록) 중 하나라도 대한민국이면 병역 대상."""
    if not nationalities:
        return False
    if isinstance(nationalities, str):
        return nationalities == MILITARY_TARGET_NATIONALITY
    return MILITARY_TARGET_NATIONALITY in nationalities


def military_status_label(status) -> str:
    """상태 코드 → 화면 표시("미필"/"복무중"/"군필"/"면제"). 대상 아님은 ""."""
    if not status or status == MILITARY_STATUS_NONE:
        return ""
    return MILITARY_STATUS_LABELS.get(status, "")

# ── 목록 걸러내기(격리용) ───────────────────────────────────────────
# 이미 fetchall()로 읽은 행 목록에서 군팀/군 리그/군대 국가 행만 뺀다.
# SQL에 WHERE를 덧붙이지 않고 파이썬에서 거르는 이유: 쿼리 문자열이 바뀌면
# SQLite 실행계획 → ORDER BY 없는 결과의 행 순서 → 난수 소비 순서가 달라져
# 기능이 꺼져 있어도 결과가 바뀔 수 있다(프로젝트 교훈 20). 군대가 없으면
# 받은 리스트를 그대로(같은 객체) 돌려주므로 기존 동작과 100% 같다.
def _pick(row, key):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def drop_military_teams(conn, rows, key=0):
    """rows의 각 행에서 key(인덱스 또는 컬럼명)가 군팀 id인 행을 뺀다."""
    tids = get_military_team_ids(conn)
    if not tids:
        return rows
    return [r for r in rows if _pick(r, key) not in tids]


def drop_military_leagues(conn, rows, key=0):
    lids = get_military_league_ids(conn)
    if not lids:
        return rows
    return [r for r in rows if _pick(r, key) not in lids]


def drop_military_countries(rows, key=0):
    """rows의 각 행에서 key가 국가명 "군대"인 행을 뺀다(DB 조회 불필요)."""
    if not rows:
        return rows
    if not any(_pick(r, key) == MILITARY_COUNTRY for r in rows):
        return rows
    return [r for r in rows if _pick(r, key) != MILITARY_COUNTRY]


def manager_nationality_for(country) -> str:
    """감독 국적: 군팀 감독은 "군대"가 아니라 대한민국 국적으로 만든다."""
    return MILITARY_TARGET_NATIONALITY if is_military_country(country) else country


# ══════════════════════════════════════════════════════════════════
# 3단계: 유형 판정 · 가상 입대 나이 · 게임 시작 시 초기 입대
# ══════════════════════════════════════════════════════════════════
import random as _random
import zlib as _zlib
from collections import Counter

_STARTER_ROLES = ("핵심", "주전")


def compute_league_levels(conn) -> dict:
    """league_id → 그 리그 소속 선수 평균 OVR(군 리그 제외)."""
    mil = get_military_league_ids(conn)
    out = {}
    for lid, avg in conn.execute(
            "SELECT t.league_id, AVG(ap.ovr) FROM ai_players ap JOIN teams t ON ap.team_id=t.id "
            "GROUP BY t.league_id").fetchall():
        if lid in mil or avg is None:
            continue
        out[lid] = float(avg)
    return out


def korea_reference_levels(conn, levels) -> dict:
    """{1: K1 평균, 2: K2 평균, 3: K3 평균}."""
    refs = {}
    for lid, tier in conn.execute(
            "SELECT l.id, l.tier FROM leagues l JOIN countries cn ON cn.id=l.country_id "
            "WHERE cn.name=? AND l.tier<=3", (MILITARY_TARGET_NATIONALITY,)).fetchall():
        if lid in levels:
            refs[tier] = levels[lid]
    # 데이터가 비정상적으로 비어도 판정이 깨지지 않게 상수 폴백(constants 기준 대략값).
    refs.setdefault(1, 77.0)
    refs.setdefault(2, 66.0)
    refs.setdefault(3, 56.0)
    return refs


def judge_service_type(level, role, refs, national_regular=False) -> str:
    """리그 수준(평균 OVR) × 팀 내 역할 라벨(+국대 단골) → 입대 유형."""
    from constants import MILITARY_LEVEL_MARGIN as M, MILITARY_TOP_LEAGUE_GAP as GAP
    if national_regular:
        return "엘리트"
    role = role or ""
    starter = role in _STARTER_ROLES
    if level is None:
        return "세미"
    if level >= refs[1] + GAP:
        return "엘리트" if starter else ("애매함" if role == "로테이션" else "평범")
    if level >= refs[1] - M:
        return "엘리트" if role == "핵심" else ("애매함" if starter else "평범")
    if level >= refs[2] - M:
        return "평범" if starter else "세미"
    if level >= refs[3] - M:
        return "세미" if starter else "바닥"
    return "바닥"


def sample_enlist_age(rng, service_type, national_regular=False) -> int:
    """유형 범위에서 가상 입대 나이를 뽑는다 — 가운데로 몰리는 삼각 가중치
    (예: 27~30이면 1:2:2:1). 국대 단골은 30 고정."""
    from constants import MILITARY_TYPE_AGE_RANGE, MILITARY_NATIONAL_REGULAR_AGE
    if national_regular:
        return MILITARY_NATIONAL_REGULAR_AGE
    lo, hi = MILITARY_TYPE_AGE_RANGE.get(service_type, MILITARY_TYPE_AGE_RANGE["평범"])
    ages = list(range(lo, hi + 1))
    n = len(ages)
    weights = [min(i + 1, n - i) for i in range(n)]
    return rng.choices(ages, weights)[0]


def national_regular_ids(conn, year) -> set:
    """최근 MILITARY_NATIONAL_REGULAR_WINDOW_YEARS년 안에 본선 대회(월드컵·대륙컵·
    지역컵) 한국 최종 명단에 1번 이상 든 선수 id."""
    from constants import MILITARY_NATIONAL_REGULAR_WINDOW_YEARS as W
    try:
        rows = conn.execute(
            "SELECT DISTINCT s.player_id FROM intl_squad s JOIN intl_tournaments t ON t.id=s.tournament_id "
            "WHERE s.country=? AND t.kind IN ('world','continent','region') AND t.year BETWEEN ? AND ?",
            (MILITARY_TARGET_NATIONALITY, year - W, year - 1)).fetchall()
    except Exception:
        return set()
    return {r[0] for r in rows}


def compute_roles_for_teams(conn, team_ids) -> dict:
    """{player_id: 역할 라벨} — 팀 로스터 전체로 formation_logic.compute_squad_roles를
    돌린다(시즌 기록이 없는 게임 시작 시점용)."""
    from formation_logic import compute_squad_roles
    team_ids = sorted(set(t for t in team_ids if t))
    out = {}
    for i in range(0, len(team_ids), 500):
        part = team_ids[i:i + 500]
        ph = ",".join("?" * len(part))
        pools = {}
        for pid, tid, pos, ovr, age in conn.execute(
                f"SELECT id, team_id, position, ovr, age FROM ai_players WHERE team_id IN ({ph}) ORDER BY id",
                part).fetchall():
            pools.setdefault(tid, []).append((pid, pos, ovr, age))
        for tid in part:
            if tid in pools:
                out.update(compute_squad_roles(pools[tid]))
    return out


def _pos_group(pos):
    pos = (pos or "").upper()
    if pos == "GK":
        return "GK"
    if pos in ("CB", "LB", "RB", "LWB", "RWB", "SW"):
        return "DF"
    if pos in ("ST", "CF", "LW", "RW", "SS"):
        return "FW"
    return "MF"


def _rng_for(conn, tag):
    try:
        from database import get_world_salt
        salt = get_world_salt()
    except Exception:
        salt = 0
    return _random.Random(_zlib.crc32(f"{tag}:{salt}".encode("utf-8")))


def run_initial_enlistment(conn, start_year) -> dict:
    """[3단계] 새 게임 시작 직후(월드 생성 → 감독 생성 전) 한 번 돌려서
    "군데스리가가 이미 돌아가고 있던 세계"를 만든다(신민용 확정).

    1) 한국 국적 AI 선수 전원의 병역 상태를 정한다: 31세 이상 군필, 그 아래는
       유형(리그 수준×역할)별 범위에서 가상 입대 나이를 뽑아 현재 나이와 비교 —
       더 크면 미필, 같으면 복무 1년차(올해 입대), 1 작으면 2년차(작년 입대),
       그보다 작으면 군필.
    2) 복무자 상한: 구단당 MILITARY_INITIAL_CLUB_CAP명, 전체 14팀×26명.
       넘치는 인원은 입대를 1년 미뤄 미필로 돌린다(나이대 한쪽으로 몰리지
       않게 무작위로 고름). 모자라면 그대로 둔다(군팀 보충 생성 금지).
    3) 배정: K1 이상 수준 리그 출신부터 1부(군단)로, 팀당 인원은 14팀에 고르게,
       같은 부 안에서는 포지션군(GK/DF/MF/FW)이 고르게 퍼지도록.
    4) 원 소속 계약: 복무 기간을 넘겨 남아 있으면(계약 만료 연도 ≥ 입대 연도+1)
       원 소속팀과 계약 유지 → 2년 임대(복귀 연도 = 입대 연도+1, 기존
       _process_loan_returns 규약과 같은 의미). 짧으면 계약 해지 → FA로 입대.
       임대 중이던 선수는 그 임대를 끝내고 원 소속(부모) 구단 기준으로 본다.
    5) 빈자리: 군인이 빠진 바로 그 자리에 같은 포지션·같은 OVR·같은 스탯의
       한국 국적 선수를 새로 넣는다(신인 보충이 아니라 "이미 그 자리를 메우고
       뛰던 선수" — 팀 전력 불변). 이 선수는 복무 중이 될 수 없다(연쇄 입대 방지).
    6) 입대 기록을 ai_transfer_log에 transfer_type='입대'로 남긴다(is_loan=0 —
       FIFA 임대 한도 계산에 안 잡히게).
    반환: 통계 dict."""
    from constants import (MILITARY_STATUS_SERVED, MILITARY_STATUS_UNSERVED, MILITARY_STATUS_SERVING,
                           MILITARY_FORCED_AGE, MILITARY_INITIAL_CLUB_CAP, MILITARY_ROSTER_RANGE,
                           MILITARY_LEVEL_MARGIN)
    invalidate_military_cache()
    stats = {"korean": 0, "served": 0, "unserved": 0, "serving": 0, "postponed_club": 0,
             "postponed_total": 0, "loan": 0, "fa": 0, "div1": 0, "div2": 0, "fill": 0}
    mil_cid = get_military_country_id(conn)
    if not mil_cid:
        return stats
    mil_teams = {tid: tier for tid, tier in conn.execute(
        "SELECT t.id, l.tier FROM teams t JOIN leagues l ON l.id=t.league_id WHERE t.country_id=? ORDER BY t.id",
        (mil_cid,)).fetchall()}
    div1 = [t for t, tr in mil_teams.items() if tr == 1]
    div2 = [t for t, tr in mil_teams.items() if tr != 1]
    if not div1 or not div2:
        return stats

    levels = compute_league_levels(conn)
    refs = korea_reference_levels(conn, levels)
    league_of = {tid: lid for tid, lid in conn.execute("SELECT id, league_id FROM teams").fetchall()}
    regulars = national_regular_ids(conn, start_year)
    rows = conn.execute(
        "SELECT id, team_id, age, ovr, position, contract_end_year, on_loan_from_team_id, name, salary "
        "FROM ai_players WHERE nationality=? ORDER BY id", (MILITARY_TARGET_NATIONALITY,)).fetchall()
    rows = [r for r in rows if r[1] not in mil_teams]
    pname = {r[0]: r[7] or "" for r in rows}
    psal = {r[0]: r[8] or 0 for r in rows}
    stats["korean"] = len(rows)
    roles = compute_roles_for_teams(conn, [r[1] for r in rows])

    plan = {}      # pid -> dict
    for pid, tid, age, ovr, pos, cend, loan_from, _nm, _sal in rows:
        rng = _rng_for(conn, f"milinit:{pid}")
        age = age or 0
        lv = levels.get(league_of.get(tid))
        reg = pid in regulars
        st_type = judge_service_type(lv, roles.get(pid), refs, reg)
        if age >= MILITARY_FORCED_AGE:
            plan[pid] = {"status": MILITARY_STATUS_SERVED, "enlist_age": 0, "type": st_type}
            continue
        ea = sample_enlist_age(rng, st_type, reg)
        if ea > age:
            plan[pid] = {"status": MILITARY_STATUS_UNSERVED, "enlist_age": ea, "type": st_type}
        elif ea >= age - 1:
            plan[pid] = {"status": MILITARY_STATUS_SERVING, "enlist_age": ea, "type": st_type,
                         "enlist_year": start_year - (age - ea), "team": tid, "lv": lv,
                         "pos": pos, "age": age, "ovr": ovr, "cend": cend or 0, "loan_from": loan_from or 0}
        else:
            plan[pid] = {"status": MILITARY_STATUS_SERVED, "enlist_age": ea, "type": st_type}

    def _postpone(pid, key):
        p = plan[pid]
        age = p["age"]
        p.update({"status": MILITARY_STATUS_UNSERVED, "enlist_age": min(MILITARY_FORCED_AGE, age + 1)})
        stats[key] += 1

    serving = [pid for pid, p in plan.items() if p["status"] == MILITARY_STATUS_SERVING]
    # 2) 구단당 상한 — 원 소속(부모) 구단 기준
    by_club = {}
    for pid in serving:
        p = plan[pid]
        by_club.setdefault(p["loan_from"] or p["team"], []).append(pid)
    for club, pids in sorted(by_club.items()):
        if len(pids) > MILITARY_INITIAL_CLUB_CAP:
            rng = _rng_for(conn, f"milcap:{club}")
            pids = sorted(pids)
            rng.shuffle(pids)
            for pid in pids[MILITARY_INITIAL_CLUB_CAP:]:
                _postpone(pid, "postponed_club")
    serving = sorted(pid for pid, p in plan.items() if p["status"] == MILITARY_STATUS_SERVING)
    cap_total = MILITARY_ROSTER_RANGE[1] * len(mil_teams)
    if len(serving) > cap_total:
        rng = _rng_for(conn, "miltotal")
        pool = list(serving)
        rng.shuffle(pool)
        for pid in pool[cap_total:]:
            _postpone(pid, "postponed_total")
        serving = sorted(pid for pid, p in plan.items() if p["status"] == MILITARY_STATUS_SERVING)

    # 3) 배정 — 팀당 목표 인원(고르게), 1부 우선순위 = K1 이상 수준 → 리그 수준 높은 순
    n = len(serving)
    order_teams = div1 + div2
    base, extra = divmod(n, len(order_teams))
    target = {t: base + (1 if i < extra else 0) for i, t in enumerate(order_teams)}
    k1_line = refs[1] - MILITARY_LEVEL_MARGIN
    ranked = sorted(serving, key=lambda pid: (
        0 if (plan[pid]["lv"] or 0) >= k1_line else 1, -(plan[pid]["lv"] or 0), -(plan[pid]["ovr"] or 0), pid))
    cap1 = sum(target[t] for t in div1)
    groups = ((div1, ranked[:cap1]), (div2, ranked[cap1:]))
    assign = {}
    for teams_, pids in groups:
        fill = {t: 0 for t in teams_}
        posc = {t: {} for t in teams_}
        # GK(가장 귀한 자리)부터 배정해야 팀마다 골키퍼가 고르게 들어간다 —
        # 알파벳순(DF→FW→GK)으로 돌면 GK 차례엔 이미 꽉 찬 팀이 생겨 0~1명인 팀이 나온다.
        _gorder = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}
        for pid in sorted(pids, key=lambda x: (_gorder[_pos_group(plan[x]["pos"])], -(plan[x]["ovr"] or 0), x)):
            g = _pos_group(plan[pid]["pos"])
            cand = [t for t in teams_ if fill[t] < target[t]]
            t = min(cand, key=lambda t: (posc[t].get(g, 0), fill[t], t))
            assign[pid] = t
            fill[t] += 1
            posc[t][g] = posc[t].get(g, 0) + 1
    stats["div1"] = sum(1 for pid in assign if assign[pid] in div1)
    stats["div2"] = len(assign) - stats["div1"]

    # 1) 상태 기록(복무자 외 전원)
    upd = [(p["status"], p["enlist_age"], pid) for pid, p in plan.items()
           if p["status"] != MILITARY_STATUS_SERVING]
    conn.executemany("UPDATE ai_players SET military_status=?, military_enlist_age=? WHERE id=?", upd)
    for pid, p in plan.items():
        stats[{MILITARY_STATUS_SERVED: "served", MILITARY_STATUS_UNSERVED: "unserved",
               MILITARY_STATUS_SERVING: "serving"}[p["status"]]] += 1

    # 5) 빈자리 채우기용 복제 대상 컬럼
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ai_players)").fetchall() if r[1] != "id"]
    col_list = ", ".join(cols)
    kr_cid_row = conn.execute("SELECT id FROM countries WHERE name=?", (MILITARY_TARGET_NATIONALITY,)).fetchone()
    names = [r[0] for r in conn.execute("SELECT name FROM player_names WHERE country_id=?",
                                        (kr_cid_row[0] if kr_cid_row else -1,)).fetchall()] or ["김민준"]
    team_name = {tid: nm for tid, nm in conn.execute("SELECT id, name FROM teams").fetchall()}

    log_rows = []
    for pid in sorted(assign):
        p = plan[pid]
        mt = assign[pid]
        cur_team = p["team"]
        parent = p["loan_from"] or cur_team
        ey = p["enlist_year"]
        rng = _rng_for(conn, f"milfill:{pid}")
        # 4) 계약
        if parent and (p["cend"] or 0) >= ey + 1:
            loan_from, loan_ret, cend = parent, ey + 1, p["cend"]
            stats["loan"] += 1
        else:
            loan_from, loan_ret, cend = 0, 0, ey + 1
            stats["fa"] += 1
        # 5) 빈자리 — 군인이 실제로 뛰던 팀(cur_team)에 같은 자리 선수
        if cur_team:
            conn.execute(f"INSERT INTO ai_players({col_list}) SELECT {col_list} FROM ai_players WHERE id=?", (pid,))
            new_id = conn.execute("SELECT MAX(id) FROM ai_players").fetchone()[0]
            age = p["age"]
            fill_age = max(19, min(34, age + rng.randint(-2, 2)))
            # 이 선수는 복무 중일 수 없다 — 미필(나중에 입대) 또는 군필
            if fill_age >= MILITARY_FORCED_AGE:
                f_status, f_ea = MILITARY_STATUS_SERVED, 0
            else:
                f_ea = sample_enlist_age(rng, p["type"], False)
                f_status = MILITARY_STATUS_UNSERVED if f_ea > fill_age else MILITARY_STATUS_SERVED
            conn.execute(
                "UPDATE ai_players SET name=?, age=?, created_year=?, creation_source='military_fill', "
                "on_loan_from_team_id=0, loan_return_year=0, last_transfer_year=0, "
                "military_status=?, military_enlist_age=?, military_enlist_year=0, "
                "military_pre_team_id=0, military_pre_league_id=0 WHERE id=?",
                (rng.choice(names), fill_age, start_year, f_status, f_ea, new_id))
            stats["fill"] += 1
        conn.execute(
            "UPDATE ai_players SET team_id=?, salary=0, on_loan_from_team_id=?, loan_return_year=?, "
            "contract_end_year=?, last_transfer_year=?, military_status=?, military_enlist_age=?, "
            "military_enlist_year=?, military_pre_team_id=?, military_pre_league_id=?, military_pre_salary=? WHERE id=?",
            (mt, loan_from, loan_ret, cend, ey, MILITARY_STATUS_SERVING, p["enlist_age"], ey,
             parent or 0, league_of.get(parent, 0) or 0, psal.get(pid, 0), pid))
        log_rows.append((ey - start_year, ey - 1, pid, pname.get(pid, ""), p["pos"] or "", p["age"] - (start_year - ey),
                         p["ovr"] or 0, cur_team or 0, mt, "입대", 0, 0, cend))
    if log_rows:
        conn.executemany(
            "INSERT INTO ai_transfer_log(season, year, player_id, player_name, player_position, player_age, "
            "player_ovr, from_team_id, to_team_id, transfer_type, fee, is_loan, contract_end_year) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", log_rows)
    print(f"[MILITARY-INIT] {start_year}년 초기 병역: 한국 {stats['korean']}명 → 군필 {stats['served']} / "
          f"미필 {stats['unserved']} / 복무 {stats['serving']}(1부 {stats['div1']}·2부 {stats['div2']}) | "
          f"연기(구단상한 {stats['postponed_club']}·전체상한 {stats['postponed_total']}) | "
          f"임대 {stats['loan']}·FA {stats['fa']} | 빈자리 채움 {stats['fill']}", flush=True)
    return stats


# ══════════════════════════════════════════════════════════════════
# 4단계: 매년 새해 처리 — 제대 → (진급/강등 맞교환) → 면제 → 유형 재판정 → 입대
# ══════════════════════════════════════════════════════════════════
def _meta_get(conn, key):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def _meta_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, str(value)))


# [2026-10 버그수정, 신민용 리포트: "팀 검색은 2007~2008 입대인데 선수 검색은 2008~2009로 밀려 있다"]
# 오프시즌 이동 기록(ai_transfer_log.year)은 프로젝트 규약상 "시즌이 끝난 해"를 적고 화면은 +1년부터
# 발효로 읽는다(교훈 5). 입대·제대·진급·강등을 "새 시즌 연도"로 적어서 선수 검색 타임라인과 개인상
# 연결이 1년씩 밀렸다 — 이제 전부 "끝난 시즌 연도"로 적는다(military_enlist_year는 실제 복무 시작 시즌 그대로).
def _log(conn, rows):
    if rows:
        conn.executemany(
            "INSERT INTO ai_transfer_log(season, year, player_id, player_name, player_position, player_age, "
            "player_ovr, from_team_id, to_team_id, transfer_type, fee, is_loan, contract_end_year, salary) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def _season_no(conn, year):
    try:
        from database import get_game_start_year
        return 1 + (year - get_game_start_year())
    except Exception:
        return 0


def _discharge(conn, finished_year, stats):
    """2시즌(입대 연도·그다음 해)을 다 채운 복무자를 제대시킨다. 원 소속 계약이
    남아 있으면 원 소속팀 복귀, 아니면 FA — AI는 입대 전 리그(없으면 같은 나라
    같은 부수)에서 인원이 가장 적은 팀에 이적료 없이 입단한다."""
    from constants import MILITARY_STATUS_SERVING, MILITARY_STATUS_SERVED, MILITARY_ROSTER_RANGE
    mil = get_military_team_ids(conn)
    rows = conn.execute(
        "SELECT id, name, position, age, ovr, team_id, on_loan_from_team_id, contract_end_year, "
        "military_pre_team_id, military_pre_league_id, military_pre_salary FROM ai_players "
        "WHERE military_status=? AND military_enlist_year<=?", (MILITARY_STATUS_SERVING, finished_year - 1)).fetchall()
    if not rows:
        return
    counts = {t: n for t, n in conn.execute("SELECT team_id, COUNT(*) FROM ai_players GROUP BY team_id").fetchall()}
    teams_by_league = {}
    for tid, lid in conn.execute("SELECT id, league_id FROM teams ORDER BY id").fetchall():
        if tid not in mil:
            teams_by_league.setdefault(lid, []).append(tid)
    exists = {t for lst in teams_by_league.values() for t in lst}
    season = _season_no(conn, finished_year)
    logs = []
    for pid, nm, pos, age, ovr, tid, loan_from, cend, pre_t, pre_l, pre_sal in rows:
        dest, new_cend = 0, cend or 0
        if loan_from and loan_from in exists and (cend or 0) > finished_year:
            dest = loan_from
            stats["returned"] += 1
        else:
            rng = _rng_for(conn, f"milfa:{pid}:{finished_year}")
            pool = teams_by_league.get(pre_l) or []
            if not pool and pre_t in exists:
                pool = [pre_t]
            if not pool:
                pool = sorted(exists)
            pool = [t for t in pool if counts.get(t, 0) < MILITARY_ROSTER_RANGE[1]] or pool
            dest = min(pool, key=lambda t: (counts.get(t, 0), t))
            new_cend = finished_year + rng.randint(1, 3)
            stats["fa"] += 1
        counts[dest] = counts.get(dest, 0) + 1
        conn.execute(
            "UPDATE ai_players SET team_id=?, on_loan_from_team_id=0, loan_return_year=0, salary=?, "
            "contract_end_year=?, last_transfer_year=?, military_status=? WHERE id=?",
            (dest, pre_sal or 0, new_cend, finished_year + 1, MILITARY_STATUS_SERVED, pid))
        logs.append((season, finished_year, pid, nm or "", pos or "", age or 0, ovr or 0, tid, dest,
                     "제대", 0, 0, new_cend, pre_sal or 0))
    _log(conn, logs)


def swap_military_rosters(conn, upper_tid, lower_tid, year):
    """[5단계 승강 플레이오프가 호출] 1부 팀(upper)과 2부 승자(lower)의 남은 복무자
    (입대 연도 == year-1, 즉 다음 시즌이 2년차)를 통째로 맞바꾼다. 팀은 그대로,
    선수만 진급/강등. 계약(2년)은 그대로 이어진다."""
    from constants import MILITARY_STATUS_SERVING
    a = [r[0] for r in conn.execute("SELECT id FROM ai_players WHERE team_id=? AND military_status=?",
                                    (upper_tid, MILITARY_STATUS_SERVING)).fetchall()]
    b = [r[0] for r in conn.execute("SELECT id FROM ai_players WHERE team_id=? AND military_status=?",
                                    (lower_tid, MILITARY_STATUS_SERVING)).fetchall()]
    conn.executemany("UPDATE ai_players SET team_id=? WHERE id=?", [(lower_tid, p) for p in a] + [(upper_tid, p) for p in b])
    season = _season_no(conn, year)
    logs = []
    for pid, nm, pos, age, ovr, tid in conn.execute(
            f"SELECT id, name, position, age, ovr, team_id FROM ai_players WHERE id IN ({','.join(map(str, a + b)) or '0'})").fetchall():
        down = pid in a
        logs.append((season, year, pid, nm or "", pos or "", age or 0, ovr or 0,
                     upper_tid if down else lower_tid, tid, "강등" if down else "진급", 0, 0, 0, 0))
    _log(conn, logs)
    return len(a), len(b)


def _apply_exemptions(conn, finished_year, stats):
    """그 해 끝난 본선 대회에서 한국이 면제 성적을 냈으면 최종 명단 전원(출전 0경기
    포함) 중 미필자를 면제로. 군인은 국대에 안 뽑히므로 복무 중 면제는 없다."""
    from constants import MILITARY_EXEMPT_RESULT, MILITARY_STATUS_UNSERVED, MILITARY_STATUS_EXEMPT
    K = MILITARY_TARGET_NATIONALITY
    for tid, kind, winner in conn.execute(
            "SELECT t.id, t.kind, t.winner FROM intl_tournaments t JOIN intl_entries e ON e.tournament_id=t.id "
            "WHERE t.year=? AND e.country=? AND t.kind IN ('world','continent','region')", (finished_year, K)).fetchall():
        need = MILITARY_EXEMPT_RESULT.get(kind)
        if not need:
            continue
        ok = winner == K
        if not ok and need == "runner_up":
            f = conn.execute("SELECT home, away FROM intl_matches WHERE tournament_id=? AND stage='F' "
                             "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
            ok = bool(f) and K in (f[0], f[1])
        if not ok:
            continue
        n = conn.execute(
            "UPDATE ai_players SET military_status=? WHERE military_status=? AND id IN "
            "(SELECT player_id FROM intl_squad WHERE tournament_id=? AND country=?)",
            (MILITARY_STATUS_EXEMPT, MILITARY_STATUS_UNSERVED, tid, K)).rowcount
        stats["exempt"] += max(0, n or 0)


def _enlist_new_year(conn, new_year, stats):
    """미필 한국 선수의 유형을 지금 소속·역할로 다시 판정해 올해 입대 대상을 뽑고
    군팀(정원 26)에 배정한다. 31세(강제 연령)는 정원을 넘어서라도 입대.
    상태가 비어 있는 한국 선수(시즌 중 생긴 신인·보충 선수 등)는 여기서 처음
    판정한다(31세 이상이면 군필)."""
    from constants import (MILITARY_STATUS_UNSERVED, MILITARY_STATUS_SERVED, MILITARY_STATUS_SERVING,
                           MILITARY_FORCED_AGE, MILITARY_ROSTER_RANGE, MILITARY_TYPE_AGE_RANGE,
                           MILITARY_LEVEL_MARGIN, MILITARY_MIN_AGE as _MIN_AGE)
    mil = get_military_team_ids(conn)
    if not mil:
        return
    tiers = {tid: tier for tid, tier in conn.execute(
        f"SELECT t.id, l.tier FROM teams t JOIN leagues l ON l.id=t.league_id WHERE t.id IN ({','.join(map(str, mil))})").fetchall()}
    div1 = sorted(t for t, tr in tiers.items() if tr == 1)
    div2 = sorted(t for t, tr in tiers.items() if tr != 1)
    levels = compute_league_levels(conn)
    refs = korea_reference_levels(conn, levels)
    league_of = {tid: lid for tid, lid in conn.execute("SELECT id, league_id FROM teams").fetchall()}
    regulars = national_regular_ids(conn, new_year)
    rows = conn.execute(
        "SELECT id, team_id, age, ovr, position, contract_end_year, on_loan_from_team_id, name, salary, "
        "military_status, military_enlist_age FROM ai_players WHERE nationality=? AND "
        "(military_status=? OR military_status='' OR military_status IS NULL) ORDER BY id",
        (MILITARY_TARGET_NATIONALITY, MILITARY_STATUS_UNSERVED)).fetchall()
    rows = [r for r in rows if r[1] not in mil]
    try:
        from ai_lifecycle import _load_role_index
        roles = _load_role_index(conn, new_year - 1) or {}
    except Exception:
        roles = {}
    if not roles:
        roles = compute_roles_for_teams(conn, [r[1] for r in rows])
    upd, cands = [], []
    for pid, tid, age, ovr, pos, cend, loan_from, nm, sal, st, ea in rows:
        age = age or 0
        if not st and age >= MILITARY_FORCED_AGE:
            upd.append((MILITARY_STATUS_SERVED, 0, pid))
            continue
        lv = levels.get(league_of.get(tid))
        reg = pid in regulars
        typ = judge_service_type(lv, roles.get(pid), refs, reg)
        lo, hi = MILITARY_TYPE_AGE_RANGE[typ]
        if reg:
            ea = max(ea or 0, 30)
        elif not ea or not (lo <= ea <= hi):
            ea = sample_enlist_age(_rng_for(conn, f"milage:{pid}:{new_year}"), typ, False)
        upd.append((MILITARY_STATUS_UNSERVED, ea, pid))
        if age >= MILITARY_FORCED_AGE or (ea <= age and age >= _MIN_AGE):
            cands.append({"pid": pid, "team": tid, "age": age, "ovr": ovr, "pos": pos, "cend": cend or 0,
                          "loan_from": loan_from or 0, "name": nm, "sal": sal or 0, "lv": lv,
                          "forced": age >= MILITARY_FORCED_AGE, "ea": ea})
    conn.executemany("UPDATE ai_players SET military_status=?, military_enlist_age=? WHERE id=?", upd)
    if not cands:
        return
    fill = {t: n for t, n in conn.execute(
        f"SELECT team_id, COUNT(*) FROM ai_players WHERE team_id IN ({','.join(map(str, mil))}) GROUP BY team_id").fetchall()}
    posc = {t: {} for t in mil}
    for t, pos in conn.execute(
            f"SELECT team_id, position FROM ai_players WHERE team_id IN ({','.join(map(str, mil))})").fetchall():
        g = _pos_group(pos)
        posc[t][g] = posc[t].get(g, 0) + 1
    k1_line = refs[1] - MILITARY_LEVEL_MARGIN
    cap = MILITARY_ROSTER_RANGE[1]
    # [2026-10 신민용 확정: "정원이 부족한 것보다 누가 연기되느냐가 문제"] 22시즌 실측에서
    # 해마다 입대 대기 ≈ 270~290명 > 정원 ≈ 208명/년인데, 예전 정렬(강제 → K1 이상 → 리그
    # 수준 → OVR)은 나이를 전혀 안 봐서 21세 K1 선수가 자리를 차지하고 28~30세 하위 리그
    # 선수가 연기됐다 — 그 결과 26세+ 미필이 서서히 늘고 2019년부터 31세 강제 입대가 나왔다.
    # 이제 두 단계로 나눈다:
    #  (1) 누가 올해 입대하나 — 강제(31세+, 정원과 무관하게 항상) → 나이 많은 순 → 예정 입대
    #      나이를 많이 넘긴 순 → 예정 입대 나이가 이른 순 → K1 이상 → 리그 수준 → OVR.
    #      남는 자리 수만큼 위에서 자르고, 나머지(=젊은 쪽)가 연기된다.
    #  (2) 뽑힌 사람을 어느 팀에 — 예전 정렬 그대로(강제 → K1 이상 → 수준 → OVR) 1부부터
    #      채운다. 1부가 수준 높은 선수로 차는 기존 설계는 그대로 유지.
    _free = sum(max(0, cap - fill.get(t, 0)) for t in mil)
    cands.sort(key=lambda d: (0 if d["forced"] else 1, -(d["age"] or 0), -((d["age"] or 0) - (d["ea"] or 0)),
                              d["ea"] or 0, 0 if (d["lv"] or 0) >= k1_line else 1,
                              -(d["lv"] or 0), -(d["ovr"] or 0), d["pid"]))
    _picked, _waiting = [], []
    for d in cands:
        if d["forced"] or len(_picked) < _free:
            _picked.append(d)
        else:
            _waiting.append(d)
    for d in _waiting:   # 자리 부족 → 연기(다음 해 다시 판정). 젊은 쪽이 여기로 온다.
        conn.execute("UPDATE ai_players SET military_enlist_age=? WHERE id=?",
                     (min(MILITARY_FORCED_AGE, d["age"] + 1), d["pid"]))
        stats["postponed"] += 1
    if _waiting:
        stats["postponed_ages"] = dict(sorted(Counter(d["age"] for d in _waiting).items()))
    cands = sorted(_picked, key=lambda d: (0 if d["forced"] else 1, 0 if (d["lv"] or 0) >= k1_line else 1,
                                           -(d["lv"] or 0), -(d["ovr"] or 0), d["pid"]))
    season = _season_no(conn, new_year - 1)
    logs = []
    # [실측 수정] 예전엔 "K1 이상이면 1부부터, 아니면 2부부터" 채워서, K1 이상
    # 입대자가 연 30~50명뿐인 탓에 1부(6팀×26)가 해마다 줄어 10~12명까지 말랐다
    # (기존 세이브 경로 실측). 이제 올해 들어올 인원까지 합친 "팀당 목표 인원"을
    # 정하고, 우선순위(강제 → K1 이상 → 리그 수준 높은 순) 위에서부터 1부를 목표까지
    # 채운 뒤 2부를 채운다 — 1부가 가장 수준 높은 선수로 차면서 인원도 고르게 맞는다.
    all_t = div1 + div2
    total = sum(fill.get(t, 0) for t in all_t) + len(cands)
    target = min(cap, -(-total // len(all_t)))
    for d in cands:
        g = _pos_group(d["pos"])
        pool = ([t for t in div1 if fill.get(t, 0) < target]
                or [t for t in div2 if fill.get(t, 0) < target]
                or [t for t in all_t if fill.get(t, 0) < cap])
        if not pool:
            if not d["forced"]:
                conn.execute("UPDATE ai_players SET military_enlist_age=? WHERE id=?",
                             (min(MILITARY_FORCED_AGE, d["age"] + 1), d["pid"]))
                stats["postponed"] += 1
                continue
            pool = list(all_t)
        t = min(pool, key=lambda t: (posc[t].get(g, 0), fill.get(t, 0), t))
        parent = d["loan_from"] or d["team"]
        if parent and d["cend"] >= new_year + 1:
            loan_from, loan_ret, cend = parent, new_year + 1, d["cend"]
            stats["loan"] += 1
        else:
            loan_from, loan_ret, cend = 0, 0, new_year + 1
            stats["fa_in"] += 1
        conn.execute(
            "UPDATE ai_players SET team_id=?, salary=0, on_loan_from_team_id=?, loan_return_year=?, "
            "contract_end_year=?, last_transfer_year=?, military_status=?, military_enlist_age=?, "
            "military_enlist_year=?, military_pre_team_id=?, military_pre_league_id=?, military_pre_salary=? WHERE id=?",
            (t, loan_from, loan_ret, cend, new_year, MILITARY_STATUS_SERVING, d["age"], new_year,
             parent or 0, league_of.get(parent, 0) or 0, d["sal"], d["pid"]))
        fill[t] = fill.get(t, 0) + 1
        posc[t][g] = posc[t].get(g, 0) + 1
        stats["enlisted"] += 1
        stats["forced"] += 1 if d["forced"] else 0
        logs.append((season, new_year - 1, d["pid"], d["name"] or "", d["pos"] or "", d["age"], d["ovr"] or 0,
                     d["team"] or 0, t, "입대", 0, 0, cend, 0))
    _log(conn, logs)


def process_military_new_year(conn, finished_year) -> dict:
    """[4단계] 시즌 전환(오프시즌) 때 한 번. 순서(신민용 확정): 제대 → 진급/강등
    맞교환(5단계 승강 PO가 swap_military_rosters로 처리) → 면제 반영 → 유형 재판정
    → 새해 입대. 나이는 이미 +1 된 뒤(_age_and_progress 이후)에 불러야 한다.
    기능을 켜고 기존 세이브를 처음 불러온 경우(초기 입대 기록 없음)엔 대신
    초기 입대를 한 번 돌린다."""
    stats = {"returned": 0, "fa": 0, "exempt": 0, "enlisted": 0, "forced": 0, "postponed": 0,
             "loan": 0, "fa_in": 0}
    if not military_enabled() or not get_military_country_id(conn):
        return stats
    if _meta_get(conn, "military_init_year") is None:
        run_initial_enlistment(conn, finished_year + 1)
        _meta_set(conn, "military_init_year", finished_year + 1)
        return stats
    # 실경기로 치른 PO 결과가 있으면 그걸 쓰고, 없으면(예전 세이브 등) 예전처럼 계산한다.
    po = _played_military_po(conn, finished_year)
    if po is None:
        po = run_military_playoff(conn, finished_year)   # 시즌 끝 선수단으로 경기(제대 전)
    _discharge(conn, finished_year, stats)
    if po and po[2]:
        down, up = swap_military_rosters(conn, po[0], po[1], finished_year)
        stats["swap"] = f"{down}↔{up}"
    _apply_exemptions(conn, finished_year, stats)
    _enlist_new_year(conn, finished_year + 1, stats)
    print(f"[MILITARY] {finished_year}→{finished_year + 1} 승강PO {('승격·맞교환 ' + stats['swap']) if stats.get('swap') else ('잔류' if po else '없음')} | 제대 {stats['returned'] + stats['fa']}"
          f"(복귀 {stats['returned']}·FA {stats['fa']}) | 면제 {stats['exempt']} | 입대 {stats['enlisted']}"
          f"(강제 {stats['forced']}, 임대 {stats['loan']}·FA {stats['fa_in']}) | 정원초과 연기 {stats['postponed']}"
          f"{(' (나이별 ' + str(stats['postponed_ages']) + ')') if stats.get('postponed_ages') else ''}",
          flush=True)
    return stats


# ══════════════════════════════════════════════════════════════════
# 5단계: 경기 수 · 승강 플레이오프(→ 선수단 맞교환)
# ══════════════════════════════════════════════════════════════════
def military_legs_for_league(league_id):
    """군 리그면 고정 legs(1부 4전·2부 2전), 아니면 None. 커넥션 없이 부를 수 있게
    캐시가 비었을 때만 한 번 연다(군대가 없으면 빈 결과가 캐시돼 이후 비용 0)."""
    from constants import MILITARY_LEGS_BY_TIER
    if _CACHE["league_tier"] is None:
        from database import get_conn
        _c = get_conn()
        try:
            _load(_c)
        finally:
            _c.close()
    tier = (_CACHE["league_tier"] or {}).get(league_id)
    return MILITARY_LEGS_BY_TIER.get(tier) if tier else None


def _final_table(conn, league_id, year):
    """그 해 최종 순위(승점 → 득실 → 다득점 → team_id) 팀 id 목록."""
    # 오프시즌 시점엔 그 해 순위 스냅샷(hist)이 아직 없을 수 있어서, 경기 결과
    # (라이브 + 아카이브)에서 직접 집계한다(_process_promotion_relegation과 같은 방식).
    season = _season_no(conn, year)
    tab = {}
    for tbl in ("match_results", "match_results_archive"):
        try:
            res = conn.execute(
                f"SELECT home_team_id, away_team_id, home_score, away_score FROM {tbl} "
                "WHERE league_id=? AND season=? AND home_score IS NOT NULL AND home_score >= 0",
                (league_id, season)).fetchall()
        except Exception:
            continue
        for h, a, hs, as_ in res:
            for t, gf, ga in ((h, hs, as_), (a, as_, hs)):
                d = tab.setdefault(t, [0, 0, 0])
                d[0] += 3 if gf > ga else (1 if gf == ga else 0)
                d[1] += gf - ga
                d[2] += gf
    return [t for t, _ in sorted(tab.items(), key=lambda kv: (-kv[1][0], -kv[1][1], -kv[1][2], kv[0]))]


def _team_strength(conn, team_id):
    vals = [r[0] for r in conn.execute(
        "SELECT ovr FROM ai_players WHERE team_id=? ORDER BY ovr DESC LIMIT 11", (team_id,)).fetchall()]
    return sum(vals) / len(vals) if vals else 50.0


def _single_match(conn, year, stage, home, away):
    """단판·중립(홈 어드밴티지 없음). 무승부는 승부차기. 결과는 military_po에 기록."""
    from competition.competition_common import match_outcome, resolve_pso
    h, a = _team_strength(conn, home), _team_strength(conn, away)
    res = match_outcome(h, a, neutral=True)
    pso = ""
    if res == "draw":
        res = resolve_pso(h, a, neutral=True)
        pso = res
    winner = home if res == "home" else away
    # 스코어(기록실 PO 패널 표시용) — 결과(승/무/패)는 위 공식이 정하고, 숫자만 만든다.
    rng = _rng_for(conn, f"milpo:{year}:{stage}:{home}:{away}")
    if pso:
        g = rng.choice((0, 0, 1, 1, 1, 2))
        hs = as_ = g
        w_p, l_p = rng.choice(((4, 3), (5, 4), (3, 2), (5, 3), (4, 2)))
        pso_score = f"{w_p}-{l_p}" if winner == home else f"{l_p}-{w_p}"
    else:
        wg = rng.choice((1, 1, 2, 2, 2, 3, 3, 4))
        lg = rng.randint(0, wg - 1)
        hs, as_ = (wg, lg) if winner == home else (lg, wg)
        pso_score = ""
    conn.execute("INSERT INTO military_po(year, stage, home_team_id, away_team_id, winner_team_id, pso, "
                 "home_score, away_score, pso_score) VALUES(?,?,?,?,?,?,?,?,?)",
                 (year, stage, home, away, winner, "승부차기" if pso else "", hs, as_, pso_score))
    return winner


def get_military_po_rows(conn, league_id, year, direction):
    """[6단계] 기록실 "연도 클릭 → 최종 순위" 화면의 PO 패널용. 일반 리그의
    world_browser.get_po_results와 같은 형태의 dict 목록. 군 리그가 아니면 None.
      2부 페이지 promotion: 준결승 2 + 결승 + 승강전 / 1부 페이지 relegation: 승강전."""
    get_military_country_id(conn)   # 캐시(league_tier) 로드 보장
    tier = (_CACHE["league_tier"] or {}).get(league_id)
    if not tier:
        return None
    # 1부 페이지에서도 2부 PO(준결승·결승)부터 승강전까지 전부 보여준다(신민용 요청).
    want = ("2부 PO 준결승", "2부 PO 결승", "승강 플레이오프")
    if (tier == 1 and direction != "relegation") or (tier != 1 and direction != "promotion"):
        return []
    tname = {i: n for i, n in conn.execute("SELECT id, name FROM teams WHERE country_id=?",
                                           (get_military_country_id(conn),)).fetchall()}
    out = []
    for stage, h, a, w, hs, as_, ps in conn.execute(
            "SELECT stage, home_team_id, away_team_id, winner_team_id, home_score, away_score, pso_score "
            "FROM military_po WHERE year=? ORDER BY id", (year,)).fetchall():
        if stage not in want:
            continue
        up = stage == "승강 플레이오프"
        out.append({"stage": stage, "home": tname.get(h, ""), "away": tname.get(a, ""),
                    "home_tier": 1 if up else 2, "away_tier": 2,
                    "home_score": hs or 0, "away_score": as_ or 0, "pso_score": ps or "",
                    "home_won": w == h})
    return out


def get_military_po_outcome(conn, league_id, year):
    """(승격 팀 이름 집합, 강등 팀 이름 집합) — 승강전에서 2부 승자가 이긴 해에만.
    팀이 실제로 옮기는 건 아니고 선수단 맞교환이지만, 기록실 색 표시(파랑/빨강)는
    일반 리그와 같은 의미로 보여준다(신민용 확정). 군 리그가 아니면 None."""
    get_military_country_id(conn)
    rows = get_military_po_rows(conn, league_id, year, "relegation" if
                                ((_CACHE["league_tier"] or {}).get(league_id) == 1) else "promotion")
    if rows is None:
        return None
    for r in rows:
        if r["stage"] == "승강 플레이오프" and not r["home_won"]:
            return {r["away"]}, {r["home"]}
    return set(), set()


def military_po_pending_slots(conn, year):
    """[실경기 PO] 시즌 종료 시점(_process_promotion_relegation)에 po_pending_slots 행을 만든다.
    lower(2부) offset 0~3 = 1~4위, upper(1부) offset 0 = 최하위. 군대가 없으면 []."""
    get_military_country_id(conn)
    tier_of = _CACHE["league_tier"] or {}
    l1 = next((l for l, t in tier_of.items() if t == 1), None)
    l2 = next((l for l, t in tier_of.items() if t == 2), None)
    if not l1 or not l2:
        return []
    t1, t2 = _final_table(conn, l1, year), _final_table(conn, l2, year)
    if len(t1) < 2 or len(t2) < 4:
        return []
    names = {i: n for i, n in conn.execute(
        f"SELECT id, name FROM teams WHERE id IN ({','.join(map(str, t2[:4] + [t1[-1]]))})").fetchall()}
    rows = [(year, l1, l2, "bracket4", "lower", k, tid, names.get(tid, "")) for k, tid in enumerate(t2[:4])]
    rows.append((year, l1, l2, "bracket4", "upper", 0, t1[-1], names.get(t1[-1], "")))
    return rows


def _played_military_po(conn, finished_year):
    """그 해 실경기로 치른 군데스리가 PO(po_tournaments/po_matches)가 있으면 military_po에
    옮겨 적고 (1부 팀, 2부 승자, 2부 승자가 이겼는가)를 돌려준다. 없으면 None."""
    get_military_country_id(conn)
    tier_of = _CACHE["league_tier"] or {}
    l1 = next((l for l, t in tier_of.items() if t == 1), None)
    if not l1:
        return None
    t = conn.execute("SELECT id FROM po_tournaments WHERE year=? AND upper_league_id=? ORDER BY id DESC LIMIT 1",
                     (finished_year, l1)).fetchone()
    if not t:
        return None
    ms = {r[0]: r for r in conn.execute(
        "SELECT match_key, home_team_id, away_team_id, home_score, away_score, pso_winner, pso_score "
        "FROM po_matches WHERE tournament_id=?", (t[0],)).fetchall()}
    f = ms.get("F")
    if not f or (f[3] is None or f[3] < 0):
        return None
    stage_of = {"SF1": "2부 PO 준결승", "SF2": "2부 PO 준결승", "LF": "2부 PO 결승", "F": "승강 플레이오프"}
    conn.execute("DELETE FROM military_po WHERE year=?", (finished_year,))
    res = None
    for key in ("SF1", "SF2", "LF", "F"):
        m = ms.get(key)
        if not m or m[3] is None or m[3] < 0:
            continue
        _, h, a, hs, as_, pw, ps = m
        w = pw if pw else (h if hs > as_ else a)
        conn.execute("INSERT INTO military_po(year, stage, home_team_id, away_team_id, winner_team_id, pso, "
                     "home_score, away_score, pso_score) VALUES(?,?,?,?,?,?,?,?,?)",
                     (finished_year, stage_of[key], h, a, w, "승부차기" if pw else "", hs, as_, ps or ""))
        if key == "F":
            res = (h, a, w == a)
    return res


def run_military_playoff(conn, finished_year):
    """2부 상위 4팀 단판 토너먼트(1v4, 2v3 → 결승) 승자 vs 1부 6위 단판 승강전.
    반환: (1부 팀, 2부 승자, 2부 승자가 이겼는가) 또는 None(순위 데이터 없음).
    맞교환 자체는 제대 뒤에 swap_military_rosters가 한다(순서 확정: 제대 → 맞교환 → 입대)."""
    from constants import MILITARY_PLAYOFF_TEAMS
    tier_of = _CACHE["league_tier"] or {}
    if not tier_of:
        get_military_country_id(conn)
        tier_of = _CACHE["league_tier"] or {}
    l1 = next((l for l, t in tier_of.items() if t == 1), None)
    l2 = next((l for l, t in tier_of.items() if t == 2), None)
    if not l1 or not l2:
        return None
    t1, t2 = _final_table(conn, l1, finished_year), _final_table(conn, l2, finished_year)
    if len(t1) < 2 or len(t2) < MILITARY_PLAYOFF_TEAMS:
        return None
    s1, s2, s3, s4 = t2[:4]
    w1 = _single_match(conn, finished_year, "2부 PO 준결승", s1, s4)
    w2 = _single_match(conn, finished_year, "2부 PO 준결승", s2, s3)
    lower = _single_match(conn, finished_year, "2부 PO 결승", w1, w2)
    upper = t1[-1]
    win = _single_match(conn, finished_year, "승강 플레이오프", upper, lower)
    return upper, lower, win == lower


def fix_military_log_years(conn):
    """[2026-10] 위 버그로 이미 1년 늦게 적힌 군 이동 기록을 한 번만 바로잡는다(meta로 1회 보장).
    새 게임은 고친 코드로 기록되므로 생성 시점에 이미 처리됨 표시를 남긴다(database.reset_game_data)."""
    if _meta_get(conn, "military_log_year_v2") is not None:
        return 0
    n = 0
    for tbl in ("ai_transfer_log", "ai_transfer_log_archive"):
        try:
            n += conn.execute(f"UPDATE {tbl} SET year = year - 1, season = season - 1 "
                              "WHERE transfer_type IN ('입대','제대','진급','강등')").rowcount or 0
        except Exception:
            pass
    _meta_set(conn, "military_log_year_v2", 1)
    return n