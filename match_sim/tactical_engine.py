# -*- coding: utf-8 -*-
"""match_sim/tactical_engine.py — 포메이션 매치업 기반 경기 결과 시뮬레이션.

[왜 필요한가]
기존 game_engine._gen_score()는 "홈-원정 OVR 차이 → 확률표 조회"로 스코어를
결정했다. 승/무/패 확률과 골 차이가 전부 OVR 차이 하나로만 정해지고, 그
경기에서 실제로 어느 구역을 누가 장악했는지, 포메이션끼리 어디서 수적/능력치
우위가 나는지는 전혀 반영되지 않았다 — "3-5-2가 중원에서 4-4-2를 5:4로
압도한다" 같은 전술적 사실이 결과에 개입할 여지가 구조적으로 없었다.

이 모듈은 그 자리를 대체한다. 실제로 피치를 3레인(좌/중/우) x 3서드(수비/
중원/공격)로 나누고, 각 팀의 포메이션이 그 구역에 배치하는 선수들의 실제
스탯(슈팅/패스/드리블/태클/포지셔닝 등)으로 "이 구역은 어느 팀이 우세한가"를
계산한 뒤, 그 우세를 따라 볼이 흘러가는 것을 분 단위(90분)로 시뮬레이션해서
슈팅/코너/파울/골이 그 결과로 자연스럽게 "발생"하게 만든다. 스코어와 팀
통계(슈팅/유효슈팅/코너/파울/점유율)는 사후에 역산되는 게 아니라 이
시뮬레이션의 직접적인 산출물이다.

[적용 범위 — 중요]
이건 사용자가 실제로 관전하는 "내 경기"(game_engine._simulate_match)에만
쓰인다. 리그의 나머지 수십~수백 경기(AI vs AI, _sim_all_ai_matches 등)는
이 정밀 시뮬레이션을 돌릴 필요가 없고(성능 낭비 + 안 보는 경기라 의미도
없음), 기존 OVR 차이 기반 확률표(_match_win_probs/_gen_score)를 그대로
쓴다 — 이 모듈은 그 함수들을 건드리지 않는다.

[개인 서사와의 관계]
내 선수 개인의 골/도움/선방/평점(game_engine._player_perf)은 이 모듈이
건드리지 않는다. 그건 여전히 "확정된 팀 스코어에 맞춰 내 개인 기록을
그럴듯하게 만드는" 별개 로직이고, 이 모듈이 만든 스코어를 입력으로 그대로
받는다. 대신 이 모듈이 만든 팀 통계(슈팅 수 등)는 game_engine._derive_match_stats
에서 "내 개인 기록이 하한선"이라는 기존 원칙과 합쳐져 최종 팀 통계가 된다
(engine이 만든 진짜 값을 기준점으로 쓰되, 내 개인 슈팅이 그보다 많으면
그쪽을 존중 — 모순이 안 생기게).
"""
import math
import random

LANES = ("L", "C", "R")

# [2026-09 신설] 슈터 풀 확장 스위치. True면 "그 레인 최전방"만이 아니라
# 같은 레인 미드필더/옆 레인 공격수까지 포지션 배수와 함께 슈터 풀에 넣는다
# (_prep_side 주석 참고 — 예전엔 4-2-3-1처럼 최전방이 레인당 1명인
# 포메이션에서 팀 골의 97.6%가 한 선수에게 쏠렸다). False면 예전 동작을
# 그대로 재현한다 — A/B 계측(tools/scorer_spread_qa.py --legacy) 전용이며
# 실제 게임에서는 항상 True.
WIDE_SHOOTER_POOL = True

# ── [2026-09 신설] 경기 내부 체력 / 교체 / 연장전 ────────────────────
# 신민용 확정 설계(교체·연장전 설계 확정본):
#   · 체력은 "사용자가 보는 게이지"가 아니라 "AI가 왜 교체했는지를 정하는
#     변수"다 — 실시간 UI/모션은 전혀 필요 없고, 이 엔진의 1~96분 내부
#     루프에서만 쓰인다.
#   · 교체 가능 5명 / 교체 기회 3회, 연장 진입 시 +1회. 하프타임과 연장
#     하프타임은 기회를 소모하지 않는다(실제 규칙과 동일).
#   · 연장은 단판 KO에서만. 2차전 합산 방식은 "2차전 90분 종료 후 합산이
#     동률일 때만" 연장에 들어간다.
#   · 체력은 90분에 초기화되지 않고 연장에서도 계속 감소한다.
#
# [밸런스 원칙] 지금 득점 baseline은 체력 개념 없이 튜닝된 값이다. 그래서
# 체력이 경기력에 미치는 영향은 의도적으로 약하게 시작한다 —
# STAMINA_PERF_COEF가 그 세기이고, A/B 계측(tools/stamina_ab_qa.py)으로
# 기존 득점/평점과 비교한 뒤에만 올린다.
STAMINA_MODEL = True          # False면 체력/교체를 완전히 끈다(A/B 계측용)
STAMINA_DRAIN_BASE = 0.62     # 분당 기본 소모(스탯 50 기준 ≈ 0.58/분)
STAMINA_ET_MULT = 1.15        # 연장전 추가 소모 배수
STAMINA_FRESH_FLOOR = 75.0    # 이 체력 위에서는 경기력 영향 없음
STAMINA_PERF_COEF = 0.0022    # FRESH_FLOOR 아래 1점당 경기력 감소분
STAMINA_PERF_MIN = 0.80       # 경기력 하한(체력 0이어도 이 밑으로는 안 내려감)

# 포지션별 활동량(같은 시간에 체력이 얼마나 더 빠지는가).
STAMINA_POS_MULT = {
    "GK": 0.25, "CB": 0.86, "SW": 0.86, "LB": 1.06, "RB": 1.06,
    "LWB": 1.14, "RWB": 1.14, "CDM": 1.08, "CM": 1.10, "LM": 1.12, "RM": 1.12,
    "CAM": 1.04, "LW": 1.06, "RW": 1.06, "ST": 0.98, "CF": 0.98, "SS": 0.98,
}

SUBS_MAX_PLAYERS = 5          # 한 경기 교체 가능 인원
SUBS_MAX_WINDOWS = 3          # 경기 중 교체 기회(하프타임 제외)
SUBS_EXTRA_TIME_WINDOWS = 1   # 연장 진입 시 추가 기회
SUB_STAMINA_TRIGGER = 62.0    # 이 체력 밑이면 교체 후보
SUB_EARLIEST_MINUTE = 46      # 하프타임 이전에는 (부상 외) 교체 안 한다

# 연장 구조 — 정규시간은 내부적으로 1~96분(90+추가시간)이고, 연장은 그
# 뒤에 붙는 별도 구간이다. 표시 분(display_minute)만 91~122로 환산한다.
REG_MINUTES = 96              # 정규시간(추가시간 포함) 내부 길이
ET_HALF_MINUTES = 16          # 연장 전/후반 각 내부 길이(15+추가시간)

# ── 득점환경 튜닝 상수 ───────────────────────────────────────────
# [2026-09 신설, 신민용 리포트: "월드컵 등 국제대회에서 약팀이 강팀을
# 5대0으로 이기는 경우도 있다 / 포메이션 등 여러 조건에서 뭔가 과하게
# 가중치를 주는 것 같다"] 이 두 값은 원래 각각 simulate_tactical_match와
# _resolve_shot 안에 리터럴로 박혀 있었다 — 실측으로 재보정하면서 모듈
# 상수로 끌어올렸다(한 곳에서만 만지면 되도록).
#
# ── 왜 바꿨나: 실측 ──────────────────────────────────────────
# 이 엔진은 "내가 뛰는 모든 경기"(리그/컵/챔스/클럽월드컵/국가대표)를
# 담당하는데, AI끼리의 경기는 game_engine._gen_score("득점환경 v1.0",
# 리그 평균 1.47~1.51골/팀으로 캘리브레이션됨)로 돌아간다. 두 엔진의
# 득점환경을 처음으로 직접 대조해보니 크게 어긋나 있었다(실측, 잉글랜드
# 1부 실제 로스터 2000경기 × 3매치업):
#     이 엔진:    경기당 총 3.75~4.14골, 4골차 이상 15.8~21.0%
#     AI 리그:    경기당 총 2.85골,      4골차 이상 6.93%
# 국가대표 경기는 더 심했다(3000경기): 프랑스(97.3) vs 일본(94.1)에서
# 일본 승 38.0%에 그중 17.7%가 4골차 이상 — 일본 8-0/9-0, 10-2 France
# 같은 스코어가 실제로 나왔다.
#
# 원인 두 가지:
#  1) _MATCH_FORM_SIGMA(경기 당일 컨디션) 7.0이 국가대표 간 실제 OVR
#     격차(월드컵 본선급 평균 3.7, 최대 12)보다 크다 — 양 팀 차이로는
#     표준편차 약 9.9라, 실력 격차가 컨디션 난수에 통째로 묻혔다.
#     (이 값은 "분 단위 시뮬만으론 이변이 안 난다"는 이유로 도입됐는데,
#      당시엔 합성 스탯으로만 검증했고 실제 로스터로는 재보지 않았다.)
#  2) _SHOT_SAVE_BASE 0.63은 슈팅당 득점확률을 약 17%로 만든다(실제
#     축구는 10~11%). 슈팅 수 자체는 팀당 11~12개로 현실적인데 전환율만
#     높아서, 총 골이 4골대로 부풀고 그 꼬리(대량득점)가 통째로 두꺼워졌다.
#
# 재보정 후 실측은 simulate_tactical_match 정의부 아래 표 참고.
_MATCH_FORM_SIGMA = 3.5   # 경기 당일 컨디션(팀 전체에 걸리는 가우시안 오차)
_SHOT_SAVE_BASE = 0.72    # 유효슈팅이 막힐 기본 확률(_resolve_shot)

# [2026-09 신설, 신민용+GPT 설계: "72→75보다 82→85가, 그보다 92→95가,
# 92→95보다 95→97이 더 크게 벌어져야 한다"] raw stat(0~99)을 "실질
# 영향력 값"으로 바꾸는 공통 비선형 변환 — 모든 공격/수비/미드필드/GK/
# 슈팅 계산이 이 함수 하나만 거치게 통일한다(개별 함수마다 따로 만들면
# 나중에 한쪽만 고쳐서 어긋나는 사고가 난다). 80 이하는 기울기 1.00(=
# 항등함수)이라 기존 보정 상수(사igmoid 나눗셈 값 등)와의 하위호환이
# 그대로 유지된다 — 대다수 선수(80 이하)는 이 변환을 넣기 전과 결과가
# 똑같고, 오직 80+ 구간에서만 격차가 벌어진다.
# [2026-09, 신민용+GPT 확정] 99+("신급") 기울기 3.80은 1차 실험값 — 극단
# 매치업(90 vs 95/99/102) 헤드리스 검증 후 확정 예정.
_STAT_VALUE_BREAKPOINTS = (
    (0,  80, 1.00),
    (80, 90, 1.25),
    (90, 95, 1.70),
    (95, 99, 2.80),
    (99, 999, 3.80),
)


def effective_stat(raw):
    """raw stat(0~99, 드물게 99+ '신급')을 실질 영향력 값으로 변환.
    _STAT_VALUE_BREAKPOINTS 정의부 주석 참고."""
    if raw is None:
        raw = 50
    value = 0.0
    for lo, hi, slope in _STAT_VALUE_BREAKPOINTS:
        if raw <= lo:
            break
        seg = min(raw, hi) - lo
        value += seg * slope
    return value

# 포지션 라벨 -> 기준 좌표. x: 0(자기 골문)~1(상대 골문), y: 0(왼쪽)~1(오른쪽).
# match_sim_viewer._POS_XY와 같은 세계관을 공유하되(같은 좌표계 감각), 이
# 모듈은 UI 레이어에 의존하면 안 되므로 별도로 갖고 있는 값이다.
_POS_XY = {
    "GK":  (0.05, 0.50),
    "CB":  (0.16, 0.50), "LB": (0.18, 0.14), "RB": (0.18, 0.86),
    "LWB": (0.28, 0.12), "RWB": (0.28, 0.88),
    "CDM": (0.34, 0.50), "CM": (0.44, 0.50), "CAM": (0.48, 0.50),
    "LM":  (0.44, 0.16), "RM": (0.44, 0.84),
    "LW":  (0.49, 0.14), "RW": (0.49, 0.86),
    "CF":  (0.50, 0.50), "ST": (0.50, 0.50),
}
_FALLBACK_SLOTS = ["GK", "CB", "CB", "LB", "RB", "LM", "CM", "CM", "RM", "ST", "ST"]


