"""economy.py - 게임 경제 계층: 연봉·시장가치(이적료 추정치) 계산.

[2026-07 신설, 신민용 요청: "OVR/연봉/계약금 관련 코드를 game_engine에
계속 얹는 것보다 따로 빼는 게 낫지 않냐"] 기존엔 game_engine.py(수천 줄)
안에 있던 연봉/시장가치 계산 로직을 여기로 분리했다.

분리 기준:
  - OVR '생성'(스쿼드 만들 때 1회성, ai_players 테이블 쓰기 필요)은
    database.py에 그대로 남긴다 — DB 커서와 강하게 묶여 있어서 떼면 오히려
    복잡해짐.
  - 연봉/시장가치 '계산'은 (grade, tier, ovr, country, team_name)만 있으면
    되는 순수 함수라 여기로 분리 — DB 의존성 없음, 어디서든 가볍게
    import해서 테스트 가능.

game_engine.py는 이 모듈에서 필요한 함수를 import해서 그대로 쓴다
(호출부 시그니처는 전혀 안 바뀜).
"""


def economy_index(year: int) -> float:
    """[2026-07 신설, 신민용+GPT 다회 검토 확정, v4] 시대별 경제 배율.
    지금까지 연봉·시장가치 앵커는 전부 "2026년 축구 경제" 기준으로 잡혀
    있었는데, 게임은 2001년부터 시작해서 2040년대까지 진행된다 — 즉
    2001년에도 2026년 수준의 거액(OVR97=연봉 550억/이적료 6000억 등)이
    그대로 나오는 시대착오가 있었다. 연도별 배율로 시대감을 반영한다.

    앵커(2001=0.48은 2차 검토에서 0.40→0.48로 상향 확정 — "갈락티코 시대
    (지단·호나우두·피구·베컴)도 지금보단 적었을 뿐 상당한 금액을 받았다"는
    근거. OVR97 EPL 기준 550억×0.48≈264억로 "당대 최고 선수" 느낌).
    2026=1.00(현재 앵커 그대로 나오는 기준점). 구간 사이는 선형 보간 —
    현실은 점프·정체가 반복되지만(2008 금융위기/2017 네이마르 등),
    게임에서는 예측 가능한 성장 곡선이 플레이어 경험상 더 낫다는 결론
    (3차 검토에서도 재확인).
    """
    anchors = [
        (2001, 0.48), (2005, 0.50), (2010, 0.65), (2015, 0.82),
        (2020, 0.95), (2026, 1.00), (2035, 1.20), (2040, 1.35),
    ]
    if year <= anchors[0][0]:
        return anchors[0][1]
    if year >= anchors[-1][0]:
        return anchors[-1][1]   # 2040 이후 정책은 추후 별도 확정
    for (y1, v1), (y2, v2) in zip(anchors, anchors[1:]):
        if y1 <= year <= y2:
            t = (year - y1) / (y2 - y1)
            return v1 + (v2 - v1) * t
    return anchors[-1][1]


def _apply_soft_cap(multiplier: float, threshold: float = 1.6, damp: float = 0.4) -> float:
    """[2026-07 신설, v4] 이적료 복합배율(리그×포지션×명문×계약×나이×잠재력)이
    전부 곱연산이라, 조건이 몰리면(SS급+명문팀+ST+21세 이하+계약5년+큰 잠재력
    gap) 개별 배율의 의도보다 훨씬 커지는 문제가 있었다 — 앵커에서 2800억으로
    잡아놔도 최종 5000억대까지 튀는 식. threshold를 넘는 초과분에만 damp
    비율을 적용해 극단값만 눌러준다(평범한 선수는 threshold 밑이라 전혀 영향
    없음). 파라미터(1.6/0.4)는 GPT 권고값 — 구현 후 대규모 시뮬레이션으로
    분포를 보고 재검증 필요(v4 문서 TODO)."""
    if multiplier <= threshold:
        return multiplier
    return threshold + (multiplier - threshold) * damp


def _base_market_value_eok(ovr: int) -> float:
    """OVR → 기본 시장가치(억원), 연봉과 완전히 독립된 축.
    [2026-07 재설계, 신민용 지적: "연봉×배수 구조면 사우디처럼 연봉만 높은
    리그의 시장가치까지 같이 폭증한다 — 호날두(연봉 높음/시장가치 낮음)
    vs 야말(연봉 낮음/시장가치 높음)처럼 둘은 다른 축이어야 한다"]
    실력만으로 결정되는 기본가치를 구간별 앵커 포인트로 고정하고, 구간
    안에서는 지수보간한다. 리그 재정력은 이 값에 ±20~30%만 얹는다
    (아래 _market_value_league_mult).

    [2026-07 5차 재조정, v4(GPT 다회 검토 확정)] 96/98/100 앵커(3000/4500/6000억)가
    현실 역대 최고 이적료(네이마르 약 3000억, 엔조 페르난데스 약 1800억,
    카이세도 약 2000억, 벨링엄 약 1700~1800억)보다 이미 96부터 웃돌아서,
    96 이상 선수는 전부 "역사상 최고 이적료 후보"가 되는 문제가 있었다.
    2026년 기준으로 앵커를 하향하고, 대신 economy_index(year)로 시대별
    스케일을 조정하는 구조로 분리했다(연도 인플레이션은 이 함수가 아니라
    호출부에서 별도 처리). 97 앵커를 새로 추가해 96→100 구간을 더 촘촘하게
    보간한다.
    """
    anchors = [
        (40, 0.02), (60, 0.1), (70, 3), (75, 8), (80, 20),
        (82, 35), (85, 80), (88, 200), (90, 400), (92, 850),
        (94, 1200), (95, 1700), (96, 2300), (97, 2800), (98, 3400),
        (99, 4100), (100, 5000),
    ]
    if ovr <= anchors[0][0]:
        return anchors[0][1]
    if ovr >= anchors[-1][0]:
        return anchors[-1][1]
    for (o1, v1), (o2, v2) in zip(anchors, anchors[1:]):
        if o1 <= ovr <= o2:
            t = (ovr - o1) / (o2 - o1)
            # 로그보간 — 구간 안에서도 위로 갈수록 급격히 커지는 곡률 유지
            import math
            lv1, lv2 = math.log(max(v1, 0.001)), math.log(max(v2, 0.001))
            return math.exp(lv1 + (lv2 - lv1) * t)
    return anchors[-1][1]


