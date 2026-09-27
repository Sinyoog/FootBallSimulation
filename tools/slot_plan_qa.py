# -*- coding: utf-8 -*-
"""대륙대항전(챔스/유로파/컨퍼런스) 출전권 배분 실측 하니스.

[2026-09 신설] continental_qualification.allocate_continental_slots()를
5개 대륙에 돌려서 아래를 표로 찍는다:
  (a) 대회별 배정 수 == 정원인지
  (b) verify_no_overlap() 중복 건수
  (c) 세 대회 전부 0장인 국가 수 + 목록
  (d) 국가별 CL/EL/ECL 슬롯표(참고표와 대조용)
  (e) 워터폴 컷 역전(챔스 컷 > 유로파 컷) 여부
  (f) 사다리 단절(상위 두 대회 합 >= LADDER_MIN인데 ECL 0장) 국가

사용:
  python3 tools/slot_plan_qa.py [--db 경로] [--cont 아시아]
DB는 읽기만 한다(복사본을 넘기는 게 안전).
"""
import _path  # noqa: F401
import argparse
import os
import sys

import database


def _setup(db_path):
    database.USE_MEMORY_DB = False
    database.DB_PATH = os.path.abspath(db_path)
    database.reset_conn_pool()


CONTS = ("유럽", "아시아", "아프리카", "남미", "북미")


def run(db_path, only_cont=None, season=0, year=None, ladder_min=3):
    _setup(db_path)
    from competition import continental_qualification as cq
    from competition import champions_engine as _cl

    conts = [only_cont] if only_cont else list(CONTS)
    grand = {"zero": 0, "dupe": 0, "capmiss": 0, "ladder": 0, "invert": 0}

    for cont in conts:
        countries = cq._ranked_countries(cont, year)
        n = len(countries)
        cl_cap = _cl.CL_TEAMS_BY_CONTINENT.get(cont, 36)
        el_cap = cq.QUALIFICATION_TEAM_CAP.get(cont, 36)
        ecl_cap = cq.conference_team_cap(cont, n)
        plan = cq.build_slot_plan(cont, n, cl_cap, el_cap)
        alloc = cq.allocate_continental_slots(cont, season, year)

        print("=" * 78)
        print(f"[{cont}] 1부 보유국 {n}개 · 정원 CL {cl_cap} / EL {el_cap} / ECL {ecl_cap}")
        print(f"  컷(깊이): {plan.get('cuts')}")

        # (a) 정원 일치
        for comp, cap in (("champions", cl_cap), ("europa", el_cap), ("conference", ecl_cap)):
            got = len(alloc[comp])
            ok = "OK" if got == cap else "MISMATCH"
            if got != cap:
                grand["capmiss"] += 1
            print(f"  {comp:11s} 배정 {got:3d} / 정원 {cap:3d}  {ok}")

        # (b) 중복
        dupes = cq.verify_no_overlap(alloc)
        grand["dupe"] += len(dupes)
        print(f"  중복 참가팀: {len(dupes)}건" + (f" {dupes[:5]}" if dupes else ""))

        # (c)(d)(f) 국가별 표
        zero, ladder_break = [], []
        rows = []
        for i, lg in enumerate(countries):
            c, e, f = plan["champions"][i], plan["europa"][i], plan["conference"][i]
            rows.append((i + 1, lg["country"], lg["grade"], c, e, f))
            if c == 0 and e == 0 and f == 0:
                zero.append(lg["country"])
            if (ecl_cap > 0 and (c + e) >= ladder_min and f == 0
                    and i >= cq.QUAL_ECL_TOP_EXEMPT):
                ladder_break.append((lg["country"], c, e))
        grand["zero"] += len(zero)
        grand["ladder"] += len(ladder_break)

        # (e) 워터폴 컷 역전
        d_cl = max([i for i, r in enumerate(rows) if r[3] > 0], default=-1) + 1
        d_el = max([i for i, r in enumerate(rows) if r[4] > 0], default=-1) + 1
        d_ecl = max([i for i, r in enumerate(rows) if r[5] > 0], default=-1) + 1
        # ECL 정원 0(= 그 대륙은 컨퍼런스 대회를 열지 않음)이면 ECL 컷은
        # 워터폴 비교 대상이 아니다 — 남미(CONFERENCE_TEAM_CAP 0).
        invert = not (d_cl <= d_el) or (ecl_cap > 0 and d_el > d_ecl)
        if invert:
            grand["invert"] += 1
        print(f"  실측 컷: CL {d_cl}위까지 / EL {d_el}위까지 / ECL {d_ecl}위까지"
              f"  {'*** 역전! ***' if invert else 'OK'}")
        print(f"  0장 국가: {len(zero)}개" + (f" → {zero}" if zero else ""))
        print(f"  사다리 단절(CL+EL>={ladder_min} & ECL 0): {len(ladder_break)}개"
              + (f" → {ladder_break}" if ladder_break else ""))
        print(f"  {'순위':>3} {'국가':<16} 등급  CL  EL ECL  합")
        for rk, name, grade, c, e, f in rows:
            mark = "  <<0장" if (c == 0 and e == 0 and f == 0) else ""
            print(f"  {rk:>3} {name:<16} {grade:<4} {c:3d} {e:3d} {f:3d} {c+e+f:3d}{mark}")

    print("=" * 78)
    print(f"[종합] 정원불일치 {grand['capmiss']} · 중복 {grand['dupe']} · "
          f"0장국가 {grand['zero']} · 사다리단절 {grand['ladder']} · 컷역전 {grand['invert']}")
    return grand


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="game.db")
    ap.add_argument("--cont", default=None)
    ap.add_argument("--season", type=int, default=0)
    ap.add_argument("--ladder-min", type=int, default=3)
    a = ap.parse_args()
    g = run(a.db, a.cont, a.season, None, a.ladder_min)
    sys.exit(0 if (g["capmiss"] == 0 and g["dupe"] == 0 and g["zero"] == 0
                   and g["ladder"] == 0 and g["invert"] == 0) else 1)