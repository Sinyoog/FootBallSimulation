# -*- coding: utf-8 -*-
"""[2026-11-22 신설, 신민용 승인] 기록 화면 전용 통산 장부 (hist.record_totals).

세계 기록실 "기록" 화면(세계·리그·클럽 대항전)과 팀 검색 "클럽 기록"이 쓰는 선수 통산 골·도움을
범위별로 미리 더해 둔다. 예전엔 화면을 열 때마다 시즌 기록 표 전체(40시즌이면 2천만 행 이상)를
훑었다 — 이 표는 선수 id가 키의 앞이라 팀·연도로 찾을 수 없다. 이 장부는 기록 화면만 읽고 게임 로직은
절대 읽지 않는다(그래서 게임 쪽 조회의 실행 계획·순서·난수에 영향이 없다).

범위(scope, skey):
  'W', ''              세계 — 선수 통산(리그 + 모든 클럽 대회: 국내컵·클럽 대항전·슈퍼컵·클럽월드컵·하위컵·국내슈퍼컵)
  'C', '<팀 id>'        클럽 — 그 팀에서 뛴 모든 클럽 공식전
  'L', '<나라 id>:<부수>' 리그 — 그 시즌에 실제로 뛴 리그(부수) 경기만
  'X', '<대륙>:<대회>'   클럽 대항전 — 'cl' 챔피언스 / 'el' 유로파 / 'ecl' 컨퍼런스
  'S', '<연도>'          [2026-11-24] 단일 시즌 — 그 해 선수 시즌 기록(리그 + 모든 클럽 대회, 시즌 중 이적하면 두 팀 합산).
                         세계 기록실 "단일 시즌 최다 득점·도움"이 부분 색인으로 상위만 읽는다. 한 해에 20만 행 가까이
                         생겨서, 끝난 시즌(다음 해 리그 기록이 들어온 해)은 그 해 골 순위 또는 도움 순위가 SEASON_KEEP위
                         안(동점 포함)인 행만 남긴다 — 역대 상위 10위는 어느 해든 그 해 상위 10위 안에서만 나온다.
AI 선수만 담는다(내 선수 기록은 본 DB에 있어 화면이 바로 더한다).

귀속 규칙(시즌 기록을 저장하는 ai_lifecycle._snapshot_season_ratings와 같은 기준):
  · 리그: 시즌 중 이적생은 팀별 조각(hist.ai_player_season_team_stats), 나머지는 시즌을 마친 팀.
    리그 범위는 그 팀이 그 해 실제로 경기한 리그(경기 결과·보관 순위표).
  · 클럽 대회: 챔스류(cl)·국내슈퍼컵(dsc)은 상반기 대회라 시즌 중 이적생은 이적 전 팀(조각 half=1),
    나머지 대회는 시즌을 마친 팀.
  · 챔스류는 그 해 그 팀이 나간 대회({cl,el,ecl}_entries — 한 팀은 한 해에 셋 중 하나에만 나간다).

쓰는 방식:
  · 평소: 시즌 기록을 큐에 넣는 그 순간(43주 리그·대회, 52주 클럽월드컵) 이미 메모리에 있는 그 해 행을
    그대로 넘긴다(enqueue_league / enqueue_comps) — 표를 다시 훑지 않는다. 실제 합산은 기록 DB 쓰기 스레드가
    "뒤쪽 작업"으로 한다(database.history_enqueue_background — 게임 기록 작업이 없을 때만 짧은 단계씩, 게임의
    기록 읽기 동기화는 이것을 기다리지 않는다. 저장·종료는 끝까지 기다린다).
  · 중복 방지: 선수 묶음마다 합산과 묶음 표시를 한 트랜잭션에, 마지막에 (연도, 부분) 표시(hist.record_applied).
    이미 표시가 있으면 더하지 않는다 — 같은 시즌 기록이 두 번 들어와도 한 번만 더해진다. 도중에 멈춘 흔적
    (묶음 표시만 있음)이 불러올 때 보이면 다시 만든다.
  · 장부가 유효하다는 표시(year=0, part='valid')가 없으면 평소 합산은 건너뛰고, 불러올 때(ensure_valid)
    기록 표에서 통째로 다시 만든다(rebuild). 불러오기 복구(database._repair_future_hist_data)가 시즌
    기록을 지우면 장부도 무효로 보고 다시 만든다.
"""
import time

SCOPE_WORLD, SCOPE_CLUB, SCOPE_LEAGUE, SCOPE_CC, SCOPE_SEASON = "W", "C", "L", "X", "S"
CC_KINDS = ("cl", "el", "ecl")
FIRST_HALF_COMPS = ("cl", "dsc")   # ai_lifecycle._snapshot_season_ratings._FIRST_HALF_COMPS와 같다
VALID_PART = (0, "valid")
# [2026-11-24] 단일 시즌 범위('S')가 들어간 장부라는 표시 — 없으면(그 전 세이브) 불러올 때 한 번 다시 만든다.
SEASON_PART = (0, "season_v1")
SEASON_KEEP = 30   # 끝난 시즌마다 남기는 골·도움 순위(표준 경쟁 순위, 동점 포함) — 화면은 10위까지
# 단일 시즌 행(끝난 해는 정리한 뒤) — src = (y, p, g, a) 기여 목록. 인자 (마지막 리그 연도, SEASON_KEEP, SEASON_KEEP).
# 순위는 bg_job 정리(_SEASON_PRUNE_PICK)와 같다: 그 해 단일 시즌 행(골 또는 도움이 있는 선수) 안의 RANK().
_SEASON_KEEP_SQL = ("SELECT 'S', CAST(y AS TEXT), p, g, a FROM ("
                    "SELECT y, p, g, a, RANK() OVER (PARTITION BY y ORDER BY g DESC) AS rg, "
                    "RANK() OVER (PARTITION BY y ORDER BY a DESC) AS ra FROM ("
                    "SELECT y, p, SUM(g) AS g, SUM(a) AS a FROM ({src}) WHERE g > 0 OR a > 0 GROUP BY y, p)) "
                    "WHERE y >= ? OR rg <= ? OR ra <= ?")
