"""
ai_lifecycle.py — AI 선수 생애 주기 시스템

시즌 종료 시(_end_of_season) 한 번 호출되어 다음을 처리한다:
  1. 나이 +1
  2. 성장(젊은 선수 OVR↑) / 노화(노쇠 선수 OVR↓)
  3. 은퇴(고령) → 신인으로 교체
  4. 이적 시장 (선수들 팀 간 이동 — 활발하게)
  5. 포메이션 변경 (일부 팀, 감독 교체 컨셉)

결과적으로 같은 팀에 오래 있어도 매 시즌 스쿼드/전력/포메가 살아 움직인다.
ai_players.ovr / team_id 가 바뀌므로 마지막에 OVR 캐시를 무효화해야 한다.

설계 메모:
  - 내(my_player)와 무관. 오직 ai_players / teams 만 건드린다.
  - calc_ovr·_gen_ai_stats·_target_ovr 등 database.py의 기존 생성 로직을 재사용.
  - 노화/성장은 '스탯' 자체를 조정하고 ovr를 재계산한다(스탯-ovr 일관성 유지).
"""
import random
import math
import contextlib   # [2026-08 최적화] _indexes_off_for_mass_update용
from database import (get_conn, calc_ovr, ALL_STATS, KEY_STATS_BY_POS,
                      roll_bench_position)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# ── [2026-08 신설, 신민용 요청: "명문팀 강등 스노우볼이 실제로
#    _retire_and_replace 때문인지 시즌별로 확인하고 싶다"] ──────────
# 평소엔 완전히 꺼진 상태(오버헤드 0)이고, DEBUG_PRESTIGE_TRACKING=True로
# 켰을 때만 지정된 팀들의 "은퇴/신인 교체가 실제로 어떤 OVR을 만들어내는지"를
# 시즌마다 한 줄로 콘솔에 남긴다. DB 스키마는 안 건드리고(세션 메모리만
# 사용), game_engine.DEBUG_RELEGATION_TRACKING(강등 순간 4시점 스냅샷)과는
# 별개 도구 — 이건 "매 시즌 은퇴자 교체가 스쿼드를 어느 쪽으로 끌고
# 가는지"를 시계열로 보기 위한 것이라 상호보완적이다.
DEBUG_PRESTIGE_TRACKING = False
DEBUG_PRESTIGE_TEAMS = {
    "토트넘 홋스퍼", "맨체스터 시티", "첼시", "아스널",
    "AC 밀란", "레알 마드리드", "FC 바이에른 뮌헨",
}


# ── 나이 분포/임계값 ──────────────────────────────────────────
_AI_MIN_AGE      = 16
_AI_NEWBIE_AGE   = (16, 21)   # 신인 영입 연령대
# [2026-08 4차 재설계, 신민용 확정(GPT 협업)] constants.
# AGE_OVR_FRACTION_MATURE_AGE(나이별 성장곡선 표가 100%에 도달하는
# 나이, 26)와 맞춰 성장 종료를 25로 늦췄다 — 예전엔 22였는데, 새
# 곡선(16세70%→25세98%→26세100%)은 22~25세 구간에도 완만한 성장분이
# 남아있어야 하기 때문. database._generate_team_players/이 파일의
# 신인 생성(아래 참고) 모두 같은 표(constants.AGE_OVR_FRACTION)를
# 공유하므로 "성장 종료 나이" 하나만 여기서 어긋나지 않게 한다.
_AI_PEAK_START   = 25         # 성장 종료(피크 진입)
_AI_PEAK_END     = 29         # 노화 시작


def _youth_target_scale(target, age):
    """[2026-08 4차 재설계] 16~24세 신인의 target(성인 잠재치)을
    constants.roll_age_ovr_fraction(나이별 명시적 표, 1% 확률 조숙형
    포함)로 낮춘다 — database._generate_team_players(최초 생성)와
    완전히 같은 표를 써서 두 생성 경로가 항상 일치하게 한다. 예전엔
    이 함수 자체가 없어서(또는 낡은 선형보간이라) 신인이 나이와
    무관하게 거의 성인 잠재치 그대로 태어났었다(실측: 명문팀 16세
    OVR89, 17세 OVR98)."""
    from constants import roll_age_ovr_fraction
    return target * roll_age_ovr_fraction(age)


# [2026-08 4차 재설계, 신민용 확정(GPT 협업): "은퇴 확률이 국가/리그
# 등급에만 의존하고 부수(tier)는 전혀 반영하지 않는다" 리포트 및
# 근본 재설계] 예전 표는 country_grade(SS~F)만 보고 tier는 완전히
# 무시했다 — 그래서 잉글랜드 1부(맨시티)와 잉글랜드 7부가 완전히
# 같은 은퇴 확률을 가졌다. 이번엔:
#   1) 국가등급 + "그 나라 안에서의 상대적 부수 깊이"를 합쳐 5단계
#      리그강도 카테고리(top/midhigh/mid/low/bottom)로 매핑
#      (_retire_league_category) — "7부까지 있는 나라는 6~7부,
#      5부까지인 나라는 5부가 그 나라의 최하위"가 되도록 절대 tier가
#      아니라 국가별 최대 tier 대비 비율(depth_ratio)을 쓴다.
#   2) 나이 구간별 은퇴 비율 표를 5개 밴드(24세 이전/25~29/30~34/
#      35~39/40~45)로 새로 설계 — "하부리그=오래 뛴다"가 아니라
#      "하부리그=은퇴 시점의 분산이 크다"(일찍 그만두는 선수도, 40대
#      까지 뛰는 선수도 둘 다 많다)는 형태로, 밴드 총합을 그대로
#      쓰고 밴드 내부만 나이별로 완만하게 배분한다.
#   3) 국가대표/월드컵 출전 경력이 있으면 30세 미만 조기 은퇴 확률에
#      배율(0.5 / 0.2)을 곱해 억제한다 — "월드컵 나갈 정도면 20대
#      후반 은퇴는 이상하다"를 반영. 해저드(조건부 확률) 모델이라
#      일부러 재분배 코드를 따로 두지 않아도, 조기 은퇴가 줄면
#      자연히 그만큼 더 오래 생존해 나이대가 뒤로 밀린다.
_RETIRE_CATEGORIES5 = ("top", "midhigh", "mid", "low", "bottom")

# 밴드별 5카테고리 은퇴 비율(%, 각 열 합계 100) — 신민용 확정표.
_RETIRE_BAND_PCT = {
    "u24":   (1.0, 3.0, 6.0, 12.0, 18.0),
    "25_29": (3.0, 7.0, 12.0, 20.0, 25.0),
    "30_34": (15.0, 20.0, 25.0, 25.0, 25.0),
    "35_39": (55.0, 50.0, 42.0, 30.0, 22.0),
    "40_45": (26.0, 20.0, 15.0, 13.0, 10.0),
}
_RETIRE_BAND_AGES = {
    "u24":   [18, 19, 20, 21, 22, 23, 24],
    "25_29": [25, 26, 27, 28, 29],
    "30_34": [30, 31, 32, 33, 34],
    "35_39": [35, 36, 37, 38, 39],
    "40_45": [40, 41, 42, 43, 44, 45],
}
# 밴드 내부 나이별 상대 가중치(완만한 굴곡만 — 밴드 합계 자체는 위 표를
# 그대로 따름). 40대는 "45세에 몰리는 인위적 벽"을 막기 위해 40세
# 쪽이 더 많고 45세로 갈수록 줄어드는 모양을 준다.
_RETIRE_BAND_SHAPE = {
    "u24":   [1.0, 1.0, 1.0, 1.3, 1.5, 1.8, 2.2],
    "25_29": [1.0, 1.1, 1.2, 1.3, 1.4],
    "30_34": [1.0, 1.05, 1.1, 1.05, 1.0],
    "35_39": [1.1, 1.05, 1.0, 0.95, 0.9],
    "40_45": [1.6, 1.4, 1.2, 1.0, 0.8, 0.6],
}


def _build_retire_pct_table():
    table = {}
    for band, ages in _RETIRE_BAND_AGES.items():
        shape = _RETIRE_BAND_SHAPE[band]
        shape_sum = sum(shape)
        for ci in range(len(_RETIRE_CATEGORIES5)):
            band_total = _RETIRE_BAND_PCT[band][ci]
            for age, w in zip(ages, shape):
                table.setdefault(age, [0.0] * len(_RETIRE_CATEGORIES5))
                table[age][ci] = band_total * (w / shape_sum)
    return {age: tuple(vals) for age, vals in table.items()}


_AI_RETIRE_PROB_PCT = _build_retire_pct_table()


def _retire_league_category(grade: str, tier: int, max_tier: int) -> str:
    """국가등급(SS~F) + 그 나라 안에서의 상대적 부수 깊이를 합쳐 5단계
    카테고리로 매핑. depth_ratio=0이면 그 나라의 1부(최상위), 1이면
    그 나라의 최심부(예: 7부까지 있으면 7부, 5부까지면 5부) — 절대
    tier 숫자가 아니라 나라별 최대 tier 대비 비율이라, "7부제 나라의
    6~7부"와 "5부제 나라의 5부"가 똑같이 '그 나라의 바닥'으로 취급된다."""
    _grade_score = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
    gscore = _grade_score.get(grade, 4)
    if max_tier and max_tier > 1:
        depth_ratio = max(0.0, min(1.0, (tier - 1) / (max_tier - 1)))
    else:
        depth_ratio = 0.0
    combined = gscore - depth_ratio * 7.0
    if combined >= 6.5:
        return "top"
    if combined >= 5.0:
        return "midhigh"
    if combined >= 3.3:
        return "mid"
    if combined >= 1.7:
        return "low"
    return "bottom"


def _build_retire_hazard_table():
    """[2026-08 신설] _AI_RETIRE_PROB_PCT(각 나이에 "은퇴할" 무조건부
    확률, 카테고리별 합계 100%)를 실제 시뮬레이션에 필요한 "그 나이까지
    살아남은 사람 중 이번 해에 은퇴할 조건부 확률"(해저드)로 변환한다 —
    무조건부 확률을 그대로 매 시즌 굴리면(이전까지 이미 은퇴한 사람
    비율을 안 빼면) 실제 은퇴 비율이 표보다 훨씬 낮게 나온다(생존자
    분모가 계속 줄어드는 걸 반영 안 하면). 표는 모듈 로드 시 한 번만
    변환해 캐싱한다."""
    ages = sorted(_AI_RETIRE_PROB_PCT.keys())
    table = {cat: {} for cat in _RETIRE_CATEGORIES5}
    for ci, cat in enumerate(_RETIRE_CATEGORIES5):
        survive_pct = 100.0
        for age in ages:
            p = _AI_RETIRE_PROB_PCT[age][ci]
            hazard = (p / survive_pct) if survive_pct > 0 else 1.0
            table[cat][age] = min(1.0, hazard)
            survive_pct -= p
    return table


_AI_RETIRE_HAZARD_TABLE = _build_retire_hazard_table()

_AI_RETIRE_AGE = 18  # 나이 기반 판정을 시작하는 나이


def _ages_well(player_id: int) -> bool:
    """[2026-08 신설, 신민용 요청: "29세 이후 바로 꺾이는 애들도 있고
    34세까진 그래도 괜찮게 꺾이는 애들이 있게 하고 싶다 — 관리를 잘하면
    99에서 92 정도로만 꺾이고 못하면 원래대로 더 내려가는데, 반반씩
    나와야 한다"] 이 선수가 "관리를 잘하는" 쪽인지 판정한다. 매 시즌
    다시 뽑으면 어느 해엔 관리를 잘하다 다음 해엔 못 하다 왔다갔다
    하게 되어 부자연스러우므로, player_id 기반 결정적 해시로 커리어
    내내 고정된 값을 쓴다 — id는 선수마다 유일하고 사실상 무작위로
    배정되므로 이 해시 결과도 자연히 정확히 반반(짝/홀)으로 갈리고,
    별도 DB 컬럼 없이 항상 같은 값이 재현된다."""
    return ((player_id * 2654435761) & 0xFFFFFFFF) % 2 == 0


def _ai_retirement_probability(age, ovr, position, category="mid", intl_factor=1.0):
    """[2026-08 4차 재설계] 나이 + (국가등급×부수깊이) 카테고리 기반
    "이번 해에 은퇴할 확률". intl_factor는 국가대표/월드컵 경력에 따른
    조기 은퇴 억제 배율(호출부에서 계산, _retire_and_replace 참고) —
    30세 미만 구간에만 곱한다(국제경력은 "조기 은퇴"만 억제할 뿐 은퇴
    자체를 막는 조건이 아니어야 하므로 30세 이상은 원 표 그대로)."""
    if age < 18:
        return 0.0
    if age > 45:
        return 1.0
    p = _AI_RETIRE_HAZARD_TABLE.get(category, _AI_RETIRE_HAZARD_TABLE["mid"]).get(
        age, 1.0 if age >= 45 else 0.0)
    if age < 30:
        p *= intl_factor
    return min(1.0, p)

# 포메이션 후보 (감독 교체 시 랜덤 선택)
_FORMATIONS = ["4-4-2", "4-3-3", "4-2-3-1", "3-5-2", "4-1-4-1", "3-4-3", "5-3-2"]

# ALL_STATS 인덱스 선조회 (반복 list.index 방지)
_STAT_COLS = ",".join(ALL_STATS)
_PHYS_STATS = {"stamina", "speed", "jump", "strength"}
# [2026-08 신설] random.choice는 set을 못 받으므로(인덱싱 불가) 리스트
# 버전도 따로 둔다 — "관리를 잘하는" 선수의 완만한 노화 감소(옛 방식)에 사용.
_AGING_PHYS_STATS = ["stamina", "speed", "jump", "strength"]

if _HAS_NUMPY:
    from database import STAT_IDX, _WEIGHT_IDX_ITEMS, _WEIGHT_SUMS
    _N_STATS = len(ALL_STATS)
    _PHYS_IDX_NP = np.array([STAT_IDX[s] for s in ["stamina", "speed", "jump", "strength"]])
    _DEFAULT_KEY_IDX_NP = np.array([STAT_IDX[s] for s in ALL_STATS[:5]])
    _KEY_IDX_BY_POS_NP = {
        pos: np.array([STAT_IDX[s] for s in keys]) for pos, keys in KEY_STATS_BY_POS.items()
    }
    # 포지션별 OVR 가중치를 (15,) 벡터로 1회 캐싱 (매 시즌 재구성 방지)
    _WEIGHT_VEC_NP = {}
    for _pos, _items in _WEIGHT_IDX_ITEMS.items():
        _wv = np.zeros(_N_STATS)
        for _idx, _wt in _items:
            _wv[_idx] = _wt
        _WEIGHT_VEC_NP[_pos] = _wv


def run_ai_offseason(year, verbose_log=None, progress_cb=None, my_team_id=None):
    """시즌 종료 시 1회 호출. AI 선수 생애주기 전체 처리.
    verbose_log: add_log 함수(있으면 요약 한 줄 남김).
    my_team_id: [2026-08 신설] 넘기면 그 팀이 관여한 이적(방출/영입)을
    verbose_log에 전부 남긴다(_transfer_market으로 그대로 전달).
    [2026-08 신설, 신민용 요청: "시즌 전환 처리 중... 이거 얼마나 남았는지
    표시 안 되나"] progress_cb: callable(done:int, total:int, label:str)
    형태의 콜백(있으면 4단계 각각 시작 시 1회씩 호출) — UI 쪽(center_panel.py
    _AdvanceWorker)이 이걸로 진행률 바를 갱신한다. None이면(헤드리스 실행
    등) 그냥 무시되며 기존 동작과 완전히 동일하다."""
    import time as _time_perf
    _TOTAL_STAGES = 4
    def _report(done, label):
        if progress_cb:
            try:
                progress_cb(done, _TOTAL_STAGES, label)
            except Exception:
                pass   # UI 콜백 실패로 시즌전환 자체가 죽으면 안 됨
    conn = get_conn()
    c = conn.cursor()

    # [2026-07 계측 추가, 신민용 리포트: "AI생애주기 합계 1.93s인데 실제
    # 2.59s — 0.66s 미계측"] 기존 _ta0~_ta4는 ensure_ai_ages/ensure_ai_sub_roles
    # 이후에 시작하고 commit/캐시무효화는 범위 밖이라 이 구간들이 안 보였다.
    # 원인 확정 전이므로 로직은 그대로 두고 타이머만 촘촘히 추가한다.
    _t_start = _time_perf.perf_counter()
    _report(0, "선수 나이·성장 처리 중")
    _ensure_ai_ages(c)               # 구버전 세이브 age 보정
    _ensure_ai_sub_roles(c)          # 구버전 세이브 sub_role 보정
    _t_ensure = _time_perf.perf_counter()
    _ta0 = _t_ensure
    grew, aged = _age_and_progress(c)   # 자체적으로 전용 컬럼 SELECT (포지션 위치접근 최적화라 별도 유지)
    _ta1 = _time_perf.perf_counter()

    # [최적화] _retire_and_replace와 _transfer_market이 각자 따로 부르던
    # "SELECT ... FROM ai_players"(전체 행) 2회를 1회로 통합해 공유한다.
    # 두 함수가 필요로 하는 컬럼(id,team_id,position,age,name,ovr)이 동일
    # 상위집합이라 안전하게 합칠 수 있다 — 로직/결과는 완전히 동일, 풀스캔
    # 횟수만 3회→2회로 감소. (ovr은 _transfer_market의 실력 기반 이적 가중치용)
    shared_ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, "
        "contract_end_year, last_transfer_year FROM ai_players ORDER BY id").fetchall()
    _t_shared = _time_perf.perf_counter()

    # [2026-08 신설, 신민용 요청: "선수 검색에서 OVR이 이적 순간에만
    # 찍히던데, 1년 단위로 그 해 OVR이 다 찍혀있어야 한다"] 방금 _age_
    # and_progress로 이 해의 성장/노화가 전부 반영된 shared_ai_rows를
    # 그대로 재사용해서(추가 쿼리 없음) 전 선수 OVR을 한 번에 아카이브
    # 한다 — 은퇴 예정자도 이 시점엔 아직 ai_players에 남아있으므로
    # "은퇴하는 그 해"까지 정상적으로 기록된다.
    c.executemany(
        "INSERT OR REPLACE INTO hist.ai_player_ovr_history(player_id, year, ovr) VALUES (?,?,?)",
        [(r["id"], year, r["ovr"]) for r in shared_ai_rows])

    # [2026-08 신설, 신민용 리포트: "1년씩 진행하면 기록되는데 10년을
    # 한번에 진행하면 기록이 안 되는 경우가 있다"] 원인 추정: 이 함수
    # 전체가 맨 끝(파일 하단 conn.commit())까지 하나의 트랜잭션이라,
    # 이후 단계(은퇴/이적시장/스쿼드보정 등)에서 어쩌다 예외가 나면
    # _end_of_season의 바깥 try/except가 조용히 삼키고 넘어가면서 —
    # 이미 끝난 나이·성장 갱신과 방금 위에서 쓴 OVR 아카이브까지 전부
    # 커밋 안 된 채로 통째로 날아갔다(여러 해를 한 번에 돌릴수록 그
    # 예외가 한 번이라도 날 확률이 누적되어 높아짐 — 1년씩이면 상대적으로
    # 덜 겪었을 뿐 근본 원인은 같음). 나이·성장·이번 해 OVR 아카이브가
    # 끝난 여기서 한 번 먼저 커밋해, 이후 단계에서 뭔가 실패해도 최소한
    # "나이 +1과 이번 해 OVR 기록"만큼은 항상 살아남게 한다.
    conn.commit()

    # [2026-08 신설, 신민용 리포트: "OVR 기록이 2000/2001/2002년 다 비어있다
    # — 기록이 되는 경우도 있고 아닌 경우도 있다"] 위 아카이브(227~229줄)는
    # 이 시즌 시작 시점의 ai_players만 담고 있어서, 이 시즌 도중 새로
    # 생긴 선수(은퇴자 대신 태어난 16세 신인 — _retire_and_replace / 이적
    # 후 스쿼드 인원이 부족해 보충되는 유망주 — _rebalance_squad_sizes,
    # 둘 다 아래에서 실행됨)는 이 스냅샷에 아예 없다 — 그래서 그 선수들의
    # 데뷔 연도(year)는 영원히 archive가 안 되고 그 다음 시즌부터만
    # 기록되는 들쭉날쭉한 현상이 있었다. 이 시즌이 시작될 때의 id 집합을
    # 기억해뒀다가, 이 함수 맨 끝(모든 신규 생성이 다 끝난 뒤)에서 "그때는
    # 없었는데 지금 생긴" id만 한 번에 추려 그 선수들도 데뷔 연도로
    # archive한다(아래 "신규 선수 데뷔연도 archive" 참고).
    _season_start_ids = {r["id"] for r in shared_ai_rows}

    # [2026-08 버그수정, 신민용 리포트: "은퇴 선수 마지막 팀에서 역할이
    # -로 뜬다"] 은퇴·이적으로 로스터가 흔들리기 "전"에 이번 시즌을
    # 실제로 뛴 상태 그대로를 먼저 스냅샷한다(상세는 _snapshot_season_
    # positions 주석 참고). 맨 아래에서 이번 오프시즌 신규 선수만
    # 한 번 더 보충한다.
    _snapshot_season_positions(c, year, rows=shared_ai_rows)

    _report(1, "은퇴 및 신인 영입 중")
    retired    = _retire_and_replace(c, year, shared_ai_rows)
    _ta2 = _time_perf.perf_counter()
    # [2026-08 버그수정] _retire_and_replace가 이제 은퇴자 행을 UPDATE가
    # 아니라 DELETE+INSERT로 처리하므로(위 함수 docstring 참고), 여기
    # shared_ai_rows(은퇴 처리 전에 떠둔 스냅샷)를 그대로 _transfer_market에
    # 넘기면 방금 삭제된 은퇴자의 옛 id가 섞여 있고 새로 태어난 신인은
    # 아예 빠져 있다 — 이적시장이 이미 사라진 행을 이적시키려 하거나
    # (조용히 무시되긴 하지만) 갓 생긴 신인은 이번 시즌 이적 후보에서
    # 통째로 누락된다. 은퇴 처리 직후 한 번 다시 조회해서 최신 상태로
    # 맞춘다.
    shared_ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, "
        "contract_end_year, last_transfer_year FROM ai_players ORDER BY id").fetchall()

    _report(2, "전세계 이적시장 처리 중")
    moved      = _transfer_market(c, year, shared_ai_rows, verbose_log=verbose_log, my_team_id=my_team_id)
    _ta3 = _time_perf.perf_counter()
    # [2026-08 신설, 신민용 리포트: "이적으로 인한 스쿼드 인원 불균형을
    # 보정하는 장치가 없다 — 짧은 팀엔 10대 선수를 추가하고, 자리 못 구한
    # 애들은 은퇴시키면 되잖아, 다 30대까지 뛰는 것도 아니고 20대에
    # 은퇴하는 애들도 있으니"] _do_one_transfer_cached의 강제 1:1
    # 맞트레이드를 줄인 뒤(위 참고) 생긴 부작용 — 은퇴 교체(_retire_and_
    # replace)는 기존 행을 그대로 재활용(UPDATE)할 뿐 팀별 인원수 자체를
    # 새로 늘리거나 줄이지 않으므로, 이적으로 어느 팀이 계속 순유입/
    # 순유출되면 스쿼드 크기가 영구히 벌어진다. 매 시즌 이적 직후, 인원이
    # 너무 적은 팀엔 10대 유망주를 새로 영입(INSERT)하고, 너무 많은 팀은
    # 자리를 못 구한 선수 중 가장 낮은 OVR부터 조기 은퇴(DELETE, 신인
    # 교체 없음)시켜 규모를 되돌린다.
    topped_up, forced_out = _rebalance_squad_sizes(c, year)
    _ta3b = _time_perf.perf_counter()
    _report(3, "포메이션 갱신 중")
    formations = _shuffle_formations(c)
    # [2026-08 신설, 신민용 요청: "이 시즌에 얘가 어디 포지션을 갔는지가
    # 중요한거야"] 방금 이번 시즌 포메이션이 확정됐으니(바로 위), 그
    # 포메이션대로 로스터를 채웠을 때 각 선수가 맡는 자리를 여기서 같이
    # 스냅샷한다 — "전술변경" 단계 시간에 합산돼 찍히지만(별도 계측 없이
    # 얹음), 실측상 팀당 계산량이 작아(선수 20~30명 vs 슬롯 11개 비교)
    # 시즌 시뮬레이션 전체에 유의미한 지연을 주지 않는다.
    # [2026-08 수정] 본 스냅샷은 이제 위(은퇴 처리 직전)에서 이미 찍었다 —
    # 여기서는 이번 오프시즌에 새로 생긴 선수만 보충한다(이미 기록된
    # 선수의 값은 덮어쓰지 않는다).
    _snapshot_season_positions(c, year, only_missing=True)
    _ta4 = _time_perf.perf_counter()
    _report(4, "시즌 전환 마무리 중")
    # [2026-07 신설, 진단용] game_engine._advance_week의 [PERF] 로그와 짝을
    # 이루는 세부 단계 측정 — "AI생애주기 N초" 중 실제로 어느 서브단계
    # (성장/은퇴·세대교체/이적시장/전술변경)가 무거운지 콘솔에서 바로 보인다.
    print(f"[PERF]     ai_offseason 세부: ensure(age/subrole) {_t_ensure-_t_start:.2f}s | "
          f"성장/노화({'numpy' if _HAS_NUMPY else 'PURE-PYTHON!'}) {_ta1-_ta0:.2f}s | "
          f"shared_ai_rows조회 {_t_shared-_ta1:.2f}s | "
          f"은퇴·세대교체 {_ta2-_t_shared:.2f}s | 이적시장 {_ta3-_ta2:.2f}s | "
          f"스쿼드 인원 보정 {_ta3b-_ta3:.2f}s | 전술변경 {_ta4-_ta3b:.2f}s")
    # [2026-08 신설, 신민용 리포트: "시즌 지날수록 은퇴·세대교체가 느려지는데
    # 처리 대상(은퇴자 수) 자체가 느는 건지 건당 비용이 느는 건지 구분이
    # 안 된다"] 위 [PERF] 줄은 이미 "은퇴·세대교체 X.XXs"를 찍고 있었지만
    # 그 시간 동안 실제로 몇 명을 처리했는지가 같이 안 찍혀서, 로그만
    # 보고는 "대상 증가에 따른 정상적인 비용 증가"인지 "건당 비용 자체가
    # 늘어난 버그"인지 구분할 수 없었다. retired/moved는 이미 계산돼 있는
    # 값이라 여기 한 줄만 추가하면 시즌별로 나란히 비교할 수 있다 —
    # 로직/결과는 전혀 안 건드리고 로그만 추가.
    print(f"[PERF-LIFECYCLE] {year}년: 은퇴/세대교체 {retired}명 · 이적 {moved}건 · "
          f"소요시간 {_ta2-_t_shared:.3f}s"
          + (f" ({(_ta2-_t_shared)/retired*1000:.2f}ms/명)" if retired else ""))

    # [2026-08 신설, 위 _season_start_ids 주석 참고 — "신규 선수 데뷔연도
    # archive"] 이 시즌 동안 새로 생긴 선수 전부(은퇴 대체 신인 +
    # 스쿼드 인원 보정으로 영입된 유망주, 출처 불문)를 한 번에 archive한다
    # — 개별 생성 지점마다 따로 챙기는 대신 여기 한 곳에서 "시즌 시작
    # 때 없었는데 지금 있는 id"만 걸러내므로, 나중에 새 생성 경로가
    # 추가돼도 이 로직을 다시 손 볼 필요가 없다.
    _final_ids_rows = c.execute("SELECT id, ovr FROM ai_players").fetchall()
    _new_this_season = [r for r in _final_ids_rows if r["id"] not in _season_start_ids]
    if _new_this_season:
        c.executemany(
            "INSERT OR REPLACE INTO hist.ai_player_ovr_history(player_id, year, ovr) VALUES (?,?,?)",
            [(r["id"], year, r["ovr"]) for r in _new_this_season])

    _t_commit0 = _time_perf.perf_counter()
    conn.commit()
    conn.close()
    _t_commit1 = _time_perf.perf_counter()

    # OVR/소속이 일괄 변경됨 → 엔진 캐시 무효화
    try:
        from game_engine import _invalidate_team_ovr_cache
        _invalidate_team_ovr_cache()
    except Exception:
        pass
    _t_cache1 = _time_perf.perf_counter()
    print(f"[PERF-AI]  commit={_t_commit1-_t_commit0:.3f}s | "
          f"cache_invalidate={_t_cache1-_t_commit1:.3f}s")

    if verbose_log:
        _rebalance_txt = f" · 스쿼드 보정(영입 {topped_up}명/조기은퇴 {forced_out}명)" if (topped_up or forced_out) else ""
        verbose_log(
            f"🔄 이적시장 마감: 이적 {moved}건 · 은퇴/세대교체 {retired}명 · "
            f"전술 변경 {formations}팀{_rebalance_txt}", "news", year, 52)

    return {"grew": grew, "aged": aged, "retired": retired,
            "moved": moved, "formations": formations,
            "squad_topped_up": topped_up, "squad_forced_out": forced_out}


