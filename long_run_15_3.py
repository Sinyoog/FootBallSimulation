# -*- coding: utf-8 -*-
"""long_run_15_3.py — 15-3: my_player 장기 이적시장 밸런스 검증.

AI-AI 경제는 감사 대상에서 제외(ai_players에 salary 컬럼 자체가 없고
ai_lifecycle.py가 _calc_salary/estimate_transfer_fee를 호출하지 않음이
확인됨 — 15-3 논의에서 합의). my_player가 실제로 겪는 오퍼/이적료/
판매추진만 다회 시행으로 분포를 뽑는다.

한 "시행"(trial) = 무작위로 뽑은 (리그tier, OVR, 시즌 활약) 조합에서
my_player를 그 상태로 세팅하고 generate_offers()를 1회 호출 + 짧은
주간 판매추진 체크(12주)를 돌린 것. "시즌"을 문자 그대로 52주 연속
플레이하는 게 아니라, 다양한 상태 조합을 폭넓게 샘플링하는 방식 —
분포 검증 목적에는 이쪽이 더 효율적이다(생성 시간도 훨씬 짧음).
"""
import sys, os, random, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
database.init_db()
import game_engine as ge
from database import get_conn

conn = get_conn()
c = conn.cursor()

KOREA_TIERS = [1, 2, 3, 4]


def _pick_team(tier):
    rows = c.execute("""SELECT t.id as id, t.name as name FROM teams t
        JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id
        WHERE cn.name='대한민국' AND l.tier=?""", (tier,)).fetchall()
    return random.choice(rows) if rows else None


def _random_season_profile(total_matches):
    """무작위 시즌 프로필 — 폼 좋음/보통/나쁨/부상/징계 등 골고루 섞이게."""
    kind = random.choices(
        ["good", "avg", "poor", "injury", "discipline"],
        weights=[0.25, 0.30, 0.20, 0.15, 0.10])[0]
    if kind == "good":
        rating = random.uniform(6.8, 7.5)
        apps = round(total_matches * random.uniform(0.80, 1.0))
        injury = susp = 0
        bench = total_matches - apps
        red = 0
    elif kind == "avg":
        rating = random.uniform(6.2, 6.8)
        apps = round(total_matches * random.uniform(0.55, 0.85))
        injury = round((total_matches - apps) * random.uniform(0, 0.3))
        susp = 0
        bench = total_matches - apps - injury
        red = random.choice([0, 0, 1])
    elif kind == "poor":
        rating = random.uniform(5.6, 6.3)
        apps = round(total_matches * random.uniform(0.4, 0.7))
        injury = round((total_matches - apps) * random.uniform(0, 0.2))
        susp = round((total_matches - apps) * random.uniform(0.1, 0.4))
        bench = max(0, total_matches - apps - injury - susp)
        red = random.choice([1, 2, 3])
    elif kind == "injury":
        rating = random.uniform(6.0, 6.8)
        apps = round(total_matches * random.uniform(0.0, 0.4))
        injury = total_matches - apps
        susp = 0
        bench = 0
        red = 0
    else:  # discipline
        rating = random.uniform(5.8, 6.5)
        apps = round(total_matches * random.uniform(0.4, 0.65))
        injury = 0
        susp = round((total_matches - apps) * random.uniform(0.4, 0.8))
        bench = max(0, total_matches - apps - susp)
        red = random.choice([2, 3, 3])
    return dict(rating=rating, apps=apps, injury=injury, susp=susp, bench=bench, red=red, kind=kind)


