# -*- coding: utf-8 -*-
"""
[2026-08 신설, 신민용 설계 확정: "챔스/유로파급/컨퍼런스급 3단계 대륙대항전
공통 엔진"]

champions_engine.py(1939줄)의 함수들을 A/B/C 세 그룹으로 나눈 것 중,
A그룹(완전 공통)과 B그룹(로직은 동일하고 테이블명/문자열만 대회마다 다름)을
이 모듈로 옮긴다. C그룹(대륙별 규모·슬롯 등 챔스 전용 규칙)은
champions_engine.py에 그대로 남는다 — continental_qualification.py가
참가팀 선정을 전담하게 되면서 C그룹 중 슬롯 계산 관련 함수들은 아예
삭제 대상이 된다(다음 단계에서 champions_engine.py를 정리할 때 처리).

[리팩터링 원칙 — 매우 중요] 이 모듈의 모든 함수는 champions_engine.py
원본 함수 본문을 그대로 옮기고, 아래 두 가지만 바꿨다:
  1) 테이블명 리터럴("cl_matches" 등) → cfg.xxx_table
  2) 대회명/시상명/momentum 타입 등 문자열 리터럴 → cfg.xxx
그 외의 로직·조건문·계산식·random 호출 순서는 단 한 줄도 안 바꿨다 —
RNG를 쓰는 게임이라 호출 순서가 바뀌면 같은 시드로도 결과가 달라지므로,
"동일 시드 → 동일 결과"를 검증하려면 이 원칙이 지켜져야 한다.

[아직 안 옮긴 것] _award_cl_awards(시상), _cl_team_stage_weights,
get_my_cl_* 조회 함수들은 이번 1차 이동에서 제외했다 — 핵심 대진/경기결과/
우승팀 결정 경로부터 먼저 검증한 뒤(신민용 요청 9개 항목 중 1~6번),
그다음 개인기록/시상(7~9번)을 옮기는 게 안전하다고 판단했다. 지금은
champions_engine.py가 이 부분만 자체 보유 중.
"""
import random
from dataclasses import dataclass

from database import get_conn
from constants import generate_round_robin

# [2026-08 신설] PSO(승부차기) 홈팀 승률에 OVR 차이가 반영되는 민감도.
# p_home = 0.5 + clamp(diff * PSO_OVR_COEF, -0.1, 0.1) — resolve_pso 참고.
# 기존 하드코딩값(0.006)을 그대로 기본값으로 옮긴 것뿐, 동작 변화 없음.
PSO_OVR_COEF = 0.010


@dataclass(frozen=True)
class CompetitionConfig:
    """대회 하나를 이 공통 엔진에 연결하는 데 필요한 최소 설정.
    [신민용 지적 반영] 대륙별 참가 규모·직행/PO 컷 같은 복잡한 진출
    규칙은 여기 억지로 넣지 않는다 — 그건 각 대회 엔진(champions_engine.py
    등)이 자기 C그룹 상수/함수로 계산해서, _build_tournament 등을 호출할
    때 이미 확정된 값(entries, games 등)으로 넘겨준다. cfg는 순수하게
    "이 대회의 결과를 어느 테이블에, 어떤 이름으로 저장할지"만 안다."""
    match_table: str
    entry_table: str
    tournament_table: str
    history_table: str
    competition_name_by_continent: dict   # {"유럽": "UEFA 유로파리그", ...}
    award_prefix: str                     # 시상 이름 접두사 (예: "유로파리그")
    momentum_type: str                    # 우승 시 club_momentum 타입명
    stage_ko: dict                        # {"league":"리그 스테이지", "R16":"16강", ...}
    round_weeks: dict                     # {"league_start":9, "PO":18, "R32":19, ...}
    league_weeks: tuple                   # (시작주, 끝주) — CL_LEAGUE_WEEKS와 동일 형태
    end_week: int
    stage_order: list                     # ["R32","R16","QF","SF","F"]
    # [2026-08 신설, 옐로카드 시스템] 이 대회의 출전정지 카운터 필드명.
    # 기본값은 기존 그대로 "cl_suspension"(챔스/유로파/컨퍼런스 공유) —
    # 슈퍼컵만 별도 그룹(super_cup_suspension)이라 SC_CFG가 오버라이드한다.
    suspension_field: str = "cl_suspension"
    # [2026-09 신설, 신민용 확정: "챔스/유로파/컨퍼런스/클럽월드컵은 결승과
    # 3/4위전, 대륙 슈퍼컵은 4강부터 전부 원정 vs 원정"] 이 튜플에 담긴
    # stage 코드의 경기는 sim_ai_match/simulate_my_match가 자동으로
    # match_outcome/resolve_pso를 neutral=True로 호출한다. 기본값은 빈
    # 튜플(아무 stage도 중립 처리 안 함 — 기존 대회 전부 동작 그대로).
    neutral_stages: tuple = ()


# ─────────────────────────────────────────────
# A그룹 — 완전 공통 (원본과 100% 동일, cfg조차 필요 없음)
# ─────────────────────────────────────────────

# [2026-08 확정, 신민용 요청: "챔스 우승팀이 너무 다양하다 — 42경기 실측
# (코펜하겐/브뢴뷔/에피날/브라운슈바이크 등 9개 우승 트리) → 강팀도 지고
# 있지만 원인의 상당수는 85~95 구간에서 OVR 격차가 최종 승률에 충분히
# 반영되지 않는 것" — 150회×16시즌 몬테카를로로 검증 완료(<85 우승
# 1.0%→0.1%, 85-89 우승 8.8%→3.0%, 부작용 없음 확인)] UEFA/대륙
# 클럽대항전(챔스/유로파/컨퍼런스/클럽월드컵) 매치 확률 계산 직전에만
# 적용하는 비선형 OVR 보정. 85 이하는 그대로 두고 85~100 구간만 벌린다
# ("중" 곡선 — "약" 곡선과 최종 우승 분포가 사실상 동일해서 "중"으로 확정,
# "강" 곡선은 이미 격차가 큰 매치를 과증폭시켜 제외).
#
# ⚠ 중요: 이건 실제 ai_players.ovr/teams.ovr 등 원본 데이터는 전혀 안
# 건드리고, 아래 match_outcome/resolve_pso 호출 시점에만 일시적으로
# 변환한 값을 쓴다 — 연봉·이적시장·선수 성장·리그 순위·club_strength에는
# 영향이 전혀 없다. 리그 경기 확률식(game_engine.py, 별도 상수 0.020)도
# 완전히 별개라 여기서 안 건드린다.
_TOURNAMENT_OVR_CURVE_PTS = [(70, 70), (80, 80), (85, 85), (90, 93), (95, 100), (100, 108)]


# ══════════════════════════════════════════════════════════════
# 대회 경기일 배정 — 리그 일정과 안 겹치는 요일 고르기 (2026-09 신설)
# ══════════════════════════════════════════════════════════════
# [신민용 확정: "컵 대회랑 리그 일정은 절대 겹치면 안돼"] 지금까지
# game_engine._week_intl_cl_day / super_cup_engine._pick_sc_days /
# domestic_super_cup_engine._pick_dsc_day 셋 다 "내 팀(my_tid)이 낀
# 경기만" 리그 일정을 피했다 — 주석에 그 이유("AI 팀끼리는 겹쳐도 화면에
# 보이는 문제가 없고, 매번 이 조회를 하면 성능만 낭비된다")까지 명시돼
# 있었는데, 세계 기록실에서 AI 팀 일정도 그대로 보이므로 그 전제가
# 틀렸다. 3·4부컵은 아예 회피 로직 자체가 없어 전 세계가
# week_to_day(week)+3 한 날에 몰려 있었다.
#
# 실측(6시즌 세이브, 2005시즌, 같은 팀이 같은 날 2경기):
#     3·4부컵 + 리그  2,760건 (영향 팀 2,141)
#     국내슈퍼컵 + 리그   23건
#     대륙슈퍼컵 + 리그    2건
# (국내컵/챔스/유로파/컨퍼런스는 AI 경기에 day를 안 쓰고 주차 단위로만
#  돌아 애초에 "같은 날" 개념이 없다 — 내 팀이 낀 경기만 날짜가 잡히고
#  그쪽은 이미 _week_intl_cl_day가 회피 중이라 대상 밖.)
#
# [gap 규칙의 의미 — 구현 전 확인 완료] 게임의 강제 휴식 규칙은
# ui/center_panel.py 주석대로 "어제 경기 있었으면 오늘 무조건 휴식"
# (경기 다음날 기준, 비대칭)이다. 그런데 경기 두 개(A일·B일, A<B)에
# 이 규칙을 적용하면 B가 A+1일 수 없다는 제약 하나로 귀결되므로,
# "두 경기는 최소 2일 떨어져 있어야 한다"(|A-B| >= 2)와 정확히 같다 —
# 즉 기존 세 함수가 쓰던 대칭 조건(abs(diff) <= 1 회피)이 이미 그
# 규칙의 올바른 표현이고, 여기서도 같은 의미로 gap=2를 쓴다.
#
# [폴백 — 신민용 확정] 조건을 만족하는 날이 하나도 없으면 다음 주로
# 미루지 않고 기존 기본 날짜를 그대로 쓴다. 이 로직의 목적은 "완벽한
# 일정 재편성"이 아니라 "가능한 한 안 겹치게"이고, 주차 경계를 넘기면
# week 기반 일정 생성 구조 자체와 충돌하기 때문이다.
#
# [기존 세이브] 이미 생성된 경기의 day는 건드리지 않는다 — 새로 만드는
# 라운드부터 이 로직이 적용된다.
DEFAULT_MATCH_DAY_GAP = 2


# ── league_day_map (연도, 주차) 캐시 ───────────────────────────
# [2026-09 성능, 신민용 리포트: "32~40주차·28주차 딜레이가 심하다"]
# 이 함수가 읽어오는 건 "그 주차 전세계 리그 일정"이고, 그건 누가 물어도
# 같은 값이다. 그런데 호출부(3·4부컵)는 나라마다(193개 대회) 라운드를
# 만들 때마다 따로 불러서, 한 주 안에 같은 (연도, 주차) 조회가 수백 번
# 반복됐다 — 그리고 그 쿼리 하나하나가 match_results를 idx_mr_year로
# 훑는(그 해 20만 행 전부가 year=? 조건을 만족하므로 사실상 풀스캔)
# 30ms짜리였다. 한 시즌 실측 프로파일링: league_day_map 1,223회 누적
# 27.5초 = 32~40주차 국내컵 버킷(주당 4.5~6.8s)과 28주차 3·4부컵 생성
# (4.7s)의 거의 전부.
#
# 고치는 방법은 두 가지를 같이 쓴다:
#   (1) (연도, 주차범위) 단위로 "팀 필터 없이 그 주 전체"를 한 번만 읽어
#       캐시한다 — 두 번째 호출부터는 DB 왕복이 아예 없다. 반환값은
#       예전과 똑같이 "요청한 team_ids만 들어있는 dict"로 잘라서 준다
#       (호출부가 dict 전체를 훑는 경우에도 동작이 100% 동일하게).
#   (2) 그 한 번의 조회도 INDEXED BY로 idx_mr_week_season(week 선두)을
#       쓰게 한다 — 기본 플래너는 선택도가 전혀 없는 idx_mr_year를 고른다
#       (실측 32ms → 12ms). INDEXED BY는 이 쿼리 하나에만 영향을 주므로
#       다른 쿼리의 실행계획·행 순서는 전혀 안 바뀐다(새 인덱스를 추가해
#       플래너 전체를 흔드는 방식은 그래서 일부러 피했다). 연도전환의
#       벌크 구간에는 이 인덱스가 잠깐 DROP되므로(database.
#       MATCH_RESULTS_INDEXES) 그때는 조용히 평소 쿼리로 폴백한다.
#
# 캐시는 match_results에 쓰기가 한 번이라도 들어오면 통째로 버린다
# (database.set_match_results_write_hook) — 일정 생성/재편성으로 day가
# 바뀌면 바로 다시 읽으므로 값이 낡을 수 없다. 캐시가 비어 있는 동안은
# 훅 자체를 해제해 두어 평소 SQL 핫패스에 비용이 붙지 않는다.
_DAY_MAP_CACHE: dict = {}
_DAY_MAP_HOOK_ON = False

