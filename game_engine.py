# game_engine.py
import random
import math
from database import get_conn, calc_ovr, ALL_STATS
from constants import *  # PHYSICAL_STATS, TECHNICAL_STATS, MENTAL_STATS 포함

_pending_transfer_type: str = ""  # join_team → _save_career_entry 전달용. ''=대기(잔류 시즌)

# ── 팀 평균 OVR 캐시 ───────────────────────────────────────────
# ai_players.ovr 및 team_id는 게임 진행 중 변경되지 않는다
# (변경 지점은 database.py의 1회성 시드/리맵뿐). 따라서 team_id별 평균 OVR은
# 세션 내내 상수다. 매 경기 시뮬마다 2.6만 행을 집계하던 _team_avg_ovr를
# 메모이즈해 동일 결과를 반환하면서 호출당 비용을 0으로 만든다.
# (값이 바뀌는 리맵 시점에는 _invalidate_team_ovr_cache로 비운다.)
_team_ovr_cache: dict = {}
_league_ovr_cache: dict = {}

def _invalidate_team_ovr_cache():
    """ai_players OVR/소속이 일괄 변경되는 경우(리맵·신규 시드) 호출."""
    _team_ovr_cache.clear()
    _league_ovr_cache.clear()


# ═══════════════════════════════════════════
# 유틸
# ═══════════════════════════════════════════

def fmt_money(amount_k: int) -> str:
    """천원 단위 정수 → 표시 문자열. (예: 1=1천원, 10000=1천만원, 100000=1억)"""
    if amount_k <= 0:
        return "무급"
    won = amount_k * 1000
    if won >= 100000000:        # 1억 이상
        return f"{won/100000000:.2f}억원"
    if won >= 10000000:         # 1천만 이상
        return f"{won/10000000:.1f}천만원"
    if won >= 10000:            # 1만 이상
        return f"{won//10000:,}만원"
    return f"{won:,}원"


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
    """나이별 훈련 효율 배수. 현실적 성장 곡선(종 모양).
    유소년기(16~18): 0.80→1.05  신체가 아직 미성숙해 성장이 더디다.
    만개기(19~peak): 1.05→1.30   19세부터 잠재력이 빠르게 터진다(피크 직전 최고).
    전성기(peak~peak+4): 1.30→1.00  고점 유지하며 완성.
    하락기(peak+4~35): 1.00→0.55   노화.
    말년(36+): 0.45
    이 형태라 16~18세에 폭발(평준화)하지 않고 20대 초중반에 완성된다.
    """
    if age <= 18:
        # 16:0.80 → 18:1.05
        t = (age - 16) / 2.0
        return round(0.80 + 0.25 * t, 3)
    if age <= peak_age:
        # 18(1.05) → peak(1.30)
        span = max(1, peak_age - 18)
        t = (age - 18) / span
        return round(1.05 + 0.25 * t, 3)
    if age <= peak_age + 4:
        # peak(1.30) → peak+4(1.00) 완만한 하강(전성기)
        t = (age - peak_age) / 4.0
        return round(1.30 - 0.30 * t, 3)
    if age <= 35:
        t = (age - (peak_age + 4)) / max(1, 35 - (peak_age + 4))
        return round(1.0 - 0.45 * t, 3)
    return 0.45


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

    # [복수국적] 일정 확률로 추가 국적 부여 (현실의 이민자 가정 등).
    # 1차 국적과 다른 나라를 최대 2개 더 뽑는다 (총 최대 3개). 없으면 단일국적.
    nationality2, flag2 = "", ""
    nationality3, flag3 = "", ""
    if random.random() < 0.20:   # 20% 확률로 2번째 국적
        r2 = c.execute(
            "SELECT name,flag FROM countries WHERE name<>? ORDER BY RANDOM() LIMIT 1",
            (nationality,)).fetchone()
        if r2:
            nationality2, flag2 = r2["name"], r2["flag"]
            # 2국적이 있을 때만, 추가로 8% 확률로 3번째 국적
            if random.random() < 0.08:
                r3 = c.execute(
                    "SELECT name,flag FROM countries WHERE name NOT IN (?,?) ORDER BY RANDOM() LIMIT 1",
                    (nationality, nationality2)).fetchone()
                if r3:
                    nationality3, flag3 = r3["name"], r3["flag"]

    personality = random.choice(PERSONALITIES)
    # [신체 특징] 성격과 별개로 1개 부여 (가중 추첨 — 무난함이 흔함)
    from constants import (PHYSICAL_TRAITS, PHYSICAL_TRAIT_WEIGHTS, PHYSICAL_TRAIT_EFFECTS,
                           BODY_TYPES, BODY_TYPE_NAMES, BODY_TYPE_WEIGHTS_BY_POS)
    physical_trait = random.choices(PHYSICAL_TRAITS, PHYSICAL_TRAIT_WEIGHTS)[0]

    # [신체 아키타입] 체형 유형 추첨. 포지션이 확률을 기울이되 고정하진 않는다
    # (윙어인데 포켓로켓/메시형, 작은데 종결자 체급 등 예외 허용).
    _bw = BODY_TYPE_WEIGHTS_BY_POS.get(position, [25, 25, 25, 25])
    body_type = random.choices(BODY_TYPE_NAMES, _bw)[0]
    _bt = BODY_TYPES[body_type]
    body_bias = _bt["stat_bias"]
    # 체형이 정한 범위 안에서 키/체중 결정
    height = random.randint(*_bt["height"])
    weight = random.randint(*_bt["weight"])

    # 재능 등급 추첨 (worldclass/elite/normal/limited) → 고강도 돌파 상한 결정
    _r = random.random()
    _acc = 0.0
    talent_tier = "normal"
    for _tname in ("worldclass", "elite", "normal", "limited"):
        _acc += TALENT_TIERS[_tname]["prob"]
        if _r < _acc:
            talent_tier = _tname
            break
    _tt = TALENT_TIERS[talent_tier]
    talent_cap = random.randint(_tt["cap_min"], _tt["cap_max"])

    # 피크 나이: 재능이 클수록 잠재력을 다 끌어내는 데 오래 걸려 늦게 정점에
    #   도달한다(월클 23~24, 무재능 20~21). 성장기(16~peak)가 길수록 천천히 오른다.
    _peak_by_tier = {
        "worldclass": (23, 24),
        "elite":      (22, 24),
        "normal":     (21, 23),
        "limited":    (20, 22),
    }
    peak_age = random.randint(*_peak_by_tier.get(talent_tier, (22, 24)))

    # 시작 스탯(16세) + 일반훈련 천장(max). max는 talent_cap을 넘지 않음.
    #   16세 시작은 낮게 잡아 20대 중반까지 천천히 성장하도록 한다.
    #   (성장 페이스는 _age_train_eff 에이징커브가 함께 결정)
    if talent_tier == "worldclass":
        target = random.randint(42, 48); dev = random.randint(7, 11)
        mx_add = (40, 54)
    elif talent_tier == "elite":
        target = random.randint(39, 45); dev = random.randint(8, 12)
        mx_add = (36, 48)
    elif talent_tier == "normal":
        target = random.randint(36, 42); dev = random.randint(10, 14)
        mx_add = (32, 44)
    else:  # limited
        target = random.randint(32, 38); dev = random.randint(11, 16)
        mx_add = (26, 38)

    stat_vals = {}
    # [해결A] 포지션 색깔: OVR 가중치를 재활용해 핵심 스탯은 살짝 높게,
    # 비핵심 스탯은 살짝 낮게 생성한다. "은은하게" — 보정폭을 작게 둬서
    # ST인데 슈팅이 바닥 같은 부자연스러운 시작만 막고, 개성은 유지.
    from database import WEIGHTS as _OVR_W
    pos_w = _OVR_W.get(position, {})
    # 가중치 평균(보통 6~7 근처)을 기준으로 +-방향 결정
    if pos_w:
        _avg_w = sum(pos_w.values()) / len(pos_w)
    else:
        _avg_w = 6.0
    # [신체 특징] 초반 신체 스탯 보너스 (신체천재 등)
    _trait_fx   = PHYSICAL_TRAIT_EFFECTS.get(physical_trait, {})
    _phys_start = _trait_fx.get("phys_start_bonus", 0)   # 신체천재: +8
    _trait_phys_stat = _trait_fx.get("phys_stat")        # 스피드스타: 'speed'만

    for s in ALL_STATS:
        # 가중치 편차를 작은 보정값으로 변환 (가중치 15 → 약 +6, 가중치 0 → 약 -5)
        w = pos_w.get(s, _avg_w)
        bias = round((w - _avg_w) * 0.7)
        bias = max(-6, min(7, bias))   # 과도한 쏠림 방지 (은은하게)
        # 신체 특징 보너스: 신체천재는 신체 3종 전체, 스피드스타는 speed만
        tbonus = 0
        if _phys_start and s in PHYSICAL_STATS:
            if _trait_phys_stat is None or _trait_phys_stat == s:
                tbonus = _phys_start
        # [신체 아키타입] 체형 보정 (현실적 ±5~8). 시작 스탯과 잠재 양쪽에 반영.
        bbias = body_bias.get(s, 0)

        cur = max(18, min(74, target + random.randint(-dev, dev) + bias + tbonus + bbias))

        # ── 스탯 상한 차등 ──────────────────────────────────────
        # 핵심 원칙:
        #   · 일반훈련 천장(max)은 talent_cap 부근에서 강/약점에 따라 흩어진다.
        #     (강점은 cap에 근접, 약점은 cap보다 확실히 낮게 → 평준화 방지)
        #   · 개별 스탯이 100을 넘는 것은 오직 '고강도 돌파(talent_cap+α)'로만
        #     가능하고, 일반훈련 max 자체는 그 천장(break_cap)을 못 넘는다.
        #   · OVR(평균)의 천장은 talent_cap 이라, 강점이 100을 넘어도 약점이
        #     낮아 평균은 cap 부근에서 균형잡힌다.
        is_strong = (bias + bbias) >= 3     # 포지션·체형이 함께 미는 강점
        is_weak   = (bias + bbias) <= -3    # 명확한 약점
        # 고강도 돌파로 도달 가능한 절대 천장 (강점만 100 초과 허용)
        if is_strong:
            break_cap = min(125, talent_cap + 12)   # 주특기는 고강도로 100+ 가능
        elif is_weak:
            break_cap = min(99,  talent_cap - 6)     # 약점은 천장이 낮음
        else:
            break_cap = min(110, talent_cap + 2)     # 평범 스탯
        # 일반훈련 천장(max)은 break_cap 보다 4~10 낮게 둔다 → 그 위(특히 100+)는
        # 고강도 훈련으로만 돌파. 재능 있어도 일반훈련만으론 100 못 감.
        soft_cap = break_cap - random.randint(4, 10)
        mx = min(soft_cap, cur + random.randint(*mx_add))
        mx = max(mx, cur + 4)        # 최소한의 성장 여지
        mx = max(28, min(break_cap, mx))
        stat_vals[s] = cur
        stat_vals[f"{s}_max"] = mx

    ovr = calc_ovr(position, stat_vals)

    conn.execute("""
    INSERT INTO my_player(
        id, name, nationality, flag, age, birth_year,
        position, sub_role, personality, height, weight, peak_age,
        stamina,stamina_max, speed,speed_max, jump,jump_max, strength,strength_max,
        shooting,shooting_max, passing,passing_max, dribbling,dribbling_max,
        tackling,tackling_max, heading,heading_max, positioning,positioning_max,
        setpiece,setpiece_max, mental,mental_max, confidence,confidence_max,
        leadership,leadership_max, concentration,concentration_max,
        ovr, current_year, current_week, current_season,
        stress, happiness, agent_grade, language,
        talent_cap, talent_tier, physical_trait, body_type
    ) VALUES (
        1,?,?,?,?,?,
        ?,?,?,?,?,?,
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
        ?,?,?,?,
        10,10,'F','ko',
        ?,?,?,?
    )""", (
        name, nationality, flag, PLAYER_START_AGE, GAME_START_YEAR,
        position, sub_role, personality, height, weight, peak_age,
        stat_vals["stamina"],    stat_vals["stamina_max"],
        stat_vals["speed"],      stat_vals["speed_max"],
        stat_vals["jump"],       stat_vals["jump_max"],
        stat_vals["strength"],   stat_vals["strength_max"],
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
        talent_cap, talent_tier, physical_trait, body_type,
    ))

    # [복수국적] 추가 국적/국기 저장 (단일국적이면 빈 값)
    conn.execute("UPDATE my_player SET nationality2=?, flag2=?, nationality3=?, flag3=? WHERE id=1",
                 (nationality2, flag2, nationality3, flag3))

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
    # [세부 지표] 시즌 누적 → 커리어 행
    d_sh, d_sho = p.get("season_shots",0), p.get("season_shots_on",0)
    d_kp, d_drb, d_blk = p.get("season_key_passes",0), p.get("season_dribbles",0), p.get("season_blocks",0)
    _pac_c = p.get("season_pass_acc_cnt",0)
    d_pac = round(p.get("season_pass_acc_sum",0.0)/_pac_c, 3) if _pac_c else 0.0

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
        shots=?, shots_on=?, key_passes=?, dribbles=?, blocks=?, pass_acc=?,
        team_id=?
        WHERE id=?""",
        (sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs,
         d_sh, d_sho, d_kp, d_drb, d_blk, d_pac, tid, existing["id"]))
    conn.commit()
    conn.close()


def _close_career_entry(p, year, week, exit_type=""):
    """현재 팀의 열린 커리어 항목(end_year=0)을 닫음. 연도별 분리용.
    exit_type: 그 팀에서 떠난 경로('팔림'/'방출'/'이적'/'계약만료'/''=재직중)."""
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
    # [세부 지표]
    d_sh, d_sho = p.get("season_shots",0), p.get("season_shots_on",0)
    d_kp, d_drb, d_blk = p.get("season_key_passes",0), p.get("season_dribbles",0), p.get("season_blocks",0)
    _pac_c2 = p.get("season_pass_acc_cnt",0)
    d_pac = round(p.get("season_pass_acc_sum",0.0)/_pac_c2, 3) if _pac_c2 else 0.0

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
        shots=?, shots_on=?, key_passes=?, dribbles=?, blocks=?, pass_acc=?,
        league_name=?, tier=?, salary=?, position=?, team_id=?, exit_type=?
        WHERE id=?""",
        (year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs,
         d_sh, d_sho, d_kp, d_drb, d_blk, d_pac,
         team_row["lname"], team_row["tier"], p.get("salary", 0),
         p.get("position", ""), tid, exit_type, existing["id"]))

    conn.commit()
    conn.close()


