# cup_engine.py
# [2026-07 신설, 2차 개편] 국내 컵대회(FA컵식) — 실제 코리안컵 구조를 참고해
# '단계적 합류'로 다시 설계. 그 나라에 존재하는 티어 전부(1~5부, 나라마다
# 다름)가 참가하되, 한 라운드에 다 같이 들어가는 게 아니라 하위 티어부터
# 먼저 시작해서 라운드가 진행될수록 상위 티어가 순서대로 합류한다
# (예: 코리안컵 — 프리라운드 K5, 1라운드 K3+K4+프리 승자, 2라운드 K리그2+
# 1라운드 승자, 3라운드부터 K리그1 합류). 무승부는 재경기 없이 승부차기로
# 바로 결정한다.
#
# [설계 이유] 챔스/월드컵 등 국제 일정이 있는 건 1부 팀뿐이다 — 그래서
# 1부는 최대한 늦게 합류시켜서 챔스 주차(8~23주차 — 2026-07 대륙 규모
# 재조정(북남미 48팀·R32 추가)으로 기간이 한 주 더 늘어남)와 안 겹치게
# 하고, 2부 이하는 그런 제약이 전혀 없으니 시즌 초반(5주차~)부터 자유롭게
# 예선을 치르게 한다. 라운드 하나는 일부러 박싱데이 시즌(달력상 12월
# 하순~1월 초)에 걸리게 배치해서 그 시기 일정이 자연스럽게 촘촘해진다.
#
# 범위: [2026-07 전체 국가 확장] 처음엔 챔스가 '내 대륙', 월드컵이 '내
# 국가대표'로 좁힌 것과 같은 원칙으로 '내 팀이 속한 나라' 하나에 대해서만
# 생성했었는데, 무소속 시즌엔 그 어떤 나라의 컵대회도 안 열려 세계
# 기록실에서 전부 "기록 없음"으로 보이는 버그가 있었다(신민용 리포트).
# 실측 결과 리그가 있는 전 세계 200여 개국 컵대회를 한 번에 생성해도
# 0.2초, 시즌 내내(수십 주) 진행까지 다 합쳐도 3초 남짓이라 성능 부담이
# 크지 않아, 이제 리그가 하나라도 있는 나라 전부에서 매 시즌 컵대회가
# 열린다. 다만 이벤트 로그(add_log)는 여전히 '내 나라(또는 대표국적)'
# 대회일 때만 남긴다 — 안 그러면 관심 없는 200개국 소식이 매주 로그에
# 다 쌓인다.
import random
from database import get_conn

# 라운드별 주차 후보 — 1부가 아직 합류 전이면 앞쪽(챔스 시작 전) 구간을,
# 합류한 뒤로는 뒤쪽(챔스 이후) 구간을 순서대로 사용한다. round_counter를
# 그대로 인덱스로 써서 별도 분기 없이 자연스럽게 앞→뒤로 이어지게 한다.
# [2026-07] 챔스가 8~21주로 늘어나면서(스위스 방식) 뒤쪽 구간 시작을
# 18→24로 밀었다. [2026-07 후속] 북남미 48팀·R32 추가로 챔스 종료가
# 21→23주로 한 주 더 밀렸지만, 24는 그대로 둬도 됨 — 여유가 3주에서
# 1주로 줄었을 뿐 챔스 결승(23주)과 겹치지 않는다.
CUP_ROUND_WEEKS_POOL = [5, 6, 7, 24, 27, 30, 33, 36, 39, 42]


_CUP_REWARD_BY_TEAMS = [
    (4,   (4, 3, 3)),
    (8,   (2, 2, 2)),
    (16,  (1, 1, 1)),
    (999, (1, 0, 0)),
]

# [2026-07 버그 수정] 모든 나라에 "FA컵"을 그대로 박아놨었는데, 정작 진짜
# 잉글랜드 FA컵 말고는 그 이름을 그대로 쓰는 나라가 거의 없다(신민용 지적
# — 한국도 2024년에 'FA컵'에서 '코리아컵'으로 개명함, 프랑스는 쿠프 드
# 프랑스, 독일은 DFB-포칼, 스페인은 코파 델 레이 등 대부분 국호·국가
# 상징을 붙인 이름을 쓴다). 알려진 주요국은 실제 대회명을 쓰고, 나머지는
# 그 나라 관례("국호+컵")를 따라 "{국가명}컵"으로 자동 생성한다.
CUP_NAME_BY_COUNTRY = {
    "잉글랜드": "FA컵",              # 실제로 유일하게 'FA컵'이 맞는 나라
    "대한민국": "코리아컵",
    "프랑스": "쿠프 드 프랑스",
    "독일": "DFB-포칼",
    "스페인": "코파 델 레이",
    "이탈리아": "코파 이탈리아",
    "브라질": "코파 두 브라지우",
    "아르헨티나": "코파 아르헨티나",
    "포르투갈": "타사 드 포르투갈",
    "네덜란드": "KNVB 베커",
    "벨기에": "벨기에컵",
    "미국": "US 오픈컵",
    "멕시코": "코파 MX",
    "일본": "천황배",
    "사우디아라비아": "킹컵",
    "튀르키예": "튀르키예컵",
    "스코틀랜드": "스코티시컵",
    "러시아": "러시아컵",
    "크로아티아": "크로아티아컵",
}


def _cup_name_for_country(country_id):
    conn = get_conn()
    row = conn.execute("SELECT name FROM countries WHERE id=?", (country_id,)).fetchone()
    conn.close()
    cname = row["name"] if row else ""
    return CUP_NAME_BY_COUNTRY.get(cname, f"{cname}컵" if cname else "컵대회")


