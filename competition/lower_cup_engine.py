# competition/lower_cup_engine.py
# [2026-09 신설, 신민용 설계] 3·4부(또는 3부까지만 있는 나라는 2·3부) 전용
# 신설 컵대회 엔진. 기존 cup_engine.py(전 부수가 단계적으로 합류하는
# FA컵식 국내컵)와는 완전히 별개의 대회다 — 저 컵은 1~5부가 전부 참가하고
# 표준 강수도 16강 상한(CUP_STANDARD_BRACKET_CAP=16)인 반면, 이 대회는
# 딱 두 개 티어(3·4부, 없으면 2·3부)만 참가하고 표준 강수 상한은 64강까지
# 열려 있다 — 두 대회는 설계 목적 자체가 다르므로 상수를 공유하지 않는다.
#
# ── 대진 구성 원칙 (2026-09, 대화로 확정) ────────────────────────
# 참가 가능 팀 수 N에 대해:
#   T = N 이하 가장 큰 2의 거듭제곱(64 상한) = _cup_target_bracket_size(N, cap=64)
#   PO(플레이오프) 참가 인원 = 2 × (N - T)   [탈락시켜야 할 인원(N-T)의 2배가
#        PO를 치른다 — 부전승 인원 = N - PO인원 = 2T-N]
#   전 팀을 "실력순"(티어가 낮을수록 하위, 같은 티어 안에서는 이번 시즌
#   진행 중 순위)으로 정렬한 뒤, 최하위 PO인원만큼이 PO를 치러 (N-T)명이
#   생존 → 나머지 상위 (2T-N)명은 부전승으로 바로 본선(T강) 합류.
#   PO 대진은 "PO 참가자 중 최상위 vs 최하위, 그 다음 vs 그 다음..." 식
#   시딩(예: 50팀 중 PO 36명이면 15위 vs 50위, 16위 vs 49위 ...).
#   N이 이미 2의 거듭제곱이면 PO 없이 그대로 T강 시작.
#
# ── 남은 미해결 사항 (반드시 실제 세이브로 검증 필요) ──────────────
# 1) 주차/요일 배정: 아래 LOWER_CUP_ROUND_WEEKS_POOL은 하반기(32~43주,
#    constants.SECOND_HALF_START 참고) 안에서 cup_engine.CUP_ROUND_WEEKS_POOL
#    (30→32,34,36,38,40,42)과 "같은 주차"를 재사용하되 요일만 다르게
#    잡는 방식(_ROUND_DAY_OFFSET)을 썼다. round_to_day/round_match_days
#    같은 리그용 정교한 분산 로직은 일부러 안 썼다(이 대회는 배경 처리
#    비중이 커서 그 정도 정밀도가 필요 없다고 판단) — 대신 한 라운드의
#    모든 경기를 그 주 안의 고정된 하루(주 시작일+_ROUND_DAY_OFFSET)에
#    몰아넣는다. 이 요일이 리그/기존 국내컵/국제대회와 실제로 안 겹치는지는
#    이 파일만으로는 검증 불가능하니(실제 게임 실행 필요) 반드시 몇 시즌
#    돌려서 로그(add_log)로 충돌 여부를 확인해줘.
# 2) 세계 전체(약 190개국) 브래킷을 몇 초 안에 처리할 수 있는지 성능
#    검증 필요 — cup_engine 전례(전 세계 0.2초)로 보아 문제없을 가능성이
#    높지만, 이 대회는 국가당 최대 64강(=63경기)까지 가므로 체감 필요.
# 3) 승강제로 "이번 시즌 3부/4부 소속"이 매 시즌 바뀐다 — 대회는 매년
#    시즌 시작 시점(하반기 개막) 조회로 새로 구성하므로 자동으로 반영된다.

import random
from database import get_conn
from competition.cup_engine import (
    _cup_target_bracket_size, _cup_bye_count, _round_name,
    _match_outcome, _resolve_pso, _winner_of,
)
from constants import day_to_week, week_to_day, SECOND_HALF_START

LOWER_CUP_BRACKET_CAP = 64

# 하반기(32~43주 근방) 안에서 기존 국내컵과 같은 주차를 쓰되 요일만
# 다르게 잡는다. 필요하면 이 리스트 자체를 늘려서(예: 33,35,37...도 추가)
# 라운드 수가 부족해지는 상황(64강 국가는 PO+6라운드=7라운드 필요)을 대비한다.
LOWER_CUP_ROUND_WEEKS_POOL = [32, 34, 36, 38, 40, 42, 43]
_ROUND_DAY_OFFSET = 3   # 그 주의 4일째(수요일 격) — cup_engine(offset 0)과 다른 요일

