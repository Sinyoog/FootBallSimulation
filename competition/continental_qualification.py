# -*- coding: utf-8 -*-
"""
[2026-08 신설, 신민용 설계 확정: "챔스/유로파급/컨퍼런스급 3단계 대륙대항전"]

한 대륙 안에서 국가별로 "챔스 슬롯 → 유로파 슬롯 → 컨퍼런스 슬롯" 순서로
슬롯을 배분하고, 각 나라 리그 순위표에서 위에서부터 그만큼씩 떼어가며
아래로 승계한다(워터폴) — 같은 팀이 두 대회에 동시에 뽑히는 일이
구조적으로 불가능하다.

이 모듈은 champions_engine.py(챔스 대회 진행 자체)와 완전히 분리된
"누가 어느 대회에 나가는지"만 결정하는 순수 로직이다 — 실제 대회
엔진(champions_engine.py / europa_engine.py / conference_engine.py,
아직 미구현)은 이 모듈이 반환한 참가팀 리스트를 그대로 받아서 조편성부터
시작하면 된다.

[국가 순위 정렬 기준] champions_engine._country_coefficients()(최근 5시즌
실측 UEFA 계수식 성적 합산)를 그대로 재사용한다 — 데이터가 아직 부족한
게임 초반(그 대륙에서 실측 시즌이 CL_COEFF_MIN_COUNTRIES개국 미만)엔
클럽 리그 등급(grade) 순으로 폴백한다. 챔스/유로파/컨퍼런스가 전부 같은
국가 순위를 공유해야 "챔스에서 밀려난 다음 순위 팀이 유로파로 내려간다"는
워터폴 의미가 성립하므로, 대회마다 다른 정렬 기준을 쓰지 않는다.

[슬롯표 확정 현황]
  유럽: 신민용 확정(2026-08) — 1~9위 2장, 10~27위 1장(유로파, 36장/27개국)
                              1~10위 1장, 11~23위 2장(컨퍼런스, 36장/23개국)
  아시아/아프리카/북남미: 아직 구체적 수치 미확정 — 우선 유럽과 동일한
    "국가 수 대비 비율"로 임시 배정해뒀다(TENTATIVE로 표시). 확정되면
    EUROPA_SLOT_TABLE_BY_CONTINENT / CONFERENCE_SLOT_TABLE_BY_CONTINENT만
    바꾸면 된다 — 아래 배분 로직 자체는 대륙 독립적이라 손댈 필요 없음.
"""
from database import get_conn
from constants import get_country_league_grade

from competition import champions_engine as _cl


# ── 유로파/컨퍼런스 슬롯표 ──────────────────────────────────────
# (상한 미만 rank_idx는 0-based. 예: rank_idx=0 → 1위)
# 반환값은 "그 나라가 받는 슬롯 수". 전 대륙 합계가 정원(36/48)을 넘을 수
# 있으므로, 실제 배분 시엔 등급 높은 나라부터 순서대로 채우다 정원에서
# 끊는다(기존 챔스 _select_entries와 동일한 원칙).

