# game_engine.py
import random
import math
import json
import intl_engine
import champions_engine
from match_sim import match_flow
from match_sim import tactical_engine
from database import (get_conn, calc_ovr, ALL_STATS,
                      rescale_team_to_target_ovr, get_league_avg_ovr,
                      get_league_strong_ovr)
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
# 리그 tier 캐시: leagues.tier 는 게임 중 변하지 않는 세션 상수.
_league_tier_cache: dict = {}
# 팀 이름 캐시: teams.name 은 세션 내 불변. _write_match_log 등에서 매번 SELECT 방지.
_team_name_cache: dict = {}

def _invalidate_team_ovr_cache():
    """ai_players OVR/소속이 일괄 변경되는 경우(리맵·신규 시드·승강) 호출.
    포메이션 위젯의 선수 목록 캐시도 함께 비운다.
    (승강 후 rescale로 AI OVR이 바뀌어도 위젯 캐시가 남아 있으면
     구 티어 OVR이 그대로 표시되는 버그 방지.)
    """
    _team_ovr_cache.clear()
    _league_ovr_cache.clear()
    _league_tier_cache.clear()
    _team_formation_cache.clear()
    # 승강으로 팀이 다른 리그로 이동해도 팀명은 안 바뀌므로 _team_name_cache는 비우지 않음

    # 포메이션 위젯 선수 목록 캐시 무효화
    # FormationWidget 인스턴스를 직접 참조하지 않고, 모듈 속성으로 플래그 세팅.
    # formation_widget.py의 load_my_team이 이 플래그를 보고 캐시를 무시한다.
    try:
        import ui.formation_widget as _fw
        _fw._ovr_cache_invalidated = True
    except Exception:
        pass

def _team_name(c, team_id, default="팀") -> str:
    """팀 이름 조회 (세션 캐시). c=열린 커서 재사용."""
    cached = _team_name_cache.get(team_id)
    if cached is not None:
        return cached
    row = c.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
    val = row["name"] if row else default
    _team_name_cache[team_id] = val
    return val


def _league_tier(c, league_id, default=3):
    """리그 tier 조회 (세션 캐시). c=열린 커서 재사용."""
    if not league_id:
        return default
    cached = _league_tier_cache.get(league_id)
    if cached is not None:
        return cached
    row = c.execute("SELECT tier FROM leagues WHERE id=?", (league_id,)).fetchone()
    val = row["tier"] if row else default
    _league_tier_cache[league_id] = val
    return val


# ═══════════════════════════════════════════
# 유틸
# ═══════════════════════════════════════════

def fmt_money(amount_k: int) -> str:
    """천원 단위 정수 → 표시 문자열. (예: 1=1천원, 10000=1천만원, 100000=1억)
    amount_k <= 0 이면 "무급"이 아닌 "0원"을 반환한다.
    진짜 무급(salary==0)은 호출부에서 별도 처리할 것.
    """
    if amount_k < 0:
        return "0원"
    if amount_k == 0:
        return "0원"
    won = amount_k * 1000
    if won >= 1000000000000:    # 1조 이상
        jo  = won // 1000000000000
        eok = (won % 1000000000000) // 100000000
        if eok:
            return f"{jo:,}조 {eok:,}억원"
        return f"{jo:,}조원"
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
    if "ovr" in kw and "peak_ovr" not in kw:
        # [전성기 OVR] ovr이 바뀔 때마다 역대 최고치를 peak_ovr에 함께 기록.
        #   read-before-write 없이 SQL의 max()로 원자적 처리(추가 왕복 없음).
        sets = ",".join(f"{k}=?" for k in kw) + ", peak_ovr=MAX(COALESCE(peak_ovr,0), ?)"
        vals = list(kw.values()) + [kw["ovr"]]
    else:
        sets = ",".join(f"{k}=?" for k in kw)
        vals = list(kw.values())
    c.execute(f"UPDATE my_player SET {sets} WHERE id=1", vals)
    conn.commit()
    conn.close()


def get_field_pos(p=None):
    """현재 팀 포메이션 기반으로 배치 포지션 런타임 계산.
    DB에 저장하지 않고 호출할 때마다 계산 → 포메이션 변경 즉시 반영.
    """
    if p is None:
        p = get_player()
    if not p:
        return "CM"
    primary = p.get("position", "CM")
    team_id = p.get("current_team_id", 0)
    if not team_id:
        return primary
    try:
        from constants import POSITION_COMPAT, FORMATION_SLOTS
        conn = get_conn()
        row = conn.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
        conn.close()
        formation = (row["formation"] if row else None) or "4-4-2"
        slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
        compat = POSITION_COMPAT.get(primary, [primary])
        best, best_rank = primary, 999
        for slot in slots:
            if slot in compat:
                rank = compat.index(slot)
                if rank < best_rank:
                    best_rank = rank
                    best = slot
        return best
    except Exception:
        return primary



# ═══════════════════════════════════════════
# [국적 연혁] 출생국적 / 귀화 / 대표선택 이력 기록
# ═══════════════════════════════════════════
# nat_history 컬럼(JSON list)에 국적 관련 사건을 시간순으로 누적한다.
#   type: "birth"(출생 보유) / "naturalize"(귀화 획득) / "commit"(대표 확정)
#   각 항목: {"type","nat","flag","year","week"}
# 은퇴 AI요약에서 "라오스 출생 → 1994년 포르투갈 귀화 → 1996년 포르투갈 대표 선택"
# 같은 연혁을 재구성하는 데 쓴다.

def get_nat_history(p=None):
    """국적 연혁 리스트 반환 (없으면 빈 리스트)."""
    if p is None:
        p = get_player()
    if not p:
        return []
    raw = p.get("nat_history", "") or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def add_nat_history(ev_type, nat, flag="", year=None, week=None, p=None):
    """국적 연혁에 사건 1건 추가. 같은 (type, nat)이 이미 있으면 중복 추가 안 함.
    year/week 생략 시 현재 시즌 상태에서 자동으로 채운다."""
    if not nat:
        return
    if p is None:
        p = get_player()
    if not p:
        return
    if year is None or week is None:
        st = get_state() or {}
        if year is None:
            year = st.get("current_year", GAME_START_YEAR)
        if week is None:
            week = st.get("current_week", 1)
    hist = get_nat_history(p)
    for h in hist:
        if h.get("type") == ev_type and h.get("nat") == nat:
            return   # 중복 방지 (대표선택은 1회뿐이지만 안전하게)
    hist.append({"type": ev_type, "nat": nat, "flag": flag or "",
                 "year": year, "week": week})
    update_player(nat_history=json.dumps(hist, ensure_ascii=False))


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


# ── 로그 버퍼: add_log 호출을 모아 flush_log_buffer()로 한 번에 INSERT ──
# 한 주차 진행 중 8~12회 add_log가 각각 get_state+INSERT+commit을 했던 것을
# 메모리에 쌓아뒀다가 advance_4weeks 루프 끝에서 1회 커밋으로 처리한다.
# year/week를 None으로 남기면 flush 시점에 실제 상태로 채운다.
_log_buffer: list = []  # [(text, log_type, year_or_None, week_or_None)]

def add_log(text: str, log_type="normal", year=None, week=None):
    """로그를 버퍼에 추가. flush_log_buffer()로 실제 DB에 기록."""
    _log_buffer.append((text, log_type, year, week))

def flush_log_buffer():
    """버퍼에 쌓인 로그를 한 번의 executemany + commit으로 DB에 기록."""
    if not _log_buffer:
        return
    st = get_state()
    cur_y = st["current_year"]
    cur_w = st["current_week"]
    rows = [(text, ltype,
             y if y is not None else cur_y,
             w if w is not None else cur_w)
            for text, ltype, y, w in _log_buffer]
    _log_buffer.clear()
    conn = get_conn()
    conn.executemany("INSERT INTO game_log(entry,log_type,year,week) VALUES(?,?,?,?)", rows)
    conn.commit()
    conn.close()


def get_logs():
    flush_log_buffer()  # 버퍼에 남은 로그 먼저 기록
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT entry FROM game_log ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    return [r["entry"] for r in rows]


