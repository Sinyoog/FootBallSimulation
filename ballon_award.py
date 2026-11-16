# -*- coding: utf-8 -*-
"""[2026-11 신설, 신민용 확정] 발롱도르 전용 채점 — 순수 계산 모듈(DB·난수 없음).

아직 수상 로직(_save_ballon_dor_top30)에 연결하지 않았다. 단위 테스트(tools/test_ballon_score.py)와 세이브 측정
(tools/ballon_check.py)을 확인받은 뒤 연결한다. 데이터 수집은 연결 단계에서 game_engine 쪽이 맡는다.

엔진 구조: AI 필드 선수의 골·도움은 선수 OVR과 무관하게 포지션 기대치 × 팀 강도 × 역할 × 시즌 운으로 만들어지고
(game_engine._estimate_ai_season "OVR 70이든 99든 같은 조건"), OVR은 평점(6.0 + (OVR−60)/35 + 골·도움 소량)에만
들어간다. 그래서 개인 점수를 '시즌 OVR(능력)'과 '골·도움 생산성(활약)'으로 나눠 평가한다.

배점(10점 만점, 2026-11 확정 — 측정 결과를 보고 사후 조정하지 않는다)
  개인 7.0 = 능력 1.75(개인 7.0의 25%, 시즌 OVR 87 → 0, 99 → 1) + 생산성 5.25
    생산성 = 0.5 × 포지션 기대 대비 + 0.5 × 공격 생산량 — 공식 대회 전부(아래 COMP_IMP)
      대회 가치 = 중요도 × 수준. 리그 1, 국내컵 0.25, 대륙 클럽 대회 1 × 대회 가중치(_CLUB_COMP_WEIGHT), 대륙 슈퍼컵
        0.2 × 그 대륙 챔스 가중치, 국가대표 공식 대회 1 × 대회 수준(수집 쪽 표: 월드컵 1.0 · 대륙 선수권 0.6 × 대륙
        비율 · U-23 올림픽 0.26 · 월드컵 예선 0.20 · 대륙 선수권 예선 0.15 · 지역컵 0.10, 친선전 제외).
        중요도는 기존 게임 점수표(트로피 점수·기존 발롱 생산성)를 옮긴 값이다.
      네 요소는 한 번씩만 들어간다(3차 확정, 신민용):
        · 포지션 기대 대비 = 포지션 기대치(득점 환경 포함) 대비 성과. 리그 수준은 넣지 않는다.
        · 공격 생산량 = (실제 골 + 0.75 × 도움) × 리그 득점 계수^−0.5 × 대회 가치. 리그 수준은 여기서 한 번만
          (리그·국내컵 몫). 득점 환경 보정은 절반 강도(4차 확정, 신민용 — 포르투갈 0.72의 30골 = PL 35.4골).
        · 득점 환경 = 리그별 득점 계수(constants.get_goal_env_mult)와 대회 득점 환경(리그 대비 측정값). 리그 수준과
          무관하게 곱한다(리그 수준에 비례시키지 않음).
        · 팀 공격력(4차 확정) = 소속팀 리그 경기당 득점 ÷ 그 리그 평균. AI 골·도움이 팀 득점에 비례해 만들어지므로
          포지션 기대 대비의 국내(리그·국내컵) 기대치에만 팀 공격력^0.25를 곱한다(1.7배 팀 → 1.14배, 확정). 실제 골·도움,
          공격 생산량, 팀 성적·우승 점수는 그대로. 내 선수도 같은 값(소속팀 리그 기록)을 쓴다.
        · 리그 수준 = league_power ÷ 그 시즌 최고 리그(0.5~1) — 개인은 공격 생산량에 1회, 팀은 리그·국내컵 성적에 1회.
      포지션 기대 대비: (Σ 가중 골+도움 + 10) ÷ (Σ 가중 기대치 + 10), 1.00 → 0 · 상한 = 후보 p99.5
        기대치 = 엔진과 같은 포지션 기준(constants.EST_POS_GOAL + EST_POS_ASSIST) × 리그 득점 환경 ×
                 (풀시즌/38)^0.35 의 경기당 값 × 그 대회 출전 경기 × 그 대회 득점 환경. 국내(리그·국내컵)는 중요도만,
                 그 밖의 대회는 수준 × 중요도를 분자·분모 둘 다에 곱한다 — 수준은 그 대회를 얼마나 크게 세는지만
                 정하고, 수준 낮은 대회에 나갔다고 기대 대비가 깎이지 않는다. +10은 기대치가 작은 포지션(CB·CDM 등)의
                 우연한 큰 비율을 줄이는 축소(10 × 리그 득점 환경 — 득점 환경 대비 같은 활약이면 같은 값).
      공격 생산량: Σ 대회 가치 × (골 + 0.75 × 도움) ÷ 득점 환경, 후보 p50 → 0 · p99.5 → 상한
        (득점 환경 = 리그 계수^0.5, 국내컵은 리그 계수^0.5 × 컵 환경, 그 밖의 대회는 그 대회 환경)
      세 척도(기대 대비·공격 생산량·GK 선방률)는 C안 완만한 상한(soft_band): p99.5까지 직선으로 0 → 0.9, 그 위는
      남은 0.1을 점점 작게 채운다(초과분이 척도 폭 하나만큼이면 0.95). 생산성 배점 5.25는 그대로.
      DF: 생산성 = 0.7 × (위 생산성) + 0.3 × 팀 수비 기록   (팀 수비 기록 = 팀 경기당 실점 2.0 → 0 · 0.94 → 1,
          × 리그 출전 비율) — AI에 없는 태클·인터셉트 같은 개인 수비 기록은 추정해서 넣지 않는다.
      GK: 기대 대비 몫 = 0.7 × 표준 환경 선방률(야신상과 같은 값) + 0.3 × 팀 수비 기록,
          공격 생산량 몫 = 골·도움 그대로(대개 0) — GK도 같은 0.5:0.5 혼합을 받는다.
  소속팀 성적 1.5 = 리그 0.4 + 국내컵 0.1 + 대륙대회 0.5 (단계표는 yashin_award와 공통, 각 대회 출전 비율 반영)
    리그 = 순위 점수 × 리그 수준 × 우승 난이도(시즌 전 전력 예상 순위 대비 실제 순위: 예상대로 0.8 → 예상보다
           팀 수의 25% 이상 위면 1.0), 국내컵 = 단계 점수 × 리그 수준
    → 팀 안에서는 개인 점수(능력+생산성+출전) 순위로 100% / 70% / 50% / 35% 배분(완전 동점은 같은 순위)
  국제대회 1.0 (월드컵·자기 대륙컵 중 높은 쪽, 출전 비율 반영) · 출전 기여 0.5 (리그 출전 70% → 0, 100% → 0.5)
  MVP·베스트11 가산점·포지션 보정 없음. Top30은 팀당 최대 3명.
내 선수와 AI 선수는 같은 입력(대회별 골·도움·출전, 시즌 OVR)으로 같은 공식을 쓴다 — 이 모듈에는 선수 종류를 가르는
분기가 없다. 내 선수만 가진 태클·키패스·실제 경기 평점은 입력에 없다."""

