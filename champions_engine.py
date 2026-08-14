"""
champions_engine.py - 클럽 대륙 챔피언스리그 엔진

대륙별로 독립된 클럽 토너먼트 4개를 운영한다 (국가대표 대회와 별개).
  유럽 챔피언스리그 / 아시아 챔피언스리그 /
  아프리카 챔피언스리그 / 북남미 챔피언스리그

각 대륙 '안의' 클럽끼리만 붙는다 (아시안컵·아프리카컵의 클럽판).
출전팀: 그 대륙 소속 국가들의 직전 시즌 순위표 상위팀에서 대륙별 정원만큼
선발 (CL_TEAMS_BY_CONTINENT - 유럽/북남미 36팀, 아시아/아프리카 18팀).
  - 각국 1부 리그 1위는 무조건 출전
  - 정원이 안 차면 클럽 리그 등급(COUNTRY_LEAGUE_GRADE) 높은 나라의
    2위 이하로 채움 (국가대표 grade가 아니라 클럽 리그 grade 기준)

[2026-07 스위스 방식 전면 개편] 기존 '8조×4팀 조별리그(3경기)+토너먼트'를
실제 2024~ UEFA 챔피언스리그와 같은 '단일 리그 스테이지 + 플레이오프'
구조로 바꿨다. 대륙마다 참가 규모가 달라(36팀/18팀) 세부 수치는
CL_LEAGUE_GAMES_BY_CONTINENT / CL_DIRECT_CUT_BY_CONTINENT /
CL_PLAYOFF_POOL_BY_CONTINENT 세 딕셔너리로 대륙별로 관리한다(유럽/북남미는
실제 UEFA 수치 그대로, 아시아/아프리카는 참가 규모가 정확히 절반이라
경기 수·컷도 절반으로 축소해 같은 비율을 유지).

진행 시점 (유럽/북남미 기준 - 아시아/아프리카는 리그 스테이지가 4경기라
그만큼 주차가 앞당겨진다. 실제 진행 여부는 대회별 참가 규모로 매주 자동 판정):
  8주: 추첨 + 출전팀 확정
  9~16주: 리그 스테이지 (팀마다 서로 다른 8팀과 1경기씩, 총 8경기)
  17주: 플레이오프 (9~24위가 맞붙어 남은 16강 8자리를 놓고 단판 승부)
  18주: 16강 (1~8위 직행팀 + 플레이오프 승자 8팀)
  19주: 8강   20주: 4강   21주: 결승 + 3/4위전
  (아시아/아프리카는 참가 규모가 절반이라 1~4위 직행/5~12위 플레이오프/
   13~18위 광탈로 축소되고, 그만큼 빨리 끝나 8강부터 시작한다)

[알려진 단순화] 실제 UEFA 스위스 방식은 세부 실력 밴드로 대진을 짜고
(각 팀이 밴드별로 정해진 수만큼 상대) 플레이오프/16강도 2경기 홈+원정
합산이지만, 이 엔진은 기존 조별리그 방식과 마찬가지로 단판 승부 구조를
그대로 따르고 대진은 전력순 시드 배정(강한 팀일수록 약한 상대와 겹치지
않도록 순환)으로 단순화했다. 두 팀이 리그 스테이지에서 같은 나라 소속일
경우를 최대한 피하는 시도는 하지만(조별리그 때와 같은 재시도 방식),
8경기 전부를 국가 중복 없이 배정하는 것까지는 보장하지 않는다.

내 팀이 출전하면 내 팀 경기만 내가 출전(개인기록 반영),
나머지 대진은 AI끼리 자동 시뮬한다.
"""

import random

from database import get_conn

def _get_field_pos(p):
    """현재 팀 포메이션 기반 배치 포지션 계산 (순환 import 방지용 로컬 버전)."""
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

from constants import GRADE_TEAM_OVR  # 참고용(미사용 가능)
from constants import get_country_league_grade  # 클럽 대항전 슬롯 계산용(국가대표 grade와 분리, [2026-08] resolver 통일)
from constants import generate_round_robin  # 리그 스테이지 대진(원형법) 생성용

# ── 대회 일정 (주차) - 2026-07 스위스 방식 개편 ─────────────────────
# 클럽 시즌이 4~43주라 8주(draw)부터 시작해도 여유가 충분하다. 리그
# 스테이지(최대 9경기)를 9~17주에 깔고, 18주 플레이오프, 19~23주에
# 32강(북남미만)부터 결승까지 이어붙인다. 국내 컵대회는 이 구간(8~23주)을
# 피해서 시작하도록 cup_engine.CUP_ROUND_WEEKS_POOL도 함께 뒤로 밀었다.
# [2026-07 신민용 요청 — 대륙 규모 재조정] 북남미를 48팀으로 늘리면서
# 리그 스테이지가 8경기→9경기로 늘어 window가 1주 더 필요해졌다(9~16주
# → 9~17주). 그만큼 플레이오프(17→18)·토너먼트(18~21→19~23)가 전부 1주씩
# 밀렸다. cup_engine.CUP_ROUND_WEEKS_POOL의 첫 컵 라운드가 24주차라
# CL_END_WEEK을 23까지만 쓰고 24는 건드리지 않는다(겹치면 같은 주에 컵+
# 챔스 결승이 동시에 잡히는 충돌이 생김).
CL_START_WEEK = 8            # 추첨 (직전 시즌 최종 순위 기준)
CL_LEAGUE_WEEKS = (9, 17)    # 리그 스테이지 최대 구간(9~17주, 실제 사용 주차 수는 대륙별로 다름)
CL_PLAYOFF_WEEK = 18         # 플레이오프 (단판)
CL_ROUND_WEEKS = {
    "R32":  19,  # 북남미(48팀)만 여기서 시작. 다른 대륙은 이 라운드 자체가 없음(정상).
    "R16":  20,
    "QF":   21,
    "SF":   22,
    "F":    23,
    "TP":   23,  # 3/4위전: 결승과 같은 주차
}
CL_END_WEEK = 23

CL_TEAMS = 36                # 기본(유럽) 리그 스테이지 참가 규모

# [2026-07 신민용 요청] 대륙별 참가 규모 재조정 — 원래는 "유럽/북남미 36,
# 아시아/아프리카 18(정확히 절반)"이었는데, 아시아·아프리카도 유럽과
# 동일한 36으로 올리고, 북남미는 대륙 통합 규모가 가장 크다는 점을 반영해
# 48로 확대했다. 아시아/아프리카는 이제 유럽과 완전히 같은 구조(경기 수·
# 컷 라인 전부 그대로 재사용)라 별도 설계가 필요 없다.
CL_TEAMS_BY_CONTINENT = {"유럽": 36, "북남미": 48, "아시아": 36, "아프리카": 36}

# 팀마다 리그 스테이지에서 치르는 경기 수(서로 다른 상대와 1경기씩).
# 북남미(48팀)만 9경기 — 나머지 세 대륙(36팀)은 8경기로 동일.
CL_LEAGUE_GAMES_BY_CONTINENT = {"유럽": 8, "북남미": 9, "아시아": 8, "아프리카": 8}

# 리그 스테이지 순위 1~N위: 플레이오프 없이 바로 다음 토너먼트 라운드 직행.
CL_DIRECT_CUT_BY_CONTINENT = {"유럽": 8, "북남미": 16, "아시아": 8, "아프리카": 8}

# 리그 스테이지 순위 (직행 다음순위)~(직행+이 값)위: 플레이오프 대상.
# 이 인원의 절반이 플레이오프를 통과해 직행팀과 합류한다.
#   유럽/아시아/아프리카: 9~24위(16명) 플레이오프 → 8명 통과 → 직행 8 + 통과 8 = 16강(16팀)
#   북남미: 17~48위(32명) 플레이오프 → 16명 통과 → 직행 16 + 통과 16 = 32강(32팀)
#     [설계 의도] direct(16) + pool(32) = 48 = 전체 참가 팀 수 — 북남미는
#     48팀 규모답게 리그 스테이지 순위만으로 완전 탈락하는 팀 없이 전원이
#     직행 아니면 최소 플레이오프 기회를 받는다(유럽 등은 25~36위 12팀이
#     리그 스테이지에서 그대로 광탈하는 것과 대비됨).
CL_PLAYOFF_POOL_BY_CONTINENT = {"유럽": 16, "북남미": 32, "아시아": 16, "아프리카": 16}

def _cl_team_cap(continent: str) -> int:
    return CL_TEAMS_BY_CONTINENT.get(continent, CL_TEAMS)

def _cl_league_games(continent: str) -> int:
    return CL_LEAGUE_GAMES_BY_CONTINENT.get(continent, 8)

def _cl_direct_cut(continent: str) -> int:
    return CL_DIRECT_CUT_BY_CONTINENT.get(continent, 8)

def _cl_playoff_pool(continent: str) -> int:
    return CL_PLAYOFF_POOL_BY_CONTINENT.get(continent, 16)