_SEASON_PRUNE_PICK = ("INSERT INTO temp.rl_sdel(skey, p) SELECT skey, player_id FROM ("
                      "SELECT skey, player_id, RANK() OVER (PARTITION BY skey ORDER BY goals DESC) AS rg, "
                      "RANK() OVER (PARTITION BY skey ORDER BY assists DESC) AS ra "
                      "FROM record_totals WHERE scope='S' AND skey < ?) WHERE rg > ? AND ra > ? ORDER BY skey, player_id")


LAST_REBUILD = {}   # 진단용 — 마지막 재구축 {"seconds", "rows", "reason"}
TASK_STATS = {"tasks": 0, "applied": 0, "skipped": 0, "rows": 0, "seconds": 0.0, "max_seconds": 0.0}   # 진단용(쓰기 스레드, 시작~끝 경과)


def create_schema(c):
    """init_db의 hist 표 생성 구간에서 호출(c = 본 연결, hist ATTACH 상태)."""
    c.execute("""CREATE TABLE IF NOT EXISTS hist.record_totals(
        scope TEXT NOT NULL, skey TEXT NOT NULL, player_id INTEGER NOT NULL,
        goals INTEGER NOT NULL DEFAULT 0, assists INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(scope, skey, player_id)) WITHOUT ROWID""")
    # 세계 범위만 선수가 수십만 명이라 상위 N을 바로 뽑을 색인을 둔다(부분 색인 — 다른 범위는 수천 행이라 불필요)
    c.execute("CREATE INDEX IF NOT EXISTS hist.idx_record_totals_w_goals ON record_totals(goals) WHERE scope='W'")
    c.execute("CREATE INDEX IF NOT EXISTS hist.idx_record_totals_w_assists ON record_totals(assists) WHERE scope='W'")
    # [2026-11-24] 단일 시즌 범위도 (선수, 연도)가 수십만 개라 상위만 뽑을 부분 색인
    c.execute("CREATE INDEX IF NOT EXISTS hist.idx_record_totals_s_goals ON record_totals(goals) WHERE scope='S'")
    c.execute("CREATE INDEX IF NOT EXISTS hist.idx_record_totals_s_assists ON record_totals(assists) WHERE scope='S'")
    c.execute("""CREATE TABLE IF NOT EXISTS hist.record_applied(
        year INTEGER NOT NULL, part TEXT NOT NULL, PRIMARY KEY(year, part)) WITHOUT ROWID""")


# ─── 평소 합산(시즌 기록 저장 시점) ───────────────────────────────────────

def _league_skey_map(c):
    """{league_id: '나라id:부수'}"""
    return {lid: f"{cid}:{tier}" for lid, cid, tier in c.execute("SELECT id, country_id, tier FROM leagues").fetchall()}


def enqueue_league(c, year, inserts, team_pieces, played_league):
    """43주 리그 시즌 기록을 큐에 넣은 직후 호출. inserts = hist.ai_player_season_stats에 넣은 행
    (player_id, year, team_id, matches, goals, assists, …), team_pieces = hist.ai_player_season_team_stats에
    넣은 행(player_id, year, team_id, half, matches, goals, assists, …) 또는 빈 목록, played_league =
    {팀: 그 해 실제로 경기한 리그 id}(ai_lifecycle._played_league_ctx의 첫 반환값)."""
    from database import history_enqueue_background
    lmap = _league_skey_map(c)
    tkey = {}
    for t, lid in played_league.items():
        k = lmap.get(lid)
        if k is not None:
            tkey[t] = k
    payload = {"kind": "inc", "parts": [(int(year), "league")],
               "league": [(r[0], r[2], r[4] or 0, r[5] or 0) for r in inserts],
               "pieces": [(r[0], r[2], r[5] or 0, r[6] or 0) for r in team_pieces],
               "tkey": tkey}
    history_enqueue_background("record_ledger", (payload,))


def enqueue_comps(c, year, comp_rows):
    """대회별 시즌 기록(hist.ai_player_season_stats_by_comp)을 큐에 넣은 직후 호출.
    comp_rows = [(player_id, competition, 그 대회를 뛴 팀, goals, assists)]."""
    from database import history_enqueue_background
    if not comp_rows:
        return
    comps = sorted({r[1] for r in comp_rows})
    cckey = {}
    if "cl" in comps:
        cckey = _cc_team_keys_for_year(c, year)
    payload = {"kind": "inc", "parts": [(int(year), f"comp:{x}") for x in comps],
               "comps": [(r[0], r[1], r[2], r[3] or 0, r[4] or 0) for r in comp_rows], "cckey": cckey}
    history_enqueue_background("record_ledger", (payload,))


def _cc_team_keys_for_year(c, year):
    out = {}
    for comp in CC_KINDS:
        try:
            for t, cont in c.execute(
                    f"SELECT e.team_id, t.continent FROM {comp}_entries e JOIN {comp}_tournaments t "
                    f"ON e.tournament_id = t.id WHERE t.year = ?", (year,)).fetchall():
                out.setdefault(t, f"{cont}:{comp}")
        except Exception:
            continue
    return out