# ── 국가별 대회명 (사용자 지정 목록 그대로) ──────────────────────
LOWER_CUP_NAME_BY_COUNTRY = {
    "아르헨티나": "코파 페데랄 데 아센소", "프랑스": "쿠프 드 라 리그 3-4",
    "스페인": "코파 페데라시온", "잉글랜드": "EFL 트로피",
    "브라질": "코파 나시오나우 다스 리가스", "모로코": "쿠프 드 라 리그 나시오날",
    "네덜란드": "KNVB 디비지 컵", "독일": "3. 리가 포칼",
    "포르투갈": "타사 다스 리가스", "벨기에": "나시오날레 디비지 컵",
    "멕시코": "코파 데 엑스판시온", "콜롬비아": "코파 프리메라 B",
    "미국": "USL 리그 컵", "이탈리아": "코파 이탈리아 세리에 C",
    "크로아티아": "쿠프 3. 이 4. 리게", "세네갈": "쿠프 나시오날 데 디비지옹",
    "일본": "J3・JFL 컵", "우루과이": "코파 데 아센소",
    "스위스": "스위스 프로모션 컵", "덴마크": "디비지온스포칼렌",
    "오스트리아": "3. 리가 포칼", "이란": "하즈피 리그 트로피",
    "나이지리아": "나이지리아 리그 트로피", "호주": "내셔널 리그 챌린지 컵",
    "노르웨이": "포스트노르드 컵", "캐나다": "리그1 캐나다 컵",
    "이집트": "이집션 리그 컵", "알제리": "쿠프 데 디비지옹",
    "에콰도르": "코파 데 아센소", "대한민국": "K3·K4 챔피언스 컵",
    "코트디부아르": "쿠프 데 리그 나시오날", "튀르키예": "TFF 2. 리그 쿠파시",
    "우크라이나": "우크라이니안 리그 컵", "러시아": "FNL-2 쿠보크",
    "폴란드": "푸하르 리그 니즈시흐", "스웨덴": "디비지온스쿠펜",
    "파라과이": "코파 데 인테르메디아", "웨일스": "컴리 리그스 컵",
    "헝가리": "NB II–III 쿠파", "파나마": "코파 데 리가스",
    "스코틀랜드": "SPFL 챌린지 컵", "세르비아": "쿠프 프르베 리게",
    "콩고 민주 공화국": "쿠프 데 리그", "체코": "포하르 ČFL–MSFL",
    "카메룬": "쿠프 데 리그", "슬로바키아": "포하르 2. 아 3. 리기",
    "그리스": "수퍼 리그 2 컵", "베네수엘라": "코파 데 아센소",
    "칠레": "코파 데 리가스", "페루": "코파 데 아센소",
    "코스타리카": "코파 데 리가스", "루마니아": "쿠파 리기",
    "말리": "쿠프 데 리그", "튀니지": "쿠프 드 라 리그 나시오날",
    "우즈베키스탄": "프로 리그 컵", "아일랜드": "리그 오브 아일랜드 퍼스트 디비전 컵",
    "슬로베니아": "포칼 2. 인 3. 리게", "카타르": "QFA 세컨드 디비전 컵",
    "사우디아라비아": "사우디 리그 트로피", "이라크": "이라키 디비전 컵",
    "남아프리카공화국": "ABC 모체페 컵", "부르키나 파소": "쿠프 데 디비지옹",
    "카보베르데": "타사 나시오나우 다스 리가스", "보스니아 헤르체고비나": "쿠프 프르베 리게",
    "가나": "디비전 원 리그 컵", "온두라스": "코파 데 아센소",
    "알바니아": "쿠파 에 카테고리베", "요르단": "요르단 리그 컵",
    "아랍에미리트": "UAE 퍼스트 디비전 컵", "북마케도니아": "쿠프 나 리가타",
    "북아일랜드": "NIFL 챌린지 컵", "자메이카": "JFF 챔피언십 컵",
    "조지아": "에로브눌리 리가 2 컵", "아이슬란드": "데일다비카르",
    "핀란드": "윅쾨슬리가 컵", "이스라엘": "리가 레우밋 컵",
    "볼리비아": "코파 데 아센소", "코소보": "리가 에 파레 컵",
    "오만": "퍼스트 디비전 컵", "몬테네그로": "쿠프 드루게 리게",
    "퀴라소": "코파 디 디비숀", "기니": "쿠프 데 리그",
    "뉴질랜드": "내셔널 리그 컵", "시리아": "시리안 리그 컵",
    "가봉": "쿠프 데 리그", "불가리아": "쿠파 나 브토라 리가",
    "아이티": "쿠프 데 리그", "앙골라": "타사 다스 디비지옹",
    "우간다": "FUFA 빅 리그 컵", "잠비아": "잠비아 내셔널 리그 컵",
    "중국": "중국 프로 리그 컵", "바레인": "바레인 세컨드 디비전 컵",
    "베냉": "쿠프 데 리그", "태국": "타이 리그 3 컵",
    "팔레스타인": "팔레스타인 리그 컵", "벨라루스": "쿠보크 페르보이 리기",
    "과테말라": "코파 데 프리메라 디비시온", "룩셈부르크": "쿠프 데 디비지옹",
    "베트남": "꿉 항 녓", "엘살바도르": "코파 데 아센소",
    "타지키스탄": "타지키스탄 퍼스트 리그 컵", "트리니다드 토바고": "TT 프리미어 디비전 컵",
    "모잠비크": "타사 다스 리가스", "마다가스카르": "쿠프 데 리그",
    "적도 기니": "코파 데 라스 리가스", "키르기스스탄": "쿠보크 리기",
    "아르메니아": "아라진 리가이 가바트", "코모로": "쿠프 데 리그",
    "케냐": "내셔널 수퍼 리그 컵", "리비아": "리비안 리그 컵",
    "카자흐스탄": "퍼스트 리그 컵", "탄자니아": "챔피언십 리그 컵",
    "모리타니": "쿠프 데 리그", "니제르": "쿠프 데 디비지옹",
    "레바논": "레바논 리그 컵", "감비아": "GFA 디비전 컵",
    "수단": "수단 리그 컵", "인도네시아": "리가 2–3 컵",
    "토고": "쿠프 데 리그", "북한": "공화국 하부리그 컵",
    "나미비아": "NFA 퍼스트 디비전 컵", "시에라리온": "내셔널 퍼스트 디비전 컵",
    "페로 제도": "1. 데일드 컵", "키프로스": "세컨드 & 서드 디비전 컵",
    "수리남": "SVB 에르스테 디비지 컵", "아제르바이잔": "I 리가 쿠보쿠",
    "에스토니아": "에실리가 컵", "르완다": "내셔널 리그 컵",
    "말라위": "내셔널 디비전 컵", "짐바브웨": "디비전 원 컵",
    "니카라과": "코파 데 세군다 디비시온", "기니비사우": "타사 다스 리가스",
    "쿠웨이트": "쿠웨이트 퍼스트 디비전 컵", "필리핀": "PFL 디비전 컵",
    "말레이시아": "MFL 챌린지 컵", "라트비아": "1. 리가 컵",
    "인도": "I-리그 2 컵", "중앙아프리카공화국": "쿠프 데 리그",
    "라이베리아": "LFA 디비전 컵", "투르크메니스탄": "퍼스트 리그 컵",
    "부룬디": "쿠프 드 라 리그", "에티오피아": "하이어 리그 컵",
    "도미니카 공화국": "코파 데 아센소", "예멘": "예멘 세컨드 디비전 컵",
    "레소토": "A 디비전 컵", "보츠와나": "퍼스트 디비전 컵",
    "싱가포르": "싱가포르 챌린지 컵", "리투아니아": "I 리가 컵",
    "가이아나": "GFF 엘리트 리그 컵", "뉴칼레도니아": "쿠프 데 리그",
    "세인트키츠 네비스": "SKNFA 디비전 컵", "솔로몬 제도": "S-리그 챌린지 컵",
    "푸에르토리코": "리가 푸에르토리코 컵", "피지": "피지 시니어 리그 컵",
    "홍콩": "홍콩 FA 챌린지 컵", "타히티": "쿠프 데 리그",
    "미얀마": "MNL-2 컵", "몰도바": "리가 1 컵",
    "바누아투": "VFF 챌린지 컵", "몰타": "내셔널 아마추어 컵",
    "앤티가 바부다": "ABFA 퍼스트 디비전 컵", "그레나다": "GFA 디비전 컵",
    "쿠바": "코파 데 아센소", "에스와티니": "내셔널 퍼스트 디비전 컵",
    "세인트루시아": "SLFA 디비전 컵", "버뮤다": "버뮤다 퍼스트 디비전 컵",
    "파푸아뉴기니": "내셔널 사커 리그 컵", "남수단": "사우스 수단 내셔널 리그 컵",
    "세인트빈센트 그레나딘": "SVGFF 디비전 컵", "아프가니스탄": "아프가니스탄 챔피언스 컵",
    "안도라": "코파 데 세고나", "몰디브": "세컨드 디비전 컵",
    "중화 타이베이": "타이완 풋볼 리그 컵", "캄보디아": "캄보디안 리그 컵",
    "몬트세라트": "MFA 디비전 컵", "네팔": "마터스 메모리얼 B 디비전 컵",
    "모리셔스": "MFA 디비전 컵", "바베이도스": "BFA 프리미어 디비전 컵",
    "벨리즈": "프리미어 리그 디벨롭먼트 컵", "방글라데시": "방글라데시 챔피언십 컵",
    "도미니카 연방": "DFL 디비전 컵", "차드": "쿠프 데 리그",
    "에리트레아": "에리트레안 리그 컵", "라오스": "라오 리그 2 컵",
    "쿡 제도": "CIFA 챌린지 컵", "스리랑카": "챔피언스 리그 컵",
    "사모아": "사모아 내셔널 리그 컵", "아루바": "아루바 디비전 컵",
    "몽골": "몽골리아 퍼스트 리그 컵", "미국령 사모아": "FFAS 디비전 컵",
    "부탄": "부탄 디비전 컵", "마카오": "마카오 세컨드 디비전 컵",
    "브루나이": "브루나이 수퍼 리그 컵", "상투메 프린시페": "타사 다스 디비지옹",
    "지부티": "쿠프 데 리그", "케이맨 제도": "CIFA 퍼스트 디비전 컵",
    "파키스탄": "파키스탄 챌린지 컵", "소말리아": "소말리 퍼스트 디비전 컵",
    "통가": "통가 챌린지 컵", "동티모르": "타사 다 세군다 디비상",
    "지브롤터": "지브롤터 인터미디에이트 컵", "괌": "GFA 챌린지 컵",
    "세이셸": "세이셸 퍼스트 디비전 컵", "터크스 케이커스 제도": "TCIFA 디비전 컵",
    "리히텐슈타인": "리히텐슈타인 챌린지 컵", "바하마": "BFA 디비전 컵",
    "미국령 버진아일랜드": "USVI 리그 컵", "영국령 버진아일랜드": "BVIFA 챌린지 컵",
    "앵귈라": "AFA 디비전 컵", "산마리노": "코파 디 레가",
}
DEFAULT_LOWER_CUP_NAME = "{country}컵(3·4부)"


