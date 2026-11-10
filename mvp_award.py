# -*- coding: utf-8 -*-
"""[2026-11 신설, 신민용 확정] MVP 계열 개인상 채점 — 순수 모듈(DB·게임 상태를 읽지 않음).

바꾼 이유(2026-11 측정, 시드 105·106 20시즌 후보 풀): 예전 공식(평점 × 출전 × 진출 단계)은
  · 컵·국제대회: 진출 단계 계수(우승 1.00 / 준우승 0.95 / 4강 0.90 …)가 평점 차이보다 커서 우승팀 쏠림
    (챔스급 MVP 91%·대륙 선수권 94%가 우승팀, 올림픽 볼 18명 전원 우승국 — 후보 중 우승팀은 4~6%)
  · 리그: AI 평점(OVR + 골 0.02 + 도움 0.015) 1위가 그대로 MVP라 ST 편중(SS·S·A 1부 ST 94%, DF·GK 0%)

1) 대회(V11) — 국내컵·하부컵·클럽 대항전(챔스·유로파·컨퍼런스)·클럽월드컵·국가대표 대회·올림픽의
   MVP·골든/실버/브론즈볼·영플레이어·올해의 수비수. 대륙 슈퍼컵은 측정 대상이 아니라 예전 그대로.
   점수 = 예전 공식(평점 계수 × 출전 계수 × 진출 단계)에서 두 가지만 바꾼다.
     · 진출 단계 계수 차이를 STAGE_DIFF(30%)로: 1 − 0.3 × (1 − 예전 계수)
       (우승 1.00 / 준우승 0.985 / 4강 0.97 / 첫 녹아웃 0.904 / 조별 0.88)
     · 출전 분모 = 그 대회 최다 경기 팀(국가)의 경기 수(예전: 자기 팀 경기 수 — 일찍 탈락한 팀 선수가
       몇 경기만 뛰고 100%가 됐다)
   후보 풀·평점 계수·출전 계수 하한은 예전 그대로(game_engine._comp_award_score).

2) 리그 MVP·리그 영플레이어(C λ0.3 + D) — 리그 올해의 수비수는 예전 그대로.
   후보 = 그 리그 시즌 경기 수의 MIN_SHARE(50%) 이상 출전(+ 예전 역할 자격). 50% 이상끼리는 예전 출전 계수.
   점수 = 예전 리그 공식(평점 계수 × 출전 계수)에 평점 대신 보정 평점을 넣는다.
     보정 평점 = (1 − λ) × 평점 + λ × z 환산 + β × 기록
       z 환산 = 그 리그 후보 평균 + 그룹 전체 표준편차 × (평점 − 그 리그 그 포지션 평균) ÷ 그룹 그 포지션 표준편차
                (같은 포지션 안 위치를 전체 평점 분포로 옮김 — 상위 리그의 ST·CB 평균 차이(0.32)가 B 이하(0.15)의
                 두 배라 "평균 차이 × γ"로는 리그 수준마다 보정량이 달랐다). 그 리그에 그 포지션 후보가
                 3명 미만이면 원래 평점.
       β = BETA × (그 리그 후보 평점 표준편차 ÷ SD_REF) — 평점 폭이 좁은 리그에서 기록 가산이 과하지 않게.
       기록 = 새 베스트11(comp_xi_award)의 기록 정의(기대 대비·누적 생산량, 수비수 팀 실점 0.35, GK 선방률
              슈팅 30개 축소 0.6 + 팀 실점 0.4)를 리그 수준 3그룹(SS·S·A 1부 / B 이하 1부 / 2부 이하)별
              척도로 0~0.9 환산.
   척도·표준편차(BANDS) = 2026-11 측정(시드 105·106 20시즌, 그 그룹 후보 분포 p10 → 0, p99.5 → 0.9,
   표준편차는 리그 안 표준편차의 그룹 중앙값) 고정값 — 같은 기록이면 다른 후보와 무관하게 같은 점수.

측정 근거: 2026-11 MVP 계열 측정(시드 105·106 20시즌 후보 풀 기록 런). 검증: tools/test_mvp_award.py."""
import ballon_award as B
import best_xi_award as BX
import comp_xi_award as CX

