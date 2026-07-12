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
from constants import get_league_grade  # 클럽 대항전 슬롯 계산용(국가대표 grade와 분리)
from constants import generate_round_robin  # 리그 스테이지 대진(원형법) 생성용

# ── 대회 일정 (주차) - 2026-07 스위스 방식 개편 ─────────────────────
# 클럽 시즌이 4~43주라 8주(draw)부터 시작해도 여유가 충분하다. 리그
# 스테이지(최대 8경기)를 9~16주에 깔고, 17주 플레이오프, 18~21주에
# 16강부터 결승까지 이어붙인다. 국내 컵대회는 이 구간(8~21주)을 피해서
# 시작하도록 cup_engine.CUP_ROUND_WEEKS_POOL도 함께 뒤로 밀었다.
CL_START_WEEK = 8            # 추첨 (직전 시즌 최종 순위 기준)
CL_LEAGUE_WEEKS = (9, 16)    # 리그 스테이지 최대 구간(9~16주, 실제 사용 주차 수는 대륙별로 다름)
CL_PLAYOFF_WEEK = 17         # 플레이오프 (9~24위, 단판)
CL_ROUND_WEEKS = {
    "R16": 18,
    "QF":  19,
    "SF":  20,
    "F":   21,
    "TP":  21,  # 3/4위전: 결승과 같은 주차
}
CL_END_WEEK = 21

CL_TEAMS = 36                # 기본(유럽/북남미) 리그 스테이지 참가 규모

# [2026-07 스위스 방식] 대륙별 참가 규모 - 실제 UEFA(36팀)를 기준으로,
# 아시아/아프리카는 클럽 리그 인프라가 상대적으로 얕다는 기존 설계 원칙을
# 유지해 정확히 절반 규모(18팀)로 낮췄다. 그 절반 비율을 경기 수·컷
# 라인에도 그대로 적용해서(8경기→4경기, 8+16+12→4+8+6) 대륙마다 리그
# 스테이지 순위표에서의 '직행/플레이오프/광탈' 비중이 똑같이 유지된다.
CL_TEAMS_BY_CONTINENT = {"유럽": 36, "북남미": 36, "아시아": 18, "아프리카": 18}

# 팀마다 리그 스테이지에서 치르는 경기 수(서로 다른 상대와 1경기씩).
CL_LEAGUE_GAMES_BY_CONTINENT = {"유럽": 8, "북남미": 8, "아시아": 4, "아프리카": 4}

# 리그 스테이지 순위 1~N위: 플레이오프 없이 바로 다음 토너먼트 라운드 직행.
CL_DIRECT_CUT_BY_CONTINENT = {"유럽": 8, "북남미": 8, "아시아": 4, "아프리카": 4}

# 리그 스테이지 순위 (직행 다음순위)~(직행+이 값)위: 플레이오프 대상.
# 이 인원의 절반이 플레이오프를 통과해 직행팀과 합류한다.
#   유럽/북남미: 9~24위(16명) 플레이오프 → 8명 통과 → 직행 8 + 통과 8 = 16강(16팀)
#   아시아/아프리카: 5~12위(8명) 플레이오프 → 4명 통과 → 직행 4 + 통과 4 = 8강(8팀)
CL_PLAYOFF_POOL_BY_CONTINENT = {"유럽": 16, "북남미": 16, "아시아": 8, "아프리카": 8}

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
CL_SLOTS_BY_GRADE = {"SS": 5, "S": 4, "A": 3, "B": 2, "C": 1, "D": 1, "E": 1, "F": 1}

# ── entry 캐시 ─────────────────────────────────────
# cl_entries(ovr/flag/team_name/grade)는 대회 진행 중 바뀌지 않으므로
# (tournament_id, team_id) 별로 1회만 조회하고 재사용한다.
# 새 토너먼트 생성 시 _clear_entry_cache() 로 비운다.
_entry_cache = {}

def _clear_entry_cache():
    _entry_cache.clear()

STAGE_KO = {"league": "리그 스테이지", "PO": "플레이오프",
            "R16": "16강", "QF": "8강", "SF": "4강", "F": "결승", "TP": "3/4위전"}
# 토너먼트 라운드 진행 순서 (플레이오프 다음부터)
_STAGE_ORDER = ["R16", "QF", "SF", "F"]

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


