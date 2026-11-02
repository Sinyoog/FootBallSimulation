"""[2026-10-27] 신인 생성 나이(성인 구단 첫 진입) + 국가대표 은퇴 단위 테스트.

사용: python tools/test_newbie_age_nt_retire.py   (DB 파일을 건드리지 않는다 — 메모리 DB만 쓴다)
"""
import _path  # noqa: F401
import random
import sqlite3
import sys
from collections import Counter

import constants as C
import ai_lifecycle as AL
import database as DB

FAILS = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ── [1] 표 ───────────────────────────────────────────────────────────
print("[1] 나이 표·은퇴 확률표")
check("신인 나이 표 합 100", sum(C.NEWBIE_ADULT_AGE_WEIGHTS.values()) == 100)
check("신인 나이 범위 16~24", min(C.NEWBIE_ADULT_AGE_WEIGHTS) == 16 and max(C.NEWBIE_ADULT_AGE_WEIGHTS) == 24)
check("외국인 범위 18~23", C.NEWBIE_FOREIGN_AGE_RANGE == (18, 23))
P = C.nt_retire_prob
cases = [((27, 5), 0.0), ((28, 0), 0.0), ((28, 1), 0.0), ((28, 2), 0.05), ((30, 10), 0.45),
         ((31, 2), 0.10), ((33, 1), 0.05), ((36, 6), 0.55), ((39, 10), 1.0), ((40, 8), 1.0),
         ((42, 1), 0.70), ((42, 5), 1.0), ((45, 1), 0.70), ((45, 12), 1.0), ((35, 15), 0.75)]
for (a, d), exp in cases:
    check(f"확률({a}세, {d}년) = {exp}", abs(P(a, d) - exp) < 1e-9, f"실제 {P(a, d)}")

# ── [2] 나이 추첨 ────────────────────────────────────────────────────
print("[2] _roll_newbie_age — 성인 구단 / 산하팀")
_orig_aff, _orig_on = AL._youth_aff, AL._youth_on
AL._youth_aff = lambda c=None: {999: 1}
AL._youth_on = lambda: True
random.seed(1)
N = 200000
cnt = Counter(AL._roll_newbie_age(5) for _ in range(N))
worst = max(abs(cnt[a] / N * 100 - w) for a, w in C.NEWBIE_ADULT_AGE_WEIGHTS.items())
check("성인 구단 나이 분포 = 표(±0.5%p)", worst < 0.5 and set(cnt) <= set(C.NEWBIE_ADULT_AGE_WEIGHTS),
      f"최대 차 {worst:.2f}%p")
check("성인 구단 25세 이상 0", all(a <= 24 for a in cnt))
ycnt = Counter(AL._roll_newbie_age(999) for _ in range(50000))
check("산하팀은 예전 17~21세 균등", set(ycnt) == set(range(17, 22))
      and max(abs(v / 50000 - 0.2) for v in ycnt.values()) < 0.01, str(sorted(ycnt.items())))

# ── [3] 안 2: 국적 → 나이 → 생성 (나이 재조정 없음) ─────────────────
print("[3] 국적별 나이 규칙 · 생성 순서 · 하한 · 99 신인")
_sub = {a: w for a, w in C.NEWBIE_ADULT_AGE_WEIGHTS.items() if 18 <= a <= 23}
_tot = sum(_sub.values())
fc = Counter(AL._roll_newbie_age(5, "브라질", "대한민국") for _ in range(100000))
worst = max(abs(fc[a] / 100000 - w / _tot) for a, w in _sub.items())
check("외국인 신인 나이 = 표를 18~23세로 정규화(±0.5%p)", worst < 0.005 and set(fc) <= set(_sub),
      f"최대 차 {worst * 100:.2f}%p")
dc = Counter(AL._roll_newbie_age(5, "대한민국", "대한민국") for _ in range(100000))
worst = max(abs(dc[a] / 100000 * 100 - w) for a, w in C.NEWBIE_ADULT_AGE_WEIGHTS.items())
check("자국 신인 나이 = 16~24세 표(±0.5%p)", worst < 0.5, f"최대 차 {worst:.2f}%p")
yc = Counter(AL._roll_newbie_age(999, "브라질", "대한민국") for _ in range(20000))
check("산하팀은 국적과 무관하게 17~21세", set(yc) == set(range(17, 22)))
check("_foreign_newbie_age(사후 나이 조정) 제거됨", not hasattr(AL, "_foreign_newbie_age"))
import inspect
for fn in ("_retire_and_replace", "_gen_topup_rows", "_sq_gen_swap_row", "apply_squad_turnover_after_movement"):
    src = inspect.getsource(getattr(AL, fn))
    i_nat, i_age = src.find("_pick_nationality("), src.find("_roll_newbie_age(")
    i_st = src.find("_gen_stats(", i_age)
    check(f"{fn}: 국적 → 나이 → 스탯 순서", 0 <= i_nat < i_age < i_st, f"{i_nat}/{i_age}/{i_st}")