def _third_of(bx):
    """이 팀 자신의 포메이션 상 역할(수비/미드필더/공격수) 분류.
    [버그 수정] 이 파일의 _POS_XY는 "한 팀이 스스로의 골문(0)을 기준으로
    갖는 기본 포메이션 형태"만 담고 있어서 x값 범위가 0.05~0.50 정도로
    좁다(공격수도 하프라인 부근인 0.50이 최댓값 — 원정팀은 이 값이
    1-bx로 뒤집혀 0.50~0.95가 됨). 그런데 예전 임계값(0.34/0.67)은 피치
    전체(0~1)를 3등분하는 값이라, 홈팀 공격수는 절대 "ATT"에 못
    들어가고(bx가 0.67을 못 넘음) 원정팀 수비수는 절대 "DEF"에 못
    들어갔다(뒤집힌 x가 0.34 밑으로 안 내려감) — 그 결과 _TeamModel의
    공격/수비 퀄리티가 실제 선수 스탯을 거의 반영하지 못했다.
    이제 이 함수는 뒤집히지 않은 원래 bx(각 팀 자신의 대형 좌표)만
    받는다 — "이 선수가 자기 팀 안에서 수비수/미드필더/공격수 중
    무엇에 가까운가"라는 팀 내부적 역할 분류이지, 피치의 고정된 절대
    구역이 아니기 때문이다. 임계값도 실제 _POS_XY 값 분포(수비 라인
    0.05~0.28, 중원 0.34~0.48, 최전방 0.49~0.50)에 맞게 재보정했다."""
    if bx < 0.30:
        return "DEF"
    if bx < 0.485:
        return "MID"
    return "ATT"


def _sigmoid(x):
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _assign_zones(lineup, is_home, slot_labels=None):
    """[{"player":dict, "lane":..., "third":...}, ...]. lineup은
    FORMATION_SLOTS[formation] 순서와 대응하는 선수 dict 리스트(None 허용).
    원정팀은 홈팀과 정반대 방향을 보고 뛰므로, 전후좌표(x)뿐 아니라
    좌우좌표(by)도 함께 뒤집어야(1-by) 홈팀 시점 고정좌표계에서 물리적으로
    맞는 위치가 된다.
    [버그 수정] 예전엔 x만 뒤집고 by는 그대로 둬서, 원정팀 왼쪽 수비수가
    실제로는 홈팀 시점 오른쪽 측면에 있어야 하는데도 그대로 "L" 레인에
    잡혔다. 매치업 계산(simulate_tactical_match의 atk.att[lane] vs
    dfn_opp.dfn[lane])이 두 팀을 같은 레인 라벨끼리 비교하는 구조라서,
    좌우 능력치가 비대칭인 스쿼드(예: 왼쪽 윙어는 강한데 오른쪽 풀백은
    약한 팀)를 상대할 때 실제로는 안 맞붙어야 할 반대편 선수와 매치업이
    계산되는 원인이었다.

    [2026-09 버그수정, 신민용 리포트: "우리팀 포메이션은 5-2-3인데
    라인업 화면엔 무조건 4-2-2-2로 뜬다 — 상대팀도 실제(4-2-1-3)와
    다르게 뜬다"] slot_labels(그 라인업을 실제로 뽑을 때 쓴
    FORMATION_SLOTS[formation])를 안 받으면 이 함수가 항상 하드코딩된
    4-4-2 라벨(_FALLBACK_SLOTS)로 매 선수의 위치를 다시 칠했다 —
    lineup 자체는 _select_lineup()이 그 팀의 실제 포메이션 슬롯 순서로
    뽑아주는데, 라벨만 여기서 딴 걸로 덮어써서 실제 배치와 표시/구역
    계산이 어긋났다. 이 어긋남은 화면 표시뿐 아니라 _third_of(공격/
    수비/중원 역할 분류)에도 그대로 들어가 매치업 계산 자체에 영향을
    준다. 이제 호출부(simulate_tactical_match)가 그 라인업을 만들 때
    쓴 실제 슬롯 리스트를 넘겨주면 그걸 쓰고, 안 넘기면(기존 호출부·
    국제대회처럼 원래 4-4-2뿐인 경우) 예전과 동일하게 _FALLBACK_SLOTS로
    폴백한다 — 하위 호환 100% 유지."""
    labels = slot_labels if slot_labels else _FALLBACK_SLOTS
    out = []
    for i, pl in enumerate(lineup):
        if pl is None:
            continue
        label = labels[i] if i < len(labels) else pl.get("position", "CM")
        bx, by = _POS_XY.get(label, (0.44, 0.5))
        x = bx if is_home else (1.0 - bx)
        y = by if is_home else (1.0 - by)
        lane = "L" if y < 0.34 else ("C" if y < 0.67 else "R")
        # [버그 수정] third(수비/미드필더/공격수 역할 분류)는 팀 내부적
        # 역할이라 뒤집힌 x가 아니라 항상 원래 bx로 판정해야 한다(자세한
        # 이유는 _third_of 문서 참고) — 안 그러면 홈팀 공격수/원정팀
        # 수비수의 실제 스탯이 att/dfn 계산에 전혀 반영되지 않는다.
        out.append({"player": pl, "pos": label, "lane": lane, "third": _third_of(bx)})
    return out


def _avg(vals, default=50.0):
    vals = list(vals)
    return sum(vals) / len(vals) if vals else default


def display_minute(minute):
    """내부 분 → 화면에 보여줄 분. 정규시간은 그대로, 연장은 91~122로 환산.

    내부: 1..96(정규) / 97..112(연장 전반) / 113..128(연장 후반)
    표시: 1..96        / 91..106           / 107..122
    """
    if minute <= REG_MINUTES:
        return int(minute)
    if minute <= REG_MINUTES + ET_HALF_MINUTES:
        return int(90 + (minute - REG_MINUTES))
    return int(90 + ET_HALF_MINUTES + (minute - REG_MINUTES - ET_HALF_MINUTES))


# ── 경기 상세 타임라인용 분 코드 ──────────────────────────────────
# [2026-09 신설, 신민용 리포트: "경기 상세에서 전반 후반 여기서 연장이
# 뜨면 연장 전반 연장 후반 이렇게도 떠야 하는데 안 떠"]
#
# 타임라인(ui/match_detail_dialog.py)은 예전부터 자기만의 분 인코딩을
# 쓴다 — 1~45=전반, 146~155=전반 추가시간(45+1~10), 46~90=후반,
# 91~100=후반 추가시간(90+1~10). 그런데 display_minute이 돌려주는 연장
# 분도 91부터 시작한다(연장 전반=91~106, 연장 후반=107~122). 즉 **표시
# 분만으로는 "후반 추가시간 91분"과 "연장 전반 91분"을 구분할 수 없다**.
# 그래서 연장 이벤트는 베이스 값을 얹은 별도 코드로 넘긴다 — 구간이
# 겹치지 않으므로 기존 기록(전부 1000 미만)과도 100% 호환된다.
TIMELINE_ET1_BASE = 1000      # 연장 전반 → 1000 + 표시분(91~106)
TIMELINE_ET2_BASE = 2000      # 연장 후반 → 2000 + 표시분(107~122)


def _style_edges(my_style, opp_style):
    """[2026-09 신설 — 감독 시스템 ②단계] 한 팀 관점의 (점유 보정, 기회품질
    보정)을 OVR 환산치로 돌려준다.

    내 빌드업 성향과 **상대의 압박 성향** 둘 다가 나에게 영향을 준다 —
    "내가 점유를 하려 해도 상대가 강하게 압박하면 못 한다"가 성립해야
    감독 매치업이 의미를 갖기 때문이다. 값의 의미와 왜 반대 방향 두 항을
    같이 주는지는 constants.MANAGER_BUILDUP_POSS_EDGE 위 주석 참고.

    성향이 없으면(감독 표가 없는 구세이브, 국제대회처럼 감독 개념이 없는
    경기) (0.0, 0.0)을 돌려줘 예전 동작을 그대로 유지한다."""
    if not my_style and not opp_style:
        return 0.0, 0.0
    from constants import (MANAGER_BUILDUP_POSS_EDGE, MANAGER_BUILDUP_CHANCE_EDGE,
                           MANAGER_PRESS_OPP_POSS_EDGE, MANAGER_PRESS_OPP_CHANCE_EDGE)
    _b = (my_style or {}).get("style_buildup") or "MIXED"
    _op = (opp_style or {}).get("style_press") or "MID_BLOCK"
    poss = (MANAGER_BUILDUP_POSS_EDGE.get(_b, 0.0)
            + MANAGER_PRESS_OPP_POSS_EDGE.get(_op, 0.0))
    qual = (MANAGER_BUILDUP_CHANCE_EDGE.get(_b, 0.0)
            + MANAGER_PRESS_OPP_CHANCE_EDGE.get(_op, 0.0))
    return poss, qual


def timeline_minute(minute):
    """내부 분 → 경기 상세 타임라인용 분 코드.

    정규시간(1~96)은 그대로 둔다 — 91~96이 곧 타임라인의 "90+1~6"
    (후반 추가시간)이라 기존 규칙과 정확히 맞는다. 연장만 위 베이스를
    얹어서 어느 구간인지 잃어버리지 않게 한다."""
    try:
        m = int(minute)
    except (TypeError, ValueError):
        return minute
    if m <= REG_MINUTES:
        return m
    if m <= REG_MINUTES + ET_HALF_MINUTES:
        return TIMELINE_ET1_BASE + display_minute(m)
    return TIMELINE_ET2_BASE + display_minute(m)


def _pkey(p):
    """선수 dict의 기록 키 — _pstat과 완전히 같은 규칙(id 없으면 파이썬 id)."""
    k = p.get("id")
    return k if k is not None else id(p)


class _Stamina:
    """한 경기 동안의 선수별 체력 상태. 선수 dict 자체는 건드리지 않고
    (국제대회 등 호출부가 공유 dict를 넘길 수 있으므로) 키 기반으로만
    관리한다. STAMINA_MODEL이 False면 모든 조회가 100/1.0을 돌려주는
    무동작 모드가 된다 — A/B 계측용."""

    __slots__ = ("val", "enabled")

    def __init__(self, enabled=True):
        self.val = {}
        self.enabled = bool(enabled)

    def ensure(self, p, start=100.0):
        if p is None:
            return
        self.val.setdefault(_pkey(p), float(start))

    def get(self, p):
        if p is None:
            return 100.0
        return self.val.get(_pkey(p), 100.0)

    def perf(self, p):
        """체력이 경기력에 주는 배수(1.0 = 영향 없음)."""
        if not self.enabled or p is None:
            return 1.0
        st = self.val.get(_pkey(p))
        if st is None or st >= STAMINA_FRESH_FLOOR:
            return 1.0
        return max(STAMINA_PERF_MIN,
                   1.0 - (STAMINA_FRESH_FLOOR - st) * STAMINA_PERF_COEF)

    def drain_minute(self, zoned, had_possession, is_extra):
        """그 팀 11명의 체력을 1분치 깎는다.

        had_possession: 이번 분에 그 팀이 볼을 가졌는지(공격 가담 쪽이 더
        소모) — 활동량 반영. is_extra면 연장 배수를 곱한다.
        """
        if not self.enabled:
            return
        et = STAMINA_ET_MULT if is_extra else 1.0
        for z in zoned:
            p = z["player"]
            if p is None:
                continue
            k = _pkey(p)
            cur = self.val.get(k)
            if cur is None:
                cur = 100.0
            # 체력 능력치가 높으면 덜 빠진다(50 → 0.93, 90 → 0.72, 30 → 1.14).
            stat = p.get("stamina", 50) or 50
            mult = max(0.55, 1.35 - 0.007 * float(stat))
            mult *= STAMINA_POS_MULT.get(z["pos"], 1.0)
            # 활동량 — 볼을 가진 쪽은 공격 라인이, 없는 쪽은 수비 라인이
            # 더 많이 뛴다고 본다.
            third = z["third"]
            if had_possession:
                if third in ("ATT", "MID"):
                    mult *= 1.06
            else:
                if third in ("DEF", "MID"):
                    mult *= 1.06
            self.val[k] = max(0.0, cur - STAMINA_DRAIN_BASE * mult * et)


