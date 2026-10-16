# -*- coding: utf-8 -*-
"""manager_salary.py — 감독 연봉 (2026-10 신설, 신민용 확정)

연봉(계약 시) = 기준값(2026년 기준 최저/평균/상위) 위의 위치 s × economy_index(서명 연도)
  · 계약을 맺을 때(부임·재계약) 한 번 정하고 계약 기간 내내 고정한다 — 재계약 때만 새 금액.
  · 기준값: 아래 LEAGUE_SALARY_DOC에 있는 리그는 그 값, 나머지는 그 리그 선수 연봉 중앙값 × 1.0
    (economy._calc_salary, 2026년 기준) — 단 부수별 하한(1부 0.05억 · 2부 0.02억 · 3부 이하 0.01억).
    자동 산정 리그의 폭은 평균 × 0.38(최저) / × 2.5(상위).
  · 위치 s = 0.5 + 구단 위상(±0.25) + 평판(±0.15) + 경력(±0.10) + 고정 변동(±0.05), 0~1로 자름.
    s=0 최저, 0.5 평균, 1 상위(사이는 기하 보간). 감독 능력치는 쓰지 않는다.
    - 구단 위상: 리그 안 상위 11명 평균 OVR 순위로 7단계(명문 +0.25 / 상위권 +0.15 / 중상위권 +0.05 /
      중위권 0 / 중하위권 −0.05 / 하위권 −0.15 / 최하위권 −0.25). 팀 수로 등급 인원을 먼저 정하고
      (위아래 대칭, 가운데로 갈수록 같거나 많음, 목표 비율 10/15/15/20/15/15/10%) 순위대로 채운다.
    - 평판: 같은 리그(대표팀은 같은 국가 등급) 현직 감독 평판 중앙값과의 차이 ÷ 20, ±1로 자름.
    - 경력: (경력 최고 수준 ÷ 지금 자리 수준) − 같은 리그(대표팀은 같은 등급) 현직 감독 중앙값, ÷ 0.5, ±1로 자름.
      [2026-10 중앙값 기준, 신민용 확정] 예전엔 둘 다 "평균"과 비교했다. 평판은 소수 감독에게 몰리므로
      (10시즌 실측: 계약 감독 평판 중앙값 0.12 vs 평균 4.77) 평균과 비교하면 현직의 약 2/3가 평균 아래가 돼
      리그 연봉 중앙값이 해마다 내려갔다(1부 문서 리그 −2% → −10%). 중앙값과 비교하면 절반이 위, 절반이
      아래다 — 10시즌 기록으로 다시 계산한 문서 리그 ±15% 밖: 평균 113건 → 중앙값 47건. 클럽·성인·U-23
      대표팀 모두 같은 규칙(REF_STAT). 승강 조정 규칙은 그대로.
      [2026-10 대칭화] 예전엔 "÷ 자리 수준 − 1"이라 부임 때 경력 최고 수준이 max로 갱신되는 구조상
      0~+0.10으로만 움직여 하위 리그 연봉을 한쪽으로 끌어올렸다(10시즌 검증에서 확인).
    - 고정 변동: (감독, 자리, 서명 연도)에서 만든 고정 난수 — 전역 random을 쓰지 않는다.
  · 대표팀: 국가 등급 S~F 표(NATIONAL_SALARY), 구단 위상 대신 같은 등급 안 국가 서열(최강 +0.25 ~ 최약 −0.25).
  · U-23 대표팀: 같은 나라·같은 감독 위치의 성인 대표팀 금액 × (0.40 + 0.20 × s_평판경력).
    s_평판경력 = 0.5 + 0.5 × (0.6 × 평판 + 0.4 × 경력) — 평판·경력이 양끝이면 정확히 0.40 / 0.60.
    대표팀(성인·U-23) 감독의 경력 위치는 그 등급 성인 대표팀 자리 수준으로 잰다(같은 경력 = 같은 성인 기준).

[계약 중 연봉 고정 — 명시적 예외: 승강 조정 조항] (2026-10 신민용 확정)
  계약 기간 중 연봉은 바뀌지 않는다. 단 하나의 예외: 클럽이 승격·강등(리그가 바뀜)하면, 그 계약이
  다음 시즌 이후까지 남아 있는 경우에 한해 새 리그 기준으로 연봉을 다시 계산한다.
  · 계약 만료 연도는 그대로 — contract_log는 건드리지 않는다.
  · 조정 금액은 승강이 정해진 해(Y)의 경제지수로 계산하고, 다음 시즌(Y+1)부터 적용한다(재계약과 같은 규칙).
  · 고정 변동은 그 계약 칸의 원래 서명 연도 값을 그대로 쓴다(계약 중 다시 뽑지 않는다 — 2026-10 확정).
  · 기록: salary_log의 그 계약 칸 안에 '|'로 "Y:금액:새리그id:P(승격)/R(강등)/L(같은 부 리그 이동)"을 덧붙인다.
  · 그 해로 끝나는 계약은 조정하지 않는다(다음 시즌에 적용할 계약이 없음). 재계약은 새 계약 연봉 그대로.
  · 승강 외의 사유(평판·전력·경제지수 변화 등)로는 계약 중 연봉을 바꾸지 않는다.

[기록] 재임 기록 표 3곳(team_managers / national_team_managers / oly_team_managers)의
salary_log에 contract_log와 같은 순서로 "서명연도:연봉(천원)"을 ';'로 이어 붙인다.
클럽은 "서명연도:연봉:리그id"(승강 조정 판정용 — 계약을 맺은 리그).
contract_log 항목이 비어 있으면(기록 이전의 예전 임기) salary_log 항목도 비워 둔다.
reconcile()이 "계약 기록은 있는데 연봉 기록이 없는" 현직 임기만 찾아 채운다 — 새 게임 생성 직후와
매주 끝에 부른다. 기록·표시 전용이라 감독 시장·경질·경기 결과는 이 값을 읽지 않는다.
"""
import random
import statistics
import zlib

