# game_engine.py
import random
import math
from database import get_conn, calc_ovr, ALL_STATS
from constants import *  # PHYSICAL_STATS, TECHNICAL_STATS, MENTAL_STATS 포함

_pending_transfer_type: str = "입단"  # join_team → _save_career_entry 전달용


# ═══════════════════════════════════════════
# 유틸
# ═══════════════════════════════════════════

def fmt_money(amount_man: int) -> str:
    """만원 단위 → 표시 문자열"""
    if amount_man >= 10000:
        return f"{amount_man/10000:.2f}억원"
    return f"{amount_man}만원"


def get_player():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM my_player WHERE id=1")
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def update_player(**kw):
    if not kw:
        return
    conn = get_conn()
    c = conn.cursor()
    sets = ",".join(f"{k}=?" for k in kw)
    c.execute(f"UPDATE my_player SET {sets} WHERE id=1", list(kw.values()))
    conn.commit()
    conn.close()


def get_state():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM season_state WHERE id=1")
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {
        "current_year": GAME_START_YEAR,
        "current_week": 1,
        "current_season": 1,
        "phase": "preseason",
    }


def set_state(**kw):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM season_state WHERE id=1")
    if c.fetchone():
        sets = ",".join(f"{k}=?" for k in kw)
        c.execute(f"UPDATE season_state SET {sets} WHERE id=1", list(kw.values()))
    else:
        keys = ",".join(kw.keys())
        vals = ",".join("?" for _ in kw)
        c.execute(f"INSERT INTO season_state(id,{keys}) VALUES(1,{vals})",
                  list(kw.values()))
    conn.commit()
    conn.close()


def add_log(text: str, log_type="normal", year=None, week=None):
    st = get_state()
    y = year if year is not None else st["current_year"]
    w = week if week is not None else st["current_week"]
    conn = get_conn()
    conn.execute("INSERT INTO game_log(entry,log_type,year,week) VALUES(?,?,?,?)",
                 (text, log_type, y, w))
    conn.commit()
    conn.close()


def get_logs():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT entry FROM game_log ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    return [r["entry"] for r in rows]


def recalc_ovr(p: dict) -> int:
    stats = {s: p.get(s, 40) for s in ALL_STATS}
    return calc_ovr(p.get("position", "CM"), stats)


def _age_train_eff(age: int, peak_age: int) -> float:
    """나이별 훈련 효율 배수.
    성장기(16~peak_age-3): 1.3x  전성기(peak-2~peak+2): 1.0x
    하락기(peak+3~32): 0.75x     유지기(33+): 0.55x
    """
    growth_end = max(18, peak_age - 3)
    prime_end  = peak_age + 2
    if age <= growth_end:
        # 16세=1.45 → growth_end=1.25 선형 감소
        t = (age - 16) / max(1, growth_end - 16)
        return round(1.45 - 0.20 * t, 3)
    elif age <= prime_end:
        return 1.0
    elif age <= 32:
        t = (age - prime_end) / max(1, 32 - prime_end)
        return round(1.0 - 0.25 * t, 3)
    else:
        return 0.55


# ═══════════════════════════════════════════
# 선수 생성
# ═══════════════════════════════════════════

def create_player(name: str, position: str, sub_role: str,
                  nationality: str = None, flag: str = None):
    conn = get_conn()
    c = conn.cursor()

    if not nationality:
        c.execute("SELECT name,flag FROM countries ORDER BY RANDOM() LIMIT 1")
        row = c.fetchone()
        nationality, flag = row["name"], row["flag"]

    height = random.randint(165, 196)
    weight = random.randint(60, 92)
    personality = random.choice(PERSONALITIES)
    peak_age = 23 if random.random() < 0.20 else random.randint(25, 26)

    # 20% 확률로 재능형: 시작 스탯이 높고 max도 높아서 1년 훈련 후 OVR 50 근처 가능
    is_talented = random.random() < 0.20
    if is_talented:
        target = random.randint(46, 52)   # 일반: 38~43 / 재능: 46~52
        dev    = random.randint(6, 10)    # 편차 작게 (고루 높음)
    else:
        target = random.randint(38, 43)
        dev    = random.randint(10, 15)

    stat_vals = {}
    for s in ALL_STATS:
        cur = max(20, min(65, target + random.randint(-dev, dev)))
        # 재능형은 max도 더 높게
        if is_talented:
            mx = max(min(99, cur + random.randint(28, 45)), 75)
        else:
            mx = max(min(99, cur + random.randint(20, 40)), 70)
        stat_vals[s] = cur
        stat_vals[f"{s}_max"] = mx

    ovr = calc_ovr(position, stat_vals)

    conn.execute("""
    INSERT INTO my_player(
        id, name, nationality, flag, age, birth_year,
        position, sub_role, personality, height, weight, peak_age,
        stamina,stamina_max, speed,speed_max, jump,jump_max,
        shooting,shooting_max, passing,passing_max, dribbling,dribbling_max,
        tackling,tackling_max, heading,heading_max, positioning,positioning_max,
        setpiece,setpiece_max, mental,mental_max, confidence,confidence_max,
        leadership,leadership_max, concentration,concentration_max,
        ovr, current_year, current_week, current_season,
        stress, happiness, agent_grade, language
    ) VALUES (
        1,?,?,?,?,?,
        ?,?,?,?,?,?,
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
        ?,?,?,?,
        10,10,'F','ko'
    )""", (
        name, nationality, flag, PLAYER_START_AGE, GAME_START_YEAR,
        position, sub_role, personality, height, weight, peak_age,
        stat_vals["stamina"],    stat_vals["stamina_max"],
        stat_vals["speed"],      stat_vals["speed_max"],
        stat_vals["jump"],       stat_vals["jump_max"],
        stat_vals["shooting"],   stat_vals["shooting_max"],
        stat_vals["passing"],    stat_vals["passing_max"],
        stat_vals["dribbling"],  stat_vals["dribbling_max"],
        stat_vals["tackling"],   stat_vals["tackling_max"],
        stat_vals["heading"],    stat_vals["heading_max"],
        stat_vals["positioning"],stat_vals["positioning_max"],
        stat_vals["setpiece"],   stat_vals["setpiece_max"],
        stat_vals["mental"],     stat_vals["mental_max"],
        stat_vals["confidence"], stat_vals["confidence_max"],
        stat_vals["leadership"], stat_vals["leadership_max"],
        stat_vals["concentration"],stat_vals["concentration_max"],
        ovr, GAME_START_YEAR, 1, 1,
    ))

    # 시즌 상태 초기화
    conn.execute("""INSERT OR REPLACE INTO season_state(id,current_year,current_week,
                    current_season,phase) VALUES(1,?,1,1,'preseason')""",
                 (GAME_START_YEAR,))
    conn.commit()
    conn.close()

    add_log(f"⭐ {GAME_START_YEAR}년  —  {name} {PLAYER_START_AGE}세", "event")
    add_log("─"*44, "sep")


# ═══════════════════════════════════════════
# 4주 진행
# ═══════════════════════════════════════════

def _find_open_entry(c, tid, team_name):
    """열린 커리어 항목(end_year=0) 조회.
    team_id 우선 매칭, 구버전 세이브(team_id=0) 행은 팀명으로 폴백.
    (동명 팀이 여러 나라에 존재하므로 이름 단독 매칭은 금지)"""
    row = c.execute(
        """SELECT id FROM career_entries
           WHERE team_id=? AND end_year=0 ORDER BY id DESC LIMIT 1""",
        (tid,)).fetchone()
    if row:
        return row
    return c.execute(
        """SELECT id FROM career_entries
           WHERE team_id=0 AND team_name=? AND end_year=0
           ORDER BY id DESC LIMIT 1""",
        (team_name,)).fetchone()


def _calc_clean_sheets(c, tid, season):
    """해당 시즌 소속 팀의 클린시트(무실점 경기) 수 집계."""
    row = c.execute("SELECT league_id FROM teams WHERE id=?", (tid,)).fetchone()
    if not row:
        return 0
    q = c.execute(
        """SELECT COUNT(*) as cnt FROM match_results
           WHERE league_id=? AND season=? AND home_score>=0
           AND ((home_team_id=? AND away_score=0)
             OR (away_team_id=? AND home_score=0))""",
        (row["league_id"], season, tid, tid)).fetchone()
    return q["cnt"] if q else 0


def _update_career_stats(p, year, week):
    """열린 커리어 항목의 스탯만 갱신. end_year는 건드리지 않음."""
    tid = p.get("current_team_id", 0)
    if not tid: return
    conn = get_conn()
    c = conn.cursor()
    team_row = c.execute("""SELECT t.name, l.name as lname, l.tier
                             FROM teams t JOIN leagues l ON t.league_id=l.id
                             WHERE t.id=?""", (tid,)).fetchone()
    if not team_row:
        conn.close(); return
    existing = _find_open_entry(c, tid, team_row["name"])
    if not existing:
        conn.close(); return

    rank_str = get_team_rank(tid)
    try: rn = int(rank_str.split("위")[0].replace("공동","").strip())
    except: rn = 0

    sm  = p.get("season_matches", 0)
    sg  = p.get("season_goals", 0)
    sa  = p.get("season_assists", 0)
    ss  = p.get("season_saves", 0)
    sga = p.get("season_goals_against", 0)
    rc  = p.get("season_rating_cnt", 0)
    rs  = p.get("season_rating_sum", 0.0)
    avg_r = round(rs/rc, 2) if rc else 0.0

    season = p.get("current_season", 1)
    lid_row = c.execute("SELECT league_id FROM teams WHERE id=?", (tid,)).fetchone()
    tw = td = tl = 0
    if lid_row:
        lid = lid_row["league_id"]
        c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                     FROM match_results WHERE league_id=? AND season=? AND home_score>=0""",
                  (lid, season))
        for row in c.fetchall():
            hid, aid, hs, as_ = row["home_team_id"], row["away_team_id"], row["home_score"], row["away_score"]
            if hid == tid:
                if hs > as_: tw += 1
                elif hs == as_: td += 1
                else: tl += 1
            elif aid == tid:
                if as_ > hs: tw += 1
                elif as_ == hs: td += 1
                else: tl += 1

    cs = _calc_clean_sheets(c, tid, season)

    c.execute("""UPDATE career_entries SET
        matches=?, goals=?, assists=?, saves=?, goals_against=?,
        avg_rating=?, team_rank=?, wins=?, draws=?, losses=?, clean_sheets=?,
        team_id=?
        WHERE id=?""",
        (sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs, tid, existing["id"]))
    conn.commit()
    conn.close()


def _close_career_entry(p, year, week):
    """현재 팀의 열린 커리어 항목(end_year=0)을 닫음. 연도별 분리용."""
    tid = p.get("current_team_id", 0)
    if not tid: return

    conn = get_conn()
    c = conn.cursor()

    team_row = c.execute("""SELECT t.name, l.name as lname, l.tier
                             FROM teams t JOIN leagues l ON t.league_id=l.id
                             WHERE t.id=?""", (tid,)).fetchone()
    if not team_row:
        conn.close(); return

    existing = _find_open_entry(c, tid, team_row["name"])
    if not existing:
        conn.close(); return

    rank_str = get_team_rank(tid)
    try:
        rn = int(rank_str.split("위")[0].replace("공동","").strip())
    except:
        rn = 0

    sm  = p.get("season_matches", 0)
    sg  = p.get("season_goals", 0)
    sa  = p.get("season_assists", 0)
    ss  = p.get("season_saves", 0)
    sga = p.get("season_goals_against", 0)
    rc  = p.get("season_rating_cnt", 0)
    rs  = p.get("season_rating_sum", 0.0)
    avg_r = round(rs/rc, 2) if rc else 0.0

    # 팀 전적 match_results 기반
    season = p.get("current_season", 1)
    lid_row = c.execute("SELECT league_id FROM teams WHERE id=?", (tid,)).fetchone()
    tw = td = tl = 0
    if lid_row:
        lid = lid_row["league_id"]
        c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                     FROM match_results WHERE league_id=? AND season=? AND home_score>=0""",
                  (lid, season))
        for row in c.fetchall():
            hid, aid, hs, as_ = row["home_team_id"], row["away_team_id"], row["home_score"], row["away_score"]
            if hid == tid:
                if hs > as_: tw += 1
                elif hs == as_: td += 1
                else: tl += 1
            elif aid == tid:
                if as_ > hs: tw += 1
                elif as_ == hs: td += 1
                else: tl += 1

    cs = _calc_clean_sheets(c, tid, season)

    c.execute("""UPDATE career_entries SET
        end_year=?, end_week=?, matches=?, goals=?, assists=?, saves=?, goals_against=?,
        avg_rating=?, team_rank=?, wins=?, draws=?, losses=?, clean_sheets=?,
        league_name=?, tier=?, salary=?, position=?, team_id=?
        WHERE id=?""",
        (year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs,
         team_row["lname"], team_row["tier"], p.get("salary", 0),
         p.get("position", ""), tid, existing["id"]))

    conn.commit()
    conn.close()


