"""[2026-11-26 신설, 신민용 요청: "최대한 유스 안에서 데뷔하게 (첼시 유스라도 노르웨이 리그까지 떨어져서라도 데뷔),
정 없으면 유스 없이 새롭게 태어나는 식으로"] 신인 대신 유스 데뷔 — constants.VY_STANDALONE(기본 게임 유스팀) 전용.

은퇴 대체·승강 직후 물갈이·인원 보충·시즌 중 공석 보충은 신인 행(row 튜플)을 모았다가 한꺼번에 INSERT한다.
substitute()는 그 INSERT 직전에 행 목록을 받아, 행마다 같은 자리를 맡을 유스가 있으면 그 유스를 그 팀으로 데뷔시키고
(소속·계약·연봉·이적 기록) 그 행을 목록에서 뺀다. 남은 행만 원래대로 새로 생성된다.

같은 자리 판정(신인 행 기준):
  · 같은 등록 포지션(유스 풀엔 GK가 없어 GK 신인은 늘 새로 생성).
  · OVR = 신인 OVR ± VY_SA_OVR_WIN — 신인으로 들어왔을 선수와 같은 수준이라 팀 전력 설계(리그·명문 하한 등)를 흔들지 않는다.
    강한 구단 유스라도 그 구단 수준에 못 미치면 OVR이 맞는 하위 리그 팀 신인 자리로 데뷔한다.
  · VY_SA_DEBUT_MIN_AGE세 이상, 이번 창구에 막 들어온 유스(last_transfer_year = 올해)는 제외.
찾는 순서(먼저 찾은 단계에서 고른다, 같은 단계 안에서는 나이 많은 순 → OVR이 신인과 가까운 순 → id):
  · 내국인 신인 자리: ① 자기 구단 유스(산하팀이면 모구단 유스) ② 같은 나라 다른 구단 유스
  · 외국인 신인 자리: ③ 같은 대륙 외국 유스 ④ 전 세계 외국 유스  — 내국인·외국인 자리를 바꾸지 않아 외국인 정원 판정은
    신인이 들어왔을 때와 같다. 외국인 완성 전력 신인(23세 초과)은 유스로 대신하지 않는다.
이적 기록: 자기 구단 유스면 "유스 승격(공급원)", 다른 구단 유스면 "유스 외부 데뷔"(이적료 0). 연봉·계약 기간은 그 자리 신인
행에 계산돼 있던 값을 그대로 쓴다.
"""
import bisect
import collections

import constants as _C
import virtual_youth as _VY

STATS = collections.Counter()   # 계측: {"출처|단계": 건수, "miss|출처": 건수}

OWN_TYPE = "유스 승격(공급원)"
EXT_TYPE = "유스 외부 데뷔"


def on():
    return _VY.standalone()


