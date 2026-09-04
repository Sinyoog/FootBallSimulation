# -*- coding: utf-8 -*-
"""
super_cup_engine.py — 대륙별 슈퍼컵 (2026-08 신설, 10순위)

[신민용 확정 설계]
대륙        대회명
아시아      AFC 그랜드 슈퍼컵
유럽        UEFA 슈퍼컵 플러스
아프리카    CAF 슈퍼컵 프리미어
남북미      APF 그랜드 슈퍼컵

연 1회, 대륙별 참가 4팀 = 그 대륙 그 해의:
  1) 챔피언스(대륙 최상위 클럽대항전) 우승팀
  2) 챔피언스 준우승팀
  3) 유로파급(2단계) 클럽대항전 우승팀
  4) 컨퍼런스급(3단계) 클럽대항전 우승팀

[2026-08 수정, 신민용 리포트: "4강에서 이긴 애들은 1/2위(결승)로, 진
애들은 3/4위전으로 가서 1234등이 다 정해져야 한다"] 처음엔 "총 3경기
(준결승2+결승, 3/4위전 없음)"로 설계했는데, 그러면 준결승에서 진 두
팀을 3위/4위로 구분할 방법이 없다(둘 다 그냥 "4강 탈락"으로 묶임).
1~4위를 전부 확정하려면 3/4위전이 있어야 하므로, CL/EL/ECL이 이미
쓰고 있는 competition_common.start_knockout/advance_round/
finish_tournament(SF 스테이지에서 진 두 팀을 자동으로 3/4위전에 배정)를
그대로 재사용한다 — 이 프레임워크가 "1~4위 전부 확정"을 이미 정확하게
검증된 방식으로 처리해준다. 총 경기 수는 4경기(준결승1/준결승2/
3·4위전/결승)로 CL 방식과 동일해졌다.

경기 스케줄: 한 주 안에서 Day1(준결승1+준결승2) → Day6(3·4위전+결승).
[신민용 원안(Day1/Day3/Day6)에서 준결승 두 경기를 같은 날(Day1)로
합쳤다 — start_knockout이 준결승 두 경기를 한 번에 만들기 때문에
day를 다르게 주려면 생성 직후 별도 UPDATE가 한 번 더 필요한데, 그정도
차이는 "한 주 안에 끝난다"는 원래 취지에 영향이 없어 단순화했다.]

[season_id 설계 결정 — 신민용이 명확화를 요청한 지점]
"2060 시즌"의 슈퍼컵은 "2060년 챔스/유로파/컨퍼런스"를 그대로 쓴다(신민용이
제시한 두 옵션 중 첫 번째: "2060 챔스 우승/준우승/2060 유로파 우승/2060
컨퍼런스 우승", 2060년 챔스/유로파/컨퍼런스가 전부 끝나면 그 4팀으로
바로 그 해 슈퍼컵을 연다). "상반기 대회 → 하반기 슈퍼컵"처럼 시즌을
넘겨서 직전 시즌 우승팀을 갖고 오는 방식은 채택하지 않았다 — 이유:
  1) 이 게임의 대륙대항전(cl/el/ecl_tournaments)은 애초에 "그 해(year)
     하나" 단위로 완결되는 구조라(champions_engine.CL_START_WEEK~
     CL_END_WEEK가 전부 같은 시즌 안), "직전 시즌 우승팀"을 넘겨받으려면
     그 우승팀이 "다음 시즌에도 여전히 존재/식별 가능"해야 하는데 —
     팀은 매 시즌 리그가 바뀌거나(승강) 심지어 사라질 수도 있어(팀
     삭제/재편) 그 사이 상태가 안전하게 보존된다는 보장이 없다.
  2) "2060 시즌 첫 슈퍼컵"에 넘겨줄 "2059 시즌 우승팀"이 없는 게임
     시작 초반(예: 게임 시작 연도 자체) 부트스트랩 문제가 아예 없어진다
     — 같은 해 안에서 다 끝나므로 항상 "이번 해에 확정된 챔피언"이
     존재할 때만 슈퍼컵이 열린다.
  3) cl/el/ecl_tournaments가 전부 'year' 컬럼 하나로 이미 시즌을
     식별하고 있어서(별도 season_id 개념이 게임에 아예 없음), 새
     개념을 추가하지 않고 기존 'year' 그대로 재사용할 수 있다.
캘린더상으로도 챔스/유로파/컨퍼런스가 전부 CL_END_WEEK(23주차)에
끝나므로, 슈퍼컵을 그보다 뒤(SC_START_WEEK=25주차)에 두면 "이번 해 3개
대회가 전부 끝난 뒤 곧바로 이번 해 슈퍼컵"이 자연스럽게 성립한다.

[내가 경기를 안 뛰어도 기록이 남아야 한다] sim_ai_match/finish_tournament/
record_my_exit는 CL/EL/ECL과 완전히 같은 공용 함수라, 내 팀이 참가하지
않는 슈퍼컵도 다른 대회들과 동일하게 매 시즌 자동으로 끝까지 진행되고
sc_tournaments/sc_history/trophy_log에 그대로 기록이 남는다 — 이건
"AI끼리 진행되는 대회는 자동 시뮬"이라는 이 게임의 기존 원칙을 그대로
따른 것뿐, 슈퍼컵만 특별히 다르게 동작하지 않는다.

[구현 범위] club_world_cup_engine.py와 동일한 전례를 따른다 — 내 팀이
슈퍼컵에 진출해도 지금은 인터랙티브 뷰어 연동 없이 AI전과 동일하게
OVR 기반으로 자동 시뮬레이션된다(로그/보상/momentum/개인기록 집계는
정상 반영). 실제 조작 가능한 경기로 만들려면 match_flow.py 쪽 작업이
별도로 필요하다.
"""
from database import get_conn
from constants import week_to_day, day_to_week
from competition.competition_common import (
    CompetitionConfig, entry, sim_ai_match, winner_of,
    start_knockout, advance_round, finish_tournament,
    get_tournament, clear_entry_cache,
)
from competition.champions_engine import CHAMPIONS_CFG
from competition.europa_engine import EUROPA_CFG
from competition.conference_engine import CONFERENCE_CFG

