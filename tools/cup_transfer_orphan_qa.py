# -*- coding: utf-8 -*-
"""[2026-09 신설] "여름 비시즌 이적 후 컵 일정이 멈춘다" 버그 재현/검증.

신민용 리포트: 2001년 11월인데 8월 컵 경기가 계속 "예정"으로 남아있고,
메인 화면 주간 카드에도 컵대회가 안 뜬다.

■ 재현 원리
  · 경기 생성(_start_next_round)        → "지금 소속팀"으로 is_my를 찍는다
  · 경기 진행(get_my_cup_match) → cup_tournaments.my_team_id
    (대회 시작 시점 소속팀)과 지금 팀이 같을 때만 플레이어에게 넘긴다
  시즌 중 이적으로 이 둘이 어긋나면, 새 팀 경기가 is_my=1이라
  process_cup_week의 AI 자동 진행(is_my=0 필터)에서 빠지고 플레이어에게도
  안 넘어가는 "고아 경기"가 된다 → _advance_round가 그 라운드에서 멈춘다.

■ 이 스크립트가 하는 일
  실제 세이브 사본을 시즌 중반까지 진행시킨 뒤, 진행 중인 컵대회 하나를
  골라 "대회 등록팀은 A인데 지금 내 팀은 B"인 상태를 인위적으로 만들고
  (= 여름 이적 직후 상태), 남은 주차를 전부 돌려 대회가 끝까지 가는지 본다.

■ 주의 — 이 코드베이스는 USE_MEMORY_DB=True다
  실제 플레이는 인메모리 DB에서 돌고 디스크 파일에는 flush_to_disk로만
  반영된다. 검증 쿼리를 디스크 사본(headless.db)에 직접 날리면 "한 번도
  안 바뀐 원본"을 읽게 되어 무조건 통과해버린다(2026-09에 실제로 이
  함정에 한 번 걸렸다). 반드시 database.get_conn()으로 읽어야 한다.

사용: python3 tools/cup_transfer_orphan_qa.py
"""
import _path  # noqa: F401
import os
import random
import shutil
import sys

ROOT = _path.ROOT
out_dir = os.path.join(ROOT, "qa_runs", "cup_orphan")
shutil.rmtree(out_dir, ignore_errors=True)
os.makedirs(out_dir)
db_path = os.path.join(out_dir, "headless.db")
shutil.copy(os.path.join(ROOT, "game.db"), db_path)
_h = os.path.join(ROOT, "game.history.db")
for _s in ("", "-wal", "-shm"):
    if os.path.exists(_h + _s):
        shutil.copy(_h + _s, os.path.join(out_dir, "headless.history.db" + _s))

sys.path.insert(0, ROOT)
import database
database.DB_PATH = db_path
os.chdir(out_dir)
database.init_db()
database.flush_to_disk_async = lambda: None

import game_engine as ge
import intl_engine
from competition import cup_engine

random.seed(4242)
if not ge.get_player():
    ge.create_player(name="Orphan QA", position="CM", sub_role="")

# 세이브가 1주차 프리시즌이라 컵이 아직 안 열려 있다 — 중반까지 진행.
cur = ge.get_state().get("current_day") or 1
sched = [(d, "휴식", {}) for d in range(cur, cur + 150)]
rem = list(sched)
for _g in range(20):
    ge.advance_days(rem)
    pend = intl_engine.get_pending_choice()
    if not pend:
        break
    for opt in pend.get("options", []):
        intl_engine.decline_national_team(opt["tournament_id"])
    cd = ge.get_state().get("current_day")
    rem = [i for i in sched if i[0] >= cd]
    if not rem:
        break
print(f"현재 주차: {ge.get_state().get('current_week')}")

conn = database.get_conn()
t = conn.execute(
    """SELECT t.* FROM cup_tournaments t
       WHERE t.status='active'
         AND EXISTS(SELECT 1 FROM cup_matches m
                    WHERE m.tournament_id=t.id AND m.home_score=-1)
       LIMIT 1""").fetchone()
if not t:
    print("진행 중인 컵대회가 없다 — 재현 불가")
    sys.exit(0)
