# constants.py

GAME_START_YEAR = 2000
PLAYER_START_AGE = 16
MIN_JOIN_AGE = 17
# [2026-07 신설, 신민용 요청] 16세에 대륙컵(네이션스컵) 발탁 선택창이 뜨는 게
# 비현실적이라는 피드백 — 세계 대회 자체(AI 국가들 진행)는 그대로 두고,
# "내가 후보로 뽑힐 수 있는 최소 나이"만 따로 제한한다.
MIN_INTL_CALLUP_AGE = 17
MAX_AGE = 50

# ── 시즌 구조 (2026-07 FIFA식 일 단위 캘린더로 재설계) ─────────────
# 1년 = 364일(52주). 클럽 시즌과 국제대회(월드컵/대륙컵/예선)가 절대 겹치지
# 않도록, 국제대회는 클럽 시즌이 완전히 끝난 뒤의 전용 비시즌 구간에만 연다.
#   - 프리시즌 + 여름 이적시장:  1~21일   (1~3주)
#   - 클럽 시즌(리그+컵대회):   22~300일  (4~43주, 총 279일)
#       · 팀 수 최대 30 기준 더블 라운드로빈 = 29+29 = 58라운드.
#         279일에 58라운드를 고르게 분배하면 평균 간격 약 4.9일.
#       · 겨울 이적시장(아래 WINTER_OFFER_*)은 이 구간 '중간에 겹쳐서' 열림
#         — 실제 프리미어리그처럼 겨울 이적시장 동안에도 경기는 계속된다.
#         (분데스리가식으로 완전히 경기를 끊고 싶으면 이 구간만 빼고
#         라운드를 재분배하면 되는데, 지금은 안 뺀 채로 간다.)
#   - 국제대회 전용 비시즌:    301~364일 (44~52주, 총 64일) — 이 구간엔
#     클럽 경기가 전혀 없다. 월드컵/대륙컵 본선·예선이 전부 여기서 열린다.
CLUB_PRESEASON_START_DAY = 1
CLUB_PRESEASON_END_DAY   = 21

CLUB_SEASON_START_DAY = 22
CLUB_SEASON_MID_DAY   = 161   # 상/하반기 분기점(홈/원정 반전 기준)
CLUB_SEASON_END_DAY   = 300

INTL_OFFSEASON_START_DAY = 301
INTL_OFFSEASON_END_DAY   = 364

# 겨울 이적시장 — 클럽 시즌 중간에 겹쳐서 열림(경기는 안 끊김)
WINTER_OFFER_START_DAY = 190
WINTER_OFFER_END_DAY   = 203

# 상/하반기 라운드 매칭 (8팀, 인덱스 기반) — 8팀 전용 리그에서만 사용,
# 그 외(대부분)는 아래 generate_round_robin()으로 팀 수에 맞게 생성.
ROUND_MATCHES = [
    [(0,7),(1,6),(2,5),(3,4)],
    [(0,6),(7,5),(1,4),(2,3)],
    [(0,5),(6,4),(7,3),(1,2)],
    [(0,4),(5,3),(6,2),(7,1)],
    [(0,3),(4,2),(5,1),(6,7)],
    [(0,2),(3,1),(4,7),(5,6)],
    [(0,1),(2,7),(3,6),(4,5)],
]

# ── 팀 수 무관 라운드로빈 대진표 생성기 (원형법 / circle method) ──────────
# n(팀 수)이 짝수면 n-1라운드 × n/2경기, 홀수면 n라운드 × (n-1)/2경기(매 라운드 1팀 부전승).
# 반환값: [[(idx_a, idx_b), ...], ...] (0-based 팀 인덱스). 팀 수는 최대 30 기준.
def generate_round_robin(n: int):
    """n팀에 대한 더블 라운드로빈의 '상반기(편도)' 라운드 구성을 생성.
    n < 2 면 빈 리스트 반환."""
    if n < 2:
        return []
    teams = list(range(n))
    bye = None
    if n % 2 == 1:
        teams.append(bye)  # 부전승 자리
    m = len(teams)
    rounds = []
    for _ in range(m - 1):
        pairs = []
        for i in range(m // 2):
            a, b = teams[i], teams[m - 1 - i]
            if a is not bye and b is not bye:
                pairs.append((a, b))
        rounds.append(pairs)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]  # 첫 팀 고정, 나머지 회전
    return rounds

# ── 일 단위 캘린더 헬퍼 ────────────────────────────────────────
# 팀 수가 리그마다 8~30(짝수)로 달라지면서 라운드 수(n-1)도 7~29로 달라진다.
# 리그별 라운드 수에 맞춰 상/하반기 구간(day) 안에 라운드를 균등 분배한다.
#   - 팀 8개  → 7라운드  → 139일/6≈23.2일 간격
#   - 팀 20개 → 19라운드 → 139일/18≈7.7일 간격
#   - 팀 30개 → 29라운드 → 139일/28≈5.0일 간격
# 팀이 많은(=상위) 리그일수록 실제 프로리그처럼 경기가 잦아지고,
# 팀이 적은 리그는 널널해진다 — 별도 튜닝 없이 팀 수만으로 자연스럽게 나옴.
#
# week 컬럼과의 관계: day → week 는 항상 (day-1)//7 + 1 로 역산 가능하게
# 맞춰뒀다. 그래서 _sim_all_ai_matches 등 기존 'WHERE week=?' 로 매치를
# 찾는 코드는 전혀 손대지 않아도 된다 — 한 주(week)에 라운드가 여러 개
# 몰리는 리그(팀 30개 등)도 그 주가 시뮬레이션될 때 한꺼번에 처리된다.
DAYS_PER_WEEK = 7
FIRST_HALF_START_DAY  = CLUB_SEASON_START_DAY        # 22
FIRST_HALF_END_DAY    = CLUB_SEASON_MID_DAY - 1      # 160
SECOND_HALF_START_DAY = CLUB_SEASON_MID_DAY          # 161
SECOND_HALF_END_DAY   = CLUB_SEASON_END_DAY          # 300

def round_to_day(rd: int, rounds_total: int, half_start_day: int, half_end_day: int,
                  offset: int = 0) -> int:
    """편도 라운드 인덱스(rd, 0-based)를 half_start_day~half_end_day 구간 안의
    날짜로 배치. rounds_total=1이면 구간 시작일 그대로 반환.

    [2026-07 수정] 이전엔 라운드 수와 무관하게 구간 전체(half_start~half_end)에
    '균등 분배'했다 — 그래서 팀이 적은 리그(예: 8팀=7라운드)는 라운드 간격이
    23일까지 벌어지는 등, 실제 축구와 전혀 다르게 한 달에 한 번꼴로만 경기가
    있는 비현실적인 일정이 나왔다(현실은 라운드 수와 무관하게 거의 매주 한
    경기씩). 이제는 간격을 최대 1주(7일)로 캡 씌운다:
      - 라운드가 적은(=팀 적은) 리그는 매주(7일 간격)로 경기하다가 그 시즌
        전반부 안에서 일찍 끝난다 (구간을 억지로 다 채우지 않음).
      - 라운드가 많은(=팀 많은) 리그, 예: 30팀=29라운드는 여전히 구간 전체를
        거의 다 채워야 해서 간격이 7일보다 좁아진다(약 5일 — 미드위크 경기가
        섞인 상위 리그처럼 촘촘한 일정, 기존과 동일).

    [2026-07 추가] offset — 대부분의 리그가 interval=7(1주 캡)로 수렴하다
    보니, half_start_day가 전 세계 모든 리그에 공유되는 하나의 상수라서
    사실상 '지구상의 모든 리그가 매주 정확히 같은 날짜에 개막/라운드를
    치른다'는 비현실적인 결과가 나왔다(예: 22일에 전 세계 동시 개막, 이후
    29일/36일/... 도 전부 동일). offset(보통 리그별 0~6 고정값)을 더해
    리그마다 그 리그의 '고정 요일'을 며칠 어긋나게 만든다 — 간격 패턴
    자체(매주 1회)는 그대로 유지하면서, 리그마다 실제로 다른 날짜에
    경기가 열리게 분산시킨다.

    [2026-07 버그 수정] offset을 마지막에 그냥 더하기만 하면(구간 폭은
    그대로 두고 매 라운드 날짜에 +offset), 라운드 수가 많아 간격이 안
    잘리는 리그(interval이 캡에 안 걸리는 경우)의 '마지막 라운드'가
    half_end_day를 offset만큼 넘어가버렸다 — 그러면 그 다음 다리(leg)의
    첫 라운드 날짜 구간을 침범해서 같은 팀이 겹쳐 뛰는 충돌이 생겼다.
    이제는 offset을 구간 '시작점'에 반영해서(eff_start = half_start_day
    + offset) 구간을 그만큼 뒤에서 시작하도록 하고, 구간 '끝점'은
    half_end_day 그대로 고정한다 — 간격(interval)이 이 줄어든 구간
    기준으로 재계산되므로, 라운드가 몇 개든 offset이 몇이든 마지막
    라운드는 항상 half_end_day를 넘지 않는다(수학적으로 보장됨).
    """
    eff_start = half_start_day + offset
    if rounds_total <= 1:
        return eff_start
    even_interval = (half_end_day - eff_start) / (rounds_total - 1)
    interval = min(DAYS_PER_WEEK, even_interval)
    return eff_start + round(rd * interval)


def league_day_offset(league_id: int) -> int:
    """리그별 고정 요일 오프셋(0~DAYS_PER_WEEK-1). 리그 id 기반 결정론적 값이라
    같은 리그는 시즌이 바뀌어도 항상 같은 오프셋을 받는다(재현 가능성 유지).
    소수(7과 서로소인 3)를 곱해 인접한 league_id끼리도 값이 뭉치지 않게 한다.
    """
    return (int(league_id) * 3) % DAYS_PER_WEEK


# ── 라운드 내부 요일 분산 (2026-07 추가) ────────────────────────
# 실제 프리미어리그도 "1라운드"가 하루에 다 열리지 않는다 — 토요일에 몇
# 경기, 일요일에 몇 경기, 월요일 나이트게임 하나 이런 식으로 한 라운드가
# 보통 토~화 사이 4일 정도에 걸쳐 흩어진다. round_to_day()는 그 라운드의
# '기준일'만 잡아주므로, 실제 경기별 날짜는 이 함수로 그 기준일 근처
# 며칠에 걸쳐 흩뿌린다.
def round_match_days(rd: int, rounds_total: int, half_start_day: int, half_end_day: int,
                      n_matches: int, offset: int = 0, max_spread: int = 4) -> list:
    """한 라운드에 속한 n_matches개 경기 각각의 날짜 리스트(경기 순서 그대로)를
    반환한다. 기준일(round_to_day)부터 최대 max_spread일 안에 고르게 분산.
    다음 라운드 시작일을 침범하지 않도록, 라운드 간 간격(interval)보다 짧게
    스프레드를 자동으로 줄인다(라운드가 촘촘한 대형 리그일수록 스프레드가
    좁아짐 — 상위 리그가 미드위크 경기까지 섞여 촘촘한 것과 비슷한 느낌).

    [2026-07 버그 수정] 마지막 라운드(rd == rounds_total-1)는 기준일 자체가
    이미 half_end_day에 정확히 맞춰져 있는데, 거기에 스프레드를 더하면
    half_end_day를 넘어 '다음 다리(leg)'의 첫 라운드 날짜 구간까지
    침범했다 — 그 결과 같은 팀이 (이번 다리 마지막 라운드 상대)와
    (다음 다리 첫 라운드 상대)를 같은 날 동시에 뛰어야 하는 겹침이
    생겼다. half_start_day~half_end_day 범위로 항상 클램프해서 방지한다
    (막판 며칠 매치가 half_end_day 하루에 살짝 더 몰릴 수는 있지만,
    경계를 넘어 다음 다리와 충돌하는 것보다는 훨씬 안전하다).
    """
    base_day = round_to_day(rd, rounds_total, half_start_day, half_end_day, offset=offset)
    if n_matches <= 1:
        return [min(max(base_day, half_start_day), half_end_day)] * max(n_matches, 0)
    if rounds_total <= 1:
        interval = max_spread
    else:
        eff_start = half_start_day + offset
        even_interval = (half_end_day - eff_start) / (rounds_total - 1)
        interval = min(DAYS_PER_WEEK, even_interval)
    # 다음 라운드 기준일을 절대 못 넘게: 스프레드 폭은 interval보다 항상 좁게.
    # [2026-07 버그 수정] int(interval)을 그대로 스프레드 상한으로 쓰면, 라운드
    # 간격이 소수(예: 4.93일, 팀 30개 같은 대형 리그)일 때 누적 반올림
    # 오차로 이번 라운드의 스프레드 끝자락이 바로 다음 라운드 시작일과
    # 맞닿거나 겹치는 경우가 실제로 있었다(같은 팀이 이틀 연속 겹쳐 뛰는
    # 버그로 이어짐). 안전 마진 1일을 항상 남겨서 방지한다.
    spread = max(1, min(max_spread, n_matches, int(interval) - 1))
    days = [base_day + (i * spread) // n_matches for i in range(n_matches)]
    return [min(max(d, half_start_day), half_end_day) for d in days]


def final_round_day(rd_second_last: int, rounds_total: int, half_start_day: int, half_end_day: int,
                     n_matches_second_last: int, offset: int = 0, max_spread: int = 4,
                     buffer_days: int = 2) -> int:
    """시즌 마지막 라운드는 전 구단이 한날한시에 치러야 공정하다(순위/강등이
    걸린 마지막 라운드에 다른 경기 결과를 보고 뛸 수 있으면 안 됨) — 그래서
    스프레드 없이 단 하루로 고정한다. 그 하루는 '모든 팀이 (마지막 라운드
    직전) 자기 경기를 1개씩만 남긴 시점' — 즉 마지막에서 두 번째 라운드가
    스프레드로 인해 가장 늦게 끝나는 날 — 로부터 buffer_days일 뒤로 잡는다.
    이렇게 하면 그 라운드에서 가장 늦게(스프레드 마지막 날) 뛴 팀도 최소
    buffer_days일은 쉬고 마지막 라운드를 맞이한다(이틀 연속 경기 방지)."""
    prev_days = round_match_days(rd_second_last, rounds_total, half_start_day, half_end_day,
                                  n_matches_second_last, offset=offset, max_spread=max_spread)
    last_day_all_have_one_left = max(prev_days) if prev_days else half_start_day
    return last_day_all_have_one_left + buffer_days


# ── 소규모 리그 "다전제" 확장 (2026-07 추가) ────────────────────
# [문제] 기존엔 모든 리그가 팀 수와 무관하게 왕복 2전(더블 라운드로빈)
# 하나뿐이었다. 라운드 간격을 최대 1주(7일)로 캡 씌우다 보니(비현실적인
# 한 달 간격 방지), 라운드 자체가 적은 소규모 리그(예: 8팀=7라운드)는
# 시즌 전체가 훨씬 일찍 끝나버렸다 — 30팀 리그가 5월까지 하는데 8팀
# 리그는 2월에 끝나는 식으로 3개월 넘게 격차가 났다.
# [해법] 실제 K리그1(12개 팀, 서로 3번씩 붙어 33라운드)처럼, 팀이 적을수록
# 서로 더 여러 번(다전제) 붙게 한다. 기존 '왕복 2전' 구조(상반기 1다리 +
# 하반기 반전 1다리)를 그대로 '사이클' 단위로 재사용 — 시즌 전체 기간을
# N개 사이클로 나누고, 사이클 하나당 기존 왕복 2전을 통째로 반복한다.
# 그래서 새 코드를 안 만들고 기존 라운드 스프레드·오프셋·마지막 라운드
# 동시진행 로직을 사이클마다 그대로 재사용할 수 있다.
TARGET_ROUNDS_PER_LEG = 19   # 20팀 리그의 라운드 수(=19)를 기준점으로 삼음

def legs_for_team_count(n: int, target: int = TARGET_ROUNDS_PER_LEG) -> int:
    """총 맞대결 횟수(짝수만 — 기존 '왕복 2전 사이클' 구조를 그대로 반복
    재사용하기 위해). 팀이 적을수록 커진다.
    예: 8팀→6전(K리그보다 살짝 많음, 30팀과 시즌 길이 맞추려면 이 정도 필요),
        12팀→4전, 20팀 이상→2전(기존과 동일, 변화 없음)."""
    if n <= 1:
        return 2
    raw = round(target / (n - 1))
    cycles = max(1, raw)
    return cycles * 2

def season_cycle_windows(n_cycles: int):
    """CLUB_SEASON_START_DAY~END_DAY 전체를 n_cycles개 구간으로 균등 분할해,
    구간별 (h1_start, h1_end, h2_start, h2_end) — 그 사이클의 '왕복 2전'용
    상반기/하반기 day 윈도우 — 리스트를 반환한다. 사이클이 1개면 기존
    FIRST_HALF_*/SECOND_HALF_* 윈도우와 동일한 값이 나온다(하위호환).

    [2026-07 버그 수정] 예전엔 사이클 i의 끝(c_end)과 사이클 i+1의 시작
    (c_start)이 같은 날짜였다(경계를 그냥 나눠 쓰기만 함) — 그런데 각
    사이클의 마지막 라운드는 그 사이클 구간 끝날에 정확히 맞춰지도록
    설계돼 있어서, 사이클 i의 마지막 라운드와 사이클 i+1의 첫 라운드가
    똑같이 그 경계일 하루에 몰려 같은 팀이 겹쳐 뛰는 충돌이 났다. 이제
    첫 사이클을 뺀 나머지는 이전 사이클 끝난 다음날부터 시작해서 경계일이
    안 겹치게 한다."""
    total = CLUB_SEASON_END_DAY - CLUB_SEASON_START_DAY
    span = total / n_cycles
    breakpoints = [CLUB_SEASON_START_DAY + round(i * span) for i in range(n_cycles + 1)]
    breakpoints[-1] = CLUB_SEASON_END_DAY  # 마지막 경계는 항상 정확히 시즌 종료일
    windows = []
    for i in range(n_cycles):
        c_start = breakpoints[i] if i == 0 else breakpoints[i] + 1
        c_end   = breakpoints[i + 1]
        mid = (c_start + c_end) // 2
        windows.append((c_start, mid - 1, mid, c_end))
    return windows

def day_to_week(day: int) -> int:
    """일자를 기존 week 체계로 역산 (1~52로 클램프)."""
    w = (day - 1) // DAYS_PER_WEEK + 1
    return max(1, min(52, w))

# ── 실제 달력(월/일) 표시용 ────────────────────────────────────
# [2026-07 수정] 1일차 = 8월 1일로 했던 걸 1월 1일로 되돌렸다 — 시즌
# 진행(day 1~364)이 그대로 그 시즌 연도(season_year) 하나 안에 전부
# 들어가서, 연도 넘어가는 계산(day_to_calendar_year_offset)이 필요 없어져
# 더 깔끔하다(요청: "1주차를 2000-01-01으로, 날짜만 달라지고 하루하루
# 진행되는 로직은 동일"). 게임 내부 로직(day/week, CLUB_SEASON_START_DAY
# 등 시즌 구간 상수)은 이 표시와 완전히 무관하게 그대로 1~364 정수로
# 동작 — 이건 순수 화면 표시용 변환일 뿐이다.
_CALENDAR_MONTH_ORDER  = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
_CALENDAR_MONTH_LENGTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 30]  # 합계 364(12월만 30일로 1일 축소)