SUPER_CUP_NAME = {
    "아시아": "AFC 그랜드 슈퍼컵",
    "유럽": "UEFA 슈퍼컵 플러스",
    "아프리카": "CAF 슈퍼컵 프리미어",
    "남미": "코메볼 그랜드 슈퍼컵",
    "북미": "콩카카프 그랜드 슈퍼컵",
}

# CL_END_WEEK(23주차)에 챔스/유로파/컨퍼런스가 전부 끝나므로, 그 뒤로
# 여유를 두고 25주차에 시작한다. 한 주(7일) 안에서 Day1(준결승) → Day6(3/4위전+결승).
# [2026-08 v3.5 재수정, 신민용 리포트: "24주차는 국제예선~휴식기(25~28주)
# 이전이라 아직 상반기 끝자락이지, 진짜 하반기가 아니다 — 국내컵 결승이
# 뒤로 밀리는지도 같이 생각해야 한다"] 정확한 지적 — 진짜 "하반기"는
# 국제예선~휴식기(INTL_QUAL_WEEK~휴식기끝, 실측 25~28주)가 끝난 뒤인
# 29주차부터다. 슈퍼컵을 29주차로 당기고, cup_engine.CUP_ROUND_WEEKS_POOL
# 쪽의 국내컵 1부 합류 라운드를 30주차로 재조정했다 — 이러면 뒤 라운드
# 간격을 굳이 좁힐 필요도 없이(2주가 아니라 원래대로 2주 그대로 둬도)
# 국내컵 마지막 라운드가 원래(이 수정 전) 위치인 42주차 그대로 유지되어
# 결승이 전혀 안 밀린다(실측 확인).
SC_START_WEEK = 29
SC_STAGE_KO = {"SF": "4강", "F": "결승", "TP": "3/4위전"}
# [2026-08 수정, 11순위] "준결승"/"3·4위전" 대신 champions_engine.STAGE_KO와
# 완전히 같은 표기("4강"/"3/4위전")로 맞췄다 — schedule_window.py의
# 대진표(BracketWidget) 정렬용 stage_order 딕셔너리가 정확히 이 문자열
# ("4강","결승","3/4위전")을 키로 쓰기 때문에, 다르게 쓰면 슈퍼컵 대진표만
# 정렬이 깨진다.
# [버그수정] competition_common.start_knockout이 first_stage="SF"일 때도
# 폴백 기본값 표현식 round_weeks["R16"]을 항상 먼저 평가한다(dict.get의
# 두 번째 인자는 호출 전에 무조건 계산되는 파이썬 특성) — SF 하나만
# 있으면 존재하지도 않는 "R16" 키를 찾다가 KeyError가 난다. 실제로 안
# 쓰더라도 KeyError 방지용으로 R32/R16/QF까지 전부 채워둔다.
SC_ROUND_WEEKS = {"R32": SC_START_WEEK, "R16": SC_START_WEEK, "QF": SC_START_WEEK,
                   "SF": SC_START_WEEK, "F": SC_START_WEEK, "TP": SC_START_WEEK}