def _ensure_career_entry(p, st):
    """팀이 있는데 열린 커리어 항목(end_year=0)이 없으면 지금 생성."""
    tid = p.get("current_team_id", 0)
    if not tid: return

    conn = get_conn()
    c = conn.cursor()

    # 팀명 조회
    team_row = c.execute("""SELECT t.name, l.id as lid, l.name as lname, l.tier
                             FROM teams t JOIN leagues l ON t.league_id=l.id
                             WHERE t.id=?""", (tid,)).fetchone()
    if not team_row:
        conn.close(); return

    # 이미 열린 항목 있으면 스킵
    existing = _find_open_entry(c, tid, team_row["name"])
    if existing:
        conn.close(); return

    # 없으면 현재 주차 기준으로 생성
    c_yrs_e  = p.get("contract_years", 0)
    tt_e     = _pending_transfer_type
    tier_e   = p.get("current_tier") or team_row["tier"]
    c.execute("""INSERT INTO career_entries
        (age, position, team_name, league_name, tier, salary,
         start_year, start_week, end_year, end_week,
         matches, goals, assists, avg_rating, team_rank, wins, draws, losses,
         contract_years, transfer_type, team_id)
        VALUES (?,?,?,?,?,?,?,?,0,0,0,0,0,0,0,0,0,0,?,?,?)""",
        (p["age"], p.get("position",""), team_row["name"], team_row["lname"],
         tier_e, p.get("salary",0),
         st["current_year"], st["current_week"],
         c_yrs_e, tt_e, tid))
    conn.commit()
    conn.close()


def advance_4weeks(schedule: list):
    p = get_player()
    if not p: return

    st = get_state()
    base_week = st["current_week"]

    # 팀이 있는데 열린 커리어 항목이 없으면 지금 생성
    if p.get("current_team_id"):
        _ensure_career_entry(p, st)

    for (week, stype, detail) in schedule:
        p = get_player()
        import intl_engine

        # 부상 중
        if p.get("injured"):
            _process_injury_week(p, week)
            if stype == "경기" and not (isinstance(detail, dict) and detail.get("intl")):
                _sim_my_team_match_as_ai(week, p, st["current_season"])
            else:
                # 훈련 주차인데 내 팀 경기가 실제로 있으면 AI로 처리
                _sim_my_unscheduled_match(week, p, st["current_season"])
            # 내 국가대표 경기는 process_intl_week가 결장 처리로 시뮬
        elif stype == "경기":
            if isinstance(detail, dict) and detail.get("intl"):
                intl_engine.simulate_my_match(week, p)
            else:
                _simulate_match(p, week, detail)
        else:
            # 훈련 주차여도 토너먼트 진출로 동적으로 생긴 국대 경기가 있으면 우선
            im = intl_engine.get_my_match(week)
            if im:
                intl_engine.simulate_my_match(week, p)
            else:
                _process_training(p, week, stype, detail)
                # 훈련으로 잡혔지만 실제 내 팀 경기가 있는 경우 AI로 처리
                _sim_my_unscheduled_match(week, p, st["current_season"])

        # 이 주차의 국제대회 AI 경기 + 라운드 진행 처리
        intl_engine.process_intl_week(week)

        # 이 주차의 다른 모든 리그 AI 경기 자동 처리
        _sim_all_ai_matches(week, p.get("current_league_id", 0), st["current_season"])

        p = get_player()

    p = get_player()
    _pay_salary(p, base_week)
    _advance_week(p, base_week)

    # 진행 중 커리어 행 실시간 갱신 (열린 항목이 없으면 내부에서 무시됨)
    p_fin = get_player()
    if p_fin and p_fin.get("current_team_id"):
        st_fin = get_state()
        _update_career_stats(p_fin, st_fin["current_year"], st_fin["current_week"])