# ── 대회 경기일 세대 카운터 (2026-09 신설) ──────────────────────
# [신민용 리포트 20번] game_engine._week_intl_cl_day는 (주차, 내 팀, 시즌)
# 으로 메모이즈되는데, 이제 리그 일정뿐 아니라 "그 팀의 다른 대회 경기일"
# (3·4부컵/슈퍼컵/클럽월드컵/승강PO/국가대표)까지 피해서 날짜를 고른다 —
# 그 경기일들은 시즌 중에 라운드가 생성될 때마다 새로 생기므로, 시즌 내내
# 고정이던 예전 캐시 키로는 "그 대회 라운드가 아직 없던 시점에 계산된 날"이
# 그대로 굳어버린다(일정창으로 몇 주 앞을 미리 보면 실제로 그렇게 된다).
# 라운드를 만들 때마다 이 카운터를 올리고 캐시 키에 함께 넣어서, 새 경기일이
# 생기면 자동으로 다시 계산되게 한다.
_MATCH_DAY_GEN = [0]


def match_day_generation() -> int:
    """대회 경기일이 새로 쓰인 횟수 — _week_intl_cl_day 캐시 키용."""
    return _MATCH_DAY_GEN[0]


def bump_match_day_generation() -> None:
    """대회 라운드를 생성해 day를 쓴 직후 호출. 세대가 올라가면 예전 키는
    두 번 다시 안 쓰이므로 캐시도 같이 비운다(키가 무한히 쌓이는 것 방지)."""
    _MATCH_DAY_GEN[0] += 1
    try:
        import game_engine
        game_engine._week_intl_cl_day_cache.clear()
    except Exception:
        pass


def invalidate_league_day_map_cache():
    """match_results 쓰기 감지 시 호출 — 캐시를 비우고 훅도 해제한다."""
    global _DAY_MAP_HOOK_ON
    _DAY_MAP_CACHE.clear()
    if _DAY_MAP_HOOK_ON:
        _DAY_MAP_HOOK_ON = False
        try:
            from database import set_match_results_write_hook
            set_match_results_write_hook(None)
        except Exception:
            pass


def _load_week_day_map(conn, year, weeks):
    """(year, weeks)에 걸린 리그 경기를 팀 필터 없이 통째로 읽어
    {team_id: set(day)}로 만든다. 캐시 미스일 때만 불린다."""
    wph = ",".join("?" * len(weeks))
    tail = (f"""FROM match_results
                WHERE year=? AND week IN ({wph}) AND day IS NOT NULL AND day>0""")
    sql_fast = f"""SELECT home_team_id, away_team_id, day
                   FROM match_results INDEXED BY idx_mr_week_season
                   WHERE year=? AND week IN ({wph}) AND day IS NOT NULL AND day>0"""
    try:
        rows = conn.execute(sql_fast, (year, *weeks)).fetchall()
    except Exception:
        # 인덱스가 없는 구간(연도전환 벌크)/구형 세이브 — 평소 쿼리로 폴백.
        rows = conn.execute(
            f"SELECT home_team_id, away_team_id, day {tail}", (year, *weeks)).fetchall()
    full: dict = {}
    _get = full.get
    for r in rows:
        h, a, d = r[0], r[1], r[2]
        s = _get(h)
        if s is None:
            s = full[h] = set()
        s.add(d)
        s = _get(a)
        if s is None:
            s = full[a] = set()
        s.add(d)
    return full


def league_day_map(conn, year, week, team_ids, span=1):
    """team_ids가 week 주변(week-span ~ week+span)에 갖고 있는 리그 경기일을
    {team_id: set(day)}로 한 번에 읽어온다.

    같은 (year, week, span)에 대한 반복 호출은 캐시로 처리한다(위 주석 참고)
    — 반환하는 dict의 내용은 예전 구현과 완전히 동일하다."""
    global _DAY_MAP_HOOK_ON
    out: dict = {}
    ids = {t for t in team_ids if t}
    if not ids:
        return out
    weeks = tuple(week + d for d in range(-span, span + 1))
    key = (year, weeks)
    full = _DAY_MAP_CACHE.get(key)
    if full is None:
        full = _load_week_day_map(conn, year, weeks)
        # 캐시는 최근 몇 주치만 들고 있으면 충분하다(호출은 항상 "지금
        # 처리 중인 주차"에 몰린다) — 무한히 쌓이지 않게 상한을 둔다.
        if len(_DAY_MAP_CACHE) >= 4:
            _DAY_MAP_CACHE.clear()
        _DAY_MAP_CACHE[key] = full
        if not _DAY_MAP_HOOK_ON:
            try:
                from database import set_match_results_write_hook
                set_match_results_write_hook(invalidate_league_day_map_cache)
                _DAY_MAP_HOOK_ON = True
            except Exception:
                pass
    _fget = full.get
    for tid in ids:
        s = _fget(tid)
        if s is not None:
            out[tid] = s
    return out


def pick_free_day(week_start, day_map, team_ids, default_day,
                  offsets=(3, 2, 4, 1, 5, 0, 6), gap=DEFAULT_MATCH_DAY_GAP):
    """week_start(그 주 첫날)부터 offsets 순서로 후보 요일을 시도해,
    team_ids 전원이 기존 리그 경기와 gap일 이상 떨어지는 첫 날을 돌려준다.
    하나도 없으면 default_day(기존 동작과 동일한 기본 날짜)로 폴백한다.

    offsets 기본값은 3(기존 3·4부컵 요일)을 1순위로 두어, 겹치지 않는
    경우에는 지금까지와 같은 날이 그대로 선택되게 한다.

    [2026-09 버그수정, 신민용 리포트 20번 실측] 폴백(default_day)이
    실제로는 "가장 나쁜 선택"이었다. 시즌 중 실측(3·4부컵 5,332경기):
        - gap을 만족하는 날이 아예 없는 경기 668건(12.5%) — 10~12팀
          리그는 다전제(4전)라 한 주에 리그 경기가 2~3개씩 들어가서,
          양 팀 리그 일정을 동시에 피할 수 있는 요일이 실제로 없다.
        - 그 668건이 전부 default_day 하루로 몰렸고, 그 하루가 마침
          어느 한 팀의 리그 경기일이면 **같은 날 2경기**가 됐다
          (실측 346건이 Δ0 = 완전 동일 날짜).
        - 반대로 "gap을 만족하는 날이 있는데도 못 고른" 경기는 0건이었다
          — 즉 위 루프 자체는 이미 최적이고 문제는 폴백뿐이다.
    이제 폴백에서도 남은 후보 중 **가장 멀리 떨어진 날**을 고른다. 같은
    날 2경기(Δ0)는 구조적으로 사라지고(실측 후보 중 Δ>=1인 날이 없는
    경기는 0건), 한 라운드가 특정 하루에 몰리는 현상도 함께 사라진다.
    gap을 만족하는 날이 하나라도 있으면 기존과 100% 동일하게 동작한다
    (offsets 순서대로 첫 번째 날을 그대로 반환)."""
    busy = set()
    for tid in team_ids:
        if tid:
            # [2026-09 버그수정] `busy |= day_map.get(tid, ())`은
            # day_map에 tid가 없을 때 기본값 ()(튜플)이 들어와 TypeError로
            # 죽는다 — set의 |=는 set만 받고 튜플은 안 받는다.
            # (week±span에 리그경기가 없는 팀 / day_map이 {}인 `if year else {}`
            #  경로 둘 다 실제로 터짐) set.update()는 임의 iterable을 받으므로
            # 값이 set이든 튜플이든 동일하게 동작한다(결과 불변).
            busy.update(day_map.get(tid, ()))
    best_dist, best_day = -1, None
    for off in offsets:
        cand = week_start + off
        dist = min((abs(cand - d) for d in busy), default=99)
        if dist >= gap:
            return cand
        if dist > best_dist:
            best_dist, best_day = dist, cand
    # gap은 못 맞추지만 최소한 '같은 날'은 아닌 날이 있으면 그 날을 쓴다.
    if best_day is not None and best_dist >= 1:
        return best_day
    return default_day


# ─────────────────────────────────────────────────────────────────────────────
# [2026-09 신설] "나" 슬롯 치환 + 골/어시 합계 정합성 복원
#
# 신민용 리포트: "지금 2대0인데 우측 보면 골이 3개 어시가 3개로 뜨는데?
# 저거 플레이어랑 겹치면 저렇게 되는거 같아" — 정확한 진단이었다.
#
# [원인] tactical_engine은 AI 11명에게 팀 득점(hs)을 정확히 hs개로 쪼개
# 배분한다(_finish_attack에서 골 1개마다 shooter 1명 +1). 그런데 그 22명
# 평점표엔 "나"가 없으므로, 각 엔진은 내 포지션과 같은 슬롯 하나를 찾아
# 내 실제 기록(_player_perf 결과)으로 통째로 덮어써 왔다. 이때 그 자리에
# 있던 AI의 골(g0)은 사라지고 내 골(g1)이 들어오므로,
#     화면 합계 = hs - g0 + g1
# 이 되어 g0 != g1이면 항상 스코어와 안 맞는다. 양방향으로 틀린다 —
#   · 내가 g0보다 많이 넣었으면 합계 > 스코어 (2-0인데 골 3개 ← 리포트 케이스)
#   · 내가 g0보다 적게 넣었으면 합계 < 스코어 (실측: OVR92 ST는 49.5%가 이 쪽)
#
# [수정 방침] 내 기록(g1/a1)은 이미 my_player/시즌 통계에 그대로 들어간
# "공식 기록"이라 여기서 절대 안 건드린다. 대신 나머지 AI 슬롯을 조정해서
# 팀 합계를 실제 스코어에 맞춘다. 불변식 3개를 모두 지킨다:
#   (1) sum(goals[side]) == 그 팀 득점
#   (2) sum(assists[side]) <= 그 팀 득점      (골 1개에 어시 최대 1개)
#   (3) 선수별 assists <= 득점 - 본인 goals   (자기 골에 자기 어시 금지)
# 조정된 슬롯은 평점도 같이 보정한다(tactical_engine._build_player_ratings의
# 실제 계수: 골 0.75 / 어시 0.4 — 골만 줄이고 평점을 그대로 두면 "골 0인데
# 평점 8.5"가 남는다).
#
# [결정론] 이 함수들은 random을 전혀 안 쓴다 — 같은 시드 → 같은 결과가
# 유지돼야 하므로(competition_common 모듈 상단 리팩터링 원칙) 조정 대상
# 선택은 전부 결정적인 정렬로만 한다.
# ─────────────────────────────────────────────────────────────────────────────