def _lock_in_championship(team_id, year, matches_at_team, min_week=30):
    """팀을 떠나는 시점에, 그 팀이 그 리그 1위면 우승을 즉시 trophy_log에 확정.

    시즌 종료까지 기다리지 않고 '떠나는 순간' 기록하므로, 종료 시점 소속이
    달라져서 우승이 누락되는 일을 원천 차단한다.

    조건:
      - 그 팀에서 그 시즌 min(5경기) 이상 뛰었을 것 (잠깐 거쳐간 팀 제외)
      - 시즌이 충분히 진행됐을 것(현재 주차 >= min_week) — 시즌 초 반짝 1위 방지
      - 그 팀이 현재 리그 1위일 것
      - 같은 연도·팀·tier 중복 기록 방지
    """
    if not team_id or matches_at_team < 5:
        return
    st = get_state()
    if st and st.get("current_week", 0) < min_week:
        # 시즌 후반이 아니면 아직 우승 확정하지 않음 (시즌 종료 판정에 맡김)
        return
    rows = get_league_standings_by_team(team_id)
    if not rows or rows[0]["id"] != team_id or rows[0].get("pts", 0) <= 0:
        return   # 1위 아님

    conn = get_conn(); c = conn.cursor()
    info = c.execute("""SELECT t.name, l.name AS lname, l.tier AS tier
                        FROM teams t JOIN leagues l ON t.league_id=l.id
                        WHERE t.id=?""", (team_id,)).fetchone()
    if not info:
        conn.close(); return
    tier = info["tier"]
    # 중복 방지
    exist = c.execute("SELECT id FROM trophy_log WHERE year=? AND team_name=? AND tier=?",
                      (year, info["name"], tier)).fetchone()
    if exist:
        conn.close(); return
    if tier == 1:
        comp = f"{info['lname']} 우승 (1부 리그 챔피언)"
    else:
        comp = f"{info['lname']} 우승 ({tier}부 1위 → {tier-1}부 승격)"
    c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
              (year, info["name"], info["lname"], tier, comp))
    conn.commit(); conn.close()
    add_log(f"🏆 {year}년  {info['name']}  {info['lname']} 우승!", "event")


def finalize_season_for_retire():
    """은퇴 확정 직전 호출. 리그 경기가 끝난(36주+) 현재 시즌의 우승·개인수상을
    '시즌 종료 처리(_end_of_season)를 거치지 않고' trophy_log/awards에 즉시 확정한다.

    이유: _end_of_season 은 새해 진입(52→1주) 때만 돌기 때문에, 36주 이후 은퇴하면
    그 시즌 우승/수상이 누락된다. 은퇴 화면에 정상 반영되도록 여기서 미리 박는다.
    나이 증가·스탯 노화·통계 리셋 같은 시즌전환 부작용은 일으키지 않는다.
    """
    p = get_player()
    if not p:
        return
    st = get_state() or {}
    year = st.get("current_year", p.get("current_year", 0))
    tid  = p.get("current_team_id", 0)

    # 1) 리그 우승 확정 (그 팀에서 충분히 뛰고 1위면). 36주+이므로 min_week=30 충족.
    if tid:
        _lock_in_championship(tid, year, p.get("season_matches", 0), min_week=30)

    # 2) 개인 수상 확정 (시즌 10경기 이상 출전 시). 시즌 통계는 아직 살아있다.
    #    이미 이 연도에 내 수상이 기록돼 있으면(중복 호출/시즌종료 후) 건너뛴다.
    if p.get("season_matches", 0) >= 10:
        _conn = get_conn()
        _dup = _conn.execute(
            "SELECT 1 FROM awards WHERE year=? AND is_mine=1 LIMIT 1", (year,)).fetchone()
        _conn.close()
        if not _dup:
            _rc = p.get("season_rating_cnt", 0)
            _rs = p.get("season_rating_sum", 0.0)
            season_avg_rating = round(_rs / _rc, 2) if _rc else 6.0
            _season_cs = _calc_clean_sheets_for_player(p)
            try:
                _process_awards(
                    p, year,
                    season_goals=p.get("season_goals", 0),
                    season_assists=p.get("season_assists", 0),
                    season_rating=season_avg_rating,
                    season_cs=_season_cs,
                    season_goals_against=p.get("season_goals_against", 0),
                )
            except Exception as e:
                print("finalize_season_for_retire 수상 오류:", e)


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
    #  - transfer_type/contract_years는 '이벤트(입단·이적·오퍼)가 발생한 그 해'에만
    #    표시되는 일회성 값이다. join_team이 _pending_transfer_type을 세팅한
    #    직후 첫 _ensure에서만 소비하고('' 로 리셋), 그 뒤 같은 팀에 머무는
    #    잔류 시즌 줄은 '재직중'(빈 값)으로 둔다.
    #    → 플래그가 '비어있지 않으면' 곧 방금 발생한 입단/이적/오퍼 이벤트.
    global _pending_transfer_type
    tt_e     = _pending_transfer_type
    if tt_e:
        # 입단/이적/오퍼 이벤트 줄 → 유형·연수 표시, 그리고 즉시 소비
        c_yrs_e = p.get("contract_years", 0)
        _pending_transfer_type = ""
    else:
        # 같은 팀 잔류 시즌 → 이벤트 아님 (연수/유형 비움 → UI에서 '—')
        c_yrs_e = 0
        tt_e    = ""
    tier_e   = p.get("current_tier") or team_row["tier"]
    role_e   = p.get("contract_role", "")
    mgr_e    = p.get("manager_type", "")
    amb_e    = p.get("club_ambition", "")
    c.execute("""INSERT INTO career_entries
        (age, position, team_name, league_name, tier, salary,
         start_year, start_week, end_year, end_week,
         matches, goals, assists, avg_rating, team_rank, wins, draws, losses,
         contract_years, transfer_type, team_id,
         contract_role, manager_type, club_ambition)
        VALUES (?,?,?,?,?,?,?,?,0,0,0,0,0,0,0,0,0,0,?,?,?,?,?,?)""",
        (p["age"], p.get("position",""), team_row["name"], team_row["lname"],
         tier_e, p.get("salary",0),
         st["current_year"], st["current_week"],
         c_yrs_e, tt_e, tid,
         role_e, mgr_e, amb_e))
    conn.commit()
    conn.close()


