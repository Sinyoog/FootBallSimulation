# game_engine.py
import random
import math
import json
import sqlite3
import intl_engine
from competition import champions_engine
from competition import europa_engine
from competition import conference_engine
from competition import continental_qualification
from competition import cup_engine
from competition import club_world_cup_engine
from competition import super_cup_engine
import promotion_playoff_engine
from match_sim import match_flow
from match_sim import tactical_engine
from database import (get_conn, calc_ovr, ALL_STATS,
                      rescale_team_to_target_ovr, rescale_teams_to_target_ovr_batch,
                      get_league_avg_ovr,
                      get_league_strong_ovr)
from constants import *  # PHYSICAL_STATS, TECHNICAL_STATS, MENTAL_STATS 포함

_pending_transfer_type: str = ""  # join_team → _save_career_entry 전달용. ''=대기(잔류 시즌)

# [2026-07 신설, 신민용+GPT 검토: "강등 프리미엄이 이적시장/노쇠화로
# 상쇄되는지 확인하고 싶다 — 근데 상시 DB 저장은 과하다"] 평소엔 완전히
#꺼진 상태로 아무 것도 안 하고(오버헤드 0), 이 플래그를 True로 켰을
# 때만 강등팀의 OVR을 4시점(리스케일 직후/개막 직전/시즌 종료)에서
# 콘솔+파일로 남긴다. DB 스키마·컬럼은 전혀 안 늘어난다 — 세션 메모리
# (_RELEGATION_DEBUG_TRACK)에만 임시로 들고 있다가 텍스트 로그 파일에
# 쌓고, 앱을 재시작하면 추적 중이던 것도 그냥 리셋된다(가벼운 디버그
# 도구이므로 이 정도 트레이드오프는 허용).
DEBUG_RELEGATION_TRACKING = False
_RELEGATION_DEBUG_TRACK: dict = {}   # team_id -> {"name":.., "season":.., "checkpoints":{...}}
_RELEGATION_DEBUG_LOG_PATH = "relegation_debug.log"


def _relegation_debug_avg_ovr(team_id) -> float:
    conn = get_conn()
    row = conn.execute(
        "SELECT AVG(ovr) AS v FROM ai_players WHERE team_id=?", (team_id,)).fetchone()
    return round(row["v"], 1) if row and row["v"] is not None else 0.0


def _relegation_debug_snapshot(team_id, stage, season=None, extra=""):
    """DEBUG_RELEGATION_TRACKING이 켜져 있을 때만 동작 — 꺼져 있으면
    즉시 리턴(오버헤드 없음)."""
    if not DEBUG_RELEGATION_TRACKING:
        return
    track = _RELEGATION_DEBUG_TRACK.get(team_id)
    if track is None:
        return
    avg_ovr = _relegation_debug_avg_ovr(team_id)
    track["checkpoints"][stage] = avg_ovr
    line = f"[{track['name']}] {stage}: 평균OVR {avg_ovr}{('  ' + extra) if extra else ''}"
    print(f"[RELEGATION-DEBUG] {line}")
    try:
        with open(_RELEGATION_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── 팀 평균 OVR 캐시 ───────────────────────────────────────────
# ai_players.ovr 및 team_id는 게임 진행 중 변경되지 않는다
# (변경 지점은 database.py의 1회성 시드/리맵뿐). 따라서 team_id별 평균 OVR은
# 세션 내내 상수다. 매 경기 시뮬마다 2.6만 행을 집계하던 _team_avg_ovr를
# 메모이즈해 동일 결과를 반환하면서 호출당 비용을 0으로 만든다.
# (값이 바뀌는 리맵 시점에는 _invalidate_team_ovr_cache로 비운다.)
_team_ovr_cache: dict = {}
_league_ovr_cache: dict = {}
# 리그 tier 캐시: leagues.tier 는 게임 중 변하지 않는 세션 상수.
_league_tier_cache: dict = {}
# 팀 이름 캐시: teams.name 은 세션 내 불변. _write_match_log 등에서 매번 SELECT 방지.
_team_name_cache: dict = {}

def _invalidate_team_ovr_cache():
    """ai_players OVR/소속이 일괄 변경되는 경우(리맵·신규 시드·승강) 호출.
    포메이션 위젯의 선수 목록 캐시도 함께 비운다.
    (승강 후 rescale로 AI OVR이 바뀌어도 위젯 캐시가 남아 있으면
     구 티어 OVR이 그대로 표시되는 버그 방지.)
    """
    _team_ovr_cache.clear()
    _league_ovr_cache.clear()
    _league_tier_cache.clear()
    _team_formation_cache.clear()
    # 승강으로 팀이 다른 리그로 이동해도 팀명은 안 바뀌므로 _team_name_cache는 비우지 않음

    # 포메이션 위젯 선수 목록 캐시 무효화
    # FormationWidget 인스턴스를 직접 참조하지 않고, 모듈 속성으로 플래그 세팅.
    # formation_widget.py의 load_my_team이 이 플래그를 보고 캐시를 무시한다.
    try:
        import ui.formation_widget as _fw
        _fw._ovr_cache_invalidated = True
    except Exception:
        pass

def _team_name(c, team_id, default="팀") -> str:
    """팀 이름 조회 (세션 캐시). c=열린 커서 재사용."""
    cached = _team_name_cache.get(team_id)
    if cached is not None:
        return cached
    row = c.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
    val = row["name"] if row else default
    _team_name_cache[team_id] = val
    return val


def _league_tier(c, league_id, default=3):
    """리그 tier 조회 (세션 캐시). c=열린 커서 재사용."""
    if not league_id:
        return default
    cached = _league_tier_cache.get(league_id)
    if cached is not None:
        return cached
    row = c.execute("SELECT tier FROM leagues WHERE id=?", (league_id,)).fetchone()
    val = row["tier"] if row else default
    _league_tier_cache[league_id] = val
    return val


# ═══════════════════════════════════════════
# 유틸
# ═══════════════════════════════════════════

def fmt_money(amount_k: int) -> str:
    """천원 단위 정수 → 표시 문자열. (예: 1=1천원, 10000=1천만원, 100000=1억)
    amount_k <= 0 이면 "무급"이 아닌 "0원"을 반환한다.
    진짜 무급(salary==0)은 호출부에서 별도 처리할 것.
    """
    if amount_k < 0:
        return "0원"
    if amount_k == 0:
        return "0원"
    won = amount_k * 1000
    if won >= 1000000000000:    # 1조 이상
        jo  = won // 1000000000000
        eok = (won % 1000000000000) // 100000000
        if eok:
            return f"{jo:,}조 {eok:,}억원"
        return f"{jo:,}조원"
    if won >= 100000000:        # 1억 이상
        return f"{won/100000000:.2f}억원"
    if won >= 10000000:         # 1천만 이상
        return f"{won/10000000:.1f}천만원"
    if won >= 10000:            # 1만 이상
        return f"{won//10000:,}만원"
    return f"{won:,}원"


def get_player():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM my_player WHERE id=1")
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# [2026-08 신설, 난이도 시스템] p가 이미 손에 있으면 그걸 쓰고(추가 쿼리
# 없이), 없으면 여기서 직접 조회한다 — player_panel.py/formation_widget.py/
# 팀검색 화면 등에서 한 줄로 게이트 조건을 걸 수 있게 하기 위한 헬퍼.
# 값이 없거나(마이그레이션 직후 등) 알 수 없는 문자열이면 항상 안전한
# 쪽(easy=전부 공개)으로 취급한다.
def get_difficulty(p=None) -> str:
    if p is None:
        p = get_player()
    d = (p or {}).get("difficulty") or "easy"
    return d if d in ("easy", "normal", "hard") else "easy"


def is_hard_mode(p=None) -> bool:
    """어려움 난이도 — 재능등급/현재OVR/성격/감독관계/타선수(내 선수 포함)
    스탯·신체스탯 전부 비표시, 포메이션 OVR 수치 비표시가 걸리는 기준."""
    return get_difficulty(p) == "hard"


def update_player(**kw):
    if not kw:
        return
    conn = get_conn()
    c = conn.cursor()
    if "ovr" in kw and "peak_ovr" not in kw:
        # [전성기 OVR] ovr이 바뀔 때마다 역대 최고치를 peak_ovr에 함께 기록.
        #   read-before-write 없이 SQL의 max()로 원자적 처리(추가 왕복 없음).
        sets = ",".join(f"{k}=?" for k in kw) + ", peak_ovr=MAX(COALESCE(peak_ovr,0), ?)"
        vals = list(kw.values()) + [kw["ovr"]]
    else:
        sets = ",".join(f"{k}=?" for k in kw)
        vals = list(kw.values())
    c.execute(f"UPDATE my_player SET {sets} WHERE id=1", vals)
    conn.commit()
    conn.close()


def get_field_pos(p=None):
    """현재 팀 포메이션 기반으로 배치 포지션 런타임 계산.
    DB에 저장하지 않고 호출할 때마다 계산 → 포메이션 변경 즉시 반영.
    """
    if p is None:
        p = get_player()
    if not p:
        return "CM"
    primary = p.get("position", "CM")
    team_id = p.get("current_team_id", 0)
    if not team_id:
        return primary
    try:
        from constants import POSITION_COMPAT, FORMATION_SLOTS
        conn = get_conn()
        row = conn.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
        conn.close()
        formation = (row["formation"] if row else None) or "4-4-2"
        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        compat = POSITION_COMPAT.get(primary, [primary])
        best, best_rank = primary, 999
        for slot in slots:
            if slot in compat:
                rank = compat.index(slot)
                if rank < best_rank:
                    best_rank = rank
                    best = slot
        return best
    except Exception:
        return primary



# ═══════════════════════════════════════════
# [국적 연혁] 출생국적 / 귀화 / 대표선택 이력 기록
# ═══════════════════════════════════════════
# nat_history 컬럼(JSON list)에 국적 관련 사건을 시간순으로 누적한다.
#   type: "birth"(출생 보유) / "naturalize"(귀화 획득) / "commit"(대표 확정)
#   각 항목: {"type","nat","flag","year","week"}
# 은퇴 AI요약에서 "라오스 출생 → 1994년 포르투갈 귀화 → 1996년 포르투갈 대표 선택"
# 같은 연혁을 재구성하는 데 쓴다.

def get_nat_history(p=None):
    """국적 연혁 리스트 반환 (없으면 빈 리스트)."""
    if p is None:
        p = get_player()
    if not p:
        return []
    raw = p.get("nat_history", "") or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def add_nat_history(ev_type, nat, flag="", year=None, week=None, p=None):
    """국적 연혁에 사건 1건 추가. 같은 (type, nat)이 이미 있으면 중복 추가 안 함.
    year/week 생략 시 현재 시즌 상태에서 자동으로 채운다."""
    if not nat:
        return
    if p is None:
        p = get_player()
    if not p:
        return
    if year is None or week is None:
        st = get_state() or {}
        if year is None:
            year = st.get("current_year", GAME_START_YEAR)
        if week is None:
            week = st.get("current_week", 1)
    hist = get_nat_history(p)
    for h in hist:
        if h.get("type") == ev_type and h.get("nat") == nat:
            return   # 중복 방지 (대표선택은 1회뿐이지만 안전하게)
    hist.append({"type": ev_type, "nat": nat, "flag": flag or "",
                 "year": year, "week": week})
    update_player(nat_history=json.dumps(hist, ensure_ascii=False))


# ── season_state 캐시 ──────────────────────────────────────────
# [2026-08 신설, 신민용 리포트: "1년 넘기기가 갈수록 느려진다" — 50시즌
# 세이브 실측] get_state()가 season_state(행 1개짜리 단일 상태 테이블)를
# 매번 새로 SELECT했는데, 이 함수가 게임 전체에서 101곳(모든 대회 엔진 +
# UI)에서 호출된다 — 실측 1년 진행에서만 3,642회, 순수 오버헤드 18.4초
# (풀 커넥션 락+재시도 래퍼를 매번 타는 비용). season_state는 하루/주/
# 시즌이 바뀌는 딱 몇 군데(_advance_week, advance_days의 날짜 갱신,
# set_state, create_player)에서만 쓰이므로, 그 지점들에서만 캐시를 갱신/
# 무효화하면 나머지 101곳은 항상 최신값을 캐시에서 그냥 읽어도 된다.
# get_state()가 반환한 dict을 호출부가 직접 수정하는 경우가 있는지 전체
# 검색으로 확인했고(없음) — 그래도 방어적으로 매번 얕은 복사본을 반환해
# 혹시 모를 호출부의 in-place 수정이 캐시를 오염시키지 않게 한다.
_state_cache: dict = None


def _invalidate_state_cache():
    """season_state가 직접 SQL로 갱신되는 지점(또는 그 값을 알 수 없는
    경우)에서 호출해 캐시를 비운다 — 다음 get_state() 호출 시 새로 조회."""
    global _state_cache
    _state_cache = None


def get_state():
    global _state_cache
    if _state_cache is not None:
        return dict(_state_cache)
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM season_state WHERE id=1")
    row = c.fetchone()
    conn.close()
    if row:
        _state_cache = dict(row)
    else:
        _state_cache = {
            "current_year": GAME_START_YEAR,
            "current_week": 1,
            "current_day": 1,
            "current_season": 1,
            "phase": "preseason",
        }
    return dict(_state_cache)


def set_state(**kw):
    global _state_cache
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM season_state WHERE id=1")
    if c.fetchone():
        sets = ",".join(f"{k}=?" for k in kw)
        c.execute(f"UPDATE season_state SET {sets} WHERE id=1", list(kw.values()))
    else:
        keys = ",".join(kw.keys())
        vals = ",".join("?" for _ in kw)
        c.execute(f"INSERT INTO season_state(id,{keys}) VALUES(1,{vals})",
                  list(kw.values()))
    conn.commit()
    conn.close()
    # [최적화] 방금 쓴 값만 캐시에 그대로 반영 — 다음 get_state()가 다시
    # DB를 조회하지 않아도 되게 한다(캐시가 아직 없던 상태면 그냥 비워서
    # 다음 호출이 새로 채우게 한다).
    if _state_cache is not None:
        _state_cache.update(kw)
    else:
        _invalidate_state_cache()


# [2026-07 성능 수정 — "이번 주 진행" 1초 버퍼링의 실제 원인]
# _week_intl_cl_day(week, p)는 결과가 오직 (week, 내 팀 id, 내 시즌)에만
# 좌우된다 — 어떤 경기(m)를 시뮬레이션하는지와는 무관하다. 그런데
# cup_engine/champions_engine/intl_engine의 _sim_ai_match()가 "그 주에
# 실제로 진행된 날짜"를 저장하겠다고 이 함수를 AI vs AI 경기 하나하나마다
# (한 라운드에 수백~수천 건) 매번 새로 호출했다 — 매번 DB에 SELECT
# fetchall을 하나씩 더 날리는 셈이라, 프로파일링 결과 주간 진행 시간의
# 대부분(6초 중 4.8초)이 여기서 소모되고 있었다. 실제로 이 값이 쓰이는
# 곳(get_my_cup_matches 등)은 my_played=1인 "내 경기" 행뿐이라, 애초에
# AI끼리 경기에 대해서는 계산할 필요조차 없었다(그건 각 엔진의 _sim_ai_match
# 쪽에서 "내 경기일 때만 계산"하도록 함께 손봤다 — 이 함수는 그와 별개로,
# 혹시 남아있는 다른 호출 경로에서도 안전하게 빠르도록 (week, tid, season)
# 조합으로 메모이즈한다. 이 조합의 결과값은 시즌이 진행되는 동안 절대
# 바뀌지 않는다(경기 스케줄의 day는 시즌 시작 시 한 번 확정되고 이후
# 재조회해도 항상 같은 값 — advance_days가 매일 새로 부르는 함수인데
# 매번 똑같은 값을 다시 계산하고 있었다는 뜻).
_week_intl_cl_day_cache: dict = {}

def _week_intl_cl_day(week: int, p: dict, st: dict = None) -> int:
    """[2026-07 버그 수정] 국제대회/챔스는 'week' 단위로만 저장돼(day 컬럼
    없음), 화면 표시는 '그 주 마지막 날'로 보여주면서 실제 진행
    (advance_days)은 그 주 국내 경기가 없는 '아무 날짜'에나(사실상 항상
    그 주 첫날) 조용히 자동 처리해버렸다 — 화면엔 일요일 챔스로 보이는데
    실제로는 월요일에 이미 AI 처리되는 식으로 표시와 실제가 어긋났다.
    이제 화면(UI)과 실제 진행(advance_days) 둘 다 이 함수 하나로 통일해서
    '그 주 안의 정확히 같은 날짜'를 가리키게 한다.

    [2026-07 확장] 원래는 국내 경기와 '정확히 같은 날'만 피했는데, 그러면
    화(챔스)-수(국내) 같은 바로 이웃한 날짜 배정이 그대로 남아 이틀 연속
    경기가 생길 수 있었다. 이제 이번 주뿐 아니라 지난주·다음주 국내 경기
    날짜까지 다 조회해서, 그 어느 쪽과도 '하루 이내(당일 포함)'로는 안
    붙게 여러 후보 요일을 순서대로 시도한다(화요일 우선 → 금요일 →
    그 외). 실제 UEFA 챔스도 국내리그(주말)와 안 겹치는 미드위크에
    편성되는 것과 같은 원리.

    [2026-07 성능 수정] (week, 내 팀, 내 시즌)이 같으면 결과도 항상
    같으므로 캐시한다 — 위 모듈 docstring 참고.

    [2026-07 추가 최적화] st를 넘기면 get_state() 재조회를 생략한다."""
    tid = p.get("current_team_id", 0)
    if st is None:
        st = get_state()
    cur_season = st["current_season"] if st else 0
    cache_key = (week, tid, cur_season)
    cached = _week_intl_cl_day_cache.get(cache_key)
    if cached is not None:
        return cached

    week_start = (week - 1) * DAYS_PER_WEEK + 1
    dom_days = []  # 이번주/지난주/다음주 국내 경기일 목록(있는 것만)
    if tid:
        conn = get_conn()
        rows = conn.execute(
            """SELECT day FROM match_results WHERE week IN (?,?,?) AND season=?
               AND day IS NOT NULL AND (home_team_id=? OR away_team_id=?)""",
            (week - 1, week, week + 1, cur_season, tid, tid)).fetchall()
        conn.close()
        dom_days = [r["day"] for r in rows if r["day"] is not None]

    def _conflicts(cand):
        return any(abs(cand - dd) <= 1 for dd in dom_days if dd is not None)

    result = week_start + 2   # 극히 드문 경우(모든 요일이 다 걸림) 기본값
    for offset in (2, 5, 3, 4, 1, 6, 0):   # 화요일 우선 → 금요일 → 나머지
        cand = week_start + offset
        if not _conflicts(cand):
            result = cand
            break
    _week_intl_cl_day_cache[cache_key] = result
    return result


# ── 로그 버퍼: add_log 호출을 모아 flush_log_buffer()로 한 번에 INSERT ──
# 한 주차 진행 중 8~12회 add_log가 각각 get_state+INSERT+commit을 했던 것을
# 메모리에 쌓아뒀다가 advance_4weeks 루프 끝에서 1회 커밋으로 처리한다.
# year/week를 None으로 남기면 flush 시점에 실제 상태로 채운다.
_log_buffer: list = []  # [(text, log_type, year_or_None, week_or_None)]

def add_log(text: str, log_type="normal", year=None, week=None):
    """로그를 버퍼에 추가. flush_log_buffer()로 실제 DB에 기록."""
    _log_buffer.append((text, log_type, year, week))

def flush_log_buffer():
    """버퍼에 쌓인 로그를 한 번의 executemany + commit으로 DB에 기록."""
    if not _log_buffer:
        return
    st = get_state()
    cur_y = st["current_year"]
    cur_w = st["current_week"]
    rows = [(text, ltype,
             y if y is not None else cur_y,
             w if w is not None else cur_w)
            for text, ltype, y, w in _log_buffer]
    _log_buffer.clear()
    conn = get_conn()
    conn.executemany("INSERT INTO game_log(entry,log_type,year,week) VALUES(?,?,?,?)", rows)
    conn.commit()
    conn.close()


def get_logs(since_id=0):
    """[2026-07 성능 수정, 신민용 리포트: "20년 쌓였을 때랑 막 시작했을 때랑
    next day 속도가 같아야 하는데 다른 것 같다"] 원인을 찾았다 — 이 함수가
    호출될 때마다(=로그 패널이 새로고침될 때마다, 사실상 매일 "다음 날"마다)
    game_log 테이블 전체를 처음부터 끝까지 통째로 다시 읽었다. game_log는
    지워지지 않고 계속 쌓이기만 하는 테이블이라, 플레이 연차가 쌓일수록
    "다음 날" 한 번의 로그 새로고침 비용이 계속 커진다 — 초반엔 안 느껴지다가
    수십 년 지나면 누적된 만큼 매번 다시 읽고 다시 그리느라 체감 지연이 생긴다.

    이제 since_id를 받아 그 이후에 새로 추가된 로그만 반환한다(반환값도
    (새 로그 목록, 마지막으로 읽은 id)로 바뀜) — 호출부(ui/log_panel.py)가
    이 id를 기억해뒀다가 다음 새로고침 때 넘기면, 매번 "그날 새로 생긴
    줄"만 읽고 그리게 되어 하루치 비용이 항상 일정해진다(연차와 무관).
    since_id=0(기본값)이면 예전처럼 전체를 반환 — 최초 1회(게임 로드 직후)
    로그 패널을 처음 채울 때만 이 경로를 쓴다.

    [2026-08 확장, 신민용 요청: "로그를 1년 단위로 보이게, 새해 시작하면
    깨끗해지고 다시 쌓이게"] 각 줄이 어느 연도(year) 소속인지를 호출부가
    알아야 연도 경계를 찾아 화면을 비울 수 있다 — entry 텍스트만 주던 걸
    (entry, year) 튜플로 바꿨다(game_log.year 컬럼은 원래부터 있었음,
    여태 안 내려주고 있었을 뿐)."""
    flush_log_buffer()  # 버퍼에 남은 로그 먼저 기록
    conn = get_conn()
    c = conn.cursor()
    if since_id:
        c.execute("SELECT id, entry, year FROM game_log WHERE id>? ORDER BY id ASC", (since_id,))
    else:
        c.execute("SELECT id, entry, year FROM game_log ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    entries = [(r["entry"], r["year"]) for r in rows]
    max_id = rows[-1]["id"] if rows else since_id
    return entries, max_id


def _day_label(week, day=None):
    """[2026-07 추가] 로그에 넣을 날짜 라벨. 지금은 하루 단위로 진행되는데
    (advance_days) 로그엔 계속 '{week}주차'만 찍혀서, 같은 주 안의 7일치
    로그가 전부 똑같은 '5주차'로만 보여 어느 날 일어난 일인지 구분이
    안 됐다. day가 주어지면(=advance_days 경로) 실제 날짜(예: '2001년
    1월 5일')로 보여준다. day가 없으면(더는 안 쓰이는 구버전 주 단위
    advance_4weeks 경로) 기존처럼 '{week}주차'로 폴백해 하위호환 유지."""
    if day is not None:
        st = get_state()
        return day_to_full_date_str(st["current_year"], day)
    return f"{week}주차"


def get_match_detail(detail_id):
    """match_details 단건 조회 → dict(파싱된 detail_json 포함) 반환. 없으면 None."""
    try:
        conn = get_conn()
        row = conn.execute("SELECT * FROM match_details WHERE id=?",
                           (int(detail_id),)).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    d = dict(row)
    try:
        d["payload"] = json.loads(d.get("detail_json") or "{}")
    except Exception:
        d["payload"] = {}
    # [신규] possession_log는 detail_json과 별개의 컬럼으로 저장했지만
    # (이유: 경기당 통째로 쓰고 읽는 구조화 데이터라 JSON 컬럼이 더
    # 맞음), 소비하는 쪽(match_sim_viewer.py)은 다른 필드들처럼 그냥
    # payload 하나만 보면 되게 여기서 합쳐준다. 구버전 경기(컬럼 자체가
    # 비어있음)는 빈 리스트 — 뷰어가 자동으로 기존 사후-추측 로직으로
    # 폴백한다.
    try:
        d["payload"]["possession_log"] = json.loads(d.get("possession_log") or "[]")
    except Exception:
        d["payload"]["possession_log"] = []
    # [신규] lineup_stats도 possession_log와 같은 방식으로 payload에
    # 합쳐준다 — match_sim_viewer.py는 payload 하나만 보면 된다.
    try:
        d["payload"]["lineup_stats"] = json.loads(d.get("lineup_stats") or "{}")
    except Exception:
        d["payload"]["lineup_stats"] = {}
    return d


def recalc_ovr(p: dict) -> int:
    stats = {s: p.get(s, 40) for s in ALL_STATS}
    # [2026-08 버그수정] 신 등급이면 talent_cap(100~105 개인별 값)을 상한으로,
    # 그 외는 기존과 동일하게 100(→ 호출부에 따라 이후 99 클램프가 또 걸림).
    _cap = p.get("talent_cap", 100) if p.get("talent_tier") == "god" else 100
    return calc_ovr(p.get("position", "CM"), stats, cap=_cap)


def _position_mismatch_rank(primary_pos: str, field_pos: str) -> int:
    """[2026-08 신설] primary_pos(등록 주 포지션) 기준으로 field_pos(실제
    배치 포지션)가 몇 순위인지 반환. POSITION_COMPAT 리스트 안에 있으면
    그 인덱스(0=주 포지션 자신), 리스트에 없으면(완전히 안 맞는 포지션)
    POSITION_MISMATCH_PENALTY의 마지막 인덱스(조건부, 가장 큰 페널티)로
    취급한다."""
    compat = POSITION_COMPAT.get(primary_pos, [primary_pos])
    if field_pos in compat:
        rank = compat.index(field_pos)
    else:
        rank = len(POSITION_MISMATCH_PENALTY) - 1
    return min(rank, len(POSITION_MISMATCH_PENALTY) - 1)


def calc_positional_ovr(primary_pos: str, stats: dict, field_pos: str, cap: int = 100) -> float:
    """[2026-08 신설, 신민용 확정] 특정 포지션(field_pos)에 배치됐을 때의
    '실질 OVR' — calc_ovr(field_pos, stats)로 그 포지션 가중치 기준 OVR을
    먼저 구하고(포지션 적합도의 1차 반영, WEIGHTS 차이에서 이미 발생),
    거기에 POSITION_MISMATCH_PENALTY(주 포지션과 얼마나 먼 자리인지에 따른
    작은 절대 OVR 보정 — 그 포지션이 '전문 분야가 아니다'라는 추가 감점)를
    뺀다. 두 페널티의 역할을 겹치지 않게 분리하는 게 핵심이라, 여기 절대
    차감폭은 -0.5~-5.0으로 작게 유지한다(그 이상은 이중 페널티가 된다).
    field_pos == primary_pos면 당연히 차감 없음(주 포지션 그대로)."""
    base = calc_ovr(field_pos, stats, cap=cap)
    rank = _position_mismatch_rank(primary_pos, field_pos)
    penalty = POSITION_MISMATCH_PENALTY[rank]
    return round(base - penalty, 1)


def _age_train_eff(age: int, peak_age: int) -> float:
    """나이별 훈련 효율 배수.
    [2026-07 재설계, 신민용 지적: "16~18세 더딤이 현실적인가?"] 예전엔
    16~18세를 0.80→1.05로 낮게 잡았는데, 이건 두 가지 문제가 있었다:
    (1) 현실적으로 10대 후반은 기술/판단력 습득 속도가 가장 빠른 시기다
        (신체 능력만 아직 안 여물었을 뿐).
    (2) "재능 상한에 가까워질수록 둔화"는 이미 소프트캡이 별도로 처리하고
        있는데, 나이 곡선까지 "초반엔 느리게"로 겹치면 이중으로 깎인다.
    그래서 16세부터 이미 높은 효율(1.20)로 시작해 peak까지 완만히
    상승(1.35)하는 것으로 바꿨다.

    [2026-07 추가 재설계, 신민용 확정: "전성기는 23~28세(peak_age~+5),
    29~31세(+5~+8)는 미세하게만 하락, 33세(+8)부터 본격적으로 한계가
    떨어진다"] 하락 구간을 3단계로 세분화 — peak_age는 등급별로 다르므로
    (월드클래스 23~25세 등) 절대 나이가 아니라 peak_age 기준 상대 오프셋
    으로 구간을 나눈다(월드클래스 하한 23세 기준일 때 23~28/28~31/31~35
    로 맞아떨어짐).
    """
    if age <= peak_age:
        span = max(1, peak_age - 16)
        t = (age - 16) / span
        return round(1.20 + 0.15 * t, 3)
    if age <= peak_age + 5:
        # 전성기 유지 구간 — 거의 안 떨어짐(1.35 → 1.28, 아주 완만)
        t = (age - peak_age) / 5.0
        return round(1.35 - 0.07 * t, 3)
    if age <= peak_age + 8:
        # 미세한 하락 (28→31세 무렵)
        t = (age - (peak_age + 5)) / 3.0
        return round(1.28 - 0.23 * t, 3)   # 1.28 → 1.05
    if age <= 35:
        # 본격 하락 (31/33세 무렵부터 서서히 한계가 떨어짐)
        t = (age - (peak_age + 8)) / max(1, 35 - (peak_age + 8))
        return round(1.05 - 0.65 * t, 3)   # 1.05 → 0.40
    return 0.35


# ═══════════════════════════════════════════
# 선수 생성
# ═══════════════════════════════════════════

def create_player(name: str, position: str, sub_role: str,
                  nationality: str = None, flag: str = None, talent_tier: str = None,
                  personality: str = None, physical_trait: str = None,
                  start_year: int = None, start_age: int = None,
                  difficulty: str = "easy"):
    # [2026-08 신설, 신민용 요청: "새 선수 생성 때 시작 연도/나이도 고를 수
    # 있게"] 입력 없으면(None) 기존과 완전히 동일하게 기본 상수를 쓴다 —
    # 이 매개변수 자체가 하위호환을 깨지 않도록 옵션으로만 존재.
    _start_year = start_year if start_year is not None else GAME_START_YEAR
    _start_age = start_age if start_age is not None else PLAYER_START_AGE
    _min_join_age = _start_age + 1  # "선택한 나이 다음 해부터 입단 가능"
    conn = get_conn()
    c = conn.cursor()

    if not nationality:
        c.execute("SELECT name,flag FROM countries ORDER BY RANDOM() LIMIT 1")
        row = c.fetchone()
        nationality, flag = row["name"], row["flag"]

    # [2026-07 제거] 예전엔 여기서 20%→8% 확률로 완전 무작위 국가를
    # 2번째/3번째 국적으로 몰래 끼워넣었다(현실 이민자 가정 컨셉이었으나,
    # 플레이어가 선택하지도 확인하지도 못한 채 생성 즉시 조용히 부여되는
    # 방식이라 "왜 갑자기 이 국적이 생겼지" 하는 혼란만 유발했다). 시작
    # 국적은 이제 플레이어가 고른 1개뿐이고, 추가 국적은 실제 플레이(그
    # 나라 리그에서 거주 귀화)로만 늘어난다.
    nationality2, flag2 = "", ""
    nationality3, flag3 = "", ""

    # [2026-07 추가] 새 게임 화면에서 성격/신체특징도 직접 고를 수 있게 됐다
    # (talent_tier와 같은 패턴) — None이면(안 고르면) 기존처럼 확률 추첨.
    if not personality:
        personality = random.choice(PERSONALITIES)
    # [신체 특징] 성격과 별개로 1개 부여 (가중 추첨 — 무난함이 흔함)
    from constants import (PHYSICAL_TRAITS, PHYSICAL_TRAIT_WEIGHTS, PHYSICAL_TRAIT_EFFECTS,
                           BODY_TYPES, BODY_TYPE_NAMES, BODY_TYPE_WEIGHTS_BY_POS)
    if not physical_trait:
        physical_trait = random.choices(PHYSICAL_TRAITS, PHYSICAL_TRAIT_WEIGHTS)[0]

    # [신체 아키타입] 체형 유형 추첨. 포지션이 확률을 기울이되 고정하진 않는다
    # (윙어인데 포켓로켓/메시형, 작은데 종결자 체급 등 예외 허용).
    _bw = BODY_TYPE_WEIGHTS_BY_POS.get(position, [25, 25, 25, 25])
    body_type = random.choices(BODY_TYPE_NAMES, _bw)[0]
    _bt = BODY_TYPES[body_type]
    body_bias = _bt["stat_bias"]
    # 체형이 정한 범위 안에서 키/체중 결정
    height = random.randint(*_bt["height"])
    weight = random.randint(*_bt["weight"])

    # 재능 등급 (TALENT_TIER_ORDER 9단계) → 고강도 돌파 상한 결정.
    # [신규] 새 게임 화면에서 선수가 직접 등급을 고를 수 있게 됐다 — talent_tier가
    # 유효한 값으로 넘어오면 그걸 그대로 쓰고, 없거나(None) 잘못된 값이면
    # (구버전 호출부 호환) 예전처럼 확률 추첨으로 정한다.
    # [2026-08 수정, 신민용 확정: 9단계 확장] 예전엔 5개 티어 이름이
    # 하드코딩돼 있어서 새로 추가된 신/슈퍼스타/아마추어/재능없음이 랜덤
    # 추첨에 절대 안 걸렸다 — TALENT_TIER_ORDER를 그대로 순회하도록 고쳐서
    # 새 등급이 추가/변경돼도 이 루프는 항상 최신 목록을 따라간다.
    if talent_tier not in TALENT_TIERS:
        _r = random.random()
        _acc = 0.0
        talent_tier = "pro"
        for _tname in TALENT_TIER_ORDER:
            _acc += TALENT_TIERS[_tname]["prob"]
            if _r < _acc:
                talent_tier = _tname
                break
    _tt = TALENT_TIERS[talent_tier]
    talent_cap = random.randint(_tt["cap_min"], _tt["cap_max"])

    # 피크 나이: 재능이 클수록 잠재력을 다 끌어내는 데 오래 걸려 늦게 정점에
    #   도달한다(월클 25~27, 평범 19~21). 성장기(16~peak)가 길수록 천천히 오른다.
    # [2026-07 재조정, 신민용 확정] 전체 2년씩 앞당김 — "전성기는 보통
    # 24~27세"라는 현실 감각에 맞춰 월드클래스 25~27→23~25세, 나머지
    # 등급도 같은 폭으로 비례 조정(등급 간 간격/순서는 그대로 유지).
    # [2026-08 확장, 신민용 확정: 9단계] 기존 5개(worldclass~ordinary)는
    # 값을 그대로 두고, 새로 추가된 4개(god/superstar/amateur/untalented)를
    # 인접 등급 사이 값으로 채워 넣었다 — 순서(재능 클수록 늦게 피크)만
    # 유지, 세부 수치는 추후 플레이 감각에 맞춰 조정 가능.
    _peak_by_tier = {
        "god":         (24, 26),
        "worldclass":  (23, 25),
        "superstar":   (22, 24),
        "elite":       (21, 23),
        "pro":         (19, 21),
        "semipro":     (18, 20),
        "amateur":     (17, 19),
        "ordinary":    (16, 18),
        "untalented":  (16, 17),
    }
    peak_age = random.randint(*_peak_by_tier.get(talent_tier, (22, 24)))

    # 시작 스탯(16세) + 일반훈련 천장(max). max는 talent_cap을 넘지 않음.
    #   16세 시작은 낮게 잡아 20대 중반까지 천천히 성장하도록 한다.
    #   (성장 페이스는 _age_train_eff 에이징커브가 함께 결정)
    # [2026-08 재조정, 신민용 확정] 예전엔 시작(target)이 talent_cap 대비
    # 몇 %인지가 등급마다 크게 벌어져 있었다 — 신(50%)·월드클래스(48%)는
    # 20대 중반까지 성장 서사가 있었지만, 평범(77%)·재능없음(84%)은
    # 16세에 이미 cap 턱밑이라 "성장"이랄 게 거의 없었다. 그래서 상위
    # 등급은 거의 그대로 두고, 하위 등급일수록 더 크게 낮춰서 어느
    # 등급이든 cap 대비 시작 비율이 46~55% 선으로 고르게 깔리도록
    # 재조정했다(dev·mx_add는 그대로 — 변동폭·훈련 성장치 성격은 유지).
    if talent_tier == "god":
        target = random.randint(44, 50); dev = random.randint(6, 10)
        mx_add = (50, 64)
    elif talent_tier == "worldclass":
        target = random.randint(40, 48); dev = random.randint(7, 11)
        mx_add = (44, 58)
    elif talent_tier == "superstar":
        target = random.randint(37, 45); dev = random.randint(7, 12)
        mx_add = (42, 56)
    elif talent_tier == "elite":
        target = random.randint(35, 42); dev = random.randint(8, 12)
        mx_add = (40, 54)
    elif talent_tier == "pro":
        target = random.randint(31, 38); dev = random.randint(9, 13)
        mx_add = (34, 48)
    elif talent_tier == "semipro":
        target = random.randint(27, 33); dev = random.randint(10, 14)
        mx_add = (28, 42)
    elif talent_tier == "amateur":
        target = random.randint(23, 29); dev = random.randint(10, 15)
        mx_add = (25, 39)
    elif talent_tier == "untalented":
        target = random.randint(16, 21); dev = random.randint(12, 17)
        mx_add = (18, 30)
    else:  # ordinary
        target = random.randint(20, 26); dev = random.randint(11, 16)
        mx_add = (22, 36)

    stat_vals = {}
    # [해결A] 포지션 색깔: OVR 가중치를 재활용해 핵심 스탯은 살짝 높게,
    # 비핵심 스탯은 살짝 낮게 생성한다. "은은하게" — 보정폭을 작게 둬서
    # ST인데 슈팅이 바닥 같은 부자연스러운 시작만 막고, 개성은 유지.
    from database import WEIGHTS as _OVR_W
    pos_w = _OVR_W.get(position, {})
    # 가중치 평균(보통 6~7 근처)을 기준으로 +-방향 결정
    if pos_w:
        _avg_w = sum(pos_w.values()) / len(pos_w)
    else:
        _avg_w = 6.0
    # [신체 특징] 초반 신체 스탯 보너스 (신체천재 등)
    _trait_fx   = PHYSICAL_TRAIT_EFFECTS.get(physical_trait, {})
    _phys_start = _trait_fx.get("phys_start_bonus", 0)   # 신체천재: +8
    _trait_phys_stat = _trait_fx.get("phys_stat")        # 스피드스타: 'speed'만

    for s in ALL_STATS:
        # 가중치 편차를 작은 보정값으로 변환 (가중치 15 → 약 +6, 가중치 0 → 약 -5)
        w = pos_w.get(s, _avg_w)
        bias = round((w - _avg_w) * 0.7)
        bias = max(-6, min(7, bias))   # 과도한 쏠림 방지 (은은하게)
        # 신체 특징 보너스: 신체천재는 신체 3종 전체, 스피드스타는 speed만
        tbonus = 0
        if _phys_start and s in PHYSICAL_STATS:
            if _trait_phys_stat is None or _trait_phys_stat == s:
                tbonus = _phys_start
        # [신체 아키타입] 체형 보정 (현실적 ±5~8). 시작 스탯과 잠재 양쪽에 반영.
        bbias = body_bias.get(s, 0)

        cur = max(18, min(74, target + random.randint(-dev, dev) + bias + tbonus + bbias))

        # ── 스탯 상한 차등 ──────────────────────────────────────
        # 핵심 원칙:
        #   · 일반훈련 천장(max)은 talent_cap 부근에서 강/약점에 따라 흩어진다.
        #     (강점은 cap에 근접, 약점은 cap보다 확실히 낮게 → 평준화 방지)
        #   · 개별 스탯이 100을 넘는 것은 오직 '고강도 돌파(talent_cap+α)'로만
        #     가능하고, 일반훈련 max 자체는 그 천장(break_cap)을 못 넘는다.
        #   · OVR(평균)의 천장은 talent_cap 이라, 강점이 100을 넘어도 약점이
        #     낮아 평균은 cap 부근에서 균형잡힌다.
        is_strong = (bias + bbias) >= 3     # 포지션·체형이 함께 미는 강점
        is_weak   = (bias + bbias) <= -3    # 명확한 약점
        # 고강도 돌파로 도달 가능한 절대 천장 (강점만 100 초과 허용)
        if is_strong:
            break_cap = min(125, talent_cap + 12)   # 주특기는 고강도로 100+ 가능
        elif is_weak:
            break_cap = min(99,  talent_cap - 6)     # 약점은 천장이 낮음
        else:
            break_cap = min(110, talent_cap + 2)     # 평범 스탯
        # 일반훈련 천장(max)은 break_cap 보다 4~10 낮게 둔다 → 그 위(특히 100+)는
        # 고강도 훈련으로만 돌파. 재능 있어도 일반훈련만으론 100 못 감.
        soft_cap = break_cap - random.randint(4, 10)
        mx = min(soft_cap, cur + random.randint(*mx_add))
        mx = max(mx, cur + 4)        # 최소한의 성장 여지
        mx = max(28, min(break_cap, mx))
        stat_vals[s] = cur
        stat_vals[f"{s}_max"] = mx

    # [2026-08 버그수정] 신 등급이면 talent_cap(100~105)을 그대로 상한으로
    # 써야 실제로 100을 넘길 수 있다 — calc_ovr 기본 cap(100)만 쓰면
    # TALENT_TIERS["god"]의 cap_max=105 설정이 여기서 다시 100으로 잘림.
    _ovr_cap = talent_cap if talent_tier == "god" else 100
    ovr = calc_ovr(position, stat_vals, cap=_ovr_cap)

    conn.execute("""
    INSERT INTO my_player(
        id, name, nationality, flag, age, birth_year,
        position, sub_role, personality, height, weight, peak_age,
        stamina,stamina_max, speed,speed_max, jump,jump_max, strength,strength_max,
        shooting,shooting_max, passing,passing_max, dribbling,dribbling_max,
        tackling,tackling_max, heading,heading_max, positioning,positioning_max,
        setpiece,setpiece_max, mental,mental_max, confidence,confidence_max,
        leadership,leadership_max, concentration,concentration_max,
        ovr, current_year, current_week, current_season,
        stress, happiness, agent_grade, language,
        talent_cap, talent_tier, physical_trait, body_type
    ) VALUES (
        1,?,?,?,?,?,
        ?,?,?,?,?,?,
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
        ?,?,?,?,
        15,50,'F','ko',
        ?,?,?,?
    )""", (
        name, nationality, flag, _start_age, _start_year - _start_age,
        position, sub_role, personality, height, weight, peak_age,
        stat_vals["stamina"],    stat_vals["stamina_max"],
        stat_vals["speed"],      stat_vals["speed_max"],
        stat_vals["jump"],       stat_vals["jump_max"],
        stat_vals["strength"],   stat_vals["strength_max"],
        stat_vals["shooting"],   stat_vals["shooting_max"],
        stat_vals["passing"],    stat_vals["passing_max"],
        stat_vals["dribbling"],  stat_vals["dribbling_max"],
        stat_vals["tackling"],   stat_vals["tackling_max"],
        stat_vals["heading"],    stat_vals["heading_max"],
        stat_vals["positioning"],stat_vals["positioning_max"],
        stat_vals["setpiece"],   stat_vals["setpiece_max"],
        stat_vals["mental"],     stat_vals["mental_max"],
        stat_vals["confidence"], stat_vals["confidence_max"],
        stat_vals["leadership"], stat_vals["leadership_max"],
        stat_vals["concentration"],stat_vals["concentration_max"],
        ovr, _start_year, 1, 1,
        talent_cap, talent_tier, physical_trait, body_type,
    ))

    # [복수국적] 추가 국적/국기 저장 (단일국적이면 빈 값)
    conn.execute("UPDATE my_player SET nationality2=?, flag2=?, nationality3=?, flag3=? WHERE id=1",
                 (nationality2, flag2, nationality3, flag3))

    # [출생국적] 태어난 고향(=1차 국적)을 별도로 영구 보존. 귀화/대표선택과 무관.
    #   디에고 코스타처럼 '출생국 ≠ 대표국'을 은퇴요약에서 구분하기 위함.
    conn.execute("UPDATE my_player SET origin_nat=?, origin_flag=? WHERE id=1",
                 (nationality, flag))

    # [2026-08 신설] 이 선수만의 입단 가능 최소 나이(선택한 시작 나이+1) 저장.
    conn.execute("UPDATE my_player SET min_join_age=? WHERE id=1", (_min_join_age,))

    # [2026-08 신설, 난이도 시스템] 생성 시 한 번만 확정되고 이후 변경
    # 불가 — 캐릭터 생성 화면에서 넘어온 값을 그대로 저장. 값 검증(잘못된
    # 문자열이 넘어오면 안전하게 easy로)까지 여기서 한 번 더 한다 —
    # 정보 제한이 걸리는 화면(player_panel/포메이션/팀검색)들이 전부 이
    # 컬럼 하나만 보고 판단하므로, 여기서 걸러두면 그쪽에서 매번 방어
    # 코드를 반복할 필요가 없다.
    if difficulty not in ("easy", "normal", "hard"):
        difficulty = "easy"
    conn.execute("UPDATE my_player SET difficulty=? WHERE id=1", (difficulty,))

    # [2026-08 신설] 실제 선택된 시작 연도를 영구 저장 — intl_engine.py가
    # 이걸 기준으로 월드컵/네이션스컵/클럽월드컵/지역컵 개최년도를 다시
    # 계산한다(자세한 이유는 database.set_game_start_year 주석 참고).
    from database import set_game_start_year
    set_game_start_year(_start_year)

    # [전성기 OVR] 시작 OVR로 초기화 (이후 update_player가 자동으로 최고치 갱신).
    conn.execute("UPDATE my_player SET peak_ovr=? WHERE id=1", (ovr,))

    # [2026-08 신설, 신민용 확정] 훈련 gain 진행률(%) 감속 커브의 기준선
    # 스냅샷 — 방금 굴린 시작 스탯값을 그대로 stat_start에 박아두고
    # 이후 커리어 내내 바뀌지 않는다(_progress_soft가 이 값을 기준으로
    # "시작→한계 전체 구간 중 몇 % 왔는지"를 계산).
    conn.execute("UPDATE my_player SET stat_start=? WHERE id=1",
                 (json.dumps({s: stat_vals[s] for s in ALL_STATS}),))

    # [국적 연혁] 출생 시점 보유 국적을 birth 이벤트로 기록(시작국적 + 복수국적).
    #   첫 항목이 '시작국적'이 되도록 1차 국적을 맨 앞에 둔다.
    _birth_hist = [{"type": "birth", "nat": nationality, "flag": flag,
                    "year": _start_year, "week": 1}]
    if nationality2:
        _birth_hist.append({"type": "birth", "nat": nationality2, "flag": flag2,
                            "year": _start_year, "week": 1})
    if nationality3:
        _birth_hist.append({"type": "birth", "nat": nationality3, "flag": flag3,
                            "year": _start_year, "week": 1})
    conn.execute("UPDATE my_player SET nat_history=? WHERE id=1",
                 (json.dumps(_birth_hist, ensure_ascii=False),))

    # 시즌 상태 초기화
    conn.execute("""INSERT OR REPLACE INTO season_state(id,current_year,current_week,
                    current_season,phase) VALUES(1,?,1,1,'preseason')""",
                 (_start_year,))
    conn.commit()
    conn.close()
    # [2026-08 신설] get_state() 캐시가 이 함수 호출 전에 이미 채워져
    # 있었을 수도 있으므로(예: 새 게임 시작 전 화면 로딩 등) 새 시즌 상태로
    # 강제 무효화 — 다음 get_state()가 방금 쓴 값을 새로 읽어온다.
    _invalidate_state_cache()

    # [실시간 전환] 이제까지는 시즌 1(=게임 시작 연도)의 전 세계 일정 생성이
    # 연도 전환 시점(_advance_week)에서만 호출돼서, 정작 첫 시즌(2000년 등)엔
    # 아무도 안 걸렸다 — 그래서 시즌 1은 오퍼에 뜬 리그(내 리그 등)만 그 해
    # 기록이 있고, 나머지 전 세계 리그는 시즌 2(다음 해)부터 기록이 시작되는
    # 불일치가 있었다. 새 커리어 시작 시점에 한 번 호출해 시즌 1도 처음부터
    # 전 세계 모든 리그가 동일하게 그 해부터 기록을 갖게 한다.
    _generate_all_league_schedules(1, _start_year)

    add_log(f"⭐ {_start_year}년  —  {name} {_start_age}세", "event")
    add_log("─"*44, "sep")


# ═══════════════════════════════════════════
# 4주 진행
# ═══════════════════════════════════════════

def _find_open_entry(c, tid, team_name):
    """열린 커리어 항목(end_year=0) 조회.
    team_id 우선 매칭, 구버전 세이브(team_id=0) 행은 팀명으로 폴백.
    (동명 팀이 여러 나라에 존재하므로 이름 단독 매칭은 금지)"""
    row = c.execute(
        """SELECT id FROM career_entries
           WHERE team_id=? AND end_year=0 ORDER BY id DESC LIMIT 1""",
        (tid,)).fetchone()
    if row:
        return row
    return c.execute(
        """SELECT id FROM career_entries
           WHERE team_id=0 AND team_name=? AND end_year=0
           ORDER BY id DESC LIMIT 1""",
        (team_name,)).fetchone()


def _calc_clean_sheets(c, tid, season, matches=None):
    """해당 시즌 소속 팀의 클린시트(무실점 경기) 수 집계.
    matches: 그 팀에서 선수가 실제 출전한 경기 수. 주어지면 이를 상한으로 적용한다.
      (버그수정) 기존엔 출전 0인 신규 이적팀도 그 팀이 시즌 전체에 쌓은
      무실점 경기 수를 그대로 반환해, 0출전인데 무실점 5가 찍혔다.
      선수가 안 뛴 경기의 무실점은 그 선수 기록이 아니므로 출전수로 캡한다."""
    if matches is not None and matches <= 0:
        return 0
    row = c.execute("SELECT league_id FROM teams WHERE id=?", (tid,)).fetchone()
    if not row:
        return 0
    q = c.execute(
        """SELECT COUNT(*) as cnt FROM match_results
           WHERE league_id=? AND season=? AND home_score>=0
           AND ((home_team_id=? AND away_score=0)
             OR (away_team_id=? AND home_score=0))""",
        (row["league_id"], season, tid, tid)).fetchone()
    cs = q["cnt"] if q else 0
    if matches is not None:
        cs = min(cs, matches)
    return cs


def _team_league_id_for_season(c, team_id, season):
    """[버그수정 2026-08, 신민용 리포트: "2004년에 승급했는데 그 해 기록이
    0승0무0패에 리그명도 승격된 리그로 잘못 나온다"] teams.league_id는
    "지금" 그 팀이 소속된 리그다 — 그런데 승강 처리(_process_promotion_
    relegation)는 클럽 시즌이 끝나는 43주에 이미 실행되고, 다음 시즌(다음
    해)부터 반영된다. 그 사이(44~52주, 국제대회 기간) '이번 시즌' 커리어
    기록을 갱신하면 teams.league_id가 이미 승격/강등된 새 리그를 가리키고
    있어서, 그 리그+시즌으로 match_results를 조회하면 (그 팀은 실제로 거기서
    안 뛰었으니) 0건이 나와 전적이 통째로 0승0무0패/리그명도 잘못 찍혔다.

    같은 시즌 안에서는 팀의 league_id가 항상 하나로 일관되므로(승강은 시즌
    경계에서만 반영), 그 시즌에 실제로 뛴 경기의 league_id를 match_results/
    match_results_archive에서 직접 찾아 쓴다 — 이번 시즌 경기를 아직 하나도
    안 뛰었으면(막 이적/입단 직후라 일정이 비어있는 경우) None을 반환하니
    호출부가 teams.league_id로 폴백하면 된다(그 경우는 애초에 승강 이슈가
    생길 수 없으므로 안전)."""
    row = c.execute(
        """SELECT league_id FROM match_results
           WHERE (home_team_id=? OR away_team_id=?) AND season=? LIMIT 1""",
        (team_id, team_id, season)).fetchone()
    if row:
        return row["league_id"]
    row = c.execute(
        """SELECT league_id FROM match_results_archive
           WHERE (home_team_id=? OR away_team_id=?) AND season=? LIMIT 1""",
        (team_id, team_id, season)).fetchone()
    return row["league_id"] if row else None


def _team_wdl_from_results(c, tid, league_id, season):
    """[버그수정 2026-07, 신민용 리포트: "커리어 팀 전적이 세계기록실 순위표랑
    다르다 — 실제 44경기 시즌인데 69승무패로 나온다"] teams.wins/draws/losses는
    매 시즌 리셋되긴 하지만, 그 사이 다른 경로(오퍼 창 미리보기 시뮬레이션 등)에서
    같은 리그+시즌에 중복 일정이 끼어들면 이 카운터가 실제 경기 수보다 부풀 수
    있다 — 반면 world_browser 순위표(get_league_standings)는 항상 match_results를
    직접 집계해서 보여주므로 그쪽이 진짜 정확한 값이다. _save_career_entry는
    이미 이 방식으로 고쳐져 있었는데 _update_career_stats/_close_career_entry는
    여전히 옛 카운터를 읽고 있었다 — 여기서 셋 다 같은 방식(match_results 직접
    집계)을 쓰도록 통일한다."""
    tw = td = tl = 0
    if not league_id:
        return tw, td, tl
    rows = c.execute(
        """SELECT home_team_id, away_team_id, home_score, away_score
           FROM match_results WHERE league_id=? AND season=? AND home_score>=0""",
        (league_id, season)).fetchall()
    for row in rows:
        hid, aid, hs, as_ = row["home_team_id"], row["away_team_id"], row["home_score"], row["away_score"]
        if hid == tid:
            if hs > as_: tw += 1
            elif hs == as_: td += 1
            else: tl += 1
        elif aid == tid:
            if as_ > hs: tw += 1
            elif as_ == hs: td += 1
            else: tl += 1
    return tw, td, tl


def _update_career_stats(p, year, week):
    """열린 커리어 항목의 스탯만 갱신. end_year는 건드리지 않음."""
    tid = p.get("current_team_id", 0)
    if not tid: return
    conn = get_conn()
    c = conn.cursor()
    team_row = c.execute("""SELECT t.name, l.id as lid, l.name as lname, l.tier
                             FROM teams t JOIN leagues l ON t.league_id=l.id
                             WHERE t.id=?""", (tid,)).fetchone()
    if not team_row:
        conn.close(); return
    existing = _find_open_entry(c, tid, team_row["name"])
    if not existing:
        conn.close(); return

    # [최적화] 이미 열린 conn 재사용하여 get_team_rank 내부 커넥션 중복 방지
    season = p.get("current_season", 1)

    # [버그수정 2026-08, 신민용 리포트: "2004년에 승급했는데 그 해 기록이
    # 0승0무0패/리그명도 승격된 리그로 잘못 나온다"] team_row(teams.league_id
    # 조인, 즉 "지금" 소속 리그)를 그대로 쓰면 43주 승강 반영 후(44~52주)엔
    # '이번 시즌 실제로 뛴 리그'가 아니라 '다음 시즌 리그'가 찍힌다 —
    # _team_league_id_for_season으로 그 시즌 실제 리그를 우선 쓰고, 이번
    # 시즌 경기가 아직 없으면(막 이적 직후) team_row로 폴백한다.
    _season_lid = _team_league_id_for_season(c, tid, season)
    if _season_lid is not None:
        _season_league_row = c.execute(
            "SELECT name, tier FROM leagues WHERE id=?", (_season_lid,)).fetchone()
    else:
        _season_league_row = None
    lname = _season_league_row["name"] if _season_league_row else team_row["lname"]
    tier  = _season_league_row["tier"] if _season_league_row else team_row["tier"]
    lid   = _season_lid if _season_lid is not None else team_row["lid"]

    rank_str = get_team_rank(tid, conn=conn, season=season)
    try: rn = int(rank_str.split("위")[0].replace("공동","").strip())
    except (ValueError, AttributeError, IndexError): rn = 0

    sm  = p.get("season_matches", 0)
    sg  = p.get("season_goals", 0)
    sa  = p.get("season_assists", 0)
    ss  = p.get("season_saves", 0)
    sga = p.get("season_goals_against", 0)
    rc  = p.get("season_rating_cnt", 0)
    rs  = p.get("season_rating_sum", 0.0)
    avg_r = round(rs/rc, 2) if rc else 0.0
    # [세부 지표] 시즌 누적 → 커리어 행
    d_sh, d_sho = p.get("season_shots",0), p.get("season_shots_on",0)
    d_kp, d_drb, d_blk = p.get("season_key_passes",0), p.get("season_dribbles",0), p.get("season_blocks",0)
    _pac_c = p.get("season_pass_acc_cnt",0)
    d_pac = round(p.get("season_pass_acc_sum",0.0)/_pac_c, 3) if _pac_c else 0.0

    # [버그수정 2026-07] teams 테이블의 누적 카운터 대신 match_results에서
    # 직접 집계 (_save_career_entry와 동일 방식 — world_browser 순위표와
    # 항상 일치하도록).
    tw, td, tl = _team_wdl_from_results(c, tid, lid, season)

    cs = _calc_clean_sheets(c, tid, season, matches=sm)
    rc_league = p.get("season_red_cards_league", 0)

    c.execute("""UPDATE career_entries SET
        matches=?, goals=?, assists=?, saves=?, goals_against=?,
        avg_rating=?, team_rank=?, wins=?, draws=?, losses=?, clean_sheets=?,
        shots=?, shots_on=?, key_passes=?, dribbles=?, blocks=?, pass_acc=?,
        team_id=?, league_name=?, tier=?, red_cards=?
        WHERE id=?""",
        (sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs,
         d_sh, d_sho, d_kp, d_drb, d_blk, d_pac, tid,
         lname, tier, rc_league, existing["id"]))
    conn.commit()
    conn.close()


def _close_career_entry(p, year, week, exit_type=""):
    """현재 팀의 열린 커리어 항목(end_year=0)을 닫음. 연도별 분리용.
    exit_type: 그 팀에서 떠난 경로('팔림'/'방출'/'이적'/'계약만료'/''=재직중)."""
    tid = p.get("current_team_id", 0)
    if not tid: return

    conn = get_conn()
    c = conn.cursor()

    team_row = c.execute("""SELECT t.name, l.id as lid, l.name as lname, l.tier
                             FROM teams t JOIN leagues l ON t.league_id=l.id
                             WHERE t.id=?""", (tid,)).fetchone()
    if not team_row:
        conn.close(); return

    existing = _find_open_entry(c, tid, team_row["name"])
    if not existing:
        conn.close(); return

    # [최적화] 이미 열린 conn 재사용
    season = p.get("current_season", 1)

    # [버그수정 2026-08, 신민용 리포트: "2004년에 승급했는데 그 해 기록이
    # 0승0무0패/리그명도 승격된 리그로 잘못 나온다"] team_row(teams.league_id
    # 조인, "지금" 소속 리그)를 그대로 쓰면 43주 승강 반영 후(44~52주, 국제
    # 대회 기간에 시즌을 닫는 경우)엔 '이번 시즌 실제로 뛴 리그'가 아니라
    # '다음 시즌 리그'가 이 닫히는 행에 찍힌다 — _team_league_id_for_season
    # 으로 그 시즌 실제 리그를 우선 쓰고, 경기가 아예 없었으면(이론상 발생
    # 안 하지만 안전하게) team_row로 폴백한다.
    _season_lid = _team_league_id_for_season(c, tid, season)
    if _season_lid is not None:
        _season_league_row = c.execute(
            "SELECT name, tier FROM leagues WHERE id=?", (_season_lid,)).fetchone()
    else:
        _season_league_row = None
    lname = _season_league_row["name"] if _season_league_row else team_row["lname"]
    tier  = _season_league_row["tier"] if _season_league_row else team_row["tier"]
    lid   = _season_lid if _season_lid is not None else team_row["lid"]

    rank_str = get_team_rank(tid, conn=conn, season=season)
    try:
        rn = int(rank_str.split("위")[0].replace("공동","").strip())
    except (ValueError, AttributeError, IndexError):
        rn = 0

    sm  = p.get("season_matches", 0)
    sg  = p.get("season_goals", 0)
    sa  = p.get("season_assists", 0)
    ss  = p.get("season_saves", 0)
    sga = p.get("season_goals_against", 0)
    rc  = p.get("season_rating_cnt", 0)
    rs  = p.get("season_rating_sum", 0.0)
    avg_r = round(rs/rc, 2) if rc else 0.0
    # [세부 지표]
    d_sh, d_sho = p.get("season_shots",0), p.get("season_shots_on",0)
    d_kp, d_drb, d_blk = p.get("season_key_passes",0), p.get("season_dribbles",0), p.get("season_blocks",0)
    _pac_c2 = p.get("season_pass_acc_cnt",0)
    d_pac = round(p.get("season_pass_acc_sum",0.0)/_pac_c2, 3) if _pac_c2 else 0.0

    # [버그수정 2026-07] teams 테이블의 누적 카운터 대신 match_results에서
    # 직접 집계 (_save_career_entry와 동일 방식 — world_browser 순위표와
    # 항상 일치하도록).
    tw, td, tl = _team_wdl_from_results(c, tid, lid, season)

    cs = _calc_clean_sheets(c, tid, season, matches=sm)
    rc_league = p.get("season_red_cards_league", 0)

    c.execute("""UPDATE career_entries SET
        end_year=?, end_week=?, matches=?, goals=?, assists=?, saves=?, goals_against=?,
        avg_rating=?, team_rank=?, wins=?, draws=?, losses=?, clean_sheets=?,
        shots=?, shots_on=?, key_passes=?, dribbles=?, blocks=?, pass_acc=?,
        league_name=?, tier=?, salary=?, position=?, team_id=?, exit_type=?, red_cards=?
        WHERE id=?""",
        (year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs,
         d_sh, d_sho, d_kp, d_drb, d_blk, d_pac,
         lname, tier, p.get("salary", 0),
         p.get("position", ""), tid, exit_type, rc_league, existing["id"]))

    conn.commit()
    conn.close()


def _lock_in_championship(team_id, year, matches_at_team, min_week=30):
    """팀을 떠나는 시점에, 그 팀이 그 리그 1위면 우승을 즉시 trophy_log에 확정.

    시즌 종료까지 기다리지 않고 '떠나는 순간' 기록하므로, 종료 시점 소속이
    달라져서 우승이 누락되는 일을 원천 차단한다.

    조건:
      - 그 팀에서 그 시즌 min(5경기) 이상 뛰었을 것 (잠깐 거쳐간 팀 제외)
      - 시즌이 충분히 진행됐을 것(현재 주차 >= min_week) — 시즌 초 반짝 1위 방지
      - 그 팀이 현재 리그 1위일 것
      - 같은 연도·팀·tier 중복 기록 방지
    """
    if not team_id or matches_at_team < 5:
        return
    st = get_state()
    if st and st.get("current_week", 0) < min_week:
        # 시즌 후반이 아니면 아직 우승 확정하지 않음 (시즌 종료 판정에 맡김)
        return
    rows = get_league_standings_by_team(team_id)
    if not rows or rows[0]["id"] != team_id or rows[0].get("pts", 0) <= 0:
        return   # 1위 아님

    conn = get_conn(); c = conn.cursor()
    info = c.execute("""SELECT t.name, l.name AS lname, l.tier AS tier
                        FROM teams t JOIN leagues l ON t.league_id=l.id
                        WHERE t.id=?""", (team_id,)).fetchone()
    if not info:
        conn.close(); return
    tier = info["tier"]
    # 중복 방지
    exist = c.execute("SELECT id FROM trophy_log WHERE year=? AND team_name=? AND tier=?",
                      (year, info["name"], tier)).fetchone()
    if exist:
        conn.close(); return
    if tier == 1:
        comp = f"{info['lname']} 우승 (1부 리그 챔피언)"
    else:
        comp = f"{info['lname']} 우승 ({tier}부 1위 → {tier-1}부 승격)"
    c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
              (year, info["name"], info["lname"], tier, comp))
    conn.commit(); conn.close()
    add_log(f"🏆 {year}년  {info['name']}  {info['lname']} 우승!", "event")


def finalize_season_for_retire():
    """은퇴 확정 직전 호출. 리그 경기가 끝난(36주+) 현재 시즌의 우승·개인수상을
    '시즌 종료 처리(_end_of_season)를 거치지 않고' trophy_log/awards에 즉시 확정한다.

    이유: _end_of_season 은 새해 진입(52→1주) 때만 돌기 때문에, 36주 이후 은퇴하면
    그 시즌 우승/수상이 누락된다. 은퇴 화면에 정상 반영되도록 여기서 미리 박는다.
    나이 증가·스탯 노화·통계 리셋 같은 시즌전환 부작용은 일으키지 않는다.
    """
    p = get_player()
    if not p:
        return
    st = get_state() or {}
    year = st.get("current_year", p.get("current_year", 0))
    tid  = p.get("current_team_id", 0)

    # 1) 리그 우승 확정 (그 팀에서 충분히 뛰고 1위면). 36주+이므로 min_week=30 충족.
    if tid:
        _lock_in_championship(tid, year, p.get("season_matches", 0), min_week=30)

    # 2) 개인 수상 확정 (리그 실제 풀시즌의 최소 35% 이상 출전 시 — 예전엔
    #    무조건 '10경기'로 고정돼 있었는데, 지금은 리그마다 풀시즌이
    #    14~58경기로 다 달라서 대형 리그에선 시즌 20%도 안 뛰고 자격이
    #    생기고 소형 리그에선 거의 다 뛰어야 하는 불균형이 있었다).
    #    시즌 통계는 아직 살아있다. 이미 이 연도에 내 수상이 기록돼
    #    있으면(중복 호출/시즌종료 후) 건너뛴다.
    _award_tid, _award_tid_matches = _primary_club_this_season(p)
    _min_matches_for_awards = max(6, round(0.35 * _league_full_season_matches(p, team_id=_award_tid)))
    _award_matches_total = p.get("award_matches", p.get("season_matches", 0))
    if _award_matches_total >= _min_matches_for_awards:
        _conn = get_conn()
        _dup = _conn.execute(
            "SELECT 1 FROM awards WHERE year=? AND is_mine=1 LIMIT 1", (year,)).fetchone()
        _conn.close()
        if not _dup:
            _rc = p.get("award_rating_cnt", p.get("season_rating_cnt", 0))
            _rs = p.get("award_rating_sum", p.get("season_rating_sum", 0.0))
            season_avg_rating = round(_rs / _rc, 2) if _rc else 6.0
            # [2026-08 버그수정] 클린시트도 '주 소속팀'(_primary_club_
            # this_season) 기준으로 — 시즌 중 이적했으면 지금 팀이 아니라
            # 실제로 더 많이 뛴 팀의 클린시트를 봐야 한다.
            _season_cs = _calc_clean_sheets_for_player(p, team_id=_award_tid, matches=_award_tid_matches)
            try:
                _process_awards(
                    p, year,
                    season_goals=p.get("award_goals", p.get("season_goals", 0)),
                    season_assists=p.get("award_assists", p.get("season_assists", 0)),
                    season_rating=season_avg_rating,
                    season_cs=_season_cs,
                    season_goals_against=p.get("award_goals_against", p.get("season_goals_against", 0)),
                )
            except Exception as e:
                print("finalize_season_for_retire 수상 오류:", e)


def _ensure_career_entry(p, st):
    """팀이 있는데 열린 커리어 항목(end_year=0)이 없으면 지금 생성."""
    global _pending_transfer_type
    tid = p.get("current_team_id", 0)
    if not tid: return

    conn = get_conn()
    c = conn.cursor()

    # 팀명 조회
    team_row = c.execute("""SELECT t.name, l.id as lid, l.name as lname, l.tier
                             FROM teams t JOIN leagues l ON t.league_id=l.id
                             WHERE t.id=?""", (tid,)).fetchone()
    if not team_row:
        conn.close(); return

    # 이미 열린 항목 있으면 스킵
    existing = _find_open_entry(c, tid, team_row["name"])
    if existing:
        conn.close(); return

    # [2026-07 방어적 수정, 신민용 리포트: "12-17~12-24처럼 1주짜리 중복
    # 행이 생긴다 — 스탯도 똑같은데 승무패만 이상하게 부풀어 있다"]
    # 정확한 트리거(왜 이 시점에 다시 호출되는지)는 실행 로그 없이
    # 100% 확정은 못 했지만, 증상은 뚜렷하다 — 같은 팀 항목이 "방금"
    # (지금 시점 기준 2주 이내) 닫혔는데 전입/이적 이벤트(_pending_
    # transfer_type) 없이 이 함수가 다시 불려서 새 행을 만드는 패턴이다.
    # 진짜 이적이었다면 _pending_transfer_type이 세팅돼 있어야 정상인데
    # 그게 없다는 것 자체가 "같은 재직의 연장"이라는 신호 — 그래서 새로
    # INSERT하는 대신 그 방금 닫힌 항목을 다시 열어(end_year/end_week를
    # 0으로 되돌림) 하나로 합친다. 반년 이상 지나 진짜로 그 팀에 복귀한
    # 경우는 "최근 2주" 조건에 안 걸려 정상적으로 새 스틴트로 취급된다.
    if not _pending_transfer_type:
        recent = c.execute("""SELECT id, end_year, end_week FROM career_entries
            WHERE team_id=? AND end_year>0
            ORDER BY end_year DESC, end_week DESC, id DESC LIMIT 1""",
            (tid,)).fetchone()
        if recent:
            _cur_y, _cur_w = st["current_year"], st["current_week"]
            _wk_gap = (_cur_y - recent["end_year"]) * 52 + (_cur_w - recent["end_week"])
            # [2026-07 버그수정, 신민용 리포트: "경력 기록이 연도별로 안 나뉘고
            # 하나로 계속 쌓인다"] 위 "방어적 수정"의 실제 동작을 재현해보니,
            # _close_career_entry가 연도 전환 때마다 정확히 '작년 52주차'에
            # 항목을 닫고, 그 직후(신년 1주차) 이 함수가 다시 불릴 때 gap이
            # 항상 정확히 1주로 계산돼서 — 진짜 이적이 아닌 '같은 팀 잔류'라면
            # 매년 100% 이 조건에 걸려 병합됐다. 즉 _close_career_entry의
            # 독스트링에 명시된 "연도별 분리" 목적 자체가 이 병합 로직 때문에
            # 매번 무효화되고 있었다 — 원래 노리던 건 "12-17~12-24"처럼 '같은
            # 해 안에서' 벌어지는 스퓨리어스 재호출이었는데, 조건이 연도
            # 경계까지 걸러내지 못했다. 닫힌 항목이 '작년'이 아니라 '올해'
            # 안에서 닫혔을 때만(recent["end_year"] == _cur_y) 병합하도록
            # 좁힌다 — 이러면 연도 전환 시점의 정상적인 새 행 생성은 더 이상
            # 병합되지 않고, 같은 해 안에서의 진짜 스퓨리어스 중복만 계속
            # 걸러진다.
            if recent["end_year"] == _cur_y and 0 <= _wk_gap <= 2:
                c.execute("UPDATE career_entries SET end_year=0, end_week=0 WHERE id=?",
                          (recent["id"],))
                conn.commit(); conn.close()
                return

    # 없으면 현재 주차 기준으로 생성
    #  - transfer_type/contract_years는 '이벤트(입단·이적·오퍼)가 발생한 그 해'에만
    #    표시되는 일회성 값이다. join_team이 _pending_transfer_type을 세팅한
    #    직후 첫 _ensure에서만 소비하고('' 로 리셋), 그 뒤 같은 팀에 머무는
    #    잔류 시즌 줄은 '재직중'(빈 값)으로 둔다.
    #    → 플래그가 '비어있지 않으면' 곧 방금 발생한 입단/이적/오퍼 이벤트.
    tt_e     = _pending_transfer_type
    if tt_e:
        # 입단/이적/오퍼 이벤트 줄 → 유형·연수 표시, 그리고 즉시 소비
        c_yrs_e = p.get("contract_years", 0)
        _pending_transfer_type = ""
    else:
        # 같은 팀 잔류 시즌 → 이벤트 아님 (연수/유형 비움 → UI에서 '—')
        c_yrs_e = 0
        tt_e    = ""
    tier_e   = p.get("current_tier") or team_row["tier"]
    role_e   = p.get("contract_role", "")
    mgr_e    = p.get("manager_type", "")
    amb_e    = p.get("club_ambition", "")

    # [버그수정 2026-07, 신민용 리포트: "오퍼는 이적료가 뜨는데 커리어엔
    # 안 뜬다"] 실제 새 커리어 행 INSERT는 _save_career_entry()가 아니라
    # 이 함수(_ensure_career_entry, 입단/이적 후 첫 4주 진행 시 호출)가
    # 담당하고 있었다 — _save_career_entry 쪽에만 이적료 계산을 넣어서
    # 실제로는 한 번도 실행되지 않는 코드였다. 여기서도 동일하게
    # "이적"/"오퍼"일 때만 계산하고, 그 외(입단/임대/연장)는 0.
    # [2026-07 재조정, 신민용+GPT 설계 확정: "팔림도 방출이 아니라 구단이
    # 판매한 것 — 이적과 같은 이적료 체계를 써야 한다. 방출/계약만료/입단만
    # 0원이 맞다"] "팔림"은 이적과 동일한 사건을 파는 쪽 관점에서 부르는
    # 이름일 뿐인데, 이 조건에서 빠져 있어서 팔림 이벤트엔 이적료가 항상
    # 0으로 저장되고 있었다 — "이적"/"오퍼"와 동일하게 취급한다.
    _transfer_fee_e = 0
    if tt_e in ("이적", "오퍼", "팔림"):
        _country_e = c.execute(
            "SELECT cn.name FROM leagues l JOIN countries cn ON l.country_id=cn.id WHERE l.id=?",
            (team_row["lid"],)).fetchone()
        _country_e = _country_e["name"] if _country_e else None
        _grade_e = get_league_grade(_country_e, "") if _country_e else None
        _contract_end_e = p.get("contract_end_year", 0)
        _cur_year_e = st["current_year"]
        _remain_e = (max(0, _contract_end_e - _cur_year_e)
                     if _contract_end_e else None)
        _transfer_fee_e = estimate_transfer_fee(
            _grade_e, tier_e, p.get("ovr", 0),
            country=_country_e, team_name=team_row["name"],
            position=get_field_pos(p), age=p.get("age"),
            talent_cap=p.get("talent_cap"),
            contract_remaining_years=_remain_e,
            year=_cur_year_e, team_id=tid,
        )

    c.execute("""INSERT INTO career_entries
        (age, position, team_name, league_name, tier, salary,
         start_year, start_week, end_year, end_week,
         matches, goals, assists, avg_rating, team_rank, wins, draws, losses,
         contract_years, transfer_type, team_id,
         contract_role, manager_type, club_ambition, transfer_fee)
        VALUES (?,?,?,?,?,?,?,?,0,0,0,0,0,0,0,0,0,0,?,?,?,?,?,?,?)""",
        (p["age"], get_field_pos(p), team_row["name"], team_row["lname"],
         tier_e, p.get("salary",0),
         st["current_year"], st["current_week"],
         c_yrs_e, tt_e, tid,
         role_e, mgr_e, amb_e, _transfer_fee_e))
    conn.commit()
    conn.close()


def advance_4weeks(schedule: list):
    """진행. schedule 길이만큼 '한 주씩 원자적으로' 전진시킨다.

    설계 핵심:
      - 매 주차를 처리한 직후 _advance_week(p, week, 1)로 정확히 1주 전진한다.
        → 13주(상반기 평점), 17주(국제대회), 52→1(시즌 종료) 같은 경계 트리거가
          4주를 한 번에 건너뛰어도 누락되지 않고 매주 정확히 검사된다.
      - 진행 도중 시즌이 끝나면(52주 → 1주) 그 시점에서 멈추고,
        schedule에 남은 '존재하지 않는 주차(53,54…)'는 건너뛴다.
        → 비시즌/시즌 경계가 겹쳐도 안전.
      - 주급은 매주 지급 → 모드와 무관하게 실제 진행된 매 주차마다 1회.

    schedule 1개면 '1주씩 보기', 4개면 '4주씩 보기'.
    """
    p = get_player()
    if not p: return

    st = get_state()
    if not schedule:
        return

    # 팀이 있는데 열린 커리어 항목이 없으면 지금 생성
    if p.get("current_team_id"):
        _ensure_career_entry(p, st)

    for (week, stype, detail) in schedule:
        # [최적화] get_player/get_state를 루프 상단에서만 1회 호출.
        #   _simulate_match/_process_training 등이 my_player를 바꾸는 경우에만
        #   해당 함수 내부에서 get_player를 재조회하므로, 여기서 매 주차 재조회는 불필요.
        p  = get_player()
        st = get_state()
        cur_week   = st["current_week"]
        cur_season = st["current_season"]

        # 안전장치: schedule이 가리키는 주차와 실제 현재 주차가 다르면
        # (시즌이 도중에 넘어가 53주 같은 유령 주차가 된 경우) 더 진행하지 않는다.
        if week != cur_week:
            break

        # ── 이번 주차 처리 ──
        # [최적화] 경기를 실제로 뛰었는지 추적 → career_stats 갱신을 경기 주차에만 실행
        _had_match = False
        if p.get("injured"):
            _process_injury_week(p, week)
            if stype == "경기" and not (isinstance(detail, dict) and detail.get("intl")):
                _sim_my_team_match_as_ai(week, p, cur_season)
            else:
                _sim_my_unscheduled_match(week, p, cur_season)
        elif stype == "경기":
            _had_match = True
            if isinstance(detail, dict) and detail.get("intl"):
                intl_engine.simulate_my_match(week, p)
            elif isinstance(detail, dict) and detail.get("cl"):
                _kind = detail.get("cl_kind", "champions")
                if _kind == "europa":
                    europa_engine.simulate_my_el_match(week, p)
                elif _kind == "conference":
                    conference_engine.simulate_my_ecl_match(week, p)
                elif _kind == "super_cup":
                    super_cup_engine.simulate_my_super_cup_match(week, p)
                else:
                    champions_engine.simulate_my_cl_match(week, p)
            else:
                _simulate_match(p, week, detail)
        else:
            im = intl_engine.get_my_match(week)
            cm = champions_engine.get_my_cl_match(week)
            elm = europa_engine.get_my_el_match(week)
            eclm = conference_engine.get_my_ecl_match(week)
            scm = super_cup_engine.get_my_super_cup_match(week)
            if im:
                _had_match = True
                intl_engine.simulate_my_match(week, p)
            elif cm:
                _had_match = True
                champions_engine.simulate_my_cl_match(week, p)
            elif elm:
                _had_match = True
                europa_engine.simulate_my_el_match(week, p)
            elif eclm:
                _had_match = True
                conference_engine.simulate_my_ecl_match(week, p)
            elif scm:
                _had_match = True
                super_cup_engine.simulate_my_super_cup_match(week, p)
            else:
                _process_training(p, week, stype, detail)
                _sim_my_unscheduled_match(week, p, cur_season)
        # 이 주차의 국제대회 + 챔스 + 다른 리그 AI 경기 처리
        # (intl/cl/ai 경기는 my_player의 year/season/team_id/salary를 바꾸지 않으므로
        #  루프 상단의 p 를 그대로 재사용한다. 불필요한 get_player() 재조회 제거.)
        intl_engine.process_intl_week(week)
        champions_engine.process_cl_week(week)
        europa_engine.process_el_week(week)
        conference_engine.process_ecl_week(week)
        club_world_cup_engine.process_cwc_week(week)
        super_cup_engine.process_super_cup_week(week)
        _sim_all_ai_matches(week, p.get("current_league_id", 0), cur_season)

        # ── 정확히 1주 전진 (경계 트리거 매주 검사) ──
        # [최적화] _simulate_match/_process_training이 season_matches 등을 갱신하므로
        #   salary/career 업데이트에는 최신 p가 필요 → 1회만 재조회.
        p_latest = get_player()

        # ── 주급: 매주 지급 ── [2026-07 수정, 신민용 지적: "축구는 주급으로
        #   준다"] 4주(=한 달)마다 salary//12를 주던 걸, 매주 salary//52로
        #   바꿨다. 자동저장 주기(한 달에 1회)는 그대로 유지하기 위해
        #   급여 지급과 분리한다.
        _pay_salary(p_latest, week)
        if week % 4 == 0:
            # [최적화] 인메모리 라이브 DB 자동저장. 기존과 동일하게 한 달에 1회.
            try:
                from database import flush_to_disk
                flush_to_disk()
            except Exception:
                pass

        _advance_week(p_latest, week, 1)

        # [국가대표 발탁 대기] 방금 INTL_CALLUP_WEEK 진입으로 국제대회(예선/본선)가
        #   생성되며 대표팀 선택 대기(my_selected=3)가 생겼다면, 발탁을 먼저 받아야
        #   하므로 더 진행하지 않고 이 주에서 멈춘다. (발탁 안 한 채 예선/본선 경기가
        #   진행돼버리는 것을 방지. center_panel이 다음 클릭 때 발탁창을 띄운다.)
        # [최적화] get_pending_choice()는 INTL_CALLUP_WEEK 진입 직후에만 의미 있으므로
        #   새 주차(new_week)가 그 값일 때만 DB 조회, 그 외엔 스킵.
        try:
            from constants import INTL_CALLUP_WEEK as _ICW
            if week == _ICW - 1 and intl_engine.get_pending_choice():
                break
        except Exception:
            pass

        # 진행 중 커리어 행 실시간 갱신 (경기가 있었던 주차에만)
        # [최적화] 훈련 주차(경기 없음)에는 스탯 변화 없으므로 갱신 스킵
        #   → 52주 중 경기 주차(~18회)에만 실행, 나머지 ~34회 DB 왕복 절약
        if _had_match and p_latest and p_latest.get("current_team_id"):
            p_fresh = get_player()   # _advance_week가 week/year를 바꿨으므로 1회만 재조회
            st_new  = get_state()
            _update_career_stats(p_fresh, st_new["current_year"], st_new["current_week"])
        # [최적화] 이 주차의 버퍼 로그 일괄 flush (개별 commit 수십 회 → 1회)
        flush_log_buffer()


def advance_days(schedule: list):
    """[2026-07 일 단위 진행] schedule 길이만큼 '하루씩 원자적으로' 전진시킨다.

    설계 핵심 — 검증된 advance_4weeks(주 단위 엔진)를 그대로 재사용:
      - schedule 항목은 (day, stype, detail). day는 연중 일자(1~364).
      - '그 주(week=day_to_week(day))의 마지막 날'(day % DAYS_PER_WEEK == 0)에
        도달했을 때만 기존 주 단위 훅(_sim_all_ai_matches, intl/CL 주간 처리,
        주급, _advance_week)을 정확히 1회 호출한다 — 매일 호출하면 이미
        처리된 주를 또 스캔하는 낭비만 생기고 정합성 이득은 없다
        (_sim_all_ai_matches는 home_score=-1인 것만 골라 처리하므로 멱등이지만,
        굳이 하루 7번씩 스캔할 이유가 없다).
      - 주 중간(경계일이 아닌 날)에는 current_day만 전진하고 current_week/
        current_year/current_season은 그대로 둔다 — _advance_week가 그 셋의
        유일한 갱신 지점이라는 불변식을 유지해서, 기존 수백 곳의 'current_week'
        참조 코드가 항상 일관된 값을 보게 한다.
      - 매일 정확히 하나의 행동(경기 또는 훈련/휴식)만 일어난다. 경기가 있는
        날엔 그날 하루는 훈련 선택이 무시되고 경기로 처리된다(실제 축구처럼
        경기 있는 날엔 훈련 대신 그 경기를 뛴다) — 그 외 요일엔 훈련/휴식만
        가능하다.

    schedule 1개면 '하루씩 보기', 7개면 '1주씩 보기'.
    """
    from constants import DAYS_PER_WEEK, day_to_week

    p = get_player()
    if not p:
        print("[ADVANCE] EXIT reason=no_player(진입시점)")
        return

    st = get_state()
    print(f"[ADVANCE] ENTER schedule_len={len(schedule)} first_day={schedule[0][0] if schedule else None} "
          f"current_day={st.get('current_day')} current_week={st.get('current_week')} "
          f"current_season={st.get('current_season')} current_year={st.get('current_year')}")
    if not schedule:
        print("[ADVANCE] EXIT reason=empty_schedule")
        return

    if p.get("current_team_id"):
        _ensure_career_entry(p, st)

    for (day, stype, detail) in schedule:
        p  = get_player()
        if not p:
            # [버그 수정] 최초 진입 시엔 p가 있었어도, 루프 도중(새 게임 리셋 등)
            # 플레이어가 사라질 수 있다 — 매 반복 재조회한 p를 여기서도 다시
            # None 체크 안 하고 바로 p.get(...)을 불러 크래시가 났었다
            # (AttributeError: 'NoneType' object has no attribute 'get').
            print(f"[ADVANCE] EXIT reason=no_player(루프중) requested_day={day}")
            break
        st = get_state()
        cur_day = st.get("current_day") or ((st["current_week"] - 1) * DAYS_PER_WEEK + 1)
        cur_season = st["current_season"]
        week = day_to_week(day)

        # 안전장치: schedule이 가리키는 날짜와 실제 현재 날짜가 다르면 멈춘다.
        if day != cur_day:
            print(f"[ADVANCE] EXIT reason=day_mismatch requested_day={day} cur_day={cur_day} "
                  f"week={week} current_week={st['current_week']} current_season={cur_season} "
                  f"current_year={st.get('current_year')}")
            break

        # [2026-08 계측 추가, 신민용 리포트: "진행 버튼 누를 때 멈칫하는 게
        # 늘었어 — 43~45주/52→1주 말고 중간중간도 봐야 할듯"] [PERF-WEEK]는
        # "그 주의 마지막 날"에만 찍혀서, 하루씩 진행할 때 매일 실제로 얼마나
        # 걸리는지는 지금까지 전혀 안 보였다(주 7일 중 6일은 로그 자체가
        # 없었음). 원인 확정 전이므로 로직은 그대로 두고, 하루 처리 전체를
        # 감싸는 타이머만 추가한다 — 0.1초 이상 걸린 날만 어떤 종류의 날
        # (경기/훈련, 부상 여부, 그 주 마지막 날 여부)이었는지와 함께 찍는다.
        import time as _time_day
        _day_t0 = _time_day.perf_counter()

        # ── 이번 날짜 처리 ──
        _had_match = False
        if p.get("injured"):
            _process_injury_week(p, week, day=day)
            # [2026-07 버그수정] 예전엔 부상 중일 때 국내리그만 AI로 대신
            # 처리되고, 컵대회/챔스/국제대회는 그날이 마침 그 경기 날이어도
            # 전혀 확인을 안 해서 그 경기가 영원히 미완료(home_score=-1)로
            # 남아 대회 전체가 멈춰버렸다(신민용 리포트: "10월인데 1월
            # 컵대회 경기가 계속 '예정'으로 떠있다"). 정상 진행(경기 있는
            # 날 분기)과 동일한 방식으로 오늘이 어떤 대회 경기 날인지 먼저
            # 확인하고, 그 대회에 맞는 AI-대체 시뮬레이션을 호출한다.
            if isinstance(detail, dict) and detail.get("intl"):
                intl_engine.sim_my_match_as_ai(week, p, reason="injury", day=day)
            elif isinstance(detail, dict) and detail.get("cl"):
                _kind = detail.get("cl_kind", "champions")
                if _kind == "europa":
                    europa_engine.sim_my_el_match_as_ai(week, p, reason="injury", day=day)
                elif _kind == "conference":
                    conference_engine.sim_my_ecl_match_as_ai(week, p, reason="injury", day=day)
                elif _kind == "super_cup":
                    super_cup_engine.sim_my_super_cup_match_as_ai(week, p, reason="injury", day=day)
                else:
                    champions_engine.sim_my_cl_match_as_ai(week, p, reason="injury", day=day)
            elif isinstance(detail, dict) and detail.get("cup"):
                cup_engine.sim_my_cup_match_as_ai(week, p, reason="injury", day=day)
            elif isinstance(detail, dict) and detail.get("cwc"):
                club_world_cup_engine.sim_my_cwc_match_as_ai(week, p, reason="injury", day=day)
            elif isinstance(detail, dict) and detail.get("po"):
                promotion_playoff_engine.sim_my_po_match_as_ai(week, p, reason="injury", day=day)
            elif stype == "경기":
                _sim_my_team_match_as_ai(week, p, cur_season)
            else:
                # [2026-07 재수정] intl_matches(예선/본선)와 cwc_matches는
                # 이제 둘 다 Phase 2로 실제 day가 있어 day로 직접 확인한다.
                # cl/cup_matches는 아직 day가 전부 0(스키마 기본값)이라
                # day만으로 걸러지지 않아 여전히 _week_intl_cl_day가 정한
                # '그 주의 딱 하루'에만 확인한다.
                _intl_cl_day = _week_intl_cl_day(week, p)
                if intl_engine.get_my_match(week, day=day, p=p):
                    intl_engine.sim_my_match_as_ai(week, p, reason="injury", day=day)
                elif day == _intl_cl_day and champions_engine.get_my_cl_match(week, day=day, p=p):
                    champions_engine.sim_my_cl_match_as_ai(week, p, reason="injury", day=day)
                elif day == _intl_cl_day and europa_engine.get_my_el_match(week, day=day, p=p):
                    europa_engine.sim_my_el_match_as_ai(week, p, reason="injury", day=day)
                elif day == _intl_cl_day and conference_engine.get_my_ecl_match(week, day=day, p=p):
                    conference_engine.sim_my_ecl_match_as_ai(week, p, reason="injury", day=day)
                elif super_cup_engine.get_my_super_cup_match(week, day=day, p=p):
                    super_cup_engine.sim_my_super_cup_match_as_ai(week, p, reason="injury", day=day)
                elif day == _intl_cl_day and cup_engine.get_my_cup_match(week, day=day, p=p):
                    cup_engine.sim_my_cup_match_as_ai(week, p, reason="injury", day=day)
                elif club_world_cup_engine.get_my_cwc_match(week, day=day, p=p):
                    club_world_cup_engine.sim_my_cwc_match_as_ai(week, p, reason="injury", day=day)
                elif promotion_playoff_engine.get_my_po_match(week, day=day, p=p):
                    promotion_playoff_engine.sim_my_po_match_as_ai(week, p, reason="injury", day=day)
                else:
                    _sim_my_unscheduled_match(week, p, cur_season, day=day)
        elif stype == "경기":
            _had_match = True
            if isinstance(detail, dict) and detail.get("intl"):
                intl_engine.simulate_my_match(week, p, day=day)
            elif isinstance(detail, dict) and detail.get("cl"):
                _kind = detail.get("cl_kind", "champions")
                if _kind == "europa":
                    europa_engine.simulate_my_el_match(week, p, day=day)
                elif _kind == "conference":
                    conference_engine.simulate_my_ecl_match(week, p, day=day)
                elif _kind == "super_cup":
                    super_cup_engine.simulate_my_super_cup_match(week, p, day=day)
                else:
                    champions_engine.simulate_my_cl_match(week, p, day=day)
            elif isinstance(detail, dict) and detail.get("cup"):
                cup_engine.simulate_my_cup_match(week, p, day=day)
            elif isinstance(detail, dict) and detail.get("cwc"):
                club_world_cup_engine.simulate_my_cwc_match(week, p, day=day)
            elif isinstance(detail, dict) and detail.get("po"):
                promotion_playoff_engine.simulate_my_po_match(week, p, day=day)
            else:
                _simulate_match(p, week, detail, day=day)
        else:
            # [2026-07 재수정] intl_matches(예선/본선)와 cwc_matches는 이제
            # 둘 다 Phase 2로 실제 day가 있어 day로 직접 확인한다. cl/cup_
            # matches는 아직 day가 전부 0(스키마 기본값)이라 day만으로
            # 걸러지지 않는다(그 주 아무 날에나 걸림 → 메인화면에 같은
            # 미완료 경기가 여러 날 반복 표시되는 원인이었다). day가 있는
            # intl/cwc만 day로 직접 확인하고, 아직 없는 챔스/유로파/컨퍼런스/
            # 컵은 예전처럼 _week_intl_cl_day가 정한 '그 주의 딱 하루'에만 확인한다.
            _intl_cl_day = _week_intl_cl_day(week, p)
            im = intl_engine.get_my_match(week, day=day, p=p)
            cm = champions_engine.get_my_cl_match(week, day=day, p=p) if day == _intl_cl_day else None
            elm = europa_engine.get_my_el_match(week, day=day, p=p) if day == _intl_cl_day else None
            eclm = conference_engine.get_my_ecl_match(week, day=day, p=p) if day == _intl_cl_day else None
            scm = super_cup_engine.get_my_super_cup_match(week, day=day, p=p)
            cu = cup_engine.get_my_cup_match(week, day=day, p=p) if day == _intl_cl_day else None
            cw = club_world_cup_engine.get_my_cwc_match(week, day=day, p=p)
            po = promotion_playoff_engine.get_my_po_match(week, day=day, p=p)
            if im:
                _had_match = True
                intl_engine.simulate_my_match(week, p, day=day)
            elif cm:
                _had_match = True
                champions_engine.simulate_my_cl_match(week, p, day=day)
            elif elm:
                _had_match = True
                europa_engine.simulate_my_el_match(week, p, day=day)
            elif eclm:
                _had_match = True
                conference_engine.simulate_my_ecl_match(week, p, day=day)
            elif scm:
                _had_match = True
                super_cup_engine.simulate_my_super_cup_match(week, p, day=day)
            elif cu:
                _had_match = True
                cup_engine.simulate_my_cup_match(week, p, day=day)
            elif cw:
                _had_match = True
                club_world_cup_engine.simulate_my_cwc_match(week, p, day=day)
            elif po:
                _had_match = True
                promotion_playoff_engine.simulate_my_po_match(week, p, day=day)
            else:
                _process_training(p, week, stype, detail, day=day)
                _sim_my_unscheduled_match(week, p, cur_season, day=day)

        _dispatch_total = _time_day.perf_counter() - _day_t0
        if _dispatch_total >= 0.1:
            print(f"[PERF-DISPATCH] {week}주차 {day}일차 경기/훈련 처리 {_dispatch_total:.3f}s "
                  f"(stype={stype!r})")

        is_week_last_day = (day % DAYS_PER_WEEK == 0)
        next_day = day + 1
        if next_day > 364:
            next_day = 1

        # [2026-07 신설, 신민용 리포트: "4강이랑 3/4위전이 안 뜬다"] 국제대회는
        # 이제 Phase 2로 실제 day 기반 일정이 있어서, 어느 요일에든 그
        # 단계(예: 8강)가 실제로 끝나면 곧바로 다음 단계(4강)가 생성돼야
        # 한다. 그런데 process_intl_week가 그 주 '마지막 날'에만 불리면,
        # 8강이 주 첫날(day330)에 끝나도 4강 생성은 그 주 끝(day336)까지
        # 미뤄진다 — 근데 4강 경기 자체는 그 사이 어느 날(day334)로 이미
        # 배정돼 있어서, 4강이 실제로 만들어지기도 전에 그 날짜가
        # 지나가버린다(화면엔 아무것도 안 뜨고, 나중에 뒤늦게 생겨도 이미
        # 지나간 day를 찾는 조회는 영원히 실패해 AI 처리로 샌다 — 실제
        # 세이브에서 재현·확인됨). 그래서 국제대회만 따로 떼어내 매일
        # 호출한다 — 내부적으로 이미 pending만 골라서 처리하는 멱등
        # 구조라 안 할 일이 있는 날엔 비용이 거의 없다.
        # [2026-08 계측 추가, 신민용 리포트: "43~45/52→1 말고 중간중간도
        # 잦은 멈칫이 있다" — [PERF-DAY]로 확인해보니 매주 마지막날이
        # 거의 항상 0.9초 이상, 44주(국제대회기간) 훈련일도 1.3~1.6초씩
        # 걸리는데 [PERF-WEEK]엔 안 잡혔다] 이 함수는 "매일" 불리는데도
        # 지금까지 개별 타이머가 없었다 — 원인 확정 전이므로 로직은 그대로
        # 두고 시간만 찍는다(0.05초 이상일 때만, 평소엔 조용하게).
        import time as _time_diw
        _diw_t0 = _time_diw.perf_counter()
        intl_engine.process_intl_week(week, day=day)
        _diw_t1 = _time_diw.perf_counter()

        # [2026-07 버그수정, 신민용 리포트: "하루씩 실행하면 나만 실행하고
        # 다른 날짜들은 멈춰 있어 다음주 올 때까지"] process_cwc_week는
        # 이미 process_intl_week와 똑같이 day 기반으로 "그날까지 온
        # 미완료 경기만" 멱등하게 처리하도록 설계돼 있었다(자기 docstring
        # 에도 그렇게 써있음) — 그런데 정작 호출부는 여태 week 마지막
        # 날에만 불렀다. 그래서 내 16강 경기는 그날 바로 처리되는데,
        # 같은 주 다른 날짜에 걸린 AI끼리의 16강 경기들은 그 주가 끝날
        # 때까지 전혀 시뮬되지 않고 그대로 멈춰 있었다(8강 대진도 그
        # 여파로 "미정"인 채 굳어있게 됨). intl_engine과 동일하게 매일
        # 부른다 — 처리할 게 없는 날엔 비용이 거의 없다.
        club_world_cup_engine.process_cwc_week(week, day=day)
        _diw_t2 = _time_diw.perf_counter()
        # [2026-08 신설, 10순위] 슈퍼컵도 클럽월드컵과 동일한 이유로 매일
        # 호출한다 — 준결승/결승이 한 주(day1/3/6) 안에서 요일 단위로
        # 갈리므로, 주 마지막 날에만 부르면 중간 요일에 이미 지난 경기가
        # 뒤늦게 처리되는 문제가 그대로 재현된다(process_cwc_week 위
        # 버그수정 주석과 동일한 이유).
        super_cup_engine.process_super_cup_week(week, day=day)
        if _diw_t2 - _diw_t0 >= 0.05:
            print(f"[PERF-DAILYHOOK] {week}주차 {day}일차: "
                  f"process_intl_week {_diw_t1-_diw_t0:.3f}s | "
                  f"process_cwc_week {_diw_t2-_diw_t1:.3f}s")

        # [2026-07 리팩터, 승강 플레이오프 도입 — 신민용 설계: "_end_of_season이
        # 역할을 너무 많이 갖고 있다, 시간축 기준으로 책임을 재배치하자"]
        # 원래 _finish_incomplete_matches_for_season + _process_promotion_
        # relegation은 _end_of_season() 안에 있어서 52주 종료(새해 진입)
        # 시점에야 실행됐다 — 그런데 승강 플레이오프는 44주(그 해 안)에
        # 열려야 하니, 그 결과를 만드는 이 두 함수는 클럽 시즌이 실제로
        # 끝나는 시점(CLUB_SEASON_END_DAY=300일, 43주)에 확정돼야 한다.
        # 두 함수를 통째로 그 시점에 맞춰 옮겼다(_finalize_club_season) —
        # 계산 결과 자체는 안 바뀐다(그 사이 클럽 경기가 없어서 언제
        # 계산해도 같은 숫자). _end_of_season에 남은 나머지(노화/행복도/
        # 시즌기록초기화/AI 생애주기/계약 등 "연도 전환" 성격의 일들)는
        # get_player()로 그때그때 최신 상태를 재조회하는 방식이라, 승강이
        # 이미 훨씬 전에 끝나있어도 아무 문제 없이 그대로 반영된다.
        from constants import CLUB_SEASON_END_DAY
        if day == CLUB_SEASON_END_DAY:
            # [2026-07 계측 추가, 신민용 리포트: "43주 44주 렉 걸리는거 같고"]
            # _finalize_club_season / start_promotion_playoffs는 여태 [PERF-WEEK]
            # 로그 범위 밖(day==CLUB_SEASON_END_DAY 전용 1회 실행)이라 계측이
            # 전혀 없었다 — 원인 확정 전이므로 로직은 그대로 두고 타이머만 추가.
            import time as _time_43
            _t43_0 = _time_43.perf_counter()
            _finalize_club_season(p, st["current_year"])
            _t43_1 = _time_43.perf_counter()
            promotion_playoff_engine.start_promotion_playoffs(st["current_year"])
            _t43_2 = _time_43.perf_counter()
            # [2026-08 신설, 신민용 리포트: "44→45주, 52→1주 렉이 심한데?"]
            # [PERF-INTLTAB] 로그에서 그룹순위계산이 연도전환 직후에만 가끔
            # 0.3~0.8s로 튀는 게 관찰됐다 — 52→1주 전환 끝에는 PRAGMA
            # optimize를 이미 한 번 부르지만, 국제대회 조편성/일정(intl_matches/
            # intl_entries)은 그 이후(43~44주 구간)에 새로 생성되므로 그
            # 시점의 SQLite 쿼리플래너 통계는 여전히 낡아있을 수 있다.
            # PRAGMA optimize는 부작용 없는(변경 감지 시에만 선별적으로
            # 갱신) 안전한 호출이라, 여기서도 한 번 더 실행해본다 — 원인
            # 확정 전 실험이므로 다음 로그로 효과를 확인해야 한다.
            try:
                get_conn().execute("PRAGMA optimize")
            except Exception:
                pass
            _t43_3 = _time_43.perf_counter()
            print(f"[PERF-YEAR] {week}주차(CLUB_SEASON_END_DAY) 세부: "
                  f"finalize_club_season={_t43_1-_t43_0:.3f}s | "
                  f"promotion_playoffs={_t43_2-_t43_1:.3f}s | "
                  f"PRAGMA optimize={_t43_3-_t43_2:.3f}s")

        # [2026-07 신설] 승강 PO도 intl/cwc와 동일한 day 기반 멱등
        # 실행기다 — 44주 동안 매일 불러 그날 온 경기를 처리한다.
        # [2026-08 계측 추가] 위 process_intl_week/process_cwc_week와
        # 동일한 이유로 개별 타이머 추가.
        _t_po0 = _time_diw.perf_counter()
        promotion_playoff_engine.process_po_week(week, day=day)
        _t_po1 = _time_diw.perf_counter()
        if _t_po1 - _t_po0 >= 0.05:
            print(f"[PERF-DAILYHOOK] {week}주차 {day}일차: process_po_week {_t_po1-_t_po0:.3f}s")


        _do_flush = False
        if is_week_last_day:
            # ── 그 주 마무리: 기존 검증된 주 단위 훅 그대로 재사용 ──
            # [2026-07 계측 추가, 신민용 리포트: "40→41, 49→50, 50→51주에
            # 렉이 심하다"] 어느 단계가 실제로 오래 걸리는지 특정하기 위해
            # 연도전환 로그와 같은 스타일로 주 단위 처리도 각 단계별 시간을
            # 콘솔에 찍는다. 0.3초 미만이면 로그를 생략해 평소엔 콘솔이
            # 조용하고, 실제로 느린 주에만 세부 내역이 보인다.
            import time as _time_mod
            _pw_t0 = _time_mod.perf_counter()
            # intl_engine.process_intl_week(week)는 위에서 이미 매일 호출함
            _pw_t1 = _time_mod.perf_counter()
            champions_engine.process_cl_week(week)
            europa_engine.process_el_week(week)
            conference_engine.process_ecl_week(week)
            _pw_t2 = _time_mod.perf_counter()
            # club_world_cup_engine.process_cwc_week(week, day=day)는
            # 위에서 이미 매일 호출함 (2026-07 버그수정, 위 주석 참고)
            _pw_t3 = _time_mod.perf_counter()
            cup_engine.process_cup_week(week)
            _pw_t4 = _time_mod.perf_counter()
            _sim_all_ai_matches(week, p.get("current_league_id", 0), cur_season)
            _pw_t5 = _time_mod.perf_counter()
            _pw_total = _pw_t5 - _pw_t0
            if _pw_total >= 0.3:
                print(f"[PERF-WEEK] {week}주차 마무리 {_pw_total:.2f}s "
                      f"(국제대회 {_pw_t1-_pw_t0:.2f}s | 챔스 {_pw_t2-_pw_t1:.2f}s | "
                      f"클럽WC {_pw_t3-_pw_t2:.2f}s | 국내컵 {_pw_t4-_pw_t3:.2f}s | "
                      f"리그시뮬 {_pw_t5-_pw_t4:.2f}s)")

            p_latest = get_player()
            # [2026-07 수정, 신민용 지적: "축구는 주급으로 준다"] 4주마다
            # salary//12를 주던 걸, 매주 salary//52로 바꿨다. 자동저장은
            # 이미 급여 지급과 별개 조건(week%4==2)이라 그대로 둔다.
            # [2026-08 계측 추가, 신민용 리포트: "18주,30주(경기 있는 주
            # 마지막날)가 1.15s씩 튀는데 [PERF-WEEK]도 안 찍혔다"] 이
            # 블록 안에서 유일하게 개별 타이머가 없던 두 곳 — 마지막
            # 사각지대라 여기서 확인한다.
            import time as _time_pw2
            _pw2_t0 = _time_pw2.perf_counter()
            _pay_salary(p_latest, week)
            _pw2_t1 = _time_pw2.perf_counter()
            # [최적화] 자동저장(flush_to_disk, DB 전체 백업)은 급여 지급과 같은 날
            # 묶여 있었는데, 52주차가 하필 4의 배수라 '연도 전환(_advance_week의
            # 최대 병목 지점)'과 '자동저장'이 같은 클릭에 겹쳐 있었다(실측 약 0.55초
            # 추가 지연). 저장 빈도(4주마다)·급여 지급 시점은 전혀 안 바꾸고,
            # 저장이 걸리는 주차만 급여 주차와 2주 어긋나게(2,6,10...50주차) 옮겨서
            # 52주차와 절대 겹치지 않게 한다 — 사용자가 체감하는 저장 주기는 동일.
            if week % 4 == 2:
                _do_flush = True

            _advance_week(p_latest, week, 1)   # current_week/year/season 갱신(검증된 로직)
            _pw2_t2 = _time_pw2.perf_counter()
            if _pw2_t2 - _pw2_t0 >= 0.1:
                print(f"[PERF-WEEKTAIL] {week}주차: _pay_salary {_pw2_t1-_pw2_t0:.3f}s | "
                      f"_advance_week {_pw2_t2-_pw2_t1:.3f}s")

        # current_day 전진 (주/연도 경계와 무관하게 매일 정확히 1회).
        # _advance_week는 current_week/year/season만 갱신하고 current_day는
        # 안 건드리므로, 여기서 항상 별도로 다음 날짜를 반영한다.
        conn_d = get_conn()
        conn_d.execute("UPDATE my_player SET current_day=? WHERE id=1", (next_day,))
        conn_d.execute("UPDATE season_state SET current_day=? WHERE id=1", (next_day,))
        # [2026-08 신설] get_state() 캐시에도 같은 값을 바로 반영 — 매일
        # 도는 경로라 무효화(다음 호출 시 재조회) 대신 직접 patch해서
        # DB 왕복 자체를 아예 안 만든다.
        if _state_cache is not None:
            _state_cache["current_day"] = next_day
        # [버그 수정] database.flush_to_disk()가 근본 원인은 정리했지만(백업
        # 직후 자체적으로 commit 시도), 혹시 다른 경로로도 같은 상태가 생길
        # 수 있으니 여기서도 한 번 더 방어한다 — "no transaction is active"는
        # 이미 저장이 끝났다는 뜻이라 그냥 무시해도 데이터 유실이 아니다.
        try:
            conn_d.commit()
        except sqlite3.OperationalError as _e:
            if "no transaction is active" not in str(_e):
                raise
        conn_d.close()

        # [버그수정] flush_to_disk()는 SQLite backup API(src_real.backup(dst))를
        #   쓰는데, 이게 커넥션의 트랜잭션 추적 상태를 흐트러뜨려서 그 '직후'
        #   같은 커넥션에 commit()을 하면 "cannot commit - no transaction is
        #   active" 오류가 났다. 그래서 flush는 이번 day 반복에서 필요한 모든
        #   커밋(위 current_day 커밋, _advance_week의 커밋)이 전부 끝난 뒤
        #   맨 마지막에, 이 반복에서 더 이상 같은 커넥션에 커밋할 게 없는
        #   시점에만 실행한다.
        # [2026-08 최적화, 신민용 리포트: "진행 버튼 누를 때 4주마다 한 번씩
        #   0.7~1초 멈춘다" — [PERF-DAY] 계측으로 자동저장 주(week%4==2)만
        #   유독 느린 게 확인됨] flush_to_disk()는 전체 DB를 디스크에
        #   backup하는 진짜 I/O라 동기로 부르면 그만큼 화면이 멈췄다 —
        #   이 백업은 게임 진행용 풀 커넥션과 완전히 분리된 별도 스냅샷
        #   커넥션만 건드리도록 이미 설계돼 있어서(위 주석 참고),
        #   백그라운드 스레드로 돌려도 안전하다. 저장 자체(내용·주기)는
        #   완전히 동일하고 "게임 진행을 막지 않고" 뒤에서 저장될 뿐이다.
        #   앱 종료 시(main_window.closeEvent)엔 여전히 동기 버전 +
        #   wait_for_pending_flush()로 저장 완료를 보장한다.
        if _do_flush:
            try:
                from database import flush_to_disk_async
                flush_to_disk_async()
            except Exception:
                pass

        # [국가대표 발탁 대기] INTL_CALLUP_WEEK 진입 직전 주의 마지막 날에 걸렸다면
        #   (=_advance_week가 방금 그 주로 진입시켰을 수 있음), 발탁 대기가
        #   생겼는지 확인 후 있으면 멈춘다.
        try:
            from constants import INTL_CALLUP_WEEK as _ICW2
            if is_week_last_day and week == _ICW2 - 1 and intl_engine.get_pending_choice():
                print(f"[ADVANCE] EXIT reason=intl_pending_choice day={day} week={week} "
                      f"pending={intl_engine.get_pending_choice()}")
                break
        except Exception:
            pass

        if _had_match and p.get("current_team_id"):
            p_fresh = get_player()
            st_new  = get_state()
            _update_career_stats(p_fresh, st_new["current_year"], st_new["current_week"])
        flush_log_buffer()

        _day_total = _time_day.perf_counter() - _day_t0
        if _day_total >= 0.1:
            print(f"[PERF-DAY] {week}주차 {day}일차 처리 {_day_total:.3f}s "
                  f"(stype={stype!r} | 그주마지막날={is_week_last_day} | "
                  f"경기있음={_had_match} | 부상={bool(p.get('injured'))})")


# ── [개선] 홈 어드밴티지 변동폭 + 포메이션 스타일 보정 ──────────
#   기존: 모든 경기에 예외 없이 고정 +3 → 팀/경기 상관없이 완전히 똑같은 값.
#   개선: 매 경기 1.5~4.5 사이로 살짝 흔들리게(평균은 기존과 동일한 3.0 부근)
#         해서 "어떤 날은 홈 응원이 유독 잘 먹힌다" 정도의 자연스러운 변동 부여.
_team_formation_cache: dict = {}

def _home_advantage():
    return random.uniform(1.5, 4.5)


def _team_formation(c, team_id):
    """팀의 현재 포메이션 조회 (세션 캐시, PK 조회라 원래도 저렴하지만 캐시로 0비용화).
    [주의] 내 팀 포메이션은 경기 중 formation_widget에서 바뀔 수 있으므로
    _invalidate_team_ovr_cache()가 호출될 때 이 캐시도 함께 비운다."""
    cached = _team_formation_cache.get(team_id)
    if cached is not None:
        return cached
    c.execute("SELECT formation FROM teams WHERE id=?", (team_id,))
    row = c.fetchone()
    val = (row["formation"] if row else None) or "4-4-2"
    _team_formation_cache[team_id] = val
    return val


def _formation_bias(c, team_id):
    """포메이션 스타일에 따른 소폭 전력 보정치 (FORMATION_STYLE 참조, ±1.5 이내)."""
    return FORMATION_STYLE.get(_team_formation(c, team_id), 0.0)


def _match_win_probs(diff):
    """[공용] 전력차(diff=home_ovr-away_ovr, 홈보정 포함)로 승/무/패 확률 산출.
    _simulate_match(내 경기)의 개선판 공식과 통일:
      - hw 상한 0.94 (구식 배경 AI 경기 공식은 0.80 캡 → 압도해도 못 이기는 비논리)
      - dw는 전력차에 반비례 (구식 공식은 dw=0.25 고정 → 전력차 무관 항상 25% 무승부)

    [2026-07 재조정, 신민용 지적: "상대적 약팀이 강팀을 이기는 확률이 현실보다
    높다"] diff 1점당 반영폭을 0.012→0.020으로, dw 감소폭도 0.006→0.010으로
    올렸다. diff=0(균형)일 때는 기존과 완전히 동일(hw=.45/dw=.28)하게 유지해
    호각세 매치는 그대로 두고, 격차가 벌어질수록(diff≈20 안팎부터) 확실한
    강팀 우세가 되도록 기울기만 가파르게 했다.
      diff  0 → hw 45% (기존과 동일)
      diff 10 → hw 65% (기존 57%)
      diff 20 → hw 85% (기존 69%)
      diff 25+ → hw 94% 캡 근접 (기존은 diff 39 근처에서야 캡)
    """
    hw = max(0.04, min(0.94, 0.45 + diff * 0.020))
    dw = max(0.05, 0.28 - abs(diff) * 0.010)
    aw = max(0.02, 1.0 - hw - dw)
    tot = hw + dw + aw
    return hw / tot, dw / tot, aw / tot


def _roll_outcome(diff):
    """diff 기반 승/무/패 확률로 outcome 문자열을 뽑는다."""
    hw, dw, aw = _match_win_probs(diff)
    roll = random.random()
    if roll < hw:         return "home"
    elif roll < hw + dw:  return "draw"
    else:                 return "away"


def _sim_my_team_match_as_ai(week, p, season):
    """부상/결장 시 내 팀 경기를 AI끼리 시뮬레이션해서 팀 전적에 반영.

    [2026-07 버그수정, 신민용 리포트: "부상으로 경기 못 나갔는데 감독관계가
    그대로다"] _calc_manager_rel()의 '결장(played=False)이면 관계 -1'
    로직은 정식 엔진(_simulate_match)에서만 호출됐다 — 근데 부상 중엔
    이 AI-대체 함수가 대신 도는데, 여긴 팀 전적만 반영하고 감독관계는
    아예 안 건드리고 있었다. 결장 자체는 정식 엔진 경로든 이 경로든
    똑같이 '내가 못 뛴 경기'이므로 동일하게 관계를 깎는다."""
    my_tid = p.get("current_team_id", 0)
    if not my_tid:
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT mr.id, mr.home_team_id, mr.away_team_id
                 FROM match_results mr
                 WHERE mr.week=? AND mr.season=? AND mr.home_score=-1
                   AND (mr.home_team_id=? OR mr.away_team_id=?)
                 LIMIT 1""", (week, season, my_tid, my_tid))
    m = c.fetchone()
    if m:
        hid, aid = m["home_team_id"], m["away_team_id"]
        ho = _team_avg_ovr(c, hid) + _home_advantage() + _formation_bias(c, hid)
        ao = _team_avg_ovr(c, aid) + _formation_bias(c, aid)
        diff = ho - ao
        outcome = _roll_outcome(diff)
        # [버그수정] diff를 _gen_score에 전달 — 이전엔 인자 누락으로 항상 박빙 취급됐음
        hs, as_ = _gen_score(outcome, diff)
        _td = {}; _accum_team_rec(_td, hid, aid, outcome, hs, as_); _flush_team_rec(c, _td)
        c.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                  (hs, as_, m["id"]))
        conn.commit()
        update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False))
    conn.close()


def _sim_my_unscheduled_match(week: int, p, season: int, day=None):
    """훈련 주차지만 실제 DB에 내 팀 경기가 있는 경우 AI로 처리.

    [2026-07 버그수정, 신민용 리포트: "하루씩 진행 중인데 아직 안 온
    일요일 경기가 화요일에 이미 결과가 나 있다"] 이 함수는 원래 '주
    단위' 레거시 엔진(advance_4weeks)용으로 짠 거라 week만 봤다. 근데
    일 단위 엔진(advance_days)이 재사용하면서 week만 필터링하는 건
    그대로 남아있었다 — 한 '주차'(week 번호) 안에 경기가 2개 잡히는
    리그(예: 월요일 홈 + 일요일 원정, 둘 다 week=27)에서는, 월요일
    경기를 마친 뒤 화요일(휴식일)에 이 함수가 돌면 "이번 주 아직 안 끝난
    경기"로 일요일 경기를 집어서 5일이나 일찍 간이 AI 결과로 확정해
    버렸다 — 그리고 실제 일요일이 되면 정식 엔진이 같은 행을 또
    시뮬레이션해서 다른 결과로 덮어써, 무승부가 패배로 바뀌는 것처럼
    보였다. day가 주어지면(일 단위 엔진) 정확히 '오늘' 날짜의 경기만
    대상으로 삼는다 — week만으로는 그 주의 다른 날짜(아직 안 온) 경기까지
    걸릴 수 있어서다. day가 없으면(레거시 주 단위 엔진) 기존처럼 week
    전체를 본다."""
    tid = p.get("current_team_id", 0)
    if not tid: return
    lid = p.get("current_league_id", 0)
    if not lid: return
    conn = get_conn()
    if day is not None:
        row = conn.execute(
            """SELECT id, home_team_id, away_team_id FROM match_results
               WHERE league_id=? AND season=? AND week=? AND day=? AND home_score=-1
               AND (home_team_id=? OR away_team_id=?)""",
            (lid, season, week, day, tid, tid)).fetchone()
    else:
        row = conn.execute(
            """SELECT id, home_team_id, away_team_id FROM match_results
               WHERE league_id=? AND season=? AND week=? AND home_score=-1
               AND (home_team_id=? OR away_team_id=?)""",
            (lid, season, week, tid, tid)).fetchone()
    if row:
        c = conn.cursor()
        ho = _team_avg_ovr_with_me(c, row["home_team_id"], p) + _home_advantage() + _formation_bias(c, row["home_team_id"])
        ao = _team_avg_ovr_with_me(c, row["away_team_id"], p) + _formation_bias(c, row["away_team_id"])
        diff = ho - ao
        outcome = _roll_outcome(diff)
        # [버그수정] diff를 _gen_score에 전달
        hs, as_ = _gen_score(outcome, diff)
        _td = {}; _accum_team_rec(_td, row["home_team_id"], row["away_team_id"], outcome, hs, as_); _flush_team_rec(c, _td)
        conn.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                     (hs, as_, row["id"]))
        conn.commit()
    conn.close()


def _sim_all_ai_matches(week, my_league_id, season):
    """모든 리그 이번 주차 미완료 경기 AI 처리 (내 팀 경기 제외)
    [최적화] match_results UPDATE를 executemany 배치로.
    [최적화] teams 전적 UPDATE도 _accum_team_rec + _flush_team_rec 배치로."""
    conn = get_conn()
    c = conn.cursor()

    # [2026-08 계측 추가, 신민용 리포트: "39주차 리그시뮬이 1.29s 튀었다"]
    # _team_avg_ovr/_formation_bias는 이미 세션 캐시가 있는데도 튄 걸 보면
    # (1) 그 주 처리할 경기 수 자체가 유난히 많았거나 (2) 캐시가 그 시점에
    # 비어있었을 가능성이 있다 — 원인 확정 전이므로 로직은 그대로 두고
    # 구간별 시간 + 처리한 경기 수만 찍는다.
    import time as _time_sim
    _sim_t0 = _time_sim.perf_counter()

    p_row = conn.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
    my_tid = p_row["current_team_id"] if p_row else 0
    _my_p = get_player() if my_tid else None   # 오프시즌 예외 케이스에서만 실제로 씀

    c.execute("""SELECT mr.id, mr.home_team_id, mr.away_team_id, mr.league_id
                 FROM match_results mr
                 WHERE mr.week=? AND mr.home_score=-1 AND mr.season=?
                 ORDER BY mr.id""",
              (week, season))
    matches = c.fetchall()
    _sim_t1 = _time_sim.perf_counter()

    from constants import SEASON_PHASES
    _ps_s, _ps_e = SEASON_PHASES["preseason1"]
    _os_s, _os_e = SEASON_PHASES["postseason"]   # = 국제대회 전용 비시즌
    is_offseason = (_ps_s <= week <= _ps_e) or (_os_s <= week <= _os_e)

    _sim_ovr_cache_hits_before = len(_team_ovr_cache)

    batch_results = []   # (hs, as_, mid) — match_results 배치
    team_deltas   = {}   # {team_id: [w,d,l,gf,ga]} — teams 배치
    for m in matches:
        is_my_match = (m["home_team_id"] == my_tid or m["away_team_id"] == my_tid)
        if is_my_match and not is_offseason:
            continue
        if is_my_match:
            # [2026-07 신설] 비시즌 예외로 내 팀 경기가 이 일괄처리에 걸리는
            # 경우, 내가 부상/정지가 아니면 merit-based로 나를 반영한다.
            ho = _team_avg_ovr_with_me(c, m["home_team_id"], _my_p) + _home_advantage() + _formation_bias(c, m["home_team_id"])
            ao = _team_avg_ovr_with_me(c, m["away_team_id"], _my_p) + _formation_bias(c, m["away_team_id"])
        else:
            ho = _team_avg_ovr(c, m["home_team_id"]) + _home_advantage() + _formation_bias(c, m["home_team_id"])
            ao = _team_avg_ovr(c, m["away_team_id"]) + _formation_bias(c, m["away_team_id"])
        diff = ho - ao
        outcome = _roll_outcome(diff)
        # [버그수정] diff를 _gen_score에 전달 — 전체 리그 경기의 90%+가 여길 거침
        hs, as_ = _gen_score(outcome, diff)
        _accum_team_rec(team_deltas, m["home_team_id"], m["away_team_id"], outcome, hs, as_)
        batch_results.append((hs, as_, m["id"]))
    _sim_t2 = _time_sim.perf_counter()

    if batch_results:
        c.executemany("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                      batch_results)
    _sim_t3 = _time_sim.perf_counter()
    _flush_team_rec(c, team_deltas)
    conn.commit()
    conn.close()
    _sim_t4 = _time_sim.perf_counter()
    _sim_total = _sim_t4 - _sim_t0
    if _sim_total >= 0.1:
        print(f"[PERF-SIM]  _sim_all_ai_matches({week}주차, {len(matches)}경기, "
              f"OVR캐시 {_sim_ovr_cache_hits_before}→{len(_team_ovr_cache)}) 세부: "
              f"경기조회 {_sim_t1-_sim_t0:.3f}s | 시뮬루프 {_sim_t2-_sim_t1:.3f}s | "
              f"executemany({len(batch_results)}건) {_sim_t3-_sim_t2:.3f}s | "
              f"teams반영+commit {_sim_t4-_sim_t3:.3f}s")


# ─────────────────────────────────────────
# 훈련
# ─────────────────────────────────────────

def effective_training_stress(p, ttype):
    """선수의 성격/신체특징 stress_mult 를 반영한 '실제 적용' 스트레스 변화량.

    TRAINING_CONFIG[ttype]['stress'] 는 기본값이고, 실제로는 냉철함/훈련광(성격),
    지구력형/강철체질(신체특징) 의 stress_mult 가 곱해진다. 메인 화면 미리보기가
    이 함수를 사용하면 표시값과 실제 적용값이 항상 일치한다.
    (계산식은 _process_training 의 stress_chg 산출과 반드시 동일하게 유지할 것.)
    """
    cfg = TRAINING_CONFIG.get(ttype)
    if not cfg:
        return 0
    from constants import PHYSICAL_TRAIT_EFFECTS as _PTE
    pe       = PERSONALITY_EFFECTS.get(p.get("personality", "성실함"), {})
    trait_fx = _PTE.get(p.get("physical_trait", "무난함"), {})
    stress_chg = cfg["stress"]
    if "stress_mult" in pe:
        stress_chg = int(stress_chg * pe["stress_mult"])
    if "stress_mult" in trait_fx:
        stress_chg = int(stress_chg * trait_fx["stress_mult"])
    return stress_chg


def _get_stat_start_map(p):
    """[2026-08 신설] my_player.stat_start(JSON 문자열)를 dict로 파싱.
    없거나 깨졌으면(구버전 세이브 등) 빈 dict를 반환 — 호출부가
    _ensure_stat_start로 채워 넣는다."""
    raw = p.get("stat_start") or ""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _ensure_stat_start(p, stat_list):
    """[2026-08 신설, 신민용 확정] stat_start에 아직 없는 스탯이 있으면
    (이 기능 추가 이전 세이브 등) 현재값을 그 스탯의 '시작값'으로
    1회 채워 넣는다 — 한 번 채워지면 그 값은 커리어 내내 절대 안 바뀐다.
    새로 만든 선수는 create_player에서 이미 전부 채워져 있어 여기선
    보통 아무 일도 안 한다(구버전 세이브 마이그레이션 안전장치)."""
    starts = _get_stat_start_map(p)
    changed = False
    for s in stat_list:
        if s not in starts:
            starts[s] = p.get(s, 40)
            changed = True
    if changed:
        p["stat_start"] = json.dumps(starts)
        update_player(stat_start=p["stat_start"])
    return starts


def _progress_soft(cur, start, mx):
    """[2026-08 신설, 신민용 확정] 훈련 gain(및 휴식 감소폭)을 '한계까지
    남은 절대 포인트'가 아니라 '시작값→한계값 전체 구간 중 진행률(%)'로
    감속하는 배율(0~1). 진행률이 PROGRESS_SOFTCAP_BREAK_PCT를 넘기 전까진
    풀스피드(1.0), 그 이후로는 지수 커브로 PROGRESS_SOFTCAP_FLOOR까지
    떨어진다(막판일수록 급격히 느려짐). FLOOR가 있어 한계 바로 앞에서도
    진행이 완전히 0이 되진 않는다 — '진짜' 한계 돌파(_max 자체를 올리는
    것)는 이 함수와 무관하게 HIGH_BREAK_PROB/FOCUS_BREAK_PROB가 cur==mx
    시점부터 별도로 담당한다.
    고강도/중강도/저강도/강점훈련/약점훈련의 gain 감속과 휴식의 감소폭
    감속까지 전부 이 함수 하나로 통일해서 쓴다."""
    total = mx - start
    if total <= 0:
        return 1.0
    progress = max(0.0, min(1.0, (cur - start) / total))
    if progress <= PROGRESS_SOFTCAP_BREAK_PCT:
        return 1.0
    t = (progress - PROGRESS_SOFTCAP_BREAK_PCT) / (1.0 - PROGRESS_SOFTCAP_BREAK_PCT)
    return 1.0 - (1.0 - PROGRESS_SOFTCAP_FLOOR) * (t ** PROGRESS_SOFTCAP_POWER)


def _process_training(p, week, ttype, focus_stat=None, day=None):
    cfg  = TRAINING_CONFIG[ttype]
    # [2026-08 신설] 진행률(%) 감속 커브의 기준선 — 이번 훈련에서 건드릴
    # 스탯뿐 아니라 ALL_STATS 전체를 한 번에 보장해둔다(휴식이 고르는
    # 스탯은 targets와 무관하므로 미리 다 채워둬야 함).
    _stat_starts = _ensure_stat_start(p, ALL_STATS)
    pers = p.get("personality","성실함")
    pe   = PERSONALITY_EFFECTS.get(pers, {})
    eff  = pe.get("train_eff", 1.0)

    # [신체 특징] 효과 로드 (성장 배수/스트레스/체력훈련 보너스 등)
    from constants import PHYSICAL_TRAIT_EFFECTS as _PTE
    trait    = p.get("physical_trait", "무난함")
    trait_fx = _PTE.get(trait, {})

    # 나이별 효율 배수
    age      = p.get("age", 20)
    peak_age = p.get("peak_age", 25)
    eff *= _age_train_eff(age, peak_age)

    # 슬럼프 패널티
    if p.get("slump"):
        eff *= SLUMP_TRAIN_PENALTY

    stress_chg = effective_training_stress(p, ttype)

    happy_chg = 0
    stat_changes = {}

    if ttype == "휴식":
        # [2026-07 버그수정, 신민용 리포트: "고강도/휴식(또는 중강도/휴식)을
        # 반복하면 부상 없이 OVR 한계치까지 올라간다"] 기존엔 성장기(피크
        # 나이 이전)엔 휴식으로 스탯이 전혀 안 깎였다("한창 크는 선수가
        # 일주일 쉰다고 퇴보하지 않는다"는 의도) — 그런데 그 결과 고강도
        # (스트레스+16)/휴식(스트레스-20, 스탯 무손실)을 반복하면 스탯
        # 손실 없이 스트레스만 계속 순감소해서, 부상 위험 게이트(스트레스
        # 100 과부하)가 평생 한 번도 안 걸리고 고강도 훈련을 무한 반복할
        # 수 있었다.
        # [신민용 확정] 휴식 시 스탯을 저강도 훈련 상승폭(TRAINING_CONFIG
        # ["저강도"])과 같은 크기로 깎는다 — 성장기 여부와 무관하게 항상
        # 적용(이게 이번 수정의 핵심: 예전엔 성장기엔 아예 면제였음). 이러면
        # "중강도·휴식·중강도·휴식·중강도·휴식·휴식" 같은 일정을 짜도 상승분
        # (중강도 3회)과 하락분(휴식 4회, 저강도급)이 서로 상쇄돼 스탯이
        # ±0 근처 또는 소폭 상승에 그친다 — 고강도/휴식 반복으로 무한정
        # 한계치를 찍는 건 막히고, 실제로 쉬어야 할 이유가 생긴다.
        _phy_pool  = [s for s in PHYSICAL_STATS
                      if s in FOCUS_TRAIN_STATS.get(p["position"], PHYSICAL_STATS)]
        _tech_pool = [s for s in TECHNICAL_STATS
                      if s in FOCUS_TRAIN_STATS.get(p["position"], TECHNICAL_STATS)]
        _rest_pool = (_phy_pool or PHYSICAL_STATS) + (_tech_pool or TECHNICAL_STATS)
        _rest_below = [s for s in _rest_pool if p.get(s, 40) > 20]  # 20 밑으로는 안 깎음
        _lo_cfg = TRAINING_CONFIG["저강도"]
        # [2026-07 재조정, 신민용 확정: "일단 이렇게 해서"] 일 단위(하루 1결정,
        # 주 7일 중 경기 1일 소모) 기준으로 다시 시뮬레이션해보니, 저강도
        # 100% 그대로 적용 시 월드클래스가 피크 나이(23~25세)에도 정상
        # 도달치보다 한참 못 미치는 부작용이 있었다(경기 스트레스가 고강도
        # 만큼 세서 휴식을 자주 강제당하는데, 그 휴식마다 100% 페널티가
        # 겹쳐 과도하게 깎임). 저강도의 50% 크기로 낮춰 — 고강도/휴식 무한
        # 반복 익스플로잇은 계속 막히면서, 정상적인 시즌 진행(경기+훈련
        # 혼합)에서는 피크 나이대에 무리 없이 도달할 수 있게 한다.
        _REST_PENALTY_MULT = 0.5
        _n_dec = random.choices([1, 2], weights=[60, 40])[0]
        for stat in random.sample(_rest_below, min(_n_dec, len(_rest_below))):
            cur = p.get(stat, 40)
            # [2026-08 신설, 신민용 확정] 휴식 감소폭도 훈련 gain과 같은
            # 진행률(%) 커브로 스케일링한다 — 한계에 가까운(원숙한) 스탯일수록
            # 휴식으로 덜 깎이고, 아직 한계와 먼(성장기) 스탯은 원래 크기
            # 그대로 깎인다.
            _mx = p.get(f"{stat}_max", 80)
            _start = _stat_starts.get(stat, p.get(stat, 40))
            _rest_soft = _progress_soft(cur, _start, _mx)
            dec = round(random.uniform(_lo_cfg["gain_min"], _lo_cfg["gain_max"])
                        * _REST_PENALTY_MULT * _rest_soft, 1)
            new_val = round(max(20, cur - dec), 1)
            if new_val != cur:
                stat_changes[stat] = round(new_val - cur, 1)
        happy_chg = random.randint(4, 8)
        # [2026-07 신설] '긍정적' 성격의 happy_gain_mult 연결 (정의만 돼있고
        # 실제 행복도 계산엔 미연결 상태였음) — 상승분에만 배율 적용.
        if "happy_gain_mult" in pe:
            happy_chg = round(happy_chg * pe["happy_gain_mult"])
        log_parts = [f"😴 휴식  {_day_label(week, day)}  스트레스 {stress_chg:+d}  행복 {happy_chg:+d}"]
        if stat_changes:
            for s, v in stat_changes.items():
                log_parts.append(f"   {STAT_KO.get(s,s)} {v:+.1f}")

    else:
        # 부상 체크
        inj_chance = cfg["injury_chance"]
        # [신체 특징] 부상 관련 보정은 성격이 아니라 신체 특징에서 읽는다.
        from constants import PHYSICAL_TRAIT_EFFECTS
        trait = p.get("physical_trait", "무난함")
        trait_fx = PHYSICAL_TRAIT_EFFECTS.get(trait, {})
        inj_add = trait_fx.get("injury_add", 0)
        immune = inj_add <= -1.0   # 강철체질: 완전 면역

        if immune:
            inj_chance = 0.0
        elif inj_chance > 0:
            # 원래 부상 위험이 있는 훈련(고강도): 특징 보정 그대로 가산
            inj_chance = max(0, inj_chance + inj_add)
        elif inj_add > 0:
            # [부상체질] 평소 안전한 훈련(중강도/집중훈련)에서도 '저 확률'로 부상.
            #   휴식·저강도는 제외. injury_add(예 0.10)의 1/3만 적용(약 3%).
            if ttype in ("중강도", "집중훈련"):
                inj_chance = inj_add / 3.0
            else:
                inj_chance = 0.0

        # 과부하(스트레스 100)면 부상 확률 급증. 단 '부상 완전 면역'(강철체질)은 예외.
        if p.get("stress", 0) >= 100 and not immune:
            inj_chance = 1.0

        if random.random() < inj_chance:
            _apply_injury(p, week, day=day)
            return

        # ── 훈련 스탯 상승 ──────────────────────────────────
        # 집중훈련: 지정 스탯 1개
        focus_mode = cfg.get("focus_mode")
        # [2026-07 신설] '완벽주의' 성격의 high_train_bonus/low_train_penalty
        # 연결 — 정의만 돼있고 실제 훈련엔 미연결 상태였음. 이미 있는 강점/
        # 약점훈련 구분(focus_mode)에 자연스럽게 대응시킨다: 이미 잘하는
        # 강점을 훈련할 땐 완벽주의 기질이 보너스로, 부족한 약점을 훈련할
        # 땐 페널티로 작용한다.
        if focus_mode == "strong" and "high_train_bonus" in pe:
            eff *= pe["high_train_bonus"]
        elif focus_mode == "weak" and "low_train_penalty" in pe:
            eff *= pe["low_train_penalty"]
        if focus_mode in ("strong", "weak"):
            # [강점/약점 훈련] 스탯을 자동 선별해 상위 2~3개를 함께 키운다.
            #   판정 기준 = '현재 수치'
            #     - strong: 현재 높은 순 → 지금 잘하는 능력치를 더 극대화
            #     - weak:   현재 낮은 순 → 지금 부족한 능력치를 메움
            #   (한계치가 아니라 현재치 기준이라, '한계는 높은데 아직 안 찬'
            #    태클 45/78 같은 스탯도 약점으로 제대로 잡힌다.)
            #   포지션 가중치 0(무관)인 스탯은 대상에서 제외(GK의 슈팅 등).
            from database import WEIGHTS as _FW
            _posw = _FW.get(p["position"], {})
            _cap_f = p.get("talent_cap", 88)
            _cand = [s for s in ALL_STATS if _posw.get(s, 0) > 0]
            if not _cand:
                _cand = list(ALL_STATS)

            def _score(s):
                # 강점/약점 판정은 '현재 수치' 기준.
                return p.get(s, 40)

            if focus_mode == "strong":
                # 강점: 현재 높은 순으로 강점군 확정 → 그 안에서 집중.
                ranked = sorted(_cand, key=_score, reverse=True)
            else:
                # 약점: 현재 낮은 순으로 약점군 확정 → 그 안에서 집중.
                ranked = sorted(_cand, key=_score)
            half = ranked[:max(3, len(ranked) // 2)]   # 상위/하위 절반(최소3)이 대상군
            # 더 올릴 여지 있는(아직 안 찬) 스탯을 우선, 없으면 그대로 둬서
            # max 돌파 로직이 받게 한다.
            if focus_mode == "strong":
                room = [s for s in half if p.get(s, 40) < _cap_f]
            else:
                room = [s for s in half if p.get(s, 40) < p.get(f"{s}_max", 80)]
            ordered = room + [s for s in half if s not in room]
            cnt = random.choices([2, 3], weights=[55, 45])[0]
            targets = ordered[:cnt]
            _slow_targets = set()
        elif ttype == "집중훈련" and focus_stat:
            # (구버전 호환) 단일 스탯 집중
            targets = [focus_stat]
            _slow_targets = set()
        else:
            pos = p["position"]
            # 포지션별 신체/기술 pool 분리
            focus = FOCUS_TRAIN_STATS.get(pos, PHYSICAL_STATS + TECHNICAL_STATS)
            phy_pool  = [s for s in PHYSICAL_STATS  if s in focus]
            tech_pool = [s for s in TECHNICAL_STATS if s in focus]
            if not phy_pool:  phy_pool  = list(PHYSICAL_STATS)
            if not tech_pool: tech_pool = list(TECHNICAL_STATS)

            # pool 내 스탯이 모두 한계에 도달했으면 전체 스탯으로 확장
            # 고강도/일반훈련 모두 '스탯별 _max'를 천장으로 본다. (고강도는 _max가
            # talent_cap+α로 높게 잡힌 강점을 100+까지 끌어올린다)
            def _below_max(stats_list):
                return [s for s in stats_list if p.get(s,40) < p.get(f"{s}_max",80)]
            phy_below  = _below_max(phy_pool)
            tech_below = _below_max(tech_pool)
            # pool 내 남은 스탯 없으면 전체에서 미달 스탯으로 확장
            if not phy_below:
                phy_below = _below_max(PHYSICAL_STATS) or phy_pool
            if not tech_below:
                tech_below = _below_max(TECHNICAL_STATS) or tech_pool
            phy_pool  = phy_below
            tech_pool = tech_below

            # 훈련 강도별 상승 스탯 수: 2개 or 3개
            # 2개 → 신체1 + 기술1
            # 3개 → 신체1 + 기술2 (기술 중 추가 1개 랜덤)
            if ttype == "고강도":
                cnt = random.choices([2, 3], weights=[60, 40])[0]
            elif ttype == "중강도":
                cnt = random.choices([2, 3], weights=[70, 30])[0]
            else:  # 저강도
                cnt = 2

            phy_pick  = random.sample(phy_pool, min(1, len(phy_pool)))

            # 우선순위 기술 스탯: PRIORITY_TECH_STATS에 있으면 70% 확률로 먼저 선택
            prio = [s for s in PRIORITY_TECH_STATS.get(pos, []) if s in tech_pool]
            rest = [s for s in tech_pool if s not in prio]

            def _pick_tech(n):
                picks = []
                pool_p = list(prio); pool_r = list(rest)
                for _ in range(n):
                    if pool_p and (not pool_r or random.random() < 0.70):
                        s = random.choice(pool_p); pool_p.remove(s)
                    elif pool_r:
                        s = random.choice(pool_r); pool_r.remove(s)
                    elif pool_p:
                        s = random.choice(pool_p); pool_p.remove(s)
                    else:
                        break
                    picks.append(s)
                return picks

            if cnt == 2:
                tech_pick = _pick_tech(1)
            else:
                tech_pick = _pick_tech(2)
            targets = phy_pick + tech_pick

            # [B] 비focus 기술 스탯도 천천히 성장.
            #   포커스가 다 차면 남는 성장 여력이 안 찬 비focus(예: ST의 패스)로
            #   흘러가도록 확률을 높였다. 전성기에 '올릴 게 없어' 정체되는 것을 완화.
            _nonfocus = [s for s in TECHNICAL_STATS
                         if s not in focus and p.get(s,40) < p.get(f"{s}_max",80)]
            if _nonfocus and ttype in ("고강도","중강도","저강도") and random.random() < 0.30:
                _slow_pick = random.choice(_nonfocus)
                _slow_targets = {_slow_pick}
                targets = targets + [_slow_pick]
            else:
                _slow_targets = set()

            # [천장 개선] focus 스탯들이 한계에 충분히 근접하면, 남은 성장 여력을
            #   '가중치 있는 비focus 스탯'에 정상 속도로 투입한다.
            #   이게 없으면 focus 몇 개만 한계에 닿고 나머지가 낮게 남아 OVR이
            #   천장보다 10+ 낮게 수렴한다.
            #   - 고강도: talent_cap 기준 (cap까지 돌파 가능)
            #   - 중강도: max 기준 (max까지만, 다 찬 뒤 안 찬 비focus 채움)
            if cfg.get("exceed_limit"):
                _focus_avg = sum(p.get(s,40) for s in focus) / max(1, len(focus))
                _focus_cap_avg = sum(p.get(f"{s}_max",80) for s in focus) / max(1, len(focus))
                if _focus_avg >= _focus_cap_avg - 6:
                    from database import WEIGHTS as _W
                    _posw = _W.get(p["position"], {})
                    _nf_all = [s for s in ALL_STATS
                               if s not in focus and _posw.get(s,0) > 0
                               and p.get(s,40) < p.get(f"{s}_max",80)]
                    if _nf_all:
                        _nf_all.sort(key=lambda s: _posw.get(s,0), reverse=True)
                        for s in _nf_all[:2]:
                            if s not in targets:
                                targets.append(s)
                        _slow_targets = set()
            elif ttype == "중강도":
                # 중강도: focus가 max에 거의 다 찼으면 가중치 있는 비focus를 채운다.
                _focus_full = all(p.get(s,40) >= p.get(f"{s}_max",80) - 2 for s in focus)
                if _focus_full:
                    from database import WEIGHTS as _W2
                    _posw2 = _W2.get(p["position"], {})
                    _nf2 = [s for s in ALL_STATS
                            if s not in focus and _posw2.get(s,0) > 0
                            and p.get(s,40) < p.get(f"{s}_max",80)]
                    if _nf2:
                        _nf2.sort(key=lambda s: _posw2.get(s,0), reverse=True)
                        for s in _nf2[:2]:
                            if s not in targets:
                                targets.append(s)
                        _slow_targets = set()

        talent_cap = p.get("talent_cap", 88)
        for stat in targets:
            g_min, g_max = cfg["gain_min"], cfg["gain_max"]
            # [신체 특징] 스탯 계열별 성장 배수
            #   - 신체천재/피지컬몬스터/스피드스타: 신체 스탯 성장↑
            #   - 강철체질: stamina 훈련 보너스
            #   - 성격 천재: 멘탈 스탯 성장↑
            gmul = 1.0
            if stat in PHYSICAL_STATS:
                pg = trait_fx.get("phys_growth_mult", 1.0)
                # 스피드스타처럼 특정 스탯 한정이면 그 스탯에만
                tps = trait_fx.get("phys_stat")
                if pg != 1.0 and (tps is None or tps == stat):
                    gmul *= pg
                if stat == "stamina" and "stamina_train" in trait_fx:
                    gmul *= trait_fx["stamina_train"]
            elif stat in MENTAL_STATS:
                gmul *= pe.get("mental_growth_mult", 1.0)   # 성격 천재
            if gmul != 1.0:
                g_min *= gmul; g_max *= gmul
            # [B] 비focus 스탯은 아주 천천히만 성장 (gain 대폭 감소)
            if stat in _slow_targets:
                g_min *= 0.30; g_max *= 0.30
            cur = p.get(stat, 40)
            mx  = p.get(f"{stat}_max", 80)

            if cfg.get("exceed_limit"):
                # 고강도 트랙: 스탯별 _max(break_cap)까지 돌파. talent_cap 일률이
                #   아니라 스탯마다 천장이 달라, 강점(_max 높음)은 100+까지 가고
                #   약점(_max 낮음)은 일찍 멈춰 평준화되지 않는다.
                #   한계 '마지막 몇 포인트'에서만 살짝 둔화시켜(soft) 천장에 닿는
                #   순간을 늦춘다. 그 외 구간은 거의 풀스피드(돌파 트랙).
                if cur < mx:
                    # [2026-08 재설계, 신민용 확정] '한계까지 남은 절대 포인트'
                    # 대신 '시작값→한계값 진행률(%)'로 감속 — 스탯 구간 폭이
                    # 달라도 일관된 커브가 나온다(_progress_soft 참고).
                    _start = _stat_starts.get(stat, p.get(stat, 40))
                    soft = _progress_soft(cur, _start, mx)
                    raw = random.uniform(g_min, g_max) * eff * soft
                    gain = round(raw, 1)  # [2026-07] 0.1 단위로 매번 눈에 보이게 누적(예전 확률적 1/0 방식 폐지)
                    new_val = round(min(mx, cur + gain), 1)  # 부동소수 오차 누적 방지
                else:
                    # _max 도달: 한 번 훈련 시 HIGH_BREAK_PROB(30%) 확률로 _max를 +1
                    #   끌어올려 talent_cap+α(강점 100+ 가능)까지 점진 돌파.
                    break_cap = min(125, talent_cap + 12)
                    if random.random() < HIGH_BREAK_PROB and mx < break_cap:
                        new_mx = mx + 1
                        stat_changes[f"{stat}_max_up"] = (stat, new_mx)
                        new_val = min(new_mx, cur + 1)
                    else:
                        new_val = cur
            elif cfg.get("focus_mode") in ("strong", "weak") or ttype == "집중훈련":
                # [강점/약점 집중훈련]
                #  - max 미달: 소프트캡을 완만히만 적용(일반훈련보다 덜 둔화) → 잘 오름
                #  - max 도달: 고강도와 달리 '가끔만'(FOCUS_BREAK_PROB) max를 1 끌어올려
                #              talent_cap까지 점진 돌파. 풀로 채운 뒤에도 천천히 cap을 향함.
                if cur < mx:
                    # [2026-08 재설계, 신민용 확정] 고강도와 동일한 진행률(%)
                    # 커브로 통일 — 더 이상 절대 포인트(SOFTCAP_DENOM)나
                    # 막판 ×2 보정을 따로 두지 않는다.
                    _start = _stat_starts.get(stat, p.get(stat, 40))
                    soft = _progress_soft(cur, _start, mx)
                    raw = random.uniform(g_min, g_max) * eff * soft
                    gain = round(raw, 1)  # [2026-07] 0.1 단위로 매번 눈에 보이게 누적(예전 확률적 1/0 방식 폐지)
                    new_val = round(min(mx, cur + gain), 1)  # 부동소수 오차 누적 방지
                else:
                    # max 도달 → 낮은 확률로만 한계 돌파 (cap 이하)
                    #   강점훈련은 돌파를 강하게(특화), 약점훈련은 거의 안 함(안전).
                    if focus_mode == "strong":
                        _break_p = FOCUS_BREAK_PROB_STRONG
                    elif focus_mode == "weak":
                        _break_p = FOCUS_BREAK_PROB_WEAK
                    else:
                        _break_p = FOCUS_BREAK_PROB
                    if random.random() < _break_p:
                        new_mx = min(99, talent_cap, mx + 1)
                        if new_mx > mx:
                            stat_changes[f"{stat}_max_up"] = (stat, new_mx)
                            new_val = min(new_mx, cur + 1)
                        else:
                            new_val = cur   # 이미 talent_cap
                    else:
                        new_val = cur
            else:
                # [2026-08 재설계, 신민용 확정] 일반훈련(중강도/저강도) 트랙도
                # 고강도/집중훈련과 동일한 진행률(%) 커브로 통일. max를 못
                # 넘는 트랙이라 여기선 돌파 분기가 없고 cur이 mx에 점근할 뿐.
                _start = _stat_starts.get(stat, p.get(stat, 40))
                soft = _progress_soft(cur, _start, mx)
                raw = random.uniform(g_min, g_max) * eff * soft
                gain = round(raw, 1)  # [2026-07] 0.1 단위로 매번 눈에 보이게 누적(예전 확률적 1/0 방식 폐지)
                new_val = round(min(mx, cur + gain), 1)  # 부동소수 오차 누적 방지
            if new_val > cur:
                stat_changes[stat] = new_val - cur

        label = f"[{ttype}]"
        log_parts = [f"🏃 {label}  {_day_label(week, day)}"]
        max_ups      = {k: v for k, v in stat_changes.items() if k.endswith("_max_up")}
        real_changes = {k: v for k, v in stat_changes.items() if not k.endswith("_max_up")}
        if real_changes or max_ups:
            for s, v in real_changes.items():
                log_parts.append(f"   {STAT_KO.get(s,s)} {v:+.1f}")
            for _, (stat, new_mx) in max_ups.items():
                log_parts.append(f"   {STAT_KO.get(stat,stat)} 잠재력↑ (최대 {new_mx})")
        else:
            log_parts.append("   (변화 없음)")

    # 업데이트
    new_stress  = max(0, min(100, p["stress"] + stress_chg))
    new_happy   = max(0, min(100, p["happiness"] + happy_chg))
    updates = dict(stress=new_stress, happiness=new_happy)
    max_ups   = {k: v for k, v in stat_changes.items() if k.endswith("_max_up")}
    real_changes = {k: v for k, v in stat_changes.items() if not k.endswith("_max_up")}
    for s, delta in real_changes.items():
        updates[s] = p.get(s, 40) + delta
    for _, (stat, new_mx) in max_ups.items():
        updates[f"{stat}_max"] = new_mx

    # 슬럼프 체크
    slump = p.get("slump", 0)
    if not slump:
        # 강철멘탈: 슬럼프 완전 면역
        if pe.get("no_slump"):
            pass
        else:
            threshold = SLUMP_STRESS_THRESHOLD
            # 유리멘탈: 발동 임계치를 낮춤
            threshold -= pe.get("slump_threshold_reduce", 0)

            # [행복도 연동] 행복도가 낮으면(SLUMP_LOW_HAPPY 이하) 슬럼프 임계치를
            #   40으로 낮춘다. 즉 스트레스가 60에 못 미쳐도 불행하면 슬럼프가 올 수 있다.
            #   - 스트레스 >= 정규 임계치(60)  : 기존 확률(SLUMP_CHANCE)
            #   - 행복도 낮고 스트레스 40~59   : 낮은 확률(SLUMP_LOW_HAPPY_CHANCE)
            low_happy = new_happy <= SLUMP_LOW_HAPPY
            eff_threshold = threshold
            if low_happy:
                eff_threshold = min(threshold, SLUMP_LOW_HAPPY_STRESS)

            if new_stress >= eff_threshold and ttype != "휴식":
                # 정규 구간(스트레스 60+)인지, 저행복 구간(40~59)인지로 베이스 확률 분기
                if new_stress >= threshold:
                    chance = SLUMP_CHANCE
                else:
                    # 저행복 때문에 낮은 임계치로 진입한 구간
                    chance = SLUMP_LOW_HAPPY_CHANCE
                if "slump_chance_mult" in pe:
                    chance *= pe["slump_chance_mult"]
                # 유리멘탈: 60 이상 구간에선 확률 추가
                if pe.get("slump_chance_add") and new_stress >= SLUMP_STRESS_THRESHOLD:
                    chance += pe["slump_chance_add"]
                chance = min(1.0, chance)
                if random.random() < chance:
                    slump = 1
                    if new_stress >= threshold:
                        add_log(f"😰 슬럼프 발생!  {_day_label(week, day)}", "slump")
                    else:
                        add_log(f"😰 행복도 저하로 슬럼프!  {_day_label(week, day)}", "slump")
            if new_happy <= SLUMP_HAPPY_THRESHOLD:
                slump = 1
                add_log(f"😰 행복도 저하로 슬럼프!  {_day_label(week, day)}", "slump")
    else:
        if new_stress <= SLUMP_RECOVER_STRESS:
            slump = 0
            add_log(f"😊 슬럼프 해소!  {_day_label(week, day)}", "slump")

    updates["slump"] = slump
    # [2026-08 버그수정] 여기도 신 등급이면 talent_cap(100~105)을 상한으로
    # 넘겨야 한다 — 안 그러면 calc_ovr 기본 cap(100)에 걸려 훈련으로
    # 100을 넘기는 게 원천적으로 불가능해진다(바로 아래 99 클램프 이전에
    # 이미 100에서 잘려있었음).
    _cap = p.get("talent_cap", 100) if p.get("talent_tier") == "god" else 100
    updates["ovr"]   = calc_ovr(p["position"], {s: updates.get(s, p.get(s,40))
                                                  for s in ALL_STATS}, cap=_cap)
    # [2026-08 신설, 신민용 확정: "신이 아니면 100을 못 찍게 하고 싶어"]
    # 강점 스탯은 talent_cap+12까지 브레이크될 수 있어서(위 break_cap 참고),
    # OVR(가중평균)이 통계적으로 100 근처까지 갈 가능성이 있었다 — "약점이
    # 낮아서 평균은 대충 유지된다"는 기대에만 의존하던 예전 방식은 수학적
    # 보장이 아니었다. talent_tier가 "god"이 아니면 여기서 무조건 99로
    # 클램프해서, 실제로 어떤 스탯 조합이 나오든 신 등급 외엔 100이 절대
    # 안 나오게 못박는다.
    if p.get("talent_tier") != "god":
        updates["ovr"] = min(updates["ovr"], 99)
    update_player(**updates)
    for line in log_parts:
        add_log(line, "training")


def _apply_injury(p, week, day=None):
    # [2026-07 확장] 등급(경미/중간/심각/매우 심각)을 먼저 확률로 고르고,
    # 그 등급 안의 구체 부상(INJURY_DETAILS)을 하나 더 골라서 그 부상
    # 고유의 좁은 회복 범위로 주수를 정한다 — "부상!"으로 뭉뚱그리지 않고
    # "왼쪽 발목 인대 파열" 식으로 실제로 뭐가 다쳤는지 로그/화면에 남는다.
    #
    # [2026-07 버그 수정] INJURY_TYPES/INJURY_DETAILS의 범위는 '주' 단위로
    # 설계했다(예: ACL 파열 24~32주 ≈ 실제 6~8개월 회복 기간). 그런데
    # _process_injury_week가 (일 단위 진행 체계에서) 하루에 한 번씩 호출돼
    # injury_weeks를 매번 -1 해왔다 — 즉 '주' 필드인데 실제로는 매일
    # 깎여서, 8주 부상이 실제로는 8일 만에 나아버리는 버그가 있었다(회복
    # 기간이 의도한 것의 1/7로 단축). 이제 주수를 실제 일수로 환산해서
    # 저장한다(예: 8주 → 56일) — injury_weeks 필드명은 마이그레이션을
    # 피하려고 그대로 두지만, 이제부터는 '남은 일수'를 담는다.
    roll = random.random()
    cum = 0.0
    itype = "경미"
    for tier, chance in INJURY_TIER_CHANCE.items():
        cum += chance
        if roll < cum:
            itype = tier
            break
    detail_pool = INJURY_DETAILS.get(itype) or [(itype, INJURY_TYPES[itype])]
    detail_name, (wmin, wmax) = random.choice(detail_pool)
    weeks = random.randint(wmin, wmax)
    days = weeks * DAYS_PER_WEEK
    # 등급이 심할수록 행복도 타격도 커지게(기존엔 등급 무관 고정 -20).
    happy_penalty = {"경미": 10, "중간": 15, "심각": 20, "매우 심각": 30}.get(itype, 20)
    update_player(injured=1, injury_weeks=days, injury_type=itype, injury_detail=detail_name,
                  happiness=max(0, p["happiness"] - happy_penalty))
    add_log(f"🚑 {detail_name} ({itype})!  {_day_label(week, day)}  ({days}일 휴식 필요)", "injury")


def _process_injury_week(p, week, day=None):
    left = p["injury_weeks"] - 1
    if left <= 0:
        update_player(injured=0, injury_weeks=0, injury_type="", injury_detail="")
        add_log(f"✅ {p.get('injury_detail') or '부상'} 회복!  {_day_label(week, day)}", "injury")
    else:
        # [2026-07 신설, 신민용 설계+확정: "인기도 = 최근 화제성 — 못 뛰면
        # 감소"] 부상으로 오래 빠지면 화제성이 서서히 식는다. 하루 단위로
        # 매일 깎으면 장기 부상 한 번에 인기도가 다 날아가버리므로, 낮은
        # 확률(15%)로만 -1씩 — 기대값으로 대략 1주(6~7일)에 1점 안팎
        # 빠지는 정도로 완만하게.
        pop = p.get("popularity", 0)
        pop_updates = {}
        if pop > 0 and random.random() < 0.15:
            pop_updates["popularity"] = pop - 1
        update_player(injury_weeks=left, **pop_updates)
        detail = p.get("injury_detail") or "부상"
        add_log(f"🚑 {detail} 휴식  {_day_label(week, day)}  ({left}일 남음)", "injury")


# ─────────────────────────────────────────
# 경기 시뮬레이션
# ─────────────────────────────────────────

def _soft_cap(x, cap):
    """[2026-07 신설] 하드 컷 대신 쓰는 소프트캡 — cap을 넘으면 잘라내지 않고
    점근적으로만 완만하게 계속 늘어나게 한다(완전히 평평해지지 않음).
    _simulate_match의 캐리 보너스가 상한 근처에서도 OVR 차이를 계속
    구분해 반영하도록 쓰인다."""
    if x <= cap:
        return x
    over = x - cap
    return cap + cap * 0.15 * (1 - math.exp(-over / cap))


def _season_condition_mult(year):
    """[2026-07 신설, 신민용 확정] 시즌마다 숨은 컨디션 변동(±8%)을 돌려준다.
    연도를 시드로 쓰는 결정적(deterministic) 난수라 같은 시즌은 항상 같은
    값이 나온다(같은 세이브를 다시 불러와도 결과가 안 바뀜). star 지수
    (`_simulate_match`의 볼록 곡선) 자체는 건드리지 않고, 그 위에 곱해서
    "이 시즌은 폼이 좋았다/나빴다" 체감만 얹는 용도."""
    rng = random.Random(f"season_condition_{year}")
    return rng.uniform(0.92, 1.08)


def _age_curve_mult(age):
    """[2026-07 신설] 나이대별 완만한 폼 보정 (OVR 자체는 성장/노화 시스템이
    이미 반영하므로 건드리지 않고, 그 위에 별도로 곱하는 소폭 보정).
    20세 0.93 → 26~28세 전성기 1.00~1.03 → 34세 0.92로 부드럽게 변화."""
    pts = [(18, 0.90), (20, 0.93), (23, 0.98), (26, 1.00),
           (28, 1.03), (30, 1.00), (32, 0.97), (34, 0.92), (38, 0.85)]
    if age <= pts[0][0]:
        return pts[0][1]
    if age >= pts[-1][0]:
        return pts[-1][1]
    for (a0, v0), (a1, v1) in zip(pts, pts[1:]):
        if a0 <= age <= a1:
            t = (age - a0) / (a1 - a0)
            return v0 + (v1 - v0) * t
    return 1.0


def _simulate_match(p, week, info: dict, day=None):
    conn = get_conn()
    c = conn.cursor()
    st = get_state()  # 현재 게임 상태 (연도 등)

    home_id  = info["home_id"]
    away_id  = info["away_id"]
    my_tid   = p.get("current_team_id", 0)
    is_home  = info["is_home"]

    home_ovr = _team_avg_ovr(c, home_id)
    away_ovr = _team_avg_ovr(c, away_id)

    my_ovr  = p.get("ovr", 40)
    _suspended, _new_susp = _check_suspended(p)
    if _suspended:
        update_player(red_card_suspension=_new_susp)
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event", st["current_year"], week)
    benched = _check_bench(p)
    played  = (not _suspended) and not benched and not p.get("injured")

    bonus = 0.0
    if played:
        # [2026-07 재설계 — 비선형 캐리] 예전엔 gap에 선형 계수(0.32)만 곱해서
        # OVR 50대든 90대든 "같은 갭이면 같은 배율"로 팀을 끌어올렸다 — 그런데
        # 현실은 그렇지 않다(신민용 지적): OVR90대~100은 50대~60대와 갭이
        # 같아도 훨씬 크게 경기를 지배해야 한다("메시급"과 "준수한 선수"의
        # 차이는 산술적이지 않고 기하급수적). 그래서 my_ovr 자체에 따라
        # 갭 배율(star)이 볼록하게(convex) 커지는 구조로 바꿨다 — 60 이하는
        # 거의 기존과 비슷하지만, 70을 넘기면서 급격히 커진다.
        #
        # 또한 예전 상한(14.0)은 하드 컷이라 OVR 80만 넘으면 거의 다 캡에
        # 걸려서 92와 100이 똑같이 취급되는 문제가 있었다(사용자 실측:
        # OVR92 골키퍼가 팀평균 30후반 리그에서 전혀 안 먹히는 느낌 —
        # 포지션 채널 문제와 별개로 이 캡도 원인이었다). 하드 컷 대신
        # 소프트캡(_soft_cap)으로 바꿔서 상한 근처에서도 완만하게 계속
        # 늘어나게 했다 — 92와 100이 여전히 구분된다.
        team_avg = home_ovr if is_home else away_ovr
        gap = max(0.0, my_ovr - team_avg)
        star = 1.0 + max(0.0, (my_ovr - 60) / 40.0) ** 1.8 * 3.0
        bonus = gap * 0.30 * star + max(0.0, my_ovr - 50) * 0.08
        bonus = _soft_cap(bonus, 30.0)
        # [2026-07 신설, 신민용 확정: "매 시즌 50골이 반복되는 게 부자연스럽다
        # — star 지수는 건드리지 말고 컨디션·나이 변동으로 자연스러운 편차를
        # 만들자"] star 공식(볼록 지수 1.8 등)은 그대로 두고, 그 위에 시즌별
        # 컨디션 변동(±8%, 연도 시드 결정적 난수라 같은 시즌은 항상 같은 값)과
        # 나이 곡선(전성기 근처 소폭 우대, 어릴 때/노장일 때 소폭 페널티)을
        # 곱한다 — 같은 OVR이라도 "2004 시즌엔 폼이 좋았다/2005는 별로였다"
        # 처럼 시즌 간 기복이 생겨서, 3시즌 연속 50골 이상 같은 지나치게
        # 균일한 결과가 완화된다.
        bonus *= _season_condition_mult(st["current_year"]) * _age_curve_mult(p.get("age", 25))
        # [2026-07 신설] '리더십' 성격의 team_win_bonus 연결 — 다른 대회
        # (챔스/컵/국제대회)와 동일하게 리그 경기에도 적용해 일관성을 맞춘다.
        _pe_bonus = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
        if "team_win_bonus" in _pe_bonus:
            bonus *= (1.0 + _pe_bonus["team_win_bonus"])

    my_position = p.get("position", "")

    # [재설계 — 포메이션 매치업 시뮬레이션] 예전엔 홈-원정 OVR 차이 하나로
    # 확률표(_match_win_probs/_gen_score)에서 스코어를 뽑았다 — 포메이션이
    # 실제로 어느 구역에서 수적/능력치 우위를 만드는지는 결과에 전혀
    # 개입하지 못했다. 이제 실제 포메이션 매치업(레인별 공격/수비 스탯
    # 비교)을 분 단위로 시뮬레이션한 결과를 쓴다 — 단, 이건 "내가 직접
    # 보는 경기"에만 적용한다. 리그 나머지 수십~수백 경기(AI 대 AI)는
    # 이 무거운 시뮬레이션을 돌릴 필요도 의미도 없어서 그대로
    # _roll_outcome/_gen_score를 쓴다(이 함수는 안 건드림). 새 엔진에
    # 예외가 나도 경기 진행 자체가 막히면 안 되므로, 실패 시 예전 방식
    # (OVR 차이 확률표)으로 조용히 폴백한다.
    engine_stats = None
    engine_plog = None
    try:
        from match_sim.tactical_engine import simulate_my_match
        home_formation = _team_formation(c, home_id)
        away_formation = _team_formation(c, away_id)
        sim = simulate_my_match(
            home_id, away_id, home_formation, away_formation,
            home_boost=(bonus if is_home else 0.0),
            away_boost=(bonus if not is_home else 0.0),
            home_boost_position=(my_position if is_home else None),
            away_boost_position=(my_position if not is_home else None),
            home_adv=_home_advantage())
        hs, as_ = sim["home_score"], sim["away_score"]
        engine_stats = {"home": sim["home_stats"], "away": sim["away_stats"]}
        engine_plog = sim["possession_log"]
        outcome = "draw" if hs == as_ else ("home" if hs > as_ else "away")
        # [정리] 이전엔 여기서 diff(홈-원정 전력차)를 다시 계산했는데, hs/as_는
        # 이미 위 전술엔진(simulate_my_match)이 정한 값이라 이 diff는 outcome/
        # 스코어 어디에도 쓰이지 않는 죽은 변수였다. 게다가 _home_advantage()를
        # 여기서 한 번 더 호출해 위(home_adv=...)에서 뽑은 값과 다른 난수를
        # 낭비하고 있었다 — 결과에 영향은 없었지만 불필요한 계산이라 제거.
    except Exception:
        home_ovr2 = home_ovr + (bonus if is_home else 0.0)
        away_ovr2 = away_ovr + (bonus if not is_home else 0.0)
        home_ovr2 += _home_advantage() + _formation_bias(c, home_id)
        away_ovr2 += _formation_bias(c, away_id)
        diff = home_ovr2 - away_ovr2
        outcome = _roll_outcome(diff)
        hs, as_ = _gen_score(outcome, diff)

    goals = assists = saves = 0
    rating = 0.0
    events = []
    detail = {"shots":0,"shots_on":0,"key_passes":0,"dribbles":0,"blocks":0,"pass_acc":0.0}
    if played:
        # [2026-07 통일] 국제대회(intl_engine)는 이미 "오늘 상대의 실제 OVR"을
        # dom 기준으로 써서 강팀 만나면 개인도 고전, 약체 만나면 활약 폭발이
        # 반영돼 있었는데, 리그 경기는 그 상대별 감도 없이 "내 리그 전체
        # 평균"만 써서 오늘 1위팀을 만나든 꼴찌팀을 만나든 개인 활약 배수가
        # 항상 똑같았다 — 스코어(팀 단위)는 이미 상대별로 다르게 나오는데
        # 개인 스탯만 그걸 못 따라가는 비대칭이었다. 국제대회와 동일하게
        # 오늘 상대 팀의 실제 평균 OVR을 넘긴다.
        _opp_ovr = away_ovr if is_home else home_ovr
        # [2026-07 구조수정] 전술엔진이 실제로 만든 유효슈팅 수를 GK 평점
        # 계산에 그대로 넘긴다 — engine_stats가 있고(전술엔진 성공) 내가 GK일
        # 때만 계산(필드플레이어는 안 쓰는 값이라 굳이 계산 안 함). 전술엔진이
        # 예외로 폴백했다면(engine_stats=None) _player_perf 내부가 자동으로
        # 예전 랜덤 테이블 경로로 폴백한다.
        _opp_sot = None
        if engine_stats is not None and my_position == "GK":
            _opp_sot = engine_stats["away"]["shots_on"] if is_home else engine_stats["home"]["shots_on"]
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, c=c, opp_ovr=_opp_ovr, opp_sot=_opp_sot)
        if p.get("slump"):
            rating = round(max(3.0, rating + SLUMP_RATING_PENALTY), 1)
        # [2026-07 신설] 퇴장 판정 — '폭력적' 성격의 red_card_chance 반영.
        # 발동하면 그 경기 활약(골/도움/평점)을 조기 강판 처리로 덮어쓰고
        # 다음 경기 출전정지를 건다.
        if _roll_red_card(p):
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(p)

        # [2026-08 신설, 골 시상 시스템 v4] 실제로 골을 넣었으면 즉시
        # goal_events에 기록(is_mine=1, is_pseudo=0) — "올해의 골" 판정의
        # 근거 데이터. 리그 경기 훅만 우선 반영(컵/국제대회는 각 엔진에
        # 별도 훅이 필요해 다음 단계로 미룸 — 설계문서 5절 열린 질문 참고).
        if played and goals > 0:
            _lg_id = info.get("league_id", 0)
            if _lg_id:
                _lg_row = c.execute(
                    """SELECT cn.name AS cname, cn.grade AS cgrade, l.name AS lname
                       FROM leagues l JOIN countries cn ON l.country_id=cn.id
                       WHERE l.id=?""", (_lg_id,)).fetchone()
                if _lg_row:
                    from constants import get_league_grade
                    _my_grade_now = get_league_grade(_lg_row["cname"], _lg_row["cgrade"])
                    _opp_id_now = away_id if is_home else home_id
                    _opp_ovr_now = away_ovr if is_home else home_ovr
                    for _ in range(goals):
                        _record_goal_event(
                            c, p, st["current_year"], week, my_tid, _opp_id_now,
                            "league", _lg_id, _lg_id, _lg_row["lname"],
                            _my_grade_now, _opp_ovr_now)

    my_result = _my_result(outcome, is_home)

    # 팀 전적 업데이트 (같은 conn 내에서)
    _update_team_rec(c, home_id, away_id, outcome, hs, as_)

    # 경기 결과 저장
    c.execute("""UPDATE match_results SET home_score=?,away_score=?
                 WHERE league_id=? AND week=? AND home_team_id=? AND away_team_id=?
                 AND season=?""",
              (hs, as_, info.get("league_id",0), week,
               home_id, away_id, info.get("season",1)))
    conn.commit()
    conn.close()  # ← 여기서 먼저 닫고 아래에서 update_player 호출

    # 내 시즌 통계 (conn 닫힌 후)
    if played:
        _ga = (as_ if info.get("is_home") else hs) if p.get("position") == "GK" else 0
        update_player(
            total_matches=p["total_matches"]+1,
            total_goals=p["total_goals"]+goals,
            total_assists=p["total_assists"]+assists,
            total_saves=p.get("total_saves",0)+saves,
            total_goals_against=p.get("total_goals_against",0)+_ga,
            season_matches=p.get("season_matches",0)+1,
            season_goals=p.get("season_goals",0)+goals,
            season_assists=p.get("season_assists",0)+assists,
            season_saves=p.get("season_saves",0)+saves,
            season_rating_sum=p.get("season_rating_sum",0)+rating,
            season_rating_cnt=p.get("season_rating_cnt",0)+1,
            season_goals_against=p.get("season_goals_against",0)+_ga,
            # [2026-08 신설] award_* — season_*와 똑같이 쌓지만 이적해도
            # 리셋 안 되는 시즌 전체 누적(시상 계산 전용, _primary_club_
            # this_season/_process_awards 참고).
            award_matches=p.get("award_matches",0)+1,
            award_goals=p.get("award_goals",0)+goals,
            award_assists=p.get("award_assists",0)+assists,
            award_saves=p.get("award_saves",0)+saves,
            award_goals_against=p.get("award_goals_against",0)+_ga,
            award_rating_sum=p.get("award_rating_sum",0)+rating,
            award_rating_cnt=p.get("award_rating_cnt",0)+1,
            # [세부 지표] 누적 (season_ + total_). 패스성공률은 합·횟수로 평균 산출.
            season_shots=p.get("season_shots",0)+detail["shots"],
            season_shots_on=p.get("season_shots_on",0)+detail["shots_on"],
            season_key_passes=p.get("season_key_passes",0)+detail["key_passes"],
            season_dribbles=p.get("season_dribbles",0)+detail["dribbles"],
            season_blocks=p.get("season_blocks",0)+detail["blocks"],
            season_pass_acc_sum=p.get("season_pass_acc_sum",0)+detail["pass_acc"],
            season_pass_acc_cnt=p.get("season_pass_acc_cnt",0)+1,
            total_shots=p.get("total_shots",0)+detail["shots"],
            total_shots_on=p.get("total_shots_on",0)+detail["shots_on"],
            total_key_passes=p.get("total_key_passes",0)+detail["key_passes"],
            total_dribbles=p.get("total_dribbles",0)+detail["dribbles"],
            total_blocks=p.get("total_blocks",0)+detail["blocks"],
        )

    # [최적화] get_player 재조회 없이 p에서 직접 계산 후 update_player 1회 통합
    new_rel = _calc_manager_rel(p, rating, my_result, played)
    # [2026-07 신설] 인기도가 리그 등급을 반영하도록 이 경기 리그의 등급을
    # 조회한다(위에서 이미 conn을 닫았으므로 짧게 새로 연다) — 실패해도
    # (조회 안 되는 예외 상황) grade=None으로 폴백해 배수 1.0(중립) 처리.
    _pop_grade = None
    try:
        from constants import get_league_grade
        _conn_g = get_conn()
        _grow = _conn_g.execute(
            """SELECT cn.grade AS cgrade, cn.name AS cname
               FROM leagues l JOIN countries cn ON l.country_id=cn.id
               WHERE l.id=?""", (info.get("league_id", 0),)).fetchone()
        _conn_g.close()
        if _grow:
            _pop_grade = get_league_grade(_grow["cname"], _grow["cgrade"])
    except Exception:
        _pop_grade = None
    new_pop = _calc_pop(p, goals, assists, rating, grade=_pop_grade)

    # 스트레스/행복/멘탈 계산 (p에서 직접, get_player 재조회 제거)
    age = p.get("age", 0) or 0
    # [2026-07 조정, 신민용 지적: "경기 스트레스가 고강도 훈련만큼은 돼야
    # 하지 않나"] 기존엔 3~8로, 고강도 훈련 스트레스(+20)의 1/4~1/2에
    # 불과했다 — 실제 경기는 전력 질주·태클·결과 압박까지 겹쳐 단일
    # 훈련 세션보다 덜하지 않다고 보는 게 맞다. 고강도 훈련(20)과 같거나
    # 넘는 수준으로 올리되, 홈/원정·나이 차등(원정↑, 30대↓)은 그대로 유지.
    # [2026-07 재조정, 신민용 확정] 고강도 훈련(16)과 균형 맞춰 경기
    # 스트레스도 14/18로 하향 — "경기 있는 주엔 고강도 1회가 자연스러운
    # 선택"이 되도록. 30대는 그보다 더 낮게(체력 안배) 유지.
    # [2026-07 재조정, 신민용 확정] 3단계 연령 구간: 25세 미만 18/22,
    # 25~29세 16/20, 30대 10/14 (체력 안배 반영, 나이 들수록 완만하게 감소).
    if age >= 30:
        match_stress = 10 if info.get("is_home") else 14
    elif age >= 25:
        match_stress = 16 if info.get("is_home") else 20
    else:
        match_stress = 18 if info.get("is_home") else 22
    ns = min(100, p["stress"] + match_stress)
    nh = p["happiness"]
    # [2026-07 신설] '긍정적' 성격의 happy_gain_mult — 승리 시 행복도 상승분에만 적용.
    _pe_happy = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    _win_happy_gain = 3
    if "happy_gain_mult" in _pe_happy:
        _win_happy_gain = round(_win_happy_gain * _pe_happy["happy_gain_mult"])
    if my_result == "win":    nh = min(100, nh+_win_happy_gain)
    elif my_result == "loss": nh = max(0,   nh-3)
    if p.get("slump"):
        nh = max(0, nh - 15)

    mental_updates = {}
    if played:
        n_up = random.choices([1, 2], weights=[70, 30])[0]
        for ms in random.sample(MENTAL_STATS, n_up):
            cur = p.get(ms, 40)
            mx  = p.get(f"{ms}_max", 80)
            if cur < mx:
                mental_updates[ms] = min(mx, cur + 1)
    else:
        ms = random.choice(MENTAL_STATS)
        cur = p.get(ms, 40)
        if cur > 20:
            mental_updates[ms] = cur - 1
        add_log(f"⚠ 경기 불참  {_day_label(week, day)}  {STAT_KO.get(ms,ms)} -1", "training")

    # [최적화] 감독관계·인기도·스트레스·행복·멘탈 모두 1회 update_player로 통합
    update_player(manager_relation=new_rel, popularity=new_pop,
                  stress=ns, happiness=nh, **mental_updates)

    _write_match_log(p, week, info["league_name"], is_home,
                     home_id, away_id, hs, as_,
                     my_result, goals, assists, saves, rating, events, played, benched,
                     detail=detail, engine_stats=engine_stats, engine_plog=engine_plog, day=day)


_team_prestige_cache: dict = {}
PRESTIGE_MATCH_BONUS = 8.0

# [2026-08 사용 중단] _team_avg_ovr()이 이제 이 고정 보너스 대신
# teams.club_strength(동적 팀 강도)를 쓴다. 이 함수 자체는 seed_club_
# strength_from_prestige()가 참조하는 data/prestige_clubs.PRESTIGE_TEAMS와
# 별개 유틸이라 그대로 남겨두지만, 매치 계산 경로에서는 더 이상 호출되지
# 않는다.
def _is_team_prestige_cached(c, team_id):
    """팀의 명문팀 여부를 세션 캐시. is_prestige()는 국가+팀명만 보므로
    강등돼도(tier가 바뀌어도) 결과가 안 바뀐다 — 그래서 세션 내내
    무효화할 필요 없이 캐시해도 안전하다."""
    cached = _team_prestige_cache.get(team_id)
    if cached is not None:
        return cached
    row = c.execute("""SELECT t.name AS tname, t.current_tier AS tier, cn.name AS cname
                        FROM teams t JOIN leagues l ON t.league_id = l.id
                        JOIN countries cn ON l.country_id = cn.id WHERE t.id=?""",
                    (team_id,)).fetchone()
    if not row:
        _team_prestige_cache[team_id] = False
        return False
    from data.prestige_clubs import is_prestige
    result = is_prestige(row["cname"], row["tier"], row["tname"])
    _team_prestige_cache[team_id] = result
    return result


def _team_avg_ovr(c, team_id):
    # 세션 캐시: 같은 team_id는 항상 같은 평균을 반환하므로 1회만 집계.
    cached = _team_ovr_cache.get(team_id)
    if cached is not None:
        return cached
    c.execute("SELECT AVG(ovr) as v FROM ai_players WHERE team_id=?", (team_id,))
    row = c.fetchone()
    val = row["v"] if row and row["v"] else 45
    # [2026-07 신설, 신민용 리포트: "명문팀(prestige_clubs.py)이 그래도
    # 강등당한다 — 초기 시딩에 보너스를 줬는데도 안 먹힌다"] 실측(실제
    # 세이브의 ai_players_seed)해보니 명문팀도 보너스를 받긴 받는데,
    # ace_lo 압축(팀간 OVR 격차를 96~100, 최대 4점으로 좁힌 것 — "팀간
    # 격차가 너무 크다"는 예전 지적으로 이미 좁혀둔 값) 때문에 OVR
    # 생성 단계의 보너스가 100 상한 클램프에 막혀 사실상 무효화되고
    # 있었다(시뮬레이션 검증: OVR 쪽 보너스를 아무리 올려도 강등확률이
    # 4.6~5.0%에서 거의 안 줄었음). 그래서 "저장되는 OVR"이 아니라
    # "경기 시뮬레이션에서만 쓰이는 이 팀 평균값"에 별도 보너스를 얹는다
    # — 100 클램프를 아예 우회하므로 실제 매치 승률에 확실히 반영된다
    # (같은 방식 시뮬레이션 검증: 강등확률 8.28%→0.02%). 화면에 보이는
    # 선수 개개인 OVR·전체 이력의 "팀간 격차가 좁다"는 느낌은 그대로
    # 유지하면서, 경기 결과에만 명문팀 우대가 반영된다.
    # [2026-08 교체, 신민용 확정: 동적 팀 강도] 정적 명문팀 하드코딩
    # 보너스(PRESTIGE_MATCH_BONUS)를 매치마다 고정으로 얹던 방식을
    # 그만두고, 그 세이브 안에서 실제 성적으로 쌓이고 깎이는
    # teams.club_strength를 대신 더한다. new_game 시 seed_club_strength_
    # from_prestige()가 명문팀 등급을 "초기값"으로만 심어두고, 이후로는
    # update_club_strength_after_season()(_process_promotion_relegation에서
    # 매 시즌 호출)이 실제 순위로 이 값을 계속 갱신한다 — 그래서 몇 시즌
    # 내내 못하면 명문팀도 서서히 이 보너스를 잃는다.
    # [2026-08 버그수정] club_strength 원래 값(-10~+12)을 그대로 더하면
    # 순수 스쿼드 OVR 격차(같은 리그 팀 간 보통 4~5점)를 완전히 뒤엎는
    # 크기라 최근 성적(club_strength)이 스쿼드 실력보다 매치 결과를 더
    # 좌우하는 문제가 있었다(constants.CLUB_STRENGTH_MATCH_WEIGHT 주석
    # 참고). 매치 강도 계산에만 가중치를 곱해 축소한다 — club_strength
    # 원본 값 자체(부전승 방어선, 신인 OVR 보정 등 다른 용도)는 그대로.
    from constants import CLUB_STRENGTH_MATCH_WEIGHT, PRESTIGE_LEVEL_MATCH_BONUS
    cs_row = c.execute("SELECT club_strength FROM teams WHERE id=?", (team_id,)).fetchone()
    if cs_row and cs_row["club_strength"]:
        val += cs_row["club_strength"] * CLUB_STRENGTH_MATCH_WEIGHT
    # [2026-08 신설, 신민용 설계 확정: "명문팀 우승 비율이 너무 낮다"]
    # club_strength(변동, 실적 기반)만으로는 명문 등급별 우승 비율 목표에
    # 크게 못 미쳤다(실측: 독일 10%, 스페인 15% 등 — 목표는 65~85%,
    # 70~85%). 등급(3/2/1)에 따라 매치 강도에 고정 보너스를 더한다 —
    # club_strength처럼 성적에 따라 오르내리지 않고, "그 팀이 명문이라는
    # 사실 자체"에서 나오는 꾸준한 이점(선수단 깊이·인프라 등)을 표현한다.
    # is_prestige()/prestige_level()과 같은 원칙대로 강등돼도 등급을
    # 유지한다(원래 명문이었다는 사실 자체는 안 바뀌므로).
    _pinfo = c.execute("""SELECT t.name AS tname, cn.name AS cname
                          FROM teams t JOIN countries cn ON t.country_id = cn.id
                          WHERE t.id=?""", (team_id,)).fetchone()
    if _pinfo:
        from data.prestige_clubs import prestige_level
        _plevel = prestige_level(_pinfo["cname"], _pinfo["tname"])
        if _plevel:
            val += PRESTIGE_LEVEL_MATCH_BONUS.get(_plevel, 0.0)
    _team_ovr_cache[team_id] = val
    return val


def _team_avg_ovr_with_me(c, team_id, p):
    """[2026-07 신설, 신민용 지적: "내가 주전경쟁에서 이기면 그 팀 자체가
    더 강해진 걸로 반영돼야 한다"] _team_avg_ovr은 ai_players 테이블만
    보기 때문에 나(my_player)를 절대 포함할 수 없다. 여기서는 내가
    부상/출전정지가 아니고 이 팀 소속이면, 내 포지션의 기존 주전
    (같은 포지션 중 최고 OVR)과 비교해서 내가 더 높을 때만 그 선수를
    나로 교체한 것으로 평균을 재계산한다(포메이션 화면의 merit-based
    선발 로직과 동일한 원칙).

    적용 범위(A안 — 좁은 수정): "내가 실제로 뛸 수 있는 상태인데 개인
    시뮬 경로를 안 타는 내 팀 경기"에만 쓴다 — _sim_my_unscheduled_match,
    _sim_all_ai_matches의 비시즌 중 내 경기 케이스. 아래는 이 함수를
    쓰지 않는 이유:
      - _simulate_match(내가 직접 뛰는 경기)는 이미 carry bonus로
        h_ovr/a_ovr에 반영되고 있어 중복 적용 방지 위해 건드리지 않음.
      - intl_engine의 내 국제경기도 동일하게 이미 bonus 반영됨.
      - _sim_my_team_match_as_ai/sim_my_*_match_as_ai(부상 등으로 AI가
        대신 뛰는 경기)는 애초에 내가 못 뛰는 상황이라 보정 없음.
      - cup_engine/champions_engine의 라운드 일괄 AI 처리는 내 경기를
        원천적으로 제외(is_my=0)하고 있어 해당 없음.
    """
    base_avg = _team_avg_ovr(c, team_id)
    if not p or p.get("current_team_id") != team_id:
        return base_avg
    if p.get("injured"):
        return base_avg
    if _check_suspended(p)[0]:
        return base_avg
    my_ovr = p.get("ovr", 0)
    my_pos = p.get("position", "")
    if not my_pos or not my_ovr:
        return base_avg
    c.execute("""SELECT ovr FROM ai_players WHERE team_id=? AND position=?
                 ORDER BY ovr DESC LIMIT 1""", (team_id, my_pos))
    row = c.fetchone()
    if not row or my_ovr <= row["ovr"]:
        return base_avg   # 그 자리 기존 주전보다 낫지 않으면 그대로
    n_row = c.execute("SELECT COUNT(*) AS n FROM ai_players WHERE team_id=?",
                       (team_id,)).fetchone()
    n = n_row["n"] if n_row else 0
    if not n:
        return base_avg
    return base_avg + (my_ovr - row["ovr"]) / n


# ── 리그 평균 OVR 캐시 ─────────────────────────────────────────
# 한 리그 전체 ai_players의 평균 OVR. ai_players는 진행 중 안 바뀌므로 상수.
# (_league_ovr_cache 선언은 파일 상단 _team_ovr_cache 옆에 있음)

def _league_avg_ovr(c, league_id):
    if not league_id:
        return 50.0
    cached = _league_ovr_cache.get(league_id)
    if cached is not None:
        return cached
    c.execute("""SELECT AVG(ap.ovr) as v FROM ai_players ap
                 JOIN teams t ON ap.team_id=t.id WHERE t.league_id=?""", (league_id,))
    row = c.fetchone()
    val = row["v"] if row and row["v"] else 50.0
    _league_ovr_cache[league_id] = val
    return val


# ══════════════════════════════════════════════════════════════
# [경기력] OVR-리그격차 지배력 시스템 (튜닝 상수는 여기 모음)
#   내 OVR이 리그 평균보다 높을수록 개인 활약(골/어시/무실점)이 폭발하고,
#   낮으면 위축된다. 14경기 풀리그 기준으로 밸런싱됨.
#   - 황희찬급(85) @ 약체리그(평균50, 격차+35): ST 약 11~12골 (압도적 득점왕)
#   - 황희찬급(85) @ 강팀리그(평균82, 격차+3):   ST 약 5~6골 (평범한 주전)
#   - 언더독(격차 음수): 활약 위축
# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
# 경기 퍼포먼스 시스템 v3 상수
# 설계: 14경기 기준, OVR 비선형 가속, 포지션별 Base 평점 차등
# ══════════════════════════════════════════════════════════════
DOMINANCE_K   = 0.040   # 선형 기본 증가폭
DOMINANCE_MIN = 0.30    # 최저 배수 (강한 리그 위축 하한)
# [2026-07 재조정, 신민용 지적: "OVR100(전성기 메시/홀란드급)이 K1리그에서
# 경기당 2골 수준은 나와야 한다"] 기존 소프트캡(2.20)은 OVR100 vs K1평균
# 상황에서 dom≈2.92로 눌려 시즌 67골(경기당 1.76골) 정도에 그쳤다.
# 소프트캡 자체를 2.20→2.60으로, 완화계수를 0.6→0.75로 올려 dom≈3.38
# (시즌 78골, 경기당 2.05골)이 나오게 했다. 소프트캡에 아예 안 걸리는
# 중간급 선수(gap이 크지 않은 경우)는 이 조정의 영향을 전혀 안 받는다 —
# 극단적 격차 구간(elite OVR가 하위 리그를 압도하는 경우)만 조정된 것.
DOMINANCE_SOFTCAP = 2.60  # 소프트캡 기준점 (이 값까진 그대로, 넘으면 완만하게만 더 증가)
# [2026-07 재조정, 신민용 지적: "OVR100(전성기 메시/홀란드급)이 K1리그
# 수준에서 경기당 1.9~2.1골 정도는 나와야 한다"] 실측 몬테카를로 시뮬레이션
# 돌려보니, DOMINANCE_SOFTCAP을 아무리 올려도 실제 골 수는 전혀 안 바뀌었다
# — dom이 이미 3.0+로 충분히 높아서 gprob/xg 계산값이 이 상한(GOAL_PROB_CAP
# 0.72, ASSIST_PROB_CAP 0.60)을 훨씬 뛰어넘는데, 정작 상한 자체가 낮아서
# 거기서 눌리고 있었다(진짜 병목은 여기였음). 상한을 올려서 재검증:
#   GOAL_PROB_CAP=0.90 → K1 평균 2.00골/경기(시즌 76골), 탑리그 평균
#   1.47골/경기(시즌 56골) — 두 기준점(신민용 제시: K1 1.9~2.1 / 탑리그
#   1.3 안팎)에 근접.
# 중간급 선수는 gprob/aprob 계산값 자체가 이 상한 근처에도 안 가므로
# (라이벌급 극소수 초엘리트만 상한에 걸림) 이 조정의 영향을 거의 안 받는다.
GOAL_PROB_CAP = 0.90    # 경기당 골 확률 상한 — 탑 ST(OVR100급)까지 고려해 상향
ASSIST_PROB_CAP = 0.85  # 경기당 어시 확률 상한 — 탑 윙어/미드필더까지 고려해 상향

def _dominance_mult(my_ovr, league_avg):
    """OVR vs 리그 평균 → 활약 배수 (v4 — 절대실력 프리미엄 + 소프트캡).

    [2026-07 재설계, 신민용 지적] "같은 격차라도 낮은 구간(30대에서 40대를
    상대)보다 높은 구간(70대에서 80대를 상대)이 실질적으로 더 압도적이어야
    한다" — 40대 재능은 그냥 평범한 수준이라 그 안에서 잘하는 거지만, 80대는
    이미 프로 중에서도 상위권이라 같은 격차라도 담긴 실력 차이가 훨씬 크다.
    구버전은 gap(격차)만 보고 my_ovr(절대 수준) 자체는 90 이상에서만
    반영했어서, OVR40이 평균30을 상대하나 OVR80이 평균70을 상대하나
    똑같이 dom=1.4가 나오는 문제가 있었다(실측 확인됨).

    [버그수정] 구버전 docstring엔 "OVR99@K1(격차17)→2.20, OVR99@EPL(격차6)→
    1.98"로 서로 다르게 나온다고 적혀 있었지만, 실제 코드의 elite 가속식은
    OVR99에서 +1.20을 만들어내 문서의 +0.74와 안 맞았다 — 그 결과 두 케이스
    다 하드캡(2.20)에 뭉개져 완전히 똑같은 값이 나오고 있었다(리그 차이가
    사라짐). 이번에 하드캡을 소프트캡으로 바꿔 이 문제도 같이 해결했다 —
    격차가 크면 클수록(가령 90대 선수가 평균30 리그에 있을 때) 상한 근처에서도
    계속 차등되게 늘어난다("30에서 90은 프리미어리그급이 아프리카 하위리그에서
    양학하는 수준"이라 gap30짜리와는 확실히 구분돼야 한다는 지적 반영).

    설계 (14경기 기준):
      리그 평균(gap0): dom=1.0 → ST 7~10골
      같은 gap10이라도 OVR40(평균30 상대)=1.40, OVR80(평균70 상대)=1.47
      OVR90 @ 평균30(gap60) ≈ 3.0, OVR60 @ 평균30(gap30) = 2.20 — 확실히 차등
      OVR99 @ K1(평균82) ≈ 2.70, OVR99 @ EPL(평균93) ≈ 2.40 — 여전히 리그별 차등
    """
    my_ovr = my_ovr or 50
    lg_avg = league_avg or 50
    gap = my_ovr - lg_avg
    base = 1.0 + gap * DOMINANCE_K          # 선형 기반 (약체 쪽/gap<=0은 그대로)

    # [신설] 절대실력 프리미엄 — 내가 우위(gap>0)일 때만, my_ovr 자체가
    # 60을 넘어서면서 격차의 위력을 키운다. 60 이하는 보정 없음(기존과
    # 완전히 동일), 80~90 부근에서 뚜렷해진다. 90 이상은 아래 elite 가속이
    # 이어받아 한 번 더 강조한다.
    if gap > 0:
        talent_mult = 1.0 + max(0.0, (my_ovr - 60) / 40.0) ** 1.6 * 0.55
        base = 1.0 + gap * DOMINANCE_K * talent_mult

    # OVR 90~94: 완만한 가속
    if my_ovr >= 90:
        elite = (my_ovr - 90) ** 1.9 * 0.012
    else:
        elite = 0.0

    # OVR 95~100: 체감형 추가 가속
    if my_ovr >= 95:
        elite += (my_ovr - 95) ** 1.5 * 0.0525

    base += elite

    # [신설] 하드컷 대신 소프트캡 — 상한(2.20) 근처에서도 완만히 계속
    # 늘어나게 해서, gap30짜리와 gap60짜리가 똑같이 뭉개지지 않고 계속
    # 차등된다. _soft_cap()과 동일한 형태(지수감쇠)를 여기 전용 계수로 적용.
    if base > DOMINANCE_SOFTCAP:
        over = base - DOMINANCE_SOFTCAP
        base = DOMINANCE_SOFTCAP + DOMINANCE_SOFTCAP * 0.75 * (1 - math.exp(-over / DOMINANCE_SOFTCAP))

    return max(DOMINANCE_MIN, base)

def _stat_n(p, stat, lo=40, hi=95):
    """스탯을 0~1로 정규화 (lo=0, hi=1). 플레이스타일 반영용."""
    v = p.get(stat, 60)
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _my_team_avg_ovr(p):
    """내 소속 팀의 AI 선수 평균 OVR (동료 수준).
    [최적화] _team_ovr_cache 우선 활용 — 세션 내 같은 팀은 캐시로 처리."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return p.get("ovr", 40)
    cached = _team_ovr_cache.get(tid)
    if cached is not None:
        return cached
    conn = get_conn()
    c = conn.cursor()
    try:
        result = _team_avg_ovr(c, tid)  # 캐시에 저장하면서 반환
    finally:
        conn.close()
    return result


def _check_bench(p):
    """OVR 격차(팀 수준 대비)를 주 변수로, 감독 관계로 보정한 벤치 판정.
    [기능1/2] 계약 역할 + 감독 성향으로 추가 보정."""
    rel = p.get("manager_relation", 50)
    team_avg = _my_team_avg_ovr(p)
    gap = team_avg - p.get("ovr", 40)   # +면 내가 팀 수준에 못 미침

    base = 0.90
    for thr, prob in BENCH_BY_GAP:
        if gap <= thr:
            base = prob
            break

    # 감독 관계로 보정
    if rel >= 70:
        base *= 0.7
    elif rel < 30:
        base *= 1.3

    # [기능1] 계약 역할 보정
    from constants import OFFER_ROLES, MANAGER_TYPES
    role = p.get("contract_role", "주전 경쟁")
    base *= OFFER_ROLES.get(role, {}).get("bench_mult", 1.0)

    # [기능2] 감독 성향 보정
    mt = MANAGER_TYPES.get(p.get("manager_type", "베테랑 신뢰"))
    if mt:
        base *= mt["bench_mult"]
        # 유스 중시: 나이 이하면 추가 완화 / 베테랑 신뢰: 어리면 불리
        yp = mt.get("youth_pref_age", 0)
        age = p.get("age", 25)
        if yp > 0 and age <= yp:
            base *= 0.75
        elif yp == -1 and age <= 22:
            base *= 1.15

    return random.random() < min(0.95, max(0.0, base))


# ══════════════════════════════════════════════════════════════
# [2026-07 신설] 퇴장(레드카드) → 다음 경기 자동 결장(출전정지) 시스템.
# '폭력적' 성격의 red_card_chance는 정의만 돼있고 실제 경기엔 연결이
# 안 돼있었다 — 이번에 실제 퇴장 처리(그 경기 조기 강판 취급) + 다음 경기
# 강제 결장까지 구현한다. 리그/챔스/컵/국제대회 4개 대회 공통으로 이
# 함수들을 재사용한다(다른 통일 작업들과 동일한 방식).
# ══════════════════════════════════════════════════════════════
BASE_RED_CARD_CHANCE = 0.012   # 평균적인 선수의 경기당 기본 퇴장 확률 (약 1/80경기)
RED_CARD_RATING = 3.2          # 퇴장 시 그 경기 평점은 고정(활약 무관 — 조기 강판이라 활약을 못 냄)


def _roll_red_card(p):
    """이번 경기에 퇴장이 발생하는지 판정. '폭력적' 성격이면 기본 확률에
    PERSONALITY_EFFECTS의 red_card_chance만큼 가산된다."""
    pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    chance = BASE_RED_CARD_CHANCE + pe.get("red_card_chance", 0.0)
    return random.random() < chance


def _check_suspended(p, field="red_card_suspension"):
    """출전정지 중이면 True를 반환하며 남은 정지 경기 수를 1 차감한다
    (DB에는 이 함수를 호출한 쪽이 update_player로 반영해야 함 — 여기서는
    판정 + 차감된 값 계산까지만 하고 실제 UPDATE는 호출부 책임).

    [2026-07 버그수정, 신민용 리포트: "챔스 출전정지가 다음 리그경기에서
    소진됨"] 대회별로 독립된 카운터를 쓰도록 field 파라미터 추가 —
    리그는 red_card_suspension(기본값), 챔스는 cl_suspension, 컵은
    cup_suspension, 국제전(월드컵 등)은 intl_suspension, 클럽월드컵은
    cwc_suspension. 호출부가 자기 대회에 맞는 field를 넘겨야 한다.
    반환: (suspended: bool, new_suspension_count: int)"""
    cur = p.get(field, 0) or 0
    if cur > 0:
        return True, cur - 1
    return False, 0


def _apply_red_card_dismissal(p, field="red_card_suspension"):
    """퇴장 처리: 그 경기 평점을 고정값으로, 골/도움/세이브는 조기 강판
    특성상 0으로 처리하고, 다음 경기(같은 대회 한정) 출전정지 1경기를 건다.
    반환: (goals, assists, saves, rating, events, detail) — _player_perf와
    동일한 반환 형태라 호출부에서 그대로 대입해 쓸 수 있다.

    [2026-08 신설, 신민용 요청: "커리어에 레드카드 기록 추가"] 이 함수가
    리그/컵/챔스/클럽월드컵/국가대표/승강PO 6개 대회 전부에서 퇴장 처리의
    공통 진입점이라, 누적 카운터도 여기 한 곳에서만 올리면 6곳 전부 커버된다.
    total_red_cards_all(커리어 통산, 전 대회 합산)은 어디서 발생하든 항상
    올리고, field가 기본값(red_card_suspension)일 때만 — 즉 "리그" 경기일
    때만 — 리그 전용 카운터(season/total_red_cards_league)도 함께 올린다.
    커리어/은퇴창의 "전체 기록"은 total_red_cards_all을, "리그 기록"은
    total_red_cards_league/career_entries.red_cards를 보여주기 위함."""
    events = [(_sample_minutes(1, 15, 85)[0], "🟥 퇴장! 조기 강판")]
    detail = {"shots": 0, "shots_on": 0, "key_passes": 0,
              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
    _is_league_rc = (field == "red_card_suspension")
    _rc_updates = {field: 1, "total_red_cards_all": p.get("total_red_cards_all", 0) + 1}
    if _is_league_rc:
        _rc_updates["total_red_cards_league"] = p.get("total_red_cards_league", 0) + 1
        _rc_updates["season_red_cards_league"] = p.get("season_red_cards_league", 0) + 1
    update_player(**_rc_updates)
    return 0, 0, 0, RED_CARD_RATING, events, detail


def _gen_score(outcome, diff=0.0):
    """경기 스코어 생성. diff(홈-원정 전력차)가 클수록 이긴 쪽이 크게 이긴다.
    diff는 _simulate_match 에서 계산된 home_ovr-away_ovr (홈 보정 포함).
      - |diff| 0~15   → 박빙/우세: 이겨도 1~2골차가 흔함
      - |diff| 28~42  → 강한 우세/압도: 3~4골차 흔함, 대량득점은 여전히 소수
      - |diff| 58+    → 초압도: 대량득점(5골+)이 뚜렷하게 늘어남
    [2026-07 재조정, 신민용 리포트] 예전 임계값(12/22/35/50)에서는 adv=25
    (그리 크지 않은 격차)만 돼도 5골차가 약 5% 나올 정도로 대량득점이
    너무 잦았다 — 임계값을 전체적으로 올리고 대량득점 가중치를 낮췄다.
    승자/패자는 outcome 으로 이미 정해졌고, 여기선 '몇 대 몇'만 정한다.
    """
    # 전력차 → 이긴 팀의 기대 득점 가중(우세할수록 큰 점수 쪽으로 분포 이동).
    adv = abs(diff)

    # [2026-07 신설, 신민용 리포트: "프랑스가 북아일랜드한테 2-6으로 짐 —
    # 이변인데 대량실점까지 겹침"] adv는 원래 전력차 크기일 뿐, 실제로
    # 누가 이겼는지(outcome)와는 무관하다. 그래서 큰 전력차의 약팀이
    # '이변으로' 이겨도 그대로 압도적 스코어 테이블을 타서 "이변+대량득점"
    # 이라는 이중으로 비현실적인 조합이 나왔다 — 실제 축구에서 이변은
    # 거의 항상 근소한 스코어(1-0, 2-1류)로 끝나지, 이변인데도 4~6골차
    # 대승은 극히 드물다. 언더독이 실제로 이겼다면(diff 부호와 outcome이
    # 반대) 전력차 크기와 무관하게 '박빙' 등급 이하로 강제 완화한다.
    is_upset = (diff > 0 and outcome == "away") or (diff < 0 and outcome == "home")
    if is_upset:
        adv = min(adv, 14)   # '박빙'(adv<15) 등급 테이블로 강제 편입

    if outcome == "draw":
        # 무승부: 전력 비슷할 때 주로 발생하므로 저득점 위주.
        g = random.choices([0, 1, 2, 3], weights=[22, 38, 28, 12])[0]
        return g, g

    # 이긴 팀 득점 분포를 전력차로 조정.
    # [2026-07 재조정, 신민용 리포트: "5등 팀이 2등한테 6-0, 3등한테 5-1로
    # 참패" — 순위가 몇 계단 차이 안 나는 팀끼리도 대량득점이 너무 잦다]
    # 실측: 기존 임계값(adv 22/35/50)에서는 adv=25(그리 크지 않은 격차)
    # 만 돼도 5골차 스코어가 2000경기 중 약 5%, adv=40에서 6골차가 약
    # 8.7% 나왔다 — 순위 몇 계단 차이 정도의 격차로는 너무 자주 대참사가
    # 나는 셈이었다. 임계값을 전체적으로 올리고(12/22/35/50→15/28/42/58)
    # 각 구간의 대량득점 쪽 가중치도 낮춰서, "웬만큼 격차 있어도 대량득점은
    # 여전히 드물고, 진짜 압도적 격차일 때만 자주 나온다"는 쪽으로 재조정.
    # (대승 자체를 없애는 게 아니라 빈도만 낮추는 방향 — 강팀의 압승 서사는
    # 그대로 남아있어야 하므로 초압도 구간은 유지)
    if adv >= 58:        # 초압도 — 드물게 7~9골 이변
        win_goals = random.choices([3, 4, 5, 6, 7, 8, 9],
                                   [18, 28, 24, 16, 9, 4, 1])[0]
        lose_goals = random.choices([0, 1],         [85, 15])[0]
    elif adv >= 42:      # 압도
        win_goals = random.choices([2, 3, 4, 5, 6], [16, 30, 30, 17, 7])[0]
        lose_goals = random.choices([0, 1, 2],      [65, 28, 7])[0]
    elif adv >= 28:      # 강한 우세
        win_goals = random.choices([1, 2, 3, 4, 5], [12, 34, 30, 18, 6])[0]
        lose_goals = random.choices([0, 1, 2],      [52, 36, 12])[0]
    elif adv >= 15:      # 우세
        win_goals = random.choices([1, 2, 3, 4],    [24, 40, 25, 11])[0]
        lose_goals = random.choices([0, 1, 2],      [46, 40, 14])[0]
    else:               # 박빙
        win_goals = random.choices([1, 2, 3, 4],    [40, 37, 17, 6])[0]
        lose_goals = random.choices([0, 1, 2],      [42, 42, 16])[0]
    lose_goals = min(lose_goals, win_goals - 1)  # 이긴 팀이 항상 더 많이

    if outcome == "home":
        return max(1, win_goals), max(0, lose_goals)
    else:  # away
        return max(0, lose_goals), max(1, win_goals)


def _my_result(outcome, is_home):
    if outcome == "draw":
        return "draw"
    return "win" if (outcome=="home")==is_home else "loss"


def _multigoal_banner(goals):
    """다득점 시 표시할 강조 배너. 1골 이하는 배너 없음(None)."""
    return {
        2: "🔥 멀티골 달성!",
        3: "🎩🔥 해트트릭 완성!!",
        4: "🎩🎩 포-골 하울!! (4골)",
        5: "🎩🎩🎩 파이브-골 하울!!! (5골)",
        6: "👑🎩 더블 해트트릭!!! (6골)",
    }.get(goals)


def _min_sortkey(m):
    """이벤트 분 정렬용 실수 키. 전반 추가시간(146~155)은 45.1~45.10으로,
       후반 추가시간(91~100)은 90.1~90.10으로 매핑해 실제 경기 시간순 정렬."""
    if 146 <= m <= 155:
        return 45 + (m - 145) / 100.0
    if 91 <= m <= 100:
        return 90 + (m - 90) / 100.0
    return float(m)


def _fmt_min(m):
    """정렬용 분(정수)을 표시 문자열로. 추가시간은 45+n / 90+n 형식.
       전반 추가시간은 146~155(=45+1~45+10)로 인코딩해 후반 정규시간과 겹치지 않게 한다.
       후반 추가시간은 91~100(=90+1~90+10) — 최근 축구 트렌드(VAR 등)상 후반 추가시간이
       10분까지도 흔히 나오므로 그만큼 지원한다.
       Godot 연동 시에도 이 표기 규칙을 그대로 쓸 수 있다."""
    if 146 <= m <= 155:          # 전반 추가시간 (45+1 ~ 45+10)
        return f"45+{m-145}"
    if 91 <= m <= 100:           # 후반 추가시간 (90+1 ~ 90+10)
        return f"90+{m-90}"
    return str(m)


def _half_of(m):
    """분(정수)이 전반인지 후반인지. 전반 추가시간(146~155)도 전반으로."""
    if 146 <= m <= 155:
        return "first"
    return "first" if m <= 45 else "second"


def _sample_minutes(n, lo, hi, avoid=None, min_gap=3):
    """경기 이벤트 분(分)을 n개 뽑는다. 정렬용 정수 리스트(오름차순) 반환.
       정규시간(lo~hi) 위주이되, 낮은 확률로 추가시간이 섞인다.
       - 전반 추가시간: 146~155 (=45+1~45+10), 짧은 쪽이 흔함
       - 후반 추가시간: 91~100  (=90+1~90+10), 짧은 쪽이 흔하지만 최근 트렌드상
         9~10분까지도 드물게 나올 수 있게 폭을 넓혔다
       정렬 시 146~155는 큰 값이라 맨 뒤로 가지만, _half_of 로 전반에 재배치된다.

       avoid: [버그 수정] 이미 다른(진짜) 이벤트가 있는 분(分)들의 집합.
       min_gap분 이내는 피해서 뽑는다 — 안 그러면 "2' 실점"과 "2' 코너킥이
       걷어내졌다"처럼 서로 무관한 이벤트가 같은/거의 같은 순간에 겹쳐
       배너·장면이 뒤죽박죽 충돌하는 문제가 있었다(득점 배너 뜨자마자
       엉뚱한 코너킥 배너가 겹쳐 뜨는 등)."""
    if n <= 0:
        return []
    avoid_keys = [_min_sortkey(a) for a in (avoid or ())]

    def _too_close(m):
        mk = _min_sortkey(m)
        return any(abs(mk - ak) < min_gap for ak in avoid_keys)

    pool = [x for x in range(lo, min(hi, 90) + 1) if not _too_close(x)]
    if not pool:
        pool = list(range(lo, min(hi, 90) + 1))  # 안전망(avoid가 범위를 다 잡아먹은 극단적 경우)
    # 추가시간: 짧을수록 자주(가중치). 전반은 후반보다 덜 나오게. 9~10분은 아주 드물게.
    fh_stop = [146,146,146, 147,147, 148,148, 149, 150, 151, 152, 153, 154, 155]   # 45+1~10
    sh_stop = [91,91,91,91, 92,92,92, 93,93,93, 94,94, 95,95, 96, 97, 98, 99, 100]  # 90+1~10
    out = set()
    attempts = 0
    while len(out) < n and attempts < n * 25:
        r = random.random()
        if r < 0.04:                       # 전반 추가시간 (드묾)
            cand = random.choice(fh_stop)
        elif r < 0.16:                     # 후반 추가시간 (좀 더 흔함)
            cand = random.choice(sh_stop)
        else:
            cand = random.choice(pool)
        if not _too_close(cand):
            out.add(cand)
        attempts += 1
    while len(out) < n and len(out) < len(pool):
        cand = random.choice(pool)
        if not _too_close(cand):
            out.add(cand)
    return sorted(out)


def _describe_goal(goal_idx, total_goals, minute, my_final, opp_final, dom, exclude=None):
    """내 골 하나의 '맥락'을 추정해 골 묘사 문구를 고른다.
    실제 골 시점의 스코어는 시뮬레이션이 분 단위로 돌지 않아 알 수 없으므로,
    최종 스코어 + 분(分) + 골 순번으로 그럴듯한 종류를 휴리스틱하게 분류한다.
      - 75분 이후 + 1골차 박빙 → 극장골/막판골
      - 1~2골차 승부에서 마지막 골 → 결승골
      - 박빙 접전에서 뒤지다 따라잡는 그림(후반) → 역전골
      - 비기는 스코어 → 동점골 / 첫 골 전반 → 선제골
    exclude: 같은 경기에서 이미 쓴 문구 set (중복 방지).
    대승(3골차 이상)에선 결승/역전/극장 분류를 끈다(어색함 방지).
    """
    exclude = exclude or set()
    margin = my_final - opp_final
    is_last = (goal_idx == total_goals)
    tight = (0 <= margin <= 2)   # 박빙 여부
    real_min = _min_sortkey(minute)        # 실제 경기 시간(전/후반 추가시간 반영)
    is_stoppage = (91 <= minute <= 100)    # 후반 추가시간만 해당

    def pick(key):
        pool = [x for x in GOAL_PHRASES[key] if x not in exclude] or GOAL_PHRASES[key]
        return random.choice(pool)

    # 후반 추가시간 + 박빙 마지막 골 → 극장골 최우선 (90+분의 박진감)
    if is_stoppage and is_last and 0 <= margin <= 1 and my_final >= opp_final:
        return pick("late")
    # 막판 극장골 (박빙 1골차, 실제 78분 이후 마지막 골)
    if real_min >= 78 and is_last and 0 <= margin <= 1 and my_final >= opp_final:
        return pick("late")
    # 결승골 (1~2골차 승리의 마지막 골) — 대승 제외
    if is_last and 1 <= margin <= 2 and my_final > opp_final:
        return pick("winner")
    # 역전골 (박빙 접전 + 후반 + 상대도 득점) — 대승 제외
    if is_last and tight and margin >= 1 and real_min >= 55 and opp_final >= 1:
        return pick("comeback")
    # 동점골 (최종 무승부)
    if margin == 0 and opp_final >= 1:
        return pick("equalizer")
    # 선제골 (첫 골 + 전반)
    if goal_idx == 1 and real_min <= 40:
        return pick("opener")
    return pick("normal")


def _poisson(lam):
    """경기당 활동 횟수(키패스·드리블·차단 등)를 포아송 분포로 뽑는다.
    λ(기대값)는 스탯·포지션·지배력으로 산출된 값. 같은 λ라도 경기마다
    결과가 흔들려(어떤 날 8개, 어떤 날 1개) 시즌 누적에 현실적 분산이 생긴다.
    Knuth 알고리즘. λ가 크면(>30) 비용 절감 위해 정규근사로 대체."""
    if lam <= 0:
        return 0
    if lam > 30:
        # 정규근사 (평균 λ, 분산 λ)
        return max(0, int(round(random.gauss(lam, lam ** 0.5))))
    L = 2.718281828459045 ** (-lam)
    k = 0
    pr = 1.0
    while True:
        k += 1
        pr *= random.random()
        if pr <= L:
            return k - 1


# ── 상대 PK 실점 마킹 ─────────────────────────────────────────
# [신규] "🥅 실점" 텍스트는 지금까지 그 실점이 PK였는지/오픈플레이였는지
# 아무 정보도 없었다. 그래서 match_sim_viewer._detect_style()이 상대의
# PK 득점을 절대 구분 못 하고 항상 "normal"(오픈플레이 빌드업+슛) 씬으로
# 떨어졌다 — "PK인데 그냥 공 잡고 뛰는 것처럼 나온다"는 지적의 원인.
# 팀 스코어(opp_score)는 절대 안 건드리고, 이미 정해진 실점 개수 중
# 일부에 "(PK)" 꼬리표만 붙여서 뷰어가 전용 스팟킥 연출(20명 클리어
# 대형)을 태울 수 있게 한다.
#
# [1단계 — 지금] 실제 축구의 골 대비 PK 비율(대략 15~20%)에 맞춘 고정
# 확률. [2단계 — 추후] 그 경기의 파울/카드 수가 많을수록(=PK가 나올
# 만한 상황 자체가 많았을수록) 확률이 올라가도록 바꿀 예정 — 그때는
# 이 함수에 파울/카드 컨텍스트 인자를 추가하고 아래 고정값 대신 그 값
# 기반 확률을 쓰면 된다. 호출부(_player_perf)는 헬퍼만 호출하므로 이
# 함수 내부만 바꾸면 됨.
_OPP_PK_CONCEDE_PROB = 0.17


def _roll_is_pk_concede():
    """상대의 이번 실점 하나가 PK였는지 확률적으로 결정.
    [2단계 확장 지점] 나중에 그 경기 파울/카드 수를 반영하려면 이 함수에
    인자를 추가하고 _OPP_PK_CONCEDE_PROB 대신 그 값 기반 확률을 쓰면 됨."""
    return random.random() < _OPP_PK_CONCEDE_PROB


# ══════════════════════════════════════════════════════════
# [2026-07 신설] 세부역할(SUB_ROLES)별 골/도움 성향 가중치.
# 지금까지 sub_role은 생성 시 저장만 되고 실제 경기 계산 어디에도
# 반영이 안 되는 장식용 값이었다 — 포지션별 이미 있는 g_base(골 성향)/
# a_base(도움 성향)/g_pos_mult(골 스케일)/sp_goal(세트피스 보너스)에
# 세부역할별 배율/가산을 곱해서 "같은 포지션이라도 역할에 따라
# 골 대 도움 비율이 달라지는" 현실적인 차이를 만든다.
# 값은 ±15~35% 수준의 보수적인 배율 — 포지션 자체의 큰 틀은 유지하되
# 역할 차이가 시즌 누적으로 체감될 정도의 편차만 준다.
# 모듈 레벨에 둬서 _player_perf(내 선수 개별경기)와 _estimate_ai_season
# (AI 시즌추정) 양쪽이 같은 기준표를 공유한다.
_SUB_ROLE_MATCH_MOD = {
    ("ST", "포처"):        {"g_mult": 1.15, "gp_mult": 1.10, "a_mult": 0.75},
    ("ST", "타깃형"):      {"g_mult": 0.90, "a_mult": 1.30, "sp_add": 0.05},
    ("ST", "올라운더"):    {"g_mult": 1.05, "a_mult": 1.05},  # [세분화 2026-07] 극단 없이 균형형
    ("CF", "딥라잉"):      {"g_mult": 0.85, "a_mult": 1.30},
    ("CF", "타깃형"):      {"g_mult": 0.95, "a_mult": 1.15, "sp_add": 0.04},
    ("CF", "폴스나인"):    {"g_mult": 0.70, "a_mult": 1.45},  # [세분화 2026-07] 처지는 9번, 연계 극대화
    ("LW", "인버티드"):    {"g_mult": 1.30, "gp_mult": 1.15, "a_mult": 0.75},
    ("LW", "클래식윙어"):  {"g_mult": 0.75, "a_mult": 1.30, "sp_add": 0.03},
    ("LW", "폴스윙어"):    {"g_mult": 0.80, "a_mult": 1.35},  # [세분화 2026-07] 중앙으로 처지는 윙어
    ("RW", "인버티드"):    {"g_mult": 1.30, "gp_mult": 1.15, "a_mult": 0.75},
    ("RW", "클래식윙어"):  {"g_mult": 0.75, "a_mult": 1.30, "sp_add": 0.03},
    ("RW", "폴스윙어"):    {"g_mult": 0.80, "a_mult": 1.35},
    ("CAM", "섀도우"):     {"g_mult": 1.25, "a_mult": 0.85},
    ("CAM", "클래식"):     {"g_mult": 0.85, "a_mult": 1.20},
    ("CAM", "세컨드스트라이커"): {"g_mult": 1.35, "a_mult": 0.70},  # [세분화 2026-07] 사실상 반쪽 스트라이커
    ("CM", "박스투박스"):  {"g_mult": 1.20, "a_mult": 1.15},
    ("CM", "플레이메이커"): {"g_mult": 0.80, "a_mult": 1.35, "pa_add": 0.03},
    ("CM", "워크호스"):    {"g_mult": 0.95, "a_mult": 1.10},  # [세분화 2026-07] 활동량형, 도움 쪽으로 살짝
    ("CDM", "홀딩"):       {"g_mult": 0.65, "a_mult": 0.70, "blk_mult": 1.20},
    ("CDM", "박스투박스"): {"g_mult": 1.30, "a_mult": 1.30},
    ("CDM", "딥라잉플레이메이커"): {"g_mult": 0.60, "a_mult": 1.50, "pa_add": 0.05},  # [세분화 2026-07] 레지스타형, 패스로 도움 극대화
    ("LB", "공격형"):      {"a_mult": 1.30, "sp_add": 0.02, "blk_mult": 0.90},
    ("LB", "수비형"):      {"a_mult": 0.70, "sp_add": 0.02, "blk_mult": 1.15},
    ("LB", "윙백"):        {"a_mult": 1.55, "sp_add": 0.02, "blk_mult": 0.85},  # [세분화 2026-07] 공격형보다 더 적극적인 오버래핑
    ("RB", "공격형"):      {"a_mult": 1.30, "sp_add": 0.02, "blk_mult": 0.90},
    ("RB", "수비형"):      {"a_mult": 0.70, "sp_add": 0.02, "blk_mult": 1.15},
    ("RB", "윙백"):        {"a_mult": 1.55, "sp_add": 0.02, "blk_mult": 0.85},
    ("CB", "볼플레잉"):    {"a_mult": 1.40, "pa_add": 0.04, "blk_mult": 0.90},
    ("CB", "수비형"):      {"sp_add": 0.02, "pa_add": -0.02, "blk_mult": 1.20},
    ("CB", "리베로"):      {"a_mult": 1.20, "sp_add": 0.01, "pa_add": 0.02, "blk_mult": 1.05},  # [세분화 2026-07] 볼플레잉과 수비형 중간, 커버형
}

# [세분화 2026-07] GK 세부역할별 선방률(_sr_target) 미세 가산.
# 스위퍼킵퍼는 배급/스위핑에 무게가 쏠려 순수 선방은 살짝 낮고,
# 세이브전문형은 반대로 순수 반사신경/포지셔닝에 특화돼 선방률이 가장 높다.
_GK_SUB_ROLE_SR_MOD = {
    "스위퍼킵퍼": -0.015,
    "전통형": 0.0,
    "세이브전문형": 0.02,
}


def _player_perf(p, outcome, is_home, hs, as_, c=None, opp_ovr=None, opp_sot=None):
    """경기 퍼포먼스 계산 v3.
    포지션별 Base 차등 + 활약 가산 구조.
    수비수는 실점 관여 확률 트리거 감점.
    """
    pos       = get_field_pos(p)
    # [2026-08 신설, 신민용 확정: "일단 내 선수만"] 배치 포지션(pos)이 주
    # 포지션과 다르면 POSITION_MISMATCH_PENALTY만큼 실질 OVR을 깎는다.
    # calc_ovr(pos, stats)가 이미 그 포지션 가중치 기준 1차 차이를 반영하고,
    # 여기 절대 차감은 "전문 포지션이 아니다"라는 작은 추가 보정이다.
    # pos==주 포지션이면 페널티 0이라 기존 p["ovr"]과 값이 동일하다(하위호환).
    # AI 선수는 아직 대상이 아님(신민용 확정 — 안전 확인 후 확장 예정),
    # my_player만 이 함수를 거치므로 자동으로 범위가 제한된다.
    _stats_for_ovr = {s: p.get(s, 40) for s in ALL_STATS}
    _ovr_cap = p.get("talent_cap", 100) if p.get("talent_tier") == "god" else 100
    _my_ovr   = calc_positional_ovr(p.get("position", "CM"), _stats_for_ovr, pos, cap=_ovr_cap)
    my_score  = hs if is_home else as_
    opp_score = as_ if is_home else hs
    goals = assists = saves = 0
    events = []
    detail = {"shots": 0, "shots_on": 0, "key_passes": 0,
              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}

    # ── dom 계산 ──────────────────────────────────────────────
    if opp_ovr is not None:
        _lg_avg = opp_ovr
    else:
        _lid = p.get("current_league_id", 0)
        _lg_avg = 50.0
        if _lid:
            try:
                if c is not None:
                    _lg_avg = _league_avg_ovr(c, _lid)
                else:
                    _tc = get_conn()
                    _lg_avg = _league_avg_ovr(_tc.cursor(), _lid)
                    _tc.close()
            except Exception:
                _lg_avg = 50.0
    dom = _dominance_mult(_my_ovr, _lg_avg)

    # ── 실점 타임라인 ─────────────────────────────────────────
    if opp_score > 0:
        for cm in _sample_minutes(opp_score, 2, 90):
            _concede_text = "🥅 실점 (PK)" if _roll_is_pk_concede() else "🥅 실점"
            events.append((cm, _concede_text))

    # ── 스탯 정규화 ──────────────────────────────────────────
    sh  = _stat_n(p, "shooting")
    pa  = _stat_n(p, "passing")
    dr  = _stat_n(p, "dribbling")
    ta  = _stat_n(p, "tackling")
    hd  = _stat_n(p, "heading")
    sp  = _stat_n(p, "setpiece")
    spd = _stat_n(p, "speed")
    pos_s = _stat_n(p, "positioning")
    sta = _stat_n(p, "stamina")

    # ══════════════════════════════════════════════════════════
    # GK 전용 분기
    # ══════════════════════════════════════════════════════════
    if pos == "GK":
        _lid2 = p.get("current_league_id", 0)
        if c is not None:
            _tier = _league_tier(c, _lid2, default=3)
        else:
            _tier = 3
            if _lid2:
                try:
                    _tc2 = get_conn()
                    _tier = _league_tier(_tc2, _lid2, default=3)
                    _tc2.close()
                except Exception:
                    pass

        if _tier == 1:   _sr_min, _sr_max = 0.46, 0.82
        elif _tier == 2: _sr_min, _sr_max = 0.42, 0.78
        else:            _sr_min, _sr_max = 0.38, 0.74

        _gk_ovr = p.get("ovr", 50)
        _ovr_t  = max(0.0, min(1.0, (_gk_ovr - 40) / 45))
        _sr_center = _sr_min + (_sr_max - _sr_min) * _ovr_t
        _sr_center = min(0.92, _sr_center * (0.55 + 0.45 * dom))
        _sr_target = max(_sr_min, min(0.92, _sr_center + random.uniform(-0.04, 0.04)))
        # [세분화 2026-07] GK 세부역할 보정 (스위퍼킵퍼는 살짝 낮음, 세이브전문형은 높음)
        _gk_sr = p.get("sub_role", "") or ""
        _sr_target = max(0.20, min(0.95, _sr_target + _GK_SUB_ROLE_SR_MOD.get(_gk_sr, 0.0)))

        if opp_sot is not None:
            # [2026-07 구조수정, 신민용 GK QA: "평점 모델이 상상하는 SOT가
            # 실제 경기 SOT와 어긋난다(OVR85~90 부근에서 부호 역전, 그 여파로
            # OVR100 평균saves가 OVR80보다 낮아짐)"] 전술엔진(tactical_engine)이
            # 이미 실제로 몇 개의 유효슈팅을 만들었는지 알고 있고, 그 결과가
            # opp_score(실점)까지 확정된 상태로 넘어온다 — "유효슈팅은 반드시
            # 골 아니면 선방"이라는 엔진의 불변식 덕분에, saves는 더 이상
            # _sr_target으로 재추정할 필요 없이 total_shots-opp_score로 정확히
            # 결정된다(추가 확률층을 얹으면 오히려 실제 경기와 다시 어긋나는
            # 이중구조가 재발함). _sr_target/_base_sot 랜덤 테이블은 이 값이
            # 없을 때(폴백 경로)만 쓰는 백업으로 아래에 남겨둔다.
            total_shots = max(opp_score, int(round(opp_sot)))
            saves = max(0, total_shots - opp_score)
            rate  = saves / total_shots if total_shots else 0
            _faced = total_shots
        else:
            if _tier == 1:   _base_sot = random.choices([1,2,3,4,5], [10,24,30,22,14])[0]
            elif _tier == 2: _base_sot = random.choices([1,2,3,4,5], [8,22,30,24,16])[0]
            else:            _base_sot = random.choices([1,2,3,4,5,6],[6,18,26,24,16,10])[0]
            # [2026-07 QA 버그수정, 신민용 리포트: "GK 평점이 OVR85에서 정점
            # 찍고 90~100으로 갈수록 오히려 떨어진다"] 압도적인 팀(dom 高)일
            # 수록 슈팅을 적게 받는 건 현실적이나, expose 하한이 0.55까지
            # 떨어지면서 OVR95~100 골키퍼가 슈팅 자체를 너무 적게 받아
            # `_faced>=3` 보너스 구간(최대 +2.0)에 못 들어가고 "슈팅 적은
            # 경기" 구간(최대 +0.55)에 갇히는 역전 현상이 있었다. 하한을
            # 0.55→0.70으로 완화했다.
            _expose = max(0.70, min(1.7, 1.45 - 0.46 * min(2.2, dom)))
            extra_sot   = max(0, int(round(_base_sot * _expose)))
            total_shots = max(opp_score + 1, opp_score + extra_sot)
            saves = max(0, min(total_shots - opp_score,
                               round(total_shots * _sr_target - opp_score*(1-_sr_target))))
            rate  = saves / total_shots if total_shots else 0
            _faced = total_shots
            # [구조수정 부수효과] sub_role(_GK_SUB_ROLE_SR_MOD)은 원래
            # _sr_target을 통해 saves 카운트 자체에 반영됐는데, opp_sot 경로는
            # saves가 실제 경기로 정확히 결정돼 그 채널이 사라진다. 세이브전문형/
            # 스위퍼킵퍼 색깔을 완전히 지우지 않기 위해, 작은 폭이었던 원래
            # 효과(±0.015~0.02)를 평점에 직접 소폭 반영한다.
            pass
        if opp_sot is not None:
            base_sub_role_nudge = _GK_SUB_ROLE_SR_MOD.get(_gk_sr, 0.0) * 2.5
        else:
            base_sub_role_nudge = 0.0

        # GK 기본 Base 6.20
        base = 6.20 + base_sub_role_nudge
        # 선방 퀄리티 보정
        if _faced >= 3:
            if   rate >= 0.85: base += 1.4 + 0.6*_ovr_t; events.append("🧤 믿을 수 없는 선방쇼!")
            elif rate >= 0.78: base += 0.9 + 0.4*_ovr_t; events.append("🧤 환상적인 선방!")
            elif rate >= 0.70: base += 0.45 + 0.2*_ovr_t; events.append("🧤 안정적인 선방")
            elif rate >= 0.60: base += 0.1
            elif opp_score > 0 and rate < 0.45: base -= 1.0; events.append("😞 불안한 선방...")
            elif opp_score > 0 and rate < 0.55: base -= 0.4
        else:
            # [2026-07 QA 버그수정] 슈팅을 적게 받아도(압도적인 팀 소속)
            # 다 막았으면 그에 걸맞은 보너스를 받아야 한다 — 예전엔
            # saves>=2에서 최대 +0.55뿐이라, "슈팅 5개 중 4개 막은 평범한
            # 경기"(최대 +2.0 구간)보다 "슈팅 2개 중 2개 다 막은 완벽한
            # 경기"가 더 낮게 평가되는 역전이 있었다.
            _rate_lowvol = saves / max(1, _faced)
            if saves >= 2 and _rate_lowvol >= 0.8:
                base += 0.9 + 0.5*_ovr_t; events.append("🧤 안정적인 선방")
            elif saves >= 2:
                base += 0.4 + 0.3*_ovr_t; events.append("🧤 안정적인 선방")
            elif saves == 1:
                base += 0.15 + 0.2*_ovr_t
            elif opp_score == 0:
                base += 0.25*_ovr_t
        if _faced - opp_score >= 5 and rate >= 0.70:
            base += 0.4; events.append("🛡 슈팅 세례를 막아냄")
        # 무실점/대량실점
        if   opp_score == 0: base += 0.15; events.append("🧱 클린시트!")
        elif opp_score >= 5: base -= 1.1;  events.append("😞 대량 실점...")
        elif opp_score >= 4: base -= 0.8;  events.append("😞 대량 실점...")
        elif opp_score >= 3: base -= 0.5;  events.append("😞 다실점...")
        elif opp_score >= 2: base -= 0.2
        # 패스성공률 (빌드업 GK)
        _pa_gk = _stat_n(p, "passing")
        detail["pass_acc"] = round(min(0.97, 0.70 + 0.22*_pa_gk + random.uniform(-0.03,0.03)), 3)
        base += 0.8 * (detail["pass_acc"] - 0.82)
        # 패스성공률 60% 이하 감점
        if detail["pass_acc"] < 0.60:
            base -= 0.15

    # ══════════════════════════════════════════════════════════
    # 필드 플레이어 분기
    # ══════════════════════════════════════════════════════════
    else:
        # ── 포지션별 Base + 계수 설정 ───────────────────────
        # [2026-07 Step2 재보정, 신민용+GPT 실측 기반] "OVR80 선수가 OVR80
        # 환경(gap=0)에서 뛰면 그 포지션의 평균적인 시즌"이 되도록 전
        # 포지션 g_base/g_sh/g_dr(골)·a_base/a_pa/a_dr(어시)를 실측
        # 비율로 축소했다 — 예전엔 ST 19.6골/CM 12.6도움/CB 2.9도움처럼
        # 전 포지션이 목표(ST 11~13골, CM 5~8도움, CB 0~2도움)보다
        # 1.5~3배 높게 나왔다. dom(격차) 배수 구조와 상단 폭발력은 그대로
        # 두고(_dominance_mult 자체는 이미 검증됨) "gap=0일 때의 밑바탕"만
        # 낮췄다 — 그래서 하위리그 지배(gap 있는 경우)는 여전히 폭발적으로
        # 나온다. base(포지션 기본 평점)는 골 기여가 줄어든 만큼 살짝
        # 올려서, gap=0에서도 목표 평점(6.8~7.1)이 나오게 맞췄다.
        if pos in ("ST", "CF"):
            base = 6.40
            g_base, g_sh, g_dr = 0.18, 0.15, 0.03
            a_base, a_pa, a_dr = 0.045, 0.10, 0.045
            g_goal, g_asst     = 1.10, 0.70
            g_pos_mult         = 1.55
            sp_goal            = 0.0
            # 스탯 가중: 슈팅★★★ 포지셔닝★★★ 헤딩★★
            _stat_bonus = 0.10*sh + 0.06*pos_s + 0.04*hd

        elif pos in ("LW", "RW"):
            base = 6.40
            g_base, g_sh, g_dr = 0.13, 0.08, 0.07  # 어시 특화라 골 낮춤
            a_base, a_pa, a_dr = 0.106, 0.153, 0.094
            g_goal, g_asst     = 1.00, 0.80
            g_pos_mult         = 0.85
            sp_goal            = 0.0
            # 드리블★★★ 스피드★★★ 패스★★
            _stat_bonus = 0.08*dr + 0.06*spd + 0.04*pa

        elif pos == "CAM":
            base = 6.45
            g_base, g_sh, g_dr = 0.083, 0.077, 0.036
            a_base, a_pa, a_dr = 0.160, 0.198, 0.086
            g_goal, g_asst     = 0.90, 0.90
            g_pos_mult         = 0.80
            sp_goal            = 0.0
            # 패스★★★ 포지셔닝★★★ 드리블★★
            _stat_bonus = 0.10*pa + 0.06*pos_s + 0.04*dr

        elif pos == "CM":
            base = 6.55
            g_base, g_sh, g_dr = 0.051, 0.056, 0.026
            a_base, a_pa, a_dr = 0.083, 0.134, 0.052
            g_goal, g_asst     = 0.85, 0.80
            g_pos_mult         = 0.55
            sp_goal            = 0.0
            # 패스★★ 태클★★ 스태미나★★
            _stat_bonus = 0.07*pa + 0.05*ta + 0.04*sta

        elif pos == "CDM":
            base = 6.68
            g_base, g_sh, g_dr = 0.017, 0.026, 0.009
            a_base, a_pa, a_dr = 0.038, 0.071, 0.021
            g_goal, g_asst     = 0.80, 0.75
            g_pos_mult         = 0.28
            sp_goal            = 0.0
            # 태클★★★ 포지셔닝★★★ 스태미나★★
            _stat_bonus = 0.10*ta + 0.07*pos_s + 0.04*sta

        elif pos in ("LB", "RB"):
            base = 6.55
            g_base, g_sh, g_dr = 0.019, 0.019, 0.019
            a_base, a_pa, a_dr = 0.072, 0.114, 0.062
            g_goal, g_asst     = 0.80, 0.80
            g_pos_mult         = 0.30
            sp_goal            = 0.018  # 오버래핑 크로스
            # 스피드★★ 태클★★ 패스★★
            _stat_bonus = 0.07*spd + 0.06*ta + 0.05*pa

        else:  # CB
            base = 6.65
            g_base, g_sh, g_dr = 0.003, 0.005, 0.0015
            a_base, a_pa, a_dr = 0.007, 0.021, 0.007
            g_goal, g_asst     = 0.80, 0.70
            g_pos_mult         = 0.22
            sp_goal            = 0.018  # 코너킥 헤더
            # 태클★★★ 포지셔닝★★★ 헤딩★★★ 스피드★★
            _stat_bonus = 0.10*ta + 0.08*pos_s + 0.06*hd + 0.04*spd

        # 스탯 보너스 반영 (dom과 무관한 개인 기량 보정)
        base += _stat_bonus * 0.5   # 최대 약 +0.2 수준으로 제한

        # [2026-07 신설] 세부역할 가중치 적용 — 위 포지션별 기본값 위에
        # sub_role(예: 인버티드/클래식윙어)에 맞는 배율만 곱한다. 세부역할이
        # 없거나(구버전 세이브) 매핑에 없는 조합이면 배율 전부 1.0(무보정).
        _sr = p.get("sub_role", "") or ""
        _mod = _SUB_ROLE_MATCH_MOD.get((pos, _sr))
        if _mod:
            g_base   *= _mod.get("g_mult", 1.0)
            a_base   *= _mod.get("a_mult", 1.0)
            g_pos_mult *= _mod.get("gp_mult", 1.0)
            sp_goal  += _mod.get("sp_add", 0.0)

        # [2026-07 재설계, 신민용+GPT 리포트: "OVR90 선수가 약체 상대로
        # 뛰면 평균 9.24점, 강한 상대로 뛰면 7.32점 — 난이도 보정이
        # 거꾸로다"] 예전엔 dom(내 OVR vs 상대 OVR 격차)이 클수록(=상대가
        # 약할수록) base에 큰 가산을 줬다 — 이건 "약체를 압도한다"는
        # 사실 자체를 평점 보상 대상으로 삼은 설계였는데, 현실 축구
        # 평점은 반대다("난이도가 낮은 승리는 당연한 결과, 난이도가 높은
        # 활약이 값지다"). 골/어시 자체는 dom이 커지면 이미 자연스럽게
        # 더 많이 나오므로(gprob/aprob가 dom에 비례) 그 보상은 이미
        # 충분하다 — 여기 있던 "추가 base 가산"만 방향을 정리한다.
        # 실측 검증 목표(OVR90 기준): 약체(OVR60) 7.8~8.2, 중간(OVR75)
        # 7.3~7.7, 강팀(OVR88) 7.0~7.5 — dom>=1.0(약체 상대) 쪽 계수를
        # 1.70→0.35로 크게 압축했다(강팀 상대 쪽은 이미 실측이 목표
        # 범위 안이라 그대로 둠). 지수도 0.55→0.45로 낮춰 완만하게.
        if dom >= 1.0:
            base += 0.35 * ((dom - 1.0) ** 0.45)  # 약체 상대: 최소한의 가산만
        else:
            base += 1.40 * (dom - 1.0)   # 강팀 상대 고전: 기존 유지(이미 목표 범위 안)
        # ── gprob / aprob 계산 ───────────────────────────────
        _gdom_exp = 0.45 + 0.28 * (g_pos_mult / 1.55)
        _gdom = dom ** _gdom_exp
        _pos_goal_scale = g_pos_mult / 1.55
        _weak_bonus = 0.25 * max(0.0, dom - 1.2) * _pos_goal_scale
        _adom_exp = 0.35 + 0.20 * (g_pos_mult / 1.55)
        _adom = dom ** _adom_exp
        # OVR 95+ gprob cap 완화
        # gprob: GOAL_PROB_CAP 고정 (월클 폭발은 xg 멀티골로 반영)
        _gprob_cap = GOAL_PROB_CAP
        gprob = min(_gprob_cap, (g_base + sh*g_sh + dr*g_dr)*_gdom + _weak_bonus)

        aprob = min(ASSIST_PROB_CAP, (a_base + pa*a_pa + dr*a_dr)*_adom + 0.4*_weak_bonus)
        # ── 골 판정 ──────────────────────────────────────────
        got_goal = False
        if my_score > 0 and random.random() < gprob:
            sh_dom = 1.0 + 0.90*((max(1.0,dom)-1.0)**0.58)*(0.35+0.65*_pos_goal_scale)
            xg = g_pos_mult * (0.35 + 0.65*sh) * sh_dom
            # [2026-07 Step4 재설계, 신민용 리포트: "90→95 구간에서 폭발하고
            # 95→100은 거의 안 늘어난다"] 예전엔 이 보너스가 sh(정규화
            # 스탯, _stat_n의 hi=95 상한 때문에 OVR95에서 이미 1.0으로
            # 포화)에 묶여있어서, 딱 그 지점에서 절벽처럼 터지고 그 위로는
            # 더 이상 못 늘었다(실측: ST OVR90→95골55.9, 95→100골58.4).
            # sh 대신 원본 OVR(_my_ovr, 100까지 안 잘림)로 갈아서
            # 85→100까지 완만하게 램프업되게 했다 — _dominance_mult의
            # 90+/95+ 단계적 가속과 같은 방향(같은 신호에 반응)으로 통일.
            _elite_frac = max(0.0, min(1.0, (_my_ovr - 85) / 15.0))
            if _elite_frac > 0:
                xg += g_pos_mult * 0.30 * _elite_frac ** 1.6
            xg = max(0.5, min(4.5, xg))   # 한 경기 최대 4~5골 수준
            goals = 1
            decay = 0.48 + 0.22 * max(0.0, sh - 0.55) / 0.45
            ep = max(0.0, min(0.92, (xg - 0.65) / 3.0))
            while goals < 7 and random.random() < ep:
                goals += 1; ep *= decay
            goals = min(goals, my_score)
            base += goals * g_goal
            got_goal = True
            goal_mins = _sample_minutes(goals, 3, 90)
            _used_txt = set()
            for gi, gm in enumerate(goal_mins):
                ev = _describe_goal(gi+1, goals, gm, my_score, opp_score, dom, exclude=_used_txt)
                _used_txt.add(ev); events.append((gm, ev))
            banner = _multigoal_banner(goals)
            if banner: events.append((goal_mins[-1], banner))

        # 세트피스 보너스 골 (LB/RB/CB 크로스/헤더, 일반 키커)
        _sp_prob = sp_goal + (0.05*sp if sp > 0.55 else 0.0)
        _sp_prob *= min(1.5, dom)
        if my_score > goals and _sp_prob > 0 and random.random() < _sp_prob:
            goals += 1; base += g_goal
            ev_sp = "🎯 세트피스 골!" if pos in ("CB","LB","RB") else "🎯 환상적인 세트피스 골!"
            events.append((random.randint(10,88), ev_sp))
        # ── 페널티킥 (PK) 판정 ──────────────────────────────────
        # PK 획득: 공격수/드리블러가 박스 안 침투 시 파울 유도
        # 14경기 기준 PK 획득 기대: ST 약 0.8회, LW/RW 0.5회, CAM 0.3회
        _pk_base = {"ST":0.06,"CF":0.06,"LW":0.05,"RW":0.05,"CAM":0.03}.get(pos, 0.01)
        _pk_prob = _pk_base * (0.5 + 0.5*dr) * min(1.4, dom)   # 드리블 높을수록, dom 높을수록 PK↑
        if my_score > goals and random.random() < _pk_prob:
            # PK 성공률: 슈팅 스탯 기반 (고스탯 = 90%+)
            _pk_success_rate = 0.65 + 0.30 * sh   # sh=0.5→80%, sh=1.0→95%
            _pk_min = random.randint(15, 88)
            if random.random() < _pk_success_rate:
                goals += 1; base += g_goal * 0.85   # PK 골 — 일반 골보다 약간 낮은 가산
                events.append((_pk_min, "🎯 페널티킥 골!"))
            else:
                base -= 0.50   # 실축 패널티
                events.append((_pk_min, "😤 페널티킥 실축..."))


        # ── 어시 판정 ────────────────────────────────────────────
        # [설계] 어시는 독립 판정 → 단 실제 어시 수는 (my_score - goals) 이내로 cap
        # "팀이 1골 넣고 내가 1골 넣었으면" assist_cap=0 → 어시 0
        # "팀이 3골 넣고 내가 1골 넣었으면" assist_cap=2 → 어시 최대 2
        # [수정] 어시 확률은 팀 기대 득점(aprob에 이미 반영)으로 독립 판정
        # cap=0이어도 aprob 판정은 하되, 결과가 나왔을 때 cap으로 자름
        # [2026-07 재조정, 신민용 지적: "RW가 골은 52인데 어시가 6이면
        # 너무 적은 거 아니냐 — 크로스가 주 역할인데" → "현실적으로 가자"]
        # 예전엔 골을 넣은 경기면 무조건 어시 확률을 0.40배로 깎았는데,
        # 이게 assist_cap(내 골을 뺀 나머지 팀골만 어시 가능)과 겹치면서
        # "잘해서 골을 자주 넣는 선수일수록 어시 낼 기회 자체가 계속
        # 사라지는" 이중 억제가 됐다. 현실은 세부역할에 따라 다르다 —
        # 클래식윙어/폴스윙어/플레이메이커류(a_mult 높음)는 그날 골을
        # 넣었어도 크로스·키패스 본능은 그대로라 어시 페널티를 크게
        # 완화하고, 인버티드/포처류(a_mult 낮음, 골에 특화)는 그날은
        # 확실히 결정력에 집중하는 게 현실적이라 기존 페널티를 유지한다.
        _a_mult_for_penalty = (_mod or {}).get("a_mult", 1.0)
        if got_goal:
            if _a_mult_for_penalty >= 1.15:
                _goal_day_mult = 0.75    # 어시 특화형: 골 넣어도 창작 본능은 그대로
            elif _a_mult_for_penalty <= 0.85:
                _goal_day_mult = 0.40    # 골 특화형: 그날은 결정력에 집중 (기존 유지)
            else:
                _goal_day_mult = 0.55    # 세부역할 없음/중립형: 기존보다 완화
        else:
            _goal_day_mult = 1.0
        eff_aprob = aprob * _goal_day_mult
        if random.random() < eff_aprob:
            _a_base_multi = 0.12 + 0.10*(a_pa / 0.26)
            multi_p = min(0.22, _a_base_multi)
            if pa > 0.78: multi_p += 1.8*(pa - 0.78)**1.3
            multi_p = min(0.52, multi_p)
            _raw_assists = 2 if random.random() < multi_p else 1
            # 실제 어시: (my_score - goals) 상한 적용
            _assist_cap = max(0, my_score - goals)
            assists = min(_raw_assists, _assist_cap)
            if assists > 0:
                base += assists * g_asst
                a_mins = _sample_minutes(assists, 5, 88)
                a_txts = ["🅰 정확한 어시스트!", "🅰 결정적 도움!", "🅰 키패스로 어시스트!",
                          "🅰 환상적인 패스로 어시!", "🅰 침투 패스 어시스트!"]
                for am in a_mins: events.append((am, random.choice(a_txts)))
                if assists >= 2: events.append((a_mins[-1], "🅰🔥 멀티 어시스트!"))
        # ── 수비수 실점 관여 트리거 감점 ────────────────────
        # "팀 실점 × 계수" 통짜 감점 대신 확률 기반 귀책 판정
        if opp_score > 0 and pos in ("CB","CDM","LB","RB"):
            _concede_prob = {"CB":0.45, "CDM":0.30, "LB":0.25, "RB":0.25}.get(pos, 0)
            _concede_pen  = {"CB":0.25, "CDM":0.18, "LB":0.15, "RB":0.15}.get(pos, 0)
            for _ in range(opp_score):
                if random.random() < _concede_prob:
                    base -= _concede_pen
            # 대량실점(3+) 추가 패널티
            if opp_score >= 3:
                base -= 0.20
        # [버그수정 2026-07, 신민용 지적: OVR97 선수가 0-7 대패 경기에서
        # 0골 0도움인데도 평점 8.4가 나온 사례] 위 실점 귀책 감점이
        # CB/CDM/LB/RB에만 있고 공격형 포지션(ST/CF/LW/RW/CAM/CM)엔
        # 대량실점에 대한 어떤 감점도 없었다 — 그래서 dom(내 OVR vs 상대
        # 평균 OVR)만 높으면 팀이 완전히 무너져도 개인 평점은 그 dom
        # 가산만으로 고평점까지 갈 수 있었다. 수비수처럼 실점 하나하나에
        # 확률적으로 귀책시키는 건 안 맞지만(공격수가 실점에 직접 관여할
        # 일은 적음), "팀 전체가 대패했다"는 경기 맥락 자체는 포지션
        # 무관하게 평점에 소폭 반영되는 게 맞다 — 실제 축구도 대참패
        # 경기에서 개인이 아무리 잘해도 8점대까진 잘 안 나온다.
        elif opp_score >= 3 and pos not in ("CB","CDM","LB","RB"):
            if   opp_score >= 6: base -= 0.55
            elif opp_score >= 5: base -= 0.35
            elif opp_score >= 4: base -= 0.20
            else:                base -= 0.10

        # ── 패스성공률 60% 이하 감점 ─────────────────────────
        # (아래 세부지표 계산 후 적용)

        # ── 세부 지표 ────────────────────────────────────────
        po = _stat_n(p, "positioning")
        _att_dom = 1.0 + 0.72*((max(1.0,dom)-1.0)**0.62)

        if pos in ("ST","CF"):
            shot_w, key_w, drb_w, blk_w = 3.2, 1.0, 1.4, 0.18
        elif pos in ("LW","RW"):
            shot_w, key_w, drb_w, blk_w = 2.4, 1.6, 2.6, 0.30
        elif pos == "CAM":
            shot_w, key_w, drb_w, blk_w = 1.8, 2.6, 1.8, 0.42
        elif pos == "CM":
            shot_w, key_w, drb_w, blk_w = 1.2, 2.0, 1.2, 0.70
        elif pos == "CDM":
            shot_w, key_w, drb_w, blk_w = 0.6, 1.2, 0.8, 1.20
        elif pos in ("LB","RB"):
            shot_w, key_w, drb_w, blk_w = 0.5, 1.3, 1.2, 1.00
        else:  # CB
            shot_w, key_w, drb_w, blk_w = 0.3, 0.5, 0.4, 1.22
        shots = int(round(shot_w * 0.72 * (0.4+0.6*sh) * _att_dom + random.uniform(0,0.7)))
        shots = max(goals, shots)
        on_ratio = 0.28 + 0.16*sh
        shots_on = int(round(shots * on_ratio + random.uniform(0,0.4)))
        shots_on = max(min(shots,goals), min(shots,shots_on))
        _kp_lambda = key_w * 0.66 * (0.35+0.4*pa+0.25*dr) * _att_dom
        key_passes = _poisson(_kp_lambda)
        key_passes = max(assists, key_passes)
        _drb_lambda = drb_w * 0.78 * (0.4+0.45*dr+0.15*spd) * _att_dom
        dribbles = _poisson(_drb_lambda)
        # 차단은 강팀 상대(dom 낮음)일수록 더 많이 발생 (상대 공격이 빈번)
        # 약체(dom 높음)는 오히려 차단 기회 줄어듦
        _blk_dom = max(0.60, 1.15 - 0.35 * min(1.5, max(0.0, dom - 0.5)))
        _blk_lambda = blk_w * (0.30+0.90*ta+0.45*po) * _blk_dom
        _blk_lambda *= _mod.get("blk_mult", 1.0) if _mod else 1.0
        blocks = _poisson(_blk_lambda)

        # 패스성공률
        _lg_pass_adj = max(-0.09, min(0.02, (_lg_avg - 78.0) * 0.004))
        _pa_floor = 0.72 if pos in ("CB","LB","RB","CDM","CM") else 0.66
        pass_acc = _pa_floor + 0.16*pa + 0.04*(dom-1.0) + _lg_pass_adj \
                   + (_mod.get("pa_add", 0.0) if _mod else 0.0) \
                   + random.uniform(-0.025, 0.025)
        pass_acc = max(0.55, min(0.96, pass_acc))
        if pass_acc < 0.60:
            base -= 0.15

        detail["shots"]      = shots
        detail["shots_on"]   = shots_on
        detail["key_passes"] = key_passes
        detail["dribbles"]   = dribbles
        detail["blocks"]     = blocks
        detail["pass_acc"]   = round(pass_acc, 3)

        # ── 득점으로 이어지지 않은 슈팅 장면 ──────────────────
        #   기존엔 detail["shots"]=8, detail["shots_on"]=3 처럼 스탯만 쌓이고
        #   실제 미해결 슈팅 시도는 이벤트로 하나도 안 남아, 재생 화면에서는
        #   골(또는 PK 실축) 말고는 아무 장면도 안 나왔다.
        #   [수정] "슈팅 11"인데 장면은 1~2개만 나오는 건 스탯-영상 불일치라서
        #   대표 몇 개만 뽑던 걸 없애고, 득점 안 된 슈팅 시도 수만큼 전부
        #   장면화한다(shots - goals개).
        #   (🚫 마커 → ui/match_sim_viewer.py의 _MISS_MARKERS가 인식해
        #    '내 팀 공격 시도, 득점 실패' 장면으로 재생함)
        if pos != "GK" and shots > goals:
            _miss_n = shots - goals
            for _mm in _sample_minutes(_miss_n, 3, 89):
                if random.random() < 0.5:
                    events.append((_mm, "🚫 슈팅이 골대를 살짝 빗나갔다"))
                else:
                    events.append((_mm, "🚫 상대 골키퍼 선방에 막혔다"))

        # ── 수비 라인 무실점 보너스 ──────────────────────────
        # [2026-07 Step3 재조정, 신민용+GPT: "CB가 골 없이도 OVR90급
        # 답게 7점대 중후반을 유지해야 한다"] 실측해보니 CB의 gap 기준
        # 평점 곡선이 65~90 구간은 이미 목표에 거의 정확히 맞았는데(오차
        # 0.01~0.04), 상단(OVR95/100)만 목표(7.5~7.9/7.6~8.0)보다
        # 0.10~0.18 부족했다 — 골 기반 엘리트 폭발(Step4)에 대응하는
        # 비득점 지표 쪽 상한이 약해서다. 계수를 0.20→0.28로 올렸다.
        if pos in ("CB","LB","RB","CDM") and opp_score == 0:
            _def_dom = max(0.5, min(1.5, dom))
            base += 0.34 * _def_dom

        # ── 차단 활약 보너스 (CDM/CB 전용) ──────────────────
        # [2026-07 Step3 재조정] 위와 같은 이유로 0.15→0.22.
        if pos in ("CDM","CB") and blocks >= 3:
            base += 0.27 * min(2.0, blocks / 3.0)
            if blocks >= 5: events.append((_sample_minutes(1, 10, 85)[0], "💪 압도적인 수비 활약!"))

        # ── 포지션별 주요 활약 타임라인 이벤트 ────────────────
        # 골/어시 외 차단·키패스·드리블·선방 등을 분 타임스탬프와 함께 기록
        # 너무 많으면 타임라인이 지저분해지므로 포지션별 핵심 지표 1~2개만
        if pos in ("CB", "CDM"):
            # 차단: 3개 이상이면 하이라이트 이벤트
            if blocks >= 5:
                for _bm in _sample_minutes(min(3, blocks//2), 10, 85):
                    events.append((_bm, "🛡 결정적 차단!"))
            elif blocks >= 3:
                events.append((_sample_minutes(1, 10, 85)[0], "🛡 중요한 차단"))
            # 패스성공률 낮으면 부정적 이벤트
            if pass_acc < 0.65 and opp_score >= 2:
                events.append((random.randint(30, 80), "⚠ 패스 미스"))

        elif pos in ("LB", "RB"):
            # 어시 없어도 키패스 많으면 기록
            if key_passes >= 3 and assists == 0:
                events.append((_sample_minutes(1, 20, 80)[0], "🎯 키패스 찬스 창출"))
            if blocks >= 3:
                events.append((_sample_minutes(1, 10, 85)[0], "🛡 오버래핑 후 귀환 차단"))

        elif pos in ("LW", "RW"):
            # 드리블 성공 많으면 기록
            if dribbles >= 5:
                for _dm in _sample_minutes(min(2, dribbles//3), 15, 80):
                    events.append((_dm, "🌪 드리블 돌파!"))
            elif dribbles >= 3:
                events.append((_sample_minutes(1, 15, 80)[0], "↗ 드리블 침투"))

        elif pos == "CAM":
            # 키패스 3개 이상이면 기록
            if key_passes >= 4:
                for _km in _sample_minutes(min(2, key_passes//2), 20, 75):
                    events.append((_km, "🔑 결정적 키패스!"))
            elif key_passes >= 2:
                events.append((_sample_minutes(1, 20, 75)[0], "🔑 기회 창출"))

        elif pos == "CM":
            # 키패스+차단 균형 활약
            if key_passes >= 3:
                events.append((_sample_minutes(1, 20, 70)[0], "🔑 전방 연결 패스"))
            if blocks >= 3:
                events.append((_sample_minutes(1, 25, 80)[0], "🛡 미드 차단"))

        elif pos in ("ST", "CF"):
            # 슈팅 많은데 골 없으면 부정적
            if shots >= 4 and goals == 0:
                events.append((_sample_minutes(1, 30, 80)[0], "😤 결정력 부재"))
            elif shots_on >= 3 and goals == 0:
                events.append((_sample_minutes(1, 30, 80)[0], "😤 유효슈팅 불운"))

    # ── 최종 평점 클램프 ──────────────────────────────────────
    # [2026-07 신설] PERSONALITY_EFFECTS에 정의만 돼있고 실제 경기엔
    # 연결이 안 돼있던 losing_rating("승부욕": 밀리는 상황에서 오히려
    # 분발)을 여기서 반영한다 — GK/필드플레이어 공통 경로라 한 곳만 고치면
    # 포지션 상관없이 다 적용된다. OVR 기반 base 위에 얹는 아주 작은
    # 가산이라 OVR의 절대적 비중은 그대로 유지된다.
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if my_score < opp_score and "losing_rating" in _pe:
        base += _pe["losing_rating"]

    _raw_rating = base + random.uniform(-0.15, 0.15)
    # [2026-07 버그수정, 신민용 리포트: "챔스 평점이 9.5/10.0이 너무
    # 흔하다 — 현실에선 10점은 시즌에 한두 번 나오는 수준"] 실측(OVR100
    # CM vs 평균OVR88 상대)해보니 10.0이 30%, 9.5가 37%로 찍혔다 — 원인은
    # 압도적인 선수의 base가 이미 9.3~10을 넘나드는데 노이즈가 ±0.15로
    # 좁아서, "그냥 좋은 경기"와 "역대급 경기"가 똑같이 하드캡(10.0)에
    # 뭉개져버렸기 때문. base가 9.3을 넘는 초과분을 압축(soft-cap)해서,
    # 9.5~10.0을 받으려면 노이즈 없이도 base가 원래 훨씬 더 높아야만
    # 하게 만든다 — 압도적 활약(해트트릭 등)은 여전히 10.0에 닿을 수
    # 있지만 "그냥 좋은 챔스 경기"만으로는 더 이상 자동으로 10점이 안 된다.
    _SOFT_CAP_START = 9.3
    _SOFT_CAP_COMPRESS = 0.35
    if _raw_rating > _SOFT_CAP_START:
        _over = _raw_rating - _SOFT_CAP_START
        _raw_rating = _SOFT_CAP_START + _over * _SOFT_CAP_COMPRESS

    rating = max(3.0, min(10.0, round(_raw_rating, 1)))

    return goals, assists, saves, rating, events, detail

def _pos_events(pos, positive):
    # 문구 풀은 constants.MATCH_PHRASES 로 분리(포지션당 8~12개로 확장).
    # 구버전 호환: 풀이 없으면 최소 기본값.
    pair = MATCH_PHRASES.get(pos, (["좋은 플레이"], ["실수"]))
    return pair[0] if positive else pair[1]


def _update_team_rec(c, hid, aid, outcome, hs, as_):
    if outcome == "home":
        c.execute("UPDATE teams SET wins=wins+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (hs,as_,hid))
        c.execute("UPDATE teams SET losses=losses+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (as_,hs,aid))
    elif outcome == "away":
        c.execute("UPDATE teams SET losses=losses+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (hs,as_,hid))
        c.execute("UPDATE teams SET wins=wins+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (as_,hs,aid))
    else:
        c.execute("UPDATE teams SET draws=draws+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (hs,as_,hid))
        c.execute("UPDATE teams SET draws=draws+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (as_,hs,aid))


def _accum_team_rec(deltas: dict, hid, aid, outcome, hs, as_):
    """팀 전적 변경분을 deltas dict에 누적 (DB 접근 없음).
    누적 완료 후 _flush_team_rec(c, deltas)로 일괄 UPDATE.
    deltas: {team_id: [wins, draws, losses, gf, ga]}
    """
    def _get(tid):
        if tid not in deltas:
            deltas[tid] = [0, 0, 0, 0, 0]  # wins,draws,losses,gf,ga
        return deltas[tid]

    hd = _get(hid); ad = _get(aid)
    hd[3] += hs; hd[4] += as_
    ad[3] += as_; ad[4] += hs
    if outcome == "home":
        hd[0] += 1; ad[2] += 1
    elif outcome == "away":
        hd[2] += 1; ad[0] += 1
    else:
        hd[1] += 1; ad[1] += 1


def _flush_team_rec(c, deltas: dict):
    """_accum_team_rec로 누적한 deltas를 executemany 1회로 커밋."""
    if not deltas:
        return
    c.executemany(
        "UPDATE teams SET wins=wins+?,draws=draws+?,losses=losses+?,"
        "goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?",
        [(v[0], v[1], v[2], v[3], v[4], tid) for tid, v in deltas.items()]
    )


def _calc_manager_rel(p, rating, result, played, not_played_penalty=1) -> int:
    """[최적화] 감독 관계 신규값 계산만 (update_player 제거 → 호출자가 통합).

    [2026-07 재수정, 신민용 지적: "부상 때문에 경기랑 겹쳐서 못 나가는
    건 관계가 깎여야 하는 게 맞다 — 내가 말한 부상은 그런 상황이었다"]
    직전 수정에서 부상 결장을 전부 페널티 없음으로 바꿨었는데, 그건 잘못
    짚은 것이었다 — 감독 입장에선 사유가 뭐든 경기에 못 나온 선수는
    똑같이 아쉬운 결장이다. '안 뛰면 -1' 페널티는 부상 여부와 무관하게
    원래대로 복원한다. 다만 '뛰었는데 부상 상태'라는 이유만으로 추가
    감점하던 것(직전 수정에서 없앤 것)은 그대로 유지 — 그건 부상 결장과
    무관한 별개의 이상한 로직이었다.

    [2026-07 추가, 신민용 확정: "챔스/컵/클럽월드컵처럼 클럽 입장에서
    중요한 대회는 결장 페널티가 리그보다 커야 한다 — 반대로 국가대표
    (월드컵/대륙컵)는 클럽 감독과 무관하니 아예 영향 없는 게 맞다"]
    not_played_penalty로 대회별 가중치를 받는다 — 국내리그는 기존대로
    1(기본값), 챔스/컵/클럽월드컵은 호출부에서 2를 넘긴다. 국가대표
    대회는 애초에 이 함수를 호출하지 않는다(intl_engine.py의 부상 AI
    대체 경로는 의도적으로 manager_relation을 안 건드림)."""
    from constants import MANAGER_TYPES
    mt = MANAGER_TYPES.get(p.get("manager_type", "베테랑 신뢰"), {})
    gain_m = mt.get("rel_gain_mult", 1.0)
    loss_m = mt.get("rel_loss_mult", 1.0)

    rel = p.get("manager_relation", 50)
    if not played:
        # round(1*loss_m)이 loss_m=0.5인 감독 성향(뚝심형)에선 파이썬의
        # 반올림 규칙(0.5는 짝수로 반올림) 때문에 round(0.5)=0이 돼서
        # 결장 페널티가 통째로 사라졌다 — max(1, ...)로 최소 1은
        # 항상 깎이게 보장한다.
        rel = max(0, rel - max(1, round(not_played_penalty * loss_m)))
    else:
        if rating >= 7.0:   rel = min(100, rel + round(3 * gain_m))
        elif rating >= 6.0: rel = min(100, rel + round(1 * gain_m))
        elif rating < 5.0:  rel = max(0, rel - round(3 * loss_m))
        if result == "win":    rel = min(100, rel + 1)
        elif result == "loss": rel = max(0, rel - round(1 * loss_m))
    return rel

def _update_manager_rel(p, rating, result, played):
    """하위호환 래퍼 (인트엔진·챔스엔진에서 직접 호출하는 경우 대비)."""
    update_player(manager_relation=_calc_manager_rel(p, rating, result, played))


def _calc_pop(p, goals, assists, rating, grade=None) -> int:
    """[최적화] 인기도 신규값 계산만 반환.
    [2026-07 재설계, 신민용 설계+확정: "인기도 = 최근 화제성"] 리그 등급별
    배수(LEAGUE_POP_MULT)를 곱해서, 같은 골/도움이라도 뛰는 리그 수준에
    따라 실제 화제성이 다르게 반영되도록 한다(EPL 골 > K5 골). grade가
    안 주어지면(호출부 미상/구버전 호환) 배수 1.0(중립)로 처리."""
    from constants import LEAGUE_POP_MULT
    mult = LEAGUE_POP_MULT.get(grade, 1.0)
    pop = p.get("popularity", 0)
    if goals > 0: pop = min(100, pop + goals*2*mult)
    if assists > 0: pop = min(100, pop + 1*mult)
    if rating < 5.0: pop = max(0, pop-1)   # 부진 페널티는 리그 등급과 무관하게 그대로
    return round(pop)

def _update_pop(p, goals, assists, rating):
    """하위호환 래퍼."""
    update_player(popularity=_calc_pop(p, goals, assists, rating))


def _derive_match_stats(is_home, hs, as_, goals, assists, saves, pos, detail, engine_stats=None):
    """[경기 통계] 점유율/슈팅/코너/파울/패스성공률을 만든다.

    [신규] engine_stats가 주어지면(내 경기를 새 전술 엔진으로 시뮬레이션한
    경우) — {"home":{...}, "away":{...}} 형태, 각 항목은
    {"poss","shots","shots_on","corners","fouls"} — 그 실제 시뮬레이션
    결과를 기준값으로 쓴다. 공식으로 사후에 지어내는 게 아니라 실제로
    벌어진 슈팅/코너/파울 횟수라는 뜻. 없으면(폴백 상황 등) 예전처럼
    점유율/스코어 기반 공식으로 만든다.

    설계 원칙 — 순서가 중요하다:
      1. 최종 스코어(hs/as_)와 내 개인 기록(goals/assists/saves/detail)은
         이미 확정된 값이다(_player_perf가 먼저 계산함).
      2. 팀 통계는 그 확정된 값들을 "하한선/기준점" 삼아 역산한다(또는
         engine_stats를 기준 삼는다). 그래서 절대 "내 슈팅 5개인데 팀
         슈팅 3개" 같은 모순이 생기지 않는다.
      3. engine_stats가 없을 때는 random.random()을 전혀 쓰지 않는다 —
         같은 스코어·같은 내 기록이면 항상 같은 통계가 나온다.

    점유율: engine_stats가 있으면 그 값, 없으면 스코어 차이에서 추정.
    슈팅: engine_stats가 있으면 그 값을 베이스로, 내 개인 슈팅 기록을 하한선 보장.
    유효슈팅: 최소한 그 팀이 넣은 골 수만큼은 보장(골은 유효슈팅에서만 나옴).
    코너/파울: engine_stats가 있으면 그 값, 없으면 슈팅·점유율에서 파생.
    패스 성공률: 내 개인 pass_acc를 우리 팀 값의 기준점으로 삼음.
    """
    my_score = hs if is_home else as_
    opp_score = as_ if is_home else hs

    my_eng = (engine_stats or {}).get("home" if is_home else "away")
    opp_eng = (engine_stats or {}).get("away" if is_home else "home")

    if my_eng and opp_eng:
        my_poss = max(30, min(70, my_eng.get("poss", 50)))
        opp_poss = 100 - my_poss
        my_shots = max(detail.get("shots", 0), my_eng.get("shots", 0))
        my_shots_on = max(detail.get("shots_on", 0), my_score, my_eng.get("shots_on", 0))
        my_shots = max(my_shots, my_shots_on)
        opp_shots = max(opp_score, opp_eng.get("shots", 0))
        opp_shots_on = max(opp_score, opp_eng.get("shots_on", 0))
        opp_shots = max(opp_shots, opp_shots_on)
        my_corners = my_eng.get("corners", 0)
        opp_corners = opp_eng.get("corners", 0)
        my_fouls = max(1, my_eng.get("fouls", 0))
        opp_fouls = max(1, opp_eng.get("fouls", 0))
        my_pass_acc = detail.get("pass_acc") or (0.66 + my_poss * 0.0026)
        opp_pass_acc = 0.66 + opp_poss * 0.0026
    else:
        diff = my_score - opp_score
        my_poss = 50 + round(20 * math.tanh(diff / 2.5))
        my_poss = max(30, min(70, my_poss))
        opp_poss = 100 - my_poss

        my_shots = max(detail.get("shots", 0), round(my_score * 3.2 + my_poss * 0.08))
        my_shots_on = max(detail.get("shots_on", 0), my_score, round(my_shots * 0.35))
        my_shots = max(my_shots, my_shots_on)

        opp_shots = round(opp_score * 3.2 + opp_poss * 0.08)
        opp_shots_on = max(opp_score, round(opp_shots * 0.35))
        opp_shots = max(opp_shots, opp_shots_on)

        my_corners = max(0, round(my_shots * 0.45 + my_poss * 0.02))
        opp_corners = max(0, round(opp_shots * 0.45 + opp_poss * 0.02))

        # 점유율이 낮은 쪽(수비에 더 시달리는 쪽)이 보통 파울이 더 잦다.
        my_fouls = max(4, round(15 - my_poss * 0.08))
        opp_fouls = max(4, round(15 - opp_poss * 0.08))

        my_pass_acc = detail.get("pass_acc") or (0.66 + my_poss * 0.0026)
        opp_pass_acc = 0.66 + opp_poss * 0.0026

    home_stats, away_stats = (
        {"poss": my_poss, "shots": my_shots, "shots_on": my_shots_on,
         "corners": my_corners, "fouls": my_fouls, "pass_acc": round(my_pass_acc, 3)},
        {"poss": opp_poss, "shots": opp_shots, "shots_on": opp_shots_on,
         "corners": opp_corners, "fouls": opp_fouls, "pass_acc": round(opp_pass_acc, 3)},
    ) if is_home else (
        {"poss": opp_poss, "shots": opp_shots, "shots_on": opp_shots_on,
         "corners": opp_corners, "fouls": opp_fouls, "pass_acc": round(opp_pass_acc, 3)},
        {"poss": my_poss, "shots": my_shots, "shots_on": my_shots_on,
         "corners": my_corners, "fouls": my_fouls, "pass_acc": round(my_pass_acc, 3)},
    )
    return {"home": home_stats, "away": away_stats}


def _save_match_detail(p, week, comp_name, is_home, home_name, away_name,
                       hs, as_, result, goals, assists, saves, rating,
                       events, played, benched, detail=None, pso=None, engine_stats=None,
                       engine_plog=None):
    """경기 상세를 match_details 에 저장하고 detail_id 를 돌려준다.
       리그/챔스/국대 모두 이 헬퍼를 공유한다(팀명은 호출자가 직접 넘김).
       events 정규화(분 배정·시간순)도 여기서 처리. 실패 시 None 반환.

       pso: 승부차기로 결정된 녹아웃 경기라면 {"won": bool, "score": "5-4"}
       형태로 넘긴다. None이면 승부차기 없는 일반 경기.
       engine_stats: 전술 엔진(match_sim.tactical_engine)이 만든 실제
       시뮬레이션 통계({"home":{...},"away":{...}}). 있으면 _derive_match_stats가
       공식 추정 대신 이 실측값을 기준으로 쓴다.
       engine_plog: 전술 엔진이 만든 진짜 분 단위 possession_log. 있으면
       match_flow의 사후 필러 생성 대신 이걸 개인 서사와 병합해서 쓴다."""
    timed = []
    if played:
        for ev in events:
            if isinstance(ev, tuple) and len(ev) == 2:
                timed.append((int(ev[0]), str(ev[1])))
            else:
                timed.append((random.randint(1, 90), str(ev)))
        timed.sort(key=lambda x: _min_sortkey(x[0]))

    verdict = _match_verdict(rating, result, goals, assists) if played else ""
    detail = detail or {}
    st = get_state() or {}
    team_stats = (_derive_match_stats(is_home, hs, as_, goals, assists, saves,
                                      p.get("position", ""), detail, engine_stats=engine_stats)
                 if played else None)

    # [구조 변경] 예전엔 team_stats에 잡힌 파울/코너킥 개수를 맞추려고
    # "🟨 우리 팀 파울" / "⛳ 상대 팀 코너킥" / "🚫 세트피스 코너킥, 수비에
    # 걷어내졌다" 같은 가짜 텍스트를 이 시점에 timed(개인 이벤트 목록)에
    # 직접 끼워 넣었다. 문제는 이게 "사후 땜빵"이라 뷰어 쪽에서 재개팀을
    # 텍스트("우리 팀"/"상대 팀")로 다시 파싱해야 했고, 그 파싱 자체가
    # 반복적인 버그의 원인이었다(파울 재개팀 오판 등).
    #
    # 이제 match_flow.generate_possession_log()가 이 역할을 통째로
    # 대체한다 — team_stats(슈팅/온타깃/코너/파울)와 진짜 개인 이벤트만
    # 가지고, "언제 어느 팀이 어느 구역에서 무슨 상황이었는지"를 구조화된
    # 레코드로 만든다(team 필드 = 그 통계의 주체라서 뷰어가 텍스트를 다시
    # 파싱할 필요가 없다). 그래서 가짜 텍스트를 timed에 주입하던 이
    # 블록은 완전히 불필요해졌다 — 삭제한다. timed는 이제 진짜 개인
    # 이벤트만 담은 채로 유지되고, 그걸 그대로 possession_log 생성에
    # 넘긴다.
    possession_log = []
    if played and team_stats:
        my_score = hs if is_home else as_
        opp_score = as_ if is_home else hs
        # [재설계 — 진짜 분 단위 로그] engine_stats와 짝을 이루는 진짜
        # 시뮬레이션 possession_log(engine_plog)가 있으면(=내 경기를 새
        # 전술 엔진으로 돌린 경우) 그걸 그대로 쓴다 — match_flow가 통계
        # 숫자만 보고 사후에 흩뿌리던 필러 대신, 실제로 "이 분엔 이 팀이
        # 이 레인/서드에서 우세했다"는 시뮬레이션 산출물 그 자체다. 내
        # 개인 실제 이벤트(골/도움/선방/파울/코너 텍스트)만 그 위에
        # 병합한다(발생 시각은 그대로 유지). 없으면(폴백 등) 예전처럼
        # match_flow의 통계 기반 사후 생성으로 만든다.
        if engine_plog:
            possession_log = tactical_engine.merge_personal_events(
                engine_plog, timed, "home" if is_home else "away")
        else:
            possession_log = match_flow.generate_possession_log(
                is_home, team_stats, timed, my_score, opp_score)

    # [신규] 22명 중 나(my_slot)를 뺀 21명은 지금까지 실제 선수 스탯과
    # 완전히 무관하게 움직였다(포메이션 슬롯 라벨만 있고 실제 로스터
    # 연결이 아예 없었음). 그 팀 로스터에서 포메이션 슬롯에 맞는 11명을
    # 뽑아 최소 스탯(speed/dribbling/tackling/positioning/jump/heading/
    # stamina)만 같이 저장해둔다 — match_sim_viewer.py가 이 스탯으로
    # 선수별 최고속도/턴오버 저항/인터셉트 확률/반응성을 실제로 다르게
    # 만든다.
    lineup_stats = {}
    if played:
        try:
            lineup_stats = match_flow.generate_lineup_stats(home_name, away_name)
        except Exception:
            lineup_stats = {}

    payload = {
        "events": [[m, t] for m, t in timed],
        "verdict": verdict,
        "played": bool(played),
        "benched": bool(benched),
        "position": p.get("position", ""),
        "pso": pso,
        "detail": {
            "shots": detail.get("shots", 0),
            "shots_on": detail.get("shots_on", 0),
            "key_passes": detail.get("key_passes", 0),
            "dribbles": detail.get("dribbles", 0),
            "blocks": detail.get("blocks", 0),
            "pass_acc": detail.get("pass_acc", 0.0),
        },
        "team_stats": team_stats,
    }
    try:
        conn2 = get_conn()
        cur = conn2.execute(
            """INSERT INTO match_details
               (year,week,season,league_name,is_home,home_name,away_name,
                home_score,away_score,result,rating,goals,assists,saves,
                detail_json,possession_log,lineup_stats)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (st.get("current_year"), week, st.get("current_season"),
             comp_name, 1 if is_home else 0, home_name, away_name,
             hs, as_, result, rating, goals, assists, saves,
             json.dumps(payload, ensure_ascii=False),
             json.dumps(possession_log, ensure_ascii=False),
             json.dumps(lineup_stats, ensure_ascii=False)))
        detail_id = cur.lastrowid
        conn2.commit()
        conn2.close()
        return detail_id
    except Exception:
        return None


def _augment_events_with_names(c, p, is_home, hid, aid, hs, as_,
                               goals, assists, played, events):
    """[텍스트-영상 싱크] 이벤트 문구를 다듬는다.

      - 내가 넣은 골/어시(⚽·🎯 세트피스·🎯 페널티킥·🅰)와 그 외 내 개인
        활약(선방·차단·드리블 등) → 뒤에 "(내 이름)"을 붙인다.
      - 실점(🥅)은 내가 한 행동이 아니라 상대가 넣은 것이므로 이름을
        붙이지 않는다.
      - 우리 팀이 넣었지만 내가 골도 어시도 아닌 나머지 득점은, 로스터에서
        아무 이름이나 랜덤으로 뽑아 붙이지 않고 "어떤 골인지"만(문구만)
        타임라인에 추가한다 — 이름 없는 일반 골로 표시.

    [수정 이력] 처음엔 로스터에서 동료/상대 이름을 랜덤으로 뽑아 붙였는데,
    국가별로 이름이 뒤죽박죽 나와 어색했다. 지금은 이름은 오직 "내 이름"
    하나만 쓰고, 내가 관여 안 한 골은 이름 없이 사실(득점 존재·시점·종류)만
    보여준다.
    """
    try:
        if not played:
            return events
        my_name = p.get("name") or "나"
        my_score = hs if is_home else as_

        def tag(text):
            if "🥅" in text:
                return text  # 상대 득점 — 내가 한 게 아니므로 이름 없음
            return f"{text} ({my_name})"  # 내 골/어시/선방/차단 등 개인 행동

        new_events = []
        for ev in events:
            if isinstance(ev, tuple) and len(ev) == 2:
                m, t = ev
                new_events.append((m, tag(str(t))))
            else:
                new_events.append(tag(str(ev)))

        # 내가 골도 어시도 아닌 우리 팀의 나머지 득점 — 이름 없이 문구만.
        remaining = max(0, my_score - goals - assists)
        if remaining > 0:
            for m in _sample_minutes(remaining, 3, 90):
                new_events.append((m, random.choice(GOAL_PHRASES["normal"])))

        return new_events
    except Exception:
        return events


def _write_match_log(p, week, league_name, is_home,
                     hid, aid, hs, as_,
                     result, goals, assists, saves, rating, events, played, benched,
                     detail=None, engine_stats=None, engine_plog=None, day=None):
    # [최적화] 팀명을 세션 캐시에서 조회 (매 경기 get_conn 제거)
    conn = get_conn()
    c = conn.cursor()
    hn = _team_name(c, hid, "홈팀")
    an = _team_name(c, aid, "원정팀")

    # [텍스트-영상 싱크 확장] 이벤트 텍스트에 실제 선수 이름을 붙이고, 내가
    # 직접 관여하지 않은 우리 팀의 나머지 득점도 실제 로스터 선수 이름으로
    # 채워 넣는다 — "경기 상세"가 내 개인 기록만 보여주던 것에서 실제 팀
    # 전체 경기처럼 보이게 하기 위함. 로스터 조회가 실패해도(오프라인 팀,
    # DB 이슈 등) 경기 저장 자체는 절대 막히면 안 되므로 전부 try/except로
    # 감싸고, 실패 시 이름 태깅 없이 기존 동작으로 조용히 폴백한다.
    events = _augment_events_with_names(c, p, is_home, hid, aid, hs, as_,
                                        goals, assists, played, events)
    conn.close()

    loc = "홈" if is_home else "원정"
    rs  = {"win":"승","draw":"무","loss":"패"}.get(result,"")

    detail_id = _save_match_detail(p, week, league_name, is_home, hn, an,
                                   hs, as_, result, goals, assists, saves, rating,
                                   events, played, benched, detail, engine_stats=engine_stats,
                                   engine_plog=engine_plog)

    # ── 로그: 헤더 한 줄(클릭 가능) + 결과 + 핵심 요약 + 순위 ──────────
    #   상세 이벤트(전/후반)는 로그에서 빼고 상세 창으로 옮겨 로그를 간결하게.
    #   헤더에 [match:{id}] 마커를 박아두면 log_panel 이 클릭 앵커로 변환한다.
    marker = f" [match:{detail_id}]" if detail_id else ""
    add_log("─"*44, "sep")
    add_log(f"⚽ 경기  [{league_name}]  {_day_label(week, day)}  ({loc}){marker}", "match")
    add_log(f"   {hn} {hs}-{as_} {an}  ({rs})", "match")

    if not played:
        add_log("   🪑 벤치 대기" if benched else "   🚑 부상 결장", "match")
    else:
        if p["position"] == "GK":
            add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
        else:
            add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")
        # 다득점/멀티어시 같은 하이라이트만 로그에 한 줄 노출(나머진 상세 창에서).
        timed = sorted([(int(e[0]), e[1]) if isinstance(e, tuple) else
                        (random.randint(1, 90), str(e)) for e in events],
                       key=lambda x: _min_sortkey(x[0]))
        hi = _log_highlight(goals, assists, timed)
        if hi:
            add_log(f"   {hi}", "match")

    rank_str = get_team_rank(p.get("current_team_id",0))
    add_log(f"   📊 리그 순위: {rank_str}", "match")


def _log_highlight(goals, assists, timed):
    """로그에 한 줄로 노출할 경기 하이라이트(있으면). 다득점/멀티어시 우선."""
    banner = _multigoal_banner(goals)
    if banner:
        return banner
    if assists >= 2:
        return "🅰🔥 멀티 어시스트!"
    # 극장골/역전골이 있으면 그걸 끌어올린다.
    for _m, t in timed:
        if "극장골" in t or "역전골" in t or "결승골" in t:
            return t
    return ""


def _match_verdict(rating, result, goals, assists):
    """평점·결과·공격포인트를 종합해 총평 문구를 고른다(맥락 기반 다양화)."""
    contrib = goals + assists
    if rating >= 8.5:
        key = "great_win" if result == "win" else "great"
    elif rating >= 7.5:
        key = "great" if contrib >= 2 else ("good_win" if result == "win" else "good")
    elif rating >= 6.8:
        key = "good_win" if result == "win" else "good"
    elif rating >= 6.0:
        key = "good" if result == "win" else "average"
    elif rating >= 5.0:
        # 패했지만 평점이 받쳐주면 '분투' 톤
        key = "loss_effort" if result == "loss" and contrib >= 1 else "poor"
    else:
        key = "terrible"
    return random.choice(VERDICT_PHRASES.get(key, VERDICT_PHRASES["average"]))


# ─────────────────────────────────────────
# 순위
# ─────────────────────────────────────────

def get_my_promotions():
    """내가 실제 재직한 기간의 승강 기록 조회 (커리어 창 / 은퇴 창 공용).
    우승과 동일 기준: 리그 경기가 끝나는 35주 시점에 그 팀 소속이었던 해의
    연말 승강만 포함. (35주 이후 합류했거나 35주 전에 떠난 해는 제외)"""
    LEAGUE_END_WEEK = 35
    conn = get_conn(); c = conn.cursor()
    entries = c.execute(
        "SELECT team_name, start_year, start_week, end_year, end_week FROM career_entries ORDER BY id"
    ).fetchall()
    conds, params = [], []
    for e in entries:
        tn = e["team_name"]
        sy, sw = e["start_year"], (e["start_week"] or 0)
        ey, ew = e["end_year"], e["end_week"]
        # 이 항목이 '35주 시점에 그 팀 소속'인 연도들만 승강 대상.
        if ey == 0:
            # 진행 중(아직 안 닫힘): 시작 연도에 35주까지 함께했는지로 판단,
            # 이후 연도는 항상 포함(연말까지 소속).
            if sw <= LEAGUE_END_WEEK:
                conds.append("(team_name=? AND year>=?)"); params.extend([tn, sy])
            else:
                conds.append("(team_name=? AND year>?)"); params.extend([tn, sy])
        else:
            # 닫힌 항목: 시작 연도(35주 전 합류) ~ 종료 연도(35주 후 잔류) 사이.
            yr_start = sy if sw <= LEAGUE_END_WEEK else sy + 1
            yr_end   = ey if (ew or 0) > LEAGUE_END_WEEK else ey - 1
            if yr_start <= yr_end:
                conds.append("(team_name=? AND year>=? AND year<=?)")
                params.extend([tn, yr_start, yr_end])
    promos = []
    if conds:
        rows = c.execute(
            f"SELECT * FROM promotion_log WHERE {' OR '.join(conds)} ORDER BY id",
            params).fetchall()
        seen = set()
        for r in rows:
            key = (r["year"], r["team_name"], r["from_tier"], r["to_tier"])
            if key not in seen:
                seen.add(key)
                promos.append(dict(r))
    conn.close()
    return promos


def get_my_trophies():
    """[2026-07 버그+성능 수정] career_window/retire_window가 지금까지
    `SELECT * FROM trophy_log`를 필터 없이 그대로 써왔다 — trophy_log는
    전 세계 모든 팀의 리그 우승·컵/챔스 우승이 다 같이 쌓이는 테이블이라
    (매 시즌 675개 리그 챔피언 + 컵/챔스 우승팀이 전부 적재됨), 두 가지
    문제가 있었다:
      1) 기능 버그: 커리어/은퇴창 '성적' 탭에 내 팀이 아닌 AI 팀들의
         우승까지 전부 섞여서 보임 (get_my_promotions()는 이미 정확히
         내 재직기간으로 필터링하는데, trophy_log만 그 필터링이 빠져있었다).
      2) 성능 버그: log_panel의 game_log와 같은 유형 — 지워지지 않고
         계속 쌓이기만 하는 테이블을 창을 열 때마다 통째로 읽어서,
         플레이 연차가 쌓일수록 커리어/은퇴창을 여는 속도가 느려진다.

    trophy_log 행은 세 종류가 섞여 있다:
      - 클럽 우승(tier>0, team_name=클럽팀명): get_my_promotions()와 동일한
        방식으로 career_entries(내 재직 기간)로 필터링해야 내 것만 남는다.
      - 클럽 국제/컵대회(tier=-1 챔피언스리그, tier=-2 국내컵, team_name=
        클럽팀명): [버그수정 2026-07, 신민용 지적: "컵 대회 결과가 성적에
        기록이 안 된다"] champions_engine._save_trophy는 tier=-1로,
        cup_engine._save_trophy는 tier=-2로 저장하는데, 이 함수의 WHERE절이
        `tier>0`만 클럽 취급하고 있어서 tier가 음수인 챔스·컵 우승/성적
        행은 트로피 로그에 정상적으로 쌓이는데도 성적 탭 조회 시 통째로
        걸러져 안 보였다. team_name 필드는 클럽 우승과 동일하게 팀명이라
        career_entries로 똑같이 필터링할 수 있으므로, tier>0과 같은 조건에
        묶는다(tier=0인 국가대표만 별도 취급 유지).
      - 국가대표 성적(tier=0, team_name=국가명): _save_trophy가 애초에
        플레이어 본인 국적에 대해서만 기록하므로 이미 필터링돼 있다.
      - 개인 수상(발롱도르/MVP, team_name=선수 이름): 호출부가 기존처럼
        _is_personal_award()로 걸러내므로 여기선 신경쓰지 않아도 된다
        (club 필터에 안 걸려도 그쪽에서 최종적으로 제외됨 — 안전).
    """
    LEAGUE_END_WEEK = 35
    conn = get_conn(); c = conn.cursor()
    entries = c.execute(
        "SELECT team_name, start_year, start_week, end_year, end_week FROM career_entries ORDER BY id"
    ).fetchall()
    conds_league, params_league = [], []
    conds_intl, params_intl = [], []
    for e in entries:
        tn = e["team_name"]
        sy, sw = e["start_year"], (e["start_week"] or 0)
        ey, ew = e["end_year"], e["end_week"]

        # [2026-07 버그수정, 신민용 리포트: "아시아 챔스 우승했는데 성적에
        # 안 뜬다"] tier<0(챔스/컵) 트로피는 아래 국내리그용 로직(시즌
        # 종료 주차=35주 근처에 있었는지로 그 해 인정 여부 판단)을 그대로
        # 썼었다 — 근데 챔스/컵 결승은 시즌 중반(예: 아시아 챔스 결승
        # 23주차)에 끝나는 경우가 흔해서, "28주차에 떠났다"처럼 결승보다
        # 한참 뒤에 이적했는데도 35주 기준으로 "시즌 안 끝나고 일찍
        # 떠남" 취급돼 그 해 전체가 통째로 걸러졌다(yr_end=ey-1<yr_start
        # 가 돼 조건 자체가 안 만들어짐). 챔스/컵은 그 해에 그 팀 소속으로
        # 등록돼 있기만 했으면(재직 연도 겹침) 인정 — 시즌 종료 주차
        # 가정을 아예 적용하지 않는다.
        if ey == 0:
            conds_intl.append("(team_name=? AND year>=?)"); params_intl.extend([tn, sy])
        else:
            conds_intl.append("(team_name=? AND year>=? AND year<=?)")
            params_intl.extend([tn, sy, ey])

        if ey == 0:
            if sw <= LEAGUE_END_WEEK:
                conds_league.append("(team_name=? AND year>=?)"); params_league.extend([tn, sy])
            else:
                conds_league.append("(team_name=? AND year>?)"); params_league.extend([tn, sy])
        else:
            yr_start = sy if sw <= LEAGUE_END_WEEK else sy + 1
            yr_end   = ey if (ew or 0) > LEAGUE_END_WEEK else ey - 1
            if yr_start <= yr_end:
                conds_league.append("(team_name=? AND year>=? AND year<=?)")
                params_league.extend([tn, yr_start, yr_end])

    where_parts, params = ["tier=0"], []
    if conds_league:
        where_parts.append(f"(tier>0 AND ({' OR '.join(conds_league)}))")
        params += params_league
    if conds_intl:
        where_parts.append(f"(tier<0 AND ({' OR '.join(conds_intl)}))")
        params += params_intl

    if len(where_parts) > 1:
        where = " OR ".join(where_parts)
        rows = c.execute(f"SELECT * FROM trophy_log WHERE {where} ORDER BY id", params).fetchall()
    else:
        # 클럽 재직 이력이 아예 없으면(예: 프리에이전트로만 지냄) 국가대표
        # 성적(tier=0)만 대상.
        rows = c.execute("SELECT * FROM trophy_log WHERE tier=0 ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_team_rank(team_id, conn=None, season=None) -> str:
    """팀 순위 문자열 반환. conn/season 주어지면 재사용.
    [2026-08 확장, 신민용 요청: "15위 (2승 0무 7패 / 승점)처럼 뜨는데
    15위/그 리그 총 몇팀 이렇게 총 팀 수도 같이 떠야 한다"] rank_str
    파싱하는 다른 호출부(예: rank_str.split("위")[0])는 "위" 바로 뒤에
    "/총 N팀"이 붙어도 그 앞부분(숫자)만 잘라내므로 영향이 없다."""
    if not team_id:
        return "정보 없음"
    rows = get_league_standings_by_team(team_id, conn=conn, season=season)
    if not rows:
        return "정보 없음"
    total = len(rows)
    for i, r in enumerate(rows):
        if r["id"] == team_id:
            rank = i + 1
            if (i > 0
                    and rows[i-1]["pts"] == r["pts"]
                    and rows[i-1]["gd"] == r["gd"]
                    and rows[i-1].get("goals_for", rows[i-1].get("gf", 0))
                        == r.get("goals_for", r.get("gf", 0))):
                rank_str = f"공동 {rank}위"
            else:
                rank_str = f"{rank}위"
            return f"{rank_str}/{total}팀  ({r['wins']}승 {r['draws']}무 {r['losses']}패 / 승점 {r['pts']}점)"
    return "정보 없음"


# [2026-08 신설] player_panel.py 순위 라벨 색상 상수 — 표준 초록/빨강/파랑.
RANK_COLOR_NEUTRAL = "#00cc44"
RANK_COLOR_RELEGATED = "#ff5555"
RANK_COLOR_PROMOTED = "#4da6ff"


def get_team_rank_with_zone_color(team_id) -> tuple:
    """[2026-08 신설, 신민용 요청: "player_panel.py 순위 표시 — 확정
    강등권이면 빨간색, 확정 승격권이면 파란색. 확정은 승강 플레이오프
    안 뛰고 바로 올라가거나 내려가는 걸 말한다. 강등당하면 지금은
    정보없음이라 뜨는데, 다음 1주차가 오기 전까지는 그 결과가 그대로
    보여야 한다 — 자동 강등이든 PO에서 져서 강등이든 빨간색, PO에서
    이겨서 잔류했으면 원래 초록색, 승격도 마찬가지로 파란색"]
    (rank_str, color_hex) 튜플 반환.

    ["확정"의 실제 판정 기준] 승격/강등 여부는 auto(직행)/PO(플레이오프)
    경로를 따로 구분할 필요가 없다 — 어느 경로든 실제로 팀이 이동하면
    promotion_playoff_engine.py가 promotion_log에 그대로 기록하므로,
    "이 시즌(year)에 이 팀의 promotion_log 기록이 있는가"만 보면 결과
    (승격/강등/잔류)를 이미 다 알 수 있다. PO가 아직 안 끝나서 최종
    결과가 없는 동안은 그냥 초록(중립)으로 둔다 — 아직 확정된 게 없으므로.

    ["다음 1주차가 오기 전까지" 유지되는 이유] get_league_standings_by_team
    (season=이번 시즌)은 이미 _team_league_id_for_season으로 "그 시즌에
    실제로 뛴 리그"를 찾는다 — 그래서 44~52주(승강이 teams.league_id엔
    이미 반영됐지만 아직 같은 시즌 번호인 기간) 동안에도 옛 리그의 최종
    순위가 그대로 나온다. 새 시즌(current_season 증가)이 되고 아직 그
    시즌 경기가 하나도 없으면, teams.league_id(승강 반영된 새 리그)로
    폴백해서 0-0-0인 새 시즌 순위표가 나온다 — 정확히 "다음 1주차가
    오면 교체"되는 지점이다."""
    if not team_id:
        return "정보 없음", RANK_COLOR_NEUTRAL
    st = get_state()
    if not st:
        return get_team_rank(team_id), RANK_COLOR_NEUTRAL

    season_now = st.get("current_season", 1)
    year_now = st.get("current_year")

    conn = get_conn()
    rank_str = get_team_rank(team_id, conn=conn, season=season_now)
    season_used, year_used = season_now, year_now
    if rank_str == "정보 없음" and season_now > 1:
        # 이번 시즌엔 아직 실제 경기가 없다(막 새 시즌 진입) — 직전
        # 시즌의 최종 순위를 대신 보여준다("다음 1주차가 오기 전까지").
        rank_str = get_team_rank(team_id, conn=conn, season=season_now - 1)
        season_used, year_used = season_now - 1, (year_now - 1 if year_now else None)

    color = RANK_COLOR_NEUTRAL
    if rank_str != "정보 없음" and year_used is not None:
        mv = conn.execute(
            """SELECT from_tier, to_tier FROM promotion_log
               WHERE year=? AND team_id=? ORDER BY id DESC LIMIT 1""",
            (year_used, team_id)).fetchone()
        if mv:
            if mv["to_tier"] < mv["from_tier"]:
                color = RANK_COLOR_PROMOTED   # tier 숫자가 작아짐 = 상위 리그로 승격
            elif mv["to_tier"] > mv["from_tier"]:
                color = RANK_COLOR_RELEGATED  # tier 숫자가 커짐 = 하위 리그로 강등
    conn.close()
    return rank_str, color


def get_league_standings_by_team(team_id, conn=None, season=None):
    """팀 ID로 해당 리그 순위표 반환. conn/season 주어지면 재사용.

    [버그수정 2026-08, 신민용 리포트: "승급 직후(43주 이후)엔 그 시즌 커리어
    기록이 0승0무0패로 나온다"] season이 주어졌는데 teams.league_id(현재
    소속 리그)를 그대로 쓰면, 승강이 이미 반영된 뒤(44~52주)엔 '그 시즌
    실제로 뛴 리그'가 아니라 '다음 시즌 리그'로 조회하게 된다 — 그 리그+
    시즌엔 이 팀 경기가 아예 없으니 순위표에서 통째로 빠진다. season이
    주어지면 _team_league_id_for_season으로 그 시즌에 실제로 뛴 리그를
    먼저 찾고, 없으면(이번 시즌 아직 한 경기도 안 뛴 경우) teams.league_id로
    폴백한다 — season=None(그냥 "지금 이 팀 리그")일 때는 기존 그대로."""
    own = conn is None
    if own:
        conn = get_conn()
    c = conn.cursor()
    lid = _team_league_id_for_season(c, team_id, season) if season is not None else None
    if lid is None:
        row = c.execute("SELECT league_id FROM teams WHERE id=?", (team_id,)).fetchone()
        lid = row["league_id"] if row else None
    if own:
        conn.close()
    if not lid:
        return []
    return get_league_standings(lid, season=season,
                                conn=None if own else conn)


def get_league_standings(league_id, season=None, conn=None):
    """순위표: match_results에서 직접 집계해서 항상 정확한 값 반환.
    [최적화] season/conn 파라미터 추가 — 외부에서 열린 커넥션 재사용 가능.

    [버그수정] 예전엔 로스터를 teams.league_id(=현재 소속 리그) 기준으로 잡았다.
    승강 전 시즌은 문제없지만, 승강이 한 번이라도 일어난 뒤 '그 이전 시즌'을
    조회하면: (1) 그 시즌엔 실제로 안 뛰었는데 지금 이 리그 소속인 팀이
    0승0무0패로 끼어들어 순위표 맨 밑에 나타나고, (2) 그 시즌엔 실제로 뛰었지만
    이후 승강돼서 지금은 다른 리그 소속인 팀은 통째로 순위표에서 빠지는 문제가
    있었다(예: FC 목포가 시즌1엔 K3에서 뛰었는데 시즌2에 K2로 승격된 뒤
    '시즌1 K2 순위표'를 보면 목포가 0-0-0-0으로 8위에 등장). 이제 그 시즌의
    실제 일정(match_results, 완료 여부 무관)에 등장하는 team_id로 로스터를
    구성해서 승강 이후에도 과거 시즌 순위표가 그때 그대로 나오게 한다."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    c = conn.cursor()

    if season is None:
        st = get_state()
        season = st["current_season"] if st else 1

    # [2026-08 추가, 신민용 리포트: "게임.db가 너무 커져서 렉이 심하다"]
    # 이 시즌이 이미 요약돼 있으면(archive_old_seasons가 시즌을 넘길 때
    # 미리 계산해둠) 원본 경기 수백만 건을 다시 훑을 필요 없이 요약
    # 테이블에서 바로 가져온다 — 결과는 완전히 동일하다(같은 공식으로
    # 미리 계산해둔 것뿐). 요약이 없으면(진행 중인 이번 시즌, 또는 아직
    # 1회성 마이그레이션 전인 옛 세이브) 예전처럼 원본을 직접 집계한다.
    summarized = c.execute(
        """SELECT team_id, wins, draws, losses, goals_for, goals_against
           FROM league_season_standings WHERE league_id=? AND season=?""",
        (league_id, season)).fetchall()
    if summarized:
        team_ids = {r["team_id"] for r in summarized}
        qmarks = ",".join("?" * len(team_ids))
        c.execute(f"SELECT id, name FROM teams WHERE id IN ({qmarks})", tuple(team_ids))
        names = {r["id"]: r["name"] for r in c.fetchall()}
        if own_conn:
            conn.close()
        rows = [{"id": r["team_id"], "name": names.get(r["team_id"], "?"),
                 "wins": r["wins"], "draws": r["draws"], "losses": r["losses"],
                 "goals_for": r["goals_for"], "goals_against": r["goals_against"]}
                for r in summarized]
        for r in rows:
            r["pts"] = r["wins"] * 3 + r["draws"]
            r["gd"] = r["goals_for"] - r["goals_against"]
        rows.sort(key=lambda r: (-r["pts"], -r["gd"], -r["goals_for"]))
        return rows

    # [2026-07 수정] match_results_archive 함께 조회 — 시즌 전환 시 과거
    # 시즌 데이터가 archive_old_seasons()로 옮겨지므로, 이 함수가 과거
    # 시즌(world_browser 역대 조회) 요청을 받으면 archive도 봐야 정확하다.
    # 현재 진행 중인 시즌 조회 시엔 archive 쪽엔 해당 season 행이 아예 없어
    # (league_id,season) 인덱스로 즉시 빈 결과가 나오므로 이 UNION이 매주
    # 호출되는 현재시즌 조회 성능에 주는 영향은 무시할 만하다.
    c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                 FROM match_results WHERE league_id=? AND season=?
                 UNION ALL
                 SELECT home_team_id, away_team_id, home_score, away_score
                 FROM match_results_archive WHERE league_id=? AND season=?""",
              (league_id, season, league_id, season))
    all_rows = c.fetchall()

    team_ids = {tid for r in all_rows for tid in (r["home_team_id"], r["away_team_id"])}
    if team_ids:
        qmarks = ",".join("?" * len(team_ids))
        c.execute(f"SELECT id, name FROM teams WHERE id IN ({qmarks})", tuple(team_ids))
    else:
        # 그 시즌 일정 자체가 아직 없는 경우(예: 새 시즌 시작 직후)엔
        # 현재 리그 소속팀을 그대로 로스터로 사용한다.
        c.execute("SELECT id, name FROM teams WHERE league_id=?", (league_id,))
    teams = {r["id"]: {"id": r["id"], "name": r["name"],
                       "wins":0,"draws":0,"losses":0,
                       "goals_for":0,"goals_against":0} for r in c.fetchall()}

    for row in all_rows:
        if row["home_score"] < 0:
            continue
        hid, aid, hs, as_ = (row["home_team_id"], row["away_team_id"],
                              row["home_score"], row["away_score"])
        for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
            if tid not in teams: continue
            teams[tid]["goals_for"]     += gf
            teams[tid]["goals_against"] += ga
            if gf > ga:    teams[tid]["wins"]   += 1
            elif gf == ga: teams[tid]["draws"]  += 1
            else:          teams[tid]["losses"] += 1

    if own_conn:
        conn.close()

    rows = list(teams.values())
    for r in rows:
        r["pts"] = r["wins"] * 3 + r["draws"]
        r["gd"]  = r["goals_for"] - r["goals_against"]

    rows.sort(key=lambda r: (-r["pts"], -r["gd"], -r["goals_for"]))
    return rows


# ─────────────────────────────────────────
# 경기 일정 생성
# ─────────────────────────────────────────

EXISTING_MATCH_DAY_TOLERANCE = 14   # 이 이내로 같은 두 팀 경기가 이미 있으면 중복으로 간주
                                     # (재생성 시 중복 오차가 최대 10일까지 관측됨 — 실측
                                     #  기반 여유값. 정상적인 재대결 사이클 간격은 수십~
                                     #  70일 이상이라 이 정도로는 절대 안 겹친다.)


def _is_dup_fixture(existing_matches: dict, pair_key: tuple, day: int) -> bool:
    """pair_key(min_tid,max_tid) 조합이 day 근방(±EXISTING_MATCH_DAY_TOLERANCE)에
    이미 예정/완료돼 있으면 True."""
    for d0 in existing_matches.get(pair_key, ()):
        if abs(d0 - day) <= EXISTING_MATCH_DAY_TOLERANCE:
            return True
    return False


def _build_league_schedule_rows(league_id, tids, season, year, existing_matches, first_half_only=False):
    """리그 하나의 시즌 전체 경기 행을 만들어 반환한다(스코어 -1,-1로 예정).

    [2026-07 추가] 팀 수가 다른 리그끼리 시즌 종료 시점이 3개월 넘게
    벌어지는 문제 수정 — 라운드 간격을 1주로 캡 씌우다 보니 라운드 자체가
    적은 소규모 리그(8팀=7라운드)가 시즌 초반에 다 끝나버렸다. 그래서
    legs_for_team_count()로 팀이 적을수록 서로 더 여러 번(다전제) 붙게
    하고(K리그1이 12팀·3전인 것과 같은 발상), season_cycle_windows()로
    시즌 전체 기간을 그 다전제 횟수만큼 사이클로 나눠 각 사이클마다
    기존 '왕복 2전'(상반기 1다리 + 하반기 반전 1다리) 구조를 그대로
    재사용한다 — 그래서 라운드 스프레드·리그별 요일 오프셋·마지막 라운드
    전 구단 동시진행 로직을 손 안 대고 사이클 수만큼 반복 적용할 수 있다.

    existing_matches: {(min_tid,max_tid): [day, ...]} 중복 방지 딕셔너리
    (직접 갱신됨) — 같은 팀 조합이 후보 day에서 며칠 이내(EXISTING_MATCH_DAY_TOLERANCE)
    에 이미 예정/완료돼 있으면 중복으로 보고 건너뛴다.
    first_half_only=True면 첫 사이클의 첫 다리(1라운드~)만 생성하고 끝낸다
    (오퍼 창 순위 미리보기용 — 예전 _generate_first_half_schedule과 동일 용도).
    반환: [(league_id,week,home,away,season,year,day), ...] executemany용.
    """
    n = len(tids)
    if n < 2:
        return []

    rounds = generate_round_robin(n)
    n_rounds = len(rounds)
    legs = 2 if first_half_only else legs_for_team_count(n)

    # [2026-08 신설, 신민용 확정: "팀 수가 아주 많은 리그(25팀 이상)는
    # 왕복이 아니라 단판(전 팀이 서로 딱 1번씩만)으로"] legs==1은 기존
    # "같은 대진을 h1/h2에서 두 번(홈/원정 반전) 반복"하는 구조와 근본적으로
    # 다르다 — 반복 없이 딱 한 번씩만 만나야 하므로, generate_round_robin이
    # 만든 n_rounds개 라운드를 절반씩 h1/h2 창에 나눠 배정한다(반전 없음,
    # 매 라운드 독립적으로 홈/원정 추첨). 그 외(legs>=2)는 기존 로직 그대로.
    is_single_round = (legs == 1)
    n_cycles = max(1, legs // 2) if not is_single_round else 1
    windows = season_cycle_windows(n_cycles)
    _lg_off = league_day_offset(league_id)

    new_rows = []
    leg_home = {}   # (cyc,rd,hi,ai) -> 그 사이클 1다리에서 실제 홈이었던 team_id
    for cyc, (h1s, h1e, h2s, h2e) in enumerate(windows):
        is_last_cycle = (cyc == n_cycles - 1)
        if is_single_round:
            # 라운드를 절반씩 h1/h2 창에 나눠 배정 — 각 라운드는 딱 한 번만 등장.
            half = (n_rounds + 1) // 2
            leg_round_slices = [(0, half, (h1s, h1e)), (half, n_rounds, (h2s, h2e))]
        else:
            leg_round_slices = [(0, n_rounds, (h1s, h1e)), (0, n_rounds, (h2s, h2e))]

        for leg_idx, (rd_start, rd_end, (w_start, w_end)) in enumerate(leg_round_slices):
            if first_half_only and (cyc, leg_idx) != (0, 0):
                break  # 미리보기용은 첫 사이클 첫 다리만 필요
            is_last_leg_of_season = (not first_half_only) and is_last_cycle and (
                leg_idx == len(leg_round_slices) - 1)
            leg_rounds = list(enumerate(rounds))[rd_start:rd_end]
            n_leg_rounds = len(leg_rounds)
            for local_idx, (rd, matches) in enumerate(leg_rounds):
                valid_pairs = [(hi, ai) for hi, ai in matches if hi < n and ai < n]
                is_final_round = is_last_leg_of_season and (local_idx == n_leg_rounds - 1)
                if is_final_round and n_leg_rounds >= 2:
                    # 시즌 진짜 마지막 라운드 — 전 구단 동시진행(스프레드 없음).
                    prev_rd, prev_matches = leg_rounds[local_idx - 1]
                    prev_valid = [(hi, ai) for hi, ai in prev_matches if hi < n and ai < n]
                    day = final_round_day(local_idx - 1, n_leg_rounds, w_start, w_end,
                                           len(prev_valid), offset=_lg_off)
                    match_days = [day] * len(valid_pairs)
                else:
                    match_days = round_match_days(local_idx, n_leg_rounds, w_start, w_end,
                                                   len(valid_pairs), offset=_lg_off)
                for (hi, ai), day in zip(valid_pairs, match_days):
                    week = day_to_week(day)
                    if is_single_round:
                        # 단판 — 반전 개념 없음, 매 라운드 독립 추첨.
                        t1, t2 = (tids[hi], tids[ai]) if random.random() < 0.5 else (tids[ai], tids[hi])
                    elif leg_idx == 0:
                        t1, t2 = (tids[hi], tids[ai]) if random.random() < 0.5 else (tids[ai], tids[hi])
                        leg_home[(cyc, rd, hi, ai)] = t1
                    else:
                        # [버그수정] 2다리는 1다리 홈/원정을 반드시 반전(같은
                        # 팀이 계속 홈이 되는 걸 방지) — generate_season_schedule
                        # 원래 주석 그대로.
                        fh_home = leg_home.get((cyc, rd, hi, ai))
                        if fh_home == tids[hi]:
                            t1, t2 = tids[ai], tids[hi]
                        elif fh_home == tids[ai]:
                            t1, t2 = tids[hi], tids[ai]
                        else:
                            t1, t2 = (tids[ai], tids[hi]) if random.random() < 0.5 else (tids[hi], tids[ai])
                    # [2026-07 버그수정, 신민용 리포트: "인천 유나이티드만
                    # 경기수가 다른 팀보다 훨씬 적다"] 예전엔 (day,t1,t2)로
                    # 중복을 걸렀는데, 일정이 두 번째로 재생성될 때 같은
                    # 두 팀 조합이 정확히 같은 day가 아니라 하루쯤 어긋난
                    # day에 배정되는 경우가 있었다 — 그러면 이 dedup이
                    # "새 경기"로 착각해서 중복 행을 또 만들었다. AI팀은
                    # 두 중복 행이 둘 다 알아서 시뮬돼 승패가 부풀려지고,
                    # 내 팀은 두 행 다 시뮬 대상에서 제외되는 로직 때문에
                    # 하나가 영원히 -1,-1로 남아 완료 경기 수만 줄어들었다.
                    # day 대신 week 단위로 같은 두 팀 조합이 이미 있는지
                    # 보면, 하루 어긋난 재생성도 정확히 잡아낸다(같은 두
                    # 팀이 한 주에 두 번 붙는 경우는 실제로 없음 — 다전제
                    # 라운드는 항상 다른 주차로 떨어지게 설계돼 있음).
                    # [2026-07 버그수정, 신민용 리포트: "인천 유나이티드만
                    # 경기수가 다른 팀보다 훨씬 적다"] 일정이 두 번째로
                    # 재생성될 때 같은 두 팀 조합이 원래 배정된 day에서
                    # 하루 이틀 어긋난 day에 또 배정되는 경우가 있었다.
                    # 처음엔 이걸 (week,home,away) 키로 막으려 했는데, 하루
                    # 차이가 week 경계를 넘나드는 경우(예: day35=5주차,
                    # day36=6주차)엔 그마저도 못 걸러냈다 — 그래서 정확한
                    # week/day 매칭 대신 "같은 두 팀이 후보 day 근방
                    # (EXISTING_MATCH_DAY_TOLERANCE일 이내)에 이미 있는지"로
                    # 판정한다. AI팀은 중복 행이 둘 다 알아서 시뮬돼 승패가
                    # 부풀려지고, 내 팀은 중복 행 하나가 시뮬 대상에서
                    # 제외되는 로직 때문에 영원히 -1,-1로 남아 완료 경기
                    # 수만 줄어드는 게 이 버그의 증상이었다.
                    pair_key = (min(t1, t2), max(t1, t2))
                    if _is_dup_fixture(existing_matches, pair_key, day):
                        continue
                    new_rows.append((league_id, week, t1, t2, season, year, day))
                    existing_matches.setdefault(pair_key, []).append(day)
    return new_rows


def generate_season_schedule(league_id, season, year, force=False):
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,))
    tids = [r["id"] for r in c.fetchall()]
    if len(tids) < 2:
        conn.close(); return

    # [2026-07] 다전제(legs_for_team_count) 반영 — 팀이 적을수록 총 경기 수가
    # 왕복 2전보다 많아질 수 있어 '완비 판정' 기준도 실제 legs 기준으로 계산.
    # [2026-08 버그수정, 신민용 리포트: "메이저 리그 사커(30팀) 경기 기록이
    # 아예 없다"] "legs // 2" 정수나눗셈은 legs=1(25팀 이상 단판, 오늘 신설)일
    # 때 0이 돼서 expected_matches가 0이 되고, 그 결과 아래 완비 판정이
    # 항상 "이미 충분함"으로 오판 — 30팀 리그 전체가 일정 생성 자체를
    # 건너뛰는 사고로 이어졌다(_generate_all_league_schedules의 동일 버그와
    # 세트). 실수 나눗셈으로 바꾸면 기존(legs 짝수) 리그는 결과가 완전히
    # 동일하고, legs=1만 올바르게 0.5로 계산된다.
    legs = legs_for_team_count(len(tids))
    expected_matches = len(tids) * (len(tids) - 1) * (legs / 2)

    # [중복 생성 방지] 그 시즌 일정이 이미 충분히 생성돼 있으면(상·하반기분)
    #   다시 만들지 않는다. 승강 등으로 teams 구성이 바뀐 뒤 재호출되면 옛 일정과
    #   새 대진이 섞여 '어떤 팀 3경기 / 어떤 팀 0경기'가 되는 것을 막는다.
    if not force:
        # [버그수정 2026-07, 신민용 리포트: "2001년엔 14팀인데 2002년엔
        # 17팀으로 뜬다"] 완비 판정이 match_results(라이브)만 셌다 —
        # archive_old_seasons()가 지난 시즌 데이터를 match_results_archive로
        # 옮기고 나면(다음 시즌 진입 시 항상 일어남) 이 함수가 그 지난 시즌에
        # 대해 재호출됐을 때(예: 오퍼 창의 "작년 성적" 계산 — prev_season으로
        # 호출) match_results엔 그 시즌 행이 0개로 보여 "완비 안 됨"으로
        # 오판, 그 시점의(승강으로 이미 달라졌을 수 있는) 팀 구성으로 새
        # 일정을 통째로 또 깔아버렸다 — 결과: 같은 리그+시즌에 원래 팀 구성
        # (아카이브, 결과 있음)과 새 팀 구성(라이브, 전부 -1,-1)이 동시에
        # 존재해 get_league_standings가 둘 다 합산하며 팀 수가 부풀어 보임
        # (실측: K3리그 14팀 → 17팀). _generate_all_league_schedules /
        # get_league_standings_for_browser는 이미 아카이브+라이브 합산으로
        # 완비 여부를 판정하도록 고쳐져 있었는데, 이 함수만 누락돼 있었다.
        n_existing = c.execute(
            """SELECT (SELECT COUNT(*) FROM match_results WHERE league_id=? AND season=?)
                     + (SELECT COUNT(*) FROM match_results_archive WHERE league_id=? AND season=?)
               AS c""",
            (league_id, season, league_id, season)).fetchone()["c"]
        # [2026-08 버그수정, 신민용 리포트: "역대 우승팀이 2000년부터 다
        # 안 나온다"] match_results_archive를 압축하면서(리그 순위표는
        # league_season_standings 요약으로 충분하다는 전제로) 내 커리어와
        # 무관한 팀들의 원본 경기 행을 지운 게, 여기(완비 여부 판정)에서는
        # "이미 끝난 과거 시즌인데 원본이 없으니 완비 안 됨"으로 오판하게
        # 만들었다 — 그러면 이미 끝난 시즌 일정을 통째로 또 생성해버릴
        # 위험이 있다. league_season_standings에 이 시즌 요약이 있으면
        # (정상적으로 다 뛰고 아카이브+압축된 시즌이라는 뜻) 원본 행 수와
        # 무관하게 완비로 간주한다.
        if n_existing < expected_matches * 0.8:
            _summarized = c.execute(
                "SELECT 1 FROM league_season_standings WHERE league_id=? AND season=? LIMIT 1",
                (league_id, season)).fetchone()
            if _summarized:
                conn.close(); return
        # 총 경기 수의 8할 이상 차 있으면 완비로 간주.
        if n_existing >= expected_matches * 0.8:
            conn.close(); return

    # 이미 예정된 경기 (같은 두 팀 조합이 후보 day 근방에 이미 있으면
    #   중복으로 본다 — _build_league_schedule_rows/_is_dup_fixture 주석 참고.
    #   day가 없는(이 기능 이전에 생성된) 과거 행은 이 dedup 대상이 아니며,
    #   전체 완비 여부는 위의 80% 카운트 체크가 별도로 지켜준다.)
    c.execute("""SELECT day, week, home_team_id, away_team_id FROM match_results
                 WHERE league_id=? AND season=? AND day IS NOT NULL""", (league_id, season))
    existing_matches = {}   # {(min_tid,max_tid): [day, ...]}
    for r in c.fetchall():
        d, h, a = r["day"], r["home_team_id"], r["away_team_id"]
        existing_matches.setdefault((min(h, a), max(h, a)), []).append(d)

    new_rows = _build_league_schedule_rows(league_id, tids, season, year, existing_matches)

    if new_rows:
        c.executemany("""INSERT INTO match_results
                         (league_id,week,home_team_id,away_team_id,
                          home_score,away_score,season,year,day)
                         VALUES(?,?,?,?,-1,-1,?,?,?)""", new_rows)

    conn.commit()
    conn.close()


def _repair_current_season_in_archive(league_id, season):
    """[2026-07 버그수정, 신민용 리포트: "팀은 있는데 일정이 안 뜬다"]
    archive_old_seasons()는 'season < current_season'인 것만 옮기므로
    정상적이라면 지금 진행 중인 season의 데이터가 archive에 있을 수 없다.
    그런데 실측 사례(릴 OSC 세이브)에서 season_state.current_season과 같은
    season의 경기 306개(완전한 한 시즌 분량, 실제 스코어 있음)가 통째로
    match_results_archive에 들어가 있고 match_results(라이브)는 텅 비어
    있었다 — 정확한 트리거는 못 찾았지만(시즌 전환 시점 재호출 등으로
    current_season 값이 일시적으로 어긋났을 가능성), 데이터 자체는
    멀쩡한 진짜 결과이므로 버리지 않고 라이브 테이블로 복구한다.
    generate_season_schedule의 '이미 완비됨' 판정이 archive까지 합산해서
    보기 때문에, 이 상태에서는 재생성 시도 자체가 조용히 no-op 되어
    영원히 복구가 안 됐다."""
    conn = get_conn()
    c = conn.cursor()
    n_archived = c.execute(
        "SELECT COUNT(*) c FROM match_results_archive WHERE league_id=? AND season=?",
        (league_id, season)).fetchone()["c"]
    if n_archived == 0:
        conn.close()
        return False
    n_live = c.execute(
        "SELECT COUNT(*) c FROM match_results WHERE league_id=? AND season=?",
        (league_id, season)).fetchone()["c"]
    if n_live > 0:
        # 라이브에도 이미 있으면(정상 상태) 손대지 않는다 — 그냥 넘어감.
        conn.close()
        return False
    cols = "id,league_id,week,home_team_id,away_team_id,home_score,away_score,season,year,day"
    c.execute(
        f"""INSERT OR IGNORE INTO match_results({cols})
            SELECT {cols} FROM match_results_archive WHERE league_id=? AND season=?""",
        (league_id, season))
    c.execute(
        "DELETE FROM match_results_archive WHERE league_id=? AND season=?",
        (league_id, season))
    conn.commit()
    conn.close()
    add_log(f"🔧 일정 복구: 리그#{league_id} {season}시즌 경기 {n_archived}건을 "
            f"보관함에서 되살렸습니다.", "event")
    return True


def _generate_adjacent_schedules(my_lid, season, year):
    """내 리그 + 같은 국가 위아래 1티어 리그 일정을 함께 생성.
    승강 처리 시 인접 리그 순위가 필요하므로 반드시 함께 생성해야 함."""
    _repair_current_season_in_archive(my_lid, season)
    generate_season_schedule(my_lid, season, year)
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT country_id, tier FROM leagues WHERE id=?", (my_lid,)).fetchone()
    if row:
        cid, tier = row["country_id"], row["tier"]
        for adj_tier in [tier - 1, tier + 1]:
            if adj_tier < 1: continue
            adj = c.execute(
                "SELECT id FROM leagues WHERE country_id=? AND tier=?",
                (cid, adj_tier)).fetchone()
            if adj:
                _repair_current_season_in_archive(adj["id"], season)
                generate_season_schedule(adj["id"], season, year)
    conn.close()


def get_schedule(league_id, season):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT mr.*, ht.name as home_name, at.name as away_name
                 FROM match_results mr
                 JOIN teams ht ON mr.home_team_id=ht.id
                 JOIN teams at ON mr.away_team_id=at.id
                 WHERE mr.league_id=? AND mr.season=?
                 ORDER BY mr.day, mr.week""", (league_id, season))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ─────────────────────────────────────────
# 주급
# ─────────────────────────────────────────

def _pay_salary(p, week):
    """[2026-07 수정, 신민용 지적: "축구는 월급이 아니라 주급으로 얘기하지
    않나"] 실제 축구는 연봉을 52주로 나눈 주급으로 지급하는 게 관례다
    (손흥민 주급 X억 식). 기존엔 salary//12를 4주마다 지급해 '월급'이었는데,
    이제 salary//52를 매주 지급하는 '주급' 체계로 바꾼다."""
    salary = p.get("salary",0)
    if salary <= 0: return
    # weekly가 0이 되지 않도록 최솟값 1천원 보장
    # (F급 tier2~3 등 초저연봉: salary=3~100천원 → salary//52=0 → 무급 표시 버그)
    weekly = max(1, salary // 52)
    # 에이전트 수수료: 개별 계약 수수료율(agent_fee_rate)이 있으면 그것,
    # 없으면(0) 등급 기본값. 같은 등급이라도 계약마다 수수료가 다를 수 있다.
    fee = p.get("agent_fee_rate", 0) or AGENT_FEE_RATE.get(p.get("agent_grade","F"), 0)
    net = max(1, int(weekly * (1-fee)))  # 수수료 후도 최소 1천원
    assets   = p.get("total_assets",   0) + net
    earnings = p.get("total_earnings", 0) + net  # 이슈10: 누적 수입
    update_player(total_assets=assets, total_earnings=earnings)
    add_log(f"💰 주급 수령  +{fmt_money(net)}  (총자산: {fmt_money(assets)})", "salary")


# ─────────────────────────────────────────
# 주차 전진
# ─────────────────────────────────────────

def _update_residency_and_naturalization(cur_year):
    """[귀화] 매 연도 전환 시 호출.
    - 현재 소속 클럽의 '나라'에서 보낸 누적 연수를 추적한다.
      같은 나라면 +1, 나라가 바뀌면 1로 리셋. (그 나라 안에서 팀 이동은 유지)
    - 같은 나라에서 3년을 채우고, 21세 이전이며, A대표 '본선'을 아직 안 밟았고,
      그 나라가 아직 내 국적/귀화국적이 아니면 → 귀화 국적을 획득(복수국적 추가).
      이후 국가대표 선택 시 후보에 포함된다. (21세 이후엔 자동 소속고정이라 무의미)
    """
    p = get_player()
    if not p:
        return
    tid = p.get("current_team_id")
    if not tid:
        return
    conn = get_conn()
    row = conn.execute(
        "SELECT c.name AS cname FROM teams t JOIN countries c ON t.country_id=c.id "
        "WHERE t.id=?", (tid,)).fetchone()
    conn.close()
    if not row:
        return
    club_country = row["cname"]

    prev_country = p.get("residency_country", "") or ""
    prev_years = p.get("residency_years", 0) or 0
    if club_country == prev_country:
        new_years = prev_years + 1
    else:
        new_years = 1
    update_player(residency_country=club_country, residency_years=new_years)

    # --- 귀화 자격 판정 ---
    # [버그수정] age 컬럼 사용. 이 함수는 나이 증가 전에 호출되고 cur_year=year+1
    #   (다음해)이므로, 다음해 기준 나이는 현재 age 컬럼 + 1.
    age = (p.get("age", 0) or 0) + 1
    if age > 21:
        return                      # 21세 넘으면 소속 자동확정, 귀화 불가
    if p.get("intl_capped", 0):
        return                      # 이미 본선 출전(cap-tie) → 변경 불가
    if p.get("intl_committed", ""):
        return                      # 이미 대표팀 영구고정
    if new_years < 2:
        return                      # 거주 2년 미충족

    # 이미 보유한 국적(출생/귀화)이면 스킵
    owned = {p.get("nationality","") or "", p.get("nationality2","") or "",
             p.get("nationality3","") or "", p.get("nationality4","") or ""}
    nat_list = [n for n in (p.get("naturalized_nats","") or "").split(",") if n]
    owned |= set(nat_list)
    if club_country in owned:
        return
    # 빈 국적 슬롯에 귀화 국적 추가 (nationality2 → nationality3 → nationality4)
    # 국기도 함께 저장해 연혁/표시에서 깃발이 비지 않게 한다.
    conn2 = get_conn()
    frow = conn2.execute("SELECT flag FROM countries WHERE name=?", (club_country,)).fetchone()
    conn2.close()
    club_flag = frow["flag"] if frow else ""
    if not (p.get("nationality2","") or ""):
        update_player(nationality2=club_country, flag2=club_flag)
    elif not (p.get("nationality3","") or ""):
        update_player(nationality3=club_country, flag3=club_flag)
    elif not (p.get("nationality4","") or ""):
        update_player(nationality4=club_country, flag4=club_flag)
    else:
        return                      # 국적 슬롯이 꽉 참(이미 4개)
    nat_list.append(club_country)
    update_player(naturalized_nats=",".join(nat_list))
    # [국적 연혁] 귀화 획득 사건 기록 (현재 연도/주차)
    add_nat_history("naturalize", club_country, club_flag, cur_year, 1)
    try:
        add_log(f"🛂 {club_country} 귀화 자격 획득! ({club_country} 리그 {new_years}년 거주) "
                f"— 국가대표 선택 시 {club_country}도 고를 수 있습니다.", "event")
    except Exception:
        pass


def _team_at_week35_for(year):
    """주어진 연도의 클럽 시즌 종료(리그 종료) 시점에 내가 소속이던 팀 id.
    시즌 종료 후 이적해도 '리그를 끝까지 함께한 팀'을 우승 귀속 대상으로 본다."""
    from constants import SEASON_PHASES
    league_end_week = SEASON_PHASES["second_half"][1]   # 신규 캘린더: 43주
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT team_id, start_week, start_year, end_week, end_year
               FROM career_entries
               WHERE team_id IS NOT NULL AND team_id<>0
                 AND start_year<=? AND (end_year=0 OR end_year>=?)
               ORDER BY start_week""", (year, year)).fetchall()
        for r in rows:
            sw = r["start_week"] or 0
            if (r["end_year"] or 0) == 0 or (r["end_year"] or 0) > year:
                ew = 52
            else:
                ew = r["end_week"] or 52
            # 시작 연도가 올해면 start_week, 아니면 1주부터로 간주
            sw_eff = sw if (r["start_year"] or year) == year else 1
            if sw_eff <= league_end_week <= ew:
                conn.close()
                return r["team_id"]
    except Exception:
        pass
    conn.close()
    p = get_player() or {}
    return p.get("current_team_id", 0)


def _lock_league_title_after_season(p, year):
    """리그 시즌 종료 다음 주 진입 시: 리그 경기(신규 캘린더 기준 43주)가
    끝났으므로, 그 종료 시점 소속 팀이 1위면 그 즉시 우승을 trophy_log에
    확정한다. (연말까지 안 기다림) 종료 다음 주에 다른 팀으로 이적해도
    종료 시점 소속 팀 기준이라 우승이 누락되지 않는다."""
    champ_tid = _team_at_week35_for(year)
    if not champ_tid:
        return
    # 그 팀에서 그 시즌 5경기 이상 뛰었을 때만(스쳐간 팀 제외).
    #   현재 팀이면 season_matches, 이미 떠난 팀이면 career_entries의 matches 사용.
    matches = 0
    p_now = get_player() or {}
    if champ_tid == p_now.get("current_team_id"):
        matches = p_now.get("season_matches", 0)
    else:
        conn = get_conn()
        try:
            r = conn.execute(
                """SELECT matches FROM career_entries
                   WHERE team_id=? AND start_year<=? AND (end_year=0 OR end_year>=?)
                   ORDER BY start_week DESC LIMIT 1""",
                (champ_tid, year, year)).fetchone()
            if r:
                matches = r["matches"] or 0
        except Exception:
            pass
        conn.close()
    _lock_in_championship(champ_tid, year, matches, min_week=35)


# ══════════════════════════════════════════════════════════════
# 구단 판매 추진 시스템 (2026-07 신설, 신민용+GPT 다회 설계 확정, v5)
# ══════════════════════════════════════════════════════════════

_SALE_PUSH_AWARD_SCORE = {"MVP": -2, "득점왕": -1, "베스트11": -1, "올해의 수비수": -1,
                          "구단 올해의 선수": -1}


def _my_team_rank_category(p) -> str:
    """팀 내 OVR 순위로 에이스/핵심/주전/후보 분류(내 선수급 디테일 대신
    가벼운 OVR 순위만 사용 — AI 팀메이트는 출전비중 등 세부 트래킹이
    없어서 통일된 기준이 OVR뿐이다)."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return "후보"
    conn = get_conn()
    teammates = conn.execute("SELECT ovr FROM ai_players WHERE team_id=?", (tid,)).fetchall()
    my_ovr = p.get("ovr", 50)
    ovrs = sorted([r["ovr"] for r in teammates] + [my_ovr], reverse=True)
    rank = ovrs.index(my_ovr) + 1
    if rank == 1:
        return "에이스"
    if rank <= 3:
        return "핵심"
    if rank <= 6:
        return "주전"
    return "후보"


def _calc_sale_push_score(p, cur_year):
    """5개 조건(+1씩) + 수상 감산으로 판매추진 점수를 계산한다.
    반환: (score, reasons) — reasons는 알림 문구 생성용 사유 키 리스트."""
    score = 0
    reasons = []
    tid = p.get("current_team_id", 0)
    conn = get_conn()

    # 1) 재정 압박 — 이번 시즌 강등 (기존 relegation 체크 로직 재사용)
    if tid:
        team_row = conn.execute("SELECT name FROM teams WHERE id=?", (tid,)).fetchone()
        if team_row:
            rl = conn.execute(
                """SELECT from_tier, to_tier FROM promotion_log
                   WHERE team_name=? AND year=? ORDER BY id DESC LIMIT 1""",
                (team_row["name"], cur_year)).fetchone()
            if rl and rl["to_tier"] > rl["from_tier"]:
                score += 1
                reasons.append("relegation")

    # 2) 계약 만료 임박 — 잔여 1년 이하를 "6개월 이하"의 근사치로 취급
    #    (연 단위 계약 데이터라 주 단위 정밀 계산은 못 하지만, "임박"
    #    판정 목적으로는 충분한 근사치다)
    contract_end = p.get("contract_end_year", 0)
    if contract_end and (contract_end - cur_year) <= 0:
        score += 1
        reasons.append("contract")

    # 3) 전력 외 — 팀 내 순위 "후보" 구간만 해당
    if _my_team_rank_category(p) == "후보":
        score += 1
        reasons.append("bench")

    # 4) 감독과 불화
    if p.get("manager_relation", 50) < 20:
        score += 1
        reasons.append("hostile")

    # 5) 본인 불만 — 기존 transfer_rejection_count(일반 성향) 재사용,
    #    2회 이상 누적이면 "불만 중"으로 본다
    if _effective_rejection_count(p, cur_year) >= 2:
        score += 1
        reasons.append("frustration")

    # 수상 감산 (이번 연도 awards, is_mine=1)
    try:
        awards = conn.execute(
            "SELECT award_type FROM awards WHERE year=? AND is_mine=1", (cur_year,)).fetchall()
        for a in awards:
            at = a["award_type"] or ""
            for key, delta in _SALE_PUSH_AWARD_SCORE.items():
                if key in at:
                    score += delta
    except Exception:
        pass

    return max(0, score), reasons


_SALE_PUSH_REASON_TEXT = {
    "relegation": "구단은 강등으로 재정 압박을 받고 있습니다.",
    "contract": "계약 만료가 임박했습니다.",
    "bench": "감독은 당신을 다음 시즌 계획에 포함하지 않았습니다.",
    "hostile": "감독과의 관계가 좋지 않습니다.",
    "frustration": "최근 이적 요청이 반복해서 거절되어 불만이 쌓여 있습니다.",
}


def _weekly_sale_push_check(p, cur_year, cur_week):
    """매주 호출 — 판매추진 점수를 재계산하고 상태 전환을 처리한다.
    알림은 상태가 실제로 바뀔 때만(평상시→판매추진 전환 시 1회) 띄운다.

    [2026-07 재수정, 신민용 리포트: "판매 추진을 억제한다기보단 구단에서
    보내는 판매 서류를 다 무시한다는 의미인데, 결과가 같게 적용되는
    건가?"] 예전 구현은 allow_club_sale_push가 꺼지면 이 함수 자체를
    건너뛰고 sale_push_active를 즉시 0으로 리셋했다 — 이러면 "구단은
    계속 판매를 추진 중인데 나만 안 본다"가 아니라 "구단이 애초에 판매
    추진 자체를 안 한다"가 되어버려서, 4점+ 강제판매(_check_sale_push_
    forced_sale, 토글 무관하게 발동해야 함)가 sale_push_active=1을
    전제로 하는데 토글이 꺼지는 순간 그 전제 자체가 사라지는 모순이
    있었다. 이제 점수 계산과 상태(sale_push_active 등)는 토글과 완전히
    무관하게 항상 그대로 돌아가고, 토글은 "예고 알림을 보여줄지"에만
    관여한다 — 구단은 서류를 계속 만들고(상태 갱신), 플레이어만 그
    서류를 안 보는(알림 억제) 것.
    """
    score, reasons = _calc_sale_push_score(p, cur_year)
    was_active = bool(p.get("sale_push_active", 0))
    _show_notice = bool(p.get("allow_club_sale_push", 1))

    if score >= 2:
        if not was_active:
            # 평상시 → 판매추진 전환: 예고 알림 1회(토글 켜져 있을 때만 표시)
            update_player(sale_push_active=1, sale_push_start_year=cur_year,
                          sale_push_start_week=cur_week, sale_push_low_score_weeks=0)
            if _show_notice:
                reason_lines = "\n".join(_SALE_PUSH_REASON_TEXT.get(r, "") for r in reasons)
                add_log(f"📋 구단 동향\n구단은 당신의 이적 가능성을 검토하고 있습니다.\n{reason_lines}",
                        "event", cur_year, cur_week)
        else:
            # 계속 판매추진 상태 — 저점수 연속주차 카운터만 리셋
            if p.get("sale_push_low_score_weeks", 0):
                update_player(sale_push_low_score_weeks=0)
            # [2026-07] 유효기간 — 2시즌(104주) 동안 오퍼 없으면 자동 종료
            start_y, start_w = p.get("sale_push_start_year", cur_year), p.get("sale_push_start_week", cur_week)
            weeks_elapsed = (cur_year - start_y) * 52 + (cur_week - start_w)
            last_offer_y = p.get("sale_push_last_offer_year", 0)
            if weeks_elapsed >= 104 and not last_offer_y:
                update_player(sale_push_active=0, sale_push_refused_count=0,
                              sale_push_low_score_weeks=0)
    else:
        if was_active:
            # [2026-07] 종료 디바운스 — 저점수가 연속 4주 유지돼야 실제 종료
            weeks_low = p.get("sale_push_low_score_weeks", 0) + 1
            if weeks_low >= 4:
                update_player(sale_push_active=0, sale_push_refused_count=0,
                              sale_push_low_score_weeks=0)
            else:
                update_player(sale_push_low_score_weeks=weeks_low)


def _check_sale_push_forced_sale(p, cur_year, cur_week):
    """[2026-07 신설, 구단판매추진 v5 설계 확정] 판매추진 점수 4점+ 이면
    후보 팀 하나를 직접 굴려 최소수용금액을 넘는지 본다. receive_
    transfer_offers/allow_club_sale_push 토글과 무관하게 독립적으로
    후보를 찾는다(설계 확정: 오퍼 토글은 이 흐름을 막지 않는다) — 단
    allow_club_sale_push 자체가 꺼져 있으면 애초에 sale_push_active가
    될 수 없으므로 자연히 발동 안 함.

    "이번 판매추진 기간 내 거절 1회는 보장" 원칙을 이 함수 안에서
    자체적으로 관리한다 — join_team의 일반 오퍼 수락/거절 흐름과는
    별개(강제판매 후보는 플레이어가 직접 고르는 오퍼 목록에 안 뜨므로).
    최소수용금액을 넘는 후보를 처음 찾으면 "거절 기록"만 남기고 그냥
    잔류시키고, 그 다음(2번째) 발견 시에 실제로 강제 진행한다.
    """
    if not p.get("sale_push_active"):
        return
    score, reasons = _calc_sale_push_score(p, cur_year)
    if score < 4:
        return

    # [2026-08 버그수정, 신민용 리포트: "승강이랑 팔림이 겹쳤을 때 크래시
    # 났었다"] 이 함수는 대상 팀의 리그/등급 정보를 조회해두고 몇 단계
    # 뒤에(오퍼 평가 → 로그 출력 → join_team) 그 정보로 실제 이적을
    # 실행한다 — 그 사이 승강제 처리가 같은 팀의 tier/league_id를 바꿔
    # 버리면 스냅샷이 어긋난 채로 join_team이 실행될 수 있었다. 함수
    # 전체를 try/except로 감싸 여기서 무슨 예외가 나든 주간 진행
    # (_advance_week) 전체가 죽지 않게 방어하고, 실제 이적 실행 직전에
    # 대상 팀 정보를 한 번 더 신선하게 재조회해서 그 사이 tier가 바뀌었으면
    # (더 이상 1부가 아니게 됐으면) 이번 주는 조용히 건너뛴다(다음 주 재시도).
    try:
        conn = get_conn()
        my_tid = p.get("current_team_id", 0)
        if not my_tid:
            return
        grades = _suitable_grades(p.get("ovr", 40), p.get("agent_grade", "F"))
        row = conn.execute(
            f"""SELECT t.id, t.name, l.id as lid, l.name as lname, l.tier,
                       cn.name as country, cn.flag, cn.grade
                FROM teams t JOIN leagues l ON t.league_id=l.id
                JOIN countries cn ON l.country_id=cn.id
                WHERE t.id != ? AND l.tier = 1 AND cn.grade IN ({",".join("?" * len(grades))})
                ORDER BY RANDOM() LIMIT 1""",
            (my_tid, *grades)).fetchone()
        if not row:
            return

        salary = _calc_salary(row["grade"], row["tier"], p.get("ovr", 40),
                              row["country"], row["name"], year=cur_year, team_id=row["id"],
                              talent_tier=p.get("talent_tier"))
        o = _build_offer(row, row["grade"], row["tier"], salary)
        o["my_grade"] = get_league_grade(p.get("nationality", ""), "C")
        my_team_row = conn.execute(
            """SELECT cn.name as cname, l.tier as tier, cn.grade as grade
               FROM teams t JOIN leagues l ON t.league_id=l.id
               JOIN countries cn ON l.country_id=cn.id WHERE t.id=?""", (my_tid,)).fetchone()
        if my_team_row and my_team_row["tier"] == 1:
            o["my_grade"] = get_league_grade(my_team_row["cname"], my_team_row["grade"])

        decision, detail = evaluate_offer_decision(p, o)
        if decision not in ("accept", "forced_sale"):
            return   # 최소수용금액도 못 넘으면 이번 주는 그냥 넘어감(다음 주 재시도)

        if p.get("sale_push_refused_count", 0) < 1:
            # 이번 판매추진 기간 내 첫 적격 후보 — 강제 안 하고 "거절 1회"만 기록
            update_player(sale_push_refused_count=1)
            add_log(f"📩 {o['team_name']}이(가) 관심을 보였으나, 당신은 잔류를 선택했습니다.",
                    "event", cur_year, cur_week)
            return

        # [2026-08 신설] 실제 실행 직전 재확인 — row는 위에서 이미 몇 단계를
        # 거쳐온 스냅샷이라, 그 사이(오퍼 평가 로직 등에서) 대상 팀이 더 이상
        # 존재하지 않거나 1부가 아니게 됐으면 이번 주는 조용히 건너뛴다.
        fresh = conn.execute(
            """SELECT l.tier FROM teams t JOIN leagues l ON t.league_id=l.id
               WHERE t.id=?""", (o["team_id"],)).fetchone()
        if not fresh or fresh["tier"] != 1:
            return

        reason_lines = "\n".join(f"- {_SALE_PUSH_REASON_TEXT.get(r, '')}" for r in reasons)
        add_log(
            f"🔒 구단 최종 결정\n구단은 선수 잔류를 원했으나, 다음 사유로 이적을 "
            f"최종 결정했습니다.\n{reason_lines}\n\n{o['team_name']} 이적료: {fmt_money(o.get('transfer_fee', 0))}",
            "event", cur_year, cur_week)
        join_team(o["team_id"], o["salary"], transfer_type="오퍼", offer=o)
        update_player(sale_push_active=0, sale_push_refused_count=0, sale_push_low_score_weeks=0)
    except Exception as e:
        print("_check_sale_push_forced_sale 오류(건너뜀):", e)


def _advance_week(p, base_week, n_weeks=4):
    new_week = base_week + n_weeks
    new_year = p["current_year"]
    new_season = p["current_season"]

    if new_week > 52:
        new_week -= 52
        new_year += 1
        new_season += 1
        # [2026-07 신설, 진단용] "52주차→내년 4초" 리포트 대응 — 어느 단계가
        # 실제로 오래 걸리는지 추측 대신 숫자로 확인하기 위한 프로파일링.
        # 콘솔(터미널/exe 콘솔창)에 [PERF] 태그로 단계별 소요시간(초)이
        # 찍힌다. 게임플레이에는 전혀 영향 없음(단순 time.perf_counter()
        # 측정 + print) — 다음에 이 로그를 보고 진짜 병목 지점(예: AI
        # 생애주기 vs 승강제 vs 일정 재생성)을 정확히 짚어서 거기만
        # 추가로 최적화할 수 있다.
        import time as _time_perf
        _t0 = _time_perf.perf_counter()
        # 연도 넘어갈 때 현재 팀 커리어 항목 닫기 (연도별 분리)
        if p.get("current_team_id"):
            _close_career_entry(p, new_year - 1, 52)
        _t1 = _time_perf.perf_counter()
        # [귀화] 거주 연수 갱신 + 자격 체크는 _end_of_season 안에서 처리
        #   (그 시점에 current_team_id가 아직 살아있어 소속국가를 읽을 수 있음)
        _end_of_season(p, new_year-1)
        _t2 = _time_perf.perf_counter()
        # [실시간 전환] 승강제 결과가 반영된 뒤(= teams.league_id 확정 후) 전 세계
        # 모든 리그의 새 시즌 일정을 미리 깔아 둔다. 이후 매주 _sim_all_ai_matches가
        # 실시간으로 채우므로 더 이상 연말에 몰아서 처리할 필요가 없다.
        _generate_all_league_schedules(new_season, new_year)
        _t3 = _time_perf.perf_counter()
        # [2026-08 신설, 파워랭킹 기반] _generate_all_league_schedules() 안에서
        # archive_old_seasons()가 이미 실행돼 league_season_standings에
        # 방금 끝난 시즌(new_year-1) 순위가 확정돼 있고, 그 해 열린 대륙컵/
        # 지역컵/챔피언스~클럽월드컵 대회들도(43~52주 구간에서 이미 끝남)
        # 전부 winner가 채워진 상태다 — 파워랭킹 계산에 필요한 데이터가
        # 전부 갖춰지는 시점이 바로 여기라 여기서 실행한다. 실패해도(예:
        # 아직 한 시즌도 못 채운 극초반 세이브) 연도 전환 자체를 막으면
        # 안 되므로 다른 [PERF] 블록들과 같은 방어적 try/except로 감싼다.
        try:
            import power_ranking
            power_ranking.run_year_end_power_ranking_update(get_conn(), new_year - 1)
        except Exception as e:
            print("파워랭킹 갱신 오류(건너뜀):", e)
        _t3c = _time_perf.perf_counter()
        # [2026-07 추가, 신민용 리포트: "연도전환이 갈수록 느려진다"] SQLite는
        # ANALYZE로 모은 테이블 통계를 바탕으로 실행계획(어느 인덱스를 쓸지)을
        # 정하는데, 이 게임은 한 번도 ANALYZE를 돌린 적이 없어서 테이블이
        # 텅 비어있던 초반 통계(또는 무통계 상태의 대략적 추정치)에 계속
        # 의존했다. 특히 match_results_archive처럼 시즌이 쌓일수록 몇 배씩
        # 커지는 테이블에서는 이 괴리가 갈수록 벌어져 실행계획이 점점
        # 부정확해질 수 있다. PRAGMA optimize는 SQLite 공식 문서가 권장하는
        # 가벼운 주기적 통계 갱신 방법이라(필요할 때만 선별적으로 ANALYZE를
        # 돌림, 매번 전체 재분석하는 비용 없음) 연 1회, 무거운 연도전환
        # 작업 직후에 실행해 다음 해 쿼리 계획을 최신 상태로 유지한다.
        try:
            get_conn().execute("PRAGMA optimize")
        except Exception:
            pass
        _t3b = _time_perf.perf_counter()
        print(f"[PERF] 연도전환 총 {_t3b-_t0:.2f}s "
              f"(커리어정리 {_t1-_t0:.2f}s | _end_of_season {_t2-_t1:.2f}s | "
              f"일정생성 {_t3-_t2:.2f}s | 파워랭킹 {_t3c-_t3:.2f}s | "
              f"PRAGMA optimize {_t3b-_t3c:.2f}s)")
    else:
        # 리그 시즌 종료 주(신규 캘린더: 43주) 다음 주 진입 시: 커리어 스탯
        # 중간 업데이트만 (항목은 닫지 않음 - 연도 변경 시 _close_career_entry가 닫음)
        from constants import SEASON_PHASES as _SP
        _league_end_wk = _SP["second_half"][1]   # 43
        if base_week <= _league_end_wk and new_week >= _league_end_wk + 1:
            if p.get("current_team_id") and p.get("season_matches", 0) > 0:
                _update_career_stats(p, new_year, new_week)
            # [우승 확정] 리그 경기는 _league_end_wk주에 끝나므로, 그 다음 주 진입 시
            #   그 시점 소속 팀이 1위면 그 즉시 우승을 기록한다.
            #   (연말까지 안 기다리고 바로 '성적'에 반영)
            _lock_league_title_after_season(p, new_year)

    # [2026-07 신설, 신민용+GPT 다회 설계 확정: "구단 판매 추진" 시스템]
    # 매주(정확히는 _advance_week가 호출될 때마다) 5개 조건 점수를
    # 재계산 — 오퍼가 매주 뜨는 기존 구조와 맞추기 위해 연말 1회가 아니라
    # 주 단위로 판정한다.
    if p.get("current_team_id"):
        try:
            _weekly_sale_push_check(p, new_year, new_week)
        except Exception as e:
            print("_weekly_sale_push_check 오류(건너뜀):", e)
        _p_after_push = get_player()
        if _p_after_push and _p_after_push.get("current_team_id"):
            _check_sale_push_forced_sale(_p_after_push, new_year, new_week)

    # [최적화] my_player + season_state 갱신을 하나의 커넥션으로 묶어 커밋 2회→1회
    conn_adv = get_conn()
    conn_adv.execute(
        "UPDATE my_player SET current_year=?,current_week=?,current_season=? WHERE id=1",
        (new_year, new_week, new_season))
    rows_ss = conn_adv.execute("SELECT id FROM season_state WHERE id=1").fetchone()
    if rows_ss:
        conn_adv.execute(
            "UPDATE season_state SET current_year=?,current_week=?,current_season=? WHERE id=1",
            (new_year, new_week, new_season))
    else:
        conn_adv.execute(
            "INSERT INTO season_state(id,current_year,current_week,current_season) VALUES(1,?,?,?)",
            (new_year, new_week, new_season))
    conn_adv.commit()
    conn_adv.close()
    # [2026-08 신설] get_state() 캐시에도 같은 값을 바로 반영(무효화 대신
    # patch) — 주/시즌 경계마다 도는 경로라 다음 get_state() 호출들이
    # 다시 DB를 조회하지 않아도 되게 한다.
    if _state_cache is not None:
        _state_cache["current_year"] = new_year
        _state_cache["current_week"] = new_week
        _state_cache["current_season"] = new_season

    # 1주차: 새 시즌 시작 시 완전히 만료된(냉각기 끝난) 오퍼 거절 기록 삭제
    # [2026-08 수정, 신민용 확정] 예전엔 "year < 현재연도"(1년만 지나면
    # 바로 삭제)였는데, 이제 냉각기 규칙이 "거절연도+2까지 차단"이라
    # 그 기준(year+2 <= 현재연도)에 맞춰야 실제로 만료된 것만 지운다.
    # (아직 냉각기가 안 끝난 기록을 여기서 지워버리면 그 팀이 바로 다음
    # 해부터 다시 오퍼를 보낼 수 있게 되는 버그가 생긴다.)
    if new_week == 1:
        conn_cl = get_conn()
        st_yr = conn_cl.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()
        if st_yr:
            conn_cl.execute("DELETE FROM offer_refused WHERE year + 2 <= ?", (st_yr["current_year"],))
            conn_cl.commit()
        conn_cl.close()

    # 겨울 이적시장 시작 1주 전: 그 시점까지의 평점 스냅샷 저장 (겨울 오퍼 확률용)
    from constants import OFFER_ZONES
    _winter_zone_start = OFFER_ZONES[1][0]   # 겨울 이적시장 시작 주차
    if new_week == _winter_zone_start - 1:
        p_snap = get_player()
        if p_snap:
            rc_s = p_snap.get("season_rating_cnt", 0)
            rs_s = p_snap.get("season_rating_sum", 0.0)
            update_player(first_half_rating=round(rs_s/rc_s, 2) if rc_s else 0.0)

    # [2026-07 재수정, 신민용 리포트: "예선 소집이 28주에 오는데 27주에
    # 와야 할듯"] 28주(휴식기 첫 주) 시작 시점에 예선을 생성하면, 27주
    # 일정을 짜는 시점엔 아직 예선 대회가 DB에 없어서 "경기 전날 휴식
    # 강제"(_get_match_for_day(d+1)로 다음날 경기 있는지 미리 확인하는
    # 로직)가 27주 마지막 날(189일)에서 다음날(190일, 예선 첫 경기)을
    # 확인해도 아직 존재하지 않는 대회라 놓친다 — 그 결과 대회 시작
    # 전날 휴식이 안 뜬다. 트리거를 한 주 앞당겨(27주 진입 시점) 생성하면
    # 27주 일정을 짜는 시점에 이미 예선 대회가 존재해서 정상적으로
    # 휴식이 뜬다. 실제 경기 날짜(day190~) 자체는 그대로다 — 데이터가
    # 미리 만들어질 뿐 매치데이는 안 당겨진다.
    from constants import INTL_QUAL_WEEK
    if new_week == INTL_QUAL_WEEK - 1:
        try:
            intl_engine.start_qualifying_if_needed(new_year)
        except Exception as e:
            add_log(f"⚠ 월드컵 예선 생성 오류: {e}", "event")

    # [2026-07 버그수정, 신민용 리포트: "10월29일 클럽월드컵 첫 경기 바로
    # 전날(10월28일)이 강제 휴식이 아니라 그냥 훈련으로 뜬다"] 예선과
    # 완전히 동일한 원인이다(위 INTL_QUAL_WEEK-1 주석 참고) — 대회가
    # INTL_CALLUP_WEEK '그 주 첫날'에 생성되는데, 그날의 바로 전날(오프
    # 시즌 진입 전 마지막 날) 일정을 화면에 그릴 때는 아직 대회 자체가
    # DB에 없어서 "내일 경기 있음" 체크가 못 찾는다. 트리거를 한 주
    # 앞당기면(INTL_CALLUP_WEEK-1 진입 시점) 그 전날 일정을 그릴 때 이미
    # 대회가 존재해서 정상적으로 휴식이 뜬다. 실제 경기 날짜 자체는
    # 그대로다(TOURNAMENT_SCHEDULE_RULES가 day를 결정, 트리거는 그저
    # 데이터를 미리 만들 뿐).
    from constants import INTL_CALLUP_WEEK
    if new_week == INTL_CALLUP_WEEK - 1:
        try:
            intl_engine.start_intl_tournament(new_year)
        except Exception as e:
            add_log(f"⚠ 국제대회 생성 오류: {e}", "event")

    # CL_START_WEEK(8주차) 진입: 클럽 대항전(챔스/유로파/컨퍼런스) 3개
    # 동시 시작 (매년). 출전팀 선발은 직전 시즌(이미 끝난 시즌)의 최종
    # 순위 기준 — continental_qualification이 대륙당 국가 순위 계산을
    # 1번만 하고 세 대회에 나눠준다(2026-08, 예전엔 챔스만 있었음).
    from competition.champions_engine import CL_START_WEEK
    if new_week == CL_START_WEEK:
        try:
            continental_qualification.start_all_continental_competitions(new_year, new_season)
        except Exception as e:
            add_log(f"⚠ 클럽 대항전 생성 오류: {e}", "event")

    # [2026-07 신설] 5주차 진입: 국내 컵대회(FA컵식) 개막 (챔스 시작보다
    # 앞서서, 1~2부 팀 전체로 대진을 짠다).
    if new_week == 5:
        try:
            cup_engine.start_domestic_cup(new_year, new_season)
        except Exception as e:
            add_log(f"⚠ 컵대회 생성 오류: {e}", "event")

    # 새 시즌 시작(1주차 진입 시 1회) 내 리그 + 인접 리그 일정 생성
    # generate_season_schedule는 멱등하지만, 1주차에만 호출해 불필요한 중복 조회 방지
    if new_week == 1 and p.get("current_team_id"):
        p_fresh = get_player()
        if p_fresh and p_fresh.get("current_league_id"):
            _generate_adjacent_schedules(
                p_fresh["current_league_id"], new_season, new_year)


def _calc_clean_sheets_for_player(p, team_id=None, matches=None):
    """소속 팀의 이번 시즌 클린시트 수 (수상 산정용).
    team_id/matches를 넘기면 그 팀·그 팀에서의 출전수 기준으로 계산한다
    (기본값은 현재 팀 — 이적 없이 쓰던 기존 호출부는 그대로 동작).
    [2026-08] 시즌 중 이적 시 시상은 '주 소속팀'(_primary_club_this_season)
    기준으로 계산해야 하므로 이 오버라이드가 필요해졌다."""
    tid = team_id if team_id is not None else p.get("current_team_id", 0)
    if not tid:
        return 0
    conn = get_conn(); c = conn.cursor()
    try:
        season = p.get("current_season", 1)
        _m = matches if matches is not None else p.get("season_matches", 0)
        return _calc_clean_sheets(c, tid, season, matches=_m)
    except Exception:
        return 0
    finally:
        conn.close()


def _estimate_ai_season(ovr, pos, team_avg, league_avg, sub_role=None, full_season_matches=14):
    """AI 선수의 시즌 성적(골/도움/평점)을 추정.
    [설계 변경] 골/도움은 더 이상 OVR로 스케일링하지 않는다 — 신민용 지적:
    "OVR 70이든 90이든 99든 같은 조건이어야 한다"(주전 스트라이커는 실력과
    무관하게 팀 내 슈팅 기회를 비슷하게 가져간다). 포지션별 고정 기준치
    (AWARD_POS_GOAL/ASSIST)에 소속팀 강도(team_avg-league_avg)만 살짝
    반영하고, 실력 차이는 rating(평점)에서만 OVR로 반영한다.
    [2026-07 신설] sub_role(인버티드/클래식윙어 등)별로 골:도움 비율을
    다르게 만든다 — _player_perf에 쓰는 _SUB_ROLE_MATCH_MOD를 그대로
    재사용해 두 계산 경로(내 선수 개별경기 vs AI 시즌추정)가 같은 기준을
    쓰게 한다.

    [2026-07 버그수정, 신민용 지적: "상 기준이 예전 14경기 설계 그대로인
    것 같다"] AWARD_POS_GOAL/ASSIST(constants.py)는 "14경기 풀시즌" 기준
    절대치로 주석에 명시돼 있는데, 이 함수는 실제 리그가 몇 경기짜리인지
    전혀 모른 채 그 절대치를 그대로 썼다. 이후 경기수 비례로 스케일하는
    수정을 넣었었지만(full_season_matches/14 선형 스케일), 이는 "시즌
    전체 기대 골 수"라는 상수의 설계 의도를 "경기당 득점 비율"로 잘못
    재해석한 것이었다 — 그 결과 20팀 리그(38경기)에서 ST가 56골, 30팀
    리그(58경기)에서는 76골까지 추정돼 실제 축구 통계(득점왕 24~35골
    선)를 크게 벗어났다.

    [2026-07 재수정, 신민용 확정: "상수(AWARD_POS_GOAL/ASSIST)는 이미
    밸런싱 끝난 값이니 건드리지 않는다. 대신 선형 스케일을 없애고
    완만한 지수 스케일만 적용한다"] 골/도움은 경기 수보다 선수 능력·
    전술·팀·운의 영향이 훨씬 크므로(반대로 클린시트는 경기 수 영향이
    커서 아래 _estimate_ai_clean_sheets의 38경기 기준 선형 스케일은
    유지), 리그 표준 규모(20팀=38경기)를 기준(scale=1.0)으로 삼아
    경기수 차이는 지수 0.35승만큼만 반영한다 — 28경기 0.90배, 46경기
    1.07배, 58경기 1.16배 정도로, 리그가 아무리 길어져도 득점이
    폭주하지 않는다."""
    scale = (full_season_matches / 38.0) ** 0.35
    g_base = (AWARD_POS_GOAL.get(pos, 1) + (team_avg-league_avg)*0.2) * scale
    a_base = (AWARD_POS_ASSIST.get(pos, 1) + (team_avg-league_avg)*0.1) * scale
    mod = _SUB_ROLE_MATCH_MOD.get((pos, sub_role or ""))
    if mod:
        g_base *= mod.get("g_mult", 1.0)
        a_base *= mod.get("a_mult", 1.0)
    goals = max(0, round(max(0, g_base) * random.uniform(0.8, 1.2)))
    assists = max(0, round(max(0, a_base) * random.uniform(0.8, 1.2)))
    # [2026-07 재보정, 신민용 수비수 QA 확정: "AI 후보(rating~7.8, OVR95)가
    # 실제 시뮬레이션 결과(_player_perf 기반, 같은 OVR95 CB 실측 평균
    # rating~7.2)보다 항상 후하게 나와서 베스트11/MVP/올해의 수비수 후보
    # 경쟁에서 실제 플레이한 내 선수가 구조적으로 밀린다"] OVR→평점
    # 기울기(/20.0)가 실제 엔진의 dominance 압축(고OVR끼리 붙으면 평점이
    # 완만하게만 오르는 구조)을 전혀 반영 못 하고 선형으로 계속 올라가는
    # 게 원인이었다. 실측 CB OVR99·8시즌 평균(rating 7.20)을 기준점으로
    # 기울기를 /20.0→/35.0으로 완화했다 — OVR60→6.0(불변), OVR95→~7.0,
    # OVR99→~7.11(+goals/assists 소폭 가산)로 실측 범위에 맞춘다. 골/도움
    # 가산(goals*0.02/assists*0.015)은 그대로 유지 — 다득점 시즌(ST 등)이
    # 여전히 그만큼 더 높게 평가되는 구조는 보존한다.
    rating = round(6.0 + (ovr-60)/35.0 + goals*0.02 + assists*0.015, 2)
    # [2026-07 버그수정, 신민용 리포트(GPT 분석 인용): "K3/K4처럼 약한
    # 리그에서 평점 5.0~5.8 정도의 평범한 시즌으로도 베스트11을 계속
    # 받는다"] 원인은 이 하한(5.0)이었다 — OVR이 낮은 약체 리그에서는
    # 공식(6.0 + (ovr-60)/20)이 자연스럽게 5.0 밑으로 내려가야 정상인데,
    # 강제로 5.0 바닥에 몰아버리니 그 리그의 AI 후보 전원이 사실상 거의
    # 같은 값(5.0 근방)에 뭉치게 됐다. 그러면 내 선수가 딱히 잘한 것도
    # 아닌 평범한 시즌(5.0~5.8)만 보내도 "바닥에 몰린 AI 풀"보다 쉽게
    # 이겨버려서 베스트11/MVP를 계속 가져가는 현상이 생겼다. 하한을 3.0
    # 으로 낮춰서 약체 리그 AI끼리도 실력 차이(낮은 OVR일수록 더 낮은
    # 평점)가 제대로 갈리게 한다.
    rating = max(3.0, min(9.5, rating))
    return goals, assists, rating


def _estimate_ai_clean_sheets(pos, ovr, team_avg, league_avg, full_season_matches=14):
    """[2026-07 신설] GK AI 후보의 시즌 클린시트 추정 — 팀 평균 대비
    소속팀 전력(team_avg-league_avg)과 GK 본인 OVR을 같이 반영한다.
    실제 골든글러브 판정 기준(season_cs>=10, 38경기 기준)과 같은 축으로
    맞추기 위해, 38경기 기준 클린시트 베이스를 잡고 리그 길이로 스케일."""
    # [2026-07 재보정, 신민용 수비수 QA 확정] base_cs_per_38=11.0은
    # SS등급(전원 엘리트) 환경에서 실측된 실제 CB 클린시트(OVR99·8시즌
    # 평균 7.75)보다 크게 높았다 — team_factor가 (team_avg-league_avg)
    # 기준이라 "모든 팀이 엘리트"인 SS리그에서는 사실상 0에 수렴해
    # 보정이 안 먹히고, ovr_factor만으로 OVR95+ 선수 전원이 CS 13~17에
    # 몰렸다. 실측 평균(7.75)에 맞춰 7.5로 낮춘다.
    base_cs_per_38 = 7.5  # 평균적 GK의 38경기 기준 클린시트 베이스
    team_factor = 1.0 + (team_avg - league_avg) * 0.03      # 강팀일수록 클린시트↑
    ovr_factor = 1.0 + max(0, ovr - 70) * 0.01                # GK 본인 실력도 소폭 반영
    scale = full_season_matches / 38.0
    cs = base_cs_per_38 * team_factor * ovr_factor * scale * random.uniform(0.8, 1.2)
    return max(0, round(cs))


def _cap_additive_bonus(raw_bonus: float, base_score: float, cap_ratio: float = 0.10) -> float:
    """[2026-07 신설, GPT 2차 피드백 반영: "빅게임 보너스도 상한이 있어야
    한 경기 때문에 MVP가 뒤집히는 걸 막을 수 있다"] 결승/준결승 같은
    빅게임 활약을 '가산'으로 얹을 때, 기준 점수(base_score)의 cap_ratio
    (기본 10%)를 넘지 못하게 상한을 씌운다. 원본 GPT 제안은 고정 숫자
    (+0.3 등)였지만, 그러면 결승 평점 6점과 9점이 같은 보너스를 받는
    문제가 있어 '실제 빅게임 기록에서 계산한 값에 상한만 씌우는' 방식으로
    수정했다."""
    if base_score <= 0 or raw_bonus <= 0:
        return 0.0
    cap = base_score * cap_ratio
    return max(0.0, min(raw_bonus, cap))


def _gk_quality_ok(saves: int, conceded: int, matches: int, full_season_matches: float,
                    min_play_ratio: float = 0.35,
                    min_save_pct: float = 0.65, max_ga_rate: float = 1.3) -> bool:
    """[2026-07 신설, GPT 피드백: "골든글러브가 클린시트 개수만 본다,
    세이브율·평균실점도 반영해야 한다"] AI 골키퍼는 클린시트 추정치만
    있고 세이브·실점 자체를 추정하지 않으므로, 세이브율·평균실점을 AI와
    직접 비교할 방법이 없다 — 그래서 'AI 대비 클린시트 1위'라는 기존
    비교 기준은 그대로 두고, 내 선수 자신의 세이브율·평균실점이 최소
    품질 기준을 넘는지를 별도 게이트로 추가한다. 세이브율만 보면 "많이
    맞고 많이 막아서 세이브율만 높은 약팀 골키퍼"가 유리해질 수 있어
    평균 실점 상한도 같이 요구한다. 표본이 너무 적으면(최소 출전 비율
    미달) 신뢰할 수 없으므로 무조건 탈락시킨다."""
    min_m = max(6, round(min_play_ratio * full_season_matches))
    if matches < min_m:
        return False
    save_pct = saves / (saves + conceded) if (saves + conceded) > 0 else 0.0
    ga_rate = conceded / matches if matches > 0 else 999.0
    return save_pct >= min_save_pct and ga_rate <= max_ga_rate


def _team_rank_mult(team_rank: int, n_teams: int) -> float:
    """[2026-07 신설, 신민용 확정: "꼴찌 팀 센터백이 리그 베스트11인 건
    현실에서 상당히 드문 사례"] 베스트11/MVP 점수가 순수 개인 활약(평점·
    클린시트·골·도움)만 보고 팀 성적은 전혀 안 봤다 — 실제 축구는 개인
    활약 80~90% + 팀 성적 10~20% 정도가 항상 섞인다(같은 실점이어도
    "우승팀 수비수"와 "꼴찌팀 수비수"는 심사에서 다르게 보임). 큰 페널티
    (예: 1위 +20%, 꼴찌 -20%)는 반대 — 그러면 개인 성적 좋은 선수가
    팀 사정만으로 떨어지는 경우가 너무 많아진다. 대신 순위를 4분위로
    나눠 아주 작은 보정만 준다 — "당락선에 있는 애매한 선수만 걸러내는"
    정도. 좋은 시즌(예: 평점 7.2)은 이 보정을 받아도 여전히 수상권이고,
    애매한 시즌(예: 평점 6.7 + 꼴찌팀)만 자연스럽게 탈락하게 된다."""
    if n_teams <= 1:
        return 1.0
    pct = team_rank / n_teams   # 0에 가까움=최상위, 1에 가까움=최하위
    if pct <= 0.25:
        return 1.03
    elif pct <= 0.5:
        return 1.01
    elif pct <= 0.75:
        return 1.00
    else:
        return 0.97


def _league_team_ranks(c, league_id) -> dict:
    """그 리그 팀들의 순위(1부터)를 {team_id: rank} 딕셔너리로 반환.
    승점(승*3+무) → 득실차 순으로 정렬 — 승강제 판정과 별개로 상 심사용
    '대략적인' 순위라 match_results 재계산 없이 teams 누적치를 그대로 쓴다."""
    rows = c.execute(
        """SELECT id, wins, draws, losses, goals_for, goals_against
           FROM teams WHERE league_id=?""", (league_id,)).fetchall()
    ranked = sorted(rows, key=lambda r: (-(r["wins"]*3 + r["draws"]),
                                          -(r["goals_for"] - r["goals_against"])))
    return {r["id"]: i + 1 for i, r in enumerate(ranked)}, len(ranked)


def _position_award_score(pos, goals, assists, rating, ovr, cs=0):
    """[2026-07 신설, 신민용 지적: "MVP가 무조건 공격수 유리한 공식(_best11_score)을
    전 포지션에 그대로 쓴다"] MVP/월드컵 골든볼/챔스 시즌MVP처럼 '포지션 무관,
    그 대회 최고의 선수 1명'을 뽑는 상에 쓰는 포지션별 가중 점수식.
    _best11_score(포워드 전용, 골 가중치 높음)와 달리, 포지션마다 실제로
    영향력을 만드는 경로가 다르다는 걸 반영한다:
      - GK: 클린시트·평점이 압도적 비중(골/도움은 사실상 없는 포지션)
      - DF: 평점 비중을 높이고, 공격포인트는 보조적으로만
      - MF: 어시스트(창조력) 비중을 골보다 높게
      - FW: 기존 _best11_score와 동일(골 비중 최고)
    """
    if pos in GK_POS:
        return cs * 3.0 + rating * 8.0 + ovr * 0.3
    if pos in DF_POS:
        return goals * 1.0 + assists * 1.5 + rating * 7.0 + ovr * 0.3
    if pos in MF_POS:
        return goals * 1.5 + assists * 2.0 + rating * 6.0 + ovr * 0.3
    return goals * 2.0 + assists * 1.0 + rating * 5.0 + ovr * 0.3  # FW/기타


def _club_award_score(pos, goals, assists, rating, ovr, cs=0):
    """[2026-07 신설, 신민용 확정] 구단 올해의 선수(Club Player of the
    Year) 전용 점수식. 리그 MVP(_position_award_score)와 포지션별 계수를
    그대로 쓰면 "리그 최고"와 "구단 내 최고"가 사실상 같은 기준이 되어
    성격이 겹친다 — 이 상은 "그 팀에서 가장 중요했던 선수"라는 더 넓은
    개념이라, 포지션 구분 없이 단일 공식으로 골/도움/평점/OVR/클린시트를
    고르게 반영해서 공격수 쏠림을 줄이고 수비수·GK도 자기 팀에서는
    핵심으로 평가받을 여지를 준다. 후보 풀 자체가 이미 내 팀 로스터로
    좁혀져 있으므로(club_pool), 점수식은 MVP보다 단순하게 유지한다.
    """
    return rating * 50.0 + goals * 2.0 + assists * 2.0 + ovr * 0.5 + cs * 1.0


def _evaluate_extra_awards(pool, my_pos, my_age=25, weight_fn=None, young_age_cutoff=21):
    """[2026-07 신설] 베스트11/영플레이어 공용 판정 — 리그 상 로직과 동일한
    4그룹(GK/DF/MF/FW) 점수식을 그대로 재사용한다. pool의 각 원소는
    {"position","goals","assists","rating","ovr","cs","age","is_mine"} 키를
    가져야 한다. 반환: 내가 받은 상 이름 리스트(예: ["베스트11","영플레이어"]).

    [2026-07 확장, 신민용 확정: "대회 MVP/베스트11에 팀 성적(진출 라운드)을
    반영하자"] weight_fn을 주면 각 후보의 점수에 그 팀/국가의 '진출 라운드
    가중치'를 곱해서 비교한다. None이면(기본값 — 리그 상 등 원래 호출부)
    기존과 완전히 동일하게 동작한다. 후보를 아예 자르는 게 아니라 점수에
    곱하는 방식이라, 우승 못 해도 개인 활약이 압도적이면 여전히 역전
    가능하다(예: 2010 월드컵 포를란류 케이스)."""
    def _w(x):
        return weight_fn(x) if weight_fn else 1.0
    won = []
    if my_pos in GK_POS:
        group = [x for x in pool if x["position"] in GK_POS]
        if group:
            best = max(group, key=lambda x: _best11_score_gk_df(x.get("cs", 0), x["rating"], x["ovr"]) * _w(x))
            if best["is_mine"]:
                won.append("베스트11")
    elif my_pos in DF_POS:
        group = [x for x in pool if x["position"] in DF_POS]
        if group:
            best = max(group, key=lambda x: _best11_score_gk_df(x.get("cs", 0), x["rating"], x["ovr"]) * _w(x))
            if best["is_mine"]:
                won.append("베스트11")
    elif my_pos in MF_POS:
        group = [x for x in pool if x["position"] in MF_POS]
        if group:
            best = max(group, key=lambda x: _best11_score_mf(x["goals"], x["assists"], x["rating"], x["ovr"]) * _w(x))
            if best["is_mine"]:
                won.append("베스트11")
    elif my_pos in FW_POS:
        group = [x for x in pool if x["position"] in FW_POS]
        if group:
            best = max(group, key=lambda x: _best11_score(x["goals"], x["assists"], x["rating"], x["ovr"]) * _w(x))
            if best["is_mine"]:
                won.append("베스트11")

    young_cands = [x for x in pool if x.get("age", 30) <= young_age_cutoff]
    if young_cands:
        best_young = max(young_cands, key=lambda x: _position_award_score(
            x["position"], x["goals"], x["assists"], x["rating"], x["ovr"], x.get("cs", 0)) * _w(x))
        if best_young["is_mine"]:
            won.append("영플레이어")
    return won


def _best11_score(goals, assists, rating, ovr):
    """FW(포워드) 포지션용 점수식 — 골 가중치 높음"""
    return goals*2 + assists*1.0 + rating*5 + ovr*0.3


def _best11_score_gk_df(clean_sheets, rating, ovr):
    """GK/DF(골키퍼, 수비수) 포지션용 점수식 — 클린시트 가중치"""
    return clean_sheets*2.5 + rating*5 + ovr*0.3


def _best11_score_mf(goals, assists, rating, ovr):
    """MF(미드필더) 포지션용 점수식 — 골과 도움 균형"""
    return goals*1.5 + assists*1.5 + rating*5 + ovr*0.3


def _collect_league_candidates(c, league_id, exclude_my_team=None, full_season_matches=14):
    """리그 내 모든 팀의 AI 공격 포지션 선수들 시즌 성적 추정 → 후보 리스트.

    [최적화] 기존엔 팀마다 ai_players를 2번씩(전체 OVR 집계용 + 공격수 목록용)
      조회해 20팀 리그면 40+ 쿼리(N+1)가 돌았다. teams JOIN으로 팀별 평균 OVR을
      1쿼리에, 공격수 목록을 1쿼리에 모아 총 2쿼리로 줄였다. 결과·계산은 동일.
    """
    # 팀별 평균 OVR + 리그 평균을 단일 JOIN 집계로.
    team_rows = c.execute(
        """SELECT t.id AS tid, AVG(ap.ovr) AS avg_ovr, COUNT(ap.id) AS n,
                  SUM(ap.ovr) AS sum_ovr
           FROM teams t LEFT JOIN ai_players ap ON ap.team_id=t.id
           WHERE t.league_id=?
           GROUP BY t.id""", (league_id,)).fetchall()
    if not team_rows:
        return [], 50.0

    team_avg = {}
    tot_sum = 0
    tot_n = 0
    for r in team_rows:
        if r["n"]:
            team_avg[r["tid"]] = r["avg_ovr"]
            tot_sum += r["sum_ovr"] or 0
            tot_n   += r["n"]
    league_avg = (tot_sum / tot_n) if tot_n else 50.0

    # [2026-07 확장, 신민용 지적: "MVP가 무조건 공격수 유리한 공식을 전
    # 포지션에 그대로 쓴다"] 예전엔 ATTACK_POS(공격 6포지션)만 후보로
    # 모아서, 수비수·GK는 애초에 AI 경쟁 풀에 존재하지도 않았다 —
    # MVP 후보 비교 자체가 "내가 수비수/GK면 무조건 공격수 AI들이랑
    # 붙어서 진다"는 구조였다. 전 포지션을 다 모으고, 포지션별 가중치는
    # 아래 _position_award_score()가 따로 처리한다.
    ALL_AWARD_POS = GK_POS + DF_POS + MF_POS + FW_POS
    placeholders = ",".join("?" for _ in ALL_AWARD_POS)
    atk_rows = c.execute(
        """SELECT ap.team_id AS tid, ap.name, ap.position, ap.ovr, ap.sub_role
           FROM ai_players ap JOIN teams t ON ap.team_id=t.id
           WHERE t.league_id=? AND ap.position IN ({})""".format(placeholders),
        (league_id, *ALL_AWARD_POS)).fetchall()

    cands = []
    for r in atk_rows:
        tavg = team_avg.get(r["tid"], league_avg)
        g, a, rt = _estimate_ai_season(r["ovr"], r["position"], tavg, league_avg, r["sub_role"],
                                        full_season_matches=full_season_matches)
        cs = _estimate_ai_clean_sheets(r["position"], r["ovr"], tavg, league_avg,
                                        full_season_matches) if r["position"] in GK_POS + DF_POS else 0
        cands.append({
            "name": r["name"], "position": r["position"], "ovr": r["ovr"],
            "goals": g, "assists": a, "rating": rt, "is_mine": False, "cs": cs,
            "matches": full_season_matches, "team_id": r["tid"],
        })
    return cands, league_avg


def _zamora_tally(c, p, year, league_id, lname, live_matches, live_ga):
    """사모라상 산정용: '같은 시즌·같은 리그'에서 뛴 출전수·실점을 합산한다.

    [규칙] 시즌 중 이적해도, 이적 전후 리그가 같으면(예: 토트넘→첼시 모두
      프리미어리그) 두 팀의 리그 출전·실점을 한 시즌으로 합쳐서 사모라상을
      심사한다. 다른 리그로 옮기면(예: 토트넘→레알) 합치지 않는다.

      합산 소스는 career_entries(팀별로 matches·goals_against·league_name·
      start_year 저장). 현재 팀의 라이브 값(live_matches/live_ga)을 베이스로,
      같은 해(start_year==year)·같은 리그명(lname)인 '다른 팀(닫힌)' 항목을 더한다.

      ※ GA 폴백: 구버전 데이터는 career_entries.goals_against 가 0으로 누락돼
        있을 수 있다. matches>0 인데 GA==0 인 GK 항목은 match_results 에서
        그 팀이 그 시즌 리그에서 먹은 골을 재계산해 보정한다(출전수로 캡).

    반환: (총출전경기, 총실점)
    """
    total_m  = int(live_matches or 0)
    total_ga = int(live_ga or 0)
    cur_tid  = p.get("current_team_id", 0)
    try:
        rows = c.execute(
            """SELECT team_id, matches, goals_against
               FROM career_entries
               WHERE start_year=? AND league_name=? AND team_id<>? AND matches>0""",
            (year, lname, cur_tid)).fetchall()
    except Exception:
        rows = []
    for r in rows:
        m  = int(r["matches"] or 0)
        ga = int(r["goals_against"] or 0)
        # GA 누락 보정: match_results 에서 해당 팀이 그 리그·시즌에 먹은 골 합
        if ga == 0 and m > 0:
            try:
                q = c.execute(
                    """SELECT COALESCE(SUM(CASE
                            WHEN home_team_id=? THEN away_score
                            WHEN away_team_id=? THEN home_score END),0) AS ga,
                              COUNT(*) AS gp
                       FROM match_results
                       WHERE league_id=? AND home_score>=0
                         AND (home_team_id=? OR away_team_id=?)""",
                    (r["team_id"], r["team_id"], league_id,
                     r["team_id"], r["team_id"])).fetchone()
                team_ga = int(q["ga"] or 0) if q else 0
                team_gp = int(q["gp"] or 0) if q else 0
                # 팀 전체 실점을 선수 출전 경기 비율로 귀속(안 뛴 경기 실점 제외).
                if team_gp > 0:
                    ga = round(team_ga * min(m, team_gp) / team_gp)
            except Exception:
                ga = 0
        total_m  += m
        total_ga += ga
    return total_m, total_ga


def _league_full_season_matches(p, team_id=None) -> int:
    """[2026-07 추가] 이 선수가 뛰는 리그의 실제 '풀시즌 팀당 경기 수'를
    구한다(팀 수 + legs_for_team_count 다전제 반영, 8~30팀 리그마다
    14~58경기로 제각각). 개인 수상 최소 출전 기준(아래 '최소 10경기'
    게이트, 사모라상 등)이 전부 리그 규모와 무관하게 고정값이었던 걸
    이 헬퍼로 통일해서 스케일한다 — _process_awards 내부에서도 득점왕/
    도움왕/발롱도르 기준 계산에 동일한 로직을 쓴다(중복 계산이지만
    가벼운 COUNT 쿼리 하나라 성능에 영향 없음).
    [2026-08] team_id를 넘기면 그 팀 기준으로 계산한다(기본값은 현재
    팀) — 시즌 중 이적한 선수의 시상 게이트는 '주 소속팀' 리그 규모를
    봐야 하므로 이 오버라이드가 필요하다."""
    tid = team_id if team_id is not None else p.get("current_team_id", 0)
    if not tid:
        return 38  # 무소속 등 예외 상황의 안전 폴백(20팀 2전제 기준값)
    conn = get_conn()
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM teams
           WHERE league_id = (SELECT league_id FROM teams WHERE id=?)""", (tid,)).fetchone()
    conn.close()
    n_teams = row["n"] if row and row["n"] else 20
    legs = legs_for_team_count(n_teams)
    return max(1, (n_teams - 1) * legs)


def team_matches_played_in_window(team_id: int, league_name: str,
                                   start_year, start_week, end_year, end_week):
    """[버그수정 2026-07, 신민용 리포트: "전체 이력이 33/3처럼 말이 안 되는
    분모로 나온다 / 팀 이력에서 분모(출전 X/Y의 Y)가 통째로 빠진다"]

    예전 버전은 team_name으로 '지금 이 순간' teams 테이블에서 그 팀을
    다시 찾았는데, 그 팀이 그 스탠트 이후 승격/강등으로 다른 리그(다른
    league_id)로 옮겨갔으면 "league_id=(그 스탠트 당시 리그) AND
    name=팀명" 조건에 걸리는 행이 하나도 없어서 조용히 실패했다(None
    반환 → 분모가 통째로 사라지거나, 다른 대회 출전만 분모에 남아
    "33/3"처럼 분자보다 작은 분모가 나옴). team_id는 승격/강등과 무관하게
    영구히 같은 값이고 career_entries에 이미 저장돼 있으므로, 팀을 다시
    찾을 필요 없이 이 함수는 그 team_id를 그대로 받아서 쓴다 — league_name은
    '그 스탠트 당시 어느 리그(=몇 부)에서 뛰었는지' 필터링 용도로만 쓴다.

    이 함수는 대신 "내가 그 팀 소속이었던 기간(start~end) 동안 그 팀이
    실제로 치른 리그 경기 수"를 센다 — 내가 직접 뛰었는지와 무관하게
    (벤치/부상/출전정지로 못 뛴 경기도) 그 기간에 열렸으면 전부 포함한다.
    분자(matches, 내가 실제로 뛴 경기)와 같은 기준(그 팀 소속 기간)으로
    분모를 맞춰야 "출전 X/Y"가 왜곡 없이 그 스탠트 안에서의 실제 출전율을
    보여준다.

    end_year가 0/미정이면(아직 그 팀 소속 = 현재 진행 중인 스탠트) 지금
    게임 시점까지로 계산한다."""
    if not team_id or not league_name or not start_year:
        return None
    conn = get_conn()
    lrow = conn.execute("SELECT id FROM leagues WHERE name=? LIMIT 1", (league_name,)).fetchone()
    if not lrow:
        conn.close()
        return None
    lid = lrow["id"]
    tid = team_id

    ey, ew = end_year, end_week
    if not ey:
        st = get_state()
        if st:
            ey, ew = st.get("current_year", start_year), st.get("current_week", 52)
        else:
            ey, ew = start_year, 52

    def _count(table):
        return conn.execute(
            f"""SELECT COUNT(*) AS n FROM {table}
                WHERE league_id=? AND (home_team_id=? OR away_team_id=?)
                  AND home_score>=0
                  AND (year > ? OR (year = ? AND week >= ?))
                  AND (year < ? OR (year = ? AND week <= ?))""",
            (lid, tid, tid, start_year, start_year, start_week or 1,
             ey, ey, ew or 52)).fetchone()["n"]

    try:
        n = _count("match_results") + _count("match_results_archive")
    except Exception:
        n = None
    conn.close()
    return n if n else None


def get_club_other_competitions_summary(team_id, start_year, end_year):
    """[2026-07 신설, 신민용 확정: "팀 이력에 리그 경기랑 클럽 경기가 섞여서
    헷갈린다 — 위(팀 이력 한 줄)는 리그 경기만, 아래는 리그 외 모든 경기
    (컵대회+챔피언스리그+클럽월드컵 합산)를 따로 보여주자"] career_entries
    한 줄(리그 전용 — season_goals 등은 _simulate_match에서만 누적되므로
    이미 리그 전용이다)에 이어서, 그 팀 소속 기간 동안 컵대회/챔피언스리그/
    클럽월드컵에서 뛴 경기를 전부 합산해 반환한다. team_id 기준으로 직접
    조회하므로(현재 소속팀 여부와 무관), 은퇴 후 오래된 스탠트를 조회해도
    정확하다 — get_my_cl_matches() 등 화면용 헬퍼는 "현재 소속팀"을
    기준으로 홈/원정을 판정해 과거 스탠트에서는 부정확할 수 있는 것과
    다르다.

    반환: {"matches":int,"goals":int,"assists":int,"avg_rating":float or None}
    (경기가 하나도 없으면 matches=0, avg_rating=None)"""
    if not team_id:
        return {"matches": 0, "goals": 0, "assists": 0, "avg_rating": None}
    ey = end_year if end_year else 9999
    conn = get_conn()
    total = {"matches": 0, "goals": 0, "assists": 0}
    rating_sum, rating_cnt = 0.0, 0
    for m_table, t_table in (("cup_matches", "cup_tournaments"),
                             ("cl_matches", "cl_tournaments"),
                             ("el_matches", "el_tournaments"),
                             ("ecl_matches", "ecl_tournaments"),
                             ("sc_matches", "sc_tournaments"),
                             ("cwc_matches", "cwc_tournaments")):
        rows = conn.execute(
            f"""SELECT m.my_goals, m.my_assists, m.my_rating FROM {m_table} m
                JOIN {t_table} t ON m.tournament_id = t.id
                WHERE (m.home_team_id=? OR m.away_team_id=?) AND m.my_played=1
                  AND t.year BETWEEN ? AND ?""",
            (team_id, team_id, start_year, ey)).fetchall()
        for r in rows:
            total["matches"] += 1
            total["goals"] += r["my_goals"] or 0
            total["assists"] += r["my_assists"] or 0
            if r["my_rating"]:
                rating_sum += r["my_rating"]
                rating_cnt += 1
    conn.close()
    total["avg_rating"] = round(rating_sum / rating_cnt, 2) if rating_cnt else None
    return total


def league_total_games_by_name(league_name: str):
    """[2026-07 추가] 리그 이름으로 그 리그의 '풀시즌 팀당 경기 수'를 구한다
    (커리어 이력처럼 예전에 뛰었던 리그 — 지금 내 팀 기준이 아닌 경우용).
    이제 리그마다 팀 수·다전제가 달라 풀시즌 경기 수가 14~58경기로 다
    다르므로, '출전 26경기'만 보면 그게 시즌을 거의 다 뛴 건지 절반만
    뛴 건지 알 수 없다 — 분모(그 리그 풀시즌 경기 수)를 같이 보여주기
    위한 조회 함수. 못 찾으면 None(호출부에서 분모 없이 표시)."""
    if not league_name:
        return None
    conn = get_conn()
    row = conn.execute("SELECT id FROM leagues WHERE name=? LIMIT 1", (league_name,)).fetchone()
    if not row:
        conn.close()
        return None
    n_teams = conn.execute(
        "SELECT COUNT(*) AS n FROM teams WHERE league_id=?", (row["id"],)).fetchone()["n"]
    conn.close()
    if n_teams < 2:
        return None
    legs = legs_for_team_count(n_teams)
    return max(1, (n_teams - 1) * legs)


def get_full_history_extras_for_period(team_id, nationality, start_year, end_year):
    """[2026-07 신설 → 재작성 → 재확장, 신민용 요청: "테이블 컬럼에 추가해서
    ㄱㄱ"] '전체 이력' 탭/표용 리그 외(컵+챔스+클럽월드컵+국가대표) 합산.
    처음엔 컵대회/클럽월드컵에 슈팅·유효슈팅·기회창출·드리블·차단·패스%가
    저장 안 된다고 판단했었는데 다시 보니 챔스/국제전은 이미 저장하고
    있었고(cl_matches/intl_matches에 my_shots 등 컬럼 존재), _player_perf가
    계산까지 해준 detail을 cup_engine.py/club_world_cup_engine.py만 버리고
    있었다 — cup_matches/cwc_matches에도 같은 컬럼을 추가하고 그 두 엔진의
    UPDATE문도 detail을 저장하도록 고쳐서, 이제 4개 대회 전부 동일하게
    집계할 수 있다.
    반환 필드: matches_available/matches_played, goals/assists,
    rating_sum/rating_cnt(가중평균용), saves/goals_against/clean_sheets,
    shots/shots_on/key_passes/dribbles/blocks, pass_acc_sum/pass_acc_cnt
    (평균 낼 때 나누는 용도 — 0인 경기까지 나누면 왜곡되므로 값 있는
    경기 수로만 나눈다), red_cards(컵+챔스+클럽월드컵+국가대표 합산
    퇴장 횟수 — my_absence_reason='red_card'인 경기 수를 셈)."""
    conn = get_conn(); c = conn.cursor()
    avail_m = played_m = goals = assists = 0
    rating_sum = rating_cnt = 0.0
    saves = goals_against = clean_sheets = 0
    shots = shots_on = key_passes = dribbles = blocks = 0
    pass_acc_sum = 0.0; pass_acc_cnt = 0
    # [2026-08 신설, 신민용 리포트: "전체 이력엔 그 해 컵대회/챔스/월드컵
    # 등 대회 레드카드가 안 잡힌다"] my_absence_reason='red_card'는
    # 퇴장이 발생한 바로 그 경기 행에 이미 저장돼 있다(cup_engine.py 등
    # _apply_red_card_dismissal 호출부에서 my_played=1과 함께 기록) —
    # my_player.total_red_cards_all(커리어 통산 합계)은 이미 정확했지만,
    # '전체 이력' 표(기간별 합산)는 이 필드를 아예 안 읽고 있었다.
    red_cards = 0
    # [2026-07 신설, 신민용 리포트: "전체 이력에 승패 표시가 사라졌어"]
    # 팀 이력의 승무패(e.wins/draws/losses)는 '내가 뛴 경기'가 아니라
    # '그 시즌 그 팀의 전적'(match_results 전체)이다 — 여기서도 같은
    # 원칙으로, 컵/챔스/클럽WC는 내가 안 뛴 경기까지 포함해 그 팀의
    # 승무패를 세고, 국가대표도 마찬가지로 그 나라 대표팀 전적을 센다.
    team_w = team_d = team_l = 0

    for tbl, tour_tbl in (("cup_matches", "cup_tournaments"),
                          ("cl_matches", "cl_tournaments"),
                          ("el_matches", "el_tournaments"),
                          ("ecl_matches", "ecl_tournaments"),
                          ("sc_matches", "sc_tournaments"),
                          ("cwc_matches", "cwc_tournaments")):
        avail_row = c.execute(
            f"""SELECT COUNT(*) n FROM {tbl} m JOIN {tour_tbl} t ON m.tournament_id=t.id
                WHERE (m.home_team_id=? OR m.away_team_id=?) AND t.year BETWEEN ? AND ?""",
            (team_id, team_id, start_year, end_year)).fetchone()
        avail_m += avail_row["n"] or 0

        wdl_rows = c.execute(
            f"""SELECT m.home_team_id, m.home_score, m.away_score
                FROM {tbl} m JOIN {tour_tbl} t ON m.tournament_id=t.id
                WHERE m.home_score>=0 AND (m.home_team_id=? OR m.away_team_id=?)
                  AND t.year BETWEEN ? AND ?""",
            (team_id, team_id, start_year, end_year)).fetchall()
        for wr in wdl_rows:
            my_score = wr["home_score"] if wr["home_team_id"] == team_id else wr["away_score"]
            opp_score = wr["away_score"] if wr["home_team_id"] == team_id else wr["home_score"]
            if my_score > opp_score: team_w += 1
            elif my_score == opp_score: team_d += 1
            else: team_l += 1

        rows = c.execute(
            f"""SELECT m.home_team_id, m.home_score, m.away_score, m.my_goals,
                       m.my_assists, m.my_saves, m.my_rating, m.my_shots,
                       m.my_shots_on, m.my_key_passes, m.my_dribbles,
                       m.my_blocks, m.my_pass_acc, m.my_absence_reason
                FROM {tbl} m JOIN {tour_tbl} t ON m.tournament_id=t.id
                WHERE m.my_played=1 AND (m.home_team_id=? OR m.away_team_id=?)
                  AND t.year BETWEEN ? AND ?""",
            (team_id, team_id, start_year, end_year)).fetchall()
        for r in rows:
            played_m += 1
            goals += r["my_goals"] or 0
            assists += r["my_assists"] or 0
            if r["my_rating"]:
                rating_sum += r["my_rating"]; rating_cnt += 1
            saves += r["my_saves"] or 0
            conceded = r["away_score"] if r["home_team_id"] == team_id else r["home_score"]
            conceded = max(0, conceded or 0)
            goals_against += conceded
            if conceded == 0:
                clean_sheets += 1
            shots += r["my_shots"] or 0
            shots_on += r["my_shots_on"] or 0
            key_passes += r["my_key_passes"] or 0
            dribbles += r["my_dribbles"] or 0
            blocks += r["my_blocks"] or 0
            if r["my_pass_acc"]:
                pass_acc_sum += r["my_pass_acc"]; pass_acc_cnt += 1
            if r["my_absence_reason"] == "red_card":
                red_cards += 1

    if nationality:
        avail_row = c.execute(
            """SELECT COUNT(*) n FROM intl_matches m JOIN intl_tournaments t ON m.tournament_id=t.id
               WHERE (m.home=? OR m.away=?) AND t.year BETWEEN ? AND ?""",
            (nationality, nationality, start_year, end_year)).fetchone()
        avail_m += avail_row["n"] or 0

        wdl_rows = c.execute(
            """SELECT m.home, m.home_score, m.away_score
               FROM intl_matches m JOIN intl_tournaments t ON m.tournament_id=t.id
               WHERE m.home_score>=0 AND (m.home=? OR m.away=?)
                 AND t.year BETWEEN ? AND ?""",
            (nationality, nationality, start_year, end_year)).fetchall()
        for wr in wdl_rows:
            my_score = wr["home_score"] if wr["home"] == nationality else wr["away_score"]
            opp_score = wr["away_score"] if wr["home"] == nationality else wr["home_score"]
            if my_score > opp_score: team_w += 1
            elif my_score == opp_score: team_d += 1
            else: team_l += 1

        rows = c.execute(
            """SELECT m.home, m.home_score, m.away_score, m.my_goals,
                      m.my_assists, m.my_saves, m.my_rating, m.my_shots,
                      m.my_shots_on, m.my_key_passes, m.my_dribbles,
                      m.my_blocks, m.my_pass_acc, m.my_absence_reason
               FROM intl_matches m JOIN intl_tournaments t ON m.tournament_id=t.id
               WHERE m.my_played=1 AND (m.home=? OR m.away=?)
                 AND t.year BETWEEN ? AND ?""",
            (nationality, nationality, start_year, end_year)).fetchall()
        for r in rows:
            played_m += 1
            goals += r["my_goals"] or 0
            assists += r["my_assists"] or 0
            if r["my_rating"]:
                rating_sum += r["my_rating"]; rating_cnt += 1
            saves += r["my_saves"] or 0
            conceded = r["away_score"] if r["home"] == nationality else r["home_score"]
            conceded = max(0, conceded or 0)
            goals_against += conceded
            if conceded == 0:
                clean_sheets += 1
            shots += r["my_shots"] or 0
            shots_on += r["my_shots_on"] or 0
            key_passes += r["my_key_passes"] or 0
            dribbles += r["my_dribbles"] or 0
            blocks += r["my_blocks"] or 0
            if r["my_pass_acc"]:
                pass_acc_sum += r["my_pass_acc"]; pass_acc_cnt += 1
            if r["my_absence_reason"] == "red_card":
                red_cards += 1
    conn.close()
    return {
        "matches_available": avail_m, "matches_played": played_m,
        "goals": goals, "assists": assists,
        "rating_sum": rating_sum, "rating_cnt": rating_cnt,
        "saves": saves, "goals_against": goals_against, "clean_sheets": clean_sheets,
        "shots": shots, "shots_on": shots_on, "key_passes": key_passes,
        "dribbles": dribbles, "blocks": blocks,
        "pass_acc_sum": pass_acc_sum, "pass_acc_cnt": pass_acc_cnt,
        "wins": team_w, "draws": team_d, "losses": team_l,
        "red_cards": red_cards,
    }


def get_club_and_total_extras_for_period(team_id, nationality, start_year, end_year):
    """[2026-07 신설, 신민용 요청: "팀 이력에 리그만 뜨니 헷갈린다 — 그
    기간의 클럽대회 전체(컵+챔스+클럽월드컵)랑 전체(국가대표까지 포함)를
    따로 보여주자"] career_entries 한 스틴트(기간+team_id) 동안, 리그를
    제외한 나머지 대회 실적을 두 단계로 합산한다:
      - "club": 그 팀 소속으로 뛴 컵대회+챔피언스리그+클럽월드컵 합계
        (국가대표 제외 — 팀 이력 위쪽 리그 줄과 같은 '클럽' 범주)
      - "total": club + 그 기간 동안의 국가대표(국제전, 예선 포함) 전부
    [2026-07 주의] '전체 이력' 표시용으로는 get_full_history_extras_for_period
    (위, 더 상세)를 대신 쓴다 — 이 함수는 구버전 호출부 호환용으로 남겨둠."""
    conn = get_conn(); c = conn.cursor()
    club_m = club_g = club_a = 0
    for tbl, tour_tbl in (("cup_matches", "cup_tournaments"),
                          ("cl_matches", "cl_tournaments"),
                          ("el_matches", "el_tournaments"),
                          ("ecl_matches", "ecl_tournaments"),
                          ("sc_matches", "sc_tournaments"),
                          ("cwc_matches", "cwc_tournaments")):
        row = c.execute(
            f"""SELECT COUNT(*) n, COALESCE(SUM(m.my_goals),0) g, COALESCE(SUM(m.my_assists),0) a
                FROM {tbl} m JOIN {tour_tbl} t ON m.tournament_id=t.id
                WHERE m.my_played=1 AND (m.home_team_id=? OR m.away_team_id=?)
                  AND t.year BETWEEN ? AND ?""",
            (team_id, team_id, start_year, end_year)).fetchone()
        club_m += row["n"] or 0
        club_g += row["g"] or 0
        club_a += row["a"] or 0

    intl_m = intl_g = intl_a = 0
    if nationality:
        irow = c.execute(
            """SELECT COUNT(*) n, COALESCE(SUM(m.my_goals),0) g, COALESCE(SUM(m.my_assists),0) a
               FROM intl_matches m JOIN intl_tournaments t ON m.tournament_id=t.id
               WHERE m.my_played=1 AND (m.home=? OR m.away=?)
                 AND t.year BETWEEN ? AND ?""",
            (nationality, nationality, start_year, end_year)).fetchone()
        intl_m, intl_g, intl_a = irow["n"] or 0, irow["g"] or 0, irow["a"] or 0
    conn.close()
    return {
        "club":  {"matches": club_m, "goals": club_g, "assists": club_a},
        "total": {"matches": club_m + intl_m, "goals": club_g + intl_g, "assists": club_a + intl_a},
    }


def league_total_teams_by_name(league_name: str):
    """[2026-07 신설, 신민용 요청] 커리어/은퇴창/AI요약의 '팀순위'를
    "12위" 대신 "12위/18팀"으로 보여주기 위한 그 리그 전체 팀 수 조회.
    league_total_games_by_name과 동일한 조회 패턴(리그명→league_id→팀 수)
    이지만, 다전제 환산 없이 팀 수 자체를 그대로 반환한다.
    못 찾으면 None(호출부에서 분모 없이 표시)."""
    if not league_name:
        return None
    conn = get_conn()
    row = conn.execute("SELECT id FROM leagues WHERE name=? LIMIT 1", (league_name,)).fetchone()
    if not row:
        conn.close()
        return None
    n_teams = conn.execute(
        "SELECT COUNT(*) AS n FROM teams WHERE league_id=?", (row["id"],)).fetchone()["n"]
    conn.close()
    return n_teams if n_teams > 0 else None


def _get_cl_cup_season_stats(year):
    """[2026-07 신설, 신민용 지적: "발롱도르는 챔스나 컵대회도 반영돼야
    하지 않나"] 그 해 챔피언스리그+국내컵에서 내가 실제로 뛴 경기의
    골/도움/평점 합계. 지금까지 개인 수상(_process_awards)은 리그 스탯
    (season_goals/season_assists/season_rating)만 봤는데, 이 셋은
    match_flow의 리그 경기 루프에서만 갱신되고 cup_engine/champions_engine은
    각자 cup_matches/cl_matches에 my_goals/my_assists/my_rating을 따로
    적재할 뿐 player의 season_* 누적치엔 전혀 반영하지 않는다 — 그래서
    챔스에서 8골을 넣어도 발롱도르 심사엔 0골로 보였다. 실제 발롱도르도
    UCL 활약이 사실상 핵심 변수이므로, 리그 스탯과 합산해서 쓸 수 있게
    별도 집계 함수로 뺀다.
    반환: {"goals","assists","rating_sum","rating_cnt","cl_won"}
    (rating_sum/rating_cnt는 평균이 아니라 합계·횟수 — 호출부에서 리그
    시즌 평균과 가중평균으로 합쳐 쓰기 위함)."""
    conn = get_conn()
    cl = conn.execute(
        """SELECT COALESCE(SUM(m.my_goals),0) g, COALESCE(SUM(m.my_assists),0) a,
                  COALESCE(SUM(m.my_rating),0) rs, COUNT(*) rc
           FROM cl_matches m JOIN cl_tournaments t ON m.tournament_id=t.id
           WHERE t.year=? AND m.my_played=1""", (year,)).fetchone()
    cup = conn.execute(
        """SELECT COALESCE(SUM(m.my_goals),0) g, COALESCE(SUM(m.my_assists),0) a,
                  COALESCE(SUM(m.my_rating),0) rs, COUNT(*) rc
           FROM cup_matches m JOIN cup_tournaments t ON m.tournament_id=t.id
           WHERE t.year=? AND m.my_played=1""", (year,)).fetchone()
    # [2026-08 신설, 10순위 슈퍼컵 시스템 구축 이후, 신민용 요청: "상 받는
    # 것도 슈퍼컵 용으로 추가해야 한다"] 챔스/컵/국가대표와 동일하게,
    # 슈퍼컵에서 낸 개인 기록(골/도움/평점)도 이 합산에 포함시킨다 —
    # 이전엔 슈퍼컵이 없어서 반영할 대상 자체가 없었다.
    sc = conn.execute(
        """SELECT COALESCE(SUM(m.my_goals),0) g, COALESCE(SUM(m.my_assists),0) a,
                  COALESCE(SUM(m.my_rating),0) rs, COUNT(*) rc
           FROM sc_matches m JOIN sc_tournaments t ON m.tournament_id=t.id
           WHERE t.year=? AND m.my_played=1""", (year,)).fetchone()
    cl_t = conn.execute(
        "SELECT my_result FROM cl_tournaments WHERE year=? AND my_in=1", (year,)).fetchone()
    sc_t = conn.execute(
        "SELECT my_result FROM sc_tournaments WHERE year=? AND my_in=1", (year,)).fetchone()
    # [2026-07 버그수정, 신민용 리포트: "월드컵 기간에는 월드컵 기준까지
    # 넣고 그래야지"] 함수 docstring/주변 주석은 "챔스+컵+국가대표 대회"를
    # 전부 반영한다고 되어 있었는데, 실제로는 국가대표 대회(월드컵/대륙컵)
    # 개인 기록(내가 그 대회에서 넣은 골/도움/평점)이 통째로 빠져 있었다.
    # trophy_bonus 쪽엔 국가대표 대회 "팀 성적"(우승/8강 등)은 반영되고
    # 있었지만, 그건 팀 성적일 뿐 "내가 개인적으로 얼마나 생산했는가"와는
    # 별개다 — 월드컵에서 8골을 넣어도 그 8골이 combined_ga에 안 잡히고
    # 있었다는 뜻. intl_matches를 추가로 합산한다.
    intl = conn.execute(
        """SELECT COALESCE(SUM(m.my_goals),0) g, COALESCE(SUM(m.my_assists),0) a,
                  COALESCE(SUM(m.my_rating),0) rs, COUNT(*) rc
           FROM intl_matches m JOIN intl_tournaments t ON m.tournament_id=t.id
           WHERE t.year=? AND m.my_played=1""", (year,)).fetchone()
    conn.close()
    cl_won = bool(cl_t and cl_t["my_result"] and "우승" in cl_t["my_result"])
    # [2026-08 신설] 슈퍼컵 우승 게이트 — cl_won과 같은 방식으로, FIFA
    # 올해의 선수/UEFA·AFC 올해의 선수 등 "우승 시 자동 통과" 조건에
    # cl_won과 나란히 OR로 추가한다(챔스보다는 약한 대회이므로 발롱도르
    # 트로피 점수 자체는 0.2배로 낮게 잡지만, "세계 무대에서 우승해봤다"는
    # 게이트 통과 조건으로는 챔스와 동등하게 인정 — 게이트는 이분법적
    # 자격 검증이라 가중치 개념이 없다).
    sc_won = bool(sc_t and sc_t["my_result"] and "우승" in sc_t["my_result"])
    return {
        "goals": cl["g"] + cup["g"] + intl["g"] + sc["g"],
        "assists": cl["a"] + cup["a"] + intl["a"] + sc["a"],
        "rating_sum": cl["rs"] + cup["rs"] + intl["rs"] + sc["rs"],
        "rating_cnt": cl["rc"] + cup["rc"] + intl["rc"] + sc["rc"],
        "cl_won": cl_won,
        "sc_won": sc_won,
    }


# ══════════════════════════════════════════════════════════════
# [2026-07 신설, 신민용 지적: "발롱도르는 리그만 보지 말고 챔스/컵/
# 국가대표 대회까지 다 반영해야 한다"] 대회별 중요도를 별점 가중치로
# 명시해 점수화한다. 신민용이 제시한 우선순위:
#   1) 개인 퍼포먼스(가장 중요, 항상 _combined_ga/평점이 주축)
#   2) 팀 성적·우승 — 대회별 무게는 챔스 ⭐⭐⭐⭐⭐ > 국대 메이저대회
#      ⭐⭐⭐⭐~⭐⭐⭐⭐⭐ > 리그 ⭐⭐⭐⭐☆ > 자국컵 ⭐⭐☆☆☆ > 슈퍼컵/
#      클럽월드컵 ⭐☆☆☆☆~⭐⭐☆☆☆
#   3) 페어플레이 (카드 시스템 자체가 없어 현재 미반영 — 아래 주석 참고)
#
# 다만 "챔스가 리그보다 별점이 높다"가 "챔스 준우승이 리그 우승보다
# 항상 세다"는 뜻은 아니다 — 신민용이 든 예시(라리가 우승+챔스 8강 vs
# 리그 밖+챔스 준우승)처럼 실제로는 "우승 자체"가 "준우승"보다 근소하게
# 더 세게 평가되는 경우가 많다. 그래서 트로피 점수 자체는 개인 성적
# (_combined_ga, 보통 20~45대)보다 작은 배점(합계 10점 안팎)으로 눌러
# 놓고 — 우선순위 1번(개인 퍼포먼스)이 항상 더 크게 작용하게 하면서,
# 트로피 점수는 개인 성적이 비슷한 경합 상황의 타이브레이커 역할을
# 하도록 설계했다.
#
# [구현 한계] 챔스/리그는 실제 결과(cl_tournaments/get_league_standings)를
# 그대로 쓰지만, AI 라이벌 선수들의 트로피 성적은 이 게임에 별도로
# 저장돼 있지 않다(AI는 스탯 추정치만 존재) — 그래서 "내 트로피 점수"와
# "AI 라이벌의 트로피 점수"를 1:1로 비교하는 건 불가능하고, 대신 내
# 개인 성적+트로피 합산 점수가 발롱도르급 최소 문턱(BALLON_SCORE_MIN)을
# 넘는지로 판정한다(기존에도 AI는 스탯만으로 추정됐으므로 같은 한계선상).
# [2026-08 갱신] 슈퍼컵은 super_cup_engine.py로 실제 구현됐다 — 아래
# _sc_trophy_points가 _cl_trophy_points의 0.2배 가중치로 반영한다(신민용
# 확정: "챔스가 발롱에 1의 영향을 주면 슈퍼컵은 0.2의 영향"). 클럽
# 월드컵은 여전히 이 심사 로직에 포함되지 않는다 — 발롱도르 가중치를
# 매기려면 별도 확정이 필요하다.
def _cl_trophy_points(result: str) -> float:
    # [2026-07 재조정, 신민용 지적: "트로피 보너스 배점이 조금 약할 가능성 —
    # 차등을 크게 두면 훨씬 현실적"] 우승/준우승 쪽을 더 높이고 8강/16강
    # 쪽은 살짝 낮춰서, "우승급 실적"과 "중위권 진출"의 격차를 더 벌렸다.
    # (기존: 8.0/4.0/2.5/1.5/0.6/0.2 → 10.0/4.5/2.5/1.2/0.5/0.2)
    if not result:
        return 0.0
    if "우승" in result and "준우승" not in result:
        return 10.0
    if "준우승" in result:
        return 4.5
    if "4강" in result or "3위" in result or "4위" in result:
        return 2.5
    if "8강" in result:
        return 1.2
    if "16강" in result:
        return 0.5
    if "32강" in result:
        return 0.2
    return 0.0   # 조별리그 탈락/플레이오프 등


def _cup_trophy_points(result: str) -> float:
    if not result:
        return 0.0
    if "우승" in result and "준우승" not in result:
        return 1.0
    if "준우승" in result:
        return 0.3
    return 0.0


def _sc_trophy_points(result: str) -> float:
    """[2026-08 신설, 10순위 슈퍼컵 시스템 구축 이후, 신민용 확정: "챔스가
    발롱에 1의 영향을 주면 슈퍼컵은 0.2의 영향을 주는 것"] 슈퍼컵은 이제
    실제로 구현됐으므로(super_cup_engine.py), 위 주석에서 "가중치를 매길
    대상 자체가 없다"고 적어뒀던 한계가 해소됐다 — _cl_trophy_points와
    완전히 같은 결과 판정 기준(우승/준우승/4강급)에 0.2를 곱해, 챔스
    대비 정확히 1/5 무게로 반영한다. 슈퍼컵은 연 1회·4팀뿐인 단기
    대회라 챔스만큼 심사에 크게 반영되면 안 된다는 원래 설계 의도
    (위 별점 표: "슈퍼컵/클럽월드컵 ⭐☆☆☆☆~⭐⭐☆☆☆")를 그대로 따른다."""
    return _cl_trophy_points(result) * 0.2


def _intl_trophy_points(result: str, kind: str = "world", continent: str = "") -> float:
    """월드컵/대륙컵(코파 아메리카·유로 격) — 개최 연도에만 intl_tournaments에
    행이 생기므로, 이 함수가 0을 넘는 값을 주는 시점 자체가 자연스럽게
    '그 해가 메이저 국가대표 대회 연도'라는 게이트 역할을 한다.
    [2026-07 재조정, 신민용 지적: "월드컵 우승 정도면 배점을 더 크게
    두는 게 현실적"] 우승/준우승 배점을 CL 수준으로 끌어올렸다(기존
    6.0/3.5/2.0/1.0 → 10.0/4.5/2.0/0.8) — 월드컵 우승은 발롱도르 심사에서
    사실상 결정적 변수로 다뤄지는 게 현실에 더 가깝다.

    [2026-07 확장, 신민용 지적: "대륙컵(네이션스컵)은 유럽만 발롱도르에
    영향이 좀 들어가고 나머지(아시안컵/남북미 대륙컵/아프리카 네이션스컵)는
    참가국 수준이 약해서 큰 영향이 없어야 한다"] 월드컵(kind='world')은
    대륙 구분이 없어 그대로 만점을 주고, 대륙컵(kind='continent')은 유럽만
    만점, 그 외 대륙은 20%로 대폭 깎는다 — 아시안컵 우승(10.0)이 유로 우승과
    동일하게 발롱도르 트로피 점수를 채워주던 것을 막기 위함.

    [2026-08 확장, 신민용 확정] 3단계 지역컵(kind='region', EAFF/AFF/SAFF/
    WAFF/COSAFA/CECAFA/WAFU/UNCAF/카리브)은 대륙컵보다도 한 단계 더 아래
    급이라 — 대륙컵 중 유럽 외(20%)보다도 훨씬 약하게(5%) 잡는다. 지역컵
    조별탈락엔 대륙컵 같은 마이너스 페널티도 안 준다 — 애초에 그 정도로
    비중 있는 대회가 아니라서.
    """
    if not result:
        return 0.0
    if "우승" in result and "준우승" not in result:
        base = 10.0
    elif "준우승" in result:
        base = 4.5
    elif "4강" in result or "3위" in result or "4위" in result:
        base = 2.0
    elif "8강" in result:
        base = 0.8
    else:
        # [2026-07 신설, GPT 3차 피드백 부분 채택: "약한 대륙컵은 잘해도
        # 보너스는 제한적이지만 못하면 손해는 크다(비대칭)"] GPT는 대회
        # 5개 × 잘했을때/못했을때 총 10개 계수의 표를 제안했지만, 이 함수는
        # 결과 문자열만 받고 개인 생산력은 안 받으므로("우승했지만 무득점"과
        # "조별탈락+무득점"을 구분 못함) 그 정교한 매트릭스를 그대로 넣으면
        # 팀이 약해서 일찍 떨어진 것까지 선수 개인 책임으로 감점하게 된다.
        # 그래서 핵심만 최소하게 반영한다 — 유럽 외 대륙컵(아시안컵/남북미
        # 대륙컵/아프리카 네이션스컵) 조별탈락은 0이 아니라 소폭 마이너스로
        # 취급해 "이 정도 대회에서도 존재감을 못 보였다"는 신호를 준다.
        # 월드컵·유로는 그대로 0(중립) — 이미 그 자체로 충분히 어려운
        # 무대라 추가 감점 없이도 개인 생산력 위주 평가가 자연스럽다.
        if kind == "continent" and continent != "유럽":
            return -1.5
        return 0.0   # 조별탈락/예선탈락 등
    if kind == "region":
        base *= 0.05
    elif kind == "continent" and continent != "유럽":
        base *= 0.2
    return base


def _league_rank_points(team_id: int) -> float:
    """리그 최종 순위 기반 점수 (1위=리그 우승 4.0, 2위 1.5, 3위 0.5)."""
    try:
        rows = get_league_standings_by_team(team_id)
    except Exception:
        return 0.0
    for i, r in enumerate(rows):
        if r.get("id") == team_id:
            if i == 0:
                return 4.0
            if i == 1:
                return 1.5
            if i == 2:
                return 0.5
            return 0.0
    return 0.0


def _get_ballon_trophy_bonus(year: int, team_id: int) -> float:
    """그 해 챔스+리그순위+자국컵+국가대표 메이저대회 성적을 합산한
    발롱도르용 트로피 보너스 점수 (0~약 25.5점 범위, 개인 성적 대비
    작게 설계됨 — 위 설계 노트 참고). [2026-07 재조정] 우승급 실적의
    배점을 올려 상한이 기존 19.5→25.5로 늘었지만, 그 해 챔스+월드컵+
    리그+자국컵을 전부 우승해야 나오는 극단치라 실질 영향은 제한적."""
    conn = get_conn()
    cl_t = conn.execute(
        "SELECT my_result FROM cl_tournaments WHERE year=? AND my_in=1", (year,)).fetchone()
    cup_t = conn.execute(
        "SELECT my_result FROM cup_tournaments WHERE year=? AND my_in=1", (year,)).fetchone()
    sc_t = conn.execute(
        "SELECT my_result FROM sc_tournaments WHERE year=? AND my_in=1", (year,)).fetchone()
    intl_t = conn.execute(
        "SELECT kind, continent, my_result FROM intl_tournaments WHERE year=? AND my_selected=1",
        (year,)).fetchone()
    conn.close()
    bonus = 0.0
    bonus += _cl_trophy_points(cl_t["my_result"] if cl_t else None)
    bonus += _cup_trophy_points(cup_t["my_result"] if cup_t else None)
    bonus += _sc_trophy_points(sc_t["my_result"] if sc_t else None)
    bonus += _intl_trophy_points(
        intl_t["my_result"] if intl_t else None,
        intl_t["kind"] if intl_t else "world",
        intl_t["continent"] if intl_t else "")
    if team_id:
        bonus += _league_rank_points(team_id)
    return bonus


def _major_stage_carry(year: int, team_id: int) -> bool:
    """[2026-07 신설, 신민용 지적: "A급 이상 리그에서 성적 좋은 애들은
    월드컵이나 이런 기간에 캐리해서 상 싹쓸이하면 A급 리그라도 발롱
    받을 수 있게 만드는 게 좋을 듯. 챔스나 이런 것도 마찬가지"]
    기존엔 BALLON_DOR_GRADES(SS/S)에 없는 리그(A급 이하, 예: K리그)
    소속 선수는 자국리그에서 아무리 압도적이어도(심지어 월드컵 우승+
    골든볼을 받아도!) 발롱도르 후보 자격 자체가 없었다. 이제 A급 이하
    리그 선수도 후보가 될 수 있게 하되, 반드시 '세계 무대에서 실제로
    증명'했어야 한다는 게이트를 하나 추가한다 — 자국리그 골 폭탄만으로는
    여전히 안 되고, 월드컵 결승급 활약(우승/준우승) 또는 유럽 챔피언십
    (유로) 우승이 있어야 이 게이트를 통과한다.

    [경계선] 아시안컵/남북미 대륙컵/아프리카 네이션스컵 우승이나, 자국
    대륙 챔피언스리그(아시아 챔스 등) 우승은 이 게이트를 통과시키지
    않는다 — 신민용이 별도로 지적한 대로 그 대회들은 참가국/참가팀
    수준이 상대적으로 약해서 실제 발롱도르 심사에 큰 영향을 주지
    않기 때문이다. 유럽 챔피언스리그(continent='유럽')만 여기서 CL
    경로로 인정하지만, 구조상 A급 이하 국가는 유럽 CL에 참가하지
    않으므로 사실상 이 함수는 A급 선수에게 '월드컵/유로 무대에서
    실제로 증명했는가'만 묻는 셈이다."""
    conn = get_conn()
    intl_t = conn.execute(
        "SELECT kind, continent, my_result FROM intl_tournaments WHERE year=? AND my_selected=1",
        (year,)).fetchone()
    cl_t = conn.execute(
        "SELECT continent, my_result FROM cl_tournaments WHERE year=? AND my_in=1",
        (year,)).fetchone()
    conn.close()
    if intl_t:
        result = intl_t["my_result"] or ""
        if intl_t["kind"] == "world" and ("우승" in result or "준우승" in result):
            return True
        if intl_t["kind"] == "continent" and intl_t["continent"] == "유럽" and "우승" in result:
            return True
    if cl_t and cl_t["continent"] == "유럽":
        result = cl_t["my_result"] or ""
        if "우승" in result:
            return True
    return False


def _primary_club_this_season(p):
    """[2026-08 신설, 신민용 리포트: "시즌 중 이적하면 구단 올해의 선수가
    엉뚱한 팀(또는 아예 무자격)으로 처리된다"] 이번 시즌(연도) 동안
    가장 많은 경기를 뛴 소속팀을 (team_id, matches)로 반환한다.

    - 아직 안 닫힌(진행 중) 스틴트: season_matches를 그대로 쓴다 —
      이적하면 이 값이 0으로 리셋되므로 "지금 팀에서만 뛴 경기수"가 맞다.
    - 그 해 안에 이미 떠난(닫힌) 스틴트: career_entries.matches를 쓴다 —
      _close_career_entry가 그 스틴트 종료 시점의 season_matches를 그대로
      박아두므로(다음 스틴트에서 다시 0으로 리셋되기 전 값) 정확하다.
    - 여러 팀 중 매치수가 가장 많은 팀을 "그 시즌의 소속팀"으로 본다.
      동률이면 시즌 종료 시점 현재 팀을 우선한다(직관적으로 "지금 팀"이
      맞다고 느껴지는 경계 상황이므로).
    """
    cur_tid = p.get("current_team_id", 0)
    cur_matches = p.get("season_matches", 0)
    year = p.get("current_year", 0)
    best_tid, best_matches = cur_tid, cur_matches
    if not year:
        return cur_tid, cur_matches
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT team_id, matches FROM career_entries
               WHERE end_year=? AND team_id!=0 AND team_id!=?""",
            (year, cur_tid)).fetchall()
    finally:
        conn.close()
    for r in rows:
        if r["matches"] and r["matches"] > best_matches:
            best_tid, best_matches = r["team_id"], r["matches"]
    return best_tid, best_matches


# ═══════════════════════════════════════════════════════════════
# [2026-08 신설, 골 시상 시스템 v4] 실제 골 이벤트 기록 + 시즌말 시상.
# 설계문서 + 검토 피드백 반영:
#   - goal_events: is_mine/is_pseudo로 "실제 골" vs "역산 대표골" 구분.
#     pseudo 골은 어떤 선수 통계 조회에도 절대 섞이면 안 됨(WHERE is_pseudo=0
#     강제) — 컬럼 자체는 그대로 두고 "조회 쪽에서 강제"하는 원칙 유지.
#   - context_score는 항상 0.80~1.10로 clamp(gen_goal 내부에서도 이중 방어) —
#     "대회 중요도가 골 퀄리티(shot_score)를 압도"하는 걸 구조적으로 차단.
#   - AI 대표골은 득점왕 1명이 아니라 상위 득점자 후보군(최대 5명)에서
#     생성해 최고 점수를 채택 — 득점왕이 항상 가장 화려한 골을 넣는 건
#     아니라는 지적 반영.
#   - AI 대표골 생성은 (year, player_id, competition_id, scope) 기반
#     결정론적 시드로 재현 가능하게 만든다(재시뮬레이션해도 같은 대표골).
#   - "후보 생성"과 "실제 시상"을 함수로 분리(_generate_goal_candidates /
#     _process_goal_awards)해서 나중에 "올해의 골 후보 TOP10" 같은 기능을
#     추가하기 쉽게 한다.
# ═══════════════════════════════════════════════════════════════

SHOT_TYPE_KR = {
    "NORMAL": "일반 슈팅", "HEADER": "헤더", "TOE_POKE": "토킥", "CHIP": "칩슛",
    "DIRECT_FREEKICK": "직접 프리킥", "CURLED": "감아차기", "DIRECT_CORNER": "직접 코너킥",
    "BACKHEEL": "백힐", "DIVING_HEADER": "다이빙 헤더", "HALF_VOLLEY": "하프발리",
    "VOLLEY": "발리", "PANENKA": "파넨카", "RABONA": "라보나",
    "OVERHEAD": "오버헤드킥", "SCORPION": "스콜피온킥",
}
FEATURE_KR = {
    "LONG_RANGE": "장거리", "EXTREME_LONG_RANGE": "초장거리", "HALF_LINE": "하프라인",
    "EXTREME_ANGLE": "극단적 각도", "SOLO_RUN": "솔로 돌파",
    "MULTIPLE_DEFENDERS": "다수 수수비 제침", "GK_GOAL": "GK 골",
    "LAST_MINUTE": "극장골", "WINNING_GOAL": "결승골", "COMEBACK_GOAL": "역전골",
}


def _goal_detail_text(c, goal_row) -> str:
    """goal_events 레코드 → 화면 표시용 상세 텍스트.
    예: "43주차 | 오버헤드킥, 초장거리 | 첼시 vs 아스날"
    [2026-08 보강, 신민용 요청: "상세에 언제 있었던 경기에서 어떤 골이었는지
    표시돼야 한다"] 기존엔 슛종류/피처/상대팀만 있고 '언제'가 없었다 —
    awards.year(연도)는 career_window/retire_window에서 이미 별도 "연도"
    컬럼으로 보여주고 있으니, 여기선 그 연도 안에서 몇 주차였는지만 덧붙인다.
    week=0(AI 대표골처럼 특정 실제 경기가 없는 값)이면 주차를 생략 — 다만
    실제로 상을 받는 골(my_best)은 항상 is_pseudo=0 실제 경기 골이라
    week이 항상 채워져 있다(AI 대표골은 비교용으로만 쓰이고 상 자체엔
    연결되지 않음)."""
    shot = SHOT_TYPE_KR.get(goal_row["shot_type"], goal_row["shot_type"])
    try:
        feats = json.loads(goal_row["goal_features"] or "[]")
    except Exception:
        feats = []
    feat_str = ", ".join(FEATURE_KR.get(f, f) for f in feats)
    parts = [shot] + ([feat_str] if feat_str else [])
    my_team = _team_name(c, goal_row["team_id"]) if goal_row["team_id"] else "?"
    opp_team = _team_name(c, goal_row["opponent_team_id"]) if goal_row["opponent_team_id"] else "?"
    base = f"{', '.join(parts)} | {my_team} vs {opp_team}"
    week = goal_row["week"] if "week" in goal_row.keys() else 0
    return f"{week}주차 | {base}" if week else base


def _calc_context_score(grade_weight: float, opp_ovr: float, my_ovr: float,
                         competition_importance_mult: float = 1.0) -> float:
    """CONTEXT_SCORE 계산 — 항상 0.80~1.10 범위로 clamp(gen_goal도 이중 방어).
    리그 등급 + 상대 강도 + 대회 중요도를 합산하되, 골 자체의 기술적 난이도
    (shot_score)를 절대 압도하지 못하도록 폭을 좁게 잡는다(검토 피드백 2번)."""
    base = 0.85 + grade_weight * 0.15  # grade_weight 0.05→0.8575, 1.00→1.00
    opp_bonus = max(0.0, min(0.05, (opp_ovr - my_ovr) * 0.002))
    comp_bonus = competition_importance_mult - 1.0
    return max(0.80, min(1.10, base + opp_bonus + comp_bonus))


def _make_goal_seed(year, player_id, competition_id, scope: str) -> "random.Random":
    """[검토 피드백 8번, RNG 결정론] AI 대표골은 같은 세이브+같은 시즌을
    다시 계산해도 항상 같은 골이 나와야 한다 — year/player_id/competition_id/
    scope로 결정론적 seed를 만든다."""
    key = f"{year}:{player_id}:{competition_id}:{scope}"
    seed = hash(key) & 0xFFFFFFFF
    return random.Random(seed)


def _record_goal_event(c, p, year, week, team_id, opponent_team_id,
                        competition_type, competition_id, league_id, league_name,
                        grade, opp_ovr):
    """[실제 골] 내 선수가 실제 경기에서 골을 넣은 그 순간 즉시 호출 —
    is_mine=1, is_pseudo=0으로 저장한다. c는 열린 커서(같은 트랜잭션)."""
    import goal_gen
    my_ovr = p.get("ovr", 60)
    grade_weight = goal_gen.LEAGUE_GRADE_WEIGHT.get(grade, 0.30)
    # 실시간 개별 골이라 시즌 골비율(goal_ratio) 대신 내 현재 OVR을 대리 지표로
    # 쓴다(설계상 goal_ratio는 "시즌 대표골" 산정에 더 맞는 개념 — AI 대표골
    # 쪽에서 실제 goal_ratio를 그대로 씀).
    opportunity = goal_gen.opportunity_for_grade(grade_weight, max(0.2, my_ovr / 100.0))
    context_score = _calc_context_score(grade_weight, opp_ovr, my_ovr)
    g = goal_gen.gen_goal(opportunity, context_score)  # 실시간 골 — 시드 고정 불필요
    c.execute("""INSERT INTO goal_events(
        year, week, player_id, team_id, opponent_team_id,
        competition_type, competition_id, league_id, league_name,
        shot_type, goal_features, shot_score, context_score, final_score,
        is_wonder_goal, is_mine, is_pseudo)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)""",
        (year, week, None, team_id, opponent_team_id,
         competition_type, competition_id, league_id, league_name,
         g["shot_type"], json.dumps(g["features"]), g["shot_score"],
         g["context_score"], g["final_score"],
         1 if g["final_score"] >= 55 else 0))
    return c.lastrowid


def _generate_ai_representative_goal(c, year, league_id, league_name, grade,
                                      candidates, competition_type="league",
                                      competition_id=None, scope="league"):
    """[AI 대표골, 검토 피드백 7번] 득점왕 1명이 아니라 상위 득점자 후보군
    (최대 5명, goals 내림차순)에서 각각 대표골을 만들고 그중 final_score
    최고를 채택한다. is_mine=0, is_pseudo=1로 저장 — 절대 선수 통계에
    섞이면 안 됨(호출부에서 WHERE is_pseudo=0 강제).
    candidates: [{"team_id","player_name"(또는 id 대용),"goals","matches","ovr","opponent_ovr"}] 형태.
    반환: 채택된 goal_events row id (없으면 None)."""
    import goal_gen
    if not candidates:
        return None
    top = sorted(candidates, key=lambda x: x.get("goals", 0), reverse=True)[:5]
    grade_weight = goal_gen.LEAGUE_GRADE_WEIGHT.get(grade, 0.30)
    best = None
    comp_id = competition_id if competition_id is not None else league_id
    for cand in top:
        goals = max(1, cand.get("goals", 1))
        matches = max(1, cand.get("matches", 1))
        goal_ratio = goals / matches
        opportunity = goal_gen.opportunity_for_grade(grade_weight, goal_ratio)
        context_score = _calc_context_score(
            grade_weight, cand.get("opponent_ovr", cand.get("ovr", 60)),
            cand.get("ovr", 60))
        # AI 식별자가 없을 수 있어(팀 id 정도만 있는 후보군) player_id 자리에
        # team_id를 대신 넣어 seed에 쓴다 — "완전한 재현"이 목적이 아니라
        # "같은 시즌 재계산 시 같은 결과"가 목적이므로 이 정도로 충분.
        seed_pid = cand.get("player_id", cand.get("team_id", 0))
        rng = _make_goal_seed(year, seed_pid, comp_id, scope)
        g = goal_gen.gen_goal(opportunity, context_score, rng=rng)
        row = {
            "team_id": cand.get("team_id"), "shot_type": g["shot_type"],
            "goal_features": json.dumps(g["features"]), "shot_score": g["shot_score"],
            "context_score": g["context_score"], "final_score": g["final_score"],
        }
        if best is None or row["final_score"] > best["final_score"]:
            best = row
    if best is None:
        return None
    cur = c.execute("""INSERT INTO goal_events(
        year, week, player_id, team_id, opponent_team_id,
        competition_type, competition_id, league_id, league_name,
        shot_type, goal_features, shot_score, context_score, final_score,
        is_wonder_goal, is_mine, is_pseudo)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1)""",
        (year, 0, None, best["team_id"], None,
         competition_type, comp_id, league_id, league_name,
         best["shot_type"], best["goal_features"], best["shot_score"],
         best["context_score"], best["final_score"],
         1 if best["final_score"] >= 55 else 0))
    return cur.lastrowid


# [2026-08 신설, 17순위 올해의 골 재설계] 튜닝 상수 — 전부 이 근처에
# 모아둬서, 나중에 "여전히 너무 자주/너무 안 나온다"는 피드백이 오면
# 여기 숫자만 조정하면 되게 한다.
#   _GOAL_POOL_SIZE: 리그 올해의 골 경쟁 풀에 넣을 AI 상위 득점자 수.
#   _GOAL_TOP_FRACTION: 그 풀에서 상위 몇 %까지 "후보" 자격을 주는지.
#   _GOAL_WIN_PROB_BY_RANK: 후보가 됐을 때 순위별 실제 당첨 확률(1등도
#     100%가 아니다 — 실제 시상식 표심처럼 편차를 준다).
#   _GOAL_WIN_PROB_TAIL: 위 딕셔너리에 없는(더 낮은) 순위의 기본 확률.
#   _PUSKAS_RIVAL_COUNT / _PUSKAS_TOP_CANDIDATES / _PUSKAS_WIN_PROB_BY_RANK:
#     푸스카스는 리그상보다 훨씬 좁은 문이라 별도로 더 엄격하게 잡는다.
_GOAL_POOL_SIZE = 10
_GOAL_TOP_FRACTION = 0.3
_GOAL_WIN_PROB_BY_RANK = {1: 0.55, 2: 0.30, 3: 0.15}
_GOAL_WIN_PROB_TAIL = 0.05
_PUSKAS_RIVAL_COUNT = 8
_PUSKAS_TOP_CANDIDATES = 3
_PUSKAS_WIN_PROB_BY_RANK = {1: 0.35, 2: 0.15, 3: 0.06}


def _process_goal_awards(c, p, year, tid, league_id, lname, grade, tier, cands, my_awards):
    """[시상 단계] "리그 올해의 골" + "FIFA 푸스카스상"(2009년 이후, 그 전엔
    "올해의 최고의 골")을 판정해 my_awards에 3-tuple(atype, detail,
    goal_event_id)로 추가한다. 대회(컵/대륙클럽대회/국가대표전 등)별
    "대회 최고의 골"은 각 대회 엔진에 아직 이 시스템과 연결된 후보군
    수집 로직이 없어 이번 단계에서는 제외(추후 단계에서 확장 예정).

    [2026-08 재설계, 17순위, 신민용 지적: "올해의 골이 너무 자주 나온다 —
    수비수가 1994년 1골로 올해의 골, 1997년 2골로 또 올해의 골 받는 건
    확률적으로 너무 높다. 선수 능력 기반이 아니라 골의 희귀성/경쟁
    기반으로 가야 한다 — 후보 점수 계산 → 상위 후보 추출 → 확률적 선정"]
    예전엔 "내 골 1개 vs AI 대표골 1개"를 만들어서 그냥 내가 이기면
    100% 받는 구조였다(사실상 동전던지기) — 이제는:
      1) 리그 상위 득점자 여러 명(_GOAL_POOL_SIZE) 각각의 대표골을 만들어
         "진짜 경쟁 풀"을 구성하고,
      2) 내 골이 그 풀 안에서 상위 몇 %(_GOAL_TOP_FRACTION) 안에 들어야
         (=충분히 희귀/뛰어나야) 아예 "후보" 자격이 생기고,
      3) 후보가 됐어도 순위별 확률(_GOAL_WIN_PROB_BY_RANK)로 당첨 여부를
         뽑는다 — 1등이어도 100%가 아니다(실제 시상식 표심처럼).
    "동일 골이 여러 상을 받을 수 있는가" 문제도 여기서 같이 정리한다 —
    FIFA 푸스카스상은 별개의 "세계 단위 올해의 골" 계층이 아니라(그런
    3단계 구조는 사실 필요 없다 — 현실의 푸스카스상 자체가 이미 "세계
    올해의 골"이므로), "이 시즌 리그 올해의 골을 받은 골만" 세계상 후보가
    되는 구조로 명확히 위계를 세운다. 그래서 같은 골이 리그상+세계상을
    "동시에" 받는 건 버그가 아니라 위계상 당연한 결과(리그를 이겨야
    세계 무대에 나갈 자격이 생기는 것과 같은 원리)로 정의한다.
    """
    if not tid or not league_id:
        return

    # 1) 내 실제 시즌 골 중 이 리그 골 중 최고 1개(방어: NULL 필터 — 검토
    #    피드백 3번). player_id는 실시간 기록 시 굳이 안 채워서(내 선수는
    #    항상 team_id=tid로 유일하게 특정 가능) team_id로 필터한다.
    my_best = c.execute("""SELECT * FROM goal_events
                            WHERE year=? AND league_id=? AND team_id=? AND is_mine=1
                              AND is_pseudo=0 AND final_score IS NOT NULL
                            ORDER BY final_score DESC LIMIT 1""",
                         (year, league_id, tid)).fetchone()
    if not my_best:
        return

    # 2) 리그 경쟁 풀 — 상위 득점자 "각자"의 대표골을 만든다(예전엔 상위
    #    5명 중 최고 1개만 남겨서 사실상 후보가 1명짜리였다).
    ai_scorers = sorted(
        [x for x in cands if not x.get("is_mine") and x.get("goals", 0) > 0],
        key=lambda x: x.get("goals", 0), reverse=True)[:_GOAL_POOL_SIZE]
    pool_scores = []
    for i, cand in enumerate(ai_scorers):
        gid = _generate_ai_representative_goal(
            c, year, league_id, lname, grade,
            [{"team_id": cand.get("team_id"), "player_id": cand.get("team_id"),
              "goals": cand.get("goals", 0), "matches": cand.get("matches", 1),
              "ovr": cand.get("ovr", 60), "opponent_ovr": cand.get("ovr", 60)}],
            competition_type="league", competition_id=league_id,
            scope=f"league_pool{i}")
        if gid:
            row = c.execute("SELECT final_score FROM goal_events WHERE id=?", (gid,)).fetchone()
            if row and row["final_score"] is not None:
                pool_scores.append(row["final_score"])

    # 3) 내 골을 풀에 넣고 순위를 매긴다 — 동점이면 내 골을 더 높은
    #    순위로 쳐준다(경쟁자 명단에 내 골까지 포함해 "몇 명 중 몇 등"을
    #    구하는 것뿐이라, 동점 처리 방향이 결과를 크게 바꾸지 않는다).
    pool_scores.append(my_best["final_score"])
    pool_scores.sort(reverse=True)
    my_rank = next(i for i, s in enumerate(pool_scores, start=1) if s <= my_best["final_score"])

    won_league_goal = False
    _eligible = my_rank <= max(1, round(len(pool_scores) * _GOAL_TOP_FRACTION))
    if _eligible:
        rng = _make_goal_seed(year, tid, league_id, "league_goal_pick")
        _win_prob = _GOAL_WIN_PROB_BY_RANK.get(my_rank, _GOAL_WIN_PROB_TAIL)
        if rng.random() < _win_prob:
            my_awards.append((f"{lname} 올해의 골", _goal_detail_text(c, my_best), my_best["id"]))
            won_league_goal = True

    # 4) FIFA 푸스카스상 — 리그 올해의 골을 이미 받은 골만 후보가 된다
    #    (위 docstring 참고 — "세계 무대"는 "리그를 먼저 이긴 골" 중에서만
    #    나온다는 위계). 세계급 라이벌을 여러 명(_PUSKAS_RIVAL_COUNT) 뽑아
    #    경쟁 풀을 만들고, 위와 동일하게 상위 후보 + 확률적 선정을 적용한다.
    _PUSKAS_GRADES = ("SS", "S")
    if won_league_goal and grade in _PUSKAS_GRADES and tier == 1:
        rivals = c.execute("""SELECT a.id, a.ovr, a.team_id FROM ai_players a
            JOIN teams t ON a.team_id=t.id
            JOIN leagues l ON t.league_id=l.id
            JOIN countries cn ON l.country_id=cn.id
            WHERE cn.grade IN ('SS','S') AND l.tier=1 AND a.position IN ({})
            ORDER BY a.ovr DESC LIMIT ?
            """.format(",".join("'%s'" % pp for pp in ATTACK_POS)),
            (_PUSKAS_RIVAL_COUNT,)).fetchall()
        rival_scores = []
        for i, rival in enumerate(rivals):
            rival_goal_id = _generate_ai_representative_goal(
                c, year, league_id, lname, grade,
                [{"team_id": rival["team_id"], "player_id": rival["id"],
                  "goals": max(12, round(rival["ovr"] / 4)), "matches": 30,
                  "ovr": rival["ovr"], "opponent_ovr": rival["ovr"]}],
                competition_type="world_rival", competition_id=0,
                scope=f"puskas_rival{i}")
            if rival_goal_id:
                rr = c.execute("SELECT final_score FROM goal_events WHERE id=?",
                                (rival_goal_id,)).fetchone()
                if rr and rr["final_score"] is not None:
                    rival_scores.append(rr["final_score"])

        rival_scores.append(my_best["final_score"])
        rival_scores.sort(reverse=True)
        my_world_rank = next(i for i, s in enumerate(rival_scores, start=1)
                              if s <= my_best["final_score"])
        # 세계상은 리그상보다 훨씬 좁은 문 — 상위 극소수만 후보.
        if my_world_rank <= _PUSKAS_TOP_CANDIDATES:
            rng2 = _make_goal_seed(year, tid, league_id, "puskas_pick")
            _win_prob2 = _PUSKAS_WIN_PROB_BY_RANK.get(my_world_rank, 0.02)
            if rng2.random() < _win_prob2:
                label = "FIFA 푸스카스상" if year >= 2009 else "올해의 최고의 골"
                my_awards.append((label, _goal_detail_text(c, my_best), my_best["id"]))


def _process_awards(p, year, season_goals, season_assists, season_rating, season_cs, season_goals_against=0):
    """시즌 종료 시 개인 수상 산정. 내 선수 실제 성적 + AI 추정 비교.

    [득점왕/도움왕 최소 기준]
      단순히 'pool 내 1위'만으로 주면, 약체 리그에서 AI 추정치가 우연히 낮게
      깔린 시즌엔 2골/2도움으로도 타이틀이 나오는 비현실적 상황이 생긴다.
      → 1위 조건에 더해 '출전 경기수 기반 최소 산출 기준'을 통과해야 수상.
         (풀시즌 7라운드*2 = 14경기 기준. 경기당 최소 생산성으로 환산)
    """
    # [2026-08 버그수정, 신민용 리포트: "시즌 중 이적하면 season_*가
    # 0으로 리셋되면서 이적 전 활약이 시상 계산에서 통째로 사라진다"]
    # 예전엔 p.get("current_team_id")를 그대로 썼다 — 시즌 종료 처리가
    # 실행되는 그 '순간' 소속팀일 뿐, 실제로 그 시즌 대부분을 뛴 팀이
    # 아닐 수 있다(예: 11개월 A팀 → 12/1 B팀 이적). _primary_club_
    # this_season이 이번 시즌 실제로 가장 많이 뛴 팀을 찾아준다.
    tid, _primary_matches = _primary_club_this_season(p)
    if not tid:
        return  # 무소속이면 수상 없음
    conn = get_conn(); c = conn.cursor()
    try:
        lrow = c.execute("""SELECT l.id as lid, l.name as lname, l.tier,
                                   cn.grade as grade, cn.name as cname
                            FROM teams t JOIN leagues l ON t.league_id=l.id
                            JOIN countries cn ON l.country_id=cn.id
                            WHERE t.id=?""", (tid,)).fetchone()
        if not lrow:
            conn.close(); return
        from constants import get_league_grade
        league_id, lname, tier = lrow["lid"], lrow["lname"], lrow["tier"]
        # 발롱도르 등 수상 판정엔 리그 등급 사용 (국대 등급 아님)
        grade = get_league_grade(lrow["cname"], lrow["grade"])

        # [2026-07 버그수정] AI 경쟁 풀 추정치도 실제 풀시즌 길이로 스케일해야
        # 하므로, 아래에서 쓰던 FULL_SEASON_MATCHES 계산을 후보 수집보다
        # 앞으로 끌어왔다(계산 내용 자체는 그대로, 호출 순서만 변경).
        FULL_SEASON_MATCHES = _league_full_season_matches(p, team_id=tid)
        cands, league_avg = _collect_league_candidates(c, league_id, full_season_matches=FULL_SEASON_MATCHES)
        # 내 선수 추가
        me = {
            "name": p.get("name","나"), "position": p.get("position","ST"),
            "ovr": p.get("ovr",40), "goals": season_goals, "assists": season_assists,
            "rating": season_rating, "is_mine": True, "age": p.get("age", 30),
            "cs": season_cs, "matches": p.get("award_matches", p.get("season_matches", 0)), "team_id": tid,
        }
        pool = cands + [me]

        my_awards = []  # (award_type, detail)

        # ── 득점왕/도움왕 최소 산출 기준 ───────────────────────────
        # [2026-07 버그 수정] 예전엔 "풀시즌 = 14경기(7팀 리그가 기본이던
        # 시절의 상/하반기 7R씩)"로 고정 하드코딩했다 — 근데 이제 리그마다
        # 팀 수(8~30팀)와 다전제(legs_for_team_count)에 따라 실제 풀시즌
        # 경기 수가 14~58경기까지 제각각이다. 고정 14를 계속 쓰면, 예를
        # 들어 30팀 리그(58경기)에서는 8골만 넣어도 득점왕 자격이 생겨
        # 버리고(실제로는 시즌 초반 8경기 페이스일 뿐), 반대로 8팀
        # 리그(42경기, 6전제)에서도 동일한 8골 기준이 적용돼 버려서 리그
        # 규모별 형평성이 깨진다.
        # 이제 이 선수가 뛰는 리그의 실제 팀 수로 진짜 풀시즌 경기 수를
        # 구해서 그 경기 수 기준으로 다시 스케일한다 — 골/도움 '경기당
        # 페이스' 기준(8골·6도움 / 14경기 ≈ 경기당 0.57골·0.43도움)은
        # 그대로 유지하고, 리그마다 그 페이스를 실제 풀시즌 길이에 맞게
        # 적용한다. 중도 합류/이적으로 적게 뛴 선수는 기존처럼 비례 조정.
        # [2026-07 재조정] 실제 프리미어리그 득점왕(골든부트) 평균 23~25골
        # (38경기 기준, 홀란급 괴물 시즌은 36골까지)을 기준점으로 다시 잡음.
        # 이전엔 옛날 14경기 체계 값(8골/14경기)을 그대로 스케일해서 20팀
        # 리그(38경기) 최소 기준이 22골로 다소 낮게 나왔었다 — 24골/38경기
        # (≈0.632/경기)로 살짝 올려 현실 페이스에 더 가깝게 맞춘다. 이건
        # '자격 최소선'이라 홀란급 시즌(30+)은 이 기준을 훌쩍 넘겨서 여전히
        # 문제없이 득점왕이 나온다.
        GOALS_PER_GAME_FOR_TITLE   = 24 / 38
        ASSISTS_PER_GAME_FOR_TITLE = 16 / 38   # 어시스트왕도 같은 기준(38경기)으로 재정렬
        GA_PER_GAME_FOR_BALLON     = 30 / 38   # 발롱도르급 시즌(골+도움 30/38 ≈ 메시·호날두 전성기)
        sm = max(1, p.get("award_matches", p.get("season_matches", 0)))
        play_ratio = min(1.0, sm / FULL_SEASON_MATCHES)
        _played_equiv = FULL_SEASON_MATCHES * play_ratio  # = min(sm, FULL_SEASON_MATCHES)
        min_goals_for_title   = max(4, round(GOALS_PER_GAME_FOR_TITLE * _played_equiv))
        min_assists_for_title = max(4, round(ASSISTS_PER_GAME_FOR_TITLE * _played_equiv))

        # 득점왕 (pool 1위 + 최소 골 기준 충족)
        # [2026-07 변경, 설계문서 v2 반영] 타이브레이커를 "골 수 → 평점"에서
        # "골 수 → 경기당 골"로 단순화한다. AI 후보는 전원 그 리그 풀시즌
        # 경기수를 그대로 가정하므로("matches" 필드) AI끼리는 이 타이브레이커가
        # 사실상 의미가 없고, 내 선수가 부상·이적으로 실제 경기수가 다를 때만
        # 실질적으로 작동한다 — 그마저도 최소 골 기준 자체가 실제 출전 비율에
        # 맞춰 스케일되므로 큰 영향은 없지만, "골 수가 같다면 더 적은 경기에
        # 넣은 쪽"이 실제 축구에서도 흔한 타이브레이커라 유지한다.
        top_scorer = max(pool, key=lambda x: (x["goals"], x["goals"] / max(1, x.get("matches", FULL_SEASON_MATCHES))))
        if top_scorer["is_mine"] and season_goals >= min_goals_for_title:
            my_awards.append(("득점왕", f"{season_goals}골"))

        # 도움왕 (pool 1위 + 최소 도움 기준 충족)
        top_assist = max(pool, key=lambda x: (x["assists"], x["assists"] / max(1, x.get("matches", FULL_SEASON_MATCHES))))
        if top_assist["is_mine"] and season_assists >= min_assists_for_title:
            my_awards.append(("도움왕", f"{season_assists}도움"))

        # 베스트11 — 포지션 그룹별 최고 점수 1위 선정 (포메이션: GK1/DF4/MF3/FW3)
        # [2026-07 신설, 신민용 확정: "꼴찌 팀 센터백이 베스트11인 건 드물다"]
        # 팀 순위를 아주 작게(±3% 이내) 반영한다 — _team_rank_mult 참고.
        _team_ranks, _n_teams_in_league = _league_team_ranks(c, league_id)

        def _rank_mult_for(x):
            return _team_rank_mult(_team_ranks.get(x.get("team_id"), _n_teams_in_league // 2 or 1),
                                    _n_teams_in_league)

        my_pos = p.get("position","ST")
        my_best11 = False
        best_df = None  # [올해의 수비수 신설] DF 분기 밖에서도 안전하게 참조하기 위한 기본값
        cs_for_me = _calc_clean_sheets_for_player(p, team_id=tid, matches=_primary_matches)
        
        if my_pos in GK_POS:
            # GK 그룹
            gk_cands = [x for x in pool if x["position"] in GK_POS]
            if gk_cands:
                # [2026-07 버그수정, 신민용 리포트: "꼴찌팀에 평점 5~6대인
                # 골키퍼가 베스트11을 7번이나 받는다"] AI 후보의 cs_est를
                # 내 선수 자신의 season_cs*0.5로 대신 계산하고 있었다 —
                # 즉 "AI 경쟁자"가 항상 내 클린시트의 절반으로 고정돼,
                # 내가 클린시트를 1개라도 기록하면 사실상 무조건 그 AI를
                # 이기는 구조였다(실력·팀 순위와 무관). _collect_league_
                # candidates()가 이미 각 AI GK 본인의 OVR·소속팀 전력
                # 기준으로 cs를 제대로 추정해뒀으므로(x["cs"]), 그 값을
                # 그대로 쓴다.
                gk_scores = []
                for x in gk_cands:
                    cs_est = cs_for_me if x["is_mine"] else x.get("cs", 0)
                    score = _best11_score_gk_df(cs_est, x["rating"], x["ovr"]) * _rank_mult_for(x)
                    gk_scores.append((x, score))
                best_gk = max(gk_scores, key=lambda x: x[1])
                if best_gk[0]["is_mine"]:
                    my_best11 = True
        
        elif my_pos in DF_POS:
            # DF 그룹 (CB, LB, RB 등)
            df_cands = [x for x in pool if x["position"] in DF_POS]
            if df_cands:
                # [2026-07 버그수정] GK와 동일한 문제 — 이제 _collect_league_
                # candidates()가 DF도 팀 전력 기준으로 cs를 추정해주므로
                # (위 GK_POS+DF_POS 수정) 그 값을 그대로 쓴다.
                df_scores = []
                for x in df_cands:
                    cs_est = cs_for_me if x["is_mine"] else x.get("cs", 0)
                    score = _best11_score_gk_df(cs_est, x["rating"], x["ovr"]) * _rank_mult_for(x)
                    df_scores.append((x, score))
                best_df = max(df_scores, key=lambda x: x[1])
                if best_df[0]["is_mine"]:
                    my_best11 = True
        
        elif my_pos in MF_POS:
            # MF 그룹 (CDM, CM, CAM)
            mf_cands = [x for x in pool if x["position"] in MF_POS]
            if mf_cands:
                best_mf = max(mf_cands, key=lambda x: _best11_score_mf(x["goals"],x["assists"],x["rating"],x["ovr"]) * _rank_mult_for(x))
                if best_mf["is_mine"]:
                    my_best11 = True
        
        elif my_pos in FW_POS:
            # FW 그룹 (LW, RW, CF, ST)
            fw_cands = [x for x in pool if x["position"] in FW_POS]
            if fw_cands:
                best_fw = max(fw_cands, key=lambda x: _best11_score(x["goals"],x["assists"],x["rating"],x["ovr"]) * _rank_mult_for(x))
                if best_fw["is_mine"]:
                    my_best11 = True
        
        if my_best11:
            my_awards.append(("베스트11", f"베스트11 ({my_pos})"))

        # [2026-07 신설, 신민용 확정] 올해의 수비수 (Defender of the Year)
        # — 베스트11(DF)이 이미 계산해둔 "리그 DF_POS 전체 중 최고 1명"
        # (_best11_score_gk_df 기반 best_df)을 그대로 재사용한다. 별도
        # 점수식을 새로 만들지 않는다 — 실제 축구에서도 Defender of the
        # Season과 Team of the Season 수비수가 같은 선수인 경우가 흔해서,
        # 베스트11(DF)과 같은 조건으로 겹쳐 받는 게 오히려 현실적이다.
        # 차이는 최소 출전 비율 게이트(MVP와 동일한 65%) 하나 — 베스트11은
        # 이 게이트가 없어서 "반 시즌만 뛰고 어쩌다 DF 풀 1등"도 통과할 수
        # 있는데, 개인 단독상인 올해의 수비수는 그 상황까진 막는다.
        DOTY_MIN_PLAY_RATIO = 0.65
        if (best_df is not None and best_df[0]["is_mine"]
                and sm >= DOTY_MIN_PLAY_RATIO * FULL_SEASON_MATCHES):
            my_awards.append(("올해의 수비수", f"{lname} 올해의 수비수"))

        # MVP (전체 베스트11 점수 1위)
        # [2026-07 수정] 포지션 무관 공격수 편향 공식(_best11_score) 대신
        # 포지션별 가중 점수식(_position_award_score) 사용 — 수비수/GK도
        # 자기 포지션 기준으로 정당하게 MVP 후보가 될 수 있게.
        # [2026-07 확장, 설계문서 v2 반영] "몇 경기 반짝 잘해서 MVP를 가져가는"
        # 상황을 막기 위해 최소 출전 비율(65%)·최소 평점(7.0) 게이트를 추가한다.
        # 출전 비율 자체를 점수식에 또 반영하진 않는다 — 문턱(게이트)과 점수
        # 가중치에 같은 값을 이중으로 쓰면 문턱을 넘은 후보끼리 또 출전율로
        # 차등이 생겨 사실상 같은 조건을 두 번 평가하는 셈이 되기 때문이다.
        MVP_MIN_PLAY_RATIO = 0.65
        MVP_MIN_RATING = 7.0
        mvp = max(pool, key=lambda x: _position_award_score(
            x["position"], x["goals"], x["assists"], x["rating"], x["ovr"], x.get("cs", 0)) * _rank_mult_for(x))
        if (mvp["is_mine"] and sm >= MVP_MIN_PLAY_RATIO * FULL_SEASON_MATCHES
                and season_rating >= MVP_MIN_RATING):
            my_awards.append(("MVP", f"{lname} 올해의 선수"))

        # [2026-07 신설, 신민용 확정] 구단 올해의 선수 (Club Player of the
        # Year) — 리그 전체가 아니라 "내 팀 로스터끼리만" 비교하는 내부
        # 투표 개념. pool의 각 항목엔 이미 team_id가 들어있어(_collect_
        # league_candidates가 채워둠) 새 쿼리 없이 필터링만으로 후보 풀을
        # 좁힐 수 있다. MVP보다 경쟁 풀이 훨씬 작으므로(리그 전체 vs 내
        # 팀 로스터) 게이트도 그만큼 낮게 잡는다 — 출전 비율은 MVP와
        # 동일(65%)하게 유지하되, 최소 평점은 7.0 대신 6.2로 낮춘다("팀
        # 내 확실한 핵심"이지 "리그 최정상급"까지는 아니어도 되는 상).
        # [2026-08 버그수정, 신민용 리포트: "구단 올해의 선수인데 라벨이
        # '프리미어리그 구단 올해의 선수'로 리그 이름이 붙어 나온다 —
        # 이건 리그 상이 아니라 내 팀만의 상이니 팀 이름이 붙어야 한다"]
        # 비교 자체는 처음부터 club_pool(팀 로스터)로 정확히 좁혀서 하고
        # 있었지만, 라벨 문자열만 다른 리그 상들과 똑같이 lname(리그 이름)을
        # 그대로 붙여쓰고 있었다 — _team_name(c, tid)로 실제 소속팀 이름을
        # 붙이도록 고친다.
        CLUB_POTY_MIN_PLAY_RATIO = 0.65
        CLUB_POTY_MIN_RATING = 6.2
        club_pool = [x for x in pool if x.get("team_id") == tid]
        if club_pool:
            club_best = max(club_pool, key=lambda x: _club_award_score(
                x["position"], x["goals"], x["assists"], x["rating"], x["ovr"], x.get("cs", 0)))
            if (club_best["is_mine"] and sm >= CLUB_POTY_MIN_PLAY_RATIO * FULL_SEASON_MATCHES
                    and season_rating >= CLUB_POTY_MIN_RATING):
                my_awards.append(("구단 올해의 선수", f"{_team_name(c, tid)} 구단 올해의 선수"))

        # 골든글러브 (GK 최다 클린시트 — 내가 GK이고 클린시트 많을 때)
        # [2026-07 확장, GPT 피드백: "클린시트 개수만 보면 안 되고 세이브율·
        # 평균실점도 봐야 한다"] AI 골키퍼는 세이브/실점 추정치가 없어서
        # 클린시트 1위 비교는 그대로 두고, 내 선수 자신의 세이브율·평균실점이
        # 최소 품질 기준을 넘는지를 _gk_quality_ok()로 추가 검증한다.
        if (p.get("position") == "GK" and season_cs >= 10
                and _gk_quality_ok(p.get("award_saves", p.get("season_saves", 0)), season_goals_against,
                                    sm, FULL_SEASON_MATCHES)):
            my_awards.append(("골든글러브", f"{season_cs} 클린시트"))

        # 영플레이어 (YPOTY) — [2026-07 변경, 신민용 확정: "리그는 23세 이하,
        # 국제대회는 21세 이하로 나누는 게 더 현실적"] EPL/UEFA 실제 기준
        # (23세 이하)에 맞춘다 — 챔스 영플레이어(이미 23세로 조정됨)와 통일.
        # 월드컵/대륙컵(intl_engine._award_intl_awards)은 21세 그대로 유지.
        # 상급 상 수상 조건 제거: 매 시즌 "유망주 중 최고"를 배출하기 위함
        young_cands = [x for x in pool if x.get("age", 30) <= 23]
        if young_cands:
            young_best = max(young_cands, key=lambda x: _position_award_score(
                x["position"], x["goals"], x["assists"], x["rating"], x["ovr"], x.get("cs", 0)))
            if young_best["is_mine"]:
                my_awards.append(("영플레이어", f"{lname} 영플레이어"))

        # 발롱도르 (S/A급 1부 + 세계 정상급 OVR + 압도적 성적 + 꾸준한 고평점)
        # [2026-07] 득점왕/도움왕과 동일하게, 리그별 실제 풀시즌 경기 수
        # (FULL_SEASON_MATCHES, 위에서 이미 팀 수·다전제 반영해 계산됨)
        # 기준으로 스케일한다. 골+도움 페이스는 메시/호날두 전성기 시즌
        # (38경기 기준 30G/A 안팎)을 기준점으로 재조정.
        # [추가] 실제 발롱도르는 골/도움 숫자만 보는 상이 아니라 시즌 내내
        # 얼마나 꾸준히 잘했는지(평균 평점)도 핵심 심사 기준이다 — 그래서
        # 골+도움 요건과 별개로 시즌 평균 평점도 일정 이상이어야 하게 했다.
        # (MVP로 대체 인정되는 경우도 동일하게 평점 요건은 유지 — MVP도
        # 결국 평점이 좋아야 받는 상이긴 하지만, 발롱도르 급의 '세계 최고'
        # 라인은 그보다 살짝 더 높게 잡는다.)
        # [2026-07 신설, 신민용 지적] 리그 스탯만 보던 걸 챔피언스리그·
        # 국내컵 스탯과 합산하도록 바꿨다 — _get_cl_cup_season_stats로
        # 그 해 컵/챔스 골·도움·평점을 가져와 리그 스탯에 더한다(평점은
        # 경기수 가중평균). 챔스 우승(cl_won)은 실제 발롱도르에서도
        # 사실상 결정적 변수라, 골+도움 문턱을 못 채워도 우승 자체로
        # dominant 인정하는 대체 경로를 추가한다(단, world_class OVR
        # 게이트는 그대로 유지 — 우승했다고 무조건은 아님).
        BALLON_MIN_RATING = 7.4
        # [2026-07 신설, 신민용 GK 발롱도르 QA: "GK는 OVR95·평균 클린시트
        # 16개짜리 시즌(200시즌 시뮬)에서도 단 한 번도 7.4를 못 넘는다(최댓값
        # 7.274) — _def_dominant_stats 경로가 있어도 이 공통 게이트에서 항상
        # 막혀 사실상 죽은 코드였다"] GK 평점 눈금 자체가 base=6.20 기반이라
        # 공격수(base 6.40~6.68)보다 절대적으로 낮게 형성된다. 포지션 간
        # 평점 분포가 다른데 하나의 문턱으로 비교하는 게 근본 원인이므로,
        # GK만 별도 문턱을 둔다(눈금 재보정은 영향범위가 너무 커서 보류).
        # [바로 아래 재보정 참고] 최초엔 6.9로 잡았으나, 실제 game.db
        # 기반 E2E 검증에서 SS등급 환경 특성상 도달 불가능한 값으로
        # 드러나 6.7로 다시 낮췄다.
        GK_BALLON_MIN_RATING = 6.7
        # [2026-07 재보정, 신민용 E2E QA 확정] 6.9는 SS등급 1부(전원
        # 엘리트+월드클래스 로스터 설계) 환경에서 이론상 최고치(OVR99,
        # 8시즌 시뮬 최고 6.77)조차 못 넘는 "발생 불가능한" 문턱이었다.
        # world_class(-2)는 그대로 유지 — 90+ 구간 1점 차이가 실질적으로
        # 크다는 _dominance_mult 설계 철학과 일치하고, "세계 최고권"이라는
        # 발롱도르 취지에도 맞다. 문제는 rating/CS 쪽 문턱이 "SS/S는 약팀이
        # 없다"는 리그 설계와 안 맞았던 것 — 6.7은 "이론상 최고 시즌은
        # 통과, 평범한 월클 시즌은 탈락" 경계에 맞춘 값.
        ballon = False
        # [2026-07 확장, 신민용 지적: "A급 리그라도 월드컵 같은 국제무대에서
        # 캐리해서 상 싹쓸이하면 발롱도르 받을 수 있게"] SS/S급 리그는
        # 기존처럼 자동으로 후보 자격이 있고, A급 이하 리그는
        # _major_stage_carry()(월드컵 결승급 활약/유로 우승)를 증명해야만
        # 후보 자격이 생긴다 — 이후의 world_class(라이벌 OVR -2 이내)·
        # dominant(생산력+평점+트로피) 검증은 SS/S와 완전히 동일하게 적용되므로,
        # A급 선수라고 기준이 느슨해지는 게 아니라 '후보 자격 게이트'만 하나 더
        # 넘도록 바뀐 것이다.
        eligible_grade = grade in BALLON_DOR_GRADES or (tier == 1 and _major_stage_carry(year, tid))
        # [2026-07 신설] UEFA/AFC 올해의 선수(대륙상)는 발롱도르보다 낮은
        # OVR 문턱(85)에서도 검사하므로, 아래 발롱도르 전용 블록(ovr>=88
        # 게이트 안)에서만 정의되는 값들을 미리 안전한 기본값으로
        # 초기화해둔다 — 88 미만(85~87)에서 대륙상 블록이 이 값들을 참조할
        # 때 NameError가 나는 걸 막기 위함.
        trophy_bonus = 0.0
        high_rating = False
        _combined_ga = 0.0
        _other_bonus = 0.0
        min_ga_for_ballon = 999999
        _cc = {"goals": 0, "assists": 0, "rating_sum": 0.0, "rating_cnt": 0, "cl_won": False}
        if eligible_grade and tier == 1 and p.get("ovr",0) >= 88:
            # [버그수정 2026-07, 신민용 지적: "K1에서 55골밖에 안 넣은
            # 애가 발롱도르를 받았다"] world_class 판정용 rival_ovr을
            # 구하는 이 쿼리가 SS등급(잉글랜드/EPL)을 빼먹고 있었다.
            # (당시 BALLON_DOR_GRADES=(SS,S,A)로 후보 자격엔 SS가 포함됐는데,
            # 정작 "세계 최고급이냐"를 비교하는 라이벌 풀은 S/A만 봐서
            # OVR 천장이 제일 높은 EPL 최고 선수가 통째로 빠졌다 —
            # rival_ovr이 실제보다 낮게 잡히면서 world_class 문턱(-2)이
            # 부당하게 낮아졌고, A등급(K리그) 선수도 화려한 골 스탯만
            # 있으면 세계 최고급으로 오판되는 구조였다.
            # [2026-07 확장, 신민용 확정: "발롱도르는 포지션별로 다른 문을
            # 통과해야 한다 — 후보 자격은 포지션마다 다르게, 최종 경쟁은
            # 동일한 잣대로"] 라이벌 OVR 비교 풀도 항상 공격수 (ATTACK_POS)
            # 만 보던 걸, 내 포지션 그룹에 맞게 바꾼다 — CB는 세계 최고급
            # CB랑 비교해야지 세계 최고급 스트라이커랑 비교하면 안 된다.
            _rival_pos_group = (GK_POS if my_pos in GK_POS
                                 else (DF_POS + ("CDM",)) if (my_pos in DF_POS or my_pos == "CDM")
                                 else ATTACK_POS)
            other = c.execute("""SELECT MAX(a.ovr) as mo FROM ai_players a
                JOIN teams t ON a.team_id=t.id
                JOIN leagues l ON t.league_id=l.id
                JOIN countries cn ON l.country_id=cn.id
                WHERE cn.grade IN ('SS','S') AND l.tier=1 AND a.position IN ({})
                """.format(",".join("'%s'" % pp for pp in _rival_pos_group))).fetchone()
            rival_ovr = other["mo"] if other and other["mo"] else 90
            _cc = _get_cl_cup_season_stats(year)
            _combined_ga = season_goals + season_assists + _cc["goals"] + _cc["assists"]
            _combined_matches = sm + _cc["rating_cnt"]
            _combined_rating = (
                (season_rating * sm + _cc["rating_sum"]) / _combined_matches
                if _combined_matches > 0 else season_rating)
            # 세계 최정상급(라이벌 -2 이내) + 압도적 성적(골+도움 또는 MVP)
            # + 꾸준한 고평점(둘 다 충족해야 함 — 평점만 좋고 생산력 없거나,
            #   반대로 생산력만 있고 기복이 심한 시즌은 발롱도르가 아니다).
            world_class = p.get("ovr",0) >= rival_ovr - 2
            min_ga_for_ballon = max(6, round(GA_PER_GAME_FOR_BALLON * _played_equiv))
            _ballon_rating_gate = GK_BALLON_MIN_RATING if my_pos in GK_POS else BALLON_MIN_RATING
            high_rating = _combined_rating >= _ballon_rating_gate
            # [2026-07 신설, 신민용 지적] 트로피 보너스(챔스/리그순위/자국컵/
            # 국가대표 메이저대회)를 개인 생산력에 더해서 문턱 통과 여부를
            # 판정한다. 개인 생산력(_combined_ga, 보통 20~45대)이 여전히
            # 훨씬 크게 작용하고, 트로피 보너스(최대 약 19.5점)는 경합
            # 상황의 타이브레이커 역할만 한다 — "챔스 준우승 하나만으로
            # 발롱도르"는 여전히 안 되지만(cl_won 단독 경로는 아래 유지),
            # "리그 우승+챔스 8강"처럼 여러 대회에 걸친 성실한 트로피
            # 실적은 골+도움이 문턱에 살짝 못 미쳐도 채워줄 수 있다.
            trophy_bonus = _get_ballon_trophy_bonus(year, tid)
            # [2026-07 신설, GPT 2차 피드백 채택: "발롱도르는 실제로 그 해
            # 다른 개인상 수상 여부가 투표에 영향을 준다"] 리그 MVP/베스트11은
            # 이 함수 안에서 이미 판정이 끝난 값(mvp/my_awards)을 그대로
            # 쓰고, 챔스 시즌MVP·월드컵 골든볼·유로 MVP 등은 이미 이 해에
            # 먼저 끝나 awards에 커밋된 기록을 조회한다(챔스 결승은 23주차,
            # 국가대표 대회는 44~52주 국제window, 리그/발롱도르 심사는
            # 52→1주 연도전환 시점이라 순서상 항상 먼저 커밋되어 있음이
            # 확인됨). GPT는 상한 +25를 제안했지만, 트로피 보너스 자체의
            # 최대치(약 19.5~25.5)와 맞먹는 상한을 또 얹으면 "개인 생산력이
            # 항상 트로피 실적보다 크게 작용한다"는 기존 설계 철학이
            # 깨지므로, 상한을 훨씬 낮은 +10으로 잡아 '타이브레이커의
            # 타이브레이커' 수준으로만 반영한다.
            _other_bonus = 0.0
            if mvp["is_mine"]:
                _other_bonus += 2.0
            if any(_a == "베스트11" for _a, _ in my_awards):
                _other_bonus += 1.0
            # [2026-07 최적화] get_conn()을 새로 또 호출할 필요 없이, 이 함수
            # (_process_awards) 맨 위에서 이미 열어둔 커서 c를 그대로 쓴다.
            # get_conn()이 풀 커넥션(싱글턴)이라 실제 버그는 아니었지만
            # (close()도 무력화돼 있어 안전), 이미 열려있는 커서를 두고 굳이
            # 한 번 더 get_conn()을 호출하는 건 불필요한 낭비였다.
            _oa_rows = c.execute(
                "SELECT award_type FROM awards WHERE year=? AND is_mine=1", (year,)).fetchall()
            for _oa in _oa_rows:
                _at = _oa["award_type"]
                if "시즌MVP" in _at:                       # 챔피언스리그 시즌MVP
                    _other_bonus += 3.0
                elif _at == "골든볼" or ("MVP" in _at and _at != "MVP"):  # 월드컵 골든볼/유로 MVP 등
                    _other_bonus += 4.0
            _other_bonus = min(_other_bonus, 10.0)
            # [2026-07 재조정, 신민용 지적: "챔스도 못 나가고 리그 4위인데
            # 57골로 발롱도르 받는 게 이상하다 — 그건 득점왕이지 발롱도르가
            # 아니다"] 예전엔 trophy_bonus가 '보너스'일 뿐이라, 개인 생산력
            # (_combined_ga)만 극단적으로 크면 트로피 실적이 진짜 0이어도
            # (리그 4위=순위점수 0, 챔스 미출전=0, 컵 조기탈락=0) 문턱을
            # 그냥 뚫어버렸다. trophy_bonus>0(무엇이든 최소한의 팀 성적 —
            # 리그 top3, 챔스 진출, 자국컵 선전, 국가대표 메이저대회 중
            # 하나라도)을 발롱도르의 필수조건으로 바꾼다 — 아무리 골을
            # 많이 넣어도 트로피 실적이 0이면 발롱도르는 아니고 득점왕만.
            # [2026-07 재조정 2차, 신민용 리포트: "리그 6등에 챔스 8강인데도
            # 골 많이 넣었다고 발롱도르 받는다"] trophy_bonus>0 게이트가 너무
            # 헐거웠다 — 챔스 8강(1.2점)만 나가도, 리그 순위가 0점(4위 밖)
            # 이어도 통과해버렸다. "무엇이든 최소한의 팀 성적"이 아니라
            # "진짜 의미 있는 팀 성적"으로 문턱을 올린다 — 챔스/국가대표
            # 4강 이상, 리그 top2, 혹은 그에 준하는 조합(리그 top3+챔스 8강
            # 등)은 통과하지만, 챔스 8강 하나로 리그 순위 없이 통과하는 건
            # 막는다(리그 3위=0.5+챔스8강=1.2=1.7로 여전히 2.0 미달 — 리그
            # 순위까지 애매하면 발롱도르는 아니라고 봄).
            # [2026-07 추가, 신민용 지적: "월드컵 기간에는 월드컵 기준까지
            # 넣고 그래야지"] 챔스 우승(_cc["cl_won"])은 이미 자동 통과
            # 조건에 있는데, 월드컵/유로 우승은 없었다 — 개인 생산력(골/도움/
            # 평점)은 이미 _get_cl_cup_season_stats로 반영되고 있었지만,
            # "우승 자체가 수치 문턱을 자동으로 우회시켜주는" 대우는 챔스만
            # 받고 있었다. 월드컵/유로 우승도 챔스 우승과 동일하게 대우한다.
            _intl_t_row = c.execute(
                "SELECT kind, continent, my_result FROM intl_tournaments WHERE year=? AND my_selected=1",
                (year,)).fetchone()
            _intl_won = False
            if _intl_t_row and _intl_t_row["my_result"] and "우승" in _intl_t_row["my_result"]:
                if _intl_t_row["kind"] == "world" or (
                        _intl_t_row["kind"] == "continent" and _intl_t_row["continent"] == "유럽"):
                    _intl_won = True
            MIN_TROPHY_BONUS_FOR_BALLON = 2.0
            # [2026-07 신설, 신민용 확정: "후보 자격 자체가 G+A로 제한되면
            # 안 된다 — 수비수/GK는 수비 지표로도 후보 등록이 가능해야
            # 한다"] 공격 포지션은 기존처럼 G+A 문턱으로 경쟁하고, 수비
            # 포지션(CB/FB/CDM)·GK는 무실점 경기 수 + 고평점 조합으로 별도
            # 대체 경로를 연다. 이건 "문턱을 낮춰주는" 게 아니라 "다른 문을
            # 여는" 것 — world_class(포지션별 라이벌 OVR-2 이내)와
            # trophy_bonus 게이트는 공격수와 완전히 동일하게 그대로 적용되고,
            # 여기서 대체되는 건 오직 _combined_ga 문턱 하나뿐이다.
            _def_like_pos = my_pos in GK_POS or my_pos in DF_POS or my_pos == "CDM"
            # [2026-07 재보정, 신민용 E2E QA 확정] CB/FB/CDM의 0.45(45%)
            # 문턱은 그대로 두되, GK만 별도 비율을 쓴다 — SS등급 1부(전원
            # 엘리트+월드클래스 로스터 설계)에서는 상대가 항상 강해 클린시트
            # 자체가 희귀하다(OVR99·8시즌 실측 최댓값 13/38≈34%). 45% 문턱은
            # 이 환경에서 이론상 최고 시즌도 못 넘는 값이라 GK만 31.6%
            # (12/38 기준)로 낮췄다 — CB/FB/CDM은 GK만큼 리그 전체 상대
            # 공격력에 매 경기 노출되지 않아(팀 전체 수비 조직력이 완충)
            # 기존 문턱을 그대로 유지한다.
            if my_pos in GK_POS:
                _cs_needed = max(10, round(0.316 * _played_equiv))
            else:
                _cs_needed = max(15, round(0.45 * _played_equiv))
            _def_dominant_stats = _def_like_pos and season_cs >= _cs_needed and high_rating
            dominant = high_rating and trophy_bonus >= MIN_TROPHY_BONUS_FOR_BALLON and (
                (_combined_ga + trophy_bonus + _other_bonus) >= min_ga_for_ballon
                or mvp["is_mine"] or _cc["cl_won"] or _intl_won or _def_dominant_stats)
            if world_class and dominant:
                ballon = True
        if ballon:
            my_awards.append(("발롱도르", f"{year} 발롱도르"))
            # [2026-07 신설, 신민용 확정: "FIFA 올해의 선수는 발롱도르와
            # 자동 연동"] 이 상을 가르는 실제 기준(기자/감독/주장/팬 투표)을
            # 재현할 데이터가 없으므로, 별도 판정 로직 없이 발롱도르 수상과
            # 그대로 묶는다. 현실에서도 같은 시즌에 두 상을 같이 받는 경우가
            # 압도적으로 많다.
            my_awards.append(("FIFA 올해의 선수", f"{year} FIFA 올해의 선수"))

        # [2026-07 신설, 신민용 확정] UEFA/AFC 올해의 선수 — 발롱도르와 달리
        # "그 대륙 안에서만" 비교하는 상이라 실제로 발롱도르와 다른 결과가
        # 나올 수 있다(발롱도르는 놓쳐도 대륙상은 받을 수 있음, 또는 그 반대).
        # 위에서 발롱도르용으로 이미 계산해둔 high_rating/trophy_bonus/
        # _combined_ga/_cc/mvp 등을 그대로 재사용한다(중복 계산 없음, GPT
        # 피드백에서도 강조된 부분) — 새로 만드는 건 "라이벌 OVR을 전세계가
        # 아니라 내 대륙으로만 좁힌 쿼리" 하나뿐이다. 발롱도르보다 후보군이
        # 훨씬 작으므로 문턱(OVR/등급)도 그만큼 낮춘다 — SS/S 등급이 아니어도,
        # 그 대륙 1부리그 소속이면 후보가 될 수 있다.
        _CONTINENT_POY = {"유럽": "UEFA 올해의 선수", "아시아": "AFC 올해의 선수",
                          "아메리카": "CONMEBOL 올해의 선수", "아프리카": "CAF 올해의 선수"}
        _my_cont_row = c.execute("""SELECT cn.continent FROM teams t
            JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id
            WHERE t.id=?""", (tid,)).fetchone()
        _my_continent = _my_cont_row["continent"] if _my_cont_row else ""
        if _my_continent in _CONTINENT_POY and tier == 1 and p.get("ovr", 0) >= 85:
            _cont_rival = c.execute("""SELECT MAX(a.ovr) as mo FROM ai_players a
                JOIN teams t2 ON a.team_id=t2.id
                JOIN leagues l2 ON t2.league_id=l2.id
                JOIN countries cn2 ON l2.country_id=cn2.id
                WHERE cn2.continent=? AND l2.tier=1 AND a.position IN ({})
                """.format(",".join("'%s'" % pp for pp in ATTACK_POS)), (_my_continent,)).fetchone()
            _cont_rival_ovr = _cont_rival["mo"] if _cont_rival and _cont_rival["mo"] else 80
            _cont_world_class = p.get("ovr", 0) >= _cont_rival_ovr - 2
            # 대륙상은 발롱도르보다 후보군이 작으므로 생산력 문턱을 30% 낮춘다.
            if _cont_world_class and high_rating and (
                    (_combined_ga + trophy_bonus + _other_bonus) >= min_ga_for_ballon * 0.7
                    or mvp["is_mine"] or _cc["cl_won"]):
                _cname = _CONTINENT_POY[_my_continent]
                my_awards.append((_cname, f"{year} {_cname}"))

        # [2026-07 신설, 신민용 확정] FIFPro 월드11 — "세계 톱리그 포지션별
        # 베스트 11". GPT 지적대로 새 점수식을 만들지 않고 리그/CL에서 이미
        # 쓰고 있는 _best11_score/_best11_score_gk_df/_best11_score_mf를
        # 그대로 재사용한다. SS/S급 리그 전체(+A급이면서 세계무대 캐리를
        # 증명한 경우) 소속 선수를 포지션별로 모아 포메이션(GK1/DF4/MF3/FW3)
        # 별 1위를 뽑고, 그중 내가 있으면 수상. 전세계 탑리그 선수를 매년
        # 한 번(연도전환 시점) 조회하는 작업이라 시즌 중 성능에는 영향이
        # 없지만, 연도전환 처리 자체는 조금 더 걸릴 수 있다.
        # [2026-07 확장, 신민용 확정: "월드11도 발롱도르처럼 개인성적+
        # 팀성적+개인상을 종합해야 진짜 월드클래스 수비수/GK도 자연스럽게
        # 들어간다"] GPT 추천(방법1: 발롱도르 점수 재활용)을 데이터
        # 실현가능한 선에서 반영한다 — SS/S 전세계 1500+명 AI 후보 전원의
        # 실제 트로피 이력(CL/컵 우승 등)을 조회하는 건 비용이 너무 크므로,
        # "팀 성적" 부분은 도메스틱 베스트11에 쓴 것과 같은 팀 순위 기반
        # 배율(_team_rank_mult, 다만 세계 무대라 범위를 조금 더 넓힌 버전)로
        # 근사하고, "개인상 보너스"는 AI가 개별 수상 이력을 추적하지 않는
        # 구조상 실제 이력이 있는 내 선수에게만(발롱도르 후보 시점에 이미
        # 계산해둔 trophy_bonus/_other_bonus 재사용) 반영한다.
        if eligible_grade and tier == 1:
            _W11_FULL_SEASON = 38  # 톱리그 표준 시즌 길이 가정(리그마다 달라도 근사치로 통일)
            _w11_pool = [{"position": p.get("position", "ST"),
                          "goals": season_goals, "assists": season_assists, "rating": season_rating,
                          "ovr": p.get("ovr", 60), "cs": season_cs, "is_mine": True, "team_id": tid}]
            _w11_ALL_POS = GK_POS + DF_POS + MF_POS + FW_POS
            _w11_ph = ",".join("'%s'" % pp for pp in _w11_ALL_POS)
            _w11_rows = c.execute(f"""SELECT a.ovr, a.position, a.sub_role, t2.id as team_id
                FROM ai_players a JOIN teams t2 ON a.team_id=t2.id
                JOIN leagues l2 ON t2.league_id=l2.id JOIN countries cn2 ON l2.country_id=cn2.id
                WHERE cn2.grade IN ('SS','S') AND l2.tier=1 AND a.position IN ({_w11_ph})""").fetchall()
            for _r in _w11_rows:
                _g, _a, _rt = _estimate_ai_season(_r["ovr"], _r["position"], 85, 85, _r["sub_role"],
                                                   full_season_matches=_W11_FULL_SEASON)
                _cs2 = (_estimate_ai_clean_sheets(_r["position"], _r["ovr"], 85, 85, _W11_FULL_SEASON)
                        if _r["position"] in GK_POS + DF_POS else 0)
                _w11_pool.append({"position": _r["position"], "goals": _g, "assists": _a, "rating": _rt,
                                  "ovr": _r["ovr"], "cs": _cs2, "is_mine": False, "team_id": _r["team_id"]})

            # 팀 성적 배율 — 팀별 league_id를 몰라도(다른 나라 리그들) 그
            # 팀이 속한 league_id를 한 번에 모아 리그별로 한 번씩만
            # _league_team_ranks를 호출(캐시)해서 N+1을 피한다.
            _w11_lid_cache: dict = {}   # team_id -> league_id
            _w11_rank_cache: dict = {}  # league_id -> (ranks_dict, n_teams)
            _w11_team_ids = {x["team_id"] for x in _w11_pool}
            if _w11_team_ids:
                _ph2 = ",".join("?" * len(_w11_team_ids))
                for _row in c.execute(f"SELECT id, league_id FROM teams WHERE id IN ({_ph2})",
                                       tuple(_w11_team_ids)).fetchall():
                    _w11_lid_cache[_row["id"]] = _row["league_id"]
            for _lid in set(_w11_lid_cache.values()):
                _w11_rank_cache[_lid] = _league_team_ranks(c, _lid)

            def _w11_team_mult(x):
                _lid = _w11_lid_cache.get(x.get("team_id"))
                if _lid is None or _lid not in _w11_rank_cache:
                    return 1.0
                _ranks, _n = _w11_rank_cache[_lid]
                _rank = _ranks.get(x["team_id"], _n // 2 or 1)
                # 세계 무대 배율은 도메스틱(±3%)보다 조금 더 넓게(±5%) —
                # "발롱도르 후보급"에서는 팀 성적(우승/상위권)이 실제로도
                # 더 크게 작용하기 때문(신민용 확정: 트로피 보너스 15% 비중).
                pct = _rank / _n if _n else 0.5
                if pct <= 0.25: return 1.05
                elif pct <= 0.5: return 1.02
                elif pct <= 0.75: return 1.00
                else: return 0.95

            _w11_groups = [(GK_POS + DF_POS, _best11_score_gk_df, "cs"),
                          (MF_POS, _best11_score_mf, "ga"),
                          (FW_POS, _best11_score, "ga")]

            # [2026-07 확장, 신민용 확정: "AI만 항상 개인상 보너스 0%인 건
            # 아쉽다 — 이력을 저장하지 않고 즉석 추정하자"] AI도 개별 수상
            # 이력을 저장하진 않지만, 지금 이 계산 안에서 이미 갖고 있는
            # 정보만으로 "이 정도면 그 해 상을 받았을 법하다"를 추정할 수
            # 있다 — 새 쿼리나 저장 없이 두 가지만 본다: ①같은 리그+포지션군
            # 안에서 1위(그 나라 리그 베스트11급, +2%) ②세계 전체(포지션
            # 무관, _position_award_score 기준) top10(발롱도르 후보급, +5%).
            for _grp_pos, _scorefn, _mode in _w11_groups:
                _grp_all = [x for x in _w11_pool if x["position"] in _grp_pos]
                _by_league: dict = {}
                for x in _grp_all:
                    _lid_key = _w11_lid_cache.get(x.get("team_id"), -1)
                    _by_league.setdefault(_lid_key, []).append(x)
                for _lid_key, _lst in _by_league.items():
                    if _mode == "cs":
                        _dom_top = max(_lst, key=lambda x: _scorefn(x["cs"], x["rating"], x["ovr"]))
                    else:
                        _dom_top = max(_lst, key=lambda x: _scorefn(x["goals"], x["assists"], x["rating"], x["ovr"]))
                    _dom_top["_domestic_best11_est"] = True
            _world_ranked = sorted(
                _w11_pool,
                key=lambda x: -_position_award_score(x["position"], x["goals"], x["assists"],
                                                      x["rating"], x["ovr"], x.get("cs", 0)))
            for x in _world_ranked[:10]:
                x["_ballon_est"] = True

            def _w11_personal_bonus(x):
                # 개인상 보너스 — 내 선수는 발롱도르 계산에서 이미 구한
                # 실제 trophy_bonus/_other_bonus를 그대로 재사용(더 정확한
                # 실제 이력이 있으니 추정치보다 우선). AI는 위에서 즉석
                # 추정한 플래그로 근사(저장 없음, 새 쿼리 없음).
                if x["is_mine"]:
                    return 1.05 if (trophy_bonus > 0 or _other_bonus > 0) else 1.0
                bonus = 1.0
                if x.get("_domestic_best11_est"):
                    bonus *= 1.02
                if x.get("_ballon_est"):
                    bonus *= 1.05
                return bonus

            for _grp_pos, _scorefn, _mode in _w11_groups:
                _grp = [x for x in _w11_pool if x["position"] in _grp_pos]
                if not _grp:
                    continue
                if _mode == "cs":
                    _best = max(_grp, key=lambda x: _scorefn(x["cs"], x["rating"], x["ovr"])
                                * _w11_team_mult(x) * _w11_personal_bonus(x))
                else:
                    _best = max(_grp, key=lambda x: _scorefn(x["goals"], x["assists"], x["rating"], x["ovr"])
                                * _w11_team_mult(x) * _w11_personal_bonus(x))
                if _best["is_mine"]:
                    my_awards.append(("FIFPro 월드11", f"{year} FIFPro 월드11"))
                    break

        # 골 시상 시스템 v4 — 리그 올해의 골 + FIFA 푸스카스상.
        # [2026-08 재설계, 신민용+검토 확정] 예전엔 "시즌 골수+평점"으로
        # 추정해서 수상 상세에 "{골수}골, 평점 {평점}"이 그대로 찍혔다 —
        # 실제로 어떤 골(슛종류/피처/상대팀)인지는 아예 추적하지 않았다.
        # 이제 goal_events(실제 골 goal_events + AI 대표골 역산)를 근거로
        # 판정하고, 화면엔 "슛종류, 피처 | 팀 vs 팀" 형식으로 표시한다.
        # 대회별(컵/대륙클럽대회/국대전 등) "대회 최고의 골"은 각 대회
        # 엔진에 아직 후보군 수집 로직이 없어 이번 단계에서는 제외 —
        # 리그 올해의 골 + 푸스카스상만 우선 반영(추후 단계에서 확장).
        _process_goal_awards(c, p, year, tid, league_id, lname, grade, tier, cands, my_awards)

        # 사모라 상 (최저 실점 골키퍼 — 경기당 평균 실점 최소)
        #   조건: GK && 1부리그 && (같은 리그 합산) 출전 >= 최소 출전(리그
        #        실제 풀시즌의 60% — 실제 사모라상도 라리가 38경기 중
        #        28경기 이상을 요구해 약 74%지만, 이 게임은 부상 등 변수로
        #        너무 빡빡하면 거의 못 받으니 60%로 완화)
        #        && 경기당 1.2골 이하
        #   [2026-07 버그 수정] ZAMORA_MIN_MATCHES=12(고정값)는 예전 14경기
        #   체계 기준이라, 지금처럼 리그마다 풀시즌이 14~58경기로 다른
        #   상황에서 그대로 쓰면 대형 리그에선 시즌 20%만 뛰어도 자격이
        #   생기고 반대로 소형 리그에선 사실상 거의 전 경기를 요구하는
        #   꼴이 된다. FULL_SEASON_MATCHES 기준 비율로 다시 잡는다.
        #   ※ 시즌 중 같은 리그 안에서 이적하면 두 팀 리그 기록을 합산한다
        #     (다른 리그로 옮기면 합치지 않음). _zamora_tally 참고.
        # [버그 수정 — 근본 원인] 원래 사모라상은 스페인 1부리그(라리가)
        # 전용 상인데, 여기선 국가·티어 제한이 전혀 없어서 어느 나라
        # 몇부 리그의 GK든 조건만 맞으면 받을 수 있었다(신민용 지적).
        # 특정 국가 하나로 좁히는 대신, 발롱도르와 같은 원칙(최상위
        # 플라이트만 인정)으로 최소한 1부리그로는 제한한다.
        if p.get("position") == "GK" and tier == 1:
            z_matches, z_ga = _zamora_tally(
                c, p, year, league_id, lname,
                p.get("season_matches", 0), season_goals_against)
            min_matches_for_zamora = max(6, round(0.6 * FULL_SEASON_MATCHES))
            if z_matches >= min_matches_for_zamora:
                my_ga_rate = z_ga / z_matches if z_matches > 0 else 999
                # 대부분의 GK는 경기당 1.3~1.5골 실점, 우수한 GK는 1.0~1.2.
                # 임계값 1.2 이하면 수상 가능.
                if my_ga_rate <= 1.2:
                    my_awards.append(("사모라상",
                        f"경기당 {my_ga_rate:.2f}골 실점 ({z_ga}/{z_matches}경기)"))

        # 저장 (DB 작업은 이 conn으로 모두 처리)
        # 개인 수상의 리그명에도 우승/트로피 기록과 동일하게 "(N부)"를 표기.
        #   awards 테이블엔 tier 컬럼이 없으므로 league_name 문자열에 합쳐 저장한다.
        #   → 수상 창·은퇴 후 창·AI 요약 등 awards.league_name 을 읽는 모든 곳이 자동 반영.
        lname_with_tier = f"{lname} ({tier}부)" if tier else lname
        # [2026-08 신설, 골 시상 시스템] my_awards는 대부분 (atype, detail)
        # 2-tuple이지만, _process_goal_awards가 추가하는 골 관련 상은
        # goal_events와 연결하기 위해 (atype, detail, goal_event_id) 3-tuple로
        # 온다 — 여기서 통일해서 처리(다른 append 호출부는 안 건드림).
        _normalized_awards = []
        for entry in my_awards:
            if len(entry) == 3:
                _normalized_awards.append(entry)
            else:
                _normalized_awards.append((entry[0], entry[1], None))
        for atype, detail, goal_event_id in _normalized_awards:
            c.execute("""INSERT INTO awards(year,award_type,league_name,detail,is_mine,goal_event_id)
                         VALUES(?,?,?,?,1,?)""",
                      (year, atype, lname_with_tier, detail, goal_event_id))
            if atype in ("발롱도르","MVP"):
                c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                          (year, p.get("name","나"), lname, tier, f"{atype} ({detail})"))
        conn.commit()
        conn.close()

        # 로그는 conn 닫은 뒤 (add_log가 별도 conn을 열므로 락 방지)
        for atype, detail, _gid in _normalized_awards:
            icon = {"득점왕":"⚽","도움왕":"🎯","베스트11":"⭐","MVP":"🏅",
                    "발롱도르":"🏆","영플레이어":"🌟","골든글러브":"🧤",
                    "푸스카스상":"💥","올해의 최고의 골":"💥",
                    "사모라상":"🛡️",
                    "올해의 수비수":"🛡️","구단 올해의 선수":"🎖️"}.get(
                        atype, "💥" if atype.endswith("올해의 골") else "🏅")
            add_log(f"{icon} {atype} 수상! ({detail})  {year}년", "event", year, 52)
        return
    except Exception as e:
        print("_process_awards 오류:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _recalc_field_pos_after_offseason(p):
    """오프시즌 포메이션 셔플 후 내 field_pos 재계산.
    감독이 포메이션을 바꾸면 내 배치 포지션도 달라질 수 있다.
    """
    if not p or not p.get("current_team_id"):
        return
    try:
        from constants import POSITION_COMPAT, FORMATION_SLOTS
        conn = get_conn()
        row = conn.execute(
            "SELECT formation FROM teams WHERE id=?",
            (p["current_team_id"],)).fetchone()
        conn.close()
        if not row:
            return
        _formation = row["formation"] or "4-4-2"
        _slots = FORMATION_SLOTS.get(_formation, FORMATION_SLOTS["4-4-2"])
        _primary = p.get("position", "CM")
        _compat = POSITION_COMPAT.get(_primary, [_primary])
        _best_pos, _best_rank = _primary, 0
        _best_found = 999
        for _slot in _slots:
            if _slot in _compat:
                _rank = _compat.index(_slot)
                if _rank < _best_found:
                    _best_found = _rank
                    _best_pos = _slot
                    _best_rank = _rank
        pass  # field_pos는 런타임 계산 (get_field_pos), DB 저장 불필요
    except Exception:
        pass


def _end_of_season(p, year):
    # 커리어 기록은 37~40주차 진입 시 이미 저장됨 → 여기선 생략

    # [2026-07 버그수정, 신민용 리포트: "발롱도르급 선수가 말도 안 되게
    # 임대를 간다"] 이 함수 뒷부분(4. 시즌 통계 초기화)에서 season_matches/
    # season_rating_sum/season_rating_cnt를 0으로 리셋하는데, 그 리셋된
    # 값을 나중에(_check_forced_release → _check_loan_candidate) 다시
    # 읽어서 "이번 시즌 얼마나 뛰었는지/평점이 어땠는지" 판정에 썼다.
    # 즉 판정 시점엔 이미 0/0이라 "출전비율 0%"·"평점 6.0(기본값)"으로
    # 항상 취급돼, 92% 선발 출전에 61골을 넣은 선수도 "거의 못 뛴 벤치
    # 자원"으로 오판되어 임대/방출 로직이 걸렸다. 리셋 전에 원본 값을
    # 스냅샷해뒀다가 판정 함수들에 그대로 넘긴다.
    _prior_season_matches    = p.get("season_matches", 0)
    _prior_season_rating_sum = p.get("season_rating_sum", 0.0)
    _prior_season_rating_cnt = p.get("season_rating_cnt", 0)

    # [귀화] 거주 연수 갱신 + 귀화 자격 체크. 이 함수 진입 시점엔 current_team_id가
    #   아직 살아있다(아래 계약만료 처리 전). next_year 기준으로 판정.
    _update_residency_and_naturalization(year + 1)

    # ── [기능1] 계약 보너스 정산 (출전·공격포인트 기반) ──────────
    if p.get("current_team_id"):
        app_b  = p.get("appearance_bonus_k", 0)
        goal_b = p.get("goal_bonus_k", 0)
        s_matches = p.get("season_matches", 0)
        s_points  = p.get("season_goals", 0) + p.get("season_assists", 0)
        bonus_total = app_b * s_matches + goal_b * s_points
        if bonus_total > 0:
            new_assets = p.get("total_assets", 0) + bonus_total
            new_earn   = p.get("total_earnings", 0) + bonus_total
            update_player(total_assets=new_assets, total_earnings=new_earn)
            add_log(f"💰 계약 보너스 정산: {fmt_money(bonus_total)} "
                    f"(출전 {s_matches}경기, 공격P {s_points})  {year}년", "event", year, 52)
            # [최적화] get_player() 재조회 없이 p 딕셔너리 직접 갱신
            p = dict(p)
            p["total_assets"] = new_assets
            p["total_earnings"] = new_earn

    # 시즌 평점 스냅샷 (아래 4단계에서 통계가 리셋되므로 미리 계산)
    # → 8단계 계약 만료 체크에서 사용
    _rc0 = p.get("season_rating_cnt", 0)
    _rs0 = p.get("season_rating_sum", 0.0)
    season_avg_rating = round(_rs0/_rc0, 2) if _rc0 else 6.0

    # 1.5 개인 수상 산정 (통계 리셋 이전에 실행). 최소 출전 기준은 위
    #     finalize_season_for_retire와 동일 원칙(리그 실제 풀시즌의 35%).
    # [2026-08 버그수정, 신민용 리포트: "시즌 중 이적하면 이전 팀 활약이
    # 시상에서 사라진다"] season_*(이적 시 리셋)가 아니라 award_*(이적해도
    # 안 리셋되는 시즌 전체 누적)로 게이트·점수를 계산한다. season_avg_rating
    # 변수 자체는 그대로 두고(바로 아래 8단계 계약 만료 체크가 '현재 팀에서의
    # 평점'을 봐야 하므로 건드리지 않음), 시상 전용으로 award_rating_*에서
    # 별도 계산한다.
    _award_matches_total = p.get("award_matches", p.get("season_matches", 0))
    _award_tid, _award_tid_matches = _primary_club_this_season(p)
    if _award_matches_total >= max(6, round(0.35 * _league_full_season_matches(p, team_id=_award_tid))):
        _award_rc = p.get("award_rating_cnt", 0)
        _award_rs = p.get("award_rating_sum", 0.0)
        award_avg_rating = round(_award_rs / _award_rc, 2) if _award_rc else season_avg_rating
        _season_cs = _calc_clean_sheets_for_player(p, team_id=_award_tid, matches=_award_tid_matches)
        _process_awards(
            p, year,
            season_goals=p.get("award_goals", p.get("season_goals", 0)),
            season_assists=p.get("award_assists", p.get("season_assists", 0)),
            season_rating=award_avg_rating,
            season_cs=_season_cs,
            season_goals_against=p.get("award_goals_against", p.get("season_goals_against", 0)),
        )

    # 2. 자연 성장 (10경기 이상, 성장기=peak 이전 + max 여유 있을 때만)
    if p.get("season_matches",0) >= 10 and p.get("age", 20) < p.get("peak_age", 25):
        base_pool = FOCUS_TRAIN_STATS.get(p["position"], ALL_STATS[:5])
        BASIC_STATS = ["stamina", "mental", "concentration"]
        pool = list(dict.fromkeys(base_pool + BASIC_STATS))
        stat = random.choice(pool)
        cur  = p.get(stat,40)
        mx   = p.get(f"{stat}_max",80)
        bonus = 1
        if "natural_growth_bonus" in PERSONALITY_EFFECTS.get(p.get("personality",""),{}):
            if random.random() < PERSONALITY_EFFECTS[p["personality"]]["natural_growth_bonus"]:
                bonus = 2
        if cur < mx - 3:
            update_player(**{stat: min(mx, cur+bonus)})
            add_log(f"🌱 시즌 자연 성장: {STAT_KO.get(stat,stat)}+{bonus}", "event", year, 52)

    # 3. 나이 증가 + 스탯 노화 (재능 티어 × 나이구간 × 계열 차등)
    new_age = p["age"] + 1
    stat_updates: dict = {"age": new_age, "total_seasons": p.get("total_seasons",0)+1}

    # 29세부터 노화. 28세 이하는 낙폭 0.
    if new_age >= 29:
        from constants import (AGING_DECLINE, AGING_DECLINE_WC_TOP, AGING_WC_TOP_OVR,
                               AGING_GROUP_WEIGHT, AGING_LIMITED_LATE_MENTAL,
                               AGING_POS_MULT, AGING_STAT_FLOOR, get_physical_age_cap,
                               PHYSICAL_STATS, TECHNICAL_STATS, MENTAL_STATS)
        tier = p.get("talent_tier", "pro")
        # 구버전 호환: 예전 키를 새 키로 변환
        _tier_compat = {"normal": "pro", "limited": "semipro",
                        "gifted": "worldclass", "mid": "elite"}
        tier = _tier_compat.get(tier, tier)
        pos  = p.get("position", "CM")

        # [티어별 나이구간 연간 OVR 낙폭] 선택.
        #   worldclass 중 전성기 천장(talent_cap) 98+ 는 더 완만한 wc_top 곡선.
        #   [2026-08 버그수정] 예전엔 god/superstar/amateur/untalented가
        #   AGING_DECLINE에 키가 없어 매번 "pro"로 조용히 폴백됐다 — 이제
        #   9단계 전부 키가 있어 이 fallback(AGING_DECLINE["pro"])은
        #   사실상 발동 안 하지만, 혹시 모를 talent_tier 오타/구버전 세이브
        #   대비 안전장치로 남겨둔다.
        if tier == "worldclass" and p.get("talent_cap", p.get("ovr", 0)) >= AGING_WC_TOP_OVR:
            decline_tbl = AGING_DECLINE_WC_TOP
        else:
            decline_tbl = AGING_DECLINE.get(tier, AGING_DECLINE["pro"])

        # 이번 나이의 '연간 OVR 낙폭(D)' 조회.
        annual_drop = 0.0
        for a0, a1, d in decline_tbl:
            if a0 <= new_age <= a1:
                annual_drop = d
                break
        # [2026-08 신설, 신민용 리포트: "46세부터 노화가 완전히 멈춘다"]
        # 위 테이블을 아무리 늘려놔도 "언젠가 그 나이도 넘는" 케이스가
        # 또 나올 수 있다(은퇴 강제가 없는 내 선수는 이론상 60세, 70세도
        # 가능) — 테이블에 정의된 마지막 구간을 넘는 나이는 그 구간 값을
        # 계속 적용해서, 어떤 나이에서도 annual_drop이 우연히 0이 되는
        # 경로 자체를 구조적으로 봉쇄한다(테이블을 몇 살까지 정의했든
        # 자동으로 커버됨 — 나중에 또 상한을 깜빡 잊어도 안전).
        if annual_drop <= 0 and decline_tbl and new_age > decline_tbl[-1][1]:
            annual_drop = decline_tbl[-1][2]

        if annual_drop > 0:
            pos_mult = AGING_POS_MULT.get(pos, 1.0)

            # 계열 비중 정규화: 전체 스탯에 평균 1.0이 되도록.
            def _group_of(s):
                if s in PHYSICAL_STATS:  return "physical"
                if s in TECHNICAL_STATS: return "technical"
                return "mental"

            # ordinary/semipro 노년(41세+)은 멘탈도 일부 깎는다.
            gw = dict(AGING_GROUP_WEIGHT)
            if tier in ("ordinary", "semipro") and new_age >= AGING_LIMITED_LATE_MENTAL["age"]:
                gw["mental"] = AGING_LIMITED_LATE_MENTAL["weight"]

            avg_w = sum(gw[_group_of(s)] for s in ALL_STATS) / len(ALL_STATS)
            if avg_w <= 0:
                avg_w = 1.0

            for stat in ALL_STATS:
                share = gw[_group_of(stat)] / avg_w      # 멘탈=0 → 안 깎임
                if share <= 0:
                    continue
                # 이번 시즌 이 스탯의 감소량 = D × 계열비중 × 포지션배수 × 랜덤(0.85~1.15)
                drop = annual_drop * share * pos_mult * random.uniform(0.85, 1.15)
                if drop <= 0:
                    continue

                # (a) 현재 스탯 직접 감소 — 훈련으로 다 메우지 못하게(핵심).
                cur = p.get(stat, 40)
                new_cur = max(AGING_STAT_FLOOR, round(cur - drop))

                # [2026-08 신설] 신체 스탯(stamina/speed/jump/strength)만
                # 나이별 절대 상한을 추가로 적용 — god/worldclass 등급이
                # GK처럼 포지션 배수가 낮은 경우, 자연 하락만으로는 40대에도
                # 신체 스탯이 80 가까이 남는 비현실적 케이스가 있었다(상세
                # 사유는 constants.AGING_PHYSICAL_AGE_CAP 주석 참고).
                # 기술/멘탈 스탯은 대상이 아니라 그대로 둔다.
                if stat in PHYSICAL_STATS:
                    _phys_cap = get_physical_age_cap(new_age)
                    if _phys_cap is not None and new_cur > _phys_cap:
                        new_cur = max(AGING_STAT_FLOOR, _phys_cap)

                if new_cur < cur:
                    stat_updates[stat] = new_cur

                # (b) 천장(_max)도 같은 양만큼 끌어내림(현재값이 다시 차오르는 것 방지).
                #     단 천장은 현재값 밑으론 안 내려가게(논리 일관).
                mk = f"{stat}_max"
                old_mx = p.get(mk, 80)
                new_mx = max(AGING_STAT_FLOOR, new_cur, round(old_mx - drop))
                if stat in PHYSICAL_STATS:
                    _phys_cap = get_physical_age_cap(new_age)
                    if _phys_cap is not None and new_mx > _phys_cap:
                        new_mx = max(AGING_STAT_FLOOR, new_cur, _phys_cap)
                if new_mx < old_mx:
                    stat_updates[mk] = new_mx

    # 4. 시즌 통계 초기화
    # [2026-08 신설] award_*(시즌 중 이적해도 리셋 안 되는 시상용 누적치)는
    # season_*와 달리 여기(진짜 시즌 종료 시점, 1.5단계 개인수상 산정이
    # 이미 끝난 뒤)에서만 초기화한다 — join_team()의 이적 리셋 코드는
    # 일부러 이 필드들을 건드리지 않는다.
    stat_updates.update(season_matches=0, season_goals=0, season_assists=0,
                        season_saves=0, season_rating_sum=0, season_rating_cnt=0,
                        season_goals_against=0,
                        season_shots=0, season_shots_on=0, season_key_passes=0,
                        season_dribbles=0, season_blocks=0,
                        season_pass_acc_sum=0, season_pass_acc_cnt=0,
                        season_red_cards_league=0,
                        award_matches=0, award_goals=0, award_assists=0,
                        award_saves=0, award_goals_against=0,
                        award_rating_sum=0, award_rating_cnt=0)
    update_player(**stat_updates)

    # [2026-07 버그수정, 신민용 리포트: "팀 정보가 사라지고 순위도
    # 0위/14팀으로 뜨고 커리어에 전적이 다 0으로 남는다"] 팀 승/무/패/
    # 득실 초기화가 원래 이 함수(_end_of_season, 연도 전환 시점=진짜 새
    # 시즌 시작)에 있어야 하는데, 승강 플레이오프 도입 때 그 초기화 코드가
    # _process_promotion_relegation 안에 같이 있는 걸 못 보고 그 함수
    # 호출 시점만 44주 앞(day300)으로 옮겨버렸다 — 그 결과 아직 같은
    # 해(2001년)인데, 시즌이 끝나자마자 팀 전적이 통째로 0으로 밀려서
    # 국제대회 기간(44~52주) 내내 커리어 화면·순위표가 전부 깨져 보였다.
    # 실제 세이브(game.db)로 재현·확인 후, 초기화 코드를 원래 있어야 할
    # 이 위치로 옮겼다 — 이제 진짜 연도가 넘어갈 때만(52→1주) 초기화된다.
    conn = get_conn()
    conn.execute("UPDATE teams SET wins=0,draws=0,losses=0,goals_for=0,goals_against=0")
    conn.commit()
    conn.close()

    # 이슈9: 시즌 종료 시 순위 기반 행복도 변화
    _apply_rank_happiness(p, year)

    # [2026-07 리팩터, 승강 플레이오프 도입] 예전엔 여기서 바로
    # "5. 승강제·우승 판정"(_finish_incomplete_matches_for_season +
    # _process_promotion_relegation)을 처리했는데, 이제 그 둘은 클럽 시즌이
    # 실제로 끝나는 시점(CLUB_SEASON_END_DAY, 43주)으로 옮겨졌다
    # (advance_days의 _finalize_club_season 호출 참고) — 승강 플레이오프가
    # 44주(그 해 안)에 열려야 해서, 대진을 만드는 재료(순위 확정 결과)가
    # 그보다 훨씬 늦은 새해 진입 시점에야 나오면 PO를 열 44주 자체가 이미
    # 지나가버린다. get_player()로 최신 상태를 그때그때 재조회하는 아래
    # 코드들(강제방출/계약 등)은 승강이 이미 몇 주 전에 끝나있어도 아무
    # 문제 없이 그 결과를 그대로 반영한다.

    # 5.7 [AI 선수 생애주기] 나이+1·성장/노화·은퇴/세대교체·이적시장·전술변경.
    #   → 같은 팀에 오래 있어도 매 시즌 스쿼드/전력/포메가 살아 움직인다.
    #   ai_players.ovr·team_id가 바뀌므로 내부에서 OVR 캐시를 무효화한다.
    import time as _time_perf2
    _tp1 = _time_perf2.perf_counter()
    try:
        from ai_lifecycle import run_ai_offseason
        run_ai_offseason(year, verbose_log=add_log)
    except Exception as _e:
        add_log(f"⚠ 이적시장 처리 중 오류: {_e}", "event", year, 52)
    if DEBUG_RELEGATION_TRACKING:
        for _tid in _RELEGATION_DEBUG_TRACK:
            _relegation_debug_snapshot(_tid, "개막 직전(이적시장 마감 후)")
    _tp2 = _time_perf2.perf_counter()
    print(f"[PERF]   _end_of_season 세부: AI생애주기 {_tp2-_tp1:.2f}s "
          f"(승강제 타이밍은 43주 _finalize_club_season으로 이동 — 여기 안 잡힘)")

    # 6. 강제 방출 체크 (이슈8 강화) — 우승 판정이 끝난 뒤에 처리
    p = get_player() or p   # 승강으로 리그/연봉이 바뀌었을 수 있으니 최신화
    # 오프시즌 포메이션 변경 후 field_pos 재계산
    _recalc_field_pos_after_offseason(p)

    # [2026-07 신설] 임대 기간이 끝났으면 원소속팀 복귀부터 처리.
    _return_from_loan_if_due(p, year)
    p = get_player() or p   # 복귀로 소속이 바뀌었을 수 있으니 다시 최신화

    _check_forced_release(p, year,
                           prior_season_matches=_prior_season_matches,
                           prior_season_rating_sum=_prior_season_rating_sum,
                           prior_season_rating_cnt=_prior_season_rating_cnt)

    # 7. (구) 연말 국제대회 일괄 시뮬 → 시즌 중 17~24주 실경기 방식으로 대체됨 (intl_engine)

    # 8. 계약 만료 체크
    p2 = get_player()
    if p2 and p2.get("current_team_id"):
        end_yr  = p2.get("contract_end_year", 0)
        if end_yr and year >= end_yr:
            # 팀의 재계약 의사 결정
            avg_r = season_avg_rating
            rel   = p2.get("manager_relation", 50)
            _grp  = POS_GROUP.get(p2.get("position","CM"), "미드")
            _base = RENEW_RATING.get(_grp, 6.3)

            # [OVR 격차 기반 재계약 거부] 팀 평균OVR(본인 제외) 대비 격차가
            # 리그등급 기준 이상이면 감독관계/평점 무관하게 재계약 안 함
            from constants import RELEASE_GAP_BY_GRADE
            _glow2 = _my_grade_tier(p2)
            _ovr_gap = 0
            if _glow2:
                _g2, _t2, _c2 = _glow2
                _team_avg2 = _my_team_avg_ovr(p2)
                _ovr_gap = _team_avg2 - p2.get("ovr", 40)
                _rel_threshold2 = RELEASE_GAP_BY_GRADE.get(_g2, 5)
            else:
                _rel_threshold2 = 5
            ovr_too_low = (_ovr_gap >= _rel_threshold2)

            wants_renew = (avg_r >= _base or rel >= 60) and not ovr_too_low
            if wants_renew:
                # 재계약 의사 있음 → UI 팝업용 플래그 저장
                # [버그수정] 기존: 현재 salary에 배율 적용 → 승강 후에도 이전 tier 연봉 기준
                # 수정: 현재 소속 리그/tier 기준으로 _calc_salary 재계산
                _gt = _my_grade_tier(p2)
                if _gt:
                    _rg2, _rt2, _rc2 = _gt
                    _fair_sal = _calc_salary(_rg2, _rt2, p2.get("ovr", 60), _rc2, year=year,
                                             team_id=p2.get("current_team_id"),
                                             talent_tier=p2.get("talent_tier"))
                else:
                    _fair_sal = p2.get("salary", 0)
                # 평점에 따라 ±15% 가감
                if avg_r >= _base + 0.5:   new_sal = int(_fair_sal * 1.15)
                elif avg_r >= _base:       new_sal = int(_fair_sal * 1.05)
                else:                      new_sal = int(_fair_sal * 0.95)
                _age = new_age
                if _age >= 33:
                    renew_yrs = 1
                elif _age >= 31:
                    renew_yrs = random.choices([1, 2], [65, 35])[0]
                elif _age >= 29:
                    renew_yrs = random.choices([1, 2, 3], [25, 50, 25])[0]
                elif _age <= 28 and avg_r >= _base + 0.5:
                    renew_yrs = random.choices([2, 3], [30, 70])[0]
                else:
                    renew_yrs = random.choices([1, 2, 3], [15, 45, 40])[0]
                update_player(_contract_renew_offer=new_sal,
                              _contract_renew_years=renew_yrs)
                add_log(f"📋 계약 만료! 팀에서 {renew_yrs}년 재계약을 제안합니다. "
                        f"(제시 연봉: {fmt_money(new_sal)})", "event", year, 52)
            else:
                if ovr_too_low:
                    add_log(f"📋 계약 만료. 팀 수준에 미달해 재계약을 원하지 않습니다. (격차 {_ovr_gap:+.0f})", "event", year, 52)
                else:
                    add_log(f"📋 계약 만료. 팀에서 재계약을 원하지 않습니다.", "event", year, 52)
                _save_career_entry(p2, year, 52, transfer_type="방출",
                                   allow_insert=False, exit_type="계약만료")
                update_player(current_team_id=0, current_league_id=0,
                              salary=0, contract_years=0, contract_end_year=0,
                              _contract_renew_offer=0, apply_attempts_used=0)

    # [2026-07 신설, 신민용 지적: "감독 관계 로직에 감독 교체 개념이 아예
    # 없다 — 같은 클럽에 계속 있으면 감독이 몇 년이 지나도 절대 안 바뀐다"]
    # manager_relation은 임대/팔림/새 입단처럼 '내가 팀을 옮길 때'만
    # 리셋되고, 반대로 '팀에 그대로 남아있는' 시즌엔 지금까지 아무 것도
    # 건드리지 않았다 — 그런데 현실은 내가 안 옮겨도 팀이 감독을 갈아
    # 치우는 경우가 흔하다(특히 성적 부진 시즌). 방출/임대/팔림/계약만료
    # 처리가 전부 끝난 뒤에도 여전히 같은 팀 소속이면(=이번 오프시즌에
    # 아무 이적도 없었으면), 낮은 확률로 감독 교체를 굴린다 — 바뀌면
    # manager_type을 새로 뽑고 관계를 중립(50)으로 리셋, 안 바뀌면 지금까지
    # 쌓인 관계를 그대로 이어간다.
    _p_final = get_player() or {}
    if _p_final.get("current_team_id") and not _pending_transfer_type:
        _maybe_change_manager(_p_final, year)
        _update_club_ambition(_p_final, year)


MANAGER_CHANGE_BASE_PROB = 0.12       # 시즌마다 기본 감독 교체 확률
MANAGER_CHANGE_POOR_RANK_BONUS = 0.15  # 하위 20%(강등권 근처)면 추가 확률


def _update_club_ambition(p, year):
    """[2026-07 신설, 신민용 지적: "구단목표가 전 시즌 성적이랑 무관하게
    뜬다"] 지금 소속팀에 그대로 남은 시즌마다, 방금 끝난 시즌 성적/승강
    이력을 근거로 club_ambition을 갱신한다(감독 교체 여부와는 무관하게
    매 시즌 갱신 — 목표는 감독 개인이 아니라 구단 차원의 것이므로).
    판단 근거가 없으면(데이터 부족) 기존 값을 그대로 둔다."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return
    conn = get_conn(); c = conn.cursor()
    row = c.execute("SELECT name FROM teams WHERE id=?", (tid,)).fetchone()
    if not row:
        conn.close()
        return
    tname = row["name"]
    new_amb = _infer_team_ambition(c, tid, tname, p.get("current_season", 1), year)
    conn.close()
    if new_amb and new_amb != p.get("club_ambition"):
        update_player(club_ambition=new_amb)
        add_log(f"🎯 구단 목표 변경: {new_amb}", "normal", year, 52)


def refresh_offer_rank_info(offers: list) -> list:
    """[2026-07 신설, 신민용 리포트: "오퍼 카드 순위/전적이 실제 리그
    순위표랑 안 맞는다"] rank_info는 오퍼가 처음 생성될 때 그 시점
    스냅샷으로 문자열로 굳어서 오퍼 dict에 박힌다. 근데 오퍼는
    save_pending_offer_state로 저장해뒀다가(재시작·창 재오픈 등) 나중에
    그대로 복원해서 다시 보여주는 경우가 있는데, 그 사이에 실제로 며칠이
    더 흘러 그 팀이 경기를 몇 개 더 뛰었으면 — 복원된 카드는 여전히
    예전 스냅샷을 보여준다(리그 순위표 창은 항상 실시간 재조회라 최신).
    그래서 승/무는 맞는데 패(→총 경기수)만 하나 어긋나는 식의 불일치가
    생긴다. 컵대회 경기가 섞여 든 게 아니라 순수 캐시 최신화 문제 —
    복원 시점에 다시 조회해서 갱신한다."""
    if not offers:
        return offers
    conn = get_conn(); c = conn.cursor()
    ss = conn.execute("SELECT current_week, current_season FROM season_state WHERE id=1").fetchone()
    for o in offers:
        try:
            o["rank_info"] = _get_team_rank_info(c, o["team_id"], ss=ss)
        except Exception:
            pass
    conn.close()
    return offers


def _maybe_change_manager(p, year):
    """[2026-07 신설] 지금 소속팀에 그대로 남은 시즌마다, 낮은 확률로
    감독이 교체된다(성적이 나쁠수록 더 잦음 — 실제로도 부진 시즌마다
    감독 경질이 흔하다). 바뀌면 manager_type을 새로 뽑고 manager_relation을
    중립값(50)으로 리셋한다 — 새로 온 사람과는 신뢰를 처음부터 쌓아야
    하니까. 안 바뀌면 아무 것도 안 건드려서 지금까지 쌓인 관계가 계속
    이어진다."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return
    prob = MANAGER_CHANGE_BASE_PROB
    rows = get_league_standings_by_team(tid)
    if rows:
        total = len(rows)
        rank = next((i + 1 for i, r in enumerate(rows) if r["id"] == tid), None)
        if rank and total >= 4:
            pct = rank / total
            if pct >= 0.8:      # 하위 20%(강등권 근처) — 경질 압박 큼
                prob += MANAGER_CHANGE_POOR_RANK_BONUS
            elif pct <= 0.15:   # 최상위권 — 안정적, 교체 확률 낮춤
                prob = max(0.03, prob - 0.05)
    prob = max(0.03, min(0.5, prob))
    if random.random() >= prob:
        return   # 감독 유임 — 관계 그대로 이어짐

    from constants import MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS
    new_type = random.choices(MANAGER_TYPE_LIST, weights=MANAGER_TYPE_WEIGHTS)[0]
    update_player(manager_type=new_type, manager_relation=50)
    conn = get_conn(); c = conn.cursor()
    row = c.execute("SELECT name FROM teams WHERE id=?", (tid,)).fetchone()
    conn.close()
    tname = row["name"] if row else "구단"
    add_log(f"📰 {tname} 감독 교체! 새 감독 성향: {new_type}  |  감독 관계 초기화(50)",
            "event", year, 52)


def _apply_rank_happiness(p, year):
    """이슈9: 시즌 종료 시 리그 순위에 따른 행복도 변화."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return
    rank_str = get_team_rank(tid)
    try:
        rn = int(rank_str.split("위")[0].replace("공동", "").strip())
    except Exception:
        return

    cur_happy = p.get("happiness", 50)  # [최적화] p에서 직접 읽기 (get_player 재조회 제거)
    delta = 0
    msg   = ""
    if rn == 1:
        delta, msg = +30, "🏆 리그 1위! 행복도 +30"
    elif rn == 2:
        delta, msg = +15, "🥈 리그 2위! 행복도 +15"
    elif rn == 3:
        delta, msg = +10, "🥉 리그 3위! 행복도 +10"
    elif rn == 7:
        delta, msg = -30, "😞 리그 7위... 행복도 -30"
    elif rn == 8:
        delta, msg = -50, "😭 리그 8위(강등권)! 행복도 -50"

    if delta != 0:
        new_happy = max(0, min(100, cur_happy + delta))
        update_player(happiness=new_happy)
        add_log(f"   {msg}", "event", year, 52)


def _check_forced_release(p, year, prior_season_matches=None,
                           prior_season_rating_sum=None, prior_season_rating_cnt=None):
    """방출 조건: 팀 평균OVR(본인 제외) 대비 격차가 리그등급 기준 이상 AND 감독관계 30 미만.
    재계약 거부: 격차 기준 초과 시 감독관계 무관 (계약 만료 시 별도 처리).
    오버페이 + 부진 선수는 방출 전 하위팀 이적(팔림) 우선 시도.

    [2026-07 버그수정] prior_season_* 인자는 _end_of_season이 시즌 통계를
    0으로 리셋하기 '전에' 떠둔 스냅샷. 이 함수는 리셋 '후'에 호출되므로
    p.get("season_matches"/"season_rating_sum"/"season_rating_cnt")는 이미
    전부 0 — 반드시 이 인자들을 우선 사용해야 실제 이번 시즌 활약을 본다."""
    rel  = p.get("manager_relation", 50)
    tid  = p.get("current_team_id", 0)
    if not tid:
        return
    if p.get("loan_from_team_id", 0):
        return   # [2026-07 신설] 임대 중엔 임대처가 소유권이 없으므로 방출/팔림 평가 자체를 건너뜀

    # ── 막 합류한 선수 보호 ───────────────────────────────
    try:
        conn0 = get_conn()
        open_row = conn0.execute(
            """SELECT start_year, start_week FROM career_entries
               WHERE team_id=? AND end_year=0
               ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
        conn0.close()
        if open_row and open_row["start_year"] == year and open_row["start_week"] >= 25:
            return   # 올 시즌 후반 합류 → 방출/판매 평가 스킵
    except Exception:
        pass

    rc = prior_season_rating_cnt if prior_season_rating_cnt is not None else p.get("season_rating_cnt", 0)
    rs = prior_season_rating_sum if prior_season_rating_sum is not None else p.get("season_rating_sum", 0.0)
    avg_rating = round(rs/rc,2) if rc > 0 else 6.0
    cur_ovr = p.get("ovr", 40)
    _matches_played = prior_season_matches if prior_season_matches is not None else p.get("season_matches", 0)

    # ── [2026-07 신설] 어린 선수 출전기회 부족 → 방출/팔림보다 먼저 임대 시도 ──
    if _check_loan_candidate(p, year, cur_ovr, season_matches=_matches_played,
                              avg_rating=avg_rating, rating_cnt=rc):
        return   # 임대 처리 완료 → 이하 방출/팔림 로직 스킵

    # 팀 평균OVR(본인 제외) 대비 격차
    team_avg = _my_team_avg_ovr(p)
    gap = team_avg - p.get("ovr", 40)   # +면 내가 팀 수준에 못 미침

    # 리그 등급 기반 방출 기준 조회
    from constants import RELEASE_GAP_BY_GRADE, RELEASE_REL_THRESHOLD
    _glow = _my_grade_tier(p)
    if _glow:
        _grade, _tier, _country = _glow
        release_threshold = RELEASE_GAP_BY_GRADE.get(_grade, 5)
    else:
        release_threshold = 5

    # ── [기능2] 감독 성향(release_relax)으로 임계치 조정 ──
    from constants import MANAGER_TYPES, OFFER_AMBITION
    mt = MANAGER_TYPES.get(p.get("manager_type", "베테랑 신뢰"), {})
    relax = mt.get("release_relax", 0.0)
    press = OFFER_AMBITION.get(p.get("club_ambition", "중위권 안정"), {}).get("press", 1.0)
    # 관대한 감독은 기준을 1~2 올려주고, 성과주의 감독은 더 빡빡하게
    adjusted_threshold = release_threshold + round(relax * 2) - round((press - 1.0) * 1)

    # ── 오버페이 + 부진 → 팔림(강제 이적) 우선 시도 ──
    cur_salary = p.get("salary", 0)
    cur_ovr    = p.get("ovr", 40)
    if _glow and cur_salary > 0:
        _grade2, _tier2, _country2 = _glow
        fair_salary = _calc_salary(_grade2, _tier2, cur_ovr, _country2, year=year,
                                   team_id=p.get("current_team_id"),
                                   talent_tier=p.get("talent_tier"))
        overpay = (cur_salary / fair_salary) if fair_salary > 0 else 99
        is_overpaid = overpay >= 1.6
        is_underperforming = (avg_rating < 6.3 and rc >= 5) or (gap >= adjusted_threshold)
        contract_left = p.get("contract_end_year", 0) > year
        if is_overpaid and is_underperforming and contract_left:
            if _try_sell_player(p, year, cur_ovr):
                return   # 팔림 처리 완료 → 방출 로직 건너뜀

    # ── 핵심 방출 조건: 격차 기준 초과 AND 감독관계 30 미만 ──
    # [2026-07 버그수정, 신민용+GPT 리포트: "영플레이어 수상에 컵 우승
    # 멤버, ACL 16강 출전까지 한 선수가 방출당하는 건 너무 가혹하다 —
    # 개인 수상·우승 이력이 방출 판단에 전혀 반영이 안 된다"] 이번
    # 시즌에 개인상을 받았거나(awards, is_mine=1) 팀이 트로피를
    # 들었으면(trophy_log) — "이제 막 증명한 선수를 자르기는 구단
    # 입장에서도 부담스럽다"는 걸 반영해 기준을 완화한다(+3).
    _has_award_or_trophy = False
    try:
        _conn_aw = get_conn()
        _aw = _conn_aw.execute(
            "SELECT 1 FROM awards WHERE year=? AND is_mine=1 LIMIT 1", (year,)).fetchone()
        if _aw:
            _has_award_or_trophy = True
        else:
            _team_row_aw = _conn_aw.execute("SELECT name FROM teams WHERE id=?", (tid,)).fetchone()
            if _team_row_aw:
                _tr = _conn_aw.execute(
                    "SELECT 1 FROM trophy_log WHERE year=? AND team_name=? LIMIT 1",
                    (year, _team_row_aw["name"])).fetchone()
                if _tr:
                    _has_award_or_trophy = True
        _conn_aw.close()
    except Exception:
        pass
    if _has_award_or_trophy:
        adjusted_threshold += 3

    cond_level = gap >= adjusted_threshold and rel < RELEASE_REL_THRESHOLD
    # 감독관계 20 미만은 격차 무관 방출 (극단적 불화) — 단, 이것도 이번
    # 시즌 개인상/우승 이력이 있으면 더 심하게(15 미만) 나빠야만 발동.
    cond_hostile = rel < (15 if _has_award_or_trophy else 20) and avg_rating < 5.5 and rc >= 5

    if cond_level or cond_hostile:
        if rel < 20:
            reason = "감독 관계 극도 악화"
        else:
            reason = "리그 수준 미달 + 감독 신뢰 상실"
        # [2026-07 버그수정, 신민용+GPT 리포트: "1년 계약이 자연스럽게
        # 끝난 건데도 방출로 뜬다 — 계약기간 남았을 때 중도 해지하는
        # '방출'이랑, 계약이 그냥 끝나서 재계약 안 하는 '계약만료'는
        # 의미가 다르다"] 이 조건(성적 부진+감독관계 나쁨)은 원래 "왜
        # 재계약을 안 하는지"를 판단하는 데 쓰이는 것이지, 계약기간이
        # 남았는데도 억지로 끊는 상황과는 다르다 — 이 시점에 계약이 이미
        # 끝났거나 끝나가는 중이면(contract_end_year<=year) '계약만료'로
        # 표시하고, 진짜 계약기간이 남았는데 내보내는 경우만 '방출'을
        # 쓴다. 사유(성적 부진 등)는 로그 메시지에 그대로 남겨 서사는
        # 유지한다.
        _contract_already_over = p.get("contract_end_year", 0) <= year
        if _contract_already_over:
            _exit_label = "계약만료"
            add_log(f"📋 계약 만료. {reason}으로 재계약하지 않습니다.  {year}년  "
                    f"(평점 {avg_rating}, 감독관계 {rel}, 수준격차 {gap:+.0f})", "event", year, 52)
        else:
            _exit_label = "방출"
            add_log(f"😡 {reason}으로 방출!  {year}년  (평점 {avg_rating}, 감독관계 {rel}, 수준격차 {gap:+.0f})", "event", year, 52)
        _save_career_entry(p, year, 52, transfer_type="방출", allow_insert=False,
                           exit_type=_exit_label)
        update_player(current_team_id=0, current_league_id=0,
                      salary=0, manager_relation=50,
                      contract_years=0, contract_end_year=0, apply_attempts_used=0)


def _my_grade_tier(p):
    """내 소속 팀의 (리그등급, 리그티어, 국가명) 반환. 무소속이면 None.
    [리그등급 분리] 국대 등급 대신 COUNTRY_LEAGUE_GRADE 사용."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    conn = get_conn()
    try:
        row = conn.execute("""SELECT cn.grade as grade, l.tier as tier, cn.name as country
                              FROM teams t JOIN leagues l ON t.league_id=l.id
                              JOIN countries cn ON l.country_id=cn.id
                              WHERE t.id=?""", (tid,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    from constants import get_league_grade
    league_grade = get_league_grade(row["country"], row["grade"])
    return (league_grade, row["tier"], row["country"])


def _try_loan_player(p, year, cur_ovr):
    """[2026-07 신설, 신민용 요청: "팔림은 사실 완전 이적이라 임대랑 다르다,
    임대를 따로 추가하는 게 맞다"] 출전 기회가 부족한 젊은 선수를 확실히
    주전으로 뛸 수 있는 약한 팀에 임대 보낸다.

    '팔림'과의 핵심 차이:
      - 팔림: 새 팀에서 완전히 새 계약(연봉·계약년수) 체결, 원소속팀 복귀 없음
      - 임대: 원소속팀 계약(연봉·계약년수·contract_end_year)을 그대로 유지한 채
        팀 소속만 임시로 옮긴다. loan_end_year(1~2시즌 뒤)가 되면
        _return_from_loan_if_due()가 자동으로 원소속팀에 복귀시킨다.

    [2026-07 재설계, 신민용 지적: "임대처에 지리적 제약이 없어서 K리그
    선수가 아무 대륙 팀으로나 임대 갈 수 있었다"] 예전엔 OVR대만 맞으면
    전세계 팀 중 완전 무작위로 뽑았다 — 실제 임대는 압도적으로 자국
    하위팀 아니면 같은 대륙 리그로 간다(다른 대륙으로의 임대는 매우
    드묾). 이제 자국 → 같은 대륙 → (그래도 없으면) 전세계 순으로
    단계적으로 찾는다.

    성공 시 True. 적당한 임대처를 못 찾으면 False(→ 기존 방출/팔림 로직으로)."""
    conn = get_conn(); c = conn.cursor()
    try:
        my_tid = p.get("current_team_id", 0)
        origin = c.execute("""SELECT cn.id AS country_id, cn.continent
                               FROM teams t JOIN leagues l ON t.league_id=l.id
                               JOIN countries cn ON l.country_id=cn.id
                               WHERE t.id=?""", (my_tid,)).fetchone()
        _ovr_lo, _ovr_hi = cur_ovr - 14, cur_ovr - 3

        def _find(extra_where, params):
            return c.execute(f"""
                SELECT t.id, t.name, l.id as lid, l.name as lname, l.tier,
                       cn.name as country, cn.flag, cn.grade
                FROM teams t
                JOIN leagues l ON t.league_id=l.id
                JOIN countries cn ON l.country_id=cn.id
                WHERE t.id != ?
                  AND (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) BETWEEN ? AND ?
                  {extra_where}
                ORDER BY RANDOM() LIMIT 1
            """, (my_tid, _ovr_lo, _ovr_hi, *params)).fetchone()

        row = None
        if origin:
            # 1순위: 자국 하위팀 (임대의 압도적 다수 케이스)
            row = _find("AND cn.id=?", (origin["country_id"],))
            if not row:
                # 2순위: 같은 대륙 다른 나라
                row = _find("AND cn.continent=? AND cn.id!=?",
                             (origin["continent"], origin["country_id"]))
        if not row:
            # 3순위: 그래도 없으면(자국·대륙에 OVR대 맞는 팀 자체가 없는 극단적
            # 상황) 예전처럼 전세계에서라도 찾는다 — 임대 자체가 불발되는
            # 것보다는 낫다.
            row = _find("", ())
        conn.close()
    except Exception:
        try: conn.close()
        except Exception: pass
        return False

    if not row:
        return False

    orig_team_id   = p.get("current_team_id", 0)
    orig_league_id = p.get("current_league_id", 0)
    orig_tier      = p.get("current_tier", 1)

    # [2026-07 신설] 임대 기간 가변화 — 예전엔 항상 "다음 시즌 종료까지"
    # (사실상 1년) 고정이었다. 현재 엔진이 임대 복귀를 시즌 종료 시점
    # (연 단위)에만 체크하는 구조라 6개월 같은 시즌 중간 복귀는 구조적으로
    # 아직 못 넣지만, 1시즌/2시즌 임대는 구분해서 반영한다 — 실제로도
    # 단기(1시즌) 임대가 압도적으로 많고, 장기(2시즌) 임대는 소수 케이스.
    loan_years = random.choices([1, 2], weights=[80, 20])[0]

    # 원소속팀 커리어 항목을 닫되(exit_type='임대'), 연봉/계약은 그대로 둔다 —
    # _try_sell_player와 달리 새 계약을 만들지 않는 게 핵심.
    # [2026-07 신설] loan_partner_team에 임대 '도착지' 팀명을 함께 저장 —
    # UI에서 "임대(1년)"이 아니라 "OO에 임대(1년)"로 상대팀을 보여주기 위함.
    _save_career_entry(p, year, 52, allow_insert=False, exit_type="임대",
                       loan_partner_team=row["name"])

    global _pending_transfer_type
    _pending_transfer_type = "임대"
    update_player(current_team_id=row["id"], current_league_id=row["lid"],
                  current_tier=row["tier"], manager_relation=50,
                  loan_from_team_id=orig_team_id, loan_from_league_id=orig_league_id,
                  loan_from_tier=orig_tier, loan_end_year=year + loan_years)
    add_log(f"🔄 {row['name']}로 임대!  {row['lname']}({row['tier']}부)  "
            f"|  연봉·계약은 원소속팀 그대로 유지  ({loan_years}시즌 뒤 복귀)",
            "event", year, 52)
    return True


def _return_from_loan_if_due(p, year):
    """[2026-07 신설] 임대 기간(loan_end_year)이 끝났으면 원소속팀으로
    자동 복귀시킨다. 원소속팀이 그사이 승강했을 수 있으니 현재 소속
    league_id/tier를 다시 조회해서 반영한다."""
    loan_from = p.get("loan_from_team_id", 0)
    if not loan_from:
        return
    loan_end_year = p.get("loan_end_year", 0)
    if loan_end_year and loan_end_year > year:
        return   # 아직 임대 기간 안 끝남

    conn = get_conn(); c = conn.cursor()
    team_row = c.execute("SELECT id, name, league_id FROM teams WHERE id=?", (loan_from,)).fetchone()
    if not team_row:
        conn.close()
        # 원소속팀이 사라진 극단적 케이스 — 임대 필드만 정리하고 지금 팀에 눌러앉는다.
        update_player(loan_from_team_id=0, loan_from_league_id=0,
                      loan_from_tier=0, loan_end_year=0)
        return
    league_row = c.execute("SELECT name, tier FROM leagues WHERE id=?",
                            (team_row["league_id"],)).fetchone()
    conn.close()
    new_tier = league_row["tier"] if league_row else p.get("loan_from_tier", 1)

    # 임대처 커리어 항목을 닫는다(exit_type='임대 종료').
    # [2026-07 신설] loan_partner_team에 복귀할 '원소속팀'명을 함께 저장 —
    # UI에서 "임대 종료(1년)"이 아니라 "OO 복귀"로 보여주기 위함.
    _save_career_entry(p, year, 52, allow_insert=False, exit_type="임대 종료",
                       loan_partner_team=team_row["name"])

    global _pending_transfer_type
    _pending_transfer_type = "임대 복귀"
    update_player(current_team_id=team_row["id"], current_league_id=team_row["league_id"],
                  current_tier=new_tier,
                  loan_from_team_id=0, loan_from_league_id=0,
                  loan_from_tier=0, loan_end_year=0)
    add_log(f"🔄 임대 종료 → {team_row['name']}로 복귀!  "
            f"({league_row['name'] if league_row else ''} {new_tier}부)", "event", year, 52)


def _check_loan_candidate(p, year, cur_ovr, season_matches=None,
                           avg_rating=None, rating_cnt=0) -> bool:
    """[2026-07 신설] 임대 후보 판정 — 젊고(23세 이하) 출전 기회가 부족한
    (풀시즌 대비 출전 비율이 낮은) 선수만 대상. 오버페이/부진 선수는
    기존처럼 팔림/방출 로직이 담당하므로 여기서 건드리지 않는다.

    [2026-07 버그수정, 신민용 리포트: "발롱도르급 선수가 임대를 간다"]
    두 가지를 고쳤다:
    1) season_matches를 호출부에서 리셋 '전' 스냅샷으로 받는다 — 예전엔
       이미 0으로 리셋된 값을 읽어서 play_ratio가 항상 0/full_season=0이
       되어 "적게 뛴 선수만" 걸러야 할 조건이 사실상 전원 통과였다.
    2) 실제로 적게 뛰었더라도, 뛴 경기에서 평점이 확실히 좋았다면
       (elite 수준 활약) 벤치 자원이 아니라 그냥 스쿼드 로테이션/부상
       등 다른 사정일 뿐 — 임대 후보에서 제외한다. cur_ovr 자체가 이미
       엘리트 등급(월드클래스 하한 부근)이면 그것만으로도 제외."""
    if p.get("loan_from_team_id", 0):
        return False   # 이미 임대 중
    age = p.get("age", 30)
    if age > 23:
        return False
    contract_left = p.get("contract_end_year", 0) - year
    if contract_left < 2:
        return False   # 계약이 곧 끝나면 임대 대신 자연스럽게 이적/재계약으로
    full_season = _league_full_season_matches(p)
    if not full_season:
        return False
    matches_played = season_matches if season_matches is not None else p.get("season_matches", 0)
    play_ratio = matches_played / full_season
    if play_ratio >= 0.30:
        return False   # 이미 어느 정도 뛰고 있으면 임대 불필요
    # [엘리트 세이프가드] 뛴 경기 수가 적어도 평점이 확실히 좋았거나(부상/로테이션
    # 등으로 결장 많았을 뿐 폼은 증명됨), 이미 OVR 자체가 엘리트급이면 —
    # "출전 기회 부족한 유망주"가 아니라 이미 검증된 선수이므로 임대 대상에서 제외.
    if avg_rating is not None and rating_cnt >= 3 and avg_rating >= 7.5:
        return False
    # [2026-08 수정] 하드코딩된 81(구버전 elite 하한) 대신 TALENT_TIERS를
    # 직접 참조 — 재능 등급 수치가 나중에 또 조정돼도 이 기준이 자동으로
    # 따라간다.
    if cur_ovr >= TALENT_TIERS["elite"]["cap_min"]:
        return False
    team_avg = _my_team_avg_ovr(p)
    gap = team_avg - cur_ovr
    if gap < 3:
        return False   # 벤치가 아니라 그냥 못 뛴 것뿐일 수 있음 — 팀 격차가 뚜렷할 때만
    return _try_loan_player(p, year, cur_ovr)


def _try_sell_player(p, year, cur_ovr):
    """오버페이+부진 선수를 현재 OVR에 맞는 하위 팀으로 강제 이적(팔림).
    성공 시 True. 적당한 팀을 못 찾으면 False(→ 방출 로직으로).

    [2026-07 버그수정, 신민용 리포트] 임대(_try_loan_player) 시스템을 추가하며
    이 함수의 `def` 라인이 실수로 삭제돼(git diff 확인) _try_sell_player가
    미정의 상태가 됐었다 — 오버페이+부진 선수가 있으면 _check_forced_release가
    NameError로 죽어 연도전환 자체가 크래시하는 버그였다. 로직은 원래
    그대로(범위·연봉 계산 등 변경 없음) 함수 정의만 복원."""
    conn = get_conn(); c = conn.cursor()
    try:
        # 현재 OVR보다 팀 평균이 약간 낮거나 비슷한 팀 (내가 주전급일 수 있는 곳)
        # 현재 리그보다 같거나 한 단계 낮은 수준을 우선
        row = c.execute("""
            SELECT t.id, t.name, l.id as lid, l.name as lname, l.tier,
                   cn.name as country, cn.flag, cn.grade,
                   (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) as tavg
            FROM teams t
            JOIN leagues l ON t.league_id=l.id
            JOIN countries cn ON l.country_id=cn.id
            WHERE t.id != ?
              AND (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) BETWEEN ? AND ?
            ORDER BY RANDOM() LIMIT 1
        """, (p.get("current_team_id", 0), cur_ovr - 6, cur_ovr + 2)).fetchone()
        conn.close()
    except Exception:
        try: conn.close()
        except Exception: pass
        return False

    if not row:
        return False

    # 새 팀 연봉 (새 OVR 기준, 리그 부유도 반영)
    new_salary = _calc_salary(get_league_grade(row["country"], row["grade"]), row["tier"], cur_ovr, row["country"], row["name"], year=year, team_id=row["id"],
                              talent_tier=p.get("talent_tier"))

    # (변경) 떠나는 팀에는 우승을 주지 않는다.
    # 우승은 '시즌 종료 시점 소속팀'이 1위일 때만 _process_promotion_relegation에서 인정.
    # 떠나기 전 순위는 team_rank(커리어 기록)에만 남는다.
    # 이전 팀 커리어 항목을 닫음 + 떠난 경로='팔림' 기록
    _save_career_entry(p, year, 52, allow_insert=False, exit_type="팔림")

    # 새 팀으로 이적 (계약은 새로 — 나이/티어 기반)
    age_now = p.get("age", 25)
    c_yrs = _calc_contract_years(age_now, row["tier"], row["country"])
    c_end = year + c_yrs   # 시즌 종료(52주) 시점이므로 다음 시즌부터 카운트

    global _pending_transfer_type
    _pending_transfer_type = "팔림"
    update_player(current_team_id=row["id"], current_league_id=row["lid"],
                  salary=new_salary, manager_relation=50,
                  contract_years=c_yrs, contract_end_year=c_end,
                  current_tier=row["tier"])
    add_log(f"💸 {row['name']}로 팔림!  {row['lname']}({row['tier']}부)  "
            f"|  연봉 {fmt_money(new_salary)}  (몸값 대비 부진으로 손절)", "event", year, 52)
    return True


def _get_promotion_policy(team_count: int) -> dict:
    """상위 리그(강등 당하는 쪽) 관점의 승강 정책 — 자동 강등 인원과 PO
    존재 여부를 정한다 (2026-07 재설계, 신민용 최종안: "PO는 항상 4팀
    브래킷이 마지막 생존권 1장을 놓고 경쟁, 상위 리그는 항상 1팀만
    위태로움").

    [자동 이동은 반드시 '한쪽' 기준으로만 대칭 결정해야 하는 이유] 실제
    게임 데이터를 보면 같은 나라의 1부/2부 팀 수가 다른 경우가 68%
    (208개국 중 141개국)나 된다 — 그래서 "자동 강등 인원"과 "자동 승격
    인원"을 각자 자기 리그 크기로 독립 계산하면 두 값이 어긋나 리그
    전체 팀 수가 서서히 무너진다. 그래서 자동 이동분은 항상 위 리그
    크기만 기준으로 정하고, 아래 리그에도 그대로(대칭) 적용한다 — 이건
    기존 설계와 동일.

    [PO는 왜 안전한가] PO는 브래킷에 몇 팀이 들어오든(2팀이든 4팀이든)
    최종적으로 딱 1팀만 실제로 자리를 바꾼다(브래킷에서 진 나머지는
    원래 있던 리그에 그냥 남을 뿐 이동이 아니다) — 그래서 아래 리그의
    PO 브래킷 크기는 자동 이동분과 달리 아래 리그 자기 크기로 독립
    결정해도 총 이동량 보존이 깨지지 않는다(_get_po_bracket_size 참고).

      team_count < 6:  자동 1, PO 없음
      6~19팀:          자동 1, PO 1자리(항상 1팀만 위태)
      20~25팀:         자동 2, PO 1자리
      26팀 이상:        자동 3, PO 1자리
    """
    if team_count < 6:
        return {"auto": 1, "po": 0}
    elif team_count < 20:
        return {"auto": 1, "po": 1}
    elif team_count <= 25:
        return {"auto": 2, "po": 1}
    else:
        return {"auto": 3, "po": 1}


def _get_po_bracket_size(lower_team_count: int) -> int:
    """아래 리그가 PO에 몇 팀을 보내는 미니 토너먼트를 치를지 — 아래 리그
    자기 크기로 독립 결정한다(위 함수 docstring 참고, 총 이동량 보존과
    무관해서 안전). 8~10팀처럼 작은 리그는 2팀(단판 예선 1경기 → 결승),
    12팀 이상은 4팀(준결승 2경기 → 결승 → 최종 승강전, 1v4·2v3 시드)."""
    return 2 if lower_team_count < 12 else 4


def _finalize_club_season(p, year):
    """[2026-07 리팩터, 승강 플레이오프 도입 — 신민용 설계] 예전엔
    _end_of_season() 안에 있던 "미완료 경기 정리 + 순위 확정 + 자동승강/
    PO대기 생성" 부분을 시간축에 맞게 분리했다. 이 부분은 클럽 시즌이
    실제로 끝나는 시점(CLUB_SEASON_END_DAY=300일, 43주)에 확정돼야
    한다 — 승강 플레이오프(44주)가 이 결과를 그대로 이어받아야 하는데,
    원래는 이 계산 자체가 훨씬 늦은 시점(52주 종료→새해 진입)에야
    실행됐다(_end_of_season의 나머지 부분과 함께).

    계산 결과는 이 시점을 옮겨도 달라지지 않는다 — CLUB_SEASON_END_DAY
    이후(44~52주)엔 클럽 리그 경기가 전혀 없어서(국제대회 전용 기간)
    match_results가 그 사이 안 바뀌기 때문에, 43주에 계산하든 52주에
    계산하든 같은 숫자가 나온다. advance_days에서 day==CLUB_SEASON_END_DAY
    일 때 1회 호출된다."""
    rs = p.get("season_rating_sum", 0.0)
    rc = p.get("season_rating_cnt", 0)
    season_avg_rating = round(rs / rc, 2) if rc else 6.0

    # [2026-07 계측 추가, 신민용 리포트: "43주 44주 렉"] finalize_club_season
    # 총량 중 미완료 경기 정리 vs 승강제 판정(667개 리그 순회) 중 어느 쪽이
    # 실제 병목인지 분리 측정 — 아직 최적화는 하지 않고 숫자만 확보한다.
    import time as _time_fcs
    _tfcs0 = _time_fcs.perf_counter()
    _finish_incomplete_matches_for_season(p.get("current_season", 1))
    _tfcs1 = _time_fcs.perf_counter()
    _process_promotion_relegation(year, season_avg_rating)
    _tfcs2 = _time_fcs.perf_counter()
    print(f"[PERF-SEASON] finalize_club_season 세부: "
          f"finish_incomplete_matches={_tfcs1-_tfcs0:.3f}s | "
          f"promotion_relegation={_tfcs2-_tfcs1:.3f}s")


def sweep_all_affiliate_conflicts(year: int) -> None:
    """[2026-08 신설] enforce_affiliate_children_tier()는 boundary match 1건이
    끝날 때마다 그 즉시 호출되는데, 같은 플레이오프 주간(44~52주) 안에서
    "먼저 끝난 경계"의 교정이 "나중에 끝나는 다른 경계"의 결과로 다시
    덮어써지는 경우가 실측으로 발견됐다 — 예: 산하팀이 tier2→3으로
    강제 강등됐는데, 그 팀이 애초에 다른 tier1/2 경계 PO에도 이미
    참가자로 확정돼 있었다면(day300 시점 대진표는 고정), 그 경기가 나중에
    끝나면서 승리 시 다시 tier2로 되돌려버린다. 개별 hook만으로는 이런
    "매치 처리 순서" 문제를 완전히 막을 수 없어서, 그 주(week)의 PO 처리가
    끝날 때마다 전체 산하팀-모팀 쌍을 한 번 더 훑어 남은 충돌을 정리한다
    (parent_team_id가 있는 팀 수만큼만 순회 — 팀 전체 스캔보다 훨씬 가벼움).
    """
    import constants
    if not getattr(constants, "AFFILIATE_PROMOTION_RESTRICTION", False):
        return
    conn = get_conn()
    c = conn.cursor()
    parent_ids = {r["parent_team_id"] for r in c.execute(
        "SELECT DISTINCT parent_team_id FROM teams WHERE parent_team_id IS NOT NULL").fetchall()}
    for pid in parent_ids:
        enforce_affiliate_children_tier(pid, year)


def enforce_affiliate_children_tier(parent_team_id: int, year: int) -> None:
    """[2026-08 신설, 신민용 리포트: "산하팀이 모팀이랑 같은 티어로 남아있는게
    보인다" — 근본 원인 수정] '산하팀은 항상 모팀보다 낮은 tier'라는 불변식은
    기존에 _process_promotion_relegation()(연 1회, CLUB_SEASON_END_DAY=43주차)
    안에서만 검사됐다. 그런데 승강 플레이오프(promotion_playoff_engine, 44~52주)
    는 그 검사 *이후*에 진행되고, 거기서 결정되는 승격/강등은 이 함수가 다시
    돌기 전까지(=다음 시즌 43주차, 최대 약 1시즌 가까이) 전혀 재검증되지 않아
    모팀이 플레이오프로 강등되면 산하팀과 tier가 같아지거나 역전된 상태로
    한 시즌 내내 방치됐다(실측 확인: 예를 들어 VfL 오스나브뤼크가 PO로
    강등되면 VfL 오스나브뤼크 II가 그 시즌 내내 모팀과 동일 tier로 남음).

    이 함수는 promotion_playoff_engine._finalize_boundary_match()가 팀 tier를
    바꿀 때마다(승격/강등 양쪽 다) 그 즉시 호출된다 — 방금 tier가 바뀐 팀을
    "모팀"으로 놓고, 그 팀을 parent_team_id로 참조하는 산하팀들의 tier를
    검사해 즉시 보정한다. 산하팀 자신도 다른 팀의 모팀일 수 있으므로(다단계
    산하 구조 대비) 큐 방식으로 재귀 처리한다.

    _process_promotion_relegation의 '경우 ②'(산하팀은 안 움직였는데 모팀이
    강등돼 충돌 발생 → 산하팀 1티어 추가 강등)와 판정 로직은 동일하되, 시즌
    끝을 기다리지 않고 그 순간 즉시 반영한다는 점만 다르다.
    """
    import constants
    if not getattr(constants, "AFFILIATE_PROMOTION_RESTRICTION", False):
        return
    conn = get_conn()
    c = conn.cursor()

    queue = [parent_team_id]
    seen = set()
    any_change = False
    while queue:
        pid = queue.pop()
        if pid in seen:
            continue
        seen.add(pid)
        prow = c.execute("SELECT current_tier FROM teams WHERE id=?", (pid,)).fetchone()
        if not prow:
            continue
        p_tier = prow["current_tier"]

        children = c.execute(
            "SELECT id, name, current_tier FROM teams WHERE parent_team_id=?", (pid,)).fetchall()
        for child in children:
            child_tier = child["current_tier"]
            if child_tier > p_tier:
                continue  # 정상 — 이미 모팀보다 아래
            # [2026-08 재설계] 예전엔 여기서 산하팀 tier를 강제로 한 단계 더
            # 내렸다(리그 순위와 무관한 인위적 조작). 이제는 리그/tier를
            # 전혀 건드리지 않고, 모팀이 산하팀에서 부족 포지션 위주로
            # 선수를 콜업해 산하팀 전력만 낮춘다 — 산하팀은 같은 리그에
            # 남아 정상적으로 경쟁한다. tier가 안 바뀌므로 그 밑의 손자팀
            # 으로 재귀 전파할 것도 없다(기존의 queue.append 제거).
            picked_ids = _affiliate_callup_from_child(conn, pid, child["id"])
            if picked_ids:
                print(f"[산하팀 전력조정-PO] {year}년 {child['name']}에서 "
                      f"{len(picked_ids)}명 콜업 (모팀 플레이오프 결과와 tier 충돌 → "
                      f"리그·순위는 유지, 산하팀 전력만 조정)")
                any_change = True

    if any_change:
        conn.commit()
        _invalidate_team_ovr_cache()


def _affiliate_callup_from_child(conn, parent_id, child_id,
                                  max_players=3, min_child_remaining=11,
                                  max_pct=0.25):
    """[2026-08 재설계, 신민용 확정: "강등 자체를 막으면 안 된다"] 1군(parent_id)이
    강등(정규 시즌 종료 또는 승강 플레이오프)으로 산하팀(child_id)과 같은
    tier가 됐을 때, 예전처럼 산하팀을 강제로 한 티어 더 밀어내리는 대신
    1군이 산하팀 선수단에서 부족한 포지션 위주로 일부를 콜업해 산하팀
    전력만 낮춘다 — 산하팀의 리그 소속·tier·순위는 전혀 건드리지 않고
    그대로 같은 리그에서 정상적으로 시즌을 치르게 한다.

    포지션 선정: 1군 스쿼드에서 포지션별(POSITIONS) 목표 보유 인원
    (_TARGET_DEPTH)보다 실제 보유가 적은 포지션만 "부족 포지션"으로 보고,
    부족한 정도가 큰 포지션부터 우선한다. 그냥 OVR 높은 순으로 아무나
    데려오지 않는다 — 실제로 필요한 자리만 채운다.

    콜업 인원은 다음 세 상한 중 가장 작은 값으로 제한한다(과도한 약화 방지):
      - max_players(기본 3명)
      - 산하팀 스쿼드의 max_pct(기본 25%)
      - 산하팀에 최소 min_child_remaining(기본 11명 — 선발 11명을 채울
        최소 인원)을 남기고 남는 인원

    [2026-08 수치 조정] 처음엔 실제 축구단 기준(18~20명 이상 유지)으로
    잡았는데, 이 게임의 실제 팀당 스쿼드 인원은 평균 11명(최소 3~최대
    19명, 실측)으로 훨씬 작다 — 18명 기준을 그대로 쓰면 사실상 거의
    모든 팀에서 cap이 0이 되어 콜업이 전혀 발동하지 않는 문제가 있었다
    (12시즌 실측 시뮬레이션에서 실제로 0건 확인 후 발견). 이 게임의
    실제 스쿼드 규모에 맞춰 "최소 11명(선발 인원)"으로 낮췄다.

    반환: 실제로 이동한 선수 id 리스트(비어있으면 이동 없음 — 로그용).
    """
    from constants import POSITIONS
    c = conn.cursor()
    parent_squad = c.execute(
        "SELECT id, position, ovr FROM ai_players WHERE team_id=?", (parent_id,)).fetchall()
    child_squad = c.execute(
        "SELECT id, position, ovr FROM ai_players WHERE team_id=?", (child_id,)).fetchall()
    if not child_squad:
        return []

    _count_by_pos = {p: 0 for p in POSITIONS}
    for r in parent_squad:
        if r["position"] in _count_by_pos:
            _count_by_pos[r["position"]] += 1

    # 포지션별 목표 보유 인원(스쿼드 depth 기준치) — 목표치보다 실제
    # 보유가 적은 포지션만 "부족"으로 보고, 부족한(보유 적은) 순서로 정렬.
    _TARGET_DEPTH = 3
    _need_positions = sorted(
        (p for p in POSITIONS if _count_by_pos.get(p, 0) < _TARGET_DEPTH),
        key=lambda p: _count_by_pos.get(p, 0))
    if not _need_positions:
        return []

    cap = min(max_players,
              int(len(child_squad) * max_pct),
              max(0, len(child_squad) - min_child_remaining))
    if cap <= 0:
        return []

    _need_rank = {p: i for i, p in enumerate(_need_positions)}
    candidates = [r for r in child_squad if r["position"] in _need_rank]
    # 부족도가 큰 포지션 우선, 같은 포지션 안에서는 OVR 높은 선수 우선.
    candidates.sort(key=lambda r: (_need_rank[r["position"]], -r["ovr"]))
    picked = candidates[:cap]
    if not picked:
        return []

    picked_ids = [r["id"] for r in picked]
    placeholders = ",".join("?" * len(picked_ids))
    c.execute(f"UPDATE ai_players SET team_id=? WHERE id IN ({placeholders})",
              (parent_id, *picked_ids))
    return picked_ids


def _process_promotion_relegation(year, season_avg_rating=6.0):
    # [2026-08 계측 추가, 신민용 리포트: "43주 44주 45주 렉 언제 고칠거야"]
    # 지난번엔 이 함수 전체(0.48~0.54s)에 바깥쪽 타이머만 달았지 내부는
    # 아직 못 쪼갰다 — 원인 확정 전이므로 로직은 그대로 두고 구간별 시간만
    # 촘촘히 찍는다.
    import time as _time_pr
    import constants
    _pr_t0 = _time_pr.perf_counter()

    conn = get_conn()
    c = conn.cursor()

    p_row = conn.execute("SELECT current_team_id, current_league_id FROM my_player WHERE id=1").fetchone()
    my_team_id   = p_row["current_team_id"]   if p_row else 0
    my_league_id = p_row["current_league_id"] if p_row else 0

    # 현재 시즌 번호
    ss_row = conn.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    season = ss_row["current_season"] if ss_row else 1

    # ── 우승/승격 귀속 팀 판정 ──────────────────────
    from constants import SEASON_PHASES
    LEAGUE_END_WEEK = SEASON_PHASES["second_half"][1]   # 클럽 시즌 종료 주차(신규 캘린더: 43)

    def _team_at_week35():
        try:
            rows = conn.execute(
                """SELECT team_id, start_week, end_week, end_year FROM career_entries
                   WHERE team_id IS NOT NULL AND team_id<>0
                     AND start_year<=? AND (end_year=0 OR end_year>=?)
                   ORDER BY start_week""",
                (year, year)).fetchall()
            for r in rows:
                sw = r["start_week"] or 0
                if (r["end_year"] or 0) == 0 or (r["end_year"] or 0) > year:
                    ew = 52
                else:
                    ew = r["end_week"] or 52
                if sw <= LEAGUE_END_WEEK <= ew:
                    return r["team_id"]
        except Exception:
            pass
        return my_team_id

    champ_team_id = _team_at_week35()

    # (참고용) 그 시즌 5경기 이상 뛴 팀 집합
    my_season_teams = set()
    if my_team_id:
        my_season_teams.add(my_team_id)
    try:
        for r in conn.execute(
            """SELECT team_id, matches FROM career_entries
               WHERE start_year<=? AND (end_year=0 OR end_year>=?)""",
            (year, year)).fetchall():
            if r["team_id"] and (r["matches"] or 0) >= 5:
                my_season_teams.add(r["team_id"])
    except Exception:
        pass

    # [최적화 — 근본 원인] 예전엔 이 캐시를 리그마다 SELECT 2회
    # (팀 목록 + match_results)로 채워서, 전 세계 675개 리그 기준 쿼리
    # 1,350회가 나갔다(리그당 쿼리 자체는 인덱스 덕에 빠르지만, 커서/파싱
    # 오버헤드가 675번 누적되면서 이 함수 하나가 시즌 전환 지연의 절반
    # 이상(실측 약 6초/10초)을 차지했다). 이제 "팀 전체 1회 SELECT" +
    # "이번 시즌 match_results 전체 1회 SELECT"만 하고, 리그별 집계는
    # 파이썬 메모리 안에서 한 번의 루프로 끝낸다 — 쿼리 675x2회 → 2회.
    # [2026-08 시도했다가 되돌림, 신민용 리포트: "43주 렉 심한데?"] 이
    # 집계를 SQL GROUP BY로 옮겨봤는데, game.db로 직접 벤치마크해보니
    # (UNION ALL 34만행 → TEMP B-TREE로 GROUP BY 정렬) 오히려 파이썬 루프
    # (0.218s)보다 SQL 버전(0.297s)이 더 느렸다 — 이 케이스는 그룹 수
    # (~9천개)가 입력 행(17만+)보다 훨씬 적어서 SQLite가 해시가 아니라
    # 정렬 기반 GROUP BY를 택했고, 그 정렬 비용이 파이썬 dict 집계보다
    # 컸다. 결과가 같더라도 항상 SQL이 빠른 건 아니라는 사례 — 원래
    # 방식으로 되돌린다.
    def _calc_all_standings_cached(team_rows, league_ids, season):
        """모든 리그의 standings를 딱 2번의 SELECT로 한꺼번에 계산.
        반환값·정렬 기준은 기존 _calc_standings_cached(리그별 개별 계산)와
        동일하다 — 순수 성능 최적화이며 판정 로직은 바뀌지 않는다.
        [2026-07 추가 최적화] teams 전체 스캔을 이 함수 안에서 직접 하지
        않고, 호출부가 이미 선조회해둔 team_rows(팀명·리그명 캐시와 공유)를
        재사용한다 — 같은 함수 안에서 teams를 3번 따로 SELECT하던 것을
        1번으로 줄인다. 결과는 완전히 동일.
        [2026-08 추가 최적화, 신민용 리포트: "43주 렉, 방식을 다르게 하면
        더 줄일 수 없어?"] SQL GROUP BY로 옮기는 시도는 game.db 실측상
        오히려 느려서 되돌렸었다(TEMP B-TREE 정렬 비용) — 대신 이 시즌
        경기 17만+ 건을 fetchall()할 때 sqlite3.Row로 감싸지 않고 순수
        튜플로 받아 위치 인덱스로 접근하도록 바꿨다(_age_and_progress가
        5.9만 명 ai_players를 읽을 때 쓰는 것과 동일한 패턴). game.db로
        직접 벤치마크해 Row객체 접근(0.256s) 대비 튜플 접근(0.219s)이
        약 15% 빠름을 확인했다 — 계산 로직·결과는 완전히 동일, row를
        "어떻게 꺼내오느냐"만 바뀐다."""
        by_league: dict = {lid: {} for lid in league_ids}
        for r in team_rows:
            lid = r["league_id"]
            if lid in by_league:
                by_league[lid][r["id"]] = {"id": r["id"], "name": r["name"],
                                            "classification_status": r["classification_status"],
                                            "pts": 0, "gd": 0, "gf": 0, "gp": 0}

        # [버그수정] conn.cursor()는 _PooledCursor(재시도 방어 래퍼, __slots__로
        # _real만 허용)를 반환해서 row_factory 속성을 못 붙인다 — _age_and_progress가
        # 쓰는 것과 동일하게 c.connection으로 진짜 sqlite3.Connection을 얻은 뒤
        # 거기서 순정 커서를 만들어야 row_factory=None이 먹는다.
        _raw_cursor = c.connection.cursor()
        _raw_cursor.row_factory = None  # 위치 접근만 쓰므로 Row 래핑 생략
        for row in _raw_cursor.execute(
                """SELECT league_id, home_team_id, away_team_id, home_score, away_score
                   FROM match_results WHERE season=? AND home_score>=0""",
                (season,)).fetchall():
            # row = (league_id, home_team_id, away_team_id, home_score, away_score)
            teams_in = by_league.get(row[0])
            if not teams_in:
                continue
            hid, aid, hs, as_ = row[1], row[2], row[3], row[4]
            for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
                t = teams_in.get(tid)
                if t is None:
                    continue
                t["gp"] += 1
                t["gf"] += gf
                t["gd"] += gf - ga
                if gf > ga:    t["pts"] += 3
                elif gf == ga: t["pts"] += 1

        out = {}
        for lid, teams_in in by_league.items():
            rows_out = [t for t in teams_in.values() if t["gp"] > 0]
            out[lid] = sorted(rows_out, key=lambda x: (-x["pts"], -x["gd"], -x["gf"]))
        return out

    # [최적화] 전체 리그 맵을 1회 SELECT로 미리 빌드 (기존: cids×tier 개별 SELECT 275회)
    all_leagues_rows = c.execute(
        "SELECT id, country_id, tier FROM leagues ORDER BY id").fetchall()
    # {(country_id, tier): league_id}
    _league_map: dict = {(r["country_id"], r["tier"]): r["id"] for r in all_leagues_rows}
    # {country_id: {tier: league_id}}
    # [2026-08 버그수정, 재현성 문제 추적 중 발견] list(set(...))는 set의
    # 내부 해시 테이블 레이아웃에 좌우돼 실행마다 순서가 달라질 위험이
    # 있다(이 순서가 country 처리 순서를 정하고, 그 순서가 club_strength
    # 강등 방어 로직 등에서 random 소비 순서를 바꿔 재현성을 깬다) —
    # sorted()로 country_id 값 기준 고정 순서를 강제한다.
    cids = sorted({r["country_id"] for r in all_leagues_rows
                   if r["tier"] == 1})

    # [버그수정 2026-07] 승강제 경계 순회가 "for tier in [1,2,3,4]"로 고정돼
    # 있어서 최대 5부(4↔5 경계)까지만 처리됐다 — 지금은 S급 5부제라 우연히
    # 안 걸렸지만, 나중에 6부 이상으로 늘리면 5↔6 경계가 조용히 누락돼
    # 그 밑 부수는 영원히 승강이 안 되는 버그가 생긴다. 나라별 실제 최대
    # 부수를 여기서 한 번에 구해서, 몇 부가 됐든 모든 경계를 자동으로 돈다.
    _country_max_tier_map: dict = {}
    for (cid_, tier_) in _league_map:
        if tier_ > _country_max_tier_map.get(cid_, 0):
            _country_max_tier_map[cid_] = tier_

    # 모든 관련 리그 ID 수집 (맵 조회, DB 추가 접근 없음)
    all_league_ids = {lid for lid in _league_map.values()}

    # [2026-07 성능개선] teams(+leagues JOIN)를 이 아래에서 필요한 3곳
    # (standings 스켈레톤 / 팀명·리그명 캐시 / team→league 캐시)이 각자
    # 따로 SELECT 하던 것을 1회 선조회로 통합 — teams 테이블 전체스캔이
    # 3회 → 1회로 줄어든다(결과는 완전히 동일, 그냥 어디서 읽어오느냐만 바뀜).
    _pr_t1 = _time_pr.perf_counter()

    _all_team_rows = c.execute(
        "SELECT t.id, t.name, t.league_id, t.country_id, t.classification_status, "
        "t.current_tier, t.parent_team_id, l.name as lname "
        "FROM teams t JOIN leagues l ON t.league_id=l.id ORDER BY t.id").fetchall()
    _pr_t2 = _time_pr.perf_counter()

    # standings 캐시: {league_id: [sorted rows]}
    _standings_cache = _calc_all_standings_cached(_all_team_rows, all_league_ids, season)
    _pr_t3 = _time_pr.perf_counter()

    # [2026-08 신설, 신민용 확정: 동적 팀 강도] 방금 계산된 이번 시즌
    # standings(= 승강으로 league_id가 바뀌기 전, "실제로 그 시즌을 치른"
    # 리그 소속 기준)를 그대로 재사용해 teams.club_strength를 갱신한다.
    # 추가 SELECT 없이 이미 메모리에 있는 _standings_cache만 쓰므로
    # 43주 시즌 전환 성능에 영향이 없다. rescale_teams_to_target_ovr_batch
    # (아래, league_id 이동 이후 호출)와는 독립적 — 순서 상관없이 안전.
    try:
        from constants import (club_strength_delta_for_rank, CLUB_STRENGTH_DECAY,
                                CLUB_STRENGTH_MIN, CLUB_STRENGTH_MAX, MOMENTUM_SCHEDULES)
        _cs_rows = c.execute(
            "SELECT id, club_strength, momentum_type, momentum_seasons_left FROM teams").fetchall()
        _cs_cur = {r["id"]: (r["club_strength"] or 0.0) for r in _cs_rows}
        _mom_cur = {r["id"]: (r["momentum_type"] or "", r["momentum_seasons_left"] or 0)
                    for r in _cs_rows}
        _cs_updates = []
        _mom_decay_updates = []
        # [2026-08 신설] 이번 시즌 성적까지 반영해 방금 계산한 club_strength를
        # team_id -> 값으로도 캐싱 — 아래 강등 최저티어 보장 로직이 재조회
        # 없이 "지금 이 팀이 얼마나 강한가"를 바로 쓸 수 있게 한다.
        _team_strength_cache: dict = {}
        for _lid, _ranked in _standings_cache.items():
            _n = len(_ranked)
            for _idx, _row in enumerate(_ranked, start=1):
                _tid = _row["id"]
                _delta = club_strength_delta_for_rank(_idx, _n)
                _old = _cs_cur.get(_tid, 0.0)
                _mtype, _mleft = _mom_cur.get(_tid, ("", 0))
                # [2026-08 신설, club_momentum] 강등 직후/국제대회 우승 직후처럼
                # momentum이 남아있는 동안(_mleft>0)은 정상 감쇠(0.85) 대신
                # 그 이벤트 스케줄(MOMENTUM_SCHEDULES)의 완화된 감쇠계수 +
                # 임시 보너스를 적용하고, 카운트다운을 1 줄인다. 다 쓰면(0이
                # 되면) 다음 시즌부터는 원래 방식으로 자동 복귀.
                _sched = MOMENTUM_SCHEDULES.get(_mtype) if _mleft > 0 else None
                if _sched:
                    _decay, _bonus = _sched.get(_mleft, (CLUB_STRENGTH_DECAY, 0.0))
                    _new = _old * _decay + _delta + _bonus
                    _mom_decay_updates.append((_mtype, _mleft - 1, _tid))
                else:
                    _new = _old * CLUB_STRENGTH_DECAY + _delta
                _new = max(CLUB_STRENGTH_MIN, min(CLUB_STRENGTH_MAX, _new))
                _cs_updates.append((_new, _tid))
                _team_strength_cache[_tid] = _new
        if _cs_updates:
            c.executemany("UPDATE teams SET club_strength=? WHERE id=?", _cs_updates)
            _invalidate_team_ovr_cache()
        if _mom_decay_updates:
            c.executemany("UPDATE teams SET momentum_type=?, momentum_seasons_left=? WHERE id=?",
                          [(t, n, tid) for t, n, tid in _mom_decay_updates])
    except Exception as _e:
        add_log(f"[club_strength 갱신 오류] {_e}", "normal", year, 52)
        _team_strength_cache = {}



    # [2026-07 신설, DEBUG_RELEGATION_TRACKING 전용] 지난 사이클에 등록해둔
    # 강등팀들의 "시즌 종료" 체크포인트 — 이번에 막 계산된 standings로
    # 순위까지 같이 남기고, 4시점 기록이 끝났으니 추적에서 제거한다.
    if DEBUG_RELEGATION_TRACKING and _RELEGATION_DEBUG_TRACK:
        _team_league_map = {r["id"]: r["league_id"] for r in _all_team_rows}
        for _tid in list(_RELEGATION_DEBUG_TRACK.keys()):
            _lid = _team_league_map.get(_tid)
            _rank_str = ""
            if _lid is not None:
                _rows_here = _standings_cache.get(_lid, [])
                _rk = next((i for i, rr in enumerate(_rows_here) if rr["id"] == _tid), None)
                if _rk is not None:
                    _rank_str = f"{_rk+1}위/{len(_rows_here)}팀"
            _relegation_debug_snapshot(_tid, "시즌 종료", extra=_rank_str)
            del _RELEGATION_DEBUG_TRACK[_tid]


    # [최적화] 팀 이름·리그명 전체 선조회 → 루프 내 ci.execute JOIN 제거
    #   기존: 승강 판정마다 ci = conn.cursor() + JOIN SELECT 2~3회
    #   변경: 1회 SELECT로 {team_id: (name, lname)} dict 빌드 후 dict 조회
    _team_info_cache: dict = {
        r["id"]: (r["name"], r["lname"]) for r in _all_team_rows
    }

    # [최적화] get_league_avg_ovr / get_league_strong_ovr 를 승강 경계마다
    # 개별 SELECT(AVG/JOIN/GROUP BY)로 부르던 것을 제거.
    # ai_players + teams를 각 1회만 읽어 팀별 ovr 리스트로 캐싱해두고,
    # 아래 헬퍼가 그 캐시로 원본과 동일한 계산(리그 전체 평균 / 팀별 평균의
    # 상위 분위)을 파이썬 메모리에서 수행한다. 판정 로직·반환값은 동일.
    _team_league_of: dict = {
        r["id"]: r["league_id"] for r in _all_team_rows
    }
    _league_teams: dict = {}
    for tid, lid in _team_league_of.items():
        _league_teams.setdefault(lid, []).append(tid)
    _player_ovrs_by_team: dict = {}
    for r in c.execute("SELECT team_id, ovr FROM ai_players").fetchall():
        _player_ovrs_by_team.setdefault(r["team_id"], []).append(r["ovr"])
    _pr_t4 = _time_pr.perf_counter()

    def _cached_league_avg_ovr(league_id, exclude_team_id=None):
        vals = []
        for tid in _league_teams.get(league_id, []):
            if tid == exclude_team_id:
                continue
            vals.extend(_player_ovrs_by_team.get(tid, []))
        return (sum(vals) / len(vals)) if vals else None

    def _cached_league_strong_ovr(league_id, pct=0.75, exclude_team_id=None):
        team_avgs = []
        for tid in _league_teams.get(league_id, []):
            if tid == exclude_team_id:
                continue
            ovrs = _player_ovrs_by_team.get(tid, [])
            if ovrs:
                team_avgs.append(sum(ovrs) / len(ovrs))
        if not team_avgs:
            return None
        vals = sorted(team_avgs)
        idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * pct))))
        return vals[idx]

    def _move_team_cache(team_id, new_league_id):
        """DB의 teams.league_id UPDATE와 짝을 맞춰 캐시도 옮긴다.
        이래야 뒤이은 티어 경계 계산(_cached_league_*)이, 매번 DB를 다시
        조회하던 원본과 동일한 결과(이동한 팀을 반영한 값)를 낸다."""
        old_lid = _team_league_of.get(team_id)
        if old_lid is not None and team_id in _league_teams.get(old_lid, []):
            _league_teams[old_lid].remove(team_id)
        _league_teams.setdefault(new_league_id, []).append(team_id)
        _team_league_of[team_id] = new_league_id


    pending_logs  = []
    my_new_league = None

    # 1부 리그 우승 기록
    for cid in cids:
        top_lid = _league_map.get((cid, 1))
        if not top_lid:
            continue
        top1_rows = _standings_cache.get(top_lid, [])
        if top1_rows and top1_rows[0]["pts"] > 0 and top1_rows[0]["id"] == champ_team_id:
            champ_tid = top1_rows[0]["id"]
            _ti = _team_info_cache.get(champ_tid)
            winner_info = {"name": _ti[0], "lname": _ti[1]} if _ti else None
            # [최적화] ci 커서 제거 - _team_info_cache 사용
            if winner_info:
                existing_champ = c.execute(
                    "SELECT id FROM trophy_log WHERE year=? AND team_name=? AND tier=1",
                    (year, winner_info["name"])).fetchone()
                if not existing_champ:
                    c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                              (year, winner_info["name"], winner_info["lname"], 1,
                               f"{winner_info['lname']} 우승 (1부 리그 챔피언)"))
                    pending_logs.append((f"🏆 {year}년  {winner_info['name']}  1부 리그 우승!", "event"))

    def _select_promotion_eligible(rows, count, moved_teams, restrict):
        """rows: 순위 정렬된 standings(1위부터). count: 필요한 인원.
        restrict=False면 기존과 완전히 동일(순서대로 count명 자르기).
        restrict=True면 이미 이동 처리된 팀은 건너뛰고, classification_status
        가 'NORMAL'이 아닌 팀도 건너뛰면서 전체 테이블을 훑어 내려가
        승격 자격이 있는 팀을 count명 채운다(부족하면 그만큼만 반환 —
        억지로 REVIEW/AFFILIATE를 채워넣지 않는다)."""
        out = []
        for r in rows:
            if r["id"] in moved_teams:
                continue
            if restrict and r.get("classification_status", "NORMAL") != "NORMAL":
                continue
            out.append(r)
            if len(out) >= count:
                break
        return out

    moved_teams: set = set()

    # [2026-08 신설] 산하팀은 항상 모팀보다 낮은 tier에 있어야 한다는
    # 불변식을 이번 시즌 승강 처리 끝에 검증하기 위한 상태.
    # pending_tier: 이번 시즌 각 팀의 "확정된" tier — current_tier를
    # 중간에 그대로 참조하면 이 함수 실행 도중(경계 처리 순서상 아직
    # 반영 전인) stale 값을 보게 되므로, 루프 안에서 팀이 이동될 때마다
    # 여기 직접 갱신한다.
    _pending_tier: dict = {r["id"]: r["current_tier"] for r in _all_team_rows}
    _original_tier: dict = dict(_pending_tier)  # 승격 취소 시 되돌릴 원래 tier
    _original_league: dict = {r["id"]: r["league_id"] for r in _all_team_rows}
    _promo_log_entry_for_team: dict = {}  # team_id -> _promotion_log_inserts에 넣은 그 튜플(취소 시 제거용)
    _parent_of: dict = {r["id"]: r["parent_team_id"] for r in _all_team_rows if r["parent_team_id"]}
    _classification_of: dict = {r["id"]: r["classification_status"] for r in _all_team_rows}
    _country_of: dict = {r["id"]: r["country_id"] for r in _all_team_rows}
    # [2026-08 신설] 강등 시 prestige_level(country, name) 판정용 country_id
    # -> 국가명 캐시. 위 country_id 캐시와 별개 쿼리 1회만 추가(팀 수와
    # 무관하게 country 테이블 1회 스캔이라 저렴).
    _country_name_by_id: dict = {r["id"]: r["name"] for r in c.execute(
        "SELECT id, name FROM countries").fetchall()}
    _rescale_jobs: list = []
    _momentum_reset_updates: list = []  # [2026-08 신설] (momentum_type, seasons_left, team_id) — 이벤트 발생 시 리셋
    # [2026-07 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"] 승격/강등
    # 팀마다 UPDATE teams + INSERT promotion_log를 개별 실행했는데(실측
    # 209개국 × 평균 2개 경계 × 경계당 승격/강등 팀들 = 수백~수천 건),
    # 이 루프 안에서 쓰는 값(_cached_league_avg_ovr 등)은 전부 인메모리
    # 캐시(_move_team_cache)만 참조하므로 DB 반영 시점을 뒤로 미뤄도
    # 계산 결과에 영향이 없다. 여기 모아뒀다가 루프가 끝난 뒤 딱 한 번씩
    # executemany로 처리한다.
    _team_move_updates: list = []      # (league_id, tier, team_id)
    _promotion_log_inserts: list = []  # (year, team_name, from_tier, to_tier, league_name)
    _po_pending_inserts: list = []     # (year, upper_lid, lower_lid, rule_id, side, offset, team_id, team_name)

    for cid in cids:
        _max_tier_here = _country_max_tier_map.get(cid, 1)
        for tier in range(1, _max_tier_here):   # 1↔2, 2↔3, ... (최대부-1)↔최대부 — 몇 부든 자동 커버
            ntier = tier + 1
            # [최적화] _league_map 조회 (기존: 개별 SELECT 440회 → 0회)
            upper_lid = _league_map.get((cid, tier))
            lower_lid = _league_map.get((cid, ntier))
            if not upper_lid or not lower_lid:
                continue

            # [최적화] 캐시에서 바로 조회 (match_results 재스캔 없음)
            upper_rows = _standings_cache.get(upper_lid, [])
            lower_rows = _standings_cache.get(lower_lid, [])
            if not upper_rows or not lower_rows:
                continue

            # [2026-07 재설계, 신민용 최종안: "상위 리그는 항상 1팀만
            # 위태, 아래 리그는 리그 크기에 따라 2~4팀이 마지막 승격
            # 티켓을 놓고 경쟁"] 자동 이동 인원(auto_count)은 반드시 위
            # 리그 크기 하나로만 정해서 양쪽에 대칭 적용한다(1부/2부 팀
            # 수가 다른 나라가 68%나 돼서, 각자 자기 크기로 따로 정하면
            # 리그 전체 팀 수가 서서히 무너진다 — _get_promotion_policy
            # docstring 참고). PO 브래킷 크기(2 또는 4)는 반대로 아래
            # 리그 자기 크기로 독립 결정해도 안전하다 — 브래킷에 몇 팀이
            # 들어오든 최종적으로 딱 1팀만 실제로 이동하기 때문.
            import promotion_playoff as _pp
            _policy = _get_promotion_policy(len(upper_rows))
            auto_count = _policy["auto"]
            po_exists = _policy["po"] == 1
            # 안전장치: 아주 작은 리그에서 자동 이동 인원이 리그 절반을
            # 넘어가지 않도록 상한(실제 데이터 범위-6~30팀-에선 발동 안 함).
            auto_count = max(0, min(auto_count, len(upper_rows) // 2, len(lower_rows)))

            auto_upper_cands = [r for r in upper_rows[-auto_count:] if r["id"] not in moved_teams] if auto_count else []
            auto_lower_cands = (
                _select_promotion_eligible(
                    lower_rows, auto_count, moved_teams,
                    constants.AFFILIATE_PROMOTION_RESTRICTION)
                if auto_count else []
            )
            # 승격 인원과 강등 인원이 달라지면 리그별 총 팀 수가 어긋나므로
            # (moved_teams 필터링으로 한쪽이 줄었을 수 있음) 더 작은 쪽에 맞춘다.
            n_actual = min(len(auto_upper_cands), len(auto_lower_cands))
            bottom_upper_list = auto_upper_cands[:n_actual]
            top_lower_list    = auto_lower_cands[:n_actual]

            # [2026-08 제거, 신민용 확정: "강등 자체를 막으면 안 된다"]
            # 예전엔 여기서 CLUB_STRENGTH_RELEGATION_FLOOR를 참조해,
            # 이미 순위표(승점→득실차)로 확정된 bottom_upper_list를
            # 사후에 다시 바꿔치기했다(강한 팀은 확률적으로 강등 취소,
            # 대신 순위표상 더 위였던 팀을 억지로 끌어내림) — 그 결과
            # "19·20위는 안 내려가고 17·18위가 대신 강등"처럼 순위
            # 결과 자체가 뒤집히는 문제가 있었다.
            # 강팀이 실제로 잘 안 떨어지게 하고 싶다면 이미 있는
            # CLUB_STRENGTH_MATCH_WEIGHT(경기 결과 자체에 반영 — 순위가
            # 확정되기 전에 작동)로 처리하는 게 맞는 경로다. 여기서
            # 순위 확정 후 결과를 다시 조작하는 건 이중 보정이자 결과
            # 왜곡이므로 완전히 제거한다. bottom_upper_list는 이제
            # 순수하게 그 시즌 순위표 그대로 유지된다.

            # [2026-08 버그수정, 신민용 리포트: "프리미어리그 21팀으로 늘어남,
            # 승격팀·강등팀 겹침"] club_strength 보호로 "대신 내려갈 팀"이
            # 뽑히면, 그 팀이 곧바로 아래(_upper_po_zone) PO 후보 계산에서
            # moved_teams로 걸러져야 하는데 — 지금까지는 이 등록이 한참 뒤
            # (실제 이동 처리 루프)에서야 일어나서, 같은 팀이 "강등 확정"과
            # "1부 잔류 PO 참가자"에 동시에 뽑히는 이중 배정이 가능했다.
            # PO가 끝난 뒤 "2부에 있는 팀이 이겼다"로 오인해 승격으로 다시
            # 기록되면서 팀 수가 +1 되는 게 실제 증상(21팀 버그)이었다.
            # bottom_upper_list/top_lower_list가 확정되는 이 시점에 바로
            # moved_teams를 갱신해서, 아래 PO 후보 계산이 정확히 걸러내게
            # 한다(뒤쪽 실제 이동 처리 루프의 moved_teams.add()는 그대로 둬도
            # 이미 들어있는 id를 다시 추가하는 것뿐이라 안전하다).
            moved_teams.update(r["id"] for r in bottom_upper_list)
            moved_teams.update(r["id"] for r in top_lower_list)

            if n_actual == 0 and not po_exists:
                continue

            # [PO 후보 추출] 위 리그: 자동존 바로 위 1팀만(있으면). 아래
            # 리그: 자동존 바로 아래 bracket_size팀(그 리그 자체 크기로
            # 결정) — 어느 한쪽이라도 이미 이동 처리된 팀이라 후보가
            # 안 채워지면 이번 해는 이 경계의 PO를 건너뛴다(다음 해에
            # 다시 시도됨, 실제 데이터 범위에선 거의 발동 안 함).
            _rule_id = None
            if po_exists:
                _bracket_size = _get_po_bracket_size(len(lower_rows))
                _upper_po_zone = upper_rows[max(0, len(upper_rows) - auto_count - 1):
                                             len(upper_rows) - auto_count]
                _po_pending_upper = [r for r in _upper_po_zone if r["id"] not in moved_teams]
                # [산하팀 제한] 자동승격으로 이미 뽑힌 팀은 제외한 나머지
                # 순위표에서 PO 후보를 뽑는다 — 제한 ON이면 여기서도
                # classification_status != 'NORMAL'인 팀은 건너뛴다
                # (자동승격 제한을 걸어놓고 PO로 우회시키면 의미가 없음).
                _lower_taken_ids = {r["id"] for r in auto_lower_cands}
                _lower_po_pool = [r for r in lower_rows
                                  if r["id"] not in moved_teams and r["id"] not in _lower_taken_ids]
                if constants.AFFILIATE_PROMOTION_RESTRICTION:
                    _lower_po_pool = [r for r in _lower_po_pool
                                       if r.get("classification_status", "NORMAL") == "NORMAL"]
                _po_pending_lower = _lower_po_pool[:_bracket_size]
                if len(_po_pending_upper) != 1 or len(_po_pending_lower) != _bracket_size:
                    po_exists = False   # 후보가 부족 — 이번 해엔 PO 없이 자동 이동만
                else:
                    _rule_id = _pp.get_rule_id_for_bracket(_bracket_size)

            if not po_exists:
                _po_pending_upper, _po_pending_lower = [], []

            if po_exists:
                for _off, _r in enumerate(_po_pending_upper):
                    _ti = _team_info_cache.get(_r["id"])
                    _po_pending_inserts.append(
                        (year, upper_lid, lower_lid, _rule_id, "upper", _off, _r["id"],
                         _ti[0] if _ti else _r.get("name", "")))
                    moved_teams.add(_r["id"])
                    if _r["id"] == my_team_id:
                        pending_logs.append(
                            (f"⚖ {year}년  {(_ti[0] if _ti else '')}  {tier}부 잔류 갈림길 — "
                             f"승강 플레이오프 진출 (44주)", "event"))
                # top_lower_list와 달리 _po_pending_lower는 lower_rows에서
                # 이미 순위 오름차순(강→약) 그대로 슬라이스한 것이라, offset도
                # 그 순서 그대로 매기면 resolve_standing_rank의 lower
                # offset=0(자동존에 가장 가까운=가장 강한 후보)과 맞는다 —
                # 예전 dual_slot처럼 reversed()가 필요 없다(그때는 양쪽을
                # 같은 폭으로 자르고 뒤에서 잘라내는 방식이라 역순이 필요
                #했는데, 지금은 처음부터 PO존만 따로 슬라이스해서 안 그럼).
                for _off, _r in enumerate(_po_pending_lower):
                    _ti = _team_info_cache.get(_r["id"])
                    _po_pending_inserts.append(
                        (year, upper_lid, lower_lid, _rule_id, "lower", _off, _r["id"],
                         _ti[0] if _ti else _r.get("name", "")))
                    moved_teams.add(_r["id"])
                    if _r["id"] == my_team_id:
                        pending_logs.append(
                            (f"⚖ {year}년  {(_ti[0] if _ti else '')}  {ntier}부 승격 갈림길 — "
                             f"승강 플레이오프 진출 (44주)", "event"))

            # [버그수정] 리스케일 목표치는 팀 이동 *전* 측정하되
            # 각 팀 본인을 제외한 순수 기존 팀 평균으로 산정.
            # - 강등팀들 목표: 하위 리그 기존 팀 상위 75% (강등팀 제외 — 아직 안 내려옴)
            # tier 1→4 순서 루프이므로, 이전 tier에서 이동된 팀이
            # 현재 리그 평균에 포함될 수 있어 moved_teams 제외 처리.
            # 목표치는 승격/강등 인원 전체가 "그 리그에 새로 들어간다"는
            # 공통 기준이므로 인원수와 무관하게 한 번만 계산해 재사용한다.
            #
            # [2026-08 버그수정, 신민용 리포트: "5~6부에서 승격하면 바로
            # 상위팀 수준 OVR을 받아서, 한번 승격하면 그대로 1부까지
            # 쭉 올라온다"] 예전엔 승격팀 목표를 상위 리그 "평균"(_cached_
            # league_avg_ovr)으로 맞췄다 — 그런데 "평균"은 사실상 "중위권"이
            # 아니라 그 자체로 이미 안정적인 스쿼드라, 승격 직후부터 다시
            # 상위권을 노릴 수 있는 전력이 되어버렸다. 실측(16시즌 헤드리스)
            # 결과 실제로 7부→6부→5부→4부→3부→2부→1부를 6시즌 연속으로
            # 찍는 팀이 여러 개 나왔다 — "승격 직후엔 그 리그에서 하위권으로
            # 시작해 자리를 잡아야 한다"는 현실감이 완전히 빠져 있었던 것.
            # 목표를 상위 리그의 "하위 25%(약체) 팀 수준"으로 낮춘다 — 여전히
            # 승격 자체로 어느 정도 전력 보강은 되지만(원래 하위 리그
            # 평균보다는 높음), 곧바로 그 리그 중상위권을 넘볼 정도는 아니게
            # 되어 "승격팀은 보통 신입답게 고전하다가 몇 시즌에 걸쳐
            # 자리를 잡는다"는 흐름을 만든다.
            # [2026-08 최종 조정] 12%ile까지 낮춰서도 재검증했지만, "5시즌
            # 연속 승격(7부→1부)" 사례 자체는 크게 안 줄었다 — 실측해보니
            # 전체 승격 경험 팀(16시즌간 6,914개) 대비 이런 극단적 연속
            # 승격 사례는 애초에 15개 안팎(0.2% 수준)으로 드문 편이라,
            # 퍼센타일을 더 극단적으로 낮춰도(즉 승격팀을 거의 최하위권으로
            # 만들어도) 이 희귀 케이스 자체는 매치 시뮬레이션 변동성/선수
            # 성장 쪽 영향이 더 커서 퍼센타일 조정만으론 다 못 잡는다.
            # 대신 "평균(50%ile)"이던 원래 목표 자체는 승격 직후부터 바로
            # 그 리그 중상위권을 노릴 수 있는 명백한 과다 보상이었으므로,
            # 이 부분은 확실히 고쳐야 한다 — 너무 극단적으로 낮추면(예:
            # 12%ile) 반대로 승격팀이 지나치게 약해져 곧바로 재강등되는
            # 정반대 문제가 생길 수 있어, 하위 20%ile(리그 하위권 시작 —
            # 신입답게 고전은 하지만 완전히 최하위는 아닌 수준)로 확정한다.
            _upper_avg = _cached_league_strong_ovr(upper_lid, 0.20)
            _lower_strong = _cached_league_strong_ovr(lower_lid, 0.75)


            for top_lower in top_lower_list:
                _tl = _team_info_cache.get(top_lower["id"])
                if not _tl:
                    continue
                tl_info = {"name": _tl[0], "lname": _tl[1]}

                # 승격: top_lower → upper
                _team_move_updates.append((upper_lid, tier, top_lower["id"]))
                _pending_tier[top_lower["id"]] = tier
                _move_team_cache(top_lower["id"], upper_lid)
                if _upper_avg is not None:
                    _rescale_jobs.append((top_lower["id"], _upper_avg))
                _promotion_log_inserts.append((year, tl_info["name"], ntier, tier, tl_info["lname"],
                                                lower_lid, upper_lid, top_lower["id"]))
                _promo_log_entry_for_team[top_lower["id"]] = _promotion_log_inserts[-1]
                tl_is_mine = (top_lower["id"] in my_season_teams)
                if tl_is_mine or my_league_id in (upper_lid, lower_lid):
                    pending_logs.append((f"🔼 {year}년  {tl_info['name']}  {ntier}부→{tier}부  (승격)", "event"))
                if top_lower["id"] == champ_team_id:
                    exist = c.execute(
                        "SELECT id FROM trophy_log WHERE year=? AND team_name=? AND tier=?",
                        (year, tl_info["name"], ntier)).fetchone()
                    if not exist:
                        c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                                  (year, tl_info["name"], tl_info["lname"], ntier,
                                   f"{tl_info['lname']} 우승 ({ntier}부 1위 → {tier}부 승격)"))
                    if top_lower["id"] == my_team_id:
                        my_new_league = upper_lid
                moved_teams.add(top_lower["id"])

            for bottom_upper in bottom_upper_list:
                _bu = _team_info_cache.get(bottom_upper["id"])
                if not _bu:
                    continue
                bu_info = {"name": _bu[0], "lname": _bu[1]}

                # 강등: bottom_upper → lower
                _team_move_updates.append((lower_lid, ntier, bottom_upper["id"]))
                _pending_tier[bottom_upper["id"]] = ntier
                _move_team_cache(bottom_upper["id"], lower_lid)
                # [2026-08 신설, club_momentum] 강등된 팀은 'relegation_recovery'
                # momentum으로 리셋 — 다음 시즌부터 몇 시즌간 club_strength
                # 감쇠 완화 + 임시 보너스를 받는다(강등 스노우볼 방지).
                # [2026-08 재조정, 신민용 리포트: "명문팀들이 다 강등당하고
                # prestige_clubs.py 0.5% 강등권 목표가 계속 터진다"] 일반
                # 강등 보너스는 전 팀 공통이지만, prestige_clubs.py에 등재된
                # 팀(레벨 1~3)은 등급별 전용 스케줄(relegation_recovery_p3/
                # _p2/_p1 — MOMENTUM_SCHEDULES 참고, 등급이 높을수록 강하고
                # 길게)을 대신 적용해 "몇 시즌 내 복귀" 요구에 맞춘다.
                from constants import MOMENTUM_START_BY_TYPE
                from data.prestige_clubs import prestige_level as _pget
                _bu_country = _country_name_by_id.get(_country_of.get(bottom_upper["id"]), "")
                _bu_plevel = _pget(_bu_country, bu_info["name"])
                _mtype = {3: "relegation_recovery_p3", 2: "relegation_recovery_p2",
                          1: "relegation_recovery_p1"}.get(_bu_plevel, "relegation_recovery")
                _momentum_reset_updates.append(
                    (_mtype, MOMENTUM_START_BY_TYPE[_mtype], bottom_upper["id"]))
                if _lower_strong is not None:
                    _rescale_jobs.append((bottom_upper["id"], _lower_strong))
                    if DEBUG_RELEGATION_TRACKING:
                        _RELEGATION_DEBUG_TRACK[bottom_upper["id"]] = {
                            "name": bu_info["name"], "season": season, "checkpoints": {}}
                _promotion_log_inserts.append((year, bu_info["name"], tier, ntier, bu_info["lname"],
                                                upper_lid, lower_lid, bottom_upper["id"]))
                if bottom_upper["id"] == my_team_id or my_league_id in (upper_lid, lower_lid):
                    pending_logs.append((f"🔽 {year}년  {bu_info['name']}  {tier}부→{ntier}부  (강등)", "event"))
                if bottom_upper["id"] == my_team_id:
                    my_new_league = lower_lid
                moved_teams.add(bottom_upper["id"])

    # ═══════════════════════════════════════════
    # [2026-08 신설] 산하팀 < 모팀 tier 불변식 검증 + 보정
    # ═══════════════════════════════════════════
    # 위 루프에서 모든 경계의 승격/강등이 pending_tier에 확정된 뒤,
    # "산하팀은 항상 모팀보다 낮은 tier(숫자가 더 큼)여야 한다"는 규칙을
    # 검사한다. current_tier가 아니라 이번 시즌 확정 상태(pending_tier)로
    # 검사해야 아직 DB에 반영 안 된 stale 값을 안 보게 된다.
    #
    # 충돌 해소 정책 (기존 합의):
    #   ① 산하팀 본인이 이번 시즌 승격해서 충돌이 생겼다면 → 그 승격을
    #      취소하고, 원래 있던 리그의 다음 NORMAL 후보에게 슬롯을 넘긴다
    #      (대체 후보가 없으면 슬롯을 빈 채로 둔다 — 억지로 안 채움).
    #   ② 산하팀은 안 움직였는데 모팀이 강등되어 내려오며 충돌이 생겼다면
    #      → 산하팀을 한 티어 더 아래로 강제 이동시킨다(내려가는 건
    #      항상 안전하다는 원칙).
    # 연쇄 반응(취소/강제이동이 새 충돌을 만드는 경우) 대비해 반복하되,
    # 변경이 없으면 즉시 종료하고 비정상적으로 길어지면 안전 상한으로
    # 강제 종료한다.
    if constants.AFFILIATE_PROMOTION_RESTRICTION and _parent_of:
        import json as _json_audit

        def _audit(row):
            row.setdefault("season_year", year)
            _wb_audit_rows.append(row)

        _wb_audit_rows: list = []

        # 이번 시즌 시작 시점, parent_team_id가 있는 모든 팀을 예외 없이
        # 1건씩 기록한다 — "아무 기록 없이 사라지는 팀"을 방지.
        for _tid0, _pid0 in _parent_of.items():
            if _tid0 not in _pending_tier or _pid0 not in _pending_tier:
                _audit({
                    "team_id": _tid0, "parent_id": _pid0,
                    "classification_status": _classification_of.get(_tid0),
                    "skip_reason": "TEAM_OR_PARENT_NOT_IN_PENDING_TIER",
                    "entered_correction": False,
                })
                continue
            _is_violation = _pending_tier[_tid0] <= _pending_tier[_pid0]
            _audit({
                "team_id": _tid0, "parent_id": _pid0,
                "country_id": _country_of.get(_tid0),
                "classification_status": _classification_of.get(_tid0),
                "parent_tier_initial": _pending_tier[_pid0],
                "child_tier_initial": _pending_tier[_tid0],
                "max_country_tier": _country_max_tier_map.get(_country_of.get(_tid0)),
                "is_violation_initial": _is_violation,
                "skip_reason": None if _is_violation else "NOT_VIOLATION",
                "entered_correction": False,
            })

        _MAX_RESOLUTION_PASSES = 10
        # [2026-08 신설] 이번 시즌 콜업으로 이미 처리된 (자식,모팀) 쌍은
        # 다시 위반 목록에 넣지 않는다 — case②는 이제 tier를 바꾸지 않고
        # 선수 콜업으로만 해결하므로, 이 셋이 없으면 같은 패스 안에서
        # 매번 다시 "위반"으로 잡혀 콜업이 무한 반복된다.
        _callup_resolved: set = set()
        for _pass_i in range(_MAX_RESOLUTION_PASSES):
            _violations = [
                tid for tid, pid in _parent_of.items()
                if tid in _pending_tier and pid in _pending_tier
                and _pending_tier[tid] <= _pending_tier[pid]
                and tid not in _callup_resolved
                and (
                    # case①: 자식이 이번 시즌 승격해서 새로 생긴 충돌
                    (tid in moved_teams and _pending_tier[tid] < _original_tier.get(tid, _pending_tier[tid]))
                    # case②: 모팀이 이번 시즌 강등돼 자식 tier로 내려오며 새로 생긴 충돌
                    or (pid in moved_teams and _pending_tier[pid] > _original_tier.get(pid, _pending_tier[pid]))
                )
            ]
            if not _violations:
                break

            _any_change = False
            for _tid in _violations:
                _cid = _country_of.get(_tid)
                _cur_tier = _pending_tier[_tid]
                _pid_cur = _parent_of.get(_tid)
                _audit_base = {
                    "team_id": _tid, "parent_id": _pid_cur, "pass": _pass_i,
                    "country_id": _cid,
                    "classification_status": _classification_of.get(_tid),
                    "parent_tier": _pending_tier.get(_pid_cur),
                    "child_tier": _cur_tier,
                    "max_country_tier": _country_max_tier_map.get(_cid),
                    "entered_correction": True,
                }

                if _tid in moved_teams and _cur_tier < _original_tier.get(_tid, _cur_tier):
                    # ① 본인이 이번 시즌 승격해서 생긴 충돌 → 승격 취소
                    _upper_lid_conflict = _league_map.get((_cid, _cur_tier))
                    _orig_tier = _original_tier[_tid]
                    _orig_lid = _original_league[_tid]
                    if _upper_lid_conflict is None or _orig_lid is None:
                        _audit({**_audit_base, "correction_action": "CANCEL_PROMOTION",
                                "skip_reason": "TARGET_LEAGUE_MISSING", "result_tier": _cur_tier})
                        continue
                    _pending_tier[_tid] = _orig_tier
                    _team_move_updates.append((_orig_lid, _orig_tier, _tid))
                    _old_entry = _promo_log_entry_for_team.pop(_tid, None)
                    if _old_entry in _promotion_log_inserts:
                        _promotion_log_inserts.remove(_old_entry)
                    _tname = _team_info_cache.get(_tid, ("", ""))[0]
                    print(f"[산하팀 tier 보정] {year}년 {_tname} 승격 취소 "
                          f"(모팀과 tier 충돌, {_orig_tier}부 잔류로 되돌림)")

                    # 원래 있던 리그(_orig_lid)의 순위표에서 다음 NORMAL
                    # 후보를 찾아 대신 승격시킨다 — 이미 이동 처리됐거나
                    # 본인(취소된 팀)은 제외.
                    _repl = None
                    for _cand in _standings_cache.get(_orig_lid, []):
                        if _cand["id"] in moved_teams or _cand["id"] == _tid:
                            continue
                        if _cand.get("classification_status", "NORMAL") != "NORMAL":
                            continue
                        _repl = _cand
                        break
                    if _repl is not None:
                        _team_move_updates.append((_upper_lid_conflict, _cur_tier, _repl["id"]))
                        _pending_tier[_repl["id"]] = _cur_tier
                        moved_teams.add(_repl["id"])
                        _rname, _rlname = _team_info_cache.get(_repl["id"], ("", ""))
                        _new_entry = (year, _rname, _orig_tier, _cur_tier, _rlname, _orig_lid,
                                      _upper_lid_conflict, _repl["id"])
                        _promotion_log_inserts.append(_new_entry)
                        print(f"[산하팀 tier 보정] {year}년 {_rname}이(가) 대신 "
                              f"{_orig_tier}부→{_cur_tier}부 승격")
                        _audit({**_audit_base, "correction_action": "CANCEL_PROMOTION",
                                "skip_reason": "CORRECTED", "result_tier": _orig_tier,
                                "replacement_team_id": _repl["id"]})
                    else:
                        print(f"[산하팀 tier 보정] {year}년 {_orig_lid} 리그, "
                              f"승격 대체 후보 없음 — 슬롯 공석으로 둠")
                        _audit({**_audit_base, "correction_action": "CANCEL_PROMOTION",
                                "skip_reason": "CORRECTED_NO_REPLACEMENT", "result_tier": _orig_tier})
                    _any_change = True

                else:
                    # ② 산하팀은 안 움직였는데 모팀이 강등돼 내려오며 충돌
                    # → [2026-08 재설계, 신민용 확정: "강등 자체를 막으면
                    #   안 된다"] 예전엔 산하팀을 한 티어 더 강등시켰다(리그
                    #   순위와 무관한 인위적 조작 — "구조적 tier 예외"까지
                    #   따로 둬야 했을 만큼 부자연스러웠다). 이제는 산하팀의
                    #   리그·tier·순위를 전혀 건드리지 않고, 모팀이 산하팀
                    #   에서 부족 포지션 위주로 선수를 콜업해 산하팀 전력만
                    #   낮춘다 — 산하팀은 같은 리그에 남아 정상적으로
                    #   경쟁한다. tier가 안 바뀌므로 "이미 최하위라 더 못
                    #   내려간다"는 구조적 예외 자체가 더 이상 필요 없다.
                    _tname = _team_info_cache.get(_tid, ("", ""))[0]
                    _picked_ids = _affiliate_callup_from_child(conn, _pid_cur, _tid)
                    if _picked_ids:
                        print(f"[산하팀 전력조정] {year}년 {_tname}에서 {len(_picked_ids)}명 "
                              f"콜업 (모팀과 tier 충돌 → 리그·순위는 유지, 산하팀 전력만 조정)")
                        _audit({**_audit_base, "correction_action": "CALLUP",
                                "skip_reason": "CORRECTED", "result_tier": _cur_tier,
                                "callup_player_count": len(_picked_ids)})
                    else:
                        # 콜업할 대상이 없어도(산하팀 스쿼드가 이미 얇거나
                        # 부족 포지션이 없는 경우) tier는 그대로 둔다 —
                        # 억지로 리그를 조작하지 않는다는 원칙은 유지.
                        _audit({**_audit_base, "correction_action": "CALLUP",
                                "skip_reason": "NO_ELIGIBLE_PLAYERS", "result_tier": _cur_tier,
                                "callup_player_count": 0})
                    # 이번 시즌엔 이 (자식,모팀) 쌍을 다시 위반으로 잡지
                    # 않는다 — tier가 그대로라 재판정하면 매 패스마다 또
                    # 콜업이 반복된다.
                    _callup_resolved.add(_tid)
                    _any_change = True

            if not _any_change:
                break
        else:
            print(f"[산하팀 tier 보정] {year}년: 안전 상한({_MAX_RESOLUTION_PASSES}회) 도달 — "
                  f"남은 충돌은 다음 시즌으로 이월됨")
            for _tid in _violations:
                _audit({"team_id": _tid, "parent_id": _parent_of.get(_tid),
                        "correction_action": "NONE",
                        "skip_reason": "MAX_PASSES_EXCEEDED", "entered_correction": True})

        # [2026-08] 감사 로그 자체(_wb_audit_rows 수집)는 항상 하되, 실제
        # 디스크 기록은 constants.TIER_AUDIT_LOGGING이 True일 때만 —
        # 이 계측은 "산하팀 tier 역전" 잔존 버그 추적 전용이라, 정식
        # 플레이에서는 파일을 만들지 않는다(위 constants.py 주석 참고).
        if constants.TIER_AUDIT_LOGGING:
            try:
                with open("tier_audit.jsonl", "a", encoding="utf-8") as _af:
                    for _row in _wb_audit_rows:
                        _af.write(_json_audit.dumps(_row, ensure_ascii=False) + "\n")
            except Exception as _audit_e:
                print(f"[tier_audit] 로그 기록 실패: {_audit_e}")

    # [2026-07 버그수정] 팀 승/무/패/득실 초기화는 _end_of_season(진짜
    # 연도 전환 시점, 52→1주)으로 옮겼다 — 여기(_process_promotion_relegation)
    # 는 이제 44주(PLAYOFF_WEEK)보다도 앞선 43주 마지막날(day300)에 호출
    # 되므로, 초기화가 여기 남아있으면 아직 같은 해인데 팀 전적이 미리
    # 지워져서 국제대회 기간(44~52주) 내내 커리어·순위 화면이 깨져 보였다.

    # [2026-07 최적화] 위 루프에서 모아둔 승격/강등 UPDATE·INSERT를 각각
    # executemany로 한 번씩만 실행 — 팀 수만큼 개별 실행하던 것을 2회로 줄인다.
    _pr_t5 = _time_pr.perf_counter()
    # [2026-08] 이번 세션 디버깅용 하드코딩 감시 목록 — 다음 세션에서는
    # "실제 위반 발생 순간 자동 감지" 방식으로 교체 예정. 그때까지는
    # TIER_AUDIT_LOGGING 플래그로 같이 묶어서 정식 플레이엔 영향 없게 둔다.
    if constants.TIER_AUDIT_LOGGING:
        _wb_watch = {3848, 7059, 3462, 6544, 8408, 5868}
        _watched_moves = [m for m in _team_move_updates if m[2] in _wb_watch]
        if _watched_moves:
            print(f"[WATCH] {year}년 _team_move_updates 중 감시 대상 팀 이동 큐 순서: {_watched_moves}")
    if _team_move_updates:
        c.executemany("UPDATE teams SET league_id=?,current_tier=? WHERE id=?", _team_move_updates)
    if _momentum_reset_updates:
        c.executemany("UPDATE teams SET momentum_type=?, momentum_seasons_left=? WHERE id=?",
                      _momentum_reset_updates)
    if _promotion_log_inserts:
        c.executemany(
            """INSERT INTO promotion_log(year,team_name,from_tier,to_tier,league_name,
                                          from_league_id,to_league_id,team_id) VALUES(?,?,?,?,?,?,?,?)""",
            _promotion_log_inserts)
    if _po_pending_inserts:
        c.executemany(
            """INSERT INTO po_pending_slots(year,upper_league_id,lower_league_id,rule_id,side,
                                             offset_idx,team_id,team_name) VALUES(?,?,?,?,?,?,?,?)""",
            _po_pending_inserts)
    _pr_t6 = _time_pr.perf_counter()

    # 승강팀 OVR 평형 일괄 적용
    # [기능 변경] 이 리스케일 자체(승강팀 OVR을 새 리그 수준에 맞추는 것)는
    # 유지하되, "⚙️ ... 리그 적응" 로그는 요청에 따라 화면에 더 이상
    # 남기지 않는다(디버그성 정보라 매 시즌 로그가 지저분해짐).
    # [최적화] 팀마다 개별 rescale_team_to_target_ovr() 호출(=팀 수만큼 개별
    # SELECT) 대신, 배치 버전으로 대상 팀 전체를 한 번에 처리한다.
    # 리그 수가 많은 세이브(수백~천 단위 이동팀)일수록 절감 효과가 크다.
    # 계산 로직·결과는 rescale_team_to_target_ovr과 완전히 동일.
    try:
        rescale_teams_to_target_ovr_batch(_rescale_jobs, conn)
    except Exception as _e:
        add_log(f"[리스케일 오류] {_e}", "normal", year, 52)
    _pr_t7 = _time_pr.perf_counter()

    if DEBUG_RELEGATION_TRACKING:
        for _tid in _RELEGATION_DEBUG_TRACK:
            _relegation_debug_snapshot(_tid, "리스케일 직후")

    conn.commit()
    conn.close()
    _pr_t8 = _time_pr.perf_counter()

    _invalidate_team_ovr_cache()
    _pr_t9 = _time_pr.perf_counter()
    print(f"[PERF-PROMO] _process_promotion_relegation 세부: "
          f"my_player/season조회 {_pr_t1-_pr_t0:.3f}s | "
          f"teams+leagues조회 {_pr_t2-_pr_t1:.3f}s | "
          f"standings계산({len(all_league_ids)}개리그) {_pr_t3-_pr_t2:.3f}s | "
          f"ai_players OVR스캔 {_pr_t4-_pr_t3:.3f}s | "
          f"승강판정루프({len(cids)}개국) {_pr_t5-_pr_t4:.3f}s | "
          f"executemany({len(_team_move_updates)}건) {_pr_t6-_pr_t5:.3f}s | "
          f"리스케일({len(_rescale_jobs)}건) {_pr_t7-_pr_t6:.3f}s | "
          f"commit/close {_pr_t8-_pr_t7:.3f}s | 캐시무효화 {_pr_t9-_pr_t8:.3f}s")

    if my_new_league:
        p_up = get_player()
        if p_up:
            old_sal = p_up.get("salary", 0)
            conn_t = get_conn()
            new_tier_row = conn_t.execute("SELECT tier FROM leagues WHERE id=?", (my_new_league,)).fetchone()
            old_tier_row = conn_t.execute("SELECT tier FROM leagues WHERE id=?", (my_league_id,)).fetchone()
            conn_t.close()
            new_tier = new_tier_row["tier"] if new_tier_row else 3
            old_tier = old_tier_row["tier"] if old_tier_row else 3
            if new_tier < old_tier:
                if   season_avg_rating >= 7.5: mult = 2.00
                elif season_avg_rating >= 7.0: mult = 1.85
                elif season_avg_rating >= 6.5: mult = 1.65
                else:                          mult = 1.50
                new_sal = int(old_sal * mult)
                # [버그수정 2026-07, 신민용 지적] old_sal*mult(최대 2.00배)로
                # 새 연봉을 만들 뿐 _calc_salary를 안 거쳐서 등급 캡이 안
                # 걸렸다 — teams.league_id는 이 지점 이전에 이미 새 리그로
                # 커밋됐으므로 _my_grade_tier로 승격된 리그 기준 등급/국가를
                # 다시 조회해 캡을 씌운다.
                gt_p = _my_grade_tier(p_up)
                if gt_p:
                    g_wealth, g_tier, g_country = gt_p
                    new_sal = _clamp_salary_to_cap(new_sal, g_wealth, g_country, g_tier)
                _pct = int(round((mult - 1) * 100))
                add_log(f"💰 승격 연봉 인상! {fmt_money(old_sal)} → {fmt_money(new_sal)} "
                        f"(+{_pct}%, 평균평점 {season_avg_rating:.2f})", "event", year, 52)
            elif new_tier > old_tier:
                if   season_avg_rating >= 7.0: cut = 0.30
                elif season_avg_rating >= 6.5: cut = 0.40
                elif season_avg_rating >= 6.0: cut = 0.50
                else:                          cut = 0.60
                new_sal = int(old_sal * (1 - cut))
                _pct = int(round(cut * 100))
                add_log(f"💸 강등 연봉 삭감. {fmt_money(old_sal)} → {fmt_money(new_sal)} "
                        f"(-{_pct}%, 평균평점 {season_avg_rating:.2f})", "event", year, 52)
            else:
                new_sal = old_sal
            update_player(current_league_id=my_new_league,
                          salary=new_sal, current_tier=new_tier)
        else:
            update_player(current_league_id=my_new_league)
        add_log(f"📋 소속 리그가 변경되었습니다", "event", year, 52)

        # [2026-07 버그수정, 신민용 리포트: "커리어에 리그가 1부로 그대로
        # 뜬다"] _update_career_stats(진행 중 커리어 항목 실시간 갱신)는
        # "그 주에 경기가 있었을 때만" 불렸는데, 승강이 확정되는 시점은
        # 클럽 시즌이 끝난 뒤(경기가 없는 주)라 다음 시즌 첫 경기 전까지
        # career_entries.league_name/tier가 옛 리그로 그대로 남아있었다
        # (사이드바의 실시간 p.current_league_id는 맞게 바뀌는데, 커리어
        # 팝업만 안 따라옴). 리그가 바뀌는 바로 이 시점에 즉시 한 번
        # 강제로 동기화한다.
        _update_career_stats(get_player(), year, 52)

    for text, ltype in pending_logs:
        add_log(text, ltype, year, 52)

def _finish_incomplete_matches_for_season(season: int):
    """[실시간 전환 - 안전망] 이제 전 세계 모든 리그가 시즌 시작 시점에
    _generate_all_league_schedules()로 일정을 미리 받고, 매주
    _sim_all_ai_matches가 실시간으로 결과를 채운다. 따라서 시즌이 끝나는
    시점엔 어느 리그든 미완료 경기(-1)가 남아 있으면 안 된다 — 정상 흐름이면
    이 함수는 사실상 아무 것도 안 찾고 끝난다.
    혹시 리그 재편·저장 편집 등으로 놓친 경기가 있을 때만 대비하는 안전망이며,
    수백 개 리그를 몰아서 새로 생성+시뮬하던 기존 로직(연 1회 2~3초 프리징의
    원인)은 더 이상 필요 없어 제거했다.

    [버그수정 - 중요] 반드시 '지금 끝나는 이번 시즌'만 처리한다. 예전엔 방어적으로
    '혹시 몰라서' season-1(직전 시즌)도 같이 확인했는데, 이게 실제 사고를 냈다 —
    승강제(_process_promotion_relegation)와 내 커리어 팀순위 기록은 그 시즌이
    끝나는 '그 순간'의 match_results를 기준으로 이미 확정·저장된다. 그런데 그
    시즌이 지난 뒤(다음, 다다음 시즌 전환 시점)에 이 함수가 그 시즌을 다시
    들여다보다가 어쩌다 남아있던 미완료 경기 하나를 그때서야 채우면, 그 결과가
    이미 확정된 승강/우승 판정 시점보다 한참 뒤의(선수 노쇠/성장이 반영된) 팀
    전력으로 다시 굴러가면서 순위가 바뀔 수 있다 — 그러면 '내 커리어 기록'엔
    예전에 확정된 팀순위가 그대로 남아있는데, 세계 리그 화면(역대 우승팀 등)은
    나중에 새로 계산되며 다른 팀을 우승팀으로 보여주는 불일치가 생긴다.
    그래서 지금은 '이번 시즌'만 정리하고, 이미 지나간 시즌은 설령 구멍이 있어도
    그대로 둔다 — 그 구멍은 그 시즌 순위표에 남지만, 이미 확정되고 화면에
    보여준 과거 기록을 조용히 다시 쓰는 것보다는 안전하다."""
    conn = get_conn()
    c = conn.cursor()

    c.execute("""SELECT id, home_team_id, away_team_id FROM match_results
                  WHERE season=? AND home_score=-1""", (season,))
    stale = c.fetchall()
    if stale:
        batch = []
        for m in stale:
            ho = _team_avg_ovr(c, m["home_team_id"]) + _home_advantage() + _formation_bias(c, m["home_team_id"])
            ao = _team_avg_ovr(c, m["away_team_id"]) + _formation_bias(c, m["away_team_id"])
            diff = ho - ao
            outcome = _roll_outcome(diff)
            hs, as_ = _gen_score(outcome, diff)
            batch.append((hs, as_, m["id"]))
        c.executemany("UPDATE match_results SET home_score=?,away_score=? WHERE id=?", batch)

    conn.commit()
    conn.close()


# [실시간 전환] 전 세계 모든 리그(211개국 x 최대 5부, 약 675개)의 이번 시즌
# 일정을 시즌 시작 시점에 한 번에 깔아 둔다. 결과(score)는 절대 여기서 채우지
# 않는다 — 매주 정규 흐름의 _sim_all_ai_matches(week 필터만 있고 league_id
# 필터는 없음)가 자연스럽게 그 주차 경기를 실시간으로 채운다.
# [기존 방식과 차이] 예전엔 유저가 안 본 리그를 방치하다가 시즌 말에
# "일정 생성 + 즉시 전 경기 시뮬"을 몰아서 했다(그것도 매 시즌 반복 — 시즌
# 번호가 바뀌면 '라이브' 판정이 초기화되는 버그가 있었다). 이제는 일정만
# 미리 깔아 두는 가벼운 INSERT 작업만 여기서 하고, 실제 결과 계산(OVR 조회 +
# 승패 굴림)은 52주에 걸쳐 자연 분산된다.
# [성능] 단일 커넥션 + 소수의 배치 쿼리(리그별 SELECT/커밋 반복 없음)로 처리.
def _generate_all_league_schedules(season: int, year: int):
    """시즌 시작 시 전 세계 모든 리그의 이번 시즌 일정(-1,-1 스코어)을 생성한다.
    이미 일정이 있는 리그(80% 이상 채워짐)는 건드리지 않는다(멱등)."""
    import time as _time_perf3
    _tg0 = _time_perf3.perf_counter()
    # [2026-07 신설, 성능] 새 시즌 진입 시점에 지난 시즌(들)을 아카이브로
    # 옮겨 match_results를 '이번 시즌 것만' 유지 — 아래 INSERT의 인덱스
    # 갱신 비용이 시즌이 쌓여도 계속 일정하게 유지된다. 승강제 처리
    # (_process_promotion_relegation)는 이 함수보다 항상 먼저 끝나므로
    # 안전하다.
    # [2026-07 추가 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"]
    # archive_old_seasons()의 벌크 DELETE(직전 시즌 17만 행)와 아래의 벌크
    # INSERT(새 시즌 17만 행)가 항상 붙어서 일어나는데, 둘 다 match_results
    # 인덱스 6개를 매 행마다 갱신했다. 이 구간 전체를 인덱스 없이 돌리고
    # 끝나면 한 번에 재생성한다(벌크 빌드는 SQLite가 정렬 스캔 1회로
    # 처리해 건별 갱신 누적보다 훨씬 싸다). 이 구간의 SELECT들(완비판정/
    # dedup조회)은 archive_old_seasons가 이미 옛 시즌을 다 걷어내서
    # match_results가 거의 빈 상태이므로 인덱스 없이도 여전히 빠르다.
    from database import (archive_old_seasons, drop_match_results_indexes,
                          rebuild_match_results_indexes)
    _idx_conn = get_conn()
    drop_match_results_indexes(_idx_conn.cursor())
    _idx_conn.commit()
    # [2026-07] try/finally로 감싸 어떤 경로(조기 return, 예외)로 빠져나가든
    # 인덱스 재생성이 반드시 실행되도록 보장한다 — return마다 재생성 호출을
    # 따로 넣으면 나중에 return 경로가 늘 때마다 빠뜨리기 쉽고, 중간에
    # 예외가 나면 인덱스가 영구히 안 돌아와 이후 모든 쿼리가 느려진다.
    try:
        archive_old_seasons(season)
        _tg1 = _time_perf3.perf_counter()

        conn = get_conn()
        c = conn.cursor()

        # 1) 리그별 팀 목록(팀 수 상한 없음 — 8팀 고정 캡 제거) 한 번에 조회
        teams_by_league: dict = {}
        for r in c.execute("SELECT id, league_id FROM teams ORDER BY league_id, id"):
            lst = teams_by_league.setdefault(r["league_id"], [])
            lst.append(r["id"])

        if not teams_by_league:
            conn.commit()
            conn.close()
            return

        # 2) 이번 시즌 리그별 기존 경기 수 — 1회 GROUP BY로 완비 여부 판정
        # [버그수정 2026-07, 신민용 리포트] 이 함수는 원래 '이번 시즌 것만'
        # match_results(라이브)에 있다고 가정하고 카운트했는데, 만약 이 함수가
        # 이미 지나가서 archive_old_seasons()로 옮겨진 과거 시즌에 대해 다시
        # 호출되면(정상 흐름에선 안 일어나야 하지만, 어떤 경로로든 재호출되면)
        # 라이브 테이블엔 그 시즌 행이 0개로 보여 "완비 안 됨"으로 오판, 그
        # 시점의(이미 달라졌을 수 있는) 팀 구성으로 새 일정을 통째로 또 깔아버려
        # 같은 시즌 번호에 서로 다른 팀 구성의 경기가 중복 생기는 버그가 있었다
        # (실측: K3리그 시즌2에 정상 182경기(아카이브) + 다른 팀 구성 182경기
        # (라이브)가 동시에 존재 → 그 시즌 팀 수가 14→17로 부풀어 보임). 아카이브
        # 테이블도 합쳐서 세야 이미 완료된 과거 시즌을 정확히 "완비됨"으로 판정한다.
        sched_counts = {}
        for r in c.execute(
                "SELECT league_id, COUNT(*) as cnt FROM match_results WHERE season=? GROUP BY league_id",
                (season,)).fetchall():
            sched_counts[r["league_id"]] = sched_counts.get(r["league_id"], 0) + r["cnt"]
        for r in c.execute(
                "SELECT league_id, COUNT(*) as cnt FROM match_results_archive WHERE season=? GROUP BY league_id",
                (season,)).fetchall():
            sched_counts[r["league_id"]] = sched_counts.get(r["league_id"], 0) + r["cnt"]

        # 리그별 팀 수와 다전제(legs_for_team_count) 반영한 총 경기 수를 완비 판정
        # 기준으로 사용 — 8팀 고정 56경기 가정을 제거해 리그마다 정확히 동작한다.
        # [2026-08 버그수정, 신민용 리포트: "메이저 리그 사커(30팀) 경기 기록이
        # 아예 없다"] legs_for_team_count가 25팀 이상 리그엔 legs=1(단판)을
        # 반환하도록 오늘 바뀌었는데, 여기서 "legs // 2"로 정수나눗셈을 하면
        # legs=1일 때 결과가 0이 된다 — 그러면 완비 판정 임계값 자체가
        # n*(n-1)*0*0.8=0이 되어, sched_counts가 진짜 0건이어도 "0 < 0"은
        # 거짓이라 이 리그가 need_league_ids에 영원히 안 들어간다(=이미
        # 완비된 것으로 오판) → 30팀 리그 36개 전부 일정이 통째로 생성 안
        # 되는 사고로 이어졌다. legs=2 이상(짝수)에서는 "//2"와 "/2"가 결과가
        # 같으므로(기존 리그는 전혀 영향 없음), 실수 나눗셈으로 바꿔 legs=1도
        # 올바르게 0.5가 나오게 한다.
        need_league_ids = [
            lid for lid, tids in teams_by_league.items()
            if len(tids) >= 2
            and sched_counts.get(lid, 0) < len(tids) * (len(tids) - 1) * (legs_for_team_count(len(tids)) / 2) * 0.8
        ]
        if not need_league_ids:
            conn.commit()
            conn.close()
            return
        _tg2 = _time_perf3.perf_counter()

        # 3) 대상 리그들의 이번 시즌 기존 경기(예정/완료 불문) 한 번에 조회 → 중복 방지 딕셔너리
        #    [2026-07 버그수정] 같은 두 팀 조합이 후보 day 근방에 이미 있으면
        #    중복(_is_dup_fixture 참고) — day 기준 exact match였을 때 재생성
        #    시 하루 어긋난 중복 행이 생기던 문제, 그 뒤 week 기준으로
        #    바꿨던 것도 하루 차이가 week 경계를 넘으면 여전히 못 걸렀던
        #    문제까지 함께 해결한다.
        existing_by_league: dict = {}
        ph = ",".join("?" * len(need_league_ids))
        for r in c.execute(
                f"""SELECT league_id, day, home_team_id, away_team_id FROM match_results
                    WHERE season=? AND league_id IN ({ph}) AND day IS NOT NULL""",
                (season, *need_league_ids)):
            s = existing_by_league.setdefault(r["league_id"], {})
            s.setdefault((min(r["home_team_id"], r["away_team_id"]),
                          max(r["home_team_id"], r["away_team_id"])), []).append(r["day"])
        _tg3 = _time_perf3.perf_counter()

        new_rows = []
        for lid in need_league_ids:
            tids = teams_by_league[lid]
            existing_matches = existing_by_league.get(lid, {})
            new_rows.extend(_build_league_schedule_rows(lid, tids, season, year, existing_matches))
        _tg4 = _time_perf3.perf_counter()

        if new_rows:
            c.executemany("""INSERT INTO match_results
                             (league_id,week,home_team_id,away_team_id,
                              home_score,away_score,season,year,day)
                             VALUES(?,?,?,?,-1,-1,?,?,?)""", new_rows)

        conn.commit()
        conn.close()
        _tg5 = _time_perf3.perf_counter()
        print(f"[PERF]   일정생성 세부: 아카이브이동 {_tg1-_tg0:.2f}s | "
              f"완비판정조회 {_tg2-_tg1:.2f}s | 기존경기dedup조회 {_tg3-_tg2:.2f}s | "
              f"일정계산 {_tg4-_tg3:.2f}s | INSERT+commit {_tg5-_tg4:.2f}s | "
              f"(대상리그 {len(need_league_ids)}개, 신규경기 {len(new_rows)}건)")
    finally:
        # [2026-07 추가 최적화] 드롭했던 인덱스 6개를 벌크 작업이 끝난 지금
        # (또는 조기 종료·예외 시에도) 반드시 한 번에 재생성한다.
        _tg_idx0 = _time_perf3.perf_counter()
        rebuild_match_results_indexes(_idx_conn.cursor())
        _idx_conn.commit()
        print(f"[PERF]   인덱스재생성 {_time_perf3.perf_counter()-_tg_idx0:.2f}s")


# [2026-07 신설, 신민용 리포트: "폴란드에서 3년 뛴 선수인데 왜 K리그가
# 10개씩 뜨나 — 현실에선 안 그렇잖아. 내가 말한 건 계약 만료 전 마지막에
# 뛰었던 리그 기준으로 입단 범위를 조절하는 것"] 처음엔 커리어 전체를
# 가중합해서 비율을 냈는데, 그건 "커리어 전체 인상"이지 신민용이 말한
# "마지막으로 뛴 리그" 기준이 아니다 — 자칫 오래 뛴 해외 리그 쪽으로
# 항상 쏠리는 것처럼 보일 수 있어 더 단순하고 명확한 기준으로 바꾼다.
# has_team(이적 오퍼) 분기가 '현재 리그 국가'를 쓰는 것과 완전히 같은
# 원리 — 무소속 분기는 '현재'가 없으니 career_entries의 가장 마지막
# (직전) 기록의 리그 국가를 그 자리에 쓴다.
def _last_played_country_id(p):
    conn = get_conn()
    c = conn.cursor()
    try:
        row = c.execute(
            """SELECT league_name FROM career_entries
               ORDER BY id DESC LIMIT 1""").fetchone()
        if not row or not row["league_name"]:
            return None
        row_c = c.execute(
            """SELECT cn.id as cid FROM leagues l
               JOIN countries cn ON l.country_id=cn.id
               WHERE l.name=? LIMIT 1""", (row["league_name"],)).fetchone()
        return row_c["cid"] if row_c else None
    finally:
        conn.close()


def _record_team_offer_cooldown(team_id):
    """[2026-08 신설, 신민용 확정] 협상 결렬(입단/오퍼 공통)이 뜬 팀은
    '연도' 기준으로 최소 1년~최대 2년 가까이 다시 오퍼를 못 보낸다.
    몇 주차였는지는 안 따지고 오직 연도만 기록한다 — 그래서 2002년 25주든
    50주든 상관없이 항상 "2004년 1주차부터 재오퍼 가능"이 된다(아래
    _is_team_offer_blocked의 year+2 조건과 짝을 이룸).
    (기존 offer_refused 테이블은 스키마만 있고 실제로 쓰는 코드가 없던
    미완성 스캐폴드였다 — 이제부터 실제로 채워 넣는다.)"""
    p = get_player()
    if not p:
        return
    cur_year = p.get("current_year", 0)
    conn = get_conn()
    conn.execute("INSERT INTO offer_refused(team_id, year) VALUES(?,?)", (team_id, cur_year))
    conn.commit()
    conn.close()


def _blocked_offer_team_ids(cur_year) -> set:
    """[2026-08 신설] 아직 냉각기가 안 끝난(현재연도 < 거절연도+2) 팀 id
    집합을 한 번에 조회 — generate_offers()가 팀마다 개별 조회하지 않고
    이 결과를 배치로 필터링에 쓴다."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT team_id FROM offer_refused WHERE year + 2 > ?",
        (cur_year,)).fetchall()
    conn.close()
    return {r["team_id"] for r in rows}


def generate_offers(count=5, force=False) -> list:
    """[2026-07 재설계] count 파라미터는 더 이상 총 개수를 결정하지 않는다
    (하위호환을 위해 시그니처만 유지) — 실제 개수는 상황별로 아래 상수에
    고정되어 있다. 자세한 이유는 아래 주석 참고.

    [2026-07 버그수정, 신민용 리포트: "계약만료 이후 팀 입단할 때 왜
    아무것도 안떠?"] center_panel.py의 "🔔 오퍼 ON/OFF" 버튼 툴팁엔 원래
    "팀 입단(무소속 강제 입단)에는 영향 없음"이라고 명시돼 있었는데, 정작
    이 함수는 그 구분 없이 offers_enabled=0이면 무조건 빈 리스트를
    돌려줬다 — 그래서 평소 오퍼 알림을 꺼둔 사람이 계약만료로 무소속이
    되면, 강제 입단 창(_do_join)조차 자동 오퍼가 0개로 떠서 "직접 지원"
    슬롯 4개만 덩그러니 보이는 문제가 있었다. force=True면(강제 입단
    호출 전용) offers_enabled 뮤트를 무시하고 항상 생성한다."""
    p = get_player()
    if not p: return []

    # [2026-07 수정] 오퍼 ON/OFF는 이미 있던 offers_enabled 필드를 그대로
    # 쓴다(center_panel.py의 기존 "🔔 오퍼 ON/OFF" 버튼과 동일 필드) —
    # 처음엔 별도 필드(receive_transfer_offers)를 새로 만들었는데, 이미
    # 똑같은 기능의 토글이 있었다는 걸 나중에 발견해서 하나로 통합했다.
    # center_panel.py와 동일하게 "이적 요청 중이면 토글 꺼져 있어도 계속
    # 옴" 예외를 그대로 유지한다. 강제판매(별도 로직)는 이 토글과 무관.
    # force=True(강제 입단)면 이 뮤트 자체를 아예 건너뛴다 — 위 버그수정
    # 주석 참고.
    if not force and not p.get("offers_enabled", 1) and not p.get("transfer_requested"):
        return []

    # [2026-07 재설계 v2, 신민용 최종안] 상황별로 의도적으로 차이를 크게
    # 둔다 — 현실에서도 "계약 중 선수는 구단이 이적료를 요구해서 접촉이
    # 조심스럽고, 자유계약 선수는 여러 팀이 부담 없이 찔러본다"는 차이가
    # 있는데, 예전엔 무소속 12 vs 소속 10으로 거의 차이가 없었다.
    #   · 첫 입단(17세, 커리어 없음): 자국 10 + 타국 6 = 16
    #   · 소속 있음(이적시즌): 현재리그 4 + 자국 3 + 기타 5 = 12
    #     (이적요청 중이면 기타에 보너스를 더해 14 — "어디든 갈 의향 있다"는
    #      신호이므로 해외 쪽이 눈에 띄게 늘어야 체감이 산다)
    #   · 계약만료·방출(자유계약): 직전 리그가 해외였다면
    #       직전리그 8 + 자국 3 + 기타 4 = 15
    #     직전 리그가 자국이었다면(또는 커리어 기록 없음)
    #       자국 10 + 기타 5 = 15
    from constants import TRANSFER_REQUEST_OFFER_BONUS, get_league_relative_margin, get_passive_offer_rank_weight
    transfer_req = bool(p.get("transfer_requested"))
    _bonus = TRANSFER_REQUEST_OFFER_BONUS if transfer_req else 0

    FIRST_JOIN_DOMESTIC = 10
    FIRST_JOIN_FOREIGN  = 6

    NO_TEAM_DOMESTIC_SAME = 10   # 자유계약, 직전 리그 = 자국(또는 정보 없음)
    NO_TEAM_FOREIGN_SAME  = 5
    NO_TEAM_PREV_LEAGUE   = 8    # 자유계약, 직전 리그가 해외였던 경우
    NO_TEAM_HOMETOWN      = 3
    NO_TEAM_OTHER_FOREIGN = 4

    HAS_TEAM_LEAGUE_COUNTRY = 4
    HAS_TEAM_HOMETOWN       = 3
    HAS_TEAM_FOREIGN        = 5 + _bonus   # 이적요청이면 +2 → 계약중12→이적요청14

    # [2026-07 신설, 구단판매추진 설계 확정] 판매추진 점수에 따라 오퍼
    # 개수(확률의 대리 지표)를 배율로 늘린다: 2점=x1.2, 3점=x1.5, 4점+=x2.0.
    # 판매추진 비활성/토글 꺼짐이면 배율 1.0(영향 없음).
    _sale_push_mult = 1.0
    if p.get("sale_push_active") and p.get("allow_club_sale_push", 1):
        _sp_score, _ = _calc_sale_push_score(p, p.get("current_year", 0))
        if _sp_score >= 4:
            _sale_push_mult = 2.0
        elif _sp_score == 3:
            _sale_push_mult = 1.5
        elif _sp_score == 2:
            _sale_push_mult = 1.2
    if _sale_push_mult != 1.0:
        HAS_TEAM_LEAGUE_COUNTRY = max(HAS_TEAM_LEAGUE_COUNTRY, round(HAS_TEAM_LEAGUE_COUNTRY * _sale_push_mult))
        HAS_TEAM_HOMETOWN = max(HAS_TEAM_HOMETOWN, round(HAS_TEAM_HOMETOWN * _sale_push_mult))
        HAS_TEAM_FOREIGN = max(HAS_TEAM_FOREIGN, round(HAS_TEAM_FOREIGN * _sale_push_mult))

    conn = get_conn()
    c = conn.cursor()

    ovr         = p.get("ovr", 40)
    age         = p.get("age", 17)
    agent       = p.get("agent_grade", "F")
    nationality = p.get("nationality", "")
    has_team    = bool(p.get("current_team_id", 0))
    my_tid      = p.get("current_team_id", 0)
    grades      = _suitable_grades(ovr, agent)
    # [2026-07 버그수정, 신민용 지적: "인기도/명성이 직접 지원엔 반영되는데
    # 자동 오퍼엔 전혀 안 쓰인다"] calc_apply_prob_with_context()는 이미
    # pop_mod/fame_mod를 반영하는데, 자동 오퍼(_team_fits_me)는 순수 OVR
    # 갭 대비 마진만 봐서 같은 선수인데도 "직접 지원하면 화제성이 통하고
    # 오퍼는 안 통하는" 불일치가 있었다. 동일한 곡선(_apply_pop_mod/
    # _apply_fame_mod)으로 계산해 오퍼 쪽 마진에도 더해준다 — 화제성/명성이
    # 높을수록 살짝 더 높은 수준의 팀에서도 오퍼가 뜰 수 있게.
    _pop_fame_bonus = min(4.0, (_apply_pop_mod(p.get("popularity", 0)) + _apply_fame_mod(p.get("fame", 0))) / 3.0)

    from constants import ALL_STATS
    avg_stat = sum(p.get(s, 40) for s in ALL_STATS) / len(ALL_STATS)

    # 자국 country_id + 등급 조회 (force_max_tier 계산에 필요해 먼저 선언)
    my_country_id = None
    my_country_grade = None
    if nationality:
        row_c = c.execute("SELECT id, grade FROM countries WHERE name=?", (nationality,)).fetchone()
        if row_c:
            my_country_id = row_c["id"]
            my_country_grade = row_c["grade"]
    my_join_margin = CLUB_JOIN_MARGIN_BY_GRADE.get(my_country_grade, CLUB_JOIN_MARGIN)

    # 자국 리그 최대 tier (없으면 3 기본값)
    def _country_max_tier(cid):
        if not cid: return 3
        row_mt = c.execute("SELECT MAX(tier) as mt FROM leagues WHERE country_id=?", (cid,)).fetchone()
        return int(row_mt["mt"]) if row_mt and row_mt["mt"] else 3

    my_max_tier = _country_max_tier(my_country_id)
    # 17세 이하 저능력 선수는 가장 하위 리그(max_tier)만 허용
    force_max_tier = (age <= 17 and avg_stat < 50)

    # [최적화] 전체 팀 평균 OVR을 1회 SELECT → dict 캐시 (기존: 루프마다 SELECT 최대 120회)
    _team_avg_cache_offers: dict = {
        r["team_id"]: r["avg_ovr"]
        for r in c.execute(
            "SELECT team_id, AVG(ovr) as avg_ovr FROM ai_players GROUP BY team_id"
        ).fetchall()
        if r["avg_ovr"] is not None
    }

    # [밸런스 조정 2026-07, 현실성 검토 반영] 마진 기준을 "그 나라 등급"에서
    #   "그 팀이 자기 리그 안에서 몇 등급이냐(상대적 위치)"로 변경.
    #   기존엔 F급 국가면 무조건 마진 7(팀 평균보다 7 낮아도 입단) 처럼
    #   나라 하나로 퉁쳤는데, 실제로는 같은 나라 안에서도 우승권 팀은
    #   깐깐하고 강등권 팀은 관대해야 자연스럽다. 그래서 같은 리그 소속팀들
    #   끼리 평균 OVR 백분위를 매겨 명문(상위 20%, 마진1)~최하위(하위 20%,
    #   마진5)로 나눈다.
    _team_league_map: dict = {
        r["id"]: r["league_id"]
        for r in c.execute("SELECT id, league_id FROM teams").fetchall()
    }
    _league_avgs: dict = {}
    for _tid, _avg in _team_avg_cache_offers.items():
        _lid = _team_league_map.get(_tid)
        if _lid is None:
            continue
        _league_avgs.setdefault(_lid, []).append(_avg)
    for _lid in _league_avgs:
        _league_avgs[_lid].sort(reverse=True)  # 내림차순: 앞쪽 = 리그 내 강팀

    def _team_relative_margin(team_row) -> int:
        """팀이 속한 리그 내 평균 OVR 백분위로 마진(1~5) 결정.
           명문(상위20%)=1 ~ 최하위(하위20%)=5. 리그 정보가 없으면 하위호환
           기본값(CLUB_JOIN_MARGIN)으로 폴백."""
        tid = team_row["id"]
        team_avg = _team_avg_cache_offers.get(tid)
        peers = _league_avgs.get(_team_league_map.get(tid))
        if team_avg is None or not peers or len(peers) < 2:
            return CLUB_JOIN_MARGIN
        rank = sum(1 for v in peers if v >= team_avg)  # 1=리그 최강팀
        pct = rank / len(peers)
        return get_league_relative_margin(pct)

    def _team_fits_me(team_row) -> bool:
        """팀 평균 OVR 대비 내 OVR 차이가 '그 팀의 리그 내 상대적 위치별
           마진(+ 인기도/명성 보너스)' 이내면 True. 명문 팀일수록 마진이
           작아(빡빡) 검증된 선수만 입단 가능, 하위권/강등권 팀일수록
           관대하다. 인기도/명성이 높으면(화제성 있는 선수) 약간 더 높은
           수준의 팀에서도 오퍼가 뜰 수 있다(direct-apply 확률 계산과
           동일한 원리 — _pop_fame_bonus 참고)."""
        team_avg = _team_avg_cache_offers.get(team_row["id"])
        if team_avg is None:
            return True
        margin = _team_relative_margin(team_row) + _pop_fame_bonus
        return (team_avg - ovr) <= margin

    first_join = (not has_team and age <= 18)

    offers = []
    tried  = 0

    # [2026-07 신설, 신민용 요청: "국가가 여러 개인 경우도 있잖아 — 이럴 땐
    # 최소한 각 국가마다 1개씩은 보장"] 귀화 등으로 nationality/nationality2/
    # nationality3/nationality4에 국적이 여러 개 실려 있을 수 있다. 지금까지는
    # '자국' 풀을 전부 nationality(주 국적) 하나로만 채워서, 두 번째·세 번째
    # 국적은 오퍼 목록에 전혀 반영이 안 됐다. 보유 국적 전부를 모아두고,
    # 자국 풀을 채울 때 국적 수만큼(슬롯이 허용하는 한) 1개씩 먼저 보장한다.
    def _all_my_nationality_ids():
        names = []
        for k in ("nationality", "nationality2", "nationality3", "nationality4"):
            v = p.get(k)
            if v and v not in names:
                names.append(v)
        ids = []
        for nm in names:
            row = c.execute("SELECT id FROM countries WHERE name=?", (nm,)).fetchone()
            if row and row["id"] not in ids:
                ids.append(row["id"])
        return ids

    _my_nat_ids = _all_my_nationality_ids()

    # [팀입단 확장] 자국/고향/타국 국가풀에서 팀을 채우는 공용 헬퍼.
    #   exclude_ids: 이미 뽑힌 team_id 집합(중복 방지, 호출부에서 관리).
    def _fill_country_pool(country_id, want, exclude_ids, max_tier_cap=None):
        pool = []
        if not country_id or want <= 0:
            return pool
        _max_t = max_tier_cap or _country_max_tier(country_id)
        _tiers = list(range(1, _max_t + 1))
        _weights = tier_weights_by_ovr_n(ovr, _max_t)
        _tr = 0
        while len(pool) < want and _tr < 60:
            _tr += 1
            tier = _max_t if force_max_tier else random.choices(_tiers, _weights)[0]
            c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                cn.name as country,cn.flag,cn.grade
                         FROM teams t
                         JOIN leagues l ON t.league_id=l.id
                         JOIN countries cn ON l.country_id=cn.id
                         WHERE cn.id=? AND l.tier=?
                         ORDER BY RANDOM() LIMIT 20""", (country_id, tier))
            # [상위권 우선 가중치 추첨 2026-07, 신민용 설계+GPT 검토] 후보를
            #   넉넉히 뽑아 마진 통과한 것들만 남긴 뒤, 팀 평균 OVR이 높은
            #   순서로 줄 세워 순위별 가중치로 추첨한다. 마진(들어갈 수
            #   있는지)은 그대로 두고 '그 안에서 어떤 팀을 보여줄지'만
            #   상위권 쪽에 무게를 실어서, 매번 같은 1~2팀만 고정되지 않게
            #   한다 — 하위권은 어차피 직접 지원으로도 갈 수 있어 패시브
            #   오퍼 슬롯을 거기 쓸 이유가 없다.
            cands = [r for r in c.fetchall()
                     if r["id"] not in exclude_ids and r["id"] != my_tid and _team_fits_me(r)]
            if not cands:
                continue
            cands.sort(key=lambda r: _team_avg_cache_offers.get(r["id"], 0), reverse=True)
            _w = [get_passive_offer_rank_weight(i + 1) for i in range(len(cands))]
            row = random.choices(cands, _w)[0]
            _wealth_g = get_league_grade(row["country"], row["grade"])
            salary = _clamp_salary_to_cap(
                int(_calc_salary(_wealth_g, tier, ovr, row["country"], row["name"], year=p.get("current_year"), team_id=row["id"], talent_tier=p.get("talent_tier")) * random.uniform(0.85, 1.15)),
                _wealth_g, row["country"], tier, talent_tier=p.get("talent_tier"))
            pool.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))
            exclude_ids.add(row["id"])
        return pool

    def _fill_home_pool_multi(want, exclude_ids, nat_ids=None):
        """자국(고향) 풀을 채우되, 보유 국적이 여러 개면 want가 허용하는
        한도 내에서 국적마다 최소 1개씩 먼저 보장하고, 남는 슬롯은 주
        국적(가장 앞) 위주로 채운다. nat_ids를 안 주면 _my_nat_ids 전체 사용."""
        pool = []
        ids = nat_ids if nat_ids is not None else _my_nat_ids
        if not ids or want <= 0:
            return pool
        guaranteed = ids[:want]
        for cid in guaranteed:
            pool.extend(_fill_country_pool(cid, 1, exclude_ids))
        remaining = want - len(pool)
        if remaining > 0:
            pool.extend(_fill_country_pool(ids[0], remaining, exclude_ids))
        return pool

    def _fill_foreign_pool(want, exclude_country_ids, exclude_ids):
        pool = []
        if want <= 0:
            return pool

        _f_tiers   = [1, 2, 3]
        _f_weights = tier_weights_by_ovr_n(ovr, 3)
        _tr = 0
        while want > 0 and _tr < 80:
            _tr += 1
            _grade_filter = random.choice(grades)
            tier = 3 if force_max_tier else random.choices(_f_tiers, _f_weights)[0]
            _excl = [cid for cid in exclude_country_ids if cid]
            if _excl:
                placeholders = ",".join("?" * len(_excl))
                c.execute(f"""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.grade=? AND l.tier=? AND cn.id NOT IN ({placeholders})
                             ORDER BY RANDOM() LIMIT 20""", tuple([_grade_filter, tier] + _excl))
            else:
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.grade=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 20""", (_grade_filter, tier))
            # [상위권 우선 가중치 추첨 2026-07] _fill_country_pool과 동일한
            #   원리 — 넉넉히 뽑아서 마진 통과분만 남기고, 팀 평균 OVR
            #   내림차순 순위별 가중치로 추첨한다.
            cands = [r for r in c.fetchall()
                     if r["id"] not in exclude_ids and r["id"] != my_tid and _team_fits_me(r)]
            if not cands:
                continue
            cands.sort(key=lambda r: _team_avg_cache_offers.get(r["id"], 0), reverse=True)
            _w = [get_passive_offer_rank_weight(i + 1) for i in range(len(cands))]
            row = random.choices(cands, _w)[0]
            _wealth_g = get_league_grade(row["country"], row["grade"])
            salary = _clamp_salary_to_cap(
                int(_calc_salary(_wealth_g, tier, ovr, row["country"], row["name"], year=p.get("current_year"), team_id=row["id"], talent_tier=p.get("talent_tier")) * random.uniform(0.85, 1.15)),
                _wealth_g, row["country"], tier, talent_tier=p.get("talent_tier"))
            pool.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))
            exclude_ids.add(row["id"])
            want -= 1
        return pool

    def _interleave(*groups):
        """그룹들을 리스트로 받아 행(row) 우선으로 지그재그 배치.
           예: _interleave([d1,d2,d3],[h1,h2,h3],[f1,f2,f3,f4])
               -> [d1,h1,d2,h2,d3,h3,f1,f2,f3,f4]
           (마지막 그룹은 남는 슬롯 전체를 그대로 뒤에 이어붙임 = 하단 풀행)"""
        result = []
        head_groups = groups[:-1]
        tail_group  = groups[-1] if groups else []
        max_len = max((len(g) for g in head_groups), default=0)
        for i in range(max_len):
            for g in head_groups:
                if i < len(g):
                    result.append(g[i])
        result.extend(tail_group)
        return result

    if first_join and my_country_id:
        # ── [자국 보장] 첫 입단은 자국 리그에서 최소 1~2개는 반드시 온다 ──
        #   현실 반영: 유스 출신은 우선 자국에서 데뷔 제안을 받는다.
        #
        #   [핵심 설계] '내 평균 수준으로 어느 티어가 입단 가능한지'를 먼저 판정하고,
        #   그 가능한 티어들 중에서만 1부10%/2부30%/3부60% 비중으로 뽑는다.
        #     - 이탈리아처럼 1부가 매우 높은 자국이면, 17세 신인은 1·2부가 수준
        #       미달이라 애초에 후보에서 빠지고 3부만 가능 → 사실상 100% 3부.
        #     - 한국처럼 1부가 약하면 1부도 후보에 들어 10% 확률로 1부 데뷔 가능.
        #   판정 기준은 일반 슬롯과 동일한 _team_fits_me (팀 평균 OVR - 내 OVR ≤ 8).
        guarantee = random.choice([1, 2])

        def _tier_fittable(tier) -> bool:
            """자국 해당 티어에 '내 수준에 맞는' 팀이 하나라도 존재하면 True.
               (가장 약한 팀 기준: 그 리그 최저 팀 평균 OVR이 내 +8 이내면 가능)"""
            c2 = conn.cursor()
            c2.execute("""SELECT MIN(ta.avg_ovr) AS min_avg
                          FROM teams t
                          JOIN leagues l ON t.league_id=l.id
                          JOIN (SELECT team_id, AVG(ovr) AS avg_ovr
                                  FROM ai_players GROUP BY team_id) ta
                                ON ta.team_id=t.id
                          WHERE l.country_id=? AND l.tier=?""",
                       (my_country_id, tier))
            r = c2.fetchone()
            if not r or r["min_avg"] is None:
                # 그 티어 자체가 자국에 없거나 선수 데이터 없음 → 후보 아님
                return False
            return (r["min_avg"] - ovr) <= my_join_margin

        def _try_domestic(tier, relax=False):
            """자국 특정 티어에서 '수준 맞는' 팀 1개 탐색.
               relax=True면 _team_fits_me 무시하고 아무 팀이나(데뷔 보장용)."""
            if relax:
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.id=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 1""", (my_country_id, tier))
                row = c.fetchone()
                if not row: return False
            else:
                # 팀 평균 OVR - 내 OVR <= CLUB_JOIN_MARGIN 인 팀들 중 무작위 1팀.
                #   (_tier_fittable 와 동일 기준으로, 판정-선택 불일치를 없앤다)
                c.execute(f"""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             JOIN (SELECT team_id, AVG(ovr) AS avg_ovr
                                     FROM ai_players GROUP BY team_id) ta
                                   ON ta.team_id=t.id
                             WHERE cn.id=? AND l.tier=? AND (ta.avg_ovr - ?) <= {int(my_join_margin)}
                             ORDER BY RANDOM() LIMIT 1""", (my_country_id, tier, ovr))
                row = c.fetchone()
                if not row: return False
            if any(o["team_id"] == row["id"] for o in offers): return False
            if row["id"] == my_tid: return False
            _wealth_g = get_league_grade(row["country"], row["grade"])
            salary = _clamp_salary_to_cap(
                int(_calc_salary(_wealth_g, tier, ovr, row["country"], row["name"], year=p.get("current_year"), team_id=row["id"], talent_tier=p.get("talent_tier")) * random.uniform(0.85, 1.15)),
                _wealth_g, row["country"], tier, talent_tier=p.get("talent_tier"))
            offers.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))
            return True

        # [1단계] 내 수준으로 가능한 자국 티어 확정.
        #   force_max_tier(저능력 17세)이면 무조건 최하위 부만.
        if force_max_tier:
            fittable = [my_max_tier] if _tier_fittable(my_max_tier) else []
        else:
            fittable = [t for t in range(1, my_max_tier + 1) if _tier_fittable(t)]

        # [2026-07 크래시 수정, 신민용 리포트: "KeyError: 6" — generate_offers
        # 에서 죽음] TIER_W가 1~5부 가중치만 정의돼 있었는데, 빅6(잉글랜드/
        # 스페인/이탈리아/독일/프랑스/브라질 — SS·S급)는 실제 리그 데이터가
        # 6부까지 있다(data/leagues.py). 이 나라 출신 선수가 자국 최하위
        # 티어(6부)까지 들어갈 수 있는 상황(주로 저능력 유망주)에서
        # `TIER_W[6]`을 찾다가 KeyError로 그대로 죽었다. 6부 가중치를
        # 추가하고, 혹시 나중에 7부 이상이 생겨도 죽지 않도록 .get()
        # 폴백(6부와 동일한 최저 비중)으로 방어한다.
        TIER_W = {1: 5, 2: 20, 3: 40, 4: 25, 5: 8, 6: 2}
        for _ in range(guarantee):
            placed = False
            if fittable:
                # 가능 티어만 남긴 가중치로 뽑기 → 매 시도 새로 뽑아 비중 유지
                for _ in range(8):
                    weights = [TIER_W.get(t, 2) for t in fittable]
                    pick_tier = random.choices(fittable, weights)[0]
                    if _try_domestic(pick_tier):
                        placed = True; break
            # [예외 보강] 가능 티어가 없거나(자국 1·2·3부 모두 수준 초과) 못 채웠으면
            #   3부에서 기준 완화해서라도 데뷔 기회 1개는 보장.
            if not placed:
                for _ in range(10):
                    if _try_domestic(my_max_tier, relax=True):
                        placed = True; break
            # 자국에 3부 리그 자체가 없으면 더는 강제하지 않음

        # 자국 팀 중 내 수준에 맞는 것만 우선
        # [2026-07 재설계 v2] 자국 10 + 타국 6 = 16 고정.
        domestic_count = FIRST_JOIN_DOMESTIC
        _seen_ids_fj = {o["team_id"] for o in offers}
        # [2026-07 신설] 보유 국적이 여러 개면(귀화 등) 주 국적 외 나머지도
        # 슬롯이 허용하는 한 1개씩 먼저 보장한다.
        for _cid in _my_nat_ids[1:]:
            if len(offers) >= domestic_count:
                break
            offers.extend(_fill_country_pool(_cid, 1, _seen_ids_fj))
        _dom_tiers = list(range(1, my_max_tier + 1))
        _dom_weights = tier_weights_by_ovr_n(ovr, my_max_tier)
        while len(offers) < domestic_count and tried < 80:
            tried += 1
            _grade_filter = random.choice(grades)   # DB 쿼리 필터용
            tier  = my_max_tier if force_max_tier else random.choices(_dom_tiers, _dom_weights)[0]
            c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                cn.name as country,cn.flag,cn.grade
                         FROM teams t
                         JOIN leagues l ON t.league_id=l.id
                         JOIN countries cn ON l.country_id=cn.id
                         WHERE cn.id=? AND l.tier=?
                         ORDER BY RANDOM() LIMIT 1""", (my_country_id, tier))
            row = c.fetchone()
            if not row: continue
            if any(o["team_id"] == row["id"] for o in offers): continue
            if row["id"] == my_tid: continue
            if not _team_fits_me(row): continue
            _wealth_g = get_league_grade(row["country"], row["grade"])
            salary = _clamp_salary_to_cap(
                int(_calc_salary(_wealth_g, tier, ovr, row["country"], row["name"], year=p.get("current_year"), team_id=row["id"], talent_tier=p.get("talent_tier")) * random.uniform(0.85, 1.15)),
                _wealth_g, row["country"], tier, talent_tier=p.get("talent_tier"))
            offers.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))

        # 자국에서 못 채웠거나 해외 슬롯이 남은 경우 → 타국으로 채움
        # [2026-07 수정] 보유 국적 전부를 제외한다(주 국적 하나만 빼면
        # 두 번째 국적 나라가 '타국' 풀에 또 중복으로 나올 수 있었다).
        _no_team_total = domestic_count + FIRST_JOIN_FOREIGN
        _nat_excl = _my_nat_ids or ([my_country_id] if my_country_id else [])
        if len(offers) < _no_team_total:
            tried2 = 0
            while len(offers) < _no_team_total and tried2 < 60:
                tried2 += 1
                _grade_filter = random.choice(grades)   # DB 쿼리 필터용
                _foreign_max = _country_max_tier(None)
                _f_tiers = list(range(1, _foreign_max + 1))
                _f_weights = tier_weights_by_ovr_n(ovr, _foreign_max)
                tier  = _foreign_max if force_max_tier else random.choices(_f_tiers, _f_weights)[0]
                _ph = ",".join("?" * len(_nat_excl)) if _nat_excl else "0"
                c.execute(f"""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.id NOT IN ({_ph}) AND cn.grade=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 1""",
                          tuple(_nat_excl) + (_grade_filter, tier))
                row = c.fetchone()
                if not row: continue
                if any(o["team_id"] == row["id"] for o in offers): continue
                if row["id"] == my_tid: continue
                if not _team_fits_me(row): continue
                _wealth_g = get_league_grade(row["country"], row["grade"])
                salary = _clamp_salary_to_cap(
                    int(_calc_salary(_wealth_g, tier, ovr, row["country"], row["name"], year=p.get("current_year"), team_id=row["id"], talent_tier=p.get("talent_tier")) * random.uniform(0.85, 1.15)),
                    _wealth_g, row["country"], tier, talent_tier=p.get("talent_tier"))
                offers.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))

        # [그리드 배치] 자국(좌열) / 타국(우열)이 매 행마다 번갈아 오도록 재정렬 + 구역 태그
        _dom_group = offers[:domestic_count]
        _for_group = offers[domestic_count:]
        for o in _dom_group: o["_zone"] = "domestic"
        for o in _for_group: o["_zone"] = "foreign"
        offers = _interleave(_dom_group, _for_group, [])

        # [2026-07 신설, 신민용 요청: "해외파는 보통 유스 때부터 가는 건데
        # 지금은 성인 이적시장 논리로만 해외 진출을 판정한다"] 지금까지
        # 모든 해외 오퍼는 '현재 OVR vs 팀 평균' 마진으로만 걸러졌다 — 그런데
        # 17~18세 신인이 명문 아카데미에 스카우트되는 건 '지금 얼마나 잘하는지'
        # 가 아니라 '재능 상한(잠재력, talent_cap)'을 보고 데려가는 것이다
        # (손흥민 함부르크 유스, 이강인 발렌시아 유스처럼 실제로도 그렇다).
        # 낮은 확률·엘리트급 이상 재능 한정으로, 재능상한 기준 마진을 통과한
        # 유스 스카우트 오퍼를 하나 추가한다 — 절대다수는 여전히 못 받고
        # (실제로도 극소수만 해당), 받으면 눈에 띄는 이벤트가 된다.
        _ys_offer = _try_youth_scout_offer(c, p, ovr, offers, _team_avg_cache_offers, my_country_id)
        if _ys_offer:
            offers.append(_ys_offer)

    elif not has_team:
        # ── [2026-07 재설계 v2, 신민용 최종안] 17세 이후 계약종료/방출 등으로
        #    소속이 사라져 '오퍼'가 아닌 '팀 입단'으로 새 팀을 찾는 경우.
        #    has_team(이적 오퍼) 분기가 '현재 리그 국가'를 기준으로 삼는 것과
        #    같은 원리 — 무소속 분기는 '현재'가 없으니 career_entries의 가장
        #    마지막 기록(계약 만료 직전 소속) 리그 국가를 그 자리에 쓴다.
        #    직전 리그가 해외였으면: 직전리그 8 + 자국 3 + 기타 4 = 15
        #    직전 리그가 자국이었으면(또는 커리어 기록 없음): 자국 10 + 기타 5 = 15
        #    "무소속은 계약 중보다 제안이 확실히 많아야 자유계약 시장에
        #    나온 느낌이 산다"는 설계 원칙.
        _prev_country_id = _last_played_country_id(p)
        if _prev_country_id and _prev_country_id != my_country_id:
            PREV_LEAGUE_COUNT = NO_TEAM_PREV_LEAGUE
            HOMETOWN_COUNT    = NO_TEAM_HOMETOWN
            OTHER_FOREIGN_COUNT = NO_TEAM_OTHER_FOREIGN + _bonus
        else:
            _prev_country_id = None  # 자국과 같으면 굳이 따로 안 나눔
            PREV_LEAGUE_COUNT = 0
            HOMETOWN_COUNT    = NO_TEAM_DOMESTIC_SAME
            OTHER_FOREIGN_COUNT = NO_TEAM_FOREIGN_SAME + _bonus

        _seen_ids = {o["team_id"] for o in offers}
        _prev_group = _fill_country_pool(_prev_country_id, PREV_LEAGUE_COUNT, _seen_ids) \
            if PREV_LEAGUE_COUNT else []
        # [2026-07 신설] 자국(고향) 슬롯도 보유 국적이 여러 개면 국적마다
        # 1개씩 먼저 보장한다(_fill_home_pool_multi).
        _home_group = _fill_home_pool_multi(HOMETOWN_COUNT, _seen_ids) if HOMETOWN_COUNT else []
        _exclude_countries = {cid for cid in ([_prev_country_id] + _my_nat_ids) if cid}
        _for_group = _fill_foreign_pool(OTHER_FOREIGN_COUNT, _exclude_countries, _seen_ids)

        for o in _prev_group: o["_zone"] = "prev_league"
        for o in _home_group: o["_zone"] = "domestic" if not PREV_LEAGUE_COUNT else "hometown"
        for o in _for_group:  o["_zone"] = "foreign"
        offers = _interleave(_prev_group, _home_group, _for_group)
    else:
        # [2026-07 재설계, 신민용 요청] 일반 이적 오퍼(계약 중, 이적시즌 트리거는
        # 기존 호출부(_auto_offer_shown/_offer_probability) 그대로 유지) — 예전엔
        # "현재 리그 국가 1~2개 + (35세 이상이면) 자국 1개 + 나머지 완전 무작위"
        # 였는데, 이제 첫 입단/무소속 재취업과 같은 3-분류 패턴으로 통일한다:
        #   현재 뛰는 리그의 국가 4 + 고향(대표국적) 3 + 그 외 타국 5 = 12
        #   (이적요청 중이면 타국에 보너스 +2 → 14).
        cur_league_id = p.get("current_league_id", 0)
        league_country_id = None
        if cur_league_id:
            row_lg = c.execute(
                "SELECT country_id FROM leagues WHERE id=?", (cur_league_id,)
            ).fetchone()
            if row_lg:
                league_country_id = row_lg["country_id"]
        if not league_country_id:
            league_country_id = my_country_id

        _seen_ids = {o["team_id"] for o in offers}
        _league_group = _fill_country_pool(league_country_id, HAS_TEAM_LEAGUE_COUNTRY, _seen_ids)
        # 고향(대표국적)이 현재 리그 국가와 같으면(외국인이 아니라 자국 리그에서
        # 뛰는 경우) 굳이 같은 나라를 두 번 채울 필요 없이 그만큼 타국으로 돌린다.
        _home_target = HAS_TEAM_HOMETOWN if my_country_id != league_country_id else 0
        # [2026-07 신설] 보유 국적이 여러 개면 국적마다 1개씩 먼저 보장.
        _home_group = _fill_home_pool_multi(_home_target, _seen_ids) if _home_target else []
        _exclude_countries = {cid for cid in ([league_country_id] + _my_nat_ids) if cid}
        _foreign_target = HAS_TEAM_FOREIGN + (HAS_TEAM_HOMETOWN - len(_home_group) if not _home_target else 0)
        _for_group = _fill_foreign_pool(_foreign_target, _exclude_countries, _seen_ids)

        for o in _league_group: o["_zone"] = "domestic"      # 현재 리그 국가
        for o in _home_group:   o["_zone"] = "hometown"
        for o in _for_group:    o["_zone"] = "foreign"
        offers = _interleave(_league_group, _home_group, _for_group)

    # [17세 첫 입단 안전망] 협상을 모두 실패해도 입단할 곳이 사라지지 않도록,
    #   생성된 오퍼 중 연봉(=등급·티어와 직결)이 가장 낮은 1곳은 항상 입단 가능하게 표시.
    #   협상 자체는 그대로 가능(성공 시 연봉 인상), 실패해도 '결렬'로 막히지 않을 뿐.
    if not has_team and offers:
        safe_offer = min(offers, key=lambda o: o["salary"])
        safe_offer["safe"] = True

    # 오퍼에 뜬 팀들의 리그만 일정 생성 + AI 시뮬 후 rank_info 반영
    st = get_state()
    cur_week = st["current_week"] if st else 1
    offer_league_ids = list({o["league_id"] for o in offers})

    from constants import SEASON_PHASES as _SP2
    _preseason_end = _SP2["preseason1"][1]     # 3
    _league_end_wk2 = _SP2["second_half"][1]   # 43
    if cur_week > _preseason_end:
        for lid in offer_league_ids:
            if cur_week > _league_end_wk2:
                # 시즌 종료 후: '작년 성적'이 풀 시즌이 되도록 전체 일정 생성
                generate_season_schedule(lid, st["current_season"], st["current_year"])
            else:
                # 시즌 중: 상반기 일정만 (입단 후 경기 일정 영향 방지)
                _generate_first_half_schedule(lid, st["current_season"], st["current_year"])
            _sim_league_full(lid, st["current_season"], exclude_team_id=p.get("current_team_id"))
    else:  # 이슈5: 프리시즌은 작년 시즌(prev_season) 결과로 rank_info 계산
        prev_season = st["current_season"] - 1 if st["current_season"] > 1 else None
        if prev_season:
            for lid in offer_league_ids:
                # 작년 시즌은 끝난 시즌이므로 전체 일정 생성 + 풀 시뮬
                generate_season_schedule(lid, prev_season, st["current_year"] - 1)
                _sim_league_full(lid, prev_season, exclude_team_id=p.get("current_team_id"))

    # [최적화] season_state를 1회 조회 후 _get_team_rank_info에 주입 (기존: 오퍼마다 SELECT)
    _ss_for_rank = conn.execute(
        "SELECT current_week, current_season FROM season_state WHERE id=1").fetchone()
    for offer in offers:
        offer["rank_info"] = _get_team_rank_info(conn.cursor(), offer["team_id"],
                                                  ss=_ss_for_rank)

    # [2026-08 신설, 신민용 확정: 시장 경쟁 효과] 이번 오퍼 배치에 이적료가
    # 걸린(유료 이적) 제안이 여러 개 동시에 잡히면 "여러 구단이 동시에
    # 노린다"는 뜻이다. evaluate_offer_decision()엔 원래 competing_offer_count
    # 파라미터가 있었지만 join_team()이 항상 기본값(1)만 넘겨서 사실상 죽은
    # 파라미터였다 — 이제 이번 배치의 실제 유료 이적 제안 수를 세서 각
    # 오퍼에 채워 넣는다(구단 최소요구 판정에 실제로 반영됨). 동시에 매수팀
    # 쪽 제안액 자체에도 소폭 프리미엄을 더한다 — 구단만 더 완고해지고
    # 매수팀 제안은 그대로면, 여러 팀이 몰릴수록 오히려 아무도 문턱을 못
    # 넘어 이적이 더 안 되는 역설이 생기기 때문.
    _paid_offers = [o for o in offers if o.get("transfer_fee", 0) > 0]
    _n_paid = len(_paid_offers)
    if _n_paid >= 2:
        _heat_mult = min(1.35, 1.0 + 0.08 * (_n_paid - 1))
        for _o in _paid_offers:
            _o["transfer_fee"] = int(_o["transfer_fee"] * _heat_mult)
    for offer in offers:
        offer["competing_offer_count"] = _n_paid

    conn.close()

    # [2026-08 신설, 신민용 확정] 협상 결렬 냉각기가 안 끝난 팀은 이번
    # 배치에서 제외한다. 다른 후보로 자리를 다시 채우진 않는다(그 팀 몫만큼
    # 이번엔 오퍼가 하나 적게 뜰 수 있음 — 냉각기 있는 팀이 스킵된다는
    # 자연스러운 결과라 별도 백필은 하지 않는다).
    if offers:
        _blocked = _blocked_offer_team_ids(p.get("current_year", 0))
        if _blocked:
            offers = [o for o in offers if o["team_id"] not in _blocked]

    # [기능3] 이적 요청 플래그 소비 (오퍼가 생성됐으면 리셋)
    if transfer_req and offers:
        update_player(transfer_requested=0)

    return offers


# ═══════════════════════════════════════════════════════════════
# [2026-07 신설] 직접 지원 — 내가 팀을 검색해서 골라 지원하는 능동적 채널.
# 패시브 오퍼(generate_offers)와 별개 — 무소속(첫 입단/계약종료·방출 후)
# 기간에만 가능하고, 시도 횟수가 제한된다(DIRECT_APPLY_MAX).
#
# 판정 공식(신민용과 합의한 설계):
#   유효격차 = (내 OVR - 팀평균) + 등급별 마진 + 에이전트보정 + 폼보정
#              + 나이보정 + 재능보정
#   성공확률 = sigmoid(유효격차 / 8), 3~95%로 clamp
#   단, 재능(talent_cap)이 그 등급 하한(TALENT_GATE_MIN_BY_GRADE)에 못 미치면
#   다른 보정과 무관하게 사실상 불가능(0.5%)으로 고정.
# ═══════════════════════════════════════════════════════════════

DIRECT_APPLY_MAX = 4

# 재능 하한선 — 이 등급에 못 미치는 talent_cap이면 그 리그 등급 지원은
# 사실상 불가능(다른 보정 무시). SS/S=엘리트 이상(88), A=프로 이상(78),
# B=세미프로 이상(69), C 이하는 사실상 누구나 도전 가능.
TALENT_GATE_MIN_BY_GRADE = {"SS": 88, "S": 88, "A": 78, "B": 69, "C": 60, "D": 0, "E": 0, "F": 0}
_GATE_GRADE_ORDER = ["SS", "S", "A", "B", "C", "D", "E", "F"]


def _gate_grade_for_tier(country_grade: str, tier: int) -> str:
    """[버그수정 2026-07] TALENT_GATE_MIN_BY_GRADE가 나라 등급만 보고 그
    나라 안에서 몇 부 리그인지는 전혀 안 봐서, 일본(A급) 5부(평균OVR
    40대)에 지원해도 일본 1부에 도전하는 것과 똑같은 '재능 78 이상'
    기준이 적용되는 버그가 있었다(신민용 리포트: OVR57 선수가 평균
    OVR 40대 팀에 지원하는데 '재능 부족'으로 막힘). 나라 등급은
    최상위 리그(1부) 기준이므로, 부수가 내려갈수록 실제 요구 수준도
    낮아지는 게 맞다 — 부수 1당 등급을 한 단계씩 완화한다.
    예: 일본(A) 1부→A(78), 2부→B(69), 3부→C(60), 4부 이상→D 이하(0)."""
    idx = _GATE_GRADE_ORDER.index(country_grade) if country_grade in _GATE_GRADE_ORDER else len(_GATE_GRADE_ORDER) - 1
    idx = min(len(_GATE_GRADE_ORDER) - 1, idx + max(0, tier - 1))
    return _GATE_GRADE_ORDER[idx]

# [2026-08 신설, 신민용 리포트: "직접 지원이 팀 입단보다 헐렁하면 안 된다"]
# agent_mod+form_mod+pop_mod+fame_mod 네 개를 다 더하면 이론상 최대
# 약 44(에이전트8 + 폼·수상16 + 인기8.1 + 명성12.15)까지 나올 수 있어서,
# 40세에 OVR가 크게 떨어진 선수도 이 네 개만으로 큰 OVR 격차+나이 페널티를
# 통째로 뒤집어 1부 팀에 "유력"이 뜨는 사례가 있었다. 패시브 오퍼가 같은
# 인기도/명성을 반영할 때 합산 상한을 4.0으로 두는 것(_pop_fame_bonus)보단
# 넉넉하게(에이전트/폼/수상 경력이 진짜 좋으면 그래도 유리해야 하므로),
# 그러나 무제한은 아니게 이 네 보정치 합계에 상한을 둔다.
REPUTATION_BONUS_CAP = 14.0

AGENT_APPLY_MOD = {"S": 8, "A": 6, "B": 4, "C": 2, "D": 0, "E": -2, "F": -4}

# 직전 시즌 평균평점 보정.
_APPLY_FORM_RATING_MOD = [(7.5, 6), (7.0, 4), (6.5, 2)]

# 직전 수상 보정(최고 하나만 적용).
_APPLY_AWARD_MOD = {"발롱도르": 10, "MVP": 8, "득점왕": 6, "도움왕": 6, "베스트11": 4,
                    "올해의 수비수": 4, "구단 올해의 선수": 2}

# 일반 나이 보정(재능과 무관 — 너무 어리거나 너무 노쇠하면 리스크로 소폭 페널티).
def _apply_age_mod(age: int) -> float:
    if age <= 20: return -2
    if age <= 29: return 0
    if age <= 32: return -2
    if age <= 35: return -5
    return -9


def _apply_recent_form_mod(p) -> float:
    """직전 완료된 소속(career_entries 최신 1건) 평균평점 + 최근 수상 보정."""
    conn = get_conn()
    row = conn.execute(
        "SELECT avg_rating FROM career_entries WHERE end_year>0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    rating_mod = 0.0
    if row and row["avg_rating"]:
        r = row["avg_rating"]
        for threshold, mod in _APPLY_FORM_RATING_MOD:
            if r >= threshold:
                rating_mod = mod
                break
        else:
            if r < 6.0:
                rating_mod = -3
    award_row = conn.execute(
        "SELECT award_type FROM awards WHERE is_mine=1 ORDER BY year DESC, id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    award_mod = _APPLY_AWARD_MOD.get(award_row["award_type"], 0) if award_row else 0
    return rating_mod + award_mod


def _apply_talent_bonus(p) -> float:
    """[2026-07 수정] 나이가중치를 고정 구간 대신 이 선수의 peak_age 기준으로
    계산한다 — peak_age 이상이면(이미 전성기 지나 OVR이 떨어지기 시작하는
    나이) 잠재력 보너스는 0(현재 OVR이 곧 실력). peak_age 전이면 남은
    햇수에 비례해서 최대 0.5까지."""
    talent_cap = p.get("talent_cap", 88)
    my_ovr = p.get("ovr", 40)
    age = p.get("age", 20)
    peak_age = p.get("peak_age", 25)
    if age >= peak_age:
        youth_weight = 0.0
    else:
        years_left = peak_age - age
        youth_weight = min(0.5, years_left * 0.05)
    return max(0.0, talent_cap - my_ovr) * youth_weight


def get_apply_attempts_left(p=None) -> int:
    """직접 지원 남은 횟수. 소속 팀이 있으면(계약 중) 0 — 그 상태에선 직접
    지원 자체가 불가능하다."""
    p = p or get_player()
    if not p or p.get("current_team_id"):
        return 0
    return max(0, DIRECT_APPLY_MAX - p.get("apply_attempts_used", 0))


# [2026-07 신설, 신민용 지적: "뭐도 없는 애를 강팀이 뽑을 이유가 없잖아"]
# [2026-07 재설계, 신민용 설계+확정] 인기도(popularity)="최근 화제성"과
# 명성(fame)="커리어에 새겨진 업적"을 분리해서 각각 반영한다. 명성이 더
# 강하게 작용하도록(챔스 우승 경력이 최근 리그 골보다 이적시장에서 더
# 신뢰를 준다) 가중치를 다르게 잡는다.
# [2026-07 재조정, 신민용 지적: "인기도/명성이 둘 다 100 근처면 OVR보다
# 영향력이 커질 수 있다 — 선형보다 완만한 곡선을 추천"] 0~30 빠르게,
# 30~60 보통, 60~100 완만하게 증가하는 3구간 누진곡선으로 바꾼다 — 초반
# (무명 탈출)엔 반응이 빠르고, 이미 충분히 유명해진 뒤(80→100)엔 한계
# 효용이 줄어 OVR 같은 실력 축을 압도하지 않게 한다.
def _diminishing_curve(value: float, k1: float = 0.15, k2: float = 0.08, k3: float = 0.03) -> float:
    """0~30(k1)/30~60(k2)/60~100(k3) 구간별 기울기가 줄어드는 누진곡선."""
    v = max(0.0, min(100.0, value))
    if v <= 30:
        return k1 * v
    if v <= 60:
        return k1 * 30 + k2 * (v - 30)
    return k1 * 30 + k2 * 30 + k3 * (v - 60)


def _apply_pop_mod(pop: float) -> float:
    # pop=30 -> 4.5 / pop=80 -> 7.5 / pop=100 -> 8.1 (100 근처는 거의 안 늘어남)
    return round(_diminishing_curve(pop), 1)


def _apply_fame_mod(fame: float) -> float:
    # 인기도와 같은 곡선에 1.5배 — fame=80 -> 11.25 / fame=100 -> 12.15
    return round(_diminishing_curve(fame) * 1.5, 1)


# [2026-07 신설, 신민용 지적: "뭐도 없는 애를 강팀이 뽑을 이유가 없잖아"]
# 직접 지원은 무소속(현재 소속 팀 없음) 상태에서만 가능해서 "현재 리그"가
# 없다 — 대신 가장 최근에 뛰었던 소속팀의 리그를 '내가 원래 있던 물'로
# 보고, 거기서 목표 팀 리그까지 등급을 얼마나 뛰어넘으려는지를 잰다.
# OVR/재능 게이트를 다 통과해도, 하위리그에서 갑자기 SS급으로 직행 지원을
# 넣는 건 "스카우트 네트워크가 그렇게까지 안 닿는다"는 현실을 반영해 추가
# 페널티를 준다. 프로 경력이 아예 없는 신인은 최하위(F급) 취급 — 무명
# 신인이 곧장 명문팀에 지원하는 걸 비현실적으로 보는 게 원칙이다.
def _apply_reference_league_grade_tier(p):
    conn = get_conn()
    row = conn.execute(
        """SELECT ce.tier AS tier, cn.grade AS cgrade, cn.name AS cname
           FROM career_entries ce
           JOIN teams t ON ce.team_id = t.id
           JOIN leagues l ON t.league_id = l.id
           JOIN countries cn ON l.country_id = cn.id
           WHERE ce.team_id > 0
           ORDER BY ce.id DESC LIMIT 1"""
    ).fetchone()
    conn.close()
    if not row:
        return "F", 1   # 프로 경력 전무(신인) — 최하위 리그 취급
    grade = get_league_grade(row["cname"], row["cgrade"])
    return grade, row["tier"] or 1


# 리그 등급을 몇 단계나 뛰어넘어 지원하는지에 따른 페널티. 0단계(같거나
# 낮은 등급)는 페널티 없음 — 오히려 여유 있게 성공해야 정상이므로 손대지
# 않는다. 위로 갈수록(SS 쪽) 한 단계당 페널티가 커진다(막판 1~2단계는
# 이미 TALENT_GATE_MIN_BY_GRADE가 사실상 막아주므로 3단계 이상은 극단값).
_TIER_JUMP_PENALTY = {0: 0, 1: -3, 2: -8, 3: -15}

# [2026-08 신설, 신민용 리포트: "나이 40에 OVR64인 선수가 팀 입단(패시브
# 오퍼)에서는 K리그 3~5부만 뜨는데, 직접 지원으로 K리그 1부를 검색하면
# 오히려 '유력'/'가능성 있음'이 뜬다 — 직접 지원은 무조건 팀 입단보다
# 엄격해야 하지 않나?"] 실측해보니 원인은 위 jump_penalty가 "직전 소속
# 리그 등급" 기준이라, 이 선수처럼 원래 1부에서 뛰다가 노쇠해서 방출된
# 경우엔 ref_grade가 여전히 1부(A급)라 jump=0(페널티 없음)으로 계산됐다
# — "어느 리그 소속이었냐"만 보고 "지금 실력이 그 리그 수준이냐"는 안
# 본 것. 반면 패시브 오퍼(_suitable_grades)는 순수하게 "지금 OVR"만
# 보고 등급대를 정하므로, 이 경우 자연스럽게 하위 등급만 뜬다 — 같은
# 선수인데 두 시스템이 서로 다른 기준(직전 리그 vs 현재 OVR)을 써서
# 결과가 크게 어긋난 것.
# 그래서 "직전 리그 대비 점프"와는 별개로 "지금 OVR 대비 점프"도 똑같은
# 페널티 표로 계산해서 추가한다 — 둘 중 하나라도 크게 벌어지면(과거엔
# 좋았지만 지금은 아니거나, 원래도 그 등급이 아니었거나) 확률이 떨어져야
# "직접 지원이 팀 입단보다 절대 헐렁해지지 않는다"는 원칙이 지켜진다.
def _ovr_natural_best_grade_idx(my_ovr, agent_grade):
    """지금 OVR+에이전트만으로 자연스러운 등급대(_suitable_grades — 패시브
    오퍼가 실제로 쓰는 것과 동일한 기준 함수)의 최상위(가장 좋은) 등급
    인덱스를 반환한다(_GATE_GRADE_ORDER 기준, SS=0이 가장 좋음)."""
    grades = _suitable_grades(my_ovr, agent_grade)
    idxs = [_GATE_GRADE_ORDER.index(g) for g in grades if g in _GATE_GRADE_ORDER]
    return min(idxs) if idxs else len(_GATE_GRADE_ORDER) - 1

# [2026-07 신설, 신민용 확정: "에이전트는 성공률을 올리는 게 아니라 불이익을
# 줄여주는 역할"] 에이전트 등급이 위 리그 점프 페널티를 등급별 비율만큼
# 상쇄한다 — "기록(인기도/명성)이 없어도 좋은 에이전트의 연줄로 눈에는
# 띌 수 있다"는 의도. 다만 70% 이상처럼 너무 강하게 주면 "무기록+S급
# 에이전트+OVR만 맞음"으로 빅클럽에 쉽게 들어가버리는 부작용이 있어
# S=75%를 상한으로 두고 등급별로 15%p 안팎씩 계단식으로 낮춘다.
AGENT_JUMP_OFFSET = {"S": 0.75, "A": 0.60, "B": 0.45, "C": 0.30, "D": 0.20, "E": 0.10, "F": 0.0}


def get_apply_player_context(p=None):
    """[2026-07 신설, 성능] 직접 지원 확률 계산 중 '팀과 무관한' 부분(에이전트/
    폼/나이/재능/인기도/명성/직전리그/국적)은 검색 결과가 몇 팀이든 플레이어
    1명당 값이 동일하므로, 검색 결과 목록 전체에 대해 반복 계산하지 않도록
    1회만 뽑아 재사용한다 (팀 검색 UI가 60건까지 한 화면에 확률을 다
    보여주는데, 매 행마다 career_entries/awards 쿼리를 새로 날리면 검색할
    때마다 체감 렉이 생김).
    반환: dict(my_ovr, talent_cap, agent_mod, agent_grade, form_mod, age_mod,
    talent_mod, pop_mod, fame_mod, ref_grade_idx, ovr_best_grade_idx, my_continent)."""
    p = p or get_player()
    if not p:
        return None
    ref_grade, ref_tier = _apply_reference_league_grade_tier(p)
    ref_gate_grade = _gate_grade_for_tier(ref_grade, ref_tier)
    agent_grade = p.get("agent_grade", "F")
    from constants import get_country_continent
    return {
        "my_ovr": p.get("ovr", 40),
        "talent_cap": p.get("talent_cap", 88),
        "agent_mod": AGENT_APPLY_MOD.get(agent_grade, 0),
        "agent_jump_offset": AGENT_JUMP_OFFSET.get(agent_grade, 0.0),
        "form_mod": _apply_recent_form_mod(p),
        "age_mod": _apply_age_mod(p.get("age", 20)),
        "talent_mod": _apply_talent_bonus(p),
        "pop_mod": _apply_pop_mod(p.get("popularity", 0)),
        "fame_mod": _apply_fame_mod(p.get("fame", 0)),
        "ref_grade_idx": _GATE_GRADE_ORDER.index(ref_gate_grade),
        # [2026-08 신설] "직전 소속 리그가 몇 부였냐"만으로는 놓치는 경우
        # (원래 1부에서 뛰다가 노쇠해서 방출된 선수 등)를 잡기 위한 두
        # 번째 기준 — "지금 OVR로 자연스러운 등급대가 어디까지냐"
        # (_suitable_grades, 패시브 오퍼와 동일 기준)도 같이 들고 있는다.
        "ovr_best_grade_idx": _ovr_natural_best_grade_idx(p.get("ovr", 40), agent_grade),
        "my_continent": get_country_continent(p.get("nationality", "")),
    }


def calc_apply_prob_with_context(team_id, ctx):
    """get_apply_player_context()로 미리 뽑은 컨텍스트를 재사용해 팀 하나에
    대한 확률만 계산 — 검색 결과 목록을 순회할 때 이걸 쓴다."""
    if ctx is None:
        return 0.0, True
    from constants import get_league_relative_margin
    conn = get_conn()
    row = conn.execute(
        """SELECT l.id as lid, l.tier, cn.name as country, cn.grade as cgrade, cn.continent as continent
           FROM teams t JOIN leagues l ON t.league_id=l.id
           JOIN countries cn ON l.country_id=cn.id WHERE t.id=?""", (team_id,)).fetchone()
    if not row:
        conn.close()
        return 0.0, True
    grade = get_league_grade(row["country"], row["cgrade"])
    team_avg_row = conn.execute(
        "SELECT AVG(ovr) as v FROM ai_players WHERE team_id=?", (team_id,)).fetchone()
    team_avg = team_avg_row["v"] if team_avg_row and team_avg_row["v"] else 50

    # [밸런스 재설계 2026-07] 마진도 오퍼/입단과 동일하게 "국가 등급"이 아니라
    # "그 팀이 자기 리그 안에서 몇 %냐"로 통일 — 같은 기준으로 평가돼야
    # 직접 지원만 유독 헐렁해지는 불일치가 없어진다.
    peer_rows = conn.execute(
        """SELECT t.id as tid, AVG(ai.ovr) as avg_ovr FROM teams t
           JOIN ai_players ai ON ai.team_id = t.id
           WHERE t.league_id=? GROUP BY t.id""", (row["lid"],)).fetchall()
    conn.close()
    peers = [r["avg_ovr"] for r in peer_rows if r["avg_ovr"] is not None]
    if len(peers) >= 2 and team_avg is not None:
        peers.sort(reverse=True)
        rank = sum(1 for v in peers if v >= team_avg)
        pct = rank / len(peers)
        margin = get_league_relative_margin(pct)
    else:
        margin = CLUB_JOIN_MARGIN_BY_GRADE.get(grade, CLUB_JOIN_MARGIN)

    # [버그수정] 부수(tier)를 반영한 완화된 등급으로 게이트 판정 — 같은
    # 나라라도 하위 리그는 실제 요구 재능이 낮다.
    gate_grade = _gate_grade_for_tier(grade, row["tier"])
    gate = TALENT_GATE_MIN_BY_GRADE.get(gate_grade, 0)
    if ctx["talent_cap"] < gate:
        return 0.005, True   # 재능 미달 — 사실상 불가능

    # [2026-07 신설] 등급 점프 페널티 — ref_grade_idx가 target_idx보다
    # 작을수록(더 좋은 등급 쪽, _GATE_GRADE_ORDER는 SS=0 순) 더 큰 도약.
    # 에이전트 등급별 상쇄 비율(agent_jump_offset)만큼 페널티를 깎아준다
    # ("에이전트는 불이익을 줄여주는 역할"이라는 설계 원칙).
    target_idx = _GATE_GRADE_ORDER.index(gate_grade)
    jump = ctx["ref_grade_idx"] - target_idx
    jump_penalty = _TIER_JUMP_PENALTY.get(max(0, jump), -22 if jump > 0 else 0)
    jump_penalty *= (1.0 - ctx["agent_jump_offset"])

    # [2026-08 신설, 신민용 리포트: "나이 40 OVR64 선수가 팀 입단에선
    # K리그 3~5부만 뜨는데 직접 지원으로 1부를 찾으면 오히려 유력하게
    # 뜬다 — 직접 지원이 팀 입단보다 헐렁하면 안 되는 거 아니냐"] 위
    # jump_penalty는 "직전 소속 리그"만 보는데, 원래 1부에서 뛰다가
    # 노쇠해서 방출된 선수는 ref_grade가 여전히 1부라 이 페널티가 0으로
    # 나온다 — "지금 실력이 그 등급에 안 맞아졌다"는 걸 놓친다. 패시브
    # 오퍼가 쓰는 것과 동일한 "지금 OVR로 자연스러운 등급대"
    # (ovr_best_grade_idx) 기준으로도 똑같은 페널티 표를 한 번 더 적용해서,
    # 두 기준 중 하나라도 크게 벌어지면 확률이 떨어지게 한다 — 직접
    # 지원이 팀 입단보다 절대 더 헐렁해지지 않는다는 원칙을 지키기 위함.
    ovr_jump = ctx["ovr_best_grade_idx"] - target_idx
    ovr_jump_penalty = _TIER_JUMP_PENALTY.get(max(0, ovr_jump), -22 if ovr_jump > 0 else 0)
    ovr_jump_penalty *= (1.0 - ctx["agent_jump_offset"])

    # [2026-07 신설, 신민용 설계+확정: "국가 명성이 아니라 국적에 따른
    # 시장 접근성"] 출신 대륙 -> 목적 대륙 이적 난이도. 한국 선수가 K리그
    # 에서 아무리 잘해도 EPL/프랑스로 바로 가는 게 드문 것처럼, 실력과
    # 별개로 스카우트망/시장 연결성 자체가 다르다는 걸 반영한다.
    from constants import transfer_region_mod
    region_mod = transfer_region_mod(ctx["my_continent"], row["continent"])

    # [2026-08 신설, 신민용 리포트 (계속): "직접 지원이 팀 입단보다 헐렁하면
    # 안 된다"] ovr_jump_penalty를 추가해도, 에이전트 등급이 좋으면
    # _suitable_grades 자체가 이미 등급대를 넓게 잡아줘서(예: S급 에이전트는
    # +3단계) 이 페널티가 0으로 나올 수 있다 — 그 경우 진짜 원인은
    # 여기(agent_mod+form_mod+pop_mod+fame_mod)가 개별로는 각각 크지 않아
    # 보여도 다 더하면(최대 약 44) 큰 OVR 격차+나이 페널티(-9~-24 정도)를
    # 통째로 뒤집을 만큼 커진다는 것 — 패시브 오퍼는 같은 인기도/명성을
    # 반영할 때도 합산 상한을 4.0으로 두는데(_pop_fame_bonus) 반해, 이쪽은
    # 상한이 아예 없었다. "직접 지원은 팀 입단보다 좀 더 대담한 시도를
    # 허용하되, 그 폭 자체엔 상한이 있어야 한다"는 원칙으로 네 보정치의
    # 합에 상한(REPUTATION_BONUS_CAP)을 둔다 — 패시브의 +4보다는 넉넉하게
    # (에이전트/폼/수상 경력이 진짜 좋은 선수는 그래도 남들보단 유리해야
    # 하므로) 두되, 무제한으로 큰 격차를 통째로 뒤집을 순 없게 막는다.
    rep_bonus = ctx["agent_mod"] + ctx["form_mod"] + ctx["pop_mod"] + ctx["fame_mod"]
    rep_bonus = max(-20.0, min(REPUTATION_BONUS_CAP, rep_bonus))

    gap = ctx["my_ovr"] - team_avg
    eff = (gap + margin + rep_bonus + ctx["age_mod"]
           + ctx["talent_mod"]
           + jump_penalty + ovr_jump_penalty + region_mod)
    prob = 1.0 / (1.0 + math.exp(-eff / 8.0)) if abs(eff) < 700 else (1.0 if eff > 0 else 0.0)
    prob = max(0.03, min(0.95, prob))
    return prob, False





def calc_apply_success_prob(team_id):
    """직접 지원 성공 확률 계산 (팀 1개만 볼 때 편의용). 여러 팀을 한꺼번에
    볼 때(검색 결과 목록)는 get_apply_player_context() + calc_apply_prob_with_context()
    조합을 써서 플레이어측 계산을 반복하지 않는 편이 낫다.
    반환: (prob: float, blocked: bool)."""
    ctx = get_apply_player_context()
    return calc_apply_prob_with_context(team_id, ctx)


def save_pending_offer_state(kind, title, force_select, grid, apply_slots,
                              offers, offer_salaries, neg_used, neg_failed,
                              applied_count, offer_years=None, years_used=None,
                              years_failed=None, years_target=None):
    """[2026-07 신설, 신민용 요청] 오퍼/입단 창(OfferWindow) 상태를 통째로
    JSON으로 저장한다 — 결정을 내리기 전에 껐다 켜도 새로 랜덤 생성하지
    않고 이 값을 그대로 복원해서 같은 오퍼 목록을 다시 보여주기 위함
    (재접속으로 오퍼를 리롤하는 걸 막는 게 목적). kind는 "join"(무소속
    강제 입단)/"auto_offer"(소속 있을 때 자동 오퍼) 중 하나.
    [2026-08 확장] offer_years/years_used/years_failed/years_target —
    연봉 협상과 독립된 '기간 협상' 상태(years_target은 플레이어가 콤보
    박스로 직접 고른 희망 계약기간). None이면(구버전 호출부 호환) 빈
    값으로 저장한다."""
    state = {
        "kind": kind, "title": title, "force_select": bool(force_select),
        "grid": bool(grid), "apply_slots": apply_slots,
        "offers": offers, "offer_salaries": offer_salaries,
        "neg_used": {str(k): v for k, v in neg_used.items()},
        "neg_failed": list(neg_failed), "applied_count": applied_count,
        "offer_years": offer_years or [],
        "years_used": {str(k): v for k, v in (years_used or {}).items()},
        "years_failed": list(years_failed or []),
        "years_target": {str(k): v for k, v in (years_target or {}).items()},
    }
    try:
        update_player(pending_offer_state=json.dumps(state, ensure_ascii=False))
    except (TypeError, ValueError):
        pass  # 직렬화 실패해도 게임 진행 자체는 막지 않는다.


def load_pending_offer_state(kind=None):
    """저장된 오퍼 상태를 불러온다. kind를 주면 그 종류일 때만 반환하고,
    아니면(다른 종류거나 파싱 실패) None — 호출부는 새로 생성하는 기존
    경로로 자연스럽게 폴백한다."""
    p = get_player()
    if not p:
        return None
    raw = p.get("pending_offer_state") or ""
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if kind and state.get("kind") != kind:
        return None
    return state


def clear_pending_offer_state():
    """결정(입단 완료/전부 결렬로 인한 보류 등)이 나면 저장된 오퍼 상태를
    비운다 — 다음에 오퍼/입단 창을 열 때는 다시 새로 생성된다."""
    update_player(pending_offer_state="")


def apply_to_team(team_id):
    """직접 지원 실행 — 시도 1회 소진(성공/실패 무관), 성공하면 그 팀
    오퍼 dict를, 실패하면 None을 반환. 반환: (success, prob, offer)."""
    p = get_player()
    if not p or p.get("current_team_id"):
        return False, 0.0, None
    if get_apply_attempts_left(p) <= 0:
        return False, 0.0, None

    prob, blocked = calc_apply_success_prob(team_id)
    update_player(apply_attempts_used=p.get("apply_attempts_used", 0) + 1)

    if random.random() >= prob:
        return False, prob, None

    conn = get_conn()
    row = conn.execute(
        """SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                  cn.name as country,cn.flag,cn.grade
           FROM teams t JOIN leagues l ON t.league_id=l.id
           JOIN countries cn ON l.country_id=cn.id WHERE t.id=?""", (team_id,)).fetchone()
    conn.close()
    if not row:
        return False, prob, None

    grade = get_league_grade(row["country"], row["grade"])
    tier = row["tier"]
    ovr = p.get("ovr", 40)
    salary = int(_calc_salary(grade, tier, ovr, row["country"], row["name"], year=p.get("current_year"), team_id=row["id"], talent_tier=p.get("talent_tier")) * random.uniform(0.95, 1.15))
    offer = _build_offer(row, grade, tier, salary, join_prob=prob)
    offer["_zone"] = "applied"
    _rank_conn = get_conn()
    offer["rank_info"] = _get_team_rank_info(_rank_conn.cursor(), team_id)
    _rank_conn.close()
    return True, prob, offer



def _calc_contract_years(age: int, tier: int, country: str = None) -> int:
    """[2026-07 밸런스 조정] 계약 기간이 나이/티어에만 좌우되고 나라(리그 재정
    수준)는 전혀 반영하지 않고 있었다 — EPL이든 최빈국 변방 리그든 유망주
    계약 길이 분포가 완전히 동일했다(신민용 지적). 재정이 넉넉한 리그일수록
    유망주를 장기계약으로 붙잡아두려는 유인이 크고, 반대로 재정이 빠듯한
    나라는 방출/이탈 유연성을 위해 단기계약을 선호하는 경향을 반영한다.
    country를 안 넘기면(기존 호출부 호환) 완전히 기존과 동일하게 동작한다."""
    if age <= 22:   base = random.choices([3,4,5], weights=[20,40,40])[0]
    elif age <= 29: base = random.choices([3,4],   weights=[40,60])[0]
    elif age <= 32: base = random.choices([1,2],   weights=[40,60])[0]
    else:           base = 1
    if tier == 1 and age >= 28: base = max(1, base - 1)

    if country and tier == 1:
        from constants import get_country_salary_mult
        wealth = get_country_salary_mult(country)   # 대략 0.05(최빈국)~1.0(유럽 최상위)
        if wealth < 0.3 and age <= 29:
            # 재정 열악한 1부 리그 — 장기계약 리스크를 피해 한 해 짧게
            base = max(1, base - 1)
        elif wealth >= 0.9 and age <= 22:
            # 최상위 부유 리그 — 유망주를 장기계약으로 조기에 묶어두는 경향
            if random.random() < 0.3:
                base = min(5, base + 1)
    return base


def _contract_years_neg_delta(tier: int) -> int:
    """[2026-08 신설] 기간 협상 1회 성공 시 옮길 수 있는 연수 폭 — 구단
    티어별로 다르다(CONTRACT_YEARS_NEG_DELTA_BY_TIER 참고)."""
    return CONTRACT_YEARS_NEG_DELTA_BY_TIER.get(tier, CONTRACT_YEARS_NEG_DELTA_DEFAULT)


def _negotiation_attempts_weights(prob: float):
    """[2026-08 재설계, 신민용 확정: "직접 지원 화면의 성공 가능성(유력/
    가능성있음/쉽지않음/거의불가능)과 협상 결렬 위험이 같은 기준을 써야
    한다"] prob(그 오퍼의 실제 입단 성공확률, calc_apply_success_prob과
    동일 공식/값)이 높을수록(유력) 협상 기회가 넉넉하고, 낮을수록(거의
    불가능) 기회 자체가 사실상 1회로 줄어든다. 기준선은 apply_window.py의
    _PROB_BANDS(0.70/0.40/0.15)와 동일하게 맞췄다."""
    from constants import NEG_ATTEMPTS_MIN, NEG_ATTEMPTS_MAX
    counts = list(range(NEG_ATTEMPTS_MIN, NEG_ATTEMPTS_MAX + 1))   # [1,2,3,4,5]
    if prob >= 0.70:        # 🟢 유력
        weights = [3, 7, 15, 30, 45]
    elif prob >= 0.40:      # 🟡 가능성 있음
        weights = [10, 20, 30, 25, 15]
    elif prob >= 0.15:      # 🟠 쉽지 않음
        weights = [40, 30, 18, 8, 4]
    else:                   # 🔴 거의 불가능 — 1회짜리가 압도적
        weights = [70, 18, 8, 3, 1]
    return counts, weights


def roll_negotiation_attempts(prob: float) -> int:
    """[2026-08 신설] 오퍼 하나당 협상(연봉·기간 공통) 시도 횟수를 그
    오퍼의 실제 입단 성공확률(join_prob) 기반으로 굴린다. 연봉/기간은
    각각 독립적으로 호출해서 별개의 시도 횟수를 갖는다."""
    counts, weights = _negotiation_attempts_weights(prob)
    return random.choices(counts, weights=weights)[0]


def negotiation_success_prob(prob: float) -> float:
    """[2026-08 재설계] 협상 1회 시도당 성공확률 — 그 오퍼의 실제 입단
    성공확률(prob, 0.03~0.95)을 회당 성공확률(NEG_SUCCESS_PROB_MIN~MAX)
    구간으로 선형 매핑한다. 유력(높은 prob)일수록 협상도 거의 항상
    성공하고, 거의불가능(낮은 prob)일수록 회당 성공확률도 바닥까지
    떨어진다. 연봉/기간 협상이 공유한다."""
    from constants import (NEG_SUCCESS_PROB_MIN, NEG_SUCCESS_PROB_MAX,
                            APPLY_PROB_FLOOR, APPLY_PROB_CEIL)
    span = APPLY_PROB_CEIL - APPLY_PROB_FLOOR
    ratio = (prob - APPLY_PROB_FLOOR) / span if span else 0.0
    ratio = max(0.0, min(1.0, ratio))
    scaled = NEG_SUCCESS_PROB_MIN + ratio * (NEG_SUCCESS_PROB_MAX - NEG_SUCCESS_PROB_MIN)
    return max(NEG_SUCCESS_PROB_MIN, min(NEG_SUCCESS_PROB_MAX, scaled))


def _offer_probability(p, week: int) -> float:
    agent_base = {"F":0.45,"E":0.55,"D":0.65,"C":0.75,"B":0.85,"A":0.92,"S":0.97}
    base = agent_base.get(p.get("agent_grade","F"), 0.45)
    if 1 <= week <= 3:
        # 여름(프리시즌) 이적시장: 작년 풀시즌 평점 사용
        conn2 = get_conn()
        row2  = conn2.execute(
            "SELECT avg_rating FROM career_entries WHERE end_year>0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn2.close()
        rating = row2["avg_rating"] if row2 and row2["avg_rating"] else 6.0
    else:
        # 겨울 이적시장: 겨울 창 진입 직전 스냅샷한 평점 사용
        rating = p.get("first_half_rating", 0)
        if not rating:
            rc = p.get("season_rating_cnt",0); rs = p.get("season_rating_sum",0.0)
            rating = round(rs/rc,2) if rc else 6.0
    ovr = p.get("ovr",40)
    # 포지션군별 평점 기준선: 수비/GK는 기대평점이 낮으므로 기준점을 낮춰 공정화
    pos_grp = POS_GROUP.get(p.get("position","CM"), "미드")
    baseline = RENEW_RATING.get(pos_grp, 6.3)
    # baseline을 5.0 위치로 매핑 → 기준선 이상이면 양수 점수
    r_s = max(0.0, min(1.0, (rating - (baseline - 1.0)) / 4.0))
    o_s = max(0.0, min(1.0, (ovr-30)/70))
    perf = r_s*0.6 + o_s*0.4

    # [밸런스 재설계 2026-07, GPT 검토+상수화 확정] 등급별 최소 보장 확률.
    #   기존 40%~70%는 "경기 못 뛰고 평점 낮아도 이적시장 두 번이면 거의
    #   반드시 온다"는 체감이었음 — 에이전트 등급에 따라 "일 잘하는 에이전트
    #   (S) vs 거의 못 구해주는 에이전트(F)" 격차가 드러나도록 낮췄다.
    #   실제확률 = max(하한선, 퍼포먼스 기반 확률).
    from constants import AGENT_MIN_OFFER_PROB, get_contract_urgency_mult
    guaranteed = AGENT_MIN_OFFER_PROB.get(p.get("agent_grade","F"), AGENT_MIN_OFFER_PROB["F"])

    # [밸런스 재설계 2026-07] 계약 만료 임박 보너스를 연 단위 대신 "게임 내
    #   남은 주 수" 단위로 세분화(보스만 룰 현실 반영: 3개월/6개월/1년
    #   이하일수록 관심 급증). 시즌=52주 고정 구조 확인 완료.
    cur_year = p.get("current_year", GAME_START_YEAR)
    end_year = p.get("contract_end_year", 0)
    cur_week = get_state().get("current_week", 1)
    weeks_left = ((end_year - cur_year) * 52 + (52 - cur_week)) if end_year else 999
    weeks_left = max(0, weeks_left)
    contract_mult = get_contract_urgency_mult(weeks_left)

    calculated = base * perf * contract_mult
    return min(0.95, max(guaranteed, calculated))


YOUTH_SCOUT_PROB = 0.35
# [2026-08 수정, 신민용 확정: 9단계 확장] 예전엔 최상위가 worldclass였지만
# 이제 그 위에 슈퍼스타/신이 새로 생겼다 — 여기 안 넣으면 오히려 가장
# 뛰어난 유망주가 해외 유스 스카우트 대상에서 빠지는 역전이 생긴다.
YOUTH_SCOUT_TALENT_TIERS = ("god", "worldclass", "superstar", "elite")


def _try_youth_scout_offer(c, p, ovr, existing_offers, team_avg_cache, my_country_id):
    """[2026-07 신설] 17~18세 첫 입단 한정, 낮은 확률로 재능상한(talent_cap)
    기준 해외 유스 스카우트 오퍼 하나를 만들어 반환한다(조건 미충족·실패 시
    None). 일반 해외 오퍼(_fill_foreign_pool 등)는 '현재 OVR'로 마진을
    판정하지만, 이건 '재능상한'으로 판정한다는 게 핵심 차이 — 아직 다
    안 큰 유망주를 잠재력만 보고 데려가는 유스 스카우팅을 반영한다.
    대상은 실제로 해외 유스 스카우팅이 활발한 축구 강국(S/SS급)으로
    한정한다(약소국 하위리그가 해외 10대를 스카우트하러 다니는 경우는
    현실적으로 드묾). 곧바로 그 나라 1부 1군으로 데뷔하는 일은 드무니
    2~3부 쪽에 가중치를 둔다."""
    if p.get("talent_tier") not in YOUTH_SCOUT_TALENT_TIERS:
        return None
    if random.random() >= YOUTH_SCOUT_PROB:
        return None
    talent_cap_v = p.get("talent_cap", ovr)
    tiers = [1, 2, 3]
    weights = [1, 3, 4]
    exist_ids = {o["team_id"] for o in existing_offers}
    for _ in range(20):
        grade = random.choice(["SS", "S"])
        tier = random.choices(tiers, weights)[0]
        c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                            cn.id as cid, cn.name as country,cn.flag,cn.grade
                     FROM teams t
                     JOIN leagues l ON t.league_id=l.id
                     JOIN countries cn ON l.country_id=cn.id
                     WHERE cn.grade=? AND l.tier=?
                     ORDER BY RANDOM() LIMIT 20""", (grade, tier))
        cands = [r for r in c.fetchall()
                 if r["id"] not in exist_ids and r["cid"] != my_country_id]
        if not cands:
            continue
        fit = []
        for r in cands:
            r_avg = team_avg_cache.get(r["id"])
            if r_avg is None:
                continue
            margin = CLUB_JOIN_MARGIN_BY_GRADE.get(grade, CLUB_JOIN_MARGIN)
            if (r_avg - talent_cap_v) <= margin:
                fit.append(r)
        if not fit:
            continue
        row = random.choice(fit)
        wealth_g = get_league_grade(row["country"], row["grade"])
        salary = _clamp_salary_to_cap(
            int(_calc_salary(wealth_g, tier, ovr, row["country"], row["name"], year=p.get("current_year"), team_id=row["id"], talent_tier=p.get("talent_tier")) * random.uniform(0.7, 0.9)),
            wealth_g, row["country"], tier, talent_tier=p.get("talent_tier"))
        offer = _build_offer(row, wealth_g, tier, salary)
        offer["_zone"] = "youth_scout"
        return offer
    return None


def _infer_team_ambition(c, team_id, team_name, season, year):
    """[2026-07 신설, 신민용 지적: "구단목표가 전 시즌 성적이랑 무관하게
    뜬다 — 하위권은 중위권을, 중위권은 상위권을, 상위권은 우승을, 강등팀은
    복귀를, 승격팀은 잔류를 목표로 해야 하는 거 아니냐"] 예전엔
    OFFER_AMBITION 가중치로 완전 무작위였다. 이제 그 팀의 방금 끝난
    시즌(승강 이력 우선, 없으면 순위 백분위)을 근거로 정한다.
    판단 근거가 없으면(데이터 없음/시즌 초반 등) None → 호출부가 기존
    가중 랜덤으로 폴백."""
    pl = c.execute("""SELECT from_tier, to_tier FROM promotion_log
                       WHERE team_name=? AND year=? ORDER BY id DESC LIMIT 1""",
                    (team_name, year)).fetchone()
    if pl:
        # to_tier < from_tier: 숫자가 작은 쪽(=상위 디비전)으로 이동 = 승격
        return "강등 회피" if pl["to_tier"] < pl["from_tier"] else "우승 도전"

    t = c.execute("SELECT league_id FROM teams WHERE id=?", (team_id,)).fetchone()
    if not t:
        return None
    league_id = t["league_id"]
    team_ids = [r["id"] for r in c.execute("""
        SELECT DISTINCT home_team_id as id FROM match_results WHERE league_id=? AND season=?
        UNION
        SELECT DISTINCT away_team_id as id FROM match_results WHERE league_id=? AND season=?
    """, (league_id, season, league_id, season)).fetchall()]
    if team_id not in team_ids or len(team_ids) < 4:
        return None

    stats = {tid: {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0} for tid in team_ids}
    for row in c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                             FROM match_results
                             WHERE league_id=? AND season=? AND home_score>=0""",
                          (league_id, season)).fetchall():
        hid, aid, hs, as_ = row["home_team_id"], row["away_team_id"], row["home_score"], row["away_score"]
        for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
            if tid not in stats:
                continue
            stats[tid]["gf"] += gf; stats[tid]["ga"] += ga
            if gf > ga:    stats[tid]["w"] += 1
            elif gf == ga: stats[tid]["d"] += 1
            else:          stats[tid]["l"] += 1

    def _sort_key(tid):
        s = stats[tid]
        return (-(s["w"] * 3 + s["d"]), -(s["gf"] - s["ga"]), -s["gf"])
    ordered = sorted(team_ids, key=_sort_key)
    rank, total = ordered.index(team_id) + 1, len(ordered)
    pct = rank / total
    if pct <= 1 / 3:
        return "우승 도전"
    elif pct <= 2 / 3:
        return "상위권 도전"
    else:
        return "중위권 안정"


def _build_offer(row, grade, tier, salary, join_prob=None) -> dict:
    o = dict(
        team_id=row["id"], team_name=row["name"],
        league_id=row["lid"], league_name=row["lname"],
        tier=row["tier"], country=row["country"],
        flag=row["flag"], grade=grade, salary=salary,
    )
    _enrich_offer(o, row, join_prob=join_prob)
    return o


def _enrich_offer(o: dict, row, join_prob=None) -> dict:
    """[기능1] 오퍼에 역할/감독관심도/구단야망/계약보너스/감독성향 부여.

    역할은 '팀 평균 OVR - 내 OVR' 격차로 결정한다:
      - 내가 팀 수준을 크게 상회 → 주전 보장
      - 비슷 → 주전 경쟁
      - 내가 부족 → 로테이션 / (어리면)유망주 영입
    """
    from constants import (OFFER_ROLES, OFFER_INTEREST, OFFER_AMBITION,
                           offer_bonus_by_tier, MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS)
    p = get_player() or {}
    my_ovr = p.get("ovr", 40)
    my_age = p.get("age", 17)

    # 팀 평균 OVR
    conn = get_conn(); c = conn.cursor()
    r = c.execute("SELECT AVG(ovr) AS a FROM ai_players WHERE team_id=?", (row["id"],)).fetchone()
    conn.close()
    team_avg = r["a"] if r and r["a"] else my_ovr
    gap = team_avg - my_ovr   # +면 내가 팀 수준에 못 미침
    # [2026-08 신설] 연봉/기간 협상 시도 횟수·성공확률 계산에 그대로 쓰기
    # 위해 팀 평균 OVR을 오퍼에 같이 저장해둔다(참고용, 아래 join_prob이 실제 협상 기준).
    o["team_avg_ovr"] = team_avg
    # [2026-08 재설계, 신민용 확정: "직접 지원 화면에 뜨는 성공 가능성(유력/
    # 가능성있음/쉽지않음/거의불가능)과 협상 결렬 위험이 같은 기준이어야
    # 한다 — 평균OVR이 나보다 낮아도 등급 점프·게이트 때문에 거의불가능일
    # 수 있는데, 협상은 그 실제 확률을 봐야지 단순 OVR 격차만 보면 안 된다"]
    # 직접 지원과 완전히 같은 공식(calc_apply_success_prob)으로 이 오퍼의
    # 실제 입단 성공확률을 구해 저장한다. 이미 direct-apply 경로(apply_to_team)
    # 에서 계산해둔 값이 있으면 그걸 그대로 받아써서 중복 계산하지 않는다.
    if join_prob is None:
        try:
            join_prob, _blocked = calc_apply_success_prob(row["id"])
        except Exception:
            join_prob = None
    o["join_prob"] = join_prob

    # 역할 결정 — [밸런스 재설계 2026-07, GPT 검토 반영] 나이 보정은
    #   '능력이 정말 애매한 경우(격차 0~3, 팀 평균이 나와 비슷하거나 살짝
    #   높음)'에만 적용한다. 격차가 음수로 큰 경우(내가 팀보다 확실히
    #   나음)는 나이와 무관하게 주전 경쟁 이상을 유지 — "OVR 84, 팀평균 80,
    #   31세인데 로테이션"처럼 실력이 나은데도 나이만으로 강등되는 걸 방지.
    from constants import ROLE_AGE_THRESHOLD, ROLE_YOUNG_PROSPECT_MAX_AGE
    if gap <= -6:
        role = "주전 보장"
    elif gap <= 3:
        role = "로테이션" if (gap >= 0 and my_age >= ROLE_AGE_THRESHOLD) else "주전 경쟁"
    else:
        role = "유망주 영입" if my_age <= ROLE_YOUNG_PROSPECT_MAX_AGE else "로테이션"
    o["role"] = role

    # 감독 관심도 (가중 랜덤, 단 주전보장이면 직접지명 확률↑)
    int_keys = list(OFFER_INTEREST.keys())
    int_w = [OFFER_INTEREST[k]["weight"] for k in int_keys]
    if role == "주전 보장":
        int_w[int_keys.index("감독 직접 지명")] += 30
    o["interest"] = random.choices(int_keys, int_w)[0]

    # 구단 야망 — [2026-07 재설계] 예전엔 완전 무작위(리그 등급만 살짝
    # 반영)였다. 이제 그 팀의 방금 끝난 시즌 실제 성적/승강 이력을 먼저
    # 본다 — 판단 근거가 없을 때만(신생 리그 등) 기존 가중 랜덤으로 폴백.
    st_now = get_state()
    _cur_season = st_now.get("current_season", 1) if st_now else 1
    _cur_year = st_now.get("current_year", GAME_START_YEAR) if st_now else GAME_START_YEAR
    conn2 = get_conn(); c2 = conn2.cursor()
    _inferred = _infer_team_ambition(c2, row["id"], row["name"], _cur_season - 1, _cur_year - 1)
    if _inferred is None:
        _inferred = _infer_team_ambition(c2, row["id"], row["name"], _cur_season, _cur_year)
    conn2.close()
    if _inferred:
        o["ambition"] = _inferred
    else:
        amb_keys = list(OFFER_AMBITION.keys())
        amb_w = [OFFER_AMBITION[k]["weight"] for k in amb_keys]
        if o.get("grade") in ("S", "A"):
            amb_w[amb_keys.index("우승 도전")] += 15
            amb_w[amb_keys.index("상위권 도전")] += 10
        o["ambition"] = random.choices(amb_keys, amb_w)[0]

    # 계약 보너스
    bonus = offer_bonus_by_tier(o["tier"])
    o["appearance_bonus_k"] = bonus["appearance_bonus_k"]
    o["goal_bonus_k"] = bonus["goal_bonus_k"]

    # 감독 성향 (입단 전 미리 노출 → 결정에 활용)
    o["manager_type"] = random.choices(MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS)[0]

    # [2026-07 신설] 이적료 — 지금 소속팀이 있으면(=계약 중인데 다른 팀이
    # 오퍼) 유료 이적으로 보고 계산, 무소속(계약만료/방출 후 apply_window로
    # 직접 지원)이면 FA라 이적료 0(0.1절 규칙 — "입단"은 항상 0).
    if p.get("current_team_id", 0):
        _contract_end = p.get("contract_end_year", 0)
        _cur_year = p.get("current_year", 0)
        _remain = (max(0, _contract_end - _cur_year)
                   if (_contract_end and _cur_year) else None)
        # [2026-07 순서 변경] my_grade(내 현재 리그 등급)를 base_fee 계산
        # '전에' 먼저 구한다 — seller_origin_dampen_mult가 매수팀 등급과
        # 내 등급의 격차를 알아야 하기 때문(아래 base_fee 계산에 바로 씀).
        #
        # [2026-08 버그수정, 신민용 지적: "6부리그 선수한테 244.8억 최소
        # 요구액이 나오는 게 말이 안 된다"] 예전엔 tier==1(자국 최상위
        # 리그)일 때만 실제 국가 등급을 구하고, tier!=1(2부든 6부든 전부)이면
        # 무조건 "C"로 뭉뚱그렸다 — 그 결과 seller_origin_dampen_mult가
        # 매수팀과의 격차를 계산할 때 "2부 소속"과 "6부 소속"이 완전히
        # 똑같이 취급됐다(둘 다 그냥 "C"). 실제 국가 등급은 항상 먼저
        # 구하고, 그 등급을 부수 깊이만큼 단계적으로 낮추는
        # _gate_grade_for_tier(직접지원 로직에서 쓰는 것과 동일한 함수)를
        # 적용한다 — 같은 나라라도 2부보다 6부가 더 낮은 등급으로 취급돼
        # seller_origin_dampen_mult 할인이 부수에 비례해 커진다.
        from constants import get_league_grade
        _my_country_grade = "C"
        _my_tier = 1
        _my_tid = p.get("current_team_id", 0)
        if _my_tid:
            conn_mg = get_conn()
            _row_mg = conn_mg.execute(
                """SELECT cn.name AS cname, t.current_tier AS tier, cn.grade AS grade
                   FROM teams t JOIN leagues l ON t.league_id=l.id
                   JOIN countries cn ON l.country_id=cn.id WHERE t.id=?""",
                (_my_tid,)).fetchone()
            conn_mg.close()
            if _row_mg:
                _my_country_grade = get_league_grade(_row_mg["cname"], _row_mg["grade"])
                _my_tier = _row_mg["tier"] or 1
        _my_grade = _gate_grade_for_tier(_my_country_grade, _my_tier)

        _base_fee = estimate_transfer_fee(
            o.get("grade"), o["tier"], my_ovr, country=o["country"],
            team_name=o["team_name"], position=get_field_pos(p),
            age=my_age, talent_cap=p.get("talent_cap"),
            contract_remaining_years=_remain,
            year=_cur_year, team_id=row["id"], season=p.get("current_season"),
        )
        # [2026-07 신설, 신민용 리포트: "K리그에서 판매한 이적료가 너무
        # 크다"] 위 base_fee는 오직 '사는 팀'의 리그 등급만 반영한다 —
        # 내가 지금 하위 리그(K리그 등)에 있다는 사실 자체는 여태 전혀
        # 안 깎였다. 매수팀이 내 현재 리그보다 훨씬 강하면(격차가 크면)
        # "검증이 덜 된 원석" 할인을 기준가에 직접 적용한다.
        _base_fee = int(_base_fee * seller_origin_dampen_mult(o.get("grade", "C"), _my_grade))
        # [2026-07 신설] 오퍼 프리미엄 — 이적 협상 시스템(강제판매 체크
        # 등)이 실제로 의미를 가지려면 제안액이 시장가와 항상 똑같으면
        # 안 된다. 내 현재 리그 등급을 구해서 매수팀 등급과의 격차로
        # 프리미엄을 굴린다.
        o["my_grade"] = _my_grade
        o["_base_fee"] = _base_fee
        _new_fee = int(_base_fee * offer_premium_mult(o.get("grade", "C"), _my_grade))
        # [2026-08 재설계, 신민용 지적: "AI가 시장가 근처 한 번 찔러보고
        # 거절당하면 그냥 끝난다 — 진짜 협상이면 거절당할수록 올려서
        # 다시 와야 한다"] 기존엔 "이전 제안액보다 낮게만 안 나오게"
        # 하는 하한선 정도였다(offer_premium_mult가 매번 새 랜덤이라
        # 거절당했다는 사실 자체는 다음 제안액에 전혀 반영 안 됐음).
        # 이제 이 팀이 실제로 몇 번 거절당했는지(rejects, join_team의
        # reject 분기에서 증가)를 기록해두고, 거절 횟수가 쌓일수록
        # "직전 제안액 대비" 인상폭을 키운다 — 1회 거절 후 재접촉은
        # +8~15%, 2회는 +18~28%, 3회 이상은 +30~45%까지. min_accept
        # (최대 base_fee의 1.60배)에 몇 차례 안에 수렴하거나 넘어서도록
        # 설계했다 — forced_sale 체크가 위에 이미 있어 무한정 치솟진
        # 않는다(그 문턱을 넘으면 애초에 강제판매로 처리됨).
        try:
            _hist = json.loads(p.get("offer_history_json") or "{}")
        except Exception:
            _hist = {}
        _team_key = str(row["id"])
        _prev_entry = _hist.get(_team_key)
        # 구버전 세이브 호환: 예전엔 {team_id: fee}(순수 숫자) 형식이었다.
        if isinstance(_prev_entry, dict):
            _prev_fee = _prev_entry.get("fee")
            _rejects = _prev_entry.get("rejects", 0)
        else:
            _prev_fee = _prev_entry
            _rejects = 0
        if _prev_fee:
            if _rejects <= 0:
                _escalate_mult = random.uniform(1.0, 1.08)
            elif _rejects == 1:
                _escalate_mult = random.uniform(1.08, 1.15)
            elif _rejects == 2:
                _escalate_mult = random.uniform(1.18, 1.28)
            else:
                _escalate_mult = random.uniform(1.30, 1.45)
            _escalated_fee = int(_prev_fee * _escalate_mult)
            if _escalated_fee > _new_fee:
                _new_fee = _escalated_fee
        _hist[_team_key] = {"fee": _new_fee, "rejects": _rejects}
        try:
            update_player(offer_history_json=json.dumps(_hist))
        except Exception:
            pass
        o["transfer_fee"] = _new_fee
    else:
        o["transfer_fee"] = 0
    return o


def _get_team_rank_info(c, team_id, ss=None) -> str:
    """이적 오퍼 카드용 순위/성적 문자열.

    현재 주차에 따라 집계 범위 결정 (2026-07 캘린더 기준):
    - 프리시즌(1~3주): 작년 시즌 전체 결과 → "작년 성적"
    - 상반기 진행 중(4~22주): 이번 시즌 상반기까지 → "상반기 성적"
    - 하반기 이후(23주~): 이번 시즌 지금까지(또는 끝났으면 전체) → "시즌 성적"
    승강전 팀이면 이전 리그도 표시.
    ss: season_state 행을 외부에서 주입 가능 (없으면 자체 조회).
    """
    c.execute("SELECT league_id, name FROM teams WHERE id=?", (team_id,))
    t = c.fetchone()
    if not t:
        return ""
    league_id = t["league_id"]
    team_name = t["name"]

    # [최적화] ss가 외부에서 주입되면 DB 재조회 생략
    if ss is None:
        c.execute("SELECT current_week, current_season FROM season_state WHERE id=1")
        ss = c.fetchone()
    cur_week   = ss["current_week"]   if ss else 1
    cur_season = ss["current_season"] if ss else 1

    from constants import SEASON_PHASES
    _ps_s, _ps_e = SEASON_PHASES["preseason1"]
    league_end_week = SEASON_PHASES["second_half"][1]   # 신규 캘린더: 43

    # 집계할 시즌과 주차 범위 결정
    if cur_week <= _ps_e:
        # 프리시즌: 작년 시즌 전체 성적 표시
        prev_season = cur_season - 1
        if prev_season < 1:
            return "(첫 시즌)"
        # 이전 시즌에 실제 경기 기록이 있는지 확인
        # [2026-07 버그수정, 신민용 리포트: "입단 창에선 작년 순위가 안
        # 뜨더라"] 시즌이 끝나면 match_results가 match_results_archive로
        # 옮겨지는데(성능상 아카이빙), 이 함수는 여태 match_results만
        # 보고 있었다 — 그래서 아카이빙된 지 오래된 리그(주로 내가 소속돼
        # 있던 리그, 승강 처리 때 같이 정리됨)의 작년 성적이 안 떴다.
        # 이제 두 테이블을 합쳐서 본다.
        c.execute("""SELECT COUNT(*) as cnt FROM (
                        SELECT home_score FROM match_results
                            WHERE league_id=? AND season=? AND home_score>=0
                        UNION ALL
                        SELECT home_score FROM match_results_archive
                            WHERE league_id=? AND season=? AND home_score>=0
                     )""",
                  (league_id, prev_season, league_id, prev_season))
        if c.fetchone()["cnt"] == 0:
            return ""
        season   = prev_season
        week_min = FIRST_HALF_START
        week_max = league_end_week
        label    = "작년 성적"
    elif cur_week < SECOND_HALF_START:
        # 상반기 진행 중: 이번 시즌 상반기까지
        season   = cur_season
        week_min = FIRST_HALF_START
        week_max = FIRST_HALF_START + 6
        label    = "상반기 성적"
    else:
        # 하반기 이후: 이번 시즌 지금까지(진행 중이면 부분, 다 끝났으면 전체)
        season   = cur_season
        week_min = FIRST_HALF_START
        week_max = min(max(cur_week - 1, FIRST_HALF_START), league_end_week)
        label    = "시즌 성적"

    # 해당 시즌 해당 리그에서 집계 대상 팀 목록 (라이브 + 아카이브 통합)
    c.execute("""SELECT DISTINCT home_team_id as id FROM match_results
                 WHERE league_id=? AND season=? AND week BETWEEN ? AND ?
                 UNION
                 SELECT DISTINCT away_team_id as id FROM match_results
                 WHERE league_id=? AND season=? AND week BETWEEN ? AND ?
                 UNION
                 SELECT DISTINCT home_team_id as id FROM match_results_archive
                 WHERE league_id=? AND season=? AND week BETWEEN ? AND ?
                 UNION
                 SELECT DISTINCT away_team_id as id FROM match_results_archive
                 WHERE league_id=? AND season=? AND week BETWEEN ? AND ?""",
              (league_id, season, week_min, week_max,
               league_id, season, week_min, week_max,
               league_id, season, week_min, week_max,
               league_id, season, week_min, week_max))
    team_ids = [r["id"] for r in c.fetchall()]

    if not team_ids:
        # 경기 기록 없음 → 승강전 정보라도 표시
        c.execute("""SELECT from_tier, to_tier, league_name FROM promotion_log
                     WHERE team_name=? AND year=(
                         SELECT MAX(year) FROM promotion_log
                         WHERE team_name=? AND year>=(
                             SELECT current_year-1 FROM season_state WHERE id=1))
                     ORDER BY id DESC LIMIT 1""",
                  (team_name, team_name,))
        pl = c.fetchone()
        if pl:
            arrow = "🔼 승격" if pl["to_tier"] < pl["from_tier"] else "🔽 강등"
            return f"({pl['league_name']}에서 {arrow})"
        return ""

    # 집계 (라이브 + 아카이브 통합)
    stats = {tid: {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0} for tid in team_ids}
    c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                 FROM match_results
                 WHERE league_id=? AND season=? AND home_score >= 0
                   AND week BETWEEN ? AND ?
                 UNION ALL
                 SELECT home_team_id, away_team_id, home_score, away_score
                 FROM match_results_archive
                 WHERE league_id=? AND season=? AND home_score >= 0
                   AND week BETWEEN ? AND ?""",
              (league_id, season, week_min, week_max,
               league_id, season, week_min, week_max))
    for row in c.fetchall():
        hid, aid, hs, as_ = (row["home_team_id"], row["away_team_id"],
                              row["home_score"],   row["away_score"])
        for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
            if tid not in stats: continue
            stats[tid]["gf"] += gf; stats[tid]["ga"] += ga
            if gf > ga:    stats[tid]["w"] += 1
            elif gf == ga: stats[tid]["d"] += 1
            else:          stats[tid]["l"] += 1

    my = stats.get(team_id)
    if my is None:
        # 이 팀은 해당 시즌 이 리그에 없었음 (승격팀)
        c.execute("""SELECT from_tier, to_tier, league_name FROM promotion_log
                     WHERE team_name=? AND year=(
                         SELECT MAX(year) FROM promotion_log
                         WHERE team_name=? AND year>=(
                             SELECT current_year-1 FROM season_state WHERE id=1))
                     ORDER BY id DESC LIMIT 1""",
                  (team_name, team_name,))
        pl = c.fetchone()
        if pl:
            arrow = "🔼 승격" if pl["to_tier"] < pl["from_tier"] else "🔽 강등"
            return f"({pl['league_name']}에서 {arrow})"
        return ""

    total = my["w"] + my["d"] + my["l"]
    if total == 0:
        result = f"({label}: 경기 없음)"
    else:
        pts = my["w"] * 3 + my["d"]
        def sort_key(tid):
            s = stats[tid]
            return (-(s["w"]*3+s["d"]), -(s["gf"]-s["ga"]), -s["gf"])
        sorted_teams = sorted(team_ids, key=sort_key)
        rank = next((i+1 for i, tid in enumerate(sorted_teams) if tid == team_id), 0)
        result = f"{rank}위  {my['w']}승{my['d']}무{my['l']}패  {pts}점  ({label})"

    # 승강전 여부: 직전 연도만
    c.execute("""SELECT from_tier, to_tier, league_name FROM promotion_log
                 WHERE team_name=? AND year>=(SELECT current_year-1 FROM season_state WHERE id=1)
                 ORDER BY year DESC, id DESC LIMIT 1""",
              (team_name,))
    pl = c.fetchone()
    if pl:
        arrow = "🔼 승격" if pl["to_tier"] < pl["from_tier"] else "🔽 강등"
        result += f"\n  ({pl['league_name']}에서 {arrow})"

    return result


def _suitable_grades(ovr, agent):
    """OVR로 자연스러운 리그 등급대를 정하고, 에이전트 등급에 따라
    '상위 리그 오퍼 +N'을 실제로 적용한다 (AGENT_UPPER_LEAGUE_BONUS).
    좋은 에이전트일수록 실력보다 높은 등급 리그의 오퍼까지 끌어온다."""
    order = ["F","E","D","C","B","A","S","SS"]
    if ovr >= 90: base = ["SS","S"]         # EPL/빅리그 정점
    elif ovr >= 85: base = ["S","A"]
    elif ovr >= 75: base = ["A","B"]
    elif ovr >= 65: base = ["B","C"]
    elif ovr >= 55: base = ["C","D"]
    elif ovr >= 45: base = ["D","E"]
    else: base = ["E","F"]

    # 에이전트 상위리그 보너스: 현재 등급대의 최상위에서 N단계 위까지 추가
    from constants import AGENT_UPPER_LEAGUE_BONUS
    bonus = AGENT_UPPER_LEAGUE_BONUS.get(agent, 0)
    if bonus > 0:
        top_i = max(order.index(g) for g in base)
        for step in range(1, bonus + 1):
            ni = top_i + step
            if ni < len(order) and order[ni] not in base:
                base.append(order[ni])

    # F급 에이전트는 하위 리그만 (상위 오퍼 못 따옴)
    if agent == "F":
        base = [g for g in base if g in ["E","F"]] or ["F"]
    return base



# [2026-07 리팩터링] 연봉/시장가치 계산 로직은 economy.py로 분리했다.
from economy import (
    _base_market_value_eok, MARKET_VALUE_GRADE_MULT, MARKET_VALUE_COUNTRY_MULT,
    _market_value_league_mult, estimate_transfer_fee, _salary_ovr_mult,
    _salary_ovr_adj, _salary_cap_table, _tier_scaled_country_cap,
    _clamp_salary_to_cap, _calc_salary,
    offer_premium_mult, forced_sale_threshold_mult, LEAGUE_GRADE_RANK,
    seller_origin_dampen_mult,
)


# ══════════════════════════════════════════════════════════════
# 이적 협상 판단 (2026-07 신설, 신민용+GPT 다회 설계 확정)
# ══════════════════════════════════════════════════════════════

def _effective_rejection_count(p, cur_year) -> int:
    """[2026-07 신설] 거절 누적 카운터의 3시즌 감쇠 — 마지막 거절로부터
    3년 이상 지났으면 오래된 불만은 잊힌 것으로 보고 0에서 다시 센다."""
    last_year = p.get("transfer_rejection_last_year", 0) or 0
    cnt = p.get("transfer_rejection_count", 0) or 0
    if last_year and (cur_year - last_year) >= 3:
        return 0
    return cnt


def _min_accept_extra_pct(p, offer, my_team_importance_ratio, competing_offer_count,
                          cur_year) -> float:
    """가산식(%) 요소들을 합산해서 반환 — 마지막에 (1.0+합계)를 clamp(0.75,1.60)
    한다. 각 요소는 v2 설계 문서에서 확정한 가중치(★ 개수)를 반영한 크기.

    - 팀 중요도(★★★★★): season_matches/그 시점까지의 팀 전체 경기 수 비율.
    - 계약 잔여기간(★★★★☆): 길수록 안 팜, 6개월 이하면 오히려 싸게라도 팜.
    - 나이/잠재력(★★★★☆): 어리고 잠재력 갭 크면 절대 안 팜, 30대 이상은 팜.
    - 구단 야망(★★★☆☆): 우승 도전이면 안 팜, 강등권이면 재정압박 흡수해 팜.
    - 상위 리그 이적 매력도(★★★★☆, 2026-08 신설): 매수팀 리그 등급이
      확실히 높으면(격차 1~3단계+) 문턱을 낮춘다 — "커리어 도약" 요소.
    - 감독 호감도(★★☆☆☆, 보조): 아주 높을 때만 소폭.
    - 경쟁 오퍼(★★☆☆☆, 보조): 여러 팀이 동시에 관심 보이면 소폭 상승.
    - 거절 누적/불만(할인): 반복 거절될수록 완화(음수로 작용).
    """
    extra = 0.0

    # 팀 중요도 ★★★★★
    if my_team_importance_ratio >= 0.7:
        extra += 0.40
    elif my_team_importance_ratio >= 0.4:
        extra += 0.20
    else:
        extra += 0.0

    # 계약 잔여기간 ★★★★☆
    _contract_end = p.get("contract_end_year", 0)
    _cur_year_r = p.get("current_year", cur_year)
    remain = (max(0, _contract_end - _cur_year_r)
              if (_contract_end and _cur_year_r) else None)
    if remain is not None:
        if remain >= 4:
            extra += 0.25
        elif remain >= 2:
            extra += 0.10
        elif remain >= 1:
            extra += 0.0
        else:
            extra -= 0.15

    # 나이/잠재력 ★★★★☆
    age = p.get("age", 25)
    talent_cap = p.get("talent_cap")
    ovr = p.get("ovr", 60)
    if age <= 21 and talent_cap and (talent_cap - ovr) >= 8:
        extra += 0.20
    elif age >= 31:
        extra -= 0.15

    # 구단 야망 ★★★☆☆ (club_ambition 필드 재사용 — _infer_team_ambition 결과)
    ambition = p.get("club_ambition", "")
    if ambition == "우승 도전":
        extra += 0.15
    elif ambition == "강등 회피":
        extra -= 0.05

    # [2026-07 버그수정, 신민용 지적: "강등팀인데 최소 요구액이 오히려
    # 높게 나온다"] _infer_team_ambition()은 "방금 강등당한 팀"에
    # "우승 도전"(=즉시 승격 노림, 핵심 선수 유지) 라벨을 붙이는데,
    # 위 구단 야망 보정에서 "우승 도전"은 +15%(더 안 팜)를 받는다 —
    # "즉시 승격을 노리는 팀은 스쿼드를 지킨다"는 기존 설계와 "강등하면
    # 재정 압박으로 판매 압박이 커진다"는 현실이 서로 부딪힌 것.
    # club_ambition 라벨 자체(다른 곳에서도 재사용됨)는 안 건드리고,
    # 여기 협상 로직에서만 "이번 시즌에 실제로 강등당했는지"를
    # promotion_log로 직접 확인해서 재정압박 할인을 덧씌운다 —
    # 위의 +15%(우승 도전)를 상쇄하고도 남는 -25%로, 강등 직후엔
    # 결국 판매 쪽으로 기운다.
    _my_tid = p.get("current_team_id", 0)
    if _my_tid:
        _conn_rl = get_conn()
        _team_row = _conn_rl.execute("SELECT name FROM teams WHERE id=?", (_my_tid,)).fetchone()
        if _team_row:
            _rl = _conn_rl.execute(
                """SELECT from_tier, to_tier FROM promotion_log
                   WHERE team_name=? AND year=? ORDER BY id DESC LIMIT 1""",
                (_team_row["name"], cur_year)).fetchone()
            if _rl and _rl["to_tier"] > _rl["from_tier"]:
                extra -= 0.25
        _conn_rl.close()

    # 감독 호감도 ★★☆☆☆ (보조)
    rel = p.get("manager_relation", 50)
    if rel >= 90:
        extra += 0.10
    elif rel >= 80:
        extra += 0.05

    # [2026-08 신설, 신민용+GPT 검토 확정] 상위 리그 이적 매력도 ★★★★☆
    # 강제판매(forced_sale_threshold_mult)에는 "매수팀이 3단계+ 높으면
    # 문턱을 낮춘다"는 격차 보정이 있는데, 정작 그 밑의 정상 협상 구간
    # (min_accept, 여기)에는 매수팀 리그 등급이 전혀 반영되지 않았다 —
    # 그래서 C급 핵심 선수한테 SS급 팀이 정상적인 수준으로 제안해도
    # "팀 중요도"만으로 문턱이 이미 1.5~1.6까지 올라가 있으면 그냥
    # 거절당했다. "상위 리그 기회는 선수·구단 모두에게 매력적"이라는
    # 요소를 추가한다 — 단, 값은 보수적으로(초기 제안 -0.25/0.15/0.08
    # 대비 완화) 잡는다: 이 함수가 이미 여러 요소를 가산하는 구조라
    # 여기에 큰 음수를 더하면 "SS팀이 항상 헐값에 데려간다"는 반대쪽
    # 부작용이 생길 수 있다는 지적을 반영했다. gap==0(동급 이적)은
    # 영향 없음 — SS→SS처럼 이미 최상위끼리는 "도약"이라 할 게 없다.
    my_grade = offer.get("my_grade", "C")
    buyer_grade = offer.get("grade", "C")
    _league_gap = LEAGUE_GRADE_RANK.get(buyer_grade, 4) - LEAGUE_GRADE_RANK.get(my_grade, 4)
    if _league_gap >= 3:
        extra -= 0.15
    elif _league_gap == 2:
        extra -= 0.10
    elif _league_gap == 1:
        extra -= 0.05

    # 경쟁 오퍼 ★★☆☆☆ (보조)
    if competing_offer_count >= 3:
        extra += 0.15
    elif competing_offer_count >= 2:
        extra += 0.10

    # 거절 누적/불만 — 완화(할인)
    rej = _effective_rejection_count(p, cur_year)
    if rej >= 4:
        extra -= 0.30
    elif rej >= 2:
        extra -= 0.15

    return extra


def _my_position_rank_bonus(p, conn=None) -> float:
    """[2026-07 신설] 같은 포지션 내에서 내 OVR이 몇 위인지에 따른 보정.
    "전체 출장은 60%인데 포지션 내 1옵션"이면 구단이 생각보다 안 팔려고
    하는 현실을 반영한다."""
    my_tid = p.get("current_team_id", 0)
    if not my_tid:
        return 0.0
    pos = get_field_pos(p)
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT ovr FROM ai_players WHERE team_id=? AND position=?",
            (my_tid, pos)).fetchall()
    except Exception:
        rows = []
    finally:
        if close_after:
            conn.close()
    my_ovr = p.get("ovr", 60)
    ovrs = sorted([r["ovr"] for r in rows] + [my_ovr], reverse=True)
    rank = ovrs.index(my_ovr) + 1
    if rank == 1:
        return 0.10
    if rank == 2:
        return 0.03
    return -0.05


def _my_team_importance_ratio(p, conn=None) -> float:
    """[2026-07 재설계, 신민용 지적: "출전 비중만 보면 부족하다 — 얼마나
    잘하는지, 포지션 내 위상도 같이 봐야 한다"] 세 요소를 가산식으로
    합산한다:
      - 출전 비중(기본값, 0~1): season_matches / 팀이 이번 시즌 치른 경기 수.
      - 성과 보정: (평균 평점-7.0)*0.15 — 잘하면 가산, 못하면 감산.
      - 포지션 내 순위 보정: 1옵션이면 +0.10, 2옵션 +0.03, 그 외 -0.05.
    최종 clamp(0.1, 1.0). 이 값은 "구단이 얼마나 붙잡으려 하는가"에만
    쓰고 OVR 등 선수 능력 평가와는 분리한다 — OVR 높아도 못 뛰면 팔릴 수
    있고, OVR 낮아도 팀 핵심이면 안 팔릴 수 있는 게 의도된 설계다."""
    season_matches = p.get("season_matches", 0) or 0
    my_tid = p.get("current_team_id", 0)
    if not my_tid:
        return 0.5
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    try:
        row = conn.execute(
            """SELECT COUNT(*) c FROM match_results
               WHERE season=? AND (home_team_id=? OR away_team_id=?) AND home_score>=0""",
            (p.get("current_season", 1), my_tid, my_tid)).fetchone()
        team_played = row["c"] if row else 0
    except Exception:
        team_played = 0
    finally:
        if close_after:
            conn.close()
    appearance_ratio = (min(1.0, season_matches / 38.0) if team_played <= 0
                        else min(1.0, season_matches / team_played))

    rc = p.get("season_rating_cnt", 0) or 0
    rs = p.get("season_rating_sum", 0.0) or 0.0
    avg_rating = (rs / rc) if rc else 7.0   # 표본 없으면 중립(7.0, 보정 0)
    rating_bonus = (avg_rating - 7.0) * 0.15

    position_bonus = _my_position_rank_bonus(p)

    return max(0.1, min(1.0, appearance_ratio + rating_bonus + position_bonus))


def evaluate_offer_decision(p, offer, competing_offer_count=1):
    """오퍼 하나를 받았을 때 원 소속 구단의 판단을 3단계로 계산한다.
    반환: (decision, detail_dict)
      decision: "forced_sale" | "accept" | "reject"
    """
    my_grade = offer.get("my_grade", "C")
    buyer_grade = offer.get("grade", "C")
    cur_year = p.get("current_year", GAME_START_YEAR)

    base_fee = offer.get("_base_fee")
    if base_fee is None:
        base_fee = offer.get("transfer_fee", 0)
    offer_fee = offer.get("transfer_fee", 0)

    # 1) 강제판매 체크
    fs_mult = forced_sale_threshold_mult(buyer_grade, my_grade)
    if base_fee > 0 and offer_fee >= base_fee * fs_mult:
        return "forced_sale", {"forced_sale_mult": fs_mult, "base_fee": base_fee}

    # 2) 최소수용금액 체크
    importance = _my_team_importance_ratio(p)
    extra_pct = _min_accept_extra_pct(p, offer, importance, competing_offer_count, cur_year)
    min_accept_mult = max(0.75, min(1.60, 1.0 + extra_pct))
    min_accept = base_fee * min_accept_mult
    if offer_fee >= min_accept:
        return "accept", {"min_accept_mult": min_accept_mult, "base_fee": base_fee}

    # [2026-08 신설, 신민용 지적: "5.2% 부족이라고 무조건 거절하는 건 너무
    # 경직됐다 — min_accept을 절대적인 거절선이 아니라 협상 기준점으로 보고,
    # 살짝 부족한 제안은 '협상 가능' 영역으로 다뤄야 한다"] 기존엔 min_accept
    # 밑으로는 단 1원만 모자라도 100% 거절이었다 — 절벽형 컷오프. 이제
    # min_accept의 NEGOTIATION_MARGIN(15%) 이내로 부족한 제안은 그 부족률에
    # 반비례하는 확률로 수락한다: 문턱 바로 아래(부족률→0)면 최대
    # NEGOTIATION_MAX_CHANCE(65%)까지, 마진 끝(부족률=15%)이면 0%로 선형
    # 감소. forced_sale 문턱은 그대로 절대선으로 남는다(그건 "거절 못 할
    # 파격 제안" 개념이라 협상 여지 없이 무조건 통과가 맞음) — 여기 바뀌는
    # 건 그 밑의 정상 협상 구간뿐이다.
    NEGOTIATION_MARGIN = 0.15
    NEGOTIATION_MAX_CHANCE = 0.65
    shortfall_pct = (min_accept - offer_fee) / min_accept if min_accept > 0 else 1.0
    if 0 <= shortfall_pct <= NEGOTIATION_MARGIN:
        accept_chance = NEGOTIATION_MAX_CHANCE * (1 - shortfall_pct / NEGOTIATION_MARGIN)
        if random.random() < accept_chance:
            return "accept", {"min_accept_mult": min_accept_mult, "base_fee": base_fee,
                               "negotiated": True, "shortfall_pct": shortfall_pct}

    return "reject", {"min_accept_mult": min_accept_mult, "base_fee": base_fee}


def _record_offer_rejection(p, cur_year):
    """오퍼가 거절될 때마다 호출 — 3시즌 감쇠를 반영해 누적치를 갱신한다."""
    prev = _effective_rejection_count(p, cur_year)
    update_player(transfer_rejection_count=prev + 1, transfer_rejection_last_year=cur_year)


def _reset_offer_rejection():
    """이적이 실제로 성사되면 불만을 리셋한다."""
    update_player(transfer_rejection_count=0, transfer_rejection_last_year=0)


def _save_career_entry(p, year, week, force_new=False, transfer_type=None,
                       allow_insert=True, exit_type="", transfer_fee=None,
                       loan_partner_team=None):
    """커리어 기록 업데이트.
    force_new=True: 이전 팀 기록 확정 (end_year 채움)
    force_new=False: 시즌 종료 시 현재 팀 기록 업데이트
    allow_insert=False: 열린 항목이 없으면 아무것도 하지 않음
        (연말 _close_career_entry로 이미 닫힌 뒤 방출/재계약 거절 시
         유령 중복 행이 생기는 것 방지)
    exit_type: 그 팀에서 떠난 경로('팔림'/'방출'/'이적'/'계약만료'/''=재직중).
        이미 닫힌 항목이어도 exit_type이 있으면 그 행에 덧칠한다.
    transfer_fee: [2026-07 신설] 이 팀에 들어올 때(새 커리어 행이 INSERT될
        때만 의미 있음) 지불된 이적료. None이면 transfer_type이 "이적"일
        때만 economy.estimate_transfer_fee()로 자동 계산하고, 그 외
        (입단/임대/연장)는 0 — transfer_type/exit_type(어떻게 왔는지)과
        완전히 별개 축이라 항상 같이 저장하되, "입단"(FA 포함)은 이적료가
        없는 게 정상이라 자동으로 0이 된다. 명시적으로 값을 넘기면 그
        값을 그대로 쓴다(기존 행 UPDATE 시 값 보존 등에 사용 가능).
    loan_partner_team: [2026-07 신설] exit_type='임대'면 임대를 보내는
        '상대팀'(도착지) 이름, exit_type='임대 종료'면 복귀할 '원소속팀'
        이름. UI가 "OO에 임대(1년)" / "OO 복귀"처럼 상대팀명을 함께
        보여줄 수 있게 한다.
    """
    tid = p.get("current_team_id", 0)
    if not tid: return

    conn = get_conn()
    c = conn.cursor()

    # 팀/리그 정보 (이적 전 팀이므로 tid 기준)
    team_row = c.execute("""SELECT t.name, l.name as lname, l.tier, cn.name as country
                            FROM teams t JOIN leagues l ON t.league_id=l.id
                            JOIN countries cn ON t.country_id=cn.id
                            WHERE t.id=?""", (tid,)).fetchone()
    if not team_row:
        conn.close(); return

    season = p.get("current_season", 1)

    # [버그수정 2026-08, 신민용 리포트: "2004년에 승급했는데 그 해 기록이
    # 0승0무0패/리그명도 승격된 리그로 잘못 나온다"] 아래는 "이미 열린
    # 항목을 시즌 도중 갱신"하는 경우에만 필요한 보정이다 — team_row(현재
    # 소속 리그)를 그대로 쓰면, 43주에 승강이 반영된 뒤(44~52주 국제대회
    # 기간)엔 '이번 시즌 실제로 뛴 리그'가 아니라 '다음 시즌 리그'가 찍힌다.
    # 반대로 아래 INSERT 분기(신규 입단 행)는 이번 시즌 경기가 아직 없는
    # 새 스틴트라 team_row(현재 소속)가 그대로 정답이므로 건드리지 않는다.
    _season_lid = _team_league_id_for_season(c, tid, season)
    if _season_lid is not None:
        _season_league_row = c.execute(
            "SELECT name, tier FROM leagues WHERE id=?", (_season_lid,)).fetchone()
    else:
        _season_league_row = None
    season_lname = _season_league_row["name"] if _season_league_row else team_row["lname"]
    season_tier  = _season_league_row["tier"] if _season_league_row else team_row["tier"]
    lid = _season_lid
    if lid is None:
        _live_lid_row = c.execute("SELECT league_id FROM teams WHERE id=?", (tid,)).fetchone()
        lid = _live_lid_row["league_id"] if _live_lid_row else None

    rank_str = get_team_rank(tid, conn=conn, season=season)
    try:
        rn = int(rank_str.split("위")[0].replace("공동","").strip())
    except (ValueError, AttributeError, IndexError):
        rn = 0

    sm  = p.get("season_matches", 0)
    sg  = p.get("season_goals", 0)
    sa  = p.get("season_assists", 0)
    ss  = p.get("season_saves", 0)
    sga = p.get("season_goals_against", 0)
    rc  = p.get("season_rating_cnt", 0)
    rs  = p.get("season_rating_sum", 0.0)
    avg_r = round(rs/rc, 2) if rc else 0.0

    # 팀 전적: teams 테이블 대신 match_results에서 직접 집계 (sync 오염 방지,
    #   _team_wdl_from_results로 통일 — _update_career_stats/_close_career_entry와 동일)
    tw, td, tl = _team_wdl_from_results(c, tid, lid, season)
    pos = get_field_pos(p)   # 배치 포지션 (포메이션 슬롯 기반, 없으면 주요 포지션)
    cs  = _calc_clean_sheets(c, tid, season, matches=sm)
    rc_league = p.get("season_red_cards_league", 0)

    # end_year=0인 열린 항목 찾기 (team_id 우선, 구버전 행은 이름 폴백)
    existing = _find_open_entry(c, tid, team_row["name"])

    if existing:
        c.execute("""UPDATE career_entries SET
            end_year=?, end_week=?, matches=?, goals=?, assists=?, saves=?, goals_against=?,
            avg_rating=?, team_rank=?, wins=?, draws=?, losses=?, clean_sheets=?,
            league_name=?, tier=?, salary=?, position=?, team_id=?, exit_type=?,
            loan_partner_team=COALESCE(?, loan_partner_team), red_cards=?
            WHERE id=?""",
            (year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs,
             season_lname, season_tier, p.get("salary", 0), pos, tid,
             exit_type, loan_partner_team, rc_league, existing["id"]))
    elif not allow_insert:
        # 이미 닫힌 항목만 존재 → 중복 행은 안 만들되, 떠난 경로(exit_type)는
        # 가장 최근에 닫힌 그 팀 항목에 덧칠해 준다 (방출/팔림 표시 누락 방지).
        if exit_type:
            closed = c.execute("""SELECT id FROM career_entries
                WHERE team_id=? AND end_year>0
                ORDER BY end_year DESC, end_week DESC, id DESC LIMIT 1""",
                (tid,)).fetchone()
            if closed:
                c.execute("UPDATE career_entries SET exit_type=?, "
                          "loan_partner_team=COALESCE(?, loan_partner_team) WHERE id=?",
                          (exit_type, loan_partner_team, closed["id"]))
                conn.commit()
        conn.close()
        return
    else:
        cur_year = p.get("current_year", year)
        cur_week = p.get("current_week", week)
        pending_tt     = transfer_type if transfer_type else _pending_transfer_type
        c_yrs_save     = p.get("contract_years", 0)

        saved_tier = p.get("current_tier") or team_row["tier"]

        # [2026-07 신설] 이적료 자동 계산 — "이적"(계약 중 유료 이적)일 때만
        # 계산하고, 입단(첫 계약/FA 포함)·임대·연장은 0. 명시적으로 넘겨받은
        # 값이 있으면 그걸 우선한다(하위호환 + 값 보존용).
        if transfer_fee is None:
            # [버그수정 2026-07, 신민용 리포트: "오퍼는 이적료가 뜨는데
            # 커리어엔 안 뜬다"] join_team()이 실제로 넘기는 유료 이적
            # transfer_type 값은 "이적"이 아니라 "오퍼"(center_panel.py의
            # _on_auto_offer_done)였다 — "이적"은 나갈 때(exit_type)만
            # 쓰이는 값이라 조건이 항상 거짓이었음. 둘 다 받아준다.
            # [2026-07 재조정, 신민용+GPT 설계 확정] 팔림도 이적과 동일한
            # 이적료 체계를 쓴다 — _ensure_career_entry와 동일하게 맞춤.
            if pending_tt in ("이적", "오퍼", "팔림"):
                _grade = get_league_grade(team_row["country"], "")
                _contract_end = p.get("contract_end_year", 0)
                _remain = (max(0, _contract_end - cur_year)
                           if _contract_end else None)
                transfer_fee = estimate_transfer_fee(
                    _grade, saved_tier, p.get("ovr", 0),
                    country=team_row["country"], team_name=team_row["name"],
                    position=get_field_pos(p), age=p.get("age"),
                    talent_cap=p.get("talent_cap"),
                    contract_remaining_years=_remain,
                    year=cur_year, team_id=tid,
                )
            else:
                transfer_fee = 0

        c.execute("""INSERT INTO career_entries
            (age, position, team_name, league_name, tier, salary,
             start_year, start_week, end_year, end_week,
             matches, goals, assists, saves, goals_against,
             avg_rating, team_rank, wins, draws, losses,
             contract_years, transfer_type, clean_sheets, team_id,
             contract_role, manager_type, club_ambition, exit_type, transfer_fee, red_cards)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["age"], pos, team_row["name"], team_row["lname"], saved_tier,
             p.get("salary", 0), cur_year, cur_week,
             year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl,
             c_yrs_save, pending_tt, cs, tid,
             p.get("contract_role",""), p.get("manager_type",""), p.get("club_ambition",""),
             exit_type, transfer_fee, rc_league))

    conn.commit()
    conn.close()


def mark_contract_extension(yrs: int):
    """재계약(연장) 수락 시 호출. 연장이 발동된 '현재 시즌'의 열린 커리어
    항목(end_year=0)에 transfer_type='연장'과 연장 연수를 박는다.

    연장은 계약 만료 다음 해 1~4주차에 발동되므로, 그해의 줄에
    '계약: N년 / 이적: 연장'이 표시된다. 열린 항목이 아직 없으면
    (드물게 줄 생성 전 시점) _pending_transfer_type='연장'만 세팅된 상태로
    다음 _ensure_career_entry가 정상 생성한다.
    """
    p = get_player()
    if not p:
        return
    global _pending_transfer_type
    tid = p.get("current_team_id", 0)
    if not tid:
        return
    update_player(contract_years=yrs)
    conn = get_conn()
    c = conn.cursor()
    team_row = c.execute("SELECT name FROM teams WHERE id=?", (tid,)).fetchone()
    tname = team_row["name"] if team_row else ""
    existing = _find_open_entry(c, tid, tname)
    if existing:
        c.execute("""UPDATE career_entries
                     SET transfer_type='연장', contract_years=?
                     WHERE id=?""", (yrs, existing["id"]))
        conn.commit()
        # 열린 줄에 직접 박았으므로 다음 시즌 줄에 '연장'이 잔류하지 않도록
        # _pending_transfer_type을 대기값('')으로 되돌린다. (연장은 발동된 그 해만 표시)
        _pending_transfer_type = ""
    else:
        # 드물게 열린 줄이 아직 없으면, 다음 _ensure_career_entry가 만들 줄에
        # '연장'이 들어가도록 플래그만 세팅. (그 _ensure가 소비)
        _pending_transfer_type = "연장"
    conn.close()


def _enforce_foreign_quota_on_join(team_id, team_country, my_nationality):
    """[2026-07 신설, 신민용 확정] 내가 외국인으로 입단해서 그 팀 외국인
    쿼터(database.FOREIGN_QUOTA_CAP)를 넘기면, AI 외국인 중 OVR 최저
    1명을 자국 선수로 바꿔치기(국적만 변경, 스탯은 그대로 — 실제로도
    외국인 쿼터 초과 시 유망주 자리를 자국 선수로 채우는 것과 비슷한
    그림)해서 쿼터를 맞춘다. 내가 자국 선수면(quota 안 걸림) 아무것도
    안 한다."""
    from database import FOREIGN_QUOTA_CAP
    quota = FOREIGN_QUOTA_CAP.get(team_country)
    if quota is None or my_nationality == team_country:
        return   # 쿼터 없는 나라거나, 내가 자국 선수라 쿼터에 안 걸림
    conn = get_conn()
    foreigners = conn.execute(
        """SELECT id, ovr FROM ai_players WHERE team_id=? AND nationality!=? AND nationality!=''
           ORDER BY ovr ASC""", (team_id, team_country)).fetchall()
    # 나(외국인)까지 합쳐서 쿼터 초과인지 확인 — AI 외국인 수 + 나(1) > quota
    if len(foreigners) + 1 > quota:
        swap_n = len(foreigners) + 1 - quota
        for r in foreigners[:swap_n]:
            conn.execute("UPDATE ai_players SET nationality=? WHERE id=?", (team_country, r["id"]))
        conn.commit()
        add_log(f"📋 외국인 쿼터 조정 — AI 선수 {swap_n}명 국적을 {team_country}로 전환", "event")
    conn.close()


def join_team(team_id, salary, transfer_type: str = "입단", offer: dict = None):
    p = get_player()

    # [2026-07 신설, 신민용+GPT 다회 설계 확정: 이적 협상 시스템] "오퍼"
    # 타입(유료 이적)에 한해 원 소속 구단이 실제로 판매를 승인하는지
    # 게이트를 건다 — 입단(FA)·로 등은 애초에 원 소속 구단이 없거나
    # 이적료 개념이 아니라서 대상이 아니다.
    if transfer_type == "오퍼" and offer and p and p.get("current_team_id"):
        decision, detail = evaluate_offer_decision(
            p, offer, competing_offer_count=offer.get("competing_offer_count", 1))
        cur_year_for_log = p.get("current_year", GAME_START_YEAR)
        if decision == "reject":
            _record_offer_rejection(p, cur_year_for_log)
            # [2026-08 신설] 이 팀 전용 거절 횟수를 올려둔다 — 다음 번
            # 이 팀이 다시 제안할 때(_enrich_offer) 이 값을 보고 얼마나
            # 더 올려서 부를지 정한다(재협상 사다리).
            try:
                _hist2 = json.loads(p.get("offer_history_json") or "{}")
                _tk2 = str(offer.get("team_id", ""))
                _entry2 = _hist2.get(_tk2)
                if isinstance(_entry2, dict):
                    _entry2["rejects"] = _entry2.get("rejects", 0) + 1
                else:
                    _entry2 = {"fee": offer.get("transfer_fee", 0), "rejects": 1}
                _hist2[_tk2] = _entry2
                update_player(offer_history_json=json.dumps(_hist2))
            except Exception:
                pass
            _buyer_name = offer.get("team_name", "")
            _buyer_grade = offer.get("grade", "?")
            add_log(
                f"🚫 구단이 이적 제안을 거절했습니다. ({_buyer_name}[{_buyer_grade}급] 제안 "
                f"{fmt_money(offer.get('transfer_fee',0))} "
                f"< 최소 요구 {fmt_money(int(detail['base_fee']*detail['min_accept_mult']))})",
                "event")
            return
        elif decision == "forced_sale":
            add_log(
                f"💰 구단은 잔류를 원했으나, 기록적인 제안({fmt_money(offer.get('transfer_fee',0))})을 "
                f"거절할 수 없어 이적을 승인했습니다.", "event")
            _reset_offer_rejection()
        else:
            if detail.get("negotiated"):
                add_log(
                    f"🤝 구단이 다소 부족한 제안이었지만 협상 끝에 이적을 승인했습니다. "
                    f"({fmt_money(offer.get('transfer_fee',0))}, 최소 요구 대비 "
                    f"{detail['shortfall_pct']*100:.1f}% 부족)", "event")
            _reset_offer_rejection()

    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT t.name,t.formation,l.id as lid,l.name as lname,l.tier,
                        cn.name as country
                 FROM teams t JOIN leagues l ON t.league_id=l.id
                 JOIN countries cn ON l.country_id=cn.id
                 WHERE t.id=?""", (team_id,))
    row = c.fetchone()
    conn.close()
    if not row: return

    st = get_state()
    cur_year = st["current_year"]
    cur_week = st["current_week"]

    # 이전 팀 커리어 기록 확정 (end_year=0인 항목 닫기)
    if p and p.get("current_team_id") and p["current_team_id"] != team_id:
        prev_tid = p["current_team_id"]
        # [버그수정 2026-07, 신민용 리포트: "시즌 시작 전 오퍼로 바로 이적하면
        # 원래 있던 팀이 커리어에 아예 안 뜬다"] 새 팀 커리어 항목은 첫 4주
        # 진행 시(_ensure_career_entry)에야 비로소 생성되는데, 그 전에
        # (즉 이전 팀에서 단 한 번도 _ensure_career_entry가 안 불린 채로)
        # 바로 다른 팀 오퍼를 수락하면 전역 _pending_transfer_type이 아래서
        # 새 값으로 덮어써지기 전에, 이전 팀 몫의 항목을 먼저 만들어둬야
        # 한다 — 안 그러면 "입단"이었다는 기록 자체가 통째로 사라진다.
        global _pending_transfer_type
        if _pending_transfer_type:
            _ensure_career_entry(p, st)
        # (변경) 시즌 중 이적 시 이전 팀에 우승을 주지 않는다.
        # 우승은 시즌 종료 시점 소속팀이 1위일 때만 인정된다.
        # 떠난 경로: 계약이 아직 남았는데 옮기면 '이적', 만료됐으면 '계약만료'
        prev_end = p.get("contract_end_year", 0)
        exit_t = "계약만료" if (prev_end and prev_end <= cur_year) else "이적"
        _save_career_entry(p, cur_year, cur_week, force_new=True, exit_type=exit_t)
        # [2026-07 버그수정, 신민용 리포트: "임대 중에 새 팀으로 오퍼가 오면
        # 임대 개념이 사라져야 하는데 안 그렇다"] 임대 중(loan_from_team_id
        # 세팅됨)에 다른 팀 오퍼를 수락해서 join_team이 또 호출되면, 이
        # 함수는 원래 loan_from_* 필드를 안 건드렸다. 그래서 나중에
        # loan_end_year가 지나면 _return_from_loan_if_due가 "아직 임대
        # 중"으로 착각해서 지금 팀(새로 오퍼로 간 팀)을 '임대 종료'로 닫고
        # 원래 임대처가 아니라 그 이전 원소속팀으로 강제 복귀시켜버렸다
        # (예: A팀 임대 중 B팀 오퍼 수락 → 나중에 자동으로 원소속팀 복귀,
        # B팀 이력엔 '임대 종료'라고 잘못 표시). 오퍼로 새 팀에 가는 순간
        # 임대 관계는 완전히 끝난 것으로 보고 임대 필드를 전부 정리한다.
        if p.get("loan_from_team_id"):
            update_player(loan_from_team_id=0, loan_from_league_id=0,
                          loan_from_tier=0, loan_end_year=0)
        # 새 팀 스탯 초기화
        #   (버그수정) 기존엔 기본 스탯만 리셋해 season_blocks/pass_acc/key_passes/
        #   dribbles/shots 등 '세부 통계'가 이전 팀에서 그대로 이월됐다.
        #   → 0출전 신규 팀인데 차단 31·패스 83% 가 찍히는 원인. 시즌말 리셋과
        #     동일한 필드 전체를 함께 0으로 초기화한다.
        update_player(season_matches=0, season_goals=0, season_assists=0,
                      season_saves=0, season_rating_sum=0.0, season_rating_cnt=0,
                      season_goals_against=0,
                      season_shots=0, season_shots_on=0, season_key_passes=0,
                      season_dribbles=0, season_blocks=0,
                      season_pass_acc_sum=0, season_pass_acc_cnt=0,
                      season_red_cards_league=0)
        # [에이전트 익스플로잇 차단] 이적 시 개별 협상 수수료(agent_fee_rate)를
        #   리셋한다. 예전엔 약소국·저연봉 시절 헐값에 잡은 낮은 수수료율이
        #   이적 후 폭등한 연봉에도 평생 고정 적용됐다. 이제 이적하면 그 특혜가
        #   사라지고, 다음 급여부터는 에이전트 '등급 기본 수수료'로 돌아간다
        #   (새 계약엔 새 조건). 재계약 원하면 에이전트 창에서 다시 협상.
        if p.get("agent_fee_rate", 0):
            update_player(agent_fee_rate=0)
            try:
                _ag = p.get("agent_grade", "F")
                _base_fee = AGENT_FEE_RATE.get(_ag, 0.0)
                add_log(f"📑 이적으로 에이전트 계약 갱신 — 수수료 {int(_base_fee*100)}%(등급 기본)로 조정", "event")
            except Exception:
                pass

    age_jt = p.get("age",17) if p else 17
    # [2026-08 수정, 신민용 확정] OfferWindow에서 '기간 협상'으로 이미
    # 합의된 값이 있으면 그걸 그대로 쓴다 — 예전엔 여기서 무조건 다시
    # _calc_contract_years를 굴려서, 화면에서 협상한 계약 기간이 실제
    # 계약 체결 시점에는 통째로 무시되고 새로 랜덤 재추첨되는 버그가
    # 있었다. offer에 없거나(구버전 호출부·직접지원 등) None이면 기존
    # 처럼 그 자리에서 새로 굴린다(하위호환).
    c_yrs = (offer.get("contract_years") if offer else None)
    if c_yrs is None:
        c_yrs = _calc_contract_years(age_jt, row["tier"], row["country"])
    # 계약 만료 연도 (만료는 해당 연도 52주차)
    #  - 시즌 초(프리시즌 1~3주) 계약: 올해가 1년차 → cur_year + c_yrs - 1
    #    예: 2008년 2주 1년계약 → 2008년 52주 만료
    #  - 시즌 중(4주~) 계약: 올해는 미포함, 다음 시즌부터 카운트 → cur_year + c_yrs
    #    예: 2008년 37주 1년계약 → 2009년 52주 만료
    from constants import SEASON_PHASES as _SP3
    if cur_week <= _SP3["preseason1"][1]:
        c_end = cur_year + c_yrs - 1
    else:
        c_end = cur_year + c_yrs
    # (global _pending_transfer_type은 위에서 이미 선언됨 — 같은 함수 내
    # 중복 global 선언은 "used prior to global declaration" SyntaxError를
    # 유발하므로 여기서는 대입만 한다)
    _pending_transfer_type = transfer_type

    # ── [기능1+2] 오퍼 맥락 반영 ─────────────────────────────
    from constants import OFFER_ROLES, OFFER_INTEREST, MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS
    role        = (offer or {}).get("role", "주전 경쟁")
    interest    = (offer or {}).get("interest", "명단 후보")
    ambition    = (offer or {}).get("ambition", "중위권 안정")
    mgr_type    = (offer or {}).get("manager_type") or random.choices(MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS)[0]
    app_bonus   = (offer or {}).get("appearance_bonus_k", 0)
    goal_bonus  = (offer or {}).get("goal_bonus_k", 0)

    # 입단 시 감독관계 초기값 = 역할 기본값 + 관심도 보너스
    rel_init = OFFER_ROLES.get(role, {}).get("rel_init", 50)
    rel_init += OFFER_INTEREST.get(interest, {}).get("rel_bonus", 0)
    rel_init = max(0, min(100, rel_init))

    # 새 팀 포메이션 기반으로 field_pos 즉시 결정 → career_entries에 올바른 포지션 저장
    try:
        from constants import POSITION_COMPAT, FORMATION_SLOTS
        _formation = row.get("formation", "4-4-2") or "4-4-2"
        _slots = FORMATION_SLOTS.get(_formation, FORMATION_SLOTS["4-4-2"])
        _primary = p.get("position", "CM") if p else "CM"
        _compat = POSITION_COMPAT.get(_primary, [_primary])
        _best_pos, _best_rank = _primary, 0
        _best_found = 999
        for _slot in _slots:
            if _slot in _compat:
                _rank = _compat.index(_slot)
                if _rank < _best_found:
                    _best_found = _rank
                    _best_pos = _slot
                    _best_rank = _rank
        _field_pos = _best_pos
        _mismatch_rank = _best_rank
    except Exception:
        _field_pos = p.get("position", "CM") if p else "CM"
        _mismatch_rank = 0

    update_player(current_team_id=team_id, current_league_id=row["lid"],
                  salary=salary, manager_relation=rel_init,
                  contract_years=c_yrs, contract_end_year=c_end,
                  current_tier=row["tier"],
                  contract_role=role, club_ambition=ambition,
                  manager_type=mgr_type,
                  appearance_bonus_k=app_bonus, goal_bonus_k=goal_bonus,
                  transfer_requested=0,
                  field_pos=_field_pos, mismatch_rank=_mismatch_rank)
    # [2026-07 신설, 신민용 확정] 내가 외국인 신분으로 입단해서 그 팀의
    # 외국인 쿼터를 넘기면, AI 외국인 한 명을 자국 선수로 바꿔치기해서
    # 쿼터를 맞춘다 (예: 4명 제한인데 AI 4명이 이미 꽉 차 있고 나까지
    # 외국인이면 → AI 중 1명을 자국으로 전환해 4명 유지).
    _enforce_foreign_quota_on_join(team_id, row["country"], p.get("nationality", "") if p else "")
    icon = {"입단":"⭐","오퍼":"✈","방출":"😡"}.get(transfer_type,"⭐")
    add_log(f"{icon} {row['name']} {transfer_type}!  {row['lname']}({row['tier']}부)"
            f"  |  {c_yrs}년 계약  |  월 {fmt_money(salary//12)}", "event")
    add_log(f"   ↳ 역할: {role} | 감독: {mgr_type} | 구단 목표: {ambition} | 관심: {interest}", "normal")

    # 새 팀 커리어 항목은 첫 4주 진행 시 생성 (advance_4weeks에서 처리)
    # 즉시 생성하면 입단 즉시 1~0/0주 같은 이상한 기록이 남음

    # 새 리그 일정 생성 (내 리그 + 인접 리그)
    _generate_adjacent_schedules(row["lid"], st["current_season"], st["current_year"])

    # 이적 시점 이전에 이미 지나간 주차의 미완료 경기를 일괄 시뮬
    _backfill_past_matches(row["lid"], st["current_season"], cur_week, team_id)

    # teams 테이블을 match_results 기준으로 재동기화
    # (오퍼 창 _sim_league_full이 match_results만 채우고 teams를 건드리지 않아서)
    _sync_teams_from_results(row["lid"], st["current_season"])


def request_transfer() -> dict:
    """[기능3] 이적 요청. 감독관계 하락을 감수하고 다음 오퍼 창을 활성화.

    반환 dict (UI/추후 Godot 공용):
      {"ok": bool, "msg": str, "manager_relation": int}
    """
    from constants import TRANSFER_REQUEST_REL_PENALTY
    p = get_player()
    if not p or not p.get("current_team_id"):
        return {"ok": False, "msg": "소속팀이 없습니다.", "manager_relation": 0}
    if p.get("transfer_requested"):
        return {"ok": False, "msg": "이미 이적을 요청한 상태입니다.",
                "manager_relation": p.get("manager_relation", 50)}

    rel = max(0, p.get("manager_relation", 50) - TRANSFER_REQUEST_REL_PENALTY)
    update_player(manager_relation=rel, transfer_requested=1)
    add_log(f"📣 이적 요청! 감독과의 관계가 악화됐다. (관계 {rel})", "event")
    return {"ok": True,
            "msg": f"이적을 요청했습니다. 다음 이적시장에서 더 많은 오퍼가 들어옵니다.\n"
                   f"감독 관계가 {TRANSFER_REQUEST_REL_PENALTY} 하락했습니다.",
            "manager_relation": rel}


def negotiate_renewal() -> dict:
    """[기능3] 재계약 협상. 평점·감독관계 기반 성공 확률로 연봉 인상+계약 연장.

    반환 dict:
      {"ok": bool, "success": bool, "msg": str,
       "old_salary": int, "new_salary": int, "manager_relation": int}
    """
    from constants import RENEW_NEGOTIATE
    p = get_player()
    if not p or not p.get("current_team_id"):
        return {"ok": False, "success": False, "msg": "소속팀이 없습니다.",
                "old_salary": 0, "new_salary": 0, "manager_relation": 0}

    rc = p.get("season_rating_cnt", 0); rs = p.get("season_rating_sum", 0.0)
    avg_rating = round(rs / rc, 2) if rc > 0 else 6.5
    rel = p.get("manager_relation", 50)
    cfg = RENEW_NEGOTIATE

    prob = (cfg["base_prob"]
            + (avg_rating - 6.5) * cfg["rating_per_point"]
            + ((rel - 50) / 10.0) * cfg["rel_per_10"])
    prob = max(0.05, min(0.95, prob))

    old_salary = p.get("salary", 0)
    st = get_state()
    cur_year = st["current_year"] if st else p.get("current_year", GAME_START_YEAR)

    if random.random() < prob:
        lo, hi = cfg["raise_success"]
        raise_pct = random.uniform(lo, hi)
        new_salary = int(old_salary * (1 + raise_pct))
        # [버그수정 2026-07, 신민용 지적] old_salary*배율로 새 연봉을 만들
        # 뿐 _calc_salary를 안 거쳐서 등급 캡이 안 걸렸다 — 재계약을 몇 번
        # 성공하면 D등급 5천만 캡도 복리로 뚫림. 현재 소속 리그 등급/국가
        # 기준으로 한 번 더 캡을 씌운다.
        gt = _my_grade_tier(p)
        if gt:
            g_wealth, g_tier, g_country = gt
            new_salary = _clamp_salary_to_cap(new_salary, g_wealth, g_country, g_tier)
        new_end = max(p.get("contract_end_year", cur_year), cur_year) + cfg["extend_years"]
        new_rel = min(100, rel + 5)
        update_player(salary=new_salary, contract_end_year=new_end,
                      manager_relation=new_rel)
        # [2026-07 버그수정, 신민용 리포트: "2002~2006년 김천 상무에서
        # 재계약해서 계속 뛴 건데 커리어/은퇴 요약 어디에도 '재계약'이
        # 안 뜬다"] 자연 계약만료 시 팀이 제안하는 재계약(mark_contract_
        # extension이 호출됨)은 career_entries에 transfer_type='연장'을
        # 정확히 남기는데, 이 능동적 협상(에이전트 창에서 직접 재계약
        # 요청) 경로는 연봉/계약기간만 바꾸고 커리어 기록엔 전혀 안
        # 남기고 있었다 — 같은 함수를 그대로 재사용해서 통일한다.
        mark_contract_extension(cfg["extend_years"])
        add_log(f"🤝 재계약 성공! 연봉 +{int(raise_pct*100)}% "
                f"(월 {fmt_money(new_salary//12)}), {cfg['extend_years']}년 연장 "
                f"(~{new_end})", "event")
        return {"ok": True, "success": True,
                "msg": f"재계약 성공! 연봉이 {int(raise_pct*100)}% 인상되고 "
                       f"{cfg['extend_years']}년 연장됐습니다.",
                "old_salary": old_salary, "new_salary": new_salary,
                "manager_relation": new_rel}
    else:
        new_rel = max(0, rel + cfg["raise_fail_rel"])
        update_player(manager_relation=new_rel)
        add_log(f"🚫 재계약 협상 결렬. 구단이 인상안을 거절했다. (관계 {new_rel})", "event")
        return {"ok": True, "success": False,
                "msg": "구단이 인상안을 거절했습니다. 감독 관계가 소폭 하락했습니다.",
                "old_salary": old_salary, "new_salary": old_salary,
                "manager_relation": new_rel}


def _sync_teams_from_results(league_id, season):
    """match_results 기준으로 teams 테이블의 전적을 재계산해서 덮어씀.
    오퍼 창에서 _sim_league_full이 teams를 건드리지 않은 경우 동기화.
    """
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,))
    team_ids = [r["id"] for r in c.fetchall()]

    stats = {tid: {"w":0,"d":0,"l":0,"gf":0,"ga":0} for tid in team_ids}

    c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                 FROM match_results
                 WHERE league_id=? AND season=? AND home_score>=0""",
              (league_id, season))
    for row in c.fetchall():
        hid  = row["home_team_id"]
        aid  = row["away_team_id"]
        hs   = row["home_score"]
        as_  = row["away_score"]
        for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
            if tid not in stats: continue
            stats[tid]["gf"] += gf; stats[tid]["ga"] += ga
            if gf > ga:    stats[tid]["w"] += 1
            elif gf == ga: stats[tid]["d"] += 1
            else:          stats[tid]["l"] += 1

    if stats:
        c.executemany("""UPDATE teams SET wins=?,draws=?,losses=?,
                     goals_for=?,goals_against=? WHERE id=?""",
                  [(s["w"], s["d"], s["l"], s["gf"], s["ga"], tid)
                   for tid, s in stats.items()])

    conn.commit()
    conn.close()


def _generate_first_half_schedule(league_id, season, year):
    """오퍼 창 순위 확인용: 상반기(5~11주) 일정만 생성.
    하반기 일정은 만들지 않아 입단 후 경기 일정에 영향 없음.
    """
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,))
    tids = [r["id"] for r in c.fetchall()]
    if len(tids) < 2:
        conn.close(); return

    # [2026-07 버그수정] existing_matches 구조가 set에서 {pair:[day,...]}
    # 딕셔너리로 바뀌었는데(_is_dup_fixture 참고) 이 호출부만 고치는 걸
    # 누락해서 AttributeError로 크래시났다 — 여기도 통일한다.
    c.execute("""SELECT day, home_team_id, away_team_id FROM match_results
                 WHERE league_id=? AND season=? AND day IS NOT NULL""", (league_id, season))
    existing = {}
    for r in c.fetchall():
        d, h, a = r["day"], r["home_team_id"], r["away_team_id"]
        existing.setdefault((min(h, a), max(h, a)), []).append(d)

    new_rows = _build_league_schedule_rows(league_id, tids, season, year, existing, first_half_only=True)

    if new_rows:
        c.executemany("""INSERT INTO match_results
                         (league_id,week,home_team_id,away_team_id,
                          home_score,away_score,season,year,day)
                         VALUES(?,?,?,?,-1,-1,?,?,?)""", new_rows)

    conn.commit()
    conn.close()


def _sim_league_full(league_id, season, c=None, st=None, exclude_team_id=None):
    """오퍼 창용: 해당 리그의 현재 주차까지 미완료 경기만 AI 시뮬.
    match_results에만 결과 저장, teams 테이블은 건드리지 않음.
    (순위는 _get_team_rank_info에서 match_results 기준으로 계산)
    과거 시즌이면 전체 주차를 시뮬 (1~4주차 '작년 성적' 계산용 ─ 버그 수정)

    c:  외부에서 연 커서를 재사용 (여러 리그를 한 커넥션으로 처리할 때).
        None이면 자체 커넥션을 열고 닫는다(기존 동작 = 하위 호환).
    st: get_state() 결과 재주입 (루프에서 매번 조회 방지). None이면 직접 조회.
    exclude_team_id: [2026-07 버그수정, 신민용 리포트: "하루씩 진행 중인데
        아직 안 온 내 경기가 미리 결과가 나 있다가, 실제 그날이 되면 다른
        결과로 덮어써진다(무승부→패배로 바뀜)"] week_cap이 '이번 주차까지'
        라 아직 실제로 진행되지 않은 이번 주 경기까지 이 간이 공식(팀
        평균OVR 기반 _roll_outcome/_gen_score)으로 미리 결과를 채워버렸다
        — 그런데 '내 경기'는 나중에 실제 진행되는 날 정식 전술 엔진으로
        따로 한 번 더 시뮬돼서 같은 match_results 행을 다른 결과로
        덮어쓴다. 오퍼 카드의 순위 계산은 원래 어차피 미완료 경기는
        빈칸으로 둬도 되는 용도이므로, 내 팀이 걸린 경기는 애초에 이
        간이 시뮬 대상에서 제외해 실제 경기일까지 미결(-1) 상태로 남겨둔다."""
    if st is None:
        st = get_state()
    cur_week   = st["current_week"]   if st else 11
    cur_season = st["current_season"] if st else 1
    week_cap = 99 if season < cur_season else cur_week

    _own_conn = c is None
    if _own_conn:
        conn = get_conn()
        c = conn.cursor()
    if exclude_team_id:
        c.execute("""SELECT id, home_team_id, away_team_id
                     FROM match_results
                     WHERE league_id=? AND season=? AND home_score=-1 AND week<=?
                       AND home_team_id!=? AND away_team_id!=?""",
                  (league_id, season, week_cap, exclude_team_id, exclude_team_id))
    else:
        c.execute("""SELECT id, home_team_id, away_team_id
                     FROM match_results
                     WHERE league_id=? AND season=? AND home_score=-1 AND week<=?""",
                  (league_id, season, week_cap))
    matches = c.fetchall()

    batch_r = []
    for m in matches:
        hid = m["home_team_id"]
        aid = m["away_team_id"]
        ho = _team_avg_ovr(c, hid) + _home_advantage() + _formation_bias(c, hid)
        ao = _team_avg_ovr(c, aid) + _formation_bias(c, aid)
        diff = ho - ao
        outcome = _roll_outcome(diff)
        hs, as_ = _gen_score(outcome, diff)  # [버그수정] diff 전달
        # teams 테이블 업데이트 없이 match_results에만 저장 (배치 처리)
        batch_r.append((hs, as_, m["id"]))

    if batch_r:
        c.executemany("UPDATE match_results SET home_score=?,away_score=? WHERE id=?", batch_r)

    if _own_conn:
        conn.commit()
        conn.close()


def _backfill_past_matches(league_id, season, current_week, my_team_id):
    """이적 시점 이전에 이미 지나간 주차의 미완료 경기를 일괄 시뮬레이션.
    내 팀이 포함된 경기는 건너뜀(결과 없음으로 두거나, 나중에 처리).
    """
    conn = get_conn()
    c = conn.cursor()

    # current_week 미만이고 아직 결과 없는(-1) 경기들
    c.execute("""SELECT mr.id, mr.home_team_id, mr.away_team_id, mr.week
                 FROM match_results mr
                 WHERE mr.league_id=? AND mr.season=?
                   AND mr.home_score=-1
                   AND mr.week < ?""",
              (league_id, season, current_week))
    matches = c.fetchall()

    team_deltas  = {}
    batch_results = []
    for m in matches:
        hid = m["home_team_id"]
        aid = m["away_team_id"]
        # 내 팀이 포함된 과거 경기도 랜덤으로 처리 (입단 전이니 AI끼리 뛴 것)
        ho = _team_avg_ovr(c, hid) + _home_advantage() + _formation_bias(c, hid)
        ao = _team_avg_ovr(c, aid) + _formation_bias(c, aid)
        diff = ho - ao
        outcome = _roll_outcome(diff)
        hs, as_ = _gen_score(outcome, diff)  # [버그수정] diff 전달
        _accum_team_rec(team_deltas, hid, aid, outcome, hs, as_)
        batch_results.append((hs, as_, m["id"]))

    if batch_results:
        c.executemany("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                      batch_results)
    _flush_team_rec(c, team_deltas)

    conn.commit()
    conn.close()

    if matches:
        add_log(f"📋 이적 전 {len(matches)}경기 결과 일괄 처리 완료", "event")