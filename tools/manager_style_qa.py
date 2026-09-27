"""manager_style_qa.py — 감독 시스템 ②단계 검증 (2026-09 신설)

①과 달리 ②는 **경기 결과가 바뀌는 게 정상**이다. 그래서 "똑같은가"가
아니라 "의도한 방향으로만 바뀌었는가"를 잰다.

  A. 하위호환    — 성향을 안 넘기면(None) 예전과 완전히 같은 경기인가.
                   중립 성향(MIXED/MID_BLOCK)도 보정이 0이라 None과 같아야
                   한다(그래야 "감독이 아직 없는 팀"과 "평범한 감독"이
                   같은 결과를 낸다).
  B. 스타일 효과  — 점유 빌드업 vs 직선적 빌드업이 실제로 점유율 차이를
                   만드는가. 전방 압박 vs 로우 블록이 상대 점유율을
                   움직이는가. 안 움직이면 ②를 한 의미가 없다.
  C. 득점 총량    — 성향을 켜도 경기당 득점이 거의 그대로인가. 설계상
                   각 성향은 반대 방향 두 항(점유↑/기회품질↓ 등)을 같이
                   받으므로 총량이 흔들리면 안 된다. 여기가 깨지면
                   constants의 MANAGER_*_EDGE 값을 다시 잡아야 한다.
  D. 포메이션 선호 — 감독 3축이 실제로 다른 포메이션을 뽑는가(순수 함수
                   검사라 DB 불필요).

사용법:
    python tools/manager_style_qa.py --db <세이브.db> [--n 200]
    python tools/manager_style_qa.py                  # D만 실행
"""
import _path  # noqa: F401
import argparse
import os
import random
import shutil
import statistics
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_POSS = {"style_attack": "BALANCED", "style_buildup": "POSSESSION", "style_press": "MID_BLOCK"}
_DIRECT = {"style_attack": "BALANCED", "style_buildup": "DIRECT", "style_press": "MID_BLOCK"}
_NEUTRAL = {"style_attack": "BALANCED", "style_buildup": "MIXED", "style_press": "MID_BLOCK"}
_HIGH = {"style_attack": "BALANCED", "style_buildup": "MIXED", "style_press": "HIGH_PRESS"}
_LOW = {"style_attack": "BALANCED", "style_buildup": "MIXED", "style_press": "LOW_BLOCK"}


# ══════════════════════════════════════════════════════════════
# D. 감독 3축 → 포메이션 선호 (DB 불필요)
# ══════════════════════════════════════════════════════════════
def test_formation_preference():
    from formation_logic import manager_style_fit
    from constants import FORMATION_STYLE

    def top(sa, sb, sp, n=4):
        order = sorted(FORMATION_STYLE,
                       key=lambda f: -manager_style_fit(f, sa, sb, sp))
        return order[:n]

    cases = [
        ("공격 + 점유 + 전방압박", ("ATTACKING", "POSSESSION", "HIGH_PRESS")),
        ("공격 + 직선 + 전방압박", ("ATTACKING", "DIRECT", "HIGH_PRESS")),
        ("수비 + 직선 + 로우블록", ("DEFENSIVE", "DIRECT", "LOW_BLOCK")),
        ("수비 + 점유 + 미들블록", ("DEFENSIVE", "POSSESSION", "MID_BLOCK")),
    ]
    problems = []
    seen = {}
    for label, args in cases:
        picks = top(*args)
        seen[label] = picks
        print(f"  {label}: {', '.join(picks)}")
    # 같은 공격 성향인데 빌드업만 달라도 1순위가 달라야 한다 —
    # 신민용 설계의 핵심("둘 다 공격적인 감독이지만 경기 양상이 다르다").
    a = seen["공격 + 점유 + 전방압박"][0]
    b = seen["공격 + 직선 + 전방압박"][0]
    if a == b:
        problems.append(f"D: 빌드업만 바꿨는데 1순위가 같음 ({a}) — 축이 안 먹는다")
    else:
        print(f"  → 빌드업만 바꿔도 1순위가 {a} → {b}로 달라짐")
    return problems


