# constants.py
import random

GAME_START_YEAR = 2000
PLAYER_START_AGE = 16
MIN_JOIN_AGE = 17
# [2026-07 신설, 신민용 요청] 16세에 대륙컵(네이션스컵) 발탁 선택창이 뜨는 게
# 비현실적이라는 피드백 — 세계 대회 자체(AI 국가들 진행)는 그대로 두고,
# "내가 후보로 뽑힐 수 있는 최소 나이"만 따로 제한한다.
MIN_INTL_CALLUP_AGE = 17
# [2026-08 신설, 신민용 요청: "새 선수 생성 때 시작 연도/나이 직접 선택
# 가능하게"] 새 선수 생성 화면에서 GAME_START_YEAR/PLAYER_START_AGE를 이
# 범위 안에서 직접 고를 수 있다(안 고르면 기본값 그대로 GAME_START_YEAR/
# PLAYER_START_AGE 사용). MAX_AGE(기존 50)는 로그 한 줄 찍는 것 말고
# 실질적인 효과가 없어서(강제 은퇴 등 아무것도 안 함) 신민용 요청으로
# 제거했다.
PLAYER_START_YEAR_MIN = 1986
PLAYER_START_YEAR_MAX = 2020
PLAYER_START_AGE_MIN = 14
PLAYER_START_AGE_MAX = 28

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
#   - 국제대회 전용 비시즌:    302~364일 (44~52주, 총 63일) — 이 구간엔
#     클럽 경기가 전혀 없다. 월드컵/대륙컵 본선·예선이 전부 여기서 열린다.
#     [2026-07 버그수정, 신민용 리포트: "42주차에 끝나던 일정이 43주차까지
#     늘어남 / 월드컵·네이션스컵 기록이 없다"] 예전엔 301일로 잡혀 있었는데,
#     day_to_week()가 (day-1)//7+1 이라 실제로는 301일도 43주차(클럽
#     시즌의 마지막 주와 동일)로 계산돼 국제대회 주간이 클럽 시즌 마지막
#     주와 겹쳐버렸다(SEASON_PHASES의 second_half=(23,43)과 postseason=
#     (43,52)이 43에서 겹치는 것으로 확인됨). 44주차의 진짜 첫째 날은
#     302일이다.
CLUB_PRESEASON_START_DAY = 1
CLUB_PRESEASON_END_DAY   = 21

CLUB_SEASON_START_DAY = 22
CLUB_SEASON_MID_DAY   = 161   # 상/하반기 분기점(홈/원정 반전 기준)
CLUB_SEASON_END_DAY   = 300

INTL_OFFSEASON_START_DAY = 302
INTL_OFFSEASON_END_DAY   = 364

# 중간 휴식기(구 겨울 이적시장) — [2026-07 확장] 원래 2주(190~203)였던 걸
# 클럽 경기가 통째로 쉬는 4주 휴식기로 확장. 이 기간엔 경기가 없다(이전엔
# "경기는 안 끊김"이었으나 이번 변경으로 뒤집힘). 월드컵 예선이 있는 해엔
# 이 4주 안에서 예선(7라운드, 4일 간격)이 전부 진행된다.
#
# [2026-08 재조정, 신민용 확정: "후반기(12주)가 전반기(24주)의 절반도
# 안 돼서 후반기만 경기가 지나치게 촘촘하다"] 휴식기를 4주 앞당겨서
# 전/후반기 길이 비율을 2.02:1(168일:83일) → 1.40:1(147일:105일)로
# 완화했다 — 휴식기 자체의 길이(4주)와 시즌 총 종료 시점(43주차,
# CLUB_SEASON_END_DAY=300)은 그대로 유지, 오직 휴식기의 "위치"만 앞으로
# 당김. week_to_day(25)=169, week_to_day(29)-1=196으로 정확히 4주(28일)
# 폭을 유지한다.
WINTER_OFFER_START_DAY = 169   # 25주차 시작 (기존 190=28주차)
WINTER_OFFER_END_DAY   = 196   # 28주차 끝   (기존 217=31주차 끝)

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
# [2026-07 수정] 중간 휴식기(WINTER_OFFER_START/END_DAY, 190~217일) 도입으로
# 상/하반기가 더는 붙어있지 않고 그 사이에 간격이 생긴다. 이 간격은
# _phase_label()의 "week < ss" 폴백이 자동으로 '비시즌'으로 표시한다
# (새 UI 코드 불필요 — 이 상수만 바뀌면 화면에 그대로 반영됨).
FIRST_HALF_END_DAY    = WINTER_OFFER_START_DAY - 1   # 189
SECOND_HALF_START_DAY = WINTER_OFFER_END_DAY + 1     # 218
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

# [2026-08 재설계, 신민용 확정: "round(19/(n-1)) 반올림 때문에 13팀→48경기,
# 14팀→26경기처럼 팀 수 경계마다 불규칙한 절벽이 생긴다"] 예전 방식은
# 20팀 기준 라운드 수를 거꾸로 역산하다 보니 매끄럽지 않은 계단이 생겼다.
# 대신 "팀 수 구간별 고정 다전제 배수" 표로 바꿔서, 절벽이 딱 두 지점
# (13→14팀: 다전제→왕복, 24→25팀: 왕복→단판)에만 생기게 하고 나머지
# 구간은 항상 매끄럽게 4경기씩만 늘어난다. 이 두 절벽도 실제 축구에서
# "왕복리그/단판리그 포맷이 갈리는 지점"과 같은 성격이라 정상이다.
# 25팀 이상은 legs=1(단판, 전 팀이 서로 딱 1번씩만) — _build_league_
# schedule_rows의 is_single_round 분기가 이 경우를 별도 처리한다.
def legs_for_team_count(n: int, target: int = TARGET_ROUNDS_PER_LEG) -> int:
    """총 맞대결 횟수. legs=1이면 단판(왕복 아님) — 그 외는 전부 짝수
    (기존 '왕복 2전 사이클' 구조 재사용).
    예: 7팀 이하→6전, 8~13팀→4전, 14~24팀→2전(기존과 동일), 25팀 이상→1전(단판)."""
    if n <= 1:
        return 2
    if n <= 7:
        return 6
    if n <= 13:
        return 4
    if n <= 24:
        return 2
    return 1

def season_cycle_windows(n_cycles: int):
    """[2026-07 재설계, 중간 휴식기 도입] 예전엔 CLUB_SEASON_START_DAY~
    END_DAY(22~300) 전체를 통짜로 n_cycles개로 나눴는데, 이러면 중간
    휴식기(WINTER_OFFER_START_DAY~END_DAY, 190~217)를 관통하는 라운드가
    생길 수 있다 — 그 구간엔 경기가 없어야 하므로 이건 버그가 된다.

    그래서 이제 시즌을 휴식기 기준으로 두 풀로 미리 나눠둔다:
      pre_pool  (22~189)  — 모든 사이클의 1다리(h1)는 여기서만 배정
      post_pool (218~300) — 모든 사이클의 2다리(h2)는 여기서만 배정
    각 풀 안에서 사이클 수(n_cycles)만큼 다시 균등 분할한다(사이클이
    여러 개인 소규모 다전제 리그, 예: 8팀 3사이클 대응). 이러면 어떤
    다리/라운드도 휴식기 내부를 침범할 수 없다 — 풀 경계 자체가 이미
    휴식기를 피해서 그어져 있기 때문.

    사이클이 1개(legs=2, 대부분의 팀 수)면 h1=pre_pool 전체,
    h2=post_pool 전체가 되어 "1다리=전반기, 2다리=후반기, 그 사이에
    휴식기" 라는 가장 직관적인 형태로 자연스럽게 떨어진다.

    [2026-07 이전 버그 수정, 유지] 사이클 경계가 겹쳐서 같은 팀이 같은
    날 두 번 배정되던 문제 — 첫 구간을 뺀 나머지는 이전 구간 끝난
    다음날부터 시작해서 경계일이 안 겹치게 하는 방식은 각 풀 내부에도
    동일하게 적용한다.
    """
    pre_start, pre_end   = CLUB_SEASON_START_DAY, WINTER_OFFER_START_DAY - 1
    post_start, post_end = WINTER_OFFER_END_DAY + 1, CLUB_SEASON_END_DAY

    def _split_pool(pool_start, pool_end, n):
        total = pool_end - pool_start
        span = total / n
        bps = [pool_start + round(i * span) for i in range(n + 1)]
        bps[-1] = pool_end  # 마지막 경계는 항상 그 풀의 끝날
        out = []
        for i in range(n):
            s = bps[i] if i == 0 else bps[i] + 1
            e = bps[i + 1]
            out.append((s, e))
        return out

    h1_windows = _split_pool(pre_start, pre_end, n_cycles)
    h2_windows = _split_pool(post_start, post_end, n_cycles)
    return [(h1s, h1e, h2s, h2e)
            for (h1s, h1e), (h2s, h2e) in zip(h1_windows, h2_windows)]

def day_to_week(day: int) -> int:
    """일자를 기존 week 체계로 역산 (1~52로 클램프)."""
    w = (day - 1) // DAYS_PER_WEEK + 1
    return max(1, min(52, w))


def week_to_day(week: int) -> int:
    """week 체계를 day로 정방향 변환 — 그 주의 첫째 날.
    (day_to_week의 역함수. INTL_GROUP_WEEKS 등 기존 week 상수를
    tournament_start_day 앵커로 쓸 때 사용)"""
    return (week - 1) * DAYS_PER_WEEK + 1

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

def week_to_iso_date_str_end(season_year: int, week: int) -> str:
    """기간 표시용 '종료일' — 그 주의 마지막 날짜를 반환한다.
    [2026-07 버그수정, 신민용 리포트: "같은 주에 입단·이적하면
    2001-01-01~2001-01-01처럼 시작=종료로 찍힌다, 2001-01-01~2001-01-07
    이어야 한다"] week_to_iso_date_str()는 항상 그 주의 첫째 날을
    반환하므로, 재직 '기간' 표시에서 종료일에도 그대로 쓰면 같은 주
    안에서 시작과 종료가 같은 날로 보인다. 종료일은 그 주가 끝나는
    마지막 날(7일째)로 잡아야 "그 주까지 재직했다"는 의미가 정확히
    전달된다."""
    day = week * DAYS_PER_WEEK
    return day_to_iso_date_str(season_year, day)


def add_days_to_iso_date_str(season_year: int, day: int, add_days: int) -> str:
    """[2026-08 신설, 부상 이력용] (season_year, day) 기준에서 add_days만큼
    뒤의 날짜를 ISO 문자열로. day는 current_day처럼 큰 누적값일 수 있어서
    (연초로 리셋되지 않음) 실제 datetime을 쓰지 않는다 — 이 게임 캘린더는
    1년=364일(실제 그레고리력 365/366일과 다름)이라 Python datetime으로
    계산하면 여러 해가 지날수록 오차가 누적된다. 대신 day_to_calendar_date가
    쓰는 것과 동일한 364일 경계 기준으로 몇 번째 해로 넘어가는지만 직접
    계산해서 season_year를 그만큼 보정한다."""
    year_blocks_before = (day - 1) // 364
    total_day = day + add_days
    year_blocks_after = (total_day - 1) // 364
    year_offset = year_blocks_after - year_blocks_before
    return day_to_iso_date_str(season_year + year_offset, total_day)


def iso_date_str_to_absolute_day(iso_str: str) -> int:
    """[2026-08 신설, 재발 취약기 판정용] 'YYYY-MM-DD' -> 절대 일수
    (연도*364 + 그 해의 며칠째). 이 함수로 변환한 값끼리만 대소 비교가
    유효하다(예: 취약기 만료일이 지났는지 판정) — 이 게임 캘린더가
    1년=364일이라 실제 그레고리력 날짜 차이 계산과는 안 맞음."""
    y, m, d = map(int, iso_str.split("-"))
    day_of_year = d
    for month, length in zip(_CALENDAR_MONTH_ORDER, _CALENDAR_MONTH_LENGTH):
        if month == m:
            break
        day_of_year += length
    return y * 364 + day_of_year

# ── 기존(주 단위) 코드와의 호환용 파생값 ──────────────────────────
# game_engine.py 등 아직 'week' 정수로 시즌 구간을 비교하는 코드가 많아서,
# 위 day 상수들로부터 주차를 역산해 그대로 제공한다. 이 값들 자체를
# 직접 바꾸지 말고 위 *_DAY 상수를 바꾸면 여기로 자동 반영된다.
FIRST_HALF_START  = day_to_week(FIRST_HALF_START_DAY)    # 4주
SECOND_HALF_START = day_to_week(SECOND_HALF_START_DAY)    # 32주 (중간 휴식기 반영)

SEASON_PHASES = {
    "preseason1":  (day_to_week(CLUB_PRESEASON_START_DAY), day_to_week(CLUB_PRESEASON_END_DAY)),
    "first_half":  (FIRST_HALF_START, day_to_week(FIRST_HALF_END_DAY)),
    "second_half": (SECOND_HALF_START, day_to_week(CLUB_SEASON_END_DAY)),
    "postseason":  (day_to_week(INTL_OFFSEASON_START_DAY), 52),   # = 국제대회 전용 구간
}

# 국제대회 윈도우 — 클럽 시즌과 완전히 안 겹치는 전용 비시즌(301~364일=44~52주)
# 전체를 사용. 월드컵은 조별리그+토너먼트, 대륙컵/예선도 전부 이 안에서 진행.
#
# [2026-07 승강 플레이오프 도입, 캘린더 재설계] 원래는 INTL_OFFSEASON_WEEK_START
# (44주)가 곧 소집 주(INTL_CALLUP_WEEK)였다 — 그 주엔 실제 경기가 없고
# "국가대표 소집!" 로그만 뜨는 순수 버퍼 주였다. 승강 PO를 넣으면서 이 44주를
# PO 전용 주로 쓰기로 했고, 원래 있던 "소집 주"는 그대로 없애지 않고 PO
# 바로 다음 주(45주)로 옮겼다 — 새 이벤트를 만든 게 아니라 기존 소집
# 이벤트가 원래 하려던 역할(경기 없이 대표팀 합류를 준비하는 한 주)을 PO
# 때문에 밀려난 자리에서 되찾은 것뿐이다. 이 아래로 파생되는 GROUP_WEEKS/
# KO_WEEKS는 전부 INTL_CALLUP_WEEK 기준 상대값이라 자동으로 함께 밀린다
# (club_world_cup_engine.CWC_START_DAY 등도 마찬가지로 자동 반영).
#
#   44주 = 승강 플레이오프 (아직 미구현 — 그 전까지는 그냥 빈 주)
#   45주 = 국가대표 소집 (기존 이벤트, 경기 없음)
#   46주 = 조별리그 시작
INTL_OFFSEASON_WEEK_START = day_to_week(INTL_OFFSEASON_START_DAY)   # 44
INTL_OFFSEASON_WEEK_END   = 52
PLAYOFF_WEEK      = INTL_OFFSEASON_WEEK_START                        # 44 — 승강 PO 전용 주
INTL_CALLUP_WEEK  = INTL_OFFSEASON_WEEK_START + 1                    # 45 — 소집/조 추첨
INTL_GROUP_WEEKS  = (INTL_CALLUP_WEEK + 1, INTL_CALLUP_WEEK + 3)   # 조별리그 3경기 (46~48)
INTL_KO_WEEKS     = (INTL_CALLUP_WEEK + 4, INTL_OFFSEASON_WEEK_END)   # 16강~결승 (49~52)

# [2026-09 신설] 랭킹 평가전(power_eval) — 클럽월드컵 해 25주(발탁/조추첨)
# 진입 시 생성, 첫 매치데이는 26주(day176). game_engine._advance_week의
# "POWER_EVAL_CALLUP_WEEK 진입 시 start_power_eval_if_needed 호출" 훅과
# intl_engine._create_power_eval_tournament의 그룹전 시작일 계산에서
# 참조한다 — 두 파일에 이미 있던 import가 정의를 찾지 못해 발생한
# ImportError를 여기서 해소한다(신민용 리포트: 지연 탭 진입 시 크래시).
POWER_EVAL_CALLUP_WEEK = 25
POWER_EVAL_GROUP_START_DAY = week_to_day(26)   # 176

# 오퍼(이적시장) 구간 — [2026-07 확장] 비시즌 3개 구간(프리시즌/중간
# 휴식기/국제 오프시즌) 전부에서 오퍼가 온다. 자동 오퍼 팝업은 이 구간
# 안(in_zone)일 때만 뜬다 (ui/center_panel.py 참고).
#
# [2026-08 버그수정, 신민용 리포트: "오퍼가 44주(PLAYOFF_WEEK)부터 오는데
# 내 팀이 승강 플레이오프에 걸리면 충돌 안 나?"] 정확한 지적 — 자동 오퍼
# 팝업은 모달(dlg.exec())이라 뜨는 동안 진행 자체를 막는데, 44주는 바로 그
# PO 경기가 실제로 열리는 주(위 PLAYOFF_WEEK 설명 참고)라 PO 경기를
# 진행해야 하는 그 주에 오퍼 모달이 끼어들 수 있었다. 국제 오프시즌 쪽
# 오퍼 시작을 PO 다음 주인 INTL_CALLUP_WEEK(45주 — 원래도 "경기 없는
# 순수 버퍼 주"로 설계돼 있던 주)로 한 주 늦춘다 — PO가 없는 팀도 이
# 버퍼 주엔 어차피 아무 경기가 없으니 손해가 없고, PO가 있는 팀은 더 이상
# 겹치지 않는다.
OFFER_ZONES = [
    (day_to_week(CLUB_PRESEASON_START_DAY), day_to_week(CLUB_PRESEASON_END_DAY)),      # 프리시즌
    (day_to_week(WINTER_OFFER_START_DAY),   day_to_week(WINTER_OFFER_END_DAY)),        # 중간 휴식기
    (INTL_CALLUP_WEEK,                       52),                                        # 국제 오프시즌(PO 다음 주부터)
]

# [2026-08 신설, 신민용 확정: 세계기록실 "국가 검색" 탭] intl_tournaments.kind
# 값 → 화면 표시 라벨/짧은 글리프. 국가별 우승 집계(world_browser.
# get_country_trophy_summary 등)가 이 kind 값 기준으로 GROUP BY 하므로,
# 나중에 대회 종류가 늘어나도(예: 컨페더레이션스컵, 네이션스리그 등) 실제
# 대회 생성 코드에서 intl_tournaments.kind에 새 값만 쓰면 집계에는 자동으로
# 잡힌다 — 다만 화면에 예쁜 한글 라벨/이모지로 보이게 하려면 여기에 한 줄만
# 추가하면 된다(안 추가해도 kind 원문 그대로 표시되니 깨지지는 않음).
INTL_TOURNAMENT_KIND_LABELS = {
    "world":     "🌐 월드컵",
    "continent": "🎖 대륙컵",
    "wc_qual":   "🎫 월드컵 예선",
    "region":    "🌏 지역컵",
    # [2026-08 버그수정, 신민용 리포트: "2000년에 '유로 유럽 예선'이라고
    # 뜨는데 유로가 아니라 유럽 네이션스컵 예선이다"] 이 버그를 고칠 때
    # 대회명(intl_engine._create_qual_tournament의 _qual_full_name)은
    # "{year} 유럽 네이션스컵 예선"으로 바로잡았는데, 바로 옆 "종류" 칸에
    # 쓰이는 이 라벨은 그대로 "유로 예선"으로 남아있어서 같은 화면 안에서
    # 이름-종류가 서로 모순됐다. wc_qual이 "continent"가 아니라 "world"의
    # 예선이라는 걸 라벨로 그대로 보여주듯, cont_qual도 "continent"(대륙컵)
    # 의 예선이라는 카테고리를 보여줘야 맞다 — "유로"라는 특정 대회 브랜드는
    # 본선 단계에서만(effective_kind="euro") 쓰고, 예선 단계는 대회명 칸에
    # 이미 "유럽 네이션스컵 예선"으로 정확히 나오므로 종류 칸까지 겹쳐서
    # "유로"를 또 넣을 필요가 없다.
    "cont_qual": "🎫 대륙컵 예선",
    # [2026-08 신설] DB엔 kind='continent'로 같이 저장되지만(유로도 대륙컵과
    # 100% 동일한 조편성/선발 로직을 쓰는 대회라 kind를 안 나눔), 화면
    # 표시/필터링에서는 name으로 구분한 "유효 종류"(world_browser._effective_kind)
    # 로 이 라벨을 따로 쓴다 — DB의 실제 kind 값은 아님, 표시 전용.
    "euro":      "⚡ 유로",
    # [2026-08 신설, 신민용 리포트: "유로(EURO) 예선인데 종류가 대륙컵
    # 예선으로 뜬다"] cont_qual은 유럽 전용으로만 생성되는 대회라서(다른
    # 대륙은 아직 이 경로를 안 탐 — intl_engine._create_qual_tournament
    # 참고) 항상 유로 예선이다. world_browser._effective_kind가 cont_qual을
    # 이 "euro_qual" 유효 종류로 매핑해서 여기 라벨을 쓰게 한다 — 위의
    # "cont_qual" 원본 라벨은 그대로 두되(다른 코드가 kind 원문 그대로
    # 참조할 수 있으므로 하위 호환용으로 유지), 실제 화면은 effective_kind
    # 우선이라 이 라벨이 보인다.
    "euro_qual": "🎫 유로 예선",
    # [2026-09 신설, 신민용 요청: "국가 검색 종류 칸에 power_eval이라고
    # 원문 그대로 뜨는데 친선전으로 뜨게 해달라"] GLYPHS에는 이미
    # power_eval/power_eval_extra 항목이 있었지만(위 주석 참고) 이 화면
    # 라벨 표(LABELS)엔 빠져 있었다 — ui/world_browser_window.py의
    # kind_label 계산이 LABELS.get(effective_kind, kind)라서 못 찾으면
    # kind 원문("power_eval")이 그대로 화면/기록복사에 노출됐던 것.
    # power_eval_extra(16개국 미만 나머지 밴드)도 사용자 입장에선 똑같은
    # "친선전"이므로 같은 라벨을 쓴다.
    "power_eval":       "📊 친선전",
    "power_eval_extra": "📊 친선전",
}
INTL_TOURNAMENT_KIND_GLYPHS = {
    "world":     "🌐",
    "continent": "🎖",
    "wc_qual":   "🎫",
    "region":    "🌏",
    "cont_qual": "🎫",
    "euro":      "⚡",
    "euro_qual": "🎫",
    # [2026-09 신설] 랭킹 평가전(power_eval) — 빠져있어서 국가 검색 등에서
    # 미등록 종류로 취급돼 기본 트로피(🏆)로만 표시되던 문제 수정.
    "power_eval":       "📊",
    "power_eval_extra": "📊",
}
INTL_TOURNAMENT_KIND_FALLBACK_LABEL = "🏆"

# ── [2026-07 신설, 국제대회 일 단위 전환 Phase 2] stage+round 기반 일정 규칙 ──
# 설계 원칙(중요): 각 stage/round는 "하루"가 아니라 "며칠짜리 창"이다.
# 그 창 안에서 daily_match_capacity만큼씩 채워가며 여러 날에 나눠 배정해야
# 실제로 day가 week의 재탕이 아니게 된다 — 라운드당 day 하나만 쓰면 안 됨
# (이전에 이 실수를 했다가 지적받고 수정함: 그러면 "day라는 이름의 week"밖에
# 안 나옴). match_count는 daily_capacity의 배수가 되도록 맞춰뒀다
# (days == ceil(match_count / daily_capacity)).
#
# 필드: (round_number, match_count, days, rest_days_after, daily_match_capacity,
#        min_team_rest_days)
# [2026-07 재조정 v2, 신민용 재요청: "월드컵 실제 기간(32강 4주/48강
# 5~6주)에 맞게 토너먼트를 1일 단위로 압축해서 단축해라"] 한 라운드를
# 1주 간격으로 뒀던 v1(rest_after=6)은 32강 체제 기준 6주, 48강 체제
# 기준 7주가 나와 실제 대회 기간(각각 ~4주/~5~6주)보다 훨씬 길었다.
# R32/R16/QF/SF는 그대로 라운드 전체를 하루(cap=match_count)에 몰아서
# 열되, 다음 라운드까지의 휴식을 6일 → 3일로 줄여 "라운드 간격 4일"
# (실제 대회의 라운드 간 텀과 비슷한 폭)이 되도록 압축한다 — 이렇게
# 해도 하루 사전생성 셸(_precreate_ko_shell) 구조 덕분에 "일정이 안
# 떠서 오류가 난다"던 예전 버그는 재발하지 않는다(그 버그의 원인은
# 간격이 아니라 라운드 진출 시점에 새 행을 INSERT하던 구조 자체였음).
# 실측 결과: 32강 체제 조별리그~결승 총 4주(28일), 48강 체제 총
# 4.5주(32일)로 목표 기간에 맞게 단축됨. 3/4위전(TP)과 결승(F)은
# 실제 대회처럼 그대로 바로 다음날 붙여서(둘 다 rest_after=0) 연다.
TOURNAMENT_SCHEDULE_RULES = {
    "world_cup_32": [
        {"stage": "group", "round": 1, "match_count": 16, "days": 4, "rest_after": 0, "cap": 4, "min_rest": 0},
        {"stage": "group", "round": 2, "match_count": 16, "days": 4, "rest_after": 0, "cap": 4, "min_rest": 2},
        {"stage": "group", "round": 3, "match_count": 16, "days": 4, "rest_after": 2, "cap": 4, "min_rest": 2},
        {"stage": "R16",   "round": 1, "match_count": 8,  "days": 1, "rest_after": 3, "cap": 8, "min_rest": 2},
        {"stage": "QF",    "round": 1, "match_count": 4,  "days": 1, "rest_after": 3, "cap": 4, "min_rest": 3},
        {"stage": "SF",    "round": 1, "match_count": 2,  "days": 1, "rest_after": 3, "cap": 2, "min_rest": 3},
        {"stage": "TP",    "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
        {"stage": "F",     "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
    ],
    "world_cup_48": [
        {"stage": "group", "round": 1, "match_count": 24, "days": 4, "rest_after": 0, "cap": 6, "min_rest": 0},
        {"stage": "group", "round": 2, "match_count": 24, "days": 4, "rest_after": 0, "cap": 6, "min_rest": 2},
        {"stage": "group", "round": 3, "match_count": 24, "days": 4, "rest_after": 2, "cap": 6, "min_rest": 2},
        {"stage": "R32",   "round": 1, "match_count": 16, "days": 1, "rest_after": 3, "cap": 16, "min_rest": 2},
        {"stage": "R16",   "round": 1, "match_count": 8,  "days": 1, "rest_after": 3, "cap": 8, "min_rest": 2},
        {"stage": "QF",    "round": 1, "match_count": 4,  "days": 1, "rest_after": 3, "cap": 4, "min_rest": 3},
        {"stage": "SF",    "round": 1, "match_count": 2,  "days": 1, "rest_after": 3, "cap": 2, "min_rest": 3},
        {"stage": "TP",    "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
        {"stage": "F",     "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
    ],
    # [2026-09 신설] 64팀 체제 — WC_EXPAND_YEAR_64가 설정되기 전까지는
    # 이 항목 자체가 그냥 미사용 상태(아무 대회도 tournament_type=
    # "world_cup_64"로 생성되지 않음). 16조(64팀)라 그룹 라운드당
    # 경기수만 world_cup_48(12조)의 4/3배(24→32)로 늘고, KO 체인은
    # world_cup_48과 완전히 동일(조 1·2위=32팀이 정확히 떨어져서
    # best-3rd 구제가 필요 없어 브래킷 크기가 48팀 체제의 R32와 같음).
    "world_cup_64": [
        {"stage": "group", "round": 1, "match_count": 32, "days": 4, "rest_after": 0, "cap": 8, "min_rest": 0},
        {"stage": "group", "round": 2, "match_count": 32, "days": 4, "rest_after": 0, "cap": 8, "min_rest": 2},
        {"stage": "group", "round": 3, "match_count": 32, "days": 4, "rest_after": 2, "cap": 8, "min_rest": 2},
        {"stage": "R32",   "round": 1, "match_count": 16, "days": 1, "rest_after": 3, "cap": 16, "min_rest": 2},
        {"stage": "R16",   "round": 1, "match_count": 8,  "days": 1, "rest_after": 3, "cap": 8, "min_rest": 2},
        {"stage": "QF",    "round": 1, "match_count": 4,  "days": 1, "rest_after": 3, "cap": 4, "min_rest": 3},
        {"stage": "SF",    "round": 1, "match_count": 2,  "days": 1, "rest_after": 3, "cap": 2, "min_rest": 3},
        {"stage": "TP",    "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
        {"stage": "F",     "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
    ],
    # [잠정] 대륙컵(24개국, 6조): 조별리그 매치수만 world_cup_32의 3/4 규모로
    # 축소하고 나머지 라운드 구조는 동일하게 재사용. 실전 확인 전까지 잠정치.
    "continental": [
        {"stage": "group", "round": 1, "match_count": 12, "days": 4, "rest_after": 0, "cap": 3, "min_rest": 0},
        {"stage": "group", "round": 2, "match_count": 12, "days": 4, "rest_after": 0, "cap": 3, "min_rest": 2},
        {"stage": "group", "round": 3, "match_count": 12, "days": 4, "rest_after": 2, "cap": 3, "min_rest": 2},
        {"stage": "R16",   "round": 1, "match_count": 8,  "days": 1, "rest_after": 3, "cap": 8, "min_rest": 2},
        {"stage": "QF",    "round": 1, "match_count": 4,  "days": 1, "rest_after": 3, "cap": 4, "min_rest": 3},
        {"stage": "SF",    "round": 1, "match_count": 2,  "days": 1, "rest_after": 3, "cap": 2, "min_rest": 3},
        {"stage": "TP",    "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
        {"stage": "F",     "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
    ],
    # [2026-08 신설] 지역컵 — 규모가 지역마다 다르지만(브래킷 4/8/16강)
    # KO 단계 체인 자체는 항상 R16→QF→SF→F 순서 중 뒷부분을 쓰는 형태라
    # (_ko_seq가 실제로 어디부터 시작할지 골라 씀) 체인 전체를 하나로
    # 등록해둔다 — _precreate_ko_shell이 이 표를 보고 R16/QF/SF/F 전
    # 스테이지의 빈 대진(placeholder) 행을 미리 만들어야 다음 라운드로
    # 정상 진행된다(등록 안 돼 있으면 첫 KO 라운드 이후 진행이 멈춤 —
    # 실측으로 확인된 버그, 신민용 리포트). match_count는 최댓값 기준
    # (카리브 23개국) 참고용 상한이고, 실제 배정은 그때그때 조 수만큼만
    # 채워진다(assign_match_days가 실제 개수를 직접 받아서 처리).
    # [2026-08 버그수정, 신민용 지적: "한 조에 5팀 들어갈 때랑 8강부터
    # 시작할 때 설계가 덜 된 거 아니냐"] 정확한 지적이었다 — 조 인원이
    # 3~4명이면 라운드로빈 3라운드로 끝나지만, 5명이면 4라운드가
    # 필요한데(_round_robin_pairs(5)는 5라운드를 반환) 이 표엔 원래
    # 3라운드 분량밖에 없었다. stage_round_start_day가 이 표를 그대로
    # 누적해서 다음 스테이지(KO) 시작일을 계산하기 때문에, 5팀 조가
    # 있는 대회(CECAFA/WAFU)는 실제 조별리그가 아직 4~5라운드째
    # 진행 중인데 다음 KO 스테이지 날짜가 이미 지나버리는 충돌이
    # 생길 수 있었다. 조 최대 인원(5)에 맞춰 라운드를 5개까지로
    # 늘려서 안전하게 여유를 둔다 — 3~4팀짜리 조뿐인 대회는 4·5라운드
    # 자리에 배정할 경기가 아예 없어서(그 라운드의 round_matches가
    # 비어있음) 실질적으로 아무 영향 없다.
    "region": [
        {"stage": "group", "round": 1, "match_count": 14, "days": 4, "rest_after": 0, "cap": 4, "min_rest": 0},
        {"stage": "group", "round": 2, "match_count": 14, "days": 4, "rest_after": 0, "cap": 4, "min_rest": 2},
        {"stage": "group", "round": 3, "match_count": 14, "days": 4, "rest_after": 2, "cap": 4, "min_rest": 2},
        {"stage": "group", "round": 4, "match_count": 14, "days": 4, "rest_after": 2, "cap": 4, "min_rest": 2},
        {"stage": "group", "round": 5, "match_count": 14, "days": 4, "rest_after": 2, "cap": 4, "min_rest": 2},
        {"stage": "R16",   "round": 1, "match_count": 8,  "days": 1, "rest_after": 3, "cap": 8, "min_rest": 2},
        {"stage": "QF",    "round": 1, "match_count": 4,  "days": 1, "rest_after": 3, "cap": 4, "min_rest": 3},
        {"stage": "SF",    "round": 1, "match_count": 2,  "days": 1, "rest_after": 3, "cap": 2, "min_rest": 3},
        {"stage": "TP",    "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
        {"stage": "F",     "round": 1, "match_count": 1,  "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
    ],    # [2026-09 버그수정, 신민용 리포트: "랭킹 평가전이 8강에서 멈추고
    # 4강/결승/3·4위전이 영원히 안 생긴다"] 원인은 위 "region" 항목
    # 주석에 이미 한 번 적혀있던 바로 그 버그다 — _precreate_ko_shell/
    # stage_round_start_day가 이 표(TOURNAMENT_SCHEDULE_RULES)를 보고
    # R16~결승 전 스테이지의 빈 대진을 미리 만들어야 다음 라운드로
    # 진행되는데, power_eval은 이 표에 아예 등록이 안 돼 있었다(표에
    # 없으면 .get(tournament_type, [])이 빈 리스트를 반환 → 8강 이후
    # 스테이지 shell이 하나도 안 만들어짐). group 라운드 3개는 실제
    # shell 생성엔 안 쓰이지만(_precreate_ko_shell이 stage=="group"은
    # 항상 skip) stage_round_start_day가 QF 시작일을 "그룹 스테이지
    # 끝난 뒤"로 정확히 누적 계산하려면 필요해서 그대로 넣어둔다 —
    # 실제 조별리그 날짜(_create_power_eval_tournament의
    # POWER_EVAL_GROUP_START_DAY + rd*2, rd=0~2)와 맞춘 값.
    "power_eval": [
        {"stage": "group", "round": 1, "match_count": 8, "days": 1, "rest_after": 1, "cap": 8, "min_rest": 0},
        {"stage": "group", "round": 2, "match_count": 8, "days": 1, "rest_after": 1, "cap": 8, "min_rest": 2},
        {"stage": "group", "round": 3, "match_count": 8, "days": 1, "rest_after": 2, "cap": 8, "min_rest": 2},
        {"stage": "QF",    "round": 1, "match_count": 4, "days": 1, "rest_after": 3, "cap": 4, "min_rest": 3},
        {"stage": "SF",    "round": 1, "match_count": 2, "days": 1, "rest_after": 3, "cap": 2, "min_rest": 3},
        {"stage": "TP",    "round": 1, "match_count": 1, "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
        {"stage": "F",     "round": 1, "match_count": 1, "days": 1, "rest_after": 0, "cap": 1, "min_rest": 3},
    ],
}


def get_stage_rule(tournament_type, stage, round_number=1):
    """해당 stage/round의 규칙 dict를 반환. 없으면 None(예: 32개국 체제엔
    round32 자체가 없어 자동으로 None → 생성기가 스킵)."""
    for r in TOURNAMENT_SCHEDULE_RULES.get(tournament_type, []):
        if r["stage"] == stage and r["round"] == round_number:
            return r
    return None


def stage_round_start_day(tournament_type, stage, round_number, tournament_start_day, skip=None):
    """tournament_start_day부터 시작해 이전 stage/round들의 days+rest_after를
    누적해서 해당 stage/round가 시작하는 절대 day를 계산한다.

    skip: [2026-08 신설, 신민용 리포트: "3팀 조만 있는 대회는 원래 1주면
    끝나는데 다음 스테이지가 5팀 조 대회 기준으로 밀려서 2주 넘게 공백이
    생긴다"] 지역컵은 조 인원(3~5명)에 따라 실제 필요한 라운드 수가
    3~5로 대회마다 다른데, 이 표(TOURNAMENT_SCHEDULE_RULES)는 모든
    "region" 대회가 공유한다. skip에 (stage, round) 튜플 집합을 넘기면
    그 항목들은 누적 계산에서 통째로 건너뛴다 — 이 대회가 실제로 안
    쓰는 라운드(예: 3팀 조뿐인 대회의 group round 4/5)만큼 다음
    스테이지 시작일이 그만큼 앞당겨진다. None(기본값)이면 기존과
    100% 동일하게 동작(월드컵/대륙컵 호출부는 안 건드림)."""
    day = tournament_start_day
    for r in TOURNAMENT_SCHEDULE_RULES.get(tournament_type, []):
        if r["stage"] == stage and r["round"] == round_number:
            return day
        if skip and (r["stage"], r["round"]) in skip:
            continue
        day += r["days"] + r.get("rest_after", 0)
    raise ValueError(f"unknown stage/round: {tournament_type}/{stage}/{round_number}")


def assign_match_days(start_day, match_count, daily_capacity):
    """start_day부터 match_count개 경기를 daily_capacity개씩 채워가며 날짜를
    배정한다. 라운드 전체에 day 하나만 주는 게 아니라 실제로 여러 날에
    나눠 떨어지게 하는 게 핵심(그래야 day가 week 재탕이 안 됨).
    반환: 매치 순서대로 대응하는 day 리스트."""
    if not daily_capacity or daily_capacity <= 0:
        daily_capacity = max(match_count, 1)
    return [start_day + (i // daily_capacity) for i in range(match_count)]


# [2026-07 재설계] 예선을 연말 오프시즌이 아니라 중간 휴식기(비시즌,
# WINTER_OFFER_START~END_DAY) 안에서 진행한다. 조별리그 6라운드 + PO
# 1라운드 = 7라운드를 4일 간격으로 압축하면 정확히 4주(28일) 휴식기
# 안에 들어맞는다(설계 검증 완료: 190,194,198,202,206,210,214일).
#
# [capacity 방식 폐기] 예전엔 같은 week에 여러 조 경기가 몰리는 걸
# INTL_QUAL_DAILY_CAPACITY로 나눠 배정했는데, 다시 보니 불필요한
# 복잡도였다 — 같은 라운드 안의 다른 조 경기는 서로 다른 나라(팀)라서
# 같은 날짜에 겹쳐도 실제 시뮬레이션 충돌이 아니다(실제 FIFA 예선도
# 여러 조가 같은 매치데이에 동시 진행됨). 그래서 라운드 하나 = day
# 하나로 단순화한다.
INTL_QUAL_START_DAY      = WINTER_OFFER_START_DAY   # 190 (중간 휴식기 첫날)
INTL_QUAL_ROUND_GAP_DAYS = 4                          # 라운드 간 간격
INTL_QUAL_WEEK           = day_to_week(WINTER_OFFER_START_DAY)  # 트리거용 주차(28)



# 훈련 설정
# gain_min/max: 일반훈련(중/저/집중)은 소프트캡과 함께 점진 성장하도록 하향.
#               고강도(exceed_limit=True)는 max~talent_cap 돌파용이라 강하게 유지.
# [일 단위 전환 임시조치 — 2026-07] 예전엔 훈련 1회 = 1주(또는 4주) 단위였는데,
# [2026-07 재조정, 신민용 확정] 1/7로 낮췄더니 재능 상한(talent_cap)까지
# 전혀 못 미쳤다(월드클래스 등급 실측: 최고 OVR 66~80, 재능상한 95인데
# 크게 부족). 실측 시뮬레이션으로 배율을 올려가며 검증한 결과 4/7일 때
# 월드클래스가 26세 무렵 재능상한(95)에 거의 정확히 도달했다 — 4배로 상향.
_TRAIN_GAIN_SCALE = 4 / 7

TRAINING_CONFIG = {
    # [2026-08 재조정, 부상 시스템 QA-0 — 신민용+GPT 확정] 실측 결과 기존
    # 값(16/14/12/8/-20)으로는 "정상 운영"만 해도 stress가 월 1회꼴로
    # 100(하드캡→강제부상)에 도달했다(90+ 체류 13.0%, 100도달 15년간 89회).
    # 목표: "중강도 위주로 적당히 쉬며 뛰면 부담이 거의 안 쌓이고, 고강도를
    # 자주 섞어야만 서서히 위험해지는" 구조(신민용 확정 철학). 휴식(-20)은
    # 그대로 유지(100→0 회복이 5일이면 충분해서 회복 쪽은 문제가 아니었음
    # — 실측으로 확인됨) — 증가 쪽만 낮췄다. 같은 테스트 패턴으로 재실측한
    # 결과: 90+ 체류 13.0%→5.2%, 100도달 89→26회(15년) — 상대적 강도 순서
    # (고강도>강점/약점>중강도>저강도)는 그대로 유지. 자세한 실측 과정은
    # 부상시스템 설계 문서 참고. 1차 실험값 — 추가 QA로 조정 예정.
    "고강도":   {"stress":+11, "injury_chance":0.05, "gain_min":4.0 * _TRAIN_GAIN_SCALE, "gain_max":5.5 * _TRAIN_GAIN_SCALE, "exceed_limit":True},
    "강점훈련": {"stress":+9,  "injury_chance":0.00, "gain_min":3.3 * _TRAIN_GAIN_SCALE, "gain_max":4.6 * _TRAIN_GAIN_SCALE, "exceed_limit":False, "focus_mode":"strong"},
    "약점훈련": {"stress":+9,  "injury_chance":0.00, "gain_min":3.3 * _TRAIN_GAIN_SCALE, "gain_max":4.6 * _TRAIN_GAIN_SCALE, "exceed_limit":False, "focus_mode":"weak"},
    "중강도":   {"stress":+5,  "injury_chance":0.00, "gain_min":2.0 * _TRAIN_GAIN_SCALE, "gain_max":3.0 * _TRAIN_GAIN_SCALE, "exceed_limit":False},
    "저강도":   {"stress":+3,  "injury_chance":0.00, "gain_min":1.1 * _TRAIN_GAIN_SCALE, "gain_max":1.8 * _TRAIN_GAIN_SCALE, "exceed_limit":False},
    "휴식":     {"stress":-20, "injury_chance":0.00, "gain_min":-1 * _TRAIN_GAIN_SCALE,  "gain_max":-1 * _TRAIN_GAIN_SCALE,  "exceed_limit":False},
}

# [2026-08 대체됨, 신민용 확정] 절대 포인트 기반 소프트캡 — 아래
# PROGRESS_SOFTCAP_* (진행률% 기반)로 고강도/중강도/저강도/집중훈련이
# 전부 통일되면서 이 두 값을 쓰던 코드가 없어졌다. 다른 곳에서 참조 중일
# 수 있어 상수 자체는 남겨두되, 신규 훈련 감속 로직에는 쓰지 않는다.
SOFTCAP_DENOM = 40.0
SOFTCAP_FLOOR = 0.10

# 강점/약점 집중훈련: max 도달 후 talent_cap까지 한계 돌파 확률.
# 고강도(상시 돌파)와 달리 가끔만 돌파한다. 두 모드 동일 — 차이는 '타겟 스탯'뿐.
#   - 강점훈련: 한계치(_max)가 높은 스탯을 집중해서 그 한계까지 채움
#   - 약점훈련: 한계치가 낮은 스탯을 집중해서 그 한계까지 채움
# [2026-08 재조정, 신민용 확정] 0.05 → 0.10으로 상향. 아래 진행률(%) 기반
# 감속 커브가 새로 생기면서 max 도달 자체가 예전보다 오래 걸리게 됐고,
# 그만큼 도달 이후의 돌파 확률을 올려 균형을 맞췄다.
FOCUS_BREAK_PROB_STRONG = 0.10
FOCUS_BREAK_PROB_WEAK   = 0.10
FOCUS_BREAK_PROB        = 0.10

# [2026-08 신설, 신민용 확정] 훈련 gain(과 휴식 시 스탯 감소폭)을 "한계까지
# 남은 절대 포인트"가 아니라 "시작값→한계값 전체 구간 중 진행률(%)"로
# 감속한다. 스탯마다 시작~한계 폭이 달라도(예: 40→75 vs 40→95) 항상 같은
# 모양의 커브가 적용되게 하기 위함. 고강도/중강도/저강도/강점훈련/
# 약점훈련의 gain과, 휴식의 감소폭까지 전부 이 커브 하나로 통일한다
# (game_engine._progress_soft 참고).
#   - PROGRESS_SOFTCAP_BREAK_PCT: 이 진행률(%)에 도달하기 전까진 감속 없이
#     풀스피드(배율 1.0). 0.5면 "절반 왔을 때부터 느려지기 시작".
#   - PROGRESS_SOFTCAP_FLOOR: 진행률이 100%에 가까워져도 배율이 이 값
#     밑으로는 절대 안 내려가는 바닥. 0으로 두면 이론상 한계에 영원히
#     못 닿을 수 있어(무한 점근), 진행 자체가 죽지 않도록 바닥을 깔아둔다.
#     (참고: 고강도/집중훈련의 실제 '한계 돌파'는 이 배율과 무관하게
#     HIGH_BREAK_PROB/FOCUS_BREAK_PROB가 별도로 담당 — cur이 실제로 mx에
#     닿은 '다음'부터는 이 커브가 아니라 그쪽 확률이 성장을 이어받는다.)
#   - PROGRESS_SOFTCAP_POWER: BREAK_PCT~100% 구간 안에서 커브가 얼마나
#     급하게 꺾이는지. 클수록 후반부(한계 바로 앞)에서 급격히 느려지는
#     "막판 정체감"이 강해진다.
# [실측 캘리브레이션] 신민용이 예시로 든 "60/80→×1.0, 70/80→×0.9,
# 75/80→×0.7, 78/80→×0.4, 79/80→×0.15"는 시작값을 기본 스탯 시작 범위인
# 40으로 가정하면 진행률 50%/75%/87.5%/95%/97.5%에 해당한다. 아래
# 파라미터(BREAK=0.5, FLOOR=0.15, POWER=3.0)로 계산하면 각각 1.0/0.89/
# 0.64/0.38/0.27로, 막판 1점(79/80)만 약간 완만하고 나머지는 근접 일치.
PROGRESS_SOFTCAP_BREAK_PCT = 0.5
PROGRESS_SOFTCAP_FLOOR     = 0.15
PROGRESS_SOFTCAP_POWER     = 3.0

# [2026-08 신설, 신민용 확정] 오퍼/입단 협상의 "기간 협상" — 연봉 협상과
# 완전히 독립된 별도 트랙(독립 시도 횟수, 독립 성공/실패). 최종 계약은
# 연봉·기간 둘 다 "결렬 없이" 끝나야 성사되고, 둘 중 하나라도 마지막
# 시도에서 실패하면 계약 전체가 결렬된다(ui/offer_window.py 참고).
# [2026-08 재설계 v2, 신민용 확정: "직접 지원 화면에 뜨는 성공 가능성
# (유력/가능성있음/쉽지않음/거의불가능)과 협상 결렬 위험이 같은 기준을
# 써야 한다 — 단순 OVR 격차가 아니라 실제 입단 성공확률(등급 게이트·
# 점프 페널티까지 반영된 값)을 봐야 한다"] 연봉/기간 협상 공통으로,
# 오퍼의 실제 입단 성공확률(join_prob, calc_apply_success_prob과 동일
# 공식)에 따라 시도 횟수(1~5회) 분포와 회당 성공확률이 둘 다 달라진다 —
# game_engine.roll_negotiation_attempts()/negotiation_success_prob() 참고.
NEG_ATTEMPTS_MIN = 1
NEG_ATTEMPTS_MAX = 5
NEG_SUCCESS_PROB_MIN = 0.08
NEG_SUCCESS_PROB_MAX = 0.85
# calc_apply_prob_with_context()가 실제로 반환하는 확률 범위(0.03~0.95) —
# join_prob을 회당 협상 성공확률로 선형 매핑할 때 양끝 기준으로 쓴다.
APPLY_PROB_FLOOR = 0.03
APPLY_PROB_CEIL  = 0.95

# [2026-08 v3.2 파워랭킹 재설계] B(AchievementRating)는 "누적 업적 자산"이고
# A(MatchRating)는 "현재 실력"이라 서로 다른 속도로 옅어져야 한다는
# 신민용+GPT 합의 결론 — A는 그 팀 스쿼드 수준(시드값) 쪽으로 천천히
# 회귀시키고(REGRESSION_BASE 그대로), B는 그냥 매년 이 비율만큼 곱해서
# 빠르게 감쇠시킨다(0 미만으로는 안 내려감). 처음부터 여러 값을 한 번에
# 바꾸지 않기 위해 클럽/국가 둘 다 0.30 하나로 시작 — 다년 시뮬레이션
# 결과를 보고 필요할 때만(반짝 우승 효과가 안 사라지는 것으로 확인될
# 때만) 0.35~0.45 쪽으로 개별 조정한다. 이름을 처음부터 분리해두는 건
# 클럽과 국가가 시즌 주기·대회 비중이 달라 나중에 서로 다른 값이 필요할
# 가능성이 높기 때문(신민용 확정).
# [2026-08 재조정, 신민용 확정: "국가 파워랭킹이 대회 한 번으로 너무 쉽게
# 뒤집힌다"] 0.30이면 1년 뒤에도 70%, 2년 뒤 49%가 남아 대회 성적 하나가
# 사실상 장기 명성 점수처럼 남는다는 실측 지적(프랑스가 통산 우승 0회에도
# 월드컵 준우승 1회만으로 즉시 세계 4위권까지 튀어오름) — 국가만 0.50으로
# 별도 상향해 1년 뒤 50%/2년 뒤 25%/3년 뒤 12.5%로 훨씬 빠르게 옅어지게
# 한다. 클럽은 이번엔 건드리지 않음(이미 여러 세션에 걸쳐 이 값 기준으로
# 명문팀 우승비율 등이 튜닝돼 있어 별개 사안으로 분리).
CLUB_B_DECAY_RATE = 0.30
COUNTRY_B_DECAY_RATE = 0.50
# [2026-08 신설, 신민용 확정] 국가 레이어B(대회 업적)가 무한히 누적되는 것
# 방지 — 월드컵+대륙컵을 연달아 우승해도 B 자체가 이 값을 넘지 못하게
# 캡을 건다. "B는 A(경기 기반 Elo)를 살짝 보정하는 역할일 뿐, 국가의
# 실제 실력을 뒤집으면 안 된다"는 설계 원칙(신민용 확정)에 따른 안전장치.
COUNTRY_B_MAX = 30.0

# [구버전, 하위호환 폴백용] 팀 평균 OVR을 못 구하는 예외적인 경우에만 쓰는
# 값 — 정상 흐름은 위 NEG_* 값 기반 함수를 쓴다.
CONTRACT_YEARS_NEG_MAX_ATTEMPTS = 2     # 기간 협상 시도 횟수(연봉과 별개) [폴백]
CONTRACT_YEARS_NEG_SUCCESS_PROB = 0.50  # 시도 1회당 성공 확률 [폴백]
# 시도 성공 시 한 번에 옮길 수 있는 연수 폭 — 구단 티어가 낮을수록(하위
# 리그·소규모 구단) 선수를 잡으려 유연하게 맞춰주고, 1부 등 상위 티어는
# 계약 정책이 완고해 폭이 좁다.
CONTRACT_YEARS_NEG_DELTA_BY_TIER = {1: 1, 2: 1, 3: 2}
CONTRACT_YEARS_NEG_DELTA_DEFAULT = 2
CONTRACT_YEARS_MIN = 1
CONTRACT_YEARS_MAX = 6

# 고강도 훈련: _max 도달 후 한 번 훈련 시 _max를 +1 끌어올릴 확률.
#   집중훈련(5%)보다 높게 둬서 고강도가 한계 돌파의 주력 트랙임을 분명히 한다.
HIGH_BREAK_PROB = 0.40

# 재능 등급별 고강도 돌파 상한 (talent_cap). 일반훈련 max와는 별개의 천장.
# 부상 없이 고강도를 꾸준히 하면 이 값까지 개별 스탯을 올릴 수 있음.
#   이 cap은 '개별 스탯이 고강도 돌파로 도달 가능한 평균적 천장'이자
#   전성기 OVR 의 목표 범위이기도 하다 (강점은 cap+α로 100 초과 가능,
#   약점은 cap 아래라 평균은 cap 부근에서 균형).
#
# [2026-08 재설계, 신민용 확정] 5단계 → 9단계로 확장. 리그 등급(SS~F,
# COUNTRY_LEAGUE_GRADE/OVR_RANGES와 동일 체계)에 그대로 대응시켜서
# "이 재능이면 대충 어느 리그에서 뛰는 수준인지" 감이 바로 오게 했다:
#   - 신(god):          OVR 100~105 — GOAT급, 신 등급만 100을 넘길 수 있다.
#                       (실제 상한은 100~105 사이 숨겨진 개별값 — 신이라고
#                       다 105는 아니고, 그 안에서도 "신들린 개체차"가 있다.)
#   - 월드클래스:        SS~S급 리그 핵심 선수 수준 (OVR_RANGES SS/S tier1 상단)
#   - 슈퍼스타:          S급 리그 주전 + A급 상위권 핵심 (S/A tier1 중상단)
#   - 엘리트:            A급 리그에서 뛰는 선수 (A tier1 일반 수준)
#   - 프로:              B급 리그 수준
#   - 세미프로:          C급 리그 수준
#   - 아마추어:          D급 리그 수준
#   - 평범:              E급 리그 수준
#   - 재능없음(untalented): F급 리그 수준 — 선수는 됐지만 재능은 거의 없음
#
# [100 하드 가드] "신이 아니면 절대 100을 못 찍는다"는 요구사항 — 개별
# 스탯은 강점 브레이크(talent_cap+12)로 cap을 넘어설 수 있어서, OVR(가중
# 평균)이 통계적으로는 100 근처까지 갈 수도 있었다(과거엔 안전장치 없이
# "약점이 낮아서 평균은 대충 유지된다"는 기대에만 의존). 이제 신 외
# 모든 등급은 cap_max를 99 이하로 두고, OVR 계산부(_apply_training_result
# 등 talent_tier를 아는 지점)에서 "신이 아니면 min(ovr, 99)로 강제
# 클램프"하는 안전장치를 추가로 건다(아래 _clamp_ovr_by_talent 참고) —
# 가중평균이 어쩌다 100이 나와도 최종 표시값은 절대 100이 될 수 없다.
#
# [확률] 예전엔 상위 등급일수록 새 게임 랜덤 확률이 높았다(월드클래스
# 15%가 최다) — 신민용 확정으로 9등급 전부 동일 확률(1/9)로 바꿨다.
TALENT_TIERS = {
    # [2026-08 확장, 신민용 지시] 신 등급도 talent_cap을 100 고정이 아니라
    # 100~105 사이에서 랜덤으로 뽑는다 — "신 등급이라고 다 100에서 끝나는
    # 게 아니라, 그중에서도 진짜 초월적인 개체는 105까지 간다"는 의도.
    # cap_min은 여전히 100이라 "신은 최소 100은 보장"은 그대로 유지된다.
    "god":         {"prob": 1/9, "cap_min": 100, "cap_max": 105},
    "worldclass":  {"prob": 1/9, "cap_min": 92,  "cap_max": 99},
    "superstar":   {"prob": 1/9, "cap_min": 84,  "cap_max": 91},
    "elite":       {"prob": 1/9, "cap_min": 74,  "cap_max": 83},
    "pro":         {"prob": 1/9, "cap_min": 64,  "cap_max": 73},
    "semipro":     {"prob": 1/9, "cap_min": 55,  "cap_max": 63},
    "amateur":     {"prob": 1/9, "cap_min": 46,  "cap_max": 54},
    "ordinary":    {"prob": 1/9, "cap_min": 37,  "cap_max": 45},
    "untalented":  {"prob": 1/9, "cap_min": 28,  "cap_max": 36},
}

# [신규] 재능 등급 한글/영문 표시명 — 새 게임 화면의 등급 선택 콤보박스와
# 선수 패널의 뱃지가 이 하나의 표를 공유한다(표시 문구가 여러 곳에서
# 따로 하드코딩되어 서로 어긋나는 걸 방지).
TALENT_TIER_KO = {
    "god": "신", "worldclass": "월드클래스", "superstar": "슈퍼스타",
    "elite": "엘리트", "pro": "프로", "semipro": "세미프로",
    "amateur": "아마추어", "ordinary": "평범", "untalented": "재능없음",
}
TALENT_TIER_EN = {
    "god": "God", "worldclass": "World Class", "superstar": "Superstar",
    "elite": "Elite", "pro": "Professional", "semipro": "Semi-Pro",
    "amateur": "Amateur", "ordinary": "Ordinary", "untalented": "Untalented",
}
# 새 게임 화면 콤보박스에 보여줄 순서(강한 순).
# [2026-08 신설, 골 시상 시스템] "OO 올해의 골"(리그마다 이름이 다름, 수백 종류)
# / "OO 최고의 골"(대회마다 이름이 다름) / "FIFA 푸스카스상"은 award_type 자체가
# award_type=display_name이라(기존 발롱도르/MVP 등과 같은 저장 방식 유지 —
# 새 컬럼 없이 그대로 호환), 정확한 문자열 목록으로 하드코딩된 기존 요약/집계
# 로직(story_generator.py, ui/career_window.py, ui/retire_window.py 3곳 전부)이
# 이 상들을 인식 못 하는 문제가 있었다. award_type을 "리그 올해의 골"/
# "대회 최고의 골"/"FIFA 푸스카스상" 3개 버킷으로 정규화하는 공용 헬퍼로 통일한다
# (한 군데만 고치면 3곳 다 정확해짐 — 리그/대회 이름이 몇 개든 상관없음).
def normalize_award_bucket(award_type: str) -> str:
    """개인상 요약 집계(Counter)용 — 리그/대회별로 이름이 다른 골 관련 상을
    공통 버킷 이름으로 묶는다. 그 외 상(발롱도르/MVP 등)은 원래 이름 그대로."""
    if award_type == "FIFA 푸스카스상":
        return award_type
    if award_type.endswith("올해의 골"):
        return "리그 올해의 골"
    if award_type.endswith("최고의 골"):
        return "대회 최고의 골"
    return award_type


_GOAL_AWARD_ICON = {"FIFA 푸스카스상": "💥", "리그 올해의 골": "⚽", "대회 최고의 골": "🌍"}


def award_icon(award_type: str, fallback_map: dict = None) -> str:
    """개인상 아이콘 조회 — 골 관련 상은 버킷 아이콘, 그 외는 fallback_map(호출부
    기존 icon 딕셔너리) 우선, 없으면 기본 🏅."""
    bucket = normalize_award_bucket(award_type)
    if bucket in _GOAL_AWARD_ICON:
        return _GOAL_AWARD_ICON[bucket]
    if fallback_map:
        return fallback_map.get(award_type, "🏅")
    return "🏅"


TALENT_TIER_ORDER = ["god", "worldclass", "superstar", "elite", "pro",
                     "semipro", "amateur", "ordinary", "untalented"]

# [2026-08 신설, 난이도 시스템 13번] 어려움 난이도에서는 새 선수 생성 시 재능
# 등급을 "신"(god)이 절대 뽑히지 않게 한다(신민용 확정) — god을 제외한 8개
# 등급으로 재배분한 확률표를 별도로 둔다. 합계 100%, 중간 등급(프로)이 가장
# 흔하고 양 끝(신급 바로 아래인 월드클래스, 최하단인 재능없음)이 가장 희귀한
# 형태: 월드클래스5%/슈퍼스타12%/엘리트18%/프로20%/세미프로18%/아마추어12%/
# 평범10%/재능없음5%.
TALENT_TIER_ORDER_HARD = [t for t in TALENT_TIER_ORDER if t != "god"]
TALENT_TIER_HARD_PROB = {
    "worldclass": 0.05, "superstar": 0.12, "elite": 0.18, "pro": 0.20,
    "semipro": 0.18, "amateur": 0.12, "ordinary": 0.10, "untalented": 0.05,
}

# (구버전 호환) 예전 티어명을 새 티어로 매핑 — worldclass/elite/pro/semipro/
# ordinary는 이름이 그대로 남아있어(캡 수치만 재조정) 기존 세이브의
# talent_tier 값이 별도 변환 없이도 새 TALENT_TIERS에서 바로 유효하다.
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

# ════════════════════════════════════════════════════════════════
# [2026-08 신설, 부상 시스템 확장 1차] 신민용 제공 부상 데이터 풀
# (실제 스포츠의학 자료 기반 100여 개 항목)을 그대로 데이터화한다.
#
# 위 INJURY_TYPES/INJURY_DETAILS/INJURY_TIER_CHANCE(구버전)는 등급을 먼저
# 뽑고 그 등급 안에서 구체 부상을 고르는 2단계 방식이었다 — 이제는 부상
# 하나하나가 자기 발생가중치(weight)를 직접 들고 있어서 "회복기간이 긴
# 부상일수록 덜 뽑히게" 훨씬 세밀하게 조절할 수 있다. 구버전 dict들은
# 다른 곳에서 참조하는 코드가 없어 그대로 남겨두되(하위호환/비교용),
# _apply_injury()는 이제 아래 INJURY_POOL만 사용한다.
#
# 필드:
#   id           고유 식별자(영문, snake_case)
#   body_part    신체 실루엣 zone 키(ui/player_panel.py의 BodySilhouette와 동일
#                네이밍) — sided=True면 런타임에 'l_'/'r_' 접두사가 랜덤으로 붙는다.
#   sided        좌/우 구분이 있는 부위인지(팔다리 등) — 없으면(머리/목/척추/
#                골반/가슴 등 중앙 부위) 접두사 없이 그대로 사용.
#   name         화면에 표시될 한글 부상명
#   tier         경미/중간/심각/매우 심각 — 기존 등급 체계와 동일 문자열
#                (happy_penalty 등 등급 기반 로직을 그대로 재사용하기 위함)
#   recovery_days 실제 발생 시 회복기간(일)을 이 (최소,최대) 범위에서
#                random.randint로 뽑는다. [2026-08] 신민용 자료의 '경기 복귀
#                기준' 값을 그대로 사용(의학적 완치가 아니라 게임용 기준).
#   pos_cat      포지션별 발생가중치 보정 카테고리(INJURY_POSITION_MULT 키).
#                None이면 포지션 보정 없음(대부분의 부상은 포지션 편차가
#                크지 않다는 게 신민용 방향 — 명시적으로 지정된 것만 보정).
# ════════════════════════════════════════════════════════════════

# 등급별 기본 발생가중치. 숫자가 클수록 잘 뽑힘 — 회복기간이 짧을수록
# (=등급이 낮을수록) 압도적으로 자주 나오게 설계(신민용 확정치 그대로).
INJURY_TIER_BASE_WEIGHT = {
    "경미": 100, "중간": 45, "심각": 12, "매우 심각": 2,
}

# 같은 '매우 심각' 등급이어도 특별히 더 희귀해야 하는 항목(신민용 확정:
# "180~365일짜리 부상은 처음부터 극희귀로 잡아두는 게 좋음") — 등급
# 기본 가중치(2) 대신 이 값을 쓴다. 값은 예시로 준 "ACL 완전파열=1,
# 아킬레스건 완전파열=0.5"를 기준점 삼아 비슷한 수준의 초중증 부상들에
# 상대적으로 배분했다 — 1차 구현 단계 잠정치, 장기 시뮬레이션(100~200년)
# 실측 후 위아래로 조정 예정(신민용 방향과 동일).
INJURY_WEIGHT_OVERRIDE = {
    "acl_complete":            1.0,
    "achilles_complete":       0.5,
    "femur_fracture":          0.3,
    "pelvis_fracture":         0.5,
    "cervical_fracture":       0.3,
    "femoral_neck_fracture":   0.4,
    "multi_ligament_injury":   0.4,
    "patellar_tendon_rupture": 0.8,
    "pcl_injury":              0.8,
    "hip_labrum":              0.8,
    "tibia_fibula_fracture":   0.6,
    "hamstring_complete":      0.8,
    "quad_complete_tear":      0.8,
}

# (id, body_part, sided, name, tier, (최소일,최대일), pos_cat)
_INJURY_POOL_RAW = [
    # ── 1. 머리/얼굴 (unsided) ──
    ("head_contusion",        "head", False, "두부 타박상",       "경미", (2, 7),    "face"),
    ("face_contusion",        "head", False, "안면 타박상",       "경미", (2, 7),    "face"),
    ("nose_contusion",        "head", False, "코 타박상",         "경미", (2, 7),    "face"),
    ("face_laceration",       "head", False, "안면 열상",         "경미", (3, 14),   "face"),
    ("concussion",            "head", False, "뇌진탕",            "중간", (7, 21),   "face"),
    ("concussion_severe",     "head", False, "심한 뇌진탕",       "심각", (21, 42),  "face"),
    ("nasal_fracture",        "head", False, "코뼈 골절",         "중간", (14, 35),  "face"),
    ("zygomatic_fracture",    "head", False, "광대뼈 골절",       "심각", (28, 56),  "face"),
    ("orbital_fracture",      "head", False, "안와 골절",         "심각", (42, 84),  "face"),
    ("mandible_fracture",     "head", False, "하악골 골절",       "심각", (42, 90),  "face"),

    # ── 2. 목 (unsided) ──
    ("neck_strain",           "neck", False, "목 근육 염좌",      "경미", (3, 14),   None),
    ("neck_tension",          "neck", False, "목 근육 긴장",      "경미", (3, 14),   None),
    ("cervical_contusion",    "neck", False, "경추 타박상",       "경미", (7, 21),   None),
    ("cervical_sprain",       "neck", False, "경추 염좌",         "중간", (14, 42),  None),
    ("cervical_disc",         "neck", False, "경추 디스크 손상",  "심각", (28, 90),  None),
    ("cervical_fracture",     "neck", False, "경추 골절",         "매우 심각", (90, 180), None),

    # ── 3. 어깨 (sided) ──
    ("shoulder_contusion",     "shoulder", True, "어깨 타박상",         "경미", (3, 10),   "shoulder"),
    ("shoulder_muscle_sprain", "shoulder", True, "어깨 근육 염좌",      "경미", (7, 21),   "shoulder"),
    ("shoulder_joint_sprain",  "shoulder", True, "어깨 관절 염좌",      "중간", (7, 28),   "shoulder"),
    ("shoulder_dislocation",   "shoulder", True, "어깨 탈구",           "심각", (28, 84),  "shoulder"),
    ("shoulder_labrum",        "shoulder", True, "어깨 관절순 손상",    "매우 심각", (42, 120), "shoulder"),
    ("rotator_cuff",           "shoulder", True, "회전근개 손상",       "심각", (21, 90),  "shoulder"),
    ("clavicle_fracture",      "shoulder", True, "쇄골 골절",           "심각", (42, 90),  "shoulder"),

    # ── 4. 팔/팔꿈치 (sided) ──
    ("arm_contusion",       "upper_arm", True, "팔 타박상",       "경미", (2, 7),   None),
    ("arm_muscle_sprain",   "upper_arm", True, "팔 근육 염좌",    "경미", (5, 21),  None),
    ("elbow_contusion",     "elbow",     True, "팔꿈치 타박상",   "경미", (3, 10),  None),
    ("elbow_sprain",        "elbow",     True, "팔꿈치 염좌",     "중간", (7, 28),  None),
    ("elbow_dislocation",   "elbow",     True, "팔꿈치 탈구",     "심각", (28, 70), None),
    ("humerus_fracture",    "upper_arm", True, "상완골 골절",     "매우 심각", (60, 120), None),
    ("olecranon_fracture",  "elbow",     True, "주두 골절",       "매우 심각", (60, 120), None),

    # ── 5. 손/손목 (sided, 손가락·손목은 hand zone으로 통합) ──
    ("hand_contusion",              "hand",    True, "손 타박상",           "경미", (2, 7),   "wrist"),
    ("finger_contusion",            "hand",    True, "손가락 타박상",       "경미", (2, 7),   "finger"),
    ("finger_sprain",               "hand",    True, "손가락 염좌",         "경미", (3, 14),  "finger"),
    ("finger_dislocation",          "hand",    True, "손가락 탈구",         "중간", (14, 42), "finger"),
    ("wrist_sprain",                "hand",    True, "손목 염좌",           "중간", (7, 28),  "wrist"),
    ("wrist_ligament",              "hand",    True, "손목 인대 손상",      "심각", (21, 70), "wrist"),
    ("radius_forearm_fracture",     "forearm", True, "요골 골절",           "매우 심각", (45, 90), "wrist"),
    ("ulna_forearm_fracture",       "forearm", True, "척골 골절",           "매우 심각", (45, 90), "wrist"),
    ("wrist_distal_radius_fracture","hand",    True, "손목 골절",           "매우 심각", (42, 90), "wrist"),
    ("metacarpal_fracture",         "hand",    True, "중수골 골절",         "심각", (28, 70), "wrist"),
    ("phalanx_fracture",            "hand",    True, "손가락 골절",         "중간", (21, 56), "finger"),

    # ── 6. 가슴/갈비뼈 (unsided) ──
    ("chest_contusion",     "chest", False, "가슴 타박상",       "경미", (3, 10),  None),
    ("rib_contusion",       "chest", False, "갈비뼈 타박상",     "경미", (7, 21),  None),
    ("intercostal_muscle",  "chest", False, "늑간근 손상",       "중간", (14, 42), None),
    ("thoracic_sprain",     "chest", False, "흉곽 염좌",         "중간", (7, 28),  None),
    ("single_rib_fracture", "chest", False, "단일 갈비뼈 골절",  "중간", (21, 42), None),
    ("multi_rib_fracture",  "chest", False, "다발성 갈비뼈 골절", "심각", (42, 84), None),

    # ── 7. 허리/척추 (unsided, 발생 확률 낮게 — 가중치로 처리) ──
    ("low_back_strain",        "back", False, "허리 근육 긴장",     "경미", (3, 14),  None),
    ("lumbar_sprain",          "back", False, "요추 염좌",          "중간", (7, 28),  None),
    ("low_back_muscle_injury", "back", False, "허리 근육 손상",     "중간", (14, 42), None),
    ("spinal_contusion",       "back", False, "척추 타박상",        "중간", (14, 42), None),
    ("lumbar_disc",            "back", False, "요추 디스크 손상",   "매우 심각", (28, 90), None),

    # ── 8. 골반/엉덩이 (unsided) ──
    ("pelvis_contusion",     "pelvis", False, "골반 타박상",         "경미", (3, 10),   None),
    ("glute_contusion",      "pelvis", False, "둔근 타박상",         "경미", (3, 10),   None),
    ("glute_strain",         "pelvis", False, "둔근 근육 긴장",      "경미", (7, 21),   None),
    ("glute_tear",           "pelvis", False, "둔근 근육 파열",      "심각", (21, 56),  None),
    ("hip_sprain",           "pelvis", False, "고관절 염좌",         "중간", (14, 42),  None),
    ("hip_impingement",      "pelvis", False, "고관절 충돌 증후군",  "중간", (14, 60),  None),
    ("hip_labrum",           "pelvis", False, "고관절 관절순 손상",  "매우 심각", (42, 120), None),
    ("pelvis_fracture",      "pelvis", False, "골반 골절",           "매우 심각", (90, 180), None),
    ("femoral_neck_fracture","pelvis", False, "대퇴골 경부 골절",    "매우 심각", (120, 240), None),

    # ── 9. 사타구니/내전근 (thigh zone, sided) ──
    ("groin_pain",             "thigh", True, "사타구니 통증",       "경미", (3, 14),  "groin"),
    ("adductor_sprain",        "thigh", True, "내전근 염좌",         "경미", (7, 21),  "groin"),
    ("adductor_muscle_injury", "thigh", True, "내전근 근육 손상",    "중간", (14, 42), "groin"),
    ("adductor_partial_tear",  "thigh", True, "내전근 부분 파열",    "심각", (21, 56), "groin"),
    ("adductor_complete_tear", "thigh", True, "내전근 완전 파열",    "매우 심각", (42, 90), "groin"),
    ("sports_hernia",          "thigh", True, "스포츠 탈장",         "심각", (28, 84), "groin"),

    # ── 10. 허벅지 - 햄스트링 (sided) ──
    ("hamstring_tension",  "thigh", True, "햄스트링 근육 긴장",     "경미", (5, 14),   "hamstring"),
    ("hamstring_minor",    "thigh", True, "햄스트링 경미한 손상",   "경미", (7, 21),   "hamstring"),
    ("hamstring_partial",  "thigh", True, "햄스트링 부분 파열",     "중간", (21, 42),  "hamstring"),
    ("hamstring_moderate", "thigh", True, "햄스트링 중등도 파열",   "심각", (28, 56),  "hamstring"),
    ("hamstring_severe",   "thigh", True, "햄스트링 중증 파열",     "매우 심각", (42, 90),  "hamstring"),
    ("hamstring_complete", "thigh", True, "햄스트링 완전 파열",     "매우 심각", (90, 180), "hamstring"),

    # ── 11. 허벅지 - 대퇴사두근 (sided) ──
    ("quad_contusion",     "thigh", True, "대퇴사두근 타박상",     "경미", (3, 14),   None),
    ("quad_tension",       "thigh", True, "대퇴사두근 근육 긴장",  "경미", (5, 14),   None),
    ("quad_injury",        "thigh", True, "대퇴사두근 손상",       "중간", (7, 28),   None),
    ("quad_partial_tear",  "thigh", True, "대퇴사두근 부분 파열",  "심각", (21, 56),  None),
    ("quad_severe_tear",   "thigh", True, "대퇴사두근 중증 파열",  "매우 심각", (42, 90),  None),
    ("quad_complete_tear", "thigh", True, "대퇴사두근 완전 파열",  "매우 심각", (90, 180), None),

    # ── 12. 허벅지 - 기타 (sided) ──
    ("thigh_contusion",             "thigh", True, "대퇴근 타박상",        "경미", (3, 14),  None),
    ("rectus_femoris_injury",       "thigh", True, "대퇴직근 손상",        "중간", (7, 42),  None),
    ("tensor_fasciae_latae_injury", "thigh", True, "대퇴근막장근 손상",    "경미", (7, 28),  None),
    ("thigh_cramp",                 "thigh", True, "허벅지 근육 경련",     "경미", (1, 3),   None),
    ("femur_fracture",              "thigh", True, "대퇴골 골절",          "매우 심각", (180, 365), None),

    # ── 13. 무릎 - 인대 (sided) ──
    ("knee_sprain",             "knee", True, "무릎 염좌",                    "경미", (7, 21),   "knee_ligament"),
    ("mcl_minor",                "knee", True, "내측측부인대(MCL) 경미 손상", "중간", (14, 28),  "knee_ligament"),
    ("mcl_partial",              "knee", True, "MCL 부분 파열",               "중간", (28, 56),  "knee_ligament"),
    ("mcl_complete",             "knee", True, "MCL 완전 파열",               "심각", (56, 90),  "knee_ligament"),
    ("lcl_injury",               "knee", True, "외측측부인대(LCL) 손상",      "심각", (21, 60),  "knee_ligament"),
    ("acl_partial",              "knee", True, "전방십자인대(ACL) 부분 손상", "매우 심각", (60, 150), "knee_ligament"),
    ("acl_complete",             "knee", True, "전방십자인대(ACL) 완전 파열", "매우 심각", (180, 300), "knee_ligament"),
    ("pcl_injury",               "knee", True, "후방십자인대(PCL) 손상",      "매우 심각", (60, 180), "knee_ligament"),
    ("multi_ligament_injury",    "knee", True, "다중 인대 손상",              "매우 심각", (180, 365), "knee_ligament"),

    # ── 14. 무릎 - 반월상연골 (sided) ──
    ("meniscus_minor",     "knee", True, "반월상연골 타박/경미 손상", "중간", (7, 21),   "knee_ligament"),
    ("meniscus_tear",      "knee", True, "반월상연골 파열",           "심각", (28, 70),  "knee_ligament"),
    ("meniscus_repair",    "knee", True, "반월상연골 봉합술",         "매우 심각", (60, 120), "knee_ligament"),
    ("meniscus_resection", "knee", True, "반월상연골 절제술",         "심각", (30, 60),  "knee_ligament"),

    # ── 15. 무릎 - 힘줄/관절 (sided) ──
    ("patellar_tendinitis",     "knee", True, "슬개건염",              "중간", (14, 60),  None),
    ("patellar_tendon_partial", "knee", True, "슬개건 부분 손상",      "심각", (28, 90),  None),
    ("patellar_tendon_rupture", "knee", True, "슬개건 파열",           "매우 심각", (120, 240), None),
    ("patella_contusion",       "knee", True, "슬개골 타박상",         "경미", (7, 21),   None),
    ("patellofemoral_pain",     "knee", True, "슬개대퇴 통증 증후군",  "중간", (14, 60),  None),
    ("knee_synovitis",          "knee", True, "무릎 관절염/활액막염",  "경미", (7, 42),   None),
    ("patella_fracture",        "knee", True, "슬개골 골절",           "매우 심각", (60, 120), None),

    # ── 16. 종아리 (sided) ──
    ("calf_contusion",       "calf", True, "종아리 타박상",     "경미", (3, 10),   None),
    ("calf_tension",         "calf", True, "종아리 근육 긴장",  "경미", (5, 14),   None),
    ("calf_injury",          "calf", True, "종아리 근육 손상",  "중간", (7, 28),   None),
    ("gastrocnemius_partial","calf", True, "비복근 부분 파열",  "심각", (21, 56),  None),
    ("soleus_injury",        "calf", True, "가자미근 손상",     "중간", (14, 42),  None),
    ("calf_severe_tear",     "calf", True, "종아리 중증 파열",  "매우 심각", (42, 90),  None),
    ("tibia_fracture",       "calf", True, "경골 골절",         "매우 심각", (90, 180), None),
    ("fibula_fracture",      "calf", True, "비골 골절",         "심각", (45, 90),  None),
    ("tibia_fibula_fracture","calf", True, "경골+비골 골절",    "매우 심각", (120, 240), None),

    # ── 17. 아킬레스건 (ankle zone, sided) ──
    ("achilles_pain",        "ankle", True, "아킬레스건 통증",      "중간", (7, 42),   None),
    ("achilles_tendinitis",  "ankle", True, "아킬레스건염",         "심각", (21, 90),  None),
    ("achilles_partial",     "ankle", True, "아킬레스건 부분 손상", "매우 심각", (60, 150), None),
    ("achilles_complete",    "ankle", True, "아킬레스건 완전 파열", "매우 심각", (180, 365), None),

    # ── 18. 발목 (sided) ──
    ("ankle_contusion",           "ankle", True, "발목 타박상",              "경미", (2, 7),    "ankle_sprain"),
    ("ankle_sprain_minor",        "ankle", True, "발목 염좌 경미",           "경미", (3, 10),   "ankle_sprain"),
    ("ankle_sprain_moderate",     "ankle", True, "발목 염좌 중등도",         "중간", (14, 28),  "ankle_sprain"),
    ("ankle_sprain_severe",       "ankle", True, "발목 염좌 중증",           "심각", (28, 60),  "ankle_sprain"),
    ("lateral_ligament_injury",   "ankle", True, "외측 인대 손상",           "중간", (14, 42),  "ankle_sprain"),
    ("lateral_ligament_partial",  "ankle", True, "외측 인대 부분 파열",      "심각", (21, 56),  "ankle_sprain"),
    ("lateral_ligament_complete", "ankle", True, "외측 인대 완전 파열",      "매우 심각", (42, 90),  "ankle_sprain"),
    ("deltoid_ligament_injury",   "ankle", True, "내측 삼각인대 손상",       "매우 심각", (28, 90),  "ankle_sprain"),
    ("high_ankle_sprain",         "ankle", True, "발목 경비인대 손상(High ankle sprain)", "매우 심각", (30, 90), "ankle_sprain"),
    ("ankle_fracture",            "ankle", True, "발목 골절",                "매우 심각", (60, 150), "ankle_sprain"),

    # ── 19. 발/발가락 (foot zone, sided — 발가락은 발에 통합) ──
    ("foot_contusion",         "foot", True, "발 타박상",           "경미", (2, 7),    None),
    ("plantar_fasciitis",      "foot", True, "발바닥 근막염",       "중간", (14, 60),  None),
    ("foot_dorsum_contusion",  "foot", True, "발등 타박상",         "경미", (3, 10),   None),
    ("metatarsal_contusion",   "foot", True, "중족골 타박상",       "경미", (7, 21),   None),
    ("toe_sprain",             "foot", True, "발가락 염좌",         "경미", (3, 14),   None),
    ("metatarsal_fracture",    "foot", True, "중족골 골절",         "심각", (42, 90),  None),
    ("foot_bone_fracture",     "foot", True, "발뼈 골절",           "매우 심각", (42, 120), None),
    ("toe_fracture",           "foot", True, "발가락 골절",         "중간", (21, 56),  None),
    ("big_toe_joint_injury",   "foot", True, "엄지발가락 관절 손상","중간", (14, 42),  None),
    ("talus_fracture",         "foot", True, "거골 골절",           "매우 심각", (90, 180), None),
    ("calcaneus_fracture",     "foot", True, "종골 골절",           "매우 심각", (90, 180), None),

    # ── 21. 피부/기타 (부위는 흔한 발생 위치로 배치, sided) ──
    ("skin_abrasion",             "calf", True,  "피부 찰과상",         "경미", (1, 3),  None),
    ("skin_laceration",           "calf", True,  "피부 열상",           "경미", (2, 10), None),
    ("skin_laceration_stitches",  "calf", True,  "봉합이 필요한 열상",  "경미", (5, 21), None),
    ("toenail_injury",            "foot", True,  "발톱 손상",           "경미", (3, 21), None),
    ("hematoma",                  "thigh",True,  "혈종",                "경미", (3, 21), None),
    ("muscle_cramp",              "calf", True,  "근육 경련",           "경미", (1, 3),  None),
    ("heat_exhaustion",           "abdomen", False, "탈수/열탈진",       "경미", (1, 3),  None),
]

INJURY_POOL = [
    {
        "id": _id, "body_part": _bp, "sided": _sd, "name": _nm, "tier": _tr,
        "recovery_days": _rd, "pos_cat": _pc,
        "weight": INJURY_WEIGHT_OVERRIDE.get(_id, INJURY_TIER_BASE_WEIGHT.get(_tr, 10)),
    }
    for (_id, _bp, _sd, _nm, _tr, _rd, _pc) in _INJURY_POOL_RAW
]

# 포지션 그룹 — 기존 position_group()(GK/DEF/ATK 3그룹, CDM을 DEF로 취급)과는
# 목적이 달라 별도로 둔다. 부상 위치 보정은 실제 스포츠의학 관점(GK=손/손목/
# 어깨 특화, MF=활동량 기반 햄스트링/발목 등)이 기준이라 CDM은 MF로 분류.
INJURY_POS_GROUP = {
    "GK": "GK",
    "CB": "DF", "LB": "DF", "RB": "DF",
    "CDM": "MF", "CM": "MF", "CAM": "MF",
    "LW": "FW", "RW": "FW", "CF": "FW", "ST": "FW",
}


def get_injury_pos_group(position: str) -> str:
    """포지션 문자열 → 'GK'/'DF'/'MF'/'FW'. 못 찾으면 'MF'(가장 무난한 기본값)."""
    return INJURY_POS_GROUP.get(position, "MF")


# [2026-08 신설, 신민용 확정 — 1차 구현은 명시적으로 지정된 카테고리만
# 보정] "포지션마다 부상 확률 전체를 다르게"가 아니라 "부상 종류별
# 발생가중치를 포지션에 따라 보정"하는 구조. pos_cat이 None인 부상(대다수)은
# 포지션 무관 동일 가중치 — 실제 축구도 특정 포지션이 특정 부상만 걸리는
# 게 아니므로, 명시적 근거가 있는 카테고리에만 약하게(0.7~2.0배) 적용한다.
INJURY_POSITION_MULT = {
    "hamstring":     {"GK": 0.7, "DF": 1.0, "MF": 1.2, "FW": 1.2},
    "ankle_sprain":  {"GK": 0.9, "DF": 1.1, "MF": 1.2, "FW": 1.2},
    "knee_ligament": {"GK": 0.8, "DF": 1.1, "MF": 1.1, "FW": 1.2},
    "groin":         {"GK": 0.7, "DF": 0.9, "MF": 1.2, "FW": 1.2},
    "shoulder":      {"GK": 1.8, "DF": 1.0, "MF": 0.7, "FW": 0.6},
    "finger":        {"GK": 2.0, "DF": 0.5, "MF": 0.3, "FW": 0.3},
    "wrist":         {"GK": 1.8, "DF": 0.7, "MF": 0.4, "FW": 0.3},
    "face":          {"GK": 1.4, "DF": 1.1, "MF": 1.0, "FW": 1.0},
}


def _lerp_curve(points, x):
    """points: [(x0,y0),(x1,y1),...] x 오름차순 정렬된 구간표를 선형보간.
    x가 범위 밖이면 양 끝값으로 clamp. 확률/배율 커브 전부 이 함수 하나로
    통일해서 쓴다(위험 factor 커브, 위험 배율 커브, 심각도 배율 커브 등)."""
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return points[-1][1]


# ════════════════════════════════════════════════════════════════
# [2026-08 신설, 부상 시스템 확장 3단계 — GPT 3~4차 검토 + 신민용 확정]
# stress/injury_load → 부상 발생 확률. 하드 임계값(100=강제부상)은 "최종
# 안전장치"로 남기고, 100 미만 구간에도 완만하게 확률이 붙는 구조.
# risk_score(0~1 factor)와 injury_probability(실제 %)를 완전히 분리한다
# — 섞어서 계산하면(예: 0.002+0.45+0.55=1.002) 의미 없는 값이 나온다는
# 지적을 반영.
# ════════════════════════════════════════════════════════════════

# stress/injury_load 각각을 0~1 위험 factor로 바꾸는 구간표(선형보간).
# 둘 다 같은 형태를 쓴다 — 50 미만은 사실상 안전, 90 넘어가면 급격히 위험.
INJURY_RISK_FACTOR_CURVE = [
    (0, 0.00), (50, 0.00), (65, 0.10), (80, 0.30),
    (90, 0.60), (97, 0.85), (100, 1.00),
]

# 위 stress_factor/load_factor를 가중합쳐 combined_factor(0~1)를 만들 때의
# 가중치. injury_load 쪽을 더 크게 — "이 시스템의 존재 이유가 stress와
# 별개로 장기 혹사를 추적하는 것"이기 때문(GPT 근거).
INJURY_RISK_STRESS_WEIGHT = 0.45
INJURY_RISK_LOAD_WEIGHT   = 0.55

# combined_factor(0~1) → 기본확률에 곱할 배율.
INJURY_RISK_MULT_CURVE = [
    (0.00, 1.0), (0.25, 1.5), (0.50, 3.0),
    (0.75, 6.0), (0.90, 12.0), (0.97, 25.0), (1.00, 25.0),
]

# 세션당 기본 확률과 상한 — 둘 다 잠정치, QA 1 결과 보고 조정 예정.
# base: "부상 없는 평상시"에도 걸리는 최소 확률. max: 100 미만 구간에서
# 아무리 위험해도 이 값을 넘지 않음(100 이상은 별도로 무조건 발동).
INJURY_BASE_PROBABILITY = 0.0015   # 0.15%
INJURY_MAX_PROBABILITY  = 0.08     # 8% (GPT 권고 5~10% 구간의 중간값)

# [2026-09 신설, 신민용 리포트: "고강도 훈련도 지금은 부상 확률이 아예
# 없는 거나 마찬가지다 — 1%정도는 넣어줘"] 위 INJURY_BASE_PROBABILITY는
# stress/injury_load가 둘 다 낮은(50 미만) 평소엔 0.15%에 mult=1.0이
# 그대로 곱해져 사실상 체감이 안 됐다 — 고강도를 휴식과 번갈아 관리
# 잘하면 stress/load가 항상 50 밑에 머물러 몇 시즌을 해도 부상을 거의
# 못 본다는 뜻. "재능 프로가 고강도 풀로 해도 100을 못 찍는 것처럼,
# 고강도는 그 자체로 부상 위험이 있는 훈련이어야 한다"는 설계 의도에
# 맞춰, 고강도 세션 전용 기본확률을 훨씬 높게 잡는다 — stress/load가
# 낮은 평소에도 최소 1%는 깔리고, 여기에 기존 mult(stress/load 위험도)가
# 그대로 곱해져 더 위험할 땐 더 위험해진다. 중강도/저강도/휴식은
# 기존 INJURY_BASE_PROBABILITY 그대로 유지(요청 범위 밖).
HIGH_INTENSITY_INJURY_BASE_PROBABILITY = 0.01   # 1%


def calc_injury_probability(stress: float, injury_load: float, ttype: str = None) -> float:
    """100 미만 구간에서 이번 세션에 부상이 발생할 확률(0~1)을 계산.
    100 이상 강제발동은 호출부에서 별도로 처리(이 함수는 그 경우 안 씀).
    [2026-09 확장] ttype="고강도"면 위 HIGH_INTENSITY_INJURY_BASE_PROBABILITY
    (1%)를 기본확률로 쓴다 — 그 외(중강도/저강도/휴식/None)는 기존과
    동일하게 INJURY_BASE_PROBABILITY(0.15%)."""
    stress_factor = _lerp_curve(INJURY_RISK_FACTOR_CURVE, stress)
    load_factor   = _lerp_curve(INJURY_RISK_FACTOR_CURVE, injury_load)
    combined = (INJURY_RISK_STRESS_WEIGHT * stress_factor
                + INJURY_RISK_LOAD_WEIGHT * load_factor)
    mult = _lerp_curve(INJURY_RISK_MULT_CURVE, combined)
    base = HIGH_INTENSITY_INJURY_BASE_PROBABILITY if ttype == "고강도" else INJURY_BASE_PROBABILITY
    return min(INJURY_MAX_PROBABILITY, base * mult)


# ════════════════════════════════════════════════════════════════
# [2026-08 신설, 부상 시스템 확장 — 경기 중 부상] 신민용 확정: 훈련 부상은
# "관리의 결과"(stress/injury_load가 쌓이면 위험도 같이 오르는 연속적 구조),
# 경기 부상은 "경기에서 발생하는 희귀한 사고"라는 성격 차이를 명확히 둔다.
# 그래서 훈련과 같은 risk_score 합성 방식을 쓰지 않고, 포지션·부상체질별
# 고정 기본 확률(매우 낮음) × injury_load 보정이라는 훨씬 단순한 구조로
# 간다 — "관리 잘해서 경기 나갔더니 경기에서 계속 다친다"는 이상한
# 결과를 피하기 위해 injury_load 보정 자체도 완만하게(최대 2.5배) 잡는다.
# ════════════════════════════════════════════════════════════════

# 경기당 기본 부상 확률(신민용 확정치) — 부상체질만 명시, 나머지는 전부
# 동일하게 기본값(강철체질은 애초에 injury_immune이라 이 표 자체를 안 봄 —
# 호출부에서 면역 체크가 먼저 걸린다).
MATCH_INJURY_BASE_PROBABILITY = {
    "부상체질": 0.05,
}
MATCH_INJURY_BASE_PROBABILITY_DEFAULT = 0.01

# injury_load에 따른 경기 부상 확률 배율 — 완만하게(최대 ×2.5). 훈련 쪽
# INJURY_RISK_MULT_CURVE(최대 ×25)를 그대로 갖다 쓰면 기본 1~5%에 곱해져
# 극단적으로 커지므로(예: 5%×25=125%) 별도의 훨씬 완만한 커브를 쓴다.
MATCH_INJURY_LOAD_RISK_CURVE = [
    (0, 1.0), (50, 1.0), (70, 1.3), (85, 1.7), (95, 2.1), (100, 2.5),
]
# 최종 확률 상한 — injury_load가 극단(100 근처)이어도 이 값을 못 넘음.
MATCH_INJURY_MAX_PROBABILITY = 0.15

# [2026-09 신설, 신민용 요청: "경기 전날 고강도 훈련을 하면 경기 부상
# 확률을 높여줘"] injury_load 곡선만으로는 고강도 세션 딱 한 번만으로는
# 거의 안 움직인다(위 커브가 50 미만이면 배율 1.0 그대로라, 그 전까지
# 잘 관리해온 선수라면 하루 고강도 정도로는 50을 잘 안 넘음) — "바로
# 전날 고강도를 했다"는 사실 자체에 대한 별도의 직접적인 배율을 둔다.
MATCH_INJURY_PREV_HIGH_INTENSITY_MULT = 1.5


def calc_match_injury_probability(injury_load: float, physical_trait: str,
                                   prev_day_high_intensity: bool = False) -> float:
    """경기 1회당 부상 확률(0~1). 강철체질(injury_immune)은 호출부에서
    이 함수 자체를 안 부르고 걸러낸다 — 이 함수는 "면역이 아닌 선수"만
    대상으로 한다.
    [2026-09 확장] prev_day_high_intensity=True(경기 바로 전날 고강도
    훈련을 했음)면 MATCH_INJURY_PREV_HIGH_INTENSITY_MULT(1.5배)를 추가로
    곱한다 — injury_load 배율과는 별개로 겹쳐 적용된다(둘 다 반영해야
    "몸 상태 자체도 안 좋은데 전날 무리까지 했다"가 제대로 표현됨)."""
    base = MATCH_INJURY_BASE_PROBABILITY.get(physical_trait, MATCH_INJURY_BASE_PROBABILITY_DEFAULT)
    mult = _lerp_curve(MATCH_INJURY_LOAD_RISK_CURVE, injury_load)
    if prev_day_high_intensity:
        mult *= MATCH_INJURY_PREV_HIGH_INTENSITY_MULT
    return min(MATCH_INJURY_MAX_PROBABILITY, base * mult)


# [2026-08 신설] injury_load가 높을수록 심각/매우심각 쪽으로 부상 종류
# 가중치가 이동하도록 — 등급(tier)별로 서로 다른 커브를 곱한다. 84와 85
# 사이에 갑자기 다른 세상이 되지 않도록 계단식이 아니라 연속 선형보간.
# 숫자는 "심각" 등급에 대해 GPT가 준 예시(20→×0.8 ... 95→×2.2)를 기준점
# 삼아, 등급이 낮을수록 반대 방향(낮은 load일 때 유리)으로, 등급이 높을
# 수록 더 가파르게 되도록 대칭적으로 확장한 잠정치 — QA 후 조정 예정.
INJURY_LOAD_SEVERITY_CURVE = {
    "경미":     [(0, 1.3), (20, 1.2), (50, 1.0), (70, 0.8), (85, 0.6), (95, 0.4), (100, 0.4)],
    "중간":     [(0, 1.0), (20, 1.0), (50, 1.0), (70, 1.05), (85, 1.1), (95, 1.1), (100, 1.1)],
    "심각":     [(0, 0.7), (20, 0.8), (50, 1.0), (70, 1.3), (85, 1.7), (95, 2.2), (100, 2.2)],
    "매우 심각": [(0, 0.4), (20, 0.5), (50, 1.0), (70, 1.6), (85, 2.4), (95, 3.4), (100, 3.4)],
}

# [2026-08 신설] 나이가 들수록 injury_load가 더 크게 쌓이는 배율. 20대=1.0,
# 30대=1.2, 40대=1.4, 50대 이상=1.6(상한 clamp). 저장하지 않고 나이에서
# 매번 즉시 계산(GPT 권고 — "시즌 전환 때만 갱신"은 생일이 시즌 중일 수
# 있어 부정확해질 여지가 있고, 이런 단순 산술은 매 세션 계산해도 성능에
# 영향 없음).
def get_injury_load_age_mult(age: int) -> float:
    return min(1.6, 1.0 + max(0, (age - 20) // 10) * 0.2)


# [2026-08 v5 신설, 신민용+GPT 확정 — v4 실패 이후 재설계] injury_load
# 훈련 증가값을 stress_delta 재사용에서 완전히 독립된 자체 스케일로
# 분리한다. v4가 실패한 이유는 stress_delta(11)를 그대로 베이스로 쓰고
# 거기에 배율(×1.8)+경기배율(×1.4)을 곱해서 두 배율이 겹쳐 곱셈 폭발이
# 났기 때문 — 이번엔 처음부터 작은 자체 숫자로 시작해서 배율을 겹치지
# 않는다. 목표: 중강도(4)-휴식(7)은 거의 회복, 고강도(14)-휴식(7)은
# 절반이 남아서 반복하면 서서히 누적(14→7→21→14→28→21...). "stress는
# 이번엔 다시 안 건드린다"는 원칙 — 이 표는 stress와 완전히 무관.
INJURY_LOAD_TRAINING_VALUE = {
    "저강도": 2,
    "중강도": 4,
    "강점훈련": 7,
    "약점훈련": 7,
    "고강도": 14,
}
# injury_load 전용 휴식 회복값 — stress의 -20과 별개. -7/-11도 실측해봤으나
# -7은 A(중강도3+휴식3+경기1) 같은 "안전해야 할" 패턴까지 연 2.53회로
# 터졌고(경기 자체가 새 훈련 스케일보다 상대적으로 커서), -11도 B(고강도
# 2회+휴식3+경기1)가 연 3.00회로 아직 높았다. -13에서 A/B(고강도 0~2회)는
# 연 0.2~0.4회로 안전, C/D(고강도 3~4회)는 연 4.3~5.9회로 뚜렷하게
# 위험해지는 깔끔한 계단이 나와 이 값으로 확정(자세한 실측 과정은 부상
# 시스템 설계 문서 3.13 참고).
INJURY_LOAD_REST_RECOVERY = -13


# [2026-08 v5 수정 — 실측 후 GPT 지시 재검토] "경기는 기존 age×match
# 방식 유지"로 시작했으나, 실제로 돌려보니 문제가 발견됨: 훈련은 독립
# 스케일로 작게 줄었는데(고강도=14) 경기는 옛 stress 기반 큰 값(승6~패16)
# ×1.4를 그대로 써서 경기 하나(평균 15.4)가 "중강도3+휴식3" 6일 전체
# 순변화(-9)보다 커져버렸다 — 결과적으로 경기가 있는 주는 훈련을 어떻게
# 짜든 거의 항상 순증가로 뒤집혀서, "안전해야 할" 패턴(A)까지 연 2.53회로
# 폭증(실측 확인). 그래서 경기도 훈련과 같은 독립 스케일 원칙으로
# 맞춘다 — MATCH_INJURY_LOAD_MULT는 더 이상 쓰지 않고, 승/무/패 각각의
# injury_load 값을 훈련 스케일과 비슷한 크기로 직접 지정(경기가 "고강도
# 훈련 한 번과 비슷하거나 약간 더 부담" 수준이 되도록 — 실제로 경기가
# 어떤 단일 훈련보다도 신체 부담이 크다는 원래 취지는 유지하되 스케일만
# 맞춤).
INJURY_LOAD_MATCH_VALUE = {"win": 4, "draw": 8, "loss": 13}

# [2026-08 신설, 신민용+GPT 확정: "스트레스는 리그 경기에서 더 쌓이며
# 승/무/패에 따라 차이가 있어야"] 경기 stress = 기본부하 + 결과보정.
# 1차 실험값 — 승6 / 무11 / 패16. base+modifier로 분리해두면 나중에
# 라이벌전/컵결승/연패 같은 상황을 modifier 쪽에 추가로 얹기 쉽다.
MATCH_STRESS_BASE = 10
MATCH_RESULT_STRESS_MOD = {"win": -4, "draw": 1, "loss": 6}


def get_position_multiplier(entry: dict, pos_group: str = None) -> float:
    """부상 종류 하나(entry)에 대한 포지션 보정 배율. pos_group은
    get_injury_pos_group()의 반환값('GK'/'DF'/'MF'/'FW')."""
    if pos_group and entry["pos_cat"]:
        return INJURY_POSITION_MULT.get(entry["pos_cat"], {}).get(pos_group, 1.0)
    return 1.0


def get_severity_multiplier(entry: dict, injury_load: float = None) -> float:
    """injury_load가 높을수록 심각/매우심각 쪽으로 분포를 이동시키는 배율
    (INJURY_LOAD_SEVERITY_CURVE, 등급별 연속 보정)."""
    if injury_load is not None:
        curve = INJURY_LOAD_SEVERITY_CURVE.get(entry["tier"])
        if curve:
            return _lerp_curve(curve, injury_load)
    return 1.0


def get_recurrence_multiplier(entry: dict, pos_group: str = None,
                               activity_type: str = None, training_type: str = None,
                               vulnerable_body_part: str = None) -> float:
    """[2026-08 구현, GPT 확정 — 부상 시스템 확장 5단계: 재발]
    vulnerable_body_part(예: 'l_knee', 재발 취약기가 활성화된 부위)가 주어지고
    이 entry의 부위(좌우 접두사 뗀 기준 — 재발 판정은 부위 단위, 좌우는
    _apply_injury에서 별도로 그 방향에 살짝 더 치우치게 뽑음)가 같으면,
    activity_type/training_type에 따라 가중치를 올린다. "부상 발생 확률"이
    아니라 "발생했을 때 하필 그 부위일 확률"만 올리는 것 — 일반 훈련은
    보정 없음(×1.0 그대로), 고강도 훈련·경기만 취약 부위 가중치를 올린다."""
    if not vulnerable_body_part:
        return 1.0
    parts = vulnerable_body_part.split("_", 1)
    base_vuln = parts[1] if len(parts) == 2 and parts[0] in ("l", "r") else vulnerable_body_part
    if entry["body_part"] != base_vuln:
        return 1.0
    if activity_type == "match":
        return INJURY_RECURRENCE_MULT.get("경기", 1.0)
    if training_type == "고강도":
        return INJURY_RECURRENCE_MULT.get("고강도", 1.0)
    return INJURY_RECURRENCE_MULT.get("기본", 1.0)


# [2026-08 신설, GPT 확정] 재발 취약기 중 부위 가중치 배율. "일반 훈련은
# 재발 보정 없음"이 곧 ×1.0이라는 뜻 — 고강도/경기만 명시적으로 올린다.
INJURY_RECURRENCE_MULT = {
    "기본": 1.0,   # 중강도/저강도/강점/약점훈련 등 — 보정 없음
    "고강도": 1.4,
    "경기": 1.9,
}


def get_training_injury_multiplier(entry: dict, activity_type: str = None,
                                    training_type: str = None) -> float:
    """[2026-08 신설, 자리만 확보 — GPT 권고] 나중에 훈련 종류가 세분화되면
    (예: 스프린트→햄스트링/종아리, 점프→무릎/발목, GK반사훈련→손/손목/어깨
    같은 방향) 여기서 activity_type("training"/"match")과 training_type
    (구체 훈련 종류)에 따라 부상 종류별 가중치를 보정할 자리. 지금은 훈련
    종류 자체가 세분화되지 않았고 그 배율을 미리 정하면 나중에 훈련
    시스템이 바뀔 때 다시 갈아엎어야 하므로, 인터페이스만 만들고 항상
    1.0을 반환한다."""
    return 1.0


def get_injury_weight(entry: dict, position: str = None, injury_load: float = None,
                       activity_type: str = None, training_type: str = None,
                       vulnerable_body_part: str = None) -> float:
    """부상 종류 하나(entry)의 최종 추첨 가중치 = 기본가중치 × 각 보정의
    곱. 보정을 여기 한 곳에 모아두면 새 보정(재발, 훈련종류별 등)이
    추가돼도 pick_injury() 자체는 안 건드려도 된다."""
    pos_group = get_injury_pos_group(position) if position else None
    w = entry["weight"]
    w *= get_position_multiplier(entry, pos_group)
    w *= get_severity_multiplier(entry, injury_load)
    w *= get_recurrence_multiplier(entry, pos_group, activity_type, training_type,
                                    vulnerable_body_part)
    w *= get_training_injury_multiplier(entry, activity_type, training_type)
    return w


def pick_injury(position: str = None, injury_load: float = None,
                 activity_type: str = None, training_type: str = None,
                 vulnerable_body_part: str = None):
    """INJURY_POOL에서 가중치 기반으로 부상 하나를 뽑는다. 실제 가중치
    계산은 get_injury_weight()에 위임(보정 항목이 늘어나도 이 함수는
    안 바뀜). vulnerable_body_part가 주어지면(재발 취약기 활성 중) 그
    부위 계열 항목의 가중치가 activity_type/training_type에 따라 올라간다
    (get_recurrence_multiplier — 재발 시스템, 2026-08 구현).
    반환: INJURY_POOL의 항목 dict 하나(그대로 복사하지 않고 참조 반환하므로
    호출부에서 내용을 변경하지 말 것)."""
    weights = [get_injury_weight(entry, position, injury_load, activity_type,
                                  training_type, vulnerable_body_part)
               for entry in INJURY_POOL]
    return random.choices(INJURY_POOL, weights=weights, k=1)[0]

# 성격
PERSONALITY_EFFECTS = {
    "성실함":   {"train_eff": 1.20},
    "게으름":   {"train_eff": 0.80},
    "냉철함":   {"stress_mult": 0.90},
    "긍정적":   {"happy_gain_mult": 1.15},
    "소심함":   {"big_match_rating": -0.3},
    "승부욕":   {"losing_rating": +0.3},
    "리더십":   {"team_win_bonus": 0.03},
    # [2026-08 옐로카드 시스템 도입 시, 신민용+GPT 협의] 원래 폭력적
    # 성격은 스트레이트 레드 확률(1.2%)에만 +5%p를 얹었는데, 옐로카드를
    # 도입하며 "폭력적 성향이 옐로카드를 통해서도 드러나야 한다"는 방향
    # 으로 yellow_card_chance(+3%p)를 추가했다.
    # [2026-08 재조정, 신민용 밸런스 요청: "폭력적인 경우엔 레드를 2~3장
    # 받을 정도로 눈에 띄어야 한다"] yellow_card_chance만으로는 시즌
    # 기대 레드카드가 0.6~0.7장까지 떨어져 성격 효과가 잘 안 느껴졌다 —
    # 스트레이트 레드에도 다시 보정을 얹는다(예전 +5%p보다 낮은 +4%p로
    # 완화). 시즌 44경기 기준 기대치: 스트레이트 레드(1.2%+4%=5.2%/경기)
    # 약 2.3장 + 2차옐로 경유 약 0.16장 ≈ 시즌 합계 2.5장 근처(TUNE LATER,
    # 실제 리그별 경기 수 14~58경기 편차가 있어 몬테카를로 재검증 필요).
    "폭력적":   {"yellow_card_chance": 0.03, "red_card_chance": 0.04},
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
    # [2026-08 재설계, 신민용 확정: "훈련에서 확률적으로 부상을 입히는
    # 기능은 없애고, 신체 부담(injury_load) 시스템으로 대체"] 예전엔
    # injury_add(부상 확률 직접 가산)로 매 훈련 세션마다 별도 룰렛을
    # 돌렸다 — 이제 부상은 injury_load가 100에 도달했을 때만 발생하므로
    # (game_engine._process_training 참고), 부상체질은 "그 100에 더 빨리
    # 도달하도록" injury_load가 쌓이는 속도 자체를 배로 만드는 방식으로
    # 바뀐다. 강철체질은 injury_immune 플래그로 "부담이 100이어도 부상
    # 자체가 아예 발생하지 않는" 완전 면역을 명시적으로 표현한다(예전엔
    # injury_add=-1.0이라는 별도 문서화 안 된 센티널 값으로 면역을
    # 표현했었음).
    "부상체질":   {"injury_load_mult": 1.5},                    # 신체 부담 누적 속도 ×1.5
    "강철체질":   {"injury_immune": True, "stamina_train": 1.15}, # 부상 완전 면역 + 체력훈련+15%
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
# [2026-08 재설계, 신민용 확정: "에이전트 기본 상태를 '없음'으로 하고,
# F급도 이제 실제로 돈 주고 사는 등급으로 만들자 — F는 E 수준으로,
# E는 D 수준으로... 한 단계씩 밀어올리고, S는 그 위에 10억 단위로 새로
# 얹는다"] 예전엔 F가 사실상 "에이전트 없음"의 대역이었다(수수료 0%,
# 계약금 0, 기본 상태) — 이제 그 역할은 AGENT_NONE("없음")으로 완전히
# 분리하고, F부터는 전부 실제로 계약금을 내고 사는 등급이 된다. 사다리
# 전체가 한 칸씩 밀린 것뿐이라(F←old E, E←old D, D←old C, C←old B,
# B←old A, A←old S), 기존에 이미 튜닝돼 있던 등급 간 격차 자체는 그대로
# 유지된다. S만 기존 최상단(old S) 위에 새 값으로 얹는다.
AGENT_NONE = "없음"
AGENT_GRADES = ["F","E","D","C","B","A","S"]
AGENT_FEE_RATE = {
    "없음": 0.00,  # old F
    "F": 0.03,     # old E
    "E": 0.06,     # old D
    "D": 0.10,     # old C
    "C": 0.15,     # old B
    "B": 0.20,     # old A
    "A": 0.28,     # old S
    "S": 0.35,     # 신설 — 기존 최상단(28%)보다 한 단계 더 위
}
AGENT_UPPER_LEAGUE_BONUS = {
    "없음": 0,  # old F
    "F": 1,     # old E
    "E": 1,     # old D
    "D": 2,     # old C
    "C": 2,     # old B
    "B": 3,     # old A
    "A": 3,     # old S
    "S": 4,     # 신설
}

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

# [2026-08 대개편, 신민용 확정: "포메이션 7개 → 20개, 전술적 다양성 확보"]
# 기존 7개에서 20개로 확장. LAM/RAM을 새 "실제 포지션"으로 만들지 않고
# 기존 CAM 슬롯을 두 번 쓰는 방식으로 통일했다(POSITION_COMPAT에 새
# 항목을 안 만들어도 됨 — LM/RM/LWB/RWB처럼 이미 있는 "포메이션 전용
# 슬롯, 실제 선수 배치는 호환 리스트로 해결" 패턴을 그대로 재사용).
# "-ATT"(공격형) 접미사는 기존 프로젝트의 영문 suffix 스타일에 맞춘
# 표기(신민용 확정) — 문자열 비교/DB/정렬에서 한글 접미사보다 안전하다.
FORMATION_SLOTS = {
    # ── 4백 (9) ──
    "4-4-2":     ["GK","CB","CB","LB","RB","LM","CM","CM","RM","ST","ST"],
    "4-4-1-1":   ["GK","CB","CB","LB","RB","LM","CM","CM","RM","CAM","ST"],
    "4-3-3":     ["GK","CB","CB","LB","RB","CDM","CM","CM","LW","RW","ST"],
    "4-3-3-ATT": ["GK","CB","CB","LB","RB","CM","CM","CAM","LW","RW","ST"],
    "4-1-4-1":   ["GK","CB","CB","LB","RB","CDM","LM","CM","CM","RM","ST"],
    "4-2-3-1":   ["GK","CB","CB","LB","RB","CDM","CDM","LW","CAM","RW","ST"],
    "4-2-2-2":   ["GK","CB","CB","LB","RB","CDM","CDM","CAM","CAM","ST","ST"],
    "4-3-1-2":   ["GK","CB","CB","LB","RB","CDM","CM","CM","CAM","ST","ST"],
    "4-3-2-1":   ["GK","CB","CB","LB","RB","CDM","CM","CM","CAM","CAM","ST"],

    # ── 3백 (5) ──
    "3-4-3":     ["GK","CB","CB","CB","LM","CM","CM","RM","LW","RW","ST"],
    "3-4-2-1":   ["GK","CB","CB","CB","LM","CM","CM","RM","CAM","CAM","ST"],
    "3-4-1-2":   ["GK","CB","CB","CB","LM","CM","CM","RM","CAM","ST","ST"],
    "3-5-2":     ["GK","CB","CB","CB","LWB","CDM","CM","CM","RWB","ST","ST"],
    "3-5-1-1":   ["GK","CB","CB","CB","LWB","CM","CM","CM","RWB","CAM","ST"],

    # ── 5백 (3) ──
    "5-3-2":     ["GK","CB","CB","CB","LWB","RWB","CM","CM","CM","ST","ST"],
    "5-4-1":     ["GK","CB","CB","CB","LWB","RWB","LM","CM","CM","RM","ST"],
    "5-2-3":     ["GK","CB","CB","CB","LWB","RWB","CDM","CDM","LW","RW","ST"],

    # ── 변형 (3) ──
    "4-2-4":      ["GK","CB","CB","LB","RB","CM","CM","LW","RW","ST","ST"],
    "4-1-2-1-2":  ["GK","CB","CB","LB","RB","CDM","LM","RM","CAM","ST","ST"],
    "4-5-1":      ["GK","CB","CB","LB","RB","LM","CM","CDM","CM","RM","ST"],
}

# ── 포메이션 스타일 보정치 ───────────────────────────────────
# 경기 시뮬(_match_win_probs/diff 계산)에 더해지는 소폭 팀 전력 보정.
#   공격적 포메이션(공격수多)은 +, 수비적 포메이션(CB/수비형MF多)은 -.
#   전력차(OVR)를 뒤집을 정도가 아니라 "같은 실력이면 스타일 차이로
#   승부가 살짝 갈릴 수 있다" 수준의 미세 조정 — [2026-08 재확인, 신민용
#   확정] 20개로 늘려도 상한은 그대로 ±1.5 유지(club_strength/prestige
#   보정 재조정 때 정한 "포메이션 하나로 경기 결과가 지나치게 안 흔들려야
#   한다"는 원칙과 동일하게 적용).
FORMATION_STYLE = {
    "4-4-2":      0.0,   # 기준(중립)
    "4-4-1-1":   -0.2,   # 살짝 수비적 (스트라이커 1 + CAM)
    "4-3-3":     +1.0,   # 공격적 (윙어 2 + 스트라이커)
    "4-3-3-ATT": +1.5,   # 최대 공격형 (수비형MF 없이 CM+CM+CAM)
    "4-1-4-1":   -1.0,   # 수비적 (단일 CDM 앵커)
    "4-2-3-1":   +0.3,   # 균형에 가까운 약공격
    "4-2-2-2":   +0.5,   # 더블 볼란치가 지켜주는 공격형
    "4-3-1-2":   +0.4,   # 중앙 집중 공격형
    "4-3-2-1":   +0.6,   # 중앙 공격 자원 多
    "3-4-3":     +1.5,   # 매우 공격적 (스리백 리스크 감수)
    "3-4-2-1":   +1.0,   # 스리백 + 공격 자원 多
    "3-4-1-2":   +0.8,   # 스리백 + 중앙 공격
    "3-5-2":     -0.5,   # 미드필드 장악형, 약간 수비적
    "3-5-1-1":   -0.3,   # 미드필드 두꺼움, 약한 공격
    "5-3-2":     -1.5,   # 수비적 (스리백+수비형)
    "5-4-1":     -1.5,   # 최대 수비형 (5백 + 스트라이커 1)
    "5-2-3":     -0.8,   # 5백이지만 전방 3 — 수비 우선 하이브리드
    "4-2-4":     +1.5,   # 매우 공격적 (공격수 4)
    "4-1-2-1-2": +0.2,   # 좁은 다이아몬드형, 약공격
    "4-5-1":     -1.2,   # 미드필드 두껍고 최전방 1명, 수비적
}

# [2026-08 신설, 신민용 확정: "포메이션 경기 보정은 두 축으로 분리"]
#   FORMATION_STYLE(위, 정적 성향, ±1.5) + SQUAD_FORMATION_FIT(스쿼드가
#   실제로 이 포메이션에 얼마나 맞는지, ±1.0) = 최대 ±2.5. club_strength
#   때와 같은 원칙 — "선택으로 손해/이득을 볼 수는 있지만, 그 자체가
#   실력차를 뒤집을 만큼 크면 안 된다." 계산은 formation_logic.py의
#   formation_fit_bonus() 참고 (POSITION_MISMATCH_PENALTY 기반).
SQUAD_FORMATION_FIT_MAX = 1.0
# 위 보너스를 만들 때 기준으로 삼는 "평균적인" 미스매치 정도(0~0.15,
# POSITION_MISMATCH_PENALTY 최댓값 기준) — 이보다 적게 어긋나면 +, 많이
# 어긋나면 -. SPAN은 그 기준에서 얼마나 벗어나야 ±MAX에 도달하는지의 폭.
SQUAD_FORMATION_FIT_BASELINE_PENALTY = 0.06
SQUAD_FORMATION_FIT_PENALTY_SPAN = 0.09

# [2026-08 신설, 신민용 확정: "팀 전술 성향 5단계, 완전 균등 랜덤은 안
# 된다 — 대부분은 균형~공격/수비 사이, 극단은 소수"] 팀 생성 시 1회
# 가중 랜덤 배정 후 teams.tactic_tendency에 영구 저장(감독 시스템이 따로
# 생기기 전까지는 시즌마다 안 바뀜 — club_ambition과 달리 이건 "장기
# 구단 전술 철학"에 가깝다는 신민용 판단).
TACTIC_TENDENCIES = ["VERY_ATTACKING", "ATTACKING", "BALANCED", "DEFENSIVE", "VERY_DEFENSIVE"]
TACTIC_TENDENCY_WEIGHTS = [10, 25, 30, 25, 10]
# FORMATION_STYLE과 같은 축(±1.5)에 맞춘 성향별 수치 — 포메이션 선택
# 점수(tendency_fit) 계산 시 FORMATION_STYLE 값과 직접 비교한다.
TACTIC_TENDENCY_LEAN = {
    "VERY_ATTACKING": 1.5,
    "ATTACKING":       0.75,
    "BALANCED":        0.0,
    "DEFENSIVE":      -0.75,
    "VERY_DEFENSIVE": -1.5,
}
TACTIC_TENDENCY_KO = {
    "VERY_ATTACKING": "매우 공격적", "ATTACKING": "공격적", "BALANCED": "균형",
    "DEFENSIVE": "수비적", "VERY_DEFENSIVE": "매우 수비적",
}

# [2026-08 신설] 시즌마다 포메이션을 다시 검토할 확률(기존 _shuffle_
# formations의 20% 순수 랜덤 교체를 대체) — 이 확률에 걸린 팀만 아래
# 가중치로 포메이션을 재선택한다(안 걸리면 기존 포메이션 유지, 단
# formation_fit_bonus는 로스터가 바뀌었을 수 있으므로 전 팀 매 시즌
# 갱신). 재검토 시 스쿼드 적합도가 가장 큰 비중을 갖는다 — "수비 성향
# 팀이라고 무조건 5-4-1만 고르지 않는다"는 신민용 요청 반영.
FORMATION_REEVAL_PROB = 0.30
FORMATION_SCORE_WEIGHTS = {"squad_fit": 0.6, "tendency_fit": 0.3, "random": 0.1}
FORMATION_CANDIDATE_TOP_N = 5

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
#
# [2026-08 확장, 신민용 리포트: "50세까지 뛰니 일부 스탯(117)만 멈춰있고
# 일부(40)만 바닥을 친다"] 두 가지 버그가 겹쳐 있었다:
#   버그1) 이 표가 41~45세 구간까지만 정의돼 있어서, 46세부터는 그 어떤
#          구간에도 안 걸려 annual_drop이 조용히 0으로 떨어졌다(노화 정지).
#          → 46~50/51~55/56+ 구간을 모든 등급에 추가하고, 그래도 정의
#          범위를 넘는 나이는 게임_engine._end_of_season의 안전장치가
#          마지막 구간 값을 계속 적용한다(구조적으로 0이 될 수 없음).
#   버그2) 재능 등급이 실제론 9단계(god/worldclass/superstar/elite/pro/
#          semipro/amateur/ordinary/untalented)인데 이 표는 옛 5단계만
#          있었다 — god/superstar/amateur/untalented는 이 표에 키가 없어
#          매번 조용히 AGING_DECLINE["pro"]로 폴백됐다(가장 재능있는
#          god이 중간 등급 pro와 같거나 더 빨리 늙는 역전 현상). 9단계
#          전부에 키를 채워서 해결한다 — worldclass는 기존 검증값을
#          그대로 보존, superstar/amateur/untalented는 이번에 새로
#          채웠다(god과 동일하게 실전 미검증 추정치).
#
# [등급별 노화 내성 위계] god(가장 느림) > worldclass > superstar > elite
# > pro > semipro > amateur > ordinary > untalented(가장 빠름) — 다만
# 매 구간마다 아래 등급이 반드시 더 큰 값이어야 하는 건 아니고(기존
# 검증값의 구간별 비단조 구간은 그대로 보존), 전체적인 추세만 이 순서를
# 따른다. pro/ordinary가 41~45세에 우연히 같은 값(2.33)인 것은 기존
# 검증값이라 그대로 뒀다 — amateur/untalented처럼 이번에 새로 만든
# 자리만 겹치지 않게 분리했다.
AGING_DECLINE = {
    # [2026-08 v3.5 재설계, 신민용 확정: "신급도 노화가 덜해야 한다 —
    # 40대에도 OVR90 이상. 100은 월드클래스 최상위(WC_TOP)와 비슷한
    # 수준이면 되고, 신급만의 차이는 talent_cap이 105까지 열려있다는
    # 것"] 예전 god 낙폭은 사실상 worldclass보다 딱 두 배 정도만
    # 완만한 수준이라("40대인데 체력이 80일 때가 있다" 버그를 잡은
    # AGING_PHYSICAL_AGE_CAP과 겹치면) peak100 ST가 40세에 81, 45세에
    # 63까지 떨어졌다 — "신급"이라는 이름에 안 맞게 가팔랐다. 아래
    # 표는 실제 시뮬레이션(calc_ovr 그대로 재현)으로 "peak100 ST가
    # 40대 내내 90 안팎을 유지"하도록 다시 잡은 값 — 신체 계열 낙폭이
    # 압도적 비중(1.35)이라 아래 AGING_PHYSICAL_AGE_CAP_GOD(신급 전용
    # 완만한 신체 상한)과 반드시 같이 적용해야 이 목표가 나온다(둘 중
    # 하나만 바꾸면 부족함 — 실측으로 확인됨).
    "god":         [(29,31,0.20),(32,34,0.30),(35,37,0.40),(38,40,0.50),(41,45,0.55),
                     (46,50,0.70),(51,55,1.00),(56,999,1.50)],
    "worldclass":  [(29,31,1.00),(32,34,1.33),(35,37,1.33),(38,40,2.67),(41,45,4.00),
                     (46,50,5.00),(51,55,6.00),(56,999,7.00)],
    "superstar":   [(29,31,1.50),(32,34,2.00),(35,37,2.30),(38,40,3.20),(41,45,3.50),
                     (46,50,4.00),(51,55,4.50),(56,999,5.00)],
    # [2026-08 v3.5 재조정, 신민용+검토 확정: "elite~untalented 6개 등급이
    # 전부 38~40세에서 튀어 올랐다가 41세부터 오히려 낙폭이 다시 작아지는
    # 들쭉날쭉한 곡선이다 — god/WC_TOP처럼 매끄럽게 증가해야 자연스럽다"]
    # 29~40세 구간은 이번엔 그대로 두고(별도 논의 대상), 41세 이후 4구간만
    # (a) 각 등급 안에서 41→46→51→56이 항상 증가하고 38~40 값보다도 항상
    # 크도록, (b) superstar<elite<pro<semipro<amateur<ordinary<untalented
    # 순서가 41세 이후 모든 구간에서 유지되도록 다시 잡았다.
    "elite":       [(29,31,2.00),(32,34,2.67),(35,37,3.17),(38,40,3.83),(41,45,4.20),
                     (46,50,4.60),(51,55,5.00),(56,999,5.50)],
    "pro":         [(29,31,3.00),(32,34,3.33),(35,37,3.00),(38,40,3.00),(41,45,4.80),
                     (46,50,5.30),(51,55,5.80),(56,999,6.40)],
    "semipro":     [(29,31,4.50),(32,34,4.33),(35,37,3.00),(38,40,4.17),(41,45,5.40),
                     (46,50,6.00),(51,55,6.60),(56,999,7.30)],
    "amateur":     [(29,31,4.80),(32,34,4.00),(35,37,3.80),(38,40,3.80),(41,45,5.80),
                     (46,50,6.40),(51,55,7.00),(56,999,7.70)],
    "ordinary":    [(29,31,5.00),(32,34,3.67),(35,37,4.17),(38,40,3.33),(41,45,6.20),
                     (46,50,6.80),(51,55,7.40),(56,999,8.10)],
    "untalented":  [(29,31,5.50),(32,34,4.50),(35,37,4.50),(38,40,3.80),(41,45,6.60),
                     (46,50,7.30),(51,55,8.00),(56,999,8.80)],
}
# worldclass 중 talent_cap 98+ 는 더 완만한 곡선 적용
# worldclass 중 talent_cap 98+ 는 더 완만한 곡선 적용
# [2026-08 v3.5 재조정, 신민용+검토 확정: "God 100(인간 최상위)과 WC 99가
# 전성기는 비슷한데 40대부터 WC만 갑자기 훨씬 빨리 늙는 건 이상하다"]
# 예전 값은 40세 79 → 45세 59로 5년 만에 20이 빠지는 등, God 100(40세
# 90→45세 83, 7 하락)과 격차가 너무 벌어졌다. "God 100=인간 최상위 /
# WC 99=그보다 조금 빠르게 늙지만 노장 커리어는 가능 / God 105=인간
# 한계를 초월"이라는 3단 계층이 서게 실측 기반으로 다시 잡았다(신체
# 상한은 기존 AGING_PHYSICAL_AGE_CAP 그대로 재사용 — god과 달리 WC는
# 전용 상한 없이도 목표 곡선에 도달함).
AGING_DECLINE_WC_TOP = [(29,31,0.70),(32,34,0.90),(35,37,1.20),(38,40,1.60),(41,45,2.10),
                        (46,50,2.90),(51,55,3.40),(56,999,4.30)]
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

# [2026-08 신설, 신민용 리포트: "40대인데 체력이 80일 때가 있다"]
# 시뮬레이션 검증(god/worldclass 등급 × GK) 결과, AGING_DECLINE 자체는
# 등급별로 잘 갈려있지만, 신체 계열(PHYSICAL_STATS)에 GK 포지션 배수
# (0.62, 전 계열 공통 적용)까지 겹치면 40세에도 스태미나가 80까지 남는
# 비현실적인 경우가 실제로 나온다 — GK가 판단력/포지셔닝을 오래 유지하는
# 건 현실적이지만, 스프린트 능력 같은 신체 스탯까지 그만큼 안 늙는 건
# 과하다. 그렇다고 AGING_DECLINE이나 AGING_POS_MULT 전체를 건드리면
# (기존에 검증된 등급별/계열별 곡선 전부가 다시 영향받아) 부작용이 크므로,
# 신체 스탯 4종에만 나이별 "절대 상한"을 별도로 하나 더 얹는다 — 등급/
# 포지션이 뭐든 이 나이가 되면 신체 스탯은 이 값을 절대 못 넘는다.
# (기술/멘탈 스탯은 전혀 안 건드림 — GK가 노년까지 판단력으로 버티는
# 특성은 그대로 유지된다.) 엘리트 이하 등급은 이미 이 상한보다 한참
# 먼저 자연 하락으로 바닥을 찍어서 실질적 영향이 없고, god/worldclass
# 최상위권에서만 체감된다(시뮬레이션 검증: god GK 스태미나 40세
# 80.1→73.7, 다른 등급/포지션은 변화 없음).
AGING_PHYSICAL_AGE_CAP = [
    (35, 37, 86), (38, 40, 78), (41, 43, 68),
    (44, 46, 58), (47, 49, 50), (50, 52, 44), (53, 999, 40),
]

def get_physical_age_cap(age):
    """나이에 해당하는 신체 스탯 상한을 반환. 정의된 구간 밖(35세 미만)이면
    None(상한 없음 — 그 나이는 AGING_DECLINE 자연 하락만으로 충분함)."""
    for a0, a1, cap in AGING_PHYSICAL_AGE_CAP:
        if a0 <= age <= a1:
            return cap
    return None

# [2026-08 v3.5 신설, 신민용 확정: "신급은 40대에도 OVR90 이상"] 위
# AGING_PHYSICAL_AGE_CAP은 전 등급 공통 상한이라, god 등급도 41세부터
# 신체 스탯이 68로, 47세부터는 50으로 강제로 눌린다 — AGING_DECLINE만
# 아무리 완만하게 해도 이 공통 상한 때문에 결국 신체 계열(가중치 1.35,
# 가장 큰 비중)이 여기 막혀서 OVR이 목표만큼 안 나온다(실측으로 확인:
# god 낙폭만 완화하고 이 상한을 그대로 두면 peak100 ST가 45세에 84에
# 그침). worldclass의 AGING_DECLINE_WC_TOP과 같은 원리로, god 등급만
# 별도의 훨씬 완만한 신체 상한을 쓴다 — game_engine.py의 나이 처리
# 루프에서 tier=="god"일 때만 이 표를 대신 참조한다.
AGING_PHYSICAL_AGE_CAP_GOD = [
    (35, 37, 95), (38, 40, 92), (41, 45, 88),
    (46, 50, 84), (51, 55, 78), (56, 999, 70),
]

def get_physical_age_cap_god(age):
    """god 등급 전용 신체 스탯 상한(get_physical_age_cap과 동일한 형식)."""
    for a0, a1, cap in AGING_PHYSICAL_AGE_CAP_GOD:
        if a0 <= age <= a1:
            return cap
    return None

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

# [2026-08 신설, 신민용 요청] 대회 주기를 GAME_START_YEAR에 고정 하드코딩된
# 절대 연도(2001/2002/2003/2004) 대신, 실제 월드컵 캘린더 기준으로 계산한다.
# 예: GAME_START_YEAR=1990이면 실제로 1990년이 월드컵 해이므로 WC_START_YEAR도
# 1990. GAME_START_YEAR=2005면 실제 다음 월드컵은 2006년이므로 WC_START_YEAR=
# 2006. WC_ANCHOR_YEAR=2002는 "실제 월드컵이 열린 해"이기만 하면 되고(4년
# 주기이므로 1930/1990/2002 등 어느 실제 월드컵 해를 기준으로 잡아도 결과는
# 동일 — mod 4 위상이 같음), 기존 GAME_START_YEAR=2000 세이브의 WC_START_YEAR=
# 2002를 그대로 보존하도록 2002를 앵커로 쓴다.
#
# 대륙컵/클럽월드컵/지역컵(유로 포함)은 월드컵과 매년 겹치지 않도록 서로
# 다른 위상(mod 4)으로 설계돼 있다(월드컵=+0, 클럽월드컵=+1, 대륙컵=+2,
# 지역컵=+3) — 이 상대 오프셋 자체가 스케줄 무충돌을 보장하는 핵심이라,
# 각자 독립적인 "실제 역사" 앵커를 따로 잡지 않고 WC_START_YEAR를 기준으로
# 파생시킨다(클럽월드컵은 intl_engine.py에서 이미 WC_START_YEAR+1로 파생
# 중이었음 — 대륙컵/지역컵도 동일한 방식으로 통일).
def get_next_tournament_year(start_year, base_year, cycle):
    """base_year를 기준으로 한 cycle년 주기 대회 중, start_year 이후(포함)
    가장 이른 개최 연도를 반환한다. base_year가 start_year보다 미래여도
    과거여도(예: 실제 앵커=2002인데 GAME_START_YEAR=1980처럼 앵커 이전)
    항상 '실제 대회 주기표'상의 연도가 정확히 나온다 — 순수 모듈러 연산이라
    방향에 상관없이 위상(phase)만으로 판단한다. (파이썬 %는 음수에도 항상
    [0, cycle) 범위의 값을 주므로 start_year가 base_year보다 훨씬 과거여도
    올바르게 동작 — 예: get_next_tournament_year(1980, 2002, 4) == 1982,
    get_next_tournament_year(1990, 2002, 4) == 1990.)"""
    diff = (start_year - base_year) % cycle
    if diff == 0:
        return start_year
    return start_year + (cycle - diff)


# [2026-08 버그수정, 신민용 리포트: "GAME_START_YEAR=2000인데 2000년에
# 대륙컵 없이 빈 해로 시작한다"] 예전엔 CONTINENTAL_START_YEAR를
# "WC_START_YEAR + 2"로 WC에서 파생시켰는데, 이게 틀렸다 — WC_START_YEAR
# 자체가 이미 "GAME_START_YEAR 이후 가장 가까운 WC 해"로 계산된 값이라,
# 여기에 +2를 더하면 대륙컵 앵커(2004) 기준으로 GAME_START_YEAR에 더 가까운
# 해(예: GAME_START_YEAR=2000이면 2004보다 4년 이른 2000 자체가 이미 같은
# 위상)를 그냥 지나쳐버린다. 4개 대회(월드컵/클럽월드컵/대륙컵/지역컵) 전부
# 서로 다른 실제 앵커 연도를 기준으로 "GAME_START_YEAR 이후 가장 가까운
# 해"를 각자 독립적으로 계산해야 한다 — 앵커 4개가 서로 다른 위상(mod 4)에
# 있기만 하면(2001,2002,2003,2004 → 각각 1,2,3,0) 겹칠 일이 없으므로 굳이
# 서로에게서 파생시킬 이유가 없다.
WC_INTERVAL       = 4
WC_ANCHOR_YEAR    = 2002   # 실제 월드컵이 열린 해(앵커) — 어느 실제 해를 써도 무방(4년 주기 위상 동일)
WC_START_YEAR     = get_next_tournament_year(GAME_START_YEAR, WC_ANCHOR_YEAR, WC_INTERVAL)

CWC_INTERVAL      = 4
CWC_ANCHOR_YEAR   = 2003   # 월드컵 다음 해(위상: WC앵커+1) — 클럽월드컵
CWC_START_YEAR    = get_next_tournament_year(GAME_START_YEAR, CWC_ANCHOR_YEAR, CWC_INTERVAL)

CONTINENTAL_INTERVAL   = 4
CONTINENTAL_ANCHOR_YEAR = 2004   # 위상: WC앵커+2
CONTINENTAL_START_YEAR = get_next_tournament_year(GAME_START_YEAR, CONTINENTAL_ANCHOR_YEAR, CONTINENTAL_INTERVAL)

# ══════════════════════════════════════════════════════════════
# 3단계: 지역 대회 (2026-08 설계 확정 v2 — 신민용 최종 개편안 전체 반영)
# ══════════════════════════════════════════════════════════════
# 월드컵(2002,06..)/대륙컵(2004,08..)/클럽월드컵(2003,07..) 사이에 있던
# "완전히 빈 해"(2001,05,09..)를 채운다 — 정확히 이 3개 주기가 안 겹치는
# 유일한 해라 스케줄 충돌이 없다. 유럽/남미는 대륙컵 자체가 이미 그 역할을
# 겸해서(유럽=54개국 예선 있는 유로, 남미=10개국뿐이라 하위분할 불필요)
# 지역대회를 안 둔다 — 오세아니아는 v2에서 새로 추가.
# [2026-09 개편] 북미도 이 목록에서 빠진다 — 옛 UNCAF/카리브 지역컵 2개가
# 골드컵(유로와 동일한 방식: 실제 예선 → 대륙컵 형식 본선, EURO_QUAL["북미"]
# 참고)으로 통합되면서 유럽과 같은 처리를 받는다.
REGIONAL_CUP_INTERVAL   = 4
REGIONAL_CUP_ANCHOR_YEAR = 2001   # 위상: WC앵커-1(=+3)
REGIONAL_CUP_START_YEAR = get_next_tournament_year(GAME_START_YEAR, REGIONAL_CUP_ANCHOR_YEAR, REGIONAL_CUP_INTERVAL)

# [2026-08 신설, 신민용 확정] "유로(EURO)" — 기존 대륙컵 자리(2004년 주기,
# "유럽 네이션스컵"으로 개명)와는 완전히 별개의 새 대회. 지역컵과 같은
# 해(2001,05,09..)에 열리며, 다른 지역컵과 다르게 유럽만 실제 예선을
# 돈다(여름 예선 → 겨울 본선, 54개국→컷오프6→48개국→12조×4→직행24→
# 24개국 본선→6조×4→16강~결승 — 기존 EURO_QUAL과 완전히 같은 포맷을
# 그대로 재사용, 연도만 다른 주기를 탄다).
EURO_NAME = "유로(EURO)"

# [2026-09 개편, 신민용 정정: "골드컵도 지역컵으로 가는거고 지역컵에서
# 표시되어야 하는거고"] 처음엔 골드컵을 유로(EURO)와 같은 "대륙컵(CONF)
# 티어"로 만들었었는데, 이건 잘못 이해한 것이었다 — 골드컵은 (예전
# 중앙아메리카 UNCAF + 카리브 두 지역컵을 합친) 지역컵(REGION) 티어
# 그대로이고, "역대 지역컵" 탭에 떠야 한다. 다만 다른 지역컵과 달리 실제
# 예선(cont_qual)을 거쳐 24개국이 본선에 오르는 특수 케이스로 남겨둔다
# (EURO_QUAL["북미"], intl_engine._qualify_region의 "북미" 분기 참고 —
# 예선 결과가 있으면 그걸 쓰고, 없으면(예선 미실행 등) 다른 지역컵처럼
# OVR 상위컷으로 폴백한다).
REGION_CUP_NAME = {
    "동아시아":     "EAFF E-1 챔피언십",
    "동남아시아":   "AFF 챔피언십",
    "남아시아":     "SAFF 챔피언십",
    "중앙아시아":   "CAFA컵",
    "서아시아":     "WAFF 챔피언십",
    "북아프리카":   "북아프리카컵",
    "서아프리카":   "WAFU컵",
    "중앙아프리카": "중앙아프리카컵",
    "동아프리카":   "CECAFA컵",
    "남부아프리카": "COSAFA컵",
    "남미":         "코파 아메리카",
    "북미":         "골드컵",
    "오세아니아":   "오세아니아컵",
}
REGION_LIST = list(REGION_CUP_NAME.keys())

# [2026-08 신설] 세계기록실 "역대 지역컵" 탭의 대륙 필터용.
REGION_TO_CONTINENT = {
    "동아시아": "아시아", "동남아시아": "아시아", "남아시아": "아시아",
    "중앙아시아": "아시아", "서아시아": "아시아",
    "북아프리카": "아프리카", "서아프리카": "아프리카",
    "중앙아프리카": "아프리카", "동아프리카": "아프리카", "남부아프리카": "아프리카",
    "남미": "남미",
    "북미": "북미",
    "오세아니아": "오세아니아",
}

# [2026-08 신설] 지역별 "본선 목표 참가국 수" — 조 인원이 3·4·5명으로
# 깔끔하게 나누어떨어지도록(신민용 확정: "5팀 우선 → 4팀 → 3팀") 각
# 지역의 실제 회원국 풀에서 몇 개국을 뽑을지 미리 정해둔다. 회원국 풀이
# 이 숫자보다 많으면 그 해 국가 OVR 기준 상위 N개국만 본선行(매번 같은
# 나라가 고정으로 빠지는 게 아니라 그때그때 실력으로 판정). 풀이 이미
# 목표와 같거나 작으면 전원 참가(컷 없음).
REGION_TARGET_SIZE = {
    "동아시아": 8, "동남아시아": 10, "남아시아": 8, "중앙아시아": 5, "서아시아": 12,
    "북아프리카": 5, "서아프리카": 16, "중앙아프리카": 8,
    "동아프리카": 8, "남부아프리카": 12,
    "남미": 10,
    # [2026-09] 골드컵은 보통 EURO_QUAL["북미"] 실제 예선 결과(24개국)로
    # 채워지므로 이 값은 예선이 아직 없을 때만 쓰이는 폴백이다.
    "북미": 24,
    "오세아니아": 8,
}

# 국가명 → 소속 지역. data/countries.py의 실제 국가 목록과 전수 대조 완료.
COUNTRY_REGION = {
    # ── 동아시아 (EAFF, 9개국 풀 → 8개국 본선) ──
    "대한민국": "동아시아", "북한": "동아시아", "일본": "동아시아", "중국": "동아시아",
    "홍콩": "동아시아", "마카오": "동아시아", "중화 타이베이": "동아시아",
    "몽골": "동아시아", "괌": "동아시아",
    # ── 동남아시아 (AFF, 10개국 — 브루나이 제외) ──
    "베트남": "동남아시아", "태국": "동남아시아", "인도네시아": "동남아시아",
    "말레이시아": "동남아시아", "필리핀": "동남아시아", "미얀마": "동남아시아",
    "캄보디아": "동남아시아", "라오스": "동남아시아", "동티모르": "동남아시아",
    "싱가포르": "동남아시아",
    # (브루나이는 이 지역 미배정 — AFF 목표 참가국 10개 초과분)

    # ── 남아시아 (SAFF, 8개국 — 아프가니스탄 편입) ──
    "인도": "남아시아", "파키스탄": "남아시아", "방글라데시": "남아시아",
    "네팔": "남아시아", "부탄": "남아시아", "몰디브": "남아시아",
    "스리랑카": "남아시아", "아프가니스탄": "남아시아",
    # ── 중앙아시아 (CAFA, 5개국, 신설) ──
    "우즈베키스탄": "중앙아시아", "카자흐스탄": "중앙아시아", "키르기스스탄": "중앙아시아",
    "타지키스탄": "중앙아시아", "투르크메니스탄": "중앙아시아",
    # ── 서아시아 (WAFF, 12개국 — 예멘 제외) ──
    "이란": "서아시아", "사우디아라비아": "서아시아", "이라크": "서아시아",
    "카타르": "서아시아", "아랍에미리트": "서아시아", "오만": "서아시아",
    "쿠웨이트": "서아시아", "바레인": "서아시아", "요르단": "서아시아",
    "시리아": "서아시아", "레바논": "서아시아", "팔레스타인": "서아시아",
    # (예멘은 이 지역 미배정 — WAFF 목표 참가국 12개 초과분)

    # ── 북아프리카 (5개국, 신설) ──
    "모로코": "북아프리카", "알제리": "북아프리카", "튀니지": "북아프리카",
    "리비아": "북아프리카", "이집트": "북아프리카",
    # ── 서아프리카 (WAFU, 16개국 — A/B로 나눴던 걸 신민용 확정으로 다시
    #    하나로 합침: "16개의 팀이 4개씩 4그룹으로 해서 8강부터") ──
    "세네갈": "서아프리카", "말리": "서아프리카", "기니": "서아프리카",
    "기니비사우": "서아프리카", "감비아": "서아프리카", "카보베르데": "서아프리카",
    "라이베리아": "서아프리카", "시에라리온": "서아프리카", "모리타니": "서아프리카",
    "가나": "서아프리카", "나이지리아": "서아프리카", "코트디부아르": "서아프리카",
    "부르키나 파소": "서아프리카", "베냉": "서아프리카", "토고": "서아프리카",
    "니제르": "서아프리카",
    # ── 중앙아프리카 (8개국, 신설) ──
    "카메룬": "중앙아프리카", "콩고 민주 공화국": "중앙아프리카", "콩고 공화국": "중앙아프리카",
    "가봉": "중앙아프리카", "적도 기니": "중앙아프리카", "중앙아프리카공화국": "중앙아프리카",
    "차드": "중앙아프리카", "상투메 프린시페": "중앙아프리카",
    # ── 동아프리카 (CECAFA, 11개국 풀 → 8개국 본선) ──
    "케냐": "동아프리카", "우간다": "동아프리카", "탄자니아": "동아프리카",
    "르완다": "동아프리카", "부룬디": "동아프리카", "에티오피아": "동아프리카",
    "지부티": "동아프리카", "소말리아": "동아프리카", "에리트레아": "동아프리카",
    "남수단": "동아프리카", "수단": "동아프리카",
    # ── 남부아프리카 (COSAFA, 12개국 — 세이셸/코모로 제외) ──
    "남아프리카공화국": "남부아프리카", "잠비아": "남부아프리카", "짐바브웨": "남부아프리카",
    "말라위": "남부아프리카", "모잠비크": "남부아프리카", "앙골라": "남부아프리카",
    "나미비아": "남부아프리카", "보츠와나": "남부아프리카", "레소토": "남부아프리카",
    "에스와티니": "남부아프리카", "마다가스카르": "남부아프리카", "모리셔스": "남부아프리카",
    # (세이셸/코모로는 이 지역 미배정 — COSAFA 목표 참가국 12개 초과분)

    # [2026-09 개편, 신민용 확정: "북미 지역컵 2개(UNCAF/카리브)를 합쳐서
    # 골드컵으로"] 예전엔 "중앙아메리카"/"카리브" 둘로 나눠뒀었는데, 이제
    # 북미 33개국 전부를 하나의 "북미" 지역(골드컵)으로 합친다. 본선은
    # 보통 EURO_QUAL["북미"] 실제 예선 결과(24개국)로 채워진다(지역컵
    # 티어이지만 예선이 있는 특수 케이스 — REGION_CUP_NAME 주석 참고).
    "멕시코": "북미", "미국": "북미", "캐나다": "북미", "파나마": "북미",
    "코스타리카": "북미", "온두라스": "북미", "자메이카": "북미", "퀴라소": "북미",
    "아이티": "북미", "과테말라": "북미", "엘살바도르": "북미",
    "트리니다드 토바고": "북미", "니카라과": "북미", "도미니카 공화국": "북미",
    "세인트키츠 네비스": "북미", "푸에르토리코": "북미", "앤티가 바부다": "북미",
    "그레나다": "북미", "쿠바": "북미", "세인트루시아": "북미", "버뮤다": "북미",
    "세인트빈센트 그레나딘": "북미", "몬트세라트": "북미", "바베이도스": "북미",
    "벨리즈": "북미", "도미니카 연방": "북미", "아루바": "북미",
    "케이맨 제도": "북미", "터크스 케이커스 제도": "북미", "바하마": "북미",
    "미국령 버진아일랜드": "북미", "영국령 버진아일랜드": "북미", "앵귈라": "북미",

    # ── 남미 (코파 아메리카, 10개국, 신설) — 남미 대륙 12개국 풀 중
    #    가이아나/수리남은 미배정(코파 아메리카 실제 전통 참가국이 아님) ──
    "브라질": "남미", "아르헨티나": "남미", "우루과이": "남미", "콜롬비아": "남미",
    "칠레": "남미", "파라과이": "남미", "페루": "남미", "에콰도르": "남미",
    "볼리비아": "남미", "베네수엘라": "남미",

    # ── 오세아니아 (12개국 풀 → 8개국 본선, 신설) ──
    "호주": "오세아니아", "뉴질랜드": "오세아니아", "피지": "오세아니아",
    "솔로몬 제도": "오세아니아", "바누아투": "오세아니아", "사모아": "오세아니아",
    "통가": "오세아니아", "파푸아뉴기니": "오세아니아", "쿡 제도": "오세아니아",
    "뉴칼레도니아": "오세아니아", "타히티": "오세아니아", "미국령 사모아": "오세아니아",
}

def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def regional_cup_format(n_teams: int) -> dict:
    """[2026-08 신설] 지역컵 참가국 수만 보고 조 편성+토너먼트 브래킷을
    자동으로 정한다 — 지역마다 규모가 7~23개국으로 크게 다른데(EAFF 9개국
    ~카리브 23개국), 실제 대회마다 예선/본선 방식이 계속 바뀌는 걸 그대로
    따라가면 유지보수가 안 되므로 "규모 기반 표준 포맷" 하나로 통일한다.
    핵심 원칙: 그 지역 국가는 전원 조별리그부터 바로 참가(예선으로 잘라내지
    않음) — 명성·성장 기회를 지역 전체가 나눠 갖는 게 목적이므로.

    반환: {"n_groups", "group_sizes"(리스트), "advance_per_group",
           "bracket_size", "best_thirds"(브래킷을 채우기 위해 필요한
           3위 중 상위 팀 수 — CONT_BEST_THIRDS와 동일한 개념)}
    """
    if n_teams <= 4:
        return {"n_groups": 0, "group_sizes": [], "advance_per_group": 0,
                "bracket_size": _next_pow2(max(2, n_teams)), "best_thirds": 0}

    def _is_safe(n_groups):
        direct = n_groups * 2
        gap = _next_pow2(direct) - direct
        return gap <= n_groups

    # [2026-08 재조정, 신민용 확정: "3·4·5의 배수로 나누는 게 토너먼트로
    # 넘어가기 깔끔하다 — 우선순위: 5팀×N조 → 4팀×N조 → 3팀×N조"] 처음
    # round(n/3.5) 방식은 "가장 가까운 안전한 조 수"만 찾았지, 조 인원이
    # 균일한지는 안 봤다(예: AFF 10개국이 [4,4,3]으로 갈라져서 "5팀씩
    # 2개조"가 될 수 있었는데도 안 그랬음). 이제 나누어떨어지는 조합을
    # 5→4→3 순서로 먼저 찾고, 없으면(예: 7·13·17개국처럼 어느 것으로도
    # 안 나누어떨어짐) 기존 "가장 가까운 안전한 값" 방식으로 폴백한다.
    for group_size in (5, 4, 3):
        if n_teams % group_size == 0:
            n_groups = n_teams // group_size
            if _is_safe(n_groups):
                group_sizes = [group_size] * n_groups
                advance_per_group = 2
                direct_advance = n_groups * advance_per_group
                bracket_size = _next_pow2(direct_advance)
                best_thirds = max(0, bracket_size - direct_advance)
                return {"n_groups": n_groups, "group_sizes": group_sizes,
                        "advance_per_group": advance_per_group,
                        "bracket_size": bracket_size, "best_thirds": best_thirds}

    # 폴백: 어느 그룹 크기로도 안 나누어떨어지면 "가장 가까운 안전한 조
    # 수"로 들쭉날쭉하게(3~5명 섞어서) 채운다 — 예전 알고리즘과 동일.
    n_groups_est = max(2, round(n_teams / 3.5))
    if _is_safe(n_groups_est):
        n_groups = n_groups_est
    else:
        n_groups = min((c for c in range(2, 13) if _is_safe(c)),
                        key=lambda c: abs(c - n_groups_est))
    base, extra = divmod(n_teams, n_groups)
    group_sizes = [base + 1 if i < extra else base for i in range(n_groups)]
    advance_per_group = 2
    direct_advance = n_groups * advance_per_group
    bracket_size = _next_pow2(direct_advance)
    best_thirds = max(0, bracket_size - direct_advance)
    return {"n_groups": n_groups, "group_sizes": group_sizes,
            "advance_per_group": advance_per_group,
            "bracket_size": bracket_size, "best_thirds": best_thirds}

# ── 국제대회 본선 설정 ──────────────────────────────
WC_TEAMS   = 32   # 월드컵 본선 32개국 (8조 × 4팀)
WC_GROUPS  = 8
CONT_TEAMS = 24   # 대륙컵 본선 24개국 (6조 × 4팀)
CONT_GROUPS = 6
# 24개국 포맷: 각 조 1·2위(12팀) + 성적 좋은 3위 중 상위 4팀 = 16강
CONT_BEST_THIRDS = 4

# ══════════════════════════════════════════════════════════════
# 월드컵 예선 대륙 그룹 (5개 통합 연맹)
# ══════════════════════════════════════════════════════════════
# [2026-09 개편, 신민용 확정: "월드컵/챔스에서 남미 북미를 나눌려고 해"]
# 기존엔 "아메리카"(남미+북미 통합) 1개 연맹이었으나, 남미(CONMEBOL 12개국)와
# 북미(CONCACAF 33개국)를 각자 독립 연맹으로 분리한다 — 국가별 대륙 데이터
# (data/countries.py)에는 원래부터 "남미"/"북미"가 따로 있었는데, 예선/
# 대회 편성 단계에서만 CONFEDERATIONS가 이 둘을 하나로 합쳐왔던 것.
# 오세아니아는 그대로 아시아에 붙는다(변경 없음).
# 연맹 대표키 → 소속 대륙 목록
CONFEDERATIONS = {
    "유럽":     ["유럽"],
    "남미":     ["남미"],
    "북미":     ["북미", "북중미"],
    "아시아":   ["아시아", "오세아니아"],
    "아프리카": ["아프리카"],
    # [2026-09 정정] 대륙컵(네이션스컵) 티어 전용 — 남미+북미를 합친
    # 가상의 연맹키. 월드컵 예선(WC_QUOTA/WC_QUAL_*)이나 클럽대항전은
    # 절대 이 키를 쓰지 않고 위의 "남미"/"북미" 개별 키를 그대로 쓴다.
    "아메리카": ["남미", "북미", "북중미"],
}
# 개별 대륙 → 연맹 대표키 (역방향 조회)
CONTINENT_TO_CONF = {
    "유럽":     "유럽",
    "남미":     "남미",
    "북미":     "북미",
    "북중미":   "북미",
    "아시아":   "아시아",
    "오세아니아":"아시아",
    "아프리카": "아프리카",
}

# [2026-09 정정, 신민용: "네이션스컵은 북남미 합쳐서 하는걸로... 이건
# 원래처럼 북미 남미 합쳐서 하는거고"] 처음엔 대륙컵(CONF 티어,
# 2004,08..주기) "네이션스컵"도 월드컵 예선/챔스처럼 남미·북미로 쪼갰는데,
# 이건 잘못 이해한 것이었다 — 이 티어는 원래 설계대로 남미+북미를
# "아메리카" 하나로 합쳐서 연다(월드컵 예선(WC_QUOTA 등)과 클럽대항전
# (champions_engine)만 남미/북미로 분리된 채 유지되고, 이 대륙컵 티어만
# 다시 합침). 그래서 CONFEDERATIONS에 남미/북미 개별 키와 별도로
# "아메리카"(둘을 합친) 키를 추가해뒀고, 이 대륙컵 티어를 만드는 루프
# (intl_engine.start_qualifying_if_needed의 all_confs)는 "아메리카"를 쓴다.
CONF_CUP_NAME = {
    "유럽":     "유럽 네이션스컵",
    "아메리카": "남북미 대륙컵",
    "아시아":   "아시안컵",
    "아프리카": "아프리카 네이션스컵",
    # 하위호환(개별 남미/북미로 조회되는 경우 대비)
    "남미": "남북미 대륙컵", "북미": "남북미 대륙컵", "북중미": "남북미 대륙컵",
    "오세아니아": "아시안컵",
}

# ══════════════════════════════════════════════════════════════
# 32팀 본선 대륙별 쿼터 (합 32)
# ══════════════════════════════════════════════════════════════
# [2026-09 개편] "아메리카"(8장) → 남미 5 + 북미 3으로 분리. 남미가 국가
# 수(12개국)는 훨씬 적어도 전력이 더 세다는 기존 설계 취지를 그대로 반영해
# 북미(33개국)보다 많이 가져간다. 합계 8은 그대로 유지(WC_QUOTA 총합 32 불변).
WC_QUOTA = {"유럽": 13, "남미": 5, "북미": 3, "아시아": 5, "아프리카": 6}

# 예선 세부 구조 (32팀 체제)
# cutoff_bottom: 하위 N개국 예선 진출 실패 (경기 없이 성적만 기록)
# pool = 전체 - cutoff_bottom → n_groups × group_size 로 딱 떨어져야 함
# direct: 조 1위 중 바로 본선 직행 수 (0이면 전원 플레이오프)
# po_teams: 플레이오프 참가 팀 수 (조 1위들)
# po_winners: 플레이오프 통과 팀 수
# wildcard: 조 2위 중 성적순 와일드카드 수
# [2026-08 신설, 신민용 확정: "유로(EURO)는 다른 지역컵과 달리 여름
# 예선 → 겨울 본선 구조로"] 대륙컵 예선(cont_qual)은 예전엔 있었다가
# "실제 경기 없이 랜덤 노이즈로 이변 느낌만 내자"는 이유로 폐지됐던
# 기능인데(intl_engine.py 상단 주석 참고), 유럽만 다시 되살린다 —
# 실제로 커리어 안에서 예선 A매치를 뛰는 경험 자체가 목적이라 랜덤
# 대체재로는 안 되기 때문. 월드컵 유럽 예선(WC_QUAL_32)과 완전히
# 같은 조 편성(54개국-6컷오프=48개국→12조×4팀)을 그대로 재사용하고,
# "direct"만 24로 바꿔서(조 1·2위 전부 직행) 딱 유로 본선 규모(24개국)에
# 맞춘다 — 플레이오프가 필요 없어서 po_teams/po_winners는 0.
EURO_QUAL = {
    "유럽": {
        "cutoff_bottom": 6,     # 54 - 6 = 48개국 → 12조×4팀 (WC 예선과 동일)
        "n_groups": 12, "group_size": 4,
        # [2026-08 버그수정] "direct"는 조 1위만 채우는 필드였다(실측:
        # direct=24로 두면 winners 리스트가 12개뿐이라 12개만 통과) —
        # 2위는 별도 wildcard 필드로 넣어야 해서, direct=12(1위 전원)
        # + wildcard=12(2위 전원, 12개 조라 정확히 다 들어감)로 분리했다.
        "direct": 12,           # 조 1위 12팀 전원 직행
        "po_teams": 0, "po_winners": 0,
        "wildcard": 12,         # 조 2위 12팀 전원 추가 (12+12=24, 본선 규모와 일치)
        "quota": 24,
    },
    # [2026-09 신설, 신민용 확정: "북미 지역컵 2개(UNCAF/카리브)를 합쳐서
    # 골드컵으로, 예선전을 거쳐 본선에 오르는 원리로"] 골드컵도 유로와
    # 완전히 같은 메커니즘(cont_qual 실제 예선 → 대륙컵 형식 본선)을 쓴다.
    # 북미 33개국(countries.py "북미" continent 기준)을 9개국 컷오프해
    # 24개국 → 12조×2팀(양방향 2경기, 실질적으로
    # 홈/원정 2연전 플레이오프)으로 나눠 조 1위 12팀 + 조 2위 12팀 =
    # 24개국이 본선(CONT_TEAMS=24, 유로/아시안컵/AFCON과 동일 포맷)에
    # 오른다. 코파 아메리카(région-tier, REGION_LIST)보다 격이 낮다는
    # 설정을 반영해 가중치는 별도로 낮게 잡는다(power_ranking.py
    # COUNTRY_TIER_WEIGHT["gold_cup"] 참고).
    "북미": {
        "cutoff_bottom": 9,     # 33 - 9 = 24개국 → 12조×2팀
        "n_groups": 12, "group_size": 2,
        "direct": 12,           # 조 1위 12팀 전원 직행
        "po_teams": 0, "po_winners": 0,
        "wildcard": 12,         # 조 2위 12팀 전원 추가 (12+12=24, 본선 규모와 일치)
        "quota": 24,
    },
}

# ══════════════════════════════════════════════════════════════
# [2026-09 전면 재설계, 신민용 리포트: "월드컵 예선전이 현실보다 가혹함"]
# 기존 구조는 북미/아시아/아프리카에서 "조 1위 전원"이 시드 없는 완전
# 무작위(random.shuffle) 단판(승부차기 포함) 플레이오프 대상이 되어, 자기
# 조 최강팀도 정확히 절반이 동전던지기 한 판으로 탈락했다 — 실제 FIFA
# 예선은 조 1위가 이렇게 쉽게 떨어지지 않는다(직행이거나, 플레이오프가
# 있어도 다리 수가 많거나 애매한 순위만 대상).
#
# [재설계, 신민용+ChatGPT 설계 확정] "1차 조별리그(4팀, 편도 3경기)→상위
# 2팀→2차 조별리그(4팀, 편도 3경기, 1차 1위/2위 포트로 재편성)→직행/
# 와일드카드/(필요시)PO" 2단계 구조로 교체. 예선 창(4주=28일, 169~196일)
# 안에서 매치데이 수가 기존(왕복6+PO1=7)과 정확히 동일(편도3+편도3+PO1=7)
# 해서 캘린더 변경이 전혀 필요 없다 — 169/173/177(1차)/181/185/189(2차)/
# 193(PO) 그대로. 유럽·남미는 참가국이 적거나 티켓이 많아 기존처럼
# 단일단계(조별리그 왕복6경기)를 그대로 유지한다(단, 유럽 플레이오프는
# 이제 완전 무작위 대신 시드 배정 — 상위 시드가 하위 시드와 붙는다).
#
# qual_cfg 스키마(대륙 하나당 dict):
#   cutoff_bottom, n_groups, group_size, legs(1=편도/2=왕복) — 1차(단일단계면
#   유일한) 조별리그 구성
#   "stage2" 키가 있으면 2단계 체제: stage2 안에 그 단계의
#     n_groups/direct_top/wildcard_rank/wildcard_count/po_* 를 담는다.
#     advance_per_group1(기본 2)만큼 1차 각 조 상위팀이 2차로 진출.
#   "stage2"가 없으면 단일단계: direct_top(매 조 상위 K팀이 조간비교 없이
#     그대로 직행)/wildcard_rank+wildcard_count(특정 순위끼리 조간 성적
#     비교해 상위 N팀 추가 직행)/po_teams+po_winners+po_pool_rank+po_seeded
#     (특정 순위끼리 조간비교해 상위 po_teams팀이 PO, po_seeded=True면
#     강한 시드가 약한 시드와 붙게 페어링, False면 기존처럼 무작위)
#   quota — 이 대륙 최종 티켓 수(검증용, 항상 direct_top/wildcard_count/
#     po_winners 합과 일치해야 함)
# ══════════════════════════════════════════════════════════════

WC_QUAL_32 = {
    "유럽": {
        "cutoff_bottom": 6, "n_groups": 12, "group_size": 4, "legs": 2,
        "direct_top": 1,                      # 조 1위 12팀 직행
        "wildcard_rank": 0, "wildcard_count": 0,
        "po_teams": 2, "po_winners": 1, "po_pool_rank": 2, "po_seeded": True,
        "quota": 13,
    },
    "남미": {
        "cutoff_bottom": 0, "n_groups": 3, "group_size": 4, "legs": 2,
        "direct_top": 1,                      # 조 1위 3팀 직행
        "wildcard_rank": 2, "wildcard_count": 2,   # 조 2위 중 상위 2팀
        "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        "quota": 5,
    },
    "북미": {
        "cutoff_bottom": 9, "n_groups": 6, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 3,                     # 1차 6조 top2=12팀 → 2차 3조×4
            "direct_top": 1,                   # 2차 조 1위 3팀 직행
            "wildcard_rank": 0, "wildcard_count": 0,
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 3,
    },
    "아시아": {
        "cutoff_bottom": 18, "n_groups": 10, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 5,                     # 1차 10조 top2=20팀 → 2차 5조×4
            "direct_top": 1,                   # 2차 조 1위 5팀 직행
            "wildcard_rank": 0, "wildcard_count": 0,
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 5,
    },
    "아프리카": {
        "cutoff_bottom": 6, "n_groups": 12, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 6,                     # 1차 12조 top2=24팀 → 2차 6조×4
            "direct_top": 1,                   # 2차 조 1위 6팀 직행
            "wildcard_rank": 0, "wildcard_count": 0,
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 6,
    },
}

# ══════════════════════════════════════════════════════════════
# 48팀 본선 대륙별 쿼터 (합 48)
# ══════════════════════════════════════════════════════════════
WC_EXPAND_YEAR     = 2022 # 월드컵 32강 날짜
WC_TEAMS_BIG       = 48
WC_GROUPS_BIG      = 12
WC_BEST_THIRDS_BIG = 8
# [2026-09 개편] "아메리카"(13장) → 남미 7 + 북미 6으로 분리(합계 불변).
WC_QUOTA_BIG = {"유럽": 16, "남미": 7, "북미": 6, "아시아": 10, "아프리카": 9}

# 예선 세부 구조 (48팀 체제) — 위 WC_QUAL_32와 동일한 신규 스키마
WC_QUAL_48 = {
    "유럽": {
        "cutoff_bottom": 6, "n_groups": 12, "group_size": 4, "legs": 2,
        "direct_top": 1,                      # 조 1위 12팀 직행
        "wildcard_rank": 0, "wildcard_count": 0,
        "po_teams": 8, "po_winners": 4, "po_pool_rank": 2, "po_seeded": True,
        "quota": 16,
    },
    "남미": {
        "cutoff_bottom": 0, "n_groups": 3, "group_size": 4, "legs": 2,
        "direct_top": 1,                      # 조 1위 3팀 직행
        "wildcard_rank": 2, "wildcard_count": 3,   # 조 2위 3팀 전원
        "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        "quota": 6,
    },
    "북미": {
        "cutoff_bottom": 1, "n_groups": 8, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 4,                     # 1차 8조 top2=16팀 → 2차 4조×4
            "direct_top": 1,                   # 2차 조 1위 4팀 직행
            "wildcard_rank": 2, "wildcard_count": 3,   # 2차 조 2위 중 상위 3팀
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 7,
    },
    "아시아": {
        "cutoff_bottom": 18, "n_groups": 10, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 5,                     # 1차 10조 top2=20팀 → 2차 5조×4
            "direct_top": 2,                   # 2차 조 1·2위 전원 직행(5×2=10)
            "wildcard_rank": 0, "wildcard_count": 0,
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 10,
    },
    "아프리카": {
        "cutoff_bottom": 6, "n_groups": 12, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 6,                     # 1차 12조 top2=24팀 → 2차 6조×4
            "direct_top": 1,                   # 2차 조 1위 6팀 직행
            "wildcard_rank": 2, "wildcard_count": 3,   # 2차 조 2위 중 상위 3팀
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 9,
    },
}

# ══════════════════════════════════════════════════════════════
# [2026-09 신설, 신민용: "64팀 본선 — 나중에 켤 것"] 언제든 켤 수 있게
# 미리 설계·구현만 해두고, 실제 활성화는 WC_EXPAND_YEAR_64에 연도를
# 넣기 전까지 절대 발동하지 않는다(None이면 완전 비활성 — wc_tier()가
# 어떤 연도를 넣어도 32/48만 반환). 활성화하려면 이 상수 하나만 실제
# 연도(int)로 바꾸면 그 해부터 자동으로 64팀 체제로 전환된다.
# ══════════════════════════════════════════════════════════════
WC_EXPAND_YEAR_64 = None   # 예: 2038 로 바꾸면 그 해부터 64팀 활성화. None=비활성
WC_TEAMS_64  = 64
WC_GROUPS_64 = 16   # 16조×4팀 → 조 1·2위(32팀)가 정확히 32강 브래킷과 일치
# best-3rd 구제가 필요 없음(48팀 체제와 달리 조/팀 수가 딱 떨어짐)
WC_QUOTA_64 = {"유럽": 22, "남미": 8, "북미": 9, "아시아": 13, "아프리카": 12}

WC_QUAL_64 = {
    "유럽": {
        "cutoff_bottom": 6, "n_groups": 12, "group_size": 4, "legs": 2,
        "direct_top": 1,                      # 조 1위 12팀 직행
        "wildcard_rank": 2, "wildcard_count": 8,   # 조 2위 중 상위 8팀 직행
        "po_teams": 4, "po_winners": 2, "po_pool_rank": 2, "po_seeded": True,
        "quota": 22,                          # 12+8+2=22
    },
    # [특수 케이스] 남미는 64팀 체제에서 8/12장(2/3)이나 줘야 해서 4팀 조
    # 방식이 안 맞는다 — 12개국을 6팀씩 2개조로 나눠(편도 5경기) 각 조
    # 1~4위(상위 4팀)가 그대로 직행. 조별비교/와일드카드/PO 전부 불필요.
    "남미": {
        "cutoff_bottom": 0, "n_groups": 2, "group_size": 6, "legs": 1,
        "direct_top": 4,                      # 매 조 1~4위 직행(조간비교 없음)
        "wildcard_rank": 0, "wildcard_count": 0,
        "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        "quota": 8,                           # 2조×4=8
    },
    "북미": {
        "cutoff_bottom": 1, "n_groups": 8, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 4,                     # 1차 8조 top2=16팀 → 2차 4조×4
            "direct_top": 2,                   # 2차 조 1·2위 전원 직행(4×2=8)
            "wildcard_rank": 3, "wildcard_count": 1,   # 2차 조 3위 중 상위 1팀
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 9,                           # 8+1=9
    },
    "아시아": {
        "cutoff_bottom": 18, "n_groups": 10, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 5,                     # 1차 10조 top2=20팀 → 2차 5조×4
            "direct_top": 2,                   # 2차 조 1·2위 전원 직행(5×2=10)
            "wildcard_rank": 3, "wildcard_count": 3,   # 2차 조 3위 중 상위 3팀
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 13,                          # 10+3=13
    },
    "아프리카": {
        "cutoff_bottom": 6, "n_groups": 12, "group_size": 4, "legs": 1,
        "advance_per_group1": 2,
        "stage2": {
            "n_groups": 6,                     # 1차 12조 top2=24팀 → 2차 6조×4
            "direct_top": 2,                   # 2차 조 1·2위 전원 직행(6×2=12)
            "wildcard_rank": 0, "wildcard_count": 0,
            "po_teams": 0, "po_winners": 0, "po_pool_rank": 0, "po_seeded": False,
        },
        "quota": 12,                          # 6×2=12
    },
}


def wc_tier(year: int) -> int:
    """해당 연도의 월드컵 체제(32/48/64)를 반환. WC_EXPAND_YEAR_64가
    None(기본값, 비활성)이면 64는 절대 반환하지 않는다 — 64팀 인프라가
    코드에 전부 구현되어 있어도, 이 값을 실제 연도로 바꾸기 전까지는
    아무 것도 바뀌지 않는다(기존 32→48 전환과 완전히 동일한 방식)."""
    if WC_EXPAND_YEAR_64 is not None and year >= WC_EXPAND_YEAR_64:
        return 64
    if year >= WC_EXPAND_YEAR:
        return 48
    return 32


WC_TEAMS_BY_TIER  = {32: WC_TEAMS, 48: WC_TEAMS_BIG, 64: WC_TEAMS_64}
WC_GROUPS_BY_TIER = {32: WC_GROUPS, 48: WC_GROUPS_BIG, 64: WC_GROUPS_64}
WC_QUOTA_BY_TIER  = {32: WC_QUOTA, 48: WC_QUOTA_BIG, 64: WC_QUOTA_64}
WC_QUAL_BY_TIER   = {32: WC_QUAL_32, 48: WC_QUAL_48, 64: WC_QUAL_64}
WC_BEST_THIRDS_BY_TIER = {32: 0, 48: WC_BEST_THIRDS_BIG, 64: 0}
WC_SCHEDULE_KEY_BY_TIER = {32: "world_cup_32", 48: "world_cup_48", 64: "world_cup_64"}

# 국가 등급 → 대표팀 전력(OVR) / 예선 기본 점수
# [버그수정 2026-07, 신민용 지적] "SS"(잉글랜드/EPL) 키가 이 4개 표에
# 전부 빠져 있었다 — .get(grade, 기본값) 폴백 때문에 에러 없이 조용히
# 최약체 취급됐다(OVR 45, 예선기준 0.2로 F급보다도 낮음). 클럽 쪽은
# SS가 최상위 등급(OVR_RANGES SS tier1=90~100)인데 국가대표는 정반대로
# 가장 약한 취급을 받고 있었던 것. 아래 값은 OVR_RANGES 상단 주석의
# "tier1 목표 avg: SS=93/S=90/A(유럽)=86/A(아시아)=82/B=75/C=65"를
# 국가대표 기준선으로 그대로 반영(A는 유럽/아시아 평균인 84로 절충).
# [2026-07 재조정, 신민용 지적: "월드컵 등 국제대회 기준 최상위 팀 OVR이
# 너무 낮다 — 각 리그 월드클래스/엘리트가 모인 스쿼드인데 90은 기본으로
# 넘겨야 한다"] S급(FIFA랭크 1~10위 — 아르헨티나/프랑스/스페인/잉글랜드/
# 브라질/독일 등)이 기존 90 base에 ±3 랜덤 노이즈를 그대로 맞아서 87까지도
# 자주 떨어졌다. base를 93으로 올리고, 아래 _nat_team_ovr()의 노이즈 폭도
# ±3→±2로 좁혀서 "월클 스쿼드가 랜덤하게 90 밑으로 자주 떨어지는" 문제를
# 줄인다(대륙보정이 있는 나라는 여전히 그 보정만큼 낮게 나올 수 있음 —
# 예: 모로코처럼 아프리카 대륙보정 -4가 걸린 S급은 90 근처에서 왔다갔다
# 하는 게 오히려 자연스러움).
GRADE_TEAM_OVR  = {"SS": 93, "S": 93, "A": 84, "B": 75, "C": 65, "D": 58, "E": 51, "F": 45}

# ══════════════════════════════════════════════════════════════
# [동적 팀 강도(club_strength)] 2026-08 신설, 신민용 확정
#   "명문팀은 강한 team_strength를 뽑을 확률이 훨씬 높다"(prestige_clubs.py)
#   는 리그 내 스쿼드 OVR 배정 단계의 정적 보정이었다. 여기서는 별도로,
#   시즌 성적이 다음 시즌 경기력에 누적 반영되는 값(teams.club_strength)을
#   둔다. 매치 계산에서만 쓰이고(_team_avg_ovr), 화면에 보이는 개별 선수
#   OVR·스쿼드 구성은 전혀 건드리지 않는다.
#
#   설계 의도: "우승 → 선수단 안정/재정 확충 → 다음 시즌도 강함"이라는
#   선순환과, 반대로 "장기 부진 → 서서히 평범해짐"이라는 하방 모두를
#   재현한다. 정적 명문팀 리스트(PRESTIGE_TEAMS)는 완전히 없애지 않고
#   "초기 시드값"으로만 남겨(new_game 시 1회성 시딩), 이후로는 순수하게
#   그 세이브 안에서의 실제 성적이 위상을 결정하게 한다.
#
#   최종 순위(승점 → 득실차 순)를 기준으로 리그 규모와 무관하게 "상위
#   몇 %/하위 몇 %"로 판정한다 — 참가팀 수가 12팀이든 24팀이든 같은
#   체감 난이도가 되도록.
# [2026-08 재조정, 신민용 확정: "강릉시민축구단(4부 출신) 같은 무명팀이
# 4~5시즌 연속 우승만 하면 그대로 왕조가 되는 버그" 수정 세트 ①] 우승
# 델타 +4.0은 18팀 리그 1위라고 해서 다음 시즌 선수단 자체가 갑자기 확
# 강해지는 게 아닌데 너무 컸다 — 델타 테이블 전체를 완만하게 낮춘다.
CLUB_STRENGTH_DELTA_BY_RANK_PCT = [
    # (순위 백분율 상한, 델타)  — 순위/참가팀수 <= pct 인 첫 항목 적용
    (1/18,  1.5),   # 우승(18팀 리그 기준 1위) 수준
    (3/18,  1.0),   # 2~3위 수준(챔스권 상단)
    (6/18,  0.3),   # 4~6위 수준(유로파권)
    (10/18, 0.0),   # 중위권 — 변화 없음(감쇠만 적용)
    (14/18, -0.5),  # 하위권
    (1.0,  -1.0),   # 강등권
]
# [2026-08 재조정, 신민용 확정: 왕조 스노우볼 버그 수정 ②] "우승 → +4 →
# 누적 → 상한 12"가 감쇠 0.85로는 단 4시즌 만에 사실상 영구 고정값에
# 도달해버리는 게 핵심 원인이었다(x=0.85x+4.0의 수렴값 26.7 >> 상한
# 12.0). 감쇠를 0.70으로 강하게 낮춰 "몇 시즌 반짝"의 효과가 훨씬 빨리
# 정상으로 되돌아오게 하고, 상/하한도 -10~+12(총 22점, 실측 스쿼드 OVR
# 격차 4~5점의 4배 이상)에서 -4.0~+4.0(대칭)으로 대폭 축소한다 —
# club_strength는 이제 "장기 전력"이 아니라 "최근 몇 시즌 폼" 수준의
# 보조 신호로만 기능한다.
CLUB_STRENGTH_DECAY = 0.70
CLUB_STRENGTH_MIN = -4.0
CLUB_STRENGTH_MAX = 4.0

# [2026-08 버그수정, 신민용 리포트: "명문팀이 한 번 나쁜 시즌을 겪으면
# 다시는 2위 안에도 못 든다 — 작년 성적이 계속 유리하게 가는 보정이
# 있는 것 같다"] 실측 확인: 분데스리가 1부 팀들의 순수 스쿼드 OVR
# 격차는 88.6~93.2(약 4.6점)뿐인데, club_strength는 -10~+12(총 22점)
# 스윙을 만들어서 매치 시뮬레이션(_team_avg_ovr)에 그대로 더해지고
# 있었다 — 그 결과 무명 하위팀(카를스루에 SC, 로트바이스 에르푸르트/
# 오버하우젠 등)이 최근 몇 시즌 성적만으로 club_strength가 상한에
# 붙어 실효 강도 103~104까지 올라가고, 반대로 바이에른 뮌헨(98.0)/
# 레버쿠젠(93.2)은 그보다 낮게 나왔다 — 스쿼드 실력이 아니라 최근
# 몇 시즌 성적(club_strength, 그 자체가 최근 성적으로 갱신되는 값)이
# 경기 결과를 지배하는 자기강화 루프가 만들어진 것. club_strength
# 자체의 상/하한(위 MIN/MAX)은 부전승 방어선(CLUB_STRENGTH_RELEGATION_
# FLOOR)이나 신인 OVR 보정(CLUB_STRENGTH_OVR_BONUS_*) 등 다른 용도에도
# 쓰이므로 그대로 두고, "매치 시뮬레이션에 실제로 더해지는 양"만 이
# 가중치로 줄인다 — 순수 스쿼드 격차(약 4~5점)와 비슷한 수준(최대
# ±3점 안팎)이 되도록, 있으나 스쿼드 실력을 뒤엎지는 못하는 보조
# 요인 정도로 낮춘다.
# [2026-08 2차 재조정, 신민용 확정: 왕조 스노우볼 버그 수정 ③] 그런데도
# 옛 MAX(12.0) 기준 최대 +3.0은 여전히 스쿼드 격차(4~5점)의 절반 이상이라
# 무명팀이 몇 시즌 상위권만 찍어도 명문급 매치 보정을 받는 문제가
# 계속됐다. MAX를 4.0으로 낮춘 것과 함께 가중치도 0.10으로 더 낮춰
# 최대 매치 영향력을 ±0.4(4.0×0.10)까지 줄인다 — 이변은 만들되 왕조는
# 못 만드는 수준.
CLUB_STRENGTH_MATCH_WEIGHT = 0.10

# [2026-08 신설, 신민용 설계 확정: "명문팀 우승 비율이 너무 낮다 —
# 잉글랜드 20%/스페인 15%/이탈리아 15%/독일 10%/프랑스 15%인데, 목표는
# 잉글랜드 55~70%/스페인 70~85%/이탈리아 60~75%/독일 65~85%/프랑스
# 60~75%"] club_strength(동적, 실적 기반)만으로는 등급별 우승 비율
# 목표에 크게 못 미쳤다 — 실측(20시즌): 독일 2/20(10%), 스페인 3/20(15%)
# 등 현실의 "특정 최상위권 지배력이 강한 리그" 느낌이 전혀 안 났다.
# CLUB_STRENGTH_MATCH_WEIGHT로 club_strength의 매치 영향력을 줄인 것과
# 별개로, 명문 등급 자체가 주는 "제도적 우위"(선수단 깊이·이적 예산·
# 코칭 인프라 등 시즌 성적과 무관하게 꾸준한 요소)를 매치 강도에 직접
# 더한다 — 예전에 있다가 club_strength로 대체되며 사라진 정적
# PRESTIGE_MATCH_BONUS(8.0, 전체 명문팀 동일)를 등급별로 차등 부활시킨
# 것. 3급(레알/바이에른급)이 가장 크고 2급, 1급 순으로 작아진다 —
# club_strength(변동)와 이 보너스(고정)가 합산되어 매치에 반영된다.
# [잠정값 — 실측 후 조정 예정]
# [2026-08 1차 조정] 6/4/2로 20시즌 실측한 결과: 독일(3급 팀이 바이에른
# 하나뿐)은 70%로 목표(65~85%)에 정확히 들어맞았는데, 잉글랜드/스페인/
# 이탈리아/프랑스는 90~100%로 심하게 오버슈팅했다 — 원인은 그 나라들이
# 3급 팀을 "여러 개"(잉글랜드 3팀, 스페인 2팀, 이탈리아 3팀) 갖고 있어서,
# 같은 보너스를 받는 강팀들끼리 우승을 나눠 가지며 "명문팀 전체 우승
# 비율"이 거의 다 쓸어가 버린 것 — 개별 팀 우승 확률은 그대로여도
# "3급 팀 중 누군가"가 이길 확률은 그 수만큼 곱으로 커진다. 독일처럼
# 3급이 1팀뿐인 나라에 맞춘 값을 그대로 다른 나라에 적용하면 항상
# 과대해진다. 보너스 자체를 낮춰서 재조정한다.
# [2026-08 3차 재조정, 신민용 확정: 왕조 스노우볼 버그 수정 ⑤] club_strength
# 쪽 매치 영향력을 12×0.25=3.0 → 4×0.10=0.4로 대폭 줄이면서, 감쇠 없이
# 세이브 내내 고정으로 붙는 이 값(+4.0/+2.5/+1.2)이 상대적으로 훨씬 더
# 커져 "뮌헨은 경기 시작부터 +4" 식의 결정론적 우위가 오히려 두드러지게
# 됐다. PRESTIGE_WEIGHT_BY_LEVEL(선수단이 강하게 구성될 확률)은 그대로
# 두고, 이미 구성된 선수단에 매 경기 직접 더해지는 이 고정 보너스만
# club_strength와 같은 비율로 축소한다 — "명문이라 좋은 선수단을 가질
# 확률이 높다"는 유지하되 "명문이라 무조건 매 경기 +4"는 없앤다.
# [2026-09 재조정 완료, 신민용 확정: "발롱도르 2002 breakdown까지 다시
# 보니 이 값은 실제로 버그가 아니라 왕조 스노우볼 수정(club_strength
# 매치 가중치 12×0.25=3.0→4×0.10=0.4 축소) 때 같이 5배 과대 축소된
# 것으로 보인다 — club_strength는 그대로 두고 이 정적 보너스만 독립적으로
# 재조정하자"] club_strength/OVR 생성 로직은 전혀 안 건드리고 이 상수만
# 바꿔가며, 새 세이브(1년차부터, 프레스티지 스쿼드 우위가 처음부터
# 누적되도록)에서 독일(레벨3 단일 클럽 — 바이에른 뮌헨) 우승률을 실제
# 경기 시뮬레이션으로 측정해 반복 조정했다.
#   - 3.0/2.0/1.0 테스트(15시즌): 바이에른 3/15=20% — 목표(65~85%) 크게
#     미달. 원인: club_strength가 이미 상한(±4.0)에서 포화돼 명문팀
#     간에도 차이를 못 만들고, OVR 생성 자체도 "격차 압축"(ace_lo, 팀간
#     최대 4~5점) 설계라 정적 보너스 혼자 이 간극을 못 메꿈.
#   - 5.0/3.3/1.7 테스트(같은 세이브 이어서 11시즌): 바이에른 7/11=63.6%,
#     최근 6시즌만 보면 5/6=83% — 스쿼드 우위가 누적되며 목표 구간에
#     안착하는 추세 확인. 최종값으로 채택.
# 잉글랜드/스페인/이탈리아/프랑스처럼 레벨3 클럽이 여러 개인 나라는
# "그 나라 레벨3 클럽 중 누군가 우승"할 확률이 곱으로 커지므로(2818줄
# 1차 조정 히스토리 참고) 독일보다 낮은 목표 구간(각 리그 문서 목표치)을
# 그대로 유지 — 이번 조정은 절대값만 올렸을 뿐 나라별 상대 배율은
# 안 건드렸으므로 그 목표들도 비례해서 같이 올라간다.
# [확인] 2002 발롱도르 회귀 기준(T72 28.03 vs X4Q 27.38)은 이 상수와
# 완전히 무관한 별도 파이프라인(_score_ballon_candidate은 트로피 점수를
# _get_team_trophy_bonus/_get_player_intl_bonus로만 계산 — 둘 다
# PRESTIGE_LEVEL_MATCH_BONUS를 참조하지 않음)이라 이 값 변경으로 흔들리지
# 않는다.
PRESTIGE_LEVEL_MATCH_BONUS = {3: 5.0, 2: 3.3, 1: 1.7}
# new_game 시 정적 명문팀 리스트를 초기 시드로만 환산할 때 쓰는 등급별
# 시작값(PRESTIGE_WEIGHT_BY_LEVEL 3/2/1단계에 대응, data/prestige_clubs.py
# 참고). 이후로는 이 초기값도 매 시즌 CLUB_STRENGTH_DECAY로 서서히
# 깎이므로, 실적이 안 따라주는 명문팀은 몇 시즌 안에 평범해진다.
CLUB_STRENGTH_SEED_BY_PRESTIGE_LEVEL = {3: 6.0, 2: 4.0, 1: 2.0}

# [2026-08 신설, 신민용 확정: "club_strength가 경기력엔 반영되는데 정작
# 선수단(_retire_and_replace 신인 OVR)에는 안 이어진다"] 실측(진단 로그)
# 확인 결과: 아스널은 club_strength가 +5.48→+8.80으로 거의 2배 회복됐는데
# 정작 스쿼드 평균 OVR은 89.0→88.0으로 오히려 소폭 하락했다 — club_strength
# (경기 승률/강등 방어에만 쓰임)와 실제 선수 생성이 완전히 단절돼 있었기
# 때문. 이제 _retire_and_replace()의 신인 목표 OVR에 club_strength 기반
# 보정을 더한다: 보정 = clamp(club_strength * K, MIN, MAX).
#   - K=0.3 기준: club_strength +10 → +3.0 / -10 → -3.0 정도로, 티어 하나당
#     감쇠폭(STEP=8)을 완전히 뒤집진 않으면서도 실제 영향을 준다.
#   - MIN/MAX로 상하한을 걸어 "명문이라 무한 보호"나 "몰락이라 무한 악화"를
#     막는다 — 하한을 상한보다 살짝 좁게 잡은 이유: 이미 존재하는
#     PRESTIGE_LEVEL_OVR_BONUS(양수 전용, +1.2~+1.8)와 중첩되므로, 상한
#     쪽은 그 위에 얹혀도 과하지 않게, 하한 쪽은 몰락한 팀도 완전히
#     리그 바닥으로 꺼지지만은 않게 비대칭으로 뒀다.
#   - 기존 PRESTIGE_LEVEL_OVR_BONUS는 그대로 유지(신민용 지시: "1차
#     실험은 기존 것 유지한 채로 얹어서 봐야 어느 쪽 효과인지 안다") —
#     추후 실측 후 조정.
CLUB_STRENGTH_OVR_BONUS_K = 0.3
CLUB_STRENGTH_OVR_BONUS_MIN = -3.0
CLUB_STRENGTH_OVR_BONUS_MAX = 4.0

# [2026-08 신설, 신민용 확정: "토트넘이 음수 보정 때문에 3부 안정 상태에서
# 4부로 추가 추락했다 — 이미 회복 가능성 있던 팀의 회복까지 막는 부작용"]
# 시드 고정 통제실험(같은 시작점) 결과, club_strength 보정을 넣었을 때
# 아스널(양수, +5.48→+8.80)은 2부→1부 승격까지 성공했고 첼시(음수지만
# -8 안팎에서 유지)는 4부 추락을 막았지만, 토트넘(음수, -6~-9로 바닥에
# 붙어있던 케이스)은 오히려 3부 안정(83→84.6)에서 4부 붕괴(72.9)로
# 악화됐다 — "이미 죽어가는 팀을 더 빨리 죽이는" 비대칭 위험이 실측으로
# 확인됨. 이후 다중 시드 실험(아래 CLUB_STRENGTH_OVR_BONUS_MODE 참고)으로
# symmetric(양+음 모두 반영)과 positive_only(음수는 페널티로 안 씀, 회복
# 보너스 전용) 두 방식을 비교했다. 기본값은 실험 결과를 반영해 정한다 —
# 자세한 실험 결과와 최종 채택 근거는 이 변경을 보낸 대화 메시지에 있음.
#   "symmetric":     bonus = clamp(club_strength * K, MIN, MAX)  — 양쪽 다 반영
#   "positive_only": bonus = clamp(max(0, club_strength) * K, 0, MAX)  — 회복만, 처벌 없음
#   "off":           bonus = 0  — 기존 동작(비교용 베이스라인)
# [2026-08 재조정, 신민용 확정: 왕조 스노우볼 버그 수정 ④] positive_only는
# "잘하는 팀은 계속 보너스, 못하는 팀은 페널티 없음"이라는 비대칭이라
# club_strength 상한에 붙은 팀의 실제 선수단 OVR이 세대교체를 거치며
# 계속 우상향하기만 하는 원인 중 하나였다. 토트넘 사례(이미 죽어가는
# 팀을 더 빨리 죽인다)로 positive_only를 택했던 건데, club_strength
# MIN/MAX 자체를 -10~+12 → -4~+4로 크게 좁힌 지금은 하방 폭도 훨씬
# 완만해졌으므로 같은 부작용이 재현될 위험이 낮다고 보고 symmetric으로
# 되돌린다.
CLUB_STRENGTH_OVR_BONUS_MODE = "symmetric"

# [2026-08 신설, 신민용 확정: club_momentum 시스템] "이번 시즌 있었던 큰
# 사건(강등, 국제대회 우승 등)이 팀 체급을 그 즉시 확 바꾸지 않고, 몇
# 시즌에 걸쳐 서서히 정상으로 수렴하게" 하는 범용 완충 장치. 강등 스노우볼
# 방지로 처음 만들었지만("relegation_shield"), 국제대회 성적처럼 다른
# 이벤트에도 같은 틀을 재사용할 수 있게 이름을 club_momentum으로 일반화.
#
# 이벤트 종류(momentum_type)별로 "남은 시즌 수 → (이번 시즌 감쇠계수,
# 임시 보너스)" 스케줄을 정의한다. 감쇠계수는 정상 CLUB_STRENGTH_DECAY
# (0.85) 대신 쓰이고, 보너스는 그 시즌 델타 위에 추가로 얹힌다. 남은
# 시즌이 0이 되면 자동으로 정상 방식(감쇠 0.85, 보너스 없음)으로 복귀.
#
# - relegation_recovery: 강등 직후 붕괴 방지용. 넉넉하게(약함이지만
#   상대적으로 가장 김) — "강등당한 이력 자체가 몇 시즌 버팀목".
#
# [2026-08 재조정, 신민용 리포트: "명문팀들이 15시즌 굴리니 다 강등당하고
# prestige_clubs.py의 0.5% 강등권 목표가 계속 터진다"] 기존 값(최대
# 보너스 1.5)은 club_strength 상한(12.0)에 비해 너무 약해서 실측 매치
# 승률에 거의 영향을 못 줬다 — 그래서 명문팀도 성적이 나쁘면 그냥
# 평범한 팀처럼 강등당했다(club_strength가 이미 무너진 상태에서 시작하는
# 팀에게 겨우 +1.5는 무의미). 두 단계로 나눈다:
#   1) 일반 팀 전체(relegation_recovery): "강등팀은 보통 새 리그에서
#      상위권 재도전팀"이라는 신민용 요청(상위 30% 70%/30~60% 29%/
#      나머지 1%)에 맞춰 예전보다 훨씬 강하게.
#   2) prestige_clubs.py에 등재된 팀은 그 위에 추가로 등급별
#      (relegation_recovery_p3/_p2/_p1) 전용 스케줄을 얹는다 — 등급별
#      "N시즌 내 복귀 확률" 요구(3급 90/98/99.5%, 2급 80/94/98%,
#      1급 65/85/92%)에 맞춰 세기와 지속 시즌 수를 등급순으로 차등.
#      (주의: club_strength는 매치 승률에 통계적으로 영향을 주는
#      레버라 "N시즌 내 X% 복귀"를 문자 그대로 보장하진 못한다 — 실제
#      매치 시뮬레이션까지 반영한 정밀 검증은 못 했으니, 이 값으로도
#      계속 못 버티면 알려줘. 그땐 확률표를 그대로 강제하는 별도
#      메커니즘(시즌마다 룰렛 돌려서 미승격이면 강제 승격)을 얹는 걸
#      권장한다.)
MOMENTUM_SCHEDULES = {
    # [2026-08 v3.3 재조정, 신민용+검토 확정: "명문 여부가 아니라 실제로
    # 연속 강등하고 있는가를 봐야 한다"] 일반 팀(prestige_clubs.py 미등재)
    # 최소 보호선을 p1과 맞춘다(4.0→5.5) — "명문이라 보호"가 아니라
    # "일단 다들 최소한의 안전판은 있어야 한다"는 원칙. 기존 비율(2.5/1.5
    # 단계도 같은 배율로 스케일)을 유지해서 감쇠 곡선 모양은 그대로 둔다.
    "relegation_recovery":    {3: (1.00, 5.5), 2: (0.90, 3.5), 1: (0.80, 2.0)},
    "relegation_recovery_p3": {5: (1.00, 9.0), 4: (0.95, 7.5), 3: (0.90, 6.0),
                                2: (0.85, 4.0), 1: (0.80, 2.0)},
    "relegation_recovery_p2": {4: (1.00, 7.0), 3: (0.92, 5.5), 2: (0.85, 3.5), 1: (0.80, 1.8)},
    "relegation_recovery_p1": {4: (1.00, 5.5), 3: (0.90, 4.0), 2: (0.85, 2.5), 1: (0.80, 1.2)},
    # [2026-08 v3.3 신설, 신민용+검토 확정: "연속 강등 가속 방지"] 방금
    # 강등된 팀이 직전 시즌에도 강등당했다면(relegation_streak≥2) — 이건
    # "정상적인 강등"이 아니라 "실력 대비 계속 자기 수준보다 아래로
    # 떨어지고 있다"는 신호라서, prestige 등급과 무관하게 이 스케줄이
    # 별도로 적용된다(강등 시점에 prestige 기반 스케줄과 비교해 더 강한
    # 쪽을 채택 — game_engine.py _process_promotion_relegation 참고).
    # 연속 2회부터 시작, 3회/4회+로 갈수록 더 세고 길게.
    "relegation_recovery_streak2": {3: (1.00, 7.0), 2: (0.90, 4.5), 1: (0.80, 2.5)},
    "relegation_recovery_streak3": {4: (1.00, 8.0), 3: (0.92, 6.0),
                                     2: (0.85, 4.0), 1: (0.80, 2.0)},
    "relegation_recovery_streak4plus": {5: (1.00, 9.0), 4: (0.95, 7.5), 3: (0.90, 6.0),
                                         2: (0.85, 4.0), 1: (0.80, 2.0)},
    "ucl_champion":        {3: (1.00, 1.5), 2: (0.90, 1.0), 1: (0.85, 0.5)},
    # [2026-08 신설] 유로파급/컨퍼런스급 우승 momentum — 대회 위상이 챔스보다
    # 낮은 만큼 부스트도 그만큼 약하게 잡는다(우승 momentum 세기 서열:
    # 챔스 > 유로파 > 컨퍼런스).
    "uel_champion":        {2: (0.95, 0.8), 1: (0.85, 0.4)},
    "uecl_champion":       {2: (0.90, 0.5), 1: (0.85, 0.25)},
    # [2026-08 신설] 클럽월드컵(4년 주기, "세계 최강 클럽" 타이틀)은 챔스보다
    # 조금 더 강하게, 국내컵은 리그/챔스보다 확실히 약하게 — 대회 위상 차이를
    # momentum 세기로도 반영한다. 스케줄만 추가하면 되는 구조라 확장이 쉽다.
    "cwc_champion":          {3: (1.00, 1.8), 2: (0.90, 1.2), 1: (0.85, 0.6)},
    "domestic_cup_champion": {2: (1.00, 0.8), 1: (0.90, 0.4)},
    # [2026-08 신설, 10순위] 슈퍼컵 — 참가 자체가 "챔스 우승/준우승 +
    # 유로파급·컨퍼런스급 우승팀"으로 이미 걸러진 4팀뿐이라 격 자체는
    # 낮지 않지만, 경기 수(3경기)가 적어 momentum 세기는 국내컵과
    # 비슷한 수준으로 잡는다.
    "super_cup_champion":    {1: (0.90, 0.5)},
    # [2026-09 신설, 신민용 요청: "3급 명문팀인데 강등은 안 당했지만
    # 중위권에서 몇 년씩 정체할 때가 있다 — 최소 1~3등은 해야 하는
    # 팀 아니냐"] 핵심 원칙(신민용 확정): "명문이라서" 발동하는 보정이
    # 아니라 "명문인데 장기간 기대 이하 성적을 냈기 때문에" 발동하는
    # 반작용이다 — is_prestige 자체나 club_strength 시딩과는 무관하게,
    # 오직 실적 이력(teams.stagnation_streak, game_engine.py
    # _process_promotion_relegation에서 매 시즌 갱신)으로만 판정된다.
    # relegation_recovery와 스케줄 구조는 같지만(감쇠 완화+club_strength
    # 보너스), 이 momentum의 진짜 핵심 효과는 club_strength가 아니라
    # ai_lifecycle.py의 영입확률(BUY_REPLACEMENT_PROB_BY_GRADE 가산)·
    # 방출강도(_team_category "mid"→"weak" 격상)·대체선수 목표 OVR
    # 가산(STAGNATION_TARGET_OVR_BONUS) 쪽에 있다 — 그 세 곳 모두
    # momentum_type 문자열을 그대로 읽어 판단한다. club_strength 보너스는
    # relegation_recovery류보다 훨씬 약하게 잡는다(순위 자체를 직접
    # 밀어올리는 게 아니라는 원칙 — "발동" 부분은 game_engine.py의 다음
    # 조건으로: 최근 2시즌 연속 9위 이하(STAGNATION_TRIGGER_PCT 이하 랭킹)
    # → weak, 3시즌 연속 → strong).
    "mid_table_stagnation_weak":   {2: (0.90, 1.0), 1: (0.85, 0.5)},
    "mid_table_stagnation_strong": {3: (0.92, 2.0), 2: (0.88, 1.5), 1: (0.85, 1.0)},
}
# 이벤트 발생 시 momentum_seasons_left를 이 값으로 리셋한다(=스케줄 길이).
MOMENTUM_START_BY_TYPE = {k: max(v.keys()) for k, v in MOMENTUM_SCHEDULES.items()}

# [2026-09 신설, "중위권 정체 탈출" momentum 판정 상수] 순위를 백분위(rank/
# n_teams)로 정규화해서 리그 규모가 달라도(18팀/20팀/24팀 등) 같은 기준이
# 적용되게 한다 — 20팀 리그 기준으로 환산하면:
#   STAGNATION_RECOVER_PCT=0.20 → 4위 이내(신민용 "1~4위 정도로 복귀")
#   STAGNATION_TRIGGER_PCT=0.40 → 9위 이하(신민용 "9위 이하", pct>0.40이면
#     20팀 기준 9위(0.45)부터 해당, 8위(0.40)는 제외)
# 그 사이(5~8위권)는 "중위권이면 유지" — streak를 늘리지도 리셋하지도 않는다.
STAGNATION_RECOVER_PCT = 0.20
STAGNATION_TRIGGER_PCT = 0.40
STAGNATION_STREAK_WEAK = 2      # 이 값 이상 연속 정체 시 weak momentum
STAGNATION_STREAK_STRONG = 3    # 이 값 이상 연속 정체 시 strong momentum

# [2026-09 신설] "중위권 정체 탈출" momentum의 실제 핵심 효과 — 신민용이
# 명시한 3가지(영입확률↑/방출강도↑/대체선수 목표OVR↑) 중 club_strength로
# 표현 안 되는 두 가지를 여기서 담당한다(방출강도는 ai_lifecycle.py
# _team_category가 momentum_type을 직접 읽어 "mid"를 "weak"로 격상하는
# 방식이라 여기 별도 상수가 필요 없음). _retire_and_replace의 은퇴 대체
# 로직(영입 확률/목표 OVR)에 그대로 더해진다 — 기존 CLUB_STRENGTH_OVR_
# BONUS/BUY_REPLACEMENT_PROB_BY_GRADE 위에 "추가로 얹는" 방식이라 기존
# 로직 자체는 건드리지 않는다(신민용 요청: "기존 시스템을 뜯어고치기보다
# 그 입력값에 추가 modifier 하나를 얹는 방식이 가장 안전").
STAGNATION_BUY_PROB_BONUS = {
    "mid_table_stagnation_weak": 0.15,
    "mid_table_stagnation_strong": 0.30,
}
STAGNATION_TARGET_OVR_BONUS = {
    "mid_table_stagnation_weak": 2.0,
    "mid_table_stagnation_strong": 4.0,
}

# [2026-08 재설계, 신민용 확정: "prestige_clubs.py 안 팀들만 보호하려던 게
# 아니라, 거기 없는 팀도 약간은 보호받아야 한다"] 처음엔 정적 명문팀
# 리스트(레벨1/2/3)로만 바닥 티어를 판정했는데, 그러면:
#   - 레벨1(토트넘·FC서울급)은 하드 플로어가 아예 없어서 실측에서 1부→4부
#     까지 그냥 밀렸고,
#   - 레벨3(바르사·레알급)도 "3부→4부" 경계에서만 걸려서 1부→2부→3부
#     구간은 무방비였고,
#   - 무엇보다 정적 리스트 밖의 팀(그 세이브 안에서 실제로 몇 시즌간
#     잘해서 강해진 팀)은 아예 대상이 아니었다.
# club_strength(동적 팀 강도, 실제 최근 성적으로 매 시즌 갱신됨)를 기준으로
# 바꾼다 — 정적 리스트가 아니라 "지금 이 세이브에서 실제로 강한 팀"이면
# 명문팀이든 아니든 똑같이 보호 대상이 된다. 명문팀은 시딩값(2~6) 덕에
# 자연히 이 밴드에 먼저 들어가지만, 하드코딩 리스트 밖의 팀도 몇 시즌
# 잘하면 club_strength가 올라가 똑같이 보호받는다 — "명문이라 보호"가
# 아니라 "강해서 보호"로 원칙이 바뀐 것.
# (min_club_strength, 바닥 티어, 그 바닥을 뚫으려 할 때 취소 확률) —
# club_strength가 높은 순으로 정렬. 절대 강등 불가가 아니라 확률적 완충.
# [2026-08 신설] 산하팀(리저브/B팀/유스팀) 1부 승격 제한 feature flag.
# [2026-08 확정] 200시즌 A/B 장기 시뮬레이션(seed=12345, PYTHONHASHSEED=0)
# 검증 완료 — ON 200시즌 동안 AUTO/PO 두 경로 모두 AFFILIATE/REVIEW의
# 1부 진입 0건, 자기참조 0건, 크래시 0건. 반대로 OFF 대조군은 200시즌
# 누적 36건(AUTO 30 + PO 6)이 실제로 1부에 진입해 리그 무결성이 깨짐이
# 확인됐다. 이제 예방적 옵션이 아니라 기본 정책으로 True가 기본값.
# True면 _process_promotion_relegation()의 승격 후보 선정 단계에서
# classification_status가 'NORMAL'인 팀만 승격 후보로 인정한다
# (AFFILIATE/REVIEW는 자동 승격/플레이오프 승격 모두 제외).
AFFILIATE_PROMOTION_RESTRICTION = True

# [2026-08 신설] 산하팀 tier 보정 로직의 감사 로그(tier_audit.jsonl) 기록
# 여부. 이건 정식 게임 기능이 아니라 "산하팀이 부모팀과 tier가 역전되는
# 잔존 버그" 추적 전용 디버깅 계측이다 — 실제 플레이에서는 매 시즌마다
# 불필요한 로그 파일이 쌓이면 안 되므로 기본값은 False.
# 다음 세션에서 "동적 tier 역전 추적"을 다시 시작할 때만 True로 켜고,
# 끝나면 다시 False로 되돌릴 것. (감사 로그를 만드는 코드 자체는
# 지우지 않고 그대로 남겨뒀다 — 이 플래그로 파일 기록만 켜고 끈다.)
TIER_AUDIT_LOGGING = False

# [2026-08 제거, 신민용 확정: "강등 자체를 막으면 안 된다"]
# 예전엔 여기 CLUB_STRENGTH_RELEGATION_FLOOR로 "club_strength가 임계값
# 이상이면 확률적으로 강등을 취소하고 대신 순위표상 더 위였던 팀을
# 대신 강등시키는" 로직(_process_promotion_relegation 안)이 있었다.
# 그런데 이건 매치 시뮬레이션이 다 끝나고 순위(승점→득실차)까지 이미
# 확정된 뒤에 그 결과 자체를 뒤집는 사후 조작이었다 — "19·20위는
# 안 내려가고 대신 17·18위가 강등"되는, 순위표 의미가 훼손되는 결과가
# 실제로 나왔다(2026-08 리포트).
# 강팀이 실제로 잘 안 떨어지게 하려면 이미 있는 CLUB_STRENGTH_MATCH_WEIGHT
# (경기 "결과가 나오기 전" 매치 강도 자체에 반영되는 값)로 처리하는 게
# 맞는 경로라, 이 상수와 그 사용처는 완전히 제거했다. 다시 추가하고
# 싶어지더라도 "순위 확정 후 결과를 바꾸는" 방식은 피할 것.


# [2026-08 신설, 신민용 확정: "일반 강등팀 하부리그 안착 분포"] 예전엔
# 일반(비명문) 강등팀도 명문팀과 마찬가지로 새 리그 상위 25%(pct=0.75)
# 지점 하나로 완전히 결정론적으로 이동시켰다 — 강등된 원인(원래 스쿼드가
# 약해서/불운해서 등)과 무관하게 "강등만 당하면 다들 새 리그 상위권에서
# 시작"하는 부자연스러운 구조였다. 신민용 요청에 따라, 목표 지점을
# 고정값이 아니라 "구간을 먼저 확률로 뽑고, 그 구간 안에서 다시 균등난수로
# percentile을 뽑는" 2단계 방식으로 바꾼다 — 같은 팀이라도 강등할 때마다
# 다른 지점에 착지할 수 있다.
#
# (lo_pct, hi_pct, weight) 튜플의 리스트. lo_pct/hi_pct는
# _cached_league_strong_ovr()/get_league_strong_ovr류 함수가 쓰는 것과
# 동일한 pct 관례(0.0=새 리그에서 가장 약한 팀 지점, 1.0=가장 강한 팀
# 지점 — sorted(team_avgs) 오름차순 인덱싱 기준)를 그대로 쓴다. weight는
# 이 구간이 뽑힐 확률(전체 합 1.0).
#
# 신민용이 준 요구사항은 "그 강등팀이 하부리그에서 차지할 순위 구간"
# 기준(예: 상위 1~10% = 새 리그에서 등수로 봤을 때 최상위 1~10위 구간)
# 이라 "상위 X%"는 강한 쪽이므로 pct 관례로는 (1 - X/100)에 가깝다.
# "강등권"(진짜 위험한 하위권 중에서도 최하단)은 "하위 40%" 안에 포함된
# 부분집합으로 보고, 두 구간이 서로 겹치지 않도록 강등권 몫(하위 10%)을
# 하위40% 쪽에서 미리 떼어냈다 — prestige_clubs.py의 상위권/중위권/
# 하위권/강등권 4단 구분과 같은 관례(강등권은 하위권 중 최하단 서브셋).
#   상위 1~10%(랭크)  → pct [0.90, 1.00]  15%
#   상위 10~25%(랭크) → pct [0.75, 0.90)  30%
#   상위 25~40%(랭크) → pct [0.60, 0.75)  30%
#   40~60%(랭크)      → pct [0.40, 0.60)  18%
#   하위 40%(랭크, 강등권 제외) → pct [0.10, 0.40)  6%
#   강등권(랭크 최하단) → pct [0.00, 0.10)  1%
# 명문팀(prestige_level 1~3)에는 적용하지 않는다 — 명문팀은 기존처럼
# 상위 25%(pct=0.75) 고정 지점을 그대로 유지한다(_process_promotion_
# relegation 참고). 필요하면 나중에 명문팀 전용 분포도 별도로 설계 가능.
GENERAL_RELEGATION_LANDING_BANDS = [
    (0.00, 0.10, 0.01),   # 강등권
    (0.10, 0.40, 0.06),   # 하위 40%(강등권 제외)
    (0.40, 0.60, 0.18),   # 40~60%
    (0.60, 0.75, 0.30),   # 상위 25~40%
    (0.75, 0.90, 0.30),   # 상위 10~25%
    (0.90, 1.00, 0.15),   # 상위 1~10%
]


def sample_general_relegation_landing_pct() -> float:
    """GENERAL_RELEGATION_LANDING_BANDS 확률표에 따라 구간을 하나 뽑고,
    그 구간 안에서 다시 균등난수로 percentile(pct, 0.0~1.0)을 하나 뽑아
    반환한다. 반환값은 _cached_league_strong_ovr(league_id, pct=...)에
    그대로 넘길 수 있는 형식이다."""
    r = random.random()
    cum = 0.0
    for lo, hi, w in GENERAL_RELEGATION_LANDING_BANDS:
        cum += w
        if r <= cum:
            return random.uniform(lo, hi)
    # 부동소수 누적오차로 cum이 1.0에 살짝 못 미쳐 루프를 다 돌아버리는
    # 경우에 대한 안전망 — 마지막 구간에서 뽑는다.
    lo, hi, _ = GENERAL_RELEGATION_LANDING_BANDS[-1]
    return random.uniform(lo, hi)


# [2026-09 신설, 신민용 리포트: "prestige_clubs.py 팀들은 강등당해도 최대
# 3년 안에 복귀하게 해뒀는데 복귀를 못하고 계속 강등당한다 — 이겼던 팀이
# 보너스를 받거나 승격한 팀이 무언가를 받아서 그런 것 같다"] 원인 확인:
# 위 GENERAL_RELEGATION_LANDING_BANDS 주석에 이미 적혀 있듯, 명문팀은
# 등급(1~3) 구분 없이 무조건 하위 리그의 상위 25%(pct=0.75) 지점에만
# 착지했다 — "상위 25%"는 그 리그에서 1등을 확신할 수 있는 자리가 아니라
# 그냥 중상위권일 뿐이라, 다른 팀이 club_strength(실적 기반, update_
# club_strength_after_season)를 잘 쌓거나 자기 나름대로 좋은 시즌을 보내면
# (사용자가 말한 "이겼던 팀의 보너스") 얼마든지 순위에서 앞설 수 있었다.
# 실제로 강등 회복 momentum(relegation_recovery_p1/p2/p3, MOMENTUM_
# SCHEDULES)의 club_strength 보너스도 CLUB_STRENGTH_MIN~MAX(-4~+4)에
# 그대로 클램프되므로, 착실히 잘하고 있는 다른 팀이 정상적으로 도달할 수
# 있는 상한과 사실상 같은 수준으로 눌린다 — 즉 명문팀의 진짜 우위는
# "3년 안 복귀를 보장할 만큼" 크지 않았다. 등급이 높을수록 그 리그
# 최상위에 훨씬 가깝게 착지하도록(3급=거의 확실한 1위권, 2급=최상위권,
# 1급=상위권) 등급별로 나눈다 — _process_promotion_relegation의 강등
# 착지 지점 계산이 이 값을 쓴다.
PRESTIGE_RELEGATION_LANDING_PCT = {3: 0.97, 2: 0.92, 1: 0.85}

# [2026-09 신설, 신민용+GPT 협업: "명문팀은 은퇴자를 유망주 즉시 생성으로
# 채우지 않는다 — 먼저 시장에서 검증된 선수를 영입 시도하고, 정말 적합한
# 선수가 없을 때만 자체 유스 생성을 fallback으로 쓴다"] 지금까지
# _retire_and_replace는 명문팀이든 아니든 항상 그 자리에 16~21세 원석을
# 새로 심었다 — 현실은 반대로, 큰 팀일수록 은퇴 공백을 이미 어딘가에서
# 검증된 선수를 사와서 메우고, 진짜 어린 원석은 작은 팀에서 자라다가
# 이적으로 올라가는 피라미드 구조다. 등급이 높을수록 "영입으로 채울
# 확률"을 높게 잡는다(신민용이 직접 제시한 표) — 못 채우면(확률 미달 또는
# 적합한 후보가 시장에 없음) 기존 유스 생성으로 자연히 폴백한다.
BUY_REPLACEMENT_PROB_BY_GRADE = {
    "SS": 0.90, "S": 0.85, "A": 0.70, "B": 0.50,
    "C": 0.30, "D": 0.10, "E": 0.10, "F": 0.05,
}
# prestige_level(명문 등급, prestige_clubs.py)이 이 문턱 이상이면, 그 팀이
# 등록된 나라의 리그 등급이 SS/S가 아니어도 최소 S급 취급으로 위 표를
# 적용한다 — "명문은 명문"이라는 원칙(다른 명문 보정들과 동일)을 여기도
# 맞춘다.
BIG_CLUB_PRESTIGE_THRESHOLD = 2
# 영입 후보를 찾을 때 목표 OVR(은퇴자가 원래 있던 자리의 "성인 잠재치")
# 대비 허용하는 폭 — (아래로 허용치, 위로 허용치). "레알 마드리드 평균
# 89면 84~90 정도를 찾는다"는 신민용 예시에 맞춰, 목표보다 살짝 낮은
# 선수까지는 넉넉히 허용하고 위로는 좁게(과분한 선수는 애초에 시장에
# 잘 안 나옴) 잡는다.
BUY_REPLACEMENT_OVR_BAND = (10, 4)
# 영입 후보 중 이 나이 이하면 성장여력을 감안해 뽑힐 가중치를 올린다
# ("젊고 성장 가능성이 높은 선수" 우선순위).
BUY_REPLACEMENT_YOUNG_AGE = 26
BUY_REPLACEMENT_YOUNG_WEIGHT = 1.6

# [2026-09 신설, 신민용 요청: "유럽 1부리그 팀들은 특히 잘하는 선수를
# 영입하려는데, 3급은 압도적인 선수를, 2급/1급은 상대적으로 덜한 선수를
# 노려야 한다 — 현실에도 토트넘은 강등은 안 당해도 17위·16위인 적이
# 있으니"] 위 BUY_REPLACEMENT_*는 "은퇴 자리가 생겼을 때만" 작동하는데,
# 그거랑 별개로 명문팀은 은퇴와 무관하게 상시로 세계 시장에서 스카우팅
# 업그레이드를 시도해야 한다(ai_lifecycle._prestige_scouting). 등급이
# 높을수록 그 포지션 세계 랭킹에서 훨씬 좁고 높은 상위권에서만 후보를
# 찾는다 — 3급은 세계 최상위 0.5%(사실상 그 포지션 최정상급)까지 노리고,
# 2급은 상위 3%, 1급은 상위 10%(그냥 "지금보다 확실히 나은" 정도)만
# 본다. 1급이 압도적 인재까지 넘보지 않게 하는 게 "토트넘이 항상 최정상급
# 스쿼드는 아니다"를 만드는 핵심 장치다.
# [2026-09 수정, 헤드리스 재검증 중 자체 발견] 처음엔 "그 등급의 상위
# 몇% *이내*"(누적)로 잘라서, 1급(상위10%)이 3급 몫(상위0.5%)까지
# 그대로 포함해버렸다 — 그러면 세비야(1급)가 세계 3위급(97) 선수를
# 뽑는 등 "1급은 압도적인 선수를 노리면 안 된다"는 요청과 어긋났다.
# 이제 등급별로 겹치지 않는 구간(위 등급의 몫은 제외)으로 나눈다 —
# 3급=최상위 0~0.5%, 2급=0.5~3%, 1급=3~10%.
PRESTIGE_SCOUT_BAND = {3: (0.0, 0.005), 2: (0.005, 0.03), 1: (0.03, 0.10)}
# 시즌당 명문팀 하나가 스카우팅을 "시도"하는 포지션 개수 — 매 시즌 전
# 포지션을 싹 다 갈아치우는 게 아니라, 가끔 한두 자리만 업그레이드를
# 노리는 정도로 제한한다(등급이 높을수록 조금 더 적극적).
PRESTIGE_SCOUT_ATTEMPTS_PER_SEASON = {3: 2, 2: 1, 1: 1}
# 지금 그 자리 선수보다 최소 이만큼은 OVR이 높아야 "업그레이드"로 보고
# 스카우팅을 시도한다 — 이미 그 자리가 강하면(격차가 안 나면) 자연히
# 스카우팅이 안 일어난다.
PRESTIGE_SCOUT_MIN_GAP = 3

# [2026-09 신설, 신민용 요청: "이적 종류(이적/임대)도 구분해야 한다"] AI
# 이적시장(_transfer_market)에서 이동하는 선수 중 일부를 완전이적 대신
# 임대로 처리한다 — "AI는 단순해야 한다" 원칙대로, 나이 하나만으로
# 가볍게 가른다(어릴수록 스쿼드 depth 확보/성장 목적의 임대가 현실적으로
# 더 흔함).
AI_LOAN_PROBABILITY_YOUNG = 0.25   # 23세 이하
AI_LOAN_PROBABILITY_OLD = 0.08     # 24세 이상
AI_LOAN_DURATION_YEARS = (1, 2)    # 임대 기간(년) 범위

# [2026-09 신설, 신민용 요청: "계약을 언제부터 했냐가 아니라 몇년치
# 했냐인건데... 기간이 늘어나면 연장 이런식으로 하고 연봉 수치도
# 변화하잖아"] 지금까지 AI 선수는 이적할 때만 새 계약(=새 연봉)이
# 생겼다 — 그대로 한 팀에 계속 있으면 계약이 만료돼도 아무 일도 안
# 일어났다(계약만료 임박이 이적시장 확률 가중치로만 조금 반영될 뿐).
# 매 시즌 계약이 만료된(그리고 임대 중이 아닌) 선수를 대상으로 "재계약"
# 여부를 굴린다 — 대부분(이적시장에서 안 팔린 선수는) 그대로 눌러앉는
# 게 현실적이라 확률을 높게 잡는다.
AI_CONTRACT_RENEWAL_PROB = 0.75
AI_CONTRACT_RENEWAL_DURATION_YEARS = (2, 5)   # 재계약 기간(년) 범위


def club_strength_delta_for_rank(rank: int, n_teams: int) -> float:
    """순위(1부터)와 리그 참가팀 수를 받아 시즌 종료 시 club_strength에
    더할 델타를 반환한다. CLUB_STRENGTH_DELTA_BY_RANK_PCT를 순회하며
    순위 백분율이 처음으로 상한 이하가 되는 구간의 델타를 쓴다."""
    if n_teams <= 0:
        return 0.0
    pct = rank / n_teams
    for cap, delta in CLUB_STRENGTH_DELTA_BY_RANK_PCT:
        if pct <= cap:
            return delta
    return CLUB_STRENGTH_DELTA_BY_RANK_PCT[-1][1]

# [2026-07 재설계, 신민용+GPT 검토: "국가대표 OVR이 100에 몰린다/등급이
# 역전된다"] 예전 방식(base + 대륙보정 + 국가별 조정치 + 노이즈)은 값
# 몇 개를 따로따로 더하고 빼다 보니 최종 합산값이 어디 떨어지는지 아무도
# 검증할 수 없어서, 노이즈를 더하기도 전에 이미 상한(100)을 넘는 나라가
# 생기고(포르투갈), 등급 역전(모로코 S인데 A 밑)도 나왔다. 이제 나라마다
# (하한, 중간값, 상한)을 직접 지정한다 — 하한=침체기(4~5년에 한 번),
# 중간값=평상시, 상한=전성기. random.triangular(하, 상, 중)으로 뽑으면
# 값 자체가 이미 1~100 안에서 확정되므로 클램프에 쏠리는 문제가 구조적
# 으로 없어진다.
#
# 등급(S/A/B)은 기존 게임 분류를 그대로 유지하고 "그 안의 수치"만 다시
# 잡았다 — 2026-07-20 FIFA 랭킹(2026 월드컵 우승 스페인, 준결승 스페인/
# 아르헨티나/프랑스/잉글랜드, 아프리카 최고 모로코 6위) + GPT 검토 의견
# (독일을 S등급 안에서 더 위로, 포르투갈은 그 아래로 재배치)을 반영했다.
# 등급 경계에서 일부러 겹치게 뒀다 — "독일이 항상 이탈리아보다 강하진
# 않다"처럼 세대에 따라 순위가 뒤집히는 게 현실적이라는 판단.
NAT_OVR_BAND = {
    # ── S등급 (7개국) ──────────────────────────────────
    "스페인":   (86, 96, 99),
    "프랑스":   (84, 94, 98),
    "아르헨티나": (83, 93, 97),
    "브라질":   (82, 92, 97),
    "잉글랜드": (81, 91, 96),
    "독일":     (80, 90, 95),
    "포르투갈": (78, 88, 94),
    "네덜란드": (77, 87, 93),
    "벨기에":   (76, 86, 92),
    "모로코":   (74, 85, 91),

    # ── A등급 (15개국) ─────────────────────────────────
    "이탈리아": (76, 88, 93),
    "크로아티아": (75, 87, 92),
    "우루과이": (74, 86, 91),
    "콜롬비아": (73, 85, 90),
    "멕시코":   (72, 84, 89),
    "미국":     (70, 83, 89),
    "일본":     (70, 82, 87),
    "세네갈":   (69, 82, 87),
    "스위스":   (69, 81, 86),
    "덴마크":   (68, 80, 85),
    "오스트리아": (67, 79, 84),
    "나이지리아": (66, 79, 85),
    "대한민국": (65, 78, 83),
    "호주":     (64, 76, 81),
    "이란":     (63, 76, 81),

    # ── B등급 (26개국) ─────────────────────────────────
    # [2026-08 재조정] 62~81은 B등급 하단에 가까웠는데, B등급 최상단에
    # 더 가까운 66~84가 더 자연스럽다는 검토를 반영.
    "튀르키예": (66, 76, 84),
    "노르웨이": (61, 74, 80),
    "폴란드":   (60, 73, 79),
    "세르비아": (60, 73, 79),
    "우크라이나": (59, 72, 78),
    "스웨덴":   (58, 71, 77),
    "웨일스":   (57, 70, 76),
    "러시아":   (57, 70, 76),
    "그리스":   (56, 69, 75),
    "스코틀랜드": (56, 69, 74),
    "체코":     (55, 68, 74),
    # [2026-08 재조정] 54~74는 조금 낮았다 — 57~76으로 소폭 상향.
    "코트디부아르": (57, 68, 76),
    "카메룬":   (54, 67, 73),
    # [2026-08 신설] 예전엔 명시 지정이 없어 C등급 보간 폴백(39~59)에
    # 의존했는데, 39~59는 D등급 중상단 정도라 C등급 국대치고 너무 낮다는
    # 지적을 반영해 명시 지정으로 승격.
    "가나":     (52, 62, 71),
    "사우디아라비아": (53, 67, 73),   # 리그(오일머니)는 강해도 국대는 자국
                                      # 선수만 쓰므로 클럽과 분리해 중위권 유지
    "알제리":   (53, 66, 72),
    "이집트":   (52, 66, 72),
    "에콰도르": (52, 65, 71),
    "칠레":     (51, 65, 71),
    "캐나다":   (51, 64, 70),
    "베네수엘라": (50, 63, 69),
    "파라과이": (50, 63, 69),
    "페루":     (49, 62, 68),
    "파나마":   (48, 61, 67),
    "슬로바키아": (48, 61, 67),
    "헝가리":   (47, 60, 66),
    "콩고 민주 공화국": (46, 59, 65),

    # ── C등급 예외 4개국 (2026-07, GPT 검토: "FIFA 랭킹은 경기 수·대륙
    # 대회 영향도 받아서 실력과 안 맞을 때가 있다 — C등급 상위 정도는
    # 손으로 잡는 게 좋다") — 나머지 C~F는 전부 fifa_rank 보간에 맡긴다.
    "핀란드":   (44, 56, 63),
    "루마니아": (43, 55, 62),
    "슬로베니아": (42, 54, 61),
    "조지아":   (41, 53, 60),
}

# C~F 등급(위 예외 4개국 제외) — 등급 안에서 fifa_rank 오름차순 순위
# 비율로 (하한, 중간값, 상한) 범위 안에서 선형보간한다. 국가 수가 많고
# (150개국) 실력 차가 미미해서 일일이 손으로 잡을 실익이 적다는 GPT
# 의견을 반영 — get_nat_ovr_band()에서 사용.
NAT_OVR_GRADE_BAND_RANGE = {
    "C": ((34, 44), (46, 56), (55, 63)),
    "D": ((26, 34), (38, 46), (46, 54)),
    "E": ((20, 26), (31, 38), (39, 46)),
    "F": ((14, 20), (24, 31), (31, 39)),
}

# [2026-07 신설] 국가대표 "세대 계수" — 밴드 안에서 매년 완전 독립적으로
# 난수를 뽑으면 "올해 하한, 내년 바로 상한" 같은 비현실적 롤러코스터가
# 나온다. 이 계수(0.97~1.03)가 8~12년 주기로 서서히 새 목표치를 향해
# 움직이면서 밴드 값에 곱해져 "황금세대/침체기"가 여러 해에 걸쳐 이어지는
# 느낌을 만든다. 매년 목표로 얼마나 다가가는지의 비율(0~1, 클수록 빨리
# 수렴) — nat_generation 테이블/_get_generation_coef()에서 사용.
NAT_GENERATION_STEP = 0.15
NAT_GENERATION_RANGE = (0.97, 1.03)
NAT_GENERATION_CYCLE_YEARS = (8, 12)   # 새 목표치를 뽑는 주기(년), 이 사이 랜덤

GRADE_QUAL_BASE = {"SS": 0.95, "S": 0.90, "A": 0.78, "B": 0.62, "C": 0.48,
                   "D": 0.36, "E": 0.26, "F": 0.16}
QUAL_NOISE = 0.12   # 예선 랜덤 노이즈 (±) → 강호 탈락/약체 진출 이변 발생

# 국가대표 선발 기준 (국가 등급별 최소 OVR / 최대 소속 리그 티어)
INTL_SELECTION_OVR = {"SS": 80, "S": 75, "A": 65, "B": 55, "C": 48, "D": 42, "E": 37, "F": 32}
# 국가대표 선발 마진: 자국 등급평균(GRADE_TEAM_OVR) 대비 이만큼 낮아도 선발.
#   작을수록 엄격(톱권만). 베테랑 보너스(_vet_bonus)가 경계선 선수를 구제한다.
#   ※ 이 시뮬은 '선발=풀타임 출전'(벤치 미구현)이므로, 스쿼드 합류가 아니라
#     '주전으로 뛸 수준'을 기준으로 한다 → 마진을 3으로 좁힘(주전급).
INTL_SELECTION_MARGIN = 3
# [2026-07 신설, 신민용 리포트: "INTL_SQUAD_QUOTA를 찾을 수 없다는 NameError가
# 난다"] _check_selection()의 포지션별 정원 경쟁 로직이 참조하는데 정의가
# 빠져 있었다. 국가 등급별로 다르게 두지 않는다(신민용 확정: "국가대표 명단
# 규모는 강팀과 약팀이 크게 다르지 않고, 선수층 차이는 경쟁에서 자연스럽게
# 드러난다") — 23인(GK3+DF8+MF8+FW4) 고정.
INTL_SQUAD_QUOTA = {"GK": 3, "DF": 8, "MF": 8, "FW": 4}
INTL_MAX_TIER      = {"SS": 1, "S": 1, "A": 1, "B": 2, "C": 2, "D": 3, "E": 3, "F": 3}
INTL_MIN_MATCHES   = 5

# 클럽 입단/오퍼 마진: 팀 평균 OVR 대비 내 OVR이 이만큼까지 낮아도 입단 가능.
#   국대와 같은 논리(입단=주전 출전, 벤치 미구현) → '주전 경쟁 가능' 수준.
#   ※ 국가 등급별 차등: 상위 리그일수록 문턱이 빡빡하다.
#     - S/A급(톱 리그): 마진 1 → 거의 그 팀 평균급이어야 입단.
#       (엘리트 전성기 90 → S급 1부, 평범 85 → A급 1부가 한계, S급은 못 감)
#     - 하위 등급: 점점 관대(아무나 데뷔 가능한 약체 리그).
CLUB_JOIN_MARGIN = 3   # (하위호환용 기본값 — 등급 미상 시 사용. my_join_margin/직접지원 계산 등에서 여전히 사용)
CLUB_JOIN_MARGIN_BY_GRADE = {
    "S": 1, "A": 1, "B": 3, "C": 4, "D": 5, "E": 6, "F": 7,
}

# [밸런스 재설계 2026-07, 신민용 설계+GPT 검토+실데이터(game.db 리그당 팀수
# 6~30팀) 대조 확정] 오퍼/입단/직접지원 세 경로를 하나의 기준으로 통일하기
# 위한 상수 모음. 숫자만 바꿔서 밸런스 조정이 가능하도록 전부 여기 모은다.

# 1) 자동 오퍼 최소 보장 확률(에이전트 등급별). 실제 확률은 이 값과
#    퍼포먼스 기반 확률 중 큰 쪽(max)을 쓴다 — 못해도 이 정도는 보장,
#    잘하면 이보다 훨씬 높은 확률로 뜬다.
# [2026-08 재설계, 에이전트 사다리 한 칸씩 밀림에 맞춰 같이 이동]
AGENT_MIN_OFFER_PROB = {
    "없음": 0.08,  # old F
    "F": 0.12,     # old E
    "E": 0.16,     # old D
    "D": 0.22,     # old C
    "C": 0.30,     # old B
    "B": 0.38,     # old A
    "A": 0.45,     # old S
    "S": 0.55,     # 신설
}

# 2) 계약 만료 임박 보너스 — "게임 내 남은 주 수" 기준(시즌=52주 고정).
#    (임계 주 수, 배율) 오름차순. 남은 주가 임계값 이하면 그 배율 적용,
#    마지막 구간을 넘으면 CONTRACT_URGENCY_FALLBACK 적용.
CONTRACT_URGENCY_BONUS = [
    (13,  1.6),   # 3개월 이하
    (26,  1.4),   # 6개월 이하
    (52,  1.2),   # 1년 이하
    (104, 1.0),   # 2년 이하
]
CONTRACT_URGENCY_FALLBACK = 0.9   # 2년 초과 — 장기계약, 관심 다소 낮음

# 3) 오퍼 역할 결정에 쓰는 나이 기준.
ROLE_AGE_THRESHOLD = 30          # 이 나이 이상 + 격차 애매(0~3)면 로테이션으로
ROLE_YOUNG_PROSPECT_MAX_AGE = 22 # 이 나이 이하 + 격차 큼이면 유망주 영입으로

# 4) 팀 후보 필터링 마진 — 국가 등급 대신 "그 팀이 자기 리그 안에서 상위
#    몇 %인가"로 결정. (누적 백분위 상한, 마진) — 리그 최강팀에 가까울수록
#    빡빡(마진1), 최하위권일수록 관대(마진5).
LEAGUE_RELATIVE_MARGIN_BANDS = [
    (0.2, 1),   # 상위 20% = 명문
    (0.4, 2),   # 상위 40% = 상위권
    (0.6, 3),   # 상위 60% = 중위권
    (0.8, 4),   # 상위 80% = 하위권
]
LEAGUE_RELATIVE_MARGIN_FALLBACK = 5   # 하위 20% = 최하위권 (그 이상 전부 포함)

# 4-1) 해외(자국 외) 팀의 오퍼 마진 페널티 — 위 리그 내 상대적 위치
#      마진에서 이 값만큼 빼서 해외 진출을 자국 이적보다 더 빡빡하게
#      만든다. 예: 명문팀(마진1) 기준 해외면 마진-1(사실상 내가 팀
#      평균보다 강해야 통과), 최하위권(마진5) 기준 해외면 마진3 정도로
#      여전히 어느 정도는 열려 있음. _AGENT_CONTINENT_BONUS(전문 에이전트
#      보정)와 합산 적용되므로 대륙 전문 에이전트가 있으면 완화된다.
FOREIGN_OFFER_MARGIN_PENALTY = 2

# 4-2) 해외 오퍼 성사 확률(pass_fraction)에 곱하는 나이 보정 — 나이가
#      들수록 해외 이적 시장의 관심이 자연스럽게 줄어드는 효과. 이미
#      effective_ovr/출전량/평점/인기/지역 보정이 시장성을 결정하므로
#      나이는 약한 감쇠/보너스로만 얹는다. (상한 나이, 배수) 오름차순 —
#      해당 나이 이하 첫 구간이 적용된다. 33세 이상 구간만 별도 처리
#      (아래 FOREIGN_OFFER_AGE_MULT_33PLUS* 참고).
#      [2026-08 신설] 후보 가중치(_w, random.choices 상대비중)가 아니라
#      pass_fraction(성사 확률)에 곱해야 실제로 효과가 있다 — _w는 선수
#      한 명에게 모든 후보가 동일한 값이라 상대 가중치 정규화 과정에서
#      상쇄되어 사실상 무효과가 된다(나라마다 다른 지역/수출국 가중치와
#      다른 점).
FOREIGN_OFFER_AGE_MULT = [
    (22, 1.08),
    (25, 1.05),
    (28, 1.00),
    (30, 0.95),
    (32, 0.90),
]
# [2026-08 신설, 신민용 지시] 33세 이상 감쇠폭은 재능 등급에 따라 다르게 —
# 월드클래스/신급은 30대 중후반까지도 실제로 뛰는 선수가 있으므로(에이징
# 커브 자체가 이미 이들을 우대) 노쇠 감쇠를 -20%가 아니라 -15%로 완화한다.
# 그 외 재능 등급은 기존 -20% 그대로.
FOREIGN_OFFER_AGE_MULT_33PLUS = 0.80
FOREIGN_OFFER_AGE_MULT_33PLUS_ELITE_TALENT = 0.85
FOREIGN_OFFER_ELITE_TALENT_TIERS = {"god", "worldclass"}


def get_foreign_offer_age_mult(age: int, talent_tier: str = None) -> float:
    """해외 오퍼 성사 확률에 곱할 나이 배수. 33세 이상은 재능 등급(god/
    worldclass면 완화된 -15%, 그 외는 기존 -20%)에 따라 갈린다."""
    for cap, mult in FOREIGN_OFFER_AGE_MULT:
        if age <= cap:
            return mult
    if talent_tier in FOREIGN_OFFER_ELITE_TALENT_TIERS:
        return FOREIGN_OFFER_AGE_MULT_33PLUS_ELITE_TALENT
    return FOREIGN_OFFER_AGE_MULT_33PLUS

# 5) 패시브 오퍼(자동 오퍼·무소속 입단) 후보 선별 — 마진 통과 후보를 팀
#    평균 OVR 높은 순으로 줄 세운 뒤, 등수 구간별 가중치로 추첨한다
#    (고정 정렬로 뽑으면 매번 같은 상위 1~2팀만 나오는 문제를 피하기 위함).
#    (순위 상한, 가중치) — 1~3위 가중치 5, 4~6위 가중치 3, 7~10위 가중치 1.
PASSIVE_OFFER_RANK_WEIGHTS = [
    (3, 5),
    (6, 3),
    (10, 1),
]
PASSIVE_OFFER_RANK_WEIGHT_FALLBACK = 0.3   # 11위 이하(넓게 뽑혔을 때의 꼬리)


def get_league_relative_margin(pct: float) -> int:
    """팀의 리그 내 백분위(0에 가까울수록 강팀)로 마진(1~5)을 반환."""
    for cap, margin in LEAGUE_RELATIVE_MARGIN_BANDS:
        if pct <= cap:
            return margin
    return LEAGUE_RELATIVE_MARGIN_FALLBACK


def get_passive_offer_rank_weight(rank: int) -> float:
    """마진 통과 후보를 팀 평균 OVR 내림차순으로 줄 세웠을 때의 순위(1부터)로
    추첨 가중치를 반환. 상위권일수록 가중치가 크다."""
    for cap, weight in PASSIVE_OFFER_RANK_WEIGHTS:
        if rank <= cap:
            return weight
    return PASSIVE_OFFER_RANK_WEIGHT_FALLBACK


def get_contract_urgency_mult(weeks_left: int) -> float:
    """계약 만료까지 남은 주 수로 오퍼 확률 배율을 반환."""
    for cap, mult in CONTRACT_URGENCY_BONUS:
        if weeks_left <= cap:
            return mult
    return CONTRACT_URGENCY_FALLBACK


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
    # [2026-07 재조정, 신민용 지적: "잉글랜드 2부가 스페인 2부랑 비슷하거나
    # 낮다 — 챔피언십은 80후반~90대로 맞춰져야 한다"] 기존 84~94는 S급
    # 2부(86~94, 스페인 세군다 등)보다도 낮은 상한이라 챔피언십이 오히려
    # 밀리는 역전이 있었다.
    # [2026-07 재수정, 신민용 리포트: "잉글랜드 2부가 1부랑 OVR 차이가
    # 아예 안 난다 — 다 90 이상이다"] 위에서 89~99로 올렸던 게 과했다 —
    # tier1(90~100)과 겹치는 폭이 너무 커서(사실상 -1 차이) 실질적으로
    # 구분이 안 됐다. 아래 STAR_COUNT_BY_GRADE 조정(스타 슬롯 축소)과
    # 함께, 상한을 92로 낮춰 "챔피언십 에이스급 = 80대 후반~90대 초반"
    # (실측 목표 평균 ≈88, 스페인 세군다 ≈80.7보다는 확실히 위,
    # 잉글랜드 1부(90~100)와는 명확히 갈리도록) 재조정.
    # [2026-07 버그수정, 신민용 리포트: "잉글랜드 3부 리그가 스페인 3부
    # 리그보다 낮다"] 확인해보니 3~6부 전 구간에서 SS(잉글랜드)가 S(프랑스/
    # 스페인/독일/이탈리아/브라질 공용)보다 낮게 잡혀 있었다 — 예:
    # 3부 SS(68~80) vs S(76~88), 6부 SS(28~40) vs S(44~56). 2부는 앞서
    # 여러 차례(위 주석 참고) 챔피언십 vs 세군다 문제로 조정된 적이
    # 있었지만, 그 아래 3~6부는 그 조정에서 함께 안 딸려 올라와서 예전
    # (더 낮았던) 값 그대로 남아있었다. SS 내부 서열(1부>2부>3부…)은
    # 그대로 유지하면서, 3~6부를 S의 해당 부수보다 확실히 위로 재조정한다.
    # [2026-08 신설, 신민용 확정: "10부까지 늘릴 수 있게 설계"] SS/S(현재
    # 실제로 6부까지 쓰이는 등급)에 7~10부를 명시적으로 채운다. 값은
    # database._tier_top_ovr / _generate_team_players / ai_lifecycle.
    # _retire_and_replace가 "표에 없는 tier"를 만났을 때 쓰던 자동 감쇠
    # 폴백(한 부수당 STEP=8씩 하락, 15 하한)과 정확히 동일한 수식으로
    # 계산했다 — 즉 동작은 이전과 100% 같고, 그 폴백에 의존하던 값을
    # 표에 명시적으로 고정해서 나중에 따로 손볼 수 있게 만든 것뿐이다.
    # A~F는 원래도 6부까지 못 채운 채로 저 폴백에 기대 왔고(A/B는 4부,
    # C~F는 3~4부까지만 정의) 여전히 안전하게 동작하므로 그대로 둔다 —
    # 하위 등급 국가가 실제로 7부 이상을 가질 일은 현실적으로 드물다.
    # [2026-08 재조정, 신민용 리포트: "잉글랜드에 비해 스페인/독일/프랑스/
    # 이탈리아 2부가 너무 높다 — 잉글 2부는 85가 평균인데 87~91이 나와야
    # 하고, 나머지 S(스페인/독일/프랑스/이탈리아) 2부는 90대 초반인데
    # 84~88로 내려가야 한다. 브라질은 그보다 한 단계 더 낮은 83~87"]
    # 실측(2016시즌 세이브): SS 2부 평균 85.9(목표 87~91), S 2부 평균
    # 91.1~91.6(목표 84~88, 브라질만 89.3→목표 83~87). SS는 위로,
    # S는 아래로 재조정. 브라질은 이미 CONTINENT_OVR_BONUS가 유럽(+1)
    # 대비 남미(0)라 S 재조정만으로 자연히 84~88보다 한 단계 낮은
    # 83~87대에 들어온다(실측으로 재확인 완료) — 별도 COUNTRY_OVR_ADJ는
    # 필요 없었다.
    # [2026-08 재조정, 신민용 확정: "챔피언십(2부) 우승권도 89~90은
    # 되어야 한다"] SS는 잉글랜드 전용 등급이라 여기(2부 상한)만 올려도
    # 다른 나라엔 영향이 없다. get_ovr_range()가 COUNTRY_LEAGUE_OVR_
    # OVERRIDE["잉글랜드"](88,98) 대비 이 tier1(90,100) 기본값의 델타(각
    # -2)를 그대로 이 tier2 값에도 적용하므로, 여기 상한을 95→100으로
    # 올리면 실제 잉글랜드 2부 상한은 93→98로 함께 올라간다(database.py의
    # STAR_STRENGTH_PENALTY_MAX_BY_GRADE["SS"], SS 2부 엘리트 슬롯 상한
    # 조정과 함께 실측 반영, 3부 이하는 그대로 유지).
    # [2026-08 재조정, 신민용 지적: "잉글 2부가 스페인/이탈리아/독일/
    # 프랑스 2부보다 하한은 너무 낮고(79) 상한은 1부와 같다(98) — 하한
    # 84대, 상한 91~92가 맞는듯"] get_ovr_range()가 COUNTRY_LEAGUE_OVR_
    # OVERRIDE["잉글랜드"](88,98) 대비 이 tier1(90,100) 기본값의 델타
    # (각 -2)를 그대로 tier2에도 적용하는 구조라, 여기 tier2를
    # (81,100)→(86,94)로 올리면 실제 잉글랜드 2부 범위가 (79,98)→
    # (84,92)가 된다 — 실측한 스페인(84,92)/이탈리아·독일(82,92)/
    # 프랑스(83,92)와 정확히 같은 상한(92)대로 맞춰짐.
    "SS":{1:(90,100),2:(86,94),3:(78,90),4:(66,78),5:(56,68),6:(46,58),
          7:(38,50),8:(30,42),9:(22,34),10:(15,26)},
    "S": {1:(85,96), 2:(80,90),3:(76,88),4:(62,73),5:(52,64),6:(44,56),
          7:(36,48),8:(28,40),9:(20,32),10:(15,24)},
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
    # [2026-08 신설, 신민용+GPT 검토: "오퍼 시스템 재설계 — OVR 80 이상을
    # 전부 동일하게(70/22/6/2/0) 취급하면 OVR80과 OVR100이 '어느 티어를
    # 시도해볼지'에서 구분이 안 된다. 7부에서 OVR95 같은 괴물급은 6부→
    # 5부→4부를 굳이 안 거치고 1부 후보로 바로 잡히는 게 더 자연스럽다"는
    # 논의 결과, 80대를 그대로 두고 90~94/95+ 두 구간을 추가로 쪼갠다.
    # 1부 비중을 100%에 가깝게 밀어붙이진 않는다(신민용 확정: "1부 100%는
    # 피해야 함") — 2부 이하로도 소량은 흘러가게 남겨서, 최종 통과 여부는
    # 여전히 _team_fits_me()의 실제 팀 수준 매칭에 맡긴다. 이 표는 '어느
    # 티어를 후보로 뽑아볼지'의 확률일 뿐, 오퍼 성사 자체를 보장하지 않는다.
    if ovr >= 95:   return [88, 10, 2,  0,  0]
    elif ovr >= 90: return [80, 16, 3,  1,  0]
    elif ovr >= 80: return [70, 22, 6,  2,  0]
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

# [2026-07 신설, 신민용 설계+확정] 인기도(popularity) = "최근 화제성" —
# 같은 활약(골/도움/평점)이라도 뛰는 리그 수준에 따라 실제로 얼마나
# 화제가 되는지는 다르다("K5에서 20골"과 "EPL에서 20골"은 다른 이야기).
# 국내리그 인기도 획득량에 곱하는 배수. 하위 리그라고 너무 심하게
# 깎으면(예: F=0.25배) 하부리그 선수는 아무리 잘해도 인기도가 거의 안
# 올라 답답해지므로, 최저를 0.45로 두어 격차를 과하게 벌리지 않는다.
LEAGUE_POP_MULT = {
    "SS": 1.40, "S": 1.25, "A": 1.10, "B": 1.00,
    "C": 0.90, "D": 0.75, "E": 0.60, "F": 0.45,
}
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
BALLON_DOR_GRADES = ("SS","S")  # SS=EPL, S=유럽 빅4(프랑스/스페인/독일/
# 이탈리아). [2026-09 수정] 브라질은 더 이상 여기 없음 — COUNTRY_LEAGUE_
# GRADE에서 S→A로 내려갔다(포르투갈과 동급). A급은 국제무대 증명이
# 있어야만 발롱도르 후보가 된다(_is_ballon_candidate 참고).
# [버그수정 2026-07, 신민용 지적: "발롱도르는 S급 리그에서만 받게 설계했는데
# A급도 받는 버그"] 원래 S등급 이상만 대상이었는데 A(대륙 메이저 — 한국
# K1, 멕시코, 미국, 일본 등)까지 후보에 끼어드는 상태였다. S 이상으로 되돌림.
# (푸스카스상 게이트(_PUSKAS_GRADES)와 동일하게 SS/S만 남긴다.)
# [2026-07 추가, 신민용 지적: "A급이라도 월드컵 같은 국제무대에서 캐리하면
# 발롱 받을 수 있게"] 이 상수 자체(SS/S만)는 안 바꾼다 — 대신
# game_engine.py의 _process_awards가 이 상수 밖(A급 이하) 선수도
# _major_stage_carry()(월드컵 결승급 활약/유로 우승 실제 증명)를
# 통과하면 별도 경로로 후보 자격을 준다. 즉 "A급이 자국리그 골만으로
# 후보가 되는 것"(위에서 고친 버그)과 "A급이 월드컵을 실제로 캐리해서
# 후보가 되는 것"은 다른 이야기 — 후자만 허용한다.

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



# ── 나이별 OVR 성장 곡선(잠재치 대비 비율) ──────────────────
# [2026-08 신설, 신민용 확정(GPT 협업 설계)] "16~17세에 이미 잠재치를
# 거의 다 찍는" 문제(생성 시점 스케일링이 신인 교체/스쿼드 보정 경로엔
# 아예 안 걸려있던 버그, ai_lifecycle.py 참고)를 계기로 나이별 성장
# 곡선 자체를 명시적인 표로 교체한다 — 기존 "16세 86~93%→22세 100%
# 선형보간" 대신, 나이마다 정확한 목표 비율을 지정하고 26세부터 완전
# 성숙(100%)으로 본다. database.py(_generate_team_players, 최초 생성)와
# ai_lifecycle.py(_retire_and_replace/_rebalance_squad_sizes, 신인 교체)
# 양쪽이 이 표 하나를 공유해야 두 생성 경로가 항상 일치한다.
AGE_OVR_FRACTION = {
    16: 0.70, 17: 0.74, 18: 0.78, 19: 0.83, 20: 0.90,
    21: 0.92, 22: 0.94, 23: 0.96, 24: 0.97, 25: 0.98,
}
AGE_OVR_FRACTION_MATURE_AGE = 26  # 이 나이부터 100%(스케일 없음)

# [2026-08 신설] 1% 확률의 "조기 성장형" 특급 유망주 — 같은 나이라도
# 일반 곡선보다 훨씬 높은 비율에서 시작해 26세 이전에 이미 정상급에
# 근접한다("16살인데 이미 프로가 주목하는 괴물" 케이스).
AGE_OVR_FRACTION_ELITE = {
    16: 0.81, 17: 0.83, 18: 0.85, 19: 0.88, 20: 0.91,
    21: 0.93, 22: 0.95, 23: 0.965, 24: 0.975, 25: 0.985,
}
AGE_OVR_ELITE_CHANCE = 0.01  # 신인/유망주 생성 시 이 표를 대신 쓸 확률


def roll_age_ovr_fraction(age: int) -> float:
    """이 나이의 선수가 잠재치(target) 대비 실제로 발현할 비율을 굴린다.
    26세 이상이면 스케일 없이 1.0(기존 동작과 동일). 1% 확률로 조숙형
    표를 쓰고, 표에서 나온 값에도 약간의 랜덤 오차(±2%p)를 얹어 같은
    나이라도 개인차가 자연스럽게 생기게 한다."""
    if age >= AGE_OVR_FRACTION_MATURE_AGE:
        return 1.0
    table = (AGE_OVR_FRACTION_ELITE if random.random() < AGE_OVR_ELITE_CHANCE
             else AGE_OVR_FRACTION)
    base = table.get(age)
    if base is None:
        # 16 미만(비정상 데이터 방어) 또는 표에 없는 나이는 가장 가까운
        # 정의된 값으로 클램프.
        nearest = min(table.keys(), key=lambda a: abs(a - age)) if age < 16 else 25
        base = table.get(nearest, table[25])
    return max(0.5, min(1.0, base + random.uniform(-0.02, 0.02)))


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

# [2026-07 신설, 신민용 설계+확정: "국가 명성이 아니라 국적에 따른 시장
# 접근성(market accessibility)"] 직접 지원 확률에 쓰는 "출신 대륙 →
# 목적 대륙" 이적 난이도 보정. 나라 단위(COUNTRY_TRANSFER)로 하나하나
# 만들면 관리가 힘들고 확장성도 떨어지므로, 이미 있는 대륙 구분
# (countries.continent / COUNTRY_CONTINENT)을 그대로 재사용해 "축구권"
# 단위로 묶는다 — 한국/일본/중국은 비슷하게, 브라질/아르헨티나도 하나로
# 관리된다.
# 같은 대륙 내 이동은 기본적으로 수월(SAME_CONTINENT_TRANSFER_BONUS).
# 대륙이 다르면 기본적으로 페널티(DEFAULT_CROSS_CONTINENT_PENALTY)를
# 주되, 실제 축구 이적 시장에서 이미 잘 뚫린 전통적 루트(남미→유럽,
# 아프리카→유럽 등 언어·과거 식민 연고 기반 스카우트망)는 페널티를
# 완화하거나 소폭 보너스를 준다. 반대로 아시아→유럽처럼 실제로 드문
# 루트는 기본 페널티보다 더 크게 준다.
SAME_CONTINENT_TRANSFER_BONUS = 4
DEFAULT_CROSS_CONTINENT_PENALTY = -6
TRANSFER_REGION_MOD = {
    ("남미", "유럽"):      +3,   # 브라질/아르헨티나 -> 포르투갈/스페인 등 전통 루트
    ("유럽", "남미"):      -9,   # 반대 방향은 훨씬 드묾
    ("아프리카", "유럽"):  -2,   # 프랑스/벨기에 등으로 이어지는 루트(과거 식민 연고)
    ("유럽", "아프리카"): -10,
    ("아시아", "유럽"):    -9,   # 한국/일본 -> 5대리그는 실제로 매우 드묾
    ("아시아", "남미"):   -10,
    ("북중미", "유럽"):    -7,
    ("오세아니아", "유럽"): -8,
}


def transfer_region_mod(my_continent: str, target_continent: str) -> float:
    """출신 대륙에서 목적 대륙으로 이적할 때의 시장 접근성 보정치."""
    if not my_continent or not target_continent:
        return 0.0
    if my_continent == target_continent:
        return SAME_CONTINENT_TRANSFER_BONUS
    return TRANSFER_REGION_MOD.get((my_continent, target_continent), DEFAULT_CROSS_CONTINENT_PENALTY)


# [2026-08 신설, 신민용+GPT 검토: "해외 자동 오퍼(_fill_foreign_pool)에는
# 대륙 보정이 전혀 안 걸려 있다 — 국가 등급만 보고 완전 무작위로 나라를
# 고른다"] transfer_region_mod()는 원래 직접 지원(calc_apply_prob_with_
# context) 전용으로 설계됐고, 그 값(+4/-6/-10 등)은 "마진에 더하는 점수"
# 스케일이다 — 이 숫자를 그대로 후보 선택 확률의 배수로 쓰면 안 된다
# (신민용 지적: "+4를 4배로 바꾸면 밸런스가 완전히 달라진다"). 그래서
# 마진용 스케일과 완전히 분리된, 후보 가중치 전용의 완만한 변환을 하나
# 더 둔다. 0.05/점 기울기 + [0.35, 1.5] 클램프로 잡아서, 같은 대륙(+4)은
# 약 1.2배, 아시아→유럽(-9)은 약 0.55배 수준의 "부드러운 쏠림"만
# 만든다 — 이걸로 오퍼 자체가 막히거나 폭증하지 않는다(최종 성사 여부는
# 여전히 _team_fits_me()의 실력 게이트가 결정).
REGION_WEIGHT_SLOPE = 0.05
REGION_WEIGHT_MIN = 0.35
REGION_WEIGHT_MAX = 1.5


def foreign_pool_region_weight(my_continent: str, target_continent: str) -> float:
    """_fill_foreign_pool 등 '해외 후보국 선택' 단계 전용 가중치 배수.
    transfer_region_mod()와 원본 정의(TRANSFER_REGION_MOD)는 그대로
    재사용하되, 결과값은 마진 점수가 아니라 0.35~1.5 사이의 배수로
    변환해서 돌려준다 — 직접 지원 쪽 마진 계산과는 완전히 독립적이다."""
    mod = transfer_region_mod(my_continent, target_continent)
    mult = 1.0 + mod * REGION_WEIGHT_SLOPE
    return max(REGION_WEIGHT_MIN, min(REGION_WEIGHT_MAX, mult))


# [2026-08 신설, 신민용+GPT 검토: "브라질처럼 해외로 선수가 많이 나가는
# 나라가 있다 — 근데 이걸 국가쌍 override 수십 개(브라질→포르투갈,
# 브라질→일본, 브라질→한국...)로 만들면 나중에 계속 늘어나기만 한다.
# 목적지 안 가리고 '이 나라 선수는 해외에 자주 노출된다'는 단일 배수
# 하나면 충분하다"] 국가쌍 테이블 대신 국가 단독 배수로 시작한다.
# [2026-08 확정, 신민용 지적: "이미 대륙가중치+티어가중치까지 겹치니
# 1.5배는 세다 — 1.15~1.25 정도로 보수적으로 시작해서 시뮬레이션 보고
# 조정하자"] 초기값은 구간 하단에 가깝게 잡는다. foreign_pool_region_
# weight()와는 완전히 별개의 곱셈 항으로 적용해야 한다 — 두 가중치를
# 한 변수에 합쳐버리면, 나중에 "브라질 선수가 왜 과도하게 해외로 나가나"
# 를 조사할 때 대륙 효과인지 수출국 효과인지 구분할 수 없게 된다.
EXPORT_MARKET_BONUS = {
    "브라질": 1.20,
    "아르헨티나": 1.15,
    "나이지리아": 1.15,
}


def export_market_weight(country: str) -> float:
    """해외 후보국 선택 시 곱하는 '선수 수출 성향' 배수. 목적지와 무관하게
    출신국 하나로만 결정 — foreign_pool_region_weight()와 독립적인 별도
    항이라 로그/디버깅 시 두 효과를 분리해서 볼 수 있다."""
    return EXPORT_MARKET_BONUS.get(country, 1.0)


# [신규] 나라별 OVR 미세조정 — 같은 등급(A 등) 안에서도 실제로는 재정·용병
#   수준 차이가 뚜렷한 나라들을 대륙 보정과 별개로 한 번 더 조정한다.
#   (신민용 요청: 오일머니/북미 자본 유입 리그는 위로, 한·일은 상대적으로
#    아래로 — A등급 안에서의 상/중/하위 구분을 대륙보정만으로는 다 못 잡음)
# [2026-07 전면 재설계, 신민용 실측 순위표(50개 리그) 기준] 등급(SS/S/A/B) 안에서도
# 실측 순위 상 세부 서열이 뚜렷하므로, 그 순서를 그대로 반영해 조정치를 다시 잡았다.
# A급 목표 순서(실측 7~15,17~20,23,28위): 포르투갈>네덜란드>벨기에>아르헨티나=터키
#   >사우디=미국>멕시코>일본=스위스>덴마크=오스트리아>스코틀랜드=체코
# B급 최상위 목표 순서(실측 16~30위 중 B급 편입국): 대한민국(24위, 아시아 페널티
#   보정 위해 가장 크게 +) > 폴란드=그리스=노르웨이(25~27위) > 크로아티아(30위)
#   =세르비아=우크라이나(36~37위, 신민용 지정에 따라 최상위권 유지)
COUNTRY_OVR_ADJ = {
    # [2026-09 재조정, 신민용 확정: "브라질 리그를 포르투갈 정도로 내려서
    # A급 최상으로, OVR도 지금보다 좀 내리고"] 브라질이 COUNTRY_LEAGUE_
    # GRADE에서 S급 → A급으로 내려간 것과 짝을 맞춘다 — 옛 -1(S등급
    # 내부에서 다른 S국가보다 살짝 낮춤 목적)은 등급 자체가 A로 내려간
    # 지금은 의미가 없다. 바로 아래 "A급 내부 서열" 포르투갈 항목(+7,
    # "A급 상한에 걸치게" 즉 A급 최상위로 의도적으로 설계된 값)과 정확히
    # 같은 값을 줘서 "포르투갈 정도"라는 요청을 그대로 반영 — 같은
    # +7이라도 대륙보정이 다르므로(포르투갈 유럽 +1, 브라질 남미 0)
    # 브라질이 자동으로 포르투갈보다 살짝 낮게 나온다(둘 다 A급 상한
    # 100 클램프 근처에 걸치므로 실질 차이는 미미).
    "브라질": +7,
    # ── A급 내부 서열 (2026-07 재조정, 신민용 목표표 기준 실측 역산) ──
    # [2026-07] 신민용이 새 목표표(SS~B급, 국가별 상/중/하 스쿼드 평균)를
    # 제시 — 실측 대비 A급 전체가 목표보다 낮고(+2.6~+3.8 부족),
    # B급 상위 클러스터가 목표보다 높아서(-3.9~-5.6 초과) 있었던 "A급
    # 하위권과 B급 상위권이 뒤섞이는" 문제를 이번에 함께 정리한다.
    "포르투갈":   +7,   # [2026-07 미세조정] A급 상한(94+대륙+조정치)이 100에서
                         # 클램프되므로, 포르투갈은 상한에 걸치게 두고 네덜란드·
                         # 벨기에는 그 아래로 내려서(-1) 실질적 차이를 만든다
                         # (조정치를 단순히 더 올려봐야 클램프에 막혀 무의미했음)
    "네덜란드":   +4,
    "벨기에":     +4,
    "아르헨티나": +3,   # [2026-07 미세조정] 국대는 세계 최강이지만 리그 자체
                         # 수준은 예전보다 내려왔다는 지적 반영(+4→+3)
    "튀르키예":   +3,
    "사우디아라비아": +6,  # PIF 오일머니로 슈퍼스타 다수 영입 (품질 자체도 A급 중상위)
    "미국":       +5,   # MLS 인프라+스타 영입(메시 등) 확대
    "멕시코":     +3,
    "일본":       +1,   # J리그
    "스위스":     -2,
    "덴마크":     -2,
    "오스트리아": -3,
    "스코틀랜드": -4,
    "체코":       -3,   # A급 최하단

    # ── B급 클러스터 (2026-07 재조정) ───────────────────────────
    # 실측상 목표(77~78 부근)보다 크게 높았던 상위 클러스터를 전부 하향.
    # [2026-07 재재조정] 신민용 지적: "한국을 B급 최상위(크로아티아 다음)로
    # 둔 건 리그 실력만 놓고 보면 다소 후한 평가 — 우루과이/콜롬비아/
    # 에콰도르/세르비아 1부가 K리그보다 우위라는 시각이 많다" — 한국을
    # 소폭 내리고 남미·세르비아 클러스터를 그만큼 올려 서열을 바꾼다.
    "대한민국":   1.5,   # (+2 → +1.5, 세르비아·우루과이 등보다 아래, 폴란드·
                          # 스웨덴·노르웨이보다는 위 유지)
    "폴란드":     -2,
    "그리스":     -3,
    "노르웨이":   -2,
    "스웨덴":     -2,
    "세르비아":   -1.5,  # (-2 → -1.5)
    "우크라이나": -2,
    "크로아티아": -1,
    "우루과이":   -0.5,  # (-1 → -0.5)
    "콜롬비아":   -0.5,  # (-1 → -0.5)
    "에콰도르":   -0.5,  # (-1 → -0.5)
    "러시아":     -2,
    "아랍에미리트": -1,
    "중국":       -1,

    # ── C급 미세조정 (2026-07) ──────────────────────────────────
    "이란":       +2,   # [신민용 지적] 페르시안걸프프로리그 — 태국·베트남보다
                         # 확실히 위 수준인데 저평가돼 있었음
    "우즈베키스탄": +1,  # 최근 AFC 대항전 성적 상승 추세 반영
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


def get_country_continent(country: str) -> str:
    """[2026-07 신설] 나라 이름 -> 대륙. transfer_region_mod()에 쓰기 위한
    공개 조회 헬퍼(기존 COUNTRY_CONTINENT는 지연 초기화라 직접 참조하면
    빈 dict를 볼 수 있어, 항상 이 함수를 통해 조회한다)."""
    _register_country_continents()
    return COUNTRY_CONTINENT.get(country, "")


def get_country_ovr_bonus(country: str, grade: str = None, continent: str = None) -> float:
    """나라 이름(+ 국대/리그 등급, 대륙) -> 최종 OVR 보정치(대륙 보정 +
    국가별 미세조정치 합산, SS등급은 초과 방지로 0 이하로 클램프).

    [2026-07 버그수정, 신민용 리포트: "월드컵/네이션스컵 기록이 없다"]
    intl_engine.py의 실제 호출부(_nat_team_ovr, 국가대표 발탁 판정)는
    이 함수를 (country, grade, continent) 3개 인자로 부르고 있었는데,
    바로 아래 docstring에 적혀 있던 "호환용 복구" 버전은 country 인자
    하나만 받도록 되어 있어서 — 부를 때마다
    'get_country_ovr_bonus() takes 1 positional argument but 3 were given'
    로 즉시 TypeError가 났다. 이 예외가 _qualify_world() 안에서 그대로
    터지면서 월드컵/대륙컵 생성 자체가 중간에 멈춰, 그 대회가 아예
    생성되지 않아 "역대 기록"에도 나타나지 않았던 것이다.
    ai_lifecycle.py/database.py에 있는 동일 공식(대륙보정+국가보정,
    SS등급은 0 이하로 클램프)과 맞춰 세 인자를 받도록 복구하고, 그
    두 파일에서 이미 한 번 잡았던 버그(COUNTRY_OVR_ADJ의 소수점 값이
    그대로 새어나가 다른 곳에서 random.randint에 float로 들어가 터짐)를
    여기서도 반복하지 않도록 round()로 정수화한다. continent를 안 넘기면
    국가명으로 직접 조회한다(과거 시그니처로 불러도 죽지 않게)."""
    if continent is None:
        continent = get_country_continent(country)
    bonus = round(CONTINENT_OVR_BONUS.get(continent, 0) + COUNTRY_OVR_ADJ.get(country, 0))
    if grade == "SS":
        bonus = min(bonus, 0)
    return bonus

_CONTINENT_MULT_MIN = {}   # 대륙별 "실제 지정된 국가들 중 최솟값" 캐시

def _register_continent_mult_min():
    """대륙별로 COUNTRY_SALARY_MULT에 개별 지정된 나라들의 최솟값을 미리
    계산해 캐시한다. [2026-07 버그수정, 신민용 지적: "슬로베니아가 스위스보다
    연봉이 높게 나옴"] CONTINENT_SALARY_MULT(유럽=1.00)로 그냥 폴백하면 —
    이 1.00은 잉글랜드(SS급) 기준값이라 — 개별 지정이 아예 없는 나라가
    실측 기반으로 낮게 잡힌 나라들(스위스 0.10, 오스트리아 0.08 등)보다
    오히려 더 유리해지는 역전이 발생했다. 대륙 평균이 아니라 그 대륙에서
    "가장 박한" 실측값을 미지정국의 기본값으로 쓰는 게 안전하다(별도
    지정이 없다는 건 데이터가 없다는 뜻이지, 부유하다는 뜻이 아니므로)."""
    if _CONTINENT_MULT_MIN:
        return
    _register_country_continents()
    for country, mult in COUNTRY_SALARY_MULT.items():
        continent = COUNTRY_CONTINENT.get(country)
        if continent is None:
            continue
        cur = _CONTINENT_MULT_MIN.get(continent)
        if cur is None or mult < cur:
            _CONTINENT_MULT_MIN[continent] = mult

def get_country_salary_mult(country: str) -> float:
    """나라별 연봉 배율 조회 — COUNTRY_SALARY_MULT(나라별 개별 지정) 우선,
    없으면 그 나라가 속한 대륙에서 실측 지정된 나라들 중 최솟값으로 폴백
    (CONTINENT_SALARY_MULT 대륙 평균 폴백은 미지정국을 과대평가하는 버그가
    있어 폐기 — 위 _register_continent_mult_min() 참고). 그마저 없으면
    CONTINENT_SALARY_MULT, 최종 안전망 1.0."""
    if country in COUNTRY_SALARY_MULT:
        return COUNTRY_SALARY_MULT[country]
    _register_continent_mult_min()
    _register_country_continents()
    continent = COUNTRY_CONTINENT.get(country)
    if continent in _CONTINENT_MULT_MIN:
        return _CONTINENT_MULT_MIN[continent]
    return CONTINENT_SALARY_MULT.get(continent, 1.0)

# ══════════════════════════════════════════════════════════════
# 리그 등급 (국대 FIFA 랭크와 완전 분리)
# countries.grade = 국대 강도 (월드컵 예선/대진 기준)
# COUNTRY_LEAGUE_GRADE = 클럽 리그 수준 (OVR 생성·연봉·오퍼 기준)
# ══════════════════════════════════════════════════════════════
COUNTRY_LEAGUE_GRADE = {
    # SS급 — EPL. 세계 자본의 정점, 연봉·선수 수준 모두 단독 최상위
    "잉글랜드": "SS",

    # S급 — 유럽 빅4
    # [2026-09 재조정, 신민용 확정: "브라질 리그를 포르투갈 정도로
    # 내려서 A급 최상으로 만드는 게 맞는듯"] 실제 발롱도르 최근 결과
    # (2024년 최종 30인 — 1위 로드리부터 공동 29위까지 전원 유럽 5대
    # 리그 소속, 브라질 리그 소속은 단 한 명도 없음)를 보면 브라질
    # 리그가 유럽 빅4와 같은 S급(자동 후보 자격)으로 묶여 있는 건 실측과
    # 어긋난다 — 아래 A급으로 내리고, A급 안에서는 최상위(포르투갈과
    # 동급)로 배치한다(OVR 재조정은 COUNTRY_LEAGUE_OVR_OVERRIDE/
    # COUNTRY_OVR_ADJ의 "브라질" 항목 참고 — 포르투갈과 동일 값으로
    # 맞춰 "포르투갈 정도로 내린" 의도를 그대로 반영). 발롱도르 게이트
    # (BALLON_DOR_GRADES=SS/S만 자동 통과)에도 그대로 반영돼, 이제
    # 브라질 리그 선수는 포르투갈 리그 선수와 마찬가지로 국제무대 증명
    # (월드컵/대륙컵/CL급 우승)이 있어야 발롱도르 후보가 된다.
    "프랑스": "S", "스페인": "S", "독일": "S", "이탈리아": "S",

    # A급 — 대륙별 최상위 메이저
    "아르헨티나": "A", "네덜란드": "A", "포르투갈": "A", "벨기에": "A",
    "브라질": "A",
    "멕시코": "A", "미국": "A", "일본": "A",
    # [2026-07 재조정, 신민용 지적] 호주 A리그는 Opta Power Rankings 등
    # 실측 순위표에서 세계 상위 15개 리그 안에 들지 못한다 — 네덜란드·
    # 포르투갈·멕시코·아르헨티나 같은 진짜 A급과는 격차가 크고, 강한
    # 샐러리캡 제도 때문에 재정 규모도 뚜렷이 작다. B급으로 하향.
    "사우디아라비아": "A", "튀르키예": "A",
    # [2026-07 재조정, 신민용 실측 순위표 기준] 대한민국은 A급 국가들
    # (포르투갈~멕시코, 7~15위권)과 순위가 크게 떨어져 있고(30위),
    # 오히려 현재 B급 국가들(스코틀랜드/노르웨이/크로아티아/스웨덴/
    # 그리스/세르비아/우크라이나, 16~29위) 사이에 낀다 — B급으로 하향.
    # 반대로 스위스/덴마크/오스트리아/스코틀랜드/체코(17~20,23,28위)는
    # 실측 순위표상 A급 최하위권(7~15위)과 거의 붙어있어 A급으로 상향한다.
    "스위스": "A", "덴마크": "A", "오스트리아": "A", "스코틀랜드": "A", "체코": "A",

    # B급 — 견고한 중상위 / 유망주 수출형
    "모로코": "B", "콜롬비아": "B", "크로아티아": "B", "우루과이": "B",
    "노르웨이": "B",
    "이집트": "B", "에콰도르": "B", "우크라이나": "B", "러시아": "B",
    "스웨덴": "B", "그리스": "B",
    # [2026-08 재조정, 신민용 확정: "이번 파워랭킹 밸런스 조정에 맞춰
    # 남아공을 B로 승격" — GLOBAL_PRESTIGE_STAR_CFG로 마멜로디 선다운즈
    # 목표 OVR(81~82)을 이미 맞춰둔 상태라, 등급 자체도 그에 맞게 올린다]
    "남아프리카공화국": "B",
    "호주": "B",
    # [2026-07 재조정, 신민용 실측 순위표 기준] 대한민국/세르비아/폴란드를
    # B급 최상위권으로 편입 (크로아티아/노르웨이/스웨덴/우크라이나와 함께).
    # 등급(품질) 기준으로만 조정 — 연봉은 COUNTRY_SALARY_MULT에서 그대로
    # 실측 목표치를 유지하도록 별도 재계산했다(등급 base가 바뀌므로).
    "대한민국": "B", "세르비아": "B", "폴란드": "B",
    # [2026-08 신설, 신민용 요청: "캐나다도 평균 OVR 82쯤으로, 등급도 B로"]
    # 포지 FC(캐나다 프리미어리그, 레벨3 명문)를 GLOBAL_PRESTIGE_STAR_CFG
    # (database.py)와 함께 처리.
    "캐나다": "B",
    # [2026-07 재조정, 신민용 지적] 나이지리아는 국가대표(해외파 위주)는
    # 강하지만, 국내리그(NPFL) 자체는 재정·인프라·관중동원 모두 취약해
    # 실제로는 이보다 약한 리그로 알려져 있다 — B급은 "국대 명성"이
    # 잘못 반영된 사례로 보여 C급으로 하향.

    # C급 — 대륙별 프로 안착 및 복병급
    "세네갈": "C", "이란": "C", "알제리": "C", "코트디부아르": "C",
    "파라과이": "C", "헝가리": "C", "카메룬": "C",
    "나이지리아": "C",
    "베네수엘라": "C", "칠레": "C", "페루": "C", "코스타리카": "C", "루마니아": "C",
    # [2026-08 재조정, 신민용 확정: "튀니지도 B로 승격, 1부 리그 OVR
    # 83대로" — 에스페랑스 드 튀니스는 CAF 챔피언스리그 최다 우승팀 중
    # 하나로 이집트·모로코와 비슷한 대우가 맞다고 판단, GLOBAL_PRESTIGE_
    # STAR_CFG(database.py)에도 같이 등록한다]
    "튀니지": "B",
    "우즈베키스탄": "C",
    # [2026-08 재조정, 신민용 확정: "OVR 67~74인데 A급은 너무 높다 —
    # 리그 실력은 C급인데 돈은 A급인 구조를 B로 표현하는 게 낫다"]
    "카타르": "B",
    "이라크": "C", "가나": "C",

    # D급 — 변방 프로 리그
    # [2026-07 밸런스 수정, 실측 데이터] 파나마는 ERI SalaryExpert 실측
    # 평균 연봉이 약 B/.19,412(≈1:1 달러 페그, 한화 약 2,500만원)로 나와서
    # 기존 D급 취급(396만원)보다 훨씬 높다 — 태국/베트남과 같은 패턴으로 C급 상향.
    "웨일스": "D", "파나마": "C", "콩고 민주 공화국": "D",
    # [2026-08 재조정, 신민용 확정: "연봉이 D치고 너무 높다(1094만~2846만,
    # C급 수준)"] 슬로바키아 D→C.
    "슬로바키아": "C", "말리": "D", "부르키나 파소": "D", "카보베르데": "D",
    "보스니아 헤르체고비나": "D", "온두라스": "D", "요르단": "D",
    # [2026-08 재조정, 신민용 확정: "OVR 65~70에 B는 과함, 카타르(B)와
    # 경제력 격차도 너무 크다"] UAE B→C.
    "아랍에미리트": "C", "북마케도니아": "D", "북아일랜드": "D", "자메이카": "D",
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
    "뉴질랜드": "D", "시리아": "D", "가봉": "D",
    # [2026-08 재조정, 신민용 확정] 불가리아도 슬로바키아와 동일 사유(연봉이
    # D치고 확실히 C급)로 D→C.
    "불가리아": "C", "앙골라": "D",
    "우간다": "D", "잠비아": "D", "바레인": "D",
    # [2026-08 재조정, 신민용+GPT 검토 확정] 중국 — "CSL 한때의 초고액
    # 외국인 영입"이 B급 근거였는데, 2021년 광저우 에버그란데 재정 파탄
    # 이후 리그 전체가 크게 위축됐다(2024년 광저우 해체, 대부분 외국인
    # 스타 이탈). 지금 시점 기준으로는 B급 근거가 더 이상 유효하지 않아
    # C급으로 하향(72~82 → 63~73).
    "중국": "C",
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
# [2026-08 신설, grade resolution 단일화] 예전엔 COUNTRY_LEAGUE_GRADE에 없는
# 국가(211개국 중 미등록 다수)를 처리하는 코드 경로마다 서로 다른 fallback을
# 썼다 — 초기 시딩(database.py)은 국대 등급, ai_lifecycle.py의 성장/노화는
# "F", 은퇴자 교체는 "D", 이적시장 대상팀은 "C", 이적시장 랭킹은 fallback
# 자체가 없어 None. 같은 미등록 국가가 시즌 처리 단계마다 완전히 다른 리그
# 수준으로 취급되는 구조적 버그였다. 이제 모든 경로가 get_country_league_grade()
# 하나만 쓴다 — fallback도 "국대 등급"으로 통일(자국 리그 데이터가 없는
# 나라는 국대 실력을 최선의 추정치로 삼되, 사우디처럼 리그와 국대 수준이
# 체계적으로 다르다고 판단되면 COUNTRY_LEAGUE_GRADE에 명시 등록해서
# 이 fallback을 덮어쓰면 된다 — fallback은 어디까지나 데이터 없는 국가를
# 위한 임시 추정치이지 영구적 원칙이 아니다).
_COUNTRY_FIFA_RANK: dict = {}


def _register_country_fifa_ranks():
    """COUNTRY_DATA(국가,국기,대륙,언어,fifa_rank)에서 국가→fifa_rank 맵을 만든다.
    지연 임포트로 순환 임포트를 피한다(_register_country_continents와 동일 패턴)."""
    if _COUNTRY_FIFA_RANK:
        return
    from data.countries import COUNTRY_DATA
    for name, _flag, _cont, _lang, rank in COUNTRY_DATA:
        _COUNTRY_FIFA_RANK[name] = rank


def _grade_from_rank(rank: int) -> str:
    """fifa_rank -> 국대 등급. database.py._grade_from_rank와 동일 산식
    (그쪽은 이 함수를 그대로 import해서 쓰도록 통일 — 산식 이원화 방지)."""
    if rank <= 10: return "S"
    if rank <= 25: return "A"
    if rank <= 50: return "B"
    if rank <= 80: return "C"
    if rank <= 120: return "D"
    if rank <= 160: return "E"
    return "F"


def get_country_grade(country: str) -> str:
    """국가명 -> 국대 등급(FIFA 랭킹 기반). countries.grade 컬럼과 동일 산식이라
    DB 조회 없이도 같은 값을 얻을 수 있다(리그등급 fallback에 필요)."""
    _register_country_fifa_ranks()
    rank = _COUNTRY_FIFA_RANK.get(country, 999)
    return _grade_from_rank(rank)


def get_country_league_grade(country_name: str, national_grade: str = None) -> str:
    """국가명 -> 리그 전용 등급. 모든 호출부가 이 함수 하나만 써야 한다.
    우선순위: 1) COUNTRY_LEAGUE_GRADE 명시 등록  2) 호출부가 이미 알고 있는
    national_grade(중복 조회 방지용, 있으면 재사용)  3) get_country_grade()로
    새로 계산한 국대 등급. 3번 경로로 fallback된 값은 "확정된 리그 실력"이
    아니라 데이터가 없을 때의 임시 추정치임을 유의 — 이 함수가 F가 아닌
    실제 국대 등급을 리턴하는 경우가 있다면, COUNTRY_LEAGUE_GRADE 명시
    등록을 검토할 후보라는 신호다."""
    if country_name in COUNTRY_LEAGUE_GRADE:
        return COUNTRY_LEAGUE_GRADE[country_name]
    return national_grade or get_country_grade(country_name)


# ══════════════════════════════════════════════════════════════
# 국가별 '리그 득점 환경' 계수 — game_engine._estimate_ai_season의
# 득점/도움 기대치에 곱해지는 배율
# ══════════════════════════════════════════════════════════════
# [2026-09 신설, 신민용 요청: "EPL 85 OVR ST와 베트남 85 OVR ST는 같은
# 85라도 실제 득점 기대치는 다르게 만들 수 있어 — 국가별로 리그 득점
# 계수를 하나 두는 걸 추천해"] 지금까지 _estimate_ai_season의 득점/도움
# 기대치는 포지션 고정 기준치(AWARD_POS_GOAL/ASSIST)+소속팀 전력차만
# 반영했지, "그 리그 자체의 득점 환경"(수비 조직력·경기 강도·타이트함)은
# 전혀 반영하지 않았다 — 그 결과 K리그1 ST와 EPL ST가 같은 OVR이면 거의
# 같은 득점 기대치를 받는 구조였다.
#
# COUNTRY_OVR_ADJ/COUNTRY_SALARY_MULT와 완전히 같은 패턴: 유저가 준 표를
# 그대로 국가별 명시 등록하고, 표에 없는 나머지 국가는 COUNTRY_LEAGUE_
# GRADE 등급별 기본값(LEAGUE_GRADE_GOAL_MULT)으로 폴백한다.
#
# 값의 의미: 1.00 = EPL/라리가 기준. 낮을수록 같은 포지션·같은 소속팀
# 전력이어도 득점/도움 기대치가 낮게 나온다. 득점왕 절대치를 좌우하는
# 다른 요소(AWARD_POS_GOAL, 팀 전력차, 경기수 스케일)는 그대로 두고 이
# 계수 하나만 추가로 곱하는 방식이라 기존 밸런스를 크게 흔들지 않는다.
# [신민용 강조: "Tier 4라고 득점왕을 무조건 낮게 제한하면 안 된다"] —
# 이 계수는 기대값(평균)만 낮출 뿐, _estimate_ai_season이 이미 쓰는
# random.uniform(0.8,1.2) 변동폭은 그대로 유지되므로 약체 리그에서도
# 특정 시즌 강팀 에이스가 몰아쳐 20골 안팎을 찍는 변동성은 그대로 남는다.
LEAGUE_GRADE_GOAL_MULT = {
    "SS": 1.00, "S": 0.90, "A": 0.72, "B": 0.58,
    "C": 0.48, "D": 0.40, "E": 0.34, "F": 0.28,
}
COUNTRY_GOAL_ENV_MULT = {
    # Tier 1 — 유럽 최상위 5대리그 (유저 제시표 그대로)
    "잉글랜드": 1.00, "스페인": 1.00, "독일": 0.95, "이탈리아": 0.90, "프랑스": 0.88,
    # Tier 2 — 중고득점 메이저
    "브라질": 0.78, "일본": 0.75,
    # Tier 3 — 중간
    "대한민국": 0.65, "멕시코": 0.65, "미국": 0.65,
    "이집트": 0.60, "모로코": 0.60, "남아프리카공화국": 0.60,
    "알제리": 0.58, "튀니지": 0.58,
    # Tier 4 — 낮음 (기대값만 낮춤 — 변동성은 그대로, 위 주석 참고)
    "베트남": 0.57, "나이지리아": 0.48, "가나": 0.48,
    "세네갈": 0.45, "코트디부아르": 0.48,
}


def get_goal_env_mult(country_name: str, league_grade: str = None) -> float:
    """국가명 -> 리그 득점 환경 배율. 우선순위: 1) COUNTRY_GOAL_ENV_MULT
    명시 등록  2) LEAGUE_GRADE_GOAL_MULT[등급](등급은 get_country_league_
    grade()로 조회, 호출부가 이미 알고 있으면 league_grade로 재사용해
    중복 조회 방지). country_name이 없으면(호출부가 국가 정보를 못 구한
    경우) 안전하게 1.0(무보정)을 반환한다 — 데이터 공백 때문에 엉뚱하게
    낮은 배율이 적용되는 것을 막기 위함."""
    if not country_name:
        return 1.0
    if country_name in COUNTRY_GOAL_ENV_MULT:
        return COUNTRY_GOAL_ENV_MULT[country_name]
    grade = league_grade or get_country_league_grade(country_name)
    return LEAGUE_GRADE_GOAL_MULT.get(grade, 0.45)


# [2026-08 신설] 국가별 리그 1부 OVR 오버라이드. COUNTRY_LEAGUE_GRADE(등급
# 문자)는 연봉 배율·연봉 상한·명문팀 보너스 등 다른 시스템과 얽혀 있어서
# 그대로 두고, "1부 리그에 실제로 존재하는 선수 OVR 범위"만 별도로 다시
# 잡고 싶을 때 쓴다 — 등급은 A인데 실제 스쿼드 깊이는 그보다 얕은 나라
# (오일머니로 상위 몇 클럽만 집중 투자되는 리그, 외국인 투자로 리그 전체가
# 부풀려진 나라 등)를 위한 것. tier1에만 적용되고, tier2 이하는 원래
# grade별 OVR_RANGES를 그대로 쓴다(오버라이드 대상 밖).
#
# 카타르 (72, 76): QSL 실측 — 최고 선수 시장가치가 리그 전체에서 약
#   1,200만 유로 수준에서 급격히 400~100만 유로대로 떨어짐(Transfermarkt,
#   2026-08 확인). A등급 표준(82~94)을 적용하기엔 리그 깊이가 얕다.
# 사우디아라비아 (80, 88): SPL 실측 — 상위 4강(알힐랄·알나스르·알이티하드
#   등, PIF가 2023년 지분 인수)과 하위팀(알아크두드 등, 총 스쿼드가치
#   약 595만 유로) 사이 스쿼드 가치 격차가 약 25배로 극단적 상위 집중형
#   (Transfermarkt, 2026-08 확인). 82~94 그대로 적용하면 리그 전체 깊이가
#   과대평가됨 — 등급(A)은 유지하되 범위만 낮춤. [신민용 확정, 잠정값 —
#   추후 UAE/중국까지 비교 검증 후 재확정 여지 있음]
# 미국 (80, 88): MLS는 샐러리캡 구조상 사우디처럼 극단적 상위 집중은
#   아니지만, 유럽 A급 리그(포르투갈 등)와 동일한 82~94를 줄 만큼의
#   리그 전체 깊이는 아니라고 판단 — 사우디와 동일 구간으로 잠정 확정.
#   [신민용 확정, 잠정값 — 두 나라의 투자구조(상위집중형 vs 샐러리캡형)가
#   달라 같은 범위를 쓰는 게 최종적으로 맞는지는 추후 재검토 여지 있음]
COUNTRY_LEAGUE_OVR_OVERRIDE = {
    # [2026-08 재조정, 신민용 확정 — 국가별 OVR 분포 밸런스 재조정안]
    # 이전 세션에서 "Big5 상위권을 잉글랜드 최상위와 동등하게" 목적으로
    # 스페인/이탈리아/독일/프랑스 상한을 전부 100으로 올렸었는데, 그 결과
    # 하위권까지 93~94로 뭉치는 반대 문제가 생겼다는 리포트를 받고 재설계.
    # 이번엔 "천장은 적당히(97~98), 대신 나라마다 하한을 세분화"하는
    # 방향으로 13개국을 한 번에 재조정한다. 하한/상한 두 값만 바꾸는
    # 1단계이고, 실제 생성 분포(평균·순위 구간별 평균)는 별도로 측정해
    # 확인한다 — TEAM_ROLE_PROFILE 등 공용 곡선은 이번엔 건드리지 않음
    # (범위만으로 목표 평균이 안 나오는 나라가 확인되면 그때 2차 조정).
    "카타르": (67, 74),
    # [2026-08 신설, 신민용 요청: "일본 85대로, 사우디 86으로"] 각각
    # 목표를 한 단계씩 더 올린다.
    "사우디아라비아": (83, 90),
    "일본": (78, 86),
    # [2026-08 재조정, 신민용+GPT 교차검토 합의 — Opta 2026 국대랭킹(아르헨\
    # 티나 2위/미국 15위/일본 18위 등)을 "클럽과 국대는 별개 척도"라는 전제\
    # 하에 상대 위계 참고자료로만 사용, 1차 목표 평균(간판 클럽 기준):\
    # 브라질92~93(유지) > 아르헨티나89 > 미국87 > 이집트·모로코86 ≈ 사우디85\
    # > 일본83~84 > 남아공81~82 > 한국75. 아래 값들은 실측(5회 평균)으로\
    # 이 목표에 맞춘 1차 조정치 — GLOBAL_PRESTIGE_STAR_CFG(database.py)와\
    # 함께 작동한다.]
    "미국": (81, 91),        # [3차 실측 88.11→소폭 하향] 목표: 간판(LA갤럭시 L2) 평균 87
    "포르투갈": (86, 94),
    "네덜란드": (82, 94),
    "벨기에": (81, 93),
    "아르헨티나": (84, 93),  # 목표: 간판(리버/보카/인데펜) 평균 89
    "말레이시아": (50, 56),
    "베트남": (52, 58),
    "태국": (54, 60),
    "인도네시아": (50, 56),
    "아제르바이잔": (53, 58),
    "키프로스": (55, 60),
    "벨라루스": (52, 58),
    "룩셈부르크": (53, 59),
    "남아프리카공화국": {1: (65, 82), 2: (56, 70), 3: (45, 59)},  # [3차 실측 83.30→소폭 하향] 목표: 간판(마멜로디) 평균 81~82
    "아랍에미리트": (65, 70),
    "중국": (55, 61),
    "튀르키예": (93, 94),
    # [2026-08 신설] 목표: 간판(알 아흘리/자말렉) 평균 86. 일반 이집트
    # 리그 팀들도 이 상한대로 소폭 오르지만, 실제 도약분 대부분은
    # GLOBAL_PRESTIGE_STAR_CFG(prestige_level>=2 전용)가 담당한다.
    "이집트": {1: (68, 87), 2: (60, 74), 3: (50, 64)},  # [3차 실측 87.91→소폭 하향]
    # [2026-08 신설] 목표: 간판(위다드/라자) 평균 86. 이집트와 동일 논리.
    "모로코": {1: (68, 85), 2: (60, 74), 3: (50, 64), 4: (39, 53)},  # [3차 실측 87.42→소폭 하향; 2026-09 신민용 확정 85로 하향]
    # [2026-08 신설, 신민용 요청: "튀니지 1부 OVR 83대로"] 이집트·모로코와
    # 동일 패턴(등급 B + GLOBAL_PRESTIGE_STAR_CFG)으로 처리.
    "튀니지": {1: (66, 86), 2: (59, 73), 3: (48, 62)},  # [1차 실측 81.25→소폭 상향]
    # [2026-08 신설, 신민용 지적: "한국이 지금 70~80인데 75대로 내려야
    # 한다"] 한국은 기존 override가 없어 OVR_RANGES["B"][1](82)+대륙보정
    # 으로 tier_top≈80.5가 잡혀 있었다 — 목표(간판 평균 75, 상위팀
    # 76~79)에 맞춰 상한을 낮춘다.
    "대한민국": {1: (58, 74), 2: (50, 62), 3: (40, 52), 4: (28, 42)},  # [1차 실측 76.39→소폭 하향]
    # [2026-08 신설, 신민용 지적: "일본도 85대로 내려야 한다" → 이후 GPT
    # 교차검토에서 사우디(85)를 아시아 기준점으로 삼고 일본은 그보다
    # 한 단계 아래(83~84)로 재조정] 기존 override(85,90)가 평균 88.78을
    # 만들고 있었다 — 상한을 낮춰 목표(간판 평균 83~84, 상위팀 84~86)에
    # 맞춘다.
    "스페인": {1: (90, 99), 2: (82, 90), 3: (74, 84), 4: (64, 74), 5: (53, 63)},
    # [2026-08 재조정, 신민용 확정: "이탈리아·독일도 잉글랜드·스페인·
    # 프랑스와 같은 95대로 나와야 한다"] 위 star_prestige_bonus S전용
    # +2.0 제거 이후 실측(6회 평균)해보니 이탈리아·독일 레벨3 명문팀만
    # 유독 94.63으로 처지고 있었다 — 원인은 이 표의 tier_top이 이탈리아
    # ·독일만 97이고 잉글랜드·스페인·프랑스는 98이라는 1점 차이 그
    # 자체였다(무작위 편차가 아니라 매 판 동일하게 적용되는 구조적 차이 —
    # worldclass/elite 목표식이 이 tier_top에서 그대로 -1 되므로 재현성
    # 100%). "이 넷을 사실상 동급으로 두자"는 방향에 맞춰 상한을 98로
    # 맞춘다.
    # [2026-08 재조정, 신민용 지적: "스페인 3부(80~90)가 5개국 중 유독
    # 하한이 높다 — 잉글·이탈·독일·프랑스와 나란히 2부 중위권 밑으로
    # 내려와야 한다"] 델타 전파 방식(튜플)은 나라마다 tier1 하한이
    # 미묘히 달라(스페인 89 vs 이탈리아·독일 87) 하위 부수까지 나라별로
    # 정확히 맞추기 어려웠다 — 빅5 5개국은 부수별 값을 직접 지정하는
    # 딕셔너리 형태로 바꿔서, "2부 중위권보다는 낮게, 인접 나라들과는
    # 나란히" 정확히 재현한다. 5개국 모두 이 원칙(그 부수 상한 < 한 단계
    # 위 부수 중위값)을 만족하는지 확인 완료.
    "이탈리아": {1: (89, 98), 2: (81, 89), 3: (73, 83), 4: (63, 73), 5: (52, 62)},
    "독일": {1: (88, 98), 2: (81, 89), 3: (73, 83), 4: (63, 73), 5: (52, 62)},
    "프랑스": {1: (87, 98), 2: (82, 90), 3: (74, 84), 4: (64, 74), 5: (53, 63)},
    # [2026-09 재조정, 신민용 확정: "브라질 리그를 포르투갈 정도로 내려서
    # A급 최상으로, OVR도 지금보다 좀 내리고"] 브라질이 COUNTRY_LEAGUE_
    # GRADE에서 S급 → A급으로 내려가면서, 이 표도 옛 S급 기준 5부수
    # 직접지정(형태 2) 대신 포르투갈과 똑같은 [형태 1] (하한,상한)
    # 튜플로 바꿨다 — get_ovr_range()가 이 tier1 값과 grade(A) 기본표
    # (OVR_RANGES["A"])의 델타를 하위 부수에도 그대로 전파하므로, 2부
    # 이하도 포르투갈과 같은 상대적 위치로 자연히 낮아진다(예전 2부
    # 79~89 → 델타 전파로 약 77~85권). 하위 부수(에스토두알 2부/3부 등)는
    # OVR_RANGES["A"]가 4부까지만 정의돼 있어, 그보다 깊은 부수는
    # get_ovr_range()의 자동 STEP 감쇠 폴백이 이어서 처리한다(문서 참고).
    "브라질": (86, 94),
    # [2026-09 재조정, 신민용 확정: "잉글랜드/스페인은 상한 99, 이탈리아/독일/
    # 프랑스는 98이 맞다"] 이탈리아·독일·프랑스는 기존 98 유지, 잉글랜드·
    # 스페인만 tier1 상한을 98→99로 1점 상향. tier2 이하와 하한은 손대지
    # 않음. data/prestige_clubs.py(명문팀별 개별 설정)는 이 조정과 무관하게
    # 그대로 둔다.
    "잉글랜드": {1: (91, 99), 2: (85, 92), 3: (76, 86), 4: (64, 74), 5: (53, 63)},
    "멕시코": (81, 91),  # [신민용 요청: "미국이랑 비슷하게"] 미국(81,91)과 동일하게 맞춤
    # [2026-08 신설, 신민용 요청: "캐나다도 평균 OVR 82쯤으로"]
    # [2026-08 재조정, 신민용 지적: "튜플(60,85) 델타 전파가 2부(54~77)/
    # 3부(43~66)를 20점 넘게 벌려놓는다"] data/leagues.py로 실제 리그
    # 개수 확인(캐나디안 프리미어리그/리그1 캐나다/캐나다 내셔널 아마추어
    # 리그 — 딱 3부까지만 실존, 4부 이상 데이터 없음) 후 부수별 직접
    # 지정으로 전환.
    "캐나다": {1: (60, 85), 2: (55, 69), 3: (46, 58)},
    "스코틀랜드": (78, 89),
}


def get_ovr_range(grade: str, tier: int, country: str = None):
    """등급+tier(+국가) -> (하한, 상한) OVR 범위. country가
    COUNTRY_LEAGUE_OVR_OVERRIDE에 있으면 값 형태에 따라 두 가지로 동작한다:

    [형태 1] (하한,상한) 튜플 — tier1 기준 델타 전파 방식(기존):
      - tier==1: 오버라이드 값을 그대로 사용.
      - tier>=2: [2026-08 버그수정, 신민용 리포트: "K1 OVR을 내렸더니 K2랑
        겹친다(일본도 동일)"] 예전엔 오버라이드가 tier1에만 적용되고
        tier2 이하는 grade 기본표(OVR_RANGES)를 그대로 썼다 — 그래서 tier1을
        내려도 tier2는 안 따라 내려가 두 리그가 겹치거나(심하면 역전)
        되는 문제가 있었다. 이제는 "오버라이드가 grade 기본 tier1 대비
        얼마나 이동했는지(delta)"를 그대로 tier2 이하에도 적용한다 — 1부를
        내리면 하위 리그도 같은 폭만큼 같이 내려가는 자연스러운 피라미드
        구조가 자동으로 유지된다. grade 표에 그 tier가 아예 없으면(예:
        B등급엔 5부가 없음) _tier_top_ovr()과 동일한 STEP 감쇠로 추정한
        기본값에 delta를 적용한다.

    [형태 2] {tier: (하한,상한), ...} 딕셔너리 — 부수별 직접 지정
      (2026-08 신설, 신민용 지적: "빅5는 같은 S/SS 등급 기본표를 공유하는데
      나라마다 1부 하한이 미묘하게 달라서(스페인 89 vs 프랑스 88), 델타
      전파만으로는 나라별 2~5부 목표치를 정확히 재현할 수 없다 — 예를 들어
      스페인·프랑스는 2부 이하 목표가 완전히 같아야 하는데 델타가 서로
      달라서 결과가 미묘하게 갈라짐"). 이런 나라는 tier마다 값을 직접
      박아둔다 — 딕셔너리에 그 tier가 있으면 그 값을 그대로 쓰고, 없는
      tier(예: 6부 이상)만 tier1 값을 기준으로 형태 1과 동일한 델타
      전파로 보완한다.

    오버라이드가 없는 국가/tier는 원래 grade별 OVR_RANGES를 그대로 반환한다."""
    grade_ranges = OVR_RANGES.get(grade, {})
    if country and country in COUNTRY_LEAGUE_OVR_OVERRIDE:
        override = COUNTRY_LEAGUE_OVR_OVERRIDE[country]
        if isinstance(override, dict):
            if tier in override:
                return override[tier]
            o_lo, o_hi = override.get(1, grade_ranges.get(1, (30, 30)))
        else:
            o_lo, o_hi = override
            if tier == 1:
                return (o_lo, o_hi)
        base_t1 = grade_ranges.get(1)
        if base_t1:
            base_tn = grade_ranges.get(tier)
            if base_tn is None and grade_ranges:
                deepest_tier = max(grade_ranges)
                deepest_lo, deepest_hi = grade_ranges[deepest_tier]
                STEP = 8
                extra = tier - deepest_tier
                base_tn = (max(15, deepest_lo - extra * STEP),
                           max(15, deepest_hi - extra * STEP))
            if base_tn:
                delta_lo = o_lo - base_t1[0]
                delta_hi = o_hi - base_t1[1]
                new_lo = max(10, base_tn[0] + delta_lo)
                new_hi = max(new_lo + 4, base_tn[1] + delta_hi)  # 최소 폭 보장(역전 방지)
                return (new_lo, min(100, new_hi))
    return grade_ranges.get(tier)



def get_league_grade(country_name: str, fallback_grade: str = "F") -> str:
    """[구버전 호환용, 신규 코드는 get_country_league_grade() 사용 권장]
    국가명 → 리그 전용 등급. COUNTRY_LEAGUE_GRADE에 없으면 fallback_grade 사용."""
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
    # [2026-08 대규모 재조정, 신민용 제공 실측 데이터 기반] 아래 국가군은
    # 유저가 제공한 최신 리그별 최저/평균/최고 연봉표(원화 기준)에 맞춰
    # (실제 _calc_salary 함수로 이진탐색해) mult를 역산했다. 등급
    # (COUNTRY_LEAGUE_GRADE)은 절대 건드리지 않았다 — 신민용 확정: 등급은
    # 팀 실력(OVR)까지 같이 결정하는 값이라, 연봉만 보고 등급을 올리면
    # 실력 판단까지 왜곡되기 때문. 미설정국은 get_country_league_grade()의
    # FIFA랭킹 기반 폴백 등급을 그대로 기준으로 삼았다.
    "가나": 0.1177,  # [2026-08 재조정]
    "그리스": 2.0448,  # [2026-08 재조정]
    "나이지리아": 0.206,  # [2026-08 재조정]
    "남아프리카공화국": 0.5887,  # [2026-08 재조정]
    "네덜란드": 0.1761,
    "노르웨이": 0.6005,  # [2026-08 재조정]
    "대한민국": 0.9737,  # [2026-08 재조정]
    "덴마크": 0.1101,  # [2026-08 재조정]
    "독일": 0.113,
    "라트비아": 0.6414,  # [2026-08 재조정]
    "러시아": 0.257,
    "레바논": 0.1264,  # [2026-08 재조정]
    "루마니아": 0.8831,  # [2026-08 재조정]
    "룩셈부르크": 0.3532,  # [2026-08 재조정]
    "리투아니아": 0.7215,  # [2026-08 재조정]
    "말라위": 0.0257,
    "말레이시아": 0.2,
    "멕시코": 0.1541,
    "모로코": 0.2272,  # [2026-08 재조정]
    "몬테네그로": 0.1895,  # [2026-08 재조정]
    "미국": 0.1321,
    "미얀마": 0.1605,  # [2026-08 재조정]
    "베네수엘라": 0.2944,  # [2026-08 재조정]
    "베트남": 0.1766,  # [2026-08 재조정]
    "벨기에": 0.1321,
    "벨라루스": 0.3532,  # [2026-08 재조정]
    "보스니아 헤르체고비나": 0.3159,  # [2026-08 재조정]
    "볼리비아": 0.4421,  # [2026-08 재조정]
    "북마케도니아": 0.2211,  # [2026-08 재조정]
    "북아일랜드": 0.4421,  # [2026-08 재조정]
    "불가리아": 0.471,  # [2026-08 재조정]
    "브라질": 0.0904,  # [2026-08 재조정]
    "사우디아라비아": 200.0,  # [2026-08 재조정]
    "세네갈": 0.1177,  # [2026-08 재조정]
    "세르비아": 0.4869,  # [2026-08 재조정]
    "스웨덴": 0.4869,  # [2026-08 재조정]
    "스위스": 0.0352,
    "스코틀랜드": 0.2114,  # [2026-08 재조정]
    "스페인": 0.1356,
    "슬로바키아": 0.5298,  # [2026-08 재조정]
    "슬로베니아": 0.4121,  # [2026-08 재조정]
    "싱가포르": 0.3208,  # [2026-08 재조정]
    "아랍에미리트": 2.3548,  # [2026-08 재조정]
    "아르메니아": 0.3159,  # [2026-08 재조정]
    "아르헨티나": 0.1761,  # [2026-08 재조정]
    "아이슬란드": 0.471,  # [2026-08 재조정]
    "아일랜드": 0.2944,  # [2026-08 재조정]
    "아제르바이잔": 0.325,
    "알바니아": 0.2649,  # [2026-08 재조정]
    "알제리": 0.4121,  # [2026-08 재조정]
    "에콰도르": 0.4869,  # [2026-08 재조정]
    "오만": 0.4421,  # [2026-08 재조정]
    "오스트리아": 0.1101,  # [2026-08 재조정]
    "요르단": 0.2526,  # [2026-08 재조정]
    "우간다": 0.0514,
    "우루과이": 0.3895,  # [2026-08 재조정]
    "우즈베키스탄": 0.471,  # [2026-08 재조정]
    "우크라이나": 0.1028,
    "웨일스": 0.2526,  # [2026-08 재조정]
    "이라크": 0.3532,  # [2026-08 재조정]
    "이란": 0.7064,  # [2026-08 재조정]
    "이스라엘": 1.4718,  # [2026-08 재조정]
    "이집트": 0.2597,  # [2026-08 재조정]
    "이탈리아": 0.0904,
    "인도": 0.4009,  # [2026-08 재조정]
    "인도네시아": 0.2355,  # [2026-08 재조정]
    "일본": 0.1541,  # [2026-08 재조정]
    "잉글랜드": 0.2623,
    "자메이카": 0.0257,
    "조지아": 0.2944,  # [2026-08 재조정]
    "중국": 1.4718,  # [2026-08 재조정]
    "체코": 0.0837,  # [2026-08 재조정]
    "칠레": 0.8831,  # [2026-08 재조정]
    "카메룬": 0.1177,  # [2026-08 재조정]
    "카타르": 1.6229,  # [2026-08 재조정]
    "코소보": 0.2526,  # [2026-08 재조정]
    "코스타리카": 0.0186,
    "코트디부아르": 0.1472,  # [2026-08 재조정]
    "콜롬비아": 0.4869,  # [2026-08 재조정]
    "콩고 민주 공화국": 0.1264,  # [2026-08 재조정]
    "크로아티아": 0.0385,
    "키프로스": 0.325,
    "태국": 0.2355,  # [2026-08 재조정]
    "튀니지": 0.2944,  # [2026-08 재조정]
    "튀르키예": 0.7045,  # [2026-08 재조정]
    "파나마": 0.145,
    "파라과이": 0.471,  # [2026-08 재조정]
    "페루": 0.471,  # [2026-08 재조정]
    "포르투갈": 0.1761,
    "폴란드": 0.7465,  # [2026-08 재조정]
    "프랑스": 0.0791,
    "핀란드": 0.471,  # [2026-08 재조정]
    "필리핀": 0.1605,  # [2026-08 재조정]
    "헝가리": 1.354,  # [2026-08 재조정]
    "호주": 0.095,
    "홍콩": 0.2406,  # [2026-08 재조정]
}

# ── 나라별 연봉 상한 (COUNTRY_SALARY_CAP, 천원/년) ──────────────────────
# OVR90+ 극소수 탑 선수에서 걸리도록 설정.
# OVR65~85 구간은 COUNTRY_SALARY_MULT × base_year × mult(ovr)로 자연 증가.
# 공식: cap ≈ base × cont_mult × mult(90) (OVR90 수준에서 cap 도달)
COUNTRY_SALARY_CAP = {
    # [2026-08 대규모 재조정, 신민용 제공 실측 데이터 기반] 유저 제공
    # 데이터의 '최고' 열(리그 내 최고 연봉)을 그대로 캡으로 썼다.
    # (D/E/F 등급 국가는 economy._calc_salary가 이 나라별 캡 적용 자체를
    # 건너뛰므로 — 등급 안전망 캡(_salary_cap_table)으로 대신 제어됨 —
    # 여기 넣지 않았다.)
    "가나": 100000,  # [2026-08 재조정]
    "그리스": 675000,  # [2026-08 배치 재조정] 등급 중앙값 대비 3.8배 과대 — p50 12.3억/p90 15.0억/p99 18.4억대로 재조정
    # (B등급 체급은 유지하되 중앙값만 정상화).  # [2026-08 재조정]
    "나이지리아": 200000,  # [2026-08 재조정]
    "남아프리카공화국": 700000,  # [2026-08 재조정]
    "네덜란드": 2500000,
    "노르웨이": 1500000,  # [2026-08 재조정]
    "대한민국": 2000000,  # [2026-08 재조정]
    "덴마크": 1500000,  # [2026-08 재조정]
    "독일": 120000000,  # [2026-08 재조정, 신민용 지적: "유럽 5대리그에서 연봉 3천억이 찍힌다"] 2,050억 -> 1,200억.
    "라트비아": 200000,
    "러시아": 2000000,
    "레바논": 80000,
    "루마니아": 180000,  # [2026-08 배치 재조정] 등급 중앙값 대비 3.2배 과대 — p50 3.1억/p90 3.5억/p99 4.5억대로 재조정.  # [2026-08 재조정]
    "룩셈부르크": 300000,  # [2026-08 재조정]
    "리투아니아": 250000,
    "말라위": 8000,
    "멕시코": 2500000,
    "모로코": 400000,  # [2026-08 재조정]
    "몬테네그로": 150000,
    "미국": 2000000,
    "미얀마": 70000,
    "베네수엘라": 300000,  # [2026-08 재조정]
    "베트남": 200000,  # [2026-08 재조정]
    "벨기에": 2000000,
    "벨라루스": 300000,  # [2026-08 재조정]
    "보스니아 헤르체고비나": 250000,
    "볼리비아": 500000,
    "북마케도니아": 150000,
    "북아일랜드": 250000,
    "불가리아": 500000,  # [2026-08 재조정]
    "브라질": 15000000,  # [2026-08 재조정]
    "사우디아라비아": 380000000,  # [2026-08 재조정, 신민용 지적] 커브 ceil_sal(312,000,000=3,120억, 호날두 실측)이 이 캡(200억)에 눌려 OVR95든 105든 전부 296억으로 평평했다. 커브 상한(OVR95=3,120억)에 맞춰 올려 "3천억은 사우디에서만 가능"이 실제로 성립하게 한다. 캡 자체는 커브 꼬리(OVR100≈3,620억)를 막지 않으면서 그 이상은 잘라내는 절대 상한(3,800억)으로 둔다.
    "세네갈": 80000,  # [2026-08 재조정]
    "세르비아": 2100000,  # [2026-08 재조정]
    "스웨덴": 900000,  # [2026-08 재조정]
    "스위스": 1000000,
    "스코틀랜드": 5750000,  # [2026-08 재조정]
    "스페인": 120000000,  # [2026-08 재조정, 신민용 지적: "유럽 5대리그에서 연봉 3천억이 찍힌다"] 2,050억 -> 1,200억.
    "슬로바키아": 400000,  # [2026-08 재조정]
    "슬로베니아": 300000,  # [2026-08 재조정]
    "싱가포르": 120000,
    "아랍에미리트": 290000,  # [2026-08 배치 재조정, C등급 이상치 스캔+percentile 실측 기반] 등급 중앙값(2.0억)
    # 대비 6.2배로 과대 — p50 2.9억/p90 4.4억/p99 5.5억대로 재조정.  # [2026-08 재조정]
    "아르메니아": 250000,
    "아르헨티나": 8000000,  # [2026-08 재조정]
    "아이슬란드": 300000,  # [2026-08 재조정]
    "아일랜드": 200000,  # [2026-08 재조정]
    "알바니아": 200000,  # [2026-08 재조정]
    "알제리": 400000,  # [2026-08 재조정]
    "에콰도르": 800000,  # [2026-08 재조정]
    "오만": 300000,
    "오스트리아": 1500000,  # [2026-08 재조정]
    "요르단": 150000,
    "우간다": 12000,
    "우루과이": 800000,  # [2026-08 재조정]
    "우즈베키스탄": 500000,  # [2026-08 재조정]
    "우크라이나": 1000000,
    "웨일스": 120000,
    "이라크": 300000,  # [2026-08 재조정]
    "이란": 160000,  # [2026-08 배치 재조정] 등급 중앙값 대비 2.4배 과대 — p50 2.8억/p90 3.4억/p99 4.3억대로 재조정.  # [2026-08 재조정]
    "이스라엘": 225000,  # [2026-08 배치 재조정] 등급 중앙값 대비 4.9배 과대 — p50 3.7억/p90 4.4억/p99 5.1억대로 재조정.  # [2026-08 재조정]
    "이집트": 600000,  # [2026-08 재조정]
    "이탈리아": 120000000,  # [2026-08 재조정, 신민용 지적: "유럽 5대리그에서 연봉 3천억이 찍힌다"] 2,050억 -> 1,200억.
    "인도": 500000,
    "인도네시아": 300000,  # [2026-08 재조정]
    "일본": 2100000,  # [2026-08 재조정, 튀르키예와 같은 방식(percentile 실측 →
    # 배율 테스트 → 목표밴드 대비 확인)으로 조정. p50(OVR86)/p75(OVR88)는
    # 원래 캡보다 raw salary가 낮아 캡 자체가 안 걸리므로 배율과 무관하게
    # 그대로 유지됨 — 조정 대상은 p90 이상(OVR91+)뿐이다. ×0.7 적용 시
    # p90≈94억/p95≈102억/p99≈111억로, 튀르키예 확정치(p90=138/p95=150/
    # p99=163억) 대비 약 65~70% 수준에 안착 — J리그가 튀르키예 Süper Lig
    # 보다 이적시장 지출이 낮은 편이라는 상대적 위상에 부합.
    "잉글랜드": 120000000,  # [2026-08 재조정, 신민용 지적: "유럽 5대리그에서 연봉 3천억이 찍힌다"] 2,050억 -> 1,200억.
    "자메이카": 30000,
    "조지아": 300000,  # [2026-08 재조정]
    "중국": 3000000,  # [2026-08 재조정]
    "체코": 1400000,  # [2026-08 재조정]
    "칠레": 220000,  # [2026-08 배치 재조정] 등급 중앙값 대비 2.4배 과대 — p50 3.2억/p90 3.6억/p99 4.7억대로 재조정.  # [2026-08 재조정]
    "카메룬": 100000,  # [2026-08 재조정]
    "카타르": 660000,  # [2026-08 배치 재조정] 등급 중앙값 대비 2.5배 과대 — p50 11.0억/p90 12.7억/p99 14.9억대로 재조정.  # [2026-08 재조정]
    "코소보": 200000,
    "코스타리카": 100000,
    "코트디부아르": 120000,  # [2026-08 재조정]
    "콜롬비아": 1000000,  # [2026-08 재조정]
    "콩고 민주 공화국": 100000,
    "크로아티아": 400000,
    "태국": 300000,  # [2026-08 재조정]
    "튀니지": 300000,  # [2026-08 재조정]
    "튀르키예": 2700000,  # [2026-08 버그수정, 신민용 리포트: "튀르키예가 잉글랜드보다
    # 연봉이 높다"] 기존 37,500,000(375억)은 같은 A등급 동료 국가
    # (네덜란드/포르투갈 25억, 아르헨티나 80억)보다 5~15배 높게 잘못
    # 입력돼 있었다 — SS등급(잉글랜드 2050억)에 근접할 정도로 relief
    # 배율까지 겹쳐 실측(salary_distribution_probe.py)에서 OVR95+ 구간이
    # 잉글랜드를 역전하는 게 확인됐다.
    # [2026-08 2차 조정] 45억(1차 수정치)으로도 여전히 과했다 — ai_players
    # 실측 percentile 기준 "리그 중앙값 선수(OVR86, p50)"가 187억로 나와
    # 튀르키예 경제력 대비 비현실적이었다(percentile은 잉글랜드와 동일한
    # p50인데 절대값만 과함 — scarcity/희소성 문제가 아니라 기본 캡 자체
    # 문제로 확인). ×0.6(27억)로 재조정 — p50 OVR86≈112억, p90 OVR91≈138억
    # 대까지 완화(salary_distribution_probe.py로 재검증 완료).
    "파라과이": 500000,  # [2026-08 재조정]
    "페루": 500000,  # [2026-08 재조정]
    "포르투갈": 1000000,  # [2026-08 배치 재조정] A등급 중앙값(36.6억) 대비 3.2배 과대 — 독일(51억)/스페인(50억)
    # 대비 전체 선수가 2배 이상 비싼 구조였다. p50 54.2억/p90 66.7억/p99 78.5억대로 재조정
    # (A등급 상위권 리그 체급 유지하되 중앙값만 정상화).
    "폴란드": 1500000,  # [2026-08 재조정]
    "프랑스": 120000000,  # [2026-08 재조정, 신민용 지적: "유럽 5대리그에서 연봉 3천억이 찍힌다"] 2,050억 -> 1,200억.
    "핀란드": 400000,  # [2026-08 재조정]
    "필리핀": 70000,
    "헝가리": 250000,  # [2026-08 배치 재조정] 등급 중앙값 대비 4.0배 과대 — p50 3.8억/p90 4.5억/p99 5.2억대로 재조정.  # [2026-08 재조정]
    "호주": 500000,
    "홍콩": 100000,
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
    # [2026-08 신설, 신민용 제공 목표 밴드 기준] 대한민국 K1 — 일반 주전
    # 2~5억 / 핵심 선수 5~10억 / 리그 최고급 10~15억 / 초월적 스타
    # 15~20억대. 기존 base_year["B"][1] + 등급캡/국가캡+relief 조합은
    # 실제 K1 선수 OVR 범위(OVR_RANGES["A"][1]=82~94, 특수 재능은 그
    # 이상)에서 목표 밴드보다 몇 배 높은 값(OVR90=45억 등)을 냈다 —
    # base_year 커브 자체가 유럽 빅리그 기준으로 설계돼 있어 한국처럼
    # 좁은 밴드를 의도한 리그엔 안 맞았다. 다른 6개 양극화 리그와 같은
    # 방식(지수보간)으로 전환해 목표 밴드에 정확히 맞춘다.
    # base_ovr=50(실존 범위 밖 외삽용, 유스/후보급 가정치)~
    # floor_ovr=80(K1 최하위 벤치권 근사)~ceil_ovr=99(초월적 스타 상한).
    # [2026-08 재조정] base_sal을 K2 tier2 캡(75,000천원)보다 낮은 값으로
    # 뒀더니 OVR50~70 구간에서 "K2가 K1보다 비싸다"는 역전이 발생했다
    # (실측 salary_distribution_probe.py로 확인). base_sal을 K2 캡보다
    # 확실히 위(150,000천원=1.5억)로 올려 OVR50부터 항상 K1>K2>K3>K4
    # 순서가 유지되도록 재조정.
    "대한민국":       {"base_ovr": 50, "base_sal":    150_000,
                    "floor_ovr": 80, "floor_sal":    250_000, "ceil_ovr": 99, "ceil_sal": 2_000_000},
    # 호날두(알 나스르) 사례처럼 PIF 지원 4대 클럽 슈퍼스타는 초고액,
    # 그 외 사우디 로컬/하위팀 선수는 유럽 중소리그 수준으로 낮음.
    # [2026-07 수정, 실측 데이터] 호날두(알 나스르) 2025-27 계약 실측 —
    # Capology 기준 연 €208.4M(약 3,126억원) → OVR100. 벤제마(알 힐랄) 연
    # €122.4M(약 1,836억원)은 OVR99 근방에서 곡선상 자연스럽게 나오도록
    # ceil_ovr을 100으로 옮겼다(전엔 99=호날두라서 벤제마 자리가 없었음).
    # [2026-07 재조정, 신민용 실측 순위표 기준] 기존 floor_ovr=77/floor_sal=75만은
    # 사우디 A급 평균 OVR(88 안팎)에서 이미 1,340만(천원)까지 치솟아 스페인·
    # 이탈리아·독일보다도 높은 "연봉 2위"가 나왔다(실측은 5위 — 독일 밑,
    # 프랑스 위). floor_ovr을 90급 근처(오일머니 슈퍼스타 영입이 본격화되는
    # 지점)로 밀어 올리고 floor_sal도 그에 맞춰 상향 — "로컬/준수한 선수는
    # 유럽 중위권 수준, 호날두급 초특급만 로날두 실측(3,126억)에 근접"이라는
    # 원래 의도는 유지하면서, A급 평균 OVR 구간의 연봉만 낮췄다.
    # [2026-08 재조정, 신민용 제공 실측 데이터 반영] base_sal을 150,000→
    # 289,024로 상향 — OVR65(A급 평균 기준 앵커)에서 목표 평균 연봉(8억)이
    # 나오도록 역산. floor/ceil(호날두·벤제마 실측 앵커)은 그대로 유지.
    # [2026-08 재조정, 신민용 지적: "사우디는 애초에 OVR100인 선수가 가지
    # 않는다 — OVR95 정도가 3,120억을 받는 게 맞다"] 호날두 실측 앵커
    # (연 €208.4M ≈ 3,126억)를 OVR100이 아니라 OVR95에 붙인다. 실제로
    # 사우디에 오는 슈퍼스타는 유럽 전성기를 지난 30대 중후반이라 게임
    # OVR로도 90대 중반이 상한선이지, 100짜리가 오지 않는다 — 예전처럼
    # 3,120억을 OVR100에 붙여두면 사우디 리그에서 실제로 나오는 OVR 구간
    # (90대 초중반)의 연봉이 그 앵커에 비해 통째로 저평가됐다.
    # ceil 위(OVR96+)는 위 salary_curve_value의 완만한 꼬리(+3%/OVR)를
    # 타므로 OVR100 ≈ 3,620억 — "100이라고 4천억·5천억을 받진 않는다".
    # floor_ovr도 92 → 88로 내렸다: ceil만 100→95로 당기면 오일머니 구간이
    # 92~95 단 3포인트에 압축돼 OVR 1 차이가 연봉 3.5배가 되는 절벽이 생긴다
    # (OVR94 897억 → OVR95 3,123억). 88~95로 7포인트에 걸쳐 펴서 기울기를
    # 절반 이하로 낮췄다. OVR88 이하(로컬/평범한 영입)는 여전히 유럽 중위권
    # 수준(20~30억대)이라 "사우디 리그 평균 연봉이 유럽 5대리그를 역전"하는
    # 예전 문제도 재발하지 않는다.
    # ceil_sal은 "실측 3,126억 그 자체"가 아니라 그 *직전 값*이다 — 사우디는
    # COUNTRY_FOOTBALL_HEAT가 높아서 prestige_salary_mult가 모든 자국 클럽에
    # 일률적으로 ×1.48을 얹는다(_calc_salary의 _apply_prestige). 211,000,000
    # × 1.48 ≈ 3,123억이라 알 나스르 소속 OVR95가 정확히 호날두 실측에
    # 떨어진다. 여기에 3,126억을 그대로 박아두면 실제 지급액이 4,600억이 된다.
    "사우디아라비아": {"base_ovr": 50, "base_sal": 289_024,
                    "floor_ovr": 88, "floor_sal":  2_000_000, "ceil_ovr": 95, "ceil_sal": 211_000_000},
    # 홀란드(맨시티) 약 505억(£27.3M 실측) — floor_sal(로테이션급)은 실측
    # 그대로 두되, ceil_sal(역대급 최상단)은 아래 S급 통일 천장(2,000억)과
    # 맞춘다. floor_ovr을 S등급(스페인 등)과 같은 도메인(86)으로 맞춰서
    # 예전에 도메인이 달라 생겼던 SS<S 역전 버그도 계속 방지된다.
    # [2026-07 신설] base_sal=40만(4억) — EPL 최하위/유망주급 실측(연 2~5억대,
    #   Capology/FootyStats 하위권 확인) 반영. floor_sal(25억=로테이션급)까지
    #   자연스럽게 상승.
    # [2026-08 재조정, 신민용 지적: "유럽 5대리그에서 연봉이 3천억씩 찍힌다 —
    #  3천억 이상은 사우디 같은 곳에서나 가능한 금액이다(이적료와는 별개로)"]
    #  아래 5대리그의 ceil_sal(OVR100 = 역대급 선수)을 2,000억 → 1,000억으로
    #  내리고, COUNTRY_SALARY_CAP(절대 상한)을 2,050억 → 1,200억으로 맞췄다.
    #  기존 2,000억 앵커는 "메시 바르셀로나 계약 €138M"이었는데, 그건 보너스·
    #  이미지권·사이닝피까지 다 합친 총액이라 순수 연봉 앵커로는 과대했다.
    #  실측 최상위 주급(홀란드 약 500억, 음바페 약 470억) 기준으로 보면
    #  OVR100 = 1,000억도 이미 "역대 최고 대우"에 해당한다.
    #  1,200억 절대 상한은 명문 프리미엄·팀 체급·시대 배율·신급(god) 재능을
    #  전부 곱한 뒤에 씌워진다(economy._calc_salary 참고) — 즉 유럽에서는
    #  어떤 조합으로도 3천억이 나올 수 없고, 3천억대는 사우디
    #  (SALARY_CURVE_OVERRIDE + CAP 모두 3,120억, 호날두 실측)에서만 나온다.
    "잉글랜드":       {"base_ovr": 50, "base_sal":    400_000,
                    "floor_ovr": 90, "floor_sal":  2_500_000, "ceil_ovr": 100, "ceil_sal": 100_000_000},
    # 음바페(레알) 약 467억. 라리가 샐러리캡 제도로 하위권은 3~7억 수준.
    # [2026-07 재조정] 메시가 바르셀로나 시절 실제로 연 €138M(변동분 포함
    # 최대 $168.5M, 약 2,000억원) 계약을 받은 적이 있다(2021년 El Mundo
    # 유출 계약서 확인, 역대 스포츠 최고액 계약으로 알려짐) — 이 실측치를
    # S급 공통 "역대급 선수" 천장의 기준값으로 삼았다.
    "스페인":         {"base_ovr": 50, "base_sal":    270_000,
                    "floor_ovr": 87, "floor_sal":    500_000, "ceil_ovr": 100, "ceil_sal": 100_000_000},
    # 케인(바이에른) 약 370억. 50+1룰로 하위권도 아주 낮진 않음(4~8억).
    # [2026-07 신설] base_sal=28만 — 분데스리가 50+1룰 특성상 하위권도 완전
    #   바닥은 아님. ceil_sal은 S급 통일 천장(1,000억).
    "독일":           {"base_ovr": 50, "base_sal":    280_000,
                    "floor_ovr": 88, "floor_sal":    600_000, "ceil_ovr": 100, "ceil_sal": 100_000_000},
    # 블라호비치(유벤) 약 180억. 유벤/인테르/밀란 외 로테이션은 3~6억.
    # [2026-07 신설] base_sal=12만 — 세리에A는 상위 3강 외 스쿼드 편차가 큼.
    #   ceil_sal은 S급 통일 천장(1,000억).
    "이탈리아":       {"base_ovr": 50, "base_sal":    120_000,
                    "floor_ovr": 87, "floor_sal":    450_000, "ceil_ovr": 100, "ceil_sal": 100_000_000},
    # 뎀벨레/마르키뉴스(PSG) 약 180~220억. PSG 제외 17개 팀은 1.5~3억 수준.
    # [2026-07 신설] base_sal=7만 — 리그앙은 PSG 제외하면 유럽 5대리그 중
    #   가장 편차 큰 축(하위권 매우 낮음). ceil_sal은 S급 통일 천장(1,000억)
    #   — PSG(카타르 자금)라면 역대급 선수에게 이 정도도 가능하다고 봄.
    "프랑스":         {"base_ovr": 50, "base_sal":     70_000,
                    "floor_ovr": 87, "floor_sal":    225_000, "ceil_ovr": 100, "ceil_sal": 100_000_000},
}

def salary_curve_value(country: str, ovr: int) -> int:
    """SALARY_CURVE_OVERRIDE 적용 국가의 tier1 연봉(천원) 계산.
    [2026-07 재설계] base→floor→ceil 세 앵커를 2구간 지수보간으로 잇는다
    (예전엔 floor 밑을 전부 flat 처리해 약한 선수와 로테이션급 선수가
    똑같은 연봉을 받는 문제가 있었다). base_ovr 미만은 base 구간과 같은
    비율로 외삽(완전히 0으로 꺼지진 않되 계속 하락).

    [2026-08 재설계, 신민용 지적: "사우디는 애초에 OVR100인 선수가 가지
    않는다 — OVR95 정도가 3,120억을 받는 게 맞고, 그렇다고 OVR100이
    4천억·5천억을 받는 것도 아니다"] 예전엔 ceil_ovr 초과를 floor~ceil
    구간과 *같은 비율*로 외삽했다. 그 구간은 "로테이션급 → 역대급"을
    잇는 가장 가파른 구간(사우디는 5 OVR에 62배)이라, 그 기울기를 그대로
    연장하면 ceil을 몇 포인트만 넘어도 값이 조 단위로 폭발했다(실제로는
    COUNTRY_SALARY_CAP이 그걸 잘라내면서 ceil 위가 통째로 평평해졌다).
    ceil_ovr은 이미 "이 리그가 줄 수 있는 최고 대우" 지점이므로, 그 위는
    가파른 상승이 아니라 완만한 꼬리(OVR당 +3%)로 잇는다 — 평평해지지도
    않고(이 코드베이스가 반복해서 고쳐온 평탄화 버그 방지), 그렇다고
    ceil의 몇 배로 튀지도 않는다. 나라별로 다르게 하고 싶으면
    SALARY_CURVE_OVERRIDE에 "tail_per_ovr"를 넣으면 된다.
    """
    a = SALARY_CURVE_OVERRIDE.get(country)
    if not a:
        return None
    hi_o, hi_s = a["ceil_ovr"], a["ceil_sal"]
    mid_o, mid_s = a["floor_ovr"], a["floor_sal"]
    if ovr > hi_o:
        return max(0, int(hi_s * (a.get("tail_per_ovr", 1.03) ** (ovr - hi_o))))
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
    # [2026-08 신설] 대한민국 K1이 SALARY_CURVE_OVERRIDE로 전환되면서
    # tier1 최저치가 2.5억(250,000천원)으로 내려갔다 — 기존
    # LOWER_LEAGUE_SALARY_OVERRIDE(K2=701,988천원 등)를 그대로 두면
    # "2부 최저가 1부 최저보다 높다"는 역전이 생긴다. 위 실측(salary_
    # distribution_probe.py)에서 확인된 자연스러운 tier 비율(K1 대비
    # K2 30.3% / K3 13.6% / K4 5.2%)을 새 K1 floor_sal(250,000)에 그대로
    # 적용해 역전 없이 이어지게 한다.
    "대한민국":       {2:    75_000, 3:    34_000, 4:    13_000},
}


# ── 나라×tier 연봉 오버라이드 (base_year 천원/년 직접 지정) ───────────────
# LEAGUE_WEALTH_OVERRIDE가 나라 전체 부유도를 조정한다면,
# 이 테이블은 특정 나라의 특정 부(tier)만 핀포인트로 조정한다.
# _calc_salary에서 wealth 결정 후, 이 테이블이 있으면 base_year를 덮어씀.
#
# base 수치 기준: OVR50 기준 ×0.25 = 실제 월급 (아래 주석은 OVR50 기준 월급)
LOWER_LEAGUE_SALARY_OVERRIDE = {
    # [2026-08 대규모 재조정, 신민용 제공 실측 데이터 기반] tier(부) 값은
    # 유저가 제공한 리그별 평균 연봉표에 실제 _calc_salary로 이진탐색해
    # 정확히 맞춘 base(천원/년)다. 기존 tier4/5 오버라이드가 있던 나라는
    # 그대로 보존했다(이번 데이터는 tier1~3만 제공됨). 단, 사우디아라비아
    # 는 SPECIAL_SALARY_COUNTRIES라 이 테이블 자체가 적용되지 않는다
    # (economy._calc_salary가 is_special이면 override 조회를 건너뜀).
    "가나": {2: 46800, 3: 40084},  # [2026-08 재조정]
    "그리스": {2: 467992, 3: 200416},  # [2026-08 재조정]
    "나이지리아": {2: 70198, 3: 50104},  # [2026-08 재조정]
    "남아프리카공화국": {2: 175498, 3: 100208},  # [2026-08 재조정]
    "네덜란드": {3: 26411, 4: 15898, 5: 4769},
    "노르웨이": {2: 292496, 3: 200416},  # [2026-08 재조정]
    "대한민국": {2: 701988, 3: 400832, 4: 16000},  # [2026-08 재조정]
    "덴마크": {2: 140600, 3: 99368, 4: 13900},  # [2026-08 재조정]
    "독일": {3: 108027, 4: 44019, 5: 31797},
    "라트비아": {2: 87748, 3: 70146},  # [2026-08 재조정]
    "레바논": {2: 46800},  # [2026-08 재조정]
    "루마니아": {2: 292496, 3: 200416},  # [2026-08 재조정]
    "룩셈부르크": {2: 116998, 3: 80166},  # [2026-08 재조정]
    "리투아니아": {2: 105298, 3: 70146},  # [2026-08 재조정]
    "멕시코": {3: 8803, 4: 1987},
    "모로코": {2: 146248, 3: 100208, 4: 5560},  # [2026-08 재조정]
    "몬테네그로": {2: 87748, 3: 70146},  # [2026-08 재조정]
    "미국": {4: 0},
    "미얀마": {2: 35100},  # [2026-08 재조정]
    "베네수엘라": {2: 87748, 3: 70146},  # [2026-08 재조정]
    "베트남": {2: 58500},  # [2026-08 재조정]
    "벨기에": {3: 15733, 4: 9265, 5: 4959},
    "벨라루스": {2: 146248, 3: 100208},  # [2026-08 재조정]
    "보스니아 헤르체고비나": {2: 116998, 3: 100208},  # [2026-08 재조정]
    "볼리비아": {2: 87748, 3: 70146},  # [2026-08 재조정]
    "북마케도니아": {2: 87748, 3: 70146},  # [2026-08 재조정]
    "북아일랜드": {2: 204746, 3: 200416},  # [2026-08 재조정]
    "불가리아": {2: 175498, 3: 120250},  # [2026-08 재조정]
    "브라질": {2: 301286, 3: 231860, 4: 7942, 5: 4959},  # [2026-08 재조정]
    "사우디아라비아": {2: 1, 3: 5000000},  # [2026-08 재조정]
    "세네갈": {2: 46800, 3: 40084, 4: 2366},  # [2026-08 재조정]
    "세르비아": {2: 233996, 3: 150312},  # [2026-08 재조정]
    "스웨덴": {2: 350994, 3: 300624, 4: 11120},  # [2026-08 재조정]
    "스위스": {2: 49000, 3: 23334, 4: 11120},
    # [버그수정] 2부(152652)가 3부(443846)보다 싼 값으로 뒤집혀 있었다 —
    # 이 파일의 다른 84개국은 전부 "부수가 낮을수록 연봉도 낮음"인데
    # 스코틀랜드만 유일하게 역전(신민용 리포트: "2번 강등당한 선수의
    # 재계약 제시액이 200만원까지 떨어졌다" — tier4엔 오버라이드가 아예
    # 없어 그 자체도 매우 낮지만, tier2/3 역전도 같이 원인). tier 키에
    # 값이 서로 바뀌어 들어간 단순 오기로 보고 2↔3 값을 맞바꿔 원래
    # 패턴(2부>3부)으로 복원.
    "스코틀랜드": {2: 443846, 3: 152652},  # [2026-08 재조정, 2026-08 버그수정: tier값 역전 교정]
    "스페인": {3: 40510, 4: 22009, 5: 15898},
    "슬로바키아": {2: 204746, 3: 150312},  # [2026-08 재조정]
    "슬로베니아": {2: 146248, 3: 100208},  # [2026-08 재조정]
    "싱가포르": {2: 46800},  # [2026-08 재조정]
    "아랍에미리트": {2: 467992, 3: 300624},  # [2026-08 재조정]
    "아르메니아": {2: 105298, 3: 70146},  # [2026-08 재조정]
    "아르헨티나": {2: 200858, 3: 165614, 4: 5294, 5: 3967},  # [2026-08 재조정]
    "아이슬란드": {2: 204746, 3: 150312},  # [2026-08 재조정]
    "아일랜드": {2: 116998, 3: 80166},  # [2026-08 재조정]
    "알바니아": {2: 105298, 3: 70146},  # [2026-08 재조정]
    "알제리": {2: 146248, 3: 100208},  # [2026-08 재조정]
    "에콰도르": {2: 233996, 3: 150312},  # [2026-08 재조정]
    "오만": {2: 116998},  # [2026-08 재조정]
    "오스트리아": {2: 140600, 3: 99368, 4: 8340},  # [2026-08 재조정]
    "요르단": {2: 87748},  # [2026-08 재조정]
    "우루과이": {2: 233996, 3: 150312, 4: 2117},  # [2026-08 재조정]
    "우즈베키스탄": {2: 146248, 3: 80166},  # [2026-08 재조정]
    "웨일스": {2: 116998, 3: 80166},  # [2026-08 재조정]
    "이라크": {2: 116998},  # [2026-08 재조정]
    "이란": {2: 175498, 3: 100208, 4: 13000},  # [2026-08 재조정]
    "이스라엘": {2: 350994, 3: 200416},  # [2026-08 재조정]
    "이집트": {2: 116998, 3: 80166},  # [2026-08 재조정]
    "이탈리아": {3: 27006, 4: 13205, 5: 7949},
    "인도": {2: 70198, 3: 50104},  # [2026-08 재조정]
    "인도네시아": {2: 70198},  # [2026-08 재조정]
    "일본": {2: 241030, 3: 132492, 4: 13000},  # [2026-08 재조정]
    "잉글랜드": {3: 202550, 4: 132058, 5: 79494},
    "조지아": {2: 116998, 3: 100208},  # [2026-08 재조정]
    "중국": {2: 350994, 3: 200416},  # [2026-08 재조정]
    "체코": {2: 120514, 3: 82808},  # [2026-08 재조정]
    "칠레": {2: 233996, 3: 150312},  # [2026-08 재조정]
    "카메룬": {2: 46800, 3: 40084},  # [2026-08 재조정]
    "카타르": {2: 467992},  # [2026-08 재조정]
    "코소보": {2: 87748, 3: 70146},  # [2026-08 재조정]
    "코트디부아르": {2: 58500, 3: 50104},  # [2026-08 재조정]
    "콜롬비아": {2: 233996, 3: 150312, 4: 2780},  # [2026-08 재조정]
    "콩고 민주 공화국": {2: 46800, 3: 40084},  # [2026-08 재조정]
    "크로아티아": {2: 19000, 3: 9440, 4: 5294},
    "태국": {2: 70198, 3: 60124},  # [2026-08 재조정]
    "튀니지": {2: 87748, 3: 70146},  # [2026-08 재조정]
    "튀르키예": {2: 301286, 3: 198738},  # [2026-08 재조정]
    "파라과이": {2: 146248, 3: 100208},  # [2026-08 재조정]
    "페루": {2: 116998, 3: 70146},  # [2026-08 재조정]
    "포르투갈": {3: 33014, 4: 23848, 5: 5962},
    "폴란드": {2: 409494, 3: 300624},  # [2026-08 재조정]
    "프랑스": {3: 33758, 4: 19808, 5: 13911},
    "핀란드": {2: 204746, 3: 150312},  # [2026-08 재조정]
    "필리핀": {2: 35100},  # [2026-08 재조정]
    "헝가리": {2: 233996, 3: 150312},  # [2026-08 재조정]
    "홍콩": {2: 40950},  # [2026-08 재조정]
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

# [2026-08 신설, 신민용+GPT 1차 구현 ③] 계약 역할별 "감독이 기대하는
# 실제 출전 비율"(0~1). game_engine._calc_manager_rel에서 실제 출전
# 비율(팀이 이번 시즌 이미 치른 경기 대비 season_matches)과 비교해 갭이
# 크면 관계에 작은 보정을 준다. OFFER_ROLES와 키를 그대로 공유한다.
ROLE_EXPECTED_PLAYTIME = {
    "주전 보장":   0.85,
    "주전 경쟁":   0.55,
    "로테이션":    0.35,
    "유망주 영입": 0.15,
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
    """[2026-07 수정, 신민용 요청: "패(부상)/승(퇴장) 이런 표시가 오히려
    방해된다 — 없애줘"] 원래는 결장 사유가 있으면 "패 (부상)" 식으로 붙여서
    보여줬는데, 실전에서는 그냥 결과(승/무/패)만 보는 게 더 깔끔하다는
    피드백을 반영해 결장 사유 표시를 껐다. 함수 자체(와 이걸 호출하는
    career_window.py/retire_window.py 아홉 곳)는 그대로 두고, 여기 한
    곳에서만 결과 문자열을 순수하게 반환하도록 바꿨다 — absence_reason은
    더 이상 표시에 쓰지 않는다."""
    return m.get("result", "")

# ══════════════════════════════════════════════════════════════
# [2026-08 신설, 신민용 리포트: "포메이션엔 AI 73QU로 뜨는데 이적 로그엔
# AI (331454)로 따로 뜬다"] AI 선수 표시 코드 — ai_players.id(전세계
# 유일 PK)를 4자리 36진수(0-9,A-Z)로 바꿔 "AI"+4자(항상 정확히 6자)
# 형식으로 만든다. 예전엔 이 변환이 ui/formation_widget.py 안에만 있어서
# (포메이션 화면 전용), 세이브 전체 기간에 걸친 영구 기록인 이적
# 로그(ai_lifecycle.py)는 "AI (id)"라는 별도 형식을 썼다 — 같은 선수인데
# 화면마다 표시가 달라 헷갈렸다. 여기 하나로 합쳐서 두 곳이 똑같은 코드를
# 쓰게 한다(36**4 = 1,679,616명까지 절대 안 겹침).
# ══════════════════════════════════════════════════════════════
_AI_CODE_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def ai_player_code(pid) -> str:
    """ai_players.id → "AI"+4자리 36진수 코드(항상 정확히 6자). pid가
    없거나 음수(가상/폴백 선수)면 "AI0000"."""
    if pid is None or pid < 0:
        return "AI0000"
    n = int(pid); out = []
    for _ in range(4):
        n, r = divmod(n, 36)
        out.append(_AI_CODE_DIGITS[r])
    return "AI" + "".join(reversed(out))

# ══════════════════════════════════════════════════════════════
# [2026-09 신설, 신민용 리포트: "노화 재설계로 40대에도 OVR이 꽤 높게
# 남는 선수가 나오는 구조가 됐는데, 국가대표 선발이 그냥 OVR 순 정렬이면
# 40대가 여러 명 동시에 뽑히는 이상한 상황이 생길 수 있다"] 실제 확인
# 결과 database.get_country_squad_players()의 선발 정렬 기준이
# "(ap.ovr - ABS(ap.age-27)*0.3) DESC"였다 — 27세 대칭이라 유망주에게도
# 페널티를 먹이면서 정작 40세는 겨우 -3.9점(OVR 5~10점 차이에도 뒤집힘),
# 포지션 구분도 전성기(peak_ovr) 구분도 전혀 없었다.
#
# 신민용이 직접 제시한 설계 원칙을 반영해 재설계:
#   - 33세까지는 페널티 없음("정상") — 34세부터 나이대별로 점점 가팔라짐
#   - 포지션군별 민감도(GK 제일 관대 → CB → 미드 → 윙어/공격수 제일 민감)
#   - "전성기 OVR"이 아주 높은 선수(레전드급)는 페널티를 크게 감면 —
#     "전성기 95 이상이면 예외적으로 30대 후반~40대도 가능" 취지
#   - 절대 컷오프가 아니라 "감점"이라 그 자리 경쟁자가 약하면(선수층이
#     얇은 나라 등) 여전히 뽑힐 수 있음 — 상대적 랭킹은 기존처럼
#     get_country_squad_players의 포지션별 "남은 후보 중 최고 점수" 로직이
#     그대로 담당(자연히 그 나라·그 포지션의 실제 경쟁자 수준이 반영됨).
# ══════════════════════════════════════════════════════════════
INTL_POS_AGE_GROUP = {
    "GK": "gk",
    "CB": "def", "LB": "mid", "RB": "mid",
    "CDM": "mid", "CM": "mid", "CAM": "mid",
    "LW": "att", "RW": "att", "CF": "att", "ST": "att",
}

# 포지션군별 노화 페널티 배율 — 숫자가 클수록 나이에 더 민감(불리).
# GK(가장 오래감) < CB(비교적 오래감) < 미드(평균) < 공격/윙어(스피드
# 의존도 높아 제일 민감).
INTL_AGE_GROUP_MULT = {"gk": 0.5, "def": 0.75, "mid": 1.0, "att": 1.4}


def intl_age_band_penalty(age: int) -> float:
    """나이대별 "기준" 페널티(포지션 배율 곱하기 전, mid 기준으로 설계).
    33세까지 0, 그 이후 구간마다 기울기가 가팔라진다(34~35 약간 불리,
    36~37 상당히 불리, 38~39 매우 불리, 40+ 극단적으로 불리 — 신민용
    확정 구간)."""
    if age <= 33:
        return 0.0
    if age <= 35:
        return (age - 33) * 1.5          # 34~35세: 1.5~3.0
    if age <= 37:
        return 3.0 + (age - 35) * 3.5    # 36~37세: 6.5~10.0
    if age <= 39:
        return 10.0 + (age - 37) * 6.0   # 38~39세: 16.0~22.0
    return 22.0 + (age - 39) * 10.0      # 40세부터: 32.0, 41세 42.0 ...


def intl_peak_relief(peak_ovr: int, age: int) -> float:
    """전성기 OVR이 아주 높은 "레전드"는 노화 페널티를 크게 깎아준다
    (1.0=감면 없음, 낮을수록 많이 감면). 33세 이하거나 전성기 정보가
    없으면 어차피 기준 페널티 자체가 0이라 영향 없음.
    신민용 확정 기준: <90 감면 없음 / 90~94 소폭 / 95~96 중간 / 97~99
    크게 / 100 거의 면제("역사적인 선수라면 40대까지 국대가 나와도
    이상하지 않음")."""
    if age <= 33 or not peak_ovr:
        return 1.0
    if peak_ovr >= 100:
        return 0.15
    if peak_ovr >= 97:
        return 0.30
    if peak_ovr >= 95:
        return 0.55
    if peak_ovr >= 90:
        return 0.80
    return 1.0


def intl_selection_score(ovr, age, position, peak_ovr):
    """국가대표 선발 순위용 점수 — OVR에서 나이·포지션·전성기 기반
    페널티를 뺀다. SQLite 커스텀 함수로 등록되어(database._new_raw_conn
    참고) get_country_squad_players의 ORDER BY에서 직접 쓰인다."""
    ovr = ovr or 0
    age = age or 25
    peak_ovr = peak_ovr or ovr
    grp = INTL_POS_AGE_GROUP.get(position, "mid")
    mult = INTL_AGE_GROUP_MULT.get(grp, 1.0)
    penalty = intl_age_band_penalty(age) * mult * intl_peak_relief(peak_ovr, age)
    return ovr - penalty