def lower_cup_name_for_country(country_name: str) -> str:
    return LOWER_CUP_NAME_BY_COUNTRY.get(
        country_name, DEFAULT_LOWER_CUP_NAME.format(country=country_name))


# ── DB 스키마 (database.py 초기화 함수에 그대로 추가) ────────────
def init_lower_cup_tables(c):
    """database.py의 테이블 초기화 함수(cup_tournaments 등을 만드는 곳)
    안에서 그대로 호출. cup_tournaments/cup_entries/cup_matches/cup_history와
    컬럼 이름을 최대한 맞춰서(is_my/my_played/my_goals/my_assists/my_saves/
    my_rating 등) 이후 UI 단계(career_window.py 등)가 기존 컵 처리 코드와
    같은 패턴으로 이 테이블들도 읽을 수 있게 했다."""
    c.execute("""CREATE TABLE IF NOT EXISTS lower_cup_tournaments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, country_id INTEGER, name TEXT,
        tier_basis TEXT DEFAULT '3+4',   -- '3+4' 또는 '2+3'(4부 없는 나라)
        status TEXT DEFAULT 'active',
        standard_bracket_size INTEGER DEFAULT 0,
        total_rounds INTEGER DEFAULT 0,
        round_counter INTEGER DEFAULT 0,
        has_qualifying INTEGER DEFAULT 0,   -- PO(예선) 라운드가 있었는지
        winner_team_id INTEGER DEFAULT 0,
        my_in INTEGER DEFAULT 0, my_result TEXT DEFAULT '',
        my_team_id INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS lower_cup_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, team_id INTEGER, team_name TEXT,
        tier INTEGER, ovr REAL, seed_rank INTEGER DEFAULT 0,
        alive INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS lower_cup_matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, round_name TEXT, round_idx INTEGER, week INTEGER, day INTEGER,
        home_team_id INTEGER, away_team_id INTEGER,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        pso_winner INTEGER DEFAULT 0, pso_score TEXT DEFAULT '',
        is_my INTEGER DEFAULT 0, slot INTEGER DEFAULT 0,
        my_played INTEGER DEFAULT 0, my_goals INTEGER DEFAULT 0,
        my_assists INTEGER DEFAULT 0, my_saves INTEGER DEFAULT 0,
        my_rating REAL DEFAULT 0,
        -- [2026-09 신설] "그 경기 당시 내 팀" — cup_matches.my_team_id와
        -- 완전히 같은 원칙(database.py 주석 참고). 대회 단위 my_team_id는
        -- 이적 시점에 갱신되므로(resync_my_lower_cup_registration) 과거
        -- 경기의 홈/원정 판정 기준으로 쓸 수 없다.
        my_team_id INTEGER DEFAULT 0)""")
    # [2026-09 신설] 기존 세이브 마이그레이션 — 이 테이블들은 database.py의
    # _MIGRATIONS가 아니라 여기서 CREATE되므로 ALTER도 같이 둔다.
    try:
        c.execute("ALTER TABLE lower_cup_matches ADD COLUMN my_team_id INTEGER DEFAULT 0")
    except Exception:
        pass   # 이미 있음
    c.execute("""CREATE TABLE IF NOT EXISTS lower_cup_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, country_id INTEGER, team_name TEXT, result TEXT,
        goals INTEGER DEFAULT 0, assists INTEGER DEFAULT 0,
        caps INTEGER DEFAULT 0, rating REAL DEFAULT 0)""")


# ── 참가 풀 결정 ──────────────────────────────────────────────
def _pool_tiers_for_country(c, country_id):
    """이 나라에 4부가 있으면 (3,4), 4부는 없고 3부까지만 있으면 (2,3),
    그마저 없으면(1~2부만 존재) None — 대회 자체를 열지 않는다."""
    tiers = {r[0] for r in c.execute(
        "SELECT DISTINCT tier FROM leagues WHERE country_id=?", (country_id,)).fetchall()}
    if 4 in tiers:
        return (3, 4)
    if 3 in tiers:
        return (2, 3)
    return None


def _current_season_rank_within_tier(c, league_id, season):
    """get_league_standings와 같은 원시 집계(승/무/패/득실)를 직접 계산해
    "이번 시즌 진행 중" 순위를 만든다. cup_engine에는 없는 함수라 여기서
    간단히 재구현 — 승점→득실차→다득점 순 표준 타이브레이크."""
    rows = c.execute(
        """SELECT home_team_id AS a, away_team_id AS b, home_score AS hs, away_score AS as_
           FROM match_results
           WHERE season=? AND (home_team_id IN
                 (SELECT id FROM teams WHERE league_id=?)
                 OR away_team_id IN (SELECT id FROM teams WHERE league_id=?))
                 AND home_score>=0 AND away_score>=0""",
        (season, league_id, league_id)).fetchall()
    team_ids = [r[0] for r in c.execute(
        "SELECT id FROM teams WHERE league_id=?", (league_id,)).fetchall()]
    stat = {tid: [0, 0, 0, 0, 0] for tid in team_ids}  # w,d,l,gf,ga
    for a, b, hs, as_ in rows:
        for tid in (a, b):
            if tid not in stat:
                stat[tid] = [0, 0, 0, 0, 0]
        if hs > as_:
            stat[a][0] += 1; stat[b][2] += 1
        elif hs < as_:
            stat[b][0] += 1; stat[a][2] += 1
        else:
            stat[a][1] += 1; stat[b][1] += 1
        stat[a][3] += hs; stat[a][4] += as_
        stat[b][3] += as_; stat[b][4] += hs
    ranked = sorted(stat.items(),
                     key=lambda kv: (-(kv[1][0]*3+kv[1][1]), -(kv[1][3]-kv[1][4]), -kv[1][3]))
    return {tid: i for i, (tid, _v) in enumerate(ranked)}   # team_id -> 0-base 순위(그 리그 안)


