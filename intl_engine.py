"""
intl_engine.py ─ 국제대회(월드컵/대륙컵) 엔진

시즌 중 17~24주 국제대회 윈도우에서 실제 경기 단위로 진행한다.
  17주차: 예선 결과 발표 + 조 추첨 + 국가대표 소집
  18~20주차: 조별리그 3경기
  월드컵:  16강(21) → 8강(22) → 4강(23) → 결승(24)
  대륙컵:  8강(21) → 4강(22) → 결승(23)

본선 진출국은 피파 랭킹 줄세우기가 아니라
'등급 기본 점수 + 랜덤 노이즈' 예선 점수로 대륙별 쿼터만큼 선발
→ 강호도 가끔 예선 탈락, 약체도 가끔 깜짝 진출.
"""

import random

from database import get_conn
from constants import (
    WC_START_YEAR, WC_INTERVAL,
    CONTINENTAL_START_YEAR, CONTINENTAL_INTERVAL,
    INTL_CALLUP_WEEK, INTL_GROUP_WEEKS, INTL_KO_WEEKS,
    WC_TEAMS, WC_GROUPS, WC_QUOTA,
    WC_EXPAND_YEAR, WC_TEAMS_BIG, WC_GROUPS_BIG, WC_QUOTA_BIG,
    CONT_TEAMS, CONT_GROUPS, CONT_BEST_THIRDS,
    CONFEDERATIONS, CONF_CUP_NAME,
    GRADE_TEAM_OVR, GRADE_QUAL_BASE, QUAL_NOISE,
    INTL_SELECTION_OVR, INTL_MAX_TIER, INTL_MIN_MATCHES,
    INTL_SELECTION_MARGIN,
)

STAGE_KO = {"group": "조별리그", "R32": "32강", "R16": "16강", "QF": "8강", "SF": "4강", "F": "결승"}

# ── entry 캐시 ─────────────────────────────────────
# intl_entries(ovr/flag/grade)는 대회 진행 중 불변 → (tid, country)별 1회 조회.
_entry_cache = {}

def _clear_entry_cache():
    _entry_cache.clear()

# 그룹 라벨
_GROUP_LABELS = ["A", "B", "C", "D", "E", "F", "G", "H",
                 "I", "J", "K", "L", "M", "N", "O", "P"]

# 조별리그 라운드 매칭 (4팀, 인덱스)
_GROUP_ROUNDS = [
    [(0, 1), (2, 3)],
    [(0, 2), (1, 3)],
    [(0, 3), (1, 2)],
]


# ─────────────────────────────────────────────
# 조회 헬퍼
# ─────────────────────────────────────────────