def day_to_calendar_date(day: int):
    """day(1~364) → (월, 일) 튜플. 1월 1일 = 1일차."""
    d = ((day - 1) % 364)
    for month, length in zip(_CALENDAR_MONTH_ORDER, _CALENDAR_MONTH_LENGTH):
        if d < length:
            return (month, d + 1)
        d -= length
    return (7, 30)   # 방어적 폴백 (도달할 일 없음)

def day_to_date_str(day: int) -> str:
    """day → '8월 1일' 형식 문자열."""
    m, d = day_to_calendar_date(day)
    return f"{m}월 {d}일"

# [2026-07 수정] 이제 1일차=1월 1일이라 시즌(1~364일) 전체가 항상
# season_year 한 해 안에 다 들어간다 — 그래서 연도 보정이 더는 필요 없다
# (예전엔 8월 시작이라 1~7월 구간에 +1년이 필요했음). 함수는 다른 코드와의
# 호환을 위해 그대로 남겨두되 항상 0을 반환한다.
def day_to_calendar_year_offset(day: int) -> int:
    """항상 0 — 시즌이 1월 1일에 시작해 그 해 안에서 끝나므로 연도 보정 불필요."""
    return 0

def day_to_full_date_str(season_year: int, day: int) -> str:
    """day → 'season_year년 월 일' 전체 문자열."""
    yr = season_year + day_to_calendar_year_offset(day)
    return f"{yr}년 {day_to_date_str(day)}"

def day_to_iso_date_str(season_year: int, day: int) -> str:
    """day → 'YYYY-MM-DD' 형식 문자열. 커리어/은퇴창 등에서 'N주차' 대신
    실제 날짜를 보여줄 때 쓴다 (2026-07 신설, 신민용 요청)."""
    yr = season_year + day_to_calendar_year_offset(day)
    m, d = day_to_calendar_date(day)
    return f"{yr:04d}-{m:02d}-{d:02d}"

def week_to_iso_date_str(season_year: int, week: int) -> str:
    """day 컬럼이 없는(구버전 세이브 등) 경기 기록을 위한 폴백 —
    그 주의 첫째 날로 근사한 날짜를 반환한다."""
    day = (week - 1) * DAYS_PER_WEEK + 1
    return day_to_iso_date_str(season_year, day)

# ── 기존(주 단위) 코드와의 호환용 파생값 ──────────────────────────
# game_engine.py 등 아직 'week' 정수로 시즌 구간을 비교하는 코드가 많아서,
# 위 day 상수들로부터 주차를 역산해 그대로 제공한다. 이 값들 자체를
# 직접 바꾸지 말고 위 *_DAY 상수를 바꾸면 여기로 자동 반영된다.
FIRST_HALF_START  = day_to_week(CLUB_SEASON_START_DAY)   # 4주
SECOND_HALF_START = day_to_week(CLUB_SEASON_MID_DAY)     # 23주

SEASON_PHASES = {
    "preseason1":  (day_to_week(CLUB_PRESEASON_START_DAY), day_to_week(CLUB_PRESEASON_END_DAY)),
    "first_half":  (FIRST_HALF_START, day_to_week(FIRST_HALF_END_DAY)),
    "second_half": (SECOND_HALF_START, day_to_week(CLUB_SEASON_END_DAY)),
    "postseason":  (day_to_week(INTL_OFFSEASON_START_DAY), 52),   # = 국제대회 전용 구간
}

# 오퍼(이적시장) 구간 — 실제 축구처럼 딱 2개(여름/겨울)만. 자동 오퍼 팝업은
# 이 구간 안(in_zone)일 때만 뜬다 (ui/center_panel.py 참고).
OFFER_ZONES = [
    (day_to_week(CLUB_PRESEASON_START_DAY), day_to_week(CLUB_PRESEASON_END_DAY)),  # 여름(프리시즌)
    (day_to_week(WINTER_OFFER_START_DAY),   day_to_week(WINTER_OFFER_END_DAY)),    # 겨울
]

# 국제대회 윈도우 — 클럽 시즌과 완전히 안 겹치는 전용 비시즌(301~364일=44~52주)
# 전체를 사용. 월드컵은 조별리그+토너먼트, 대륙컵/예선도 전부 이 안에서 진행.
INTL_OFFSEASON_WEEK_START = day_to_week(INTL_OFFSEASON_START_DAY)   # 44
INTL_OFFSEASON_WEEK_END   = 52
INTL_CALLUP_WEEK  = INTL_OFFSEASON_WEEK_START                        # 소집/조 추첨
INTL_GROUP_WEEKS  = (INTL_OFFSEASON_WEEK_START + 1, INTL_OFFSEASON_WEEK_START + 3)   # 조별리그 3경기
INTL_KO_WEEKS     = (INTL_OFFSEASON_WEEK_START + 4, INTL_OFFSEASON_WEEK_END)          # 16강~결승


# 훈련 설정
# gain_min/max: 일반훈련(중/저/집중)은 소프트캡과 함께 점진 성장하도록 하향.
#               고강도(exceed_limit=True)는 max~talent_cap 돌파용이라 강하게 유지.
# [일 단위 전환 임시조치 — 2026-07] 예전엔 훈련 1회 = 1주(또는 4주) 단위였는데,
# 앞으로 훈련이 하루 단위로 훨씬 자주(주 7회) 일어나게 되므로, 같은 배율을 그대로
# 두면 성장이 약 7배 빨라진다. 정확한 일 단위 성장 곡선은 캘린더 설계가 끝난 뒤
# 다시 잡을 예정이라, 우선 gain_min/gain_max만 기존 값의 1/7로 낮춰 둔다.
# 스트레스(stress)/부상확률(injury_chance)은 설계 확정 전까지 기존 값 그대로 유지.
_TRAIN_GAIN_SCALE = 1 / 7

TRAINING_CONFIG = {
    "고강도":   {"stress":+20, "injury_chance":0.05, "gain_min":4.0 * _TRAIN_GAIN_SCALE, "gain_max":5.5 * _TRAIN_GAIN_SCALE, "exceed_limit":True},  # [2026-07] 부상확률 0.20→0.10 (요청: 1/2로 낮춤)
    "강점훈련": {"stress":+16, "injury_chance":0.00, "gain_min":3.3 * _TRAIN_GAIN_SCALE, "gain_max":4.6 * _TRAIN_GAIN_SCALE, "exceed_limit":False, "focus_mode":"strong"},
    "약점훈련": {"stress":+16, "injury_chance":0.00, "gain_min":3.3 * _TRAIN_GAIN_SCALE, "gain_max":4.6 * _TRAIN_GAIN_SCALE, "exceed_limit":False, "focus_mode":"weak"},
    "중강도":   {"stress":+15, "injury_chance":0.00, "gain_min":2.0 * _TRAIN_GAIN_SCALE, "gain_max":3.0 * _TRAIN_GAIN_SCALE, "exceed_limit":False},
    "저강도":   {"stress":+ 8, "injury_chance":0.00, "gain_min":1.1 * _TRAIN_GAIN_SCALE, "gain_max":1.8 * _TRAIN_GAIN_SCALE, "exceed_limit":False},
    "휴식":     {"stress":-15, "injury_chance":0.00, "gain_min":-1 * _TRAIN_GAIN_SCALE,  "gain_max":-1 * _TRAIN_GAIN_SCALE,  "exceed_limit":False},
}

# 소프트캡: 일반훈련 시 max에 가까울수록 상승폭 둔화 (분모 클수록 완만)
SOFTCAP_DENOM = 40.0
SOFTCAP_FLOOR = 0.10

# 강점/약점 집중훈련: max 도달 후 talent_cap까지 한계 돌파 확률.
# 고강도(상시 돌파)와 달리 가끔만 돌파한다. 두 모드 동일 — 차이는 '타겟 스탯'뿐.
#   - 강점훈련: 한계치(_max)가 높은 스탯을 집중해서 그 한계까지 채움
#   - 약점훈련: 한계치가 낮은 스탯을 집중해서 그 한계까지 채움
FOCUS_BREAK_PROB_STRONG = 0.05
FOCUS_BREAK_PROB_WEAK   = 0.05
FOCUS_BREAK_PROB        = 0.05

# 고강도 훈련: _max 도달 후 한 번 훈련 시 _max를 +1 끌어올릴 확률.
#   집중훈련(5%)보다 높게 둬서 고강도가 한계 돌파의 주력 트랙임을 분명히 한다.
HIGH_BREAK_PROB = 0.40

# 재능 등급별 고강도 돌파 상한 (talent_cap). 일반훈련 max와는 별개의 천장.
# 부상 없이 고강도를 꾸준히 하면 이 값까지 개별 스탯을 올릴 수 있음.
#   이 cap은 '개별 스탯이 고강도 돌파로 도달 가능한 평균적 천장'이자
#   전성기 OVR 의 목표 범위이기도 하다 (강점은 cap+α로 100 초과 가능,
#   약점은 cap 아래라 평균은 cap 부근에서 균형).
#   5등급 체계:
#   - 월드클래스(worldclass): 전성기 OVR 96~100, 강점 스탯 고강도로 100+ 가능
#   - 엘리트(elite):          전성기 OVR 88~95
#   - 프로(pro):              전성기 OVR 78~87
#   - 세미프로(semipro):      전성기 OVR 69~77
#   - 평범(ordinary):         전성기 OVR 60~68 (아마추어 느낌, 선수는 됐지만 별 볼 일 없음)
TALENT_TIERS = {
    # [2026-07 재조정] 사용자 판단으로 전체 캡을 한 단계씩 낮춤 — 기존엔
    # "평범"조차 최대 68까지 갈 수 있어서 현실의 세미프로~아마추어 체감과
    # 어긋났다. K4리그(한국에서 실제로 "세미프로"급으로 불리는 리그)가
    # 대략 세미프로 캡(60대 후반) 근처에 오도록 OVR_RANGES도 같이 맞췄다.
    "worldclass": {"prob": 0.15, "cap_min": 95, "cap_max": 100},
    "elite":      {"prob": 0.20, "cap_min": 81, "cap_max": 94},
    "pro":        {"prob": 0.30, "cap_min": 69, "cap_max": 80},
    "semipro":    {"prob": 0.25, "cap_min": 59, "cap_max": 68},
    "ordinary":   {"prob": 0.10, "cap_min": 48, "cap_max": 58},
}

# [신규] 재능 등급 한글/영문 표시명 — 새 게임 화면의 등급 선택 콤보박스와
# 선수 패널의 뱃지가 이 하나의 표를 공유한다(표시 문구가 여러 곳에서
# 따로 하드코딩되어 서로 어긋나는 걸 방지).
TALENT_TIER_KO = {
    "worldclass": "월드클래스", "elite": "엘리트", "pro": "프로",
    "semipro": "세미프로", "ordinary": "평범",
}
TALENT_TIER_EN = {
    "worldclass": "World Class", "elite": "Elite", "pro": "Pro",
    "semipro": "Semi-Pro", "ordinary": "Ordinary",
}
# 새 게임 화면 콤보박스에 보여줄 순서(강한 순).
TALENT_TIER_ORDER = ["worldclass", "elite", "pro", "semipro", "ordinary"]

# (구버전 호환) 예전 티어명을 새 티어로 매핑
_LEGACY_TALENT_ALIAS = {
    "gifted": "worldclass", "mid": "elite",
    "normal": "pro", "limited": "semipro",
}

MATCH_STRESS = +20
MATCH_STAT_GAIN_MIN = 1
MATCH_STAT_GAIN_MAX = 2

# 슬럼프
SLUMP_STRESS_THRESHOLD   = 60
SLUMP_HAPPY_THRESHOLD     = 20
SLUMP_CHANCE             = 0.50
SLUMP_RECOVER_STRESS     = 40
SLUMP_TRAIN_PENALTY      = 0.50  # 슬럼프 시 모든 훈련 효율 50% 감소
SLUMP_RATING_PENALTY     = -1.0

# [행복도 연동] 행복도가 낮으면 스트레스가 60에 못 미쳐도(40 이상) 슬럼프 가능.
#   - 행복도가 LOW_HAPPY 이하이면 슬럼프 스트레스 임계치를 60 → 40으로 낮춘다.
#   - 단 이 저행복 구간 발동 확률은 정규 구간보다 낮게(스트레스 60+ 만큼 흔하진 않게).
SLUMP_LOW_HAPPY          = 35   # 행복도 이 값 이하면 '저행복 슬럼프' 구간 진입
SLUMP_LOW_HAPPY_STRESS   = 40   # 저행복일 때 적용되는 낮춘 스트레스 임계치
SLUMP_LOW_HAPPY_CHANCE   = 0.30 # 저행복 구간(스트레스 40~59)에서의 슬럼프 발동 확률

# 부상
# [2026-07 확장] 예전엔 등급(경미/중간/심각) 하나로만 뭉뚱그려서 "부상"이라고만
# 뜨고 회복 기간도 등급 통짜 범위(예: 심각=5~6주)에서 균등하게 뽑았다 — 실제
# 축구 부상은 등급이 같아도 구체적으로 뭐가 다쳤냐에 따라 회복 기간 편차가
# 크다(발목 염좌 2주 vs 십자인대 파열 30주는 둘 다 '심각'으로 뭉치면 안 됨).
# 그래서 등급별로 여러 '구체 부상'을 두고, 그 구체 부상마다 자기 회복 기간
# 범위를 따로 갖게 세분화했다. INJURY_TYPES(등급→주수 범위)는 기존 코드
# 호환용으로 그대로 두고(등급 자체의 대략적 스펙트럼 표시용), 실제 부상 발생
# 시엔 아래 INJURY_DETAILS에서 등급 안의 구체 부상 하나를 더 골라 그 부상
# 고유의 좁은 회복 범위로 주수를 정한다.
INJURY_TYPES = {
    "경미":     (1, 2),
    "중간":     (3, 5),
    "심각":     (6, 10),
    "매우 심각": (16, 32),
}

# 등급 → [(구체 부상명, (최소주, 최대주)), ...]. 등급 안에서 균등 확률로 하나 선택.
INJURY_DETAILS = {
    "경미": [
        ("타박상",          (1, 1)),
        ("근육 경직",        (1, 1)),
        ("경미한 발목 염좌",  (1, 2)),
        ("경미한 무릎 타박상", (1, 2)),
    ],
    "중간": [
        ("햄스트링 경미 손상",   (3, 4)),
        ("발목 인대 염좌",      (3, 4)),
        ("종아리 근육 파열(경도)", (3, 5)),
        ("무릎 염좌",          (4, 5)),
    ],
    "심각": [
        ("햄스트링 심각 손상",   (6, 8)),
        ("발목 인대 파열",      (6, 8)),
        ("반월판 부분 손상",     (7, 9)),
        ("피로 골절",          (8, 10)),
    ],
    "매우 심각": [
        ("전방십자인대(ACL) 파열", (24, 32)),
        ("아킬레스건 파열",       (20, 28)),
        ("경골/비골 골절",       (16, 24)),
    ],
}

# 등급별 발생 확률. 예전(60/30/10) 대비 '매우 심각' 신설분만큼 심각을 살짝 줄임.
INJURY_TIER_CHANCE = {
    "경미": 0.60, "중간": 0.28, "심각": 0.10, "매우 심각": 0.02,
}