def _player_attack_value(p):
    return (p.get("shooting", 50) * 0.35 + p.get("dribbling", 50) * 0.30
            + p.get("passing", 50) * 0.35)


def _player_defense_value(p):
    return (p.get("tackling", 50) * 0.45 + p.get("positioning", 50) * 0.35
            + p.get("strength", 50) * 0.20)


def _attack_quality(players):
    return _avg(_player_attack_value(p) for p in players)


def _defense_quality(players):
    return _avg(_player_defense_value(p) for p in players)


def _midfield_quality(players, perf=None):
    return _avg((p.get("passing", 50) * 0.35 + p.get("positioning", 50) * 0.30
                 + p.get("dribbling", 50) * 0.20 + p.get("stamina", 50) * 0.15)
                * (perf(p) if perf else 1.0)
                for p in players)


# [2026-09 신설, 신민용+GPT 설계: "포메이션을 11명의 배치로만 보지 말고
# 팀 능력이 어느 방향(레인)으로 분배되는지를 보라"] 포지션별로 L/C/R
# 공격·수비 레인에 얼마나 기여하는지 — 공격 기여와 수비 기여를 분리한다
# (예: LW는 공격은 L에 강하게 기여하지만 수비는 L을 LB만큼 책임지지
# 않는다). 1차 구현값이라 정확한 숫자는 헤드리스 검증 후 조정 대상.
# att[ln]/dfn[ln] 계산 시 가중평균(weighted_sum/total_weight)으로 정규화
# 하므로, 특정 레인에 선수가 몇 명 배치되든 "그 레인 평균 기여도"만
# 반영되고 인원수 자체가 무한 보너스가 되지는 않는다 — 포메이션 차이는
# "누가 그 레인에 얼마나 기여하는가"의 가중치 분포 변화로만 나타난다.
ATT_LANE_AFFINITY = {
    "LW":  {"L": 1.00, "C": 0.10, "R": 0.00},
    "RW":  {"L": 0.00, "C": 0.10, "R": 1.00},
    "ST":  {"L": 0.10, "C": 1.00, "R": 0.10},
    "CF":  {"L": 0.10, "C": 1.00, "R": 0.10},
    "CAM": {"L": 0.15, "C": 1.00, "R": 0.15},
    "LM":  {"L": 0.75, "C": 0.30, "R": 0.00},
    "RM":  {"L": 0.00, "C": 0.30, "R": 0.75},
    "CM":  {"L": 0.15, "C": 0.70, "R": 0.15},
    "CDM": {"L": 0.05, "C": 0.85, "R": 0.05},
    "LB":  {"L": 0.45, "C": 0.10, "R": 0.00},
    "RB":  {"L": 0.00, "C": 0.10, "R": 0.45},
    "LWB": {"L": 0.75, "C": 0.15, "R": 0.00},
    "RWB": {"L": 0.00, "C": 0.15, "R": 0.75},
    "CB":  {"L": 0.05, "C": 0.55, "R": 0.05},
    "GK":  {"L": 0.00, "C": 0.00, "R": 0.00},
}

DEF_LANE_AFFINITY = {
    "LB":  {"L": 1.00, "C": 0.10, "R": 0.00},
    "RB":  {"L": 0.00, "C": 0.10, "R": 1.00},
    "LWB": {"L": 0.90, "C": 0.15, "R": 0.00},
    "RWB": {"L": 0.00, "C": 0.15, "R": 0.90},
    "CB":  {"L": 0.15, "C": 1.00, "R": 0.15},
    "CDM": {"L": 0.10, "C": 0.75, "R": 0.10},
    "CM":  {"L": 0.15, "C": 0.55, "R": 0.15},
    "LM":  {"L": 0.55, "C": 0.25, "R": 0.00},
    "RM":  {"L": 0.00, "C": 0.25, "R": 0.55},
    "CAM": {"L": 0.05, "C": 0.25, "R": 0.05},
    "LW":  {"L": 0.20, "C": 0.05, "R": 0.00},
    "RW":  {"L": 0.00, "C": 0.05, "R": 0.20},
    "ST":  {"L": 0.02, "C": 0.10, "R": 0.02},
    "CF":  {"L": 0.02, "C": 0.10, "R": 0.02},
    "GK":  {"L": 0.00, "C": 0.00, "R": 0.00},
}


def _lane_quality(zoned, ln, affinity_table, value_fn):
    """affinity_table(포지션→레인 기여도)로 가중평균한 그 레인의 실질
    퀄리티. affinity가 0인 선수는 그 레인 계산에서 완전히 빠진다(가중치
    0). 분모(total_w)로 나누는 가중평균이라 그 레인에 선수가 많다고
    무조건 값이 커지지 않는다 — 포메이션 차이는 "누가 얼마나 기여하는가"
    분포로만 나타난다."""
    total_w = 0.0
    total_val = 0.0
    for z in zoned:
        aff = affinity_table.get(z["pos"], {}).get(ln, 0.0)
        if aff <= 0:
            continue
        total_val += value_fn(z["player"]) * aff
        total_w += aff
    return total_val / total_w if total_w > 0 else 50.0


def _gk_quality(gk, perf=None):
    # [2026-09, 신민용+GPT 설계: "증폭이 여러 단계에 중복 적용되면 안 된다"]
    # 이 함수의 결과는 (a) home.gk_q(현재는 boost 분배에만 쓰임 — 경기
    # 결과에 미치는 영향이 미미함)와 (b) _resolve_shot의 opp_gk_q(슈터와
    # 직접 맞대결하는 "결정력의 순간") 두 곳에 쓰인다. 비선형 변환은
    # (b) 목적에 맞춘 것이므로, 여기서는 원래 raw stat을 그대로 반환하고
    # _resolve_shot 쪽에서 opp_gk_q에 effective_stat()을 그 자리에서
    # 적용한다(중복 적용 방지 — 아래 _resolve_shot 주석 참고).
    if not gk:
        return 50.0
    return (gk.get("positioning", 50) * 0.5 + gk.get("concentration", 50) * 0.3
            + gk.get("jump", 50) * 0.2) * (perf(gk) if perf else 1.0)


# [2026-07 신설] 포지션별 boost(캐리 보너스) 채널 분배. 예전엔 포지션 구분
# 없이 모든 포지션이 공격 0.6/수비 0.15/중원 0.5/GK 0.1로 똑같이 받아서,
# 골키퍼가 아무리 OVR이 높아도 보너스의 90%가 공격·중원(본인이 관여 안 함)
# 으로 새고 정작 세이브 능력(gk_q)엔 10%만 반영되는 문제가 있었다(사용자
# 실측: OVR92 골키퍼가 팀평균 30후반 리그에서 전혀 안 먹힘). 이제 그
# 포지션이 실제로 경기에 관여하는 영역에 보너스가 집중되도록 나눈다.
# 각 값은 서로 다른 채널에 독립적으로 곱해지는 계수라 합이 1일 필요는
# 없다(기존 방식과 동일한 구조) — 다만 그 포지션의 실제 영향력 분포를
# 반영해 채널별 비중을 다르게 뒀다.
POSITION_BOOST_WEIGHTS = {
    "GK":  {"att": 0.00, "dfn": 0.15, "mid": 0.00, "gk": 0.90},
    "CB":  {"att": 0.05, "dfn": 0.80, "mid": 0.15, "gk": 0.00},
    "LB":  {"att": 0.15, "dfn": 0.65, "mid": 0.25, "gk": 0.00},
    "RB":  {"att": 0.15, "dfn": 0.65, "mid": 0.25, "gk": 0.00},
    "LWB": {"att": 0.25, "dfn": 0.55, "mid": 0.30, "gk": 0.00},
    "RWB": {"att": 0.25, "dfn": 0.55, "mid": 0.30, "gk": 0.00},
    "CDM": {"att": 0.10, "dfn": 0.35, "mid": 0.55, "gk": 0.00},
    "CM":  {"att": 0.25, "dfn": 0.15, "mid": 0.65, "gk": 0.00},
    "CAM": {"att": 0.45, "dfn": 0.05, "mid": 0.50, "gk": 0.00},
    "LM":  {"att": 0.35, "dfn": 0.15, "mid": 0.55, "gk": 0.00},
    "RM":  {"att": 0.35, "dfn": 0.15, "mid": 0.55, "gk": 0.00},
    "LW":  {"att": 0.70, "dfn": 0.05, "mid": 0.30, "gk": 0.00},
    "RW":  {"att": 0.70, "dfn": 0.05, "mid": 0.30, "gk": 0.00},
    "CF":  {"att": 0.85, "dfn": 0.00, "mid": 0.20, "gk": 0.00},
    "ST":  {"att": 0.85, "dfn": 0.00, "mid": 0.20, "gk": 0.00},
}
# 포지션을 모르거나 표에 없을 때 쓰는 폴백 — 기존(2026-07 이전) 분배값 그대로.
_DEFAULT_BOOST_WEIGHTS = {"att": 0.6, "dfn": 0.15, "mid": 0.5, "gk": 0.1}


def _boost_weights_for(position):
    return POSITION_BOOST_WEIGHTS.get(position, _DEFAULT_BOOST_WEIGHTS)


class _TeamModel:
    """한 팀의 레인별 공격/수비 퀄리티 + 중원 퀄리티 + GK 퀄리티를 미리
    계산해 담아두는 그릇. boost는 '내 에이스가 팀을 끌어올리는 효과'를
    수비/공격/중원 전역에 고르게 얹기 위한 값(game_engine._simulate_match가
    이미 계산해둔 bonus를 그대로 받는다)."""

    def __init__(self, lineup, is_home, boost=0.0, boost_position=None, slot_labels=None,
                 perf=None):
        # perf: [2026-09 신설] 선수 dict → 체력에 따른 경기력 배수(1.0 = 영향
        # 없음). 교체/체력 갱신 시점마다 이 모델을 다시 만들어서 그 시점의
        # 체력이 레인 퀄리티에 반영되게 한다(_Stamina.perf 참고).
        zoned = _assign_zones(lineup, is_home, slot_labels)
        self.zoned = zoned
        self.gk = next((z["player"] for z in zoned if z["pos"] == "GK"), None)
        w = _boost_weights_for(boost_position) if boost else _DEFAULT_BOOST_WEIGHTS
        if perf is not None:
            _atk_fn = lambda _p: _player_attack_value(_p) * perf(_p)   # noqa: E731
            _dfn_fn = lambda _p: _player_defense_value(_p) * perf(_p)  # noqa: E731
        else:
            _atk_fn, _dfn_fn = _player_attack_value, _player_defense_value
        self.att = {}
        self.dfn = {}
        # [2026-09 신설, ATT_LANE_AFFINITY/DEF_LANE_AFFINITY 정의부 주석
        # 참고] 예전엔 lane+third로 하드 배정된 선수들만 평균냈다(예:
        # LW는 항상 L-ATT에만 잡히고 수비 계산엔 전혀 안 들어감) — 이제
        # 전체 11명을 대상으로 포지션별 레인 기여도(affinity)로 가중평균
        # 한다. third(DEF/MID/ATT) 하드 배정은 슈터 후보 풀 선정(third==
        # "ATT")에는 그대로 쓰이므로 _assign_zones 자체는 안 바꾼다.
        for ln in LANES:
            self.att[ln] = _lane_quality(zoned, ln, ATT_LANE_AFFINITY, _atk_fn) + boost * w["att"]
            self.dfn[ln] = _lane_quality(zoned, ln, DEF_LANE_AFFINITY, _dfn_fn) + boost * w["dfn"]
        mid_players = [z["player"] for z in zoned if z["third"] == "MID"]
        self.mid = _midfield_quality(mid_players, perf=perf) + boost * w["mid"]
        self.gk_q = _gk_quality(self.gk, perf=perf) + boost * w["gk"]


