# -*- coding: utf-8 -*-
"""mvp_award(대회 V11 · 리그 C λ0.3 + D) + game_engine 연결 헬퍼 단위 테스트 — DB를 쓰지 않는다.
  python tools/test_mvp_award.py"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mvp_award as MA        # noqa: E402
import game_engine as GE      # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'통과' if cond else '실패'}] {name} {detail}")
    if not cond:
        FAILS.append(name)


def close(a, b, eps=1e-9):
    return abs(a - b) < eps


print("① 진출 단계 계수 V11")
old = {"W": 1.00, "RU": 0.95, "SF": 0.90, "KO": 0.68, "G": 0.60}
f = MA.stage_v11(lambda k: old[k])
check("우승 1.00", close(f("W"), 1.00))
check("준우승 0.985", close(f("RU"), 0.985))
check("4강 0.97", close(f("SF"), 0.97))
check("첫 녹아웃 0.904", close(f("KO"), 0.904))
check("조별 0.88", close(f("G"), 0.88))
check("계수 None → 조별 하한", close(MA.stage_v11(lambda k: None)("x"), 0.88))
check("단계 함수가 없으면 None", MA.stage_v11(None) is None)

print("② 출전 분모 = 그 대회 최다 경기 팀")
pool = [{"team_id": 1}, {"team_id": 2}, {"team_id": 3}]
check("dict 분모 → 풀 안 최댓값", MA.max_matches(pool, {1: 4, 2: 7, 3: 5, 9: 99}) == 7)
check("스칼라 분모는 그대로", MA.max_matches(pool, 38) == 38)
check("분모 없음 → None", MA.max_matches(pool, None) is None and MA.max_matches([], {}) is None)
check("국가 키(stage_key)", MA.max_matches([{"stage_key": "A"}, {"stage_key": "B"}], {"A": 3, "B": 7}, "stage_key") == 7)

print("③ 리그 그룹·후보 자격")
check("SS 1부 → 상위", MA.league_group("SS", 1) == MA.GROUP_TOP)
check("A 1부 → 상위", MA.league_group("A", 1) == MA.GROUP_TOP)
check("B 1부 → B 이하", MA.league_group("B", 1) == MA.GROUP_MID)
check("등급 없음 1부 → B 이하", MA.league_group(None, 1) == MA.GROUP_MID)
check("SS 2부 → 2부 이하", MA.league_group("SS", 2) == MA.GROUP_LOW)
check("50% 출전 자격", MA.league_eligible({"position": "ST", "matches": 19}, 38)
      and not MA.league_eligible({"position": "ST", "matches": 18}, 38))
check("포지션 그룹 없는 선수 제외", not MA.league_eligible({"position": "SS", "matches": 38}, 38))


def mk(pid, pos, rating, g=0, a=0, m=34, sv=0, gc=0, gcpm=None, age=27, team=1, role="core"):
    return {"player_id": pid, "position": pos, "rating": rating, "goals": g, "assists": a, "matches": m,
            "saves": sv, "goals_conceded": gc, "gcpm": gcpm, "age": age, "team_id": team, "stage_key": team,
            "role": role}


print("④ 리그 보정 평점")
rng = random.Random(7)
elig = []
pid = 0
for pos, base, n in (("ST", 7.2, 8), ("CB", 6.9, 10), ("CM", 7.0, 10), ("GK", 6.8, 6), ("LW", 7.1, 6)):
    for _ in range(n):
        pid += 1
        elig.append(mk(pid, pos, round(base + rng.uniform(-0.2, 0.2), 3), g=rng.randint(0, 20), a=rng.randint(0, 10),
                       sv=rng.randint(40, 120) if pos == "GK" else 0, gc=rng.randint(20, 50) if pos == "GK" else 0,
                       gcpm=round(rng.uniform(0.6, 1.8), 2)))
adj = MA.league_adjusted_ratings(elig, MA.GROUP_TOP, 38)
shuf = list(elig)
rng.shuffle(shuf)
check("입력 순서와 무관", MA.league_adjusted_ratings(shuf, MA.GROUP_TOP, 38) == adj)
check("입력을 바꾸지 않음", all("gcpm" in e and "rating" in e for e in elig) and elig[0]["rating"] == shuf[[e["player_id"] for e in shuf].index(1)]["rating"])
few = elig + [mk(900, "LWB", 7.4), mk(901, "LWB", 7.3)]          # FB 종류 2명 → z 환산 없음
adj2 = MA.league_adjusted_ratings(few, MA.GROUP_TOP, 38)
sd_comp = MA._pstdev([e["rating"] for e in few])
beta = MA.BETA * sd_comp / MA.SD_REF
check("포지션 후보 3명 미만 → 원래 평점 + 기록 가산",
      close(adj2[900], 7.4 + beta * MA.record_score(few[-2], MA.GROUP_TOP, 38)))
# 같은 포지션 안에서는 평점이 높을수록(기록 같으면) 보정 평점도 높다
two = [mk(1, "ST", 7.0, g=10), mk(2, "ST", 7.3, g=10), mk(3, "ST", 7.1, g=10), mk(4, "CB", 6.9), mk(5, "CB", 7.0),
       mk(6, "CB", 6.8)]
a3 = MA.league_adjusted_ratings(two, MA.GROUP_MID, 38)
check("같은 포지션·같은 기록 → 평점 순서 유지", a3[2] > a3[3] > a3[1])
narrow = [mk(i, "CM", 7.0 + 0.01 * (i % 5), a=i % 4) for i in range(1, 11)]
wide = [mk(i, "CM", 7.0 + 0.10 * (i % 5), a=i % 4) for i in range(1, 11)]
gap = lambda grp: (MA.league_adjusted_ratings(grp, MA.GROUP_MID, 38)[4] - MA.league_adjusted_ratings(grp, MA.GROUP_MID, 38)[1])
rec_gap = MA.record_score(narrow[3], MA.GROUP_MID, 38) - MA.record_score(narrow[0], MA.GROUP_MID, 38)
beta_n = MA.BETA * MA._pstdev([e["rating"] for e in narrow]) / MA.SD_REF
beta_w = MA.BETA * MA._pstdev([e["rating"] for e in wide]) / MA.SD_REF
check("평점 폭이 좁은 리그일수록 β가 작다(D)", beta_n < beta_w and beta_n < MA.BETA,
      f"(좁은 {beta_n:.3f} · 넓은 {beta_w:.3f}, 기록 차 {rec_gap:.3f})")

print("⑤ 기록 점수")
check("GK: 같은 슈팅에서 선방 많을수록 높다",
      MA.record_score(mk(1, "GK", 7, sv=120, gc=20), MA.GROUP_TOP, 38) > MA.record_score(mk(1, "GK", 7, sv=100, gc=40), MA.GROUP_TOP, 38))
check("ST: 골 많을수록 높다",
      MA.record_score(mk(1, "ST", 7, g=25), MA.GROUP_TOP, 38) > MA.record_score(mk(1, "ST", 7, g=10), MA.GROUP_TOP, 38))
check("CB: 팀 실점 적을수록 높다",
      MA.record_score(mk(1, "CB", 7, gcpm=0.7), MA.GROUP_TOP, 38) > MA.record_score(mk(1, "CB", 7, gcpm=1.5), MA.GROUP_TOP, 38))
cb_none = MA.record_score(mk(1, "CB", 7, g=3, gcpm=None), MA.GROUP_TOP, 38)
cb_good = MA.record_score(mk(1, "CB", 7, g=3, gcpm=0.7), MA.GROUP_TOP, 38)
check("CB 실점 정보 없음 → 공격 기록 몫만(0 < 없음 < 실점 좋음)", 0 < cb_none < cb_good)

print("⑥ game_engine._mvp_v11_apply (대회)")
# 우승팀(1) 선수 평점 7.20 vs 4강 팀(2) 선수 평점 7.35 — 예전 공식은 우승팀, V11은 4강 팀 선수
stage = {1: 1.00, 2: 0.90, 3: 0.60}
cnt = {1: 7, 2: 7, 3: 3}
tp = [mk(10, "ST", 7.20, m=7, team=1), mk(11, "ST", 7.35, m=7, team=2), mk(12, "CM", 7.60, m=3, team=3, age=21),
      mk(13, "CB", 7.10, m=7, team=1), mk(14, "CB", 7.25, m=7, team=2), mk(15, "GK", 7.0, m=7, team=1)]
sf = lambda k: stage[k]
old_mvp, _sc = GE._pick_club_comp_mvp_and_scorer(tp, sf, cnt)
extra = GE._pick_comp_extra_winners(tp, stage_factor=sf, part_denom=cnt)
keep = {k: extra[k] for k in ("assist", "goalkeeper", "best11")}
out = GE._mvp_v11_apply(tp, sf, cnt, "team_id", extra)
check("예전 MVP는 우승팀 선수", old_mvp["player_id"] == 10)
check("V11 MVP는 평점 높은 4강 팀 선수", out[0][0]["player_id"] == 11)
check("3경기(최다 7경기의 43%) 조별 탈락 선수는 출전 분모로 밀린다", out[0][0]["player_id"] != 12)
check("V11 분모 = 최다 경기 7", out[2] == 7)
check("V11 올해의 수비수(4강 팀 CB 평점 0.15 높음 > 단계 차 3%)", extra["defender"]["player_id"] == 14)
check("저장 점수가 V11 점수", close(extra["defender"]["_award_score"], GE._comp_award_score(tp[4], out[1], 7)))
e3 = GE._pick_comp_extra_winners(tp, stage_factor=sf, part_denom=cnt)
check("예전 올해의 수비수는 우승팀 CB(단계 차 10%)", e3["defender"]["player_id"] == 13)
check("도움왕·골든글러브·베스트11 칸은 그대로", all(extra[k] is keep[k] for k in keep))
check("영플레이어 없음 처리(올림픽 young_age_cutoff=None이면 그대로 둠)",
      GE._mvp_v11_apply(tp, sf, cnt, "team_id", {"young": "그대로", "defender": None}, young_age_cutoff=None) is not None)
e2 = {"young": "그대로", "defender": None}
GE._mvp_v11_apply(tp, sf, cnt, "team_id", e2, young_age_cutoff=None)
check("young_age_cutoff=None → 영플레이어 칸 안 건드림", e2["young"] == "그대로")
check("뽑힌 수비수는 사본(풀 원소에 V11 점수를 쓰지 않음)", extra["defender"] is not tp[4])
check("실패하면 None", GE._mvp_v11_apply([{"no_id": 1}], sf, cnt, "team_id", {}) is None)
check("녹아웃 없는 대회(단계 함수 None)", GE._mvp_v11_apply(tp, None, cnt, "team_id")[1] is None)

print("⑦ game_engine._league_mvp_young_v2 (리그)")
lp = [mk(1, "ST", 7.40, g=12, m=36), mk(2, "ST", 7.30, g=28, a=6, m=36), mk(3, "ST", 7.20, g=9, m=36),
      mk(4, "CB", 7.00, m=36, team=2), mk(5, "CB", 6.90, m=36), mk(6, "CB", 6.85, m=36),
      mk(7, "CM", 7.10, a=5, m=36, age=21), mk(8, "CM", 7.00, m=36), mk(9, "CM", 6.95, m=36),
      mk(10, "ST", 7.90, g=15, m=15)]                                     # 15/38경기 → 후보 아님
ranks = {1: (1, 20, 30, 38), 2: (2, 20, 35, 38)}
r = GE._league_mvp_young_v2(lp, 38, "잉글랜드", 1, ranks)
check("50% 미만(15/38경기) 평점 1위는 후보 아님", r[0]["player_id"] != 10)
check("영플레이어 = 23세 이하 후보", r[1]["player_id"] == 7)
check("50% 후보가 없으면 None(예전 선정)", GE._league_mvp_young_v2([mk(1, "ST", 7, m=5)], 38, "잉글랜드", 1, {}) is None)
check("역할 자격 없는 선수(전력외)는 후보 아님",
      GE._league_mvp_young_v2([dict(mk(1, "ST", 9.9, m=38), role="전력외"), mk(2, "ST", 7, m=38)], 38, "잉글랜드", 1,
                              {})[0]["player_id"] == 2)

print("⑧ 내 경력 행(명단 기준)")
me = {"player_id": GE._SIA_MY_PLAYER_ID}
ai = {"player_id": 5}
rows = GE._my_club_list_awards("cl", "UEFA 챔피언스리그", 2003, "UEFA 챔피언스리그", me, {"young": me, "defender": ai})
check("챔스 MVP·영플 이름은 예전 그대로", [x[0] for x in rows] == ["챔피언스리그 시즌MVP", "챔피언스리그 영플레이어"])
check("챔스 설명도 예전 그대로", rows[0][2] == "2003 UEFA 챔피언스리그 MVP")
rows = GE._my_club_list_awards("cwc", "클럽 월드컵", 2003, "클럽 월드컵", ai, {"young": None, "defender": me})
check("클럽 월드컵 수비수", rows == [("클럽 월드컵 올해의 수비수", "클럽 월드컵", "2003 클럽 월드컵 올해의 수비수")])
check("내가 수상자가 아니면 행 없음", GE._my_club_list_awards("cl", "x", 2003, "x", ai, {"young": ai, "defender": None}) == [])
rows = GE._my_club_list_awards("cl", "UEFA 챔피언스리그", 2003, "UEFA 챔피언스리그", ai, {"goalkeeper": dict(me, cs=7)},
                               scorer=dict(me, goals=24))
check("챔스 득점왕·골든글러브(예전 이름·설명)", rows == [("챔피언스리그 득점왕", "UEFA 챔피언스리그", "24골"),
                                               ("챔피언스리그 골든글러브", "UEFA 챔피언스리그", "7 클린시트")])
rows = GE._my_club_list_awards("cwc", "클럽 월드컵", 2007, "클럽 월드컵", ai, {"goalkeeper": dict(ai, cs=5)},
                               scorer=dict(me, goals=14))
check("클럽 월드컵 득점왕만(골든글러브는 AI)", rows == [("클럽 월드컵 득점왕", "클럽 월드컵", "14골")])
check("득점왕 인자 없으면(예전 호출) 득점왕 행 없음",
      GE._my_club_list_awards("cl", "x", 2003, "x", ai, {}) == [])
wc = {"name": "월드컵", "kind": "world"}
rows = GE._my_intl_list_awards(wc, 2002, [ai, me, ai], {"young": me, "defender": None})
check("월드컵 실버볼·영플레이어상", [x[0] for x in rows] == ["실버볼", "영플레이어상"])
eu = {"name": "유로", "kind": "continent"}
rows = GE._my_intl_list_awards(eu, 2004, [ai, ai, me], {"young": None, "defender": me})
check("대륙컵 MVP 3위·올해의 수비수", [x[0] for x in rows] == ["유로 MVP 3위", "유로 올해의 수비수"])

print("\n결과:", "전부 통과" if not FAILS else f"실패 {len(FAILS)}건 — {FAILS}")
sys.exit(1 if FAILS else 0)