# [2026-07 개편] 나라별 챔스 출전 슬롯 수 - 실제 UEFA처럼 리그 등급이 높을수록
# 한 나라에서 여러 팀이 동시에 나간다(1위만 나가던 방식 폐지).
#   SS등급(EPL 단독): 5장  S등급(빅리그): 4장  A등급: 3장  B등급: 2장  C~F등급: 1장
# 대륙별로 이 슬롯을 다 더하면 그 대륙 본선 정원(_cl_team_cap)을 훌쩍 넘는데
# (유럽만 봐도 최대 100장 안팎), 등급 높은 나라부터 순서대로 슬롯을 채워가다
# 정원에서 끊는다 - 마지막 나라는 남은 자리만큼만 받을 수도 있다.
#
# [버그 수정] 예전엔 이 슬롯을 country.grade(국가대표 FIFA 랭킹 기준 등급)로
# 계산했다 - 그런데 이건 '국가대표 전력'이지 '그 나라 클럽 리그 수준'이 아니다.
# 예: 모로코는 국가대표 세계 랭킹은 최상위권(FIFA 랭킹 기준 grade=S)이지만,
# 모로코 자국 리그 자체는 유럽 빅리그에 비할 바가 못 된다(선수 대부분이
# 해외파). 그 결과 실제로는 클럽 리그 인프라가 약한 아프리카 국가가 유럽
# 빅리그와 동급(S, 4장)의 챔스 슬롯을 받는 왜곡이 있었다. 클럽 대항전은
# 클럽 리그 수준(COUNTRY_LEAGUE_GRADE, get_league_grade())으로 슬롯을
# 정해야 한다 - 그 표에는 아프리카 최고가 B등급(모로코/나이지리아/이집트/
# 남아공)까지만 있어서 S등급 국가가 없는 대륙이 실제로 존재하게 된다.
# [2026-07 재조정, 신민용 지적: "챔스가 유럽만 등급 하나로 뭉뚱그려져서
# 스페인/프랑스가 똑같이 4장 받는 게 이상하다"] 등급(SS/S/A/B...) 단일
# 기준 대신, 실제 UEFA 계수 기반 접근 슬롯에 가까운 국가별 오버라이드를
# 우선 적용한다. 지정 안 된 나라는 그대로 등급 기본값(CL_SLOTS_BY_GRADE)
# 으로 폴백 — COUNTRY_OVR_ADJ/COUNTRY_SALARY_MULT와 같은 패턴.
CL_SLOTS_BY_GRADE = {"SS": 5, "S": 4, "A": 3, "B": 2, "C": 1, "D": 1, "E": 1, "F": 1}
CL_SLOTS_OVERRIDE = {
    # 최상위 (5장) — 잉글랜드는 이미 SS급이라 등급 기본값(5)과 동일하지만
    # 명시적으로 같이 적어 "최상위 그룹"이라는 의도를 코드에서도 드러낸다.
    "잉글랜드": 5, "스페인": 5, "이탈리아": 5, "독일": 5,
    # 상위 (3장)
    "프랑스": 3,
    # 중상위 (2장)
    "네덜란드": 2, "포르투갈": 2, "벨기에": 2,
    # 중위 (1장, 그래도 본선 직행)
    "튀르키예": 1, "오스트리아": 1, "스위스": 1, "덴마크": 1, "체코": 1,
    # 나머지 국가는 CL_SLOTS_BY_GRADE 등급 기본값으로 폴백
    # (하위권은 예선을 거쳐 1장 정도 배정되는 셈 — 별도 예선 시스템은
    # 미구현이라 지금은 "직행 1장"으로 동일하게 처리됨)
}

# ══════════════════════════════════════════════════════════════
# [2026-07 신설, 신민용 확정] 국가별 챔스 슬롯 동적 배정 — "덴마크=항상
# 1팀"처럼 영구 고정이 아니라, 그 대륙에서 최근 몇 시즌 챔스 성적이
# 좋은 나라일수록 슬롯이 자연스럽게 늘어나고 부진하면 줄어드는 구조.
# 실제 UEFA 계수 제도를 흉내낸 것 — 클럽월드컵(club_world_cup.py)의
# "4시즌 누적 점수제"와 완전히 같은 채점 함수(_team_stage_points)를
# 그대로 재사용해서, 대회당 아니라 "국가" 단위로 합산한 게 계수다.
#
# 데이터가 부족한 게임 초반(그 대륙에서 실측 챔스 시즌이 몇 번 안 쌓였을
# 때)엔 위 CL_SLOTS_OVERRIDE(유럽) / 등급 기본값(그 외 대륙)을 "시드값"
# 으로 그대로 쓰고, 시즌이 쌓이면서 자동으로 실측 계수 기반으로 넘어간다
# — 별도 전환 스위치 없이, "실측 데이터가 min개 이상 있으면 그걸 우선
# 사용"이라는 규칙 하나로 자연스럽게 전환됨.
CL_COEFF_SEASONS = 5      # 계수 산정에 쓰는 롤링 시즌 수 (실제 UEFA와 동일)
CL_COEFF_MIN_COUNTRIES = 6  # 이만큼 국가가 랭킹에 잡혀야 "데이터 충분"으로 보고 실측 사용


def _slots_from_rank(continent: str, rank_idx: int) -> int:
    """국가 순위(0-based, 계수 1위=0)를 슬롯 수로 변환.
    [2026-07 재조정, 신민용 확정] 4개 대륙 밴드를 각각 다르게 잡는다 —
    유럽/아시아/아프리카는 참가 규모(36개국)는 같아도 실제 상위권 쏠림
    정도가 다르고(유럽이 가장 쏠림), 북남미는 참가 규모 자체가 48개로
    더 크다(+남미 강호 쏠림도 반영해 한 단계 더 후하게)."""
    if continent == "북남미":
        if rank_idx < 1:  return 6   # 1위
        if rank_idx < 3:  return 4   # 2~3위
        if rank_idx < 6:  return 3   # 4~6위
        if rank_idx < 12: return 2   # 7~12위
        return 1                     # 13위~
    if continent == "아시아":
        if rank_idx < 2:  return 4   # 1~2위
        if rank_idx < 5:  return 3   # 3~5위
        if rank_idx < 10: return 2   # 6~10위
        return 1                     # 11위~
    if continent == "아프리카":
        if rank_idx < 2:  return 3   # 1~2위
        if rank_idx < 6:  return 2   # 3~6위
        return 1                     # 7위~
    # 유럽 (기본값) — 기존 그대로
    if rank_idx < 2:  return 5   # 1~2위
    if rank_idx < 4:  return 4   # 3~4위
    if rank_idx < 6:  return 3   # 5~6위
    if rank_idx < 10: return 2   # 7~10위
    return 1                     # 11위~


def _country_coefficients(conn, continent: str, upto_year: int, n_seasons: int = CL_COEFF_SEASONS):
    """continent(챔스 키: 유럽/아시아/아프리카/북남미)의 최근 n_seasons년치
    챔스 성적을 국가 단위로 합산한 계수 랭킹. [(country, pts), ...] 내림차순.
    [2026-07 성능수정] 팀별 개별 쿼리(N+1) 대신 대회당 1회 배치 조회로
    바꿔서, 매년 8주차마다 도는 이 함수가 리그 수가 많아질수록(실측
    664개 리그) 느려지던 문제를 없앴다."""
    from club_world_cup import _batch_team_stage_points
    years = list(range(upto_year - n_seasons, upto_year))
    if not years:
        return []
    ph = ",".join("?" * len(years))
    tournaments = conn.execute(
        f"SELECT id, winner_team_id FROM cl_tournaments WHERE continent=? AND year IN ({ph})",
        (continent, *years)).fetchall()
    scores: dict = {}
    for t in tournaments:
        entries = conn.execute(
            "SELECT team_id, country FROM cl_entries WHERE tournament_id=?", (t["id"],)).fetchall()
        stage_pts = _batch_team_stage_points(conn, t["id"], t["winner_team_id"])
        for e in entries:
            pts = stage_pts.get(e["team_id"], 0)
            scores[e["country"]] = scores.get(e["country"], 0) + pts
    return sorted(scores.items(), key=lambda kv: -kv[1])


def get_cl_slots(country: str, grade: str, continent: str = None, year: int = None) -> int:
    """나라별 챔스 슬롯 수. continent+year가 주어지면 최근 5시즌 실측
    계수로 동적 산정을 우선 시도하고, 데이터가 아직 부족하면(게임 초반)
    시드값(CL_SLOTS_OVERRIDE → 등급 기본값 순)으로 폴백한다.
    continent/year를 안 넘기면(하위호환) 예전처럼 시드값만 바로 반환."""
    if continent and year:
        conn = get_conn()
        ranking = _country_coefficients(conn, continent, year)
        conn.close()
        if len(ranking) >= CL_COEFF_MIN_COUNTRIES:
            rank_map = {c: i for i, (c, _) in enumerate(ranking)}
            if country in rank_map:
                return _slots_from_rank(continent, rank_map[country])
            # 랭킹엔 없지만(최근 5시즌 챔스에 한 번도 못 나간 나라) 데이터
            # 자체는 충분한 상황 — 시드값이 있으면 그걸, 없으면 최하위(1장).
    if country in CL_SLOTS_OVERRIDE:
        return CL_SLOTS_OVERRIDE[country]
    return CL_SLOTS_BY_GRADE.get(grade, 1)

