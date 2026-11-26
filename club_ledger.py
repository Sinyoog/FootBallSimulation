# -*- coding: utf-8 -*-
"""[2026-11-21 클럽 선수단 운영 개편 — 검증 중] 클럽 공통 수요 장부.

모드 constants.CLUB_LEDGER_MODE: "off"(기존) / "world"(월드 생성만) / "full"(통합). 검증용 임시 스위치 —
채택하면 스위치 없이 확정, 폐기하면 이 모듈과 호출부를 제거한다.

정의(2026-11-21 확정)
  · 칸 10종: GK CB LB RB CDM CM CAM LW RW FW (LM→LW, LWB→LB, ST·CF→FW)
  · 목표 인원 T(C안): SS·S·A급 1부 26 · B~F급 1부 25 · 2부 25 · 3부 24 · 4부 이하 23 · 산하 유스팀 26
  · 목표표 table(f, N): squad_targets GK 3명 표(대안 B: 3-5-2·5-3-2·3-4-1-2만 FW 2단계 +1 없음) + 측면 규칙(쓰는 측면 좌우 각 2,
    안 쓰는 측면 좌우 각 1 — 중앙 여유 칸에서 옮김) + 단조성. 판정은 항상 table(f, T).
  · 3단계: L1 선발선 / L2 규칙선(L1 + 측면 2/1 + GK 3) / L3 목표선. 보유: 현재 명단, 주포·부포 1인 1칸,
    L1 → L2 → L3 순서 최대 배정. 부족량 d_k = Σ max(0, L_k[c] − h[c]).
  · 자유 판매 선수: 빼도 d1·d2·d3가 하나도 늘지 않는 선수.
  · 판매 허용: 빼서 부족량이 늘면 같은 거래에서 ① 이적 대체자(기준 OVR = min(판 선수, 그 칸 주전 최저) − 3 이상,
    자유 판매 선수, 보충 완결) ② (여름·깊이 부족만) 실제 생성한 신인 1명(같은 기준, 한 번만 생성) — 둘 다 안 되면 불가.
GK는 GK 3명 정책이 따로 다룬다 — 장부는 GK 칸을 대체자·신인 칸으로 고르지 않는다.
"""
import bisect
import collections
import functools

import constants as _C
import squad_targets as _ST
import virtual_youth as _VY
from constants import FORMATION_SLOTS

CELLS = _ST.TARGET_POSITIONS
FIELD = tuple(p for p in CELLS if p != "GK")
SIDE_CELLS = ("LB", "RB", "LW", "RW")
CENTRAL = ("CB", "CM", "CDM", "FW", "CAM")
MAX_N = _ST.MAX_SQUAD
BASE_N = 22
SQUAD_CAP = 26
M_REPL = 3                     # 기준 OVR 여유(확정 2026-11-21)
CHAIN_MAX_P = 30               # [2026-11-22 연쇄 이적] 판매 1건에서 살펴보는 P 후보 수(기준 OVR에 가까운 순)
VET_AGE = 35                   # 대체자·보충 영입에서 뺀다(노장 규칙 1을 보수적으로)
CELL_TO_POS = {"GK": "GK", "CB": "CB", "LB": "LB", "RB": "RB", "CDM": "CDM", "CM": "CM",
               "CAM": "CAM", "LW": "LW", "RW": "RW", "FW": "ST"}
LOG = collections.Counter()    # 계측(동작과 무관). 측정 러너가 읽고 비운다.


def r1():
    """[2026-11-25 장부 R1] constants.SUB_POS_LEDGER_R1 — 꺼져 있으면 아래 R1 경로는 모두 무동작(예전과 같다)."""
    return bool(getattr(_C, "SUB_POS_LEDGER_R1", False))


def newbie_lvl_ok(dep):
    """판매 신인을 허용하는 부족 단계 — 기본은 깊이 부족(L3)만. [2026-11-25 장부 R1 보완 (나)] R1과
    SUB_POS_LEDGER_R1_L2_NEWBIE가 켜져 있으면 주포 부족 게이트의 L2도 허용."""
    if dep["lvl"] == 3:
        return True
    return (dep["lvl"] == 2 and bool(dep.get("main")) and r1()
            and bool(getattr(_C, "SUB_POS_LEDGER_R1_L2_NEWBIE", False)))


def main_cell(pos):
    """등록 포지션의 장부 칸(부포 무시)."""
    return _ST.REGISTERED_TO_TARGET.get(pos)
STATS_SRC = collections.Counter()   # [2026-11-22 1단계] 공급원 후보 탐색 결과(계측)
HANDOFF = {}                   # 여름 이적시장 장부 → 끌어올리기(같은 오프시즌, 바로 다음 경로)


def mode():
    return str(getattr(_C, "CLUB_LEDGER_MODE", "off") or "off")


def world_on():
    return mode() in ("world", "full")


def full():
    return mode() == "full"


def target_size(grade, tier, youth=False):
    if youth:
        return 26
    t = tier or 1
    if t == 1:
        return 26 if grade in ("SS", "S", "A") else 25
    if t == 2:
        return 25
    if t == 3:
        return 24
    return 23


def fm(f):
    return f if f in FORMATION_SLOTS else "4-4-2"


@functools.lru_cache(maxsize=None)
def starters(f):
    return dict(_ST.starter_counts(fm(f)))


@functools.lru_cache(maxsize=None)
def level2(f):
    s = starters(f)
    out = dict(s)
    out["GK"] = 3
    for c in SIDE_CELLS:
        out[c] = 2 if s[c] > 0 else 1
    return out


# [2026-11-21 확정 — 대안 B] 무윙 투톱 3개 포메이션만 FW 2단계 +1을 뺀다(T≥25 FW 5 → 4). 나머지 포메이션은
# 모든 인원에서 기존 GK 3명 목표표 그대로(결정표 "나머지 포메이션 기존 목표표 유지").
ALT_B_FORMS = frozenset(("3-5-2", "5-3-2", "3-4-1-2"))


def _raw(f, n):
    s = starters(f)
    t = (dict(_ST.targets_gk3_b(fm(f), n)) if fm(f) in ALT_B_FORMS
         else dict(_ST._targets_cached(fm(f), n, True)))
    floor = {c: (s[c] + 1 if s[c] > 0 else 0) for c in CENTRAL}
    side = {c: (2 if s[c] > 0 else 1) for c in SIDE_CELLS}
    for c in SIDE_CELLS:
        while t[c] < side[c]:
            donors = [d for d in CENTRAL if t[d] - 1 >= max(floor[d], s[d])]
            if not donors:
                break
            d = max(donors, key=lambda x: (t[x] - floor[x], -CENTRAL.index(x)))
            t[d] -= 1
            t[c] += 1
    return t


@functools.lru_cache(maxsize=None)
def _table(f):
    f = fm(f)
    tab = {BASE_N: _raw(f, BASE_N)}
    for n in range(BASE_N + 1, MAX_N + 1):
        prev, raw = tab[n - 1], _raw(f, n)
        c = max(CELLS, key=lambda x: (raw[x] - prev[x], -CELLS.index(x)))
        cur = dict(prev)
        cur[c] += 1
        tab[n] = cur
    s = starters(f)
    for n in range(BASE_N - 1, 10, -1):
        nxt, raw = tab[n + 1], _raw(f, n)
        cand = [x for x in CELLS if nxt[x] > s[x] and not (x == "GK" and nxt[x] <= 1)]
        c = max(cand, key=lambda x: (nxt[x] - raw[x], CELLS.index(x)))
        cur = dict(nxt)
        cur[c] -= 1
        tab[n] = cur
    return tab


def table(f, n):
    return dict(_table(fm(f))[max(11, min(int(n), MAX_N))])