import math
from constants import EST_POS_GOAL, EST_POS_ASSIST
import yashin_award as _Y

W_PERSONAL = 7.0
OVR_SHARE = 0.25                       # 개인 7.0 중 능력(OVR) 비중 → 1.75점
W_TEAM, W_INTL, W_PART = 1.5, 1.0, 0.5
TEAM_SPLIT = {"league": 0.4, "cup": 0.1, "cont": 0.5}
PROD_MIX = 0.5                         # 포지션 기대 대비 : 공격 생산량 = 0.5 : 0.5
DEF_TEAM_SHARE = 0.3                   # DF·GK 생산성 안 팀 수비 기록 몫
REL_SHRINK = 10.0                      # 포지션 기대 대비 축소(골+도움 개수 × 리그 득점 환경)
GOAL_ENV_EXP = 0.5                     # 공격 생산량의 리그 득점 환경 보정 강도: × 득점 계수^−0.5 (4차 확정)
TEAM_ATTACK_EXP = 0.25                 # 포지션 기대 대비 국내 기대치 × 팀 공격력^0.25 (확정 — 5차 측정에서 0.5는 너무 가파름)
ASSIST_W = 0.75                        # 공격 생산량에서 도움 1개 = 골 0.75개

# 대회별 중요도(리그 = 1) — 기존 게임 점수표를 그대로 옮긴 값. 수준(level)은 대회마다 입력으로 받는다.
#   cup  0.25 : 국내컵 우승 1.0 ÷ 리그 우승 4.0(game_engine._cup_trophy_points / _league_rank_points, 둘 다 리그
#               국가 가중치를 곱함 → 수준 = 리그 수준)
#   cont 1.0  : 기존 발롱 생산성(_ballon_cl_bonus)이 대륙 클럽 대회 골·도움에 _CLUB_COMP_WEIGHT를 곱하던 방식
#               (유럽 챔스 = 1.0) → 수준 = 그 대회 가중치. 남미 컨퍼런스리그는 게임에 없는 대회라 쓰지 않는다.
#   sc   0.2  : 대륙 슈퍼컵 "챔스가 1이면 슈퍼컵 0.2"(_sc_trophy_points) → 수준 = 그 대륙 챔스 가중치
#   intl 1.0  : 국가대표 공식 대회(3차, 신민용 확정 — 친선전 제외). 수준은 수집 쪽(tools/ballon_check.intl_level):
#               월드컵 1.0(트로피 10 = 챔스 우승 10), 대륙 선수권 0.6 × 대륙 비율(_CONF_CUP_WEIGHT ÷ 10), U-23 올림픽
#               0.26(유럽 컨퍼런스리그 × 0.5), 월드컵 예선 0.20 · 대륙 선수권 예선 0.15 · 지역컵 0.10(3차 확정 초기값).
#               국가대표 팀 성적은 국제대회 1.0점에서 따로 본다.
#               [2026-11 신민용 확정] U-23 올림픽(본선·예선) 개인 기록은 골든보이·코파 트로피에만 넣는다 — 발롱도르·대륙
#               올해의 선수·세계 베스트 11 입력에서는 수집 쪽(game_engine._ballon_inputs youth=False)이 뺀다.
# 기존 점수표에 값이 없는 대회(클럽 월드컵·국내 슈퍼컵·3·4부컵·올림픽 예선)는 넣지 않는다(0) — 보류.
COMP_IMP = {"cup": 0.25, "cont": 1.0, "sc": 0.2, "intl": 1.0}

OVR_BAND = (87.0, 99.0)
REL_BAND = (1.00, 1.56)                # 상한 = 두 시드 후보 분포 p99.5(5차 입력 기준 — 4차 1.47)
VOL_BAND = (4.3, 38.4)                 # 후보 분포 p50 → p99.5(같은 기준, 4차와 같음)
TEAM_GC_BAND = (2.0, 0.94)             # 팀 경기당 실점 — 낮을수록 좋음
GK_SAVE_BAND = (0.750, 0.806)          # 표준 환경 선방률(축소 후) — GK 그룹 중앙값 → 0, p99.5 → 상한
PART_BAND = (0.70, 1.00)
SOFT_KNEE = 0.90                       # C안: p99.5 지점 = 척도의 90%
SOFT_HALF = 1.0                        # p99.5 초과분이 척도 폭(p50~p99.5) 하나만큼이면 남은 10%의 절반
LEVEL_FLOOR = 0.5                      # 리그 수준 하한(league_power 비율)
DIFF_EXPECTED, DIFF_SPAN = 0.8, 0.25   # 우승 난이도: 예상대로 0.8, 예상보다 팀 수의 25% 이상 위면 1.0
DECAY = (1.0, 0.7, 0.5, 0.35)          # 팀 안 개인 점수 순위별 소속팀 성적 배분
TOP_N, MAX_PER_TEAM = 30, 3
# [2026-11 신설, 신민용 확정] 골든보이(20세 이하 유망주 — score_golden). 같은 엔진에 두 가지만 다르다:
#   출전 점수 리그 출전 50% → 0, 100% → 만점(자격도 50%라 로테이션 유망주까지 후보, 주전이 더 받음) ·
#   소속팀 리그 성적 × 리그 출전 비율(국내컵·대륙대회는 원래부터 그 대회 출전 비율을 곱한다 — 리그도 같게).
# 발롱도르(score_season 기본값)는 예전과 완전히 같다.
GOLDEN_PART_BAND = (0.50, 1.00)
# [2026-11 신설, 신민용 확정] 대륙 올해의 선수(score_continent) — 발롱도르 계산 그대로에 선수 대륙의 국가대표 대회만
# × CONT_INTL_MULT: 대륙 선수권·지역컵(코파 아메리카·골드컵 포함 — 입력 sub 'continent'·'region')의 개인 기록 수준과
# 국가대표 팀 성적 칸의 대륙 단위 몫(입력 intl_v_cont). 월드컵·예선·올림픽은 그대로. 선수는 자기 대륙 대회만 뛰므로
# "선수 대륙에 맞는 대회" = 그 선수의 대륙 선수권·지역컵. 측정(105·106 20년): 우승국 선수 수상 38%(1.0배와 같음),
# 상마다 20년 중 약 1번 수상자 변화, 2.0배는 수상자만 더 바뀜.
CONT_INTL_MULT = 1.5
CONT_INTL_SUBS = ("continent", "region")

GK_POS = ("GK",)
DF_POS = ("CB", "LB", "RB", "LWB", "RWB")

band = _Y.band


def soft_band(x, lo, hi, knee=None, half=None):
    """C안 완만한 상한: lo → 0, hi(p99.5) → knee 직선, 그 위는 knee + (1−knee)·d/(d+half) (d = hi 초과분 ÷ (hi−lo)).
    1에 닿지 않고 계속 오르므로 극단 기록끼리도 구분된다. 최댓값 1(배점 그대로)."""
    if x is None:
        return 0.0
    knee = SOFT_KNEE if knee is None else knee
    half = SOFT_HALF if half is None else half
    u = (x - lo) / (hi - lo)
    if u <= 0:
        return 0.0
    if u <= 1:
        return knee * u
    d = u - 1.0
    return knee + (1.0 - knee) * d / (d + half)