# 성격
PERSONALITY_EFFECTS = {
    "성실함":   {"train_eff": 1.20},
    "게으름":   {"train_eff": 0.80},
    "냉철함":   {"stress_mult": 0.90},
    "긍정적":   {"happy_gain_mult": 1.15},
    "소심함":   {"big_match_rating": -0.3},
    "승부욕":   {"losing_rating": +0.3},
    "리더십":   {"team_win_bonus": 0.03},
    "폭력적":   {"red_card_chance": 0.05},
    "완벽주의": {"high_train_bonus": 1.10, "low_train_penalty": 0.90},
    "멘탈갑":   {"slump_chance_mult": 0.70},
    "겁쟁이":   {"cup_rating": -0.5},
    # 성격 천재: 멘탈 스탯 성장 + 자연 성장 보너스 (재능이 '머리/정신'에서 옴)
    "천재":     {"natural_growth_bonus": 0.20, "mental_growth_mult": 1.25},
    # 멘탈 계열
    "강철멘탈": {"no_slump": True},                          # 슬럼프 면역
    "유리멘탈": {"slump_threshold_reduce": 20,
                 "slump_chance_add": 0.30},                  # 40 이상부터 발동, 60+ 확률+30%
    "훈련광":   {"train_eff": 1.20, "stress_mult": 1.10},    # 훈련효율+20%, 스트레스도 10% 더 쌓임
}
PERSONALITIES = list(PERSONALITY_EFFECTS.keys())

# ════════════════════════════════════════════════════════════════
# 신체 특징 (physical trait) — 성격과 별개. 선수는 특징 1개를 가진다.
#   '부상체질/강철체질'처럼 체질·신체 계열은 성격이 아니라 여기로 분리.
#   '무난함'은 특별한 특징이 없는 평범한 신체(가중치 높음).
# ════════════════════════════════════════════════════════════════
PHYSICAL_TRAIT_EFFECTS = {
    "무난함":     {},                                         # 특징 없음(평범)
    "부상체질":   {"injury_add": +0.10},                      # 부상 확률 +10%
    "강철체질":   {"injury_add": -1.0, "stamina_train": 1.15},# 부상 완전 면역 + 체력훈련+15%
    "지구력형":   {"stress_mult": 0.85},                      # 스트레스 덜 쌓임(체력 좋음)
    "스피드스타": {"phys_growth_mult": 1.20, "phys_stat": "speed"},  # 스피드 성장↑
    "피지컬몬스터":{"phys_growth_mult": 1.15},                 # 신체 스탯 전반 성장↑
    # 신체 천재: 초반 신체 스탯이 높게 생성 + 신체 성장 보너스
    "신체천재":   {"phys_start_bonus": +8, "phys_growth_mult": 1.20},
}
PHYSICAL_TRAITS = list(PHYSICAL_TRAIT_EFFECTS.keys())
# 등장 가중치: 무난함이 흔하고, 천재/몬스터는 희귀
PHYSICAL_TRAIT_WEIGHTS = [34, 12, 8, 14, 12, 10, 10]

# ──────────────────────────────────────────────────────────────
# [신체 아키타입] 체형 유형. PHYSICAL_TRAIT(부상/성장 특성)와는 별개의 축.
#   - 선수의 키/체중을 결정하고, 일부 스탯을 ±로 보정한다(현실적 ±5~8).
#   - 포지션이 어떤 타입이 나올지 '확률을 기울이되' 고정하진 않는다.
#     → 윙어인데 포켓로켓(메시형)이 나오거나, 작은데 종결자 체급(트라오레)도
#       드물게 가능. 현실의 다양성을 재현.
#
# 스탯 보정(stat_bias)은 '시작 스탯 + 잠재(max) 양쪽'에 더해진다.
#   양수 = 그 스탯이 또래보다 높게 시작/성장, 음수 = 낮게.
#   몸싸움 계열(strength/heading/jump)은 크게, 부차 스탯은 작게 둬서
#   "키 작으면 몸싸움 밀린다"가 분명히 체감되되 개성은 유지되게 한다.
# ──────────────────────────────────────────────────────────────
BODY_TYPES = {
    "하드웨어 종결자형": {
        "desc": "압도적 체격으로 육체적으로 제압. 포스트플레이·제공권.",
        "height": (186, 196),
        "weight": (84, 100),
        "stat_bias": {
            "strength": +8, "heading": +7, "jump": +6,
            "speed": -5, "dribbling": -5, "stamina": -2,
        },
    },
    "음속 지배자형": {
        "desc": "폭발적인 속도로 측면을 파괴. 치고 달리기·역습.",
        "height": (172, 186),
        "weight": (66, 76),
        "stat_bias": {
            "speed": +8, "stamina": +4, "dribbling": +3,
            "strength": -5, "heading": -4, "jump": -2,
        },
    },
    "포켓 로켓형": {
        "desc": "작지만 단단하고 민첩. 좁은 공간 탈압박·방향전환.",
        "height": (165, 175),
        "weight": (63, 73),
        "stat_bias": {
            "dribbling": +8, "speed": +4, "setpiece": +2,
            "strength": -6, "heading": -7, "jump": -5,
        },
    },
    "인간 발전기형": {
        "desc": "공수 양면 활동량과 밸런스. 육각형 미드필더.",
        "height": (175, 185),
        "weight": (70, 80),
        "stat_bias": {
            "stamina": +7, "passing": +3, "tackling": +2,
            "confidence": +1,   # 큰 약점 없이 골고루(보정폭 작게)
        },
    },
}
BODY_TYPE_NAMES = list(BODY_TYPES.keys())

# 포지션별 아키타입 등장 확률(가중치). 합이 100이 아니어도 됨(상대 비율).
#   정석 타입에 무게를 싣되, 다른 타입도 0이 아니게 둬서 이질적 선수를 허용한다.
#   순서: [종결자, 음속, 포켓로켓, 발전기]
BODY_TYPE_WEIGHTS_BY_POS = {
    "GK":  [55, 10,  5, 30],   # 키 큰 편
    "CB":  [60, 10,  3, 27],   # 종결자 다수
    "LB":  [10, 55, 20, 15],   # 측면=음속
    "RB":  [10, 55, 20, 15],
    "CDM": [30, 10, 10, 50],   # 발전기 다수
    "CM":  [12, 13, 20, 55],   # 발전기 중심
    "CAM": [ 6, 14, 50, 30],   # 포켓로켓(창의형) 많음
    "LW":  [ 8, 52, 30, 10],   # 음속/포켓로켓
    "RW":  [ 8, 52, 30, 10],
    "CF":  [40, 18, 17, 25],   # 타깃맨~섀도우 다양
    "ST":  [48, 22, 12, 18],   # 종결자(타깃맨) 우세하되 발빠른 9번도
}

# 에이전트
AGENT_GRADES = ["F","E","D","C","B","A","S"]
AGENT_FEE_RATE = {"F":0.00,"E":0.03,"D":0.06,"C":0.10,"B":0.15,"A":0.20,"S":0.28}
AGENT_UPPER_LEAGUE_BONUS = {"F":0,"E":1,"D":1,"C":2,"B":2,"A":3,"S":3}

# 포지션
POSITIONS = ["GK","CB","LB","RB","CDM","CM","CAM","LW","RW","CF","ST"]

# 포지션 그룹: 커리어/은퇴 지표를 포지션 성격에 맞게 보여주기 위한 분류.
#   - GK: 선방/실점/선방률/무실점이 핵심 (골·어시 무의미)
#   - 수비수(DEF): 무실점·실점·평점이 핵심, 골·어시는 보조
#   - 그 외(미드/공격): 골·어시·평점이 핵심
GK_POSITIONS  = ["GK"]
DEF_POSITIONS = ["CB", "LB", "RB", "CDM"]   # 중앙·측면 수비 + 수비형 미드
ATK_POSITIONS = ["CM", "CAM", "LW", "RW", "CF", "ST"]

def position_group(pos):
    """포지션 → 'GK' / 'DEF' / 'ATK' 그룹 반환."""
    if pos in GK_POSITIONS:
        return "GK"
    if pos in DEF_POSITIONS:
        return "DEF"
    return "ATK"

# 세부역할
SUB_ROLES = {
    # [2026-07 세분화] 포지션당 2개 → 3개로 확장. 새로 추가한 역할은
    # game_engine._SUB_ROLE_MATCH_MOD에도 대응 가중치를 같이 넣어야
    # 실제 경기에 반영된다(기존 2개는 이미 매치 반영이 검증됨).
    "GK":  ["스위퍼킵퍼","전통형","세이브전문형"],
    "CB":  ["볼플레잉","수비형","리베로"],
    "LB":  ["공격형","수비형","윙백"],
    "RB":  ["공격형","수비형","윙백"],
    "CDM": ["홀딩","박스투박스","딥라잉플레이메이커"],
    "CM":  ["박스투박스","플레이메이커","워크호스"],
    "CAM": ["섀도우","클래식","세컨드스트라이커"],
    "LW":  ["인버티드","클래식윙어","폴스윙어"],
    "RW":  ["인버티드","클래식윙어","폴스윙어"],
    "CF":  ["딥라잉","타깃형","폴스나인"],
    "ST":  ["포처","타깃형","올라운더"],
}

# 집중훈련 가능 스탯 (포지션별)
FOCUS_TRAIN_STATS = {
    "GK":  ["stamina","jump","positioning"],
    "CB":  ["tackling","heading","jump","stamina","positioning"],
    "LB":  ["tackling","speed","passing","stamina","positioning"],
    "RB":  ["tackling","speed","passing","stamina","positioning"],
    "CDM": ["tackling","passing","positioning","stamina"],
    "CM":  ["passing","dribbling","positioning","stamina","shooting"],
    "CAM": ["passing","dribbling","shooting","positioning","setpiece"],
    "LW":  ["dribbling","speed","shooting","passing","positioning"],
    "RW":  ["dribbling","speed","shooting","passing","positioning"],
    "CF":  ["shooting","heading","dribbling","positioning","passing"],
    "ST":  ["shooting","heading","jump","speed","positioning"],
}

# 포지션별 핵심(우선순위 높은) 기술 스탯 — 훈련 시 tech_pool에서 이 스탯 먼저 선택
PRIORITY_TECH_STATS = {
    "GK":  [],
    "CB":  ["tackling","heading"],
    "LB":  ["tackling","passing"],
    "RB":  ["tackling","passing"],
    "CDM": ["tackling","passing"],
    "CM":  ["passing","dribbling"],
    "CAM": ["passing","dribbling","shooting"],
    "LW":  ["dribbling","shooting"],
    "RW":  ["dribbling","shooting"],
    "CF":  ["shooting","heading"],
    "ST":  ["shooting","heading"],
}

ALL_STATS = [
    "stamina","speed","jump","strength","shooting","passing","dribbling",
    "tackling","heading","positioning","setpiece",
    "mental","confidence","leadership","concentration"
]

# 훈련으로 오르는 스탯 분류
PHYSICAL_STATS  = ["stamina","speed","jump","strength"]
TECHNICAL_STATS = ["shooting","passing","dribbling","tackling","heading","positioning","setpiece"]
MENTAL_STATS    = ["mental","confidence","leadership","concentration"]

STAT_KO = {
    "stamina":"체력","speed":"스피드","jump":"점프력","strength":"몸싸움",
    "shooting":"슈팅","passing":"패스","dribbling":"드리블",
    "tackling":"태클","heading":"헤딩","positioning":"포지셔닝",
    "setpiece":"세트피스","mental":"멘탈","confidence":"자신감",
    "leadership":"리더십","concentration":"집중력"
}
STAT_EN = {
    "stamina":"Stamina","speed":"Speed","jump":"Jump","strength":"Strength",
    "shooting":"Shooting","passing":"Passing","dribbling":"Dribbling",
    "tackling":"Tackling","heading":"Heading","positioning":"Positioning",
    "setpiece":"Set Piece","mental":"Mental","confidence":"Confidence",
    "leadership":"Leadership","concentration":"Concentration"
}

# 포메이션 포지션 목록
FORMATION_SLOTS = {
    "4-4-2":   ["GK","CB","CB","LB","RB","LM","CM","CM","RM","ST","ST"],
    "4-3-3":   ["GK","CB","CB","LB","RB","CDM","CM","CM","LW","RW","ST"],
    "3-5-2":   ["GK","CB","CB","CB","LWB","CDM","CM","CM","RWB","ST","ST"],
    "4-2-3-1": ["GK","CB","CB","LB","RB","CDM","CDM","LW","CAM","RW","ST"],
    "5-3-2":   ["GK","CB","CB","CB","LWB","RWB","CM","CM","CM","ST","ST"],
    "4-1-4-1": ["GK","CB","CB","LB","RB","CDM","LM","CM","CM","RM","ST"],
    "3-4-3":   ["GK","CB","CB","CB","LM","CM","CM","RM","LW","RW","ST"],
}

# ── 포메이션 스타일 보정치 ───────────────────────────────────
# 경기 시뮬(_match_win_probs/diff 계산)에 더해지는 소폭 팀 전력 보정.
#   공격적 포메이션(공격수多)은 +, 수비적 포메이션(CB/수비형MF多)은 -.
#   전력차(OVR)를 뒤집을 정도가 아니라 "같은 실력이면 스타일 차이로
#   승부가 살짝 갈릴 수 있다" 수준의 미세 조정(±1.5 이내)으로 설계.
FORMATION_STYLE = {
    "4-4-2":    0.0,   # 기준(중립)
    "4-3-3":   +1.0,   # 공격적 (윙어 2 + 스트라이커)
    "3-5-2":   -0.5,   # 미드필드 장악형, 약간 수비적
    "4-2-3-1": +0.3,   # 균형에 가까운 약공격
    "5-3-2":   -1.5,   # 수비적 (스리백+수비형)
    "4-1-4-1": -1.0,   # 수비적 (단일 CDM 앵커)
    "3-4-3":   +1.5,   # 매우 공격적 (스리백 리스크 감수)
}

# ── 포지션 호환성 맵 ────────────────────────────────────────
# 주요 포지션 → 포메이션 슬롯 우선순위 리스트
# 앞에 있을수록 자연스러운 배치 (1순위), 뒤로 갈수록 어색 (패널티)
# 경기 퍼포먼스 계수는 배치된 슬롯 포지션 기준으로 결정됨
POSITION_COMPAT = {
    "ST":  ["ST", "CF", "LW", "RW", "CAM"],
    "CF":  ["CF", "ST", "CAM", "LW", "RW"],
    "LW":  ["LW", "LM", "CAM", "ST", "RW"],
    "RW":  ["RW", "RM", "CAM", "ST", "LW"],
    "CAM": ["CAM", "CM", "LW", "RW", "LM", "RM"],
    "CM":  ["CM", "CDM", "CAM", "LM", "RM"],
    "CDM": ["CDM", "CM"],
    "LB":  ["LB", "LWB", "CB"],
    "RB":  ["RB", "RWB", "CB"],
    "CB":  ["CB", "LB", "RB"],
    "GK":  ["GK"],
    # 포메이션 전용 슬롯 (등록 포지션으로 선택 불가)
    "LM":  ["LM", "LW", "CM"],
    "RM":  ["RM", "RW", "CM"],
    "LWB": ["LWB", "LB", "CB"],
    "RWB": ["RWB", "RB", "CB"],
}

# 배치 포지션 미스매치 패널티 (주요 포지션과 슬롯이 다를 때)
# 1순위(완벽 매치) → 패널티 없음 / 2순위 → 5% / 3순위 → 10% / 그 이상 → 15%
POSITION_MISMATCH_PENALTY = [0.0, 0.05, 0.10, 0.15, 0.15]



# 감독 관계 벤치 확률
BENCH_BY_RELATION = [(15,0.30),(10,0.50),(5,0.70),(0,0.90)]

# ════════════════════════════════════════════════════════════════
# 노화 시스템 (재능 티어 × 나이구간 × 스탯계열 차등)
# ════════════════════════════════════════════════════════════════
# 설계 원칙:
#   1) 재능 티어별로 '나이구간 연간 OVR 낙폭'을 직접 지정한다(아래 표).
#      - 28세까지 유지, 29세부터 하락 시작, 나이 들수록 가속.
#      - 재능 높을수록 황혼기가 길다(월클은 40대에도 정상급, 범부는 30대 중반 급락).
#   2) 그 낙폭을 스탯 계열 비중으로 분배한다(AGING_GROUP_WEIGHT).
#      - 신체(speed/stamina/...)가 가장 빨리·많이 빠지고,
#      - 기술(shooting/passing/...)은 늦게·완만히,
#      - 멘탈(mental/confidence/...)은 유지(원칙적으로 안 깎음).
#   3) 노화는 _max(천장)뿐 아니라 '현재 스탯'도 직접 깎는다.
#      → 고강도 훈련으로 일부 상쇄되지만 노화 하락분을 다 메우진 못한다
#        (28세 86이 31세에 86 유지 불가). 훈련 회복은 _process_training 이 담당.
#
# 목표 곡선(피크 OVR 86 기준, 훈련회복 포함 실측 — 멘탈 노화 도입 후):
#   목표 곡선 (전성기 OVR 중간값 기준, 범위폭 7):
#   worldclass(98): 29→97  31→96  34→92  37→88  40→80  43→68
#   elite(91):      29→89  31→85  34→77  37→67  40→56  43→47
#   pro(82):        29→78  31→72  34→62  37→53  40→44  43→37
#   semipro(73):    29→68  31→59  34→46  37→37  40→25  43→15
#   ordinary(64):   29→58  31→48  34→37  37→25  40→15  43→8

