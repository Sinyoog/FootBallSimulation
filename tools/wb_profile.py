# -*- coding: utf-8 -*-
"""wb_profile.py — 세계 축구 기록실 데이터 경로 측정(화면 없이).

사용: python tools/wb_profile.py <세이브의 game.db 경로> [--reps 3]

  [2026-10 추가] 게임 시작(init_db·seed)·종료(마지막 저장) 시간, 선수 검색 필터별, 이름 변경(저장 +
  화면 갱신이 다시 부르는 데이터 함수) 측정. 동작마다 "▶ 측정 중"을 먼저 찍고 15초마다 경과를 찍어
  어디서 오래 걸리는지 바로 보이게 했다. 무거운 동작(개인상 전체 등)은 맨 뒤 + 1회만 돈다.

  · 원본 세이브는 읽기만 한다 — game.db / game.history.db를 qa_runs/wb_profile_<시각>/ 로 복사한 뒤
    그 복사본으로만 돈다.
  · 동작(action) = 세계 기록실의 한 화면/클릭이 부르는 데이터 함수 묶음(ui/world_browser_window.py
    에서 그 핸들러가 부르는 wb.*/pr.* 호출을 그대로 옮겼다). Qt 배치·그리기 시간은 포함하지 않는다.
  · 동작마다 reps회 반복해 중앙값(초), SQL 실행 횟수, 첫 호출(캐시 없음) 시간을 기록하고,
    가장 무거운 동작부터 정렬해 출력한다. 함수별 누적 시간(어느 wb 함수가 범인인지)도 같이 낸다.
  · 결과: summary.txt(핵심 표), actions.csv, functions.csv — 원인 추적용 전체 표는 CSV로만.
"""
import _path  # noqa: F401
import csv
import datetime
import functools
import os
import shutil
import statistics
import sys
import time

REPS = 3
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if "--reps" in sys.argv:
    REPS = int(sys.argv[sys.argv.index("--reps") + 1])
    args = [a for a in args if a != str(REPS)]
if not args:
    print(__doc__)
    sys.exit(1)
SRC = os.path.abspath(args[0])
_base = SRC[:-3] if SRC.endswith(".db") else SRC
SRC_HIST = _base + ".history.db"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "qa_runs", "wb_profile_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(OUT, exist_ok=True)
DST = os.path.join(OUT, "game.db")
shutil.copy(SRC, DST)
if os.path.exists(SRC_HIST):
    shutil.copy(SRC_HIST, os.path.join(OUT, "game.history.db"))
else:
    print(f"[경고] {SRC_HIST} 없음 — 기록(hist) 데이터 없이 측정한다")
os.chdir(OUT)

import database
database.DB_PATH = DST
# [2026-10 추가] 시작 시간 분해 — init_db = load_from_disk(디스크 → 메모리 복사) + 나머지(스키마·마이그레이션)
_ld = {"s": 0.0}
_orig_ld = database.load_from_disk


def _timed_ld(*a, **k):
    _t0 = time.perf_counter()
    try:
        return _orig_ld(*a, **k)
    finally:
        _ld["s"] += time.perf_counter() - _t0


database.load_from_disk = _timed_ld
# 시작 중 SQL 문장별 시간(스키마·마이그레이션 1.8초가 어디서 나는지)
_sql_t = {}
_orig_retry = database._retry_sqlite_op


def _timed_retry(fn, *a, **k):
    _t0 = time.perf_counter()
    try:
        return _orig_retry(fn, *a, **k)
    finally:
        if a and isinstance(a[0], str):
            _key = " ".join(a[0].split())[:110]
            _v = _sql_t.setdefault(_key, [0, 0.0]); _v[0] += 1; _v[1] += time.perf_counter() - _t0