class _Pool:
    """가상 유스(열린 유스팀) 선수 색인 — substitute() 한 번에 한 번 만든다."""

    def __init__(self, cur, year):
        self.year = year
        self.used = set()
        self.own = collections.defaultdict(list)          # 모구단 id → [선수]
        self.dom = collections.defaultdict(list)          # (나라, 포지션) → OVR 정렬
        self.cont = collections.defaultdict(list)         # (대륙, 포지션) → OVR 정렬
        self.world = collections.defaultdict(list)        # 포지션 → OVR 정렬
        self.team = {}
        # [2026-11-26 S4 — constants.VY_V3_DEBUT_GATE] 조기 데뷔 제한: 특급 명단·데뷔할 팀 리그 하한(팀별 캐시)
        self.gate = _VY.v3() and bool(getattr(_C, "VY_V3_DEBUT_GATE", False))
        self.special = set()
        self.tfloor = {}
        if self.gate:
            try:
                self.special = {r[0] for r in cur.execute("SELECT player_id FROM vy3_talent WHERE tier=2")}
            except Exception:
                self.special = set()
        closed = _VY.sa_closed(cur)
        vt = {}
        for vid, par, cname, cont in cur.execute(
                "SELECT t.id, t.parent_team_id, cn.name, cn.continent FROM teams t "
                "LEFT JOIN countries cn ON cn.id = t.country_id WHERE t.classification_status=?", (_VY.STATUS,)):
            if vid not in closed:
                vt[vid] = (par, cname, cont)
        self.n = 0
        if not vt:
            return
        amin = int(getattr(_C, "VY_SA_DEBUT_MIN_AGE", 16))
        ids = sorted(vt)
        rows = []
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            ph = ",".join("?" * len(part))
            rows += cur.execute(
                f"SELECT id, team_id, position, ovr, age, nationality, COALESCE(last_transfer_year, 0) "
                f"FROM ai_players WHERE team_id IN ({ph})", part).fetchall()
        for pid, tid, pos, ovr, age, nat, lty in rows:
            if (age or 0) < amin or lty == year or ovr is None:
                continue
            par, cname, cont = vt[tid]
            e = (pid, tid, par, pos, int(ovr), int(age), nat, cname, cont)
            self.own[par].append(e)
            self.dom[(nat, pos)].append(e)
            self.cont[(cont, pos)].append(e)
            self.world[pos].append(e)
            self.n += 1
        for d in (self.dom, self.cont, self.world):
            for k2 in d:
                d[k2].sort(key=lambda e: (e[4], e[0]))
        self.keys = {}
        for name, d in (("dom", self.dom), ("cont", self.cont), ("world", self.world)):
            for k2, lst in d.items():
                self.keys[(name, k2)] = [e[4] for e in lst]

    def team_info(self, cur, tid):
        t = self.team.get(tid)
        if t is None:
            r = cur.execute(
                "SELECT t.parent_team_id, cn.name, cn.continent FROM teams t "
                "LEFT JOIN leagues l ON l.id = t.league_id "
                "LEFT JOIN countries cn ON cn.id = COALESCE(l.country_id, t.country_id) WHERE t.id=?", (tid,)).fetchone()
            t = self.team[tid] = (tuple(r) if r else (None, None, None))
        return t

    def _best(self, cands, ovr, ok):
        best, bk = None, None
        for e in cands:
            if e[0] in self.used or not ok(e):
                continue
            k = (e[5], -abs(e[4] - ovr), -e[0])
            if bk is None or k > bk:
                best, bk = e, k
        return best

    def _window(self, name, d, key, lo, hi):
        lst = d.get(key)
        if not lst:
            return ()
        ks = self.keys[(name, key)]
        return lst[bisect.bisect_left(ks, lo):bisect.bisect_right(ks, hi)]

    def _gate_fn(self, cur, tid):
        """[2026-11-26 S4] 데뷔할 팀 tid 기준 나이 조건 함수 — 15세 이하 불가, 16~17세 특급 + OVR ≥ 리그 하한 − LO,
        18세 OVR ≥ 리그 하한 − LO, 19세 이상 그대로. 리그 하한은 신인 생성과 같은 보정(virtual_youth._v3_floor)."""
        f = self.tfloor.get(tid)
        if f is None:
            r = cur.execute("SELECT l.tier, cn.name, cn.continent FROM teams t JOIN leagues l ON l.id = t.league_id "
                            "JOIN countries cn ON cn.id = l.country_id WHERE t.id=?", (tid,)).fetchone()
            f = (_VY._v3_floor(r[1], r[2], r[0]) if r else None)
            f = self.tfloor[tid] = (f if f is not None else 40) - int(getattr(_C, "VY_V3_MKT_LO", 3))
        sp = self.special

        def g(e):
            a = e[5]
            if a >= 19:
                return True
            if a <= 15:
                return False
            if a <= 17 and e[0] not in sp:
                return False
            return e[4] >= f
        return g

    def pick(self, cur, tid, pos, ovr, nat, age):
        """신인 행 하나(팀·포지션·OVR·국적·나이)를 대신할 유스 → (선수 튜플, 단계) 또는 (None, None)."""
        if pos == "GK" or ovr is None:
            return None, None
        par, tc, tcont = self.team_info(cur, tid)
        if tc is None:
            return None, None
        gate = self._gate_fn(cur, tid) if self.gate else None
        w = int(getattr(_C, "VY_SA_OVR_WIN", 3))
        lo, hi = int(ovr) - w, int(ovr) + w
        if nat == tc:
            own = [e for p in {tid, par} if p for e in self.own.get(p, ()) if e[3] == pos and lo <= e[4] <= hi]
            e = self._best(own, ovr, lambda e: e[6] == tc and (gate is None or gate(e)))
            if e is not None:
                return e, "own"
            e = self._best(self._window("dom", self.dom, (tc, pos), lo, hi), ovr,
                           lambda e: gate is None or gate(e))
            if e is not None:
                return e, "domestic"
            return None, None
        if (age or 0) > 23:
            return None, None
        e = self._best(self._window("cont", self.cont, (tcont, pos), lo, hi), ovr,
                       lambda e: e[6] != tc and (gate is None or gate(e)))
        if e is not None:
            return e, "continent"
        e = self._best(self._window("world", self.world, pos, lo, hi), ovr,
                       lambda e: e[6] != tc and (gate is None or gate(e)))
        if e is not None:
            return e, "world"
        return None, None