def _pstat(player_stats, p):
    """[2026-08 신설, 신민용 요청: "경기 시뮬레이션에 다른 선수들의 OVR·
    스탯도 계산해서 정교한 결과를 뽑고, 경기 상세에서 22명 전원 평점을
    보여달라"] player_stats(선수 id -> 개인 기록 누적 dict)에서 이 선수의
    항목을 찾아 반환 — 없으면 0으로 초기화해 새로 만든다. id가 없는(가상
    폴백) 선수는 객체 자체의 파이썬 id()를 키로 대신 써서 최소한 이번
    한 경기 안에서는 같은 객체가 같은 기록으로 누적되게 한다."""
    key = p.get("id")
    if key is None:
        key = id(p)
    rec = player_stats.get(key)
    if rec is None:
        rec = {"shots": 0, "shots_on": 0, "goals": 0, "assists": 0,
               "saves": 0, "goals_conceded": 0}
        player_stats[key] = rec
    return rec


def _new_stats_detail():
    """[2026-09 신설] "표시용" team_stats(점유율/슈팅/유효슈팅/코너/파울/
    패스성공률/오프사이드/카드/세이브 — 10개, 실제 중계화면 느낌)와 별도로
    엔진 내부에서 계산해두는 세부 통계 그릇. 화면엔 기본적으로 안 뿌리고
    (나중에 선수 통계·분석 등에 재활용하기 위해) 같이 저장만 해둔다.

    전부 기존 분당 시뮬레이션 루프(shot_chance/corner_chance/foul_chance로
    스코어를 결정하는 그 로직)는 손대지 않고, 그 결과에 병렬로 얹어서
    누적한다 — 경기 결과에 영향을 주지 않으므로 속도·밸런스 둘 다 그대로."""
    return {
        "passes": 0, "passes_ok": 0,
        "crosses": 0, "crosses_ok": 0,
        "tackles": 0, "tackles_ok": 0,
        "interceptions": 0, "clearances": 0, "blocks": 0,
        "aerial_duels": 0, "aerial_duels_ok": 0,
        "dribbles": 0, "dribbles_ok": 0,
        "turnovers_won": 0, "turnovers_lost": 0,
        "final_third_entries": 0, "box_entries": 0,
        "big_chances": 0, "big_chances_missed": 0,
        "xg": 0.0, "xa": 0.0,
        "woodwork": 0, "free_kicks": 0, "penalties": 0,
        "save_pct": 0.0,
    }


def _resolve_shot(rng, side, lane, minute, shooter_pool, opp_gk, opp_gk_q,
                   home_stats, away_stats, home_player_stats, away_player_stats, plog,
                   home_detail, away_detail, shooter_mults=None, perf_of=None):
    """슈팅 하나를 판정해서 team_stats/possession_log/선수별 개인 기록
    (슈팅·유효슈팅·골·도움·선방·실점)을 함께 갱신한다.
    반환값: 골이 들어갔으면 "home"/"away", 아니면 None.

    [2026-08 확장] 예전엔 shooter_pool에서 슈팅 스탯 가중으로 슈터를
    뽑아놓고도 그 신원을 결과에 전혀 남기지 않았다(팀+결과만 기록) —
    이제 그 선수 개인 기록에 직접 누적한다. 어시스트는 완전한 패스체인
    시뮬레이션 대신, 골이 들어갔을 때 같은 레인 공격 풀에서 슈터를 뺀
    나머지를 패스 능력 가중으로 뽑아 일정 확률로 붙이는 근사치다.

    [2026-09 확장, xG/xA] on_target_p(유효슈팅 확률)*(1-save_p)(안 막힐
    확률)를 "이 슈팅의 결과가 나오기 전 기대 득점(xG)"으로 그대로 쓴다 —
    이미 계산해두는 값이라 추가 비용이 없다. xA는 신민용 확정 설계대로
    "그 슈팅의 xG를 마지막 기여자에게 배분": 실제 어시스트가 붙으면 그
    도움 선수에게, 골로 안 이어진 유효슈팅도 일정 확률로 "키패스"를 굴려
    같은 방식으로 배분한다(어시스트 기록 없이도 창조적 기여를 평가할 수
    있게 하기 위함, 향후 개인 스탯 확장 대비)."""
    stats = home_stats if side == "home" else away_stats
    detail = home_detail if side == "home" else away_detail
    opp_detail = away_detail if side == "home" else home_detail
    my_pstats = home_player_stats if side == "home" else away_player_stats
    opp_pstats = away_player_stats if side == "home" else home_player_stats
    stats["shots"] += 1
    detail["final_third_entries"] += 1
    detail["box_entries"] += 1

    # [2026-09] shooter_mults는 "이 레인에서 그 선수가 마무리를 맡을 상대적
    # 빈도"(최전방 같은 레인 1.0 / 같은 레인 미드 0.42 / 옆 레인 공격 0.30 /
    # 옆 레인 미드 0.12) — _prep_side 주석 참고. 안 넘기면 예전처럼 결정력
    # 가중만 쓴다. rng.choices 호출 횟수는 그대로다.
    if shooter_pool:
        if shooter_mults and len(shooter_mults) == len(shooter_pool):
            weights = [max(0.05, effective_stat(p.get("shooting", 50)) * m)
                       for p, m in zip(shooter_pool, shooter_mults)]
        else:
            weights = [max(1.0, effective_stat(p.get("shooting", 50))) for p in shooter_pool]
        shooter = rng.choices(shooter_pool, weights=weights, k=1)[0]
    else:
        shooter = None
    # [2026-09] 체력은 "결정력의 순간"에도 들어간다 — 지친 공격수의 마무리,
    # 지친 GK의 선방이 실제로 나빠져야 교체에 의미가 생긴다(perf_of는
    # _Stamina.perf, 체력 모델이 꺼져 있으면 항상 1.0).
    shot_stat = effective_stat(shooter.get("shooting", 50)) if shooter else effective_stat(50)
    if perf_of is not None and shooter is not None:
        shot_stat *= perf_of(shooter)
    if shooter is not None:
        _pstat(my_pstats, shooter)["shots"] += 1

    # [2026-09, 신민용+GPT 설계: "증폭은 한 지점에서만"] opp_gk_q는
    # _gk_quality()가 raw로 넘겨준 값 — 슈터와 정확히 같은 "결정력의 순간"
    # 비교이므로 여기서 딱 한 번만 effective_stat을 적용한다(호출부인
    # _TeamModel.gk_q 자체는 raw로 남겨 boost 분배 등 다른 용도에 중복
    # 증폭이 새지 않게 한다).
    eff_gk_q = effective_stat(opp_gk_q)
    # [2026-09 재조정, 위 lane quality /24.0과 동일한 실측 근거] 150/120도
    # 같은 이유로 과민했다 — effective_stat 변환(80+ 구간에서 값이 커짐)이
    # 여기 들어오면서 기존 나눗값 그대로 두면 이중으로 민감해진다. 240/210
    # 으로 넓혀서 "결정력 격차가 큰 의미를 갖되, 경기가 사실상 결정론이
    # 되지는 않는" 지점을 실측으로 찾았다(90 vs 105 극단 매치업에서도
    # 원정 100% 승리는 아님, 85+스타1명 캐리 시나리오도 검증됨).
    on_target_p = max(0.15, min(0.78, 0.30 + (shot_stat - 50) / 240.0))
    save_p = max(0.08, min(0.90, _SHOT_SAVE_BASE + (eff_gk_q - shot_stat) / 210.0))
    shot_xg = round(on_target_p * (1.0 - save_p), 4)
    detail["xg"] += shot_xg
    is_big_chance = shot_xg >= 0.35

    if rng.random() >= on_target_p:
        # [세분화] 빗나간 슈팅 중 일부는 "완전히 벗어남"이 아니라 "골대를
        # 맞고 나감"으로 표시만 더 얹는다 — 유효슈팅/스코어 판정은 그대로.
        if rng.random() < 0.05:
            detail["woodwork"] += 1
        if is_big_chance:
            detail["big_chances"] += 1
            detail["big_chances_missed"] += 1
        plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                     "outcome": "shot_off", "me": False, "text": None})
        return None

    stats["shots_on"] += 1
    if shooter is not None:
        _pstat(my_pstats, shooter)["shots_on"] += 1
    if rng.random() < save_p:
        # 유효슈팅이 막힘 — 그중 일부는 "수비수 블록"으로 더 세분화하고
        # (상대 수비 기여 스탯), 골로 이어지지 않았어도 그 장면을 만든
        # 선수에게 키패스/xA를 확률적으로 인정한다.
        if rng.random() < 0.30:
            opp_detail["blocks"] += 1
        if opp_gk is not None:
            _pstat(opp_pstats, opp_gk)["saves"] += 1
        if is_big_chance:
            detail["big_chances"] += 1
            detail["big_chances_missed"] += 1
        if shooter_pool and len(shooter_pool) > 1 and rng.random() < 0.35:
            detail["xa"] += shot_xg
        plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                     "outcome": "save", "me": False, "text": None})
        return None

    # 골.
    if is_big_chance:
        detail["big_chances"] += 1
    plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                 "outcome": "goal", "me": False, "text": None,
                 "scorer_id": (shooter.get("id") if shooter is not None else None)})
    if shooter is not None:
        _pstat(my_pstats, shooter)["goals"] += 1
    if opp_gk is not None:
        _pstat(opp_pstats, opp_gk)["goals_conceded"] += 1
    if shooter_pool and len(shooter_pool) > 1 and rng.random() < 0.62:
        # 도움도 같은 레인 배수를 반영한다 — 안 그러면 옆 레인 미드필더가
        # 패스 스탯만으로 도움 1위가 되어버린다.
        _cz = list(zip(shooter_pool, shooter_mults)) if (
            shooter_mults and len(shooter_mults) == len(shooter_pool)) else [
            (p, 1.0) for p in shooter_pool]
        creator_pool = [p for p, _m in _cz if p is not shooter]
        _cmults = [_m for p, _m in _cz if p is not shooter]
        if creator_pool:
            weights2 = [max(0.05, p.get("passing", 50) * m)
                        for p, m in zip(creator_pool, _cmults)]
            creator = rng.choices(creator_pool, weights=weights2, k=1)[0]
            _pstat(my_pstats, creator)["assists"] += 1
            detail["xa"] += shot_xg
    return side


def _lineup_avg_ovr(lineup):
    vals = [p.get("ovr", 50) for p in lineup if p is not None]
    return sum(vals) / len(vals) if vals else 50.0


