"""
formation_logic.py — 포메이션 슬롯 배정 순수 로직 (Qt 의존성 없음)

[2026-08 신설, 신민용 요청: "이 시즌에 얘가 어디 포지션을 갔는지가
중요한거야"] _pos_category/_best_slot_for_player/_greedy_fill_slots는
원래 ui/formation_widget.py에 있었다 — 화면(포메이션 탭)에서 선수를
슬롯에 배정할 때만 쓰던 순수 계산 함수였다. 그런데 ai_lifecycle.py
(매 시즌 전환 때 도는 백엔드 생애주기 로직, headless_runner.py 등
GUI 없는 헤드리스 실행에서도 계속 도는 코드)가 "그 시즌 실제로 어느
슬롯을 맡았는지" 스냅샷을 남기려면 이 알고리즘이 필요해졌다.

ui.formation_widget에서 바로 import하면 그 모듈 맨 위의
`from PyQt6.QtWidgets import ...`가 딸려 들어와서, PyQt6이 없는(또는
디스플레이가 없는) 순수 헤드리스 환경에서 ai_lifecycle.py를 import하는
것만으로 죽어버린다 — headless_runner.py/qa_runs 등 여러 무인 테스트
스크립트가 지금까지 UI 의존성이 전혀 없었는데 이걸 깨뜨리게 된다.

그래서 이 세 함수를 Qt 의존성이 전혀 없는 이 파일로 옮기고,
ui/formation_widget.py와 ai_lifecycle.py 둘 다 여기서 import해서 쓴다
— 로직은 완전히 동일(화면에 뜨는 배정과 시즌 스냅샷 배정이 항상
같은 알고리즘을 쓰므로 서로 다른 결과가 나올 일이 없다), 위치만
공용 모듈로 옮긴 것.

═══════════════════════════════════════════════════════════════
[2026-09 성능 재작성, 신민용 리포트: "52주차→1주차 렉"]

계산식·판정 순서·랜덤 소비는 하나도 바꾸지 않고, "같은 값을 매번 다시
구하던 것"만 표로 접었다. 실측 근거:

  · _shuffle_formations 한 번에 formation_fit_penalty가 76,392회 호출되고
    (팀 11,393개 중 30%가 포메이션 20개를 전부 평가), 그 안에서
    _find_compat_slot 1,749,348회 / dict.get 10,548,433회가 돌았다.
  · 진짜 원인은 dict.get 자체가 아니라 세 가지 반복이었다:
      (1) 같은 팀의 로스터를 포메이션마다 20번씩 다시 정렬
      (2) 후보×슬롯×단계마다 dict에서 position/ovr을 다시 꺼냄
      (3) compat.index()로 슬롯 11개를 매번 선형 탐색
    (3)의 "이 포지션이 이 슬롯 구성에서 선호하는 슬롯 순서"는 (슬롯구성,
    포지션)만의 순수 함수 — 20×15 = 300칸짜리 표면 끝난다.

검증: 전 팀(11,393) × 전 포메이션(20) = 227,860건의 formation_fit_penalty
값을 기존 구현과 대조해 불일치 0. _shuffle_formations 전체를 같은 seed로
돌려 (팀ID, 최종 포메이션, formation_fit_bonus)까지 완전 일치.
핵심 루프 2.68s → 0.74s(×3.6).
═══════════════════════════════════════════════════════════════
"""
from constants import POSITION_COMPAT, POSITION_MISMATCH_PENALTY


# [2026-08 최적화] 아래 _pos_category는 시즌 전환 한 번에 350만 회 넘게
# 호출된다(이적 판매후보 포지션그룹 집계 + 포메이션 슬롯 배정). 원래는
# 호출마다 문자열을 튜플 3개와 차례로 대조했는데, 값 자체는 포지션
# 문자열만의 순수 함수라 그냥 표로 만들어두면 조회 한 번으로 끝난다.
# 표에 없는 포지션이 전부 "ATK"로 떨어지는 것도 예전 동작 그대로다.
_POS_CATEGORY = {"GK": "GK"}
for _p in ("CB", "LB", "RB", "LWB", "RWB", "SW"):
    _POS_CATEGORY[_p] = "DEF"