def run_trials(n_trials, seed=12345):
    random.seed(seed)
    rows = []
    forced_sale_trials = []

    for i in range(n_trials):
        tier = random.choice(KOREA_TIERS)
        team = _pick_team(tier)
        if not team:
            continue
        tid, tname = team["id"], team["name"]
        total_matches = ge._get_season_total_matches(tid)
        prof = _random_season_profile(total_matches)
        ovr = random.randint(45, 92)
        age = random.randint(19, 33)
        cur_year = 2026
        contract_years_left = random.randint(0, 5)

        ge.update_player(current_team_id=tid, ovr=ovr, nationality="대한민국",
                          position=random.choice(["CB", "CM", "ST", "LB", "RW"]),
                          agent_grade=random.choice(["F", "D", "C", "B"]),
                          age=age, popularity=0, fame=0,
                          offers_enabled=1, transfer_requested=0,
                          salary=random.randint(5000, 2000000),
                          contract_end_year=cur_year + contract_years_left,
                          current_year=cur_year, manager_relation=random.randint(20, 80),
                          season_rating_cnt=prof["apps"], season_rating_sum=prof["rating"] * prof["apps"],
                          season_injury_matches_missed=prof["injury"],
                          season_suspension_matches_missed=prof["susp"],
                          season_bench_matches_missed=prof["bench"],
                          season_red_cards_league=prof["red"])

        p = ge.get_player()
        form = ge.calc_season_form_score(p, total_matches=total_matches)["form_score"]

        try:
            offers = ge.generate_offers(force=True)
        except Exception as e:
            print(f"[trial {i}] generate_offers 예외: {e}")
            continue

        for o in offers:
            rows.append({
                "trial": i, "my_tier": tier, "my_ovr": ovr, "form": form, "kind": prof["kind"],
                "buyer_country": o.get("country"), "buyer_tier": o.get("tier"),
                "buyer_avg_ovr": o.get("team_avg_ovr"),
                "ovr_gap": (o.get("team_avg_ovr") or ovr) - ovr,
                "salary_eok": o.get("salary", 0) / 100000,
                "fee_eok": o.get("transfer_fee", 0) / 100000,
            })

        # 강제판매 체크 (12주, 속도 위해 축약)
        c.execute("DELETE FROM promotion_log WHERE team_name=? AND year=?", (tname, cur_year))
        if random.random() < 0.3:  # 30% 확률로 이번 시행은 "강등팀" 시나리오
            c.execute("INSERT INTO promotion_log (year, team_name, from_tier, to_tier, league_name) VALUES (?,?,?,?,?)",
                       (cur_year, tname, tier, tier + 1, "test"))
        conn.commit()
        forced = False
        for week in range(1, 13):
            p = ge.get_player()
            if p.get("current_team_id") != tid:
                forced = True
                break
            ge._weekly_sale_push_check(p, cur_year, week)
            p = ge.get_player()
            ge._check_sale_push_forced_sale(p, cur_year, week)
        if forced:
            p_final = ge.get_player()
            score_final, reasons_final = ge._calc_sale_push_score(p, cur_year)
            new_team_row = c.execute("SELECT name FROM teams WHERE id=?",
                                      (p_final.get("current_team_id"),)).fetchone()
            forced_sale_trials.append({
                "trial": i, "ovr": ovr, "form": form,
                "contract_years_left": contract_years_left, "kind": prof["kind"],
                "salary_eok": p.get("salary", 0) / 100000,
                "sale_score": score_final, "reasons": reasons_final,
                "new_team": new_team_row["name"] if new_team_row else "?",
            })

    return rows, forced_sale_trials


