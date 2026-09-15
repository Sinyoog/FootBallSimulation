"""[2026-09 신설, 신민용 리포트: "은퇴 후 새 시작 하면 완전히 처음 게임을
깔고 시작하는 거랑 같이 깨끗해야 하는데 뭐가 남는 거 같다"]
database.reset_game_data()의 "새 표/새 컬럼을 삭제·초기화 목록에 추가하는 걸
깜빡함" 버그가 이 프로젝트에서 10회 넘게 재발했다. 사람이 목록을 눈으로
대조하는 대신, 실제로 플레이된 세이브 사본에서 reset을 돌린 뒤
  (1) 시드 표(국가/리그/팀/선수 원본 등)를 뺀 모든 main/hist 표에 행이 남았는지
  (2) 팀 표의 "흐름" 컬럼(모멘텀/연속 강등/정체)이 기본값으로 돌아왔는지
를 기계적으로 검사한다. 새 표를 추가했으면 이 스크립트를 한 번 돌리면 된다.

사용: python tools/reset_residue_qa.py
  - 프로젝트 루트의 game.db(+game.history.db)를 qa_runs/reset_residue/로 복사해
    그 사본에서만 실행한다(원본 세이브는 읽기만 함). 여러 시즌 진행된 세이브일수록
    검사가 의미 있다(비어 있는 표는 남을 게 없으므로).
  - 남은 표가 있으면 목록을 찍고 종료코드 1, 깨끗하면 0.
"""
import _path  # noqa: F401
import os
import shutil
import sys

ROOT = _path.ROOT
# reset_game_data가 지우지 않고 원본 상태로 "복원/재생성"하는 표 — 행이 있는 게 정상.
SEED_TABLES = {
    "ai_players", "ai_players_seed", "countries", "leagues", "teams", "meta",
    "player_names", "team_formation_seed", "sqlite_sequence", "sqlite_stat1",
}
TEAM_FLOW_COLS = ("momentum_type", "momentum_seasons_left", "relegation_streak", "stagnation_streak")


def main():
    src = os.path.join(ROOT, "game.db")
    if not os.path.exists(src):
        print("[reset-qa] game.db가 없습니다 — 몇 시즌 진행한 세이브가 있어야 검사할 수 있습니다.")
        return 2
    out_dir = os.path.join(ROOT, "qa_runs", "reset_residue")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    shutil.copy(src, os.path.join(out_dir, "game.db"))
    for sfx in ("", "-wal", "-shm"):
        h = os.path.join(ROOT, "game.history.db" + sfx)
        if os.path.exists(h):
            shutil.copy(h, os.path.join(out_dir, "game.history.db" + sfx))
    os.chdir(out_dir)

    import database
    database.DB_PATH = os.path.join(out_dir, "game.db")
    database.flush_to_disk_async = lambda: None
    database.init_db()
    database.reset_game_data()
    conn = database.get_conn()

    problems = []
    for schema in ("main", "hist"):
        for (t,) in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'").fetchall():
            if t in SEED_TABLES:
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {schema}.{t}").fetchone()[0]
            if n:
                problems.append(f"표 {schema}.{t}: {n}행 남음")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(teams)").fetchall()}
    for col in TEAM_FLOW_COLS:
        if col not in cols:
            continue
        n = conn.execute(f"SELECT COUNT(*) FROM teams WHERE COALESCE({col}, '') NOT IN ('', 0, '0')").fetchone()[0]
        if n:
            problems.append(f"teams.{col}: {n}팀이 기본값 아님")
    conn.close()
    os.chdir(ROOT)
    if problems:
        print("[reset-qa] 새 게임에 이전 판 데이터가 남습니다:")
        for p in problems:
            print("  -", p)
        return 1
    print("[reset-qa] 깨끗합니다 — 시드 표 외에 남은 데이터 없음.")
    return 0


if __name__ == "__main__":
    sys.exit(main())