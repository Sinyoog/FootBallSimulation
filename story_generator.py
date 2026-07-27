# -*- coding: utf-8 -*-
"""
story_generator.py — 은퇴창 "AI 커리어 스토리"의 로컬(비-API) 버전.

[2026-07 신설, 신민용 요청: "클로드 API 말고 자체적으로 만드는 게 낫겠다 —
현질 끝나면 API 못 쓰잖아"] 외부 LLM 호출 없이, 게임 안에 내장된 문장
뱅크(템플릿)만으로 선수 커리어 연대기를 조립해서 만든다. 데이터는 절대
왜곡하지 않는다 — 숫자는 항상 career_entries/awards/trophies 원본 그대로
꽂아 넣고, 문장의 "틀"만 여러 개 중에서 골라 조합한다.

핵심 아이디어 (Football Manager류 게임의 "미디어 반응" 생성기와 동일한 방식):
  1. build_seasons()    : career_entries를 '시즌' 단위로 병합 (중복 행 자동 흡수)
  2. classify_season()  : 각 시즌을 유형(데뷔/성장/하락/우승/이적/임대/은퇴 등)으로 분류
  3. group_eras()       : 연속된 같은 팀 재직을 '스틴트'로 묶고, 짧은 스틴트들은
                          하나의 챕터로 합쳐서 "N부" 구조를 자동 생성
  4. _chapter_character(): 챕터 안 시즌 유형 분포를 보고 챕터의 '성격'(도약기/
                          방랑기/전성기/하락기 등)을 판정해 서사적인 제목을 붙임
  5. TEMPLATES          : 유형별 여러 문체 변형 문장 뱅크 (+ 컵/챔스/국가대표
                          디테일을 본문에 자연스럽게 끼워 넣는 보조 문장들)
  6. generate_story()   : 위 전부를 조립해 최종 장문 텍스트 반환

이 파일은 PyQt와 무관한 순수 로직이라 단독으로 테스트 가능하다.
"""

import random
from collections import Counter


# ══════════════════════════════════════════════════════════════════
# 0. 한국어 조사 처리 — 팀명/리그명 받침 유무에 따라 조사가 자동으로 붙는다
# ══════════════════════════════════════════════════════════════════

def josa(word: str, pair: str) -> str:
    """word 뒤에 붙일 조사를 받침 유무에 맞게 고른다.
    pair 예: '은/는', '이/가', '을/를', '과/와', '으로/로'
    한글이 아닌 문자(영문/숫자/이모지 국기 등)로 끝나면 받침 없다고 가정한다
    (완벽하진 않지만, 어색함보다 문장이 안 끊기는 쪽이 낫다)."""
    if not word:
        word = ""
    last = word[-1] if word else ""
    code = ord(last) if last else 0
    if 0xAC00 <= code <= 0xD7A3:
        jong = (code - 0xAC00) % 28
        has_batchim = jong != 0
    else:
        has_batchim = False
        jong = 0

    if pair == "으로/로":
        if has_batchim and jong != 8:   # 8 = 'ㄹ' 받침 → '로'
            return "으로"
        return "로"

    a, b = pair.split("/")
    return a if has_batchim else b


def with_josa(word: str, pair: str) -> str:
    """'{word}{조사}' 형태로 바로 이어붙인 문자열을 반환."""
    return f"{word}{josa(word, pair)}"


# ══════════════════════════════════════════════════════════════════
# 1. 시즌 데이터 병합
# ══════════════════════════════════════════════════════════════════

_STAT_FIELDS = ("matches", "goals", "assists", "saves", "goals_against",
                "clean_sheets", "blocks", "key_passes", "dribbles",
                "shots", "shots_on", "wins", "draws", "losses")


def build_seasons(entries):
    """career_entries(리그 재직 이력, id순 정렬)를 '(연도, 팀)' 단위로 병합.
    같은 해 같은 팀에 스퓨리어스 중복 행이 여러 개 있어도(별개로 리포트된
    버그) 여기서 자동으로 하나로 합쳐지기 때문에 스토리에는 영향이 없다."""
    merged = {}
    order = []
    for e in entries:
        key = (e.get("start_year", 0), e.get("team_id") or e.get("team_name", ""))
        if key not in merged:
            s = dict(e)
            merged[key] = s
            order.append(key)
        else:
            s = merged[key]
            for f in _STAT_FIELDS:
                s[f] = s.get(f, 0) + e.get(f, 0)
            if (e.get("end_year", 0), e.get("end_week", 0)) > \
               (s.get("end_year", 0), s.get("end_week", 0)):
                s["end_year"] = e.get("end_year", 0)
                s["end_week"] = e.get("end_week", 0)
                s["team_rank"] = e.get("team_rank", s.get("team_rank", 0))
                s["exit_type"] = e.get("exit_type", s.get("exit_type", ""))
            if e.get("transfer_type") and not s.get("transfer_type"):
                s["transfer_type"] = e["transfer_type"]
            if e.get("transfer_fee"):
                s["transfer_fee"] = e["transfer_fee"]
            if e.get("matches", 0) > 0 and s.get("avg_rating", 0) in (0, None):
                s["avg_rating"] = e.get("avg_rating", 0)
    seasons = [merged[k] for k in order]
    seasons.sort(key=lambda s: (s.get("start_year", 0), s.get("start_week", 1)))
    return seasons


def is_narrative_season(s):
    """실제로 이야기할 만한 '진짜 시즌'인지 — 아주 짧은 기간(2주 이하)에
    출전이 0이면 순수 '이적 마커' 행이므로 문단 하나를 통째로 할애하지
    않는다(대신 전환 문장으로만 언급)."""
    if s.get("matches", 0) > 0:
        return True
    sy, sw = s.get("start_year", 0), s.get("start_week", 1)
    ey, ew = s.get("end_year", sy), s.get("end_week", sw)
    weeks = max(1, (ey - sy) * 52 + (ew - sw))
    return weeks >= 8


# ══════════════════════════════════════════════════════════════════
# 2. 트로피/컵/국가대표 데이터 정리
# ══════════════════════════════════════════════════════════════════
# get_my_trophies()가 주는 trophy_log 행은 tier로 종류가 갈린다:
#   tier > 0  : 리그 우승 (실제로 '우승'한 시즌에만 행이 생김 — 매 시즌 로그 아님)
#   tier == -1: 챔피언스리그 — 참가할 때마다 그 해 도달 라운드가 기록됨(우승 아님)
#   tier == -2: 국내 컵대회 — 역시 참가할 때마다 그 해 도달 라운드가 기록됨
#   tier == 0 : 국가대표 관련(월드컵/대륙컵 소집·미선발 등)
# 그래서 "우승했는지"는 tier>0이거나, tier가 -1/-2인데 결과(league_name
# 필드에 저장됨)가 정확히 "우승"일 때만이다 — 그냥 tier가 있다고 전부
# "우승"으로 취급하면(예전 버그) 컵 16강 탈락한 해까지 전부 CAT_CHAMPION으로
# 잘못 분류된다.