# [2026-07 신설] 리그 재정력은 시장가치에 20~30%만 반영(연봉만큼 크게
# 흔들지 않음). 등급별 기본값 + 나라별 역보정(오일머니 리그처럼 "연봉은
# 높아도 시장가치는 오히려 낮아야 하는" 예외 케이스).
# [2026-07 v4 재조정] SS 1.20→1.15로 하향 — 소프트캡 신설과 별개로,
# 개별 배율 자체도 조금씩 낮춰서 극단값 도달을 더 어렵게 한다는 취지.
MARKET_VALUE_GRADE_MULT = {
    "SS": 1.15, "S": 1.05, "A": 0.90, "B": 0.70,
    "C": 0.50, "D": 0.35, "E": 0.25, "F": 0.15,
}
MARKET_VALUE_COUNTRY_MULT = {
    # [2026-07 재조정, 신민용 지적: "사우디가 전부 ×0.55면 '기존 스타
    # 이적'과 '22살 유망주 영입'을 구분 못 한다 — 알힐랄이 젊은 브라질
    # 유망주를 700억에 사 오는 경우도 있는데, 국적만으로 저평가되는
    # 셈"] 0.55(전체 역보정) → 0.8로 완화. 나이별 세분화(노장 0.55 /
    # 유망주 1.0)는 ai_players에 나이 컬럼이 생겨야 가능해 5.2절 과제로
    # 남긴다 — 지금은 그 중간값으로 절충.
    "사우디아라비아": 0.8,
    "카타르":        0.8,
    "아랍에미리트":  0.8,
    "브라질":        1.15,   # 유망주 생산·유럽 진출 전 단계 시장이 강함
}


# [2026-07 신설, 신민용 지적: "같은 OVR90이어도 ST가 CB보다 비싼 게
# 현실"] 포지션별 시장가치 배수. position 인자가 없으면(하위호환) 1.0.
POSITION_MARKET_MULT = {
    # [2026-07 재조정, 신민용 지적: "반다이크·그바르디올·루벤 디아스처럼
    # 최근 시장에서 CB도 공격수 못지않게 비싸다 — GK 0.75는 너무 FM식"]
    "ST": 1.15, "CF": 1.15, "LW": 1.10, "RW": 1.10,
    # [2026-07 v4 재조정] 시장가치 앵커 근거로 인용한 실측 사례(엔조
    # 페르난데스 약 1800억, 카이세도 약 2000억)가 둘 다 CDM인데 정작
    # 배율은 CM과 같은 1.00(평균)이었다 — 근거 데이터와 배율표가 안 맞는
    # 모순. 최근 시장에서 CDM이 확실히 비싸졌다는 지적도 반영해 CAM
    # 근처로 상향.
    "CAM": 1.10, "CM": 1.00, "CDM": 1.08,
    "LB": 0.95, "RB": 0.95, "LWB": 0.95, "RWB": 0.95,
    "CB": 1.05, "GK": 0.85,
}


def _market_value_league_mult(grade: str, country: str = None) -> float:
    base = MARKET_VALUE_GRADE_MULT.get(grade, 0.5)
    if country and country in MARKET_VALUE_COUNTRY_MULT:
        return MARKET_VALUE_COUNTRY_MULT[country]
    return base


def _market_value_prestige_mult(country: str = None, team_name: str = None,
                                 tier: int = 1) -> float:
    """[2026-07 v7 재조정, 신민용+GPT 다회 검토: "같은 등급 안에서도
    구단 위상 차이(팬 규모·판매 경험·브랜드력)는 나야 한다 — 단 구단
    재정 시스템처럼 크게 벌리지 말고 가볍게(±10%대)만"] 예전엔 명문팀
    리스트에 있으면 무조건 1.05 고정이었는데, "전통 명문"과 "세계급
    명문"이 똑같이 취급되는 게 어색하다는 지적 — 국가별 연봉 프리미엄
    (PRESTIGE_SALARY_MULT, 이미 나라마다 세분화돼 있음)에 비례해서
    스케일하되, 연봉만큼 크게 벌어지지 않도록 강하게 압축(10%만 반영)
    한다. 예: 연봉 프리미엄 2.0배 국가의 명문팀 → 이적료는 1.10배만.
    """
    if not (team_name and country):
        return 1.0
    from data.prestige_clubs import is_prestige, PRESTIGE_SALARY_MULT, PRESTIGE_SALARY_MULT_DEFAULT
    if is_prestige(country, tier, team_name):
        salary_mult = PRESTIGE_SALARY_MULT.get(country, PRESTIGE_SALARY_MULT_DEFAULT)
        return max(1.0, min(1.20, 1.0 + (salary_mult - 1.0) * 0.10))
    return 1.0


def _team_rank_status_mult(team_id, tier: int = 1, season=None) -> float:
    """[2026-07 신설] "같은 등급이라도 그 리그 안에서 상위권/하위권이면
    협상력이 다르다"는 지적 — 구단 재정 시스템은 안 만들고, 이미 있는
    "현재 리그 순위"만 가볍게 반영한다(±5% 이내). 명문팀 리스트 밖의
    일반 팀들 사이에서도 "리그 상위권팀 vs 하위권팀" 차이를 조금
    만들어준다. 순위를 못 구하면(팀ID 없음, 시즌 초반 등) 중립(1.0).

    [주의] teams.wins/draws/losses는 예전에 스케줄 재생성 버그로 부풀던
    캐시 컬럼이라 여기서 직접 안 쓰고, 이미 match_results 기준으로
    정확하게 재계산해주는 get_league_standings()를 그대로 재사용한다."""
    if not team_id or tier != 1:
        return 1.0
    try:
        from game_engine import get_league_standings
        from database import get_conn
        conn = get_conn()
        row = conn.execute("SELECT league_id FROM teams WHERE id=?", (team_id,)).fetchone()
        if not row:
            conn.close()
            return 1.0
        league_id = row["league_id"]
        conn.close()
        standings = get_league_standings(league_id, season=season)
        if not standings or len(standings) < 4:
            return 1.0
        rank = next((i for i, t in enumerate(standings) if t.get("id") == team_id), None)
        if rank is None:
            return 1.0
        pct = rank / max(1, len(standings) - 1)   # 0=1위, 1=꼴찌
        if pct <= 0.20:
            return 1.05
        if pct >= 0.80:
            return 0.97
        return 1.0
    except Exception:
        return 1.0