def get_my_cl_match(week):
    """이번 주차에 내가 뛸 챔스 경기가 있으면 dict, 없으면 None."""
    from game_engine import get_player, get_state
    p = get_player()
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
    p = get_player()
    if not p:
        return
    prev_season = season - 1
    if prev_season < 1:
        return   # 첫 시즌엔 참고할 직전 시즌 성적이 없음 → 챔스 생략

    # 이미 만들어졌으면(어느 대륙이든) 중복 생성 방지
    if get_cl_tournament(year, "유럽"):
        return

    _clear_entry_cache()   # 새 시즌 대회 → 이전 캐시 무효화

    my_cont = _my_continent(p)
    my_tid = p.get("current_team_id", 0)

    for cont in ("유럽", "아시아", "아프리카", "북남미"):
        entries = _select_entries(cont, prev_season)
        if len(entries) < 4:
            continue  # 출전팀 부족하면 그 대륙 대회 생략
        _build_tournament(year, cont, entries, my_tid if cont == my_cont else 0)

    # ── 내 대회 안내 로그 (출전 자격 = 직전 시즌 내 리그 순위가 배정 슬롯 안) ──
    if my_cont and my_tid:
        t = get_cl_tournament(year, my_cont)
        if t:
            # 출전 자격 판정: 내 팀이 직전 시즌 '내 1부 리그'에서 CL 슬롯 안에 들었는가?
            qualified = _is_my_team_cl_qualified(p, my_tid, prev_season)
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


def _is_my_team_cl_qualified(p, my_tid, season):
    """내 팀이 그 시즌 '내 1부 리그'에서 CL 슬롯(CL_SLOTS_BY_GRADE) 안에 드는지
    — 챔스 출전 자격 판정. 챔스는 1부(tier=1) 리그 소속만 자격이 있고,
    그 나라 등급에 따라 1~4위까지도 출전할 수 있다(2부 이하는 자격 없음)."""
    if not my_tid:
        return False
    from game_engine import get_league_standings
    conn = get_conn()
    row = conn.execute(
        "SELECT league_id FROM teams WHERE id=?", (my_tid,)).fetchone()
    if not row:
        conn.close()
        return False
    lid = row["league_id"]
    lg_row = conn.execute(
        """SELECT l.tier AS tier, cn.name AS country FROM leagues l
           JOIN countries cn ON l.country_id = cn.id WHERE l.id=?""", (lid,)).fetchone()
    conn.close()
    # 1부가 아니면 챔스 자격 없음 (2부 1위는 승격 대상일 뿐)
    if not lg_row or lg_row["tier"] != 1:
        return False
    # [버그 수정] 국가대표 grade가 아니라 클럽 리그 grade로 슬롯 수를 정한다.
    league_grade = get_league_grade(lg_row["country"], "F")
    slots = CL_SLOTS_BY_GRADE.get(league_grade, 1)
    standings = get_league_standings(lid, season=season)
    if not standings:
        return False
    my_rank = next((i for i, r in enumerate(standings, start=1) if r["id"] == my_tid), None)
    return my_rank is not None and my_rank <= slots