def league_level(power, top_power):
    """리그 수준 = 그 시즌 league_power ÷ 그 시즌 최고 리그 power (0.5~1). 값이 없으면 1.0(보정 없음)."""
    if not power or not top_power:
        return 1.0
    return max(LEVEL_FLOOR, min(1.0, power / float(top_power)))


def difficulty(actual, expected, n_teams):
    """우승 난이도 0.8~1.0 — 시즌 전 전력 예상 순위보다 실제 순위가 얼마나 위인가(팀 수의 25% 이상 위 → 1.0).
    예상 순위를 모르면 0.8(예상대로와 같게)."""
    if not actual or not expected or not n_teams:
        return DIFF_EXPECTED
    t = (expected - actual) / (DIFF_SPAN * n_teams)
    return DIFF_EXPECTED + (1.0 - DIFF_EXPECTED) * max(0.0, min(1.0, t))


def league_value(rank, n_teams, relegated, level, expected):
    """소속팀 리그 성적 0~1 = 순위 점수(yashin_award와 같은 표) × 리그 수준 × 우승 난이도."""
    return _Y.league_rank_value(rank, n_teams, relegated) * level * difficulty(rank, expected, n_teams)


def league_per_match(position, full_season, goal_env=1.0):
    """리그 경기당 기대 골+도움 — 엔진 기대치와 같은 포지션 기준·득점 환경·시즌 길이 보정."""
    base = EST_POS_GOAL.get(position, 3.0) + EST_POS_ASSIST.get(position, 3.0)
    fsm = float(full_season or 38)
    return base * (goal_env or 1.0) * (fsm / 38.0) ** 0.35 / fsm


def comp_rows(e, league_lvl=1.0):
    """중요도가 있는 대회 기록만 [(종류, 중요도, 수준, 골, 도움, 출전, 득점 환경)]. 국내컵 수준 = 그 선수의 리그 수준
    (입력 level 무시)."""
    out = []
    for r in e.get("comps") or ():
        imp = COMP_IMP.get(r.get("kind"), 0.0)
        if imp <= 0 or not r.get("matches"):
            continue
        lvl = league_lvl if r.get("kind") == "cup" else max(0.0, r.get("level") or 0.0)
        out.append((r.get("kind"), imp, lvl, r.get("goals") or 0, r.get("assists") or 0, r.get("matches") or 0,
                    r.get("env") or 1.0))
    return out


def productivity(e):
    """생산성 0~1과 부분값. e: position, goals, assists, matches(리그), full_season, goal_env, league_level,
    comps([{kind, goals, assists, matches, level, env}]), team_attack(팀 공격력, 없으면 1.0), team_gc(팀 경기당 실점,
    없으면 None), share, gk_save(GK만)."""
    pos = e.get("position") or ""
    g, a = e.get("goals") or 0, e.get("assists") or 0
    lvl = e.get("league_level")
    lvl = 1.0 if lvl is None else lvl
    gem = e.get("goal_env") or 1.0
    pm = league_per_match(pos, e.get("full_season"), gem)
    # 포지션 기대 대비 — 국내(리그·국내컵)는 중요도만, 그 밖의 대회는 수준 × 중요도를 분자·분모 둘 다에(기대치만큼
    # 하면 그 대회 몫은 1.0 — 수준 낮은 대회 출전으로 깎이지 않음).
    # [2026-11 변경, 신민용 확정 — 리그 수준 적용 범위 통일] 국내 몫(리그 + 국내컵)의 '기대 초과분'에만 리그 수준을
    # 곱한다(약한 리그에서 기대치보다 많이 넣은 골·도움은 그만큼 덜 친다). 대륙 클럽대회·슈퍼컵·국가대표 몫은 그 대회
    # 수준으로만 본다(소속 리그가 약하다고 아시아 챔스·아시안컵 활약까지 깎지 않음). 기대치에 못 미친 경우는 그대로.
    # 리그 수준 1.0이면 예전 식과 완전히 같다(tools/test_league_level.py A1).
    num_d = g + a
    den_d = pm * (e.get("matches") or 0)
    num_c = den_c = 0.0
    # 팀 공격력 보정 — 국내(리그·국내컵) 기대치에만, 절반 강도.
    tatk = e.get("team_attack") or 1.0
    tmul = tatk ** TEAM_ATTACK_EXP
    den_d *= tmul
    # 공격 생산량 — 실제 골·도움 × 리그 득점 계수^−0.5 × 대회 가치(리그 수준은 국내 몫에 여기서 한 번).
    genv_v = gem ** GOAL_ENV_EXP
    vol_raw = lvl * (g + ASSIST_W * a) / genv_v
    for kind, imp, cl, cg, ca, cm, env in comp_rows(e, lvl):
        if kind == "cup":
            num_d += imp * (cg + ca)
            den_d += imp * pm * env * cm * tmul
        else:
            num_c += imp * cl * (cg + ca)
            den_c += imp * cl * pm * env * cm
        vol_raw += imp * cl * (cg + ASSIST_W * ca) / ((genv_v if kind == "cup" else 1.0) * env)
    if num_d > den_d:
        num_d = den_d + (num_d - den_d) * lvl
    shrink = REL_SHRINK * gem               # 축소도 득점 환경 단위(득점 환경 대비 같은 활약 = 같은 기대 대비)
    rel_rate = (num_d + num_c + shrink) / (den_d + den_c + shrink)
    rel = soft_band(rel_rate, *REL_BAND)
    vol = soft_band(vol_raw, *VOL_BAND)
    # 팀 수비 = 팀 리그 경기당 실점(낮을수록 좋음) × 리그 출전 비율 × 리그 수준([2026-11 변경] 약한 리그의 적은 실점은
    # 그만큼 덜 친다 — 리그 실점이라 리그 수준이 맞다). GK 선방률은 상대 공격진 OVR로 이미 보정돼 있어 곱하지 않는다.
    tgc = e.get("team_gc")
    tdef = (band(tgc, *TEAM_GC_BAND) * max(0.0, min(1.0, e.get("share") or 0.0)) * lvl) if tgc is not None else 0.0
    if pos in GK_POS:
        gk_rel = (1 - DEF_TEAM_SHARE) * soft_band(e.get("gk_save"), *GK_SAVE_BAND) + DEF_TEAM_SHARE * tdef
        prod = PROD_MIX * gk_rel + (1 - PROD_MIX) * vol
    else:
        prod = PROD_MIX * rel + (1 - PROD_MIX) * vol
        if pos in DF_POS:
            prod = (1 - DEF_TEAM_SHARE) * prod + DEF_TEAM_SHARE * tdef
    return {"rel_rate": rel_rate, "vol_raw": vol_raw, "rel": rel, "vol": vol, "tdef": tdef, "prod": prod}