def get_match_detail(detail_id):
    """match_details 단건 조회 → dict(파싱된 detail_json 포함) 반환. 없으면 None."""
    try:
        conn = get_conn()
        row = conn.execute("SELECT * FROM match_details WHERE id=?",
                           (int(detail_id),)).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    d = dict(row)
    try:
        d["payload"] = json.loads(d.get("detail_json") or "{}")
    except Exception:
        d["payload"] = {}
    # [신규] possession_log는 detail_json과 별개의 컬럼으로 저장했지만
    # (이유: 경기당 통째로 쓰고 읽는 구조화 데이터라 JSON 컬럼이 더
    # 맞음), 소비하는 쪽(match_sim_viewer.py)은 다른 필드들처럼 그냥
    # payload 하나만 보면 되게 여기서 합쳐준다. 구버전 경기(컬럼 자체가
    # 비어있음)는 빈 리스트 — 뷰어가 자동으로 기존 사후-추측 로직으로
    # 폴백한다.
    try:
        d["payload"]["possession_log"] = json.loads(d.get("possession_log") or "[]")
    except Exception:
        d["payload"]["possession_log"] = []
    # [신규] lineup_stats도 possession_log와 같은 방식으로 payload에
    # 합쳐준다 — match_sim_viewer.py는 payload 하나만 보면 된다.
    try:
        d["payload"]["lineup_stats"] = json.loads(d.get("lineup_stats") or "{}")
    except Exception:
        d["payload"]["lineup_stats"] = {}
    return d


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
                  nationality: str = None, flag: str = None, talent_tier: str = None):
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

    # 재능 등급 (worldclass/elite/pro/semipro/ordinary) → 고강도 돌파 상한 결정.
    # [신규] 새 게임 화면에서 선수가 직접 등급을 고를 수 있게 됐다 — talent_tier가
    # 유효한 값으로 넘어오면 그걸 그대로 쓰고, 없거나(None) 잘못된 값이면
    # (구버전 호출부 호환) 예전처럼 확률 추첨으로 정한다.
    if talent_tier not in TALENT_TIERS:
        _r = random.random()
        _acc = 0.0
        talent_tier = "pro"
        for _tname in ("worldclass", "elite", "pro", "semipro", "ordinary"):
            _acc += TALENT_TIERS[_tname]["prob"]
            if _r < _acc:
                talent_tier = _tname
                break
    _tt = TALENT_TIERS[talent_tier]
    talent_cap = random.randint(_tt["cap_min"], _tt["cap_max"])

    # 피크 나이: 재능이 클수록 잠재력을 다 끌어내는 데 오래 걸려 늦게 정점에
    #   도달한다(월클 25~27, 평범 19~21). 성장기(16~peak)가 길수록 천천히 오른다.
    _peak_by_tier = {
        "worldclass": (25, 27),
        "elite":      (23, 25),
        "pro":        (22, 24),
        "semipro":    (20, 22),
        "ordinary":   (19, 21),
    }
    peak_age = random.randint(*_peak_by_tier.get(talent_tier, (22, 24)))

    # 시작 스탯(16세) + 일반훈련 천장(max). max는 talent_cap을 넘지 않음.
    #   16세 시작은 낮게 잡아 20대 중반까지 천천히 성장하도록 한다.
    #   (성장 페이스는 _age_train_eff 에이징커브가 함께 결정)
    if talent_tier == "worldclass":
        target = random.randint(42, 50); dev = random.randint(7, 11)
        mx_add = (44, 58)
    elif talent_tier == "elite":
        target = random.randint(39, 47); dev = random.randint(8, 12)
        mx_add = (40, 54)
    elif talent_tier == "pro":
        target = random.randint(36, 43); dev = random.randint(9, 13)
        mx_add = (34, 48)
    elif talent_tier == "semipro":
        target = random.randint(32, 39); dev = random.randint(10, 14)
        mx_add = (28, 42)
    else:  # ordinary
        target = random.randint(28, 35); dev = random.randint(11, 16)
        mx_add = (22, 36)

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
        15,50,'F','ko',
        ?,?,?,?
    )""", (
        name, nationality, flag, PLAYER_START_AGE, GAME_START_YEAR - PLAYER_START_AGE,
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

    # [출생국적] 태어난 고향(=1차 국적)을 별도로 영구 보존. 귀화/대표선택과 무관.
    #   디에고 코스타처럼 '출생국 ≠ 대표국'을 은퇴요약에서 구분하기 위함.
    conn.execute("UPDATE my_player SET origin_nat=?, origin_flag=? WHERE id=1",
                 (nationality, flag))

    # [전성기 OVR] 시작 OVR로 초기화 (이후 update_player가 자동으로 최고치 갱신).
    conn.execute("UPDATE my_player SET peak_ovr=? WHERE id=1", (ovr,))

    # [국적 연혁] 출생 시점 보유 국적을 birth 이벤트로 기록(시작국적 + 복수국적).
    #   첫 항목이 '시작국적'이 되도록 1차 국적을 맨 앞에 둔다.
    _birth_hist = [{"type": "birth", "nat": nationality, "flag": flag,
                    "year": GAME_START_YEAR, "week": 1}]
    if nationality2:
        _birth_hist.append({"type": "birth", "nat": nationality2, "flag": flag2,
                            "year": GAME_START_YEAR, "week": 1})
    if nationality3:
        _birth_hist.append({"type": "birth", "nat": nationality3, "flag": flag3,
                            "year": GAME_START_YEAR, "week": 1})
    conn.execute("UPDATE my_player SET nat_history=? WHERE id=1",
                 (json.dumps(_birth_hist, ensure_ascii=False),))

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


def _calc_clean_sheets(c, tid, season, matches=None):
    """해당 시즌 소속 팀의 클린시트(무실점 경기) 수 집계.
    matches: 그 팀에서 선수가 실제 출전한 경기 수. 주어지면 이를 상한으로 적용한다.
      (버그수정) 기존엔 출전 0인 신규 이적팀도 그 팀이 시즌 전체에 쌓은
      무실점 경기 수를 그대로 반환해, 0출전인데 무실점 5가 찍혔다.
      선수가 안 뛴 경기의 무실점은 그 선수 기록이 아니므로 출전수로 캡한다."""
    if matches is not None and matches <= 0:
        return 0
    row = c.execute("SELECT league_id FROM teams WHERE id=?", (tid,)).fetchone()
    if not row:
        return 0
    q = c.execute(
        """SELECT COUNT(*) as cnt FROM match_results
           WHERE league_id=? AND season=? AND home_score>=0
           AND ((home_team_id=? AND away_score=0)
             OR (away_team_id=? AND home_score=0))""",
        (row["league_id"], season, tid, tid)).fetchone()
    cs = q["cnt"] if q else 0
    if matches is not None:
        cs = min(cs, matches)
    return cs


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

    # [최적화] 이미 열린 conn 재사용하여 get_team_rank 내부 커넥션 중복 방지
    season = p.get("current_season", 1)
    rank_str = get_team_rank(tid, conn=conn, season=season)
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

    # [최적화] teams 테이블에서 직접 읽기 (match_results 풀스캔 제거)
    trec = c.execute("SELECT wins, draws, losses FROM teams WHERE id=?", (tid,)).fetchone()
    tw = trec["wins"] if trec else 0
    td = trec["draws"] if trec else 0
    tl = trec["losses"] if trec else 0

    cs = _calc_clean_sheets(c, tid, season, matches=sm)

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

    # [최적화] 이미 열린 conn 재사용
    season = p.get("current_season", 1)
    rank_str = get_team_rank(tid, conn=conn, season=season)
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

    # 팀 전적: teams 테이블에서 직접 읽기
    trec2 = c.execute("SELECT wins, draws, losses FROM teams WHERE id=?", (tid,)).fetchone()
    tw = trec2["wins"] if trec2 else 0
    td = trec2["draws"] if trec2 else 0
    tl = trec2["losses"] if trec2 else 0

    cs = _calc_clean_sheets(c, tid, season, matches=sm)

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
        (p["age"], get_field_pos(p), team_row["name"], team_row["lname"],
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
        # [최적화] get_player/get_state를 루프 상단에서만 1회 호출.
        #   _simulate_match/_process_training 등이 my_player를 바꾸는 경우에만
        #   해당 함수 내부에서 get_player를 재조회하므로, 여기서 매 주차 재조회는 불필요.
        p  = get_player()
        st = get_state()
        cur_week   = st["current_week"]
        cur_season = st["current_season"]

        # 안전장치: schedule이 가리키는 주차와 실제 현재 주차가 다르면
        # (시즌이 도중에 넘어가 53주 같은 유령 주차가 된 경우) 더 진행하지 않는다.
        if week != cur_week:
            break

        # ── 이번 주차 처리 ──
        # [최적화] 경기를 실제로 뛰었는지 추적 → career_stats 갱신을 경기 주차에만 실행
        _had_match = False
        if p.get("injured"):
            _process_injury_week(p, week)
            if stype == "경기" and not (isinstance(detail, dict) and detail.get("intl")):
                _sim_my_team_match_as_ai(week, p, cur_season)
            else:
                _sim_my_unscheduled_match(week, p, cur_season)
        elif stype == "경기":
            _had_match = True
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
                _had_match = True
                intl_engine.simulate_my_match(week, p)
            elif cm:
                _had_match = True
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

        # ── 정확히 1주 전진 (경계 트리거 매주 검사) ──
        # [최적화] _simulate_match/_process_training이 season_matches 등을 갱신하므로
        #   salary/career 업데이트에는 최신 p가 필요 → 1회만 재조회.
        p_latest = get_player()

        # ── 월급: 4의 배수 주차(= 한 달의 마지막 주)에서만 ──
        if week % 4 == 0:
            _pay_salary(p_latest, week)

        _advance_week(p_latest, week, 1)

        # [국가대표 발탁 대기] 방금 17주 진입으로 국제대회(예선/본선)가 생성되며
        #   대표팀 선택 대기(my_selected=3)가 생겼다면, 발탁을 먼저 받아야 하므로
        #   더 진행하지 않고 이 주에서 멈춘다. (발탁 안 한 채 예선/본선 경기가
        #   진행돼버리는 것을 방지. center_panel이 다음 클릭 때 발탁창을 띄운다.)
        # [최적화] get_pending_choice()는 17주 진입 직후에만 의미 있으므로
        #   새 주차(new_week)가 17일 때만 DB 조회, 그 외엔 스킵.
        try:
            if week == 16 and intl_engine.get_pending_choice():
                break
        except Exception:
            pass

        # 진행 중 커리어 행 실시간 갱신 (경기가 있었던 주차에만)
        # [최적화] 훈련 주차(경기 없음)에는 스탯 변화 없으므로 갱신 스킵
        #   → 52주 중 경기 주차(~18회)에만 실행, 나머지 ~34회 DB 왕복 절약
        if _had_match and p_latest and p_latest.get("current_team_id"):
            p_fresh = get_player()   # _advance_week가 week/year를 바꿨으므로 1회만 재조회
            st_new  = get_state()
            _update_career_stats(p_fresh, st_new["current_year"], st_new["current_week"])
        # [최적화] 이 주차의 버퍼 로그 일괄 flush (개별 commit 수십 회 → 1회)
        flush_log_buffer()


# ── [개선] 홈 어드밴티지 변동폭 + 포메이션 스타일 보정 ──────────
#   기존: 모든 경기에 예외 없이 고정 +3 → 팀/경기 상관없이 완전히 똑같은 값.
#   개선: 매 경기 1.5~4.5 사이로 살짝 흔들리게(평균은 기존과 동일한 3.0 부근)
#         해서 "어떤 날은 홈 응원이 유독 잘 먹힌다" 정도의 자연스러운 변동 부여.
_team_formation_cache: dict = {}

def _home_advantage():
    return random.uniform(1.5, 4.5)


def _team_formation(c, team_id):
    """팀의 현재 포메이션 조회 (세션 캐시, PK 조회라 원래도 저렴하지만 캐시로 0비용화).
    [주의] 내 팀 포메이션은 경기 중 formation_widget에서 바뀔 수 있으므로
    _invalidate_team_ovr_cache()가 호출될 때 이 캐시도 함께 비운다."""
    cached = _team_formation_cache.get(team_id)
    if cached is not None:
        return cached
    c.execute("SELECT formation FROM teams WHERE id=?", (team_id,))
    row = c.fetchone()
    val = (row["formation"] if row else None) or "4-4-2"
    _team_formation_cache[team_id] = val
    return val


def _formation_bias(c, team_id):
    """포메이션 스타일에 따른 소폭 전력 보정치 (FORMATION_STYLE 참조, ±1.5 이내)."""
    return FORMATION_STYLE.get(_team_formation(c, team_id), 0.0)


def _match_win_probs(diff):
    """[공용] 전력차(diff=home_ovr-away_ovr, 홈보정 포함)로 승/무/패 확률 산출.
    _simulate_match(내 경기)의 개선판 공식과 통일:
      - hw 상한 0.92 (구식 배경 AI 경기 공식은 0.80 캡 → 압도해도 못 이기는 비논리)
      - dw는 전력차에 반비례 (구식 공식은 dw=0.25 고정 → 전력차 무관 항상 25% 무승부)"""
    hw = max(0.06, min(0.92, 0.45 + diff * 0.012))
    dw = max(0.06, 0.28 - abs(diff) * 0.006)
    aw = max(0.02, 1.0 - hw - dw)
    tot = hw + dw + aw
    return hw / tot, dw / tot, aw / tot


def _roll_outcome(diff):
    """diff 기반 승/무/패 확률로 outcome 문자열을 뽑는다."""
    hw, dw, aw = _match_win_probs(diff)
    roll = random.random()
    if roll < hw:         return "home"
    elif roll < hw + dw:  return "draw"
    else:                 return "away"


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
        ho = _team_avg_ovr(c, hid) + _home_advantage() + _formation_bias(c, hid)
        ao = _team_avg_ovr(c, aid) + _formation_bias(c, aid)
        diff = ho - ao
        outcome = _roll_outcome(diff)
        # [버그수정] diff를 _gen_score에 전달 — 이전엔 인자 누락으로 항상 박빙 취급됐음
        hs, as_ = _gen_score(outcome, diff)
        _td = {}; _accum_team_rec(_td, hid, aid, outcome, hs, as_); _flush_team_rec(c, _td)
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
        ho = _team_avg_ovr(c, row["home_team_id"]) + _home_advantage() + _formation_bias(c, row["home_team_id"])
        ao = _team_avg_ovr(c, row["away_team_id"]) + _formation_bias(c, row["away_team_id"])
        diff = ho - ao
        outcome = _roll_outcome(diff)
        # [버그수정] diff를 _gen_score에 전달
        hs, as_ = _gen_score(outcome, diff)
        _td = {}; _accum_team_rec(_td, row["home_team_id"], row["away_team_id"], outcome, hs, as_); _flush_team_rec(c, _td)
        conn.execute("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                     (hs, as_, row["id"]))
        conn.commit()
    conn.close()


def _sim_all_ai_matches(week, my_league_id, season):
    """모든 리그 이번 주차 미완료 경기 AI 처리 (내 팀 경기 제외)
    [최적화] match_results UPDATE를 executemany 배치로.
    [최적화] teams 전적 UPDATE도 _accum_team_rec + _flush_team_rec 배치로."""
    conn = get_conn()
    c = conn.cursor()

    p_row = conn.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
    my_tid = p_row["current_team_id"] if p_row else 0

    c.execute("""SELECT mr.id, mr.home_team_id, mr.away_team_id, mr.league_id
                 FROM match_results mr
                 WHERE mr.week=? AND mr.home_score=-1 AND mr.season=?""",
              (week, season))
    matches = c.fetchall()

    is_offseason = (1 <= week <= 4) or (12 <= week <= 25)

    batch_results = []   # (hs, as_, mid) — match_results 배치
    team_deltas   = {}   # {team_id: [w,d,l,gf,ga]} — teams 배치
    for m in matches:
        is_my_match = (m["home_team_id"] == my_tid or m["away_team_id"] == my_tid)
        if is_my_match and not is_offseason:
            continue
        ho = _team_avg_ovr(c, m["home_team_id"]) + _home_advantage() + _formation_bias(c, m["home_team_id"])
        ao = _team_avg_ovr(c, m["away_team_id"]) + _formation_bias(c, m["away_team_id"])
        diff = ho - ao
        outcome = _roll_outcome(diff)
        # [버그수정] diff를 _gen_score에 전달 — 전체 리그 경기의 90%+가 여길 거침
        hs, as_ = _gen_score(outcome, diff)
        _accum_team_rec(team_deltas, m["home_team_id"], m["away_team_id"], outcome, hs, as_)
        batch_results.append((hs, as_, m["id"]))

    if batch_results:
        c.executemany("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                      batch_results)
    _flush_team_rec(c, team_deltas)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────
# 훈련
# ─────────────────────────────────────────

def effective_training_stress(p, ttype):
    """선수의 성격/신체특징 stress_mult 를 반영한 '실제 적용' 스트레스 변화량.

    TRAINING_CONFIG[ttype]['stress'] 는 기본값이고, 실제로는 냉철함/훈련광(성격),
    지구력형/강철체질(신체특징) 의 stress_mult 가 곱해진다. 메인 화면 미리보기가
    이 함수를 사용하면 표시값과 실제 적용값이 항상 일치한다.
    (계산식은 _process_training 의 stress_chg 산출과 반드시 동일하게 유지할 것.)
    """
    cfg = TRAINING_CONFIG.get(ttype)
    if not cfg:
        return 0
    from constants import PHYSICAL_TRAIT_EFFECTS as _PTE
    pe       = PERSONALITY_EFFECTS.get(p.get("personality", "성실함"), {})
    trait_fx = _PTE.get(p.get("physical_trait", "무난함"), {})
    stress_chg = cfg["stress"]
    if "stress_mult" in pe:
        stress_chg = int(stress_chg * pe["stress_mult"])
    if "stress_mult" in trait_fx:
        stress_chg = int(stress_chg * trait_fx["stress_mult"])
    return stress_chg


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

    stress_chg = effective_training_stress(p, ttype)

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
                    # _max 도달: 한 번 훈련 시 HIGH_BREAK_PROB(30%) 확률로 _max를 +1
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

            # [행복도 연동] 행복도가 낮으면(SLUMP_LOW_HAPPY 이하) 슬럼프 임계치를
            #   40으로 낮춘다. 즉 스트레스가 60에 못 미쳐도 불행하면 슬럼프가 올 수 있다.
            #   - 스트레스 >= 정규 임계치(60)  : 기존 확률(SLUMP_CHANCE)
            #   - 행복도 낮고 스트레스 40~59   : 낮은 확률(SLUMP_LOW_HAPPY_CHANCE)
            low_happy = new_happy <= SLUMP_LOW_HAPPY
            eff_threshold = threshold
            if low_happy:
                eff_threshold = min(threshold, SLUMP_LOW_HAPPY_STRESS)

            if new_stress >= eff_threshold and ttype != "휴식":
                # 정규 구간(스트레스 60+)인지, 저행복 구간(40~59)인지로 베이스 확률 분기
                if new_stress >= threshold:
                    chance = SLUMP_CHANCE
                else:
                    # 저행복 때문에 낮은 임계치로 진입한 구간
                    chance = SLUMP_LOW_HAPPY_CHANCE
                if "slump_chance_mult" in pe:
                    chance *= pe["slump_chance_mult"]
                # 유리멘탈: 60 이상 구간에선 확률 추가
                if pe.get("slump_chance_add") and new_stress >= SLUMP_STRESS_THRESHOLD:
                    chance += pe["slump_chance_add"]
                chance = min(1.0, chance)
                if random.random() < chance:
                    slump = 1
                    if new_stress >= threshold:
                        add_log(f"😰 슬럼프 발생!  {week}주차", "slump")
                    else:
                        add_log(f"😰 행복도 저하로 슬럼프!  {week}주차", "slump")
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

    bonus = 0.0
    if played:
        # [에이스 영향력] 내가 팀 평균보다 높을수록 팀을 끌어올린다.
        #   - 11명 중 1명이지만, 에이스는 경기 영향력이 산술평균 이상.
        #   - gap(내 OVR - 팀평균) 기반으로 강하게 주되, 1명의 한계로 상한을 둔다.
        #   - 메시급(약팀에서도 캐리)을 재현하되 11명 게임의 한계는 유지.
        team_avg = home_ovr if is_home else away_ovr
        gap = my_ovr - team_avg
        bonus = max(0.0, gap) * 0.32 + my_ovr * 0.05
        bonus = min(bonus, 14.0)

    # [재설계 — 포메이션 매치업 시뮬레이션] 예전엔 홈-원정 OVR 차이 하나로
    # 확률표(_match_win_probs/_gen_score)에서 스코어를 뽑았다 — 포메이션이
    # 실제로 어느 구역에서 수적/능력치 우위를 만드는지는 결과에 전혀
    # 개입하지 못했다. 이제 실제 포메이션 매치업(레인별 공격/수비 스탯
    # 비교)을 분 단위로 시뮬레이션한 결과를 쓴다 — 단, 이건 "내가 직접
    # 보는 경기"에만 적용한다. 리그 나머지 수십~수백 경기(AI 대 AI)는
    # 이 무거운 시뮬레이션을 돌릴 필요도 의미도 없어서 그대로
    # _roll_outcome/_gen_score를 쓴다(이 함수는 안 건드림). 새 엔진에
    # 예외가 나도 경기 진행 자체가 막히면 안 되므로, 실패 시 예전 방식
    # (OVR 차이 확률표)으로 조용히 폴백한다.
    engine_stats = None
    engine_plog = None
    try:
        from match_sim.tactical_engine import simulate_my_match
        home_formation = _team_formation(c, home_id)
        away_formation = _team_formation(c, away_id)
        sim = simulate_my_match(
            home_id, away_id, home_formation, away_formation,
            home_boost=(bonus if is_home else 0.0),
            away_boost=(bonus if not is_home else 0.0),
            home_adv=_home_advantage())
        hs, as_ = sim["home_score"], sim["away_score"]
        engine_stats = {"home": sim["home_stats"], "away": sim["away_stats"]}
        engine_plog = sim["possession_log"]
        outcome = "draw" if hs == as_ else ("home" if hs > as_ else "away")
        diff = (home_ovr + (bonus if is_home else 0.0) + _home_advantage()
                + _formation_bias(c, home_id)
                - (away_ovr + (bonus if not is_home else 0.0) + _formation_bias(c, away_id)))
    except Exception:
        home_ovr2 = home_ovr + (bonus if is_home else 0.0)
        away_ovr2 = away_ovr + (bonus if not is_home else 0.0)
        home_ovr2 += _home_advantage() + _formation_bias(c, home_id)
        away_ovr2 += _formation_bias(c, away_id)
        diff = home_ovr2 - away_ovr2
        outcome = _roll_outcome(diff)
        hs, as_ = _gen_score(outcome, diff)

    goals = assists = saves = 0
    rating = 0.0
    events = []
    detail = {"shots":0,"shots_on":0,"key_passes":0,"dribbles":0,"blocks":0,"pass_acc":0.0}
    if played:
        goals, assists, saves, rating, events, detail = _player_perf(p, outcome, is_home, hs, as_, c=c)
        if p.get("slump"):
            rating = round(max(3.0, rating + SLUMP_RATING_PENALTY), 1)

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

    # [최적화] get_player 재조회 없이 p에서 직접 계산 후 update_player 1회 통합
    new_rel = _calc_manager_rel(p, rating, my_result, played)
    new_pop = _calc_pop(p, goals, assists, rating)

    # 스트레스/행복/멘탈 계산 (p에서 직접, get_player 재조회 제거)
    age = p.get("age", 0) or 0
    if age >= 30:
        match_stress = 3 if info.get("is_home") else 6
    else:
        match_stress = 5 if info.get("is_home") else 8
    ns = min(100, p["stress"] + match_stress)
    nh = p["happiness"]
    if my_result == "win":    nh = min(100, nh+3)
    elif my_result == "loss": nh = max(0,   nh-3)
    if p.get("slump"):
        nh = max(0, nh - 15)

    mental_updates = {}
    if played:
        n_up = random.choices([1, 2], weights=[70, 30])[0]
        for ms in random.sample(MENTAL_STATS, n_up):
            cur = p.get(ms, 40)
            mx  = p.get(f"{ms}_max", 80)
            if cur < mx:
                mental_updates[ms] = min(mx, cur + 1)
    else:
        ms = random.choice(MENTAL_STATS)
        cur = p.get(ms, 40)
        if cur > 20:
            mental_updates[ms] = cur - 1
        add_log(f"⚠ 경기 불참  {week}주차  {STAT_KO.get(ms,ms)} -1", "training")

    # [최적화] 감독관계·인기도·스트레스·행복·멘탈 모두 1회 update_player로 통합
    update_player(manager_relation=new_rel, popularity=new_pop,
                  stress=ns, happiness=nh, **mental_updates)

    _write_match_log(p, week, info["league_name"], is_home,
                     home_id, away_id, hs, as_,
                     my_result, goals, assists, saves, rating, events, played, benched,
                     detail=detail, engine_stats=engine_stats, engine_plog=engine_plog)


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
# ══════════════════════════════════════════════════════════════
# 경기 퍼포먼스 시스템 v3 상수
# 설계: 14경기 기준, OVR 비선형 가속, 포지션별 Base 평점 차등
# ══════════════════════════════════════════════════════════════
DOMINANCE_K   = 0.040   # 선형 기본 증가폭
DOMINANCE_MIN = 0.30    # 최저 배수 (강한 리그 위축 하한)
DOMINANCE_MAX = 2.20    # 최고 배수 Cap (K1 OVR99도 2.20 이내)
GOAL_PROB_CAP = 0.72    # 경기당 골 확률 상한 — 탑 ST도 72%
ASSIST_PROB_CAP = 0.60  # 경기당 어시 확률 상한

def _dominance_mult(my_ovr, league_avg):
    """OVR vs 리그 평균 → 활약 배수 (v3 비선형).
    설계 (14경기 기준):
      리그 평균: dom=1.0 → ST 7~10골
      OVR 5 위:  dom≈1.20 → ST 10~14골
      OVR95~99:  비선형 가속으로 월클 폭발 (14~20골)
    공식 검증:
      OVR99 @ K1(avg82): gap=17 → base=1.68 + elite0.74 = 2.42 → cap → 2.20
      OVR99 @ EPL(avg93): gap=6  → base=1.24 + elite0.74 = 1.98 (cap 미도달)
    """
    my_ovr = my_ovr or 50
    lg_avg = league_avg or 50
    gap = my_ovr - lg_avg
    base = 1.0 + gap * DOMINANCE_K          # 선형 기반

    # OVR 90~94: 완만한 가속
    if my_ovr >= 90:
        elite = (my_ovr - 90) ** 1.9 * 0.012  # 90→0, 94→+0.21
    else:
        elite = 0.0

    # OVR 95~100: 체감형 추가 가속 (검증된 공식)
    if my_ovr >= 95:
        elite += (my_ovr - 95) ** 1.5 * 0.0525  # 95→+0, 97→+0.15, 99→+0.42, 100→+0.52
        # 99 총합: 0.32(90~94 구간) + 0.42 = +0.74 ✓

    base += elite
    return max(DOMINANCE_MIN, min(DOMINANCE_MAX, base))

def _stat_n(p, stat, lo=40, hi=95):
    """스탯을 0~1로 정규화 (lo=0, hi=1). 플레이스타일 반영용."""
    v = p.get(stat, 60)
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _my_team_avg_ovr(p):
    """내 소속 팀의 AI 선수 평균 OVR (동료 수준).
    [최적화] _team_ovr_cache 우선 활용 — 세션 내 같은 팀은 캐시로 처리."""
    tid = p.get("current_team_id", 0)
    if not tid:
        return p.get("ovr", 40)
    cached = _team_ovr_cache.get(tid)
    if cached is not None:
        return cached
    conn = get_conn()
    c = conn.cursor()
    try:
        result = _team_avg_ovr(c, tid)  # 캐시에 저장하면서 반환
    finally:
        conn.close()
    return result


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


def _gen_score(outcome, diff=0.0):
    """경기 스코어 생성. diff(홈-원정 전력차)가 클수록 이긴 쪽이 크게 이긴다.
    diff는 _simulate_match 에서 계산된 home_ovr-away_ovr (홈 보정 포함).
      - |diff| 0      → 박빙: 이겨도 1~2골차
      - |diff| 15~25  → 우세: 2~3골차 흔함
      - |diff| 35+    → 압도: 4골+ 대량득점, 무실점 잦음
    승자/패자는 outcome 으로 이미 정해졌고, 여기선 '몇 대 몇'만 정한다.
    """
    # 전력차 → 이긴 팀의 기대 득점 가중(우세할수록 큰 점수 쪽으로 분포 이동).
    adv = abs(diff)
    if outcome == "draw":
        # 무승부: 전력 비슷할 때 주로 발생하므로 저득점 위주.
        g = random.choices([0, 1, 2, 3], weights=[22, 38, 28, 12])[0]
        return g, g

    # 이긴 팀 득점 분포를 전력차로 조정.
    if adv >= 50:        # 초압도 (브라질 vs 약소국급) — 드물게 7~9골 이변
        win_goals = random.choices([3, 4, 5, 6, 7, 8, 9],
                                   [14, 24, 24, 18, 12, 6, 2])[0]
        lose_goals = random.choices([0, 1],         [85, 15])[0]
    elif adv >= 35:      # 압도
        win_goals = random.choices([2, 3, 4, 5, 6], [12, 26, 30, 20, 12])[0]
        lose_goals = random.choices([0, 1, 2],      [70, 24, 6])[0]
    elif adv >= 22:      # 강한 우세
        win_goals = random.choices([1, 2, 3, 4, 5], [10, 30, 30, 20, 10])[0]
        lose_goals = random.choices([0, 1, 2],      [58, 32, 10])[0]
    elif adv >= 12:      # 우세
        win_goals = random.choices([1, 2, 3, 4],    [22, 38, 26, 14])[0]
        lose_goals = random.choices([0, 1, 2],      [50, 38, 12])[0]
    else:               # 박빙
        win_goals = random.choices([1, 2, 3, 4],    [38, 36, 18, 8])[0]
        lose_goals = random.choices([0, 1, 2],      [44, 40, 16])[0]
    lose_goals = min(lose_goals, win_goals - 1)  # 이긴 팀이 항상 더 많이

    if outcome == "home":
        return max(1, win_goals), max(0, lose_goals)
    else:  # away
        return max(0, lose_goals), max(1, win_goals)


def _my_result(outcome, is_home):
    if outcome == "draw":
        return "draw"
    return "win" if (outcome=="home")==is_home else "loss"


def _multigoal_banner(goals):
    """다득점 시 표시할 강조 배너. 1골 이하는 배너 없음(None)."""
    return {
        2: "🔥 멀티골 달성!",
        3: "🎩🔥 해트트릭 완성!!",
        4: "🎩🎩 포-골 하울!! (4골)",
        5: "🎩🎩🎩 파이브-골 하울!!! (5골)",
        6: "👑🎩 더블 해트트릭!!! (6골)",
    }.get(goals)


def _min_sortkey(m):
    """이벤트 분 정렬용 실수 키. 전반 추가시간(146~155)은 45.1~45.10으로,
       후반 추가시간(91~100)은 90.1~90.10으로 매핑해 실제 경기 시간순 정렬."""
    if 146 <= m <= 155:
        return 45 + (m - 145) / 100.0
    if 91 <= m <= 100:
        return 90 + (m - 90) / 100.0
    return float(m)


def _fmt_min(m):
    """정렬용 분(정수)을 표시 문자열로. 추가시간은 45+n / 90+n 형식.
       전반 추가시간은 146~155(=45+1~45+10)로 인코딩해 후반 정규시간과 겹치지 않게 한다.
       후반 추가시간은 91~100(=90+1~90+10) — 최근 축구 트렌드(VAR 등)상 후반 추가시간이
       10분까지도 흔히 나오므로 그만큼 지원한다.
       Godot 연동 시에도 이 표기 규칙을 그대로 쓸 수 있다."""
    if 146 <= m <= 155:          # 전반 추가시간 (45+1 ~ 45+10)
        return f"45+{m-145}"
    if 91 <= m <= 100:           # 후반 추가시간 (90+1 ~ 90+10)
        return f"90+{m-90}"
    return str(m)


def _half_of(m):
    """분(정수)이 전반인지 후반인지. 전반 추가시간(146~155)도 전반으로."""
    if 146 <= m <= 155:
        return "first"
    return "first" if m <= 45 else "second"


def _sample_minutes(n, lo, hi, avoid=None, min_gap=3):
    """경기 이벤트 분(分)을 n개 뽑는다. 정렬용 정수 리스트(오름차순) 반환.
       정규시간(lo~hi) 위주이되, 낮은 확률로 추가시간이 섞인다.
       - 전반 추가시간: 146~155 (=45+1~45+10), 짧은 쪽이 흔함
       - 후반 추가시간: 91~100  (=90+1~90+10), 짧은 쪽이 흔하지만 최근 트렌드상
         9~10분까지도 드물게 나올 수 있게 폭을 넓혔다
       정렬 시 146~155는 큰 값이라 맨 뒤로 가지만, _half_of 로 전반에 재배치된다.

       avoid: [버그 수정] 이미 다른(진짜) 이벤트가 있는 분(分)들의 집합.
       min_gap분 이내는 피해서 뽑는다 — 안 그러면 "2' 실점"과 "2' 코너킥이
       걷어내졌다"처럼 서로 무관한 이벤트가 같은/거의 같은 순간에 겹쳐
       배너·장면이 뒤죽박죽 충돌하는 문제가 있었다(득점 배너 뜨자마자
       엉뚱한 코너킥 배너가 겹쳐 뜨는 등)."""
    if n <= 0:
        return []
    avoid_keys = [_min_sortkey(a) for a in (avoid or ())]

    def _too_close(m):
        mk = _min_sortkey(m)
        return any(abs(mk - ak) < min_gap for ak in avoid_keys)

    pool = [x for x in range(lo, min(hi, 90) + 1) if not _too_close(x)]
    if not pool:
        pool = list(range(lo, min(hi, 90) + 1))  # 안전망(avoid가 범위를 다 잡아먹은 극단적 경우)
    # 추가시간: 짧을수록 자주(가중치). 전반은 후반보다 덜 나오게. 9~10분은 아주 드물게.
    fh_stop = [146,146,146, 147,147, 148,148, 149, 150, 151, 152, 153, 154, 155]   # 45+1~10
    sh_stop = [91,91,91,91, 92,92,92, 93,93,93, 94,94, 95,95, 96, 97, 98, 99, 100]  # 90+1~10
    out = set()
    attempts = 0
    while len(out) < n and attempts < n * 25:
        r = random.random()
        if r < 0.04:                       # 전반 추가시간 (드묾)
            cand = random.choice(fh_stop)
        elif r < 0.16:                     # 후반 추가시간 (좀 더 흔함)
            cand = random.choice(sh_stop)
        else:
            cand = random.choice(pool)
        if not _too_close(cand):
            out.add(cand)
        attempts += 1
    while len(out) < n and len(out) < len(pool):
        cand = random.choice(pool)
        if not _too_close(cand):
            out.add(cand)
    return sorted(out)


def _describe_goal(goal_idx, total_goals, minute, my_final, opp_final, dom, exclude=None):
    """내 골 하나의 '맥락'을 추정해 골 묘사 문구를 고른다.
    실제 골 시점의 스코어는 시뮬레이션이 분 단위로 돌지 않아 알 수 없으므로,
    최종 스코어 + 분(分) + 골 순번으로 그럴듯한 종류를 휴리스틱하게 분류한다.
      - 75분 이후 + 1골차 박빙 → 극장골/막판골
      - 1~2골차 승부에서 마지막 골 → 결승골
      - 박빙 접전에서 뒤지다 따라잡는 그림(후반) → 역전골
      - 비기는 스코어 → 동점골 / 첫 골 전반 → 선제골
    exclude: 같은 경기에서 이미 쓴 문구 set (중복 방지).
    대승(3골차 이상)에선 결승/역전/극장 분류를 끈다(어색함 방지).
    """
    exclude = exclude or set()
    margin = my_final - opp_final
    is_last = (goal_idx == total_goals)
    tight = (0 <= margin <= 2)   # 박빙 여부
    real_min = _min_sortkey(minute)        # 실제 경기 시간(전/후반 추가시간 반영)
    is_stoppage = (91 <= minute <= 100)    # 후반 추가시간만 해당

    def pick(key):
        pool = [x for x in GOAL_PHRASES[key] if x not in exclude] or GOAL_PHRASES[key]
        return random.choice(pool)

    # 후반 추가시간 + 박빙 마지막 골 → 극장골 최우선 (90+분의 박진감)
    if is_stoppage and is_last and 0 <= margin <= 1 and my_final >= opp_final:
        return pick("late")
    # 막판 극장골 (박빙 1골차, 실제 78분 이후 마지막 골)
    if real_min >= 78 and is_last and 0 <= margin <= 1 and my_final >= opp_final:
        return pick("late")
    # 결승골 (1~2골차 승리의 마지막 골) — 대승 제외
    if is_last and 1 <= margin <= 2 and my_final > opp_final:
        return pick("winner")
    # 역전골 (박빙 접전 + 후반 + 상대도 득점) — 대승 제외
    if is_last and tight and margin >= 1 and real_min >= 55 and opp_final >= 1:
        return pick("comeback")
    # 동점골 (최종 무승부)
    if margin == 0 and opp_final >= 1:
        return pick("equalizer")
    # 선제골 (첫 골 + 전반)
    if goal_idx == 1 and real_min <= 40:
        return pick("opener")
    return pick("normal")


def _poisson(lam):
    """경기당 활동 횟수(키패스·드리블·차단 등)를 포아송 분포로 뽑는다.
    λ(기대값)는 스탯·포지션·지배력으로 산출된 값. 같은 λ라도 경기마다
    결과가 흔들려(어떤 날 8개, 어떤 날 1개) 시즌 누적에 현실적 분산이 생긴다.
    Knuth 알고리즘. λ가 크면(>30) 비용 절감 위해 정규근사로 대체."""
    if lam <= 0:
        return 0
    if lam > 30:
        # 정규근사 (평균 λ, 분산 λ)
        return max(0, int(round(random.gauss(lam, lam ** 0.5))))
    L = 2.718281828459045 ** (-lam)
    k = 0
    pr = 1.0
    while True:
        k += 1
        pr *= random.random()
        if pr <= L:
            return k - 1


# ── 상대 PK 실점 마킹 ─────────────────────────────────────────
# [신규] "🥅 실점" 텍스트는 지금까지 그 실점이 PK였는지/오픈플레이였는지
# 아무 정보도 없었다. 그래서 match_sim_viewer._detect_style()이 상대의
# PK 득점을 절대 구분 못 하고 항상 "normal"(오픈플레이 빌드업+슛) 씬으로
# 떨어졌다 — "PK인데 그냥 공 잡고 뛰는 것처럼 나온다"는 지적의 원인.
# 팀 스코어(opp_score)는 절대 안 건드리고, 이미 정해진 실점 개수 중
# 일부에 "(PK)" 꼬리표만 붙여서 뷰어가 전용 스팟킥 연출(20명 클리어
# 대형)을 태울 수 있게 한다.
#
# [1단계 — 지금] 실제 축구의 골 대비 PK 비율(대략 15~20%)에 맞춘 고정
# 확률. [2단계 — 추후] 그 경기의 파울/카드 수가 많을수록(=PK가 나올
# 만한 상황 자체가 많았을수록) 확률이 올라가도록 바꿀 예정 — 그때는
# 이 함수에 파울/카드 컨텍스트 인자를 추가하고 아래 고정값 대신 그 값
# 기반 확률을 쓰면 된다. 호출부(_player_perf)는 헬퍼만 호출하므로 이
# 함수 내부만 바꾸면 됨.
_OPP_PK_CONCEDE_PROB = 0.17


def _roll_is_pk_concede():
    """상대의 이번 실점 하나가 PK였는지 확률적으로 결정.
    [2단계 확장 지점] 나중에 그 경기 파울/카드 수를 반영하려면 이 함수에
    인자를 추가하고 _OPP_PK_CONCEDE_PROB 대신 그 값 기반 확률을 쓰면 됨."""
    return random.random() < _OPP_PK_CONCEDE_PROB


def _player_perf(p, outcome, is_home, hs, as_, c=None, opp_ovr=None):
    """경기 퍼포먼스 계산 v3.
    포지션별 Base 차등 + 활약 가산 구조.
    수비수는 실점 관여 확률 트리거 감점.
    """
    pos       = get_field_pos(p)
    _my_ovr   = p.get("ovr", 50)
    my_score  = hs if is_home else as_
    opp_score = as_ if is_home else hs
    goals = assists = saves = 0
    events = []
    detail = {"shots": 0, "shots_on": 0, "key_passes": 0,
              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}

    # ── dom 계산 ──────────────────────────────────────────────
    if opp_ovr is not None:
        _lg_avg = opp_ovr
    else:
        _lid = p.get("current_league_id", 0)
        _lg_avg = 50.0
        if _lid:
            try:
                if c is not None:
                    _lg_avg = _league_avg_ovr(c, _lid)
                else:
                    _tc = get_conn()
                    _lg_avg = _league_avg_ovr(_tc.cursor(), _lid)
                    _tc.close()
            except Exception:
                _lg_avg = 50.0
    dom = _dominance_mult(_my_ovr, _lg_avg)

    # ── 실점 타임라인 ─────────────────────────────────────────
    if opp_score > 0:
        for cm in _sample_minutes(opp_score, 2, 90):
            _concede_text = "🥅 실점 (PK)" if _roll_is_pk_concede() else "🥅 실점"
            events.append((cm, _concede_text))

    # ── 스탯 정규화 ──────────────────────────────────────────
    sh  = _stat_n(p, "shooting")
    pa  = _stat_n(p, "passing")
    dr  = _stat_n(p, "dribbling")
    ta  = _stat_n(p, "tackling")
    hd  = _stat_n(p, "heading")
    sp  = _stat_n(p, "setpiece")
    spd = _stat_n(p, "speed")
    pos_s = _stat_n(p, "positioning")
    sta = _stat_n(p, "stamina")

    # ══════════════════════════════════════════════════════════
    # GK 전용 분기
    # ══════════════════════════════════════════════════════════
    if pos == "GK":
        _lid2 = p.get("current_league_id", 0)
        if c is not None:
            _tier = _league_tier(c, _lid2, default=3)
        else:
            _tier = 3
            if _lid2:
                try:
                    _tc2 = get_conn()
                    _tier = _league_tier(_tc2, _lid2, default=3)
                    _tc2.close()
                except Exception:
                    pass

        if _tier == 1:   _sr_min, _sr_max = 0.46, 0.82
        elif _tier == 2: _sr_min, _sr_max = 0.42, 0.78
        else:            _sr_min, _sr_max = 0.38, 0.74

        _gk_ovr = p.get("ovr", 50)
        _ovr_t  = max(0.0, min(1.0, (_gk_ovr - 40) / 45))
        _sr_center = _sr_min + (_sr_max - _sr_min) * _ovr_t
        _sr_center = min(0.92, _sr_center * (0.55 + 0.45 * dom))
        _sr_target = max(_sr_min, min(0.92, _sr_center + random.uniform(-0.04, 0.04)))

        if _tier == 1:   _base_sot = random.choices([1,2,3,4,5], [10,24,30,22,14])[0]
        elif _tier == 2: _base_sot = random.choices([1,2,3,4,5], [8,22,30,24,16])[0]
        else:            _base_sot = random.choices([1,2,3,4,5,6],[6,18,26,24,16,10])[0]
        _expose = max(0.55, min(1.7, 1.45 - 0.46 * min(2.2, dom)))
        extra_sot   = max(0, int(round(_base_sot * _expose)))
        total_shots = max(opp_score + 1, opp_score + extra_sot)
        saves = max(0, min(total_shots - opp_score,
                           round(total_shots * _sr_target - opp_score*(1-_sr_target))))
        rate  = saves / total_shots if total_shots else 0
        _faced = total_shots

        # GK 기본 Base 6.20
        base = 6.20
        # 선방 퀄리티 보정
        if _faced >= 3:
            if   rate >= 0.85: base += 1.4 + 0.6*_ovr_t; events.append("🧤 믿을 수 없는 선방쇼!")
            elif rate >= 0.78: base += 0.9 + 0.4*_ovr_t; events.append("🧤 환상적인 선방!")
            elif rate >= 0.70: base += 0.45 + 0.2*_ovr_t; events.append("🧤 안정적인 선방")
            elif rate >= 0.60: base += 0.1
            elif opp_score > 0 and rate < 0.45: base -= 1.0; events.append("😞 불안한 선방...")
            elif opp_score > 0 and rate < 0.55: base -= 0.4
        else:
            if saves >= 2: base += 0.3 + 0.25*_ovr_t; events.append("🧤 안정적인 선방")
            elif saves == 1: base += 0.1 + 0.15*_ovr_t
            elif opp_score == 0: base += 0.15*_ovr_t
        if _faced - opp_score >= 5 and rate >= 0.70:
            base += 0.4; events.append("🛡 슈팅 세례를 막아냄")
        # 무실점/대량실점
        if   opp_score == 0: base += 0.15; events.append("🧱 클린시트!")
        elif opp_score >= 5: base -= 1.1;  events.append("😞 대량 실점...")
        elif opp_score >= 4: base -= 0.8;  events.append("😞 대량 실점...")
        elif opp_score >= 3: base -= 0.5;  events.append("😞 다실점...")
        elif opp_score >= 2: base -= 0.2
        # 패스성공률 (빌드업 GK)
        _pa_gk = _stat_n(p, "passing")
        detail["pass_acc"] = round(min(0.97, 0.70 + 0.22*_pa_gk + random.uniform(-0.03,0.03)), 3)
        base += 0.8 * (detail["pass_acc"] - 0.82)
        # 패스성공률 60% 이하 감점
        if detail["pass_acc"] < 0.60:
            base -= 0.15

    # ══════════════════════════════════════════════════════════
    # 필드 플레이어 분기
    # ══════════════════════════════════════════════════════════
    else:
        # ── 포지션별 Base + 계수 설정 ───────────────────────
        if pos in ("ST", "CF"):
            base = 5.80
            g_base, g_sh, g_dr = 0.30, 0.24, 0.05
            a_base, a_pa, a_dr = 0.06, 0.14, 0.06
            g_goal, g_asst     = 1.10, 0.70
            g_pos_mult         = 1.55
            sp_goal            = 0.0
            # 스탯 가중: 슈팅★★★ 포지셔닝★★★ 헤딩★★
            _stat_bonus = 0.10*sh + 0.06*pos_s + 0.04*hd

        elif pos in ("LW", "RW"):
            base = 5.80
            g_base, g_sh, g_dr = 0.18, 0.12, 0.10  # 어시 특화라 골 낮춤
            a_base, a_pa, a_dr = 0.18, 0.26, 0.16
            g_goal, g_asst     = 1.00, 0.80
            g_pos_mult         = 0.85
            sp_goal            = 0.0
            # 드리블★★★ 스피드★★★ 패스★★
            _stat_bonus = 0.08*dr + 0.06*spd + 0.04*pa

        elif pos == "CAM":
            base = 5.90
            g_base, g_sh, g_dr = 0.14, 0.13, 0.06
            a_base, a_pa, a_dr = 0.26, 0.32, 0.14
            g_goal, g_asst     = 0.90, 0.90
            g_pos_mult         = 0.80
            sp_goal            = 0.0
            # 패스★★★ 포지셔닝★★★ 드리블★★
            _stat_bonus = 0.10*pa + 0.06*pos_s + 0.04*dr

        elif pos == "CM":
            base = 6.00
            g_base, g_sh, g_dr = 0.10, 0.11, 0.05
            a_base, a_pa, a_dr = 0.16, 0.26, 0.10
            g_goal, g_asst     = 0.85, 0.80
            g_pos_mult         = 0.55
            sp_goal            = 0.0
            # 패스★★ 태클★★ 스태미나★★
            _stat_bonus = 0.07*pa + 0.05*ta + 0.04*sta

        elif pos == "CDM":
            base = 6.10
            g_base, g_sh, g_dr = 0.04, 0.06, 0.02
            a_base, a_pa, a_dr = 0.09, 0.17, 0.05
            g_goal, g_asst     = 0.80, 0.75
            g_pos_mult         = 0.28
            sp_goal            = 0.0
            # 태클★★★ 포지셔닝★★★ 스태미나★★
            _stat_bonus = 0.10*ta + 0.07*pos_s + 0.04*sta

        elif pos in ("LB", "RB"):
            base = 6.00
            g_base, g_sh, g_dr = 0.04, 0.04, 0.04
            a_base, a_pa, a_dr = 0.14, 0.22, 0.12
            g_goal, g_asst     = 0.80, 0.80
            g_pos_mult         = 0.30
            sp_goal            = 0.04  # 오버래핑 크로스
            # 스피드★★ 태클★★ 패스★★
            _stat_bonus = 0.07*spd + 0.06*ta + 0.05*pa

        else:  # CB
            base = 6.10
            g_base, g_sh, g_dr = 0.02, 0.03, 0.01
            a_base, a_pa, a_dr = 0.02, 0.06, 0.02
            g_goal, g_asst     = 0.80, 0.70
            g_pos_mult         = 0.22
            sp_goal            = 0.05  # 코너킥 헤더
            # 태클★★★ 포지셔닝★★★ 헤딩★★★ 스피드★★
            _stat_bonus = 0.10*ta + 0.08*pos_s + 0.06*hd + 0.04*spd

        # 스탯 보너스 반영 (dom과 무관한 개인 기량 보정)
        base += _stat_bonus * 0.5   # 최대 약 +0.2 수준으로 제한

        # dom 가산 (포지션별 기본 base 위에 올림 — "가만히 있어도 고평점" 방지)
        # dom 가산 — 약체 리그 압도 시 base↑, 강팀 리그 고전 시 base↓
        if dom >= 1.0:
            base += 1.70 * ((dom - 1.0) ** 0.55)  # dom2.2→+1.88, dom1.5→+1.16
        else:
            base += 1.40 * (dom - 1.0)   # dom<1.0 위축
        # ── gprob / aprob 계산 ───────────────────────────────
        _gdom_exp = 0.45 + 0.28 * (g_pos_mult / 1.55)
        _gdom = dom ** _gdom_exp
        _pos_goal_scale = g_pos_mult / 1.55
        _weak_bonus = 0.25 * max(0.0, dom - 1.2) * _pos_goal_scale
        _adom_exp = 0.35 + 0.20 * (g_pos_mult / 1.55)
        _adom = dom ** _adom_exp
        # OVR 95+ gprob cap 완화
        # gprob: GOAL_PROB_CAP 고정 (월클 폭발은 xg 멀티골로 반영)
        _gprob_cap = GOAL_PROB_CAP
        gprob = min(_gprob_cap, (g_base + sh*g_sh + dr*g_dr)*_gdom + _weak_bonus)

        aprob = min(ASSIST_PROB_CAP, (a_base + pa*a_pa + dr*a_dr)*_adom + 0.4*_weak_bonus)
        # ── 골 판정 ──────────────────────────────────────────
        got_goal = False
        if my_score > 0 and random.random() < gprob:
            sh_dom = 1.0 + 0.90*((max(1.0,dom)-1.0)**0.58)*(0.35+0.65*_pos_goal_scale)
            xg = g_pos_mult * (0.35 + 0.65*sh) * sh_dom
            # 엘리트 결정력 가산 (OVR90+ sh 높을수록 폭발)
            if sh > 0.75:
                xg += g_pos_mult * 14.0 * (sh - 0.75) ** 2
            xg = max(0.5, min(4.5, xg))   # 한 경기 최대 4~5골 수준
            goals = 1
            decay = 0.48 + 0.22 * max(0.0, sh - 0.55) / 0.45
            ep = max(0.0, min(0.92, (xg - 0.65) / 3.0))
            while goals < 7 and random.random() < ep:
                goals += 1; ep *= decay
            goals = min(goals, my_score)
            base += goals * g_goal
            got_goal = True
            goal_mins = _sample_minutes(goals, 3, 90)
            _used_txt = set()
            for gi, gm in enumerate(goal_mins):
                ev = _describe_goal(gi+1, goals, gm, my_score, opp_score, dom, exclude=_used_txt)
                _used_txt.add(ev); events.append((gm, ev))
            banner = _multigoal_banner(goals)
            if banner: events.append((goal_mins[-1], banner))

        # 세트피스 보너스 골 (LB/RB/CB 크로스/헤더, 일반 키커)
        _sp_prob = sp_goal + (0.05*sp if sp > 0.55 else 0.0)
        _sp_prob *= min(1.5, dom)
        if my_score > goals and _sp_prob > 0 and random.random() < _sp_prob:
            goals += 1; base += g_goal
            ev_sp = "🎯 세트피스 골!" if pos in ("CB","LB","RB") else "🎯 환상적인 세트피스 골!"
            events.append((random.randint(10,88), ev_sp))
        # ── 페널티킥 (PK) 판정 ──────────────────────────────────
        # PK 획득: 공격수/드리블러가 박스 안 침투 시 파울 유도
        # 14경기 기준 PK 획득 기대: ST 약 0.8회, LW/RW 0.5회, CAM 0.3회
        _pk_base = {"ST":0.06,"CF":0.06,"LW":0.05,"RW":0.05,"CAM":0.03}.get(pos, 0.01)
        _pk_prob = _pk_base * (0.5 + 0.5*dr) * min(1.4, dom)   # 드리블 높을수록, dom 높을수록 PK↑
        if my_score > goals and random.random() < _pk_prob:
            # PK 성공률: 슈팅 스탯 기반 (고스탯 = 90%+)
            _pk_success_rate = 0.65 + 0.30 * sh   # sh=0.5→80%, sh=1.0→95%
            _pk_min = random.randint(15, 88)
            if random.random() < _pk_success_rate:
                goals += 1; base += g_goal * 0.85   # PK 골 — 일반 골보다 약간 낮은 가산
                events.append((_pk_min, "🎯 페널티킥 골!"))
            else:
                base -= 0.50   # 실축 패널티
                events.append((_pk_min, "😤 페널티킥 실축..."))


        # ── 어시 판정 ────────────────────────────────────────────
        # [설계] 어시는 독립 판정 → 단 실제 어시 수는 (my_score - goals) 이내로 cap
        # "팀이 1골 넣고 내가 1골 넣었으면" assist_cap=0 → 어시 0
        # "팀이 3골 넣고 내가 1골 넣었으면" assist_cap=2 → 어시 최대 2
        # [수정] 어시 확률은 팀 기대 득점(aprob에 이미 반영)으로 독립 판정
        # cap=0이어도 aprob 판정은 하되, 결과가 나왔을 때 cap으로 자름
        eff_aprob = aprob * 0.40 if got_goal else aprob
        if random.random() < eff_aprob:
            _a_base_multi = 0.12 + 0.10*(a_pa / 0.26)
            multi_p = min(0.22, _a_base_multi)
            if pa > 0.78: multi_p += 1.8*(pa - 0.78)**1.3
            multi_p = min(0.52, multi_p)
            _raw_assists = 2 if random.random() < multi_p else 1
            # 실제 어시: (my_score - goals) 상한 적용
            _assist_cap = max(0, my_score - goals)
            assists = min(_raw_assists, _assist_cap)
            if assists > 0:
                base += assists * g_asst
                a_mins = _sample_minutes(assists, 5, 88)
                a_txts = ["🅰 정확한 어시스트!", "🅰 결정적 도움!", "🅰 키패스로 어시스트!",
                          "🅰 환상적인 패스로 어시!", "🅰 침투 패스 어시스트!"]
                for am in a_mins: events.append((am, random.choice(a_txts)))
                if assists >= 2: events.append((a_mins[-1], "🅰🔥 멀티 어시스트!"))
        # ── 수비수 실점 관여 트리거 감점 ────────────────────
        # "팀 실점 × 계수" 통짜 감점 대신 확률 기반 귀책 판정
        if opp_score > 0 and pos in ("CB","CDM","LB","RB"):
            _concede_prob = {"CB":0.45, "CDM":0.30, "LB":0.25, "RB":0.25}.get(pos, 0)
            _concede_pen  = {"CB":0.25, "CDM":0.18, "LB":0.15, "RB":0.15}.get(pos, 0)
            for _ in range(opp_score):
                if random.random() < _concede_prob:
                    base -= _concede_pen
            # 대량실점(3+) 추가 패널티
            if opp_score >= 3:
                base -= 0.20

        # ── 패스성공률 60% 이하 감점 ─────────────────────────
        # (아래 세부지표 계산 후 적용)

        # ── 세부 지표 ────────────────────────────────────────
        po = _stat_n(p, "positioning")
        _att_dom = 1.0 + 0.72*((max(1.0,dom)-1.0)**0.62)

        if pos in ("ST","CF"):
            shot_w, key_w, drb_w, blk_w = 3.2, 1.0, 1.4, 0.18
        elif pos in ("LW","RW"):
            shot_w, key_w, drb_w, blk_w = 2.4, 1.6, 2.6, 0.30
        elif pos == "CAM":
            shot_w, key_w, drb_w, blk_w = 1.8, 2.6, 1.8, 0.42
        elif pos == "CM":
            shot_w, key_w, drb_w, blk_w = 1.2, 2.0, 1.2, 0.70
        elif pos == "CDM":
            shot_w, key_w, drb_w, blk_w = 0.6, 1.2, 0.8, 1.20
        elif pos in ("LB","RB"):
            shot_w, key_w, drb_w, blk_w = 0.5, 1.3, 1.2, 1.00
        else:  # CB
            shot_w, key_w, drb_w, blk_w = 0.3, 0.5, 0.4, 1.22
        shots = int(round(shot_w * 0.72 * (0.4+0.6*sh) * _att_dom + random.uniform(0,0.7)))
        shots = max(goals, shots)
        on_ratio = 0.28 + 0.16*sh
        shots_on = int(round(shots * on_ratio + random.uniform(0,0.4)))
        shots_on = max(min(shots,goals), min(shots,shots_on))
        _kp_lambda = key_w * 0.66 * (0.35+0.4*pa+0.25*dr) * _att_dom
        key_passes = _poisson(_kp_lambda)
        key_passes = max(assists, key_passes)
        _drb_lambda = drb_w * 0.78 * (0.4+0.45*dr+0.15*spd) * _att_dom
        dribbles = _poisson(_drb_lambda)
        # 차단은 강팀 상대(dom 낮음)일수록 더 많이 발생 (상대 공격이 빈번)
        # 약체(dom 높음)는 오히려 차단 기회 줄어듦
        _blk_dom = max(0.60, 1.15 - 0.35 * min(1.5, max(0.0, dom - 0.5)))
        _blk_lambda = blk_w * (0.30+0.90*ta+0.45*po) * _blk_dom
        blocks = _poisson(_blk_lambda)

        # 패스성공률
        _lg_pass_adj = max(-0.09, min(0.02, (_lg_avg - 78.0) * 0.004))
        _pa_floor = 0.72 if pos in ("CB","LB","RB","CDM","CM") else 0.66
        pass_acc = _pa_floor + 0.16*pa + 0.04*(dom-1.0) + _lg_pass_adj \
                   + random.uniform(-0.025, 0.025)
        pass_acc = max(0.55, min(0.96, pass_acc))
        if pass_acc < 0.60:
            base -= 0.15

        detail["shots"]      = shots
        detail["shots_on"]   = shots_on
        detail["key_passes"] = key_passes
        detail["dribbles"]   = dribbles
        detail["blocks"]     = blocks
        detail["pass_acc"]   = round(pass_acc, 3)

        # ── 득점으로 이어지지 않은 슈팅 장면 ──────────────────
        #   기존엔 detail["shots"]=8, detail["shots_on"]=3 처럼 스탯만 쌓이고
        #   실제 미해결 슈팅 시도는 이벤트로 하나도 안 남아, 재생 화면에서는
        #   골(또는 PK 실축) 말고는 아무 장면도 안 나왔다.
        #   [수정] "슈팅 11"인데 장면은 1~2개만 나오는 건 스탯-영상 불일치라서
        #   대표 몇 개만 뽑던 걸 없애고, 득점 안 된 슈팅 시도 수만큼 전부
        #   장면화한다(shots - goals개).
        #   (🚫 마커 → ui/match_sim_viewer.py의 _MISS_MARKERS가 인식해
        #    '내 팀 공격 시도, 득점 실패' 장면으로 재생함)
        if pos != "GK" and shots > goals:
            _miss_n = shots - goals
            for _mm in _sample_minutes(_miss_n, 3, 89):
                if random.random() < 0.5:
                    events.append((_mm, "🚫 슈팅이 골대를 살짝 빗나갔다"))
                else:
                    events.append((_mm, "🚫 상대 골키퍼 선방에 막혔다"))

        # ── 수비 라인 무실점 보너스 ──────────────────────────
        if pos in ("CB","LB","RB","CDM") and opp_score == 0:
            _def_dom = max(0.5, min(1.5, dom))
            base += 0.20 * _def_dom

        # ── 차단 활약 보너스 (CDM/CB 전용) ──────────────────
        if pos in ("CDM","CB") and blocks >= 3:
            base += 0.15 * min(2.0, blocks / 3.0)
            if blocks >= 5: events.append((_sample_minutes(1, 10, 85)[0], "💪 압도적인 수비 활약!"))

        # ── 포지션별 주요 활약 타임라인 이벤트 ────────────────
        # 골/어시 외 차단·키패스·드리블·선방 등을 분 타임스탬프와 함께 기록
        # 너무 많으면 타임라인이 지저분해지므로 포지션별 핵심 지표 1~2개만
        if pos in ("CB", "CDM"):
            # 차단: 3개 이상이면 하이라이트 이벤트
            if blocks >= 5:
                for _bm in _sample_minutes(min(3, blocks//2), 10, 85):
                    events.append((_bm, "🛡 결정적 차단!"))
            elif blocks >= 3:
                events.append((_sample_minutes(1, 10, 85)[0], "🛡 중요한 차단"))
            # 패스성공률 낮으면 부정적 이벤트
            if pass_acc < 0.65 and opp_score >= 2:
                events.append((random.randint(30, 80), "⚠ 패스 미스"))

        elif pos in ("LB", "RB"):
            # 어시 없어도 키패스 많으면 기록
            if key_passes >= 3 and assists == 0:
                events.append((_sample_minutes(1, 20, 80)[0], "🎯 키패스 찬스 창출"))
            if blocks >= 3:
                events.append((_sample_minutes(1, 10, 85)[0], "🛡 오버래핑 후 귀환 차단"))

        elif pos in ("LW", "RW"):
            # 드리블 성공 많으면 기록
            if dribbles >= 5:
                for _dm in _sample_minutes(min(2, dribbles//3), 15, 80):
                    events.append((_dm, "🌪 드리블 돌파!"))
            elif dribbles >= 3:
                events.append((_sample_minutes(1, 15, 80)[0], "↗ 드리블 침투"))

        elif pos == "CAM":
            # 키패스 3개 이상이면 기록
            if key_passes >= 4:
                for _km in _sample_minutes(min(2, key_passes//2), 20, 75):
                    events.append((_km, "🔑 결정적 키패스!"))
            elif key_passes >= 2:
                events.append((_sample_minutes(1, 20, 75)[0], "🔑 기회 창출"))

        elif pos == "CM":
            # 키패스+차단 균형 활약
            if key_passes >= 3:
                events.append((_sample_minutes(1, 20, 70)[0], "🔑 전방 연결 패스"))
            if blocks >= 3:
                events.append((_sample_minutes(1, 25, 80)[0], "🛡 미드 차단"))

        elif pos in ("ST", "CF"):
            # 슈팅 많은데 골 없으면 부정적
            if shots >= 4 and goals == 0:
                events.append((_sample_minutes(1, 30, 80)[0], "😤 결정력 부재"))
            elif shots_on >= 3 and goals == 0:
                events.append((_sample_minutes(1, 30, 80)[0], "😤 유효슈팅 불운"))

    # ── 최종 평점 클램프 ──────────────────────────────────────
    rating = max(3.0, min(10.0, round(base + random.uniform(-0.15, 0.15), 1)))

    return goals, assists, saves, rating, events, detail

def _pos_events(pos, positive):
    # 문구 풀은 constants.MATCH_PHRASES 로 분리(포지션당 8~12개로 확장).
    # 구버전 호환: 풀이 없으면 최소 기본값.
    pair = MATCH_PHRASES.get(pos, (["좋은 플레이"], ["실수"]))
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


def _accum_team_rec(deltas: dict, hid, aid, outcome, hs, as_):
    """팀 전적 변경분을 deltas dict에 누적 (DB 접근 없음).
    누적 완료 후 _flush_team_rec(c, deltas)로 일괄 UPDATE.
    deltas: {team_id: [wins, draws, losses, gf, ga]}
    """
    def _get(tid):
        if tid not in deltas:
            deltas[tid] = [0, 0, 0, 0, 0]  # wins,draws,losses,gf,ga
        return deltas[tid]

    hd = _get(hid); ad = _get(aid)
    hd[3] += hs; hd[4] += as_
    ad[3] += as_; ad[4] += hs
    if outcome == "home":
        hd[0] += 1; ad[2] += 1
    elif outcome == "away":
        hd[2] += 1; ad[0] += 1
    else:
        hd[1] += 1; ad[1] += 1


def _flush_team_rec(c, deltas: dict):
    """_accum_team_rec로 누적한 deltas를 executemany 1회로 커밋."""
    if not deltas:
        return
    c.executemany(
        "UPDATE teams SET wins=wins+?,draws=draws+?,losses=losses+?,"
        "goals_for=goals_for+?,goals_against=goals_against+? WHERE id=?",
        [(v[0], v[1], v[2], v[3], v[4], tid) for tid, v in deltas.items()]
    )


def _calc_manager_rel(p, rating, result, played) -> int:
    """[최적화] 감독 관계 신규값 계산만 (update_player 제거 → 호출자가 통합)."""
    from constants import MANAGER_TYPES
    mt = MANAGER_TYPES.get(p.get("manager_type", "베테랑 신뢰"), {})
    gain_m = mt.get("rel_gain_mult", 1.0)
    loss_m = mt.get("rel_loss_mult", 1.0)

    rel = p.get("manager_relation", 50)
    if not played:
        rel = max(0, rel - round(1 * loss_m))
    else:
        if rating >= 7.0:   rel = min(100, rel + round(3 * gain_m))
        elif rating >= 6.0: rel = min(100, rel + round(1 * gain_m))
        elif rating < 5.0:  rel = max(0, rel - round(3 * loss_m))
        if result == "win":    rel = min(100, rel + 1)
        elif result == "loss": rel = max(0, rel - round(1 * loss_m))
        if p.get("injured"): rel = max(0, rel - round(2 * loss_m))
    return rel

def _update_manager_rel(p, rating, result, played):
    """하위호환 래퍼 (인트엔진·챔스엔진에서 직접 호출하는 경우 대비)."""
    update_player(manager_relation=_calc_manager_rel(p, rating, result, played))


def _calc_pop(p, goals, assists, rating) -> int:
    """[최적화] 인기도 신규값 계산만 반환."""
    pop = p.get("popularity", 0)
    if goals > 0: pop = min(100, pop + goals*2)
    if assists > 0: pop = min(100, pop+1)
    if rating < 5.0: pop = max(0, pop-1)
    return pop

def _update_pop(p, goals, assists, rating):
    """하위호환 래퍼."""
    update_player(popularity=_calc_pop(p, goals, assists, rating))


def _derive_match_stats(is_home, hs, as_, goals, assists, saves, pos, detail, engine_stats=None):
    """[경기 통계] 점유율/슈팅/코너/파울/패스성공률을 만든다.

    [신규] engine_stats가 주어지면(내 경기를 새 전술 엔진으로 시뮬레이션한
    경우) — {"home":{...}, "away":{...}} 형태, 각 항목은
    {"poss","shots","shots_on","corners","fouls"} — 그 실제 시뮬레이션
    결과를 기준값으로 쓴다. 공식으로 사후에 지어내는 게 아니라 실제로
    벌어진 슈팅/코너/파울 횟수라는 뜻. 없으면(폴백 상황 등) 예전처럼
    점유율/스코어 기반 공식으로 만든다.

    설계 원칙 — 순서가 중요하다:
      1. 최종 스코어(hs/as_)와 내 개인 기록(goals/assists/saves/detail)은
         이미 확정된 값이다(_player_perf가 먼저 계산함).
      2. 팀 통계는 그 확정된 값들을 "하한선/기준점" 삼아 역산한다(또는
         engine_stats를 기준 삼는다). 그래서 절대 "내 슈팅 5개인데 팀
         슈팅 3개" 같은 모순이 생기지 않는다.
      3. engine_stats가 없을 때는 random.random()을 전혀 쓰지 않는다 —
         같은 스코어·같은 내 기록이면 항상 같은 통계가 나온다.

    점유율: engine_stats가 있으면 그 값, 없으면 스코어 차이에서 추정.
    슈팅: engine_stats가 있으면 그 값을 베이스로, 내 개인 슈팅 기록을 하한선 보장.
    유효슈팅: 최소한 그 팀이 넣은 골 수만큼은 보장(골은 유효슈팅에서만 나옴).
    코너/파울: engine_stats가 있으면 그 값, 없으면 슈팅·점유율에서 파생.
    패스 성공률: 내 개인 pass_acc를 우리 팀 값의 기준점으로 삼음.
    """
    my_score = hs if is_home else as_
    opp_score = as_ if is_home else hs

    my_eng = (engine_stats or {}).get("home" if is_home else "away")
    opp_eng = (engine_stats or {}).get("away" if is_home else "home")

    if my_eng and opp_eng:
        my_poss = max(30, min(70, my_eng.get("poss", 50)))
        opp_poss = 100 - my_poss
        my_shots = max(detail.get("shots", 0), my_eng.get("shots", 0))
        my_shots_on = max(detail.get("shots_on", 0), my_score, my_eng.get("shots_on", 0))
        my_shots = max(my_shots, my_shots_on)
        opp_shots = max(opp_score, opp_eng.get("shots", 0))
        opp_shots_on = max(opp_score, opp_eng.get("shots_on", 0))
        opp_shots = max(opp_shots, opp_shots_on)
        my_corners = my_eng.get("corners", 0)
        opp_corners = opp_eng.get("corners", 0)
        my_fouls = max(1, my_eng.get("fouls", 0))
        opp_fouls = max(1, opp_eng.get("fouls", 0))
        my_pass_acc = detail.get("pass_acc") or (0.66 + my_poss * 0.0026)
        opp_pass_acc = 0.66 + opp_poss * 0.0026
    else:
        diff = my_score - opp_score
        my_poss = 50 + round(20 * math.tanh(diff / 2.5))
        my_poss = max(30, min(70, my_poss))
        opp_poss = 100 - my_poss

        my_shots = max(detail.get("shots", 0), round(my_score * 3.2 + my_poss * 0.08))
        my_shots_on = max(detail.get("shots_on", 0), my_score, round(my_shots * 0.35))
        my_shots = max(my_shots, my_shots_on)

        opp_shots = round(opp_score * 3.2 + opp_poss * 0.08)
        opp_shots_on = max(opp_score, round(opp_shots * 0.35))
        opp_shots = max(opp_shots, opp_shots_on)

        my_corners = max(0, round(my_shots * 0.45 + my_poss * 0.02))
        opp_corners = max(0, round(opp_shots * 0.45 + opp_poss * 0.02))

        # 점유율이 낮은 쪽(수비에 더 시달리는 쪽)이 보통 파울이 더 잦다.
        my_fouls = max(4, round(15 - my_poss * 0.08))
        opp_fouls = max(4, round(15 - opp_poss * 0.08))

        my_pass_acc = detail.get("pass_acc") or (0.66 + my_poss * 0.0026)
        opp_pass_acc = 0.66 + opp_poss * 0.0026

    home_stats, away_stats = (
        {"poss": my_poss, "shots": my_shots, "shots_on": my_shots_on,
         "corners": my_corners, "fouls": my_fouls, "pass_acc": round(my_pass_acc, 3)},
        {"poss": opp_poss, "shots": opp_shots, "shots_on": opp_shots_on,
         "corners": opp_corners, "fouls": opp_fouls, "pass_acc": round(opp_pass_acc, 3)},
    ) if is_home else (
        {"poss": opp_poss, "shots": opp_shots, "shots_on": opp_shots_on,
         "corners": opp_corners, "fouls": opp_fouls, "pass_acc": round(opp_pass_acc, 3)},
        {"poss": my_poss, "shots": my_shots, "shots_on": my_shots_on,
         "corners": my_corners, "fouls": my_fouls, "pass_acc": round(my_pass_acc, 3)},
    )
    return {"home": home_stats, "away": away_stats}


def _save_match_detail(p, week, comp_name, is_home, home_name, away_name,
                       hs, as_, result, goals, assists, saves, rating,
                       events, played, benched, detail=None, pso=None, engine_stats=None,
                       engine_plog=None):
    """경기 상세를 match_details 에 저장하고 detail_id 를 돌려준다.
       리그/챔스/국대 모두 이 헬퍼를 공유한다(팀명은 호출자가 직접 넘김).
       events 정규화(분 배정·시간순)도 여기서 처리. 실패 시 None 반환.

       pso: 승부차기로 결정된 녹아웃 경기라면 {"won": bool, "score": "5-4"}
       형태로 넘긴다. None이면 승부차기 없는 일반 경기.
       engine_stats: 전술 엔진(match_sim.tactical_engine)이 만든 실제
       시뮬레이션 통계({"home":{...},"away":{...}}). 있으면 _derive_match_stats가
       공식 추정 대신 이 실측값을 기준으로 쓴다.
       engine_plog: 전술 엔진이 만든 진짜 분 단위 possession_log. 있으면
       match_flow의 사후 필러 생성 대신 이걸 개인 서사와 병합해서 쓴다."""
    timed = []
    if played:
        for ev in events:
            if isinstance(ev, tuple) and len(ev) == 2:
                timed.append((int(ev[0]), str(ev[1])))
            else:
                timed.append((random.randint(1, 90), str(ev)))
        timed.sort(key=lambda x: _min_sortkey(x[0]))

    verdict = _match_verdict(rating, result, goals, assists) if played else ""
    detail = detail or {}
    st = get_state() or {}
    team_stats = (_derive_match_stats(is_home, hs, as_, goals, assists, saves,
                                      p.get("position", ""), detail, engine_stats=engine_stats)
                 if played else None)

    # [구조 변경] 예전엔 team_stats에 잡힌 파울/코너킥 개수를 맞추려고
    # "🟨 우리 팀 파울" / "⛳ 상대 팀 코너킥" / "🚫 세트피스 코너킥, 수비에
    # 걷어내졌다" 같은 가짜 텍스트를 이 시점에 timed(개인 이벤트 목록)에
    # 직접 끼워 넣었다. 문제는 이게 "사후 땜빵"이라 뷰어 쪽에서 재개팀을
    # 텍스트("우리 팀"/"상대 팀")로 다시 파싱해야 했고, 그 파싱 자체가
    # 반복적인 버그의 원인이었다(파울 재개팀 오판 등).
    #
    # 이제 match_flow.generate_possession_log()가 이 역할을 통째로
    # 대체한다 — team_stats(슈팅/온타깃/코너/파울)와 진짜 개인 이벤트만
    # 가지고, "언제 어느 팀이 어느 구역에서 무슨 상황이었는지"를 구조화된
    # 레코드로 만든다(team 필드 = 그 통계의 주체라서 뷰어가 텍스트를 다시
    # 파싱할 필요가 없다). 그래서 가짜 텍스트를 timed에 주입하던 이
    # 블록은 완전히 불필요해졌다 — 삭제한다. timed는 이제 진짜 개인
    # 이벤트만 담은 채로 유지되고, 그걸 그대로 possession_log 생성에
    # 넘긴다.
    possession_log = []
    if played and team_stats:
        my_score = hs if is_home else as_
        opp_score = as_ if is_home else hs
        # [재설계 — 진짜 분 단위 로그] engine_stats와 짝을 이루는 진짜
        # 시뮬레이션 possession_log(engine_plog)가 있으면(=내 경기를 새
        # 전술 엔진으로 돌린 경우) 그걸 그대로 쓴다 — match_flow가 통계
        # 숫자만 보고 사후에 흩뿌리던 필러 대신, 실제로 "이 분엔 이 팀이
        # 이 레인/서드에서 우세했다"는 시뮬레이션 산출물 그 자체다. 내
        # 개인 실제 이벤트(골/도움/선방/파울/코너 텍스트)만 그 위에
        # 병합한다(발생 시각은 그대로 유지). 없으면(폴백 등) 예전처럼
        # match_flow의 통계 기반 사후 생성으로 만든다.
        if engine_plog:
            possession_log = tactical_engine.merge_personal_events(
                engine_plog, timed, "home" if is_home else "away")
        else:
            possession_log = match_flow.generate_possession_log(
                is_home, team_stats, timed, my_score, opp_score)

    # [신규] 22명 중 나(my_slot)를 뺀 21명은 지금까지 실제 선수 스탯과
    # 완전히 무관하게 움직였다(포메이션 슬롯 라벨만 있고 실제 로스터
    # 연결이 아예 없었음). 그 팀 로스터에서 포메이션 슬롯에 맞는 11명을
    # 뽑아 최소 스탯(speed/dribbling/tackling/positioning/jump/heading/
    # stamina)만 같이 저장해둔다 — match_sim_viewer.py가 이 스탯으로
    # 선수별 최고속도/턴오버 저항/인터셉트 확률/반응성을 실제로 다르게
    # 만든다.
    lineup_stats = {}
    if played:
        try:
            lineup_stats = match_flow.generate_lineup_stats(home_name, away_name)
        except Exception:
            lineup_stats = {}

    payload = {
        "events": [[m, t] for m, t in timed],
        "verdict": verdict,
        "played": bool(played),
        "benched": bool(benched),
        "position": p.get("position", ""),
        "pso": pso,
        "detail": {
            "shots": detail.get("shots", 0),
            "shots_on": detail.get("shots_on", 0),
            "key_passes": detail.get("key_passes", 0),
            "dribbles": detail.get("dribbles", 0),
            "blocks": detail.get("blocks", 0),
            "pass_acc": detail.get("pass_acc", 0.0),
        },
        "team_stats": team_stats,
    }
    try:
        conn2 = get_conn()
        cur = conn2.execute(
            """INSERT INTO match_details
               (year,week,season,league_name,is_home,home_name,away_name,
                home_score,away_score,result,rating,goals,assists,saves,
                detail_json,possession_log,lineup_stats)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (st.get("current_year"), week, st.get("current_season"),
             comp_name, 1 if is_home else 0, home_name, away_name,
             hs, as_, result, rating, goals, assists, saves,
             json.dumps(payload, ensure_ascii=False),
             json.dumps(possession_log, ensure_ascii=False),
             json.dumps(lineup_stats, ensure_ascii=False)))
        detail_id = cur.lastrowid
        conn2.commit()
        conn2.close()
        return detail_id
    except Exception:
        return None


