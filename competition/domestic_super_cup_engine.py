# -*- coding: utf-8 -*-
"""
domestic_super_cup_engine.py — 국내 슈퍼컵 (2026-09 신설)

[신민용 확정 설계]
대륙 슈퍼컵(super_cup_engine.py)과 별개로, "그 나라" 단위로 매년 여는
국내 슈퍼컵. Y년 대회는 (Y-1)년 그 나라 1부 리그 우승팀 vs (Y-1)년
국내컵(cup_engine.py) 우승팀 — 컵 우승팀이 2부 이하 팀이어도 그대로
붙인다(실제로 흔한 일: 이 대회는 "누가 컵을 들었나"만 본다).
둘이 같은 팀(리그 우승팀이 컵도 들었을 때)이면 컵 준우승팀으로
대체한다. (Y-1)년 데이터 자체가 없는 해(게임 시작 연도 등, 리그
시즌 요약이 아예 없음)에만 그 리그 1부 OVR 1·2위로 대체한다 — 이건
"리그가 있는데 컵 결과만 없다" 같은 부분적 결손이 아니라 "그 전년도가
통째로 없다"는 신호(_season_for_year가 None)일 때만 켜진다.

4주차(FIRST_HALF_START, 국내리그가 시작하는 바로 그 주)에 배정한다 —
리그와 달리 이 주엔 이미 각 팀의 리그 경기일이 정해져 있으므로,
_pick_dsc_day가 내 팀 기준으로 그 주 리그 경기일과 안 겹치는 요일을
고른다(super_cup_engine._pick_sc_days와 동일한 원리).

단판이며 홈 어드밴티지가 없다("양팀 다 원정"으로 취급) — 기존
대회들의 match_outcome/_match_outcome은 diff=0이어도 홈 46%/원정 30%로
비대칭이라(중립 구장이라는 주석과 달리 실제로는 비대칭) 그대로 못
쓰고, 이 파일 전용 _neutral_match_outcome/_neutral_resolve_pso를 새로
둔다(기울기 0.022는 다른 대회와 동일하게 맞춰 이변 확률 감각만 유지).
상금·명성 보상·모멘텀·발롱도르 점수는 전혀 없다(신민용 확정: "상같은건
없어") — 그래서 competition_common.finish_tournament/record_my_exit/
save_trophy(전부 보상을 얹어준다)를 그대로 재사용하지 않고 이 파일
전용의 무보상 버전을 둔다. trophy_log는 tier=-4로 구분(-1=대륙급
클럽대항전·대륙슈퍼컵, -2=국내컵, -3=클럽월드컵과 안 겹치는 새 값).

[재사용 vs 신규 판단] competition_common의 entry()/get_my_matches()는
continent 개념이 전혀 없는 순수 cfg 기반 함수라 그대로 재사용한다.
반면 get_tournament()/my_tournament()/sim_ai_match()/simulate_my_match()
등은 continent 컬럼 조회나 홈 어드밴티지가 있는 outcome 함수에 묶여
있어 이 대회(나라 단위 스코프, 무홈어드밴티지)엔 안 맞으므로, cup_
engine.py의 실제 구현(simulate_my_cup_match 등)을 템플릿 삼아 이
파일 전용으로 새로 쓴다. 다만 전술엔진(match_sim.tactical_engine)
연동까지는 이번 범위에 넣지 않았다 — cup_engine.simulate_my_cup_match의
tactical_engine 시도/실패 시 폴백 중 "폴백"(_player_perf 기반) 쪽만
써서, 연 1회뿐인 이 대회에서도 개인 골/어시/평점은 정상 반영되게
하면서 복잡도는 낮춘다.

[구현 범위 안내] 이 파일은 대회 생성/진행/개인기록/스케줄 조회까지
포함해 실제로 플레이 가능한 상태다. 다만 세계 축구 기록실(world_browser)
쪽 UI(팀/선수 기록표 칸 추가, "컵대회 검색" 탭 3번째 필터, 복사
텍스트 반영)와 career_window/retire_window 탭 추가는 별도 후속
작업이다 — 이 파일의 get_my_domestic_sc_matches()가 그 UI들이 그대로
가져다 쓸 수 있는 형태(super_cup_engine.get_my_sc_matches()와 동일한
모양)로 이미 준비돼 있다.
"""
import random

from database import get_conn
from constants import week_to_day, FIRST_HALF_START
from competition.competition_common import (
    CompetitionConfig, entry, clear_entry_cache, winner_of,
    league_day_map, pick_free_day,
)

DOMESTIC_SC_WEEK = FIRST_HALF_START   # 4주차 — 국내리그가 시작하는 그 주

