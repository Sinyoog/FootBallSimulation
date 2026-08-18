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

# [2026-07 수정, 신민용 리포트: "컵대회는 비시즌에 낄 때 있네"] 중간 휴식기
# (INTL_QUAL_WEEK~휴식기 끝 주차, 28~31주) 도입으로 위 풀의 30이 그 안에
# 걸려버렸다 — 클럽 경기가 없는 주간에 컵 라운드가 잡히는 버그. 휴식기에
# 걸리는 주차는 자동으로 휴식기 끝난 바로 다음 주로 민다(하드코딩 숫자를
# 또 손으로 맞추는 대신, 상수 기준으로 자동 보정해서 나중에 휴식기 길이가
# 또 바뀌어도 여기를 다시 안 고쳐도 되게 함).
from constants import INTL_QUAL_WEEK, WINTER_OFFER_END_DAY, day_to_week as _day_to_week
_CUP_BREAK_END_WEEK = _day_to_week(WINTER_OFFER_END_DAY)
CUP_ROUND_WEEKS_POOL = [
    (_CUP_BREAK_END_WEEK + 1) if (INTL_QUAL_WEEK <= _w <= _CUP_BREAK_END_WEEK) else _w
    for _w in CUP_ROUND_WEEKS_POOL
]


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

# [2026-08 신설, 신민용 요청] 국내컵 참가 범위 — "그 나라에 리그가 몇 부까지
# 존재하느냐"와 "컵대회에 몇 부까지 참가시키느냐"는 별개 축이다. 예전엔
# _start_domestic_cup_for_country가 그 나라에 존재하는 티어 전부를 무조건
# 컵에 합류시켰는데(예: 7부리그까지 생기면 7부도 자동으로 컵에 낌), 실제
# 축구는 나라마다 컵의 개방성이 다르다 — 잉글랜드 FA컵은 비리그 팀까지
# 열려있는 반면(사실상 최상위 몇 부는 사실상 무제한), 이탈리아 코파
# 이탈리아는 3부 정도까지로 좁다. CUP_NAME_BY_COUNTRY와 동일하게 국가명을
# 키로 쓴다 — 딕셔너리에 없는 나라는 DEFAULT_CUP_MAX_TIER를 적용하고,
# 그 나라에 실제로 존재하는 티어가 상한보다 적으면(예: 3부까지만 있는데
# 상한이 4부) 있는 만큼만 자연히 참가한다(별도 처리 불필요).
# [2026-08 재설계, 신민용 확정: "6부는 컵 대회도 나가지 못하는거고 5부부터가
# 시작"] 이건 나라별 커스터마이징(잉글랜드=넓게, 이탈리아=좁게)과는 별개로
# 절대 넘을 수 없는 전역 상한이다 — CUP_MAX_TIER_BY_COUNTRY에 5보다 큰 값이
# 있어도(예전 잉글랜드=10) 5로 잘린다. _cup_max_tier_for_country()에서
# min(5, ...)로 강제한다.
DEFAULT_CUP_MAX_TIER = 5
CUP_ABSOLUTE_MAX_TIER = 5   # 6부 이상은 어느 나라든 컵 자체에 못 나감

CUP_MAX_TIER_BY_COUNTRY = {
    "잉글랜드": 10,   # FA컵 — 실제론 훨씬 개방적이지만 CUP_ABSOLUTE_MAX_TIER(5)로 잘림
    "스페인": 4,      # 코파 델 레이 — 1ª/2ª RFEF(3~4부)까지
    "독일": 4,        # DFB-포칼 — 3부(리가)+지역컵 선발 4부팀까지, 전체 개방은 아님
    "이탈리아": 3,    # 코파 이탈리아 — 세리에 A/B/C 일부까지로 제한적
}


def _cup_max_tier_for_country(country_id):
    """이 나라 컵대회가 참가시키는 최대 하부리그 티어(그 이하 부수는 컵에
    합류하지 않음). CUP_MAX_TIER_BY_COUNTRY에 없으면 DEFAULT_CUP_MAX_TIER,
    있어도 CUP_ABSOLUTE_MAX_TIER(5)를 절대 못 넘는다."""
    conn = get_conn()
    row = conn.execute("SELECT name FROM countries WHERE id=?", (country_id,)).fetchone()
    conn.close()
    cname = row["name"] if row else ""
    return min(CUP_ABSOLUTE_MAX_TIER,
               CUP_MAX_TIER_BY_COUNTRY.get(cname, DEFAULT_CUP_MAX_TIER))


def _cup_name_for_country(country_id):
    conn = get_conn()
    row = conn.execute("SELECT name FROM countries WHERE id=?", (country_id,)).fetchone()
    conn.close()
    cname = row["name"] if row else ""
    return CUP_NAME_BY_COUNTRY.get(cname, f"{cname}컵" if cname else "컵대회")


