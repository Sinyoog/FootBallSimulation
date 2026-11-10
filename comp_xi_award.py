# -*- coding: utf-8 -*-
"""[2026-11 신설, 신민용 확정] 대회 베스트 11 채점 — 순수 모듈(DB·게임 상태를 읽지 않음). 리그·국내컵·하부컵·클럽 대항전·
클럽월드컵·국가대표 대회의 "베스트11" 선정이 이 채점을 쓴다(game_engine._comp_xi_pick → _comp_xi_apply가 예전
_pick_comp_extra_winners의 평점 순 베스트11을 바꾼다). 같은 대회의 다른 상은 그대로다. FIFA·대륙 베스트 11은
best_xi_award(세계상)로 따로다. 후보와 기록은 그 대회 것만 쓴다(리그 베스트 11은 리그 기록만, 월드컵은 월드컵만).
바꾼 이유(2026-11 측정, 시드 105·106 20시즌): 예전 선정은 국가대표 대회에서 우승국 선수가 평균 10/11명, 클럽 대항전
7.6명이었고 국내컵 명단의 7.6%는 그 대회 최다 경기 팀 대비 출전 50% 미만이었다.

대상 대회: 리그(1부 / 2부 이하), 국내컵, 하부컵, 클럽 대항전(챔스·유로파·컨퍼런스), 클럽월드컵, 월드컵, 대륙 선수권,
지역컵, 올림픽. 슈퍼컵은 대상이 아니다(1~2경기라 표본이 없음 — 슈퍼컵 베스트11은 예전 선정 그대로).

후보 = 그 대회 출전이 그 대회 최다 경기 팀 경기 수의 MIN_SHARE(50%) 이상. 자기 팀 경기 수 대비가 아니다 — 조기
탈락 팀 선수가 1~2경기만 뛰고 100%가 되지 않게(측정: 국내컵 선정자 중 2경기 이하 44% → 2%).

총점(OVR 단위) = 실력 + K × 활약
  실력 = OVR 0.5 + 그 대회 평점 환산 0.5. 평점 환산 = 60 + 35 × (평점 − 6.0)(AI 평점 공식의 역), OVR ± RAT_CAP 안.
         실력 압축은 하지 않는다(측정: 약한 리그 선수 진입은 그대로이고 OVR 역전만 늘었다).
  활약(0~1) = 기록 0.588 + 팀 성적 0.294 + 출전 0.118 (세계 베스트 11의 0.5 : 0.25 : 0.10, 국가대표 몫 없음)
    기록: 공격수·미드필더 = 포지션 기대 대비(골+도움, ballon_award.REL_SHRINK만큼 1 쪽으로 축소) 0.5 + 누적 생산량
          (골 + 0.75 × 도움, 포지션 종류별 척도) 0.5 — 누적이라 오래 살아남은 팀 선수가 유리하다(대회 취지).
          수비수 = 위 값 × 0.65 + 그 대회 팀 경기당 실점(× 출전 비율) × 0.35.
          GK = 그 대회 선방률(슈팅 GK_SHRINK개만큼 대회 유형 평균 쪽으로 축소) 0.6 + 팀 실점 0.4.
    팀 성적: 리그 = 순위 점수(yashin_award.league_rank_value), 토너먼트 = 진출 단계 계수(게임 0.60~1.00 → 0~1).
    출전 = 50% → 0, 100% → 1.
  리그 수준·대회 수준 보정, 공격포인트 상위 보장, 팀별 인원 제한, 조건부 감점은 없다(전부 측정 후 미채택).
  척도 BANDS = 2026-11 측정(시드 105·106 20시즌, 그 대회 유형 후보 분포 p10 → 0, p99.5 → 0.9) 고정값.

포메이션 = 세계 베스트 11과 같은 자리 기준 정규화(best_xi_award.slot_value) — 그 대회 후보의 포지션 종류별 상위
CLASS_TOP명 총점 평균·표준편차(종류에 후보가 3명 미만이면 같은 그룹 값, 표준편차 하한 SD_FLOOR). 가산점 없음.

검증: tools/test_comp_xi.py, tools/comp_xi_check.py. 측정 근거: 2026-11 대회 베스트 11 측정."""
import ballon_award as B
import yashin_award as Y
import best_xi_award as BX

K = 5.0
RAT_CAP = 5.0
W_REC, W_TEAM, W_PART = 0.5 / 0.85, 0.25 / 0.85, 0.10 / 0.85
MIN_SHARE = 0.5
DF_TEAM = 0.35
GK_SAVE = 0.6
GK_SHRINK = 30.0
CLASS_TOP = 10
SD_FLOOR = 0.5
STAGE_LO, STAGE_HI = 0.60, 1.00       # 게임 진출 단계 계수 범위(_TSF_*: 조별 0.60 ~ 우승 1.00)