def team_value(league_v, cup_v, cont_v):
    """소속팀 성적 0~1 — 리그 0.4 + 국내컵 0.1 + 대륙대회 0.5(각 부분값은 이미 단계 × 대회 수준 × 출전 비율)."""
    return (TEAM_SPLIT["league"] * (league_v or 0.0) + TEAM_SPLIT["cup"] * (cup_v or 0.0)
            + TEAM_SPLIT["cont"] * (cont_v or 0.0))


def team_parts(e, league_share=False):
    """(리그, 국내컵, 대륙대회) 부분값. 입력: league_rank·league_n·relegated·league_exp(시즌 전 예상 순위),
    league_level, cup_v(단계 × 출전 비율), cont_v(단계 × 대회 가중치 × 출전 비율).
    league_share=True(골든보이): 리그 부분값에도 리그 출전 비율(share, 0~1)을 곱한다."""
    lvl = e.get("league_level")
    lvl = 1.0 if lvl is None else lvl
    lv = league_value(e.get("league_rank"), e.get("league_n"), e.get("relegated"), lvl, e.get("league_exp"))
    if league_share:
        lv *= max(0.0, min(1.0, e.get("share") or 0.0))
    return lv, (e.get("cup_v") or 0.0) * lvl, e.get("cont_v") or 0.0


def personal(e, part_band=None):
    """개인 점수(능력 + 생산성 + 출전). 팀 성적·국제대회와 무관 — 팀 안 배분 순위와 '개인 점수 Top10' 검증에 쓴다.
    part_band: 출전 점수 구간(None = PART_BAND, 골든보이는 GOLDEN_PART_BAND)."""
    p = productivity(e)
    s_ovr = W_PERSONAL * OVR_SHARE * band(e.get("ovr"), *OVR_BAND)
    s_prod = W_PERSONAL * (1 - OVR_SHARE) * p["prod"]
    s_part = W_PART * band(e.get("share"), *(part_band or PART_BAND))
    return dict(p, s_ovr=s_ovr, s_prod=s_prod, s_part=s_part, personal=s_ovr + s_prod + s_part)


def decay_ranks(members):
    """팀 안 개인 점수 순위(0부터). 개인 점수가 완전히 같으면 같은 순위(1·1·3위 방식). 동점이 없으면 DB 순서와 무관한
    내림차순 순위."""
    ordered = sorted(members, key=lambda e: (-round(e["personal"], 9), e["player_id"]))
    out, prev, rank = {}, None, 0
    for i, e in enumerate(ordered):
        k = round(e["personal"], 9)
        if k != prev:
            rank, prev = i, k
        out[id(e)] = rank
    return out


def score_season(entries, part_band=None, league_share=False):
    """그 시즌 후보 전원(제자리 갱신) → 총점 내림차순 정렬 리스트. 각 entry에 personal·s_team·s_intl·total 추가.
    팀 안 배분은 같은 team_id 후보끼리(시즌을 마친 팀 기준). part_band·league_share는 골든보이용(score_golden) —
    기본값이면 발롱도르 그대로."""
    for e in entries:
        e.update(personal(e, part_band))
        e["league_v"], e["cup_lv"], e["cont_lv"] = team_parts(e, league_share)
        e["team_v"] = team_value(e["league_v"], e["cup_lv"], e["cont_lv"])
        e["s_intl"] = W_INTL * max(0.0, min(1.0, e.get("intl_v") or 0.0))
    by_team = {}
    for e in entries:
        by_team.setdefault(e.get("team_id"), []).append(e)
    for members in by_team.values():
        rk = decay_ranks(members)
        for e in members:
            e["team_rank_in_team"] = rk[id(e)]
            e["decay"] = DECAY[min(rk[id(e)], len(DECAY) - 1)]
            e["s_team"] = W_TEAM * e["team_v"] * e["decay"]
    for e in entries:
        e["trophy"] = e["s_team"] + e["s_intl"]
        e["total"] = e["personal"] + e["trophy"]
    return sorted(entries, key=sort_key)


def sort_key(e):
    """총점 → 개인 점수 → 생산성 → 시즌 OVR → 리그 출전 → 선수 번호. 마지막 키는 모든 값이 같을 때만 쓰인다
    (그때만 내 선수 번호 −1이 앞선다 — 같은 기록이면 점수는 언제나 같다)."""
    return (-round(e["total"], 9), -round(e["personal"], 9), -round(e["prod"], 9), -(e.get("ovr") or 0),
            -(e.get("matches") or 0), e["player_id"])


def top30(scored):
    """팀당 최대 3명으로 Top30."""
    out, cnt = [], {}
    for e in scored:
        t = e.get("team_id")
        k = cnt.get(t, 0) if t is not None else 0
        if t is not None and k >= MAX_PER_TEAM:
            continue
        out.append(e)
        if t is not None:
            cnt[t] = k + 1
        if len(out) >= TOP_N:
            break
    return out


def score_golden(entries):
    """[2026-11 신설] 골든보이 채점 — score_season에 골든보이 두 규칙(GOLDEN_PART_BAND · 리그 성적 × 리그 출전 비율)만
    켠 것. 총점 내림차순 정렬 리스트(팀당 인원 제한 없음 — Top N은 호출부가 앞에서 자른다)."""
    return score_season(entries, part_band=GOLDEN_PART_BAND, league_share=True)


def continental_entries(entries, mult=CONT_INTL_MULT):
    """대륙 올해의 선수 입력 — entries의 사본(원본은 그대로). 국가대표 대륙 단위 대회(CONT_INTL_SUBS)의 개인 기록 수준
    × mult, 국가대표 팀 성적 = max(월드컵 몫, mult × 대륙 단위 몫). 월드컵·대륙 몫이 따로 없는 입력(예전 형식)은 intl_v
    그대로. mult = 1이면 발롱도르 입력과 같은 값.
    [2026-11 신민용 확정] 네이션스 예선 칸(intl_v_qual — 네이션스 예선만 있는 해)은 mult를 곱하지 않는다(본선만 mult):
    국가대표 팀 성적 = max(월드컵 몫, mult × 대륙 단위 몫, 예선 몫)."""
    import copy
    out = copy.deepcopy(entries)
    for e in out:
        for r in e.get("comps") or ():
            if r.get("kind") == "intl" and r.get("sub") in CONT_INTL_SUBS:
                r["level"] = (r.get("level") or 0.0) * mult
        if e.get("intl_v_cont") is not None or e.get("intl_v_world") is not None:
            e["intl_v"] = max(e.get("intl_v_world") or 0.0, mult * (e.get("intl_v_cont") or 0.0),
                              e.get("intl_v_qual") or 0.0)
    return out


def score_continent(entries, mult=CONT_INTL_MULT):
    """[2026-11 신설] 대륙 올해의 선수 채점 — 발롱도르와 같은 후보·같은 score_season에 continental_entries만 거친 것.
    총점 내림차순 정렬 리스트(국적 대륙별 1위는 호출부가 고른다)."""
    return score_season(continental_entries(entries, mult))