# [사용자 제공 목록, 2026-09] 실제 국가별 대회명. data/countries.py의
# 211개 나라 전부와 정확히 일치 확인됨(스크립트로 대조 완료) — 그래도
# 나중에 나라가 추가되는 등 여기 없는 나라가 생기면 "{국가명} 슈퍼컵"
# 폴백으로 자연스럽게 이어진다(cup_engine.CUP_NAME_BY_COUNTRY와 동일한
# 방어 패턴, 이 표 대부분이 이미 그 패턴이라 폴백이 튀지도 않는다).
DOMESTIC_SC_NAME_BY_COUNTRY = {
    '아르헨티나': '수페르코파 아르헨티나',
    '프랑스': '트로페 데 샹피옹',
    '스페인': '수페르코파 데 에스파냐',
    '잉글랜드': 'FA 커뮤니티 실드',
    '브라질': '수페르코파 두 브라질',
    '모로코': '모로코 슈퍼컵',
    '네덜란드': '요한 크루이프 실드',
    '독일': '프란츠 베켄바워 슈퍼컵',
    '포르투갈': '수페르타사 칸디두 드 올리베이라',
    '벨기에': '벨기에 슈퍼컵',
    '멕시코': '캄페온 데 캄페오네스',
    '콜롬비아': '수페르리가 콜롬비아나',
    '미국': 'US 슈퍼컵',
    '이탈리아': '수페르코파 이탈리아나',
    '크로아티아': '크로아티아 슈퍼컵',
    '세네갈': '세네갈 슈퍼컵',
    '일본': '후지필름 슈퍼컵',
    '우루과이': '수페르코파 우루과야',
    '스위스': '스위스 슈퍼컵',
    '덴마크': '덴마크 슈퍼컵',
    '오스트리아': '오스트리아 슈퍼컵',
    '이란': '이란 슈퍼컵',
    '나이지리아': '나이지리아 슈퍼컵',
    '호주': '호주 슈퍼컵',
    '노르웨이': '메스터피날렌',
    '캐나다': '캐나다 슈퍼컵',
    '이집트': '이집트 슈퍼컵',
    '알제리': '알제리 슈퍼컵',
    '에콰도르': '수페르코파 에콰도르',
    '대한민국': 'K리그 슈퍼컵',
    '코트디부아르': '쿠프 우푸에부아니',
    '튀르키예': '튀르키예 슈퍼컵',
    '우크라이나': '우크라이나 슈퍼컵',
    '러시아': '러시아 슈퍼컵',
    '폴란드': '폴란드 슈퍼컵',
    '스웨덴': '스웨덴 슈퍼컵',
    '파라과이': '수페르코파 파라과이',
    '웨일스': '웨일스 슈퍼컵',
    '헝가리': '헝가리 슈퍼컵',
    '파나마': '파나마 슈퍼컵',
    '스코틀랜드': '스코티시 슈퍼컵',
    '세르비아': '세르비아 슈퍼컵',
    '콩고 민주 공화국': '콩고 슈퍼컵',
    '체코': '체코 슈퍼컵',
    '카메룬': '카메룬 슈퍼컵',
    '슬로바키아': '슬로바키아 슈퍼컵',
    '그리스': '그리스 슈퍼컵',
    '베네수엘라': '수페르코파 데 베네수엘라',
    '칠레': '수페르코파 데 칠레',
    '페루': '수페르코파 페루아나',
    '코스타리카': '수페르코파 데 코스타리카',
    '루마니아': '루마니아 슈퍼컵',
    '말리': '말리 슈퍼컵',
    '튀니지': '튀니지 슈퍼컵',
    '우즈베키스탄': '우즈베키스탄 슈퍼컵',
    '아일랜드': '아일랜드 슈퍼컵',
    '슬로베니아': '슬로베니아 슈퍼컵',
    '카타르': '셰이크 자심 컵',
    '사우디아라비아': '사우디 슈퍼컵',
    '이라크': '이라크 슈퍼컵',
    '남아프리카공화국': '남아공 슈퍼컵',
    '부르키나 파소': '부르키나 파소 슈퍼컵',
    '카보베르데': '카보베르데 슈퍼컵',
    '보스니아 헤르체고비나': '보스니아 슈퍼컵',
    '가나': '가나 슈퍼컵',
    '온두라스': '온두라스 슈퍼컵',
    '알바니아': '알바니아 슈퍼컵',
    '요르단': '요르단 슈퍼컵',
    '아랍에미리트': 'UAE 슈퍼컵',
    '북마케도니아': '북마케도니아 슈퍼컵',
    '북아일랜드': '북아일랜드 슈퍼컵',
    '자메이카': '자메이카 슈퍼컵',
    '조지아': '조지아 슈퍼컵',
    '아이슬란드': '아이슬란드 슈퍼컵',
    '핀란드': '핀란드 슈퍼컵',
    '이스라엘': '이스라엘 슈퍼컵',
    '볼리비아': '수페르코파 볼리비아',
    '코소보': '코소보 슈퍼컵',
    '오만': '오만 슈퍼컵',
    '몬테네그로': '몬테네그로 슈퍼컵',
    '퀴라소': '퀴라소 슈퍼컵',
    '기니': '기니 슈퍼컵',
    '뉴질랜드': '채리티 컵',
    '시리아': '시리아 슈퍼컵',
    '가봉': '가봉 슈퍼컵',
    '불가리아': '불가리아 슈퍼컵',
    '아이티': '아이티 슈퍼컵',
    '앙골라': '앙골라 슈퍼컵',
    '우간다': '우간다 슈퍼컵',
    '잠비아': '잠비아 슈퍼컵',
    '중국': '중국 FA 슈퍼컵',
    '바레인': '바레인 슈퍼컵',
    '베냉': '베냉 슈퍼컵',
    '태국': '타이랜드 챔피언스 컵',
    '팔레스타인': '팔레스타인 슈퍼컵',
    '벨라루스': '벨라루스 슈퍼컵',
    '과테말라': '과테말라 슈퍼컵',
    '룩셈부르크': '룩셈부르크 슈퍼컵',
    '베트남': '베트남 슈퍼컵',
    '엘살바도르': '엘살바도르 슈퍼컵',
    '타지키스탄': '타지키스탄 슈퍼컵',
    '트리니다드 토바고': '트리니다드 토바고 채리티 실드',
    '모잠비크': '모잠비크 슈퍼컵',
    '마다가스카르': '마다가스카르 슈퍼컵',
    '적도 기니': '적도 기니 슈퍼컵',
    '키르기스스탄': '키르기스스탄 슈퍼컵',
    '아르메니아': '아르메니아 슈퍼컵',
    '코모로': '코모로 슈퍼컵',
    '케냐': '케냐 슈퍼컵',
    '리비아': '리비아 슈퍼컵',
    '카자흐스탄': '카자흐스탄 슈퍼컵',
    '탄자니아': '탄자니아 커뮤니티 실드',
    '모리타니': '모리타니 슈퍼컵',
    '니제르': '니제르 슈퍼컵',
    '레바논': '레바논 슈퍼컵',
    '감비아': '감비아 슈퍼컵',
    '수단': '수단 슈퍼컵',
    '인도네시아': '인도네시아 슈퍼컵',
    '토고': '토고 슈퍼컵',
    '북한': '조선 슈퍼컵',
    '나미비아': '나미비아 슈퍼컵',
    '시에라리온': '시에라리온 슈퍼컵',
    '페로 제도': '페로 제도 슈퍼컵',
    '키프로스': '키프로스 슈퍼컵',
    '수리남': '수리남 프레지던츠 컵',
    '아제르바이잔': '아제르바이잔 슈퍼컵',
    '에스토니아': '에스토니아 슈퍼컵',
    '르완다': '르완다 슈퍼컵',
    '말라위': '말라위 슈퍼컵',
    '짐바브웨': '짐바브웨 슈퍼컵',
    '니카라과': '니카라과 슈퍼컵',
    '기니비사우': '기니비사우 슈퍼컵',
    '쿠웨이트': '쿠웨이트 슈퍼컵',
    '콩고 공화국': '콩고 슈퍼컵',
    '필리핀': '필리핀 슈퍼컵',
    '말레이시아': '술탄 하지 아흐마드 샤 컵',
    '라트비아': '라트비아 슈퍼컵',
    '인도': '인도 슈퍼컵',
    '중앙아프리카공화국': '중앙아프리카공화국 슈퍼컵',
    '라이베리아': '라이베리아 슈퍼컵',
    '투르크메니스탄': '투르크메니스탄 슈퍼컵',
    '부룬디': '부룬디 슈퍼컵',
    '에티오피아': '에티오피아 슈퍼컵',
    '도미니카 공화국': '도미니카 공화국 슈퍼컵',
    '예멘': '예멘 슈퍼컵',
    '레소토': '레소토 슈퍼컵',
    '보츠와나': '보츠와나 슈퍼컵',
    '싱가포르': '싱가포르 커뮤니티 실드',
    '리투아니아': '리투아니아 슈퍼컵',
    '가이아나': '가이아나 슈퍼컵',
    '뉴칼레도니아': '뉴칼레도니아 슈퍼컵',
    '세인트키츠 네비스': '세인트키츠 네비스 슈퍼컵',
    '솔로몬 제도': '솔로몬 제도 슈퍼컵',
    '푸에르토리코': '푸에르토리코 슈퍼컵',
    '피지': '피지 슈퍼컵',
    '홍콩': '홍콩 슈퍼컵',
    '타히티': '타히티 쿠프 데 샹피옹',
    '미얀마': '미얀마 슈퍼컵',
    '몰도바': '몰도바 슈퍼컵',
    '바누아투': '바누아투 슈퍼컵',
    '몰타': '몰타 슈퍼컵',
    '앤티가 바부다': '앤티가 바부다 슈퍼컵',
    '그레나다': '그레나다 슈퍼컵',
    '쿠바': '쿠바 슈퍼컵',
    '에스와티니': '에스와티니 슈퍼컵',
    '세인트루시아': '세인트루시아 슈퍼컵',
    '버뮤다': '버뮤다 슈퍼컵',
    '파푸아뉴기니': '파푸아뉴기니 슈퍼컵',
    '남수단': '남수단 슈퍼컵',
    '세인트빈센트 그레나딘': '세인트빈센트 그레나딘 슈퍼컵',
    '아프가니스탄': '아프가니스탄 슈퍼컵',
    '안도라': '안도라 슈퍼컵',
    '몰디브': '몰디브 FA 채리티 실드',
    '중화 타이베이': '대만 슈퍼컵',
    '캄보디아': '캄보디아 슈퍼컵',
    '몬트세라트': '몬트세라트 슈퍼컵',
    '네팔': '네팔 슈퍼컵',
    '모리셔스': '모리셔스 슈퍼컵',
    '바베이도스': '바베이도스 슈퍼컵',
    '벨리즈': '벨리즈 슈퍼컵',
    '방글라데시': '방글라데시 챌린지 컵',
    '도미니카 연방': '도미니카 슈퍼컵',
    '차드': '차드 슈퍼컵',
    '에리트레아': '에리트레아 슈퍼컵',
    '라오스': '라오스 슈퍼컵',
    '쿡 제도': '쿡 제도 슈퍼컵',
    '스리랑카': '스리랑카 슈퍼컵',
    '사모아': '사모아 슈퍼컵',
    '아루바': '아루바 슈퍼컵',
    '몽골': '몽골 슈퍼컵',
    '미국령 사모아': '미국령 사모아 슈퍼컵',
    '부탄': '부탄 슈퍼컵',
    '마카오': '마카오 슈퍼컵',
    '브루나이': '브루나이 슈퍼컵',
    '상투메 프린시페': '상투메 프린시페 슈퍼컵',
    '지부티': '지부티 슈퍼컵',
    '케이맨 제도': '케이맨 제도 슈퍼컵',
    '파키스탄': '파키스탄 슈퍼컵',
    '소말리아': '소말리아 슈퍼컵',
    '통가': '통가 슈퍼컵',
    '동티모르': '동티모르 슈퍼컵',
    '지브롤터': '지브롤터 슈퍼컵',
    '괌': '괌 슈퍼컵',
    '세이셸': '세이셸 슈퍼컵',
    '터크스 케이커스 제도': '터크스 케이커스 제도 슈퍼컵',
    '리히텐슈타인': '리히텐슈타인 컵 슈퍼컵',
    '바하마': '바하마 슈퍼컵',
    '미국령 버진아일랜드': '미국령 버진아일랜드 슈퍼컵',
    '영국령 버진아일랜드': '영국령 버진아일랜드 슈퍼컵',
    '앵귈라': '앵귈라 슈퍼컵',
    '산마리노': '수페르코파 삼마리네세',
}


