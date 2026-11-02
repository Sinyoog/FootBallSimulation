# -*- coding: utf-8 -*-
"""OFF 기준선 래퍼 — tools/run_sqv31_season.py(SQV31=0)를 그대로 돌리고, 읽기 전용 지표만 덧붙인다.
게임 함수는 감싸서 원본을 그대로 호출만 한다(인자·반환값·난수 소비 변경 없음).
추가 지표(시즌마다):
  gen_hash      그 해 생성된 선수(created_year=그 해) id·생성경로·포지션·OVR·잠재력·나이·국적 해시
  gen_by_src    그 해 생성 선수 생성 경로별 인원
  retired_hash  ai_players_retired 전체 해시
  forced_out    _rebalance_squad_sizes 반환 조기은퇴 인원(= squad_forced_out)
  archive_all   _archive_forced_out_players로 들어간 전체 인원(모든 강제 삭제 경로 합)
사용: python3 off_baseline.py <code_root>  (환경변수 SQV3_SEED, SQV3_SEASONS)"""
import collections
import hashlib
import json
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
os.environ["SQV3_ROOT"] = ROOT
os.environ["SQV31"] = "0"
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.argv = [sys.argv[0]]
import run_sqv31_season as RS  # noqa: E402

_CNT = collections.defaultdict(lambda: {"forced_out": 0, "archive_all": 0})
_orig_metrics = RS.metrics
_patched = [False]


def _patch():
    import ai_lifecycle as AL
    o_rb, o_ar = AL._rebalance_squad_sizes, AL._archive_forced_out_players

    def rb(c, year):
        r = o_rb(c, year)
        _CNT[year]["forced_out"] += r[1]
        return r

    def ar(c, ids, year):
        _CNT[year]["archive_all"] += len(set(ids or ()))
        return o_ar(c, ids, year)
    AL._rebalance_squad_sizes = rb
    AL._archive_forced_out_players = ar


def _h(c, sql, args=()):
    h = hashlib.sha256()
    for r in c.execute(sql, args):
        h.update(repr(tuple(r)).encode())
    return h.hexdigest()[:16]


def metrics(c, C):
    if not _patched[0]:
        _patch()
        _patched[0] = True
    m = _orig_metrics(c, C)
    y = c.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()[0]
    m["cur_year"] = y
    m["gen_hash_all"] = _h(c, "SELECT id, creation_source, created_year, position, ovr, potential_ovr, age, "
                              "nationality FROM ai_players ORDER BY id")
    m["gen_by_src_prev_year"] = dict(c.execute(
        "SELECT creation_source, COUNT(*) FROM ai_players WHERE created_year=? GROUP BY 1 ORDER BY 1",
        (y - 1,)).fetchall())
    m["gen_by_src_cur_year"] = dict(c.execute(
        "SELECT creation_source, COUNT(*) FROM ai_players WHERE created_year=? GROUP BY 1 ORDER BY 1",
        (y,)).fetchall())
    m["retired_hash"] = _h(c, "SELECT * FROM ai_players_retired ORDER BY id")
    m["retired_n"] = c.execute("SELECT COUNT(*) FROM ai_players_retired").fetchone()[0]
    m["forced_counts"] = {str(k): dict(v) for k, v in sorted(_CNT.items())}
    print("@@PROGRESS@@" + json.dumps({"year": y, "hash": RS.table_hash(c)}), file=sys.__stderr__, flush=True)
    return m


RS.metrics = metrics
RS.main()