@functools.lru_cache(maxsize=None)
def levels(f, T):
    return (starters(f), level2(f), table(f, T))


def cells_of(pos, sub):
    p = _ST.REGISTERED_TO_TARGET.get(pos)
    if sub.__class__ is tuple:
        # [2026-11-25 SUB_POS_V2] 부포 2개 — 주포와 다른 칸만(같은 칸 부포는 인원으로 안 센다). 0칸 None, 1칸 문자열
        # (부포 1개와 같은 값·같은 경로), 2칸 튜플.
        out = []
        for x in sub:
            s = _ST.SLOT_TO_TARGET.get(x) if x else None
            if p and s and s != p and s not in out:
                out.append(s)
        return p, (None if not out else (out[0] if len(out) == 1 else tuple(out)))
    s = _ST.SLOT_TO_TARGET.get(sub) if sub else None
    return p, (s if (p and s and s != p) else None)


def cell_list(pos, sub):
    """[2026-11-25 SUB_POS_V2] 그 선수가 설 수 있는 칸들(주포 칸, 부포 칸...) — 부포가 문자열이면 예전 cells_of와 같은 2개."""
    a, b = cells_of(pos, sub)
    return ((a,) + b) if b.__class__ is tuple else (a, b)


def _moves(prim_i, alt_i, cur):
    """[2026-11-25 SUB_POS_V2] 지금 칸 cur에서 옮겨 갈 수 있는 칸들 — 부포 1개면 예전 _other와 같은 한 칸."""
    if alt_i.__class__ is tuple:
        return [x for x in (prim_i,) + alt_i if x and x != cur]
    return (alt_i if cur == prim_i else prim_i,)


def assign(entries, lv):
    """entries [(등록 포지션, 부포|None)] → (cnt, assign 리스트, (d1,d2,d3)). L1 → L2 → L3 사전식 최대 배정."""
    prim, alt = [], []
    for p, s in entries:
        a, b = cells_of(p, s)
        prim.append(a)
        alt.append(b)
    return assign_ab(prim, alt, lv)


def assign_ab(prim, alt, lv):
    """assign과 같다 — 칸이 이미 풀린 (주포 칸 목록, 부포 칸 목록)을 받는다(반복 계산용)."""
    asg = list(prim)
    cnt = collections.Counter(t for t in asg if t)
    if any(alt):
        at = {t: [] for t in CELLS}
        for i, t in enumerate(asg):
            if t:
                at[t].append(i)

        for tg in lv:
            while True:
                sources = [t for t in CELLS if cnt[t] > tg.get(t, 0)]
                if not sources or not any(cnt[t] < tg.get(t, 0) for t in CELLS):
                    break
                parent = {}
                queue = list(sources)
                seen = set(sources)
                goal = None
                qi = 0
                while qi < len(queue) and goal is None:
                    u = queue[qi]
                    qi += 1
                    for i in at[u]:
                        for v in _moves(prim[i], alt[i], asg[i]):
                            if not v or v in seen:
                                continue
                            seen.add(v)
                            parent[v] = (u, i)
                            if cnt[v] < tg.get(v, 0):
                                goal = v
                                break
                            queue.append(v)
                        if goal is not None:
                            break
                if goal is None:
                    break
                v = goal
                while v in parent:
                    u, i = parent[v]
                    at[u].remove(i)
                    at[v].append(i)
                    asg[i] = v
                    cnt[u] -= 1
                    cnt[v] += 1
                    v = u
    d = tuple(sum(max(0, tg.get(c, 0) - cnt[c]) for c in CELLS) for tg in lv)
    return cnt, asg, d


def worse(d_new, d_old):
    return any(x > y for x, y in zip(d_new, d_old))


def bench_positions(f, T):
    """[월드 생성] 벤치 등록 포지션 목록(T − 11명) — table(f, T) − 선발. 순서는 기존 규칙 배열처럼 백업 1번째부터
    칸별로 돌아가며(CB → 중원(선발 많은 순) → FW → LB → RB → LW → RW → 선발에 없는 중원), 3번째 GK는 맨 끝.
    FW 칸은 ST로 등록한다(기존 월드 생성과 같다)."""
    f = fm(f)
    s = starters(f)
    t = table(f, T)
    left = {c: t[c] - s[c] for c in CELLS}
    mids_present = sorted([m for m in ("CDM", "CM", "CAM") if s[m] > 0], key=lambda m: (-s[m], ("CDM", "CM", "CAM").index(m)))
    mids_absent = [m for m in ("CDM", "CM", "CAM") if s[m] == 0]
    order = ["CB"] + mids_present + ["FW", "LB", "RB", "LW", "RW"] + mids_absent
    out = []
    gk_bench = left["GK"]
    if gk_bench >= 1:
        out.append("GK")
    while any(left[c] > 0 for c in order):
        for c in order:
            if left[c] > 0:
                out.append(CELL_TO_POS[c])
                left[c] -= 1
    out.extend(["GK"] * max(0, gk_bench - 1))
    return out