def _sim_my_team_match_as_ai(week, p, season):
    """부상/결장 시 내 팀 경기를 AI끼리 시뮬레이션해서 팀 전적에 반영."""
    my_tid = p.get("current_team_id", 0)
    if not my_tid:
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT mr.id, mr.home_team_id, mr.away_team_id
                 FROM match_results mr
                 WHERE mr.week=? AND mr.season=? AND mr.home_score=-1
                   AND (mr.home_team_id=? OR mr.away_team_id=?)
                 LIMIT 1""", (week, season, my_tid, my_tid))
    m = c.fetchone()
    if m:
        hid, aid = m["home_team_id"], m["away_team_id"]
        ho = _team_avg_ovr(c, hid) + 3
        ao = _team_avg_ovr(c, aid)
        diff = ho - ao
        hw = max(0.10, min(0.80, 0.45 + diff * 0.01))
        dw = 0.25
        roll = random.random()
        if roll < hw:        outcome = "home"
        elif roll < hw+dw:   outcome = "draw"
        else:                outcome = "away"
        hs, as_ = _gen_score(outcome)
        _update_team_rec(c, hid, aid, outcome, hs, as_)
        c.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                  (hs, as_, m["id"]))
        conn.commit()
    conn.close()


def _sim_my_unscheduled_match(week: int, p, season: int):
    """훈련 주차지만 실제 DB에 내 팀 경기가 있는 경우 AI로 처리."""
    tid = p.get("current_team_id", 0)
    if not tid: return
    lid = p.get("current_league_id", 0)
    if not lid: return
    conn = get_conn()
    row = conn.execute(
        """SELECT id, home_team_id, away_team_id FROM match_results
           WHERE league_id=? AND season=? AND week=? AND home_score=-1
           AND (home_team_id=? OR away_team_id=?)""",
        (lid, season, week, tid, tid)).fetchone()
    if row:
        c = conn.cursor()
        ho = _team_avg_ovr(c, row["home_team_id"]) + 3
        ao = _team_avg_ovr(c, row["away_team_id"])
        diff = ho - ao
        hw = max(0.10, min(0.80, 0.45 + diff * 0.01))
        dw = 0.25
        roll = random.random()
        if roll < hw:         outcome = "home"
        elif roll < hw + dw:  outcome = "draw"
        else:                 outcome = "away"
        hs, as_ = _gen_score(outcome)
        _update_team_rec(c, row["home_team_id"], row["away_team_id"], outcome, hs, as_)
        conn.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                     (hs, as_, row["id"]))
        conn.commit()
    conn.close()


def _sim_all_ai_matches(week, my_league_id, season):
    """모든 리그 이번 주차 미완료 경기 AI 처리 (내 팀 경기 제외)"""
    conn = get_conn()
    c = conn.cursor()

    # 내 팀 ID 조회
    p_row = conn.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
    my_tid = p_row["current_team_id"] if p_row else 0

    c.execute("""SELECT mr.id, mr.home_team_id, mr.away_team_id, mr.league_id
                 FROM match_results mr
                 WHERE mr.week=? AND mr.home_score=-1 AND mr.season=?""",
              (week, season))
    matches = c.fetchall()

    # 비시즌 여부 확인: 1~4주, 12~25주는 비시즌 → 내 팀 경기도 AI 처리
    is_offseason = (1 <= week <= 4) or (12 <= week <= 25)

    for m in matches:
        is_my_match = (m["home_team_id"] == my_tid or m["away_team_id"] == my_tid)
        if is_my_match and not is_offseason:
            # 시즌 중 내 팀 경기는 _simulate_match에서 처리됨 → 건너뜀
            continue
        # 비시즌이거나 내 팀 미포함 경기 → AI로 처리
        ho = _team_avg_ovr(c, m["home_team_id"]) + 3
        ao = _team_avg_ovr(c, m["away_team_id"])
        diff = ho - ao
        hw = max(0.10, min(0.80, 0.45 + diff * 0.01))
        dw = 0.25
        roll = random.random()
        if roll < hw:          outcome = "home"
        elif roll < hw + dw:   outcome = "draw"
        else:                  outcome = "away"
        hs, as_ = _gen_score(outcome)
        _update_team_rec(c, m["home_team_id"], m["away_team_id"], outcome, hs, as_)
        c.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                  (hs, as_, m["id"]))

    conn.commit()
    conn.close()


# ─────────────────────────────────────────
# 훈련
# ─────────────────────────────────────────

def _process_training(p, week, ttype, focus_stat=None):
    cfg  = TRAINING_CONFIG[ttype]
    pers = p.get("personality","성실함")
    pe   = PERSONALITY_EFFECTS.get(pers, {})
    eff  = pe.get("train_eff", 1.0)

    # 나이별 효율 배수
    age      = p.get("age", 20)
    peak_age = p.get("peak_age", 25)
    eff *= _age_train_eff(age, peak_age)

    # 슬럼프 패널티
    if p.get("slump"):
        eff *= SLUMP_TRAIN_PENALTY

    stress_chg = cfg["stress"]
    # 냉철함 / 훈련광 등 stress_mult 적용
    if "stress_mult" in pe:
        stress_chg = int(stress_chg * pe["stress_mult"])

    happy_chg = 0
    stat_changes = {}

    if ttype == "휴식":
        # 신체/기술 스탯 중 랜덤 1개 소폭 하락 (멘탈 스탯은 휴식으로 안 떨어짐)
        _phy_pool  = [s for s in PHYSICAL_STATS
                      if s in FOCUS_TRAIN_STATS.get(p["position"], PHYSICAL_STATS)]
        _tech_pool = [s for s in TECHNICAL_STATS
                      if s in FOCUS_TRAIN_STATS.get(p["position"], TECHNICAL_STATS)]
        _rest_pool = (_phy_pool or PHYSICAL_STATS) + (_tech_pool or TECHNICAL_STATS)
        stat = random.choice(_rest_pool)
        cur = p.get(stat, 40)
        if cur > 20:
            stat_changes[stat] = -1
        happy_chg = random.randint(4, 8)
        log_parts = [f"😴 휴식  {week}주차  스트레스 {stress_chg:+d}  행복 {happy_chg:+d}"]
        if stat_changes:
            for s, v in stat_changes.items():
                log_parts.append(f"   {STAT_KO.get(s,s)} {v:+d}")

    else:
        # 부상 체크
        inj_chance = cfg["injury_chance"]
        inj_add = PERSONALITY_EFFECTS.get(pers,{}).get("injury_add", 0)
        inj_chance = max(0, inj_chance + inj_add)
        if p.get("stress", 0) >= 100:
            inj_chance = 1.0

        if random.random() < inj_chance:
            _apply_injury(p, week)
            return

        # ── 훈련 스탯 상승 ──────────────────────────────────
        # 집중훈련: 지정 스탯 1개
        if ttype == "집중훈련" and focus_stat:
            targets = [focus_stat]
        else:
            pos = p["position"]
            # 포지션별 신체/기술 pool 분리
            focus = FOCUS_TRAIN_STATS.get(pos, PHYSICAL_STATS + TECHNICAL_STATS)
            phy_pool  = [s for s in PHYSICAL_STATS  if s in focus]
            tech_pool = [s for s in TECHNICAL_STATS if s in focus]
            if not phy_pool:  phy_pool  = list(PHYSICAL_STATS)
            if not tech_pool: tech_pool = list(TECHNICAL_STATS)

            # pool 내 스탯이 모두 max에 도달했으면 전체 스탯으로 확장
            def _below_max(stats_list):
                return [s for s in stats_list if p.get(s,40) < p.get(f"{s}_max",80)]
            phy_below  = _below_max(phy_pool)
            tech_below = _below_max(tech_pool)
            # pool 내 남은 스탯 없으면 전체에서 max 미달 스탯으로 확장
            if not phy_below:
                phy_below = _below_max(PHYSICAL_STATS) or phy_pool
            if not tech_below:
                tech_below = _below_max(TECHNICAL_STATS) or tech_pool
            phy_pool  = phy_below
            tech_pool = tech_below

            # 훈련 강도별 상승 스탯 수: 2개 or 3개
            # 2개 → 신체1 + 기술1
            # 3개 → 신체1 + 기술2 (기술 중 추가 1개 랜덤)
            if ttype == "고강도":
                cnt = random.choices([2, 3], weights=[60, 40])[0]
            elif ttype == "중강도":
                cnt = random.choices([2, 3], weights=[70, 30])[0]
            else:  # 저강도
                cnt = 2

            phy_pick  = random.sample(phy_pool, min(1, len(phy_pool)))

            # 우선순위 기술 스탯: PRIORITY_TECH_STATS에 있으면 70% 확률로 먼저 선택
            prio = [s for s in PRIORITY_TECH_STATS.get(pos, []) if s in tech_pool]
            rest = [s for s in tech_pool if s not in prio]

            def _pick_tech(n):
                picks = []
                pool_p = list(prio); pool_r = list(rest)
                for _ in range(n):
                    if pool_p and (not pool_r or random.random() < 0.70):
                        s = random.choice(pool_p); pool_p.remove(s)
                    elif pool_r:
                        s = random.choice(pool_r); pool_r.remove(s)
                    elif pool_p:
                        s = random.choice(pool_p); pool_p.remove(s)
                    else:
                        break
                    picks.append(s)
                return picks

            if cnt == 2:
                tech_pick = _pick_tech(1)
            else:
                tech_pick = _pick_tech(2)
            targets = phy_pick + tech_pick

        for stat in targets:
            g_min, g_max = cfg["gain_min"], cfg["gain_max"]
            raw = random.randint(g_min, g_max) * eff
            if raw < 1.0:
                gain = 1 if random.random() < raw else 0
            else:
                gain = int(raw)
            cur = p.get(stat, 40)
            mx  = p.get(f"{stat}_max", 80)
            if cfg.get("exceed_limit"):
                new_val = min(100, cur + gain)
            elif ttype == "집중훈련" and cur >= mx:
                new_mx = min(99, mx + 1)
                if new_mx > mx:
                    stat_changes[f"{stat}_max_up"] = (stat, new_mx)
                new_val = cur
            else:
                new_val = min(mx, cur + gain)
            if new_val > cur:
                stat_changes[stat] = new_val - cur

        label = f"[{ttype}]"
        log_parts = [f"🏃 {label}  {week}주차"]
        max_ups      = {k: v for k, v in stat_changes.items() if k.endswith("_max_up")}
        real_changes = {k: v for k, v in stat_changes.items() if not k.endswith("_max_up")}
        if real_changes or max_ups:
            for s, v in real_changes.items():
                log_parts.append(f"   {STAT_KO.get(s,s)} {v:+d}")
            for _, (stat, new_mx) in max_ups.items():
                log_parts.append(f"   {STAT_KO.get(stat,stat)} 잠재력↑ (최대 {new_mx})")
        else:
            log_parts.append("   (변화 없음)")

    # 업데이트
    new_stress  = max(0, min(100, p["stress"] + stress_chg))
    new_happy   = max(0, min(100, p["happiness"] + happy_chg))
    updates = dict(stress=new_stress, happiness=new_happy)
    max_ups   = {k: v for k, v in stat_changes.items() if k.endswith("_max_up")}
    real_changes = {k: v for k, v in stat_changes.items() if not k.endswith("_max_up")}
    for s, delta in real_changes.items():
        updates[s] = p.get(s, 40) + delta
    for _, (stat, new_mx) in max_ups.items():
        updates[f"{stat}_max"] = new_mx

    # 슬럼프 체크
    slump = p.get("slump", 0)
    if not slump:
        # 강철멘탈: 슬럼프 완전 면역
        if pe.get("no_slump"):
            pass
        else:
            threshold = SLUMP_STRESS_THRESHOLD
            # 유리멘탈: 발동 임계치를 낮춤
            threshold -= pe.get("slump_threshold_reduce", 0)

            if new_stress >= threshold and ttype != "휴식":
                chance = SLUMP_CHANCE
                if "slump_chance_mult" in pe:
                    chance *= pe["slump_chance_mult"]
                # 유리멘탈: 60 이상 구간에선 확률 추가
                if pe.get("slump_chance_add") and new_stress >= SLUMP_STRESS_THRESHOLD:
                    chance += pe["slump_chance_add"]
                chance = min(1.0, chance)
                if random.random() < chance:
                    slump = 1
                    add_log(f"😰 슬럼프 발생!  {week}주차", "slump")
            if new_happy <= SLUMP_HAPPY_THRESHOLD:
                slump = 1
                add_log(f"😰 행복도 저하로 슬럼프!  {week}주차", "slump")
    else:
        if new_stress <= SLUMP_RECOVER_STRESS:
            slump = 0
            add_log(f"😊 슬럼프 해소!  {week}주차", "slump")

    updates["slump"] = slump
    updates["ovr"]   = calc_ovr(p["position"], {s: updates.get(s, p.get(s,40))
                                                  for s in ALL_STATS})
    update_player(**updates)
    for line in log_parts:
        add_log(line, "training")


def _apply_injury(p, week):
    roll = random.random()
    if roll < 0.6:
        itype = "경미"
    elif roll < 0.9:
        itype = "중간"
    else:
        itype = "심각"
    wmin, wmax = INJURY_TYPES[itype]
    weeks = random.randint(wmin, wmax)
    update_player(injured=1, injury_weeks=weeks, injury_type=itype,
                  happiness=max(0, p["happiness"] - 20))  # 이슈9: 부상 -20
    add_log(f"🚑 {itype} 부상!  {week}주차  ({weeks}주 휴식 필요)", "injury")


def _process_injury_week(p, week):
    left = p["injury_weeks"] - 1
    if left <= 0:
        update_player(injured=0, injury_weeks=0, injury_type="")
        add_log(f"✅ 부상 회복!  {week}주차", "injury")
    else:
        update_player(injury_weeks=left)
        add_log(f"🚑 부상 휴식  {week}주차  ({left}주 남음)", "injury")


# ─────────────────────────────────────────
# 경기 시뮬레이션
# ─────────────────────────────────────────

def _simulate_match(p, week, info: dict):
    conn = get_conn()
    c = conn.cursor()

    home_id  = info["home_id"]
    away_id  = info["away_id"]
    my_tid   = p.get("current_team_id", 0)
    is_home  = info["is_home"]

    home_ovr = _team_avg_ovr(c, home_id)
    away_ovr = _team_avg_ovr(c, away_id)

    my_ovr  = p.get("ovr", 40)
    benched = _check_bench(p)
    played  = not benched and not p.get("injured")

    if played:
        bonus = my_ovr * 0.08
        if is_home: home_ovr += bonus
        else:       away_ovr += bonus

    home_ovr += 3
    diff = home_ovr - away_ovr
    hw = max(0.10, min(0.80, 0.45 + diff * 0.01))
    dw = 0.25
    aw = max(0.05, 1.0 - hw - dw)

    roll = random.random()
    if roll < hw:         outcome = "home"
    elif roll < hw + dw:  outcome = "draw"
    else:                 outcome = "away"

    hs, as_ = _gen_score(outcome)

    goals = assists = saves = 0
    rating = 0.0
    events = []
    if played:
        goals, assists, saves, rating, events = _player_perf(p, outcome, is_home, hs, as_)
        if p.get("slump"):
            rating = max(3.0, rating + SLUMP_RATING_PENALTY)

    my_result = _my_result(outcome, is_home)

    # 팀 전적 업데이트 (같은 conn 내에서)
    _update_team_rec(c, home_id, away_id, outcome, hs, as_)

    # 경기 결과 저장
    c.execute("""UPDATE match_results SET home_score=?,away_score=?
                 WHERE league_id=? AND week=? AND home_team_id=? AND away_team_id=?
                 AND season=?""",
              (hs, as_, info.get("league_id",0), week,
               home_id, away_id, info.get("season",1)))
    conn.commit()
    conn.close()  # ← 여기서 먼저 닫고 아래에서 update_player 호출

    # 내 시즌 통계 (conn 닫힌 후)
    if played:
        _ga = (as_ if info.get("is_home") else hs) if p.get("position") == "GK" else 0
        update_player(
            total_matches=p["total_matches"]+1,
            total_goals=p["total_goals"]+goals,
            total_assists=p["total_assists"]+assists,
            total_saves=p.get("total_saves",0)+saves,
            total_goals_against=p.get("total_goals_against",0)+_ga,
            season_matches=p.get("season_matches",0)+1,
            season_goals=p.get("season_goals",0)+goals,
            season_assists=p.get("season_assists",0)+assists,
            season_saves=p.get("season_saves",0)+saves,
            season_rating_sum=p.get("season_rating_sum",0)+rating,
            season_rating_cnt=p.get("season_rating_cnt",0)+1,
            season_goals_against=p.get("season_goals_against",0)+_ga,
        )

    _update_manager_rel(p, rating, my_result, played)
    _update_pop(p, goals, assists, rating)

    p2 = get_player()
    match_stress = 5 if info.get("is_home") else 8
    ns = min(100, p2["stress"] + match_stress)
    nh = p2["happiness"]
    if my_result == "win":    nh = min(100, nh+3)
    elif my_result == "loss": nh = max(0,   nh-3)
    # 이슈9: 슬럼프 진행 중 매 경기마다 행복도 -15
    if p2.get("slump"):
        nh = max(0, nh - 15)

    mental_updates = {}
    if played:
        # 경기 출전 → 정신 스탯 랜덤 1~2개 소폭 상승
        n_up = random.choices([1, 2], weights=[70, 30])[0]
        for ms in random.sample(MENTAL_STATS, n_up):
            cur = p2.get(ms, 40)
            mx  = p2.get(f"{ms}_max", 80)
            if cur < mx:
                mental_updates[ms] = min(mx, cur + 1)
    else:
        # 경기 불참(벤치/부상) → 정신 스탯 랜덤 1개 소폭 하락
        ms = random.choice(MENTAL_STATS)
        cur = p2.get(ms, 40)
        if cur > 20:
            mental_updates[ms] = cur - 1
        add_log(f"⚠ 경기 불참  {week}주차  {STAT_KO.get(ms,ms)} -1", "training")

    update_player(stress=ns, happiness=nh, **mental_updates)

    _write_match_log(p, week, info["league_name"], is_home,
                     home_id, away_id, hs, as_,
                     my_result, goals, assists, saves, rating, events, played, benched)


def _team_avg_ovr(c, team_id):
    c.execute("SELECT AVG(ovr) as v FROM ai_players WHERE team_id=?", (team_id,))
    row = c.fetchone()
    return row["v"] if row and row["v"] else 45


def _check_bench(p):
    rel = p.get("manager_relation", 50)
    for threshold, prob in BENCH_BY_RELATION:
        if rel <= threshold:
            return random.random() < prob
    return False


def _gen_score(outcome):
    if outcome == "home":
        h = random.choices([1,2,3,4], weights=[25,35,25,15])[0]
        a = random.choices([0,1,2],   weights=[45,35,20])[0]
        a = min(a, h-1)
    elif outcome == "away":
        a = random.choices([1,2,3,4], weights=[25,35,25,15])[0]
        h = random.choices([0,1,2],   weights=[45,35,20])[0]
        h = min(h, a-1)
    else:
        g = random.choices([0,1,2,3], weights=[20,35,30,15])[0]
        h, a = g, g
    return max(0,h), max(0,a)


def _my_result(outcome, is_home):
    if outcome == "draw":
        return "draw"
    return "win" if (outcome=="home")==is_home else "loss"


def _player_perf(p, outcome, is_home, hs, as_):
    pos     = p["position"]
    pers    = p.get("personality","성실함")
    base    = 6.0
    goals   = assists = saves = 0
    events  = []

    my_score  = hs if is_home else as_
    opp_score = as_ if is_home else hs

    if pos == "GK":
        # ── 티어+OVR 기반 선방률 산출 ──────────────────────────
        # 현재 리그 티어 조회 (p에 current_league_id 있으면 사용)
        _tier = 3
        _lid  = p.get("current_league_id", 0)
        if _lid:
            try:
                _conn2 = get_conn()
                _row2  = _conn2.execute("SELECT tier FROM leagues WHERE id=?", (_lid,)).fetchone()
                _conn2.close()
                if _row2:
                    _tier = _row2["tier"]
            except Exception:
                pass

        # 티어별 선방률 범위 (하위권/평균/상위권)
        # 1부: 58~80%, 2부: 55~75%, 3부: 50~72%
        _gk_ovr = p.get("ovr", 50)
        if _tier == 1:
            _sr_min, _sr_max = 0.58, 0.80
        elif _tier == 2:
            _sr_min, _sr_max = 0.55, 0.75
        else:
            _sr_min, _sr_max = 0.50, 0.72

        # OVR → 선방률 범위 내 위치 (OVR 40=하위 10%, 99=상위 10%)
        _ovr_t = max(0.0, min(1.0, (_gk_ovr - 40) / 60))
        _sr_center = _sr_min + (_sr_max - _sr_min) * _ovr_t
        # 경기마다 ±4% 랜덤 변동
        _sr_target = max(_sr_min, min(_sr_max, _sr_center + random.uniform(-0.04, 0.04)))

        # 유효슈팅 수: 티어 높을수록 슈팅 많음 (1부 5~9, 2부 4~8, 3부 3~7)
        if _tier == 1:
            total_shots = opp_score + random.randint(4, 8)
        elif _tier == 2:
            total_shots = opp_score + random.randint(3, 7)
        else:
            total_shots = opp_score + random.randint(2, 6)
        total_shots = max(opp_score + 1, total_shots)  # 최소 실점+1

        # 목표 선방률로 선방 수 역산
        saves = max(0, min(total_shots - opp_score,
                           round(total_shots * _sr_target - opp_score * (1 - _sr_target))))
        saves = max(0, saves)

        rate = (saves + opp_score) and saves / total_shots or 0
        if rate >= 0.75: base += 1.0; events.append("🧤 훌륭한 선방!")
        elif rate >= 0.65: base += 0.4
        if opp_score == 0: base += 0.5; events.append("🧱 클린시트!")
        elif opp_score >= 3: base -= 1.0; events.append("😞 다실점...")
    else:
        # 골
        gprob = {"ST":0.35,"CF":0.30,"LW":0.25,"RW":0.25,"CAM":0.20,"CM":0.12}.get(pos, 0.05)
        if random.random() < gprob:
            goals = random.choices([1,2],[75,25])[0]
            base += goals * 1.0
            events.append(f"⚽ {'골!' if goals==1 else '멀티골!'}")

        # 어시
        if goals == 0 and random.random() < 0.22:
            assists = 1; base += 0.5; events.append("🎯 어시스트!")

        # 긍정 이벤트
        pos_ev = _pos_events(pos, True)
        if random.random() < 0.40:
            ev = random.choice(pos_ev)
            base += 0.3; events.append(f"✅ {ev}")

        # 부정 이벤트
        neg_ev = _pos_events(pos, False)
        if random.random() < 0.30:
            ev = random.choice(neg_ev)
            base -= 0.3; events.append(f"😞 {ev}")

        # 레드카드
        rc = PERSONALITY_EFFECTS.get(pers,{}).get("red_card_chance", 0)
        if random.random() < rc:
            events.append("🟥 레드카드! 1경기 정지")
            base -= 2.0
            p2 = get_player()
            update_player(manager_relation=max(0,p2.get("manager_relation",50)-5))

        # 승부욕 보정
        if pers == "승부욕" and _my_result(outcome, is_home) == "loss":
            base += 0.3
        if pers == "소심함" and (my_score + (0 if is_home else 0)) < opp_score:
            base -= 0.3

    base = round(max(3.0, min(10.0, base + random.uniform(-0.4,0.4))), 1)
    return goals, assists, saves, base, events


def _pos_events(pos, positive):
    POS = {
        "GK":  (["선방 성공!","공중볼 장악","킥 정확"],["포지셔닝 실수로 실점","막지 못했다"]),
        "CB":  (["태클 성공!","헤딩 클리어","인터셉트"],["마킹 실수","태클 미스"]),
        "LB":  (["오버랩 성공","크로스 연결","태클 성공"],["역습 허용","마킹 실수"]),
        "RB":  (["오버랩 성공","크로스 연결","태클 성공"],["역습 허용","마킹 실수"]),
        "CDM": (["공 차단!","패스 연결","포지셔닝"],["패스 미스","포지셔닝 실수"]),
        "CM":  (["키패스 성공","드리블 돌파","공간 침투"],["턴오버","패스 미스"]),
        "CAM": (["창의적 패스","드리블 돌파 성공!","공간 침투"],["찬스 창출 실패","결정력 부족"]),
        "LW":  (["드리블 돌파 성공!","크로스 연결","속도 돌파"],["드리블 실패","크로스 미스"]),
        "RW":  (["드리블 돌파 성공!","크로스 연결","속도 돌파"],["드리블 실패","크로스 미스"]),
        "CF":  (["공간 침투 성공!","연결 플레이","키패스"],["빅찬스 미스","오프사이드"]),
        "ST":  (["공간 침투 성공!","포스트 플레이","슈팅 시도"],["빅찬스 미스!","결정력 부족"]),
    }
    pair = POS.get(pos,(["좋은 플레이"],["실수"]))
    return pair[0] if positive else pair[1]


def _update_team_rec(c, hid, aid, outcome, hs, as_):
    if outcome == "home":
        c.execute("UPDATE teams SET wins=wins+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (hs,as_,hid))
        c.execute("UPDATE teams SET losses=losses+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (as_,hs,aid))
    elif outcome == "away":
        c.execute("UPDATE teams SET losses=losses+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (hs,as_,hid))
        c.execute("UPDATE teams SET wins=wins+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (as_,hs,aid))
    else:
        c.execute("UPDATE teams SET draws=draws+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (hs,as_,hid))
        c.execute("UPDATE teams SET draws=draws+1,goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?", (as_,hs,aid))


def _update_manager_rel(p, rating, result, played):
    rel = p.get("manager_relation",50)
    if not played:
        rel = max(0, rel-1)
    else:
        if rating >= 7.0: rel = min(100, rel+3)
        elif rating >= 6.0: rel = min(100, rel+1)
        elif rating < 5.0: rel = max(0, rel-3)
        if result == "win": rel = min(100, rel+1)
        elif result == "loss": rel = max(0, rel-1)
        if p.get("injured"): rel = max(0, rel-2)
    update_player(manager_relation=rel)


def _update_pop(p, goals, assists, rating):
    pop = p.get("popularity",0)
    if goals > 0: pop = min(100, pop + goals*2)
    if assists > 0: pop = min(100, pop+1)
    if rating < 5.0: pop = max(0, pop-1)
    update_player(popularity=pop)


def _write_match_log(p, week, league_name, is_home,
                     hid, aid, hs, as_,
                     result, goals, assists, saves, rating, events, played, benched):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT name FROM teams WHERE id=?", (hid,))
    hn = (c.fetchone() or {"name":"홈팀"})["name"]
    c.execute("SELECT name FROM teams WHERE id=?", (aid,))
    an = (c.fetchone() or {"name":"원정팀"})["name"]
    conn.close()

    loc = "홈" if is_home else "원정"
    rs  = {"win":"승","draw":"무","loss":"패"}.get(result,"")

    add_log("─"*44, "sep")
    add_log(f"⚽ 경기  [{league_name}]  {week}주차  ({loc})", "match")
    add_log(f"   {hn} {hs}-{as_} {an}  ({rs})", "match")

    if not played:
        add_log("   🪑 벤치 대기" if benched else "   🚑 부상 결장", "match")
    else:
        if p["position"] == "GK":
            add_log(f"   평점 {rating}  선방 {saves}", "match")
        else:
            add_log(f"   평점 {rating}  골 {goals}  어시 {assists}", "match")

        mid = len(events)//2
        fh = events[:mid+1]
        sh = events[mid+1:]

        add_log("   [전반]", "match")
        for ev in fh:
            m = random.randint(1,45)
            add_log(f"   {m}'  {ev}", "match")
        add_log("   [후반]", "match")
        for ev in sh:
            m = random.randint(46,90)
            add_log(f"   {m}'  {ev}", "match")

        # 총평
        labels = [(9,"완벽한 경기"),(8,"훌륭한 경기"),(7,"좋은 경기"),
                  (6,"준수한 경기"),(5,"평범한 경기"),(4,"부진한 경기"),(0,"최악의 경기")]
        total = next(l for t,l in labels if rating >= t)
        add_log(f"   😞 경기 총평: {total}", "match")

    rank_str = get_team_rank(p.get("current_team_id",0))
    add_log(f"   📊 리그 순위: {rank_str}", "match")


# ─────────────────────────────────────────
# 순위
# ─────────────────────────────────────────

def get_my_promotions():
    """내가 실제 재직한 기간의 승강 기록 조회 (커리어 창 / 은퇴 창 공용).
    연말(52주)까지 재직한 해의 연말 승강은 포함, 시즌 중 떠난 해의 연말 승강은 제외."""
    conn = get_conn(); c = conn.cursor()
    entries = c.execute(
        "SELECT team_name, start_year, end_year, end_week FROM career_entries ORDER BY id"
    ).fetchall()
    conds, params = [], []
    for e in entries:
        tn, sy = e["team_name"], e["start_year"]
        ey, ew = e["end_year"], e["end_week"]
        if ey == 0:
            conds.append("(team_name=? AND year>=?)"); params.extend([tn, sy])
        elif ew >= 52:
            conds.append("(team_name=? AND year>=? AND year<=?)"); params.extend([tn, sy, ey])
        else:
            conds.append("(team_name=? AND year>=? AND year<?)"); params.extend([tn, sy, ey])
    promos = []
    if conds:
        rows = c.execute(
            f"SELECT * FROM promotion_log WHERE {' OR '.join(conds)} ORDER BY id",
            params).fetchall()
        seen = set()
        for r in rows:
            key = (r["year"], r["team_name"], r["from_tier"], r["to_tier"])
            if key not in seen:
                seen.add(key)
                promos.append(dict(r))
    conn.close()
    return promos


def get_team_rank(team_id) -> str:
    if not team_id:
        return "정보 없음"
    rows = get_league_standings_by_team(team_id)
    if not rows:
        return "정보 없음"
    for i, r in enumerate(rows):
        if r["id"] == team_id:
            rank = i + 1
            if i > 0 and rows[i-1]["pts"] == r["pts"] and rows[i-1]["gd"] == r["gd"]:
                rank_str = f"공동 {rank}위"
            else:
                rank_str = f"{rank}위"
            return f"{rank_str}  ({r['wins']}승 {r['draws']}무 {r['losses']}패 / 승점 {r['pts']}점)"
    return "정보 없음"


def get_league_standings_by_team(team_id):
    """팀 ID로 해당 리그 순위표 반환 (match_results 기준)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT league_id FROM teams WHERE id=?", (team_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return []
    return get_league_standings(row["league_id"])


