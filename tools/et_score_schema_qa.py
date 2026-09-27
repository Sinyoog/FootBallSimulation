"""et_score_schema_qa.py — 연장 스코어 컬럼 스키마 일치 검사 (2026-09 신설)

[왜 이 스크립트가 있는가] 신민용 지적: "DB 마이그레이션과 CREATE TABLE
정의가 어긋나면 나중에 다시 꼬일 가능성이 있어서, 한 번에 정리하는 게 좋다.
ALTER TABLE 마이그레이션과 각 엔진의 CREATE TABLE IF NOT EXISTS를 같은
작업에서 맞춘다." — hist.* PK 불일치 때 실제로 당한 함정이 정확히 이것이라,
그 일치를 사람 눈이 아니라 스크립트가 매번 확인하게 만든다.

두 경로를 각각 독립적으로 재현해서 결과가 같은지 본다:
  (A) 새 DB    : 빈 파일 → init_db() → CREATE TABLE 경로만 탄다
  (B) 기존 세이브: 컬럼이 없던 기존 game.db 사본 → init_db() → ALTER 경로

둘 다 database.ET_SCORE_MATCH_TABLES 10개 테이블 전부에
home_score_90 / away_score_90 / went_extra_time 이 있어야 통과.
(B)는 기존 행의 값이 기본값(-1/-1/0)으로 들어왔는지도 같이 본다 — 구
세이브의 과거 경기는 "미기록"으로 남아야 하고, 화면은 그걸 최종 스코어만
보여주는 경로로 처리한다.

사용법:
    python tools/et_score_schema_qa.py [기존세이브.db]
기존 세이브를 안 주면 (A)만 검사한다.
"""
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _check(db_path, label, expect_defaults=False):
    """db_path를 열어 10개 테이블 × 3컬럼을 확인한다. (통과여부, 메시지목록)."""
    import database as d

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    msgs = []
    missing = []
    checked = 0
    for tbl in d.ET_SCORE_MATCH_TABLES:
        rows = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
        if not rows:
            msgs.append(f"  - {tbl}: 테이블 없음 (검사 제외)")
            continue
        checked += 1
        have = {r[1] for r in rows}
        for name, _type in d._ET_SCORE_COLS:
            if name not in have:
                missing.append(f"{tbl}.{name}")

    default_problems = []
    if expect_defaults and not missing:
        # 기존 행이 실제로 기본값으로 채워졌는지 — ALTER TABLE ... DEFAULT는
        # 기존 행에도 그 값을 넣는다(SQLite 보장). 행이 있는 표만 본다.
        for tbl in d.ET_SCORE_MATCH_TABLES:
            try:
                row = conn.execute(
                    f"""SELECT COUNT(*) AS n,
                               SUM(home_score_90 <> -1) AS bad_h,
                               SUM(away_score_90 <> -1) AS bad_a,
                               SUM(went_extra_time <> 0) AS bad_e
                        FROM {tbl}""").fetchone()
            except sqlite3.OperationalError:
                continue
            if not row or not row["n"]:
                continue
            bad = (row["bad_h"] or 0) + (row["bad_a"] or 0) + (row["bad_e"] or 0)
            if bad:
                default_problems.append(f"{tbl}: {bad}/{row['n']}행이 기본값 아님")
            else:
                msgs.append(f"  - {tbl}: 기존 {row['n']}행 전부 미기록(-1/-1/0) ✓")

    conn.close()

    ok = not missing and not default_problems
    print(f"[{label}] {'통과' if ok else '실패'} — 검사한 테이블 {checked}개")
    for m in msgs:
        print(m)
    if missing:
        print("  ✗ 누락된 컬럼:")
        for m in missing:
            print(f"      {m}")
    if default_problems:
        print("  ✗ 기본값 문제:")
        for m in default_problems:
            print(f"      {m}")
    return ok


def _run_init_db(db_path):
    """database를 디스크 직결 모드로 돌려 init_db()를 db_path에 적용한다.

    USE_MEMORY_DB=True(기본)면 라이브 DB가 인메모리라 파일 스키마를 직접
    볼 수 없다 — 검사 목적이므로 database.py가 이미 지원하는 폴백 경로
    (USE_MEMORY_DB=False, "문제가 생기면 이 플래그 하나만 False로")를 쓴다.
    """
    import database as d
    d.USE_MEMORY_DB = False
    d.DB_PATH = db_path
    d.reset_conn_pool()
    d.init_db()
    d.reset_conn_pool()


def main():
    tmpdir = tempfile.mkdtemp(prefix="et_score_qa_")
    results = []

    # ── (A) 새 DB: CREATE TABLE 경로 ──────────────────────────────
    fresh = os.path.join(tmpdir, "fresh.db")
    _run_init_db(fresh)
    results.append(_check(fresh, "A. 새 DB (CREATE TABLE 경로)"))

    # ── (B) 기존 세이브: ALTER TABLE 경로 ─────────────────────────
    if len(sys.argv) > 1:
        src = sys.argv[1]
        if not os.path.exists(src):
            print(f"[B] 건너뜀 — 파일 없음: {src}")
        else:
            old = os.path.join(tmpdir, "old.db")
            shutil.copy(src, old)
            # 사전 확인: 이 사본엔 정말 컬럼이 없어야 ALTER 경로 검사가 의미 있다.
            _c = sqlite3.connect(old)
            _pre = [r[1] for r in _c.execute("PRAGMA table_info(cup_matches)")]
            _c.close()
            print(f"[B] 사전 상태 — cup_matches에 home_score_90 "
                  f"{'있음(ALTER 검사 의미 없음)' if 'home_score_90' in _pre else '없음 ✓'}")
            _run_init_db(old)
            results.append(_check(old, "B. 기존 세이브 (ALTER TABLE 경로)",
                                  expect_defaults=True))
    else:
        print("[B] 건너뜀 — 기존 세이브 경로를 인자로 주면 ALTER 경로도 검사합니다.")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print()
    if all(results) and results:
        print("=== 전체 통과: CREATE 경로와 ALTER 경로가 같은 스키마를 만든다 ===")
        return 0
    print("=== 실패 ===")
    return 1


if __name__ == "__main__":
    sys.exit(main())