CONTRACT_MULT = {0: 0.6, 1: 0.75, 2: 0.9, 3: 1.0, 4: 1.1}
CONTRACT_MULT_5PLUS = 1.2


def _contract_mult(contract_remaining_years) -> float:
    """[11차 설계] 계약기간 계수 — "선수 실력 보정"이 아니라 "판매
    협상력 보정"이다. None(모름/AI 선수 등 데이터 없음)이면 1.0(중립).
    0년(진짜 계약만료/FA)은 estimate_transfer_fee()에서 exit_type으로
    먼저 걸러지므로 여기까지 오면 "계약 중이지만 얼마 안 남음"으로
    취급한다."""
    if contract_remaining_years is None:
        return 1.0
    if contract_remaining_years >= 5:
        return CONTRACT_MULT_5PLUS
    return CONTRACT_MULT.get(max(0, int(contract_remaining_years)), 1.0)


def _age_mult(age) -> float:
    """나이 계수 — "영향도 캡" 방식(순수 곱연산 폭주 방지). None이면 1.0."""
    if age is None:
        return 1.0
    if age <= 21:
        raw = 1.75
    elif age <= 25:
        raw = 1.35
    elif age <= 29:
        raw = 1.0
    elif age <= 33:
        raw = 0.8
    else:
        raw = 0.5
    return 1.0 + (raw - 1.0) * 0.5   # 영향력을 절반으로 완화


def potential_mult(current_ovr, talent_cap) -> float:
    """잠재력 계수 — talent_cap(전성기 최대 OVR, 기존 필드 재사용)과
    현재 OVR의 gap이 클수록(=아직 다 안 큰 유망주일수록) 프리미엄이
    붙는다. gap에 current_ovr 비례 가중치(quality)를 곱해서, "현재
    OVR70·gap25"인 선수가 "현재 OVR85·gap10"인 완성형 선수보다
    비싸지는 역전을 완화한다.

    [2026-07 v4 재조정] 나이 배율과 잠재력 배율이 둘 다 "아직 안 큰
    유망주"에게 겹쳐 붙어서 중복 반영되는 느낌이 있다는 지적 — 계수를
    0.03→0.015로 절반 낮춘다(예: OVR90·gap10이면 기존 +27% → 현재 +13%
    수준으로 완화).
    """
    if not talent_cap or talent_cap <= current_ovr:
        return 1.0
    gap = talent_cap - current_ovr
    quality = current_ovr / 100.0
    return 1.0 + gap * 0.015 * quality


def estimate_transfer_fee(grade, tier, ovr, country=None, team_name=None,
                          position=None, exit_type=None, age=None,
                          talent_cap=None, contract_remaining_years=None,
                          year=None, team_id=None, season=None, debug=False):
    """선수 시장가치(이적료 추정치, 천원 단위).

    exit_type: [11차 신설] "계약만료"면 진짜 FA — 다른 계수 계산 없이
      즉시 0을 반환한다(3.11/3.13절). "6개월 남음"과 "진짜 계약 끝남"을
      contract_remaining_years 숫자만으로 구분하면 위험해서, 이 판정을
      계산의 가장 첫 단계로 명시적으로 분리했다.
    age, talent_cap, contract_remaining_years: [11차 신설] 없으면(None,
      AI 선수 등) 전부 중립(1.0)으로 하위호환. my_player는 이미
      age/talent_cap/contract_end_year 필드가 다 있어 바로 연결 가능.
    year: [2026-07 v4 신설] 게임 내 현재 연도 — economy_index(year)로
      시대별 배율을 곱한다. None이면(하위호환) 1.0(2026년 기준, 배율
      없음)으로 취급 — 호출부가 아직 year를 안 넘기는 경우에도 기존과
      동일하게 동작.
    team_id, season: [2026-07 v7 신설, 신민용 지적: "같은 등급 안에서도
      팀별 위상 차이가 있어야 한다"] 있으면 _team_rank_status_mult로
      "그 리그 안에서 상위권/하위권인지"를 가볍게(±5%) 반영한다. 없으면
      (AI 선수 등 기존 호출부) 중립(1.0)으로 하위호환.
    """
    if exit_type == "계약만료":
        return 0

    base_eok = _base_market_value_eok(ovr)
    league_mult = _market_value_league_mult(grade, country)
    pos_mult = POSITION_MARKET_MULT.get(position, 1.0) if position else 1.0
    prestige_mult = _market_value_prestige_mult(country, team_name, tier)
    rank_mult = _team_rank_status_mult(team_id, tier, season) if team_id else 1.0
    c_mult = _contract_mult(contract_remaining_years)
    a_mult = _age_mult(age)
    p_mult = potential_mult(ovr, talent_cap)

    # [2026-07 v4 신설] 리그×포지션×명문×계약×나이×잠재력이 전부 곱연산이라
    # 조건이 몰리면(SS급+명문팀+ST+21세 이하+계약5년+큰 잠재력 gap) 개별
    # 배율의 의도보다 훨씬 커지는 문제가 있었다 — 앵커에서 2800억으로
    # 잡아놔도 최종 5000억대까지 튀는 식. 결합배율에 소프트캡을 적용해
    # 평범한 선수는 그대로, 극단적으로 조건이 겹친 경우만 완화한다.
    combined_mult = league_mult * pos_mult * prestige_mult * rank_mult * c_mult * a_mult * p_mult
    capped_mult = _apply_soft_cap(combined_mult)

    val_eok = base_eok * capped_mult
    if tier and tier >= 2:
        val_eok *= max(0.15, 0.55 ** (tier - 1))  # 2부 0.55x, 3부 0.30x ...

    # [2026-07 v4 신설] 시대 배율 — 게임은 2001년부터 시작하는데 위 앵커는
    # 전부 2026년 기준이라, 시대감을 살리기 위해 곱한다. 연봉과 동일하게
    # 지수 없이 그대로 적용(2차 검토에서 ^0.8 지수를 시도했으나 2003년
    # 값이 여전히 과하다는 3차 검토를 반영해 1.0으로 통일 — v4 문서 참고).
    eidx = economy_index(year) if year is not None else 1.0
    val_eok *= eidx

    final = int(val_eok * 100_000)  # 억원 -> 천원 단위(_calc_salary와 통일)

    # [2026-07 v6 재조정, 신민용+GPT 검토: "C급 하위권은 연봉 1.126억인데
    # 이적료가 0.132억(연봉의 12%)밖에 안 된다 — 계약 3년 남은 27세면
    # 최소 연봉의 절반 정도는 나오는 게 현실적이다"] 이적료는 원래
    # OVR·등급·역할·계약기간만으로 계산해 연봉과 완전히 독립적인 축으로
    # 설계했는데, C/D급 하위권에서는 그 결과가 "등록비" 수준까지 떨어져
    # 부자연스러웠다. C/D급에 한해 "이적료 최저선 = 연봉의 일정 비율"을
    # 하한으로만 걸어준다(다른 등급은 원래도 이 정도로 낮게 안 떨어지므로
    # 영향 없음). 참고용 연봉은 같은 조건으로 _calc_salary를 내부에서
    # 한 번 더 불러 구한다 — 두 축의 "독립적 계산" 설계 원칙은 그대로
    # 유지하면서, 결과가 비상식적으로 벌어질 때만 안전망으로 개입한다.
    _MIN_FEE_RATIO_OF_SALARY = {"C": 0.40, "D": 0.25}
    if grade in _MIN_FEE_RATIO_OF_SALARY:
        ref_salary = _calc_salary(grade, tier, ovr, country, team_name, year=year)
        fee_floor = int(ref_salary * _MIN_FEE_RATIO_OF_SALARY[grade])
        if final < fee_floor:
            final = fee_floor

    if debug:
        return {"fee": final, "debug": {
            "base_eok": base_eok, "league_mult": league_mult,
            "position_mult": pos_mult, "prestige_mult": prestige_mult,
            "contract_mult": c_mult, "age_mult": a_mult,
            "potential_mult": p_mult, "rank_mult": rank_mult, "combined_mult": combined_mult,
            "capped_mult": capped_mult, "economy_index": eidx,
        }}
    return final