def get_league_standings(league_id):
    """순위표: match_results에서 직접 집계해서 항상 정확한 값 반환."""
    conn = get_conn()
    c = conn.cursor()

    st = get_state()
    season = st["current_season"] if st else 1

    c.execute("SELECT id, name FROM teams WHERE league_id=?", (league_id,))
    teams = {r["id"]: {"id": r["id"], "name": r["name"],
                       "wins":0,"draws":0,"losses":0,
                       "goals_for":0,"goals_against":0} for r in c.fetchall()}

    c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                 FROM match_results
                 WHERE league_id=? AND season=? AND home_score>=0""",
              (league_id, season))
    for row in c.fetchall():
        hid, aid, hs, as_ = (row["home_team_id"], row["away_team_id"],
                              row["home_score"], row["away_score"])
        for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
            if tid not in teams: continue
            teams[tid]["goals_for"]     += gf
            teams[tid]["goals_against"] += ga
            if gf > ga:    teams[tid]["wins"]   += 1
            elif gf == ga: teams[tid]["draws"]  += 1
            else:          teams[tid]["losses"] += 1

    conn.close()

    rows = list(teams.values())
    for r in rows:
        r["pts"] = r["wins"] * 3 + r["draws"]
        r["gd"]  = r["goals_for"] - r["goals_against"]

    rows.sort(key=lambda r: (-r["pts"], -r["gd"], -r["goals_for"]))
    return rows


# ─────────────────────────────────────────
# 경기 일정 생성
# ─────────────────────────────────────────

def generate_season_schedule(league_id, season, year, force=False):
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM teams WHERE league_id=? LIMIT 8", (league_id,))
    tids = [r["id"] for r in c.fetchall()]
    if len(tids) < 2:
        conn.close(); return

    # 이미 완료된 경기 주차
    c.execute("""SELECT week FROM match_results
                 WHERE league_id=? AND season=? AND home_score >= 0""", (league_id, season))
    played_weeks = {r["week"] for r in c.fetchall()}

    # 이미 예정된 경기 (홈/원정 양방향 모두 체크해서 중복 삽입 방지)
    c.execute("""SELECT week, home_team_id, away_team_id FROM match_results
                 WHERE league_id=? AND season=?""", (league_id, season))
    existing_matches = set()
    for r in c.fetchall():
        w, h, a = r["week"], r["home_team_id"], r["away_team_id"]
        existing_matches.add((w, h, a))
        existing_matches.add((w, a, h))  # 역방향도 등록해서 중복 방지

    # 내 팀이 리그에 있는데 아직 예정된 경기가 없는 주차 확인
    p_row = conn.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
    my_tid = p_row["current_team_id"] if p_row else 0

    for rd, matches in enumerate(ROUND_MATCHES):
        week = FIRST_HALF_START + rd
        if week in played_weeks: continue
        for hi, ai in matches:
            if hi >= len(tids) or ai >= len(tids): continue
            # 홈/원정 랜덤 배정
            t1, t2 = (tids[hi], tids[ai]) if random.random() < 0.5 else (tids[ai], tids[hi])
            key  = (week, t1, t2)
            rkey = (week, t2, t1)
            if key in existing_matches or rkey in existing_matches: continue
            c.execute("""INSERT INTO match_results
                         (league_id,week,home_team_id,away_team_id,
                          home_score,away_score,season,year)
                         VALUES(?,?,?,?,-1,-1,?,?)""",
                      (league_id, week, t1, t2, season, year))
            existing_matches.add(key)
            existing_matches.add(rkey)

    for rd, matches in enumerate(ROUND_MATCHES):
        week = SECOND_HALF_START + rd
        if week in played_weeks: continue
        for hi, ai in matches:
            if hi >= len(tids) or ai >= len(tids): continue
            # 하반기는 상반기 반대 (홈↔원정) - 하지만 상반기 기록이 없으면 랜덤
            # 상반기에 t1이 홈이었다면 하반기엔 t2가 홈
            first_half_week = FIRST_HALF_START + rd
            flip_key1 = (first_half_week, tids[hi], tids[ai])
            flip_key2 = (first_half_week, tids[ai], tids[hi])
            if flip_key1 in existing_matches:
                t1, t2 = tids[ai], tids[hi]  # 반전
            elif flip_key2 in existing_matches:
                t1, t2 = tids[hi], tids[ai]  # 반전
            else:
                t1, t2 = (tids[ai], tids[hi]) if random.random() < 0.5 else (tids[hi], tids[ai])
            key  = (week, t1, t2)
            rkey = (week, t2, t1)
            if key in existing_matches or rkey in existing_matches: continue
            c.execute("""INSERT INTO match_results
                         (league_id,week,home_team_id,away_team_id,
                          home_score,away_score,season,year)
                         VALUES(?,?,?,?,-1,-1,?,?)""",
                      (league_id, week, t1, t2, season, year))
            existing_matches.add(key)
            existing_matches.add(rkey)

    conn.commit()
    conn.close()


def _generate_adjacent_schedules(my_lid, season, year):
    """내 리그 + 같은 국가 위아래 1티어 리그 일정을 함께 생성.
    승강 처리 시 인접 리그 순위가 필요하므로 반드시 함께 생성해야 함."""
    generate_season_schedule(my_lid, season, year)
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT country_id, tier FROM leagues WHERE id=?", (my_lid,)).fetchone()
    if row:
        cid, tier = row["country_id"], row["tier"]
        for adj_tier in [tier - 1, tier + 1]:
            if adj_tier < 1: continue
            adj = c.execute(
                "SELECT id FROM leagues WHERE country_id=? AND tier=?",
                (cid, adj_tier)).fetchone()
            if adj:
                generate_season_schedule(adj["id"], season, year)
    conn.close()


def get_schedule(league_id, season):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT mr.*, ht.name as home_name, at.name as away_name
                 FROM match_results mr
                 JOIN teams ht ON mr.home_team_id=ht.id
                 JOIN teams at ON mr.away_team_id=at.id
                 WHERE mr.league_id=? AND mr.season=?
                 ORDER BY mr.week""", (league_id, season))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ─────────────────────────────────────────
# 월급
# ─────────────────────────────────────────

def _pay_salary(p, week):
    salary = p.get("salary",0)
    if salary <= 0: return
    monthly = salary // 12
    fee = AGENT_FEE_RATE.get(p.get("agent_grade","F"), 0)
    net = int(monthly * (1-fee))
    assets   = p.get("total_assets",   0) + net
    earnings = p.get("total_earnings", 0) + net  # 이슈10: 누적 수입
    update_player(total_assets=assets, total_earnings=earnings)
    add_log(f"💰 월급 수령  +{fmt_money(net)}  (총자산: {fmt_money(assets)})", "salary")


