# -*- coding: utf-8 -*-
"""
[2026-08 신설, 신민용 설계 확정: "챔스/유로파급/컨퍼런스급 3단계 대륙대항전"]

한 대륙 안에서 국가별로 "챔스 슬롯 → 유로파 슬롯 → 컨퍼런스 슬롯" 순서로
슬롯을 배분하고, 각 나라 리그 순위표에서 위에서부터 그만큼씩 떼어가며
아래로 승계한다(워터폴) — 같은 팀이 두 대회에 동시에 뽑히는 일이
구조적으로 불가능하다.

이 모듈은 champions_engine.py(챔스 대회 진행 자체)와 완전히 분리된
"누가 어느 대회에 나가는지"만 결정하는 순수 로직이다 — 실제 대회
엔진(champions_engine.py / europa_engine.py / conference_engine.py,
아직 미구현)은 이 모듈이 반환한 참가팀 리스트를 그대로 받아서 조편성부터
시작하면 된다.

[국가 순위 정렬 기준] champions_engine._country_coefficients()(최근 5시즌
실측 UEFA 계수식 성적 합산)를 그대로 재사용한다 — 데이터가 아직 부족한
게임 초반(그 대륙에서 실측 시즌이 CL_COEFF_MIN_COUNTRIES개국 미만)엔
클럽 리그 등급(grade) 순으로 폴백한다. 챔스/유로파/컨퍼런스가 전부 같은
국가 순위를 공유해야 "챔스에서 밀려난 다음 순위 팀이 유로파로 내려간다"는
워터폴 의미가 성립하므로, 대회마다 다른 정렬 기준을 쓰지 않는다.

[슬롯표 확정 현황]
  유럽: 신민용 확정(2026-08) — 1~9위 2장, 10~27위 1장(유로파, 36장/27개국)
                              1~10위 1장, 11~23위 2장(컨퍼런스, 36장/23개국)
  아시아/아프리카/북남미: 아직 구체적 수치 미확정 — 우선 유럽과 동일한
    "국가 수 대비 비율"로 임시 배정해뒀다(TENTATIVE로 표시). 확정되면
    EUROPA_SLOT_TABLE_BY_CONTINENT / CONFERENCE_SLOT_TABLE_BY_CONTINENT만
    바꾸면 된다 — 아래 배분 로직 자체는 대륙 독립적이라 손댈 필요 없음.
"""
from database import get_conn
from constants import get_country_league_grade

from competition import champions_engine as _cl


# ── 유로파/컨퍼런스 슬롯표 ──────────────────────────────────────
# (상한 미만 rank_idx는 0-based. 예: rank_idx=0 → 1위)
# 반환값은 "그 나라가 받는 슬롯 수". 전 대륙 합계가 정원(36/48)을 넘을 수
# 있으므로, 실제 배분 시엔 등급 높은 나라부터 순서대로 채우다 정원에서
# 끊는다(기존 챔스 _select_entries와 동일한 원칙).

def _europa_slots_from_rank(continent: str, rank_idx: int) -> int:
    """[확정: 유럽] 1~9위 2장 / 10~27위 1장 / 28위~ 0장 (36장/27개국 목표)."""
    if continent == "유럽":
        if rank_idx < 9:
            return 2
        if rank_idx < 27:
            return 1
        return 0
    # [2026-09 버그수정, 신민용 리포트: "유로파/챔스 참가팀이 왔다갔다한다"]
    # 남미(12개국)만 실제로 이 비율 스케일링 공식으로는 정원(32)에 구조적으로
    # 못 미쳤다 — "상위 1/3은 2장, 나머지는 1장"짜리 2단 커브의 최댓값은
    # two_cut(≤n) + one_cut(≤n)인데, n=12면 아무리 후하게 잡아도
    # 4+12=16이 한계라 32장을 절대 못 채운다(북미/아시아/아프리카는
    # 국가 수가 많아 이 커브의 최댓값이 정원을 훌쩍 넘어서 캡으로 정확히
    # 잘렸을 뿐 — 실측: 북미 44산출→32캡 정상, 남미는 16산출로 캡 자체가
    # 무의미했음). 그래서 남미는 champions_engine._slots_from_rank의
    # 남미 커브처럼 국가당 슬롯 상한을 2가 아니라 4까지 열어 별도로
    # 정의한다 — 여유(정원 32 대비 35)를 둬서 한두 나라 리그 데이터가
    # 아직 얇아도(시즌 극초반) 정원 미달이 안 나게 한다.
    if continent == "남미":
        if rank_idx < 3:
            return 4   # 1~3위
        if rank_idx < 8:
            return 3   # 4~8위
        return 2       # 9~12위 (12개국×평균 2.9장 ≈ 35장, 32장 캡보다 여유)
    # [TENTATIVE] 아시아/아프리카/북미 — 확정 수치 받기 전까지 유럽과
    # 동일한 비율(상위 1/3 국가는 2장, 나머지는 1장 — 유럽 확정 수치가
    # "27개국 중 1~9위 2장/10~27위 1장"이므로 그 비율은 9/27, 27/27)로
    # 그 대륙 실제 국가 수에 맞춰 스케일링.
    # [2026-08 버그수정, 신민용 리포트: "유로파/컨퍼런스 북남미가 30팀
    # 밖에 안 됨, 36으로 다른 대륙급이랑 맞춰야"] 분모를 27(유럽 확정
    # 국가 수)이 아니라 54로 잘못 써서 절반 비율로 스케일링되고 있었다
    # — 북남미(45개국)에서 목표 정원(36)보다 한참 적은 30장만 나온 원인.
    # 분모를 27로 고치면 슬롯 합계가 정원(36)을 넉넉히 넘게 배분되고,
    # allocate_continental_slots()가 정원에서 정확히 잘라주므로(캡 로직
    # 자체는 원래 정상) 결과적으로 정확히 36장이 채워진다. 이 스케일링은
    # 국가 수가 충분히 많은 대륙(아시아/아프리카/북미)에서만 정원을
    # 넘기므로 안전하다 — 남미처럼 국가 수가 적으면 위 전용 분기를 탄다.
    from competition.champions_engine import CONTINENT_MAP
    n_countries = _n_countries_in_continent(continent)
    two_cut = max(1, round(n_countries * 9 / 27))
    one_cut = max(two_cut + 1, round(n_countries * 27 / 27))
    if rank_idx < two_cut:
        return 2
    if rank_idx < one_cut:
        return 1
    return 0