# 공격 가담도 순위 — 골을 되돌려줄 때 "누가 넣었을 법한가" 순서.
_ATTACK_RANK = {
    "ST": 0, "CF": 0, "SS": 0, "LW": 1, "RW": 1, "CAM": 2,
    "LM": 3, "RM": 3, "CM": 4, "CDM": 5,
    "LWB": 6, "RWB": 6, "LB": 7, "RB": 7, "CB": 8, "SW": 8, "GK": 9,
}

_RATING_PER_GOAL = 0.75    # tactical_engine._build_player_ratings와 동일
_RATING_PER_ASSIST = 0.4


def _slot_rank(entry):
    if not entry:
        return 99
    if entry.get("is_gk"):
        return 9
    return _ATTACK_RANK.get(entry.get("position") or "", 4)


def _bump_rating(entry, d_goals=0, d_assists=0):
    if not entry or (not d_goals and not d_assists):
        return
    try:
        r = float(entry.get("rating") or 6.3)
    except (TypeError, ValueError):
        r = 6.3
    r += d_goals * _RATING_PER_GOAL + d_assists * _RATING_PER_ASSIST
    entry["rating"] = round(max(3.0, min(10.0, r)), 1)


def reconcile_side_stats(lst, my_idx, team_score):
    """한 팀 11명 평점 리스트의 골/어시 합계를 team_score에 맞춘다.

    lst      : player_ratings["home"] 또는 ["away"] (빈 슬롯은 None)
    my_idx   : "나"로 치환된 슬롯 인덱스(없으면 None) — 이 슬롯은 안 건드린다
    team_score: 그 팀의 실제 득점

    제자리(in-place) 수정이며 반환값은 없다.
    """
    if not lst:
        return
    try:
        team_score = max(0, int(team_score or 0))
    except (TypeError, ValueError):
        return
    idxs = [i for i, r in enumerate(lst) if r]
    others = [i for i in idxs if i != my_idx]
    if not others:
        return

    def _g(i):
        try:
            return max(0, int(lst[i].get("goals", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _a(i):
        try:
            return max(0, int(lst[i].get("assists", 0) or 0))
        except (TypeError, ValueError):
            return 0

    # ── (1) 골 합계를 스코어에 정확히 맞춘다
    d = sum(_g(i) for i in idxs) - team_score
    if d > 0:
        # 내가 그 슬롯 AI보다 많이 넣었다 → 그만큼 나머지에서 회수.
        # 많이 넣은 슬롯 → 공격 가담도 낮은 슬롯 순으로 1골씩 깎는다.
        order = sorted(others, key=lambda i: (-_g(i), -_slot_rank(lst[i]), i))
        while d > 0:
            moved = False
            for i in order:
                if d <= 0:
                    break
                if _g(i) > 0:
                    lst[i]["goals"] = _g(i) - 1
                    _bump_rating(lst[i], d_goals=-1)
                    d -= 1
                    moved = True
            if not moved:
                break   # 더 깎을 골이 없다(내 골만으로 스코어 초과 — 이론상 불가)
    elif d < 0:
        # 내가 그 슬롯 AI보다 적게 넣었다 → 남은 골의 주인을 다시 찾아준다.
        # 선발 → 이미 득점한 슬롯 → 공격 가담도 높은 슬롯 → 오래 뛴 선수
        # → OVR 높은 순.
        #
        # [2026-09 수정, 신민용 리포트: "왜 오른쪽엔 AI00RL이 2골 넣었다고
        # 표시돼?"] 예전엔 "이미 득점한 슬롯"이 1순위라, 68분에 교체 투입돼
        # 72분에 한 골 넣은 선수가 17분 골까지 받아 2골이 되곤 했다 — 그
        # 선수는 17분엔 그라운드에 있지도 않았다. 선발과 출전시간을 앞세워
        # "그 시간에 뛰고 있었을 가능성이 높은 선수"에게 먼저 돌린다.
        # (merge_my_slot이 1순위로 '골/어시가 나와 같은 슬롯'을 고르게 된
        #  뒤로는 이 재분배 자체가 잘 일어나지 않는다 — 그래도 남는 경우의
        #  결과를 덜 이상하게 만드는 안전망이다.)
        order = sorted(
            others,
            key=lambda i: (0 if lst[i].get("started", True) else 1,
                           0 if _g(i) > 0 else 1, _slot_rank(lst[i]),
                           -float(lst[i].get("minutes", 90) or 0),
                           -float(lst[i].get("ovr", 50) or 50), i))
        k = 0
        while d < 0:
            i = order[k % len(order)]
            lst[i]["goals"] = _g(i) + 1
            # 골이 슈팅/유효슈팅보다 많아지면 안 되므로 같이 올린다.
            _sh_on = max(int(lst[i].get("shots_on", 0) or 0), _g(i))
            lst[i]["shots_on"] = _sh_on
            lst[i]["shots"] = max(int(lst[i].get("shots", 0) or 0), _sh_on)
            _bump_rating(lst[i], d_goals=1)
            d += 1
            k += 1

    # ── (2)(3) 어시: 골 1개에 최대 1개, 득점자 자신은 그 골의 어시 불가
    for i in others:
        cap = max(0, team_score - _g(i))
        if _a(i) > cap:
            _old = _a(i)
            lst[i]["assists"] = cap
            _bump_rating(lst[i], d_assists=cap - _old)
    d = sum(_a(i) for i in idxs) - team_score
    if d > 0:
        order = sorted(others, key=lambda i: (-_a(i), -_slot_rank(lst[i]), i))
        while d > 0:
            moved = False
            for i in order:
                if d <= 0:
                    break
                if _a(i) > 0:
                    lst[i]["assists"] = _a(i) - 1
                    _bump_rating(lst[i], d_assists=-1)
                    d -= 1
                    moved = True
            if not moved:
                break


def find_my_slot(labels, my_position, starters=None, eligible=None):
    """내 포지션과 같은 슬롯 인덱스(정확 일치 → POSITION_COMPAT 호환 →
    GK 아닌 아무 자리 순). 예전에 각 엔진에 그대로 복붙돼 있던 로직을
    한 곳으로 모은 것 — 동작은 완전히 동일하다.

    [2026-09 교체 시스템 대응] 평점표에 교체 투입 선수까지 들어오면서
    리스트에 같은 라벨이 둘 이상 있을 수 있다. starters(각 항목이 선발인지
    여부)를 받으면 선발 슬롯만 후보로 본다 — "나"는 항상 선발로 뛰므로.

    eligible: starters에 더해 걸 추가 조건(같은 길이의 bool 리스트).
        merge_my_slot이 "교체로 빠지지 않은 선발"을 1순위로 찾을 때 쓴다 —
        자세한 이유는 merge_my_slot 주석 참고.
    """
    def _ok(i):
        if starters is not None and not starters[i]:
            return False
        if eligible is not None and not eligible[i]:
            return False
        return True

    for i, lab in enumerate(labels):
        if lab == my_position and _ok(i):
            return i
    from constants import POSITION_COMPAT
    for want in POSITION_COMPAT.get(my_position, [my_position]):
        for i, lab in enumerate(labels):
            if lab == want and _ok(i):
                return i
    for i, lab in enumerate(labels):
        if lab is not None and lab != "GK" and _ok(i):
            return i
    return None


def augment_team_goal_events(p, is_home, hs, as_, goals, assists, played,
                             events, engine_plog, player_ratings):
    """[2026-09 신설, 신민용 리포트: "경기 상세에서 나만 뜨는 것 같은데
    다른 선수들이 골 넣어도 다 뜨게 해줘"]

    경기 상세의 타임라인에 "내가 골도 어시도 아닌 우리 팀 나머지 득점"을
    실제 득점자 이름과 함께 채워 넣는다.

    이 처리는 원래 game_engine._augment_events_with_names가 하는데, 그
    함수는 _write_match_log(리그 경기 전용) 안에서만 불렸다 — 대회 엔진
    (컵/챔스/유로파/컨퍼런스/클럽월드컵/국내컵/국내슈퍼컵/국제대회/승강
    PO)은 _save_match_detail을 직접 호출하므로 이 단계를 통째로 건너뛰었고,
    그래서 대회 경기 상세 타임라인에는 내 이벤트만 떴다. 여기서 같은
    함수를 재사용해 대회 쪽도 리그와 동일하게 만든다.

    [호출 위치 주의] 반드시 merge_my_slot **이전에** 부를 것.
    득점자 이름은 possession_log의 scorer_id를 player_ratings로 뒤집어
    찾는데, merge_my_slot이 내 슬롯을 덮어쓰고 나면 "내가 맡은 포지션의
    AI"가 리스트에서 사라져 그 선수가 넣은 골의 이름을 못 찾게 된다
    (game_engine이 scorer_ratings라는 치환 전 스냅샷을 따로 두는 것과
    같은 이유 — 여기서는 호출 순서로 같은 효과를 낸다).

    실패해도 경기 저장 자체는 절대 막으면 안 되므로 전부 삼키고 원래
    events를 그대로 돌려준다(리그 쪽과 동일한 방어).
    """
    try:
        if not played or not engine_plog or not player_ratings:
            return events
        from game_engine import _augment_events_with_names
        # c/hid/aid/live_record는 그 함수가 실제로 안 쓰는 레거시 인자다.
        return _augment_events_with_names(
            None, p, is_home, 0, 0, hs, as_, goals, assists, played, events,
            engine_plog=engine_plog, player_ratings=player_ratings)
    except Exception:
        return events


def merge_my_slot(player_ratings, is_home, my_position, my_entry, hs, as_):
    """player_ratings에서 내 슬롯을 my_entry로 치환하고 양 팀 합계를 복원한다.

    my_entry의 "position"은 치환된 슬롯의 라벨로 덮어쓴다(호출부가 뭘 넣든).
    반환: (side_key, idx) — 치환할 자리를 못 찾았으면 (None, None).

    [주의] 치환이 일어나지 않은 팀(상대팀)도 reconcile을 한 번 통과시킨다 —
    전술엔진 원본은 이미 정합하므로 아무것도 안 바뀌지만, 혹시 엔진 쪽에
    회귀가 생기면 화면에 안 틀린 값이 나가도록 하는 안전망이다.

    ── [2026-09 버그수정, 신민용 리포트: "왜 교체는 2번 되었는데 위에
    교체되었다는건 1명만 표시되어 있어?"] ──────────────────────────
    원인이 바로 이 치환이었다. 예전엔 포지션만 맞으면 아무 선발 슬롯이나
    골랐는데, 하필 **경기 도중 교체로 빠진 선발**의 자리를 고르면 그
    선수의 subbed_out/off_min 키가 my_entry로 통째로 덮어써져 사라졌다.
    그러면 라인업 목록에서 그 선수의 "↓62'" 표시가 증발해, 🔁 교체 섹션엔
    교체가 2건인데 라인업엔 ↓가 1개만 보이는 불일치가 생긴다. 게다가
    교체 섹션은 여전히 그 선수 이름을 부르는데 라인업엔 그 이름이 아예
    없어서 더 이상해 보인다.

    수정: **풀타임을 뛴 선발**을 1순위로 고른다("나"는 교체로 안 빠지므로
    의미상으로도 이쪽이 맞다). 같은 포지션 선발이 전부 교체로 빠진 드문
    경우에만 예전처럼 그 자리를 쓰되, 그때는 그 슬롯의 교체 정보를
    my_entry에 그대로 물려줘서 ↓ 개수만은 어긋나지 않게 한다.

    [트레이드오프 — 실측] 이 우선순위(풀타임 + 득점 무관여) 때문에 "내
    포지션과 정확히 같은 슬롯"을 못 고르는 경우가 생긴다. 우리 팀 득점이
    있는 65건 표본 기준:
        · 득점/어시한 선수를 덮어쓴 경우: 22건 → 0건  (타임라인 불일치 제거)
        · 내 포지션과 정확히 일치한 슬롯: 36건 → 27건 (9건이 호환 포지션으로)
    즉 "화면상 내가 서 있는 칸의 라벨"을 일부 포기하고 "득점 기록의
    정합성"을 얻는 교환이다. 그럼에도 이쪽을 택한 이유:
      · 반대로 하면(정확 포지션 우선) 그 슬롯의 교체 정보를 물려받게 돼
        "나 ↓62'" — 즉 내가 62분에 교체된 것처럼 표시된다. 내 선수는
        실제로 풀타임을 뛰었으므로(_player_perf는 교체를 모델링하지 않음)
        이건 내 기록에 대한 명백한 거짓 표시다.
      · 슬롯 라벨이 호환 포지션으로 바뀌는 건 "팀 라인업 화면에서 내가
        어느 칸에 그려지는가"의 문제일 뿐, 내 개인 기록(my_position 등)은
        _get_field_pos(p)로 따로 저장되므로 영향을 받지 않는다.
      · 반대로 하면(정확 포지션 우선) 신민용이 실제로 본 그 화면이 그대로
        재현된다 — 스트라이커인 내가 득점한 스트라이커 자리를 덮어써서,
        그 골이 68분 교체 투입 선수에게 얹히는 형태.
    정확 포지션 표시를 더 중시한다면 아래 1·2순위 호출의 eligible 인자를
    빼면 예전 동작으로 돌아간다(대신 위 두 버그가 함께 돌아온다).
    """
    if player_ratings is None:
        return None, None
    side_key = "home" if is_home else "away"
    my_list = player_ratings.get(side_key)
    idx = None
    if my_list:
        labels = [r.get("position") if r else None for r in my_list]
        starters = [bool(r.get("started", True)) if r else False for r in my_list]
        # 교체로 빠지지 않은(=풀타임) 선발만 2순위 후보.
        full_match = [(not bool(r.get("subbed_out"))) if r else False for r in my_list]

        # ── 1순위: 풀타임 + 공격포인트가 내 기록과 정확히 같은 슬롯 ──
        # [2026-09 버그수정, 신민용 리포트: "좌측엔 AI00QI랑 AI00RL가
        # 넣었다고 뜨는데 왜 오른쪽엔 AI00RL이 2골 넣었다고 표시돼?"]
        # 원인: 하필 **득점한 선수**의 자리를 내가 덮어쓰면(내 골은 0),
        # 그 팀 표시 골 합계가 스코어보다 모자라진다. 그러면
        # reconcile_side_stats가 "남은 골의 주인"을 다시 찾아주는데, 그
        # 우선순위가 "이미 득점한 슬롯"이라 68분에 교체 투입된 AI00RL에게
        # 2번째 골이 얹혔다 — 정작 타임라인은(전술엔진 원본 기준) 17분
        # AI00QI, 72분 AI00RL이라 양쪽이 어긋난다. 게다가 17분 골을 68분에
        # 들어온 선수가 넣었다는 말이 되어 시간상으로도 불가능해진다.
        #
        # 애초에 "골/어시가 내 기록과 같은 슬롯"을 고르면 재분배 자체가
        # 일어나지 않는다(d == 0) — 그러면 타임라인과 라인업이 저절로
        # 일치한다. 내가 0골 0어시인 보통의 경우엔 "득점에 관여 안 한
        # 풀타임 선발"을 고르는 것과 같은 뜻이다.
        try:
            _my_g = max(0, int(my_entry.get("goals", 0) or 0))
            _my_a = max(0, int(my_entry.get("assists", 0) or 0))
        except (TypeError, ValueError):
            _my_g = _my_a = 0

        def _same_points(r):
            if not r:
                return False
            try:
                return (int(r.get("goals", 0) or 0) == _my_g
                        and int(r.get("assists", 0) or 0) == _my_a)
            except (TypeError, ValueError):
                return False

        clean = [full_match[i] and _same_points(r) for i, r in enumerate(my_list)]
        idx = find_my_slot(labels, my_position, starters=starters, eligible=clean)
        if idx is None:
            # 2순위 — 같은 골/어시 슬롯이 없으면 풀타임만이라도 지킨다
            # (교체 표시 보존. 이때는 reconcile이 차이를 메운다).
            idx = find_my_slot(labels, my_position, starters=starters, eligible=full_match)
        if idx is None:
            # 3순위 폴백 — 같은 포지션 선발이 전부 교체로 빠진 경우.
            idx = find_my_slot(labels, my_position, starters=starters)
        if idx is not None:
            _old = my_list[idx] or {}
            my_entry = dict(my_entry)
            my_entry["position"] = labels[idx]
            # 폴백으로 "교체된 선발" 자리를 쓰게 됐다면 그 슬롯의 교체
            # 표시(↓분)를 물려받는다 — 안 그러면 위에 적은 개수 불일치가
            # 그대로 남는다. 1순위(풀타임)로 잡혔으면 이 키들은 애초에
            # 없거나 False라 아무것도 안 붙는다.
            for _k in ("started", "subbed_in", "subbed_out", "on_min", "off_min", "minutes"):
                if _k in _old and _k not in my_entry:
                    my_entry[_k] = _old[_k]
            my_list[idx] = my_entry
    reconcile_side_stats(player_ratings.get("home"),
                         idx if is_home else None, hs)
    reconcile_side_stats(player_ratings.get("away"),
                         None if is_home else idx, as_)
    return (side_key, idx) if idx is not None else (None, None)


def _tournament_effective_ovr(ovr: float) -> float:
    """UEFA/대륙 클럽대항전 매치 확률 계산 전용 OVR 변환("중" 곡선).
    _TOURNAMENT_OVR_CURVE_PTS 구간을 선형 보간하고, 표 밖(70 미만/100 초과)은
    가장 가까운 구간의 기울기를 그대로 연장한다."""
    pts = _TOURNAMENT_OVR_CURVE_PTS
    if ovr < pts[0][0]:
        return ovr + (pts[0][1] - pts[0][0])
    if ovr > pts[-1][0]:
        return ovr + (pts[-1][1] - pts[-1][0])
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= ovr <= x1:
            t = (ovr - x0) / (x1 - x0) if x1 > x0 else 0
            return y0 + t * (y1 - y0)
    return ovr


def match_outcome(h_ovr, a_ovr, neutral=False):
    """'home'/'draw'/'away' (KO 무승부 → 승부차기).
    champions_engine._match_outcome과 완전히 동일 — 단, 위 비선형 곡선으로
    변환한 effective OVR을 쓴다(원본 공식/계수는 그대로).

    [2026-09 확장, 신민용 확정: "챔스/유로파/컨퍼런스/클럽월드컵은 결승과
    3/4위전, 대륙 슈퍼컵은 4강부터 전부 원정 vs 원정(홈 어드밴티지 없음)"]
    기존 공식은 문서화된 이름과 달리 diff=0이어도 홈 46%/원정 30%로
    비대칭이다(국내 슈퍼컵을 만들 때 이미 확인된 특성) — neutral=True면
    diff=0에서 정확히 hw==aw가 되는 진짜 대칭 공식을 쓴다(기울기 0.022·
    무승부 폭 감소 계수 0.009는 그대로 유지해 이변 확률 감각만 보존).
    호출부는 CompetitionConfig.neutral_stages에 그 경기의 stage가
    들어있는지로 이 플래그를 결정한다(sim_ai_match/simulate_my_match가
    자동으로 계산해서 넘긴다 — 호출자가 매번 판단할 필요 없음)."""
    h_eff = _tournament_effective_ovr(h_ovr)
    a_eff = _tournament_effective_ovr(a_ovr)
    diff = h_eff - a_eff
    if neutral:
        dw = max(0.05, 0.24 - abs(diff) * 0.009)
        half = max(0.0, 1.0 - dw) / 2.0
        hw = max(0.04, min(0.95, half + diff * 0.022))
        aw = max(0.02, min(0.95, half - diff * 0.022))
    else:
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


def resolve_pso(h_ovr, a_ovr, neutral=False):
    """champions_engine._resolve_pso와 완전히 동일.
    [2026-08 신설, 신민용 요청: "PSO에서 OVR 차이가 승부차기 결과에 지나치게
    약하게 반영된다 — 챔스 이변이 실제보다 너무 잦은 원인 중 하나"] 계수를
    하드코딩 리터럴이 아니라 모듈 상수(PSO_OVR_COEF)로 빼서, A/B 테스트
    스크립트가 함수 코드를 손대지 않고 이 상수만 monkeypatch해서 여러 값을
    비교할 수 있게 한다.
    [2026-08 확정, 신민용 요청] 0.006 → 0.010. 80회×16시즌 A/B(0.006/0.008/
    0.010)로 실측 검증: 0.008은 0.006과 거의 차이 없었고, 0.010에서만
    <85 우승(0.4%→0.1%)·85-89 우승(4.1%→3.0%)·서로다른우승팀(95→88개)이
    부작용(95+ 우승 과다 등) 없이 확실히 더 개선됨을 확인.
    [2026-08 추가] 위 match_outcome과 동일하게, 여기서도 원본 OVR이 아니라
    비선형 변환한 effective OVR로 계산한다(강팀이 PSO에서도 그 우위를
    일관되게 유지하도록).
    [2026-09 확장] neutral=True(결승/3·4위전 등 원정 vs 원정)여도 PSO
    자체는 애초에 홈/원정 구분 없이 순수 OVR 차이만 반영하는 공식이라
    (p_home이 "홈팀이 이길 확률"이라는 이름일 뿐 편향은 없음) 그대로 써도
    되지만, 시그니처는 match_outcome과 맞춰 호출부를 단순하게 둔다.
    """
    h_eff = _tournament_effective_ovr(h_ovr)
    a_eff = _tournament_effective_ovr(a_ovr)
    p_home = 0.5 + max(-0.1, min(0.1, (h_eff - a_eff) * PSO_OVR_COEF))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


def winner_of(m):
    """champions_engine._winner_of와 완전히 동일."""
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home_team_id"] if m["home_score"] > m["away_score"] else m["away_team_id"]


def first_stage_for(n):
    """champions_engine._first_stage_for와 완전히 동일."""
    if n >= 32:
        return "R32"
    if n >= 16:
        return "R16"
    if n >= 8:
        return "QF"
    if n >= 4:
        return "SF"
    return "F"


def league_phase_pairs(entries, games, my_tid):
    """champions_engine._league_phase_pairs와 완전히 동일."""
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


# ─────────────────────────────────────────────
# B그룹 — 로직 동일, cfg로 테이블명/문자열만 주입
# ─────────────────────────────────────────────

_entry_cache: dict = {}


def clear_entry_cache():
    _entry_cache.clear()


def entry(cfg, tid, team_id):
    """champions_engine._entry와 동일(테이블명만 cfg)."""
    key = (cfg.match_table, tid, team_id)
    cached = _entry_cache.get(key)
    if cached is not None:
        return cached
    conn = get_conn()
    row = conn.execute(
        f"SELECT * FROM {cfg.entry_table} WHERE tournament_id=? AND team_id=?",
        (tid, team_id)).fetchone()
    conn.close()
    result = dict(row) if row else {"team_name": "?", "flag": "", "ovr": 50}
    _entry_cache[key] = result
    return result


def entry_from(lg, standing_row, cl_rank=1):
    """champions_engine._entry_from과 완전히 동일 — 대회 무관 공용."""
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


def build_tournament(cfg, year, continent, entries, my_tid, team_cap, games):
    """champions_engine._build_tournament과 동일 — team_cap/games는 호출부
    (각 대회 엔진의 C그룹 함수)가 넘겨준다."""
    name = cfg.competition_name_by_continent.get(continent, cfg.award_prefix)

    entries.sort(key=lambda e: e["ovr"], reverse=True)
    n = len(entries) - (len(entries) % 2)
    n = min(n, team_cap)
    entries = entries[:n]
    if n < games + 1:
        return None

    my_in = 1 if (my_tid and any(e["team_id"] == my_tid for e in entries)) else 0
    my_reg_tid = my_tid if my_in else 0

    conn = get_conn()
    c = conn.cursor()
    c.execute(f"""INSERT INTO {cfg.tournament_table}(year, continent, name, status,
                    my_in, my_team_id, my_qualified)
                 VALUES(?,?,?,?,?,?,?)""",
              (year, continent, name, "league", my_in, my_reg_tid, my_in))
    tid = c.lastrowid

    entry_rows = [(tid, e["team_id"], e["team_name"], e["flag"],
                   e["country"], e["grade"], e["ovr"]) for e in entries]
    c.executemany(f"""INSERT INTO {cfg.entry_table}
                         (tournament_id, team_id, team_name, flag, country,
                          grade, ovr, alive)
                         VALUES(?,?,?,?,?,?,?,1)""", entry_rows)

    w0 = cfg.league_weeks[0]
    match_rows = []
    for rd_idx, home, away in league_phase_pairs(entries, games, my_tid):
        wk = w0 + rd_idx
        is_my = 1 if my_tid in (home["team_id"], away["team_id"]) else 0
        # [2026-09 신설] "그 경기 당시 내 팀"을 경기 행에 같이 박는다 —
        # cup_matches.my_team_id와 완전히 같은 원칙(database.py 주석 참고).
        # 대회 단위 my_team_id는 이적 시 갱신되므로(resync_my_registration)
        # 과거 경기의 홈/원정 판정 기준으로 쓸 수 없다.
        match_rows.append((tid, "league", wk,
                   home["team_id"], away["team_id"], is_my,
                   my_tid if is_my else 0))
    c.executemany(f"""INSERT INTO {cfg.match_table}
                             (tournament_id, stage, week,
                              home_team_id, away_team_id,
                              home_score, away_score, is_my, slot, my_team_id)
                             VALUES(?,?,?,?,?,-1,-1,?,0,?)""", match_rows)
    c.execute(f"UPDATE {cfg.tournament_table} SET status='league', first_stage='league' WHERE id=?",
              (tid,))
    conn.commit()
    conn.close()
    return tid


def sim_ai_match(cfg, t, m, my_played=False, conn=None, reason="injury", batch=None, p=None):
    """champions_engine._sim_ai_match와 동일(테이블명·로그 접두사만 cfg).

    [2026-08 최적화] p를 넘기면 get_player() 재조회를 생략한다 —
    process_one이 미처리 경기 개수만큼 이 함수를 루프 안에서 부르므로,
    호출부가 이미 조회해둔 p를 그대로 넘기면 그 루프 전체에서 DB 왕복이
    한 번으로 줄어든다."""
    from game_engine import add_log, get_player, _gen_score, _week_intl_cl_day
    if p is None:
        p = get_player()
    he = entry(cfg, t["id"], m["home_team_id"])
    ae = entry(cfg, t["id"], m["away_team_id"])

    outcome = match_outcome(he["ovr"], ae["ovr"], neutral=(m["stage"] in cfg.neutral_stages))
    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "league")
    if outcome == "draw" and is_ko:
        win_home, pso_score = resolve_pso(he["ovr"], ae["ovr"], neutral=(m["stage"] in cfg.neutral_stages))
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, he["ovr"] - ae["ovr"])

    day = _week_intl_cl_day(m["week"], p or {}) if m["is_my"] else m.get("day")

    _absence = reason if m["is_my"] else None
    _row = (hs, as_, pso_winner, pso_score, day, _absence, m["id"])
    if batch is not None:
        batch.append(_row)
    else:
        _own = conn is None
        if _own:
            conn = get_conn()
        conn.execute(f"""UPDATE {cfg.match_table} SET home_score=?, away_score=?,
                        pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
                     _row)
        if _own:
            conn.commit()
            conn.close()

    if m["is_my"]:
        my_tid = p.get("current_team_id", 0) if p else 0
        if my_tid in (m["home_team_id"], m["away_team_id"]):
            stage_ko = cfg.stage_ko.get(m["stage"], "")
            pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
            add_log(f"🏆 {t['name']} {stage_ko}  "
                    f"{he['flag']}{he['team_name']} {hs}-{as_} {ae['flag']}{ae['team_name']}{pso_txt}",
                    "match")
            if not my_played:
                _reason_ko = {"injury": "부상", "suspension": "출전정지", "bench": "벤치"}.get(reason, reason)
                add_log(f"   🚑 {_reason_ko}(으)로 {cfg.award_prefix} 경기 결장", "match")


def get_league_standings(cfg, tid):
    """champions_engine.get_cl_league_standings와 동일(테이블명만 cfg)."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        f"SELECT * FROM {cfg.entry_table} WHERE tournament_id=?", (tid,)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        f"""SELECT * FROM {cfg.match_table} WHERE tournament_id=?
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


def finalize_league_phase(cfg, t, direct_cut, po_pool, playoff_week, start_knockout_fn):
    """champions_engine._finalize_league_phase와 동일 — direct_cut/po_pool/
    playoff_week은 호출부(각 대회 엔진)가 자기 C그룹 상수로 계산해 넘긴다.
    start_knockout_fn: 이 모듈의 start_knockout을 그대로 넘기되, 호출부가
    자기 team_cap 등을 이미 partial로 바인딩해서 넘긴다(순환 의존 회피)."""
    from game_engine import add_log, get_player
    tid = t["id"]

    rows = get_league_standings(cfg, tid)
    direct = rows[:direct_cut]
    playoff_teams = rows[direct_cut:direct_cut + po_pool]
    eliminated = rows[direct_cut + po_pool:]

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    conn = get_conn(); c = conn.cursor()
    for r in eliminated:
        c.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?",
                  (tid, r["team_id"]))

    half = len(playoff_teams) // 2
    seeded, unseeded = playoff_teams[:half], playoff_teams[half:]
    po_pairs = list(zip(seeded, reversed(unseeded)))

    if po_pairs:
        for slot, (home, away) in enumerate(po_pairs):
            is_my = 1 if my_tid in (home["team_id"], away["team_id"]) else 0
            c.execute(f"""INSERT INTO {cfg.match_table}
                         (tournament_id, stage, week, home_team_id, away_team_id,
                          home_score, away_score, is_my, slot, my_team_id)
                         VALUES(?,?,?,?,?,-1,-1,?,?,?)""",
                      (tid, "PO", playoff_week,
                       home["team_id"], away["team_id"], is_my, slot,
                       my_tid if is_my else 0))
        c.execute(f"UPDATE {cfg.tournament_table} SET status='playoff' WHERE id=?", (tid,))
        conn.commit()
        conn.close()
    else:
        conn.commit()
        conn.close()
        start_knockout_fn(t, [r["team_id"] for r in direct])

    add_log(f"🏆 {t['name']} 리그 스테이지 종료 → 1~{direct_cut}위 직행, "
            f"{direct_cut+1}~{direct_cut+po_pool}위 플레이오프", "event")
    if my_tid and any(r["team_id"] == my_tid for r in eliminated):
        my_rank = next((i + 1 for i, r in enumerate(rows) if r["team_id"] == my_tid), 0)
        my_row = next((r for r in rows if r["team_id"] == my_tid), None)
        if my_rank and my_row:
            result_txt = (f"리그 스테이지 {my_rank}위 "
                          f"({my_row['w']}승{my_row['d']}무{my_row['l']}패, {my_row['pts']}점)")
        else:
            result_txt = "리그 스테이지"
        record_my_exit(cfg, t, result_txt)


def finalize_playoff(cfg, t, start_knockout_fn):
    """champions_engine._finalize_playoff와 동일."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    po_matches = [dict(r) for r in conn.execute(
        f"SELECT * FROM {cfg.match_table} WHERE tournament_id=? AND stage='PO' ORDER BY slot",
        (tid,)).fetchall()]
    direct_ids = [r["team_id"] for r in conn.execute(
        f"SELECT team_id FROM {cfg.entry_table} WHERE tournament_id=? AND alive=1", (tid,)).fetchall()]
    conn.close()

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    winners, losers = [], []
    conn = get_conn(); c = conn.cursor()
    for m in po_matches:
        w = winner_of(m)
        l = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
        winners.append(w)
        losers.append(l)
        c.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?", (tid, l))
        if my_tid and l == my_tid:
            conn.commit(); conn.close()
            record_my_exit(cfg, t, "플레이오프")
            conn = get_conn(); c = conn.cursor()
    conn.commit(); conn.close()

    po_team_ids = {m["home_team_id"] for m in po_matches} | {m["away_team_id"] for m in po_matches}
    direct_only = [tid_ for tid_ in direct_ids if tid_ not in po_team_ids]
    qualifiers = direct_only + winners

    add_log(f"🏆 {t['name']} 플레이오프 종료 → {cfg.stage_ko.get(first_stage_for(len(qualifiers)), '')} 진출팀 확정", "event")
    start_knockout_fn(t, qualifiers, direct_ids=direct_only, winner_ids=winners)


def start_knockout(cfg, t, qualifier_ids, round_weeks, direct_ids=None, winner_ids=None):
    """champions_engine._start_knockout과 동일 — round_weeks는 호출부의
    CL_ROUND_WEEKS 상당 딕셔너리를 그대로 넘긴다."""
    from game_engine import get_player
    tid = t["id"]
    conn = get_conn()
    infos = {r["team_id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM {cfg.entry_table} WHERE tournament_id=?", (tid,)).fetchall()}
    conn.close()

    if direct_ids and winner_ids and len(direct_ids) == len(winner_ids):
        d_sorted = sorted(direct_ids, key=lambda tid_: infos.get(tid_, {}).get("ovr", 0), reverse=True)
        w_sorted = sorted(winner_ids, key=lambda tid_: infos.get(tid_, {}).get("ovr", 0))
        pairs = list(zip(d_sorted, w_sorted))
    else:
        ranked = sorted(qualifier_ids, key=lambda tid_: infos.get(tid_, {}).get("ovr", 0), reverse=True)
        half = len(ranked) // 2
        top, bottom = ranked[:half], ranked[half:]
        pairs = list(zip(top, reversed(bottom)))

    first_stage = first_stage_for(len(qualifier_ids))
    next_week = round_weeks.get(first_stage, round_weeks["R16"])

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    conn = get_conn(); c = conn.cursor()
    for slot, (home, away) in enumerate(pairs):
        is_my = 1 if my_tid in (home, away) else 0
        c.execute(f"""INSERT INTO {cfg.match_table}
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot, my_team_id)
                     VALUES(?,?,?,?,?,-1,-1,?,?,?)""",
                  (tid, first_stage, next_week, home, away, is_my, slot,
                   my_tid if is_my else 0))
    c.execute(f"UPDATE {cfg.tournament_table} SET status='ko', first_stage=? WHERE id=?",
              (first_stage, tid))
    conn.commit()
    conn.close()


def advance_round(cfg, t, cur_stage, next_stage, round_weeks):
    """champions_engine._advance_round와 동일."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        f"""SELECT * FROM {cfg.match_table} WHERE tournament_id=? AND stage=?
           ORDER BY slot""", (tid, cur_stage)).fetchall()]
    conn.close()
    if not cur:
        return

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    cur_stage_ko = cfg.stage_ko.get(cur_stage, "")
    next_week = round_weeks[next_stage]

    is_sf = (cur_stage == "SF")

    winners = []
    losers  = []
    conn = get_conn()
    c = conn.cursor()
    exit_label = cur_stage_ko
    for m in cur:
        w = winner_of(m)
        loser = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
        winners.append((m["slot"], w))
        if not is_sf:
            c.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?",
                      (tid, loser))
            if my_tid and loser == my_tid:
                conn.commit(); conn.close()
                record_my_exit(cfg, t, exit_label)
                conn = get_conn(); c = conn.cursor()
        else:
            losers.append(loser)

    winners.sort()
    for slot in range(0, len(winners), 2):
        if slot + 1 >= len(winners):
            break
        home, away = winners[slot][1], winners[slot + 1][1]
        is_my = 1 if my_tid in (home, away) else 0
        c.execute(f"""INSERT INTO {cfg.match_table}
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot, my_team_id)
                     VALUES(?,?,?,?,?,-1,-1,?,?,?)""",
                  (tid, next_stage, next_week, home, away, is_my, slot // 2,
                   my_tid if is_my else 0))

    if is_sf and len(losers) == 2:
        tp_home, tp_away = losers[0], losers[1]
        tp_week = round_weeks["TP"]
        is_my_tp = 1 if my_tid in (tp_home, tp_away) else 0
        c.execute(f"""INSERT INTO {cfg.match_table}
                     (tournament_id, stage, week, home_team_id, away_team_id,
                      home_score, away_score, is_my, slot, my_team_id)
                     VALUES(?,?,?,?,?,-1,-1,?,999,?)""",
                  (tid, "TP", tp_week, tp_home, tp_away, is_my_tp,
                   my_tid if is_my_tp else 0))
        te_h = entry(cfg, tid, tp_home); te_a = entry(cfg, tid, tp_away)
        add_log(f"🥉 {t['name']} 3/4위전: {te_h['team_name']} vs {te_a['team_name']} ({tp_week}주차)", "event")

    conn.commit()
    conn.close()
    if t["my_in"]:
        conn = get_conn()
        mr = conn.execute(f"SELECT my_result FROM {cfg.tournament_table} WHERE id=?", (tid,)).fetchone()
        conn.close()
        if not (mr and mr["my_result"]):
            add_log(f"🏆 {t['name']} {cur_stage_ko} 종료 → {cfg.stage_ko[next_stage]} 대진 확정", "event")


def finish_tournament(cfg, t, award_fn=None):
    """champions_engine._finish_tournament과 동일. award_fn: 시상 함수(2차
    이동 전까지는 None으로 넘겨 시상 단계만 건너뛸 수 있음)."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    fm = conn.execute(
        f"""SELECT * FROM {cfg.match_table} WHERE tournament_id=? AND stage='F'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
    tp = conn.execute(
        f"""SELECT * FROM {cfg.match_table} WHERE tournament_id=? AND stage='TP'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
    conn.close()
    if not fm:
        return
    fm = dict(fm)
    winner = winner_of(fm)
    runner = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    third = fourth = None
    if tp:
        tp = dict(tp)
        third  = winner_of(tp)
        fourth = tp["away_team_id"] if third == tp["home_team_id"] else tp["home_team_id"]

    conn = get_conn()
    conn.execute(f"UPDATE {cfg.tournament_table} SET status='done', winner_team_id=? WHERE id=?",
                 (winner, tid))
    conn.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?",
                 (tid, runner))
    if fourth:
        conn.execute(f"UPDATE {cfg.entry_table} SET alive=0 WHERE tournament_id=? AND team_id=?",
                     (tid, fourth))
    from constants import MOMENTUM_START_BY_TYPE
    conn.execute("UPDATE teams SET momentum_type=?, momentum_seasons_left=? WHERE id=?",
                 (cfg.momentum_type, MOMENTUM_START_BY_TYPE[cfg.momentum_type], winner))
    conn.commit()
    conn.close()

    we = entry(cfg, tid, winner)
    add_log(f"🏆 {t['name']} 우승: {we['flag']}{we['team_name']}!", "event")
    if third:
        te = entry(cfg, tid, third)
        add_log(f"🥉 {t['name']} 3위: {te['flag']}{te['team_name']}", "event")

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    # [2026-09 버그수정, 신민용 리포트: "아시아 챔스 뛰고 시즌 중 아스날로
    # 이적했는데, 내가 가기 전에 아스날이 딴 유럽 챔스 우승이 내 발롱도르
    # 점수로 들어온다"] 여기서 my_tid는 "지금 이 순간(대회가 끝난 시점)
    # 내 소속팀"인데, 이걸 그대로 winner/runner/third/fourth와 비교하면
    # 이 대회와 등록 당시(build_tournament 시점) 전혀 무관했던 팀으로
    # 이적한 뒤에도, 마침 그 팀이 이 대회 우승팀이면 "내가 우승했다"고
    # 잘못 기록해버린다 — 실제로 이 대회에서 단 한 경기도 안 뛰었어도.
    # t["my_team_id"](등록 당시 내 팀)가 지금 내 팀과 같을 때만, 즉
    # 시즌 내내(또는 최소한 이 대회 등록 시점부터) 계속 그 팀에 있었을
    # 때만 이 대회 결과를 내 걸로 인정한다 — get_my_champions_matches가
    # 이미 쓰고 있던 "등록 당시 팀과 현재 팀이 같을 때만 내 대회로 본다"
    # 원칙(스케줄 화면)을 결과 기록에도 똑같이 적용.
    if my_tid and t.get("my_in") and t.get("my_team_id") == my_tid:
        if my_tid == winner:
            record_my_exit(cfg, t, "우승")
        elif my_tid == runner:
            record_my_exit(cfg, t, "준우승")
        elif my_tid == third:
            record_my_exit(cfg, t, "3위")
        elif my_tid == fourth:
            record_my_exit(cfg, t, "4위")

    if award_fn:
        award_fn(t, my_tid)


