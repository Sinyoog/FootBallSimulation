# promotion_playoff.py — 승강 플레이오프 규칙 스키마 + Validator
#
# [2026-07 신설, 신민용 설계] 캘린더(constants.py의 PLAYOFF_WEEK 등)는 이미
# 확정됐고, 이 파일은 그 다음 단계 — "어떤 팀들이 어떤 대진으로 붙는가"를
# 데이터로 선언하는 스키마와, 그 데이터가 실제로 말이 되는지 게임 시작
# 시점에 한 번 검사하는 Validator다.
#
# 여기서는 아직 실제 실행기(셸 생성 → 매일 진행 → 결과 반영)를 만들지
# 않는다 — 그건 다음 단계(PromotionPlayoffEngine)의 몫이다. 이 파일이
# 정의하는 건 "정적인 데이터"뿐이고, 이후 모든 코드는 이 데이터를
# 읽기만 하면 된다.
#
# ── 설계 배경 (요약) ──────────────────────────────────────────────
# · 승강 경계 하나당 총 이동 인원/자동/PO 자리 수는
#   game_engine._get_promotion_policy(team_count)로 이미 결정된다
#   (6~15팀=2/1/1, 16~24팀=3/2/1, 25~26팀=4/3/1, 27팀+=4/2/2) — 그리고
#   승격 인원과 강등 인원은 항상 같아야 한다(리그 전체 팀 수 보존).
# · PO는 이 총 이동 중 마지막 po자리만 담당하고, 나머지(자동)는 그대로
#   자동 승강이다. po_count는 룰마다 고정값(0/1/2)이라, 실제 순위(rank)는
#   "그 리그의 team_count/total/po로부터 상대적으로" 계산돼야 한다 — 20팀
#   리그의 17위와 24팀 리그의 17위는 자동존/PO존 경계상
#   전혀 다른 의미이기 때문에, 규칙 자체엔 절대 순위를 못 박지 않는다.
# · intl_engine의 대륙컵과 동일하게, 하나의 PLAYOFF_RULE 템플릿은 나라마다
#   ·승강 경계마다 독립적으로 여러 번 인스턴스화된다. 그래서 규칙 안의
#   match id/참조는 전부 "그 템플릿 안에서의 상대 id"이지 실제 DB row id가
#   아니다 (실제 DB row로 바뀌는 건 다음 단계 실행기의 역할).


# ── source.type 스키마 ────────────────────────────────────────────
# 현재 두 가지만 지원한다. LOSER는 의도적으로 미지원(YAGNI) — 필요해지면
# source.type에 "loser"만 추가하면 되고, 기존 데이터/검증기는 안 깨진다.
#
#   {"type": "standing", "side": "upper"|"lower", "offset": int}
#       side="upper": 이 경계의 위쪽 리그(자동강등 되지 않고 남은 팀들 중
#           자동잔류 구간과 가장 가까운 순서로 offset번째, 0-based).
#       side="lower": 이 경계의 아래쪽 리그(자동승격 구간 바로 다음 순서로
#           offset번째, 0-based).
#       실제 순위(rank)로 바뀌는 계산은 resolve_standing_rank() 참고 —
#           규칙 정의 시점엔 team_count를 모르므로 여기선 offset만 쓴다.
#   {"type": "winner", "match": "<이 템플릿 안의 다른 match id>"}
#       그 경기의 승자. 반드시 "이전에 정의된" match를 가리켜야 한다
#       (미래 참조/자기참조 금지 — Validator가 검사).


def resolve_standing_rank(side: str, offset: int, team_count: int, auto_count: int, po_slots: int) -> int:
    """PLAYOFF_RULE의 {"type":"standing", side, offset}를 실제 순위(1=1위)로
    변환한다. 규칙 자체엔 절대 순위가 없고, 인스턴스화(실제 리그에
    적용) 시점에만 이 함수로 계산한다.

    [2026-07 재설계] po_slots(그 side에서 PO 후보로 예약된 자리 수)를
    양쪽이 공유하는 하나의 값(po_count)이 아니라 side별로 따로 받는다 —
    상위 리그는 항상 1자리("상위 리그는 항상 1팀만 위태")인데 아래 리그는
    2자리 또는 4자리(리그 크기별 브래킷)라, 더 이상 대칭이 아니다.
    auto_count(자동 이동 인원)는 반드시 양쪽에 같은 값이 들어와야 한다
    (그래야 리그 전체 팀 수가 보존됨 — game_engine._get_promotion_policy
    참고, 위 리그 크기 하나로만 결정해서 호출부가 양쪽에 동일하게 넘김).

    upper(위 리그): 자동강등 구간 바로 위, offset=0이 자동존에 가장 가까운
        (=가장 절박한) 팀. rank = team_count - auto_count - po_slots + 1 + offset
    lower(아래 리그): 자동승격 구간 바로 아래, offset=0이 자동존에 가장
        가까운(=가장 유리한, 순위표 기준 상위) 팀. rank = auto_count + 1 + offset
    """
    if side == "upper":
        return team_count - auto_count - po_slots + 1 + offset
    elif side == "lower":
        return auto_count + 1 + offset
    raise ValueError(f"알 수 없는 side: {side!r} (upper/lower만 허용)")