def _salary_ovr_mult(ovr: int) -> float:
    """OVR → 연봉 배수. 4구간 piecewise.

    구간별 특성:
      OVR40~79: 완만한 상승 (0.08 → 2.00)
      OVR80~89: 가파른 가속 (2.00 → 16.00, 에이스 프리미엄)
      OVR90~92: 완충 (16.00 → 40.00)
      OVR93~99: 급격 (40.00 → 141.60, 월드클래스)

    SS 1부(base 19,996,093천원) 기준:
      OVR82 → 45억/년 (평균)
      OVR87 → 155억/년
      OVR90 → 320억/년
      OVR93 → 800억/년
      OVR99 → 2831억/년 (CAP 3000억)
    """
    if ovr < 80:
        t = max(0.0, (ovr - 40) / 40.0)
        return 0.08 + t ** 2.2 * 1.92        # 40→79: 0.08 → 2.00
    elif ovr < 90:
        t = (ovr - 80) / 10.0
        return 2.0 + t ** 2.5 * 14.0         # 80→89: 2.00 → 16.00
    elif ovr < 93:
        t = (ovr - 90) / 3.0
        return 16.0 + t ** 1.5 * 24.0        # 90→92: 16.00 → 40.00
    else:
        t = (ovr - 93) / 6.0
        return 40.0 + t ** 1.2 * 101.6       # 93→99: 40.00 → 141.60


def _salary_ovr_adj(ovr: int, grade: str, tier: int) -> float:
    """하위 호환 래퍼 — _salary_ovr_mult 위임."""
    return _salary_ovr_mult(ovr)

def _salary_cap_table():
    """등급별 연봉 상한(천원) 테이블. _calc_salary와 _clamp_salary_to_cap이
    같은 값을 쓰도록 한 곳에만 정의해 둔다."""
    return {
        "SS": 50_000_000, "S": 20_000_000, "A": 5_000_000, "B": 1_000_000,
        "C": 300_000, "D": 50_000, "E": 20_000, "F": 10_000,
    }


def _tier_scaled_country_cap(country_cap, tier):
    """[버그수정 2026-07, 신민용 지적: "K1이랑 K2가 둘 다 30억으로 고정"]
    COUNTRY_SALARY_CAP은 나라별 flat 값 하나뿐이라 tier를 전혀 구분하지
    않았다 — 그래서 대한민국처럼 COUNTRY_SALARY_CAP만 있고
    LOWER_TIER_SALARY_CAP은 없는 나라는 K1(1부)이든 K2(2부)든 OVR이 충분히
    높으면 똑같은 상한(30억)에 눌렸다. tier1 대비 비율로 낮춰서 부가
    내려갈수록 확실히 낮아지게 한다(LOWER_TIER_SALARY_CAP이 6개국에
    쓰는 비율과 동일한 스케일)."""
    if tier <= 1 or country_cap <= 0:
        return country_cap
    ratio = {2: 0.35, 3: 0.15, 4: 0.06, 5: 0.025}.get(tier, 0.01)
    return max(1, int(country_cap * ratio))


def _cap_relief_mult(ovr) -> float:
    """[2026-07 신설, 신민용 지적: "브라질이 OVR88 이상 전부 30억으로
    고정 — 네이마르급도 그냥 준수한 선수랑 연봉이 똑같아진다"]
    COUNTRY_SALARY_CAP은 나라 전체에 적용되는 평평한 고정 상한이라, 실측
    앵커 곡선이 없는 나라(6개국 제외 전부)는 재능 등급이 아무리 올라가도
    상한을 넘는 순간부터 연봉이 안 오르는 구조적 결함이 있었다.
    평범~프로(OVR≤80) 구간은 원래 상한을 안전망으로 그대로 쓰고,
    엘리트(81~94)·월드클래스(95+) 구간만 상한을 단계적으로 풀어준다.
    """
    if ovr is None or ovr <= 80:
        return 1.0
    if ovr < 90:
        t = (ovr - 80) / 10.0
        return 1.0 + t * 0.8          # 80→90: 1.0배 → 1.8배
    if ovr < 95:
        t = (ovr - 90) / 5.0
        return 1.8 + t * 1.2          # 90→95: 1.8배 → 3.0배
    t = min(1.0, (ovr - 95) / 5.0)
    return 3.0 + t * 3.0              # 95→100: 3.0배 → 6.0배


