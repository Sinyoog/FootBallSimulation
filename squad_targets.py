"""AI 클럽 스쿼드 포지션 목표 인원표 — 포메이션별 '여유 자리 순서표' 하나에서 목표표를 만든다.

[2026-10 확정, 신민용+GPT — AI 스쿼드 CM 계열 과잉 재설계 범위 1, 10시즌 × 3시드 검증 후 채택]
ai_lifecycle._gen_topup_rows(연말 인원 보충·오퍼 빈자리 보충)와 포지션 맞바꾸기의 GK 보장이 쓴다.

규칙(목표 포지션 10종: GK CB LB RB CDM CM CAM LW RW FW, FW = ST+CF 합계)
  · 슬롯 → 등록 포지션: LM/RM→LW/RW, LWB/RWB→LB/RB, ST/CF→FW. 이 치환은
    POSITION_COMPAT에서 그 슬롯을 1순위 감점(5%)으로 받는 등록 포지션과 같다
    (LM 슬롯 ← LW, RM ← RW, LWB ← LB, RWB ← RB — test_squad_targets가 검사).
  · 최소 = 선발 수(고정). 12~28번째 자리는 아래 순서표 순서대로 한 명씩 더한다.
    목표(N) = 선발 + 순서표 앞쪽 N-11명 → 합계는 항상 N, N을 늘려도 줄어드는 칸이 없다.
  · 순서표:
      a) 필수 백업: GK, CB, MID, FW, (선발에 있으면) LB, RB, LW, RW 각 1명.
      b) 1단계(2배 뎁스까지): 점수 = 선발 수 / (받은 여유+1)이 가장 큰 칸.
         여유 상한: GK 1, CB k, 측면 한쪽 k, MID min(k,4)(총원 9 이하), FW max(k,2).
      c) 2단계: 4백 CB·MID·FW +1, 측면 한쪽 +1(선발 측면 2명인 쪽은 제외), 선발에 없는
         측면은 커버 1명씩(점수 0.5), GK 2번째 여유는 26번째 자리부터(점수 0.6/(여유+1)).
      d) 동점이면 CB > MID > FW > LB > RB > LW > RW > GK.
      MID 칸이 나올 때 종류: 선발에 있는 종류 1명씩 먼저(선발 많은 순), 그다음 선발 수 비례.
  · 목표표는 '보충 기준'이지 그 수만큼 생성하라는 명령이 아니다 — 보충 경로만 이 표를 본다.
"""
import collections
import functools

import constants as _C
from constants import FORMATION_SLOTS