# ── entry 캐시 ─────────────────────────────────────
# [2026-08 리팩터링] competition_common.py로 이동(_entry_cache 자체도
# 그쪽으로 옮겼다 — 키에 match_table이 포함돼 있어 대회별로 안전하게
# 공유된다). 여기서는 이름 호환을 위해 얇게 위임만 한다.
def _clear_entry_cache():
    from competition_common import clear_entry_cache
    clear_entry_cache()

STAGE_KO = {"league": "리그 스테이지", "PO": "플레이오프",
            "R32": "32강", "R16": "16강", "QF": "8강", "SF": "4강", "F": "결승", "TP": "3/4위전"}
# 토너먼트 라운드 진행 순서 (플레이오프 다음부터)
# [2026-07] R32 추가 — 북남미(48팀)만 여기서 시작하고 다른 대륙은
# _first_stage_for()가 애초에 "R16"부터 반환하므로 R32 단계 자체를 건너뛴다.
_STAGE_ORDER = ["R32", "R16", "QF", "SF", "F"]

# 대륙 그룹핑: 게임 내 continent 값 → 챔스 대륙 키
#   오세아니아 → 아시아 편입, 북미/남미 → 북남미 통합
CONTINENT_MAP = {
    "유럽": "유럽",
    "아시아": "아시아",
    "오세아니아": "아시아",
    "아프리카": "아프리카",
    "북미": "북남미",
    "남미": "북남미",
}
# 대회 이름
CL_CUP_NAME = {
    "유럽": "유럽 챔피언스리그",
    "아시아": "아시아 챔피언스리그",
    "아프리카": "아프리카 챔피언스리그",
    "북남미": "북남미 챔피언스리그",
}

# [2026-08 신설] competition_common.py 공용 엔진에 연결하는 설정.
# 여기 정의된 값들은 리팩터링 전 CL_CUP_NAME/STAGE_KO/CL_ROUND_WEEKS/
# CL_LEAGUE_WEEKS/CL_END_WEEK/_STAGE_ORDER와 완전히 동일한 값을 그대로
# 참조한다(값 복제가 아니라 같은 객체를 담아서, 나중에 위 상수들이
# 바뀌면 cfg도 자동으로 같이 바뀜).
from competition_common import CompetitionConfig
CHAMPIONS_CFG = CompetitionConfig(
    match_table="cl_matches",
    entry_table="cl_entries",
    tournament_table="cl_tournaments",
    history_table="cl_history",
    competition_name_by_continent=CL_CUP_NAME,
    award_prefix="챔피언스리그",
    momentum_type="ucl_champion",
    stage_ko=STAGE_KO,
    round_weeks=CL_ROUND_WEEKS,
    league_weeks=CL_LEAGUE_WEEKS,
    end_week=CL_END_WEEK,
    stage_order=_STAGE_ORDER,
)

# 결과별 보상 (명성, 인기, 행복도) - 클럽 대회는 국가대표보다 약간 낮게
_REWARD = {
    "우승":         (18, 12, 16),
    "준우승":       (11,  7,  8),
    "3위":          ( 9,  5,  6),
    "4위":          ( 6,  3,  4),
    "4강":          ( 7,  4,  5),  # 3/4위전 없는 경우 호환
    "8강":          ( 4,  3,  3),
    "16강":         ( 2,  2,  1),
    "플레이오프":    ( 1,  1,  0),  # 직행 실패, 플레이오프에서 탈락
    "리그 스테이지": ( 1,  0, -1),  # 리그 스테이지에서 컷(광탈)
    "16강 탈락":     ( 2,  2,  1),
    "8강 탈락":      ( 4,  3,  3),
    "4강 탈락":      ( 7,  4,  5),
}

# ─────────────────────────────────────────────
# 조회 헬퍼
# ─────────────────────────────────────────────

def get_cl_tournament(year, continent):
    """해당 연도+대륙의 챔스 row (없으면 None)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM cl_tournaments WHERE year=? AND continent=? ORDER BY id DESC LIMIT 1",
        (year, continent)).fetchone()
    conn.close()
    return dict(row) if row else None


def _my_continent(p):
    """내 소속팀이 속한 대륙(챔스 키). 팀 없으면 None."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    conn = get_conn()
    row = conn.execute(
        """SELECT cn.continent FROM teams t
           JOIN countries cn ON t.country_id = cn.id
           WHERE t.id=?""", (tid,)).fetchone()
    conn.close()
    if not row:
        return None
    return CONTINENT_MAP.get(row["continent"])


def _my_cl_tournament(p, year):
    """내 대륙의 이번 연도 챔스 (있으면). 내 팀이 출전했는지와 무관."""
    cont = _my_continent(p)
    if not cont:
        return None
    return get_cl_tournament(year, cont)