# 대회 유형(게임 경로가 정해 넘긴다)
TYPES = ("league1", "league2", "cup", "lower_cup", "club", "cwc", "world", "continent", "region", "olympic")
TYPE_NAME = {"league1": "리그 1부", "league2": "리그 2부 이하", "cup": "국내컵", "lower_cup": "하부컵",
             "club": "클럽 대항전", "cwc": "클럽월드컵", "world": "월드컵", "continent": "대륙 선수권",
             "region": "지역컵", "olympic": "올림픽"}
INTL_KIND_TYPE = {"world": "world", "continent": "continent", "region": "region", "olympic": "olympic"}

# 척도(고정) — rel: 기대 대비, gk: 축소 선방률, gkmean: 축소 기준 평균, 포지션 종류: 누적 생산량.
BANDS = {
    "league1": {"rel": (0.7557, 1.3025), "gk": (0.6541, 0.8019), "gkmean": 0.7013, "CB": (0.0000, 5.2500), "FB": (0.7500, 5.7500), "CM": (1.5000, 13.5000), "WM": (2.5000, 9.7500), "AM": (3.7500, 18.7500), "W": (3.5000, 18.2500), "ST": (6.5000, 27.7500)},
    "league2": {"rel": (0.7622, 1.2893), "gk": (0.6289, 0.7807), "gkmean": 0.6753, "CB": (0.0000, 5.2500), "FB": (0.7500, 5.7500), "CM": (1.5000, 14.2500), "WM": (2.5000, 10.2500), "AM": (4.2500, 20.0000), "W": (3.5000, 19.2500), "ST": (6.5000, 29.5000)},
    "cup": {"rel": (0.9406, 1.4388), "gk": (0.6700, 0.7979), "gkmean": 0.7156, "CB": (0.0000, 1.7500), "FB": (0.0000, 1.7500), "CM": (0.0000, 5.0000), "WM": (0.0000, 3.7500), "AM": (0.0000, 6.7500), "W": (0.0000, 6.7500), "ST": (1.0000, 9.7500)},
    "lower_cup": {"rel": (0.9310, 1.3225), "gk": (0.6908, 0.8055), "gkmean": 0.7314, "CB": (0.0000, 1.5000), "FB": (0.0000, 1.5000), "CM": (0.0000, 3.7500), "WM": (0.0000, 3.0000), "AM": (0.0000, 5.2500), "W": (0.0000, 5.2500), "ST": (0.7500, 7.7500)},
    "club": {"rel": (0.8429, 1.4844), "gk": (0.6314, 0.8260), "gkmean": 0.7100, "CB": (0.0000, 3.5000), "FB": (0.0000, 3.5000), "CM": (0.0000, 7.7500), "WM": (0.0000, 6.0000), "AM": (0.7500, 10.8625), "W": (0.7500, 11.7500), "ST": (1.0000, 15.2500)},
    "cwc": {"rel": (0.9350, 1.6077), "gk": (0.7332, 0.8337), "gkmean": 0.7721, "CB": (0.0000, 1.8687), "FB": (0.0000, 2.9350), "CM": (0.0000, 6.5650), "WM": (0.0000, 2.7500), "AM": (0.7500, 8.3450), "W": (0.3000, 4.7500), "ST": (1.6750, 11.1025)},
    "world": {"rel": (0.9214, 1.7038), "gk": (0.7387, 0.8491), "gkmean": 0.7846, "CB": (0.0000, 2.0000), "FB": (0.0000, 1.8438), "CM": (0.0000, 4.8050), "WM": (0.3750, 11.9000), "AM": (0.7500, 11.3787), "W": (1.0000, 7.1062), "ST": (1.0000, 11.4000)},
    "continent": {"rel": (0.9131, 1.5453), "gk": (0.6905, 0.8664), "gkmean": 0.7525, "CB": (0.0000, 3.0000), "FB": (0.0000, 3.5000), "CM": (0.0000, 6.2500), "WM": (0.7500, 9.5575), "AM": (0.7500, 9.4150), "W": (0.7500, 9.7175), "ST": (1.0000, 10.9312)},
    "region": {"rel": (0.9009, 1.4817), "gk": (0.6192, 0.8407), "gkmean": 0.7009, "CB": (0.0000, 3.5000), "FB": (0.0000, 3.5000), "CM": (0.0000, 6.7500), "WM": (0.0000, 7.5225), "AM": (0.0000, 8.3100), "W": (0.0000, 6.9600), "ST": (0.0000, 10.7500)},
    "olympic": {"rel": (0.9248, 1.4886), "gk": (0.7397, 0.8696), "gkmean": 0.7824, "CB": (0.0000, 2.5000), "FB": (0.0000, 2.2750), "CM": (0.0000, 4.7013), "WM": (0.7500, 6.7825), "AM": (0.7500, 9.1075), "W": (0.0750, 4.7500), "ST": (1.0000, 11.0550)},
}


