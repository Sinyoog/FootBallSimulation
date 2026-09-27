"""manager_migration_qa.py — 감독 시스템 ①단계 검증 (2026-09 신설)

신민용 확정 순서의 ①단계(테이블 + 기존 값 이관)만 검사한다. ②(전술엔진
연결)·③(부임/경질)은 아직 없다.

  A. 스키마       — managers / team_managers / 인덱스가 새 DB·기존 세이브
                    양쪽에서 같은 모양으로 생기는가
  B. 이관 정확도   — 모든 팀이 현재 감독을 정확히 1명 갖는가,
                    teams.tactic_tendency == managers.style_attack 인가,
                    내 팀 감독의 manager_type == my_player.manager_type 인가
  C. 멱등성       — init_db를 두 번 돌려도 감독이 늘지 않는가
  D. **마이그레이션이 난수열을 밀지 않는가** — ①의 절대 조건.
  H. 새 게임 경로 — season_state가 빈 상태에서도 안 죽는가, reset_game_data
     뒤에 감독이 새 월드 기준으로 다시 생기고 이전 판 감독이 안 남는가.
     (둘 다 실제로 터진 버그다 — 자세한 건 test_new_game_path 주석 참고)

[D의 의미가 ②에서 달라진 점] 원래 D는 "마이그레이션 전/후로 경기 결과가
완전히 같은가"였다. ②단계에서 전술엔진이 감독 성향을 읽기 시작했으므로,
감독이 생긴 DB는 **당연히** 다른 경기 결과를 낸다 — 그게 ②의 목적이다.
그래서 D는 지금도 유효한 쪽으로 좁혔다:

  D-1. _migrate_managers()가 전역 random 상태를 건드리지 않는가.
       이 게임은 "같은 세이브 + 같은 시드 → 같은 결과"를 전제로 QA를
       돌리는데(tools/verify_determinism.py), 마이그레이션이 전역 random을
       한 번이라도 소비하면 그 뒤 모든 경기의 난수열이 통째로 밀린다.
       _migrate_managers는 팀마다 random.Random 인스턴스를 따로 만들어
       쓰므로 전역 상태가 그대로여야 한다.
  D-2. 감독 성향 보정을 끈 상태(_style_edges → 0)에서는 마이그레이션
       전/후 경기 결과가 여전히 완전히 같은가. 즉 결과 차이가 **오직
       감독 성향 때문**이고 마이그레이션 자체의 부작용은 없는가.

사용법:
    python tools/manager_migration_qa.py --db <세이브.db> [--n 40]
"""
import _path  # noqa: F401
import argparse
import os
import random
import shutil
import sqlite3
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def _prep(src, work, fname):
    os.makedirs(work, exist_ok=True)
    dst = os.path.join(work, fname)
    shutil.copy(os.path.abspath(src), dst)
    hsrc = os.path.splitext(os.path.abspath(src))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(dst)[0] + ".history.db")
    return dst


