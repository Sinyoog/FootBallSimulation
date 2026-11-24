"""10시즌 4런 판정 — 집계 방식은 실행 전에 고정한 그대로(2026-11-21 승인).
  · AI 이적 건수(시즌 평균) = 10시즌(2000~2009년) 전체 이적 기록 수 ÷ 10 (ai_transfer_log ∪ archive, id 기준 중복 제거)
  · 국제 이적 비중 = 같은 10시즌 기록 중 (보낸·받은 팀 둘 다 있고 두 팀 나라가 다른 것) ÷ 전체 기록
  · AI 총원, 빅5 1부 팀 평균 OVR(팀별 전체 AI 평균 → 팀 평균), 빅5 상·중·하위(팀 평균 순 3등분), 등급별(SS·S·A·B) 1부 팀 평균,
    90+·95+ 인원, 빅5 1부 승점 표준편차(리그별 → 평균), 빅5 상위 25%−하위 25% 팀 평균 격차 = 10시즌째 종료 시점
  · 빅5 우승팀 수 = 10시즌 동안 빅5 리그별 고유 우승팀 수의 합(우승 = 승점 → 득실차 → 득점 순 1위)
판정: 안정 수치 ±2%(상대) / 안정 비중 ±2%p / 시드 민감 D ≤ 1.5V (V=0이면 안정 규칙, D=0이면 통과)."""
import json, os, sqlite3, statistics as S, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # 프로젝트 루트(constants)
from constants import get_country_league_grade
B = sys.argv[1] if len(sys.argv) > 1 else "."   # 사용: python3 analyze10.py <s10_* 결과 폴더들이 있는 폴더>
BIG5 = ("잉글랜드", "스페인", "독일", "이탈리아", "프랑스")
RUNS = {("base", 1): "s10_base_w1", ("base", 2): "s10_base_w2", ("new", 1): "s10_new_w1", ("new", 2): "s10_new_w2"}