def _europa_slots_from_rank(continent: str, rank_idx: int) -> int:
    """[확정: 유럽] 1~9위 2장 / 10~27위 1장 / 28위~ 0장 (36장/27개국 목표)."""
    if continent == "유럽":
        if rank_idx < 9:
            return 2
        if rank_idx < 27:
            return 1
        return 0
    # [TENTATIVE] 아시아/아프리카/북남미 — 확정 수치 받기 전까지 유럽과
    # 동일한 비율(상위 1/3 국가는 2장, 나머지는 1장 — 유럽 확정 수치가
    # "27개국 중 1~9위 2장/10~27위 1장"이므로 그 비율은 9/27, 27/27)로
    # 그 대륙 실제 국가 수에 맞춰 스케일링.
    # [2026-08 버그수정, 신민용 리포트: "유로파/컨퍼런스 북남미가 30팀
    # 밖에 안 됨, 36으로 다른 대륙급이랑 맞춰야"] 분모를 27(유럽 확정
    # 국가 수)이 아니라 54로 잘못 써서 절반 비율로 스케일링되고 있었다
    # — 북남미(45개국)에서 목표 정원(36)보다 한참 적은 30장만 나온 원인.
    # 분모를 27로 고치면 슬롯 합계가 정원(36)을 넉넉히 넘게 배분되고,
    # allocate_continental_slots()가 정원에서 정확히 잘라주므로(캡 로직
    # 자체는 원래 정상) 결과적으로 정확히 36장이 채워진다.
    from competition.champions_engine import CONTINENT_MAP
    n_countries = _n_countries_in_continent(continent)
    two_cut = max(1, round(n_countries * 9 / 27))
    one_cut = max(two_cut + 1, round(n_countries * 27 / 27))
    if rank_idx < two_cut:
        return 2
    if rank_idx < one_cut:
        return 1
    return 0


def _conference_slots_from_rank(continent: str, rank_idx: int) -> int:
    """[확정: 유럽] 1~10위 1장 / 11~23위 2장 / 24위~ 0장 (36장/23개국 목표)."""
    if continent == "유럽":
        if rank_idx < 10:
            return 1
        if rank_idx < 23:
            return 2
        return 0
    # [TENTATIVE] 아시아/아프리카/북남미 — 위와 동일한 이유로 비율
    # 스케일링(유럽 확정 수치 "23개국 중 1~10위 1장/11~23위 2장"의
    # 비율 10/23, 23/23을 그대로 사용).
    # [2026-08 버그수정] europa와 동일한 원인(분모 54→23) 수정.
    n_countries = _n_countries_in_continent(continent)
    one_cut = max(1, round(n_countries * 10 / 23))
    two_cut = max(one_cut + 1, round(n_countries * 23 / 23))
    if rank_idx < one_cut:
        return 1
    if rank_idx < two_cut:
        return 2
    return 0


# [2026-08 확정, 신민용 요청] 유로파/컨퍼런스는 챔스와 달리 대륙 무관하게
# 전부 36장 — 북남미 챔스만 48로 확대된 건 챔스 전용 결정이라(위
# champions_engine.CL_TEAMS_BY_CONTINENT 참고) 여기엔 안 물려받는다.
QUALIFICATION_TEAM_CAP = {"유럽": 36, "북남미": 36, "아시아": 36, "아프리카": 36}


def _n_countries_in_continent(continent: str) -> int:
    from competition.champions_engine import CONTINENT_MAP
    game_conts = [gc for gc, ck in CONTINENT_MAP.items() if ck == continent]
    conn = get_conn()
    ph = ",".join("?" * len(game_conts))
    n = conn.execute(
        f"""SELECT COUNT(DISTINCT cn.id) FROM countries cn
            JOIN leagues l ON l.country_id = cn.id
            WHERE l.tier=1 AND cn.continent IN ({ph})""", game_conts).fetchone()[0]
    conn.close()
    return n or 1


