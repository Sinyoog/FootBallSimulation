# -*- coding: utf-8 -*-
"""
europa_engine.py - 클럽 대륙 유로파리그급 엔진 (2026-08 신설)

champions_engine.py와 구조가 거의 동일하지만, 참가팀 자체가 다르다
(continental_qualification.allocate_continental_slots()의 "europa" 몫 —
각국 리그에서 챔스 슬롯 바로 다음 순위부터 뽑힘, 신민용 확정 슬롯표:
유럽 1~9위 2장/10~27위 1장, 36장/27개국). 나머지(대륙별 정원·리그 스테이지
경기 수·직행/PO 컷)는 챔스와 완전히 같은 비율을 그대로 재사용한다 — 대회
성격이 다를 뿐 대회 "형식"(스위스 방식 리그 스테이지 + 플레이오프 + 토너먼트)
은 같기 때문. 실제 매치 시뮬레이션·진행 로직은 전부 competition_common.py를
그대로 호출한다(champions_engine.py 리팩터링 때 이미 "동일 시드→동일 결과"
검증 완료된 공용 엔진).
"""
from database import get_conn
from competition import champions_engine as _cl
from competition.competition_common import CompetitionConfig
from competition import continental_qualification as _cq

EL_START_WEEK = _cl.CL_START_WEEK          # 챔스와 같은 주차에 동시 개막
EL_LEAGUE_WEEKS = _cl.CL_LEAGUE_WEEKS
EL_PLAYOFF_WEEK = _cl.CL_PLAYOFF_WEEK
EL_ROUND_WEEKS = _cl.CL_ROUND_WEEKS
EL_END_WEEK = _cl.CL_END_WEEK

EL_CUP_NAME = {
    "유럽": "UEFA 유로파리그",
    "아시아": "AFC 챔피언스리그 투",
    "아프리카": "CAF 컨페더레이션컵",
    "남미": "코파 수다메리카나",
    "북미": "콩카카프 리그스컵",
}

EUROPA_CFG = CompetitionConfig(
    match_table="el_matches",
    entry_table="el_entries",
    tournament_table="el_tournaments",
    history_table="el_history",
    competition_name_by_continent=EL_CUP_NAME,
    award_prefix="유로파리그",
    momentum_type="uel_champion",
    stage_ko=_cl.STAGE_KO,
    round_weeks=EL_ROUND_WEEKS,
    league_weeks=EL_LEAGUE_WEEKS,
    end_week=EL_END_WEEK,
    stage_order=_cl._STAGE_ORDER,
)


# ─────────────────────────────────────────────
# 대륙별 규모
# [2026-08 버그수정, 신민용 리포트: "유로파/컨퍼런스는 대륙 다 36팀으로
# 같은데 왜 북남미만 1~16 직행/17~36 플레이오프로 챔스랑 다르게 가?"]
# continental_qualification.QUALIFICATION_TEAM_CAP을 36으로 통일해서
# "누가 참가하는지"는 이미 대륙 무관 36팀으로 고쳐놨는데, "대회를 어떻게
# 진행하는지"(직행/플레이오프 컷)는 이 함수들이 여전히 챔스 전용 상수
# (CL_TEAMS_BY_CONTINENT 등 — 북남미만 48팀 기준 team_cap=48/direct_cut=16/
# playoff_pool=32)를 그대로 물려받고 있었다. 실제 참가자는 36명인데 진행
# 로직은 48명 규모를 가정해서, 나머지(48-16=32명 목표였던) 플레이오프
# 풀이 실제로는 36-16=20명(17~36위)만 채워지는 불일치가 났던 것 — 이게
# "북남미만 1~16 직행/17~36 플레이오프"로 보였던 원인.
# 유로파/컨퍼런스는 대륙 무관하게 항상 유럽과 똑같은 구조(36팀/8경기/
# 8직행/16풀=9~24위 플레이오프)를 쓴다 — continent 인자는 형식만 유지.
# ─────────────────────────────────────────────

def _el_team_cap(continent):
    return 36


def _el_league_games(continent):
    return 8


def _el_direct_cut(continent):
    return 8


def _el_playoff_pool(continent):
    return 16


# ─────────────────────────────────────────────
# 조회
# ─────────────────────────────────────────────

def get_el_tournament(year, continent):
    from competition.competition_common import get_tournament
    return get_tournament(EUROPA_CFG, year, continent)


def _my_el_tournament(p, year):
    from competition.competition_common import my_tournament
    return my_tournament(EUROPA_CFG, p, year)


def get_my_el_match(week, day=None, p=None, st=None):
    """[2026-08 리팩터링] competition_common.get_my_match로 이동
    (완전 동일 로직) — 위임만."""
    from competition.competition_common import get_my_match
    return get_my_match(EUROPA_CFG, week, day=day, p=p, st=st)