_STAGE_CHUNK = 50000   # 임시 표에 넣는 한 단계 행 수
_MERGE_CHUNK = 20000   # 장부에 더하는 한 단계 행 수 — 한 단계(트랜잭션)를 짧게 해 게임 기록 작업이 오래 기다리지 않게 한다

_STAGE_DDL = (
    "CREATE TEMP TABLE IF NOT EXISTS rl_lg(p INTEGER, t INTEGER, g INTEGER, a INTEGER)",
    "CREATE TEMP TABLE IF NOT EXISTS rl_pc(p INTEGER PRIMARY KEY, dummy INTEGER)",   # 이적생(조각이 있는 선수) 목록
    "CREATE TEMP TABLE IF NOT EXISTS rl_pcs(p INTEGER, t INTEGER, g INTEGER, a INTEGER)",
    "CREATE TEMP TABLE IF NOT EXISTS rl_tk(t INTEGER PRIMARY KEY, skey TEXT)",
    "CREATE TEMP TABLE IF NOT EXISTS rl_cp(p INTEGER, comp TEXT, t INTEGER, g INTEGER, a INTEGER)",
    "CREATE TEMP TABLE IF NOT EXISTS rl_cc(t INTEGER PRIMARY KEY, skey TEXT)",
    # 합산 결과 — 키 순서대로 넣어 rowid가 키 순서(장부에 나눠 더할 때 rowid 구간이 곧 연속한 키 구간)
    "CREATE TEMP TABLE IF NOT EXISTS rl_agg(scope TEXT, skey TEXT, p INTEGER, g INTEGER, a INTEGER)",
    "CREATE TEMP TABLE IF NOT EXISTS rl_sdel(skey TEXT, p INTEGER)",   # [2026-11-24] 끝난 시즌 단일 시즌 행 정리 목록
    "CREATE TEMP TABLE IF NOT EXISTS rl_sbf(y INTEGER, p INTEGER, g INTEGER, a INTEGER)",   # 단일 시즌 채우기 한 구간
    "CREATE TEMP TABLE IF NOT EXISTS rl_srk(y INTEGER, p INTEGER, g INTEGER, a INTEGER, src INTEGER, keep INTEGER)",
)
_STAGE_TABLES = ("rl_lg", "rl_pc", "rl_pcs", "rl_tk", "rl_cp", "rl_cc", "rl_agg", "rl_sdel", "rl_sbf", "rl_srk")

# 기여(범위, 키, 선수, 골, 도움) — 0/0 행은 넣지 않는다(기여 없는 선수는 장부 행이 없다).
_LG_PIECES = "SELECT p, t, g, a FROM rl_lg WHERE p NOT IN (SELECT p FROM rl_pc) UNION ALL SELECT p, t, g, a FROM rl_pcs"
_CONTRIB_SQL = {   # (원본, 범위) → 그 범위 합을 키 순서대로 rl_agg에 넣는 SELECT (0/0 행 제외 — 기여 없는 선수는 장부 행이 없다)
    ("league", SCOPE_WORLD): "SELECT 'W', '', p, SUM(g), SUM(a) FROM rl_lg WHERE g > 0 OR a > 0 GROUP BY p ORDER BY p",
    ("league", SCOPE_CLUB): (f"SELECT 'C', CAST(t AS TEXT), p, SUM(g), SUM(a) FROM ({_LG_PIECES}) "
                             "WHERE (g > 0 OR a > 0) AND t IS NOT NULL GROUP BY t, p ORDER BY CAST(t AS TEXT), p"),
    ("league", SCOPE_LEAGUE): (f"SELECT 'L', k.skey, y.p, SUM(y.g), SUM(y.a) FROM ({_LG_PIECES}) y "
                               "JOIN rl_tk k ON k.t = y.t WHERE y.g > 0 OR y.a > 0 GROUP BY k.skey, y.p ORDER BY k.skey, y.p"),
    ("comps", SCOPE_WORLD): "SELECT 'W', '', p, SUM(g), SUM(a) FROM rl_cp WHERE g > 0 OR a > 0 GROUP BY p ORDER BY p",
    ("comps", SCOPE_CLUB): ("SELECT 'C', CAST(t AS TEXT), p, SUM(g), SUM(a) FROM rl_cp WHERE (g > 0 OR a > 0) AND t IS NOT NULL "
                            "GROUP BY t, p ORDER BY CAST(t AS TEXT), p"),
    ("comps", SCOPE_CC): ("SELECT 'X', k.skey, x.p, SUM(x.g), SUM(x.a) FROM rl_cp x JOIN rl_cc k ON k.t = x.t "
                          "WHERE x.comp = 'cl' AND (x.g > 0 OR x.a > 0) GROUP BY k.skey, x.p ORDER BY k.skey, x.p"),
    # [2026-11-24] 단일 시즌 — 키는 그 해 연도(bg_job이 {year}를 채운다). 세계 범위와 같은 합, 연도별로.
    ("league", SCOPE_SEASON): "SELECT 'S', '{year}', p, SUM(g), SUM(a) FROM rl_lg WHERE g > 0 OR a > 0 GROUP BY p ORDER BY p",
    ("comps", SCOPE_SEASON): "SELECT 'S', '{year}', p, SUM(g), SUM(a) FROM rl_cp WHERE g > 0 OR a > 0 GROUP BY p ORDER BY p",
}
_SCOPES_BY_SRC = {"league": (SCOPE_WORLD, SCOPE_CLUB, SCOPE_LEAGUE, SCOPE_SEASON),
                  "comps": (SCOPE_WORLD, SCOPE_CLUB, SCOPE_CC, SCOPE_SEASON)}