def test_schema_and_transfer(db_path, work):
    import database as d
    problems = []

    db = _prep(db_path, work, "mig.db")
    d.USE_MEMORY_DB = False
    d.DB_PATH = db
    cwd = os.getcwd()
    os.chdir(work)
    d.reset_conn_pool()
    d.init_db()

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    # ── A. 스키마 ────────────────────────────────────────────────
    tbls = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("managers", "team_managers"):
        if t not in tbls:
            problems.append(f"A: {t} 테이블이 없음")
    idxs = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    for i in ("idx_team_managers_current", "idx_team_managers_manager"):
        if i not in idxs:
            problems.append(f"A: {i} 인덱스가 없음")
    mcols = {r[1] for r in conn.execute("PRAGMA table_info(managers)")}
    for col in ("name", "nationality", "birth_year", "retired", "from_player_id",
                "style_attack", "style_buildup", "style_press", "manager_type"):
        if col not in mcols:
            problems.append(f"A: managers.{col} 컬럼이 없음")
    print(f"  A. 스키마 — 테이블 2개 / 인덱스 2개 / managers 컬럼 {len(mcols)}개")

    # ── B. 이관 정확도 ───────────────────────────────────────────
    n_teams = conn.execute("SELECT COUNT(*) c FROM teams").fetchone()["c"]
    n_mgr = conn.execute("SELECT COUNT(*) c FROM managers").fetchone()["c"]
    n_cur = conn.execute(
        "SELECT COUNT(*) c FROM team_managers WHERE end_year IS NULL").fetchone()["c"]
    no_mgr = conn.execute(
        """SELECT COUNT(*) c FROM teams t WHERE NOT EXISTS
           (SELECT 1 FROM team_managers tm
            WHERE tm.team_id=t.id AND tm.end_year IS NULL)""").fetchone()["c"]
    dup = conn.execute(
        """SELECT COUNT(*) c FROM (SELECT team_id FROM team_managers
           WHERE end_year IS NULL GROUP BY team_id HAVING COUNT(*)>1)""").fetchone()["c"]
    if no_mgr:
        problems.append(f"B: 현재 감독이 없는 팀 {no_mgr}개")
    if dup:
        problems.append(f"B: 현재 감독이 2명 이상인 팀 {dup}개")
    print(f"  B. 팀 {n_teams} / 감독 {n_mgr} / 현재 재임 {n_cur} "
          f"(감독 없는 팀 {no_mgr}, 중복 {dup})")

    # tactic_tendency == style_attack
    mism = conn.execute(
        """SELECT COUNT(*) c FROM teams t
           JOIN team_managers tm ON tm.team_id=t.id AND tm.end_year IS NULL
           JOIN managers m ON m.id=tm.manager_id
           WHERE COALESCE(NULLIF(TRIM(t.tactic_tendency),''),'BALANCED')
                 <> m.style_attack""").fetchone()["c"]
    if mism:
        problems.append(f"B: tactic_tendency != style_attack 인 팀 {mism}개")
    print(f"  B. tactic_tendency → style_attack 이관 불일치 {mism}건")

    # 내 팀 감독 성향 승계
    mp = conn.execute(
        "SELECT current_team_id, manager_type FROM my_player LIMIT 1").fetchone()
    if mp and mp["current_team_id"]:
        row = conn.execute(
            """SELECT m.manager_type FROM team_managers tm
               JOIN managers m ON m.id=tm.manager_id
               WHERE tm.team_id=? AND tm.end_year IS NULL""",
            (mp["current_team_id"],)).fetchone()
        if row and (mp["manager_type"] or "") and row["manager_type"] != mp["manager_type"]:
            problems.append(
                f"B: 내 팀 감독 성향 불일치 — my_player={mp['manager_type']} "
                f"vs managers={row['manager_type']}")
        print(f"  B. 내 팀 감독 성향 승계: my_player={mp['manager_type']!r} "
              f"→ managers={row['manager_type'] if row else None!r}")
    else:
        print("  B. 내 팀 감독 승계 — 무소속이라 검사 제외")

    # 이름/국적이 비어있지 않은가
    blank = conn.execute(
        "SELECT COUNT(*) c FROM managers WHERE name IS NULL OR TRIM(name)=''").fetchone()["c"]
    if blank:
        problems.append(f"B: 이름이 빈 감독 {blank}명")
    ages = conn.execute(
        """SELECT MIN(birth_year) a, MAX(birth_year) b FROM managers""").fetchone()
    print(f"  B. 이름 빈 감독 {blank}명 / 출생연도 {ages['a']}~{ages['b']}")

    print("  B. 새 축 분포:")
    for col in ("style_buildup", "style_press"):
        rows = conn.execute(
            f"SELECT {col} v, COUNT(*) n FROM managers GROUP BY {col} ORDER BY n DESC")
        print(f"       {col}: " + ", ".join(f"{r['v']} {r['n']}" for r in rows))
    conn.close()

    # ── C. 멱등성 ────────────────────────────────────────────────
    d.reset_conn_pool()
    d.init_db()
    conn = sqlite3.connect(db)
    n_mgr2 = conn.execute("SELECT COUNT(*) FROM managers").fetchone()[0]
    conn.close()
    if n_mgr2 != n_mgr:
        problems.append(f"C: init_db 재실행으로 감독이 {n_mgr} → {n_mgr2}로 늘었음")
    print(f"  C. init_db 재실행 후 감독 수 {n_mgr} → {n_mgr2}")

    os.chdir(cwd)
    return problems