tid, year = t["id"], t["year"]
alive = [r["team_id"] for r in conn.execute(
    "SELECT team_id FROM cup_entries WHERE tournament_id=? AND alive=1 ORDER BY team_id",
    (tid,)).fetchall()]
if len(alive) < 2:
    print("생존팀 2팀 미만 — 재현 불가")
    sys.exit(0)
team_a, team_b = alive[0], alive[1]
print(f"대회 id={tid} ({t['name']}, {year}년) | 생존팀 {len(alive)} | A={team_a} B={team_b}")

# 여름 이적 직후 상태: 등록팀은 A, 지금 내 팀은 B.
conn.execute("UPDATE cup_tournaments SET my_in=1, my_team_id=? WHERE id=?", (team_a, tid))
conn.execute("UPDATE my_player SET current_team_id=? WHERE id=1", (team_b,))
conn.commit()
ge._invalidate_state_cache()
print(f"이적 흉내: my_player.current_team_id={team_b} / cup_tournaments.my_team_id={team_a}")

for wk in range(1, 53):
    try:
        cup_engine.process_cup_week(wk)
    except Exception as e:
        print(f"  week {wk} 예외: {e}")

conn = database.get_conn()
total = conn.execute("SELECT COUNT(*) FROM cup_matches WHERE tournament_id=?", (tid,)).fetchone()[0]
st = conn.execute("SELECT status, my_team_id, my_in FROM cup_tournaments WHERE id=?", (tid,)).fetchone()
reg = st["my_team_id"] or 0
left = [dict(r) for r in conn.execute(
    "SELECT * FROM cup_matches WHERE tournament_id=? AND home_score=-1", (tid,)).fetchall()]

# 판정 기준: "미완료 경기 0"이 아니다. 수정 후에는 새 팀(B) 경기가
# 정상적으로 내 경기(is_my=1)로 남아 플레이어의 출전을 기다리는 게 맞는
# 동작이고, 헤드리스라 아무도 그 경기를 뛰지 않으니 -1로 남는 게 정상이다.
# 진짜 고장은 "아무도 진행시킬 수 없는 경기"가 남는 것 —
#   is_my=1 인데 그 경기에 대회 등록팀(my_team_id)이 안 들어있는 경우.
# 이러면 AI 자동진행(is_my=0 필터)에서도 빠지고
# get_my_cup_match(등록팀≠현재팀이면 None)에서도 빠진다.
orphans = [m for m in left
           if m["is_my"] and reg not in (m["home_team_id"], m["away_team_id"])]
playable = [m for m in left
            if m["is_my"] and reg in (m["home_team_id"], m["away_team_id"])]
print(f"결과: 전체 {total}경기 | 미완료 {len(left)}경기 "
      f"(플레이어 대기 {len(playable)} / 고아 {len(orphans)}) | "
      f"status={st['status']} my_team_id={reg} my_in={st['my_in']}")

# 대기 중인 경기가 실제로 플레이어에게 넘어오는지도 직접 확인한다.
handed = 0
for m in playable:
    try:
        if cup_engine.get_my_cup_match(m["week"]):
            handed += 1
    except Exception as e:
        print("  get_my_cup_match 예외:", e)
print(f"      플레이어에게 실제로 넘어온 경기: {handed}/{len(playable)}")

ok = (not orphans) and (handed == len(playable))
print("PASS — 고아 경기 없음, 대기 경기는 전부 플레이어에게 전달됨" if ok else
      "FAIL — 아무도 진행시킬 수 없는 경기가 남았다")

# ── 챔스/유로파/컨퍼런스도 같은 결함이 있었다(2026-09 표준화) ────────
# 이쪽은 _process_one이 is_my 필터 없이 전부 AI로 돌려서 "대회가 멈추는"
# 증상은 없지만, 여름 이적 후 새 소속팀 대항전을 시즌 내내 직접 못 뛰는
# 문제가 있다. resync가 미완료 경기의 is_my를 새 팀으로 옮기는지 본다.
from competition.competition_common import resync_my_registration
from competition.champions_engine import CHAMPIONS_CFG
print()
print("── 대륙대항전(챔스) 등록 재동기화 검증 ──")
conn = database.get_conn()
ct = conn.execute(
    """SELECT t.* FROM cl_tournaments t
       WHERE t.status!='done'
         AND EXISTS(SELECT 1 FROM cl_matches m
                    WHERE m.tournament_id=t.id AND m.home_score=-1)
       LIMIT 1""").fetchone()