_MERGE = ("INSERT INTO record_totals(scope, skey, player_id, goals, assists) "
          "SELECT scope, skey, p, g, a FROM temp.rl_agg WHERE rowid BETWEEN ? AND ? "
          "ON CONFLICT(scope, skey, player_id) DO UPDATE SET "
          "goals = goals + excluded.goals, assists = assists + excluded.assists")


def _applied(conn, year, part):
    return conn.execute("SELECT 1 FROM record_applied WHERE year=? AND part=?", (year, part)).fetchone() is not None


def bg_job(conn, payloads):
    """기록 DB 쓰기 스레드의 뒤쪽 작업(database.history_enqueue_background) — 생성기. yield 한 번 = 한 단계이고
    단계마다 쓰기 스레드가 커밋한다(conn = 기록 DB 파일을 직접 연 연결, 표 이름에 hist. 접두어 없음).
    ① 그 해 행을 임시 표(이 연결 전용 — 기록 DB 잠금 없음)에 넣고 ② 범위·키·선수별 합을 SQLite 안에서 범위마다
    한 번씩 구해 키 순서대로 임시 표에 두고 ③ 그 표를 rowid 구간(_MERGE_CHUNK행)씩 장부에 더하며 구간 표시('~부분#구간')를
    같은 트랜잭션에 남기고 ④ 마지막에 (연도, 부분) 표시를 남기고 구간 표시를 지운다. 같은 payload면 ②의 결과와
    구간 나눔이 늘 같다(키 순서) — 멈췄다 다시 넘겨도 표시가 있는 구간만 건너뛴다.
    중복 방지: (연도, 부분) 표시가 이미 있으면 통째로 건너뛴다.
    장부가 무효(유효 표시 없음)면 아무것도 안 한다 — 불러올 때 기록 표에서 통째로 다시 만든다(ensure_valid)."""
    for pl in payloads:
        if pl.get("kind") == "season_backfill":   # [2026-11-24] 예전 세이브의 단일 시즌 범위 채우기(ensure_valid가 넣음)
            yield from _season_backfill_steps(conn)
            continue
        if pl.get("kind") != "inc":
            continue
        t0 = time.perf_counter()
        TASK_STATS["tasks"] += 1
        if not _applied(conn, *VALID_PART):
            TASK_STATS["skipped"] += 1
            continue
        parts = [(int(y), str(pt)) for y, pt in pl["parts"]]
        if any(_applied(conn, y, pt) for y, pt in parts):
            TASK_STATS["skipped"] += 1
            continue
        year = parts[0][0]
        pkey = "+".join(pt for _y, pt in parts)
        src = "league" if "league" in pl else "comps"
        for ddl in _STAGE_DDL:
            conn.execute(ddl)
        for tb in _STAGE_TABLES:
            conn.execute(f"DELETE FROM temp.{tb}")
        if src == "league":
            rows = pl["league"]
            for i in range(0, len(rows), _STAGE_CHUNK):
                conn.executemany("INSERT INTO temp.rl_lg(p, t, g, a) VALUES (?, ?, ?, ?)", rows[i:i + _STAGE_CHUNK])
                yield
            pcs = pl.get("pieces") or []
            conn.executemany("INSERT INTO temp.rl_pcs(p, t, g, a) VALUES (?, ?, ?, ?)", pcs)
            conn.executemany("INSERT OR IGNORE INTO temp.rl_pc(p, dummy) VALUES (?, 0)", [(r[0],) for r in pcs])
            conn.executemany("INSERT OR IGNORE INTO temp.rl_tk(t, skey) VALUES (?, ?)", list((pl.get("tkey") or {}).items()))
        else:
            rows = pl["comps"]
            for i in range(0, len(rows), _STAGE_CHUNK):
                conn.executemany("INSERT INTO temp.rl_cp(p, comp, t, g, a) VALUES (?, ?, ?, ?, ?)", rows[i:i + _STAGE_CHUNK])
                yield
            conn.executemany("INSERT OR IGNORE INTO temp.rl_cc(t, skey) VALUES (?, ?)", list((pl.get("cckey") or {}).items()))
        yield
        for sc in _SCOPES_BY_SRC[src]:   # 범위마다 한 단계 — 합을 키 순서대로(같은 payload면 늘 같은 rowid 순서)
            _sql = _CONTRIB_SQL[(src, sc)]
            if sc == SCOPE_SEASON:
                _sql = _sql.replace("{year}", str(int(year)))
            conn.execute(f"INSERT INTO temp.rl_agg(scope, skey, p, g, a) {_sql}")
            yield
        n = conn.execute("SELECT MAX(rowid) FROM temp.rl_agg").fetchone()[0] or 0
        for lo in range(1, n + 1, _MERGE_CHUNK):
            mark = f"~{pkey}#{lo}"
            if _applied(conn, year, mark):
                continue
            conn.execute(_MERGE, (lo, lo + _MERGE_CHUNK - 1))
            conn.execute("INSERT INTO record_applied(year, part) VALUES (?, ?)", (year, mark))
            yield
        conn.executemany("INSERT OR IGNORE INTO record_applied(year, part) VALUES (?, ?)", parts)
        conn.execute("DELETE FROM record_applied WHERE year = ? AND part LIKE ?", (year, f"~{pkey}#%"))
        for tb in _STAGE_TABLES:
            conn.execute(f"DELETE FROM temp.{tb}")
        if src == "league":
            # [2026-11-24] 새 시즌 리그 기록이 들어왔다 = 그 전 해들은 클럽월드컵까지 끝났다 → 단일 시즌 행 정리.
            # 같은 결과를 몇 번 다시 해도 같다(도중에 멈추면 다음 시즌에 마저 한다).
            yield
            conn.execute(_SEASON_PRUNE_PICK, (str(year), SEASON_KEEP, SEASON_KEEP))
            nd = conn.execute("SELECT MAX(rowid) FROM temp.rl_sdel").fetchone()[0] or 0
            for lo in range(1, nd + 1, _MERGE_CHUNK):
                yield
                conn.execute("DELETE FROM record_totals WHERE scope='S' AND (skey, player_id) IN "
                             "(SELECT skey, p FROM temp.rl_sdel WHERE rowid BETWEEN ? AND ?)", (lo, lo + _MERGE_CHUNK - 1))
            conn.execute("DELETE FROM temp.rl_sdel")
        dt = time.perf_counter() - t0
        TASK_STATS["applied"] += 1
        TASK_STATS["rows"] += n
        TASK_STATS["seconds"] += dt
        TASK_STATS["max_seconds"] = max(TASK_STATS["max_seconds"], dt)
        yield


