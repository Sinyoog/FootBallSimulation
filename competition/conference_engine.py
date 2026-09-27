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
    "남미": "코파 데 라 리가",
    "북미": "콩카카프 센트럴컵",
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
    neutral_stages=("F", "TP"),
)


# [2026-08 버그수정, 신민용 리포트: "유로파/컨퍼런스는 대륙 다 36팀인데
# 왜 북남미만 진행 방식이 챔스처럼 안 가?"] europa_engine.py와 동일한
# 이유 — team_cap/direct_cut/playoff_pool을 챔스 전용 대륙별 상수에서
# 떼어내 대륙 무관 고정값(36팀/8경기/8직행/16풀)으로 통일한다.
def _ecl_team_cap(continent):
    # [2026-09 3차 수정, 신민용 확정: "ECL은 참가팀이 리그 순위에 맞춰 뽑혀
    # 나가는 36팀 대회다 — 아시아 모든 팀이 참가하는 대회가 아니다. 정원
    # 36은 고정이고, 그 36자리를 어느 나라 몇 위 팀에게 주느냐만 배분
    # 규칙이 정한다"] 2차 수정에서 "3위~최하위 전원 최소 1장"을 만족시키려고
    # 정원 자체를 국가 수에 맞춰 52(유럽/아프리카)/56(아시아)까지 늘렸던 건
    # 대회 규격을 깬 잘못된 수정이었다 — 정원은 다시 고정값으로 돌렸고,
    # 36자리 배분은 continental_qualification._conference_plan이 우선순위
    # 밴드로 처리한다.
    # 정원 값 자체는 계속 배분 쪽(continental_qualification.conference_team_cap)
    # 에서 가져온다 — 두 파일에 서로 다른 숫자가 박히는 일이 없게 하려는
    # 기존 의도는 그대로 유지(예전엔 여기 36, QUALIFICATION_TEAM_CAP에 36이
    # 따로 있어서 어긋날 수 있었다).
    # 실측: 유럽 36 · 아시아 36 · 아프리카 36 · 북미 32 · 남미 0
    # (남미는 컨퍼런스를 열지 않는다 — CONFERENCE_TEAM_CAP 주석 참고. 참가팀이
    #  4팀 미만이면 대회를 안 만드는 기존 가드가 있어 자동으로 안 열린다).
    try:
        from competition.continental_qualification import conference_team_cap
        return conference_team_cap(continent)
    except Exception:
        return 36   # 어떤 이유로든 실패하면 예전 동작 그대로


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
        resync_my_registration(CONFERENCE_CFG, year)
    except Exception as _e:
        print("[ECL] resync_my_registration 실패(건너뜀):", _e, flush=True)
    for cont in ("유럽", "아시아", "아프리카", "남미", "북미"):
        t = get_ecl_tournament(year, cont)
        if not t or t["status"] == "done":
            continue
        league_end_week = ECL_LEAGUE_WEEKS[0] + _ecl_league_games(cont) - 1
        process_one(CONFERENCE_CFG, t, week, league_end_week, ECL_PLAYOFF_WEEK,
                    ECL_ROUND_WEEKS, _cl._STAGE_ORDER,
                    _finalize_league_phase, _finalize_playoff,
                    _advance_round, _finish_tournament)