def substitute(c, year, rows, sal_idx, src, mid=False):
    """신인 행 목록 rows(ai_players INSERT 순서 — 0 team_id, 1 name, 2 position, 3~17 능력치 15개, 18 ovr, 19 age,
    20 sub_role, 21 nationality, 22 true_nationality, 23 contract_end_year, 24 last_transfer_year, 25 created_year,
    그다음 salary·potential_ovr(은퇴 대체는 26 salary, 나머지 경로는 27 salary — sal_idx로 받는다), 마지막 creation_source)
    중 유스로 대신할 수 있는 행은 유스를 데뷔시키고 뺀 목록을 돌려준다. 꺼져 있거나 유스 풀이 비어 있으면 rows 그대로."""
    if not rows or not on():
        return rows
    cur = c.cursor() if hasattr(c, "cursor") and not hasattr(c, "fetchone") else c
    pool = _Pool(cur, year)
    if not pool.n:
        return rows
    try:
        _cs = cur.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
        season = _cs[0] if _cs else 0
    except Exception:
        season = 0
    keep = []
    upd, logs = [], []
    for row in rows:
        if len(row) < 29 or row[-1] != src:   # 다른 출처(포지션 교체 등)의 행은 그대로
            keep.append(row)
            continue
        tid, pos, ovr, age, nat = row[0], row[2], row[18], row[19], row[21]
        e, tier = pool.pick(cur, tid, pos, ovr, nat, age)
        if e is None:
            keep.append(row)
            STATS[f"miss|{src}"] += 1
            continue
        pool.used.add(e[0])
        pid, vt, par, ppos, povr, page = e[0], e[1], e[2], e[3], e[4], e[5]
        cend, sal = row[23], row[sal_idx]
        ttype = OWN_TYPE if tier == "own" else EXT_TYPE
        upd.append((tid, cend, year, sal, pid))
        logs.append((season, year, pid, "", ppos, page, povr, vt, tid, 0, 0, 0.0, 0.0, ttype,
                     (1 if mid else 0), "", 0, 0, 0, sal, cend))
        STATS[f"{src}|{tier}"] += 1
    if upd:
        cur.executemany("UPDATE ai_players SET team_id=?, contract_end_year=?, last_transfer_year=?, salary=? "
                        "WHERE id=?", upd)
        cur.executemany("""INSERT INTO ai_transfer_log(
                season, year, player_id, player_name, player_position, player_age, player_ovr,
                from_team_id, to_team_id, from_team_prestige, to_team_prestige,
                from_team_avg_ovr, to_team_avg_ovr, transfer_type, is_mid_season, player_role,
                fee, is_loan, loan_return_year, salary, contract_end_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", logs)
    return keep