def _only_season_backfill_pending():
    """뒤쪽 작업 큐에 남은 것이 단일 시즌 채우기(season_backfill)뿐인지."""
    try:
        import database as _db
        jobs = list(_db._history_bg_jobs)
    except Exception:
        return False
    return all(j[0] == "record_ledger" and all((pl or {}).get("kind") == "season_backfill" for pl in j[1])
               for j in jobs)


def wait_idle(season=False):
    """화면 조회 직전 — 뒤쪽 작업(장부 합산)이 끝날 때까지 기다린다(시즌이 반만 더해진 장부를 보여주지 않게).
    보통은 이미 끝나 있어 즉시 반환한다. 기록 DB 쓰기 오류면 기다리지 않고 지금 장부를 보여준다.
    [2026-11-24] season=False(단일 시즌 범위를 안 읽는 조회 — 세계 통산·클럽·리그·클럽 대항전)는 남은 뒤쪽 작업이
    단일 시즌 채우기뿐이면 그것을 기다리지 않는다(그 작업은 단일 시즌 행만 쓴다). 예전 세이브를 처음 불러온 직후
    채우기가 도는 동안 구단 기록 등을 열면 채우기가 끝날 때까지(수 초~20초) 멈춰 있었다. 게임 기록 큐는 그대로 기다린다."""
    try:
        from database import history_drain
        if not season and _only_season_backfill_pending():
            history_drain(main_only=True)
            return
        history_drain()
    except Exception:
        pass


def season_ready(c):
    """[2026-11-24] 단일 시즌 범위가 다 채워졌는지(완료 표시) — 아니면 예전 세이브의 첫 채우기가 아직 도는 중."""
    try:
        return c.execute("SELECT 1 FROM hist.record_applied WHERE year=? AND part=?", SEASON_PART).fetchone() is not None
    except Exception:
        return True


# ─── 기록 표에서 통째로 다시 만들기(불러올 때 장부가 무효이면) ─────────────────

_LEAGUE_SRC = ("SELECT team_id t, year y, league_id l FROM league_season_standings "
               "UNION ALL SELECT home_team_id, year, league_id FROM match_results "
               "UNION ALL SELECT away_team_id, year, league_id FROM match_results "
               "UNION ALL SELECT home_team_id, year, league_id FROM match_results_archive "
               "UNION ALL SELECT away_team_id, year, league_id FROM match_results_archive")