def _round_name(n_teams: int, round_counter: int) -> str:
    m = {2: "결승", 4: "4강", 8: "8강", 16: "16강", 32: "32강", 64: "64강"}
    # round_counter는 0부터 시작하는 내부 인덱스라, 사람이 보는 라운드
    # 번호는 +1 해서 "0라운드"가 아니라 "1라운드"부터 보이게 한다.
    return m.get(n_teams, f"{round_counter + 1}라운드")


def get_cup_tournament(year, country_id):
    if not country_id:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM cup_tournaments WHERE year=? AND country_id=?",
        (year, country_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def _my_country_id(p):
    """이번 시즌 컵대회를 어느 나라 것으로 만들지 결정.
    [2026-07 버그 수정] 예전엔 무소속(current_team_id=0)이면 그냥 None을
    반환해서 start_domestic_cup()이 아무것도 안 만들고 끝나버렸다 — 그 결과
    무소속으로 보낸 시즌엔 그 어떤 나라의 컵대회도 통째로 생성이 안 되고,
    세계 기록실에서 내 나라를 봐도 그 해만 쏙 빠지는(극단적으로는 계속
    무소속이면 전부 "기록 없음") 문제가 있었다. 팀이 있으면 그 팀 나라를,
    없으면 대표국적 나라로 폴백한다 — 이번 시즌 내가 못 뛰는 건 어차피
    my_in=0으로 정확히 반영되니, 최소한 그 나라의 컵대회 자체는 계속
    존재해야 기록실 공백이 안 생긴다."""
    tid = p.get("current_team_id", 0)
    if tid:
        conn = get_conn()
        row = conn.execute(
            """SELECT l.country_id AS cid FROM teams t JOIN leagues l ON t.league_id=l.id
               WHERE t.id=?""", (tid,)).fetchone()
        conn.close()
        if row:
            return row["cid"]
    nat = p.get("nationality")
    if not nat:
        return None
    conn = get_conn()
    row = conn.execute("SELECT id FROM countries WHERE name=?", (nat,)).fetchone()
    conn.close()
    return row["id"] if row else None


def _my_cup_tournament(p, year):
    cid = _my_country_id(p)
    if not cid:
        return None
    return get_cup_tournament(year, cid)


def get_my_cup_matches():
    """[2026-07 커리어 기록 추가] 내가 실제 출전한 국내 컵대회 경기 목록(시간순).
    champions_engine.get_my_cl_matches()와 같은 패턴.
    [2026-07 수정, 신민용 요청] 결장(부상/출전정지) 경기도 이제 포함한다 —
    예전엔 my_played=1인 것만 보여줘서 결장 경기 자체가 커리어에서 통째로
    사라졌는데, 이제 "(부상)"/"(출전정지)" 식으로 표시하기 위해 결장
    경기도 함께 조회하고 my_absence_reason을 실어 보낸다.
    컵대회는 국가/시즌 단위라 cup_matches만으로 연도순 정렬하면 충분하다."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT m.*, t.year AS t_year, t.name AS comp, t.my_team_id AS t_my_tid
           FROM cup_matches m
           JOIN cup_tournaments t ON m.tournament_id = t.id
           WHERE m.my_played = 1 OR m.my_absence_reason IS NOT NULL
           ORDER BY t.year, m.week""").fetchall()]
    conn.close()

    out = []

    for m in rows:
        # [2026-07 버그수정, champions_engine.get_my_cl_matches와 동일한
        # 버그 발견/수정] "현재" 소속팀 대신 cup_tournaments.my_team_id
        # (그 대회 시작 시점에 고정 저장된 내 팀)를 쓴다 — 안 그러면 그
        # 이후 이적한 경우 과거 경기의 상대가 그때의 내 팀 이름으로
        # 뒤바뀌어 표시되고 스코어/승패도 뒤집힌다.
        my_tid = m["t_my_tid"]
        he = _entry(m["tournament_id"], m["home_team_id"])
        ae = _entry(m["tournament_id"], m["away_team_id"])
        is_home = (m["home_team_id"] == my_tid)
        opp = ae if is_home else he
        my_s = m["home_score"] if is_home else m["away_score"]
        op_s = m["away_score"] if is_home else m["home_score"]

        if m["pso_winner"]:
            won = (m["pso_winner"] == (m["home_team_id"] if is_home else m["away_team_id"]))
            result = "승(PSO)" if won else "패(PSO)"
        elif my_s > op_s:
            result = "승"
        elif my_s < op_s:
            result = "패"
        else:
            result = "무"

        from constants import day_to_iso_date_str, week_to_iso_date_str
        date_str = (day_to_iso_date_str(m["t_year"], m["day"]) if m.get("day")
                    else week_to_iso_date_str(m["t_year"], m["week"]))

        out.append({
            "year": m["t_year"], "week": m["week"], "date": date_str,
            "comp": m["comp"], "stage": m["round_name"],
            "opp": opp.get("team_name", "?"), "opp_tier": opp.get("tier"),
            "goals": m["my_goals"], "assists": m["my_assists"],
            "saves": m["my_saves"], "conceded": op_s,
            "rating": m["my_rating"],
            "score": f"{my_s}-{op_s}", "result": result,
            "absence_reason": m.get("my_absence_reason"),
        })
    return out


def has_my_cup_match_between(week_from, week_to):
    """주차 범위 내 내 컵대회 경기 존재 여부 (센터패널 표시용).
    [2026-07 신설, 신민용 리포트: "일정이 안뜰 때가 있다"] intl_engine.
    has_my_match_between / champions_engine.has_my_cl_match_between과
    똑같은 용도의 함수가 컵대회 쪽에만 없었다. 그래서 center_panel.py의
    _check_match()가 리그/국제대회/챔스만 확인하고 컵대회는 아예 확인을
    안 해서, 그 주에 컵대회 경기만 있고 리그 경기가 없는 경우 실제로는
    경기가 있는데도 "이번 주 경기 없음" 배너가 잘못 떴다."""
    for w in range(week_from, week_to + 1):
        if get_my_cup_match(w):
            return True
    return False


