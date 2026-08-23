"""
intl_engine.py ─ 국제대회(월드컵/대륙컵) 엔진

시즌 중 17~24주 국제대회 윈도우에서 실제 경기 단위로 진행한다.
  17주차: 예선 결과 발표 + 조 추첨 + 국가대표 소집
  18~20주차: 조별리그 3경기
  월드컵:  16강(21) → 8강(22) → 4강(23) → 결승(24)
  대륙컵:  8강(21) → 4강(22) → 결승(23)

[예선 정책]
  - 월드컵 예선(wc_qual) : 내 대륙 전체 참가, 6R 홈앤어웨이, 통과국 qual_results 저장
  - 대륙컵 예선(cont_qual): 폐지 → 랜덤 선발로 바로 본선
본선 진출국은 피파 랭킹 줄세우기가 아니라
'등급 기본 점수 + 랜덤 노이즈' 예선 점수로 대륙별 쿼터만큼 선발
→ 강호도 가끔 예선 탈락, 약체도 가끔 깜짝 진출.
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

from constants import (
    WC_START_YEAR, WC_INTERVAL,
    CWC_START_YEAR, CWC_INTERVAL,
    CONTINENTAL_START_YEAR, CONTINENTAL_INTERVAL,
    INTL_CALLUP_WEEK, INTL_GROUP_WEEKS, INTL_KO_WEEKS,
    WC_TEAMS, WC_GROUPS, WC_QUOTA,
    WC_EXPAND_YEAR, WC_TEAMS_BIG, WC_GROUPS_BIG, WC_QUOTA_BIG, WC_BEST_THIRDS_BIG,
    CONT_TEAMS, CONT_GROUPS, CONT_BEST_THIRDS,
    CONFEDERATIONS, CONTINENT_TO_CONF, CONF_CUP_NAME,
    WC_QUAL_32, WC_QUAL_48, EURO_QUAL,
    GRADE_TEAM_OVR, GRADE_QUAL_BASE, QUAL_NOISE,
    INTL_SELECTION_OVR, INTL_MAX_TIER, INTL_MIN_MATCHES,
    INTL_SELECTION_MARGIN, INTL_SQUAD_QUOTA,
    GK_POS, DF_POS, MF_POS, FW_POS,
    get_country_ovr_bonus, get_league_grade,
    get_stage_rule, stage_round_start_day, assign_match_days, week_to_day,
    day_to_week, TOURNAMENT_SCHEDULE_RULES,
    INTL_QUAL_START_DAY, INTL_QUAL_ROUND_GAP_DAYS, INTL_QUAL_WEEK,
)


_NAT_SQUAD_POSITIONS = ["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]

def _tournament_start_years():
    """[2026-08 버그수정, 신민용 리포트: "1986년으로 시작하면 월드컵/
    네이션스컵/클럽월드컵/지역컵이 하나도 안 열린다"] 위에서 import한
    WC_START_YEAR/CWC_START_YEAR/CONTINENTAL_START_YEAR는 intl_engine.py가
    맨 처음 로드되는 시점의 constants.GAME_START_YEAR(하드코딩된 기본값
    2000)로 딱 한 번 계산된 값이라, 새 선수 생성 화면에서 실제로 다른
    시작 연도(예: 1986)를 골라도 전혀 반영이 안 됐다 - 대회 개최년도
    판정이 계속 "2000년 기준"으로만 이뤄지는 버그였다. 이 함수는
    database.get_game_start_year()(실제로 선택된 시작 연도, 없으면
    기존 상수로 폴백)를 기준으로 매번 새로 계산해서 돌려준다."""
    from database import get_game_start_year
    from constants import (get_next_tournament_year,
                            WC_ANCHOR_YEAR, CWC_ANCHOR_YEAR,
                            CONTINENTAL_ANCHOR_YEAR, REGIONAL_CUP_ANCHOR_YEAR,
                            REGIONAL_CUP_INTERVAL)
    gsy = get_game_start_year()
    return {
        "wc": get_next_tournament_year(gsy, WC_ANCHOR_YEAR, WC_INTERVAL),
        "cwc": get_next_tournament_year(gsy, CWC_ANCHOR_YEAR, CWC_INTERVAL),
        "continental": get_next_tournament_year(gsy, CONTINENTAL_ANCHOR_YEAR, CONTINENTAL_INTERVAL),
        "regional": get_next_tournament_year(gsy, REGIONAL_CUP_ANCHOR_YEAR, REGIONAL_CUP_INTERVAL),
    }



def _get_real_squad_ovr(country):
    """[2026-07 신설, 신민용 확정: "국적 배정했으니 스쿼드도 실제 선수로
    뽑아야"] database.get_country_avg_squad_ovr()의 3단계 폴백(국적태그→
    자국리그→해외 하위리그)으로 실제 선수 풀을 최대한 넓게 확보한다.
    그래도 8명(포지션) 미만이면 None을 반환해 호출부가 기존 공식값을 쓰게 한다.

    [2026-07 버그수정, 신민용 리포트: "국대 실제값이 밴드 상한을 훨씬
    넘어선다"] 원래는 get_country_squad_players(포지션당 1등 픽)를 썼는데,
    단일 이상치(707명 중 우연히 EPL 소속인 1명 때문에 평균이 88.6까지
    치솟은 실측 사례)에 취약했다 — get_country_avg_squad_ovr(포지션당
    상위 3명 평균)로 교체해서 안정성을 높였다."""
    from database import get_country_avg_squad_ovr
    return get_country_avg_squad_ovr(country, min_count=8)


_grade_rank_cache = {}   # {grade: [(country_name, fifa_rank), ...] 오름차순} — 등급별 1회만 조회


def get_nat_ovr_band(name, grade):
    """[2026-07 신설, 신민용+GPT 검토] 나라 이름 -> (하한, 중간값, 상한) 밴드.
    NAT_OVR_BAND에 직접 지정된 나라(S/A/B 61개국 + C등급 예외 4개국)는
    그 값을 그대로 쓰고, 없으면 등급별 범위(NAT_OVR_GRADE_BAND_RANGE)
    안에서 fifa_rank 오름차순 순위 비율로 선형보간한다."""
    from constants import NAT_OVR_BAND, NAT_OVR_GRADE_BAND_RANGE
    if name in NAT_OVR_BAND:
        return NAT_OVR_BAND[name]

    lo_r, mid_r, hi_r = NAT_OVR_GRADE_BAND_RANGE.get(grade, ((30, 30), (40, 40), (50, 50)))
    if grade not in _grade_rank_cache:
        conn = get_conn()
        rows = conn.execute(
            "SELECT name, fifa_rank FROM countries WHERE grade=? ORDER BY fifa_rank",
            (grade,)).fetchall()
        conn.close()
        _grade_rank_cache[grade] = [(r["name"], r["fifa_rank"]) for r in rows]
    ranked = _grade_rank_cache[grade]
    total = len(ranked)
    idx = next((i for i, (n, _) in enumerate(ranked) if n == name), total // 2)
    t = idx / max(1, total - 1)   # 0(등급 내 1위) ~ 1(등급 내 꼴찌)

    def _interp(rng):
        lo, hi = rng
        return round(hi - t * (hi - lo), 1)
    return (_interp(lo_r), _interp(mid_r), _interp(hi_r))


def _get_generation_coef(name, year):
    """[2026-07 신설, 신민용+GPT 검토: "밴드만 있으면 매년 독립적으로
    난수를 뽑아서 올해 하한 찍었다가 내년 바로 상한 찍는 롤러코스터가
    나온다"] 나라별 '세대 계수'(0.97~1.03)를 nat_generation 테이블에
    저장해두고, 8~12년 주기로만 새 목표치를 뽑은 뒤 그 목표를 향해
    매년 조금씩(NAT_GENERATION_STEP) 다가간다 — 같은 해에 여러 번
    불려도 같은 값을 반환하고(연도 단위 일관성), 연도가 바뀌면 딱
    그만큼만 진행시킨다."""
    if not name or not year:
        return 1.0
    from constants import NAT_GENERATION_STEP, NAT_GENERATION_RANGE, NAT_GENERATION_CYCLE_YEARS
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM nat_generation WHERE country=?", (name,)).fetchone()
    if row is None:
        # 최초 생성 — 나라마다 랜덤 위상으로 시작해서 모든 나라가 동시에
        # 세대교체하지 않게 한다.
        cycle_len = random.randint(*NAT_GENERATION_CYCLE_YEARS)
        target = round(random.uniform(*NAT_GENERATION_RANGE), 4)
        coef = round(random.uniform(*NAT_GENERATION_RANGE), 4)
        c.execute("""INSERT INTO nat_generation(country, coef, target, cycle_start_year, cycle_len, last_year)
                     VALUES(?,?,?,?,?,?)""", (name, coef, target, year, cycle_len, year))
        conn.commit()
        conn.close()
        return coef

    coef, target = row["coef"], row["target"]
    cycle_start, cycle_len = row["cycle_start_year"], row["cycle_len"]
    last_year = row["last_year"]
    if year <= last_year:
        conn.close()
        return coef   # 같은 해(또는 과거) 재조회 — 이미 저장된 값 그대로

    y = last_year
    while y < year:
        y += 1
        if (y - cycle_start) >= cycle_len:
            target = round(random.uniform(*NAT_GENERATION_RANGE), 4)
            cycle_start = y
            cycle_len = random.randint(*NAT_GENERATION_CYCLE_YEARS)
        coef = round(coef + (target - coef) * NAT_GENERATION_STEP, 4)
    c.execute("""UPDATE nat_generation SET coef=?, target=?, cycle_start_year=?, cycle_len=?, last_year=?
                 WHERE country=?""", (coef, target, cycle_start, cycle_len, y, name))
    conn.commit()
    conn.close()
    return coef


def _nat_team_ovr(grade, name="", continent="", fast=False, year=None):
    """[2026-07 전면 재설계, 신민용+GPT 검토: "OVR가 100에 몰린다/등급이
    역전된다"] 예전 방식(등급 base + 대륙보정 + 국가별 조정치 + 노이즈를
    따로따로 더하고 빼는 방식)은 최종 합산값이 어디 떨어지는지 검증할
    수 없어서, 노이즈를 더하기도 전에 이미 상한(100)을 넘는 나라가
    생기고(포르투갈 47.8%가 그냥 100) 등급 역전(모로코 S인데 A 밑)도
    나왔다. 이제 나라마다 (하한, 중간값, 상한) 밴드를 직접 지정하고
    (get_nat_ovr_band), random.triangular(하, 상, 중)으로 뽑는다 — 값
    자체가 이미 1~100 안에서 확정되므로 클램프에 쏠리는 문제가 구조적
    으로 없다.

    [세대 계수] 밴드 안에서 매년 완전 독립적으로 뽑으면 "올해 하한,
    내년 바로 상한" 롤러코스터가 나오므로, 8~12년 주기로 서서히 움직이는
    나라별 계수(_get_generation_coef)를 곱한다 — "국가 등급은 안 바뀌고
    세대만 바뀐다"는 설계 원칙을 결과값에도 반영한다. year를 안 넘기면
    (연도 무관 호출) 계수 없이 밴드 삼각분포만 쓴다.

    [실제 스쿼드 블렌딩] ai_players.nationality로 실제 스쿼드를 구성할
    수 있으면(포지션별 8명 이상) 그 실제 평균을 70%, 밴드 기반 공식값을
    30%로 블렌딩한다. fast=True면 이 블렌딩을 건너뛰고 공식값만 쓴다
    (월드컵 예선처럼 200여 개국을 한 번에 순회하는 대량 호출 지점 전용
    — 나라마다 실제 스쿼드 조회까지 다 태우면 체감될 만큼 느려진다)."""
    lo, mid, hi = get_nat_ovr_band(name, grade)
    gen_coef = _get_generation_coef(name, year) if (name and year) else 1.0
    formula_val = min(100.0, max(1.0, random.triangular(lo, hi, mid) * gen_coef))
    if fast:
        return formula_val
    real_val = _get_real_squad_ovr(name) if name else None
    if real_val is not None:
        return round(min(100.0, max(1.0, 0.7 * real_val + 0.3 * formula_val)), 2)
    return formula_val

STAGE_KO = {"group": "조별리그", "R32": "32강", "R16": "16강", "QF": "8강", "SF": "4강", "F": "결승", "TP": "3/4위전",
            "qual_group": "조별리그", "qual_po": "플레이오프"}

# ── entry 캐시 ─────────────────────────────────────
# intl_entries(ovr/flag/grade)는 대회 진행 중 불변 → (tid, country)별 1회 조회.
_entry_cache = {}

def _clear_entry_cache():
    _entry_cache.clear()

# 그룹 라벨
_GROUP_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H",
                 "I", "J", "K", "L", "M", "N", "O", "P"]

# 조별리그 라운드 매칭 (4팀, 인덱스)
_GROUP_ROUNDS = [
    [(0, 1), (2, 3)],
    [(0, 2), (1, 3)],
    [(0, 3), (1, 2)],
]


def _round_robin_pairs(n):
    """[2026-08 신설] N개 팀의 라운드로빈 대진(인덱스 쌍)을 3라운드
    구조로 반환 — region 모드는 조 인원이 3명일 수도 있어서(4팀 고정인
    _GROUP_ROUNDS로는 인덱스 3에서 죽는다) 필요해졌다. 4팀은 기존
    _GROUP_ROUNDS와 완전히 동일(호환). 3팀은 매 라운드 한 경기씩(한
    팀은 그 라운드 부전승)."""
    if n == 4:
        return _GROUP_ROUNDS
    if n == 3:
        return [[(0, 1)], [(0, 2)], [(1, 2)]]
    if n <= 1:
        return []
    # 일반화(추후 다른 조 인원수 대비) — 서클 메소드
    teams = list(range(n))
    if n % 2:
        teams.append(None)  # 부전승 자리
    rounds = []
    m = len(teams)
    for _ in range(m - 1):
        pairs = [(teams[i], teams[m - 1 - i]) for i in range(m // 2)
                 if teams[i] is not None and teams[m - 1 - i] is not None]
        rounds.append(pairs)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds

# 예선 조별리그: 4팀 홈앤어웨이 = 6라운드(앞 3R + 홈/원정 뒤집은 3R)
_QUAL_ROUNDS = [
    [(0, 1), (2, 3)],
    [(0, 2), (1, 3)],
    [(0, 3), (1, 2)],
    [(1, 0), (3, 2)],   # 홈/원정 반전
    [(2, 0), (3, 1)],
    [(3, 0), (2, 1)],
]


# ─────────────────────────────────────────────
# 조회 헬퍼
# ─────────────────────────────────────────────

def get_tournament(year):
    """해당 연도의 국제대회 row (없으면 None)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM intl_tournaments WHERE year=? ORDER BY id DESC LIMIT 1",
        (year,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_tournaments(year):
    """[복수대륙컵] 해당 연도의 모든 국제대회 row 리스트 (없으면 빈 리스트).
    미고정 복수국적이면 한 해에 대륙컵이 2~3개 존재할 수 있다."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM intl_tournaments WHERE year=? ORDER BY id ASC",
        (year,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _my_continent_key(p):
    """플레이어 국적이 속한 연맹(4키: 유럽/아메리카/아시아/아프리카).
    committed(확정 국적)가 있으면 그걸, 없으면 1국적 기준."""
    nat = (p.get("intl_committed") or "") or (p.get("nationality") or "")
    if not nat:
        return None
    conn = get_conn()
    row = conn.execute("SELECT continent FROM countries WHERE name=?", (nat,)).fetchone()
    conn.close()
    if not row:
        return None
    return _conf_key(row["continent"])


def get_my_tournament(year=None, qual=None):
    """[복수대륙컵] '내가 실제로 출전 중/표시 대상'인 대회 1개를 선별 반환.

    qual=None  : 본선/예선 구분 없이 (기존 호환)
    qual=False : 본선 대회만 (world/continent)
    qual=True  : 예선 대회만 (wc_qual)

    우선순위:
      1) my_selected==1 (출전 확정) 대회
      2) my_selected==3 (선택 대기) 대회
      3) 그 외 — 표시용 대표 대회
    """
    from game_engine import get_state, get_player
    if year is None:
        st = get_state()
        if not st:
            return None
        year = st["current_year"]
    ts = get_tournaments(year)
    if not ts:
        return None
    # 본선/예선 필터
    # [2026-08 버그수정, 신민용 리포트: "2001년부터 하는 유로 예선은 경기
    # 일정에 안 뜬다"] 유로 예선(kind='cont_qual')이 나중에 추가됐는데,
    # 이 필터가 여전히 'wc_qual'(월드컵 예선)만 걸러서 유로 예선 대회를
    # 놓치고 있었다 — schedule_window._make_intl_tab(qual=True)가 이
    # 함수를 거쳐 "국제대회(예선)" 탭을 만드는데, 유로 예선이 있는
    # 해(2001, 2005, 2009...)엔 이 필터가 빈 리스트를 반환해서 None이
    # 되고 탭 자체가 생성되지 않았다.
    if qual is True:
        ts = [t for t in ts if t.get("kind") in ("wc_qual", "cont_qual")]
    elif qual is False:
        # [2026-08 버그수정, 신민용 리포트: "경기 일정 창에 국제대회 탭
        # 자체가 안 뜬다"] 지역컵(kind='region')이 이 화이트리스트에
        # 없어서, schedule_window._make_intl_tab(qual=False)가 이
        # 함수를 거쳐 갈 때마다 매 지역컵 시즌(2001,05,09..)에 활성
        # 대회가 있어도 get_my_tournament가 None을 반환 — "🌍 국제대회"
        # 탭 자체가 생성되지 않았다.
        ts = [t for t in ts if t.get("kind") in ("world", "continent", "region")]
    if not ts:
        return None
    # [2026-07 버그 수정, 신민용 리포트: "월드컵 일정이 안 떠"] 예선과 본선이
    # 이제 같은 해에 공존한다(예선은 이미 끝나 status='done', 본선은 진행
    # 중) — my_selected==1인 대회가 이 둘 다일 수 있는데, 예선이 id가 더
    # 작아(먼저 생성됨) 항상 먼저 반환되면서 get_my_match의
    # status=='done' 체크에 걸려 본선 일정이 통째로 안 보였다. 끝나지
    # 않은(진행 중인) 대회를 먼저 찾고, 그게 없을 때만(둘 다 done이거나
    # 예선만 있는 경우) done인 대회라도 표시용으로 반환한다.
    for t in ts:
        if t.get("my_selected") == 1 and t.get("status") != "done":
            return t
    for t in ts:
        if t.get("my_selected") == 1:
            return t
    for t in ts:
        if t.get("my_selected") == 3:
            return t
    # [버그수정] my_selected=0(미선발)/2(탈락·미참가)인 대회는 표시용으로만 반환.
    # 기존: kind='world'/'wc_qual'이면 my_selected 무관하게 반환
    # → 예선 미선발(my_selected=0)인데도 월드컵 탭이 보이고 선택창이 다시 뜨는 버그.
    # 수정: my_selected=2(탈락/미참가) 대회만 표시용으로 반환.
    #       my_selected=0(미선발)은 반환하지 않음 — 출전 자격 자체가 없음.
    for t in ts:
        if t.get("my_selected") == 2 and t.get("kind") in ("world", "wc_qual", "cont_qual"):
            return t
    # [2026-07 버그수정, 신민용 리포트: "국대로 못 뽑히면 일정에 유럽권이
    # 무조건 뜬다"] 대륙컵들이 유럽→아메리카→아시아→아프리카 순서로
    # 생성되다 보니 유럽 대회가 항상 가장 작은 id를 가져서, 아래 표시용
    # 폴백이 그냥 리스트의 첫 번째(=유럽)를 집어왔다. 내 국적이 속한
    # 대륙 대회가 있으면 그걸 우선 보여준다.
    _my_cont = _my_continent_key(get_player() or {})
    if _my_cont:
        for t in ts:
            if t.get("my_selected") == 2 and _conf_key(t.get("continent") or "") == _my_cont:
                return t
    for t in ts:
        if t.get("my_selected") == 2:
            return t
    return None


def get_pending_choice():
    """[복수국적·복수대륙컵] 대표팀 선택/동의가 필요한 대회들을 하나로 묶어 반환.

    그 해 my_selected==3(선택 대기)인 모든 대회의 후보 국적을 평탄화해
    최대 3개의 선택지로 제시한다. 각 선택지는 (국적, 대회명, tournament_id)를
    가지므로, 예를 들어 '크로아티아 → 유럽 챔피언십', '대한민국 → 아시안컵'이
    같은 발탁창에 함께 뜬다. 전부 거절도 가능하다.

    [선택 우선 원칙] 후보는 cand_nats(선발 통과국)에서 가져온다. 선택해서
    출전(choose_national_team)하면 그제서야 예선 통과/탈락이 드러나고,
    본선에 출전하면 그 나라로 영구 고정(cap-tie)된다."""
    from game_engine import get_state, get_player
    st = get_state(); p = get_player()
    if not st or not p:
        return None
    ts = [t for t in get_tournaments(st["current_year"])
          if t.get("my_selected") == 3]
    if not ts:
        return None

    conn = get_conn()
    opts = []
    seen = set()   # (nat, tournament_id) 중복 방지
    flag_cache = {}
    for t in ts:
        cand_raw = (t.get("cand_nats", "") or "")
        cand = [n for n in cand_raw.split(",") if n]
        for n in cand:
            key = (n, t["id"])
            if not n or key in seen:
                continue
            seen.add(key)
            if n not in flag_cache:
                fr = conn.execute("SELECT flag FROM countries WHERE name=?", (n,)).fetchone()
                flag_cache[n] = fr["flag"] if fr else ""
            opts.append({"nat": n, "flag": flag_cache[n],
                         "tournament_id": t["id"], "competition": t["name"]})
    conn.close()
    if len(opts) < 1:
        return None
    opts = opts[:3]   # 최대 3개 선택지
    # 대표 tournament_id(구버전 UI 호환): 첫 선택지의 대회.
    # 대회명은 여러 개일 수 있으므로 '/'로 묶어 표기.
    comp_names = []
    for o in opts:
        if o["competition"] not in comp_names:
            comp_names.append(o["competition"])
    return {"tournament_id": opts[0]["tournament_id"],
            "name": " / ".join(comp_names),
            "year": st["current_year"], "options": opts,
            "multi": True}


def choose_national_team(tournament_id, nat):
    """[복수국적] 대표팀 선택 확정 → 그 나라로 고정하고 대회 출전국 설정.
    선발 판정을 다시 수행해 my_selected를 1(선발)/0(미선발)로 갱신."""
    from game_engine import get_player, update_player
    p = get_player()
    if not p:
        return None
    conn = get_conn()
    grow = conn.execute("SELECT grade, continent FROM countries WHERE name=?", (nat,)).fetchone()
    grade = grow["grade"] if grow else "F"
    nat_continent = grow["continent"] if grow else ""
    conn.close()

    # [버그수정] 선택 시점에는 절대 고정하지 않는다.
    #   cap-tie(국적 영구 고정)는 FIFA 규정대로 '본선 A매치 실제 출전' 시점에만
    #   일어나야 한다. 본선 출전 처리는 simulate_my_match()가 담당하며,
    #   여기서는 미선발/예선탈락이어도 고정되지 않아 다음 대회에 다른 나라를
    #   다시 선택할 수 있다. (기존엔 선택 즉시 update_player(intl_committed=nat)을
    #   호출해, 본선에 못 가도 영구 고정돼버리는 버그가 있었다.)
    from game_engine import get_state
    _st = get_state() or {}

    # ── [선택 우선] 결과 공개 순서: ① 컷오프 → ② 선발 여부 → ③ 예선 통과 여부 ──
    from game_engine import add_log
    p = get_player()

    # ① 컷오프 체크: 선택한 나라가 예선 풀 자체에 없으면 예선 진출 실패
    _cut_check = False
    try:
        from constants import WC_QUAL_32, WC_QUAL_48, WC_EXPAND_YEAR, CONFEDERATIONS
        _qc2 = get_conn()
        _trow2 = _qc2.execute("SELECT year, kind, continent FROM intl_tournaments WHERE id=?",
                               (tournament_id,)).fetchone()
        _qc2.close()
        if _trow2 and _trow2["kind"] in ("wc_qual", "cont_qual"):
            _tyear2 = _trow2["year"]
            _tconf2 = (_trow2["continent"] or "").strip()
            _big2 = (_tyear2 + 1) >= WC_EXPAND_YEAR
            if _trow2["kind"] == "cont_qual":
                from constants import EURO_QUAL
                _qcfg2 = EURO_QUAL.get(_tconf2, {})
            else:
                _qcfg2 = (WC_QUAL_48 if _big2 else WC_QUAL_32).get(_tconf2, {})
            _all_rows2 = sorted(_enrich_countries(_conf_countries(_tconf2), year=_tyear2),
                                key=lambda r: r["ovr"], reverse=True)
            _cutoff2 = _qcfg2.get("cutoff_bottom", 0)
            _cut_names2 = {r["name"] for r in _all_rows2[len(_all_rows2)-_cutoff2:]}
            _cut_check = nat in _cut_names2
    except Exception:
        pass

    if _cut_check:
        # 컷오프 → 예선 진출 실패
        conn2 = get_conn()
        _tr2 = conn2.execute("SELECT year, name FROM intl_tournaments WHERE id=?",
                              (tournament_id,)).fetchone()
        _ty2 = _tr2["year"] if _tr2 else _st.get("current_year")
        _tn2 = _tr2["name"] if _tr2 else ""
        conn2.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=2 WHERE id=?",
                      (nat, tournament_id))
        conn2.commit(); conn2.close()
        try:
            from game_engine import update_player as _upd_cut
            _upd_cut(qual_pledged_nat="")  # 컷오프: pledge 초기화
        except Exception:
            pass
        _save_trophy(_ty2, nat, _tn2, "예선 진출 실패")
        add_log(f"❌ {nat} {_tn2} 진출 실패 (랭킹 하위권)", "event")
        return {"nat": nat, "selected": False, "qualified": False,
                "result": "예선진출실패", "kind": _trow2["kind"]}

    selected = _check_selection(p, grade, country=nat, continent=nat_continent)

    conn = get_conn()
    trow = conn.execute("SELECT year, name, kind FROM intl_tournaments WHERE id=?",
                        (tournament_id,)).fetchone()
    tyear = trow["year"] if trow else _st.get("current_year")
    tname = trow["name"] if trow else ""
    tkind = trow["kind"] if trow else ""
    # 선택한 나라가 이번 대회 본선에 진출했는가(예선 통과 여부 — 이제야 공개)
    qrow = conn.execute(
        "SELECT 1 FROM intl_entries WHERE tournament_id=? AND country=? LIMIT 1",
        (tournament_id, nat)).fetchone()
    qualified = bool(qrow)

    if not selected:
        # ① 선발 미달 — 예선 결과와 무관하게 이번 대회 출전 없음
        my_sel = 0
        conn.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=? WHERE id=?",
                     (nat, my_sel, tournament_id))
        conn.commit(); conn.close()
        try:
            from game_engine import update_player as _upd_ms
            _upd_ms(qual_pledged_nat="")  # 미선발: pledge 초기화
        except Exception:
            pass
        _save_trophy(tyear, nat, tname, "국가대표 미선발")
        return {"nat": nat, "selected": False, "qualified": qualified, "result": "미선발", "kind": tkind}

    if not qualified:
        # ② 선발은 됐지만 그 나라가 예선 탈락 → 본선 출전 불가
        my_sel = 2
        conn.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=? WHERE id=?",
                     (nat, my_sel, tournament_id))
        conn.commit(); conn.close()
        try:
            from game_engine import update_player as _upd_et
            _upd_et(qual_pledged_nat="")  # 예선탈락: pledge 초기화
        except Exception:
            pass
        _save_trophy(tyear, nat, tname, "예선 탈락")
        return {"nat": nat, "selected": True, "qualified": False, "result": "예선탈락", "kind": tkind}

    # ③ 선발 + 본선 진출 → 정식 출전. 내 경기로 일정 재태깅.
    #   [고정 시점] 실제 영구 고정은 본선 첫 경기 출전 시 simulate_my_match()가
    #   처리한다. 여기서는 본선 확정 사실만 연혁에 commit으로 남긴다.
    my_sel = 1
    conn.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=? WHERE id=?",
                 (nat, my_sel, tournament_id))
    # 이 대회 경기들 중 선택국이 낀 경기를 내 경기로 표시(선택 전에는 후보 전체였음)
    conn.execute("UPDATE intl_matches SET is_my=0 WHERE tournament_id=?", (tournament_id,))
    conn.execute("UPDATE intl_matches SET is_my=1 WHERE tournament_id=? AND (home=? OR away=?)",
                 (tournament_id, nat, nat))
    # [복수대륙컵] 본선 출전을 확정했으므로, 같은 해 다른 '선택 대기(3)' 대회는
    #   이번엔 출전하지 않는 것으로 마감(my_selected=2). 한 해에 본선 출전(1)은
    #   동시에 1개만 존재하도록 보장한다. (명시적 거절 기록은 남기지 않음 —
    #   다른 나라를 골랐을 뿐이며, 이 나라로 cap-tie되면 다음 해부터 자동 정리됨.)
    if tyear is not None:
        # [2026-07 버그수정, 신민용 리포트: "호주/앙골라 복수국적인데 호주를
        # 선택했는데 커리어에 앙골라 대륙컵 경기도 같이 기록됨"] 대회
        # 생성 시점엔 아직 선택 전이라 후보국(cand_nats) 경기 전부에
        # is_my=1이 미리 찍혀 있다(발탁창 뜨기 전엔 어느 나라를 고를지
        # 모르므로). 그런데 여기서 다른 대회를 my_selected=2로 닫을 때
        # my_selected만 바꾸고 intl_matches.is_my는 그대로 1로 남겨뒀다 —
        # 그래서 그 대회가 매주 AI 시뮬레이션될 때(_sim_ai_match)
        # "m['is_my']==1"만 보고 내 경기로 착각해 커리어 로그에 그대로
        # 찍혔다(고르지도 않은 나라의 결과가 "부상으로 결장"과 함께 남는
        # 사고). 이제 다른 대회를 닫을 때 그 대회의 intl_matches.is_my도
        # 함께 0으로 초기화해 완전히 "내 경기 아님" 처리한다.
        _closed = [r["id"] for r in conn.execute(
            "SELECT id FROM intl_tournaments WHERE year=? AND id<>? AND my_selected=3",
            (tyear, tournament_id)).fetchall()]
        conn.execute(
            "UPDATE intl_tournaments SET my_selected=2 "
            "WHERE year=? AND id<>? AND my_selected=3",
            (tyear, tournament_id))
        if _closed:
            _ph = ",".join("?" * len(_closed))
            conn.execute(f"UPDATE intl_matches SET is_my=0 WHERE tournament_id IN ({_ph})",
                         _closed)
    conn.commit(); conn.close()
    # [국적 연혁] 본선 출전 확정 → 대표 국적 commit 기록 (중복은 add_nat_history가 무시)
    #   단, 예선(wc_qual)은 영구고정이 아니므로 commit 기록하지 않는다.
    #   (예선은 cap-tie 안 됨 → 다음 예선 때 다른 나라 선택 가능)
    _is_qual = False
    try:
        _cc = get_conn()
        _kr = _cc.execute("SELECT kind FROM intl_tournaments WHERE id=?",
                          (tournament_id,)).fetchone()
        _cc.close()
        if _kr and _kr["kind"] in ("wc_qual", "cont_qual"):
            _is_qual = True
    except Exception:
        pass
    if _is_qual:
        # 예선 선택: cap-tie는 안 하되, 이 사이클(예선→본선) 동안 그 나라로 출전하도록 pledge
        try:
            from game_engine import update_player as _upd2
            _upd2(qual_pledged_nat=nat)
        except Exception:
            pass
    else:
        try:
            from game_engine import add_nat_history
            _fl = ""
            for _nk, _fk in (("nationality","flag"),("nationality2","flag2"),("nationality3","flag3")):
                if (p.get(_nk,"") or "") == nat:
                    _fl = p.get(_fk,"") or ""; break
            add_nat_history("commit", nat, _fl,
                            _st.get("current_year"), _st.get("current_week"))
        except Exception:
            pass
    return {"nat": nat, "selected": True, "qualified": True, "result": "선발", "kind": tkind}


def decline_national_team(tournament_id):
    """[복수국적·복수대륙컵] 이번 대표팀 발탁을 거절(보류). 영구 고정하지 않는다.

    같은 해에 선택 대기(my_selected==3)인 대회가 여러 개면(여러 대륙컵) 전부
    거절 처리한다. 즉 발탁창의 '전부 거절'에 해당한다. 다음 대회에서 다시 제안된다.

    [거절 기록] 거절도 커리어에 남긴다 (은퇴창/AI요약 표시용).
      - year       : 거절한 연도
      - team_name  : 거절한 후보 국가 전부 (대회 통합, 예: '크로아티아/대한민국')
      - league_name: '발탁 거절'
      - competition: 대회명(들)
    과거 거절은 나중에 같은 나라 대표로 뛰어도 그대로 남는다(역사 보존)."""
    conn = get_conn()
    trow = conn.execute(
        "SELECT year FROM intl_tournaments WHERE id=?", (tournament_id,)).fetchone()
    year = trow["year"] if trow else None
    # 그 해 선택 대기 대회 전부 수집 (후보 국가/대회명 통합 기록용)
    cand_all = []
    comp_all = []
    if year is not None:
        rows = conn.execute(
            "SELECT name, cand_nats FROM intl_tournaments WHERE year=? AND my_selected=3",
            (year,)).fetchall()
        for r in rows:
            if r["name"] and r["name"] not in comp_all:
                comp_all.append(r["name"])
            for n in (r["cand_nats"] or "").split(","):
                if n and n not in cand_all:
                    cand_all.append(n)
        conn.execute(
            "UPDATE intl_tournaments SET my_nat='', my_selected=2 "
            "WHERE year=? AND my_selected=3", (year,))
    else:
        conn.execute("UPDATE intl_tournaments SET my_nat='', my_selected=2 WHERE id=?",
                     (tournament_id,))
    conn.commit(); conn.close()

    # [버그수정] 거절 시 qual_pledged_nat 초기화
    # 예선 발탁 거절했는데 qual_pledged_nat가 남아있으면
    # 다음 해 월드컵 본선에서 pledged 로직으로 자동 참가하는 버그가 있었음
    try:
        from game_engine import update_player as _upd_p
        _upd_p(qual_pledged_nat="")
    except Exception:
        pass

    if year is not None:
        nat_str = "/".join(cand_all) if cand_all else "대표팀"
        comp_str = " / ".join(comp_all) if comp_all else "대륙컵"
        _save_decline(year, nat_str, comp_str)
    return True


def decline_national_team_option(tournament_id, nat):
    """[2026-08 신설, 신민용 요청: "국적이 2개면 동시에 창이 뜨고, 하나만
    거절하면 그 나라만 닫히고 나머지는 그대로 떠있어야 한다"]
    decline_national_team()은 그 해 선택 대기(my_selected==3) 대회를 전부
    한꺼번에 거절한다 — "잉글랜드는 거절하되 코트디부아르는 계속 고민한다"가
    안 됐다. 이 함수는 딱 그 (tournament_id, nat) 하나만 후보에서 뺀다.

    그 대회에 남은 후보 국적이 있으면(복수 대륙컵이 같은 대회에 묶인
    경우는 없지만, cand_nats 하나에 여러 나라가 들어있는 경우 — 예:
    월드컵 본선처럼 서로 다른 대륙 예선을 통과한 복수국적자) my_selected는
    3(선택 대기)으로 그대로 두고 cand_nats에서 그 나라만 제거한다.
    후보가 그 나라 하나뿐이었으면 그 대회 자체를 my_selected=2로 닫는다
    (한 번 거절해도 다음 대회에서 다시 제안되는 기존 정책과 동일).

    choose_national_team()이 이미 '하나를 수락하면 같은 해 다른 대기
    대회를 전부 자동으로 닫는' 로직을 갖고 있으므로("네를 선택하면
    나머지는 사라지게"는 별도 구현 불필요 — 기존 인프라 그대로 재사용),
    이 함수는 "거절" 쪽 대칭만 채워 넣으면 된다."""
    conn = get_conn()
    row = conn.execute(
        "SELECT year, name, cand_nats FROM intl_tournaments WHERE id=?",
        (tournament_id,)).fetchone()
    if not row:
        conn.close()
        return False
    cand = [n for n in (row["cand_nats"] or "").split(",") if n and n != nat]
    if cand:
        # 남은 후보가 있으면 그 나라들만으로 계속 선택 대기 상태 유지
        conn.execute("UPDATE intl_tournaments SET cand_nats=? WHERE id=?",
                     (",".join(cand), tournament_id))
    else:
        # 이 대회의 후보가 그 나라 하나뿐이었음 → 대회 자체를 닫는다
        conn.execute("UPDATE intl_tournaments SET my_nat='', my_selected=2, "
                     "cand_nats=? WHERE id=?", ("", tournament_id))
    conn.commit()
    year = row["year"]
    conn.close()

    # 거절 기록은 그 나라 하나만 남긴다(decline_national_team의 '전부 통합'
    # 기록과 구분 — 나중에 커리어에서 "잉글랜드 발탁 거절"처럼 개별로 보임).
    if year is not None:
        _save_decline(year, nat, row["name"] or "대표팀")
    return True


def _save_decline(year, nat_str, competition):
    """[거절 기록] 발탁 거절을 trophy_log(tier=0)에 남긴다.
    같은 (year, competition)에 거절 기록이 이미 있으면 중복 방지.
    선발/예선 결과 줄과 별개로 '발탁 거절' 줄을 따로 남길 수 있도록
    league_name='발탁 거절' 조건까지 함께 본다."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=? AND league_name=?",
        (year, competition, "발탁 거절")).fetchone()
    if not existing:
        conn.execute(
            """INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
               VALUES(?,?,?,0,?)""", (year, nat_str, "발탁 거절", competition))
        conn.commit()
    conn.close()


def get_forced_commit():
    """[복수국적] 22세 1~4주차(비시즌)에 아직 대표팀을 고정하지 않았다면,
    강제로 국적을 정하게 하는 정보 반환. 없으면 None.

    - 조건: 미고정(intl_committed=='') + 나이 == 22 + 현재 주차 1~4
    - 본선 진출 여부와 무관하게 '보유 국적 전부'를 선택지로 제공한다.
      (대회에 안 나가도 평생 뛸 대표팀을 이 시점에 확정)
    - 이 선택으로 intl_committed만 설정되며, 보유 국적(nationality/2/3)은
      그대로 유지된다(국적이 사라지지 않음).
    """
    from game_engine import get_state, get_player
    st = get_state(); p = get_player()
    if not st or not p:
        return None
    if (p.get("intl_committed", "") or ""):
        return None   # 이미 고정됨
    week = st.get("current_week", 0)
    from constants import SEASON_PHASES
    _ps_s, _ps_e = SEASON_PHASES["preseason1"]
    if not (_ps_s <= week <= _ps_e):
        return None
    year = st.get("current_year", 0)
    # [버그수정] 나이는 'age' 컬럼이 정확하다. birth_year는 게임 내내 갱신되지 않아
    #   (year - birth_year)는 실제 나이보다 16 적게 나온다 → 22세 판정이 영원히 실패했었다.
    age = p.get("age", 0) or 0
    if age != 22:
        return None

    # 보유 국적 전부 (본선 진출 무관)
    pairs = [
        (p.get("nationality", "")  or "", p.get("flag", "")  or ""),
        (p.get("nationality2", "") or "", p.get("flag2", "") or ""),
        (p.get("nationality3", "") or "", p.get("flag3", "") or ""),
    ]
    opts = []
    seen = set()
    for nat, flag in pairs:
        if nat and nat not in seen:
            seen.add(nat)
            opts.append({"nat": nat, "flag": flag})
    if not opts:
        return None
    return {"forced": True, "year": year, "options": opts}


def commit_nationality(nat):
    """[복수국적] 22세 강제 선택 확정 → intl_committed만 그 나라로 설정.
    보유 국적(nationality/2/3)은 건드리지 않아 사라지지 않는다."""
    from game_engine import get_player, update_player
    p = get_player()
    if not p:
        return None
    update_player(intl_committed=nat)
    # [국적 연혁] 22세 강제확정 사건 기록
    try:
        from game_engine import add_nat_history, get_state
        _st = get_state() or {}
        _fl = ""
        for _nk, _fk in (("nationality","flag"),("nationality2","flag2"),("nationality3","flag3")):
            if (p.get(_nk,"") or "") == nat:
                _fl = p.get(_fk,"") or ""; break
        add_nat_history("commit", nat, _fl,
                        _st.get("current_year"), _st.get("current_week"))
    except Exception:
        pass
    return {"nat": nat}


def fmt_nationalities(p):
    """[복수국적] 보유 국적 전부를 '국기+이름' 문자열로 (예: '🇦🇹오스트리아 / 🇵🇦파나마')."""
    if not p:
        return ""
    pairs = [
        (p.get("nationality", "")  or "", p.get("flag", "")  or ""),
        (p.get("nationality2", "") or "", p.get("flag2", "") or ""),
        (p.get("nationality3", "") or "", p.get("flag3", "") or ""),
    ]
    seen = set(); out = []
    for nat, flag in pairs:
        if nat and nat not in seen:
            seen.add(nat)
            out.append(f"{flag}{nat}")
    return " / ".join(out)


def fmt_rep_nationality(p):
    """[복수국적] 축구 대표로 뛰는 국적(국기 포함). 미고정이면 '미정'."""
    if not p:
        return "미정"
    rep = p.get("intl_committed", "") or ""
    if not rep:
        return "미정"
    # 국기 찾기
    for nat_key, flag_key in (("nationality", "flag"),
                              ("nationality2", "flag2"),
                              ("nationality3", "flag3")):
        if (p.get(nat_key, "") or "") == rep:
            return f"{p.get(flag_key, '') or ''}{rep}"
    return rep


def _my_nat(t, p):
    """[복수국적] 이 대회에서 내가 뛰는 나라.
    대회에 저장된 my_nat 우선, 없으면(구 세이브) 주 국적으로 폴백."""
    if t:
        mn = t.get("my_nat") if isinstance(t, dict) else t["my_nat"]
        if mn:
            return mn
    return (p.get("nationality", "") if p else "") or ""


def _active_tournament():
    from game_engine import get_state
    st = get_state()
    if not st:
        return None
    t = get_my_tournament(st["current_year"])
    if t and t["status"] != "done":
        return t
    return t  # done이어도 반환 (UI 표시용) ─ 호출부에서 status 체크


def get_my_match(week, day=None, p=None, st=None):
    """이번 주차(또는 특정 day)에 내가 뛸 국가대표 경기가 있으면 dict, 없으면 None.

    [2026-07 수정] 한 주에 예선 라운드가 2개 이상 들어갈 수 있게 되면서
    (중간 휴식기 4일 간격 배정), week만으로 조회하면 그 주의 여러 경기 중
    아무거나 하나만 (그것도 SQL이 우연히 먼저 반환하는 순서로) 잡히는
    문제가 있었다. day를 넘기면 day가 있는 행은 정확히 그 날짜의 경기만
    골라내고, day가 아직 없는 행(옛 세이브 등)은 그대로 week만으로 찾는다.

    [2026-07 최적화, 신민용 리포트: "일 단위 전환 후 전체적으로 렉"]
    화면 미리보기(center_panel._get_match_for_day)는 하루 셀 하나마다
    이 함수를 여러 번 호출하는데, 그때마다 get_player()가 다시 DB
    왕복을 해서(한 화면 새로고침에 최대 100회 가까이) 누적 지연의
    절반 이상을 차지했다. 호출부가 이미 조회해둔 p를 넘기면 재조회를
    생략한다 — 안 넘기면 예전처럼 직접 조회(하위호환)."""
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if st is None:
        st = get_state()
    if not p or not st:
        return None
    t = get_my_tournament(st["current_year"])
    if not t or t["status"] == "done" or t["my_selected"] != 1:
        return None
    nat = _my_nat(t, p)
    conn = get_conn()
    if day is not None:
        m = conn.execute(
            """SELECT * FROM intl_matches
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home=? OR away=?) AND (day=? OR day IS NULL)""",
            (t["id"], week, nat, nat, day)).fetchone()
    else:
        m = conn.execute(
            """SELECT * FROM intl_matches
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home=? OR away=?)""",
            (t["id"], week, nat, nat)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home"] == nat)
    opp = m["away"] if is_home else m["home"]
    fr = conn.execute("SELECT flag FROM intl_entries WHERE tournament_id=? AND country=?",
                      (t["id"], opp)).fetchone()
    conn.close()
    return {
        "intl": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],
        "kind": t.get("kind", ""),
        "stage": m["stage"],
        "stage_ko": STAGE_KO.get(m["stage"], m["stage"]),
        "grp": m["grp"],
        "opp": opp,
        "opp_flag": fr["flag"] if fr else "",
        "is_home": is_home,
        "week": week,
    }


def get_my_pending_stage(week, day=None, p=None, st=None):
    """[2026-07 신설, 신민용 요청: "8강 날짜가 되면 이기기 전까지는 미정
    이렇게 메인 화면에 떠야 한다"] get_my_match는 내 국가가 실제로 배정된
    (home/away에 내 국가명이 들어간) 행만 찾는다 — 그런데
    _precreate_ko_shell로 미리 만든 미래 라운드는 그 라운드가 실제로
    올 때까지 home/away가 빈 문자열이라(아직 대진 미확정) get_my_match가
    절대 못 찾는다. 그래서 오늘이 어떤 라운드의 예정일인데 아직 대진이
    안 정해졌으면, 이 함수가 그 사실을 찾아서 "미정" 표시용 정보를
    돌려준다. 내 국가가 이미 탈락(alive=0)했으면 None(더 이상 내 대회가
    아니므로 표시 안 함).

    [2026-07 최적화] get_my_match와 동일하게 p를 넘기면 get_player()
    재조회를 생략한다."""
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if st is None:
        st = get_state()
    if not p or not st or day is None:
        return None
    t = get_my_tournament(st["current_year"])
    if not t or t["status"] not in ("group", "ko") or t["my_selected"] != 1:
        return None
    nat = _my_nat(t, p)
    if not nat:
        return None
    conn = get_conn()
    alive_row = conn.execute(
        "SELECT alive FROM intl_entries WHERE tournament_id=? AND country=?",
        (t["id"], nat)).fetchone()
    if alive_row and alive_row["alive"] == 0:
        conn.close()
        return None
    m = conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND day=?
           AND stage!='group' AND (home='' OR away='') LIMIT 1""",
        (t["id"], day)).fetchone()
    if m and m["stage"] in ("F", "TP"):
        # [2026-07 신설, 신민용 리포트: "결승 진출이 확정됐으면 3/4위전은
        # 안 보여도 될 것 같다"] 결승(F)과 3/4위전(TP)은 같은 4강에서
        # 갈라지는 '서로 배타적인' 두 갈래다(4강 승자→결승, 패자→3/4위전)
        # — 내 국가가 이미 한쪽에 확정 배치됐으면 반대쪽엔 절대 갈 수
        # 없으므로, 그 반대쪽 placeholder는 더 이상 "혹시 나일 수도"가
        # 아니라 무조건 남의 경기다. 표시할 이유가 없다.
        other_stage = "TP" if m["stage"] == "F" else "F"
        other_row = conn.execute(
            "SELECT home, away FROM intl_matches WHERE tournament_id=? AND stage=?",
            (t["id"], other_stage)).fetchone()
        if other_row and nat in (other_row["home"], other_row["away"]):
            conn.close()
            return None
    conn.close()
    if not m:
        return None
    return {
        "intl": True,
        "pending": True,   # 대진 미확정 placeholder임을 표시
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],
        "kind": t.get("kind", ""),
        "stage": m["stage"],
        "stage_ko": STAGE_KO.get(m["stage"], m["stage"]),
        "week": week,
    }


def has_my_match_between(week_from, week_to):
    """주차 범위 내 내 국가대표 경기 존재 여부 (센터패널 표시용)."""
    for w in range(week_from, week_to + 1):
        if get_my_match(w):
            return True
    return False


# ─────────────────────────────────────────────
# 대회 생성 (17주차 진입 시)
# ─────────────────────────────────────────────

def _gather_nat_context(p):
    """국적/고정여부/대륙정보 조회 — start_intl_tournament와
    start_qualifying_if_needed가 공유하는 공통 설정."""
    nat1 = p.get("nationality", "") or ""
    nat2 = p.get("nationality2", "") or ""
    nat3 = p.get("nationality3", "") or ""
    committed = p.get("intl_committed", "") or ""
    if committed:
        my_nats = [committed]
    else:
        my_nats = [n for n in (nat1, nat2, nat3) if n]

    conn = get_conn()
    nat_info = {}
    if my_nats:
        _ph = ",".join("?" * len(my_nats))
        for r in conn.execute(
                f"SELECT name, continent, grade FROM countries WHERE name IN ({_ph})",
                my_nats).fetchall():
            nat_info[r["name"]] = {"continent": r["continent"], "grade": r["grade"]}
    conn.close()
    return my_nats, nat_info, committed


def start_qualifying_if_needed(year):
    """[2026-07 신설] 중간 휴식기 시작 주(INTL_QUAL_WEEK) 진입 시 호출.
    올해가 월드컵 해면 지금(비시즌 중간 휴식기, 28~31주) 예선을 생성한다.

    [2026-07 재설계] 예전엔 예선이 본선 '전년도'(예: 2001년 예선 →
    2002 월드컵)에 연말 오프시즌에서 진행됐다. 이제는 예선과 본선이
    '같은 해'(2002년) 안에서, 예선은 중간 휴식기에 본선은 연말
    오프시즌에 나눠 진행된다 — 그래서 예선 생성 시점의 year가 곧
    본선 연도와 같다(더는 year+1 오프셋 없음)."""
    from game_engine import get_player
    p = get_player()
    if not p:
        return

    # [2026-08 버그수정] 정적 상수(WC_START_YEAR 등) 대신, 실제로 선택된
    # 시작 연도 기준으로 매번 다시 계산한다 (_tournament_start_years 참고).
    _ty = _tournament_start_years()
    is_wc = year >= _ty["wc"] and (year - _ty["wc"]) % WC_INTERVAL == 0
    is_cont = (not is_wc and year >= _ty["continental"]
               and (year - _ty["continental"]) % CONTINENTAL_INTERVAL == 0)
    # [2026-08 신설, 신민용 확정: "유로(EURO)는 기존 대륙컵('유럽
    # 네이션스컵', 2004년 주기)과는 완전히 별개로 지역컵과 같은 해
    # (2001,05,09..)에 새로 연다"] 지역컵 주기와 동일한 년도 판정을
    # 그대로 재사용 — REGIONAL_CUP_START_YEAR/INTERVAL이 정확히 2001/4.
    from constants import REGIONAL_CUP_INTERVAL
    is_euro_cycle = (not is_wc and not is_cont and year >= _ty["regional"]
                     and (year - _ty["regional"]) % REGIONAL_CUP_INTERVAL == 0)
    if not is_wc and not is_cont and not is_euro_cycle:
        return

    if is_wc:
        if [t for t in get_tournaments(year) if t["kind"] == "wc_qual"]:
            return  # 이미 이 해 예선이 생성됨 → 중복 방지

        # [2026-08 신설, 옐로카드 시스템] 새 월드컵 예선 사이클이 시작되는
        # 시점에 wc_qual 그룹의 결장카운터/시즌누적경고를 리셋한다 — 국가
        # 대표는 클럽처럼 매년 리셋할 수 없어서(소집 자체가 가끔이라) "그
        # 대회 사이클이 새로 열릴 때"를 리셋 시점으로 삼는다. 지난 예선
        # 사이클에서 미처 소진되지 않고 남아있던 결장/누적이 다음 예선까지
        # 이어지는 걸 막는다(월드컵 본선/기타 국제대회 그룹인 intl_suspension/
        # season_yellow_intl은 여기서 건드리지 않음 — 별도 그룹이라 그대로 유지).
        from game_engine import update_player as _reset_wcq
        _reset_wcq(wc_qual_suspension=0, season_yellow_wc_qual=0,
                   yellow_susp_progress_wc_qual=0)

        _clear_entry_cache()
        my_nats, nat_info, committed = _gather_nat_context(p)

        # [2026-07 신설, 신민용 리포트: "2002년 월드컵이 통째로 사라졌다"]
        # 대륙별로 예외를 격리해서, 한 대륙이 실패해도 나머지 대륙은 정상적으로
        # 예선이 생성되게 한다(에러는 로그에 남기되 다른 대륙까지 함께
        # 망가지는 것만 막는다).
        all_confs = ["유럽", "아메리카", "아시아", "아프리카"]
        for conf in all_confs:
            try:
                _create_qual_tournament(year, "wc_qual", conf,
                                        p=p, my_nats=my_nats, nat_info=nat_info,
                                        committed=committed)
            except Exception as e:
                from game_engine import add_log
                add_log(f"⚠ {year}년 월드컵 {conf} 예선 생성 오류: {e}"
                        f"  (다른 대륙 예선은 정상 진행)", "event")
        return

    # [2026-08 신설, 신민용 확정: "유로(EURO)는 다른 지역컵과 달리 여름
    # 예선 → 겨울 본선"] 대륙컵 해(2004,08..)의 '유럽 네이션스컵'과 별개로
    # 지역컵 해(2001,05..)의 '유로(EURO)'도 여기서 같은 방식으로 예선을
    # 돈다 — 이 함수는 year만 받아서 그 해 기준으로 동작하므로(연도
    # 자체에 하드코딩된 분기가 없음) is_cont든 is_euro_cycle이든 같은
    # 코드로 자연히 처리된다. 다른 3개 대륙(아시아/아메리카/아프리카)은
    # 여전히 기존 랜덤 노이즈 직행 선발 그대로 유지 — 호출부인
    # start_intl_tournament의 본선 생성 쪽에서 유럽만 이 예선 결과를 읽고
    # 나머지는 손 안 댐.
    if [t for t in get_tournaments(year) if t["kind"] == "cont_qual"]:
        return  # 이미 이 해 유로 예선이 생성됨 → 중복 방지
    _clear_entry_cache()
    my_nats, nat_info, committed = _gather_nat_context(p)
    try:
        _create_qual_tournament(year, "cont_qual", "유럽",
                                p=p, my_nats=my_nats, nat_info=nat_info,
                                committed=committed)
    except Exception as e:
        from game_engine import add_log
        add_log(f"⚠ {year}년 유로 예선 생성 오류: {e}", "event")


def start_intl_tournament(year):
    """44주차(INTL_CALLUP_WEEK) 진입 시 호출. 해당 연도 본선/대륙컵/
    클럽월드컵을 생성한다(예선은 이제 start_qualifying_if_needed가
    중간 휴식기에 별도로 처리 — 이 함수에서는 다루지 않음).

    [2026-07 재설계] 4년 주기:
      WC해     (2002,2006..) : 예선(이미 중간휴식기에 생성됨) + 본선(지금, 44주)
      WC해+1   (2003,2007..) : 클럽월드컵
      대륙컵해 (2004,2008..) : 대륙컵
      WC해+3   (2001,2005..) : 완전히 빈 해 — 아무 국제대회도 없음

    [복수국적·복수대륙컵] 미고정 선수가 서로 다른 대륙 국적을 보유하면,
    대륙컵 해에는 보유 국적이 속한 '각 대륙'의 대륙컵을 모두 생성한다.
    월드컵 해에는 종전과 동일하게 단일 대회만 생성한다.
    committed(고정)면 그 나라 대륙의 대륙컵 1개만 생성한다.
    """
    from game_engine import get_player
    p = get_player()
    if not p:
        return

    # [2026-08 버그수정] 정적 상수 대신 매번 실제 시작 연도 기준으로
    # 재계산 (_tournament_start_years 참고).
    _ty2 = _tournament_start_years()
    is_wc = year >= _ty2["wc"] and (year - _ty2["wc"]) % WC_INTERVAL == 0
    is_cont = (not is_wc and year >= _ty2["continental"]
               and (year - _ty2["continental"]) % CONTINENTAL_INTERVAL == 0)
    is_cwc = (not is_wc and not is_cont and year >= _ty2["cwc"]
              and (year - _ty2["cwc"]) % CWC_INTERVAL == 0)

    if is_cwc:
        # 월드컵 다음 해 — 캘린더 겹침이 전혀 없어서(챔스는 이미 23주차에
        # 다 끝나있는 상태) 이 해에 클럽 월드컵을 연다.
        from competition.club_world_cup_engine import start_club_world_cup
        start_club_world_cup(year)
        return
    if not is_wc and not is_cont:
        # [2026-08 신설] 완전히 빈 해였던 자리(2001,05,09..)에 3단계
        # 지역컵을 채운다 — 월드컵/대륙컵/클럽월드컵 어느 것과도 주기가
        # 안 겹치는 유일한 해라 스케줄 충돌이 없다.
        from constants import REGIONAL_CUP_INTERVAL, REGION_LIST
        is_regional = (year >= _ty2["regional"]
                       and (year - _ty2["regional"]) % REGIONAL_CUP_INTERVAL == 0)
        if not is_regional:
            return  # 정말 빈 해
        if [t for t in get_tournaments(year) if t["kind"] == "region"]:
            return  # 이미 그 해 지역컵이 생성됨 → 중복 생성 방지
        _clear_entry_cache()
        my_nats, nat_info, committed = _gather_nat_context(p)
        for region in REGION_LIST:
            _create_one_tournament(year, is_wc=False, my_continent=None,
                                   p=p, my_nats=my_nats, nat_info=nat_info,
                                   committed=committed, my_region=region)
        # [2026-08 신설, 신민용 확정] 유로(EURO) — 지역컵과 같은 해에,
        # 유럽만 별도로 한 번 더 대륙컵 형식(24개국 본선)을 연다. 여름에
        # start_qualifying_if_needed가 이미 이 해의 예선을 돌려놨을
        # 것이므로(_qualify_continental이 qual_results를 자동으로
        # 먼저 확인), 그 통과국 24개국이 그대로 본선에 들어간다.
        from constants import EURO_NAME
        if not [t for t in get_tournaments(year) if t["kind"] == "continent" and t["name"] == EURO_NAME]:
            try:
                _create_one_tournament(year, is_wc=False, my_continent="유럽",
                                       p=p, my_nats=my_nats, nat_info=nat_info,
                                       committed=committed, name_override=EURO_NAME)
            except Exception as e:
                from game_engine import add_log
                add_log(f"⚠ {year}년 유로(EURO) 생성 오류: {e}", "event")
        _close_other_pending_when_committed(year)
        return

    if [t for t in get_tournaments(year) if t["kind"] in ("world", "continent")]:
        return  # 이미 그 해 본선/대륙컵이 생성됨 → 중복 생성 방지

    _clear_entry_cache()   # 새 대회 → 이전 캐시 무효화
    my_nats, nat_info, committed = _gather_nat_context(p)

    if is_wc:
        # 월드컵: 전 세계 단일 대회. (대륙 개념 없음)
        _create_one_tournament(year, is_wc=True, my_continent=None,
                               p=p, my_nats=my_nats, nat_info=nat_info,
                               committed=committed)
        return

    # ── 대륙컵: 4개 대륙 연맹 전부 생성 ──
    #   예전엔 '내 국적이 속한 대륙'만 만들었으나, 챔피언스리그처럼
    #   4개 대륙 전부 항상 생성하도록 확장 — 다른 대륙 대회 결과도 역대기록에서
    #   조회 가능해짐. _create_one_tournament 내부는 my_nats를 그 대륙 국적과
    #   교집합해서 참가여부(my_sel)를 판정하므로, 내 국적이 없는 대륙은 그냥
    #   'AI끼리 진행되는 배경 대회'가 되고 선택창 등 기존 동작에는 영향 없다.
    all_confs = ["유럽", "아메리카", "아시아", "아프리카"]
    for cont in all_confs:
        _create_one_tournament(year, is_wc=False, my_continent=cont,
                               p=p, my_nats=my_nats, nat_info=nat_info,
                               committed=committed)

    # [본선 자동출전 정리] pledge/committed로 my_sel=1 확정된 본선이 있으면,
    #   같은 해 다른 '선택 대기(3)' 본선 대회는 닫는다(한 해 본선 출전 1개 보장).
    _close_other_pending_when_committed(year)


def _close_other_pending_when_committed(year):
    """그 해 본선 중 my_selected=1(자동/확정 출전)이 있으면,
    같은 해 나머지 본선 '선택 대기(3)'를 닫는다(my_selected=2).
    예선 pledge로 본선 자동출전한 경우, 다른 대륙 본선 선택창이 또 뜨는 것 방지."""
    conn = get_conn()
    has_committed = conn.execute(
        """SELECT 1 FROM intl_tournaments
           WHERE year=? AND my_selected=1
             AND kind IN ('world','continent','region') LIMIT 1""", (year,)).fetchone()
    if has_committed:
        # [2026-07 버그수정] choose_national_team의 동일 버그와 같은 이유로,
        # 여기서 닫히는 대회들의 intl_matches.is_my도 함께 초기화해야
        # "선택 안 한 나라 경기가 커리어에 같이 기록"되는 사고를 막는다.
        _closed2 = [r["id"] for r in conn.execute(
            """SELECT id FROM intl_tournaments
               WHERE year=? AND my_selected=3 AND kind IN ('world','continent','region')""",
            (year,)).fetchall()]
        conn.execute(
            """UPDATE intl_tournaments SET my_selected=2
               WHERE year=? AND my_selected=3 AND kind IN ('world','continent','region')""",
            (year,))
        if _closed2:
            _ph2 = ",".join("?" * len(_closed2))
            conn.execute(f"UPDATE intl_matches SET is_my=0 WHERE tournament_id IN ({_ph2})",
                         _closed2)
        conn.commit()
    conn.close()


def _conf_key(continent):
    """대륙명 → 4개 통합 연맹 대표키.
    유럽/아메리카/아시아/아프리카로 정규화."""
    return CONTINENT_TO_CONF.get(continent, continent)


def _precreate_ko_shell(conn, c, tid, tournament_type, tournament_start_day):
    """[2026-07 신설, 신민용 설계 제안: "경기 자체는 미리 존재하고 참가팀만
    나중에 확정된다"] 조별리그를 만드는 시점에 그 뒤 전체 토너먼트
    (R32/R16~결승, 3/4위전)의 '빈 대진' 행을 미리 만들어둔다 — home/away를
    빈 문자열로 둔 placeholder다. 실제 진출국이 정해지면(_finalize_groups/
    _advance_knockout) 새 행을 INSERT하는 대신 이 placeholder를
    UPDATE해서 팀 이름만 채워 넣는다.

    이 방식의 핵심 이점:
      - day/week가 대회 시작 시점에 전부 확정돼, 그 뒤 어떤 진행
        순서로 진행되든(하루씩/1주씩, 유저가 그 나라 선수든 아니든)
        일정이 다시 계산되거나 밀리는 일이 구조적으로 없어진다 —
        "8강전이 실제 날짜도 되기 전에 결장 처리된다"류의 타이밍 버그
        (지난 세션에 process_intl_week day필터로 막았던 것)의 근본
        원인 자체가 사라진다.
      - schedule_window.py 같은 화면에서 "8강 진출 시 경기 예정"을
        자연스럽게 보여줄 수 있다 — 행이 이미 존재하므로 그냥 home/away
        가 빈 문자열이면 "상대 미정"으로 표시하면 된다.

    match_count/day는 constants.TOURNAMENT_SCHEDULE_RULES를 그대로
    쓴다 — _advance_knockout이 다음 라운드 day를 계산할 때 쓰던 것과
    완전히 동일한 규칙이라 결과가 항상 일치한다. TP(3/4위전)는 기존
    관례대로 slot=999 하나만 둔다.

    [2026-08 버그수정, 신민용 리포트: "16강 미정인데 8강은 상대가 이미
    정해져서 뜬다"] 지역컵(kind='region')은 규모가 4/8/16강으로
    지역마다 달라서, TOURNAMENT_SCHEDULE_RULES["region"]에는 R16~F
    체인 전체를 등록해뒀다(_ko_seq가 실제로 어디부터 시작할지 골라
    씀) — 근데 이 함수는 그 표를 그대로 다 읽어서 8강부터 시작하는
    대회(예: AFF)에도 "절대 안 쓰일 16강" 빈 대진을 만들어버렸다.
    그 안 쓰이는 R16 자리를 schedule_window.py가 "다음 경기(미정)"로
    잘못 집어서, 실제로 이미 상대가 정해진 8강보다 먼저 보여준 것 —
    이 대회가 실제로 안 쓰는 앞단계 스테이지는 애초에 shell 자체를
    안 만든다.
    """
    _skip_stages = set()
    _skip_rounds = set()  # [2026-08 신설] (stage, round) 튜플 — 이 대회가
    # 실제로 안 쓰는 그룹 라운드(예: 3팀 조뿐인 대회의 group 4·5라운드)
    if tournament_type == "region":
        from constants import regional_cup_format
        _n_entries = c.execute(
            "SELECT COUNT(*) n FROM intl_entries WHERE tournament_id=?", (tid,)).fetchone()["n"]
        _fmt = regional_cup_format(_n_entries)
        _bracket = _fmt["bracket_size"]
        _full_seq = ["R16", "QF", "SF", "F"]
        _start_idx = {16: 0, 8: 1, 4: 2, 2: 3}.get(_bracket, 0)
        _skip_stages = set(_full_seq[:_start_idx])
        if "SF" in _skip_stages:
            # [2026-08 버그수정, 신민용 리포트: "CAFA/북아프리카(1개조,
            # 결승만 있는 대회)가 계속 진행중에서 안 끝난다"] bracket_
            # size=2(4강 없이 곧장 결승)인 대회는 3/4위전(TP)을 정할
            # 준결승 패자 자체가 없다 — 근데 TP shell을 그대로 만들면
            # "F는 끝났는데 TP는 영원히 미배정"이라 완료 판정(tp_done)이
            # 절대 True가 안 됐다. SF가 없는 대회는 TP도 같이 뺀다.
            _skip_stages.add("TP")
        # [2026-08 버그수정, 신민용 리포트: "3팀 조만 있는 대회는 원래
        # 1주면 끝나는데 다음 스테이지가 2주 넘게 밀린다"] 이 대회의
        # 실제 최대 조 인원(3~5명)으로 진짜 필요한 라운드 수를 구해서,
        # 그보다 많은 group round(예: 4·5라운드)는 다음 스테이지 시작일
        # 계산에서도, shell 생성에서도 완전히 빼버린다.
        _max_group_size = max(_fmt["group_sizes"]) if _fmt["group_sizes"] else 4
        _rounds_needed = len(_round_robin_pairs(_max_group_size))
        for _rd in range(_rounds_needed + 1, 6):
            _skip_rounds.add(("group", _rd))
        # [2026-08 버그수정] 위 _skip_stages(건너뛰는 KO 스테이지, 예:
        # SAFF는 R16·QF 둘 다 건너뛰고 SF부터 시작)는 shell 생성에서만
        # 빼고 있었지, "다음 스테이지 시작일" 누적 계산(stage_round_
        # start_day)에서는 안 빼고 있었다 — 그래서 SAFF는 4강 시작일을
        # 계산할 때도 "안 쓰는 16강+8강 몫"까지 그대로 더해져 불필요한
        # 공백이 더 컸다. 같은 skip 집합에 합쳐서 한 번에 넘긴다.
        _skip_rounds |= {(s, 1) for s in _skip_stages}

    rows = []
    for r in TOURNAMENT_SCHEDULE_RULES.get(tournament_type, []):
        stage = r["stage"]
        if stage == "group" or stage in _skip_stages:
            continue
        start_day = stage_round_start_day(tournament_type, stage, r["round"], tournament_start_day,
                                           skip=_skip_rounds)
        n = r["match_count"]
        day_list = assign_match_days(start_day, n, r["cap"])
        for idx in range(n):
            d = day_list[idx]
            wk = day_to_week(d)
            slot = 999 if stage == "TP" else idx
            rows.append((tid, stage, "", wk, d, "", "", -1, -1, 0, slot))
    if rows:
        c.executemany("""INSERT INTO intl_matches
                     (tournament_id, stage, grp, week, day, home, away,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""", rows)


def _fill_ko_shell(conn, c, tid, stage, home_away_by_slot):
    """[2026-07 신설] _precreate_ko_shell로 미리 만들어둔 그 stage의
    placeholder 행에 실제 팀 이름을 채워 넣는다(slot 번호로 매칭).
    혹시 그 stage의 placeholder가 없는 예외 상황(과거 세이브 호환,
    TOURNAMENT_SCHEDULE_RULES에 없는 잠정 체제 등)이면 해당 slot만
    새로 INSERT해서(day=None) 예전 동작으로 안전하게 폴백한다 — 대회가
    조용히 멈추는 것보다 day 정밀도 없이라도 진행되는 게 낫다.
    home_away_by_slot: {slot: (home, away, is_my)}
    """
    for slot, (home, away, is_my) in home_away_by_slot.items():
        cur = c.execute(
            """UPDATE intl_matches SET home=?, away=?, is_my=?
               WHERE tournament_id=? AND stage=? AND slot=?""",
            (home, away, is_my, tid, stage, slot))
        if cur.rowcount == 0:
            c.execute(
                """INSERT INTO intl_matches
                   (tournament_id, stage, grp, week, day, home, away,
                    home_score, away_score, is_my, slot)
                   VALUES(?,?,?,?,?,?,?,-1,-1,?,?)""",
                (tid, stage, "", 1, None, home, away, is_my, slot))


def _create_one_tournament(year, is_wc, my_continent, p, my_nats, nat_info, committed,
                            my_region=None, name_override=None):
    """대회 1개를 생성(조 추첨·일정 포함)하고 로그를 남긴다.

    - is_wc=True  : 월드컵(전 세계 단일). my_continent 무시.
    - is_wc=False : my_continent 대륙컵. cand_nats는 그 대륙 소속 보유국적만.
    - my_region 지정(is_wc=False와 함께): [2026-08 신설] 3단계 지역컵.
      my_continent는 무시되고, COUNTRY_REGION 기준 그 지역 소속 보유국적만
      후보가 된다. 조 편성은 regional_cup_format()으로 지역 규모에 맞춰
      자동 결정(4팀 고정 포트 방식이 아니라 3~4명 들쭉날쭉한 조를 스네이크
      시드로 채움) — 아래 group_sizes 참고.
    - name_override: [2026-08 신설] "유로(EURO)" — 유럽만 대륙컵 자리
      (2004년 주기, '유럽 네이션스컵')와 별개로 지역컵과 같은 해(2001년
      주기)에 한 번 더 열린다. CONF_CUP_NAME 고정 조회 대신 이 값으로
      대회명을 강제 지정한다 — 그 외 로직(조 편성·선발·기록)은 일반
      대륙컵과 100% 동일.
    """
    from game_engine import add_log

    group_sizes = None  # [2026-08 신설] region 모드에서만 씀(들쭉날쭉한 조 인원)
    if is_wc:
        kind, name = "world", "월드컵"
        entries = _qualify_world(year)
        n_groups = WC_GROUPS_BIG if year >= WC_EXPAND_YEAR else WC_GROUPS
        # 월드컵은 대륙 무관 → 내 국적 전부가 후보 대상
        cont_nats = [n for n in my_nats if n]
    elif my_region:
        from constants import REGION_CUP_NAME, COUNTRY_REGION, regional_cup_format
        kind = "region"
        name = REGION_CUP_NAME.get(my_region, f"{my_region} 지역컵")
        entries = _qualify_region(my_region)
        _fmt = regional_cup_format(len(entries))
        n_groups = _fmt["n_groups"]
        group_sizes = _fmt["group_sizes"]
        # 이 지역컵 후보 = 그 지역 소속 보유 국적만
        cont_nats = [n for n in my_nats if COUNTRY_REGION.get(n) == my_region]
    else:
        kind = "continent"
        name = name_override or CONF_CUP_NAME.get(my_continent, "대륙컵")
        entries = _qualify_continental(my_continent)
        n_groups = CONT_GROUPS
        # 이 대륙컵 후보 = 그 대륙(연맹) 소속 보유 국적만
        confs = set(CONFEDERATIONS.get(my_continent, [my_continent]))
        cont_nats = [n for n in my_nats
                     if nat_info.get(n, {}).get("continent") in confs]

    entry_names = {e["name"] for e in entries}
    # 이 대회 본선 진출한 내 국적
    qualified_nats = [n for n in cont_nats if n in entry_names]

    # [2026-08 신설, 신민용 리포트: "유로(EURO)는 예선 때 국대로 뽑혀도
    # 본선에서 탈락되는 경우가 있다 — 다른 국제대회(월드컵)처럼 예선
    # 통과하면 본선까지 그대로 가게 해달라"] 이 함수 안의 "본선에서 예선
    # 결과를 신뢰할지, 다시 실력 재검증(_check_selection)할지"는 원래
    # is_wc(월드컵인가)로만 갈렸다 — 월드컵은 예선(wc_qual)이 있으니 그
    # 결과를 신뢰하고, 그 외(is_wc=False)는 전부 "예선 자체가 없는 대회"로
    # 취급해 매번 재검증했다. 그런데 유로(EURO)는 나중에 자체 예선
    # (cont_qual)이 추가됐는데도 is_wc=False라서 여전히 "예선 없는 대회"
    # 취급을 받아, 예선을 통과한 선수가 본선 직전에 다시 재검증당해
    # 탈락할 수 있었다. has_qualifying으로 "이 대회가 예선을 실제로
    # 거쳤는가"를 is_wc와 분리해서 판단한다 — name_override(EURO_NAME)가
    # 있는 게 지금은 유로뿐이라 이걸로 구분한다. qual_kind는 그 예선
    # 기록을 실제로 조회할 때 쓸 intl_tournaments.kind 값이다(월드컵은
    # 'wc_qual', 유로는 'cont_qual').
    has_qualifying = is_wc or bool(name_override)
    qual_kind = "wc_qual" if is_wc else "cont_qual"

    # 출전국/선발 결정
    my_nat = ""
    cand_nats = []
    # [정책] pledge는 wc_qual → 월드컵 본선 연계에만 사용.
    #        대륙컵은 예선이 없으므로 pledge가 있어도 대륙컵 발탁에 영향 없음.
    pledged = (p.get("qual_pledged_nat", "") or "") if is_wc else ""
    _age_dropped = False  # [신규] 팀은 본선에 나갔지만 개인 재판정에서 탈락한 경우
    if committed:
        # [bugfix] committed path: also check wc_qual result
        #   If wc_qual existed last year, committed player who was
        #   not selected (my_selected=0) or eliminated (my_selected=2)
        #   must NOT participate in the main tournament.
        if committed not in cont_nats:
            my_sel = 2
        elif committed in qualified_nats:
            _blocked_by_qual = False
            if has_qualifying:
                try:
                    # [버그 수정 — 근본 원인] 예선에서 개인 재판정(_check_selection)에
                    # 떨어지면 my_nat이 ""(빈 문자열)로 저장된다(위
                    # _create_qual_tournament의 "my_nat = committed if
                    # my_sel==1 else ''" 참고). 그런데 여기 조회는
                    # "my_nat=committed"로 필터링해서, 정작 "미선발"이었던
                    # 바로 그 케이스(my_nat="")를 못 찾고 _bqr=None이
                    # 됐다 — None이면 _blocked_by_qual이 초기값 False로
                    # 남아서, 예선 미선발이었던 선수가 본선에는 오히려
                    # 자동 선발되는 정반대 결과가 나왔다(신민용 지적:
                    # "2009년 미선발인데 2010년 갑자기 본선 출전"). my_nat이
                    # 아니라 continent로 그 시즌 예선 기록 자체를 찾은 뒤,
                    # my_selected==1이면서 my_nat도 committed와 일치하는지
                    # 둘 다 확인한다.
                    # [2026-07 버그수정, 신민용 리포트: "예선 탈락했는데 본선에서
                    # 또 발탁창이 뜬다"] 예선·본선이 '같은 해'(year)에 진행되도록
                    # 재설계(start_qualifying_if_needed 참고)됐는데, 이 조회는
                    # 그 이전(예선이 전년도였던) 구조의 잔재로 여전히 year-1을
                    # 찾고 있었다 — 실제 wc_qual 레코드는 항상 year에 저장되므로
                    # year-1 조회는 영원히 빈 결과만 반환해 _bqr이 항상 None이
                    # 되고, 그 결과 예선 기록 유무와 무관하게 매번 _check_selection
                    # 재검증(사실상 예선 탈락 여부를 무시)으로 새 버그가 생겼다.
                    # [2026-08 버그수정, 신민용 리포트: "유로는 예선 통과해도
                    # 본선에서 탈락한다"] 이 조회가 kind='wc_qual'로 고정돼
                    # 있어서, 유로의 예선 기록(kind='cont_qual')은 찾지 못하고
                    # 매번 "예선 기록 없음" 폴백(아래 else — 실력 재검증)으로
                    # 빠졌다 — 사실상 유로는 예선을 봐놓고도 그 결과가 한 번도
                    # 안 쓰인 것. qual_kind로 월드컵/유로를 구분해 조회한다.
                    _committed_cont = nat_info.get(committed, {}).get("continent", "")
                    _bqc = get_conn()
                    _bqr = _bqc.execute(
                        """SELECT my_selected, my_nat FROM intl_tournaments
                           WHERE year=? AND kind=? AND continent=?
                           LIMIT 1""",
                        (year, qual_kind, _committed_cont)).fetchone()
                    _bqc.close()
                    if _bqr is not None:
                        _blocked_by_qual = not (_bqr["my_selected"] == 1
                                                 and _bqr["my_nat"] == committed)
                    else:
                        # [버그수정] 예선 기록 자체가 없는 경우(대륙명 표기 불일치,
                        # 저장 시점 문제 등으로 조회가 안 되는 경우 포함) 여기서
                        # "기록이 없으니 통과한 걸로 치자"고 넘어가면 정말로
                        # 예선에 출전조차 안 한 선수가 본선에 자동 발탁되는
                        # 사고가 난다(신민용 지적: 2028년 대륙컵 국가대표
                        # 미선발 + 2029년 예선 미참가 상태였는데 2030년
                        # 월드컵 본선에 OVR 70으로 출전). 증명할 예선 기록이
                        # 없으면 "통과했다"고 가정하지 말고, 대륙컵과 동일한
                        # 실력 재검증(_check_selection)을 거쳐야 한다.
                        _blocked_by_qual = not _check_selection(
                            p, nat_info.get(committed, {}).get("grade", "F"),
                            country=committed,
                            continent=nat_info.get(committed, {}).get("continent", ""))
                except Exception:
                    # [버그수정] 조회 중 예외가 나도 "통과로 간주"는 금물 —
                    # 위와 같은 이유로 실력 재검증으로 대체한다.
                    try:
                        _blocked_by_qual = not _check_selection(
                            p, nat_info.get(committed, {}).get("grade", "F"),
                            country=committed,
                            continent=nat_info.get(committed, {}).get("continent", ""))
                    except Exception:
                        _blocked_by_qual = False
            if _blocked_by_qual:
                my_sel = 2
                # [버그 수정] 이 케이스는 "우리나라가 예선에서 떨어졌다"가
                # 아니라 "나라는 본선에 갔는데 내가 예선 때 개인 재판정에서
                # 떨어졌다"는 완전히 다른 사실이다. 이 플래그가 없으면
                # 아래 로그가 무조건 "예선 탈락"(팀 실패)으로 찍혀서,
                # 실제로는 팀이 본선에 갔는데도 사실과 다른 메시지가
                # 남았다(스크린샷 사례: 2005년 "미선발" 다음 2006년
                # "예선 탈락"으로 바뀌어 마치 사유가 달라진 것처럼 보임 —
                # 신민용 지적).
                _age_dropped = True
            else:
                # [버그 수정 — 근본 원인] 대륙컵은 예선 자체가 없어서(제도상
                # 폐지됨), committed(국적 확정) 선수는 지금까지 "그 나라가
                # 대회 본선에 나가기만 하면" 개인 컨디션(OVR/나이)과 완전히
                # 무관하게 무조건 발탁(my_sel=1)됐다. 월드컵은 예선 단계
                # (_create_qual_tournament)에서 _check_selection이 한 번은
                # 걸러주지만, 대륙컵은 그 관문 자체가 없어서 한 번 국가를
                # 확정하면 이후로는 나이 먹어 기량이 떨어져도 평생 대표팀에
                # 뽑히는 것처럼 보였다(신민용 지적). choose_national_team이
                # 선택 확정 시 쓰는 것과 같은 판정 함수로 그 시점 실제
                # 기량을 재검증한다 — 월드컵/유로는 예선 통과로 이미
                # 검증됐으니(재판정하면 오히려 예선 이후 짧은 폼 기복에
                # 흔들릴 수 있어) 그대로 둔다.
                if (not has_qualifying) and not _check_selection(
                        p, nat_info.get(committed, {}).get("grade", "F"),
                        country=committed,
                        continent=nat_info.get(committed, {}).get("continent", "")):
                    my_sel = 2
                    _age_dropped = True
                else:
                    # [2026-08 신설, 신민용 요청: "22살 이후 국대 결정한 후엔
                    # 본선도 자동으로 치뤄지는데, 이것도 발탁 거절 형태로
                    # 뜨게 해달라"] 예전엔 committed 확정 이후로는 여기서
                    # 곧바로 my_sel=1로 확정해버려서, 미고정 시절(선택창이
                    # 뜨던 시절)과 달리 그 뒤로는 매 대회 소집을 거부할
                    # 기회가 영영 사라졌다. 미고정 케이스(my_sel=3)와 동일한
                    # 발탁창 경로로 보내되 cand_nats를 내 나라 하나만 담아
                    # 넘긴다 — choose_national_team/decline_national_team이
                    # 후보 1개짜리도 그대로 처리하므로(위 "🌍 {나라} 대표팀에서
                    # 발탁을 제안합니다" 단일 후보 로그 분기 참고) 새 로직 없이
                    # 기존 발탁/거절 인프라를 그대로 재사용한다.
                    my_nat = ""
                    my_sel = 3
                    cand_nats = [committed]
        else:
            my_sel = 2
    elif pledged and pledged in cont_nats:
        # [월드컵 예선 연계] 예선에서 pledge한 나라가 이 월드컵 후보군에 있음.
        if pledged in qualified_nats:
            # [2026-08 신설, 신민용 요청: 위와 동일 이유] pledge 확정 자동
            # 출전도 발탁창(my_sel=3)을 통해 수락/거절을 선택할 수 있게 한다.
            my_nat = ""
            my_sel = 3
            cand_nats = [pledged]
        else:
            my_sel = 2   # pledge한 나라가 본선 진출 실패(예선 탈락) → 출전 없음
    else:
        # 미고정 + pledge 없음.
        #   [월드컵/유로] 같은 해 예선(wc_qual/cont_qual)이 있었는데 그
        #                 결과가 없다 = 예선 미선발/탈락 → 본선 출전 불가.
        #   [일반 대륙컵] 예선 자체가 없으므로 무조건 발탁창을 띄운다.
        _had_qual = False
        if has_qualifying:
            try:
                _cc = get_conn()
                # [2026-07 버그수정, 신민용 리포트: "예선 탈락했는데 국제대회때
                # 한번 더 국대 나가실래요가 뜬다"] 예선·본선이 이제 '같은 해'
                # (year)에 진행되도록 재설계됐는데(start_qualifying_if_needed
                # 참고), 이 조회는 예선이 전년도였던 옛 구조 그대로 year-1을
                # 찾고 있었다 — 실제 wc_qual 레코드는 항상 같은 year에 저장돼
                # year-1 조회는 늘 빈 결과라 _had_wc_qual이 항상 False였다.
                # 그 결과 "예선이 아예 없었던 해"와 동일하게 취급돼, 예선에서
                # 떨어진 선수에게도 무조건(자격 재검증 없이) 발탁창이 떴다.
                # [2026-08 버그수정, 신민용 리포트: "유로는 예선 통과해도
                # 본선에서 탈락한다"] kind='wc_qual' 고정이라 유로의
                # cont_qual 예선 기록은 못 찾았다 — qual_kind로 구분.
                _qr = _cc.execute(
                    """SELECT 1 FROM intl_tournaments
                       WHERE year=? AND kind=? LIMIT 1""",
                    (year, qual_kind)).fetchone()
                _cc.close()
                _had_qual = bool(_qr)
            except Exception:
                pass

        cand_nats = [n for n in cont_nats if n]
        if has_qualifying and _had_qual:
            # 같은 해 예선이 있었음 → 예선에서 내 처리 결과 확인
            # my_selected=0(미선발)/2(예선탈락·미참가) → 본선 발탁창 없음
            # my_selected=1(예선출전확정) + pledge → 본선 발탁창 제시
            # [2026-07 버그수정] 위 _had_wc_qual과 동일한 이유로 year-1을
            # year로 수정 — wc_qual 레코드는 항상 같은 year에 있다.
            _qual_results = []
            try:
                _qc = get_conn()
                _qual_results = [dict(r) for r in _qc.execute(
                    "SELECT my_selected, my_nat FROM intl_tournaments"
                    " WHERE year=? AND kind=?",
                    (year, qual_kind)).fetchall()]
                _qc.close()
            except Exception:
                pass
            # 예선에서 선발 확정(my_selected=1)된 대회가 하나라도 있고
            # 그 나라가 본선에도 진출했으면 발탁창 제시 — 단, 후보가 하나뿐이면
            # (아직 국적이 안 고정된 선수라도 예선 단계에서 이미 그 나라 하나로만
            # 뛰어서 실질적으로 고를 게 없다) [2026-08 버그수정, 신민용 리포트:
            # "2002년 월드컵 예선에서 이미 그 나라로 뛰겠다고 선택해서 뛰었는데,
            # 같은 2002년 월드컵 본선 시작 직전에 똑같은 걸 또 물어본다"] 실제로
            # 선택할 게 없는데도(후보 1개) 매번 다시 팝업이 떠서 불필요한 반복
            # 이었다 — 후보가 2개 이상(진짜 복수국적 갈등)일 때만 재확인 발탁창을
            # 띄우고, 후보가 1개면 예선 선택을 그대로 이어받아 자동으로 확정한다.
            _qual_passed_nats = [
                r["my_nat"] for r in _qual_results
                if r["my_selected"] == 1 and r["my_nat"] in entry_names
            ]
            if len(_qual_passed_nats) == 1:
                my_sel = 1
                my_nat = _qual_passed_nats[0]
                cand_nats = []
            elif _qual_passed_nats:
                my_sel = 3
                cand_nats = _qual_passed_nats
            else:
                # 예선 미선발/탈락/미참가 → 본선 발탁창 없음
                my_sel = 2
                cand_nats = []
        elif cand_nats:
            # [2026-07 신설, 신민용 요청: "16세에 대륙컵 발탁창이 뜨는데
            # 이건 좀 비현실적이지 않나"] 대회 자체(전 세계 다른 나라들의
            # 대륙컵 진행)는 그대로 두고, 내가 후보로 뽑힐 수 있는 최소
            # 나이만 따로 제한한다 — 미달이면 그냥 "이 대회에 낄 수 있는
            # 후보가 없음(my_sel=2)"과 동일하게 처리해 발탁창 자체가 안 뜬다.
            from constants import MIN_INTL_CALLUP_AGE
            if p.get("age", 0) < MIN_INTL_CALLUP_AGE:
                my_sel = 2
                cand_nats = []
            else:
                my_sel = 3   # 발탁창 제시
        else:
            my_sel = 2   # 이 대회에 낄 수 있는 보유 국적 없음

    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO intl_tournaments(year, kind, name, status, my_selected, my_nat, cand_nats)
                 VALUES(?,?,?,?,?,?,?)""",
              (year, kind, name, "group", my_sel, my_nat, ",".join(cand_nats)))
    tid = c.lastrowid

    # 포트 추첨: 전력순 4개 포트 → 조마다 포트별 1팀
    # [2026-08 신설] region 모드는 조 인원이 3~4명으로 들쭉날쭉해서(예:
    # CECAFA 5조[4,4,4,3,3]) 4팀 고정 포트 방식이 안 맞는다 — 대신 OVR
    # 내림차순으로 정렬한 뒤 조를 순환하며 채우는 방식(라운드마다 그룹
    # 0→1→2...로 한 바퀴, 자리가 찬 조는 건너뜀)으로 강팀이 고르게
    # 분산되게 한다. group_sizes 합계 == len(entries)라 무조건 다 채워짐.
    entries.sort(key=lambda e: e["ovr"], reverse=True)
    groups = {g: [] for g in _GROUP_LABELS[:n_groups]}
    if kind == "region" and group_sizes:
        labels = _GROUP_LABELS[:n_groups]
        caps = list(group_sizes)
        gi = 0
        for e in entries:
            while caps[gi % n_groups] <= 0:
                gi += 1
            slot = gi % n_groups
            g = labels[slot]
            groups[g].append(e)
            caps[slot] -= 1
            gi += 1
            pot = len(groups[g])  # 그 조 안에서 몇 번째로 들어왔는지(포트 대용 표시값)
            c.execute("""INSERT INTO intl_entries
                         (tournament_id, country, flag, grade, ovr, grp, pot, alive)
                         VALUES(?,?,?,?,?,?,?,1)""",
                      (tid, e["name"], e["flag"], e["grade"], e["ovr"], g, pot))
    else:
        pot_size = len(entries) // 4
        for pot in range(4):
            pool = entries[pot * pot_size:(pot + 1) * pot_size]
            random.shuffle(pool)
            for gi, e in enumerate(pool):
                g = _GROUP_LABELS[gi]
                groups[g].append(e)
                c.execute("""INSERT INTO intl_entries
                             (tournament_id, country, flag, grade, ovr, grp, pot, alive)
                             VALUES(?,?,?,?,?,?,?,1)""",
                          (tid, e["name"], e["flag"], e["grade"], e["ovr"], g, pot + 1))

    # 조별리그 일정 (18~20주 앵커 → 이 시점부터 실제 day도 함께 배정)
    w0 = INTL_GROUP_WEEKS[0]
    # [Phase 2] 32/48개국 체제 판별은 위에서 n_groups 정할 때 쓴 것과 동일한
    # 기준(year >= WC_EXPAND_YEAR)을 그대로 재사용.
    # [2026-08 신설] region은 TOURNAMENT_SCHEDULE_RULES에 등록된 체제가
    # 아니라 get_stage_rule이 항상 None을 반환 → day는 NULL로 남고 week만
    # 배정된다(기존에도 "잠정치 미확정 체제"에 이미 있던 안전한 폴백 경로,
    # 새 코드 아님).
    tournament_type = (("world_cup_48" if year >= WC_EXPAND_YEAR else "world_cup_32")
                        if is_wc else ("region" if kind == "region" else "continental"))
    tournament_start_day = week_to_day(w0)
    if my_nat:
        _my_match_nats = {my_nat}
    elif my_sel == 3:
        _my_match_nats = set(cand_nats)
    else:
        # my_sel==2: 출전 없음 → 내 경기 없음 (기존엔 cont_nats 전체가 들어가던 버그)
        _my_match_nats = set()
    # [2026-08 신설] region 모드는 조마다 인원(3~4명)이 달라서 라운드별
    # 대진(pairs)도 조마다 따로 계산해야 한다 — _GROUP_ROUNDS 하나를
    # 전체 조가 공유하던 기존 방식 대신, 조별로 _round_robin_pairs(그
    # 조 인원)를 미리 구해두고 라운드 인덱스로 조회한다. 4팀 조는
    # _round_robin_pairs(4) == _GROUP_ROUNDS라 기존 대륙컵/월드컵 경로는
    # 값 그대로 100% 동일하게 동작(회귀 없음).
    _group_round_pairs = {g: _round_robin_pairs(len(members)) for g, members in groups.items()}
    _max_rounds = max((len(rp) for rp in _group_round_pairs.values()), default=0) \
        if kind == "region" else len(_GROUP_ROUNDS)
    for rd in range(_max_rounds):
        # [2026-07 버그 수정, DB로 실제 확인] wk = w0 + rd(라운드 인덱스로
        # 대충 계산)는 day_list가 4일 capacity 기준으로 분산 배정되는 것과
        # 안 맞았다 — 한 라운드(4일)가 실제 캘린더 week 경계(7일 단위)와
        # 어긋나서, day는 맞는데 week 컬럼이 틀린 행이 생겼다(실제 재현:
        # day313은 진짜 45주인데 저장은 46주로 찍힘 → get_my_match가
        # week=45로 조회하면 이 행을 영원히 못 찾음). day가 먼저 정해지면
        # week는 항상 그 day로부터 역산한다(day_to_week) — 아래서 매치별로
        # 처리.
        # [Phase 2, 중요] 이 라운드에 속한 모든 (조, 매치) 쌍을 먼저 한 줄로
        # 펼친 다음 day 리스트를 1:1로 대응시킨다 — 라운드 하나에 day 값
        # 하나만 주면 daily_match_capacity가 무시되고 "day라는 이름의
        # week"밖에 안 나온다(실제로 이 실수를 했다가 지적받고 고침).
        round_matches = [(g, hi, ai) for g in groups
                          for rp in [_group_round_pairs[g]] if rd < len(rp)
                          for hi, ai in rp[rd]]
        rule = get_stage_rule(tournament_type, "group", rd + 1)
        if rule:
            round_start_day = stage_round_start_day(
                tournament_type, "group", rd + 1, tournament_start_day)
            day_list = assign_match_days(round_start_day, len(round_matches), rule["cap"])
        else:
            # tournament_schedule_rules에 없는 체제(잠정치 미확정 등)는
            # day를 NULL로 남긴다 — week 기반 진행에는 영향 없음.
            day_list = [None] * len(round_matches)
        for (g, hi, ai), day_val in zip(round_matches, day_list):
            members = groups[g]
            home, away = members[hi], members[ai]
            is_my = 1 if (home["name"] in _my_match_nats or away["name"] in _my_match_nats) else 0
            wk = day_to_week(day_val) if day_val is not None else (w0 + rd)
            c.execute("""INSERT INTO intl_matches
                         (tournament_id, stage, grp, week, day, home, away,
                          home_score, away_score, is_my, slot)
                         VALUES(?,?,?,?,?,?,?,-1,-1,?,0)""",
                      (tid, "group", g, wk, day_val, home["name"], away["name"], is_my))

    # [2026-07 신설] 그룹리그가 끝나기도 전에, 이후 전체 토너먼트의 '빈
    # 대진'을 미리 만들어둔다 — 자세한 이유는 _precreate_ko_shell 참고.
    _precreate_ko_shell(conn, c, tid, tournament_type, tournament_start_day)

    conn.commit()
    conn.close()

    # ── 로그 ──
    add_log("─" * 44, "sep")
    add_log(f"🌍 {year}년 {name} 개막!  본선 {len(entries)}개국", "event", year, INTL_CALLUP_WEEK)

    if my_sel == 3:
        nat_list = " / ".join(cand_nats)
        if len(cand_nats) == 1:
            add_log(f"   🌍 {nat_list} 대표팀에서 발탁을 제안합니다! 출전 여부를 선택하세요",
                    "event", year, INTL_CALLUP_WEEK)
        else:
            add_log(f"   🌍 여러 나라가 당신을 원합니다! {nat_list} 중 대표팀을 선택하세요",
                    "event", year, INTL_CALLUP_WEEK)
    elif my_nat:
        _grow = _country_flag(my_nat)
        try:
            my_g = next(g for g, ms in groups.items() if any(m["name"] == my_nat for m in ms))
            mates = [f"{m['flag']}{m['name']}" for m in groups[my_g] if m["name"] != my_nat]
            add_log(f"   {_grow}{my_nat} {my_g}조 편성  (vs {', '.join(mates)})",
                    "event", year, INTL_CALLUP_WEEK)
        except StopIteration:
            pass
        if my_sel == 1:
            add_log(f"   📣 국가대표 소집! 조별리그 {w0}~{w0+2}주차", "event", year, INTL_CALLUP_WEEK)
        else:
            add_log("   📋 국가대표 미선발... 대표팀 경기를 지켜봅니다", "event", year, INTL_CALLUP_WEEK)
            _save_trophy(year, my_nat, name, "국가대표 미선발")
    else:
        # my_sel==2
        #   committed가 이 대회 대륙이고 예선 탈락한 경우만 기록.
        #   (committed가 타 대륙이라 이 대회와 무관하면 아무 기록도 남기지 않는다.)
        # [버그 수정] 팀은 본선에 나갔는데 개인 재판정(_age_dropped)에서
        # 떨어진 경우까지 "예선 탈락"으로 뭉뚱그리면 사실과 다른 메시지가
        # 된다 — 나이/기량 저하로 못 뽑힌 것과 국가가 본선에 못 간 것은
        # 다른 사실이므로 구분해서 기록한다.
        if committed and committed in cont_nats:
            if _age_dropped:
                add_log(f"   📋 {committed} 대표팀 미선발 (기량 저하) — 이번 대회 출전 없음",
                        "event", year, INTL_CALLUP_WEEK)
                _save_trophy(year, committed, name, "국가대표 미선발")
            else:
                add_log(f"   📋 {committed} 예선 탈락 — 이번 대회 출전 없음", "event", year, INTL_CALLUP_WEEK)
                _save_trophy(year, committed, name, "예선 탈락")

def _country_flag(name):
    """국가 국기 조회 (없으면 빈 문자열)."""
    conn = get_conn()
    r = conn.execute("SELECT flag FROM countries WHERE name=?", (name,)).fetchone()
    conn.close()
    return r["flag"] if r else ""


def _vet_bonus(age):
    """베테랑 가산점: 노련함 프리미엄. 최대 +5. (절대 하한은 별도 보장)"""
    if age >= 36:
        return 5
    if age >= 33:
        return 4
    if age >= 30:
        return 2
    return 0


def _intl_form_raw(pos, goals, assists, rating, cs=0):
    """[2026-07 신설] 국가대표 선발 전용 '순수 폼' 점수 — 개인상의
    _position_award_score와 같은 포지션별 가중치를 쓰되 ovr*0.3 항은 뺀다.
    최종 선발점수에서 OVR을 이미 45%로 따로 반영하니, 폼 안에 또 섞이면
    이중 반영이 된다(신민용 지적)."""
    if pos in GK_POS:
        return cs * 3.0 + rating * 8.0
    if pos in DF_POS:
        return goals * 1.0 + assists * 1.5 + rating * 7.0
    if pos in MF_POS:
        return goals * 1.5 + assists * 2.0 + rating * 6.0
    return goals * 2.0 + assists * 1.0 + rating * 5.0  # FW/기타


def _normalize_form(raw_by_id, my_id, my_raw):
    """그 포지션 그룹 후보 전원의 raw 폼 점수를 상대 정규화(최고=100,
    최저=40)한다 — 시대·리그 수준이 달라도(1960년대 vs 2020년대) 항상
    같은 스케일로 비교 가능하게. 후보가 나 하나뿐이면 70(평균) 취급."""
    all_raw = list(raw_by_id.values()) + [my_raw]
    mx, mn = max(all_raw), min(all_raw)
    if mx == mn:
        return {k: 70.0 for k in raw_by_id}, 70.0
    def _norm(v):
        return 40.0 + (v - mn) / (mx - mn) * 60.0
    return {k: _norm(v) for k, v in raw_by_id.items()}, _norm(my_raw)


def _intl_tier_penalty(tier):
    """[2026-07 신설, 신민용 지적: "K2/K3에서만 뛴 선수가 국가대표 주전
    경쟁을 하는 건 비현실적 — 선발 로직이 지금 뛰는 리그 등급(1부인지
    하위리그인지)을 아예 안 본다"] OVR·폼만으로 선발을 정하면, 하위리그
    에서도 OVR/폼 수치만 맞으면 1부 경험이 전무해도 상위 대표팀 경쟁에서
    이길 수 있다 — 실제로는 "지금 어느 무대에서 검증되고 있는지"가 대표팀
    선발에 크게 작용한다(스카우트 노출·코칭 수준·경기 강도 차이). 모든
    나라·포지션에 공통 적용되는 완만한 페널티를 선발점수에 추가한다 —
    1부는 무페널티, 부수가 내려갈수록 커진다. 절대 문턱(하위리그는 무조건
    탈락)은 아니라서, 정말 압도적인 폼/OVR을 가진 '깜짝 발탁'은 여전히
    가능하다(실제 축구에도 드물게 있는 사례) — 다만 그 정도로 확실하게
    나아야 한다는 문턱만 높였다.
    """
    t = tier or 1
    if t <= 1:
        return 0.0
    return {2: -4.0, 3: -9.0, 4: -15.0}.get(t, -18.0)


def _intl_ovr_gap_penalty(ovr, real_squad_ovr):
    """[2026-07 재설계, 신민용 지적: "OVR 컷은 좋은 아이디어지만 하드컷
    보다는 평균과의 격차에 따라 단계적으로 문턱이 낮아지는 게 현실적이다
    — 88.4는 절대 못 뽑히는 게 아니니까"] 하드컷(딱 잘라 탈락) 대신
    평균과의 gap을 5단계로 나눠 선발점수에서 깎는 정도를 조절한다.
    "후보 자격을 아예 박탈"하는 게 아니라 "경쟁에서 불리하게 만드는"
    방식이라, 폼이 특출나면(아래 형식 경쟁에서) 여전히 역전 가능하다.

      평균+2 이상   → 0점 감점    (무조건 후보)
      평균 ~ +2     → 1점 감점    (대부분 후보)
      평균-2 ~ 0    → 6점 감점    (폼이 좋아야 후보)
      평균-5 ~ -2   → 14점 감점   (특수한 경우만)
      평균-5 미만   → 30점 감점   (거의 불가능하지만 절대불가는 아님)
    """
    if real_squad_ovr is None:
        return 0.0
    gap = ovr - real_squad_ovr
    if gap >= 2:
        return 0.0
    elif gap >= 0:
        return 1.0
    elif gap >= -2:
        return 6.0
    elif gap >= -5:
        return 14.0
    else:
        return 30.0


def _check_selection(p, my_grade, country="", continent=""):
    """국가대표 선발 판정 — [2026-07 전면 재설계, 신민용 확정] "OVR이 그
    나라 평균과 비슷하면 무조건 뽑힌다"는 절대 문턱 방식에서, 실제
    국가대표처럼 포지션별 정원(23인 = GK3/DF8/MF8/FW4) 안에서 동포지션
    후보들과 경쟁하는 방식으로 바꿨다. 같은 포지션에 나보다 잘하는 동포
    선수가 이미 정원만큼 있으면, 내 OVR이 웬만큼 높아도 밀릴 수 있다
    (실제로 그렇듯이).

    선발점수 = OVR×45% + 정규화폼×55% − 페널티 − 부수페널티(_intl_tier_penalty)
      - 폼은 _intl_form_raw()(개인상 포지션 가중치에서 ovr 항만 뺀 것)를
        그 나라 동포지션 후보군 안에서 상대 정규화(최고100/최저40)한 값.
        "지금 잘하는 선수"가 커리어 내내 OVR만 높은 선수보다 유리해지는
        핵심 장치.
      - 페널티는 예전처럼 임계값을 올리는 게 아니라 선발점수를 직접 깎는다
        (장기부상 -15, 감독불화 -8, 출장시간부족 -6, 하위리그 소속 -4~-18).
        하위리그 페널티는 나(내 선수)와 AI 동포 후보 전원에게 동일한
        기준으로 적용된다(그 나라 CM AI가 하위리그에 있어도 똑같이 깎임).
    포지션 그룹(GK/DF/MF/FW) 안에서 AI 동포 선수 전원(_estimate_ai_season
    으로 폼 추정) + 나를 한 풀에 놓고 점수 순으로 정렬 → 정원 안에 들면
    선발. 정원 경계(마지노선, INTL_SELECTION_MARGIN=3점 이내 차이)에서는
    25% 확률로 순위가 뒤집힐 수 있다 — 격차가 크면(3점 초과) 뒤집히지
    않는다."""
    from constants import MIN_INTL_CALLUP_AGE
    if p.get("age", 0) < MIN_INTL_CALLUP_AGE:
        return False

    my_pos = p.get("position", "ST")
    if my_pos in GK_POS:
        pos_group, group_members = "GK", GK_POS
    elif my_pos in DF_POS:
        pos_group, group_members = "DF", DF_POS
    elif my_pos in MF_POS:
        pos_group, group_members = "MF", MF_POS
    else:
        pos_group, group_members = "FW", FW_POS

    nat = country or p.get("nationality", "")

    # [2026-07 재설계, 신민용 지적: "그래도 K3리그에서 뛰는 선수가 국대
    # 발탁은 아니지" → "OVR는 그 나라 평균은 되어야 뽑히게" → "근데 딱
    # 잘리는 하드컷보다는 평균과의 격차에 따라 단계적으로 낮아지는 게
    # 현실적이다"] _get_real_squad_ovr(그 나라 실제 스쿼드 평균 OVR —
    # get_country_ovr에 이미 70% 가중치로 블렌딩되는 것과 같은 값)를
    # "후보 자격을 아예 박탈하는 하드컷"이 아니라 "선발점수에서 깎는
    # 정도를 정하는 기준"으로 쓴다(_intl_ovr_gap_penalty, 5단계 그라데이션)
    # — 최종 판정은 여전히 아래 포지션 경쟁·폼 반영 점수 비교가 담당한다.
    # 그 나라 실제 스쿼드 표본이 너무 적어(8명 미만) real_val이 없으면
    # (신생/희귀 국적 등) 감점 없이(0.0) 기존 폼 경쟁으로만 판정한다.
    _real_squad_ovr = _get_real_squad_ovr(nat) if nat else None

    quota = INTL_SQUAD_QUOTA.get(pos_group, 4)

    _league_grade_team = get_league_grade(country, my_grade)
    team_avg = GRADE_TEAM_OVR.get(my_grade, 45) + \
        get_country_ovr_bonus(country, _league_grade_team, continent)

    # 그 나라 동포지션 AI 후보 전원 수집 + 폼 추정 (+ 실제 소속 리그 부수)
    # [2026-07 재설계] AI 후보 쪽도 SQL로 아예 걸러내지 않는다 — 나만
    # 경쟁에서 불리해지고 AI는 무조건 후보 자격을 유지하는 비대칭을
    # 피하기 위해, 아래 ai_scores 계산에서 동일한 그라데이션 감점을
    # 각 AI 후보의 OVR 기준으로 적용한다(경쟁 자체에서 자연스럽게 밀림).
    conn = get_conn()
    ph = ",".join("'%s'" % pp for pp in group_members)
    rows = conn.execute(
        f"""SELECT ap.id, ap.ovr, ap.position, ap.sub_role, ap.age, l.tier AS tier
            FROM ai_players ap
            LEFT JOIN teams t ON ap.team_id = t.id
            LEFT JOIN leagues l ON t.league_id = l.id
            WHERE ap.nationality=? AND ap.position IN ({ph})""", (nat,)).fetchall()
    conn.close()
    from game_engine import _estimate_ai_season, _estimate_ai_clean_sheets, _calc_clean_sheets_for_player
    raw_by_id, ovr_by_id, tier_by_id = {}, {}, {}
    for r in rows:
        # [2026-07 버그수정, 신민용 리포트: "K2/K3에서만 뛴 선수가 국가대표
        # 로 너무 쉽게 뽑힌다"] _estimate_ai_season/_estimate_ai_clean_sheets에
        # full_season_matches를 안 넘겨서 기본값 14로 계산되고 있었다 —
        # 반면 내 선수의 폼은 실제 풀시즌(보통 30~38경기) 누적치를 그대로
        # 쓴다. 즉 AI 동포 후보 전원의 추정 생산량이 실제의 1/3 수준으로
        # 깎인 채 나(전체 시즌 실측치)와 비교됐다 — 그 결과 내 폼이 OVR
        # 90대 AI보다도 항상 정규화 최고점(100)을 받아버려서, 부수 페널티
        # 정도로는 이 격차를 절대 못 뒤집었다. 38경기(리그 대표 기준,
        # GOALS_PER_GAME_FOR_TITLE 등 다른 곳에서 쓰는 기준과 동일)로
        # 맞춰서 AI도 나와 동일한 '풀시즌' 기준으로 비교되게 한다.
        g, a, rt = _estimate_ai_season(r["ovr"], r["position"], team_avg, team_avg, r["sub_role"],
                                        full_season_matches=38)
        cs = _estimate_ai_clean_sheets(r["position"], r["ovr"], team_avg, team_avg, 38) if pos_group == "GK" else 0
        raw_by_id[r["id"]] = _intl_form_raw(r["position"], g, a, rt, cs)
        ovr_by_id[r["id"]] = r["ovr"]
        tier_by_id[r["id"]] = r["tier"] or 1   # 팀 정보 없음(드묾) → 무페널티 폴백

    # 내 폼(실제 이번 시즌 기록)
    # [2026-07 버그수정, 신민용 리포트: "발롱도르 5회 FIFA/UEFA 올해의
    # 선수인 선수가 국가대표에 단 한 번도 못 뽑힌다"] 월드컵 예선은 시즌
    # 중간(INTL_QUAL_START_DAY, 클럽 시즌의 약 60% 지점)에 열리는데, 이
    # 시점의 내 season_goals/season_assists는 '그 시즌 지금까지 누적치'
    # (아직 시즌 안 끝남)인 반면, AI 동포 후보는 _estimate_ai_season이
    # 처음부터 풀시즌(38경기) 기준으로 추정된다 — 즉 내 폼만 시즌 중간
    # 스냅샷과 AI의 풀시즌 추정치를 그대로 비교하는 규격 불일치가 있었다.
    # 아무리 압도적인 시즌을 보내고 있어도 시즌이 덜 끝났다는 이유만으로
    # 골/어시 누적치가 AI보다 항상 낮게 나와 선발점수가 부당하게 깎였다.
    # 지금까지 뛴 경기 수 기준으로 38경기(AI와 동일한 풀시즌 기준) 페이스로
    # 환산해서 비교한다 — 평점(rating)은 총량이 아니라 평균이라 이미
    # 시즌 길이와 무관하므로 그대로 둔다.
    _rc = p.get("season_rating_cnt", 0)
    my_avg_rating = (p.get("season_rating_sum", 0) / _rc) if _rc > 0 else 6.0
    my_cs = _calc_clean_sheets_for_player(p) if pos_group == "GK" else 0
    _played_so_far = p.get("season_matches", 0)
    if 0 < _played_so_far < 38:
        _pace = 38.0 / _played_so_far
        _my_g = p.get("season_goals", 0) * _pace
        _my_a = p.get("season_assists", 0) * _pace
        if pos_group == "GK":
            my_cs = my_cs * _pace
    else:
        _my_g = p.get("season_goals", 0)
        _my_a = p.get("season_assists", 0)
    my_raw = _intl_form_raw(my_pos, _my_g, _my_a, my_avg_rating, my_cs)

    norm_by_id, my_norm = _normalize_form(raw_by_id, id(p), my_raw)

    # 페널티(선발점수 직접 감점 — 예전처럼 임계값 상향이 아님)
    penalty = 0.0
    if p.get("injured"):
        penalty += 15.0
    rel = p.get("manager_relation", 50)
    if rel < 30:
        penalty += 8.0
    elif rel < 50:
        penalty += 3.0
    if p.get("season_matches", 0) < 10:
        penalty += 6.0
    penalty -= _intl_tier_penalty(p.get("current_tier", 1))   # 마이너스 페널티를 더하는 형태이므로 부호 반전
    penalty += _intl_ovr_gap_penalty(p.get("ovr", 0), _real_squad_ovr)

    my_ovr = p.get("ovr", 0)
    my_score = my_ovr * 0.45 + my_norm * 0.55 - penalty
    ai_scores = sorted((ovr_by_id[k] * 0.45 + norm_by_id[k] * 0.55 + _intl_tier_penalty(tier_by_id.get(k, 1))
                        - _intl_ovr_gap_penalty(ovr_by_id[k], _real_squad_ovr)
                        for k in raw_by_id), reverse=True)

    if len(ai_scores) < quota:
        return True  # 그 포지션에 나 포함해도 정원이 안 찬 나라 — 자동 선발

    boundary_top = ai_scores[quota - 1] if quota - 1 < len(ai_scores) else -999
    boundary_bottom = ai_scores[quota] if quota < len(ai_scores) else -999
    my_rank = 1 + sum(1 for s in ai_scores if s > my_score)

    if my_rank <= quota:
        if my_rank == quota and (boundary_top - boundary_bottom) <= INTL_SELECTION_MARGIN:
            return random.random() < 0.75   # 마지노선 접전 — 25% 확률로 밀려남
        return True
    else:
        if my_rank == quota + 1 and (boundary_top - boundary_bottom) <= INTL_SELECTION_MARGIN:
            return random.random() < 0.25   # 마지노선 접전 — 25% 확률로 발탁
        return False


def _qualify_world(year=0):
    """4개 대륙 연맹의 예선 결과(qual_results)를 조합해 본선 진출국 확정.

    - 예선 결과가 있는 연맹: qual_results에서 읽어 그대로 사용
    - 예선 결과가 없는 연맹(이전 세이브 호환 등): 등급 기반 랜덤 선발
    - 쿼터 합산이 본선 팀 수(32 or 48)와 맞지 않으면 부족분을 랜덤으로 보충
    """
    big = (year >= WC_EXPAND_YEAR)
    quota_map = WC_QUOTA_BIG if big else WC_QUOTA
    n_teams   = WC_TEAMS_BIG  if big else WC_TEAMS

    # 예선 통과국 로드
    conn = get_conn()
    qual_rows = [dict(r) for r in conn.execute(
        "SELECT country, flag, grade, ovr, continent FROM qual_results WHERE target_year=? AND kind='world'",
        (year,)).fetchall()]
    all_countries = [dict(r) for r in conn.execute(
        "SELECT name, flag, continent, grade FROM countries").fetchall()]
    conn.close()

    # 오세아니아 → 아시아, 북중미/북미 → 아메리카 정규화
    import time
    _t0 = time.perf_counter()
    for r in all_countries:
        r["conf"] = _conf_key(r["continent"])
        r["ovr"]  = _nat_team_ovr(r["grade"], r["name"], r["continent"], fast=True, year=year)
        r["qual"] = GRADE_QUAL_BASE.get(r["grade"], 0.2) + random.uniform(-QUAL_NOISE, QUAL_NOISE)
    print(f"[PERF] 월드컵 예선 전세계 {len(all_countries)}개국 OVR계산 {time.perf_counter()-_t0:.2f}s")
    # [버그 수정 — 근본 원인] qual_results에 같은 나라가 중복으로 들어있으면
    # (과거 _save_qual_results의 중복 저장 버그, 지금은 수정됨) 아래
    # "[:quota]" 자르기에서 중복 항목이 자리를 차지해 실제로 예선을
    # 통과한 나라가 쿼터 밖으로 밀려나는 문제가 있었다(예: 유럽 13장인데
    # 어떤 나라가 3번 겹쳐 들어가면 진짜 13번째 통과국이 잘려나감 —
    # "예선은 통과로 기록됐는데 본선 진출국 명단엔 없다"는 모순의 원인).
    # _save_qual_results 쪽 중복 저장 자체는 이미 막았지만, 혹시 모를
    # 잔존 데이터나 재발에도 안전하도록 여기서도 국가명 기준으로 한 번 더
    # 중복 제거한다.
    qual_by_conf = {}   # conf_key → [국가dict]
    _seen_by_conf = {}  # conf_key → {country_name, ...}
    for q in qual_rows:
        ck = _conf_key(q.get("continent", ""))
        if not ck:
            continue
        seen = _seen_by_conf.setdefault(ck, set())
        if q["country"] in seen:
            continue
        seen.add(q["country"])
        qual_by_conf.setdefault(ck, []).append({
            "name": q["country"], "flag": q["flag"],
            "continent": ck, "grade": q["grade"], "ovr": q["ovr"], "qual": 1.0
        })

    picked = []
    picked_names = set()

    # 1) 예선 결과 있는 연맹: 그대로 사용 (쿼터만큼)
    for conf, quota in quota_map.items():
        ck = _conf_key(conf)
        if ck in qual_by_conf:
            teams = qual_by_conf[ck][:quota]
            picked.extend(teams)
            picked_names.update(t["name"] for t in teams)

    # 2) 예선 결과 누락 연맹 → 오류 로그 + 예외 발생
    # 예선 시스템이 정상 작동한다면 모든 연맹에 qual_results가 있어야 한다.
    # 랜덤 보충은 "예선이 없던 시대" 호환용이었으나, 현재는 예선 버그를
    # 숨기는 역할만 하므로 제거하고 명시적 오류를 발생시킨다.
    missing_confs = []
    for conf in quota_map:
        ck = _conf_key(conf)
        if ck not in qual_by_conf:
            missing_confs.append(conf)
    if missing_confs:
        from game_engine import add_log
        msg = f"[오류] {year}년 월드컵 본선: 예선 결과 누락 — {missing_confs}"
        add_log(msg, "event")
        raise RuntimeError(msg)

    # 각 연맹 쿼터 충족 여부 검증 (초과는 허용, 미달만 오류)
    for conf, quota in quota_map.items():
        ck = _conf_key(conf)
        actual = len(qual_by_conf.get(ck, []))
        if actual < quota:
            from game_engine import add_log
            msg = (f"[오류] {year}년 월드컵 본선: {conf} 예선 통과국 {actual}팀 < 쿼터 {quota}팀"
                   f" — 예선이 완료되지 않았거나 qual_results가 불완전합니다")
            add_log(msg, "event")
            raise RuntimeError(msg)

    return picked[:n_teams]


def _qualify_continental(my_continent):
    """내 대륙 연맹의 대륙컵 24개국 선발 (남북미 통합, 오세아니아→아시아).
    작년 예선 결과(qual_results)가 있으면 우선 사용, 없으면 랜덤 계산."""
    from game_engine import get_state
    st = get_state() or {}
    year = st.get("current_year", 0)
    cont_key = _conf_key(my_continent)

    conn = get_conn()
    qual_rows = [dict(r) for r in conn.execute(
        """SELECT country, flag, grade, ovr FROM qual_results
           WHERE target_year=? AND kind='continent' AND continent=?""",
        (year, cont_key)).fetchall()]
    conn.close()

    if qual_rows:
        # 예선 통과국 사용. [버그 수정] world 쪽과 동일한 이유로 국가명
        # 기준 중복 제거 후 자른다(위 _qualify_world 주석 참고).
        seen = set()
        result = []
        for q in qual_rows:
            if q["country"] in seen:
                continue
            seen.add(q["country"])
            result.append({"name": q["country"], "flag": q["flag"], "grade": q["grade"],
                            "ovr": q["ovr"]})
        return result[:CONT_TEAMS]

    # 폴백: 기존 랜덤 방식
    confs = CONFEDERATIONS.get(my_continent, [my_continent])
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"SELECT name, flag, continent, grade FROM countries WHERE continent IN ({','.join('?'*len(confs))})",
        confs).fetchall()]
    conn.close()
    for r in rows:
        r["qual"] = GRADE_QUAL_BASE.get(r["grade"], 0.2) + random.uniform(-QUAL_NOISE, QUAL_NOISE)
        r["ovr"] = _nat_team_ovr(r["grade"], r["name"], r["continent"], fast=True, year=year)
    print(f"[PERF] 대륙컵 예선 폴백 {len(rows)}개국 OVR계산 완료")
    rows.sort(key=lambda r: r["qual"], reverse=True)
    return rows[:CONT_TEAMS]


def _qualify_region(my_region):
    """[2026-08 재설계 v2] 지역컵 참가국 = 그 지역 소속 국가 풀에서 목표
    본선 규모(REGION_TARGET_SIZE)만큼, 그 해 국가 OVR 상위 순으로 뽑는다.
    풀이 목표보다 크면(예: WAFF 12개국 풀 → 12개국 목표는 그대로, CECAFA
    11개국 풀 → 8개국 목표는 상위 8개국만) 나머지는 그 대회에 못 낀다 —
    고정 제외가 아니라 매 대회마다 그 시점 실력으로 다시 판정한다(신민용
    확정: "그 당시 국가 OVR 기준으로 낮은 국가는 참여 안 하는 걸로").
    풀이 이미 목표 이하면(예: CAFA 5개국 풀=5개국 목표) 전원 참가."""
    from game_engine import get_state
    from constants import COUNTRY_REGION, REGION_TARGET_SIZE
    st = get_state() or {}
    year = st.get("current_year", 0)
    names = [c for c, r in COUNTRY_REGION.items() if r == my_region]
    conn = get_conn()
    ph = ",".join("?" * len(names))
    rows = [dict(r) for r in conn.execute(
        f"SELECT name, flag, continent, grade FROM countries WHERE name IN ({ph})",
        names).fetchall()]
    conn.close()
    for r in rows:
        r["ovr"] = _nat_team_ovr(r["grade"], r["name"], r["continent"], fast=True, year=year)
    target = REGION_TARGET_SIZE.get(my_region, len(rows))
    if len(rows) > target:
        rows.sort(key=lambda r: r["ovr"], reverse=True)
        rows = rows[:target]
    return rows



# ─────────────────────────────────────────────

def _continent_qual_quota(qual_kind, continent, year):
    """이 대륙(연맹)이 올해(=예선과 같은 해) 본선에서 차지하는 진출 쿼터(장수)."""
    if qual_kind == "wc_qual":
        big = year >= WC_EXPAND_YEAR
        quota_map = WC_QUOTA_BIG if big else WC_QUOTA
        ck = _conf_key(continent)
        return quota_map.get(ck, 4)
    else:
        return CONT_TEAMS


def _conf_countries(conf_key):
    """연맹 대표키 → 해당 연맹 전체 국가 목록 (DB 조회)."""
    confs = CONFEDERATIONS.get(conf_key, [conf_key])
    conn = get_conn()
    placeholders = ",".join("?" * len(confs))
    rows = [dict(r) for r in conn.execute(
        f"SELECT name, flag, continent, grade FROM countries WHERE continent IN ({placeholders})",
        confs).fetchall()]
    conn.close()
    return rows


def _enrich_countries(rows, year=None):
    """국가 목록에 ovr/qual 점수 추가."""
    import time
    _t0 = time.perf_counter()
    for r in rows:
        r["ovr"]  = _nat_team_ovr(r["grade"], r.get("name", ""), r.get("continent", ""), fast=True, year=year)
        r["qual"] = GRADE_QUAL_BASE.get(r["grade"], 0.2) + random.uniform(-QUAL_NOISE, QUAL_NOISE)
    if len(rows) >= 20:   # 소규모 호출까지 매번 찍으면 로그 스팸이라 큰 호출만
        print(f"[PERF] _enrich_countries {len(rows)}개국 OVR계산 {time.perf_counter()-_t0:.2f}s")
    return rows


def _sim_single_match_ai(home, away):
    """단판 AI vs AI 시뮬. 전력 기반 확률로 승패 결정. 승자 dict 반환."""
    h_str = home.get("ovr", 50); a_str = away.get("ovr", 50)
    total = h_str + a_str
    h_win_p = (h_str / total) * 0.6 + 0.2   # 홈 어드밴티지 포함
    r = random.random()
    return home if r < h_win_p else away


def _create_qual_tournament(year, qual_kind, continent, p, my_nats, nat_info, committed):
    """월드컵 예선(wc_qual) 및 유로 예선(cont_qual, 유럽 전용) 대회 생성.

    단계:
      18주차 — 1차 예선 (하위국 단판, DB에 qual_r1 스테이지로 저장)
      19~24주차 — 조별리그 (qual_group, 홈앤어웨이 6경기)
      25주차 — 플레이오프 (qual_po, 단판 / 32팀 체제만)

    내 국적이 이 연맹에 속하면 is_my=1, 아니면 0.
    통과국은 _finalize_qual이 qual_results에 저장.

    qual_kind="cont_qual"(2026-08 신설): 유로 전용 — EURO_QUAL 설정을
    쓴다. 다른 대륙(아시아/아메리카/아프리카)은 아직 이 경로를 안 타서
    (호출부에서 유럽만 넘김) 기존 "랜덤 노이즈 직행 선발" 그대로다.
    """
    from game_engine import add_log

    if qual_kind == "cont_qual":
        qual_cfg = EURO_QUAL.get(continent)
        # [2026-08 버그수정, 신민용 리포트: "2000년에 '유로 유럽 예선'이라고
        # 뜨는데 이 시기엔 유로가 아니라 유럽 네이션스컵 예선이다"] 대회명이
        # "{year} 유로 {continent} 예선" 형태였는데, cont_qual은 지금
        # 유럽 전용이라 continent가 항상 "유럽"이라서 "유로 유럽 예선"처럼
        # 어색하게 겹쳤다. 정식 명칭인 "유럽 네이션스컵 예선"으로 바꾸고,
        # 이미 "유럽"을 포함하므로 continent를 또 붙이지 않는다.
        _qual_full_name = f"{year} 유럽 네이션스컵 예선"
    else:
        big = year >= WC_EXPAND_YEAR
        qual_cfg = WC_QUAL_48.get(continent) if big else WC_QUAL_32.get(continent)
        _qual_full_name = f"{year} 월드컵 {continent} 예선"
    if not qual_cfg:
        return

    # 예선 사이클 리셋 (pledge 해제)
    if not committed:
        try:
            from game_engine import update_player as _upd0
            _upd0(qual_pledged_nat="")
        except Exception:
            pass

    # 연맹 전체 국가 조회
    all_rows = _enrich_countries(_conf_countries(continent), year=year)
    if len(all_rows) < 4:
        return

    # FIFA 랭크(ovr) 기준 정렬
    all_rows.sort(key=lambda r: r["ovr"], reverse=True)

    # ─── 1차 예선 컷오프: 실제 경기 없이 하위국 제외 ───
    # 하위국은 조별리그 진출 실패로 성적에만 기록 (대륙컵 예선 미참가 방식과 동일)
    cutoff_n  = qual_cfg["cutoff_bottom"]
    top_rows  = all_rows[: len(all_rows) - cutoff_n]   # 조별리그 진출
    cut_rows  = all_rows[len(all_rows) - cutoff_n:]    # 예선 진출 실패
    cut_names = {r["name"] for r in cut_rows}

    # 내 나라 컷오프 여부는 my_nats/committed 기준으로만 판정 (sel_cand 계산 전)
    my_cut_nat = ""
    check_nats = [committed] if committed else [n for n in my_nats if n]
    for cand in check_nats:
        if cand in cut_names:
            my_cut_nat = cand
            break

    pool = list(top_rows)
    random.shuffle(pool)

    # ─── 내 국적 처리 ───
    cont_set = set(CONFEDERATIONS.get(continent, [continent]))
    if committed:
        cand_nats = [committed] if nat_info.get(committed, {}).get("continent") in cont_set else []
    else:
        cand_nats = [n for n in my_nats
                     if n and nat_info.get(n, {}).get("continent") in cont_set]

    # [2026-08 버그수정, 신민용 리포트: "월드컵은 MIN_INTL_CALLUP_AGE(17세)에
    # 영향을 안 받는 것 같다"] 대륙컵(예선 없는 대회, 위쪽 cand_nats 분기 —
    # "16세에 대륙컵 발탁창이 뜨는데 이건 좀 비현실적이지 않나" 코멘트 참고)엔
    # 최소 나이 필터가 있는데, 월드컵/유로 "예선"을 만드는 이 함수에는 같은
    # 필터가 빠져 있었다. cand_nats가 대륙 소속 여부만으로 정해지다 보니,
    # 16세도 예선 발탁창(my_sel=3)이 그대로 떴다 — "수락"을 눌러도 이후
    # _check_selection(나이 체크 포함)에서 결국 미선발 처리되긴 하지만,
    # 애초에 나이 미달자에게 발탁창 자체가 뜨면 안 된다는 원칙(대륙컵과 동일)이
    # 여기만 깨져 있었다. 대륙컵과 동일하게 여기서도 후보 자체를 비워
    # my_sel이 아예 3(발탁창)이 아니라 2(대상 없음)로 떨어지게 한다.
    from constants import MIN_INTL_CALLUP_AGE
    if p.get("age", 0) < MIN_INTL_CALLUP_AGE:
        cand_nats = []

    # OVR 조건 통과 여부 (choose_national_team에서 미선발 처리에 사용)
    sel_cand = [n for n in cand_nats
                if _check_selection(p, nat_info.get(n, {}).get("grade", "F"),
                                     country=n,
                                     continent=nat_info.get(n, {}).get("continent", ""))]

    # ─── my_sel 결정 ───
    # [핵심 설계] 순서: 국적 선택 → 예선 진출 실패 → 예선 탈락 → 조별리그 탈락 → 토너먼트
    # 컷오프 걸린 국적도 선택창 먼저 띄우고, choose_national_team에서 결과 처리.
    if my_cut_nat:
        # 컷오프 걸린 국적이 있어도 선택창 제시 (committed면 자동처리)
        if committed:
            # 이미 고정된 나라가 컷오프 → 바로 예선 진출 실패 기록
            failed_nat = committed
            _save_trophy(year, failed_nat, _qual_full_name, "예선 진출 실패")
            try:
                conn_fc = get_conn()
                conn_fc.execute("""INSERT INTO intl_history(year, competition, team_name, result,
                                                            goals, assists, caps, rating)
                                   VALUES(?,?,?,?,?,?,?,?)""",
                                (year, _qual_full_name,
                                 failed_nat, "예선 진출 실패", 0, 0, 0, 0.0))
                conn_fc.commit(); conn_fc.close()
            except Exception:
                pass
            from game_engine import add_log
            add_log(f"❌ {failed_nat} {_qual_full_name} 진출 실패 (랭킹 하위권)", "event")
            my_sel = 2; my_nat = ""; cand_nats_final = []
        else:
            # 미고정: 컷오프 국적 포함해서 선택창 제시
            # choose_national_team에서 컷오프 여부 확인 후 "예선 진출 실패" 처리
            my_sel = 3; my_nat = ""; cand_nats_final = cand_nats
    elif committed:
        if committed in sel_cand:
            # [2026-08 신설, 신민용 요청: "22살 이후 국대 결정한 후엔 예선도
            # 자동으로 치뤄지는데, 이것도 발탁 거절 형태로 뜨게 해달라"]
            # 예전엔 committed(평생 대표국 확정) 이후로는 선발 재검증만 하고
            # my_sel=1로 바로 확정해버려서, 미고정 시절(선택창이 뜨던 시절)과
            # 달리 그 뒤로는 매 예선마다 소집을 거부할 기회가 영영 사라졌다
            # (실제 사례: 무소속 선수가 committed 이후 예선 전 경기를 전부
            # 부상-AI 대체로 자동 소화, 발탁 거절 로그가 한 줄도 안 남음).
            # 미고정 케이스(아래 else 분기, my_sel=3)와 동일한 발탁창
            # 경로로 보내되 cand_nats_final을 내 나라 하나만 담아 넘긴다 —
            # choose_national_team/decline_national_team이 후보 1개짜리도
            # 그대로 처리하므로 새 로직 없이 기존 발탁/거절 인프라를 그대로
            # 재사용한다. 거절 시 _save_decline이 trophy_log에 남기는
            # '발탁 거절' 기록도 미고정 시절과 동일하게 남는다.
            my_sel = 3; my_nat = ""; cand_nats_final = [committed]
        else:
            my_sel = 2; my_nat = ""; cand_nats_final = []
    else:
        # 미고정: 해당 연맹 소속 국적 있으면 선택창 (OVR 무관)
        if cand_nats:
            my_sel = 3; my_nat = ""; cand_nats_final = cand_nats
        else:
            my_sel = 2; my_nat = ""; cand_nats_final = []

    # ─── 조별리그 편성 ───
    n_groups   = qual_cfg["n_groups"]
    group_size = qual_cfg["group_size"]

    pool.sort(key=lambda r: r["ovr"], reverse=True)
    glabels = _qual_group_labels(n_groups)
    groups  = {g: [] for g in glabels}
    for pot in range(group_size):
        segment = pool[pot * n_groups:(pot + 1) * n_groups]
        random.shuffle(segment)
        for gi, e in enumerate(segment):
            if gi < n_groups:
                groups[glabels[gi]].append(e)

    # ─── DB 저장 ───
    name = _qual_full_name
    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO intl_tournaments(year, kind, name, status, my_selected, my_nat, cand_nats, continent)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (year, qual_kind, name, "qual_group", my_sel, my_nat,
               ",".join(cand_nats_final), continent))
    tid = c.lastrowid

    # entries 저장
    my_grp = None
    for g, members in groups.items():
        for e in members:
            is_my = 0
            if my_nat and e["name"] == my_nat:
                is_my = 1; my_grp = g
            elif my_sel == 3 and any(e["name"] == n for n in cand_nats_final):
                is_my = 1; my_grp = g
            c.execute("""INSERT INTO intl_entries
                         (tournament_id, country, flag, grade, ovr, grp, is_my, continent)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (tid, e["name"], e.get("flag",""), e.get("grade","F"),
                       round(e["ovr"], 1), g, is_my, e.get("continent","")))

    # 홈앤어웨이 6라운드 일정 생성 — [2026-07 재설계] 연말 오프시즌이 아니라
    # 중간 휴식기(비시즌, INTL_QUAL_START_DAY부터) 안에서 4일 간격으로 진행.
    # 라운드로빈 알고리즘: 1팀 고정 + 나머지 회전 → 정방향 3R + 역방향 3R

    def _round_robin_schedule(names):
        """n팀 라운드로빈. 각 라운드는 (홈, 원정) 쌍의 리스트."""
        t = list(names)
        if len(t) % 2 == 1:
            t.append(None)  # 홀수 더미
        nt = len(t)
        rounds = []
        for _ in range(nt - 1):
            pairs = []
            for i in range(nt // 2):
                h, a = t[i], t[nt - 1 - i]
                if h is not None and a is not None:
                    pairs.append((h, a))
            rounds.append(pairs)
            t = [t[0]] + [t[-1]] + t[1:-1]
        return rounds

    # [2026-07 단순화] 같은 라운드의 다른 조 경기는 서로 다른 나라라서
    # 같은 날짜에 겹쳐도 실제 충돌이 아니다(실제 FIFA 매치데이도 여러 조가
    # 동시 진행됨) — 그래서 week 버킷/capacity 분산 없이 라운드=day를
    # 직접 매핑한다. 라운드 수는 그룹 크기에 따라 달라질 수 있어
    # len(all_rounds) 기준으로 매번 계산한다(4팀 조면 6라운드).
    cand_set = set(cand_nats_final)
    for g, members in groups.items():
        names = [e["name"] for e in members]
        fwd_rounds = _round_robin_schedule(names)
        # 역방향: 홈↔원정 반전
        rev_rounds = [[(a, h) for h, a in rnd] for rnd in fwd_rounds]
        all_rounds = fwd_rounds + rev_rounds  # 4팀 조 기준 6라운드

        for rnd_idx, rnd_pairs in enumerate(all_rounds):
            day = INTL_QUAL_START_DAY + rnd_idx * INTL_QUAL_ROUND_GAP_DAYS
            wk = day_to_week(day)
            for home_nat, away_nat in rnd_pairs:
                is_my_match = (
                    (my_nat and (home_nat == my_nat or away_nat == my_nat)) or
                    (my_sel == 3 and (home_nat in cand_set or away_nat in cand_set))
                )
                c.execute("""INSERT INTO intl_matches
                             (tournament_id, week, day, stage, grp, home, away, is_my, my_played)
                             VALUES(?,?,?,?,?,?,?,?,?)""",
                          (tid, wk, day, "qual_group", g, home_nat, away_nat,
                           1 if is_my_match else 0, 0))

    conn.commit(); conn.close()

    # 플레이오프 주차(25주차) 레코드는 _finalize_qual이 조별 완료 시 생성
    # (qual_cfg["po_teams"] > 0 인 체제에서만)

    # 로그
    add_log(f"🌐 {name} 예선 조 추첨 완료 ({n_groups}개 조)", "event")
    if my_sel == 3:
        add_log(f"   발탁 후보: {', '.join(cand_nats_final)}", "event")
    elif my_sel == 1:
        add_log(f"   {my_nat} 대표로 출전 확정", "event")

    # 미선발 로그
    # [최적화] 예전엔 cand_nats 후보 수만큼 동일한 UPDATE를 반복 실행했다
    #  (n2를 쿼리에서 쓰지 않아 매번 같은 tid에 같은 값을 덮어씀 → 불필요한
    #   커밋 반복). 결과는 항상 같으므로 후보 수와 무관하게 1회만 실행한다.
    if my_sel == 2 and cand_nats:
        conn2 = get_conn()
        conn2.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?",
                      ("예선 미선발", tid))
        conn2.commit(); conn2.close()


def _qual_group_labels(n):
    """예선 조가 8개를 넘을 수 있으므로 A~Z, 그 이상은 A1,A2... 로 확장."""
    base = [chr(ord("A") + i) for i in range(26)]
    if n <= 26:
        return base[:n]
    labels = list(base)
    i = 0
    while len(labels) < n:
        labels.append(f"{base[i % 26]}{i // 26 + 1}")
        i += 1
    return labels[:n]




def process_intl_week(week, day=None):
    """이번 주차의 남은 국제대회 경기(AI) 시뮬 + 라운드 진행.
    [복수대륙컵] 그 해 열린 모든 대회를 각각 진행한다.

    [2026-07 버그수정, 신민용 리포트: "아시안컵 8강/4강이 메인화면에서
    사라지고 부상으로 결장 처리된다 — 강철체질이라 부상일 리 없다"]
    이 함수는 이제(4강/3-4위전이 늦게 뜨는 버그 수정으로) 매일 호출된다.
    day를 함께 넘기면 _process_one_tournament_week가 '오늘(day)까지'만
    쓸어담고, day가 아직 안 된 미래 경기(같은 주 안에 있어도)는 그날이
    와서 정상적으로(get_my_match→simulate_my_match) 처리될 때까지
    건드리지 않는다."""
    from game_engine import get_state
    st = get_state()
    if not st:
        return
    for t in get_tournaments(st["current_year"]):
        if t.get("status") == "done":
            continue
        _process_one_tournament_week(t, week, day=day)


def _process_one_tournament_week(t, week, day=None):
    """대회 1개의 이번 주차 경기 시뮬 + 라운드 진행.

    [2026-07 버그수정, DB로 실제 확인한 버그: "8강/4강 경기가 실제 날짜가
    되기도 전에 '부상 결장'으로 자동 처리돼버린다"] 국제대회는 Phase 2로
    한 주 안에도 서로 다른 day를 가진 경기가 여러 개 있을 수 있는데(예:
    조별리그 2라운드가 같은 주의 다른 날, 또는 다음 라운드가 이번 주 뒷날에
    이미 생성돼 있는 경우), 이 함수가 매일 호출되면서도 pending을 week
    기준으로만 걸러 골랐다 — 그러면 아직 그 경기의 실제 날짜(day)가 오지도
    않았는데 이번 주에 속한다는 이유만으로 오늘 즉시 AI(결장) 시뮬로
    넘어가버렸다(실제 세이브에서 재현: day329 8강전이 day327에 이미
    '부상으로 결장' 처리됨). day를 받으면 day<=오늘 인 경기만 쓸어담고,
    아직 안 된 day는 그대로 두어 실제 그날 advance_days의 정상 경로
    (get_my_match/simulate_my_match)가 처리하게 한다."""
    conn = get_conn()
    if day is not None:
        pending = [dict(r) for r in conn.execute(
            """SELECT * FROM intl_matches
               WHERE tournament_id=? AND home_score=-1 AND home!='' AND away!=''
                 AND ((day IS NOT NULL AND day<=?) OR (day IS NULL AND week<=?))
               ORDER BY id""",
            (t["id"], day, week)).fetchall()]
    else:
        pending = [dict(r) for r in conn.execute(
            """SELECT * FROM intl_matches
               WHERE tournament_id=? AND week<=? AND home_score=-1
                 AND home!='' AND away!='' ORDER BY id""",
            (t["id"], week)).fetchall()]
    conn.close()

    # [최적화] champions_engine/cup_engine과 동일한 패턴: 경기마다 커넥션을
    # 열고 commit/close 하던 것을, 한 커넥션·한 트랜잭션으로 일괄 처리.
    # 국제대회 주간에는 대회 여러 개(월드컵+대륙컵+예선 등)가 동시에 진행돼
    # pending이 수십~백 건 이상일 수 있어 개별 commit 누적 비용이 체감됨.
    # [2026-07 추가 최적화] 개별 execute()도 batch에 모아 executemany()로
    # 한 번에 반영 — champions_engine/cup_engine과 동일하게 "1주 진행"
    # 체감 지연을 더 줄인다.
    if pending:
        conn2 = get_conn()
        _batch = []
        for m in pending:
            _sim_ai_match(t, m, batch=_batch)
        if _batch:
            conn2.executemany(
                """UPDATE intl_matches SET home_score=?, away_score=?,
                   pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
                _batch)
        conn2.commit()
        conn2.close()

    # ── 예선 진행 ──
    # [2026-08 버그수정, 신민용 리포트: "유로 예선이 조별리그에서 영원히
    # 안 끝난다"] 이 블록이 kind=="wc_qual"만 통과시켜서, 유로 예선
    # (kind='cont_qual')은 매주 이 처리 자체를 건너뛰고 있었다 — 조별리그
    # 경기는 생성됐지만 마감 판정(_finalize_qual 호출)이 한 번도 안 불려서
    # status가 'qual_group'에 영원히 머물렀다.
    if t["kind"] in ("wc_qual", "cont_qual"):
        # [버그수정] t는 루프 시작 시의 스냅샷이므로 status를 DB에서 재조회한다.
        # _finalize_qual 내부에서 status='qual_po' or 'done'으로 갱신되기 때문에
        # 스냅샷 값으로 판정하면 PO 체제에서 다음 주차 호출이 누락될 수 있다.
        conn2 = get_conn()
        cur_row = conn2.execute(
            "SELECT status FROM intl_tournaments WHERE id=?", (t["id"],)).fetchone()
        conn2.close()
        status = cur_row["status"] if cur_row else t.get("status", "qual_group")

        # 조별리그: 완료 주차(그룹 구간 마지막 주)에 마감
        # [2026-07 수정] 예선이 이제 중간 휴식기(INTL_QUAL_START_DAY)
        # 기준이라, 완료 판정도 그 앵커로 계산해야 한다 — 옛 오프시즌
        # 앵커(INTL_GROUP_WEEKS)를 그대로 쓰면 이 조건이 영영 안 걸려서
        # 예선이 조별리그에서 멈춰버린다(실제로 이 버그로 재현됨).
        qual_group_last_day = INTL_QUAL_START_DAY + 5 * INTL_QUAL_ROUND_GAP_DAYS
        qual_po_day = INTL_QUAL_START_DAY + 6 * INTL_QUAL_ROUND_GAP_DAYS
        # [2026-07 버그수정, 신민용 리포트: "플레이오프가 31주차에 열린다면서
        # 대진표에 팀만 뜨고 스코어가 영원히 안 나온다"] 이 마감 판정이
        # week 단위였다 — PO 경기 실제 day(214)가 그 주(31주차=211~217일)
        # 중간인데, week>=31이 그 주 첫날(211일)부터 이미 참이 돼서 실제
        # 경기 날짜가 오기도 전에 _finalize_qual_po가 불려버렸다.
        # _finalize_qual_po는 그 시점에 경기가 아직 미시뮬 상태(-1,-1)면
        # intl_matches 행은 그대로 둔 채 별도 코인플립(_sim_single_match_ai)
        # 으로만 승자를 정해버려서, 대회 결과(예선 통과)는 확정되는데 정작
        # 화면에 보이는 그 경기 박스는 스코어 없이 영원히 빈 채로 남았다.
        # day가 있으면 day 기준으로, 없으면(구버전 호출 등) week로 폴백한다.
        if status == "qual_group":
            ready = (day >= qual_group_last_day) if day is not None \
                    else (week >= day_to_week(qual_group_last_day))
            if ready:
                _finalize_qual(t)   # 직행 확정 or qual_po 생성
        # 플레이오프: 완료 시 마감
        elif status == "qual_po":
            ready = (day >= qual_po_day) if day is not None \
                    else (week >= day_to_week(qual_po_day))
            if ready:
                _finalize_qual_po(t)  # 플레이오프 승자 → qual_results
        return

    # 라운드 진행 — [2026-07 재설계, DB로 확인한 실제 버그: "예선은 끝났는데
    # 16강을 안 한다"] 예전엔 "각 단계는 정확히 1주씩 걸린다"고 가정한 week
    # 기반 plan 딕셔너리(last_group_week에 +1씩)로 다음 단계 트리거 시점을
    # 판정했다. 그런데 압축된 day 기반 일정(daily_match_capacity) 때문에
    # 한 단계가 여러 주에 걸치거나(예: 8강이 47~48주 두 주에 걸침), 조별
    # 리그 종료 주(day로 역산하면 실제로는 46주)가 하드코딩된
    # INTL_GROUP_WEEKS[1](47)와 안 맞는 경우가 생겼다 — 이러면 트리거
    # 주차가 아예 안 걸리거나 한 단계씩 밀려서 다음 라운드가 영원히
    # 안 생긴다. week 대신 stage 이름으로 "지금 어느 단계가 진행 중이고
    # 끝났는지"를 직접 판정하면 며칠/몇 주에 걸치든 항상 정확하다.
    big = (t["year"] >= WC_EXPAND_YEAR) if t["kind"] == "world" else False
    if t["kind"] == "world":
        _ko_seq = ["R32", "R16", "QF", "SF", "F"] if big else ["R16", "QF", "SF", "F"]
    elif t["kind"] == "region":
        # [2026-08 신설] 지역컵은 규모(브래킷 4/8/16강)가 지역마다 달라서,
        # 대회별 실제 참가국 수로 다시 계산해 그 브래킷 크기에 맞는
        # 지점부터 KO 시퀀스를 시작한다 — 조 편성 때 쓴 것과 같은
        # regional_cup_format()이라 값이 항상 일치.
        from constants import regional_cup_format
        _conn_rc = get_conn()
        _n_entries = _conn_rc.execute(
            "SELECT COUNT(*) n FROM intl_entries WHERE tournament_id=?", (t["id"],)).fetchone()["n"]
        _conn_rc.close()
        _bracket = regional_cup_format(_n_entries)["bracket_size"]
        _full_seq = ["R16", "QF", "SF", "F"]
        _start_idx = {16: 0, 8: 1, 4: 2, 2: 3}.get(_bracket, 1)
        _ko_seq = _full_seq[_start_idx:]
    else:
        _ko_seq = ["R16", "QF", "SF", "F"]

    conn_s = get_conn()
    if t["status"] == "group":
        group_pending = conn_s.execute(
            "SELECT COUNT(*) n FROM intl_matches WHERE tournament_id=? AND stage='group' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn_s.close()
        if group_pending == 0:
            _finalize_groups(t, _ko_seq[0], week)
        return

    if t["status"] != "ko":
        conn_s.close()
        return

    # [2026-07 재설계, KO 셸 사전생성에 맞춰 재작성] 예전엔 "stage!='group'
    # 인 미완료 행이 하나라도 있으면 이번 주차엔 할 게 없다"고 판단했는데,
    # 이제 R16~결승/3-4위전 전 스테이지가 대회 시작 시점에 이미
    # placeholder(home='', away='')로 전부 존재한다 — 그러면 이 조건이
    # 대회 내내(마지막 스테이지가 끝나기 전까지) 항상 참이 되어 다음
    # 라운드가 영원히 대진 확정이 안 되는 회귀가 생긴다. 이제 각 스테이지를
    # "채워졌는지(home!='')"와 "다 끝났는지(home_score!=-1)"로 직접 걸어서
    # 확인한다 — _ko_seq 순서대로 훑으며, 채워지고 끝난 스테이지인데 바로
    # 다음 스테이지가 아직 채워지지 않았으면 그 전환을 진행한다.
    def _stage_rows(stage):
        return conn_s.execute(
            "SELECT home, away, home_score FROM intl_matches WHERE tournament_id=? AND stage=?",
            (t["id"], stage)).fetchall()

    for i, cur_stage in enumerate(_ko_seq[:-1]):
        rows = _stage_rows(cur_stage)
        if not rows:
            continue
        if any(r["home"] == "" for r in rows):
            # 이 스테이지가 아직 채워지지 않았다 = 그 이전 단계가 아직
            # 안 끝났다는 뜻(정상 — 순서대로면 여기서 멈춰야 함).
            conn_s.close()
            return
        if any(r["home_score"] == -1 for r in rows):
            # 채워졌지만 아직 진행 중.
            conn_s.close()
            return
        next_stage = _ko_seq[i + 1]
        next_rows = _stage_rows(next_stage)
        if next_rows and all(r["home"] == "" for r in next_rows):
            conn_s.close()
            _advance_knockout(t, cur_stage, next_stage, week)
            return
        # next_stage가 이미 채워졌으면(직전 호출에서 처리됨) 계속 다음으로.

    # 여기 도달 = 마지막 KO 스테이지(F, 그리고 있다면 TP)까지 전부 채워짐.
    # F/TP 완료 여부를 확인해 대회 종료를 트리거한다.
    f_rows = _stage_rows("F")
    tp_rows = _stage_rows("TP")
    conn_s.close()
    f_done = bool(f_rows) and all(r["home"] != "" and r["home_score"] != -1 for r in f_rows)
    tp_done = (not tp_rows) or all(r["home"] != "" and r["home_score"] != -1 for r in tp_rows)
    if f_done and tp_done:
        _finish_tournament(t, week)


# ─────────────────────────────────────────────
# 경기 시뮬 (AI)
# ─────────────────────────────────────────────

def _entry(tid, country):
    key = (tid, country)
    cached = _entry_cache.get(key)
    if cached is not None:
        return cached
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=? AND country=?",
        (tid, country)).fetchone()
    conn.close()
    val = dict(row) if row else {"ovr": 50, "flag": "", "grade": "F"}
    _entry_cache[key] = val
    return val


def _match_outcome(h_ovr, a_ovr, knockout):
    """중립 구장 가정. 'home'/'draw'/'away' 반환 (KO는 무승부 → 승부차기).
    [수정] 무승부 확률을 전력차에 반비례하도록 개선 (기존 dw=0.22 고정 →
    전력차 무관하게 항상 22% 무승부였음. 국내리그 _match_win_probs와 같은 취지).

    [2026-07 재조정, 신민용 지적: 대한민국(A급, OVR≈84) vs 필리핀(E급,
    OVR≈51)처럼 33점 차이나는 예선에서도 필리핀 승률이 7%나 나와 이변이
    너무 잦았다] game_engine._match_win_probs와 동일한 취지로 diff 반영폭을
    올렸다 — diff=0(균형)은 기존과 동일하게 유지, 격차가 클수록(diff 20~
    이상) 훨씬 더 확실하게 강팀 쪽으로 쏠리도록 기울기만 가파르게 했다.
    """
    diff = h_ovr - a_ovr
    hw = max(0.04, min(0.95, 0.46 + diff * 0.022))
    dw = max(0.05, 0.24 - abs(diff) * 0.009)
    aw = max(0.02, 1.0 - hw - dw)
    tot = hw + dw + aw
    hw, dw, aw = hw / tot, dw / tot, aw / tot
    roll = random.random()
    if roll < hw:
        return "home"
    elif roll < hw + dw:
        return "draw"
    return "away"


def _gen_intl_score(outcome, diff=0.0):
    from game_engine import _gen_score
    return _gen_score(outcome, diff)


def _resolve_pso(h_ovr, a_ovr):
    """승부차기: 전력이 살짝 유리하게."""
    p_home = 0.5 + max(-0.1, min(0.1, (h_ovr - a_ovr) * 0.006))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


def _sim_ai_match(t, m, my_played=False, conn=None, reason="injury", batch=None):
    """AI끼리(또는 내가 결장한 내 경기) 시뮬.

    conn: 외부에서 연 커넥션을 재사용해 다수 경기를 한 트랜잭션으로 묶는다
          (champions_engine._sim_ai_match와 동일한 패턴).
          None이면 자체 커넥션을 열고 commit/close(기존 동작 = 하위 호환).
    reason: 내 경기인데 결장한 사유 — 'injury'(부상)/'suspension'(출전정지) 등.
    batch: [2026-07 성능 최적화] 리스트를 넘기면 UPDATE를 즉시 실행하지 않고
           이 리스트에 튜플만 쌓아둔다 — 호출부(_process_one_tournament_week)가
           그 주 모든 국제대회 경기를 다 모은 뒤 executemany()로 한 번에
           반영한다(월드컵/대륙컵은 한 라운드에 수십~수백 개국 경기가 동시에
           돈다 — "1주 진행" 체감 지연을 줄인다).
    """
    from game_engine import add_log, get_player, _week_intl_cl_day
    he = _entry(t["id"], m["home"])
    ae = _entry(t["id"], m["away"])
    knockout = m["stage"] not in ("group", "qual_group")  # [버그수정] 예선 조별도 무승부 허용

    outcome = _match_outcome(he["ovr"], ae["ovr"], knockout)
    pso_winner, pso_score = "", ""
    if knockout and outcome == "draw":
        win_home, pso_score = _resolve_pso(he["ovr"], ae["ovr"])
        pso_winner = m["home"] if win_home else m["away"]
    # [버그수정 2026-07] diff 누락 — 예전엔 항상 diff=0(박빙 취급)이라 강팀이
    # 약팀을 만나도 승패(outcome)만 전력차를 반영하고 스코어차는 전력차와
    # 무관하게 항상 근소하게(최대 4골차)만 나왔다. game_engine/champions_engine/
    # cup_engine은 이미 diff를 넘기고 있었는데 국제대회 조별/예선 AI 매치만
    # 빠져 있었음.
    hs, as_ = _gen_intl_score(outcome, he["ovr"] - ae["ovr"])

    # [2026-07 신설] 이 경기가 실제로 진행되는 날짜를 지금 시점 기준으로
    # 한 번 계산해 저장한다 — 나중에 커리어/은퇴창에서 재계산 없이 그대로 쓴다.
    # [2026-07 성능 수정] cup_engine/champions_engine과 동일한 이유로,
    # my_played=1(내 경기)로 조회될 때만 의미가 있으므로 AI끼리 경기에서는
    # 계산을 건너뛴다(월드컵/네이션스컵은 한 라운드에 수십~수백 개국 경기가
    # 동시에 도는데, 그때마다 get_player() DB 조회를 하던 낭비를 없앤다).
    # [2026-07 버그수정] m["is_my"] 하나만 보고 "내 경기"로 취급하면, 복수
    # 국적으로 후보에 올랐다가 다른 나라가 선택돼 이 대회는 my_selected=2로
    # 닫혔는데도(선택 시점에 is_my가 항상 정리되도록 위쪽도 고쳤지만, 이미
    # 오염된 기존 세이브에는 여전히 is_my=1이 남아있을 수 있다) 선택하지
    # 않은 나라의 경기가 커리어 로그에 같이 찍히는 사고가 난다. 실제로
    # "내 경기"인지는 is_my 플래그와 함께 그 대회가 현재 my_selected==1
    # (정식 선택·출전 확정) 상태인지도 같이 봐야 한다.
    _really_mine = bool(m["is_my"]) and t.get("my_selected") == 1
    # [2026-07 재수정, 국제대회 일 단위 전환 Phase 2 버그 수정] 이전엔
    # "내 경기가 아니면 day=None"으로 무조건 덮어썼는데, 이제 예선/본선
    # 생성 시점에 모든 경기(AI 포함)에 이미 정확한 day가 채워져 있다
    # (Phase 2 참고) — 여기서 무조건 None을 넣으면 그 값을 시뮬레이션
    # 순간 지워버리는 회귀 버그가 된다(실제로 재현됨: 생성 직후엔 day가
    # 있는데 process_intl_week가 한 번 돌면 싹 NULL로 바뀜).
    #
    # [2026-07 재재수정, DB로 실제 확인한 버그] 바로 위 수정("내 경기만
    # _week_intl_cl_day로 재계산")이 또 다른 회귀였다 — Phase 2가 생성
    # 시점에 이미 capacity 기반으로 정확히 배정해둔 day(예: round2라서
    # 315일)를, "내 경기"라는 이유로 _week_intl_cl_day가 그 주(week) 안의
    # 아무 날로나(예: 318일, 사실은 round3 구간) 다시 계산해서 덮어썼다.
    # 그 결과 week(46, round2)와 day(318, 실은 round3 날짜)가 서로 안
    # 맞는 행이 생겼고, 화면에서 "오늘(day) 경기 있냐"고 물어봐도
    # 못 찾았다(실제 세이브에서 재현·확인됨: round2가 313~316일에 4개씩
    # 깔끔하게 나뉘어야 하는데 내 경기 하나만 318일로 튀어서 315일 자리가
    # 비었었다). _week_intl_cl_day는 day가 아예 없던 옛 구조(챔스/컵/CWC)를
    # 위한 폴백이지, 이미 정확한 day가 있는 예선/본선엔 애초에 쓸 이유가
    # 없었다 — "내 경기"든 아니든 이제 항상 저장된 day를 그대로 보존한다.
    day = m.get("day")

    _absence = reason if _really_mine else None
    _row = (hs, as_, pso_winner, pso_score, day, _absence, m["id"])
    if batch is not None:
        batch.append(_row)
    else:
        _own = conn is None
        if _own:
            conn = get_conn()
        conn.execute("""UPDATE intl_matches SET home_score=?, away_score=?,
                        pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
                     _row)
        if _own:
            conn.commit()
            conn.close()

    # 내 국가 경기(결장 포함)는 로그 출력. AI끼리 경기는 get_player() 불필요.
    if _really_mine:
        p = get_player()
        nat = _my_nat(t, p)
        if nat in (m["home"], m["away"]):
            stage_ko = STAGE_KO.get(m["stage"], "")
            pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
            add_log(f"🌍 {t['name']} {stage_ko}  "
                    f"{he['flag']}{m['home']} {hs}-{as_} {ae['flag']}{m['away']}{pso_txt}", "match")
            if t["my_selected"] == 1 and not my_played:
                _reason_ko = {"injury": "부상", "suspension": "출전정지", "bench": "벤치"}.get(reason, reason)
                add_log(f"   🚑 {_reason_ko}(으)로 대표팀 경기 결장", "match")


def _winner_of(m):
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home"] if m["home_score"] > m["away_score"] else m["away"]


# ─────────────────────────────────────────────
# 내 경기 시뮬
# ─────────────────────────────────────────────

def sim_my_match_as_ai(week, p, reason="injury", day=None):
    """[2026-07 신설, 버그수정] 부상 등으로 내가 못 뛸 때 내 국가대표 경기를
    AI끼리 시뮬레이션 — cup_engine.sim_my_cup_match_as_ai와 동일한 이유로
    신설(이게 없으면 그 경기가 영원히 미완료로 남아 대회 진행이 멈춘다)."""
    info = get_my_match(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM intl_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM intl_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()
    if m["home_score"] != -1:
        return  # 이미 처리됨(멱등)
    _sim_ai_match(t, m, my_played=False, reason=reason)


def simulate_my_match(week, p, day=None):
    """내가 출전하는 국가대표 경기."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _roll_red_card, _apply_red_card_dismissal,
                             _roll_card_events)
    info = get_my_match(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM intl_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM intl_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    nat = _my_nat(t, p)
    # 예선(wc_qual)은 cap-tie 대상이 아니다 → 국적 고정 안 함.
    _is_qual = t.get("kind") in ("wc_qual", "cont_qual")
    # [복수국적] A매치 첫 출전 → 그 나라로 영구 고정(cap-tie). (본선만)
    # 이후 대회부터는 이 나라로만 차출된다.
    if (not _is_qual) and nat and not (p.get("intl_committed", "") or ""):
        from game_engine import update_player as _upd
        _upd(intl_committed=nat)
        # [국적 연혁] A매치 첫 출전으로 자동 고정된 경우도 commit 기록
        try:
            from game_engine import add_nat_history, get_state
            _st = get_state() or {}
            _fl = ""
            for _nk, _fk in (("nationality","flag"),("nationality2","flag2"),("nationality3","flag3")):
                if (p.get(_nk,"") or "") == nat:
                    _fl = p.get(_fk,"") or ""; break
            add_nat_history("commit", nat, _fl,
                            _st.get("current_year"), _st.get("current_week"))
        except Exception:
            pass
    he = _entry(t["id"], m["home"])
    ae = _entry(t["id"], m["away"])
    is_home = info["is_home"]
    knockout = m["stage"] not in ("group", "qual_group")  # [버그수정] 예선 조별도 무승부 허용

    # [2026-08 신설, 옐로카드 시스템] 월드컵 예선(wc_qual)은 본선과 카드
    # 누적 그룹을 분리한다 — 예선 마지막 경기에서 받은 퇴장/정지가 본선
    # 개막전 결장으로 이어지던 버그 수정. 그 외(cont_qual/본선/유로/AFCON/
    # 아시안컵/지역컵 등)는 지금 단계에서 전부 intl_suspension 하나로
    # 유지(신민용+GPT 협의 확정 — 국가대표 대회가 클럽만큼 빈번하지 않아
    # 더 세분화할 실익이 작음).
    _susp_field = "wc_qual_suspension" if t.get("kind") == "wc_qual" else "intl_suspension"

    # [2026-07 신설] 출전정지 체크 — 퇴장 다음 경기는 강제 결장.
    _suspended, _new_susp = _check_suspended(p, field=_susp_field)
    if _suspended:
        update_player(**{_susp_field: _new_susp})
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event")

    # 내 출전 보너스 (격차 기반 에이스 영향력)
    # [2026-07 통일] 리그(game_engine._simulate_match)와 동일한 볼록가속+
    # 소프트캡 공식으로 교체 — 예전 선형+하드컷(14.0)보다 월드클래스급
    # 선수의 캐리력이 정확히 반영된다.
    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = max(0.0, _my_ovr - _team_ovr)
    _star = 1.0 + max(0.0, (_my_ovr - 60) / 40.0) ** 1.8 * 3.0
    bonus = _gap * 0.30 * _star + max(0.0, _my_ovr - 50) * 0.08
    bonus = _soft_cap(bonus, 30.0)
    # [2026-07 신설] '리더십' 성격의 team_win_bonus 연결 (정의만 돼있고
    # 실제 경기엔 미연결 상태였음) — 캐리 보너스에 작은 배율만 얹는다.
    from constants import PERSONALITY_EFFECTS
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if "team_win_bonus" in _pe:
        bonus *= (1.0 + _pe["team_win_bonus"])
    if _suspended:
        bonus = 0.0
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = _match_outcome(h_ovr, a_ovr, knockout)
    pso_winner, pso_score = "", ""
    if knockout and outcome == "draw":
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home"] if win_home else m["away"]
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    if _suspended:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
        _absence_reason = "suspension"
        _yellow_cnt = 0
    else:
        # [수정] 국제대회 개인 경기력은 '상대 국가대표 평균 OVR'을 dom 기준으로
        # 삼는다. 내가 홈이면 상대는 ae(원정), 원정이면 he(홈). 강팀 상대면
        # 개인도 고전, 약체국 상대면 골·평점 폭발 — 클럽 리그 기준이 아니라
        # 상대 국가 강함 반영.
        _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr)
        _absence_reason = None
        _yellow_cnt = 0
        # [2026-07 신설 → 2026-08 확장(옐로카드)] 카드 판정.
        _dismissed, _card_reason, _yellow_ev, _yellow_cnt = _roll_card_events(p, _susp_field)
        if _dismissed:
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(
                p, field=_susp_field, reason=_card_reason)
            _absence_reason = _card_reason
        elif _yellow_ev:
            events = list(events) + _yellow_ev
    # [2026-07 신설] '소심함' 성격의 big_match_rating 연결 — 국가대표 경기는
    # 전부 빅매치 성격이라(챔스와 동일 기준) 모든 경기에 적용한다.
    if not _suspended and "big_match_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["big_match_rating"], 1)))
    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    # [2026-07 재수정] 실제 진행 날짜는 Phase 2가 생성 시점에 이미
    # 정확히 배정해뒀다 — _week_intl_cl_day로 다시 계산하면 그 값을
    # (week는 그대로 둔 채) 엉뚱한 날로 덮어써서 week/day 불일치가
    # 생긴다(_sim_ai_match에서 실제로 재현·수정된 것과 동일한 버그).
    day = m.get("day")

    conn = get_conn()
    conn.execute("""UPDATE intl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?,
                    my_played=?, my_nat=?, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?,
                    day=?, my_absence_reason=?, my_yellow_cards=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score,
                  0 if _suspended else 1, nat, _get_field_pos(p),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  my_conceded, day, _absence_reason, _yellow_cnt, m["id"]))
    conn.commit()
    conn.close()

    # 국가대표 개인 기록 (클럽 시즌 통계와 분리)
    #  [cap-tie] 본선 무대를 밟으면 그 나라로 영구 고정. 단 예선은 고정 안 함.
    if _is_qual:
        # 예선: caps/goals/assists 누적만, capped/committed 미설정 (국적 변경 자유)
        update_player(
            intl_caps=p.get("intl_caps", 0) + 1,
            intl_goals=p.get("intl_goals", 0) + goals,
            intl_assists=p.get("intl_assists", 0) + assists,
        )
    else:
        update_player(
            intl_caps=p.get("intl_caps", 0) + 1,
            intl_goals=p.get("intl_goals", 0) + goals,
            intl_assists=p.get("intl_assists", 0) + assists,
            intl_capped=1,
            intl_committed=(p.get("intl_committed", "") or nat),
            qual_pledged_nat="",   # 본선 출전으로 영구고정됐으니 pledge 정리
        )
    # [세부 지표] 통산(total_*)에도 누적 → 커리어 통합 통계에 A매치 반영
    p2 = get_player()
    update_player(
        total_shots=p2.get("total_shots", 0) + detail["shots"],
        total_shots_on=p2.get("total_shots_on", 0) + detail["shots_on"],
        total_key_passes=p2.get("total_key_passes", 0) + detail["key_passes"],
        total_dribbles=p2.get("total_dribbles", 0) + detail["dribbles"],
        total_blocks=p2.get("total_blocks", 0) + detail["blocks"],
    )

    # 인기/스트레스/행복
    p2 = get_player()
    _update_pop(p2, goals, assists, rating)
    p2 = get_player()
    # [2026-07 조정, 신민용 지적: "경기 스트레스가 고강도 훈련만큼은 돼야
    # 하지 않나"] 리그/컵/챔스와 동일 원칙으로 상향.
    ns = min(100, p2["stress"] + 20)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    # ── 로그 ──
    stage_ko = STAGE_KO.get(m["stage"], "")
    grp_txt = f" {m['grp']}조" if m["stage"] == "group" else ""
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == nat else '패'})"
        rs = "무"
    comp_name = f"{t['name']} {stage_ko}{grp_txt}".strip()
    home_disp = f"{he['flag']}{m['home']}"
    away_disp = f"{ae['flag']}{m['away']}"
    pso = {"won": pso_winner == nat, "score": pso_score} if pso_winner else None
    detail_id = _save_match_detail(
        p, week, comp_name, is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, True, False, detail, pso=pso)
    marker = f" [match:{detail_id}]" if detail_id else ""

    add_log("─" * 44, "sep")
    # [2026-07 신설, 신민용 요청: "48주차가 아니라 11월 27일처럼 실제
    # 날짜로 떠야 한다"] 이제 국제대회도 day 기반이라 실제 날짜를 알 수
    # 있는데 로그엔 계속 '주차'만 찍히고 있었다 — day가 있으면 실제
    # 날짜로, 없으면(구버전 호출 등) 기존처럼 '주차'로 폴백한다.
    if day is not None:
        from constants import day_to_date_str
        when_txt = day_to_date_str(day)
    else:
        when_txt = f"{week}주차"
    add_log(f"🌍 {comp_name}  {when_txt}{marker}", "match")
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
# 조별리그 마감 / 토너먼트 진행
# ─────────────────────────────────────────────

def _qual_group_standings(tid, grp):
    """예선 조 순위 (stage='qual_group' 기준)."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=? AND grp=?",
        (tid, grp)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND grp=?
           AND stage='qual_group' AND home_score>=0""", (tid, grp)).fetchall()]
    conn.close()

    tbl = {e["country"]: {"country": e["country"], "flag": e["flag"], "ovr": e["ovr"],
                          "grade": e["grade"], "p": 0, "w": 0, "d": 0, "l": 0,
                          "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    for m in matches:
        h, a = tbl.get(m["home"]), tbl.get(m["away"])
        if not h or not a:
            continue
        hs, as_ = m["home_score"], m["away_score"]
        h["p"] += 1; a["p"] += 1
        h["gf"] += hs; h["ga"] += as_
        a["gf"] += as_; a["ga"] += hs
        if hs > as_:
            h["pts"] += 3; h["w"] += 1; a["l"] += 1
        elif hs < as_:
            a["pts"] += 3; a["w"] += 1; h["l"] += 1
        else:
            h["pts"] += 1; a["pts"] += 1; h["d"] += 1; a["d"] += 1
    rows = list(tbl.values())
    rows.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]), reverse=True)
    return rows


def get_qual_advance_status(t):
    """예선(wc_qual/cont_qual) 각 국가의 본선 진출 상태를 계산해 반환.

    [2026-08 신설, 신민용 리포트: "국제대회(예선) 탭에서 실제로 본선에
    올라가는 팀들이 초록색으로 안 뜨고 조 1위만 뜬다"] UI(schedule_window)
    쪽의 기존 색상 결정(_intl_advance_count)은 예선 대회 설정을 전혀 보지
    않고 무조건 "조 1위만 직행, 나머지는 전부 탈락(회색)"으로 그려왔다.
    실제로는 대륙/체제에 따라:
      - 유로 예선: 조 1·2위 전원 직행(2위도 초록이어야 함)
      - 월드컵 아메리카(48팀): 조 1위 직행 + 조 2위 중 성적 상위 N팀도
        직행(와일드카드)
      - 월드컵 유럽/48팀 체제 아프리카: 조 1위 직행 + 조 2위 중 성적
        상위 N팀은 플레이오프(단판)로 나머지 자리를 놓고 경쟁
    이 함수가 _finalize_qual과 동일한 설정표(WC_QUAL_32/48, EURO_QUAL)를
    읽어 실제 진출 로직을 그대로 재현한다. 조별리그가 아직 진행 중이면
    "지금까지의 성적 기준" 잠정 계산이며(다른 조별리그 UI와 동일한 방식),
    조별리그가 끝난 뒤에는 실제 확정 결과와 일치한다.

    반환: {country: status}
      'direct'     — 직행권 확보 (조 1위 직행 + 와일드카드로 직행 확정된 2위)
      'po_bubble'  — 플레이오프 경쟁 중(아직 결과 미확정, 조별리그 진행
                     중이면 "현재 기준 진출권" 후보)
      'po_ok'      — 플레이오프 승리로 진출 확정
      'eliminated' — 탈락 / 진출 가능성 없음
    """
    from constants import WC_QUAL_32, WC_QUAL_48, EURO_QUAL, WC_EXPAND_YEAR

    tid = t["id"]
    conn = get_conn()
    grps = [r["grp"] for r in conn.execute(
        "SELECT DISTINCT grp FROM intl_entries WHERE tournament_id=? ORDER BY grp", (tid,)).fetchall()]
    all_countries = {r["country"] for r in conn.execute(
        "SELECT country FROM intl_entries WHERE tournament_id=?", (tid,)).fetchall()}
    po_matches = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_matches WHERE tournament_id=? AND stage='qual_po'", (tid,)).fetchall()]
    conn.close()

    status = {c: "eliminated" for c in all_countries}
    if not grps:
        return status

    continent = _conf_key((t.get("continent") or "").strip() or "유럽")
    if t.get("kind") == "cont_qual":
        qual_cfg = EURO_QUAL.get(continent, {})
    else:
        big = t.get("year", 0) >= WC_EXPAND_YEAR
        qual_cfg = (WC_QUAL_48 if big else WC_QUAL_32).get(continent, {})

    direct_n = qual_cfg.get("direct", len(grps))
    po_teams = qual_cfg.get("po_teams", 0)
    wildcard = qual_cfg.get("wildcard", 0)

    winners, runners = [], []
    for g in grps:
        rows = _qual_group_standings(tid, g)
        if len(rows) >= 1:
            winners.append(rows[0])
        if len(rows) >= 2:
            runners.append(rows[1])

    # 조 라벨 순서가 아니라 성적순으로 상위 direct_n팀만 직행시킨다
    # (_finalize_qual과 동일한 기준, 위 버그수정 참고).
    if direct_n < len(winners):
        winners = sorted(winners, key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]),
                          reverse=True)
    direct_set = {w["country"] for w in winners[:direct_n]}

    runners_sorted = sorted(runners, key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]),
                             reverse=True)
    # [2026-08 버그수정, 신민용 리포트: "아프리카는 1등팀들끼리 플레이오프
    # 하는 거 아니야?"] direct=0인 체제(32팀 예선의 아시아/아프리카)는
    # _finalize_qual에서 실제로 "조 1위 전원이 직행 없이 플레이오프로
    # 간다"(po_pool = winners[:po_teams])로 처리하는데, 여기서는 그 분기를
    # 빼먹고 항상 조 2위(runners_sorted)를 플레이오프 후보로 잘못
    # 계산했다 — 그 결과 실제 플레이오프에 진출한 조 1위팀들은 화면에
    # 회색(탈락)으로, 정작 플레이오프와 무관한 조 2위팀들이 주황(경쟁
    # 중)으로 뜨는 정반대 표시가 났다. _finalize_qual과 동일하게
    # direct_n==0이면 조 1위(winners)를 플레이오프 후보로 삼는다.
    if wildcard > 0:
        direct_set |= {r["country"] for r in runners_sorted[:wildcard]}
        po_pool = runners_sorted[wildcard:wildcard + po_teams]
    elif direct_n == 0 and po_teams > 0:
        po_pool = winners[:po_teams]
    else:
        po_pool = runners_sorted[:po_teams]

    for c in direct_set:
        if c in status:
            status[c] = "direct"
    for r in po_pool:
        if r["country"] in status:
            status[r["country"]] = "po_bubble"

    # 플레이오프 경기 결과 반영 (경기가 끝난 만큼만 확정 상태로 갱신)
    for m in po_matches:
        h, a = m["home"], m["away"]
        hs = m.get("home_score", -1)
        if hs is None or hs < 0:
            continue
        as_ = m["away_score"]
        if hs > as_:
            winner, loser = h, a
        elif as_ > hs:
            winner, loser = a, h
        elif m.get("pso_winner"):
            winner = m["pso_winner"]
            loser = a if winner == h else h
        else:
            continue
        if winner in status:
            status[winner] = "po_ok"
        if loser in status:
            status[loser] = "eliminated"

    return status


def _finalize_qual(t):
    """예선 조별 종료 → 플레이오프 생성 or 통과국 확정.

    [32팀 체제] po_teams>0 이면 25주차 플레이오프 경기를 생성하고 status='qual_po'로 전환.
                플레이오프 완료 후 _finalize_qual_po()가 최종 진출국 확정.
    [48팀 체제] 조 1위(+와일드카드) 직행 → 즉시 qual_results 저장.
    """
    from game_engine import add_log, get_player
    from constants import WC_QUAL_32, WC_QUAL_48

    tid = t["id"]
    conn = get_conn()
    grps = [r["grp"] for r in conn.execute(
        "SELECT DISTINCT grp FROM intl_entries WHERE tournament_id=? ORDER BY grp", (tid,)).fetchall()]
    # [2026-07 최적화, 신민용 리포트: "49~50주에 렉이 심하다" — 실측 [PERF-WEEK]
    # 로그로 국제대회 처리에서만 6~7초가 나오는 게 확인됨] 원래 조 라벨마다
    # _qual_group_standings(tid, g)를 따로 호출했는데, 그 함수가 매번 새
    # 커넥션을 열고 entries/matches 쿼리를 2번씩 날렸다. 월드컵 예선은 유럽12+
    # 아메리카8+아시아10+아프리카12 = 조 42개나 있어서(대륙컵보다 훨씬 많음)
    # champions_engine._finalize_groups에서 고쳤던 것과 똑같은 문제가 여기서는
    # 훨씬 크게 터졌다. 대회 전체 entries/matches를 딱 2번의 쿼리로 한 번에
    # 가져와서 파이썬에서 조별로 나눈다.
    _all_entries = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=?", (tid,)).fetchall()]
    _all_matches = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=?
           AND stage='qual_group' AND home_score>=0""", (tid,)).fetchall()]
    conn.close()
    _entries_by_grp: dict = {}
    for e in _all_entries:
        _entries_by_grp.setdefault(e["grp"], []).append(e)
    _matches_by_grp: dict = {}
    for m in _all_matches:
        _matches_by_grp.setdefault(m["grp"], []).append(m)

    def _qual_standings_for(entries, matches):
        tbl = {e["country"]: {"country": e["country"], "flag": e["flag"], "ovr": e["ovr"],
                              "grade": e["grade"], "p": 0, "w": 0, "d": 0, "l": 0,
                              "gf": 0, "ga": 0, "pts": 0}
               for e in entries}
        for m in matches:
            h, a = tbl.get(m["home"]), tbl.get(m["away"])
            if not h or not a:
                continue
            hs, as_ = m["home_score"], m["away_score"]
            h["p"] += 1; a["p"] += 1
            h["gf"] += hs; h["ga"] += as_
            a["gf"] += as_; a["ga"] += hs
            if hs > as_:
                h["pts"] += 3; h["w"] += 1; a["l"] += 1
            elif hs < as_:
                a["pts"] += 3; a["w"] += 1; h["l"] += 1
            else:
                h["pts"] += 1; a["pts"] += 1; h["d"] += 1; a["d"] += 1
        rows = list(tbl.values())
        rows.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]), reverse=True)
        return rows

    continent = _conf_key((t.get("continent") or "").strip() or "유럽")
    # [2026-08 버그수정, 신민용 리포트: "유로 예선이 qual_group에서 안
    # 끝난다"] 이 함수가 대회의 kind를 보지 않고 무조건 WC_QUAL_32/48만
    # 읽고 있었다 — 유로 예선(kind='cont_qual')도 월드컵 유럽 예선 설정
    # (직행12+플레이오프2)이 그대로 적용돼서, 원래 필요 없는 플레이오프
    # 단계를 기다리다 멈췄다. 대회 kind로 올바른 설정표를 고른다.
    if t["kind"] == "cont_qual":
        from constants import EURO_QUAL
        qual_cfg = EURO_QUAL.get(continent, {})
    else:
        big = t["year"] >= WC_EXPAND_YEAR
        qual_cfg = (WC_QUAL_48 if big else WC_QUAL_32).get(continent, {})

    # 조별 1위/2위 수집
    winners = []
    runners = []
    for g in grps:
        standings = _qual_standings_for(_entries_by_grp.get(g, []), _matches_by_grp.get(g, []))
        if not standings:
            continue
        if len(standings) >= 1: winners.append(standings[0])
        if len(standings) >= 2: runners.append(standings[1])

    direct_n  = qual_cfg.get("direct", len(winners))
    po_teams  = qual_cfg.get("po_teams", 0)
    wildcard  = qual_cfg.get("wildcard", 0)
    quota     = qual_cfg.get("quota", direct_n)

    # ─── 직행 확정 ───
    # [2026-08 버그수정, 신민용 리포트: "예선에서 실제로 올라가는 팀들이
    # 초록색으로 안 뜨고 조 1위만 뜬다" 조사 중 발견] direct_n < 조 수
    # (예: 아프리카 12조 → 상위 9팀만 직행, 3팀 탈락)인 체제에서, winners를
    # 조 라벨 순서(A,B,C...) 그대로 잘라 항상 앞쪽 조 우승팀만 직행시키고
    # 있었다 — "1위 중 상위 N팀"이라는 constants.py 주석의 의도와 달리
    # 성적과 무관하게 조 순서로 당락이 갈리는 버그. 성적순 정렬 후 자른다.
    if direct_n < len(winners):
        winners = sorted(winners, key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]),
                          reverse=True)
    direct_teams = winners[:direct_n]

    # 와일드카드 (아메리카 48팀 체제: 조 2위 중 상위 N팀)
    if wildcard > 0:
        runners.sort(key=lambda r: (r["pts"], r["gf"]-r["ga"], r["gf"], r["ovr"]), reverse=True)
        direct_teams = direct_teams + runners[:wildcard]

    # ─── 플레이오프 필요한 체제 ───
    # 32팀: 유럽(직행12+PO2→1), 아시아(PO10→5), 아프리카(PO12→6)
    # 48팀: 유럽(직행12+PO8→4), 아시아(PO10→10), 아프리카(PO12→9)
    if po_teams > 0:
        # [버그수정] 직행팀을 먼저 저장할 때 set_done=False를 전달해
        # _save_qual_results가 status='done'으로 설정하는 것을 막는다.
        # status='done'이 되면 process_intl_week가 다음 주차에 이 대회를 스킵해
        # _finalize_qual_po가 호출되지 않고, PO 종료 로그도 출력되지 않는 버그 원인.
        if direct_teams:
            _save_qual_results(t, continent, direct_teams, set_done=False)

        # PO 대상: direct_n==0이면 조 1위 전원, 아니면 조 2위 중 상위 po_teams팀
        if direct_n == 0:
            po_pool = winners[:po_teams]
        else:
            # 유럽: 조 2위 중 성적 상위 po_teams팀
            runners.sort(key=lambda r: (r["pts"], r["gf"]-r["ga"], r["gf"], r["ovr"]), reverse=True)
            po_pool = runners[:po_teams]

        random.shuffle(po_pool)
        conn = get_conn(); c = conn.cursor()
        p = get_player()
        my_nat = _my_nat(t, p) if p else ""
        # [2026-07 재설계] 조별 6라운드 뒤 7번째 라운드 = INTL_QUAL_START_DAY
        # + 6*GAP. PO끼리도 서로 다른 나라라 같은 날짜 겹쳐도 무방.
        po_day = INTL_QUAL_START_DAY + 6 * INTL_QUAL_ROUND_GAP_DAYS
        po_week = day_to_week(po_day)
        _po_pairs = [(po_pool[i], po_pool[i+1]) for i in range(0, len(po_pool)-1, 2)]
        for home, away in _po_pairs:
            is_my = 1 if my_nat and (home["country"] == my_nat or away["country"] == my_nat) else 0
            c.execute("""INSERT INTO intl_matches
                         (tournament_id, week, day, stage, home, away, is_my, my_played)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (tid, po_week, po_day, "qual_po", home["country"], away["country"], is_my, 0))
        c.execute("UPDATE intl_tournaments SET status='qual_po' WHERE id=?", (tid,))
        conn.commit(); conn.close()
        add_log(f"🏆 {t['name']} 플레이오프 시작! ({po_week}주차)", "event")
        return  # 플레이오프 완료 후 _finalize_qual_po가 처리

    # ─── 즉시 확정 (플레이오프 없는 체제) ───
    _save_qual_results(t, continent, direct_teams)


def _finalize_qual_po(t):
    """25주차 플레이오프 완료 → 승자를 qual_results에 저장."""
    from game_engine import add_log, get_player
    tid = t["id"]
    continent = _conf_key((t.get("continent") or "").strip() or "유럽")

    conn = get_conn()
    po_matches = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_matches WHERE tournament_id=? AND stage='qual_po'", (tid,)).fetchall()]
    conn.close()

    p = get_player()
    my_nat = _my_nat(t, p) if p else ""
    winners = []
    po_logs = []  # 경기 결과 로그 버퍼 (헤더와 함께 출력)
    for m in po_matches:
        home_row = _entry(tid, m["home"])
        away_row = _entry(tid, m["away"])
        home = {"country": m["home"], "flag": home_row.get("flag",""),
                "grade": home_row.get("grade","F"), "ovr": home_row.get("ovr",50)}
        away = {"country": m["away"], "flag": away_row.get("flag",""),
                "grade": away_row.get("grade","F"), "ovr": away_row.get("ovr",50)}
        if m.get("home_score", -1) >= 0:
            hs, as_ = m["home_score"], m["away_score"]
            if hs > as_:
                winner = home
            elif as_ > hs:
                winner = away
            elif m.get("pso_winner"):
                # [버그수정 2026-07, 신민용 지적] 동점(무승부)이면 승부차기로
                # 이미 승자가 정해져 있는데(sim_my_match_as_ai/_sim_ai_match가
                # _resolve_pso로 pso_winner를 DB에 저장하고 경기 로그에도
                # "승부차기 4-3 승/패"로 표시함), 여기선 그 결과를 무시하고
                # 완전히 새로운 50/50 코인플립으로 승자를 다시 뽑고 있었다.
                # 그 결과 "승부차기에서 졌다"고 로그에 뜨고서 예선 통과는
                # 반대로 표시되는(로그와 결과가 서로 다른 RNG를 쓰는) 모순이
                # 발생했다. cup_engine/champions_engine의 동일 로직
                # (_winner_of 등)은 이미 pso_winner를 우선 확인하고 있었음 —
                # 국제대회 PO만 이 체크가 빠져 있었다.
                winner = home if m["pso_winner"] == home["country"] else away
            else:
                # 승부차기 기록이 없는 경우에만 방어적으로 코인플립 (이론상
                # knockout 무승부는 항상 PSO를 거치므로 거의 발생하지 않음).
                winner = home if random.random() > 0.5 else away
        else:
            # [2026-07 버그수정, 신민용 리포트: "플레이오프가 대진표엔
            # 팀만 뜨고 스코어가 영원히 안 나온다"] 이 분기(경기가 아직
            # 미시뮬 상태로 마감 시점이 온 경우)는 예전엔 승자만 내부적으로
            # 정하고 intl_matches 행은 그대로 -1,-1로 남겨뒀다 — 대회
            # 결과(예선 통과 여부)는 확정되는데 화면에 보이는 그 경기
            # 박스는 스코어 없이 영원히 빈 채로 남는 불일치가 있었다.
            # 이제 실제 스코어도 같이 생성해서 DB에 반영한다(위의 day
            # 타이밍 수정으로 이 분기 자체가 훨씬 드물어지지만, 만에
            # 하나를 위한 안전장치로 결과 일관성은 항상 보장한다).
            _outcome = _match_outcome(home["ovr"], away["ovr"], True)
            _pso_w, _pso_s = "", ""
            if _outcome == "draw":
                _win_home, _pso_s = _resolve_pso(home["ovr"], away["ovr"])
                _hs, _as = 1, 1
                _pso_w = home["country"] if _win_home else away["country"]
            else:
                _hs, _as = _gen_intl_score(_outcome, home["ovr"] - away["ovr"])
            _conn_fix = get_conn()
            _conn_fix.execute(
                """UPDATE intl_matches SET home_score=?, away_score=?,
                   pso_winner=?, pso_score=? WHERE id=?""",
                (_hs, _as, _pso_w, _pso_s, m["id"]))
            _conn_fix.commit()
            _conn_fix.close()
            winner = home if (_pso_w == home["country"] if _pso_w else _hs > _as) else away
        winners.append(winner)
        loser = away if winner["country"] == home["country"] else home
        my_marker = ""
        if my_nat:
            if winner["country"] == my_nat:
                my_marker = " ← 우리팀 통과 ✅"
            elif loser["country"] == my_nat:
                my_marker = " ← 우리팀 탈락 ❌"
        po_logs.append(f"   {home['flag']}{m['home']} vs {away['flag']}{m['away']}"
                       f" → {winner['flag']}{winner['country']} 통과{my_marker}")

    # PO 경기 결과 로그 출력 (경기 시뮬 완료 후)
    if po_logs:
        add_log("─" * 44, "sep")
        add_log(f"🏆 {t['name']} 플레이오프 결과", "event")
        for _line in po_logs:
            add_log(_line, "event")

    # [버그수정] PO 승자만 넘기면 _save_qual_results의 DELETE가
    # 기존 직행팀을 지워버린다. 직행팀을 미리 읽어 PO 승자와 합쳐서
    # 전체를 한 번에 저장한다.
    target_year = t["year"]
    target_kind = "world" if t["kind"] == "wc_qual" else "continent"
    conn_pre = get_conn()
    existing_rows = [dict(r) for r in conn_pre.execute(
        "SELECT country, flag, grade, ovr FROM qual_results"
        " WHERE target_year=? AND kind=? AND continent=?",
        (target_year, target_kind, continent)).fetchall()]
    conn_pre.close()
    existing_names = {r["country"] for r in existing_rows}
    # 직행팀 dict를 _save_qual_results 형식에 맞게 변환
    direct_teams = [{"country": r["country"], "flag": r["flag"],
                     "grade": r["grade"], "ovr": r["ovr"]} for r in existing_rows]
    # po_winners 값만큼만 PO 승자 반영 (유럽: 4팀 PO → 승자 2팀이지만 po_winners=1)
    from constants import WC_QUAL_32, WC_QUAL_48, WC_EXPAND_YEAR
    big = t["year"] >= WC_EXPAND_YEAR
    qual_cfg = (WC_QUAL_48 if big else WC_QUAL_32).get(continent, {})
    po_winners_n = qual_cfg.get("po_winners", len(winners))
    po_new = [w for w in winners if w["country"] not in existing_names][:po_winners_n]
    all_qualified = direct_teams + po_new
    _save_qual_results(t, continent, all_qualified)


def _save_qual_results(t, continent, qualified_list, set_done=True):
    """통과국 목록을 qual_results에 저장 + 내 성적 기록.

    set_done=False: status를 'done'으로 갱신하지 않는다.
    PO 체제(유럽/아시아/아프리카 32팀)에서 직행팀 먼저 저장 시 사용.
    PO 완료(_finalize_qual_po) 시에는 True(기본값)로 호출해 'done' 처리.

    [버그 수정] set_done=False 경로는 지금까지 그냥 INSERT만 해서, 어떤
    이유로든 _finalize_qual()이 같은 (target_year, continent)에 대해
    두 번 이상 불리면(예: 주차 진행 로직이 같은 주차를 다시 처리하는
    경우) 직행팀이 그대로 중복 저장됐다 — 유럽만 direct>0이면서 동시에
    po_teams>0인 유일한 대륙이라 이 경로를 타서, 유독 유럽 예선 통과국
    목록에서만 같은 나라가 2~3개씩 중복으로 보이는 원인이었다(다른
    대륙은 set_done=True만 쓰거나 이 중간 저장 자체를 안 타서 매번
    DELETE 후 재삽입되니 중복이 안 생겼음). 이제 set_done 여부와 무관하게
    country 단위로 먼저 지우고 넣어서, 같은 목록으로 몇 번을 다시
    호출해도 항상 국가당 한 줄만 남는다(멱등).
    """
    from game_engine import add_log, get_player

    tid = t["id"]
    target_year = t["year"]
    target_kind = "world" if t["kind"] == "wc_qual" else "continent"
    qualified_names = {q["country"] for q in qualified_list}

    conn = get_conn(); c = conn.cursor()
    if set_done:
        # 최종 저장(PO 완료 or 직행 전원 확정): 기존 행 전부 지우고 새로 씀
        c.execute("DELETE FROM qual_results WHERE target_year=? AND kind=? AND continent=?",
                  (target_year, target_kind, continent))
    # set_done=True(최종): DELETE 후 qualified_list 전체를 새로 저장.
    # set_done=False(직행팀 중간 저장): _finalize_qual_po가 나중에
    #   직행팀+PO승자를 합쳐 set_done=True로 한 번에 덮어씀.
    for q in qualified_list:
        # [2026-07 버그수정, 신민용 리포트: "국제대회 OVR가 너무 낮다 —
        # 프랑스가 88 정도로 뜬다"] 예선 단계(_qualify_world/_enrich_countries
        # 등)는 전세계 200여 개국을 한 번에 훑어야 해서 fast=True(공식값만,
        # 실제 스쿼드 미반영)로 OVR을 계산했다 — 그 값이 그대로 여기
        # qual_results에 저장돼 이후 조 추첨·순위표 등 화면에 계속
        # 노출됐다. 문제는 fast=True 공식값의 난수 폭(삼각분포 -10~+4)이
        # 꽤 넓어서, 프랑스처럼 실제 태그된 선수가 최정상급(직접 계산
        # 결과 베스트11 평균 97.5)이어도 운 나쁘면 88 같은 값이 그대로
        # 굳어버릴 수 있었다. 여기서는 예선 통과국(최종 32~48개국 정도로
        # 이미 추려진 소규모 목록)에 한해 fast=False로 다시 계산해서
        # 실제 스쿼드 반영값(70%)+공식값(30%) 블렌딩을 정확히 적용한다 —
        # 대상이 작아서 성능 문제도 없다.
        _accurate_ovr = _nat_team_ovr(q.get("grade", "F"), q["country"], continent, fast=False, year=target_year)
        # DELETE로 이미 저장돼 있으면(재호출로 인한 중복 삽입
        # 방지) 먼저 지운 뒤 다시 넣는다 — set_done=False라 위에서 전체
        # DELETE를 안 했어도 국가 단위로는 항상 유일하게 유지된다.
        c.execute("""DELETE FROM qual_results
                     WHERE target_year=? AND kind=? AND continent=? AND country=?""",
                  (target_year, target_kind, continent, q["country"]))
        c.execute("""INSERT INTO qual_results
                     (target_year, kind, continent, country, flag, grade, ovr)
                     VALUES(?,?,?,?,?,?,?)""",
                  (target_year, target_kind, continent,
                   q["country"], q.get("flag",""), q.get("grade","F"), _accurate_ovr))
    if set_done:
        c.execute("UPDATE intl_tournaments SET status='done' WHERE id=?", (tid,))
    conn.commit(); conn.close()

    # 내 나라 성적 기록
    p = get_player()
    my_nat = _my_nat(t, p) if p else ""
    if my_nat and t["my_selected"] == 1:
        passed = my_nat in qualified_names
        result = "예선 통과" if passed else "예선 탈락"
        conn = get_conn()
        conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?", (result, tid))
        agg = conn.execute(
            """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                      COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
               FROM intl_matches WHERE tournament_id=? AND my_played=1""", (tid,)).fetchone()
        conn.execute("""INSERT INTO intl_history(year, competition, team_name, result,
                                                 goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (t["year"], t["name"], my_nat, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
        conn.commit(); conn.close()
        _save_trophy(t["year"], my_nat, t["name"], result)
        icon = "✅" if passed else "❌"
        add_log("─" * 44, "sep")
        add_log(f"{icon} {t['name']} 결과: {my_nat} {result}", "event")
        if passed:
            add_log(f"   → {target_year}년 월드컵 본선 진출!", "event")
        else:
            # [버그수정] 예선 탈락 시 pledge 초기화
            # qual_pledged_nat가 살아있으면 다음 해 본선 _create_one_tournament에서
            # pledged 경로로 진입해 오동작할 수 있음
            try:
                from game_engine import update_player as _upd_pledged
                _upd_pledged(qual_pledged_nat="")
            except Exception:
                pass
    else:
        conn = get_conn()
        conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?", ("예선 미참가", tid))
        conn.commit(); conn.close()

    # 각 대륙 예선 완료 시 무조건 진출국 목록 출력
    if set_done:
        add_log("─" * 44, "sep")
        add_log(f"🌐 {t['name']} 예선 완료 — {len(qualified_list)}개국 본선 진출", "event")
        for _q in qualified_list:
            add_log(f"   ✈️  {_q.get('flag','')} {_q['country']}", "event")


def get_group_standings(tid, grp):
    """조 순위 계산: 승점 → 득실 → 다득점 → 팀 전력."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=? AND grp=?",
        (tid, grp)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND grp=?
           AND stage='group' AND home_score>=0""", (tid, grp)).fetchall()]
    conn.close()

    tbl = {e["country"]: {"country": e["country"], "flag": e["flag"], "ovr": e["ovr"],
                          "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    for m in matches:
        h, a = tbl.get(m["home"]), tbl.get(m["away"])
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


def _pair_avoiding_same_group(strong, weak):
    """[버그 수정] strong/weak: [(조라벨, 값), ...] 리스트. 순서대로 그냥
    짝지으면(예전 방식) 3위 진출팀 배정 순서에 따라 같은 조 1위와 3위가
    바로 다음 라운드에서 다시 만나는 경우가 실제로 자주 생겼다(강한 조가
    1·3위를 같이 배출하면 거의 확정적으로 발생 — 실측 확인됨). 실제
    대회는 이런 조 충돌을 드로우 규칙으로 원천 차단하므로, 여기서도 같은
    조 라벨끼리는 절대 페어링되지 않도록 순서를 유지하며 건너뛴다."""
    weak = list(weak)
    pairs = []
    for sg, s in strong:
        idx = next((i for i, (wg, _w) in enumerate(weak) if wg != sg), None)
        if idx is None:
            idx = 0 if weak else None  # 정말 다 같은 조뿐이면(극단적 예외) 어쩔 수 없이 배정
        if idx is not None:
            _wg, w = weak.pop(idx)
            pairs.append((s, w))
    # 남은 weak끼리 페어링할 때도 같은 조 충돌 회피
    leftover = list(weak)
    while len(leftover) >= 2:
        g0, v0 = leftover.pop(0)
        idx = next((i for i, (g, _v) in enumerate(leftover) if g != g0), None)
        if idx is None:
            idx = 0 if leftover else None
        if idx is not None:
            _g1, v1 = leftover.pop(idx)
            pairs.append((v0, v1))
    return pairs


def _finalize_groups(t, next_stage, next_week):
    """조별리그 종료 → 진출국 확정, 다음 라운드 대진 생성.
    - 월드컵 32개국(8조): 각 조 1·2위 = 16팀 → R16
    - 월드컵 48개국(12조): 각 조 1·2위(24팀) + 3위 중 상위 8팀 = 32팀 → R32
    - 대륙컵(6조): 각 조 1·2위(12팀) + 3위 중 상위 4팀 = 16팀 → R16
    """
    from game_engine import add_log, get_player
    from constants import WC_BEST_THIRDS_BIG
    tid = t["id"]
    is_wc = (t["kind"] == "world")
    is_region = (t["kind"] == "region")
    is_big = is_wc and t["year"] >= WC_EXPAND_YEAR   # 48개국 시대

    # [2026-07 최적화, 신민용 리포트: "국제대회 주간(47~51주)에 렉이 심하다"]
    # 원래 get_group_standings(tid, g)를 조 라벨마다 따로 호출했는데, 그
    # 함수 자체가 매번 새 커넥션을 열고 entries/matches 쿼리를 2번씩
    # 날렸다 — 조 6~12개 × 대회 최대 5개(월드컵+대륙컵 4개가 겹치는 해)가
    # 동시에 이 함수를 타는 주간에는 쿼리가 수십~백 건까지 쌓였다. 이
    # 대회의 전체 조(entries+group stage matches)를 딱 2번의 쿼리로 한
    # 번에 가져와서 파이썬에서 조별로 나누는 방식으로 바꾼다.
    conn0 = get_conn()
    _all_entries = [dict(r) for r in conn0.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=?", (tid,)).fetchall()]
    _all_matches = [dict(r) for r in conn0.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=?
           AND stage='group' AND home_score>=0""", (tid,)).fetchall()]
    conn0.close()

    # [2026-08 신설] region은 조 개수가 저장돼 있지 않으므로(대회마다
    # 나라 수가 달라서 규모 자체가 다름) 실제 참가국 수로 그때그때
    # regional_cup_format()을 다시 돌려 n_groups/best_thirds를 구한다 —
    # 조 편성 때 쓴 것과 완전히 같은 함수라 값도 항상 일치한다.
    if is_wc:
        n_groups = WC_GROUPS_BIG if is_big else WC_GROUPS
        n_best = WC_BEST_THIRDS_BIG if is_big else 0
    elif is_region:
        from constants import regional_cup_format
        _fmt = regional_cup_format(len(_all_entries))
        n_groups = _fmt["n_groups"]
        n_best = _fmt["best_thirds"]
    else:
        n_groups = CONT_GROUPS
        n_best = CONT_BEST_THIRDS
    labels = _GROUP_LABELS[:n_groups]

    _entries_by_grp: dict = {}
    for e in _all_entries:
        _entries_by_grp.setdefault(e["grp"], []).append(e)
    _matches_by_grp: dict = {}
    for m in _all_matches:
        _matches_by_grp.setdefault(m["grp"], []).append(m)

    def _standings_for(entries, matches):
        tbl = {e["country"]: {"country": e["country"], "flag": e["flag"], "ovr": e["ovr"],
                              "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
               for e in entries}
        for m in matches:
            h, a = tbl.get(m["home"]), tbl.get(m["away"])
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

    firsts, seconds = {}, {}
    thirds = []      # (조라벨, row) — best-3rd 후보
    eliminated = []
    for g in labels:
        rows = _standings_for(_entries_by_grp.get(g, []), _matches_by_grp.get(g, []))
        if len(rows) < 2:
            continue
        firsts[g]  = rows[0]["country"]
        seconds[g] = rows[1]["country"]
        if is_wc and not is_big:
            # 32개국 월드컵: 3위 이하 전부 탈락
            eliminated.extend(r["country"] for r in rows[2:])
        else:
            # 48개국 월드컵 / 대륙컵: 3위는 best-3rd 경쟁, 4위는 탈락
            if len(rows) >= 3:
                thirds.append((g, rows[2]))
            eliminated.extend(r["country"] for r in rows[3:])

    # 3위 팀 진출 처리 (48개국 월드컵 & 대륙컵 & 지역컵 공통, n_best는 위에서 이미 결정됨)
    best_thirds = []
    if n_best > 0 and thirds:
        thirds.sort(key=lambda gr: (gr[1]["pts"], gr[1]["gf"] - gr[1]["ga"],
                                    gr[1]["gf"], gr[1]["ovr"]), reverse=True)
        adv = thirds[:n_best]
        best_thirds = [(g, r["country"]) for g, r in adv]
        eliminated.extend(r["country"] for _, r in thirds[n_best:])

    conn = get_conn()
    c = conn.cursor()
    if eliminated:
        c.executemany("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                      [(tid, nat_e) for nat_e in eliminated])

    # ── 다음 라운드 대진 생성 ──
    if is_wc and not is_big:
        # 32개국: 1A-2B, 1C-2D, … / 1B-2A, 1D-2C, … → 16강
        # [버그수정] firsts/seconds에 없는 조 라벨 접근 시 KeyError 방지
        pairs = []
        for i in range(0, n_groups - 1, 2):
            if i + 1 >= len(labels): break
            g1, g2 = labels[i], labels[i + 1]
            if g1 not in firsts or g2 not in seconds: continue
            pairs.append((firsts[g1], seconds[g2]))
        for i in range(0, n_groups - 1, 2):
            if i + 1 >= len(labels): break
            g1, g2 = labels[i], labels[i + 1]
            if g2 not in firsts or g1 not in seconds: continue
            pairs.append((firsts[g2], seconds[g1]))

    elif is_big:
        # 48개국: 조 1·2위(24팀) + 3위 8팀 = 32팀 → 32강
        # [버그 수정] 같은 조 1위·3위(또는 1위·2위)가 32강에서 바로 다시
        # 만나지 않도록 _pair_avoiding_same_group으로 조 충돌을 회피한다.
        strong = [(g, firsts[g]) for g in labels if g in firsts]
        weak = list(best_thirds) + [(g, seconds[g]) for g in labels if g in seconds]
        pairs = _pair_avoiding_same_group(strong, weak)

    else:
        # 대륙컵 24개국: 1위6 + 2위6 + 3위4 = 16팀 → 16강
        # [버그 수정] 위와 동일하게 같은 조 충돌 회피.
        strong = [(g, firsts[g]) for g in labels if g in firsts]
        weak = list(best_thirds) + [(g, seconds[g]) for g in labels if g in seconds]
        pairs = _pair_avoiding_same_group(strong, weak)

    p = get_player()
    nat = _my_nat(t, p)
    # [2026-07 재설계] day는 더 이상 여기서 재계산하지 않는다 — 대회
    # 생성 시점에 _precreate_ko_shell이 이미 이 스테이지의 모든 슬롯에
    # 정확한 day/week를 배정해뒀다(그때와 완전히 같은 규칙이라 값도
    # 항상 일치한다). 여기서는 그 placeholder에 실제 진출국 이름만
    # slot 번호로 매칭해 채워 넣는다 — "경기 자체는 미리 존재하고
    # 참가팀만 나중에 확정된다"는 설계(신민용 제안).
    fills = {}
    for slot, (home, away) in enumerate(pairs):
        is_my = 1 if nat in (home, away) else 0
        fills[slot] = (home, away, is_my)
    _fill_ko_shell(conn, c, tid, next_stage, fills)
    c.execute("UPDATE intl_tournaments SET status='ko' WHERE id=?", (tid,))
    conn.commit()
    conn.close()

    add_log(f"🌍 {t['name']} 조별리그 종료 → {STAGE_KO[next_stage]} 진출국 확정",
            "event")
    # 내 국가가 조별 탈락했으면 결과 확정
    if nat and nat in eliminated:
        _record_my_exit(t, "조별리그 탈락")


def _advance_knockout(t, cur_stage, next_stage, next_week):
    """현재 KO 라운드(cur_stage) 종료 → 패자 탈락, 다음 라운드 생성.

    [2026-07 재설계] 예전엔 week=?로 현재 라운드 경기를 찾았는데, 압축된
    day 기반 일정 때문에 한 라운드가 여러 주에 걸칠 수 있어서(예: 8강이
    47~48주 두 주에 걸침) week 하나만 보면 그 라운드의 절반을 놓친다.
    stage 이름으로 조회하면 몇 주에 걸치든 항상 그 라운드 전체를 정확히
    찾는다."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND stage=?
           ORDER BY slot""", (tid, cur_stage)).fetchall()]
    conn.close()
    if not cur:
        return

    p = get_player()
    nat = _my_nat(t, p)
    cur_stage_ko = STAGE_KO.get(cur[0]["stage"], "")

    winners = []
    is_sf = cur and cur[0]["stage"] == "SF"

    conn = get_conn()
    c = conn.cursor()
    for m in cur:
        w = _winner_of(m)
        loser = m["away"] if w == m["home"] else m["home"]
        winners.append((m["slot"], w))
        # SF 패자는 3/4위전을 뛰므로 alive=0으로 즉시 탈락 처리하지 않는다.
        # (탈락은 _finish_tournament에서 4위 확정 후 처리)
        if not is_sf:
            c.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                      (tid, loser))
        if nat and loser == nat and not is_sf:
            conn.commit()
            conn.close()
            _record_my_exit(t, cur_stage_ko)
            conn = get_conn()
            c = conn.cursor()

    # 4강(SF) 종료 시: 패자 2팀으로 3/4위전(TP) 생성 (결승과 같은 주차)
    losers = []
    if is_sf:
        for m in cur:
            w = _winner_of(m)
            loser = m["away"] if w == m["home"] else m["home"]
            losers.append(loser)

    # [2026-07 재설계] day는 더 이상 여기서 재계산하지 않는다 — 대회
    # 생성 시점에 _precreate_ko_shell이 next_stage/TP 슬롯의 day/week를
    # 이미 정확히 배정해뒀다. 여기서는 승자 이름만 slot 번호로 매칭해
    # placeholder를 채운다("경기는 미리 존재, 참가팀만 나중에 확정").
    winners.sort()
    _next_pairs = []
    for slot in range(0, len(winners), 2):
        if slot + 1 >= len(winners):
            break
        _next_pairs.append((winners[slot][1], winners[slot + 1][1]))

    fills = {}
    for idx, (home, away) in enumerate(_next_pairs):
        is_my = 1 if nat in (home, away) else 0
        fills[idx] = (home, away, is_my)
    _fill_ko_shell(conn, c, tid, next_stage, fills)

    # 3/4위전: SF 패자 2팀 — TP placeholder(slot=999)를 채운다.
    if len(losers) == 2:
        tp_home, tp_away = losers[0], losers[1]
        is_my_tp = 1 if nat in (tp_home, tp_away) else 0
        _fill_ko_shell(conn, c, tid, "TP", {999: (tp_home, tp_away, is_my_tp)})
        add_log(f"🥉 {t['name']} 3/4위전 대진: {tp_home} vs {tp_away}", "event")

    conn.commit()
    conn.close()
    add_log(f"🌍 {t['name']} {cur_stage_ko} 종료 → {STAGE_KO[next_stage]} 대진 확정", "event")


def _intl_country_stage_weights(tid):
    """월드컵/대륙컵 참가국별 '진출 라운드 가중치' — champions_engine.
    _cl_team_stage_weights와 동일한 설계(신민용 확정: "대회 MVP/베스트11에
    팀(국가) 성적을 반영하자"). intl_matches는 팀 ID 대신 국가명(country
    TEXT)으로 식별한다. 조별리그만=0.70, 32강=0.75, 16강=0.80, 8강=0.90,
    4강(3/4위전 포함)=0.96, 준우승=0.99, 우승=1.00."""
    conn = get_conn()
    t = conn.execute("SELECT winner FROM intl_tournaments WHERE id=?", (tid,)).fetchone()
    winner_nat = t["winner"] if t else ""
    _ORDER = {"R32": 0, "R16": 1, "QF": 2, "SF": 3}
    _TIER_W = {0: 0.75, 1: 0.80, 2: 0.90, 3: 0.96}
    furthest = {}
    runner_up_nat = None
    for m in conn.execute(
            "SELECT stage, home, away FROM intl_matches "
            "WHERE tournament_id=? AND stage IN ('R32','R16','QF','SF','F','TP') "
            "AND home_score>=0", (tid,)).fetchall():
        stg = m["stage"]
        if stg == "F":
            loser = m["away"] if m["home"] == winner_nat else m["home"]
            runner_up_nat = loser
            continue
        if stg == "TP":
            for nat in (m["home"], m["away"]):
                furthest[nat] = max(furthest.get(nat, -1), _ORDER["SF"])
            continue
        if stg not in _ORDER:
            continue
        idx = _ORDER[stg]
        for nat in (m["home"], m["away"]):
            furthest[nat] = max(furthest.get(nat, -1), idx)
    conn.close()

    def _weight(country):
        if country == winner_nat:
            return 1.00
        if country == runner_up_nat:
            return 0.99
        return _TIER_W.get(furthest.get(country, -1), 0.70)
    return _weight


def _award_intl_awards(t):
    """[2026-07 확장, 신민용 확정] 월드컵 골든볼/골든부트/베스트11/영플레이어상,
    대륙컵 MVP/득점왕/베스트11/영플레이어상 + 골든글러브. 이제 ai_players.
    nationality로 실제 각국 선수를 조회할 수 있으므로, champions_engine.
    _award_cl_awards와 동일 패턴으로 실제 선수 기반 AI 경쟁 풀을 구성한다.
    내가 조기 탈락해도(4강 못 가도) 대회 전체 기준으로 별개 판정.
    [2026-07 추가 확장, 설계문서 v2 반영] 결승·준결승 빅게임 보너스(가산,
    상한 있음), 골든글러브 세이브율·평균실점 품질 게이트, 그리고 "월드컵/
    대륙컵 영플레이어상은 사실상 평생 한 번"을 실제로 강제하는 로직을
    추가한다(과거에 같은 대회 성격으로 이미 영플레이어상을 받았으면 후보
    제외 — 나이 조건만으로는 극단적으로 어린 나이에 데뷔한 경우 두 번
    받는 게 이론적으로 가능했음)."""
    from game_engine import (get_player, add_log, _estimate_ai_season, _estimate_ai_clean_sheets,
                             _position_award_score, _evaluate_extra_awards,
                             _cap_additive_bonus, _gk_quality_ok,
                             ATTACK_POS, GK_POS, DF_POS, MF_POS)
    tid = t["id"]
    is_wc = (t["kind"] == "world")
    conn = get_conn()
    my_row = conn.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(my_goals),0) g, COALESCE(SUM(my_assists),0) a,
                  COALESCE(AVG(my_rating),0) r, COALESCE(SUM(my_saves),0) sv,
                  COALESCE(SUM(my_conceded),0) gc
           FROM intl_matches WHERE tournament_id=? AND my_played=1""", (tid,)).fetchone()
    if not my_row or my_row["n"] == 0:
        conn.close()
        return
    n_games = max(1, my_row["n"])
    p = get_player()
    my_pos = p.get("position", "ST") if p else "ST"
    my_ovr = p.get("ovr", 60) if p else 60
    my_age = p.get("age", 25) if p else 25
    my_nat = _my_nat(t, p)
    my_cs = conn.execute(
        """SELECT COUNT(*) c FROM intl_matches WHERE tournament_id=? AND my_played=1
           AND ((home=? AND away_score=0) OR (away=? AND home_score=0))""",
        (tid, my_nat, my_nat)).fetchone()["c"]

    pool = [{"position": my_pos, "goals": my_row["g"], "assists": my_row["a"], "rating": my_row["r"],
             "ovr": my_ovr, "cs": my_cs, "age": my_age, "is_mine": True, "country": my_nat}]

    entries = conn.execute(
        "SELECT country FROM intl_entries WHERE tournament_id=?", (tid,)).fetchall()
    ALL_POS = GK_POS + DF_POS + MF_POS + ATTACK_POS
    ph = ",".join("?" * len(ALL_POS))
    for e in entries:
        if e["country"] == my_nat:
            continue
        rows = conn.execute(
            f"""SELECT ovr, position, sub_role, age FROM ai_players
                WHERE nationality=? AND position IN ({ph})""",
            (e["country"], *ALL_POS)).fetchall()
        for r in rows:
            g, a, rt = _estimate_ai_season(r["ovr"], r["position"], 85, 85, r["sub_role"],
                                           full_season_matches=n_games)
            cs = _estimate_ai_clean_sheets(r["position"], r["ovr"], 85, 85, n_games) if r["position"] in GK_POS else 0
            pool.append({"position": r["position"], "goals": g, "assists": a, "rating": rt,
                        "ovr": r["ovr"], "cs": cs, "age": r["age"] or 25, "is_mine": False,
                        "country": e["country"]})

    # [2026-07 신설] 국가 진출 라운드 가중치 — 골든볼(MVP)/베스트11/영플레이어에만 적용
    _stage_w = _intl_country_stage_weights(tid)
    my_base_score = _position_award_score(my_pos, my_row["g"], my_row["a"], my_row["r"], my_ovr, my_cs)
    my_score = my_base_score * _stage_w(my_nat)

    # [2026-07 신설] 빅게임 보너스 — 결승/준결승/3·4위전 경기의 실제 기록만
    # 따로 계산해 가산(고정 숫자 아님, 상한은 기준 점수의 10%). champions_engine.
    # _award_cl_awards와 동일한 설계.
    _bg = conn.execute(
        """SELECT COUNT(*) n, COALESCE(AVG(my_rating),0) r, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a
           FROM intl_matches WHERE tournament_id=? AND my_played=1 AND stage IN ('SF','F','TP')""",
        (tid,)).fetchone()
    if _bg and _bg["n"] > 0:
        _raw_bonus = (_bg["r"] - 6.0) * 1.2 + (_bg["g"] + _bg["a"]) * 0.8
        my_score += _cap_additive_bonus(_raw_bonus, my_base_score, cap_ratio=0.10)

    others = [x for x in pool if not x["is_mine"]]
    # [2026-07 확장, 신민용 확정] 골든볼/골든부트를 "내가 1등이냐"만 보던 걸
    # sorted()로 순위 전체를 매겨서 2위(실버)·3위(브론즈)까지 판정한다.
    # 비용은 max()→sorted() 수준이라 거의 없다. 실제로도 월드컵은 골든볼/
    # 실버볼/브론즈볼, 골든부트/실버부트/브론즈부트를 전부 시상한다.
    _ai_scorer_scores = sorted((x["goals"] for x in others), reverse=True)
    _ai_mvp_scores = sorted((_position_award_score(x["position"], x["goals"], x["assists"],
                                                    x["rating"], x["ovr"], x["cs"]) * _stage_w(x["country"])
                              for x in others), reverse=True)

    def _my_rank(my_val, ai_sorted_desc):
        """내 값이 AI 정렬 리스트(내림차순) 안에서 몇 등인지(1부터). 동점은
        내가 우선(기존 >= 판정 관례 유지)."""
        rank = 1
        for v in ai_sorted_desc:
            if v > my_val:
                rank += 1
            else:
                break
        return rank

    year = t["year"]
    mvp_name = "골든볼" if is_wc else f"{t['name']} MVP"
    boot_name = "골든부트" if is_wc else f"{t['name']} 득점왕"
    glove_name = "골든글러브" if is_wc else f"{t['name']} 골든글러브"
    best11_name = "베스트11" if is_wc else f"{t['name']} 베스트11"
    young_name = "영플레이어상" if is_wc else f"{t['name']} 영플레이어상"
    # 은/동메달용 이름 — 월드컵만 실제 명칭(실버볼 등)이 있고, 대륙컵은
    # 실제로 이런 시상이 없어 "MVP 2위/득점 2위" 식으로 이름 붙인다.
    mvp2_name = "실버볼" if is_wc else f"{t['name']} MVP 2위"
    mvp3_name = "브론즈볼" if is_wc else f"{t['name']} MVP 3위"
    boot2_name = "실버부트" if is_wc else f"{t['name']} 득점 2위"
    boot3_name = "브론즈부트" if is_wc else f"{t['name']} 득점 3위"
    awards = []
    if my_row["g"] > 0:
        _scorer_rank = _my_rank(my_row["g"], _ai_scorer_scores)
        if _scorer_rank == 1:
            awards.append((boot_name, f"{my_row['g']}골"))
        elif _scorer_rank == 2:
            awards.append((boot2_name, f"{my_row['g']}골"))
        elif _scorer_rank == 3:
            awards.append((boot3_name, f"{my_row['g']}골"))
    _mvp_rank = _my_rank(my_score, _ai_mvp_scores)
    if _mvp_rank == 1:
        awards.append((mvp_name, f"{year} {t['name']}"))
    elif _mvp_rank == 2:
        awards.append((mvp2_name, f"{year} {t['name']}"))
    elif _mvp_rank == 3:
        awards.append((mvp3_name, f"{year} {t['name']}"))
    # [2026-07 신설, 설계문서 v2 반영] 월드컵/대륙컵 영플레이어상은 4년 주기
    # 대회 특성상 실제로는 사실상 평생 한 번인데, 나이 조건(<=21)만으로는
    # 아주 어린 나이에 데뷔한 극소수 케이스가 두 번 받는 게 이론상 가능했다.
    # 과거에 같은 대회 성격(월드컵이면 월드컵끼리, 대륙컵이면 이 대회 이름
    # 그대로)으로 이미 영플레이어상을 받은 적이 있으면 이번엔 후보에서 제외한다.
    _already_won_young = conn.execute(
        "SELECT 1 FROM awards WHERE is_mine=1 AND award_type=? AND year<?",
        (young_name, year)).fetchone() is not None
    for label in _evaluate_extra_awards(pool, my_pos, my_age,
                                         weight_fn=lambda x: _stage_w(x["country"])):
        if label == "베스트11":
            awards.append((best11_name, f"{year} {t['name']} {label}"))
        elif not _already_won_young:
            awards.append((young_name, f"{year} {t['name']} {label}"))
    if (my_pos in GK_POS and my_cs >= 2
            and _gk_quality_ok(my_row["sv"], my_row["gc"], n_games, n_games, min_play_ratio=0.0)):
        gk_group = [x for x in pool if x["position"] in GK_POS]
        best_gk = max(gk_group, key=lambda x: x["cs"]) if gk_group else None
        if best_gk and best_gk["is_mine"]:
            awards.append((glove_name, f"{my_cs} 클린시트"))

    for atype, detail in awards:
        add_log(f"🏅 {atype} 수상! ({detail})", "event")
        conn.execute(
            "INSERT INTO awards(year,award_type,league_name,detail,is_mine) VALUES(?,?,?,?,1)",
            (year, atype, t["name"], detail))
    if awards:
        conn.commit()
    conn.close()


def _finish_tournament(t, final_week):
    """결승 + 3/4위전 종료 → 우승국·3위 확정, 내 결과 기록."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    fm = conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND stage='F'
           AND home_score>=0""", (tid,)).fetchone()
    tp = conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND stage='TP'
           AND home_score>=0""", (tid,)).fetchone()
    conn.close()
    if not fm:
        return
    fm = dict(fm)
    winner  = _winner_of(fm)
    runner  = fm["away"] if winner == fm["home"] else fm["home"]

    # 3/4위전 결과
    third = fourth = None
    if tp:
        tp = dict(tp)
        third  = _winner_of(tp)
        fourth = tp["away"] if third == tp["home"] else tp["home"]

    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET status='done', winner=? WHERE id=?",
                 (winner, tid))
    conn.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                 (tid, runner))
    if fourth:
        conn.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                     (tid, fourth))
    conn.commit()
    conn.close()

    we = _entry(tid, winner)
    add_log(f"🏆 {t['name']} 우승: {we['flag']}{winner}!", "event")
    if third:
        te = _entry(tid, third)
        add_log(f"🥉 {t['name']} 3위: {te['flag']}{third}", "event")

    p = get_player()
    nat = _my_nat(t, p)
    if nat == winner:
        _record_my_exit(t, "우승")
    elif nat == runner:
        _record_my_exit(t, "준우승")
    elif nat == third:
        _record_my_exit(t, "3위")
    elif nat == fourth:
        _record_my_exit(t, "4위")

    # [2026-07 신설] 조기탈락해도 골든볼/골든부트는 별개로 판정
    _award_intl_awards(t)


# ─────────────────────────────────────────────
# 내 결과 확정 + 보상
# ─────────────────────────────────────────────

_REWARD = {  # 결과: (명성, 인기, 행복도) ─ 월드컵 기준
    "우승":         (25, 15, 20),
    "준우승":       (15,  8, 10),
    "3위":          (12,  6,  8),
    "4위":          ( 9,  4,  5),
    "4강":          (10,  5,  6),   # 3/4위전 없는 대회(대륙컵 등) 호환
    "8강":          ( 6,  3,  3),
    "16강":         ( 3,  2,  1),
    "32강":         ( 2,  1,  0),   # 48팀 체제 전용 (32팀 체제엔 없는 라운드)
    "조별리그 탈락": ( 1,  0, -2),
}


def _record_my_exit(t, result):
    """내 국가의 최종 성적 확정: 트로피/이력 기록 + 보상 (선발됐을 때만)."""
    from game_engine import add_log, get_player, update_player
    p = get_player()
    if not p:
        return
    nat = _my_nat(t, p)

    # 미선발(또는 출전 보류)이면 이 대회 성적은 내 경력이 아니다.
    #  - my_result는 'XX 미선발'로만 표시(대회 화면 일관성용)
    #  - trophy_log / intl_history(개인기록) / 보상은 일절 기록하지 않는다.
    #    (선발 안 됐는데 대표팀이 우승했다고 내 우승 트로피로 박히던 버그 방지)
    if t["my_selected"] != 1:
        conn = get_conn()
        conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?",
                     (f"{result} (미선발)", t["id"]))
        conn.commit()
        conn.close()
        return

    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?", (result, t["id"]))
    conn.commit()
    conn.close()

    _save_trophy(t["year"], nat, t["name"], result)

    fame_g, pop_g, hap_g = _REWARD.get(result, (0, 0, 0))
    if t["kind"] != "world":  # 대륙컵은 60% 스케일
        fame_g = round(fame_g * 0.6)
        pop_g = round(pop_g * 0.6)
        hap_g = round(hap_g * 0.6)

    update_player(
        fame=min(100, p.get("fame", 0) + fame_g),
        popularity=min(100, p.get("popularity", 0) + pop_g),
        happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
    )

    # 이번 대회 개인 기록 집계 → intl_history (대회 단위)
    conn = get_conn()
    agg = conn.execute(
        """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM intl_matches
           WHERE tournament_id=? AND my_played=1""", (t["id"],)).fetchone()
    conn.execute("""INSERT INTO intl_history(year, competition, team_name, result,
                                             goals, assists, caps, rating)
                    VALUES(?,?,?,?,?,?,?,?)""",
                 (t["year"], t["name"], nat, result,
                  agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()

    icon = "🏆" if result == "우승" else "🌍"
    add_log(f"{icon} {t['year']}년 {t['name']} 최종 성적: {result}  "
            f"(명성 +{fame_g}, 인기 +{pop_g})", "event")


def _save_trophy(year, nat, competition, result):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=?",
        (year, competition)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,0,?)""", (year, nat, result, competition))
        conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# 국제전 이력 조회 (커리어창 / 은퇴창 공용)
# ─────────────────────────────────────────────

def get_my_intl_matches(only_qual=False):
    """내가 실제 출전한 A매치 목록 (시간순). 결장 경기는 제외.

    only_qual=False: 본선 경기만 (world/continent)
    only_qual=True : 예선 경기만 (wc_qual)

    반환 dict: year, week, position, nat, nat_flag, comp, stage,
               opp, opp_flag, goals, assists, saves, conceded,
               rating, score, result(승/무/패, PSO 표기 포함)
    """
    if only_qual:
        # [2026-08 버그수정, 신민용 리포트: "은퇴엔 국제전(예선)이 있는데
        # 커리어에는 이게 사라졌다"] 유로 예선(kind='cont_qual')이 나중에
        # 추가됐는데 이 필터가 여전히 'wc_qual'만 걸러서, 유로 예선에서
        # 뛴 경기가 커리어/은퇴창 "국제전(예선)" 탭에서 통째로 빠지고
        # 있었다 — get_my_tournament()의 동일 버그(스케줄 탭 자체가 안
        # 뜨는 문제)와 같은 원인.
        kind_filter = "t.kind IN ('wc_qual', 'cont_qual')"
    else:
        # [2026-08 신설, 신민용 리포트: "지역컵 경기가 국제전 기록에 하나도
        # 안 들어간다"] 3단계 지역컵(kind='region')이 이 화이트리스트에
        # 없어서, 실제로 뛴 지역컵 경기가 커리어/은퇴창 "국제전" 탭에서
        # 통째로 빠지고 있었다 — 개인 통계는 저장되고 있었는데(스탯 컬럼
        # 자체는 kind 무관하게 채워짐) 이 조회 하나가 걸러버린 것.
        kind_filter = "t.kind IN ('world','continent','region')"
    conn = get_conn()
    # [2026-07 재수정, 신민용 지적: "다친 게 아니라 그냥 벤치라 안 뛴
    # 경기도 있는데 그건 빠진다"] my_played=1이거나 absence_reason이
    # 있는 것만 걸렀더니, "건강한데 로테이션으로 그냥 안 뛴" 경기
    # (my_played=0이면서 absence_reason도 NULL)가 통째로 빠졌다 —
    # 내 대표팀 소속으로 치러진 경기는 전부 보여주고(결과가 난 것만,
    # is_my=1로 범위 한정), 뛰었는지 안 뛰었는지는 화면에서
    # my_played/absence_reason으로 구분한다.
    # [2026-07 재수정, 신민용 리포트: "국가대표 미선발인데 국제전 기록에
    # 벤치로 뜬다"] is_my=1은 "이 나라가 내 후보국"이라는 뜻일 뿐, "이
    # 대회에 실제로 선발됐다"는 뜻이 아니다(intl_matches.is_my는 후보국
    # 매치에도 미리 찍혀 있음 — database.py 주석 참고). 실제 선발 여부는
    # intl_tournaments.my_selected(1=선발)에 따로 있는데 이걸 빼먹어서,
    # 미선발 대회의 경기까지 전부 "벤치"로 잘못 나왔다 — t.my_selected=1
    # 조건을 추가해서 진짜 선발된 대회만 가져온다.
    rows = [dict(r) for r in conn.execute(
        f"""SELECT m.*, t.year AS t_year, t.name AS comp
           FROM intl_matches m
           JOIN intl_tournaments t ON m.tournament_id = t.id
           WHERE m.is_my = 1 AND t.my_selected = 1 AND m.home_score >= 0 AND {kind_filter}
           ORDER BY t.year, m.week""").fetchall()]
    # [2026-08 성능 수정, 신민용 리포트: "재능 좋은 선수로 오래 뛰면
    # 은퇴/커리어창이 심하게 렉걸린다"] intl_entries 전체 대신 내 경기가
    # 걸쳐있는 tournament_id만 걸러서 가져온다.
    _tids = {r["tournament_id"] for r in rows}
    flags = {}
    if _tids:
        _ph = ",".join("?" * len(_tids))
        flags = {(r["tournament_id"], r["country"]): r["flag"]
                 for r in conn.execute(
                     f"SELECT tournament_id, country, flag "
                     f"FROM intl_entries WHERE tournament_id IN ({_ph})",
                     tuple(_tids)).fetchall()}
    conn.close()

    from game_engine import get_player
    p = get_player()
    # [2026-07 수정] 결장(부상/출전정지) 경기는 my_nat이 안 남겨져 있으므로
    # (실제로 뛴 경기만 my_nat을 기록했음), 현재 확정된 국적(intl_committed)을
    # 대신 써서 홈/원정을 판별한다 — 대표팀 국적은 첫 A매치 이후 보통 고정.
    _committed_nat = (p.get("intl_committed") or "") if p else ""

    out = []
    for m in rows:
        nat = m["my_nat"] or _committed_nat
        is_home = (m["home"] == nat)
        opp  = m["away"] if is_home else m["home"]
        my_s = m["home_score"] if is_home else m["away_score"]
        op_s = m["away_score"] if is_home else m["home_score"]

        if m["pso_winner"]:
            result = "승(PSO)" if m["pso_winner"] == nat else "패(PSO)"
        elif my_s > op_s:
            result = "승"
        elif my_s < op_s:
            result = "패"
        else:
            result = "무"

        stage = STAGE_KO.get(m["stage"], m["stage"])
        if m["stage"] in ("group", "qual_group") and m["grp"]:
            stage = f"조별 {m['grp']}조"

        # [2026-07 신설] 'N주차' 대신 실제 날짜(YYYY-MM-DD). day가 저장돼
        # 있으면(신규 경기) 그대로, 없으면(구버전 세이브의 과거 경기) 그
        # 주의 첫날로 근사한다.
        from constants import day_to_iso_date_str, week_to_iso_date_str
        date_str = (day_to_iso_date_str(m["t_year"], m["day"]) if m.get("day")
                    else week_to_iso_date_str(m["t_year"], m["week"]))

        out.append({
            "year": m["t_year"], "week": m["week"], "date": date_str,
            "position": m["my_position"], "nat": nat,
            "nat_flag": flags.get((m["tournament_id"], nat), ""),
            "comp": m["comp"], "stage": stage,
            "opp": opp, "opp_flag": flags.get((m["tournament_id"], opp), ""),
            "goals": m["my_goals"], "assists": m["my_assists"],
            "saves": m["my_saves"], "conceded": op_s,
            "rating": m["my_rating"],
            "shots": m.get("my_shots", 0), "shots_on": m.get("my_shots_on", 0),
            "key_passes": m.get("my_key_passes", 0), "dribbles": m.get("my_dribbles", 0),
            "blocks": m.get("my_blocks", 0), "pass_acc": m.get("my_pass_acc", 0),
            "score": f"{my_s}-{op_s}", "result": result,
            "absence_reason": m.get("my_absence_reason"),
            "my_played": m.get("my_played", 0),
            # [2026-08 신설, PHASE 2: opponent_context_engine] 이 경기가
            # 열린 대회 id — 상대가 그 대회에서 최종적으로 어디까지
            # 갔는지(opponent_context.get_intl_opponent_stage)를 나중에
            # 조회하려면 필요하다. 기존 소비처는 이 키를 안 쓰므로
            # 하위호환에 영향 없음(추가만, 제거/변경 없음).
            "tournament_id": m["tournament_id"],
        })
    return out


def get_my_qual_matches():
    """내가 출전한 예선 경기만 반환 (커리어/은퇴 '국제전(예선)' 탭용)."""
    return get_my_intl_matches(only_qual=True)