def _ranked_countries(continent: str, year: int):
    """그 대륙의 국가 목록을, 챔스와 동일한 기준(실측 계수 우선, 부족하면
    클럽 리그 등급)으로 순위를 매겨 반환한다.
    반환: [{"country":..., "grade":..., "lid":..., "flag":...}, ...] (1위부터)
    """
    from competition.champions_engine import CONTINENT_MAP, CL_COEFF_MIN_COUNTRIES
    game_conts = [gc for gc, ck in CONTINENT_MAP.items() if ck == continent]
    conn = get_conn()
    ph = ",".join("?" * len(game_conts))
    leagues = conn.execute(
        f"""SELECT l.id AS lid, cn.name AS country, cn.flag AS flag
            FROM leagues l JOIN countries cn ON l.country_id = cn.id
            WHERE l.tier=1 AND cn.continent IN ({ph})""", game_conts).fetchall()
    leagues = [dict(r) for r in leagues]
    for lg in leagues:
        lg["grade"] = get_country_league_grade(lg["country"])

    ranking = _cl._country_coefficients(get_conn(), continent, year) if year else []
    if len(ranking) >= CL_COEFF_MIN_COUNTRIES:
        rank_map = {c: i for i, (c, _pts) in enumerate(ranking)}
        _grade_rank = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
        # 실측 랭킹에 있는 나라 우선(순위대로), 없는 나라는 등급순으로 뒤에 붙인다.
        ranked_part = [lg for lg in leagues if lg["country"] in rank_map]
        ranked_part.sort(key=lambda lg: rank_map[lg["country"]])
        unranked_part = [lg for lg in leagues if lg["country"] not in rank_map]
        unranked_part.sort(key=lambda lg: -_grade_rank.get(lg["grade"], 0))
        leagues = ranked_part + unranked_part
    else:
        _grade_rank = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
        leagues.sort(key=lambda lg: -_grade_rank.get(lg["grade"], 0))
    conn.close()
    return leagues


def allocate_continental_slots(continent: str, season: int, year: int = None):
    """한 대륙의 챔스/유로파/컨퍼런스 참가팀을 워터폴 방식으로 확정한다.

    각 나라의 리그 순위표를 위에서부터 훑으며:
      1) 챔스 슬롯 수만큼 챔스로
      2) 그다음 순위부터 유로파 슬롯 수만큼 유로파로
      3) 그다음 순위부터 컨퍼런스 슬롯 수만큼 컨퍼런스로
    떼어간다 — 한 팀이 동시에 두 대회 후보가 될 수 없다(리스트가 겹치지
    않음, 아래 검증 함수로 매 호출마다 실측 확인 가능).

    각 대회는 대륙 정원(유로파/컨퍼런스는 36 또는 48, 챔스는 별도로
    champions_engine.CL_TEAMS_BY_CONTINENT를 따름)을 넘지 않는 선에서,
    국가 순위가 높은 나라부터 채워진다(챔스 _select_entries와 동일한
    원칙 — 정원 초과분은 그냥 버려짐, 억지로 안 채움).

    반환: {"champions": [entry,...], "europa": [entry,...], "conference": [entry,...]}
    entry = {"team_id","team_name","flag","country","grade","ovr","cl_rank"}
    (cl_rank: 그 나라 리그 순위표 상 몇 위 팀인지, 1=우승팀. champions_engine._entry_from
    재사용이라 이름이 cl_rank지만 대회 종류 무관하게 "국내 순위"라는 뜻이다.)
    """
    # [2026-08 버그수정, 신민용 리포트: "유로파/컨퍼런스 북남미가 36이
    # 아니라 30~48로 어긋남"] 예전엔 cap 하나를 챔스/유로파/컨퍼런스
    # 셋 다에 공용으로 썼다 — 북남미 챔스를 48로 키우려던 조정이 유로파/
    # 컨퍼런스까지 같이 48로 끌고 가버렸다. 챔스는 champions_engine의
    # 대륙별 정원(북남미만 48)을 그대로 쓰고, 유로파/컨퍼런스는 항상
    # QUALIFICATION_TEAM_CAP(모든 대륙 36)을 쓰도록 분리한다.
    cl_cap = _cl.CL_TEAMS_BY_CONTINENT.get(continent, 36)
    el_cf_cap = QUALIFICATION_TEAM_CAP.get(continent, 36)
    cap_by_comp = {"champions": cl_cap, "europa": el_cf_cap, "conference": el_cf_cap}
    countries = _ranked_countries(continent, year)

    out = {"champions": [], "europa": [], "conference": []}
    for rank_idx, lg in enumerate(countries):
        if all(len(out[k]) >= cap_by_comp[k] for k in out):
            break
        ch_slots = _cl.get_cl_slots(lg["country"], lg["grade"], continent, year)
        eu_slots = _europa_slots_from_rank(continent, rank_idx)
        cf_slots = _conference_slots_from_rank(continent, rank_idx)
        if ch_slots <= 0 and eu_slots <= 0 and cf_slots <= 0:
            continue

        rows = _cl._standings_or_pseudo(lg["lid"], season)
        if not rows:
            continue

        cursor = 0  # 이 나라 순위표에서 어디까지 이미 떼어갔는지
        for comp_name, slots in (("champions", ch_slots), ("europa", eu_slots),
                                  ("conference", cf_slots)):
            if slots <= 0:
                continue
            remaining_cap = cap_by_comp[comp_name] - len(out[comp_name])
            take = min(slots, remaining_cap, len(rows) - cursor)
            if take <= 0:
                cursor += slots  # 정원 꽉 찼어도 다음 대회를 위해 순위 커서는 그대로 전진
                continue
            for i in range(take):
                row = rows[cursor + i]
                out[comp_name].append(
                    _cl._entry_from(lg, row, cl_rank=cursor + i + 1))
            cursor += slots

    return out