_DEEP_CUP_RESULTS = ("우승", "준우승", "4강", "8강")


def _is_real_win(t):
    tier = t.get("tier", 0)
    if tier > 0:
        return True
    return t.get("league_name", "") == "우승"


def _build_trophy_maps(trophies):
    """연도별로 (진짜 우승 연도 집합, 컵대회 결과, 챔스 결과)를 만든다."""
    trophy_years = set()
    cup_by_year = {}
    cl_by_year = {}
    for t in trophies:
        y = t.get("year", 0)
        tier = t.get("tier", 0)
        result = t.get("league_name", "")
        if _is_real_win(t):
            trophy_years.add(y)
        if tier == -2 and y not in cup_by_year:
            cup_by_year[y] = (result, t.get("competition", "컵대회"))
        if tier == -1 and y not in cl_by_year:
            cl_by_year[y] = (result, t.get("competition", "챔피언스리그"))
    return trophy_years, cup_by_year, cl_by_year


_INTL_MISS_RESULTS = ("국가대표 미선발", "발탁 거절", "예선 탈락", "예선 진출 실패")


def _build_intl_map(intl_trophies):
    intl_by_year = {}
    for t in intl_trophies:
        y = t.get("year", 0)
        if y not in intl_by_year:
            intl_by_year[y] = (t.get("competition", "국가대표"), t.get("league_name", ""))
    return intl_by_year


# ══════════════════════════════════════════════════════════════════
# 3. 시즌 유형 분류
# ══════════════════════════════════════════════════════════════════

CAT_DEBUT         = "debut"
CAT_RISING        = "rising"
CAT_STEADY        = "steady"
CAT_DECLINING     = "declining"
CAT_RELEGATION    = "relegation_battle"
CAT_CHAMPION      = "champion"
CAT_NEW_CHALLENGE = "new_challenge"
CAT_ABROAD        = "abroad_challenge"
CAT_HOMECOMING    = "homecoming"
CAT_LOAN          = "loan"
CAT_LOAN_RETURN   = "loan_return"
CAT_VETERAN       = "veteran"
CAT_FINAL         = "final_season"
CAT_BENCH         = "bench"


def classify_season(seasons, idx, awards_by_year, trophy_years, retire_age, player_age_at,
                     home_country=""):
    s = seasons[idx]
    year = s.get("start_year", 0)
    prev_playing = None
    for j in range(idx - 1, -1, -1):
        if seasons[j].get("matches", 0) > 0:
            prev_playing = seasons[j]
            break

    is_first = (idx == 0)
    is_last = (idx == len(seasons) - 1)

    if is_last:
        return CAT_FINAL
    if year in trophy_years:
        return CAT_CHAMPION
    if year in awards_by_year:
        return CAT_STEADY if idx > 0 else CAT_DEBUT
    if s.get("matches", 0) == 0:
        return CAT_BENCH
    if is_first:
        return CAT_DEBUT

    prev_team = seasons[idx - 1].get("team_name", "")
    cur_team = s.get("team_name", "")
    in_type = s.get("transfer_type", "")

    if in_type == "임대":
        return CAT_LOAN

    if cur_team != prev_team:
        if seasons[idx - 1].get("exit_type", "") == "임대 종료":
            return CAT_LOAN_RETURN
        cur_country = s.get("country", "")
        prev_country = seasons[idx - 1].get("country", "")
        if cur_country and prev_country and cur_country != prev_country:
            if home_country and cur_country == home_country and prev_country != home_country:
                return CAT_HOMECOMING
            return CAT_ABROAD
        return CAT_NEW_CHALLENGE

    if prev_playing is not None:
        r_now = s.get("avg_rating", 0) or 0
        r_prev = prev_playing.get("avg_rating", 0) or 0
        if r_prev:
            if r_now - r_prev >= 0.35:
                return CAT_RISING
            if r_prev - r_now >= 0.35:
                return CAT_DECLINING

    rank = s.get("team_rank", 0)
    total = s.get("_total_teams", 0)
    if rank and total and rank / total >= 0.75:
        return CAT_RELEGATION

    age = player_age_at(year)
    if age is not None and retire_age and age >= retire_age - 1:
        return CAT_VETERAN

    return CAT_STEADY


# ══════════════════════════════════════════════════════════════════
# 4. 챕터(부) 구성
# ══════════════════════════════════════════════════════════════════

def group_eras(seasons):
    """연속된 같은 팀 재직을 '스틴트'로 묶고, 3시즌 미만의 짧은 스틴트는
    챕터 하나로 합친다. 단, 합치는 도중 '이전에 이미 등장했던 팀'으로
    다시 돌아오면(예: 임대를 여러 곳 다녀오다 원래 뛰던 국내 리그로
    복귀) 그 지점에서 챕터를 끊는다 — 그래야 "잠깐 여러 곳을 떠돌던 시기"와
    "다시 국내로 돌아온 시기"가 하나의 챕터로 뭉개지지 않는다."""
    narrative = [s for s in seasons if is_narrative_season(s)]

    # [버그수정, 신민용 리포트: "은퇴 시즌이 스토리에서 통째로 빠진다"]
    # 마지막 시즌이 아주 짧거나 0경기라(=is_narrative_season 기준 미달)
    # 위 필터에서 걸러지면, classify_season()은 여전히 그 시즌을
    # CAT_FINAL로 판정하는데 정작 어느 챕터에도 안 들어가서 은퇴 시즌
    # 자체가 통째로 사라지는 문제가 있었다. 마지막 시즌만큼은 내용이
    # 아무리 짧아도(출전 0이어도) 항상 포함시킨다 — "왜 이 나이에
    # 은퇴했는지"를 설명하는 마지막 문장이 없으면 이야기가 끊긴 채
    # 끝나버리기 때문이다.
    if seasons and not any(s is seasons[-1] for s in narrative):
        narrative.append(seasons[-1])

    if not narrative:
        return []

    stints = []
    cur = [narrative[0]]
    for s in narrative[1:]:
        if s.get("team_name") == cur[-1].get("team_name"):
            cur.append(s)
        else:
            stints.append(cur)
            cur = [s]
    stints.append(cur)

    chapters = []
    buf = []
    used_teams = set()

    def flush():
        nonlocal buf
        if buf:
            chapters.append([s for grp in buf for s in grp])
            buf = []

    for stint in stints:
        team = stint[0].get("team_name", "")
        if len(stint) >= 3:
            flush()
            chapters.append(stint)
            used_teams.add(team)

        else:
            if team in used_teams:
                flush()
            buf.append(stint)
            used_teams.add(team)
    flush()

    return chapters