def run_ai_mid_season_transfer(year, verbose_log=None, my_team_id=None):
    """[2026-08 신설, 상반기/하반기 이적 기록 분리 기능, 신민용 요청:
    "상황에 따라 중간에도 AI 선수들 이적이 가능하긴 하나 이때는 0~2명
    정도만 이적하게 해줘"] 하반기 시작 주차(SECOND_HALF_START, 겨울
    이적시장 마감 직후)에 game_engine.py._advance_week가 딱 한 번
    호출한다 — run_ai_offseason(연 1회, 시즌 완전히 끝난 뒤 은퇴·세대
    교체까지 포함하는 무거운 전체 생애주기 처리)과 달리, 이건 이적
    시장만 아주 작은 규모(volume_scale=0.15 — 리그 팀 수 기준 오프시즌의
    약 1/10 수준, 20팀 리그면 기대값 3~4건 안팎이라 대부분 팀은 0명,
    일부만 1~2명)로 딱 한 번 더 돌리는 가벼운 호출이다. 은퇴/신인 생성/
    노화·성장/포메이션 변경은 여기서 처리하지 않는다(전부 오프시즌
    전용) — 순수하게 "시즌 도중 이적 창구"만 재현한다.

    반환: 이번에 옮겨간 인원 수(moved)."""
    conn = get_conn()
    c = conn.cursor()
    ai_rows = c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality, "
        "contract_end_year, last_transfer_year FROM ai_players ORDER BY id").fetchall()
    moved = _transfer_market(c, year, ai_rows, verbose_log=verbose_log, my_team_id=my_team_id,
                              volume_scale=0.15, is_mid_season=True)
    conn.commit()
    conn.close()
    # [2026-08 신설] 이적으로 team_id가 바뀐 선수가 있으므로, 포메이션
    # 화면 캐시도 오프시즌 처리와 동일하게 무효화해야 한다(안 하면 그
    # 시즌이 끝날 때까지 새로 이적한 선수가 옛 팀 소속으로 계속 보임).
    try:
        import ui.formation_widget as _fw
        _fw._ovr_cache_invalidated = True
    except Exception:
        pass
    if verbose_log and moved:
        verbose_log(f"❄ 겨울 이적시장: 이적 {moved}건", "event", year, 32)
    return moved


# ─────────────────────────────────────────────
# 0. 나이 보정 (구버전 세이브: age=0/NULL → 랜덤 부여)
# ─────────────────────────────────────────────
def _ensure_ai_ages(c):
    """[2026-07 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"] 이 보정은
    '구버전 세이브에 남아있던 age=0/NULL'을 고치기 위한 1회성 마이그레이션인데,
    run_ai_offseason이 매 시즌 호출될 때마다 ai_players 10만+ 행을 무조건
    풀스캔하고 있었다(정상 세이브라면 매번 0건 매치라 완전히 낭비 — 실측
    103,323행 스캔에 age 0건/sub_role 0건). age는 이후 _age_and_progress가
    매 시즌 전원에게 항상 값을 채우므로, 한 번 깨끗하다고 확인되면 그
    세이브에선 다시는 더러워질 수 없다 — meta 플래그로 "이 세이브는 이미
    깨끗함"을 기록해두고, 다음 시즌부터는 쿼리 자체를 건너뛴다."""
    try:
        row = c.execute("SELECT value FROM meta WHERE key='ai_ages_clean_v1'").fetchone()
    except Exception:
        row = None
    if row:
        return
    rows = c.execute("SELECT id FROM ai_players WHERE age IS NULL OR age=0").fetchall()
    if rows:
        # [최적화] executemany로 한 번에 처리
        updates = [(int(round(random.triangular(16, 34, 25))), r["id"]) for r in rows]
        c.executemany("UPDATE ai_players SET age=? WHERE id=?", updates)
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ai_ages_clean_v1','1')")


def _ensure_ai_sub_roles(c):
    """[세부역할 2026-07] sub_role 컬럼이 새로 생겨서 기존 세이브엔 빈 값('')
    인 AI 선수가 있다 — 포지션에 맞는 SUB_ROLES 중 하나를 무작위로 채운다.
    (신규 시딩 때는 _generate_team_players가 이미 채우므로 여기선 빈 것만
    골라 보정한다.)

    [2026-07 최적화] _ensure_ai_ages와 동일한 이유로 meta 플래그 가드 추가 —
    한 번 깨끗해지면 다시 더러워질 수 없으므로 매 시즌 풀스캔할 필요가 없다."""
    try:
        row = c.execute("SELECT value FROM meta WHERE key='ai_sub_roles_clean_v1'").fetchone()
    except Exception:
        row = None
    if row:
        return
    from constants import SUB_ROLES
    rows = c.execute(
        "SELECT id, position FROM ai_players WHERE sub_role IS NULL OR sub_role=''").fetchall()
    if rows:
        updates = [(random.choice(SUB_ROLES.get(r["position"], ["기본"])), r["id"]) for r in rows]
        c.executemany("UPDATE ai_players SET sub_role=? WHERE id=?", updates)
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ai_sub_roles_clean_v1','1')")


