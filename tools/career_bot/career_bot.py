"""커리어 봇 — 메인 화면(ui/center_panel)의 오퍼·팝업 처리를 화면 없이 같은 엔진 함수로 재현해 시즌을 진행한다.
게임 코드는 건드리지 않는다. 원본·확정본에 똑같이 적용한다.

사용: PYTHONHASHSEED=0 python3 career_bot.py <코드폴더> <시작월드폴더> <출력폴더> <시드> <시즌수>

재현하는 화면 처리(center_panel 순서 그대로, "1주씩 보기" 진행):
  · 주 진행: 그 주 남은 날을 "휴식" 일정으로 advance_days. 국대 발탁 대기는 헤드리스 러너처럼 전부 거절.
  · 오퍼 구간 진입 판정(_on_advance_finished): 이번 주 구간 ≠ 직전 주 구간이면 '이번 구간 오퍼 표시' 초기화.
  · 팝업(_on_advance_finished_continue 순서): 재계약 → 판매추진 → 임대 후 완전 영입 → 구단 임대 추진 → 자동 오퍼.
      재계약: 협상 없이 수락(_show_renew_dialog의 수락 처리와 같은 갱신 — 그 함수가 쓰는 난수 2회도 같이 소비).
      판매추진·완전 영입·구단 임대: 거절(각 화면이 부르는 엔진 거절 함수).
  · 자동 오퍼(_show_auto_offer): 소속 있음·이번 구간 미표시·입단한 이적 기간 아님·오퍼 ON이면 generate_offers().
      0건이면 표시하지 않음(다음 주에 다시 판정 — 화면과 같음). 고가 매각 제안은 거절(reject_high_value_sale).
      OfferWindow 생성 시 난수(협상 횟수·계약 기간·기간 협상 횟수)를 화면과 같은 순서로 소비하고 상태 저장.
      수락 규칙: 우선순위 1위 오퍼의 팀 평균 OVR ≥ 지금 소속팀이면 그 오퍼로 reserve_join_transfer(화면과 같은 인자),
      아니면 전부 거절(창 닫기 = 저장 상태 지움). 구단이 거절해도 이적 요청은 쓰지 않는다.
  · 무소속 + 프리시즌 입단 기간: generate_offers(force=True) → 1위 오퍼로 join_team("입단") (_do_join·_on_join_done).
기록: 창마다 오퍼 목록·결정·예약 결과, 이적 실행 전후 양 팀 인원, 매주 26명 고정 리그 정원 초과 수.
"""
import sys, os, json, shutil, random, time
CODE, WORLD, OUT, SEED, SEASONS = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
sys.path.insert(0, CODE)
os.makedirs(OUT, exist_ok=True)
os.chdir(OUT)
for f in os.listdir(WORLD):
    if f.startswith("world."):
        shutil.copy(os.path.join(WORLD, f), f.replace("world.", "run."))
import database
database.DB_PATH = os.path.abspath("run.db")
database.init_db()
random.seed(SEED)
import game_engine as ge
import intl_engine
import my_loan as ml
from constants import OFFER_ZONES, DAYS_PER_WEEK, get_country_league_grade, get_league_grade

# 화면의 그날 경기 조회(center_panel._get_match_for_day)를 소스에서 그대로 떼어 쓴다 — 일정 구성이 화면과 같아지게.
import ast as _ast
_cp_src = open(os.path.join(CODE, "ui", "center_panel.py"), encoding="utf-8").read()
for _n in _ast.walk(_ast.parse(_cp_src)):
    if isinstance(_n, _ast.FunctionDef) and _n.name == "_get_match_for_day":
        _fn_src = _ast.get_source_segment(_cp_src, _n)
        break
import textwrap as _tw
_ns = {}
exec(_tw.dedent(_fn_src), _ns)
_get_match_for_day = lambda day, p, st=None, include_played=False: _ns["_get_match_for_day"](None, day, p, st=st, include_played=include_played)
TRAIN_DEFAULTS = ["고강도", "휴식", "고강도", "휴식", "고강도", "휴식", "휴식"]   # center_panel.TRAIN_DEFAULTS(콤보 기본값)