# 결과별 보상(명성/인기/행복) — champions_engine._REWARD와 동일 값을
# 그대로 재사용한다(대회 등급이 달라도 "우승/준우승/..." 보상 체계 자체는
# 공용). champions_engine에 이미 정의돼 있으므로 여기서는 import.
def _get_reward_table():
    from competition.champions_engine import _REWARD
    return _REWARD


def record_my_exit(cfg, t, result):
    """champions_engine._record_my_exit과 동일."""
    from game_engine import add_log, get_player, update_player
    p = get_player()
    if not p:
        return
    my_tid = p.get("current_team_id", 0)

    conn = get_conn()
    conn.execute(f"UPDATE {cfg.tournament_table} SET my_result=? WHERE id=?", (result, t["id"]))
    te = conn.execute(
        f"SELECT team_name, country FROM {cfg.entry_table} WHERE tournament_id=? AND team_id=?",
        (t["id"], my_tid)).fetchone()
    conn.commit()
    conn.close()
    _raw_name = te["team_name"] if te else ""
    team_name = _raw_name

    save_trophy(cfg, t["year"], team_name, t["name"], result)

    conn = get_conn()
    agg = conn.execute(
        f"""SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM {cfg.match_table}
           WHERE tournament_id=? AND my_played=1""", (t["id"],)).fetchone()
    exists = conn.execute(
        f"SELECT id FROM {cfg.history_table} WHERE year=? AND competition=?",
        (t["year"], t["name"])).fetchone()
    if not exists:
        conn.execute(f"""INSERT INTO {cfg.history_table}(year, competition, team_name, result,
                                               goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (t["year"], t["name"], team_name, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()

    _REWARD = _get_reward_table()
    fame_g, pop_g, hap_g = _REWARD.get(result, (0, 0, 0))
    update_player(
        fame=min(100, p.get("fame", 0) + fame_g),
        popularity=min(100, p.get("popularity", 0) + pop_g),
        happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
    )

    icon = "🏆" if result == "우승" else "🏅"
    add_log(f"{icon} {t['year']}년 {t['name']} 최종 성적: {result}  "
            f"(명성 +{fame_g}, 인기 +{pop_g})", "event")


def process_one(cfg, t, week, league_end_week, playoff_week, round_weeks, stage_order,
                 finalize_league_phase_fn, finalize_playoff_fn,
                 advance_round_fn, finish_tournament_fn):
    """[2026-08 이동] champions_engine._process_one과 완전히 동일한 로직 —
    단일 대회의 이번 주차 이하 미진행 경기를 AI로 시뮬레이션한 뒤, 그
    주차가 리그 스테이지 마감/플레이오프 마감/토너먼트 라운드 마감이면
    그에 맞는 마무리 함수를 호출한다.

    league_end_week/playoff_week/round_weeks: 호출부(각 대회 엔진)가
    자기 C그룹 상수로 계산해 넘긴다.
    finalize_league_phase_fn 등 4개: 각 엔진의 얇은 위임 함수(_finalize_
    league_phase 등)를 그대로 넘긴다 — cfg가 이미 그 함수들 내부에
    바인딩돼 있으므로 여기서는 t/week만 알면 된다.

    [2026-08 최적화] 이 함수 안에서 미처리 경기 개수만큼 sim_ai_match를
    부르므로, get_player()를 여기서 한 번만 조회해 넘긴다."""
    from game_engine import get_player
    p = get_player()
    conn = get_conn()
    pending = [dict(r) for r in conn.execute(
        f"""SELECT * FROM {cfg.match_table}
           WHERE tournament_id=? AND week<=? AND home_score=-1 ORDER BY id""",
        (t["id"], week)).fetchall()]

    _batch = []
    for m in pending:
        sim_ai_match(cfg, t, m, batch=_batch, p=p)
    if _batch:
        conn.executemany(
            f"""UPDATE {cfg.match_table} SET home_score=?, away_score=?,
               pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
            _batch)
    conn.commit()
    conn.close()

    if week == league_end_week:
        conn = get_conn()
        remain = conn.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='league' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn.close()
        if remain == 0:
            finalize_league_phase_fn(t)
        return

    if week == playoff_week:
        conn = get_conn()
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='PO'",
            (t["id"],)).fetchone()["n"]
        remain = conn.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='PO' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn.close()
        if total > 0 and remain == 0:
            finalize_playoff_fn(t)
        return

    cur_stage = None
    for stg, wk in round_weeks.items():
        if wk == week:
            cur_stage = stg
            break
    if cur_stage is None:
        return

    conn = get_conn()
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage=?",
        (t["id"], cur_stage)).fetchone()["n"]
    if total == 0:
        conn.close()
        return
    remain = conn.execute(
        f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage=? AND home_score=-1",
        (t["id"], cur_stage)).fetchone()["n"]
    conn.close()
    if remain > 0:
        return

    if cur_stage == "F":
        conn2 = get_conn()
        tp_remain = conn2.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='TP' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn2.close()
        if tp_remain == 0:
            finish_tournament_fn(t)
    elif cur_stage == "TP":
        conn2 = get_conn()
        f_remain = conn2.execute(
            f"SELECT COUNT(*) AS n FROM {cfg.match_table} WHERE tournament_id=? AND stage='F' AND home_score=-1",
            (t["id"],)).fetchone()["n"]
        conn2.close()
        if f_remain == 0:
            finish_tournament_fn(t)
    else:
        nxt = stage_order[stage_order.index(cur_stage) + 1]
        advance_round_fn(t, cur_stage, nxt)