# ─────────────────────────────────────────
# 주차 전진
# ─────────────────────────────────────────

def _advance_week(p, base_week):
    new_week = base_week + 4
    new_year = p["current_year"]
    new_season = p["current_season"]

    if new_week > 52:
        new_week -= 52
        new_year += 1
        new_season += 1
        # 연도 넘어갈 때 현재 팀 커리어 항목 닫기 (연도별 분리)
        if p.get("current_team_id"):
            _close_career_entry(p, new_year - 1, 52)
        _end_of_season(p, new_year-1)
    else:
        # 하반기 종료(33~36주차) → 37~40주차: 커리어 스탯 중간 업데이트만
        # (항목은 닫지 않음 - 연도 변경 시 _close_career_entry가 닫음)
        if base_week <= 36 and new_week >= 37:
            if p.get("current_team_id") and p.get("season_matches", 0) > 0:
                _update_career_stats(p, new_year, new_week)

    update_player(current_year=new_year, current_week=new_week,
                  current_season=new_season)
    set_state(current_year=new_year, current_week=new_week,
              current_season=new_season)

    # 1주차: 새 시즌 시작 시 이전 연도 오퍼 거절 기록 삭제
    if new_week == 1:
        conn_cl = get_conn()
        st_yr = conn_cl.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()
        if st_yr:
            conn_cl.execute("DELETE FROM offer_refused WHERE year<?", (st_yr["current_year"],))
            conn_cl.commit()
        conn_cl.close()

    # 13주차 진입 시 상반기 평점 스냅샷 저장 (13~24주 오퍼 확률용)
    if new_week == 13:
        p_snap = get_player()
        if p_snap:
            rc_s = p_snap.get("season_rating_cnt", 0)
            rs_s = p_snap.get("season_rating_sum", 0.0)
            update_player(first_half_rating=round(rs_s/rc_s, 2) if rc_s else 0.0)

    # 17주차 진입: 국제대회 윈도우 시작 (월드컵/대륙컵 해당 연도면 생성)
    from constants import INTL_CALLUP_WEEK
    if new_week == INTL_CALLUP_WEEK:
        try:
            import intl_engine
            intl_engine.start_intl_tournament(new_year)
        except Exception as e:
            add_log(f"⚠ 국제대회 생성 오류: {e}", "event")

    # 새 시즌 시작 시 내 리그 + 인접 리그(위아래 1티어) 일정 생성
    if new_week <= 4 and p.get("current_team_id"):
        p_fresh = get_player()
        if p_fresh and p_fresh.get("current_league_id"):
            _generate_adjacent_schedules(
                p_fresh["current_league_id"], new_season, new_year)


def _end_of_season(p, year):
    # 커리어 기록은 37~40주차 진입 시 이미 저장됨 → 여기선 생략

    # 시즌 평점 스냅샷 (아래 4단계에서 통계가 리셋되므로 미리 계산)
    # → 8단계 계약 만료 체크에서 사용
    _rc0 = p.get("season_rating_cnt", 0)
    _rs0 = p.get("season_rating_sum", 0.0)
    season_avg_rating = round(_rs0/_rc0, 2) if _rc0 else 6.0

    # 2. 자연 성장 (10경기 이상)
    if p.get("season_matches",0) >= 10:
        base_pool = FOCUS_TRAIN_STATS.get(p["position"], ALL_STATS[:5])
        BASIC_STATS = ["stamina", "mental", "concentration"]
        pool = list(dict.fromkeys(base_pool + BASIC_STATS))
        stat = random.choice(pool)
        cur  = p.get(stat,40)
        mx   = p.get(f"{stat}_max",80)
        bonus = 1
        if "natural_growth_bonus" in PERSONALITY_EFFECTS.get(p.get("personality",""),{}):
            if random.random() < PERSONALITY_EFFECTS[p["personality"]]["natural_growth_bonus"]:
                bonus = 2
        if cur < mx:
            update_player(**{stat: min(mx, cur+bonus)})
            add_log(f"🌱 시즌 자연 성장: {STAT_KO.get(stat,stat)}+{bonus}", "event", year, 52)

    # 3. 나이 증가 + 스탯 노화
    new_age = p["age"] + 1
    decay   = 0
    for threshold, d in AGING:
        if new_age >= threshold:
            decay = d

    stat_updates: dict = {"age": new_age, "total_seasons": p.get("total_seasons",0)+1}

    if decay > 0:
        for stat in ALL_STATS:
            mk = f"{stat}_max"
            old_mx = p.get(mk,80)
            new_mx = max(20, old_mx - random.randint(0, decay))
            stat_updates[mk] = new_mx
            if p.get(stat,40) > new_mx:
                stat_updates[stat] = new_mx

    # 4. 시즌 통계 초기화
    stat_updates.update(season_matches=0, season_goals=0, season_assists=0,
                        season_saves=0, season_rating_sum=0, season_rating_cnt=0,
                        season_goals_against=0)
    update_player(**stat_updates)

    # 이슈9: 시즌 종료 시 순위 기반 행복도 변화
    _apply_rank_happiness(p, year)

    # 5. 강제 방출 체크 (이슈8 강화)
    _check_forced_release(p, year)

    # 6. 승강제 (스탯 리셋 후): 먼저 미완료 AI 경기 전부 처리
    _sim_all_leagues_for_season_end(p.get("current_season", 1))
    _process_promotion_relegation(year)

    # 7. (구) 연말 국제대회 일괄 시뮬 → 시즌 중 17~24주 실경기 방식으로 대체됨 (intl_engine)

    # 8. 계약 만료 체크
    p2 = get_player()
    if p2 and p2.get("current_team_id"):
        end_yr  = p2.get("contract_end_year", 0)
        if end_yr and year >= end_yr:
            # 팀의 재계약 의사 결정
            # (season_rating_*는 4단계에서 이미 리셋됨 → 리셋 전 스냅샷 사용)
            avg_r = season_avg_rating
            rel   = p2.get("manager_relation", 50)
            wants_renew = (avg_r >= 6.5 or rel >= 60)
            if wants_renew:
                # 재계약 의사 있음 → UI 팝업용 플래그 저장
                base_sal = p2.get("salary", 0)
                if avg_r >= 7.0:   new_sal = int(base_sal * 1.15)
                elif avg_r >= 6.5: new_sal = int(base_sal * 1.05)
                else:              new_sal = int(base_sal * 0.95)
                update_player(_contract_renew_offer=new_sal)
                add_log(f"📋 계약 만료! 팀에서 재계약을 제안합니다. (제시 연봉: {fmt_money(new_sal)})", "event", year, 52)
            else:
                # 재계약 거절 → 소속 없음
                # (연말 항목은 이미 닫혔으므로 allow_insert=False로 중복 방지)
                _save_career_entry(p2, year, 52, transfer_type="방출",
                                   allow_insert=False)
                update_player(current_team_id=0, current_league_id=0,
                              salary=0, contract_years=0, contract_end_year=0,
                              _contract_renew_offer=0)
                add_log(f"📋 계약 만료. 팀에서 재계약을 원하지 않습니다.", "event", year, 52)

    if new_age >= MAX_AGE:
        add_log(f"⭐ {new_age}세. 선수 생활을 마감합니다.", "event", year, 52)


