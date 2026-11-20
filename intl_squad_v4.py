# -*- coding: utf-8 -*-
"""intl_squad_v4.py — 국가대표 26인 선발 v4 (2026-11 신설, 신민용 확정)

[왜] v3까지의 문제(1986 프랑스 실측):
  · 26인을 포지션 그룹별 고정 정원(GK3/CB5/FB4/CM6/AM2/WG4/ST2)으로 먼저 뽑고, 포메이션은 그 26명을 놓고
    나중에 골랐다 — ST가 아무리 좋아도 2명까지만 뽑히고, LB 3 : RB 1처럼 한쪽 백업이 비었다.
  · 주전 11명은 선발 점수가 아니라 "OVR − 27세와의 나이 차 × 0.3"으로 골랐다 — 22세 득점왕 ST가 같은 OVR의
    27세 근처 ST에게 밀렸다.
  · 자리 배치가 예전 호환표(정확 포지션 → 인접 순)라 부포·포지션 거리 단계가 반영되지 않았다.

[v4 순서] (신민용 확정 설계 그대로)
  1. 그 나라 국적자 전원의 선발 점수 — intl_engine.intl_selection_score_v3 그대로(AI·내 선수 같은 공식).
     생산성·최근폼은 출전 수 표본 보정(출전 ÷ (출전 + V4_SHRINK_K))을 양방향으로 건다(intl_engine 쪽).
  2. 넓은 후보군 — 포지션별 숏리스트(정원 없음) 중 점수 상위 V4_POOL_TOP명(+ GK 상위 V4_POOL_GK명).
  3. 20개 포메이션 각각의 베스트 11 — 유효 가치 = 선발 점수 × 자리 적합도(formation_logic._slot_value ÷ OVR —
     AI 선발 전력과 같은 규칙: 부포 −1, 거리 단계 3/8/15/25%, 주발), 한 선수 한 자리(최적 배정).
  4. 포메이션 = 베스트 11 전력 85% + 감독 성향 15%(랜덤 없음). 전력 점수는 "최고 포메이션 대비 V4_FIT_BAND점
     이내를 0~1로" 정규화 — 성향은 전력 차가 ≈0.5점 이하일 때만 순서를 바꾼다.
  5. 주전 11명 확정.
  6. 백업 — 주전을 뺀 선수로 10개 필드 자리에 **동시에** 배정(한 선수 한 자리 — LB·RB 겸용 선수가 두 자리의
     백업으로 겹치지 않는다). 적합도 V4_COVER_FIT_MIN 미만은 큰 감점이라, 대체 가능한 선수가 있으면 그 선수가
     먼저 간다(좌우는 자리별 적합도로 판단 — 반대쪽 풀백·윙어는 거리 단계 감점). GK는 총 3명.
  7. 남은 자리는 포지션 무관 선발 점수 순(GK 3명 상한) — ST 3~4명도 가능.
"""
import math