# [티어별 나이구간 연간 OVR 낙폭] (start_age, end_age, drop_per_year)
#   29세부터 적용. 28세 이하는 낙폭 0(노화 없음).
#   낙폭이 클수록 빠르게 쇠퇴 → 하위 티어일수록 30대 초반에 사실상 도태.
#   팀 오퍼가 없어 자연 은퇴하는 구조 (강제 은퇴 없음).
AGING_DECLINE = {
    "worldclass": [(29,31,1.00),(32,34,1.33),(35,37,1.33),(38,40,2.67),(41,45,4.00)],
    "elite":      [(29,31,2.00),(32,34,2.67),(35,37,3.17),(38,40,3.83),(41,45,3.00)],
    "pro":        [(29,31,3.00),(32,34,3.33),(35,37,3.00),(38,40,3.00),(41,45,2.33)],
    "semipro":    [(29,31,4.50),(32,34,4.33),(35,37,3.00),(38,40,4.17),(41,45,3.33)],
    "ordinary":   [(29,31,5.00),(32,34,3.67),(35,37,4.17),(38,40,3.33),(41,45,2.33)],
}
# worldclass 중 talent_cap 98+ 는 더 완만한 곡선 적용
AGING_DECLINE_WC_TOP = [(29,31,0.80),(32,34,1.20),(35,37,1.50),(38,40,2.50),(41,45,4.00)]
AGING_WC_TOP_OVR = 98   # 이 전성기 OVR 이상인 worldclass 는 wc_top 곡선 적용

# [스탯 계열별 노화 비중] 연간 낙폭을 계열에 차등 분배.
#   신체 > 기술 > 멘탈. 평균이 1.0이 되도록 내부에서 정규화해 쓴다.
AGING_GROUP_WEIGHT = {
    "physical":  1.35,   # 신체: 가장 빨리·많이
    "technical": 0.70,   # 기술: 늦게·완만히
    "mental":    0.45,   # 멘탈: 가장 느리게(하지만 노화함)
}
# [예외] ordinary/semipro 는 노년(41세+)에 멘탈도 일부 깎인다.
AGING_LIMITED_LATE_MENTAL = {"age": 41, "weight": 0.55}

# [포지션별 노화 속도 배수] 윙/공격은 빨리, 수비/GK는 천천히 늙는다.
#   (현실: 윙어는 26세 부근 피크 후 빠르게 쇠퇴, 센터백·GK는 31세까지 정점 유지)
#   [수정] 멘탈 노화 도입(weight 0→0.45)으로 전 포지션이 더 깎이게 되어,
#   가장 늦게 늙어야 할 GK 배수를 0.72→0.50으로 낮춰 목표 곡선(37세 80, 40세 76)에 맞춤.
AGING_POS_MULT = {
    "LW":1.20, "RW":1.20, "ST":1.12, "CF":1.12,
    "CAM":1.0, "CM":1.0,  "LM":1.05, "RM":1.05,
    "CB":0.82, "LB":0.88, "RB":0.88, "GK":0.62,
}

# [노화 하한선] 스탯이 노화로 떨어져도 이 값 밑으론 안 내려간다(바닥).
#   재능 무관 절대 하한. (범부 노년 멘탈 하락 시에도 이 밑으론 안 감)
AGING_STAT_FLOOR = 40

# 팬수 기본값
BASE_FANS = {
    "S":{1:500000,2:50000,3:500},
    "A":{1:300000,2:30000,3:300},
    "B":{1:200000,2:20000,3:200},
    "C":{1:100000,2:10000,3:100},
    "D":{1:50000, 2:5000, 3:50},
    "E":{1:20000, 2:2000, 3:20},
    "F":{1:50000, 2:25000,3:500},
}
AFRICA_FAN_MULT = 10

# 월드컵
WC_START_YEAR     = 2002
WC_INTERVAL       = 4

CONTINENTAL_START_YEAR = 2004
CONTINENTAL_INTERVAL   = 4

# ── 국제대회 본선 설정 ──────────────────────────────
WC_TEAMS   = 32   # 월드컵 본선 32개국 (8조 × 4팀)
WC_GROUPS  = 8
CONT_TEAMS = 24   # 대륙컵 본선 24개국 (6조 × 4팀)
CONT_GROUPS = 6
# 24개국 포맷: 각 조 1·2위(12팀) + 성적 좋은 3위 중 상위 4팀 = 16강
CONT_BEST_THIRDS = 4

# ══════════════════════════════════════════════════════════════
# 월드컵 예선 대륙 그룹 (4개 통합 연맹)
# ══════════════════════════════════════════════════════════════
# 연맹 대표키 → 소속 대륙 목록
CONFEDERATIONS = {
    "유럽":     ["유럽"],
    "아메리카": ["남미", "북미", "북중미"],
    "아시아":   ["아시아", "오세아니아"],
    "아프리카": ["아프리카"],
}
# 개별 대륙 → 연맹 대표키 (역방향 조회)
CONTINENT_TO_CONF = {
    "유럽":     "유럽",
    "남미":     "아메리카",
    "북미":     "아메리카",
    "북중미":   "아메리카",
    "아시아":   "아시아",
    "오세아니아":"아시아",
    "아프리카": "아프리카",
}

CONF_CUP_NAME = {
    "유럽":     "유럽 챔피언십",
    "아메리카": "남북미 대륙컵",
    "아시아":   "아시안컵",
    "아프리카": "아프리카 네이션스컵",
    # 하위호환
    "남미": "남북미 대륙컵", "북미": "남북미 대륙컵", "북중미": "남북미 대륙컵",
    "오세아니아": "아시안컵",
}

# ══════════════════════════════════════════════════════════════
# 32팀 본선 대륙별 쿼터 (합 32)
# ══════════════════════════════════════════════════════════════
WC_QUOTA = {"유럽": 13, "아메리카": 8, "아시아": 5, "아프리카": 6}

# 예선 세부 구조 (32팀 체제)
# cutoff_bottom: 하위 N개국 예선 진출 실패 (경기 없이 성적만 기록)
# pool = 전체 - cutoff_bottom → n_groups × group_size 로 딱 떨어져야 함
# direct: 조 1위 중 바로 본선 직행 수 (0이면 전원 플레이오프)
# po_teams: 플레이오프 참가 팀 수 (조 1위들)
# po_winners: 플레이오프 통과 팀 수
# wildcard: 조 2위 중 성적순 와일드카드 수
WC_QUAL_32 = {
    "유럽": {
        "cutoff_bottom": 6,    # 54 - 6 = 48개국 → 12조×4팀
        "n_groups": 12, "group_size": 4,
        "direct": 12,          # 조 1위 12팀 직행
        "po_teams": 2,         # 조 2위 중 성적 상위 2팀 → 단판 1매치
        "po_winners": 1,       # 승자 1팀 추가 (총 13)
        "wildcard": 0, "quota": 13,
    },
    "아메리카": {
        "cutoff_bottom": 13,   # 45 - 13 = 32개국 → 8조×4팀
        "n_groups": 8, "group_size": 4,
        "direct": 8,           # 조 1위 8팀 직행, 플레이오프 없음
        "po_teams": 0, "po_winners": 0, "wildcard": 0, "quota": 8,
    },
    "아시아": {
        "cutoff_bottom": 18,   # 58 - 18 = 40개국 → 10조×4팀
        "n_groups": 10, "group_size": 4,
        "direct": 0,
        "po_teams": 10,        # 조 1위 10팀 → 단판 5매치
        "po_winners": 5,
        "wildcard": 0, "quota": 5,
    },
    "아프리카": {
        "cutoff_bottom": 6,    # 54 - 6 = 48개국 → 12조×4팀
        "n_groups": 12, "group_size": 4,
        "direct": 0,
        "po_teams": 12,        # 조 1위 12팀 → 단판 6매치
        "po_winners": 6,
        "wildcard": 0, "quota": 6,
    },
}

# ══════════════════════════════════════════════════════════════
# 48팀 본선 대륙별 쿼터 (합 48)
# ══════════════════════════════════════════════════════════════
WC_EXPAND_YEAR     = 2022 # 월드컵 32강 날짜
WC_TEAMS_BIG       = 48
WC_GROUPS_BIG      = 12
WC_BEST_THIRDS_BIG = 8
WC_QUOTA_BIG = {"유럽": 16, "아메리카": 13, "아시아": 10, "아프리카": 9}

# 예선 세부 구조 (48팀 체제)
WC_QUAL_48 = {
    # 48팀 체제: 32팀 체제보다 참가국 많거나 같고, 뽑히는 팀도 증가.
    # WC_EXPAND_YEAR(constants.py) 값에 연동되므로 해당 값만 바꾸면 자동 적용.
    "유럽": {
        "cutoff_bottom": 6,    # 54 - 6 = 48개국 → 12조×4팀 (32팀 체제와 동일)
        "n_groups": 12, "group_size": 4,
        "direct": 12,          # 조 1위 12팀 직행
        "po_teams": 8,         # 조 2위 중 성적 상위 8팀 → 단판 4매치
        "po_winners": 4,       # 승자 4팀 추가 (총 16)
        "wildcard": 0, "quota": 16,
    },
    "아메리카": {
        "cutoff_bottom": 5,    # 45 - 5 = 40개국 → 10조×4팀 (32팀 체제 32개국 → 40개국)
        "n_groups": 10, "group_size": 4,
        "direct": 10,          # 조 1위 10팀 직행
        "po_teams": 0, "po_winners": 0,
        "wildcard": 3,         # 조 2위 중 성적 상위 3팀 와카 (총 13)
        "quota": 13,
    },
    "아시아": {
        "cutoff_bottom": 18,   # 58 - 18 = 40개국 → 10조×4팀 (32팀 체제와 동일)
        "n_groups": 10, "group_size": 4,
        "direct": 10,          # 조 1위 10팀 전원 직행 (PO→전원통과는 단판구현 불가)
        "po_teams": 0, "po_winners": 0,
        "wildcard": 0, "quota": 10,
    },
    "아프리카": {
        "cutoff_bottom": 6,    # 54 - 6 = 48개국 → 12조×4팀 (32팀 체제 50개국 → 48개국)
        "n_groups": 12, "group_size": 4,
        "direct": 9,           # 조 1위 중 상위 9팀 직행 (12→9, 3팀 탈락)
        "po_teams": 0, "po_winners": 0,
        "wildcard": 0, "quota": 9,
    },
}

# 국가 등급 → 대표팀 전력(OVR) / 예선 기본 점수
GRADE_TEAM_OVR  = {"S": 86, "A": 79, "B": 72, "C": 65, "D": 58, "E": 51, "F": 45}
GRADE_QUAL_BASE = {"S": 0.90, "A": 0.78, "B": 0.62, "C": 0.48,
                   "D": 0.36, "E": 0.26, "F": 0.16}
QUAL_NOISE = 0.12   # 예선 랜덤 노이즈 (±) → 강호 탈락/약체 진출 이변 발생

# 국가대표 선발 기준 (국가 등급별 최소 OVR / 최대 소속 리그 티어)
INTL_SELECTION_OVR = {"S": 75, "A": 65, "B": 55, "C": 48, "D": 42, "E": 37, "F": 32}
# 국가대표 선발 마진: 자국 등급평균(GRADE_TEAM_OVR) 대비 이만큼 낮아도 선발.
#   작을수록 엄격(톱권만). 베테랑 보너스(_vet_bonus)가 경계선 선수를 구제한다.
#   ※ 이 시뮬은 '선발=풀타임 출전'(벤치 미구현)이므로, 스쿼드 합류가 아니라
#     '주전으로 뛸 수준'을 기준으로 한다 → 마진을 3으로 좁힘(주전급).
INTL_SELECTION_MARGIN = 3
INTL_MAX_TIER      = {"S": 1, "A": 1, "B": 2, "C": 2, "D": 3, "E": 3, "F": 3}
INTL_MIN_MATCHES   = 5

# 클럽 입단/오퍼 마진: 팀 평균 OVR 대비 내 OVR이 이만큼까지 낮아도 입단 가능.
#   국대와 같은 논리(입단=주전 출전, 벤치 미구현) → '주전 경쟁 가능' 수준.
#   ※ 국가 등급별 차등: 상위 리그일수록 문턱이 빡빡하다.
#     - S/A급(톱 리그): 마진 1 → 거의 그 팀 평균급이어야 입단.
#       (엘리트 전성기 90 → S급 1부, 평범 85 → A급 1부가 한계, S급은 못 감)
#     - 하위 등급: 점점 관대(아무나 데뷔 가능한 약체 리그).
CLUB_JOIN_MARGIN = 3   # (하위호환용 기본값 — 등급 미상 시 사용)
CLUB_JOIN_MARGIN_BY_GRADE = {
    "S": 1, "A": 1, "B": 3, "C": 4, "D": 5, "E": 6, "F": 7,
}

CONTINENT_NAMES = ["유럽","아시아","아프리카","북미+남미"]

# OVR 범위 (등급별)
OVR_RANGES = {
    # (하한, 상한): 리그에 존재할 수 있는 선수 OVR 범위
    # 하한 = 최약팀 벤치 수준, 상한 = 최강팀 에이스 수준
    # CONTINENT_OVR_BONUS로 대륙별 추가 보정 (유럽+1, 아시아-3 등)
    # tier1 목표 avg: SS=93 / S=90 / A(유럽)=86 / A(아시아)=82 / B=75 / C=65
    # tier2 목표 avg: SS=88.5 / S=86.6 / A=78.3 / B=67.7 / C=56.5
    # [버그수정 2026-07] SS는 이미 5부(내셔널리그)가 실존하는데 OVR_RANGES가
    # 4부까지만 정의돼 있었다 — 5부 조회 시 아래 폴백(_tier_top_ovr)이 타면서
    # tier1과 비슷한 값이 나오는 문제가 있었다(피라미드 밑바닥인데 최상위급
    # OVR이 나오는 심각한 역전). S도 6부 신설에 맞춰 같이 정의한다.
    # S 6부는 "스페인 6부 ≈ 한국(A급) 4부" 기준(대화에서 합의한 벤치마크)에
    # 맞춰 A 4부(42-53) 근처로 맞추고, 5부는 4부와 6부 사이에 오도록 재조정.
    "SS":{1:(90,100),2:(84,94),3:(68,80),4:(53,65),5:(40,52),6:(28,40)},
    "S": {1:(85,96), 2:(86,94),3:(76,88),4:(62,73),5:(52,64),6:(44,56)},
    "A": {1:(82,94), 2:(73,85),3:(65,75),4:(55,68)},
    "B": {1:(72,82), 2:(66,74),3:(55,63),4:(38,49)},
    "C": {1:(63,73), 2:(55,63),3:(43,52),4:(29,40)},
    "D": {1:(53,63), 2:(43,53),3:(33,43)},
    "E": {1:(43,53), 2:(33,43),3:(26,35)},
    "F": {1:(33,43), 2:(25,35),3:(18,27)},
}

# ── 포지션군 (평점/오퍼 임계치 분리용) ──────────────────────
POS_GROUP = {
    "GK":"GK",
    "CB":"수비","LB":"수비","RB":"수비",
    "CDM":"미드","CM":"미드",
    "CAM":"공격","LW":"공격","RW":"공격","CF":"공격","ST":"공격",
}
DEF_POS = {"CB","LB","RB","CDM"}   # 수비 라인 평점 보너스 대상

# 재계약/오퍼 평점 기준선 (포지션군별). 공격수 편향 보정.
RENEW_RATING = {"공격":6.5, "미드":6.3, "수비":6.1, "GK":6.1}

# ── 리그 수준 적합성 / 도태 시스템 ──────────────────────────
# OVR 격차(gap = 팀평균OVR(본인제외) - 내OVR) 기반 벤치 확률
BENCH_BY_GAP = [(-5,0.02),(0,0.08),(5,0.20),(10,0.45),(15,0.70),(999,0.90)]

# [리그 등급별 방출 격차 기준] 팀 평균OVR(본인 제외) 대비 내 OVR 격차
#   방출 조건: gap >= 기준 AND manager_relation < RELEASE_REL_THRESHOLD
#   재계약 거부: gap >= 기준 (감독 관계 무관 — 계약 만료 시 무조건 재계약 안 함)
RELEASE_GAP_BY_GRADE = {
    "SS": 2,   # EPL — 조금만 부족해도 교체
    "S":  3,   # 빅리그 — 약간의 관용
    "A":  4,   # 준메이저
    "B":  5,   # 중위리그
    "C":  6,   # 하위리그
    "D":  8,   # 최하위
    "E":  8,
    "F":  8,
}
# 방출 트리거 감독 관계 임계치 (이 값 미만일 때만 방출 실행)
RELEASE_REL_THRESHOLD = 30

# OVR 기반 오퍼 티어 가중치 (성장 시 상위 리그로 이동)
def tier_weights_by_ovr(ovr):
    # 반환값: [tier1, tier2, tier3, tier4, tier5] 가중치
    # 4·5부는 OVR이 낮을수록, 1부는 OVR이 높을수록 가중치 높음
    if ovr >= 80:   return [70, 22, 6,  2,  0]
    elif ovr >= 70: return [45, 35, 14, 5,  1]
    elif ovr >= 60: return [20, 38, 28, 11, 3]
    elif ovr >= 50: return [8,  28, 38, 20, 6]
    elif ovr >= 40: return [3,  12, 35, 32, 18]
    else:           return [1,  5,  24, 38, 32]