TARGET_POSITIONS = ("GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "FW")
SLOT_TO_TARGET = {"GK": "GK", "CB": "CB", "LB": "LB", "RB": "RB", "LWB": "LB", "RWB": "RB",
                  "CDM": "CDM", "CM": "CM", "CAM": "CAM", "LM": "LW", "RM": "RW",
                  "LW": "LW", "RW": "RW", "ST": "FW", "CF": "FW"}
# 등록 포지션 → 목표 포지션(ST·CF는 FW 한 칸으로 센다)
REGISTERED_TO_TARGET = {"GK": "GK", "CB": "CB", "LB": "LB", "RB": "RB", "CDM": "CDM",
                        "CM": "CM", "CAM": "CAM", "LW": "LW", "RW": "RW", "ST": "FW", "CF": "FW"}
MAX_SQUAD = 28
DEFAULT_FORMATION = "4-4-2"
_UNITS = ("CB", "MID", "FW", "LB", "RB", "LW", "RW", "GK")      # 동점 순서
_SIDE = ("LB", "RB", "LW", "RW")
_MIDS = ("CDM", "CM", "CAM")


def _formation(name):
    return name if name in FORMATION_SLOTS else DEFAULT_FORMATION


@functools.lru_cache(maxsize=None)
def starter_counts(formation):
    """선발 11명을 목표 포지션으로 바꾼 인원(dict, 10칸 전부 키 있음)."""
    s = collections.Counter(SLOT_TO_TARGET[x] for x in FORMATION_SLOTS[_formation(formation)])
    return {p: s[p] for p in TARGET_POSITIONS}


def _cap(u, k, seat, tier):
    kk = k[u]
    if u == "GK":
        return 1 if (tier == 1 or seat < 26) else 2
    if u in _SIDE:
        if kk == 0:
            return 0 if tier == 1 else 1
        return kk if (tier == 1 or kk >= 2) else kk + 1
    if u == "MID":
        return min(min(kk, 4) + (tier - 1), 9 - kk)
    if u == "FW":
        return max(kk, 2) + (tier - 1)
    return kk if (tier == 1 or kk >= 3) else kk + 1             # CB: 3백은 6명에서 멈춤


def _score(u, k, res):
    if u == "GK":
        return 0.6 / (res[u] + 1)
    kk = k[u] if k[u] > 0 else 0.5
    return kk / (res[u] + 1)


@functools.lru_cache(maxsize=None)
def reserve_order(formation):
    """12번째~28번째 자리의 목표 포지션 순서(tuple, 길이 17). 목표표의 유일한 원천."""
    s = starter_counts(formation)
    k = {"GK": 1, "CB": s["CB"], "MID": s["CDM"] + s["CM"] + s["CAM"], "FW": s["FW"],
         "LB": s["LB"], "RB": s["RB"], "LW": s["LW"], "RW": s["RW"]}
    res = {u: 0 for u in _UNITS}
    units = []
    seat = 12
    for u in ("GK", "CB", "MID", "FW", "LB", "RB", "LW", "RW"):
        if u in _SIDE and k[u] == 0:
            continue
        res[u] += 1; units.append(u); seat += 1
    for tier in (1, 2):
        while seat <= MAX_SQUAD:
            cand = [u for u in _UNITS if res[u] < _cap(u, k, seat, tier)]
            if not cand:
                break
            u = max(cand, key=lambda u: (round(_score(u, k, res), 9), -_UNITS.index(u)))
            res[u] += 1; units.append(u); seat += 1
    used = sorted([p for p in _MIDS if s[p] > 0], key=lambda p: -s[p])
    got = collections.Counter()
    out = []
    for u in units:
        if u != "MID":
            out.append(u)
            continue
        first = [q for q in used if got[q] == 0]
        p = first[0] if first else max(used, key=lambda q: (s[q] / (got[q] + 1), -used.index(q)))
        got[p] += 1
        out.append(p)
    return tuple(out)


def _gk3():
    """[2026-10-30 신설] constants.WORLD_SQUAD_BY_FORMATION — 켜져 있으면 목표표 GK 3명.
    [2026-10-31] SQUAD_BY_FORMATION_V3도 같다(선수단 생성·보충 부분만 떼어 낸 스위치)."""
    # [2026-11-01] GK 2~3명 규칙(constants.GK_SQUAD_2_3)도 목표표 GK 3명.
    return bool(getattr(_C, "WORLD_SQUAD_BY_FORMATION", False) or getattr(_C, "SQUAD_BY_FORMATION_V3", False)
                or getattr(_C, "SQUAD_BY_FORMATION_V31", False) or getattr(_C, "GK_SQUAD_2_3", False))


def _v31():
    """[2026-10-31] constants.SQUAD_BY_FORMATION_V31 — 목표표 중원 배분(_mid_alloc)을 쓴다(기존 스위치가 켜져
    있으면 그쪽 경로 우선)."""
    return bool(getattr(_C, "SQUAD_BY_FORMATION_V31", False)) and not getattr(_C, "WORLD_SQUAD_BY_FORMATION", False)


@functools.lru_cache(maxsize=None)
def _mid_alloc(formation, m):
    """[2026-10-31 신설, 신민용 확정 — V3.1 중원 배분] 중원 합계 m명을 CDM·CM·CAM으로 나눈다(dict).
      ① 선발  ② 백업: 선발에 있는 종류마다 1명
      ③ 인접 대비: CM 선발이 있으면 선발에 없는 CDM·CAM 1명씩, CM 선발이 없으면(CDM·CAM 선발이 있을 때) CM 1명
      ④ 나머지: 인원이 적은 종류부터 1명씩. 동률이면 백업(선발 초과분)이 적은 쪽. 그래도 같으면 남은 자리가
         동률 수 이상일 때 양쪽에 1명씩(순서 무관), 1자리뿐이면 CM에 준다 — CM은 CDM 칸·CAM 칸 양쪽 대체 명단
         (POSITION_COMPAT)에 있어 두 칸의 대체 가능 인원이 똑같이 늘고 CDM = CAM 균형이 유지된다.
    m이 선발보다 적으면(목표표에서는 생기지 않음) 선발 그대로 돌려준다. 난수 없음."""
    s = starter_counts(formation)
    out = {p: s[p] for p in _MIDS}
    left = m - sum(out.values())
    for p in _MIDS:                                  # ② 백업
        if s[p] > 0 and left > 0:
            out[p] += 1
            left -= 1
    if s["CM"] > 0:                                  # ③ 인접 대비
        adj = [p for p in ("CDM", "CAM") if s[p] == 0]
    else:
        adj = ["CM"] if (s["CDM"] or s["CAM"]) else []
    for p in adj:
        if left > 0:
            out[p] += 1
            left -= 1
    while left > 0:                                  # ④ 부족 종류 우선
        mn = min(out.values())
        cand = [p for p in _MIDS if out[p] == mn]
        if len(cand) > 1:
            mb = min(out[p] - s[p] for p in cand)
            cand = [p for p in cand if out[p] - s[p] == mb]
        if len(cand) > 1:
            if left >= len(cand):
                for p in cand:
                    out[p] += 1
                left -= len(cand)
                continue
            cand = ["CM"] if "CM" not in cand else cand[:1]
        out[cand[0]] += 1
        left -= 1
    return out


@functools.lru_cache(maxsize=None)
def _targets_v31_cached(formation, n):
    """V3.1 목표표 = V3 목표표(GK 3명)에서 중원 합계는 그대로, 종류만 _mid_alloc으로 다시 나눈 것."""
    t = dict(_targets_cached(formation, n, True))
    t.update(_mid_alloc(formation, sum(t[p] for p in _MIDS)))
    return t


@functools.lru_cache(maxsize=None)
def field_reserve_order(formation):
    """[2026-10-30 신설] 여유 자리 순서표에서 GK 칸만 뺀 필드 순서(GK 3명 목표표용)."""
    return tuple(p for p in reserve_order(formation) if p != "GK")


@functools.lru_cache(maxsize=None)
def _order_cached(formation, gk3):
    """12번째 자리부터의 목표 포지션 순서. GK 3명이면 GK 여유 2명을 맨 앞에 두고 나머지는
    기존 순서표의 필드 순서 그대로 — 22~28명에서 '(N−1)명 표 + GK 1명'과 같다(단위 테스트 검사)."""
    if not gk3:
        return reserve_order(formation)
    return ("GK", "GK") + field_reserve_order(formation)


@functools.lru_cache(maxsize=None)
def _targets_cached(formation, n, gk3=False):
    t = dict(starter_counts(formation))
    for p in _order_cached(formation, gk3)[:max(0, n - 11)]:
        t[p] += 1
    return t


def position_targets(formation, n):
    """N명 팀의 목표 인원(dict). N은 11~28로 자른다.
    [2026-10-30] constants.WORLD_SQUAD_BY_FORMATION이 켜져 있으면 GK 3명(13명 이상)."""
    n = max(11, min(int(n), MAX_SQUAD))
    if _v31():
        return dict(_targets_v31_cached(_formation(formation), n))
    return dict(_targets_cached(_formation(formation), n, _gk3()))


def max_targets(formation, n):
    """[2026-10-30 신설] 월드 생성 포지션 상한 — GK 3 고정, 나머지는 목표+1(목표 0이면 1)."""
    t = position_targets(formation, n)
    return {p: (3 if p == "GK" else (t[p] + 1 if t[p] > 0 else 1)) for p in TARGET_POSITIONS}


# ── [2026-10-30 2차, 신민용 승인] 대체 포메이션 대비 인원 ─────────────────────────
# 대체 포메이션 = 포메이션 재검토와 같은 점수식(formation_logic.alt_formations, 상위 8개 —
# 현재 포메이션 제외)으로 정한다. 이 모듈은 그 결과(alts, 튜플)만 받는다.
#   A(필수): 주 포메이션 선발에 없는데 대체 포메이션 선발에 있는 포지션 → 1명.
#   B(여유): 대체 포메이션이 같은 포지션을 더 많이 쓰면(예: 4-2-3-1의 CDM 2) 그 차이 —
#            주 포메이션 필수 백업(포지션 단위별 1명) 다음 자리에 둬서 인원이 남을 때만 채워진다.
#   자리 순서: GK 2 → A → 주 포메이션 필수 백업 → B → 나머지 주 포메이션 순서표.
#   A·B로 이미 채운 포지션은 뒤의 주 포메이션 순서표에서 같은 수만큼 건너뛴다(중복 계산 없음).
_FIELD = tuple(p for p in TARGET_POSITIONS if p != "GK")


@functools.lru_cache(maxsize=None)
def cover_plan(formation, alts):
    """(A, B, 대체 포메이션 필요 인원) — 모두 {포지션: 인원}. alts: 대체 포메이션 튜플."""
    f = _formation(formation)
    s = starter_counts(f)
    need = {p: max([starter_counts(_formation(a))[p] for a in alts] + [0]) for p in _FIELD}
    a_cov = {p: 1 for p in _FIELD if s[p] == 0 and need[p] > 0}
    ess = field_reserve_order(f)[:_essential_count(f)]
    ess_cnt = collections.Counter(ess)
    b_cov = {}
    for p in _FIELD:
        extra = need[p] - s[p] - a_cov.get(p, 0) - ess_cnt[p]
        if extra > 0:
            b_cov[p] = extra
    return a_cov, b_cov, need


def _essential_count(formation):
    """필드 순서표 맨 앞의 필수 백업 수 — CB·MID·FW 각 1 + 선발에 있는 측면(LB·RB·LW·RW) 각 1."""
    s = starter_counts(formation)
    return 3 + sum(1 for p in _SIDE if s[p] > 0)


@functools.lru_cache(maxsize=None)
def cover_order(formation, alts):
    """12번째 자리부터의 목표 포지션 순서(GK 3명 + 대비 인원). 길이는 MAX_SQUAD-11 이상."""
    f = _formation(formation)
    a_cov, b_cov, _need = cover_plan(f, alts)
    field = list(field_reserve_order(f))
    e = _essential_count(f)
    pre = [p for p in _FIELD for _ in range(a_cov.get(p, 0))]
    mid = [p for p in _FIELD for _ in range(b_cov.get(p, 0))]
    skip = collections.Counter(pre) + collections.Counter(mid)
    order = ["GK", "GK"] + pre + field[:e] + mid
    for p in field[e:]:
        if skip[p] > 0:
            skip[p] -= 1
            continue
        order.append(p)
    return tuple(order)


@functools.lru_cache(maxsize=None)
def _team_targets_cached(formation, n, alts):
    t = dict(starter_counts(formation))
    for p in cover_order(formation, alts)[:max(0, n - 11)]:
        t[p] += 1
    return t


def team_targets(formation, n, alts):
    """[2026-10-30 2차] 대체 포메이션 대비 인원을 넣은 N명 목표표(GK 3명). alts가 비면 position_targets와 같다."""
    f = _formation(formation)
    n = max(11, min(int(n), MAX_SQUAD))
    alts = tuple(sorted(_formation(a) for a in (alts or ()) if _formation(a) != f))
    if not alts and _v31():
        # [2026-10-31 V3.1] 대체 포메이션이 없으면(V3·V3.1 월드 생성은 빈 목록) 중원 배분을 넣은 목표표 그대로 —
        # 월드 생성(_build_squad_positions_by_formation)·추첨(choose_target_position)이 같은 표를 보게 한다.
        return position_targets(f, n)
    return dict(_team_targets_cached(f, n, alts))


def team_max(formation, n, alts):
    """[2026-10-30 2차] 상한 — GK 3 고정, 나머지는 목표+1(목표 0이면 1)."""
    t = team_targets(formation, n, alts)
    return {p: (3 if p == "GK" else (t[p] + 1 if t[p] > 0 else 1)) for p in TARGET_POSITIONS}


def mandatory_cover(formation, alts):
    """[2026-10-30 2차] A(필수 대비) 포지션 집합."""
    f = _formation(formation)
    alts = tuple(sorted(_formation(a) for a in (alts or ()) if _formation(a) != f))
    return set(cover_plan(f, alts)[0])


def formable(entries, formation):
    """[2026-10-30 2차] 이 포메이션 선발 11명을 선수 중복 없이 등록 포지션·부포로 채울 수 있는가.
    entries: [(등록 포지션, 부포 또는 None)]. 칸은 목표 포지션 단위(LM→LW, LWB→LB, ST·CF→FW).
    held_assign(증가 경로 = 최대 유량)으로 선발 칸 수를 목표로 배정해 모든 칸이 차면 True."""
    s = starter_counts(_formation(formation))
    h = held_assign(entries, s)
    return all(h[p] >= s[p] for p in TARGET_POSITIONS)


def held_counter(registered_positions):
    """등록 포지션 목록 → 목표 포지션별 보유 수(Counter). 목표표 밖 포지션은 세지 않는다."""
    c = collections.Counter()
    for p in registered_positions:
        tp = REGISTERED_TO_TARGET.get(p)
        if tp:
            c[tp] += 1
    return c


def held_assign(entries, targets, return_assignment=False):
    """[2026-10 신설, 신민용 확정 — 주포·부포 ③] 부포까지 센 목표 포지션별 보유 수(Counter).
    entries: [(등록 포지션, 부포 또는 None)], targets: position_targets(포메이션, N) 결과.

    원칙: 선수 1명은 정확히 1칸 — 주포 칸 또는 부포 칸 중 하나에만 센다(이중 계산 금지).
      1) 전원을 주포 칸(REGISTERED_TO_TARGET)에 배정한다. 부포는 SLOT_TO_TARGET으로 목표 칸에
         맞춘다(LM→LW, RM→RW, LWB→LB, RWB→RB, CF→FW). 부포 칸이 주포 칸과 같으면 부포는 없는 셈.
      2) 목표를 넘는 칸(초과)에서 목표에 못 미치는 칸(부족)으로 가는 증가 경로를 BFS로 찾는다 —
         칸 U에 배정된 선수 중 다른 쪽(주포↔부포)이 칸 T인 선수가 있으면 U→T로 갈 수 있다.
         경로를 찾으면 경로 위 선수를 한 칸씩 옮긴다(연쇄 이동). 출발 칸은 초과라 옮겨도 목표
         아래로 내려가지 않고, 중간 칸은 들어오고 나가는 수가 같아 그대로다 — 새 부족이 생기지
         않는다. 한 경로에서 칸마다 한 번만 펼치므로 같은 선수를 두 번 옮기지 않는다.
      3) 경로가 없을 때까지 반복한다. 매번 총 부족량이 정확히 1 줄어 반드시 끝난다.
    탐색 순서는 TARGET_POSITIONS 순서와 entries 순서(호출부가 주는 순서)라 결정론이다.
    부포가 하나도 없으면 결과가 held_counter([등록...])와 같다.
    return_assignment=True면 (Counter, [선수별 배정 칸 또는 None])을 돌려준다(테스트용)."""
    prim = [REGISTERED_TO_TARGET.get(p) for p, _s in entries]
    alt = []
    for (p, s), pt in zip(entries, prim):
        st = SLOT_TO_TARGET.get(s) if s else None
        alt.append(st if (pt and st and st != pt) else None)
    assign = list(prim)
    cnt = collections.Counter(t for t in assign if t)
    at = {t: [] for t in TARGET_POSITIONS}
    for i, t in enumerate(assign):
        if t:
            at[t].append(i)

    def _other(i):
        return alt[i] if assign[i] == prim[i] else prim[i]

    if any(alt):
        while True:
            sources = [t for t in TARGET_POSITIONS if cnt[t] > targets.get(t, 0)]
            if not sources or not any(cnt[t] < targets.get(t, 0) for t in TARGET_POSITIONS):
                break
            parent = {}
            queue = list(sources)
            seen = set(sources)
            goal = None
            qi = 0
            while qi < len(queue) and goal is None:
                u = queue[qi]; qi += 1
                for i in at[u]:
                    v = _other(i)
                    if not v or v in seen:
                        continue
                    seen.add(v)
                    parent[v] = (u, i)
                    if cnt[v] < targets.get(v, 0):
                        goal = v
                        break
                    queue.append(v)
            if goal is None:
                break
            v = goal
            while v in parent:          # 경로를 거꾸로 따라가며 한 칸씩 옮긴다
                u, i = parent[v]
                at[u].remove(i)
                at[v].append(i)
                assign[i] = v
                cnt[u] -= 1
                cnt[v] += 1
                v = u
    out = collections.Counter({t: c for t, c in cnt.items() if c})
    return (out, assign) if return_assignment else out


def _weighted_pick(items, rng):
    total = sum(w for _p, w in items)
    if total <= 0:
        return None
    x = rng.random() * total
    acc = 0.0
    for p, w in items:
        acc += w
        if x < acc:
            return p
    return items[-1][0]


def choose_target_position(formation, n_goal, held, rng, exception_prob, max_table=None,
                           alts=None):
    """보충 1명의 목표 포지션(FW 포함 10종 중 하나, 못 고르면 None). held(Counter)는 바꾸지 않는다.
      · exception_prob 확률: 목표표(N) 비율로 — 28명 목표에 이미 닿은 칸은 후보 제외.
      · 그 외: 목표(N) 대비 부족량 비례.
      · 부족이 없으면: 순서표에서 아직 못 채운 첫 자리(28명 목표에 닿은 칸은 건너뜀).
    max_table: [2026-10-30 신설, 월드 생성 전용] 주면 그 상한에 닿은 칸도 후보에서 뺀다 —
      상한에 걸린 추첨은 금지가 아니라 다른 칸으로 넘어간다. 안 주면(None) 예전과 같다.
    alts: [2026-10-30 2차] 주면(None이 아니면) 대체 포메이션 대비 인원을 넣은 목표표(team_targets)를 쓴다.
    난수: rng.random()을 1번(예외 판정) + 고른 경로에서 1번 쓴다."""
    f = _formation(formation)
    if alts is not None:
        tgt = team_targets(f, n_goal, alts)
        cap = team_targets(f, MAX_SQUAD, alts)
    else:
        tgt = position_targets(f, n_goal)
        cap = position_targets(f, MAX_SQUAD)
    if rng.random() < exception_prob:
        p = _weighted_pick([(p, tgt[p]) for p in TARGET_POSITIONS if held[p] < cap[p] and tgt[p] > 0
                            and (max_table is None or held[p] < max_table[p])], rng)
        if p is not None:
            return p
    deficit = [(p, tgt[p] - held[p]) for p in TARGET_POSITIONS if tgt[p] > held[p]]
    if deficit:
        return _weighted_pick(deficit, rng)
    count = dict(starter_counts(f))
    _ord = (cover_order(f, tuple(sorted(_formation(a) for a in alts if _formation(a) != f)))
            if alts is not None else _order_cached(f, _gk3()))
    for p in _ord:
        count[p] += 1
        if (held[p] < count[p] and held[p] < cap[p]
                and (max_table is None or held[p] < max_table[p])):
            return p
    return None