def _augment_events_with_names(c, p, is_home, hid, aid, hs, as_,
                               goals, assists, played, events):
    """[텍스트-영상 싱크] 이벤트 문구를 다듬는다.

      - 내가 넣은 골/어시(⚽·🎯 세트피스·🎯 페널티킥·🅰)와 그 외 내 개인
        활약(선방·차단·드리블 등) → 뒤에 "(내 이름)"을 붙인다.
      - 실점(🥅)은 내가 한 행동이 아니라 상대가 넣은 것이므로 이름을
        붙이지 않는다.
      - 우리 팀이 넣었지만 내가 골도 어시도 아닌 나머지 득점은, 로스터에서
        아무 이름이나 랜덤으로 뽑아 붙이지 않고 "어떤 골인지"만(문구만)
        타임라인에 추가한다 — 이름 없는 일반 골로 표시.

    [수정 이력] 처음엔 로스터에서 동료/상대 이름을 랜덤으로 뽑아 붙였는데,
    국가별로 이름이 뒤죽박죽 나와 어색했다. 지금은 이름은 오직 "내 이름"
    하나만 쓰고, 내가 관여 안 한 골은 이름 없이 사실(득점 존재·시점·종류)만
    보여준다.
    """
    try:
        if not played:
            return events
        my_name = p.get("name") or "나"
        my_score = hs if is_home else as_

        def tag(text):
            if "🥅" in text:
                return text  # 상대 득점 — 내가 한 게 아니므로 이름 없음
            return f"{text} ({my_name})"  # 내 골/어시/선방/차단 등 개인 행동

        new_events = []
        for ev in events:
            if isinstance(ev, tuple) and len(ev) == 2:
                m, t = ev
                new_events.append((m, tag(str(t))))
            else:
                new_events.append(tag(str(ev)))

        # 내가 골도 어시도 아닌 우리 팀의 나머지 득점 — 이름 없이 문구만.
        remaining = max(0, my_score - goals - assists)
        if remaining > 0:
            for m in _sample_minutes(remaining, 3, 90):
                new_events.append((m, random.choice(GOAL_PHRASES["normal"])))

        return new_events
    except Exception:
        return events