def _round_name(n_teams: int, round_counter: int, is_pure_ko: bool = True) -> str:
    """[2026-08 버그수정, 신민용 리포트: "최하위 리그가 딱 16팀이면 1라운드가
    16강으로 잘못 뜬다"] 예전엔 참가 팀 수만 보고 이름을 붙여서, 아직 하위
    부수가 계속 합류 중인 예선 단계인데도 우연히 그 라운드 참가 팀이
    16/32/64 등과 맞아떨어지면 "16강" 같은 본선 이름이 붙었다 — 진짜
    본선(더 이상 새 부수가 합류하지 않는 단계, is_pure_ko=True)일 때만
    표준 강수 이름을 쓰고, 그 전(예선 단계)엔 숫자가 뭐든 무조건
    "N라운드"로 표시한다."""
    if not is_pure_ko:
        return f"{round_counter + 1}라운드"
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
           -- [2026-07 재수정, 신민용 지적: "다친 게 아니라 그냥
           -- 벤치라 안 뛴 경기도 있는데 그건 빠진다"] my_played=1
           -- 이거나 absence_reason이 있는 것만 걸렀더니, "건강한데
           -- 로테이션으로 그냥 안 뛴" 경기(my_played=0이면서
           -- absence_reason도 NULL)가 통째로 빠졌다 — 그 팀 소속으로
           -- 치러진 경기는 전부 보여주고(결과가 난 것만), 뛰었는지
           -- 안 뛰었는지는 화면에서 my_played/absence_reason으로
           -- 구분한다.
           WHERE m.is_my = 1 AND m.home_score >= 0
           ORDER BY t.year, m.week""").fetchall()]

    # [2026-08 성능 수정, 신민용 리포트: "재능 좋은 선수로 오래 뛰면
    # 은퇴/커리어창이 심하게 렉걸린다"] 예전엔 경기 하나마다 _entry()를
    # 홈/원정 각각 따로 호출(N+1 쿼리)했다 — 컵대회를 많이 뛴 커리어일수록
    # 쿼리가 수백~수천 번씩 발생했다. 이 경기들이 걸쳐있는 tournament_id만
    # 모아 한 번의 IN 쿼리로 필요한 엔트리만 배치 조회한다(전체
    # cup_entries를 다 읽지 않는다 — 그건 세계 전체 컵대회 참가팀이라 훨씬
    # 크다).
    tids = {m["tournament_id"] for m in rows}
    entries = {}
    if tids:
        ph = ",".join("?" * len(tids))
        for r in conn.execute(
                f"SELECT * FROM cup_entries WHERE tournament_id IN ({ph})",
                tuple(tids)).fetchall():
            entries[(r["tournament_id"], r["team_id"])] = dict(r)
    conn.close()

    def _entry_lookup(tid, team_id):
        return entries.get((tid, team_id), {"team_name": "?", "ovr": 60})

    out = []

    for m in rows:
        # [2026-07 버그수정, champions_engine.get_my_cl_matches와 동일한
        # 버그 발견/수정] "현재" 소속팀 대신 cup_tournaments.my_team_id
        # (그 대회 시작 시점에 고정 저장된 내 팀)를 쓴다 — 안 그러면 그
        # 이후 이적한 경우 과거 경기의 상대가 그때의 내 팀 이름으로
        # 뒤바뀌어 표시되고 스코어/승패도 뒤집힌다.
        my_tid = m["t_my_tid"]
        he = _entry_lookup(m["tournament_id"], m["home_team_id"])
        ae = _entry_lookup(m["tournament_id"], m["away_team_id"])
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
            "my_played": m.get("my_played", 0),
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


def get_my_cup_match(week, day=None, p=None, st=None):
    """이번 주차(또는 특정 day)에 내가 뛸 컵대회 경기가 있으면 dict, 없으면 None.

    [2026-07 최적화] p를 넘기면 get_player() 재조회를 생략한다."""
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if st is None:
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
    if day is not None:
        m = conn.execute(
            """SELECT * FROM cup_matches
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?) AND (day=? OR day IS NULL OR day=0)""",
            (t["id"], week, tid, tid, day)).fetchone()
    else:
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
    파라미터화한 것.

    [2026-08 전면 재설계, 신민용 설계 확정: "국내컵 단계적 합류 구조가
    잘못됐다"] 예전엔 참가 티어 전부(예: 5,4,3,2,1)를 한 칸씩 순서대로
    pending_tiers에 넣고, 맨 처음 팝되는 티어(5부만 있는 라운드)를 그냥
    "1라운드"라고 불렀다 — 그러면 "5부부터 시작해서 4부가 1라운드에 합류"
    하는 게 아니라 "5부 자체가 1라운드"가 되어버려 실제 설계 의도보다
    한 칸씩 밀려 있었다. 이제:
      - 그 나라의 최하위 참가 티어가 5부면: 5부 단독 라운드를 "예선"이라는
        별도 이름으로 분리하고, 그 다음(4부 합류)부터 "1라운드"로 다시
        번호를 매긴다(cup_tournaments.has_qualifying=1로 표시).
      - 4부/3부/2부까지만 있는 나라는 예선 없이 그 최하위 티어 자체가
        "1라운드"다.
      - 1부는 한 라운드에 전원 합류하지 않는다 — 그 시즌 그 대륙
        챔피언스리그에 참가하지 않는 일반 1부 팀들이 먼저 합류하고
        ("1a"), 챔피언스리그 참가팀은 그 다음 라운드에 더 늦게 합류한다
        ("1b") — 실제 FA컵에서 유럽대항전 참가 빅클럽이 더 늦게 합류하는
        것과 같은 취지. pending_tiers 문자열에 정수가 아니라 "1a"/"1b"
        같은 태그를 넣어 표현하고, _pop_next_tier/_tier_teams가 이 태그를
        해석한다.
      - 5부~2부는 예전처럼 그 라운드에서 전원(존재하는 팀 전부) 합류하고,
        숫자가 대진에 안 맞아떨어질 때만(홀수 등) 부전승으로 처리한다 —
        "일부만 뽑아서 넣는" 별도 선발 규칙은 두지 않는다(신민용 확정).
    """
    conn = get_conn()
    tiers = [r["tier"] for r in conn.execute(
        "SELECT DISTINCT tier FROM leagues WHERE country_id=? ORDER BY tier DESC",
        (cid,)).fetchall()]
    conn.close()
    if not tiers:
        return

    # [2026-08] 이 나라 컵대회의 참가 상한 티어를 적용 — 그 나라에 리그가
    # 7부까지 있어도 컵은 4부까지만 참가하는 식으로, "존재하는 티어"와
    # "컵에 참가하는 티어"를 분리한다. CUP_ABSOLUTE_MAX_TIER(5)를 항상
    # 절대 상한으로 겸한다(_cup_max_tier_for_country 안에서 처리됨).
    _max_tier = _cup_max_tier_for_country(cid)
    tiers = [t for t in tiers if t <= _max_tier]
    if not tiers:
        return

    # tiers는 여기서 내림차순(예: [5,4,3,2,1]) — 가장 낮은 리그(숫자 큼)가
    # 맨 앞. 1부는 join_queue에서 빼고 맨 끝에 "1a","1b" 두 물결로 대체한다.
    has_qualifying = 1 if tiers[0] == 5 else 0   # 최하위 참가 티어가 5부일 때만 예선
    join_queue = [str(t) for t in tiers if t != 1]
    if 1 in tiers:
        join_queue += ["1a", "1b"]
    pending_tiers = ",".join(join_queue)

    # [2026-08 재조정, 신민용 리포트: "16강부터인거지... 근데 지금 니가
    # 만든건 64강부터 아니야?"] 예전 게임 설계 메모("코리아컵은 16강,
    # 독일/잉글랜드는 32강 또는 64강")를 그대로 물려받아 cap=64로 계산
    # 했었는데, 신민용이 확정한 정책은 그게 아니었다 — 참가 가능 팀
    # 총합이 몇 팀이든(16팀이든 96팀이든) 표준 강수는 16강이 상한이다:
    #   16팀 이상 → 16강 / 8~15팀 → 8강 / 4~7팀 → 4강 / 2~3팀 → 결승
    # 즉 "표준 강수"는 나라 규모에 따라 32강·64강으로 더 커지지 않고,
    # 16강이 유일한 '큰 쪽' 상한이다 — 총 참가팀이 아무리 많아도 그만큼
    # 앞단의 예선 라운드("N라운드")가 더 늘어날 뿐, 표준 강수 이름이
    # 붙는 본선 시작점 자체는 항상 16강(또는 그 밑, 팀이 적으면)이다.
    _total_entrants = sum(len(_tier_teams(cid, tok, year=year)) for tok in join_queue)
    _standard_bracket_size = _cup_target_bracket_size(_total_entrants, cap=CUP_STANDARD_BRACKET_CAP)

    from game_engine import get_player
    p = get_player()
    my_tid = p.get("current_team_id", 0) if (p and cid == my_cid) else 0
    my_in = 1 if my_tid else 0
    cup_name = _cup_name_for_country(cid)

    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO cup_tournaments(year, country_id, name, status,
                 total_rounds, round_counter, pending_tiers, has_qualifying,
                 my_in, my_team_id, standard_bracket_size)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (year, cid, cup_name, "active", len(join_queue), 0,
               pending_tiers, has_qualifying, my_in, my_tid, _standard_bracket_size))
    conn.commit(); conn.close()

    if cid == my_cid:
        add_log(f"🏆 {year}년 {cup_name} 개막 (참가 리그 {tiers[0]}부까지)", "event")
    t = get_cup_tournament(year, cid)
    _start_next_round(t, p=p)