V4_ENABLED = True
V4_SHRINK_K = 10.0          # 생산성·최근폼 표본 보정: 출전 ÷ (출전 + K) — 5경기 33% / 15경기 60% / 30경기 75%
V4_POOL_TOP = 60            # 포메이션 비교 후보(점수 상위)
V4_POOL_GK = 4              # 포메이션 비교 후보에 넣는 GK 상위
V4_BACKUP_TOP = 120         # 백업 배정 후보(점수 상위)
V4_FIT_BAND = 3.0           # 전력 정규화 폭(점)
V4_STYLE_W = 0.15           # 감독 성향 비중(전력 0.85)
V4_COVER_FIT_MIN = 0.92     # 백업으로 인정하는 자리 적합도(인접 역할 −8%까지)
V4_COVER_MISS_PEN = 20.0    # 그 미만으로 배정될 때 감점(점) — 대체 가능한 선수가 있으면 그쪽이 먼저
V4_GK_TOTAL = 3
MY_ID = -1
# [2026-11 v4.1] 포메이션 전용 자리 — 등록 포지션에 LM·RM·LWB·RWB가 없어서(전 세계 선수 0명) 이 자리가 있는
# 포메이션(4-4-2·4-5-1·3백 계열)은 누가 서도 거리 단계 감점(LW→LM 3%)을 받았다 → 같은 선수로 4-2-4가 4-4-2를
# 항상 이겼다(v4 3시즌 실측: 4-2-4 최다 160회). 이 자리는 같은 역할의 등록 포지션 자리로 본다(감점 없음).
V4_SLOT_EQUIV = {"LM": "LW", "RM": "RW", "LWB": "LB", "RWB": "RB"}
# [v4.2 ② — 2026-11-19 신민용 결정: 미적용] True = v4.1 그대로(LM·RM·LWB·RWB를 같은 역할 등록 포지션 자리로 보고
# 감점 없음). False로 바꾸면 경기 엔진과 같은 감점(LW→LM 3% 등) — 2시즌 검증에서 등록 LM·RM 선수가 0명이라
# LM·RM 포메이션이 거의 사라지고 4-2-3-1·4-2-4로 쏠려서 보류. 엔진 쪽 재설계(LW↔LM 감점)는 추후 별도 검토.
V4_SLOT_EQUIV_NO_PENALTY = True
# [2026-11 v4.1] 남은 명단 — 선수마다 "포메이션 자리 라벨 중 가장 높은 효용"(유효 가치 − 깊이 감점)으로 비교한다.
# 깊이 = 그 라벨을 맡을 수 있는 명단 인원 — 주 역할(적합도 최고 라벨)이면 1명, 다른 라벨도 V4_COVER_FIT_MIN 이상으로
# 볼 수 있으면(적합 ≥ V4_DEPTH_SECOND_FIT — 부포지션·한 단계 거리) 그 라벨에 V4_DEPTH_SECOND명으로 센다
# (이미 확보된 멀티 포지션 백업을 센다; ST→CAM 같은 두 단계 거리 0.92는 백업 공백 판정에만 쓰고 깊이엔 안 센다).
# 라벨 자리 수 × V4_DEPTH_FREE(주전+백업 1명)까지는 감점 없고, 넘는 1명당 V4_DEPTH_PEN점.
# 정원이 아니라 효용: 그 자리 유효 가치가 감점보다 높으면 더 들어온다(고정 정원 없음).
V4_DEPTH_PEN = 3.0
V4_DEPTH_FREE = 2.0
V4_DEPTH_SECOND = 0.5
V4_DEPTH_SECOND_FIT = 0.95
# [2026-11-19 v4.2, 신민용 확정 ①] 26인 구성과 실제 자리 배치의 기준을 분리한다.
#   · 26인 구성(build_squad 안의 포메이션별 베스트 11·백업·남은 자리): 선발 점수(폼·출전 등) × 자리 적합도 — 예전 그대로.
#   · 실제 경기 배치(intl_engine이 부르는 choose_formation·best_xi — 대회 포메이션과 주전 11명·자리):
#     경기 엔진 기준 OVR × 자리 적합도(= formation_logic._slot_value, 팀 전력과 같은 값). 적합도 감점은 그대로라
#     원래 포지션을 무시하고 OVR만 높은 선수를 넣지 않는다.
#   예(2시즌 실측): OVR 85 CAM이 CB(유효 72.3)로 서고 OVR 77 CB가 벤치에 남던 배치 — 점수가 낮은 CB가 밀렸었다.
V4_PLACE_BASIS = "engine"   # 실제 배치 기준: "engine"(= OVR × 적합도, 경기 엔진과 같다 — "ovr"도 같은 뜻) / "score"(v4.1 예전 동작)