def _write_match_log(p, week, league_name, is_home,
                     hid, aid, hs, as_,
                     result, goals, assists, saves, rating, events, played, benched,
                     detail=None, engine_stats=None, engine_plog=None):
    # [최적화] 팀명을 세션 캐시에서 조회 (매 경기 get_conn 제거)
    conn = get_conn()
    c = conn.cursor()
    hn = _team_name(c, hid, "홈팀")
    an = _team_name(c, aid, "원정팀")

    # [텍스트-영상 싱크 확장] 이벤트 텍스트에 실제 선수 이름을 붙이고, 내가
    # 직접 관여하지 않은 우리 팀의 나머지 득점도 실제 로스터 선수 이름으로
    # 채워 넣는다 — "경기 상세"가 내 개인 기록만 보여주던 것에서 실제 팀
    # 전체 경기처럼 보이게 하기 위함. 로스터 조회가 실패해도(오프라인 팀,
    # DB 이슈 등) 경기 저장 자체는 절대 막히면 안 되므로 전부 try/except로
    # 감싸고, 실패 시 이름 태깅 없이 기존 동작으로 조용히 폴백한다.
    events = _augment_events_with_names(c, p, is_home, hid, aid, hs, as_,
                                        goals, assists, played, events)
    conn.close()

    loc = "홈" if is_home else "원정"
    rs  = {"win":"승","draw":"무","loss":"패"}.get(result,"")

    detail_id = _save_match_detail(p, week, league_name, is_home, hn, an,
                                   hs, as_, result, goals, assists, saves, rating,
                                   events, played, benched, detail, engine_stats=engine_stats,
                                   engine_plog=engine_plog)

    # ── 로그: 헤더 한 줄(클릭 가능) + 결과 + 핵심 요약 + 순위 ──────────
    #   상세 이벤트(전/후반)는 로그에서 빼고 상세 창으로 옮겨 로그를 간결하게.
    #   헤더에 [match:{id}] 마커를 박아두면 log_panel 이 클릭 앵커로 변환한다.
    marker = f" [match:{detail_id}]" if detail_id else ""
    add_log("─"*44, "sep")
    add_log(f"⚽ 경기  [{league_name}]  {week}주차  ({loc}){marker}", "match")
    add_log(f"   {hn} {hs}-{as_} {an}  ({rs})", "match")

    if not played:
        add_log("   🪑 벤치 대기" if benched else "   🚑 부상 결장", "match")
    else:
        if p["position"] == "GK":
            add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
        else:
            add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")
        # 다득점/멀티어시 같은 하이라이트만 로그에 한 줄 노출(나머진 상세 창에서).
        timed = sorted([(int(e[0]), e[1]) if isinstance(e, tuple) else
                        (random.randint(1, 90), str(e)) for e in events],
                       key=lambda x: _min_sortkey(x[0]))
        hi = _log_highlight(goals, assists, timed)
        if hi:
            add_log(f"   {hi}", "match")

    rank_str = get_team_rank(p.get("current_team_id",0))
    add_log(f"   📊 리그 순위: {rank_str}", "match")


