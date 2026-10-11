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


@functools.lru_cache(maxsize=None)
def _targets_cached(formation, n):
    t = dict(starter_counts(formation))
    for p in reserve_order(formation)[:max(0, n - 11)]:
        t[p] += 1
    return t


def position_targets(formation, n):
    """N명 팀의 목표 인원(dict). N은 11~28로 자른다."""
    n = max(11, min(int(n), MAX_SQUAD))
    return dict(_targets_cached(_formation(formation), n))


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


def choose_target_position(formation, n_goal, held, rng, exception_prob):
    """보충 1명의 목표 포지션(FW 포함 10종 중 하나, 못 고르면 None). held(Counter)는 바꾸지 않는다.
      · exception_prob 확률: 목표표(N) 비율로 — 28명 목표에 이미 닿은 칸은 후보 제외.
      · 그 외: 목표(N) 대비 부족량 비례.
      · 부족이 없으면: 순서표에서 아직 못 채운 첫 자리(28명 목표에 닿은 칸은 건너뜀).
    난수: rng.random()을 1번(예외 판정) + 고른 경로에서 1번 쓴다."""
    f = _formation(formation)
    tgt = position_targets(f, n_goal)
    cap = position_targets(f, MAX_SQUAD)
    if rng.random() < exception_prob:
        p = _weighted_pick([(p, tgt[p]) for p in TARGET_POSITIONS if held[p] < cap[p] and tgt[p] > 0], rng)
        if p is not None:
            return p
    deficit = [(p, tgt[p] - held[p]) for p in TARGET_POSITIONS if tgt[p] > held[p]]
    if deficit:
        return _weighted_pick(deficit, rng)
    count = dict(starter_counts(f))
    for p in reserve_order(f):
        count[p] += 1
        if held[p] < count[p] and held[p] < cap[p]:
            return p
    return None