def _apply_rank_happiness(p, year):
    """이슈9: 시즌 종료 시 리그 순위에 따른 행복도 변화."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return
    rank_str = get_team_rank(tid)
    try:
        rn = int(rank_str.split("위")[0].replace("공동", "").strip())
    except Exception:
        return

    cur_happy = get_player().get("happiness", 50)
    delta = 0
    msg   = ""
    if rn == 1:
        delta, msg = +30, "🏆 리그 1위! 행복도 +30"
    elif rn == 2:
        delta, msg = +15, "🥈 리그 2위! 행복도 +15"
    elif rn == 3:
        delta, msg = +10, "🥉 리그 3위! 행복도 +10"
    elif rn == 7:
        delta, msg = -30, "😞 리그 7위... 행복도 -30"
    elif rn == 8:
        delta, msg = -50, "😭 리그 8위(강등권)! 행복도 -50"

    if delta != 0:
        new_happy = max(0, min(100, cur_happy + delta))
        update_player(happiness=new_happy)
        add_log(f"   {msg}", "event", year, 52)


def _check_forced_release(p, year):
    """이슈8: 방출 조건 강화 - 감독 관계 악화 OR 성적 부진 + 평점 저조."""
    rel  = p.get("manager_relation", 50)
    tid  = p.get("current_team_id", 0)
    if not tid:
        return

    rank_str = get_team_rank(tid)
    try:
        rn = int(rank_str.split("위")[0].replace("공동", "").strip())
    except Exception:
        rn = 0

    rc = p.get("season_rating_cnt",0); rs = p.get("season_rating_sum",0.0)
    avg_rating = round(rs/rc,2) if rc > 0 else 6.0
    cond_ext   = (avg_rating < 5.5 and rc >= 5) or (rel < 20)
    cond_combo = avg_rating < 6.0 and rel < 40 and rc >= 5
    if cond_ext or cond_combo:
        reason = "감독 관계 악화" if rel < 20 else ("저조한 평점" if avg_rating < 5.5 else "성적 부진")
        # 연말 항목은 _close_career_entry로 이미 닫힘 → allow_insert=False로 중복 방지
        _save_career_entry(p, year, 52, transfer_type="방출", allow_insert=False)
        add_log(f"😡 {reason}으로 방출!  {year}년  (평점 {avg_rating}, 감독관계 {rel})", "event", year, 52)
        update_player(current_team_id=0, current_league_id=0,
                      salary=0, manager_relation=50,
                      contract_years=0, contract_end_year=0)


def _process_promotion_relegation(year):
    conn = get_conn()
    c = conn.cursor()

    p_row = conn.execute("SELECT current_team_id, current_league_id FROM my_player WHERE id=1").fetchone()
    my_team_id   = p_row["current_team_id"]   if p_row else 0
    my_league_id = p_row["current_league_id"] if p_row else 0

    # 현재 시즌 번호
    ss_row = conn.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    season = ss_row["current_season"] if ss_row else 1

    c.execute("SELECT DISTINCT country_id FROM leagues WHERE tier=1")
    cids = [r["country_id"] for r in c.fetchall()]

    pending_logs  = []
    my_new_league = None

    # 1부 리그 우승 기록 (승강과 무관하게 1위 팀)
    for cid in cids:
        c.execute("SELECT id FROM leagues WHERE country_id=? AND tier=1", (cid,))
        top_lid_row = c.fetchone()
        if not top_lid_row:
            continue
        top_lid = top_lid_row["id"]

        def _league_standings_1(lid, _conn=conn, _season=season):
            cx = _conn.cursor()
            cx.execute("SELECT id, name FROM teams WHERE league_id=?", (lid,))
            teams = {r["id"]: {"id":r["id"],"name":r["name"],"pts":0,"gd":0,"gf":0}
                     for r in cx.fetchall()}
            if not teams: return []
            cx2 = _conn.cursor()
            cx2.execute("""SELECT home_team_id,away_team_id,home_score,away_score
                           FROM match_results WHERE league_id=? AND season=? AND home_score>=0""",
                        (lid, _season))
            for row in cx2.fetchall():
                hid,aid,hs,as_ = (row["home_team_id"],row["away_team_id"],
                                  row["home_score"],row["away_score"])
                for tid,gf,ga in [(hid,hs,as_),(aid,as_,hs)]:
                    if tid not in teams: continue
                    teams[tid]["gf"] += gf
                    teams[tid]["gd"] += gf - ga
                    if gf > ga:    teams[tid]["pts"] += 3
                    elif gf == ga: teams[tid]["pts"] += 1
            return sorted(teams.values(), key=lambda x: (-x["pts"],-x["gd"],-x["gf"]))

        top1_rows = _league_standings_1(top_lid)
        if top1_rows and top1_rows[0]["pts"] > 0 and top1_rows[0]["id"] == my_team_id:
            ci = conn.cursor()
            ci.execute("SELECT t.name, l.name as lname FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?", (my_team_id,))
            winner_info = ci.fetchone()
            if winner_info:
                # 이슈7: 이미 같은 연도·같은 리그 우승 기록이 없을 때만 삽입
                existing_champ = c.execute(
                    "SELECT id FROM trophy_log WHERE year=? AND team_name=? AND tier=1",
                    (year, winner_info["name"])).fetchone()
                if not existing_champ:
                    c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                              (year, winner_info["name"], winner_info["lname"], 1,
                               f"{winner_info['lname']} 우승 (1부 리그 챔피언)"))
                    pending_logs.append((f"🏆 {year}년  {winner_info['name']}  1부 리그 우승!", "event"))

    moved_teams: set = set()  # 이번 시즌 이미 이동한 팀 ID

    for cid in cids:
        for tier in [1, 2]:
            ntier = tier + 1

            # 상위 리그 ID
            c.execute("SELECT id FROM leagues WHERE country_id=? AND tier=?", (cid, tier))
            upper_lid_row = c.fetchone()
            c.execute("SELECT id FROM leagues WHERE country_id=? AND tier=?", (cid, ntier))
            lower_lid_row = c.fetchone()
            if not upper_lid_row or not lower_lid_row:
                continue

            upper_lid = upper_lid_row["id"]
            lower_lid = lower_lid_row["id"]

            # match_results 기반 순위 계산 - 별도 커서 사용
            # 이 리그에서 실제 경기한 팀만 포함 (방금 승강으로 이동해온 팀이
            # 승점 0 꼴찌로 잡혀 2↔3부 교환이 스킵되는 버그 방지)
            def _league_standings(lid, _conn=conn, _season=season):
                cx = _conn.cursor()
                cx.execute("SELECT id, name FROM teams WHERE league_id=?", (lid,))
                teams = {r["id"]: {"id":r["id"],"name":r["name"],"pts":0,"gd":0,"gf":0,"gp":0}
                         for r in cx.fetchall()}
                if not teams: return []
                cx2 = _conn.cursor()
                cx2.execute("""SELECT home_team_id,away_team_id,home_score,away_score
                               FROM match_results WHERE league_id=? AND season=? AND home_score>=0""",
                            (lid, _season))
                for row in cx2.fetchall():
                    hid,aid,hs,as_ = (row["home_team_id"],row["away_team_id"],
                                      row["home_score"],row["away_score"])
                    for tid,gf,ga in [(hid,hs,as_),(aid,as_,hs)]:
                        if tid not in teams: continue
                        teams[tid]["gp"] += 1
                        teams[tid]["gf"] += gf
                        teams[tid]["gd"] += gf - ga
                        if gf > ga:    teams[tid]["pts"] += 3
                        elif gf == ga: teams[tid]["pts"] += 1
                rows = [t for t in teams.values() if t["gp"] > 0]
                return sorted(rows, key=lambda x: (-x["pts"],-x["gd"],-x["gf"]))

            upper_rows = _league_standings(upper_lid)
            lower_rows = _league_standings(lower_lid)

            if not upper_rows or not lower_rows:
                continue
            # 경기 한 번도 안 한 리그 건너뜀: match_results 기준으로 실제 경기 수 확인
            c.execute("""SELECT COUNT(*) as cnt FROM match_results
                         WHERE league_id=? AND season=? AND home_score>=0""",
                      (upper_lid, season))
            if c.fetchone()["cnt"] == 0:
                continue
            c.execute("""SELECT COUNT(*) as cnt FROM match_results
                         WHERE league_id=? AND season=? AND home_score>=0""",
                      (lower_lid, season))
            if c.fetchone()["cnt"] == 0:
                continue

            bottom_upper = upper_rows[-1]  # 상위 리그 꼴찌
            top_lower    = lower_rows[0]   # 하위 리그 1위

            # 이번 시즌 이미 이동한 팀이면 skip (double relegation 방지)
            if bottom_upper["id"] in moved_teams or top_lower["id"] in moved_teams:
                continue

            # 팀 정보 조회 (별도 커서)
            ci = conn.cursor()
            ci.execute("SELECT t.name, l.name as lname FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?", (top_lower["id"],))
            tl_info = ci.fetchone()
            ci.execute("SELECT t.name, l.name as lname FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?", (bottom_upper["id"],))
            bu_info = ci.fetchone()
            if not tl_info or not bu_info: continue

            # 승격: top_lower → upper
            c.execute("UPDATE teams SET league_id=?,current_tier=? WHERE id=?",
                      (upper_lid, tier, top_lower["id"]))
            c.execute("INSERT INTO promotion_log(year,team_name,from_tier,to_tier,league_name) VALUES(?,?,?,?,?)",
                      (year, tl_info["name"], ntier, tier, tl_info["lname"]))
            if top_lower["id"] == my_team_id or my_league_id in (upper_lid, lower_lid):
                pending_logs.append((f"🔼 {year}년  {tl_info['name']}  {ntier}부→{tier}부  (승격)", "event"))
            if top_lower["id"] == my_team_id:
                my_new_league = upper_lid
                # 승격 = 하위 리그 우승 → trophy_log에 리그 우승으로 기록
                c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                          (year, tl_info["name"], tl_info["lname"], ntier,
                           f"{tl_info['lname']} 우승 ({ntier}부 1위 → {tier}부 승격)"))

            # 강등: bottom_upper → lower
            c.execute("UPDATE teams SET league_id=?,current_tier=? WHERE id=?",
                      (lower_lid, ntier, bottom_upper["id"]))
            c.execute("INSERT INTO promotion_log(year,team_name,from_tier,to_tier,league_name) VALUES(?,?,?,?,?)",
                      (year, bu_info["name"], tier, ntier, bu_info["lname"]))
            if bottom_upper["id"] == my_team_id or my_league_id in (upper_lid, lower_lid):
                pending_logs.append((f"🔽 {year}년  {bu_info['name']}  {tier}부→{ntier}부  (강등)", "event"))
            if bottom_upper["id"] == my_team_id:
                my_new_league = lower_lid
                # 강등은 promotion_log에만 기록 (trophy_log 제거)
            moved_teams.add(bottom_upper["id"])
            moved_teams.add(top_lower["id"])

        # teams 전적 초기화
        c.execute("""UPDATE teams SET wins=0,draws=0,losses=0,goals_for=0,goals_against=0
                     WHERE league_id IN (SELECT id FROM leagues WHERE country_id=?)""", (cid,))

    conn.commit()
    conn.close()

    if my_new_league:
        p_up = get_player()
        if p_up:
            old_sal = p_up.get("salary", 0)
            # 승격: +20%, 강등: -20%
            # my_new_league의 tier와 현재 tier 비교
            conn_t = get_conn()
            new_tier_row = conn_t.execute("SELECT tier FROM leagues WHERE id=?", (my_new_league,)).fetchone()
            old_tier_row = conn_t.execute("SELECT tier FROM leagues WHERE id=?", (my_league_id,)).fetchone()
            conn_t.close()
            new_tier = new_tier_row["tier"] if new_tier_row else 3
            old_tier = old_tier_row["tier"] if old_tier_row else 3
            if new_tier < old_tier:   # 승격
                new_sal = int(old_sal * 1.20)
                add_log(f"💰 승격 연봉 인상! {fmt_money(old_sal)} → {fmt_money(new_sal)} (+20%)", "event", year, 52)
            elif new_tier > old_tier:  # 강등
                new_sal = int(old_sal * 0.80)
                add_log(f"💸 강등 연봉 삭감. {fmt_money(old_sal)} → {fmt_money(new_sal)} (-20%)", "event", year, 52)
            else:
                new_sal = old_sal
            update_player(current_league_id=my_new_league,
                          salary=new_sal, current_tier=new_tier)
        else:
            update_player(current_league_id=my_new_league)
        add_log(f"📋 소속 리그가 변경되었습니다", "event", year, 52)

    for text, ltype in pending_logs:
        add_log(text, ltype, year, 52)


# ─────────────────────────────────────────
# 이적/입단
# ─────────────────────────────────────────


def _sim_all_leagues_for_season_end(season: int):
    """시즌 종료 시 내 국가 tier 1~3 리그 일정 생성 + 미완료 경기 시뮬.
    또한 이전 시즌 미완료 경기도 일괄 정리해서 구 시즌 데이터가 남지 않게 함.
    """
    # 이전 시즌 미완료 경기 전부 AI로 처리 (구 시즌 데이터 오염 방지)
    if season > 1:
        prev = season - 1
        conn0 = get_conn()
        c0 = conn0.cursor()
        c0.execute("""SELECT id, home_team_id, away_team_id FROM match_results
                      WHERE season=? AND home_score=-1""", (prev,))
        stale = c0.fetchall()
        for m in stale:
            ho = _team_avg_ovr(c0, m["home_team_id"]) + 3
            ao = _team_avg_ovr(c0, m["away_team_id"])
            diff = ho - ao
            hw = max(0.10, min(0.80, 0.45 + diff * 0.01))
            dw = 0.25
            roll = random.random()
            if roll < hw:        outcome = "home"
            elif roll < hw+dw:   outcome = "draw"
            else:                outcome = "away"
            hs, as_ = _gen_score(outcome)
            conn0.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                          (hs, as_, m["id"]))
        conn0.commit()
        conn0.close()

    conn = get_conn()
    c = conn.cursor()

    # 내 팀 국가 파악
    p_row = conn.execute(
        "SELECT current_league_id FROM my_player WHERE id=1").fetchone()
    my_lid = p_row["current_league_id"] if p_row else 0

    if not my_lid:
        conn.close()
        return

    lg_row = c.execute(
        "SELECT country_id, tier FROM leagues WHERE id=?", (my_lid,)).fetchone()
    if not lg_row:
        conn.close()
        return

    cid  = lg_row["country_id"]
    ss   = conn.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()
    year = ss["current_year"] if ss else 1990

    # 내 국가 tier 1~3 리그 전부
    c.execute("SELECT id FROM leagues WHERE country_id=? AND tier IN (1,2,3)", (cid,))
    league_ids = [r["id"] for r in c.fetchall()]
    conn.close()

    for lid in league_ids:
        # 일정 없으면 먼저 생성
        conn2 = get_conn()
        cnt = conn2.execute(
            "SELECT COUNT(*) as c FROM match_results WHERE league_id=? AND season=?",
            (lid, season)).fetchone()["c"]
        conn2.close()
        if cnt == 0:
            generate_season_schedule(lid, season, year)
        _sim_league_full(lid, season)


def generate_offers(count=5) -> list:
    p = get_player()
    if not p: return []

    conn = get_conn()
    c = conn.cursor()

    ovr         = p.get("ovr", 40)
    age         = p.get("age", 17)
    agent       = p.get("agent_grade", "F")
    nationality = p.get("nationality", "")
    has_team    = bool(p.get("current_team_id", 0))
    my_tid      = p.get("current_team_id", 0)
    grades      = _suitable_grades(ovr, agent)

    from constants import ALL_STATS
    avg_stat = sum(p.get(s, 40) for s in ALL_STATS) / len(ALL_STATS)
    force_tier3 = (age <= 17 and avg_stat < 50)

    first_join = (not has_team and age <= 18)

    # 자국 country_id 조회
    my_country_id = None
    if nationality:
        row_c = c.execute("SELECT id FROM countries WHERE name=?", (nationality,)).fetchone()
        if row_c:
            my_country_id = row_c["id"]

    # 자국 팀의 리그 평균 OVR이 내 OVR과 얼마나 차이나는지 확인
    # 차이가 너무 크면 자국 팀이 뜨지 않음 (E/F 등급 약체 리그는 괜찮음)
    def _team_fits_me(team_row) -> bool:
        """해당 팀의 리그 평균 OVR과 내 OVR 차이가 25 이내면 True."""
        c2 = conn.cursor()
        c2.execute("SELECT AVG(ovr) as avg FROM ai_players WHERE team_id=?", (team_row["id"],))
        row = c2.fetchone()
        if not row or not row["avg"]:
            return True
        league_avg = row["avg"]
        # 팀 평균보다 내가 25 이상 낮으면 현실적으로 입단 불가
        return (league_avg - ovr) <= 8

    offers = []
    tried  = 0

    if first_join and my_country_id:
        # 자국 팀 중 내 수준에 맞는 것만 우선
        # domestic_count: 30% 확률로 4개(+해외 1개), 70% 확률로 5개 모두 자국
        domestic_count = 4 if random.random() < 0.30 else 5
        while len(offers) < domestic_count and tried < 80:
            tried += 1
            grade = random.choice(grades)
            tier  = 3 if force_tier3 else random.choices([1, 2, 3], [5, 25, 70])[0]
            c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                cn.name as country,cn.flag,cn.grade
                         FROM teams t
                         JOIN leagues l ON t.league_id=l.id
                         JOIN countries cn ON l.country_id=cn.id
                         WHERE cn.id=? AND l.tier=?
                         ORDER BY RANDOM() LIMIT 1""", (my_country_id, tier))
            row = c.fetchone()
            if not row: continue
            if any(o["team_id"] == row["id"] for o in offers): continue
            if row["id"] == my_tid: continue
            if not _team_fits_me(row): continue   # ← 수준 안 맞으면 스킵
            salary = int(_calc_salary(row["grade"], tier, ovr) * random.uniform(0.85, 1.15))
            offers.append(_build_offer(row, grade, tier, salary))

        # 자국에서 못 채웠거나 해외 슬롯이 남은 경우 → 타국으로 채움
        if len(offers) < count:
            tried2 = 0
            while len(offers) < count and tried2 < 60:
                tried2 += 1
                grade = random.choice(grades)
                tier  = 3 if force_tier3 else random.choices([1, 2, 3], [5, 25, 70])[0]
                # 자국 못 채운 경우도 포함하여 타국에서 OVR 맞는 팀 탐색
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.id!=? AND cn.grade=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 1""", (my_country_id, grade, tier))
                row = c.fetchone()
                if not row: continue
                if any(o["team_id"] == row["id"] for o in offers): continue
                if row["id"] == my_tid: continue
                if not _team_fits_me(row): continue
                salary = int(_calc_salary(row["grade"], tier, ovr) * random.uniform(0.85, 1.15))
                offers.append(_build_offer(row, grade, tier, salary))
    else:
        # 일반 이적/입단 오퍼
        # ── 현재 소속 리그의 국가 팀 우선 1~2개 ──────────────────
        # 소속 리그가 있을 때: 해당 리그 국가 팀을 상단 1~2개에 배치
        # 티어별 확률: 현재 내 티어 기준 (같은 티어 가장 높음)
        cur_league_id = p.get("current_league_id", 0)
        league_country_id = None
        my_current_tier = 3
        if cur_league_id:
            row_lg = c.execute(
                "SELECT country_id, tier FROM leagues WHERE id=?", (cur_league_id,)
            ).fetchone()
            if row_lg:
                league_country_id = row_lg["country_id"]
                my_current_tier   = row_lg["tier"]

        if league_country_id:
            # 티어 가중치: 현재 티어 중심. 1부 있으면 [40,35,25], 2부 [20,50,30], 3부 [10,30,60]
            if my_current_tier == 1:
                tier_weights = [40, 35, 25]
            elif my_current_tier == 2:
                tier_weights = [20, 50, 30]
            else:
                tier_weights = [10, 30, 60]

            home_league_count = random.choices([1, 2], weights=[40, 60])[0]  # 1개 or 2개
            tried_home = 0
            while len([o for o in offers if o.get("_home_league")]) < home_league_count and tried_home < 50:
                tried_home += 1
                tier = random.choices([1, 2, 3], tier_weights)[0]
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.id=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 1""", (league_country_id, tier))
                row = c.fetchone()
                if not row: continue
                if any(o["team_id"] == row["id"] for o in offers): continue
                if row["id"] == my_tid: continue
                salary = int(_calc_salary(row["grade"], tier, ovr) * random.uniform(0.85, 1.15))
                offer = _build_offer(row, row["grade"], tier, salary)
                offer["_home_league"] = True  # 정렬용 플래그
                offers.append(offer)

        # 35세 이상이면 자국 팀 1개 추가 (소속 리그 국가와 다를 때만)
        if age >= 35 and my_country_id and my_country_id != league_country_id:
            tried_home = 0
            while tried_home < 30:
                tried_home += 1
                tier = 3 if force_tier3 else random.choices([1, 2, 3], [10, 30, 60])[0]
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.id=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 1""", (my_country_id, tier))
                row = c.fetchone()
                if not row: continue
                if any(o["team_id"] == row["id"] for o in offers): continue
                if row["id"] == my_tid: continue
                salary = int(_calc_salary(row["grade"], tier, ovr) * random.uniform(0.85, 1.15))
                offers.append(_build_offer(row, row["grade"], tier, salary))
                break

        while len(offers) < count and tried < 120:
            tried += 1
            grade = random.choice(grades)
            tier  = 3 if force_tier3 else random.choices([1, 2, 3], [10, 30, 60])[0]
            c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                cn.name as country,cn.flag,cn.grade
                         FROM teams t
                         JOIN leagues l ON t.league_id=l.id
                         JOIN countries cn ON l.country_id=cn.id
                         WHERE cn.grade=? AND l.tier=?
                         ORDER BY RANDOM() LIMIT 1""", (grade, tier))
            row = c.fetchone()
            if not row: continue
            if any(o["team_id"] == row["id"] for o in offers): continue
            if row["id"] == my_tid: continue
            if not _team_fits_me(row): continue
            salary = int(_calc_salary(row["grade"], tier, ovr) * random.uniform(0.85, 1.15))
            offers.append(_build_offer(row, grade, tier, salary))

    # _home_league 플래그 있는 오퍼를 맨 앞으로 정렬
    offers.sort(key=lambda o: 0 if o.get("_home_league") else 1)
    # 플래그 제거 (UI에 노출 불필요)
    for o in offers:
        o.pop("_home_league", None)

    # 오퍼에 뜬 팀들의 리그만 일정 생성 + AI 시뮬 후 rank_info 반영
    st = get_state()
    cur_week = st["current_week"] if st else 1
    offer_league_ids = list({o["league_id"] for o in offers})

    if cur_week >= 5:
        for lid in offer_league_ids:
            if cur_week >= SECOND_HALF_START + 7:
                # 36주~ 시즌 종료 후: '작년 성적'이 풀 시즌이 되도록 전체 일정 생성
                generate_season_schedule(lid, st["current_season"], st["current_year"])
            else:
                # 시즌 중: 상반기 일정만 (입단 후 경기 일정 영향 방지)
                _generate_first_half_schedule(lid, st["current_season"], st["current_year"])
            _sim_league_full(lid, st["current_season"])
    else:  # 이슈5: 1~4주차는 작년 시즌(prev_season) 결과로 rank_info 계산
        prev_season = st["current_season"] - 1 if st["current_season"] > 1 else None
        if prev_season:
            for lid in offer_league_ids:
                # 작년 시즌은 끝난 시즌이므로 전체 일정 생성 + 풀 시뮬
                generate_season_schedule(lid, prev_season, st["current_year"] - 1)
                _sim_league_full(lid, prev_season)

    for offer in offers:
        c2 = conn.cursor()
        offer["rank_info"] = _get_team_rank_info(c2, offer["team_id"])

    conn.close()
    return offers[:count]