def _select_entries(continent, season):
    """대륙 소속 각 1부 리그에서, 나라 등급별 슬롯 수(CL_SLOTS_BY_GRADE)만큼
    순위표 상위팀을 뽑는다 (최대 32).

    규칙:
      - 나라 등급이 높을수록(S~F) 한 나라에서 나가는 팀 수가 많음 (최대 4장)
      - 등급 높은 나라부터 순서대로 슬롯을 소진, 32장에서 컷
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
        lg["grade"] = get_league_grade(lg["country"], "F")

    # 등급 높은 나라 우선 (정원 초과 시 컷 기준 + 슬롯 배정 우선순위)
    grade_rank = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
    leagues.sort(key=lambda r: -grade_rank.get(r["grade"], 0))

    picked = []
    for lg in leagues:
        remaining = cap - len(picked)
        if remaining <= 0:
            break
        slots = min(CL_SLOTS_BY_GRADE.get(lg["grade"], 1), remaining)
        rows = get_league_standings(lg["lid"], season=season)
        if not rows:
            continue
        for rank, row in enumerate(rows[:slots], start=1):
            picked.append(_entry_from(lg, row, cl_rank=rank))
        if len(picked) >= cap:
            break
    return picked[:cap]


def _entry_from(lg, standing_row, cl_rank=1):
    """리그 + 순위표 한 행 → entry dict + 팀 전력(OVR) 계산."""
    from game_engine import get_conn as _gc
    tid = standing_row["id"]
    conn = _gc()
    row = conn.execute("SELECT AVG(ovr) AS v FROM ai_players WHERE team_id=?", (tid,)).fetchone()
    conn.close()
    ovr = (row["v"] if row and row["v"] else 50) + random.uniform(-2, 2)
    return {
        "team_id": tid,
        "team_name": standing_row["name"],
        "flag": lg["flag"],
        "country": lg["country"],
        "grade": lg["grade"],
        "ovr": ovr,
        "cl_rank": cl_rank,
    }


def _league_phase_pairs(entries, games, my_tid):
    """[2026-07 신설] 리그 스테이지 대진 생성 - generate_round_robin(n)으로
    n팀 전체 라운드로빈 순서를 만든 뒤 앞의 `games`라운드만 쓴다(각 팀이
    서로 다른 `games`팀과 정확히 1경기씩). 순서를 몇 번 무작위로 섞어
    같은 나라 팀끼리의 매치업이 가장 적은 배치를 고른다(완벽 회피는
    보장하지 않음 - 조별리그 추첨 때와 같은 절충).
    반환: [(round_idx, home_entry, away_entry), ...]"""
    n = len(entries)
    best_order, best_conflicts = None, None
    for _try in range(6):
        order = entries[:]
        random.shuffle(order)
        rounds = generate_round_robin(n)[:games]
        conflicts = sum(
            1 for rd in rounds for a, b in rd
            if order[a]["country"] == order[b]["country"])
        if best_conflicts is None or conflicts < best_conflicts:
            best_order, best_conflicts = order, conflicts
        if conflicts == 0:
            break

    rounds = generate_round_robin(n)[:games]
    pairs = []
    for rd_idx, rd in enumerate(rounds):
        for a, b in rd:
            home, away = (best_order[a], best_order[b]) if rd_idx % 2 == 0 \
                         else (best_order[b], best_order[a])
            pairs.append((rd_idx, home, away))
    return pairs


def _build_tournament(year, continent, entries, my_tid):
    """대회 row + 출전팀 + 리그 스테이지 일정 생성 (2026-07 스위스 방식)."""
    name = CL_CUP_NAME.get(continent, "챔피언스리그")
    games = _cl_league_games(continent)

    # 대륙 정원만큼 정규화 (부족하면 가능한 만큼, 단 대진 생성을 위해 짝수로 컷)
    entries.sort(key=lambda e: e["ovr"], reverse=True)
    n = len(entries) - (len(entries) % 2)
    n = min(n, _cl_team_cap(continent))
    entries = entries[:n]
    if n < games + 1:
        return  # 리그 스테이지를 치르기엔 참가팀이 너무 적음

    # 내 팀이 출전팀(=리그 1위 등 슬롯 안)인지. = 출전 자격(my_qualified)과 동일.
    my_in = 1 if (my_tid and any(e["team_id"] == my_tid for e in entries)) else 0
    my_reg_tid = my_tid if my_in else 0

    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO cl_tournaments(year, continent, name, status,
                    my_in, my_team_id, my_qualified)
                 VALUES(?,?,?,?,?,?,?)""",
              (year, continent, name, "league", my_in, my_reg_tid, my_in))
    tid = c.lastrowid

    entry_rows = [(tid, e["team_id"], e["team_name"], e["flag"],
                   e["country"], e["grade"], e["ovr"]) for e in entries]
    c.executemany("""INSERT INTO cl_entries
                         (tournament_id, team_id, team_name, flag, country,
                          grade, ovr, alive)
                         VALUES(?,?,?,?,?,?,?,1)""", entry_rows)

    # ── 리그 스테이지 일정 (팀마다 서로 다른 `games`팀과 1경기씩) ──
    w0 = CL_LEAGUE_WEEKS[0]
    match_rows = []
    for rd_idx, home, away in _league_phase_pairs(entries, games, my_tid):
        wk = w0 + rd_idx
        is_my = 1 if my_tid in (home["team_id"], away["team_id"]) else 0
        match_rows.append((tid, "league", wk,
                   home["team_id"], away["team_id"], is_my))
    c.executemany("""INSERT INTO cl_matches
                             (tournament_id, stage, week,
                              home_team_id, away_team_id,
                              home_score, away_score, is_my, slot)
                             VALUES(?,?,?,?,?,-1,-1,?,0)""", match_rows)
    c.execute("UPDATE cl_tournaments SET status='league', first_stage='league' WHERE id=?",
              (tid,))
    conn.commit()
    conn.close()