def get_tournament(year):
    """해당 연도의 국제대회 row (없으면 None)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM intl_tournaments WHERE year=? ORDER BY id DESC LIMIT 1",
        (year,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_pending_choice():
    """[복수국적] 대표팀 선택/동의가 필요한 대회가 있으면 정보 반환, 없으면 None.
    (미고정 + 본선 진출국 1개 이상 → my_selected==3)
    나이와 무관하게, 발탁창에서 본인이 골라야(또는 동의해야) 한다.
    진출국이 1개여도 본인 동의를 받기 위해 팝업을 띄운다.
    선택해서 본선에 출전하면 그 나라로 영구 고정(cap-tie)된다."""
    from game_engine import get_state, get_player
    st = get_state(); p = get_player()
    if not st or not p:
        return None
    t = get_tournament(st["current_year"])
    if not t or t.get("my_selected") != 3:
        return None
    # 22세 이상은 정상적으로는 1~4주차 강제선택(get_forced_commit)으로 이미
    # 고정되어 있어야 한다. 그 팝업을 건너뛴 미고정 선수는 여기서도 선택 가능.

    nats = [p.get("nationality", "") or "",
            p.get("nationality2", "") or "",
            p.get("nationality3", "") or ""]
    conn = get_conn()
    entry_names = {r["country"] for r in conn.execute(
        "SELECT country FROM intl_entries WHERE tournament_id=?", (t["id"],)).fetchall()}
    opts = []
    seen = set()
    for n in nats:
        if n and n in entry_names and n not in seen:
            seen.add(n)
            fr = conn.execute("SELECT flag FROM intl_entries WHERE tournament_id=? AND country=?",
                              (t["id"], n)).fetchone()
            opts.append({"nat": n, "flag": fr["flag"] if fr else ""})
    conn.close()
    if len(opts) < 1:
        return None
    return {"tournament_id": t["id"], "name": t["name"],
            "year": t["year"], "options": opts}


def choose_national_team(tournament_id, nat):
    """[복수국적] 대표팀 선택 확정 → 그 나라로 고정하고 대회 출전국 설정.
    선발 판정을 다시 수행해 my_selected를 1(선발)/0(미선발)로 갱신."""
    from game_engine import get_player, update_player
    p = get_player()
    if not p:
        return None
    conn = get_conn()
    grow = conn.execute("SELECT grade FROM countries WHERE name=?", (nat,)).fetchone()
    grade = grow["grade"] if grow else "F"
    conn.close()

    # 이 선택으로 영구 고정 (이후 모든 대회에서 이 나라로만)
    update_player(intl_committed=nat)

    p = get_player()
    selected = _check_selection(p, grade)
    my_sel = 1 if selected else 0
    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET my_nat=?, my_selected=? WHERE id=?",
                 (nat, my_sel, tournament_id))
    # 미선발이면 대회 자동판정 경로(start_intl_tournament)와 동일하게
    # '국가대표 미선발'을 trophy_log에 남긴다 (선택대기→동의→미선발 빈틈 보정).
    if not selected:
        trow = conn.execute("SELECT year, name FROM intl_tournaments WHERE id=?",
                            (tournament_id,)).fetchone()
        conn.commit(); conn.close()
        if trow:
            _save_trophy(trow["year"], nat, trow["name"], "국가대표 미선발")
    else:
        conn.commit(); conn.close()
    return {"nat": nat, "selected": selected}


def decline_national_team(tournament_id):
    """[복수국적] 이번 대회 대표팀 발탁을 거절(보류). 영구 고정하지 않으며,
    이번 대회만 출전 없음(my_selected=2) 처리한다. 다음 대회에서 다시 제안된다."""
    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET my_nat='', my_selected=2 WHERE id=?",
                 (tournament_id,))
    conn.commit(); conn.close()
    return True


def get_forced_commit():
    """[복수국적] 22세 1~4주차(비시즌)에 아직 대표팀을 고정하지 않았다면,
    강제로 국적을 정하게 하는 정보 반환. 없으면 None.

    - 조건: 미고정(intl_committed=='') + 나이 == 22 + 현재 주차 1~4
    - 본선 진출 여부와 무관하게 '보유 국적 전부'를 선택지로 제공한다.
      (대회에 안 나가도 평생 뛸 대표팀을 이 시점에 확정)
    - 이 선택으로 intl_committed만 설정되며, 보유 국적(nationality/2/3)은
      그대로 유지된다(국적이 사라지지 않음).
    """
    from game_engine import get_state, get_player
    st = get_state(); p = get_player()
    if not st or not p:
        return None
    if (p.get("intl_committed", "") or ""):
        return None   # 이미 고정됨
    week = st.get("current_week", 0)
    if not (1 <= week <= 4):
        return None
    year = st.get("current_year", 0)
    age  = year - (p.get("birth_year", year - 17) or (year - 17))
    if age != 22:
        return None

    # 보유 국적 전부 (본선 진출 무관)
    pairs = [
        (p.get("nationality", "")  or "", p.get("flag", "")  or ""),
        (p.get("nationality2", "") or "", p.get("flag2", "") or ""),
        (p.get("nationality3", "") or "", p.get("flag3", "") or ""),
    ]
    opts = []
    seen = set()
    for nat, flag in pairs:
        if nat and nat not in seen:
            seen.add(nat)
            opts.append({"nat": nat, "flag": flag})
    if not opts:
        return None
    return {"forced": True, "year": year, "options": opts}


def commit_nationality(nat):
    """[복수국적] 22세 강제 선택 확정 → intl_committed만 그 나라로 설정.
    보유 국적(nationality/2/3)은 건드리지 않아 사라지지 않는다."""
    from game_engine import get_player, update_player
    p = get_player()
    if not p:
        return None
    update_player(intl_committed=nat)
    return {"nat": nat}


def fmt_nationalities(p):
    """[복수국적] 보유 국적 전부를 '국기+이름' 문자열로 (예: '🇦🇹오스트리아 / 🇵🇦파나마')."""
    if not p:
        return ""
    pairs = [
        (p.get("nationality", "")  or "", p.get("flag", "")  or ""),
        (p.get("nationality2", "") or "", p.get("flag2", "") or ""),
        (p.get("nationality3", "") or "", p.get("flag3", "") or ""),
    ]
    seen = set(); out = []
    for nat, flag in pairs:
        if nat and nat not in seen:
            seen.add(nat)
            out.append(f"{flag}{nat}")
    return " / ".join(out)


def fmt_rep_nationality(p):
    """[복수국적] 축구 대표로 뛰는 국적(국기 포함). 미고정이면 '미정'."""
    if not p:
        return "미정"
    rep = p.get("intl_committed", "") or ""
    if not rep:
        return "미정"
    # 국기 찾기
    for nat_key, flag_key in (("nationality", "flag"),
                              ("nationality2", "flag2"),
                              ("nationality3", "flag3")):
        if (p.get(nat_key, "") or "") == rep:
            return f"{p.get(flag_key, '') or ''}{rep}"
    return rep


def _my_nat(t, p):
    """[복수국적] 이 대회에서 내가 뛰는 나라.
    대회에 저장된 my_nat 우선, 없으면(구 세이브) 주 국적으로 폴백."""
    if t:
        mn = t.get("my_nat") if isinstance(t, dict) else t["my_nat"]
        if mn:
            return mn
    return (p.get("nationality", "") if p else "") or ""


def _active_tournament():
    from game_engine import get_state
    st = get_state()
    if not st:
        return None
    t = get_tournament(st["current_year"])
    if t and t["status"] != "done":
        return t
    return t  # done이어도 반환 (UI 표시용) ─ 호출부에서 status 체크


def get_my_match(week):
    """이번 주차에 내가 뛸 국가대표 경기가 있으면 dict, 없으면 None."""
    from game_engine import get_player, get_state
    p = get_player()
    st = get_state()
    if not p or not st:
        return None
    t = get_tournament(st["current_year"])
    if not t or t["status"] == "done" or t["my_selected"] != 1:
        return None
    nat = _my_nat(t, p)
    conn = get_conn()
    m = conn.execute(
        """SELECT * FROM intl_matches
           WHERE tournament_id=? AND week=? AND home_score=-1
             AND (home=? OR away=?)""",
        (t["id"], week, nat, nat)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home"] == nat)
    opp = m["away"] if is_home else m["home"]
    fr = conn.execute("SELECT flag FROM intl_entries WHERE tournament_id=? AND country=?",
                      (t["id"], opp)).fetchone()
    conn.close()
    return {
        "intl": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],
        "stage": m["stage"],
        "stage_ko": STAGE_KO.get(m["stage"], m["stage"]),
        "grp": m["grp"],
        "opp": opp,
        "opp_flag": fr["flag"] if fr else "",
        "is_home": is_home,
        "week": week,
    }


def has_my_match_between(week_from, week_to):
    """주차 범위 내 내 국가대표 경기 존재 여부 (센터패널 표시용)."""
    for w in range(week_from, week_to + 1):
        if get_my_match(w):
            return True
    return False


# ─────────────────────────────────────────────
# 대회 생성 (17주차 진입 시)
# ─────────────────────────────────────────────

def start_intl_tournament(year):
    """17주차 진입 시 호출. 해당 연도에 대회가 있으면 생성."""
    from game_engine import add_log, get_player
    p = get_player()
    if not p:
        return

    is_wc = year >= WC_START_YEAR and (year - WC_START_YEAR) % WC_INTERVAL == 0
    is_cont = (not is_wc and year >= CONTINENTAL_START_YEAR
               and (year - CONTINENTAL_START_YEAR) % CONTINENTAL_INTERVAL == 0)
    if not is_wc and not is_cont:
        return
    if get_tournament(year):
        return  # 중복 생성 방지

    _clear_entry_cache()   # 새 대회 → 이전 캐시 무효화

    # [복수국적] 내 국적 목록 (1~3개). committed(고정)되어 있으면 그 나라만.
    nat1 = p.get("nationality", "") or ""
    nat2 = p.get("nationality2", "") or ""
    nat3 = p.get("nationality3", "") or ""
    committed = p.get("intl_committed", "") or ""
    if committed:
        my_nats = [committed]
    else:
        my_nats = [n for n in (nat1, nat2, nat3) if n]

    conn = get_conn()
    # 대회 종류(월드컵/대륙컵)는 '주 국적'의 대륙 기준으로 결정 (기존 동작 유지).
    nrow = conn.execute(
        "SELECT continent, grade, flag FROM countries WHERE name=?", (nat1,)).fetchone()
    my_continent = nrow["continent"] if nrow else "유럽"
    # 각 국적의 대륙/등급 조회
    nat_info = {}
    for n in my_nats:
        r = conn.execute(
            "SELECT continent, grade FROM countries WHERE name=?", (n,)).fetchone()
        if r:
            nat_info[n] = {"continent": r["continent"], "grade": r["grade"]}
    conn.close()

    if is_wc:
        kind, name = "world", "월드컵"
        entries = _qualify_world(year)
        n_groups = WC_GROUPS_BIG if year >= WC_EXPAND_YEAR else WC_GROUPS
    else:
        kind = "continent"
        name = CONF_CUP_NAME.get(my_continent, "대륙컵")
        entries = _qualify_continental(my_continent)
        n_groups = CONT_GROUPS

    entry_names = {e["name"] for e in entries}
    # 이번 대회에 '본선 진출'한 내 국적들 (대륙컵이면 그 대륙 소속만 해당)
    qualified_nats = [n for n in my_nats if n in entry_names]

    # 출전국/선발 결정
    #  - 이미 고정(committed)된 경우: 그 나라가 진출했으면 그 나라로 출전·선발 판정,
    #    아니면 이번 대회는 출전 없음.
    #  - 미고정 + 21세 이하: 진출국 1개 이상이면 '선택 대기(my_sel=3)'.
    #    본인이 팝업에서 골라야만(또는 동의해야만) 영구 고정된다.
    #  - 미고정 + 22세 이상: 더 이상 선택 불가. 국적 중 '주 국적(nat1)'으로
    #    자동 고정(cap-tie). 예선 탈락이어도 그 나라 소속으로 간주.
    #    (현실 규칙: 일정 나이가 지나면 사실상 한 나라에 묶임)
    #  - 진출국 0개 → 출전 없음(my_sel=2)
    age = year - (p.get("birth_year", year - 17) or (year - 17))
    my_nat = ""
    if committed:
        if committed in qualified_nats:
            my_nat = committed
            grade = nat_info.get(my_nat, {}).get("grade", "F")
            my_sel = 1 if _check_selection(p, grade) else 0
        else:
            my_sel = 2   # 고정된 나라가 본선 진출 못함 → 이번엔 출전 없음
    else:
        # 미고정: 본인 선택권 있음. 진출국 1개 이상이면 선택/동의 대기.
        #  (정상 흐름에선 22세 1~4주차에 get_forced_commit 팝업으로 이미 고정됨.
        #   그 팝업을 건너뛴 구 세이브 등 예외 상황의 폴백 경로.)
        if len(qualified_nats) == 0:
            my_sel = 2
        else:
            my_sel = 3   # 선택/동의 대기

    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO intl_tournaments(year, kind, name, status, my_selected, my_nat)
                 VALUES(?,?,?,?,?,?)""", (year, kind, name, "group", my_sel, my_nat))
    tid = c.lastrowid

    # 포트 추첨: 전력순 4개 포트 → 조마다 포트별 1팀
    entries.sort(key=lambda e: e["ovr"], reverse=True)
    pot_size = len(entries) // 4
    groups = {g: [] for g in _GROUP_LABELS[:n_groups]}
    for pot in range(4):
        pool = entries[pot * pot_size:(pot + 1) * pot_size]
        random.shuffle(pool)
        for gi, e in enumerate(pool):
            g = _GROUP_LABELS[gi]
            groups[g].append(e)
            c.execute("""INSERT INTO intl_entries
                         (tournament_id, country, flag, grade, ovr, grp, pot, alive)
                         VALUES(?,?,?,?,?,?,?,1)""",
                      (tid, e["name"], e["flag"], e["grade"], e["ovr"], g, pot + 1))

    # 조별리그 일정 (18~20주)
    w0 = INTL_GROUP_WEEKS[0]
    for rd, pairs in enumerate(_GROUP_ROUNDS):
        wk = w0 + rd
        for g, members in groups.items():
            for hi, ai in pairs:
                home, away = members[hi], members[ai]
                # 내가 이 대회에서 뛰는 나라(my_nat)가 끼면 내 경기로 표시.
                # 아직 선택 대기(my_sel=3)면 두 국적 다 내 경기 후보로 표시.
                if my_nat:
                    is_my = 1 if my_nat in (home["name"], away["name"]) else 0
                else:
                    is_my = 1 if (home["name"] in my_nats or away["name"] in my_nats) else 0
                c.execute("""INSERT INTO intl_matches
                             (tournament_id, stage, grp, week, home, away,
                              home_score, away_score, is_my, slot)
                             VALUES(?,?,?,?,?,?,-1,-1,?,0)""",
                          (tid, "group", g, wk, home["name"], away["name"], is_my))
    conn.commit()
    conn.close()

    # ── 로그 ──
    add_log("─" * 44, "sep")
    add_log(f"🌍 {year}년 {name} 개막!  본선 {len(entries)}개국", "event", year, INTL_CALLUP_WEEK)

    if my_sel == 3:
        # 미고정 + 진출국 1개 이상 → 본인 동의(선택) 대기
        nat_list = " / ".join(qualified_nats)
        if len(qualified_nats) == 1:
            add_log(f"   🌍 {nat_list} 대표팀 발탁 제안! 출전 여부를 선택하세요", "event", year, INTL_CALLUP_WEEK)
        else:
            add_log(f"   🌍 복수국적 발탁 가능! {nat_list} 중 대표팀을 선택하세요", "event", year, INTL_CALLUP_WEEK)
    elif my_nat:
        # 출전국 확정 (진출국 1개)
        _flag = ""
        _grow = _country_flag(my_nat)
        try:
            my_g = next(g for g, ms in groups.items() if any(m["name"] == my_nat for m in ms))
            mates = [f"{m['flag']}{m['name']}" for m in groups[my_g] if m["name"] != my_nat]
            add_log(f"   {_grow}{my_nat} {my_g}조 편성  (vs {', '.join(mates)})",
                    "event", year, INTL_CALLUP_WEEK)
        except StopIteration:
            pass
        if my_sel == 1:
            add_log(f"   📣 국가대표 소집! 조별리그 {w0}~{w0+2}주차", "event", year, INTL_CALLUP_WEEK)
        else:
            add_log("   📋 국가대표 미선발... 대표팀 경기를 지켜봅니다", "event", year, INTL_CALLUP_WEEK)
            _save_trophy(year, my_nat, name, "국가대표 미선발")
    else:
        # 진출국 없음
        none_nat = my_nats[0] if my_nats else ""
        add_log(f"   📋 {none_nat}, 예선 탈락으로 본선 진출 실패", "event", year, INTL_CALLUP_WEEK)
        if none_nat:
            _save_trophy(year, none_nat, name, "예선 탈락")