# ── 챕터 성격 판정 + 제목 뱅크 ──────────────────────────────────

CHAPTER_TITLES = {
    "debut": [
        "가장 낮은 곳에서 시작된 이름",
        "무명에서 내디딘 첫걸음",
        "아무도 주목하지 않았던 출발선",
        "낮은 곳에서 시작된 도전",
    ],
    "final": [
        "마지막 도전과 조용한 마무리",
        "긴 여정의 마지막 페이지",
        "커리어의 황혼",
        "유니폼을 벗기까지",
    ],
    "trophy": [
        "정상에 오른 순간들",
        "트로피와 함께한 시간",
        "커리어 최고의 나날",
        "우승으로 채운 계절",
    ],
    "abroad": [
        "낯선 땅에서 찾은 새로운 가능성",
        "해외에서 써 내려간 새로운 장",
        "여러 대륙을 넘나든 도전",
        "국경을 넘은 모험",
    ],
    "struggle": [
        "현실의 벽 앞에서",
        "흔들리는 시간들",
        "버텨내야 했던 나날",
        "가장 힘들었던 시기",
    ],
    "rising": [
        "다시 올라선 시간",
        "성장이 뚜렷했던 나날",
        "반등의 계절",
        "다시 찾은 상승세",
    ],
    "veteran": [
        "베테랑으로 남긴 마지막 불꽃",
        "경험으로 채운 황혼기",
        "관록이 빛난 시간",
    ],
    "wander": [
        "여러 무대를 떠돌다",
        "정착하지 못한 시간들",
        "새로운 곳을 찾아 떠난 나날",
    ],
    "steady": [
        "묵묵히 쌓아 올린 시간",
        "꾸준함으로 채운 나날",
        "큰 굴곡 없이 흘러간 시간",
    ],
}