def _build_all_season_rank_maps(c, season):
    """[2026-09 성능, 신민용 리포트: "28주차→29주차에 걸리는 버퍼링"]
    _current_season_rank_within_tier를 리그마다 부르지 않고, 그 시즌 전체
    순위표를 한 번에 만들어 {league_id: {team_id: 0-base 순위}}로 돌려준다.

    [원인] 원래 쿼리는
        WHERE season=? AND (home_team_id IN (SELECT id FROM teams WHERE league_id=?)
                         OR away_team_id IN (SELECT id FROM teams WHERE league_id=?))
    인데, OR로 묶인 두 IN(서브쿼리)은 SQLite가 인덱스를 못 쓴다 —
    idx_mr_home_team/idx_mr_away_team이 둘 다 있어도 소용없다. 그래서
    호출 한 번마다 match_results(20만 행)를 통째로 스캔했고, 전세계
    3·4부컵 생성이 리그 386개를 도는 구조라 그 스캔이 386번 반복됐다.
    실측: start_lower_cup 22.7초 중 fetchall만 19.2초(1,371회).

    [동치성] 원래 함수는 "그 리그 소속 팀이 홈이든 원정이든 한쪽에라도
    끼어 있는 경기"를 그 리그 집계에 넣고, 경기에 나온 team_id가 그 리그
    소속이 아니어도 stat에 새로 추가했다(플레이오프처럼 다른 리그 팀과
    붙는 경기 대비). 여기서도 경기 하나를 양 팀이 속한 '모든' 리그의
    집계에 넣고, 그 경기의 두 팀을 해당 리그 stat에 없으면 추가한다 —
    승점·득실 계산식과 정렬 타이브레이크(승점→득실차→다득점)도 그대로다.
    """
    team_league = {}
    league_teams = {}
    for tid, lid in c.execute("SELECT id, league_id FROM teams").fetchall():
        team_league[tid] = lid
        league_teams.setdefault(lid, []).append(tid)
    stats = {lid: {tid: [0, 0, 0, 0, 0] for tid in tids}      # w,d,l,gf,ga
             for lid, tids in league_teams.items()}
    for a, b, hs, as_ in c.execute(
            """SELECT home_team_id, away_team_id, home_score, away_score
               FROM match_results
               WHERE season=? AND home_score>=0 AND away_score>=0""", (season,)).fetchall():
        la, lb = team_league.get(a), team_league.get(b)
        for lid in ((la,) if la == lb else (la, lb)):
            if lid is None:
                continue
            st = stats.get(lid)
            if st is None:
                st = stats[lid] = {}
            sa = st.get(a)
            if sa is None:
                sa = st[a] = [0, 0, 0, 0, 0]
            sb = st.get(b)
            if sb is None:
                sb = st[b] = [0, 0, 0, 0, 0]
            if hs > as_:
                sa[0] += 1; sb[2] += 1
            elif hs < as_:
                sb[0] += 1; sa[2] += 1
            else:
                sa[1] += 1; sb[1] += 1
            sa[3] += hs; sa[4] += as_
            sb[3] += as_; sb[4] += hs
    out = {}
    for lid, st in stats.items():
        ranked = sorted(st.items(),
                        key=lambda kv: (-(kv[1][0]*3+kv[1][1]), -(kv[1][3]-kv[1][4]), -kv[1][3]))
        out[lid] = {tid: i for i, (tid, _v) in enumerate(ranked)}
    return out


def _build_seeded_pool(c, country_id, season, rank_maps=None):
    """(3,4)부 또는 (2,3)부 팀을 모아 "실력순"(낮은 티어 번호가 항상 위,
    같은 티어 안에서는 이번 시즌 진행 중 순위) 리스트를 반환.
    반환: [(team_id, team_name, tier, ovr), ...] 강한 순."""
    from game_engine import _team_avg_ovr
    tiers = _pool_tiers_for_country(c, country_id)
    if not tiers:
        return None, None
    hi_tier, lo_tier = tiers  # hi_tier가 숫자는 더 작음(더 상위 리그)
    pool = []
    for tier in (hi_tier, lo_tier):
        lg = c.execute("SELECT id FROM leagues WHERE country_id=? AND tier=?",
                        (country_id, tier)).fetchone()
        if not lg:
            continue
        league_id = lg[0]
        rank_map = (rank_maps.get(league_id, {}) if rank_maps is not None
                    else _current_season_rank_within_tier(c, league_id, season))
        teams = c.execute("SELECT id, name FROM teams WHERE league_id=?", (league_id,)).fetchall()
        teams_sorted = sorted(teams, key=lambda t: rank_map.get(t[0], 999))
        for tid, tname in teams_sorted:
            pool.append((tid, tname, tier, _team_avg_ovr(c, tid)))
    return pool, tiers