def _conference_slots_from_rank(continent: str, rank_idx: int) -> int:
    """[확정: 유럽] 1~10위 1장 / 11~23위 2장 / 24위~ 0장 (36장/23개국 목표)."""
    if continent == "유럽":
        if rank_idx < 10:
            return 1
        if rank_idx < 23:
            return 2
        return 0
    # [2026-09 버그수정] _europa_slots_from_rank와 동일한 이유 — 남미
    # (12개국)는 비율 스케일링 공식으로 정원(32)에 구조적으로 못 미쳐서
    # 전용 커브를 쓴다(여유를 둬서 37장 산출 → 32캡으로 정확히 잘림).
    if continent == "남미":
        if rank_idx < 4:
            return 4   # 1~4위
        if rank_idx < 9:
            return 3   # 5~9위
        return 2       # 10~12위
    # [TENTATIVE] 아시아/아프리카/북미 — 위와 동일한 이유로 비율
    # 스케일링(유럽 확정 수치 "23개국 중 1~10위 1장/11~23위 2장"의
    # 비율 10/23, 23/23을 그대로 사용).
    # [2026-08 버그수정] europa와 동일한 원인(분모 54→23) 수정.
    n_countries = _n_countries_in_continent(continent)
    one_cut = max(1, round(n_countries * 10 / 23))
    two_cut = max(one_cut + 1, round(n_countries * 23 / 23))
    if rank_idx < one_cut:
        return 1
    if rank_idx < two_cut:
        return 2
    return 0


# [2026-08 확정, 신민용 요청] 유로파/컨퍼런스는 챔스와 달리 대륙 무관하게
# 전부 36장 — 북남미 챔스만 48로 확대된 건 챔스 전용 결정이라(위
# champions_engine.CL_TEAMS_BY_CONTINENT 참고) 여기엔 안 물려받는다.
# [2026-09 개편] "북남미" → 남미/북미 분리. 챔스 정원(32)에 맞춰 유로파/
# 컨퍼런스도 남미·북미는 32로 통일한다(다른 대륙은 그대로 36 유지).
QUALIFICATION_TEAM_CAP = {"유럽": 36, "남미": 32, "북미": 32, "아시아": 36, "아프리카": 36}