def advance_4weeks(schedule: list):
    """진행. schedule 길이만큼 '한 주씩 원자적으로' 전진시킨다.

    설계 핵심:
      - 매 주차를 처리한 직후 _advance_week(p, week, 1)로 정확히 1주 전진한다.
        → 13주(상반기 평점), 17주(국제대회), 52→1(시즌 종료) 같은 경계 트리거가
          4주를 한 번에 건너뛰어도 누락되지 않고 매주 정확히 검사된다.
      - 진행 도중 시즌이 끝나면(52주 → 1주) 그 시점에서 멈추고,
        schedule에 남은 '존재하지 않는 주차(53,54…)'는 건너뛴다.
        → 비시즌/시즌 경계가 겹쳐도 안전.
      - 월급은 4의 배수 주차를 지날 때만 지급 → 모드와 무관하게 한 달에 1회.

    schedule 1개면 '1주씩 보기', 4개면 '4주씩 보기'.
    """
    p = get_player()
    if not p: return

    st = get_state()
    if not schedule:
        return

    # 팀이 있는데 열린 커리어 항목이 없으면 지금 생성
    if p.get("current_team_id"):
        _ensure_career_entry(p, st)

    for (week, stype, detail) in schedule:
        p  = get_player()
        st = get_state()
        cur_week   = st["current_week"]
        cur_season = st["current_season"]

        # 안전장치: schedule이 가리키는 주차와 실제 현재 주차가 다르면
        # (시즌이 도중에 넘어가 53주 같은 유령 주차가 된 경우) 더 진행하지 않는다.
        if week != cur_week:
            break

        import intl_engine
        import champions_engine

        # ── 이번 주차 처리 ──
        if p.get("injured"):
            _process_injury_week(p, week)
            if stype == "경기" and not (isinstance(detail, dict) and detail.get("intl")):
                _sim_my_team_match_as_ai(week, p, cur_season)
            else:
                _sim_my_unscheduled_match(week, p, cur_season)
        elif stype == "경기":
            if isinstance(detail, dict) and detail.get("intl"):
                intl_engine.simulate_my_match(week, p)
            elif isinstance(detail, dict) and detail.get("cl"):
                champions_engine.simulate_my_cl_match(week, p)
            else:
                _simulate_match(p, week, detail)
        else:
            im = intl_engine.get_my_match(week)
            cm = champions_engine.get_my_cl_match(week)
            if im:
                intl_engine.simulate_my_match(week, p)
            elif cm:
                champions_engine.simulate_my_cl_match(week, p)
            else:
                _process_training(p, week, stype, detail)
                _sim_my_unscheduled_match(week, p, cur_season)

        # 이 주차의 국제대회 + 챔스 + 다른 리그 AI 경기 처리
        # (intl/cl/ai 경기는 my_player의 year/season/team_id/salary를 바꾸지 않으므로
        #  루프 상단의 p 를 그대로 재사용한다. 불필요한 get_player() 재조회 제거.)
        intl_engine.process_intl_week(week)
        champions_engine.process_cl_week(week)
        _sim_all_ai_matches(week, p.get("current_league_id", 0), cur_season)

        # ── 월급: 4의 배수 주차(= 한 달의 마지막 주)에서만 ──
        # salary 지급은 total_assets 를 갱신하므로, 직전 경기/훈련 처리가
        # assets 를 바꿨을 가능성에 대비해 최신 p 를 읽어 안전하게 더한다.
        # (4주에 1회뿐이라 재조회 비용은 무시할 수준)
        if week % 4 == 0:
            _pay_salary(get_player(), week)

        # ── 정확히 1주 전진 (경계 트리거 매주 검사) ──
        # _advance_week 는 season_matches(경기 출전 시 갱신됨) 등을 읽어
        # 37주 경계 커리어 갱신을 판단하므로 최신 p 를 사용한다. (주차당 1회)
        _advance_week(get_player(), week, 1)

        # 진행 중 커리어 행 실시간 갱신
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

    # [신체 특징] 효과 로드 (성장 배수/스트레스/체력훈련 보너스 등)
    from constants import PHYSICAL_TRAIT_EFFECTS as _PTE
    trait    = p.get("physical_trait", "무난함")
    trait_fx = _PTE.get(trait, {})

    # 나이별 효율 배수
    age      = p.get("age", 20)
    peak_age = p.get("peak_age", 25)
    eff *= _age_train_eff(age, peak_age)

    # 슬럼프 패널티
    if p.get("slump"):
        eff *= SLUMP_TRAIN_PENALTY

    stress_chg = cfg["stress"]
    # 냉철함 / 훈련광 등 stress_mult 적용 (성격)
    if "stress_mult" in pe:
        stress_chg = int(stress_chg * pe["stress_mult"])
    # 지구력형 / 강철체질 등 stress_mult 적용 (신체 특징)
    if "stress_mult" in trait_fx:
        stress_chg = int(stress_chg * trait_fx["stress_mult"])

    happy_chg = 0
    stat_changes = {}

    if ttype == "휴식":
        # 신체/기술 스탯 중 랜덤 1개 소폭 하락 (멘탈 스탯은 휴식으로 안 떨어짐)
        #   단, 아직 성장기(피크 나이 이전)면 휴식으로 스탯이 깎이지 않는다.
        #   한창 크는 선수가 일주일 쉰다고 퇴보하지 않으며, 성장기에 휴식 감소가
        #   있으면 스트레스 관리용 휴식이 성장을 상쇄해 버린다(22세 OVR 미달의 원인).
        #   피크 이후에는 노화로 가끔(35%) 소폭 하락.
        _age_now = p.get("age", 16)
        _peak = p.get("peak_age", 25)
        if _age_now > _peak and random.random() < 0.35:
            _phy_pool  = [s for s in PHYSICAL_STATS
                          if s in FOCUS_TRAIN_STATS.get(p["position"], PHYSICAL_STATS)]
            _tech_pool = [s for s in TECHNICAL_STATS
                          if s in FOCUS_TRAIN_STATS.get(p["position"], TECHNICAL_STATS)]
            _rest_pool = (_phy_pool or PHYSICAL_STATS) + (_tech_pool or TECHNICAL_STATS)
            _rest_below = [s for s in _rest_pool if p.get(s, 40) < p.get(f"{s}_max", 80)]
            if _rest_below:
                stat = random.choice(_rest_below)
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
        # [신체 특징] 부상 관련 보정은 성격이 아니라 신체 특징에서 읽는다.
        from constants import PHYSICAL_TRAIT_EFFECTS
        trait = p.get("physical_trait", "무난함")
        trait_fx = PHYSICAL_TRAIT_EFFECTS.get(trait, {})
        inj_add = trait_fx.get("injury_add", 0)
        immune = inj_add <= -1.0   # 강철체질: 완전 면역

        if immune:
            inj_chance = 0.0
        elif inj_chance > 0:
            # 원래 부상 위험이 있는 훈련(고강도): 특징 보정 그대로 가산
            inj_chance = max(0, inj_chance + inj_add)
        elif inj_add > 0:
            # [부상체질] 평소 안전한 훈련(중강도/집중훈련)에서도 '저 확률'로 부상.
            #   휴식·저강도는 제외. injury_add(예 0.10)의 1/3만 적용(약 3%).
            if ttype in ("중강도", "집중훈련"):
                inj_chance = inj_add / 3.0
            else:
                inj_chance = 0.0

        # 과부하(스트레스 100)면 부상 확률 급증. 단 '부상 완전 면역'(강철체질)은 예외.
        if p.get("stress", 0) >= 100 and not immune:
            inj_chance = 1.0

        if random.random() < inj_chance:
            _apply_injury(p, week)
            return

        # ── 훈련 스탯 상승 ──────────────────────────────────
        # 집중훈련: 지정 스탯 1개
        focus_mode = cfg.get("focus_mode")
        if focus_mode in ("strong", "weak"):
            # [강점/약점 훈련] 스탯을 자동 선별해 상위 2~3개를 함께 키운다.
            #   판정 기준 = '현재 수치'
            #     - strong: 현재 높은 순 → 지금 잘하는 능력치를 더 극대화
            #     - weak:   현재 낮은 순 → 지금 부족한 능력치를 메움
            #   (한계치가 아니라 현재치 기준이라, '한계는 높은데 아직 안 찬'
            #    태클 45/78 같은 스탯도 약점으로 제대로 잡힌다.)
            #   포지션 가중치 0(무관)인 스탯은 대상에서 제외(GK의 슈팅 등).
            from database import WEIGHTS as _FW
            _posw = _FW.get(p["position"], {})
            _cap_f = p.get("talent_cap", 88)
            _cand = [s for s in ALL_STATS if _posw.get(s, 0) > 0]
            if not _cand:
                _cand = list(ALL_STATS)

            def _score(s):
                # 강점/약점 판정은 '현재 수치' 기준.
                return p.get(s, 40)

            if focus_mode == "strong":
                # 강점: 현재 높은 순으로 강점군 확정 → 그 안에서 집중.
                ranked = sorted(_cand, key=_score, reverse=True)
            else:
                # 약점: 현재 낮은 순으로 약점군 확정 → 그 안에서 집중.
                ranked = sorted(_cand, key=_score)
            half = ranked[:max(3, len(ranked) // 2)]   # 상위/하위 절반(최소3)이 대상군
            # 더 올릴 여지 있는(아직 안 찬) 스탯을 우선, 없으면 그대로 둬서
            # max 돌파 로직이 받게 한다.
            if focus_mode == "strong":
                room = [s for s in half if p.get(s, 40) < _cap_f]
            else:
                room = [s for s in half if p.get(s, 40) < p.get(f"{s}_max", 80)]
            ordered = room + [s for s in half if s not in room]
            cnt = random.choices([2, 3], weights=[55, 45])[0]
            targets = ordered[:cnt]
            _slow_targets = set()
        elif ttype == "집중훈련" and focus_stat:
            # (구버전 호환) 단일 스탯 집중
            targets = [focus_stat]
            _slow_targets = set()
        else:
            pos = p["position"]
            # 포지션별 신체/기술 pool 분리
            focus = FOCUS_TRAIN_STATS.get(pos, PHYSICAL_STATS + TECHNICAL_STATS)
            phy_pool  = [s for s in PHYSICAL_STATS  if s in focus]
            tech_pool = [s for s in TECHNICAL_STATS if s in focus]
            if not phy_pool:  phy_pool  = list(PHYSICAL_STATS)
            if not tech_pool: tech_pool = list(TECHNICAL_STATS)

            # pool 내 스탯이 모두 한계에 도달했으면 전체 스탯으로 확장
            # 고강도/일반훈련 모두 '스탯별 _max'를 천장으로 본다. (고강도는 _max가
            # talent_cap+α로 높게 잡힌 강점을 100+까지 끌어올린다)
            def _below_max(stats_list):
                return [s for s in stats_list if p.get(s,40) < p.get(f"{s}_max",80)]
            phy_below  = _below_max(phy_pool)
            tech_below = _below_max(tech_pool)
            # pool 내 남은 스탯 없으면 전체에서 미달 스탯으로 확장
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

            # [B] 비focus 기술 스탯도 천천히 성장.
            #   포커스가 다 차면 남는 성장 여력이 안 찬 비focus(예: ST의 패스)로
            #   흘러가도록 확률을 높였다. 전성기에 '올릴 게 없어' 정체되는 것을 완화.
            _nonfocus = [s for s in TECHNICAL_STATS
                         if s not in focus and p.get(s,40) < p.get(f"{s}_max",80)]
            if _nonfocus and ttype in ("고강도","중강도","저강도") and random.random() < 0.30:
                _slow_pick = random.choice(_nonfocus)
                _slow_targets = {_slow_pick}
                targets = targets + [_slow_pick]
            else:
                _slow_targets = set()

            # [천장 개선] focus 스탯들이 한계에 충분히 근접하면, 남은 성장 여력을
            #   '가중치 있는 비focus 스탯'에 정상 속도로 투입한다.
            #   이게 없으면 focus 몇 개만 한계에 닿고 나머지가 낮게 남아 OVR이
            #   천장보다 10+ 낮게 수렴한다.
            #   - 고강도: talent_cap 기준 (cap까지 돌파 가능)
            #   - 중강도: max 기준 (max까지만, 다 찬 뒤 안 찬 비focus 채움)
            if cfg.get("exceed_limit"):
                _focus_avg = sum(p.get(s,40) for s in focus) / max(1, len(focus))
                _focus_cap_avg = sum(p.get(f"{s}_max",80) for s in focus) / max(1, len(focus))
                if _focus_avg >= _focus_cap_avg - 6:
                    from database import WEIGHTS as _W
                    _posw = _W.get(p["position"], {})
                    _nf_all = [s for s in ALL_STATS
                               if s not in focus and _posw.get(s,0) > 0
                               and p.get(s,40) < p.get(f"{s}_max",80)]
                    if _nf_all:
                        _nf_all.sort(key=lambda s: _posw.get(s,0), reverse=True)
                        for s in _nf_all[:2]:
                            if s not in targets:
                                targets.append(s)
                        _slow_targets = set()
            elif ttype == "중강도":
                # 중강도: focus가 max에 거의 다 찼으면 가중치 있는 비focus를 채운다.
                _focus_full = all(p.get(s,40) >= p.get(f"{s}_max",80) - 2 for s in focus)
                if _focus_full:
                    from database import WEIGHTS as _W2
                    _posw2 = _W2.get(p["position"], {})
                    _nf2 = [s for s in ALL_STATS
                            if s not in focus and _posw2.get(s,0) > 0
                            and p.get(s,40) < p.get(f"{s}_max",80)]
                    if _nf2:
                        _nf2.sort(key=lambda s: _posw2.get(s,0), reverse=True)
                        for s in _nf2[:2]:
                            if s not in targets:
                                targets.append(s)
                        _slow_targets = set()

        talent_cap = p.get("talent_cap", 88)
        for stat in targets:
            g_min, g_max = cfg["gain_min"], cfg["gain_max"]
            # [신체 특징] 스탯 계열별 성장 배수
            #   - 신체천재/피지컬몬스터/스피드스타: 신체 스탯 성장↑
            #   - 강철체질: stamina 훈련 보너스
            #   - 성격 천재: 멘탈 스탯 성장↑
            gmul = 1.0
            if stat in PHYSICAL_STATS:
                pg = trait_fx.get("phys_growth_mult", 1.0)
                # 스피드스타처럼 특정 스탯 한정이면 그 스탯에만
                tps = trait_fx.get("phys_stat")
                if pg != 1.0 and (tps is None or tps == stat):
                    gmul *= pg
                if stat == "stamina" and "stamina_train" in trait_fx:
                    gmul *= trait_fx["stamina_train"]
            elif stat in MENTAL_STATS:
                gmul *= pe.get("mental_growth_mult", 1.0)   # 성격 천재
            if gmul != 1.0:
                g_min *= gmul; g_max *= gmul
            # [B] 비focus 스탯은 아주 천천히만 성장 (gain 대폭 감소)
            if stat in _slow_targets:
                g_min *= 0.30; g_max *= 0.30
            cur = p.get(stat, 40)
            mx  = p.get(f"{stat}_max", 80)

            if cfg.get("exceed_limit"):
                # 고강도 트랙: 스탯별 _max(break_cap)까지 돌파. talent_cap 일률이
                #   아니라 스탯마다 천장이 달라, 강점(_max 높음)은 100+까지 가고
                #   약점(_max 낮음)은 일찍 멈춰 평준화되지 않는다.
                #   한계 '마지막 몇 포인트'에서만 살짝 둔화시켜(soft) 천장에 닿는
                #   순간을 늦춘다. 그 외 구간은 거의 풀스피드(돌파 트랙).
                if cur < mx:
                    # _max 미달: 풀스피드로 _max까지 채운다(마지막 12점만 둔화).
                    headroom = max(0, mx - cur)
                    soft = 1.0 if headroom >= 12 else max(0.5, headroom / 12.0)
                    raw = random.uniform(g_min, g_max) * eff * soft
                    gain = (1 if random.random() < raw else 0) if raw < 1.0 else int(raw)
                    new_val = min(mx, cur + gain)
                else:
                    # _max 도달: 한 번 훈련 시 HIGH_BREAK_PROB(20%) 확률로 _max를 +1
                    #   끌어올려 talent_cap+α(강점 100+ 가능)까지 점진 돌파.
                    break_cap = min(125, talent_cap + 12)
                    if random.random() < HIGH_BREAK_PROB and mx < break_cap:
                        new_mx = mx + 1
                        stat_changes[f"{stat}_max_up"] = (stat, new_mx)
                        new_val = min(new_mx, cur + 1)
                    else:
                        new_val = cur
            elif cfg.get("focus_mode") in ("strong", "weak") or ttype == "집중훈련":
                # [강점/약점 집중훈련]
                #  - max 미달: 소프트캡을 완만히만 적용(일반훈련보다 덜 둔화) → 잘 오름
                #  - max 도달: 고강도와 달리 '가끔만'(FOCUS_BREAK_PROB) max를 1 끌어올려
                #              talent_cap까지 점진 돌파. 풀로 채운 뒤에도 천천히 cap을 향함.
                if cur < mx:
                    # [막판 보정] _max까지 10 이하로 남으면 상승폭 ×2 (고강도 제외 공통).
                    headroom = max(0, mx - cur)
                    soft = min(1.0, max(0.45, headroom / (SOFTCAP_DENOM * 2)))
                    final_mult = 2.0 if headroom <= 10 else 1.0
                    raw = random.uniform(g_min, g_max) * eff * soft * final_mult
                    gain = (1 if random.random() < raw else 0) if raw < 1.0 else int(raw)
                    new_val = min(mx, cur + gain)
                else:
                    # max 도달 → 낮은 확률로만 한계 돌파 (cap 이하)
                    #   강점훈련은 돌파를 강하게(특화), 약점훈련은 거의 안 함(안전).
                    if focus_mode == "strong":
                        _break_p = FOCUS_BREAK_PROB_STRONG
                    elif focus_mode == "weak":
                        _break_p = FOCUS_BREAK_PROB_WEAK
                    else:
                        _break_p = FOCUS_BREAK_PROB
                    if random.random() < _break_p:
                        new_mx = min(99, talent_cap, mx + 1)
                        if new_mx > mx:
                            stat_changes[f"{stat}_max_up"] = (stat, new_mx)
                            new_val = min(new_mx, cur + 1)
                        else:
                            new_val = cur   # 이미 talent_cap
                    else:
                        new_val = cur
            else:
                # 일반훈련 트랙: max까지, 소프트캡으로 둔화.
                #   [막판 보정] _max까지 10 이하로 남으면 상승폭을 ×2.
                #   소프트캡 FLOOR 때문에 마지막 구간이 너무 느려, 고강도 없이는
                #   초기 한계스탯조차 못 채우던 문제를 보정한다. (고강도는 별도 트랙)
                headroom = max(0, mx - cur)
                soft = min(1.0, max(SOFTCAP_FLOOR, headroom / SOFTCAP_DENOM))
                final_mult = 2.0 if headroom <= 10 else 1.0
                raw = random.uniform(g_min, g_max) * eff * soft * final_mult
                gain = (1 if random.random() < raw else 0) if raw < 1.0 else int(raw)
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
    st = get_state()  # 현재 게임 상태 (연도 등)

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
    detail = {"shots":0,"shots_on":0,"key_passes":0,"dribbles":0,"blocks":0,"pass_acc":0.0}
    if played:
        goals, assists, saves, rating, events, detail = _player_perf(p, outcome, is_home, hs, as_)
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
            # [세부 지표] 누적 (season_ + total_). 패스성공률은 합·횟수로 평균 산출.
            season_shots=p.get("season_shots",0)+detail["shots"],
            season_shots_on=p.get("season_shots_on",0)+detail["shots_on"],
            season_key_passes=p.get("season_key_passes",0)+detail["key_passes"],
            season_dribbles=p.get("season_dribbles",0)+detail["dribbles"],
            season_blocks=p.get("season_blocks",0)+detail["blocks"],
            season_pass_acc_sum=p.get("season_pass_acc_sum",0)+detail["pass_acc"],
            season_pass_acc_cnt=p.get("season_pass_acc_cnt",0)+1,
            total_shots=p.get("total_shots",0)+detail["shots"],
            total_shots_on=p.get("total_shots_on",0)+detail["shots_on"],
            total_key_passes=p.get("total_key_passes",0)+detail["key_passes"],
            total_dribbles=p.get("total_dribbles",0)+detail["dribbles"],
            total_blocks=p.get("total_blocks",0)+detail["blocks"],
        )

    _update_manager_rel(p, rating, my_result, played)
    _update_pop(p, goals, assists, rating)

    p2 = get_player()
    # 30대 이후: 경험이 풍부하여 스트레스 감소
    age = st["current_year"] - p2.get("birth_year", 0)
    if age >= 30:
        match_stress = 3 if info.get("is_home") else 6  # 원래 5/8 → 3/6
    else:
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
    # 세션 캐시: 같은 team_id는 항상 같은 평균을 반환하므로 1회만 집계.
    cached = _team_ovr_cache.get(team_id)
    if cached is not None:
        return cached
    c.execute("SELECT AVG(ovr) as v FROM ai_players WHERE team_id=?", (team_id,))
    row = c.fetchone()
    val = row["v"] if row and row["v"] else 45
    _team_ovr_cache[team_id] = val
    return val


# ── 리그 평균 OVR 캐시 ─────────────────────────────────────────
# 한 리그 전체 ai_players의 평균 OVR. ai_players는 진행 중 안 바뀌므로 상수.
# (_league_ovr_cache 선언은 파일 상단 _team_ovr_cache 옆에 있음)

def _league_avg_ovr(c, league_id):
    if not league_id:
        return 50.0
    cached = _league_ovr_cache.get(league_id)
    if cached is not None:
        return cached
    c.execute("""SELECT AVG(ap.ovr) as v FROM ai_players ap
                 JOIN teams t ON ap.team_id=t.id WHERE t.league_id=?""", (league_id,))
    row = c.fetchone()
    val = row["v"] if row and row["v"] else 50.0
    _league_ovr_cache[league_id] = val
    return val


# ══════════════════════════════════════════════════════════════
# [경기력] OVR-리그격차 지배력 시스템 (튜닝 상수는 여기 모음)
#   내 OVR이 리그 평균보다 높을수록 개인 활약(골/어시/무실점)이 폭발하고,
#   낮으면 위축된다. 14경기 풀리그 기준으로 밸런싱됨.
#   - 황희찬급(85) @ 약체리그(평균50, 격차+35): ST 약 11~12골 (압도적 득점왕)
#   - 황희찬급(85) @ 강팀리그(평균82, 격차+3):   ST 약 5~6골 (평범한 주전)
#   - 언더독(격차 음수): 활약 위축
# ══════════════════════════════════════════════════════════════
DOMINANCE_K       = 0.045   # 격차 1당 배수 증가폭 (↑일수록 격차 영향 큼)
DOMINANCE_MIN     = 0.35    # 최저 배수 (강한 리그에서 위축 하한)
DOMINANCE_MAX     = 2.80    # 최고 배수 (약체리그 무쌍 상한)
GOAL_PROB_CAP     = 0.85    # 경기당 골 시도 성공확률 상한 (무쌍 방지)
ASSIST_PROB_CAP   = 0.45    # 경기당 어시 확률 상한

def _dominance_mult(my_ovr, league_avg):
    """내 OVR vs 리그 평균 → 활약 배수. 격차 0이면 1.0(리그 평균 수준)."""
    gap = (my_ovr or 50) - (league_avg or 50)
    return max(DOMINANCE_MIN, min(DOMINANCE_MAX, 1.0 + gap * DOMINANCE_K))

def _stat_n(p, stat, lo=40, hi=95):
    """스탯을 0~1로 정규화 (lo=0, hi=1). 플레이스타일 반영용."""
    v = p.get(stat, 60)
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _my_team_avg_ovr(p):
    """내 소속 팀의 AI 선수 평균 OVR (나 제외, 즉 동료 수준)."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return p.get("ovr", 40)
    conn = get_conn()
    try:
        row = conn.execute("SELECT AVG(ovr) as v FROM ai_players WHERE team_id=?", (tid,)).fetchone()
    finally:
        conn.close()
    return row["v"] if row and row["v"] else p.get("ovr", 40)


def _check_bench(p):
    """OVR 격차(팀 수준 대비)를 주 변수로, 감독 관계로 보정한 벤치 판정.
    [기능1/2] 계약 역할 + 감독 성향으로 추가 보정."""
    rel = p.get("manager_relation", 50)
    team_avg = _my_team_avg_ovr(p)
    gap = team_avg - p.get("ovr", 40)   # +면 내가 팀 수준에 못 미침

    base = 0.90
    for thr, prob in BENCH_BY_GAP:
        if gap <= thr:
            base = prob
            break

    # 감독 관계로 보정
    if rel >= 70:
        base *= 0.7
    elif rel < 30:
        base *= 1.3

    # [기능1] 계약 역할 보정
    from constants import OFFER_ROLES, MANAGER_TYPES
    role = p.get("contract_role", "주전 경쟁")
    base *= OFFER_ROLES.get(role, {}).get("bench_mult", 1.0)

    # [기능2] 감독 성향 보정
    mt = MANAGER_TYPES.get(p.get("manager_type", "베테랑 신뢰"))
    if mt:
        base *= mt["bench_mult"]
        # 유스 중시: 나이 이하면 추가 완화 / 베테랑 신뢰: 어리면 불리
        yp = mt.get("youth_pref_age", 0)
        age = p.get("age", 25)
        if yp > 0 and age <= yp:
            base *= 0.75
        elif yp == -1 and age <= 22:
            base *= 1.15

    return random.random() < min(0.95, max(0.0, base))


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
    # [세부 지표] 이 경기에서 발생한 활약 수치 (스탯·포지션·dom 연동)
    detail  = {"shots": 0, "shots_on": 0, "key_passes": 0,
               "dribbles": 0, "blocks": 0, "pass_acc": 0.0}

    my_score  = hs if is_home else as_
    opp_score = as_ if is_home else hs

    # ── 지배력 배수: 내 OVR vs 현재 리그 평균 OVR ──
    # 격차가 클수록 개인 활약(골/어시/선방/무실점)이 폭발, 작거나 음수면 위축.
    _my_ovr = p.get("ovr", 50)
    _lid    = p.get("current_league_id", 0)
    _lg_avg = 50.0
    if _lid:
        try:
            _c_dom = get_conn()
            _lg_avg = _league_avg_ovr(_c_dom.cursor(), _lid)
            _c_dom.close()
        except Exception:
            _lg_avg = 50.0
    dom = _dominance_mult(_my_ovr, _lg_avg)

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
        # 지배력 보정: 리그 평균보다 월등하면 선방률 추가 상승 (상한 92%)
        _sr_center = min(0.92, _sr_center * (0.7 + 0.3 * dom))
        # 경기마다 ±4% 랜덤 변동
        _sr_target = max(_sr_min, min(0.92, _sr_center + random.uniform(-0.04, 0.04)))

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
        # GK 세부지표: 패스성공률만 의미 (분배 능력). 슈팅/드리블/차단은 0.
        _pa_gk = _stat_n(p, "passing")
        detail["pass_acc"] = round(min(0.97, 0.70 + 0.22 * _pa_gk + random.uniform(-0.03, 0.03)), 3)
    else:
        # ── 골/어시: 지배력 배수 × 포지션 × 개인 스탯(플레이스타일) ──
        # 슈팅↑=골, 패스↑=어시, 드리블↑=둘 다 약간, 세트피스↑=보너스골.
        sh = _stat_n(p, "shooting")
        pa = _stat_n(p, "passing")
        dr = _stat_n(p, "dribbling")
        sp = _stat_n(p, "setpiece")

        # 포지션별 기본 골/어시 성향 (계수)
        if pos in ("ST", "CF"):
            g_base, g_sh, g_dr = 0.20, 0.18, 0.04
            a_base, a_pa, a_dr = 0.05, 0.18, 0.08
        elif pos in ("LW", "RW"):
            g_base, g_sh, g_dr = 0.15, 0.15, 0.07
            a_base, a_pa, a_dr = 0.07, 0.18, 0.10
        elif pos == "CAM":
            g_base, g_sh, g_dr = 0.06, 0.13, 0.05
            a_base, a_pa, a_dr = 0.07, 0.22, 0.09
        elif pos == "CM":
            g_base, g_sh, g_dr = 0.04, 0.11, 0.04
            a_base, a_pa, a_dr = 0.06, 0.20, 0.08
        else:  # 수비/CDM 등: 골·어시 드묾
            g_base, g_sh, g_dr = 0.02, 0.06, 0.02
            a_base, a_pa, a_dr = 0.03, 0.10, 0.04

        gprob = min(GOAL_PROB_CAP, (g_base + sh * g_sh + dr * g_dr) * dom)
        aprob = min(ASSIST_PROB_CAP, (a_base + pa * a_pa + dr * a_dr) * dom)

        # 골 (내 팀 득점 my_score 이내로 클램프)
        got_goal = False
        if my_score > 0 and random.random() < gprob:
            goals = random.choices([1, 2, 3], [70, 24, 6])[0]
            goals = min(goals, my_score)
            base += goals * 1.0
            events.append(f"⚽ {'골!' if goals == 1 else ('멀티골!' if goals == 2 else '해트트릭!')}")
            got_goal = True

        # 세트피스 보너스 골 (세트피스 높은 키커, 약체 상대일수록 ↑)
        if my_score > goals and sp > 0.55 and random.random() < 0.04 * dom:
            goals += 1; base += 1.0; events.append("🎯 세트피스 골!")

        # 어시 (골과 독립 판정. 골 넣은 경기는 어시 확률 절반 — 한 장면 중복 방지)
        rem = my_score - goals
        if rem > 0:
            eff_aprob = aprob * 0.5 if got_goal else aprob
            if random.random() < eff_aprob:
                assists = 1; base += 0.5; events.append("🅰 어시스트!")

        # ── [세부 지표] 슈팅/유효슈팅/키패스/드리블/차단/패스성공 ──────
        #   골/어시와 동일 체계: 정규화 스탯 × 포지션 성향 × 지배력(dom).
        #   dom 은 내 OVR vs 리그평균이라 리그 수준이 자연히 반영된다.
        ta = _stat_n(p, "tackling")
        po = _stat_n(p, "positioning")
        spd = _stat_n(p, "speed")

        # 포지션군별 활동량 계수 (공격가담/수비가담 성향)
        if pos in ("ST", "CF"):
            shot_w, key_w, drb_w, blk_w = 3.2, 1.0, 1.4, 0.3
        elif pos in ("LW", "RW"):
            shot_w, key_w, drb_w, blk_w = 2.4, 1.6, 2.6, 0.5
        elif pos == "CAM":
            shot_w, key_w, drb_w, blk_w = 1.8, 2.6, 1.8, 0.7
        elif pos == "CM":
            shot_w, key_w, drb_w, blk_w = 1.2, 2.0, 1.2, 1.6
        elif pos == "CDM":
            shot_w, key_w, drb_w, blk_w = 0.6, 1.2, 0.8, 2.8
        elif pos in ("LB", "RB"):
            shot_w, key_w, drb_w, blk_w = 0.5, 1.3, 1.2, 2.4
        else:  # CB
            shot_w, key_w, drb_w, blk_w = 0.3, 0.5, 0.4, 3.0

        # 슈팅: 슈팅·포지셔닝 스탯 × 공격성향 × dom. 유효슈팅은 그중 일부(결정력=shooting).
        shots = int(round(shot_w * (0.4 + 0.6 * sh) * dom + random.uniform(0, 1)))
        shots = max(0, shots)
        on_ratio = 0.30 + 0.35 * sh                       # shooting 높을수록 유효슈팅 비율↑
        shots_on = int(round(shots * on_ratio + random.uniform(0, 0.5)))
        shots_on = max(min(shots, goals), min(shots, shots_on))  # 최소 골 수 이상
        # 키패스(기회창출): 패스·드리블 × 창의 성향 × dom
        key_passes = int(round(key_w * (0.35 + 0.4 * pa + 0.25 * dr) * dom + random.uniform(0, 1)))
        key_passes = max(assists, key_passes)             # 최소 어시 수 이상
        # 드리블 성공: 드리블·스피드 × 성향 × dom
        dribbles = int(round(drb_w * (0.4 + 0.45 * dr + 0.15 * spd) * dom + random.uniform(0, 1)))
        dribbles = max(0, dribbles)
        # 차단(태클+인터셉트): 태클·포지셔닝 × 수비성향. 약체 상대(dom↑)일수록 상대 공격이
        #   적어 차단 기회도 줄므로 dom 역방향(2-dom) 가중.
        # 차단(태클+인터셉트): 태클·포지셔닝 × 수비성향. 약체 상대(dom↑)면 상대 공격이
        #   적어 차단 기회도 다소 줄지만(완만한 역방향), 수비수의 기본 활동량이 핵심.
        _blk_dom = 1.25 - 0.25 * min(1.4, dom)   # dom 1.0→1.0, 강팀일수록 소폭↓
        blocks = int(round(blk_w * (0.7 + 0.6 * ta + 0.3 * po) * _blk_dom
                           + random.uniform(0, 1.2)))
        blocks = max(0, blocks)
        # 패스 성공률: 패스 스탯 기반 (수비/미드일수록 안정적, 공격수는 약간 낮음)
        _pa_floor = 0.72 if pos in ("CB","LB","RB","CDM","CM") else 0.66
        pass_acc = min(0.97, _pa_floor + 0.22 * pa + random.uniform(-0.03, 0.03))

        detail["shots"]      = shots
        detail["shots_on"]   = shots_on
        detail["key_passes"] = key_passes
        detail["dribbles"]   = dribbles
        detail["blocks"]     = blocks
        detail["pass_acc"]   = round(pass_acc, 3)

        # 수비 라인 평점 보정 (무실점 기여 — 지배력 클수록 더 안정적)
        if pos in DEF_POS:
            if opp_score == 0:
                base += 1.0 * (0.7 + 0.3 * dom); events.append("🧱 무실점 기여")
            elif opp_score == 1:
                base += 0.4
            elif opp_score >= 3:
                base -= 0.6; events.append("😞 수비 불안")

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
    return goals, assists, saves, base, events, detail


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
    """[기능2] 감독 성향에 따라 관계 상승/하락 폭이 달라진다."""
    from constants import MANAGER_TYPES
    mt = MANAGER_TYPES.get(p.get("manager_type", "베테랑 신뢰"), {})
    gain_m = mt.get("rel_gain_mult", 1.0)
    loss_m = mt.get("rel_loss_mult", 1.0)

    rel = p.get("manager_relation",50)
    if not played:
        rel = max(0, rel - round(1 * loss_m))
    else:
        if rating >= 7.0:   rel = min(100, rel + round(3 * gain_m))
        elif rating >= 6.0: rel = min(100, rel + round(1 * gain_m))
        elif rating < 5.0:  rel = max(0, rel - round(3 * loss_m))
        if result == "win":    rel = min(100, rel + 1)
        elif result == "loss": rel = max(0, rel - round(1 * loss_m))
        if p.get("injured"): rel = max(0, rel - round(2 * loss_m))
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
    우승과 동일 기준: 리그 경기가 끝나는 35주 시점에 그 팀 소속이었던 해의
    연말 승강만 포함. (35주 이후 합류했거나 35주 전에 떠난 해는 제외)"""
    LEAGUE_END_WEEK = 35
    conn = get_conn(); c = conn.cursor()
    entries = c.execute(
        "SELECT team_name, start_year, start_week, end_year, end_week FROM career_entries ORDER BY id"
    ).fetchall()
    conds, params = [], []
    for e in entries:
        tn = e["team_name"]
        sy, sw = e["start_year"], (e["start_week"] or 0)
        ey, ew = e["end_year"], e["end_week"]
        # 이 항목이 '35주 시점에 그 팀 소속'인 연도들만 승강 대상.
        if ey == 0:
            # 진행 중(아직 안 닫힘): 시작 연도에 35주까지 함께했는지로 판단,
            # 이후 연도는 항상 포함(연말까지 소속).
            if sw <= LEAGUE_END_WEEK:
                conds.append("(team_name=? AND year>=?)"); params.extend([tn, sy])
            else:
                conds.append("(team_name=? AND year>?)"); params.extend([tn, sy])
        else:
            # 닫힌 항목: 시작 연도(35주 전 합류) ~ 종료 연도(35주 후 잔류) 사이.
            yr_start = sy if sw <= LEAGUE_END_WEEK else sy + 1
            yr_end   = ey if (ew or 0) > LEAGUE_END_WEEK else ey - 1
            if yr_start <= yr_end:
                conds.append("(team_name=? AND year>=? AND year<=?)")
                params.extend([tn, yr_start, yr_end])
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

    # [중복 생성 방지] 그 시즌 일정이 이미 충분히 생성돼 있으면(상·하반기 14라운드분)
    #   다시 만들지 않는다. 승강 등으로 teams 구성이 바뀐 뒤 재호출되면 옛 일정과
    #   새 대진이 섞여 '어떤 팀 3경기 / 어떤 팀 0경기'가 되는 것을 막는다.
    if not force:
        n_existing = c.execute(
            "SELECT COUNT(*) AS c FROM match_results WHERE league_id=? AND season=?",
            (league_id, season)).fetchone()["c"]
        # 8팀 풀리그 = 라운드당 4경기 × 14라운드 = 56경기. 8할 이상 차 있으면 완비로 간주.
        if n_existing >= 56 * 0.8:
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
    # 에이전트 수수료: 개별 계약 수수료율(agent_fee_rate)이 있으면 그것,
    # 없으면(0) 등급 기본값. 같은 등급이라도 계약마다 수수료가 다를 수 있다.
    fee = p.get("agent_fee_rate", 0) or AGENT_FEE_RATE.get(p.get("agent_grade","F"), 0)
    net = int(monthly * (1-fee))
    assets   = p.get("total_assets",   0) + net
    earnings = p.get("total_earnings", 0) + net  # 이슈10: 누적 수입
    update_player(total_assets=assets, total_earnings=earnings)
    add_log(f"💰 월급 수령  +{fmt_money(net)}  (총자산: {fmt_money(assets)})", "salary")


# ─────────────────────────────────────────
# 주차 전진
# ─────────────────────────────────────────

def _update_residency_and_naturalization(cur_year):
    """[귀화] 매 연도 전환 시 호출.
    - 현재 소속 클럽의 '나라'에서 보낸 누적 연수를 추적한다.
      같은 나라면 +1, 나라가 바뀌면 1로 리셋. (그 나라 안에서 팀 이동은 유지)
    - 같은 나라에서 3년을 채우고, 21세 이전이며, A대표 '본선'을 아직 안 밟았고,
      그 나라가 아직 내 국적/귀화국적이 아니면 → 귀화 국적을 획득(복수국적 추가).
      이후 국가대표 선택 시 후보에 포함된다. (21세 이후엔 자동 소속고정이라 무의미)
    """
    p = get_player()
    if not p:
        return
    tid = p.get("current_team_id")
    if not tid:
        return
    conn = get_conn()
    row = conn.execute(
        "SELECT c.name AS cname FROM teams t JOIN countries c ON t.country_id=c.id "
        "WHERE t.id=?", (tid,)).fetchone()
    conn.close()
    if not row:
        return
    club_country = row["cname"]

    prev_country = p.get("residency_country", "") or ""
    prev_years = p.get("residency_years", 0) or 0
    if club_country == prev_country:
        new_years = prev_years + 1
    else:
        new_years = 1
    update_player(residency_country=club_country, residency_years=new_years)

    # --- 귀화 자격 판정 ---
    age = cur_year - (p.get("birth_year", cur_year - 17) or (cur_year - 17))
    if age > 21:
        return                      # 21세 넘으면 소속 자동확정, 귀화 불가
    if p.get("intl_capped", 0):
        return                      # 이미 본선 출전(cap-tie) → 변경 불가
    if p.get("intl_committed", ""):
        return                      # 이미 대표팀 영구고정
    if new_years < 3:
        return                      # 거주 3년 미충족

    # 이미 보유한 국적(출생/귀화)이면 스킵
    owned = {p.get("nationality","") or "", p.get("nationality2","") or "",
             p.get("nationality3","") or ""}
    nat_list = [n for n in (p.get("naturalized_nats","") or "").split(",") if n]
    owned |= set(nat_list)
    if club_country in owned:
        return
    # 빈 국적 슬롯에 귀화 국적 추가 (nationality2 → nationality3)
    if not (p.get("nationality2","") or ""):
        update_player(nationality2=club_country)
    elif not (p.get("nationality3","") or ""):
        update_player(nationality3=club_country)
    else:
        return                      # 국적 슬롯이 꽉 참(이미 3개)
    nat_list.append(club_country)
    update_player(naturalized_nats=",".join(nat_list))
    try:
        add_log(f"🛂 {club_country} 귀화 자격 획득! ({club_country} 리그 {new_years}년 거주) "
                f"— 국가대표 선택 시 {club_country}도 고를 수 있습니다.", "event")
    except Exception:
        pass


def _team_at_week35_for(year):
    """주어진 연도의 35주차(리그 종료) 시점에 내가 소속이던 팀 id.
    36주 이후 이적해도 '리그를 끝까지 함께한 팀'을 우승 귀속 대상으로 본다."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT team_id, start_week, start_year, end_week, end_year
               FROM career_entries
               WHERE team_id IS NOT NULL AND team_id<>0
                 AND start_year<=? AND (end_year=0 OR end_year>=?)
               ORDER BY start_week""", (year, year)).fetchall()
        for r in rows:
            sw = r["start_week"] or 0
            if (r["end_year"] or 0) == 0 or (r["end_year"] or 0) > year:
                ew = 52
            else:
                ew = r["end_week"] or 52
            # 시작 연도가 올해면 start_week, 아니면 1주부터로 간주
            sw_eff = sw if (r["start_year"] or year) == year else 1
            if sw_eff <= 35 <= ew:
                conn.close()
                return r["team_id"]
    except Exception:
        pass
    conn.close()
    p = get_player() or {}
    return p.get("current_team_id", 0)