# ── 대회(V11) ───────────────────────────────────────────────────
STAGE_DIFF = 0.3          # 진출 단계 계수 차이를 예전의 30%로
STAGE_LO = 0.60           # 계수가 없을 때(게임 _TSF_GROUP과 같은 값)

# ── 리그(C λ0.3 + D) ────────────────────────────────────────────
MIN_SHARE = 0.5
LAMBDA = 0.3
BETA = 0.3
SD_REF = 0.1499           # SS·S·A 1부 리그 안 평점 표준편차(그룹 중앙값)
SD_FLOOR = 0.02
MIN_CLASS = 3             # 그 리그에 그 포지션 종류 후보가 이보다 적으면 z 환산 안 함
DF_TEAM = 0.35            # 수비수 기록 = 0.65 × 공격 기록 + 0.35 × 팀 실점(새 베스트11과 같은 값)
GK_SAVE = 0.6
GK_SHRINK = 30.0

GROUP_TOP, GROUP_MID, GROUP_LOW = "SS·S·A 1부", "B 이하 1부", "2부 이하"
GROUPS = (GROUP_TOP, GROUP_MID, GROUP_LOW)
TOP_GRADES = ("SS", "S", "A")

# 그룹별 척도: rel(기대 대비) · 포지션 종류별 누적 생산량 · gc(팀 경기당 실점, 높음 → 낮음) · gk(축소 선방률) ·
# gkmean(축소 기준 평균) · sd(평점 표준편차, "_all" = 리그 후보 전체)
BANDS = {
    GROUP_TOP: {"rel": (0.7685, 1.4043), "gc": (1.7895, 0.5862), "gk": (0.699, 0.8339), "gkmean": 0.7421,
                "AM": (4.25, 22.75), "CB": (1.0, 5.25), "CM": (1.75, 15.25), "FB": (0.75, 6.0),
                "ST": (6.5, 32.5), "W": (3.5, 21.0), "WM": (2.5, 11.5),
                "sd": {"AM": 0.0848, "CB": 0.0806, "CM": 0.0992, "FB": 0.0821, "GK": 0.1135, "ST": 0.0991,
                       "W": 0.0883, "WM": 0.0745, "_all": 0.1499}},
    GROUP_MID: {"rel": (0.7557, 1.287), "gc": (1.4667, 0.5333), "gk": (0.6263, 0.8016), "gkmean": 0.6812,
                "AM": (3.75, 18.25), "CB": (0.0, 5.25), "CM": (1.5, 13.0), "FB": (0.75, 5.75),
                "ST": (6.5, 26.75), "W": (3.5, 17.25), "WM": (2.5, 9.25),
                "sd": {"AM": 0.0964, "CB": 0.1024, "CM": 0.1056, "FB": 0.1009, "GK": 0.1109, "ST": 0.104,
                       "W": 0.0973, "WM": 0.0835, "_all": 0.1223}},
    GROUP_LOW: {"rel": (0.762, 1.287), "gc": (1.5806, 0.5667), "gk": (0.6074, 0.7911), "gkmean": 0.6637,
                "AM": (4.25, 20.0), "CB": (0.0, 5.25), "CM": (1.5, 14.25), "FB": (0.75, 5.75),
                "ST": (6.5, 29.5), "W": (3.5, 19.25), "WM": (2.5, 10.25),
                "sd": {"AM": 0.1064, "CB": 0.1087, "CM": 0.1162, "FB": 0.1097, "GK": 0.123, "ST": 0.1148,
                       "W": 0.1048, "WM": 0.0905, "_all": 0.1393}},
}


# ── 대회(V11) ───────────────────────────────────────────────────
def stage_v11(stage_factor):
    """예전 진출 단계 함수 → 차이를 STAGE_DIFF로 줄인 함수(None이면 None — 녹아웃 없는 대회)."""
    if stage_factor is None:
        return None

    def f(key):
        s = stage_factor(key)
        return 1.0 - STAGE_DIFF * (1.0 - (STAGE_LO if s is None else s))
    return f


def max_matches(pool, part_denom, part_key="team_id"):
    """출전 분모 = 그 대회 후보 풀에 있는 팀(국가) 중 최다 경기 수. 분모를 모르면 None(출전 계수 1.0)."""
    if not isinstance(part_denom, dict):
        return part_denom
    vals = [part_denom.get(x.get(part_key)) for x in pool or []]
    vals = [v for v in vals if v]
    return max(vals) if vals else None


