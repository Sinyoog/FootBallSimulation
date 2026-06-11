"""
check_save.py — 세이브/파일 버전 진단 스크립트
게임 폴더에서 `python check_save.py` 로 실행.
커리어가 안 올라갈 때 원인을 찾아준다.
"""
import os, sqlite3, sys

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, "game.db")

def main():
    print("=" * 46)
    print(" FootBallSimulation 세이브 진단")
    print("=" * 46)

    ok = True

    # 1. DB 존재
    if not os.path.exists(DB):
        print("❌ game.db 없음 — 게임을 한 번 실행해서 생성하세요.")
        return

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 2. team_id 마이그레이션 확인 (가장 흔한 원인)
    cols = [r[1] for r in c.execute("PRAGMA table_info(career_entries)")]
    if "team_id" not in cols:
        ok = False
        print("❌ career_entries에 team_id 컬럼 없음")
        print("   → database.py가 구버전입니다. 최신 database.py로 교체 후")
        print("     게임을 재실행하면 자동 마이그레이션됩니다.")
    else:
        print("✅ team_id 마이그레이션 적용됨")
    if "clean_sheets" not in cols:
        ok = False
        print("❌ clean_sheets 컬럼 없음 → database.py 구버전")

    # 3. 코드 버전 확인
    try:
        sys.path.insert(0, BASE)
        import constants
        shs = getattr(constants, "SECOND_HALF_START", None)
        oz  = getattr(constants, "OFFER_ZONES", None)
        if shs == 29 and oz:
            print("✅ constants.py 최신 (하반기 29주 시작)")
        else:
            ok = False
            print(f"❌ constants.py 구버전 (SECOND_HALF_START={shs})")
        import game_engine
        if hasattr(game_engine, "_find_open_entry"):
            print("✅ game_engine.py 최신 (team_id 조회 지원)")
        else:
            ok = False
            print("❌ game_engine.py 구버전")
        import database
        if hasattr(database, "sync_countries"):
            n = c.execute("SELECT COUNT(*) c FROM countries").fetchone()["c"]
            print(f"✅ database.py 최신 (국가 {n}개)")
        else:
            ok = False
            print("❌ database.py 구버전 (sync_countries 없음)")
    except Exception as e:
        ok = False
        print(f"❌ 코드 로딩 실패: {e}")

    # 4. 세이브 상태
    p = c.execute("""SELECT name, current_year, current_week, current_season,
                            current_team_id, season_matches FROM my_player WHERE id=1""").fetchone()
    if p:
        print(f"\n선수: {p['name']}  |  {p['current_year']}년 {p['current_week']}주차"
              f" (시즌 {p['current_season']})  |  이번 시즌 {p['season_matches']}경기")
        sel_cols = "id, team_name, matches" + (", team_id" if "team_id" in cols else "")
        open_entries = c.execute(
            f"SELECT {sel_cols} FROM career_entries WHERE end_year=0").fetchall()
        if p["current_team_id"] and not open_entries:
            print("⚠  소속 팀이 있는데 열린 커리어 항목이 없음 → 다음 4주 진행 시 자동 생성됨")
        for e in open_entries:
            has_tid = "team_id" in cols and e["team_id"]
            tag = "" if has_tid else "  (구버전 행 — 다음 갱신 때 자동 보정)"
            print(f"   열린 항목: {e['team_name']}  {e['matches']}경기{tag}")

    print("\n" + ("✅ 전부 정상 — 문제가 계속되면 콘솔 에러 메시지를 공유해주세요."
                  if ok else "⚠  위 항목을 해결한 뒤 게임을 재실행하세요."))
    conn.close()

if __name__ == "__main__":
    main()