# ══════════════════════════════════════════════════════════════
# A~C. 실제 경기로 측정
# ══════════════════════════════════════════════════════════════
def _setup(db_path, work):
    import database
    os.makedirs(work, exist_ok=True)
    db = os.path.join(work, "style.db")
    shutil.copy(os.path.abspath(db_path), db)
    hsrc = os.path.splitext(os.path.abspath(db_path))[0] + ".history.db"
    if os.path.exists(hsrc):
        shutil.copy(hsrc, os.path.splitext(db)[0] + ".history.db")
    database.USE_MEMORY_DB = False
    database.DB_PATH = db
    os.chdir(work)
    database.reset_conn_pool()
    database.init_db()

    from database import get_conn
    import game_engine as ge
    conn = get_conn()
    teams = [r["id"] for r in conn.execute(
        """SELECT t.id FROM teams t
           WHERE (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) >= 18
           ORDER BY t.id LIMIT 16""")]
    c = conn.cursor()
    forms = {t: ge._team_formation(c, t) for t in teams}
    conn.close()
    return teams, forms


def _run_series(teams, forms, n, hs, as_, seed=24680):
    """같은 대진·같은 시드로 n경기를 돌려 (스코어, 점유율) 목록을 모은다."""
    from match_sim.tactical_engine import simulate_tactical_match
    from match_sim.match_flow import _select_lineup, select_bench
    out = []
    random.seed(seed)
    for i in range(n):
        h, a = teams[i % len(teams)], teams[(i + 5) % len(teams)]
        if h == a:
            continue
        hl = _select_lineup(h, forms[h])
        al = _select_lineup(a, forms[a])
        sim = simulate_tactical_match(
            hl, al, home_adv=0.0,
            home_formation=forms[h], away_formation=forms[a],
            home_bench=select_bench(h, hl), away_bench=select_bench(a, al),
            home_style=hs, away_style=as_)
        out.append((sim["home_score"], sim["away_score"],
                    float(sim["home_stats"].get("poss", 50)),
                    int(sim["home_stats"].get("shots", 0)),
                    int(sim["away_stats"].get("shots", 0))))
    return out


def _summary(rows):
    goals = [r[0] + r[1] for r in rows]
    poss = [r[2] for r in rows]
    shots = [r[3] + r[4] for r in rows]
    return (statistics.mean(goals), statistics.mean(poss), statistics.mean(shots))