def _first_stage_for(n):
    """출전팀 수 n에 맞는 첫 토너먼트 라운드 스테이지 (직행+플레이오프 통과 합계 기준)."""
    if n >= 16:
        return "R16"
    if n >= 8:
        return "QF"
    if n >= 4:
        return "SF"
    return "F"


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
           WHERE tournament_id=? AND week<=? AND home_score=-1""",
        (t["id"], week)).fetchall()]

    # pending 경기를 한 커넥션·한 트랜잭션으로 일괄 시뮬(경기마다 개폐하던 것을 1회로).
    for m in pending:
        _sim_ai_match(t, m, conn=conn)
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
    key = (tid, team_id)
    cached = _entry_cache.get(key)
    if cached is not None:
        return cached
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM cl_entries WHERE tournament_id=? AND team_id=?",
        (tid, team_id)).fetchone()
    conn.close()
    val = dict(row) if row else {"ovr": 50, "flag": "", "team_name": "?", "grade": "F"}
    _entry_cache[key] = val
    return val


def _match_outcome(h_ovr, a_ovr):
    """중립 구장 가정. 'home'/'draw'/'away' (KO 무승부 → 승부차기).
    [수정] 무승부 확률을 전력차에 반비례하도록 개선 (기존 dw=0.22 고정)."""
    diff = h_ovr - a_ovr
    hw = max(0.08, min(0.85, 0.46 + diff * 0.014))
    dw = max(0.08, 0.24 - abs(diff) * 0.005)
    aw = max(0.05, 1.0 - hw - dw)
    tot = hw + dw + aw
    hw, dw, aw = hw / tot, dw / tot, aw / tot
    roll = random.random()
    if roll < hw:
        return "home"
    elif roll < hw + dw:
        return "draw"
    return "away"


def _resolve_pso(h_ovr, a_ovr):
    p_home = 0.5 + max(-0.1, min(0.1, (h_ovr - a_ovr) * 0.006))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


def _sim_ai_match(t, m, my_played=False, conn=None):
    """AI끼리(또는 내가 결장한 내 경기) 시뮬.

    conn: 외부에서 연 커넥션을 재사용해 다수 경기를 한 트랜잭션으로 묶는다.
          None이면 자체 커넥션을 열고 commit/close(기존 동작 = 하위 호환).
    """
    from game_engine import add_log, get_player, _gen_score, _week_intl_cl_day
    he = _entry(t["id"], m["home_team_id"])
    ae = _entry(t["id"], m["away_team_id"])

    outcome = _match_outcome(he["ovr"], ae["ovr"])
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "league")  # [2026-07 버그 수정] 조별리그->리그 스테이지 개편 후 남아있던 옛 스테이지명 비교
    if outcome == "draw" and is_ko:
        win_home, pso_score = _resolve_pso(he["ovr"], ae["ovr"])
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, he["ovr"] - ae["ovr"])

    # [2026-07 신설] 실제 진행 날짜 저장 (커리어/은퇴창 표시용).
    # [2026-07 성능 수정] cup_engine._sim_ai_match와 동일한 이유 — 이 값은
    # my_played=1인 "내 경기" 행만 읽으므로 AI vs AI 경기에서는 계산 자체가
    # 낭비다(한 라운드당 AI 경기가 수백~수천 건이라 매번 get_player() DB
    # 조회를 아끼는 효과가 크다).
    day = _week_intl_cl_day(m["week"], get_player() or {}) if m["is_my"] else 0

    _own = conn is None
    if _own:
        conn = get_conn()
    conn.execute("""UPDATE cl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?, day=? WHERE id=?""",
                 (hs, as_, pso_winner, pso_score, day, m["id"]))
    if _own:
        conn.commit()
        conn.close()

    # 내 팀 경기(결장 포함)면 로그. AI끼리 경기는 get_player() 불필요.
    if m["is_my"]:
        p = get_player()
        my_tid = p.get("current_team_id", 0) if p else 0
        if my_tid in (m["home_team_id"], m["away_team_id"]):
            stage_ko = STAGE_KO.get(m["stage"], "")
            pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
            add_log(f"🏆 {t['name']} {stage_ko}  "
                    f"{he['flag']}{he['team_name']} {hs}-{as_} {ae['flag']}{ae['team_name']}{pso_txt}",
                    "match")
            if not my_played:
                add_log("   🚑 부상으로 챔스 경기 결장", "match")


def _winner_of(m):
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home_team_id"] if m["home_score"] > m["away_score"] else m["away_team_id"]


# ─────────────────────────────────────────────
# 내 경기 시뮬
# ─────────────────────────────────────────────

def simulate_my_cl_match(week, p):
    """내가 출전하는 챔스 경기."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail)
    info = get_my_cl_match(week)
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

    # 내 출전 보너스 (클럽 리그 경기와 동일: 격차 기반 에이스 영향력)
    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = _my_ovr - _team_ovr
    bonus = min(max(0.0, _gap) * 0.32 + _my_ovr * 0.05, 14.0)
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = _match_outcome(h_ovr, a_ovr)
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "league")  # [2026-07 버그 수정] 조별리그->리그 스테이지 개편 후 남아있던 옛 스테이지명 비교
    if outcome == "draw" and is_ko:
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    goals, assists, saves, rating, events, detail = _player_perf(p, outcome, is_home, hs, as_)
    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    # [2026-07 신설] 실제 진행 날짜 저장 (커리어/은퇴창 표시용).
    from game_engine import _week_intl_cl_day
    day = _week_intl_cl_day(m["week"], p)

    conn = get_conn()
    conn.execute("""UPDATE cl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?,
                    my_played=1, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?,
                    day=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score,
                  _get_field_pos(p),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  my_conceded, day, m["id"]))
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
    ns = min(100, p2["stress"] + 8)
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
    home_disp = f"{he['flag']}{he['team_name']}"
    away_disp = f"{ae['flag']}{ae['team_name']}"
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
    """챔스 리그 스테이지 순위: 승점 → 득실 → 다득점 → 전력.
    (조가 없어졌으므로 대회 전체 팀을 한 표로 정렬 - 실제 UEFA 리그
    스테이지와 동일한 방식.)"""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM cl_entries WHERE tournament_id=?", (tid,)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        """SELECT * FROM cl_matches WHERE tournament_id=?
           AND stage='league' AND home_score>=0""", (tid,)).fetchall()]
    conn.close()

    tbl = {e["team_id"]: {"team_id": e["team_id"], "team_name": e["team_name"],
                          "flag": e["flag"], "ovr": e["ovr"],
                          "country": e["country"] if "country" in e.keys() else "",
                          "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    for m in matches:
        h, a = tbl.get(m["home_team_id"]), tbl.get(m["away_team_id"])
        if not h or not a:
            continue
        hs, as_ = m["home_score"], m["away_score"]
        h["p"] += 1; a["p"] += 1
        h["gf"] += hs; h["ga"] += as_
        a["gf"] += as_; a["ga"] += hs
        if hs > as_:
            h["w"] += 1; h["pts"] += 3; a["l"] += 1
        elif hs < as_:
            a["w"] += 1; a["pts"] += 3; h["l"] += 1
        else:
            h["d"] += 1; a["d"] += 1; h["pts"] += 1; a["pts"] += 1
    rows = list(tbl.values())
    rows.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]), reverse=True)
    return rows


def _finalize_league_phase(t):
    """[2026-07 신설] 챔스 리그 스테이지 종료 → 순위 확정 → 1~직행컷위는
    바로 다음 토너먼트 라운드 직행, 그다음 플레이오프컷 인원은 플레이오프
    대상, 나머지는 광탈. 실제 UEFA(1~8위 직행/9~24위 PO/25~36위 광탈)와
    같은 비율을 대륙별 참가 규모에 맞춰 그대로 적용한다."""
    from game_engine import add_log, get_player
    tid = t["id"]
    cont = t["continent"]
    direct_cut = _cl_direct_cut(cont)
    po_pool = _cl_playoff_pool(cont)

    rows = get_cl_league_standings(tid)
    direct = rows[:direct_cut]
    playoff_teams = rows[direct_cut:direct_cut + po_pool]
    eliminated = rows[direct_cut + po_pool:]

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    conn = get_conn(); c = conn.cursor()
    for r in eliminated:
        c.execute("UPDATE cl_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                  (tid, r["team_id"]))

    # ── 플레이오프 대진: 시드(상위 절반)가 넌시드(하위 절반)를 상대로 홈 개최.
    #   1번 시드가 최약체 넌시드를, 마지막 시드가 최강 넌시드를 만나는
    #   실제 UEFA 방식(높은 시드일수록 상대적으로 편한 대진)을 그대로 따른다.
    half = len(playoff_teams) // 2
    seeded, unseeded = playoff_teams[:half], playoff_teams[half:]
    po_pairs = list(zip(seeded, reversed(unseeded)))

    if po_pairs:
        for slot, (home, away) in enumerate(po_pairs):
            is_my = 1 if my_tid in (home["team_id"], away["team_id"]) else 0
            c.execute("""INSERT INTO cl_matches
                         (tournament_id, stage, week, home_team_id, away_team_id,
                          home_score, away_score, is_my, slot)
                         VALUES(?,?,?,?,?,-1,-1,?,?)""",
                      (tid, "PO", CL_PLAYOFF_WEEK,
                       home["team_id"], away["team_id"], is_my, slot))
        c.execute("UPDATE cl_tournaments SET status='playoff' WHERE id=?", (tid,))
        conn.commit()
        conn.close()
    else:
        # [버그 수정] _start_knockout도 같은 풀 커넥션을 열어서 자체적으로
        # commit/close 한다 — 여기서 트랜잭션을 먼저 끝내지 않고 호출하면
        # 같은 커넥션 위에 트랜잭션이 중첩돼 다른 화면(주간 진행 등)에서
        # "cannot start a transaction within a transaction"으로 이어질 수
        # 있다. 플레이오프 대상이 아예 없을 만큼 참가 규모가 작으면
        # 직행팀만으로 바로 KO — 먼저 커밋/닫고 나서 호출한다.
        conn.commit()
        conn.close()
        _start_knockout(t, [r["team_id"] for r in direct])

    add_log(f"🏆 {t['name']} 리그 스테이지 종료 → 1~{direct_cut}위 직행, "
            f"{direct_cut+1}~{direct_cut+po_pool}위 플레이오프", "event")
    if my_tid and any(r["team_id"] == my_tid for r in eliminated):
        _record_my_exit(t, "리그 스테이지")


def _finalize_playoff(t):
    """[2026-07 신설] 플레이오프 종료 → 승자 + 리그 스테이지 직행팀을 합쳐
    첫 토너먼트 라운드(16강 또는 8강) 대진 생성."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    po_matches = [dict(r) for r in conn.execute(
        "SELECT * FROM cl_matches WHERE tournament_id=? AND stage='PO' ORDER BY slot",
        (tid,)).fetchall()]
    direct_ids = [r["team_id"] for r in conn.execute(
        "SELECT team_id FROM cl_entries WHERE tournament_id=? AND alive=1", (tid,)).fetchall()]
    conn.close()

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    winners, losers = [], []
    conn = get_conn(); c = conn.cursor()
    for m in po_matches:
        w = _winner_of(m)
        l = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
        winners.append(w)
        losers.append(l)
        c.execute("UPDATE cl_entries SET alive=0 WHERE tournament_id=? AND team_id=?", (tid, l))
        if my_tid and l == my_tid:
            conn.commit(); conn.close()
            _record_my_exit(t, "플레이오프")
            conn = get_conn(); c = conn.cursor()
    conn.commit(); conn.close()

    # direct_ids에는 PO 참가팀(현재 alive=1인 채로 남아있던 팀 중 winners 아직 안 뺀 것)이
    # 섞여 있지 않다 - PO 참가팀은 alive=1 상태로 대기했으므로 direct_ids에 winners/losers도
    # 포함돼 있을 수 있어, 최종 진출팀은 '직행팀(PO 미참가)' + 'PO 승자'로 명시적으로 합친다.
    po_team_ids = {m["home_team_id"] for m in po_matches} | {m["away_team_id"] for m in po_matches}
    direct_only = [tid_ for tid_ in direct_ids if tid_ not in po_team_ids]
    qualifiers = direct_only + winners

    add_log(f"🏆 {t['name']} 플레이오프 종료 → {STAGE_KO.get(_first_stage_for(len(qualifiers)), '')} 진출팀 확정", "event")
    _start_knockout(t, qualifiers)


