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
"""
from constants import POSITION_COMPAT


def _pos_category(pos):
    if pos == "GK": return "GK"
    if pos in ("CB", "LB", "RB", "LWB", "RWB", "SW"): return "DEF"
    if pos in ("CDM", "CM", "CAM", "LM", "RM", "DM", "AM"): return "MID"
    return "ATK"


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
    """
    slot_filled = [None] * len(slots_only)
    remaining = sorted(candidates, key=lambda x: -(x.get("ovr", 0) or 0))

    def _find_compat_slot(pl):
        pos = pl.get("position", "CM")
        compat = POSITION_COMPAT.get(pos, [pos])
        best_i, best_rank = None, 999
        for i, sp in enumerate(slots_only):
            if slot_filled[i] is None and sp in compat:
                r = compat.index(sp)
                if r < best_rank:
                    best_rank = r; best_i = i
        return best_i

    def _find_category_slot(pl):
        cat = _pos_category(pl.get("position", "CM"))
        for i, sp in enumerate(slots_only):
            if slot_filled[i] is None and _pos_category(sp) == cat:
                return i
        return None

    for phase_fn in (_find_compat_slot, _find_category_slot):
        still_left = []
        for pl in remaining:
            i = phase_fn(pl)
            if i is not None:
                slot_filled[i] = pl
                pl["_slot_idx"] = i
            else:
                still_left.append(pl)
        remaining = still_left

    # 3) 진짜 마지막 수단 — 위 두 단계로도 못 채운 선수는 남은 빈 슬롯에 순서대로.
    open_idx = [i for i in range(len(slot_filled)) if slot_filled[i] is None]
    for i, pl in zip(open_idx, remaining):
        slot_filled[i] = pl
        pl["_slot_idx"] = i

    return slot_filled