def has_my_el_match_between(week_from, week_to):
    from competition.competition_common import has_my_match_between
    return has_my_match_between(EUROPA_CFG, week_from, week_to)


def sim_my_el_match_as_ai(week, p, reason="injury", day=None):
    from competition.competition_common import sim_my_match_as_ai
    sim_my_match_as_ai(EUROPA_CFG, week, p, get_my_el_match, reason=reason, day=day)


def simulate_my_el_match(week, p, day=None):
    from competition.competition_common import simulate_my_match
    simulate_my_match(EUROPA_CFG, week, p, get_my_el_match, day=day)


def get_my_el_league_standings(year):
    from competition.competition_common import get_my_league_standings
    from game_engine import get_player
    p = get_player()
    cont = _cl._my_continent(p) if p else None
    if not cont:
        return None
    return get_my_league_standings(EUROPA_CFG, year, _el_direct_cut(cont), _el_playoff_pool(cont))


def get_my_europa_matches(year):
    from competition.competition_common import get_my_matches_for_schedule
    return get_my_matches_for_schedule(EUROPA_CFG, year)


def get_my_el_matches():
    from competition.competition_common import get_my_matches
    return get_my_matches(EUROPA_CFG)


# ─────────────────────────────────────────────
# 대회 생성 — continental_qualification이 이미 계산해둔 "europa" 몫을
# 그대로 받아서 대회 셸만 만든다(국가 슬롯/워터폴 재계산 없음).
# ─────────────────────────────────────────────

def build_from_qualification(year, continent, entries, my_tid):
    """entries: continental_qualification.allocate_continental_slots()의
    alloc["europa"] 중 이 대륙 몫(이미 team_id/team_name/flag/country/grade/
    ovr 다 갖춘 상태)."""
    from competition.competition_common import build_tournament
    build_tournament(EUROPA_CFG, year, continent, entries, my_tid,
                      team_cap=_el_team_cap(continent), games=_el_league_games(continent))


# ─────────────────────────────────────────────
# 주차 처리
# ─────────────────────────────────────────────

def _finalize_league_phase(t):
    from competition.competition_common import finalize_league_phase
    cont = t["continent"]
    finalize_league_phase(EUROPA_CFG, t, _el_direct_cut(cont), _el_playoff_pool(cont),
                           EL_PLAYOFF_WEEK, _start_knockout)


def _finalize_playoff(t):
    from competition.competition_common import finalize_playoff
    finalize_playoff(EUROPA_CFG, t, _start_knockout)


def _start_knockout(t, qualifier_ids, direct_ids=None, winner_ids=None):
    from competition.competition_common import start_knockout
    start_knockout(EUROPA_CFG, t, qualifier_ids, EL_ROUND_WEEKS,
                   direct_ids=direct_ids, winner_ids=winner_ids)


def _advance_round(t, cur_stage, next_stage):
    from competition.competition_common import advance_round
    advance_round(EUROPA_CFG, t, cur_stage, next_stage, EL_ROUND_WEEKS)


def _finish_tournament(t):
    from competition.competition_common import finish_tournament
    # [2026-08 신설] 시상(득점왕 등)은 아직 안 옮겼다(챔스 쪽도 마찬가지로
    # award_fn=None이면 시상 단계만 건너뜀) — 1차는 대진/결과/우승팀부터.
    finish_tournament(EUROPA_CFG, t, award_fn=None)


def process_el_week(week):
    """CL_START_WEEK부터 매주 호출 — 5개 대륙 유로파 전부 처리."""
    from game_engine import get_state
    from competition.competition_common import process_one, resync_my_registration
    st = get_state()
    if not st:
        return
    year = st["current_year"]
    # [2026-09 신설] 시즌 중 이적으로 소속팀이 바뀌었으면 먼저 대회 등록을
    # 맞춘다 — 아래 is_my 판정이 전부 이 값을 전제로 한다
    # (competition_common.resync_my_registration 주석 참고, 바뀐 게 없으면
    #  SELECT 1~2회로 끝난다).
    try:
        resync_my_registration(EUROPA_CFG, year)
    except Exception as _e:
        print("[EL] resync_my_registration 실패(건너뜀):", _e, flush=True)
    for cont in ("유럽", "아시아", "아프리카", "남미", "북미"):
        t = get_el_tournament(year, cont)
        if not t or t["status"] == "done":
            continue
        league_end_week = EL_LEAGUE_WEEKS[0] + _el_league_games(cont) - 1
        process_one(EUROPA_CFG, t, week, league_end_week, EL_PLAYOFF_WEEK,
                    EL_ROUND_WEEKS, _cl._STAGE_ORDER,
                    _finalize_league_phase, _finalize_playoff,
                    _advance_round, _finish_tournament)