for _p in ("CDM", "CM", "CAM", "LM", "RM", "DM", "AM"):
    _POS_CATEGORY[_p] = "MID"
del _p
_POS_CATEGORY_GET = _POS_CATEGORY.get   # 뜨거운 루프에서 속성 조회까지 줄이려고 미리 바인딩


def _pos_category(pos):
    return _POS_CATEGORY_GET(pos, "ATK")


# ─────────────────────────────────────────────
# [2026-09 성능] 슬롯 배정 사전계산 표
# ─────────────────────────────────────────────
# pref     : {포지션: [슬롯인덱스 ...]} — (rank, 인덱스) 오름차순
# catslots : {카테고리: [슬롯인덱스 ...]} — 인덱스 오름차순
#
# 원본 _find_compat_slot은 rank가 가장 작은 슬롯을 고르고, 동점이면
# 인덱스가 작은 쪽을 골랐다(`if r < best_rank` 엄격 부등호 + enumerate
# 순서). 그래서 (rank, 인덱스)로 정렬해두고 "그 순서대로 훑다가 처음
# 만나는 빈 슬롯"을 집으면 결과가 정확히 같다. 카테고리 폴백
# (_find_category_slot)도 인덱스 오름차순으로 처음 만나는 빈 슬롯을
# 골랐으므로 같은 방식으로 미리 묶는다.
#
# 캐시 키는 호출부가 주는 값(포메이션 이름이면 그걸 그대로, 아니면
# tuple(slots))이다 — 슬롯 리스트 내용이 같으면 표도 같다.
_SLOT_TABLES: dict = {}


def _slot_tables(slots_key, slots):
    t = _SLOT_TABLES.get(slots_key)
    if t is None:
        pref = {}
        for pos, compat in POSITION_COMPAT.items():
            order = []
            for i, sp in enumerate(slots):
                if sp in compat:
                    order.append((compat.index(sp), i))
            order.sort()
            pref[pos] = [i for _r, i in order]
        cats = {}
        for i, sp in enumerate(slots):
            cats.setdefault(_POS_CATEGORY_GET(sp, "ATK"), []).append(i)
        t = (pref, cats)
        _SLOT_TABLES[slots_key] = t
    return t


def _pref_for(pref, slots, pos):
    """POSITION_COMPAT에 없는 포지션까지 처리한다 — 원본의
    `POSITION_COMPAT.get(pos, [pos])` 폴백(자기 이름과 같은 슬롯만 매치)과
    동일한 결과를 만들어 표에 채워 넣는다."""
    order = pref.get(pos)
    if order is None:
        order = [i for i, sp in enumerate(slots) if sp == pos]
        pref[pos] = order
    return order


# (선수 주포지션, 슬롯 포지션) → POSITION_MISMATCH_PENALTY 값.
# 조합 수가 유한(포지션 종류 × 슬롯 포지션 종류)이라 한 번 구하면 끝난다.
_MISMATCH_PENALTY_CACHE: dict = {}
_PENALTY_LAST_IDX = len(POSITION_MISMATCH_PENALTY) - 1