def _clamp_salary_to_cap(sal, wealth, country=None, tier=1, is_special=False, ovr=None):
    """[버그수정 2026-07, 신민용 지적] 등급/국가별 연봉 상한 최종 안전망.

    _calc_salary는 자기 내부에서만 캡을 체크하는데, 재계약 협상 성공
    (_negotiate_renew_contract: old_salary * (1+raise_pct))이나 승격 연봉
    인상(old_salary * mult, 최대 2.00배)처럼 '이미 확정된 old_salary'에
    배율만 곱해 새 연봉을 만드는 경로는 _calc_salary를 아예 거치지 않아서
    등급 캡이 통째로 새고 있었다 — 예: D등급(캡 5천만)이어도 재계약을 몇 번
    성공하거나(회당 +12~30%, 재클램프 없이 계속 복리로 누적) 승격 인상이
    한 번(최대 2.00배)만 걸려도 5천만 → 1억 넘게 쉽게 뚫림.
    이 함수를 old_salary*배율 계산 직후에 반드시 한 번 더 거치게 해서,
    어느 경로로 연봉이 바뀌든 등급 최고 상한을 넘지 못하게 한다.

    [버그수정 2026-07 #2, 신민용 지적: "프랑스가 다 200억으로 고정"] 이
    함수가 등급 범용 캡(_salary_cap_table, S=200억)을 country_cap 유무와
    무관하게 무조건 먼저 적용하고 있었다 — 근데 프랑스/스페인/독일/
    이탈리아/잉글랜드는 COUNTRY_SALARY_CAP이 그 범용 캡보다 훨씬 높게
    (2,050억) 따로 설계돼 있다("역대급 선수는 S급 이상 어디든 ~2,000억
    가능"). _calc_salary 본문(tier1 커브 국가 조기 반환 분기)은 이걸 제대로
    지키는데, 이 clamp 함수는 그 우선순위를 무시하고 낮은 범용 캡으로 먼저
    눌러버려서 오퍼 화면에 뜨는 실제 금액이 항상 200억으로 뭉개졌다 —
    country_cap이 있으면 그걸 우선(더 낮은 범용 캡을 추가로 덧씌우지 않음),
    없을 때만 범용 캡을 쓰도록 순서를 맞춘다.

    [버그수정 2026-07 #3, 신민용 지적: "브라질 OVR88+ 전부 30억 고정"]
    ovr 인자가 주어지면 _cap_relief_mult로 엘리트/월드클래스 구간의 상한을
    완화한다. 기본값 None → 기존 호출부는 하위호환(완화 없음)."""
    from constants import COUNTRY_SALARY_CAP, LOWER_TIER_SALARY_CAP
    relief = _cap_relief_mult(ovr)
    country_cap = COUNTRY_SALARY_CAP.get(country, 0) if (country and not is_special) else 0
    if country_cap > 0:
        sal = min(sal, int(_tier_scaled_country_cap(country_cap, tier) * relief))
    else:
        cap = _tier_scaled_country_cap(_salary_cap_table().get(wealth, 0), tier)
        if cap > 0:
            sal = min(sal, int(cap * relief))
    if country and not is_special and tier >= 2:
        _lt = LOWER_TIER_SALARY_CAP.get(country, {})
        if _lt:
            lt_cap = _lt.get(tier, _lt[max(_lt.keys())])
            if lt_cap > 0:
                sal = min(sal, int(lt_cap * relief))
    return max(0, int(sal))


# [2026-07 v4 신설, 신민용 지적: "하드 플로어를 쓰니 슬로바키아=우간다=
# 볼리비아가 전부 같은 값이 돼버린다 — 국가 차이가 사라진다"] 처음엔
# max(b, 고정값) 방식으로 했는데, D/E/F급 국가 대부분의 cont_mult가
# 워낙 작아서(0.01~0.08대) 거의 다 같은 플로어에 눌려 국가 간 순위가
# 사라졌다. 고정 바닥 대신 "곱연산 부스트"로 바꿔 순위는 그대로 두고
# 절대 스케일만 올린다 — 목표(신민용 제시: D급 OVR53~63이 0.08~0.30억
# 근방)에 맞춰 역산한 배율(34배)을 D/E/F 공통으로 적용.
_LOW_GRADE_SCALE_BOOST = {"D": 34.0, "E": 34.0, "F": 34.0}