def compute_from_hist(c):
    """기록 표(hist.ai_player_season_stats / _team_stats / _by_comp)와 본 DB(리그·대회 참가 기록)에서
    장부 전체를 계산한다 → (행 목록 [(scope, skey, player_id, goals, assists)] 정렬됨, (연도, 부분) 표시 목록).
    재구축과 검증(평소 합산 결과 = 이 결과)이 같이 쓴다. c = 본 연결(hist ATTACH)."""
    c.execute("DROP TABLE IF EXISTS temp._rl_ty")
    c.execute("CREATE TEMP TABLE _rl_ty(team_id INTEGER, year INTEGER, skey TEXT, PRIMARY KEY(team_id, year))")
    have_archive = bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='match_results_archive'").fetchone())
    src = _LEAGUE_SRC if have_archive else _LEAGUE_SRC.split(" UNION ALL SELECT home_team_id, year, league_id FROM match_results_archive")[0]
    c.execute(f"""INSERT INTO temp._rl_ty(team_id, year, skey)
                  SELECT x.t, x.y, l.country_id || ':' || l.tier
                  FROM (SELECT t, y, MIN(l) AS lid FROM ({src}) GROUP BY t, y) x JOIN leagues l ON l.id = x.lid""")
    c.execute("DROP TABLE IF EXISTS temp._rl_cc")
    c.execute("CREATE TEMP TABLE _rl_cc(team_id INTEGER, year INTEGER, skey TEXT, PRIMARY KEY(team_id, year))")
    for comp in CC_KINDS:
        try:
            c.execute(f"""INSERT OR IGNORE INTO temp._rl_cc(team_id, year, skey)
                          SELECT e.team_id, t.year, t.continent || ':{comp}'
                          FROM {comp}_entries e JOIN {comp}_tournaments t ON e.tournament_id = t.id""")
        except Exception:
            continue
    # 기여 목록(선수, 연도, 팀, 대회, 골, 도움) — 리그 조각 + 리그(이적 안 한 선수) + 대회별
    c.execute("DROP TABLE IF EXISTS temp._rl_contrib")
    c.execute("CREATE TEMP TABLE _rl_contrib(p INTEGER, y INTEGER, t INTEGER, comp TEXT, g INTEGER, a INTEGER)")
    c.execute("""INSERT INTO temp._rl_contrib
                 SELECT player_id, year, team_id, '', COALESCE(goals,0), COALESCE(assists,0)
                 FROM hist.ai_player_season_team_stats""")
    c.execute("""INSERT INTO temp._rl_contrib
                 SELECT s.player_id, s.year, s.team_id, '', COALESCE(s.goals,0), COALESCE(s.assists,0)
                 FROM hist.ai_player_season_stats s
                 WHERE NOT EXISTS (SELECT 1 FROM hist.ai_player_season_team_stats x
                                   WHERE x.year = s.year AND x.player_id = s.player_id)""")
    fh = ",".join(f"'{x}'" for x in FIRST_HALF_COMPS)
    c.execute(f"""INSERT INTO temp._rl_contrib
                  SELECT b.player_id, b.year,
                         CASE WHEN b.competition IN ({fh})
                              THEN COALESCE((SELECT f.team_id FROM hist.ai_player_season_team_stats f
                                             WHERE f.year = b.year AND f.player_id = b.player_id AND f.half = 1),
                                            s.team_id)
                              ELSE s.team_id END,
                         b.competition, COALESCE(b.goals,0), COALESCE(b.assists,0)
                  FROM hist.ai_player_season_stats_by_comp b
                  LEFT JOIN hist.ai_player_season_stats s ON s.player_id = b.player_id AND s.year = b.year""")
    rows = []
    # 세계: 리그는 선수 시즌 총기록(조각 합 = 총기록이므로 기여 목록으로 더해도 같다)
    rows += c.execute("""SELECT 'W', '', p, SUM(g), SUM(a) FROM temp._rl_contrib
                         WHERE g > 0 OR a > 0 GROUP BY p""").fetchall()
    rows += c.execute("""SELECT 'C', CAST(t AS TEXT), p, SUM(g), SUM(a) FROM temp._rl_contrib
                         WHERE (g > 0 OR a > 0) AND t IS NOT NULL GROUP BY t, p""").fetchall()
    rows += c.execute("""SELECT 'L', ty.skey, x.p, SUM(x.g), SUM(x.a) FROM temp._rl_contrib x
                         JOIN temp._rl_ty ty ON ty.team_id = x.t AND ty.year = x.y
                         WHERE x.comp = '' AND (x.g > 0 OR x.a > 0) GROUP BY ty.skey, x.p""").fetchall()
    rows += c.execute("""SELECT 'X', cc.skey, x.p, SUM(x.g), SUM(x.a) FROM temp._rl_contrib x
                         JOIN temp._rl_cc cc ON cc.team_id = x.t AND cc.year = x.y
                         WHERE x.comp = 'cl' AND (x.g > 0 OR x.a > 0) GROUP BY cc.skey, x.p""").fetchall()
    _ly = c.execute("SELECT MAX(y) FROM temp._rl_contrib WHERE comp = ''").fetchone()[0]
    rows += c.execute(_SEASON_KEEP_SQL.format(src="SELECT y, p, g, a FROM temp._rl_contrib"),
                      (_ly if _ly is not None else 10 ** 9, SEASON_KEEP, SEASON_KEEP)).fetchall()
    parts = [(y, "league") for (y,) in c.execute(
        "SELECT DISTINCT y FROM temp._rl_contrib WHERE comp = ''").fetchall()]
    parts += [(y, f"comp:{cp}") for y, cp in c.execute(
        "SELECT DISTINCT y, comp FROM temp._rl_contrib WHERE comp <> ''").fetchall()]
    for t in ("_rl_ty", "_rl_cc", "_rl_contrib"):
        c.execute(f"DROP TABLE IF EXISTS temp.{t}")
    # 임시 표 쓰기가 연 트랜잭션을 닫는다 — 열어 두면 이 연결의 기록 DB 읽기 시점이 고정돼(쓰기 스레드가 그 뒤에
    # 커밋한 것이 안 보이고) 이 연결로 기록 DB에 쓸 때 잠금 오류가 난다.
    c.commit()
    rows = [tuple(r) for r in rows]
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return rows, sorted(set(parts))


def rebuild(c, reason=""):
    """장부를 기록 표에서 통째로 다시 만든다(본 연결에서 직접 — 불러오는 중이라 게임이 멈춰 있을 때만).
    쓰기 스레드 큐를 먼저 비운다(그 사이 들어온 기록까지 기록 표에 반영된 뒤에 계산)."""
    from database import history_drain
    t0 = time.perf_counter()
    history_drain()
    if getattr(c, "in_transaction", False):
        c.commit()
    rows, parts = compute_from_hist(c)
    c.execute("DELETE FROM hist.record_totals")
    c.execute("DELETE FROM hist.record_applied")
    c.executemany("INSERT INTO hist.record_totals(scope, skey, player_id, goals, assists) VALUES (?,?,?,?,?)", rows)
    c.executemany("INSERT INTO hist.record_applied(year, part) VALUES (?,?)", parts + [VALID_PART, SEASON_PART])
    c.commit()
    LAST_REBUILD.clear()
    LAST_REBUILD.update({"seconds": round(time.perf_counter() - t0, 2), "rows": len(rows), "reason": reason})
    print(f"[RECORD-LEDGER] 장부 재구축({reason}) {len(rows)}행 {LAST_REBUILD['seconds']}s")