def build_week_sched(day):
    """center_panel._build_week_sched(1주 모드, 콤보 기본값 그대로)."""
    p = ge.get_player() or {}
    st = ge.get_state()
    sched = []
    for i in range(DAYS_PER_WEEK):
        d = day + i
        ttype = TRAIN_DEFAULTS[i]
        mi = _get_match_for_day(d, p, st=st)
        if mi and mi.get("pending"):
            mi = None
        if mi:
            sched.append((d, "경기", mi))
        else:
            prev_mi = _get_match_for_day(d - 1, p, st=st, include_played=True)
            if prev_mi and prev_mi.get("pending"):
                prev_mi = None
            if prev_mi:
                ttype = "휴식"
            else:
                try:
                    _md = ge.my_military_duty(d, p)
                except Exception:
                    _md = None
                if _md:
                    ttype = _md
            sched.append((d, ttype, None))
    return sched


LOG = {"windows": [], "moves": [], "renewals": [], "declined_popups": [], "cap_violations": [], "seasons": [],
       "events": []}


def zone_of(w):
    for s, e in OFFER_ZONES:
        if s <= w <= e:
            return (s, e)
    return None


def team_info(c, tid):
    if not tid:
        return None
    r = c.execute("""SELECT t.id, t.name, l.tier, cn.name country, cn.grade cg,
        (SELECT COUNT(*) FROM ai_players WHERE team_id=t.id) n,
        (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) avg
        FROM teams t JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id WHERE t.id=?""",
                  (tid,)).fetchone()
    if not r:
        return None
    return dict(id=r["id"], name=r["name"], tier=r["tier"], country=r["country"],
                lgrade=get_country_league_grade(r["country"], r["cg"]), n=r["n"], avg=round(r["avg"] or 0, 2))


def fixed26_violations(c):
    from ai_lifecycle import _is_fixed26
    p = ge.get_player() or {}
    mine = p.get("current_team_id") or 0
    out = []
    for r in c.execute("""SELECT t.id, l.tier, cn.name country, cn.grade cg, COUNT(a.id) n FROM teams t
        JOIN leagues l ON l.id=t.league_id JOIN countries cn ON cn.id=l.country_id
        LEFT JOIN ai_players a ON a.team_id=t.id GROUP BY t.id"""):
        if _is_fixed26(get_country_league_grade(r["country"], r["cg"]), r["tier"]):
            n = r["n"] + (1 if r["id"] == mine else 0)
            if n > 26:
                out.append((r["id"], n))
    return out


def offer_window_ctor_random(offers):
    """ui/offer_window.OfferWindow.__init__(restore 없음)의 난수 소비를 같은 순서로 — 협상 횟수 → 계약 기간 → 기간 협상 횟수."""
    neg_used = {i: ge.roll_negotiation_attempts(o.get("join_prob") if o.get("join_prob") is not None
                                                else ge.calc_apply_success_prob(o.get("team_id"))[0])
                for i, o in enumerate(offers)}
    p_now = ge.get_player() or {}
    years = [ge._calc_contract_years(p_now.get("age", 17), o.get("tier", 3), o.get("country")) for o in offers]
    years_used = {i: ge.roll_negotiation_attempts(o.get("join_prob") if o.get("join_prob") is not None
                                                  else ge.calc_apply_success_prob(o.get("team_id"))[0])
                  for i, o in enumerate(offers)}
    return neg_used, years, years_used


def bot_renew(p):
    """center_panel._show_renew_dialog 의 '수락'(협상 없이) — 같은 갱신 + 그 화면이 여는 순간 쓰는 난수 2회."""
    c = database.get_conn()
    row = c.execute("SELECT t.id FROM teams t WHERE t.id=?", (p.get("current_team_id", 0),)).fetchone()
    team_id = row["id"] if row else p.get("current_team_id", 0)
    try:
        _prob, _ = ge.calc_apply_success_prob(team_id)
    except Exception:
        _prob = 0.5
    _early = bool(p.get("_contract_renew_early", 0))
    st = ge.get_state() or {}
    _min_yrs = ge.renewal_min_years(p, st.get("current_year") or 0, st.get("current_week") or 1) if _early else 1
    sal = p.get("_contract_renew_offer", 0)
    yrs = max(_min_yrs, p.get("_contract_renew_years", 0) or 2)
    ge.roll_negotiation_attempts(_prob); ge.roll_negotiation_attempts(_prob)     # sal_used / yrs_used (화면과 같은 소비)
    end = ge.renewal_contract_end(st["current_year"], st["current_week"], yrs)
    _end2 = ge.my_clamp_contract_end(end)
    if _end2 < end:
        yrs = max(1, yrs - (end - _end2)); end = _end2
    ge.update_player(contract_years=yrs, contract_end_year=end, salary=sal, _contract_renew_offer=0,
                     _contract_renew_years=0, _contract_renew_early=0)
    ge.mark_contract_extension(yrs)
    ge.add_log(f"✅ 재계약 완료! {yrs}년 계약  |  연봉 {ge.fmt_money(sal)}", "event")
    LOG["renewals"].append(dict(year=st["current_year"], week=st["current_week"], yrs=yrs, early=_early))