def test_engine(db_path, work, n):
    teams, forms = _setup(db_path, work)
    problems = []

    # ── A. 하위호환 ──────────────────────────────────────────────
    none_rows = _run_series(teams, forms, n, None, None)
    neut_rows = _run_series(teams, forms, n, _NEUTRAL, _NEUTRAL)
    if none_rows != neut_rows:
        diff = sum(1 for x, y in zip(none_rows, neut_rows) if x != y)
        problems.append(f"A: 성향 없음 vs 중립 성향 결과가 다름 ({diff}/{len(none_rows)}경기)")
    print(f"  A. 성향 없음 vs 중립(MIXED/MID_BLOCK) — 다른 경기 "
          f"{sum(1 for x, y in zip(none_rows, neut_rows) if x != y)}/{len(none_rows)}")

    # ── B. 스타일 효과 ───────────────────────────────────────────
    poss_rows = _run_series(teams, forms, n, _POSS, _NEUTRAL)
    dir_rows = _run_series(teams, forms, n, _DIRECT, _NEUTRAL)
    hi_rows = _run_series(teams, forms, n, _NEUTRAL, _HIGH)   # 상대가 하이프레스
    lo_rows = _run_series(teams, forms, n, _NEUTRAL, _LOW)    # 상대가 로우블록

    g0, p0, s0 = _summary(none_rows)
    gp, pp, sp = _summary(poss_rows)
    gd, pd, sd = _summary(dir_rows)
    gh, ph, sh = _summary(hi_rows)
    gl, pl, sl = _summary(lo_rows)

    print(f"  B. 홈팀 평균 점유율")
    print(f"       기준(중립)          {p0:5.1f}%")
    print(f"       홈이 점유 빌드업     {pp:5.1f}%  ({pp - p0:+.1f}%p)")
    print(f"       홈이 직선적 빌드업   {pd:5.1f}%  ({pd - p0:+.1f}%p)")
    print(f"       상대가 전방 압박     {ph:5.1f}%  ({ph - p0:+.1f}%p)")
    print(f"       상대가 로우 블록     {pl:5.1f}%  ({pl - p0:+.1f}%p)")
    # 판정 하한은 표본오차의 3배 정도로 잡는다 — 실측(n=1200, 시드 2개)에서
    # 빌드업 축은 ±3.1~3.3%p, 압박 축은 ±1.9~3.6%p가 나왔다. n이 작으면
    # 이 값들이 노이즈에 묻히므로(n=200이면 표준오차가 ±0.9%p라 실제로
    # 한 번 흔들리는 걸 확인했다) 기준을 표본 수에 맞춰 움직인다.
    _se = 13.0 / max(1.0, len(none_rows) ** 0.5)     # 점유율 표준편차 ≈ 13
    _need = max(0.5, 2.5 * _se)
    print(f"       (표본 {len(none_rows)}경기 · 표준오차 ±{_se:.2f}%p · "
          f"판정 하한 {_need:.2f}%p)")
    if not (pp > p0 + _need):
        problems.append(f"B: 점유 빌드업이 점유율을 못 올림 ({p0:.1f} → {pp:.1f})")
    if not (pd < p0 - _need):
        problems.append(f"B: 직선적 빌드업이 점유율을 못 내림 ({p0:.1f} → {pd:.1f})")
    if not (ph < p0 - _need):
        problems.append(f"B: 상대 전방압박이 내 점유율을 못 내림 ({p0:.1f} → {ph:.1f})")
    if not (pl > p0 + _need):
        problems.append(f"B: 상대 로우블록이 내 점유율을 못 올림 ({p0:.1f} → {pl:.1f})")

    # ── C. 득점 총량 ─────────────────────────────────────────────
    print(f"  C. 경기당 총 득점 (양팀 합)")
    for label, g in (("기준(중립)", g0), ("점유 빌드업", gp), ("직선적 빌드업", gd),
                     ("상대 전방압박", gh), ("상대 로우블록", gl)):
        print(f"       {label:14s} {g:5.2f}  ({g - g0:+.2f})")
    worst = max(abs(g - g0) for g in (gp, gd, gh, gl))
    # 기준선의 15%를 넘게 흔들리면 밸런스 재조정이 필요하다고 본다.
    limit = max(0.25, g0 * 0.15)
    if worst > limit:
        problems.append(f"C: 득점 총량이 {worst:+.2f}골 흔들림 (허용 {limit:.2f})")
    print(f"       최대 변동 {worst:.2f}골 / 허용 {limit:.2f}골")

    print(f"  C. 경기당 총 슈팅: 기준 {s0:.1f} / 점유 {sp:.1f} / 직선 {sd:.1f}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--n", type=int, default=600,
                    help="경기 수. 점유율 표준오차가 n=200이면 ±0.9%p, "
                         "n=600이면 ±0.5%p, n=1200이면 ±0.4%p다 — "
                         "작게 주면 B 판정이 노이즈로 흔들린다.")
    ap.add_argument("--work", default=None)
    args = ap.parse_args()

    print("D. 감독 3축 → 포메이션 선호")
    problems = test_formation_preference()
    print()

    if args.db:
        work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="style_qa_"))
        print("A~C. 전술엔진 연결 (하위호환 / 스타일 효과 / 득점 총량)")
        problems += test_engine(args.db, work, args.n)
        print()
    else:
        print("A~C 건너뜀 — --db <세이브.db>를 주면 실제 경기로도 검사합니다.\n")

    if problems:
        print("=== 실패 ===")
        for x in problems:
            print(f"  {x}")
        return 1
    print("=== 전체 통과 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())