SC_CFG = CompetitionConfig(
    match_table="sc_matches",
    entry_table="sc_entries",
    tournament_table="sc_tournaments",
    history_table="sc_history",
    competition_name_by_continent=SUPER_CUP_NAME,
    award_prefix="슈퍼컵",
    momentum_type="super_cup_champion",
    stage_ko=SC_STAGE_KO,
    round_weeks=SC_ROUND_WEEKS,
    league_weeks=(SC_START_WEEK, SC_START_WEEK),
    end_week=SC_START_WEEK,
    stage_order=["SF", "F"],
    # [2026-08 신설, 옐로카드 시스템] 슈퍼컵은 카드 누적 그룹상 유럽대항전
    # (챔스/유로파/컨퍼런스)과 별개다 — 지금까지는 competition_common.
    # simulate_my_match의 기본값(cl_suspension)을 그대로 물려받아 셋과
    # 같은 카운터를 썼는데(버그), 이제 전용 필드로 분리한다.
    suspension_field="super_cup_suspension",
)

_CONTINENTS = ("유럽", "아시아", "아프리카", "남미", "북미")
_SF_DAY = week_to_day(SC_START_WEEK)          # Day1 — 준결승 2경기
_FINAL_DAY = _SF_DAY + 5                       # Day6 — 3·4위전 + 결승


def _pick_sc_days(my_tid, cur_season):
    """[2026-08 버그수정, 신민용 리포트: "슈퍼컵이 경기가 진행됐다고는
    뜨는데 일정엔 안 보인다 — 리그랑 겹치면서 그러는 것 같다"] 원래
    SF/결승 요일(_SF_DAY=그 주 1일차="일요일", _FINAL_DAY=+5일="금요일")은
    고정값이라 국내리그와 실제로 같은 날 겹칠 수 있었다 — 특히 SF 요일
    (일요일)은 game_engine._week_intl_cl_day(챔스/유로파/컨퍼런스/국내컵이
    쓰는 국내리그 안 겹치는 요일 자동선택, 화→금→수→목→월→토→일 순으로
    시도)가 정확히 "다른 요일이 전부 겹쳐야만 겨우 고르는 최후순위"로
    다루는 요일이다 — 그만큼 국내리그가 자주 놓이는 요일이라는 뜻인데,
    슈퍼컵 SF는 하필 그 요일에 못박혀 있었다. 같은 팀이 같은 날 국내리그
    경기와 슈퍼컵 경기를 동시에 갖는 데이터 자체가 잘못이므로(화면
    표시뿐 아니라 실제로도 한 팀이 하루에 두 경기를 뛰는 셈), _week_
    intl_cl_day와 같은 원리(주변 주차 국내 경기일과 하루 이내로는 안
    겹치게)로 "SF/결승 요일 조합" 후보 중 국내 경기와 안 겹치는 조합을
    고른다. 후보는 (기존 기본값인 일+금) 다음으로 (월+토) 단 두 개뿐이다
    — 결승이 SF보다 5일 뒤여야 한다는 설계(한 주 안에서 Day1→Day6)를
    지키면서 그 주(week_start~+6) 안에 들어가려면 SF는 0 또는 1 오프셋
    (그래야 SF+5가 6을 넘지 않음)만 가능하기 때문이다. 둘 다 겹치면
    (극히 드문 경우) 기존 기본값을 그대로 쓴다 — 이전과 다를 바 없는
    상황이니 나빠지진 않는다. 내 팀이 이 대회에 참가하지 않으면(my_tid=0,
    AI끼리만 진행되는 나머지 3개 대륙) 국내 일정을 조회할 필요가 아예
    없어 기본값을 그대로 쓴다 — AI 팀끼리는 겹쳐도 화면에 보이는 문제가
    없고, 매번 이 조회를 하면 성능만 낭비된다."""
    if not my_tid:
        return _SF_DAY, _FINAL_DAY
    conn = get_conn()
    rows = conn.execute(
        """SELECT day FROM match_results WHERE week IN (?,?,?) AND season=?
           AND day IS NOT NULL AND (home_team_id=? OR away_team_id=?)""",
        (SC_START_WEEK - 1, SC_START_WEEK, SC_START_WEEK + 1, cur_season,
         my_tid, my_tid)).fetchall()
    conn.close()
    dom_days = [r["day"] for r in rows if r["day"] is not None]

    def _conflicts(cand):
        return any(abs(cand - dd) <= 1 for dd in dom_days if dd is not None)

    week_start = _SF_DAY
    for sf_off, f_off in ((0, 5), (1, 6)):
        sf_cand, f_cand = week_start + sf_off, week_start + f_off
        if not _conflicts(sf_cand) and not _conflicts(f_cand):
            return sf_cand, f_cand
    return _SF_DAY, _FINAL_DAY