# ── 팀 장부 ───────────────────────────────────────────────────────────────
class TeamBook:
    """한 팀 장부. 부포로 다른 칸에 설 수 있는 선수가 없으면(_alt_n == 0) 배정은 주포 칸 그대로라 칸 인원을 바로
    더하고 빼서 계산한다(일반 배정과 결과가 같다 — 단위 테스트 T7)."""
    __slots__ = ("tid", "f", "T", "players", "ver", "_cv", "_res", "_fv", "_free", "_cnt", "_alt_n")

    def __init__(self, tid, f, T):
        self.tid, self.f, self.T = tid, fm(f), T
        self.players = {}
        self.ver = 0
        self._cv = self._fv = -1
        self._res = self._free = None
        self._cnt = collections.Counter()
        self._alt_n = 0

    def _put(self, pid, rec):
        self.players[pid] = rec
        if rec[3]:
            self._cnt[rec[3]] += 1
        if rec[4]:
            self._alt_n += 1

    def add(self, pid, pos, sub, ovr):
        a, b = cells_of(pos, sub)
        old = self.players.get(pid)
        if old is not None:
            self._drop(pid, old)
        self._put(pid, (pos, sub, ovr or 0, a, b))
        self.ver += 1

    def _drop(self, pid, rec):
        del self.players[pid]
        if rec[3]:
            self._cnt[rec[3]] -= 1
        if rec[4]:
            self._alt_n -= 1

    def remove(self, pid):
        rec = self.players.get(pid)
        if rec is not None:
            self._drop(pid, rec)
            self.ver += 1

    def _d_from(self, cnt):
        return tuple(sum(max(0, tg.get(c, 0) - cnt[c]) for c in CELLS) for tg in self.lv())

    def lv(self):
        return levels(self.f, self.T)

    def calc(self):
        if self._cv != self.ver:
            ids = sorted(self.players)
            pl = self.players
            if self._alt_n == 0:
                cnt = collections.Counter({c: n for c, n in self._cnt.items() if n})
                self._res = (ids, cnt, {i: pl[i][3] for i in ids}, self._d_from(cnt))
            else:
                cnt, asg, d = assign_ab([pl[i][3] for i in ids], [pl[i][4] for i in ids], self.lv())
                self._res = (ids, cnt, dict(zip(ids, asg)), d)
            self._cv = self.ver
        return self._res

    def d(self):
        return self.calc()[3]

    def dm(self):
        """[2026-11-25 장부 R1] 주포만 센 (d1, d2, d3) — 전문 선수 부족 진단(부포는 세지 않는다)."""
        return self._d_from(self._cnt)

    def m_deficits(self):
        """[2026-11-25 장부 R1] 주포만 센 칸 부족 [(단계, 부족량, 칸)] — deficits()와 같은 정렬. GK 제외."""
        out = []
        for c in FIELD:
            for k, tg in enumerate(self.lv(), 1):
                if self._cnt[c] < tg.get(c, 0):
                    out.append((k, tg[c] - self._cnt[c], c))
                    break
        out.sort(key=lambda x: (x[0], -x[1], CELLS.index(x[2])))
        return out

    def fresh_d(self):
        """증분 계산을 쓰지 않고 명단 전체로 다시 배정한 (d1, d2, d3) — 보충 패스 단계별 재확인용."""
        ids = sorted(self.players)
        pl = self.players
        return assign_ab([pl[i][3] for i in ids], [pl[i][4] for i in ids], self.lv())[2]

    def n_gk(self):
        return sum(1 for v in self.players.values() if v[0] == "GK")

    def d_change(self, drop=(), add=()):
        pl = self.players
        adds = [cells_of(p, s) for p, s in add]
        if self._alt_n == 0 and not any(b for _a, b in adds) and all(i in pl for i in drop):
            cnt = collections.Counter(self._cnt)
            for i in drop:
                if pl[i][3]:
                    cnt[pl[i][3]] -= 1
            for a, _b in adds:
                if a:
                    cnt[a] += 1
            return cnt, None, self._d_from(cnt)
        ids = [i for i in sorted(pl) if i not in drop]
        prim = [pl[i][3] for i in ids]
        alt = [pl[i][4] for i in ids]
        for a, b in adds:
            prim.append(a)
            alt.append(b)
        return assign_ab(prim, alt, self.lv())

    def free_ids(self):
        """빼도 d1·d2·d3가 안 느는 선수. 최대 배정에서 한 명을 빼면 그 칸이 하나 빈다 — 목표 초과 칸에서 출발해
        (주포↔부포 이동으로) 그 칸까지 가는 증가 경로가 있으면 다시 채워져 어느 단계도 안 늘고, 없으면 목표선
        부족이 1 늘어난다(최대 유량 성질). 그래서 '초과 칸 + 초과 칸에서 도달 가능한 칸'의 선수가 자유 판매 선수다.
        출발 칸은 목표선(L3) 초과라 옮겨도 L1·L2 아래로 내려가지 않는다. 팀당 BFS 1번(전수 확인과 같음 — 단위 테스트)."""
        if self._fv == self.ver:
            return self._free
        ids, cnt, amap, d = self.calc()
        tg3 = self.lv()[2]
        reach = {c for c in CELLS if cnt[c] > tg3.get(c, 0)}
        edges = collections.defaultdict(set)
        pl = self.players
        for i in ids:
            a, b = pl[i][3], pl[i][4]
            u = amap[i]
            if u is None:
                continue
            for v in _moves(a, b, u):
                if v:
                    edges[u].add(v)
        queue = list(reach)
        while queue:
            u = queue.pop()
            for v in edges.get(u, ()):
                if v not in reach:
                    reach.add(v)
                    queue.append(v)
        out = {i for i in ids if amap[i] in reach}
        if r1():
            # [2026-11-25 장부 R1] 자기 주포 칸에 L3 목표보다 주포가 많은 선수만 — 부포 선수가 메워 준다는 이유로 전문 선수를
            # 자유 판매로 보지 않는다(부포 선수 본인도 자기 주포 칸에 여유가 있어야 자유).
            mc = self._cnt
            out = {i for i in out if pl[i][3] and mc[pl[i][3]] > tg3.get(pl[i][3], 0)}
        self._free, self._fv = out, self.ver
        return out

    def starter_min(self, cell):
        s = starters(self.f).get(cell, 0)
        if s <= 0:
            return None
        ids, cnt, amap, d = self.calc()
        if r1():
            # [2026-11-25 장부 R1] 선발 자리는 주포 우선 — 주포 OVR 높은 순, 모자라면 부포 선수(OVR 높은 순)
            ov = [o for _m, o in sorted(((self.players[i][3] != cell, -self.players[i][2]) for i in ids
                                         if amap[i] == cell))]
            ov = [-o for o in ov]
        else:
            ov = sorted((self.players[i][2] for i in ids if amap[i] == cell), reverse=True)
        if not ov:
            return None
        return ov[min(s, len(ov)) - 1]

    def xi_min(self):
        ids, cnt, amap, d = self.calc()
        out = None
        for c in CELLS:
            s = starters(self.f).get(c, 0)
            if s <= 0:
                continue
            if r1():   # [2026-11-25 장부 R1] 선발 자리는 주포 우선
                ov = [-o for _m, o in sorted(((self.players[i][3] != c, -self.players[i][2]) for i in ids
                                              if amap[i] == c))][:s]
            else:
                ov = sorted((self.players[i][2] for i in ids if amap[i] == c), reverse=True)[:s]
            for o in ov:
                out = o if out is None else min(out, o)
        return out or 0

    def roles(self):
        """[2026-11-22 연쇄 이적 계측] {선수: 'S'(주전)|'R'(로테이션)|'W'(대기)} — 최대 배정 칸 안에서 OVR 높은 순으로
        1단계(선발) 인원까지 S, 2단계 인원까지 R, 나머지·배정 없음 W. 동작과 무관(보고용)."""
        ids, cnt, amap, d = self.calc()
        lv = self.lv()
        by = collections.defaultdict(list)
        for i in ids:
            if amap[i]:
                if r1():   # [2026-11-25 장부 R1] 같은 칸 안에서 주포가 먼저 선발·로테이션 자리를 맡는다
                    by[amap[i]].append((self.players[i][3] != amap[i], -self.players[i][2], i))
                else:
                    by[amap[i]].append((-self.players[i][2], i))
        out = {i: "W" for i in ids}
        for c, lst in by.items():
            lst.sort()
            s, r = lv[0].get(c, 0), lv[1].get(c, 0)
            for k, e in enumerate(lst):
                i = e[-1]
                out[i] = "S" if k < s else ("R" if k < r else "W")
        return out

    def deficits(self):
        """[(등급, 부족량, 칸)] — 가장 심한 단계 순, 부족량 큰 순, 칸 순서. GK 제외."""
        ids, cnt, amap, d = self.calc()
        out = []
        for c in FIELD:
            for k, tg in enumerate(self.lv(), 1):
                if cnt[c] < tg.get(c, 0):
                    out.append((k, tg[c] - cnt[c], c))
                    break
        out.sort(key=lambda x: (x[0], -x[1], CELLS.index(x[2])))
        return out