# ── 자리 적합도 ──────────────────────────────────────────────────────────
def fit_ratio(c, slot):
    """자리 적합도(0~1) — formation_logic._slot_value ÷ OVR. GK 자리는 GK만, 필드 자리는 필드 선수만."""
    pos = c.get("position") or ""
    if slot == "GK":
        return 1.0 if pos == "GK" else 0.0
    if pos == "GK":
        return 0.0
    ovr = float(c.get("ovr") or 0.0)
    if ovr <= 0:
        return 0.0
    # [v4.2 ② 스위치] V4_SLOT_EQUIV_NO_PENALTY(지금 True — v4.1 그대로)면 LM·RM·LWB·RWB를 같은 역할 등록 포지션
    # 자리로 봐서 감점 없이 잰다. False면 경기 엔진(_slot_value)과 같은 감점. 위 상수 주석 참고.
    _eq = V4_SLOT_EQUIV.get(slot)
    if V4_SLOT_EQUIV_NO_PENALTY and _eq and (pos == _eq or (c.get("sub_pos") or "") == _eq):
        slot = _eq
    import formation_logic as _fl
    try:
        return max(0.0, float(_fl._slot_value(c, slot)) / ovr)
    except Exception:
        return 1.0 if pos == slot else 0.8


def value(c, slot, basis="score"):
    """유효 가치 = 기준값 × 자리 적합도. basis "score" = 선발 점수(26인 구성), "ovr" = OVR(실제 배치·경기 엔진)."""
    base = c.get("ovr") if basis in ("engine", "ovr") else c.get("_score")
    return float(base or 0.0) * fit_ratio(c, slot)


# ── 최적 배정(헝가리안, 행 ≤ 열, 비용 최소) ──────────────────────────────────
def _hungarian(cost):
    """cost[n][m] (n ≤ m) → 행 i에 배정된 열 번호 목록. e-maxx 구현(O(n²m))."""
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            ci = cost[i0 - 1]
            ui0 = u[i0]
            for j in range(1, m + 1):
                if not used[j]:
                    cur = ci[j - 1] - ui0 - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    res = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            res[p[j] - 1] = j - 1
    return res


def best_xi(slots, pool, basis=None):
    """slots(자리 라벨 11개)에 pool을 최적 배정 → ([(slot, cand)], 유효 가치 평균). 빈 자리는 None.
    basis: None이면 V4_PLACE_BASIS(실제 배치 — intl_engine 호출), build_squad 안에서는 "score"."""
    basis = basis or V4_PLACE_BASIS
    if not pool:
        return [(s, None) for s in slots], 0.0
    BIG = 1e6
    cost = [[(-value(c, s, basis) if fit_ratio(c, s) > 0 else BIG) for c in pool] for s in slots]
    if len(pool) < len(slots):
        cost = [row + [BIG] * (len(slots) - len(pool)) for row in cost]
    asg = _hungarian(cost)
    out, tot, n = [], 0.0, 0
    for i, s in enumerate(slots):
        j = asg[i]
        c = pool[j] if (0 <= j < len(pool) and cost[i][j] < BIG) else None
        out.append((s, c))
        if c is not None:
            tot += value(c, s, basis)
            n += 1
    return out, (tot / len(slots) if slots else 0.0)


# ── 포메이션 선택 ─────────────────────────────────────────────────────────
def choose_formation(pool, style=None, basis=None):
    """(포메이션 이름, {이름: 베스트11 평균 가치}, 베스트11 배정) — 전력 85% + 감독 성향 15%, 랜덤 없음.
    basis: None이면 V4_PLACE_BASIS(실제 대회 포메이션 — intl_engine 호출), build_squad 안에서는 "score"."""
    from constants import FORMATION_SLOTS
    import formation_logic as _fl
    basis = basis or V4_PLACE_BASIS
    xi_by, asg_by = {}, {}
    for name, slots in FORMATION_SLOTS.items():
        asg, mean = best_xi(list(slots), pool, basis)
        xi_by[name], asg_by[name] = mean, asg
    if not xi_by:
        return "4-4-2", {}, []
    best = max(xi_by.values())
    st = style or {}

    def _score(name):
        squad_fit = max(0.0, min(1.0, 1.0 - (best - xi_by[name]) / V4_FIT_BAND))
        try:
            sfit = _fl.manager_style_fit(name, style_attack=st.get("style_attack"),
                                         style_buildup=st.get("style_buildup"),
                                         style_press=st.get("style_press"))
        except Exception:
            sfit = 0.5
        return (1.0 - V4_STYLE_W) * squad_fit + V4_STYLE_W * sfit

    name = max(xi_by, key=lambda n_: (_score(n_), xi_by[n_], n_))
    return name, xi_by, asg_by[name]


