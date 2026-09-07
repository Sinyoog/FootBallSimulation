# -*- coding: utf-8 -*-
"""헤드리스 실행 결과(game.db + game.history.db) 전체 테이블 해시 덤프.

사용: python3 verify_determinism.py <run_dir> [출력.json]
det_probe.py가 flush_to_disk()로 남긴 headless.db / headless.history.db를
읽어, 모든 테이블을 rowid(없으면 전체 컬럼) 순으로 정렬해 해시한다.
정렬 순서를 명시하는 이유: ORDER BY 없는 스캔 순서 자체는 재현성 판정의
대상이 아니라(같은 내용이어도 물리적 순서는 달라질 수 있음) 노이즈다.
"""
import hashlib
import json
import os
import sqlite3
import sys

run_dir = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else None
res = {}
for label, fn in (("main", "headless.db"), ("hist", "headless.history.db")):
    path = os.path.join(run_dir, fn)
    if not os.path.exists(path):
        continue
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.text_factory = str
    tabs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tabs:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        try:
            order = "rowid"
            conn.execute(f"SELECT rowid FROM {t} LIMIT 1").fetchall()
        except sqlite3.OperationalError:
            order = ",".join(f'"{c}"' for c in cols)
        h = hashlib.blake2b(digest_size=16)
        n = 0
        for row in conn.execute(f"SELECT * FROM {t} ORDER BY {order}"):
            n += 1
            h.update(repr(row).encode("utf-8", "replace"))
        res[f"{label}.{t}"] = [n, h.hexdigest()]
    conn.close()

total = hashlib.blake2b(digest_size=16)
for k in sorted(res):
    total.update(f"{k}:{res[k][0]}:{res[k][1]}|".encode())
res["__TOTAL__"] = [sum(v[0] for k, v in res.items()), total.hexdigest()]
if out:
    json.dump(res, open(out, "w"), indent=0)
print(json.dumps({"tables": len(res) - 1, "rows": res["__TOTAL__"][0],
                  "TOTAL_HASH": res["__TOTAL__"][1]}))