def _build_player_ratings(lineup, player_stats, gf, ga, gk, rng, slot_labels=None,
                          appear=None, total_minutes=None):
    """[2026-08 신설] 경기 종료 후 이 팀 11명 전원(라인업 슬롯 순서 그대로,
    빈 슬롯은 None)의 개인 기록 + 평점을 만든다.

    평점 공식은 game_engine._player_perf(내 선수 전용 평점)와 같은
    발상 — 기본값에서 시작해 팀 결과/개인 기여/포지션별 특성을 더하고
    빼는 방식 — 을 22명 전체로 일반화한 것이다. 다만 이 엔진은 개별
    수비 액션(태클 성공/실패 등)까지는 분 단위로 추적하지 않으므로,
    비GK 필드 플레이어의 평점은 "팀 결과 + 그 선수의 골/도움 기여 +
    그 선수 OVR이 이 라인업 평균보다 얼마나 높은가(에이스는 골이 없어도
    경기를 지배한 것으로 봄)"를 근거로 삼는다 — 완전한 개인 이벤트
    로그가 아니라 근사치라는 점은 명확히 해둔다.

    [2026-08 버그수정, 신민용 리포트: "라인업 평점에 뜨는 이름이
    AI0JP8이 아니라 names.py에서 뽑힌 실제 이름이다"] ai_players.name은
    data/names.py에서 뽑은 내부 시드값일 뿐, 화면에는 항상 마스킹된
    표시명(ui/formation_widget._mask_ai_names와 완전히 동일한 규칙 —
    사용자가 직접 지어준 커스텀 이름이 있으면 그걸, 없으면
    constants.ai_player_code로 만든 "AI"+코드)을 써야 한다. 이 값은
    실제로 존재하는 그 선수(가상으로 지어낸 게 아니라 그 팀 로스터에서
    _select_lineup이 실제로 뽑은 ai_players 레코드)이고, 문제는 이름
    '표시' 단계뿐이었다.

    [2026-09 버그수정, 신민용 리포트: "포메이션이 5-2-3인데 라인업
    화면엔 4-2-2-2로 뜬다"] _assign_zones와 같은 이유로, 여기 출력되는
    각 선수의 "position"도 실제 포메이션과 무관하게 항상 _FALLBACK_SLOTS
    (4-4-2 라벨)로 찍히고 있었다 — ui/match_detail_dialog.py의 라인업
    평점·포메이션 시각화가 이 값을 그대로 보여주므로 화면에 실제
    포메이션과 다른 모양이 떴다. slot_labels(호출부가 이 lineup을 뽑을
    때 쓴 실제 FORMATION_SLOTS[formation])를 받으면 그걸 쓰고, 안
    받으면 예전처럼 _FALLBACK_SLOTS로 폴백한다."""
    labels = slot_labels if slot_labels else _FALLBACK_SLOTS
    # [2026-09 확장 — 교체 선수 포함] appear가 있으면 "이 경기에 실제로
    # 나온 모든 선수"(선발 11 + 교체 투입)를 대상으로 만든다. 선발은 슬롯
    # 순서를 그대로 유지하고(화면의 포메이션 시각화가 인덱스에 의존),
    # 교체 투입 선수는 그 뒤에 투입 시각 순으로 붙인다.
    subs_extra = []
    if appear:
        subs_extra = sorted(
            (a for a in appear.values() if not a.get("started")),
            key=lambda a: (a.get("on") or 0, str(a.get("slot"))))
    real_ids = [p.get("id") for p in lineup if p is not None and p.get("id") is not None]
    real_ids += [a["player"].get("id") for a in subs_extra
                 if a["player"] is not None and a["player"].get("id") is not None]
    try:
        from database import get_ai_player_custom_names
        custom_names = get_ai_player_custom_names(real_ids) if real_ids else {}
    except Exception:
        custom_names = {}
    from constants import ai_player_code

    def _display_name(pl):
        pid = pl.get("id")
        if pid is None:
            return pl.get("name", "") or "AI"
        return custom_names.get(pid) or ai_player_code(pid)

    avg_ovr = _lineup_avg_ovr(lineup)
    clean_sheet = (ga == 0)
    result_mod = 0.45 if gf > ga else (-0.35 if gf < ga else 0.0)
    _tot = max(1, int(total_minutes or REG_MINUTES))

    def _entry(p, label, is_gk, mins, started, on_min, off_min):
        key = _pkey(p)
        pstat = player_stats.get(key, {})
        # [2026-09] 27분만 뛴 교체 선수에게 90분 뛴 선수와 똑같은 팀 결과
        # 보정(result_mod)·완봉 보너스를 주면 안 된다 — 출전시간 비중으로
        # 깎는다(골/도움/선방은 실제 기록이므로 그대로 만점 반영).
        share = max(0.0, min(1.0, mins / float(_tot)))
        _w = share ** 0.5 if started is False else 1.0
        base = 6.3 + result_mod * _w
        base += max(-0.6, min(0.6, (p.get("ovr", 50) - avg_ovr) / 40.0)) * _w
        base += pstat.get("goals", 0) * 0.75
        base += pstat.get("assists", 0) * 0.4
        base += max(0, pstat.get("shots_on", 0) - pstat.get("goals", 0)) * 0.05
        if is_gk:
            base += pstat.get("saves", 0) * 0.12
            conceded = pstat.get("goals_conceded", 0)
            if clean_sheet:
                base += 0.5 * _w
            base -= max(0, conceded - 1) * 0.15
        elif clean_sheet:
            # 필드 플레이어도 완봉승엔 소폭 가산(수비 기여를 개인 이벤트
            # 없이도 어느 정도 반영 — 실제 수비 기여도는 못 따로 추적함).
            base += 0.15 * _w
        base += rng.gauss(0, 0.25)
        e = {
            "id": p.get("id"), "name": _display_name(p), "position": label,
            "ovr": p.get("ovr", 50),
            "goals": pstat.get("goals", 0), "assists": pstat.get("assists", 0),
            "shots": pstat.get("shots", 0), "shots_on": pstat.get("shots_on", 0),
            "saves": pstat.get("saves", 0) if is_gk else 0,
            "is_gk": is_gk,
            "rating": round(max(3.0, min(10.0, base)), 1),
        }
        if appear:
            _on_d = display_minute(on_min) if on_min else 0
            _off_d = display_minute(off_min) if off_min is not None else None
            e["started"] = bool(started)
            # 출전시간은 "표시 분" 기준으로 센다 — 내부 96분/128분을 90으로
            # 환산하면 연장 경기에서 120분 뛴 선수가 90분으로 찍힌다.
            e["minutes"] = max(0, (_off_d if _off_d is not None else 0) - _on_d)
            e["on_min"] = _on_d
            e["off_min"] = _off_d
            e["subbed_in"] = (not started)
            e["subbed_out"] = bool(off_min is not None and off_min < _tot)
        return e

    out = []
    for i, p in enumerate(lineup):
        if p is None:
            out.append(None)
            continue
        label = labels[i] if i < len(labels) else p.get("position", "CM")
        ap = (appear or {}).get(_pkey(p)) or {}
        on_min = ap.get("on", 0) or 0
        off_min = ap.get("off", _tot)
        if off_min is None:
            off_min = _tot
        mins = max(0, off_min - on_min) if appear else _tot
        out.append(_entry(p, label, (p is gk), mins, True, on_min, off_min))
    for ap in subs_extra:
        p = ap["player"]
        if p is None:
            continue
        on_min = ap.get("on", 0) or 0
        off_min = ap.get("off", _tot)
        if off_min is None:
            off_min = _tot
        out.append(_entry(p, ap.get("slot") or p.get("position", "CM"),
                          False, max(0, off_min - on_min), False, on_min, off_min))
    return out