def offer_summary(o):
    return dict(tid=o["team_id"], name=o.get("team_name"), country=o.get("country"), tier=o.get("tier"),
                lgrade=get_league_grade(o.get("country"), o.get("grade")) if o.get("grade") else None,
                grade=o.get("grade"), avg=round(o.get("team_avg_ovr") or 0, 2), role=o.get("role"),
                fee=o.get("transfer_fee", 0), salary=o.get("salary", 0))


def auto_offer(week):
    p = ge.get_player()
    if not p or not p.get("current_team_id"):
        return False
    if ge.joined_this_offer_window():
        return False
    if not p.get("offers_enabled", 1):
        return False
    offers = ge.generate_offers()
    st = ge.get_state()
    rec = dict(year=st["current_year"], week=week, n=len(offers), offers=[offer_summary(o) for o in offers])
    if not offers:
        LOG["windows"].append(rec)
        return False      # 화면: 0건이면 표시 안 함(구간 표시 플래그도 안 켬)
    hv = ge.pick_high_value_sale(offers)
    if hv is not None:
        ge.reject_high_value_sale(hv)
        rec["hv_rejected"] = hv.get("team_id")
    neg_used, years, years_used = offer_window_ctor_random(offers)
    ge.save_pending_offer_state(kind="auto_offer", title="✈ 오퍼", force_select=False, grid=True, apply_slots=0,
                                offers=offers, offer_salaries=[o["salary"] for o in offers], neg_used=neg_used,
                                neg_failed=set(), applied_count=0, offer_years=years, years_used=years_used,
                                years_failed=set(), years_target={i: years[i] for i in range(len(offers))})
    c = database.get_conn()
    me = team_info(c, p.get("current_team_id"))
    top = offers[0]
    _force = os.environ.get("BOT_FORCE_ACCEPT") == "1"        # 동작 확인 전용(수락 경로 시험) — 본 실험에서는 쓰지 않는다
    if _force or (top.get("team_avg_ovr") or 0) >= (me["avg"] if me else 0):
        chosen = dict(top); chosen["salary"] = top["salary"]; chosen["contract_years"] = years[0]
        ge.clear_pending_offer_state()
        res = ge.reserve_join_transfer(chosen["team_id"], chosen["salary"], chosen.get("team_name", ""),
                                       transfer_type="오퍼", offer=chosen)
        rec["decision"] = "accept"; rec["reserve"] = (res is not False)
        rec["refused"] = (res is False)
    else:
        ge.clear_pending_offer_state()
        rec["decision"] = "reject_all"
    rec["me"] = me
    LOG["windows"].append(rec)
    return True


def fa_join():
    p = ge.get_player()
    offers = ge.generate_offers(force=True)
    st = ge.get_state()
    rec = dict(year=st["current_year"], week=st["current_week"], n=len(offers), fa=True,
               offers=[offer_summary(o) for o in offers])
    if offers:
        neg_used, years, years_used = offer_window_ctor_random(offers)
        chosen = dict(offers[0]); chosen["contract_years"] = years[0]
        ge.clear_pending_offer_state()
        ge.join_team(chosen["team_id"], chosen["salary"], transfer_type="입단", offer=chosen)
        rec["decision"] = "join"
    LOG["windows"].append(rec)


