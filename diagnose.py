"""
[2026-08 신설] "no such column: classification_status" 크래시가 최신
database.py를 넣었는데도 계속 나는 경우 진단용. 실제로 파이썬이 어느
database.py 파일을 import하고 있는지, 그 파일에 마이그레이션 코드가
있는지, 그리고 실제 game.db 스키마가 뭔지 한 번에 확인한다.

사용법: 이 파일을 FootBallSimulation-main 폴더(main.py와 같은 위치)에
넣고 `python diagnose.py` 실행.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database

print("=" * 60)
print("1) 실제로 로드된 database.py 경로:")
print("  ", database.__file__)

src_path = database.__file__
with open(src_path, encoding="utf-8") as f:
    src = f.read()

print()
print("=" * 60)
print("2) 이 파일에 classification_status 마이그레이션 코드가 있는가?")
has_fix = 'if "classification_status" not in _existing_team_cols' in src
print("  ->", "있음 (최신본 맞음)" if has_fix else "없음 (구버전!)")

print()
print("=" * 60)
print("3) DB_PATH 및 game.db 파일 존재 여부:")
print("   DB_PATH =", database.DB_PATH)
print("   존재함?", os.path.exists(database.DB_PATH))

print()
print("=" * 60)
print("4) game.db 실제 스키마(teams 테이블 컬럼) — sqlite3로 직접 열어서 확인:")
import sqlite3
if os.path.exists(database.DB_PATH):
    conn = sqlite3.connect(database.DB_PATH)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(teams)").fetchall()]
    print("  ", cols)
    conn.close()
else:
    print("   game.db가 아직 없음 — 새 게임을 한 번도 시작 안 한 상태일 수 있음")

print()
print("=" * 60)
print("5) __pycache__에 남은 컴파일 캐시 확인 (구버전 .pyc가 우선 로드될 가능성):")
pycache_dir = os.path.join(os.path.dirname(src_path), "__pycache__")
if os.path.isdir(pycache_dir):
    matches = [f for f in os.listdir(pycache_dir) if f.startswith("database.")]
    print("  ", matches if matches else "database 관련 캐시 없음")
else:
    print("   __pycache__ 폴더 없음")