def _calc_contract_years(age: int, tier: int) -> int:
    if age <= 22:   base = random.choices([3,4,5], weights=[20,40,40])[0]
    elif age <= 29: base = random.choices([3,4],   weights=[40,60])[0]
    elif age <= 32: base = random.choices([1,2],   weights=[40,60])[0]
    else:           base = 1
    if tier == 1 and age >= 28: base = max(1, base - 1)
    return base


def _offer_probability(p, week: int) -> float:
    agent_base = {"F":0.45,"E":0.55,"D":0.65,"C":0.75,"B":0.85,"A":0.92,"S":0.97}
    base = agent_base.get(p.get("agent_grade","F"), 0.45)
    if 1 <= week <= 4:
        conn2 = get_conn()
        row2  = conn2.execute(
            "SELECT avg_rating FROM career_entries WHERE end_year>0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn2.close()
        rating = row2["avg_rating"] if row2 and row2["avg_rating"] else 6.0
    elif week >= 36:
        # 여름 이적시장(포스트시즌): 풀시즌 평점 사용
        rc = p.get("season_rating_cnt",0); rs = p.get("season_rating_sum",0.0)
        rating = round(rs/rc,2) if rc else p.get("first_half_rating", 0) or 6.0
    else:
        rating = p.get("first_half_rating", 0)
        if not rating:
            rc = p.get("season_rating_cnt",0); rs = p.get("season_rating_sum",0.0)
            rating = round(rs/rc,2) if rc else 6.0
    ovr = p.get("ovr",40)
    r_s = max(0.0, min(1.0, (rating-5.0)/5.0))
    o_s = max(0.0, min(1.0, (ovr-30)/70))
    perf = r_s*0.6 + o_s*0.4

    # 등급별 최소 보장 확률 (OVR 낮아도 F급 팀은 오퍼 올 수 있음)
    min_prob = {"F":0.40,"E":0.45,"D":0.50,"C":0.55,"B":0.60,"A":0.65,"S":0.70}
    guaranteed = min_prob.get(p.get("agent_grade","F"), 0.40)

    cur_year = p.get("current_year", 1990)
    end_year = p.get("contract_end_year", 0)
    years_left = max(0, end_year - cur_year) if end_year else 3
    if years_left <= 1:   contract_mult = 1.5
    elif years_left == 2: contract_mult = 1.2
    elif years_left >= 4: contract_mult = 0.7
    else:                 contract_mult = 1.0

    calculated = base * perf * contract_mult
    return min(0.95, max(guaranteed, calculated))


def _build_offer(row, grade, tier, salary) -> dict:
    return dict(
        team_id=row["id"], team_name=row["name"],
        league_id=row["lid"], league_name=row["lname"],
        tier=row["tier"], country=row["country"],
        flag=row["flag"], grade=grade, salary=salary,
    )


def _get_team_rank_info(c, team_id) -> str:
    """이적 오퍼 카드용 순위/성적 문자열.

    현재 주차에 따라 집계 범위 결정:
    - 13~25주차 비시즌: 상반기(5~11주차) 결과만 → "상반기 성적"
    - 37~52주차 / 새 시즌 1~4주차: 시즌 전체(5~32주차) 결과 → "작년 성적"
    승강전 팀이면 이전 리그도 표시.
    """
    c.execute("SELECT league_id, name FROM teams WHERE id=?", (team_id,))
    t = c.fetchone()
    if not t:
        return ""
    league_id = t["league_id"]
    team_name = t["name"]

    # 현재 주차 파악
    c.execute("SELECT current_week, current_season FROM season_state WHERE id=1")
    ss = c.fetchone()
    cur_week   = ss["current_week"]   if ss else 1
    cur_season = ss["current_season"] if ss else 1

    # 집계할 시즌과 주차 범위 결정
    if 12 <= cur_week < SECOND_HALF_START:
        # 상반기 비시즌(미드시즌): 이번 시즌 상반기(5~11주)만
        season   = cur_season
        week_min = FIRST_HALF_START
        week_max = FIRST_HALF_START + 6
        label    = "상반기 성적"
    elif cur_week <= 4:
        # 새 시즌 시작 전: 작년 시즌 전체 성적 표시
        prev_season = cur_season - 1
        if prev_season < 1:
            return "(첫 시즌)"
        # 이전 시즌에 실제 경기 기록이 있는지 확인
        c.execute("""SELECT COUNT(*) as cnt FROM match_results
                     WHERE league_id=? AND season=? AND home_score>=0""",
                  (league_id, prev_season))
        if c.fetchone()["cnt"] == 0:
            return ""
        season   = prev_season
        week_min = FIRST_HALF_START
        week_max = SECOND_HALF_START + 6
        label    = "작년 성적"
    else:
        # 36~52주(시즌 후): 방금 끝난 현재 시즌 전체
        # (시즌 번호는 52→1주에 넘어가므로 직전 시즌 = cur_season ─ 버그 수정)
        season   = cur_season
        week_min = FIRST_HALF_START
        week_max = SECOND_HALF_START + 6
        label    = "작년 성적"

    # 해당 시즌 해당 리그에서 집계 대상 팀 목록
    c.execute("""SELECT DISTINCT home_team_id as id FROM match_results
                 WHERE league_id=? AND season=? AND week BETWEEN ? AND ?
                 UNION
                 SELECT DISTINCT away_team_id as id FROM match_results
                 WHERE league_id=? AND season=? AND week BETWEEN ? AND ?""",
              (league_id, season, week_min, week_max,
               league_id, season, week_min, week_max))
    team_ids = [r["id"] for r in c.fetchall()]

    if not team_ids:
        # 경기 기록 없음 → 승강전 정보라도 표시
        c.execute("""SELECT from_tier, to_tier, league_name FROM promotion_log
                     WHERE team_name=? AND year=(
                         SELECT MAX(year) FROM promotion_log
                         WHERE team_name=? AND year>=(
                             SELECT current_year-1 FROM season_state WHERE id=1))
                     ORDER BY id DESC LIMIT 1""",
                  (team_name, team_name,))
        pl = c.fetchone()
        if pl:
            arrow = "🔼 승격" if pl["to_tier"] < pl["from_tier"] else "🔽 강등"
            return f"({pl['league_name']}에서 {arrow})"
        return ""

    # 집계
    stats = {tid: {"w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0} for tid in team_ids}
    c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                 FROM match_results
                 WHERE league_id=? AND season=? AND home_score >= 0
                   AND week BETWEEN ? AND ?""",
              (league_id, season, week_min, week_max))
    for row in c.fetchall():
        hid, aid, hs, as_ = (row["home_team_id"], row["away_team_id"],
                              row["home_score"],   row["away_score"])
        for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
            if tid not in stats: continue
            stats[tid]["gf"] += gf; stats[tid]["ga"] += ga
            if gf > ga:    stats[tid]["w"] += 1
            elif gf == ga: stats[tid]["d"] += 1
            else:          stats[tid]["l"] += 1

    my = stats.get(team_id)
    if my is None:
        # 이 팀은 해당 시즌 이 리그에 없었음 (승격팀)
        c.execute("""SELECT from_tier, to_tier, league_name FROM promotion_log
                     WHERE team_name=? AND year=(
                         SELECT MAX(year) FROM promotion_log
                         WHERE team_name=? AND year>=(
                             SELECT current_year-1 FROM season_state WHERE id=1))
                     ORDER BY id DESC LIMIT 1""",
                  (team_name, team_name,))
        pl = c.fetchone()
        if pl:
            arrow = "🔼 승격" if pl["to_tier"] < pl["from_tier"] else "🔽 강등"
            return f"({pl['league_name']}에서 {arrow})"
        return ""

    total = my["w"] + my["d"] + my["l"]
    if total == 0:
        result = f"({label}: 경기 없음)"
    else:
        pts = my["w"] * 3 + my["d"]
        def sort_key(tid):
            s = stats[tid]
            return (-(s["w"]*3+s["d"]), -(s["gf"]-s["ga"]), -s["gf"])
        sorted_teams = sorted(team_ids, key=sort_key)
        rank = next((i+1 for i, tid in enumerate(sorted_teams) if tid == team_id), 0)
        result = f"{rank}위  {my['w']}승{my['d']}무{my['l']}패  {pts}점  ({label})"

    # 승강전 여부: 직전 연도만
    c.execute("""SELECT from_tier, to_tier, league_name FROM promotion_log
                 WHERE team_name=? AND year>=(SELECT current_year-1 FROM season_state WHERE id=1)
                 ORDER BY year DESC, id DESC LIMIT 1""",
              (team_name,))
    pl = c.fetchone()
    if pl:
        arrow = "🔼 승격" if pl["to_tier"] < pl["from_tier"] else "🔽 강등"
        result += f"\n  ({pl['league_name']}에서 {arrow})"

    return result


def _suitable_grades(ovr, agent):
    order = ["F","E","D","C","B","A","S"]
    if ovr >= 85: base = ["S","A"]
    elif ovr >= 75: base = ["A","B"]
    elif ovr >= 65: base = ["B","C"]
    elif ovr >= 55: base = ["C","D"]
    elif ovr >= 45: base = ["D","E"]
    else: base = ["E","F"]

    ai = order.index(agent)
    if ai >= 5 and "S" not in base: base.append("S")
    elif ai >= 3:
        bi = order.index(base[0])
        if bi > 0 and order[bi-1] not in base:
            base.append(order[bi-1])
    if agent == "F":
        base = [g for g in base if g in ["E","F"]] or ["F"]
    return base


def _calc_salary(grade, tier, ovr):
    base_year = {
        "S":{1:60000,2:12000,3:2400},
        "A":{1:24000,2:6000, 3:1200},
        "B":{1:12000,2:3600, 3:720},
        "C":{1:6000, 2:1800, 3:360},
        "D":{1:2400, 2:720,  3:180},
        "E":{1:1200, 2:360,  3:96},
        "F":{1:600,  2:180,  3:36},
    }
    b = base_year.get(grade,{}).get(tier,10)
    mult = ovr / 65.0
    return max(1, int(b * mult))


def _save_career_entry(p, year, week, force_new=False, transfer_type=None,
                       allow_insert=True):
    """커리어 기록 업데이트.
    force_new=True: 이전 팀 기록 확정 (end_year 채움)
    force_new=False: 시즌 종료 시 현재 팀 기록 업데이트
    allow_insert=False: 열린 항목이 없으면 아무것도 하지 않음
        (연말 _close_career_entry로 이미 닫힌 뒤 방출/재계약 거절 시
         유령 중복 행이 생기는 것 방지)
    """
    tid = p.get("current_team_id", 0)
    if not tid: return

    conn = get_conn()
    c = conn.cursor()

    # 팀/리그 정보 (이적 전 팀이므로 tid 기준)
    team_row = c.execute("""SELECT t.name, l.name as lname, l.tier
                            FROM teams t JOIN leagues l ON t.league_id=l.id
                            WHERE t.id=?""", (tid,)).fetchone()
    if not team_row:
        conn.close(); return

    rank_str = get_team_rank(tid)
    try:
        rn = int(rank_str.split("위")[0].replace("공동","").strip())
    except:
        rn = 0

    sm  = p.get("season_matches", 0)
    sg  = p.get("season_goals", 0)
    sa  = p.get("season_assists", 0)
    ss  = p.get("season_saves", 0)
    sga = p.get("season_goals_against", 0)
    rc  = p.get("season_rating_cnt", 0)
    rs  = p.get("season_rating_sum", 0.0)
    avg_r = round(rs/rc, 2) if rc else 0.0

    # 팀 전적: teams 테이블 대신 match_results에서 직접 집계 (sync 오염 방지)
    season = p.get("current_season", 1)
    league_id_row = c.execute("SELECT league_id FROM teams WHERE id=?", (tid,)).fetchone()
    tw = td = tl = 0
    if league_id_row:
        lid = league_id_row["league_id"]
        c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                     FROM match_results WHERE league_id=? AND season=? AND home_score>=0""",
                  (lid, season))
        for row in c.fetchall():
            hid, aid, hs, as_ = row[0], row[1], row[2], row[3]
            if hid == tid:
                if hs > as_: tw += 1
                elif hs == as_: td += 1
                else: tl += 1
            elif aid == tid:
                if as_ > hs: tw += 1
                elif as_ == hs: td += 1
                else: tl += 1
    pos = p.get("position", "")
    cs  = _calc_clean_sheets(c, tid, season)

    # end_year=0인 열린 항목 찾기 (team_id 우선, 구버전 행은 이름 폴백)
    existing = _find_open_entry(c, tid, team_row["name"])

    if existing:
        c.execute("""UPDATE career_entries SET
            end_year=?, end_week=?, matches=?, goals=?, assists=?, saves=?, goals_against=?,
            avg_rating=?, team_rank=?, wins=?, draws=?, losses=?, clean_sheets=?,
            league_name=?, tier=?, salary=?, position=?, team_id=?
            WHERE id=?""",
            (year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs,
             team_row["lname"], team_row["tier"], p.get("salary", 0), pos, tid,
             existing["id"]))
    elif not allow_insert:
        # 이미 닫힌 항목만 존재 → 중복 행 생성 방지
        conn.close()
        return
    else:
        cur_year = p.get("current_year", year)
        cur_week = p.get("current_week", week)
        pending_tt     = transfer_type if transfer_type else _pending_transfer_type
        c_yrs_save     = p.get("contract_years", 0)

        saved_tier = p.get("current_tier") or team_row["tier"]
        c.execute("""INSERT INTO career_entries
            (age, position, team_name, league_name, tier, salary,
             start_year, start_week, end_year, end_week,
             matches, goals, assists, saves, goals_against,
             avg_rating, team_rank, wins, draws, losses,
             contract_years, transfer_type, clean_sheets, team_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["age"], pos, team_row["name"], team_row["lname"], saved_tier,
             p.get("salary", 0), cur_year, cur_week,
             year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl,
             c_yrs_save, pending_tt, cs, tid))

    conn.commit()
    conn.close()


def join_team(team_id, salary, transfer_type: str = "입단"):
    p = get_player()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT t.name,l.id as lid,l.name as lname,l.tier
                 FROM teams t JOIN leagues l ON t.league_id=l.id
                 WHERE t.id=?""", (team_id,))
    row = c.fetchone()
    conn.close()
    if not row: return

    st = get_state()
    cur_year = st["current_year"]
    cur_week = st["current_week"]

    # 이전 팀 커리어 기록 확정 (end_year=0인 항목 닫기)
    if p and p.get("current_team_id") and p["current_team_id"] != team_id:
        _save_career_entry(p, cur_year, cur_week, force_new=True)
        # 새 팀 스탯 초기화
        update_player(season_matches=0, season_goals=0, season_assists=0,
                      season_saves=0, season_rating_sum=0.0, season_rating_cnt=0,
                      season_goals_against=0)

    age_jt = p.get("age",17) if p else 17
    c_yrs  = _calc_contract_years(age_jt, row["tier"])
    # 계약 시점이 시즌 시작(1~4주) 이후면 그 해 시즌도 포함 → +1 올림
    # 예: 21주차에 3년 계약 → cur_year+4 (올해 포함 3년 = 올해+1, +2, +3, +4)
    if cur_week > 4:
        c_end = cur_year + c_yrs + 1
    else:
        c_end = cur_year + c_yrs
    global _pending_transfer_type
    _pending_transfer_type = transfer_type
    update_player(current_team_id=team_id, current_league_id=row["lid"],
                  salary=salary, manager_relation=50,
                  contract_years=c_yrs, contract_end_year=c_end,
                  current_tier=row["tier"])
    icon = {"입단":"⭐","오퍼":"✈","방출":"😡"}.get(transfer_type,"⭐")
    add_log(f"{icon} {row['name']} {transfer_type}!  {row['lname']}({row['tier']}부)"
            f"  |  {c_yrs}년 계약  |  월 {fmt_money(salary//12)}", "event")

    # 새 팀 커리어 항목은 첫 4주 진행 시 생성 (advance_4weeks에서 처리)
    # 즉시 생성하면 입단 즉시 1~0/0주 같은 이상한 기록이 남음

    # 새 리그 일정 생성 (내 리그 + 인접 리그)
    _generate_adjacent_schedules(row["lid"], st["current_season"], st["current_year"])

    # 이적 시점 이전에 이미 지나간 주차의 미완료 경기를 일괄 시뮬
    _backfill_past_matches(row["lid"], st["current_season"], cur_week, team_id)

    # teams 테이블을 match_results 기준으로 재동기화
    # (오퍼 창 _sim_league_full이 match_results만 채우고 teams를 건드리지 않아서)
    _sync_teams_from_results(row["lid"], st["current_season"])


def _sync_teams_from_results(league_id, season):
    """match_results 기준으로 teams 테이블의 전적을 재계산해서 덮어씀.
    오퍼 창에서 _sim_league_full이 teams를 건드리지 않은 경우 동기화.
    """
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,))
    team_ids = [r["id"] for r in c.fetchall()]

    stats = {tid: {"w":0,"d":0,"l":0,"gf":0,"ga":0} for tid in team_ids}

    c.execute("""SELECT home_team_id, away_team_id, home_score, away_score
                 FROM match_results
                 WHERE league_id=? AND season=? AND home_score>=0""",
              (league_id, season))
    for row in c.fetchall():
        hid  = row["home_team_id"]
        aid  = row["away_team_id"]
        hs   = row["home_score"]
        as_  = row["away_score"]
        for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
            if tid not in stats: continue
            stats[tid]["gf"] += gf; stats[tid]["ga"] += ga
            if gf > ga:    stats[tid]["w"] += 1
            elif gf == ga: stats[tid]["d"] += 1
            else:          stats[tid]["l"] += 1

    for tid, s in stats.items():
        c.execute("""UPDATE teams SET wins=?,draws=?,losses=?,
                     goals_for=?,goals_against=? WHERE id=?""",
                  (s["w"], s["d"], s["l"], s["gf"], s["ga"], tid))

    conn.commit()
    conn.close()