def _country_flag(name):
    """국가 국기 조회 (없으면 빈 문자열)."""
    conn = get_conn()
    r = conn.execute("SELECT flag FROM countries WHERE name=?", (name,)).fetchone()
    conn.close()
    return r["flag"] if r else ""


def _vet_bonus(age):
    """베테랑 가산점: 노련함 프리미엄. 최대 +5. (절대 하한은 별도 보장)"""
    if age >= 36:
        return 5
    if age >= 33:
        return 4
    if age >= 30:
        return 2
    return 0


def _check_selection(p, my_grade):
    """국가대표 선발 판정 — 자국 등급(국대 평균 전력) 대비 상대평가.

    선발 기준을 클럽 선수 풀이 아니라 GRADE_TEAM_OVR(국대 경기 시뮬에 쓰는
    그 나라 평균 전력)에 정합시킨다. 선발 스케일 = 경기 스케일.
      임계 = 국대평균 - 마진(톱권)
      유효OVR = 내 OVR + 베테랑 보너스(최대 +5)
      → 유효OVR이 임계 이상이면 선발. 단 절대 하한 미달은 보너스로도 구제 불가.
    """
    nat_avg = GRADE_TEAM_OVR.get(my_grade, 45)
    threshold = nat_avg - INTL_SELECTION_MARGIN
    my_ovr = p.get("ovr", 0)
    eff_ovr = my_ovr + _vet_bonus(p.get("age", 25))

    # 절대 하한: 베테랑 보너스로도 이 밑이면 탈락 (36세 60 같은 경우 차단)
    if my_ovr < threshold - INTL_SELECTION_MARGIN:
        return False
    if eff_ovr < threshold:
        return False
    if p.get("total_matches", 0) < INTL_MIN_MATCHES:
        return False
    tid = p.get("current_team_id", 0)
    if not tid:
        return False
    # 티어 보조 가드(완화): 등급 기준 티어보다 한 단계까지는 허용
    #   → 노쇠해 하위 리그로 내려간 베테랑도 OVR이 충분하면 막지 않는다.
    conn = get_conn()
    row = conn.execute(
        """SELECT l.tier FROM teams t JOIN leagues l ON t.league_id=l.id
           WHERE t.id=?""", (tid,)).fetchone()
    conn.close()
    my_tier = row["tier"] if row else 99
    return my_tier <= INTL_MAX_TIER.get(my_grade, 3) + 1


