# -*- coding: utf-8 -*-
"""[2026-11 신설, 신민용 확정] 세계 베스트 11(FIFA·대륙별) 채점 — 순수 모듈(DB·게임 상태를 읽지 않음).

후보 = 1부 전체(game_engine._get_ballon_candidates(any_league=True)) 중 리그 출전 50% 이상, 입력은 발롱도르와 같은
game_engine._ballon_inputs(리그 수준 적용 범위 통일 포함 — ballon_award.productivity). 한 번 채점한 점수를 FIFA(전원)와
대륙별(국적 대륙으로 거른 후보)에 그대로 쓴다 — 세계상끼리 서로의 결과를 읽지 않고, 선정 순서와 무관하다.

총점(OVR 단위) = 실력 + K × 활약
  실력 = OVR 0.5 + 평점 환산 0.5. 평점 환산 = 60 + 35 × (시즌 평점 − 6.0)(AI 평점 공식의 역 — 평점을 OVR 눈금으로),
         OVR ± RAT_CAP 안으로 자른다. OVR·AI 평점 공식은 바꾸지 않는다.
  활약(0~1) = 기록 0.5 + 팀 성적 0.25 + 출전 0.10 + 국가대표 0.15
    기록: 공격수·미드필더 = 포지션 기대 대비 0.5 + 공격 생산량 0.5
          수비수 = (기대 대비 0.5 + 생산량 0.5) × 0.65 + 팀 실제 실점(× 출전 비율 × 리그 수준) × 0.35
          GK = 선방률 0.6 + 팀 실점 0.4. 개인 클린시트는 쓰지 않는다(AI 수비수 클린시트는 팀 결과와 거의 무관한 추정치).
          척도 = BANDS(고정값 — 2026-11 측정 두 시드 20년 후보 분포 p10 → 0, p99.5 → 0.9의 연도 평균, ballon_award.soft_band).
          생산량은 포지션 종류별. 고정값이라 선수 점수가 다른 후보와 무관하다(후보를 줄여 계산해도 같은 점수).
    팀 성적 = ballon_award.team_value(리그 순위 × 리그 수준 × 우승 난이도 + 국내컵 + 대륙 클럽대회) — 팀 내 감쇠 없음
              (베스트 11은 우승팀에서 여러 명이 나오는 상).
    출전 = 리그 출전 50% → 0, 100% → 1.  국가대표 = 국가대표 팀 성적(발롱도르 입력 intl_v, 0~1).
  K = 5 — 시즌 활약으로 뒤집을 수 있는 최대 OVR 폭(측정: 같은 OVR 쌍 74%가 활약으로 갈림, OVR 1 차이 역전 10%,
      3 이상 차이는 거의 안 뒤집힘).

포메이션 선택(자리 기준 정규화 — 선택에만 쓰고 선수 총점은 그대로):
  자리 종류(GK·CB·풀백·중앙 MF·측면 MF·공격형 MF·윙어·공격수)마다 세계 후보 상위 CLASS_TOP명의 총점 평균·표준편차.
  선수 p가 자리 s에 설 때의 값 = (p 총점 − s 종류 평균) / s 종류 표준편차. 제자리가 아니면 자기 포지션 종류 기준 값과
  비교해 낮은 쪽을 쓰고 게임과 같은 감점(× (1 − 0.10))을 준다 — 기준이 낮은 자리로 옮겨 점수가 부푸는 것을 막는다.
  포메이션 가산점은 없다.

검증: tools/test_best_xi.py, 측정 근거는 2026-11 측정(시드 105·106 20년)."""
import ballon_award as B
import yashin_award as Y

K = 5.0
RAT_CAP = 5.0
PERF_W = {"rec": 0.5, "team": 0.25, "part": 0.10, "intl": 0.15}
MIN_SHARE = 0.5
DF_TEAM = 0.35
GK_SAVE = 0.6
BAND_LO, BAND_HI = 0.10, 0.995
CLASS_TOP = 50
ADJ_PENALTY = 0.10
# 기록 척도(고정) — 기대 대비(GK 제외 전원), GK 선방률, 공격 생산량(포지션 종류별). calibrate_bands()로 다시 잴 수 있다.
BANDS = {"rel": (0.894, 1.894), "gk": (0.468, 0.684),
         ("vol", "CB"): (0.0, 5.478), ("vol", "FB"): (0.595, 5.923), ("vol", "CM"): (1.300, 13.783),
         ("vol", "WM"): (1.933, 10.406), ("vol", "AM"): (3.584, 19.976), ("vol", "W"): (3.295, 18.727),
         ("vol", "ST"): (5.721, 29.486), ("vol", "GK"): (0.0, 1.221)}
# 총점 범위 — 실력 = OVR ± RAT_CAP/2, 활약 0~1 → [OVR − RAT_CAP/2, OVR + RAT_CAP/2 + K]. 후보 줄이기(game_engine)에 쓴다.
TOTAL_LO = -RAT_CAP / 2.0
TOTAL_HI = RAT_CAP / 2.0 + K

PCLASS = {"GK": "GK", "CB": "CB", "LB": "FB", "RB": "FB", "LWB": "FB", "RWB": "FB", "CDM": "CM", "CM": "CM",
          "LM": "WM", "RM": "WM", "CAM": "AM", "LW": "W", "RW": "W", "ST": "ST", "CF": "ST"}