# ── PLAYOFF_RULES: 프리셋 템플릿 ───────────────────────────────────
# Generator가 아니라 템플릿(하드코딩)이다 — "리그 크기가 같다고 PO 방식도
# 같아야 하는 건 아니다"(챔피언십/독일2부/벨기에가 전부 18~20팀인데
# 방식이 다 다른 것과 동일한 이유). 리그 크기 → 기본 룰 매핑은
# DEFAULT_POLICY가 정책으로 담당하고, 개별 리그는 이 정책을 override할 수
# 있다(override 필드는 다음 단계에서 leagues 테이블에 추가).
PLAYOFF_RULES = {
    "bracket2": {
        "description": ("PO 1자리, 아래 리그 2팀 미니 예선(작은 리그, 8~10팀 규모) — "
                         "아래 2팀이 붙어 이긴 팀이 위 리그의 유일한 PO 대상과 최종 승강전."),
        "po_count": 1,
        "matches": [
            {
                "id": "Q1",
                "home": {"type": "standing", "side": "lower", "offset": 0},
                "away": {"type": "standing", "side": "lower", "offset": 1},
            },
            {
                "id": "F",
                "home": {"type": "standing", "side": "upper", "offset": 0},
                "away": {"type": "winner", "match": "Q1"},
            },
        ],
    },
    "bracket4": {
        "description": ("PO 1자리, 아래 리그 4팀 미니 토너먼트(12팀+ 일반적인 리그 규모) — "
                         "1v4·2v3 준결승 → 결승으로 아래 리그 대표 1팀을 뽑고, 위 리그의 "
                         "유일한 PO 대상과 최종 승강전. 실제 EFL 챔피언십 플레이오프와 동일 구조."),
        "po_count": 1,
        "matches": [
            {
                "id": "SF1",
                "home": {"type": "standing", "side": "lower", "offset": 0},
                "away": {"type": "standing", "side": "lower", "offset": 3},
            },
            {
                "id": "SF2",
                "home": {"type": "standing", "side": "lower", "offset": 1},
                "away": {"type": "standing", "side": "lower", "offset": 2},
            },
            {
                "id": "LF",
                "home": {"type": "winner", "match": "SF1"},
                "away": {"type": "winner", "match": "SF2"},
            },
            {
                "id": "F",
                "home": {"type": "standing", "side": "upper", "offset": 0},
                "away": {"type": "winner", "match": "LF"},
            },
        ],
    },
}

# 아래 리그 PO 브래킷 크기(2 또는 4) → 룰. bracket_size는
# game_engine._get_po_bracket_size(lower_team_count)가 결정한다.
#
# [2026-07 재설계, 신민용 최종안] "위 리그는 항상 1팀만 위태, 아래 리그는
# 리그 크기에 따라 2팀 또는 4팀이 마지막 승격 티켓을 놓고 경쟁"으로
# 구조를 통일했다 — po_count는 이제 룰과 무관하게 항상 1(boundary 경기가
# 정확히 1개)이라 더 이상 룰 선택 기준이 될 수 없고, 대신 브래킷 크기로
# 고른다.
RULE_BY_BRACKET_SIZE = {
    2: "bracket2",
    4: "bracket4",
}


def get_rule_id_for_bracket(bracket_size: int):
    """bracket_size(2 또는 4)로 PLAYOFF_RULE id를 고른다. 정의되지 않은
    크기가 들어오면 안전하게 4팀(bracket4)로 취급한다."""
    return RULE_BY_BRACKET_SIZE.get(bracket_size, "bracket4")


# ── Validator ──────────────────────────────────────────────────────
class PlayoffRuleError(ValueError):
    """PLAYOFF_RULE 정의 자체가 구조적으로 잘못됐을 때(로드 시점 검증 실패)."""