def _pop_next_tier(t):
    """pending_tiers에서 다음 합류 티어를 꺼내고 DB에서 제거. 값은 "5"처럼
    순수 티어 숫자거나(5~2부, 그 티어 전원 합류), "1a"/"1b"처럼 1부를
    두 물결로 나눈 태그일 수 있다(_tier_teams가 해석) — 반환값은 그대로
    문자열 토큰이다(호출부에서 필요할 때 int로 변환).

    반환: (next_token, is_last) — is_last=True면 이 토큰이 pending_tiers의
    마지막 항목이었다는 뜻(=이 라운드가 끝나면 더 이상 합류할 티어가 없어
    다음 라운드부터 순수 토너먼트 단계가 시작된다). [2026-08 신설] 표준
    강수(16강 등) 수렴을 "마지막 합류 라운드" 시점에 앞당겨 시작하기 위해
    호출부가 미리 알아야 해서 추가했다."""
    pt = t.get("pending_tiers") or ""
    if not pt:
        return None, False
    parts = [x for x in pt.split(",") if x]
    if not parts:
        return None, False
    next_token = parts[0]
    rest = ",".join(parts[1:])
    conn = get_conn()
    conn.execute("UPDATE cup_tournaments SET pending_tiers=? WHERE id=?", (rest, t["id"]))
    conn.commit()
    conn.close()
    return next_token, (rest == "")