def _best_slot_for_player(primary_pos, slots):
    """주요 포지션에서 포메이션 슬롯 중 가장 자연스러운 슬롯 인덱스 반환.
    POSITION_COMPAT 우선순위 리스트 기준: 앞에 있는 슬롯일수록 높은 우선순위.
    반환: (slot_index, field_pos, mismatch_rank)
      mismatch_rank=0: 완벽 매치 / 1: 2순위 / 2: 3순위 ...
    [주의] '나(me)' 한 명의 슬롯을 결정할 때만 쓴다. 여러 후보를 한꺼번에
    슬롯에 채울 때는(AI 11명 등) 아래 _greedy_fill_slots를 쓴다 — 이
    함수의 최종 폴백(호환/카테고리 매치가 전혀 없을 때 slots[0]을 그냥
    반환)은 후보가 하나뿐일 때는 무해하지만, 여러 명을 순서대로 돌리면서
    쓰면 포지션이 몰린(예: GK 2명) 잉여 후보가 엉뚱한 자리를 가로채고
    그 여파로 뒤 후보들이 줄줄이 밀리는 문제가 있었다(2026-08 버그수정,
    신민용 리포트: "RB 의 주포가 GK로 뜨고 ST가 CB으로 간다").

    [2026-09] 호출 빈도가 낮은(내 선수 1명 전용) 함수라 최적화 대상이
    아니다 — 예전 구현 그대로 둔다.
    """
    compat = POSITION_COMPAT.get(primary_pos, [primary_pos])
    # 이미 할당된 슬롯 제외 없이 최적 슬롯만 찾음 (호출 시 이미 할당된 슬롯 제외 처리)
    best_idx, best_rank = 0, 999
    for si, slot_pos in enumerate(slots):
        if slot_pos in compat:
            rank = compat.index(slot_pos)
            if rank < best_rank:
                best_rank = rank
                best_idx = si
    if best_rank == 999:
        # 호환 없으면 카테고리로 fallback
        my_cat = _pos_category(primary_pos)
        for si, slot_pos in enumerate(slots):
            if _pos_category(slot_pos) == my_cat:
                return si, slot_pos, 4
        return 0, slots[0] if slots else primary_pos, 4
    return best_idx, slots[best_idx], best_rank


def _greedy_fill_slots(candidates, slots_only):
    """여러 후보 선수를 포메이션 슬롯(slots_only, 원본 순서)에 배정한다.
    각 배정된 선수 dict에 원본 슬롯 인덱스를 "_slot_idx"로 태깅하고,
    slots_only와 같은 길이의 리스트(빈 자리는 None)를 반환한다.

    [2026-08 버그수정, 신민용 리포트: "상대팀 포메이션에서 RB 의 주포가
    GK로 뜨고 ST가 CB으로 가며 GK는 클릭해도 스탯이 안 떠"] 예전엔 후보
    전원을 OVR 내림차순으로 한 번에 훑으면서, 각자 차례가 오면 그 자리에서
    바로 "정확한 포지션 매치 → 카테고리 매치 → 그냥 첫 빈 슬롯" 순으로
    즉시 확정지었다. 그런데 스쿼드에 같은 포지션이 몰려있으면(예: GK가
    상위 OVR 11명 안에 2명 다 들어옴) 두 번째 GK는 정확한 매치도 카테고리
    매치도 없다(GK 슬롯은 하나뿐이고 이미 찼으므로) — 그래서 "그냥 첫
    빈 슬롯"으로 강제 확정되는데, 하필 그 자리가 아직 자기 차례가 안 온
    진짜 RB 선수의 자리였다. 그러면 진짜 RB 선수는 이후 순서에서 밀려나
    엉뚱한 자리(ST 등)로 떠밀리고, 그 자리에 있던 선수도 또 밀리는 식으로
    연쇄 오배치가 일어난다. 또한 진짜 GK 슬롯 자체가 다른 잉여 선수에게
    가로채여 아예 안 채워지는 경우도 있었다(클릭해도 스탯 안 뜨는 원인).

    이제 3단계로 나눠서, 포지션이 정확히/카테고리로 맞는 선수가 항상
    먼저 자기 자리를 차지하게 하고, 그렇게도 안 맞는 잉여 선수만 맨
    마지막에 진짜 남는 자리로 보낸다:
      1) 정확한 포지션 호환(POSITION_COMPAT) 매치만 전원 시도
      2) 1)에서 못 찾은 선수만 카테고리(GK/DEF/MID/ATK) 매치 시도
      3) 그래도 남은 선수만 마지막 수단으로 아무 빈 슬롯에 순서대로

    [2026-09 성능] 3단계 판정 자체와 배정 결과는 그대로 두고, 슬롯 탐색만
    _slot_tables의 사전계산 표를 쓴다(파일 상단 주석 참고).
    """
    n = len(slots_only)
    slot_filled = [None] * n
    remaining = sorted(candidates, key=lambda x: -(x.get("ovr", 0) or 0))
    pref, catslots = _slot_tables(tuple(slots_only), slots_only)

    # 1) 정확한 포지션 호환(POSITION_COMPAT) 매치
    still_left = []
    for pl in remaining:
        got = -1
        for i in _pref_for(pref, slots_only, pl.get("position", "CM")):
            if slot_filled[i] is None:
                got = i
                break
        if got >= 0:
            slot_filled[got] = pl
            pl["_slot_idx"] = got
        else:
            still_left.append(pl)

    # 2) 카테고리(GK/DEF/MID/ATK) 매치
    remaining = still_left
    still_left = []
    for pl in remaining:
        got = -1
        for i in catslots.get(_POS_CATEGORY_GET(pl.get("position", "CM"), "ATK"), ()):
            if slot_filled[i] is None:
                got = i
                break
        if got >= 0:
            slot_filled[got] = pl
            pl["_slot_idx"] = got
        else:
            still_left.append(pl)

    # 3) 진짜 마지막 수단 — 위 두 단계로도 못 채운 선수는 남은 빈 슬롯에 순서대로.
    if still_left:
        open_idx = [i for i in range(n) if slot_filled[i] is None]
        for i, pl in zip(open_idx, still_left):
            slot_filled[i] = pl
            pl["_slot_idx"] = i

    return slot_filled