def test_result_invariance(db_path, work, n):
    """①의 절대 조건 — 마이그레이션이 난수열을 밀지 않는가.
    (D-1/D-2의 정확한 의미는 파일 상단 주석 참고)"""
    import database as d
    problems = []
    cwd = os.getcwd()

    # ── D-1. 전역 random 상태를 건드리지 않는가 ──────────────────
    # 이미 감독이 있는 DB에서는 대상이 0건이라 의미가 없으므로, 감독
    # 표만 비운 사본에서 실제로 11,000여 명을 만들면서 확인한다.
    db_rng = _prep(db_path, work, "rng.db")
    d.USE_MEMORY_DB = False
    d.DB_PATH = db_rng
    os.chdir(work)
    d.reset_conn_pool()
    d.init_db()
    _c = sqlite3.connect(db_rng)
    _c.execute("DELETE FROM team_managers")
    _c.execute("DELETE FROM managers")
    _c.commit()
    _c.close()
    d.reset_conn_pool()
    random.seed(4242)
    _before = random.getstate()
    n_made = d._migrate_managers()
    _after = random.getstate()
    if _before != _after:
        problems.append("D-1: _migrate_managers가 전역 random 상태를 바꿨음")
    print(f"  D-1. 감독 {n_made}명 생성 중 전역 random 상태 변화: "
          f"{'없음' if _before == _after else '있음 ← 실패'}")
    os.chdir(cwd)

    def _run(tag, do_migrate):
        db = _prep(db_path, work, f"inv_{tag}.db")
        d.USE_MEMORY_DB = False
        d.DB_PATH = db
        os.chdir(work)
        d.reset_conn_pool()
        if do_migrate:
            d.init_db()            # 감독 마이그레이션 포함
        else:
            # 감독 마이그레이션만 빼고 나머지 init_db는 동일하게 태운다 —
            # "감독 생성이 난수를 먹었는가"만 분리해서 보기 위해서다.
            _orig = d._migrate_managers
            d._migrate_managers = lambda: 0
            try:
                d.init_db()
            finally:
                d._migrate_managers = _orig

        from database import get_conn
        import match_sim.tactical_engine as _te
        from match_sim.tactical_engine import simulate_my_match
        import game_engine as ge
        # D-2 — 감독 성향 보정만 꺼서 "마이그레이션 자체의 부작용"만 본다.
        # ②에서 성향이 켜지면 결과가 달라지는 건 정상이므로, 그 항을 0으로
        # 눌러놓고 비교해야 순수한 부작용 유무를 판정할 수 있다.
        _te._style_edges = lambda a, b: (0.0, 0.0)
        conn = get_conn()
        teams = [r["id"] for r in conn.execute(
            """SELECT t.id FROM teams t
               WHERE (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) >= 18
               ORDER BY t.id LIMIT 12""")]
        c = conn.cursor()
        forms = {t: ge._team_formation(c, t) for t in teams}
        conn.close()

        out = []
        random.seed(13579)          # 전역 시드를 명시적으로 맞춘다
        for i in range(n):
            h, a = teams[i % len(teams)], teams[(i + 5) % len(teams)]
            if h == a:
                continue
            sim = simulate_my_match(h, a, forms[h], forms[a],
                                    home_adv=0.0, extra_time=True)
            out.append((
                sim["home_score"], sim["away_score"],
                sim.get("went_extra_time"),
                tuple(round(float(r.get("rating", 0)), 1)
                      for r in (sim.get("home_player_ratings") or []) if r),
                len(sim.get("home_subs") or []), len(sim.get("away_subs") or []),
            ))
        return out

    before = _run("plain", False)
    after = _run("mig", True)
    os.chdir(cwd)

    if len(before) != len(after):
        problems.append(f"D: 경기 수가 다름 {len(before)} vs {len(after)}")
    diff = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
    print(f"  D-2. 성향 보정 OFF · 같은 시드 {len(before)}경기 대조 — "
          f"다른 경기 {len(diff)}건")
    if diff:
        problems.append(f"D-2: 성향을 껐는데도 결과가 다름 — 마이그레이션 자체의 부작용 ({len(diff)}/{len(before)}경기)")
        for i in diff[:3]:
            print(f"       [{i}] 이전 {before[i][:3]} / 이후 {after[i][:3]}")
    else:
        sc = ", ".join(f"{b[0]}-{b[1]}" for b in before[:6])
        print(f"       스코어 앞 6경기: {sc} (양쪽 동일)")
    return problems



