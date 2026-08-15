# -*- coding: utf-8 -*-
"""
conference_engine.py - 클럽 대륙 컨퍼런스리그급 엔진 (2026-08 신설)

europa_engine.py와 완전히 같은 구조 — 참가팀만 continental_qualification의
"conference" 몫(각국 리그에서 유로파 슬롯 다음 순위, 신민용 확정 슬롯표:
유럽 1~10위 1장/11~23위 2장, 36장/23개국)을 쓴다. 위계상 챔스 > 유로파 >
컨퍼런스라 momentum 세기도 셋 중 가장 약하게(constants.MOMENTUM_SCHEDULES
"uecl_champion") 잡혀 있다.
"""
from database import get_conn
from competition import champions_engine as _cl
from competition.competition_common import CompetitionConfig

ECL_START_WEEK = _cl.CL_START_WEEK
ECL_LEAGUE_WEEKS = _cl.CL_LEAGUE_WEEKS
ECL_PLAYOFF_WEEK = _cl.CL_PLAYOFF_WEEK
ECL_ROUND_WEEKS = _cl.CL_ROUND_WEEKS
ECL_END_WEEK = _cl.CL_END_WEEK

ECL_CUP_NAME = {
    "유럽": "UEFA 컨퍼런스리그",
    "아시아": "AFC 챌린지리그",
    "아프리카": "CAF 아프리카 클럽 챌린지컵",
    "북남미": "아메리카 클럽 챌린지컵",
}

CONFERENCE_CFG = CompetitionConfig(
    match_table="ecl_matches",
    entry_table="ecl_entries",
    tournament_table="ecl_tournaments",
    history_table="ecl_history",
    competition_name_by_continent=ECL_CUP_NAME,
    award_prefix="컨퍼런스리그",
    momentum_type="uecl_champion",
    stage_ko=_cl.STAGE_KO,
    round_weeks=ECL_ROUND_WEEKS,
    league_weeks=ECL_LEAGUE_WEEKS,
    end_week=ECL_END_WEEK,
    stage_order=_cl._STAGE_ORDER,
)


# [2026-08 버그수정, 신민용 리포트: "유로파/컨퍼런스는 대륙 다 36팀인데
# 왜 북남미만 진행 방식이 챔스처럼 안 가?"] europa_engine.py와 동일한
# 이유 — team_cap/direct_cut/playoff_pool을 챔스 전용 대륙별 상수에서
# 떼어내 대륙 무관 고정값(36팀/8경기/8직행/16풀)으로 통일한다.
def _ecl_team_cap(continent):
    return 36


def _ecl_league_games(continent):
    return 8


def _ecl_direct_cut(continent):
    return 8


def _ecl_playoff_pool(continent):
    return 16


def get_ecl_tournament(year, continent):
    from competition.competition_common import get_tournament
    return get_tournament(CONFERENCE_CFG, year, continent)


def _my_ecl_tournament(p, year):
    from competition.competition_common import my_tournament
    return my_tournament(CONFERENCE_CFG, p, year)


def get_my_ecl_match(week, day=None, p=None, st=None):
    from competition.competition_common import get_my_match
    return get_my_match(CONFERENCE_CFG, week, day=day, p=p, st=st)


def has_my_ecl_match_between(week_from, week_to):
    from competition.competition_common import has_my_match_between
    return has_my_match_between(CONFERENCE_CFG, week_from, week_to)


def sim_my_ecl_match_as_ai(week, p, reason="injury", day=None):
    from competition.competition_common import sim_my_match_as_ai
    sim_my_match_as_ai(CONFERENCE_CFG, week, p, get_my_ecl_match, reason=reason, day=day)


def simulate_my_ecl_match(week, p, day=None):
    from competition.competition_common import simulate_my_match
    simulate_my_match(CONFERENCE_CFG, week, p, get_my_ecl_match, day=day)


def get_my_ecl_league_standings(year):
    from competition.competition_common import get_my_league_standings
    from game_engine import get_player
    p = get_player()
    cont = _cl._my_continent(p) if p else None
    if not cont:
        return None
    return get_my_league_standings(CONFERENCE_CFG, year, _ecl_direct_cut(cont), _ecl_playoff_pool(cont))


def get_my_conference_matches(year):
    from competition.competition_common import get_my_matches_for_schedule
    return get_my_matches_for_schedule(CONFERENCE_CFG, year)


def get_my_ecl_matches():
    from competition.competition_common import get_my_matches
    return get_my_matches(CONFERENCE_CFG)


def build_from_qualification(year, continent, entries, my_tid):
    from competition.competition_common import build_tournament
    build_tournament(CONFERENCE_CFG, year, continent, entries, my_tid,
                      team_cap=_ecl_team_cap(continent), games=_ecl_league_games(continent))


def _finalize_league_phase(t):
    from competition.competition_common import finalize_league_phase
    cont = t["continent"]
    finalize_league_phase(CONFERENCE_CFG, t, _ecl_direct_cut(cont), _ecl_playoff_pool(cont),
                           ECL_PLAYOFF_WEEK, _start_knockout)


def _finalize_playoff(t):
    from competition.competition_common import finalize_playoff
    finalize_playoff(CONFERENCE_CFG, t, _start_knockout)


def _start_knockout(t, qualifier_ids, direct_ids=None, winner_ids=None):
    from competition.competition_common import start_knockout
    start_knockout(CONFERENCE_CFG, t, qualifier_ids, ECL_ROUND_WEEKS,
                   direct_ids=direct_ids, winner_ids=winner_ids)


def _advance_round(t, cur_stage, next_stage):
    from competition.competition_common import advance_round
    advance_round(CONFERENCE_CFG, t, cur_stage, next_stage, ECL_ROUND_WEEKS)


def _finish_tournament(t):
    from competition.competition_common import finish_tournament
    finish_tournament(CONFERENCE_CFG, t, award_fn=None)


def process_ecl_week(week):
    from game_engine import get_state
    from competition.competition_common import process_one
    st = get_state()
    if not st:
        return
    year = st["current_year"]
    for cont in ("유럽", "아시아", "아프리카", "북남미"):
        t = get_ecl_tournament(year, cont)
        if not t or t["status"] == "done":
            continue
        league_end_week = ECL_LEAGUE_WEEKS[0] + _ecl_league_games(cont) - 1
        process_one(CONFERENCE_CFG, t, week, league_end_week, ECL_PLAYOFF_WEEK,
                    ECL_ROUND_WEEKS, _cl._STAGE_ORDER,
                    _finalize_league_phase, _finalize_playoff,
                    _advance_round, _finish_tournament)