def report(rows, forced, n_trials):
    print(f"\n{'='*70}\n{n_trials}회 시행 결과 (오퍼 {len(rows)}건)\n{'='*70}")

    # ── K4 오퍼 연봉 분포 ──────────────────────────────
    k4 = [r for r in rows if r["buyer_country"] == "대한민국" and r["buyer_tier"] == 4]
    if k4:
        sals = sorted(r["salary_eok"] for r in k4)
        n = len(sals)
        print(f"\n[K4 오퍼 연봉] n={n}  평균={statistics.mean(sals):.2f}억  "
              f"중앙값={sals[n//2]:.2f}억  상위90%={sals[int(n*0.9)] if n>1 else sals[0]:.2f}억  "
              f"최고={max(sals):.2f}억")
    else:
        print("\n[K4 오퍼] 이번 시행에서 K4 오퍼 없음")

    # ── 리그별 오퍼 연봉 ──────────────────────────────
    print("\n[리그(tier)별 오퍼 연봉 분포 — 국내]")
    for tier in KOREA_TIERS:
        t_rows = [r for r in rows if r["buyer_country"] == "대한민국" and r["buyer_tier"] == tier]
        if not t_rows:
            continue
        sals = sorted(r["salary_eok"] for r in t_rows)
        n = len(sals)
        print(f"  K{tier}: n={n}  평균={statistics.mean(sals):.2f}억  중앙값={sals[n//2]:.2f}억  최고={max(sals):.2f}억")

    # ── form별 오퍼 체급(구매팀 OVR갭) ──────────────────────────────
    bad_form = [r for r in rows if r["form"] < 0.45]
    good_form = [r for r in rows if r["form"] >= 0.65]
    print(f"\n[form별 오퍼 체급 — 구매팀avgOVR - 내OVR]")
    if bad_form:
        gaps = [r["ovr_gap"] for r in bad_form]
        print(f"  낮은폼(<0.45) n={len(bad_form)}  평균갭={statistics.mean(gaps):.2f}  최대갭={max(gaps):.1f}")
    if good_form:
        gaps = [r["ovr_gap"] for r in good_form]
        print(f"  높은폼(>=0.65) n={len(good_form)}  평균갭={statistics.mean(gaps):.2f}  최대갭={max(gaps):.1f}")

    # ── bad_form_top_club_offer_rate ──────────────────────────────
    GAP_THRESHOLD = 2.0
    if bad_form:
        rate_bad = sum(1 for r in bad_form if r["ovr_gap"] >= GAP_THRESHOLD) / len(bad_form)
    else:
        rate_bad = None
    if good_form:
        rate_good = sum(1 for r in good_form if r["ovr_gap"] >= GAP_THRESHOLD) / len(good_form)
    else:
        rate_good = None
    print(f"\n[bad_form_top_club_offer_rate] (갭>={GAP_THRESHOLD} 기준)")
    print(f"  낮은폼: {rate_bad*100:.1f}%" if rate_bad is not None else "  낮은폼: 데이터없음",
          f" / 높은폼: {rate_good*100:.1f}%" if rate_good is not None else " / 높은폼: 데이터없음")

    # ── 이적료: OVR 대비 ──────────────────────────────
    fee_rows = [r for r in rows if r["fee_eok"] > 0]
    if fee_rows:
        print(f"\n[이적료] n={len(fee_rows)}  평균={statistics.mean(r['fee_eok'] for r in fee_rows):.2f}억  "
              f"최고={max(r['fee_eok'] for r in fee_rows):.2f}억")
        bad_fee = [r for r in fee_rows if r["form"] < 0.45]
        if bad_fee:
            print(f"  낮은폼 이적료: n={len(bad_fee)}  평균={statistics.mean(r['fee_eok'] for r in bad_fee):.2f}억  "
                  f"최고={max(r['fee_eok'] for r in bad_fee):.2f}억")

    # ── 강제판매 ──────────────────────────────
    print(f"\n[강제판매] {len(forced)}건 / {n_trials}회 시행 ({len(forced)/n_trials*100:.1f}%)")
    if forced:
        long_contract_forced = [f for f in forced if f["contract_years_left"] >= 3]
        print(f"  이 중 장기계약(3년+) 강제판매: {len(long_contract_forced)}건")
        print(f"  강제판매 평균 form: {statistics.mean(f['form'] for f in forced):.3f}")
        print(f"  강제판매 평균 OVR: {statistics.mean(f['ovr'] for f in forced):.1f}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 12345
    rows, forced = run_trials(n, seed=seed)
    report(rows, forced, n)
    