def _domestic_sc_name(country_name):
    return DOMESTIC_SC_NAME_BY_COUNTRY.get(country_name, f"{country_name} 슈퍼컵")


# competition_common.entry()/get_my_matches()만 재사용하기 위한 최소
# 설정값 — momentum_type/round_weeks 등은 이 파일이 그 경로를 아예
# 안 타므로(모멘텀·보상 없음) 실질적으로 쓰이지 않지만, dataclass라
# 필드는 채워둬야 한다.
DSC_CFG = CompetitionConfig(
    match_table="domestic_sc_matches",
    entry_table="domestic_sc_entries",
    tournament_table="domestic_sc_tournaments",
    history_table="domestic_sc_history",
    competition_name_by_continent={},
    award_prefix="국내슈퍼컵",
    momentum_type="domestic_sc_champion",
    stage_ko={"F": ""},
    round_weeks={"F": DOMESTIC_SC_WEEK},
    league_weeks=(DOMESTIC_SC_WEEK, DOMESTIC_SC_WEEK),
    end_week=DOMESTIC_SC_WEEK,
    stage_order=["F"],
    suspension_field="domestic_sc_suspension",
)


# ─────────────────────────────────────────────
# 중립 구장 OVR 승부 공식 (양팀 다 "원정" — 홈 어드밴티지 없음)
# ─────────────────────────────────────────────

def _neutral_match_outcome(h_ovr, a_ovr):
    """다른 대회들의 match_outcome(diff=0에서도 홈 46%/원정 30%로
    비대칭)과 달리, diff=0이면 정확히 hw==aw가 되도록 대칭으로 설계.
    기울기(0.022)·무승부 폭(0.009)은 기존 대회들과 동일하게 맞춰서
    "OVR 차이에 따른 이변 확률" 감각만 그대로 유지한다."""
    diff = h_ovr - a_ovr
    dw = max(0.05, 0.24 - abs(diff) * 0.009)
    half = max(0.0, 1.0 - dw) / 2.0
    hw = max(0.02, min(0.96, half + diff * 0.022))
    aw = max(0.02, min(0.96, half - diff * 0.022))
    tot = hw + dw + aw
    hw, dw, aw = hw / tot, dw / tot, aw / tot
    roll = random.random()
    if roll < hw:
        return "home"
    elif roll < hw + dw:
        return "draw"
    return "away"


def _neutral_resolve_pso(h_ovr, a_ovr):
    p_home = 0.5 + max(-0.1, min(0.1, (h_ovr - a_ovr) * 0.010))
    winner_home = random.random() < p_home
    score = random.choice(["5-4", "4-3", "4-2", "3-2", "5-3"])
    return winner_home, score