def _tier_teams(country_id, tier_token, year=None):
    """이 나라·이 티어(또는 1부 물결)에서 컵에 합류할 팀 목록을 반환한다.
    tier_token은 "5"~"2" 같은 순수 티어 숫자거나, "1a"(챔피언스리그
    비참가 1부)/"1b"(챔피언스리그 참가 1부) 태그다.

    [2026-08 신설, 신민용 설계 확정: "1부는 일부만 먼저 합류하고 빅클럽은
    더 늦게"] 이 시즌 그 대륙 챔피언스리그 출전권을 이미 받은 팀(cl_entries)
    은 "1b"로 분류해 한 라운드 더 늦게 합류시킨다 — 컵 초반에 하위리그
    팀이 챔스 나가는 빅클럽부터 만나는 비현실성을 줄이기 위함. 이 시점
    (컵 4라운드 전후, 시즌 20주차 이후)엔 챔스 출전팀이 이미 확정돼 있어
    (챔스 조 편성은 6~7주차) 안전하게 조회할 수 있다."""
    from game_engine import _team_avg_ovr
    conn = get_conn(); c = conn.cursor()

    if tier_token in ("1a", "1b"):
        rows = c.execute(
            """SELECT t.id AS tid, t.name AS tname FROM teams t JOIN leagues l ON t.league_id=l.id
               WHERE l.country_id=? AND l.tier=1""", (country_id,)).fetchall()
        cl_team_ids = set()
        if year is not None:
            cl_team_ids = {r["team_id"] for r in c.execute(
                """SELECT DISTINCT ce.team_id FROM cl_entries ce
                   JOIN cl_tournaments ct ON ce.tournament_id=ct.id
                   WHERE ct.year=?""", (year,)).fetchall()}
        want_cl = (tier_token == "1b")
        rows = [r for r in rows if (r["tid"] in cl_team_ids) == want_cl]
    else:
        tier = int(tier_token)
        rows = c.execute(
            """SELECT t.id AS tid, t.name AS tname FROM teams t JOIN leagues l ON t.league_id=l.id
               WHERE l.country_id=? AND l.tier=?""", (country_id, tier)).fetchall()

    out = [(r["tid"], r["tname"], _team_avg_ovr(c, r["tid"])) for r in rows]
    conn.close()
    return out


# [2026-08 신설, 신민용 설계 확정: "표준 강수는 16강이 상한 — 참가팀이
# 많아도 32강/64강으로 더 커지지 않는다"] cup_tournaments.standard_bracket_size
# 계산(_start_domestic_cup_for_country)에서 쓰는 상한. _cup_bye_count 자체는
# 범용 유틸이라 기본 cap=64를 그대로 두되(다른 용도로 재사용될 가능성 대비),
# 실제 대회 표준 강수를 정할 때는 이 상수를 명시적으로 cap으로 넘긴다.
CUP_STANDARD_BRACKET_CAP = 16


def _cup_target_bracket_size(n, cap=64):
    """[2026-08 신설] n(참가 가능 팀 수, 또는 지금 이 순간 남은 인원) 이하의
    가장 큰 2의 거듭제곱을 반환한다(cap 상한). _cup_bye_count의 핵심 계산을
    분리해서, "대회 시작 시점의 참가 가능 팀 총합"으로 표준 강수를 미리
    한 번 정할 때도(그 값을 이후 모든 라운드의 부전승 계산 cap으로 계속
    사용) 재사용할 수 있게 했다."""
    if n <= 0:
        return 0
    p = 1
    while p * 2 <= n and p * 2 <= cap:
        p *= 2
    return p


def _cup_bye_count(n, cap=64):
    """[2026-08 신설, 신민용 설계 확정] "더 이상 새 부수가 합류하지 않는
    첫 라운드"부터, 생존자 수를 표준 강수(8/16/32/64 중 하나, 64 상한 —
    "게임에서 128강보다 64강이 훨씬 익숙하다"는 이유로 상한을 둠)로 깔끔히
    수렴시키기 위한 부전승 인원을 계산한다.

    P = n 이하의 가장 큰 2의 거듭제곱(cap 상한)
    bye = 2P - n   (n이 이미 P와 같으면, 즉 이미 딱 떨어지면 0)

    이렇게 한 라운드에서만 부전승을 몰아주면, 그 다음부터는 항상 정확히
    반씩 줄어들어(P → P/2 → ... → 2) 라운드 이름(_round_name)과 3/4위전
    생성(_advance_round의 is_sf 판정)이 별도 손질 없이도 자동으로 맞아
    떨어진다.

    [2026-08 확장, 신민용 설계 확정: "본선 표준 강수는 참가 가능 팀
    총합 기준"] 예전엔 이 함수가 "더 이상 합류할 부수가 없는 순수
    토너먼트 단계"에서만 호출됐다 — 그런데 그 단계에 들어가기 *전*
    예선 라운드들이 그냥 절반씩 걸러내다가 우연히 표준 강수 밑으로
    떨어지는 문제가 있었다(예: 대한민국 4~5부까지 합쳐 참가 가능 팀이
    수십 팀인데도 예선에서 계속 반씩 줄다 보니 마지막엔 9팀 남아 8강부터
    시작). 이제 호출부(cup_engine._start_next_round)가 대회 시작 시점에
    미리 정해둔 표준 강수(cup_tournaments.standard_bracket_size)를 cap
    으로 넘겨서 예선 단계에서도 매 라운드 이 함수를 호출한다 — 매
    라운드 "지금 남은 인원을 표준 강수까지 최대한 수렴"시키려 시도하므로,
    티어가 계속 새로 합류해 인원이 다시 불어나도 그때마다 다시 표준
    강수로 눌러준다. 그 결과 모든 티어가 다 합류하고 나면 이미 표준
    강수 근처(대개 정확히 그 값)에 도달해 있어, "우연히 몇 명 남았는지"가
    아니라 "원래 몇 팀이 있었는지"로 본선 시작 지점이 정해진다.

    n이 cap의 2배를 넘어(공식이 음수가 나오는 극단적인 경우) 극단적인
    경우는 이번 라운드엔 부전승을 강제로 몰아주지 않고(홀수면 1명만,
    예전 방식) 다음 라운드에서 다시 계산한다 — 몇 라운드만 지나면
    자연히 공식이 정상 작동하는 범위로 줄어든다.
    """
    if n <= 0:
        return 0
    p = _cup_target_bracket_size(n, cap=cap)
    if p >= n:
        return 0
    bye = 2 * p - n
    if bye < 0:
        return 1 if n % 2 == 1 else 0
    return bye