# [2026-09 3차 수정, 신민용 확정: "ECL은 참가팀이 리그 순위에 맞춰 뽑혀 나가는
# 36팀 대회다 — 아시아 모든 팀이 참가하는 대회가 아니다. 정원 36은 고정이고,
# 그 36자리를 어느 나라 몇 위 팀에게 주느냐만 배분 규칙이 정한다"]
# 직전 버전은 "3위~최하위 전원 ECL 1장"을 만족시키려고 정원 자체를 국가 수에
# 맞춰 52(유럽/아프리카)/56(아시아)까지 늘려버렸다 — 이건 대회 규격을 깬
# 잘못된 수정이었다. 정원은 다시 고정하고, 대신 아래 _conference_plan()이
# 우선순위 밴드로 36자리를 나눈다.
#
# 남미만 0 = 컨퍼런스 대회 자체를 열지 않는다(신민용 질문 "남미만 컨퍼런스를
# 없애거나 정원을 24로 줄이는 게 맞아 보이는데 어느 쪽이냐"에 대한 결정).
#   - 남미는 1부 보유국이 12개뿐이라 32×3 = 96장 → 국가당 평균 8장. 실측으로
#     콜롬비아/우루과이/에콰도르가 11장씩(1부 전체의 절반 이상) 나갔다.
#   - 정원을 24로 줄여도 88장/12개국 = 7.3장이라 이 문제가 거의 그대로다.
#   - 실제 CONMEBOL도 대회가 2개(리베르타도레스 32 + 수다메리카나 32)뿐이다.
#   - 컨퍼런스를 없애면 64장/12개국 = 5.3장(실제 6.4장과 근사)이 되고,
#     12개국 전원이 CL·EL에서 최소 2장씩 받으므로 "0장 국가 0개" 조건도
#     그대로 만족한다.
# 되돌리려면 이 표의 "남미" 값만 바꾸면 된다(conference_engine._ecl_team_cap이
# 이 함수를 그대로 쓰고, start_all_continental_competitions는 참가팀이 4팀
# 미만이면 대회를 안 만들므로 0이면 자동으로 대회가 안 열린다).
CONFERENCE_TEAM_CAP = {"유럽": 36, "남미": 0, "북미": 32, "아시아": 36, "아프리카": 36}

# ── [2026-09 전면 재설계] 밴드 기반 국가별 출전권 배분 ───────────────
# [신민용 리포트 + GPT/Gemini 교차검증] 기존 방식의 실측 문제:
#   - 유럽 챔스: 커브 합계 84장인데 정원 36 → 국가순위 14위에서 잘림.
#     15위 이하 나라는 챔스뿐 아니라 유로파/컨퍼런스도 같은 식으로
#     위에서부터 채워져 아무 대회도 못 나갔다.
#   - 1부 리그를 가진 나라의 40%가 세 대회 전부 0장.
#   - 아시아는 유로파 컷(18위)이 챔스 컷(19위)보다 얕아서, "챔스에서
#     밀리면 유로파로 내려간다"는 워터폴 전제 자체가 역전됐다.
#   - 남미는 국가가 12개뿐인데 정원이 32×3이라 브라질이 13팀을 보냈다.
# 신민용 확정 방향: "모든 국가에 무조건 1장"이 아니라 "1부 리그를 가진
# 국가가 국제대항전에 진입할 수 있는 최소 경로를 확보하고, 국가 수준에
# 따라 대회 등급을 제한한다" — 베트남이 아시아에서 1팀도 못 나갈 정도는
# 아니지만, 그렇다고 챔스에 4장을 받을 수는 없다.
#
# 새 구조는 "대회별 커브를 각자 그려놓고 정원에서 자르는" 방식을 버리고,
# 대륙 국가 수(n)와 대회별 정원에서 **컷 깊이를 역산**한다:
#   챔스   : 1위 ~ d_cl위        (상위권만)
#   유로파 : 1위 ~ d_el위        (d_cl < d_el — 워터폴 전제 보장)
#   컨퍼런스: s_ecl위 ~ n위      (s_ecl <= d_el+1 이므로 빈틈 없음,
#                                 s_ecl > d_cl 이므로 최상위국은 3부
#                                 대회에까지 나가지 않는다)
# 세 구간의 합집합이 1~n을 전부 덮으므로 0장 국가가 구조적으로 안 생기고,
# 각 대회 합계는 정원과 정확히 일치한다(아래 _distribute의 최대잔여법 +
# 상한 재분배). 실측 결과는 docs가 아니라 이 파일의 슬롯표 자체가 아니라
# tools 쪽 검증 스크립트로 언제든 다시 뽑을 수 있다.