def _chapter_character(chapter_seasons, categories, is_first_chapter, is_last_chapter):
    cnt = Counter(categories)
    n = len(categories)
    if is_first_chapter:
        return "debut"
    if is_last_chapter:
        return "final"
    if cnt.get(CAT_CHAMPION, 0) >= 1:
        return "trophy"
    abroad_n = cnt.get(CAT_ABROAD, 0) + cnt.get(CAT_LOAN, 0)
    if abroad_n >= max(1, n // 2):
        return "abroad"
    struggle_n = cnt.get(CAT_RELEGATION, 0) + cnt.get(CAT_DECLINING, 0)
    if struggle_n >= max(1, (n + 1) // 2):
        return "struggle"
    if cnt.get(CAT_RISING, 0) >= 2:
        return "rising"
    if cnt.get(CAT_VETERAN, 0) >= max(1, (n + 1) // 2):
        return "veteran"
    teams = {s.get("team_name", "") for s in chapter_seasons}
    if len(teams) >= 3:
        return "wander"
    return "steady"


def _chapter_title(chapter_seasons, categories, is_first_chapter, is_last_chapter, rng, tracker=None):
    teams = []
    for s in chapter_seasons:
        t = s.get("team_name", "")
        if t and t not in teams:
            teams.append(t)
    y0 = chapter_seasons[0].get("start_year", "")
    y1 = chapter_seasons[-1].get("start_year", "")
    yr = f"({y0}~{y1})" if y0 != y1 else f"({y0})"

    character = _chapter_character(chapter_seasons, categories, is_first_chapter, is_last_chapter)
    if tracker is not None:
        phrase = _pick(rng, CHAPTER_TITLES[character], tracker, f"title:{character}")
    else:
        phrase = rng.choice(CHAPTER_TITLES[character])

    if len(teams) == 1:
        title = f"{phrase} — {teams[0]}"
    elif len(teams) <= 3:
        title = f"{phrase} — " + " · ".join(teams)
    else:
        title = phrase
    return title, yr


# ══════════════════════════════════════════════════════════════════
# 5. 문장 뱅크
# ══════════════════════════════════════════════════════════════════
# 플레이스홀더: {team} {league} {country} {apps} {goals} {assists}
#               {rating} {rank} {total} {wdl} {year} {age} {pos}

TEMPLATES = {

    CAT_DEBUT: [
        "{year}년, {team} 유니폼을 입고 성인 무대에 데뷔했다. 첫 시즌 {apps}경기에 나서 "
        "{goals}골 {assists}도움을 기록했고, 팀은 {wdl}로 시즌을 마쳐 {rank_str}에 자리했다. "
        "화려한 조명을 받는 데뷔는 아니었지만, 낮은 곳에서부터 시작하는 길을 택한 것이다.",

        "커리어의 첫 페이지는 {team} 소속으로 열렸다. {year}년 {apps}경기 {goals}골 {assists}도움 — "
        "숫자 자체보다, 성인 무대에 처음 나선 어린 선수가 곧바로 자기 자리를 만들어 갔다는 사실이 더 중요했다. "
        "팀은 {rank_str}로 마쳤지만, 그의 이름은 이미 조금씩 알려지기 시작했다.",

        "{year}년, {team}에서 프로 무대 첫발을 뗐다. {apps}경기에 나서 {goals}골 {assists}도움을 남겼고, "
        "팀 성적은 {rank_str}({wdl})였다. 화려하진 않았지만, 분명한 시작이었다.",

        "모든 것은 {year}년 {team}에서 시작됐다. 데뷔 시즌 {apps}경기 {goals}골 {assists}도움, "
        "팀은 {wdl}로 {rank_str}에 자리했다 — 앞으로 이어질 긴 여정의 첫 장이었다.",
    ],

    CAT_RISING: [
        "{team} 소속으로 맞은 {year}년, 확실한 상승세를 보여줬다. {apps}경기 {goals}골 {assists}도움, "
        "평점 {rating}. 전 시즌보다 눈에 띄게 나아진 경기력이었고, 팀 내에서의 입지도 그만큼 단단해졌다.",

        "{year}년은 발전이 뚜렷했던 해였다. {team}에서 {apps}경기를 뛰며 {goals}골 {assists}도움, "
        "평점 {rating}을 기록했다. 팀은 {wdl}로 {rank_str}를 차지했고, 개인의 성장세가 팀 성적과 맞물려 갔다.",

        "한 단계 올라선 {year}년이었다. {team}에서 {apps}경기 {goals}골 {assists}도움, 평점 {rating} — "
        "이전 시즌과는 분명히 다른 무게감이었다.",

        "{year}년, {team}에서의 경기력이 눈에 띄게 좋아졌다. {apps}경기 {goals}골 {assists}도움을 기록하며 "
        "평점 {rating}까지 끌어올렸고, 팀도 {rank_str}({wdl})로 함께 상승세를 탔다.",
    ],

    CAT_STEADY: [
        "{year}년에도 {team}에서 꾸준한 한 해를 보냈다. {apps}경기 {goals}골 {assists}도움, "
        "평점 {rating}. 극적인 반전은 없었지만, 그 꾸준함 자체가 그의 가치였다. 팀은 {wdl}로 {rank_str}에 머물렀다.",

        "특별한 반전도, 특별한 부진도 없었던 {year}년. {team} 소속으로 {apps}경기에 나서 "
        "{goals}골 {assists}도움을 보탰다. 팀 성적은 {rank_str}({wdl}) — 안정 속에서 한 시즌을 더 쌓았다.",

        "{year}년, {team}에서 담담하게 시즌을 이어갔다. {apps}경기 {goals}골 {assists}도움, "
        "팀은 {rank_str}({wdl})로 시즌을 마쳤다 — 큰 사건은 없었지만 제 몫은 분명히 했다.",

        "굴곡 없는 한 해였다. {year}년 {team}에서 {apps}경기 {goals}골 {assists}도움을 기록했고, "
        "팀은 {wdl}로 {rank_str}에 자리했다.",
    ],

    CAT_DECLINING: [
        "{year}년은 쉽지 않은 한 해였다. {team}에서 {apps}경기에 나섰지만 평점은 {rating}으로 "
        "이전보다 떨어졌고, {goals}골 {assists}도움에 그쳤다. 팀 역시 {rank_str}({wdl})로 고전했다.",

        "상승세가 꺾인 시즌이었다. {year}년 {team} 소속으로 {apps}경기를 뛰었지만 경기력은 예전만 못했다 — "
        "평점 {rating}, {goals}골 {assists}도움. 팀 성적도 {rank_str}에 그쳐 어려운 시기가 겹쳤다.",

        "{year}년, {team}에서 좀처럼 리듬을 찾지 못했다. {apps}경기 {goals}골 {assists}도움, 평점 {rating} — "
        "이전 시즌들에 비하면 아쉬움이 남는 한 해였다.",

        "모든 선수에게 부침은 있다. {year}년 {team}에서의 시즌이 그랬다. {apps}경기에 나섰지만 "
        "평점 {rating}에 그쳤고, 팀도 {rank_str}({wdl})로 힘든 한 해를 보냈다.",
    ],

    CAT_RELEGATION: [
        "{team_eun} {year}년 하위권에서 힘든 싸움을 이어갔다({wdl}, {rank_str}). "
        "그런 팀 상황 속에서도 {apps}경기 {goals}골 {assists}도움을 기록하며 흔들리지 않는 모습을 보였다 — "
        "무너지는 팀에서 버티는 경험은, 편하게 이기는 경험과는 다른 무게를 남긴다.",

        "{year}년 {team_eun} 순위표 하단에서 벗어나지 못했다({rank_str}, {wdl}). "
        "팀 전체가 흔들리는 와중에도 {apps}경기에 나서 {goals}골 {assists}도움으로 제 몫을 했다.",

        "강등 위기가 감돌던 {year}년, {team}에서 {apps}경기를 뛰었다. {goals}골 {assists}도움을 남겼지만 "
        "팀은 결국 {rank_str}({wdl})까지 밀려났다 — 하위권 팀의 중심을 잡아야 하는 부담이 만만치 않았다.",

        "{year}년의 {team_eun} 순위표 아래쪽에서 한 해 내내 씨름했다({rank_str}, {wdl}). "
        "그 속에서도 {apps}경기 {goals}골 {assists}도움으로 자신의 자리를 지켰다.",
    ],

    CAT_CHAMPION: [
        "🏆 {year}년, {team} 우승을 함께했다. {apps}경기 {goals}골 {assists}도움, 평점 {rating} — "
        "커리어에서 손에 꼽을 시즌이었고, 팀 전체가 정점에 오른 해였다.",

        "{year}년은 트로피와 함께 기억될 시즌이다. {team} 소속으로 {apps}경기에 나서 "
        "{goals}골 {assists}도움을 기록했고, 팀은 마침내 우승을 차지했다.",

        "우승. {year}년 {team}에서 이 한 단어로 시즌을 요약할 수 있다. {apps}경기 {goals}골 {assists}도움을 "
        "보태며 팀의 정상 등극에 힘을 보탰다.",

        "{team_eun} {year}년 정상에 섰다. {apps}경기 {goals}골 {assists}도움을 기록한 그에게도 "
        "커리어에서 가장 빛나는 시즌 중 하나로 남을 한 해였다.",
    ],

    CAT_NEW_CHALLENGE: [
        "{year}년, {team_ro} 이적하며 새로운 도전에 나섰다. 낯선 환경에 적응해야 하는 시즌이었지만 "
        "{apps}경기 {goals}골 {assists}도움을 기록하며 나쁘지 않은 출발을 알렸다. 팀 성적은 {rank_str}({wdl}).",

        "새 유니폼을 입은 {year}년. {team}에서의 첫 시즌은 적응이 관건이었다. "
        "{apps}경기 {goals}골 {assists}도움, 팀은 {rank_str}로 마쳤다 — 새로운 곳에서 자리를 잡아가는 과정이었다.",

        "{year}년 {team_ro} 자리를 옮겼다. 처음 몇 달은 적응기였지만 {apps}경기 {goals}골 {assists}도움을 "
        "남기며 새 소속팀에서의 첫 시즌을 마쳤다.",

        "이적 후 첫 시즌, {year}년 {team}에서 {apps}경기 {goals}골 {assists}도움을 기록했다. "
        "팀은 {rank_str}({wdl})로 시즌을 마쳤고, 새로운 환경에서의 적응은 나쁘지 않았다.",
    ],

    CAT_ABROAD: [
        "{year}년 {country} 무대로 건너가며 커리어의 방향을 바꿨다. {team}에서 뛰며 낯선 리그, "
        "낯선 문화에 적응해야 했던 시즌 — {apps}경기 {goals}골 {assists}도움을 기록했고, 팀은 {rank_str}({wdl})였다.",

        "해외 도전이 시작된 {year}년. {country}의 {team}에 합류해 완전히 다른 축구 환경 속에서 시즌을 치렀다. "
        "{apps}경기 {goals}골 {assists}도움 — 결과보다 새로운 세계에 적응해 낸 과정 자체가 의미 있는 시즌이었다.",

        "{year}년, 국경을 넘어 {country}의 {team_ro} 향했다. 언어도 문화도 낯선 곳에서 {apps}경기에 나서 "
        "{goals}골 {assists}도움을 기록하며 새로운 장을 열었다.",

        "익숙했던 환경을 떠나 {year}년 {country_ro} 향했다. {team} 소속으로 {apps}경기 {goals}골 {assists}도움을 "
        "남긴 이 시즌은, 커리어의 지도를 넓힌 한 해였다.",
    ],

    CAT_HOMECOMING: [
        "{year}년, {country_ro} 돌아왔다. 여러 해 타지를 떠돌던 끝에 다시 밟은 익숙한 땅이었다. "
        "{team}에서 {apps}경기 {goals}골 {assists}도움을 기록하며 새로운 챕터를 시작했다.",

        "긴 타향 생활을 뒤로하고 {year}년 {country_ro} 복귀했다. {team} 소속으로 {apps}경기에 나서 "
        "{goals}골 {assists}도움을 보탰다 — 낯선 곳에서의 경험을 안고 돌아온 익숙한 무대였다.",

        "{year}년, 다시 {country_ro} 돌아와 {team}에 자리를 잡았다. {apps}경기 {goals}골 {assists}도움을 "
        "기록하며, 그동안 해외에서 쌓은 경험을 고향 무대에 풀어놓기 시작했다.",
    ],

    CAT_LOAN: [
        "{year}년 {team_ro} 임대를 떠났다. 원소속팀에서 밀려난 임대라기보다, 새로운 경험을 쌓기 위한 시간이었다. "
        "{apps}경기 {goals}골 {assists}도움을 기록하며 임대 기간을 알차게 보냈다.",

        "임대 신분으로 맞은 {year}년, {team}의 유니폼을 입었다. {apps}경기 {goals}골 {assists}도움 — "
        "잠시 거쳐 가는 곳이었지만 그 안에서도 자신의 몫을 다했다.",

        "{year}년, {team_ro} 임대를 떠나 새로운 실전 감각을 쌓았다. {apps}경기 {goals}골 {assists}도움을 남기며, "
        "원소속팀에서 얻지 못한 출전 기회를 이곳에서 채워갔다.",
    ],

    CAT_LOAN_RETURN: [
        "임대를 마치고 {year}년 {team_ro} 복귀했다. {apps}경기 {goals}골 {assists}도움을 기록하며 "
        "원소속팀에서 다시 자리를 찾아가는 시즌이었다.",

        "{year}년, 임대 생활을 끝내고 {team_ro} 돌아왔다. 오랜만의 복귀였지만 {apps}경기에 나서 "
        "{goals}골 {assists}도움으로 존재감을 남겼다.",

        "임대를 통해 쌓은 경험을 안고 {year}년 {team_ro} 돌아왔다. {apps}경기 {goals}골 {assists}도움을 "
        "기록하며 원소속팀에서 새로운 시작을 알렸다.",
    ],

    CAT_VETERAN: [
        "베테랑이 된 {year}년에도 {team}에서 제 몫을 했다. {apps}경기 {goals}골 {assists}도움, 평점 {rating} — "
        "나이는 숫자일 뿐이라는 걸 경기력으로 보여준 시즌이었다.",

        "{year}년, 이제는 팀의 어른이 되어 {team}에서 시즌을 치렀다. {apps}경기 {goals}골 {assists}도움을 "
        "기록하며, 경험에서 나오는 안정감으로 팀에 기여했다.",

        "관록이 묻어난 {year}년이었다. {team}에서 {apps}경기 {goals}골 {assists}도움을 기록하며, "
        "젊은 선수들 사이에서 든든한 기둥 역할을 했다.",
    ],

    CAT_BENCH: [
        "{year}년은 {team}에서 출전 기회를 좀처럼 잡지 못한 시즌이었다. 경기장 밖에서 팀을 지켜봐야 하는 "
        "시간이 길었지만, 그 또한 커리어의 한 페이지였다.",

        "{year}년, {team}에서 좀처럼 기회를 얻지 못했다. 벤치를 지키는 날이 많았지만, "
        "다음 시즌을 기약하며 묵묵히 자리를 지켰다.",
    ],

    CAT_FINAL: [
        "그리고 {year}년, {team}에서의 시즌을 끝으로 유니폼을 벗기로 했다. {apps}경기 {goals}골 {assists}도움을 "
        "남긴 마지막 시즌 — 화려한 마무리는 아니었을지 몰라도, 자신의 이름으로 채운 커리어의 마침표였다.",

        "{year}년, {team} 소속으로 뛴 이 시즌을 마지막으로 은퇴를 선택했다. {apps}경기 {goals}골 {assists}도움. "
        "긴 여정의 끝에서, 그는 조용히 그러나 확실하게 자신의 시대를 마감했다.",

        "커리어의 마지막 장. {year}년 {team}에서 {apps}경기 {goals}골 {assists}도움을 기록한 뒤 은퇴를 결정했다. "
        "요란하지 않은 퇴장이었지만, 그가 지나온 길은 결코 가볍지 않았다.",
    ],
}

# 마지막 시즌인데 그 해엔 사실상 뛰지 못한 경우(0경기) — "0경기 0골 0도움을
# 남긴 마지막 시즌" 같은 어색한 문장 대신 별도 문체를 쓴다. [2026-07 신설,
# 신민용 리포트: 18세에 데뷔 시즌 우승·MVP·베스트11을 휩쓸고 이듬해 실질
# 출전 없이 은퇴한 케이스에서 발견 — 이런 급작스러운 은퇴도 사실 그대로
# (출전 없었다는 것) 담담히 언급하고 넘어간다.]
FINAL_INACTIVE_TEMPLATES = [
    "{year}년, {team}에서 이렇다 할 출전 기회 없이 조용히 커리어를 마쳤다. 화려한 은퇴는 아니었지만, "
    "그 전까지 쌓아온 시즌들이 그의 커리어를 말해준다.",

    "{year}년을 끝으로 {team}에서 유니폼을 벗었다. 마지막 시즌엔 경기장에 나서지 못한 채였지만, "
    "그가 이미 남긴 발자취는 그것과 무관하게 선명했다.",

    "정작 {year}년, 마지막 시즌은 그라운드 위가 아니라 벤치에서 저물었다. 그렇게 조용히, "
    "그러나 이전 시즌들의 기억을 남긴 채 커리어를 마무리했다.",
]

AWARD_SENTENCES = [
    "이 시즌 {award_line}까지 차지하며 리그가 인정하는 이름이 됐다.",
    "그리고 시즌 종료 후 {award_line} — 그의 활약이 숫자를 넘어 공식적으로 인정받은 순간이었다.",
    "이 활약은 {award_line_ro} 이어졌다.",
    "리그는 이 활약에 {award_line}(으)로 화답했다.",
]

CUP_SENTENCES = [
    "이 시즌 컵대회에서도 {competition} {cup_result}까지 오르며 두 마리 토끼를 쫓았다.",
    "동시에 {competition}에서도 {cup_result}이라는 성과를 남겼다.",
    "컵대회({competition}) 성적도 나쁘지 않았다 — {cup_result}까지 진출했다.",
]

CL_SENTENCES = [
    "여기에 {competition} 무대에서도 {cup_result}까지 오르며 유럽/대륙 대항전에서도 존재감을 남겼다.",
    "{competition}에서도 {cup_result}라는 성과를 거두며 클럽 대항전 경험을 쌓았다.",
]

INTL_CALLED_SENTENCES = [
    "그 해 국가대표팀에도 이름을 올려 {competition} 무대를 밟았다.",
    "동시에 국가대표팀 소집에 응해 {competition}에 참가했다.",
    "클럽 시즌과 별개로, {competition} 국가대표팀 명단에도 포함됐다.",
]

INTL_MISSED_SENTENCES = [
    "다만 그 해 열린 {competition}에서는 국가대표팀의 부름을 받지 못했다.",
    "그러나 {competition}이 열린 그 해에도 대표팀 명단에는 들지 못했다.",
    "국가대표팀 소집과는 다시 한번 인연이 닿지 않았다 — {competition} 명단 발표에도 그의 이름은 없었다.",
]


# ══════════════════════════════════════════════════════════════════
# 6. 조립
# ══════════════════════════════════════════════════════════════════

CHAPTER_NUMS = ["1부", "2부", "3부", "4부", "5부", "6부", "7부", "8부", "9부", "10부"]


def _rank_str(rank, total):
    if not rank:
        return "순위 미상"
    if total:
        return f"{rank}위/{total}팀"
    return f"{rank}위"


def _wdl_str(s):
    return f"{s.get('wins',0)}승{s.get('draws',0)}무{s.get('losses',0)}패"


def _fill(tmpl, s, extra=None):
    team = s.get("team_name", "")
    league = s.get("league_name", "")
    country = s.get("country") or (league.split()[0] if league else "")
    d = {
        "team": team,
        "team_ro": with_josa(team, "으로/로"),
        "team_eun": with_josa(team, "은/는"),
        "team_ga": with_josa(team, "이/가"),
        "league": league,
        "country": country,
        "country_ro": with_josa(country, "으로/로"),
        "apps": s.get("matches", 0),
        "goals": s.get("goals", 0),
        "assists": s.get("assists", 0),
        "rating": round(s.get("avg_rating", 0) or 0, 1),
        "rank": s.get("team_rank", 0),
        "total": s.get("_total_teams", 0),
        "rank_str": _rank_str(s.get("team_rank", 0), s.get("_total_teams", 0)),
        "wdl": _wdl_str(s),
        "year": s.get("start_year", ""),
        "age": s.get("_age", ""),
        "pos": s.get("position", ""),
    }
    if extra:
        d.update(extra)
    try:
        return tmpl.format(**d)
    except (KeyError, IndexError):
        return tmpl


def _pick(rng, bank, tracker, key):
    """같은 유형의 문장이 짧은 기간 안에 반복되지 않도록, 이미 쓴 문장은
    그 뱅크를 다 쓸 때까지 다시 고르지 않는다. [2026-07 신설, 리뷰 피드백:
    "20년 커리어에 '꾸준한 시즌' 문장이 계속 반복된다"] rng.choice()만
    쓰면 확률적으로 같은 문장이 금방 또 나올 수 있어서, 카테고리별로
    이미 사용한 문장 목록을 추적해 전부 소진되면 그때 초기화한다."""
    used = tracker.setdefault(key, [])
    candidates = [t for t in bank if t not in used]
    if not candidates:
        used.clear()
        candidates = list(bank)
    choice = rng.choice(candidates)
    used.append(choice)
    return choice


# ── 성격/신체 특징 플레이버 문장 ────────────────────────────────
# [2026-07 신설, 리뷰 피드백: "선수 성향 데이터가 있는데 거의 안 쓴다"]
# player['personality']/['physical_trait']는 게임이 실제로 갖고 있는
# 값(constants.py의 PERSONALITY_EFFECTS/PHYSICAL_TRAIT_EFFECTS)이라
# 지어내는 게 아니라 실제 데이터를 문장에 반영하는 것이다. 챕터마다
# 한 번씩만 등장시켜 반복을 피한다.
TRAIT_NOUN = {
    "부상체질": "잦은 부상 속에서도 꺾이지 않는 근성",
    "강철체질": "부상 한 번 없는 강철 같은 몸",
    "지구력형": "지치지 않는 지구력",
    "스피드스타": "압도적인 스피드",
    "피지컬몬스터": "타의 추종을 불허하는 피지컬",
    "신체천재": "타고난 신체 능력",
}
PERSONALITY_NOUN = {
    "성실함": "성실한 자기관리",
    "리더십": "동료들을 이끄는 리더십",
    "승부욕": "남다른 승부욕",
    "긍정적": "긍정적인 태도",
    "냉철함": "냉철한 판단력",
    "완벽주의": "완벽을 추구하는 자세",
    "천재": "천재적인 축구 지능",
    "훈련광": "지독한 훈련량",
    "강철멘탈": "흔들리지 않는 멘탈",
}
TRAIT_SENTENCES = [
    "이 시기 그를 지탱한 것은 {noun_ida}.",
    "동료들과 팬들은 그의 {noun_ul} 특히 높이 평가했다.",
    "{noun_eun} 이 무렵 그가 보여준 가장 큰 무기였다.",
]


def _trait_flavor(rng, tracker, player):
    """player의 성격/신체특징 중 문장 뱅크에 있는 것 하나를 골라 짧은
    플레이버 문장을 만든다. 둘 다 없거나(예: '무난함') 매핑이 없으면
    None을 반환 — 억지로 채우지 않는다."""
    if not player:
        return None
    nouns = []
    trait = player.get("physical_trait", "")
    pers = player.get("personality", "")
    if trait in TRAIT_NOUN:
        nouns.append(TRAIT_NOUN[trait])
    if pers in PERSONALITY_NOUN:
        nouns.append(PERSONALITY_NOUN[pers])
    if not nouns:
        return None
    noun = rng.choice(nouns)
    tmpl = _pick(rng, TRAIT_SENTENCES, tracker, "trait")
    return tmpl.format(noun_ul=with_josa(noun, "을/를"), noun_eun=with_josa(noun, "은/는"),
                        noun_ida=with_josa(noun, "이었다/였다"))


def render_season(rng, seasons, idx, awards_by_year, trophy_years, retire_age, player_age_at,
                   home_country="", cup_by_year=None, cl_by_year=None, intl_by_year=None,
                   template_tracker=None, player=None, is_chapter_start=False):
    tracker = template_tracker if template_tracker is not None else {}
    s = seasons[idx]
    cat = classify_season(seasons, idx, awards_by_year, trophy_years, retire_age, player_age_at,
                           home_country=home_country)
    if cat == CAT_FINAL and s.get("matches", 0) == 0:
        tmpl = _pick(rng, FINAL_INACTIVE_TEMPLATES, tracker, "final_inactive")
    else:
        tmpl = _pick(rng, TEMPLATES[cat], tracker, f"cat:{cat}")
    text = _fill(tmpl, s)

    year = s.get("start_year", 0)
    extras = []

    if year in awards_by_year and cat != CAT_DEBUT:
        names = awards_by_year[year]
        award_line = " · ".join(names)
        extras.append(_pick(rng, AWARD_SENTENCES, tracker, "award").format(
            award_line=award_line, award_line_ro=with_josa(award_line, "으로/로")))

    # 컵대회 — 8강 이상으로 깊이 간 시즌만 본문에 언급 (매번 다 넣으면
    # 20시즌 내내 같은 말이 반복돼 오히려 지루해진다).
    cup_by_year = cup_by_year or {}
    if year in cup_by_year:
        result, comp = cup_by_year[year]
        if result in _DEEP_CUP_RESULTS and cat != CAT_CHAMPION:
            extras.append(_pick(rng, CUP_SENTENCES, tracker, "cup").format(
                competition=comp, cup_result=result))

    cl_by_year = cl_by_year or {}
    if year in cl_by_year:
        result, comp = cl_by_year[year]
        if result in _DEEP_CUP_RESULTS and cat != CAT_CHAMPION:
            extras.append(_pick(rng, CL_SENTENCES, tracker, "cl").format(
                competition=comp, cup_result=result))

    # 국가대표 — 월드컵/대륙컵이 열린 해에만 등장하므로(2~4년 주기),
    # 나올 때마다 본문에 자연스럽게 끼워 넣는다. 소집이든 미선발이든
    # 둘 다 선수의 그 해를 설명하는 중요한 정보다.
    intl_by_year = intl_by_year or {}
    if year in intl_by_year:
        comp, result = intl_by_year[year]
        if result in _INTL_MISS_RESULTS:
            extras.append(_pick(rng, INTL_MISSED_SENTENCES, tracker, "intl_missed").format(competition=comp))
        else:
            extras.append(_pick(rng, INTL_CALLED_SENTENCES, tracker, "intl_called").format(competition=comp))

    # 성격/신체특징 플레이버 — 매 시즌 넣으면 반복이 심해지니 챕터의
    # 첫 시즌에서만 한 번 등장시킨다.
    if is_chapter_start:
        flavor = _trait_flavor(rng, tracker, player)
        if flavor:
            extras.append(flavor)

    return text + ("  " + " ".join(extras) if extras else "")


def render_chapter(rng, chapter_seasons, all_seasons, awards_by_year, trophy_years,
                    retire_age, player_age_at, idx_offset_map, home_country="",
                    cup_by_year=None, cl_by_year=None, intl_by_year=None,
                    template_tracker=None, player=None):
    parts = []
    for i, s in enumerate(chapter_seasons):
        idx = idx_offset_map[id(s)]
        parts.append(render_season(rng, all_seasons, idx, awards_by_year, trophy_years,
                                    retire_age, player_age_at, home_country=home_country,
                                    cup_by_year=cup_by_year, cl_by_year=cl_by_year,
                                    intl_by_year=intl_by_year, template_tracker=template_tracker,
                                    player=player, is_chapter_start=(i == 0)))
    return "\n\n".join(parts)


def generate_prologue(player, seasons, has_trophy, awards, rng):
    name = player.get("name", "선수")
    # [버그수정, 신민용 리포트: "프롤로그는 3시즌, 에필로그는 2시즌으로
    # 서로 다르게 나온다"] len(seasons)는 병합된 (연도,팀) 레코드 개수라
    # 0경기짜리 스텁 재직까지 다 세지만, player['total_seasons']는 게임이
    # 실제로 집계하는 공식 시즌 수다 — 에필로그와 동일한 값을 쓴다.
    total_seasons = player.get("total_seasons", len(seasons))
    teams = {s.get("team_name", "") for s in seasons if s.get("team_name")}
    has_award = len(awards) > 0
    peak_ovr = player.get("peak_ovr", player.get("ovr", 0))

    openers = [
        f"모든 축구 선수의 커리어가 화려한 스포트라이트 속에서 시작하는 것은 아니다. "
        f"{name}의 이야기 역시 그랬다.",
        f"{name}. 이 이름이 축구 역사에 남긴 궤적은 처음부터 정해져 있던 것이 아니었다. "
        f"매 시즌, 매 이적, 매 선택이 쌓여 하나의 커리어를 완성했다.",
        f"긴 커리어를 마친 {name}의 발자취를 처음부터 다시 따라가 본다.",
    ]
    body = (
        f"{total_seasons}시즌 동안 {len(teams)}개 팀의 유니폼을 입었고, "
        f"{'트로피와 인연을 맺으며' if has_trophy else '트로피보다는 꾸준함으로'} "
        f"{'개인상까지 챙긴' if has_award else '자신의 자리를 지켜낸'} 선수였다. "
        f"전성기 OVR {peak_ovr}. 이제 그 커리어를 처음부터 다시 돌아본다."
    )
    return rng.choice(openers) + "\n\n" + body


def generate_epilogue(player, seasons, has_trophy, awards, intl_trophies, rng):
    name = player.get("name", "선수")
    total_m = player.get("total_matches", sum(s.get("matches", 0) for s in seasons))
    total_g = player.get("total_goals", sum(s.get("goals", 0) for s in seasons))
    total_a = player.get("total_assists", sum(s.get("assists", 0) for s in seasons))
    total_s = player.get("total_seasons", len(seasons))
    age = player.get("age", 0)

    lines = [f"{with_josa(name, '은/는')} {age}세, {total_s}시즌 만에 유니폼을 벗었다.",
             f"통산 {total_m}경기 {total_g}골 {total_a}도움."]

    if awards:
        cnt = Counter(a.get("award_type", "") for a in awards)
        order = ["발롱도르", "MVP", "득점왕", "도움왕", "베스트11", "골든글러브", "영플레이어"]
        parts = [f"{k} {cnt[k]}회" for k in order if cnt.get(k)]
        if parts:
            lines.append("개인 수상: " + " · ".join(parts) + ".")

    intl_called = any(t.get("league_name", "") not in _INTL_MISS_RESULTS for t in intl_trophies)
    if intl_trophies:
        if intl_called:
            lines.append("국가대표팀의 부름에도 응답하며 클럽을 넘어선 발자취를 남겼다.")
        else:
            lines.append("리그에서는 꾸준한 활약을 이어갔지만, 국가대표팀의 벽은 끝내 넘지 못했다.")

    closer = ("화려한 우승 트로피가 커리어를 채우진 않았지만, "
              "그가 남긴 것은 어떤 무대에서도 흔들리지 않았던 꾸준함이었다.") \
             if not has_trophy else \
             ("우승의 순간들과 함께, 그는 자신의 자리에서 늘 제 몫을 해낸 선수로 기억될 것이다.")
    lines.append(closer)
    return "\n".join(lines)


def generate_legacy_section(player, seasons, chapters, has_trophy, awards, home_country, rng):
    """[2026-07 신설, 리뷰 피드백: "선수 서사 키워드/그는 어떤 선수였는가
    섹션이 있으면 좋겠다"] 지어낸 사건 없이, 이미 계산해둔 데이터(최고
    평점 시즌, 거쳐간 국가 수, 트로피/개인상 유무)만으로 커리어 전체를
    한 문단으로 요약한다."""
    name = player.get("name", "선수")

    countries = {s.get("country", "") for s in seasons if s.get("country")}
    foreign = countries - ({home_country} if home_country else set())
    teams = {s.get("team_name", "") for s in seasons if s.get("team_name")}

    if len(foreign) >= 2:
        journey = "여러 나라를 오가며 커리어를 쌓은 저니맨"
    elif len(foreign) == 1:
        journey = "국내와 해외를 오간 선수"
    elif len(teams) == 1:
        journey = "한 팀에서 커리어 전부를 보낸 원클럽맨"
    elif len(chapters) >= 4:
        journey = "여러 팀을 거치며 끊임없이 자리를 옮긴 선수"
    else:
        journey = "국내 무대를 중심으로 커리어를 쌓은 선수"

    lines = [f"{name}의 커리어는 한마디로 {with_josa(journey, '이었다/였다')}."]

    playing = [s for s in seasons if s.get("matches", 0) > 0]
    if playing:
        best = max(playing, key=lambda s: s.get("avg_rating", 0) or 0)
        by, bt = best.get("start_year", ""), best.get("team_name", "")
        br = round(best.get("avg_rating", 0) or 0, 1)
        if br:
            lines.append(f"커리어 최고의 순간을 꼽자면 {by}년 {bt}에서의 활약(평점 {br})을 빼놓을 수 없다.")

    if awards:
        lines.append("개인상까지 손에 넣으며 숫자 이상의 것을 증명해 보였다.")
    if has_trophy:
        lines.append("트로피를 들어 올린 순간들은 그 커리어에 확실한 정점을 남겼다.")
    else:
        lines.append("화려한 트로피는 없었지만, 매 시즌 자신의 자리를 지켜낸 것 자체가 이 커리어를 설명한다.")

    return "\n".join(lines)


def generate_story(player, entries, trophies, awards, promos=None, intl_trophies=None, seed=None):
    """메인 진입점. retire_window.py에서 이 함수 하나만 호출하면 된다.
      player        : get_player() 결과 dict
      entries       : career_entries 전체 리스트 (id순). 각 행에 'country'
                      (리그가 속한 국가명) 필드를 채워서 넘겨야 해외 진출/
                      귀국 판정이 정확하다.
      trophies      : get_my_trophies() 결과 (개인상 제외). tier>0(리그),
                      -1(챔스), -2(컵대회) 행이 섞여 있고, 컵/챔스는 참가할
                      때마다 결과가 남으므로 이 함수 내부에서 실제 '우승'
                      여부를 다시 판별한다.
      awards        : awards 테이블(is_mine=1) 리스트
      intl_trophies : 국가대표 관련 trophy 리스트 (tier==0), 없으면 []
    """
    intl_trophies = intl_trophies or []
    rng = random.Random(seed if seed is not None else player.get("name", "seed"))

    seasons = build_seasons(entries)
    if not seasons:
        return "커리어 기록이 없습니다."

    awards_by_year = {}
    for a in awards:
        y = a.get("year", 0)
        awards_by_year.setdefault(y, []).append(
            f"{a.get('award_type','')}({a.get('detail','') or a.get('league_name','')})")

    trophy_years, cup_by_year, cl_by_year = _build_trophy_maps(trophies)
    intl_by_year = _build_intl_map(intl_trophies)
    has_trophy = len(trophy_years) > 0

    # [버그수정] player['age']는 '은퇴 시점'의 나이다. 그런데 이전 코드는
    # 이 나이를 첫 시즌 연도에 앵커링해서, 데뷔 시즌 나이가 은퇴 나이와
    # 같아지고(예: 36세) 마지막 시즌엔 55세가 되는 등 나이 계산이 완전히
    # 거꾸로였다. 은퇴 나이는 '마지막' 시즌 연도에 맞춰야 한다 — 그래야
    # 데뷔 시즌엔 10대 후반, 은퇴 시즌엔 실제 은퇴 나이가 나온다.
    birth_year = None
    for s in reversed(seasons):
        if s.get("start_year"):
            birth_year = s["start_year"] - (player.get("age", 20) - 1)
            break

    def player_age_at(year):
        if birth_year is None:
            return None
        return year - birth_year + 1

    idx_offset_map = {id(s): i for i, s in enumerate(seasons)}
    chapters = group_eras(seasons)
    home_country = player.get("nationality", "") or player.get("origin_nat", "")
    template_tracker = {}

    # 챕터별 시즌 유형(카테고리)을 미리 한 번 계산해둔다 — 챕터 제목
    # 판정(_chapter_character)과 본문 렌더링이 같은 분류 결과를 쓰도록.
    all_categories = [
        classify_season(seasons, i, awards_by_year, trophy_years,
                         player.get("age", 0), player_age_at, home_country=home_country)
        for i in range(len(seasons))
    ]

    out = [generate_prologue(player, seasons, has_trophy, awards, rng), ""]

    for i, chap in enumerate(chapters):
        chap_categories = [all_categories[idx_offset_map[id(s)]] for s in chap]
        is_first_chapter = (i == 0)
        is_last_chapter = (i == len(chapters) - 1)
        title, yr = _chapter_title(chap, chap_categories, is_first_chapter, is_last_chapter, rng,
                                    tracker=template_tracker)
        num = CHAPTER_NUMS[i] if i < len(CHAPTER_NUMS) else f"{i+1}부"
        out.append(f"{num} — {title} {yr}")
        out.append("")
        out.append(render_chapter(rng, chap, seasons, awards_by_year, trophy_years,
                                   player.get("age", 0), player_age_at, idx_offset_map,
                                   home_country=home_country, cup_by_year=cup_by_year,
                                   cl_by_year=cl_by_year, intl_by_year=intl_by_year,
                                   template_tracker=template_tracker, player=player))
        out.append("")

    out.append("그는 어떤 선수였는가")
    out.append("")
    out.append(generate_legacy_section(player, seasons, chapters, has_trophy, awards, home_country, rng))
    out.append("")

    out.append("에필로그")
    out.append("")
    out.append(generate_epilogue(player, seasons, has_trophy, awards, intl_trophies, rng))

    return "\n".join(out)