def tier_weights_by_ovr_n(ovr, n):
    """[2026-07 버그 수정, 신민용 리포트: "ValueError: The number of
    weights does not match the population"] tier_weights_by_ovr()는
    항상 정확히 5개(1~5부) 가중치만 반환하는데, 호출부들이
    `tier_weights_by_ovr(ovr)[:n]`처럼 그 나라의 실제 최대 티어(n)만큼
    잘라 썼다. 대부분 나라는 n<=5라 문제없었지만, 5부보다 더 깊은
    리그 구조를 가진 나라(예: 한국 K4/K5)에서는 n>5가 되어 리스트를
    잘라도 5개뿐인데 random.choices()의 population(티어 목록)은 n개라
    "개수가 안 맞는다"는 에러로 그대로 죽었다.

    이 함수는 항상 정확히 n개를 반환한다 — n<=5면 그대로 자르고,
    n>5면 5부(가장 깊은 정의된 티어) 가중치를 그 이후 모든 티어에
    반복해서 채운다(깊은 티어일수록 확률이 아주 낮게 유지되는 기존
    설계 의도를 그대로 살리면서, 아예 후보에서 빠지는 것도 방지)."""
    w = tier_weights_by_ovr(ovr)
    if n <= len(w):
        return w[:n]
    return w + [w[-1]] * (n - len(w))


# ── 개인 수상 시스템 ────────────────────────────────────────
# 포지션별 시즌 기대 득점 베이스 (14경기 풀시즌 기준).
# [설계 변경] OVR로 스케일링하지 않는다 — 주전 스트라이커는 못하든 잘하든
#   팀 내에서 슈팅 기회 자체를 비슷하게 가져가므로(주포지션 역할이 기회량을
#   결정), OVR70이든 99든 ST는 항상 이 기준치(15~20골) 근방에서 형성된다.
#   실력 차이는 골 수가 아니라 평점(rating)·OVR 자체로 반영된다.
AWARD_POS_GOAL = {"ST":18,"CF":14,"LW":9,"RW":9,"CAM":7,"CM":4}
# 포지션별 시즌 기대 도움 베이스 (마찬가지로 OVR 무관 고정 기준치)
AWARD_POS_ASSIST = {"CAM":11,"CM":8,"LW":8,"RW":8,"CF":6,"ST":5}
# 사모라상(최저 실점 GK) 최소 출전 경기 수.
#   8팀 리그(더블 라운드로빈)는 한 시즌 리그 14경기이므로, 출전 기준이
#   리그 규모를 넘으면(예: 20) 사실상 수상 불가가 된다. 리그 14경기 기준
#   약 85% 출전선인 12로 둔다(골든글러브 클린시트 10개와 비슷한 난이도).
#   ※ season_matches 는 리그 경기만 카운트(챔스/대표전은 미포함).
ZAMORA_MIN_MATCHES = 12
# 공격 가담 포지션 (수상 후보 풀)
ATTACK_POS = ("ST","CF","LW","RW","CAM","CM")
# 발롱도르 후보 리그 등급 (최상위 리그만)
BALLON_DOR_GRADES = ("SS","S")  # SS=EPL, S=빅4+브라질.
# [버그수정 2026-07, 신민용 지적: "발롱도르는 S급 리그에서만 받게 설계했는데
# A급도 받는 버그"] 원래 S등급 이상만 대상이었는데 A(대륙 메이저 — 한국
# K1, 멕시코, 미국, 일본 등)까지 후보에 끼어드는 상태였다. S 이상으로 되돌림.
# (푸스카스상 게이트(_PUSKAS_GRADES)와 동일하게 SS/S만 남긴다.)

# 포지션 그룹 (베스트 11 포메이션용)
GK_POS = ("GK",)
DF_POS = ("CB", "LB", "RB", "LWB", "RWB")
MF_POS = ("CDM", "CM", "CAM")
FW_POS = ("LW", "RW", "CF", "ST")

# ── 국가대표 선발 기준 ────────────────────────────────────────
# 국대 grade(FIFA 랭크 기반) 별 월드클래스 수 / 최소 출전 OVR
#   - wc_count: 스쿼드 내 worldclass 선수 목표 수 (min, max)
#   - min_ovr : 출전 가능한 최소 OVR
#   - top2    : S급 중 FIFA 랭크 상위 2개국은 더 높은 기준 적용
NAT_SQUAD_STANDARD = {
    "S": {
        "wc_count": (3, 4),
        "min_ovr":  88,
        "top2_min_ovr": 90,   # S급 피파랭크 상위 2개국 최소 OVR
    },
    "A": {"wc_count": (0, 2), "min_ovr": 80},
    "B": {"wc_count": (0, 1), "min_ovr": 72},
    "C": {"wc_count": (0, 0), "min_ovr": 64},
    "D": {"wc_count": (0, 0), "min_ovr": 55},
    "E": {"wc_count": (0, 0), "min_ovr": 55},
    "F": {"wc_count": (0, 0), "min_ovr": 55},
}

# ── 클럽 리그 등급별 선수 구성 기준 ─────────────────────────
# AI 선수 생성/이적 시 리그 등급에 맞는 talent_tier 비율 목표
# wc_per_team: 팀당 worldclass 선수 목표 수 (min, max)
# min_ovr    : 해당 리그에서 뛸 수 있는 최소 OVR (1부 기준)
LEAGUE_SQUAD_STANDARD = {
    "SS": {"wc_per_team": (3, 4), "min_talent": "elite",    "min_ovr": 90},
    "S":  {"wc_per_team": (1, 2), "min_talent": "elite",    "min_ovr": 85},
    "A":  {"wc_per_team": (0, 1), "min_talent": "pro",      "min_ovr": 78},
    "B":  {"wc_per_team": (0, 0), "min_talent": "pro",      "min_ovr": 70},
    "C":  {"wc_per_team": (0, 0), "min_talent": "semipro",  "min_ovr": 62},
    "D":  {"wc_per_team": (0, 0), "min_talent": "semipro",  "min_ovr": 55},
    "E":  {"wc_per_team": (0, 0), "min_talent": "ordinary", "min_ovr": 45},
    "F":  {"wc_per_team": (0, 0), "min_talent": "ordinary", "min_ovr": 35},
}



# ── 리그 부유도(연봉 수준) 오버라이드 ───────────────────────
# FIFA 등급(국대 실력)과 별개로, 리그가 부유한 나라는 연봉이 높음.
# 예: 사우디는 국대 C급이지만 오일머니로 리그 연봉은 S급.
# 여기 없는 나라는 FIFA 등급을 그대로 부유도로 사용.
# ══════════════════════════════════════════════════════════════
# 대륙별 OVR 보정치 — 같은 리그 등급이라도 대륙에 따라 실제 선수 수준 차이
#   유럽 기준 설계이므로 유럽 +1, 나머지는 하향
#   SS는 이미 상한(100)에 근접하므로 보정 적용 시 min(100) 처리
CONTINENT_OVR_BONUS = {
    "유럽":       +1,
    "남미":        0,
    "북미":       -2,
    "북중미":     -2,
    "아시아":     -3,
    "아프리카":   -4,
    "오세아니아": -3,
}

# [신규] 나라별 OVR 미세조정 — 같은 등급(A 등) 안에서도 실제로는 재정·용병
#   수준 차이가 뚜렷한 나라들을 대륙 보정과 별개로 한 번 더 조정한다.
#   (신민용 요청: 오일머니/북미 자본 유입 리그는 위로, 한·일은 상대적으로
#    아래로 — A등급 안에서의 상/중/하위 구분을 대륙보정만으로는 다 못 잡음)
COUNTRY_OVR_ADJ = {
    "네덜란드":   +3,   # 아약스/PSV 등 유스+이적 수익 기반 준S급 실력
    "사우디아라비아": +3,  # PIF 오일머니로 슈퍼스타 다수 영입
    "미국":       +3,   # MLS 인프라+스타 영입(메시 등) 확대
    "대한민국":   -3,   # K리그 — A등급 안에서는 중하위
    "일본":       -2,   # J리그 — 스쿼드 depth·재정 기준 K리그보다 근소 우위로 반영
}

# 대륙별 연봉 배율 — 같은 등급이라도 리그 재정 차이 반영
#   브라질: 실력은 S급이지만 리그 재정 약해 연봉 낮음
#   아시아: K/J리그 수준 (2~4억)
#   아프리카: 극히 낮음 (1~3천만)
CONTINENT_SALARY_MULT = {
    "유럽":       1.00,
    "남미":       0.25,
    "북미":       0.85,
    "북중미":     0.85,
    "아시아":     0.45,
    "아프리카":   0.10,
    "오세아니아": 0.30,
}

# [2026-07 버그 수정] CONTINENT_SALARY_MULT는 정의만 돼 있고 실제로 어디서도
# 쓰이지 않고 있었다(game_engine._calc_salary는 COUNTRY_SALARY_MULT에 없는
# 나라는 그냥 cont_mult=1.0, 즉 "유럽과 동일 재정"으로 취급했다). 그 결과
# COUNTRY_SALARY_MULT에 개별 지정이 없는 나라(전 세계 211개국 중 다수 —
# 특히 COUNTRY_LEAGUE_GRADE에도 없어 국대 등급으로 대체되는 나라들)는
# 실제로는 유럽/아시아/아프리카 등 어느 대륙이든 상관없이 똑같은 연봉
# 곡선을 썼다 — 신민용이 지적한 "일부 지역은 단순 등급으로만 처리됨"이
# 정확히 이 문제다. get_country_salary_mult()가 그 의도된 폴백(대륙별
# 배율)을 실제로 연결해준다: 1) 나라별 지정값이 있으면 그걸 최우선 사용,
# 2) 없으면 그 나라가 속한 대륙의 CONTINENT_SALARY_MULT를 사용,
# 3) 대륙 정보조차 없으면(이론상 없어야 정상) 최종 안전망으로 1.0.
COUNTRY_CONTINENT = {}

def _register_country_continents():
    """COUNTRY_DATA(국가,국기,대륙,언어,fifa_rank)에서 국가→대륙 맵을 만든다.
    지연 임포트 — constants.py는 원래 다른 모듈에 의존하지 않는 순수 데이터
    파일이라, 맨 위에서 바로 import하는 대신 이 함수가 처음 필요할 때만
    한 번 채운다(순환 임포트 걱정 없이 기존 구조를 그대로 유지)."""
    if COUNTRY_CONTINENT:
        return
    from data.countries import COUNTRY_DATA
    for name, _flag, continent, _lang, _rank in COUNTRY_DATA:
        COUNTRY_CONTINENT[name] = continent

def get_country_salary_mult(country: str) -> float:
    """나라별 연봉 배율 조회 — COUNTRY_SALARY_MULT(나라별 개별 지정) 우선,
    없으면 CONTINENT_SALARY_MULT(대륙별 배율)로 폴백. 최종 안전망 1.0.
    (기존엔 나라별 지정이 없으면 그냥 1.0 — 유럽과 동급 재정으로 취급되던
    문제를 고친다.)"""
    if country in COUNTRY_SALARY_MULT:
        return COUNTRY_SALARY_MULT[country]
    _register_country_continents()
    continent = COUNTRY_CONTINENT.get(country)
    return CONTINENT_SALARY_MULT.get(continent, 1.0)

# ══════════════════════════════════════════════════════════════
# 리그 등급 (국대 FIFA 랭크와 완전 분리)
# countries.grade = 국대 강도 (월드컵 예선/대진 기준)
# COUNTRY_LEAGUE_GRADE = 클럽 리그 수준 (OVR 생성·연봉·오퍼 기준)
# ══════════════════════════════════════════════════════════════
COUNTRY_LEAGUE_GRADE = {
    # SS급 — EPL. 세계 자본의 정점, 연봉·선수 수준 모두 단독 최상위
    "잉글랜드": "SS",

    # S급 — 유럽 빅4 + 브라질
    "프랑스": "S", "스페인": "S", "독일": "S", "이탈리아": "S", "브라질": "S",

    # A급 — 대륙별 최상위 메이저
    "아르헨티나": "A", "네덜란드": "A", "포르투갈": "A", "벨기에": "A",
    "멕시코": "A", "미국": "A", "일본": "A", "대한민국": "A",
    # [2026-07 재조정, 신민용 지적] 호주 A리그는 Opta Power Rankings 등
    # 실측 순위표에서 세계 상위 15개 리그 안에 들지 못한다 — 네덜란드·
    # 포르투갈·멕시코·아르헨티나 같은 진짜 A급과는 격차가 크고, 강한
    # 샐러리캡 제도 때문에 재정 규모도 뚜렷이 작다. B급으로 하향.
    "사우디아라비아": "A", "튀르키예": "A",

    # B급 — 견고한 중상위 / 유망주 수출형
    "모로코": "B", "콜롬비아": "B", "크로아티아": "B", "우루과이": "B",
    "스위스": "B", "덴마크": "B", "오스트리아": "B", "노르웨이": "B",
    "이집트": "B", "에콰도르": "B", "우크라이나": "B", "러시아": "B",
    "스웨덴": "B", "스코틀랜드": "B", "그리스": "B", "남아프리카공화국": "B",
    "호주": "B",
    # [2026-07 재조정, 신민용 지적] 나이지리아는 국가대표(해외파 위주)는
    # 강하지만, 국내리그(NPFL) 자체는 재정·인프라·관중동원 모두 취약해
    # 실제로는 이보다 약한 리그로 알려져 있다 — B급은 "국대 명성"이
    # 잘못 반영된 사례로 보여 C급으로 하향.

    # C급 — 대륙별 프로 안착 및 복병급
    "세네갈": "C", "이란": "C", "알제리": "C", "코트디부아르": "C", "폴란드": "C",
    "파라과이": "C", "헝가리": "C", "세르비아": "C", "체코": "C", "카메룬": "C",
    "나이지리아": "C",
    "베네수엘라": "C", "칠레": "C", "페루": "C", "코스타리카": "C", "루마니아": "C",
    "튀니지": "C", "우즈베키스탄": "C", "카타르": "A", "이라크": "C", "가나": "C",

    # D급 — 변방 프로 리그
    # [2026-07 밸런스 수정, 실측 데이터] 파나마는 ERI SalaryExpert 실측
    # 평균 연봉이 약 B/.19,412(≈1:1 달러 페그, 한화 약 2,500만원)로 나와서
    # 기존 D급 취급(396만원)보다 훨씬 높다 — 태국/베트남과 같은 패턴으로 C급 상향.
    "캐나다": "D", "웨일스": "D", "파나마": "C", "콩고 민주 공화국": "D",
    "슬로바키아": "D", "말리": "D", "부르키나 파소": "D", "카보베르데": "D",
    "보스니아 헤르체고비나": "D", "온두라스": "D", "요르단": "D",
    "아랍에미리트": "B", "북마케도니아": "D", "북아일랜드": "D", "자메이카": "D",
    # [2026-07 밸런스 수정, 실측 데이터] 조지아 — Sporting Intelligence 2018
    # 집계 기준 리그 평균 연봉 €13,403(약 2,000만원)로, 기존 D급 취급(466만원)
    # 보다 확실히 높다. C급으로 상향.
    "조지아": "C",
    # [2026-07 밸런스 수정, 실측 데이터] 핀란드/아이슬란드/이스라엘은 국대
    # 성적은 약하지만(D급 국대 등급) 선진 경제국 리그라 실제 연봉은 진짜
    # 변방 D급 나라들과 다르다. 핀란드 베이까우스리가 실측 평균 연봉은
    # 2~3만유로(위키피디아/footystats.org 확인, 한화 약 3,600만~4,500만원)로
    # 태국 등 아세안 C급 리그와 비슷한 수준 — 태국처럼 C급으로 상향한다.
    "아이슬란드": "C", "핀란드": "C", "이스라엘": "C",
    "볼리비아": "D", "코소보": "D", "오만": "D", "몬테네그로": "D", "기니": "D",
    "뉴질랜드": "D", "시리아": "D", "가봉": "D", "불가리아": "D", "앙골라": "D",
    "우간다": "D", "잠비아": "D", "중국": "B", "바레인": "D",
    # [2026-07 밸런스 수정, 신민용 지적] 태국/베트남/말레이시아/인도네시아는
    # 실제로 기업 스폰서·외국인 스타 영입이 있는 정식 프로리그(특히 타이
    # 리그 1은 아시아 내에서도 나름 자리잡은 리그)인데, 그동안 다른 변방
    # D급 국가(오만/코소보 등 사실상 세미프로)와 똑같이 취급돼 연봉이
    # 거의 0에 수렴했다 — "K3보다 태국리그가 돈을 더 번다"는 실제 축구
    # 이적시장 통념과 정반대로, 게임 안에서는 태국 1부가 한국 K3(3부)보다도
    # 한참 낮게 나오고 있었다. 팀 실력 등급(COUNTRY_LEAGUE_GRADE)을 C로
    # 한 단계 올려 더 큰 base 구간을 쓰게 하고, 아래 COUNTRY_SALARY_MULT로
    # 세부 조정한다(팀 OVR 생성도 이 등급을 같이 쓰므로, 연봉만 오르는 게
    # 아니라 선수단 수준도 D급보다 약간 올라간다 — 실제로도 이 네 나라
    # 리그가 주변 D급 변방국보다 스쿼드 수준이 나은 편이라 자연스럽다).
    "태국": "C", "베트남": "C", "말레이시아": "C", "인도네시아": "C",
    # [2026-07 밸런스 수정, 실측 데이터] 키프로스는 국대는 약하지만(D급) 실제
    # 리그 재정은 놀랍도록 좋다 — 부동산/해운 자금이 유입돼 파포스FC 팀 연봉
    # 총액이 €970만(선수당 평균 €42만)에 달하고, 신규 이적생에게 €25~50만도
    # 흔하다(financialmirror.com 확인). 룩셈부르크도 세계 최고 수준 GDP를
    # 반영해 일반 임금 수준 자체가 높다(ERI SalaryExpert 평균 €5.5만 확인).
    # 둘 다 태국/핀란드와 같은 패턴 — C급으로 상향.
    # [2026-07 밸런스 수정, 실측 데이터] 벨라루스/아제르바이잔 — 같은
    # Sporting Intelligence 2018 집계에서 벨라루스 리그 평균 €31,589(약
    # 4,700만원), 아제르바이잔은 €52,638(약 7,900만원 — 국영기업 자금이
    # 들어간 소수 빅클럽이 평균을 끌어올림, 카라바흐 등)로 예상보다 훨씬
    # 높다. 둘 다 C급으로 상향.
    "벨라루스": "C", "과테말라": "D", "룩셈부르크": "C",
    "엘살바도르": "D", "키프로스": "C", "아제르바이잔": "C",

    # E급 이하는 기본값(countries.grade)으로 충분 — 별도 지정 없음
}