# ── 리그(C λ0.3 + D) ────────────────────────────────────────────
def league_group(grade, tier):
    """리그 수준 그룹 — 2부 이하 / (1부) SS·S·A / (1부) B 이하."""
    if (tier or 1) > 1:
        return GROUP_LOW
    return GROUP_TOP if grade in TOP_GRADES else GROUP_MID


def league_share(matches, full_season):
    return CX.share_of(matches, full_season)


def league_eligible(e, full_season):
    """후보 자격(역할 자격은 호출부) — 시즌 경기 수의 50% 이상 + 포지션 그룹이 있는 선수."""
    pos = e.get("xi_pos") or e.get("position")
    return ((e.get("matches") or 0) > 0 and BX.group(pos) is not None
            and league_share(e.get("matches"), full_season) >= MIN_SHARE)


def _sband(x, lo_hi, rev=False):
    lo, hi = lo_hi
    if rev:
        x, lo, hi = -x, -lo, -hi
    if hi <= lo:
        return 0.0
    return B.soft_band(x, lo, hi)


def record_score(e, group, full_season):
    """기록 점수(0~0.9 근처, 상한 위로는 완만) — 새 베스트11 기록 정의 + 그 그룹 척도.
    e 키: position(또는 xi_pos), goals, assists, matches, saves, goals_conceded, gcpm(팀 경기당 실점, 없으면 None)."""
    bd = BANDS[group]
    pos = e.get("xi_pos") or e.get("position")
    g, a, m = e.get("goals") or 0, e.get("assists") or 0, e.get("matches") or 0
    share = league_share(m, full_season)
    gcpm = e.get("gcpm")
    tdef = _sband(gcpm, bd["gc"], rev=True) * share if gcpm is not None else 0.0
    grp = BX.group(pos)
    if grp == "GK":
        sv, gc = e.get("saves") or 0, e.get("goals_conceded") or 0
        rate = (sv + bd["gkmean"] * GK_SHRINK) / (sv + gc + GK_SHRINK)
        return GK_SAVE * _sband(rate, bd["gk"]) + (1 - GK_SAVE) * tdef
    pm = B.league_per_match(pos, 38, 1.0)
    rel = (g + a + B.REL_SHRINK) / (pm * m + B.REL_SHRINK)
    vol = g + B.ASSIST_W * a
    r = 0.5 * _sband(rel, bd["rel"]) + 0.5 * _sband(vol, bd.get(BX.pclass(pos), (0.0, 0.0)))
    if grp == "DF":
        r = (1 - DF_TEAM) * r + DF_TEAM * tdef
    return r


def _mean(v):
    return sum(v) / len(v)


def _pstdev(v):
    if len(v) < 2:
        return 0.0
    mu = _mean(v)
    return (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5


def league_adjusted_ratings(elig, group, full_season):
    """그 리그 50% 후보(elig, 역할 자격 무관 — 평균·표준편차는 후보 전체로) → {player_id: 보정 평점}.
    입력은 바꾸지 않는다. 순서와 무관하게 같은 결과."""
    if not elig:
        return {}
    bd = BANDS[group]
    sds = bd["sd"]
    sd_all = sds["_all"]
    rt = lambda e: e.get("rating") or 6.0
    cls = lambda e: BX.pclass(e.get("xi_pos") or e.get("position"))
    by = {}
    for e in elig:
        by.setdefault(cls(e), []).append(rt(e))
    mu = _mean([rt(e) for e in elig])
    cmu = {k: _mean(v) for k, v in by.items()}
    sd_comp = _pstdev([rt(e) for e in elig]) if len(elig) > 1 else sd_all
    beta = BETA * sd_comp / SD_REF
    out = {}
    for e in elig:
        r0 = rt(e)
        c = cls(e)
        if len(by.get(c, ())) >= MIN_CLASS:
            sdc = max(sds.get(c, sd_all), SD_FLOOR)
            r1 = (1 - LAMBDA) * r0 + LAMBDA * (mu + sd_all * (r0 - cmu[c]) / sdc)
        else:
            r1 = r0
        out[e["player_id"]] = r1 + beta * record_score(e, group, full_season)
    return out