# ─────────────────────────────────────────────
# [2026-08 신설, 신민용 요청: "선수 검색 소속팀 대회 기록에 그 해 이
# 선수가 주전/로테이션/대기/유망주 중 뭐였는지 연도별로 보여달라"]
# ─────────────────────────────────────────────
# 경기당 출전율(%)로 나누고 싶어했지만 AI 선수는 경기별 출전 기록을 안
# 남긴다(전세계 매주 수천 경기 × AI 2.6만 명을 다 기록하면 저장·연산
# 비용이 감당 안 됨) — 대신 "그 시즌 이 팀 로스터 안에서 OVR 순위가
# 몇 번째인가"로 근사한다. 비중은 주전 40% : 로테이션 30% : 대기 25% :
# 유망주 15%(신민용이 준 구간의 중앙값, 정규화 전 합 110 → 아래서 총합
# 으로 나눠 정확히 100%가 되게 함).
# [2026-08 수정, 신민용 요청: "마지막 구간(유망주 및 전력외)은 나이로
# 갈라야 한다"] 마지막 구간에 걸린 선수만 나이로 한 번 더 갈라 19세
# 이하만 "유망주"를 유지하고, 20세 이상은 "전력외"로 표시한다(이 게임
# 성장곡선이 25세까지 계속 크므로 — ai_lifecycle._AI_PEAK_START — 19세면
# 아직 성장 초반이라 "재원"이 맞고, 20세부턴 낮은 OVR이 "아직 안 커서"
# 보다 "지금 실력이 이 정도"에 더 가깝다고 봄). [2026-09 수정, 신민용
# 확정: "20살부터는 전력외라 뜨게 해달라"] 원래는 "대기"(3번째 구간)와
# 합쳐서 표시했는데, 발롱도르 참가도 설계 논의 중 "20~40% 구간(꾸준한
# 백업)"과 "사실상 전력외"는 성격이 다르다는 지적으로 별도 라벨을 신설—
# _ROLE_TIER_WEIGHTS의 "대기"는 이제 3번째 구간 전용이고, 이 마지막
# 구간의 성인 몫은 항상 "전력외"로 확정 분리된다.
#
# ai_lifecycle._snapshot_season_positions(매 시즌 전환마다 팀별 로스터를
# 이미 훑고 있음)가 그 자리에서 이 함수로 같이 계산해 ai_player_
# position_history.role에 영구히 남긴다 — 매번 다시 계산하지 않고
# "그 해 실제 로스터 기준"으로 한 번만 계산해 고정하는 것이 핵심(지금
# 로스터로 과거 연도를 되짚어 계산하면 그 사이 이적으로 로스터 자체가
# 달라져 있어 부정확함).
_ROLE_TIER_WEIGHTS = [("주전", 40), ("로테이션", 30), ("대기", 25), ("유망주", 15)]
_ROLE_YOUNG_MAX_AGE = 19