def _calc_salary(grade, tier, ovr, country=None, team_name=None, year=None):
    """연봉 계산 (천원 단위).
    wealth 결정 우선순위:
      1) SPECIAL_SALARY_COUNTRIES — 특수 연봉 국가 (사우디/카타르/UAE)
      2) COUNTRY_LEAGUE_GRADE    — 리그 전용 등급 (국대 등급과 분리)
      3) grade 파라미터           — fallback
    나라별 연봉 배율(COUNTRY_SALARY_MULT) 추가 적용:
      같은 등급 내에서도 나라마다 재정 수준이 달라 연봉 차이 반영.
      단, SPECIAL_SALARY_COUNTRIES(사우디 등)는 배율 적용 제외.

    team_name: [2026-07 신설] 주어지면 그 팀이 명문팀(prestige_clubs.py)인지
      확인해서 PRESTIGE_SALARY_MULT를 최종적으로 곱한다. OVR 팀간 격차는
      건드리지 않고 "이 팀 소속이면 확실히 더 번다"는 연봉만의 프리미엄.
      기본값 None → 기존 호출부는 전부 하위호환(프리미엄 없음).
    year: [2026-07 v4 신설] 게임 내 현재 연도 — economy_index(year)로
      시대별 배율을 곱한다. None이면(하위호환) 1.0으로 취급.
    """
    eidx = economy_index(year) if year is not None else 1.0
    from constants import (LOWER_LEAGUE_SALARY_OVERRIDE, SPECIAL_SALARY_COUNTRIES,
                           get_league_grade, SALARY_CURVE_OVERRIDE, salary_curve_value,
                           COUNTRY_SALARY_CAP)

    def _apply_prestige(sal):
        if team_name and country:
            from data.prestige_clubs import is_prestige, PRESTIGE_SALARY_MULT, PRESTIGE_SALARY_MULT_DEFAULT
            if is_prestige(country, tier, team_name):
                mult = PRESTIGE_SALARY_MULT.get(country, PRESTIGE_SALARY_MULT_DEFAULT)
                return int(sal * mult)
        return sal

    # [양극화 리그 특례] tier1 + 앵커커브 적용국은 base_year/mult 대신
    # (하위권 OVR→하위권 연봉)~(월드클래스 OVR→최고연봉) 지수보간 곡선을 그대로 사용.
    # 국대등급(grade)과 무관하게 국가명 자체로 판정하므로 SPECIAL 여부와도 독립적.
    if tier == 1 and country in SALARY_CURVE_OVERRIDE:
        sal = salary_curve_value(country, ovr)
        cap = COUNTRY_SALARY_CAP.get(country, 0)
        if cap > 0:
            sal = min(sal, cap)
        return max(0, int(_apply_prestige(sal) * eidx))

    is_special = country and country in SPECIAL_SALARY_COUNTRIES
    if country:
        if is_special:
            wealth = SPECIAL_SALARY_COUNTRIES[country]
        else:
            wealth = get_league_grade(country, grade)
    else:
        wealth = grade


    base_year = {
        # 천원/년. SS 1부 기준, 각 등급 OVR65 평균 주전 목표 연봉으로 역산된 base.
        # 나라별 실제 연봉은 COUNTRY_SALARY_MULT로 조정.
        # tier 비율: 1부=1.0 / 2부≈0.316 / 3부 이하는 LOWER_LEAGUE_OVERRIDE로 관리.
        "SS":{1:19_996_093, 2:6_318_572, 3:2_025_506, 4:1_650_731, 5:1_324_913},
        "S": {1:11_603_489, 2:3_666_703, 3:1_180_000, 4:   77_216, 5:   61_990},
        "A": {1: 2_977_645, 2:  941_397, 3:   45_853, 4:   26_498},
        "B": {1: 1_020_507, 2:  322_480, 3:   19_445, 4:    9_266},
        "C": {1:   562_640, 2:  177_794, 3:    9_936, 4:    6_573},
        "D": {1:    15_425, 2:    4_874, 3:    3_122},
        "E": {1:     6_076, 2:    1_920, 3:    1_234},
        "F": {1:     5_560, 2:    1_757, 3:      605},
    }
    # 등급별 연봉 상한 (천원/년) — 나라별 COUNTRY_SALARY_CAP이 실제 상한 역할.
    # 이 값은 COUNTRY_SALARY_CAP 없는 나라의 최종 안전망.
    # [2026-07] _clamp_salary_to_cap과 값이 어긋나지 않도록 공용 테이블 사용.
    _salary_cap = _salary_cap_table()
    b = base_year.get(wealth, {}).get(tier, 100)

    # 나라×tier 오버라이드 (2부 이하)
    # [버그수정] LOWER_LEAGUE_SALARY_OVERRIDE는 이미 나라별 절대 base값이므로
    #   override 사용 시 cont_mult를 적용하지 않는다.
    #   (기존: override에도 cont_mult 재적용 → K3 의도 150만이 31만으로 축소되는 버그)
    # [버그수정] 원래 tier>=3만 override 대상이라 2부는 항상 base_year×cont_mult
    #   수식으로만 계산됐다. cont_mult가 아주 작은 나라(이란/세네갈/모로코/
    #   스웨덴/덴마크 등)는 이 수식값이 override로 지정된 3부 절대값보다도
    #   낮아져 "2부가 3부보다 싼" 역전이 발생했다. tier==2도 override 대상에
    #   포함시켜, 해당 국가엔 3부보다 확실히 높은 2부 절대값을 지정해 둔다.
    _used_override = False
    if country and tier >= 2:
        _ov = LOWER_LEAGUE_SALARY_OVERRIDE.get(country, {})\
            if not is_special else {}
        if tier in _ov:
            b = _ov[tier]
            _used_override = True

    if b == 0:
        return 0

    # 나라별 연봉 배율: override를 사용하지 않은 경우에만 적용
    # (override는 이미 나라별 절대값 — cont_mult 중복 적용 방지)
    # [2026-07 버그 수정] 나라별 개별 배율(COUNTRY_SALARY_MULT)이 없는
    # 나라는 전부 1.0(=유럽과 동일 재정)으로 처리되고 있었다 — 대륙별
    # 배율(CONTINENT_SALARY_MULT)이 정의만 되고 실제로는 어디서도 안 쓰였기
    # 때문. get_country_salary_mult()가 그 폴백을 실제로 적용한다(나라별
    # 지정 → 없으면 대륙별 배율 → 그것도 없으면 1.0).
    if not is_special and country and not _used_override:
        from constants import get_country_salary_mult
        cont_mult = get_country_salary_mult(country)
        b = int(b * cont_mult)

    if b == 0:
        return 0

    # [2026-07 v4 버그수정, 신민용 지적: "D~F급은 OVR53이든 63이든 경제적
    # 가치가 거의 같다 — 선수 성장→가치 상승 루프가 끊긴다"] D/E/F급은
    # base_year 자체가 워낙 작은데(예: D급 15,425천원) 여기에 국가 배율
    # (많은 나라가 0.01~0.08 수준)까지 곱하면 b가 거의 0에 수렴해서,
    # `_salary_ovr_mult` 곡선이 아무리 정교해도 최종값이 커질 여지가
    # 없었다(곱셈이라 base가 작으면 커브 모양은 그대로여도 절대값이
    # 전부 눌린다). 바닥값을 OVR 비례로 바꾼 것만으론 부족했던 이유가
    # 이거다 — tier1 한정으로 등급별 "유효 최소 base"를 둬서 국가 배율이
    # 아무리 작아도 커브가 실제로 펼쳐질 최소한의 여지를 보장한다.
    # (D=40,000천원 → OVR53~63 연봉 0.10~0.26억, 신민용이 제시한 목표치
    # 0.08~0.30억과 근접하도록 역산 — E/F는 기존 base_year 비율(D 대비
    # E≈0.39배, F≈0.36배)을 그대로 유지해 등급 간 순서는 보존한다.)
    if tier == 1 and wealth in _LOW_GRADE_SCALE_BOOST:
        b = int(b * _LOW_GRADE_SCALE_BOOST[wealth])

    sal = int(b * _salary_ovr_adj(ovr, wealth, tier))
    if wealth == "F" and tier >= 3 and ovr < 38:
        return 0
    # [버그수정] 최저임금 바닥값이 예전엔 tier>=4에 고정 50으로만 적용돼,
    #   3부 계산값이 50 미만인 저평가 국가(말라위/볼리비아 등)에서
    #   "3부<4부" 역전이 났다. 그렇다고 3부도 그냥 50으로 맞추면 이번엔
    #   2부 계산값이 그 50보다 낮은 국가(말레이시아/태국/불가리아 등 저배율국)
    #   에서 "2부<3부(바닥50)" 역전이 새로 생긴다.
    #   → 바닥값 자체를 티어가 낮을수록(숫자가 작을수록) 커지도록 계단식으로
    #     주어 바닥값끼리도 항상 2부>3부>4부>5부 순서가 유지되게 한다.
    # [2026-07 v4 버그수정, 신민용 지적: "E/F급은 OVR33이든 53이든 전부
    # 15만원으로 똑같이 눌린다 — 이건 가난한 리그를 표현한 게 아니라
    # 연봉 함수가 계단화된 것"] 고정 바닥값(예: tier1=150천원)에 걸리면
    # OVR 정보가 통째로 사라졌다 — E/F급은 base_year 자체가 워낙 작아서
    # 거의 항상 이 바닥에 눌렸다. 바닥값을 고정치가 아니라 OVR에 비례하는
    # 값으로 바꾼다(기준 OVR=50 — 이 근방에서 기존 고정 바닥값과 거의
    # 같아지도록 캘리브레이션돼 있어 기존 밸런스와 자연스럽게 이어짐).
    # 티어 간 순서(2부>3부>4부>5부)는 모든 티어에 같은 비율을 곱하므로
    # 그대로 유지된다.
    _floor_by_tier = {1: 150, 2: 110, 3: 80, 4: 60, 5: 50}
    _floor_ref_ovr = 50
    _floor_base = _floor_by_tier.get(tier, 0)
    if _floor_base and b > 0:
        _floor = max(1, int(_floor_base * (ovr / _floor_ref_ovr)))
        if sal < _floor:
            sal = _floor

    # [2026-07 v5 재설계, 신민용+GPT 여러 차례 검토 후 확정: "Floor를
    # max(raw, floor)로 하면 F급처럼 raw가 워낙 작은 등급에서 국가/OVR
    # 차이가 전부 사라진다 — floor를 max가 아니라 가산(+)으로 바꿔야
    # 최저생계는 보장하면서 차이도 살아남는다"] E/F급 tier1에 한해
    # "최저생활보장액"을 max가 아니라 덧셈으로 준다. D급은 이미 기존
    # _LOW_GRADE_SCALE_BOOST만으로 충분히 자연스러운 값이 나오고 있어서
    # (신민용 확인: "슬로바키아는 이미 괜찮다, 괜히 건드리지 마라")
    # 건드리지 않는다 — E/F만 대상.
    _livelihood_addend = {"E": 5_000, "F": 2_000}  # 천원, economy_index 적용 전 기준
    if tier == 1 and wealth in _livelihood_addend:
        sal = sal + _livelihood_addend[wealth]

    # 등급별 연봉 상한 적용
    # [버그수정 2026-07, 신민용 지적: "프랑스뿐 아니라 다른 나라도 다
    # 문제 있을 것 같다"] 실측으로 전체 97개국 스캔해보니, COUNTRY_SALARY_CAP이
    # 없어서 이 범용 등급 캡(_salary_cap[wealth])으로 떨어지는 나라는 전부
    # (68개 국가×OVR 조합 확인) 1부·2부가 OVR95~100에서 완전히 같은 값으로
    # 뭉개지고 있었다 — 이 캡도 tier를 구분 안 했기 때문. 국가별 캡과 동일한
    # 비율로 tier 스케일을 적용한다.
    cap = _tier_scaled_country_cap(_salary_cap.get(wealth, 0), tier)
    if cap > 0:
        sal = min(sal, int(cap * _cap_relief_mult(ovr)))
    # [버그수정] 나라별 연봉 상한 적용 (COUNTRY_SALARY_CAP)
    #   constants.py에 정의돼 있었으나 _calc_salary에서 import/적용이 누락됐었음.
    # [버그수정 2026-07, 신민용 지적: "K1이랑 K2가 둘 다 30억으로 고정"]
    #   이 캡이 tier를 구분 안 해서, LOWER_TIER_SALARY_CAP이 따로 없는
    #   나라(대한민국 등 대부분)는 1부든 2부든 OVR만 높으면 똑같은 국가
    #   상한에 눌렸다. tier별로 비율을 낮춰 적용한다.
    # [버그수정 2026-07 #2, 신민용 지적: "브라질이 OVR88 이상 전부 30억으로
    #   고정 — 네이마르급도 그냥 준수한 선수랑 똑같아진다"] 이 캡도 OVR을
    #   전혀 구분 안 해서, 실측 앵커 곡선이 없는 나라(6개국 제외 전부)는
    #   엘리트 이상 구간이 통째로 평평해지고 있었다. _cap_relief_mult로
    #   엘리트(81~94)·월드클래스(95+) 구간만 상한을 단계적으로 풀어준다.
    # [2026-07 v4 버그수정] D/E/F급은 새로 넣은 _LOW_GRADE_SCALE_BOOST가
    # 이미 그 등급에 맞는 스케일을 보장하는데, 여기에 옛날에(base가 훨씬
    # 작았을 때) 잡아둔 국가별 소액 캡(예: 우간다 12,000천원=0.12억)까지
    # 겹치면 방금 살려낸 OVR 곡선이 다시 그 캡에서 눌려버린다 — D/E/F는
    # 이 국가별 캡 적용에서 제외한다(등급 캡 자체는 그대로 유지, 스케일
    # 제어는 최소 base 쪽으로 일원화).
    if country and not is_special and wealth not in ("D", "E", "F"):
        from constants import COUNTRY_SALARY_CAP
        country_cap = COUNTRY_SALARY_CAP.get(country, 0)
        if country_cap > 0:
            sal = min(sal, int(_tier_scaled_country_cap(country_cap, tier) * _cap_relief_mult(ovr)))
    # [버그수정] 양극화 리그(SALARY_CURVE_OVERRIDE 적용국)는 tier1만 재계산돼서
    #   COUNTRY_SALARY_CAP이 tier1 기준 안전망(예: 잉글랜드 550억)으로 상향됐다.
    #   그 캡이 2부 이하에도 그대로 적용되면 "1부보다 2부가 더 비싼" 역전이
    #   생기므로, tier>=2는 별도의 낮은 캡(LOWER_TIER_SALARY_CAP)으로 다시 누른다.
    if country and tier >= 2:
        from constants import LOWER_TIER_SALARY_CAP
        # [버그수정 2026-07] 예전엔 나라별 flat 값 하나를 tier 2~5 전부에
        # 똑같이 적용해서, 2부(챔피언십급)와 5부(세미프로급)가 동일한
        # 상한을 받았다 — 이제 tier별로 나뉜 값을 쓰고, 5부보다 더 깊은
        # tier(예: 6부)가 있으면 가장 낮은(가장 아래 tier) 값으로 폴백한다.
        _lt = LOWER_TIER_SALARY_CAP.get(country, {})
        if _lt:
            lt_cap = _lt.get(tier, _lt[max(_lt.keys())])
            if lt_cap > 0:
                sal = min(sal, lt_cap)
    return max(0, int(_apply_prestige(sal) * eidx))