# ─────────── 기준값(억 원, 2026년): 국가 → {부: (최저, 평균, 상위)} ───────────
def _t(*rows):
    return {tier: (lo, av, hi) for tier, lo, av, hi in rows}


LEAGUE_SALARY_DOC = {
    "대한민국": _t((1, 1.0, 1.7, 2.5), (2, .4, .7, 1.0), (3, .15, .3, .45), (4, .05, .12, .20), (5, .02, .05, .10)),
    "일본": _t((1, 1.5, 2.5, 3.5), (2, .5, 1.0, 1.5), (3, .15, .3, .5), (4, .05, .12, .2), (5, .02, .05, .1)),
    "태국": _t((1, .5, 1.2, 2.5), (2, .15, .4, .8), (3, .05, .12, .3)),
    "베트남": _t((1, .2, .6, 1.2), (2, .07, .2, .4), (3, .02, .07, .15)),
    "말레이시아": _t((1, .25, .7, 1.5), (2, .08, .2, .45), (3, .02, .07, .15)),
    "인도네시아": _t((1, .25, .7, 1.5), (2, .08, .25, .5), (3, .02, .08, .18)),
    "인도": _t((1, .3, .8, 1.8), (2, .08, .25, .6), (3, .02, .08, .2)),
    "말라위": _t((1, .08, .18, .30), (2, .02, .06, .10), (3, .005, .02, .04)),
    **{k: _t((1, .05, .15, .35), (2, .015, .05, .10), (3, .003, .015, .04)) for k in ("라오스", "네팔", "캄보디아")},
    "잉글랜드": _t((1, 30, 80, 200), (2, 5, 15, 35), (3, 1.5, 4, 8), (4, .6, 1.5, 3), (5, .2, .6, 1.2),
                   (6, .08, .2, .4), (7, .03, .08, .18), (8, .01, .03, .08)),
    "스페인": _t((1, 15, 40, 100), (2, 2, 6, 15), (3, .5, 1.5, 4), (4, .15, .4, 1), (5, .05, .15, .35),
                 (6, .02, .06, .15), (7, .005, .02, .06)),
    "독일": _t((1, 15, 40, 100), (2, 2, 6, 15), (3, .7, 2, 5), (4, .2, .6, 1.5), (5, .06, .18, .45),
               (6, .02, .07, .18), (7, .005, .025, .07)),
    **{k: _t((1, 12, 35, 90), (2, 1.5, 5, 12), (3, .4, 1.2, 3), (4, .12, .35, .9), (5, .04, .12, .3),
             (6, .015, .05, .12), (7, .005, .02, .05)) for k in ("프랑스", "이탈리아")},
    "포르투갈": _t((1, 2, 6, 15), (2, .5, 1.5, 4), (3, .15, .4, 1), (4, .05, .15, .35), (5, .015, .05, .12)),
    "네덜란드": _t((1, 4, 12, 30), (2, .8, 2.5, 6), (3, .2, .6, 1.5), (4, .06, .18, .45), (5, .02, .06, .15)),
    "벨기에": _t((1, 3, 8, 20), (2, .6, 1.8, 4), (3, .15, .45, 1.1)),
    "스코틀랜드": _t((1, 3, 8, 20), (2, .5, 1.5, 3.5), (3, .12, .35, .8), (4, .04, .12, .3)),
    "브라질": _t((1, 8, 25, 70), (2, 1.5, 5, 15), (3, .5, 1.5, 4), (4, .15, .45, 1.2), (5, .05, .15, .4),
                 (6, .015, .05, .15)),
    "아르헨티나": _t((1, 4, 12, 30), (2, .8, 2.5, 6), (3, .25, .7, 1.8), (4, .08, .22, .55), (5, .025, .07, .18)),
    "멕시코": _t((1, 5, 15, 40), (2, .8, 2.5, 7), (3, .15, .5, 1.5)),
    "미국": _t((1, 5, 15, 40), (2, .8, 2.5, 7), (3, .2, .7, 2)),
    **{k: _t((1, 1.5, 4, 10), (2, .3, .9, 2.5), (3, .08, .25, .6)) for k in ("콜롬비아", "칠레")},
    "우루과이": _t((1, 1, 3, 7), (2, .25, .7, 1.8), (3, .06, .18, .45)),
    "파라과이": _t((1, .8, 2.5, 6), (2, .2, .6, 1.5), (3, .05, .15, .4)),
    "코스타리카": _t((1, .6, 1.8, 4), (2, .15, .45, 1.1), (3, .04, .12, .3)),
    "자메이카": _t((1, .3, .9, 2), (2, .08, .25, .6), (3, .02, .07, .18)),
    **{k: _t((1, .8, 2.5, 6), (2, .2, .6, 1.5), (3, .05, .15, .4)) for k in ("이집트", "모로코")},
    "남아프리카공화국": _t((1, 1, 3, 8), (2, .25, .8, 2), (3, .06, .2, .5)),
    "알제리": _t((1, .6, 1.8, 4), (2, .15, .45, 1.1), (3, .04, .12, .3)),
    "튀니지": _t((1, .5, 1.5, 3.5), (2, .12, .35, .9), (3, .03, .1, .25)),
    "세네갈": _t((1, .3, .9, 2.2), (2, .08, .25, .6), (3, .02, .07, .18)),
    "나이지리아": _t((1, .3, .9, 2.2), (2, .07, .22, .55), (3, .02, .07, .18)),
    **{k: _t((1, .25, .8, 2), (2, .06, .2, .5), (3, .015, .06, .15)) for k in ("가나", "코트디부아르")},
    "케냐": _t((1, .2, .6, 1.5), (2, .05, .15, .4), (3, .01, .04, .12)),
    "잠비아": _t((1, .15, .5, 1.2), (2, .04, .12, .3), (3, .01, .035, .10)),
    "호주": _t((1, 2, 6, 15), (2, .4, 1.2, 3), (3, .1, .3, .8)),
    "뉴질랜드": _t((1, .3, .9, 2), (2, .08, .25, .6), (3, .02, .07, .18)),
    "사우디아라비아": _t((1, 5, 15, 40), (2, .8, 2.5, 7), (3, .2, .6, 1.5)),
    "이란": _t((1, .5, 1.5, 4), (2, .15, .45, 1.2), (3, .04, .12, .3)),
    **{k: _t((1, .4, 1.2, 3), (2, .1, .3, .8), (3, .025, .08, .2)) for k in ("이라크", "우즈베키스탄", "싱가포르")},
    **{k: _t((1, 4, 12, 30), (2, .8, 2.5, 6), (3, .2, .6, 1.5)) for k in ("튀르키예", "스위스")},
    "러시아": _t((1, 5, 15, 40), (2, .8, 2.5, 7), (3, .2, .6, 1.5)),
    **{k: _t((1, 2, 6, 15), (2, .4, 1.2, 3), (3, .1, .3, .8)) for k in ("그리스", "덴마크", "오스트리아")},
    **{k: _t((1, 1.5, 4, 10), (2, .3, .9, 2.5), (3, .08, .25, .6)) for k in ("스웨덴", "폴란드", "체코")},
    "노르웨이": _t((1, 2, 5, 12), (2, .4, 1.2, 3), (3, .1, .3, .8)),
    **{k: _t((1, 1, 3, 7), (2, .2, .6, 1.5), (3, .05, .15, .4)) for k in ("크로아티아", "세르비아")},
    "루마니아": _t((1, 1.2, 3.5, 8), (2, .25, .7, 1.8), (3, .06, .18, .45)),
    # 중국·카타르·UAE·홍콩은 문서 값이 선수 연봉 목표였으므로 자동 산정(여기 넣지 않음).
}
NATIONAL_SALARY = {"S": (15, 25, 40), "A": (8, 15, 25), "B": (3, 6, 10), "C": (1.5, 3, 6),
                   "D": (0.6, 1.5, 3), "E": (0.2, 0.6, 1.2), "F": (0.05, 0.15, 0.4)}