database._retry_sqlite_op = _timed_retry
_t = time.perf_counter()
database.init_db()
database._retry_sqlite_op = _orig_retry
_init_s = time.perf_counter() - _t
print(f"[로드] init_db {_init_s:.1f}s  (그중 디스크→메모리 복사 {_ld['s']:.1f}s, 스키마·마이그레이션 {_init_s - _ld['s']:.1f}s)")
try:
    _c0 = database.get_conn()
    _pc = _c0.execute("PRAGMA main.page_count").fetchone()[0]; _ps = _c0.execute("PRAGMA main.page_size").fetchone()[0]
    _fl = _c0.execute("PRAGMA main.freelist_count").fetchone()[0]
    print(f"[크기] game.db {os.path.getsize(DST) / 1e6:.0f}MB · 메인 {_pc * _ps / 1e6:.0f}MB(빈 페이지 {_fl * _ps / 1e6:.0f}MB)"
          + (f" · game.history.db {os.path.getsize(os.path.join(OUT, 'game.history.db')) / 1e6:.0f}MB"
             if os.path.exists(os.path.join(OUT, 'game.history.db')) else ""))
    try:
        _big = _c0.execute("SELECT name, SUM(pgsize) FROM dbstat WHERE schema='main' GROUP BY name ORDER BY 2 DESC LIMIT 10").fetchall()
        print("[크기] 메인 큰 표·인덱스:", ", ".join(f"{n} {b / 1e6:.0f}MB" for n, b in _big))
    except Exception:
        # dbstat이 없는 SQLite(윈도우 기본 파이썬 등) — 표별 행 수로 대신 본다
        _tabs = [r[0] for r in _c0.execute("SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        _cnt = []
        for _tn in _tabs:
            try:
                _cnt.append((_tn, _c0.execute(f'SELECT COUNT(*) FROM main."{_tn}"').fetchone()[0]))
            except Exception:
                pass
        _cnt.sort(key=lambda x: -x[1])
        print("[크기] 메인 행 수 상위:", ", ".join(f"{n} {c:,}" for n, c in _cnt[:12]))
except Exception as _e:
    print(f"[크기] 측정 실패: {_e}")
print(f"[로드] init_db 중 SQL {sum(v[0] for v in _sql_t.values())}개 · 느린 문장 상위 8:")
for _k, (_n, _sec) in sorted(_sql_t.items(), key=lambda kv: -kv[1][1])[:8]:
    print(f"    {_sec:6.2f}s ×{_n:<4} {_k}")
# [2026-10] 게임 시작 = init_db + seed_initial_data(skip_ai_regen=True) — main.py와 같은 순서
_t = time.perf_counter()
try:
    database.seed_initial_data(skip_ai_regen=True)
    print(f"[시작] seed_initial_data {time.perf_counter() - _t:.1f}s  (게임 시작 체감 = init_db + 이것 + 화면 생성)")
except Exception as _e:
    print(f"[시작] seed_initial_data 오류: {_e}")

import world_browser as wb
import power_ranking as pr

# ── SQL 카운터 ─────────────────────────────────────────────
_sql = {"n": 0}


def _trace(_stmt):
    _sql["n"] += 1


def _attach_trace():
    conn = database.get_conn()
    try:
        object.__getattribute__(conn, "_real").set_trace_callback(_trace)
    except Exception:
        try:
            conn.set_trace_callback(_trace)
        except Exception:
            pass


_attach_trace()

# ── 함수별 누적 시간 (wb.*, pr.* 공개 함수 감싸기 — 바깥 호출만 센다) ──
_fn_stats = {}       # name -> [calls, total_s]
_depth = {"d": 0}


def _wrap(mod, name):
    fn = getattr(mod, name)

    @functools.wraps(fn)
    def w(*a, **k):
        _depth["d"] += 1
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            dt = time.perf_counter() - t0
            _depth["d"] -= 1
            if _depth["d"] == 0:
                s = _fn_stats.setdefault(f"{mod.__name__}.{name}", [0, 0.0])
                s[0] += 1
                s[1] += dt
    setattr(mod, name, w)


for _m in (wb, pr):
    for _n in dir(_m):
        if _n.startswith(("get_", "search_", "list_", "has_", "find_", "ensure_")) and callable(getattr(_m, _n)):
            _wrap(_m, _n)

conn = database.get_conn()
q = lambda sql, *p: conn.execute(sql, p).fetchall()


def one(sql, *p):
    r = q(sql, *p)
    return r[0] if r else None


# ── 표본 고르기(데이터가 가장 많이 쌓인 대상 위주) ────────────────
cur_year = wb.get_current_game_year()
print(f"[세이브] 현재 연도 {cur_year}, 시작 연도 {database.get_game_start_year()}")
S = {}
try:
    S["league"] = one("""SELECT league_id FROM league_season_standings GROUP BY league_id
                         ORDER BY COUNT(*) DESC LIMIT 1""")[0]
except Exception:
    S["league"] = one("SELECT id FROM leagues WHERE tier=1 LIMIT 1")[0]
_lc = one("SELECT country_id FROM leagues WHERE id=?", S["league"])
S["country_id"] = _lc[0] if _lc else one("SELECT id FROM countries LIMIT 1")[0]
S["country"] = one("SELECT name FROM countries WHERE id=?", S["country_id"])[0]
try:
    S["team"] = one("""SELECT team_id FROM league_season_standings WHERE league_id=?
                       GROUP BY team_id ORDER BY COUNT(*) DESC, MIN(rank) LIMIT 1""", S["league"])[0]
except Exception:
    S["team"] = one("SELECT id FROM teams WHERE league_id=? LIMIT 1", S["league"])[0]
_wc = one("SELECT id, year FROM intl_tournaments WHERE kind LIKE '%world%' OR name LIKE '%월드컵%' "
          "ORDER BY year DESC LIMIT 1") or one("SELECT id, year FROM intl_tournaments ORDER BY year DESC LIMIT 1")
S["intl_tid"], S["intl_year"] = (_wc[0], _wc[1]) if _wc else (None, None)
_ct = one("SELECT id FROM cup_tournaments WHERE country_id=? ORDER BY year DESC LIMIT 1", S["country_id"])
S["cup_tid"] = _ct[0] if _ct else None
_cw = one("SELECT id FROM cwc_tournaments ORDER BY year DESC LIMIT 1")
S["cwc_tid"] = _cw[0] if _cw else None
# 선수: 경력 긴 은퇴 선수 + 현역 최고 OVR (복사 26명 = 국가대표 최근 대회 스쿼드)
_rp = one("SELECT id FROM ai_players_retired ORDER BY career_years DESC LIMIT 1")
S["retired_pid"] = _rp[0] if _rp else None
S["active_pid"] = one("SELECT id FROM ai_players ORDER BY ovr DESC LIMIT 1")[0]
S["squad"] = []
if S["intl_tid"]:
    _sq_c = one("SELECT country FROM intl_squad WHERE tournament_id=? GROUP BY country "
                "ORDER BY SUM(appearances) DESC LIMIT 1", S["intl_tid"])
    if _sq_c:
        S["squad_country"] = _sq_c[0]
        S["squad"] = [r[0] for r in q("SELECT player_id FROM intl_squad WHERE tournament_id=? AND country=?",
                                      S["intl_tid"], _sq_c[0])]
if not S["squad"]:
    S["squad"] = [r[0] for r in q("SELECT id FROM ai_players WHERE team_id=? LIMIT 26", S["team"])]
    S["squad_country"] = S["country"]
_mg = one("SELECT manager_id FROM team_managers GROUP BY manager_id ORDER BY COUNT(*) DESC LIMIT 1")
S["manager"] = _mg[0] if _mg else None
S["ry"] = pr.get_latest_ranking_year(conn) or cur_year
print("[표본]", {k: (v if not isinstance(v, list) else f"{len(v)}명") for k, v in S.items()})


def _player_detail(pid):
    """_show_player_detail 경로(정적 추적 결과)와 같은 데이터 함수들."""
    d = wb.get_ai_player_detail(pid)
    if not d:
        return
    tid = d.get("team_id") or 0
    wb.get_ai_player_career_history(pid, tid, retirement_year=d.get("retirement_year"),
                                    current_age=d.get("age"))
    wb.get_ai_player_team_timeline(pid, tid)
    wb.get_ai_player_ovr_checkpoints(pid)
    wb.get_ai_player_position_checkpoints(pid)
    wb.get_ai_player_role_checkpoints(pid)
    wb.get_player_intl_records(pid)
    wb.get_player_award_groups_by_year(pid)
    wb.get_player_awards_with_year_highlight(pid)
    wb.get_player_late_award_labels(pid)


A = []   # (이름, 함수)


_ONCE = set()   # 1회만 도는 무거운 동작


def act(name, once=False):
    def deco(f):
        A.append((name, f))
        if once:
            _ONCE.add(name)
        return f
    return deco


@act("국가 검색 탭 열기")
def _a1():
    wb.list_continents(); wb.list_grades()
    wb.get_country_trophy_kind_options()
    wb.search_countries()
    wb.get_all_countries_trophy_counts()
    wb.get_all_countries_placement_counts()


@act("국가 상세(클릭)")
def _a2():
    wb.get_country_trophy_summary(S["country"])
    wb.get_country_best_non_winning_results(S["country"])
    wb.get_country_tournament_results(S["country"])
    pr.get_country_power_history(conn, S["country"])


@act("국가 대회 카드 펼치기(스쿼드)")
def _a3():
    if not S["intl_tid"]:
        return
    c = S["squad_country"]
    wb.get_country_tournament_squad(S["intl_tid"], c)
    wb.get_intl_formation(S["intl_tid"], c)
    wb.get_national_manager_for_tournament(S["intl_tid"], c)
    wb.get_country_intl_match_log(S["intl_tid"], c)


@act("요약 복사(국가 대회 26명)")
def _a4():
    for pid in S["squad"]:
        _player_detail(pid)


@act("팀 상세(클릭)")
def _a5():
    wb.get_team_history(S["team"])
    pr.get_team_power_history(conn, S["team"])


@act("팀 연도 카드(라인업)")
def _a6():
    y = cur_year - 1
    wb.get_team_season_lineup(S["team"], y)
    wb.get_team_season_lineup(S["team"], y, half=1)
    wb.get_team_manager_for_year(S["team"], y)
    wb.get_team_season_finance(S["team"], y)


@act("리그 순위표(현재)")
def _a7():
    wb.get_league_standings_for_browser(S["league"])


@act("리그 역대 우승·최다 순위")
def _a8():
    wb.get_league_champions(S["league"])
    wb.get_league_rank_leaders(S["league"])


@act("컵대회 검색(국가)")
def _a9():
    wb.get_cup_history(S["country_id"])
    wb.get_cup_rank_leaders(S["country_id"])
    if S["cup_tid"]:
        wb.get_cup_tournament_detail(S["cup_tid"])
    wb.has_cup_data_bulk(); wb.has_domestic_sc_data_bulk(); wb.has_lower_cup_data_bulk()


@act("클럽 월드컵 탭")
def _a10():
    wb.get_cwc_history()
    wb.get_cwc_rank_leaders()
    if S["cwc_tid"]:
        wb.get_cwc_tournament_detail(S["cwc_tid"])


@act("A매치(월드컵) 탭")
def _a11():
    wb.get_wc_history()
    wb.get_wc_rank_leaders()
    if S["intl_tid"]:
        wb.get_intl_tournament_detail(S["intl_tid"])
        wb.get_wc_country_placements(S["intl_tid"])


@act("역대 개인상 탭(최근 연도)")
def _a12():
    ys = wb.get_individual_award_years()
    y = max(ys) if ys else cur_year
    for t in ("ballon_dor",):
        try:
            wb.get_season_individual_awards(y, t)
        except Exception:
            pass
    wb.get_world_poty_awards(y)
    wb.get_league_award_years()
    wb.get_league_awards(y)
    wb.get_cup_award_years()
    wb.get_cup_awards(y)


@act("파워랭킹 탭(최신 연도)")
def _a13():
    ry = S["ry"]
    pr.get_team_power_ranking_grouped(conn, ry)
    pr.get_country_power_ranking(conn, ry)
    pr.get_league_power_ranking(conn, ry)


@act("파워랭킹 이전 순위(팀/국가/리그)")
def _a14():
    pr.get_team_power_history(conn, S["team"])
    pr.get_country_power_history_with_continent(conn, S["country"])
    pr.get_league_power_history(conn, S["league"])


@act("선수 검색 탭 열기(기본 목록)")
def _a15():
    wb.list_continents(); wb.list_countries(); wb.list_grades()
    wb.search_ai_players()


@act("선수 검색(은퇴 포함 이름)")
def _a16():
    wb.search_ai_players(name_query="A", status="retired")


@act("선수 상세 — 경력 긴 은퇴 선수")
def _a17():
    if S["retired_pid"]:
        _player_detail(S["retired_pid"])


@act("선수 상세 — 현역 최고 OVR")
def _a18():
    _player_detail(S["active_pid"])


@act("감독 검색 탭 열기")
def _a19():
    wb.search_managers()


@act("감독 상세(경력 최장)")
def _a20():
    if S["manager"]:
        wb.get_manager_career(S["manager"])


@act("세계 인구 표시")
def _a21():
    wb.get_world_population_stats()


# ── [2026-10 추가] 선수 검색 필터별 — 화면(_refresh_player_list)과 같은 limit=300 ────────────
_PS = dict(limit=300)
_nat_cid = S["country_id"]
_pos = (one("SELECT position FROM ai_players GROUP BY position ORDER BY COUNT(*) DESC LIMIT 1") or ["ST"])[0]
_SEARCHES = [
    ("필터: 이름 부분 일치", dict(name_query="A1")),
    ("필터: 국적", dict(nat_country_id=_nat_cid)),
    ("필터: 대륙", dict(continent="유럽")),
    ("필터: 소속 국가(리그)", dict(country_id=_nat_cid)),
    ("필터: 리그", dict(league_id=S["league"])),
    ("필터: 팀(현재)", dict(team_id=S["team"])),
    ("필터: 팀 경력 포함", dict(team_id=S["team"], team_mode="career")),
    ("필터: 팀 경력 + 연도", dict(team_id=S["team"], team_mode="career", team_year=cur_year - 10)),
    ("필터: 포지션", dict(position=_pos)),
    ("필터: 나이 20~23", dict(min_age=20, max_age=23)),
    ("필터: OVR 85 이상", dict(min_ovr=85)),
    ("필터: 외국인만", dict(foreign_only=True, country_id=_nat_cid)),
    ("필터: 국가대표", dict(natteam=True, nat_country_id=_nat_cid)),
    ("필터: 이름 지정한 선수만", dict(custom_named_only=True)),
    ("필터: 은퇴 + 국적", dict(status="retired", nat_country_id=_nat_cid)),
    ("필터: 은퇴 + 팀 경력", dict(status="retired", team_id=S["team"], team_mode="career")),
    ("필터: 은퇴 + 경력 15년 이상", dict(status="retired", min_career_years=15)),
    ("필터: 은퇴 + 포지션 + 나이", dict(status="retired", position=_pos, min_age=35, max_age=40)),
]
for _nm, _kw in _SEARCHES:
    act("선수 검색 " + _nm)((lambda kw: (lambda: wb.search_ai_players(**dict(_PS, **kw))))(_kw))


# ── [2026-10 추가] 이름 변경 — 저장 + 화면 갱신이 다시 부르는 데이터 함수(apply_custom_name_live_to_browser:
# 선수 목록 줄 패치 → 상세 다시 그림 → 역대 개인상 표 다시 조회). 원래 이름으로 되돌려 놓는다.
_ren_pid = S["active_pid"]
_ren_old = database.get_ai_player_custom_name(_ren_pid) if hasattr(database, "get_ai_player_custom_name") else None


@act("이름 변경 — 저장만")
def _ren1():
    database.set_ai_player_custom_name(_ren_pid, "측정용이름")
    database.set_ai_player_custom_name(_ren_pid, _ren_old or "")


@act("이름 변경 — 선수 상세 다시 그림")
def _ren2():
    database.set_ai_player_custom_name(_ren_pid, "측정용이름")
    _player_detail(_ren_pid)
    database.set_ai_player_custom_name(_ren_pid, _ren_old or "")


@act("이름 변경 — 역대 개인상 표 다시 조회(올해, 세계상)", once=True)
def _ren3():
    ys = wb.get_individual_award_years()
    y = max(ys) if ys else cur_year
    wb.get_world_poty_awards(y)


# ── [2026-10 추가] 이름 일괄변경 26명 — 저장 26번 + 기록실 갱신(역대 개인상 표 재조회).
# 갱신 1회 = 개인상 탭이 열려 있을 때 다시 부르는 조회(세계상 + 리그전 국가 1개 + 컵 국가 1개).
# 예전 방식은 사람마다 갱신했다(26회) — 수정 전 코드의 조회 방식(수상자마다 선수 상세)을 그대로 흉내 낸
# 갱신 1회를 재서 ×26으로 예상치를 낸다(26번 실제로 돌리면 큰 세이브에서 수 분 걸린다).
_bulk_ids = [p for p in (S["squad"] if isinstance(S["squad"], list) else []) if p and p >= 0][:26]


def _ia_refresh_once():
    ys = wb.get_individual_award_years()
    y = max(ys) if ys else cur_year
    wb.get_world_poty_awards(y)
    cs = wb.get_league_award_countries(y)
    wb.get_league_awards(y, country=(S["country"] if S["country"] in cs else (cs[0] if cs else None)))
    cs2 = wb.get_cup_award_countries(y)
    wb.get_cup_awards(y, country=(S["country"] if S["country"] in cs2 else (cs2[0] if cs2 else None)))


def _bulk_set(names):
    for _p, _n in zip(_bulk_ids, names):
        database.set_ai_player_custom_name(_p, _n)


_bulk_old_names = [database.get_ai_player_custom_name(_p) or "" for _p in _bulk_ids]


@act(f"이름 일괄변경 {len(_bulk_ids)}명 — 새 방식(저장 {len(_bulk_ids)} + 갱신 1회)", once=True)
def _bulk_new():
    _bulk_set([f"일괄{i}" for i in range(len(_bulk_ids))])
    _ia_refresh_once()
    _bulk_set(_bulk_old_names)


@act("이름 일괄변경 — 예전 방식 갱신 1회분(예전 총시간 ≈ 이 값 × 인원)", once=True)
def _bulk_old_one():
    _orig = getattr(wb, "_award_player_briefs", None)
    if _orig is not None:
        wb._award_player_briefs = lambda _c, pids: {p: (wb.get_ai_player_detail(p) or {}) for p in pids}
    try:
        _ia_refresh_once()
    finally:
        if _orig is not None:
            wb._award_player_briefs = _orig


# ── 역대 개인상 — 하위 단계로 쪼갬(맨 뒤, 1회) ─────────────────────────
_ia_year = {"y": None}


def _iy():
    if _ia_year["y"] is None:
        ys = wb.get_individual_award_years()
        _ia_year["y"] = max(ys) if ys else cur_year
    return _ia_year["y"]


@act("개인상: 연도 목록", once=True)
def _ia1():
    wb.get_individual_award_years(); wb.get_league_award_years(); wb.get_cup_award_years()


@act("개인상: 세계상(발롱도르 등)", once=True)
def _ia2():
    wb.get_world_poty_awards(_iy())


@act("개인상: 리그전 — 국가 1개", once=True)
def _ia3():
    cs = wb.get_league_award_countries(_iy())
    wb.get_league_awards(_iy(), country=(S["country"] if S["country"] in cs else (cs[0] if cs else None)))


@act("개인상: 컵대회 — 국가 1개", once=True)
def _ia4():
    cs = wb.get_cup_award_countries(_iy())
    wb.get_cup_awards(_iy(), country=(S["country"] if S["country"] in cs else (cs[0] if cs else None)))


@act("개인상: 리그전 — 전체 국가(필터 없음)", once=True)
def _ia5():
    print(f"      (행 수 {len(wb.get_league_awards(_iy()))})", flush=True)


@act("개인상: 컵대회 — 전체 국가(필터 없음)", once=True)
def _ia6():
    print(f"      (행 수 {len(wb.get_cup_awards(_iy()))})", flush=True)


@act("게임 종료 — 기록 저장 대기(history_drain)", once=True)
def _quit0():
    database.history_drain()


@act("게임 종료 — 마지막 저장(flush_to_disk(final))", once=True)
def _quit():
    database.flush_to_disk(final=True)


@act("[실험] 종료 저장 — 임시파일 동기화 끄고 복사 + fsync 1번(결과물 동일, 측정 전용)", once=True)
def _quit_fast():
    import sqlite3 as _sq
    _tmp = DST + ".exp.tmp"
    if os.path.exists(_tmp):
        os.remove(_tmp)
    src = _sq.connect(database._MEM_URI, uri=True, timeout=30)
    dst = _sq.connect(_tmp, timeout=30)
    try:
        dst.execute("PRAGMA journal_mode=OFF"); dst.execute("PRAGMA synchronous=OFF")
        src.backup(dst)
    finally:
        dst.close(); src.close()
    with open(_tmp, "rb+") as _f:
        os.fsync(_f.fileno())
    print(f"      (임시파일 {os.path.getsize(_tmp) / 1e6:.0f}MB)", flush=True)
    os.remove(_tmp)


@act("새 게임 시작 — 기존 판 지우기 + 세계 생성(reset_game_data, 복사본에서만)", once=True)
def _newgame():
    # 맨 마지막(복사본 데이터를 지운다). 단계별 시간은 함수가 [RESET] 줄로 직접 찍는다.
    # [2026-10 추가] "표 비우기(DELETE)" 17초가 어느 표에서 나는지 — 문장별 시간 상위 12(메인/기록 DB 구분)
    _rt = {}

    def _timed(fn, *a, **k):
        _t0 = time.perf_counter()
        try:
            return _orig_retry(fn, *a, **k)
        finally:
            if a and isinstance(a[0], str) and a[0].lstrip().upper().startswith(("DELETE", "DROP", "VACUUM")):
                _key = " ".join(a[0].split())[:90]
                _v = _rt.setdefault(_key, [0, 0.0]); _v[0] += 1; _v[1] += time.perf_counter() - _t0

    database._retry_sqlite_op = _timed
    try:
        database.reset_game_data()
    finally:
        database._retry_sqlite_op = _orig_retry
    _hist = sum(v[1] for k, v in _rt.items() if "hist." in k)
    _all = sum(v[1] for v in _rt.values())
    print(f"      (지우기·압축 문장 합계 {_all:.1f}s — 기록 DB(hist.) {_hist:.1f}s / 메인 {_all - _hist:.1f}s)")
    for _k, (_n, _sec) in sorted(_rt.items(), key=lambda kv: -kv[1][1])[:12]:
        print(f"      {_sec:6.2f}s ×{_n:<3} {_k}")


# 순서: 검색·이름 변경(중요) → 기존 화면 → 개인상 하위 단계 → 종료 저장 → 새 게임
_old_awards = "역대 개인상 탭(최근 연도)"
_first = [a for a in A if a[0].startswith(("선수 검색 필터", "이름 변경", "이름 일괄변경"))]
_last_ia = [a for a in A if a[0].startswith("개인상:")]
_quit_a = [a for a in A if a[0].startswith(("게임 종료", "[실험] 종료"))]
_new_a = [a for a in A if a[0].startswith("새 게임 시작")]
_mid = [a for a in A if a not in _first and a not in _last_ia and a not in _quit_a and a not in _new_a
        and a[0] != _old_awards]
A[:] = _first + _mid + _last_ia + _quit_a + _new_a

import threading
_cur = {"name": None, "t0": 0.0}


def _heartbeat():
    while True:
        time.sleep(15)
        if _cur["name"]:
            print(f"    … {_cur['name']} 진행 중 {time.perf_counter() - _cur['t0']:.0f}s (SQL {_sql['n']})", flush=True)


threading.Thread(target=_heartbeat, daemon=True).start()

# ── 실행 ─────────────────────────────────────────────────
rows = []
t_all = time.perf_counter()
for name, fn in A:
    times, sqls, err = [], [], ""
    print(f"▶ {name}", flush=True)
    for i in range(1 if name in _ONCE else REPS):
        n0 = _sql["n"]
        t0 = time.perf_counter()
        _cur.update(name=name, t0=t0)
        try:
            fn()
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:120]
        times.append(time.perf_counter() - t0)
        sqls.append(_sql["n"] - n0)
        _cur["name"] = None
    rows.append({"action": name, "first_s": round(times[0], 3), "median_s": round(statistics.median(times), 3),
                 "sql_first": sqls[0], "sql_median": int(statistics.median(sqls)), "error": err})
    print(f"  {name:<34} 첫 {times[0]:7.3f}s  중앙 {statistics.median(times):7.3f}s  SQL {sqls[0]}"
          + (f"  [오류] {err}" if err else ""), flush=True)

with open(os.path.join(OUT, "actions.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
fns = sorted(_fn_stats.items(), key=lambda kv: -kv[1][1])
with open(os.path.join(OUT, "functions.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["function", "calls", "total_s", "avg_ms"])
    for k, (n, s) in fns:
        w.writerow([k, n, round(s, 3), round(s / n * 1000, 1)])

lines = [f"세이브: {SRC}", f"현재 연도 {cur_year} / 시작 {database.get_game_start_year()} / reps {REPS}",
         f"전체 {time.perf_counter() - t_all:.1f}s", "",
         "[동작 — 첫 호출 기준 느린 순]"]
for r in sorted(rows, key=lambda r: -r["first_s"]):
    flag = " ◀ 0.2s 초과" if r["first_s"] > 0.2 else ""
    lines.append(f"  {r['action']:<34} 첫 {r['first_s']:7.3f}s  중앙 {r['median_s']:7.3f}s  "
                 f"SQL {r['sql_first']:>6}{flag}" + (f"  [오류] {r['error']}" if r["error"] else ""))
lines += ["", "[함수 누적 시간 상위 15]"]
for k, (n, s) in fns[:15]:
    lines.append(f"  {k:<52} {n:>5}회  {s:7.3f}s  (평균 {s / n * 1000:.1f}ms)")
open(os.path.join(OUT, "summary.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
print(f"\n[결과 폴더] {OUT}")