# ─────────────────────────────────────────────
# 1+2. 나이 +1, 성장/노화
# ─────────────────────────────────────────────
def _age_and_progress(c):
    """모든 AI 선수 나이 +1 후, 연령대별로 스탯 성장/노화 → ovr 재계산.
    [2026-07 개선] numpy가 있으면 전체를 벡터 연산으로 처리(_age_and_progress_np),
    없으면 기존 순수 파이썬 배치 버전(_age_and_progress_py)으로 자동 폴백한다.
    실측(5.9만 명 기준, 52→1 시즌전환의 최대 병목이던 지점): 순수 파이썬 약
    0.35~1.2초(환경별 차이) → numpy 벡터화 약 0.15~0.2초. 팀 수/선수 수가
    늘어날수록(향후 20팀+ 확장 등) 격차가 더 벌어진다 — 파이썬 루프는 선수 수에
    선형 비례해 늘지만, 벡터화 버전은 대부분의 시간이 상수 오버헤드라 훨씬
    완만하게 늘어난다.
    [2026-08 재현성 수정, 신민용 리포트: "같은 시드로 재현해도 성장/노화
    결과가 달라진다"] 원래 이 numpy Generator를 시드 없이(np.random.
    default_rng()) 만들었는데, 이러면 매 실행마다 OS 엔트로피로 새로
    초기화돼 파이썬 random 모듈을 아무리 고정 시드로 돌려도 이 함수가
    뽑는 난수만은 매번 달라졌다 — 그 차이가 선수 OVR → 이적/은퇴 → 팀
    전력 → 리그 결과로 계속 번져나가 몇 시즌 뒤엔 완전히 다른 세계선이
    됐다(200시즌 A/B 밸런스 테스트가 PYTHONHASHSEED=0을 고정해도 완전히
    재현되지 않던 원인). 이제 이미 시드가 고정된 파이썬 random 모듈에서
    시드값을 하나 뽑아 numpy Generator를 초기화한다 — random 모듈 자체의
    시드(예: random.seed(12345))가 같으면 이 함수가 매 시즌 뽑는 난수도
    항상 똑같다. 시드 생성 자체는 사실상 공짜라 numpy 벡터화로 얻은
    속도 이득은 전혀 줄지 않는다. [주의] 이 수정 전/후로 "같은 시드"가
    만들어내는 실제 성장 결과값 자체는 달라진다(수정 전엔 애초에 미정의
    였으므로 이건 "다른 값이 됨"이 아니라 "처음으로 값이 고정됨"에
    가깝다) — 기존에 저장된 세이브의 과거 시즌 기록에는 영향 없음(그
    시점에 이미 계산·저장된 값을 다시 계산하지 않음), 이후 새로 진행하는
    시즌의 성장 난수 값만 이제 시드에 따라 고정된다."""
    from database import STAT_IDX, calc_ovr_from_list, OVR_RANGES
    from constants import CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, get_country_league_grade, get_ovr_range

    # [2026-08 계측 추가, 신민용 리포트: "numpy 쓰는데도 0.71s, 예상보다
    # 느린데?"] numpy 벡터화 버전이 실제로 도는데도 docstring이 적어둔
    # 0.15~0.2s 범위가 아니라 순수 파이썬 범위(0.35~1.2s)만큼 걸렸다 —
    # numpy 연산 자체가 아니라 그 앞뒤(team_cap 조회, 5.9만 행 fetch,
    # DB 쓰기)가 무거운 건 아닌지 구간을 쪼개서 확인한다.
    import time as _time_ap
    _ap_t0 = _time_ap.perf_counter()

    # ── team_id → 성장기 스탯 상한 사전 조회 (선수마다 매번 JOIN 방지) ──
    # 등급별 OVR_RANGES 상단에 대륙보정 + 나라별 미세조정까지 반영해서,
    # 초기 생성 때 쓰는 보정치와 항상 같은 기준으로 성장 상한을 잡는다.
    team_cap: dict = {}
    for r in c.execute(
            """SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname,
                      cn.continent AS continent
               FROM teams t JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall():
        grade = get_country_league_grade(r["cname"])
        # [2026-08] tier1은 COUNTRY_LEAGUE_OVR_OVERRIDE 등록국이면 그 값을 우선.
        rng = get_ovr_range(grade, r["tier"] or 1, r["cname"])
        top = rng[1] if rng else 43
        # [버그수정 2026-07, 신민용 리포트: "이적시장 처리 중 오류: 'float'
        # object cannot be interpreted as an integer"] COUNTRY_OVR_ADJ에
        # 대한민국(1.5)·세르비아(-1.5)·우루과이/콜롬비아/에콰도르(-0.5)처럼
        # 소수점 조정치가 섞여 있어서, 이 값이 그대로 bonus에 더해지면
        # bonus 자체가 float이 되고, 그게 OVR 상한 계산에 계속 실려
        # 내려가다가 결국 아래(신인 교체 로직)의 random.randint(mid, hi)에
        # float가 그대로 들어가 터졌다. 정수 등급 보정치라는 원래 의도대로
        # 여기서 반올림해 int로 확정한다.
        bonus = round(CONTINENT_OVR_BONUS.get(r["continent"], 0) + COUNTRY_OVR_ADJ.get(r["cname"], 0))
        if grade == "SS":
            bonus = min(bonus, 0)
        team_cap[r["tid"]] = min(99, top + bonus + 3)
    _ap_t1 = _time_ap.perf_counter()

    # JOIN에 안 잡힌 팀(league_id/country_id 연결 누락 등)의 폴백 상한.
    _ORPHAN_CAP_FALLBACK = 46

    rows = c.connection.cursor()
    rows.row_factory = None  # 위치 접근만 쓰므로 Row 래핑 생략 (5.9만 행 fetch 오버헤드 절감)
    rows = rows.execute(
        "SELECT id, position, age, team_id, " + _STAT_COLS + " FROM ai_players").fetchall()
    _ap_t2 = _time_ap.perf_counter()
    if not rows:
        return 0, 0

    if _HAS_NUMPY:
        _result = _age_and_progress_np(c, rows, team_cap, _ORPHAN_CAP_FALLBACK)
    else:
        _result = _age_and_progress_py(c, rows, team_cap, _ORPHAN_CAP_FALLBACK)
    _ap_t3 = _time_ap.perf_counter()
    print(f"[PERF-AGE] _age_and_progress({'numpy' if _HAS_NUMPY else 'python'}) 세부: "
          f"team_cap조회 {_ap_t1-_ap_t0:.3f}s | ai_players fetch({len(rows)}행) {_ap_t2-_ap_t1:.3f}s | "
          f"계산+DB쓰기 {_ap_t3-_ap_t2:.3f}s")
    return _result


# [2026-08 최적화] 전 선수(26만 행) 나이/스탯/OVR 일괄 UPDATE 전용 —
# ai_players에는 ovr이 들어간 인덱스가 2개(idx_aiplayers_nat_pos_ovr,
# idx_aiplayers_ovr_id) 있어서, 한 행을 고칠 때마다 그 인덱스 B-트리에서
# 옛 항목을 지우고 새 항목을 끼워 넣는 일이 행마다 2번씩 일어난다.
# 26만 행을 한꺼번에 갱신할 때는 인덱스를 잠깐 내렸다가 끝나고 한 번에
# 다시 만드는 쪽이 훨씬 싸다(정렬 한 번으로 끝나므로).
#   · 갱신하는 컬럼(age/스탯/ovr)을 실제로 참조하는 인덱스만 내린다 —
#     team_id 인덱스(idx_aiplayers_team)는 이 UPDATE와 무관한데다, 이게
#     없으면 같은 시즌전환 안의 이적시장 팀 조회가 125초까지 폭발한다
#     (실측 확인). 절대 건드리지 않는다.
#   · 중간에 무슨 일이 생겨도 인덱스가 사라진 채로 남지 않도록 finally로
#     반드시 복구한다.
#   · 인덱스는 순수 성능용이라 이 처리로 게임 데이터·결과는 전혀 달라지지 않는다.
_MASS_UPDATE_COLS = ("ovr", "age") + tuple(ALL_STATS)


@contextlib.contextmanager
def _indexes_off_for_mass_update(c):
    dropped = []
    try:
        for r in c.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='ai_players' "
                "AND sql IS NOT NULL").fetchall():
            _name, _sql = r[0], r[1]
            _cols = _sql[_sql.find("("):].lower()
            if any(col in _cols for col in _MASS_UPDATE_COLS):
                dropped.append((_name, _sql))
        for _name, _ in dropped:
            c.execute(f"DROP INDEX IF EXISTS {_name}")
    except Exception:
        dropped = []   # 조회/삭제 실패 시엔 그냥 예전처럼 인덱스를 둔 채로 진행
    try:
        yield
    finally:
        for _name, _sql in dropped:
            try:
                c.execute(_sql)
            except Exception:
                pass   # 인덱스는 성능용이라 재생성에 실패해도 게임은 정상 동작


def _age_and_progress_np(c, rows, team_cap, orphan_fallback):
    """벡터화 버전 — 선수 5.9만 명(+향후 확장분)을 파이썬 for문 없이 numpy로 처리.
    로직(확률/증감폭/키스탯 가중치)은 순수 파이썬 버전과 동일하게 유지했다."""
    from database import _WEIGHT_SUMS
    # [2026-08 계측 추가, 신민용 리포트: "numpy 쓰는데도 예상보다 느린데?"]
    # "계산+DB쓰기" 0.49s가 numpy 벡터 연산 자체인지 executemany(현재
    # 10만+ 행)인지 갈라본다.
    import time as _time_npf
    _npf_t0 = _time_npf.perf_counter()

    N = len(rows)
    pids = [r[0] for r in rows]
    pids_arr_full = np.array(pids, dtype=np.int64)  # [2026-08 신설] _ages_well 벡터화용 — 아래서 재사용
    pos_list = [r[1] for r in rows]
    pos_arr = np.array(pos_list)
    ages = np.array([(r[2] or 20) for r in rows], dtype=np.int64)
    tids = [r[3] for r in rows]

    # None/0 스탯은 기존과 동일하게 50으로 보정 (구버전 세이브 방어)
    # [최적화] 중첩 리스트(list-of-tuples)를 np.array로 바로 변환하는 것보다
    # 1차원으로 펼친 뒤 reshape하는 편이 실측상 더 빠름(타입 추론 오버헤드 감소).
    _flat = [v for r in rows for v in r[4:]]
    raw = np.array(_flat, dtype=np.float64).reshape(N, _N_STATS)
    vals_arr = np.where(np.isnan(raw) | (raw == 0), 50.0, raw).astype(np.int64)

    # [2026-07 최적화, 신민용 리포트: "일정 진행이 갈수록 오래 걸린다" — 실측
    # 결과 이 함수가 "벡터화 버전"이라면서 여기 한 곳만 순수 파이썬 for문으로
    # 10만+ 회를 도는 게 남아있었다(dict.get()을 선수 수만큼 반복). team_cap은
    # 팀 수(9천여 개)만큼만 있으니, searchsorted로 완전히 벡터화한다 —
    # dict 방식 O(N) 파이썬 루프 → O(N log M) numpy 연산(M=팀 수)으로 대체.
    tids_arr = np.array(tids, dtype=np.int64)
    if team_cap:
        _cap_keys = np.array(list(team_cap.keys()), dtype=np.int64)
        _cap_vals = np.array(list(team_cap.values()), dtype=np.int64)
        _order = np.argsort(_cap_keys)
        _cap_keys_sorted = _cap_keys[_order]
        _cap_vals_sorted = _cap_vals[_order]
        _idx = np.searchsorted(_cap_keys_sorted, tids_arr)
        _idx = np.clip(_idx, 0, len(_cap_keys_sorted) - 1)
        _found = _cap_keys_sorted[_idx] == tids_arr
        cap_by_row = np.where(_found, _cap_vals_sorted[_idx], orphan_fallback).astype(np.int64)
        _orphan_team_ids = set(tids_arr[~_found].tolist())
    else:
        cap_by_row = np.full(N, orphan_fallback, dtype=np.int64)
        _orphan_team_ids = set(tids_arr.tolist())

    new_age = ages + 1
    growth_mask = new_age <= _AI_PEAK_START
    peak_mask = (new_age > _AI_PEAK_START) & (new_age <= _AI_PEAK_END)
    aging_mask = new_age > _AI_PEAK_END

    # [2026-08 재현성 수정] 파이썬 random 모듈(이미 게임 마스터 시드로
    # 고정돼 있음)에서 시드값을 하나 뽑아 numpy Generator를 초기화 —
    # _age_and_progress 함수 docstring 참고. random 모듈 시드가 같으면
    # 이 시즌의 성장/노화 난수도 항상 동일해진다.
    rng = np.random.default_rng(random.getrandbits(64))
    # [2026-08 버그수정, 전체 최적화 감사 중 발견 — 신민용이 예전에
    # "PYTHONHASHSEED=0을 고정해도 완전히 재현되지 않는다"고 했던 원인]
    # 아래 세 군데의 `for pos in unique_positions:` 루프는 순회 순서대로
    # numpy 난수를 뽑아 쓴다. 그런데 파이썬 set의 순회 순서는 원소(문자열)
    # 해시에 좌우되고, 그 해시는 프로세스마다 무작위로 바뀐다(해시 무작위화).
    # 즉 같은 시드로 돌려도 실행할 때마다 포지션 처리 순서가 달라져
    # 성장/노화 결과가 통째로 달라지고 있었다 — 시즌 결과 재현이 원천적으로
    # 불가능했던 지점. sorted()로 순서를 못박아 같은 시드면 항상 같은 결과가
    # 나오게 한다. 다루는 포지션 집합·처리 내용은 전혀 바뀌지 않고
    # (전부 처리하는 건 동일) 순서만 고정되며, 애초에 이 순서에 의미가
    # 부여된 로직도 없다(포지션별로 독립적으로 처리).
    unique_positions = sorted(set(pos_list))

    # ── 성장기: 키스탯 70% / 전체스탯 30%, 1~3회, +1~3, 팀 상한까지 ──
    for pos in unique_positions:
        idxs = np.where(growth_mask & (pos_arr == pos))[0]
        Ng = len(idxs)
        if Ng == 0:
            continue
        key_idx = _KEY_IDX_BY_POS_NP.get(pos, _DEFAULT_KEY_IDX_NP)
        n_up = rng.integers(1, 4, size=Ng)  # 1~3
        for rnd in range(3):
            active = n_up > rnd
            if not active.any():
                continue
            act_idx = idxs[active]
            m = len(act_idx)
            use_key = rng.random(m) < 0.7
            chosen = np.where(
                use_key,
                key_idx[rng.integers(0, len(key_idx), size=m)],
                rng.integers(0, _N_STATS, size=m))
            inc = rng.integers(1, 4, size=m)  # 1~3
            cur = vals_arr[act_idx, chosen]
            cap = cap_by_row[act_idx]
            vals_arr[act_idx, chosen] = np.minimum(cap, cur + inc)

    # ── 피크기: 30% 확률로 전체스탯 중 1개 ±1 (승격/강등과 무관한 절대
    #    상한) ──
    # [2026-08 수정, 신민용 요청: "승격한 팀이 그거에 맞춰 팀을 개편하는
    # 식으로 가면 좋겠다 — 20대 초반은 재능등급 오르게 OVR을 올릴 수
    # 있지만, 전성기(29세)는 그렇게 오르는 시스템이 아니어도 된다"]
    # 예전엔 여기도 cap_by_row(팀의 현재 등급/tier에서 나온 상한 — 팀이
    # 방금 승격하면 이 상한도 즉시 올라감)를 썼다 — 그러면 이미 성장이
    # 끝난(24세 이하 성장기가 아닌) 25~29세 선수도 소속팀이 승격하는
    # 순간 곧바로 OVR이 슬금슬금 오를 여지가 생겼다. 성장기(위, 24세
    # 이하)는 팀 상한을 그대로 쓰게 놔둬 어린 선수는 상위 리그 이적/
    # 소속팀 승격으로 실제로 더 클 수 있게 하고(신민용이 명시적으로
    # 허용), 이 피크기 구간만 절대 상한(99)으로 바꿔서 승격/강등과 완전히
    # 무관하게 만든다 — 승격팀이 강해지는 건 이제 이적시장에서 실제로
    # 더 좋은 선수를 사 오는 쪽(카테고리별 이적 물량 확대)으로만 반영된다.
    idxs = np.where(peak_mask)[0]
    if len(idxs):
        active = rng.random(len(idxs)) < 0.3
        act_idx = idxs[active]
        m = len(act_idx)
        if m:
            chosen = rng.integers(0, _N_STATS, size=m)
            coin = rng.integers(0, 3, size=m)          # random.choice([-1,1,1])과 동일 분포
            delta = np.where(coin == 0, -1, 1)
            cur = vals_arr[act_idx, chosen]
            vals_arr[act_idx, chosen] = np.clip(cur + delta, 15, 99)

    # ── 노화기: 키스탯 70% / 전체스탯 30%(관리 못하는 절반) 또는
    #    신체스탯 65% / 전체스탯 30%(관리 잘하는 절반), 나이 비례 하락 ──
    # [2026-08 버그수정, 신민용 리포트: "35세 OVR94가 37세에 93밖에 안
    # 떨어졌다 — 노화가 OVR에 제대로 반영 안 되는 거 아니냐"] 예전엔
    # 포지션과 무관하게 신체스탯(65%)/전체스탯(35%) 중에서만 깎았는데,
    # OVR은 포지션별 가중평균(calc_ovr_from_list)이라 그 포지션에서
    # 가중치가 낮은 스탯만 계속 깎이면 실제로는 계속 노화 중인데도 OVR엔
    # 거의 안 잡히는 왜곡이 생겼다(실측: 35세 OVR94→37세 OVR93).
    # [2026-08 확장, 신민용 요청: "관리를 잘하면 완만하게, 못하면
    # 원래대로 가파르게 꺾이는 선수가 반반씩 있으면 좋겠다"] _ages_well
    # (player_id 기반, 커리어 내내 고정) 로 정확히 반반 갈라서, 관리를
    # 잘하는 쪽은 옛 방식(신체스탯 위주, OVR엔 완만하게만 반영)을 그대로
    # 쓰고 못하는 쪽만 키스탯 위주(성장기와 대칭)로 확실히 깎는다.
    well_arr = ((pids_arr_full.astype(np.int64) * 2654435761) & 0xFFFFFFFF) % 2 == 0
    for pos in unique_positions:
        for well in (True, False):
            idxs = np.where(aging_mask & (pos_arr == pos) & (well_arr == well))[0]
            if len(idxs) == 0:
                continue
            key_idx = _KEY_IDX_BY_POS_NP.get(pos, _DEFAULT_KEY_IDX_NP)
            decline_n = 2 + (new_age[idxs] - _AI_PEAK_END) // 2
            max_rounds = int(decline_n.max())
            _pool = _PHYS_IDX_NP if well else key_idx
            _pool_prob = 0.65 if well else 0.7
            for rnd in range(max_rounds):
                active = decline_n > rnd
                if not active.any():
                    continue
                act_idx = idxs[active]
                m = len(act_idx)
                use_pool = rng.random(m) < _pool_prob
                chosen = np.where(
                    use_pool,
                    _pool[rng.integers(0, len(_pool), size=m)],
                    rng.integers(0, _N_STATS, size=m))
                dec = rng.integers(1, 4, size=m)
                cur = vals_arr[act_idx, chosen]
                vals_arr[act_idx, chosen] = np.maximum(15, cur - dec)

    # ── OVR 재계산 (포지션별 가중치 벡터와 행렬곱, 5.9만 명 순회 없이 일괄 처리) ──
    ovr_out = np.empty(N, dtype=np.int64)
    for pos in unique_positions:
        mask = pos_arr == pos
        wv = _WEIGHT_VEC_NP.get(pos, _WEIGHT_VEC_NP["CM"])
        wsum = _WEIGHT_SUMS.get(pos, _WEIGHT_SUMS["CM"])
        total = vals_arr[mask] @ wv / wsum
        ovr_out[mask] = np.clip(np.round(total), 1, 100).astype(np.int64)

    # [최적화] (age, *stats, ovr, id) 튜플을 파이썬 루프로 만드는 대신
    # column_stack으로 한 번에 이어붙여 tolist() — sqlite3.executemany는
    # 튜플뿐 아니라 리스트 행도 그대로 받아준다. 5.9만 회 언패킹 루프 제거.
    updates = np.column_stack([new_age, vals_arr, ovr_out, pids_arr_full]).tolist()
    _npf_t1 = _time_npf.perf_counter()

    set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
    with _indexes_off_for_mass_update(c):
        c.executemany(
            f"UPDATE ai_players SET age=?, {set_clause}, ovr=? WHERE id=?",
            updates)
    _npf_t2 = _time_npf.perf_counter()
    print(f"[PERF-AGE-NP]  numpy계산 {_npf_t1-_npf_t0:.3f}s | "
          f"executemany({len(updates)}건) {_npf_t2-_npf_t1:.3f}s")

    if _orphan_team_ids:
        import sys as _sys
        print(f"[⚠ ai_lifecycle 경고] team_cap 매칭 실패 팀 {len(_orphan_team_ids)}개 "
              f"(league_id/country_id 연결 확인 필요, 폴백 상한 {orphan_fallback} 적용됨): "
              f"{sorted(_orphan_team_ids)[:20]}{'...' if len(_orphan_team_ids) > 20 else ''}",
              file=_sys.stderr)

    return int(growth_mask.sum()), int(aging_mask.sum())


def _age_and_progress_py(c, rows, team_cap, orphan_fallback):
    """순수 파이썬 폴백 버전 (numpy 미설치 환경용). 로직은 numpy 버전과 동일."""
    from database import STAT_IDX, calc_ovr_from_list
    grew = aged = 0
    updates = []  # (age, s1, s2, ..., ovr, id) 튜플 목록
    _default_keys = ALL_STATS[:5]
    _orphan_team_ids = set()

    _randint = random.randint
    _choice = random.choice
    _random = random.random

    for r in rows:
        pid = r[0]
        pos = r[1]
        new_age = (r[2] or 20) + 1
        tid = r[3]
        if tid in team_cap:
            _cap = team_cap[tid]
        else:
            _cap = orphan_fallback
            _orphan_team_ids.add(tid)
        vals = [v or 50 for v in r[4:]]
        keys = KEY_STATS_BY_POS.get(pos, _default_keys)

        if new_age <= _AI_PEAK_START:
            n_up = _randint(1, 3)
            for _ in range(n_up):
                s = _choice(keys if _random() < 0.7 else ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = min(_cap, vals[i] + _randint(1, 3))
            grew += 1
        elif new_age <= _AI_PEAK_END:
            # [2026-08 수정, 신민용 요청: "승격/강등과 무관하게, 전성기
            # (29세)는 팀 상한을 따라 오르는 시스템이 아니어도 된다"]
            # 위 numpy 버전과 동일 — 성장기(_cap, 팀 승격 시 즉시 상승)와
            # 달리 피크기는 절대 상한(99)만 쓴다.
            if _random() < 0.3:
                s = _choice(ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = min(99, max(15, vals[i] + _choice([-1, 1, 1])))
        else:
            # [2026-08 확장, 신민용 요청: "관리를 잘하면 완만하게, 못하면
            # 원래대로 가파르게 꺾이는 선수가 반반씩 있으면 좋겠다"]
            # _ages_well(고정된 개인 성향, 반반)로 갈라서 — 관리를 잘하는
            # 쪽은 예전 방식(신체스탯 위주 65%, OVR엔 완만하게만 반영)을
            # 그대로 쓰고, 못하는 쪽만 위에서 고친 키스탯 위주(70%) 방식을
            # 쓴다. 두 방식 다 이미 실측 검증된 것들이라 새로 검증할 필요
            # 없이 그대로 재사용.
            decline_n = 2 + (new_age - _AI_PEAK_END) // 2
            _well = _ages_well(pid)
            for _ in range(decline_n):
                if _well:
                    s = _choice(_AGING_PHYS_STATS) if _random() < 0.65 else _choice(ALL_STATS)
                else:
                    s = _choice(keys) if _random() < 0.7 else _choice(ALL_STATS)
                i = STAT_IDX[s]
                vals[i] = max(15, vals[i] - _randint(1, 3))
            aged += 1

        new_ovr = calc_ovr_from_list(pos, vals)
        updates.append((new_age, *vals, new_ovr, pid))

    set_clause = ", ".join(f"{s}=?" for s in ALL_STATS)
    with _indexes_off_for_mass_update(c):
        c.executemany(
            f"UPDATE ai_players SET age=?, {set_clause}, ovr=? WHERE id=?",
            updates)

    if _orphan_team_ids:
        import sys as _sys
        print(f"[⚠ ai_lifecycle 경고] team_cap 매칭 실패 팀 {len(_orphan_team_ids)}개 "
              f"(league_id/country_id 연결 확인 필요, 폴백 상한 {orphan_fallback} 적용됨): "
              f"{sorted(_orphan_team_ids)[:20]}{'...' if len(_orphan_team_ids) > 20 else ''}",
              file=_sys.stderr)

    return grew, aged


# ─────────────────────────────────────────────
# 3. 은퇴 + 신인 교체
# ─────────────────────────────────────────────
def _retire_and_replace(c, year, ai_rows=None):
    """고령 선수 은퇴 → 같은 팀·같은 포지션에 신인 영입.
    [버그수정] 신인 목표 OVR을 team_avg 기반 → 리그 등급/tier OVR_RANGES 기반으로 변경.
    기존: team_avg가 낮으면 낮은 신인이 들어와 리그 전체 OVR이 해마다 하락하는 버그.
    수정: OVR_RANGES[grade][tier] 범위 하단~중간값을 신인 목표로 사용 → 리그 OVR 유지.
    [최적화] 팀 info 선조회 + 이름풀 캐시로 은퇴자마다 DB 왕복 제거.
    ai_rows: 호출부(run_ai_offseason)가 이미 조회해둔 ai_players 행
      (id,team_id,position,age,name)을 넘겨받아 재사용 — 이 함수와
      _transfer_market이 각자 같은 조건의 SELECT를 또 날리던 것을 없애
      전체 스캔 횟수를 줄인다(로직/결과는 완전히 동일). None이면(단독 호출
      등 하위호환) 기존처럼 이 함수가 직접 조회한다."""
    from constants import (OVR_RANGES, CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, SUB_ROLES,
                           get_country_league_grade, get_ovr_range, COUNTRY_LEAGUE_OVR_OVERRIDE)
    from database import _pick_nationality, get_foreign_quota_range
    retired = 0

    # 팀 → 리그등급/tier/보정치 선조회 (은퇴자마다 JOIN 방지)
    # [2026-07 확장] 국적 재배정(_pick_nationality)에 필요한 국가명/대륙도
    # 같이 캐싱한다 — 신인이 은퇴자의 옛 국적을 그대로 물려받던 버그 수정용.
    # [2026-07 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"] 아래
    # 명문팀 가산 로직이 은퇴자마다 "SELECT name FROM teams WHERE id=?"를
    # 따로 날리고 있었다 — 이 함수 전체가 "은퇴자마다 DB 왕복 제거"를
    # 원칙으로 세워놨는데 그 원칙을 깨는 N+1 쿼리였다(은퇴자가 많을수록,
    # 세이브가 오래될수록 이 함수가 계속 느려지던 원인 중 하나 — 실측
    # 로그에서 "은퇴·세대교체" 단계가 시즌이 지날수록 조금씩 늘어나는
    # 추세를 보였음). 팀 이름도 이 아래 team_info 캐시 SELECT 한 번에
    # 같이 담아서, 이후 루프에서는 dict 조회만 하도록 고친다.
    from data.prestige_clubs import is_prestige, prestige_level, PRESTIGE_LEVEL_OVR_BONUS
    from constants import (CLUB_STRENGTH_OVR_BONUS_K, CLUB_STRENGTH_OVR_BONUS_MIN,
                           CLUB_STRENGTH_OVR_BONUS_MAX, CLUB_STRENGTH_OVR_BONUS_MODE)
    # [2026-08 신설, 은퇴 시스템 tier 연동] 국가별 "가장 깊은 부수"를
    # 미리 조회해둔다 — 7부까지 있는 나라는 6~7부, 5부까지인 나라는
    # 5부가 그 나라의 "최하위"가 되도록, tier를 국가마다 다른 절대
    # 깊이가 아니라 "그 나라 안에서의 상대적 깊이(depth_ratio)"로 써야
    # 하기 때문(_retire_league_category 참고).
    country_max_tier = {r["cid"]: r["mt"] for r in c.execute(
        "SELECT country_id AS cid, MAX(tier) AS mt FROM leagues GROUP BY country_id").fetchall()}

    team_info = {}  # {team_id: (grade, tier, bonus, cname, continent, tname, club_strength, retire_cat)}
    for r in c.execute(
            """SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                      t.club_strength AS club_strength,
                      cn.id AS cid, cn.name AS cname, cn.continent AS continent
               FROM teams t
               JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall():
        grade = get_country_league_grade(r["cname"])
        # [버그수정 2026-07, 신민용 리포트: "이적시장 처리 중 오류: 'float'
        # object cannot be interpreted as an integer"] COUNTRY_OVR_ADJ의
        # 소수점 조정치(대한민국 1.5, 세르비아 -1.5, 우루과이/콜롬비아/
        # 에콰도르 -0.5)가 그대로 더해지면 bonus가 float이 되고, 그게
        # lo/hi/mid를 전부 float으로 오염시켜 아래 random.randint(mid, hi)
        # 에서 바로 이 예외가 났다. 정수로 반올림해서 확정한다.
        bonus = round(CONTINENT_OVR_BONUS.get(r["continent"], 0) + COUNTRY_OVR_ADJ.get(r["cname"], 0))
        if grade == "SS":
            bonus = min(bonus, 0)
        _tier = r["tier"] or 1
        _max_tier = country_max_tier.get(r["cid"], _tier)
        _retire_cat = _retire_league_category(grade, _tier, _max_tier)
        team_info[r["tid"]] = (grade, _tier, bonus, r["cname"], r["continent"], r["tname"],
                                r["club_strength"] or 0.0, _retire_cat)

    # [2026-08 신설, 신민용 확정(GPT 협업): "월드컵 등 국제대회에 출전할
    # 정도면 29세 이전 은퇴는 이상하잖아"] 국가대표(어느 대회든 intl_squad
    # 명단에 한 번이라도 포함) / 월드컵 출전(kind='world' 대회의 명단
    # 포함) 여부를 한 번에 조회해둔다 — 30세 미만 조기 은퇴 확률에만
    # 배율로 적용(30세 이상은 원 표 그대로, 국제경력이 은퇴 자체를 막는
    # 조건이 아니라 "조기 은퇴"만 억제하는 보정이어야 하므로).
    _natteam_ids = {r["player_id"] for r in c.execute(
        "SELECT DISTINCT player_id FROM intl_squad").fetchall()}
    _wc_ids = {r["player_id"] for r in c.execute(
        """SELECT DISTINCT s.player_id FROM intl_squad s
           JOIN intl_tournaments t ON t.id = s.tournament_id
           WHERE t.kind='world'""").fetchall()}

    # [최적화] 이름풀 전체 1회 로드 (은퇴자마다 ORDER BY RANDOM() 방지)
    name_cache = _build_name_cache(c)
    # 팀→국가 캐시 초기화 (오프시즌 시작 시 리셋)
    _team_country_cache.clear()

    # [2026-08 신설, 진단용] DEBUG_PRESTIGE_TRACKING이 켜져있으면 추적
    # 대상 팀들의 "은퇴 전 평균 OVR"을 미리 스냅샷해둔다(비교 기준선).
    _dbg = {}
    if DEBUG_PRESTIGE_TRACKING:
        _dbg_name_to_tid = {info[5]: tid for tid, info in team_info.items()
                             if info[5] in DEBUG_PRESTIGE_TEAMS}
        for tname, tid in _dbg_name_to_tid.items():
            row = c.execute("SELECT AVG(ovr) v, COUNT(*) n FROM ai_players WHERE team_id=?",
                             (tid,)).fetchone()
            cs_row = c.execute("SELECT club_strength FROM teams WHERE id=?", (tid,)).fetchone()
            _dbg[tid] = {
                "name": tname, "tier": team_info[tid][1],
                "before_avg": round(row["v"], 1) if row and row["v"] else 0.0,
                "squad_n": row["n"] if row else 0,
                "club_strength": round((cs_row["club_strength"] or 0.0) if cs_row else 0.0, 2),
                "retired": 0, "new_ovrs": [],
            }

    # [최적화] 이름 중복방지 캐시 + 은퇴 대상 목록을 별도 두 번 풀스캔하던 것을
    #   컬럼을 합쳐 1회 SELECT로 통합했었고(5.9만 행 전체스캔 2회 → 1회),
    #   이제 그 SELECT 자체도 호출부에서 넘겨받은 ai_rows로 재사용해
    #   _transfer_market과의 중복 스캔까지 없앤다(3회 → 2회).
    _src_rows = ai_rows if ai_rows is not None else c.execute(
        "SELECT id, team_id, position, age, name, ovr, nationality FROM ai_players").fetchall()
    team_used_names: dict = {}
    rows = []
    # [2026-07 신설] 팀별 현재 외국인 수 카운터 — 신인 국적 재배정 시
    # 쿼터(FOREIGN_QUOTA_CAP)를 그대로 지키기 위해 필요.
    foreign_count_by_team: dict = {}
    for r in _src_rows:
        team_used_names.setdefault(r["team_id"], set()).add(r["name"])
        rows.append(r)
        tinfo = team_info.get(r["team_id"])
        if tinfo and r["nationality"] and r["nationality"] != tinfo[3]:
            foreign_count_by_team[r["team_id"]] = foreign_count_by_team.get(r["team_id"], 0) + 1
    retire_deletes = []  # 은퇴자 DELETE용
    retire_archives = []  # [2026-08 신설] 은퇴자 ai_players_retired 아카이브용
    new_rows = []         # 신인 INSERT용

    for r in rows:
        age = r["age"] or 25
        if age < _AI_RETIRE_AGE:
            continue
        _tinfo_r = team_info.get(r["team_id"])
        _cat_r = _tinfo_r[7] if _tinfo_r else "mid"
        _intl_factor = 0.2 if r["id"] in _wc_ids else (0.5 if r["id"] in _natteam_ids else 1.0)
        p_retire = _ai_retirement_probability(age, r["ovr"], r["position"],
                                               category=_cat_r, intl_factor=_intl_factor)
        if p_retire <= 0 or random.random() >= p_retire:
            continue

        # [버그수정] 신인 목표 OVR: 리그 등급/tier OVR_RANGES 하단~중간 범위
        #  + 대륙/나라 보정. [조정] 예전엔 중간값+5까지 허용해서 신인이 데뷔부터
        #  거의 에이스급으로 들어왔다(A등급 기준 82~91). 하단~중간(82~86)으로
        #  좁혀서, 실제로 몇 시즌 성장해야 에이스 근처에 도달하도록 한다.
        grade, tier, _bonus, cname, continent, _tname, _club_strength, _cat_unused = team_info.get(
            r["team_id"], ("D", 1, 0, "", "유럽", "", 0.0, "mid"))
        # [2026-08] COUNTRY_LEAGUE_OVR_OVERRIDE 등록국이면 최우선 사용 —
        # 이미 그 나라 실측에 맞춘 값이라 대륙/국가 보정(_bonus)은 중복
        # 적용하지 않는다(초기 시딩의 _tier_top_ovr(country=...)와 동일 원칙).
        # [2026-08 버그수정, 신민용 리포트: "K1 OVR을 내렸더니 K2랑 겹친다"]
        # 예전엔 이 판정이 tier==1일 때만 걸려서, tier2 이하 신인은
        # get_ovr_range()가 이미 델타-캐스케이드한 값 위에 _bonus까지 또
        # 더해지는 이중보정이 있었다 — get_ovr_range 자체가 이제 모든
        # tier에서 오버라이드를 반영하므로, 여기 판정도 tier 무관하게
        # 국가 등록 여부만 본다.
        _is_override = cname in COUNTRY_LEAGUE_OVR_OVERRIDE
        ovr_rng = get_ovr_range(grade, tier, cname)
        _plvl = 0  # [2026-08 신설] 아래 분기 중 하나에서만 채워지므로 기본값 선정의
        if ovr_rng:
            lo, hi = ovr_rng
            if not _is_override:
                lo, hi = lo + _bonus, hi + _bonus
            mid = (lo + hi) // 2
            # [2026-07 버그수정, 신민용 리포트: "명문팀이 계속 강등당한다"]
            # 예전엔 항상 '하단~중간'에서 뽑고 명문팀이면 그 위에 그냥
            # +2~5만 더했다 — 그런데 게임 초반 시딩(_generate_all_ai_players
            # → weighted_team_order)은 "명문팀은 강한 슬롯을 뽑을 확률이
            # 훨씬 높되(PRESTIGE_WEIGHT=6.0) 100%는 아니다"라는 철학이었다.
            # 신인 교체가 이 철학을 안 따르고 매번 '하단~중간 + 소폭 보정'만
            # 하다 보니, 명문팀 선수단이 은퇴로 교체될수록(대략 10~15년 후
            # 전체 세대교체) 원래 시딩 때 받았던 우위가 사라지고 리그 평균
            # 수준으로 수렴해버렸다 — 그래서 시간이 지날수록 명문팀이 점점
            # 강등권에 가까워지는 정확히 그 증상이었다. 이제 명문팀은
            # weighted_team_order와 같은 확률(PRESTIGE_WEIGHT 기반)로
            # '중간~상단'에서 뽑을 확률이 훨씬 높게 하되, 완전히 배제하진
            # 않는다(가끔은 평범한 신인도 나와야 "명문팀도 가끔 훅 간다"가
            # 재현됨).
            # [2026-07 확률 보정] 처음엔 random()**(1/PRESTIGE_WEIGHT)>=0.5 조건을
            # 썼는데, 실측 시뮬레이션해보니 98.4% 확률로 상단이 나와서 원래
            # 설계 문서(prestige_clubs.py 상단 주석)가 말하는 "대략 10~20%
            # 안팎만 하위권"이라는 의도보다 훨씬 강했다(거의 100% 고정 강세와
            # 다를 게 없어짐). 의도한 비율(상단 85%, 하위 15%)을 직접
            # 상수로 명시한다.
            _PRESTIGE_UPPER_PROB = 0.85
            _is_prestige_team = is_prestige(cname, tier, _tname)
            _use_upper = _is_prestige_team and (random.random() < _PRESTIGE_UPPER_PROB)
            if _use_upper:
                target = random.randint(mid, hi)
            else:
                target = random.randint(lo, mid)
            # [2026-08 신설] prestige_level(3/2/1) 가산 보너스 — 85/15 확률
            # 편향과 역할을 분리한다: 85/15는 "명문팀이 좋은 세대교체를 할
            # 가능성"을, 이 가산은 "3급/2급/1급 사이의 지속적인 질적 차이"를
            # 담당한다(PRESTIGE_LEVEL_OVR_BONUS 정의부 주석 참고). 강등된
            # 명문팀도 현재 tier 기준 범위(lo~hi) 위에 이 보너스만 얹힐 뿐,
            # 원래 tier로 강제 복귀되지는 않는다 — 강등의 의미는 유지된다.
            _plvl = prestige_level(cname, _tname)
            if _plvl:
                target += PRESTIGE_LEVEL_OVR_BONUS.get(_plvl, 0)
        else:
            # [버그수정 2026-07] 그 등급에 이 tier가 정의 안 돼 있으면(부수가
            # 늘었는데 표를 못 채운 경우) 고정 30~45가 아니라, 그 등급 안에서
            # 정의된 가장 깊은 부수 기준 단계별 감쇠 값을 쓴다 — database._tier_top_ovr
            # 과 동일한 감쇠 방식이라, 등급표 밖 tier라도 "한 단계 위보다는
            # 확실히 낮고, SS/S 같은 상위 등급이 갑자기 완전히 다른 등급처럼
            # 뚝 떨어지지 않는" 자연스러운 값이 된다.
            grade_ranges = OVR_RANGES.get(grade, {})
            if grade_ranges:
                deepest_tier = max(grade_ranges)
                deepest_lo, deepest_hi = grade_ranges[deepest_tier]
                STEP = 8
                extra = (tier - deepest_tier) * STEP
                lo = max(15, deepest_lo - extra) + _bonus
                hi = max(lo + 1, deepest_hi - extra) + _bonus
                target = random.randint(lo, (lo + hi) // 2)
            else:
                target = random.randint(30, 45)
                hi = target  # [방어] 이 극단적 폴백 경로엔 hi가 없어 아래 명문팀 가산에서 참조 에러 방지

        # [2026-07 수정] 명문팀 보정은 이제 위 target 산출 시점(중간~상단 확률
        # 편향)에서 이미 반영되므로, 여기서 별도로 다시 가산하지 않는다 —
        # 예전엔 여기서 +2~5를 또 더했는데, 그러면 이중 보정이 된다.
        # [2026-07 최적화] 팀 이름은 위 team_info 캐시에서 바로 꺼낸다
        # (원래 여기서 은퇴자마다 "SELECT name FROM teams WHERE id=?"를
        # 따로 날렸던 N+1 쿼리였음 — 함수 상단 주석 참고).

        # [2026-08 신설, 신민용 확정: "club_strength가 경기력엔 반영되는데
        # 정작 선수단엔 안 이어진다"] 위 PRESTIGE_LEVEL_OVR_BONUS(정적
        # 명문 리스트 전용, 강등돼도 안 바뀌는 고정값)와 별개로, "그 세이브
        # 안에서 실제로 지금 강한/약한 팀인지"를 나타내는 club_strength를
        # 신인 목표 OVR에도 반영한다. 명문 리스트에 없는 팀도 실적으로
        # club_strength를 쌓으면 똑같이 이 보정을 받는다(원래 설계 철학
        # "명문이라서가 아니라 강해서 보호"와 일치). 1차 실험이라 기존
        # PRESTIGE_LEVEL_OVR_BONUS는 그대로 두고 이 보정을 추가로 얹는다
        # — 어느 쪽 효과인지 나중에 구분해서 조정할 수 있게.
        _cs_bonus = max(CLUB_STRENGTH_OVR_BONUS_MIN,
                         min(CLUB_STRENGTH_OVR_BONUS_MAX, _club_strength * CLUB_STRENGTH_OVR_BONUS_K))
        if CLUB_STRENGTH_OVR_BONUS_MODE == "positive_only":
            _cs_bonus = max(0.0, _cs_bonus)
        elif CLUB_STRENGTH_OVR_BONUS_MODE == "off":
            _cs_bonus = 0.0
        target += _cs_bonus

        # [2026-08 버그수정, 위 _youth_target_scale 주석 참고] 나이를
        # 먼저 뽑아서, target(성인 잠재치)을 그 나이에 맞게 낮춘 뒤
        # 스탯을 생성한다 — 예전엔 new_age를 스탯 생성 이후에 뽑아서
        # 전혀 반영이 안 되고 있었다.
        new_age = random.randint(*_AI_NEWBIE_AGE)
        _scaled_target = _youth_target_scale(target, new_age)
        # [2026-08 신설, 신민용 리포트: "OVR81따리가 레알 마드리드나
        # 바르셀로나에 있을 수 있냐"] database._generate_team_players와
        # 동일한 명문팀 바닥(prestige_level>=2)을 신인 교체 경로에도
        # 적용 — 진짜 명문팀(레알/바르사급)은 유스 신인이라도 그 등급/
        # 부수 하한 대비 너무 크게 못 내려가게 한다.
        # [2026-08 재설계 — database._generate_team_players와 동일한
        # Prestige×리그등급 표로 교체(신민용 확정, GPT 협업). 산하팀 보유
        # 여부는 여기 섞지 않는다 — 별도 시스템 몫.
        if ovr_rng:
            _prestige_base = {3: 1, 2: 2, 1: 3}.get(_plvl, 4)
            _grade_adj = {"SS": 0, "S": 0, "A": 0, "B": 1, "C": 1,
                         "D": 2, "E": 2, "F": 3}.get(grade, 2)
            _young_floor_off = _prestige_base + _grade_adj
            _scaled_target = max(_scaled_target, ovr_rng[0] - _young_floor_off)
        stats = _gen_stats(r["position"], _scaled_target)
        new_ovr = calc_ovr(r["position"], stats)
        # [2026-08 신설, 진단용] 추적 대상 팀이면 이번에 생성된 신인 OVR을 기록.
        if DEBUG_PRESTIGE_TRACKING and r["team_id"] in _dbg:
            _dbg[r["team_id"]]["retired"] += 1
            _dbg[r["team_id"]]["new_ovrs"].append(new_ovr)
            _dbg[r["team_id"]].setdefault("cs_bonuses", []).append(round(_cs_bonus, 2))
        # [세부역할 2026-07] 새 신인은 은퇴자의 예전 세부역할을 물려받지 않고
        # 그 포지션에 맞는 SUB_ROLES 중 하나를 새로 무작위 배정한다.
        new_sub_role = random.choice(SUB_ROLES.get(r["position"], ["기본"]))
        # [2026-07 신설, 신민용 지적: "은퇴하면 새 선수 들어오는데 국적도
        # 새로 뽑아야지, 안 그러면 은퇴자 국적을 그대로 물려받는다"] 은퇴자가
        # 외국인이었으면 먼저 카운터에서 빼고, 새 국적을 다시 뽑는다.
        tid = r["team_id"]
        old_nat = r["nationality"] if "nationality" in r.keys() else ""
        cur_foreign = foreign_count_by_team.get(tid, 0)
        if old_nat and old_nat != cname:
            cur_foreign = max(0, cur_foreign - 1)
        _q_lo, quota = get_foreign_quota_range(cname, continent)
        new_nat, cur_foreign = _pick_nationality(cname, continent, grade, r["position"],
                                                  False, cur_foreign, quota)
        foreign_count_by_team[tid] = cur_foreign
        # 팀 내 중복 방지: used_in_team에 팀 현재 이름 set 전달
        used = team_used_names.setdefault(r["team_id"], set())
        name = _random_name(c, r["team_id"], name_cache, used_in_team=used)
        # [2026-08 버그수정, 신민용 리포트: "AI5가 은퇴하면 AI5가 다시
        # 생기는 게 아니라 AI11이 나타나야 하고, AI5는 그 은퇴한 선수로
        # 남아있어야 한다"] 예전엔 은퇴 교체를 "같은 행을 UPDATE"로
        # 처리했다 — ai_player_code()가 ai_players.id를 그대로 코드로
        # 쓰는데, 같은 id를 재활용하면 "AI0005"라는 코드가 은퇴 전엔
        # 베테랑이었다가 은퇴 후엔 완전히 다른 신인을 가리키게 되어,
        # 코드가 특정 선수의 영구적인 정체성이 아니라 그냥 "로스터 자리
        # 번호"가 되어버렸다. id는 AUTOINCREMENT라 삭제해도 그 번호가
        # 재사용되지 않으므로, 이제 은퇴자 행은 그대로 DELETE하고 신인은
        # INSERT로 새 id를 받는다 — 은퇴한 선수의 코드는 그 선수에게
        # 영구히 남고, 신인은 한 번도 안 쓰인 새 코드를 받는다. team_id
        # (팀은 그대로), position(같은 자리 채움)만 은퇴자와 동일하게
        # 넣고, 나머지는 전부 새로 생성된 값.
        retire_deletes.append((r["id"],))
        # [2026-08 신설, 신민용 요청: "은퇴하면... 얘네도 차후 검색할 수
        # 있어야 해"] DELETE 전에 은퇴 직전 스냅샷(마지막 OVR/나이/포지션/
        # 국적/마지막 소속팀)을 같은 id로 아카이브 테이블에 남겨서,
        # ai_player_code(id)가 은퇴 후에도 계속 이 선수를 가리키게 한다.
        retire_archives.append((r["id"], r["name"], r["position"], r["ovr"], age,
                                 r["nationality"], r["team_id"],
                                 team_info.get(r["team_id"], (None,) * 6)[5], year))
        new_rows.append((
            r["team_id"], name, r["position"],
            *[stats[s] for s in ALL_STATS], new_ovr, new_age, new_sub_role, new_nat,
            year + random.randint(3, 5), 0, year))
        retired += 1

    if new_rows:
        if retire_archives:
            c.executemany(
                """INSERT OR REPLACE INTO ai_players_retired
                   (id, name, position, ovr, age, nationality, last_team_id,
                    last_team_name, retirement_year)
                   VALUES(?,?,?,?,?,?,?,?,?)""", retire_archives)
        if retire_deletes:
            c.executemany("DELETE FROM ai_players WHERE id=?", retire_deletes)
        c.executemany(
            f"""INSERT INTO ai_players
                (team_id,name,position,{_STAT_COLS},ovr,age,sub_role,nationality,
                 contract_end_year,last_transfer_year,created_year)
                VALUES(?,?,?,{','.join('?' for _ in ALL_STATS)},?,?,?,?,?,?,?)""",
            new_rows)

    # [2026-08 신설, 진단용] 추적 대상 팀들의 이번 시즌 은퇴/신인 교체 요약을
    # 한 줄씩 찍는다 — "강등 → 낮은 OVR 신인 → 추가 강등" 루프가 실제로
    # 발생하는지 시즌별로 눈으로 확인하기 위함.
    if DEBUG_PRESTIGE_TRACKING:
        for tid, d in _dbg.items():
            n_new = len(d["new_ovrs"])
            new_avg = round(sum(d["new_ovrs"]) / n_new, 1) if n_new else None
            cs_bonuses = d.get("cs_bonuses", [])
            cs_bonus_avg = round(sum(cs_bonuses) / len(cs_bonuses), 2) if cs_bonuses else None
            print(f"[PRESTIGE-DEBUG] {year}년 {d['name']} (현재 {d['tier']}부): "
                  f"교체전 스쿼드평균 {d['before_avg']}({d['squad_n']}명) | "
                  f"은퇴/교체 {d['retired']}명 | 신인평균OVR {new_avg} | "
                  f"club_strength {d['club_strength']:+.2f} | "
                  f"신인OVR에 얹힌 cs보정 {cs_bonus_avg}")

    return retired


# ─────────────────────────────────────────────
# 4. 이적 시장 (활발하게)
# ─────────────────────────────────────────────
# [2026-08 신설, 15-7-3, 신민용+GPT 검토: "국제 이동(5%) 확률이 출신국
# 등급/선수 OVR과 무관하게 완전히 균일하다 — D급 리그 평균OVR58인데
# 72면 엄청난 아웃라이어인데, S급 평균88에 92는 그렇게 특별하지 않다.
# 그러니 'OVR 절대값'이 아니라 '자기 시장 대비 상대적 위치'로 유출
# 확률을 올려야 한다"] 국제 이동 분기 확률(기본 5%)에 곱하는 승수.
# 두 요인을 곱한다:
#   1) outlier_mult — 이 팀 스쿼드 최고 OVR이 팀 평균보다 얼마나 튀는가.
#      (국가 전체 평균 대신 소속팀 평균을 쓴다 — team_avg가 이미 캐싱돼
#      있어 추가 집계 없이 재사용 가능하고, 약체 리그일수록 팀 평균 자체가
#      국가 평균에 가깝다.)
#   2) market_mult — 그 팀이 속한 국가등급이 얼마나 약한가(LEAGUE_GRADE_RANK
#      1=F~8=SS, 낮을수록 약함). SS/S는 사실상 보정 없음(신민용: "거긴
#      유출이 아니라 선수의 선택 문제") — F급에 가까울수록 배율이 커진다.
# 결과는 0.05(원래 고정값)에 곱해질 배수이고, 최종 국제이동 비중은
# min(0.35, 0.05*승수)로 캡을 씌워 폭주를 막는다(아래 호출부 참고).
# 아웃라이어가 없고(gap<=0) 등급도 SS/S면 승수는 정확히 1.0 — 즉 기존
# 균일 5% 동작과 100% 동일하게 유지된다(회귀 없음 보장).
_OUTLIER_GAP_DIVISOR = 10.0
_OUTLIER_COMPONENT_CAP = 3.0
_MARKET_RANK_STEP = 0.15


def _outlier_intl_multiplier(best_ovr, team_avg_ovr, grade_rank) -> float:
    """[2026-08 재조정] 최초 버전(outlier_mult × market_mult을 각각
    독립적으로 곱함)은 gap=0(아웃라이어 없음)이어도 약체 등급이면 기본
    5%가 최대 9%대까지 올라가는 부작용이 있었다(신민용 원칙 위반: "약체
    등급이라고 평범한 선수까지 유출 확률이 오르면 안 된다 — 아웃라이어일
    때만"). market 보정을 outlier_component에 곱하는 형태로 바꿔서,
    gap=0이면 등급과 무관하게 정확히 1.0(=5% 그대로)이 나오게 한다 —
    "시장이 약할수록 아웃라이어가 더 잘 빠져나간다"이지 "약체 시장 평균
    선수도 잘 빠져나간다"가 아니기 때문."""
    gap = max(0.0, (best_ovr or 0) - (team_avg_ovr or 0))
    outlier_component = min(_OUTLIER_COMPONENT_CAP, gap / _OUTLIER_GAP_DIVISOR)
    rank = grade_rank if grade_rank is not None else 8   # 등급 정보 없으면 보정 없음(SS 취급)
    market_scale = 1.0 + max(0, 8 - rank) * _MARKET_RANK_STEP
    return 1.0 + outlier_component * market_scale


def _transfer_market(c, year, ai_rows=None, verbose_log=None, my_team_id=None,
                      volume_scale=1.0, is_mid_season=False):
    """선수들이 팀 간 이동. 같은 리그 내 + 국내 다른 tier + 국제 이동.
    [최적화] ORDER BY RANDOM() 제거 → 팀별 선수 목록 선조회 후 Python shuffle.
    이적마다 DB 왕복 2회(RANDOM 쿼리) → 0회로 감소.
    ai_rows: _retire_and_replace와 공유하는 ai_players 선조회 결과
      (id,team_id,position,age,name,ovr,contract_end_year,last_transfer_year)
      — None이면 기존처럼 직접 조회.

    [2026-07 v2 신설] year 파라미터 추가 — 계약 잔여기간(길수록 이적
    확률↓) 반영과 "방금 이적한 선수는 최소 1시즌은 유지"를 위해 필요.

    [2026-07 v3 신설, 신민용+GPT 검토: "K리그는 계속 K리그 안에서만 돈다 —
    승강 시스템이랑 이적시장이 따로 논다 + 10년 지나도 세계가 닫혀있는
    느낌"] 이적 종류를 3가지로 분리한다: 87% 같은 리그(기존), 8% 국내
    다른 tier(승강 인접), 5% 국제 이동(동일 등급 ±1등급, tier1끼리만 —
    하위 tier의 "등급"은 안 매겨져 있어서 국제 이동은 tier1로 한정한다).
    스타 선수 보호·계약 반영·최소 잔류기간은 이 확장된 후보군에도 그대로
    적용된다(mover 선택 로직은 공통이고 destination 후보군만 넓어지는
    구조라 자연스럽게 유지됨).

    [2026-07 v3 신설] verbose_log — 표시용 이적료(저장 없음). 이번 호출에서
    일어난 이적 중 (OVR85 이상 또는 이적료 최고액) 조건을 만족하는 1건만
    골라 로그에 남긴다. 자금 이동은 없음 — 순수 서사/기록용.

    [2026-08 신설, 신민용 요청: "우리팀에 누가 나가고 누가 들어왔는지
    로그에 표시해달라"] my_team_id를 넘기면, 그 팀이 관여한 모든 이적
    (방출/영입)을 별도로 verbose_log에 전부 남긴다(위 "주요 이적" 1건
    필터와 무관하게 우리 팀 건은 전부). 선수 이름은 실명 대신
    constants.ai_player_code()가 만드는 "AI"+4자 코드(예: "AI73QU")를
    쓴다 — ui/formation_widget.py의 포메이션 화면과 완전히 동일한 규칙
    (ai_players.id 기반, 세이브 전체 기간 동안 절대 안 바뀜)이라 화면마다
    표기가 달라지는 일이 없다.

    [2026-08 신설, 상반기/하반기 이적 기록 분리 기능] volume_scale/
    is_mid_season — 신민용 요청("시즌 도중에도 AI 선수들 이적이 가능하긴
    하나 이때는 0~2명 정도만")으로 하반기 시작 직전(겨울 이적시장)에도
    이 함수를 한 번 더 부르기 위해 추가. 기존 오프시즌 호출(연 1회,
    팀당 1~2건 규모)은 volume_scale=1.0(기본값)으로 그대로 두고,
    시즌 도중 호출만 volume_scale을 작게 줘서(예: 0.15) 이적 건수를
    리그 전체 기준 확 줄인다 — n_transfers 계산식에 그대로 곱해지므로
    로직 변경 없이 규모만 조절된다. is_mid_season은 ai_transfer_log에
    그대로 저장돼, "선수 검색"이 그 해 기록을 상반기/하반기로 쪼갤지
    판단하는 근거가 된다.
    """
    moved = 0

    from constants import get_country_league_grade
    from economy import LEAGUE_GRADE_RANK
    # [2026-08 신설, 신민용 리포트: "중간 이적한 해 상반기 팀에 역할이
    # 안 뜬다/떠도 하반기 팀이랑 똑같이 뜬다"] 아래 mover 처리 루프에서
    # is_mid_season일 때만 이적 나가기 직전 역할을 계산하는 데 쓴다 —
    # 루프 안에서 매번 import하지 않도록 함수 시작에서 한 번만 가져온다.
    from formation_logic import compute_squad_roles

    # [2026-08 계측 추가, 신민용 리포트: "이적시장 0.92s가 어디서 쓰이는지
    # 쪼개보자"] 아직 로직은 그대로 두고 구간별 시간만 찍는다 —
    # (1) teams 조회(상관 서브쿼리 AVG(ovr) 포함, 팀마다 1회 실행되므로
    #     팀 수가 많을수록 이 구간이 의심됨) (2) 그룹핑 dict 구성
    # (3) team_players dict 구성 (4) 실제 이적 루프(667개 리그 × 팀당
    #     1~2건, _do_one_transfer_cached 반복 호출 — 가장 유력한 후보)
    # (5) executemany UPDATE.
    import time as _time_tm
    _tm0 = _time_tm.perf_counter()

    # [2026-08 버그수정, 재현성 문제 추적 중 발견] ORDER BY 없이 조회하면
    # by_league/by_country_tier/tier1_by_grade 등 이 함수 전체가 쓰는
    # 팀 후보 리스트들의 순서가 실행마다 달라질 수 있고, 그 순서가
    # random.choice() 등이 뽑는 인덱스에 그대로 영향을 줘서 동일 seed로도
    # 이적 결과가 실행마다 달라지는 원인이 됐다(RNG 소비량 계측으로 확인:
    # 이 함수 진입 전까지는 완전히 동일했는데 완료 후 소비량이 갈렸음).
    teams = [dict(r) for r in c.execute(
        """SELECT t.id AS tid, t.league_id AS lid, t.current_tier AS tier,
                  t.name AS tname, cn.id AS cid, cn.name AS cname,
                  (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) AS avg_ovr
           FROM teams t
           JOIN leagues l ON t.league_id = l.id
           JOIN countries cn ON l.country_id = cn.id
           ORDER BY t.id""").fetchall()]
    team_avg = {t["tid"]: (t["avg_ovr"] or 50) for t in teams}
    # [2026-08 신설, 신민용 리포트: "38~39세 OVR84~86짜리가 바르셀로나로
    # 이적하고, 유럽 5대 리그가 왜 저런 퇴물급을 영입하냐"] 목적지 선택이
    # 순수 OVR 격차·스쿼드 크기만 보고 나이는 전혀 안 봤던 게 원인 —
    # SS/S(최상위 5대 리그급) 목적지에 한해 나이 기반 페널티를 추가로
    # 곱하기 위해 팀별 등급을 미리 조회해둔다(아래 _do_one_transfer_cached
    # 참고).
    dst_grade_by_tid = {t["tid"]: get_country_league_grade(t["cname"]) for t in teams}
    # [2026-08 신설, 신민용 리포트: "OVR81따리가 레알 마드리드나 바르셀로나에
    # 있을 수 있냐"] 목적지 가우시안 가중치(아래 _do_one_transfer_cached)가
    # SS/S 등급 전체에 동일한 폭을 쓰다 보니, 등급은 SS/S여도 진짜 명문
    # (레알/바르사급)이 아닌 팀과 똑같은 관용폭을 진짜 명문팀에도 줘버렸다.
    # 진짜 명문(prestige_level>=2)은 훨씬 좁은 격차만 허용하도록 목적지별
    # 명문등급도 같이 미리 조회해둔다.
    from data.prestige_clubs import prestige_level as _tm_prestige_level
    dst_prestige_by_tid = {t["tid"]: _tm_prestige_level(t["cname"], t["tname"]) for t in teams}
    # [2026-08 신설, 이적 로그용] 팀마다 prestige_level을 한 번만 계산해
    # 캐싱 — 이적마다 다시 계산하면 수천 건 반복이라 성능에 영향을 준다.
    from data.prestige_clubs import prestige_level as _prestige_level_fn
    team_prestige = {t["tid"]: (_prestige_level_fn(t["cname"], t["tname"]) or 0) for t in teams}
    # [2026-08 최적화] verbose_log용 _estimate_ai_transfer_fee_display가
    # 이적마다 teams 리스트를 선형탐색(최대 2회) + 팀명 SQL SELECT 2회를
    # 추가로 날리고 있었다 — 여기서 tid→row 딕셔너리를 한 번만 만들어
    # 재사용하면 그 함수 안의 왕복이 전부 O(1) 조회로 바뀐다.
    team_row_by_tid = {t["tid"]: t for t in teams}
    _tm1 = _time_tm.perf_counter()

    # 리그별 팀 그룹 (기존, 87%용)
    by_league: dict = {}
    # 국내 다른 tier 그룹 (국가+tier 기준, 8%용)
    by_country_tier: dict = {}
    # tier1 등급별 그룹 (국제 이동, 5%용) — 등급 없는 나라는 제외
    tier1_by_grade: dict = {}
    team_tier = {}
    team_grade_rank = {}
    # [2026-08 최적화, 신민용 리포트: "이적루프 0.6~0.9s 원인 찾자"] 아래
    # 이적 루프 안에서 "국내 다른 tier" 후보군(8%)을 고를 때 src 팀의
    # cid(국가ID)가 필요한데, 예전엔 이걸 캐싱 안 하고 매번
    # `next(t["cid"] for t in teams if t["tid"]==src)`로 teams 리스트
    # 전체(전 세계 모든 리그, 수천 팀)를 선형탐색했다 — team_tier/
    # team_grade_rank는 이미 딕셔너리로 캐싱해뒀으면서 이것만 빠져있었다.
    # 이적 시도가 667개 리그에 걸쳐 수천~1만 건 발생하고 그중 8%가 이
    # 탐색을 타므로, "시도 수천 회 × teams 크기 수천"의 불필요한 반복이
    # 누적된 것으로 보인다 — 순수 O(1) 캐싱이라 결과는 완전히 동일하다.
    team_to_cid = {}
    team_lid = {}
    for t in teams:
        by_league.setdefault(t["lid"], []).append(t["tid"])
        by_country_tier.setdefault((t["cid"], t["tier"]), []).append(t["tid"])
        team_tier[t["tid"]] = t["tier"]
        team_to_cid[t["tid"]] = t["cid"]
        team_lid[t["tid"]] = t["lid"]
        if t["tier"] == 1:
            # [2026-08 grade resolution 단일화] 예전엔 COUNTRY_LEAGUE_GRADE에
            # 명시 등록 안 된 나라는 grade=None이라 "등급 없는 나라는 제외"
            # 방침으로 이 국제이동 풀(tier1_by_grade)에서 조용히 빠졌다.
            # get_country_league_grade()는 항상 유효한 등급(최소 국대 등급
            # fallback)을 반환하므로 더 이상 제외되는 나라가 없다 —
            # 등록 안 된 나라의 tier1 팀도 국제 이동 후보군에 정상 포함된다.
            grade = get_country_league_grade(t["cname"])
            rank = LEAGUE_GRADE_RANK.get(grade, 4)
            team_grade_rank[t["tid"]] = rank
            tier1_by_grade.setdefault(rank, []).append(t["tid"])
    _tm2 = _time_tm.perf_counter()

    # [2026-08 신설, 신민용 요청: "SS에서 뛰던 선수도 A로 바로 갈 수
    # 있고, 사우디·미국 1부 위주로 가는 그림을 만들어달라 — 현실에서도
    # 손흥민이 토트넘에서 미국으로 갔다"] 사우디아라비아·미국 tier1을
    # "은퇴 무대" 후보 풀로 별도 모아둔다 — 아래 국제이동 로직에서 나이
    # 든(노쇠화된) 선수가 최상위 리그를 떠날 때 이 풀을 우선적으로
    # 고려하게 한다(_do_one_transfer_cached에 전달).
    _VETERAN_DEST_COUNTRIES = {"사우디아라비아", "미국"}
    veteran_pool_tids = [t["tid"] for t in teams
                          if t["tier"] == 1 and t["cname"] in _VETERAN_DEST_COUNTRIES]

    # [2026-08 전면 재설계, 신민용 요청: "이적도 좀 더 현실적으로 —
    # 감독 성향/팀 성적에 따라 강팀은 소폭 보강, 중위권은 활발, 하위권은
    # 회전율 매우 높게, 강등팀은 대방출, 승격팀은 대보강"] 팀을 카테고리
    # (strong/mid/weak/promoted/relegated)로 분류해서, 카테고리별로 지정된
    # 범위 안에서 이번 시즌 "방출 인원 목표치"를 뽑는다 — 예전엔 리그
    # 전체 기준으로 팀 수×1~2배만큼만 총량을 굴리고 어떤 팀이 몇 명을
    # 내보낼지는 순전히 스쿼드 크기 가중치로 결정했는데, 이제 팀 성적/
    # 승강 상황이 직접 방출 규모를 결정한다.
    #
    # 승격/강등 판정: promotion_log(team_name 매칭이라 동명이팀 충돌
    # 위험이 있음)에 기대지 않고, "이번 시즌 실제로 뛴 리그"(match_results.
    # league_id, 승강 반영 전)와 "지금 teams.league_id"(승강 반영 후)를
    # 직접 비교한다 — 다르면 승강이 일어난 것이고, tier가 낮아졌으면
    # 승격/높아졌으면 강등이다. team_id 기준이라 이름 충돌 걱정이 없다.
    league_tier_by_id = {t["lid"]: t["tier"] for t in teams}
    # [2026-08 견고화] 방금 끝난 시즌의 원본 경기 데이터는 보통 아직
    # match_results에 남아있지만(archive_old_seasons가 이 함수보다
    # 나중에 실행됨 — game_engine.py의 호출 순서 참고), 혹시 이미
    # 지나간 시즌(예: 재시뮬레이션·디버그 목적의 단독 호출)을 대상으로
    # 부르는 경우까지 대비해 match_results_archive도 함께 조회한다
    # (get_team_history와 동일한 원칙).
    _std_rows = c.execute(
        "SELECT home_team_id, away_team_id, home_score, away_score, league_id "
        "FROM match_results WHERE year=? AND home_score>=0 "
        "UNION ALL "
        "SELECT home_team_id, away_team_id, home_score, away_score, league_id "
        "FROM match_results_archive WHERE year=? AND home_score>=0", (year, year)).fetchall()
    _wdl: dict = {}
    played_league_by_team: dict = {}
    for r in _std_rows:
        h, a, hs, as_, lid = r["home_team_id"], r["away_team_id"], r["home_score"], r["away_score"], r["league_id"]
        for tid in (h, a):
            _wdl.setdefault(tid, [0, 0, 0, 0, 0])
            played_league_by_team[tid] = lid
        if hs > as_:
            _wdl[h][0] += 1; _wdl[a][2] += 1
        elif hs < as_:
            _wdl[a][0] += 1; _wdl[h][2] += 1
        else:
            _wdl[h][1] += 1; _wdl[a][1] += 1
        _wdl[h][3] += hs; _wdl[h][4] += as_
        _wdl[a][3] += as_; _wdl[a][4] += hs

    rank_pct_by_team: dict = {}
    _by_played_league: dict = {}
    for tid, lid in played_league_by_team.items():
        _by_played_league.setdefault(lid, []).append(tid)
    for lid, tids_l in _by_played_league.items():
        ranked = sorted(tids_l, key=lambda t: (-(_wdl[t][0] * 3 + _wdl[t][1]),
                                                -(_wdl[t][3] - _wdl[t][4])))
        n = len(ranked)
        for i, tid in enumerate(ranked):
            rank_pct_by_team[tid] = (i + 1) / n

    promoted_ids: set = set()
    relegated_ids: set = set()
    for tid, played_lid in played_league_by_team.items():
        cur_lid = team_lid.get(tid)
        if cur_lid is None or played_lid == cur_lid:
            continue
        played_tier = league_tier_by_id.get(played_lid)
        cur_tier = team_tier.get(tid)
        if played_tier is None or cur_tier is None:
            continue
        if cur_tier < played_tier:
            promoted_ids.add(tid)
        elif cur_tier > played_tier:
            relegated_ids.add(tid)

    # (영입 하한, 영입 상한, 방출 하한, 방출 상한) — 신민용이 제시한
    # 실측 기반 구간을 그대로 적용. 영입 수는 이 함수에서 직접 강제하지
    # 않는다(목적지 선택은 기존처럼 OVR 적합도 가중 로직이 자연스럽게
    # 분산시키고, 승격팀처럼 원래도 매력적인 목적지는 자연히 더 많이
    # 받는다 — 방출 쪽만 카테고리별로 강제하면 영입 쪽은 시장 원리로
    # 따라온다). 방출 하한/상한만 실제로 쓰인다.
    _TRANSFER_QUOTA = {
        "strong":    (2, 4, 2, 4),
        "mid":       (4, 7, 5, 8),
        "weak":      (6, 10, 6, 10),
        "relegated": (5, 10, 8, 15),
        "promoted":  (8, 12, 5, 8),
    }

    # [2026-08 신설, 신민용 요청: "무작위로 바꾸지 말고 핵심 선수는
    # 상황에 따라 다르게 가야 하지 않냐"] 팀 카테고리별로 "에이스를
    # 얼마나 지키는지" 강도를 다르게 준다. 강팀은 스쿼드 뼈대를 안
    # 흔든다(높은 보호 → 에이스가 팔릴 확률 낮음), 반대로 약팀/강등팀은
    # "고주급자 스타들도 팀을 떠나려 한다"(7번 스펙 그대로) — 보호를
    # 크게 낮춰 핵심 자원도 실제로 현금화 대상이 되게 한다. mid는 기존
    # 고정값(0.85)을 그대로 유지 — 이번 변경 전과 동일하게 작동.
    _STAR_PROTECT_BY_CATEGORY = {
        "strong": 0.92, "mid": 0.85, "weak": 0.55,
        "relegated": 0.35, "promoted": 0.80,
    }

    def _team_category(tid):
        if tid in relegated_ids:
            return "relegated"
        if tid in promoted_ids:
            return "promoted"
        pct = rank_pct_by_team.get(tid)
        if pct is None:
            return "mid"
        if pct <= 0.25:
            return "strong"
        if pct >= 0.75:
            return "weak"
        return "mid"

    # [최적화] 팀별 선수 목록을 _retire_and_replace와 공유된 스냅샷에서 재사용
    all_players_rows = ai_rows if ai_rows is not None else c.execute(
        "SELECT id, team_id, position, age, name, ovr, contract_end_year, last_transfer_year "
        "FROM ai_players").fetchall()
    team_players: dict = {}
    # [2026-08 최적화] 예전엔 행마다 `"name" in r.keys()` 식으로 컬럼 존재
    # 여부를 매번 확인했다. sqlite3.Row.keys()는 호출할 때마다 컬럼 이름
    # 리스트를 새로 만들어 돌려주는 메서드라, 26만 행 × 컬럼 4개 =
    # 108만 회나 리스트를 만들고 버리고 있었다(cProfile 실측). 한 결과셋
    # 안에서는 컬럼 구성이 절대 바뀌지 않으므로 첫 행에서 딱 한 번만
    # 확인하고 그 결과를 재사용한다 — 판정 결과·기본값 처리는 동일.
    if all_players_rows:
        _cols = set(all_players_rows[0].keys())
        _has_name = "name" in _cols
        _has_age = "age" in _cols
        _has_cend = "contract_end_year" in _cols
        _has_lty = "last_transfer_year" in _cols
        for r in all_players_rows:
            _age = (r["age"] if _has_age else None) or 25
            _ovr = r["ovr"]
            team_players.setdefault(r["team_id"], []).append({
                "id": r["id"], "position": r["position"],
                "name": r["name"] if _has_name else "",
                "age": _age,
                "ovr": _ovr if _ovr is not None else 50,
                "contract_end_year": r["contract_end_year"] if _has_cend else 0,
                "last_transfer_year": r["last_transfer_year"] if _has_lty else 0,
            })
    # [2026-08 2차 최적화] 팀별 "인원 가중치" 표를 미리 만들어둔다.
    # size_w = exp(-(인원 - _SQUAD_TARGET)/0.15)는 인원(정수)만의 함수라,
    # 예전처럼 후보를 평가할 때마다(시즌당 267만 회) len()으로 세고 exp를
    # 부르는 대신 여기서 팀당 한 번만 계산해두고 이적으로 인원이 실제로
    # 바뀔 때만(이적 1건당 2팀) 갱신하면 된다. 값 자체는 예전 식 그대로다.
    _sw_by_tid = {tid: _size_weight(len(plist)) for tid, plist in team_players.items()}
    _tm3 = _time_tm.perf_counter()

    # 이적 결과 누적 후 executemany
    # [2026-07 v2] 이적 시 새 계약(2~4년)과 이적연도를 같이 기록한다 —
    # (new_team_id, new_contract_end_year, last_transfer_year, player_id)
    transfer_updates = []
    # [2026-08 신설, 신민용 요청: "주요 이적도 스페인/프랑스/독일/이탈리아/
    # 잉글랜드 각각 1명씩, 이름도 표시해서 각각 가장 비싼 이적료들을
    # 보여달라"] 예전엔 전세계 통틀어 딱 1건(_big_transfer)만 추적했는데,
    # 목적지 리그 국가별로 최고액 1건씩(5개국) 따로 추적하도록 확장.
    _MAJOR_TRANSFER_COUNTRIES = ("스페인", "프랑스", "독일", "이탈리아", "잉글랜드")
    _big_transfer_by_country: dict = {}   # {country_name: (fee, ovr, src_name, dst_name, player_id)}

    # [2026-08 신설, "명문팀 lifecycle 조사" 요청] AI 이적 로그 배치 —
    # season은 이 함수 호출당 한 번만 조회(이적 건마다 조회하면 수천 건
    # 반복이라 성능에 영향).
    _season_row = c.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    _cur_season = _season_row["current_season"] if _season_row else 0
    transfer_log_rows = []
    my_team_events = []   # [2026-08 신설] (방향, p_entry, old_tid, new_tid) — 우리 팀 관여 이적만

    # [2026-08 최적화] 이적 루프 전용 캐시 2종(이 호출 안에서만 살아있음).
    #  _intl_pool_by_rank: 국제 이동(5%) 후보군을 등급 rank별로 1회만 조립.
    #  _pool_meta_cache : 후보 풀 리스트별 (팀평균OVR / sigma분모 / SS·S여부)
    #                     배열. team_avg·dst_prestige_by_tid·dst_grade_by_tid는
    #                     이 루프 내내 불변이라 풀마다 한 번만 만들면 된다.
    # 둘 다 "매번 다시 계산하던 같은 값"을 재사용하는 것뿐이라 결과는 동일.
    _intl_pool_by_rank: dict = {}
    _pool_meta_cache: dict = {}

    for lid, tids in by_league.items():
        if len(tids) < 2:
            continue
        for src in tids:
            cat = _team_category(src)
            _out_lo, _out_hi = _TRANSFER_QUOTA[cat][2], _TRANSFER_QUOTA[cat][3]
            out_quota = random.randint(_out_lo, _out_hi)
            # [2026-08 확장, 상반기/하반기 이적 기록 분리 기능] 시즌 도중
            # 소규모 창구 호출(volume_scale<1.0)도 같은 카테고리 로직을 그대로
            # 쓰되, 목표 인원만 비례해서 줄인다 — "0~2명 정도만"이라는 신민용
            # 요청과 일치(강팀 방출목표 2~4명 × 0.15 ≈ 0명, 약팀 6~10명 × 0.15
            # ≈ 1명 등, 카테고리가 강할수록 시즌 도중 이적도 자연히 더 적다).
            if volume_scale != 1.0:
                out_quota = max(0, int(round(out_quota * volume_scale)))
            # [2026-08 신설, 15-7-3] out_quota 루프 시작 전에 이 팀의 "국제
            # 이동 승수"를 한 번만 계산해둔다(선수 하나하나가 아니라 팀
            # 단위 슬롯 확률이라 매 반복 재계산할 필요가 없음 — cat/
            # out_quota와 동일한 패턴). 팀 스쿼드 최고 OVR을 아웃라이어
            # 신호로 쓴다.
            _src_players_ovrs = [pl["ovr"] for pl in team_players.get(src, []) if pl.get("ovr")]
            _src_best_ovr = max(_src_players_ovrs) if _src_players_ovrs else team_avg.get(src, 50)
            _intl_mult = _outlier_intl_multiplier(
                _src_best_ovr, team_avg.get(src, 50), team_grade_rank.get(src))
            # 국제이동 비중을 5%*_intl_mult로 가변화(최대 35% 캡) — 승수가
            # 정확히 1.0(아웃라이어 없음 + SS/S급)이면 0.87/0.95 그대로라
            # 기존 동작과 100% 동일하다. 국내 다른 tier(8%) 폭은 고정 유지.
            _intl_share = min(0.35, 0.05 * _intl_mult)
            _same_league_upper = 1.0 - 0.08 - _intl_share
            _domestic_other_upper = 1.0 - _intl_share
            for _ in range(out_quota):
                src_tier = team_tier.get(src, 1)
                # 후보군 결정: (같은 리그) / (국내 다른 tier, 8% 고정) /
                # (국제, 기본 5%이나 위 _intl_share로 가변)
                roll = random.random()
                if roll < _same_league_upper or src_tier != 1:
                    dst_pool_tids = tids
                elif roll < _domestic_other_upper:
                    cid = team_to_cid.get(src)
                    cand = by_country_tier.get((cid, 2), []) or by_country_tier.get((cid, src_tier + 1), [])
                    dst_pool_tids = cand if len(cand) >= 1 else tids
                else:
                    rank = team_grade_rank.get(src)
                    if rank is None:
                        dst_pool_tids = tids
                    else:
                        # [2026-08 확장, 신민용 요청: "SS에서 뛰던 선수도
                        # A로 바로 갈 수는 있다"] 예전엔 ±1등급만 후보였는데
                        # (SS→S/SS까지만), 아래로 두 단계(rank-2)까지 넓혀서
                        # 최상위(S/SS) 선수도 그 아래 A급까지 곧장 갈 수
                        # 있게 한다 — 위로는 그대로 +1까지만(상승 이적은
                        # 점진적이어야 자연스러움, 비대칭 유지).
                        # [2026-08 최적화] 이 후보군은 "등급 rank"에만 의존
                        # 하는데(rank-2 ~ rank+1의 tier1 팀 전부, 전세계
                        # 1,200팀 규모), 예전엔 이적 한 건마다 매번 리스트를
                        # 새로 이어붙이고 다시 한 번 필터해서 통째로 복사했다
                        # — 시즌당 이 경로만 3,700회쯤 타므로 440만 회분의
                        # 불필요한 리스트 생성이었다. rank별로 딱 한 번만
                        # 만들어 재사용한다(같은 리스트 객체를 계속 넘기게
                        # 되므로 _do_one_transfer_cached의 풀 메타데이터
                        # 캐시도 그대로 적중한다).
                        # src 제외는 예전엔 여기서 했지만 어차피
                        # _do_one_transfer_cached의 가중치 루프가 t != src를
                        # 한 번 더 거른다 — src는 자기 rank 풀에 반드시
                        # 포함되므로(rank가 (rank-2..rank+1) 범위 안에 있음)
                        # "src를 뺀 뒤 1개 이상"은 "빼기 전 2개 이상"과
                        # 항상 같은 조건이라 판정 결과도 동일하다.
                        cand = _intl_pool_by_rank.get(rank)
                        if cand is None:
                            cand = []
                            for r in (rank - 2, rank - 1, rank, rank + 1):
                                cand.extend(tier1_by_grade.get(r, []))
                            _intl_pool_by_rank[rank] = cand
                        dst_pool_tids = cand if len(cand) >= 2 else tids

                # [2026-08 신설, 신민용 요청: "사우디·미국 1부 위주로 가는
                # 그림"] 국제이동(87%/8% 아닌 위 else 분기)이고 src가
                # 최상위권(S/SS, rank>=7)일 때만 veteran_pool_tids를 같이
                # 넘긴다 — _do_one_transfer_cached가 실제 mover(선수)가
                # 정해진 뒤에 그 선수 나이를 보고, 나이 든 선수면 이 풀을
                # 우선 후보로 쓴다(뒤에서 구현).
                # [2026-08 수정, 15-7-3] 국제이동 분기 상한이 0.95 고정에서
                # _domestic_other_upper(가변)로 바뀌었으므로 이 판정도
                # 그에 맞춰 같이 옮긴다 — 안 옮기면 _intl_share가 커진
                # 팀에서 roll이 0.90~0.95 사이일 때 "국제 이동"인데도
                # veteran_pool 판정에서는 여전히 빠지는 불일치가 생긴다.
                _veteran_pool = (veteran_pool_tids
                                 if (roll >= _domestic_other_upper and src_tier == 1
                                     and (team_grade_rank.get(src) or 0) >= 7)
                                 else None)

                result = _do_one_transfer_cached(
                    src, dst_pool_tids, team_players, team_avg, year,
                    protect_strength=_STAR_PROTECT_BY_CATEGORY[cat],
                    veteran_pool_tids=_veteran_pool,
                    dst_grade_by_tid=dst_grade_by_tid,
                    dst_prestige_by_tid=dst_prestige_by_tid,
                    pool_cache=_pool_meta_cache, sw_by_tid=_sw_by_tid)
                if result:
                    for new_tid, pid, old_tid in result:
                        new_contract_end = year + random.randint(2, 4)
                        transfer_updates.append((new_tid, new_contract_end, year, pid))
                        # [2026-08 성능 수정, 신민용 리포트: "52주차→1주차 렉"]
                        # 예전엔 이동한 선수를 원 소속팀 리스트에서 지울 때
                        # next()로 한 번 찾고(O(n)), 그다음 리스트 컴프리헨션으로
                        # 그 선수만 뺀 새 리스트를 통째로 다시 만들었다(O(n) 또
                        # 한 번) — 시즌당 이적 2.7만여 건마다 이 이중 O(n)이
                        # 반복되며 _transfer_market 자체 시간의 상당 부분을
                        # 차지하고 있었다(cProfile 실측: tottime 0.48s). 인덱스를
                        # 한 번만 찾아 pop()으로 바로 제거하면 한 번의 스캔으로
                        # 끝나고, 새 리스트를 통째로 재할당하지도 않는다 — 결과는
                        # 동일(같은 선수가 원 소속팀 리스트에서 빠지고 목적지
                        # 팀 리스트에 추가됨). [2026-08 추가 조사] "같은 팀을
                        # src로 다시 뽑았을 때 mover 선정 계산을 캐싱"하는 방안도
                        # 시도해봤으나, 실측 캐시 히트율이 0%였다(이적 시도의
                        # 성공률이 거의 100%에 가까워 캐시가 쌓이기도 전에 거의
                        # 매번 무효화됨) — 이득이 없어 되돌리고 이 pop() 수정만
                        # 남긴다.
                        _old_list = team_players.get(old_tid, [])
                        _idx = next((i for i, e in enumerate(_old_list) if e["id"] == pid), None)
                        # [2026-08 신설, 신민용 리포트: "중간 이적한 해에
                        # 상반기 팀엔 역할(주전/로테이션 등)이 안 뜬다 —
                        # 뜨더라도 하반기 팀이랑 완전히 똑같이 뜨는데,
                        # 실제로는 상반기 팀에서 후보였다"] world_browser.py의
                        # 반기 표시(_half_season_league_entry)는 지금까지
                        # ai_player_position_history.role(연도 하나당 한
                        # 값 — 그 해 "최종/하반기" 소속팀 스냅샷)을 상/하반기
                        # 두 줄에 그대로 같이 썼다 — 상반기(이 시점 old_tid)
                        # 팀 로스터 기준 역할이 따로 없었기 때문. pop() 하기
                        # 직전(선수 본인이 아직 이 로스터에 포함돼 있을 때)
                        # compute_squad_roles로 "나가기 직전 그 팀에서의
                        # 역할"을 계산해 이적 로그에 같이 남긴다 — 오프시즌
                        # (연 1회, 팀당 1~2건이지만 세계 전체로는 수만 건)은
                        # world_browser.py가 애초에 반기 분리 표시를 안 해서
                        # 이 값이 쓰이지도 않으므로, is_mid_season(팀당
                        # 0~2명 규모)일 때만 계산해 비용을 그 작은 물량으로
                        # 가둔다.
                        _dep_role = ""
                        if is_mid_season and _idx is not None:
                            _dep_role = compute_squad_roles(
                                [(e["id"], e.get("ovr"), e.get("age")) for e in _old_list]
                            ).get(pid, "")
                        p_entry = _old_list.pop(_idx) if _idx is not None else None
                        if p_entry is not None:
                            # 인원이 바뀐 팀만 가중치 표를 갱신(위 _sw_by_tid 주석 참고)
                            _sw_by_tid[old_tid] = _size_weight(len(_old_list))
                        if p_entry:
                            # [2026-08 신설, 이적 로그] p_entry는 아직 이적 전 값(포지션/
                            # 나이/OVR)이라 이 시점에 기록해야 정확하다 — 아래에서
                            # contract_end_year/last_transfer_year을 덮어쓰기 직전.
                            _from_lid = team_lid.get(old_tid)
                            _to_lid = team_lid.get(new_tid)
                            if _from_lid == _to_lid:
                                _actual_ttype = "리그내"
                            elif team_to_cid.get(old_tid) == team_to_cid.get(new_tid):
                                _actual_ttype = "국내 타부수"
                            else:
                                _actual_ttype = "국제 이동"
                            transfer_log_rows.append((
                                _cur_season, year, pid, p_entry.get("name", ""), p_entry.get("position", ""),
                                p_entry.get("age", 0), p_entry.get("ovr", 0),
                                old_tid, new_tid,
                                team_prestige.get(old_tid, 0), team_prestige.get(new_tid, 0),
                                round(team_avg.get(old_tid, 0), 2), round(team_avg.get(new_tid, 0), 2),
                                _actual_ttype, 1 if is_mid_season else 0, _dep_role))
                            # [2026-08 신설, 신민용 요청: "우리팀에 누가
                            # 나가고 누가 들어왔는지"] 우리 팀이 관여한
                            # 건이면(방출 또는 영입) 별도로 모아둔다 —
                            # p_entry(포지션/나이/OVR)를 이 시점에 얕은
                            # 복사해서 남긴다(아래에서 계약 필드를 덮어쓰기
                            # 전이라 이적 전 상태 그대로).
                            if my_team_id is not None and (old_tid == my_team_id or new_tid == my_team_id):
                                direction = "out" if old_tid == my_team_id else "in"
                                my_team_events.append((direction, dict(p_entry), old_tid, new_tid))
                            p_entry["contract_end_year"] = new_contract_end
                            p_entry["last_transfer_year"] = year
                            _new_list = team_players.setdefault(new_tid, [])
                            _new_list.append(p_entry)
                            _sw_by_tid[new_tid] = _size_weight(len(_new_list))
                            if verbose_log is not None:
                                _fee = _estimate_ai_transfer_fee_display(p_entry, old_tid, new_tid, year, team_row_by_tid)
                                if _fee:
                                    _dst_row2 = team_row_by_tid.get(new_tid)
                                    _dst_country = _dst_row2["cname"] if _dst_row2 else None
                                    if _dst_country in _MAJOR_TRANSFER_COUNTRIES:
                                        _prev = _big_transfer_by_country.get(_dst_country)
                                        if _prev is None or _fee[0] > _prev[0]:
                                            _big_transfer_by_country[_dst_country] = (*_fee, p_entry["id"])
                    moved += 1
    _tm4 = _time_tm.perf_counter()

    if transfer_updates:
        # [2026-08 최적화] 위 스냅샷과 같은 이유 — WHERE id=? 로 8만 건을
        # 갱신하는데 순서가 뒤죽박죽이면 매번 다른 페이지를 오간다. id 순으로
        # 정렬하면 앞에서 뒤로 한 번 훑는 형태가 된다. 안정 정렬이라 같은
        # 선수가 두 번 들어 있어도(맞트레이드 등) 원래의 앞뒤 순서가 유지되므로
        # 마지막에 적용되는 값이 예전과 같다 — 최종 결과 동일.
        transfer_updates.sort(key=_tu_key)
        c.executemany(
            "UPDATE ai_players SET team_id=?, contract_end_year=?, last_transfer_year=? WHERE id=?",
            transfer_updates)
    if transfer_log_rows:
        c.executemany(
            """INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            transfer_log_rows)
    _tm5 = _time_tm.perf_counter()
    print(f"[PERF-TM]  teams조회(서브쿼리포함) {_tm1-_tm0:.3f}s | "
          f"그룹핑 {_tm2-_tm1:.3f}s | team_players빌드 {_tm3-_tm2:.3f}s | "
          f"이적루프({len(by_league)}개리그) {_tm4-_tm3:.3f}s | "
          f"executemany({len(transfer_updates)}건) {_tm5-_tm4:.3f}s")

    # [2026-08 버그수정, 신민용 리포트: "이적 뉴스가 실제론 28주차(겨울
    # 이적시장 마감=WINTER_OFFER_END_DAY) 사건인데 52주차로 뜬다"] 이 함수는
    # 오프시즌 전체 처리(run_ai_offseason, 연 1회·시즌이 완전히 끝난 뒤라
    # 진짜 52주차)와 시즌 도중 겨울 이적시장(run_ai_mid_season_transfer,
    # is_mid_season=True로 호출)이 공유해서 부르는데, 아래 "news" 로그들은
    # 호출 맥락과 무관하게 항상 week=52를 찍고 있었다 — 오프시즌 호출은
    # 실제로 52주차라 우연히 맞았지만, 겨울 이적시장 호출 때도 그대로 52가
    # 찍혀서 실제 사건 시점(겨울 이적시장 마감 주차)과 어긋났다.
    # is_mid_season일 땐 WINTER_OFFER_END_DAY를 주차로 환산해 실제 마감
    # 시점을 쓴다.
    from constants import day_to_week, WINTER_OFFER_END_DAY
    _news_week = day_to_week(WINTER_OFFER_END_DAY) if is_mid_season else 52

    if verbose_log is not None and _big_transfer_by_country:
        from constants import ai_player_code
        from database import get_ai_player_custom_name
        # [2026-08 신설] 국가별로(5대리그) 최고액 1건씩, 이름도 같이 표시.
        # log_type="news" — ui/log_panel.py의 "뉴스" 탭 전용 필터 대상.
        for _country in _MAJOR_TRANSFER_COUNTRIES:
            _entry = _big_transfer_by_country.get(_country)
            if not _entry:
                continue
            fee, ovr, src_name, dst_name, _pid = _entry
            _tag = get_ai_player_custom_name(_pid) or ai_player_code(_pid)
            verbose_log(f"💰 주요 이적({_country}): {_tag} (OVR{ovr})  {src_name} → {dst_name}  "
                        f"예상 이적료 약 {fee/100000:.0f}억원", "news", year, _news_week)

    # [2026-08 신설, 신민용 요청: "우리팀에 누가 나가고 누가 들어왔는지
    # 로그에 표시해달라"] 위 "주요 이적"(전세계 최고액 1건)과 별개로,
    # 우리 팀이 관여한 이적은 방출/영입 전부 각각 한 줄씩 남긴다.
    if verbose_log is not None and my_team_events:
        from constants import ai_player_code
        from database import get_ai_player_custom_name
        for direction, p_entry, old_tid, new_tid in my_team_events:
            _fee = _estimate_ai_transfer_fee_display(p_entry, old_tid, new_tid, year, team_row_by_tid)
            _fee_txt = f"  (예상 이적료 약 {_fee[0]/100000:.0f}억원)" if _fee else ""
            # [2026-08 수정, 신민용 리포트: "좌측(이적 로그)엔 AI (331454)로
            # 뜨는데 포메이션엔 AI 73QU로 따로 뜬다"] 포메이션 화면
            # (ui/formation_widget.py._mask_ai_names)과 완전히 같은 코드
            # 생성 규칙(constants.ai_player_code)을 공유해서, 같은 선수는
            # 어느 화면에서 봐도 항상 같은 표기로 보이게 한다.
            # [2026-08 확장, 신민용 요청: "AICD8C 식별코드로 뜨는 선수의
            # 이름을 내가 지을 수 있게 — 이적 로그도 내가 지은 이름으로"]
            # 사용자가 지어준 이름이 있으면 코드 대신 그 이름을 쓴다.
            _tag = get_ai_player_custom_name(p_entry['id']) or ai_player_code(p_entry['id'])
            # [2026-08 신설, 신민용 요청: "우리팀이 뭔지도 표시해달라 —
            # 나중에 다른 팀으로 옮기면 저게 언제 어느 팀에서 있었던
            # 일인지 알 수가 없다"] 그때 당시의 "우리팀" 이름을 명시
            # 적으로 같이 남긴다 — my_team_id는 이 호출 시점(그 이적이
            # 실제로 일어난 그 해)의 소속팀이므로, 나중에 다른 팀으로
            # 이적해도 이 로그 한 줄만 보면 그때 어느 팀 소속으로 겪은
            # 일인지 항상 알 수 있다.
            _my_team_name = team_row_by_tid.get(my_team_id, {}).get("tname", "우리팀")
            if direction == "out":
                _dst = team_row_by_tid.get(new_tid, {}).get("tname", "?")
                verbose_log(f"📤 방출 — {_tag} ({p_entry.get('position','')} OVR{p_entry.get('ovr',0)}) "
                            f"{_my_team_name} → {_dst}{_fee_txt}", "news", year, _news_week)
            else:
                _src = team_row_by_tid.get(old_tid, {}).get("tname", "?")
                verbose_log(f"📥 영입 — {_tag} ({p_entry.get('position','')} OVR{p_entry.get('ovr',0)}) "
                            f"{_src} → {_my_team_name}{_fee_txt}", "news", year, _news_week)

    return moved


def _estimate_ai_transfer_fee_display(p_entry, old_tid, new_tid, year, team_row_by_tid):
    """[2026-07 v3 신설] 표시용 이적료 — 자금 이동 없음, DB 저장도 없음.
    이적 순간에만 즉석 계산해서 그 시즌 최고액 1건만 로그로 소비하고 버린다.
    OVR85 이상이거나 이적료 최고액인 경우에만 verbose_log에서 실제로
    출력되도록, 여기서는 조건 없이 계산만 해서 넘긴다(최종 필터는 호출부).

    team_row_by_tid: {tid: team_row_dict} — 예전엔 teams 리스트를 매번
    선형탐색(최대 2회)하고 팀명도 별도 SQL SELECT 2회로 조회했는데
    (신민용 리포트: "이적루프 렉" 조사 중 발견), 호출부에서 만든 tid→row
    딕셔너리를 그대로 받아 전부 O(1) 조회로 바꾼다 — 결과는 동일.

    [2026-08 버그수정, 신민용+GPT 리포트: "OVR99 선수가 아틀레티코 마드리드
    → 알코벤다스 CF인데 6276억이면 이상하다"] estimate_transfer_fee()에
    country/team_id를 안 넘기고 있었다 — economy.py 쪽 로직 자체는 멀쩡한데
    (country and tier)가 False가 되어 구단 지불여력 상한(affordability
    cap)이 아예 통째로 건너뛰어지고 있었다(디버그로 직접 확인:
    affordability_cap=None). 명문/체급 보정도 team_id가 없어서 전부 중립
    (1.0) 처리됐다 — 방금 승격한 약체 구단이어도 "표시용 이적료"에서는
    부자 구단과 똑같이 취급됐다는 뜻. dst_row에 이미 cname(국가명)과
    tid(팀ID)가 있으므로 그대로 넘기기만 하면 된다 — 실제 이적(어느
    팀으로 가는지, 스탯이 어떻게 바뀌는지)에는 영향 없음, 오직 이
    로그 한 줄의 "예상 이적료" 표시값만 정확해진다."""
    if p_entry.get("ovr", 0) < 70:
        return None   # 너무 낮은 OVR은 계산 자체를 생략(성능/의미 둘 다 낮음)
    try:
        from economy import estimate_transfer_fee
        from constants import get_country_league_grade
        dst_row = team_row_by_tid.get(new_tid)
        if not dst_row:
            return None
        grade = get_country_league_grade(dst_row["cname"])
        fee = estimate_transfer_fee(grade, dst_row["tier"], p_entry["ovr"],
                                    country=dst_row["cname"], team_id=new_tid,
                                    position=p_entry.get("position"), year=year)
        if not fee or (p_entry.get("ovr", 0) < 85 and fee < 5_000_000):  # 50억(천원단위) 미만이면 스킵
            return None
        src_row = team_row_by_tid.get(old_tid)
        src_name = src_row["tname"] if src_row else None
        dst_name = dst_row.get("tname")
        return (fee, p_entry["ovr"],
                src_name if src_name else "?",
                dst_name if dst_name else "?")
    except Exception:
        return None


# [2026-08 최적화] 아래 _do_one_transfer_cached 전용 순수함수 메모이즈 2종.
# 둘 다 "입력이 정수(또는 작은 정수)뿐인 수식"이라 값이 항상 같으므로
# 캐싱해도 결과가 달라질 여지가 전혀 없다 — 계산식 자체는 원본 그대로다.
_CONTRACT_DECAY = {k: 0.6 ** k for k in range(0, 13)}   # 0.6 ** 남은계약연수
from operator import itemgetter as _itemgetter
_ins_key = _itemgetter(0)   # executemany 전에 기본키 순으로 정렬할 때 쓰는 key
_tu_key  = _itemgetter(3)   # 이적 UPDATE 배치를 player_id 순으로 정렬
_SIZE_W_CACHE: dict = {}


def _size_weight(dst_size):
    """exp(-(스쿼드인원 - _SQUAD_TARGET) / 0.15) — 인원(정수)만의 함수라
    한 번 계산한 값을 그대로 재사용한다. 시즌당 math.exp 호출 약 290만 회
    감소(실측 5,771,032회 중 절반가량이 이 식이었다)."""
    w = _SIZE_W_CACHE.get(dst_size)
    if w is None:
        w = math.exp(-(dst_size - _SQUAD_TARGET) / 0.15)
        _SIZE_W_CACHE[dst_size] = w
    return w


# [2026-08 최적화] 포지션 문자열 → 판매보호 판정용 그룹키(GK/DF/MF/FW).
# 예전 코드의 _GROUP_KEY[_pos_category(pos)]를 한 단계로 미리 합쳐둔 표다
# (formation_logic._POS_CATEGORY와 같은 분류를 그대로 쓴다). 표에 없는
# 포지션(ST/LW/RW/CF/SS 등)은 예전 _pos_category의 최종 폴백 "ATK"에
# 대응하는 "FW"로 떨어지므로 결과가 완전히 같다.
_POS_GROUP = {"GK": "GK",
              "CB": "DF", "LB": "DF", "RB": "DF", "LWB": "DF", "RWB": "DF", "SW": "DF",
              "CDM": "MF", "CM": "MF", "CAM": "MF", "LM": "MF", "RM": "MF", "DM": "MF", "AM": "MF"}


def _do_one_transfer_cached(src, dst_pool_tids, team_players, team_avg, year, protect_strength=0.85,
                             veteran_pool_tids=None, dst_grade_by_tid=None, dst_prestige_by_tid=None,
                             pool_cache=None, sw_by_tid=None):
    """[최적화] ORDER BY RANDOM() 없이 Python-side shuffle로 이적 처리.
    team_players: {team_id: [{"id","position","ovr","contract_end_year",
    "last_transfer_year"}, ...]} 선조회 캐시.
    src: 판매 측 팀ID(호출부에서 이미 결정해서 넘김).
    dst_pool_tids: 목적지 후보 팀ID 리스트(같은 리그/국내 다른 tier/국제
      중 호출부가 이미 결정한 풀 — src 자신은 포함 안 돼 있어도/있어도 무방,
      아래에서 다시 한번 걸러진다).

    [버그수정 2026-07] team_avg를 함수가 받기만 하고 실제로는 전혀 참조하지
    않아, 리그 내 이적이 팀 실력과 무관하게 완전 무작위로 일어나고 있었다
    (최강팀 선수가 최약팀으로 가는 것과 그 반대가 똑같은 확률). 이제 이동할
    선수(mover)의 OVR과 각 목적지 팀 평균OVR(team_avg) 차이가 작을수록
    (비슷한 수준 팀끼리, 혹은 살짝 더 좋은 팀으로) 그 팀이 목적지로 뽑힐
    확률이 높아지도록 가우시안 가중치를 준다 — 팀 간 실력차가 40 이상이면
    사실상 이적 후보에서 배제된다(가중치가 0에 수렴).

    [2026-07 v2 신설, 신민용+GPT 검토: "레알 에이스나 벤치나 완전히 같은
    확률로 이적하면 세계가 너무 흔들린다 — 스타 선수는 조금만 보호해도
    이적시장이 훨씬 현실적으로 보인다"] mover를 뽑는 단계 자체를 완전
    균등추출(random.choice)에서, "팀 내 OVR 순위가 높을수록 뽑힐 확률을
    낮추는" 가중 추출로 바꾼다. 팀 재정/감독 관계 같은 내 선수급 디테일은
    AI에겐 없으니(원칙: AI는 플레이어보다 단순해야 한다), OVR 순위 하나만
    가지고 가볍게 계산한다.

    [2026-07 v3 신설] 계약 잔여기간 반영 + 최소 잔류기간(1시즌). 둘 다
    "AI는 단순하게" 원칙에 맞춰 가벼운 규칙만 적용한다:
      - last_transfer_year가 최근(작년)이면 이적 후보에서 아예 제외.
      - 계약 미설정(0, 기존 선수)은 중립 취급, 설정돼 있으면 남은 연수가
        많을수록(0.6^연수) 뽑힐 확률이 줄어든다.

    [2026-07 v3 신설] 국내 다른 tier/국제 이동 풀이 넘어올 경우, 어린
    선수·고OVR·계약만료 임박 선수일수록 그 풀로 실제로 이동될 확률이
    붙도록(성향 보정) mover 선정 가중치에 반영한다 — "유망주 해외 진출/
    베테랑 잔류/스타 이적" 패턴이 자연스럽게 생기게.

    [2026-08 확장, 신민용 요청: "무작위로 바꾸지 말고 핵심 선수는 상황에
    따라 다르게 가야 하지 않냐"] protect_strength를 호출부(카테고리별
    _STAR_PROTECT_BY_CATEGORY)에서 넘겨받는다 — 강팀은 에이스를 거의 안
    팔고(높은 보호), 약팀/강등팀은 반대로 에이스도 현금화 대상이 된다
    ("고주급자 스타들은 팀을 떠나려 한다"는 신민용 스펙 그대로) 낮은
    보호로 실제 매각 확률이 오르게. 기본값 0.85는 예전 고정값과 동일 —
    호출부가 안 넘기면 기존과 완전히 같게 동작한다.

    [2026-08 신설, 신민용 요청: "SS에서 뛰던 선수도 A로 바로 갈 수 있고,
    사우디·미국 1부 위주로 가는 그림을 만들어달라 — 현실에서도 손흥민이
    토트넘에서 미국으로 갔다"] veteran_pool_tids(사우디·미국 tier1
    팀 목록, 호출부가 src가 S/SS급 최상위권일 때만 넘김)가 있고 이번에
    뽑힌 mover가 30세 이상이면, 60% 확률로 목적지 후보를 이 풀로 바꿔서
    고른다 — mover가 정해지기 전(위 후보군 결정 시점)엔 그 선수 나이를
    알 수 없어서, 여기 mover 선정 직후에 판단해야 한다. 30세 미만이거나
    확률에 안 걸리면 기존처럼 등급대 기반 일반 후보군을 그대로 쓴다.
    [2026-08 신설, 신민용 리포트: "38~39세 OVR84~86짜리가 바르셀로나로
    이적한다 — 유럽 5대 리그급이 왜 저런 나이의 선수를 영입하냐"] 예전엔
    mover 선정도 목적지 선정도 나이를 전혀 안 봤다(OVR/스쿼드 크기만
    반영) — 이제 (1) 33세 이상이면 mover로 뽑힐 가중치를 추가로 올려
    노쇠한 선수가 (특히 좋은 팀에서) 더 빨리 정리되게 하고, (2) 목적지가
    SS/S(5대 리그급) 등급이면 33세 이상부터 나이에 비례해 급격히 감쇠하는
    페널티를 곱한다 — 다만 OVR이 정말 레전드급(85 초과)이면 감쇠를
    완화해 "노장 슈퍼스타가 아주 가끔 빅클럽에 남는" 예외는 허용한다.
    """
    src_players = team_players.get(src, [])
    if not src_players:
        return None

    # 최소 잔류기간: 작년(또는 그 이후)에 이미 이적한 선수는 이번엔 후보 제외
    # [2026-08 최적화] team_players의 각 항목은 _transfer_market이 만들 때
    # 6개 키를 항상 전부 채우고(빠지는 경우가 구조적으로 없음), 이적으로
    # 팀을 옮겨 다니는 동안에도 같은 dict가 그대로 재사용된다 — 그래서
    # 이 함수 전체에서 .get(키, 기본값) 대신 직접 인덱싱을 쓴다. cProfile
    # 실측상 이 함수 하나가 dict.get을 2,421만 회 호출하고 있었는데(시즌당
    # 이적 7.4만 건 × 후보 선수 수 × 키 6개), 결과는 완전히 동일하면서
    # 호출당 오버헤드만 사라진다.
    eligible = [p for p in src_players if (year - p["last_transfer_year"]) >= 1]
    if not eligible:
        return None

    # [2026-08 신설, 신민용 요청: "마지막 GK/마지막 CB 같은 선수가 정상
    # 판매 후보로 들어가면 안 된다 — 그 선수를 팔면 팀에 해당 포지션
    # 그룹이 0명이 되는가만 검사해서 막아야 한다"] 원인: 위 eligible은
    # "작년에 이적했는가"만 볼 뿐 포지션은 전혀 안 봐서, 팀의 유일한
    # GK도 다른 후보와 똑같이(순위가 낮으면 오히려 더 높은 확률로) 팔려
    # 나갈 수 있었다 — 신민용 리포트: GK가 0명이라 LW 주포 선수가 GK로
    # 뛴 사례. 이 아래 필터는 그 경로 자체를 차단한다: "팔면 그 포지션
    # 그룹(GK/DF/MF/FW, _pos_category 기준)이 팀에서 0명이 되는 선수"만
    # 후보에서 제외하고, 그 외에는 기존 판매 확률 가중치(OVR순위/나이/
    # 계약)를 그대로 둔다 — 신민용 명시 요청대로 가중치 자체는 절대
    # 안 건드림. src_players(시간 필터 전 팀 전체 로스터) 기준으로
    # 그룹별 인원을 세야 정확하다(마지막 1명 판정은 "지금 이 팀에 몇
    # 명 있는가"의 문제이지 "언제 이적했는가"와는 무관하므로).
    # [2026-08 최적화] 예전엔 선수 한 명당 "함수 호출(_pos_category) →
    # 그 결과로 _GROUP_KEY.get" 2단계를 거쳤다. 시즌당 이적 7.4만 건 ×
    # 팀 로스터 23명 × 2회(집계+판정)라 이 2단계만 350만 회 넘게 돌았다.
    # 포지션 문자열 → 그룹키는 순수 대응이므로 모듈 상단에서 한 번
    # 합쳐둔 표(_POS_GROUP)로 조회 한 번에 끝낸다 — 매핑 결과는 예전과
    # 완전히 동일(표에 없는 포지션은 ATK→"FW"로 떨어지는 것까지 동일).
    _grp_count: dict = {}
    _pg = _POS_GROUP
    for _p in src_players:
        _g = _pg.get(_p["position"], "FW")
        _grp_count[_g] = _grp_count.get(_g, 0) + 1
    _gc = _grp_count.get
    _protected_ids = {p["id"] for p in eligible
                       if _gc(_pg.get(p["position"], "FW"), 0) <= 1}
    if _protected_ids and len(_protected_ids) < len(eligible):
        eligible = [p for p in eligible if p["id"] not in _protected_ids]
    # (매우 드문 극단적 예외: 팀 전체가 포지션 그룹당 딱 1명씩이라 위
    # 필터가 eligible을 통째로 비워버리는 경우엔 적용하지 않는다 —
    # 이적 자체가 완전히 멈추는 것보다는 기존 동작이 낫다.)
    if not eligible:
        return None

    n = len(eligible)
    if n == 1:
        mover = eligible[0]
    else:
        # [2026-08 최적화] 예전엔 sorted(range(n), key=lambda i: -eligible[i].get("ovr",50))
        # 로 정렬해서 비교 한 번마다 파이썬 람다 + dict.get이 돌았다(실측
        # 람다 호출만 151만 회). OVR을 미리 한 번씩만 꺼내 리스트로 만들고
        # 그 리스트의 __getitem__을 key로 쓰면 비교 자체는 C 레벨에서 끝난다
        # — 키 값(-ovr)도, 동점자 순서(파이썬 정렬은 안정 정렬)도 예전과
        # 완전히 동일하므로 뽑히는 선수가 달라지지 않는다.
        _neg_ovr = [-e["ovr"] for e in eligible]
        ranked = sorted(range(n), key=_neg_ovr.__getitem__)
        _inv = n - 1   # n>=2 이므로 예전의 max(1, n-1)과 항상 같은 값
        weights = [0.0] * n
        for pos_rank, i in enumerate(ranked):
            _e = eligible[i]
            w = 0.15 + protect_strength * (pos_rank / _inv)
            _cend = _e["contract_end_year"] or 0
            remain = max(0, _cend - year) if _cend else 2   # 미설정=중립(2년 취급)
            w *= _CONTRACT_DECAY.get(remain) or 0.6 ** remain
            # [2026-07 v3] 어린 선수(22세 이하)·고OVR(80+)·계약만료 임박(1년
            # 이하)일수록 이 풀(국내 다른 tier/국제 이동)로 실제 이동될
            # 성향을 살짝 높인다. dst_pool_tids가 src 포함 같은 리그 그대로면
            # (=87% 케이스) 이 보정은 사실상 의미 없이 상쇄되므로 안전하다.
            _age = _e["age"]
            if _age <= 22:
                w *= 1.3
            if _e["ovr"] >= 80:
                w *= 1.5
            if _cend and (_cend - year) <= 1:
                w *= 1.4
            # [2026-08 신설] 노쇠한 선수(33세+)는 팀 내 OVR 순위와 무관하게
            # 추가로 이동(퇴출) 확률을 높인다 — 현실 클럽은 "아직 스쿼드
            # 내 최약체는 아니어도" 나이 자체를 이유로 세대교체를 하므로.
            if _age >= 33:
                w *= 1.0 + 0.10 * (_age - 32)
            weights[i] = w
        mover = random.choices(eligible, weights=weights, k=1)[0]

    # [2026-08 신설, 신민용 요청: "사우디·미국 1부 위주로 가는 그림"]
    # 나이 든(30세+) 선수가 최상위권 리그를 떠나는 경우, 이 풀이 있으면
    # 60% 확률로 목적지 후보를 여기로 바꾼다 — 나머지 40%/veteran_pool
    # 자체가 비어있는 경우엔 기존 등급대 기반 일반 후보군을 그대로 쓴다
    # (은퇴 무대로 완전히 강제하지 않고 개인차/확률을 남겨둔다).
    if veteran_pool_tids and mover["age"] >= 30 and random.random() < 0.6:
        # [2026-08 최적화] 예전엔 여기서 매번 [t for t in veteran_pool_tids
        # if t != src]로 새 리스트를 만들었다 — src 제외는 아래 가중치
        # 루프가 어차피 한 번 더 하므로, 여기서는 "src를 뺐을 때 남는 팀이
        # 하나라도 있는지"만 O(1)로 판정하고 원본 리스트를 그대로 넘긴다
        # (아래 풀 메타데이터 캐시가 같은 리스트 객체를 재사용할 수 있게
        # 하는 효과도 있다). 판정 결과·이후 동작은 예전과 동일.
        if len(veteran_pool_tids) > 1 or veteran_pool_tids[0] != src:
            dst_pool_tids = veteran_pool_tids

    mover_ovr = mover["ovr"]
    # 가우시안 가중치: 목적지 팀 평균OVR이 이 선수 수준과 비슷할수록(약간
    # 위쪽 포함) 가중치가 크다. sigma=15 → 격차 15면 가중치 약 0.61배,
    # 격차 30이면 약 0.14배로 실질 배제 수준까지 떨어진다.
    # [2026-08 버그수정, 신민용 리포트: "잉글랜드 같은 나라도 부족한 팀이
    # 나올 수 있는 거 아니냐"] OVR 격차만 보고 목적지를 고르면 스쿼드가
    # 이미 넘치는 팀도 계속 영입 후보가 되고, 이미 얇아진 팀은 계속
    # 배제될 이유가 없어서 순수 랜덤워크로 격차가 무한정 벌어졌다(40시즌
    # 시뮬레이션 실측: 같은 20팀 리그 안에서 6명~28명까지 벌어짐). 목적지
    # 팀의 현재 스쿼드 크기가 기준(_SQUAD_TARGET)보다 작을수록 가중치를
    # 올리고 클수록 내려서, 위 src 쪽 가중치와 함께 "커지면 팔고 작아지면
    # 사는" 복원력을 만든다. 나눔값(2.0)은 여러 배율(2/3/5/8/12)로 40~60
    # 시즌씩 돌려 비교한 값 — 12는 여전히 대부분 시즌에 어느 팀이 15명
    # 밑으로 떨어졌고, 2 정도로 좁혀야(위 src 쪽 제곱 가중치와 함께)
    # 20팀 리그 기준 60시즌 중 1~7번 수준으로 "정말 드문 예외"가 된다
    # (여러 시드·8팀 소규모 리그로도 재확인).
    # [2026-08 재수정] _SQUAD_TARGET이 18→23으로 오르면서 이 계수(2.0)도
    # 다시 튜닝 — 0.15로 훨씬 좁혀야(위 src 지수도 5로 강화) 새 정상범위
    # (22~25)에서 비슷한 수준의 안정성이 나온다. 여러 시드·리그 크기로
    # 재검증했다.
    # [2026-08 강화, 신민용 리포트: "OVR74인 37세 선수가 프리미어리그에
    # 있다가 1부/2부를 오가고, 전북현대(OVR 60후반~70대)에 OVR50짜리가
    # 뛰기도 한다 — 노련함으로 어느 정도는 인정해도 이건 너무 심하다"]
    # 분모 450(sigma≈15)은 격차 18~20에서도 가중치가 0.4~0.5로 여전히
    # 높게 남아, 이런 수준 미스매치가 드물지 않게 실제로 성사됐다.
    # 170(sigma≈9.2)으로 좁혀서 격차 10 안팎은 예전과 비슷하게 흔하되
    # (0.55 부근), 격차 20 근처부터는 급격히 희박해지게(0.09 부근) 만든다
    # — "가끔은 있어도 되지만 흔하면 안 된다"는 요청에 맞춘 튜닝.
    # [2026-08 최적화] 아래 루프가 이 함수 — 나아가 시즌 전환 전체 —
    # 에서 가장 뜨거운 지점이었다. cProfile 실측으로 math.exp가 시즌당
    # 5,771,032회 호출됐는데, 목적지 후보 하나당 2~3회씩 도는 게 원인이다
    # (특히 국제 이동(5%) 후보군은 한 번에 1,200팀 규모). 세 가지를 고친다:
    #
    #  (a) team_avg / dst_prestige_by_tid / dst_grade_by_tid는 _transfer_market이
    #      루프 시작 전에 한 번 만든 뒤 끝까지 바뀌지 않는다 — 그래서
    #      "이 후보 풀의 팀별 (평균OVR, sigma 분모, SS/S 여부)"도 불변이다.
    #      풀 리스트 객체 단위로 이 배열들을 한 번만 만들어 캐시하면
    #      (pool_cache) 이후 호출에서는 dict 조회 자체가 사라진다.
    #  (b) size_w = exp(-(스쿼드인원 - _SQUAD_TARGET)/0.15)는 인원(정수)만의
    #      순수 함수라 값을 메모이즈할 수 있다(_size_weight).
    #  (c) 나이 페널티는 후보마다 값이 똑같은데 루프 안에서 매번 다시
    #      계산하고 있었다 — 루프 밖으로 한 번만 끌어올린다.
    #
    # 계산식·상수·후보 순서는 전혀 건드리지 않았으므로 가중치 값도,
    # random.choices가 뽑는 결과도 예전과 완전히 동일하다.
    _meta = pool_cache.get(id(dst_pool_tids)) if pool_cache is not None else None
    if _meta is None or _meta[0] is not dst_pool_tids:
        _avgs = [team_avg.get(t, 50) for t in dst_pool_tids]
        if dst_prestige_by_tid:
            _dens = []
            for t in dst_pool_tids:
                _p = dst_prestige_by_tid.get(t, 0)
                _dens.append(35.0 if _p >= 3 else (60.0 if _p >= 2 else 170.0))
        else:
            _dens = [170.0] * len(dst_pool_tids)
        _tops = ([(dst_grade_by_tid.get(t) in ("SS", "S")) for t in dst_pool_tids]
                 if dst_grade_by_tid is not None else None)
        _meta = (dst_pool_tids, _avgs, _dens, _tops)
        if pool_cache is not None:
            pool_cache[id(dst_pool_tids)] = _meta
        # 인원 가중치 표에 이 풀의 팀이 하나라도 빠져 있으면(= 선수 명단이
        # 아예 비어 있는 팀) 여기서 채워둔다. 예전 코드의
        # len(team_players.get(t, [])) → 0 과 같은 값이며, 풀마다 딱 한 번만
        # 돌기 때문에(위 캐시에 걸림) 후보 평가 루프에서는 조건 검사 없이
        # sw_by_tid[t] 한 번으로 끝낼 수 있다.
        if sw_by_tid is not None:
            _sw0 = _size_weight(0)
            for _t in dst_pool_tids:
                if _t not in sw_by_tid:
                    sw_by_tid[_t] = _sw0
    _, _avgs, _dens, _tops = _meta
    # 하위호환: sw_by_tid 없이 호출되는 옛 경로(_do_one_transfer)에서는
    # 예전과 똑같이 team_players에서 그때그때 만들어 쓴다.
    _sw_by_tid = sw_by_tid if sw_by_tid is not None else {
        t: _size_weight(len(team_players.get(t, ()))) for t in dst_pool_tids}

    # 나이 페널티(후보와 무관하게 mover 하나로 결정되는 상수) 선계산.
    _age_penalty = 1.0
    _apply_age_penalty = False
    if _tops is not None and mover["age"] >= 33:
        _apply_age_penalty = True
        _age_excess = mover["age"] - 32
        _legend_relief = max(0.0, (mover_ovr - 85) / 15.0)
        _denom = 18.0 + 40.0 * _legend_relief
        _age_penalty = math.exp(-(_age_excess * _age_excess) / _denom)

    # [2026-08 2차 최적화] 이 루프는 시즌 전환 전체에서 가장 많이 도는
    # 구간(시즌당 후보 평가 267만 회)이라 "한 번당 몇 나노초"가 그대로
    # 총 시간이 된다. 1차 최적화 뒤 프로파일에 남아 있던 세 가지를 없앤다:
    #  · _size_weight()를 후보마다 호출 — 값 자체는 이미 캐시돼 있었지만
    #    파이썬 함수 호출이 267만 번이라 그 오버헤드가 계산보다 더 컸다.
    #    호출자가 넘겨주는 _sw_by_tid(팀별 인원 가중치 표)에서 dict 조회
    #    한 번으로 끝낸다 — 이 표는 팀 인원이 실제로 바뀔 때(이적 1건당
    #    2팀)만 갱신하면 되므로, 시즌당 15만 회 갱신으로 267만 회의
    #    "dict.get + len + 함수호출"을 대체하는 셈이다.
    #  · enumerate + _avgs[_i] + _dens[_i] 인덱싱 → zip으로 한 번에 꺼낸다.
    #  · 나이 페널티가 없는 대다수 경우에도 후보마다 if를 두 번씩 확인 →
    #    적용 여부는 mover 하나로 정해지므로 루프를 두 갈래로 나눈다.
    # 아래 두 갈래 모두 예전과 같은 식을 같은 순서로 계산한다:
    #   ovr_w  : 명문팀(prestige_level>=2)일수록 좁은 격차만 허용하도록
    #            sigma 분모를 줄인 값(170 일반 / 60 레벨2 / 35 레벨3) —
    #            이 분모는 팀별로 불변이라 _dens에 미리 담아둔 것이다.
    #   size_w : 목표 인원(_SQUAD_TARGET)에서 멀어질수록 급감하는 가중치.
    #   나이   : 목적지가 SS/S(5대 리그급)면 33세부터 초과분의 제곱에
    #            비례해 감쇠(OVR 85 초과는 분모를 넓혀 소폭 완화) — 값이
    #            mover 하나로 정해지므로 루프 밖에서 이미 계산해뒀다.
    _exp = math.exp
    dst_candidates = []
    weights = []
    _wsum = 0.0
    _dc_append = dst_candidates.append
    _w_append = weights.append
    if _apply_age_penalty:
        for t, _avg, _den, _top in zip(dst_pool_tids, _avgs, _dens, _tops):
            if t == src:
                continue
            gap = _avg - mover_ovr
            w = _exp(-(gap * gap) / _den) * _sw_by_tid[t]
            if _top:
                w *= _age_penalty
            _dc_append(t)
            _w_append(w)
            _wsum += w
    else:
        for t, _avg, _den in zip(dst_pool_tids, _avgs, _dens):
            if t == src:
                continue
            gap = _avg - mover_ovr
            w = _exp(-(gap * gap) / _den) * _sw_by_tid[t]
            _dc_append(t)
            _w_append(w)
            _wsum += w
    if not dst_candidates:
        return None
    if _wsum <= 0:
        dst = random.choice(dst_candidates)
    else:
        dst = random.choices(dst_candidates, weights=weights, k=1)[0]

    dst_players = team_players.get(dst, [])
    same_pos = [p for p in dst_players if p["position"] == mover["position"]
                and (year - p["last_transfer_year"]) >= 1]

    # [2026-08 버그수정, 신민용 리포트: "상대팀에서 선수가 나가면 무조건
    # 그 팀에서 한 명이 우리 쪽으로 오는 식인데 현실은 이렇게 안
    # 진행된다"] 예전엔 목적지 팀에 같은 포지션 선수가 있기만 하면(대부분
    # 팀은 포지션마다 최소 1명은 있으므로 사실상 거의 항상) 그 선수를
    # 자동으로 맞바꿔 보냈다 — 모든 이적이 사실상 "선수 대 선수 맞트레이드"
    # 가 되어버리는 구조였다. 실제 축구는 이런 1:1 맞트레이드가 오히려
    # 드문 예외(주로 같은 리그 라이벌 팀끼리 필요에 의해 성사)이고,
    # 대부분은 이적료를 매개로 한 일방적 이동(우리는 내보내기만 하거나
    # 받기만 함)이다. 이제 같은 포지션 선수가 있어도 낮은 확률
    # (SWAP_DEAL_CHANCE)로만 실제 맞트레이드가 성사되고, 나머지는 전부
    # 일반적인 일방 이적으로 처리한다 — 스쿼드 인원수는 은퇴자 즉시 충원
    # (_retire_and_replace)과 전 세계 단위로 봤을 때의 유입/유출 균형으로
    # 자연히 맞춰지므로, 매 이적마다 억지로 1:1을 맞출 필요가 없다.
    SWAP_DEAL_CHANCE = 0.12
    if same_pos and random.random() < SWAP_DEAL_CHANCE:
        swap = random.choice(same_pos)
        # (new_tid, pid, old_tid)
        return [(dst, mover["id"], src), (src, swap["id"], dst)]
    else:
        return [(dst, mover["id"], src)]


# _do_one_transfer는 하위호환용 별칭 (외부에서 직접 호출하는 경우 대비)
def _do_one_transfer(c, tids, team_avg, year=None):
    """하위호환 래퍼. 신규 코드는 _do_one_transfer_cached 사용."""
    import time as _t
    if year is None:
        year = _t.gmtime().tm_year
    players_rows = c.execute(
        "SELECT id, team_id, position, ovr, contract_end_year, last_transfer_year "
        "FROM ai_players WHERE team_id IN ({})".format(
            ",".join("?" for _ in tids)), tids).fetchall()
    tp: dict = {}
    for r in players_rows:
        tp.setdefault(r["team_id"], []).append({
            "id": r["id"], "position": r["position"],
            "ovr": r["ovr"] if r["ovr"] is not None else 50,
            "contract_end_year": r["contract_end_year"] or 0,
            "last_transfer_year": r["last_transfer_year"] or 0,
        })
    return _do_one_transfer_cached(tids[0] if tids else None, tids, tp, team_avg, year)


# ─────────────────────────────────────────────
# 4.5. 스쿼드 인원수 보정 (2026-08 신설)
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# 4.5. 스쿼드 인원수 보정 (2026-08 신설)
# ─────────────────────────────────────────────
# [2026-08 재조정, 신민용 요청: "후보는 최소 GK2/DF3/MF3/FW3(11명)~최대
# GK2/DF4/MF4/FW4(14명)로 맞춰줘"] 주전 11 + 벤치 11~14 = 22~25가 이제
# "정상 스쿼드"이므로, 붕괴 복구용 안전망 임계값도 여기 맞춰 올린다.
# 초기 생성 기준(TEAM_POSITIONS)이 이제 팀마다 벤치 길이가 다른
# 가변값이라 그 길이를 그대로 기준(18)으로 못 쓰므로, 새 정상범위의
# 중간값을 직접 상수로 못박는다 — 이 관계는 database._build_squad_positions()
# (주전11+벤치11~14)와 항상 같이 맞춰서 조정해야 한다.
_SQUAD_TARGET   = 23   # 정상범위(22~25)의 중간값
_SQUAD_MIN      = 22   # 이 밑으로 떨어지면 유망주 영입 (주전11+벤치 최소11)
_SQUAD_MAX      = 25   # 이 위로 넘어가면 조기 은퇴 (주전11+벤치 최대14)


def _archive_forced_out_players(c, ids, year):
    """[2026-08 신설, 신민용 리포트: "이름 지어준 선수(따효니)가 갑자기
    화면(세계기록실 라인업 등)에서 '(공석)'으로 사라졌다"] 원인규명:
    _rebalance_squad_sizes(포지션 균형 조정)와 apply_squad_turnover_
    after_movement(승강 후 물갈이) 둘 다 스쿼드에서 밀려난 선수를
    DELETE FROM ai_players로 곧바로 지우는데, 정상 은퇴 경로
    (_retire_and_replace)와 달리 ai_players_retired 아카이브를 전혀
    안 남겼다 — ai_player_code(id)/이름(ai_player_custom_names)이
    가리킬 실제 행이 아예 없어져서, 이름을 지어준 선수라도 이후 모든
    조회 화면(세계 기록실 라인업/선수 검색 등)에서 완전히 자취를
    감춰버렸다(사용자 세이브 실측: 커스텀 이름 62명 중 5명, 전체로는
    사상 존재했던 730,011명 중 162,105명(22%)이 이 상태였음).

    두 함수 모두 실제 DELETE 직전에 이 함수를 호출해, 삭제될 선수의
    마지막 상태(이름/포지션/OVR/나이/국적/소속팀)를 _retire_and_replace
    와 똑같은 형태로 ai_players_retired에 먼저 남긴다 — 그 다음에야
    진짜 DELETE가 실행되므로, id가 조회 불가능해지는 순간 자체가
    생기지 않는다. ids는 이미 이 시점의 ai_players에 실존하는 행이라
    (아직 지우기 전이므로) 조회가 항상 성공한다."""
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    rows = c.execute(
        f"""SELECT id, name, position, ovr, age, nationality, team_id
            FROM ai_players WHERE id IN ({placeholders})""", ids).fetchall()
    if not rows:
        return
    team_ids = {r["team_id"] for r in rows if r["team_id"]}
    team_names = {}
    if team_ids:
        tph = ",".join("?" * len(team_ids))
        team_names = {r["id"]: r["name"] for r in c.execute(
            f"SELECT id, name FROM teams WHERE id IN ({tph})", list(team_ids)).fetchall()}
    archive_rows = [
        (r["id"], r["name"], r["position"], r["ovr"], r["age"], r["nationality"],
         r["team_id"], team_names.get(r["team_id"], ""), year)
        for r in rows
    ]
    c.executemany(
        """INSERT OR REPLACE INTO ai_players_retired
           (id, name, position, ovr, age, nationality, last_team_id,
            last_team_name, retirement_year)
           VALUES(?,?,?,?,?,?,?,?,?)""", archive_rows)


def _rebalance_squad_sizes(c, year):
    """[2026-08 신설, 신민용 리포트: "이적으로 인한 스쿼드 인원 불균형을
    보정하는 장치가 없다"] 은퇴 교체(_retire_and_replace)는 기존 행을
    그대로 재활용(UPDATE)할 뿐이라 팀별 인원수를 안 바꾼다 — 이적
    (_transfer_market)이 어느 팀엔 계속 순유입, 다른 팀엔 계속 순유출을
    만들면 그 격차가 시즌이 갈수록 그대로 누적된다. 매 시즌 이적 직후
    한 번, 전 세계 팀을 훑어 초기 생성 기준 인원(18명, TEAM_POSITIONS
    길이) 대비 너무 적거나 많은 팀만 되돌린다:
      - 부족(< _SQUAD_MIN): 그 팀 리그 등급/tier에 맞는 OVR 범위에서
        10대(16~19세) 유망주를 새로 영입(INSERT)해 채운다.
      - 과다(> _SQUAD_MAX): 자리를 못 구한(=OVR이 가장 낮은) 선수부터
        조기 은퇴 처리한다 — 신인 교체 없이 그냥 명단에서 빠진다
        (신민용 지적대로, 모든 선수가 30대까지 뛰는 게 아니라 20대에
        일찌감치 접는 선수도 실제로 있다는 점을 반영).
    반환: (topped_up, forced_out) — 영입/조기은퇴된 인원수."""
    from constants import (CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, SUB_ROLES,
                           get_country_league_grade, get_ovr_range, COUNTRY_LEAGUE_OVR_OVERRIDE)
    from database import _pick_nationality, get_foreign_quota_range
    from data.prestige_clubs import prestige_level as _rebal_prestige_level
    from database import _BENCH_GROUP_WEIGHTS, _BENCH_GROUP_POOLS
    from formation_logic import _pos_category
    _GROUP_KEY = {"GK": "GK", "DEF": "DF", "MID": "MF", "ATK": "FW"}

    team_rows = c.execute(
        """SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                  cn.name AS cname, cn.continent AS continent
           FROM teams t JOIN leagues l ON t.league_id=l.id
                        JOIN countries cn ON l.country_id=cn.id""").fetchall()
    team_info = {r["tid"]: (r["tier"] or 1, r["cname"], r["continent"] or "유럽", r["tname"])
                 for r in team_rows}

    counts: dict = {}
    for r in c.execute("SELECT team_id, COUNT(*) n FROM ai_players GROUP BY team_id").fetchall():
        counts[r["team_id"]] = r["n"]
    # [2026-08 신설, 신민용 리포트: "키퍼/수비수/미드필더/공격수 비율을
    # 맞춰뒀는데 안 따르는거 같다 — 내 팀 후보 14명 중 5명이 키퍼고
    # 수비수가 0명"] 원인은 AI 이적(_transfer_market)이 포지션을 전혀
    # 안 보고 OVR/나이/계약만으로 사고팔기 때문(내 팀 소속 AI 동료도
    # 예외 없음) — 아래 그룹별 스냅샷은 이 편향을 잡아내기 위한 자료.
    roster_by_team: dict = {}
    for r in c.execute("SELECT id, team_id, position, ovr FROM ai_players").fetchall():
        roster_by_team.setdefault(r["team_id"], []).append((r["id"], r["position"], r["ovr"]))

    name_cache = _build_name_cache(c)
    topped_up = 0
    forced_out = 0
    new_rows = []       # INSERT용
    delete_ids = []     # DELETE용

    for tid, (tier, cname, continent, tname) in team_info.items():
        n = counts.get(tid, 0)
        grade = get_country_league_grade(cname)
        bonus = round(CONTINENT_OVR_BONUS.get(continent, 0) + COUNTRY_OVR_ADJ.get(cname, 0))
        is_override = cname in COUNTRY_LEAGUE_OVR_OVERRIDE

        if n < _SQUAD_MIN:
            need = _SQUAD_MIN - n
            ovr_rng = get_ovr_range(grade, tier, cname)
            if ovr_rng:
                lo, hi = ovr_rng
                if not is_override:
                    lo, hi = lo + bonus, hi + bonus
            else:
                lo, hi = 40, 55
            _plvl = _rebal_prestige_level(cname, tname)
            used = set()
            _q_lo, quota = get_foreign_quota_range(cname, continent)
            foreign_ct = 0
            for _ in range(need):
                # [2026-08 버그수정, 신민용 리포트: "지금 팀 후보 포지션
                # 비율이 이상하게 됐다(키퍼 3, 수비 3, 미드 2, 공격 5)"]
                # 예전엔 여기서 TEAM_POSITIONS(주전11+옛 고정벤치12 통짜
                # 리스트)를 균등 추첨했는데, 이 리스트의 그룹 비중(GK≈13%
                # /DF≈35%/MF≈26%/FW≈26%)이 database._build_squad_positions
                # (팀 최초 생성)가 목표로 하는 벤치 비율(GK 5~10%/DF
                # 30~35%/MF 35~40%/FW 20~25%)과 전혀 달랐다 — 이적으로
                # 얇아진 팀을 매 시즌 이 함수로 보충할 때마다 그 낡은
                # 비중 쪽으로 스쿼드가 계속 다시 끌려가, 수십 시즌이
                # 지나면 처음 생성 비율이 완전히 무너져 있었다. 이제 최초
                # 생성과 똑같은 roll_bench_position()을 써서 두 경로가
                # 항상 같은 목표 비율로 수렴하게 한다.
                pos = roll_bench_position()
                target = random.randint(lo, max(lo, (lo + hi) // 2))
                age = random.randint(*_AI_NEWBIE_AGE)
                # [2026-08 버그수정, _youth_target_scale 주석 참고] 이 경로도
                # 신인 생성인데 나이 스케일링이 빠져 있었다 — _retire_and_replace와
                # 동일하게 나이를 먼저 뽑아 target에 반영한다.
                _scaled = _youth_target_scale(target, age)
                # [2026-08 재설계 — _retire_and_replace와 동일한
                # Prestige×리그등급 표.]
                if ovr_rng:
                    _prestige_base = {3: 1, 2: 2, 1: 3}.get(_plvl, 4)
                    _grade_adj = {"SS": 0, "S": 0, "A": 0, "B": 1, "C": 1,
                                 "D": 2, "E": 2, "F": 3}.get(grade, 2)
                    _young_floor_off = _prestige_base + _grade_adj
                    _scaled = max(_scaled, ovr_rng[0] - _young_floor_off)
                stats = _gen_stats(pos, _scaled)
                ovr = calc_ovr(pos, stats)
                sub_role = random.choice(SUB_ROLES.get(pos, ["기본"]))
                nat, foreign_ct = _pick_nationality(cname, continent, grade, pos,
                                                    False, foreign_ct, quota)
                name = _random_name(c, tid, name_cache, used_in_team=used)
                new_rows.append((tid, name, pos,
                    stats["stamina"], stats["speed"], stats["jump"], stats["strength"],
                    stats["shooting"], stats["passing"], stats["dribbling"],
                    stats["tackling"], stats["heading"], stats["positioning"],
                    stats["setpiece"], stats["mental"], stats["confidence"],
                    stats["leadership"], stats["concentration"], ovr, age, sub_role, nat,
                    year + random.randint(2, 4), 0, year))
                topped_up += 1

        elif n > _SQUAD_MAX:
            excess = n - _SQUAD_MAX
            # [2026-08 신설, 신민용 요청: "강제 조기은퇴도 이적 가드와
            # 같은 문제(마지막 GK/DF 등이 최저OVR이면 그냥 잘려서 그
            # 그룹이 0명이 됨)를 가진다 — 최저OVR 우선순위는 그대로
            # 두고, '이 선수를 자르면 그 포지션 그룹이 0명이 되는가'만
            # 추가로 걸러라"] 위 이적 가드(_do_one_transfer_cached)와
            # 완전히 동일한 원칙: 정렬 기준(최저 OVR 우선)은 손대지
            # 않고, 후보 목록에서 "그 그룹의 마지막 1명"만 건너뛴다.
            # roster_by_team은 이 함수 진입 시점(=은퇴/이적이 이미 끝난
            # 뒤) 1회 조회한 스냅샷이라 지금 이 팀의 실제 구성과 일치한다.
            roster = roster_by_team.get(tid, [])
            _grp_count_max: dict = {}
            for _pid, _ppos, _povr in roster:
                _g = _GROUP_KEY.get(_pos_category(_ppos), "MF")
                _grp_count_max[_g] = _grp_count_max.get(_g, 0) + 1
            _protected_max = {_pid for _pid, _ppos, _povr in roster
                              if _grp_count_max.get(_GROUP_KEY.get(_pos_category(_ppos), "MF"), 0) <= 1}
            _candidates = sorted(roster, key=lambda t: t[2])  # 기존과 동일: OVR 오름차순
            picks = [_pid for _pid, _ppos, _povr in _candidates if _pid not in _protected_max][:excess]
            # (극단적 예외) 보호 대상을 뺀 후보만으론 목표 감축분을 못
            # 채우면(팀 전체가 그룹당 1명씩에 가까운 경우) 나머지는 기존
            # 방식대로 보호 대상에서도 채운다 — 스쿼드가 영구히 과다한
            # 상태로 남는 것보다는 이 편이 낫다(신민용 원안의 "매우 드문
            # 극단 예외" 취급과 동일한 원칙).
            if len(picks) < excess:
                _picked = set(picks)
                _rest = [_pid for _pid, _ppos, _povr in _candidates if _pid not in _picked]
                picks.extend(_rest[:excess - len(picks)])
            delete_ids.extend(picks)
            forced_out += len(picks)

        else:
            # [2026-08 신설] 총원은 22~25 정상범위라서 위 두 분기 다
            # 발동을 안 하는 팀들 — 그런데 총원이 정상이어도 그 안의
            # 포지션 그룹 구성비는 이적 편향으로 심하게 틀어져 있을 수
            # 있다(신민용 리포트 사례: 25명인데 GK 5/DF 0). 여기서는
            # 총원을 그대로 유지한 채(스왑: 가장 넘치는 그룹 최저OVR
            # 1명을 빼고 가장 부족한 그룹에 1명을 채움) _BENCH_GROUP_
            # WEIGHTS(위 database.py의 벤치 목표 비율과 동일 기준) 대비
            # "명백히 비정상"인 선(0명이거나 기대치의 40% 미만 = 부족,
            # 기대치의 2.2배 이상 = 과다)에서만 발동해서, 정상적인
            # 통계적 편차까지 억지로 깎아내리진 않는다.
            roster = roster_by_team.get(tid, [])
            if roster:
                group_players: dict = {"GK": [], "DF": [], "MF": [], "FW": []}
                for pid, ppos, povr in roster:
                    grp = _GROUP_KEY.get(_pos_category(ppos), "MF")
                    group_players[grp].append((pid, povr))
                total_n = len(roster)
                deficient, surplus = [], []
                for grp, w in _BENCH_GROUP_WEIGHTS:
                    expected = total_n * (w / 100.0)
                    actual = len(group_players[grp])
                    if actual == 0 or actual < expected * 0.4:
                        deficient.append(grp)
                    elif actual > max(expected * 2.2, expected + 3):
                        surplus.append((grp, actual - expected))
                if deficient and surplus:
                    surplus.sort(key=lambda x: -x[1])
                    ovr_rng = get_ovr_range(grade, tier, cname)
                    if ovr_rng:
                        _lo, _hi = ovr_rng
                        if not is_override:
                            _lo, _hi = _lo + bonus, _hi + bonus
                    else:
                        _lo, _hi = 40, 55
                    _plvl = _rebal_prestige_level(cname, tname)
                    _used = set()
                    _q_lo, _quota = get_foreign_quota_range(cname, continent)
                    _foreign_ct = 0
                    for si, grp in enumerate(deficient):
                        if si >= len(surplus):
                            break
                        sgrp, _ = surplus[si]
                        weakest = min(group_players[sgrp], key=lambda t: t[1])
                        delete_ids.append(weakest[0])
                        group_players[sgrp].remove(weakest)
                        _pos = random.choice(_BENCH_GROUP_POOLS[grp])
                        _target = random.randint(_lo, max(_lo, (_lo + _hi) // 2))
                        _age = random.randint(*_AI_NEWBIE_AGE)
                        _scaled = _youth_target_scale(_target, _age)
                        if ovr_rng:
                            _prestige_base = {3: 1, 2: 2, 1: 3}.get(_plvl, 4)
                            _grade_adj = {"SS": 0, "S": 0, "A": 0, "B": 1, "C": 1,
                                         "D": 2, "E": 2, "F": 3}.get(grade, 2)
                            _young_floor_off = _prestige_base + _grade_adj
                            _scaled = max(_scaled, ovr_rng[0] - _young_floor_off)
                        _stats = _gen_stats(_pos, _scaled)
                        _ovr = calc_ovr(_pos, _stats)
                        _sub_role = random.choice(SUB_ROLES.get(_pos, ["기본"]))
                        _nat, _foreign_ct = _pick_nationality(cname, continent, grade, _pos,
                                                              False, _foreign_ct, _quota)
                        _name = _random_name(c, tid, name_cache, used_in_team=_used)
                        new_rows.append((tid, _name, _pos,
                            _stats["stamina"], _stats["speed"], _stats["jump"], _stats["strength"],
                            _stats["shooting"], _stats["passing"], _stats["dribbling"],
                            _stats["tackling"], _stats["heading"], _stats["positioning"],
                            _stats["setpiece"], _stats["mental"], _stats["confidence"],
                            _stats["leadership"], _stats["concentration"], _ovr, _age, _sub_role, _nat,
                            year + random.randint(2, 4), 0, year))
                        topped_up += 1
                        forced_out += 1

    if new_rows:
        c.executemany("""INSERT INTO ai_players
            (team_id,name,position,stamina,speed,jump,strength,shooting,passing,
             dribbling,tackling,heading,positioning,setpiece,
             mental,confidence,leadership,concentration,ovr,age,sub_role,nationality,
             contract_end_year,last_transfer_year,created_year)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new_rows)
    if delete_ids:
        _archive_forced_out_players(c, delete_ids, year)
        c.executemany("DELETE FROM ai_players WHERE id=?", [(i,) for i in delete_ids])

    return topped_up, forced_out


# ─────────────────────────────────────────────
# 5. 포메이션 변경 (감독 교체 컨셉)
# ─────────────────────────────────────────────
def _snapshot_season_positions(c, year, only_missing=False, rows=None):
    """[2026-08 신설, 신민용 요청: "이 시즌에 얘가 어디 포지션을 갔는지가
    중요한거야 — 위(선수 검색 맨 위 요약행)는 주포라 안 변하는 게
    맞는데, 연도별 기록엔 그 시즌 실제로 어느 자리서 뛰었는지가 있어야
    한다"] 등록 포지션(ai_players.position, 안 바뀌는 "주포")과 별개로,
    이 시즌 각 팀의 실제 포메이션(teams.formation)에 로스터를 채워 넣었을
    때 이 선수가 어느 슬롯을 맡는지를 매 시즌 스냅샷으로 남긴다.

    화면(포메이션 탭)에 뜨는 것과 다른 알고리즘을 쓰면 "선수 검색은
    CB라는데 포메이션 화면은 LB"처럼 또 다른 불일치가 생기므로,
    formation_logic._greedy_fill_slots(여러 후보를 슬롯에 배정하는 바로
    그 함수 — ui/formation_widget.py도 동일 모듈에서 가져다 쓴다)를
    그대로 재사용한다 — OVR 상위 11명(베스트 XI)에 든 선수는 그 슬롯
    포지션을, 나머지(후보) 선수는 등록 포지션 그대로 기록한다(이
    게임엔 후보용 별도 포메이션 개념이 없으므로).

    [주의] ui.formation_widget에서 직접 import하지 않는다 — 그 모듈은
    PyQt6을 import하므로, headless_runner.py 등 PyQt6 없는 헤드리스
    환경에서 ai_lifecycle.py를 그냥 import하는 것만으로 죽는다.
    formation_logic.py(Qt 의존성 없는 순수 로직 전용)에서 가져온다.

    ai_player_ovr_history와 완전히 같은 타이밍(매 시즌 전환)에 호출된다.
    [한계] 이 기능 신설 이전 과거 시즌엔 소급 적용이 안 된다 — 그 이전
    연도는 세계 브라우저 쪽에서 이적 시점 등록 포지션으로 대체 표시한다."""
    from formation_logic import _greedy_fill_slots, compute_squad_roles
    from constants import FORMATION_SLOTS
    import json

    # [2026-08 확장, 신민용 요청: "그 해 주전/로테이션/대기/유망주였는지도
    # 연도별로 표시"] 역할 계산(formation_logic.compute_squad_roles)이
    # 나이도 필요해서 age를 같이 뽑는다 — 이 함수가 이미 팀별 로스터
    # 전체를 훑고 있으므로(베스트XI 슬롯 배정용) 추가 쿼리 없이 그대로
    # 재사용한다.
    # [2026-08 신설, 신민용 리포트: "은퇴 선수의 마지막 시즌 역할이 -로
    # 뜬다"] 이 함수는 원래 시즌 전환의 맨 끝(은퇴·이적·포메이션 변경이
    # 전부 끝난 뒤)에 딱 한 번만 돌았다 — 그런데 그 시점엔 이번 시즌을
    # 마지막으로 은퇴한 선수가 ai_players에서 이미 삭제된 뒤라, 그
    # 선수의 마지막 시즌만 이 표에 행이 아예 안 생겼다(그래서 화면에
    # 역할이 "-"로 떴다. OVR/포지션은 ai_player_ovr_history 쪽에서
    # 나오므로 그 줄 자체는 정상적으로 보였고 역할 칸만 비었던 것).
    # 이제 두 번 나눠 부른다:
    #   1) 은퇴·이적 처리 "전"에 한 번 (only_missing=False) — 이번 시즌을
    #      실제로 뛴 로스터 그대로가 남는다. 은퇴자도 아직 살아 있고,
    #      오프시즌 이적자도 아직 옛 팀 소속이라 연도 귀속이 정확해진다.
    #   2) 전부 끝난 뒤 한 번 더 (only_missing=True) — 이번 오프시즌에
    #      새로 생긴 선수(은퇴 대체 신인 등)만 채운다. 이 선수들은
    #      ai_player_ovr_history에도 이번 해로 기록되므로(데뷔연도
    #      archive), 여기서도 같이 채워야 화면에 역할 칸만 비지 않는다.
    # only_missing=True일 땐 아직 이 해 행이 없는 선수가 있는 팀만
    # 훑는다 — 역할(팀 내 OVR 순위)은 그 팀 로스터 전체가 있어야
    # 계산되므로 팀 단위로 가져오되, 실제로 저장하는 건 빠져 있던
    # 선수 행뿐이라 이미 1)에서 기록된 값은 절대 덮어쓰지 않는다.
    _missing_ids = None
    if only_missing:
        _missing = c.execute(
            """SELECT ap.id, ap.team_id FROM ai_players ap
               WHERE ap.team_id IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM hist.ai_player_position_history h
                                 WHERE h.player_id = ap.id AND h.year = ?)""",
            (year,)).fetchall()
        if not _missing:
            return
        _missing_ids = {r[0] for r in _missing}
        # 대상 팀만 골라 오되, 팀 id를 SQL 문자열에 몇천 개씩 나열하면
        # (IN (...)) 그 구문을 만들고 파싱하는 것만으로도 느려진다 —
        # 임시표에 넣고 JOIN으로 좁힌다. 임시표는 이 연결에서만 보이며
        # 끝나고 바로 지운다.
        c.execute("DROP TABLE IF EXISTS temp._snap_target_teams")
        c.execute("CREATE TEMP TABLE _snap_target_teams(team_id INTEGER PRIMARY KEY)")
        c.executemany("INSERT OR IGNORE INTO temp._snap_target_teams(team_id) VALUES(?)",
                      [(r[1],) for r in _missing])
        rows = c.execute(
            """SELECT ap.id AS id, ap.team_id AS team_id, ap.position AS position,
                      ap.ovr AS ovr, ap.age AS age, t.formation AS formation
               FROM ai_players ap
               JOIN temp._snap_target_teams st ON st.team_id = ap.team_id
               JOIN teams t ON ap.team_id = t.id""").fetchall()
        c.execute("DROP TABLE IF EXISTS temp._snap_target_teams")
    elif rows is None:
        rows = c.execute(
            """SELECT ap.id AS id, ap.team_id AS team_id, ap.position AS position,
                      ap.ovr AS ovr, ap.age AS age, t.formation AS formation
               FROM ai_players ap JOIN teams t ON ap.team_id = t.id
               WHERE ap.team_id IS NOT NULL""").fetchall()
    if not rows:
        return

    # [2026-08 최적화] 호출부가 이미 떠 놓은 선수 목록(rows)을 넘겨주면
    # 26만 행을 다시 JOIN해서 읽지 않는다 — 대신 그 목록엔 팀 포메이션이
    # 없으므로 teams(1만여 행, 훨씬 쌈)만 따로 읽어 팀→포메이션 표를
    # 만들어 쓴다. 결과는 JOIN해서 읽었을 때와 동일.
    _form_by_team = None
    if not only_missing and "formation" not in rows[0].keys():
        _form_by_team = {r[0]: r[1] for r in
                         c.execute("SELECT id, formation FROM teams").fetchall()}

    by_team = {}
    for r in rows:
        _tid = r["team_id"]
        if _tid is None:
            continue   # 무소속(rows를 넘겨받은 경로엔 섞여 있을 수 있음)
        by_team.setdefault(_tid, []).append(r)

    inserts = []
    # [2026-08 신설, 신민용 요청: "팀 검색에서 연도를 클릭하면 그 해
    # 포메이션이 떠야 한다"] 아래 루프가 팀마다 어차피 계산하는 placed
    # (슬롯별 베스트11 배정)를 선수 단위(inserts)로 흩어 담기 직전에,
    # 팀 단위로도 그대로 한 벌 더 챙겨둔다 — 새 연산이 아니라 이미 계산된
    # 결과를 한 번 더 저장하는 것뿐이라 비용이 거의 없다. only_missing=True
    # 두 번째 패스(오프시즌 최종 로스터 기준)에서도 다시 채워지는데,
    # INSERT OR REPLACE라 나중 값(더 확정된 최종 로스터)이 이긴다 —
    # 오히려 더 정확해지므로 굳이 이 패스를 걸러낼 필요가 없다.
    team_inserts = []
    for _team_id, players in by_team.items():
        if _form_by_team is not None:
            formation = _form_by_team.get(_team_id) or "4-4-2"
        else:
            formation = players[0]["formation"] or "4-4-2"
        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        candidates = [{"id": p["id"], "position": p["position"], "ovr": p["ovr"] or 0}
                      for p in players]
        placed = _greedy_fill_slots(candidates, slots)
        roles = compute_squad_roles([(p["id"], p["ovr"], p["age"]) for p in players])
        started_ids = set()
        for slot_idx, pl in enumerate(placed):
            if pl is None:
                continue
            inserts.append((pl["id"], year, slots[slot_idx], roles.get(pl["id"], "")))
            started_ids.add(pl["id"])
        for p in players:
            if p["id"] not in started_ids:
                inserts.append((p["id"], year, p["position"] or "", roles.get(p["id"], "")))
        slots_payload = [{"slot": slots[i], "id": (pl["id"] if pl else None)}
                          for i, pl in enumerate(placed)]
        # [2026-08 신설, 신민용 리포트: "팀도 주전 후보가 있는데 왜 안떠?"]
        # 포메이션 11자리에 못 들어간 나머지 로스터(=후보)도 OVR 내림차순으로
        # 같이 저장해둔다 — 국가대표 스쿼드 화면(get_country_tournament_squad)의
        # 주전/후보 패턴과 동일하게 맞추기 위함. 새 연산 없이 이미 위에서 구한
        # started_ids/players를 그대로 재사용.
        bench_payload = [{"id": p["id"], "position": p["position"] or ""}
                          for p in sorted(
                              (p for p in players if p["id"] not in started_ids),
                              key=lambda p: -(p["ovr"] or 0))]
        team_inserts.append((_team_id, year, formation,
                              json.dumps(slots_payload), json.dumps(bench_payload)))

    if _missing_ids is not None:
        # 팀 로스터 전체로 슬롯·역할을 계산했지만, 실제로 저장하는 건
        # 이 해 행이 없던 선수(이번 오프시즌 신규 생성)뿐이다.
        inserts = [t for t in inserts if t[0] in _missing_ids]

    if team_inserts:
        c.executemany(
            "INSERT OR REPLACE INTO hist.team_season_lineup"
            "(team_id, year, formation, slots_json, bench_json) "
            "VALUES (?,?,?,?,?)", team_inserts)

    if inserts:
        # [2026-08 최적화] player_id 순으로 정렬해서 넣는다. 이 표의 기본키는
        # (player_id, year)이고 WITHOUT ROWID라 키 순서가 곧 저장 순서인데,
        # 위 루프는 "팀별"로 돌기 때문에 player_id가 뒤죽박죽인 채로 26만 건이
        # 들어갔다 — B-tree 입장에서는 매번 다른 페이지를 열어 중간에 끼워넣는
        # 셈이라 페이지 분할이 계속 일어난다. 키 순으로 넣으면 뒤쪽에 차곡차곡
        # 붙기만 하면 된다. 정렬은 안정 정렬이고 (player_id, year)가 이 목록
        # 안에서 유일하므로(선수 한 명당 이 해에 한 행) 저장 결과는 완전히 동일.
        inserts.sort(key=_ins_key)
        c.executemany(
            "INSERT OR REPLACE INTO hist.ai_player_position_history(player_id, year, position, role) "
            "VALUES (?,?,?,?)", inserts)


def seed_initial_position_history(year):
    """[2026-08 신설] seed_initial_ovr_history(database.py)와 같은 이유 —
    시즌 전환이 한 번도 없었던 세이브 첫 해는 _snapshot_season_positions을
    부를 계기가 없어 영구히 빈칸이 된다. 캐릭터 생성 직후(game_engine.py가
    seed_initial_ovr_history 바로 다음 자리에서 호출) 한 번 아카이브해서
    첫 해부터 정확하게 남긴다. formation_widget 의존성 때문에 database.py가
    아니라 여기(ai_lifecycle.py)에 둔다."""
    conn = get_conn()
    c = conn.cursor()
    _snapshot_season_positions(c, year)
    conn.commit()
    conn.close()


def snapshot_my_player_position(year):
    """[2026-08 신설, 신민용 요청: "세계 축구 기록실 선수 검색에서 AI는
    연도별 주전/로테이션/대기/유망주가 뜨는데 나(my_player)는 안 뜬다"]
    _snapshot_season_positions()는 ai_players만 훑고 my_player는 대상이
    아니라서 생긴 공백을 메운다.

    [설계 — 신민용+GPT 검토] _snapshot_season_positions() 자체(전세계
    ai_players 26만 건을 매 시즌 훑는 무거운 함수)를 고쳐서 my_player를
    끼워 넣는 대신, my_player가 소속된 팀 하나만 targeted 조회하는 별도
    함수로 분리했다 — 이유는 두 가지. (1) 성능: 이미 O(전세계)인 그
    함수에 로직을 더 얹기보다, my_player 소속팀 로스터(팀당 20명대)만
    보는 이 함수가 훨씬 싸다. (2) 안전성: 매 시즌 전체 AI 이력을 쌓는
    핵심 공용 함수를 건드리면 실수 시 파급 범위가 전세계 선수단이라
    커진다 — my_player 전용 로직을 완전히 분리해두면 이 기능 하나만
    독립적으로 검증·롤백할 수 있다.

    베스트11 배정은 formation_logic._greedy_fill_slots/compute_squad_roles
    를 그대로 재사용해 _snapshot_season_positions와 동일한 알고리즘으로
    맞춘다(그래야 "포메이션 화면은 주전인데 선수 검색은 후보"같은 또
    다른 불일치가 안 생김). my_player의 id는 ai_players.id와 값이
    겹칠 수 있으므로("__ME__" 같은 문자열 sentinel을 써서) 이 함수
    안에서만 쓰고 절대 저장하지 않는다 — 저장은 my_player_position_
    history(year 단일 PK, player_id 없음)에 한다.

    소속팀이 없으면(무소속) 그 해는 기록하지 않는다 — AI가 방출/은퇴로
    한 해 team_id가 없으면 그 해 role_checkpoints에 값이 없는 것과
    동일한 동작."""
    from formation_logic import _greedy_fill_slots, compute_squad_roles
    from constants import FORMATION_SLOTS

    conn = get_conn()
    c = conn.cursor()
    try:
        me = c.execute(
            "SELECT current_team_id, position, ovr, age FROM my_player WHERE id=1").fetchone()
        if not me or not me["current_team_id"]:
            return
        team_id = me["current_team_id"]
        team_row = c.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
        if not team_row:
            return
        formation = team_row["formation"] or "4-4-2"
        teammates = c.execute(
            "SELECT id, position, ovr, age FROM ai_players WHERE team_id=?", (team_id,)).fetchall()

        ME = "__ME__"
        candidates = [{"id": r["id"], "position": r["position"], "ovr": r["ovr"] or 0}
                      for r in teammates]
        candidates.append({"id": ME, "position": me["position"], "ovr": me["ovr"] or 0})
        pool = [(r["id"], r["ovr"], r["age"]) for r in teammates]
        pool.append((ME, me["ovr"], me["age"]))

        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        placed = _greedy_fill_slots(candidates, slots)
        roles = compute_squad_roles(pool)

        my_position = me["position"] or ""
        for slot_idx, pl in enumerate(placed):
            if pl is not None and pl["id"] == ME:
                my_position = slots[slot_idx]
                break
        my_role = roles.get(ME, "")

        c.execute(
            "INSERT OR REPLACE INTO my_player_position_history(year, position, role) VALUES (?,?,?)",
            (year, my_position, my_role))
        conn.commit()
    finally:
        conn.close()


def _shuffle_formations(c):
    """일부 팀의 포메이션 변경. 시즌마다 ~20% 팀이 전술 교체.
    [최적화] executemany로 일괄 UPDATE."""
    changed = 0
    teams = c.execute("SELECT id, formation FROM teams").fetchall()
    updates = []
    for t in teams:
        if random.random() < 0.20:
            new_f = random.choice([f for f in _FORMATIONS if f != t["formation"]])
            updates.append((new_f, t["id"]))
            changed += 1
    if updates:
        c.executemany("UPDATE teams SET formation=? WHERE id=?", updates)
    return changed


# ─────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────
def _gen_stats(pos, target):
    """database._gen_ai_stats 재사용 (목표 OVR→스탯 역산)."""
    try:
        from database import _gen_ai_stats
        return _gen_ai_stats(pos, target)
    except Exception:
        keys = KEY_STATS_BY_POS.get(pos, ALL_STATS[:5])
        stats = {}
        for s in ALL_STATS:
            base = target + (3 if s in keys else -3)
            stats[s] = min(99, max(15, int(round(random.gauss(base, 4)))))
        return stats


def _build_name_cache(c):
    """국가별 이름풀 전체를 1회 로드 → {country_id: [name, ...]}
    _retire_and_replace에서 한 번 호출 후 재사용. ORDER BY RANDOM() 완전 제거."""
    rows = c.execute("SELECT country_id, name FROM player_names").fetchall()
    cache: dict = {}
    for r in rows:
        cache.setdefault(r["country_id"], []).append(r["name"])
    return cache


# 팀→국가 매핑 캐시 (오프시즌 내 반복 JOIN 방지)
_team_country_cache: dict = {}


def _get_team_country(c, team_id):
    """팀 ID → country_id. 한 번 조회 후 모듈 캐시에 저장."""
    if team_id not in _team_country_cache:
        row = c.execute(
            """SELECT cn.id AS cid FROM teams t
               JOIN leagues l ON t.league_id=l.id
               JOIN countries cn ON l.country_id=cn.id
               WHERE t.id=?""", (team_id,)).fetchone()
        _team_country_cache[team_id] = row["cid"] if row else None
    return _team_country_cache[team_id]


def _random_name(c, team_id, name_cache=None, used_in_team=None):
    """팀 소속국 이름풀에서 랜덤 이름. 같은 팀 내 중복 방지.
    used_in_team: set — 이번 오프시즌에 이미 이 팀에 배정된 이름들.
    다른 팀/리그 동명이인은 허용 (현실적으로 전 세계에 동명이인 있음).
    """
    cid = _get_team_country(c, team_id)
    if cid is not None:
        pool = None
        if name_cache is not None:
            pool = name_cache.get(cid, [])
        else:
            rows = c.execute(
                "SELECT name FROM player_names WHERE country_id=?", (cid,)).fetchall()
            pool = [r["name"] for r in rows]

        if pool:
            if used_in_team:
                # 팀 내 중복 회피: 사용 안 된 이름 우선
                available = [n for n in pool if n not in used_in_team]
                if available:
                    chosen = random.choice(available)
                else:
                    # 이름풀 소진 시 어쩔 수 없이 중복 허용
                    chosen = random.choice(pool)
            else:
                chosen = random.choice(pool)
            if used_in_team is not None:
                used_in_team.add(chosen)
            return chosen
    return f"신인{random.randint(100, 999)}"


# ─────────────────────────────────────────────
# 6. 승격/강등 직후 스쿼드 개편 (일부 방출+영입)
# ─────────────────────────────────────────────
def apply_squad_turnover_after_movement(rescale_jobs, year, turnover_frac=0.25,
                                         release_frac_of_turnover=0.35):
    """[2026-08 신설, 신민용 리포트: "30년 정도 돌리면 1부가 5부로, 5부가
    1부로 가는 경우가 아예 적지는 않다 — 승격/강등하면 팀 개편(방출 포함)이
    크게 일어나는 거 맞냐"] 확인 결과 답은 "아니오"였다 — game_engine.
    _process_promotion_relegation이 승강 직후 부르는 rescale_team_to_target_
    ovr()/rescale_teams_to_target_ovr_batch()는 스쿼드 전원의 스탯에 "같은
    델타"를 더하는 평행이동만 한다(선수 구성·개인별 순위는 전혀 안 바뀜).
    그래서 몇 단계를 한꺼번에 뛰어넘는 승격/강등이 반복돼도 스쿼드는 계속
    같은 선수들이 이름만 유지한 채 통째로 오르내릴 뿐, "이 정도로 급격히
    수준이 바뀌면 스쿼드도 크게 갈아엎힌다"는 현실감이 빠져 있었다.

    이 함수는 리스케일 직후(game_engine._process_promotion_relegation이
    rescale_teams_to_target_ovr_batch 호출 바로 뒤에 호출) 그 팀에서 OVR이
    가장 낮은 turnover_frac(기본 25%)만큼을 골라, 그 중 release_frac_of_
    turnover(기본 35%)는 신인 교체 없이 그냥 방출(삭제만 — 스쿼드가
    줄어들면 다음 시즌 _rebalance_squad_sizes가 자연스럽게 채운다, 이미
    있는 "자리 못 구한 선수 조기 은퇴" 경로와 동일한 원칙), 나머지는 새
    tier/등급 수준에 맞는 신규 선수로 즉시 교체(방출+영입)한다 — 스쿼드
    전체를 다 갈아엎지는 않는다(핵심 선수단은 유지, 하위권만 물갈이).

    rescale_jobs: [(team_id, target_ovr), ...] — game_engine이 이미 만들어둔
    _rescale_jobs를 그대로 재사용(팀별 새 목표 OVR을 다시 구할 필요 없음).
    반환: (replaced, released) 인원수."""
    from constants import (get_country_league_grade, CONTINENT_OVR_BONUS,
                           COUNTRY_OVR_ADJ, SUB_ROLES)
    from database import get_ovr_range, _pick_nationality, get_foreign_quota_range

    if not rescale_jobs:
        return 0, 0

    conn = get_conn()
    c = conn.cursor()

    team_ids = [j[0] for j in rescale_jobs]
    ph = ",".join("?" * len(team_ids))
    team_rows = {r["tid"]: r for r in c.execute(
        f"""SELECT t.id AS tid, t.current_tier AS tier, cn.name AS cname,
                   cn.continent AS continent
            FROM teams t JOIN leagues l ON t.league_id=l.id
                         JOIN countries cn ON l.country_id=cn.id
            WHERE t.id IN ({ph})""", team_ids).fetchall()}

    name_cache = _build_name_cache(c)
    replaced = 0
    released = 0
    del_ids = []
    new_rows = []

    for team_id, _target_ovr in rescale_jobs:
        info = team_rows.get(team_id)
        if not info:
            continue
        grade = get_country_league_grade(info["cname"])
        tier = info["tier"] or 1
        cname = info["cname"]
        continent = info["continent"] or "유럽"
        bonus = round(CONTINENT_OVR_BONUS.get(continent, 0) + COUNTRY_OVR_ADJ.get(cname, 0))
        rng = get_ovr_range(grade, tier, cname)
        if rng:
            lo, hi = rng[0] + bonus, rng[1] + bonus
        else:
            lo, hi = 40, 55

        squad = c.execute(
            "SELECT id, position FROM ai_players WHERE team_id=? ORDER BY ovr ASC",
            (team_id,)).fetchall()
        n = len(squad)
        if n < 2:
            continue
        n_turn = min(max(1, int(round(n * turnover_frac))), n - 1)
        n_release = max(0, min(n_turn, int(round(n_turn * release_frac_of_turnover))))
        used = set()
        _q_lo, quota = get_foreign_quota_range(cname, continent)
        foreign_ct = 0

        for i, pl in enumerate(squad[:n_turn]):
            del_ids.append(pl["id"])
            if i < n_release:
                released += 1
                continue
            pos = pl["position"]
            target = random.randint(lo, max(lo, (lo + hi) // 2))
            age = random.randint(*_AI_NEWBIE_AGE)
            stats = _gen_stats(pos, _youth_target_scale(target, age))
            ovr = calc_ovr(pos, stats)
            sub_role = random.choice(SUB_ROLES.get(pos, ["기본"]))
            nat, foreign_ct = _pick_nationality(cname, continent, grade, pos,
                                                False, foreign_ct, quota)
            name = _random_name(c, team_id, name_cache, used_in_team=used)
            new_rows.append((team_id, name, pos, *[stats[s] for s in ALL_STATS], ovr, age,
                              sub_role, nat, year + random.randint(2, 4), 0, year))
            replaced += 1

    if del_ids:
        _archive_forced_out_players(c, del_ids, year)
        c.executemany("DELETE FROM ai_players WHERE id=?", [(i,) for i in del_ids])
    if new_rows:
        c.executemany(
            f"""INSERT INTO ai_players
                (team_id,name,position,{_STAT_COLS},ovr,age,sub_role,nationality,
                 contract_end_year,last_transfer_year,created_year)
                VALUES(?,?,?,{','.join('?' for _ in ALL_STATS)},?,?,?,?,?,?,?)""",
            new_rows)
    conn.commit()
    return replaced, released