def _start_next_round(t, p=None):
    """생존 풀(alive=1) + 다음 합류 티어를 합쳐 한 라운드를 만든다.
    합칠 티어가 더 없으면 생존 풀만으로 진행(순수 토너먼트 단계).

    [2026-08 최적화] p를 넘기면 get_player() 재조회를 생략한다(process_cup_week
    참고) — 실측 3시즌 헤드리스에서 이 함수 하나가 5,244회 호출됐는데, 넘겨받은
    p가 없을 때만 자체 조회하므로 기존 호출부(있다면)와도 그대로 호환된다."""
    from game_engine import add_log, get_player
    if p is None:
        p = get_player()
    tid = t["id"]
    # [2026-07 전체 국가 확장] 이제 이 함수가 모든 나라 컵대회에서 매주 여러 번
    # 호출되므로, 이벤트 로그는 내 나라(또는 대표국적) 대회일 때만 남긴다.
    _is_mine = (t["country_id"] == _my_country_id(p or {}))
    conn = get_conn()
    survivors = [dict(r) for r in conn.execute(
        "SELECT team_id, team_name FROM cup_entries WHERE tournament_id=? AND alive=1",
        (tid,)).fetchall()]
    conn.close()

    _orig_pending = [x for x in (t.get("pending_tiers") or "").split(",") if x]
    next_token, is_last_tier = _pop_next_tier(t)
    _rest_tiers = _orig_pending[1:]  # 이번에 합류한 티어(_orig_pending[0])를 뺀 나머지 — 앞으로 합류할 티어들
    # [2026-08 신설] cup_entries.tier는 INTEGER라 "1a"/"1b" 태그를 그대로
    # 못 넣는다 — 저장용 실제 티어 숫자(둘 다 1부)와, 라운드 로직/이름
    # 판정에 쓰는 원본 토큰을 분리한다.
    next_tier_num = None if next_token is None else int(next_token[0])
    pool = [(s["team_id"], s["team_name"], 0.0) for s in survivors]
    if next_token is not None:
        new_teams = _tier_teams(t["country_id"], next_token, year=t.get("year"))
        conn = get_conn(); c = conn.cursor()
        # [2026-08 버그수정, 신민용 리포트: "6라운드 다음이 갑자기 8라운드로
        # 뜬다 / 결승 직전 라운드가 이상하다"] "1a"(챔스 미참가 1부)와
        # "1b"(챔스 참가 1부)는 서로 다른 라운드(보통 몇 주 간격)에서 각각
        # 별도로 _tier_teams()를 호출해 "그 시점의 cl_entries 소속 여부"로
        # 나눈다. 그런데 실제 세이브에서 확인해보니, cl_entries가 "1a" 라운드
        # 처리 시점과 "1b" 라운드 처리 시점(최대 5주 간격) 사이에 갱신되면
        # (챔스 조편성 확정 등으로) 같은 팀이 "1a" 때는 비참가로 판정돼
        # 들어갔다가, "1b" 때 다시 참가로 판정돼 또 들어가는 식으로 같은
        # 팀이 cup_entries에 중복으로 쌓였다(실측: 도르트문트가 4라운드에
        # 이미 들어가 있는데 5라운드에서 또 들어가 그 라운드에 2경기를
        # 동시에 뛰는 상태가 됨 — 이후 라운드의 pool_entering이 실제 생존
        # 팀 수와 어긋나면서 "8강" 다음이 "4강"이 아니라 "8라운드"처럼
        # 표준 강수와 안 맞는 이름으로 밀려나는 원인이 됐다).
        # 어느 쪽이 "맞는" 분류인지 굳이 따지지 않고, 이 토너먼트에 이미
        # 들어와 있는 팀(cup_entries에 이미 행이 있는 팀)은 무조건 제외한다
        # — 한 대회에 같은 팀이 두 번 들어갈 방법 자체를 원천 차단.
        if new_teams:
            _already_in = {r["team_id"] for r in c.execute(
                "SELECT team_id FROM cup_entries WHERE tournament_id=?", (tid,)).fetchall()}
            new_teams = [nt for nt in new_teams if nt[0] not in _already_in]
        if new_teams:
            c.executemany("""INSERT INTO cup_entries(tournament_id, team_id, team_name, tier, ovr)
                         VALUES(?,?,?,?,?)""",
                          [(tid, team_id, team_name, next_tier_num, ovr) for team_id, team_name, ovr in new_teams])
        conn.commit(); conn.close()
        pool = pool + [(x[0], x[1], x[2]) for x in new_teams]


    if len(pool) < 2:
        if len(pool) == 1:
            _finish_tournament(t, pool[0][0])
        return

    pool_entering = len(pool)   # 이 라운드에 '참가하는' 팀 수 (라운드 이름 기준 — 예: 16강=16팀 참가)
    random.shuffle(pool)
    byes = []
    # [2026-08 재설계 v2, 신민용 설계 확정: "8강/16강은 원래 참가 가능했던
    # 팀 총합 기준으로 정해야 한다"] v1(len(pool)>=표준강수일 때만 수렴)은
    # 틀렸다 — 티어가 순차적으로 합류하는 구조상, 앞선 예선 라운드들이
    # 이미 절반씩 걸러낸 뒤라 "지금 이 순간의 pool"이 표준 강수(예: 64)에
    # 도달하는 일 자체가 없었다(예: 대한민국 총 96팀인데 예선 매 라운드
    # 반씩 걸러지며 pool이 한 번도 64를 못 넘김). 표준 강수는 "앞으로 더
    # 합류할 티어까지 다 합친 최종 총합"을 기준으로 이미 정해져 있으므로
    # (standard_bracket_size, 대회 시작 시점에 계산), 매 라운드 "지금
    # 당장 몇 명을 걸러내도 되는지"를 다음처럼 배분한다:
    #   future_total = 앞으로 합류할 티어들의 팀 수 합(아직 안 만난 팀)
    #   max_elim     = max(0, len(pool) + future_total - 표준강수)
    #     (이번 라운드에서 이만큼까지는 걸러내도, 남은 미래 합류분을 다
    #      더해도 여전히 표준강수 이상은 유지된다는 안전 마진)
    #   matches      = min(len(pool)//2, max_elim)  (한 라운드에 최대
    #      가능한 매치 수와 안전 마진 중 작은 쪽 — 페이스 조절의 핵심)
    #   나머지는 전부 부전승(byes)으로 이번 라운드를 건너뛴다.
    # 이렇게 하면 예선 초반엔 여유가 많아 공격적으로 걸러내다가(빈 마진
    # 소진), 남은 티어가 가까워질수록 자동으로 걸러내는 양이 줄어들어,
    # 모든 티어가 다 합류한 시점엔 정확히 표준 강수(또는 그 이하로 자연
    # 스럽게 근접)만 남는다. 더 이상 합류할 티어가 없는 순수 토너먼트
    # 단계(next_token is None)는 future_total=0이라 이 공식이 기존
    # _cup_bye_count와 동일하게 동작한다(자연스럽게 통합됨).
    _std = t.get("standard_bracket_size") or 0
    if _std <= 0:
        _std = CUP_STANDARD_BRACKET_CAP   # 이 컬럼이 없는 구버전 세이브(마이그레이션 이전 생성된 대회) 안전 폴백 — 16강 상한
    if next_token is None:
        # 더 이상 합류할 티어가 없는 순수 토너먼트 단계 — 여기서부턴
        # "안 걸러도 되는 여유"를 계산할 필요 없이, 그냥 표준 강수로
        # 정상적으로 반씩 줄여나가면 된다(기존 _cup_bye_count 그대로).
        n_bye = _cup_bye_count(len(pool), cap=_std)
        for _ in range(n_bye):
            byes.append(pool.pop())
    else:
        _future_total = sum(
            len(_tier_teams(t["country_id"], tok, year=t.get("year")))
            for tok in _rest_tiers)
        # [2026-08 버그수정, 신민용 리포트: "18~26팀 정도인 나라도 16강이
        # 가능한데 왜 8강부터 시작해?"] 실측(422개 신규 대회) 결과 표준
        # 강수(_std)>=16으로 계산됐는데도 실제론 16강을 못 만들고 8강으로
        # 떨어지는 대회가 62개 있었다 — 전부 총 참가팀이 18~30팀 안팎으로
        # 여유가 빠듯한 소국이었다. 원인: 바로 아래 "매 라운드 최소 1경기
        # 보장"이 max_elim=0(=이번 라운드는 하나도 안 걸러야 딱 맞는 상황)
        # 이어도 강제로 1명을 더 걸러낸다 — 남은 합류 라운드 수만큼 이
        # "불필요한 초과 탈락"이 반복 누적되면, 표준강수 문턱 바로 위에
        # 있던 소국들이 문턱 밑으로 새 버렸다. 남은 합류 라운드 수
        # (len(_rest_tiers))만큼 여유(margin)를 미리 더 깎아서 계산해두면,
        # "이후 모든 라운드가 전부 강제로 1명씩 더 걸러내는 최악의 경우"
        # 를 가정해도 최종적으로 표준강수 이상이 남도록 보장된다.
        _raw_margin = len(pool) + _future_total - _std
        _max_elim = max(0, _raw_margin - len(_rest_tiers))
        if _raw_margin <= 0:
            # [2026-08 신설] 여유가 전혀 없는 극단적 소국(총 참가팀이
            # 표준강수와 거의 같거나 더 적음, 예: 총 16팀에 표준강수도
            # 정확히 16)은 "최소 1경기 강제"조차 걸면 바로 목표 밑으로
            # 떨어진다 — 이런 경우 이번 라운드는 아무도 안 걸러내고
            # (부전승 처리만) 그대로 다음 라운드로 즉시 넘어간다(아래
            # _match_rows가 비면 그 주차를 소모하지 않고 재귀 진행).
            _matches_this_round = 0
        else:
            # [2026-08 버그수정] max_elim이 0이 나오는 라운드(여유가 충분해서
            # "이번엔 안 걸러도 표준강수를 채울 수 있다"는 경우)가 실제로
            # 여러 라운드 연속으로 나올 수 있는데, matches=0이면 이 라운드에
            # cup_matches가 단 한 건도 안 생긴다 — process_cup_week/_process_one이
            # "그 주차에 cup_matches가 있는 라운드"로만 진행 여부를 판단하므로
            # (round_names 조회, 위 참고), 매치가 0건인 라운드는 감지 자체가
            # 안 돼서 대회가 그 지점에서 영원히 멈춘다(실측으로 확인됨). 매
            # 라운드 최소 1경기는 반드시 열리도록 강제한다 — 페이스 조절
            # 공식보다 우선한다(len(pool)>=2는 위에서 이미 보장됨). _raw_margin>0
            # 임을 이미 확인했으므로, 최소 1경기를 강제해도 표준강수 밑으로
            # 떨어지지 않는다(안전).
            _matches_this_round = min(len(pool) // 2, max(1, _max_elim))
        _n_bye = len(pool) - 2 * _matches_this_round
        for _ in range(_n_bye):
            byes.append(pool.pop())


    conn = get_conn()
    p_row = conn.execute("SELECT current_team_id FROM my_player WHERE id=1").fetchone()
    my_tid = p_row["current_team_id"] if p_row else 0

    round_counter = t["round_counter"]
    # [2026-08 신설, 신민용 설계 확정: "예선(5부 단독)은 별도 이름, 그
    # 다음부터 1라운드로 다시 번호 매김"] has_qualifying=1인 대회는
    # round_counter==0인 딱 한 번(그 대회의 첫 라운드 = pending_tiers
    # 맨 앞이 "5"였던 그 라운드)만 "예선"으로 고정하고, 그 이후 라운드는
    # 화면 번호에서 예선 몫(1칸)을 빼서 1라운드부터 다시 세게 한다 —
    # 안 그러면 "예선"이 라운드 번호 하나를 잡아먹어 4부 합류가
    # "2라운드"로 밀려버린다(원래 의도는 "1라운드").
    _has_qual = t.get("has_qualifying", 0) or 0
    if _has_qual and round_counter == 0:
        rname = "예선"
    else:
        _display_counter = round_counter - _has_qual
        # [버그 수정] '결승'은 2팀이 붙어서 1팀이 남는 라운드인데, 예전엔 이
        # 라운드가 끝난 뒤 '남는 팀 수'로 이름을 붙여서 4팀이 붙는 라운드가
        # '결승'으로, 진짜 결승(2팀)은 이름 없는 'N라운드'로 밀려나는 오류가
        # 있었다. 실제 관례대로 '이 라운드에 들어오는 팀 수' 기준으로 고쳤다
        # (16강=16팀 참가, 결승=2팀 참가).
        rname = _round_name(pool_entering, _display_counter, is_pure_ko=(next_token is None))
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
        # [2026-07 추가 수정] 오버플로우 구간도 휴식기(28~31주)만큼은
        # 피한다. 다만 상한은 43(클럽 시즌 끝)이 아니라 52로 유지했다 —
        # 43으로 낮추면 라운드가 아주 많은 나라(부수가 많아 오버플로우가
        # 큰 경우)에서 여러 라운드가 전부 43주로 겹쳐, 예전에 고쳤던
        # "라운드 중복 생성 무한루프" 버그가 재발할 위험이 있다. 44주
        # 이후(국제 오프시즌, 클럽 경기 없음)로 밀리는 경기가 생기는
        # 근본 문제는 아직 안 풀렸다 — 팀/부수 수에 맞춰 라운드 간격
        # 자체를 조정하는 재설계가 필요한 부분이라 이번 수정 범위 밖.
        if INTL_QUAL_WEEK <= week <= _CUP_BREAK_END_WEEK:
            week = _CUP_BREAK_END_WEEK + 1

    c = conn.cursor()
    _match_rows = []
    for slot in range(0, len(pool), 2):
        home, away = pool[slot], pool[slot + 1]
        is_my = 1 if my_tid in (home[0], away[0]) else 0
        _match_rows.append((tid, rname, round_counter, week, home[0], away[0], is_my, slot // 2,
                            pool_entering))
    if not _match_rows:
        # [2026-08 신설] 이번 라운드가 부전승만으로 이루어져(_matches_this_round=0)
        # 실제 경기가 하나도 없다 — process_cup_week/_process_one은 그 주차에
        # cup_matches가 있어야만 라운드를 감지하므로, 빈 라운드를 그대로
        # 만들면 감지가 안 돼서 대회가 영원히 멈춘다. 아무 팀도 안 걸러냈으니
        # (전원 부전승) 이번 주차를 소모할 필요 자체가 없다 — round_counter만
        # 그대로 둔 채(아직 진짜 라운드가 시작 안 했으므로) 곧바로 다음
        # 라운드 생성을 재귀 호출해서 이어간다. pending_tiers는 이미
        # _pop_next_tier()에서 갱신됐으므로 t를 다시 조회해서 넘긴다.
        conn.commit(); conn.close()
        _t2 = get_cup_tournament(t.get("year"), t["country_id"])
        if _t2:
            _start_next_round(_t2, p=p)
        return
    c.executemany("""INSERT INTO cup_matches
                     (tournament_id, round_name, round_idx, week,
                      home_team_id, away_team_id, is_my, slot, pool_entering)
                     VALUES(?,?,?,?,?,?,?,?,?)""", _match_rows)
    if byes:
        if _is_mine:
            if len(byes) == 1:
                add_log(f"🏆 {t['name']} {rname}: {byes[0][1]} 부전승", "event")
            else:
                add_log(f"🏆 {t['name']} {rname}: {len(byes)}팀 부전승", "event")
    conn.execute("UPDATE cup_tournaments SET round_counter=? WHERE id=?",
                 (round_counter + 1, tid))
    conn.commit()
    conn.close()
    if _is_mine:
        add_log(f"🏆 {t['name']} {rname} 대진 확정 ({len(pool)}팀 + 부전승 {len(byes)}팀)", "event")


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
    # [2026-07 재수정] "내 경기 아니면 day=None 강제"는 생성 시점에 이미
    # day가 채워진 경기의 값을 시뮬레이션 순간 지워버리는 회귀 버그가
    # 된다(intl_engine.py 예선/본선에서 실제로 재현·수정된 것과 동일한
    # 패턴) — 이제 기존 값을 보존한다.
    day = _week_intl_cl_day(m["week"], get_player() or {}) if m["is_my"] else m.get("day")

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


def sim_my_cup_match_as_ai(week, p, reason="injury", day=None):
    """[2026-07 신설, 버그수정] 부상 등으로 내가 못 뛸 때 내 컵대회 경기를
    AI끼리(내 보너스 없이) 시뮬레이션 — 이게 없으면 그 경기가 영원히
    home_score=-1(미완료)로 남아 대회 전체 진행이 멈춘다(신민용 리포트:
    "10월인데 1월 경기가 계속 '예정'으로 남아있다"). simulate_my_cup_match와
    동일하게 정보를 조회한 뒤 _sim_ai_match로 넘긴다.

    [2026-07 버그수정, 신민용 리포트: "부상으로 경기 못 나갔는데 감독관계가
    그대로다"] game_engine._sim_my_team_match_as_ai와 동일한 이유로,
    이 컵대회 AI-대체 경로도 결장 페널티(manager_relation -1)를 적용한다."""
    info = get_my_cup_match(week, day=day)
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
    from game_engine import update_player, _calc_manager_rel
    update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False, not_played_penalty=2))


