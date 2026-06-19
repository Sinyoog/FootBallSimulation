# -*- coding: utf-8 -*-
# 기존 세이브의 잘못 고정된 국가대표 국적을 초기화한다.
# FootBallSimulation 폴더에 두고:  py reset_nat.py
# 실행 후 다음 국가대표 대회(아프리카 네이션스컵/월드컵)에서 다시 선택 팝업이 뜬다.
import sqlite3, os
db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game.db")
c = sqlite3.connect(db)
p = c.execute("SELECT nationality, nationality2, nationality3, intl_committed FROM my_player WHERE id=1").fetchone()
print("현재 고정국적:", p[3] if p else "?")
c.execute("UPDATE my_player SET intl_committed='' WHERE id=1")
# 아직 안 끝난(미래) 대회의 선택상태도 초기화 → 다시 제안받도록
c.execute("UPDATE intl_tournaments SET my_nat='', my_selected=3 WHERE status<>'done' AND my_selected IN (1,2)")
c.commit()
c.close()
print("초기화 완료. 이제 다음 대표팀 발탁 때 직접 선택할 수 있어.")