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


def _base_market_value_eok(ovr: int) -> float:
    """OVR → 기본 시장가치(억원), 연봉과 완전히 독립된 축.
    [2026-07 재설계, 신민용 지적: "연봉×배수 구조면 사우디처럼 연봉만 높은
    리그의 시장가치까지 같이 폭증한다 — 호날두(연봉 높음/시장가치 낮음)
    vs 야말(연봉 낮음/시장가치 높음)처럼 둘은 다른 축이어야 한다"]
    실력만으로 결정되는 기본가치를 구간별 앵커 포인트로 고정하고, 구간
    안에서는 지수보간한다. 리그 재정력은 이 값에 ±20~30%만 얹는다
    (아래 _market_value_league_mult).

    앵커(OVR, 억원): 60→0.1, 70→3, 75→8, 80→20, 85→60, 90→350,
                     93→900, 96→1800, 99→3000 (역대급 월드클래스 상한권)
    """
    anchors = [
        (40, 0.02), (60, 0.1), (70, 3), (75, 8), (80, 20),
        (85, 45), (88, 85), (90, 130), (93, 400), (96, 1200), (99, 2600),
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
MARKET_VALUE_GRADE_MULT = {
    "SS": 1.20, "S": 1.05, "A": 0.90, "B": 0.70,
    "C": 0.50, "D": 0.35, "E": 0.25, "F": 0.15,
}
MARKET_VALUE_COUNTRY_MULT = {
    # 오일머니 리그: 연봉으로 스타를 영입하지만(SPECIAL_SALARY_COUNTRIES),
    # 유럽 빅리그 대비 커리어 정점/이적 유동성이 낮게 평가돼 시장가치는
    # 오히려 등급 대비 역보정된다.
    "사우디아라비아": 0.55,
    "카타르":        0.55,
    "아랍에미리트":  0.55,
    "브라질":        0.95,  # 실력은 S급이나 리그 잔류 유인이 약해 소폭 하향
}


def _market_value_league_mult(grade: str, country: str = None) -> float:
    base = MARKET_VALUE_GRADE_MULT.get(grade, 0.5)
    if country and country in MARKET_VALUE_COUNTRY_MULT:
        return MARKET_VALUE_COUNTRY_MULT[country]
    return base


def estimate_transfer_fee(grade, tier, ovr, country=None, team_name=None) -> int:
    """선수 시장가치(이적료 추정치, 천원 단위). [2026-07 재설계] 연봉에서
    파생시키지 않고 OVR(기본가치) × 리그재정력(20~30%만 반영)으로 독립
    계산한다 — 연봉이 높다고 시장가치까지 비례해서 튀는 문제(사우디 등)를
    구조적으로 차단한다. tier가 1이 아니면(2부 이하) 추가로 감가한다.
    """
    base_eok = _base_market_value_eok(ovr)
    mult = _market_value_league_mult(grade, country)
    val_eok = base_eok * mult
    if tier and tier >= 2:
        val_eok *= max(0.15, 0.55 ** (tier - 1))  # 2부 0.55x, 3부 0.30x ...
    return int(val_eok * 100_000)  # 억원 -> 천원 단위로 환산(_calc_salary와 단위 통일)



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


def _clamp_salary_to_cap(sal, wealth, country=None, tier=1, is_special=False):
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
    없을 때만 범용 캡을 쓰도록 순서를 맞춘다."""
    from constants import COUNTRY_SALARY_CAP, LOWER_TIER_SALARY_CAP
    country_cap = COUNTRY_SALARY_CAP.get(country, 0) if (country and not is_special) else 0
    if country_cap > 0:
        sal = min(sal, _tier_scaled_country_cap(country_cap, tier))
    else:
        cap = _tier_scaled_country_cap(_salary_cap_table().get(wealth, 0), tier)
        if cap > 0:
            sal = min(sal, cap)
    if country and not is_special and tier >= 2:
        _lt = LOWER_TIER_SALARY_CAP.get(country, {})
        if _lt:
            lt_cap = _lt.get(tier, _lt[max(_lt.keys())])
            if lt_cap > 0:
                sal = min(sal, lt_cap)
    return max(0, int(sal))


def _calc_salary(grade, tier, ovr, country=None, team_name=None):
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
    """
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
        return max(0, _apply_prestige(sal))

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
    _floor_by_tier = {1: 150, 2: 110, 3: 80, 4: 60, 5: 50}
    _floor = _floor_by_tier.get(tier, 0)
    if _floor and sal < _floor and b > 0:
        sal = _floor
    # 등급별 연봉 상한 적용
    # [버그수정 2026-07, 신민용 지적: "프랑스뿐 아니라 다른 나라도 다
    # 문제 있을 것 같다"] 실측으로 전체 97개국 스캔해보니, COUNTRY_SALARY_CAP이
    # 없어서 이 범용 등급 캡(_salary_cap[wealth])으로 떨어지는 나라는 전부
    # (68개 국가×OVR 조합 확인) 1부·2부가 OVR95~100에서 완전히 같은 값으로
    # 뭉개지고 있었다 — 이 캡도 tier를 구분 안 했기 때문. 국가별 캡과 동일한
    # 비율로 tier 스케일을 적용한다.
    cap = _tier_scaled_country_cap(_salary_cap.get(wealth, 0), tier)
    if cap > 0:
        sal = min(sal, cap)
    # [버그수정] 나라별 연봉 상한 적용 (COUNTRY_SALARY_CAP)
    #   constants.py에 정의돼 있었으나 _calc_salary에서 import/적용이 누락됐었음.
    # [버그수정 2026-07, 신민용 지적: "K1이랑 K2가 둘 다 30억으로 고정"]
    #   이 캡이 tier를 구분 안 해서, LOWER_TIER_SALARY_CAP이 따로 없는
    #   나라(대한민국 등 대부분)는 1부든 2부든 OVR만 높으면 똑같은 국가
    #   상한에 눌렸다. tier별로 비율을 낮춰 적용한다.
    if country and not is_special:
        from constants import COUNTRY_SALARY_CAP
        country_cap = COUNTRY_SALARY_CAP.get(country, 0)
        if country_cap > 0:
            sal = min(sal, _tier_scaled_country_cap(country_cap, tier))
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
    return max(0, _apply_prestige(sal))