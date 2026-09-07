# -*- coding: utf-8 -*-
"""salary_distribution_probe.py

[2026-08 신설, tier_decay/affordability 설계 전 실측용 임시 스크립트]

목적: _calc_salary()를 건드리지 않고 순수 호출만으로 "현재 시스템이
실제로 만들어내는 연봉 곡선"을 관찰한다. 시뮬레이션을 돌리지 않고
game.db에 이미 있는 실제 팀/리그/국가 데이터를 그대로 사용해서
_calc_salary(grade, tier, ovr, country, team_name, year, team_id)를
그리드로 호출한다.

관찰하려는 것 두 가지:
  1) 같은 국가 안에서 tier(1~4부)가 내려갈 때 같은 OVR의 연봉이
     얼마나 감소하는가 → tier_decay 역산 근거
  2) 같은 tier 안에서도 구단 명성(prestige_salary_mult)/체급
     (_club_strength_salary_mult)에 따라 상위/중위/하위 구단끼리
     얼마나 차이가 나는가 → affordability를 리그 단위로만 볼지,
     구단 단위까지 더 세분화할지 판단 근거

주의: "선수의 팀 내 역할(레전드/에이스/주전/벤치)"은 이 스크립트의
입력이 아니다. _calc_salary() 자체가 그런 개념을 받지 않기 때문 —
오직 OVR(50~99 그리드) × 국가 × tier × 대표구단(상/중/하)만 넣어서
함수의 출력(연봉)이 어떤 곡선을 그리는지 관찰할 뿐이다.

출력: salary_probe_raw.csv (country, tier, ovr, grade, team_name, team_id, raw_salary_krw)
      salary_probe_pivot.csv (country, tier, club_rank, OVR50, OVR55, ..., OVR99)
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)

import csv
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_conn, load_from_disk
from economy import _calc_salary
from constants import get_league_grade

PROBE_YEAR = 2026          # economy_index 앵커 연도 — 시대감 배제하고 "현재 기준"으로 고정
OVR_GRID = list(range(50, 100, 5))  # 50,55,...,95 + 99 아래서 별도 추가
OVR_GRID.append(99)
TIERS = [1, 2, 3, 4]


def _pick_representative_teams(c, country_id, tier, n=3):
    """해당 국가×tier의 팀들을 팀 평균 OVR 기준으로 정렬해서
    상위/중위/하위 n개 팀을 뽑는다. AI 선수가 없는 신생 세이브라면
    OVR 대신 team id 순서로 폴백한다."""
    rows = c.execute("""
        SELECT t.id, t.name
        FROM teams t
        JOIN leagues l ON t.league_id = l.id
        WHERE l.country_id = ? AND l.tier = ?
    """, (country_id, tier)).fetchall()
    if not rows:
        return []

    ovr_map = {
        r["team_id"]: r["avg_ovr"]
        for r in c.execute("""
            SELECT team_id, AVG(ovr) as avg_ovr FROM ai_players
            GROUP BY team_id
        """).fetchall()
        if r["avg_ovr"] is not None
    }

    teams = [(r["id"], r["name"], ovr_map.get(r["id"], 0)) for r in rows]
    teams.sort(key=lambda x: x[2], reverse=True)

    if len(teams) <= n:
        # 팀이 적으면 있는 만큼만 (상/중/하 라벨은 순서로 근사)
        picks = teams
    else:
        idx = [0, len(teams) // 2, len(teams) - 1]  # 최상위 / 중위 / 최하위
        picks = [teams[i] for i in idx]

    labels = ["top", "mid", "bottom"][:len(picks)]
    return list(zip(labels, picks))


def main():
    # [주의] 이 게임은 game.db를 "세이브 파일"로만 쓰고, 실행 중엔 인메모리
    # DB(SQLite 공유캐시 :memory:)를 라이브 DB로 쓴다(database.py 참고).
    # get_conn()만 부르면 빈 인메모리 DB가 나오므로, 먼저 디스크의 game.db를
    # 인메모리로 로드해야 실제 팀/리그/국가 데이터를 볼 수 있다.
    load_from_disk()
    conn = get_conn()
    c = conn.cursor()

    countries = c.execute("SELECT id, name, grade FROM countries ORDER BY name").fetchall()

    raw_rows = []

    for cn in countries:
        country_id, country_name, country_grade = cn["id"], cn["name"], cn["grade"]

        for tier in TIERS:
            reps = _pick_representative_teams(c, country_id, tier, n=3)
            if not reps:
                continue  # 이 국가엔 해당 tier 리그 자체가 없음

            wealth_grade = get_league_grade(country_name, country_grade)

            for club_rank, (team_id, team_name, team_avg_ovr) in reps:
                for ovr in OVR_GRID:
                    sal = _calc_salary(
                        wealth_grade, tier, ovr,
                        country=country_name, team_name=team_name,
                        year=PROBE_YEAR, team_id=team_id, talent_tier=None,
                    )
                    raw_rows.append({
                        "country": country_name,
                        "tier": tier,
                        "club_rank": club_rank,
                        "team_name": team_name,
                        "team_id": team_id,
                        "team_avg_ovr": round(team_avg_ovr, 1),
                        "ovr": ovr,
                        "grade": wealth_grade,
                        "raw_salary_krw_thousand": sal,   # 천원 단위 (게임 내부 저장 단위)
                        "raw_salary_eok": round(sal / 100000, 3),  # 억원 환산 (사람이 읽기 편하게)
                    })

    conn.close()

    if not raw_rows:
        print("데이터 없음 — game.db에 국가/리그/팀 데이터가 있는지 확인 필요")
        return

    # ── raw CSV ──────────────────────────────────────────────
    raw_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "salary_probe_raw.csv")
    fieldnames = list(raw_rows[0].keys())
    with open(raw_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(raw_rows)

    # ── pivot CSV: country, tier, club_rank -> OVR별 억원 ──────
    pivot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "salary_probe_pivot.csv")
    pivot_map = {}
    for r in raw_rows:
        key = (r["country"], r["tier"], r["club_rank"], r["team_name"])
        pivot_map.setdefault(key, {})[r["ovr"]] = r["raw_salary_eok"]

    pivot_fieldnames = ["country", "tier", "club_rank", "team_name"] + [f"OVR{o}" for o in OVR_GRID]
    with open(pivot_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=pivot_fieldnames)
        w.writeheader()
        for (country, tier, club_rank, team_name), ovr_vals in sorted(pivot_map.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
            row = {"country": country, "tier": tier, "club_rank": club_rank, "team_name": team_name}
            for o in OVR_GRID:
                row[f"OVR{o}"] = ovr_vals.get(o, "")
            w.writerow(row)

    print(f"완료: {len(raw_rows)}행")
    print(f"  raw   -> {raw_path}")
    print(f"  pivot -> {pivot_path}")

    # ── 콘솔에 한국 케이스 샘플 미리보기 (검증용) ───────────────
    print("\n[미리보기] 대한민국 tier별 OVR80 연봉(억원, top 구단 기준):")
    for tier in TIERS:
        for r in raw_rows:
            if r["country"] == "대한민국" and r["tier"] == tier and r["ovr"] == 80 and r["club_rank"] == "top":
                print(f"  K{tier} ({r['team_name']}): {r['raw_salary_eok']}억")
                break


if __name__ == "__main__":
    main()