# -*- coding: utf-8 -*-
"""이적/영입 로직 회귀 테스트 — 성능과 '게임 규칙'을 분리해서 검사한다.

왜 필요한가
───────────
이 시뮬레이션은 후보 리스트의 **순서**가 곧 게임 로직이다.
random.choices()는 넘겨받은 리스트의 순서대로 인덱스를 뽑으므로,
후보 집합이 똑같아도 순서만 바뀌면 다른 선수가 뽑힌다(실측 96.8%).
그러면 이적 결과가 갈리고, 순위표가 갈리고, 세이브 전체가 갈린다.
오류도 경고도 없이. 그래서 최적화할 때마다 아래를 같이 봐야 한다.

  BEHAVIOR TEST : 게임 규칙(불변조건)이 지켜지는가
                  — 원본이 없어도 단독 검사 가능. 리팩터링 후에도 유효.
  EQUIVALENCE   : 원본과 완전히 같은 결과를 내는가
                  — 같은 난수 상태에서 나란히 돌려 뽑힌 선수까지 대조.
  PERFORMANCE   : 얼마나 빨라졌는가

사용법
──────
  python perf_regression.py
      현재 코드로 BEHAVIOR + PERFORMANCE 실행

  python perf_regression.py --baseline /path/to/old/ai_lifecycle.py
      위에 더해 EQUIVALENCE(원본 대조)까지 실행

  python perf_regression.py --calls 3000
      호출 횟수 조정(기본 5000, 빠르게 보고 싶을 때 줄인다)

주의: 이 스크립트는 세이브 파일을 전혀 건드리지 않는다.
      data/leagues.py + constants.py로 메모리상에 가상 세계를 만들어 쓴다.
"""
import argparse
import bisect
import importlib.util
import os
import random
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SEED = 20260905