def my_continent(p):
    """[2026-08 이동] champions_engine._my_continent와 완전히 동일 —
    대회 무관 공용(테이블 조회 없음, teams/countries만 봄)."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    from competition.champions_engine import CONTINENT_MAP
    conn = get_conn()
    row = conn.execute(
        """SELECT cn.continent FROM teams t
           JOIN countries cn ON t.country_id = cn.id
           WHERE t.id=?""", (tid,)).fetchone()
    conn.close()
    if not row:
        return None
    return CONTINENT_MAP.get(row["continent"])


def get_tournament(cfg, year, continent):
    """[2026-08 이동] champions_engine.get_cl_tournament과 동일(테이블명만 cfg)."""
    conn = get_conn()
    row = conn.execute(
        f"SELECT * FROM {cfg.tournament_table} WHERE year=? AND continent=? ORDER BY id DESC LIMIT 1",
        (year, continent)).fetchone()
    conn.close()
    return dict(row) if row else None


def my_tournament(cfg, p, year):
    """[2026-08 신설] 내 대륙의 이번 연도 대회(있으면)."""
    cont = my_continent(p)
    if not cont:
        return None
    return get_tournament(cfg, year, cont)


def get_my_match(cfg, week, day=None, p=None, st=None):
    """[2026-08 이동] champions_engine.get_my_cl_match와 완전히 동일."""
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
    t = my_tournament(cfg, p, st["current_year"])
    if not t or t["status"] == "done":
        return None
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != tid:
        return None

    conn = get_conn()
    if day is not None:
        m = conn.execute(
            f"""SELECT * FROM {cfg.match_table}
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?) AND (day=? OR day IS NULL OR day=0)""",
            (t["id"], week, tid, tid, day)).fetchone()
    else:
        m = conn.execute(
            f"""SELECT * FROM {cfg.match_table}
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?)""",
            (t["id"], week, tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    oe = conn.execute(
        f"SELECT team_name, flag FROM {cfg.entry_table} WHERE tournament_id=? AND team_id=?",
        (t["id"], opp_id)).fetchone()
    conn.close()
    return {
        "cl": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],
        "stage": m["stage"],
        "stage_ko": cfg.stage_ko.get(m["stage"], m["stage"]),
        "grp": m["grp"] if "grp" in m.keys() else "",
        "opp": oe["team_name"] if oe else "?",
        "opp_flag": oe["flag"] if oe else "",
        "is_home": is_home,
        "week": week,
    }


def has_my_match_between(cfg, week_from, week_to):
    """[2026-08 이동] champions_engine.has_my_cl_match_between과 동일."""
    for w in range(week_from, week_to + 1):
        if get_my_match(cfg, w):
            return True
    return False


def sim_my_match_as_ai(cfg, week, p, get_my_match_fn, reason="injury", day=None):
    """[2026-08 이동] champions_engine.sim_my_cl_match_as_ai와 완전히 동일 로직."""
    info = get_my_match_fn(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute(f"SELECT * FROM {cfg.tournament_table} WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute(f"SELECT * FROM {cfg.match_table} WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()
    if m["home_score"] != -1:
        return
    sim_ai_match(cfg, t, m, my_played=False, reason=reason)
    from game_engine import update_player, _calc_manager_rel
    update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False, not_played_penalty=2))


def simulate_my_match(cfg, week, p, get_my_match_fn, day=None):
    """[2026-08 이동] champions_engine.simulate_my_cl_match와 완전히 동일 로직.

    [설계 결정] 출전정지 카운터는 cl_suspension 컬럼 하나를 챔스/유로파/
    컨퍼런스가 공유한다 — 세 대회는 참가팀이 겹치지 않으므로(워터폴 구조상
    한 팀은 항상 셋 중 하나에만 속함) 한 시즌에 한 선수가 두 대회를 동시에
    뛸 일이 없어 별도 컬럼을 만들 실익이 없다."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _check_bench, _roll_red_card,
                             _apply_red_card_dismissal,
                             _roll_card_events,
                             _week_intl_cl_day, _log_highlight, _min_sortkey)
    from competition.champions_engine import _get_field_pos
    info = get_my_match_fn(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute(f"SELECT * FROM {cfg.tournament_table} WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute(f"SELECT * FROM {cfg.match_table} WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    he = entry(cfg, t["id"], m["home_team_id"])
    ae = entry(cfg, t["id"], m["away_team_id"])
    is_home = info["is_home"]

    _susp_field = cfg.suspension_field
    _suspended, _new_susp = _check_suspended(p, field=_susp_field)
    if _suspended:
        update_player(**{_susp_field: _new_susp})
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event")

    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = max(0.0, _my_ovr - _team_ovr)
    # [2026-08 신설, 신민용 리포트: "챔스/유로파/컨퍼런스/슈퍼컵도 리그처럼
    # 벤치가 있어야 한다"] 리그와 동일한 _check_bench를 재사용하되, 비교
    # 기준은 이 대회에서 실제로 뛰는 "이 팀"(_team_ovr) — 이미 위에서
    # 홈/원정에 맞게 뽑아둔 값을 그대로 넘긴다. 출전정지면 벤치 확률
    # 계산 자체가 무의미하므로 건너뛴다(우선순위: 징계 > 벤치).
    _benched = (not _suspended) and _check_bench(p, team_avg_ovr=_team_ovr)
    if _benched:
        add_log("🪑 벤치 대기로 결장", "event")
    _star = 1.0 + max(0.0, (_my_ovr - 60) / 40.0) ** 1.8 * 3.0
    bonus = _gap * 0.30 * _star + max(0.0, _my_ovr - 50) * 0.08
    bonus = _soft_cap(bonus, 30.0)
    from constants import PERSONALITY_EFFECTS
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if "team_win_bonus" in _pe:
        bonus *= (1.0 + _pe["team_win_bonus"])
    if _suspended or _benched:
        bonus = 0.0
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    # [2026-08 신설, 신민용 요청: "챔피언스리그처럼 다른 국가 팀이랑 하면
    # 라인업 평점이 안 뜬다"] champions_engine.simulate_my_cl_match와 완전히
    # 동일한 패턴 — 유로파/컨퍼런스/슈퍼컵이 이 함수 하나를 공유하므로
    # 여기 한 번만 고치면 세 대회 전부 적용된다.
    # [2026-09 버그수정, 신민용 리포트: "그럼 플레이어가 뛰는 16강은 이제
    # 홈 어드벤티지가 생겼다는거지?"] 예전엔 스테이지 상관없이 항상
    # home_adv=0.0이었다 — "결승/3·4위전만 중립"이라는 이번 설계와 달리
    # 16강 같은 초반 라운드까지 전부 홈 어드벤티지가 없던 상태였던 것
    # (내가 직접 뛰는 경기에 한해서만 — AI끼리는 원래도 match_outcome이
    # 라운드별로 정상 적용됐음). neutral_stages에 없는 라운드는 리그와
    # 똑같이 _home_advantage()(1.5~4.5 랜덤)를 쓰게 고친다.
    from game_engine import _home_advantage
    _neutral_stage = m["stage"] in cfg.neutral_stages
    my_position = p.get("position", "")
    engine_stats = None
    engine_plog = None
    player_ratings = None
    # [2026-09 신설] 정규시간 스코어(연장 결과로 덮어쓰지 않는다) / 연장 진입
    # 여부 / 교체 기록. 전술엔진이 예외로 폴백하면 그대로 None/False로 남고
    # 경기 상세는 예전과 똑같이 동작한다.
    hs90 = as90 = None
    went_et = False
    _subs = {"home": [], "away": []}
    try:
        from match_sim.tactical_engine import simulate_my_match as _sim_tactical
        from game_engine import _team_formation
        _fconn = get_conn()
        _c = _fconn.cursor()
        home_formation = _team_formation(_c, m["home_team_id"])
        away_formation = _team_formation(_c, m["away_team_id"])
        _fconn.close()
        sim = _sim_tactical(
            m["home_team_id"], m["away_team_id"], home_formation, away_formation,
            home_boost=(bonus if is_home else 0.0),
            away_boost=(bonus if not is_home else 0.0),
            home_boost_position=(my_position if is_home else None),
            away_boost_position=(my_position if not is_home else None),
            home_adv=(0.0 if _neutral_stage else _home_advantage()),
            extra_time=(m["stage"] != "league"))
        hs, as_ = sim["home_score"], sim["away_score"]
        hs90, as90 = sim.get("home_score_90", hs), sim.get("away_score_90", as_)
        went_et = bool(sim.get("went_extra_time"))
        _subs = {"home": sim.get("home_subs") or [], "away": sim.get("away_subs") or []}
        engine_stats = {"home": sim["home_stats"], "away": sim["away_stats"]}
        engine_plog = sim["possession_log"]
        player_ratings = {"home": sim.get("home_player_ratings") or [],
                          "away": sim.get("away_player_ratings") or []}
        outcome = "draw" if hs == as_ else ("home" if hs > as_ else "away")
    except Exception:
        outcome = match_outcome(h_ovr, a_ovr, neutral=_neutral_stage)
        hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    pso_winner, pso_score = 0, ""
    is_ko = (m["stage"] != "league")
    if outcome == "draw" and is_ko:
        win_home, pso_score = resolve_pso(h_ovr, a_ovr, neutral=_neutral_stage)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]

    if _suspended or _benched:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
        # [2026-08 신설] 벤치는 "결장 사유"가 아니다 — career_window/
        # retire_window가 이미 my_played=0 + absence_reason=NULL을
        # "벤치"로 표시하는 규칙을 쓰고 있으므로(_absence_override 등)
        # 여기서도 그 규칙을 그대로 따른다(출전정지만 사유 문자열을 남김).
        _absence_reason = "suspension" if _suspended else None
        _yellow_cnt = 0
    else:
        _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr, is_big_match=True)
        _absence_reason = None
        _dismissed, _card_reason, _yellow_ev, _yellow_cnt = _roll_card_events(p, _susp_field)
        if _dismissed:
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(
                p, field=_susp_field, reason=_card_reason)
            _absence_reason = _card_reason
        elif _yellow_ev:
            events = list(events) + _yellow_ev
    if not (_suspended or _benched) and "big_match_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["big_match_rating"], 1)))

    # [2026-08 신설, 신민용 요청] champions_engine과 동일한 "나" 슬롯
    # 바꿔치기 — 전술엔진 로스터엔 "나"가 없으므로 포지션이 같은 슬롯을
    # 찾아 방금 계산된 내 실제 기록으로 덮어쓴다.
    # [2026-09 버그수정, 신민용 리포트: "2대0인데 골이 3개 어시가 3개"]
    # 이제 슬롯 치환 직후 "팀 골 합계 == 실제 스코어"를 복원하는 공용
    # 헬퍼(competition_common.merge_my_slot)로 전 대회를 통일했다 — 자세한
    # 원인/불변식은 그 함수 주석 참고.
    # [2026-09 신설, 신민용 리포트: "다른 선수들이 골 넣어도 다 뜨게 해줘"]
    # 내가 관여 안 한 우리 팀 득점을 실제 득점자 이름과 함께 타임라인에
    # 채운다. 반드시 merge_my_slot 이전 — augment_team_goal_events 주석 참고.
    events = augment_team_goal_events(
        p, is_home, hs, as_, goals, assists, not (_suspended or _benched),
        events, engine_plog, player_ratings)
    _side_key, _idx = merge_my_slot(
        player_ratings, is_home, my_position,
        {"id": None, "name": p.get("name") or "나",
         "position": None, "ovr": p.get("ovr", 40),
         "goals": goals, "assists": assists,
         "shots": detail.get("shots", 0),
         "shots_on": detail.get("shots_on", 0),
         "saves": saves, "is_gk": (my_position == "GK"),
         "rating": rating, "is_me": True},
        hs, as_)

    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    day_val = _week_intl_cl_day(m["week"], p)

    conn = get_conn()
    # [2026-09 신설] 기록실용 90분 스코어 — 이 함수는 유로파/컨퍼런스/슈퍼컵
    # 3개 대회(el_matches/ecl_matches/sc_matches)가 공유하므로 여기 한 번
    # 넣으면 셋 다 따라온다. 컬럼/값 정의는 database.py _ET_SCORE_COLS 참고.
    from database import ET_SCORE_SET_SQL, et_score_values
    conn.execute(f"""UPDATE {cfg.match_table} SET home_score=?, away_score=?,
                    {ET_SCORE_SET_SQL},
                    pso_winner=?, pso_score=?,
                    my_played=?, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?,
                    day=?, my_absence_reason=?, my_yellow_cards=?
                    WHERE id=?""",
                 (hs, as_, *et_score_values(hs90, as90, went_et),
                  pso_winner, pso_score,
                  0 if (_suspended or _benched) else 1, _get_field_pos(p),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  my_conceded, day_val, _absence_reason, _yellow_cnt, m["id"]))
    conn.commit()
    conn.close()

    update_player(
        total_shots=p.get("total_shots", 0) + detail["shots"],
        total_shots_on=p.get("total_shots_on", 0) + detail["shots_on"],
        total_key_passes=p.get("total_key_passes", 0) + detail["key_passes"],
        total_dribbles=p.get("total_dribbles", 0) + detail["dribbles"],
        total_blocks=p.get("total_blocks", 0) + detail["blocks"],
    )

    _update_pop(p, goals, assists, rating)
    p2 = get_player()
    ns = min(100, p2["stress"] + 20)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    stage_ko = cfg.stage_ko.get(m["stage"], "")
    my_tid = p.get("current_team_id", 0)
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == my_tid else '패'})"
        rs = "무"

    comp_name = f"{t['name']} {stage_ko}".strip()
    home_disp = f"{he['flag']}{he['team_name']}({he.get('country','?')})"
    away_disp = f"{ae['flag']}{ae['team_name']}({ae.get('country','?')})"
    pso = {"won": pso_winner == my_tid, "score": pso_score} if pso_winner else None
    detail_id = _save_match_detail(
        p, week, comp_name, is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, not (_suspended or _benched), _benched, detail, pso=pso,
        engine_stats=engine_stats, engine_plog=engine_plog, player_ratings=player_ratings,
        match_extra={"score_90": ([hs90, as90] if hs90 is not None else [hs, as_]),
                     "went_extra_time": went_et, "subs": _subs})
    # [2026-08 신설] 이 함수는 유로파/컨퍼런스/슈퍼컵 3개 대회가 공유하므로
    # (europa_engine.py/conference_engine.py/super_cup_engine.py) 헤더
    # 마커의 kind는 고정 리터럴이 아니라 cfg.award_prefix로 구분한다 —
    # ui/log_panel.py가 대회별 상자 색을 고를 때 쓴다.
    _AWARD_PREFIX_TO_KIND = {
        "유로파리그": "europa",
        "컨퍼런스리그": "conference",
        "슈퍼컵": "supercup",
    }
    _kind = _AWARD_PREFIX_TO_KIND.get(cfg.award_prefix, "cup")
    marker = f" [match:{detail_id}:{_kind}]" if detail_id else ""

    add_log("─" * 44, "sep")
    add_log(f"🏆 {comp_name}  {week}주차{marker}", "match")
    add_log(f"   {home_disp} {hs}-{as_} {away_disp}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")
    _timed = sorted([(int(e[0]), e[1]) if isinstance(e, tuple) else
                     (random.randint(1, 90), str(e)) for e in events],
                    key=lambda x: _min_sortkey(x[0]))
    hi = _log_highlight(goals, assists, _timed)
    if hi:
        add_log(f"   {hi}", "match")