def main():
    t0 = time.time()
    shown = False
    st0 = ge.get_state()
    start_season = st0["current_season"]
    prev_week = st0["current_week"]
    c = database.get_conn()
    LOG["start"] = dict(year=st0["current_year"], team=team_info(c, (ge.get_player() or {}).get("current_team_id")))
    last_team = (ge.get_player() or {}).get("current_team_id")
    max_weeks = int(os.environ.get("BOT_MAX_WEEKS", "0") or 0)   # 동작 확인용(0 = 시즌 수로만 끝냄)
    n_weeks = 0
    while True:
        st = ge.get_state()
        if st["current_season"] - start_season >= SEASONS or (max_weeks and n_weeks >= max_weeks):
            break
        n_weeks += 1
        cur_day = st.get("current_day") or ((st["current_week"] - 1) * DAYS_PER_WEEK + 1)
        week_start = ((cur_day - 1) // DAYS_PER_WEEK) * DAYS_PER_WEEK + 1
        sched = [it for it in build_week_sched(week_start) if it[0] >= cur_day]
        before_team = (ge.get_player() or {}).get("current_team_id")
        c = database.get_conn()
        before_info = team_info(c, before_team)
        for _g in range(30):
            ge.advance_days(sched)
            pend = intl_engine.get_pending_choice()
            if not pend:
                break
            for opt in pend.get("options", []):
                intl_engine.decline_national_team(opt["tournament_id"])
            d_now = ge.get_state().get("current_day")
            sched = [it for it in sched if it[0] >= d_now]
            if not sched:
                break
        st2 = ge.get_state()
        new_week = st2["current_week"]
        p2 = ge.get_player() or {}
        c = database.get_conn()
        # 이적 실행 감지
        if p2.get("current_team_id") != last_team:
            LOG["moves"].append(dict(year=st2["current_year"], week=new_week, old=before_info,
                                     old_after=team_info(c, before_team), new=team_info(c, p2.get("current_team_id"))))
            last_team = p2.get("current_team_id")
        # [v2 기록 추가 — 조회만] 이적 실행 뒤 4주 동안 떠난 팀 인원(프리시즌 이적은 4주차 마감 보충이 채운다)
        for _mv in LOG["moves"]:
            if _mv.get("old") and len(_mv.setdefault("old_follow", [])) < 4 and \
                    (st2["current_year"], new_week) != (_mv["year"], _mv["week"]):
                _oi = team_info(c, _mv["old"]["id"])
                _mv["old_follow"].append((st2["current_year"], new_week, _oi["n"] if _oi else None))
        viol = fixed26_violations(c)
        if viol:
            LOG["cap_violations"].append(dict(year=st2["current_year"], week=new_week, teams=viol[:10], n=len(viol)))
        # 구간 진입 판정(1주 묶음 → 직전 주 기준)
        pw = new_week - 1 if new_week > 1 else 52
        if zone_of(new_week) and zone_of(new_week) != zone_of(pw):
            shown = False
        # 팝업 순서
        if p2.get("_contract_renew_offer", 0) > 0:
            bot_renew(p2)
        p2 = ge.get_player() or {}
        if p2.get("sale_push_proposal_json"):
            ge.reject_sale_push_proposal(); LOG["declined_popups"].append(("sale_push", st2["current_year"], new_week))
        p2 = ge.get_player() or {}
        if p2.get("loan_buy_proposal_json"):
            ge.reject_loan_buy_proposal(); LOG["declined_popups"].append(("loan_buy", st2["current_year"], new_week))
        p2 = ge.get_player() or {}
        if p2.get("club_loan_proposal_json"):
            ml.reject_club_loan_proposal(); LOG["declined_popups"].append(("club_loan", st2["current_year"], new_week))
        p2 = ge.get_player() or {}
        if p2.get("current_team_id") and zone_of(new_week) and not shown:
            if auto_offer(new_week):
                shown = True
        elif not p2.get("current_team_id") and ge.preseason_offer_open(new_week) and \
                p2.get("age", 0) >= ge.my_effective_min_join_age(p2):
            fa_join()
        if st2["current_season"] != st["current_season"]:
            pp = ge.get_player() or {}
            LOG["seasons"].append(dict(season=st2["current_season"], year=st2["current_year"], t=round(time.time() - t0),
                                       ovr=pp.get("ovr"), age=pp.get("age"),
                                       team=team_info(c, pp.get("current_team_id")),
                                       military=pp.get("military_status")))
            print(f"[bot] season {st2['current_season']} done {time.time()-t0:.0f}s team="
                  f"{(team_info(c, pp.get('current_team_id')) or {}).get('name')} ovr={pp.get('ovr')}", flush=True)
            json.dump(LOG, open("bot_log.json", "w"), ensure_ascii=False, default=str)
    database.wait_for_pending_flush(timeout=1800)
    database.history_drain()
    database.flush_to_disk(final=True)
    sys.path.insert(0, os.path.join(CODE, "tools"))
    from affiliate_integrity import run_all_checks
    res = run_all_checks(database.get_conn().cursor(), verbose=False)
    LOG["integrity_passed"] = bool(res.get("passed"))
    LOG["elapsed"] = round(time.time() - t0)
    json.dump(LOG, open("bot_log.json", "w"), ensure_ascii=False, default=str)
    print("[bot] done", LOG["elapsed"], "integrity", LOG["integrity_passed"])


main()