# ── 가상 세계 구성 ────────────────────────────────────────────────
def build_world():
    """실제 data/leagues.py의 국가·부수·팀 구성과 constants.get_ovr_range의
    등급/부수별 OVR 범위를 그대로 써서 세계를 만든다. 이걸 안 맞추면
    벤치가 현실과 어긋난다 — 균등 분포로 만들면 '자국에 후보가 늘 있는'
    비현실적 상황이 되어 전세계 검색 경로를 측정하지 못한다."""
    from data.leagues import LEAGUE_DATA
    from constants import get_country_league_grade, get_ovr_range

    rnd = random.Random(SEED)
    squad = ["GK", "GK", "CB", "CB", "CB", "LB", "RB", "CDM", "CM", "CM", "CAM",
             "LM", "RM", "LW", "RW", "ST", "ST", "CB", "CM", "ST", "GK", "CM"]
    max_tier = {cn: max(t) for cn, t in LEAGUE_DATA.items()}

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""CREATE TABLE ai_players(id INTEGER PRIMARY KEY, team_id INT,
                 position TEXT, age INT, name TEXT, ovr INT, nationality TEXT,
                 contract_end_year INT, last_transfer_year INT)""")

    team_info, rows_raw = {}, []
    tid = pid = 0
    for cname, tiers in LEAGUE_DATA.items():
        grade = get_country_league_grade(cname)
        for tier, (_lname, teams) in tiers.items():
            rng = get_ovr_range(grade, tier, cname) or (40, 60)
            lo, hi = rng
            mid, spread = (lo + hi) / 2.0, max(2.0, (hi - lo) / 4.0)
            for tname in teams:
                tid += 1
                team_info[tid] = (grade, tier, 0, cname, "EU", tname, 0.0,
                                  "mid", max_tier[cname], "", 0)
                for pos in squad:
                    pid += 1
                    rows_raw.append((pid, tid, pos, rnd.randint(16, 40),
                                     "선수%d" % pid,
                                     max(30, min(99, int(rnd.gauss(mid, spread)))),
                                     cname, 2030, 2020))
    c.executemany("INSERT INTO ai_players VALUES(?,?,?,?,?,?,?,?,?)", rows_raw)
    rows = c.execute("SELECT id, team_id, position, age, name, ovr, nationality, "
                     "contract_end_year, last_transfer_year FROM ai_players "
                     "ORDER BY id").fetchall()
    return conn, rows, team_info


def build_calls(rows, team_info, n_calls):
    """실제 _retire_and_replace가 _find_buy_replacement를 부르는 패턴을 재현한다
    (등급별 영입 시도 확률 · 명문팀 global_scouting 판정 · 목표 OVR 범위)."""
    from constants import BUY_REPLACEMENT_PROB_BY_GRADE as PROB, get_ovr_range
    from constants import BIG_CLUB_PRESTIGE_THRESHOLD as BIG
    try:
        from data.prestige_clubs import prestige_level
    except Exception:
        def prestige_level(_c, _t):
            return 0

    rnd = random.Random(SEED + 1)
    calls = []
    for r in rnd.sample(list(rows), min(len(rows), n_calls * 4)):
        ti = team_info.get(r["team_id"])
        if not ti:
            continue
        grade = ti[0]
        try:
            plvl = prestige_level(ti[3], ti[5]) or 0
        except Exception:
            plvl = 0
        buy_grade = "S" if (plvl >= BIG and grade not in ("SS", "S")) else grade
        if rnd.random() >= PROB.get(buy_grade, 0.10):
            continue
        rng = get_ovr_range(grade, ti[1], ti[3]) or (40, 60)
        calls.append((r["position"], rnd.randint(rng[0], rng[1]), r["team_id"],
                      ti[3], grade in ("SS", "S") or plvl >= BIG))
        if len(calls) >= n_calls:
            break
    return calls


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_pools(mod, rows, team_info):
    """_build_buy_pools의 시그니처가 버전마다 다르므로(구버전은 team_info를
    안 받는다) 둘 다 지원한다."""
    try:
        return mod._build_buy_pools(rows, team_info)
    except TypeError:
        return mod._build_buy_pools(rows)


def is_nested(tc):
    return bool(tc) and isinstance(next(iter(tc.values())), dict)


def counts_as_flat(tc):
    """{(tid,grp):n} 과 {grp:{tid:n}} 두 형태를 하나로 정규화한다."""
    if is_nested(tc):
        return {(t, g): v for g, dd in tc.items() for t, v in dd.items()}
    return dict(tc)


def apply_pick(mod, pick, used, tc, flat=None):
    """실제 호출부(_retire_and_replace)와 똑같이 상태를 갱신한다."""
    if pick is None:
        return
    used.add(pick["id"])
    grp = mod._POS_GROUP.get(pick["position"], "FW")
    tid = pick["team_id"]
    if is_nested(tc):
        dd = tc.setdefault(grp, {})
        dd[tid] = max(0, dd.get(tid, 1) - 1)
        newv = dd[tid]
    else:
        tc[(tid, grp)] = max(0, tc.get((tid, grp), 1) - 1)
        newv = tc[(tid, grp)]
    if flat is not None:
        flat[(tid, grp)] = newv


# ── BEHAVIOR TEST — 게임 규칙(불변조건) ──────────────────────────
def behavior_test(mod, rows, team_info, calls):
    """원본이 없어도 단독으로 돌릴 수 있는 규칙 검사.
    '최적화 때문에 규칙이 사라졌는지'를 잡는 게 목적이다."""
    from constants import BUY_REPLACEMENT_OVR_BAND as BAND
    try:
        from economy import LEAGUE_GRADE_RANK as RANK
    except Exception:
        RANK = {}
    _PG = mod._POS_GROUP
    pools = make_pools(mod, rows, team_info)
    tc = mod._build_team_pos_group_count(rows)
    flat = counts_as_flat(tc)
    ovr_by_id = {r["id"]: r["ovr"] for r in rows}
    team_by_id = {r["id"]: r["team_id"] for r in rows}
    pos_by_id = {r["id"]: r["position"] for r in rows}

    fails = {}
    stat = {"n": 0, "same_country": 0, "dom_avail": 0, "dom_picked": 0, "global": 0}

    def fail(rule, detail):
        fails.setdefault(rule, []).append(detail)

    used = set()
    rnd = random.Random(SEED + 2)
    for pos, target, dst_team, dst_cname, gs in calls:
        random.setstate(rnd.getstate())
        got = mod._find_buy_replacement(pos, target, dst_team, dst_cname,
                                        pools, team_info, tc, used,
                                        global_scouting=gs)
        rnd.random()
        if got is None:
            continue
        stat["n"] += 1
        if gs:
            stat["global"] += 1
        gid = got["id"]

        # 규칙 1: OVR 밴드 준수
        if not (target - BAND[0] <= ovr_by_id[gid] <= target + BAND[1]):
            fail("OVR 밴드 준수", (gid, ovr_by_id[gid], target))
        # 규칙 2: 목적지 팀 자기 선수를 사오지 않는다
        if team_by_id[gid] == dst_team:
            fail("자기 팀 제외", gid)
        # 규칙 3: 같은 등록 포지션만 후보가 된다
        if pos_by_id[gid] != pos:
            fail("포지션 일치", (gid, pos_by_id[gid], pos))
        # 규칙 4: 그 팀의 마지막 포지션그룹 선수는 빼오지 않는다
        grp = _PG.get(pos_by_id[gid], "FW")
        if flat.get((team_by_id[gid], grp), 0) <= 1:
            fail("포지션그룹 마지막 1명 보호", (gid, team_by_id[gid], grp))
        # 규칙 5: 이번 시즌 이미 팔린 선수는 다시 안 뽑힌다
        if gid in used:
            fail("중복 영입 방지", gid)

        src_ti = team_info.get(team_by_id[gid])
        src_cname = src_ti[3] if src_ti else ""
        if src_cname == dst_cname:
            stat["same_country"] += 1

        # 규칙 6: 명문팀 전세계 검색이라도 자기보다 강한 리그에서는 못 뺏어온다
        if gs and RANK:
            dst_ti = team_info.get(dst_team)
            dr = RANK.get(dst_ti[0] if dst_ti else "D", 4)
            sr = RANK.get(src_ti[0] if src_ti else "D", 1)
            if sr > dr:
                fail("약한 목적지가 강한 리그에서 영입 불가", (gid, sr, dr))

        # 규칙 7: 자국 우선 — 자국에 후보가 있었는데 해외에서 데려오면 위반
        #         (global_scouting 팀은 전세계 우선이 '정상'이므로 제외)
        if not gs:
            lst, ovrs = pools[pos][0], pools[pos][1]
            i0 = bisect.bisect_left(ovrs, target - BAND[0])
            i1 = bisect.bisect_right(ovrs, target + BAND[1])
            dom = False
            for x in lst[i0:i1]:
                if x["team_id"] == dst_team or x["id"] in used:
                    continue
                xti = team_info.get(x["team_id"])
                if (xti[3] if xti else "") != dst_cname:
                    continue
                if flat.get((x["team_id"], _PG.get(x["position"], "FW")), 0) <= 1:
                    continue
                dom = True
                break
            if dom:
                stat["dom_avail"] += 1
                if src_cname == dst_cname:
                    stat["dom_picked"] += 1
                else:
                    fail("자국 후보가 있으면 자국에서 영입",
                         (gid, dst_cname, src_cname))

        apply_pick(mod, got, used, tc, flat)
    return fails, stat


# ── EQUIVALENCE — 원본과 결과가 같은가 ───────────────────────────
def equivalence_test(base, new, rows, team_info, calls):
    """같은 난수 상태에서 나란히 돌려 '뽑힌 선수'까지 대조한다.
    호출 사이의 상태(used_ids, 포지션그룹 인원)도 실제 호출부와 똑같이
    갱신하며 진행해야 의미가 있다 — 한 번 갈리면 이후가 전부 갈리므로."""
    pb, pn = make_pools(base, rows, team_info), make_pools(new, rows, team_info)
    tb = base._build_team_pos_group_count(rows)
    tn = new._build_team_pos_group_count(rows)
    fb, fn = counts_as_flat(tb), counts_as_flat(tn)
    if fb != fn:
        return None, ("포지션그룹 인원표가 원본과 다름 (%d vs %d 항목)"
                      % (len(fb), len(fn)))

    ub, un = set(), set()
    mismatch = []
    rnd = random.Random(SEED + 3)
    for i, (pos, target, dst_team, dst_cname, gs) in enumerate(calls):
        st = rnd.getstate()
        random.setstate(st)
        a = base._find_buy_replacement(pos, target, dst_team, dst_cname, pb,
                                       team_info, tb, ub, global_scouting=gs)
        random.setstate(st)
        b = new._find_buy_replacement(pos, target, dst_team, dst_cname, pn,
                                      team_info, tn, un, global_scouting=gs)
        rnd.random()
        ai = a["id"] if a is not None else None
        bi = b["id"] if b is not None else None
        if ai != bi:
            mismatch.append((i, ai, bi))
        elif b is not None and not isinstance(b, sqlite3.Row):
            mismatch.append((i, "Row 타입", type(b).__name__))
        apply_pick(base, a, ub, tb)
        apply_pick(new, b, un, tn)
    return mismatch, None


# ── PERFORMANCE ─────────────────────────────────────────────────
def perf_test(mod, rows, team_info, calls, label):
    t0 = time.perf_counter()
    pools = make_pools(mod, rows, team_info)
    tc = mod._build_team_pos_group_count(rows)
    t_build = time.perf_counter() - t0
    used = set()
    t0 = time.perf_counter()
    for pos, target, dst_team, dst_cname, gs in calls:
        mod._find_buy_replacement(pos, target, dst_team, dst_cname, pools,
                                  team_info, tc, used, global_scouting=gs)
    t_search = time.perf_counter() - t0
    print("  %-6s 풀구축 %6.2fs · 탐색 %6.2fs · 합계 %6.2fs"
          % (label, t_build, t_search, t_build + t_search))
    return t_build + t_search


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", help="비교할 원본 ai_lifecycle.py 경로")
    ap.add_argument("--calls", type=int, default=5000, help="영입 시도 호출 수")
    args = ap.parse_args()

    print("가상 세계 구성 중...")
    _conn, rows, team_info = build_world()
    calls = build_calls(rows, team_info, args.calls)
    n_gs = sum(1 for x in calls if x[4])
    print("  팀 %d개 · 선수 %d명 · 영입시도 %d회 (전세계검색 %d회, %.0f%%)\n"
          % (len(team_info), len(rows), len(calls), n_gs,
             100.0 * n_gs / max(1, len(calls))))

    here = os.path.dirname(os.path.abspath(__file__))
    new = load_module(os.path.join(here, "ai_lifecycle.py"), "_rt_new")

    print("BEHAVIOR TEST — 게임 규칙(불변조건)")
    print("-" * 62)
    fails, stat = behavior_test(new, rows, team_info, calls)
    rules = ["OVR 밴드 준수", "자기 팀 제외", "포지션 일치",
             "포지션그룹 마지막 1명 보호", "중복 영입 방지",
             "약한 목적지가 강한 리그에서 영입 불가",
             "자국 후보가 있으면 자국에서 영입"]
    for r in rules:
        bad = fails.get(r)
        print("  %-38s %s" % (r, "PASS" if not bad
                              else "FAIL (%d건) 예: %s" % (len(bad), bad[0])))
    if stat["n"]:
        print("\n  참고: 영입 성사 %d건 · 자국 영입 %.1f%% · 전세계검색 %.1f%%"
              % (stat["n"], 100.0 * stat["same_country"] / stat["n"],
                 100.0 * stat["global"] / stat["n"]))
        if stat["dom_avail"]:
            print("        자국 후보가 있던 %d건 중 자국 영입 %d건"
                  % (stat["dom_avail"], stat["dom_picked"]))
    behavior_ok = not fails

    equiv_ok = None
    if args.baseline:
        print("\nEQUIVALENCE TEST — 원본과 결과 동일한가")
        print("-" * 62)
        base = load_module(os.path.abspath(args.baseline), "_rt_base")
        mismatch, err = equivalence_test(base, new, rows, team_info, calls)
        if err:
            print("  FAIL:", err)
            equiv_ok = False
        else:
            print("  %d회 순차 대조 · 불일치 %d건 -> %s"
                  % (len(calls), len(mismatch), "PASS" if not mismatch else "FAIL"))
            for m in mismatch[:5]:
                print("    %s번째 호출: 원본 %s vs 수정 %s" % m)
            equiv_ok = not mismatch

    print("\nPERFORMANCE TEST")
    print("-" * 62)
    if args.baseline:
        t_old = perf_test(sys.modules["_rt_base"], rows, team_info, calls, "원본")
        t_new = perf_test(new, rows, team_info, calls, "현재")
        print("  -> %.2f배 (%.2fs 절감)" % (t_old / max(1e-9, t_new), t_old - t_new))
    else:
        perf_test(new, rows, team_info, calls, "현재")

    print()
    ok = behavior_ok and (equiv_ok is not False)
    print("종합 판정:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())