# ── 리그 등급 조회 헬퍼 ──────────────────────────────────────
def get_league_grade(country_name: str, fallback_grade: str = "F") -> str:
    """국가명 → 리그 전용 등급. COUNTRY_LEAGUE_GRADE에 없으면 fallback_grade 사용."""
    return COUNTRY_LEAGUE_GRADE.get(country_name, fallback_grade)

# ══════════════════════════════════════════════════════════════
# 특수 연봉 국가 (리그 등급은 동일하나 연봉 구조가 특수한 나라)
# ══════════════════════════════════════════════════════════════
# ── 나라별 연봉 등급 오버라이드 ─────────────────────────────────────────
# 리그 등급(COUNTRY_LEAGUE_GRADE)과 별개로 연봉 산정에 사용할 등급을 지정.
# 오일머니/특수 경제국가에 적용.
SPECIAL_SALARY_COUNTRIES = {
    # SPECIAL 국가: cont_mult 미적용, base_year 등급을 직접 지정.
    # CAP(COUNTRY_SALARY_CAP)으로 실제 상한 제어.
    "사우디아라비아": "A",   # 오일머니. A급 base + CAP 50억
    # 카타르/UAE/중국은 COUNTRY_SALARY_MULT + CAP으로 관리 (일반 경로)
}
LEAGUE_WEALTH_OVERRIDE = SPECIAL_SALARY_COUNTRIES  # 하위호환

# ── 나라별 연봉 배율 ────────────────────────────────────────────────────
# base_year(SS 1부 최고 기준)에서 각 나라 OVR65 평균 주전 목표 연봉이 나오도록 역산.
# 공식: cm = target_OVR65 / (base_year[grade][1] * mult(65))
# mult(65) ≈ 0.763
# 없는 나라 → 1.0 (base_year 그대로, 사실상 미지정)
COUNTRY_SALARY_MULT = {
    # ── SS급 ──────────────────────────────────────────────────────────
    "잉글랜드":   0.2623,  # EPL OVR65 목표 연40억

    # ── S급 ───────────────────────────────────────────────────────────
    "스페인":     0.1356,  # 연12억
    "독일":       0.1130,  # 연10억
    "이탈리아":   0.0904,  # 연8억
    "프랑스":     0.0791,  # 연7억
    "브라질":     0.0339,  # 연3억

    # ── A급 ───────────────────────────────────────────────────────────
    "아르헨티나": 0.1321,  # 연3억
    "네덜란드":   0.1761,  # 연4억
    "포르투갈":   0.1761,  # 연4억
    "벨기에":     0.1321,  # 연3억
    "멕시코":     0.1541,  # 연3.5억
    "미국":       0.1321,  # 연3억
    "일본":       0.0793,  # 연1.8억 (J1 현실)
    "대한민국":   0.0528,  # 연1.2억 (K1 현실)
    "튀르키예":   0.1761,  # 연4억

    # ── B급 유럽 ──────────────────────────────────────────────────────
    "스코틀랜드": 0.0771,  # 연6천만
    "덴마크":     0.0771,  # 연6천만
    "스웨덴":     0.0514,  # 연4천만
    "노르웨이":   0.0514,  # 연4천만
    "스위스":     0.1028,  # 연8천만
    "오스트리아": 0.0771,  # 연6천만
    "그리스":     0.0514,  # 연4천만
    "러시아":     0.2570,  # 연20억 (RPL 외국인 고연봉)
    "우크라이나": 0.1028,  # 연8천만
    "크로아티아": 0.0385,  # 연3천만

    # ── B급 비유럽 ────────────────────────────────────────────────────
    "콜롬비아":   0.0257,  # 연2천만
    "에콰도르":   0.0257,  # 연2천만
    "우루과이":   0.0193,  # 연1.5천만
    "모로코":     0.0154,  # 연1.2천만
    # [2026-07 재조정, 신민용 지적] 나이지리아는 위에서 B→C급으로 하향
    # (국내리그 NPFL 자체는 국대 명성과 달리 재정이 취약함 — 실측 확인).
    # 이 mult는 C급 base 기준으로 재계산 없이도 이미 NPFL 현재 평균
    # (월 38~42만나이라, 약 연 410~450만원)과 잘 맞아 그대로 둔다.
    "나이지리아": 0.0154,
    "남아프리카공화국": 0.0257,  # 연2천만
    "이집트":     0.0193,  # 연1.5천만
    "칠레":       0.0350,  # 연2천만 (C급이지만 B급 수준)
    # [2026-07 재조정, 신민용 지적] 호주는 위에서 A→B급으로 하향 —
    # B급 base 기준으로 A리그 실측(2023-24 CBA 평균 3~4만호주달러)에
    # 맞춰 mult를 다시 역산했다.
    "호주":       0.095,   # B급 base, OVR55 목표 연2,900만원(A리그 CBA 실측 평균과 일치)

    # ── C급 유럽 ──────────────────────────────────────────────────────
    "폴란드":     0.0583,  # 연2.5천만
    "세르비아":   0.0350,  # 연1.5천만
    "체코":       0.0466,  # 연2천만
    "루마니아":   0.0280,  # 연1.2천만
    "헝가리":     0.0280,  # 연1.2천만

    # ── C급 중동/아시아/아프리카 ──────────────────────────────────────
    # [버그수정] 이란/세네갈은 기존 mult가 너무 낮아 1부 계산값이
    #  LOWER_LEAGUE_SALARY_OVERRIDE의 3부 절대값보다도 낮은 "1부<3부" 역전이
    #  있었다. 3부override 대비 1부가 항상 여유있게(약 1.7배+) 위에 오도록
    #  소폭 상향 조정.
    "이란":       0.0965,  # (구 0.0280) 연1.2천만 → 3부(연3.2천만)보다 낮았던 역전 수정
    "알제리":     0.0186,  # 연8백만
    "튀니지":     0.0186,
    "세네갈":     0.0122,  # (구 0.0070) 연3백만 → 3부(연4백만)보다 낮았던 역전 수정
    "카메룬":     0.0070,
    "가나":       0.0070,
    "코트디부아르": 0.0186, # 연8백만
    "이라크":     0.0186,
    "우즈베키스탄": 0.0140,

    # ── C급 남미 ──────────────────────────────────────────────────────
    "파라과이":   0.0233,  # 연1천만
    "페루":       0.0186,
    "베네수엘라": 0.0140,
    "코스타리카": 0.0186,

    # ── D급 특수 ──────────────────────────────────────────────────────
    # ── SPECIAL 제거 국가 (카타르/UAE/중국은 일반 경로로 처리) ─────────────
    "카타르":     0.1321,  # A급 base, OVR65 목표 연3억
    "아랍에미리트": 0.1028, # B급 base, OVR65 목표 연8천만
    "중국":       0.0321,  # B급 base, OVR65 목표 연2.5천만

    # [2026-07 밸런스 수정] 태국/베트남/말레이시아/인도네시아가 위에서 C급으로
    # 올라간 뒤 다시 역산한 배율. C급 base는 D급보다 훨씬 커서, 예전 D급
    # 기준으로 잡았던 작은 mult(0.05 안팎)를 그대로 두면 다시 너무 커진다 —
    # OVR55(평균급 선수) 기준 목표: 타이리그1 ≈ 연 1,600만원(K3 약 1,000만원
    # 보다 눈에 띄게 높게 — "K3보다 태국 가면 더 번다"), 베트남/말레이시아는
    # 그보다 살짝 낮게(대략 K3와 비슷~약간 아래), 인도네시아는 재정 불안정
    # 이력을 반영해 넷 중 가장 낮게.
    # [2026-07 재조정, 실측 데이터 반영] 태국 타이리그1은 실제로는 월
    # 8만~50만 바트(약 8천원~500만원 THB→KRW 환산 기준 약 300만~1,900만원/월)
    # 보수가 보고되고(German All Stars Bangkok 가이드, footystats.org 등),
    # 위에서 K3/K4 등 한국 하위부 자체도 실측 법정최저 기준으로 다시
    # 올린 뒤라(LOWER_LEAGUE_SALARY_FLOOR 참고) 그 새 기준선에 맞춰
    # 재조정했다 — 태국 1부가 K리그2 하단부와 비슷한 수준까지 올라오도록.
    "태국":       0.265,  # C급 base, OVR55 목표 연4,500만원(K리그2 하단과 비슷)
    "베트남":     0.190,  # C급 base, OVR55 목표 연3,200만원(태국보다 살짝 아래)
    "말레이시아": 0.200,  # C급 base, OVR55 목표 연3,400만원
    "인도네시아": 0.160,  # C급 base, OVR55 목표 연2,700만원(재정 불안정 이력 반영)

    # [2026-07 신설, 실측 데이터] 핀란드/아이슬란드/이스라엘 — 위에서 D→C급으로
    # 올라간 나라들. 핀란드는 위키피디아/footystats.org 실측(평균 2~3만유로/년),
    # 이스라엘은 시장가치 데이터(마카비 텔아비브 스쿼드 가치 €22M 등, footystats.org)로
    # 볼 때 핀란드보다 형편이 좀 더 나은 걸로 보여 소폭 높게 잡았다.
    # 아이슬란드는 비슷한 소규모 북유럽 리그로 핀란드보다 약간 낮게 추정.
    "핀란드":     0.235,  # C급 base, OVR55 목표 연4,000만원(실측 평균과 일치)
    "이스라엘":   0.295,  # C급 base, OVR55 목표 연5,000만원
    "아이슬란드": 0.195,  # C급 base, OVR55 목표 연3,300만원
    "키프로스":   0.325,  # C급 base, OVR55 목표 연5,500만원(부동산·해운 자금 유입 실측 반영)
    "룩셈부르크": 0.235,  # C급 base, OVR55 목표 연4,000만원(고GDP 반영)
    "파나마":     0.145,  # C급 base, OVR55 목표 연2,500만원(ERI SalaryExpert 실측 평균 기준)
    "조지아":     0.105,  # C급 base, OVR55 목표 연1,800만원(Sporting Intelligence 2018 평균 €13,403 기준)
    "벨라루스":   0.225,  # C급 base, OVR55 목표 연3,800만원(Sporting Intelligence 2018 평균 €31,589 기준)
    "아제르바이잔": 0.325, # C급 base, OVR55 목표 연5,500만원(Sporting Intelligence 2018 평균 €52,638 기준 — 카라바흐 등 국영자금 빅클럽 반영)
    "자메이카":   0.0257,
    "볼리비아":   0.0193,
    "우간다":     0.0514,  # D급 base가 낮아 mult 높임
    "말라위":     0.0257,
    "불가리아":   0.0771,  # D급이지만 유럽 수준
    "슬로바키아": 0.0771,
}

# ── 나라별 연봉 상한 (COUNTRY_SALARY_CAP, 천원/년) ──────────────────────
# OVR90+ 극소수 탑 선수에서 걸리도록 설정.
# OVR65~85 구간은 COUNTRY_SALARY_MULT × base_year × mult(ovr)로 자연 증가.
# 공식: cap ≈ base × cont_mult × mult(90) (OVR90 수준에서 cap 도달)
COUNTRY_SALARY_CAP = {
    # ── SS급 ──────────────────────────────────────────────────────────
    # ※ 잉글랜드는 SALARY_CURVE_OVERRIDE 적용(tier1). 이 값은 tier1 외
    #   구간 및 곡선 외삽 시 안전망 — S급 통일 천장(2,000억)에 맞춰 상향.
    "잉글랜드":    205_000_000,   # 안전망 2,050억

    # ── S급 ───────────────────────────────────────────────────────────
    # ※ 스페인/독일/이탈리아/프랑스도 SALARY_CURVE_OVERRIDE 적용(tier1).
    # [2026-07 재조정, 신민용 지적] "역대급 선수 앞에서는 S급 이상 어디든
    #   ~2,000억까지 가능"이라는 원칙을 5개국에 공통 적용 — 메시 사례는
    #   그 기준값(예시)일 뿐 스페인 전용 특례가 아니었다.
    "스페인":      205_000_000,   # 안전망 2,050억
    "독일":        205_000_000,   # 안전망 2,050억
    "이탈리아":    205_000_000,   # 안전망 2,050억
    "프랑스":      205_000_000,   # 안전망 2,050억
    "브라질":       3_000_000,   # OVR85 약 30억

    # ── A급 ───────────────────────────────────────────────────────────
    "아르헨티나":   2_000_000,   # OVR85 약 20억
    "네덜란드":     2_500_000,
    "포르투갈":     2_500_000,
    "벨기에":       2_000_000,
    "멕시코":       2_500_000,
    "미국":         2_000_000,
    "일본":         4_000_000,   # J1 OVR85 약 40억
    "대한민국":     3_000_000,   # K1 OVR85 약 30억
    "호주":           500_000,   # A리그 약 5억
    "튀르키예":     3_000_000,
    # ※ SALARY_CURVE_OVERRIDE 적용(tier1). 호날두 사례처럼 PIF 4대클럽
    #   슈퍼스타는 실측(호날두 연 €208.4M ≈ 3,126억) 근접까지 가능하도록 상향.
    "사우디아라비아": 320_000_000, # 안전망 3,200억 (호날두 실측 3,126억보다 약간 높게)
    "카타르":      20_000_000,   # QSL top 200억

    # ── B급 유럽 ──────────────────────────────────────────────────────
    "스코틀랜드":     800_000,   # OVR85 약 8억
    "덴마크":         800_000,
    "스웨덴":         500_000,
    "노르웨이":       500_000,
    "스위스":       1_000_000,
    "오스트리아":     800_000,
    "그리스":         500_000,
    "러시아":       2_000_000,
    "우크라이나":   1_000_000,
    "크로아티아":     400_000,

    # ── B급 비유럽 ────────────────────────────────────────────────────
    "콜롬비아":       200_000,
    "에콰도르":       300_000,
    "우루과이":       150_000,
    "모로코":         160_000,
    "나이지리아":     150_000,
    "남아프리카공화국": 300_000,
    "이집트":         200_000,

    # ── C급 유럽 ──────────────────────────────────────────────────────
    "폴란드":         300_000,
    "세르비아":       200_000,
    "체코":           250_000,
    "루마니아":       150_000,
    "헝가리":         150_000,

    # ── C급 중동/아프리카/남미 ────────────────────────────────────────
    "이란":           150_000,
    "알제리":         100_000,
    "튀니지":         100_000,
    "세네갈":          40_000,
    "카메룬":          50_000,
    "가나":            50_000,
    "코트디부아르":   100_000,
    "이라크":         100_000,
    "우즈베키스탄":    80_000,
    "칠레":           200_000,
    "파라과이":       150_000,
    "페루":           100_000,
    "베네수엘라":      80_000,
    "코스타리카":     100_000,

    # ── D급 특수 ──────────────────────────────────────────────────────
    "아랍에미리트":  5_000_000,  # UAE리그 top 50억
    "중국":         1_500_000,   # CSL top 15억
    # [2026-07 밸런스 수정] 말레이시아/인도네시아는 위에서 C급으로 올라가면서
    # 개별 상한을 없앴다 — 예전 D급 시절의 낮은 절대값(3천만원 안팎)을
    # 그대로 두면 중간 OVR에서 벌써 상한에 걸려버려, 그보다 잘하는 선수와
    # 월드클래스급 선수의 연봉이 똑같아지는 문제가 있었다. 국가별 캡 없이
    # 두면 C급 공통 상한(3억원)이 자동 적용되는데, 이는 베트남과 동일 기준.
    # [2026-07 재조정] 태국은 실제 최고 연봉 선수(부리람 유나이티드 비쏘리,
    # 약 €1.03M/년 ≈ 15억원, Transfermarkt/FootyStats 확인)를 반영해 C급
    # 공통 상한(3억)보다 높은 개별 캡을 지정한다 — 최근 버그 수정으로
    # 나라별 캡이 등급 기본값보다 높을 때도 실제로 적용되게 고쳤으니
    # (game_engine._calc_salary) 이 캡이 이제 제대로 작동한다.
    "태국":            1_500_000,  # 15억 (부리람 비쏘리 실제 연봉 기준)
    "자메이카":        30_000,
    "볼리비아":        20_000,
    "우간다":          12_000,
    "말라위":           8_000,
    "불가리아":        80_000,
    "슬로바키아":      80_000,
}