def _resolve_origin_and_rounds(rule_id: str, rule: dict):
    """DAG를 순회하며 각 match의 origin_side("upper"/"lower"/"boundary")와
    round(위상 정렬 깊이 — 0=1라운드, 1=2라운드...)를 계산한다.
    동시에 아래를 검사한다:
      · match id 중복
      · winner 참조가 존재하는 match를 가리키는지, 미래/자기참조는 아닌지
      · 순환(cycle) 없는 DAG인지
      · source.type이 유효한지(standing/winner만)
      · boundary(양쪽 기원이 다른) match의 승자를 또 다른 match가
        참조하지 않는지 — 이걸 허용하면 "이 경기가 최종 승강 결정인지
        아닌지"가 애매해져서, 검증 시점에 upper/lower 대칭 인원을 셀 수
        없어진다(바로 이 규칙이, 예전에 논의했던 "6팀 참가 체인형 B안"
        예시가 실제로는 깨져 있었다는 걸 잡아낸 지점이다 — 그 예시는
        1부27과 1부28을 같은 체인 안에서 순차로 묶었는데, 그러면 최종
        승격 인원이 1명인데 강등 위기 인원은 2명이 되어 버려 대칭이
        깨진다).
    """
    matches = rule.get("matches")
    if not matches:
        raise PlayoffRuleError(f"[{rule_id}] matches가 비어있습니다.")

    by_id = {}
    for m in matches:
        mid = m.get("id")
        if not mid:
            raise PlayoffRuleError(f"[{rule_id}] id 없는 match가 있습니다: {m}")
        if mid in by_id:
            raise PlayoffRuleError(f"[{rule_id}] match id 중복: {mid!r}")
        by_id[mid] = m

    order = list(by_id.keys())  # 선언 순서 = "미래 참조 금지" 판정 기준
    declared_before = {}
    seen = set()
    for mid in order:
        declared_before[mid] = set(seen)
        seen.add(mid)

    referenced_by = {}   # match id -> 그 승자를 참조하는 match id 목록
    origin_cache = {}
    round_cache = {}

    def _validate_source(mid, src, field):
        if not isinstance(src, dict) or "type" not in src:
            raise PlayoffRuleError(f"[{rule_id}] {mid}.{field}: source.type이 없습니다: {src}")
        stype = src["type"]
        if stype == "standing":
            if src.get("side") not in ("upper", "lower"):
                raise PlayoffRuleError(f"[{rule_id}] {mid}.{field}: standing.side는 upper/lower만 허용 ({src})")
            if not isinstance(src.get("offset"), int) or src["offset"] < 0:
                raise PlayoffRuleError(f"[{rule_id}] {mid}.{field}: standing.offset은 0 이상 정수 ({src})")
            return ("standing", src["side"], None)
        elif stype == "winner":
            ref = src.get("match")
            if ref not in by_id:
                raise PlayoffRuleError(f"[{rule_id}] {mid}.{field}: winner가 존재하지 않는 match를 참조: {ref!r}")
            if ref not in declared_before[mid]:
                raise PlayoffRuleError(
                    f"[{rule_id}] {mid}.{field}: winner가 자기 자신이거나 아직 선언 안 된(미래) match를 "
                    f"참조합니다({ref!r}) — match는 반드시 참조 대상보다 뒤에 선언돼야 합니다(순환 방지).")
            return ("winner", None, ref)
        raise PlayoffRuleError(f"[{rule_id}] {mid}.{field}: 알 수 없는 source.type: {stype!r} (standing/winner만 지원)")

    def _resolve(mid, _stack=None):
        """재귀적으로 origin_side/round 계산. _stack은 순환 탐지용."""
        if mid in origin_cache:
            return origin_cache[mid], round_cache[mid]
        _stack = _stack or set()
        if mid in _stack:
            raise PlayoffRuleError(f"[{rule_id}] match 참조에 순환이 있습니다: {mid!r}")
        _stack = _stack | {mid}

        m = by_id[mid]
        sides = []
        depths = []
        for field in ("home", "away"):
            src = m.get(field)
            if src is None:
                raise PlayoffRuleError(f"[{rule_id}] {mid}.{field}가 없습니다.")
            kind, side, ref = _validate_source(mid, src, field)
            if kind == "standing":
                sides.append(side)
                depths.append(0)
            else:
                ref_origin, ref_round = _resolve(ref, _stack)
                if ref_origin == "boundary":
                    raise PlayoffRuleError(
                        f"[{rule_id}] {mid}: match {ref!r}는 이미 승강을 결정짓는 boundary 경기입니다 — "
                        f"boundary 경기의 승자를 또 다른 경기에 넣을 수 없습니다(체인 금지, 대칭 계산이 "
                        f"불가능해짐). boundary가 아닌 같은 쪽(위/아래) 경기의 승자만 다음 라운드에 연결할 "
                        f"수 있습니다.")
                referenced_by.setdefault(ref, []).append(mid)
                sides.append(ref_origin)
                depths.append(ref_round + 1)

        origin = sides[0] if sides[0] == sides[1] else "boundary"
        rnd = max(depths)
        origin_cache[mid] = origin
        round_cache[mid] = rnd
        return origin, rnd

    for mid in order:
        _resolve(mid)

    return by_id, order, origin_cache, round_cache, referenced_by


