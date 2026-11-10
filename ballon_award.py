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