def get_my_league_standings(cfg, year, direct_cut, playoff_pool):
    """[2026-08 이동] champions_engine.get_my_cl_league_standings와 동일."""
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return None
    my_tid = p["current_team_id"]
    t = my_tournament(cfg, p, year)
    if not t:
        return None
    rows = get_league_standings(cfg, t["id"])
    if not rows:
        return None
    return {
        "standings": rows, "my_team_id": my_tid,
        "direct_cut": direct_cut,
        "playoff_cut": direct_cut + playoff_pool,
    }


def get_my_matches_for_schedule(cfg, year):
    """[2026-08 이동] champions_engine.get_my_champions_matches와 동일."""
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return []
    t = my_tournament(cfg, p, year)
    if not t or not t.get("my_in"):
        return []
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != p.get("current_team_id", 0):
        return []

    conn = get_conn()
    entries = {r["team_id"]: dict(r) for r in conn.execute(
        f"SELECT team_id, team_name, flag, country FROM {cfg.entry_table} WHERE tournament_id=?",
        (t["id"],)).fetchall()}
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM {cfg.match_table} WHERE tournament_id=? ORDER BY week, slot",
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
            "stage": cfg.stage_ko.get(m["stage"], m["stage"]), "week": m["week"],
            "stage_raw": m["stage"], "grp": m["grp"] if "grp" in m.keys() else "",
        })
    return out