def _log_highlight(goals, assists, timed):
    """로그에 한 줄로 노출할 경기 하이라이트(있으면). 다득점/멀티어시 우선."""
    banner = _multigoal_banner(goals)
    if banner:
        return banner
    if assists >= 2:
        return "🅰🔥 멀티 어시스트!"
    # 극장골/역전골이 있으면 그걸 끌어올린다.
    for _m, t in timed:
        if "극장골" in t or "역전골" in t or "결승골" in t:
            return t
    return ""


def _match_verdict(rating, result, goals, assists):
    """평점·결과·공격포인트를 종합해 총평 문구를 고른다(맥락 기반 다양화)."""
    contrib = goals + assists
    if rating >= 8.5:
        key = "great_win" if result == "win" else "great"
    elif rating >= 7.5:
        key = "great" if contrib >= 2 else ("good_win" if result == "win" else "good")
    elif rating >= 6.8:
        key = "good_win" if result == "win" else "good"
    elif rating >= 6.0:
        key = "good" if result == "win" else "average"
    elif rating >= 5.0:
        # 패했지만 평점이 받쳐주면 '분투' 톤
        key = "loss_effort" if result == "loss" and contrib >= 1 else "poor"
    else:
        key = "terrible"
    return random.choice(VERDICT_PHRASES.get(key, VERDICT_PHRASES["average"]))


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


def get_team_rank(team_id, conn=None, season=None) -> str:
    """팀 순위 문자열 반환. conn/season 주어지면 재사용."""
    if not team_id:
        return "정보 없음"
    rows = get_league_standings_by_team(team_id, conn=conn, season=season)
    if not rows:
        return "정보 없음"
    for i, r in enumerate(rows):
        if r["id"] == team_id:
            rank = i + 1
            if (i > 0
                    and rows[i-1]["pts"] == r["pts"]
                    and rows[i-1]["gd"] == r["gd"]
                    and rows[i-1].get("goals_for", rows[i-1].get("gf", 0))
                        == r.get("goals_for", r.get("gf", 0))):
                rank_str = f"공동 {rank}위"
            else:
                rank_str = f"{rank}위"
            return f"{rank_str}  ({r['wins']}승 {r['draws']}무 {r['losses']}패 / 승점 {r['pts']}점)"
    return "정보 없음"


def get_league_standings_by_team(team_id, conn=None, season=None):
    """팀 ID로 해당 리그 순위표 반환. conn/season 주어지면 재사용."""
    own = conn is None
    if own:
        conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT league_id FROM teams WHERE id=?", (team_id,))
    row = c.fetchone()
    if own:
        conn.close()
    if not row:
        return []
    return get_league_standings(row["league_id"], season=season,
                                conn=None if own else conn)