def _qualify_world(year=0):
    """대륙별 쿼터 + 확률 예선으로 본선 진출국 선발.
    year >= WC_EXPAND_YEAR 이면 64개국, 아니면 32개국."""
    big = (year >= WC_EXPAND_YEAR)
    quota_map = WC_QUOTA_BIG if big else WC_QUOTA
    n_teams = WC_TEAMS_BIG if big else WC_TEAMS

    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT name, flag, continent, grade FROM countries").fetchall()]
    conn.close()

    # 오세아니아 → 아시아 편입
    for r in rows:
        if r["continent"] == "오세아니아":
            r["continent"] = "아시아"
        r["qual"] = GRADE_QUAL_BASE.get(r["grade"], 0.2) + random.uniform(-QUAL_NOISE, QUAL_NOISE)
        r["ovr"] = GRADE_TEAM_OVR.get(r["grade"], 45) + random.uniform(-3, 3)

    # 개최국: S/A/B 중 가중 랜덤 → 자동 진출
    host_pool = [r for r in rows if r["grade"] in ("S", "A", "B")]
    weights = [{"S": 3, "A": 2, "B": 1}[r["grade"]] for r in host_pool]
    host = random.choices(host_pool, weights=weights)[0]

    picked = [host]
    for cont, quota in quota_map.items():
        q = quota - (1 if host["continent"] == cont else 0)
        pool = [r for r in rows if r["continent"] == cont and r["name"] != host["name"]]
        pool.sort(key=lambda r: r["qual"], reverse=True)
        picked.extend(pool[:q])
    return picked[:n_teams]