def validate_playoff_rule(rule_id: str, rule: dict) -> None:
    """PLAYOFF_RULE 하나를 구조적으로 검증한다. 문제 있으면 PlayoffRuleError.
    체크리스트 (신민용 최종안 기준):
      ✓ Match ID 중복            ✓ Winner 참조 존재/미래참조 금지
      ✓ DAG 순환 검사             ✓ source.type 유효성
      ✓ 도달 불가능한 match 없음   ✓ 시작 match 존재
      ✓ upper/lower 참가수 대칭   ✓ boundary 경기 체이닝 금지(위 함수 내)
    standing.offset이 실제 리그 크기 범위 안인지 / 자동승강 구간과 안
    겹치는지는 여기서 못 본다(team_count가 그때 가서야 정해지므로) —
    그건 인스턴스화 시점 검증(validate_instance, 다음 단계 실행기에서
    구현)의 몫이다.
    """
    if "po_count" not in rule:
        raise PlayoffRuleError(f"[{rule_id}] po_count가 없습니다.")
    po_count = rule["po_count"]

    by_id, order, origin, rounds, referenced_by = _resolve_origin_and_rounds(rule_id, rule)

    # 시작 match 존재(=standing 소스만으로 이뤄진 1라운드 match가 최소 1개)
    if not any(rounds[mid] == 0 for mid in order):
        raise PlayoffRuleError(f"[{rule_id}] 1라운드(모든 입력이 standing인) match가 하나도 없습니다.")

    # 도달 불가능한 match 없음: 모든 match는 (a) 최종 boundary 경기이거나
    # (b) 다른 match에서 winner로 참조돼야 한다. 둘 다 아니면 "만들어놓고
    # 아무도 안 쓰는" 죽은 match.
    terminal = [mid for mid in order if mid not in referenced_by]
    for mid in order:
        if mid not in referenced_by and origin[mid] != "boundary":
            raise PlayoffRuleError(
                f"[{rule_id}] match {mid!r}는 같은 쪽(위/아래) 경기인데 아무 경기에서도 승자를 참조하지 "
                f"않습니다 — 결과가 최종 승강 결정에 아예 반영되지 않는 '죽은 경기'입니다.")

    # 모든 terminal match가 boundary(승강 결정)여야 함
    for mid in terminal:
        if origin[mid] != "boundary":
            raise PlayoffRuleError(
                f"[{rule_id}] match {mid!r}는 아무 데도 참조되지 않는 마지막 경기인데 같은 쪽끼리의 경기라 "
                f"승강을 결정짓지 않습니다 — 마지막 경기는 반드시 upper vs lower(boundary)여야 합니다.")

    boundary_matches = [mid for mid in order if origin[mid] == "boundary"]

    # upper/lower 참가수 대칭: boundary 경기 수 = po_count여야 하고,
    # 그게 곧 위/아래 각각 최종적으로 이동(또는 잔류 결정)하는 인원이다
    # (boundary 경기 하나당 정확히 위쪽 대표 1 vs 아래쪽 대표 1이 이미
    # _resolve_origin_and_rounds에서 보장됨 — origin이 "boundary"라는 게
    # 바로 그 뜻).
    if len(boundary_matches) != po_count:
        raise PlayoffRuleError(
            f"[{rule_id}] po_count={po_count}인데 실제 boundary(승강 결정) 경기는 "
            f"{len(boundary_matches)}개({boundary_matches})입니다 — 선언한 자리 수와 실제 브래킷이 "
            f"안 맞습니다.")


def validate_all_playoff_rules() -> None:
    """게임 시작 시 1회 호출. PLAYOFF_RULES 전체와 RULE_BY_BRACKET_SIZE 참조
    무결성을 검사한다 — 잘못된 템플릿은 여기서 바로 예외로 잡힌다(런타임에
    특정 리그가 그 룰을 실제로 쓰는 순간에야 터지는 것보다 훨씬 싸다)."""
    for rule_id, rule in PLAYOFF_RULES.items():
        validate_playoff_rule(rule_id, rule)

    for bracket_size, rule_id in RULE_BY_BRACKET_SIZE.items():
        if rule_id is not None and rule_id not in PLAYOFF_RULES:
            raise PlayoffRuleError(
                f"RULE_BY_BRACKET_SIZE[{bracket_size}]가 존재하지 않는 룰 {rule_id!r}을 가리킵니다.")