# 컷 깊이(대륙 국가 수 대비 비율). 정원을 그라데이션으로 채우는 데 필요한
# 최소 국가 수와 비교해 더 깊은 쪽을 쓴다 — 북미(33개국/정원 32)처럼
# 국가가 적으면 비율만 보고 잡았을 때 상위 7개국이 전부 국가당 상한(4장)에
# 붙어 "강한 나라와 중위권 나라의 차이"가 사라진다.
# [2026-09 2차 수정, 신민용 확정: "카타르~베트남을 ECL 0으로 해버리면
# 카타르 리그 5위가 갈 국제대회가 없어져버린다 — ECL을 '약소국 전용 대회'로
# 생각하면 안 되고, '상위 2개국 정도를 제외한 대부분의 국가가 최소 1팀을
# 보내는 3번째 대륙대항전'으로 설계하는 게 맞다"]
#
# 1차 버전은 컨퍼런스 시작 순위를 정원에서 역산했다(s_ecl = n - 정원 + 1).
# 아시아는 58개국/정원 36이라 s_ecl=23 — 한국·카타르·호주·이란·중국·베트남이
# 전부 ECL 0이 되고, 그래서 "CL 4장 + EL 3장을 받은 나라의 8위 팀은 갈 곳이
# 없다"는 사다리 단절이 생겼다. 2차 버전은 그걸 고치려고 정원 자체를 국가
# 수에 맞춰 늘렸는데(52/56), 그건 "ECL은 36팀 대회"라는 규격을 깬 잘못된
# 수정이었다(위 CONFERENCE_TEAM_CAP 주석).
#
# 3차(현재): 정원 36 고정 + **우선순위 밴드**로 36자리를 나눈다
# (_conference_plan 참고). 컨퍼런스는 더 이상 "연속 구간"이 아니다:
#   ① 유일한 경로 밴드 — CL·EL이 0장인 국가(= EL 컷 아래 전부)에 1장씩.
#      이게 "0장 국가 0개"(조건 1)를 보장하는 필수 배정이라 최우선.
#   ② 사다리 보강 밴드 — CL+EL 합이 QUAL_ECL_LADDER_MIN 이상인 국가
#      (최상위 QUAL_ECL_TOP_EXEMPT개국 제외)에 1장씩. 카타르가 CL3+EL2를
#      받았으면 리그 6위 팀이 갈 대회를 만들어준다(조건 3).
#   ③ 남는 장수 — 중간 국가(CL+EL 1~2장)에 국가 순위 순으로 1장씩,
#      그래도 남으면 ②부터 2장째를 준다.
# ①+②가 정원을 넘으면 EL 컷(d_el)을 한 칸씩 늘려 ① 밴드를 줄인다 — 꼬리
# 국가 한 나라가 ECL 대신 EL로 올라가는 것이라 워터폴(조건 2)은 그대로다.
#
# 챔스 컷 깊이도 같이 조정했다(0.28 → 0.34). 1차 버전은 아시아 상위 6개국이
# 전부 국가당 상한(4장)에 붙어 일본과 이란이 같은 4장이 됐는데, 신민용 참고표
# (일본4 · 한국3 · 호주2~3 · 우즈벡2 · 베트남1)는 더 완만한 내림차순이다.
# 깊이를 늘리면 같은 정원(36)이 더 많은 나라에 퍼지므로 머리가 자연히 낮아진다.
QUAL_DEPTH_FRAC = {"champions": 0.34, "europa": 0.48}
# 컨퍼런스에서 제외되는 최상위 국가 수 — "일본·사우디처럼 압도적으로 강한
# 국가만 ECL에 참가하지 않는다"(신민용). 이 나라들은 리그 상위 7팀이 이미
# 챔스·유로파에 나가므로 그 밑을 굳이 3번째 대회로 보낼 필요가 없다.
QUAL_ECL_TOP_EXEMPT = 2
# 사다리 보강 기준 — CL+EL 합이 이 값 이상인 국가는 ECL도 한 자리 받는다.
# (신민용 확정: "상위 두 대회를 여러 장 받는 나라는 ECL도 한 자리 받아야
# 한다 — 카타르가 CL 3 + EL 2를 받으면 리그 6위 팀이 갈 대회가 없어진다")
QUAL_ECL_LADDER_MIN = 3
# 국가당 슬롯 상한(대회별). 국가 수가 적어 이 상한으로는 정원을 구조적으로
# 못 채우는 대륙(남미 12개국)에서만 필요한 만큼 자동으로 올라간다.
QUAL_MAX_PER_COUNTRY = {"champions": 4, "europa": 3, "conference": 4}
# 곡선 기울기 — 클수록 1~2위 국가에 몰리고, 1.0이면 선형에 가깝다.
# [2026-09 2차 조정] 챔스 기울기를 2.4 → 1.2로 낮췄다. 깊이(0.34)와 조합해
# 아시아 챔스 슬롯이 [4,4,3,3,3,3,2,2,1,1,1]이 되는데, 이는 신민용 참고표
# (일본4 · 사우디4 · 한국3 · 카타르3 · 호주3 · 이란3 · 우즈벡2 · 중국2 ·
# UAE1 · 태국1 · 베트남1)와 **오차 0**으로 일치한다(후보 기울기 6종 ×
# 깊이 3종을 실측 비교해 고른 값). 2.4는 상위 6개국이 전부 국가당 상한(4)에
# 붙어 일본과 이란이 같은 4장이 되는 문제가 있었다.
QUAL_CURVE_STEEP = {"champions": 1.2, "europa": 1.2, "conference": 0.7}