def compute_squad_roles(pool):
    """pool: [(id, ovr, age), ...] — 한 팀 로스터 전체(주전+후보 다 포함,
    보통 22~25명). 반환: {id: role_label}. O(n log n)이며 n이 스쿼드
    크기(수십 명) 수준이라 팀 하나당 사실상 즉시 끝난다."""
    n = len(pool)
    if n == 0:
        return {}
    ordered = sorted(pool, key=lambda t: -(t[1] or 0))
    total_w = sum(w for _label, w in _ROLE_TIER_WEIGHTS)
    result = {}
    for idx, (pid, _ovr, age) in enumerate(ordered):
        frac = (idx + 1) / n
        running = 0
        role = _ROLE_TIER_WEIGHTS[-1][0]
        for label, w in _ROLE_TIER_WEIGHTS:
            running += w
            if frac <= running / total_w:
                role = label
                break
        if role == "유망주" and not (age is not None and age <= _ROLE_YOUNG_MAX_AGE):
            role = "전력외"
        result[pid] = role
    return result


# ─────────────────────────────────────────────
# [2026-08 신설, 신민용 확정: "포메이션 20개 확장 + 스쿼드 적합도 시스템"]
# 팀 로스터가 특정 포메이션 슬롯 구성에 얼마나 잘 맞는지를 계산한다.
# ai_lifecycle._update_formations_and_fit()(시즌 전환 시 모든 팀의
# 포메이션 재검토/formation_fit_bonus 캐시 갱신)과 game_engine._formation_
# bias()(경기 시뮬 보정, 캐시된 값만 읽음)가 이 모듈의 함수를 공용으로
# 쓴다 — Qt 의존성 없는 이 파일에 두는 이유는 파일 맨 위 docstring과
# 동일(헤드리스 환경에서도 import 가능해야 함).
# ─────────────────────────────────────────────
def _mismatch_rank(player_pos, slot_pos):
    """_best_slot_for_player와 동일한 우선순위 규칙으로 (선수 주포지션,
    배정된 슬롯) 한 쌍의 미스매치 등급을 구한다. 0=완벽 매치, 값이
    클수록 어색한 배치 — POSITION_MISMATCH_PENALTY 인덱스로 그대로 쓴다."""
    compat = POSITION_COMPAT.get(player_pos, [player_pos])
    if slot_pos in compat:
        return compat.index(slot_pos)
    if _pos_category(player_pos) == _pos_category(slot_pos):
        return 4   # 카테고리만 일치 — _best_slot_for_player의 카테고리 폴백과 동일 등급
    return 4        # 완전 폴백도 동일 등급(POSITION_MISMATCH_PENALTY 마지막 값 재사용)


def _mismatch_penalty(player_pos, slot_pos):
    """[2026-09 성능] _mismatch_rank 결과를 POSITION_MISMATCH_PENALTY 값까지
    바로 뽑아 메모이즈한다. 예전 formation_fit_penalty가 슬롯마다 하던
    `idx = min(rank, len(POSITION_MISMATCH_PENALTY)-1)`까지 포함한 값이라
    결과가 완전히 동일하다."""
    key = (player_pos, slot_pos)
    v = _MISMATCH_PENALTY_CACHE.get(key)
    if v is None:
        rank = _mismatch_rank(player_pos, slot_pos)
        v = POSITION_MISMATCH_PENALTY[rank if rank < _PENALTY_LAST_IDX else _PENALTY_LAST_IDX]
        _MISMATCH_PENALTY_CACHE[key] = v
    return v


def prep_roster(roster):
    """[2026-09 성능 신설] 한 팀 로스터를 "OVR 내림차순 (포지션, OVR) 튜플
    리스트"로 한 번만 접어둔다.

    choose_formation은 같은 로스터에 대해 포메이션 20개를 평가하는데,
    예전엔 그 20번이 전부 로스터를 다시 정렬하고 dict에서 position/ovr을
    다시 꺼내고 있었다 — 정렬 결과도 꺼내는 값도 매번 완전히 동일하다.

    반환값은 formation_fit_penalty_prepped/choose_formation_prepped에
    그대로 넘긴다. 정렬 키(-ovr)와 안정 정렬 성질이 _greedy_fill_slots의
    것과 같으므로 배정 순서도 동일하다."""
    ordered = sorted(roster, key=lambda x: -(x.get("ovr", 0) or 0))
    return [(p.get("position", "CM"), (p.get("ovr", 0) or 0)) for p in ordered]


