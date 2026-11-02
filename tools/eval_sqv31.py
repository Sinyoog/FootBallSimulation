# -*- coding: utf-8 -*-
"""[2026-10-31] V3.1 판정 보조 — run_sqv31_season.py가 저장한 팀 구성 덤프(SQV31_DUMP)를 같은 목표표로 채점한다.

양쪽 팔(OFF·ON)을 모두 '새 설계 목표표'(V3.1: GK 3명 + 중원 배분)로 판정하고[필수 4·5],
기존 목표표(작업 전, GK 2명) 기준 수치는 관찰용으로 함께 낸다. 게임 코드는 바꾸지 않는다.

사용: python tools/eval_sqv31.py dump1.json [dump2.json …]   → JSON 한 줄씩(덤프마다, 시즌마다)
  pos_over        한 포지션이라도 목표+1을 넘는 팀 수                       [필수 4]
  pos_over_by     포지션별 목표+1 초과 팀 수
  mid_over        중원 묶음(CDM+CM+CAM)이 목표 합계+1을 넘는 팀 수          [필수 5]
  pos5_nonmid     CB·중원 밖 한 포지션 5명 이상 팀 수(관찰)
  [2026-10-31 추가] 보유 수는 주포·부포 기준(squad_targets.held_assign — 선수 1명은 1칸) — 덤프에 부포가 있으면
  [포지션, 부포] 쌍으로 센다(없으면 주포만, 1시즌은 부포 0명이라 같다).
  pos_short       한 포지션이라도 목표−2 이하인 팀 수 / pos_short_by 포지션별
  old_pos_over / old_mid_over   기존 목표표 기준(관찰)"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import constants as C          # noqa: E402
import squad_targets as ST     # noqa: E402

MID = ("CDM", "CM", "CAM")


def _table(v31):
    C.set_squad_by_formation_v3(False)
    C.set_squad_by_formation_v31(v31)
    return {(f, n): ST.position_targets(f, n) for f in C.FORMATION_SLOTS for n in range(11, ST.MAX_SQUAD + 1)}


NEW = _table(True)
OLD = _table(False)


def _entries(pos):
    """덤프 포지션 dict → held_assign 입력. 키가 "주포|부포"면 부포까지(run_sqv31_season이 부포를 저장할 때)."""
    out = []
    for k, n in pos.items():
        p, _, sub = k.partition("|")
        out += [(p, sub or None)] * n
    return out


def score(teams, table, with_short=False):
    pos_over = mid_over = pos_short = 0
    by = collections.Counter()
    sby = collections.Counter()
    for _tid, _cn, _g, _tier, f, n, pos in teams:
        f = f if f in C.FORMATION_SLOTS else ST.DEFAULT_FORMATION
        t = table[(f, max(11, min(n, ST.MAX_SQUAD)))]
        held = ST.held_assign(_entries(pos), t)
        bad = [p for p in ST.TARGET_POSITIONS if held[p] > t[p] + 1]
        if bad:
            pos_over += 1
            by.update(bad)
        sh = [p for p in ST.TARGET_POSITIONS if held[p] <= t[p] - 2]
        if sh:
            pos_short += 1
            sby.update(sh)
        if sum(held[p] for p in MID) > sum(t[p] for p in MID) + 1:
            mid_over += 1
    if with_short:
        return pos_over, mid_over, dict(by.most_common()), pos_short, dict(sby.most_common())
    return pos_over, mid_over, dict(by.most_common())


def pos5_nonmid(teams):
    def reg(pos):
        c = collections.Counter()
        for k, n in pos.items():
            c[k.partition("|")[0]] += n
        return c
    return sum(1 for r in teams if any(v >= 5 for k, v in reg(r[6]).items() if k not in ("CB",) + MID))


def main(paths):
    for path in paths:
        d = json.load(open(path, encoding="utf-8"))
        for label, teams in [("start", d["start"])] + [(f"season{i + 1}", t) for i, t in enumerate(d["seasons"])]:
            po, mo, by, ps, sby = score(teams, NEW, with_short=True)
            opo, omo, _ = score(teams, OLD)
            print(json.dumps({"dump": os.path.basename(path), "arm": d["arm"], "seed": d["seed"], "at": label,
                              "teams": len(teams), "pos_over": po, "mid_over": mo, "pos_over_by": by, "pos_short": ps, "pos_short_by": sby,
                              "pos5_nonmid": pos5_nonmid(teams), "old_pos_over": opo, "old_mid_over": omo},
                             ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])