# ── 대회 생성 ─────────────────────────────────────────────────
def start_lower_cup(year, season):
    """cup_engine.start_domestic_cup(year, season)과 완전히 같은 호출 규약 —
    game_engine.py에서 그 함수를 부르는 바로 다음 줄에
    lower_cup_engine.start_lower_cup(new_year, new_season)만 추가하면 된다.
    내부에서 get_player()/add_log를 직접 끌어와 내 나라·내 팀을 판정하는
    것까지 cup_engine과 동일한 패턴(중복 생성 방지 포함)."""
    from game_engine import get_player, add_log
    from competition.cup_engine import _my_country_id
    p = get_player()
    my_country_id = _my_country_id(p) if p else None
    my_team_id = (p.get("current_team_id", 0) or None) if p else None

    conn = get_conn(); c = conn.cursor()
    # [2026-09 성능] 나라별로 _build_seeded_pool을 돌 때마다 리그 순위를
    # 다시 계산하면 match_results 전체 스캔이 리그 수만큼 반복된다
    # (_build_all_season_rank_maps 주석 참고). 전세계 순위를 여기서 한 번만
    # 만들어 넘긴다.
    _rank_maps = _build_all_season_rank_maps(c, season)
    country_rows = c.execute("SELECT DISTINCT country_id FROM leagues").fetchall()
    existing_cids = {r[0] for r in c.execute(
        "SELECT country_id FROM lower_cup_tournaments WHERE year=?", (year,)).fetchall()}
    created = 0
    for (cid,) in country_rows:
        if cid in existing_cids:
            continue
        pool, tiers = _build_seeded_pool(c, cid, season, rank_maps=_rank_maps)
        if not pool or len(pool) < 8:
            continue   # 8강도 안 되는 나라는 대회를 열지 않는다(최소 규모 가드)
        n = len(pool)
        bracket = _cup_target_bracket_size(n, cap=LOWER_CUP_BRACKET_CAP)
        bye = _cup_bye_count(n, cap=LOWER_CUP_BRACKET_CAP)
        po_pool_size = n - bye if bye else 0   # PO 참가 인원 = 2*(N-T) = N-부전승인원

        cname_row = c.execute("SELECT name FROM countries WHERE id=?", (cid,)).fetchone()
        cname = cname_row[0] if cname_row else "?"
        tname = lower_cup_name_for_country(cname)

        is_my = 1 if (my_country_id == cid) else 0
        c.execute("""INSERT INTO lower_cup_tournaments
            (year, country_id, name, tier_basis, status, standard_bracket_size,
             total_rounds, round_counter, has_qualifying, my_in, my_team_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (year, cid, tname, f"{tiers[0]}+{tiers[1]}", "active", bracket,
             0, 0, 1 if po_pool_size else 0, is_my, my_team_id if is_my else 0))
        tid = c.lastrowid

        for seed_rank, (pid, pname, tier, ovr) in enumerate(pool):
            c.execute("""INSERT INTO lower_cup_entries
                (tournament_id, team_id, team_name, tier, ovr, seed_rank, alive)
                VALUES (?,?,?,?,?,?,1)""", (tid, pid, pname, tier, ovr, seed_rank))

        if po_pool_size:
            _create_po_round(c, tid, pool, po_pool_size, week=LOWER_CUP_ROUND_WEEKS_POOL[0],
                              my_team_id=my_team_id if is_my else None)
        else:
            _create_ko_round(c, tid, [team[0] for team in pool], round_idx=0,
                              week=LOWER_CUP_ROUND_WEEKS_POOL[0],
                              my_team_id=my_team_id if is_my else None)
        created += 1
        if is_my and add_log:
            add_log(f"🏆 {tname} 개막 — {n}개 팀 참가"
                    + (f" (예선 {po_pool_size}팀 → {bye}팀 부전승)" if po_pool_size else ""))
    conn.commit(); conn.close()
    return created


def _create_po_round(c, tid, pool, po_pool_size, week, my_team_id=None):
    """최하위 po_pool_size명을 뽑아 "최상위 vs 최하위" 시딩으로 PO 대진을 짠다.
    나머지 상위(부전승) 팀은 alive=1 그대로 두고 다음 라운드에 바로 합류한다
    (별도 매치 없이 통과)."""
    n = len(pool)
    po_group = pool[n - po_pool_size:]   # 순위 하위 po_pool_size명(약한 팀들)
    half = po_pool_size // 2
    day = week_to_day(week) + _ROUND_DAY_OFFSET
    slot = 0
    for i in range(half):
        top = po_group[i]          # PO 참가자 중 상대적으로 강한 쪽
        bottom = po_group[po_pool_size - 1 - i]   # 상대적으로 약한 쪽
        is_my = 1 if my_team_id in (top[0], bottom[0]) else 0
        c.execute("""INSERT INTO lower_cup_matches
            (tournament_id, round_name, round_idx, week, day,
             home_team_id, away_team_id, is_my, slot, my_team_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (tid, "예선 플레이오프", 0, week, day, top[0], bottom[0], is_my, slot,
             my_team_id if is_my else 0))
        slot += 1


def _create_ko_round(c, tid, team_ids, round_idx, week, my_team_id=None, round_name=None):
    """순수 토너먼트 라운드(부전승 없이 딱 떨어지는 인원) 대진 생성.
    시딩은 seed_rank 순으로 이미 정렬된 team_ids를 그대로 절반씩 짝짓는다
    (1번-마지막, 2번-마지막에서 2번째 ... 식 브래킷 시딩)."""
    n = len(team_ids)
    rname = round_name or _round_name(n, round_idx, is_pure_ko=True)
    day = week_to_day(week) + _ROUND_DAY_OFFSET
    half = n // 2
    slot = 0
    for i in range(half):
        home = team_ids[i]
        away = team_ids[n - 1 - i]
        is_my = 1 if my_team_id in (home, away) else 0
        c.execute("""INSERT INTO lower_cup_matches
            (tournament_id, round_name, round_idx, week, day,
             home_team_id, away_team_id, is_my, slot, my_team_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (tid, rname, round_idx, week, day, home, away, is_my, slot,
             my_team_id if is_my else 0))
        slot += 1


# ── 주간 처리 (game_engine.py 주간 루프에서 cup_engine.process_cup_week(week)
#    바로 다음 줄에 process_lower_cup_week(week)를 추가 호출) ─────
def resync_my_lower_cup_registration(p=None, year=None):
    """[2026-09 신설] cup_engine.resync_my_cup_registration의 3·4부컵판 —
    같은 결함, 같은 해법이다(그쪽 docstring에 원인 전체를 적어뒀다).

    이 대회는 28주차(하반기 시작) 개막이라 여름 이적에는 안 걸리지만,
    같은 28주차에 겨울 이적시장(run_ai_mid_season_transfer)이 함께 돌고
    플레이어도 그 창구에서 팀을 옮길 수 있으므로 시점이 정확히 겹친다.
    이적하면 새 팀 경기가 is_my=1로 찍히는데(라운드 생성 시 현재팀 기준)
    get_my_lower_cup_match는 대회 단위 my_team_id로 판정해서 안 넘겨주고,
    process_lower_cup_week는 is_my=1이면 건너뛰므로 아무도 진행시킬 수
    없는 고아 경기가 되어 그 라운드에서 대회가 멈춘다.
    """
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if not p:
        return False
    if year is None:
        st = get_state()
        if not st:
            return False
        year = st["current_year"]
    my_tid = p.get("current_team_id", 0) or 0

    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, my_in, my_team_id FROM lower_cup_tournaments WHERE year=?",
        (year,)).fetchall()]
    if not rows:
        conn.close()
        return False
    entered = set()
    if my_tid:
        entered = {r["tournament_id"] for r in conn.execute(
            "SELECT tournament_id FROM lower_cup_entries WHERE team_id=?",
            (my_tid,)).fetchall()}
    changed = False
    c = conn.cursor()
    for t in rows:
        want = my_tid if (my_tid and t["id"] in entered) else 0
        if (t["my_team_id"] or 0) == want:
            continue
        c.execute("UPDATE lower_cup_tournaments SET my_team_id=?, my_in=? WHERE id=?",
                  (want, 1 if want else 0, t["id"]))
        c.execute("""UPDATE lower_cup_matches
                     SET is_my = (CASE WHEN home_team_id=? OR away_team_id=? THEN 1 ELSE 0 END),
                         my_team_id = (CASE WHEN home_team_id=? OR away_team_id=? THEN ? ELSE 0 END)
                     WHERE tournament_id=? AND home_score=-1""",
                  (want, want, want, want, want, t["id"]))
        changed = True
    if changed:
        conn.commit()
    conn.close()
    return changed


def process_lower_cup_week(week):
    # [2026-09 신설] 겨울 이적으로 소속팀이 바뀌었으면 먼저 등록을 맞춘다.
    try:
        resync_my_lower_cup_registration()
    except Exception as _e:
        print("[LOWERCUP] resync 실패(건너뜀):", _e, flush=True)
    from game_engine import get_player
    _p = get_player()
    _my_tid_now = _p.get("current_team_id", 0) if _p else 0

    conn = get_conn(); c = conn.cursor()
    matches = c.execute(
        "SELECT * FROM lower_cup_matches WHERE week=? AND home_score=-1", (week,)).fetchall()
    cols = [d[0] for d in c.description] if matches else []
    by_tournament = {}
    for row in matches:
        m = dict(zip(cols, row))
        by_tournament.setdefault(m["tournament_id"], []).append(m)

    # [2026-09 안전망, 신민용 확정 "C안"] 대회별 등록팀을 미리 읽어둔다 —
    # is_my=1인데 등록팀도 현재팀도 아닌 경기는 플레이어가 뛸 방법이 없어
    # (get_my_lower_cup_match가 등록팀≠현재팀이면 None) 대회 전체가 영구히
    # 멈춘다. 위 resync가 그런 상태를 안 만들지만, 한 번이라도 생기면
    # 치명적이라 실행 시점에도 한 겹 더 막는다.
    _reg_by_tid = {}
    if by_tournament:
        _ph = ",".join("?" * len(by_tournament))
        _reg_by_tid = {r[0]: (r[1] or 0) for r in c.execute(
            f"SELECT id, my_team_id FROM lower_cup_tournaments WHERE id IN ({_ph})",
            tuple(by_tournament.keys())).fetchall()}

    for tid, mlist in by_tournament.items():
        for m in mlist:
            if m["is_my"]:
                _reg = _reg_by_tid.get(tid, 0)
                _h, _a = m["home_team_id"], m["away_team_id"]
                if _reg and (_h == _reg or _a == _reg):
                    continue   # 정상: 플레이어가 뛸 경기(별도 훅이 처리)
                if _my_tid_now and (_h == _my_tid_now or _a == _my_tid_now):
                    continue   # 정상: 지금 내 팀 경기
                print(f"[LOWERCUP-ORPHAN] tournament_id={tid} match_id={m['id']} "
                      f"week={week} is_my=1인데 등록팀({_reg})/현재팀({_my_tid_now}) "
                      f"모두 불일치 — AI로 진행", flush=True)
            _sim_ai_lower_cup_match(c, m)
        _advance_lower_cup_round(c, tid, week)
    conn.commit(); conn.close()


def _team_ovr(c, team_id):
    from game_engine import _team_avg_ovr
    return _team_avg_ovr(c, team_id)


def _sim_ai_lower_cup_match(c, m):
    h_ovr = _team_ovr(c, m["home_team_id"])
    a_ovr = _team_ovr(c, m["away_team_id"])
    outcome = _match_outcome(h_ovr, a_ovr)
    if outcome == "home":
        hs, as_ = random.choice([1, 2, 2, 3]), random.randint(0, 1)
    elif outcome == "away":
        hs, as_ = random.randint(0, 1), random.choice([1, 2, 2, 3])
    else:
        hs = as_ = random.choice([0, 1, 1, 2])
    pso_winner, pso_score = 0, ""
    if hs == as_:
        winner_home, score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if winner_home else m["away_team_id"]
        pso_score = score
    c.execute("""UPDATE lower_cup_matches SET home_score=?, away_score=?,
                 pso_winner=?, pso_score=? WHERE id=?""",
              (hs, as_, pso_winner, pso_score, m["id"]))
    loser_id = m["away_team_id"] if (hs > as_ or pso_winner == m["home_team_id"]) else m["home_team_id"]
    c.execute("UPDATE lower_cup_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
              (m["tournament_id"], loser_id))


def _advance_lower_cup_round(c, tid, week):
    """이번 주에 걸려있던 모든 경기가 끝났으면 다음 라운드(또는 우승 확정)를 만든다.
    내 팀 경기(is_my=1)는 실제 매치엔진이 별도로 home_score/away_score를 채워야만
    이 함수가 넘어간다 — 아직 안 채워졌으면(-1) 이번 주는 대기.

    [2026-09 신설, 신민용 요청: "3부/4부컵도 4강 이후 3/4위전이 있어야
    한다"] cup_engine._advance_round의 is_sf 분기와 완전히 같은 원리로
    3/4위전을 추가한다. 이 대회는 대회 시작 시점(_cup_bye_count)에 부전승을
    한 라운드에 몰아줘서 그 이후로는 항상 정확히 절반씩만 줄어들도록
    설계돼 있다(문서 참고) — 그래서 "이번에 끝난 라운드 이후 생존자가
    정확히 2명"이면, 그 직전 라운드는 반드시 4강(4명 참가 → 2명 생존)이었다는
    뜻이 자동으로 성립한다(cup_engine처럼 별도로 "이 라운드가 4강이냐"를
    라운드 이름이나 참가 인원으로 다시 확인할 필요가 없다). 이 경우 그
    라운드에서 진 2팀(이번 주 매치들의 패자)을 모아, 새로 만들 결승과
    같은 주차에 3/4위전 매치를 하나 더 만든다 — world_browser.py의
    _batch_lower_cup_placements/get_lower_cup_tournament_detail은 이미
    round_name='3·4위전' 매치를 읽어서 3·4위를 표시하도록 짜여 있었으므로
    (지금까지는 이 매치 자체가 안 생겨서 항상 '-'로 떴다), 여기서 매치를
    만들기만 하면 화면 표시까지 그대로 이어진다."""
    still_pending = c.execute(
        "SELECT COUNT(*) FROM lower_cup_matches WHERE tournament_id=? AND week=? AND home_score=-1",
        (tid, week)).fetchone()[0]
    if still_pending:
        return

    trow = c.execute("SELECT * FROM lower_cup_tournaments WHERE id=?", (tid,)).fetchone()
    tcols = [d[0] for d in c.description]
    t = dict(zip(tcols, trow))

    survivors = [r[0] for r in c.execute(
        "SELECT team_id FROM lower_cup_entries WHERE tournament_id=? AND alive=1", (tid,)).fetchall()]
    if len(survivors) <= 1:
        winner = survivors[0] if survivors else 0
        c.execute("UPDATE lower_cup_tournaments SET status='done', winner_team_id=? WHERE id=?",
                   (winner, tid))
        return

    next_round_idx = t["round_counter"] + 1
    used_weeks = LOWER_CUP_ROUND_WEEKS_POOL
    idx_in_pool = used_weeks.index(week) + 1 if week in used_weeks else len(used_weeks)
    next_week = used_weeks[idx_in_pool] if idx_in_pool < len(used_weeks) else week + 2

    # [3/4위전] 방금 끝난 라운드가 4강이었으면(생존자가 2명으로 줄었으면),
    # 그 라운드의 패자 2명으로 결승과 같은 주차에 3/4위전을 만든다. 이미
    # 만들어져 있으면(이 함수가 같은 주차 전환에 대해 중복 호출된 경우)
    # 다시 만들지 않는다.
    if len(survivors) == 2:
        already_tp = c.execute(
            "SELECT COUNT(*) FROM lower_cup_matches WHERE tournament_id=? AND round_name='3·4위전'",
            (tid,)).fetchone()[0]
        if not already_tp:
            sf_rows = c.execute(
                "SELECT * FROM lower_cup_matches WHERE tournament_id=? AND week=?",
                (tid, week)).fetchall()
            sf_cols = [d[0] for d in c.description]
            sf_matches = [dict(zip(sf_cols, r)) for r in sf_rows]
            losers = []
            for m in sf_matches:
                w = _winner_of(m)
                losers.append(m["away_team_id"] if w == m["home_team_id"] else m["home_team_id"])
            if len(losers) == 2:
                tp_day = week_to_day(next_week) + _ROUND_DAY_OFFSET
                is_my_tp = 1 if (t["my_in"] and t["my_team_id"] in losers) else 0
                c.execute("""INSERT INTO lower_cup_matches
                             (tournament_id, round_name, round_idx, week, day,
                              home_team_id, away_team_id, is_my, slot)
                             VALUES (?,?,?,?,?,?,?,?,999)""",
                          (tid, "3·4위전", next_round_idx, next_week, tp_day,
                           losers[0], losers[1], is_my_tp))
                if t["my_in"]:
                    from competition.cup_engine import _my_country_id
                    from game_engine import add_log, get_player
                    _p = get_player()
                    if _p and t["country_id"] == _my_country_id(_p):
                        he = _lower_entry(tid, losers[0])
                        ae = _lower_entry(tid, losers[1])
                        add_log(f"🥉 {t['name']} 3/4위전: {he['team_name']} vs {ae['team_name']}",
                                "event")

    # seed_rank 순으로 살아남은 팀을 다시 정렬해서 브래킷 시딩 유지
    rows = c.execute(
        """SELECT team_id, seed_rank FROM lower_cup_entries
           WHERE tournament_id=? AND alive=1 ORDER BY seed_rank""", (tid,)).fetchall()
    ids = [r[0] for r in rows]
    _create_ko_round(c, tid, ids, round_idx=next_round_idx, week=next_week,
                      my_team_id=(t["my_team_id"] if t["my_in"] else None))
    c.execute("UPDATE lower_cup_tournaments SET round_counter=? WHERE id=?",
               (next_round_idx, tid))


# ══════════════════════════════════════════════════════════════
# ── 2단계: 내 팀 경기 연결 (get_my_*/simulate_my_*) ──────────────
# center_panel._get_match_for_day, game_engine.advance_days의 dispatch
# 체인(detail.get("cup")/detail.get("lower_cup") 분기), career_window/
# retire_window/schedule_window가 전부 이 함수들에 의존한다.
#
# [설계 메모] cup_engine.simulate_my_cup_match를 거의 그대로 미러링했다
# (전술엔진 연동/성격 보너스/카드·출전정지·벤치 체크/_save_match_detail
# 저장까지 동일 패턴). 딱 하나 의도적으로 다르게 둔 지점: 출전정지
# 카운터를 cup_suspension 필드에 그대로 합산한다 — 즉 국내컵과 이
# 대회가 "퇴장 시 다음 경기 결장" 카운터를 공유한다. 두 대회를 완전히
# 분리하려면 players 테이블에 lower_cup_suspension 컬럼을 새로 추가하고
# _check_suspended/_roll_card_events 호출부만 그 필드로 바꾸면 되는데,
# 이건 DB 스키마 변경(마이그레이션)이 필요해 이번 패치 범위 밖으로
# 남겨뒀다 — 원하면 알려주면 그 마이그레이션까지 만들어줄게.
# ══════════════════════════════════════════════════════════════

_lower_entry_cache: dict = {}


def _invalidate_lower_cup_engine_caches():
    _lower_entry_cache.clear()


def _lower_entry(tid, team_id):
    _key = (tid, team_id)
    _cached = _lower_entry_cache.get(_key)
    if _cached is not None:
        return _cached
    conn = get_conn()
    r = conn.execute("SELECT * FROM lower_cup_entries WHERE tournament_id=? AND team_id=?",
                      (tid, team_id)).fetchone()
    conn.close()
    result = dict(r) if r else {"team_name": "?", "ovr": 60, "tier": None}
    if r:
        _lower_entry_cache[_key] = result
    return result


def _my_lower_cup_tournament(p, year):
    from competition.cup_engine import _my_country_id
    cid = _my_country_id(p)
    if not cid:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM lower_cup_tournaments WHERE year=? AND country_id=?",
        (year, cid)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_my_lower_cup_match(week, day=None, p=None, st=None):
    """이번 주차(또는 특정 day)에 내가 뛸 3·4부컵 경기가 있으면 dict, 없으면 None.
    cup_engine.get_my_cup_match과 같은 규약(반환 dict에 "lower_cup": True를
    심어서 advance_days/formation_widget이 이 대회로 인식하게 한다).
    [Phase1과 차이] lower_cup_matches.day는 항상 실제 값이 채워져 있으므로
    (cup_matches처럼 day=0 폴백을 봐줄 필요가 없다) day가 주어지면 정확히
    그 날짜로만 매치한다."""
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
    t = _my_lower_cup_tournament(p, st["current_year"])
    if not t or t["status"] == "done":
        return None
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != tid:
        return None

    conn = get_conn()
    if day is not None:
        m = conn.execute(
            """SELECT * FROM lower_cup_matches
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?) AND day=?""",
            (t["id"], week, tid, tid, day)).fetchone()
    else:
        m = conn.execute(
            """SELECT * FROM lower_cup_matches
               WHERE tournament_id=? AND week=? AND home_score=-1
                 AND (home_team_id=? OR away_team_id=?)""",
            (t["id"], week, tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    oe = conn.execute(
        "SELECT team_name, tier FROM lower_cup_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], opp_id)).fetchone()
    conn.close()
    return {
        "lower_cup": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "opp_tier": oe["tier"] if oe else None,
        "league_name": t["name"],
        "round_name": m["round_name"],
        "opp": oe["team_name"] if oe else "?",
        "is_home": is_home,
        "week": week,
    }


def get_my_lower_cup_matches():
    """내가 실제 출전한 3·4부컵 경기 목록(시간순) — cup_engine.get_my_cup_matches와
    100% 동일한 필드 셰이프(연도/기간/대회/라운드/상대/골/어시/선방/실점/평점/
    스코어/결과/결장사유/my_played) — career_window._cup_tab/retire_window.
    _cup_table가 그대로 재사용 가능하도록 맞췄다."""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        """SELECT m.*, t.year AS t_year, t.name AS comp, t.my_team_id AS t_my_tid
           FROM lower_cup_matches m
           JOIN lower_cup_tournaments t ON m.tournament_id = t.id
           WHERE m.is_my = 1 AND m.home_score >= 0
           ORDER BY t.year, m.week""").fetchall()]

    tids = {m["tournament_id"] for m in rows}
    entries = {}
    if tids:
        ph = ",".join("?" * len(tids))
        for r in conn.execute(
                f"SELECT * FROM lower_cup_entries WHERE tournament_id IN ({ph})",
                tuple(tids)).fetchall():
            entries[(r["tournament_id"], r["team_id"])] = dict(r)
    conn.close()

    def _entry_lookup(tid, team_id):
        return entries.get((tid, team_id), {"team_name": "?", "ovr": 60})

    out = []
    for m in rows:
        # [2026-09 수정] 경기 행의 "그 경기 당시 내 팀" 우선, 없으면(옛 행)
        # 대회 값으로 폴백 — cup_engine.get_my_cup_matches와 동일.
        my_tid = m.get("my_team_id") or m["t_my_tid"]
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


def sim_my_lower_cup_match_as_ai(week, p, reason="injury", day=None):
    """부상 등으로 내가 못 뛸 때 내 3·4부컵 경기를 AI끼리(내 보너스 없이)
    시뮬레이션 — cup_engine.sim_my_cup_match_as_ai와 동일 패턴."""
    info = get_my_lower_cup_match(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM lower_cup_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM lower_cup_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()
    if m["home_score"] != -1:
        return  # 이미 처리됨(멱등)
    _sim_ai_lower_cup_match(get_conn().cursor(), m)
    get_conn().commit()
    from game_engine import update_player, _calc_manager_rel
    update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False, not_played_penalty=2))
    _advance_after_my_match(t["id"], week)


def _advance_after_my_match(tournament_id, week):
    """내 경기가 방금 결과가 났으니, 같은 주 나머지(AI끼리) 경기까지 마저
    처리하고 라운드를 진행시킨다 — process_lower_cup_week가 is_my=1은
    건너뛰므로, 내 경기 쪽(simulate_my_lower_cup_match/AI대체)이 직접
    이 마무리를 불러줘야 한다."""
    conn = get_conn(); c = conn.cursor()
    remaining = c.execute(
        """SELECT * FROM lower_cup_matches
           WHERE tournament_id=? AND week=? AND home_score=-1 AND is_my=0""",
        (tournament_id, week)).fetchall()
    cols = [d[0] for d in c.description] if remaining else []
    for row in remaining:
        _sim_ai_lower_cup_match(c, dict(zip(cols, row)))
    _advance_lower_cup_round(c, tournament_id, week)
    conn.commit(); conn.close()


def simulate_my_lower_cup_match(week, p, day=None):
    """내가 출전하는 3·4부컵 경기. cup_engine.simulate_my_cup_match를
    최대한 그대로 미러링(전술엔진/성격보너스/카드·출전정지·벤치/매치
    상세 저장까지) — 표시 마커만 ":lower_cup"으로 바꿔서 로그/커리어창이
    이 대회를 별도 색(#00A6A6)·별도 탭으로 구분하게 한다."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _check_bench, _roll_red_card,
                             _apply_red_card_dismissal,
                             _roll_card_events, _day_label)
    from constants import PERSONALITY_EFFECTS
    info = get_my_lower_cup_match(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM lower_cup_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM lower_cup_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    he = _lower_entry(t["id"], m["home_team_id"])
    ae = _lower_entry(t["id"], m["away_team_id"])
    is_home = info["is_home"]

    # 출전정지 체크 — [설계 메모] cup_suspension 필드를 국내컵과 공유(파일
    # 상단 설계 메모 참고).
    _suspended, _new_susp = _check_suspended(p, field="cup_suspension")
    if _suspended:
        update_player(cup_suspension=_new_susp)
        add_log(f"🟥 출전정지로 결장{'  (다음 경기부터 복귀)' if _new_susp == 0 else f'  (남은 정지 {_new_susp}경기)'}",
                "event")

    _my_ovr = p.get("ovr", 40)
    _team_ovr = he["ovr"] if is_home else ae["ovr"]
    _gap = max(0.0, _my_ovr - _team_ovr)
    _benched = (not _suspended) and _check_bench(p, team_avg_ovr=_team_ovr)
    if _benched:
        add_log("🪑 벤치 대기로 결장", "event")
    _star = 1.0 + max(0.0, (_my_ovr - 60) / 40.0) ** 1.8 * 3.0
    bonus = _gap * 0.30 * _star + max(0.0, _my_ovr - 50) * 0.08
    bonus = _soft_cap(bonus, 30.0)
    _pe = PERSONALITY_EFFECTS.get(p.get("personality", ""), {})
    if "team_win_bonus" in _pe:
        bonus *= (1.0 + _pe["team_win_bonus"])
    if _suspended or _benched:
        bonus = 0.0
    h_ovr = he["ovr"] + (bonus if is_home else 0)
    a_ovr = ae["ovr"] + (0 if is_home else bonus)

    my_position = p.get("position", "")
    engine_stats = None
    engine_plog = None
    player_ratings = None
    try:
        from match_sim.tactical_engine import simulate_my_match
        from game_engine import _team_formation
        _fconn = get_conn()
        _c = _fconn.cursor()
        home_formation = _team_formation(_c, m["home_team_id"])
        away_formation = _team_formation(_c, m["away_team_id"])
        _fconn.close()
        sim = simulate_my_match(
            m["home_team_id"], m["away_team_id"], home_formation, away_formation,
            home_boost=(bonus if is_home else 0.0),
            away_boost=(bonus if not is_home else 0.0),
            home_boost_position=(my_position if is_home else None),
            away_boost_position=(my_position if not is_home else None),
            home_adv=0.0)
        hs, as_ = sim["home_score"], sim["away_score"]
        engine_stats = {"home": sim["home_stats"], "away": sim["away_stats"]}
        engine_plog = sim["possession_log"]
        player_ratings = {"home": sim.get("home_player_ratings") or [],
                          "away": sim.get("away_player_ratings") or []}
        outcome = "draw" if hs == as_ else ("home" if hs > as_ else "away")
    except Exception:
        outcome = _match_outcome(h_ovr, a_ovr)
        hs, as_ = _gen_score(outcome, h_ovr - a_ovr)

    pso_winner, pso_score = 0, ""
    if outcome == "draw":
        win_home, pso_score = _resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]

    if _suspended or _benched:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
        _absence_reason = "suspension" if _suspended else None
        _yellow_cnt = 0
    else:
        _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr)
        _absence_reason = None
        _dismissed, _card_reason, _yellow_ev, _yellow_cnt = _roll_card_events(p, "cup_suspension")
        if _dismissed:
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(
                p, field="cup_suspension", reason=_card_reason)
            _absence_reason = _card_reason
        elif _yellow_ev:
            events = list(events) + _yellow_ev
    if not (_suspended or _benched) and "cup_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["cup_rating"], 1)))
    if m.get("round_name") == "결승" and not (_suspended or _benched) and "big_match_rating" in _pe:
        rating = max(3.0, min(10.0, round(rating + _pe["big_match_rating"], 1)))

    if player_ratings is not None:
        _side_key = "home" if is_home else "away"
        _my_list = player_ratings.get(_side_key)
        if _my_list:
            _labels = [r.get("position") if r else None for r in _my_list]
            _idx = None
            for _i, _lab in enumerate(_labels):
                if _lab == my_position:
                    _idx = _i; break
            if _idx is None:
                from constants import POSITION_COMPAT
                for _want in POSITION_COMPAT.get(my_position, [my_position]):
                    for _i, _lab in enumerate(_labels):
                        if _lab == _want:
                            _idx = _i; break
                    if _idx is not None:
                        break
            if _idx is None:
                for _i, _lab in enumerate(_labels):
                    if _lab is not None and _lab != "GK":
                        _idx = _i; break
            if _idx is not None:
                _my_list[_idx] = {
                    "id": None, "name": p.get("name") or "나",
                    "position": _labels[_idx], "ovr": p.get("ovr", 40),
                    "goals": goals, "assists": assists,
                    "shots": detail.get("shots", 0),
                    "shots_on": detail.get("shots_on", 0),
                    "saves": saves, "is_gk": (my_position == "GK"),
                    "rating": rating, "is_me": True,
                }

    my_result = _my_result(outcome, is_home)

    if day is None:
        from game_engine import _week_intl_cl_day
        day = _week_intl_cl_day(week, p)

    conn = get_conn()
    conn.execute("""UPDATE lower_cup_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?, my_played=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?, day=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score, 0 if (_suspended or _benched) else 1,
                  saves, goals, assists, rating, day, m["id"]))
    # [버그수정, 테스트 중 발견] _sim_ai_lower_cup_match는 패자를 alive=0으로
    # 표시하는데, 이 함수(내 팀 경기)는 그걸 빼먹어서 내가 진 경기 이후에도
    # 내 팀이 계속 다음 라운드에 남아있는 버그가 있었다 — 여기서도 동일하게
    # 패자를 탈락 처리한다.
    loser_id = m["away_team_id"] if (hs > as_ or pso_winner == m["home_team_id"]) else m["home_team_id"]
    conn.execute("UPDATE lower_cup_entries SET alive=0 WHERE tournament_id=? AND team_id=?",
                 (t["id"], loser_id))
    conn.commit()
    conn.close()

    _update_pop(p, goals, assists, rating)
    p2 = get_player()
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
        events, not (_suspended or _benched), _benched, detail, pso=pso,
        engine_stats=engine_stats, engine_plog=engine_plog, player_ratings=player_ratings)
    marker = f" [match:{detail_id}:lower_cup]" if detail_id else ""

    add_log("─" * 44, "sep")
    add_log(f"🏅 {comp_name}  {_day_label(week, day)}{marker}", "match")
    add_log(f"   {home_disp} {hs}-{as_} {away_disp}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")

    _advance_after_my_match(t["id"], week)