def simulate_tactical_match(home_lineup, away_lineup, home_boost=0.0, away_boost=0.0,
                             home_boost_position=None, away_boost_position=None,
                             home_adv=3.0, seed=None,
                             home_formation=None, away_formation=None,
                             home_bench=None, away_bench=None,
                             extra_time=False, agg_home=0, agg_away=0,
                             home_style=None, away_style=None):
    """포메이션 매치업을 실제로 계산해서 90분(+추가시간, 필요하면 연장)
    경기를 시뮬레이션한다.

    [2026-09 확장 — 체력·교체·연장] 신민용 확정 설계:
        home_bench/away_bench: 교체 후보(벤치) 선수 dict 리스트. 넘기면
            경기 중 체력 저하/스코어 상황에 따라 AI가 실제로 교체를 한다
            (5명/3회, 연장 시 +1회, 하프타임·연장 하프타임은 기회 무소모).
            안 넘기면 교체 없이 기존과 동일하게 동작한다.
        extra_time: True면 정규시간 종료 시 동점(또는 합산 동률)일 때
            연장 전반 15분 + 연장 후반 15분을 실제로 더 돌린다. 리그는
            False, 단판 KO는 True.
        agg_home/agg_away: 2차전 합산 방식일 때 1차전까지의 누적 스코어.
            연장 진입 판정은 "이 경기 스코어"가 아니라 "합산"으로 한다 —
            예: 1차전 A 1-0 B → 2차전에서 agg_home/agg_away를 넘기면
            2차전 90분 후 합산 동률일 때만 연장.

    Args:
        home_lineup/away_lineup: FORMATION_SLOTS 순서의 선수 dict 리스트
            (match_sim.match_flow._select_lineup()의 반환값 그대로 넣으면 됨).
            None 슬롯 허용(그 자리는 그냥 빈 것으로 취급).
        home_boost/away_boost: 그 팀에 얹을 전역 보정(내 에이스 효과 등).
        home_boost_position/away_boost_position: [2026-07 신설] 그 보정을
            받는 선수(=나)의 포지션. POSITION_BOOST_WEIGHTS로 보정이 실제
            그 포지션이 영향력을 행사하는 채널(공격/수비/중원/GK)에 집중
            되도록 분배한다. None이면 기존 방식(포지션 무관 균등 분배)으로
            폴백.
        home_adv: 홈 이점(중원 퀄리티에 가산).
        seed: 지정하면 결정론적 재현.
        home_formation/away_formation: [2026-09 신설, 신민용 리포트: "포메이션이
            5-2-3인데 라인업 화면엔 4-2-2-2로 뜬다"] home_lineup/away_lineup을
            실제로 뽑을 때 쓴 FORMATION_SLOTS 키(예: "5-2-3"). 넘기면 구역
            계산(_TeamModel/_assign_zones)과 라인업 평점의 포지션 라벨이
            전부 이 실제 포메이션 기준으로 맞춰진다. None이면(기존 호출부·
            항상 4-4-2뿐인 국제대회 등) 예전과 동일하게 4-4-2 라벨로
            폴백한다 — 하위 호환 100% 유지.

    Returns:
        {"home_score", "away_score", "home_stats", "away_stats",
         "home_stats_detail", "away_stats_detail", "possession_log", ...}
        home_stats/away_stats: 실제 중계화면에 보여줄 "표시용" 10개 —
            {"poss","shots","shots_on","corners","fouls","pass_acc",
             "offsides","yellow_cards","red_cards","saves"}. pass_acc는
             실제 패스 시도/성공 집계(0으로 나뉠 일 없이 항상 0.0~1.0
             사이 값)로 항상 채워진다.
        home_stats_detail/away_stats_detail: [2026-09 신설] 화면엔 기본
            노출 안 하고 저장만 해두는 세부 통계 — _new_stats_detail()의
            키 그대로(총패스/성공패스, 크로스, 태클, 가로채기, 클리어링,
            블록, 공중볼, 드리블, 볼탈취/상실, 서드·박스 진입, 빅찬스,
            xG/xA, 골대, 프리킥, PK, 선방률).
        possession_log: match_flow.generate_possession_log()와 같은 레코드
            형식([{"min","team","zone","outcome","me","text"}, ...]) — 이번
            단계에서는 팀 결과(스코어/통계)만 이 로그의 골/슈팅 합계와
            일치시키고, 화면 재생은 여전히 match_flow가 만드는 필러로
            채운다(시각화까지 이 로그를 직접 쓰는 건 다음 단계 작업).
    """
    rng = random.Random(seed) if seed is not None else random

    home_slots = away_slots = None
    if home_formation or away_formation:
        from constants import FORMATION_SLOTS
        if home_formation:
            home_slots = FORMATION_SLOTS.get(home_formation)
        if away_formation:
            away_slots = FORMATION_SLOTS.get(away_formation)

    # [신규 — 경기 당일 컨디션] 매 분마다 실력 평균으로 수렴하는 구조라,
    # 분 단위 시뮬레이션만으로는 실제 축구의 "약팀이 어쩌다 강팀을 잡는"
    # 이변이 거의 안 나왔다(실측: OVR 15 차이에도 패배 확률 3%로, 기존
    # 확률표의 16%보다 훨씬 낮았음). 경기 시작 전에 딱 한 번 양팀에
    # "그날의 컨디션" 오차를 부여해서, 그 경기 내내 일관되게 유지되는
    # 변동성을 추가한다 — 매 분 독립적으로 흔들리는 잡음과 달리, 이건
    # "그 팀이 그날 유독 잘 풀리거나 안 풀리는" 것과 같아서 이변 가능성을
    # 만들어준다.
    home_form = rng.gauss(0, _MATCH_FORM_SIGMA)
    away_form = rng.gauss(0, _MATCH_FORM_SIGMA)
    # [2026-09 구조변경] 예전엔 여기서 _TeamModel을 한 번 만들고 컨디션을
    # 얹은 뒤 경기 내내 그대로 썼다. 이제 체력 저하와 교체 때문에 팀 모델이
    # 경기 중에 바뀌므로, 모델 생성 + 컨디션 가산을 _rebuild_models()로
    # 묶어서 체크포인트마다 다시 부른다. _TeamModel은 rng를 전혀 쓰지 않으
    # 므로 생성 시점을 컨디션 추출 뒤로 옮겨도 시드 재현성에는 영향이 없다.
    home = away = None

    home_stats = {"shots": 0, "shots_on": 0, "corners": 0, "fouls": 0,
                  "offsides": 0, "yellow_cards": 0, "red_cards": 0, "saves": 0}
    away_stats = {"shots": 0, "shots_on": 0, "corners": 0, "fouls": 0,
                  "offsides": 0, "yellow_cards": 0, "red_cards": 0, "saves": 0}
    # [2026-09 신설] "표시용" 10개 옆에 별도로 두는 세부 통계 그릇 —
    # _new_stats_detail() 참고.
    home_detail = _new_stats_detail()
    away_detail = _new_stats_detail()
    # [2026-08 신설] 선수 id(또는 id 없는 폴백 선수는 파이썬 id()) ->
    # {"shots","shots_on","goals","assists","saves","goals_conceded"} 누적.
    # _resolve_shot이 채우고, 경기 종료 후 아래에서 평점으로 환산한다.
    home_player_stats = {}
    away_player_stats = {}
    home_score = away_score = 0
    plog = []

    home_mid_total = away_mid_total = 0.0
    home_poss_minutes = 0
    home_zoned = away_zoned = None
    p_home_poss = 0.5

    # 부상시간 포함 대략 96분 정도로(정규시간). 연장은 그 뒤에 별도 구간.
    total_minutes = REG_MINUTES

    # ── [2026-09 신설] 경기 내부 체력 + 교체 상태 ────────────────────
    # 체력은 선수 dict를 건드리지 않고 _Stamina가 키 기반으로 따로 관리한다
    # (호출부가 공유 dict를 넘길 수 있으므로). 벤치를 안 넘긴 호출부는
    # 교체가 일어나지 않고, 체력 저하만 적용된다.
    stam = _Stamina(enabled=bool(STAMINA_MODEL))
    _sides = {
        "home": {"cur": list(home_lineup), "slots": home_slots, "is_home": True,
                 "boost": home_boost, "bpos": home_boost_position,
                 "bench": [p for p in (home_bench or []) if p],
                 "subs": [], "used": 0, "windows": SUBS_MAX_WINDOWS, "appear": {}},
        "away": {"cur": list(away_lineup), "slots": away_slots, "is_home": False,
                 "boost": away_boost, "bpos": away_boost_position,
                 "bench": [p for p in (away_bench or []) if p],
                 "subs": [], "used": 0, "windows": SUBS_MAX_WINDOWS, "appear": {}},
    }
    for _sk, _sv in _sides.items():
        _labels = _sv["slots"] or _FALLBACK_SLOTS
        for _i, _p in enumerate(_sv["cur"]):
            if _p is None:
                continue
            stam.ensure(_p)
            _sv["appear"][_pkey(_p)] = {
                "player": _p, "slot": (_labels[_i] if _i < len(_labels) else
                                       _p.get("position", "CM")),
                "slot_idx": _i, "on": 0, "off": None, "started": True}
        for _p in _sv["bench"]:
            stam.ensure(_p)

    def _rebuild_models():
        """현재 라인업/체력/스코어 상황으로 팀 모델과 레인 캐시를 다시 만든다.

        예전엔 경기 시작 전 딱 한 번만 계산해서 루프 내내 재사용했다(성능
        최적화). 이제 체력 저하·교체 때문에 값이 실제로 변하므로 체크포인트
        (15분마다 + 교체 직후)에서만 다시 만든다 — 경기당 10회 정도라
        분당 재계산(96회)으로 되돌아가지는 않는다."""
        nonlocal home, away, home_zoned, away_zoned
        nonlocal home_mid_total, away_mid_total, p_home_poss
        nonlocal home_lanes, home_weights, home_quality_by_lane
        nonlocal home_pool_by_lane, home_mult_by_lane
        nonlocal away_lanes, away_weights, away_quality_by_lane
        nonlocal away_pool_by_lane, away_mult_by_lane
        home = _TeamModel(_sides["home"]["cur"], True, boost=home_boost,
                          boost_position=home_boost_position, slot_labels=home_slots,
                          perf=stam.perf)
        away = _TeamModel(_sides["away"]["cur"], False, boost=away_boost,
                          boost_position=away_boost_position, slot_labels=away_slots,
                          perf=stam.perf)
        for _ln in LANES:
            home.att[_ln] += home_form * 0.6
            home.dfn[_ln] += home_form * 0.6
            away.att[_ln] += away_form * 0.6
            away.dfn[_ln] += away_form * 0.6
        home.mid += home_form
        away.mid += away_form
        home.gk_q += home_form * 0.5
        away.gk_q += away_form * 0.5
        # [2026-09 신설, 신민용 설계: "1골 넣은 후 눕기 위해 수비수 투입,
        # 지고 있으면 공격 투입"] 교체로 선수만 바꾸는 게 아니라 팀이 실제로
        # 자세를 바꾸는 부분. 후반 중반 이후 스코어 상황에 따라 공격/수비
        # 퀄리티를 아주 작게(±1.5) 밀어준다 — 큰 값을 주면 기존 득점
        # baseline이 흔들리므로 의도적으로 약하게 잡았다.
        _diff = home_score - away_score
        if _cur_minute >= 70 and _diff != 0:
            _chase = "away" if _diff > 0 else "home"     # 지고 있는 쪽
            _lead = "home" if _diff > 0 else "away"
            _cm = home if _chase == "home" else away
            _lm = home if _lead == "home" else away
            for _ln in LANES:
                _cm.att[_ln] += 1.5
                _cm.dfn[_ln] -= 1.5
                if _cur_minute >= 80:
                    _lm.att[_ln] -= 1.0
                    _lm.dfn[_ln] += 1.5
        home_zoned, away_zoned = home.zoned, away.zoned
        home_mid_total = home.mid + home_adv
        away_mid_total = away.mid
        # [2026-09 — 감독 시스템 ②단계] 감독 성향 보정. _style_edges가
        # (점유 보정, 기회품질 보정)을 각 팀 관점으로 돌려준다 — 성향이
        # 없으면(None) 전부 0.0이라 예전과 비트 단위로 같은 경기가 된다.
        # 점유 보정은 p_home_poss에만, 기회품질 보정은 레인 퀄리티에만
        # 들어간다(두 축을 섞지 않는다 — 섞으면 "점유율이 올라서 기회도
        # 좋아지는" 이중 계산이 된다).
        _h_poss_edge, _h_q_edge = _style_edges(home_style, away_style)
        _a_poss_edge, _a_q_edge = _style_edges(away_style, home_style)
        p_home_poss = _sigmoid(
            (home_mid_total - away_mid_total + _h_poss_edge - _a_poss_edge) / 16.0)
        (home_lanes, home_weights, home_quality_by_lane, home_pool_by_lane,
         home_mult_by_lane) = _prep_side(home, away, home_zoned,
                                         home_mid_total - away_mid_total, _h_q_edge)
        (away_lanes, away_weights, away_quality_by_lane, away_pool_by_lane,
         away_mult_by_lane) = _prep_side(away, home, away_zoned,
                                         away_mid_total - home_mid_total, _a_q_edge)

    home_lanes = home_weights = home_quality_by_lane = None
    home_pool_by_lane = home_mult_by_lane = None
    away_lanes = away_weights = away_quality_by_lane = None
    away_pool_by_lane = away_mult_by_lane = None
    _cur_minute = 0

    def _prep_side(atk, dfn_opp, zoned_atk, mid_edge, style_q_edge=0.0):
        lane_scores = {ln: max(1.0, atk.att[ln] - dfn_opp.dfn[ln] + 50.0) for ln in LANES}
        lanes, weights = zip(*lane_scores.items())
        # [2026-09 버그수정, 신민용+GPT 실측: "90 vs 99(9점 차)만 돼도
        # 변환 없이도 원정승 82%·평균 4골 실점 — 극단 매치업이 이미
        # 결정론적"] /11.0은 raw stat 차이만으로도 이미 과민했다. effective_
        # stat 변환(아래 _resolve_shot의 결정력 지점)과는 별개 문제 — 이
        # 레인 퀄리티는 "기회 빈도"를 정하는 곳이라 raw stat을 그대로 쓰되
        # (증폭은 결정력 지점 한 곳에만 몰아준다는 원칙, effective_stat
        # 정의부 주석 참고) 나눗값만 24.0으로 넓혀 민감도를 낮췄다 — 실측
        # 검증(90/95/99/102/105 극단 매치업, 85+스타1명 캐리 시나리오)
        # 완료.
        # [2026-09 신설, 신민용+GPT 설계: "미드필드가 점유율만 만들고
        # 기회 품질엔 안 들어간다"] mid_edge(이 팀 미드필드 - 상대 미드필드)
        # 를 아주 작은 계수(0.15)로만 diff에 더한다 — "미드필드+10 →
        # 슈팅확률+30%" 같은 직접 보정은 피하고, 이미 있는 공격/수비 퀄리티
        # 격차 위에 미드필드 우위만큼만 살짝 얹는다(점유율 배분과는 별개
        # 경로 — 점유율은 위 p_home_poss가 이미 담당).
        # style_q_edge: 감독 성향이 이 팀 기회 품질에 주는 보정(OVR 환산).
        # 성향이 없으면 0.0이라 예전 식과 완전히 동일하다.
        quality_by_lane = {ln: _sigmoid((atk.att[ln] - dfn_opp.dfn[ln]
                                         + mid_edge * 0.15 + style_q_edge) / 24.0)
                           for ln in LANES}
        att_pool_all = [z["player"] for z in zoned_atk if z["third"] == "ATT"]
        # [2026-09 버그수정 — 득점자가 한 명에게 몰리는 문제]
        # 예전 슈터 풀은 "그 레인 + ATT third"뿐이었다. 그런데 4-2-3-1이나
        # 4-3-3처럼 최전방이 레인마다 1명인 포메이션에선 그 풀의 크기가
        # 정확히 1이 되어, 그 레인에서 나온 모든 슈팅이 언제나 같은 선수
        # 것이 됐다. C 레인 가중치가 보통 가장 높으므로 결과적으로 팀 골이
        # 최전방 한 명에게 거의 전부 쏠렸다 — 실측(tools/scorer_spread_qa.py,
        # 맨유 200경기): 최다득점 슬롯이 팀 골의 97.6%, 2골 이상 낸 135개
        # 팀-경기 중 126건이 "득점자 1명". 게다가 풀 크기가 1이면 어시스트
        # 판정(len(shooter_pool) > 1)도 통째로 건너뛰어져 도움이 거의 안
        # 붙었다.
        #
        # 그래서 풀을 넓히고, 대신 포지션별 가중 배수(mult)를 같이 넘긴다 —
        # 최전방 같은 레인이 1.0, 같은 레인 미드필더 0.42, 옆 레인 공격수
        # 0.30, 옆 레인 미드필더 0.12. 슈터 선택은 여전히 결정력(shooting)
        # 가중이 주도하므로 스트라이커가 최다 득점자인 건 그대로다.
        # rng 호출 '횟수/순서'는 슈팅 판정 자체에선 그대로다(rng.choices는
        # 풀 크기와 무관하게 1회) — 어시스트 판정만 이제 제대로 들어간다.
        _ATT_SAME, _MID_SAME, _ATT_ADJ, _MID_ADJ = 1.0, 0.42, 0.30, 0.12
        pool_by_lane = {}
        mult_by_lane = {}
        for ln in LANES:
            if not WIDE_SHOOTER_POOL:      # A/B 비교용 — 예전(좁은) 풀 재현
                pool = [z["player"] for z in zoned_atk
                        if z["lane"] == ln and z["third"] == "ATT"]
                pool_by_lane[ln] = pool if pool else att_pool_all
                mult_by_lane[ln] = None
                continue
            pool, mults = [], []
            for z in zoned_atk:
                if z["third"] == "ATT":
                    m = _ATT_SAME if z["lane"] == ln else _ATT_ADJ
                elif z["third"] == "MID":
                    m = _MID_SAME if z["lane"] == ln else _MID_ADJ
                else:
                    continue   # 수비수는 세트피스 밖에서는 슈터 풀에 안 넣는다
                pool.append(z["player"])
                mults.append(m)
            if not pool:
                pool, mults = list(att_pool_all), [1.0] * len(att_pool_all)
            pool_by_lane[ln] = pool
            mult_by_lane[ln] = mults
        return lanes, weights, quality_by_lane, pool_by_lane, mult_by_lane

    # ── [2026-09 신설] AI 교체 판단 ───────────────────────────────────
    _SLOT_PEN = (0, 5, 9, 13, 13)

    def _slot_fit(p, slot):
        """그 슬롯에서의 실질 경쟁력 — _select_lineup의 점수 계산과 같은
        발상(포지션 적합도 페널티를 OVR에서 뺀다)."""
        from constants import POSITION_COMPAT
        compat = POSITION_COMPAT.get(p.get("position"), [p.get("position")])
        try:
            idx = compat.index(slot)
        except ValueError:
            idx = len(_SLOT_PEN) - 1
        return (p.get("ovr") or 50) - _SLOT_PEN[min(idx, len(_SLOT_PEN) - 1)]

    def _try_subs(minute, free_window, is_extra):
        """양 팀의 교체를 한 번 판단한다. 실제로 교체가 일어나면 True.

        판단 근거(신민용 설계): 현재 체력 + 경기 시간 + 포지션 + 스코어
        상황 + 벤치 선수의 실질 경쟁력. 단순히 "N분이면 무조건 교체"가
        아니라, 지친 선수를 더 나은/신선한 선수로 바꿀 수 있을 때만 한다.
        """
        did = False
        for sk in ("home", "away"):
            sv = _sides[sk]
            if not sv["bench"] or sv["used"] >= SUBS_MAX_PLAYERS:
                continue
            if not free_window and sv["windows"] <= 0:
                continue
            labels = sv["slots"] or _FALLBACK_SLOTS
            my_sc = home_score if sk == "home" else away_score
            op_sc = away_score if sk == "home" else home_score
            diff = my_sc - op_sc
            # 스코어 상황에 따른 "어느 라인을 바꾸고 싶은가".
            want = None
            if diff < 0 and minute >= 60:
                want = "attack"      # 지고 있다 → 공격 자원 투입
            elif diff > 0 and minute >= 75:
                want = "defend"      # 이기고 있다 → 수비/중원 보강
            # 후보: 체력이 낮은 순. GK는 교체 대상에서 제외(부상 미구현).
            cands = []
            for i, p in enumerate(sv["cur"]):
                if p is None:
                    continue
                slot = labels[i] if i < len(labels) else p.get("position", "CM")
                if slot == "GK" or p.get("position") == "GK":
                    continue
                _ap = sv["appear"].get(_pkey(p)) or {}
                # 방금 투입한 선수를 다시 빼지 않는다(실측에서 62분 투입 →
                # 68분 교체아웃이 나왔다). 20분은 뛰게 둔다.
                if not _ap.get("started", True) and minute - (_ap.get("on") or 0) < 20:
                    continue
                st = stam.get(p)
                third = _third_of(_POS_XY.get(slot, (0.3, 0.5))[0])
                tired = st < SUB_STAMINA_TRIGGER
                if not tired:
                    # 지치지 않은 선수는 "전술적 이유"가 있을 때만 후보다 —
                    # 지고 있어서 수비/중원을 빼거나, 이기고 있어서 공격수를
                    # 빼는 경우. 그 외에는 순수 실력 업그레이드만으로
                    # 멀쩡한 주전을 빼지 않는다.
                    if want == "attack" and third in ("DEF", "MID"):
                        pass
                    elif want == "defend" and third == "ATT":
                        pass
                    else:
                        continue
                urge = (SUB_STAMINA_TRIGGER - st)
                if want == "attack" and third in ("DEF", "MID"):
                    urge += 10.0
                elif want == "defend" and third == "ATT":
                    urge += 10.0
                cands.append((urge, st, i, p, slot, third, tired))
            cands.sort(key=lambda t: (-t[0], t[1], t[2]))
            n_this_window = 0
            for urge, st, idx, out_p, slot, third, tired in cands:
                if n_this_window >= 2 or sv["used"] >= SUBS_MAX_PLAYERS:
                    break
                out_eff = _slot_fit(out_p, slot) * stam.perf(out_p)
                best_in, best_score = None, -1e9
                for cand in sv["bench"]:
                    if cand.get("position") == "GK":
                        continue
                    sc = _slot_fit(cand, slot)
                    if want == "attack":
                        sc += 3.0 * (1.0 if _third_of(
                            _POS_XY.get(cand.get("position"), (0.3, 0.5))[0]) == "ATT" else 0.0)
                    if sc > best_score:
                        best_in, best_score = cand, sc
                if best_in is None:
                    continue
                ok = (best_score >= out_eff - 3.0) if tired else (best_score > out_eff - 6.0)
                if not ok:
                    continue
                # 실제 교체 실행.
                sv["cur"][idx] = best_in
                sv["bench"].remove(best_in)
                sv["used"] += 1
                n_this_window += 1
                ap = sv["appear"].get(_pkey(out_p))
                if ap is not None:
                    ap["off"] = minute
                sv["appear"][_pkey(best_in)] = {
                    "player": best_in, "slot": slot, "slot_idx": idx,
                    "on": minute, "off": None, "started": False}
                sv["subs"].append({
                    "min": int(minute), "disp": display_minute(minute),
                    "slot": slot,
                    "out_id": out_p.get("id"), "out_name": out_p.get("name"),
                    "out_stamina": round(st, 1),
                    "in_id": best_in.get("id"), "in_name": best_in.get("name"),
                    "reason": ("tired" if tired else (want or "quality")),
                    "extra_time": bool(is_extra),
                })
                plog.append({"min": float(minute), "team": sk, "zone": "mid", "lane": "C",
                             "outcome": "sub", "me": False, "text": None,
                             "in_id": best_in.get("id"), "out_id": out_p.get("id")})
                did = True
            if n_this_window and not free_window:
                sv["windows"] -= 1
        return did

    _rebuild_models()

    def _run_phase(lo, hi, is_extra=False):
        nonlocal home_score, away_score, home_poss_minutes, _cur_minute
        for minute in range(lo, hi + 1):
            _cur_minute = minute
            _minute_body(minute, is_extra)

    def _minute_body(minute, is_extra):
        nonlocal home_score, away_score, home_poss_minutes
        poss_home = rng.random() < p_home_poss
        if poss_home:
            home_poss_minutes += 1
            side, dfn_opp = "home", away
            lanes, weights = home_lanes, home_weights
            quality_by_lane, pool_by_lane = home_quality_by_lane, home_pool_by_lane
            mult_by_lane = home_mult_by_lane
        else:
            side, dfn_opp = "away", home
            lanes, weights = away_lanes, away_weights
            quality_by_lane, pool_by_lane = away_quality_by_lane, away_pool_by_lane
            mult_by_lane = away_mult_by_lane

        lane = rng.choices(lanes, weights=weights, k=1)[0]
        quality = quality_by_lane[lane]
        atk_detail = home_detail if side == "home" else away_detail
        dfn_detail = away_detail if side == "home" else home_detail

        # [2026-09 신설] 패스는 슈팅/코너/파울/빌드업과 무관하게 "이번 분에
        # 볼을 가진 팀"이면 항상 몇 번씩 오간다고 보고 매 분 누적한다(기존
        # shot_chance/corner_chance/foul_chance 판정과는 완전히 별개 —
        # 결과 결정 로직은 안 건드림). 성공률은 이 레인의 quality(공격측이
        # 수비를 얼마나 압도하는가)에 연동 — 밀어붙이는 팀일수록 패스가
        # 더 잘 이어진다는 감각.
        _pass_n = 3 + rng.randrange(0, 4)          # 분당 3~6회
        _pass_p = max(0.55, min(0.95, 0.68 + (quality - 0.5) * 0.35))
        _pass_ok = int(round(_pass_n * _pass_p + rng.uniform(-0.4, 0.4)))
        _pass_ok = max(0, min(_pass_n, _pass_ok))
        atk_detail["passes"] += _pass_n
        atk_detail["passes_ok"] += _pass_ok

        roll = rng.random()
        shot_chance = 0.075 + quality * 0.23             # 대략 7.5~30.5%
        corner_chance = 0.02 + quality * 0.035          # 걷어낸 공이 라인 밖으로
        foul_chance = 0.035 + (1.0 - quality) * 0.03    # 밀릴 때 거칠게 끊는 경우

        shooter_pool = pool_by_lane[lane]
        shooter_mults = mult_by_lane[lane]

        if roll < shot_chance:
            scorer_side = _resolve_shot(
                rng, side, lane, minute, shooter_pool, dfn_opp.gk, dfn_opp.gk_q,
                home_stats, away_stats, home_player_stats, away_player_stats, plog,
                home_detail, away_detail, shooter_mults=shooter_mults)
            if scorer_side == "home":
                home_score += 1
            elif scorer_side == "away":
                away_score += 1
        elif roll < shot_chance + corner_chance:
            (home_stats if side == "home" else away_stats)["corners"] += 1
            atk_detail["final_third_entries"] += 1
            plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                         "outcome": "corner", "me": False, "text": None})
        elif roll < shot_chance + corner_chance + foul_chance:
            fouling_side = "away" if side == "home" else "home"
            (home_stats if fouling_side == "home" else away_stats)["fouls"] += 1
            fouled_detail = atk_detail    # 파울을 당한(=프리킥/PK를 얻는) 쪽
            # [2026-09 신설] 파울 하나를 프리킥/PK와 카드 유무로 세분화한다.
            # 공격측이 이미 그 레인을 크게 압도(quality 높음)하던 중 끊긴
            # 파울만 낮은 확률로 PK(박스 안 파울)로 승격 — 나머지는 프리킥.
            if quality > 0.68 and rng.random() < 0.10:
                fouled_detail["penalties"] += 1
            else:
                fouled_detail["free_kicks"] += 1
            _card_roll = rng.random()
            if _card_roll < 0.006:
                (home_stats if fouling_side == "home" else away_stats)["red_cards"] += 1
            elif _card_roll < 0.11:
                (home_stats if fouling_side == "home" else away_stats)["yellow_cards"] += 1
            plog.append({"min": float(minute), "team": fouling_side, "zone": "mid", "lane": lane,
                         "outcome": "foul", "me": False, "text": None})
        else:
            # [신규 — 필러 없는 진짜 로그] 예전엔 이 "특별한 일 없는" 분들이
            # match_flow의 무작위 필러(최대 24개, 실제 우세와 무관하게
            # 대충 배분)로 채워졌다. 이제는 이 시뮬레이션이 실제로 계산한
            # "이번 분에 어느 팀이 어느 레인/서드에서 우세했는가"를 그대로
            # 기록한다 — 90분 전체가 진짜 매치업 계산의 산출물이 된다.
            # zone(서드)은 quality(공격측이 그 레인에서 얼마나 우세했는지)
            # 로 판정: 크게 우세하면 상대 진영 깊숙이(att), 팽팽하면
            # 중원(mid), 밀리면 자기 진영(def)에 머문 것으로 본다.
            if quality > 0.62:
                zone = "att"
            elif quality < 0.38:
                zone = "def"
            else:
                zone = "mid"

            # [2026-09 신설] 이 "특별한 일 없는" 국면도 실제로 무슨 장면
            # 이었는지 세부 통계로 나눠 둔다 — possession_log 텍스트/필러
            # 로직도, 위 shot/corner/foul 판정도 안 건드리고 병렬로만
            # 누적한다.
            if zone == "att":
                atk_detail["final_third_entries"] += 1
                sub = rng.random()
                if lane != "C" and sub < 0.22:
                    atk_detail["crosses"] += 1
                    if rng.random() < (0.35 + quality * 0.25):
                        atk_detail["crosses_ok"] += 1
                elif sub < 0.40:
                    atk_detail["dribbles"] += 1
                    if rng.random() < (0.40 + quality * 0.30):
                        atk_detail["dribbles_ok"] += 1
                elif sub < 0.45:
                    # 침투 시도가 오프사이드로 끊김
                    (home_stats if side == "home" else away_stats)["offsides"] += 1
            elif zone == "def":
                # 공격측이 밀리는 국면 — 수비측이 볼을 따낸다.
                sub = rng.random()
                if sub < 0.30:
                    dfn_detail["tackles"] += 1
                    if rng.random() < (0.45 + (1.0 - quality) * 0.30):
                        dfn_detail["tackles_ok"] += 1
                        dfn_detail["turnovers_won"] += 1
                        atk_detail["turnovers_lost"] += 1
                elif sub < 0.50:
                    dfn_detail["interceptions"] += 1
                    dfn_detail["turnovers_won"] += 1
                    atk_detail["turnovers_lost"] += 1
                elif sub < 0.65:
                    dfn_detail["clearances"] += 1
            if rng.random() < 0.06:
                dfn_detail["aerial_duels"] += 1
                atk_detail["aerial_duels"] += 1
                if rng.random() < 0.5:
                    dfn_detail["aerial_duels_ok"] += 1
                else:
                    atk_detail["aerial_duels_ok"] += 1

            plog.append({"min": float(minute), "team": side, "zone": zone, "lane": lane,
                         "outcome": "buildup", "me": False, "text": None})

        # ── 분 종료: 체력 소모 → 필요하면 교체/모델 재계산 ──────────
        if stam.enabled:
            stam.drain_minute(home_zoned, poss_home, is_extra)
            stam.drain_minute(away_zoned, not poss_home, is_extra)
        _need_rebuild = (minute % 15 == 0)
        if minute in _SUB_DECISION_MINUTES:
            if _try_subs(minute, minute in _FREE_WINDOW_MINUTES, is_extra):
                _need_rebuild = True
        if _need_rebuild:
            _rebuild_models()

    # 교체를 판단하는 시점. 46분(하프타임)·97분(연장 시작)·113분(연장
    # 하프타임)은 실제 규칙대로 교체 기회를 소모하지 않는다.
    _FREE_WINDOW_MINUTES = frozenset({SUB_EARLIEST_MINUTE,
                                      REG_MINUTES + 1,
                                      REG_MINUTES + ET_HALF_MINUTES + 1})
    _SUB_DECISION_MINUTES = frozenset(
        {SUB_EARLIEST_MINUTE, 56, 62, 68, 74, 80, 86,
         REG_MINUTES + 1, REG_MINUTES + 8,
         REG_MINUTES + ET_HALF_MINUTES + 1, REG_MINUTES + ET_HALF_MINUTES + 8})

    # ── 정규시간 ────────────────────────────────────────────────────
    _run_phase(1, REG_MINUTES, is_extra=False)
    home_score_90, away_score_90 = home_score, away_score

    # ── 연장전 판정 ─────────────────────────────────────────────────
    # 신민용 확정 설계: 리그는 연장 없음(extra_time=False). 단판 KO는 이
    # 경기 스코어가 동점일 때. 2차전 합산 방식은 agg_*를 받아서 "합산이
    # 동률일 때만" — 2차전 자체가 동점인지를 보는 게 아니다.
    went_extra_time = False
    if extra_time:
        _tot_h = int(agg_home or 0) + home_score
        _tot_a = int(agg_away or 0) + away_score
        if _tot_h == _tot_a:
            went_extra_time = True
            total_minutes = REG_MINUTES + ET_HALF_MINUTES * 2
            # 연장 진입 직전 휴식 — 교체 기회 +1회(하프타임처럼 무소모인
            # 97분 판정과는 별개로, 경기 중 기회 자체를 한 번 더 준다).
            for _sv in _sides.values():
                _sv["windows"] += SUBS_EXTRA_TIME_WINDOWS
            _rebuild_models()
            _run_phase(REG_MINUTES + 1, REG_MINUTES + ET_HALF_MINUTES, is_extra=True)
            _run_phase(REG_MINUTES + ET_HALF_MINUTES + 1,
                       REG_MINUTES + ET_HALF_MINUTES * 2, is_extra=True)

    # 아직 경기장에 남아 있는 선수들의 출전 종료 시각을 확정.
    for _sv in _sides.values():
        for _ap in _sv["appear"].values():
            if _ap["off"] is None:
                _ap["off"] = total_minutes

    home_poss_pct = round(100.0 * home_poss_minutes / total_minutes)
    home_poss_pct = max(28, min(72, home_poss_pct))
    home_stats["poss"] = home_poss_pct
    away_stats["poss"] = 100 - home_poss_pct

    # [2026-09 신설] pass_acc는 team_stats(표시용)에 반드시 존재해야 하는
    # 값이라(MatchStatsPanel이 h_st['pass_acc']를 그대로 참조) 0으로
    # 나누는 경우까지 여기서 안전하게 처리해 확정한다. saves(표시용)는
    # 이 경기에서 뛴 GK들의 개인 saves 합으로 집계 — GK가 둘 이상 나올
    # 일은 없지만(교체 미시뮬레이션) 합으로 두면 어떤 경우에도 안전하다.
    home_stats["pass_acc"] = round(
        home_detail["passes_ok"] / home_detail["passes"], 3) if home_detail["passes"] else 0.0
    away_stats["pass_acc"] = round(
        away_detail["passes_ok"] / away_detail["passes"], 3) if away_detail["passes"] else 0.0
    home_stats["saves"] = sum(v.get("saves", 0) for v in home_player_stats.values())
    away_stats["saves"] = sum(v.get("saves", 0) for v in away_player_stats.values())

    # [선방률] 이 팀 GK가 막은 슈팅 / (막은 슈팅 + 실점) — 실점은 상대
    # 스코어와 동일(우리 골문에 들어간 공 = 상대가 넣은 골).
    home_conceded = away_score
    away_conceded = home_score
    home_detail["save_pct"] = round(
        home_stats["saves"] / (home_stats["saves"] + home_conceded), 3
    ) if (home_stats["saves"] + home_conceded) else 0.0
    away_detail["save_pct"] = round(
        away_stats["saves"] / (away_stats["saves"] + away_conceded), 3
    ) if (away_stats["saves"] + away_conceded) else 0.0

    plog.sort(key=lambda r: r["min"])
    # [2026-08 신설] 22명(양팀 라인업 슬롯 순서, 빈 슬롯은 None) 전원의
    # 개인 기록 + 평점 — ui/match_detail_dialog.py가 이 값이 있으면
    # FotMob 스타일 라인업+평점 화면을 보여준다(없으면 기존처럼 팀 단위
    # 통계만 보여주는 화면으로 자동 폴백).
    home_player_ratings = _build_player_ratings(
        home_lineup, home_player_stats, home_score, away_score, home.gk, rng, home_slots,
        appear=_sides["home"]["appear"], total_minutes=total_minutes)
    away_player_ratings = _build_player_ratings(
        away_lineup, away_player_stats, away_score, home_score, away.gk, rng, away_slots,
        appear=_sides["away"]["appear"], total_minutes=total_minutes)

    # [2026-09] 교체 기록의 선수 이름은 ai_players.name(내부 시드값, 보통
    # 빈 문자열)이 아니라 화면 표시명이어야 한다 — 방금 만든 평점표가 이미
    # 마스킹된 표시명을 갖고 있으므로 거기서 id로 끌어온다(ui/formation_
    # widget._mask_ai_names와 완전히 같은 규칙).
    for _sk, _prs in (("home", home_player_ratings), ("away", away_player_ratings)):
        _nm = {r["id"]: r["name"] for r in _prs if r and r.get("id") is not None}
        _rt = {r["id"]: r.get("rating") for r in _prs if r and r.get("id") is not None}
        for _s in _sides[_sk]["subs"]:
            _s["out_name"] = _nm.get(_s["out_id"]) or _s.get("out_name") or "?"
            _s["in_name"] = _nm.get(_s["in_id"]) or _s.get("in_name") or "?"
            _s["out_rating"] = _rt.get(_s["out_id"])
            _s["in_rating"] = _rt.get(_s["in_id"])
    return {
        "home_score": home_score, "away_score": away_score,
        # [2026-09 신설] 정규시간 스코어는 연장 결과로 덮어쓰지 않는다 —
        # 기록실에서 "90분 2-2 / 연장 3-2"로 구분해 보여줄 수 있어야 한다.
        "home_score_90": home_score_90, "away_score_90": away_score_90,
        "went_extra_time": went_extra_time,
        "home_subs": _sides["home"]["subs"], "away_subs": _sides["away"]["subs"],
        "total_minutes": total_minutes,
        "home_stats": home_stats, "away_stats": away_stats,
        "home_stats_detail": home_detail, "away_stats_detail": away_detail,
        "possession_log": plog,
        "home_player_ratings": home_player_ratings,
        "away_player_ratings": away_player_ratings,
    }