def simulate_my_cup_match(week, p, day=None):
    """내가 출전하는 컵대회 경기."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _roll_red_card, _apply_red_card_dismissal)
    from constants import PERSONALITY_EFFECTS
    # [2026-07 버그수정] day 파라미터가 시그니처엔 있었지만 실제로
    # get_my_cup_match에 전달이 안 돼 무시되고 있었다.
    info = get_my_cup_match(week, day=day)
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
    # [2026-08 최적화, 신민용 리포트: "1년씩 돌리는데 전보다 느려졌다"]
    # 예전엔 활성 국내컵 나라 수(최대 200개 이상)만큼 _process_one →
    # _advance_round → _start_next_round 체인 안에서 get_player()를
    # 매번 새로 DB 조회했다(실측: 3시즌 헤드리스에서만 get_player() 2만 회,
    # 순수 오버헤드 2.4초). 컵대회가 리그처럼 나라 전체로 확장되면서
    # (전에는 몇 개국뿐이었을 이 경로가) 호출 횟수가 그만큼 곱연산으로
    # 늘어난 게 체감 저하의 큰 부분으로 보인다. get_player()는 이 한 주
    # 처리 안에서는 값이 바뀌지 않으므로 여기서 딱 한 번만 조회해 아래로
    # 전달한다 — 결과(각 대회 진행 로직)는 완전히 동일, DB 왕복 횟수만 준다.
    from game_engine import get_player
    p = get_player()
    conn = get_conn()
    ts = [dict(r) for r in conn.execute(
        "SELECT * FROM cup_tournaments WHERE status='active'").fetchall()]
    conn.close()
    for t in ts:
        _process_one(t, week, p=p)


def _process_one(t, week, p=None):
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
        "SELECT * FROM cup_matches WHERE tournament_id=? AND week=? AND home_score=-1 AND is_my=0 ORDER BY id",
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
        _advance_round(t, rname, week, p=p)


def _advance_round(t, round_name, week, p=None):
    """한 라운드(round_name, week 조합 — 결승/3·4위전처럼 같은 주차에 여러
    라운드명이 동시에 있을 수 있다)가 이번 주차에 전부 끝났는지 확인하고,
    끝났으면 탈락 처리 + 다음 단계로 진행시킨다.

    [2026-08 최적화] p를 넘기면 get_player() 재조회를 생략한다(process_cup_week
    참고) — 넘기지 않는 다른 호출부(있다면)를 위해 기존처럼 자체 조회하는
    경로도 그대로 남겨둔다."""
    from game_engine import add_log, get_player
    if p is None:
        p = get_player()
    tid = t["id"]
    conn = get_conn()
    cur = [dict(r) for r in conn.execute(
        "SELECT * FROM cup_matches WHERE tournament_id=? AND week=? AND round_name=? ORDER BY slot",
        (tid, week, round_name)).fetchall()]
    conn.close()
    if not cur or any(m["home_score"] == -1 for m in cur):
        return  # 이 라운드는 아직 없거나 미완료

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
            _start_next_round(t2, p=p)

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
                if t["country_id"] == _my_country_id(p or {}):
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
        _start_next_round(t2, p=p)


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
    # [2026-08 신설, 신민용 확정: club_momentum 확장] 국내컵 우승팀도 챔스와
    # 같은 틀로 momentum을 받는다 — 다만 국내컵은 챔스보다 위상이 낮으므로
    # domestic_cup_champion 스케줄(더 약하고 짧음)을 쓴다.
    from constants import MOMENTUM_START_BY_TYPE
    conn.execute("UPDATE teams SET momentum_type=?, momentum_seasons_left=? WHERE id=?",
                 ("domestic_cup_champion", MOMENTUM_START_BY_TYPE["domestic_cup_champion"], winner_id))
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