AUTO_RATIO = 1.0
AUTO_LO, AUTO_HI = 0.38, 2.5
AUTO_FLOOR = {1: 0.05, 2: 0.02}
AUTO_FLOOR_OTHER = 0.01
W_STATURE, W_REP, W_CAREER, JITTER = 0.25, 0.15, 0.10, 0.05
REF_STAT = "median"          # 평판·경력 비교 기준: "median"(2026-10 확정) / "mean"(예전 — 비교·검증용)
REP_SCALE = 20.0
CAREER_SCALE = 0.5                                           # 경력 비율 차이 0.5 = ±0.10
STATURE_SHARE = (0.10, 0.15, 0.15, 0.20)                    # 명문, 상위, 중상위, 중위(위아래 대칭)
STATURE_VALUES = (0.25, 0.15, 0.05, 0.0, -0.05, -0.15, -0.25)
STATURE_NAMES = ("명문", "상위권", "중상위권", "중위권", "중하위권", "하위권", "최하위권")
U23_LO, U23_SPAN = 0.40, 0.20
_EOK = 100_000                                               # 1억 = 100,000천원

_BASE_CACHE = {}       # (league_id, 기준 연도) → (최저, 평균, 상위, 출처)
_QUOTA_CACHE = {}


def clear_cache():
    """새 게임·다른 세이브 로드 때 — league_id가 새 판에서 재사용되므로 반드시 비운다."""
    _BASE_CACHE.clear()