# [2026-08 최적화, club_world_cup_engine.py와 동일한 이유] 이 주차 이하로
# 처리할 슈퍼컵 대회가 하나도 없는 날엔 DB 조회 없이 바로 빠지는 캐시.
_sc_has_active_cache = None


def _invalidate_sc_active_cache():
    global _sc_has_active_cache
    _sc_has_active_cache = None


# ─────────────────────────────────────────────
# 참가팀 확정 (챔스/유로파/컨퍼런스 3개 대회가 전부 끝난 뒤)
# ─────────────────────────────────────────────

def _finalist_pair(match_table, tournament_id):
    """그 대회 결승(F) 매치에서 (우승팀, 준우승팀) team_id를 뽑는다.
    결승이 아직 없거나 안 끝났으면 (None, None)."""
    conn = get_conn()
    m = conn.execute(
        f"""SELECT * FROM {match_table} WHERE tournament_id=? AND stage='F'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tournament_id,)).fetchone()
    conn.close()
    if not m:
        return None, None
    m = dict(m)
    winner = winner_of(m)
    runner_up = m["away_team_id"] if winner == m["home_team_id"] else m["home_team_id"]
    return winner, runner_up


def _set_match_days(tid, stage, day):
    """start_knockout/advance_round는 week만 채우고 day는 안 채운다
    (기본 0) — 이 대회는 day 기반 스케줄(Day1/Day6)을 쓰므로, 생성
    직후 그 스테이지의 매치들에 day를 일괄로 채워 넣는다."""
    conn = get_conn()
    conn.execute("UPDATE sc_matches SET day=? WHERE tournament_id=? AND stage=?",
                 (day, tid, stage))
    conn.commit()
    conn.close()


def _build_super_cup(year, continent):
    """그 해·그 대륙의 챔스/유로파/컨퍼런스가 전부 status='done'이면
    4팀을 확정하고 준결승 대진(start_knockout)을 만든다. 이미 만들어져
    있으면 아무것도 안 함. 셋 중 하나라도 아직 안 끝났으면 조용히
    넘어간다(다음 날 다시 확인)."""
    if get_tournament(SC_CFG, year, continent):
        return   # 이미 생성됨

    cl_t = get_tournament(CHAMPIONS_CFG, year, continent)
    el_t = get_tournament(EUROPA_CFG, year, continent)
    ecl_t = get_tournament(CONFERENCE_CFG, year, continent)
    if not (cl_t and el_t and ecl_t):
        return
    if not (cl_t["status"] == "done" and el_t["status"] == "done" and ecl_t["status"] == "done"):
        return

    cl_champion, cl_runner_up = _finalist_pair("cl_matches", cl_t["id"])
    if not (cl_champion and cl_runner_up):
        return
    el_champion = el_t["winner_team_id"] or None
    ecl_champion = ecl_t["winner_team_id"] or None
    if not (el_champion and ecl_champion):
        return

    seeds = [
        (cl_champion, "cl_champion", CHAMPIONS_CFG, cl_t["id"]),
        (cl_runner_up, "cl_runner_up", CHAMPIONS_CFG, cl_t["id"]),
        (el_champion, "el_champion", EUROPA_CFG, el_t["id"]),
        (ecl_champion, "ecl_champion", CONFERENCE_CFG, ecl_t["id"]),
    ]
    # [방어적 처리] 정상적으로는 4팀이 겹칠 수 없다(같은 시즌에 한 팀이
    # 서로 다른 두 단계 대회 결승에 동시에 있을 수 없음 — continental_
    # qualification.py가 대회별로 슬롯을 배타적으로 나눠 배정하므로).
    # 그래도 데이터가 꼬여 겹치면 슈퍼컵 자체를 건너뛴다(어설프게 3팀
    # 대회를 여는 것보다 안전).
    if len({s[0] for s in seeds}) != 4:
        from game_engine import add_log
        add_log(f"⚠️ {SUPER_CUP_NAME.get(continent, '슈퍼컵')}({year}년): "
                f"참가 4팀 중 겹치는 팀이 있어 이번 해는 건너뜁니다.", "event")
        return

    name = SUPER_CUP_NAME.get(continent, "슈퍼컵")
    from game_engine import get_player, get_state
    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    my_in = 1 if any(s[0] == my_tid for s in seeds) else 0
    _st = get_state()
    _cur_season = _st["current_season"] if _st else 1
    sf_day, final_day = _pick_sc_days(my_tid if my_in else 0, _cur_season)

    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO sc_tournaments(year, continent, name, status,
                    my_in, my_team_id, my_qualified)
                 VALUES(?,?,?,?,?,?,?)""",
              (year, continent, name, "sf", my_in, my_tid if my_in else 0, my_in))
    tid = c.lastrowid

    entry_rows = []
    for team_id, role, src_cfg, src_tid in seeds:
        src = entry(src_cfg, src_tid, team_id)
        entry_rows.append((tid, team_id, src.get("team_name", "?"), src.get("flag", ""),
                            src.get("country", ""), src.get("grade", ""),
                            src.get("ovr", 50), role))
    c.executemany("""INSERT INTO sc_entries
                        (tournament_id, team_id, team_name, flag, country,
                         grade, ovr, alive, seed_role)
                     VALUES(?,?,?,?,?,?,?,1,?)""", entry_rows)
    conn.commit()
    conn.close()
    clear_entry_cache()

    # 준결승 대진 — start_knockout(CL/EL/ECL과 동일한 공용 로직)이 4팀을
    # OVR 기준으로 1v4/2v3으로 짝짓는다.
    t = get_tournament(SC_CFG, year, continent)
    start_knockout(SC_CFG, t, [s[0] for s in seeds], SC_ROUND_WEEKS)
    _set_match_days(tid, "SF", sf_day)
    # [2026-08 버그수정, 신민용 리포트: "챔스는 기록이 남는데 슈퍼컵은
    # 1년 다 돌려도 대회만 생기고 경기가 하나도 시뮬 안 된다"] 실제
    # 세이브로 재현됨: process_super_cup_week가 맨 처음(아직 챔스/유로파/
    # 컨퍼런스가 하나도 안 끝난 날) 호출됐을 때 "처리할 대회가 없다"고
    # 판단해 _sc_has_active_cache=False로 캐시해버린다. 그런데 여기서
    # 나중에(3개 대회가 다 끝난 뒤) 대회를 실제로 만들어도, 이 캐시
    # 무효화를 안 해주면 process_super_cup_week가 "캐시상 처리할 게
    # 없다"고 믿고 매번 맨 위에서 바로 return해버려서 — 방금 만든
    # 준결승 경기를 영원히 시뮬레이션 하지 않는다(sc_tournaments엔
    # 대회가 생겼는데 sc_matches는 전부 -1로 멈춰있는 상태로 확인됨).
    # 대회를 실제로 만들 때마다 캐시를 무효화해서, 다음 호출이 다시
    # DB를 확인하게 한다.
    _invalidate_sc_active_cache()

    from game_engine import add_log
    cl_e = entry(CHAMPIONS_CFG, cl_t["id"], cl_champion)
    add_log(f"🏆 {year}년 {name} 참가팀 확정: {cl_e.get('flag','')}{cl_e.get('team_name','?')} "
            f"외 3팀 ({SC_STAGE_KO['SF']} {day_to_week(_SF_DAY)}주차)", "event")