def merge_personal_events(plog, personal_events, my_side):
    """엔진이 만든 possession_log(전부 text=None)에 내 개인 서사(실제 골/
    도움/선방/파울/코너 텍스트)를 끼워 넣는다.

    설계 원칙: 실제 개인 이벤트가 벌어진 "분(minute)"은 이미 확정된
    사실이라 절대 옮기지 않는다 — 대신 같은 team/outcome을 가진 필러
    레코드 중 그 분에 가장 가까운 것 하나를 그 실제 시각으로 당겨와서
    text/me를 채운다. 그러면 팀 통계(그 outcome 총 개수)는 그대로 유지
    되면서, 실제로 있었던 사건은 정확한 순간에 표시된다.

    personal_events: [(minute, text), ...] — game_engine._player_perf가
    만든 개인 이벤트 목록. match_flow._classify_personal로 분류되는
    것만 처리하고(골/도움/실점/선방/파울/코너), 그 외 텍스트(부상,
    카드 등 possession과 무관한 것)는 이 함수가 손대지 않는다 —
    호출자가 그 텍스트를 timeline에 그대로 유지해야 한다.
    """
    from match_sim.match_flow import _classify_personal

    opp_side = "away" if my_side == "home" else "home"
    out = [dict(r) for r in plog]
    used_idx = set()

    kind_map = {
        "goal_for": (my_side, "goal", True),
        "goal_against": (opp_side, "goal", False),
        "miss_for": (my_side, "save", True),
        "save": (opp_side, "save", True),
    }
    for m, text in personal_events:
        kind = _classify_personal(text)
        if kind in kind_map:
            side, outcome, me_flag = kind_map[kind]
        elif kind == "foul":
            side = my_side if "우리 팀" in text else opp_side
            outcome, me_flag = "foul", False
        elif kind == "corner":
            side = my_side if "우리 팀" in text else opp_side
            outcome, me_flag = "corner", False
        else:
            continue

        candidates = [i for i, r in enumerate(out)
                      if i not in used_idx and r["team"] == side and r["outcome"] == outcome
                      and r["text"] is None]
        if not candidates:
            continue
        best = min(candidates, key=lambda i: abs(out[i]["min"] - float(m)))
        used_idx.add(best)
        out[best]["min"] = float(m)
        out[best]["text"] = text
        out[best]["me"] = me_flag

    out.sort(key=lambda r: r["min"])
    return out


