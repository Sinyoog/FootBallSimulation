# -*- coding: utf-8 -*-
"""[2026-11 신설, 신민용 확정] 야신상 전용 채점 — 순수 계산 모듈(DB·난수 없음).

아직 수상 로직(_save_yashin_trophy_top10)에 연결하지 않았다. 단위 테스트(tools/test_yashin_score.py)를 통과하고
확인받은 뒤 연결한다. 데이터 수집(상대 공격진·대회 단계·출전 비율)은 연결 단계에서 game_engine 쪽이 맡고,
이 모듈은 이미 모은 값만 받아 계산한다.

엔진 구조(ai_lifecycle._gk_align_team): AI GK의 실점·클린시트는 팀 실제 리그 결과를 출전 수대로 나눈 값이고,
선방률만 GK 개인 값이다 — 기대 선방률(_gk_match_save: GK OVR 대 상대 공격진 OVR, 팀의 모든 리그 경기 평균)
+ 시즌 운(표준편차 √(p(1−p)/슈팅)). 받은 슈팅은 실점 ÷ (1 − 선방률)로 거꾸로 만들어진다.

배점(소속팀 성적 1.0 기준, 합 10.0)
  ① 선방 능력 6.0   표준 환경 선방률 — 실제 선방률에서 상대 공격 환경 몫만 그 GK 자신의 기울기로 뺀다
                    (= 이 GK가 표준 상대 공격진 OVR 92.5를 만났다면의 선방률). 상대 공격진의 질만 보정한
                    근사치다 — 엔진에 슈팅의 질 데이터는 없다. 72% → 0점, 82% → 1점.
  ② 경기당 실점 0.75 (실제 출전 경기 기준, 팀 수비 영향이 크다)  2.1 → 0점, 0.8 → 1점
  ③ 클린시트율 0.75 (실제 출전 경기 기준, 팀 수비 영향이 크다)   0.12 → 0점, 0.50 → 1점
  ④ 소속팀 성적 1.0  리그 0.5 + 국내컵 0.2 + 대륙대회 0.3 (단계표 아래)
  ⑤ 국제대회 1.0     월드컵·자기 대륙컵 중 높은 쪽 — [2026-11] 네이션스 예선만 있는 해는 네이션스 예선 통과 0.45 · 탈락 0.25
                    (× 대륙컵 가중치 × 예선 출전 비율, QUAL_STAGE)도 같이 비교한다. U-23 올림픽은 넣지 않는다.
  ⑥ 출전 기여 0.5    (리그 출전 비율 − 70%) ÷ 30%
표본 축소: ① 계산 순서 = 실제 선방률(선방 ÷ (선방+실점)) → 환경 보정 → 그 시즌 후보의 '보정된' 선방률 평균 쪽으로
슈팅 150개만큼 축소 → 점수 환산. 실제 선방률 평균을 쓰면 리그 환경 차이가 다시 들어오므로 쓰지 않는다."""

from ai_lifecycle import _gk_match_save as _engine_match_save, _GK_SAVE_CAP

STD_ATT = 92.5                 # 표준 상대 공격진 OVR(두 시드 70% 자격 GK의 상대 공격진 중앙값, 고정)
SHRINK_SHOTS = 150             # 표본 축소 — 후보 평균 쪽으로 당기는 가상 슈팅 수. [2026-11 확정] 40은 표본 적은 백업이
                               # 운으로 1위를 너무 자주 했다(실력 같을 때 62%). 150 = 실력이 같으면 주전·백업 1위 확률이
                               # 같아지고 최고 실력 GK를 뽑는 정확도가 가장 높은 값(엔진 운 모형 시뮬레이션, 테스트 D)
CLUB_CAP = _GK_SAVE_CAP["club"]

W_SAVE, W_GC, W_CS, W_TEAM, W_INTL, W_PART = 6.0, 0.75, 0.75, 1.0, 1.0, 0.5
TEAM_SPLIT = {"league": 0.5, "cup": 0.2, "cont": 0.3}