# ─────────────────────────────────────────────
# 조회 헬퍼
# ─────────────────────────────────────────────

def get_domestic_sc_tournament(year, country_id):
    if not country_id:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM domestic_sc_tournaments WHERE year=? AND country_id=?",
        (year, country_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def _my_country_id(p):
    """cup_engine._my_country_id와 동일한 이유(무소속 시즌도 대표국적으로
    폴백)로 그대로 같은 로직을 쓴다."""
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


def _season_for_year(year):
    """이 연도의 season 번호 — league_season_standings에서 역산(하드코딩
    공식 대신 실제 데이터로 조회). 못 찾으면 None(전년도 데이터 자체가
    없다는 신호 — 게임 시작 연도 등)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT season FROM league_season_standings WHERE year=? LIMIT 1", (year,)).fetchone()
    conn.close()
    return row["season"] if row else None


def _team_snapshot(c, team_id):
    row = c.execute(
        """SELECT t.name AS team_name, cn.grade AS grade, cn.name AS country, cn.flag AS flag
           FROM teams t JOIN countries cn ON t.country_id=cn.id WHERE t.id=?""", (team_id,)).fetchone()
    if not row:
        return {"team_name": "?", "flag": "", "country": "", "grade": "", "ovr": 50}
    from game_engine import _team_avg_ovr
    return {"team_name": row["team_name"], "flag": row["flag"], "country": row["country"],
            "grade": row["grade"], "ovr": _team_avg_ovr(c, team_id) or 50}


# ─────────────────────────────────────────────
# 참가팀 결정
# ─────────────────────────────────────────────

def _get_domestic_sc_participants(year, country_id):
    """Y년 대회 참가팀 (home_team_id, away_team_id) — (Y-1)년 그 나라
    1부 리그 우승팀 vs (Y-1)년 국내컵 우승팀(2부 이하여도 그대로),
    둘이 같으면 컵 준우승팀으로 대체. (Y-1)년 리그 시즌 자체가 없으면
    (게임 시작 연도 등) 그 리그 OVR 1·2위로 대체. 뭘 해도 못 정하면
    None(그 해 그 나라는 조용히 건너뜀 — 다른 대회들과 동일한 방어
    패턴)."""
    prev_year = year - 1
    conn = get_conn()
    lg_row = conn.execute(
        "SELECT id FROM leagues WHERE country_id=? AND tier=1", (country_id,)).fetchone()
    conn.close()
    if not lg_row:
        return None
    league_id = lg_row["id"]

    season = _season_for_year(prev_year)
    if season is None:
        # 전년도 데이터 자체가 없음(게임 시작 연도 등) → 그 리그 OVR 1·2위.
        conn = get_conn(); c = conn.cursor()
        rows = c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,)).fetchall()
        ovr_list = sorted(
            ((r["id"], (_team_snapshot(c, r["id"])["ovr"])) for r in rows),
            key=lambda x: -x[1])
        conn.close()
        if len(ovr_list) < 2:
            return None
        return ovr_list[0][0], ovr_list[1][0]

    from game_engine import get_league_standings
    standings = get_league_standings(league_id, season=season)
    if not standings:
        return None
    league_champion = standings[0]["id"]

    conn = get_conn()
    ct = conn.execute(
        "SELECT id FROM cup_tournaments WHERE year=? AND country_id=? AND status='done'",
        (prev_year, country_id)).fetchone()
    conn.close()
    if not ct:
        return None
    conn = get_conn()
    fm = conn.execute(
        """SELECT * FROM cup_matches WHERE tournament_id=? AND round_name='결승'
           AND home_score>=0 ORDER BY id DESC LIMIT 1""", (ct["id"],)).fetchone()
    conn.close()
    if not fm:
        return None
    fm = dict(fm)
    winner = winner_of(fm)
    runner = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    away = runner if winner == league_champion else winner
    if not away or away == league_champion:
        return None
    return league_champion, away


# ─────────────────────────────────────────────
# 일정 배정 — 4주차, 내 팀 기준으로 그 주 리그 경기일과 안 겹치게
# ─────────────────────────────────────────────

def _pick_dsc_day(team_ids, year, conn=None):
    """그 주 리그 경기일과 하루 이내로는 안 겹치는 요일을 고른다.

    [2026-09 버그수정, 신민용 확정: "컵 대회랑 리그 일정은 절대 겹치면
    안돼"] 예전 시그니처는 _pick_dsc_day(my_tid, cur_season)이었고, 내
    팀이 이 대회와 무관하면(my_tid=0) 조회 자체를 건너뛰고 그 주 첫날을
    그대로 썼다 — 그 결과 211개 대회가 전부 같은 날에 몰렸고, 그중 23팀은
    같은 날 리그 경기도 갖고 있었다(2005시즌 실측). 이제 참가 두 팀
    모두를 기준으로 고른다. 후보가 전부 막히면 기존과 동일하게 그 주
    첫날로 폴백한다(신민용 확정: 다음 주로 미루지 않는다)."""
    week_start = week_to_day(DOMESTIC_SC_WEEK)
    _c = conn or get_conn()
    day_map = league_day_map(_c, year, DOMESTIC_SC_WEEK, team_ids)
    return pick_free_day(week_start, day_map, team_ids, week_start,
                         offsets=tuple(range(7)))


# ─────────────────────────────────────────────
# 생성
# ─────────────────────────────────────────────

def _build_domestic_sc(year, country_id):
    if get_domestic_sc_tournament(year, country_id):
        return   # 이미 생성됨

    participants = _get_domestic_sc_participants(year, country_id)
    if not participants:
        return
    home_id, away_id = participants

    conn = get_conn()
    country_row = conn.execute(
        "SELECT name FROM countries WHERE id=?", (country_id,)).fetchone()
    conn.close()
    if not country_row:
        return
    name = _domestic_sc_name(country_row["name"])

    from game_engine import get_player, get_state
    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    my_in = 1 if my_tid in (home_id, away_id) else 0
    st = get_state()
    cur_season = st["current_season"] if st else 1
    day = _pick_dsc_day((home_id, away_id), year)

    conn = get_conn(); c = conn.cursor()
    c.execute("""INSERT INTO domestic_sc_tournaments
                    (year, country_id, name, status, home_team_id, away_team_id,
                     my_in, my_team_id)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (year, country_id, name, "active", home_id, away_id,
               my_in, my_tid if my_in else 0))
    tid = c.lastrowid

    for team_id in (home_id, away_id):
        info = _team_snapshot(c, team_id)
        c.execute("""INSERT INTO domestic_sc_entries
                        (tournament_id, team_id, team_name, flag, country, grade, ovr)
                     VALUES(?,?,?,?,?,?,?)""",
                  (tid, team_id, info["team_name"], info["flag"], info["country"],
                   info["grade"], info["ovr"]))

    c.execute("""INSERT INTO domestic_sc_matches
                    (tournament_id, stage, week, day, home_team_id, away_team_id,
                     is_my, my_team_id)
                 VALUES(?,'F',?,?,?,?,?,?)""",
              (tid, DOMESTIC_SC_WEEK, day, home_id, away_id,
               my_in, my_tid if my_in else 0))
    conn.commit()
    conn.close()
    clear_entry_cache()

    if my_in or country_id == _my_country_id(p or {}):
        from game_engine import add_log
        he = entry(DSC_CFG, tid, home_id)
        ae = entry(DSC_CFG, tid, away_id)
        add_log(f"🏆 {year}년 {name} 대진 확정: "
                f"{he.get('flag','')}{he.get('team_name','?')} vs "
                f"{ae.get('flag','')}{ae.get('team_name','?')} "
                f"({DOMESTIC_SC_WEEK}주차)", "event")


def start_all_domestic_super_cups(year, season):
    """매 시즌 새해 진입 시 1회 호출 — 리그가 있는 나라 전부에 대해
    그 해 대회 생성을 시도한다(조건 안 맞으면 조용히 건너뜀).
    cup_engine.start_domestic_cup과 동일하게 세계 기록실 공백을 막기 위해
    나라를 좁히지 않는다.

    [2026-09 성능, 신민용 리포트: "국내 슈퍼컵 만든 후 52→1주차 딜레이"]
    예전엔 나라마다 _build_domestic_sc를 불러, 나라 하나당 리그 조회·
    전년도 season 역산(league_season_standings 풀스캔 — 매년 1만 행씩 커지는
    표를 211번)·국내컵 결승 조회·get_player()·get_state()·commit·캐시 비우기를
    전부 따로 했다(나라 211개 × 쿼리 10여 개 + 커밋 211회). 결과를 바꾸지 않고
    "나라와 무관하게 같은 값"(전년도 season, 내 선수/상태/내 나라)은 한 번만,
    "나라별 값"(1부 리그, 컵 결승)은 연도 단위 일괄 조회로 미리 읽어 두고
    커밋은 마지막에 한 번만 한다. 나라 처리 순서·참가팀 결정 규칙·INSERT
    순서(=대회 id)는 예전과 완전히 같다 — 개별 조회 버전(_build_domestic_sc)은
    단독 호출용으로 그대로 남겨둔다."""
    conn = get_conn()
    c = conn.cursor()
    country_ids = [r["country_id"] for r in c.execute(
        "SELECT DISTINCT country_id FROM leagues WHERE tier=1").fetchall()]
    if not country_ids:
        conn.close()
        return
    prev_year = year - 1

    # 이미 생성된 나라(재호출 멱등성) — get_domestic_sc_tournament와 동일 조건.
    existing = {r["country_id"] for r in c.execute(
        "SELECT country_id FROM domestic_sc_tournaments WHERE year=?", (year,)).fetchall()}
    # 나라별 1부 리그 — 개별 버전의 fetchone()과 같게 가장 앞(rowid) 행을 쓴다.
    tier1_league = {}
    for r in c.execute("SELECT id, country_id FROM leagues WHERE tier=1 ORDER BY rowid").fetchall():
        tier1_league.setdefault(r["country_id"], r["id"])
    country_names = {r["id"]: r["name"] for r in c.execute(
        "SELECT id, name FROM countries").fetchall()}
    season_prev = _season_for_year(prev_year)   # 나라와 무관 — 1회만

    cup_tid_by_country = {}
    final_by_cup_tid = {}
    if season_prev is not None:
        for r in c.execute(
                "SELECT id, country_id FROM cup_tournaments WHERE year=? AND status='done' ORDER BY id",
                (prev_year,)).fetchall():
            cup_tid_by_country.setdefault(r["country_id"], r["id"])
        _cup_tids = list(cup_tid_by_country.values())
        for _i in range(0, len(_cup_tids), 500):
            _chunk = _cup_tids[_i:_i + 500]
            _ph = ",".join("?" * len(_chunk))
            for r in c.execute(
                    f"""SELECT * FROM cup_matches WHERE tournament_id IN ({_ph})
                        AND round_name='결승' AND home_score>=0 ORDER BY id""",
                    tuple(_chunk)).fetchall():
                final_by_cup_tid[r["tournament_id"]] = dict(r)   # ORDER BY id → 마지막(=id DESC LIMIT 1)이 남는다

    from game_engine import get_player, get_state, get_league_standings, add_log
    p = get_player()
    my_tid = p.get("current_team_id", 0) if p else 0
    st = get_state()
    cur_season = st["current_season"] if st else 1
    my_cid = _my_country_id(p or {})

    created_logs = []
    for cid in country_ids:
        try:
            if not cid or cid in existing:
                continue
            league_id = tier1_league.get(cid)
            if not league_id:
                continue
            if season_prev is None:
                rows = c.execute("SELECT id FROM teams WHERE league_id=?", (league_id,)).fetchall()
                ovr_list = sorted(
                    ((r["id"], (_team_snapshot(c, r["id"])["ovr"])) for r in rows),
                    key=lambda x: -x[1])
                if len(ovr_list) < 2:
                    continue
                home_id, away_id = ovr_list[0][0], ovr_list[1][0]
            else:
                standings = get_league_standings(league_id, season=season_prev, conn=conn)
                if not standings:
                    continue
                league_champion = standings[0]["id"]
                ct_id = cup_tid_by_country.get(cid)
                if not ct_id:
                    continue
                fm = final_by_cup_tid.get(ct_id)
                if not fm:
                    continue
                winner = winner_of(fm)
                runner = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]
                away = runner if winner == league_champion else winner
                if not away or away == league_champion:
                    continue
                home_id, away_id = league_champion, away

            cname = country_names.get(cid)
            if not cname:
                continue
            name = _domestic_sc_name(cname)
            my_in = 1 if my_tid in (home_id, away_id) else 0
            day = _pick_dsc_day((home_id, away_id), year, conn=c)

            c.execute("""INSERT INTO domestic_sc_tournaments
                            (year, country_id, name, status, home_team_id, away_team_id,
                             my_in, my_team_id)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (year, cid, name, "active", home_id, away_id,
                       my_in, my_tid if my_in else 0))
            tid = c.lastrowid
            for team_id in (home_id, away_id):
                info = _team_snapshot(c, team_id)
                c.execute("""INSERT INTO domestic_sc_entries
                                (tournament_id, team_id, team_name, flag, country, grade, ovr)
                             VALUES(?,?,?,?,?,?,?)""",
                          (tid, team_id, info["team_name"], info["flag"], info["country"],
                           info["grade"], info["ovr"]))
            c.execute("""INSERT INTO domestic_sc_matches
                            (tournament_id, stage, week, day, home_team_id, away_team_id,
                             is_my, my_team_id)
                         VALUES(?,'F',?,?,?,?,?,?)""",
                      (tid, DOMESTIC_SC_WEEK, day, home_id, away_id,
                       my_in, my_tid if my_in else 0))
            if my_in or cid == my_cid:
                created_logs.append((tid, name, home_id, away_id))
        except Exception as e:
            print(f"국내 슈퍼컵 생성 오류(country_id={cid}, 건너뜀):", e)
    conn.commit()
    conn.close()
    clear_entry_cache()
    for tid, name, home_id, away_id in created_logs:
        he = entry(DSC_CFG, tid, home_id)
        ae = entry(DSC_CFG, tid, away_id)
        add_log(f"🏆 {year}년 {name} 대진 확정: "
                f"{he.get('flag','')}{he.get('team_name','?')} vs "
                f"{ae.get('flag','')}{ae.get('team_name','?')} "
                f"({DOMESTIC_SC_WEEK}주차)", "event")


# ─────────────────────────────────────────────
# 내 경기 조회 (스케줄/센터패널용)
# ─────────────────────────────────────────────

def get_my_domestic_sc_match(week, day=None, p=None, st=None):
    """super_cup_engine.get_my_super_cup_match와 동일한 반환 형식 —
    다만 continent가 아니라 나라 단위 스코프."""
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
    cid = _my_country_id(p)
    t = get_domestic_sc_tournament(st["current_year"], cid)
    if not t or t["status"] == "done":
        return None
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != tid:
        return None

    conn = get_conn()
    if day is not None:
        m = conn.execute(
            """SELECT * FROM domestic_sc_matches WHERE tournament_id=? AND week=?
               AND home_score=-1 AND (home_team_id=? OR away_team_id=?)
               AND (day=? OR day IS NULL OR day=0)""",
            (t["id"], week, tid, tid, day)).fetchone()
    else:
        m = conn.execute(
            """SELECT * FROM domestic_sc_matches WHERE tournament_id=? AND week=?
               AND home_score=-1 AND (home_team_id=? OR away_team_id=?)""",
            (t["id"], week, tid, tid)).fetchone()
    if not m:
        conn.close()
        return None
    is_home = (m["home_team_id"] == tid)
    opp_id = m["away_team_id"] if is_home else m["home_team_id"]
    oe = conn.execute(
        "SELECT team_name, flag FROM domestic_sc_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], opp_id)).fetchone()
    conn.close()
    return {
        "domestic_sc": True,
        "match_id": m["id"],
        "tournament_id": t["id"],
        "league_name": t["name"],
        "stage": "F", "stage_ko": "", "grp": "",
        "opp": oe["team_name"] if oe else "?",
        "opp_flag": oe["flag"] if oe else "",
        "is_home": is_home,
        "week": week,
    }


def has_my_domestic_sc_match_between(week_from, week_to):
    for w in range(week_from, week_to + 1):
        if get_my_domestic_sc_match(w):
            return True
    return False


def get_my_domestic_sc_matches_for_schedule(year):
    """schedule_window.py용 — competition_common.get_my_matches_for_
    schedule과 동일한 반환 형식(country 스코프라 my_tournament 대신
    get_domestic_sc_tournament를 직접 쓰는 것만 다름)."""
    from game_engine import get_player
    p = get_player()
    if not p or not p.get("current_team_id"):
        return []
    cid = _my_country_id(p)
    t = get_domestic_sc_tournament(year, cid)
    if not t or not t.get("my_in"):
        return []
    reg_tid = t.get("my_team_id", 0)
    if not reg_tid or reg_tid != p.get("current_team_id", 0):
        return []

    conn = get_conn()
    entries = {r["team_id"]: dict(r) for r in conn.execute(
        "SELECT team_id, team_name, flag, country FROM domestic_sc_entries WHERE tournament_id=?",
        (t["id"],)).fetchall()}
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM domestic_sc_matches WHERE tournament_id=? ORDER BY week",
        (t["id"],)).fetchall()]
    conn.close()

    def _name(tid):
        e = entries.get(tid, {})
        return f"{e.get('flag','')}{e.get('team_name','?')}"

    out = []
    for m in rows:
        pso_name = _name(m["pso_winner"]) if m["pso_winner"] else ""
        out.append({
            "home_id": m["home_team_id"], "away_id": m["away_team_id"],
            "home_name": _name(m["home_team_id"]), "away_name": _name(m["away_team_id"]),
            "home_league": entries.get(m["home_team_id"], {}).get("country", ""),
            "away_league": entries.get(m["away_team_id"], {}).get("country", ""),
            "home_score": m["home_score"], "away_score": m["away_score"],
            "pso_winner": pso_name, "pso_score": m["pso_score"],
            "stage": "", "week": m["week"], "stage_raw": "F", "grp": "",
        })
    return out


def resync_my_domestic_sc_registration(p=None, year=None):
    """cup_engine.resync_my_cup_registration과 동일한 이유(시즌 중
    이적 시 등록 팀 재계산) — 이 대회는 4주차 안에 대개 끝나 이적과
    겹칠 일이 드물지만, 프리시즌(1~3주) 이적으로 소속이 바뀐 채
    4주차를 맞는 경우를 대비해 동일 패턴으로 둔다."""
    from game_engine import get_player, get_state
    if p is None:
        p = get_player()
    if not p:
        return False
    if year is None:
        st = get_state()
        year = st["current_year"] if st else None
    if year is None:
        return False
    my_tid = p.get("current_team_id", 0) or 0
    cid = _my_country_id(p)
    t = get_domestic_sc_tournament(year, cid)
    if not t:
        return False
    entered = bool(my_tid) and my_tid in (t["home_team_id"], t["away_team_id"])
    want = my_tid if entered else 0
    if (t.get("my_team_id") or 0) == want:
        return False
    conn = get_conn()
    conn.execute("UPDATE domestic_sc_tournaments SET my_team_id=?, my_in=? WHERE id=?",
                 (want, 1 if want else 0, t["id"]))
    conn.execute("""UPDATE domestic_sc_matches
                    SET is_my=(CASE WHEN home_team_id=? OR away_team_id=? THEN 1 ELSE 0 END),
                        my_team_id=(CASE WHEN home_team_id=? OR away_team_id=? THEN ? ELSE 0 END)
                    WHERE tournament_id=? AND home_score=-1""",
                 (want, want, want, want, want, t["id"]))
    conn.commit()
    conn.close()
    return True


def get_my_domestic_sc_matches():
    """career_window/retire_window용 — super_cup_engine.get_my_sc_matches와
    이름 패턴만 다르고 완전히 같은 방식으로 competition_common.
    get_my_matches(DSC_CFG)를 그대로 부른다(continent 개념이 없는 순수
    cfg 기반 함수라 그대로 재사용 가능)."""
    from competition.competition_common import get_my_matches
    return get_my_matches(DSC_CFG)


# ─────────────────────────────────────────────
# 시뮬레이션
# ─────────────────────────────────────────────

def _sim_ai_match(t, m, conn=None, p=None):
    """AI끼리(또는 내가 결장한 내 경기) 시뮬 — cup_engine._sim_ai_match와
    동일한 패턴, 홈 어드밴티지만 없는(중립) outcome 공식을 쓴다."""
    from game_engine import add_log, get_player, _gen_score
    if p is None:
        p = get_player()
    he = entry(DSC_CFG, t["id"], m["home_team_id"])
    ae = entry(DSC_CFG, t["id"], m["away_team_id"])
    outcome = _neutral_match_outcome(he["ovr"], ae["ovr"])
    pso_winner, pso_score = 0, ""
    if outcome == "draw":
        win_home, pso_score = _neutral_resolve_pso(he["ovr"], ae["ovr"])
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]
    hs, as_ = _gen_score(outcome, he["ovr"] - ae["ovr"])

    _own = conn is None
    if _own:
        conn = get_conn()
    conn.execute("""UPDATE domestic_sc_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=? WHERE id=?""",
                 (hs, as_, pso_winner, pso_score, m["id"]))
    if _own:
        conn.commit()
        conn.close()

    my_tid = p.get("current_team_id", 0) if p else 0
    if my_tid in (m["home_team_id"], m["away_team_id"]):
        pso_txt = f"  (승부차기 {pso_score})" if pso_winner else ""
        add_log(f"🏆 {t['name']}  {he['flag']}{he['team_name']} {hs}-{as_} "
                f"{ae['flag']}{ae['team_name']}{pso_txt}", "match")


def sim_my_domestic_sc_match_as_ai(week, p, reason="injury", day=None):
    """부상 등으로 내가 못 뛸 때 AI끼리(내 보너스 없이) 시뮬 —
    cup_engine.sim_my_cup_match_as_ai와 동일한 패턴."""
    info = get_my_domestic_sc_match(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM domestic_sc_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM domestic_sc_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()
    if m["home_score"] != -1:
        return
    _sim_ai_match(t, m, p=p)
    from game_engine import update_player, _calc_manager_rel
    update_player(manager_relation=_calc_manager_rel(p, 0, "", played=False, not_played_penalty=2))


def simulate_my_domestic_sc_match(week, p, day=None):
    """내가 출전하는 국내 슈퍼컵 경기 — cup_engine.simulate_my_cup_match를
    템플릿으로 하되, 전술엔진(tactical_engine) 시도 없이 그 함수의
    "실패 시 폴백" 경로(_player_perf 기반)만 사용해 복잡도를 낮췄다.
    개인 골/어시/평점/카드/출전정지는 정상 반영된다."""
    from game_engine import (add_log, get_player, update_player,
                             _player_perf, _my_result, _update_pop, _gen_score,
                             _save_match_detail, _soft_cap,
                             _check_suspended, _check_bench,
                             _apply_red_card_dismissal, _roll_card_events,
                             _week_intl_cl_day, _day_label)
    from constants import PERSONALITY_EFFECTS
    info = get_my_domestic_sc_match(week, day=day)
    if not info:
        return
    conn = get_conn()
    t = dict(conn.execute("SELECT * FROM domestic_sc_tournaments WHERE id=?",
                          (info["tournament_id"],)).fetchone())
    m = dict(conn.execute("SELECT * FROM domestic_sc_matches WHERE id=?",
                          (info["match_id"],)).fetchone())
    conn.close()

    he = entry(DSC_CFG, t["id"], m["home_team_id"])
    ae = entry(DSC_CFG, t["id"], m["away_team_id"])
    is_home = info["is_home"]

    _suspended, _new_susp = _check_suspended(p, field="domestic_sc_suspension")
    if _suspended:
        update_player(domestic_sc_suspension=_new_susp)
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

    outcome = _neutral_match_outcome(h_ovr, a_ovr)
    hs, as_ = _gen_score(outcome, h_ovr - a_ovr)
    pso_winner, pso_score = 0, ""
    if outcome == "draw":
        win_home, pso_score = _neutral_resolve_pso(h_ovr, a_ovr)
        pso_winner = m["home_team_id"] if win_home else m["away_team_id"]

    if _suspended or _benched:
        goals, assists, saves, rating = 0, 0, 0, 0.0
        events, detail = [], {"shots": 0, "shots_on": 0, "key_passes": 0,
                              "dribbles": 0, "blocks": 0, "pass_acc": 0.0}
        _absence_reason = "suspension" if _suspended else None
        _yellow_cnt = 0
    else:
        _opp_ovr = (ae["ovr"] if is_home else he["ovr"])
        # [2026-09 신설] 국내 슈퍼컵은 매 시즌 단판 결승 자체(round_name이
        # 항상 '결승')라 챔스/국대와 동일하게 전 경기를 빅매치로 취급한다.
        goals, assists, saves, rating, events, detail = _player_perf(
            p, outcome, is_home, hs, as_, opp_ovr=_opp_ovr, is_big_match=True)
        _absence_reason = None
        _dismissed, _card_reason, _yellow_ev, _yellow_cnt = _roll_card_events(
            p, "domestic_sc_suspension")
        if _dismissed:
            goals, assists, saves, rating, events, detail = _apply_red_card_dismissal(
                p, field="domestic_sc_suspension", reason=_card_reason)
            _absence_reason = _card_reason
        elif _yellow_ev:
            events = list(events) + _yellow_ev

    my_result = _my_result(outcome, is_home)
    my_conceded = (as_ if is_home else hs)

    if day is None:
        day = _week_intl_cl_day(week, p)

    conn = get_conn()
    conn.execute("""UPDATE domestic_sc_matches SET home_score=?, away_score=?,
                    pso_winner=?, pso_score=?, my_played=?,
                    my_saves=?, my_goals=?, my_assists=?, my_rating=?,
                    my_shots=?, my_shots_on=?, my_key_passes=?,
                    my_dribbles=?, my_blocks=?, my_pass_acc=?, my_conceded=?,
                    my_absence_reason=?, my_yellow_cards=?
                    WHERE id=?""",
                 (hs, as_, pso_winner, pso_score, 0 if (_suspended or _benched) else 1,
                  saves, goals, assists, rating,
                  detail["shots"], detail["shots_on"], detail["key_passes"],
                  detail["dribbles"], detail["blocks"], detail["pass_acc"], my_conceded,
                  _absence_reason, _yellow_cnt, m["id"]))
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

    comp_name = t["name"]
    home_disp = he["team_name"]
    away_disp = ae["team_name"]
    pso = {"won": pso_winner == my_tid, "score": pso_score} if pso_winner else None
    detail_id = _save_match_detail(
        p, week, comp_name, is_home, home_disp, away_disp,
        hs, as_, my_result, goals, assists, saves, rating,
        events, not (_suspended or _benched), _benched, detail, pso=pso)
    marker = f" [match:{detail_id}:domestic_sc]" if detail_id else ""

    add_log("─" * 44, "sep")
    add_log(f"🏆 {comp_name}  {_day_label(week, day)}{marker}", "match")
    add_log(f"   {home_disp} {hs}-{as_} {away_disp}  ({rs}){pso_txt}", "match")
    if p.get("position") == "GK":
        add_log(f"   평점 {rating:.1f}  선방 {saves}", "match")
    else:
        add_log(f"   평점 {rating:.1f}  골 {goals}  어시 {assists}", "match")


# ─────────────────────────────────────────────
# 종료 처리 — 상금/모멘텀/발롱도르 반영 없음(신민용 확정)
# ─────────────────────────────────────────────

def _save_domestic_sc_trophy(year, team_name, result, competition):
    """cup_engine._save_trophy와 동일한 형태 — tier=-4로 국내 슈퍼컵을
    구분(-1=대륙급 클럽대항전·대륙슈퍼컵, -2=국내컵, -3=클럽월드컵과
    안 겹치는 새 값). competition_common.save_trophy는 tier=-1로
    고정돼 있어 그대로 못 쓴다."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM trophy_log WHERE year=? AND competition=? AND team_name=?",
        (year, competition, team_name)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO trophy_log(year, team_name, league_name, tier, competition)
                        VALUES(?,?,?,-4,?)""", (year, team_name, result, competition))
        conn.commit()
    conn.close()


def _record_my_exit(t, result):
    """상금·명성·모멘텀 없이 트로피 기록만 남긴다(신민용 확정: "상같은건
    없어") — competition_common.record_my_exit는 보상을 얹어주므로
    그대로 재사용하지 않는다."""
    from game_engine import get_player
    p = get_player()
    if not p:
        return
    my_tid = p.get("current_team_id", 0)

    conn = get_conn()
    conn.execute("UPDATE domestic_sc_tournaments SET my_result=? WHERE id=?", (result, t["id"]))
    te = conn.execute(
        "SELECT team_name FROM domestic_sc_entries WHERE tournament_id=? AND team_id=?",
        (t["id"], my_tid)).fetchone()
    conn.commit()
    conn.close()
    team_name = te["team_name"] if te else ""

    _save_domestic_sc_trophy(t["year"], team_name, result, t["name"])

    conn = get_conn()
    agg = conn.execute(
        """SELECT COUNT(*) caps, COALESCE(SUM(my_goals),0) g,
                  COALESCE(SUM(my_assists),0) a, COALESCE(AVG(my_rating),0) r
           FROM domestic_sc_matches WHERE tournament_id=? AND my_played=1""",
        (t["id"],)).fetchone()
    exists = conn.execute(
        "SELECT id FROM domestic_sc_history WHERE year=? AND competition=?",
        (t["year"], t["name"])).fetchone()
    if not exists:
        conn.execute("""INSERT INTO domestic_sc_history
                            (year, competition, team_name, result, goals, assists, caps, rating)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (t["year"], t["name"], team_name, result,
                      agg["g"], agg["a"], agg["caps"], round(agg["r"], 2)))
    conn.commit()
    conn.close()


def _finish_domestic_sc(t):
    conn = get_conn()
    cur = conn.execute("SELECT status FROM domestic_sc_tournaments WHERE id=?",
                       (t["id"],)).fetchone()
    if cur and cur["status"] == "done":
        conn.close()
        return
    fm = conn.execute(
        "SELECT * FROM domestic_sc_matches WHERE tournament_id=? AND home_score>=0",
        (t["id"],)).fetchone()
    conn.close()
    if not fm:
        return
    fm = dict(fm)
    winner = winner_of(fm)
    runner = fm["away_team_id"] if winner == fm["home_team_id"] else fm["home_team_id"]

    conn = get_conn()
    conn.execute("UPDATE domestic_sc_tournaments SET status='done', winner_team_id=? WHERE id=?",
                 (winner, t["id"]))
    conn.commit()
    conn.close()

    from game_engine import add_log, get_player
    p = get_player()
    we = entry(DSC_CFG, t["id"], winner)
    if t["country_id"] == _my_country_id(p or {}):
        add_log(f"🏆 {t['name']} 우승: {we.get('flag','')}{we.get('team_name','?')}!", "event")

    my_tid = p.get("current_team_id", 0) if p else 0
    if my_tid and t.get("my_in") and t.get("my_team_id") == my_tid:
        _record_my_exit(t, "우승" if my_tid == winner else "준우승")


# ─────────────────────────────────────────────
# 주간 진행 — cup_engine.process_cup_week와 동일하게 그 주 마지막 날에만
# 호출(day 자체는 스케줄 표시용, 실제 시뮬레이션은 주 단위로 처리해도
# 이미 지나간 날짜라 항상 안전)
# ─────────────────────────────────────────────

def process_domestic_sc_week(week):
    from game_engine import get_state, get_player
    st = get_state()
    if not st:
        return
    year = st["current_year"]

    conn = get_conn()
    pending = [dict(r) for r in conn.execute(
        "SELECT * FROM domestic_sc_tournaments WHERE year=? AND status!='done'",
        (year,)).fetchall()]
    conn.close()
    if not pending:
        return

    p = get_player()
    for t in pending:
        conn = get_conn()
        m = conn.execute(
            """SELECT * FROM domestic_sc_matches WHERE tournament_id=? AND week<=?
               AND home_score=-1""", (t["id"], week)).fetchone()
        conn.close()
        if m:
            conn = get_conn()
            _sim_ai_match(t, dict(m), conn=conn, p=p)
            conn.commit()
            conn.close()

        conn = get_conn()
        done_row = conn.execute(
            "SELECT 1 FROM domestic_sc_matches WHERE tournament_id=? AND home_score>=0",
            (t["id"],)).fetchone()
        conn.close()
        if done_row:
            _finish_domestic_sc(t)