def get_my_cup_match(week):
    """이번 주차에 내가 뛸 컵대회 경기가 있으면 dict, 없으면 None."""
    from game_engine import get_player, get_state
    p = get_player()
    st = get_state()
    if not p or not st:
        return None
    tid = p.get("current_team_id", 0)
    if not tid:
        return None
    t = _my_cup_tournament(p, st["current_year"])
    if not t or t["status"] == "done":
        return None
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != tid:
        return None

    conn = get_conn()
    m = conn.execute(
        """SELECT * FROM cup_matches
           WHERE tournament_id=? AND week=? AND home_score=-1
             AND (home_team_id=? OR away_team_id=?)""",
        (t["id"], week, tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    oe = conn.execute(
        "SELECT team_name, tier FROM cup_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], opp_id)).fetchone()
    conn.close()
    return {
        "cup": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "opp_tier": oe["tier"] if oe else None,
        "league_name": t["name"],
        "round_name": m["round_name"],
        "opp": oe["team_name"] if oe else "?",
        "is_home": is_home,
        "week": week,
    }


def start_domestic_cup(year, season):
    """[2026-07 전체 국가 확장] 5주차 진입 시 1회 호출 — 예전엔 '내 나라'
    한정으로만 컵대회를 만들었는데(성능 우려), 실제로는 process_cup_week()가
    이미 'status=active인 대회 전부'를 도는 구조라 진행 로직 자체는 처음부터
    전 세계를 감당할 수 있었다. 문제는 오직 '생성'을 내 나라만 했다는 것 —
    그래서 무소속 시즌엔 그 어떤 나라의 컵대회도 안 열려 세계 기록실에서
    전부 "기록 없음"으로 보이는 버그가 있었다(신민용 리포트).
    이제 리그가 하나라도 있는 나라 전부에 대해 대회를 개막한다. 이벤트
    로그(add_log)는 내 나라(또는 대표국적) 대회일 때만 남긴다 — 안 그러면
    관심 없는 나라 소식까지 매주 로그에 쌓인다."""
    from game_engine import get_player, add_log
    p = get_player()
    if not p:
        return
    my_cid = _my_country_id(p)

    conn = get_conn()
    # 리그가 하나라도 있는 나라만 (팀이 아예 없는 나라는 컵을 열 수 없음).
    country_ids = [r["cid"] for r in conn.execute(
        "SELECT DISTINCT country_id AS cid FROM leagues").fetchall()]
    # 이미 이번 연도에 대회가 생성된 나라는 건너뛴다(중복 방지).
    existing_cids = {r["country_id"] for r in conn.execute(
        "SELECT country_id FROM cup_tournaments WHERE year=?", (year,)).fetchall()}
    conn.close()

    for cid in country_ids:
        if cid in existing_cids:
            continue
        _start_domestic_cup_for_country(year, cid, my_cid, add_log)


def _start_domestic_cup_for_country(year, cid, my_cid, add_log):
    """한 나라의 컵대회 1개를 개막한다(대진 첫 라운드까지). start_domestic_cup()의
    국가별 반복 본체 — 예전 start_domestic_cup()의 내용을 그대로 country_id
    파라미터화한 것."""
    conn = get_conn()
    tiers = [r["tier"] for r in conn.execute(
        "SELECT DISTINCT tier FROM leagues WHERE country_id=? ORDER BY tier DESC",
        (cid,)).fetchall()]
    conn.close()
    if not tiers:
        return

    from game_engine import get_player
    p = get_player()
    my_tid = p.get("current_team_id", 0) if (p and cid == my_cid) else 0
    my_in = 1 if my_tid else 0
    cup_name = _cup_name_for_country(cid)

    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO cup_tournaments(year, country_id, name, status,
                 total_rounds, round_counter, pending_tiers, my_in, my_team_id)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
              (year, cid, cup_name, "active", len(tiers), 0,
               ",".join(str(x) for x in tiers), my_in, my_tid))
    conn.commit(); conn.close()

    if cid == my_cid:
        add_log(f"🏆 {year}년 {cup_name} 개막 (참가 리그 {len(tiers)}부까지)", "event")
    t = get_cup_tournament(year, cid)
    _start_next_round(t)


def _pop_next_tier(t):
    """pending_tiers에서 다음 합류 티어를 꺼내고 DB에서 제거."""
    pt = t.get("pending_tiers") or ""
    if not pt:
        return None
    parts = [x for x in pt.split(",") if x]
    if not parts:
        return None
    next_tier = int(parts[0])
    rest = ",".join(parts[1:])
    conn = get_conn()
    conn.execute("UPDATE cup_tournaments SET pending_tiers=? WHERE id=?", (rest, t["id"]))
    conn.commit()
    conn.close()
    return next_tier


def _tier_teams(country_id, tier):
    from game_engine import _team_avg_ovr
    conn = get_conn(); c = conn.cursor()
    rows = c.execute(
        """SELECT t.id AS tid, t.name AS tname FROM teams t JOIN leagues l ON t.league_id=l.id
           WHERE l.country_id=? AND l.tier=?""", (country_id, tier)).fetchall()
    out = [(r["tid"], r["tname"], _team_avg_ovr(c, r["tid"])) for r in rows]
    conn.close()
    return out


def _start_next_round(t):
    """생존 풀(alive=1) + 다음 합류 티어를 합쳐 한 라운드를 만든다.
    합칠 티어가 더 없으면 생존 풀만으로 진행(순수 토너먼트 단계)."""
    from game_engine import add_log, get_player
    tid = t["id"]
    # [2026-07 전체 국가 확장] 이제 이 함수가 모든 나라 컵대회에서 매주 여러 번
    # 호출되므로, 이벤트 로그는 내 나라(또는 대표국적) 대회일 때만 남긴다.
    _is_mine = (t["country_id"] == _my_country_id(get_player() or {}))
    conn = get_conn()
    survivors = [dict(r) for r in conn.execute(
        "SELECT team_id, team_name FROM cup_entries WHERE tournament_id=? AND alive=1",
        (tid,)).fetchall()]
    conn.close()

    next_tier = _pop_next_tier(t)
    pool = [(s["team_id"], s["team_name"], 0.0) for s in survivors]
    if next_tier is not None:
        new_teams = _tier_teams(t["country_id"], next_tier)
        conn = get_conn(); c = conn.cursor()
        if new_teams:
            c.executemany("""INSERT INTO cup_entries(tournament_id, team_id, team_name, tier, ovr)
                         VALUES(?,?,?,?,?)""",
                          [(tid, team_id, team_name, next_tier, ovr) for team_id, team_name, ovr in new_teams])
        conn.commit(); conn.close()
        pool = pool + [(x[0], x[1], x[2]) for x in new_teams]

    if len(pool) < 2:
        if len(pool) == 1:
            _finish_tournament(t, pool[0][0])
        return

    pool_entering = len(pool)   # 이 라운드에 '참가하는' 팀 수 (라운드 이름 기준 — 예: 16강=16팀 참가)
    random.shuffle(pool)
    bye = None
    if len(pool) % 2 == 1:
        bye = pool.pop()

    conn = get_conn()
    p_row = conn.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
    my_tid = p_row["current_team_id"] if p_row else 0

    round_counter = t["round_counter"]
    # [버그 수정] '결승'은 2팀이 붙어서 1팀이 남는 라운드인데, 예전엔 이
    # 라운드가 끝난 뒤 '남는 팀 수'로 이름을 붙여서 4팀이 붙는 라운드가
    # '결승'으로, 진짜 결승(2팀)은 이름 없는 'N라운드'로 밀려나는 오류가
    # 있었다. 실제 관례대로 '이 라운드에 들어오는 팀 수' 기준으로 고쳤다
    # (16강=16팀 참가, 결승=2팀 참가).
    rname = _round_name(pool_entering, round_counter)
    # [버그수정 2026-07, 신민용 리포트] CUP_ROUND_WEEKS_POOL은 10칸뿐인데,
    # 팀 수가 아주 많은 나라(프랑스·이탈리아·스페인·브라질·독일·잉글랜드 등,
    # 하위 리그까지 다 합치면 팀이 훨씬 많아 라운드가 10개를 넘게 필요함)는
    # round_counter가 9를 넘어서면 예전 코드(min으로 마지막 칸 고정)가 그 뒤
    # 모든 라운드를 전부 "42주차"에 몰아넣었다. 그러면 한 주차에 서로 다른
    # 라운드(예: '10라운드'와 '결승')가 겹치고, 이미 끝난 라운드를 처리하며
    # "다음 라운드로 진행"이 또 호출돼 결승이 계속 복제되는 무한루프가
    # 생겼다(실측: round_idx 10~30이 전부 '결승'으로 중복 생성, 대회가
    # 영원히 안 끝남). 풀을 넘어서면 마지막 주차부터 1주씩 이어 붙여서
    # 절대 같은 주차에 겹치지 않게 한다(52주 상한은 유지).
    if round_counter < len(CUP_ROUND_WEEKS_POOL):
        week = CUP_ROUND_WEEKS_POOL[round_counter]
    else:
        extra = round_counter - (len(CUP_ROUND_WEEKS_POOL) - 1)
        week = min(52, CUP_ROUND_WEEKS_POOL[-1] + extra)

    c = conn.cursor()
    _match_rows = []
    for slot in range(0, len(pool), 2):
        home, away = pool[slot], pool[slot + 1]
        is_my = 1 if my_tid in (home[0], away[0]) else 0
        _match_rows.append((tid, rname, round_counter, week, home[0], away[0], is_my, slot // 2,
                            pool_entering))
    if _match_rows:
        c.executemany("""INSERT INTO cup_matches
                     (tournament_id, round_name, round_idx, week,
                      home_team_id, away_team_id, is_my, slot, pool_entering)
                     VALUES(?,?,?,?,?,?,?,?,?)""", _match_rows)
    if bye:
        if _is_mine:
            add_log(f"🏆 {t['name']} {rname}: {bye[1]} 부전승", "event")
    conn.execute("UPDATE cup_tournaments SET round_counter=? WHERE id=?",
                 (round_counter + 1, tid))
    conn.commit()
    conn.close()
    if _is_mine:
        add_log(f"🏆 {t['name']} {rname} 대진 확정 ({len(pool)}팀 + 부전승 {1 if bye else 0}팀)", "event")


def _entry(tid, team_id):
    conn = get_conn()
    r = conn.execute("SELECT * FROM cup_entries WHERE tournament_id=? AND team_id=?",
                     (tid, team_id)).fetchone()
    conn.close()
    return dict(r) if r else {"team_name": "?", "ovr": 60}


def _match_outcome(h_ovr, a_ovr):
    """[2026-07 재조정, 신민용 지적: "컵대회 우승팀이 리그에서는 10등,
    챔스 우승팀이 리그 하위권인 게 이상하다"] 이 함수가 리그(_match_win_probs)/
    국제대회(intl_engine._match_outcome)와 똑같은 예전 완만한 공식(계수
    0.014, 캡 0.85)에 그대로 머물러 있었다 — 리그는 38~58경기라 표본이
    커서 진짜 실력 순으로 수렴하는데, 컵대회는 토너먼트 몇 경기뿐이라
    이변 확률이 낮아야 결과가 리그 순위와 크게 어긋나지 않는다. 오히려
    거꾸로 컵대회 쪽이 리그보다 더 완만한(이변이 잦은) 공식을 쓰고
    있었으니, 몇 경기 안 되는 토너먼트에서 실제 순위와 동떨어진 결과가
    누적되기 쉬웠다. 리그/국제대회와 동일한 기울기로 통일한다."""
    diff = h_ovr - a_ovr
    hw = max(0.04, min(0.95, 0.46 + diff * 0.022))
    dw = max(0.05, 0.24 - abs(diff) * 0.009)
    aw = max(0.02, 1.0 - hw - dw)
    tot = hw + dw + aw
    hw, dw, aw = hw / tot, dw / tot, aw / tot
    roll = random.random()
    if roll < hw:
        return "home"
    elif roll < hw + dw:
        return "draw"
    return "away"


def _resolve_pso(h_ovr, a_ovr):
    p_home = 0.5 + max(-0.1, min(0.1, (h_ovr - a_ovr) * 0.006))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


def _sim_ai_match(t, m, conn=None, reason="injury", batch=None):
    """AI끼리(또는 내가 결장한 내 경기) 시뮬.
    reason: 내 경기(m['is_my'])인데 내가 결장한 사유 — 'injury'(부상) 등.
    향후 다른 결장 사유가 생기면 호출부에서 이 값만 바꿔 넘기면 된다.

    batch: [2026-07 성능 최적화] 리스트를 넘기면 UPDATE를 즉시 실행하지
    않고 이 리스트에 튜플만 쌓아둔다 — 호출부(_process_one)가 한 라운드
    분량을 다 모은 뒤 executemany()로 한 번에 반영한다("1주 진행" 클릭 시
    컵대회 경기가 많은 라운드일수록 개별 execute() 호출이 누적되던 비용을
    줄인다 — game_engine._sim_all_ai_matches의 배치 패턴과 동일)."""
    from game_engine import add_log, get_player, _gen_score, _week_intl_cl_day
    he = _entry(t["id"], m["home_team_id"])
    ae = _entry(t["id"], m["away_team_id"])
    outcome = _match_outcome(he["ovr"], ae["ovr"])
    pso_winner, pso_score = 0, ""
    if outcome == "draw":
        win_home, pso_score = _resolve_pso(he["ovr"], ae["ovr"])
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, he["ovr"] - ae["ovr"])

    # [2026-07 신설] 실제 진행 날짜 저장 (커리어/은퇴창 표시용).
    # [2026-07 성능 수정] 이 값은 get_my_cup_matches()가 my_played=1인
    # 행만 읽으므로, 나(m["is_my"])와 무관한 AI vs AI 경기에서는 계산해도
    # 아무도 읽지 않는다 — 한 라운드에 수백~수천 건인 AI 경기마다 매번
    # get_player()(DB 조회)를 호출하던 걸 없앤다(_week_intl_cl_day 자체도
    # 이제 캐시되지만, 애초에 호출 자체가 불필요했다).
    day = _week_intl_cl_day(m["week"], get_player() or {}) if m["is_my"] else 0

    _absence = reason if m["is_my"] else None
    _row = (hs, as_, pso_winner, pso_score, day, _absence, m["id"])
    if batch is not None:
        batch.append(_row)
    else:
        _own = conn is None
        if _own:
            conn = get_conn()
        conn.execute("""UPDATE cup_matches SET home_score=?, away_score=?,
                        pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
                     _row)
        if _own:
            conn.commit()
            conn.close()

    if m["is_my"]:
        p = get_player()
        my_tid = p.get("current_team_id", 0) if p else 0
        if my_tid in (m["home_team_id"], m["away_team_id"]):
            pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
            add_log(f"🏆 {t['name']} {m['round_name']}  "
                    f"{he['team_name']} {hs}-{as_} {ae['team_name']}{pso_txt}", "match")
            _reason_ko = {"injury": "부상", "suspension": "출전정지", "bench": "벤치"}.get(reason, reason)
            add_log(f"   🚑 {_reason_ko}(으)로 컵대회 경기 결장", "match")


def _winner_of(m):
    if m["pso_winner"]:
        return m["pso_winner"]
    return m["home_team_id"] if m["home_score"] > m["away_score"] else m["away_team_id"]


def sim_my_cup_match_as_ai(week, p, reason="injury"):
    """[2026-07 신설, 버그수정] 부상 등으로 내가 못 뛸 때 내 컵대회 경기를
    AI끼리(내 보너스 없이) 시뮬레이션 — 이게 없으면 그 경기가 영원히
    home_score=-1(미완료)로 남아 대회 전체 진행이 멈춘다(신민용 리포트:
    "10월인데 1월 경기가 계속 '예정'으로 남아있다"). simulate_my_cup_match와
    동일하게 정보를 조회한 뒤 _sim_ai_match로 넘긴다."""
    info = get_my_cup_match(week)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM cup_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM cup_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()
    if m["home_score"] != -1:
        return  # 이미 처리됨(멱등)
    _sim_ai_match(t, m, reason=reason)


def simulate_my_cup_match(week, p, day=None):
    """내가 출전하는 컵대회 경기."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _roll_red_card, _apply_red_card_dismissal)
    from constants import PERSONALITY_EFFECTS
    info = get_my_cup_match(week)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM cup_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM cup_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    he = _entry(t["id"], m["home_team_id"])
    ae = _entry(t["id"], m["away_team_id"])
    is_home = info["is_home"]

    # [2026-07 신설] 출전정지 체크 — 퇴장 다음 경기는 강제 결장(개인 캐리
    # 보너스·개인 스탯 전부 0), 팀은 나 없이 시뮬레이션된다.
    _suspended, _new_susp = _check_suspended(p, field="cup_suspension")
    if _suspended:
        update_player(cup_suspension=_new_susp)
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event")

    # [2026-07 통일] 리그(game_engine._simulate_match)와 동일한 볼록가속+
    # 소프트캡 공식으로 교체 — 예전 선형+하드컷(14.0)보다 월드클래스급
    # 선수의 캐리력이 정확히 반영된다.
    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = max(0.0, _my_ovr - _team_ovr)
    _star = 1.0 + max(0.0, (_my_ovr - 60) / 40.0) ** 1.8 * 3.0
    bonus = _gap * 0.30 * _star + max(0.0, _my_ovr - 50) * 0.08
    bonus = _soft_cap(bonus, 30.0)
    # [2026-07 신설] '리더십' 성격의 team_win_bonus — 정의만 돼있고 실제
    # 경기엔 연결이 안 돼있던 효과. 캐리 보너스에 아주 작은 배율만 얹어서
    # "주장감 선수가 팀을 살짝 더 끌어올린다" 정도로만 반영한다.
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if "team_win_bonus" in _pe:
        bonus *= (1.0 + _pe["team_win_bonus"])
    if _suspended:
        bonus = 0.0
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    outcome = _match_outcome(h_ovr, a_ovr)
    pso_winner, pso_score = 0, ""
    if outcome == "draw":
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    if _suspended:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
        _absence_reason = "suspension"
    else:
        # [2026-07 통일] intl_engine(국제대회)과 동일하게 "오늘 상대의 실제 팀
        # OVR"을 dom 기준으로 넘긴다 — 강팀 상대면 개인도 고전, 약체 상대면
        # 골·평점이 폭발하도록. he/ae는 보너스 반영 전 원본 팀 OVR이다.
        _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr)
        _absence_reason = None
        # [2026-07 신설] 퇴장 판정 — '폭력적' 성격의 red_card_chance 반영.
        if _roll_red_card(p):
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(p, field="cup_suspension")
            _absence_reason = "red_card"
    # [2026-07 신설] '겁쟁이' 성격의 cup_rating(컵대회 전반 위축) +
    # '소심함'의 big_match_rating(결승전 한정 위축) 연결. 둘 다 정의만
    # 돼있고 실제 경기엔 연결이 안 돼있던 효과였다.
    if not _suspended and "cup_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["cup_rating"], 1)))
    if m.get("round_name") == "결승" and not _suspended and "big_match_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["big_match_rating"], 1)))
    my_result = _my_result(outcome, is_home)

    # [2026-07 신설] 실제 진행 날짜 저장 (커리어/은퇴창 표시용).
    #   day 인자가 없으면(하위 호환) 지금 시점 기준으로 계산해 폴백.
    if day is None:
        from game_engine import _week_intl_cl_day
        day = _week_intl_cl_day(week, p)

    conn = get_conn()
    conn.execute("""UPDATE cup_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?, my_played=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?, day=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?,
                    my_absence_reason=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score, 0 if _suspended else 1,
                  saves, goals, assists, rating, day,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"],
                  _absence_reason, m["id"]))
    conn.commit()
    conn.close()

    _update_pop(p, goals, assists, rating)
    p2 = get_player()
    # [2026-07 조정, 신민용 지적: "경기 스트레스가 고강도 훈련만큼은 돼야
    # 하지 않나"] 리그 경기와 동일 원칙 — 고강도 훈련(20)과 최소 동급으로
    # 올림. 컵대회는 홈/원정·나이 구분 없이 단일 값을 쓰는 기존 구조는
    # 유지하고 크기만 리그 스케일에 맞췄다.
    ns = min(100, p2["stress"] + 20)
    nh = p2["happiness"]
    if my_result == "win":
        nh = min(100, nh + 4)
    elif my_result == "loss":
        nh = max(0, nh - 4)
    update_player(stress=ns, happiness=nh)

    rs = {"win": "승", "draw": "무", "loss": "패"}.get(my_result, "")
    pso_txt = ""
    my_tid = p.get("current_team_id", 0)
    if pso_winner:
        pso_txt = f"  (승부차기 {pso_score} {'승' if pso_winner == my_tid else '패'})"
        rs = "무"

    comp_name = f"{t['name']} {m['round_name']}".strip()
    home_disp = he["team_name"]
    away_disp = ae["team_name"]
    pso = {"won": pso_winner == my_tid, "score": pso_score} if pso_winner else None
    detail_id = _save_match_detail(
        p, week, comp_name, is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, True, False, detail, pso=pso)
    marker = f" [match:{detail_id}]" if detail_id else ""

    add_log("─" * 44, "sep")
    from game_engine import _day_label
    add_log(f"🏆 {comp_name}  {_day_label(week, day)}{marker}", "match")
    add_log(f"   {home_disp} {hs}-{as_} {away_disp}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")


def process_cup_week(week):
    """이번 주차에 진행 중인 모든 컵대회를 확인해 라운드 종료/다음 라운드 생성."""
    conn = get_conn()
    ts = [dict(r) for r in conn.execute(
        "SELECT * FROM cup_tournaments WHERE status='active'").fetchall()]
    conn.close()
    for t in ts:
        _process_one(t, week)


def _process_one(t, week):
    # [2026-07 3/4위전 추가] 결승과 3/4위전이 같은 주차에 동시에 열리므로,
    # 예전처럼 그 주차의 '아무 경기 1건'으로 라운드를 판별하면(LIMIT 1)
    # 둘 중 하나를 놓친다. 이 주차에 존재하는 라운드명을 전부 모아 각각
    # 별도로 완료 여부를 확인·처리한다.
    conn = get_conn()
    round_names = [r["round_name"] for r in conn.execute(
        "SELECT DISTINCT round_name FROM cup_matches WHERE tournament_id=? AND week=?",
        (t["id"], week)).fetchall()]
    conn.close()
    if not round_names:
        return

    # 남은 AI끼리 경기를 채운다(내 경기는 이미 그 주 안에 별도로 처리됨).
    # [2026-07 성능 최적화] 예전엔 경기마다 conn.execute()를 개별 호출했다
    # ("1주 진행" 시 컵대회 라운드가 큰 주차일수록 체감 지연의 한 원인).
    # 이제 game_engine._sim_all_ai_matches와 동일하게 batch 리스트에 모아
    # executemany()로 한 번에 반영한다 — 결과(어느 경기가 몇 대 몇으로
    # 끝나는지)는 완전히 동일하고, DB 반영 방식만 배치로 바뀐다.
    conn2 = get_conn()
    pending = [dict(r) for r in conn2.execute(
        "SELECT * FROM cup_matches WHERE tournament_id=? AND week=? AND home_score=-1 AND is_my=0",
        (t["id"], week)).fetchall()]
    _batch = []
    for m in pending:
        _sim_ai_match(t, m, batch=_batch)
    if _batch:
        conn2.executemany(
            """UPDATE cup_matches SET home_score=?, away_score=?,
               pso_winner=?, pso_score=?, day=?, my_absence_reason=? WHERE id=?""",
            _batch)
    conn2.commit()
    conn2.close()

    for rname in round_names:
        _advance_round(t, rname, week)


def _advance_round(t, round_name, week):
    """한 라운드(round_name, week 조합 — 결승/3·4위전처럼 같은 주차에 여러
    라운드명이 동시에 있을 수 있다)가 이번 주차에 전부 끝났는지 확인하고,
    끝났으면 탈락 처리 + 다음 단계로 진행시킨다."""
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        "SELECT * FROM cup_matches WHERE tournament_id=? AND week=? AND round_name=? ORDER BY slot",
        (tid, week, round_name)).fetchall()]
    conn.close()
    if not cur or any(m["home_score"] == -1 for m in cur):
        return  # 이 라운드는 아직 없거나 미완료

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0

    is_final = (round_name == "결승")
    is_tp    = (round_name == "3·4위전")
    # [2026-07 버그 수정] 예전엔 round_name=='4강'(정확히 4팀)일 때만 3/4위전을
    # 만들어서, 부전승 등으로 이 라운드에 3팀·5팀이 들어와 이름이 "3라운드"/
    # "5라운드"가 되면(그래도 결승 진출자 2명을 정하는 라운드인 건 똑같은데)
    # 3/4위전 자체가 안 생겨 세계 기록실에 3·4위가 통째로 비었다(신민용 리포트:
    # "같은 컵대회인데 1·2위만 뜨는 경우가 있다"). 라운드 이름이 아니라 그
    # 라운드에 실제로 들어온 팀 수(pool_entering, 부전승 포함)로 "이 라운드
    # 승자가 곧 결승 진출자 2명인지"를 구조적으로 판별한다.
    pool_entering = (cur[0].get("pool_entering") or 0) if cur else 0
    winners_next = (pool_entering + 1) // 2  # 부전승 있으면 홀수도 정확히 반올림
    is_sf = (not is_final and not is_tp and pool_entering > 0 and winners_next == 2)

    conn = get_conn(); c = conn.cursor()
    sf_losers = []
    _loser_updates = []  # [2026-07 최적화] 패자 UPDATE를 모았다가 executemany로 일괄 반영
    for m in cur:
        w = _winner_of(m)
        loser = m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"]
        if is_sf:
            # 4강 패자는 3/4위전을 뛰므로, 이번 라운드에서는 alive를 건드리지
            # 않고 탈락 기록도 미룬다(3/4위전 결과가 진짜 최종 성적이다).
            sf_losers.append(loser)
            continue
        if my_tid and loser == my_tid and not is_tp:
            # 내 팀이 탈락하는 희귀 케이스만 기존처럼 그 자리에서 즉시 처리
            # (커밋 순서가 _record_my_exit 호출 전에 반드시 끝나야 하므로).
            if _loser_updates:
                c.executemany("UPDATE cup_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                              _loser_updates)
                _loser_updates = []
            c.execute("UPDATE cup_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                      (tid, loser))
            exit_label = "준우승" if is_final else round_name
            conn.commit(); conn.close()
            _record_my_exit(t, exit_label, _teams_remaining_at(tid))
            conn = get_conn(); c = conn.cursor()
        else:
            _loser_updates.append((tid, loser))
    if _loser_updates:
        c.executemany("UPDATE cup_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                      _loser_updates)
    conn.commit(); conn.close()

    if is_sf:
        conn = get_conn(); c = conn.cursor()
        for lid in sf_losers:
            c.execute("UPDATE cup_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                      (tid, lid))
        conn.commit(); conn.close()

        # 4강 승자로 결승 대진을 먼저 만든다 (기존 흐름 그대로).
        t2 = get_cup_tournament(t["year"], t["country_id"])
        if t2 and t2["status"] == "active":
            _start_next_round(t2)

        # 4강 패자 2팀으로 3/4위전 생성 (결승과 같은 주차).
        if len(sf_losers) == 2:
            conn = get_conn()
            fm = conn.execute(
                """SELECT week, round_idx FROM cup_matches
                   WHERE tournament_id=? AND round_name='결승' ORDER BY id DESC LIMIT 1""",
                (tid,)).fetchone()
            conn.close()
            if fm:
                tp_home, tp_away = sf_losers[0], sf_losers[1]
                is_my_tp = 1 if my_tid in (tp_home, tp_away) else 0
                conn = get_conn(); c = conn.cursor()
                c.execute("""INSERT INTO cup_matches
                             (tournament_id, round_name, round_idx, week,
                              home_team_id, away_team_id, is_my, slot)
                             VALUES(?,?,?,?,?,?,?,999)""",
                          (tid, "3·4위전", fm["round_idx"], fm["week"],
                           tp_home, tp_away, is_my_tp))
                conn.commit(); conn.close()
                he = _entry(tid, tp_home); ae = _entry(tid, tp_away)
                # [2026-07 전체 국가 확장] 이제 컵대회가 모든 나라에서 열리므로,
                # 이 로그는 '내 나라(또는 내 대표국적)' 대회일 때만 남긴다 —
                # 안 그러면 관심 없는 나라 소식까지 매주 이벤트 로그에 다 쌓인다.
                if t["country_id"] == _my_country_id(get_player() or {}):
                    add_log(f"🥉 {t['name']} 3/4위전: {he['team_name']} vs {ae['team_name']}", "event")
        return

    if is_tp:
        winner = _winner_of(cur[0])
        loser  = cur[0]["away_team_id"] if winner == cur[0]["home_team_id"] else cur[0]["home_team_id"]
        if my_tid in (winner, loser):
            result_label = "3위" if my_tid == winner else "4위"
            _record_my_exit(t, result_label, 4)
        # 결승도 끝났으면 같이 대회를 종료한다.
        conn = get_conn()
        f_row = conn.execute(
            """SELECT * FROM cup_matches WHERE tournament_id=? AND round_name='결승'
               ORDER BY id DESC LIMIT 1""", (tid,)).fetchone()
        conn.close()
        if f_row and f_row["home_score"] != -1:
            _finish_tournament(t, _winner_of(dict(f_row)))
        return

    if is_final:
        conn = get_conn()
        tp_remain = conn.execute(
            """SELECT COUNT(*) AS n FROM cup_matches
               WHERE tournament_id=? AND round_name='3·4위전' AND home_score=-1""",
            (tid,)).fetchone()["n"]
        conn.close()
        if tp_remain == 0:   # 3/4위전이 없거나 이미 끝났으면 바로 종료
            w = _winner_of(cur[0])
            _finish_tournament(t, w)
        # tp_remain > 0 이면 3/4위전이 끝날 때 다시 이 함수가 호출되어 종료됨.
        return

    # 일반 라운드: 다음 라운드로 진행.
    t2 = get_cup_tournament(t["year"], t["country_id"])
    if t2 and t2["status"] == "active":
        _start_next_round(t2)


def _teams_remaining_at(tournament_id):
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM cup_entries WHERE tournament_id=? AND alive=1",
        (tournament_id,)).fetchone()["n"]
    conn.close()
    return n


def _finish_tournament(t, winner_id):
    from game_engine import add_log, get_player
    tid = t["id"]
    conn = get_conn()
    # [2026-07 3/4위전 추가] 결승/3·4위전이 같은 주차에 있어서 두 라운드가
    # 각자 "상대 라운드도 끝났으면 종료" 체크를 하다 보면 이 함수가 두 번
    # 불릴 수 있다 — 이미 끝난 대회면 보상 중복 지급을 막기 위해 바로 반환.
    already = conn.execute("SELECT status FROM cup_tournaments WHERE id=?", (tid,)).fetchone()
    if already and already["status"] == "done":
        conn.close()
        return
    conn.execute("UPDATE cup_tournaments SET status='done', winner_team_id=? WHERE id=?",
                 (winner_id, tid))
    conn.commit()
    conn.close()

    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    we = _entry(tid, winner_id)
    # [2026-07 전체 국가 확장] 마찬가지로 내 나라(또는 대표국적) 대회일 때만
    # 이벤트 로그에 남긴다. cup_history/trophy_log 등 기록 자체는 나라와
    # 무관하게 항상 남으니(세계 기록실 조회용) 여기서 로그만 걸러낸다.
    if t["country_id"] == _my_country_id(p or {}):
        add_log(f"🏆 {t['year']}년 {t['name']} 우승: {we['team_name']}", "event")
    if my_tid == winner_id:
        _record_my_exit(t, "우승", 1)


def _reward_for(result, n_remaining):
    if result == "우승":
        return (10, 8, 10)
    if result == "준우승":
        return (6, 4, 5)
    for cap, reward in _CUP_REWARD_BY_TEAMS:
        if n_remaining <= cap:
            return reward
    return (0, 0, 0)


def _record_my_exit(t, result, n_remaining):
    from game_engine import add_log, get_player, update_player
    p = get_player()
    if not p:
        return
    my_tid = p.get("current_team_id", 0)

    conn = get_conn()
    conn.execute("UPDATE cup_tournaments SET my_result=? WHERE id=?", (result, t["id"]))
    te = conn.execute(
        "SELECT team_name FROM cup_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], my_tid)).fetchone()
    conn.commit()
    conn.close()
    team_name = te["team_name"] if te else ""

    _save_trophy(t["year"], team_name, result, t["name"])

    conn = get_conn()
    agg = conn.execute(
        """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM cup_matches WHERE tournament_id=? AND my_played=1""", (t["id"],)).fetchone()
    exists = conn.execute(
        "SELECT id FROM cup_history WHERE year=? AND team_name=?",
        (t["year"], team_name)).fetchone()
    if not exists:
        conn.execute("""INSERT INTO cup_history(year, team_name, result,
                                                goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?)""",
                     (t["year"], team_name, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()

    fame_g, pop_g, hap_g = _reward_for(result, n_remaining)
    update_player(
        fame=min(100, p.get("fame", 0) + fame_g),
        popularity=min(100, p.get("popularity", 0) + pop_g),
        happiness=max(0, min(100, p.get("happiness", 50) + hap_g)),
    )
    icon = "🏆" if result == "우승" else "🏅"
    add_log(f"{icon} {t['year']}년 {t['name']} 최종 성적: {result}  "
            f"(명성 +{fame_g}, 인기 +{pop_g})", "event")


def _save_trophy(year, team_name, result, competition="컵대회"):
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=? AND team_name=?",
        (year, competition, team_name)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,-2,?)""", (year, team_name, result, competition))
        conn.commit()
    conn.close()