def formation_fit_penalty_prepped(prepped, slots_key, slots):
    """prep_roster() 결과를 받는 formation_fit_penalty. slots_key는 표
    캐시 키(포메이션 이름을 그대로 쓰면 된다). 값은 formation_fit_penalty와
    완전히 동일하다 — 배정 3단계도 _greedy_fill_slots와 같은 순서다."""
    if not prepped or not slots:
        return 0.0
    pref, catslots = _slot_tables(slots_key, slots)
    n = len(slots)
    filled = [None] * n

    # 1) 정확한 포지션 호환 매치
    left = []
    for pl in prepped:
        got = -1
        for i in _pref_for(pref, slots, pl[0]):
            if filled[i] is None:
                got = i
                break
        if got >= 0:
            filled[got] = pl
        else:
            left.append(pl)

    if left:
        # 2) 카테고리 매치
        left2 = []
        for pl in left:
            got = -1
            for i in catslots.get(_POS_CATEGORY_GET(pl[0], "ATK"), ()):
                if filled[i] is None:
                    got = i
                    break
            if got >= 0:
                filled[got] = pl
            else:
                left2.append(pl)
        # 3) 남은 빈 슬롯에 순서대로
        if left2:
            it = iter(left2)
            for i in range(n):
                if filled[i] is None:
                    pl = next(it, None)
                    if pl is None:
                        break
                    filled[i] = pl

    total_ovr = 0.0
    weighted_penalty = 0.0
    _mp = _mismatch_penalty
    for slot_pos, pl in zip(slots, filled):
        if pl is None:
            continue
        ovr = pl[1]
        weighted_penalty += ovr * _mp(pl[0], slot_pos)
        total_ovr += ovr
    if total_ovr <= 0:
        return 0.0
    return weighted_penalty / total_ovr


def formation_fit_penalty(roster, slots):
    """roster: [{'position': pos, 'ovr': ovr}, ...] (스쿼드 전체, 보통
    20~25명 — 상위 OVR 11명이 자연스럽게 주전으로 뽑힌다. _greedy_fill_
    slots와 동일한 알고리즘이라 "이 시즌 실제 주전 슬롯 배정"과 항상
    같은 기준으로 계산된다).
    slots: FORMATION_SLOTS[formation] (길이 11).
    반환: OVR 가중 평균 미스매치 페널티(0.0~0.15, POSITION_MISMATCH_
    PENALTY 범위 그대로) — 낮을수록 이 스쿼드가 이 포메이션에 잘 맞는다.

    [2026-09] 같은 로스터로 여러 포메이션을 평가할 때는 prep_roster() +
    formation_fit_penalty_prepped()를 쓰는 쪽이 훨씬 싸다(정렬 1회).
    이 함수는 그 조합의 얇은 래퍼로 남겨 기존 호출부(intl_engine 등)를
    그대로 지원한다."""
    if not roster or not slots:
        return 0.0
    return formation_fit_penalty_prepped(prep_roster(roster), tuple(slots), slots)


def formation_fit_norm(avg_penalty, max_penalty=0.15):
    """formation_fit_penalty() 결과(0~0.15)를 여러 포메이션 후보를 서로
    비교하는 선택 로직용으로 0~1 정규화한다 — 낮은 페널티일수록 1에
    가깝다(스쿼드가 잘 맞는 포메이션일수록 점수가 높다)."""
    if max_penalty <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - avg_penalty / max_penalty))