def _lock_league_title_at_37(p, year):
    """37주 진입 시: 리그 경기(35주)가 끝났으므로, 35주 소속 팀이 1위면
    그 즉시 우승을 trophy_log에 확정한다. (연말까지 안 기다림)
    36주에 다른 팀으로 이적해도 35주 소속 팀 기준이라 우승이 누락되지 않는다."""
    champ_tid = _team_at_week35_for(year)
    if not champ_tid:
        return
    # 그 팀에서 그 시즌 5경기 이상 뛰었을 때만(스쳐간 팀 제외).
    #   현재 팀이면 season_matches, 이미 떠난 팀이면 career_entries의 matches 사용.
    matches = 0
    p_now = get_player() or {}
    if champ_tid == p_now.get("current_team_id"):
        matches = p_now.get("season_matches", 0)
    else:
        conn = get_conn()
        try:
            r = conn.execute(
                """SELECT matches FROM career_entries
                   WHERE team_id=? AND start_year<=? AND (end_year=0 OR end_year>=?)
                   ORDER BY start_week DESC LIMIT 1""",
                (champ_tid, year, year)).fetchone()
            if r:
                matches = r["matches"] or 0
        except Exception:
            pass
        conn.close()
    _lock_in_championship(champ_tid, year, matches, min_week=35)