def gen_like_sites(target, age, pos, lhm=None, phm=None):
    """4곳 생성 경로의 OVR 계산(나이 비율 → 스탯 → 리그·명문 하한)을 그대로 재현."""
    sc = AL._youth_target_scale(target, age)
    st = AL._gen_stats(pos, sc)
    ov = AL.calc_ovr(pos, st)
    if lhm is not None:
        st, ov = AL._lift_stats_to_ovr(pos, st, ov, max(lhm, (phm or 0) * (sc / target)))
    return st, ov


# 하한 미적용: 외국인(구간 밖, 하한 보정 꺼짐)은 _apply_foreign_adult가 아무것도 바꾸지 않는다 → 자국과 같은 분포
AL._FOREIGN_FLOOR_CTX["on"] = False
random.seed(11)
f_ovr, d_ovr = [], []
for _ in range(4000):
    a = AL._roll_newbie_age(5, "브라질", "대한민국")
    st, ov = gen_like_sites(70, a, "CM")
    s2, o2, t2, a2 = AL._apply_foreign_adult(5, "A", 1, "대한민국", "아시아", "x", "CM", "브라질", 70, a, st, ov)
    if a2 != a or o2 != ov:
        f_ovr = None; break
    f_ovr.append((a, o2))
check("하한 미적용 외국인: 나이·OVR을 사후에 바꾸지 않음", f_ovr is not None)
if f_ovr:
    by_age_f = {}
    for a, o in f_ovr: by_age_f.setdefault(a, []).append(o)
    for a in (19, 20, 21):
        dd = [gen_like_sites(70, a, "CM")[1] for _ in range(len(by_age_f[a]))]
        mf, md = sum(by_age_f[a]) / len(by_age_f[a]), sum(dd) / len(dd)
        check(f"같은 나이({a}세)·같은 목표 외국인 vs 자국 OVR 평균 차 ≤ 0.5", abs(mf - md) <= 0.5, f"{mf:.2f} vs {md:.2f}")
# 하한 적용: 잉글랜드 1부 리그 하한 88 — 어린 나이도 88 이상, 그러나 증폭되지 않는다(목표 90 기준)
mx = 0; mn = 99
for a in (16, 17, 18, 19, 20, 21, 22, 23, 24):
    for _ in range(300):
        st, ov = gen_like_sites(90, a, "CM", lhm=88, phm=None)
        nat_age = a
        s2, o2, t2, a2 = AL._apply_foreign_adult(5, "SS", 1, "잉글랜드", "유럽", "x", "CM", "브라질", 90, nat_age, st, ov)
        mx, mn = max(mx, o2), min(mn, o2)
check("하한 적용(리그 하한 88): 88 이상 유지", mn >= 88, f"최저 {mn}")
check("하한 적용: 나이 조정으로 99까지 튀지 않음(목표 90이면 최고 ≤ 92)", mx <= 92, f"최고 {mx}")
# 외국인 주전급 하한(오프시즌 구간): 그 나이의 비율만큼만, 스타·산하팀 제외
_orig_fl = AL._foreign_starter_floor
AL._foreign_starter_floor = lambda *a, **k: 90
AL._FOREIGN_FLOOR_CTX["on"] = True
st, ov = gen_like_sites(70, 20, "CM")
s2, o2, t2, a2 = AL._apply_foreign_adult(5, "A", 1, "대한민국", "아시아", "x", "CM", "브라질", 70, 20, dict(st), ov)
check("주전급 하한: 하한 90 × 20세 비율(0.90) 이상으로 올림, 나이 불변", o2 >= int(90 * 0.90) and a2 == 20, f"{ov}→{o2}")
s3, o3, t3, a3 = AL._apply_foreign_adult(5, "A", 1, "대한민국", "아시아", "x", "CM", "브라질", 70, 20, dict(st), ov,
                                        star_kind="elite")