def _generate_first_half_schedule(league_id, season, year):
    """오퍼 창 순위 확인용: 상반기(5~11주) 일정만 생성.
    하반기 일정은 만들지 않아 입단 후 경기 일정에 영향 없음.
    """
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM teams WHERE league_id=? LIMIT 8", (league_id,))
    tids = [r["id"] for r in c.fetchall()]
    if len(tids) < 2:
        conn.close(); return

    c.execute("""SELECT week, home_team_id, away_team_id FROM match_results
                 WHERE league_id=? AND season=?""", (league_id, season))
    existing = set()
    for r in c.fetchall():
        w, h, a = r["week"], r["home_team_id"], r["away_team_id"]
        existing.add((w, h, a))
        existing.add((w, a, h))  # 역방향도 등록

    for rd, matches in enumerate(ROUND_MATCHES):
        week = FIRST_HALF_START + rd
        for hi, ai in matches:
            if hi >= len(tids) or ai >= len(tids): continue
            t1, t2 = (tids[hi], tids[ai]) if random.random() < 0.5 else (tids[ai], tids[hi])
            key  = (week, t1, t2)
            rkey = (week, t2, t1)
            if key in existing or rkey in existing: continue
            c.execute("""INSERT INTO match_results
                         (league_id,week,home_team_id,away_team_id,
                          home_score,away_score,season,year)
                         VALUES(?,?,?,?,-1,-1,?,?)""",
                      (league_id, week, t1, t2, season, year))
            existing.add(key)
            existing.add(rkey)

    conn.commit()
    conn.close()


def _sim_league_full(league_id, season):
    """오퍼 창용: 해당 리그의 현재 주차까지 미완료 경기만 AI 시뮬.
    match_results에만 결과 저장, teams 테이블은 건드리지 않음.
    (순위는 _get_team_rank_info에서 match_results 기준으로 계산)
    과거 시즌이면 전체 주차를 시뮬 (1~4주차 '작년 성적' 계산용 ─ 버그 수정)
    """
    st = get_state()
    cur_week   = st["current_week"]   if st else 11
    cur_season = st["current_season"] if st else 1
    week_cap = 99 if season < cur_season else cur_week

    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT id, home_team_id, away_team_id
                 FROM match_results
                 WHERE league_id=? AND season=? AND home_score=-1 AND week<=?""",
              (league_id, season, week_cap))
    matches = c.fetchall()

    for m in matches:
        hid = m["home_team_id"]
        aid = m["away_team_id"]
        ho = _team_avg_ovr(c, hid) + 3
        ao = _team_avg_ovr(c, aid)
        diff = ho - ao
        hw = max(0.10, min(0.80, 0.45 + diff * 0.01))
        dw = 0.25
        roll = random.random()
        if roll < hw:          outcome = "home"
        elif roll < hw + dw:   outcome = "draw"
        else:                  outcome = "away"
        hs, as_ = _gen_score(outcome)
        # teams 테이블 업데이트 없이 match_results에만 저장
        c.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                  (hs, as_, m["id"]))

    conn.commit()
    conn.close()


def _backfill_past_matches(league_id, season, current_week, my_team_id):
    """이적 시점 이전에 이미 지나간 주차의 미완료 경기를 일괄 시뮬레이션.
    내 팀이 포함된 경기는 건너뜀(결과 없음으로 두거나, 나중에 처리).
    """
    conn = get_conn()
    c = conn.cursor()

    # current_week 미만이고 아직 결과 없는(-1) 경기들
    c.execute("""SELECT mr.id, mr.home_team_id, mr.away_team_id, mr.week
                 FROM match_results mr
                 WHERE mr.league_id=? AND mr.season=?
                   AND mr.home_score=-1
                   AND mr.week < ?""",
              (league_id, season, current_week))
    matches = c.fetchall()

    for m in matches:
        hid = m["home_team_id"]
        aid = m["away_team_id"]
        # 내 팀이 포함된 과거 경기도 랜덤으로 처리 (입단 전이니 AI끼리 뛴 것)
        ho = _team_avg_ovr(c, hid) + 3
        ao = _team_avg_ovr(c, aid)
        diff = ho - ao
        hw = max(0.10, min(0.80, 0.45 + diff * 0.01))
        dw = 0.25
        roll = random.random()
        if roll < hw:
            outcome = "home"
        elif roll < hw + dw:
            outcome = "draw"
        else:
            outcome = "away"
        hs, as_ = _gen_score(outcome)
        _update_team_rec(c, hid, aid, outcome, hs, as_)
        c.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                  (hs, as_, m["id"]))

    conn.commit()
    conn.close()

    if matches:
        add_log(f"📋 이적 전 {len(matches)}경기 결과 일괄 처리 완료", "event")