def _advance_week(p, base_week, n_weeks=4):
    new_week = base_week + n_weeks
    new_year = p["current_year"]
    new_season = p["current_season"]

    if new_week > 52:
        new_week -= 52
        new_year += 1
        new_season += 1
        # 연도 넘어갈 때 현재 팀 커리어 항목 닫기 (연도별 분리)
        if p.get("current_team_id"):
            _close_career_entry(p, new_year - 1, 52)
        # [귀화] 거주 연수 갱신 + 자격 체크는 _end_of_season 안에서 처리
        #   (그 시점에 current_team_id가 아직 살아있어 소속국가를 읽을 수 있음)
        _end_of_season(p, new_year-1)
    else:
        # 하반기 종료(33~36주차) → 37~40주차: 커리어 스탯 중간 업데이트만
        # (항목은 닫지 않음 - 연도 변경 시 _close_career_entry가 닫음)
        if base_week <= 36 and new_week >= 37:
            if p.get("current_team_id") and p.get("season_matches", 0) > 0:
                _update_career_stats(p, new_year, new_week)
            # [우승 확정] 리그 경기는 35주에 끝나므로, 37주 진입 시 35주 소속 팀이
            #   1위면 그 즉시 우승을 기록한다. (연말까지 안 기다리고 바로 '성적'에 반영)
            _lock_league_title_at_37(p, new_year)

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

    # 41주차 진입: 클럽 대륙 챔피언스리그 시작 (매년)
    #   리그 경기는 35주에 끝나므로 직전 시즌(current_season) 순위로 출전팀 선발.
    from champions_engine import CL_START_WEEK
    if new_week == CL_START_WEEK:
        try:
            import champions_engine
            champions_engine.start_champions_league(new_year, new_season)
        except Exception as e:
            add_log(f"⚠ 챔피언스리그 생성 오류: {e}", "event")

    # 새 시즌 시작(1주차 진입 시 1회) 내 리그 + 인접 리그 일정 생성
    # generate_season_schedule는 멱등하지만, 1주차에만 호출해 불필요한 중복 조회 방지
    if new_week == 1 and p.get("current_team_id"):
        p_fresh = get_player()
        if p_fresh and p_fresh.get("current_league_id"):
            _generate_adjacent_schedules(
                p_fresh["current_league_id"], new_season, new_year)