_DIRTY = {"flag": False}


def mark_dirty():
    """불러오기 복구가 시즌 기록 표(장부의 원본)를 지웠을 때 database._repair_future_hist_data가 부른다."""
    _DIRTY["flag"] = True


def ensure_valid(c):
    """init_db 끝(불러오기 직후)에 호출 — 장부가 무효(유효 표시 없음·복구로 원본이 지워짐)면 다시 만든다.
    평소엔 유효 표시 조회 한 번뿐."""
    dirty = _DIRTY["flag"]
    _DIRTY["flag"] = False
    try:
        ok = c.execute("SELECT 1 FROM hist.record_applied WHERE year=? AND part=?", VALID_PART).fetchone()
        partial = c.execute("SELECT 1 FROM hist.record_applied WHERE part LIKE '~%' LIMIT 1").fetchone()
        has_s = c.execute("SELECT 1 FROM hist.record_applied WHERE year=? AND part=?", SEASON_PART).fetchone()
    except Exception:
        return
    if ok is not None and not dirty and partial is None:
        if has_s is None:   # [2026-11-24] 예전 세이브 — 단일 시즌 범위를 뒤쪽 작업으로 채운다(불러오기를 막지 않음)
            from database import history_enqueue_background
            history_enqueue_background("record_ledger", ({"kind": "season_backfill"},))
        return
    rebuild(c, "불러오기 복구로 시즌 기록이 지워짐" if dirty else
            ("합산 도중 멈춘 시즌이 있음" if partial is not None else "장부 없음"))


SEASON_BACKFILL_CHUNK = 5000    # 첫 단계 선수 id 폭(기록 표 기본 키 앞부분 — 범위로 바로 찾는다)
SEASON_BACKFILL_STEP_S = 0.12   # 한 단계 목표 시간 — 폭을 걸린 시간에 맞춰 늘리고 줄인다(결과는 폭과 무관하게 같다)


def _season_backfill_steps(conn):
    """[2026-11-24] 단일 시즌 범위가 생기기 전 세이브 — 단일 시즌 행을 기록 표에서 채운다. 쓰기 스레드의 뒤쪽 작업
    (bg_job, conn = 기록 DB 직접 연결)으로 선수 id 구간마다 한 단계씩 한다 — 불러오기를 막지 않고, 게임 기록 작업이
    있으면 그쪽이 먼저다. 값 = 리그 시즌 기록 + 대회별 기록을 선수·연도로 더한 것(조각 합 = 시즌 총기록이라 재구축·
    평소 합산과 같다).
    끝난 해(마지막 리그 연도보다 앞선 해)는 구간마다 정리한다(_SEASON_PRUNE_PICK). 일부 선수만 본 상태에서 순위가
    SEASON_KEEP 밖이면 선수가 더 늘어도 밖이라(순위는 늘기만 한다), 구간마다 정리해도 한 번에 정리한 결과와 같다.
    완료 표시(SEASON_PART)는 맨 끝에 남긴다 — 도중에 꺼지면 표시가 없어 다음 불러오기 때 처음부터 다시 한다(남은
    단일 시즌 행은 첫 단계에서 지운다). 그 사이 화면이 단일 시즌 표를 열면 wait_idle이 이 작업이 끝나길 기다린다."""
    if _applied(conn, *SEASON_PART) or not _applied(conn, *VALID_PART):
        return
    t0 = time.perf_counter()
    for ddl in _STAGE_DDL:
        conn.execute(ddl)
    conn.execute("DELETE FROM temp.rl_sdel")
    conn.execute("DELETE FROM record_totals WHERE scope='S'")
    yield
    ly = conn.execute("SELECT MAX(year) FROM record_applied WHERE part='league'").fetchone()[0]
    ly_i = int(ly) if ly is not None else 10 ** 9
    ly = str(ly_i)
    # MIN·MAX를 한 문장에 같이 쓰면 색인 끝값 조회가 안 되고 표 전체를 훑는다 — 하나씩
    ids = [conn.execute(f"SELECT {fn}(player_id) FROM {tb}").fetchone()[0]
           for tb in ("ai_player_season_stats", "ai_player_season_stats_by_comp") for fn in ("MIN", "MAX")]
    ids = [r for r in ids if r is not None]
    if ids:
        lo_all, hi_all = min(ids), max(ids)
        lo, width = lo_all, SEASON_BACKFILL_CHUNK
        while lo <= hi_all:
            hi = lo + width - 1
            _ts = time.perf_counter()
            # ① 이 구간 선수의 (연도, 선수) 시즌 합 → 임시 표(장부 색인을 건드리지 않음)
            conn.execute("DELETE FROM temp.rl_sbf")
            conn.execute(
                "INSERT INTO temp.rl_sbf(y, p, g, a) SELECT y, p, SUM(g), SUM(a) FROM ("
                "SELECT year AS y, player_id AS p, COALESCE(goals,0) AS g, COALESCE(assists,0) AS a "
                "FROM ai_player_season_stats WHERE player_id BETWEEN ? AND ? UNION ALL "
                "SELECT year, player_id, COALESCE(goals,0), COALESCE(assists,0) FROM ai_player_season_stats_by_comp "
                "WHERE player_id BETWEEN ? AND ?) WHERE g > 0 OR a > 0 GROUP BY y, p", (lo, hi, lo, hi))
            # ② 마지막 리그 연도 이후(정리 안 하는 해)는 그대로 넣는다
            conn.execute("INSERT INTO record_totals(scope, skey, player_id, goals, assists) "
                         "SELECT 'S', CAST(y AS TEXT), p, g, a FROM temp.rl_sbf WHERE y >= ?", (ly_i,))
            # ③ 끝난 해: 이미 남긴 행 + 이 구간 행을 합쳐 순위를 매기고, 순위 안 새 행만 넣고 순위 밖이 된 기존 행은 지운다
            conn.execute("DELETE FROM temp.rl_srk")
            conn.execute(
                "INSERT INTO temp.rl_srk(y, p, g, a, src, keep) SELECT y, p, g, a, src, "
                "(RANK() OVER (PARTITION BY y ORDER BY g DESC) <= ? OR RANK() OVER (PARTITION BY y ORDER BY a DESC) <= ?) "
                "FROM (SELECT CAST(skey AS INTEGER) AS y, player_id AS p, goals AS g, assists AS a, 0 AS src "
                "FROM record_totals WHERE scope='S' AND skey < ? "
                "UNION ALL SELECT y, p, g, a, 1 FROM temp.rl_sbf WHERE y < ?)",
                (SEASON_KEEP, SEASON_KEEP, ly, ly_i))
            conn.execute("INSERT INTO record_totals(scope, skey, player_id, goals, assists) "
                         "SELECT 'S', CAST(y AS TEXT), p, g, a FROM temp.rl_srk WHERE src = 1 AND keep")
            conn.execute("DELETE FROM record_totals WHERE scope='S' AND (skey, player_id) IN "
                         "(SELECT CAST(y AS TEXT), p FROM temp.rl_srk WHERE src = 0 AND NOT keep)")
            _dt = time.perf_counter() - _ts
            width = int(min(20000, max(500, width * min(1.25, max(0.5, SEASON_BACKFILL_STEP_S / max(_dt, 1e-3))))))
            lo = hi + 1
            yield
        conn.execute("DELETE FROM temp.rl_sbf")
        conn.execute("DELETE FROM temp.rl_srk")
    conn.execute("INSERT OR IGNORE INTO record_applied(year, part) VALUES (?, ?)", SEASON_PART)
    LAST_REBUILD["season_backfill_s"] = round(time.perf_counter() - t0, 2)
    yield