# ── 장부 전체 ─────────────────────────────────────────────────────────────
class Ledger:
    """한 경로(또는 이적시장 1회) 동안 쓰는 장부. DB에서 만들고, 그 경로 안의 이동마다 갱신한다."""

    def __init__(self, c, year, window="summer", lazy=False):
        import time as _t
        _t0 = _t.perf_counter()
        self._lazy = lazy
        self._init(c, year, window)
        LOG["t_init_us"] += int((_t.perf_counter() - _t0) * 1000000)

    def ensure(self, tid):
        """lazy 장부: 그 팀 명단을 처음 쓸 때 DB에서 읽는다(전체 장부와 같은 내용). 전체 장부면 아무것도 안 한다."""
        if not self._lazy or tid in self.books or tid not in self._tinfo:
            return
        import sub_positions as _SP
        f, T = self._tinfo[tid]
        b = self.books[tid] = TeamBook(tid, f, T)
        subs = _SP._load_entries() if _SP.active() else {}   # [2026-11-25 SUB_POS_V2] 2개면 튜플
        for pid, pos, ovr, nat, lty, loan, age in self._c.execute(
                "SELECT id, position, ovr, nationality, last_transfer_year, on_loan_from_team_id, age "
                "FROM ai_players WHERE team_id=? ORDER BY id", (tid,)).fetchall():
            sb = subs.get(pid)
            b.add(pid, pos, sb, ovr or 0)
            self.pinfo[pid] = [tid, pos, sb, ovr or 0, nat or "", lty or 0, loan, age or 25]

    def _init(self, c, year, window):
        from constants import get_country_league_grade, get_ovr_range
        from database import get_foreign_quota_range
        import military_service as _MS
        import sub_positions as _SP
        self.year, self.window = year, window
        self.chain_supply = collections.Counter()   # [2026-11-22 연쇄 이적] 이번 창구에 B팀이 연쇄로 내준 선수 수
        self._chain_ix = None
        mil = set(_MS.get_military_team_ids(c.connection if hasattr(c, "connection") else c))
        try:
            import ai_lifecycle as _AL
            aff = dict(_AL._youth_aff(c)) if _AL._youth_on() else {}
        except Exception:
            aff = {}
        me = None
        try:
            r = c.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
            me = r[0] if r else None
        except Exception:
            me = None
        self.aff, self.me = aff, me
        self.children = collections.defaultdict(list)
        for ch, pa in aff.items():
            self.children[pa].append(ch)
        self.books, self.meta = {}, {}
        gc = {}
        self.vpool = collections.defaultdict(set)
        self.vparent = {}
        self.promo = collections.Counter()
        self._stage1 = False
        if self._lazy:
            self._c = c
            self.pinfo = {}
            self._tinfo = {}
            for tid, f, tier, cn in c.execute(
                    "SELECT t.id, t.formation, t.current_tier, cn.name FROM teams t JOIN leagues l ON t.league_id=l.id "
                    "JOIN countries cn ON l.country_id=cn.id").fetchall():
                if tid in mil:
                    continue
                g = gc.get(cn)
                if g is None:
                    g = gc[cn] = get_country_league_grade(cn)
                self._tinfo[tid] = (fm(f), target_size(g, tier or 1, tid in aff))
            self.by_lid = collections.defaultdict(list)
            self.by_cid = collections.defaultdict(list)
            self._idx = self._idx_nat = None
            self._free, self._free_cell, self._fc = {}, {}, {}
            return
        for tid, f, tier, lid, cn, cid, cont, tname in c.execute(
                "SELECT t.id, t.formation, t.current_tier, t.league_id, cn.name, cn.id, cn.continent, t.name "
                "FROM teams t JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id "
                "ORDER BY t.id").fetchall():
            if tid in mil:
                continue
            g = gc.get(cn)
            if g is None:
                g = gc[cn] = get_country_league_grade(cn)
            tier = tier or 1
            youth = tid in aff
            rng = get_ovr_range(g, tier, cn)
            self.meta[tid] = dict(f=fm(f), tier=tier, lid=lid, cname=cn, cid=cid, cont=cont, tname=tname,
                                  grade=g, youth=youth, ceil=(rng[1] if rng else 43),
                                  floor=(rng[0] if rng else 30),
                                  qhi=get_foreign_quota_range(cn, cont, tier=tier)[1], me=(tid == me))
            self.books[tid] = TeamBook(tid, f, target_size(g, tier, youth))
        subs = _SP._load_entries() if _SP.active() else {}   # [2026-11-25 SUB_POS_V2] 2개면 튜플
        self.pinfo = {}
        _bget = self.books.get
        _sget = subs.get
        _r2t = _ST.REGISTERED_TO_TARGET.get
        _s2t = _ST.SLOT_TO_TARGET.get
        pinfo = self.pinfo
        for pid, tid, pos, ovr, nat, lty, loan, age in c.execute(
                "SELECT id, team_id, position, ovr, nationality, last_transfer_year, on_loan_from_team_id, age "
                "FROM ai_players WHERE team_id IS NOT NULL ORDER BY id").fetchall():
            b = _bget(tid)
            if b is None:
                continue
            sb = _sget(pid)
            o = ovr or 0
            if sb.__class__ is tuple:   # [2026-11-25 SUB_POS_V2] 부포 2개
                a, bb = cells_of(pos, sb)
                b._put(pid, (pos, sb, o, a, bb))
                pinfo[pid] = [tid, pos, sb, o, nat or "", lty or 0, loan, age or 25]
                continue
            a = _r2t(pos)
            sc = _s2t(sb) if sb else None
            b._put(pid, (pos, sb, o, a, (sc if (a and sc and sc != a) else None)))
            pinfo[pid] = [tid, pos, sb, o, nat or "", lty or 0, loan, age or 25]
        for b in self.books.values():
            b.ver += 1
        # [2026-11-22 1단계] 가상 유스 풀(장부 밖) — 모구단별 선수 집합과 그 해 공급원 승격 수
        self.vpool = collections.defaultdict(set)
        self.vparent = {}
        self.promo = collections.Counter()
        self._stage1 = _VY.stage1()
        if self._stage1:
            self.vparent = _VY.parent_map(c)
            self.promo = _VY.promo_counts(c, year)
            if self.vparent:
                _vp = sorted(self.vparent)
                for k in range(0, len(_vp), 500):
                    part = _vp[k:k + 500]
                    ph = ",".join("?" * len(part))
                    for pid, tid, pos, ovr, nat, lty, loan, age in c.execute(
                            "SELECT id, team_id, position, ovr, nationality, last_transfer_year, on_loan_from_team_id, age "
                            f"FROM ai_players WHERE team_id IN ({ph}) ORDER BY id", part).fetchall():
                        pinfo[pid] = [tid, pos, _sget(pid), ovr or 0, nat or "", lty or 0, loan, age or 25]
                        self.vpool[self.vparent[tid]].add(pid)
        self.by_lid = collections.defaultdict(list)
        self.by_cid = collections.defaultdict(list)
        for tid in sorted(self.books):
            m = self.meta[tid]
            self.by_lid[m["lid"]].append(tid)
            self.by_cid[m["cid"]].append(tid)
        self._idx = None
        self._idx_nat = None
        self._free = {}
        self._free_cell = {}
        self._fc = {}

    # ── 조회 ──
    def T(self, tid):
        b = self.books.get(tid)
        return b.T if b else 23

    def size(self, tid):
        b = self.books.get(tid)
        return len(b.players) if b else 0

    def foreign_ct(self, tid):
        from database import is_roster_foreign
        b = self.books[tid]
        k = self._fc.get(tid)
        if k and k[0] == b.ver:
            return k[1]
        cn = self.meta[tid]["cname"]
        n = sum(1 for p in b.players if is_roster_foreign(self.pinfo[p][4], cn))
        self._fc[tid] = (b.ver, n)
        return n

    def is_foreign(self, pid, tid):
        from database import is_roster_foreign
        return is_roster_foreign(self.pinfo[pid][4], self.meta[tid]["cname"])

    def moved(self, pid):
        info = self.pinfo.get(pid)
        return info is not None and (self.year - (info[5] or 0)) < 1

    # ── 판매 판정 ──
    def departure(self, S, X, Y=None):
        """S에서 X가 나가고(Y가 들어오면 Y 엔트리 (pos, sub) 포함) 부족량이 늘면 판정 정보, 아니면 None."""
        self.ensure(S)
        b = self.books.get(S)
        if b is None or X not in b.players:
            return None
        ids, cnt0, amap, d0 = b.calc()
        add = (Y,) if Y is not None else ()
        cnt1, _a1, d1 = b.d_change(drop=(X,), add=add)
        inc = [k for k in range(3) if d1[k] > d0[k]]
        if not inc:
            if r1():
                return self._main_departure(b, X, Y, d0)
            return None
        lvl = inc[0] + 1
        tg = b.lv()[inc[0]]
        cell = next((c for c in CELLS if max(0, tg.get(c, 0) - cnt1[c]) > max(0, tg.get(c, 0) - cnt0[c])), None)
        sc = b.starter_min(cell) if cell else None
        ovx = b.players[X][2]
        ref = min(ovx, sc) if sc is not None else ovx
        if r1():
            # [2026-11-25 장부 R1 보완 — 판매 게이트 우선순위] 장부 부족과 주포 부족이 같이 늘면 주포 부족 게이트가 먼저다
            # (대체 후보는 그 칸 주포만). 주포 수는 X의 주포 칸에서만 줄어드므로 주포 부족 칸 = X의 주포 칸.
            # X가 판매 전 주포 칸이 아닌 칸에 배정돼 있었으면(need_cov) 대체 선수가 X 자리를 그대로 이어받지 못하므로
            # 대체 후보마다 장부 부족이 판매 전으로 돌아가는지 따로 확인한다(cov_d0).
            md = self._main_departure(b, X, Y, d0)
            if md is not None:
                md["cov_cell"] = cell
                md["cov_lvl"] = lvl
                md["lvl"] = min(md["lvl"], lvl)   # 신인(깊이 부족 전용) 허용 여부는 두 부족 중 더 앞 단계 기준
                md["need_cov"] = amap.get(X) != md["cell"]
                LOG["dep_both|" + ("same" if cell == md["cell"] else "diff")] += 1
                if md["need_cov"]:
                    LOG["dep_both|need_cov"] += 1
                return md
            return {"lvl": lvl, "cell": cell, "ref": ref, "d0": d0, "gk": cell == "GK", "main": False}
        return {"lvl": lvl, "cell": cell, "ref": ref, "d0": d0, "gk": cell == "GK"}

    def _main_departure(self, b, X, Y, d0):
        """[2026-11-25 장부 R1] 장부 부족은 안 늘지만 주포만 센 부족(L1~L3)이 늘면 판정 정보("main": True), 아니면 None.
        칸은 X의 주포 칸(주포 수가 줄어드는 유일한 칸)."""
        a = b.players[X][3]
        if not a:
            return None
        cnt = collections.Counter(b._cnt)
        cnt[a] -= 1
        if Y is not None:
            ya = main_cell(Y[0])
            if ya:
                cnt[ya] += 1
        dm0 = b._d_from(b._cnt)
        dm1 = b._d_from(cnt)
        inc = [k for k in range(3) if dm1[k] > dm0[k]]
        if not inc:
            return None
        sc = b.starter_min(a)
        ovx = b.players[X][2]
        ref = min(ovx, sc) if sc is not None else ovx
        LOG[f"dep_main|L{inc[0] + 1}|{a}"] += 1
        return {"lvl": inc[0] + 1, "cell": a, "ref": ref, "d0": d0, "gk": a == "GK", "main": True, "dm0": dm0}

    # ── 자유 판매 색인 ──
    def _src_ok(self, tid):
        m = self.meta.get(tid)
        return m is not None and not m["me"] and not m["youth"]

    def _team_free(self, tid):
        out = set()
        if not self._src_ok(tid):
            return out
        for pid in self.books[tid].free_ids():
            info = self.pinfo[pid]
            if info[6] or (self.year - (info[5] or 0)) < 1 or info[1] == "GK" or (info[7] or 0) >= VET_AGE:
                continue
            out.add(pid)
        return out

    def _idx_put(self, pid, add):
        info = self.pinfo[pid]
        for c in cell_list(info[1], info[2]):
            if c and c != "GK":
                fc = self._free_cell.setdefault((info[0], c), set())
                if add:
                    fc.add(pid)
                else:
                    fc.discard(pid)
                k = (info[3], pid)
                for lst in (self._idx[c], self._idx_nat[(c, info[4])]):
                    i = bisect.bisect_left(lst, k)
                    if add:
                        if i >= len(lst) or lst[i] != k:
                            lst.insert(i, k)
                    elif i < len(lst) and lst[i] == k:
                        lst.pop(i)

    def _build_idx(self):
        self._idx = collections.defaultdict(list)
        self._idx_nat = collections.defaultdict(list)
        self._free = {}
        self._free_cell = {}
        for tid in sorted(self.books):
            fr = self._team_free(tid)
            self._free[tid] = fr
            for pid in fr:
                info = self.pinfo[pid]
                for c in cell_list(info[1], info[2]):
                    if c and c != "GK":
                        self._idx[c].append((info[3], pid))
                        self._idx_nat[(c, info[4])].append((info[3], pid))
                        self._free_cell.setdefault((tid, c), set()).add(pid)
        for v in self._idx.values():
            v.sort()
        for v in self._idx_nat.values():
            v.sort()
        LOG["idx_builds"] += 1

    def _refresh(self, tid):
        if self._idx is None or tid not in self.books:
            return
        old = self._free.get(tid, set())
        new = self._team_free(tid)
        for pid in sorted(old - new):
            if pid in self.pinfo:
                self._idx_put(pid, False)
        for pid in sorted(new - old):
            self._idx_put(pid, True)
        self._free[tid] = new

    # ── 대체자 ──
    def _ok_R(self, R, S, X, cell, lo, d0, youth_age_max):
        """후보 R(자유 판매 색인에서 온, 그 칸에 설 수 있는 선수)의 남은 조건. 보충 완결성: X를 빼서 비는 칸이 cell이고
        R이 cell에 설 수 있으면 R을 넣은 최대 배정은 X를 뺀 배정 + R(cell)보다 나쁘지 않아 판매 전 부족량으로 돌아간다 —
        승인 뒤 _lg_check가 한 번 더 확인한다(N9 계측)."""
        from database import is_roster_foreign
        info = self.pinfo[R]
        if info[0] == S or R == X:
            return False
        ms = self.meta[S]
        o = info[3]
        if o < lo or o - ms["ceil"] > 3.0 or ms["floor"] - o > 12.0:
            return False
        if youth_age_max is not None and (info[7] or 0) > youth_age_max:
            return False
        if is_roster_foreign(info[4], ms["cname"]) and not self.quota_room(S, X):
            return False
        return True

    def quota_room(self, S, X):
        """S에 외국인 1명을 더 받을 수 있나(X가 나가는 거래면 X 포함 계산). [2026-11-22 1단계 — 외국인 쿼터 대체 예외]
        켜져 있으면 판매 선수 X가 외국인일 때는 쿼터가 꽉 찼어도(넘쳐 있어도) 허용 — 판매팀 외국인 수가 늘지 않는다."""
        ms = self.meta[S]
        x_f = X is not None and X in self.pinfo and self.is_foreign(X, S)
        if self._stage1 and x_f:
            if not getattr(self, "_planning", False):
                LOG["quota_swap_ok"] += 1
            return True
        fc = self.foreign_ct(S) - (1 if x_f else 0)
        return ms["qhi"] is not None and fc + 1 <= ms["qhi"]

    # ── [2026-11-22 1단계] 공급원(실제 산하팀 → 없으면 가상 유스) 승격 후보 ──
    def source_candidate(self, S, X, cell, lo=None, stats=True, main_only=False, cov_d0=None):
        """[2026-11-25 장부 R1] R1이면 그 칸이 주포인 선수부터 찾고, main_only가 아니면 없을 때 부포 선수까지 본다.
        cov_d0: 주어지면 X 대신 후보가 들어와 장부 부족이 cov_d0보다 나빠지지 않는 후보만(주포 게이트 need_cov)."""
        if r1():
            r = self._source_candidate(S, X, cell, lo, stats, True, cov_d0)
            if r is not None or main_only:
                LOG["r1_src_main" if r is not None else "r1_src_main_none"] += 1
                return r
            LOG["r1_src_fallback"] += 1
        return self._source_candidate(S, X, cell, lo, stats, False)

    def _source_candidate(self, S, X, cell, lo=None, stats=True, mf=False, cov_d0=None):
        """S에 cell 칸으로 올릴 공급원 선수 — (선수, 출처 팀, 종류 'affiliate'|'virtual') 또는 None.
        조건: 연간 한도(PROMO_LIMIT) 안, 그 칸에 설 수 있음, GK 아님, 임대·이번 창구 이동 아님, 나이 ≤ MAX_AGE,
        OVR ≥ lo(판매 거래 안에서만, 보충은 lo=None), 실제 산하팀은 그 팀 선발·규칙 부족(d1·d2)이 늘지 않음,
        외국인 쿼터(quota_room). 후보 중 OVR이 가장 높은 선수(같으면 id 작은 순).
        [2026-11-22 유스 승격 d3 보호 — YOUTH_PROMO_D3_GUARD] 켜져 있으면 실제 산하팀 후보는 그 산하팀의
        목표선 부족(d3)도 늘지 않아야 한다(가상 유스는 해당 없음). 꺼져 있으면 기존 그대로(d1·d2만)."""
        _st = STATS_SRC if stats else collections.Counter()   # [연쇄 이적] 계획 단계 조회는 계측에 안 센다
        if not self._stage1 or self.promo[S] >= _VY.PROMO_LIMIT or S not in self.meta:
            _st["limit_or_off"] += 1
            return None
        best = None
        chs = sorted(self.children.get(S, ()))
        if chs:
            pools = [(ch, "affiliate", sorted(self.books[ch].players) if ch in self.books else []) for ch in chs]
        else:
            vt = next((t for t, p in self.vparent.items() if p == S), None)
            pools = [(vt, "virtual", sorted(self.vpool.get(S, ())))] if vt is not None else []
        if not pools:
            _st["no_source"] += 1
            return None
        _g3 = bool(getattr(_C, "YOUTH_PROMO_D3_GUARD", False))
        for src, kind, pids in pools:
            cb = self.books.get(src) if kind == "affiliate" else None
            d_ch = cb.d() if cb is not None else None
            for p in pids:
                if p <= 0 or p == X:
                    continue
                info = self.pinfo.get(p)
                if info is None or info[1] == "GK" or info[6] or self.moved(p):
                    continue
                if (info[7] or 0) > _VY.MAX_AGE or cell not in cell_list(info[1], info[2]):
                    continue
                if mf and main_cell(info[1]) != cell:
                    continue
                if lo is not None and info[3] < lo:
                    continue
                k = (-info[3], p)
                if best is not None and k >= best[0]:
                    continue
                if cb is not None:
                    dn = cb.d_change(drop=(p,))[2]
                    if dn[0] > d_ch[0] or dn[1] > d_ch[1]:
                        continue
                    if _g3 and dn[2] > d_ch[2]:
                        _st["d3_guard_skip"] += 1
                        continue
                if self.is_foreign(p, S) and not self.quota_room(S, X):
                    continue
                if cov_d0 is not None and not self._cov_ok(S, X, p, cov_d0):
                    continue
                best = (k, p, src, kind)
        if best is None:
            _st["no_candidate"] += 1
            return None
        return best[1], best[2], best[3]

    def source_diag(self, S, X, cell, lo=None, mf=False, cov_d0=None):
        """[2026-11-25 계측 전용 — 동작 무관, 읽기만] _source_candidate가 후보를 못 찾은 이유를 같은 조건 순서로 판정.
        연간 한도·공급원 없음은 그 자체가 이유. 그 밖엔 후보를 단계별로 걸러 처음으로 0명이 되는 단계:
        pool_empty(풀에 GK 아닌 선수 없음) → loan_or_moved(임대·이번 창구 이동) → age(23세 초과) → position(그 칸에 설 수
        있는 선수 없음) → main_only(부포 선수만 있음 — 주포 게이트) → ovr_lo(승격 OVR 하한 미달) → affiliate_guard(실제
        산하팀 부족 보호) → quota(외국인 정원) → coverage(장부 부족 복원 실패). 한도에 걸린 경우 한도를 빼고도 후보가
        있었는지 함께 돌려준다(limit|cand / limit|nocand)."""
        if not self._stage1 or S not in self.meta:
            return "off"
        chs = sorted(self.children.get(S, ()))
        if chs:
            pools = [(ch, "affiliate", sorted(self.books[ch].players) if ch in self.books else []) for ch in chs]
        else:
            vt = next((t for t, p in self.vparent.items() if p == S), None)
            pools = [(vt, "virtual", sorted(self.vpool.get(S, ())))] if vt is not None else []
        if not pools:
            return "no_source"
        _g3 = bool(getattr(_C, "YOUTH_PROMO_D3_GUARD", False))
        n = collections.Counter()
        for src, kind, pids in pools:
            cb = self.books.get(src) if kind == "affiliate" else None
            d_ch = cb.d() if cb is not None else None
            for p in pids:
                if p <= 0 or p == X:
                    continue
                info = self.pinfo.get(p)
                if info is None or info[1] == "GK":
                    continue
                n["pool_empty"] += 1
                if info[6] or self.moved(p):
                    continue
                n["loan_or_moved"] += 1
                if (info[7] or 0) > _VY.MAX_AGE:
                    continue
                n["age"] += 1
                if cell not in cell_list(info[1], info[2]):
                    continue
                n["position"] += 1
                if mf and main_cell(info[1]) != cell:
                    continue
                n["main_only"] += 1
                if lo is not None and info[3] < lo:
                    continue
                n["ovr_lo"] += 1
                if cb is not None:
                    dn = cb.d_change(drop=(p,))[2]
                    if dn[0] > d_ch[0] or dn[1] > d_ch[1] or (_g3 and dn[2] > d_ch[2]):
                        continue
                n["affiliate_guard"] += 1
                if self.is_foreign(p, S) and not self.quota_room(S, X):
                    continue
                n["quota"] += 1
                if cov_d0 is not None:
                    inf = self.pinfo[p]
                    if worse(self.books[S].d_change(drop=(X,), add=((inf[1], inf[2]),))[2], cov_d0):
                        continue
                n["coverage"] += 1
        stages = ("pool_empty", "loan_or_moved", "age", "position", "main_only", "ovr_lo", "affiliate_guard", "quota",
                  "coverage")
        why = next((s for s in stages if n[s] == 0), "ok")
        if self.promo[S] >= _VY.PROMO_LIMIT:
            return "limit|" + ("cand" if why == "ok" else "nocand")
        return why

    def promote(self, p, src, S, kind):
        """장부 반영: 공급원 선수 p를 S로(DB 기록은 호출부). 연간 승격 수 +1."""
        if kind == "virtual":
            self.vpool[S].discard(p)
        self.move(p, src, S)
        self.promo[S] += 1
        LOG[f"promo|{kind}"] += 1

    def _cov_ok(self, S, X, R, d0):
        """[2026-11-25 장부 R1 보완] S에서 X가 나가고 R이 들어오면 장부 부족이 d0보다 나빠지지 않나."""
        LOG["r1_cov_chk"] += 1
        inf = self.pinfo[R]
        ok = not worse(self.books[S].d_change(drop=(X,), add=((inf[1], inf[2]),))[2], d0)
        if not ok:
            LOG["r1_cov_chk_fail"] += 1
        return ok

    def find_R(self, S, X, cell, ref, d0, lo=None, excl=None, tag="R_", main_only=False, cov_d0=None):
        """S에 cell 칸 대체자(자유 판매 선수) — 같은 리그 → 국내 → 세계, 기준 OVR에 가까운 순. 없으면 None.
        excl: 후보에서 뺄 소속 팀(연쇄 이적 B팀 대체자 — A·목적지 팀 선수 제외). tag: 계측 이름 앞머리.
        [2026-11-25 장부 R1] R1이면 그 칸이 주포인 선수부터 찾고, main_only가 아니면 없을 때 부포 선수까지 본다."""
        import time as _t
        _t0 = _t.perf_counter()
        try:
            if r1():
                r = self._find_R(S, X, cell, ref, d0, lo, excl, tag, True, cov_d0)
                if r is not None or main_only:
                    LOG["r1_findR_main" if r is not None else "r1_findR_main_none"] += 1
                    return r
                LOG["r1_findR_fallback"] += 1
            return self._find_R(S, X, cell, ref, d0, lo, excl, tag)
        finally:
            LOG["t_findR_us"] += int((_t.perf_counter() - _t0) * 1000000)

    def _find_R(self, S, X, cell, ref, d0, lo=None, excl=None, tag="R_", mf=False, cov_d0=None):
        if self._idx is None:
            self._build_idx()
        ms = self.meta[S]
        if lo is None:
            lo = ref - M_REPL
        yam = None
        if ms["youth"]:
            yam = int(getattr(_C, "YOUTH_MAX_AGE", 23))
        for scope, tids in (("리그", self.by_lid.get(ms["lid"], ())), ("국내", self.by_cid.get(ms["cid"], ()))):
            best = None
            for t in tids:
                if t == S or (scope == "국내" and self.meta[t]["lid"] == ms["lid"]):
                    continue
                if excl and t in excl:
                    continue
                for R in sorted(self._free_cell.get((t, cell), ())):
                    if mf and main_cell(self.pinfo[R][1]) != cell:
                        continue
                    if self._ok_R(R, S, X, cell, lo, d0, yam):
                        if cov_d0 is not None and not self._cov_ok(S, X, R, cov_d0):
                            continue
                        k = (abs(self.pinfo[R][3] - ref), R)
                        if best is None or k < best:
                            best = k
            if best:
                LOG[tag + scope] += 1
                return best[1]
        # 세계: 외국인 정원이 찼으면 그 나라 국적 선수만 볼 수 있다(외국인은 _ok_R에서 어차피 탈락) — 국적별 색인으로
        # 같은 후보를 같은 순서로 훑는다. 하한은 리그 하한 −12(그 아래는 _ok_R 탈락)로 미리 자른다.
        from database import is_roster_foreign
        _fc_s = self.foreign_ct(S) - (1 if (X is not None and self.is_foreign(X, S)) else 0)
        _q_full = not self.quota_room(S, X)   # [2026-11-22] _ok_R와 같은 판정(1단계 대체 예외 포함)
        if _q_full:   # 국적이 비어 있는 선수도 외국인으로 안 센다(database.is_roster_foreign)
            lst = sorted(self._idx_nat.get((cell, ms["cname"]), []) + self._idx_nat.get((cell, ""), []))
        else:
            lst = self._idx.get(cell, [])
        lo = max(lo, ms["floor"] - 12.0)
        hi = ms["ceil"] + 3
        i = bisect.bisect_left(lst, (ref, -1))
        j = i - 1
        while i < len(lst) or j >= 0:
            ci = lst[i] if i < len(lst) and lst[i][0] <= hi else None
            cj = lst[j] if j >= 0 and lst[j][0] >= lo else None
            if ci is None and cj is None:
                break
            if cj is None or (ci is not None and ci[0] - ref <= ref - cj[0]):
                cand = ci
                i += 1
            else:
                cand = cj
                j -= 1
            if excl and self.pinfo[cand[1]][0] in excl:
                continue
            if mf and main_cell(self.pinfo[cand[1]][1]) != cell:
                continue
            if self._ok_R(cand[1], S, X, cell, lo, d0, yam) and (cov_d0 is None or self._cov_ok(S, X, cand[1], cov_d0)):
                LOG[tag + "세계"] += 1
                return cand[1]
        return None

    # ── [2026-11-22 연쇄 이적] 계획(읽기만 — 장부·DB·난수를 바꾸지 않는다) ──
    def _chain_index(self):
        """장부 안 전체 선수(자유 판매 여부 무관, GK 칸 제외)의 (칸, 국적) → [(OVR, 선수)] 정렬 색인 — 창구마다 한 번.
        소속 팀은 조회 때 pinfo에서 다시 읽는다(이동해도 칸·국적·OVR은 그대로). 나중에 들어온 선수는 이번 창구 이동이라
        어차피 후보가 아니다."""
        if self._chain_ix is None:
            ix = collections.defaultdict(list)
            for pid, info in self.pinfo.items():
                if pid <= 0 or info[0] not in self.books:
                    continue
                for c in cell_list(info[1], info[2]):
                    if c and c != "GK":
                        ix[(c, info[4])].append((info[3], pid))
            for v in ix.values():
                v.sort()
            self._chain_ix = ix
        return self._chain_ix

    def chain_plan(self, A, X, dep, dst, newbie_ok):
        """A가 X를 팔면 dep(부족 판정)이 생기고 자유 판매 대체자가 없을 때 — 1단계 연쇄 계획.
        P: A 기준 국내 선수(국적), A 칸 dep["cell"]에 설 수 있음, OVR [max(기준−3, 리그 하한−12), 리그 상한+3],
           B팀 자유 판매 선수 아님, 임대·이번 창구 이동·35세+ 아님, A가 유스면 유스 나이. B = P 소속: A·목적지 아님,
           내 팀·유스팀 아님, 이번 창구 연쇄 공급 CHAIN_B_CAP 미만. 기준 OVR에 가까운 순(같으면 id) CHAIN_MAX_P명.
        A 조건: X 대신 P가 들어와 A 부족이 판매 전(dep["d0"])보다 나빠지지 않음.
        B 보충(그 P에서 순서대로): (b) B 자유 판매 대체자 R2(find_R — A·목적지 팀 선수 제외, B 기준 −3·쿼터) →
           (c) B 공급원 승격(source_candidate, 하한 B 기준−3) — 둘 다 B 부족이 판매 전보다 나빠지지 않을 때만.
        (d) (b)(c)가 모든 P에서 안 되면, 여름(newbie_ok)에 P 판매로 B 부족이 3단계에서만 생기는 첫 P — B 신인(생성은
           확정 단계에서 한 번, 검증 통과 때만).
        반환 계획 dict 또는 None. 막힌 이유는 LOG chain_why|… 에 센다."""
        from database import is_roster_foreign
        self._planning = True
        try:
            return self._chain_plan(A, X, dep, dst, newbie_ok, is_roster_foreign)
        finally:
            self._planning = False

    def _chain_plan(self, A, X, dep, dst, newbie_ok, is_roster_foreign):
        import constants as _Cc
        cap = int(getattr(_Cc, "CHAIN_B_CAP", 3))
        ma = self.meta[A]
        cell, ref = dep["cell"], dep["ref"]
        bkA = self.books[A]
        lo = max(ref - M_REPL, ma["floor"] - 12.0)
        hi = ma["ceil"] + 3
        yam = int(getattr(_Cc, "YOUTH_MAX_AGE", 23)) if ma["youth"] else None
        ix = self._chain_index()
        lst = ix.get((cell, ma["cname"]), []) + ix.get((cell, ""), [])
        cands = []
        n_cap = 0
        for o, p in lst:
            if o < lo or o > hi:
                continue
            info = self.pinfo[p]
            B = info[0]
            if B is None or B == A or B == dst or B not in self.books or not self._src_ok(B):
                continue
            if p == X or info[1] == "GK" or info[6] or self.moved(p) or (info[7] or 0) >= VET_AGE:
                continue
            if yam is not None and (info[7] or 0) > yam:
                continue
            if is_roster_foreign(info[4], ma["cname"]):
                continue
            if p in self.books[B].free_ids():
                continue
            if dep.get("main") and main_cell(info[1]) != cell:
                continue
            if self.chain_supply[B] >= cap:
                n_cap += 1
                continue
            cands.append((abs(o - ref), p))
        cands.sort()
        cands = cands[:CHAIN_MAX_P]
        if n_cap:
            LOG["chain_block|cap"] += 1
            LOG["chain_block_cap_cands"] += n_cap
        if not cands:
            LOG["chain_why|no_candidate" + ("_cap" if n_cap else "")] += 1
            return None
        d_first = None
        why = collections.Counter()
        for _g, P in cands:
            pi = self.pinfo[P]
            B = pi[0]
            if worse(bkA.d_change(drop=(X,), add=((pi[1], pi[2]),))[2], dep["d0"]):
                why["A_not_fit"] += 1
                continue
            depB = self.departure(B, P)
            if depB is None or depB["gk"]:
                why["B_dep_none"] += 1
                continue
            bkB = self.books[B]
            R2 = self.find_R(B, P, depB["cell"], depB["ref"], depB["d0"], excl=(A, dst), tag="chainR_",
                             main_only=bool(depB.get("main")), cov_d0=depB["d0"] if depB.get("need_cov") else None)
            if R2 is not None:
                ri = self.pinfo[R2]
                if not worse(bkB.d_change(drop=(P,), add=((ri[1], ri[2]),))[2], depB["d0"]):
                    return {"kind": "b", "A": A, "X": X, "dst": dst, "B": B, "P": P, "R2": R2, "holder": ri[0],
                            "dep": dep, "depB": depB}
                why["b_not_fit"] += 1
            else:
                why["b_none"] += 1
            if self._stage1:
                sc = self.source_candidate(B, P, depB["cell"], depB["ref"] - M_REPL, stats=False,
                                           main_only=bool(depB.get("main")),
                                           cov_d0=depB["d0"] if depB.get("need_cov") else None)
                if sc is not None and (sc[1] in (A, dst) or sc[0] == X):
                    why["c_src_conflict"] += 1   # B의 공급원이 A·목적지 팀(그 팀 선수를 빼면 그 팀 판정이 달라진다)
                elif sc is not None:
                    yi = self.pinfo[sc[0]]
                    if not worse(bkB.d_change(drop=(P,), add=((yi[1], yi[2]),))[2], depB["d0"]):
                        return {"kind": "c", "A": A, "X": X, "dst": dst, "B": B, "P": P, "R2": sc[0],
                                "holder": sc[1], "src_kind": sc[2], "dep": dep, "depB": depB}
                    why["c_not_fit"] += 1
                else:
                    why["c_none"] += 1
            if d_first is None and depB["lvl"] == 3:
                d_first = (P, B, depB)
        if newbie_ok and d_first is not None:
            P, B, depB = d_first
            return {"kind": "d", "A": A, "X": X, "dst": dst, "B": B, "P": P, "R2": None, "holder": None,
                    "dep": dep, "depB": depB}
        for k, v in why.items():
            LOG["chain_why|" + k] += v
        LOG["chain_why|fail" + ("_d_winter" if (d_first is not None and not newbie_ok) else "")] += 1
        return None

    # ── 갱신 ──
    def move(self, pid, src, dst, loan=False):
        import time as _t
        _t0 = _t.perf_counter()
        try:
            return self._move(pid, src, dst, loan)
        finally:
            LOG["t_move_us"] += int((_t.perf_counter() - _t0) * 1000000)

    def _move(self, pid, src, dst, loan=False):
        self.ensure(src)
        self.ensure(dst)
        info = self.pinfo.get(pid)
        if info is None:
            return
        if self._idx is not None and pid in self._free.get(src, ()):
            self._idx_put(pid, False)
            self._free[src].discard(pid)
        b = self.books.get(src)
        if b is not None:
            b.remove(pid)
        bd = self.books.get(dst)
        info[5] = self.year
        if loan:
            info[6] = src
        if bd is None:
            info[0] = None
        else:
            bd.add(pid, info[1], info[2], info[3])
            info[0] = dst
        self._refresh(src)
        self._refresh(dst)

    def add_player(self, tid, pid, pos, ovr, nat, age, lty=None):
        b = self.books.get(tid)
        if b is None:
            return
        b.add(pid, pos, None, ovr)
        self.pinfo[pid] = [tid, pos, None, ovr or 0, nat or "", self.year if lty is None else lty, None, age or 18]
        self._refresh(tid)

    def remove_player(self, pid):
        info = self.pinfo.get(pid)
        if info is None:
            return
        tid = info[0]
        if self._idx is not None and pid in self._free.get(tid, ()):
            self._idx_put(pid, False)
            self._free[tid].discard(pid)
        b = self.books.get(tid)
        if b is not None:
            b.remove(pid)
        info[0] = None
        self._refresh(tid)

    # ── 보충 칸 고르기(신인 생성) ──
    def pick_cell(self, tid, rng, exc_prob, retiree_pos=None):
        return pick_cell_book(self.books.get(tid), rng, exc_prob, retiree_pos)