def metrics(d):
    c = sqlite3.connect(f"{B}/{d}/run.db"); c.row_factory = sqlite3.Row
    c.execute(f"ATTACH DATABASE '{B}/{d}/run.history.db' AS hist")
    tc = {r["id"]: (r["country"], r["tier"], r["cg"]) for r in c.execute(
        "SELECT t.id, cn.name country, l.tier, cn.grade cg FROM teams t JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id")}
    m = {}
    m["AI 총원"] = c.execute("SELECT COUNT(*) FROM ai_players").fetchone()[0]
    rows = {}
    for tbl in ("ai_transfer_log", "ai_transfer_log_archive"):
        for r in c.execute(f"SELECT id, from_team_id f, to_team_id t FROM {tbl} WHERE year BETWEEN 2000 AND 2009"):
            rows[r["id"]] = (r["f"], r["t"])
    m["AI 이적 건수(시즌 평균)"] = len(rows) / 10
    intl = sum(1 for f, t in rows.values() if f and t and f in tc and t in tc and tc[f][0] != tc[t][0])
    m["국제 이적 비중"] = intl / len(rows) if rows else 0
    tavg = {r["team_id"]: r["a"] for r in c.execute("SELECT team_id, AVG(ovr) a FROM ai_players WHERE team_id>0 GROUP BY team_id")}
    big = sorted(v for k, v in tavg.items() if k in tc and tc[k][0] in BIG5 and tc[k][1] == 1)
    m["빅5 1부 팀 평균 OVR"] = S.mean(big)
    k3 = len(big) // 3
    m["빅5 하위 1/3 팀 평균"] = S.mean(big[:k3]); m["빅5 중위 1/3 팀 평균"] = S.mean(big[k3:len(big) - k3])
    m["빅5 상위 1/3 팀 평균"] = S.mean(big[len(big) - k3:])
    for g in ("SS", "S", "A", "B"):
        v = [a for k, a in tavg.items() if k in tc and tc[k][1] == 1 and get_country_league_grade(tc[k][0], tc[k][2]) == g]
        m[f"{g}급 1부 팀 평균 OVR"] = S.mean(v) if v else 0
    m["90+ 인원"] = c.execute("SELECT COUNT(*) FROM ai_players WHERE ovr>=90").fetchone()[0]
    m["95+ 인원"] = c.execute("SELECT COUNT(*) FROM ai_players WHERE ovr>=95").fetchone()[0]
    q = max(1, len(big) // 4)
    m["빅5 상위25%−하위25% 격차"] = S.mean(big[-q:]) - S.mean(big[:q])
    # 우승팀·승점 표준편차(빅5 1부 리그)
    big_l = [r["id"] for r in c.execute("SELECT l.id FROM leagues l JOIN countries cn ON cn.id=l.country_id WHERE l.tier=1 AND cn.name IN (%s)" % ",".join("?" * 5), BIG5)]
    seasons = sorted({r[0] for r in c.execute("SELECT DISTINCT season FROM hist.league_season_standings")})[-10:]
    champs, stds = 0, []
    for lid in big_l:
        ch = set()
        for s in seasons:
            st = c.execute("SELECT team_id, wins*3+draws pts, goals_for-goals_against gd, goals_for gf FROM hist.league_season_standings "
                           "WHERE league_id=? AND season=? ORDER BY pts DESC, gd DESC, gf DESC", (lid, s)).fetchall()
            if st:
                ch.add(st[0]["team_id"])
                if s == seasons[-1]:
                    stds.append(S.pstdev([r["pts"] for r in st]))
        champs += len(ch)
    m["빅5 우승팀 수(10시즌 고유)"] = champs
    m["빅5 1부 승점 표준편차"] = S.mean(stds) if stds else 0
    m["_seasons"] = seasons
    return m


KIND = {"AI 총원": "num", "AI 이적 건수(시즌 평균)": "num", "빅5 1부 팀 평균 OVR": "num", "빅5 상위 1/3 팀 평균": "num",
        "빅5 중위 1/3 팀 평균": "num", "빅5 하위 1/3 팀 평균": "num", "SS급 1부 팀 평균 OVR": "num", "S급 1부 팀 평균 OVR": "num",
        "A급 1부 팀 평균 OVR": "num", "B급 1부 팀 평균 OVR": "num", "90+ 인원": "num", "국제 이적 비중": "share",
        "95+ 인원": "seed", "빅5 우승팀 수(10시즌 고유)": "seed", "빅5 1부 승점 표준편차": "seed", "빅5 상위25%−하위25% 격차": "seed"}


def judge(kind, b1, b2, n1, n2):
    bm, nm = (b1 + b2) / 2, (n1 + n2) / 2
    D = abs(nm - bm)
    if kind == "seed":
        V = max(abs(b1 - b2), abs(n1 - n2))
        if V > 0:
            return D <= 1.5 * V, f"D {D:.3g} ≤ 1.5V {1.5 * V:.3g}"
        kind = "num"
        if D == 0:
            return True, "V=0, D=0"
    if kind == "share":
        return D <= 0.02, f"차이 {D * 100:.2f}%p"
    rel = D / abs(bm) if bm else (0 if D == 0 else 1)
    return rel <= 0.02, f"차이 {rel * 100:.2f}%"


if __name__ == "__main__":
    M = {k: metrics(v) for k, v in RUNS.items()}
    print("시즌:", M[("base", 1)]["_seasons"])
    ok_all = True
    for name, kind in KIND.items():
        b1, b2, n1, n2 = (M[(a, s)][name] for a, s in (("base", 1), ("base", 2), ("new", 1), ("new", 2)))
        ok, why = judge(kind, b1, b2, n1, n2)
        ok_all &= ok
        f = (lambda x: f"{x * 100:.1f}%") if kind == "share" else (lambda x: f"{x:.2f}" if isinstance(x, float) else str(x))
        print(f"{'✅' if ok else '❌'} {name} [{ {'num': '안정 ±2%', 'share': '안정 ±2%p', 'seed': '시드 민감'}[kind]}] "
              f"원본 {f(b1)} / {f(b2)} · 확정본 {f(n1)} / {f(n2)} → {why}")
    print("전체:", "통과" if ok_all else "불합격 항목 있음")