SAVE_BAND = (0.72, 0.82)
GC_BAND = (2.1, 0.8)           # 높을수록 나쁨 — 2.1 → 0, 0.8 → 1
CS_BAND = (0.12, 0.50)
PART_BAND = (0.70, 1.00)

# 대회 단계표(공통 — MVP·발롱도르 재설계 때 같이 쓴다). 미개최·미참가 = 0.
CUP_STAGE = {"우승": 1.0, "준우승": 0.8, "4강": 0.6, "8강": 0.4, "16강": 0.2, "32강": 0.1}
CONT_STAGE = {"우승": 1.0, "준우승": 0.8, "4강": 0.65, "8강": 0.5, "16강": 0.35, "플레이오프": 0.25,
              "리그페이즈": 0.15}
INTL_STAGE = {"우승": 1.0, "준우승": 0.8, "3위": 0.7, "4위": 0.6, "8강": 0.45, "16강": 0.3, "32강": 0.2,
              "조별": 0.1}
# [2026-11 신설, 신민용 확정] 국가대표 예선 단계표 — 그 본선 우승 = 1 기준. 네이션스 예선만 있는 해(월드컵 없는 짝수 해)의
# 네이션스 예선(× 대륙컵 가중치)과 U-23 올림픽 예선(× 올림픽 가중치, 골든보이·코파 트로피 전용)에 쓴다. 1차·2차 탈락 구분 없음.
QUAL_STAGE = {"통과": 0.45, "탈락": 0.25}
# [2026-11 신설, 신민용 확정] U-23 올림픽 본선 단계표 — INTL_STAGE에 하한 0.45(= 예선 통과)를 건 것. 본선에 나간 나라가
# 예선 탈락국보다 낮아지지 않게(조별·16강·8강 = 예선 통과와 같은 값). 골든보이·코파 트로피 전용.
OLY_STAGE = {k: max(QUAL_STAGE["통과"], v) for k, v in INTL_STAGE.items()}


def band(x, lo, hi):
    """x를 lo → 0, hi → 1로 선형 환산(0~1로 자름). lo > hi면 작을수록 좋은 지표."""
    if x is None:
        return 0.0
    t = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def match_save(gk_ovr, att_ovr, cap=CLUB_CAP):
    """엔진 기대 선방률 그대로(ai_lifecycle._gk_match_save)."""
    return _engine_match_save(gk_ovr, att_ovr, cap)


def env_shift(gk_ovr, segments, std_att=STD_ATT, cap=CLUB_CAP):
    """상대 공격 환경 몫 = (실제 상대에게 기대되는 선방률 − 표준 상대에게 기대되는 선방률), 그 GK 자신의 OVR로.
    segments: [(가중치, [상대 공격진 OVR, ...]), ...] — 소속팀 구간마다 하나(시즌 중 이적 GK는 이적 전·후 두 개).
    가중치는 그 구간의 받은 슈팅 수(없으면 출전 수). 구간 안에서는 엔진처럼 상대마다 기대 선방률을 구해 평균한다.
    상대 정보가 없는 구간은 빼고, 남는 게 없으면 0(보정 없음)."""
    tot_w, acc = 0.0, 0.0
    p_std = match_save(gk_ovr, std_att, cap)
    for w, atts in segments or ():
        atts = [a for a in (atts or ()) if a is not None]
        if not atts or not w or w <= 0:
            continue
        p_real = sum(match_save(gk_ovr, a, cap) for a in atts) / len(atts)
        acc += w * (p_real - p_std)
        tot_w += w
    return (acc / tot_w) if tot_w > 0 else 0.0


def std_save_rate(saves, conceded, gk_ovr, segments, std_att=STD_ATT):
    """(표준 환경 선방률 또는 None, 받은 슈팅 수). 받은 슈팅 = 선방 + 실점. 슈팅이 0이면 None."""
    sv, gc = int(saves or 0), int(conceded or 0)
    shots = sv + gc
    if shots <= 0:
        return None, 0
    return (sv / shots) - env_shift(gk_ovr, segments, std_att), shots


