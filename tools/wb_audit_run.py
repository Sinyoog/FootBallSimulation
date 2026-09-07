"""
매 시즌 종료 직후 모든 AFFILIATE/REVIEW 팀의 parent-tier 불변식을 스냅샷
찍어서 season_boundary_audit.jsonl에 남긴다. game_engine.py의
tier_audit.jsonl(보정 함수 내부 결정 기록)과 조합해서 "몇 시즌에 위반이
생겼다가, 몇 시즌에 없어졌다가, 몇 시즌에 다시 생겼는지"를 팀 단위로
추적할 수 있다.

사용: python3 wb_audit_run.py <db_path> <시즌수> <seed>
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import json
import os
import sys

import database
import game_engine as ge
import intl_engine
from affiliate_integrity import check_parent_tier_coexistence


def snapshot_season_boundary(c, season_label):
    # [2026-08 정책 변경] 동일/역전 tier 공존은 이제 콜업으로 처리되는
    # 정상 상태라 "위반/구조적 예외" 구분이 의미 없어졌다 — 그냥 몇 쌍이
    # 공존 중인지만 기록한다.
    rows = check_parent_tier_coexistence(c)
    with open("season_boundary_audit.jsonl", "a", encoding="utf-8") as f:
        for team_id, name, tier, parent_name, parent_tier in rows:
            f.write(json.dumps({
                "season_label": season_label, "team_id": team_id, "name": name,
                "tier": tier, "parent_name": parent_name, "parent_tier": parent_tier,
            }, ensure_ascii=False) + "\n")
    return rows


def main():
    db_path = sys.argv[1]
    n_seasons = int(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 12345

    import random
    random.seed(seed)

    database.DB_PATH = os.path.abspath(db_path)
    database.init_db()
    database.flush_to_disk_async = lambda: None
    conn = database.get_conn()
    c = conn.cursor()

    p = ge.get_player()
    if not p:
        ge.create_player(name="Headless Dummy", position="CM", sub_role="")

    schedule = [(d, "휴식", {}) for d in range(1, 365)]
    for s in range(n_seasons):
        remaining = list(schedule)
        for _ in range(20):
            ge.advance_days(remaining)
            pending = intl_engine.get_pending_choice()
            if not pending:
                break
            for opt in pending.get("options", []):
                intl_engine.decline_national_team(opt["tournament_id"])
            cur_day = ge.get_state().get("current_day")
            remaining = [it for it in schedule if it[0] >= cur_day]
            if not remaining:
                break
        rows = snapshot_season_boundary(c, s + 1)
        print(f"[season {s+1}] 종료 후 위반 건수: {len(rows)}")

    database.flush_to_disk()


if __name__ == "__main__":
    main()