check("주전급 하한: 스타 등급은 제외", o3 == ov)
s4, o4, t4, a4 = AL._apply_foreign_adult(999, "A", 1, "대한민국", "아시아", "x", "CM", "브라질", 70, 20, dict(st), ov)
check("주전급 하한: 산하팀 제외", o4 == ov)
s5, o5, t5, a5 = AL._apply_foreign_adult(5, "A", 1, "대한민국", "아시아", "x", "CM", "대한민국", 70, 20, dict(st), ov)
check("주전급 하한: 자국 제외", o5 == ov)
AL._FOREIGN_FLOOR_CTX["on"] = False
AL._foreign_starter_floor = _orig_fl
AL._youth_aff, AL._youth_on = _orig_aff, _orig_on

# ── [4] 국대 은퇴 판정 패스 ───────────────────────────────────────────
print("[4] _nt_retirement_pass (메모리 DB)")


def mkdb():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE intl_tournaments(id INTEGER PRIMARY KEY, year INTEGER, kind TEXT, name TEXT);
        CREATE TABLE intl_squad(tournament_id INTEGER, country TEXT, player_id INTEGER,
            appearances INTEGER DEFAULT 0, starter INTEGER DEFAULT 0, position TEXT DEFAULT '',
            slot TEXT DEFAULT '', PRIMARY KEY(tournament_id, country, player_id));
        CREATE TABLE ai_players(id INTEGER PRIMARY KEY, age INTEGER);
        CREATE TABLE ai_nt_career(player_id INTEGER PRIMARY KEY, country TEXT DEFAULT '',
            first_app_year INTEGER DEFAULT 0, last_app_year INTEGER DEFAULT 0,
            last_tournament_id INTEGER DEFAULT 0, last_position TEXT DEFAULT '',
            retired_year INTEGER DEFAULT 0, retired_age INTEGER DEFAULT 0);
    """)
    return c


c = mkdb()
c.executemany("INSERT INTO intl_tournaments VALUES(?,?,?,?)",
              [(1, 2000, "wc_qual", "예선"), (2, 2000, "olympic", "올림픽"), (3, 2001, "world", "월드컵"),
               (4, 2001, "power_eval", "평가전")])
c.executemany("INSERT INTO ai_players VALUES(?,?)",
              [(1, 42), (2, 42), (3, 27), (4, 42), (5, 42), (6, 30)])
# 1: 2000 A매치 출전(CB → slot LB) / 2: 2000 올림픽만 출전 / 3: 27세 / 4: 소집만(출전 0) /
# 5: 2000 출전, 2001 출전(초기화) / 6: 2000 출전, 클럽 은퇴(ai_players에서 삭제 예정)
c.executemany("INSERT INTO intl_squad(tournament_id,country,player_id,appearances,position,slot) "
              "VALUES(?,?,?,?,?,?)",
              [(1, "대한민국", 1, 3, "CB", "LB"), (2, "대한민국", 2, 2, "ST", ""),
               (1, "대한민국", 3, 1, "CM", "CM"), (1, "대한민국", 4, 0, "GK", ""),
               (1, "대한민국", 5, 1, "RB", "RB"), (3, "대한민국", 5, 1, "RB", "RWB"),
               (1, "대한민국", 6, 2, "LW", "LW"), (4, "대한민국", 4, 0, "GK", "")])
random.seed(7)
AL._nt_retirement_pass(c, 2000)
rows = {r["player_id"]: dict(r) for r in c.execute("SELECT * FROM ai_nt_career")}
check("2000년: 출전자만 경력 등록(소집만 한 4번 제외)", set(rows) == {1, 2, 3, 5, 6}, str(sorted(rows)))
check("올림픽 출전도 경력으로 침", 2 in rows)
check("포지션 = 맡은 자리(slot), 없으면 등록 포지션", rows[1]["last_position"] == "LB"
      and rows[2]["last_position"] == "ST")
check("출전한 해에는 은퇴 판정 없음", all(r["retired_year"] == 0 for r in rows.values()))
c.execute("DELETE FROM ai_players WHERE id=6")       # 6번 클럽 은퇴
AL._nt_retirement_pass(c, 2001)                      # 42세 · 미출전 1년 → 70%
rows = {r["player_id"]: dict(r) for r in c.execute("SELECT * FROM ai_nt_career")}
check("다시 출전하면 초기화(5번 last_app 2001, 자리 RWB)", rows[5]["last_app_year"] == 2001
      and rows[5]["last_position"] == "RWB" and rows[5]["retired_year"] == 0)
check("27세는 판정 없음", rows[3]["retired_year"] == 0)
check("클럽 은퇴 선수는 판정 없음", rows[6]["retired_year"] == 0)
check("판정 대상 수 = 2명(1·2번, 42세 미출전 1년)", AL.NT_RETIRE_STATS["judged"] == 2,
      str(AL.NT_RETIRE_STATS))
for y in range(2002, 2012):                          # 42세 미출전 2년+ … 결국 100%
    AL._nt_retirement_pass(c, y)
rows = {r["player_id"]: dict(r) for r in c.execute("SELECT * FROM ai_nt_career")}
check("42세 미출전 선수는 결국 은퇴(1·2번)", rows[1]["retired_year"] > 0 and rows[2]["retired_year"] > 0)
check("은퇴 연도 = 판정 성공 연도(2001~2005)", all(2001 <= rows[p]["retired_year"] <= 2005 for p in (1, 2)),
      f"{rows[1]['retired_year']}, {rows[2]['retired_year']}")
check("은퇴 나이 기록", rows[1]["retired_age"] == 42)
check("5번(2001 출전, 42세)도 2002년부터 판정되어 은퇴", rows[5]["retired_year"] >= 2002)
_r1 = rows[1]["retired_year"]
AL._nt_retirement_pass(c, 2012)
check("은퇴자는 다시 판정하지 않음(연도 불변)",
      c.execute("SELECT retired_year FROM ai_nt_career WHERE player_id=1").fetchone()[0] == _r1)

# 확률 실측: 35세 미출전 1년(10%)
c = mkdb()
c.execute("INSERT INTO intl_tournaments VALUES(1, 2000, 'world', 'x')")
c.executemany("INSERT INTO ai_players VALUES(?,?)", [(i, 35) for i in range(1, 20001)])
c.executemany("INSERT INTO intl_squad(tournament_id,country,player_id,appearances) VALUES(1,'브라질',?,1)",
              [(i,) for i in range(1, 20001)])
AL._nt_retirement_pass(c, 2000)
AL._nt_retirement_pass(c, 2001)
rate = AL.NT_RETIRE_STATS["retired"] / 20000
check("35세 미출전 1년 실측 ≈ 10%", abs(rate - 0.10) < 0.01, f"{rate * 100:.2f}%")
c.execute("UPDATE ai_players SET age=33")
AL._nt_retirement_pass(c, 2002)                      # 33세 미출전 2년 → 15%
rate = AL.NT_RETIRE_STATS["retired"] / AL.NT_RETIRE_STATS["judged"]
check("33세 미출전 2년 실측 ≈ 15%", abs(rate - 0.15) < 0.012, f"{rate * 100:.2f}%")

# ── [5] 선발 제외 ────────────────────────────────────────────────────
print("[5] drop_nt_retired / _nt_ret_excl_sql")
c = mkdb()
c.executemany("INSERT INTO ai_nt_career(player_id,country,retired_year) VALUES(?,?,?)",
              [(1, "대한민국", 2005), (2, "대한민국", 0), (3, "일본", 2005)])
rows = [{"id": i} for i in (1, 2, 3, 4)]
check("이 나라 은퇴자만 빠짐", [r["id"] for r in DB.drop_nt_retired(rows, c, "대한민국")] == [2, 3, 4])
check("은퇴자 없으면 그대로(같은 객체)", DB.drop_nt_retired(rows, c, "브라질") is rows)
check("SQL 조건", DB._nt_ret_excl_sql(c, "대한민국") == " AND ap.id NOT IN (1)"
      and DB._nt_ret_excl_sql(c, "브라질") == "" and DB._nt_ret_excl_sql(c, "일본", col="id") == " AND id NOT IN (3)")
c2 = sqlite3.connect(":memory:")
check("표가 없는 DB면 빈 집합", DB.nt_retired_ids(c2, "대한민국") == set())

# ── [6] 리셋 목록 ────────────────────────────────────────────────────
print("[6] reset_game_data 삭제 목록")
import inspect
check("ai_nt_career가 삭제 목록에 있음", '"ai_nt_career"' in inspect.getsource(DB.reset_game_data))

print()
print("결과:", "전부 통과" if not FAILS else f"실패 {len(FAILS)}건 — {FAILS}")
sys.exit(1 if FAILS else 0)