def shrink_rates(entries, k=SHRINK_SHOTS):
    """entries: [{"std_rate": 표준 환경 선방률 또는 None, "shots": 슈팅}] — 같은 시즌 후보 전원.
    후보 평균 = 보정된 선방률(std_rate)의 슈팅 가중 평균. 각자 shrunk = (std_rate×슈팅 + 평균×k) / (슈팅 + k).
    슈팅이 없는 후보는 평균(정보 없음)을 받는다. 반환: 후보 평균(그 시즌 기준점)."""
    num = sum(e["std_rate"] * e["shots"] for e in entries if e.get("std_rate") is not None and e.get("shots"))
    den = sum(e["shots"] for e in entries if e.get("std_rate") is not None and e.get("shots"))
    mean = (num / den) if den > 0 else 0.75
    for e in entries:
        r, s = e.get("std_rate"), e.get("shots") or 0
        e["shrunk"] = mean if (r is None or s <= 0) else (r * s + mean * k) / (s + k)
    return mean


def league_rank_value(rank, n_teams, relegated=False):
    """리그 최종 순위 점수. 1~4위 고정, 그 아래는 팀 수 비율(상위 30% 0.5 · 50% 0.35 · 75% 0.2 · 그 아래 0.1), 강등 0."""
    if relegated:
        return 0.0
    if not rank or not n_teams:
        return 0.0
    fixed = {1: 1.0, 2: 0.85, 3: 0.75, 4: 0.65}
    if rank in fixed:
        return fixed[rank]
    p = rank / float(n_teams)
    if p <= 0.30:
        return 0.5
    if p <= 0.50:
        return 0.35
    if p <= 0.75:
        return 0.2
    return 0.1


def comp_value(stage, table, weight=1.0, participation=1.0):
    """대회 단계 점수 × 대회 가중치 × 그 GK의 그 대회 출전 비율(0~1). 미참가(stage None) = 0."""
    if not stage:
        return 0.0
    return table.get(stage, 0.0) * max(0.0, weight) * max(0.0, min(1.0, participation))


def team_value(league_v, cup_v, cont_v):
    return (TEAM_SPLIT["league"] * league_v + TEAM_SPLIT["cup"] * cup_v + TEAM_SPLIT["cont"] * cont_v)


def score(e):
    """e: shrunk(축소된 표준 환경 선방률), matches(리그 출전), goals_conceded, clean_sheets, share(리그 출전 비율 0~1),
    team_v(④ 0~1), intl_v(⑤ 0~1). 반환: 항목별 점수와 합계(10점 만점)."""
    m = e.get("matches") or 0
    gcpm = (e.get("goals_conceded") or 0) / m if m else None
    cspm = (e.get("clean_sheets") or 0) / m if m else None
    s_save = W_SAVE * band(e.get("shrunk"), *SAVE_BAND)
    s_gc = W_GC * band(gcpm, *GC_BAND) if gcpm is not None else 0.0
    s_cs = W_CS * band(cspm, *CS_BAND) if cspm is not None else 0.0
    s_team = W_TEAM * max(0.0, min(1.0, e.get("team_v") or 0.0))
    s_intl = W_INTL * max(0.0, min(1.0, e.get("intl_v") or 0.0))
    s_part = W_PART * band(e.get("share"), *PART_BAND)
    total = s_save + s_gc + s_cs + s_team + s_intl + s_part
    return {"s_save": round(s_save, 4), "s_gc": round(s_gc, 4), "s_cs": round(s_cs, 4), "s_team": round(s_team, 4),
            "s_intl": round(s_intl, 4), "s_part": round(s_part, 4), "total": round(total, 4),
            "personal": round(s_save + s_gc + s_cs + s_part, 4), "gc_per_match": gcpm, "cs_per_match": cspm}


def sort_key(e):
    """최종 정렬 — 총점 → 축소된 표준 환경 선방률 → 경기당 실점(적을수록) → 선수 번호. DB 조회 순서와 무관."""
    gcpm = e.get("gc_per_match")
    return (-e["total"], -(e.get("shrunk") or 0.0), gcpm if gcpm is not None else 99.0, e["player_id"])