def get_my_super_cup_matches(year):
    """[2026-08 신설, 11순위] 경기 일정 화면(schedule_window.py)이 쓰는
    공용 함수 — CL/EL/ECL의 get_my_champions_matches/get_my_europa_matches/
    get_my_conference_matches와 이름 패턴만 다르고 완전히 같은 방식으로
    competition_common.get_my_matches_for_schedule(SC_CFG, year)를 그대로
    부른다."""
    from competition.competition_common import get_my_matches_for_schedule
    return get_my_matches_for_schedule(SC_CFG, year)


def get_my_super_cup_match(week, day=None, p=None, st=None):
    """[2026-08 신설, 11순위] center_panel.py가 "이번 주 내 일정"을 확인할
    때 쓰는 함수 — CL/EL/ECL의 get_my_cl_match/get_my_el_match/
    get_my_ecl_match와 동일한 반환 형식(competition_common.get_my_match가
    이미 그 형식으로 준다)."""
    from competition.competition_common import get_my_match
    return get_my_match(SC_CFG, week, day=day, p=p, st=st)


def has_my_super_cup_match_between(week_from, week_to):
    """[2026-08 신설, 11순위] center_panel._check_match가 "이번 주에 내
    경기가 있는지"를 확인할 때 쓴다 — 클럽월드컵 등과 동일한 패턴."""
    from competition.competition_common import has_my_match_between
    return has_my_match_between(SC_CFG, week_from, week_to)


