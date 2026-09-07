"""
다중 seed OFF/ON 검증 — bit-level 재현성 대신, "정책이 통계적으로
안정적으로 작동하는가"를 여러 독립 시드로 검증한다.

각 seed마다 OFF/ON을 완전히 독립된 새 DB에서 실행하고, 매 시즌:
  AFFILIATE AUTO 승격 = 0 (ON 기준)
  AFFILIATE PO 승격   = 0 (ON 기준)
  REVIEW AUTO 승격    = 0 (ON 기준)
  REVIEW PO 승격      = 0 (ON 기준)
  1부 위반(정책 예외 제외) = 0 (ON 기준)
  자기참조             = 0 (ON/OFF 공통)
  크래시               = 0 (ON/OFF 공통)
  진행 중단            = 0 (ON/OFF 공통)
를 확인한다. OFF는 대조군으로 "제한 없으면 실제로 새는가"를 같이 기록한다.

사용: python3 multi_seed_validation.py <시작DB> <시즌수> <seed1> [seed2] ...
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import sys

from extended_ab_test import run_extended


def summarize(mode, seed, rows):
    total_auto_aff = sum(r["auto_aff"] for r in rows)
    total_auto_rev = sum(r["auto_rev"] for r in rows)
    total_po_aff = sum(r["po_aff"] for r in rows)
    total_po_rev = sum(r["po_rev"] for r in rows)
    total_crash = sum(1 for r in rows if r["crashed"])
    total_stall = sum(1 for r in rows if r["stalled"])
    final_viol = rows[-1]["tier1_violations"] if rows else None
    final_selfp = rows[-1]["self_parent"] if rows else None
    max_viol = max((r["tier1_violations"] for r in rows), default=0)
    max_selfp = max((r["self_parent"] for r in rows), default=0)
    seasons_completed = len(rows)
    return {
        "mode": mode, "seed": seed, "seasons_completed": seasons_completed,
        "total_auto_aff": total_auto_aff, "total_auto_rev": total_auto_rev,
        "total_po_aff": total_po_aff, "total_po_rev": total_po_rev,
        "max_tier1_violations": max_viol, "final_tier1_violations": final_viol,
        "max_self_parent": max_selfp, "final_self_parent": final_selfp,
        "crash_seasons": total_crash, "stall_seasons": total_stall,
    }


def main():
    src_db = sys.argv[1]
    n_seasons = int(sys.argv[2])
    seeds = [int(s) for s in sys.argv[3:]]

    summaries = []
    for seed in seeds:
        for mode in ("off", "on"):
            print(f"\n########## seed={seed} mode={mode} ##########")
            rows = run_extended(mode, src_db, n_seasons, seed)
            summ = summarize(mode, seed, rows)
            summaries.append(summ)
            print(f"[SUMMARY] {summ}")

    print("\n\n===================== 최종 요약 =====================")
    header = (f"{'seed':>6} {'mode':>4} | {'seasons':>7} | {'auto_aff':>8} {'auto_rev':>8} "
              f"{'po_aff':>7} {'po_rev':>7} | {'max_viol':>8} {'final_viol':>10} | "
              f"{'max_selfp':>9} | {'crash':>5} {'stall':>5}")
    print(header)
    all_pass = True
    for s in summaries:
        print(f"{s['seed']:>6} {s['mode']:>4} | {s['seasons_completed']:>7} | "
              f"{s['total_auto_aff']:>8} {s['total_auto_rev']:>8} "
              f"{s['total_po_aff']:>7} {s['total_po_rev']:>7} | "
              f"{s['max_tier1_violations']:>8} {s['final_tier1_violations']:>10} | "
              f"{s['max_self_parent']:>9} | {s['crash_seasons']:>5} {s['stall_seasons']:>5}")
        if s["mode"] == "on":
            ok = (s["total_auto_aff"] == 0 and s["total_auto_rev"] == 0
                  and s["total_po_aff"] == 0 and s["total_po_rev"] == 0
                  and s["max_tier1_violations"] == 0 and s["max_self_parent"] == 0
                  and s["crash_seasons"] == 0 and s["stall_seasons"] == 0
                  and s["seasons_completed"] == n_seasons)
            if not ok:
                all_pass = False
                print(f"   !!! seed={s['seed']} ON 기준 미달 !!!")

    print(f"\n최종 판정: {'PASS' if all_pass else 'FAIL'}")


if __name__ == "__main__":
    main()