def get_league_standings(league_id, season=None, conn=None):
    """순위표: match_results에서 직접 집계해서 항상 정확한 값 반환.
    [최적화] season/conn 파라미터 추가 — 외부에서 열린 커넥션 재사용 가능."""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    c = conn.cursor()

    if season is None:
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

    if own_conn:
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

    new_rows = []  # (league_id,week,home_team_id,away_team_id,season,year) - executemany 배치용

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
            new_rows.append((league_id, week, t1, t2, season, year))
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
            new_rows.append((league_id, week, t1, t2, season, year))
            existing_matches.add(key)
            existing_matches.add(rkey)

    if new_rows:
        c.executemany("""INSERT INTO match_results
                         (league_id,week,home_team_id,away_team_id,
                          home_score,away_score,season,year)
                         VALUES(?,?,?,?,-1,-1,?,?)""", new_rows)

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
    # monthly가 0이 되지 않도록 최솟값 1천원 보장
    # (F급 tier2~3 등 초저연봉: salary=3~100천원 → salary//12=0 → 무급 표시 버그)
    monthly = max(1, salary // 12)
    # 에이전트 수수료: 개별 계약 수수료율(agent_fee_rate)이 있으면 그것,
    # 없으면(0) 등급 기본값. 같은 등급이라도 계약마다 수수료가 다를 수 있다.
    fee = p.get("agent_fee_rate", 0) or AGENT_FEE_RATE.get(p.get("agent_grade","F"), 0)
    net = max(1, int(monthly * (1-fee)))  # 수수료 후도 최소 1천원
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
    # [버그수정] age 컬럼 사용. 이 함수는 나이 증가 전에 호출되고 cur_year=year+1
    #   (다음해)이므로, 다음해 기준 나이는 현재 age 컬럼 + 1.
    age = (p.get("age", 0) or 0) + 1
    if age > 21:
        return                      # 21세 넘으면 소속 자동확정, 귀화 불가
    if p.get("intl_capped", 0):
        return                      # 이미 본선 출전(cap-tie) → 변경 불가
    if p.get("intl_committed", ""):
        return                      # 이미 대표팀 영구고정
    if new_years < 2:
        return                      # 거주 2년 미충족

    # 이미 보유한 국적(출생/귀화)이면 스킵
    owned = {p.get("nationality","") or "", p.get("nationality2","") or "",
             p.get("nationality3","") or ""}
    nat_list = [n for n in (p.get("naturalized_nats","") or "").split(",") if n]
    owned |= set(nat_list)
    if club_country in owned:
        return
    # 빈 국적 슬롯에 귀화 국적 추가 (nationality2 → nationality3)
    # 국기도 함께 저장해 연혁/표시에서 깃발이 비지 않게 한다.
    conn2 = get_conn()
    frow = conn2.execute("SELECT flag FROM countries WHERE name=?", (club_country,)).fetchone()
    conn2.close()
    club_flag = frow["flag"] if frow else ""
    if not (p.get("nationality2","") or ""):
        update_player(nationality2=club_country, flag2=club_flag)
    elif not (p.get("nationality3","") or ""):
        update_player(nationality3=club_country, flag3=club_flag)
    else:
        return                      # 국적 슬롯이 꽉 참(이미 3개)
    nat_list.append(club_country)
    update_player(naturalized_nats=",".join(nat_list))
    # [국적 연혁] 귀화 획득 사건 기록 (현재 연도/주차)
    add_nat_history("naturalize", club_country, club_flag, cur_year, 1)
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

    # [최적화] my_player + season_state 갱신을 하나의 커넥션으로 묶어 커밋 2회→1회
    conn_adv = get_conn()
    conn_adv.execute(
        "UPDATE my_player SET current_year=?,current_week=?,current_season=? WHERE id=1",
        (new_year, new_week, new_season))
    rows_ss = conn_adv.execute("SELECT id FROM season_state WHERE id=1").fetchone()
    if rows_ss:
        conn_adv.execute(
            "UPDATE season_state SET current_year=?,current_week=?,current_season=? WHERE id=1",
            (new_year, new_week, new_season))
    else:
        conn_adv.execute(
            "INSERT INTO season_state(id,current_year,current_week,current_season) VALUES(1,?,?,?)",
            (new_year, new_week, new_season))
    conn_adv.commit()
    conn_adv.close()

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
            intl_engine.start_intl_tournament(new_year)
        except Exception as e:
            add_log(f"⚠ 국제대회 생성 오류: {e}", "event")

    # 41주차 진입: 클럽 대륙 챔피언스리그 시작 (매년)
    #   리그 경기는 35주에 끝나므로 직전 시즌(current_season) 순위로 출전팀 선발.
    from champions_engine import CL_START_WEEK
    if new_week == CL_START_WEEK:
        try:
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
        return _calc_clean_sheets(c, tid, season, matches=p.get("season_matches", 0))
    except Exception:
        return 0
    finally:
        conn.close()


def _estimate_ai_season(ovr, pos, team_avg, league_avg):
    """AI 선수의 시즌 성적(골/도움/평점)을 추정.
    [설계 변경] 골/도움은 더 이상 OVR로 스케일링하지 않는다 — 신민용 지적:
    "OVR 70이든 90이든 99든 같은 조건이어야 한다"(주전 스트라이커는 실력과
    무관하게 팀 내 슈팅 기회를 비슷하게 가져간다). 포지션별 고정 기준치
    (AWARD_POS_GOAL/ASSIST)에 소속팀 강도(team_avg-league_avg)만 살짝
    반영하고, 실력 차이는 rating(평점)에서만 OVR로 반영한다."""
    g_base = AWARD_POS_GOAL.get(pos, 1) + (team_avg-league_avg)*0.2
    goals = max(0, round(max(0, g_base) * random.uniform(0.8, 1.2)))
    a_base = AWARD_POS_ASSIST.get(pos, 1) + (team_avg-league_avg)*0.1
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
    """리그 내 모든 팀의 AI 공격 포지션 선수들 시즌 성적 추정 → 후보 리스트.

    [최적화] 기존엔 팀마다 ai_players를 2번씩(전체 OVR 집계용 + 공격수 목록용)
      조회해 20팀 리그면 40+ 쿼리(N+1)가 돌았다. teams JOIN으로 팀별 평균 OVR을
      1쿼리에, 공격수 목록을 1쿼리에 모아 총 2쿼리로 줄였다. 결과·계산은 동일.
    """
    # 팀별 평균 OVR + 리그 평균을 단일 JOIN 집계로.
    team_rows = c.execute(
        """SELECT t.id AS tid, AVG(ap.ovr) AS avg_ovr, COUNT(ap.id) AS n,
                  SUM(ap.ovr) AS sum_ovr
           FROM teams t LEFT JOIN ai_players ap ON ap.team_id=t.id
           WHERE t.league_id=?
           GROUP BY t.id""", (league_id,)).fetchall()
    if not team_rows:
        return [], 50.0

    team_avg = {}
    tot_sum = 0
    tot_n = 0
    for r in team_rows:
        if r["n"]:
            team_avg[r["tid"]] = r["avg_ovr"]
            tot_sum += r["sum_ovr"] or 0
            tot_n   += r["n"]
    league_avg = (tot_sum / tot_n) if tot_n else 50.0

    # 공격수 전체를 단일 쿼리로 (팀별 반복 제거). ATTACK_POS 는 코드 내 고정 튜플.
    placeholders = ",".join("?" for _ in ATTACK_POS)
    atk_rows = c.execute(
        """SELECT ap.team_id AS tid, ap.name, ap.position, ap.ovr
           FROM ai_players ap JOIN teams t ON ap.team_id=t.id
           WHERE t.league_id=? AND ap.position IN ({})""".format(placeholders),
        (league_id, *ATTACK_POS)).fetchall()

    cands = []
    for r in atk_rows:
        tavg = team_avg.get(r["tid"], league_avg)
        g, a, rt = _estimate_ai_season(r["ovr"], r["position"], tavg, league_avg)
        cands.append({
            "name": r["name"], "position": r["position"], "ovr": r["ovr"],
            "goals": g, "assists": a, "rating": rt, "is_mine": False,
        })
    return cands, league_avg


def _zamora_tally(c, p, year, league_id, lname, live_matches, live_ga):
    """사모라상 산정용: '같은 시즌·같은 리그'에서 뛴 출전수·실점을 합산한다.

    [규칙] 시즌 중 이적해도, 이적 전후 리그가 같으면(예: 토트넘→첼시 모두
      프리미어리그) 두 팀의 리그 출전·실점을 한 시즌으로 합쳐서 사모라상을
      심사한다. 다른 리그로 옮기면(예: 토트넘→레알) 합치지 않는다.

      합산 소스는 career_entries(팀별로 matches·goals_against·league_name·
      start_year 저장). 현재 팀의 라이브 값(live_matches/live_ga)을 베이스로,
      같은 해(start_year==year)·같은 리그명(lname)인 '다른 팀(닫힌)' 항목을 더한다.

      ※ GA 폴백: 구버전 데이터는 career_entries.goals_against 가 0으로 누락돼
        있을 수 있다. matches>0 인데 GA==0 인 GK 항목은 match_results 에서
        그 팀이 그 시즌 리그에서 먹은 골을 재계산해 보정한다(출전수로 캡).

    반환: (총출전경기, 총실점)
    """
    total_m  = int(live_matches or 0)
    total_ga = int(live_ga or 0)
    cur_tid  = p.get("current_team_id", 0)
    try:
        rows = c.execute(
            """SELECT team_id, matches, goals_against
               FROM career_entries
               WHERE start_year=? AND league_name=? AND team_id<>? AND matches>0""",
            (year, lname, cur_tid)).fetchall()
    except Exception:
        rows = []
    for r in rows:
        m  = int(r["matches"] or 0)
        ga = int(r["goals_against"] or 0)
        # GA 누락 보정: match_results 에서 해당 팀이 그 리그·시즌에 먹은 골 합
        if ga == 0 and m > 0:
            try:
                q = c.execute(
                    """SELECT COALESCE(SUM(CASE
                            WHEN home_team_id=? THEN away_score
                            WHEN away_team_id=? THEN home_score END),0) AS ga,
                              COUNT(*) AS gp
                       FROM match_results
                       WHERE league_id=? AND home_score>=0
                         AND (home_team_id=? OR away_team_id=?)""",
                    (r["team_id"], r["team_id"], league_id,
                     r["team_id"], r["team_id"])).fetchone()
                team_ga = int(q["ga"] or 0) if q else 0
                team_gp = int(q["gp"] or 0) if q else 0
                # 팀 전체 실점을 선수 출전 경기 비율로 귀속(안 뛴 경기 실점 제외).
                if team_gp > 0:
                    ga = round(team_ga * min(m, team_gp) / team_gp)
            except Exception:
                ga = 0
        total_m  += m
        total_ga += ga
    return total_m, total_ga


def _process_awards(p, year, season_goals, season_assists, season_rating, season_cs, season_goals_against=0):
    """시즌 종료 시 개인 수상 산정. 내 선수 실제 성적 + AI 추정 비교.

    [득점왕/도움왕 최소 기준]
      단순히 'pool 내 1위'만으로 주면, 약체 리그에서 AI 추정치가 우연히 낮게
      깔린 시즌엔 2골/2도움으로도 타이틀이 나오는 비현실적 상황이 생긴다.
      → 1위 조건에 더해 '출전 경기수 기반 최소 산출 기준'을 통과해야 수상.
         (풀시즌 7라운드*2 = 14경기 기준. 경기당 최소 생산성으로 환산)
    """
    tid = p.get("current_team_id", 0)
    if not tid:
        return  # 무소속이면 수상 없음
    conn = get_conn(); c = conn.cursor()
    try:
        lrow = c.execute("""SELECT l.id as lid, l.name as lname, l.tier,
                                   cn.grade as grade, cn.name as cname
                            FROM teams t JOIN leagues l ON t.league_id=l.id
                            JOIN countries cn ON l.country_id=cn.id
                            WHERE t.id=?""", (tid,)).fetchone()
        if not lrow:
            conn.close(); return
        from constants import get_league_grade
        league_id, lname, tier = lrow["lid"], lrow["lname"], lrow["tier"]
        # 발롱도르 등 수상 판정엔 리그 등급 사용 (국대 등급 아님)
        grade = get_league_grade(lrow["cname"], lrow["grade"])

        cands, league_avg = _collect_league_candidates(c, league_id)
        # 내 선수 추가
        me = {
            "name": p.get("name","나"), "position": p.get("position","ST"),
            "ovr": p.get("ovr",40), "goals": season_goals, "assists": season_assists,
            "rating": season_rating, "is_mine": True,
        }
        pool = cands + [me]

        my_awards = []  # (award_type, detail)

        # ── 득점왕/도움왕 최소 산출 기준 ───────────────────────────
        # 풀시즌 리그 경기 = 14경기(상/하반기 7R씩). 내가 실제로 뛴 경기수를
        # 기준으로 최소 기준을 비례 조정한다(중도 합류/이적 시 너무 빡빡하지 않게).
        #   - 풀시즌 기준 득점왕 최소 8골 / 도움왕 최소 6도움.
        #   - 절대 하한(아무리 적게 뛰어도): 득점왕 4골 / 도움왕 4도움.
        # 이렇게 하면 '2도움 도움왕' 같은 표본 부족 타이틀이 사라진다.
        FULL_SEASON_MATCHES = 14
        sm = max(1, p.get("season_matches", 0))
        play_ratio = min(1.0, sm / FULL_SEASON_MATCHES)
        min_goals_for_title  = max(4, round(8 * play_ratio))
        min_assists_for_title = max(4, round(6 * play_ratio))

        # 득점왕 (pool 1위 + 최소 골 기준 충족)
        top_scorer = max(pool, key=lambda x: (x["goals"], x["rating"]))
        if top_scorer["is_mine"] and season_goals >= min_goals_for_title:
            my_awards.append(("득점왕", f"{season_goals}골"))

        # 도움왕 (pool 1위 + 최소 도움 기준 충족)
        top_assist = max(pool, key=lambda x: (x["assists"], x["rating"]))
        if top_assist["is_mine"] and season_assists >= min_assists_for_title:
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
                # [최적화] is_mine인 후보는 항상 p 자신이라 cs_for_me와 값이
                #  같다. 매번 _calc_clean_sheets_for_player(p)를 다시 불러
                #  DB를 재조회하는 대신 위에서 이미 계산한 값을 재사용한다.
                gk_scores = []
                for x in gk_cands:
                    cs_est = cs_for_me if x["is_mine"] else max(0, int(p.get("season_cs", 0) * 0.5))
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
        # [버그 수정 — 근본 원인] 이 게임의 "풀시즌"은 14경기(위 득점왕/
        # 도움왕 기준과 같은 FULL_SEASON_MATCHES)인데, 원래 "골+도움 30+"은
        # 38경기급 실제 리그 풀시즌(메시/호날두 전성기, 경기당 0.79G+A)을
        # 그대로 옮겨온 값이라 14경기 안에서는 사실상 도달 불가능했다
        # (경기당 2.14를 요구하는 셈 — 실측: 11시즌 내내 두 자릿수 골에
        # 평점 7.8~8.4를 찍고도 이 조건 하나로 단 한 번도 발롱도르를 못
        # 받음). 득점왕/도움왕과 똑같이 이 게임의 실제 풀시즌(14경기)
        # 기준으로 다시 잡는다 — 실제 세계 최정상급 비율(38경기 30G/A ≈
        # 경기당 0.79)을 14경기에 그대로 적용하면 약 11.
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
            # (골+도움 최소 기준 충족 또는 MVP 수상)
            world_class = p.get("ovr",0) >= rival_ovr - 2
            min_ga_for_ballon = max(6, round(11 * play_ratio))
            dominant = (season_goals + season_assists >= min_ga_for_ballon) or mvp["is_mine"]
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
        # [버그 수정 — 근본 원인] 실제 푸스카스상은 전세계에서 딱 1명
        # 뽑히는 상인데, 후보 비교가 내 리그 pool(multi_goal_cands)에만
        # 갇혀 있었다 — 발롱도르와 달리 "다른 리그와 비교"하는 게이트가
        # 아예 없어서, 국가·티어 개수만큼 각자 따로 수여될 수 있는
        # 구조였다(신민용 지적). 발롱도르와 같은 패턴(S/A등급 1부리그
        # 최고 공격 OVR 조회)으로 세계급 게이트를 추가한다. 다만
        # "그 해 최고의 선수"까지 요구하는 발롱도르(-2)보다는 마진을
        # 느슨하게 잡는다 — 한 경기의 멀티골+고평점이면 되는 상이라
        # 시즌 전체 지배력까지는 필요 없기 때문.
        _PUSKAS_OVR_MARGIN = 6
        multi_goal_cands = [x for x in pool if x["goals"] >= 2]
        if multi_goal_cands:
            puskas_best = max(multi_goal_cands, key=lambda x: x["rating"])
            if puskas_best["is_mine"]:
                _pk_rival = c.execute("""SELECT MAX(a.ovr) as mo FROM ai_players a
                    JOIN teams t ON a.team_id=t.id
                    JOIN leagues l ON t.league_id=l.id
                    JOIN countries cn ON l.country_id=cn.id
                    WHERE cn.grade IN ('S','A') AND l.tier=1 AND a.position IN ({})
                    """.format(",".join("'%s'" % pp for pp in ATTACK_POS))).fetchone()
                _pk_rival_ovr = _pk_rival["mo"] if _pk_rival and _pk_rival["mo"] else 90
                if p.get("ovr", 0) >= _pk_rival_ovr - _PUSKAS_OVR_MARGIN:
                    my_awards.append(("푸스카스상", f"{season_goals}골, 평점 {season_rating:.1f}"))

        # 사모라 상 (최저 실점 골키퍼 — 경기당 평균 실점 최소)
        #   조건: GK && 1부리그 && (같은 리그 합산) 출전 >= ZAMORA_MIN_MATCHES
        #        && 경기당 1.2골 이하
        #   ※ 시즌 중 같은 리그 안에서 이적하면 두 팀 리그 기록을 합산한다
        #     (다른 리그로 옮기면 합치지 않음). _zamora_tally 참고.
        # [버그 수정 — 근본 원인] 원래 사모라상은 스페인 1부리그(라리가)
        # 전용 상인데, 여기선 국가·티어 제한이 전혀 없어서 어느 나라
        # 몇부 리그의 GK든 조건만 맞으면 받을 수 있었다(신민용 지적).
        # 특정 국가 하나로 좁히는 대신, 발롱도르와 같은 원칙(최상위
        # 플라이트만 인정)으로 최소한 1부리그로는 제한한다.
        if p.get("position") == "GK" and tier == 1:
            z_matches, z_ga = _zamora_tally(
                c, p, year, league_id, lname,
                p.get("season_matches", 0), season_goals_against)
            if z_matches >= ZAMORA_MIN_MATCHES:
                my_ga_rate = z_ga / z_matches if z_matches > 0 else 999
                # 대부분의 GK는 경기당 1.3~1.5골 실점, 우수한 GK는 1.0~1.2.
                # 임계값 1.2 이하면 수상 가능.
                if my_ga_rate <= 1.2:
                    my_awards.append(("사모라상",
                        f"경기당 {my_ga_rate:.2f}골 실점 ({z_ga}/{z_matches}경기)"))

        # 저장 (DB 작업은 이 conn으로 모두 처리)
        # 개인 수상의 리그명에도 우승/트로피 기록과 동일하게 "(N부)"를 표기.
        #   awards 테이블엔 tier 컬럼이 없으므로 league_name 문자열에 합쳐 저장한다.
        #   → 수상 창·은퇴 후 창·AI 요약 등 awards.league_name 을 읽는 모든 곳이 자동 반영.
        lname_with_tier = f"{lname} ({tier}부)" if tier else lname
        for atype, detail in my_awards:
            c.execute("INSERT INTO awards(year,award_type,league_name,detail,is_mine) VALUES(?,?,?,?,1)",
                      (year, atype, lname_with_tier, detail))
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


def _recalc_field_pos_after_offseason(p):
    """오프시즌 포메이션 셔플 후 내 field_pos 재계산.
    감독이 포메이션을 바꾸면 내 배치 포지션도 달라질 수 있다.
    """
    if not p or not p.get("current_team_id"):
        return
    try:
        from constants import POSITION_COMPAT, FORMATION_SLOTS
        conn = get_conn()
        row = conn.execute(
            "SELECT formation FROM teams WHERE id=?",
            (p["current_team_id"],)).fetchone()
        conn.close()
        if not row:
            return
        _formation = row["formation"] or "4-4-2"
        _slots = FORMATION_SLOTS.get(_formation, FORMATION_SLOTS["4-4-2"])
        _primary = p.get("position", "CM")
        _compat = POSITION_COMPAT.get(_primary, [_primary])
        _best_pos, _best_rank = _primary, 0
        _best_found = 999
        for _slot in _slots:
            if _slot in _compat:
                _rank = _compat.index(_slot)
                if _rank < _best_found:
                    _best_found = _rank
                    _best_pos = _slot
                    _best_rank = _rank
        pass  # field_pos는 런타임 계산 (get_field_pos), DB 저장 불필요
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
            # [최적화] get_player() 재조회 없이 p 딕셔너리 직접 갱신
            p = dict(p)
            p["total_assets"] = new_assets
            p["total_earnings"] = new_earn

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

    # 3. 나이 증가 + 스탯 노화 (재능 티어 × 나이구간 × 계열 차등)
    new_age = p["age"] + 1
    stat_updates: dict = {"age": new_age, "total_seasons": p.get("total_seasons",0)+1}

    # 29세부터 노화. 28세 이하는 낙폭 0.
    if new_age >= 29:
        from constants import (AGING_DECLINE, AGING_DECLINE_WC_TOP, AGING_WC_TOP_OVR,
                               AGING_GROUP_WEIGHT, AGING_LIMITED_LATE_MENTAL,
                               AGING_POS_MULT, AGING_STAT_FLOOR,
                               PHYSICAL_STATS, TECHNICAL_STATS, MENTAL_STATS)
        tier = p.get("talent_tier", "pro")
        # 구버전 호환: 예전 키를 새 키로 변환
        _tier_compat = {"normal": "pro", "limited": "semipro",
                        "gifted": "worldclass", "mid": "elite"}
        tier = _tier_compat.get(tier, tier)
        pos  = p.get("position", "CM")

        # [티어별 나이구간 연간 OVR 낙폭] 선택.
        #   worldclass 중 전성기 천장(talent_cap) 98+ 는 더 완만한 wc_top 곡선.
        if tier == "worldclass" and p.get("talent_cap", p.get("ovr", 0)) >= AGING_WC_TOP_OVR:
            decline_tbl = AGING_DECLINE_WC_TOP
        else:
            decline_tbl = AGING_DECLINE.get(tier, AGING_DECLINE["pro"])

        # 이번 나이의 '연간 OVR 낙폭(D)' 조회.
        annual_drop = 0.0
        for a0, a1, d in decline_tbl:
            if a0 <= new_age <= a1:
                annual_drop = d
                break

        if annual_drop > 0:
            pos_mult = AGING_POS_MULT.get(pos, 1.0)

            # 계열 비중 정규화: 전체 스탯에 평균 1.0이 되도록.
            def _group_of(s):
                if s in PHYSICAL_STATS:  return "physical"
                if s in TECHNICAL_STATS: return "technical"
                return "mental"

            # ordinary/semipro 노년(41세+)은 멘탈도 일부 깎는다.
            gw = dict(AGING_GROUP_WEIGHT)
            if tier in ("ordinary", "semipro") and new_age >= AGING_LIMITED_LATE_MENTAL["age"]:
                gw["mental"] = AGING_LIMITED_LATE_MENTAL["weight"]

            avg_w = sum(gw[_group_of(s)] for s in ALL_STATS) / len(ALL_STATS)
            if avg_w <= 0:
                avg_w = 1.0

            for stat in ALL_STATS:
                share = gw[_group_of(stat)] / avg_w      # 멘탈=0 → 안 깎임
                if share <= 0:
                    continue
                # 이번 시즌 이 스탯의 감소량 = D × 계열비중 × 포지션배수 × 랜덤(0.85~1.15)
                drop = annual_drop * share * pos_mult * random.uniform(0.85, 1.15)
                if drop <= 0:
                    continue

                # (a) 현재 스탯 직접 감소 — 훈련으로 다 메우지 못하게(핵심).
                cur = p.get(stat, 40)
                new_cur = max(AGING_STAT_FLOOR, round(cur - drop))
                if new_cur < cur:
                    stat_updates[stat] = new_cur

                # (b) 천장(_max)도 같은 양만큼 끌어내림(현재값이 다시 차오르는 것 방지).
                #     단 천장은 현재값 밑으론 안 내려가게(논리 일관).
                mk = f"{stat}_max"
                old_mx = p.get(mk, 80)
                new_mx = max(AGING_STAT_FLOOR, new_cur, round(old_mx - drop))
                if new_mx < old_mx:
                    stat_updates[mk] = new_mx

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
    _process_promotion_relegation(year, season_avg_rating)

    # 5.7 [AI 선수 생애주기] 나이+1·성장/노화·은퇴/세대교체·이적시장·전술변경.
    #   → 같은 팀에 오래 있어도 매 시즌 스쿼드/전력/포메가 살아 움직인다.
    #   ai_players.ovr·team_id가 바뀌므로 내부에서 OVR 캐시를 무효화한다.
    try:
        from ai_lifecycle import run_ai_offseason
        run_ai_offseason(year, verbose_log=add_log)
    except Exception as _e:
        add_log(f"⚠ 이적시장 처리 중 오류: {_e}", "event", year, 52)

    # 6. 강제 방출 체크 (이슈8 강화) — 우승 판정이 끝난 뒤에 처리
    p = get_player() or p   # 승강으로 리그/연봉이 바뀌었을 수 있으니 최신화
    # 오프시즌 포메이션 변경 후 field_pos 재계산
    _recalc_field_pos_after_offseason(p)

    _check_forced_release(p, year)

    # 7. (구) 연말 국제대회 일괄 시뮬 → 시즌 중 17~24주 실경기 방식으로 대체됨 (intl_engine)

    # 8. 계약 만료 체크
    p2 = get_player()
    if p2 and p2.get("current_team_id"):
        end_yr  = p2.get("contract_end_year", 0)
        if end_yr and year >= end_yr:
            # 팀의 재계약 의사 결정
            avg_r = season_avg_rating
            rel   = p2.get("manager_relation", 50)
            _grp  = POS_GROUP.get(p2.get("position","CM"), "미드")
            _base = RENEW_RATING.get(_grp, 6.3)

            # [OVR 격차 기반 재계약 거부] 팀 평균OVR(본인 제외) 대비 격차가
            # 리그등급 기준 이상이면 감독관계/평점 무관하게 재계약 안 함
            from constants import RELEASE_GAP_BY_GRADE
            _glow2 = _my_grade_tier(p2)
            _ovr_gap = 0
            if _glow2:
                _g2, _t2, _c2 = _glow2
                _team_avg2 = _my_team_avg_ovr(p2)
                _ovr_gap = _team_avg2 - p2.get("ovr", 40)
                _rel_threshold2 = RELEASE_GAP_BY_GRADE.get(_g2, 5)
            else:
                _rel_threshold2 = 5
            ovr_too_low = (_ovr_gap >= _rel_threshold2)

            wants_renew = (avg_r >= _base or rel >= 60) and not ovr_too_low
            if wants_renew:
                # 재계약 의사 있음 → UI 팝업용 플래그 저장
                # [버그수정] 기존: 현재 salary에 배율 적용 → 승강 후에도 이전 tier 연봉 기준
                # 수정: 현재 소속 리그/tier 기준으로 _calc_salary 재계산
                _gt = _my_grade_tier(p2)
                if _gt:
                    _rg2, _rt2, _rc2 = _gt
                    _fair_sal = _calc_salary(_rg2, _rt2, p2.get("ovr", 60), _rc2)
                else:
                    _fair_sal = p2.get("salary", 0)
                # 평점에 따라 ±15% 가감
                if avg_r >= _base + 0.5:   new_sal = int(_fair_sal * 1.15)
                elif avg_r >= _base:       new_sal = int(_fair_sal * 1.05)
                else:                      new_sal = int(_fair_sal * 0.95)
                _age = new_age
                if _age >= 33:
                    renew_yrs = 1
                elif _age >= 31:
                    renew_yrs = random.choices([1, 2], [65, 35])[0]
                elif _age >= 29:
                    renew_yrs = random.choices([1, 2, 3], [25, 50, 25])[0]
                elif _age <= 28 and avg_r >= _base + 0.5:
                    renew_yrs = random.choices([2, 3], [30, 70])[0]
                else:
                    renew_yrs = random.choices([1, 2, 3], [15, 45, 40])[0]
                update_player(_contract_renew_offer=new_sal,
                              _contract_renew_years=renew_yrs)
                add_log(f"📋 계약 만료! 팀에서 {renew_yrs}년 재계약을 제안합니다. "
                        f"(제시 연봉: {fmt_money(new_sal)})", "event", year, 52)
            else:
                if ovr_too_low:
                    add_log(f"📋 계약 만료. 팀 수준에 미달해 재계약을 원하지 않습니다. (격차 {_ovr_gap:+.0f})", "event", year, 52)
                else:
                    add_log(f"📋 계약 만료. 팀에서 재계약을 원하지 않습니다.", "event", year, 52)
                _save_career_entry(p2, year, 52, transfer_type="방출",
                                   allow_insert=False, exit_type="계약만료")
                update_player(current_team_id=0, current_league_id=0,
                              salary=0, contract_years=0, contract_end_year=0,
                              _contract_renew_offer=0)

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

    cur_happy = p.get("happiness", 50)  # [최적화] p에서 직접 읽기 (get_player 재조회 제거)
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
    """방출 조건: 팀 평균OVR(본인 제외) 대비 격차가 리그등급 기준 이상 AND 감독관계 30 미만.
    재계약 거부: 격차 기준 초과 시 감독관계 무관 (계약 만료 시 별도 처리).
    오버페이 + 부진 선수는 방출 전 하위팀 이적(팔림) 우선 시도."""
    rel  = p.get("manager_relation", 50)
    tid  = p.get("current_team_id", 0)
    if not tid:
        return

    # ── 막 합류한 선수 보호 ───────────────────────────────
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

    rc = p.get("season_rating_cnt",0); rs = p.get("season_rating_sum",0.0)
    avg_rating = round(rs/rc,2) if rc > 0 else 6.0

    # 팀 평균OVR(본인 제외) 대비 격차
    team_avg = _my_team_avg_ovr(p)
    gap = team_avg - p.get("ovr", 40)   # +면 내가 팀 수준에 못 미침

    # 리그 등급 기반 방출 기준 조회
    from constants import RELEASE_GAP_BY_GRADE, RELEASE_REL_THRESHOLD
    _glow = _my_grade_tier(p)
    if _glow:
        _grade, _tier, _country = _glow
        release_threshold = RELEASE_GAP_BY_GRADE.get(_grade, 5)
    else:
        release_threshold = 5

    # ── [기능2] 감독 성향(release_relax)으로 임계치 조정 ──
    from constants import MANAGER_TYPES, OFFER_AMBITION
    mt = MANAGER_TYPES.get(p.get("manager_type", "베테랑 신뢰"), {})
    relax = mt.get("release_relax", 0.0)
    press = OFFER_AMBITION.get(p.get("club_ambition", "중위권 안정"), {}).get("press", 1.0)
    # 관대한 감독은 기준을 1~2 올려주고, 성과주의 감독은 더 빡빡하게
    adjusted_threshold = release_threshold + round(relax * 2) - round((press - 1.0) * 1)

    # ── 오버페이 + 부진 → 팔림(강제 이적) 우선 시도 ──
    cur_salary = p.get("salary", 0)
    cur_ovr    = p.get("ovr", 40)
    if _glow and cur_salary > 0:
        _grade2, _tier2, _country2 = _glow
        fair_salary = _calc_salary(_grade2, _tier2, cur_ovr, _country2)
        overpay = (cur_salary / fair_salary) if fair_salary > 0 else 99
        is_overpaid = overpay >= 1.6
        is_underperforming = (avg_rating < 6.3 and rc >= 5) or (gap >= adjusted_threshold)
        contract_left = p.get("contract_end_year", 0) > year
        if is_overpaid and is_underperforming and contract_left:
            if _try_sell_player(p, year, cur_ovr):
                return   # 팔림 처리 완료 → 방출 로직 건너뜀

    # ── 핵심 방출 조건: 격차 기준 초과 AND 감독관계 30 미만 ──
    cond_level = gap >= adjusted_threshold and rel < RELEASE_REL_THRESHOLD
    # 감독관계 20 미만은 격차 무관 방출 (극단적 불화)
    cond_hostile = rel < 20 and avg_rating < 5.5 and rc >= 5

    if cond_level or cond_hostile:
        if rel < 20:
            reason = "감독 관계 극도 악화"
        else:
            reason = "리그 수준 미달 + 감독 신뢰 상실"
        _save_career_entry(p, year, 52, transfer_type="방출", allow_insert=False,
                           exit_type="방출")
        add_log(f"😡 {reason}으로 방출!  {year}년  (평점 {avg_rating}, 감독관계 {rel}, 수준격차 {gap:+.0f})", "event", year, 52)
        update_player(current_team_id=0, current_league_id=0,
                      salary=0, manager_relation=50,
                      contract_years=0, contract_end_year=0)


def _my_grade_tier(p):
    """내 소속 팀의 (리그등급, 리그티어, 국가명) 반환. 무소속이면 None.
    [리그등급 분리] 국대 등급 대신 COUNTRY_LEAGUE_GRADE 사용."""
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
    from constants import get_league_grade
    league_grade = get_league_grade(row["country"], row["grade"])
    return (league_grade, row["tier"], row["country"])


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
    new_salary = _calc_salary(get_league_grade(row["country"], row["grade"]), row["tier"], cur_ovr, row["country"])

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


def _process_promotion_relegation(year, season_avg_rating=6.0):
    conn = get_conn()
    c = conn.cursor()

    p_row = conn.execute("SELECT current_team_id, current_league_id FROM my_player WHERE id=1").fetchone()
    my_team_id   = p_row["current_team_id"]   if p_row else 0
    my_league_id = p_row["current_league_id"] if p_row else 0

    # 현재 시즌 번호
    ss_row = conn.execute("SELECT current_season FROM season_state WHERE id=1").fetchone()
    season = ss_row["current_season"] if ss_row else 1

    # ── 우승/승격 귀속 팀 판정 ──────────────────────
    LEAGUE_END_WEEK = 35

    def _team_at_week35():
        try:
            rows = conn.execute(
                """SELECT team_id, start_week, end_week, end_year FROM career_entries
                   WHERE team_id IS NOT NULL AND team_id<>0
                     AND start_year<=? AND (end_year=0 OR end_year>=?)
                   ORDER BY start_week""",
                (year, year)).fetchall()
            for r in rows:
                sw = r["start_week"] or 0
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

    # (참고용) 그 시즌 5경기 이상 뛴 팀 집합
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

    # [최적화] 모든 국가의 모든 리그 standings를 한 번에 계산해 캐시
    # 이후 루프에서 재계산 없이 조회만 한다.
    # standings 계산: match_results에서 직접 집계 (중첩 함수 제거)
    def _calc_standings_cached(lid):
        """리그 standings를 conn 재사용으로 계산. 경기 없으면 []."""
        teams_in = {r["id"]: {"id": r["id"], "name": r["name"],
                               "pts": 0, "gd": 0, "gf": 0, "gp": 0}
                    for r in c.execute("SELECT id, name FROM teams WHERE league_id=?",
                                       (lid,)).fetchall()}
        if not teams_in:
            return []
        for row in c.execute(
            """SELECT home_team_id, away_team_id, home_score, away_score
               FROM match_results WHERE league_id=? AND season=? AND home_score>=0""",
                (lid, season)).fetchall():
            hid, aid, hs, as_ = (row["home_team_id"], row["away_team_id"],
                                  row["home_score"], row["away_score"])
            for tid, gf, ga in [(hid, hs, as_), (aid, as_, hs)]:
                if tid not in teams_in:
                    continue
                teams_in[tid]["gp"] += 1
                teams_in[tid]["gf"] += gf
                teams_in[tid]["gd"] += gf - ga
                if gf > ga:    teams_in[tid]["pts"] += 3
                elif gf == ga: teams_in[tid]["pts"] += 1
        rows_out = [t for t in teams_in.values() if t["gp"] > 0]
        return sorted(rows_out, key=lambda x: (-x["pts"], -x["gd"], -x["gf"]))

    # [최적화] 전체 리그 맵을 1회 SELECT로 미리 빌드 (기존: cids×tier 개별 SELECT 275회)
    all_leagues_rows = c.execute(
        "SELECT id, country_id, tier FROM leagues").fetchall()
    # {(country_id, tier): league_id}
    _league_map: dict = {(r["country_id"], r["tier"]): r["id"] for r in all_leagues_rows}
    # {country_id: {tier: league_id}}
    cids = list({r["country_id"] for r in all_leagues_rows
                 if r["tier"] == 1})

    # 모든 관련 리그 ID 수집 (맵 조회, DB 추가 접근 없음)
    all_league_ids = {lid for lid in _league_map.values()}

    # standings 캐시: {league_id: [sorted rows]}
    _standings_cache = {lid: _calc_standings_cached(lid) for lid in all_league_ids}

    # [최적화] 팀 이름·리그명 전체 선조회 → 루프 내 ci.execute JOIN 제거
    #   기존: 승강 판정마다 ci = conn.cursor() + JOIN SELECT 2~3회
    #   변경: 1회 SELECT로 {team_id: (name, lname)} dict 빌드 후 dict 조회
    _team_info_cache: dict = {
        r["id"]: (r["name"], r["lname"])
        for r in c.execute(
            "SELECT t.id, t.name, l.name as lname "
            "FROM teams t JOIN leagues l ON t.league_id=l.id"
        ).fetchall()
    }


    pending_logs  = []
    my_new_league = None

    # 1부 리그 우승 기록
    for cid in cids:
        top_lid = _league_map.get((cid, 1))
        if not top_lid:
            continue
        top1_rows = _standings_cache.get(top_lid, [])
        if top1_rows and top1_rows[0]["pts"] > 0 and top1_rows[0]["id"] == champ_team_id:
            champ_tid = top1_rows[0]["id"]
            _ti = _team_info_cache.get(champ_tid)
            winner_info = {"name": _ti[0], "lname": _ti[1]} if _ti else None
            # [최적화] ci 커서 제거 - _team_info_cache 사용
            if winner_info:
                existing_champ = c.execute(
                    "SELECT id FROM trophy_log WHERE year=? AND team_name=? AND tier=1",
                    (year, winner_info["name"])).fetchone()
                if not existing_champ:
                    c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                              (year, winner_info["name"], winner_info["lname"], 1,
                               f"{winner_info['lname']} 우승 (1부 리그 챔피언)"))
                    pending_logs.append((f"🏆 {year}년  {winner_info['name']}  1부 리그 우승!", "event"))

    moved_teams: set = set()
    _rescale_jobs: list = []

    for cid in cids:
        for tier in [1, 2, 3, 4]:
            ntier = tier + 1
            # [최적화] _league_map 조회 (기존: 개별 SELECT 440회 → 0회)
            upper_lid = _league_map.get((cid, tier))
            lower_lid = _league_map.get((cid, ntier))
            if not upper_lid or not lower_lid:
                continue

            # [최적화] 캐시에서 바로 조회 (match_results 재스캔 없음)
            upper_rows = _standings_cache.get(upper_lid, [])
            lower_rows = _standings_cache.get(lower_lid, [])
            if not upper_rows or not lower_rows:
                continue

            bottom_upper = upper_rows[-1]
            top_lower    = lower_rows[0]

            if bottom_upper["id"] in moved_teams or top_lower["id"] in moved_teams:
                continue

            _tl = _team_info_cache.get(top_lower["id"])
            _bu = _team_info_cache.get(bottom_upper["id"])
            tl_info = {"name": _tl[0], "lname": _tl[1]} if _tl else None
            bu_info = {"name": _bu[0], "lname": _bu[1]} if _bu else None
            # [최적화] ci 커서 제거 - _team_info_cache 사용
            if not tl_info or not bu_info:
                continue

            # [버그수정] 리스케일 목표치는 팀 이동 *전* 측정하되
            # 각 팀 본인을 제외한 순수 기존 팀 평균으로 산정.
            # - 승격팀(top_lower) 목표: 상위 리그 기존 팀 평균 (승격팀 제외 불필요 — 아직 안 올라옴)
            # - 강등팀(bottom_upper) 목표: 하위 리그 기존 팀 상위 75% (강등팀 제외 — 아직 안 내려옴)
            # tier 1→4 순서 루프이므로, 이전 tier에서 이동된 팀이
            # 현재 리그 평균에 포함될 수 있어 moved_teams 제외 처리.
            _upper_avg = get_league_avg_ovr(upper_lid, conn)
            _lower_strong = get_league_strong_ovr(lower_lid, 0.75, conn,
                                                   exclude_team_id=bottom_upper["id"])

            # 승격: top_lower → upper
            c.execute("UPDATE teams SET league_id=?,current_tier=? WHERE id=?",
                      (upper_lid, tier, top_lower["id"]))
            if _upper_avg is not None:
                _rescale_jobs.append((top_lower["id"], _upper_avg))
            if _lower_strong is not None:
                _rescale_jobs.append((bottom_upper["id"], _lower_strong))
            c.execute("INSERT INTO promotion_log(year,team_name,from_tier,to_tier,league_name) VALUES(?,?,?,?,?)",
                      (year, tl_info["name"], ntier, tier, tl_info["lname"]))
            tl_is_mine = (top_lower["id"] in my_season_teams)
            if tl_is_mine or my_league_id in (upper_lid, lower_lid):
                pending_logs.append((f"🔼 {year}년  {tl_info['name']}  {ntier}부→{tier}부  (승격)", "event"))
            if top_lower["id"] == champ_team_id:
                exist = c.execute(
                    "SELECT id FROM trophy_log WHERE year=? AND team_name=? AND tier=?",
                    (year, tl_info["name"], ntier)).fetchone()
                if not exist:
                    c.execute("INSERT INTO trophy_log(year,team_name,league_name,tier,competition) VALUES(?,?,?,?,?)",
                              (year, tl_info["name"], tl_info["lname"], ntier,
                               f"{tl_info['lname']} 우승 ({ntier}부 1위 → {tier}부 승격)"))
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
            moved_teams.add(bottom_upper["id"])
            moved_teams.add(top_lower["id"])

        # teams 전적 초기화
        c.execute("""UPDATE teams SET wins=0,draws=0,losses=0,goals_for=0,goals_against=0
                     WHERE league_id IN (SELECT id FROM leagues WHERE country_id=?)""", (cid,))

    # 승강팀 OVR 평형 일괄 적용
    for _tid, _target in _rescale_jobs:
        try:
            _d, _b, _a = rescale_team_to_target_ovr(_tid, _target, conn)
            if _d != 0:
                _ti2 = _team_info_cache.get(_tid)
                _nm = _ti2[0] if _ti2 else f"#{_tid}"
                _dir = "강화" if _d > 0 else "약화"
                pending_logs.append(
                    (f"⚙️ {_nm}  선수단 {_dir}  평균 OVR {_b:.0f}→{_a:.0f} (리그 적응)", "normal"))
        except Exception as _e:
            add_log(f"[리스케일 오류] team {_tid}: {_e}", "normal", year, 52)

    conn.commit()
    conn.close()

    _invalidate_team_ovr_cache()

    if my_new_league:
        p_up = get_player()
        if p_up:
            old_sal = p_up.get("salary", 0)
            conn_t = get_conn()
            new_tier_row = conn_t.execute("SELECT tier FROM leagues WHERE id=?", (my_new_league,)).fetchone()
            old_tier_row = conn_t.execute("SELECT tier FROM leagues WHERE id=?", (my_league_id,)).fetchone()
            conn_t.close()
            new_tier = new_tier_row["tier"] if new_tier_row else 3
            old_tier = old_tier_row["tier"] if old_tier_row else 3
            if new_tier < old_tier:
                if   season_avg_rating >= 7.5: mult = 2.00
                elif season_avg_rating >= 7.0: mult = 1.85
                elif season_avg_rating >= 6.5: mult = 1.65
                else:                          mult = 1.50
                new_sal = int(old_sal * mult)
                _pct = int(round((mult - 1) * 100))
                add_log(f"💰 승격 연봉 인상! {fmt_money(old_sal)} → {fmt_money(new_sal)} "
                        f"(+{_pct}%, 평균평점 {season_avg_rating:.2f})", "event", year, 52)
            elif new_tier > old_tier:
                if   season_avg_rating >= 7.0: cut = 0.30
                elif season_avg_rating >= 6.5: cut = 0.40
                elif season_avg_rating >= 6.0: cut = 0.50
                else:                          cut = 0.60
                new_sal = int(old_sal * (1 - cut))
                _pct = int(round(cut * 100))
                add_log(f"💸 강등 연봉 삭감. {fmt_money(old_sal)} → {fmt_money(new_sal)} "
                        f"(-{_pct}%, 평균평점 {season_avg_rating:.2f})", "event", year, 52)
            else:
                new_sal = old_sal
            update_player(current_league_id=my_new_league,
                          salary=new_sal, current_tier=new_tier)
        else:
            update_player(current_league_id=my_new_league)
        add_log(f"📋 소속 리그가 변경되었습니다", "event", year, 52)

    for text, ltype in pending_logs:
        add_log(text, ltype, year, 52)