# ════════════════════════════════════════════════════════════════════════════════════════════════════════
# [2026-11 신설, 신민용 확정] 발롱도르 1위 최소 자격 — 점수와 분리된 1위 선정 필터(점수·Top30 구성·2위 이하 상대 순서 불변).
#   Top30을 지금처럼 확정한 뒤, 아래 경로 중 하나 이상을 충족한 선수 중 점수가 가장 높은 선수를 1위로 올린다(나머지는
#   원래 순서 그대로 한 칸씩 뒤로). Top30 안에 자격자가 없으면 기존 1위 유지(공석·Top30 밖 추가 없음). 2위 이하엔 적용 안 함.
#   A  소속팀(시즌 중 이적생은 대륙대회를 뛴 상반기 팀) — 유럽 챔피언스리그 4강 이상 · 유럽 유로파리그 우승 ·
#      남미 챔피언스리그(코파 리베르타도레스) 우승. 아시아·아프리카·북미 챔스는 단독 인정 안 함.
#      [2026-11-15 개정, 신민용 확정: 2004 유로파 우승 + 리그 MVP 78골 시즌이 1위에서 막힌 사례]
#   B  국가대표 대회 — 대회별 인정 성적(TOP1_INTL_RULES) + 그 대회 전체 경기(최다 경기 팀 기준)의 50% 이상 출전.
#      [2026-11-15 개정] 월드컵·유로 4강 이상 / 코파 아메리카 결승 / 남북미 대륙컵·아시안컵·아프리카 네이션스컵·골드컵
#      우승. 유럽 네이션스컵·오세아니아컵·그 밖 지역컵은 제외.
#      예외: 인정 성적 미달이라도 그 대회 MVP(월드컵 골든볼) 또는 득점왕(골든부트) — 월드컵·유로·코파만, 출전 50% 그대로.
#   C  1부 리그 + 소속팀 유럽 챔스 8강 이상 +
#      C-1 리그 MVP + (리그 공격포인트 1위 또는 리그 평점 1위)
#      C-2 리그 득점왕 또는 도움왕 + 리그 공격포인트 1위 + 리그 평점 3위 이내
#      순위는 같은 리그에서 리그 출전 70% 이상 선수끼리(더 높은 선수 수 + 1 — 동률은 같은 순위).
#   D  [2026-11-15 신설] 5대 리그(잉글랜드·스페인·이탈리아·독일·프랑스) 1부 우승팀 소속 + 그 리그 MVP 또는 득점왕
#      (대륙대회 성적은 안 본다 — 리그 MVP·득점왕 자체가 리그 출전 70% 이상이 조건이라 추가 출전 조건은 두지 않는다).
#   MVP·득점왕은 자격 판정에만 쓰고 점수에는 넣지 않는다(발롱 점수엔 개인상 가산점이 없다).
#   리그 MVP 선정 방식이 바뀌면 C-1을 다시 검증한다(40시즌 조사: 기존 1위 40명 중 32명 유지, 8명 변경).
# ════════════════════════════════════════════════════════════════════════════════════════════════════════
TOP1_CL_SF = ("4강", "준우승", "우승")
TOP1_CL_QF = ("8강",) + TOP1_CL_SF
TOP1_INTL_NAMES = ("월드컵", "유로(EURO)", "코파 아메리카")   # B-예외(MVP·득점왕) 대상 · 발롱 ②예외 대상(그대로)
TOP1_INTL_SF = ("4강", "3위", "4위", "준우승", "우승")
TOP1_INTL_FINAL = ("준우승", "우승")
TOP1_INTL_WIN = ("우승",)
# [2026-11-15 개정, 신민용 확정] B 경로 대회별 인정 성적(대회 이름 → 인정 단계 라벨).
TOP1_INTL_RULES = {
    "월드컵": TOP1_INTL_SF, "유로(EURO)": TOP1_INTL_SF,
    "코파 아메리카": TOP1_INTL_FINAL,
    "남북미 대륙컵": TOP1_INTL_WIN, "아시안컵": TOP1_INTL_WIN, "아프리카 네이션스컵": TOP1_INTL_WIN, "골드컵": TOP1_INTL_WIN,
}
TOP1_BIG5 = ("잉글랜드", "스페인", "이탈리아", "독일", "프랑스")
TOP1_PATH_DESC = {
    "A": "A 클럽 대회(챔스 4강↑ · 유로파 우승 · 남미 챔스 우승)",
    "B": "B 국가대표 대회 성적",
    "B-예외": "B 예외(월드컵·유로·코파 MVP 또는 득점왕)",
    "C-1": "C-1 리그 MVP + 공격P/평점 1위 + 챔스 8강↑",
    "C-2": "C-2 리그 득점·도움왕 + 공격P 1위·평점 3위↑ + 챔스 8강↑",
    "D": "D 5대 리그 우승 + 리그 MVP/득점왕",
}


def top1_paths(g):
    """1위 자격 경로 목록(빈 목록 = 자격 없음). g(없으면 미충족):
      cl_stage  유럽 챔스 단계 라벨('우승'·'준우승'·'4강'·'8강'·…) — 유럽 챔스가 아니면 None
      el_win    유럽 유로파리그 우승 · sa_cl_win 남미 챔피언스리그 우승 (bool)
      intl      [{"name": 대회 이름, "stage": 국가 단계 라벨, "apps": 출전, "n": 대회 최다 경기 수, "mvp": bool,
                  "scorer": bool}] (TOP1_INTL_RULES 대회만 — name이 없으면 예전 규칙(4강 이상·예외 허용)으로 본다)
      big5_champ  5대 리그 1부 우승팀 소속인가
      tier1     소속 리그가 1부인가 · league_awards {'MVP', '득점왕', '도움왕'} 중 받은 것
      ga_rank / rt_rank  리그 공격포인트·평점 순위(1부터)"""
    out = []
    cl = g.get("cl_stage")
    if cl in TOP1_CL_SF or g.get("el_win") or g.get("sa_cl_win"):
        out.append("A")
    for t in g.get("intl") or ():
        n, apps = t.get("n") or 0, t.get("apps") or 0
        if n <= 0 or apps * 2 < n:              # 출전 50% 미만(정수 비교)
            continue
        nm = t.get("name")
        ok = TOP1_INTL_RULES.get(nm, TOP1_INTL_SF) if nm else TOP1_INTL_SF
        if nm and nm not in TOP1_INTL_RULES:
            continue
        if t.get("stage") in ok:
            out.append("B")
            break
        if (not nm or nm in TOP1_INTL_NAMES) and (t.get("mvp") or t.get("scorer")):
            out.append("B-예외")
            break
    aw = set(g.get("league_awards") or ())
    if g.get("tier1") and cl in TOP1_CL_QF:
        ga, rt = g.get("ga_rank"), g.get("rt_rank")
        if "MVP" in aw and (ga == 1 or rt == 1):
            out.append("C-1")
        elif aw & {"득점왕", "도움왕"} and ga == 1 and rt is not None and rt <= 3:
            out.append("C-2")
    if g.get("big5_champ") and aw & {"MVP", "득점왕"}:
        out.append("D")
    return out