# ══════════════════════════════════════════════════════════════
# tier1 전용 OVR→연봉 앵커 커브 (양극화가 심한 6개 리그)
# ══════════════════════════════════════════════════════════════
# 기존 base_year × _salary_ovr_mult 방식은 나라별로 배율(b)만 다르고
# 곡선 형태(최고:최저 배율비)는 공통이라, 리그마다 실제로 다른
# "최고연봉 : 하위권연봉" 비율(EPL 24배, 라리가 93배, 사우디 100배+ 등)을
# 하나의 곡선으로 동시에 재현할 수 없다.
# 그래서 이 6개 리그만 (하위권 OVR, 하위권 연봉) → (월드클래스 OVR, 최고연봉)
# 두 앵커를 직접 지정하고 그 사이를 지수보간(exponential interpolation)한다.
#   floor_ovr/floor_sal : 그 리그에서 뛸 수 있는 일반적인 하위권/로컬 선수 수준
#   ceil_ovr/ceil_sal   : 월드클래스(이적으로 유입되는 최상급 선수 포함) 최고 수준
# 단위: 천원/년. 실제 2024~2025시즌 최고연봉자 기준 역산.
# [2026-07 재조정, 신민용 지적] "메시가 2,000억을 받은 적 있다"는 예시였을
# 뿐, 실제 의도는 S급 이상 리그라면 어디든 극소수 역대급 선수 앞에서는
# 이 정도(~2,000억) 계약이 나올 수 있다는 뜻이다 — 스페인만의 특례가
# 아니라 S/SS급 5개국 전부의 ceil_sal을 2,000억 선으로 통일했다.
# 반대로 사우디는 "리그 수준 대비 유난히 세게 준다"는 게 핵심 특징이라
# (실제 호날두 실측 3,126억), S/SS급 통일 천장(2,000억)보다 사우디가
# 더 높게 유지된다 — "사우디는 등급(A급)은 유럽 5대리그보다 낮은데도
# 최고 연봉만큼은 그 위"라는 지적하신 특성이 그대로 살아있다.
SALARY_CURVE_OVERRIDE = {
    # 호날두(알 나스르) 사례처럼 PIF 지원 4대 클럽 슈퍼스타는 초고액,
    # 그 외 사우디 로컬/하위팀 선수는 유럽 중소리그 수준으로 낮음.
    # [2026-07 수정, 실측 데이터] 호날두(알 나스르) 2025-27 계약 실측 —
    # Capology 기준 연 €208.4M(약 3,126억원) → OVR100. 벤제마(알 힐랄) 연
    # €122.4M(약 1,836억원)은 OVR99 근방에서 곡선상 자연스럽게 나오도록
    # ceil_ovr을 100으로 옮겼다(전엔 99=호날두라서 벤제마 자리가 없었음).
    "사우디아라비아": {"base_ovr": 50, "base_sal": 150_000,
                    "floor_ovr": 77, "floor_sal":    750_000, "ceil_ovr": 100, "ceil_sal": 312_000_000},
    # 홀란드(맨시티) 약 505억(£27.3M 실측) — floor_sal(로테이션급)은 실측
    # 그대로 두되, ceil_sal(역대급 최상단)은 아래 S급 통일 천장(2,000억)과
    # 맞춘다. floor_ovr을 S등급(스페인 등)과 같은 도메인(86)으로 맞춰서
    # 예전에 도메인이 달라 생겼던 SS<S 역전 버그도 계속 방지된다.
    # [2026-07 신설] base_sal=40만(4억) — EPL 최하위/유망주급 실측(연 2~5억대,
    #   Capology/FootyStats 하위권 확인) 반영. floor_sal(25억=로테이션급)까지
    #   자연스럽게 상승.
    "잉글랜드":       {"base_ovr": 50, "base_sal":    400_000,
                    "floor_ovr": 90, "floor_sal":  2_500_000, "ceil_ovr": 100, "ceil_sal": 200_000_000},
    # 음바페(레알) 약 467억. 라리가 샐러리캡 제도로 하위권은 3~7억 수준.
    # [2026-07 재조정] 메시가 바르셀로나 시절 실제로 연 €138M(변동분 포함
    # 최대 $168.5M, 약 2,000억원) 계약을 받은 적이 있다(2021년 El Mundo
    # 유출 계약서 확인, 역대 스포츠 최고액 계약으로 알려짐) — 이 실측치를
    # S급 공통 "역대급 선수" 천장의 기준값으로 삼았다.
    "스페인":         {"base_ovr": 50, "base_sal":    270_000,
                    "floor_ovr": 87, "floor_sal":    500_000, "ceil_ovr": 100, "ceil_sal": 200_000_000},
    # 케인(바이에른) 약 370억. 50+1룰로 하위권도 아주 낮진 않음(4~8억).
    # [2026-07 신설] base_sal=28만 — 분데스리가 50+1룰 특성상 하위권도 완전
    #   바닥은 아님. ceil_sal은 S급 통일 천장(2,000억).
    "독일":           {"base_ovr": 50, "base_sal":    280_000,
                    "floor_ovr": 88, "floor_sal":    600_000, "ceil_ovr": 100, "ceil_sal": 200_000_000},
    # 블라호비치(유벤) 약 180억. 유벤/인테르/밀란 외 로테이션은 3~6억.
    # [2026-07 신설] base_sal=12만 — 세리에A는 상위 3강 외 스쿼드 편차가 큼.
    #   ceil_sal은 S급 통일 천장(2,000억).
    "이탈리아":       {"base_ovr": 50, "base_sal":    120_000,
                    "floor_ovr": 87, "floor_sal":    450_000, "ceil_ovr": 100, "ceil_sal": 200_000_000},
    # 뎀벨레/마르키뉴스(PSG) 약 180~220억. PSG 제외 17개 팀은 1.5~3억 수준.
    # [2026-07 신설] base_sal=7만 — 리그앙은 PSG 제외하면 유럽 5대리그 중
    #   가장 편차 큰 축(하위권 매우 낮음). ceil_sal은 S급 통일 천장(2,000억)
    #   — PSG(카타르 자금)라면 역대급 선수에게 이 정도도 가능하다고 봄.
    "프랑스":         {"base_ovr": 50, "base_sal":     70_000,
                    "floor_ovr": 87, "floor_sal":    225_000, "ceil_ovr": 100, "ceil_sal": 200_000_000},
}

def salary_curve_value(country: str, ovr: int) -> int:
    """SALARY_CURVE_OVERRIDE 적용 국가의 tier1 연봉(천원) 계산.
    [2026-07 재설계] base→floor→ceil 세 앵커를 2구간 지수보간으로 잇는다
    (예전엔 floor 밑을 전부 flat 처리해 약한 선수와 로테이션급 선수가
    똑같은 연봉을 받는 문제가 있었다). base_ovr 미만은 base 구간과 같은
    비율로 외삽(완전히 0으로 꺼지진 않되 계속 하락), ceil_ovr 초과도
    ceil 구간 비율로 외삽(월드클래스를 넘는 초고액 이적생 반영).
    """
    a = SALARY_CURVE_OVERRIDE.get(country)
    if not a:
        return None
    hi_o, hi_s = a["ceil_ovr"], a["ceil_sal"]
    mid_o, mid_s = a["floor_ovr"], a["floor_sal"]
    if "base_ovr" in a:
        lo_o, lo_s = a["base_ovr"], a["base_sal"]
        if ovr <= mid_o:
            # base~floor 구간(또는 그 아래로 외삽) — 약한/유망주급에서
            # 로테이션급으로 자연스럽게 상승.
            frac = (ovr - lo_o) / (mid_o - lo_o)
            val = lo_s * (mid_s / lo_s) ** frac
        else:
            frac = (ovr - mid_o) / (hi_o - mid_o)
            val = mid_s * (hi_s / mid_s) ** frac
    else:
        # base_ovr이 없는(하위 호환) 나라는 기존 방식 그대로.
        frac = max(0.0, (ovr - mid_o) / (hi_o - mid_o))
        val = mid_s * (hi_s / mid_s) ** frac
    return max(0, int(val))


# [버그수정] SALARY_CURVE_OVERRIDE는 tier1만 재계산하는데, 2부 이하는 예전
# base_year/등급캡(S=200억) 그대로 남아있어서 "1부 최저(잉글랜드 20억)보다
# 2부가 훨씬 높게(170억+) 나오는" 역전이 발생했다. 실제로도 1부>2부가
# 항상 성립해야 하므로, 이 6개국의 2부 이하에는 별도 낮은 안전캡을 건다.
# ※ 각 나라 tier1 floor_sal(SALARY_CURVE_OVERRIDE)보다 반드시 낮게 잡아야
#   "1부 최저 OVR"과 "2부 최고"가 겹쳐도 역전이 안 생긴다.
# [버그수정 2026-07, 신민용 지적: "SS급 2부에서 OVR100 선수한테 1.2억밖에
# 안 준다"] 이 캡이 원래 '단일 flat값'이라 tier 2/3/4/5를 전부 똑같은
# 값으로 눌렀다 — base_year["SS"][2]*_salary_ovr_mult(100)(=163배)처럼
# OVR이 높으면 uncapped 값이 조 단위까지 치솟는 걸 막으려던 안전망인데,
# 그 안전망 자체가 "tier1 최저보다 낮게"라는 목적에만 맞춰져 있어서 2부
# (챔피언십급 — 세계 최고 리그 바로 아래, 명백히 프로 상위권)와 5부(세미
# 프로급)가 완전히 똑같은 상한을 받는 문제가 있었다. 그 결과 OVR100 같은
# 역대급 선수가 2부에 있어도 5부 선수와 동일한 연봉 상한에 눌려버렸다.
# tier1의 floor_sal(로테이션급 실측 앵커) 대비 비율로 tier별 상한을 다시
# 나눠서, tier2>tier3>tier4>tier5 순서를 지키면서도 각 tier 안에서 OVR
# 차이가 여전히 의미 있게 반영되게 한다(tier1 floor_sal 밑으로는 항상
# 유지 — tier1과의 역전은 그대로 방지).
LOWER_TIER_SALARY_CAP = {
    "잉글랜드":       {2: 1_500_000, 3:   625_000, 4:   250_000, 5:   100_000},
    "스페인":         {2:   300_000, 3:   125_000, 4:    50_000, 5:    20_000},
    "독일":           {2:   360_000, 3:   150_000, 4:    60_000, 5:    24_000},
    "이탈리아":       {2:   270_000, 3:   112_500, 4:    45_000, 5:    18_000},
    "프랑스":         {2:   135_000, 3:    56_250, 4:    22_500, 5:     9_000},
    "사우디아라비아": {2:   450_000, 3:   187_500, 4:    75_000, 5:    30_000},
}


# ── 나라×tier 연봉 오버라이드 (base_year 천원/년 직접 지정) ───────────────
# LEAGUE_WEALTH_OVERRIDE가 나라 전체 부유도를 조정한다면,
# 이 테이블은 특정 나라의 특정 부(tier)만 핀포인트로 조정한다.
# _calc_salary에서 wealth 결정 후, 이 테이블이 있으면 base_year를 덮어씀.
#
# base 수치 기준: OVR50 기준 ×0.25 = 실제 월급 (아래 주석은 OVR50 기준 월급)
LOWER_LEAGUE_SALARY_OVERRIDE = {
    # 각 나라 tier3 이하 base를 직접 지정 (천원/년).
    # 각 등급/tier의 평균 OVR에서 목표 월급이 나오도록 역산.
    # 항상 상위 tier 이하 유지 (역전 없음 검증 완료).
    #
    # [버그수정] 2부(tier=2)는 원래 country×tier override 대상이 아니라
    #  base_year×COUNTRY_SALARY_MULT 수식으로만 계산됐다. COUNTRY_SALARY_MULT가
    #  낮은 나라(중동/아프리카/북유럽 일부)는 이 수식값이 아래 3부 절대값보다도
    #  낮아져 "2부가 3부보다 싼" 역전이 났다. 이제 2도 override 대상이라
    #  (game_engine._calc_salary), 아래처럼 2부 값을 직접 지정한 나라는 항상
    #  3부보다 확실히 높게 고정된다.

    # ── SS급 5부제 ──────────────────────────────────────────
    "잉글랜드":   {3: 202_550, 4: 132_058, 5:  79_494},
    # 3부 월1500만 / 4부 월600만 / 5부 월200만
    "독일":       {3: 108_027, 4:  44_019, 5:  31_797},
    # 3부 월800만 / 4부 월200만 / 5부 월80만
    "스페인":     {3:  40_510, 4:  22_009, 5:  15_898},
    # 3부 월300만 / 4부 월100만 / 5부 월40만
    "프랑스":     {3:  33_758, 4:  19_808, 5:  13_911},
    # 3부 월250만 / 4부 월90만 / 5부 월35만
    "이탈리아":   {3:  27_006, 4:  13_205, 5:   7_949},
    # 3부 월200만 / 4부 월60만 / 5부 월20만

    # ── S급 남미 ─────────────────────────────────────────────
    "브라질":     {3:  15_733, 4:   7_942, 5:   4_959},
    # 3부 월100만 / 4부 월30만 / 5부 월10만
    "아르헨티나": {3:   9_440, 4:   5_294, 5:   3_967},
    # 3부 월60만 / 4부 월20만 / 5부 월8만

    # ── S/A급 유럽 ───────────────────────────────────────────
    "포르투갈":   {3:  33_014, 4:  23_848, 5:   5_962},
    # 3부 월150만 / 4부 월60만 / 5부 월15만
    "네덜란드":   {3:  26_411, 4:  15_898, 5:   4_769},
    # 3부 월120만 / 4부 월40만 / 5부 월12만
    "벨기에":     {3:  15_733, 4:   9_265, 5:   4_959},
    # 3부 월100만 / 4부 월35만 / 5부 월10만
    "크로아티아": {2:  19_000, 3:   9_440, 4:   5_294},
    # 2부 월160만 / 3부 월60만 / 4부 월20만

    # ── A급 아시아 ───────────────────────────────────────────
    "대한민국":   {2:  72_000, 3:  33_014, 4:  16_000},
    # K2 월600만 / K3 월150만 / K4 월35만
    "일본":       {3:  26_411, 4:  13_000},
    # J3 월120만 / JFL 월30만
    "이란":       {2:  41_500, 3:  31_797, 4:  13_000},
    # 이란2부 월350만 / 이란3부 월80만 / 이란4부 월17만

    # ── A급 북미 ─────────────────────────────────────────────
    "미국":       {4: 0},
    # USL리그투 NCAA 규정상 무급
    "멕시코":     {3:   8_803, 4:   1_987},
    # 3부 월40만 / 4부 월5만

    # ── 남미 기타 ────────────────────────────────────────────
    "콜롬비아":   {2:  12_000, 3:   5_833, 4:   2_780},
    # 2부 월100만 / 3부 월20만 / 4부 월5만
    "우루과이":   {2:   8_800, 3:   3_933, 4:   2_117},
    # 2부 월70만 / 3부 월25만 / 4부 월8만

    # ── 아프리카 ─────────────────────────────────────────────
    "모로코":     {2:  11_700, 3:   8_750, 4:   5_560},
    # 2부 월100만 / 3부 월30만 / 4부 월10만
    "세네갈":     {2:   5_200, 3:   3_974, 4:   2_366},
    # 2부 월40만 / 3부 월10만 / 4부 월3만

    # ── B급 북유럽/중유럽 ────────────────────────────────────
    "스위스":     {2:  49_000, 3:  23_334, 4:  11_120},
    # 2부 월400만 / 3부 월80만 / 4부 월20만
    "오스트리아": {2:  37_000, 3:  17_500, 4:   8_340},
    # 2부 월300만 / 3부 월60만 / 4부 월15만
    "덴마크":     {2:  40_000, 3:  20_417, 4:  13_900},
    # 2부 월330만 / 3부 월70만 / 4부 월25만
    "스웨덴":     {2:  30_000, 3:  17_500, 4:  11_120},
    # 2부 월250만 / 3부 월60만 / 4부 월20만
}

