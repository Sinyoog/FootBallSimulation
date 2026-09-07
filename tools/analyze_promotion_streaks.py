# -*- coding: utf-8 -*-
"""
analyze_promotion_streaks.py — "승격 후 다음 시즌 또 승격" 연속승격 확률 측정.

목적: GPT 쪽 제안("2부->1부처럼 승격 직후 다음 시즌 또 승격하는 비율을
2~3% 정도로 자연 발생시키는 게 현실적"이라는 견해)이 실제 이 게임
시뮬레이션에서 몇 %로 나오는지, 감이 아니라 헤드리스 실행 결과로 직접
측정한다. headless_runner.run()으로 N시즌을 새로 굴린 뒤, 그 실행에서
쌓인 promotion_log를 team_id별로 묶어 "연속 승격 스트릭" 길이 분포와
단계별 조건부 생존확률을 계산한다.

game.db(코드베이스에 포함된 시드 템플릿, 시즌1 상태)를 원본 그대로
복사해서 새 헤드리스 세이브로 굴리므로 실제 세이브는 전혀 건드리지 않는다.

사용법: python3 analyze_promotion_streaks.py [시즌수] [시드]
(기본 20시즌, 시드 99)
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import sys
import os
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import headless_runner
import database

N_SEASONS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 99

print(f"[analyze] {N_SEASONS}시즌 헤드리스 실행 시작 (seed={SEED})...")
headless_runner.run(N_SEASONS, seed=SEED, standalone_output=True)
print("\n[analyze] 실행 완료 — promotion_log 분석 시작\n")

conn = database.get_conn()
c = conn.cursor()

# to_tier < from_tier 인 행만 '승격'(강등은 to_tier > from_tier).
rows = c.execute(
    "SELECT year, team_id, team_name, from_tier, to_tier FROM promotion_log "
    "WHERE to_tier < from_tier ORDER BY team_id, year").fetchall()

by_team = defaultdict(list)
max_year_seen = 0
for r in rows:
    by_team[r["team_id"]].append(r["year"])
    max_year_seen = max(max_year_seen, r["year"])

streak_lengths = []      # 팀별로 이번 실행에서 나온 "연속 승격 런" 길이들
continuation_pairs = 0   # 승격 이벤트 중, 바로 다음 해에도 그 팀이 또 승격한 건수
censored = 0             # 관측 마지막 해(max_year_seen)의 승격 — "다음 시즌"이
                          # 관측 범위 밖이라 이어졌는지 알 수 없는 건(우측 절단)
promotion_events = 0

for team_id, years in by_team.items():
    years = sorted(set(years))
    run_len = 1
    for i in range(1, len(years)):
        if years[i] == years[i - 1] + 1:
            run_len += 1
        else:
            streak_lengths.append(run_len)
            run_len = 1
    streak_lengths.append(run_len)

    for i, y in enumerate(years):
        if y == max_year_seen:
            censored += 1
            continue  # 분모(promotion_events)에서 제외 — 다음 시즌 관측 불가
        promotion_events += 1
        if i + 1 < len(years) and years[i + 1] == y + 1:
            continuation_pairs += 1

print(f"[요약] 관측 구간: {N_SEASONS}시즌 (seed={SEED}) | 승격 이벤트 총 {len(rows)}건 "
      f"| 승격 경험 팀 {len(by_team)}개")
print(f"[요약] 마지막 관측 연도({max_year_seen})의 승격 {censored}건은 '다음 시즌' 결과를 "
      f"알 수 없어 아래 비율 계산에서 제외(우측 절단)\n")

if promotion_events:
    pct = continuation_pairs / promotion_events * 100
    print(f"[핵심 지표] 승격 후 '바로 다음 시즌'에도 또 승격한 비율: "
          f"{continuation_pairs}/{promotion_events} = {pct:.3f}%")
    print("            (GPT 제안 목표: 2~3%)\n")
else:
    print("[핵심 지표] 계산 가능한 승격 이벤트가 없음 (시즌 수를 늘려서 재실행 필요)\n")

hist = Counter(streak_lengths)
total_runs = sum(hist.values())
print(f"[연속 승격 스트릭 길이 분포] (팀당 1런, 총 {total_runs}개 런 — 이번 실행 마지막 해에")
print(" 걸쳐 아직 안 끝났을 수 있는 런도 그대로 포함되어 있어 약간 과소 스트릭으로 잡힐 수 있음)")
for length in sorted(hist):
    cnt = hist[length]
    print(f"  {length}연속: {cnt}건 ({cnt / total_runs * 100:.3f}%)")

print("\n[조건부 생존확률] (n연속을 이미 달성한 런들 중, n+1연속까지 이어진 비율)")
max_len = max(hist) if hist else 0
for n in range(1, max_len):
    reached_n = sum(cnt for length, cnt in hist.items() if length >= n)
    reached_n1 = sum(cnt for length, cnt in hist.items() if length >= n + 1)
    if reached_n:
        print(f"  {n}연속 -> {n + 1}연속: {reached_n1}/{reached_n} = "
              f"{reached_n1 / reached_n * 100:.3f}%")