def simulate_my_match(home_team_id, away_team_id, home_formation, away_formation,
                       home_boost=0.0, away_boost=0.0,
                       home_boost_position=None, away_boost_position=None,
                       home_adv=3.0, seed=None,
                       extra_time=False, agg_home=0, agg_away=0):
    """team_id 두 개만 받아서 로스터/포메이션 조회부터 시뮬레이션까지 전부
    처리하는 편의 함수. game_engine._simulate_match에서 이걸 하나만 호출하면
    된다.

    [2026-09] 벤치(교체 후보)도 여기서 같이 뽑아 넘긴다 — 리그 경기는
    extra_time=False 그대로, 단판 KO를 돌리는 대회 엔진은 extra_time=True
    (2차전 합산이면 agg_home/agg_away까지) 넘기면 연장까지 처리된다."""
    from match_sim.match_flow import _select_lineup, select_bench

    home_lineup = _select_lineup(home_team_id, home_formation)
    away_lineup = _select_lineup(away_team_id, away_formation)
    # [2026-09 — 감독 시스템 ②단계] 두 팀의 현재 감독 성향을 실어 보낸다.
    # 감독이 없거나 조회에 실패하면 None → 전술엔진이 예전과 똑같이 돈다.
    # 이 편의 함수를 쓰는 경로(리그·컵·챔스·클럽월드컵·국내슈퍼컵·승강PO)
    # 만 감독 보정을 받는다 — 국가대표 경기는 simulate_tactical_match를
    # 직접 호출하므로 자연히 제외된다(대표팀엔 클럽 감독이 없다).
    _hs = _as = None
    try:
        from database import get_team_manager
        _hs = get_team_manager(home_team_id)
        _as = get_team_manager(away_team_id)
    except Exception:
        pass
    return simulate_tactical_match(home_lineup, away_lineup, home_boost=home_boost,
                                    away_boost=away_boost,
                                    home_boost_position=home_boost_position,
                                    away_boost_position=away_boost_position,
                                    home_adv=home_adv, seed=seed,
                                    home_formation=home_formation,
                                    away_formation=away_formation,
                                    home_bench=select_bench(home_team_id, home_lineup),
                                    away_bench=select_bench(away_team_id, away_lineup),
                                    extra_time=extra_time,
                                    agg_home=agg_home, agg_away=agg_away,
                                    home_style=_hs, away_style=_as)