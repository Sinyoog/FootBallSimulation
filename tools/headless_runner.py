"""
헤드리스 러너 v0 — 1시즌 스모크 테스트 전용.

목적: teams에 classification_status/parent_team_id/review_reason 컬럼을
추가한 새 스키마가 실제 게임 진행(월드 시뮬레이션) 중 크래시 없이
잘 굴러가는지만 확인한다. 승격 제한 로직 자체는 아직 구현 전이라
"산하팀이 실제로 배제되는지"는 이 스크립트로 확인 불가 — 그건 feature
flag 작업(다음 단계) 이후에 확인한다.

사용: python3 headless_runner.py [시즌수]

[2026-08 신설, 신민용 리포트: "실행할 때마다 결과 파일이 뒤죽박죽된다"]
두 가지를 고친다:
  1) 예전엔 database.DB_PATH를 따로 안 정해줘서 기본값(실제 세이브 파일
     game.db)을 그대로 썼다 — 이 스크립트를 직접 돌리면 더미 플레이어로
     오염된 데이터가 실제 세이브에 그대로 flush_to_disk()될 위험이 있었다.
  2) 실행마다 qa_runs/ 아래 타임스탬프 폴더를 새로 만들어 그 안에서만
     동작한다(DB 파일은 물론, game_engine.py가 상대경로로 남기는
     tier_audit.jsonl 같은 로그도 os.chdir() 덕에 자동으로 이 폴더 안에
     떨어진다) — 여러 번 돌린 결과가 프로젝트 루트에 섞이지 않는다.
run_ab_test.py/extended_ab_test.py처럼 호출부가 이미 자기 출력 폴더로
os.chdir()하고 database.DB_PATH를 직접 설정해둔 경우엔
standalone_output=False로 넘겨서 이 자동 폴더 생성을 건너뛴다(안 그러면
호출부가 만든 work_db를 이 함수가 다시 덮어써버림).

[격리하다가 발견한 문제] 이 스크립트는 seed_initial_data()(국가/리그/팀
데이터를 실제로 채우는 함수, main.py의 새 게임 생성 흐름에서만 호출됨)를
전혀 호출하지 않는다 — 그래서 예전처럼 DB_PATH가 우연히 이미 채워진
game.db를 가리킬 때만 굴러갔다(격리하고 나니 빈 스키마에서
countries 테이블이 비어 create_player가 즉시 죽는 게 드러남). 이제
standalone 모드에서는 스크립트가 있는 위치의 game.db(있다면)를 seed
템플릿으로 복사해서 쓴다 — 원본은 절대 안 건드리고 읽기만 한다.
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import datetime
import os
import shutil
import random
import sys

import database
import game_engine as ge
from affiliate_integrity import run_all_checks

_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_training_schedule(days=364):
    # 경기 없는 순수 훈련 스케줄. 더미 플레이어는 팀이 없으므로
    # _process_training/_sim_my_unscheduled_match가 전부 안전하게
    # no-op에 가깝게 처리된다(팀 없는 선수라 실제 경기 매칭이 없음).
    return [(d, "휴식", {}) for d in range(1, days + 1)]


def _make_run_dir(prefix: str) -> str:
    """qa_runs/{prefix}_{타임스탬프}/ 폴더를 새로 만들고 절대경로를 반환."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.abspath(os.path.join("qa_runs", f"{prefix}_{ts}"))
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def run(n_seasons: int, seed: int = 12345, restriction: bool = False,
        standalone_output: bool = True, src_db: str = None):
    random.seed(seed)

    if standalone_output:
        out_dir = _make_run_dir(f"headless_seed{seed}_n{n_seasons}")
        # src_db가 없으면 스크립트 옆의 game.db를 seed 템플릿으로 쓴다
        # (원본은 읽기만 하고 복사본만 건드린다 — 실제 세이브 보호).
        if src_db is None:
            _default_seed = os.path.join(_SCRIPT_DIR, "game.db")
            src_db = _default_seed if os.path.exists(_default_seed) else None
        db_path = os.path.join(out_dir, "headless.db")
        if src_db and os.path.exists(src_db):
            shutil.copy(os.path.abspath(src_db), db_path)
            print(f"[runner] 시드 DB 복사: {src_db} -> {db_path}")
        else:
            print("[runner] 경고: seed DB(game.db)를 찾지 못했다 — 빈 스키마로 "
                  "시작하며, countries/leagues 데이터가 없어 create_player가 "
                  "실패할 수 있다.")
        os.chdir(out_dir)
        database.DB_PATH = db_path
        print(f"[runner] 출력 폴더: {out_dir}")

    database.init_db()
    conn = database.get_conn()
    c = conn.cursor()

    import constants
    constants.AFFILIATE_PROMOTION_RESTRICTION = restriction
    print(f"[runner] AFFILIATE_PROMOTION_RESTRICTION = {restriction}")

    p = ge.get_player()
    if not p:
        # [2026-11 수정] 시드 템플릿(game.db)은 국가·리그·팀만 있고 선수가 없다 — 게임의 새 게임 흐름과 같은
        # 순서(reset_game_data → create_player)로 세계를 먼저 만든다. 예전엔 선수 없는 세계로 진행하다
        # team_power_rating 표가 없어 첫 주에 멈췄다. 이미 선수가 있는 DB(예전 방식)는 그대로 둔다.
        if not c.execute("SELECT 1 FROM ai_players LIMIT 1").fetchone():
            from constants import GAME_START_YEAR
            database.reset_game_data(start_year=GAME_START_YEAR)
        # [2026-11] DUMMY_NAT(예: 대한민국)를 주면 더미 선수 국적을 고정한다 — 없으면 예전처럼 무작위
        # (SQLite RANDOM()이라 시드로 고정되지 않음). 검증 런은 반드시 고정한다.
        _nat = os.environ.get("DUMMY_NAT")
        _kw = {}
        if _nat:
            _row = database.get_conn().execute("SELECT name, flag FROM countries WHERE name=?", (_nat,)).fetchone()
            if _row:
                _kw = {"nationality": _row["name"], "flag": _row["flag"]}
        ge.create_player(
            name="Headless Dummy",
            position="CM",
            sub_role="",
            **_kw,
        )
        p = ge.get_player()
    print(f"[runner] 더미 플레이어 준비 완료 (id={p['id']}, team={p.get('current_team_id')})")

    st = ge.get_state()
    start_season = st["current_season"]
    print(f"[runner] 시작 시즌: {start_season}")

    schedule = build_training_schedule()

    import intl_engine

    for i in range(n_seasons):
        cur_season_before = ge.get_state()["current_season"]
        # 국가대표 발탁 대기로 advance_days가 멈출 수 있으므로(더미
        # 플레이어도 무작위 국적이 배정돼 이 조건에 걸릴 수 있음), 멈출
        # 때마다 자동으로 전부 거절하고 이어서 진행한다. 안전 상한을 걸어
        # 무한루프를 방지한다(정상적으로는 한 시즌에 몇 번 안 걸림).
        remaining = list(schedule)
        for _guard in range(20):
            ge.advance_days(remaining)
            pending = intl_engine.get_pending_choice()
            if not pending:
                break
            for opt in pending.get("options", []):
                intl_engine.decline_national_team(opt["tournament_id"])
            cur_day = ge.get_state().get("current_day")
            remaining = [item for item in schedule if item[0] >= cur_day]
            if not remaining:
                break
        else:
            print(f"[runner] 경고: {i+1}번째 시즌에서 발탁 대기 처리가 "
                  f"안전 상한(20회)에 도달함")

        cur_season_after = ge.get_state()["current_season"]
        print(f"[runner] {i+1}/{n_seasons}시즌 진행 완료 "
              f"(season {cur_season_before} -> {cur_season_after})")

    # [2026-11 수정] 앱 종료(ui/main_window.closeEvent)와 같은 순서로 마지막 저장 — 진행 중인 자동저장(비동기)을
    # 기다리고 → 기록 DB 쓰기 큐를 비우고 → 한 번에 저장. 예전엔 바로 flush_to_disk()를 불러 시즌 전환 직후
    # 자동저장과 같은 임시 파일(.tmp)을 동시에 써서 disk I/O error로 끝나는 일이 있었다(세이브는 자동저장본이 남음).
    database.wait_for_pending_flush(timeout=1800)
    database.history_drain()
    database.flush_to_disk(final=True)

    print("\n[runner] === 종료 후 무결성 재검사 ===")
    # [2026-11 수정] 진행 중 커넥션이 교체돼 시작 때 만든 커서(c)가 닫혀 있을 수 있다 — 지금 커넥션으로 새로 연다.
    result = run_all_checks(database.get_conn().cursor())
    print("[runner] 통과 여부:", result["passed"])
    return result


if __name__ == "__main__":
    # python tools/headless_runner.py [시즌 수] [시드]   (시드 생략 시 12345 — 예전과 같음)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 12345
    run(n, seed=seed)