def reset_empty(c):
    """새 게임(reset_game_data) — 장부를 비우고 빈 장부를 유효로 표시한다(새 판 첫 시즌부터 평소 합산)."""
    c.execute("DELETE FROM hist.record_totals")
    c.execute("DELETE FROM hist.record_applied")
    c.execute("INSERT OR IGNORE INTO hist.record_applied(year, part) VALUES (?,?)", VALID_PART)
    c.execute("INSERT OR IGNORE INTO hist.record_applied(year, part) VALUES (?,?)", SEASON_PART)
    _DIRTY["flag"] = False


# ─── 화면용 조회 ─────────────────────────────────────────────────────────

def scope_totals(c, scope, skey):
    """그 범위 AI 선수 통산 ({선수: 골}, {선수: 도움}) — 범위 키로 바로 찾는다(세계 범위는 쓰지 말 것: top_world)."""
    wait_idle()
    g, a = {}, {}
    for p, gg, aa in c.execute("SELECT player_id, goals, assists FROM hist.record_totals WHERE scope=? AND skey=?",
                               (scope, str(skey))).fetchall():
        if gg:
            g[p] = gg
        if aa:
            a[p] = aa
    return g, a


def top_world(c, col, k):
    """세계 범위 골('goals') 또는 도움('assists') 상위 k명 [(선수, 값)] — 부분 색인으로 바로 뽑는다."""
    wait_idle()
    idx = "idx_record_totals_w_goals" if col == "goals" else "idx_record_totals_w_assists"
    return [tuple(r) for r in c.execute(
        f"SELECT player_id, {col} FROM hist.record_totals INDEXED BY {idx} WHERE scope='W' AND {col} > 0 "
        f"ORDER BY {col} DESC LIMIT ?", (int(k),)).fetchall()]


def world_values(c, player_ids):
    """세계 범위 {선수: (골, 도움)} — 기본 키로 바로 찾는다."""
    out = {}
    ids = sorted(set(player_ids))
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        ph = ",".join("?" * len(part))
        for p, g, a in c.execute(f"SELECT player_id, goals, assists FROM hist.record_totals "
                                 f"WHERE scope='W' AND skey='' AND player_id IN ({ph})", part).fetchall():
            out[p] = (g, a)
    return out


def top_season(c, col, k=None, min_value=None):
    """[2026-11-24] 단일 시즌 범위 골('goals') 또는 도움('assists') 상위 [(선수, 연도, 값)] — 부분 색인으로 바로 뽑는다.
    k개까지, 또는 min_value 이상 전부(둘 다 주면 둘 다 만족)."""
    wait_idle(season=True)
    idx = "idx_record_totals_s_goals" if col == "goals" else "idx_record_totals_s_assists"
    sql = (f"SELECT player_id, CAST(skey AS INTEGER), {col} FROM hist.record_totals INDEXED BY {idx} "
           f"WHERE scope='S' AND {col} >= ? ORDER BY {col} DESC")
    args = [max(1, int(min_value or 1))]
    if k is not None:
        sql += " LIMIT ?"
        args.append(int(k))
    return [tuple(r) for r in c.execute(sql, args).fetchall()]