# ══════════════════════════════════════════════════════════════
# 이적 협상 시스템 (2026-07 신설, 신민용+GPT 다회 설계 확정)
# ══════════════════════════════════════════════════════════════
# 오퍼가 뜨면 무조건 이적 가능했던 예전 구조 대신, 3단계 판단을 거친다:
#   1) 강제판매 체크 — 제안액이 시장가 대비 압도적이면 구단도 못 막는다.
#   2) 최소수용금액 체크 — 구단이 팔고 싶어하는 최소 조건(가산식+clamp).
#   3) 거절 누적 — 반복 거절되면 선수 불만이 쌓여 다음 최소수용금액이
#      완화된다(3시즌 지나면 자연 감쇠).
# 오퍼 자체의 제안액도 시장가 그대로가 아니라 프리미엄(매수팀-내팀 등급
# 격차 기반 랜덤)이 붙는다 — 안 그러면 강제판매가 발동할 상황 자체가
# 생기지 않는다(제안액이 항상 시장가와 정확히 같았으므로).

LEAGUE_GRADE_RANK = {"F": 1, "E": 2, "D": 3, "C": 4, "B": 5, "A": 6, "S": 7, "SS": 8}


def offer_premium_mult(buyer_grade: str, my_grade: str) -> float:
    """매수팀 등급이 내 팀보다 얼마나 높은지(gap)에 따라 오퍼 제안액에
    붙는 프리미엄 배율. 같은 급끼리는 정상 협상 범위(0.9~1.1)에 머물고,
    격차가 3단계 이상 벌어지면(예: D급 선수에게 SS급 팀이 관심) 낮은
    확률로 훨씬 큰 프리미엄이 튄다 — "하위 리그 원석을 빅클럽이 묻지마
    영입"하는 드문 케이스를 표현한다."""
    import random
    gap = LEAGUE_GRADE_RANK.get(buyer_grade, 4) - LEAGUE_GRADE_RANK.get(my_grade, 4)
    if gap <= 0:
        return random.uniform(0.9, 1.1)
    if gap <= 2:
        return random.uniform(1.0, 1.5)
    roll = random.random()
    if roll < 0.02:
        return random.uniform(2.0, 5.0)
    if roll < 0.10:
        return random.uniform(1.2, 2.0)
    return random.uniform(1.0, 1.3)