def apply_top1_gate(top, paths_of):
    """Top30(점수 순) → (새 순서 리스트, 1위의 자격 경로 목록). paths_of(entry) = top1_paths 결과.
    1위가 자격이 있으면 그대로, 없으면 자격자 중 가장 앞(점수 최고)을 맨 앞으로 — 나머지 순서 유지.
    자격자가 아무도 없으면 그대로(경로 목록 빈 값). top은 바꾸지 않는다."""
    lst = list(top)
    for i, e in enumerate(lst):
        p = paths_of(e)
        if p:
            if i == 0:
                return lst, p
            return [e] + lst[:i] + lst[i + 1:], p
    return lst, []


# ════════════════════════════════════════════════════════════════════════════════════════════════════════
# [2026-11 신설, 신민용 확정] 발롱도르 전용 채점 — score_season_bd. 기존 score_season(대륙 올해의 선수·골든보이 등이
# 쓰는 공용 채점)은 그대로 두고 발롱도르 Top30만 이 경로로 채점한다. 40시즌 읽기 전용 실험(V7a + E안 + (라))으로 정한 값.
#  ① 생산성(V7a): 공격량 척도가 꺾이는 지점(VOL_BAND 상한) 위에서 멈추지 않고 로그 곡선으로 계속 오른다(꺾임 지점
#     기울기의 절반에서 시작). 미드필더(CAM·CM·LM·RM)는 미드필더 공격량 상위 0.5%(BD_MF_VOL_KNEE)부터 같은 세기의
#     로그 가산을 더한다. 꺾임 아래 점수·기대 대비 척도·DF 팀 수비·GK는 기존과 같다.
#  ② 국가대표 기록((라)): 주요 대회(월드컵·대륙 선수권·코파·골드컵 — 국대 'world'·'continent', 수준 0.27 이상 'region')
#     골·도움은 생산성에서 빼고 국대 개인 활약 칸에서만 센다. 단, 그 대회들의 실제 골+도움이 기대치보다 적으면 기대 대비
#     척도는 지금처럼 대회 경기를 넣어 계산해 부진 감점을 남긴다(공격량에는 넣지 않음 — 두 번 세지 않게). 예선·소규모
#     지역컵은 생산성에 그대로.
#  ③ 국대 개인 활약(E안): 국대 팀 성적 칸(W_INTL, 1.0)은 그대로 두고 개인 활약 칸(BD_IND_W, 1.0)을 따로 더한다.
#     대회마다 0~1(포지션 기대 대비 + 공격량, DF는 팀 실점 30%, GK는 선방·클린시트·실점) → 대회·포지션별 최저선
#     (4강 이상 팀 주전 중앙값) 아래는 0 → 출전 비율 × 대회 위상(_ballon_comp_level) → MVP 25%(개인 활약 칸의 7.5%),
#     그 해 주요 8개 대회 중 가장 높은 값. 대회 평점은 쓰지 않는다(OVR과 상관 0.91). 득점왕은 골로 이미 반영.
# ════════════════════════════════════════════════════════════════════════════════════════════════════════
BD_VOL_LOG_K = 0.5                 # 꺾임 뒤 로그 곡선 시작 기울기 = 꺾임 지점 직선 기울기 × 0.5
BD_MF_POS = ("CAM", "CM", "LM", "RM")
BD_MF_VOL_KNEE = 30.353409981827   # 미드필더(CAM·CM·LM·RM) 공격량 상위 0.5% — 40시즌 후보 분포
BD_MF_LOG_K = 0.5
BD_IND_W = 1.0                     # 국대 개인 활약 칸 배점(국대 팀 성적 W_INTL과 별도)
BD_IND_MVP = 0.25                  # MVP 반영 비율(100% = 개인 활약 칸의 30%)
BD_IND_MVP_SHARE = 0.30
BD_IND_MAIN = ("월드컵", "유로(EURO)", "코파 아메리카", "유럽 네이션스컵", "남북미 대륙컵", "아프리카 네이션스컵",
               "아시안컵", "골드컵")
BD_IND_SHRINK = 2.0                # 대회 기대 대비 축소 상수(짧은 대회)
BD_IND_REL = (1.0, 2.375)          # 대회 기대 대비: 기대만큼 → 0, 주요 대회 출전 50%+ 필드 선수 상위 1% → 1
BD_IND_VOL = (0.0, 8.75)           # 대회 공격량(골 + 0.75 × 도움): 0 → 0, 상위 1% → 1
BD_IND_GK = ((0.65, 0.85), (0.1, 0.6), (2.0, 0.5))   # GK 선방률(축소)·클린시트율·경기당 실점 구간
BD_IND_DF_TGC = (2.0, 0.5)         # DF 팀 경기당 실점 구간(낮을수록 1)
BD_IND_GK_POS = ("GK",)
BD_IND_DF_POS = ("CB", "LB", "RB", "LWB", "RWB")
BD_IND_FW_POS = ("ST", "CF", "LW", "RW", "SS")
# 최저선 = 대회·포지션 그룹별 '4강 이상 팀, 출전 50%+, MVP 아닌 선수' 개인 기록 중앙값(40시즌; 표본 15 미만은 그룹 전체 중앙값)
BD_IND_FLOOR = {
    ("월드컵", "FW"): 0.15714285714285714, ("월드컵", "MF"): 0.15714285714285714,
    ("월드컵", "DF"): 0.2571428571428571, ("월드컵", "GK"): 0.5758008658008658,
    ("유로(EURO)", "FW"): 0.26153846153846155, ("유로(EURO)", "MF"): 0.1,
    ("유로(EURO)", "DF"): 0.2571428571428571, ("유로(EURO)", "GK"): 0.5954808590102707,
    ("코파 아메리카", "FW"): 0.23807500910304644, ("코파 아메리카", "MF"): 0.10982800982800989,
    ("코파 아메리카", "DF"): 0.26666666666666666, ("코파 아메리카", "GK"): 0.6740873015873017,
    ("유럽 네이션스컵", "FW"): 0.19285714285714284, ("유럽 네이션스컵", "MF"): 0.1,
    ("유럽 네이션스컵", "DF"): 0.2571428571428571, ("유럽 네이션스컵", "GK"): 0.6469747899159661,
    ("남북미 대륙컵", "FW"): 0.26153846153846155, ("남북미 대륙컵", "MF"): 0.22532467532467532,
    ("남북미 대륙컵", "DF"): 0.36621789032610674, ("남북미 대륙컵", "GK"): 0.7143223443223444,
    ("아프리카 네이션스컵", "FW"): 0.17142857142857143, ("아프리카 네이션스컵", "MF"): 0.22532467532467532,
    ("아프리카 네이션스컵", "DF"): 0.36621789032610674, ("아프리카 네이션스컵", "GK"): 0.8614800514800517,
    ("아시안컵", "FW"): 0.36811521811521813, ("아시안컵", "MF"): 0.2538985359210078,
    ("아시안컵", "DF"): 0.3767797944418969, ("아시안컵", "GK"): 0.6885714285714286,
    ("골드컵", "FW"): 0.21428571428571427, ("골드컵", "MF"): 0.21103896103896103,
    ("골드컵", "DF"): 0.433487228099527, ("골드컵", "GK"): 0.6771602787456446,
}
BD_IND_FLOOR_GROUP = {"FW": 0.21428571428571427, "MF": 0.18211143695014664, "DF": 0.3, "GK": 0.6771602787456446}