def _qualify_continental(my_continent):
    """내 대륙 연맹의 대륙컵 16개국 선발 (남북미 통합, 오세아니아→아시아)."""
    confs = CONFEDERATIONS.get(my_continent, [my_continent])
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"SELECT name, flag, continent, grade FROM countries WHERE continent IN ({','.join('?'*len(confs))})",
        confs).fetchall()]
    conn.close()
    for r in rows:
        r["qual"] = GRADE_QUAL_BASE.get(r["grade"], 0.2) + random.uniform(-QUAL_NOISE, QUAL_NOISE)
        r["ovr"] = GRADE_TEAM_OVR.get(r["grade"], 45) + random.uniform(-3, 3)
    rows.sort(key=lambda r: r["qual"], reverse=True)
    return rows[:CONT_TEAMS]


# ─────────────────────────────────────────────
# 주차 처리 (advance_4weeks에서 매주 호출)
# ─────────────────────────────────────────────

def process_intl_week(week):
    """이번 주차의 남은 국제대회 경기(AI) 시뮬 + 라운드 진행."""
    from game_engine import get_state
    st = get_state()
    if not st:
        return
    t = get_tournament(st["current_year"])
    if not t or t["status"] == "done":
        return

    conn = get_conn()
    pending = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches
           WHERE tournament_id=? AND week<=? AND home_score=-1""",
        (t["id"], week)).fetchall()]
    conn.close()

    for m in pending:
        _sim_ai_match(t, m)

    # 라운드 진행
    last_group_week = INTL_GROUP_WEEKS[1]
    if t["kind"] == "world":
        # 64개국(2002~)은 32강부터, 32개국은 16강부터
        big = (t["year"] >= WC_EXPAND_YEAR)
        if big:
            plan = {last_group_week: ("R32", 21), 21: ("R16", 22),
                    22: ("QF", 23), 23: ("SF", 24), 24: ("F", 25), 25: (None, None)}
        else:
            plan = {last_group_week: ("R16", 21), 21: ("QF", 22),
                    22: ("SF", 23), 23: ("F", 24), 24: (None, None)}
    else:
        # 대륙컵 24개국: 조별(6조) → 16강 → 8강 → 4강 → 결승
        plan = {last_group_week: ("R16", 21), 21: ("QF", 22),
                22: ("SF", 23), 23: ("F", 24), 24: (None, None)}

    if week not in plan:
        return
    next_stage, next_week = plan[week]

    if week == last_group_week:
        _finalize_groups(t, next_stage, next_week)
    elif next_stage is None:
        _finish_tournament(t, week)
    else:
        _advance_knockout(t, week, next_stage, next_week)


# ─────────────────────────────────────────────
# 경기 시뮬 (AI)
# ─────────────────────────────────────────────

def _entry(tid, country):
    key = (tid, country)
    cached = _entry_cache.get(key)
    if cached is not None:
        return cached
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=? AND country=?",
        (tid, country)).fetchone()
    conn.close()
    val = dict(row) if row else {"ovr": 50, "flag": "", "grade": "F"}
    _entry_cache[key] = val
    return val


def _match_outcome(h_ovr, a_ovr, knockout):
    """중립 구장 가정. 'home'/'draw'/'away' 반환 (KO는 무승부 → 승부차기)."""
    diff = h_ovr - a_ovr
    hw = max(0.08, min(0.85, 0.46 + diff * 0.014))
    dw = 0.22
    aw = max(0.05, 1.0 - hw - dw)
    roll = random.random()
    if roll < hw:
        return "home"
    elif roll < hw + dw:
        return "draw"
    return "away"


def _gen_intl_score(outcome):
    from game_engine import _gen_score
    return _gen_score(outcome)


def _resolve_pso(h_ovr, a_ovr):
    """승부차기: 전력이 살짝 유리하게."""
    p_home = 0.5 + max(-0.1, min(0.1, (h_ovr - a_ovr) * 0.006))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


def _sim_ai_match(t, m, my_played=False):
    """AI끼리(또는 내가 결장한 내 경기) 시뮬."""
    from game_engine import add_log, get_player
    he = _entry(t["id"], m["home"])
    ae = _entry(t["id"], m["away"])
    knockout = m["stage"] != "group"

    outcome = _match_outcome(he["ovr"], ae["ovr"], knockout)
    pso_winner, pso_score = "", ""
    if knockout and outcome == "draw":
        win_home, pso_score = _resolve_pso(he["ovr"], ae["ovr"])
        pso_winner = m["home"] if win_home else m["away"]
    hs, as_ = _gen_intl_score(outcome)

    conn = get_conn()
    conn.execute("""UPDATE intl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=? WHERE id=?""",
                 (hs, as_, pso_winner, pso_score, m["id"]))
    conn.commit()
    conn.close()

    # 내 국가 경기(결장 포함)는 로그 출력. AI끼리 경기는 get_player() 불필요.
    if m["is_my"]:
        p = get_player()
        nat = _my_nat(t, p)
        if nat in (m["home"], m["away"]):
            stage_ko = STAGE_KO.get(m["stage"], "")
            pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
            add_log(f"🌍 {t['name']} {stage_ko}  "
                    f"{he['flag']}{m['home']} {hs}-{as_} {ae['flag']}{m['away']}{pso_txt}", "match")
            if t["my_selected"] == 1 and not my_played:
                add_log("   🚑 부상으로 대표팀 경기 결장", "match")


def _winner_of(m):
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home"] if m["home_score"] > m["away_score"] else m["away"]


# ─────────────────────────────────────────────
# 내 경기 시뮬
# ─────────────────────────────────────────────

def simulate_my_match(week, p):
    """내가 출전하는 국가대표 경기."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score)
    info = get_my_match(week)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM intl_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM intl_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    nat = _my_nat(t, p)
    # [복수국적] A매치 첫 출전 → 그 나라로 영구 고정(cap-tie).
    # 이후 대회부터는 이 나라로만 차출된다.
    if nat and not (p.get("intl_committed", "") or ""):
        from game_engine import update_player as _upd
        _upd(intl_committed=nat)
    he = _entry(t["id"], m["home"])
    ae = _entry(t["id"], m["away"])
    is_home = info["is_home"]
    knockout = m["stage"] != "group"

    # 내 출전 보너스 (클럽 경기와 동일 계수)
    bonus = p.get("ovr", 40) * 0.08
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = _match_outcome(h_ovr, a_ovr, knockout)
    pso_winner, pso_score = "", ""
    if knockout and outcome == "draw":
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home"] if win_home else m["away"]
    hs, as_ = _gen_score(outcome)

    goals, assists, saves, rating, events, detail = _player_perf(p, outcome, is_home, hs, as_)
    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    conn = get_conn()
    conn.execute("""UPDATE intl_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?,
                    my_played=1, my_nat=?, my_position=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score,
                  nat, p.get("position", ""),
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  my_conceded, m["id"]))
    conn.commit()
    conn.close()

    # 국가대표 개인 기록 (클럽 시즌 통계와 분리)
    #  [cap-tie] 본선 무대(조별리그~결승)를 밟으면 그 나라로 영구 고정된다.
    #  이 함수는 본선 경기에서만 호출되므로(예선은 별도), 출전 즉시 cap 처리.
    update_player(
        intl_caps=p.get("intl_caps", 0) + 1,
        intl_goals=p.get("intl_goals", 0) + goals,
        intl_assists=p.get("intl_assists", 0) + assists,
        intl_capped=1,
        intl_committed=(p.get("intl_committed", "") or nat),
    )
    # [세부 지표] 통산(total_*)에도 누적 → 커리어 통합 통계에 A매치 반영
    p2 = get_player()
    update_player(
        total_shots=p2.get("total_shots", 0) + detail["shots"],
        total_shots_on=p2.get("total_shots_on", 0) + detail["shots_on"],
        total_key_passes=p2.get("total_key_passes", 0) + detail["key_passes"],
        total_dribbles=p2.get("total_dribbles", 0) + detail["dribbles"],
        total_blocks=p2.get("total_blocks", 0) + detail["blocks"],
    )

    # 인기/스트레스/행복
    p2 = get_player()
    _update_pop(p2, goals, assists, rating)
    p2 = get_player()
    ns = min(100, p2["stress"] + 8)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    # ── 로그 ──
    stage_ko = STAGE_KO.get(m["stage"], "")
    grp_txt = f" {m['grp']}조" if m["stage"] == "group" else ""
    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == nat else '패'})"
        rs = "무"
    add_log("─" * 44, "sep")
    add_log(f"🌍 {t['name']} {stage_ko}{grp_txt}  {week}주차", "match")
    add_log(f"   {he['flag']}{m['home']} {hs}-{as_} {ae['flag']}{m['away']}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating}  골 {goals}  어시 {assists}", "match")
    for ev in events:
        mm = random.randint(1, 90)
        add_log(f"   {mm}'  {ev}", "match")


# ─────────────────────────────────────────────
# 조별리그 마감 / 토너먼트 진행
# ─────────────────────────────────────────────

def get_group_standings(tid, grp):
    """조 순위 계산: 승점 → 득실 → 다득점 → 팀 전력."""
    conn = get_conn()
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM intl_entries WHERE tournament_id=? AND grp=?",
        (tid, grp)).fetchall()]
    matches = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND grp=?
           AND stage='group' AND home_score>=0""", (tid, grp)).fetchall()]
    conn.close()

    tbl = {e["country"]: {"country": e["country"], "flag": e["flag"], "ovr": e["ovr"],
                          "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
           for e in entries}
    for m in matches:
        h, a = tbl.get(m["home"]), tbl.get(m["away"])
        if not h or not a:
            continue
        hs, as_ = m["home_score"], m["away_score"]
        h["p"] += 1; a["p"] += 1
        h["gf"] += hs; h["ga"] += as_
        a["gf"] += as_; a["ga"] += hs
        if hs > as_:
            h["w"] += 1; h["pts"] += 3; a["l"] += 1
        elif hs < as_:
            a["w"] += 1; a["pts"] += 3; h["l"] += 1
        else:
            h["d"] += 1; a["d"] += 1; h["pts"] += 1; a["pts"] += 1
    rows = list(tbl.values())
    rows.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]), reverse=True)
    return rows