def test_new_game_path(db_path, work):
    """H. 새 게임 생성 경로.

    [실제로 터진 버그 두 개를 고정한다]
      H-1. season_state가 비어 있으면 NameError로 죽었다.
           reset_game_data는 월드(teams)는 남긴 채 season_state를 비우고
           init_db를 부른다 — 그 조합에서만 `else GAME_START_YEAR` 분기가
           처음 평가되는데, database.py는 그 상수를 모듈 스코프에 두지 않고
           함수 안에서 지역 import 한다. 평소 경로에선 season_state에 늘
           행이 있어서 이 분기가 한 번도 안 돌아 안 보이던 버그다.
      H-2. reset_game_data가 managers/team_managers를 안 비웠다.
           teams row는 DELETE가 아니라 UPDATE로 재사용되므로, 안 지우면
           이전 세계관의 감독이 새 게임의 같은 id 팀에 그대로 붙는다.
           게다가 reset은 init_db를 **먼저** 부르고 나중에 표를 비우므로,
           비우기만 하고 끝내면 이번엔 감독 0명으로 시작한다.
    """
    import database as d
    problems = []
    cwd = os.getcwd()

    db = _prep(db_path, work, "newgame.db")
    d.USE_MEMORY_DB = False
    d.DB_PATH = db
    os.chdir(work)
    d.reset_conn_pool()
    d.init_db()

    # ── H-1. season_state가 빈 상태 ──────────────────────────────
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM season_state")
    conn.execute("DELETE FROM team_managers")
    conn.execute("DELETE FROM managers")
    conn.commit()
    before_ids = [r[0] for r in conn.execute(
        "SELECT id FROM managers ORDER BY id LIMIT 1")]
    conn.close()
    d.reset_conn_pool()
    d._game_start_year_cache = None
    try:
        n = d._migrate_managers()
        print(f"  H-1. season_state 빈 상태에서 감독 {n}명 생성 — 예외 없음")
        if not n:
            problems.append("H-1: season_state가 비었을 때 감독이 하나도 안 생김")
    except Exception as e:
        problems.append(f"H-1: season_state가 빈 상태에서 예외 {type(e).__name__}: {e}")
        print(f"  H-1. 예외 {type(e).__name__}: {e} ← 실패")

    # ── H-2. reset_game_data 이후 ────────────────────────────────
    conn = sqlite3.connect(db)
    old_ids = {r[0] for r in conn.execute("SELECT id FROM managers")}
    conn.execute("UPDATE teams SET club_ambition='우승 도전'")   # 이전 판 잔재
    conn.commit()
    conn.close()
    d.reset_conn_pool()
    try:
        d.reset_game_data(skip_ai_regen=True)
    except Exception as e:
        problems.append(f"H-2: reset_game_data 예외 {type(e).__name__}: {e}")
        os.chdir(cwd)
        return problems

    conn = sqlite3.connect(db)
    n_mgr = conn.execute("SELECT COUNT(*) FROM managers").fetchone()[0]
    no_mgr = conn.execute(
        """SELECT COUNT(*) FROM teams t WHERE NOT EXISTS
           (SELECT 1 FROM team_managers tm WHERE tm.team_id=t.id AND tm.end_year IS NULL)"""
    ).fetchone()[0]
    left_amb = conn.execute(
        "SELECT COUNT(*) FROM teams WHERE club_ambition<>''").fetchone()[0]
    kept_old = conn.execute(
        "SELECT COUNT(*) FROM managers WHERE id IN (%s)"
        % (",".join(str(i) for i in list(old_ids)[:900]) or "0")).fetchone()[0]
    mism = conn.execute(
        """SELECT COUNT(*) FROM teams t
           JOIN team_managers tm ON tm.team_id=t.id AND tm.end_year IS NULL
           JOIN managers m ON m.id=tm.manager_id
           WHERE COALESCE(NULLIF(TRIM(t.tactic_tendency),''),'BALANCED') <> m.style_attack"""
    ).fetchone()[0]
    conn.close()
    os.chdir(cwd)

    print(f"  H-2. reset 후 감독 {n_mgr}명 / 감독 없는 팀 {no_mgr} / "
          f"이전 감독 잔존 {kept_old} / club_ambition 잔존 {left_amb} / "
          f"성향 불일치 {mism}")
    if not n_mgr:
        problems.append("H-2: reset 후 감독이 0명 — 새 게임이 감독 없이 시작된다")
    if no_mgr:
        problems.append(f"H-2: reset 후 감독 없는 팀 {no_mgr}개")
    if kept_old:
        problems.append(f"H-2: 이전 판 감독 {kept_old}명이 새 게임에 남아있음")
    if left_amb:
        problems.append(f"H-2: 이전 판 club_ambition이 {left_amb}팀에 남아있음")
    if mism:
        problems.append(f"H-2: tactic_tendency와 style_attack 불일치 {mism}팀")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--work", default=None)
    args = ap.parse_args()
    work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="mgr_qa_"))

    print("A~C. 스키마 / 이관 정확도 / 멱등성")
    problems = test_schema_and_transfer(args.db, work)
    print()
    print("D. 마이그레이션이 난수열을 밀지 않는가 (①단계의 절대 조건)")
    problems += test_result_invariance(args.db, work, args.n)
    print()
    print("H. 새 게임 생성 경로 (season_state 빈 상태 / reset_game_data)")
    problems += test_new_game_path(args.db, work)
    print()
    if problems:
        print("=== 실패 ===")
        for x in problems:
            print(f"  {x}")
        return 1
    print("=== 전체 통과: 감독 표가 생겼고, 마이그레이션 자체의 부작용은 없다 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())