def resync_my_registration(cfg, year, p=None):
    """[2026-09 신설, 신민용 리포트: "여름 비시즌에 이적했을 때 컵/대항전
    일정이 이상해진다"] cup_engine.resync_my_cup_registration의 cfg판 —
    챔스/유로파/컨퍼런스가 국내컵과 똑같은 결함을 갖고 있어 같은 방식으로
    표준화한다.

    ■ 결함
    "이 경기가 내 경기냐"의 기준이 두 군데에서 달랐다.
      · 경기 생성 → 그 시점 소속팀으로 is_my를 박는다
      · 경기 진행/조회 → 대회 단위 my_team_id(대회 개막 시점 팀)로 판정
    챔스는 5주차 개막이고 리그페이즈 대진이 그때 전부 한 번에 만들어지므로,
    여름에 이적하면 그 시즌 내내
      · 옛 소속팀 경기가 계속 "내 경기"로 남고
      · 새 소속팀의 대항전 경기는 영원히 직접 뛸 수 없다
    (국내컵과 달리 _process_one이 is_my 필터 없이 전부 AI로 돌리기 때문에
     대회가 멈추지는 않는다 — 그래서 눈에 덜 띄었을 뿐 같은 결함이다.)

    ■ 하는 일 — cup_engine과 동일한 순서
      대회 등록팀 갱신 → 미완료 경기만 is_my/my_team_id 재계산 →
      이미 치른 경기는 절대 수정하지 않음
    반환: 실제로 바뀐 대회가 있었으면 True.
    """
    from game_engine import get_player
    if p is None:
        p = get_player()
    if not p:
        return False
    my_tid = p.get("current_team_id", 0) or 0

    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"SELECT id, my_in, my_team_id FROM {cfg.tournament_table} WHERE year=?",
        (year,)).fetchall()]
    if not rows:
        conn.close()
        return False
    # 지금 내 팀이 그 대회 참가팀인 경우에만 "내 대회"로 잡는다 — 국내컵과
    # 달리 대륙대항전은 나라가 아니라 팀 단위 출전이라, 참가 여부를 직접
    # 확인해야 한다(이적한 팀이 그 대회에 아예 없으면 my_in=0).
    entered = set()
    if my_tid:
        entered = {r["tournament_id"] for r in conn.execute(
            f"SELECT tournament_id FROM {cfg.entry_table} WHERE team_id=?",
            (my_tid,)).fetchall()}
    changed = False
    c = conn.cursor()
    for t in rows:
        want = my_tid if (my_tid and t["id"] in entered) else 0
        if (t["my_team_id"] or 0) == want:
            continue
        c.execute(f"UPDATE {cfg.tournament_table} SET my_team_id=?, my_in=? WHERE id=?",
                  (want, 1 if want else 0, t["id"]))
        # want=0이면 home_team_id=0인 행이 없으므로 전부 0으로 떨어진다
        # (= 옛 팀 대회는 전부 AI 진행).
        c.execute(f"""UPDATE {cfg.match_table}
                     SET is_my = (CASE WHEN home_team_id=? OR away_team_id=? THEN 1 ELSE 0 END),
                         my_team_id = (CASE WHEN home_team_id=? OR away_team_id=? THEN ? ELSE 0 END)
                     WHERE tournament_id=? AND home_score=-1""",
                  (want, want, want, want, want, t["id"]))
        changed = True
    if changed:
        conn.commit()
    conn.close()
    return changed