def share_of(matches, max_matches):
    """그 대회 최다 경기 팀 경기 수 대비 출전 비율(0~1). 분모가 없으면 1."""
    if not max_matches:
        return 1.0
    return min(1.0, (matches or 0) / float(max_matches))


def eligible(e):
    return (e.get("share") or 0.0) >= MIN_SHARE and BX.group(e.get("pos")) is not None and (e.get("matches") or 0) > 0


def team_value_league(rank, n_teams):
    return Y.league_rank_value(rank, n_teams)


def team_value_stage(stage_factor):
    sf = STAGE_LO if stage_factor is None else stage_factor
    return max(0.0, min(1.0, (sf - STAGE_LO) / (STAGE_HI - STAGE_LO)))


def ovr_from_rating(rating, goals=0, assists=0):
    """OVR 이력이 없을 때만 — AI 평점 공식(6.0 + (OVR−60)/35 + 골 0.02 + 도움 0.015)의 역."""
    return 60 + 35 * ((rating or 6.0) - 6.0 - 0.02 * (goals or 0) - 0.015 * (assists or 0))


def _band(x, lo_hi):
    lo, hi = lo_hi
    if hi <= lo:
        return 0.0
    return B.soft_band(x, lo, hi)


def _raw(e):
    pm = B.league_per_match(e["pos"], 38, 1.0)
    g, a, m = e.get("goals") or 0, e.get("assists") or 0, e.get("matches") or 0
    rel = (g + a + B.REL_SHRINK) / (pm * m + B.REL_SHRINK)
    vol = g + B.ASSIST_W * a
    gcpm = e.get("gcpm")
    tdef = (B.band(gcpm, *B.TEAM_GC_BAND) * e["share"]) if gcpm is not None else 0.0
    return rel, vol, tdef


def score_pool(entries, ctype):
    """entries(eligible 통과분, 아래 키) · 대회 유형 → {player_id: 점수 dict(total, ability, rec, team, part)}.
    entry 키: player_id, pos, matches, goals, assists, rating, saves, goals_conceded, share, ovr, team(0~1), gcpm.
    입력은 바꾸지 않는다. 같은 기록이면 다른 후보와 무관하게 같은 점수(척도 고정)."""
    bd = BANDS[ctype]
    out = {}
    for e in entries:
        rel, vol, tdef = _raw(e)
        grp = BX.group(e["pos"])
        o = float(e["ovr"])
        rt = e.get("rating")
        r_eq = 60 + 35 * ((rt if rt else 6 + (o - 60) / 35) - 6.0)
        r_eq = max(o - RAT_CAP, min(o + RAT_CAP, r_eq))
        ability = 0.5 * o + 0.5 * r_eq
        if grp == "GK":
            sv, gc = e.get("saves") or 0, e.get("goals_conceded") or 0
            rate = (sv + bd["gkmean"] * GK_SHRINK) / (sv + gc + GK_SHRINK)
            rec = GK_SAVE * _band(rate, bd["gk"]) + (1 - GK_SAVE) * tdef
        else:
            b_rel = _band(rel, bd["rel"])
            b_vol = _band(vol, bd.get(BX.pclass(e["pos"]), (0.0, 0.0)))
            rec = 0.5 * b_rel + 0.5 * b_vol
            if grp == "DF":
                rec = (1 - DF_TEAM) * rec + DF_TEAM * tdef
        team = max(0.0, min(1.0, e.get("team") or 0.0))
        part = Y.band(e["share"], MIN_SHARE, 1.0)
        out[e["player_id"]] = {"total": ability + K * (W_REC * rec + W_TEAM * team + W_PART * part),
                               "ability": ability, "rec": K * W_REC * rec, "team": K * W_TEAM * team,
                               "part": K * W_PART * part}
    return out


def class_stats(entries, scores):
    """자리 종류별 (평균, 표준편차) — 그 대회 후보 상위 CLASS_TOP명. 후보 3명 미만인 종류는 같은 그룹 값."""
    def _ms(v):
        top = sorted(v, reverse=True)[:CLASS_TOP]
        mu = sum(top) / len(top)
        sd = (sum((t - mu) ** 2 for t in top) / len(top)) ** 0.5
        return mu, max(sd, SD_FLOOR)
    by, byg = {}, {}
    for e in entries:
        t = scores[e["player_id"]]["total"]
        by.setdefault(BX.pclass(e["pos"]), []).append(t)
        byg.setdefault(BX.group(e["pos"]), []).append(t)
    stg = {g: _ms(v) for g, v in byg.items()}
    return {k: (_ms(v) if len(v) >= 3 else stg[BX._GROUP[k]]) for k, v in by.items()}


def slot_value(total, own_pos, slot, stats):
    return BX.slot_value(total, own_pos, slot, stats)