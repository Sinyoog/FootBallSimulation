"""
[2026-08 신설] 이미 진행 중인 세이브(game.db)에 산하팀 분류를 소급
적용하는 1회성 스크립트. affiliate_classify.py의 경로 버그(data/ 빠짐)
때문에 지금까지 모든 팀이 classification_status='NORMAL'로만 남아있어서
(parent_team_id도 전부 NULL) AFFILIATE_PROMOTION_RESTRICTION이 지켜줄
대상 자체가 없었다 — 그래서 유스팀(B팀)이 모구단을 추월해 상위 리그로
올라가는 게 안 막혔다.

주의사항:
  - 이 스크립트는 데이터 분류만 적용한다. 지금 이미 붕괴된 서열
    (예: CD 온다 B가 1부, 모구단 CD 온다가 6부)은 이 스크립트가 자동
    으로 되돌려주지 않는다 — 그건 다음 시즌 종료 시점에
    _process_promotion_relegation의 사후 보정 로직이 정상적으로
    작동하기 시작하면서 점진적으로(승격 취소/강제 강등) 정리된다.
  - 여러 번 승강을 거친 세이브라 current_tier가 원본 데이터와
    달라졌을 팀은 이름+국가만으로 재시도하는 기존 매칭 로직을 그대로
    쓴다 — 동명 팀이 여럿이면 안전하게 건너뛴다(강제로 아무거나
    고르지 않음).

사용법: game.db와 같은 폴더에 넣고 `py apply_affiliate_to_save.py`
실행 전에 game.db 백업 권장.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game.db")

if not os.path.exists(DB_PATH):
    print(f"game.db를 못 찾았어: {DB_PATH}")
    raise SystemExit(1)

jsonl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "affiliate_raw.jsonl")
if not os.path.exists(jsonl_path):
    print(f"data/affiliate_raw.jsonl을 못 찾았어: {jsonl_path}")
    print("이 파일이 있어야 분류를 적용할 수 있어 — 게임 폴더 안 data/ 서브폴더를 확인해줘.")
    raise SystemExit(1)

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from affiliate_classify import apply_classification

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

before = c.execute(
    "SELECT classification_status, COUNT(*) FROM teams GROUP BY classification_status"
).fetchall()
print("적용 전:", dict(before))

stats = apply_classification(c, jsonl_path=jsonl_path)

conn.commit()

after = c.execute(
    "SELECT classification_status, COUNT(*) FROM teams GROUP BY classification_status"
).fetchall()
print("적용 후:", dict(after))

conn.close()
print("\n완료 — 게임을 다시 실행해봐. 이미 무너진 서열은 다음 시즌 종료 때 정리되기 시작할 거야.")