def _start_knockout(t, qualifier_ids):
    """[2026-07 신설] 확정된 진출팀 목록으로 첫 토너먼트 라운드 대진을 만든다.
    전력순으로 정렬해 1번 시드 vs 최약체, 2번 시드 vs 차약체 식으로 짝지어
    (실제 대회 추첨과 완전히 같진 않지만) 강팀끼리 초반에 만나는 걸 줄인다."""
    from game_engine import get_player
    tid = t["id"]
    conn = get_conn()
    infos = {r["team_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM cl_entries WHERE tournament_id=?", (tid,)).fetchall()}
    conn.close()

    ranked = sorted(qualifier_ids, key=lambda tid_: infos.get(tid_, {}).get("ovr", 0), reverse=True)
    half = len(ranked) // 2
    top, bottom = ranked[:half], ranked[half:]
    pairs = list(zip(top, reversed(bottom)))

    first_stage = _first_stage_for(len(ranked))
    next_week = CL_ROUND_WEEKS.get(first_stage, CL_ROUND_WEEKS["R16"])

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    conn = get_conn(); c = conn.cursor()
    for slot, (home, away) in enumerate(pairs):
        is_my = 1 if my_tid in (home, away) else 0
        c.execute("""INSERT INTO cl_matches
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,-1,-1,?,?)""",
                  (tid, first_stage, next_week, home, away, is_my, slot))
    c.execute("UPDATE cl_tournaments SET status='ko', first_stage=? WHERE id=?",
              (first_stage, tid))
    conn.commit()
    conn.close()