def start_all_continental_competitions(year, season):
    """[2026-08 신설] CL_START_WEEK(8주차) 진입 시 game_engine이 호출하는
    통합 진입점 — 챔스/유로파/컨퍼런스 3개 대회를 한 번에 생성한다.
    대륙마다 allocate_continental_slots()를 딱 1번만 호출해서(국가 순위
    계산 + 워터폴을 세 대회가 각자 따로 다시 하면 3배 낭비이므로) 그
    결과를 세 대회 엔진에 나눠준다.

    champions_engine.start_champions_league()의 "직전 시즌 없으면 스킵/
    이미 생성됐으면 중복 방지/내 팀 안내 로그" 정책은 그대로 유지하되,
    유로파·컨퍼런스는 참가팀 자체가 챔스보다 훨씬 넓어서(27개국/23개국 vs
    보통 10여개국) 매 시즌 정상적으로 열린다."""
    from game_engine import add_log, get_player
    from competition import champions_engine as _cl_mod
    from competition import europa_engine
    from competition import conference_engine
    p = get_player()
    if not p:
        return
    prev_season = season - 1
    if prev_season < 0:
        return
    if _cl_mod.get_cl_tournament(year, "유럽"):
        return  # 이미 이번 연도 생성됨(챔스 기준으로 중복 방지 판단 — 셋 다 항상 같이 생성되므로)

    _cl_mod._clear_entry_cache()
    from competition.competition_common import clear_entry_cache
    clear_entry_cache()

    my_cont = _cl_mod._my_continent(p)
    my_tid = p.get("current_team_id", 0)

    for cont in ("유럽", "아시아", "아프리카", "북남미"):
        alloc = allocate_continental_slots(cont, prev_season, year)
        this_my_tid = my_tid if cont == my_cont else 0

        if len(alloc["champions"]) >= 4:
            _cl_mod._build_tournament(year, cont, alloc["champions"], this_my_tid)
        if len(alloc["europa"]) >= 4:
            europa_engine.build_from_qualification(year, cont, alloc["europa"], this_my_tid)
        if len(alloc["conference"]) >= 4:
            conference_engine.build_from_qualification(year, cont, alloc["conference"], this_my_tid)

    add_log("─" * 44, "sep")
    add_log(f"🏆 {year}년 클럽 대항전(챔피언스리그/유로파리그/컨퍼런스리그) 개막!", "event")


def verify_no_overlap(alloc: dict) -> list:
    """세 대회 참가팀 리스트에 겹치는 team_id가 있는지 검증. 겹치면 그
    team_id 목록을 반환(정상이면 빈 리스트)."""
    seen: dict = {}
    dupes = []
    for comp_name, entries in alloc.items():
        for e in entries:
            tid = e["team_id"]
            if tid in seen and seen[tid] != comp_name:
                dupes.append((tid, seen[tid], comp_name))
            seen[tid] = comp_name
    return dupes