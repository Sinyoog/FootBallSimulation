# -*- coding: utf-8 -*-
"""time_probe.py를 N회 반복해 최소값을 집계한다(측정 노이즈 대응).
사용: python3 tools/run_timing.py <tag> <반복횟수> [days]
결과는 timing_<tag>.json 에 누적 저장 — 여러 번 나눠 돌려도 합쳐진다."""
import json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tag = sys.argv[1]; n = int(sys.argv[2]); days = sys.argv[3] if len(sys.argv) > 3 else "370"
env = dict(os.environ, PYTHONHASHSEED="0")
path = os.path.join(ROOT, f"timing_{tag}.json")
acc = json.load(open(path)) if os.path.exists(path) else []
for i in range(n):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "time_probe.py"),
                        f"{tag}{len(acc)}", days], capture_output=True, text=True, env=env, cwd=ROOT)
    line = [l for l in r.stdout.strip().splitlines() if l.startswith("{")]
    if not line:
        print("FAIL", r.stderr[-500:]); break
    out = json.loads(line[-1])
    log = open(os.path.join(ROOT, "live_sim.log"), encoding="utf-8", errors="replace").read()
    def pick(key):
        hits = [l for l in log.splitlines() if key in l]
        return hits[-1].strip() if hits else ""
    out["tm"] = pick("[PERF-TM]"); out["off"] = pick("ai_offseason 세부")
    acc.append(out)
    print(f"  run{len(acc)-1}: total={out['total_370d']}s transition={out['transition']}s")
json.dump(acc, open(path, "w"), ensure_ascii=False, indent=0)
if acc:
    print(f"[{tag}] n={len(acc)}  transition min={min(x['transition'] for x in acc)}s  "
          f"total min={min(x['total_370d'] for x in acc)}s")