# ── 나라별 연봉 "바닥값"(실제 최저연봉/평균 실측 기준, 천원/년) ──────────
# [2026-07 신설, 실측 데이터 기반] 위 LOWER_LEAGUE_SALARY_OVERRIDE는 base_year를
# 지정할 뿐이라, calc_ovr 커브가 낮은 OVR 구간에서 워낙 가파르게 깎아내려서
# (예: OVR40~50 구간) 실제 리그의 법적/관행적 최저 대우보다도 한참 낮은 값이
# 나오는 경우가 있었다. 특히 대한민국 K4리그는 대한축구협회 규정상
# 연봉단위 계약 시 법정 최저연봉이 2천만원으로 못박혀 있는데(namu.wiki
# "K4리그/규정" 확인), 기존 계산식은 OVR55에서도 483만원 수준으로 나와
# 법정 최저치의 1/4에도 못 미쳤다. 스페인도 RFEF(3~5부) 각 리그별로
# 실제 계약 관행 최저치가 웹에 보고돼 있다(1부 RFEF 2~3.5만유로,
# 2부 RFEF 월1.2~2.5천유로, 3부 RFEF 월300~800유로 등 — futboljobs.com,
# osdcsports.com 등 확인). 이 값들을 실측 기준으로 별도 "바닥"으로 두고,
# _calc_salary가 계산한 값이 이 바닥보다 낮을 때만 끌어올린다(절대 깎지
# 않음 — 이미 바닥보다 높은 고OVR 선수 연봉엔 전혀 영향 없음).
# [주의] 이 목록은 실측 데이터를 확보한 나라만 우선 채워뒀다 — 나머지
# 나라는 기존 로직(LOWER_LEAGUE_SALARY_OVERRIDE + 등급별 안전 바닥)을
# 그대로 쓴다. 다른 나라도 구체적 실측 근거가 있으면 여기에 추가하면 된다.
LOWER_LEAGUE_SALARY_FLOOR = {
    "대한민국": {
        1: 45_000,   # K리그1: K4 법정최저(2천만원)보다는 확실히 위 — 톱리그 백업 선수도
                     #         세미프로 리그 법정 최저보다는 벌어야 함(안전망 성격)
        2: 35_000,   # K리그2: K3보다 위, K1보다 아래
        3: 25_000,   # K3리그: 세미프로지만 20명+ 연봉계약 의무(나무위키 확인)
        4: 20_000,   # K4리그: 대한축구협회 규정상 법정 최저연봉 2천만원 (실측 확정값)
    },
    "스페인": {
        # 2부(Segunda División) 법정 최저 9.1~9.3만유로/년 (2023~24 협약) ×약1,500원/유로
        2: 137_000,
        # 3부(Primera RFEF) 법정 최저 2~3.5만유로/년(구단 매출 규모별) → 평균값 기준
        3: 45_000,
        # 4부(Segunda RFEF) 공식 최저는 없으나 실보고 월1.2~2.5천유로 평균
        4: 25_000,
        # 5부(Tercera RFEF) 지역리그, 실보고 월300~800유로 평균(완전 무급 구단도 있음 — 이건 평균 기준)
        5: 8_000,
    },
    # [2026-07 신설, 실측 데이터] 나이지리아 NPFL — 2026/27 시즌부터 전 선수
    # 법정 최저월급 2백만나이라(약 22만원×0.9환산 아님, 나이라/원 환율
    # 약 0.9원/나이라 기준 연 약 2,160만원) 시행 확정(2주 전 발표, NSC·NFF
    # 승인). 기존 평균은 월 38~42만나이라(연 약 410~453만원) 수준이었다.
    "나이지리아": {1: 21_600},
}

# ════════════════════════════════════════════════════════════════
# [기능1] 이적 오퍼 맥락 시스템 — 역할 / 감독 관심도 / 구단 야망 / 계약 옵션
# ════════════════════════════════════════════════════════════════

# 오퍼 역할: 입단 후 기대 출전 + 벤치 확률 보정 + 감독관계 초기값 보정
#   bench_mult : 벤치 확률에 곱(주전일수록 낮음)
#   rel_init   : 입단 시 manager_relation 초기값 (주전 보장일수록 높음)
#   ovr_gap_pref : 이 역할이 뜨기 위한 (팀평균OVR - 내OVR) 선호 구간
OFFER_ROLES = {
    "주전 보장":   {"bench_mult": 0.45, "rel_init": 62, "press": 1.20, "desc": "즉시 주전으로 기용"},
    "주전 경쟁":   {"bench_mult": 0.85, "rel_init": 50, "press": 1.05, "desc": "경쟁을 통한 주전 도전"},
    "로테이션":    {"bench_mult": 1.25, "rel_init": 48, "press": 0.85, "desc": "로테이션 자원"},
    "유망주 영입": {"bench_mult": 1.40, "rel_init": 55, "press": 0.70, "desc": "미래를 보고 육성"},
}

# 감독 관심도: 오퍼 카드에 표시 + 입단 시 감독관계 가산
OFFER_INTEREST = {
    "감독 직접 지명": {"rel_bonus": +12, "weight": 25, "desc": "감독이 당신을 콕 집어 원함"},
    "구단 추천":      {"rel_bonus": +4,  "weight": 45, "desc": "구단이 영입을 추천"},
    "명단 후보":      {"rel_bonus": 0,   "weight": 30, "desc": "영입 후보 명단에 포함"},
}

# 구단 야망: 입단 후 기대치(압박) 결정 → 방출 임계치에 영향
OFFER_AMBITION = {
    "우승 도전":     {"press": 1.35, "weight": 18, "desc": "리그 우승이 목표"},
    "상위권 도전":   {"press": 1.15, "weight": 30, "desc": "유럽대회 진출권 목표"},
    "중위권 안정":   {"press": 1.00, "weight": 34, "desc": "안정적인 시즌 운영"},
    "강등 회피":     {"press": 0.80, "weight": 18, "desc": "잔류가 최우선"},
}

# 계약 옵션(보너스): 입단 시 부여, 시즌 정산에 반영(가벼운 보상)
#   appearance_bonus_k : 경기당 출전 보너스(천원)
#   goal_bonus_k       : 골/도움당 보너스(천원)
# 티어/등급이 좋을수록 보너스 규모 ↑
def offer_bonus_by_tier(tier: int):
    base = {1: (40, 120), 2: (18, 55), 3: (6, 20)}.get(tier, (6, 20))
    return {"appearance_bonus_k": base[0], "goal_bonus_k": base[1]}


# ════════════════════════════════════════════════════════════════
# [기능2] 감독 성향 시스템 — 팀(감독)마다 타입 부여, 벤치/관계/방출 보정
# ════════════════════════════════════════════════════════════════
#   bench_mult       : 벤치 확률 곱
#   rel_gain_mult    : 좋은 평점 시 관계 상승 곱
#   rel_loss_mult    : 나쁜 평점 시 관계 하락 곱
#   release_relax    : 방출 임계치 완화(+면 잘 안 자름)
#   stress_mult      : 경기/훈련 스트레스 곱
#   youth_pref_age   : 이 나이 이하면 벤치 확률 추가 완화(유스 중시)
MANAGER_TYPES = {
    "뚝심형":     {"bench_mult": 0.75, "rel_gain_mult": 0.8, "rel_loss_mult": 0.5,
                  "release_relax": +0.15, "stress_mult": 1.0, "youth_pref_age": 0,
                  "desc": "한번 믿으면 부진해도 꾸준히 기용"},
    "성과주의":   {"bench_mult": 1.20, "rel_gain_mult": 1.3, "rel_loss_mult": 1.4,
                  "release_relax": -0.15, "stress_mult": 1.10, "youth_pref_age": 0,
                  "desc": "결과로 모든 걸 판단, 부진하면 가차없이"},
    "유스 중시":  {"bench_mult": 1.0, "rel_gain_mult": 1.1, "rel_loss_mult": 0.9,
                  "release_relax": +0.10, "stress_mult": 0.95, "youth_pref_age": 23,
                  "desc": "어린 유망주를 적극 기용·육성"},
    "베테랑 신뢰": {"bench_mult": 1.0, "rel_gain_mult": 1.0, "rel_loss_mult": 1.0,
                  "release_relax": 0.0, "stress_mult": 0.95, "youth_pref_age": -1,
                  "desc": "경험 많은 선수를 선호"},
    "엄격함":     {"bench_mult": 1.05, "rel_gain_mult": 0.9, "rel_loss_mult": 1.2,
                  "release_relax": -0.05, "stress_mult": 1.20, "youth_pref_age": 0,
                  "desc": "훈련·규율이 혹독해 스트레스가 크다"},
    "온화함":     {"bench_mult": 0.95, "rel_gain_mult": 1.2, "rel_loss_mult": 0.7,
                  "release_relax": +0.10, "stress_mult": 0.85, "youth_pref_age": 0,
                  "desc": "선수를 다독이며 분위기를 중시"},
}
MANAGER_TYPE_LIST = list(MANAGER_TYPES.keys())
# 등장 가중치 (현실감: 성과주의/뚝심형이 흔함)
MANAGER_TYPE_WEIGHTS = [22, 24, 12, 12, 15, 15]


# ════════════════════════════════════════════════════════════════
# [기능3] 능동 액션 — 이적 요청 / 재계약 협상
# ════════════════════════════════════════════════════════════════
# 이적 요청: 감독관계 하락 감수 → 다음 오퍼 창에서 오퍼 수/품질 ↑
TRANSFER_REQUEST_REL_PENALTY = 25     # 요청 시 감독관계 즉시 하락
TRANSFER_REQUEST_OFFER_BONUS = 2      # 다음 오퍼 창 오퍼 개수 +n

# 재계약 협상: 성공 시 연봉 인상 + 계약 연장, 실패 시 감독관계 소폭 하락
#   협상 성공 확률 = base + 평점보정 + 감독관계보정
RENEW_NEGOTIATE = {
    "base_prob": 0.45,
    "rating_per_point": 0.18,   # (평점-6.5) * 이 값
    "rel_per_10": 0.06,         # (관계-50)/10 * 이 값
    "raise_success": (0.12, 0.30),  # 성공 시 연봉 인상폭 범위
    "raise_fail_rel": -8,           # 실패 시 감독관계 변화
    "extend_years": 2,              # 성공 시 연장 연수
}

# ════════════════════════════════════════════════════════════════
# [경기 로그 풍부화] 문구 풀 — 같은 이벤트도 다양한 표현으로 출력
# ════════════════════════════════════════════════════════════════
# 포지션별 긍정/부정 플레이 문구 풀. _pos_events()가 여기서 랜덤 추출한다.
#   기존 3개씩 → 8~12개씩으로 확장해 반복 체감을 줄인다.
#   텍스트만 다양화하는 1단계이므로 평점 영향은 기존 로직(±0.3) 그대로 유지.
MATCH_PHRASES = {
    "GK": (
        ["선방 성공!", "공중볼 장악", "정확한 킥 배급", "1대1 저지!", "빠른 발 빼기",
         "크로스 차단", "침착한 빌드업", "각 좁히기 성공", "위치 선정 완벽", "데드볼 처리 안정"],
        ["포지셔닝 실수로 위기", "펀칭 미스", "킥 부정확", "공중볼 놓침",
         "느린 반응", "백패스 처리 불안", "각 내주며 위기 허용"],
    ),
    "CB": (
        ["태클 성공!", "헤딩 클리어", "인터셉트", "라인 컨트롤 완벽", "몸싸움 압도",
         "위기 차단!", "빌드업 전개", "커버 플레이 일품", "공중볼 제압", "수비 조율 리드"],
        ["마킹 실수", "태클 미스", "헤딩 경합 패배", "라인 붕괴 유발",
         "백패스 실수", "공간 허용", "몸싸움 밀림"],
    ),
    "LB": (
        ["오버랩 침투!", "정확한 크로스", "측면 태클 성공", "공격 가담 활발", "1대1 수비 완벽",
         "라인 백업 안정", "빠른 전환 시발점", "측면 봉쇄"],
        ["역습 허용", "마킹 실수", "크로스 차단 실패", "측면 공간 노출", "오버랩 후 복귀 지연"],
    ),
    "RB": (
        ["오버랩 침투!", "정확한 크로스", "측면 태클 성공", "공격 가담 활발", "1대1 수비 완벽",
         "라인 백업 안정", "빠른 전환 시발점", "측면 봉쇄"],
        ["역습 허용", "마킹 실수", "크로스 차단 실패", "측면 공간 노출", "오버랩 후 복귀 지연"],
    ),
    "CDM": (
        ["볼 차단!", "전진 패스 연결", "수비 라인 보호", "공간 메우기 완벽", "압박 차단",
         "템포 조절 리드", "롱패스 전환 성공", "위치 선정 탁월"],
        ["패스 미스", "포지셔닝 실수", "압박 회피 실패", "공 빼앗김", "커버 지연"],
    ),
    "CM": (
        ["키패스 성공", "드리블 돌파", "공간 침투", "박스투박스 활약", "전환 패스 일품",
         "중원 장악", "전진 드리블", "압박 탈출 성공", "경기 조립 리드"],
        ["턴오버", "패스 미스", "공 소유 실패", "압박에 고전", "중원 장악 실패"],
    ),
    "CAM": (
        ["창의적 스루패스", "드리블 돌파 성공!", "공간 침투", "기회 창출", "전방 연계 일품",
         "킬패스 시도", "수비 라인 붕괴 유도", "공간 발견", "원투 패스 전개"],
        ["찬스 창출 실패", "결정력 부족", "패스 차단당함", "공 소유 빼앗김", "연계 실패"],
    ),
    "LW": (
        ["폭발적 드리블 돌파!", "정확한 크로스", "속도로 측면 제압", "컷인 슈팅 시도", "1대1 돌파 성공",
         "역습 선봉", "측면 헤집기", "백라인 흔들기"],
        ["드리블 실패", "크로스 미스", "수비에 막힘", "오프사이드", "마무리 부정확"],
    ),
    "RW": (
        ["폭발적 드리블 돌파!", "정확한 크로스", "속도로 측면 제압", "컷인 슈팅 시도", "1대1 돌파 성공",
         "역습 선봉", "측면 헤집기", "백라인 흔들기"],
        ["드리블 실패", "크로스 미스", "수비에 막힘", "오프사이드", "마무리 부정확"],
    ),
    "CF": (
        ["공간 침투 성공!", "영리한 연계 플레이", "키패스 배급", "포스트 플레이 안정", "수비 끌어들이기",
         "헤딩 경합 승리", "박스 안 침투", "연계 후 전환"],
        ["빅찬스 미스", "오프사이드", "마무리 실패", "고립", "연계 끊김"],
    ),
    "ST": (
        ["날카로운 공간 침투!", "포스트 플레이 완벽", "위협적 슈팅", "수비 등 뒤 침투", "헤딩 경합 승리",
         "결정적 움직임", "박스 장악", "압박으로 실수 유도"],
        ["빅찬스 미스!", "결정력 부족", "오프사이드", "고립", "슈팅 부정확"],
    ),
}

# 골 상황별 묘사 풀. (분/점수 맥락에 따라 _write_match_log 가 골라 쓴다)
GOAL_PHRASES = {
    "normal":  ["⚽ 침착한 마무리 골!", "⚽ 깔끔한 득점!", "⚽ 골망을 흔들다!",
                "⚽ 정확한 슈팅으로 득점!", "⚽ 결정적 한 방!"],
    "opener":  ["⚽ 선제골을 터뜨리다!", "⚽ 균형을 깨는 선제 득점!", "⚽ 경기 첫 골!"],
    "equalizer":["⚽ 동점골!", "⚽ 경기를 원점으로! 동점 득점", "⚽ 균형을 되돌리는 골!"],
    "winner":  ["⚽ 결승골!! 승리를 결정짓다", "⚽ 천금같은 결승골!", "⚽ 승부를 가르는 골!"],
    "comeback":["⚽ 역전골!! 경기를 뒤집다", "⚽ 짜릿한 역전 득점!", "⚽ 분위기를 가져오는 역전골!"],
    "late":    ["⚽ 종료 직전 극장골!!", "⚽ 버저비터 같은 막판 골!", "⚽ 후반 추가시간 결승골!!"],
}

# 총평 풀 — (평점대, 결과) 조합. _write_match_log 에서 맥락에 맞게 추출.
VERDICT_PHRASES = {
    "great_win":  ["🌟 경기를 지배한 완벽한 활약!", "🌟 팀 승리를 이끈 발군의 플레이!", "🌟 인생 경기를 펼치다!"],
    "great":      ["⭐ 빛나는 개인 활약", "⭐ 경기 내내 위협적이었다", "⭐ 최고의 컨디션"],
    "good_win":   ["👍 승리에 기여한 좋은 경기", "👍 안정적인 활약으로 승리 보탬", "👍 제 몫을 다한 경기"],
    "good":       ["🙂 무난하고 좋은 경기", "🙂 꾸준한 활약", "🙂 안정적인 플레이"],
    "average":    ["😐 평범한 경기", "😐 특별할 것 없는 하루", "😐 무난했던 경기"],
    "poor":       ["😞 아쉬움이 남는 경기", "😞 부진했던 하루", "😞 컨디션이 올라오지 않았다"],
    "terrible":   ["💀 최악의 경기", "💀 잊고 싶은 하루", "💀 경기 내내 헤맸다"],
    "loss_effort":["😤 패했지만 분투한 경기", "😤 패배 속 빛난 투혼", "😤 결과는 아쉬웠던 호투"],
}
# ══════════════════════════════════════════════════════════════
# [2026-07 신설] 컵/챔스/국제대회 결장 사유 한글 라벨.
# get_my_cup_matches/get_my_cl_matches/get_my_intl_matches가 반환하는
# "absence_reason" 필드를 커리어/은퇴창 결과 표시에 "(부상)" 식으로
# 붙이기 위해 ui/career_window.py, ui/retire_window.py가 공유해서 쓴다.
# ══════════════════════════════════════════════════════════════
ABSENCE_REASON_KO = {
    "injury": "부상",
    "suspension": "출전정지",
    "bench": "벤치",
    "red_card": "조기퇴장",
}


def format_result_with_absence(m: dict) -> str:
    """경기 dict(result, absence_reason 키 포함)를 받아 결장 사유가 있으면
    "패 (부상)" 식으로, 없으면 그냥 결과 문자열을 반환한다."""
    res = m.get("result", "")
    reason = m.get("absence_reason")
    if reason:
        label = ABSENCE_REASON_KO.get(reason, reason)
        return f"{res} ({label})"
    return res