def is_main_intl_row(r):
    """생산성 comps 행 중 국대 개인 활약 칸이 대신 세는 주요 대회 행인가((라) 처리 대상)."""
    return r.get("kind") == "intl" and (r.get("sub") in ("world", "continent")
                                        or (r.get("sub") == "region" and (r.get("level") or 0) >= 0.27))


def vol_band_bd(x, pos):
    """V7a 공격량 0~(1 초과 가능): 꺾임 아래는 soft_band와 같은 직선, 위는 로그 곡선. 미드필더는 BD_MF_VOL_KNEE부터 로그 가산."""
    if x is None:
        return 0.0
    lo, hi = VOL_BAND
    slope = SOFT_KNEE / (hi - lo)
    v = SOFT_KNEE * max(0.0, (x - lo) / (hi - lo)) if x <= hi else SOFT_KNEE + BD_VOL_LOG_K * slope * hi * math.log(x / hi)
    if pos in BD_MF_POS and x > BD_MF_VOL_KNEE:
        v += BD_MF_LOG_K * slope * BD_MF_VOL_KNEE * math.log(x / BD_MF_VOL_KNEE)
    return v


def productivity_bd(e):
    """발롱도르 전용 생산성(V7a + (라)). 반환은 productivity와 같은 키 + intl_short(주요 대회 기대 미달 — 부진 감점 유지)."""
    pos = e.get("position") or ""
    comps = e.get("comps") or ()
    main = [r for r in comps if is_main_intl_row(r)]
    e_wo = dict(e)
    e_wo["comps"] = [r for r in comps if not is_main_intl_row(r)]
    p_wo = productivity(e_wo)
    short = False
    rel_rate = p_wo["rel_rate"]
    if main:
        pm = league_per_match(pos, e.get("full_season"), e.get("goal_env") or 1.0)
        act = sum(COMP_IMP["intl"] * max(0.0, r.get("level") or 0) * ((r.get("goals") or 0) + (r.get("assists") or 0))
                  for r in main)
        exp = sum(COMP_IMP["intl"] * max(0.0, r.get("level") or 0) * pm * (r.get("env") or 1.0) * (r.get("matches") or 0)
                  for r in main)
        if act < exp:
            short = True
            rel_rate = productivity(e)["rel_rate"]
    if pos in GK_POS:
        return dict(p_wo, intl_short=short)          # GK 기대 대비는 선방률 — 대회 골·도움과 무관, 공격량도 사실상 0
    rel = soft_band(rel_rate, *REL_BAND)
    vol = vol_band_bd(p_wo["vol_raw"], pos)
    prod = PROD_MIX * rel + (1 - PROD_MIX) * vol
    if pos in DF_POS:
        prod = (1 - DEF_TEAM_SHARE) * prod + DEF_TEAM_SHARE * p_wo["tdef"]
    return dict(p_wo, rel_rate=rel_rate, rel=rel, vol=vol, prod=prod, intl_short=short)


def _lin(x, lo, hi):
    return 0.0 if x is None else max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _ind_group(pos):
    return ("GK" if pos in BD_IND_GK_POS else "DF" if pos in BD_IND_DF_POS else "FW" if pos in BD_IND_FW_POS else "MF")


def intl_tournament_perf(t):
    """대회 하나의 개인 기록 0~1(최저선·출전 비율 적용 전). t: pos(대표팀 포지션)·apps·g·a·sv·gc·cs·tgc(팀 경기당 실점)."""
    pos = t.get("pos") or ""
    apps = t.get("apps") or 0
    if apps <= 0:
        return 0.0
    g, a = t.get("g") or 0, t.get("a") or 0
    if pos in BD_IND_GK_POS:
        sv, gc = t.get("sv") or 0, t.get("gc") or 0
        (s_lo, s_hi), (c_lo, c_hi), (g_lo, g_hi) = BD_IND_GK
        return (0.6 * _lin((sv + 10 * 0.70) / (sv + gc + 10), s_lo, s_hi) + 0.2 * _lin((t.get("cs") or 0) / apps, c_lo, c_hi)
                + 0.2 * _lin(gc / apps, g_lo, g_hi))
    pm = league_per_match(pos, 38, 1.0)
    rel = (g + a + BD_IND_SHRINK) / (pm * apps + BD_IND_SHRINK)
    v = 0.5 * _lin(rel, *BD_IND_REL) + 0.5 * _lin(g + ASSIST_W * a, *BD_IND_VOL)
    if pos in BD_IND_DF_POS:
        v = 0.7 * v + 0.3 * _lin(t.get("tgc"), *BD_IND_DF_TGC)
    return v


def intl_individual(rows):
    """국대 개인 활약 0~1 — rows: 그 해 주요 대회별 dict(name·w(대회 위상)·n(대회 최다 경기 수)·mvp + intl_tournament_perf 입력).
    대회마다 최저선 위만 → × 출전 비율 → MVP 25% 섞기 → × 대회 위상, 그중 가장 높은 값."""
    best = 0.0
    for t in rows or ():
        n = t.get("n") or 0
        if t.get("name") not in BD_IND_MAIN or not n:
            continue
        pr = intl_tournament_perf(t)
        grp = _ind_group(t.get("pos") or "")
        f = BD_IND_FLOOR.get((t["name"], grp), BD_IND_FLOOR_GROUP[grp])
        pr = max(0.0, (pr - f) / (1 - f))
        m = BD_IND_MVP
        v = (t.get("w") or 0.0) * ((1 - BD_IND_MVP_SHARE * m) * pr * min(1.0, (t.get("apps") or 0) / n)
                                   + BD_IND_MVP_SHARE * m * (1.0 if t.get("mvp") else 0.0))
        best = max(best, v)
    return best


def personal_bd(e):
    p = productivity_bd(e)
    s_ovr = W_PERSONAL * OVR_SHARE * band(e.get("ovr"), *OVR_BAND)
    s_prod = W_PERSONAL * (1 - OVR_SHARE) * p["prod"]
    s_part = W_PART * band(e.get("share"), *PART_BAND)
    return dict(p, s_ovr=s_ovr, s_prod=s_prod, s_part=s_part, personal=s_ovr + s_prod + s_part)