if not ct:
    print("  진행 중인 챔스 대회 없음 — 건너뜀")
else:
    ctid = ct["id"]
    # B(이적할 팀)는 반드시 "아직 안 치른 경기가 있는 팀"이어야 검증이 된다.
    unplayed_teams = []
    for r in conn.execute(
            "SELECT home_team_id, away_team_id FROM cl_matches "
            "WHERE tournament_id=? AND home_score=-1", (ctid,)).fetchall():
        unplayed_teams += [r[0], r[1]]
    # A(이적 전 팀)는 이미 치른 경기가 있는 팀 — 그 기록이 보존되는지 본다.
    played_teams = [r[0] for r in conn.execute(
        "SELECT home_team_id FROM cl_matches WHERE tournament_id=? AND home_score>=0",
        (ctid,)).fetchall()]
    b2 = unplayed_teams[0]
    a2 = next((t for t in played_teams if t != b2), None)
    if a2 is None:
        print("  조건에 맞는 팀 조합이 없음 — 건너뜀")
        raise SystemExit(0)
    conn.execute("UPDATE cl_tournaments SET my_in=1, my_team_id=? WHERE id=?", (a2, ctid))
    conn.execute("""UPDATE cl_matches
                    SET is_my=(CASE WHEN home_team_id=? OR away_team_id=? THEN 1 ELSE 0 END)
                    WHERE tournament_id=?""", (a2, a2, ctid))
    conn.execute("UPDATE my_player SET current_team_id=? WHERE id=1", (b2,))
    conn.commit()
    ge._invalidate_state_cache()
    before_b = conn.execute(
        """SELECT COUNT(*) FROM cl_matches WHERE tournament_id=? AND home_score=-1
           AND is_my=1 AND (home_team_id=? OR away_team_id=?)""", (ctid, b2, b2)).fetchone()[0]
    resync_my_registration(CHAMPIONS_CFG, ct["year"])
    conn = database.get_conn()
    after_b = conn.execute(
        """SELECT COUNT(*) FROM cl_matches WHERE tournament_id=? AND home_score=-1
           AND is_my=1 AND (home_team_id=? OR away_team_id=?)""", (ctid, b2, b2)).fetchone()[0]
    still_a = conn.execute(
        """SELECT COUNT(*) FROM cl_matches WHERE tournament_id=? AND home_score=-1
           AND is_my=1 AND (home_team_id=? OR away_team_id=?)""", (ctid, a2, a2)).fetchone()[0]
    played_a = conn.execute(
        """SELECT COUNT(*) FROM cl_matches WHERE tournament_id=? AND home_score>=0
           AND is_my=1 AND (home_team_id=? OR away_team_id=?)""", (ctid, a2, a2)).fetchone()[0]
    reg = conn.execute("SELECT my_team_id FROM cl_tournaments WHERE id=?", (ctid,)).fetchone()[0]
    print(f"  대회 id={ctid} A={a2} B={b2} | 등록팀 {a2} → {reg}")
    print(f"  새 팀(B) 미완료 내 경기: {before_b} → {after_b} | "
          f"옛 팀(A) 미완료 내 경기: {still_a} | 옛 팀(A) 이미 치른 내 경기(보존): {played_a}")
    total_b_unplayed = conn.execute(
        """SELECT COUNT(*) FROM cl_matches WHERE tournament_id=? AND home_score=-1
           AND (home_team_id=? OR away_team_id=?)""", (ctid, b2, b2)).fetchone()[0]
    ok2 = (reg == b2 and after_b == total_b_unplayed and total_b_unplayed > 0
           and still_a == 0 and played_a > 0)
    print(f"  (B의 전체 미완료 경기 {total_b_unplayed}개)")
    print("  PASS — 등록/미완료 경기가 새 팀으로 이관, 치른 경기는 보존"
          if ok2 else "  FAIL")