def simulate_my_super_cup_match(week, p, day=None):
    """[2026-08 신설, 11순위] 내가 슈퍼컵에 참가했을 때(연 1회, 4팀 중
    하나일 때만) 그 경기를 처리한다. club_world_cup_engine.py와 동일한
    전례를 따라, 지금은 인터랙티브 뷰어 없이 AI전과 동일한 OVR 기반
    자동 시뮬레이션이다(개인 기록·명성·momentum은 정상 반영) — 진짜
    조작 가능한 경기로 만들려면 match_flow.py 쪽 작업이 별도로 필요하다."""
    from competition.competition_common import simulate_my_match
    simulate_my_match(SC_CFG, week, p, get_my_super_cup_match, day=day)


def sim_my_super_cup_match_as_ai(week, p, reason="injury", day=None):
    """[2026-08 신설, 11순위] 부상/출전정지 등으로 내가 못 뛸 때(AI로 대신
    처리) — CL/EL/ECL과 동일한 패턴."""
    from competition.competition_common import sim_my_match_as_ai
    sim_my_match_as_ai(SC_CFG, week, p, get_my_super_cup_match, reason=reason, day=day)


def get_my_sc_matches():
    """[2026-08 신설, 14순위] career_window.py/retire_window.py가 쓰는
    "내 슈퍼컵 개인기록 전체" 조회 — CL/EL/ECL의 get_my_cl_matches/
    get_my_el_matches/get_my_ecl_matches와 이름 패턴만 다르고 완전히
    같은 방식으로 competition_common.get_my_matches(SC_CFG)를 그대로
    부른다."""
    from competition.competition_common import get_my_matches
    return get_my_matches(SC_CFG)


def _ensure_all_continents(year):
    for cont in _CONTINENTS:
        _build_super_cup(year, cont)


# ─────────────────────────────────────────────
# 진행 (준결승 → 3·4위전+결승 → 종료)
# ─────────────────────────────────────────────