def score_season_bd(entries):
    """발롱도르 전용 채점(제자리 갱신) → 총점 내림차순. score_season과 같은 팀 안 배분·정렬에 personal_bd와
    국대 개인 활약(e['bd_intl'] — 없으면 0)을 쓴다. s_intl = 국대 팀 성적 + 국대 개인 활약(s_intl_team·s_intl_ind로도 남김)."""
    for e in entries:
        e.update(personal_bd(e))
        e["league_v"], e["cup_lv"], e["cont_lv"] = team_parts(e)
        e["team_v"] = team_value(e["league_v"], e["cup_lv"], e["cont_lv"])
        e["s_intl_team"] = W_INTL * max(0.0, min(1.0, e.get("intl_v") or 0.0))
        e["s_intl_ind"] = BD_IND_W * intl_individual(e.get("bd_intl"))
        e["s_intl"] = e["s_intl_team"] + e["s_intl_ind"]
    by_team = {}
    for e in entries:
        by_team.setdefault(e.get("team_id"), []).append(e)
    for members in by_team.values():
        rk = decay_ranks(members)
        for e in members:
            e["team_rank_in_team"] = rk[id(e)]
            e["decay"] = DECAY[min(rk[id(e)], len(DECAY) - 1)]
            e["s_team"] = W_TEAM * e["team_v"] * e["decay"]
    for e in entries:
        e["trophy"] = e["s_team"] + e["s_intl"]
        e["total"] = e["personal"] + e["trophy"]
    return sorted(entries, key=sort_key)


# ════════════════════════════════════════════════════════════════════════════════════════════════════════
# [2026-11-16 신설, 신민용 확정 — 설계안 E] 발롱도르 상위 3명 경쟁(클럽 성과 반영). 발롱도르 전용.
#   조사(40시즌): 상위 후보끼리 개인 활약이 비슷하면 클럽 팀 성과가 순위를 거의 못 바꿨다(성과별 소속팀 성적 폭이
#   더블 1.27 ↔ 무관 0.65 정도로 압축, 국대 점수가 더 큰 지렛대). 점수를 모든 후보에 키우면(A+B+C) 강팀 조연이
#   상위 10명 끝으로 올라오고(R2 불합격), 상위 10명 전체를 다시 줄 세우면(D) 원칙과 지표가 충돌했다.
#   E: Top30·상위 10명 구성과 4~10위 순서는 기존 총점 그대로. 상위 3명 자리만 —
#     후보 = 기존 1~3위 + 4~10위 중 '팀 에이스'(상위 10명 안 같은 팀 개인 점수 1순위)이면서 개인 점수가 기존 1~3위 중
#            가장 낮은 선수와 BD_E_CHALLENGE_GAP 이내인 선수.
#     경쟁 점수 = 개인 점수 + 국제대회(둘 다 기존 그대로) + 클럽 성과(BD_E_W_CLUB × 단계표 BD_E_* · 우승 후보 할인 없음,
#                × 상위 10명 안 같은 팀 개인 점수 순위 배분 BD_E_DECAY 100/50/0).
#     경쟁 점수 상위 3명이 1~3위, 나머지 후보는 4위부터 기존 총점 순서. 1위 최소 자격(apply_top1_gate)은 그 뒤에 적용.
#   오프라인(40시즌) 판정 통과: T3 37→14 · T1 5→1 · R1 0 · R2 3 · R3 1 · R4 4. 저장 총점(total)은 기존 값 그대로 둔다.
# ════════════════════════════════════════════════════════════════════════════════════════════════════════
BD_E_W_CLUB = 2.5
BD_E_LEAGUE_FIXED = {1: 1.0, 2: 0.65, 3: 0.5, 4: 0.4}
BD_E_LEAGUE_TAIL = ((0.30, 0.3), (0.50, 0.2), (0.75, 0.1))
BD_E_LEAGUE_BOTTOM = 0.05
BD_E_CONT_STAGE = {"우승": 1.0, "준우승": 0.7, "4강": 0.5, "8강": 0.35, "16강": 0.2, "플레이오프": 0.1, "리그페이즈": 0.05}
BD_E_DECAY = (1.0, 0.5, 0.0)
BD_E_CHALLENGE_GAP = 0.5
BD_E_TOP, BD_E_POOL = 3, 10


def e_league_rank_value(rank, n_teams, relegated=False):
    """E 리그 순위 점수 — 1위와 그 아래 차이를 키운 표(강등 0)."""
    if relegated or not rank or not n_teams:
        return 0.0
    if rank in BD_E_LEAGUE_FIXED:
        return BD_E_LEAGUE_FIXED[rank]
    p = rank / float(n_teams)
    for cut, v in BD_E_LEAGUE_TAIL:
        if p <= cut:
            return v
    return BD_E_LEAGUE_BOTTOM


def e_club_value(e):
    """E 클럽 성과 0~1 = 리그 0.4 + 국내컵 0.1 + 대륙대회 0.5. 리그 = E 순위 점수 × 리그 수준(우승 난이도 없음),
    국내컵 = 기존 단계 점수 × 리그 수준, 대륙대회 = E 단계표 × (대회 가중치 × 출전 비율 = cont_wp)."""
    lvl = e.get("league_level")
    lvl = 1.0 if lvl is None else lvl
    lv = e_league_rank_value(e.get("league_rank"), e.get("league_n"), e.get("relegated")) * lvl
    cont = BD_E_CONT_STAGE.get(e.get("cont_stage"), 0.0) * max(0.0, e.get("cont_wp") or 0.0)
    return team_value(lv, (e.get("cup_v") or 0.0) * lvl, cont)


def contest_top3(top):
    """Top30(기존 총점 순) → 상위 3명만 E 경쟁으로 정한 새 순서(새 리스트). 각 entry에 contest(경쟁 점수, 후보만)와
    contest_moved(E로 순위가 바뀐 선수 True)를 남긴다. top 자체 순서는 바꾸지 않는다."""
    lst = list(top)
    if len(lst) <= 1:
        return lst
    top10 = lst[:BD_E_POOL]
    by = {}
    for e in top10:
        by.setdefault(e.get("team_id"), []).append(e)
    rank_in = {}
    for members in by.values():
        members = sorted(members, key=lambda e: (-round(e["personal"], 9), e["player_id"]))
        for i, e in enumerate(members):
            rank_in[id(e)] = i
    head = top10[:BD_E_TOP]
    floor = min(e["personal"] for e in head)
    pool = list(head) + [e for e in top10[BD_E_TOP:]
                         if rank_in[id(e)] == 0 and e["personal"] >= floor - BD_E_CHALLENGE_GAP - 1e-12]
    for e in pool:
        d = BD_E_DECAY[min(rank_in[id(e)], len(BD_E_DECAY) - 1)]
        e["contest"] = e["personal"] + (e.get("s_intl") or 0.0) + BD_E_W_CLUB * e_club_value(e) * d
    win = sorted(pool, key=lambda e: (-round(e["contest"], 9), -round(e["personal"], 9), e["player_id"]))[:BD_E_TOP]
    wid = {id(e) for e in win}
    new = win + [e for e in top10 if id(e) not in wid] + lst[BD_E_POOL:]
    for i, e in enumerate(new):
        e["contest_moved"] = e is not lst[i]
    return new