def _advance_round(t, cur_stage, next_stage):
    """현재 KO 라운드 종료 → 패자 탈락, 다음 라운드 대진 생성."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        """SELECT * FROM cl_matches WHERE tournament_id=? AND stage=?
           ORDER BY slot""", (tid, cur_stage)).fetchall()]
    conn.close()
    if not cur:
        return

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    cur_stage_ko = STAGE_KO.get(cur_stage, "")
    next_week = CL_ROUND_WEEKS[next_stage]

    is_sf = (cur_stage == "SF")

    winners = []
    losers  = []
    conn = get_conn()
    c = conn.cursor()
    # 탈락 결과 라벨: 토너먼트에서 진 라운드 = '도달한 라운드'로 기록한다.
    #   16강에서 졌으면 '16강 진출'까지가 성취 → '16강 탈락'(X)이 아니라 '16강'(O).
    #   (월드컵 intl_engine 과 동일한 관례.)
    exit_label = cur_stage_ko
    for m in cur:
        w = _winner_of(m)
        loser = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
        winners.append((m["slot"], w))
        # SF 패자는 3/4위전을 뛰므로 즉시 alive=0 처리하지 않음
        if not is_sf:
            c.execute("UPDATE cl_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                      (tid, loser))
            if my_tid and loser == my_tid:
                conn.commit(); conn.close()
                _record_my_exit(t, exit_label)
                conn = get_conn(); c = conn.cursor()
        else:
            losers.append(loser)

    winners.sort()
    for slot in range(0, len(winners), 2):
        if slot + 1 >= len(winners):
            break
        home, away = winners[slot][1], winners[slot + 1][1]
        is_my = 1 if my_tid in (home, away) else 0
        c.execute("""INSERT INTO cl_matches
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,-1,-1,?,?)""",
                  (tid, next_stage, next_week, home, away, is_my, slot // 2))

    # SF 종료 시 패자 2팀으로 3/4위전 생성 (결승과 같은 주차)
    if is_sf and len(losers) == 2:
        tp_home, tp_away = losers[0], losers[1]
        tp_week = CL_ROUND_WEEKS["TP"]
        is_my_tp = 1 if my_tid in (tp_home, tp_away) else 0
        c.execute("""INSERT INTO cl_matches
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,-1,-1,?,999)""",
                  (tid, "TP", tp_week, tp_home, tp_away, is_my_tp))
        te_h = _entry(tid, tp_home); te_a = _entry(tid, tp_away)
        add_log(f"🥉 {t['name']} 3/4위전: {te_h['team_name']} vs {te_a['team_name']} ({tp_week}주차)", "event")

    conn.commit()
    conn.close()
    # 진행 상황 로그: 내 팀이 참가했고, 아직 탈락 전일 때만
    if t["my_in"]:
        conn = get_conn()
        mr = conn.execute("SELECT my_result FROM cl_tournaments WHERE id=?", (tid,)).fetchone()
        conn.close()
        if not (mr and mr["my_result"]):
            add_log(f"🏆 {t['name']} {cur_stage_ko} 종료 → {STAGE_KO[next_stage]} 대진 확정", "event")


def _finish_tournament(t):
    """결승 + 3/4위전 종료 → 우승팀·3위 확정, 내 결과 기록."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    fm = conn.execute(
        """SELECT * FROM cl_matches WHERE tournament_id=? AND stage='F'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
    tp = conn.execute(
        """SELECT * FROM cl_matches WHERE tournament_id=? AND stage='TP'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
    conn.close()
    if not fm:
        return
    fm = dict(fm)
    winner = _winner_of(fm)
    runner = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    third = fourth = None
    if tp:
        tp = dict(tp)
        third  = _winner_of(tp)
        fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]

    conn = get_conn()
    conn.execute("UPDATE cl_tournaments SET status='done', winner_team_id=? WHERE id=?",
                 (winner, tid))
    conn.execute("UPDATE cl_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                 (tid, runner))
    if fourth:
        conn.execute("UPDATE cl_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                     (tid, fourth))
    conn.commit()
    conn.close()

    we = _entry(tid, winner)
    add_log(f"🏆 {t['name']} 우승: {we['flag']}{we['team_name']}!", "event")
    if third:
        te = _entry(tid, third)
        add_log(f"🥉 {t['name']} 3위: {te['flag']}{te['team_name']}", "event")

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    if my_tid == winner:
        _record_my_exit(t, "우승")
    elif my_tid == runner:
        _record_my_exit(t, "준우승")
    elif my_tid == third:
        _record_my_exit(t, "3위")
    elif my_tid == fourth:
        _record_my_exit(t, "4위")


# ─────────────────────────────────────────────
# 내 결과 확정 + 보상
# ─────────────────────────────────────────────

def _record_my_exit(t, result):
    """내 팀의 챔스 최종 성적 확정: 트로피 + 보상."""
    from game_engine import add_log, get_player, update_player
    p = get_player()
    if not p:
        return
    my_tid = p.get("current_team_id", 0)

    conn = get_conn()
    conn.execute("UPDATE cl_tournaments SET my_result=? WHERE id=?", (result, t["id"]))
    # 내 팀명 + 국가명 (예: "Paradou AC (알제리)")
    te = conn.execute(
        "SELECT team_name, country FROM cl_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], my_tid)).fetchone()
    conn.commit()
    conn.close()
    _raw_name = te["team_name"] if te else ""
    _country  = te["country"]   if te else ""
    team_name = f"{_raw_name} ({_country})" if _raw_name and _country else _raw_name

    _save_trophy(t["year"], team_name, t["name"], result)

    # 이번 대회 개인 기록 집계 → cl_history (월드컵 intl_history와 동일)
    conn = get_conn()
    agg = conn.execute(
        """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM cl_matches
           WHERE tournament_id=? AND my_played=1""", (t["id"],)).fetchone()
    # 같은 연도·대회 중복 방지
    exists = conn.execute(
        "SELECT id FROM cl_history WHERE year=? AND competition=?",
        (t["year"], t["name"])).fetchone()
    if not exists:
        conn.execute("""INSERT INTO cl_history(year, competition, team_name, result,
                                               goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (t["year"], t["name"], team_name, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()

    fame_g, pop_g, hap_g = _REWARD.get(result, (0, 0, 0))
    update_player(
        fame=min(100, p.get("fame", 0) + fame_g),
        popularity=min(100, p.get("popularity", 0) + pop_g),
        happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
    )

    icon = "🏆" if result == "우승" else "🏅"
    add_log(f"{icon} {t['year']}년 {t['name']} 최종 성적: {result}  "
            f"(명성 +{fame_g}, 인기 +{pop_g})", "event")


def _save_trophy(year, team_name, competition, result):
    """trophy_log에 챔스 결과 기록 (tier=-1로 클럽 국제대회 구분)."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=?",
        (year, competition)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,-1,?)""", (year, team_name, result, competition))
        conn.commit()
    conn.close()


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
    """내가 실제 출전한 챔스 경기 목록 (시간순). 결장 경기는 제외."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT m.*, t.year AS t_year, t.name AS comp
           FROM cl_matches m
           JOIN cl_tournaments t ON m.tournament_id = t.id
           WHERE m.my_played = 1
           ORDER BY t.year, m.week""").fetchall()]
    names = {(r["tournament_id"], r["team_id"]): (r["team_name"], r["flag"])
             for r in conn.execute(
                 "SELECT tournament_id, team_id, team_name, flag FROM cl_entries").fetchall()}
    conn.close()

    out = []
    from game_engine import get_player
    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    for m in rows:
        # 내 팀 식별: 그 경기 시점 소속을 알 수 없으므로 양쪽 중
        # my_played 행 기준으로 현재 팀이 끼어있으면 그쪽을, 아니면 home 기준.
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

        my_name, my_flag = names.get((m["tournament_id"], m["home_team_id"] if is_home else m["away_team_id"]), ("", ""))
        opp_name, opp_flag = names.get((m["tournament_id"], opp_id), ("?", ""))

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
        })
    return out