# 등급별 강제판매 기본 배수 — "이 정도 배수면 아무리 안 팔고 싶어도
# 어쩔 수 없다"는 기준. 빅리그일수록 자금 여유가 있어 쉽게 안 팔고
# (배수가 큼), 하위 리그일수록 구단 규모상 거액을 거절하기 어렵다
# (배수가 작음).
FORCED_SALE_BASE_MULT = {
    "SS": 2.75, "S": 2.5, "A": 2.2, "B": 2.0, "C": 1.8, "D": 1.5, "E": 1.4, "F": 1.4,
}


def forced_sale_threshold_mult(buyer_grade: str, my_grade: str) -> float:
    """강제판매 기준 배수 — 시장가의 몇 배 이상 제안하면 구단이 거절
    못 하는지. 기본값은 파는 쪽(my_grade) 리그 등급으로 정하고, 사는
    쪽(buyer_grade)이 훨씬 강한 리그면(격차 3단계 이상) 기준을 낮춰서
    (더 쉽게 발동) "명문팀 제안은 그 자체로 거절하기 어렵다"를 반영—
    반대로 사는 쪽이 같거나 약한 리그면 기준을 높여서(더 어렵게 발동)
    "동급/하위 리그의 거액 제안은 의심스러워 쉽게 안 넘어간다"를 반영."""
    base = FORCED_SALE_BASE_MULT.get(my_grade, 2.0)
    gap = LEAGUE_GRADE_RANK.get(buyer_grade, 4) - LEAGUE_GRADE_RANK.get(my_grade, 4)
    if gap >= 3:
        adj = 0.8
    elif gap <= -1:
        adj = 1.2
    else:
        adj = 1.0
    return base * adj