def _pick_main_first(b, xm):
    """[2026-11-25 은퇴 대체 D안 — RETIRE_REPL_MAINFIRST] ① 장부 L1 → ② 주포 L1 → ③ 주포 L2 → ④ 은퇴자 주포 칸에 주포
    L3 부족이 있으면 그 칸. 해당 없으면 None(기존 결정 분기로). 난수 없음. (칸, 단계) 반환."""
    dd = b.deficits()
    if dd and dd[0][0] == 1:
        return dd[0][2], "cov_L1"
    md = b.m_deficits()
    for lv in (1, 2):
        x = [e for e in md if e[0] == lv]
        if x:
            return x[0][2], f"main_L{lv}"
    if xm in FIELD and any(e[2] == xm for e in md):
        return xm, "retiree_L3"
    return None, None


def pick_cell_book(b, rng, exc_prob, retiree_pos=None):
        """신인 1명의 칸 — 가장 심한 단계·부족량 큰 칸(GK 제외). exc_prob 확률로 목표표 비율 추첨(세대 변동 여지,
        기존 예외 15%와 같은 역할). 부족이 없으면 목표표(n+1)에서 아직 못 채운 첫 칸. 난수: rng.random() 1번
        (+ 예외일 때 1번)."""
        f = b.f if b else "4-4-2"
        T = b.T if b else 23
        if rng.random() < exc_prob:
            tg = table(f, T)
            items = [(c, tg[c]) for c in FIELD if tg[c] > 0]
            tot = sum(w for _c, w in items)
            x = rng.random() * tot
            acc = 0.0
            for c, w in items:
                acc += w
                if x < acc:
                    LOG["pick_exception"] += 1
                    return c
            return items[-1][0]
        if b is not None:
            if retiree_pos is not None and getattr(_C, "RETIRE_REPL_MAINFIRST", False):
                c, how = _pick_main_first(b, main_cell(retiree_pos))
                if c is not None:
                    LOG[f"retire_D|{how}"] += 1
                    return c
                LOG["retire_D|fallback"] += 1
            dd = b.deficits()
            if dd:
                LOG["pick_deficit"] += 1
                return dd[0][2]
            ids, cnt, amap, d = b.calc()
            tg = table(f, len(b.players) + 1)
            for c in FIELD:
                if cnt[c] < tg[c]:
                    LOG["pick_next_seat"] += 1
                    return c
        LOG["pick_fallback"] += 1
        return "CM"


def cell_to_pos(cell, rng=None):
    if cell == "FW" and rng is not None:
        return "CF" if rng.random() < float(getattr(_C, "SQUAD_TARGET_CF_SHARE", 0.2)) else "ST"
    return CELL_TO_POS.get(cell, cell)