_GROUP = {"GK": "GK", "CB": "DF", "FB": "DF", "CM": "MF", "WM": "MF", "AM": "MF", "W": "FW", "ST": "FW"}


def pclass(pos):
    return PCLASS.get(pos or "")


def group(pos):
    k = pclass(pos)
    return _GROUP.get(k) if k else None


def _q(vals, p):
    s = sorted(v for v in vals if v is not None)
    if not s:
        return 0.0
    k = (len(s) - 1) * p
    i = int(k)
    f = k - i
    return s[i] + (s[min(i + 1, len(s) - 1)] - s[i]) * f


def eligible(e):
    """리그 출전 50% 이상이고 베스트 11 포지션인가."""
    return (e.get("share") or 0.0) >= MIN_SHARE and group(e.get("position")) is not None


def _raw(e):
    p = B.productivity(e)
    lv, cu, co = B.team_parts(e)
    return {"rel_rate": p["rel_rate"], "vol_raw": p["vol_raw"], "tdef": p["tdef"], "team_v": B.team_value(lv, cu, co)}


def calibrate_bands(pool, raw):
    """(측정용) 후보 분포로 척도를 잰다 — BANDS를 다시 정할 때 쓴다. 게임 채점은 BANDS 고정값."""
    rel = [raw[id(e)]["rel_rate"] for e in pool if group(e["position"]) != "GK"]
    gk = [e.get("gk_save") for e in pool if group(e["position"]) == "GK"]
    bd = {"rel": (_q(rel, BAND_LO), _q(rel, BAND_HI)), "gk": (_q(gk, BAND_LO), _q(gk, BAND_HI))}
    for k in set(PCLASS.values()):
        v = [raw[id(e)]["vol_raw"] for e in pool if pclass(e["position"]) == k]
        if v:
            bd["vol", k] = (_q(v, BAND_LO), _q(v, BAND_HI))
    return bd


def _band(x, lo_hi):
    lo, hi = lo_hi
    if hi <= lo:
        return 0.0
    return B.soft_band(x, lo, hi)


def score_pool(entries, ratings):
    """entries(_ballon_inputs 결과) · ratings({player_id: 시즌 평점}) → (후보 목록, {player_id: 점수 dict}, 자리 종류 기준).
    후보 = eligible. 입력은 바꾸지 않는다(점수는 따로 돌려준다). 점수 dict: total, ability, rec, team, part, intl."""
    pool = [e for e in entries if eligible(e)]
    if not pool:
        return [], {}, {}
    raw = {id(e): _raw(e) for e in pool}
    bd = BANDS
    out = {}
    for e in pool:
        r = raw[id(e)]
        pos = e["position"]
        g = group(pos)
        o = float(e.get("ovr") or 0)
        rt = ratings.get(e["player_id"])
        r_eq = 60.0 + 35.0 * ((rt if rt else 6.0 + (o - 60.0) / 35.0) - 6.0)
        r_eq = max(o - RAT_CAP, min(o + RAT_CAP, r_eq))
        ability = 0.5 * o + 0.5 * r_eq
        rel = _band(r["rel_rate"], bd["rel"])
        vol = _band(r["vol_raw"], bd.get(("vol", pclass(pos)), (0.0, 0.0)))
        if g == "GK":
            rec = GK_SAVE * _band(e.get("gk_save"), bd["gk"]) + (1 - GK_SAVE) * r["tdef"]
        elif g == "DF":
            rec = (1 - DF_TEAM) * (0.5 * rel + 0.5 * vol) + DF_TEAM * r["tdef"]
        else:
            rec = 0.5 * rel + 0.5 * vol
        part = Y.band(e.get("share"), MIN_SHARE, 1.0)
        team = max(0.0, min(1.0, r["team_v"]))
        intl = max(0.0, min(1.0, e.get("intl_v") or 0.0))
        perf = PERF_W["rec"] * rec + PERF_W["team"] * team + PERF_W["part"] * part + PERF_W["intl"] * intl
        out[e["player_id"]] = {"total": ability + K * perf, "ability": ability, "rec": K * PERF_W["rec"] * rec,
                               "team": K * PERF_W["team"] * team, "part": K * PERF_W["part"] * part,
                               "intl": K * PERF_W["intl"] * intl}
    by = {}
    for e in pool:
        by.setdefault(pclass(e["position"]), []).append(out[e["player_id"]]["total"])
    stats = {}
    for k, v in by.items():
        top = sorted(v, reverse=True)[:CLASS_TOP]
        mu = sum(top) / len(top)
        sd = (sum((t - mu) ** 2 for t in top) / len(top)) ** 0.5
        stats[k] = (mu, sd if sd > 0 else 1.0)
    return pool, out, stats


def slot_value(total, own_pos, slot, stats):
    """포메이션 선택용 값 — 자리 s 종류 기준 정규화, 제자리가 아니면 min(자리 기준, 자기 포지션 기준) − 감점."""
    ks, ko = pclass(slot), pclass(own_pos)
    mu, sd = stats.get(ks) or stats.get(ko) or (0.0, 1.0)
    v = (total - mu) / sd
    if slot != own_pos:
        if ko in stats:
            mu2, sd2 = stats[ko]
            v = min(v, (total - mu2) / sd2)
        v -= ADJ_PENALTY * abs(v)
    return v