def _distribute(cap: int, depth: int, max_per: int, steep: float) -> list:
    """상위 depth개 국가에 cap장을 내림차순 곡선으로 나눈다.

    w_i = (depth - i) ** steep 비례 배분 + 최대잔여법으로 정수화한 뒤,
    [1, max_per] 범위로 클램프하고 남거나 넘친 장수를 재분배해서 합계가
    정확히 cap이 되게 맞춘다(재분배로도 못 맞추는 경우 = 상한×depth <
    cap 이면 상한을 미리 올려두므로 실제로는 항상 정확히 맞는다).
    반환: 길이 depth의 리스트(0번이 그 대륙 1위 국가).
    """
    if depth <= 0 or cap <= 0:
        return []
    # 국가 수가 적은 대륙(남미)은 상한이 걸려 정원을 못 채운다 — 그럴
    # 때만 필요한 만큼 상한을 올린다(다른 대륙 결과는 전혀 안 바뀜).
    max_per = max(max_per, -(-cap // depth))
    w = [(depth - i) ** steep for i in range(depth)]
    tot = sum(w) or 1.0
    raw = [cap * x / tot for x in w]
    base = [int(v) for v in raw]
    rem = cap - sum(base)
    order = sorted(range(depth), key=lambda i: (-(raw[i] - base[i]), i))
    for i in order[:max(0, rem)]:
        base[i] += 1
    for i in range(depth):
        base[i] = max(1, min(max_per, base[i]))
    diff = cap - sum(base)
    guard = 0
    while diff != 0 and guard < 10000:
        guard += 1
        if diff > 0:
            cands = [i for i in range(depth) if base[i] < max_per]
            if not cands:
                break
            base[cands[0]] += 1   # 남으면 위에서부터
            diff -= 1
        else:
            cands = [i for i in range(depth) if base[i] > 1]
            if not cands:
                break
            base[cands[-1]] -= 1  # 넘치면 아래에서부터
            diff += 1
    return base


def conference_team_cap(continent: str, n_countries: int = None) -> int:
    """그 대륙 컨퍼런스리그 정원 — CONFERENCE_TEAM_CAP 고정값.

    실측 정원: 유럽 36 · 아시아 36 · 아프리카 36 · 북미 32 · 남미 0(대회
    자체를 열지 않음 — 위 CONFERENCE_TEAM_CAP 주석).
    conference_engine._ecl_team_cap이 이 함수를 그대로 쓰므로, 배분 쪽과
    대회 엔진 쪽 정원이 어긋날 수 없다(예전엔 두 파일에 36이 따로 박혀
    있었다). n_countries는 하위호환용으로만 받고 쓰지 않는다 — 정원은
    국가 수와 무관하게 대회 규격으로 고정이다.
    """
    if continent in CONFERENCE_TEAM_CAP:
        return CONFERENCE_TEAM_CAP[continent]
    return QUALIFICATION_TEAM_CAP.get(continent, 36)


def _conference_plan(n: int, cl: list, el: list, ecl_cap: int) -> list:
    """고정 정원 ecl_cap을 우선순위 밴드로 국가에 나눈다(위 3차 주석 참고).

    cl/el은 길이 n으로 패딩된 슬롯 리스트. 반환도 길이 n.
    """
    ecl = [0] * n
    if ecl_cap <= 0 or n <= 0:
        return ecl
    exempt = max(0, min(QUAL_ECL_TOP_EXEMPT, n))
    top = [cl[i] + el[i] for i in range(n)]
    # ① 유일한 경로(최우선) ② 사다리 보강 ③ 중간 — 셋 다 국가 순위 순
    band_only = [i for i in range(exempt, n) if top[i] == 0]
    band_ladder = [i for i in range(exempt, n) if top[i] >= QUAL_ECL_LADDER_MIN]
    band_mid = [i for i in range(exempt, n) if 0 < top[i] < QUAL_ECL_LADDER_MIN]
    left = ecl_cap
    for band in (band_only, band_ladder, band_mid):
        for i in band:
            if left <= 0:
                return ecl
            ecl[i] = 1
            left -= 1
    # 그래도 남으면 2장째부터 — 사다리 보강 국가(강한 나라) 우선
    order = band_ladder + band_mid + band_only
    for extra in range(2, QUAL_MAX_PER_COUNTRY["conference"] + 1):
        for i in order:
            if left <= 0:
                return ecl
            if ecl[i] == extra - 1:
                ecl[i] = extra
                left -= 1
    return ecl


def build_slot_plan(continent: str, n_countries: int, cl_cap: int, ec_cap: int) -> dict:
    """그 대륙의 국가 순위별 슬롯표를 만든다(위 재설계 주석 참고).

    반환: {"champions":[국가1위,2위,...], "europa":[...], "conference":[...],
           "cuts": (d_cl, d_el, d_ecl)}
    각 리스트는 길이 n_countries이며 0은 "그 대회 출전권 없음"을 뜻한다.
    """
    n = max(1, int(n_countries))
    pad = lambda v: (list(v) + [0] * n)[:n]
    ecl_cap = conference_team_cap(continent, n)
    d_cl = min(n, max(4, round(n * QUAL_DEPTH_FRAC["champions"]), -(-cl_cap * 10 // 24)))
    d_el_base = min(n, max(d_cl + 1, round(n * QUAL_DEPTH_FRAC["europa"]),
                           -(-ec_cap * 10 // 18)))
    cl = pad(_distribute(cl_cap, d_cl, QUAL_MAX_PER_COUNTRY["champions"],
                         QUAL_CURVE_STEEP["champions"]))
    # EL 컷을 한 칸씩 늘려가며 "0장 국가 0개"가 되는 가장 얕은 컷을 찾는다
    # (얕을수록 상위국 EL 장수가 두꺼워져 참고표에 가깝다 — 아시아는
    # d_el=28에서 ①30개국+②6개국 = 정확히 36이라 늘릴 필요가 없다).
    # 컷을 늘려도 못 맞추면(EL 정원 자체가 국가 수보다 작은 경우) 마지막
    # 결과를 그냥 쓴다 — _distribute가 컷 안의 모든 나라에 최소 1장을
    # 주므로 d_el은 ec_cap을 넘을 수 없다.
    d_el_max = min(n, max(d_el_base, int(ec_cap)))
    d_el, el, ecl = d_el_base, None, None
    while True:
        el = pad(_distribute(ec_cap, d_el, QUAL_MAX_PER_COUNTRY["europa"],
                             QUAL_CURVE_STEEP["europa"]))
        ecl = _conference_plan(n, cl, el, ecl_cap)
        zero = sum(1 for i in range(n) if cl[i] + el[i] + ecl[i] == 0)
        if zero == 0 or d_el >= d_el_max:
            break
        d_el += 1
    d_ecl = max([i for i in range(n) if ecl[i] > 0], default=-1) + 1
    return {"champions": cl, "europa": el, "conference": ecl,
            "cuts": (d_cl, d_el, d_ecl)}




def _n_countries_in_continent(continent: str) -> int:
    from competition.champions_engine import CONTINENT_MAP
    game_conts = [gc for gc, ck in CONTINENT_MAP.items() if ck == continent]
    conn = get_conn()
    ph = ",".join("?" * len(game_conts))
    n = conn.execute(
        f"""SELECT COUNT(DISTINCT cn.id) FROM countries cn
            JOIN leagues l ON l.country_id = cn.id
            WHERE l.tier=1 AND cn.continent IN ({ph})""", game_conts).fetchone()[0]
    conn.close()
    return n or 1


def _ranked_countries(continent: str, year: int):
    """그 대륙의 국가 목록을, 챔스와 동일한 기준(실측 계수 우선, 부족하면
    클럽 리그 등급)으로 순위를 매겨 반환한다.
    반환: [{"country":..., "grade":..., "lid":..., "flag":...}, ...] (1위부터)
    """
    from competition.champions_engine import CONTINENT_MAP, CL_COEFF_MIN_COUNTRIES
    game_conts = [gc for gc, ck in CONTINENT_MAP.items() if ck == continent]
    conn = get_conn()
    ph = ",".join("?" * len(game_conts))
    leagues = conn.execute(
        f"""SELECT l.id AS lid, cn.name AS country, cn.flag AS flag
            FROM leagues l JOIN countries cn ON l.country_id = cn.id
            WHERE l.tier=1 AND cn.continent IN ({ph})""", game_conts).fetchall()
    leagues = [dict(r) for r in leagues]
    for lg in leagues:
        lg["grade"] = get_country_league_grade(lg["country"])

    ranking = _cl._country_coefficients(get_conn(), continent, year) if year else []
    if len(ranking) >= CL_COEFF_MIN_COUNTRIES:
        rank_map = {c: i for i, (c, _pts) in enumerate(ranking)}
        _grade_rank = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
        # 실측 랭킹에 있는 나라 우선(순위대로), 없는 나라는 등급순으로 뒤에 붙인다.
        ranked_part = [lg for lg in leagues if lg["country"] in rank_map]
        ranked_part.sort(key=lambda lg: rank_map[lg["country"]])
        unranked_part = [lg for lg in leagues if lg["country"] not in rank_map]
        unranked_part.sort(key=lambda lg: -_grade_rank.get(lg["grade"], 0))
        leagues = ranked_part + unranked_part
    else:
        _grade_rank = {"SS": 8, "S": 7, "A": 6, "B": 5, "C": 4, "D": 3, "E": 2, "F": 1}
        leagues.sort(key=lambda lg: -_grade_rank.get(lg["grade"], 0))
    conn.close()
    return leagues


def allocate_continental_slots(continent: str, season: int, year: int = None):
    """한 대륙의 챔스/유로파/컨퍼런스 참가팀을 워터폴 방식으로 확정한다.

    각 나라의 리그 순위표를 위에서부터 훑으며:
      1) 챔스 슬롯 수만큼 챔스로
      2) 그다음 순위부터 유로파 슬롯 수만큼 유로파로
      3) 그다음 순위부터 컨퍼런스 슬롯 수만큼 컨퍼런스로
    떼어간다 — 한 팀이 동시에 두 대회 후보가 될 수 없다(리스트가 겹치지
    않음, 아래 검증 함수로 매 호출마다 실측 확인 가능).

    각 대회는 대륙 정원(유로파/컨퍼런스는 36 또는 48, 챔스는 별도로
    champions_engine.CL_TEAMS_BY_CONTINENT를 따름)을 넘지 않는 선에서,
    국가 순위가 높은 나라부터 채워진다(챔스 _select_entries와 동일한
    원칙 — 정원 초과분은 그냥 버려짐, 억지로 안 채움).

    반환: {"champions": [entry,...], "europa": [entry,...], "conference": [entry,...]}
    entry = {"team_id","team_name","flag","country","grade","ovr","cl_rank"}
    (cl_rank: 그 나라 리그 순위표 상 몇 위 팀인지, 1=우승팀. champions_engine._entry_from
    재사용이라 이름이 cl_rank지만 대회 종류 무관하게 "국내 순위"라는 뜻이다.)
    """
    # [2026-08 버그수정, 신민용 리포트: "유로파/컨퍼런스 북남미가 36이
    # 아니라 30~48로 어긋남"] 예전엔 cap 하나를 챔스/유로파/컨퍼런스
    # 셋 다에 공용으로 썼다 — 북남미 챔스를 48로 키우려던 조정이 유로파/
    # 컨퍼런스까지 같이 48로 끌고 가버렸다. 챔스는 champions_engine의
    # 대륙별 정원(북남미만 48)을 그대로 쓰고, 유로파/컨퍼런스는 항상
    # QUALIFICATION_TEAM_CAP(모든 대륙 36)을 쓰도록 분리한다.
    cl_cap = _cl.CL_TEAMS_BY_CONTINENT.get(continent, 36)
    el_cf_cap = QUALIFICATION_TEAM_CAP.get(continent, 36)
    countries = _ranked_countries(continent, year)
    # [2026-09 2차 수정] 컨퍼런스만 정원이 다르다 — 위 conference_team_cap
    # 주석 참고(3위~최하위 전원 최소 1장을 만족하는 정원).
    cf_cap = conference_team_cap(continent, len(countries))
    cap_by_comp = {"champions": cl_cap, "europa": el_cf_cap, "conference": cf_cap}

    # [2026-09 전면 재설계, 위 build_slot_plan 주석 참고] 대회별 커브를
    # 따로 그려 정원에서 자르던 방식(0장 국가 40%, 워터폴 컷 역전)을
    # 버리고, 이 대륙 국가 수에서 컷 깊이를 역산한 슬롯표를 쓴다.
    # _europa_slots_from_rank / _conference_slots_from_rank / _cl.get_cl_slots
    # 는 다른 호출부(챔스 엔진의 "내 팀이 나가는지" 판정 등)가 계속 쓰므로
    # 지우지 않고 그대로 남겨둔다 — 이 배분에서만 쓰지 않는다.
    _plan = build_slot_plan(continent, len(countries), cl_cap, el_cf_cap)

    out = {"champions": [], "europa": [], "conference": []}
    # 나라별 (순위표, 커서) — 아래 정원 보충 패스에서 재사용한다.
    state = {}
    for rank_idx, lg in enumerate(countries):
        if all(len(out[k]) >= cap_by_comp[k] for k in out):
            break
        ch_slots = _plan["champions"][rank_idx]
        eu_slots = _plan["europa"][rank_idx]
        cf_slots = _plan["conference"][rank_idx]
        if ch_slots <= 0 and eu_slots <= 0 and cf_slots <= 0:
            continue

        rows = _cl._standings_or_pseudo(lg["lid"], season)
        if not rows:
            continue

        cursor = 0  # 이 나라 순위표에서 어디까지 이미 떼어갔는지
        for comp_name, slots in (("champions", ch_slots), ("europa", eu_slots),
                                  ("conference", cf_slots)):
            if slots <= 0:
                continue
            remaining_cap = cap_by_comp[comp_name] - len(out[comp_name])
            take = min(slots, remaining_cap, len(rows) - cursor)
            if take <= 0:
                cursor += slots  # 정원 꽉 찼어도 다음 대회를 위해 순위 커서는 그대로 전진
                continue
            for i in range(take):
                row = rows[cursor + i]
                out[comp_name].append(
                    _cl._entry_from(lg, row, cl_rank=cursor + i + 1))
            cursor += slots
        state[rank_idx] = (lg, rows, min(cursor, len(rows)))

    # [2026-09 신설] 정원 보충 패스 — 슬롯표가 배정한 장수보다 그 나라 1부
    # 팀 수가 적으면 그만큼 정원이 빈다. 실측: 북미 캐나다가 1부 8팀인데
    # CL4+EL3+ECL2 = 9장을 받아 컨퍼런스가 32 정원에 31팀만 찼다.
    # 남는 자리는 국가 순위가 높은 나라의 "아직 안 뽑힌 다음 순위 팀"으로
    # 채운다(챔스 _select_entries와 같은 원칙). 커서를 공유하므로 이
    # 패스로 같은 팀이 두 대회에 들어가는 일은 없다(verify_no_overlap로
    # 매 호출 검증 가능).
    for comp_name in ("champions", "europa", "conference"):
        need = cap_by_comp[comp_name] - len(out[comp_name])
        if need <= 0:
            continue
        for rank_idx in sorted(state):
            if need <= 0:
                break
            lg, rows, cursor = state[rank_idx]
            while need > 0 and cursor < len(rows):
                out[comp_name].append(
                    _cl._entry_from(lg, rows[cursor], cl_rank=cursor + 1))
                cursor += 1
                need -= 1
            state[rank_idx] = (lg, rows, cursor)

    return out


def start_all_continental_competitions(year, season):
    """[2026-08 신설] CL_START_WEEK(8주차) 진입 시 game_engine이 호출하는
    통합 진입점 — 챔스/유로파/컨퍼런스 3개 대회를 한 번에 생성한다.
    대륙마다 allocate_continental_slots()를 딱 1번만 호출해서(국가 순위
    계산 + 워터폴을 세 대회가 각자 따로 다시 하면 3배 낭비이므로) 그
    결과를 세 대회 엔진에 나눠준다.

    champions_engine.start_champions_league()의 "직전 시즌 없으면 스킵/
    이미 생성됐으면 중복 방지/내 팀 안내 로그" 정책은 그대로 유지하되,
    유로파·컨퍼런스는 참가팀 자체가 챔스보다 훨씬 넓어서(27개국/23개국 vs
    보통 10여개국) 매 시즌 정상적으로 열린다."""
    from game_engine import add_log, get_player
    from competition import champions_engine as _cl_mod
    from competition import europa_engine
    from competition import conference_engine
    p = get_player()
    if not p:
        return
    prev_season = season - 1
    if prev_season < 0:
        return
    if _cl_mod.get_cl_tournament(year, "유럽"):
        return  # 이미 이번 연도 생성됨(챔스 기준으로 중복 방지 판단 — 셋 다 항상 같이 생성되므로)

    _cl_mod._clear_entry_cache()
    from competition.competition_common import clear_entry_cache
    clear_entry_cache()

    my_cont = _cl_mod._my_continent(p)
    my_tid = p.get("current_team_id", 0)

    # [2026-09 개편] "북남미" → 남미/북미 분리(5개 대륙).
    for cont in ("유럽", "아시아", "아프리카", "남미", "북미"):
        alloc = allocate_continental_slots(cont, prev_season, year)
        this_my_tid = my_tid if cont == my_cont else 0

        if len(alloc["champions"]) >= 4:
            _cl_mod._build_tournament(year, cont, alloc["champions"], this_my_tid)
        if len(alloc["europa"]) >= 4:
            europa_engine.build_from_qualification(year, cont, alloc["europa"], this_my_tid)
        if len(alloc["conference"]) >= 4:
            conference_engine.build_from_qualification(year, cont, alloc["conference"], this_my_tid)

    add_log("─" * 44, "sep")
    add_log(f"🏆 {year}년 클럽 대항전(챔피언스리그/유로파리그/컨퍼런스리그) 개막!", "event")


def verify_no_overlap(alloc: dict) -> list:
    """세 대회 참가팀 리스트에 겹치는 team_id가 있는지 검증. 겹치면 그
    team_id 목록을 반환(정상이면 빈 리스트)."""
    seen: dict = {}
    dupes = []
    for comp_name, entries in alloc.items():
        for e in entries:
            tid = e["team_id"]
            if tid in seen and seen[tid] != comp_name:
                dupes.append((tid, seen[tid], comp_name))
            seen[tid] = comp_name
    return dupes