def _calc_clean_sheets_for_player(p):
    """현재 선수 소속 팀의 이번 시즌 클린시트 수 (수상 산정용)."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return 0
    conn = get_conn(); c = conn.cursor()
    try:
        season = p.get("current_season", 1)
        return _calc_clean_sheets(c, tid, season)
    except Exception:
        return 0
    finally:
        conn.close()


def _estimate_ai_season(ovr, pos, team_avg, league_avg):
    """AI 선수의 시즌 성적(골/도움/평점)을 OVR 기반으로 추정."""
    g_base = AWARD_POS_GOAL.get(pos, 1) + (ovr-70)*0.55 + (team_avg-league_avg)*0.25
    goals = max(0, round(max(0, g_base) * random.uniform(0.8, 1.2)))
    a_base = AWARD_POS_ASSIST.get(pos, 1) + (ovr-70)*0.35 + (team_avg-league_avg)*0.15
    assists = max(0, round(max(0, a_base) * random.uniform(0.8, 1.2)))
    rating = round(6.0 + (ovr-60)/20.0 + goals*0.02 + assists*0.015, 2)
    rating = max(5.0, min(9.5, rating))
    return goals, assists, rating


def _best11_score(goals, assists, rating, ovr):
    """FW(포워드) 포지션용 점수식 — 골 가중치 높음"""
    return goals*2 + assists*1.0 + rating*5 + ovr*0.3


def _best11_score_gk_df(clean_sheets, rating, ovr):
    """GK/DF(골키퍼, 수비수) 포지션용 점수식 — 클린시트 가중치"""
    return clean_sheets*2.5 + rating*5 + ovr*0.3


def _best11_score_mf(goals, assists, rating, ovr):
    """MF(미드필더) 포지션용 점수식 — 골과 도움 균형"""
    return goals*1.5 + assists*1.5 + rating*5 + ovr*0.3


def _collect_league_candidates(c, league_id, exclude_my_team=None):
    """리그 내 모든 팀의 AI 공격 포지션 선수들 시즌 성적 추정 → 후보 리스트."""
    teams = c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,)).fetchall()
    if not teams:
        return [], 50.0
    team_ids = [t["id"] for t in teams]
    # 리그 평균 OVR
    all_ovr = []
    team_avg = {}
    for tid in team_ids:
        ovrs = [r["ovr"] for r in c.execute("SELECT ovr FROM ai_players WHERE team_id=?", (tid,))]
        if ovrs:
            team_avg[tid] = sum(ovrs)/len(ovrs)
            all_ovr += ovrs
    league_avg = sum(all_ovr)/len(all_ovr) if all_ovr else 50.0

    cands = []
    for tid in team_ids:
        tavg = team_avg.get(tid, league_avg)
        rows = c.execute(
            "SELECT name, position, ovr FROM ai_players WHERE team_id=? AND position IN ({})".format(
                ",".join("'%s'" % p for p in ATTACK_POS)),
            (tid,)).fetchall()
        for r in rows:
            g, a, rt = _estimate_ai_season(r["ovr"], r["position"], tavg, league_avg)
            cands.append({
                "name": r["name"], "position": r["position"], "ovr": r["ovr"],
                "goals": g, "assists": a, "rating": rt, "is_mine": False,
            })
    return cands, league_avg


def _process_awards(p, year, season_goals, season_assists, season_rating, season_cs, season_goals_against=0):
    """시즌 종료 시 개인 수상 산정. 내 선수 실제 성적 + AI 추정 비교."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return  # 무소속이면 수상 없음
    conn = get_conn(); c = conn.cursor()
    try:
        lrow = c.execute("""SELECT l.id as lid, l.name as lname, l.tier,
                                   cn.grade as grade
                            FROM teams t JOIN leagues l ON t.league_id=l.id
                            JOIN countries cn ON l.country_id=cn.id
                            WHERE t.id=?""", (tid,)).fetchone()
        if not lrow:
            conn.close(); return
        league_id, lname, tier, grade = lrow["lid"], lrow["lname"], lrow["tier"], lrow["grade"]

        cands, league_avg = _collect_league_candidates(c, league_id)
        # 내 선수 추가
        me = {
            "name": p.get("name","나"), "position": p.get("position","ST"),
            "ovr": p.get("ovr",40), "goals": season_goals, "assists": season_assists,
            "rating": season_rating, "is_mine": True,
        }
        pool = cands + [me]

        my_awards = []  # (award_type, detail)

        # 득점왕 (10경기 이상 출전 가정 — 내 출전 충분할 때만 후보)
        top_scorer = max(pool, key=lambda x: (x["goals"], x["rating"]))
        if top_scorer["is_mine"] and season_goals > 0:
            my_awards.append(("득점왕", f"{season_goals}골"))

        # 도움왕
        top_assist = max(pool, key=lambda x: (x["assists"], x["rating"]))
        if top_assist["is_mine"] and season_assists > 0:
            my_awards.append(("도움왕", f"{season_assists}도움"))

        # 베스트11 — 포지션 그룹별 최고 점수 1위 선정 (포메이션: GK1/DF4/MF3/FW3)
        my_pos = p.get("position","ST")
        my_best11 = False
        cs_for_me = _calc_clean_sheets_for_player(p)
        
        if my_pos in GK_POS:
            # GK 그룹
            gk_cands = [x for x in pool if x["position"] in GK_POS]
            if gk_cands:
                # GK는 클린시트로 평가 (내 선수는 cs_for_me, AI는 추정치)
                gk_scores = []
                for x in gk_cands:
                    cs_est = _calc_clean_sheets_for_player(p) if x["is_mine"] else max(0, int(p.get("season_cs", 0) * 0.5))
                    score = _best11_score_gk_df(cs_est, x["rating"], x["ovr"])
                    gk_scores.append((x, score))
                best_gk = max(gk_scores, key=lambda x: x[1])
                if best_gk[0]["is_mine"]:
                    my_best11 = True
        
        elif my_pos in DF_POS:
            # DF 그룹 (CB, LB, RB 등)
            df_cands = [x for x in pool if x["position"] in DF_POS]
            if df_cands:
                df_scores = []
                for x in df_cands:
                    cs_est = cs_for_me if x["is_mine"] else max(0, int(p.get("season_cs", 0) * 0.4))
                    score = _best11_score_gk_df(cs_est, x["rating"], x["ovr"])
                    df_scores.append((x, score))
                best_df = max(df_scores, key=lambda x: x[1])
                if best_df[0]["is_mine"]:
                    my_best11 = True
        
        elif my_pos in MF_POS:
            # MF 그룹 (CDM, CM, CAM)
            mf_cands = [x for x in pool if x["position"] in MF_POS]
            if mf_cands:
                best_mf = max(mf_cands, key=lambda x: _best11_score_mf(x["goals"],x["assists"],x["rating"],x["ovr"]))
                if best_mf["is_mine"]:
                    my_best11 = True
        
        elif my_pos in FW_POS:
            # FW 그룹 (LW, RW, CF, ST)
            fw_cands = [x for x in pool if x["position"] in FW_POS]
            if fw_cands:
                best_fw = max(fw_cands, key=lambda x: _best11_score(x["goals"],x["assists"],x["rating"],x["ovr"]))
                if best_fw["is_mine"]:
                    my_best11 = True
        
        if my_best11:
            my_awards.append(("베스트11", f"베스트11 ({my_pos})"))

        # MVP (전체 베스트11 점수 1위)
        mvp = max(pool, key=lambda x: _best11_score(x["goals"],x["assists"],x["rating"],x["ovr"]))
        if mvp["is_mine"]:
            my_awards.append(("MVP", f"{lname} 올해의 선수"))

        # 골든글러브 (GK 최다 클린시트 — 내가 GK이고 클린시트 많을 때)
        if p.get("position") == "GK" and season_cs >= 10:
            my_awards.append(("골든글러브", f"{season_cs} 클린시트"))

        # 영플레이어 (YPOTY) — 21세 이하만 필터 → 그들 중 최고 활약자
        # 상급 상 수상 조건 제거: 매 시즌 "유망주 중 최고"를 배출하기 위함
        young_cands = [x for x in pool if x.get("age", 30) <= 21]
        if young_cands:
            young_best = max(young_cands, key=lambda x: _best11_score(x["goals"],x["assists"],x["rating"],x["ovr"]))
            if young_best["is_mine"]:
                my_awards.append(("영플레이어", f"{lname} 영플레이어"))

        # 발롱도르 (S/A급 1부 + 세계 정상급 OVR + 압도적 성적)
        # 개선: 25골+ 고정 조건 → 골+도움 30+ (포지션별 공평화)
        # 이제 윙어/미드필더도 도움으로 기여도를 인정받을 수 있음
        ballon = False
        if grade in BALLON_DOR_GRADES and tier == 1 and p.get("ovr",0) >= 88:
            other = c.execute("""SELECT MAX(a.ovr) as mo FROM ai_players a
                JOIN teams t ON a.team_id=t.id
                JOIN leagues l ON t.league_id=l.id
                JOIN countries cn ON l.country_id=cn.id
                WHERE cn.grade IN ('S','A') AND l.tier=1 AND a.position IN ({})
                """.format(",".join("'%s'" % pp for pp in ATTACK_POS))).fetchone()
            rival_ovr = other["mo"] if other and other["mo"] else 90
            # 세계 최정상급(라이벌 -2 이내) + 압도적 시즌
            # (골+도움 30+ 또는 MVP 수상)
            world_class = p.get("ovr",0) >= rival_ovr - 2
            dominant = (season_goals + season_assists >= 30) or mvp["is_mine"]
            if world_class and dominant:
                ballon = True
        if ballon:
            my_awards.append(("발롱도르", f"{year} 발롱도르"))

        # 신데렐라 스토리 (저OVR 대비 활약도 최고)
        # MVP 점수 / OVR로 "효율"을 계산 → 저OVR(70 이하)이면서 가장 높은 효율자
        cinderella_cand = []
        for x in pool:
            if x["ovr"] <= 70:
                score = _best11_score(x["goals"], x["assists"], x["rating"], x["ovr"])
                eff = score / x["ovr"] if x["ovr"] > 0 else 0
                cinderella_cand.append((x, eff))
        if cinderella_cand:
            cinderella_best = max(cinderella_cand, key=lambda x: x[1])
            if cinderella_best[0]["is_mine"]:
                x = cinderella_best[0]
                my_awards.append(("신데렐라", f"OVR {x['ovr']} → 리그 정상급 활약"))

        # 푸스카스상 (올해의 골 — 최고 평점이면서 멀티골 이상)
        # 조건: season_goals >= 2 && 최고 평점 기록
        multi_goal_cands = [x for x in pool if x["goals"] >= 2]
        if multi_goal_cands:
            puskas_best = max(multi_goal_cands, key=lambda x: x["rating"])
            if puskas_best["is_mine"]:
                my_awards.append(("푸스카스상", f"{season_goals}골, 평점 {season_rating:.1f}"))

        # 사모라 상 (최저 실점 골키퍼 — 경기당 평균 실점 최소)
        # 조건: GK && season_matches >= 20 && 경기당 1.2골 이하
        season_matches = p.get("season_matches", 0)
        if p.get("position") == "GK" and season_matches >= 20:
            my_ga_rate = season_goals_against / season_matches if season_matches > 0 else 999
            # 대부분의 GK는 경기당 1.3~1.5골 실점, 우수한 GK는 1.0~1.2
            # 임계값 1.2 이하면 수상 가능
            if my_ga_rate <= 1.2:
                my_awards.append(("사모라상", f"경기당 {my_ga_rate:.2f}골 실점 ({season_goals_against}/{season_matches}경기)"))

        # 저장 (DB 작업은 이 conn으로 모두 처리)
        for atype, detail in my_awards:
            c.execute("INSERT INTO awards(year,award_type,league_name,detail,is_mine) VALUES(?,?,?,?,1)",
                      (year, atype, lname, detail))
            if atype in ("발롱도르","MVP"):
                c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                          (year, p.get("name","나"), lname, tier, f"{atype} ({detail})"))
        conn.commit()
        conn.close()

        # 로그는 conn 닫은 뒤 (add_log가 별도 conn을 열므로 락 방지)
        for atype, detail in my_awards:
            icon = {"득점왕":"⚽","도움왕":"🎯","베스트11":"⭐","MVP":"🏅",
                    "발롱도르":"🏆","영플레이어":"🌟","골든글러브":"🧤",
                    "신데렐라":"✨","푸스카스상":"💥","사모라상":"🛡️"}.get(atype,"🏅")
            add_log(f"{icon} {atype} 수상! ({detail})  {year}년", "event", year, 52)
        return
    except Exception as e:
        print("_process_awards 오류:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _end_of_season(p, year):
    # 커리어 기록은 37~40주차 진입 시 이미 저장됨 → 여기선 생략

    # [귀화] 거주 연수 갱신 + 귀화 자격 체크. 이 함수 진입 시점엔 current_team_id가
    #   아직 살아있다(아래 계약만료 처리 전). next_year 기준으로 판정.
    _update_residency_and_naturalization(year + 1)

    # ── [기능1] 계약 보너스 정산 (출전·공격포인트 기반) ──────────
    if p.get("current_team_id"):
        app_b  = p.get("appearance_bonus_k", 0)
        goal_b = p.get("goal_bonus_k", 0)
        s_matches = p.get("season_matches", 0)
        s_points  = p.get("season_goals", 0) + p.get("season_assists", 0)
        bonus_total = app_b * s_matches + goal_b * s_points
        if bonus_total > 0:
            new_assets = p.get("total_assets", 0) + bonus_total
            new_earn   = p.get("total_earnings", 0) + bonus_total
            update_player(total_assets=new_assets, total_earnings=new_earn)
            add_log(f"💰 계약 보너스 정산: {fmt_money(bonus_total)} "
                    f"(출전 {s_matches}경기, 공격P {s_points})  {year}년", "event", year, 52)
            p = get_player()   # 갱신 반영

    # 시즌 평점 스냅샷 (아래 4단계에서 통계가 리셋되므로 미리 계산)
    # → 8단계 계약 만료 체크에서 사용
    _rc0 = p.get("season_rating_cnt", 0)
    _rs0 = p.get("season_rating_sum", 0.0)
    season_avg_rating = round(_rs0/_rc0, 2) if _rc0 else 6.0

    # 1.5 개인 수상 산정 (통계 리셋 이전에 실행)
    if p.get("season_matches", 0) >= 10:
        _season_cs = _calc_clean_sheets_for_player(p)
        _process_awards(
            p, year,
            season_goals=p.get("season_goals", 0),
            season_assists=p.get("season_assists", 0),
            season_rating=season_avg_rating,
            season_cs=_season_cs,
            season_goals_against=p.get("season_goals_against", 0),
        )

    # 2. 자연 성장 (10경기 이상, 성장기=peak 이전 + max 여유 있을 때만)
    if p.get("season_matches",0) >= 10 and p.get("age", 20) < p.get("peak_age", 25):
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
        if cur < mx - 3:
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
                        season_goals_against=0,
                        season_shots=0, season_shots_on=0, season_key_passes=0,
                        season_dribbles=0, season_blocks=0,
                        season_pass_acc_sum=0, season_pass_acc_cnt=0)
    update_player(**stat_updates)

    # 이슈9: 시즌 종료 시 순위 기반 행복도 변화
    _apply_rank_happiness(p, year)

    # 5. 승강제·우승 판정 (강제 방출보다 먼저!)
    #    우승/승격은 '그 시즌에 그 팀 소속이었다'는 사실에 근거해야 한다.
    #    방출/이적을 먼저 처리하면 current_team_id=0이 되어, 리그 1위를 하고도
    #    "내 팀 아님"으로 판정돼 우승·승격 기록이 통째로 누락된다. (순서 버그 수정)
    _sim_all_leagues_for_season_end(p.get("current_season", 1))
    _process_promotion_relegation(year)

    # 6. 강제 방출 체크 (이슈8 강화) — 우승 판정이 끝난 뒤에 처리
    p = get_player() or p   # 승강으로 리그/연봉이 바뀌었을 수 있으니 최신화
    _check_forced_release(p, year)

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
            # 포지션군별 기준선 (수비/GK는 낮게)
            _grp  = POS_GROUP.get(p2.get("position","CM"), "미드")
            _base = RENEW_RATING.get(_grp, 6.3)
            wants_renew = (avg_r >= _base or rel >= 60)
            if wants_renew:
                # 재계약 의사 있음 → UI 팝업용 플래그 저장
                base_sal = p2.get("salary", 0)
                if avg_r >= _base + 0.5:   new_sal = int(base_sal * 1.15)
                elif avg_r >= _base:       new_sal = int(base_sal * 1.05)
                else:                      new_sal = int(base_sal * 0.95)
                # 팀이 계약 기간(1~3년)을 결정한다. 나이·활약 기반:
                #  - 어리고(28세 이하) 잘하면 장기(3년) 제안
                #  - 전성기 지난(31세+) 선수는 단기(1년) 위주
                #  - 그 사이는 활약도로 2~3년
                _age = new_age
                if _age >= 33:
                    renew_yrs = 1
                elif _age >= 31:
                    renew_yrs = random.choices([1, 2], [65, 35])[0]
                elif _age >= 29:
                    renew_yrs = random.choices([1, 2, 3], [25, 50, 25])[0]
                elif _age <= 28 and avg_r >= _base + 0.5:
                    renew_yrs = random.choices([2, 3], [30, 70])[0]   # 유망/핵심 → 장기
                else:
                    renew_yrs = random.choices([1, 2, 3], [15, 45, 40])[0]
                update_player(_contract_renew_offer=new_sal,
                              _contract_renew_years=renew_yrs)
                add_log(f"📋 계약 만료! 팀에서 {renew_yrs}년 재계약을 제안합니다. "
                        f"(제시 연봉: {fmt_money(new_sal)})", "event", year, 52)
            else:
                # 재계약 거절 → 소속 없음
                # (연말 항목은 이미 닫혔으므로 allow_insert=False로 중복 방지)
                _save_career_entry(p2, year, 52, transfer_type="방출",
                                   allow_insert=False, exit_type="계약만료")
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

    # ── 막 합류한 선수 보호 ───────────────────────────────
    # 시즌 후반(예: 25주 이후)에 입단했으면 평가할 표본(출전)이 부족하다.
    # 이런 선수를 방출/판매하면, 입단하자마자 또 쫓겨나는 비정상이 생긴다.
    # (스샷: 45주 입단 → 다음 시즌 시작하자마자 또 이적) → 이번 시즌은 평가 보류.
    try:
        conn0 = get_conn()
        open_row = conn0.execute(
            """SELECT start_year, start_week FROM career_entries
               WHERE team_id=? AND end_year=0
               ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
        conn0.close()
        if open_row and open_row["start_year"] == year and open_row["start_week"] >= 25:
            return   # 올 시즌 후반 합류 → 방출/판매 평가 스킵
    except Exception:
        pass

    rank_str = get_team_rank(tid)
    try:
        rn = int(rank_str.split("위")[0].replace("공동", "").strip())
    except Exception:
        rn = 0

    rc = p.get("season_rating_cnt",0); rs = p.get("season_rating_sum",0.0)
    avg_rating = round(rs/rc,2) if rc > 0 else 6.0

    # 리그 수준 적합성: 팀 평균 OVR과의 격차
    team_avg = _my_team_avg_ovr(p)
    gap = team_avg - p.get("ovr", 40)        # +면 내가 팀 수준에 못 미침
    season_matches = p.get("season_matches", 0)

    cond_ext   = (avg_rating < 5.5 and rc >= 5) or (rel < 20)
    cond_combo = avg_rating < 6.0 and rel < 40 and rc >= 5
    # 수준 미달: 팀보다 많이 낮고 출전도 거의 못 함(벤치 누적)
    cond_level = (gap >= RELEASE_GAP_SOFT and season_matches < RELEASE_GAP_SOFT_MATCH)
    # 심각한 수준 미달: 격차가 너무 크면 평점 무관 방출
    cond_level_hard = (gap >= RELEASE_GAP_HARD)

    # ── [기능2] 감독 성향(release_relax) + [기능1] 구단 야망(press)으로 임계치 조정 ──
    # relax가 +면 잘 안 자르고(뚝심형), -면 가차없다(성과주의).
    # 야망 press가 높으면(우승도전) 기대치가 높아 방출각이 빨라진다.
    from constants import MANAGER_TYPES, OFFER_AMBITION
    mt = MANAGER_TYPES.get(p.get("manager_type", "베테랑 신뢰"), {})
    relax = mt.get("release_relax", 0.0)
    press = OFFER_AMBITION.get(p.get("club_ambition", "중위권 안정"), {}).get("press", 1.0)
    # 평점 기준선을 relax/press로 이동 (관대할수록 낮은 평점도 용인)
    rate_floor_shift = (-relax * 2.0) + ((press - 1.0) * 1.5)
    if rc >= 5:
        eff_rating = avg_rating - rate_floor_shift   # 관대하면 평점을 후하게 본 셈
        cond_ext   = (eff_rating < 5.5) or (rel < 20)
        cond_combo = eff_rating < 6.0 and rel < 40

    # ── 팔림(강제 이적) 체크: 오버페이 + 부진 → 손절 후 하위팀 이적 ──
    # 계약이 남았어도 구단이 손해보면 판다. 무소속이 아니라 수준 맞는 팀으로.
    cur_salary = p.get("salary", 0)
    cur_ovr    = p.get("ovr", 40)
    _glow = _my_grade_tier(p)   # (grade, tier, country)
    if _glow and cur_salary > 0:
        _grade, _tier, _country = _glow
        fair_salary = _calc_salary(_grade, _tier, cur_ovr, _country)
        overpay = (cur_salary / fair_salary) if fair_salary > 0 else 99
        # 오버페이 1.6배+ AND 부진(평점<6.3 또는 수준미달) AND 아직 방출각은 아님
        is_overpaid = overpay >= 1.6
        is_underperforming = (avg_rating < 6.3 and rc >= 5) or cond_level
        contract_left = p.get("contract_end_year", 0) > year
        if is_overpaid and is_underperforming and contract_left and not (cond_ext or cond_level_hard):
            if _try_sell_player(p, year, cur_ovr):
                return   # 팔림 처리 완료 → 방출 로직 건너뜀

    if cond_ext or cond_combo or cond_level or cond_level_hard:
        if rel < 20:
            reason = "감독 관계 악화"
        elif cond_level_hard or cond_level:
            reason = "리그 수준 미달"
        elif avg_rating < 5.5:
            reason = "저조한 평점"
        else:
            reason = "성적 부진"
        # 연말 항목은 _close_career_entry로 이미 닫힘 → allow_insert=False로 중복 방지
        # exit_type='방출'을 직전 팀 항목에 덧칠
        _save_career_entry(p, year, 52, transfer_type="방출", allow_insert=False,
                           exit_type="방출")
        add_log(f"😡 {reason}으로 방출!  {year}년  (평점 {avg_rating}, 감독관계 {rel}, 수준격차 {gap:+.0f})", "event", year, 52)
        update_player(current_team_id=0, current_league_id=0,
                      salary=0, manager_relation=50,
                      contract_years=0, contract_end_year=0)


def _my_grade_tier(p):
    """내 소속 팀의 (국가등급, 리그티어, 국가명) 반환. 무소속이면 None."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    conn = get_conn()
    try:
        row = conn.execute("""SELECT cn.grade as grade, l.tier as tier, cn.name as country
                              FROM teams t JOIN leagues l ON t.league_id=l.id
                              JOIN countries cn ON l.country_id=cn.id
                              WHERE t.id=?""", (tid,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return (row["grade"], row["tier"], row["country"])


def _try_sell_player(p, year, cur_ovr):
    """오버페이+부진 선수를 현재 OVR에 맞는 하위 팀으로 강제 이적(팔림).
    성공 시 True. 적당한 팀을 못 찾으면 False(→ 방출 로직으로)."""
    conn = get_conn(); c = conn.cursor()
    try:
        # 현재 OVR보다 팀 평균이 약간 낮거나 비슷한 팀 (내가 주전급일 수 있는 곳)
        # 현재 리그보다 같거나 한 단계 낮은 수준을 우선
        row = c.execute("""
            SELECT t.id, t.name, l.id as lid, l.name as lname, l.tier,
                   cn.name as country, cn.flag, cn.grade,
                   (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) as tavg
            FROM teams t
            JOIN leagues l ON t.league_id=l.id
            JOIN countries cn ON l.country_id=cn.id
            WHERE t.id != ?
              AND (SELECT AVG(ovr) FROM ai_players WHERE team_id=t.id) BETWEEN ? AND ?
            ORDER BY RANDOM() LIMIT 1
        """, (p.get("current_team_id", 0), cur_ovr - 6, cur_ovr + 2)).fetchone()
        conn.close()
    except Exception:
        try: conn.close()
        except Exception: pass
        return False

    if not row:
        return False

    # 새 팀 연봉 (새 OVR 기준, 리그 부유도 반영)
    new_salary = _calc_salary(row["grade"], row["tier"], cur_ovr, row["country"])

    # (변경) 떠나는 팀에는 우승을 주지 않는다.
    # 우승은 '시즌 종료 시점 소속팀'이 1위일 때만 _process_promotion_relegation에서 인정.
    # 떠나기 전 순위는 team_rank(커리어 기록)에만 남는다.
    # 이전 팀 커리어 항목을 닫음 + 떠난 경로='팔림' 기록
    _save_career_entry(p, year, 52, allow_insert=False, exit_type="팔림")

    # 새 팀으로 이적 (계약은 새로 — 나이/티어 기반)
    age_now = p.get("age", 25)
    c_yrs = _calc_contract_years(age_now, row["tier"])
    c_end = year + c_yrs   # 시즌 종료(52주) 시점이므로 다음 시즌부터 카운트

    global _pending_transfer_type
    _pending_transfer_type = "팔림"
    update_player(current_team_id=row["id"], current_league_id=row["lid"],
                  salary=new_salary, manager_relation=50,
                  contract_years=c_yrs, contract_end_year=c_end,
                  current_tier=row["tier"])
    add_log(f"💸 {row['name']}로 팔림!  {row['lname']}({row['tier']}부)  "
            f"|  연봉 {fmt_money(new_salary)}  (몸값 대비 부진으로 손절)", "event", year, 52)
    return True


def _process_promotion_relegation(year):
    conn = get_conn()
    c = conn.cursor()

    p_row = conn.execute("SELECT current_team_id, current_league_id FROM my_player WHERE id=1").fetchone()
    my_team_id   = p_row["current_team_id"]   if p_row else 0
    my_league_id = p_row["current_league_id"] if p_row else 0

    # 현재 시즌 번호
    ss_row = conn.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    season = ss_row["current_season"] if ss_row else 1

    # ── 우승/승격 귀속 팀 판정 ──────────────────────
    # 규칙: 리그 경기는 35주(하반기 마지막)에 모두 끝난다.
    #   → 35주 시점에 소속이던 팀이 그 시즌 '리그 경기를 끝까지 함께한 팀'.
    #     그 팀이 1위면 우승은 내 것. (36주 이후 이적은 경기 다 뛴 뒤이므로 우승 받고 떠남)
    #   → 35주 전에 떠나 다른 팀에서 시즌을 마친 경우, 떠난 팀이 1위로 끝나도
    #     내 우승이 아님. (예: 1위팀에서 같은 리그 2위팀으로 이적)
    LEAGUE_END_WEEK = 35

    def _team_at_week35():
        """올해 35주 시점 내가 소속이던 팀 id. 없으면 종료 시점 소속팀."""
        try:
            rows = conn.execute(
                """SELECT team_id, start_week, end_week, end_year FROM career_entries
                   WHERE team_id IS NOT NULL AND team_id<>0
                     AND start_year<=? AND (end_year=0 OR end_year>=?)
                   ORDER BY start_week""",
                (year, year)).fetchall()
            for r in rows:
                sw = r["start_week"] or 0
                # 올해 도중 끝난 항목이면 end_week, 아직 진행/연말까지면 52로 간주
                if (r["end_year"] or 0) == 0 or (r["end_year"] or 0) > year:
                    ew = 52
                else:
                    ew = r["end_week"] or 52
                if sw <= LEAGUE_END_WEEK <= ew:
                    return r["team_id"]
        except Exception:
            pass
        return my_team_id

    champ_team_id = _team_at_week35()

    # (참고용) 그 시즌 5경기 이상 뛴 팀 집합 — 승격 '알림' 표시에만 사용
    my_season_teams = set()
    if my_team_id:
        my_season_teams.add(my_team_id)
    try:
        for r in conn.execute(
            """SELECT team_id, matches FROM career_entries
               WHERE start_year<=? AND (end_year=0 OR end_year>=?)""",
            (year, year)).fetchall():
            if r["team_id"] and (r["matches"] or 0) >= 5:
                my_season_teams.add(r["team_id"])
    except Exception:
        pass

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
        # 우승은 '리그 경기 종료(35주) 시점 소속팀'이 1위일 때만 인정.
        # → 경기 다 뛰고 36주+ 에 이적해도 그 팀 우승은 내 것.
        # → 35주 전에 떠나 다른 팀에서 끝냈으면 떠난 팀이 1위여도 내 우승 아님.
        if top1_rows and top1_rows[0]["pts"] > 0 and top1_rows[0]["id"] == champ_team_id:
            champ_tid = top1_rows[0]["id"]
            ci = conn.cursor()
            ci.execute("SELECT t.name, l.name as lname FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?", (champ_tid,))
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
            tl_is_mine = (top_lower["id"] in my_season_teams)
            if tl_is_mine or my_league_id in (upper_lid, lower_lid):
                pending_logs.append((f"🔼 {year}년  {tl_info['name']}  {ntier}부→{tier}부  (승격)", "event"))
            if top_lower["id"] == champ_team_id:   # 35주까지 그 팀 소속이었을 때만 승격 우승 인정
                # 승격 = 하위 리그 우승 → trophy_log에 리그 우승으로 기록
                # (시즌 중 이 팀에서 충분히 뛰었으면, 종료 시점에 떠났어도 우승 인정)
                exist = c.execute(
                    "SELECT id FROM trophy_log WHERE year=? AND team_name=? AND tier=?",
                    (year, tl_info["name"], ntier)).fetchone()
                if not exist:
                    c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                              (year, tl_info["name"], tl_info["lname"], ntier,
                               f"{tl_info['lname']} 우승 ({ntier}부 1위 → {tier}부 승격)"))
                # 단, 내가 '아직 그 팀에 소속'일 때만 함께 1부로 따라 올라간다.
                if top_lower["id"] == my_team_id:
                    my_new_league = upper_lid

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

    # [기능3] 이적 요청 상태면 오퍼 개수 증가
    from constants import TRANSFER_REQUEST_OFFER_BONUS
    transfer_req = bool(p.get("transfer_requested"))
    if transfer_req:
        count += TRANSFER_REQUEST_OFFER_BONUS

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
        # ── [자국 보장] 첫 입단은 자국 리그에서 최소 1~2개는 반드시 온다 ──
        #   현실 반영: 유스 출신은 우선 자국에서 데뷔 제안을 받는다.
        #
        #   [핵심 설계] '내 평균 수준으로 어느 티어가 입단 가능한지'를 먼저 판정하고,
        #   그 가능한 티어들 중에서만 1부10%/2부30%/3부60% 비중으로 뽑는다.
        #     - 이탈리아처럼 1부가 매우 높은 자국이면, 17세 신인은 1·2부가 수준
        #       미달이라 애초에 후보에서 빠지고 3부만 가능 → 사실상 100% 3부.
        #     - 한국처럼 1부가 약하면 1부도 후보에 들어 10% 확률로 1부 데뷔 가능.
        #   판정 기준은 일반 슬롯과 동일한 _team_fits_me (팀 평균 OVR - 내 OVR ≤ 8).
        guarantee = random.choice([1, 2])

        def _tier_fittable(tier) -> bool:
            """자국 해당 티어에 '내 수준에 맞는' 팀이 하나라도 존재하면 True.
               (가장 약한 팀 기준: 그 리그 최저 팀 평균 OVR이 내 +8 이내면 가능)"""
            c2 = conn.cursor()
            c2.execute("""SELECT MIN(ta.avg_ovr) AS min_avg
                          FROM teams t
                          JOIN leagues l ON t.league_id=l.id
                          JOIN (SELECT team_id, AVG(ovr) AS avg_ovr
                                  FROM ai_players GROUP BY team_id) ta
                                ON ta.team_id=t.id
                          WHERE l.country_id=? AND l.tier=?""",
                       (my_country_id, tier))
            r = c2.fetchone()
            if not r or r["min_avg"] is None:
                # 그 티어 자체가 자국에 없거나 선수 데이터 없음 → 후보 아님
                return False
            return (r["min_avg"] - ovr) <= 8

        def _try_domestic(tier, relax=False):
            """자국 특정 티어에서 '수준 맞는' 팀 1개 탐색.
               relax=True면 _team_fits_me 무시하고 아무 팀이나(데뷔 보장용)."""
            if relax:
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.id=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 1""", (my_country_id, tier))
                row = c.fetchone()
                if not row: return False
            else:
                # 팀 평균 OVR - 내 OVR <= 8 인 팀들 중에서만 무작위 1팀.
                #   (_tier_fittable 와 동일 기준으로, 판정-선택 불일치를 없앤다)
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             JOIN (SELECT team_id, AVG(ovr) AS avg_ovr
                                     FROM ai_players GROUP BY team_id) ta
                                   ON ta.team_id=t.id
                             WHERE cn.id=? AND l.tier=? AND (ta.avg_ovr - ?) <= 8
                             ORDER BY RANDOM() LIMIT 1""", (my_country_id, tier, ovr))
                row = c.fetchone()
                if not row: return False
            if any(o["team_id"] == row["id"] for o in offers): return False
            if row["id"] == my_tid: return False
            grade  = random.choice(grades)
            salary = int(_calc_salary(row["grade"], tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
            offers.append(_build_offer(row, grade, tier, salary))
            return True

        # [1단계] 내 수준으로 가능한 자국 티어 확정.
        #   force_tier3(저능력 17세)이면 무조건 3부만.
        if force_tier3:
            fittable = [3] if _tier_fittable(3) else []
        else:
            fittable = [t for t in (1, 2, 3) if _tier_fittable(t)]

        # [2단계] 가능 티어들 중에서 1부10/2부30/3부60 비중으로 뽑아 슬롯 채움.
        TIER_W = {1: 10, 2: 30, 3: 60}
        for _ in range(guarantee):
            placed = False
            if fittable:
                # 가능 티어만 남긴 가중치로 뽑기 → 매 시도 새로 뽑아 비중 유지
                for _ in range(8):
                    weights = [TIER_W[t] for t in fittable]
                    pick_tier = random.choices(fittable, weights)[0]
                    if _try_domestic(pick_tier):
                        placed = True; break
            # [예외 보강] 가능 티어가 없거나(자국 1·2·3부 모두 수준 초과) 못 채웠으면
            #   3부에서 기준 완화해서라도 데뷔 기회 1개는 보장.
            if not placed:
                for _ in range(10):
                    if _try_domestic(3, relax=True):
                        placed = True; break
            # 자국에 3부 리그 자체가 없으면 더는 강제하지 않음

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
            salary = int(_calc_salary(row["grade"], tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
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
                salary = int(_calc_salary(row["grade"], tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
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
            # 티어 가중치: OVR 기반(성장 시 상위 리그로 이동) + 현재 티어를 약간 가산
            tier_weights = list(tier_weights_by_ovr(ovr))
            if my_current_tier == 1:   tier_weights[0] += 15
            elif my_current_tier == 2: tier_weights[1] += 15
            else:                      tier_weights[2] += 15

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
                if not _team_fits_me(row): continue   # 내 OVR과 너무 차이나는 팀 제외
                salary = int(_calc_salary(row["grade"], tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
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
                if not _team_fits_me(row): continue   # 내 OVR과 너무 차이나는 팀 제외
                salary = int(_calc_salary(row["grade"], tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
                offers.append(_build_offer(row, row["grade"], tier, salary))
                break

        while len(offers) < count and tried < 120:
            tried += 1
            grade = random.choice(grades)
            tier  = 3 if force_tier3 else random.choices([1, 2, 3], tier_weights_by_ovr(ovr))[0]
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
            salary = int(_calc_salary(row["grade"], tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
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

    # [기능3] 이적 요청 플래그 소비 (오퍼가 생성됐으면 리셋)
    if transfer_req and offers:
        update_player(transfer_requested=0)

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
    # 포지션군별 평점 기준선: 수비/GK는 기대평점이 낮으므로 기준점을 낮춰 공정화
    pos_grp = POS_GROUP.get(p.get("position","CM"), "미드")
    baseline = RENEW_RATING.get(pos_grp, 6.3)
    # baseline을 5.0 위치로 매핑 → 기준선 이상이면 양수 점수
    r_s = max(0.0, min(1.0, (rating - (baseline - 1.0)) / 4.0))
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
    o = dict(
        team_id=row["id"], team_name=row["name"],
        league_id=row["lid"], league_name=row["lname"],
        tier=row["tier"], country=row["country"],
        flag=row["flag"], grade=grade, salary=salary,
    )
    _enrich_offer(o, row)
    return o


def _enrich_offer(o: dict, row) -> dict:
    """[기능1] 오퍼에 역할/감독관심도/구단야망/계약보너스/감독성향 부여.

    역할은 '팀 평균 OVR - 내 OVR' 격차로 결정한다:
      - 내가 팀 수준을 크게 상회 → 주전 보장
      - 비슷 → 주전 경쟁
      - 내가 부족 → 로테이션 / (어리면)유망주 영입
    """
    from constants import (OFFER_ROLES, OFFER_INTEREST, OFFER_AMBITION,
                           offer_bonus_by_tier, MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS)
    p = get_player() or {}
    my_ovr = p.get("ovr", 40)
    my_age = p.get("age", 17)

    # 팀 평균 OVR
    conn = get_conn(); c = conn.cursor()
    r = c.execute("SELECT AVG(ovr) AS a FROM ai_players WHERE team_id=?", (row["id"],)).fetchone()
    conn.close()
    team_avg = r["a"] if r and r["a"] else my_ovr
    gap = team_avg - my_ovr   # +면 내가 팀 수준에 못 미침

    # 역할 결정
    if gap <= -6:
        role = "주전 보장"
    elif gap <= 3:
        role = "주전 경쟁"
    else:
        role = "유망주 영입" if my_age <= 21 else "로테이션"
    o["role"] = role

    # 감독 관심도 (가중 랜덤, 단 주전보장이면 직접지명 확률↑)
    int_keys = list(OFFER_INTEREST.keys())
    int_w = [OFFER_INTEREST[k]["weight"] for k in int_keys]
    if role == "주전 보장":
        int_w[int_keys.index("감독 직접 지명")] += 30
    o["interest"] = random.choices(int_keys, int_w)[0]

    # 구단 야망 (가중 랜덤, 상위 등급 리그는 우승/상위권↑)
    amb_keys = list(OFFER_AMBITION.keys())
    amb_w = [OFFER_AMBITION[k]["weight"] for k in amb_keys]
    if o.get("grade") in ("S", "A"):
        amb_w[amb_keys.index("우승 도전")] += 15
        amb_w[amb_keys.index("상위권 도전")] += 10
    o["ambition"] = random.choices(amb_keys, amb_w)[0]

    # 계약 보너스
    bonus = offer_bonus_by_tier(o["tier"])
    o["appearance_bonus_k"] = bonus["appearance_bonus_k"]
    o["goal_bonus_k"] = bonus["goal_bonus_k"]

    # 감독 성향 (입단 전 미리 노출 → 결정에 활용)
    o["manager_type"] = random.choices(MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS)[0]
    return o


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
    """OVR로 자연스러운 리그 등급대를 정하고, 에이전트 등급에 따라
    '상위 리그 오퍼 +N'을 실제로 적용한다 (AGENT_UPPER_LEAGUE_BONUS).
    좋은 에이전트일수록 실력보다 높은 등급 리그의 오퍼까지 끌어온다."""
    order = ["F","E","D","C","B","A","S"]
    if ovr >= 85: base = ["S","A"]
    elif ovr >= 75: base = ["A","B"]
    elif ovr >= 65: base = ["B","C"]
    elif ovr >= 55: base = ["C","D"]
    elif ovr >= 45: base = ["D","E"]
    else: base = ["E","F"]

    # 에이전트 상위리그 보너스: 현재 등급대의 최상위에서 N단계 위까지 추가
    from constants import AGENT_UPPER_LEAGUE_BONUS
    bonus = AGENT_UPPER_LEAGUE_BONUS.get(agent, 0)
    if bonus > 0:
        top_i = max(order.index(g) for g in base)
        for step in range(1, bonus + 1):
            ni = top_i + step
            if ni < len(order) and order[ni] not in base:
                base.append(order[ni])

    # F급 에이전트는 하위 리그만 (상위 오퍼 못 따옴)
    if agent == "F":
        base = [g for g in base if g in ["E","F"]] or ["F"]
    return base


def _salary_ovr_mult(ovr):
    """OVR → 연봉 배수. 상단으로 갈수록 가파르게 (현실의 슈퍼스타 프리미엄).
    OVR50=0.25배, 70=1.4배, 85=4.1배, 90=5.4배, 99=8.5배.
    → OVR99 톱리그(S급1부)면 약 600억(메시·음바페급)에 도달."""
    t = max(0.0, (ovr - 40) / 59.0)
    return 0.12 + (t ** 2.8) * 8.4


def _calc_salary(grade, tier, ovr, country=None):
    # 연봉 base (천원 단위). 천장을 현실적으로 상향.
    # 하위 리그도 0이 아니라 아주 적게라도 받음(말라위 등). 진짜 무급은 극소수만.
    # country가 주어지면 '리그 부유도' 오버라이드 적용 (사우디=오일머니 등).
    wealth = LEAGUE_WEALTH_OVERRIDE.get(country, grade) if country else grade
    base_year = {
        "S":{1:7000000, 2:1300000, 3:160000},
        "A":{1:3200000, 2:640000,  3:80000},
        "B":{1:1300000, 2:260000,  3:40000},
        "C":{1:520000,  2:120000,  3:16000},
        "D":{1:210000,  2:48000,   3:5500},
        "E":{1:80000,   2:18000,   3:1600},
        "F":{1:26000,   2:5200,    3:400},   # F3 base=400천원, OVR 따라 변동
    }
    b = base_year.get(wealth, {}).get(tier, 100)
    sal = int(b * _salary_ovr_mult(ovr))
    # 극소수 진짜 무급: 최하위 리그(F3)에서 OVR이 매우 낮은 무명만
    if wealth == "F" and tier == 3 and ovr < 38:
        return 0
    return max(0, sal)


def _save_career_entry(p, year, week, force_new=False, transfer_type=None,
                       allow_insert=True, exit_type=""):
    """커리어 기록 업데이트.
    force_new=True: 이전 팀 기록 확정 (end_year 채움)
    force_new=False: 시즌 종료 시 현재 팀 기록 업데이트
    allow_insert=False: 열린 항목이 없으면 아무것도 하지 않음
        (연말 _close_career_entry로 이미 닫힌 뒤 방출/재계약 거절 시
         유령 중복 행이 생기는 것 방지)
    exit_type: 그 팀에서 떠난 경로('팔림'/'방출'/'이적'/'계약만료'/''=재직중).
        이미 닫힌 항목이어도 exit_type이 있으면 그 행에 덧칠한다.
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
            league_name=?, tier=?, salary=?, position=?, team_id=?, exit_type=?
            WHERE id=?""",
            (year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl, cs,
             team_row["lname"], team_row["tier"], p.get("salary", 0), pos, tid,
             exit_type, existing["id"]))
    elif not allow_insert:
        # 이미 닫힌 항목만 존재 → 중복 행은 안 만들되, 떠난 경로(exit_type)는
        # 가장 최근에 닫힌 그 팀 항목에 덧칠해 준다 (방출/팔림 표시 누락 방지).
        if exit_type:
            closed = c.execute("""SELECT id FROM career_entries
                WHERE team_id=? AND end_year>0
                ORDER BY end_year DESC, end_week DESC, id DESC LIMIT 1""",
                (tid,)).fetchone()
            if closed:
                c.execute("UPDATE career_entries SET exit_type=? WHERE id=?",
                          (exit_type, closed["id"]))
                conn.commit()
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
             contract_years, transfer_type, clean_sheets, team_id,
             contract_role, manager_type, club_ambition, exit_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["age"], pos, team_row["name"], team_row["lname"], saved_tier,
             p.get("salary", 0), cur_year, cur_week,
             year, week, sm, sg, sa, ss, sga, avg_r, rn, tw, td, tl,
             c_yrs_save, pending_tt, cs, tid,
             p.get("contract_role",""), p.get("manager_type",""), p.get("club_ambition",""),
             exit_type))

    conn.commit()
    conn.close()


def mark_contract_extension(yrs: int):
    """재계약(연장) 수락 시 호출. 연장이 발동된 '현재 시즌'의 열린 커리어
    항목(end_year=0)에 transfer_type='연장'과 연장 연수를 박는다.

    연장은 계약 만료 다음 해 1~4주차에 발동되므로, 그해의 줄에
    '계약: N년 / 이적: 연장'이 표시된다. 열린 항목이 아직 없으면
    (드물게 줄 생성 전 시점) _pending_transfer_type='연장'만 세팅된 상태로
    다음 _ensure_career_entry가 정상 생성한다.
    """
    p = get_player()
    if not p:
        return
    global _pending_transfer_type
    tid = p.get("current_team_id", 0)
    if not tid:
        return
    update_player(contract_years=yrs)
    conn = get_conn()
    c = conn.cursor()
    team_row = c.execute("SELECT name FROM teams WHERE id=?", (tid,)).fetchone()
    tname = team_row["name"] if team_row else ""
    existing = _find_open_entry(c, tid, tname)
    if existing:
        c.execute("""UPDATE career_entries
                     SET transfer_type='연장', contract_years=?
                     WHERE id=?""", (yrs, existing["id"]))
        conn.commit()
        # 열린 줄에 직접 박았으므로 다음 시즌 줄에 '연장'이 잔류하지 않도록
        # _pending_transfer_type을 대기값('')으로 되돌린다. (연장은 발동된 그 해만 표시)
        _pending_transfer_type = ""
    else:
        # 드물게 열린 줄이 아직 없으면, 다음 _ensure_career_entry가 만들 줄에
        # '연장'이 들어가도록 플래그만 세팅. (그 _ensure가 소비)
        _pending_transfer_type = "연장"
    conn.close()


def join_team(team_id, salary, transfer_type: str = "입단", offer: dict = None):
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
        prev_tid = p["current_team_id"]
        # (변경) 시즌 중 이적 시 이전 팀에 우승을 주지 않는다.
        # 우승은 시즌 종료 시점 소속팀이 1위일 때만 인정된다.
        # 떠난 경로: 계약이 아직 남았는데 옮기면 '이적', 만료됐으면 '계약만료'
        prev_end = p.get("contract_end_year", 0)
        exit_t = "계약만료" if (prev_end and prev_end <= cur_year) else "이적"
        _save_career_entry(p, cur_year, cur_week, force_new=True, exit_type=exit_t)
        # 새 팀 스탯 초기화
        update_player(season_matches=0, season_goals=0, season_assists=0,
                      season_saves=0, season_rating_sum=0.0, season_rating_cnt=0,
                      season_goals_against=0)
        # [에이전트 익스플로잇 차단] 이적 시 개별 협상 수수료(agent_fee_rate)를
        #   리셋한다. 예전엔 약소국·저연봉 시절 헐값에 잡은 낮은 수수료율이
        #   이적 후 폭등한 연봉에도 평생 고정 적용됐다. 이제 이적하면 그 특혜가
        #   사라지고, 다음 급여부터는 에이전트 '등급 기본 수수료'로 돌아간다
        #   (새 계약엔 새 조건). 재계약 원하면 에이전트 창에서 다시 협상.
        if p.get("agent_fee_rate", 0):
            update_player(agent_fee_rate=0)
            try:
                _ag = p.get("agent_grade", "F")
                _base_fee = AGENT_FEE_RATE.get(_ag, 0.0)
                add_log(f"📑 이적으로 에이전트 계약 갱신 — 수수료 {int(_base_fee*100)}%(등급 기본)로 조정", "event")
            except Exception:
                pass

    age_jt = p.get("age",17) if p else 17
    c_yrs  = _calc_contract_years(age_jt, row["tier"])
    # 계약 만료 연도 (만료는 해당 연도 52주차)
    #  - 시즌 초(1~4주) 계약: 올해가 1년차 → cur_year + c_yrs - 1
    #    예: 2008년 2주 1년계약 → 2008년 52주 만료
    #  - 시즌 중(5주~) 계약: 올해는 미포함, 다음 시즌부터 카운트 → cur_year + c_yrs
    #    예: 2008년 37주 1년계약 → 2009년 52주 만료
    if cur_week <= 4:
        c_end = cur_year + c_yrs - 1
    else:
        c_end = cur_year + c_yrs
    global _pending_transfer_type
    _pending_transfer_type = transfer_type

    # ── [기능1+2] 오퍼 맥락 반영 ─────────────────────────────
    from constants import OFFER_ROLES, OFFER_INTEREST, MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS
    role        = (offer or {}).get("role", "주전 경쟁")
    interest    = (offer or {}).get("interest", "명단 후보")
    ambition    = (offer or {}).get("ambition", "중위권 안정")
    mgr_type    = (offer or {}).get("manager_type") or random.choices(MANAGER_TYPE_LIST, MANAGER_TYPE_WEIGHTS)[0]
    app_bonus   = (offer or {}).get("appearance_bonus_k", 0)
    goal_bonus  = (offer or {}).get("goal_bonus_k", 0)

    # 입단 시 감독관계 초기값 = 역할 기본값 + 관심도 보너스
    rel_init = OFFER_ROLES.get(role, {}).get("rel_init", 50)
    rel_init += OFFER_INTEREST.get(interest, {}).get("rel_bonus", 0)
    rel_init = max(0, min(100, rel_init))

    update_player(current_team_id=team_id, current_league_id=row["lid"],
                  salary=salary, manager_relation=rel_init,
                  contract_years=c_yrs, contract_end_year=c_end,
                  current_tier=row["tier"],
                  contract_role=role, club_ambition=ambition,
                  manager_type=mgr_type,
                  appearance_bonus_k=app_bonus, goal_bonus_k=goal_bonus,
                  transfer_requested=0)
    icon = {"입단":"⭐","오퍼":"✈","방출":"😡"}.get(transfer_type,"⭐")
    add_log(f"{icon} {row['name']} {transfer_type}!  {row['lname']}({row['tier']}부)"
            f"  |  {c_yrs}년 계약  |  월 {fmt_money(salary//12)}", "event")
    add_log(f"   ↳ 역할: {role} | 감독: {mgr_type} | 구단 목표: {ambition} | 관심: {interest}", "normal")

    # 새 팀 커리어 항목은 첫 4주 진행 시 생성 (advance_4weeks에서 처리)
    # 즉시 생성하면 입단 즉시 1~0/0주 같은 이상한 기록이 남음

    # 새 리그 일정 생성 (내 리그 + 인접 리그)
    _generate_adjacent_schedules(row["lid"], st["current_season"], st["current_year"])

    # 이적 시점 이전에 이미 지나간 주차의 미완료 경기를 일괄 시뮬
    _backfill_past_matches(row["lid"], st["current_season"], cur_week, team_id)

    # teams 테이블을 match_results 기준으로 재동기화
    # (오퍼 창 _sim_league_full이 match_results만 채우고 teams를 건드리지 않아서)
    _sync_teams_from_results(row["lid"], st["current_season"])


def request_transfer() -> dict:
    """[기능3] 이적 요청. 감독관계 하락을 감수하고 다음 오퍼 창을 활성화.

    반환 dict (UI/추후 Godot 공용):
      {"ok": bool, "msg": str, "manager_relation": int}
    """
    from constants import TRANSFER_REQUEST_REL_PENALTY
    p = get_player()
    if not p or not p.get("current_team_id"):
        return {"ok": False, "msg": "소속팀이 없습니다.", "manager_relation": 0}
    if p.get("transfer_requested"):
        return {"ok": False, "msg": "이미 이적을 요청한 상태입니다.",
                "manager_relation": p.get("manager_relation", 50)}

    rel = max(0, p.get("manager_relation", 50) - TRANSFER_REQUEST_REL_PENALTY)
    update_player(manager_relation=rel, transfer_requested=1)
    add_log(f"📣 이적 요청! 감독과의 관계가 악화됐다. (관계 {rel})", "event")
    return {"ok": True,
            "msg": f"이적을 요청했습니다. 다음 이적시장에서 더 많은 오퍼가 들어옵니다.\n"
                   f"감독 관계가 {TRANSFER_REQUEST_REL_PENALTY} 하락했습니다.",
            "manager_relation": rel}


def negotiate_renewal() -> dict:
    """[기능3] 재계약 협상. 평점·감독관계 기반 성공 확률로 연봉 인상+계약 연장.

    반환 dict:
      {"ok": bool, "success": bool, "msg": str,
       "old_salary": int, "new_salary": int, "manager_relation": int}
    """
    from constants import RENEW_NEGOTIATE
    p = get_player()
    if not p or not p.get("current_team_id"):
        return {"ok": False, "success": False, "msg": "소속팀이 없습니다.",
                "old_salary": 0, "new_salary": 0, "manager_relation": 0}

    rc = p.get("season_rating_cnt", 0); rs = p.get("season_rating_sum", 0.0)
    avg_rating = round(rs / rc, 2) if rc > 0 else 6.5
    rel = p.get("manager_relation", 50)
    cfg = RENEW_NEGOTIATE

    prob = (cfg["base_prob"]
            + (avg_rating - 6.5) * cfg["rating_per_point"]
            + ((rel - 50) / 10.0) * cfg["rel_per_10"])
    prob = max(0.05, min(0.95, prob))

    old_salary = p.get("salary", 0)
    st = get_state()
    cur_year = st["current_year"] if st else p.get("current_year", 1990)

    if random.random() < prob:
        lo, hi = cfg["raise_success"]
        raise_pct = random.uniform(lo, hi)
        new_salary = int(old_salary * (1 + raise_pct))
        new_end = max(p.get("contract_end_year", cur_year), cur_year) + cfg["extend_years"]
        new_rel = min(100, rel + 5)
        update_player(salary=new_salary, contract_end_year=new_end,
                      manager_relation=new_rel)
        add_log(f"🤝 재계약 성공! 연봉 +{int(raise_pct*100)}% "
                f"(월 {fmt_money(new_salary//12)}), {cfg['extend_years']}년 연장 "
                f"(~{new_end})", "event")
        return {"ok": True, "success": True,
                "msg": f"재계약 성공! 연봉이 {int(raise_pct*100)}% 인상되고 "
                       f"{cfg['extend_years']}년 연장됐습니다.",
                "old_salary": old_salary, "new_salary": new_salary,
                "manager_relation": new_rel}
    else:
        new_rel = max(0, rel + cfg["raise_fail_rel"])
        update_player(manager_relation=new_rel)
        add_log(f"🚫 재계약 협상 결렬. 구단이 인상안을 거절했다. (관계 {new_rel})", "event")
        return {"ok": True, "success": False,
                "msg": "구단이 인상안을 거절했습니다. 감독 관계가 소폭 하락했습니다.",
                "old_salary": old_salary, "new_salary": old_salary,
                "manager_relation": new_rel}


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