# ── 26인 구성 ─────────────────────────────────────────────────────────────
def build_squad(cands, size, style=None, force_ids=()):
    """cands: 선발 점수(_score)가 붙은 후보 dict 목록(그 나라 국적자, 내 선수 포함 가능).
    반환 dict: formation, xi(=[(slot, cand)]), backups(=[(slot, cand)]), squad(cand 목록, size명 이하),
               xi_by(포메이션별 베스트11 평균 가치).
    force_ids: 반드시 넣을 id(이미 발탁된 내 선수) — 빠지면 마지막으로 채운 선수와 바꾼다."""
    if not cands:
        return {"formation": "4-4-2", "xi": [], "backups": [], "squad": [], "xi_by": {}}
    ranked = sorted(cands, key=lambda c: (-float(c.get("_score") or 0.0), c.get("id") or 0))
    gks = [c for c in ranked if (c.get("position") or "") == "GK"]
    pool = ranked[:V4_POOL_TOP]
    _pid = {c["id"] for c in pool}
    pool += [g for g in gks[:V4_POOL_GK] if g["id"] not in _pid]
    for fid in force_ids:
        if fid not in {c["id"] for c in pool}:
            pool += [c for c in ranked if c["id"] == fid]
    # [v4.2] 26인 구성은 선발 점수 기준 그대로(실제 대회 포메이션·주전은 intl_engine이 OVR 기준으로 다시 정한다)
    formation, xi_by, xi = choose_formation(pool, style, basis="score")
    taken = {c["id"] for _s, c in xi if c is not None}
    squad = [c for _s, c in xi if c is not None]
    # 백업 — 필드 자리 10개 동시 배정(한 선수 한 자리)
    field_slots = [s for s, _c in xi if s != "GK"]
    rest = [c for c in ranked[:V4_BACKUP_TOP] if c["id"] not in taken and (c.get("position") or "") != "GK"]
    for fid in force_ids:
        if fid not in taken and fid not in {c["id"] for c in rest}:
            rest += [c for c in ranked if c["id"] == fid and (c.get("position") or "") != "GK"]
    backups = []
    if rest and field_slots:
        BIG = 1e6
        cost = []
        for s in field_slots:
            row = []
            for c in rest:
                f = fit_ratio(c, s)
                if f <= 0:
                    row.append(BIG)
                else:
                    row.append(-(float(c.get("_score") or 0.0) * f) + (V4_COVER_MISS_PEN if f < V4_COVER_FIT_MIN else 0.0))
            cost.append(row)
        if len(rest) < len(field_slots):
            cost = [r + [BIG] * (len(field_slots) - len(rest)) for r in cost]
        asg = _hungarian(cost)
        for i, s in enumerate(field_slots):
            j = asg[i]
            if 0 <= j < len(rest) and cost[i][j] < BIG:
                backups.append((s, rest[j]))
    for _s, c in backups:
        if c["id"] not in taken and len(squad) < size:
            squad.append(c)
            taken.add(c["id"])
    # GK 총 3명
    n_gk = sum(1 for c in squad if (c.get("position") or "") == "GK")
    for g in gks:
        if n_gk >= V4_GK_TOTAL or len(squad) >= size:
            break
        if g["id"] not in taken:
            squad.append(g)
            taken.add(g["id"])
            n_gk += 1
    # 남은 자리 — [v4.1] 라벨별 효용(유효 가치 − 깊이 감점) 최고값 순. 고정 정원 없음(위 V4_DEPTH_PEN 주석).
    labels = {}
    for _s, _c in xi:
        if _s != "GK":
            labels[_s] = labels.get(_s, 0) + 1
    cover = {L: 0.0 for L in labels}
    _lab_order = list(labels)

    def _cover_inc(c):
        """이 선수가 라벨별로 더하는 깊이 — 주 역할 1, 다른 소화 가능 라벨 V4_DEPTH_SECOND."""
        fs = {L: fit_ratio(c, L) for L in _lab_order}
        pos = c.get("position") or ""
        prim = max(_lab_order, key=lambda L: (round(fs[L], 3), L == pos or V4_SLOT_EQUIV.get(L) == pos,
                                              -_lab_order.index(L)))
        return {L: (1.0 if L == prim else (V4_DEPTH_SECOND if fs[L] >= V4_DEPTH_SECOND_FIT else 0.0))
                for L in _lab_order}, fs

    def _add_cover(c):
        inc, _fs = _cover_inc(c)
        for L in _lab_order:
            cover[L] += inc[L]
    for c in squad:
        if (c.get("position") or "") != "GK":
            _add_cover(c)
    pool_fill = [c for c in ranked[:V4_BACKUP_TOP + 40] if c["id"] not in taken
                 and (c.get("position") or "") != "GK"]
    while len(squad) < size and pool_fill:
        best = None
        for c in pool_fill:
            inc, fs = _cover_inc(c)
            for L, n_L in labels.items():
                f = fs[L]
                if f <= 0:
                    continue
                over = max(0.0, cover[L] + inc[L] - V4_DEPTH_FREE * n_L)
                u = float(c.get("_score") or 0.0) * f - V4_DEPTH_PEN * over
                if best is None or u > best[0]:
                    best = (u, c)
        if best is None:
            break
        _u, c = best
        squad.append(c)
        taken.add(c["id"])
        _add_cover(c)
        pool_fill.remove(c)
    for c in ranked:          # (필드 후보가 바닥난 소국) — 남으면 예전처럼 점수 순
        if len(squad) >= size:
            break
        if c["id"] in taken:
            continue
        if (c.get("position") or "") == "GK" and n_gk >= V4_GK_TOTAL:
            continue
        squad.append(c)
        taken.add(c["id"])
        if (c.get("position") or "") == "GK":
            n_gk += 1
    # 꼭 넣어야 할 선수(이미 발탁된 내 선수)
    for fid in force_ids:
        if fid in taken:
            continue
        add = next((c for c in ranked if c["id"] == fid), None)
        if add is None:
            continue
        protect = {c["id"] for _s, c in xi if c is not None} | {c["id"] for _s, c in backups}
        drop = next((c for c in reversed(squad) if c["id"] not in protect and c["id"] not in force_ids), None)
        if drop is not None and len(squad) >= size:
            squad.remove(drop)
        squad.append(add)
        taken.add(fid)
    return {"formation": formation, "xi": xi, "backups": backups, "squad": squad[:max(size, len(force_ids))],
            "xi_by": xi_by}


def coverage_report(xi, squad):
    """검증용 — 주전 외 선수로 필드 10자리에 동시 백업 배정을 했을 때 적합도 V4_COVER_FIT_MIN 이상 백업이 없는 자리."""
    ids = {c["id"] for _s, c in xi if c is not None}
    rest = [c for c in squad if c["id"] not in ids and (c.get("position") or "") != "GK"]
    slots = [s for s, _c in xi if s != "GK"]
    if not slots:
        return []
    if not rest:
        return list(slots)
    BIG = 1e6
    cost = [[(-(1.0 if fit_ratio(c, s) >= V4_COVER_FIT_MIN else 0.0) * 100 - fit_ratio(c, s)) for c in rest]
            for s in slots]
    if len(rest) < len(slots):
        cost = [r + [BIG] * (len(slots) - len(rest)) for r in cost]
    asg = _hungarian(cost)
    miss = []
    for i, s in enumerate(slots):
        j = asg[i]
        if not (0 <= j < len(rest)) or fit_ratio(rest[j], s) < V4_COVER_FIT_MIN:
            miss.append(s)
    return miss