def get_my_cl_match(week, day=None, p=None, st=None):
    """이번 주차(또는 특정 day)에 내가 뛸 챔스 경기가 있으면 dict, 없으면 None.

    [2026-07 최적화, 신민용 리포트: "일 단위 전환 후 전체적으로 렉"] p를
    넘기면 get_player() 재조회를 생략한다 — center_panel의 하루 셀
    새로고침이 하루당 여러 번 이런 조회 함수를 부르는데, 그때마다
    새로 get_player()를 하는 게 누적 지연의 큰 비중을 차지했다."""
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if st is None:
        st = get_state()
    if not p or not st:
        return None
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    t = _my_cl_tournament(p, st["current_year"])
    if not t or t["status"] == "done":
        return None
    # 출전 자격 체크: 대회 생성(41주) 당시 등록된 내 팀과 현재 팀이 같아야 한다.
    #   시즌 중 다른 팀으로 이적한 경우(등록 마감 후 합류)는 그 시즌 챔스에 못 뛴다.
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != tid:
        return None

    conn = get_conn()
    if day is not None:
        m = conn.execute(
            """SELECT * FROM cl_matches
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?) AND (day=? OR day IS NULL OR day=0)""",
            (t["id"], week, tid, tid, day)).fetchone()
    else:
        m = conn.execute(
            """SELECT * FROM cl_matches
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?)""",
            (t["id"], week, tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    oe = conn.execute(
        "SELECT team_name, flag FROM cl_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], opp_id)).fetchone()
    conn.close()
    return {
        "cl": True,                       # 클럽 챔스 경기 표시 플래그
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],         # 대회명 (UI 호환 위해 league_name 키 사용)
        "stage": m["stage"],
        "stage_ko": STAGE_KO.get(m["stage"], m["stage"]),
        "grp": m["grp"] if "grp" in m.keys() else "",
        "opp": oe["team_name"] if oe else "?",
        "opp_flag": oe["flag"] if oe else "",
        "is_home": is_home,
        "week": week,
    }


def has_my_cl_match_between(week_from, week_to):
    """주차 범위 내 내 챔스 경기 존재 여부 (센터패널 표시용)."""
    for w in range(week_from, week_to + 1):
        if get_my_cl_match(w):
            return True
    return False


# ─────────────────────────────────────────────
# 대회 생성 (41주차 진입 시)
# ─────────────────────────────────────────────

def start_champions_league(year, season):
    """CL_START_WEEK(8주차) 진입 시 호출. 4개 대륙 챔스를 모두 생성.

    [2026-07] season은 '이번 시즌'(막 시작해서 아직 진행 중) 값이 넘어온다.
    출전팀은 직전 시즌(season-1)의 '이미 확정된 최종 순위'로 뽑아야 하므로
    (이번 시즌은 이제 막 8주차라 순위표가 완성돼 있지 않음), 실제 조회는
    prev_season = season-1 기준으로 한다. season-1이 없으면(첫 시즌) 스킵.
    """
    from game_engine import add_log, get_player
    import time
    _t0 = time.perf_counter()
    p = get_player()
    if not p:
        return
    prev_season = season - 1
    # [2026-07 재설계, 신민용 확정] 예전엔 "직전 시즌이 없으면(1년차) 챔스
    # 자체를 생략"했는데, 이러면 게임 초반 몇 년간 챔스/이 게임의 클럽월드컵
    # 등 국제 클럽대항전 역사가 통째로 비어버린다. 플레이어는 MIN_INTL_CALLUP_AGE
    # (17세) 나이 제한 때문에 1년차(16세)엔 애초에 국대에 못 뽑히므로 "가짜
    # 시즌 도중 내가 소집되는" 위험은 이미 구조적으로 차단돼 있다 — 그래서
    # 1년차도 그냥 정상 진행하고, 없는 "직전 시즌 순위"만 team_strength 기반
    # 추정 순위로 대체한다(_select_entries 내부의 _pseudo_season_standings
    # 참고). prev_season이 음수로 내려가는 건 이론상 불가능하지만 방어적으로만 막는다.
    if prev_season < 0:
        return

    # 이미 만들어졌으면(어느 대륙이든) 중복 생성 방지
    if get_cl_tournament(year, "유럽"):
        return

    _clear_entry_cache()   # 새 시즌 대회 → 이전 캐시 무효화

    my_cont = _my_continent(p)
    my_tid = p.get("current_team_id", 0)

    for cont in ("유럽", "아시아", "아프리카", "북남미"):
        entries = _select_entries(cont, prev_season, year)
        if len(entries) < 4:
            continue  # 출전팀 부족하면 그 대륙 대회 생략
        _build_tournament(year, cont, entries, my_tid if cont == my_cont else 0)
    print(f"[PERF] 챔스 4대륙 생성(슬롯 계산 포함) {time.perf_counter()-_t0:.2f}s")

    # ── 내 대회 안내 로그 (출전 자격 = 직전 시즌 내 리그 순위가 배정 슬롯 안) ──
    if my_cont and my_tid:
        t = get_cl_tournament(year, my_cont)
        if t:
            # 출전 자격 판정: 내 팀이 직전 시즌 '내 1부 리그'에서 CL 슬롯 안에 들었는가?
            qualified = _is_my_team_cl_qualified(p, my_tid, prev_season, year)
            conn = get_conn()
            mine = conn.execute(
                "SELECT 1 FROM cl_entries WHERE tournament_id=? AND team_id=?",
                (t["id"], my_tid)).fetchone()
            conn.close()

            if mine:
                # 본선 진출 (자격도 당연히 있음)
                conn = get_conn()
                conn.execute("UPDATE cl_tournaments SET my_qualified=1 WHERE id=?",
                             (t["id"],))
                conn.commit(); conn.close()
                add_log("─" * 44, "sep")
                add_log(f"🏆 {year}년 {t['name']} 개막!  내 팀 본선 진출!",
                        "event", year, CL_START_WEEK)
                add_log(f"   리그 스테이지 {CL_LEAGUE_WEEKS[0]}주차부터 시작",
                        "event", year, CL_START_WEEK)
            elif qualified:
                # 슬롯 안(자격)인데 32팀 컷 등으로 본선엔 못 들어감 → '본선 진출 실패'
                conn = get_conn()
                conn.execute("UPDATE cl_tournaments SET my_qualified=1 WHERE id=?",
                             (t["id"],))
                trow = conn.execute("SELECT name FROM teams WHERE id=?",
                                    (my_tid,)).fetchone()
                conn.commit(); conn.close()
                team_name = trow["name"] if trow else ""
                _save_trophy(year, team_name, t["name"], "본선 진출 실패")
                add_log("─" * 44, "sep")
                add_log(f"🏆 {year}년 {t['name']}  챔스 출전권 확보했지만 본선 진출 실패",
                        "event", year, CL_START_WEEK)
            # else: 슬롯 밖(자격 없음) → 챔스와 무관, 아무것도 안 뜸 (침묵)


def _pseudo_season_standings(league_id):
    """[2026-07 신설] 1년차(직전 시즌 자체가 없음)에 챔스 출전팀을 뽑아야
    할 때 쓰는 가상 순위표. get_league_standings(season=0)은 실제 경기가
    하나도 없어서 그 리그 소속팀을 전부 0승0무0패로 반환하는데, 이러면
    정렬 기준(pts/gd/득점)이 전부 동률이라 사실상 team_id 순서 같은
    의미 없는 순서로 챔스 출전팀이 뽑힌다. 대신 팀 평균 OVR(=선수 생성 때
    이미 확정된 team_strength의 결과물)로 정렬해서, '한 시즌 뛰었다면
    이런 순서로 끝났을 것'이라는 그럴듯한 가상 순위를 만든다."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.id AS id, t.name AS name, AVG(a.ovr) AS avg_ovr
           FROM teams t LEFT JOIN ai_players a ON a.team_id = t.id
           WHERE t.league_id=? GROUP BY t.id""", (league_id,)).fetchall()
    conn.close()
    ranked = sorted(
        [{"id": r["id"], "name": r["name"], "wins": 0, "draws": 0, "losses": 0,
          "goals_for": 0, "goals_against": 0, "pts": 0, "gd": 0}
         for r in rows],
        key=lambda r: -(next(x["avg_ovr"] for x in rows if x["id"] == r["id"]) or 0))
    return ranked


def _standings_or_pseudo(league_id, season):
    """season<1(1년차, 참고할 직전 시즌 자체가 없음)이면 가상 순위,
    아니면 실제 순위표. _select_entries/_is_my_team_cl_qualified 공용."""
    from game_engine import get_league_standings
    if season < 1:
        return _pseudo_season_standings(league_id)
    return get_league_standings(league_id, season=season)


def _is_my_team_cl_qualified(p, my_tid, season, year=None):
    """내 팀이 그 시즌 '내 1부 리그'에서 CL 슬롯(CL_SLOTS_BY_GRADE) 안에 드는지
    — 챔스 출전 자격 판정. 챔스는 1부(tier=1) 리그 소속만 자격이 있고,
    그 나라 등급에 따라 1~4위까지도 출전할 수 있다(2부 이하는 자격 없음)."""
    if not my_tid:
        return False
    conn = get_conn()
    row = conn.execute(
        "SELECT league_id FROM teams WHERE id=?", (my_tid,)).fetchone()
    if not row:
        conn.close()
        return False
    lid = row["league_id"]
    lg_row = conn.execute(
        """SELECT l.tier AS tier, cn.name AS country, cn.continent AS continent FROM leagues l
           JOIN countries cn ON l.country_id = cn.id WHERE l.id=?""", (lid,)).fetchone()
    conn.close()
    # 1부가 아니면 챔스 자격 없음 (2부 1위는 승격 대상일 뿐)
    if not lg_row or lg_row["tier"] != 1:
        return False
    # [버그 수정] 국가대표 grade가 아니라 클럽 리그 grade로 슬롯 수를 정한다.
    league_grade = get_country_league_grade(lg_row["country"])
    cl_cont = CONTINENT_MAP.get(lg_row["continent"])
    slots = get_cl_slots(lg_row["country"], league_grade, cl_cont, year)
    standings = _standings_or_pseudo(lid, season)
    if not standings:
        return False
    my_rank = next((i for i, r in enumerate(standings, start=1) if r["id"] == my_tid), None)
    return my_rank is not None and my_rank <= slots


def _select_entries(continent, season, year=None):
    """대륙 소속 각 1부 리그에서, 나라 등급별 슬롯 수(CL_SLOTS_BY_GRADE)만큼
    순위표 상위팀을 뽑는다 (대륙별 정원은 _cl_team_cap — 36 또는 48).

    규칙:
      - 나라 등급이 높을수록(S~F) 한 나라에서 나가는 팀 수가 많음 (최대 4장)
      - 등급 높은 나라부터 순서대로 슬롯을 소진, 정원에서 컷
        (마지막 나라는 남은 자리만큼만 받을 수 있음 — 부분 배정)
      - 각 나라 안에서는 '직전 시즌 최종 순위' 상위팀부터 배정된 슬롯 수만큼
    반환: [{team_id, team_name, flag, ovr, grade, country, cl_rank}, ...] (최대 32)
    cl_rank: 그 나라 안에서 몇 위로 출전했는지 (1=리그 우승팀, 2=2위 ...)
    """
    from game_engine import get_league_standings

    game_conts = [gc for gc, ck in CONTINENT_MAP.items() if ck == continent]
    cap = _cl_team_cap(continent)

    conn = get_conn()
    placeholders = ",".join("?" * len(game_conts))
    leagues = conn.execute(
        f"""SELECT l.id AS lid, cn.name AS country, cn.flag AS flag, cn.grade AS grade
            FROM leagues l JOIN countries cn ON l.country_id = cn.id
            WHERE l.tier = 1 AND cn.continent IN ({placeholders})""",
        game_conts).fetchall()
    leagues = [dict(r) for r in leagues]
    conn.close()

    # [버그 수정] 슬롯/정렬 기준을 국가대표 grade(cn.grade)가 아니라 클럽 리그
    # grade(COUNTRY_LEAGUE_GRADE)로 바꾼다. r["grade"]는 아래에서 그대로
    # league_grade로 덮어써서, _entry_from()이 만드는 entry의 "grade" 필드도
    # (화면에 노출되는 값도) 클럽 리그 등급을 가리키게 통일한다.
    for lg in leagues:
        lg["grade"] = get_country_league_grade(lg["country"])

    # 등급 높은 나라 우선 (정원 초과 시 컷 기준 + 슬롯 배정 우선순위)
    grade_rank = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
    leagues.sort(key=lambda r: -grade_rank.get(r["grade"], 0))

    picked = []
    for lg in leagues:
        remaining = cap - len(picked)
        if remaining <= 0:
            break
        slots = min(get_cl_slots(lg["country"], lg["grade"], continent, year), remaining)
        rows = _standings_or_pseudo(lg["lid"], season)
        if not rows:
            continue
        for rank, row in enumerate(rows[:slots], start=1):
            picked.append(_entry_from(lg, row, cl_rank=rank))
        if len(picked) >= cap:
            break
    return picked[:cap]


def _entry_from(lg, standing_row, cl_rank=1):
    """[2026-08 리팩터링] competition_common.entry_from으로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import entry_from
    return entry_from(lg, standing_row, cl_rank)


def _league_phase_pairs(entries, games, my_tid):
    """[2026-08 리팩터링] competition_common.league_phase_pairs로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import league_phase_pairs
    return league_phase_pairs(entries, games, my_tid)


def _build_tournament(year, continent, entries, my_tid):
    """[2026-08 리팩터링] competition_common.build_tournament으로 이동
    (완전 동일 로직) — team_cap/games만 챔스 전용 C그룹 함수로 계산해 넘긴다."""
    from competition_common import build_tournament
    build_tournament(CHAMPIONS_CFG, year, continent, entries, my_tid,
                      team_cap=_cl_team_cap(continent), games=_cl_league_games(continent))


def _first_stage_for(n):
    """[2026-08 리팩터링] competition_common.first_stage_for로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import first_stage_for
    return first_stage_for(n)


# ─────────────────────────────────────────────
# 주차 처리 (advance_4weeks에서 매주 호출)
# ─────────────────────────────────────────────

def process_cl_week(week):
    """이번 주차의 남은 챔스 경기(AI) 시뮬 + 라운드 진행 (모든 대륙)."""
    from game_engine import get_state
    st = get_state()
    if not st:
        return
    year = st["current_year"]

    for cont in ("유럽", "아시아", "아프리카", "북남미"):
        t = get_cl_tournament(year, cont)
        if not t or t["status"] == "done":
            continue
        _process_one(t, week)


def _process_one(t, week):
    """단일 대회: 이번 주차 이하 미진행 경기 AI 시뮬 → 라운드/스테이지 마감."""
    conn = get_conn()
    pending = [dict(r) for r in conn.execute(
        """SELECT * FROM cl_matches
           WHERE tournament_id=? AND week<=? AND home_score=-1 ORDER BY id""",
        (t["id"], week)).fetchall()]

    # pending 경기를 한 커넥션·한 트랜잭션으로 일괄 시뮬(경기마다 개폐하던 것을 1회로).
    # [2026-07 성능 최적화] 개별 execute() 대신 batch에 모아 executemany()로
    # 한 번에 반영 — "1주 진행" 시 챔스 라운드가 큰 주차(리그페이즈 등)일수록
    # 체감 지연이 줄어든다. game_engine._sim_all_ai_matches와 동일한 패턴.
    _batch = []
    for m in pending:
        _sim_ai_match(t, m, batch=_batch)
    if _batch:
        conn.executemany(
            """UPDATE cl_matches SET home_score=?, away_score=?,
               pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
            _batch)
    conn.commit()
    conn.close()

    # 리그 스테이지 마지막 주차(대륙마다 다름 - games만큼) → 순위 확정
    league_end_week = CL_LEAGUE_WEEKS[0] + _cl_league_games(t["continent"]) - 1
    if week == league_end_week:
        conn = get_conn()
        remain = conn.execute(
            "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage='league' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn.close()
        if remain == 0:
            _finalize_league_phase(t)
        return

    # 플레이오프 주차 → 16강(또는 8강) 진출팀 확정
    if week == CL_PLAYOFF_WEEK:
        conn = get_conn()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage='PO'",
            (t["id"],)).fetchone()["n"]
        remain = conn.execute(
            "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage='PO' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn.close()
        if total > 0 and remain == 0:
            _finalize_playoff(t)
        return

    # 토너먼트 라운드 주차 확인
    cur_stage = None
    for stg, wk in CL_ROUND_WEEKS.items():
        if wk == week:
            cur_stage = stg
            break
    if cur_stage is None:
        return

    # [버그 수정] 대륙마다 대회 규모가 달라(16강부터/8강부터 등) 첫 토너먼트
    # 라운드 이름이 다르다. 이 주차에 해당하는 스테이지(cur_stage) 경기가
    # 이 대회엔 애초에 없을 수도 있다 — 그 경우 '이미 진행 완료'가 아니라
    # '이 대회는 이 라운드를 안 치른다'는 뜻이므로, 잘못 다음 라운드로
    # 진행시키지 않고 그냥 넘어간다.
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage=?",
        (t["id"], cur_stage)).fetchone()["n"]
    if total == 0:
        conn.close()
        return
    remain = conn.execute(
        "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage=? AND home_score=-1",
        (t["id"], cur_stage)).fetchone()["n"]
    conn.close()
    if remain > 0:
        return

    if cur_stage == "F":
        # TP(3/4위전)도 같은 주차 — 둘 다 완료된 후 _finish_tournament 호출
        conn2 = get_conn()
        tp_remain = conn2.execute(
            "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage='TP' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn2.close()
        if tp_remain == 0:   # TP도 끝났거나 TP 경기 자체가 없으면 바로 종료
            _finish_tournament(t)
        # tp_remain > 0 이면 TP 완료 시 다시 이 함수가 호출됨
    elif cur_stage == "TP":
        # 3/4위전 완료 → 결승도 끝났는지 확인 후 같이 종료
        conn2 = get_conn()
        f_remain = conn2.execute(
            "SELECT COUNT(*) AS n FROM cl_matches WHERE tournament_id=? AND stage='F' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn2.close()
        if f_remain == 0:
            _finish_tournament(t)
    else:
        nxt = _STAGE_ORDER[_STAGE_ORDER.index(cur_stage) + 1]
        _advance_round(t, cur_stage, nxt)


# ─────────────────────────────────────────────
# 경기 시뮬 (AI)
# ─────────────────────────────────────────────

def _entry(tid, team_id):
    """[2026-08 리팩터링] competition_common.entry로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import entry
    return entry(CHAMPIONS_CFG, tid, team_id)


def _match_outcome(h_ovr, a_ovr):
    """[2026-08 리팩터링] competition_common.match_outcome으로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import match_outcome
    return match_outcome(h_ovr, a_ovr)


def _resolve_pso(h_ovr, a_ovr):
    """[2026-08 리팩터링] competition_common.resolve_pso로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import resolve_pso
    return resolve_pso(h_ovr, a_ovr)


def _sim_ai_match(t, m, my_played=False, conn=None, reason="injury", batch=None):
    """[2026-08 리팩터링] competition_common.sim_ai_match로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import sim_ai_match
    sim_ai_match(CHAMPIONS_CFG, t, m, my_played=my_played, conn=conn, reason=reason, batch=batch)


def _winner_of(m):
    """[2026-08 리팩터링] competition_common.winner_of로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import winner_of
    return winner_of(m)


# ─────────────────────────────────────────────
# 내 경기 시뮬
# ─────────────────────────────────────────────

def sim_my_cl_match_as_ai(week, p, reason="injury", day=None):
    """[2026-07 신설, 버그수정] 부상 등으로 내가 못 뛸 때 내 챔스 경기를
    AI끼리 시뮬레이션 — cup_engine.sim_my_cup_match_as_ai와 동일한 이유로
    신설(이게 없으면 그 경기가 영원히 미완료로 남아 대회 진행이 멈춘다).

    [2026-07 버그수정, 신민용 리포트: "부상으로 경기 못 나갔는데 감독관계가
    그대로다"] game_engine._sim_my_team_match_as_ai와 동일한 이유로,
    이 챔스 AI-대체 경로도 결장 페널티(manager_relation -1)를 적용한다."""
    info = get_my_cl_match(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM cl_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM cl_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()
    if m["home_score"] != -1:
        return  # 이미 처리됨(멱등)
    _sim_ai_match(t, m, my_played=False, reason=reason)
    from game_engine import update_player, _calc_manager_rel
    update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False, not_played_penalty=2))


def simulate_my_cl_match(week, p, day=None):
    """내가 출전하는 챔스 경기."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _roll_red_card, _apply_red_card_dismissal)
    info = get_my_cl_match(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM cl_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM cl_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    he = _entry(t["id"], m["home_team_id"])
    ae = _entry(t["id"], m["away_team_id"])
    is_home = info["is_home"]

    # [2026-07 신설] 출전정지 체크 — 퇴장 다음 경기는 강제 결장.
    _suspended, _new_susp = _check_suspended(p, field="cl_suspension")
    if _suspended:
        update_player(cl_suspension=_new_susp)
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event")

    # 내 출전 보너스 (클럽 리그 경기와 동일: 격차 기반 에이스 영향력)
    # [2026-07 통일] 예전엔 리그(game_engine._simulate_match)만 "OVR가
    # 높을수록 같은 격차라도 더 크게 반영"하는 볼록 가속 + 소프트캡을 쓰고
    # 챔스는 선형+하드컷(14.0)이라, 월드클래스 선수가 챔스에서 팀을 끌어
    # 올리는 정도가 리그보다 오히려 약하게 나오는 불일치가 있었다. 리그와
    # 완전히 동일한 공식으로 맞춘다.
    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = max(0.0, _my_ovr - _team_ovr)
    _star = 1.0 + max(0.0, (_my_ovr - 60) / 40.0) ** 1.8 * 3.0
    bonus = _gap * 0.30 * _star + max(0.0, _my_ovr - 50) * 0.08
    bonus = _soft_cap(bonus, 30.0)
    # [2026-07 신설] '리더십' 성격의 team_win_bonus 연결 (정의만 돼있고 실제
    # 경기엔 미연결 상태였음) — 캐리 보너스에 작은 배율만 얹는다.
    from constants import PERSONALITY_EFFECTS
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if "team_win_bonus" in _pe:
        bonus *= (1.0 + _pe["team_win_bonus"])
    if _suspended:
        bonus = 0.0
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = _match_outcome(h_ovr, a_ovr)
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "league")  # [2026-07 버그 수정] 조별리그->리그 스테이지 개편 후 남아있던 옛 스테이지명 비교
    if outcome == "draw" and is_ko:
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    if _suspended:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
        _absence_reason = "suspension"
    else:
        # [2026-07 통일] intl_engine(국제대회)과 동일하게 "오늘 상대의 실제 팀
        # OVR"을 dom 기준으로 넘긴다 — 강팀 상대면 개인도 고전, 약체 상대면
        # 골·평점이 폭발하도록. he/ae는 보너스 반영 전 원본 팀 OVR이라
        # game_engine._simulate_match의 home_ovr/away_ovr과 동일한 성격이다.
        _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr)
        _absence_reason = None
        # [2026-07 신설] 퇴장 판정 — '폭력적' 성격의 red_card_chance 반영.
        if _roll_red_card(p):
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(p, field="cl_suspension")
            _absence_reason = "red_card"
    # [2026-07 신설] '소심함' 성격의 big_match_rating 연결 — 챔피언스리그는
    # 대회 자체가 빅매치 성격이라(국내컵과 달리 결승 한정이 아니라) 모든
    # 경기에 적용한다.
    if not _suspended and "big_match_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["big_match_rating"], 1)))
    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    # [2026-07 신설] 실제 진행 날짜 저장 (커리어/은퇴창 표시용).
    from game_engine import _week_intl_cl_day
    day = _week_intl_cl_day(m["week"], p)

    conn = get_conn()
    conn.execute("""UPDATE cl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?,
                    my_played=?, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?,
                    day=?, my_absence_reason=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score,
                  0 if _suspended else 1, _get_field_pos(p),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  my_conceded, day, _absence_reason, m["id"]))
    conn.commit()
    conn.close()

    # [세부 지표] 통산(total_*)에도 누적 → 커리어 통합 통계에 챔스 경기 반영
    update_player(
        total_shots=p.get("total_shots", 0) + detail["shots"],
        total_shots_on=p.get("total_shots_on", 0) + detail["shots_on"],
        total_key_passes=p.get("total_key_passes", 0) + detail["key_passes"],
        total_dribbles=p.get("total_dribbles", 0) + detail["dribbles"],
        total_blocks=p.get("total_blocks", 0) + detail["blocks"],
    )

    # 인기/스트레스/행복
    _update_pop(p, goals, assists, rating)
    p2 = get_player()
    # [2026-07 조정, 신민용 지적: "경기 스트레스가 고강도 훈련만큼은 돼야
    # 하지 않나"] 리그/컵과 동일 원칙으로 상향.
    ns = min(100, p2["stress"] + 20)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    # ── 로그 (리그전과 동일하게: 헤더 클릭 → 상세 창) ──
    stage_ko = STAGE_KO.get(m["stage"], "")
    my_tid = p.get("current_team_id", 0)
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == my_tid else '패'})"
        rs = "무"

    comp_name = f"{t['name']} {stage_ko}".strip()
    # [2026-07 신민용 요청] 챔피언스리그는 국제대회라 팀명만으론 어느 나라
    # 소속인지 안 보여서, 팀명 옆에 (국가)를 붙인다.
    home_disp = f"{he['flag']}{he['team_name']}({he.get('country','?')})"
    away_disp = f"{ae['flag']}{ae['team_name']}({ae.get('country','?')})"
    pso = {"won": pso_winner == my_tid, "score": pso_score} if pso_winner else None
    detail_id = _save_match_detail(
        p, week, comp_name, is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, True, False, detail, pso=pso)
    marker = f" [match:{detail_id}]" if detail_id else ""

    add_log("─" * 44, "sep")
    add_log(f"🏆 {comp_name}  {week}주차{marker}", "match")
    add_log(f"   {home_disp} {hs}-{as_} {away_disp}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")
    from game_engine import _log_highlight, _min_sortkey
    _timed = sorted([(int(e[0]), e[1]) if isinstance(e, tuple) else
                     (random.randint(1, 90), str(e)) for e in events],
                    key=lambda x: _min_sortkey(x[0]))
    hi = _log_highlight(goals, assists, _timed)
    if hi:
        add_log(f"   {hi}", "match")


# ─────────────────────────────────────────────
# 라운드 진행
# ─────────────────────────────────────────────

def get_cl_league_standings(tid):
    """[2026-08 리팩터링] competition_common.get_league_standings으로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import get_league_standings
    return get_league_standings(CHAMPIONS_CFG, tid)


def _finalize_league_phase(t):
    """[2026-08 리팩터링] competition_common.finalize_league_phase로 이동
    (완전 동일 로직) — direct_cut/po_pool/playoff_week만 챔스 전용 C그룹
    함수·상수로 계산해 넘긴다. start_knockout_fn은 순환 없이 _start_knockout
    자체를 그대로 넘긴다(모듈 레벨 함수라 문제 없음)."""
    from competition_common import finalize_league_phase
    cont = t["continent"]
    finalize_league_phase(CHAMPIONS_CFG, t, _cl_direct_cut(cont), _cl_playoff_pool(cont),
                           CL_PLAYOFF_WEEK, _start_knockout)


def _finalize_playoff(t):
    """[2026-08 리팩터링] competition_common.finalize_playoff로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import finalize_playoff
    finalize_playoff(CHAMPIONS_CFG, t, _start_knockout)


def _start_knockout(t, qualifier_ids, direct_ids=None, winner_ids=None):
    """[2026-08 리팩터링] competition_common.start_knockout으로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import start_knockout
    start_knockout(CHAMPIONS_CFG, t, qualifier_ids, CL_ROUND_WEEKS,
                   direct_ids=direct_ids, winner_ids=winner_ids)


def _advance_round(t, cur_stage, next_stage):
    """[2026-08 리팩터링] competition_common.advance_round로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import advance_round
    advance_round(CHAMPIONS_CFG, t, cur_stage, next_stage, CL_ROUND_WEEKS)


def _cl_team_stage_weights(conn, tid):
    """[2026-07 신설, 설계문서 v2 6절에서 발견한 공백 메움] 국가대표 대회
    (`intl_engine._intl_country_stage_weights`)와 클럽월드컵
    (`club_world_cup_engine._cwc_team_stage_weights`)은 이미 진출 라운드
    가중치를 개인상에 반영하고 있는데, 정작 챔피언스리그(가장 자주 열리는
    대회)만 이 가중치가 빠져 있었다 — 조별(리그 스테이지) 탈락 선수와
    우승 선수가 개인 스탯만 같으면 챔스 시즌MVP를 동률로 다퉜다는 뜻이다.
    이 함수는 그 공백을 메운다. 챔스 스테이지 구성(리그 스테이지→플레이오프
    →32강(북남미만)→16강→8강→4강→결승/3·4위전)에 맞춰 조별(리그 스테이지)만
    =0.70, 플레이오프=0.75, 32강=0.80, 16강=0.85, 8강=0.90, 4강(3/4위전
    포함)=0.96, 준우승=0.99, 우승=1.00으로 둔다."""
    t = conn.execute("SELECT winner_team_id FROM cl_tournaments WHERE id=?", (tid,)).fetchone()
    winner_tid = t["winner_team_id"] if t else 0
    _ORDER = {"PO": 0, "R32": 1, "R16": 2, "QF": 3, "SF": 4}
    _TIER_W = {0: 0.75, 1: 0.80, 2: 0.85, 3: 0.90, 4: 0.96}
    furthest = {}
    runner_up_tid = None
    for m in conn.execute(
            "SELECT stage, home_team_id, away_team_id FROM cl_matches "
            "WHERE tournament_id=? AND stage!='league' AND home_score>=0", (tid,)).fetchall():
        stg = m["stage"]
        if stg == "F":
            loser = m["away_team_id"] if m["home_team_id"] == winner_tid else m["home_team_id"]
            runner_up_tid = loser
            continue
        if stg == "TP":
            for side_tid in (m["home_team_id"], m["away_team_id"]):
                furthest[side_tid] = max(furthest.get(side_tid, -1), _ORDER["SF"])
            continue
        if stg not in _ORDER:
            continue
        idx = _ORDER[stg]
        for side_tid in (m["home_team_id"], m["away_team_id"]):
            furthest[side_tid] = max(furthest.get(side_tid, -1), idx)

    def _weight(team_id):
        if team_id == winner_tid:
            return 1.00
        if team_id == runner_up_tid:
            return 0.99
        return _TIER_W.get(furthest.get(team_id, -1), 0.70)
    return _weight


def _award_cl_awards(t, my_tid):
    """[2026-07 확장, 신민용 확정] 챔스 득점왕/시즌MVP/베스트11/영플레이어/
    골든글러브. 내 팀이 조기탈락해도(4강까지 못 가도) 대회 전체 기준으로
    별개 판정한다.
    [2026-07 추가 확장, 설계문서 v2 반영] 진출 라운드 가중치
    (_cl_team_stage_weights)를 드디어 적용하고, 결승·준결승 빅게임 보너스
    (가산, 상한 있음)를 추가하고, 영플레이어 나이컷을 UEFA 실제 기준
    (23세 이하)으로 올리고, 골든글러브에 세이브율·평균실점 품질 게이트를
    추가한다."""
    from game_engine import (get_player, add_log, _estimate_ai_season, _estimate_ai_clean_sheets,
                             _position_award_score, _evaluate_extra_awards,
                             _cap_additive_bonus, _gk_quality_ok,
                             ATTACK_POS, GK_POS, DF_POS, MF_POS)
    tid = t["id"]
    conn = get_conn()
    my_row = conn.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(my_goals),0) g, COALESCE(SUM(my_assists),0) a,
                  COALESCE(AVG(my_rating),0) r, COALESCE(SUM(my_saves),0) sv,
                  COALESCE(SUM(my_conceded),0) gc
           FROM cl_matches WHERE tournament_id=? AND my_played=1""", (tid,)).fetchone()
    if not my_row or my_row["n"] == 0:
        conn.close()
        return
    n_games = max(1, my_row["n"])
    p = get_player()
    my_pos = p.get("position", "ST") if p else "ST"
    my_ovr = p.get("ovr", 60) if p else 60
    my_age = p.get("age", 25) if p else 25
    my_cs = conn.execute(
        """SELECT COUNT(*) c FROM cl_matches WHERE tournament_id=? AND my_played=1
           AND ((home_team_id=? AND away_score=0) OR (away_team_id=? AND home_score=0))""",
        (tid, my_tid, my_tid)).fetchone()["c"]

    pool = [{"position": my_pos, "goals": my_row["g"], "assists": my_row["a"], "rating": my_row["r"],
             "ovr": my_ovr, "cs": my_cs, "age": my_age, "is_mine": True, "team_id": my_tid}]

    entries = conn.execute(
        "SELECT team_id FROM cl_entries WHERE tournament_id=?", (tid,)).fetchall()
    ALL_POS = GK_POS + DF_POS + MF_POS + ATTACK_POS
    ph = ",".join("?" * len(ALL_POS))
    for e in entries:
        if e["team_id"] == my_tid:
            continue
        rows = conn.execute(
            f"""SELECT ovr, position, sub_role, age FROM ai_players
                WHERE team_id=? AND position IN ({ph})""",
            (e["team_id"], *ALL_POS)).fetchall()
        for r in rows:
            g, a, rt = _estimate_ai_season(r["ovr"], r["position"], 80, 80, r["sub_role"],
                                           full_season_matches=n_games)
            cs = _estimate_ai_clean_sheets(r["position"], r["ovr"], 80, 80, n_games) if r["position"] in GK_POS else 0
            pool.append({"position": r["position"], "goals": g, "assists": a, "rating": rt,
                        "ovr": r["ovr"], "cs": cs, "age": r["age"] or 25, "is_mine": False,
                        "team_id": e["team_id"]})

    _stage_w = _cl_team_stage_weights(conn, tid)
    my_base_score = _position_award_score(my_pos, my_row["g"], my_row["a"], my_row["r"], my_ovr, my_cs)
    my_score = my_base_score * _stage_w(my_tid)

    # [2026-07 신설] 빅게임 보너스 — 결승/준결승/3·4위전 경기의 "실제" 기록만
    # 따로 골라 계산한 값을 가산한다(고정 숫자 아님). 우승하지 못해도 그
    # 무대에서 결정적으로 잘한 선수는 여전히 후보가 될 수 있게 하되, 상한
    # (기준 점수의 10%)을 넘지 못하므로 이 보너스 하나로 MVP가 뒤집히진 않는다.
    # AI 후보는 스테이지별 개인 기록을 추정하지 않으므로(추정치는 대회 전체
    # 뭉뚱그린 값) 이 보너스는 실제 경기별 기록이 있는 내 선수에게만 계산되고,
    # AI 쪽엔 계산 자체가 불가능하다는 점을 감안해 상한을 두었다.
    _bg = conn.execute(
        """SELECT COUNT(*) n, COALESCE(AVG(my_rating),0) r, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a
           FROM cl_matches WHERE tournament_id=? AND my_played=1 AND stage IN ('SF','F','TP')""",
        (tid,)).fetchone()
    if _bg and _bg["n"] > 0:
        _raw_bonus = (_bg["r"] - 6.0) * 1.2 + (_bg["g"] + _bg["a"]) * 0.8
        my_score += _cap_additive_bonus(_raw_bonus, my_base_score, cap_ratio=0.10)

    others = [x for x in pool if not x["is_mine"]]
    best_ai_scorer_g = max((x["goals"] for x in others), default=-1)
    best_ai_mvp_score = max((_position_award_score(x["position"], x["goals"], x["assists"],
                                                    x["rating"], x["ovr"], x["cs"]) * _stage_w(x["team_id"])
                             for x in others), default=-1)
    year = t["year"]
    awards = []
    if my_row["g"] > 0 and my_row["g"] >= best_ai_scorer_g:
        awards.append(("챔피언스리그 득점왕", f"{my_row['g']}골"))
    if my_score >= best_ai_mvp_score:
        awards.append(("챔피언스리그 시즌MVP", f"{year} {t['name']} MVP"))
    for label in _evaluate_extra_awards(pool, my_pos, my_age, weight_fn=lambda x: _stage_w(x["team_id"]),
                                         young_age_cutoff=23):
        awards.append((f"챔피언스리그 {label}", f"{year} {t['name']} {label}"))
    if (my_pos in GK_POS and my_cs >= 2
            and _gk_quality_ok(my_row["sv"], my_row["gc"], n_games, n_games, min_play_ratio=0.0)):
        gk_group = [x for x in pool if x["position"] in GK_POS]
        best_gk = max(gk_group, key=lambda x: x["cs"]) if gk_group else None
        if best_gk and best_gk["is_mine"]:
            awards.append(("챔피언스리그 골든글러브", f"{my_cs} 클린시트"))

    for atype, detail in awards:
        add_log(f"🏅 {atype} 수상! ({detail})", "event")
        conn.execute(
            "INSERT INTO awards(year,award_type,league_name,detail,is_mine) VALUES(?,?,?,?,1)",
            (year, atype, t["name"], detail))
    if awards:
        conn.commit()
    conn.close()


def _finish_tournament(t):
    """[2026-08 리팩터링] competition_common.finish_tournament으로 이동
    (완전 동일 로직) — 시상 함수(_award_cl_awards)만 그대로 넘겨서 이전과
    동일하게 결승 종료 시 호출되게 한다."""
    from competition_common import finish_tournament
    finish_tournament(CHAMPIONS_CFG, t, award_fn=_award_cl_awards)


# ─────────────────────────────────────────────
# 내 결과 확정 + 보상
# ─────────────────────────────────────────────

def _record_my_exit(t, result):
    """[2026-08 리팩터링] competition_common.record_my_exit으로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import record_my_exit
    record_my_exit(CHAMPIONS_CFG, t, result)


def _save_trophy(year, team_name, competition, result):
    """[2026-08 리팩터링] competition_common.save_trophy로 이동
    (완전 동일 로직) — 위임만."""
    from competition_common import save_trophy
    save_trophy(CHAMPIONS_CFG, year, team_name, competition, result)


# ─────────────────────────────────────────────
# 챔스 이력 조회 (커리어창 / 은퇴창 공용)
# ─────────────────────────────────────────────

def get_my_cl_all_groups(year):
    """[2026-07 폐기 예정] 스위스 방식 개편으로 '조'가 없어졌다. 예전
    UI(ui/schedule_window.py의 조별리그 표시)와의 하위 호환을 위해 함수
    자체는 남겨두되 항상 None을 반환한다 — 호출부는 get_my_cl_league_standings로
    교체됐다."""
    return None


def get_my_cl_group_info(year):
    """[2026-07 폐기 예정] 위와 동일한 이유로 항상 None. ui/center_panel.py의
    '경기 없는 주차에도 조별리그 context 유지' 분기가 이 함수를 호출하는데,
    None을 반환하면 그 분기는 조용히 건너뛴다(에러 없음) — 리그 스테이지는
    매 주차 실제 경기가 있어서 애초에 그 분기가 필요한 상황 자체가 크게
    줄었다."""
    return None


def get_my_cl_league_standings(year):
    """[UI용/2026-07 신설] 내 대륙 챔스의 리그 스테이지 전체 순위표.
    반환: {"standings": [...], "my_team_id": tid, "direct_cut": N, "playoff_cut": N}
    또는 None (대회가 없거나 아직 리그 스테이지 매치가 없을 때)."""
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return None
    my_tid = p["current_team_id"]
    t = _my_cl_tournament(p, year)
    if not t:
        return None
    rows = get_cl_league_standings(t["id"])
    if not rows:
        return None
    cont = t["continent"]
    return {
        "standings": rows, "my_team_id": my_tid,
        "direct_cut": _cl_direct_cut(cont),
        "playoff_cut": _cl_direct_cut(cont) + _cl_playoff_pool(cont),
    }


def get_my_champions_matches(year):
    """[일정 탭용] 내 팀이 출전한 그 해 챔스의 전체 대진 목록.

    schedule_window의 챔피언스리그 탭이 기대하는 형식으로 반환:
      home_id, away_id, home_name, away_name, home_league, away_league,
      home_score, away_score, pso_winner, pso_score, stage(한글), week
    내 팀이 그 대회에 없으면 빈 리스트.
    """
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return []
    t = _my_cl_tournament(p, year)
    if not t or not t.get("my_in"):
        return []
    # 출전 자격: 등록 당시 팀과 현재 팀이 같을 때만 '내 대회'로 본다.
    #   시즌 중 이적해 들어온 팀의 챔스는 일정에 띄우지 않는다.
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != p.get("current_team_id", 0):
        return []

    conn = get_conn()
    entries = {r["team_id"]: dict(r) for r in conn.execute(
        "SELECT team_id, team_name, flag, country FROM cl_entries WHERE tournament_id=?",
        (t["id"],)).fetchall()}
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM cl_matches WHERE tournament_id=? ORDER BY week, slot",
        (t["id"],)).fetchall()]
    conn.close()

    def _name(tid):
        e = entries.get(tid, {})
        return f"{e.get('flag','')}{e.get('team_name','?')}"

    def _league(tid):
        return entries.get(tid, {}).get("country", "")

    out = []
    for m in rows:
        pso_name = ""
        if m["pso_winner"]:
            pso_name = _name(m["pso_winner"])
        out.append({
            "home_id": m["home_team_id"], "away_id": m["away_team_id"],
            "home_name": _name(m["home_team_id"]), "away_name": _name(m["away_team_id"]),
            "home_league": _league(m["home_team_id"]), "away_league": _league(m["away_team_id"]),
            "home_score": m["home_score"], "away_score": m["away_score"],
            "pso_winner": pso_name, "pso_score": m["pso_score"],
            "stage": STAGE_KO.get(m["stage"], m["stage"]), "week": m["week"],
            "stage_raw": m["stage"], "grp": m["grp"] if "grp" in m.keys() else "",
        })
    return out


def get_my_cl_matches():
    """내가 실제 출전한 챔스 경기 목록 (시간순).
    [2026-07 수정, 신민용 요청] 결장(부상/출전정지) 경기도 포함 —
    "(부상)"/"(출전정지)" 표시를 위해 my_absence_reason을 함께 싣는다."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT m.*, t.year AS t_year, t.name AS comp, t.my_team_id AS t_my_tid
           FROM cl_matches m
           JOIN cl_tournaments t ON m.tournament_id = t.id
           -- [2026-07 재수정, 신민용 지적: "다친 게 아니라 그냥
           -- 벤치라 안 뛴 경기도 있는데 그건 빠진다"] my_played=1
           -- 이거나 absence_reason이 있는 것만 걸렀더니, "건강한데
           -- 로테이션으로 그냥 안 뛴" 경기(my_played=0이면서
           -- absence_reason도 NULL)가 통째로 빠졌다 — 그 팀 소속으로
           -- 치러진 경기는 전부 보여주고(결과가 난 것만), 뛰었는지
           -- 안 뛰었는지는 화면에서 my_played/absence_reason으로
           -- 구분한다.
           WHERE m.is_my = 1 AND m.home_score >= 0
           ORDER BY t.year, m.week""").fetchall()]
    # [2026-08 성능 수정, 신민용 리포트: "재능 좋은 선수로 오래 뛰면
    # 은퇴/커리어창이 심하게 렉걸린다"] 예전엔 cl_entries(세계 전체 챔스
    # 참가팀 이력) 전체를 매번 통째로 로드했다 — 내 경기가 걸쳐있는
    # tournament_id만 걸러서 필요한 만큼만 가져온다.
    _tids = {r["tournament_id"] for r in rows}
    names = {}
    if _tids:
        _ph = ",".join("?" * len(_tids))
        names = {(r["tournament_id"], r["team_id"]): (r["team_name"], r["flag"], r["country"])
                 for r in conn.execute(
                     f"SELECT tournament_id, team_id, team_name, flag, country "
                     f"FROM cl_entries WHERE tournament_id IN ({_ph})",
                     tuple(_tids)).fetchall()}
    conn.close()

    out = []

    for m in rows:
        # [2026-07 버그수정, 신민용 리포트: "옛날 경기의 상대가 그때 내 팀
        # 이름으로 뜬다(자기자신과 붙은 것처럼 보임)"] 예전엔 "현재" 소속팀
        # (p.get("current_team_id"))으로 그 경기 당시 내가 홈/원정 중 어느
        # 쪽이었는지를 판정했다 — 그 이후 이적을 한 번이라도 하면 현재 팀ID가
        # 그 경기의 홈/원정 둘 중 어느 쪽과도 안 맞아떨어지게 되고, 그러면
        # is_home이 무조건 False로 잡혀서 "그 경기 당시 내 팀"이 오히려
        # 상대팀으로 표시되고(홈이었던 경우), 스코어/승패까지 뒤집혀
        # 나왔다. cl_tournaments.my_team_id는 그 대회가 시작될 때 이미
        # "그 시점 내 팀"으로 고정 저장돼 있으므로, 그걸 그대로 쓴다 —
        # 이적을 몇 번을 하든 과거 기록은 항상 그 당시 팀 기준으로 정확하다.
        my_tid = m["t_my_tid"]
        is_home = (m["home_team_id"] == my_tid)
        opp_id = m["away_team_id"] if is_home else m["home_team_id"]
        my_s = m["home_score"] if is_home else m["away_score"]
        op_s = m["away_score"] if is_home else m["home_score"]

        if m["pso_winner"]:
            won = (m["pso_winner"] == (m["home_team_id"] if is_home else m["away_team_id"]))
            result = "승(PSO)" if won else "패(PSO)"
        elif my_s > op_s:
            result = "승"
        elif my_s < op_s:
            result = "패"
        else:
            result = "무"

        my_name, my_flag, _my_country = names.get(
            (m["tournament_id"], m["home_team_id"] if is_home else m["away_team_id"]), ("", "", ""))
        opp_name, opp_flag, opp_country = names.get((m["tournament_id"], opp_id), ("?", "", ""))
        # [2026-07 신민용 요청] 챔스는 국제대회라 팀명만으론 어느 나라 팀인지
        # 안 보여서, 상대 팀명 옆에 (국가)를 붙인다.
        if opp_country:
            opp_name = f"{opp_name}({opp_country})"

        from constants import day_to_iso_date_str, week_to_iso_date_str
        date_str = (day_to_iso_date_str(m["t_year"], m["day"]) if m.get("day")
                    else week_to_iso_date_str(m["t_year"], m["week"]))

        out.append({
            "year": m["t_year"], "week": m["week"], "date": date_str,
            "position": m["my_position"], "team": my_name, "team_flag": my_flag,
            "comp": m["comp"], "stage": STAGE_KO.get(m["stage"], m["stage"]),
            "opp": opp_name, "opp_flag": opp_flag,
            "goals": m["my_goals"], "assists": m["my_assists"],
            "saves": m["my_saves"], "conceded": op_s,
            "rating": m["my_rating"],
            "shots": m.get("my_shots", 0), "shots_on": m.get("my_shots_on", 0),
            "key_passes": m.get("my_key_passes", 0), "dribbles": m.get("my_dribbles", 0),
            "blocks": m.get("my_blocks", 0), "pass_acc": m.get("my_pass_acc", 0),
            "score": f"{my_s}-{op_s}", "result": result,
            "absence_reason": m.get("my_absence_reason"),
            "my_played": m.get("my_played", 0),
        })
    return out