def _sim_all_leagues_for_season_end(season: int):
    """시즌 종료 시 내 국가 tier 1~3 리그 일정 생성 + 미완료 경기 시뮬.
    또한 이전 시즌 미완료 경기도 일괄 정리해서 구 시즌 데이터가 남지 않게 함.
    [최적화] 기존 conn0/conn/conn_chk/conn_sim 4개 분리 → 단일 conn 통합.
             get_conn() 4회→1회, commit/close 4회→1회.
    """
    conn = get_conn()
    c    = conn.cursor()

    # 이전 시즌 미완료 경기 전부 AI로 처리 (구 시즌 데이터 오염 방지)
    if season > 1:
        prev = season - 1
        c.execute("""SELECT id, home_team_id, away_team_id FROM match_results
                      WHERE season=? AND home_score=-1""", (prev,))
        stale = c.fetchall()
        if stale:
            batch = []
            for m in stale:
                ho = _team_avg_ovr(c, m["home_team_id"]) + _home_advantage() + _formation_bias(c, m["home_team_id"])
                ao = _team_avg_ovr(c, m["away_team_id"]) + _formation_bias(c, m["away_team_id"])
                diff = ho - ao
                outcome = _roll_outcome(diff)
                hs, as_ = _gen_score(outcome, diff)  # [버그수정] diff 전달
                batch.append((hs, as_, m["id"]))
            c.executemany("UPDATE match_results SET home_score=?,away_score=? WHERE id=?", batch)

    # 내 팀 국가 파악
    p_row = conn.execute(
        "SELECT current_league_id FROM my_player WHERE id=1").fetchone()
    my_lid = p_row["current_league_id"] if p_row else 0

    if not my_lid:
        conn.commit()
        conn.close()
        return

    lg_row = c.execute(
        "SELECT country_id, tier FROM leagues WHERE id=?", (my_lid,)).fetchone()
    if not lg_row:
        conn.commit()
        conn.close()
        return

    cid  = lg_row["country_id"]
    ss   = conn.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()
    year = ss["current_year"] if ss else 2000

    # 내 국가 전체 리그 (tier 1~5, 실제 존재하는 것만)
    c.execute("SELECT id FROM leagues WHERE country_id=?", (cid,))
    league_ids = [r["id"] for r in c.fetchall()]

    if league_ids:
        # [최적화] 리그별 개별 COUNT → 1회 GROUP BY로 대체
        ph = ",".join("?" * len(league_ids))
        sched_counts = {
            r["league_id"]: r["cnt"]
            for r in c.execute(
                f"SELECT league_id, COUNT(*) as cnt FROM match_results "
                f"WHERE league_id IN ({ph}) AND season=? GROUP BY league_id",
                (*league_ids, season)
            ).fetchall()
        }
        need_sched = [lid for lid in league_ids if not sched_counts.get(lid)]
    else:
        need_sched = []

    # 일정 생성이 필요한 리그가 있으면 먼저 commit (generate_season_schedule 내부 commit 방지)
    if need_sched:
        conn.commit()
        for lid in need_sched:
            generate_season_schedule(lid, season, year)

    # 시뮬은 단일 커넥션으로 전 리그 처리
    st = get_state()
    for lid in league_ids:
        _sim_league_full(lid, season, c=c, st=st)
    conn.commit()
    conn.close()



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

    # 자국 country_id + 등급 조회 (force_max_tier 계산에 필요해 먼저 선언)
    my_country_id = None
    my_country_grade = None
    if nationality:
        row_c = c.execute("SELECT id, grade FROM countries WHERE name=?", (nationality,)).fetchone()
        if row_c:
            my_country_id = row_c["id"]
            my_country_grade = row_c["grade"]
    my_join_margin = CLUB_JOIN_MARGIN_BY_GRADE.get(my_country_grade, CLUB_JOIN_MARGIN)

    # 자국 리그 최대 tier (없으면 3 기본값)
    def _country_max_tier(cid):
        if not cid: return 3
        row_mt = c.execute("SELECT MAX(tier) as mt FROM leagues WHERE country_id=?", (cid,)).fetchone()
        return int(row_mt["mt"]) if row_mt and row_mt["mt"] else 3

    my_max_tier = _country_max_tier(my_country_id)
    # 17세 이하 저능력 선수는 가장 하위 리그(max_tier)만 허용
    force_max_tier = (age <= 17 and avg_stat < 50)

    # [최적화] 전체 팀 평균 OVR을 1회 SELECT → dict 캐시 (기존: 루프마다 SELECT 최대 120회)
    _team_avg_cache_offers: dict = {
        r["team_id"]: r["avg_ovr"]
        for r in c.execute(
            "SELECT team_id, AVG(ovr) as avg_ovr FROM ai_players GROUP BY team_id"
        ).fetchall()
        if r["avg_ovr"] is not None
    }

    def _team_fits_me(team_row) -> bool:
        """팀 평균 OVR 대비 내 OVR 차이가 '그 팀 국가 등급별 마진' 이내면 True.
           상위 등급(S/A) 리그일수록 마진이 작아(빡빡) 검증된 선수만 입단 가능."""
        team_avg = _team_avg_cache_offers.get(team_row["id"])
        if team_avg is None:
            return True
        try:
            from constants import get_league_grade as _glg
            grade = _glg(team_row.get("country", ""), team_row["grade"])
        except Exception:
            grade = None
        margin = CLUB_JOIN_MARGIN_BY_GRADE.get(grade, CLUB_JOIN_MARGIN)
        return (team_avg - ovr) <= margin

    first_join = (not has_team and age <= 18)

    offers = []
    tried  = 0

    # [팀입단 확장] 자국/고향/타국 국가풀에서 팀을 채우는 공용 헬퍼.
    #   exclude_ids: 이미 뽑힌 team_id 집합(중복 방지, 호출부에서 관리).
    def _fill_country_pool(country_id, want, exclude_ids, max_tier_cap=None):
        pool = []
        if not country_id or want <= 0:
            return pool
        _max_t = max_tier_cap or _country_max_tier(country_id)
        _tiers = list(range(1, _max_t + 1))
        _weights = tier_weights_by_ovr(ovr)[:_max_t]
        _tr = 0
        while len(pool) < want and _tr < 60:
            _tr += 1
            tier = _max_t if force_max_tier else random.choices(_tiers, _weights)[0]
            c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                cn.name as country,cn.flag,cn.grade
                         FROM teams t
                         JOIN leagues l ON t.league_id=l.id
                         JOIN countries cn ON l.country_id=cn.id
                         WHERE cn.id=? AND l.tier=?
                         ORDER BY RANDOM() LIMIT 1""", (country_id, tier))
            row = c.fetchone()
            if not row: continue
            if row["id"] in exclude_ids or row["id"] == my_tid: continue
            if not _team_fits_me(row): continue
            salary = int(_calc_salary(get_league_grade(row["country"], row["grade"]), tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
            pool.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))
            exclude_ids.add(row["id"])
        return pool

    def _fill_foreign_pool(want, exclude_country_ids, exclude_ids):
        pool = []
        if want <= 0:
            return pool
        _f_tiers   = [1, 2, 3]
        _f_weights = tier_weights_by_ovr(ovr)[:3]
        _tr = 0
        while len(pool) < want and _tr < 80:
            _tr += 1
            _grade_filter = random.choice(grades)
            tier = 3 if force_max_tier else random.choices(_f_tiers, _f_weights)[0]
            _excl = [cid for cid in exclude_country_ids if cid]
            if _excl:
                placeholders = ",".join("?" * len(_excl))
                c.execute(f"""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.grade=? AND l.tier=? AND cn.id NOT IN ({placeholders})
                             ORDER BY RANDOM() LIMIT 1""", tuple([_grade_filter, tier] + _excl))
            else:
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.grade=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 1""", (_grade_filter, tier))
            row = c.fetchone()
            if not row: continue
            if row["id"] in exclude_ids or row["id"] == my_tid: continue
            if not _team_fits_me(row): continue
            salary = int(_calc_salary(get_league_grade(row["country"], row["grade"]), tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
            pool.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))
            exclude_ids.add(row["id"])
        return pool

    def _interleave(*groups):
        """그룹들을 리스트로 받아 행(row) 우선으로 지그재그 배치.
           예: _interleave([d1,d2,d3],[h1,h2,h3],[f1,f2,f3,f4])
               -> [d1,h1,d2,h2,d3,h3,f1,f2,f3,f4]
           (마지막 그룹은 남는 슬롯 전체를 그대로 뒤에 이어붙임 = 하단 풀행)"""
        result = []
        head_groups = groups[:-1]
        tail_group  = groups[-1] if groups else []
        max_len = max((len(g) for g in head_groups), default=0)
        for i in range(max_len):
            for g in head_groups:
                if i < len(g):
                    result.append(g[i])
        result.extend(tail_group)
        return result

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
            return (r["min_avg"] - ovr) <= my_join_margin

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
                # 팀 평균 OVR - 내 OVR <= CLUB_JOIN_MARGIN 인 팀들 중 무작위 1팀.
                #   (_tier_fittable 와 동일 기준으로, 판정-선택 불일치를 없앤다)
                c.execute(f"""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             JOIN (SELECT team_id, AVG(ovr) AS avg_ovr
                                     FROM ai_players GROUP BY team_id) ta
                                   ON ta.team_id=t.id
                             WHERE cn.id=? AND l.tier=? AND (ta.avg_ovr - ?) <= {int(my_join_margin)}
                             ORDER BY RANDOM() LIMIT 1""", (my_country_id, tier, ovr))
                row = c.fetchone()
                if not row: return False
            if any(o["team_id"] == row["id"] for o in offers): return False
            if row["id"] == my_tid: return False
            salary = int(_calc_salary(get_league_grade(row["country"], row["grade"]), tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
            offers.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))
            return True

        # [1단계] 내 수준으로 가능한 자국 티어 확정.
        #   force_max_tier(저능력 17세)이면 무조건 최하위 부만.
        if force_max_tier:
            fittable = [my_max_tier] if _tier_fittable(my_max_tier) else []
        else:
            fittable = [t for t in range(1, my_max_tier + 1) if _tier_fittable(t)]

        # [2단계] 가능 티어들 중에서 1부10/2부30/3부60 비중으로 뽑아 슬롯 채움.
        TIER_W = {1: 5, 2: 20, 3: 40, 4: 25, 5: 10}
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
                    if _try_domestic(my_max_tier, relax=True):
                        placed = True; break
            # 자국에 3부 리그 자체가 없으면 더는 강제하지 않음

        # 자국 팀 중 내 수준에 맞는 것만 우선
        # [팀입단 확장] count(보통 10)의 절반은 자국, 절반은 타국으로 고정 분할.
        domestic_count = count // 2
        _dom_tiers = list(range(1, my_max_tier + 1))
        _dom_weights = tier_weights_by_ovr(ovr)[:my_max_tier]
        while len(offers) < domestic_count and tried < 80:
            tried += 1
            _grade_filter = random.choice(grades)   # DB 쿼리 필터용
            tier  = my_max_tier if force_max_tier else random.choices(_dom_tiers, _dom_weights)[0]
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
            if not _team_fits_me(row): continue
            salary = int(_calc_salary(get_league_grade(row["country"], row["grade"]), tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
            offers.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))

        # 자국에서 못 채웠거나 해외 슬롯이 남은 경우 → 타국으로 채움
        if len(offers) < count:
            tried2 = 0
            while len(offers) < count and tried2 < 60:
                tried2 += 1
                _grade_filter = random.choice(grades)   # DB 쿼리 필터용
                _foreign_max = _country_max_tier(None)
                _f_tiers = list(range(1, _foreign_max + 1))
                _f_weights = tier_weights_by_ovr(ovr)[:_foreign_max]
                tier  = _foreign_max if force_max_tier else random.choices(_f_tiers, _f_weights)[0]
                c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                    cn.name as country,cn.flag,cn.grade
                             FROM teams t
                             JOIN leagues l ON t.league_id=l.id
                             JOIN countries cn ON l.country_id=cn.id
                             WHERE cn.id!=? AND cn.grade=? AND l.tier=?
                             ORDER BY RANDOM() LIMIT 1""", (my_country_id, _grade_filter, tier))
                row = c.fetchone()
                if not row: continue
                if any(o["team_id"] == row["id"] for o in offers): continue
                if row["id"] == my_tid: continue
                if not _team_fits_me(row): continue
                salary = int(_calc_salary(get_league_grade(row["country"], row["grade"]), tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
                offers.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))

        # [그리드 배치] 자국(좌열) / 타국(우열)이 매 행마다 번갈아 오도록 재정렬 + 구역 태그
        _dom_group = offers[:domestic_count]
        _for_group = offers[domestic_count:]
        for o in _dom_group: o["_zone"] = "domestic"
        for o in _for_group: o["_zone"] = "foreign"
        offers = _interleave(_dom_group, _for_group, [])

    elif not has_team:
        # ── [팀입단 확장] 17세 이후 계약종료/방출 등으로 소속이 사라져
        #    '오퍼'가 아닌 '팀 입단'으로 새 팀을 찾는 경우.
        #    10개 = 좌측(직전 소속 리그 국가) 3 + 우측(고향=출생국적) 3 + 하단(타국) 4.
        #    ※ 좌측은 대표국적(nationality)이 아니라 '직전까지 뛰던 리그의 국가'다.
        #      예) 출생 모리타니, 바하마 1부에서 뛰다 계약종료 → 좌측=바하마, 우측=모리타니.
        prev_country_id = None
        row_prev = c.execute(
            """SELECT team_id FROM career_entries
               WHERE end_year>0 AND team_id>0
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if row_prev and row_prev["team_id"]:
            row_pc = c.execute(
                """SELECT l.country_id FROM teams t
                   JOIN leagues l ON t.league_id=l.id
                   WHERE t.id=?""", (row_prev["team_id"],)
            ).fetchone()
            if row_pc:
                prev_country_id = row_pc["country_id"]
        if not prev_country_id:
            # 직전 소속 팀 기록이 없으면(경력 자체가 없는 예외 케이스) 대표국적으로 대체
            prev_country_id = my_country_id

        _origin_nat = p.get("origin_nat") or nationality
        origin_country_id = my_country_id
        if _origin_nat and _origin_nat != nationality:
            row_o = c.execute("SELECT id FROM countries WHERE name=?", (_origin_nat,)).fetchone()
            if row_o:
                origin_country_id = row_o["id"]

        _seen_ids = {o["team_id"] for o in offers}
        _dom_group  = _fill_country_pool(prev_country_id, 3, _seen_ids)
        _home_group = _fill_country_pool(origin_country_id, 3, _seen_ids)
        _exclude_countries = {cid for cid in (prev_country_id, origin_country_id) if cid}
        _for_group  = _fill_foreign_pool(count - len(_dom_group) - len(_home_group),
                                          _exclude_countries, _seen_ids)

        for o in _dom_group:  o["_zone"] = "prev_league"
        for o in _home_group: o["_zone"] = "hometown"
        for o in _for_group:  o["_zone"] = "foreign"
        offers = _interleave(_dom_group, _home_group, _for_group)
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
            # 현재 소속 리그 국가의 최대 tier
            _home_max_tier = _country_max_tier(league_country_id)
            # 티어 가중치: OVR 기반(성장 시 상위 리그로 이동) + 현재 티어를 약간 가산
            tier_weights = list(tier_weights_by_ovr(ovr))[:_home_max_tier]
            _cur_idx = min(my_current_tier - 1, len(tier_weights) - 1)
            tier_weights[_cur_idx] = tier_weights[_cur_idx] + 15
            _home_tiers = list(range(1, _home_max_tier + 1))

            home_league_count = random.choices([1, 2], weights=[40, 60])[0]  # 1개 or 2개
            tried_home = 0
            while len([o for o in offers if o.get("_home_league")]) < home_league_count and tried_home < 50:
                tried_home += 1
                tier = random.choices(_home_tiers, tier_weights)[0]
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
                salary = int(_calc_salary(get_league_grade(row["country"], row["grade"]), tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
                offer = _build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary)
                offer["_home_league"] = True  # 정렬용 플래그
                offers.append(offer)

        # 35세 이상이면 자국 팀 1개 추가 (소속 리그 국가와 다를 때만)
        if age >= 35 and my_country_id and my_country_id != league_country_id:
            _ret_tiers = list(range(1, my_max_tier + 1))
            _ret_weights = tier_weights_by_ovr(ovr)[:my_max_tier]
            tried_home = 0
            while tried_home < 30:
                tried_home += 1
                tier = my_max_tier if force_max_tier else random.choices(_ret_tiers, _ret_weights)[0]
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
                salary = int(_calc_salary(get_league_grade(row["country"], row["grade"]), tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
                offers.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))
                break

        while len(offers) < count and tried < 120:
            tried += 1
            _grade_filter = random.choice(grades)   # DB 쿼리 필터용
            _g_max = 3
            _g_tws = tier_weights_by_ovr(ovr)[:_g_max]
            tier  = _g_max if force_max_tier else random.choices(list(range(1, _g_max + 1)), _g_tws)[0]
            c.execute("""SELECT t.id,t.name,l.id as lid,l.name as lname,l.tier,
                                cn.name as country,cn.flag,cn.grade
                         FROM teams t
                         JOIN leagues l ON t.league_id=l.id
                         JOIN countries cn ON l.country_id=cn.id
                         WHERE cn.grade=? AND l.tier=?
                         ORDER BY RANDOM() LIMIT 1""", (_grade_filter, tier))
            row = c.fetchone()
            if not row: continue
            if any(o["team_id"] == row["id"] for o in offers): continue
            if row["id"] == my_tid: continue
            if not _team_fits_me(row): continue
            salary = int(_calc_salary(get_league_grade(row["country"], row["grade"]), tier, ovr, row["country"]) * random.uniform(0.85, 1.15))
            offers.append(_build_offer(row, get_league_grade(row["country"], row["grade"]), tier, salary))

    # _home_league 플래그 있는 오퍼를 맨 앞으로 정렬
    offers.sort(key=lambda o: 0 if o.get("_home_league") else 1)
    # 플래그 제거 (UI에 노출 불필요)
    for o in offers:
        o.pop("_home_league", None)

    # [17세 첫 입단 안전망] 협상을 모두 실패해도 입단할 곳이 사라지지 않도록,
    #   생성된 오퍼 중 연봉(=등급·티어와 직결)이 가장 낮은 1곳은 항상 입단 가능하게 표시.
    #   협상 자체는 그대로 가능(성공 시 연봉 인상), 실패해도 '결렬'로 막히지 않을 뿐.
    if not has_team and offers:
        safe_offer = min(offers, key=lambda o: o["salary"])
        safe_offer["safe"] = True

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

    # [최적화] season_state를 1회 조회 후 _get_team_rank_info에 주입 (기존: 오퍼마다 SELECT)
    _ss_for_rank = conn.execute(
        "SELECT current_week, current_season FROM season_state WHERE id=1").fetchone()
    for offer in offers:
        offer["rank_info"] = _get_team_rank_info(conn.cursor(), offer["team_id"],
                                                  ss=_ss_for_rank)

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

    cur_year = p.get("current_year", GAME_START_YEAR)
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


def _get_team_rank_info(c, team_id, ss=None) -> str:
    """이적 오퍼 카드용 순위/성적 문자열.

    현재 주차에 따라 집계 범위 결정:
    - 13~25주차 비시즌: 상반기(5~11주차) 결과만 → "상반기 성적"
    - 37~52주차 / 새 시즌 1~4주차: 시즌 전체(5~32주차) 결과 → "작년 성적"
    승강전 팀이면 이전 리그도 표시.
    ss: season_state 행을 외부에서 주입 가능 (없으면 자체 조회).
    """
    c.execute("SELECT league_id, name FROM teams WHERE id=?", (team_id,))
    t = c.fetchone()
    if not t:
        return ""
    league_id = t["league_id"]
    team_name = t["name"]

    # [최적화] ss가 외부에서 주입되면 DB 재조회 생략
    if ss is None:
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
    order = ["F","E","D","C","B","A","S","SS"]
    if ovr >= 90: base = ["SS","S"]         # EPL/빅리그 정점
    elif ovr >= 85: base = ["S","A"]
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



def _salary_ovr_mult(ovr: int) -> float:
    """OVR → 연봉 배수. 4구간 piecewise.

    구간별 특성:
      OVR40~79: 완만한 상승 (0.08 → 2.00)
      OVR80~89: 가파른 가속 (2.00 → 16.00, 에이스 프리미엄)
      OVR90~92: 완충 (16.00 → 40.00)
      OVR93~99: 급격 (40.00 → 141.60, 월드클래스)

    SS 1부(base 19,996,093천원) 기준:
      OVR82 → 45억/년 (평균)
      OVR87 → 155억/년
      OVR90 → 320억/년
      OVR93 → 800억/년
      OVR99 → 2831억/년 (CAP 3000억)
    """
    if ovr < 80:
        t = max(0.0, (ovr - 40) / 40.0)
        return 0.08 + t ** 2.2 * 1.92        # 40→79: 0.08 → 2.00
    elif ovr < 90:
        t = (ovr - 80) / 10.0
        return 2.0 + t ** 2.5 * 14.0         # 80→89: 2.00 → 16.00
    elif ovr < 93:
        t = (ovr - 90) / 3.0
        return 16.0 + t ** 1.5 * 24.0        # 90→92: 16.00 → 40.00
    else:
        t = (ovr - 93) / 6.0
        return 40.0 + t ** 1.2 * 101.6       # 93→99: 40.00 → 141.60


def _salary_ovr_adj(ovr: int, grade: str, tier: int) -> float:
    """하위 호환 래퍼 — _salary_ovr_mult 위임."""
    return _salary_ovr_mult(ovr)

def _calc_salary(grade, tier, ovr, country=None):
    """연봉 계산 (천원 단위).
    wealth 결정 우선순위:
      1) SPECIAL_SALARY_COUNTRIES — 특수 연봉 국가 (사우디/카타르/UAE)
      2) COUNTRY_LEAGUE_GRADE    — 리그 전용 등급 (국대 등급과 분리)
      3) grade 파라미터           — fallback
    나라별 연봉 배율(COUNTRY_SALARY_MULT) 추가 적용:
      같은 등급 내에서도 나라마다 재정 수준이 달라 연봉 차이 반영.
      단, SPECIAL_SALARY_COUNTRIES(사우디 등)는 배율 적용 제외.
    """
    from constants import (LOWER_LEAGUE_SALARY_OVERRIDE, SPECIAL_SALARY_COUNTRIES,
                           get_league_grade, SALARY_CURVE_OVERRIDE, salary_curve_value,
                           COUNTRY_SALARY_CAP)

    # [양극화 리그 특례] tier1 + 앵커커브 적용국은 base_year/mult 대신
    # (하위권 OVR→하위권 연봉)~(월드클래스 OVR→최고연봉) 지수보간 곡선을 그대로 사용.
    # 국대등급(grade)과 무관하게 국가명 자체로 판정하므로 SPECIAL 여부와도 독립적.
    if tier == 1 and country in SALARY_CURVE_OVERRIDE:
        sal = salary_curve_value(country, ovr)
        cap = COUNTRY_SALARY_CAP.get(country, 0)
        if cap > 0:
            sal = min(sal, cap)
        return max(0, sal)

    is_special = country and country in SPECIAL_SALARY_COUNTRIES
    if country:
        if is_special:
            wealth = SPECIAL_SALARY_COUNTRIES[country]
        else:
            wealth = get_league_grade(country, grade)
    else:
        wealth = grade


    base_year = {
        # 천원/년. SS 1부 기준, 각 등급 OVR65 평균 주전 목표 연봉으로 역산된 base.
        # 나라별 실제 연봉은 COUNTRY_SALARY_MULT로 조정.
        # tier 비율: 1부=1.0 / 2부≈0.316 / 3부 이하는 LOWER_LEAGUE_OVERRIDE로 관리.
        "SS":{1:19_996_093, 2:6_318_572, 3:2_025_506, 4:1_650_731, 5:1_324_913},
        "S": {1:11_603_489, 2:3_666_703, 3:1_180_000, 4:   77_216, 5:   61_990},
        "A": {1: 2_977_645, 2:  941_397, 3:   45_853, 4:   26_498},
        "B": {1: 1_020_507, 2:  322_480, 3:   19_445, 4:    9_266},
        "C": {1:   562_640, 2:  177_794, 3:    9_936, 4:    6_573},
        "D": {1:    15_425, 2:    4_874, 3:    3_122},
        "E": {1:     6_076, 2:    1_920, 3:    1_234},
        "F": {1:     5_560, 2:    1_757, 3:      605},
    }
    # 등급별 연봉 상한 (천원/년) — 나라별 COUNTRY_SALARY_CAP이 실제 상한 역할.
    # 이 값은 COUNTRY_SALARY_CAP 없는 나라의 최종 안전망.
    _salary_cap = {
        "SS": 50_000_000,   # 500억 안전망
        "S":  20_000_000,   # 200억
        "A":   5_000_000,   # 50억
        "B":   1_000_000,   # 10억
        "C":     300_000,   # 3억
        "D":      50_000,   # 5천만
        "E":      20_000,   # 2천만
        "F":      10_000,   # 1천만
    }
    b = base_year.get(wealth, {}).get(tier, 100)

    # 나라×tier 오버라이드 (3부 이하)
    # [버그수정] LOWER_LEAGUE_SALARY_OVERRIDE는 이미 나라별 절대 base값이므로
    #   override 사용 시 cont_mult를 적용하지 않는다.
    #   (기존: override에도 cont_mult 재적용 → K3 의도 150만이 31만으로 축소되는 버그)
    _used_override = False
    if country and tier >= 3:
        _ov = LOWER_LEAGUE_SALARY_OVERRIDE.get(country, {})\
            if not is_special else {}
        if tier in _ov:
            b = _ov[tier]
            _used_override = True

    if b == 0:
        return 0

    # 나라별 연봉 배율: override를 사용하지 않은 경우에만 적용
    # (override는 이미 나라별 절대값 — cont_mult 중복 적용 방지)
    if not is_special and country and not _used_override:
        from constants import COUNTRY_SALARY_MULT
        cont_mult = COUNTRY_SALARY_MULT.get(country, 1.0)
        b = int(b * cont_mult)

    if b == 0:
        return 0

    sal = int(b * _salary_ovr_adj(ovr, wealth, tier))
    if wealth == "F" and tier >= 3 and ovr < 38:
        return 0
    if tier >= 4 and sal < 50 and b > 0:
        sal = 50
    # 등급별 연봉 상한 적용
    cap = _salary_cap.get(wealth, 0)
    if cap > 0:
        sal = min(sal, cap)
    # [버그수정] 나라별 연봉 상한 적용 (COUNTRY_SALARY_CAP)
    #   constants.py에 정의돼 있었으나 _calc_salary에서 import/적용이 누락됐었음.
    if country and not is_special:
        from constants import COUNTRY_SALARY_CAP
        country_cap = COUNTRY_SALARY_CAP.get(country, 0)
        if country_cap > 0:
            sal = min(sal, country_cap)
    # [버그수정] 양극화 리그(SALARY_CURVE_OVERRIDE 적용국)는 tier1만 재계산돼서
    #   COUNTRY_SALARY_CAP이 tier1 기준 안전망(예: 잉글랜드 550억)으로 상향됐다.
    #   그 캡이 2부 이하에도 그대로 적용되면 "1부보다 2부가 더 비싼" 역전이
    #   생기므로, tier>=2는 별도의 낮은 캡(LOWER_TIER_SALARY_CAP)으로 다시 누른다.
    if country and tier >= 2:
        from constants import LOWER_TIER_SALARY_CAP
        lt_cap = LOWER_TIER_SALARY_CAP.get(country, 0)
        if lt_cap > 0:
            sal = min(sal, lt_cap)
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
    pos = get_field_pos(p)   # 배치 포지션 (포메이션 슬롯 기반, 없으면 주요 포지션)
    cs  = _calc_clean_sheets(c, tid, season, matches=sm)

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
    c.execute("""SELECT t.name,t.formation,l.id as lid,l.name as lname,l.tier
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
        #   (버그수정) 기존엔 기본 스탯만 리셋해 season_blocks/pass_acc/key_passes/
        #   dribbles/shots 등 '세부 통계'가 이전 팀에서 그대로 이월됐다.
        #   → 0출전 신규 팀인데 차단 31·패스 83% 가 찍히는 원인. 시즌말 리셋과
        #     동일한 필드 전체를 함께 0으로 초기화한다.
        update_player(season_matches=0, season_goals=0, season_assists=0,
                      season_saves=0, season_rating_sum=0.0, season_rating_cnt=0,
                      season_goals_against=0,
                      season_shots=0, season_shots_on=0, season_key_passes=0,
                      season_dribbles=0, season_blocks=0,
                      season_pass_acc_sum=0, season_pass_acc_cnt=0)
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

    # 새 팀 포메이션 기반으로 field_pos 즉시 결정 → career_entries에 올바른 포지션 저장
    try:
        from constants import POSITION_COMPAT, FORMATION_SLOTS
        _formation = row.get("formation", "4-4-2") or "4-4-2"
        _slots = FORMATION_SLOTS.get(_formation, FORMATION_SLOTS["4-4-2"])
        _primary = p.get("position", "CM") if p else "CM"
        _compat = POSITION_COMPAT.get(_primary, [_primary])
        _best_pos, _best_rank = _primary, 0
        _best_found = 999
        for _slot in _slots:
            if _slot in _compat:
                _rank = _compat.index(_slot)
                if _rank < _best_found:
                    _best_found = _rank
                    _best_pos = _slot
                    _best_rank = _rank
        _field_pos = _best_pos
        _mismatch_rank = _best_rank
    except Exception:
        _field_pos = p.get("position", "CM") if p else "CM"
        _mismatch_rank = 0

    update_player(current_team_id=team_id, current_league_id=row["lid"],
                  salary=salary, manager_relation=rel_init,
                  contract_years=c_yrs, contract_end_year=c_end,
                  current_tier=row["tier"],
                  contract_role=role, club_ambition=ambition,
                  manager_type=mgr_type,
                  appearance_bonus_k=app_bonus, goal_bonus_k=goal_bonus,
                  transfer_requested=0,
                  field_pos=_field_pos, mismatch_rank=_mismatch_rank)
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
    cur_year = st["current_year"] if st else p.get("current_year", GAME_START_YEAR)

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

    if stats:
        c.executemany("""UPDATE teams SET wins=?,draws=?,losses=?,
                     goals_for=?,goals_against=? WHERE id=?""",
                  [(s["w"], s["d"], s["l"], s["gf"], s["ga"], tid)
                   for tid, s in stats.items()])

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

    new_rows = []
    for rd, matches in enumerate(ROUND_MATCHES):
        week = FIRST_HALF_START + rd
        for hi, ai in matches:
            if hi >= len(tids) or ai >= len(tids): continue
            t1, t2 = (tids[hi], tids[ai]) if random.random() < 0.5 else (tids[ai], tids[hi])
            key  = (week, t1, t2)
            rkey = (week, t2, t1)
            if key in existing or rkey in existing: continue
            new_rows.append((league_id, week, t1, t2, season, year))
            existing.add(key)
            existing.add(rkey)

    if new_rows:
        c.executemany("""INSERT INTO match_results
                         (league_id,week,home_team_id,away_team_id,
                          home_score,away_score,season,year)
                         VALUES(?,?,?,?,-1,-1,?,?)""", new_rows)

    conn.commit()
    conn.close()


def _sim_league_full(league_id, season, c=None, st=None):
    """오퍼 창용: 해당 리그의 현재 주차까지 미완료 경기만 AI 시뮬.
    match_results에만 결과 저장, teams 테이블은 건드리지 않음.
    (순위는 _get_team_rank_info에서 match_results 기준으로 계산)
    과거 시즌이면 전체 주차를 시뮬 (1~4주차 '작년 성적' 계산용 ─ 버그 수정)

    c:  외부에서 연 커서를 재사용 (여러 리그를 한 커넥션으로 처리할 때).
        None이면 자체 커넥션을 열고 닫는다(기존 동작 = 하위 호환).
    st: get_state() 결과 재주입 (루프에서 매번 조회 방지). None이면 직접 조회.
    """
    if st is None:
        st = get_state()
    cur_week   = st["current_week"]   if st else 11
    cur_season = st["current_season"] if st else 1
    week_cap = 99 if season < cur_season else cur_week

    _own_conn = c is None
    if _own_conn:
        conn = get_conn()
        c = conn.cursor()
    c.execute("""SELECT id, home_team_id, away_team_id
                 FROM match_results
                 WHERE league_id=? AND season=? AND home_score=-1 AND week<=?""",
              (league_id, season, week_cap))
    matches = c.fetchall()

    batch_r = []
    for m in matches:
        hid = m["home_team_id"]
        aid = m["away_team_id"]
        ho = _team_avg_ovr(c, hid) + _home_advantage() + _formation_bias(c, hid)
        ao = _team_avg_ovr(c, aid) + _formation_bias(c, aid)
        diff = ho - ao
        outcome = _roll_outcome(diff)
        hs, as_ = _gen_score(outcome, diff)  # [버그수정] diff 전달
        # teams 테이블 업데이트 없이 match_results에만 저장 (배치 처리)
        batch_r.append((hs, as_, m["id"]))

    if batch_r:
        c.executemany("UPDATE match_results SET home_score=?,away_score=? WHERE id=?", batch_r)

    if _own_conn:
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

    team_deltas  = {}
    batch_results = []
    for m in matches:
        hid = m["home_team_id"]
        aid = m["away_team_id"]
        # 내 팀이 포함된 과거 경기도 랜덤으로 처리 (입단 전이니 AI끼리 뛴 것)
        ho = _team_avg_ovr(c, hid) + _home_advantage() + _formation_bias(c, hid)
        ao = _team_avg_ovr(c, aid) + _formation_bias(c, aid)
        diff = ho - ao
        outcome = _roll_outcome(diff)
        hs, as_ = _gen_score(outcome, diff)  # [버그수정] diff 전달
        _accum_team_rec(team_deltas, hid, aid, outcome, hs, as_)
        batch_results.append((hs, as_, m["id"]))

    if batch_results:
        c.executemany("UPDATE match_results SET home_score=?,away_score=? WHERE id=?",
                      batch_results)
    _flush_team_rec(c, team_deltas)

    conn.commit()
    conn.close()

    if matches:
        add_log(f"📋 이적 전 {len(matches)}경기 결과 일괄 처리 완료", "event")