def _finalize_groups(t, next_stage, next_week):
    """조별리그 종료 → 16강 진출국 확정, R16 대진 생성.
    - 월드컵(8조): 각 조 1·2위 = 16팀
    - 대륙컵(6조): 각 조 1·2위(12팀) + 성적 좋은 3위 중 상위 4팀 = 16팀
    """
    from game_engine import add_log, get_player
    tid = t["id"]
    is_wc = (t["kind"] == "world")
    if is_wc:
        n_groups = WC_GROUPS_BIG if t["year"] >= WC_EXPAND_YEAR else WC_GROUPS
    else:
        n_groups = CONT_GROUPS
    labels = _GROUP_LABELS[:n_groups]

    firsts, seconds = {}, {}
    thirds = []          # (조라벨, row) — 대륙컵 best-3rd 후보
    eliminated = []
    standings = {}
    for g in labels:
        rows = get_group_standings(tid, g)
        standings[g] = rows
        firsts[g]  = rows[0]["country"]
        seconds[g] = rows[1]["country"]
        if is_wc:
            # 월드컵: 3위 이하 전부 탈락
            eliminated.extend(r["country"] for r in rows[2:])
        else:
            # 대륙컵: 3위는 best-3rd 경쟁, 4위는 탈락
            if len(rows) >= 3:
                thirds.append((g, rows[2]))
            eliminated.extend(r["country"] for r in rows[3:])

    # 대륙컵: 3위 팀들 중 성적 좋은 상위 CONT_BEST_THIRDS만 진출, 나머지 탈락
    best_thirds = []
    if not is_wc:
        thirds.sort(key=lambda gr: (gr[1]["pts"], gr[1]["gf"] - gr[1]["ga"],
                                     gr[1]["gf"], gr[1]["ovr"]), reverse=True)
        adv = thirds[:CONT_BEST_THIRDS]
        best_thirds = [(g, r["country"]) for g, r in adv]
        eliminated.extend(r["country"] for _, r in thirds[CONT_BEST_THIRDS:])

    conn = get_conn()
    c = conn.cursor()
    for nat_e in eliminated:
        c.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                  (tid, nat_e))

    # ── R16 대진 생성 ──
    if is_wc:
        # 표준 월드컵 대진: 1A-2B, 1C-2D, … / 1B-2A, 1D-2C, …
        pairs = []
        for i in range(0, n_groups, 2):
            g1, g2 = labels[i], labels[i + 1]
            pairs.append((firsts[g1], seconds[g2]))
        for i in range(0, n_groups, 2):
            g1, g2 = labels[i], labels[i + 1]
            pairs.append((firsts[g2], seconds[g1]))
    else:
        # 대륙컵 24개국 표준 대진 (유로 2016 방식):
        #   16팀 = 1위6 + 2위6 + 3위4.
        #   1위는 3위/2위와, 2위끼리 일부 맞붙는 고정 브래킷.
        #   구현 단순화: 1위6 + best3위4 = 10팀을 한 풀, 2위6을 다른 풀로 나눠
        #   상위 시드(1위)가 하위 시드(2위/3위)와 만나도록 페어링.
        firsts_list  = [firsts[g]  for g in labels]              # 6
        seconds_list = [seconds[g] for g in labels]              # 6
        thirds_list  = [nat for _, nat in best_thirds]           # 4
        # 시드 풀: 강(1위) 6 + 약(2위6 + 3위4) 10 → 16
        # 1위 6팀은 2위/3위 중에서 상대를 받고, 남는 2위끼리 맞붙음.
        strong = list(firsts_list)                # 6 (상위 시드)
        weak   = thirds_list + seconds_list        # 10 (하위 시드: 3위 먼저 소진)
        pairs = []
        for s in strong:
            opp = weak.pop(0) if weak else None
            if opp is not None:
                pairs.append((s, opp))
        # 남은 weak(2위 4팀)끼리 2경기
        while len(weak) >= 2:
            pairs.append((weak.pop(0), weak.pop(0)))

    p = get_player()
    nat = _my_nat(t, p)
    for slot, (home, away) in enumerate(pairs):
        is_my = 1 if nat in (home, away) else 0
        c.execute("""INSERT INTO intl_matches
                     (tournament_id, stage, grp, week, home, away,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,?,-1,-1,?,?)""",
                  (tid, next_stage, "", next_week, home, away, is_my, slot))
    c.execute("UPDATE intl_tournaments SET status='ko' WHERE id=?", (tid,))
    conn.commit()
    conn.close()

    add_log(f"🌍 {t['name']} 조별리그 종료 → {STAGE_KO[next_stage]} 진출국 확정",
            "event")
    # 내 국가가 조별 탈락했으면 결과 확정
    if nat and nat in eliminated:
        _record_my_exit(t, "조별리그 탈락")