def process_super_cup_week(week, day=None):
    """게임 진행 루프(매일 호출)에서 호출 — 그 해 4개 대륙 슈퍼컵을 각각
    확인한다. club_world_cup_engine.process_cwc_week와 동일한 캐시/day
    기반 처리 패턴(그날까지 온 미완료 경기만 멱등하게 시뮬).

    [2026-08 수정] 준결승→(3·4위전+결승) 전환과 최종 집계는 competition_
    common.advance_round/finish_tournament(CL과 완전히 동일한 검증된
    로직)에 그대로 맡긴다 — 이 함수는 "이번 주까지 온 미완료 경기를
    시뮬레이션하고, 스테이지가 끝났으면 다음 단계로 넘긴다"는 오케스트
    레이션만 담당한다."""
    global _sc_has_active_cache
    from game_engine import get_state
    st = get_state()
    if not st:
        return
    year = st["current_year"]

    # 이 해에 아직 안 만들어진 대륙 슈퍼컵이 있으면 만들어본다(3개 대회가
    # 전부 끝났을 때만 실제로 생성됨 — 그 전엔 조용히 아무 일도 안 함).
    _ensure_all_continents(year)

    if _sc_has_active_cache is False:
        return
    conn = get_conn()
    if _sc_has_active_cache is not True:
        pending = conn.execute(
            "SELECT 1 FROM sc_tournaments WHERE status!='done' LIMIT 1").fetchone()
        if not pending:
            _sc_has_active_cache = False
            conn.close()
            return
        _sc_has_active_cache = True

    tours = [dict(r) for r in conn.execute(
        "SELECT * FROM sc_tournaments WHERE status!='done'").fetchall()]
    conn.close()
    if not tours:
        return

    from game_engine import get_player
    p = get_player()
    for t in tours:
        conn = get_conn()
        if day is not None:
            matches = [dict(r) for r in conn.execute(
                """SELECT * FROM sc_matches WHERE tournament_id=? AND home_score=-1
                   AND ((day IS NOT NULL AND day>0 AND day<=?) OR
                        ((day IS NULL OR day=0) AND week<=?))
                   ORDER BY id""", (t["id"], day, week)).fetchall()]
        else:
            matches = [dict(r) for r in conn.execute(
                """SELECT * FROM sc_matches WHERE tournament_id=? AND week<=?
                   AND home_score=-1 ORDER BY id""", (t["id"], week)).fetchall()]
        conn.close()
        if matches:
            conn = get_conn()
            for m in matches:
                sim_ai_match(SC_CFG, t, m, conn=conn, p=p)
            conn.commit()
            conn.close()

        # 이번에 뭘 처리했든 안 했든, 스테이지 전환 조건은 매번 다시 검사한다
        # (예: 어제 이미 준결승이 끝났는데 결승 생성이 아직 안 됐을 수 있음).
        conn = get_conn()
        sf = [dict(r) for r in conn.execute(
            "SELECT * FROM sc_matches WHERE tournament_id=? AND stage='SF' ORDER BY slot",
            (t["id"],)).fetchall()]
        f_exists = conn.execute(
            "SELECT 1 FROM sc_matches WHERE tournament_id=? AND stage='F' LIMIT 1",
            (t["id"],)).fetchone()
        conn.close()

        if sf and all(m["home_score"] >= 0 for m in sf) and not f_exists:
            advance_round(SC_CFG, t, "SF", "F", SC_ROUND_WEEKS)
            # [2026-08 버그수정] SF와 같은 (my_tid, season) 조합으로 다시
            # 고르면 _pick_sc_days가 결정적이라 SF 생성 때와 정확히 같은
            # 결승 요일이 나온다 — 별도로 저장해둘 필요 없이 매번 다시
            # 계산해도 안전하다(주변 주차 국내 경기일은 시즌 시작 때
            # 이미 확정되어 이후 안 바뀜).
            _, f_day = _pick_sc_days(t["my_team_id"] if t.get("my_in") else 0,
                                      st["current_season"])
            _set_match_days(t["id"], "F", f_day)
            _set_match_days(t["id"], "TP", f_day)
            _invalidate_sc_active_cache()   # 새로 생긴 경기가 있으니 캐시 갱신
            continue

        if f_exists:
            conn = get_conn()
            f_done = conn.execute(
                "SELECT 1 FROM sc_matches WHERE tournament_id=? AND stage='F' "
                "AND home_score>=0 LIMIT 1", (t["id"],)).fetchone()
            conn.close()
            if f_done:
                finish_tournament(SC_CFG, t, award_fn=None)
                _invalidate_sc_active_cache()