def get_my_matches(cfg):
    """[2026-08 이동] champions_engine.get_my_cl_matches와 동일."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"""SELECT m.*, t.year AS t_year, t.name AS comp, t.my_team_id AS t_my_tid
           FROM {cfg.match_table} m
           JOIN {cfg.tournament_table} t ON m.tournament_id = t.id
           WHERE m.is_my = 1 AND m.home_score >= 0
           ORDER BY t.year, m.week""").fetchall()]
    _tids = {r["tournament_id"] for r in rows}
    names = {}
    if _tids:
        _ph = ",".join("?" * len(_tids))
        names = {(r["tournament_id"], r["team_id"]): (r["team_name"], r["flag"], r["country"])
                 for r in conn.execute(
                     f"SELECT tournament_id, team_id, team_name, flag, country "
                     f"FROM {cfg.entry_table} WHERE tournament_id IN ({_ph})",
                     tuple(_tids)).fetchall()}
    conn.close()

    out = []
    for m in rows:
        # [2026-09 수정] 대회 단위 my_team_id는 이제 이적 시점에 갱신되므로
        # (resync_my_registration) 대회 전체를 대표하지 않는다 — 경기 행에
        # 박아둔 "그 경기 당시 내 팀"을 우선 쓴다. 그 컬럼이 생기기 전에
        # 저장된 옛 행(0)만 예전처럼 대회 값으로 폴백한다.
        my_tid = m.get("my_team_id") or m["t_my_tid"]
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
        if opp_country:
            opp_name = f"{opp_name}({opp_country})"

        from constants import day_to_iso_date_str, week_to_iso_date_str
        date_str = (day_to_iso_date_str(m["t_year"], m["day"]) if m.get("day")
                    else week_to_iso_date_str(m["t_year"], m["week"]))

        out.append({
            "year": m["t_year"], "week": m["week"], "date": date_str,
            "position": m["my_position"], "team": my_name, "team_flag": my_flag,
            "comp": m["comp"], "stage": cfg.stage_ko.get(m["stage"], m["stage"]),
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


def save_trophy(cfg, year, team_name, competition, result):
    """champions_engine._save_trophy와 동일(tier=-1로 클럽 국제대회 구분 —
    대회 등급 무관 공용 규칙)."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=?",
        (year, competition)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,-1,?)""", (year, team_name, result, competition))
        conn.commit()
    conn.close()