def _advance_knockout(t, week, next_stage, next_week):
    """현재 KO 라운드 종료 → 패자 탈락, 다음 라운드 생성."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND week=?
           AND stage!='group' ORDER BY slot""", (tid, week)).fetchall()]
    conn.close()
    if not cur:
        return

    p = get_player()
    nat = _my_nat(t, p)
    cur_stage_ko = STAGE_KO.get(cur[0]["stage"], "")

    winners = []
    conn = get_conn()
    c = conn.cursor()
    for m in cur:
        w = _winner_of(m)
        loser = m["away"] if w == m["home"] else m["home"]
        winners.append((m["slot"], w))
        c.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                  (tid, loser))
        if nat and loser == nat:
            conn.commit()
            conn.close()
            _record_my_exit(t, cur_stage_ko)
            conn = get_conn()
            c = conn.cursor()

    winners.sort()
    for slot in range(0, len(winners), 2):
        if slot + 1 >= len(winners):
            break
        home, away = winners[slot][1], winners[slot + 1][1]
        is_my = 1 if nat in (home, away) else 0
        c.execute("""INSERT INTO intl_matches
                     (tournament_id, stage, grp, week, home, away,
                      home_score, away_score, is_my, slot)
                     VALUES(?,?,?,?,?,?,-1,-1,?,?)""",
                  (tid, next_stage, "", next_week, home, away, is_my, slot // 2))
    conn.commit()
    conn.close()
    add_log(f"🌍 {t['name']} {cur_stage_ko} 종료 → {STAGE_KO[next_stage]} 대진 확정", "event")


def _finish_tournament(t, final_week):
    """결승 종료 → 우승국 확정, 내 결과 기록."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    fm = conn.execute(
        """SELECT * FROM intl_matches WHERE tournament_id=? AND stage='F'
           AND home_score>=0""", (tid,)).fetchone()
    conn.close()
    if not fm:
        return
    fm = dict(fm)
    winner = _winner_of(fm)
    loser = fm["away"] if winner == fm["home"] else fm["home"]

    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET status='done', winner=? WHERE id=?",
                 (winner, tid))
    conn.execute("UPDATE intl_entries SET alive=0 WHERE tournament_id=? AND country=?",
                 (tid, loser))
    conn.commit()
    conn.close()

    we = _entry(tid, winner)
    add_log(f"🏆 {t['name']} 우승: {we['flag']}{winner}!", "event")

    p = get_player()
    nat = _my_nat(t, p)
    if nat == winner:
        _record_my_exit(t, "우승")
    elif nat == loser:
        _record_my_exit(t, "준우승")


# ─────────────────────────────────────────────
# 내 결과 확정 + 보상
# ─────────────────────────────────────────────

_REWARD = {  # 결과: (명성, 인기, 행복도) ─ 월드컵 기준
    "우승":         (25, 15, 20),
    "준우승":       (15,  8, 10),
    "4강":          (10,  5,  6),
    "8강":          ( 6,  3,  3),
    "16강":         ( 3,  2,  1),
    "조별리그 탈락": ( 1,  0, -2),
}


def _record_my_exit(t, result):
    """내 국가의 최종 성적 확정: 트로피/이력 기록 + 보상 (선발됐을 때만)."""
    from game_engine import add_log, get_player, update_player
    p = get_player()
    if not p:
        return
    nat = _my_nat(t, p)

    # 미선발(또는 출전 보류)이면 이 대회 성적은 내 경력이 아니다.
    #  - my_result는 'XX 미선발'로만 표시(대회 화면 일관성용)
    #  - trophy_log / intl_history(개인기록) / 보상은 일절 기록하지 않는다.
    #    (선발 안 됐는데 대표팀이 우승했다고 내 우승 트로피로 박히던 버그 방지)
    if t["my_selected"] != 1:
        conn = get_conn()
        conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?",
                     (f"{result} (미선발)", t["id"]))
        conn.commit()
        conn.close()
        return

    conn = get_conn()
    conn.execute("UPDATE intl_tournaments SET my_result=? WHERE id=?", (result, t["id"]))
    conn.commit()
    conn.close()

    _save_trophy(t["year"], nat, t["name"], result)

    fame_g, pop_g, hap_g = _REWARD.get(result, (0, 0, 0))
    if t["kind"] != "world":  # 대륙컵은 60% 스케일
        fame_g = round(fame_g * 0.6)
        pop_g = round(pop_g * 0.6)
        hap_g = round(hap_g * 0.6)

    update_player(
        fame=min(100, p.get("fame", 0) + fame_g),
        popularity=min(100, p.get("popularity", 0) + pop_g),
        happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
    )

    # 이번 대회 개인 기록 집계 → intl_history (대회 단위)
    conn = get_conn()
    agg = conn.execute(
        """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM intl_matches
           WHERE tournament_id=? AND my_played=1""", (t["id"],)).fetchone()
    conn.execute("""INSERT INTO intl_history(year, competition, team_name, result,
                                             goals, assists, caps, rating)
                    VALUES(?,?,?,?,?,?,?,?)""",
                 (t["year"], t["name"], nat, result,
                  agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()

    icon = "🏆" if result == "우승" else "🌍"
    add_log(f"{icon} {t['year']}년 {t['name']} 최종 성적: {result}  "
            f"(명성 +{fame_g}, 인기 +{pop_g})", "event")


def _save_trophy(year, nat, competition, result):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=?",
        (year, competition)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,0,?)""", (year, nat, result, competition))
        conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# 국제전 이력 조회 (커리어창 / 은퇴창 공용)
# ─────────────────────────────────────────────

def get_my_intl_matches():
    """내가 실제 출전한 A매치 목록 (시간순). 결장 경기는 제외.

    반환 dict: year, week, position, nat, nat_flag, comp, stage,
               opp, opp_flag, goals, assists, saves, conceded,
               rating, score, result(승/무/패, PSO 표기 포함)
    """
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT m.*, t.year AS t_year, t.name AS comp
           FROM intl_matches m
           JOIN intl_tournaments t ON m.tournament_id = t.id
           WHERE m.my_played = 1
           ORDER BY t.year, m.week""").fetchall()]
    flags = {(r["tournament_id"], r["country"]): r["flag"]
             for r in conn.execute(
                 "SELECT tournament_id, country, flag FROM intl_entries").fetchall()}
    conn.close()

    out = []
    for m in rows:
        nat = m["my_nat"]
        is_home = (m["home"] == nat)
        opp  = m["away"] if is_home else m["home"]
        my_s = m["home_score"] if is_home else m["away_score"]
        op_s = m["away_score"] if is_home else m["home_score"]

        if m["pso_winner"]:
            result = "승(PSO)" if m["pso_winner"] == nat else "패(PSO)"
        elif my_s > op_s:
            result = "승"
        elif my_s < op_s:
            result = "패"
        else:
            result = "무"

        stage = STAGE_KO.get(m["stage"], m["stage"])
        if m["stage"] == "group" and m["grp"]:
            stage = f"조별 {m['grp']}조"

        out.append({
            "year": m["t_year"], "week": m["week"],
            "position": m["my_position"], "nat": nat,
            "nat_flag": flags.get((m["tournament_id"], nat), ""),
            "comp": m["comp"], "stage": stage,
            "opp": opp, "opp_flag": flags.get((m["tournament_id"], opp), ""),
            "goals": m["my_goals"], "assists": m["my_assists"],
            "saves": m["my_saves"], "conceded": op_s,
            "rating": m["my_rating"],
            "shots": m.get("my_shots", 0), "shots_on": m.get("my_shots_on", 0),
            "key_passes": m.get("my_key_passes", 0), "dribbles": m.get("my_dribbles", 0),
            "blocks": m.get("my_blocks", 0), "pass_acc": m.get("my_pass_acc", 0),
            "score": f"{my_s}-{op_s}", "result": result,
        })
    return out