# ─────────── 순수 도우미 ───────────
def tier_quotas(n):
    """팀 수 n → 7등급 인원(명문 … 최하위권). 위아래 대칭, 가운데로 갈수록 같거나 많고,
    목표 비율에 가장 가까운 조합. 6팀 미만은 1위 명문·꼴찌 최하위권·나머지 중위권."""
    if n in _QUOTA_CACHE:
        return _QUOTA_CACHE[n]
    if n < 6:
        out = (0, 0, 0, n, 0, 0, 0) if n < 2 else (1, 0, 0, n - 2, 0, 0, 1)
    else:
        best = None
        for k1 in range(1, n // 2 + 1):
            for k2 in range(k1, n // 2 + 1):
                for k3 in range(k2, n // 2 + 1):
                    mid = n - 2 * (k1 + k2 + k3)
                    if mid < 0:
                        break
                    if n >= 7 and mid < k3:
                        continue
                    err = ((k1 - STATURE_SHARE[0] * n) ** 2 + (k2 - STATURE_SHARE[1] * n) ** 2
                           + (k3 - STATURE_SHARE[2] * n) ** 2 + (mid - STATURE_SHARE[3] * n) ** 2 / 2)
                    key = (round(err, 9), -mid, k1)
                    if best is None or key < best[0]:
                        best = (key, (k1, k2, k3, mid, k3, k2, k1))
        out = best[1]
    _QUOTA_CACHE[n] = out
    return out


def _interp(lo, av, hi, s):
    s = max(0.0, min(1.0, s))
    if s < 0.5:
        return av * (lo / av) ** (1 - 2 * s)
    return av * (hi / av) ** (2 * s - 1)


def _norm(x):
    return max(-1.0, min(1.0, x))


def _ref(values, default):
    """평판·경력 비교 기준값 — REF_STAT에 따라 중앙값(기본) 또는 평균. 값이 없으면 default."""
    if not values:
        return default
    if REF_STAT == "mean":
        return sum(values) / len(values)
    return statistics.median(values)


def _jitter(manager_id, key, year):
    r = random.Random(zlib.crc32(f"mgr_salary:{manager_id}:{key}:{year}".encode("utf-8")))
    return (r.random() * 2 - 1) * JITTER


def _career_ratio(career, job_level):
    if not job_level:
        return None
    cb = job_level if career is None else career
    return cb / job_level


def _career_term(career, job_level, ref_ratio):
    """같은 리그(대표팀은 같은 등급) 현직 감독 경력 비율의 기준값(REF_STAT — 기본 중앙값)과 비교 —
    위아래 대칭, −1~+1."""
    r = _career_ratio(career, job_level)
    if r is None:
        return 0.0
    return _norm((r - (ref_ratio if ref_ratio else 1.0)) / CAREER_SCALE)


# ─────────── 기준값·리그 맥락 ───────────
def _league_roster(c, league_id, memo):
    """[2026-10 성능, 결과 동일] 리그 선수단을 (팀 id, 팀 이름, OVR, 인원)으로 한 번만 읽어 memo에 둔다 —
    자동 산정 기준값(league_base)과 구단 위상(_league_ctx의 상위 11명 평균 OVR)이 같이 쓴다.
    예전엔 둘이 각자 리그 선수 전원을 따로 조회했다(연도 전환 주에 리그 약 490개 × 2번)."""
    k = ("R", league_id)
    r = memo.get(k)
    if r is None:
        r = memo[k] = c.execute(
            """SELECT a.team_id, t.name, a.ovr, COUNT(*) FROM ai_players a JOIN teams t ON t.id = a.team_id
               WHERE t.league_id = ? GROUP BY a.team_id, a.ovr""", (league_id,)).fetchall()
    return r


def league_base(c, league_id, tier, country, base_year, memo=None):
    """(최저, 평균, 상위, 출처) 억 원 2026년 기준. 자동 산정은 (리그, 기준 연도)마다 한 번 계산.

    [2026-10 성능, 결과 동일 — 신민용 확정 "Version 2"] 자동 산정은 리그 선수 전원의 선수 연봉
    (economy._calc_salary) 중앙값이다. 예전엔 선수마다 한 번씩 계산했는데(연도 전환 주에 약 16만 번),
    _calc_salary가 팀 이름을 쓰는 곳은 명문 연봉 배율(prestige_salary_mult) 하나뿐이라 같은 리그에서
    (OVR, 그 배율 값)이 같으면 결과도 같다. 그래서 (OVR, 배율)마다 한 번만 계산하고 인원수로 중앙값
    (정렬했을 때 len//2번째 값 — 예전과 같은 자리)을 찾는다. 선수 연봉 함수 자체는 건드리지 않는다."""
    tbl = LEAGUE_SALARY_DOC.get(country)
    if tbl and tier in tbl:
        lo, av, hi = tbl[tier]
        return lo, av, hi, "문서"
    k = (league_id, base_year)
    if k in _BASE_CACHE:
        return _BASE_CACHE[k]
    from economy import _calc_salary
    from constants import get_country_league_grade
    from data.prestige_clubs import prestige_salary_mult
    w = get_country_league_grade(country)
    memo = {} if memo is None else memo
    mult_of, val_of, count_of = {}, {}, {}
    for _tid, tname, ovr, n in _league_roster(c, league_id, memo):
        m = mult_of.get(tname)
        if m is None:
            m = mult_of[tname] = prestige_salary_mult(country, tname) if (tname and country) else None
        key = (ovr, m)
        v = val_of.get(key)
        if v is None:
            v = val_of[key] = _calc_salary(w, tier, ovr, country=country, team_name=tname, year=2026) / _EOK
        count_of[v] = count_of.get(v, 0) + n
    p50 = 0.0
    total = sum(count_of.values())
    if total:
        idx, acc = total // 2, 0
        for v in sorted(count_of):
            acc += count_of[v]
            if acc > idx:
                p50 = v
                break
    floor = AUTO_FLOOR.get(tier, AUTO_FLOOR_OTHER)
    av = max(floor, AUTO_RATIO * p50)
    out = (av * AUTO_LO, av, av * AUTO_HI, "하한" if av == floor else "자동")
    _BASE_CACHE[k] = out
    return out


def _league_ctx(c, league_id, memo):
    k = ("L", league_id)
    if k in memo:
        return memo[k]
    teams = [r[0] for r in c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,))]
    # 팀별 상위 11명 평균 OVR — 예전엔 리그 선수 전원을 창 함수로 따로 읽었다. 이제 기준값과 같은
    # 선수단 묶음(_league_roster)으로 계산한다: OVR 높은 순으로 11명(OVR 없는 선수는 맨 뒤 — SQLite의
    # 내림차순 NULL 위치와 같다)을 세고, 평균은 OVR 있는 선수만(SQL AVG와 같은 정수 합 ÷ 인원).
    by_team = {}
    for tid, _tname, o, n in _league_roster(c, league_id, memo):
        by_team.setdefault(tid, []).append((o, n))
    ovr = {}
    for tid, lst in by_team.items():
        lst.sort(key=lambda x: (x[0] is None, -(x[0] or 0)))
        taken = tot = cnt = 0
        for o, n in lst:
            take = min(n, 11 - taken)
            if take <= 0:
                break
            taken += take
            if o is not None:
                tot += o * take
                cnt += take
        ovr[tid] = (tot / cnt) if cnt else 0.0
    order = sorted(teams, key=lambda t: (-ovr.get(t, 0.0), t))
    stature, i = {}, 0
    for cnt, (val, name) in zip(tier_quotas(len(order)), zip(STATURE_VALUES, STATURE_NAMES)):
        for t in order[i:i + cnt]:
            stature[t] = (val, name)
        i += cnt
    reps, cars = [], []
    for rep, cb, jl in c.execute(
            """SELECT m.reputation, m.career_best_level, tm.job_level FROM team_managers tm
               JOIN managers m ON m.id = tm.manager_id JOIN teams t ON t.id = tm.team_id
               WHERE t.league_id = ? AND tm.end_year IS NULL AND tm.job_kind = 'club'""", (league_id,)):
        reps.append(rep or 0.0)
        r = _career_ratio(cb, jl)
        if r is not None:
            cars.append(r)
    memo[k] = {"stature": stature, "rep_ref": _ref(reps, 0.0), "car_ref": _ref(cars, 1.0)}
    return memo[k]


def _nation_ctx(c, memo):
    if "N" in memo:
        return memo["N"]
    rows = c.execute("SELECT id, grade, fifa_rank FROM countries").fetchall()
    by = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    pos = {}
    for g, lst in by.items():
        lst.sort(key=lambda r: (r[2] if r[2] is not None else 9999, r[0]))
        n = len(lst)
        for i, r in enumerate(lst):
            pos[r[0]] = (1 - 2 * i / (n - 1)) if n > 1 else 0.0
    from constants import national_job_level
    reps, cars = {}, {}
    for g, rep, cb in c.execute(
            """SELECT cn.grade, m.reputation, m.career_best_level FROM national_team_managers n
               JOIN managers m ON m.id = n.manager_id JOIN countries cn ON cn.id = n.country_id
               WHERE n.end_year IS NULL"""):
        reps.setdefault(g, []).append(rep or 0.0)
        # 대표팀 경력 위치는 그 등급 성인 대표팀 자리 수준으로 잰다(등급이 바뀌어도 같은 눈금).
        r = _career_ratio(cb, national_job_level(g))
        if r is not None:
            cars.setdefault(g, []).append(r)
    memo["N"] = {"grade": {r[0]: r[1] for r in rows}, "pos": pos,
                 "rep_ref": {g: _ref(v, 0.0) for g, v in reps.items()},
                 "car_ref": {g: _ref(v, 1.0) for g, v in cars.items()}}
    return memo["N"]


def _mgr(c, manager_id):
    r = c.execute("SELECT reputation, career_best_level FROM managers WHERE id=?", (manager_id,)).fetchone()
    return (r[0] or 0.0, r[1]) if r else (0.0, None)


def _economy(year):
    from economy import economy_index
    return economy_index(year)


# ─────────── 연봉 계산(천원) ───────────
def club_salary(c, team_id, manager_id, sign_year, job_level, memo=None, base_year=None, detail=False,
                jitter_year=None):
    """jitter_year: 고정 변동을 정하는 연도. 기본(None)은 sign_year — 부임·재계약은 그대로.
    승강 조정(_promotion_adjust)만 계약 칸의 원래 서명 연도를 넘겨 계약 때 고정 변동을 유지한다."""
    memo = {} if memo is None else memo
    t = c.execute("""SELECT l.id, l.tier, cn.name FROM teams t JOIN leagues l ON l.id = t.league_id
                     JOIN countries cn ON cn.id = l.country_id WHERE t.id = ?""", (team_id,)).fetchone()
    if not t:
        return (0, {}) if detail else 0
    lid, tier, country = t
    lo, av, hi, src = league_base(c, lid, tier, country, base_year if base_year is not None else sign_year,
                                  memo=memo)
    ctx = _league_ctx(c, lid, memo)
    rep, career = _mgr(c, manager_id)
    st_val, st_name = ctx["stature"].get(team_id, (0.0, "중위권"))
    st = W_STATURE * (st_val / 0.25)
    rp = W_REP * _norm((rep - ctx["rep_ref"]) / REP_SCALE)
    cr = W_CAREER * _career_term(career, job_level, ctx["car_ref"])
    s = 0.5 + st + rp + cr + _jitter(manager_id, f"club{team_id}",
                                     sign_year if jitter_year is None else jitter_year)
    amt = max(1, int(round(_interp(lo, av, hi, s) * _EOK * _economy(sign_year))))
    if detail:
        return amt, {"s": s, "lo": lo, "av": av, "hi": hi, "src": src, "stature": st_name,
                     "st": st, "rp": rp, "cr": cr, "league_id": lid, "tier": tier}
    return amt


def national_salary(c, country_id, manager_id, sign_year, job_level, u23=False, memo=None, detail=False):
    memo = {} if memo is None else memo
    ctx = _nation_ctx(c, memo)
    g = ctx["grade"].get(country_id, "F")
    lo, av, hi = NATIONAL_SALARY.get(g, NATIONAL_SALARY["F"])
    rep, career = _mgr(c, manager_id)
    r_n = _norm((rep - ctx["rep_ref"].get(g, 0.0)) / REP_SCALE)
    # 성인·U-23 모두 경력 위치를 그 등급 성인 대표팀 자리 수준으로 잰다 — 같은 평판·같은 경력이면
    # 두 직책이 같은 위치이고, 차이는 U-23 배율(0.40~0.60)뿐이다. 넘겨받은 job_level은 쓰지 않는다.
    from constants import national_job_level
    k_n = _career_term(career, national_job_level(g), ctx["car_ref"].get(g, 1.0))
    # U-23도 성인 대표팀과 같은 위치·같은 고정 변동을 쓴다 → 두 직책 차이는 배율뿐.
    s = (0.5 + W_STATURE * ctx["pos"].get(country_id, 0.0) + W_REP * r_n + W_CAREER * k_n
         + _jitter(manager_id, f"nat{country_id}", sign_year))
    senior = _interp(lo, av, hi, s) * _EOK * _economy(sign_year)
    mult = 1.0
    s_rc = None
    if u23:
        s_rc = max(0.0, min(1.0, 0.5 + 0.5 * (0.6 * r_n + 0.4 * k_n)))
        mult = U23_LO + U23_SPAN * s_rc
    amt = max(1, int(round(senior * mult)))
    if detail:
        return amt, {"s": s, "grade": g, "senior": senior, "mult": mult, "s_rc": s_rc}
    return amt


# ─────────── 기록 맞추기 ───────────
_TABLES = (
    # (표, 자리 컬럼, 종류, 추가 조건)
    ("team_managers", "team_id", "club", "AND COALESCE(job_kind,'club') = 'club'"),
    ("national_team_managers", "country_id", "nation", ""),
    ("oly_team_managers", "country_id", "u23", ""),
)


def _semis(col):
    return f"(length(COALESCE({col},'')) - length(replace(COALESCE({col},''), ';', '')))"


def reconcile(conn=None, base_year=None):
    """계약 기록(contract_log)은 있는데 그 항목의 연봉 기록(salary_log)이 없는 현직 임기를 채운다.
    반환: 새로 적은 연봉 항목 수. 실패해도 예외를 밖으로 내지 않는다(기록 전용)."""
    own = conn is None
    if own:
        from database import get_conn
        conn = get_conn()
    written = 0
    try:
        c = conn.cursor()
        if base_year is None:
            r = c.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()
            base_year = r[0] if r else None
        memo = {}
        updates = {}
        for tbl, place_col, kind, extra in _TABLES:
            try:
                rows = c.execute(
                    f"""SELECT id, manager_id, {place_col}, job_level, contract_log, COALESCE(salary_log,'')
                        FROM {tbl}
                        WHERE end_year IS NULL {extra} AND COALESCE(contract_log,'') <> ''
                          AND (COALESCE(salary_log,'') = '' OR {_semis('contract_log')} <> {_semis('salary_log')})
                        ORDER BY id""").fetchall()
            except Exception:
                continue          # 표/컬럼이 없는 세이브(올림픽 꺼짐 등)
            ups = []
            for tid_, mid, place, jl, clog, slog in rows:
                parts = clog.split(";")
                sal = slog.split(";") if slog else []
                sal += [""] * (len(parts) - len(sal))
                sal = sal[:len(parts)]
                changed = False
                for i, p in enumerate(parts):
                    if not p or sal[i]:
                        continue
                    try:
                        sy = int(p.split(":")[0])
                    except ValueError:
                        continue
                    if kind == "club":
                        amt, d = club_salary(c, place, mid, sy, jl, memo=memo,
                                             base_year=base_year if base_year is not None else sy, detail=True)
                        entry = f"{sy}:{amt}:{d.get('league_id', '')}"
                    else:
                        amt = national_salary(c, place, mid, sy, jl, u23=(kind == "u23"), memo=memo)
                        entry = f"{sy}:{amt}"
                    if amt > 0:
                        sal[i] = entry
                        changed = True
                        written += 1
                if changed or len(sal) != len((slog or "").split(";")):
                    ups.append((";".join(sal), tid_))
            if ups:
                updates[tbl] = ups
        for tbl, ups in updates.items():
            c.executemany(f"UPDATE {tbl} SET salary_log=? WHERE id=?", ups)
        adj = _promotion_adjust(c, base_year, memo)
        written += adj
        if updates or adj:
            conn.commit()
    except Exception as e:
        print(f"[감독 연봉] 기록 맞추기 실패(건너뜀): {e}")
    finally:
        if own:
            conn.close()
    return written


def _promotion_adjust(c, cur_year, memo):
    """[명시적 예외 — 승강 조정 조항] 클럽 리그가 바뀐 현직 임기의 연봉을 새 리그 기준으로 다시 정한다.
    조정 대상: 지금 계약 칸의 마지막 금액이 계약한 리그와 지금 리그가 다르고, 계약이 다음 시즌까지 남은 임기.
    계약 만료(contract_log)는 그대로, 금액은 cur_year 경제지수, 적용은 cur_year+1부터."""
    if cur_year is None:
        return 0
    rows = c.execute(
        """SELECT tm.id, tm.manager_id, tm.team_id, tm.job_level, tm.contract_log, tm.salary_log,
                  t.league_id, l.tier
           FROM team_managers tm JOIN teams t ON t.id = tm.team_id JOIN leagues l ON l.id = t.league_id
           WHERE tm.end_year IS NULL AND COALESCE(tm.job_kind,'club') = 'club'
             AND COALESCE(tm.salary_log,'') <> '' ORDER BY tm.id""").fetchall()
    tier_of = {}
    ups = []
    for tmid, mid, team_id, jl, clog, slog, cur_lid, cur_tier in rows:
        cparts = (clog or "").split(";")
        sslots = slog.split(";")
        idx = max((i for i, x in enumerate(sslots) if x), default=None)
        if idx is None or idx >= len(cparts) or not cparts[idx]:
            continue
        segs = sslots[idx].split("|")
        last = segs[-1].split(":")
        if len(last) < 3 or not last[2]:
            continue                      # 계약 리그를 모르는 기록(예전 형식) — 조정하지 않는다
        try:
            signed_lid = int(last[2])
            until = int(cparts[idx].split(":")[1])
        except (ValueError, IndexError):
            continue
        try:
            signed_year = int(cparts[idx].split(":")[0])
        except ValueError:
            signed_year = None            # 서명 연도를 못 읽는 기록이면 예전처럼 cur_year로(조정 자체는 그대로)
        if signed_lid == cur_lid or until < cur_year + 1:
            continue
        if signed_lid not in tier_of:
            r = c.execute("SELECT tier FROM leagues WHERE id=?", (signed_lid,)).fetchone()
            tier_of[signed_lid] = r[0] if r else cur_tier
        old_tier = tier_of[signed_lid]
        flag = "P" if cur_tier < old_tier else ("R" if cur_tier > old_tier else "L")
        # [2026-10 고정 변동 유지, 신민용 승인] 고정 변동은 계약 칸의 원래 서명 연도로 정한다 —
        # 예전엔 cur_year로 다시 뽑혀 계약 중에 바뀌었다(10시즌 기록상 조정 칸의 87%). 금액의 경제지수·
        # 위상·평판·경력은 예전처럼 cur_year 기준.
        amt = club_salary(c, team_id, mid, cur_year, jl, memo=memo, base_year=cur_year,
                          jitter_year=signed_year)
        if amt <= 0:
            continue
        segs.append(f"{cur_year}:{amt}:{cur_lid}:{flag}")
        sslots[idx] = "|".join(segs)
        ups.append((";".join(sslots), tmid))
    if ups:
        c.executemany("UPDATE team_managers SET salary_log=? WHERE id=?", ups)
    return len(ups)


# ─────────── 화면용 ───────────
_ADJ_NOTE = {"P": "승격 조정", "R": "강등 조정", "L": "리그 이동 조정"}


def contract_salary_by_year(contract_log, salary_log, first_year):
    """연도 → (연봉 천원 또는 None, 계약 N년, 시작 연도, 만료 연도, 비고, 금액 계산 연도).
    계약 기록이 없는 해는 빠진다. 부임 계약은 실제 첫 지휘 연도부터, 재계약은 서명 다음 해부터
    적용한다(_contract_notes와 같은 규칙). 승강 조정 금액은 조정한 해의 다음 해부터(비고 "승격 조정" 등)."""
    parts = (contract_log or "").split(";")
    sal = (salary_log or "").split(";") if salary_log else []
    out = {}
    spans = []
    for i, p in enumerate(parts):
        if not p:
            continue
        try:
            s_, u_ = (int(x) for x in p.split(":")[:2])
        except ValueError:
            continue
        start = int(first_year) if i == 0 else s_ + 1
        segs = []                                   # (적용 시작 연도, 금액, 비고, 계산 연도)
        if i < len(sal) and sal[i]:
            for k, seg in enumerate(sal[i].split("|")):
                f = seg.split(":")
                try:
                    cy, amt = int(f[0]), int(f[1])
                except (ValueError, IndexError):
                    continue
                if k == 0:
                    segs.append((start, amt, "", cy))
                else:
                    segs.append((max(start, cy + 1), amt, _ADJ_NOTE.get(f[3] if len(f) > 3 else "", "조정"), cy))
        spans.append((start, u_, segs))
    for j, (start, until, segs) in enumerate(spans):
        stop = until
        if j + 1 < len(spans):
            stop = max(start, spans[j + 1][0] - 1)
        for y in range(start, max(start, stop) + 1):
            cur = None
            for seg in segs:
                if seg[0] <= y:
                    cur = seg
            if cur:
                out[y] = (cur[1], max(1, until - start + 1), start, until, cur[2], cur[3])
            else:
                out[y] = (None, max(1, until - start + 1), start, until, "", None)
    return out