def formation_fit_bonus(avg_penalty):
    """[2026-08 신설, 신민용 확정: "포메이션 적합도는 선수 능력치를 직접
    올리는 게 아니라 팀 전술 효율 보정으로만 쓴다"] formation_fit_penalty()
    결과를 경기 시뮬레이션(game_engine._formation_bias)에 더해지는
    ±constants.SQUAD_FORMATION_FIT_MAX 범위의 보너스로 변환한다.
    SQUAD_FORMATION_FIT_BASELINE_PENALTY(평균적인 어긋남 정도)보다 덜
    어긋나면 +, 더 어긋나면 - — 실력차(OVR)를 뒤집을 정도로 크지 않게
    club_strength/prestige 재조정 때와 같은 원칙으로 상한을 걸었다."""
    from constants import (SQUAD_FORMATION_FIT_MAX,
                           SQUAD_FORMATION_FIT_BASELINE_PENALTY,
                           SQUAD_FORMATION_FIT_PENALTY_SPAN)
    raw = (SQUAD_FORMATION_FIT_BASELINE_PENALTY - avg_penalty) / SQUAD_FORMATION_FIT_PENALTY_SPAN
    return max(-SQUAD_FORMATION_FIT_MAX,
               min(SQUAD_FORMATION_FIT_MAX, raw * SQUAD_FORMATION_FIT_MAX))


def choose_formation_prepped(prepped, current_formation, tendency, rng=None):
    """prep_roster() 결과를 받는 choose_formation. 점수 계산·정렬·후보
    선정·랜덤 소비 순서가 choose_formation과 완전히 동일하다 — 포메이션
    하나당 _rng.random()을 정확히 한 번씩, FORMATION_SLOTS 순서대로
    소비하고 마지막에 _rng.choices를 한 번 부른다."""
    import random as _random
    from constants import (FORMATION_SLOTS, FORMATION_STYLE, TACTIC_TENDENCY_LEAN,
                           FORMATION_SCORE_WEIGHTS, FORMATION_CANDIDATE_TOP_N)
    _rng = rng or _random
    lean = TACTIC_TENDENCY_LEAN.get(tendency, 0.0)
    w = FORMATION_SCORE_WEIGHTS
    _w_squad, _w_tend, _w_rand = w["squad_fit"], w["tendency_fit"], w["random"]
    _style = FORMATION_STYLE.get
    _rand = _rng.random
    scored = []
    for name, slots in FORMATION_SLOTS.items():
        penalty = formation_fit_penalty_prepped(prepped, name, slots)
        squad_fit = formation_fit_norm(penalty)
        tendency_fit = max(0.0, 1.0 - abs(lean - _style(name, 0.0)) / 3.0)
        score = (_w_squad * squad_fit + _w_tend * tendency_fit
                 + _w_rand * _rand())
        scored.append((score, name, penalty))
    scored.sort(key=lambda t: -t[0])
    top = scored[:FORMATION_CANDIDATE_TOP_N] or scored
    weights = [max(0.01, s) for s, _n, _p in top]
    chosen_score, chosen_name, chosen_penalty = _rng.choices(top, weights)[0]
    return chosen_name, chosen_penalty


def choose_formation(roster, current_formation, tendency, rng=None):
    """[2026-08 신설, 신민용 확정: "score = squad_fit(60%) + tendency_fit(30%)
    + random(10%), 상위 몇 개 후보 중 가중 랜덤"] 시즌 전환 시 포메이션을
    재검토하는 팀에 대해 호출한다(재검토 여부 자체는 constants.
    FORMATION_REEVAL_PROB로 호출부에서 먼저 굴린다 — 이 함수는 "재검토
    한다면 무엇을 고를지"만 담당).
    roster: [{'position':pos,'ovr':ovr}, ...] 그 팀 스쿼드 전체.
    current_formation: 지금 쓰고 있는 포메이션(동점 시 유지 성향에 참고 가능,
    현재는 후보 풀에 포함만 시키고 특별 가중치는 안 둠).
    tendency: constants.TACTIC_TENDENCIES 중 하나.
    반환: (선택된 포메이션 이름, 그 포메이션의 formation_fit_penalty 값)
    — 후자는 호출부가 formation_fit_bonus() 캐싱에 바로 재사용한다."""
    return choose_formation_prepped(prep_roster(roster), current_formation, tendency, rng=rng)