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

import json
import random
import re
from collections import Counter


# ══════════════════════════════════════════════════════════════════
# 0. 한국어 조사 처리 — 팀명/리그명 받침 유무에 따라 조사가 자동으로 붙는다
# ══════════════════════════════════════════════════════════════════

# [2026-07 버그수정, 신민용 리포트: "평점 9.0를 받으며"처럼 숫자로 끝나는
# 단어에서 조사가 틀린다] 숫자별 한글 발음의 받침 유무 — 0(영)/1(일)/
# 3(삼)/6(육)/7(칠)/8(팔)은 받침 있음, 2(이)/4(사)/5(오)/9(구)는 받침 없음.
# 1/7/8은 받침이 'ㄹ'이라 "으로/로" 규칙에서 "로"를 써야 한다(is_rieul).
_DIGIT_BATCHIM_INFO = {
    "0": (True, False), "1": (True, True), "2": (False, False),
    "3": (True, False), "4": (False, False), "5": (False, False),
    "6": (True, False), "7": (True, True), "8": (True, True),
    "9": (False, False),
}


def josa(word: str, pair: str) -> str:
    """word 뒤에 붙일 조사를 받침 유무에 맞게 고른다.
    pair 예: '은/는', '이/가', '을/를', '과/와', '으로/로'
    [2026-07 버그수정] 예전엔 한글이 아닌 문자(숫자 포함)로 끝나면
    무조건 "받침 없음"으로 가정했는데, "9.0"(구점영 — 영은 받침 있음)
    처럼 실제로는 받침 있는 숫자 발음에서도 항상 받침 없는 쪽 조사가
    붙었다. 마지막 글자가 숫자면 그 숫자의 한글 발음 기준으로 따로
    판정한다. 그 외(영문/이모지 등)는 여전히 받침 없다고 가정한다
    (완벽하진 않지만, 어색함보다 문장이 안 끊기는 쪽이 낫다)."""
    if not word:
        word = ""
    last = word[-1] if word else ""
    if last.isdigit():
        has_batchim, is_rieul = _DIGIT_BATCHIM_INFO.get(last, (False, False))
        jong = 8 if is_rieul else (1 if has_batchim else 0)
    else:
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


def _build_trophy_maps(trophies, seasons=None):
    """연도별로 (진짜 우승 연도 집합, (연도,팀명) 우승 쌍 집합, 컵대회 결과,
    챔스 결과)를 만든다.
    [2026-07 버그수정, 신민용 리포트: "우승 안 한 팀 시즌도 champion으로
    분류된다"] trophy_years는 연도만 담고 있어서, career_entries에 같은
    해에 여러 팀 행이 섞여 있으면(예: 0경기 스텁 + 실제 이적 후 팀) 우승과
    무관한 팀의 시즌까지 그 해라는 이유만으로 CAT_CHAMPION으로 분류됐다
    — 그 결과 NarrativeQuestion(TITLE_CHASE)이 보이지도 않는 스텁 시즌
    에서 조용히 해소되고, 정작 우승한 진짜 시즌 문단엔 해소 문장이 아예
    안 나오는 문제로 이어졌다. (연도, 팀명) 쌍까지 같이 반환해서
    classify_season()이 팀까지 맞는지 확인할 수 있게 한다.
    [2026-07 버그수정, 신민용 리포트: "2015년 스토리엔 우승이라는데 실제
    성적은 3위/24팀이다 — 데이터 자체가 모순된다"] 근본 원인은
    game_engine._lock_in_championship()이 시즌 중(30~35주차) '그 순간
    1위'라는 스냅샷만 보고 trophy_log를 확정해버리고, 이후 순위가
    바뀌어도 갱신하지 않는다는 데 있다(career_entries.team_rank는 시즌
    종료 시점 실제 최종 순위라 더 신뢰할 수 있음). 게임 엔진 쪽 타이밍
    자체를 고치는 건 시즌 진행 로직 전반을 건드려야 하는 큰 작업이라,
    여기서는 스토리 생성 시점에 두 기록을 서로 검증해서 방어한다 —
    리그 우승(tier>0)인데 그 시즌 실제 team_rank가 1이 아니면 '우승'으로
    인정하지 않는다(승격 자체는 promotion_log 기반 별도 로직이 이미
    처리하므로 그쪽엔 영향 없음)."""
    trophy_years = set()
    trophy_team_years = set()
    cup_by_year = {}
    cl_by_year = {}

    rank_lookup = {}
    if seasons is not None:
        for s in seasons:
            rank_lookup[(s.get("start_year"), s.get("team_name", ""))] = s.get("team_rank", 0)

    for t in trophies:
        y = t.get("year", 0)
        tier = t.get("tier", 0)
        result = t.get("league_name", "")
        team = t.get("team_name", "")
        if _is_real_win(t):
            if tier > 0 and seasons is not None:
                actual_rank = rank_lookup.get((y, team))
                if actual_rank is not None and actual_rank != 1:
                    # trophy_log는 우승이라 하지만 실제 최종 순위가 1위가
                    # 아니다 — 신뢰할 수 있는 쪽(실제 순위)을 따라 우승
                    # 판정에서 제외한다.
                    continue
            trophy_years.add(y)
            trophy_team_years.add((y, team))
        if tier == -2 and y not in cup_by_year:
            cup_by_year[y] = (result, t.get("competition", "컵대회"))
        if tier == -1 and y not in cl_by_year:
            cl_by_year[y] = (result, t.get("competition", "챔피언스리그"))
    return trophy_years, trophy_team_years, cup_by_year, cl_by_year


_INTL_MISS_RESULTS = ("국가대표 미선발", "국가대표 탈락", "발탁 거절", "예선 탈락", "예선 진출 실패")


def _build_intl_map(intl_trophies):
    intl_by_year = {}
    for t in intl_trophies:
        y = t.get("year", 0)
        if y not in intl_by_year:
            intl_by_year[y] = (t.get("competition", "국가대표"), t.get("league_name", ""))
    return intl_by_year


# ══════════════════════════════════════════════════════════════════
# 2.5. 커리어 메모리 (Career Memory) — 사실/이벤트/분석/추론/전환점/Arc
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, 신민용 설계 + 여러 차례 검토 반영] 시즌을 하나씩 독립적으로
# 읽으며 즉석에서 문장을 고르던 방식에, "커리어 전체를 먼저 한 번 훑어서
# 요약해두고, 그걸 참고하며 각 시즌을 쓴다"는 계층을 하나 얹는다.
#
# 원칙: 여기서 만드는 값들은 전부 "이미 있는 사실을 요약"한 것이지 새로
# 저장하는 데이터가 아니다 — DB에 새 테이블을 만들지 않고, 스토리를 생성하는
# 그 순간에 career_entries/trophy_log/awards/intl 자료를 한 번 훑어 임시
# 딕셔너리로 만들었다가 스토리 작성이 끝나면 버린다. 그래서 저장 공간·매주
# 도는 부담·새 게임 초기화 항목 추가, 이 세 가지 문제가 생기지 않는다.
#
# 계층 순서: Facts/Timeline(사실 수집, 판단 없음) → Analysis(순수 계산) →
#   Turning Point Detector → Story Arc Builder(=새 챕터 분할, group_eras
#   대체) → 이후 챕터별 문장을 쓸 때 recall/find_* 로 필요한 사실을 조회.


def _timeline_event(year, etype, **meta):
    return {"year": year, "type": etype, **meta}


def recall(memory, key):
    """career_memory['facts']에서 key에 해당하는 값을 그대로 가져온다.
    '판단'은 전혀 하지 않는다 — 저장된 사실을 그대로 돌려줄 뿐이다."""
    return (memory or {}).get("facts", {}).get(key)


def find_previous(memory, event_type, before_year):
    """주어진 연도보다 이전에 있었던 event_type 사건 중 가장 최근 것."""
    cands = [e for e in (memory or {}).get("timeline", [])
             if e["type"] == event_type and e["year"] < before_year]
    return max(cands, key=lambda e: e["year"]) if cands else None


def find_next(memory, event_type, after_year):
    """주어진 연도보다 이후에 있을 event_type 사건 중 가장 가까운 것 —
    은퇴 스토리는 이미 전체 커리어를 다 아는 채로 쓰므로 '미래'도 조회 가능."""
    cands = [e for e in (memory or {}).get("timeline", [])
             if e["type"] == event_type and e["year"] > after_year]
    return min(cands, key=lambda e: e["year"]) if cands else None


def find_all(memory, event_type):
    return [e for e in (memory or {}).get("timeline", []) if e["type"] == event_type]


def build_facts(seasons, trophy_years, awards, intl_trophies, home_country):
    """커리어에서 '한 번뿐인 사실'만 뽑는다. 판단은 하지 않는다 — 그냥
    데이터에서 가장 이르거나/가장 늦거나/가장 큰 값을 찾아올 뿐이다."""
    facts = {}
    if not seasons:
        return facts

    facts["debut_year"] = seasons[0].get("start_year")
    facts["debut_team"] = seasons[0].get("team_name")
    facts["retire_year"] = seasons[-1].get("end_year") or seasons[-1].get("start_year")

    for s in seasons:
        if s.get("goals", 0) > 0 and "first_goal_year" not in facts:
            facts["first_goal_year"] = s.get("start_year")
        if s.get("assists", 0) > 0 and "first_assist_year" not in facts:
            facts["first_assist_year"] = s.get("start_year")
        if (home_country and s.get("country") and s.get("country") != home_country
                and "first_foreign_year" not in facts):
            facts["first_foreign_year"] = s.get("start_year")
            facts["first_foreign_country"] = s.get("country")

    if trophy_years:
        facts["first_title_year"] = min(trophy_years)

    wc_years = sorted({t.get("year", 0) for t in intl_trophies
                        if "월드컵" in t.get("competition", "")})
    if wc_years:
        facts["first_world_cup_year"] = wc_years[0]
        facts["last_world_cup_year"] = wc_years[-1]

    tenure = {}
    for s in seasons:
        tn = s.get("team_name", "")
        if tn:
            tenure[tn] = tenure.get(tn, 0) + 1
    if tenure:
        facts["longest_tenure_team"] = max(tenure, key=tenure.get)

    return facts


def build_timeline(seasons, awards_by_year, trophy_years, intl_by_year):
    """연도별로 여러 개 있을 수 있는 '시즌 이벤트'를 시간순 목록으로 만든다."""
    timeline = []
    years = set(awards_by_year.keys()) | set(trophy_years) | set(intl_by_year.keys())
    for y in sorted(years):
        if y in trophy_years:
            timeline.append(_timeline_event(y, "title"))
        if y in awards_by_year:
            timeline.append(_timeline_event(y, "award", names=awards_by_year[y]))
        if y in intl_by_year:
            comp, result = intl_by_year[y]
            timeline.append(_timeline_event(y, "national_call", competition=comp, result=result))
    return timeline


def build_match_events(match_rows):
    """경기 단위 기록(match_rows)에서 해트트릭/멀티도움/고평점 경기를 뽑는다.
    match_rows가 없으면(구버전 호출·데이터 미제공 등) 빈 리스트를 반환한다 —
    이 기능이 없어도 나머지 스토리 생성은 그대로 동작해야 한다."""
    events = []
    for m in (match_rows or []):
        y = m.get("year", 0)
        opp = m.get("away_name") if m.get("is_home") else m.get("home_name")
        if (m.get("goals") or 0) >= 3:
            events.append(_timeline_event(y, "hattrick", opponent=opp, goals=m.get("goals")))
        elif (m.get("assists") or 0) >= 2:
            events.append(_timeline_event(y, "multi_assist", opponent=opp, assists=m.get("assists")))
        if (m.get("rating") or 0) >= 9.0:
            events.append(_timeline_event(y, "great_match", opponent=opp, rating=m.get("rating")))
    return events


def analyze_starting_trajectory(match_rows, min_sample=8, phase_size=15):
    """[2026-08 신설, PHASE 1 — 신민용+GPT 협업 설계] 데뷔 초반 "벤치를
    전전하다 어느 순간 주전으로 자리 잡았는가"라는 서사 하나만 우선
    구현한다(GPT 권고: 처음부터 RAPID_ASCENT 등 태그를 여러 개 만들지
    말고 이 패턴 하나만 작게 시작).

    핵심 설계:
    - "첫 N경기"를 날짜가 아니라 선수의 실제 "스쿼드 경기 순서"로 센다
      (그 팀이 뛴 경기 중 이 선수가 선발이거나 최소한 벤치에는 있었던
      경기만 — 부상/징계로 아예 결장한 경기는 "선택"과 무관하니 순서에서
      제외한다). played=True/benched=True 둘 다 스쿼드 포함, 팀 로직상
      이 둘은 상호배타적이다(_simulate_match: benched면 played는 항상
      False).
    - 표본이 min_sample(기본 8) 미만이면 아예 판정하지 않는다 — 한두
      경기만으로 "벤치를 전전했다" 같은 서사가 나오는 걸 막기 위함.
    - 초기 구간(phase_size=15경기)의 선발 비율이 높으면(65%+) 처음부터
      주전이었다는 뜻이라 EARLY_STARTER, 낮으면 EARLY_BENCH.
    - EARLY_BENCH인 경우, 그 이후 구간을 phase_size 단위로 훑어서 처음
      선발 비율이 70% 이상을 찍는 구간을 "정착 시점"으로 잡는다(마지막
      자투리 구간이 window의 절반도 안 되면 표본 부족으로 판정하지
      않는다).
    반환: dict({sample, early_start_ratio, tag, [subtag], [established_at]})
    또는 표본 부족 시 None. 이 함수는 순수 계산만 하고 문장은 만들지
    않는다(패턴 감지와 템플릿 선택을 분리하라는 설계 원칙 — analyzer가
    사실을 확정하면, 문장 쪽은 그 태그만 보고 고른다)."""
    squad = []
    for m in (match_rows or []):
        raw = m.get("detail_json")
        if not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        played = bool(payload.get("played"))
        benched = bool(payload.get("benched"))
        if not played and not benched:
            continue  # 부상/징계 등 완전 결장 — 선택 여부와 무관, 시퀀스 제외
        squad.append((m.get("year", 0), m.get("week", 0), m.get("id", 0), played))
    squad.sort(key=lambda t: (t[0], t[1], t[2]))
    if len(squad) < min_sample:
        return None

    early = squad[:phase_size]
    early_start_ratio = sum(1 for row in early if row[3]) / len(early)
    result = {"sample": len(squad), "early_start_ratio": round(early_start_ratio, 2)}

    if early_start_ratio >= 0.65:
        result["tag"] = "EARLY_STARTER"
        return result

    result["tag"] = "EARLY_BENCH"
    rest = squad[phase_size:]
    window = phase_size
    for i in range(0, len(rest), window):
        chunk = rest[i:i + window]
        if len(chunk) < max(5, window // 2):
            break
        start_ratio = sum(1 for row in chunk if row[3]) / len(chunk)
        if start_ratio >= 0.7:
            result["subtag"] = "STARTER_ESTABLISHED"
            result["established_at"] = phase_size + i + 1  # 1-based 스쿼드 경기 순번
            break
    return result


STARTING_TRAJECTORY_TEMPLATES = {
    "EARLY_BENCH_ESTABLISHED": [
        "프로 생활의 시작은 순탄하지 않았다. 초반에는 좀처럼 출전 기회를 잡지 못하고 벤치를 지키는 날이 많았지만, 어느 순간을 기점으로 주전 자리를 굳혀 나갔다.",
        "데뷔 초반에는 선발보다 벤치에서 경기를 지켜보는 시간이 길었다. 그러나 기회를 잡은 뒤로는 그 자리를 놓치지 않고 주전으로 자리매김했다.",
        "출전 기회는 쉽게 찾아오지 않았다. 초반의 그 답답한 시기를 지나, 그는 결국 스스로의 자리를 만들어냈다.",
    ],
    "EARLY_BENCH_ONGOING": [
        "프로 생활의 시작은 순탄하지 않았다. 초반에는 벤치를 지키는 날이 많았다.",
        "데뷔 초반에는 선발보다 벤치에서 경기를 지켜보는 시간이 더 길었다.",
    ],
    "EARLY_STARTER": [
        "데뷔 초기부터 꾸준히 선발 자리를 지키며 커리어를 시작했다.",
        "프로 무대에 발을 들인 순간부터 주전 경쟁에서 밀리지 않았다.",
        "이른 시기부터 선발 명단에 이름을 올리며 커리어의 첫 발을 뗐다.",
    ],
}


def build_starting_trajectory_sentence(rng, trajectory):
    """analyze_starting_trajectory() 결과(dict 또는 None)를 문장 하나로
    바꾼다. None이면(표본 부족 등) 빈 문자열."""
    if not trajectory:
        return ""
    tag = trajectory.get("tag")
    if tag == "EARLY_STARTER":
        bank = STARTING_TRAJECTORY_TEMPLATES["EARLY_STARTER"]
    elif tag == "EARLY_BENCH" and trajectory.get("subtag") == "STARTER_ESTABLISHED":
        bank = STARTING_TRAJECTORY_TEMPLATES["EARLY_BENCH_ESTABLISHED"]
    elif tag == "EARLY_BENCH":
        bank = STARTING_TRAJECTORY_TEMPLATES["EARLY_BENCH_ONGOING"]
    else:
        return ""
    return rng.choice(bank)


def _find_runs(items, key_fn, min_len):
    """items를 순서대로 훑어 key_fn(item)이 연속으로 같은 값(None 제외)을
    내는 구간을 찾는다. min_len 이상인 구간만 (category, start_idx,
    end_idx)로 반환. PHASE 5(경기 묶음 서술) 공용 헬퍼 — 승/패 연속과
    벤치/결장 연속 둘 다 이걸로 찾는다."""
    runs = []
    cur_cat, cur_start = None, None
    for i, it in enumerate(items):
        cat = key_fn(it)
        if cat != cur_cat:
            if cur_cat is not None and i - cur_start >= min_len:
                runs.append((cur_cat, cur_start, i - 1))
            cur_cat, cur_start = cat, i
    if cur_cat is not None and len(items) - cur_start >= min_len:
        runs.append((cur_cat, cur_start, len(items) - 1))
    return runs


def build_match_streaks(matches, max_sentences=3):
    """[2026-08 신설, PHASE 5: 경기 묶음 서술, 신민용 요청 — "경기를 덜
    뛰는 게 부상 때문인지 로테이션 때문인지 알 수 있잖아, 그것도 자세히
    쓰고 싶다"] 한 시즌(팀 재직 하나)의 리그 경기 목록(시간순, 각 항목에
    date/opp_name/score/result/my_played/benched)에서:
    - 3연승 이상 / 3연패 이상(둘 다 my_played=1인 경기만) → 승/패 연속
    - 4경기 이상 연속 벤치(my_played=0, benched=1) → 로테이션(스쿼드엔
      있었지만 선발에서 밀림)
    - 3경기 이상 연속 완전 결장(my_played=0, benched=0) → 부상/징계 등
      스쿼드 자체에서 빠진 결장(로테이션과 명확히 다른 사유)
    을 찾아 실제 상대팀·날짜·스코어를 인용한 문장으로 만든다. 데이터에
    없는 "왜"는 절대 지어내지 않는다 — 로테이션과 결장을 구분하는 것도
    추측이 아니라 played/benched 두 불리언이 이미 알려주는 사실이다.
    최대 max_sentences개까지만(시즌 하나가 끝없이 길어지지 않도록),
    가장 긴 구간부터 우선."""
    if not matches:
        return []

    def _result_cat(m):
        if not m.get("my_played"):
            return None
        r = (m.get("result") or "")[:1]
        return r if r in ("승", "패") else None

    def _avail_cat(m):
        if m.get("my_played"):
            return None
        return "bench" if m.get("benched") else "absent"

    runs = []
    runs += [("결과", c, s, e) for c, s, e in _find_runs(matches, _result_cat, 3)]
    runs += [("가용성", c, s, e) for c, s, e in _find_runs(matches, _avail_cat, 3)
             if c == "bench" and (e - s + 1) >= 4 or c == "absent" and (e - s + 1) >= 3]
    if not runs:
        return []
    runs.sort(key=lambda r: r[3] - r[2], reverse=True)

    out = []
    for kind, cat, s, e in runs[:max_sentences]:
        n = e - s + 1
        first, last = matches[s], matches[e]
        if kind == "결과" and cat == "승":
            out.append(f"{first['date']} {first['opp_name']}전 승리를 시작으로 "
                       f"{last['date']} {last['opp_name']}전({last['score']})까지 {n}연승을 이어갔다.")
        elif kind == "결과" and cat == "패":
            out.append(f"{first['date']} {first['opp_name']}전부터 "
                       f"{last['date']} {last['opp_name']}전({last['score']})까지 {n}연패에 빠졌다.")
        elif cat == "bench":
            out.append(f"{first['date']}부터 {last['date']}까지 {n}경기 연속 벤치에 머물렀다 — "
                       f"스쿼드에는 있었지만 선발 기회를 얻지 못한 시기였다.")
        elif cat == "absent":
            out.append(f"{first['date']}부터 {last['date']}까지 {n}경기 연속 명단에서조차 빠졌다 — "
                       f"부상이나 징계 등으로 스쿼드 자체에 들지 못한 기간이었다.")
    return out


def build_season_match_narratives(league_matches):
    """get_my_league_matches() 결과(팀 구분 없이 시간순 전체)를 받아
    (팀명, 연도)별로 묶은 뒤 build_match_streaks()를 적용한다.
    반환: {(team_name, year): [sentence, ...]}. 이 딕셔너리를 memory에
    실어두면 render_chapter가 시즌 문단 바로 뒤에 이어붙인다."""
    if not league_matches:
        return {}
    buckets = {}
    for m in league_matches:
        key = (m.get("team_name", ""), m.get("year", 0))
        buckets.setdefault(key, []).append(m)
    out = {}
    for key, ms in buckets.items():
        ms_sorted = sorted(ms, key=lambda m: (m.get("year", 0), m.get("week", 0)))
        sentences = build_match_streaks(ms_sorted)
        if sentences:
            out[key] = sentences
    return out


def build_transfer_level_sentence(prev_season, cur_season):
    """[2026-08 신설, 신민용 요청: "팀을 옮기는 게 전 시즌이랑 비슷한
    수준이면 비슷하게, 팀 수준이 낮아지면 낮아진 걸로 해석해야 한다"]
    직전 시즌과 이번 시즌의 tier(부수)·salary(연봉)만 비교한다 — 둘 다
    career_entries에 이미 있는 값이라 새 데이터 없이 바로 가능하다.
    "왜 옮겼는지"는 절대 추측하지 않고, tier가 오르면 상승/내리면 하락/
    같으면 수평이동이라는 사실만 말하고, 연봉이 뚜렷이 오르내렸으면
    ("15% 이상 차이) 그 사실도 덧붙인다 — "돈을 보고 갔다" 같은 동기
    단정은 하지 않고 연봉 숫자 자체의 방향만 보여준다.
    prev_season이 없으면(데뷔 시즌 등) 빈 문자열."""
    if not prev_season or not cur_season:
        return ""
    pt, ct = prev_season.get("tier", 0), cur_season.get("tier", 0)
    if not pt or not ct:
        return ""
    if ct < pt:
        level = "이전 소속팀보다 한 단계 높은 무대로 올라선 이적이었다."
    elif ct > pt:
        level = "이전 소속팀보다 낮은 단계의 리그로 내려간 이적이었다."
    else:
        level = "리그 단계 자체는 이전 소속팀과 같은 수평 이동이었다."
    ps, cs = prev_season.get("salary", 0), cur_season.get("salary", 0)
    pay = ""
    if ps and cs:
        if cs >= ps * 1.15:
            pay = " 연봉은 이전 소속팀보다 뚜렷하게 올랐다."
        elif cs <= ps * 0.85:
            pay = " 연봉은 오히려 이전 소속팀보다 낮아졌다."
    return level + pay


def build_season_transfer_narratives(seasons):
    """seasons(build_seasons() 결과, 시간순)를 훑어 팀이 실제로 바뀐
    지점마다 build_transfer_level_sentence()를 적용한다.
    반환: {(team_name, start_year): sentence}."""
    out = {}
    prev_playing = None
    for s in seasons:
        cur_team = s.get("team_name", "")
        if prev_playing is not None and cur_team and cur_team != prev_playing.get("team_name", ""):
            sentence = build_transfer_level_sentence(prev_playing, s)
            if sentence:
                out[(cur_team, s.get("start_year", 0))] = sentence
        if s.get("matches", 0) > 0 or s.get("team_name"):
            prev_playing = s
    return out


def build_analysis(seasons):
    """순수 계산값만 담는다 — 해석 기준(예: '전성기 평점 몇 이상')이 나중에
    바뀌어도 이 값들은 다시 계산할 필요가 없다."""
    playing = [s for s in seasons if s.get("matches", 0) > 0]
    ratings = [s.get("avg_rating", 0) or 0 for s in playing]
    analysis = {
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0,
        "peak_rating": max(ratings) if ratings else 0,
        "season_count": len(playing),
    }
    if len(ratings) >= 2:
        mean = analysis["avg_rating"]
        var = sum((r - mean) ** 2 for r in ratings) / len(ratings)
        analysis["rating_std"] = round(var ** 0.5, 2)
    else:
        analysis["rating_std"] = 0.0

    streak = best_streak = 0
    for r in ratings:
        if r >= analysis["avg_rating"]:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0
    analysis["longest_good_streak"] = best_streak
    return analysis


def extract_confirmed_injury_years(match_rows, absence_events=None):
    """[2026-07 신설] '평소보다 적게 뛴 시즌'을 통계로 추정하는 대신, 게임이
    이미 갖고 있는 진짜 근거를 먼저 확인한다.
    - 리그: match_rows(match_details) 각 행의 detail_json 안에 played/benched
      플래그가 있다 — played=False이면서 benched=False인 경기가 곧 부상 결장.
    - 컵/챔스/국대: cup_engine/champions_engine/intl_engine이 이미
      absence_reason(injury/suspension/...)을 매겨서 반환한다(스크린샷으로
      확인된 그 데이터) — reason이 정확히 'injury'인 것만 부상으로 센다.
      suspension(출전정지)·bench(벤치)는 부상이 아니므로 제외한다.
    0.0 평점 자체를 부상 신호로 쓰지 않는 이유: 벤치 대기도 rating=0으로
    똑같이 남기 때문에, 숫자 하나만 보면 벤치와 부상을 구분할 수 없다.
    반환값은 {연도 집합}뿐 — 시즌 단위로만 쓰기 때문에 주차까지는 안 남긴다."""
    years = set()
    for m in (match_rows or []):
        raw = m.get("detail_json")
        if not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if payload.get("played") is False and not payload.get("benched"):
            y = m.get("year")
            if y:
                years.add(y)
    for ev in (absence_events or []):
        if ev.get("reason") == "injury" and ev.get("year"):
            years.add(ev["year"])
    return years


def _typical_matches(seasons):
    """시즌들의 '평소 출전 수' 중앙값. infer_injury_seasons()가 내부에서
    쓰던 계산을 재사용 가능하게 분리했다 — 부상 문구의 강도(전체 결장
    vs 일부 결장)를 실제 출전 수와 비교해서 고를 때도 같은 기준을 쓴다."""
    played = [s for s in seasons if s.get("matches", 0) > 0]
    if len(played) < 3:
        return None
    return sorted(s["matches"] for s in played)[len(played) // 2]


def infer_injury_seasons(seasons, confirmed_years=None):
    """[추정 + 신뢰도] 부상 현재 상태만 저장되고 과거 이력이 안 남아있는
    경우(구버전 세이브 등)를 위한 통계적 추정 — 확정 근거(confirmed_years)가
    있는 시즌은 여기서 다시 추정하지 않고 그대로 confidence=1.0을 부여한다.
    확정 근거가 없는 시즌만 '평소 출전 수 대비 유독 적게 뛰었는데 이적/임대/
    은퇴 시즌도 아닌' 경우를 부상의 간접 정황으로 추정한다. season의 id()를
    key로, confidence(0~1)를 value로 반환 — 렌더러가 확신 정도에 따라 표현
    수위(확정/단정/추정/완곡)를 다르게 고른다."""
    confirmed_years = confirmed_years or set()
    out = {}
    for s in seasons:
        if (s.get("start_year") in confirmed_years) or (s.get("end_year") in confirmed_years):
            out[id(s)] = 1.0

    played = [s for s in seasons if s.get("matches", 0) > 0]
    if len(played) < 3:
        return out
    typical = _typical_matches(seasons)
    if not typical:
        return out
    for s in seasons:
        if id(s) in out:
            continue  # 이미 확정 근거로 채워진 시즌은 통계 추정을 덮어쓰지 않는다
        if s.get("matches", 0) <= 0 or s is seasons[-1]:
            continue  # 0경기(별도 처리)나 마지막 시즌(은퇴 사유와 겹침)은 제외
        if s.get("transfer_type") in ("임대", "이적", "오퍼"):
            continue  # 팀을 옮긴 해는 출전 감소의 다른 이유가 있으므로 제외
        ratio = s["matches"] / typical
        if ratio < 0.6:
            out[id(s)] = round(min(0.9, max(0.15, 1 - ratio)), 2)
    return out


def detect_turning_points(seasons, categories, awards_by_year, trophy_years, intl_by_year):
    """전환점 후보를 찾는다. Turning Score = 범주 중요도 + 그 해 사건 밀도.
    2년 이내로 몰린 후보는 점수가 더 높은 쪽 하나만 남긴다."""
    scored = []
    for i, s in enumerate(seasons):
        year = s.get("start_year", 0)
        cat = categories[i]
        score = 0
        if cat in (CAT_ABROAD, CAT_HOMECOMING, CAT_CHAMPION):
            score += 3
        density = int(year in awards_by_year) + int(year in trophy_years) + int(year in intl_by_year)
        score += density
        if score > 0:
            scored.append((i, year, score))

    scored.sort(key=lambda t: t[1])
    kept = []
    for idx, year, score in scored:
        if kept and year - kept[-1][1] <= 2:
            if score > kept[-1][2]:
                kept[-1] = (idx, year, score)
        else:
            kept.append((idx, year, score))
    return [idx for idx, _, _ in kept]


def build_story_arcs(seasons, categories, awards_by_year, trophy_years, intl_by_year):
    """Story Arc Builder — 챕터 분할 기준을 '같은 팀 재직'(group_eras)에서
    '전환점'으로 바꾼다. 반환 형태는 group_eras()와 동일(시즌 dict 리스트의
    리스트)해서, 이후 챕터 제목/본문을 만드는 기존 파이프라인
    (_chapter_character/_chapter_title/render_chapter)을 그대로 재사용한다
    — 그 함수들이 이미 '첫 챕터=debut', '마지막 챕터=final'을 강제하므로
    Closing Arc 요건도 자동으로 충족된다."""
    narrative = [s for s in seasons if is_narrative_season(s)]
    if seasons and not any(s is seasons[-1] for s in narrative):
        narrative.append(seasons[-1])
    if not narrative:
        return []

    idx_map = {id(s): i for i, s in enumerate(seasons)}
    turning_indices = set(detect_turning_points(
        seasons, categories, awards_by_year, trophy_years, intl_by_year))

    arcs = []
    cur = [narrative[0]]
    for i in range(1, len(narrative)):
        s = narrative[i]
        if idx_map[id(s)] in turning_indices:
            arcs.append(cur)
            cur = [s]
        else:
            cur.append(s)
    arcs.append(cur)
    return arcs


# ══════════════════════════════════════════════════════════════════
# 2.6. NarrativeQuestion — "사건"이 아니라 "질문"으로 이어지는 서사
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, v8 설계 우선순위 3, 신민용+GPT 다차례 검토] 예전 Narrative
# Debt 초안은 이벤트 타입(STARTER_BATTLE, TITLE_DROUGHT...) 중심이었는데,
# GPT 지적대로 "독자는 이벤트보다 질문을 오래 기억한다" — "이 선수는
# 결국 주전을 차지할 수 있을까?" 같은 질문 하나가 열리고, 몇 시즌에 걸쳐
# 진행되다가, 닫힌다. 동시에 열리는 질문은 장기(LONG_TERM) 1개 + 단기
# (SHORT_TERM) 1개까지만 — 슬롯이 이미 차 있으면 새 질문 후보는 그냥
# 무시한다(같은 종류가 계속 열렸다 닫혔다 하며 산만해지는 것 방지).
#
# 판정 재료는 전부 이미 있는 카테고리(classify_season)뿐 — 새 데이터,
# 새 DB 테이블 전혀 불필요.

QUESTION_OPEN_SENTENCES = {
    "STARTER_BATTLE": [
        "과연 그는 다시 팀의 중심으로 돌아올 수 있을까 — 그 물음표가 이때부터 그를 따라다니기 시작했다.",
        "다시 주전 자리를 되찾을 수 있을지, 그 질문이 이 시즌부터 선명해졌다.",
    ],
    "TITLE_CHASE": [
        "커리어 첫 우승에 다다를 수 있을까 — 그 갈증은 이 시즌부터 뚜렷해졌다.",
        "첫 우승이라는 물음표가 그의 커리어에 붙기 시작한 것도 이 무렵부터였다.",
    ],
    # [2026-07 신설, 신민용 리포트: "2005년에 이미 우승했는데 2006년에
    # '첫 우승이라는 물음표'가 또 나온다 — 커리어 이벤트를 기억 못 해서
    # 생기는 문제"] 이미 한 번 이상 우승한 뒤 다시 열리는 우승 갈증은
    # TITLE_CHASE와 다른 타입(TITLE_CHASE_AGAIN)으로 취급해 "첫 우승"이
    # 아니라 "또 한 번의 우승"으로 표현한다.
    "TITLE_CHASE_AGAIN": [
        "우승의 기쁨을 다시 한번 맛볼 수 있을까 — 그 물음표가 이 시즌부터 새로 시작됐다.",
        "한 번의 우승으로 만족할 수는 없었다 — 또 다른 트로피를 향한 갈증이 이 무렵부터 다시 고개를 들었다.",
    ],
    "INJURY_COMEBACK": [
        "부상에서 완전히 돌아올 수 있을지가 다음 시즌의 관심사가 됐다.",
        "이 부상을 딛고 얼마나 회복할 수 있을지, 그건 다음 시즌이 답해줄 문제였다.",
        "부상 이후 경기력을 어디까지 되찾을지가 당장의 과제로 남았다.",
    ],
    "ADAPTATION": [
        "새로운 환경에 얼마나 빨리 적응할 수 있을지가 그 앞에 놓인 과제였다.",
        "낯선 팀에서 자기 자리를 만들어낼 수 있을지가 당장의 물음이었다.",
    ],
    "NATIONAL_TEAM_CHASE": [
        "국가대표팀에 대한 갈증이 이 시즌부터 그를 따라다니기 시작했다.",
        "대표팀 명단에 들 수 있을지가, 이때부터 커리어의 또 다른 물음표가 됐다.",
    ],
}
QUESTION_PROGRESS_SENTENCES = {
    "STARTER_BATTLE": [
        "주전 자리를 되찾기 위한 싸움은 이 시즌에도 계속됐다.",
        "그 경쟁은 여전히 현재진행형이었다.",
    ],
    "TITLE_CHASE": [
        "첫 우승을 향한 갈증은 이 시즌에도 채워지지 않았다.",
        "우승이라는 물음표는 이 시즌에도 여전히 열려 있었다.",
    ],
    "TITLE_CHASE_AGAIN": [
        "두 번째 우승을 향한 갈증은 이 시즌에도 채워지지 않았다.",
        "다시 한번 정상에 서고 싶다는 마음은 이 시즌에도 여전했다.",
    ],
    "INJURY_COMEBACK": [
        # [2026-08 수정, 신민용+GPT 지적: "완전히 회복했다"는 의학적
        # 판단은 데이터에 없다] 부상 결장 이후 다시 뛴다는 사실만 확인
        # 가능하므로, "완전히" 회복했다는 단정은 빼고 사실 위주로 서술.
        "부상 이후 경기력을 완전히 되찾기까지는 조금 더 시간이 필요해 보였다.",
        "회복은 더디게, 그러나 착실히 이어졌다.",
        "예전의 움직임을 조금씩 되찾아가는 중이었다.",
    ],
}

# [2026-07 신설, v9 우선순위 4: 국대 좌절의 감정 변화, GPT 검토: "사람
# 이라면 처음엔 '아직 어리니까', 몇 년 뒤 '선택받지 못했다', 후반엔
# '현실적인 목표가 아니었다'처럼 감정이 변한다 — 지금은 6번 모두 같은
# 문장이다"] NATIONAL_TEAM_CHASE만 진행 횟수에 따라 다른 단계의 문장을
# 쓴다 — 같은 '진행 중' 의미라도 몇 번째 미선발인지에 따라 어투 자체가
# 달라진다. 판정은 이미 세는 진행 카운터(_qprog_used)를 그대로 재사용
# 한다 — 새 데이터 없음.
QUESTION_PROGRESS_STAGED = {
    "NATIONAL_TEAM_CHASE": {
        "STAGE1": [   # 1~2번째 미선발 — 아직 여유
            "아직 나이가 있어, 다음을 기약할 수 있는 시점이었다.",
            "아쉬운 결과였지만, 시간은 아직 그의 편이었다.",
        ],
        "STAGE2": [   # 3~4번째 미선발 — 반복되는 좌절
            "꾸준한 활약에도 좀처럼 대표팀의 문턱을 넘지 못했다.",
            "리그에서의 활약과는 별개로, 대표팀 명단은 여전히 먼 이야기였다.",
        ],
        "STAGE3": [   # 5번째 이상 — 거듭된 미선발
            # [2026-08 수정, 신민용+GPT 지적] "그 꿈은 희미해져 갔다"는
            # 선수의 내면을 단정하는 문장이라 미선발 횟수라는 사실만
            # 남기는 쪽으로 바꾼다.
            "대표팀 명단은 이번에도 그의 이름을 비켜갔다.",
            "나이가 들수록 대표팀 발탁 가능성은 점점 낮아지고 있었다.",
        ],
    },
}

QUESTION_RESOLVED_SENTENCES = {
    "STARTER_BATTLE": [
        "마침내 그는 다시 팀의 중심 자리로 돌아왔다.",
        "오랜 싸움 끝에, 그는 다시 주전 자리를 되찾았다.",
    ],
    "TITLE_CHASE": [
        "오랜 기다림 끝에, 마침내 우승의 순간이 찾아왔다.",
        "그 갈증은 이 시즌 마침내 트로피로 채워졌다.",
    ],
    "TITLE_CHASE_AGAIN": [
        "그 갈증은 이 시즌 다시 한번 트로피로 채워졌다.",
        "두 번째 우승과 함께, 그는 다시 한번 정상에 섰다.",
    ],
    "INJURY_COMEBACK": [
        # [2026-08 수정, 신민용+GPT 지적] "완전히 떨쳐냈다"/"완전한 몸
        # 상태"는 의학적 판단이라 데이터로 확인 불가 — 결장 이후 다시
        # 출전했다는 사실만 남긴다.
        "부상 결장 이후 다시 그라운드에 모습을 드러냈다.",
        "다행히 그 부상은 긴 그림자를 남기지 않았다.",
        "부상에서 돌아와 다시 꾸준히 경기에 나섰다.",
    ],
    "ADAPTATION": [
        "적응기는 그렇게 지나갔고, 그는 새 환경에 자리를 잡았다.",
        "낯설던 환경도 어느새 익숙한 무대가 되어 있었다.",
    ],
    "NATIONAL_TEAM_CHASE": [
        "마침내 국가대표팀의 부름이 찾아왔다.",
        "오랜 기다림 끝에, 그는 태극마크를 달게 됐다.",
    ],
}


def build_narrative_questions(seasons, categories, memory, intl_by_year=None):
    """시즌을 연대순으로 훑으며 NarrativeQuestion을 열고/진행시키고/닫는다.
    반환값은 {id(season): [(state, type), ...]} — build_season_narrative()가
    이 값을 보고 문장을 고른다.
    [2026-07 확장, v9 우선순위 4: 국대 좌절의 감정 변화] 장기(주전경쟁/
    우승도전)·단기(부상/적응)와 별개로 '국가대표' 전용 슬롯을 하나 더
    둔다 — 클럽 성적과는 결이 다른 독립된 서사 축이라 따로 추적한다."""
    long_q = None    # 현재 열린 장기 질문: {"type":..., "opened_year":...}
    short_q = None   # 현재 열린 단기 질문
    natl_q = None    # 현재 열린 국가대표 질문 (장기/단기와 독립)
    per_season = {}
    # [2026-07 신설, 신민용 리포트: "이미 우승했는데 또 '첫 우승' 물음표가
    # 열린다"] 지금까지 CAT_CHAMPION 시즌을 한 번이라도 지났는지 추적해서,
    # 두 번째 이후의 우승 갈증은 TITLE_CHASE_AGAIN으로 연다.
    has_won_before = False

    for idx, s in enumerate(seasons):
        cat = categories[idx] if idx < len(categories) else None
        year = s.get("start_year", 0)
        events = []  # 이 시즌에 있었던 질문 이벤트들 (장기+단기+국대 동시 가능)

        # ── 장기 질문: 주전 경쟁 ──
        if long_q is None and cat in (CAT_BENCH, CAT_RELEGATION):
            long_q = {"type": "STARTER_BATTLE", "opened_year": year}
            events.append(("OPEN", "STARTER_BATTLE"))
        elif long_q and long_q["type"] == "STARTER_BATTLE":
            if cat in (CAT_RISING, CAT_CHAMPION, CAT_VETERAN):
                events.append(("RESOLVED", "STARTER_BATTLE"))
                long_q = None
            else:
                events.append(("PROGRESS", "STARTER_BATTLE"))

        # ── 장기 질문: 우승 도전 (STARTER_BATTLE이 비어있을 때만 후보) ──
        if long_q is None and cat in (CAT_STEADY, CAT_RISING, CAT_NEW_CHALLENGE, CAT_ABROAD):
            next_title = find_next(memory, "title", year) if memory else None
            if next_title and next_title["year"] > year:
                _tq_type = "TITLE_CHASE_AGAIN" if has_won_before else "TITLE_CHASE"
                long_q = {"type": _tq_type, "opened_year": year}
                events.append(("OPEN", _tq_type))
        elif long_q and long_q["type"] in ("TITLE_CHASE", "TITLE_CHASE_AGAIN"):
            if cat == CAT_CHAMPION:
                events.append(("RESOLVED", long_q["type"]))
                long_q = None
            else:
                events.append(("PROGRESS", long_q["type"]))

        # ── 단기 질문: 부상 복귀 (실제로 부상이 이어지는 동안은 PROGRESS로
        # 계속 진행되다가, 이 시즌에 부상 신호가 사라지면 그제서야 해소) ──
        # [2026-07 수정, v9 GPT 검토: "2008 부상 → 2009 회복과정 → 2010
        # 마침내 되찾았다 — 이렇게 3시즌이 하나의 이야기여야 한다"] 예전엔
        # 부상이 열리면 실제 부상 지속 여부와 무관하게 무조건 다음 시즌에
        # 강제로 해소됐다(1시즌짜리 OPEN→RESOLVED만 가능). 이제 이 시즌에도
        # 부상 신뢰도(conf)가 남아있으면 PROGRESS로 계속 이어가고, 정말
        # 신호가 사라진 시즌에만 RESOLVED로 닫는다 — 여러 시즌짜리 부상도
        # 하나의 서사로 이어질 수 있다.
        # [2026-07 버그수정, 신민용 리포트: "36경기나 뛰었는데(78% 출전)
        # '부상에서 돌아올 수 있을지'라는 질문이 열린다"] confirmed_years
        # 기반이면 컵 경기 딱 1번 결장만 해도 confidence가 채워지는데,
        # 그 정도로는 '부상 복귀'라는 시즌급 서사를 열 근거가 안 된다.
        # 실제 출전 수가 평소 대비 뚜렷이 줄어든 시즌에만 이 질문을 연다.
        conf = (memory or {}).get("injury_seasons", {}).get(id(s))
        _typical_m = (memory or {}).get("typical_matches")
        _apps_ratio_q = (s.get("matches", 0) / _typical_m) if _typical_m else 0
        conf_significant = conf is not None and (not _typical_m or _apps_ratio_q < 0.75)
        if short_q is None and conf_significant:
            short_q = {"type": "INJURY_COMEBACK", "opened_year": year}
            events.append(("OPEN", "INJURY_COMEBACK"))
        elif short_q and short_q["type"] == "INJURY_COMEBACK":
            if conf_significant:
                events.append(("PROGRESS", "INJURY_COMEBACK"))
            else:
                events.append(("RESOLVED", "INJURY_COMEBACK"))
                short_q = None

        # ── 단기 질문: 새 환경 적응 (이적/해외진출/복귀 다음 시즌 자동 해소) ──
        if short_q is None and cat in (CAT_NEW_CHALLENGE, CAT_ABROAD, CAT_HOMECOMING):
            short_q = {"type": "ADAPTATION", "opened_year": year}
            events.append(("OPEN", "ADAPTATION"))
        elif short_q and short_q["type"] == "ADAPTATION":
            events.append(("RESOLVED", "ADAPTATION"))
            short_q = None

        # ── 국가대표 질문 (독립 슬롯) — 미선발이 시작되면 열리고, 미선발이
        # 반복되는 동안 진행되다가, 실제로 소집되면 그제서야 닫힌다.
        # [2026-07 신설] 판정 재료는 intl_by_year(이미 있는 국대 소집/
        # 미선발 결과)뿐 — 새 데이터 없음.
        if intl_by_year and year in intl_by_year:
            _, intl_result = intl_by_year[year]
            is_miss = intl_result in _INTL_MISS_RESULTS
            if natl_q is None and is_miss:
                natl_q = {"type": "NATIONAL_TEAM_CHASE", "opened_year": year}
                events.append(("OPEN", "NATIONAL_TEAM_CHASE"))
            elif natl_q and natl_q["type"] == "NATIONAL_TEAM_CHASE":
                if is_miss:
                    events.append(("PROGRESS", "NATIONAL_TEAM_CHASE"))
                else:
                    events.append(("RESOLVED", "NATIONAL_TEAM_CHASE"))
                    natl_q = None

        if events:
            per_season[id(s)] = events

        # [2026-07 신설] 이 시즌이 우승 시즌이었으면, 다음 시즌부터는
        # TITLE_CHASE_AGAIN(첫 우승이 아닌 재도전)으로 열리게 플래그를
        # 갱신한다. 반드시 이 시즌의 OPEN/PROGRESS/RESOLVED 판정이 끝난
        # "뒤"에 갱신해야 한다 — 그래야 우승을 확정지은 바로 그 시즌
        # 자체는 정상적으로 TITLE_CHASE의 RESOLVED로 처리된다.
        if cat == CAT_CHAMPION:
            has_won_before = True

    return per_season


def build_career_memory(seasons, awards_by_year, trophy_years, intl_by_year,
                         awards, intl_trophies, home_country, match_rows=None,
                         absence_events=None, categories=None):
    """위 함수들을 묶어 하나의 '커리어 메모리' 딕셔너리로 만든다.
    generate_story()가 시작할 때 한 번만 호출한다.
    [2026-07 수정] absence_events(컵/챔스/국대의 확정 부상 결장 기록)가
    있으면 injury_seasons를 추정이 아니라 확정 근거로 우선 채운다.
    [2026-07 수정, v8 설계 3] categories가 있으면 NarrativeQuestion도
    함께 계산한다 — timeline이 먼저 완성돼야 find_next("title")를 쓸 수
    있으므로, 그 값들을 다 채운 뒤 마지막에 questions를 계산해 추가한다."""
    facts = build_facts(seasons, trophy_years, awards, intl_trophies, home_country)
    timeline = build_timeline(seasons, awards_by_year, trophy_years, intl_by_year)
    timeline += build_match_events(match_rows)
    timeline.sort(key=lambda e: e["year"])
    confirmed_years = extract_confirmed_injury_years(match_rows, absence_events)
    memory = {
        "facts": facts,
        "timeline": timeline,
        "analysis": build_analysis(seasons),
        "injury_seasons": infer_injury_seasons(seasons, confirmed_years),
        "typical_matches": _typical_matches(seasons),
    }
    if categories:
        memory["questions"] = build_narrative_questions(seasons, categories, memory, intl_by_year=intl_by_year)
    else:
        memory["questions"] = {}
    return memory


# 회고(foreshadow) 문장 — 어려운 챕터(방랑/부진/해외)의 도입부에서, 그
# 시점 이후 몇 년 안에 첫 우승이 있었다는 걸 미리 암시한다. 스토리 전체에서
# 한 번만 쓴다(반복되면 오히려 뻔해짐).
FORESHADOW_TITLE_SENTENCES = [
    "당시엔 몰랐지만, 이로부터 {n}년 뒤 그는 커리어 첫 우승을 들어올리게 된다.",
    "이때만 해도 누구도 예상하지 못했지만, {n}년 후 그는 마침내 우승 트로피를 품에 안는다.",
    "돌이켜보면 이 시기는 {n}년 뒤 찾아올 첫 우승의 준비 과정이었다.",
]

# [2026-07 신설, 신민용 리포트: "2005년에 이미 우승했는데, 2009년 강등
# 시즌 회고에 '6년 뒤 찾아올 첫 우승의 준비 과정'이라고 나온다 — 이미
# 한 번 우승한 뒤인데 또 '첫 우승'이라고 한다"] find_next(memory,"title",
# year)가 찾아주는 다음 우승이 실제로는 두 번째 이후 우승인 경우 이
# 뱅크를 대신 쓴다. "첫 우승"이라는 말 자체를 빼고 "또 한 번의 우승"으로
# 표현한다.
FORESHADOW_TITLE_AGAIN_SENTENCES = [
    "당시엔 몰랐지만, 이로부터 {n}년 뒤 그는 또 한 번 우승 트로피를 들어올리게 된다.",
    "이때만 해도 알 수 없었지만, {n}년 후 그는 두 번째 우승을 품에 안는다.",
    "돌이켜보면 이 시기는 {n}년 뒤 찾아올 또 한 번의 우승으로 이어지는 길이었다.",
]

# [2026-07 신설, 신민용 리포트: "24위/22위 같은 진짜 바닥 시즌까지도
# '준비 과정'이라며 항상 긍정적으로 포장한다"] 위 두 뱅크는 반드시 미래에
# 우승이 있다는 전제하에서만 쓰인다. 하지만 그 시즌 자체는 그냥 나쁜
# 시즌일 수도 있다 — 몇 년 뒤 우승과 무관하게, 이 시기 자체의 무게를
# 미화 없이 짚어주는 뱅크를 별도로 둔다. is_severe_relegation인 시즌엔
# 이쪽을 우선 사용해, "언젠가 보상받을 고생"이 아니라 "그 자체로 힘들었던
# 시기"로 남긴다.
FORESHADOW_BLEAK_SENTENCES = [
    "이 시기를 미화할 필요는 없다 — 그저 버텨내야 했던 시간이었다.",
    "훗날의 우승이 이 시절의 무게를 지워주지는 않는다. 이때는 그저 힘든 시기였다.",
    "모든 고생이 보상받는 것은 아니다. 이 시기는 그 자체로 힘겨운 시간이었을 뿐이다.",
]

# [2026-07 신설, v8 설계 우선순위 6] Retrospective Commentary — 은퇴
# 시점 지식으로 과거를 재해석하는 문장. FORESHADOW_TITLE_SENTENCES가
# "몇 년 뒤 우승한다"는 구체적 사건을 미리 스포일러하는 것과 달리, 이건
# 특정 사건을 짚지 않고 "이 시즌 자체가 분기점이었다"는 무게감만 준다 —
# 그래서 우승과 무관한 전환점(이적/역할 변화 등)에도 쓸 수 있다.
RETROSPECTIVE_SENTENCES = [
    "당시에는 평범한 시즌처럼 보였다. 그러나 훗날 돌아보면, 이때가 커리어의 분기점이었다.",
    "그때는 몰랐지만, 지나고 보면 이 시즌이 이후의 모든 것을 바꿔놓았다.",
    "겉보기엔 특별할 것 없던 한 시즌이었다 — 하지만 커리어 전체를 놓고 보면 이야기가 달라진다.",
]

# [2026-07 신설, v9 우선순위: 회고 확대] 이 시즌 이후 실제 우승까지
# 남은 햇수(n)를 알 수 있을 때 쓰는, 더 구체적인 회고 문장 — 막연히
# "분기점이었다"가 아니라 "그 우승의 밑거름이 됐다"처럼 실제 결과와
# 직접 연결한다.
RETROSPECTIVE_LINKED_SENTENCES = [
    "당시에는 힘겨운 시즌처럼 보였다. 하지만 훗날 돌아보면, 이 시기를 버텨낸 경험이 {n}년 뒤 찾아올 우승의 밑거름이 되었다.",
    "그때는 그저 견디는 시간처럼 느껴졌다. 그러나 이 시기가 없었다면 {n}년 뒤의 우승도 없었을지 모른다.",
    "지나고 보면, 이 시즌의 인내가 {n}년 후 트로피로 돌아왔다고 해도 과언이 아니다.",
]

# 부상 추정 문장 — 신뢰도(confidence) 구간별로 표현 수위를 다르게 한다.
# [2026-07 신설] CONFIRMED는 추정이 아니라 게임 기록(결장 사유)으로
# 확인된 시즌 전용 — 그래서 HIGH보다도 더 단정적으로 쓴다.
INJURY_CONFIRMED_SENTENCES = [
    "실제로 부상으로 결장한 기록이 남아있는 시즌이었다.",
    "부상 때문에 경기에 나서지 못한 적이 있었다.",
    "이 시즌엔 부상으로 그라운드를 떠나 있어야 했던 순간이 있었다.",
]
INJURY_INFERENCE_SENTENCES_HIGH = [
    "부상으로 상당 기간 그라운드를 떠나 있어야 했다.",
    "부상이 발목을 잡으며 시즌 내내 힘든 시간을 보냈다.",
]
INJURY_INFERENCE_SENTENCES_MID = [
    "부상 여파가 있었던 것으로 보이는 시즌이었다.",
    "몸 상태 문제로 출전에 어려움을 겪은 듯하다.",
]
INJURY_INFERENCE_SENTENCES_LOW = [
    "이유는 명확히 남아있지 않지만, 이 시즌은 유독 출전 시간이 줄었다.",
    "평소보다 출전 기회 자체가 크게 줄어든 시즌이었다.",
]


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
                     home_country="", trophy_team_years=None, relegation_years=None):
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
    # [2026-07 버그수정] trophy_team_years가 주어지면(팀명까지 기록된
    # 우승 정보) 그 팀이 실제로 이 시즌의 팀과 일치할 때만 CAT_CHAMPION —
    # 같은 해 다른 팀(0경기 스텁 등)까지 우승 시즌으로 오분류되는 것을
    # 막는다. trophy_team_years를 안 넘기면(구버전 호출 호환) 예전처럼
    # 연도만으로 판정한다.
    if year in trophy_years:
        if trophy_team_years is None or (year, s.get("team_name", "")) in trophy_team_years:
            return CAT_CHAMPION

    # [2026-07 버그수정, 신민용 리포트: "2004년 24위/24팀인데 '특별할 것
    # 없는 시즌'으로 나온다"] 개인 수상 여부(awards_by_year)가 팀의 성적과
    # 무관하게 최우선으로 체크되고 있어서, 팀이 꼴찌를 해도 그해 베스트11만
    # 받으면 무조건 CAT_STEADY로 확정되고 CAT_RELEGATION 후보 자체가 안
    # 들어갔다. 심각한 강등권 성적(순위 비율 0.75 이상)은 개인 수상보다
    # 먼저 체크해서, "팀은 무너졌지만 개인은 인정받았다"는 대비가 살아
    # 있는 CAT_RELEGATION 쪽으로 분류되게 한다.
    # [2026-07 버그수정, 신민용 리포트: "2002년 삼성 FC가 실제로는 강등
    # 안 했는데 스토리에 강등했다고 나온다"] 이전엔 순위 비율(0.75 이상)
    # 만으로 "강등"을 추측했는데, 이건 실제 게임 데이터(promotion_log —
    # game_engine.get_my_promotions())와 무관한 자체 추측이었다. 실제로는
    # 리그 순위가 낮아도 강등선 안에 안 들 수 있고(리그마다 강등 인원이
    # 다름), 반대로 상대적으로 높은 순위라도 강등될 수 있다. relegation_
    # years(실제 강등 기록에서 만든 (연도, 팀명) 집합)가 주어지면 그것만
    # 신뢰하고, 안 주어지면(구버전 호출 호환) 예전처럼 순위 비율로
    # 추정한다.
    _rank = s.get("team_rank", 0)
    _total = s.get("_total_teams", 0)
    if relegation_years is not None:
        severe_relegation = bool(s.get("matches", 0) > 0
                                  and (year, s.get("team_name", "")) in relegation_years)
    else:
        severe_relegation = bool(_rank and _total and _rank / _total >= 0.75 and s.get("matches", 0) > 0)

    if year in awards_by_year and not severe_relegation:
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

    if severe_relegation:
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
        "{stat_phrase}을 기록했고, 팀은 {wdl}로 시즌을 마쳐 {rank_str}에 자리했다. "
        "화려한 조명을 받는 데뷔는 아니었지만, 낮은 곳에서부터 시작하는 길을 택한 것이다.",

        "커리어의 첫 페이지는 {team} 소속으로 열렸다. {year}년 {apps}경기 {stat_phrase} — "
        "숫자 자체보다, 성인 무대에 처음 나선 어린 선수가 곧바로 자기 자리를 만들어 갔다는 사실이 더 중요했다. "
        "팀은 {rank_str}로 마쳤지만, 그의 이름은 이미 조금씩 알려지기 시작했다.",

        "{year}년, {team}에서 프로 무대 첫발을 뗐다. {apps}경기에 나서 {stat_phrase}을 남겼고, "
        "팀 성적은 {rank_str}({wdl})였다. 화려하진 않았지만, 분명한 시작이었다.",

        "모든 것은 {year}년 {team}에서 시작됐다. 데뷔 시즌 {apps}경기 {stat_phrase}, "
        "팀은 {wdl}로 {rank_str}에 자리했다 — 앞으로 이어질 긴 여정의 첫 장이었다.",
    ],

    CAT_RISING: [
        "{team} 소속으로 맞은 {year}년, 확실한 상승세를 보여줬다. {apps}경기 {stat_phrase}, "
        "평점 {rating}. 전 시즌보다 눈에 띄게 나아진 경기력이었고, 팀 내에서의 입지도 그만큼 단단해졌다.",

        "{year}년은 발전이 뚜렷했던 해였다. {team}에서 {apps}경기를 뛰며 {stat_phrase}, "
        "평점 {rating_reul} 기록했다. 팀은 {wdl}로 {rank_str}를 차지했고, 개인의 성장세가 팀 성적과 맞물려 갔다.",

        "한 단계 올라선 {year}년이었다. {team}에서 {apps}경기 {stat_phrase}, 평점 {rating} — "
        "이전 시즌과는 분명히 다른 무게감이었다.",

        "{year}년, {team}에서의 경기력이 눈에 띄게 좋아졌다. {apps}경기 {stat_phrase}을 기록하며 "
        "평점 {rating}까지 끌어올렸고, 팀도 {rank_str}({wdl})로 함께 상승세를 탔다.",
    ],

    CAT_STEADY: [
        "{year}년에도 {team}에서 꾸준한 한 해를 보냈다. {apps}경기 {stat_phrase}, "
        "평점 {rating}. 극적인 반전은 없었지만, 그 꾸준함 자체가 그의 가치였다. 팀은 {wdl}로 {rank_str}에 머물렀다.",

        "특별한 반전도, 특별한 부진도 없었던 {year}년. {team} 소속으로 {apps}경기에 나서 "
        "{stat_phrase}을 보탰다. 팀 성적은 {rank_str}({wdl}) — 안정 속에서 한 시즌을 더 쌓았다.",

        "{year}년, {team}에서 담담하게 시즌을 이어갔다. {apps}경기 {stat_phrase}, "
        "팀은 {rank_str}({wdl})로 시즌을 마쳤다 — 큰 사건은 없었지만 제 몫은 분명히 했다.",

        "굴곡 없는 한 해였다. {year}년 {team}에서 {apps}경기 {stat_phrase}을 기록했고, "
        "팀은 {wdl}로 {rank_str}에 자리했다.",

        "{team_eun} {year}년 {rank_str}로 시즌을 마쳤다 — 그 안에서 그는 {apps}경기 {stat_phrase}으로 "
        "제 몫을 다했다. 요란하진 않았지만 분명한 존재감이었다.",

        "특별할 것 없어 보이는 시즌이었다. 그러나 {year}년 {team}에서의 {apps}경기, {stat_phrase}에는 "
        "그 나름의 무게가 있었다. 팀은 {rank_str}({wdl}).",

        "{apps}경기, {stat_phrase} — {year}년 {team}에서의 기록은 그리 요란하지 않았다. "
        "팀 성적도 {rank_str}({wdl})에 머물렀지만, 꾸준함만큼은 분명했다.",

        "시간은 {year}년에도 {team}에서 그렇게 흘러갔다. {apps}경기 {stat_phrase}, "
        "팀은 {wdl}로 {rank_str}를 기록하며 시즌을 마쳤다.",
    ],

    CAT_DECLINING: [
        "{year}년은 쉽지 않은 한 해였다. {team}에서 {apps}경기에 나섰지만 평점은 {rating_ro} "
        "이전보다 떨어졌고, {stat_phrase}에 그쳤다. 팀 역시 {rank_str}({wdl})로 고전했다.",

        "상승세가 꺾인 시즌이었다. {year}년 {team} 소속으로 {apps}경기를 뛰었지만 경기력은 예전만 못했다 — "
        "평점 {rating}, {stat_phrase}. 팀 성적도 {rank_str}에 그쳐 어려운 시기가 겹쳤다.",

        "{year}년, {team}에서 좀처럼 리듬을 찾지 못했다. {apps}경기 {stat_phrase}, 평점 {rating} — "
        "이전 시즌들에 비하면 아쉬움이 남는 한 해였다.",

        "모든 선수에게 부침은 있다. {year}년 {team}에서의 시즌이 그랬다. {apps}경기에 나섰지만 "
        "평점 {rating}에 그쳤고, 팀도 {rank_str}({wdl})로 힘든 한 해를 보냈다.",

        "팀은 {year}년 {rank_str}({wdl})로 고전했다. 그 한가운데서 그 역시 {apps}경기, 평점 {rating}으로 "
        "예년만 못한 한 해를 보냈다.",

        "숫자만 보면 티가 안 날 수도 있다. 하지만 {year}년 {team}에서의 {apps}경기, 평점 {rating}은 "
        "분명 이전 시즌들에 못 미치는 기록이었다.",
    ],

    CAT_RELEGATION: [
        "{team_eun} {year}년 하위권에서 힘든 싸움을 이어갔다({wdl}, {rank_str}). "
        "그런 팀 상황 속에서도 {apps}경기 {stat_phrase}을 기록하며 흔들리지 않는 모습을 보였다 — "
        "무너지는 팀에서 버티는 경험은, 편하게 이기는 경험과는 다른 무게를 남긴다.",

        "{year}년 {team_eun} 순위표 하단에서 벗어나지 못했다({rank_str}, {wdl}). "
        "팀 전체가 흔들리는 와중에도 {apps}경기에 나서 {stat_phrase}으로 제 몫을 했다.",

        "강등 위기가 감돌던 {year}년, {team}에서 {apps}경기를 뛰었다. {stat_phrase}을 남겼지만 "
        "팀은 결국 {rank_str}({wdl})까지 밀려났다 — 하위권 팀의 중심을 잡아야 하는 부담이 만만치 않았다.",

        "{year}년의 {team_eun} 순위표 아래쪽에서 한 해 내내 씨름했다({rank_str}, {wdl}). "
        "그 속에서도 {apps}경기 {stat_phrase}으로 자신의 자리를 지켰다.",
    ],

    CAT_CHAMPION: [
        "🏆 {year}년, {team} 우승을 함께했다. {apps}경기 {stat_phrase}, 평점 {rating} — "
        "커리어에서 손에 꼽을 시즌이었고, 팀 전체가 정점에 오른 해였다.",

        "{year}년은 트로피와 함께 기억될 시즌이다. {team} 소속으로 {apps}경기에 나서 "
        "{stat_phrase}을 기록했고, 팀은 마침내 우승을 차지했다.",

        "우승. {year}년 {team}에서 이 한 단어로 시즌을 요약할 수 있다. {apps}경기 {stat_phrase}을 "
        "보태며 팀의 정상 등극에 힘을 보탰다.",

        "{team_eun} {year}년 정상에 섰다. {apps}경기 {stat_phrase}을 기록한 그에게도 "
        "커리어에서 가장 빛나는 시즌 중 하나로 남을 한 해였다.",
    ],

    CAT_NEW_CHALLENGE: [
        "{year}년, {team_ro} 이적하며 새로운 도전에 나섰다. 낯선 환경에 적응해야 하는 시즌이었지만 "
        "{apps}경기 {stat_phrase}을 기록하며 나쁘지 않은 출발을 알렸다. 팀 성적은 {rank_str}({wdl}).",

        "새 유니폼을 입은 {year}년. {team}에서의 첫 시즌은 적응이 관건이었다. "
        "{apps}경기 {stat_phrase}, 팀은 {rank_str}로 마쳤다 — 새로운 곳에서 자리를 잡아가는 과정이었다.",

        "{year}년 {team_ro} 자리를 옮겼다. 처음 몇 달은 적응기였지만 {apps}경기 {stat_phrase}을 "
        "남기며 새 소속팀에서의 첫 시즌을 마쳤다.",

        "이적 후 첫 시즌, {year}년 {team}에서 {apps}경기 {stat_phrase}을 기록했다. "
        "팀은 {rank_str}({wdl})로 시즌을 마쳤고, 새로운 환경에서의 적응은 나쁘지 않았다.",
    ],

    CAT_ABROAD: [
        "{year}년 해외 무대로 건너가며 커리어의 방향을 바꿨다. {team}에서 뛰며 낯선 리그, "
        "낯선 문화에 적응해야 했던 시즌 — {apps}경기 {stat_phrase}을 기록했고, 팀은 {rank_str}({wdl})였다.",

        "해외 도전이 시작된 {year}년. {team}에 합류해 완전히 다른 축구 환경 속에서 시즌을 치렀다. "
        "{apps}경기 {stat_phrase} — 결과보다 새로운 세계에 적응해 낸 과정 자체가 의미 있는 시즌이었다.",

        "{year}년, 국경을 넘어 {team_ro} 향했다. 언어도 문화도 낯선 곳에서 {apps}경기에 나서 "
        "{stat_phrase}을 기록하며 새로운 장을 열었다.",

        "익숙했던 환경을 떠나 {year}년 낯선 땅으로 향했다. {team} 소속으로 {apps}경기 {stat_phrase}을 "
        "남긴 이 시즌은, 커리어의 지도를 넓힌 한 해였다.",
    ],

    CAT_HOMECOMING: [
        "{year}년, {country_ro} 돌아왔다. 여러 해 타지를 떠돌던 끝에 다시 밟은 익숙한 땅이었다. "
        "{team}에서 {apps}경기 {stat_phrase}을 기록하며 새로운 챕터를 시작했다.",

        "긴 타향 생활을 뒤로하고 {year}년 {country_ro} 복귀했다. {team} 소속으로 {apps}경기에 나서 "
        "{stat_phrase}을 보탰다 — 낯선 곳에서의 경험을 안고 돌아온 익숙한 무대였다.",

        "{year}년, 다시 {country_ro} 돌아와 {team}에 자리를 잡았다. {apps}경기 {stat_phrase}을 "
        "기록하며, 그동안 해외에서 쌓은 경험을 고향 무대에 풀어놓기 시작했다.",
    ],

    CAT_LOAN: [
        # [2026-08 수정, 신민용 지적: "임대도 보통 팀이 얘를 임대보낸 것"]
        # "밀려난 임대라기보다, 새로운 경험을 쌓기 위한 시간이었다"는 선수
        # 쪽 의지를 단정하는 문장이라 삭제 — 어느 쪽이 주도했는지 데이터로
        # 알 수 없으니 사실(임대를 떠났다/그 기간 기록)만 남긴다.
        "{year}년 {team_ro} 임대를 떠났다. {apps}경기 {stat_phrase}을 기록하며 임대 기간을 보냈다.",

        "임대 신분으로 맞은 {year}년, {team}의 유니폼을 입었다. {apps}경기 {stat_phrase} — "
        "잠시 거쳐 가는 곳이었지만 그 안에서도 자신의 몫을 다했다.",

        "{year}년, {team_ro} 임대를 떠나 새로운 실전 감각을 쌓았다. {apps}경기 {stat_phrase}을 남기며, "
        "원소속팀에서 얻지 못한 출전 기회를 이곳에서 채워갔다.",
    ],

    CAT_LOAN_RETURN: [
        "임대를 마치고 {year}년 {team_ro} 복귀했다. {apps}경기 {stat_phrase}을 기록하며 "
        "원소속팀에서 다시 자리를 찾아가는 시즌이었다.",

        "{year}년, 임대 생활을 끝내고 {team_ro} 돌아왔다. 오랜만의 복귀였지만 {apps}경기에 나서 "
        "{stat_phrase}으로 존재감을 남겼다.",

        "임대를 통해 쌓은 경험을 안고 {year}년 {team_ro} 돌아왔다. {apps}경기 {stat_phrase}을 "
        "기록하며 원소속팀에서 새로운 시작을 알렸다.",
    ],

    CAT_VETERAN: [
        "베테랑이 된 {year}년에도 {team}에서 제 몫을 했다. {apps}경기 {stat_phrase}, 평점 {rating} — "
        "나이는 숫자일 뿐이라는 걸 경기력으로 보여준 시즌이었다.",

        "{year}년, 이제는 팀의 어른이 되어 {team}에서 시즌을 치렀다. {apps}경기 {stat_phrase}을 "
        "기록하며, 경험에서 나오는 안정감으로 팀에 기여했다.",

        "관록이 묻어난 {year}년이었다. {team}에서 {apps}경기 {stat_phrase}을 기록하며, "
        "젊은 선수들 사이에서 든든한 기둥 역할을 했다.",
    ],

    CAT_BENCH: [
        "{year}년은 {team}에서 출전 기회를 좀처럼 잡지 못한 시즌이었다. 경기장 밖에서 팀을 지켜봐야 하는 "
        "시간이 길었지만, 그 또한 커리어의 한 페이지였다.",

        "{year}년, {team}에서 좀처럼 기회를 얻지 못했다. 벤치를 지키는 날이 많았지만, "
        "다음 시즌을 기약하며 묵묵히 자리를 지켰다.",
    ],

    CAT_FINAL: [
        "그리고 {year}년, {team}에서의 시즌을 끝으로 유니폼을 벗기로 했다. {apps}경기 {stat_phrase}을 "
        "남긴 마지막 시즌 — 화려한 마무리는 아니었을지 몰라도, 자신의 이름으로 채운 커리어의 마침표였다.",

        "{year}년, {team} 소속으로 뛴 이 시즌을 마지막으로 은퇴를 선택했다. {apps}경기 {stat_phrase}. "
        "긴 여정의 끝에서, 그는 조용히 그러나 확실하게 자신의 시대를 마감했다.",

        "커리어의 마지막 장. {year}년 {team}에서 {apps}경기 {stat_phrase}을 기록한 뒤 은퇴를 결정했다. "
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

# ══════════════════════════════════════════════════════════════════
# 6.9. 정보 순서 다양화 — Lead 템플릿
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, v9 우선순위 1(최종), GPT 검토: "경기→순위→스탯→개인상→
# 국대→부상→마무리, 95%가 이 순서다 — 사람은 이렇게 안 쓴다. '국대
# 탈락 → 그래도 시즌 활약' 처럼 시작할 수도 있다"] 지금까지 모든
# 시즌이 "경기 기록 요약(base_text)이 먼저, 국대/부상 같은 부가정보는
# 나중"이라는 고정 순서였다. 이 Lead 템플릿은 그 반대 순서로 시작하는
# 대안 — 국대 미선발/부상으로 문을 열고, 그 안에 경기 기록을 자연스럽게
# 접어 넣는다. 매번 쓰면 그것도 새 패턴이 되므로, build_season_narrative
# 에서 확률적으로만(전체 시즌의 일부만) 선택한다. CAT_CHAMPION/DEBUT/
# FINAL은 그 자체로 이미 강한 정체성이 있는 카테고리라 대상에서 뺀다.
INTL_MISS_LEAD_TEMPLATES = [
    "{competition} 명단에서 또다시 이름이 빠졌다. 실망도 잠시, {team}에서는 {apps}경기 {stat_phrase}으로 제 몫을 다했다.",
    "{competition} 소집엔 이번에도 응답받지 못했다. 그래도 {team}에서의 {apps}경기, {stat_phrase}은 흔들림이 없었다.",
    "또 한 번 {competition} 명단은 그의 이름 없이 발표됐다. 하지만 {team}에서 {apps}경기 {stat_phrase}을 기록하며 시즌을 채웠다.",
]
INJURY_LEAD_TEMPLATES = [
    "부상이 시즌의 상당 부분을 앗아갔다. 그럼에도 {team}에서 {apps}경기 {stat_phrase}을 남겼다.",
    "몸 상태가 완전하지 않은 채로 시즌을 치러야 했다. 그런 와중에도 {team}에서 {apps}경기, {stat_phrase}을 기록했다.",
]

# [2026-07 신설, 신민용 리포트: "24위/22위처럼 진짜 바닥인 시즌도
# CAT_RELEGATION 안에서는 하위권/씨름 정도의 톤으로 뭉뚱그려진다"] 순위
# 비율이 특히 심각한(꼴찌권) 시즌은 별도 뱅크로 분리해 "강등을 피하지
# 못했다", "최하위로 추락했다"처럼 더 단정적이고 무거운 어투를 쓴다.
# build_season_narrative에서 rank/total 비율로 이 뱅크와 기존
# TEMPLATES[CAT_RELEGATION]을 갈라 쓴다.
SEVERE_RELEGATION_TEMPLATES = [
    "{year}년 {team_eun} 최하위권으로 추락하며 강등을 피하지 못했다({rank_str}, {wdl}). "
    "그 자신은 {apps}경기 {stat_phrase}을 기록했지만, 팀의 몰락을 막기에는 역부족이었다.",

    "붕괴에 가까운 시즌이었다. {team_eun} {year}년 {rank_str}({wdl})로 시즌을 마쳤다. "
    "{apps}경기 {stat_phrase}을 기록하며 버텼지만, 팀 전체가 무너지는 걸 혼자 막아낼 수는 없었다.",

    "{year}년, {team}에서 최악에 가까운 한 해를 보냈다. 팀은 {rank_str}({wdl})까지 추락했고, "
    "{apps}경기 {stat_phrase}이라는 개인 기록도 이 붕괴 앞에서는 위안이 되지 못했다.",

    "강등이 확정된 {year}년. {team_eun} 시즌 내내 순위표 맨 밑을 벗어나지 못했다({rank_str}, {wdl}). "
    "{apps}경기 {stat_phrase}을 남겼지만, 그 숫자로도 가릴 수 없는 실패의 시즌이었다.",
]

# [2026-07 신설, 신민용 리포트: "2017년 23위/24팀인데 '적응은 나쁘지
# 않았다'로 끝난다 — 성적이 나쁜데 좋게 포장된다"] CAT_NEW_CHALLENGE/
# CAT_ABROAD 템플릿의 마지막 문장이 팀 순위와 무관하게 항상 긍정으로
# 끝났다. 팀 순위 비율이 나쁜(0.75 이상) 시즌에는 이 전용 뱅크를 대신
# 쓴다 — "새로운 도전"이라는 사실 자체는 유지하되, 낙관적 마무리는 빼고
# 담백하게 끝맺는다.
NEW_CHALLENGE_STRUGGLE_TEMPLATES = [
    "{year}년, {team_ro} 이적하며 새로운 도전에 나섰다. {apps}경기 {stat_phrase}을 기록했지만, "
    "팀은 결국 {rank_str}({wdl})에 머물렀다 — 새로운 환경은 기대만큼 순탄하지 않았다.",

    "새 유니폼을 입은 {year}년. {team}에서 {apps}경기 {stat_phrase}을 남겼다. "
    "하지만 팀 성적은 {rank_str}({wdl})까지 처졌고, 이적은 새로운 출발이었을 뿐 결과로 이어지진 못했다.",

    "{year}년 {team_ro} 자리를 옮겼지만, 적응 이상의 시련이 기다리고 있었다. "
    "{apps}경기 {stat_phrase}을 기록하는 동안 팀은 {rank_str}({wdl})로 추락했다.",
]

ABROAD_STRUGGLE_TEMPLATES = [
    "{year}년 해외 무대로 건너가며 커리어의 방향을 바꿨다. {team}에서 {apps}경기 {stat_phrase}을 "
    "기록했지만, 팀은 {rank_str}({wdl})로 고전했다 — 낯선 땅에서의 시작은 쉽지 않았다.",

    "해외 도전이 시작된 {year}년. {team}에서 {apps}경기 {stat_phrase}을 남겼다. "
    "새로운 세계에 적응하는 것과 팀을 구하는 것은 별개의 문제였고, 팀은 {rank_str}({wdl})까지 밀렸다.",

    "{year}년, 국경을 넘어 {team_ro} 향했다. {apps}경기 {stat_phrase}을 기록했지만 "
    "팀 성적은 {rank_str}({wdl}) — 언어도 문화도, 결과도 모두 낯선 한 해였다.",
]

# [2026-07 신설] 심각한 강등 시즌에 개인 수상까지 겹치는 경우, 기존
# AWARD_SENTENCES(전부 긍정 일변도)를 그대로 붙이면 "팀은 꼴찌인데
# 베스트11 받아서 좋은 시즌"처럼 읽힌다. 팀의 실패와 개인의 활약을
# 대비시키는 전용 문장을 쓴다.
RELEGATION_AWARD_CONTRAST_SENTENCES = [
    "팀은 무너졌지만, 그 와중에도 개인은 {award_line_eul} 놓치지 않았다 — 씁쓸한 위안이었다.",
    "팀의 추락 속에서도 그의 활약만큼은 {award_line_ro} 이어질 정도였다. 다만 그 인정이 강등을 막아주지는 못했다.",
    "추락하는 팀 안에서 홀로 {award_line}(으)로 존재감을 지켰지만, 팀 성적표 앞에서는 크게 의미 없는 훈장이었다.",
]

# [2026-07 신설, 신민용 리포트: "베스트11 18번이 전부 같은 톤이다"]
# NATIONAL_TEAM_CHASE의 3단계 스테이징과 같은 원리 — 커리어 통산 수상
# 횟수(1부터 누적)에 따라 문장의 톤을 바꾼다. 처음 받을 땐 "인정받기
# 시작", 여러 번 받고 나면 "이제는 당연한 일", 노장이 되어서도 받으면
# "여전히"라는 무게가 실린다.
AWARD_SENTENCES_STAGED = {
    "STAGE1": [   # 통산 1~2번째 — 처음 인정받는 단계
        "이 시즌 {award_line}까지 차지하며 리그가 인정하는 이름이 됐다.",
        "그리고 시즌 종료 후 {award_line} — 그의 활약이 숫자를 넘어 공식적으로 인정받은 순간이었다.",
    ],
    "STAGE2": [   # 통산 3~5번째 — 반복되며 익숙해지는 단계
        "이 활약은 {award_line_ro} 이어졌다 — 어느새 익숙해진 결과였다.",
        "리그는 이 활약에 또 한 번 {award_line}(으)로 화답했다. 이제 그의 이름이 명단에 오르는 건 놀라운 일이 아니었다.",
    ],
    "STAGE3": [   # 통산 6번째 이상 — 당연시되는 단계
        "이제 {award_line_eun} 그에게 새삼스러운 일이 아니었다 — 그만큼 꾸준함이 쌓인 결과였다.",
        "{award_line}. 몇 번째인지 세는 것도 무의미해질 만큼, 그의 이름은 늘 그 자리에 있었다.",
    ],
}

# [2026-07 신설] 나이가 들어서도(베테랑 시기) 수상이 이어지면, 단순 반복이
# 아니라 "여전히"라는 감정이 실려야 한다 — STAGE와 별개로 veteran 여부로
# 한 번 더 갈린다.
AWARD_SENTENCES_VETERAN = [
    "노장이 된 뒤에도 그는 여전히 {award_line}(으)로 인정받는 선수였다.",
    "나이는 숫자일 뿐이었다 — {award_line_i} 그 사실을 다시 한번 증명했다.",
    "황혼기에도 {award_line_eul} 놓치지 않는 모습은, 그가 왜 그 자리에 오래 남을 수 있었는지를 보여줬다.",
]

# [2026-07 신설, 신민용 리포트: "베스트11 칭찬이 6~7번씩 반복된다 — 한
# 번만 나와도 된다"] STAGE1/STAGE2/STAGE3/VETERAN 각각 그 톤으로는
# 딱 한 번만(그 단계에 처음 진입했을 때) 위 뱅크의 '칭찬형' 문장을 쓰고,
# 같은 단계에서 또 수상하면 그냥 사실만 짧게 언급하는 이 중립 뱅크로
# 넘어간다.
AWARD_NEUTRAL_MENTION = [
    "이 시즌에도 {award_line}에 이름을 올렸다.",
    "{award_line}도 함께였다.",
    "이 시즌 수상 명단에도 이름을 올렸다.",
    "{award_line}까지 더했다.",
]

# 이전 버전과의 호환을 위해 남겨둔다 (더 이상 build_season_narrative에서
# 직접 쓰이진 않지만, 다른 곳에서 참조할 수 있어 유지).
# [2026-07 수정, 신민용 리포트: "경기장을 떠나지 못했다/라커룸이 침묵
# 했다는 데이터에 없는 구체적 장면이다 — 데이터 기반 생성기라는 원칙에
# 안 맞는다"] 실제로 확인할 수 없는 미시적 장면(누가 어디서 뭘 했다)은
# 빼고, "강등/우승이라는 결과 자체가 선수에게 어떤 의미였는가"라는
# 결과 해석 수준으로만 감정을 표현한다.
RELEGATION_EMOTION_SENTENCES = [
    "강등이라는 결과는 그가 받아들이기 쉽지 않은 현실이었다.",
    "강등은 선수 개인에게도 뼈아픈 결과였다.",
    "숫자로는 다 담기지 않는 실망이 그 시즌에 남았다.",
]
CHAMPION_EMOTION_SENTENCES = [
    "우승이라는 결과는 그동안의 모든 과정에 의미를 더해주었다.",
    "트로피는 그 시즌 그가 쌓아온 모든 것에 대한 보상이었다.",
    "오랜 기다림 끝의 우승이었던 만큼, 그 의미는 더 각별했다.",
]

AWARD_SENTENCES = [
    "이 시즌 {award_line}까지 차지하며 리그가 인정하는 이름이 됐다.",
    "그리고 시즌 종료 후 {award_line} — 그의 활약이 숫자를 넘어 공식적으로 인정받은 순간이었다.",
    "이 활약은 {award_line_ro} 이어졌다.",
    "리그는 이 활약에 {award_line}(으)로 화답했다.",
]

CUP_SENTENCES = [
    "이 시즌 컵대회에서도 {competition} {cup_result}까지 오르며 두 마리 토끼를 쫓았다.",
    "동시에 {competition}에서도 {cup_result_ira} 성과를 남겼다.",
    "컵대회({competition}) 성적도 나쁘지 않았다 — {cup_result}까지 진출했다.",
]

CL_SENTENCES = [
    "여기에 {competition} 무대에서도 {cup_result}까지 오르며 유럽/대륙 대항전에서도 존재감을 남겼다.",
    "{competition}에서도 {cup_result_ira} 성과를 거두며 클럽 대항전 경험을 쌓았다.",
]

INTL_CALLED_SENTENCES = [
    "그 해 국가대표팀에도 이름을 올려 {competition} 무대를 밟았다.",
    "동시에 국가대표팀 소집에 응해 {competition}에 참가했다.",
    "클럽 시즌과 별개로, {competition} 국가대표팀 명단에도 포함됐다.",
]

# [2026-07 신설, 신민용 리포트: "챔스 등 국제대회 경험이 있으면 그것도
# 스토리 설계에 반영돼야 한다"] 컵대회(CUP_SENTENCES)·챔스(CL_SENTENCES)는
# _DEEP_CUP_RESULTS(우승/준우승/4강/8강)에 걸리면 결과를 구체적으로
# 언급하는데, 국가대표(월드컵/대륙컵)는 지금까지 "소집됐다/못 됐다"만
# 구분하고 실제로 몇 강까지 갔는지는 전혀 반영하지 않았다 — 월드컵
# 결승에 오른 시즌도 그냥 "무대를 밟았다"로 뭉뚱그려졌다. 컵/챔스와
# 같은 원리로 딥런 결과 전용 뱅크를 추가한다.
_INTL_DEEP_RESULTS = ("우승", "준우승", "4강", "8강", "16강")

INTL_DEEP_SENTENCES = [
    "국가대표팀 소집에 응해 {competition}에서 {result}까지 오르는 성과를 냈다.",
    "그 해 {competition} 무대에서 {result}이라는 값진 결과를 만들어냈다.",
    "동시에 {competition}에서도 {result}까지 진출하며 대표팀에서도 존재감을 남겼다.",
]

INTL_MISSED_SENTENCES = [
    "다만 그 해 열린 {competition}에서는 국가대표팀의 부름을 받지 못했다.",
    "그러나 그 해 열린 {competition}에서도 대표팀 명단에는 들지 못했다.",
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


def _team_intro(s):
    """[2026-07 신설, 신민용 리포트: "팀명을 말하기 전에 그 리그가 그
    당시 어느 나라 몇부였는지 설명하는 것도 필요할 거 같다"] 새 팀에
    합류하는 시즌(데뷔/새 도전/해외진출/귀국/임대)에서, 팀 이름만
    던지지 않고 "어느 나라 몇 부 리그 소속인지"까지 먼저 소개한다.
    career_entries에 이미 tier(부수)/country(국가) 컬럼이 있으므로
    새로 지어내는 정보는 없다 — 그냥 조합만 다르게 한다. 데이터가
    없으면(구버전 세이브 등) 팀 이름만 반환해 문장이 안 깨지게 한다."""
    team = s.get("team_name", "")
    country = s.get("country", "")
    league = s.get("league_name", "")
    tier = s.get("tier", 0)
    if country and tier and league:
        return f"{country} {tier}부 리그({league})에 있는 {team}"
    if country and league:
        return f"{country} {league} 소속 {team}"
    return team


def _stat_phrase(s):
    """[2026-07 신설, v8 설계 우선순위 1: GK/포지션별 템플릿 분리] 지금까지
    기본 카테고리 문장 46개 전부가 '{goals}골 {assists}도움'을 하드코딩
    하고 있었다 — GK는 항상 0/0이라 14시즌 내내 "0골 0도움"이 반복되는
    문제가 있었다(신민용 리포트). 정규식 치환은 문법이 깨질 위험이 있다는
    지적(GPT 리뷰)을 받아, 대신 각 템플릿의 그 자리를 {stat_phrase}
    플레이스홀더로 통일하고 여기서 포지션 그룹별로 다른 명사구를 채운다.

    문법 주의: 템플릿들이 뒤에 "을"/"으로" 같은 받침 있는 조사를 하드코딩
    하고 있으므로(원래 "도움" 자체가 받침 ㅁ으로 끝남), 대체 명사구도
    "받침 있는 글자로 끝나야" 조사가 안 깨진다. [주의: "N회 선방"이 아니라
    "선방 N회" 순서로 쓰면 마지막 글자가 "회"(받침 없음, 모음 ㅚ)가 되어
    "회를"이 맞는데 하드코딩된 "을"이 그대로 붙어 "회을"이라는 오문이
    난다 — 그래서 반드시 "N회 선방"/"N회 무실점" 순서로, 숫자+회를 앞에
    두고 받침 있는 명사(방/점)로 끝맺는다.]"""
    grp = POSITION_GROUP.get(s.get("position", ""), "MF")
    if grp == "GK":
        saves = s.get("saves", 0) or 0
        cs = s.get("clean_sheets", 0) or 0
        if saves > 0:
            return f"{saves}회 선방"
        if cs > 0:
            return f"{cs}회 무실점"
        return "안정적인 수비 뒷받침"
    return f"{s.get('goals', 0)}골 {s.get('assists', 0)}도움"


def _fill(tmpl, s, extra=None):
    team = s.get("team_name", "")
    league = s.get("league_name", "")
    country = s.get("country") or (league.split()[0] if league else "")
    d = {
        "team": team,
        "team_intro": _team_intro(s),
        "team_intro_ro": with_josa(_team_intro(s), "으로/로"),
        "team_ro": with_josa(team, "으로/로"),
        "team_eun": with_josa(team, "은/는"),
        "team_ga": with_josa(team, "이/가"),
        "league": league,
        "country": country,
        "country_ro": with_josa(country, "으로/로"),
        "apps": s.get("matches", 0),
        "goals": s.get("goals", 0),
        "assists": s.get("assists", 0),
        "stat_phrase": _stat_phrase(s),
        "rating": round(s.get("avg_rating", 0) or 0, 1),
        "rating_ro": with_josa(str(round(s.get("avg_rating", 0) or 0, 1)), "으로/로"),
        "rating_reul": with_josa(str(round(s.get("avg_rating", 0) or 0, 1)), "을/를"),
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


# ══════════════════════════════════════════════════════════════════
# 6. 시즌 확장 레이어 — Probe(SeasonFacts) → Profile → 레이어별 문장
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, 신민용 설계 + 여러 차례 검토 v1~v4] 목표: "이벤트가 많을수록
# 자연스럽게 분량이 늘어난다"는 원칙으로 시즌 서술을 확장한다. 절대 원칙은
# 그대로다 — 숫자는 항상 원본 그대로, 문장의 틀만 골라서 조합한다.
#
# 파이프라인: Probe(재료 유무만 싸게 확인 → SeasonFacts) → Profile 결정
# (BASIC/EVENT/REFLECTION/LEGEND) → 그 프로필에 맞는 레이어만 실제 문장으로
# 생성. 분량 하한선은 두지 않는다 — 짧은 커리어는 짧게 끝나는 게 맞다.

POSITION_GROUP = {
    "GK": "GK",
    "CB": "DF", "LB": "DF", "RB": "DF",
    "CDM": "MF", "CM": "MF", "CAM": "MF",
    "LW": "FW", "RW": "FW", "CF": "FW", "ST": "FW",
}

# 포지션 그룹별 디테일 문장. (조건함수, 클러스터ID, 문장 템플릿) — 조건함수가
# False면 그 시즌에는 애초에 후보에서 빠진다(예: 슈팅이 0인데 "유효슈팅 비율"
# 문장이 나오는 어색함 방지). 클러스터ID는 반복 방지기(RepetitionGuard)가
# "최근에 이 뜻 묶음을 썼는지"를 판단하는 단위다.
# [2026-07 확장, 신민용 리포트: "패스 성공률 XX%/차단 XX회/공중볼 경합
# 문장이 거의 매 시즌 반복된다 — 이게 제일 AI 티가 난다"] 클러스터별로
# 템플릿을 하나가 아니라 여러 개(변형) 두고, 같은 클러스터가 다시
# 뽑히더라도 문장 표현 자체는 달라지게 한다. 또한 출전이 적은(부상 등)
# 시즌 전용 클러스터를 추가해 "적은 출전에도 꾸준했다"는 어색한 단정을
# 피하고 "경험으로 버텼다"는 결이 다른 표현을 쓸 수 있게 한다.
DETAIL_CANDIDATES = {
    "GK": [
        (lambda s: (s.get("saves", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "save", [
            "선방 {saves}회를 기록하며 골문을 든든히 지켰다.",
            "위기의 순간마다 선방 {saves}회로 팀을 구해냈다.",
            "골문 앞에서의 집중력이 돋보였다 — 선방 {saves}회.",
        ]),
        (lambda s: (s.get("clean_sheets", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "clean_sheet", [
            "무실점 {clean_sheets}회로 팀의 최후방을 책임졌다.",
            "무실점 {clean_sheets}회, 뒷문을 굳게 걸어 잠근 한 해였다.",
        ]),
        (lambda s: (s.get("matches", 0) or 0) >= 15, "steady_gk", [
            "안정적인 골킥과 빌드업으로 수비 조직의 시작점 역할을 했다.",
            "위기 상황에서도 침착한 판단으로 실점을 최소화했다.",
            "포지셔닝과 커맨드로 뒷문을 조율했다.",
        ]),
        (lambda s: (s.get("matches", 0) or 0) < 15, "limited_gk", [
            "출전 기회는 많지 않았지만, 나설 때마다 침착함을 잃지 않았다.",
            "많은 경기를 뛰지는 못했어도, 백업으로서 제 역할은 분명히 했다.",
        ]),
    ],
    "DF": [
        (lambda s: (s.get("blocks", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "block", [
            "차단 {blocks}회를 기록하며 상대 공격을 여러 차례 끊어냈다.",
            "몸을 던지는 차단이 {blocks}회 — 수비 라인을 지키는 데 진심이었다.",
            "위험 지역에서의 차단 {blocks}회가 그의 존재감을 말해줬다.",
        ]),
        (lambda s: (s.get("pass_acc", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "pass_df", [
            "패스 성공률 {pass_acc_pct}%로 후방 빌드업의 핵심 역할을 했다.",
            "안정된 빌드업으로 후방을 조율했다 — 패스 성공률 {pass_acc_pct}%.",
            "공을 다루는 침착함이 눈에 띄었다. 패스 성공률 {pass_acc_pct}%.",
        ]),
        (lambda s: (s.get("clean_sheets", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "clean_sheet", [
            "무실점 {clean_sheets}회로 수비 조직의 중심을 잡았다.",
            "무실점 {clean_sheets}회, 뒷문을 든든히 지킨 한 해였다.",
        ]),
        (lambda s: (s.get("matches", 0) or 0) >= 15, "aerial", [
            "공중볼 경합과 대인 방어에서 안정적인 모습을 보였다.",
            "몸싸움과 위치선정으로 상대 공격수를 자주 봉쇄했다.",
            "라인 조율과 커뮤니케이션으로 수비진을 이끌었다.",
            "묵묵히 수비진의 중심을 지켰다.",
        ]),
        (lambda s: 0 < (s.get("matches", 0) or 0) < 15, "limited_df", [
            "출전은 많지 않았지만, 나설 때마다 경험으로 수비진을 지탱했다.",
            "많이 뛰지는 못했어도, 그라운드에 설 때만큼은 노련함을 보여줬다.",
        ]),
    ],
    "MF": [
        (lambda s: (s.get("key_passes", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "key_pass", [
            "기회창출 {key_passes}회로 공격 전개의 중심에 섰다.",
            "날카로운 스루패스로 기회창출 {key_passes}회를 기록했다.",
        ]),
        (lambda s: (s.get("pass_acc", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "pass_mf", [
            "패스 성공률 {pass_acc_pct}%를 유지하며 경기를 조율했다.",
            "경기의 리듬을 쥔 건 그였다 — 패스 성공률 {pass_acc_pct}%.",
        ]),
        (lambda s: (s.get("dribbles", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "dribble_mf", [
            "드리블 {dribbles}회로 전진 패스 이상의 역할을 해냈다.",
            "볼을 소유한 채 전진하는 능력이 돋보였다 — 드리블 {dribbles}회.",
        ]),
        (lambda s: (s.get("matches", 0) or 0) >= 15, "workrate", [
            "폭넓은 활동량으로 공수 양면에서 존재감을 드러냈다.",
            "경기장 구석구석을 뛰어다니며 팀의 균형을 잡았다.",
        ]),
        (lambda s: 0 < (s.get("matches", 0) or 0) < 15, "limited_mf", [
            "출전 시간은 줄었지만, 주어진 기회마다 제 몫을 하려 애썼다.",
            "많은 경기를 소화하지는 못했지만, 팀에 필요한 순간엔 늘 그 자리에 있었다.",
        ]),
    ],
    "FW": [
        (lambda s: (s.get("shots_on", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "finishing", [
            "슈팅 {shots}회 중 {shots_on}회를 유효슈팅으로 연결하며 결정력을 보여줬다.",
            "결정적인 순간의 마무리가 좋았다 — 유효슈팅 {shots_on}회.",
        ]),
        (lambda s: (s.get("dribbles", 0) or 0) > 0 and (s.get("matches", 0) or 0) >= 15, "dribble_fw", [
            "드리블 {dribbles}회로 상대 수비를 끊임없이 흔들었다.",
            "치고 나가는 돌파로 상대 수비를 자주 무너뜨렸다 — 드리블 {dribbles}회.",
        ]),
        (lambda s: (s.get("matches", 0) or 0) >= 15, "clinical", [
            "골 앞에서의 침착함으로 득점 기회를 놓치지 않았다.",
            "공격 포인트뿐 아니라 전방 압박에서도 제 몫을 했다.",
            "위치 선정과 마무리로 꾸준히 팀에 기여했다.",
        ]),
        (lambda s: 0 < (s.get("matches", 0) or 0) < 15, "limited_fw", [
            "출전 기회는 많지 않았지만, 교체로 들어가서도 존재감을 남기려 했다.",
            "많이 뛰진 못했어도, 주어진 시간 안에서는 위협적이었다.",
        ]),
    ],
}



def _pick_clustered(rng, candidates, tracker, key, s, window=4):
    """반복 방지기(Repetition Guard). candidates는 (조건함수, 클러스터ID,
    [템플릿 변형...]) 튜플 목록 — 조건을 만족하는 것만 후보로 남기고,
    그중에서도 '최근 window개 시즌 안에 이미 쓴 클러스터'는 가능하면
    피한다. 클러스터가 정해진 뒤에는 그 안의 여러 변형 문장 중 하나를
    _pick()으로 골라, 같은 클러스터가 다시 뽑히더라도 문장 표현 자체는
    달라지게 한다."""
    usable = [(cid, tmpls) for cond, cid, tmpls in candidates if cond(s)]
    if not usable:
        return None
    recent = tracker.setdefault(f"_recent_{key}", [])
    fresh = [(cid, tmpls) for cid, tmpls in usable if cid not in recent]
    pool = fresh or usable
    cid, tmpls = rng.choice(pool)
    recent.append(cid)
    if len(recent) > window:
        recent.pop(0)
    if isinstance(tmpls, str):
        return tmpls
    return _pick(rng, tmpls, tracker, f"{key}:{cid}")


def build_detail_sentence(rng, tracker, s):
    """포지션 그룹에 맞는 세부 지표 문장 하나. 데이터가 아예 없으면 None."""
    grp = POSITION_GROUP.get(s.get("position", ""), "MF")
    tmpl = _pick_clustered(rng, DETAIL_CANDIDATES[grp], tracker, f"detail:{grp}", s)
    if not tmpl:
        return None
    pass_acc_pct = round((s.get("pass_acc") or 0) * 100)
    extra = {"pass_acc_pct": pass_acc_pct,
             "blocks": s.get("blocks", 0), "saves": s.get("saves", 0),
             "clean_sheets": s.get("clean_sheets", 0), "key_passes": s.get("key_passes", 0),
             "dribbles": s.get("dribbles", 0), "shots": s.get("shots", 0),
             "shots_on": s.get("shots_on", 0)}
    return _fill(tmpl, s, extra=extra)


# ── 경기 하이라이트(명장면) — Career Memory의 match_events를 처음으로 렌더링 ──
_MATCH_EVENT_PRIORITY = {"hattrick": 3, "great_match": 2, "multi_assist": 1}

HIGHLIGHT_SENTENCES = [
    "그 시즌 최고의 순간은 {opp_ro} 경기였다.",
    "{opp_ro} 경기는 이 시즌을 대표하는 장면으로 남았다.",
    "특히 {opp_ro} 경기에서의 활약은 시즌 최고의 퍼포먼스로 평가받았다.",
]
HIGHLIGHT_HATTRICK_DETAIL = "이 경기에서만 {goals}골을 몰아넣었다."
HIGHLIGHT_ASSIST_DETAIL = "이 경기에서 {assists}개의 도움을 기록했다."
HIGHLIGHT_RATING_DETAIL = "평점 {rating_reul} 받으며 그 시즌 개인 최고 활약을 펼쳤다."


def _best_match_event(memory, year):
    if not memory:
        return None
    cands = [e for e in memory.get("timeline", [])
             if e.get("year") == year and e.get("type") in _MATCH_EVENT_PRIORITY]
    if not cands:
        return None
    return max(cands, key=lambda e: _MATCH_EVENT_PRIORITY[e["type"]])


def build_highlight_sentence(rng, tracker, memory, year):
    ev = _best_match_event(memory, year)
    if not ev:
        return None
    opp = ev.get("opponent") or "상대팀"
    tmpl = _pick(rng, HIGHLIGHT_SENTENCES, tracker, "highlight")
    text = tmpl.format(opp_ro=with_josa(opp, "과의/와의"))
    if ev["type"] == "hattrick":
        text += " " + HIGHLIGHT_HATTRICK_DETAIL.format(goals=ev.get("goals", 3))
    elif ev["type"] == "multi_assist":
        text += " " + HIGHLIGHT_ASSIST_DETAIL.format(assists=ev.get("assists", 2))
    elif ev["type"] == "great_match":
        rating_str = str(round(ev.get("rating", 9.0), 1))
        text += " " + HIGHLIGHT_RATING_DETAIL.format(rating_reul=with_josa(rating_str, "을/를"))
    return text


# ── 새 팀 합류/이탈 — 감독·구단 정보를 별도 문단이 아니라 짧은 수식어로만 ──
# [2026-07 버그수정, 신민용 리포트: "팀 이야기가 거의 없다"] 기존 매핑
# 키(성과주의/육성형/전술가/관계중시)가 실제 game_engine.MANAGER_TYPES의
# 값(뚝심형/성과주의/유스 중시/베테랑 신뢰/엄격함/온화함)과 대부분
# 일치하지 않아서, 이 절이 사실상 거의 항상 빈 문자열만 반환하고
# 있었다 — manager_clause 자체가 죽어있던 셈이다. 실제 게임 데이터의
# 값으로 다시 맞춘다.
# [2026-07 수정, 신민용 리포트: "'선수단 분위기를 다독이는 감독 아래
# 새 시즌을 시작했다'는 소설투다 — '감독은 꾸준히 그를 중용했다'처럼
# 기사체로"] 수식어+종속절 형태 대신, 그 자체로 완결된 짧은 문장으로
# 바꿔 기사 문체에 가깝게 만든다.
MANAGER_TYPE_CLAUSE = {
    "성과주의": "감독은 성과를 최우선으로 요구했다.",
    "뚝심형": "감독은 꾸준히 그를 중용했다.",
    "유스 중시": "감독은 어린 선수 육성에 무게를 뒀다.",
    "베테랑 신뢰": "감독은 경험 많은 선수를 신뢰했다.",
    "엄격함": "감독은 훈련과 규율에 엄격했다.",
    "온화함": "감독은 선수단을 다독이는 편이었다.",
}
# [2026-07 버그수정] exit_type 실제 값(이적/계약만료/방출/임대/임대
# 종료/팔림)에 맞춰 재정리. "팔림"은 이미 EXIT_TYPE_TONE에 있었지만
# "이적"이 빠져 있어서 시즌 중 이적한 케이스에서 절이 비어 있었다.
EXIT_TYPE_TONE = {
    "방출": "아쉬움을 남긴 채",
    # [2026-08 수정, 신민용 지적: "팔리는 건 대부분 구단이 팔고 싶어서
    # 판 것"] "새로운 기회를 찾아"는 선수가 능동적으로 원해서 떠난 것처럼
    # 읽혀 데이터가 뒷받침 못 하는 동기를 서술하게 된다 — 실제로는 구단의
    # 결정(이적료를 받고 내보냄)인 경우가 대부분이므로, 어느 쪽이 원했는지
    # 단정하지 않는 중립적 문구로 바꾼다.
    "팔림": "구단의 결정으로",
    "계약만료": "계약이 끝나며",
    "임대": "임대 신분으로",
    "임대 종료": "임대를 마치고",
    "이적": "새로운 도전을 찾아",
}

# [2026-07 신설, 신민용 리포트: "팀 이야기가 거의 없다 — 브래드포드는
# 기억 안 난다. '브래드포드는 당시 승격을 노리던 팀이었다' 같은 문장이
# 있으면 좋겠다"] career_entries에 이미 있는 club_ambition 필드(우승
# 도전/상위권 도전/중위권 안정/강등 회피)로 그 시즌의 "팀이 어떤 팀
# 이었는지"를 짧게 짚어준다. 새로 팀에 합류한 시즌에만 붙여서, 매
# 시즌 반복되지 않게 한다.
CLUB_AMBITION_CLAUSE = {
    "우승 도전": "리그 우승을 노리던 팀이었다.",
    "상위권 도전": "유럽대회 진출권을 놓고 다투던 팀이었다.",
    "중위권 안정": "안정적인 중위권을 목표로 하던 팀이었다.",
    "강등 회피": "잔류가 급선무였던 팀이었다.",
}


def team_ambition_clause(team_row) -> str:
    """새 팀 합류 시즌에 짧게 그 팀의 목표를 짚어주는 문장. 데이터
    없으면 빈 문자열."""
    amb = (team_row or {}).get("club_ambition", "")
    frame = CLUB_AMBITION_CLAUSE.get(amb, "")
    if not frame:
        return ""
    team = with_josa(team_row.get("team_name", "이 팀"), "은/는")
    return f"{team} 당시 {frame}"


def manager_clause(team_row) -> str:
    """새 팀 합류 문장에 끼워 넣을 짧은 수식어. 데이터 없으면 빈 문자열."""
    mt = (team_row or {}).get("manager_type", "")
    return MANAGER_TYPE_CLAUSE.get(mt, "")


def exit_tone_clause(team_row) -> str:
    et = (team_row or {}).get("exit_type", "")
    return EXIT_TYPE_TONE.get(et, "")


# ── SeasonFacts(Probe) + Profile 결정 ──────────────────────────────
PROFILE_BASIC = "BASIC"
PROFILE_EVENT = "EVENT"
PROFILE_REFLECTION = "REFLECTION"
PROFILE_LEGEND = "LEGEND"

_CATEGORY_WEIGHT = {
    CAT_DEBUT: 2, CAT_CHAMPION: 3, CAT_ABROAD: 2, CAT_HOMECOMING: 2,
    CAT_FINAL: 2, CAT_RELEGATION: 1, CAT_NEW_CHALLENGE: 1, CAT_VETERAN: 1,
}


def probe_season_facts(seasons, idx, categories, memory, awards_by_year, trophy_years,
                        cup_by_year, cl_by_year, intl_by_year, turning_indices):
    """Probe 단계 — 문장을 만들지 않고 '재료가 있는지'만 싸게 확인해서
    SeasonFacts(딕셔너리)로 묶어둔다. 이후 단계는 이 재료를 다시 뒤지지
    않고 SeasonFacts만 보고 판단한다."""
    s = seasons[idx]
    year = s.get("start_year", 0)
    cat = categories[idx]
    cup_by_year = cup_by_year or {}
    cl_by_year = cl_by_year or {}
    intl_by_year = intl_by_year or {}

    has_cup = year in cup_by_year and cup_by_year[year][0] in _DEEP_CUP_RESULTS
    has_cl = year in cl_by_year and cl_by_year[year][0] in _DEEP_CUP_RESULTS
    has_award = year in awards_by_year
    has_intl = year in intl_by_year
    is_turning = idx in turning_indices
    match_ev = _best_match_event(memory, year)
    injury_conf = (memory or {}).get("injury_seasons", {}).get(id(s))

    density = sum([has_cup, has_cl, has_award, has_intl, is_turning,
                   match_ev is not None, injury_conf is not None])
    weight = _CATEGORY_WEIGHT.get(cat, 0) + (2 if is_turning else 0) + min(density, 3)

    return {
        "season": s, "category": cat, "year": year,
        "has_cup": has_cup, "has_cl": has_cl, "has_award": has_award, "has_intl": has_intl,
        "is_turning_point": is_turning, "match_event": match_ev, "injury_confidence": injury_conf,
        "density": density, "weight": weight,
    }


def determine_profile(facts) -> str:
    """SeasonFacts → 프로필. 분량/문체를 여기서 한 번에 결정한다."""
    high_weight = facts["weight"] >= 3
    high_density = facts["density"] >= 2
    if high_weight and high_density:
        return PROFILE_LEGEND
    if high_weight and not high_density:
        return PROFILE_REFLECTION
    if not high_weight and high_density:
        return PROFILE_EVENT
    return PROFILE_BASIC


# ══════════════════════════════════════════════════════════════════
# 4.5. Conflict → Theme 매핑
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, v8 설계 우선순위 4, GPT 검토: "Theme보다 중요한 건
# Conflict다 — Theme는 Conflict의 결과다"] 예전 설계(v5~v7)는 Theme
# (main/tone/beat)를 시즌 데이터에서 직접 계산하려 했는데, 그러면
# 판정 로직이 계속 늘어난다. 대신 이미 있는 재료(카테고리, Narrative
# Question 상태)로 "이번 시즌 갈등이 뭐였고 어떻게 됐는지"만 보고,
# Theme는 계산이 아니라 매핑표 조회로 끝낸다 — 코드가 오히려 준다.
#
# 지금은 구조체(SeasonNarrative)에 값만 붙여두는 단계다 — 실제로 이
# 값을 문장 톤 선택에 쓰는 건 다음 단계(챕터/총평 집계, Voice)에서.
THEME_MAIN = ["BREAKTHROUGH", "STABLE", "STRUGGLE", "REBUILD",
              "REDEMPTION", "PEAK", "DECLINE", "LAST_DANCE"]

# 어떤 유형의 NarrativeQuestion이 해소됐느냐로 "긍정적 반전"의 성격이
# 갈린다 — 힘든 시간을 딛고 일어선 거면 REDEMPTION, 새 환경 적응처럼
# 순조로운 전개면 BREAKTHROUGH.
_REDEMPTION_QUESTION_TYPES = {"STARTER_BATTLE", "INJURY_COMEBACK"}


def determine_theme(cat, question_events):
    """Conflict(카테고리 + NarrativeQuestion 상태)를 보고 Theme를
    매핑표에서 조회한다 — 새로 판단하지 않는다."""
    events = question_events or []
    resolved_types = [t for s, t in events if s == "RESOLVED"]
    ongoing = any(s in ("OPEN", "PROGRESS") for s, t in events)

    if resolved_types:
        if any(t in _REDEMPTION_QUESTION_TYPES for t in resolved_types):
            return "REDEMPTION"
        if "TITLE_CHASE" in resolved_types or "TITLE_CHASE_AGAIN" in resolved_types:
            return "PEAK"
        return "BREAKTHROUGH"   # ADAPTATION 등 순조로운 해소
    if ongoing:
        return "STRUGGLE"
    if cat == CAT_CHAMPION:
        return "PEAK"
    if cat == CAT_DECLINING:
        return "DECLINE"
    if cat == CAT_FINAL:
        return "LAST_DANCE"
    if cat in (CAT_NEW_CHALLENGE, CAT_ABROAD, CAT_HOMECOMING):
        return "REBUILD"
    if cat == CAT_RISING:
        return "BREAKTHROUGH"
    return "STABLE"


# ── 브리지(시즌 연결) 문장 ──────────────────────────────────────────
_POSITIVE_CATS = {CAT_RISING, CAT_STEADY, CAT_CHAMPION, CAT_VETERAN}
_NEGATIVE_CATS = {CAT_DECLINING, CAT_RELEGATION, CAT_BENCH}

BRIDGE_SENTENCES = {
    "continue": [
        "이 활약으로 다음 시즌에도 신뢰를 유지했다.",
        "이런 꾸준함은 다음 시즌까지 자연스럽게 이어졌다.",
        "상승세는 거기서 멈추지 않았다.",
        "그 기세를 다음 시즌까지 그대로 이어갔다.",
        "이 흐름은 한동안 계속됐다.",
        "다음 시즌에도 크게 흔들리지 않았다.",
    ],
    "reverse": [
        "하지만 좋은 흐름은 오래가지 못했다.",
        "그러나 다음 시즌의 상황은 달라져 있었다.",
        "이 기세가 계속되지는 않았다.",
        "그러나 그 흐름은 거기서 끊겼다.",
        "다음 시즌은 전혀 다른 얼굴로 다가왔다.",
    ],
    "turning": [
        "이 시즌은 이후 커리어의 전환점이 되었다.",
        "돌이켜보면 이 시기가 변화의 시작이었다.",
        "그리고 모든 것이 달라지기 시작했다.",
        "이때를 기점으로 흐름이 바뀌었다.",
        "여기서부터는 이전과 다른 이야기였다.",
        "다음 시즌부터는 상황이 눈에 띄게 달라져 있었다.",
    ],
}


def build_bridge_sentence(rng, tracker, categories, idx, turning_indices):
    """다음 시즌으로 넘어가는 연결 문장. 조건이 안 맞으면 None(매 시즌마다
    억지로 넣지 않는다).
    [2026-07 수정, 신민용 리포트: "돌이켜보면/이때를 기점으로/변화의
    시작 같은 문장이 거의 매번 나온다"] 전환점(turning)마다 무조건
    코멘트를 붙이지 않고, 일정 확률로는 그냥 아무 언급 없이 다음
    시즌으로 넘어가게 한다 — 모든 시즌이 "의미 부여"를 받을 필요는
    없다."""
    if idx + 1 >= len(categories):
        return None
    if (idx + 1) in turning_indices:
        # [2026-07 수정] "이때를 기점으로/변화의 시작" 류도 회고 톤 공유
        # 예산(_global_retro_used)을 함께 쓴다 — 확률 게이트만으로는
        # 다른 메커니즘들과 합쳐졌을 때 전체 빈도를 못 잡았다.
        if rng.random() < 0.35 or tracker.get("_global_retro_used", 0) >= 3:
            return None
        tracker["_global_retro_used"] = tracker.get("_global_retro_used", 0) + 1
        bank = BRIDGE_SENTENCES["turning"]
        key = "bridge_turning"
    elif categories[idx] in _POSITIVE_CATS and categories[idx + 1] in _POSITIVE_CATS:
        bank = BRIDGE_SENTENCES["continue"]
        key = "bridge_continue"
    elif categories[idx] in _POSITIVE_CATS and categories[idx + 1] in _NEGATIVE_CATS:
        bank = BRIDGE_SENTENCES["reverse"]
        key = "bridge_reverse"
    else:
        return None
    return _pick(rng, bank, tracker, key)


# ── 챕터 회고 — 챕터 안 시즌들의 요약이 아니라 '그 챕터의 의미' ──────────
CHAPTER_RETRO = {
    "debut": ["이 시기는 그의 축구 인생이 본격적으로 시작된 출발점이었다.",
              "돌이켜보면 이 시기는 모든 것이 시작된 지점이었다."],
    "rising": ["이 무렵 그는 한 걸음씩 자신의 자리를 넓혀갔다.",
               "이 시기는 가능성이 현실이 되어가던 과정이었다."],
    "trophy": ["이 시기는 커리어의 정점이자, 가장 빛나던 순간들로 채워졌다.",
               "돌이켜보면 이 시기가 그의 커리어를 가장 잘 설명해주는 구간이었다."],
    "abroad": ["이 시기 그는 낯선 땅에서 새로운 도전과 마주해야 했다.",
               "익숙한 무대를 떠나 처음부터 다시 증명해야 했던 시기였다."],
    "struggle": ["이 시기는 쉽지 않은 시간의 연속이었지만, 그는 끝내 자리를 지켜냈다.",
                 "어려움 속에서도 그는 무너지지 않았다."],
    "wander": ["이 시기 그는 여러 무대를 오가며 자신의 축구를 찾아갔다.",
               "정착보다는 도전을 택해야 했던 시기였다."],
    "veteran": ["이 시기 그는 경험을 앞세워 팀에 깊이를 더했다.",
                "더 이상 화려하진 않았지만, 그의 존재감은 여전했다."],
    "steady": ["이 시기 그는 특별한 굴곡 없이 자신의 몫을 꾸준히 해냈다.",
               "화려하진 않았지만, 담담하게 제 역할을 이어간 시기였다."],
    "final": ["그리고 이 시기를 끝으로, 그의 선수 생활은 막을 내렸다.",
              "이렇게 그의 마지막 장이 조용히 닫혔다."],
}


def build_chapter_retrospective(rng, tracker, chapter_character, chapter_facts=None, chapter_seasons=None):
    """[2026-07 강화, v9 우선순위 3: 회고 구체화, GPT 검토: "돌이켜보면
    이 시기가 그의 커리어를 가장 잘 설명해주는 구간이었다 — 이건 아직도
    추상적이다. 차라리 '여러 나라를 떠돌았지만 끝내 자신의 자리를 잃지
    않았던 시기였다'처럼 구체적인 의미를 말하는 게 좋다"] 가능하면
    이미 계산해둔 chapter_facts/chapter_seasons(팀 수, 시즌 수 등)를
    반영한 더 구체적인 문장을 먼저 시도하고, 조건이 안 맞으면 기존
    추상적 뱅크로 폴백한다 — 새 판단 로직이 아니라 이미 있는 값을
    문장에 직접 꽂아넣는 것뿐이다."""
    if chapter_character == "wander" and chapter_seasons:
        teams = []
        for s in chapter_seasons:
            t = s.get("team_name", "")
            if t and t not in teams:
                teams.append(t)
        if len(teams) >= 3:
            bank = [
                f"{len(teams)}개 팀을 오가면서도, 그는 끝내 자신의 자리를 잃지는 않았던 시기였다.",
                f"{len(teams)}개 팀을 떠돌았지만, 축구 자체에 대한 확신만은 흔들리지 않았다.",
            ]
            return _pick(rng, bank, tracker, f"chapter_retro_specific:{chapter_character}")

    if chapter_character == "steady" and chapter_facts and chapter_facts.get("season_count", 0) >= 4:
        n = chapter_facts["season_count"]
        bank = [
            f"{n}시즌 동안 큰 굴곡 없이, 그러나 분명하게 자신의 자리를 지켜낸 시기였다.",
            f"화려한 장면은 적었지만, {n}시즌을 채운 꾸준함이 곧 그의 실력이었다.",
        ]
        return _pick(rng, bank, tracker, f"chapter_retro_specific:{chapter_character}")

    if chapter_character == "struggle" and chapter_facts and chapter_facts.get("worst_rating"):
        wy = chapter_facts["worst_season"].get("start_year", "") if chapter_facts.get("worst_season") else ""
        bank = [
            f"{wy}년의 어려움까지 포함해, 이 시기는 쉽지 않은 시간의 연속이었다 — 그러나 그는 끝내 자리를 지켜냈다.",
        ]
        return _pick(rng, bank, tracker, f"chapter_retro_specific:{chapter_character}")

    bank = CHAPTER_RETRO.get(chapter_character)
    if not bank:
        return None
    # [2026-07 수정] "돌이켜보면"으로 시작하는 변형도 회고 톤 공유
    # 예산을 쓴다 — 예산이 다 찼으면 그 변형은 후보에서 뺀다.
    if tracker.get("_global_retro_used", 0) >= 3:
        filtered = [t for t in bank if not t.startswith("돌이켜보면")]
        bank = filtered or bank
    picked = _pick(rng, bank, tracker, f"chapter_retro:{chapter_character}")
    if picked.startswith("돌이켜보면"):
        tracker["_global_retro_used"] = tracker.get("_global_retro_used", 0) + 1
    return picked


# [2026-07 신설, v9 우선순위 4: 회고 강화, GPT 검토: "챕터 말미가 단순
# 요약이 아니라, 이 시기에 시작된 문제가 이후 어떻게 이어졌는지까지
# 연결돼야 한다"] 지금까지 챕터 회고(build_chapter_retrospective)는
# 챕터 '성격'만 보고 고정 문장을 뽑았다 — 이 챕터에서 열린 채로 안
# 닫힌 NarrativeQuestion이 있으면, 다른 챕터를 포함해 커리어 전체에서
# 그게 언제/어떻게 해소되는지(또는 끝내 해소 안 됐는지)를 한 문장으로
# 잇는다. 새 데이터 없이 이미 있는 memory['questions']만 앞뒤로 훑는다.
_DEBT_CALLBACK_LINKED = {
    "STARTER_BATTLE": [
        "이 시기에 시작된 주전 경쟁은, {year}년이 되어서야 비로소 답을 찾는다.",
        "그때 열린 주전 경쟁이라는 물음은, {year}년에 가서야 정리된다.",
    ],
    "TITLE_CHASE": [
        "이때 품었던 우승에 대한 갈증은, {year}년이 되어서야 마침내 채워진다.",
        "이 시기의 갈증이 트로피로 돌아오기까지는 {year}년까지 기다려야 했다.",
    ],
    "TITLE_CHASE_AGAIN": [
        "이때 다시 고개를 든 우승에 대한 갈증은, {year}년이 되어서야 다시 채워진다.",
        "두 번째 우승을 향한 이 갈증은 {year}년에 가서야 풀린다.",
    ],
    "INJURY_COMEBACK": [
        "이 시기의 부상 여파는 {year}년에야 완전히 걷힌다.",
        "몸 상태를 완전히 되찾기까지는 {year}년까지 걸렸다.",
    ],
}
_DEBT_CALLBACK_UNRESOLVED = {
    "STARTER_BATTLE": [
        "이 시기에 시작된 경쟁은 끝내 완전히 해소되지 못한 채 남았다.",
        "그때 열린 경쟁의 물음표는 커리어 내내 완전히 지워지지 않았다.",
    ],
    "TITLE_CHASE": [
        "이때 품었던 갈증은 커리어 내내 완전히 채워지지 않았다.",
        "그 갈증은 끝내 두 번째 트로피로 이어지지 못했다.",
    ],
    "TITLE_CHASE_AGAIN": [
        "이때 다시 고개를 든 갈증은 커리어 내내 완전히 채워지지 않았다.",
        "두 번째 우승을 향한 이 갈증은 끝내 풀리지 못한 채 남았다.",
    ],
    "INJURY_COMEBACK": [
        "이 부상의 여파는 이후로도 완전히 걷히지 않았다.",
        "완전한 회복은 끝내 찾아오지 않았다.",
    ],
}


def build_chapter_debt_callback(rng, tracker, chapter_seasons, all_seasons, memory, idx_offset_map):
    """이 챕터가 끝나는 시점에 아직 열린 채로 남은 NarrativeQuestion이
    있으면, 커리어 전체(이후 다른 챕터 포함)를 훑어 언제 해소되는지 —
    또는 끝내 해소되지 않았는지 — 한 문장으로 만든다."""
    if not memory or not chapter_seasons:
        return None
    last_s = chapter_seasons[-1]
    last_idx = idx_offset_map.get(id(last_s))
    if last_idx is None:
        return None

    # 챕터 시작부터 끝까지 순서대로 재생해서, 끝난 시점에 '아직 열려
    # 있는' 질문 타입만 남긴다(여러 번 열렸다 닫혔다 해도 정확히 추적).
    open_types = []
    for i in range(0, last_idx + 1):
        s = all_seasons[i]
        for state, qtype in memory.get("questions", {}).get(id(s), []):
            if state == "OPEN":
                if qtype not in open_types:
                    open_types.append(qtype)
            elif state == "RESOLVED" and qtype in open_types:
                open_types.remove(qtype)

    if not open_types:
        return None
    open_qtype = open_types[-1]   # 가장 최근에 열린 것을 우선으로 콜백

    resolved_year = None
    for i in range(last_idx + 1, len(all_seasons)):
        s = all_seasons[i]
        for state, qtype in memory.get("questions", {}).get(id(s), []):
            if state == "RESOLVED" and qtype == open_qtype:
                resolved_year = s.get("start_year")
        if resolved_year:
            break

    if resolved_year:
        bank = _DEBT_CALLBACK_LINKED.get(open_qtype)
        if not bank:
            return None
        return _pick(rng, bank, tracker, f"debt_callback_linked:{open_qtype}").format(year=resolved_year)
    bank = _DEBT_CALLBACK_UNRESOLVED.get(open_qtype)
    if not bank:
        return None
    return _pick(rng, bank, tracker, f"debt_callback_unresolved:{open_qtype}")


# ── LEGEND 프로필 전용 — 이 시즌이 커리어 전체에서 어떤 위치인지 ──────
# [2026-07 신설, S급 개선 1] 예전엔 프로필 4단계(BASIC/EVENT/REFLECTION/
# LEGEND)가 실제로는 detail·highlight 유무 정도만 갈라서 분량 차이가
# 1~2문장에 그쳤다. LEGEND 시즌(비중도 크고 사건도 몰린 시즌)에는 이미
# 계산해둔 career_memory['analysis'](커리어 평균/최고 평점)와 비교하는
# 문장을 한 줄 더 얹는다 — 새 판단을 만들지 않고, 이미 있는 두 숫자를
# 비교만 하는 선에서 그친다.
LEGEND_PEAK_SENTENCES = [
    "돌이켜보면 이 시즌은 커리어를 통틀어 가장 높은 평점을 기록한 해였다.",
    "커리어 전체를 놓고 봐도, 이만한 경기력을 보여준 시즌은 손에 꼽힌다.",
    "훗날 돌아봤을 때도 이 시즌은 개인 커리어의 정점으로 남는다.",
]
LEGEND_ABOVE_AVG_SENTENCES = [
    "커리어 평균 평점을 눈에 띄게 웃돈 시즌이었다.",
    "통산 평균과 비교해도 확연히 두드러지는 활약이었다.",
    "이 시즌의 경기력은 커리어 전체 평균을 훌쩍 뛰어넘었다.",
]


def build_legend_reflection(rng, tracker, memory, s):
    """LEGEND 프로필에서만 호출. 지어내는 감상이 아니라, 이미 계산되어
    있는 career_memory['analysis']의 평균/최고 평점과 이 시즌의 평점을
    비교만 한다 — 비교 대상 숫자가 없으면(0이면) 아무 말도 하지 않는다."""
    if not memory:
        return None
    analysis = memory.get("analysis") or {}
    avg = analysis.get("avg_rating", 0)
    peak = analysis.get("peak_rating", 0)
    rating = s.get("avg_rating", 0) or 0
    if not rating or not avg:
        return None
    if peak and rating >= peak - 0.01:
        bank = LEGEND_PEAK_SENTENCES
    elif rating - avg >= 0.4:
        bank = LEGEND_ABOVE_AVG_SENTENCES
    else:
        return None
    return _pick(rng, bank, tracker, "legend_reflection")



# ══════════════════════════════════════════════════════════════════
# 5.5. 구조 분리 — Facts → Narrative Model → Editorial Pass → Text
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, v8 설계 1단계, 신민용+GPT 다차례 검토] 지금까지 render_season()
# 하나가 "문장 고르기"와 "이어붙이기"를 동시에 했다 — 기능이 늘수록(포지션별
# 템플릿, NarrativeQuestion, Narrative Score, Retrospective Pass 등, 앞으로
# 계획된 것만도 여러 개) 이 함수 하나에 조건문이 계속 쌓이는 구조였다.
#
# 3단계로 쪼갠다:
#   1) build_season_narrative() — 문장을 "이어붙이지 않고" 후보 조각들을
#      구조화된 dict로만 모은다. 각 조각은 {key, text} — key로 나중에
#      (Narrative Score 도입 후) 어떤 조각인지 식별/필터링할 수 있게 한다.
#   2) editorial_filter() — 후보들을 다듬는 단계. 지금(1단계 리팩터링)은
#      아무것도 안 하는 통과 함수다 — 이후 라운드(Narrative Score+Top-N,
#      의미 중복 제거 등)가 여기 들어갈 자리만 미리 만들어둔 것.
#   3) render_narrative_to_text() — 다듬어진 조각들을 그제서야 문장으로
#      이어붙인다.
#
# render_season()은 이 3단계를 순서대로 호출하는 얇은 래퍼로 남긴다 —
# render_chapter() 등 기존 호출부는 전혀 안 건드려도 된다. 이번 단계의
# 목표는 "출력이 기존과 완전히 동일한 채로 구조만 바뀌는 것"이다(안전한
# 리팩터링) — 실제 내용 변경(GK 템플릿 등)은 다음 단계부터.

# ══════════════════════════════════════════════════════════════════
# 6.8. Club Arc — 팀 스틴트 요약
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, v9 우선순위 2, GPT 검토: "8년 가까이 있었던 팀인데
# 글에서는 그냥 브래드포드 시즌, 브래드포드 시즌... 이다 — Club Arc가
# 들어가야 하는 자리"] 한 팀에 여러 시즌 머물렀던 스틴트 자체를 하나의
# 서사 단위로 요약한다. 새 데이터 불필요 — 이미 있는 시즌별 평점/트로피
# 연도만 훑는다. 그 팀을 떠나는 마지막 시즌에 한 문장만 추가한다.

CLUB_ARC_TEMPLATES = {
    "TROPHY": [
        "{y1}년부터 {y2}년까지 {n}시즌간 이어진 {team}에서의 시간은, 우승이라는 결실로 남았다.",
        "{team}에서 보낸 {n}시즌({y1}~{y2})은 트로피로 완성됐다.",
    ],
    "RISING_EXIT": [
        "{y1}년부터 {y2}년까지 {n}시즌 동안, {team}에서 그는 점점 더 단단해졌다 — 특히 {peak_year}년의 활약이 그 정점이었다.",
        "{team}에서의 {n}시즌({y1}~{y2})은 그에게 성장의 기록으로 남았다.",
    ],
    "STEADY": [
        "{y1}년부터 {y2}년까지 {n}시즌간, {team_eun} 그의 커리어에서 가장 오래 머문 곳 중 하나로 남았다.",
        "{team}에서 보낸 {n}시즌({y1}~{y2})은 화려하진 않아도 꾸준함으로 채워졌다.",
    ],
}


def build_club_arc_summary(rng, tracker, seasons, idx, trophy_years):
    """이 시즌이 한 팀 스틴트의 마지막 시즌이고, 그 스틴트가 3시즌
    이상이면 입단→전환점→퇴단을 한 문장으로 요약한다. group_eras()와
    같은 '같은 팀 연속 재직' 개념을 여기서는 시즌 하나 기준으로
    거슬러 올라가며 직접 찾는다(새 계산 아님, 이미 하던 팀 이력 스캔)."""
    team = seasons[idx].get("team_name", "")
    start = idx
    while start > 0 and seasons[start - 1].get("team_name") == team:
        start -= 1
    stint = seasons[start:idx + 1]
    if len(stint) < 3:
        return None

    y1 = stint[0].get("start_year", "")
    y2 = stint[-1].get("start_year", "")
    n = len(stint)
    rated = [(s, s.get("avg_rating", 0) or 0) for s in stint
             if s.get("matches", 0) > 0 and s.get("avg_rating")]
    peak_season, peak_rating = max(rated, key=lambda t: t[1]) if rated else (None, 0)
    has_trophy = any((s.get("start_year", 0) in trophy_years) for s in stint)

    if has_trophy:
        arc_result = "TROPHY"
    elif peak_season is not None and peak_season is stint[-1]:
        arc_result = "RISING_EXIT"
    else:
        arc_result = "STEADY"

    bank = CLUB_ARC_TEMPLATES[arc_result]
    tmpl = _pick(rng, bank, tracker, f"club_arc:{arc_result}")
    return tmpl.format(team=team, team_eun=with_josa(team, "은/는"), y1=y1, y2=y2, n=n,
                        peak_year=peak_season.get("start_year", y1) if peak_season else y1)


def build_season_narrative(rng, seasons, idx, awards_by_year, trophy_years, retire_age, player_age_at,
                            home_country="", cup_by_year=None, cl_by_year=None, intl_by_year=None,
                            template_tracker=None, player=None, is_chapter_start=False,
                            memory=None, chapter_character=None, categories=None, turning_indices=None):
    """1단계: 문장을 만들지 않는다. 이 시즌에 대해 "어떤 의미 조각들이
    있는지"만 candidates 리스트(각 원소는 {"key":..., "text":...})로
    모아서 구조화된 dict로 반환한다."""
    tracker = template_tracker if template_tracker is not None else {}
    s = seasons[idx]
    cat = categories[idx] if categories else classify_season(
        seasons, idx, awards_by_year, trophy_years, retire_age, player_age_at,
        home_country=home_country)
    year = s.get("start_year", 0)

    # [2026-07 신설, 신민용 리포트: "24위/22위처럼 진짜 바닥인 시즌도
    # 하위권/씨름 톤으로 뭉뚱그려진다"] CAT_RELEGATION 중에서도 순위
    # 비율이 특히 심각한(0.9 이상, 사실상 꼴찌권) 시즌만 별도의 더 무거운
    # 어투(SEVERE_RELEGATION_TEMPLATES)를 쓴다.
    is_severe_relegation = False
    if cat == CAT_RELEGATION:
        _rk = s.get("team_rank", 0)
        _tt = s.get("_total_teams", 0)
        is_severe_relegation = bool(_rk and _tt and _rk / _tt >= 0.9)

    # [2026-07 신설, 신민용 리포트: "23위/24팀인데도 '적응은 나쁘지
    # 않았다'로 끝난다"] CAT_NEW_CHALLENGE/CAT_ABROAD 역시 팀 순위가
    # 나쁘면(0.75 이상) 낙관적 마무리 대신 struggle 뱅크를 쓴다.
    is_bad_new_team_season = False
    if cat in (CAT_NEW_CHALLENGE, CAT_ABROAD):
        _rk2 = s.get("team_rank", 0)
        _tt2 = s.get("_total_teams", 0)
        is_bad_new_team_season = bool(_rk2 and _tt2 and _rk2 / _tt2 >= 0.75)

    def _select_bank():
        if is_severe_relegation:
            return SEVERE_RELEGATION_TEMPLATES, f"cat:{cat}:severe"
        if is_bad_new_team_season:
            if cat == CAT_NEW_CHALLENGE:
                return NEW_CHALLENGE_STRUGGLE_TEMPLATES, f"cat:{cat}:struggle"
            return ABROAD_STRUGGLE_TEMPLATES, f"cat:{cat}:struggle"
        return TEMPLATES[cat], f"cat:{cat}"

    # [2026-07 신설, v9 우선순위 1(최종): 정보 순서 다양화] 매번 "경기
    # 기록이 먼저, 국대/부상은 나중"이라는 고정 순서 대신, 조건이 맞는
    # 일부 시즌(약 30%)은 국대 미선발/부상으로 먼저 문을 연다. CAT_FINAL/
    # CAT_CHAMPION/CAT_DEBUT은 이미 그 자체로 강한 정체성이 있는
    # 카테고리라 이 대안 순서 대상에서 뺀다. 선택되면 이후 intl/injury
    # 후보 블록에서 같은 사실을 또 언급하지 않도록 플래그로 표시해둔다.
    used_intl_lead = False
    used_injury_lead = False
    _lead_extra = {}
    if cat not in (CAT_FINAL, CAT_CHAMPION, CAT_DEBUT):
        _intl_year = (intl_by_year or {}).get(year)
        if _intl_year and _intl_year[1] in _INTL_MISS_RESULTS and rng.random() < 0.3:
            tmpl = _pick(rng, INTL_MISS_LEAD_TEMPLATES, tracker, "intl_miss_lead")
            _lead_extra = {"competition": _intl_year[0]}
            used_intl_lead = True
        elif (memory or {}).get("injury_seasons", {}).get(id(s)) is not None and rng.random() < 0.3:
            # [2026-07 버그수정, 신민용 리포트: "36경기(78% 출전)나 뛰었는데
            # '부상이 시즌의 상당 부분을 앗아갔다'는 과장된 문구가 붙는다"]
            # confirmed_years 기반이면 confidence가 항상 1.0이라, 컵 경기
            # 딱 1번 결장한 것과 시즌 내내 결장한 것을 문구 강도로 구분할
            # 수 없었다. 실제 출전 수가 '평소'(typical_matches) 대비
            # 확실히 줄어든 시즌에만 이 강한 표현("상당 부분을 앗아갔다")을
            # 쓰고, 아니면 이 대안 리드 자체를 건너뛴다(뒤에서 결장 사실은
            # 더 절제된 문구로 여전히 언급된다).
            _typical = (memory or {}).get("typical_matches")
            _apps_ratio = (s.get("matches", 0) / _typical) if _typical else 0
            if _typical and _apps_ratio < 0.75:
                tmpl = _pick(rng, INJURY_LEAD_TEMPLATES, tracker, "injury_lead")
                used_injury_lead = True
            else:
                _bank, _bank_key = _select_bank()
                tmpl = _pick(rng, _bank, tracker, _bank_key)
        elif cat == CAT_FINAL and s.get("matches", 0) == 0:
            tmpl = _pick(rng, FINAL_INACTIVE_TEMPLATES, tracker, "final_inactive")
        else:
            _bank, _bank_key = _select_bank()
            tmpl = _pick(rng, _bank, tracker, _bank_key)
    elif cat == CAT_FINAL and s.get("matches", 0) == 0:
        tmpl = _pick(rng, FINAL_INACTIVE_TEMPLATES, tracker, "final_inactive")
    else:
        _bank, _bank_key = _select_bank()
        tmpl = _pick(rng, _bank, tracker, _bank_key)

    # [2026-07 재설계, 신민용 리포트: "왜 2001년(데뷔)에만 리그 소개가
    # 붙냐 — 팀들 관련해서 다 붙이라고 한 거다"] 처음엔 CAT_DEBUT/
    # NEW_CHALLENGE/ABROAD/HOMECOMING/LOAN 5개 카테고리에만 team_intro를
    # 박아뒀는데, classify_season()은 "그 해 수상이 있으면 무조건 STEADY"
    # 를 팀 변경 체크보다 먼저 보기 때문에(예: 해외 첫 시즌에 베스트11을
    # 받으면 ABROAD가 아니라 STEADY로 분류됨), 카테고리에 의존하면 정작
    # 새 팀에 합류한 시즌 상당수가 그냥 지나가 버렸다. 카테고리가 뭐로
    # 나오든 상관없이 "직전 시즌과 팀이 다른가"만 직접 확인해서, 새
    # 팀이면 어떤 템플릿을 쓰든 그 안의 {team}이 전부 team_intro로
    # 채워지게 한다.
    is_new_team = (idx == 0) or (seasons[idx - 1].get("team_name") != s.get("team_name"))
    _team_extra = {}
    if is_new_team:
        _intro = _team_intro(s)
        _team_extra = {"team": _intro, "team_ro": with_josa(_intro, "으로/로"),
                       "team_eun": with_josa(_intro, "은/는"), "team_ga": with_josa(_intro, "이/가")}
    _team_extra.update(_lead_extra)
    base_text = _fill(tmpl, s, extra=_team_extra)

    candidates = []

    def _add(key, text):
        if text:
            candidates.append({"key": key, "text": text})

    # ── Probe → Profile: 이 시즌에 재료가 얼마나 있고(밀도), 얼마나
    # 중요한지(비중)를 먼저 정하고, 그 프로필에 맞는 레이어만 채운다.
    facts = probe_season_facts(seasons, idx, categories or [cat], memory,
                                awards_by_year, trophy_years, cup_by_year, cl_by_year,
                                intl_by_year, turning_indices or set())
    profile = determine_profile(facts)

    # [2026-07 신설, v8 설계 4] NarrativeQuestion 이벤트를 미리 조회해서
    # Theme 매핑에 쓴다 — 아래 "질문" 후보 블록에서도 이 값을 재사용한다
    # (같은 조회를 두 번 안 함).
    q_events = (memory or {}).get("questions", {}).get(id(s), [])
    theme = determine_theme(cat, q_events)

    # 새 팀 합류 — 감독 성향/구단 목표를 별도 문단이 아니라 짧은 절로만
    # 흡수. REFLECTION 프로필(비중은 크지만 사건은 몰리지 않은 시즌)에서는
    # 이 절까지 생략해 "담백하게"라는 프로필 의도를 실제 분량 차이로 반영.
    # [2026-07 신설, 신민용 리포트: "팀 이야기가 거의 없다"] 감독 성향과
    # 구단 목표(club_ambition) 둘 다 매번 같이 나오면 부담스러우니, 매번
    # 둘 중 하나만 무작위로 고른다.
    # [2026-07 수정, 신민용 리포트: "팀마다 다 소개되면 또 반복된다 —
    # 커리어 전체에서 3~4팀 정도만"] 구단 목표 소개(team_ambition_clause)
    # 는 새 팀에 합류할 때마다 매번 나오면 오히려 상투적이 된다. 스토리
    # 전체에서 최대 4번까지만 — 첫 팀/주요 이적처럼 실제로 의미가 큰
    # 합류 시점에 자연스럽게 소진되도록 그냥 등장 순서대로 캡을 둔다.
    if cat in (CAT_NEW_CHALLENGE, CAT_ABROAD, CAT_HOMECOMING, CAT_DEBUT) and profile != PROFILE_REFLECTION:
        if rng.random() < 0.5:
            mclause = manager_clause(s)
            if mclause:
                _add("manager_clause", mclause)
        elif tracker.get("_team_ambition_used", 0) < 4:
            aclause = team_ambition_clause(s)
            if aclause:
                _add("manager_clause", aclause)
                tracker["_team_ambition_used"] = tracker.get("_team_ambition_used", 0) + 1

    # 포지션별 디테일 — REFLECTION 프로필만 담백하게 가려고 생략
    if profile != PROFILE_REFLECTION:
        _add("detail", build_detail_sentence(rng, tracker, s))

    # 경기 하이라이트 — EVENT/LEGEND 프로필에서만
    if profile in (PROFILE_EVENT, PROFILE_LEGEND):
        _add("highlight", build_highlight_sentence(rng, tracker, memory, year))

    # LEGEND 프로필 전용 — 커리어 평균/최고 평점과 비교하는 회고 한 줄을
    # 더 얹어, LEGEND와 BASIC의 분량 차이를 실제로 크게 벌린다.
    if profile == PROFILE_LEGEND:
        _add("legend_reflection", build_legend_reflection(rng, tracker, memory, s))

    # [2026-07 신설, 신민용 리포트: "감정이 부족하다 — 강등했다/우승했다
    # 로 끝난다"] 커리어에서 가장 크게 요동치는 두 순간(진짜 바닥의
    # 강등, 우승)에만 짧은 감정 한 줄을 더한다. 구체적 사건을 지어내지
    # 않고, 그 결과라면 누구나 겪을 법한 보편적인 반응만 담는다. 매번
    # 나오면 오히려 상투적이 되므로 확률적으로, 스토리 전체 최대 2번씩만.
    if cat == CAT_RELEGATION and is_severe_relegation and rng.random() < 0.5 \
            and tracker.get("_releg_emotion_used", 0) < 2:
        _add("emotion", _pick(rng, RELEGATION_EMOTION_SENTENCES, tracker, "releg_emotion"))
        tracker["_releg_emotion_used"] = tracker.get("_releg_emotion_used", 0) + 1
    elif cat == CAT_CHAMPION and rng.random() < 0.5 and tracker.get("_champion_emotion_used", 0) < 2:
        _add("emotion", _pick(rng, CHAMPION_EMOTION_SENTENCES, tracker, "champion_emotion"))
        tracker["_champion_emotion_used"] = tracker.get("_champion_emotion_used", 0) + 1

    # [2026-07 버그수정, 신민용 리포트: "2001년에 이미 베스트11을 받았는데
    # 스토리에 안 나온다 — 그래서 2002년 문구가 마치 '처음 인정받은
    # 순간'처럼 읽힌다"] CAT_DEBUT 시즌은 밀도 조절을 위해 수상 언급 자체를
    # 생략하고 있었는데, 그 결과 STAGE 카운터가 실제 수상 이력보다 한 해
    # 늦게 시작되면서 다음 해 문구가 사실과 어긋나 보였다. 데뷔 시즌에도
    # 수상이 있으면 담백하게라도 언급한다.
    if year in awards_by_year:
        names = awards_by_year[year]
        award_line = " · ".join(names)
        _award_fmt = dict(award_line=award_line, award_line_ro=with_josa(award_line, "으로/로"),
                           award_line_eul=with_josa(award_line, "을/를"),
                           award_line_eun=with_josa(award_line, "은/는"),
                           award_line_i=with_josa(award_line, "이/가"))

        # [2026-07 신설, 신민용 리포트: "베스트11 18번이 전부 같은 톤이다"]
        # 강등권 시즌이면 대비 문장(RELEGATION_AWARD_CONTRAST_SENTENCES),
        # 그 외에는 통산 수상 누적 횟수로 STAGE1~3을 나누고, 베테랑
        # 시기면 그보다 우선해서 "여전히" 톤의 전용 뱅크를 쓴다.
        if cat == CAT_RELEGATION:
            _add("award", _pick(rng, RELEGATION_AWARD_CONTRAST_SENTENCES, tracker,
                                 "award_relegation").format(**_award_fmt))
        else:
            _age = player_age_at(year)
            _is_veteran_award = bool(_age is not None and retire_age and _age >= retire_age - 1)
            if _is_veteran_award:
                _bucket, _bank = "VETERAN", AWARD_SENTENCES_VETERAN
            else:
                _acount = tracker.get("_award_count", 0) + 1
                tracker["_award_count"] = _acount
                _astage = "STAGE1" if _acount <= 2 else ("STAGE2" if _acount <= 5 else "STAGE3")
                _bucket, _bank = _astage, AWARD_SENTENCES_STAGED[_astage]

            # [2026-07 신설] 같은 톤 단계(bucket)에서는 그 단계에 처음
            # 진입했을 때만 "칭찬형" 문장을 쓰고, 그 다음부터는 중립
            # 문장으로 짧게 언급만 한다 — 같은 감탄이 반복되는 것을 막는다.
            _full_key = f"_award_full_used:{_bucket}"
            if not tracker.get(_full_key):
                _add("award", _pick(rng, _bank, tracker, f"award:{_bucket}").format(**_award_fmt))
                tracker[_full_key] = True
            else:
                _add("award", _pick(rng, AWARD_NEUTRAL_MENTION, tracker,
                                     "award_neutral").format(**_award_fmt))

    # 컵대회 — 8강 이상으로 깊이 간 시즌만 본문에 언급 (매번 다 넣으면
    # 20시즌 내내 같은 말이 반복돼 오히려 지루해진다).
    cup_by_year = cup_by_year or {}
    if year in cup_by_year:
        result, comp = cup_by_year[year]
        if result in _DEEP_CUP_RESULTS and cat != CAT_CHAMPION:
            _add("cup", _pick(rng, CUP_SENTENCES, tracker, "cup").format(
                competition=comp, cup_result=result, cup_result_ira=with_josa(result, "이라는/라는")))

    cl_by_year = cl_by_year or {}
    if year in cl_by_year:
        result, comp = cl_by_year[year]
        if result in _DEEP_CUP_RESULTS and cat != CAT_CHAMPION:
            _add("cl", _pick(rng, CL_SENTENCES, tracker, "cl").format(
                competition=comp, cup_result=result, cup_result_ira=with_josa(result, "이라는/라는")))

    # 국가대표 — 월드컵/대륙컵이 열린 해에만 등장하므로(2~4년 주기),
    # 나올 때마다 본문에 자연스럽게 끼워 넣는다. 소집이든 미선발이든
    # 둘 다 선수의 그 해를 설명하는 중요한 정보다.
    # [2026-07 수정] 컵/챔스처럼 딥런(4강 이상 등)이면 결과를 구체적으로
    # 언급한다 — 예전엔 월드컵 결승에 가든 조별리그 탈락하든 똑같이
    # "무대를 밟았다"로만 뭉뚱그려졌다.
    intl_by_year = intl_by_year or {}
    if year in intl_by_year and not used_intl_lead:
        comp, result = intl_by_year[year]
        if result in _INTL_MISS_RESULTS:
            _add("intl", _pick(rng, INTL_MISSED_SENTENCES, tracker, "intl_missed").format(competition=comp))
        elif result in _INTL_DEEP_RESULTS:
            _add("intl", _pick(rng, INTL_DEEP_SENTENCES, tracker, "intl_deep").format(
                competition=comp, result=result))
        else:
            _add("intl", _pick(rng, INTL_CALLED_SENTENCES, tracker, "intl_called").format(competition=comp))

    # 회고(foreshadow) — 부진/강등권/해외도전/임대처럼 '힘든' 시즌에서,
    # 아직 오지 않은 우승을 한 번만 미리 암시한다. 스토리 전체에서
    # 딱 한 번만 쓴다.
    # [2026-07 버그수정, 신민용 리포트: "이미 우승한 적 있는데 또 '첫
    # 우승의 준비 과정'이라고 한다"] find_next()가 찾아준 다음 우승이
    # 실제 커리어 첫 우승인지 아닌지를 확인해서 뱅크를 나눈다.
    # [2026-07 신설, 같은 리포트: "24위 같은 진짜 바닥 시즌까지도 항상
    # '미래를 위한 준비'로 미화한다"] 진짜 심각한 강등 시즌
    # (is_severe_relegation)이면 절반의 확률로 미래의 우승과 엮지 않고,
    # 그 자체로 힘들었던 시기로 남기는 FORESHADOW_BLEAK_SENTENCES를 쓴다.
    if (memory and cat in (CAT_DECLINING, CAT_RELEGATION, CAT_ABROAD, CAT_LOAN)
            and "foreshadow_title" not in tracker):
        next_title = find_next(memory, "title", year)
        if next_title:
            n = next_title["year"] - year
            if n > 0:
                if is_severe_relegation and rng.random() < 0.5:
                    _add("foreshadow", _pick(rng, FORESHADOW_BLEAK_SENTENCES, tracker,
                                              "foreshadow_bleak_pool"))
                else:
                    already_won = bool(categories) and any(c == CAT_CHAMPION for c in categories[:idx])
                    _bank = FORESHADOW_TITLE_AGAIN_SENTENCES if already_won else FORESHADOW_TITLE_SENTENCES
                    _add("foreshadow", _pick(rng, _bank, tracker, "foreshadow_title_pool").format(n=n))
                tracker["foreshadow_title"] = True

    # 부상 추정 — 신뢰도 구간별로 표현 수위를 다르게 한다. confidence가
    # 1.0이면 추정이 아니라 게임 기록으로 확인된 부상(CONFIRMED).
    if memory and not used_injury_lead:
        conf = memory.get("injury_seasons", {}).get(id(s))
        if conf is not None:
            if conf >= 0.99:
                bank = INJURY_CONFIRMED_SENTENCES
            elif conf >= 0.6:
                bank = INJURY_INFERENCE_SENTENCES_HIGH
            elif conf >= 0.35:
                bank = INJURY_INFERENCE_SENTENCES_MID
            else:
                bank = INJURY_INFERENCE_SENTENCES_LOW
            _add("injury", _pick(rng, bank, tracker, "injury_inference"))

    # [2026-07 신설, v8 설계 3] NarrativeQuestion — 이 시즌에 열리거나
    # 진행되거나 닫힌 질문이 있으면 그 상태에 맞는 문장을 하나 추가한다.
    # TITLE_CHASE가 이미 foreshadow 후보와 같은 "우승을 향한 기다림"
    # 테마를 다룰 수 있어, foreshadow가 이미 이 시즌에 채택됐으면
    # TITLE_CHASE 쪽은 생략해 같은 얘기를 두 번 안 하게 한다.
    # [2026-07 수정, v9 우선순위: Meaning Group, GPT 검토: "우승이라는
    # 물음표 / 첫 우승을 향한 갈증 — 문장은 다르지만 의미는 똑같은 게
    # 열려있는 내내 매 시즌 반복된다"] 질문이 오래 열려있으면(예: 우승
    # 도전이 6시즌 이어짐) PROGRESS 상태 문장이 그 6번 다 나왔는데, 이제
    # 같은 질문 인스턴스당(OPEN~RESOLVED 한 사이클) PROGRESS 언급은
    # 최대 2번까지만 — 그 이후는 조용히 생략한다(질문 자체는 계속
    # 진행되지만 매번 다시 말하지 않는다).
    # [2026-07 확장, v9 우선순위 4] NATIONAL_TEAM_CHASE는 예외 — 매번
    # 같은 말을 반복하는 게 아니라 진행 횟수에 따라 어투 자체가 바뀌므로
    # (초반 "아직 여유" → 중반 "반복되는 좌절" → 후반 "체념") cap 없이
    # 진행 횟수를 그대로 stage 선택에 쓴다.
    if memory:
        has_foreshadow = any(c["key"] == "foreshadow" for c in candidates)
        for state, qtype in q_events:
            if qtype in ("TITLE_CHASE", "TITLE_CHASE_AGAIN") and has_foreshadow and state != "RESOLVED":
                continue
            # [2026-07 신설, 신민용 리포트: "부상 관련 문장이 거의 매번
            # 등장한다"] INJURY_COMEBACK은 커리어 중 여러 차례(부상이 반복
            #될 때마다) 열렸다 닫혔다 할 수 있는데, 매 사이클마다 질문
            # 문장을 다 보여주면 전체 스토리에서 부상 언급이 과도하게
            # 반복된다. 스토리 전체에서 이 질문 문장은 최대 5번까지만
            # 노출하고, 그 이후는 조용히 생략한다(부상 자체는 injury
            # inference 문장으로 이미 별도 표현됨).
            if qtype == "INJURY_COMEBACK":
                _ginj_key = "_global_injury_q_used"
                if tracker.get(_ginj_key, 0) >= 5:
                    continue
                tracker[_ginj_key] = tracker.get(_ginj_key, 0) + 1
            if state == "OPEN":
                tracker[f"_qprog_used:{qtype}"] = 0
            elif state == "PROGRESS" and qtype in QUESTION_PROGRESS_STAGED:
                _prog_key = f"_qprog_used:{qtype}"
                stage_n = tracker.get(_prog_key, 0) + 1
                tracker[_prog_key] = stage_n
                stage = "STAGE1" if stage_n <= 2 else ("STAGE2" if stage_n <= 4 else "STAGE3")
                bank = QUESTION_PROGRESS_STAGED[qtype].get(stage)
                if bank:
                    _add("question", _pick(rng, bank, tracker, f"question:{qtype}:{state}:{stage}"))
                continue
            elif state == "PROGRESS":
                _prog_key = f"_qprog_used:{qtype}"
                if tracker.get(_prog_key, 0) >= 2:
                    continue
                tracker[_prog_key] = tracker.get(_prog_key, 0) + 1
            bank = {"OPEN": QUESTION_OPEN_SENTENCES,
                    "PROGRESS": QUESTION_PROGRESS_SENTENCES,
                    "RESOLVED": QUESTION_RESOLVED_SENTENCES}[state].get(qtype)
            if bank:
                _add("question", _pick(rng, bank, tracker, f"question:{qtype}:{state}"))

    # 성격/신체특징 플레이버 — [2026-07 수정, v9 우선순위: 중복표현
    # 감소, GPT 검토: "2001 성격 언급, 2008/2016 없음, 2020 은퇴에서
    # 한 번 더 — 이 정도가 더 강하다. 좋은 전기는 안 하는 말이 많다"]
    # 예전엔 챕터마다(즉 4~6번) 등장했는데, 스토리 전체에서 최대
    # 2번으로 전역 제한한다 — 첫 등장(주로 1부)과 마지막 기회(주로
    # 은퇴 근처)에만 자연스럽게 남도록.
    if is_chapter_start and tracker.get("_trait_used", 0) < 2:
        flavor = _trait_flavor(rng, tracker, player)
        if flavor:
            _add("trait", flavor)
            tracker["_trait_used"] = tracker.get("_trait_used", 0) + 1

    # 팀 이탈(같은 챕터 안에서 다음 시즌에 팀이 바뀌는 경우) — 짧은 톤 절
    if idx + 1 < len(seasons) and seasons[idx + 1].get("team_name") != s.get("team_name"):
        etone = exit_tone_clause(s)
        if etone:
            _add("exit_tone", f"{etone} 팀을 떠나게 됐다.")

        # [2026-07 신설, v9 우선순위 2: Club Arc] 3시즌 이상 머문 팀을
        # 떠나는 시즌에만, 그 스틴트 전체를 한 문장으로 요약한다.
        _add("club_arc", build_club_arc_summary(rng, tracker, seasons, idx, trophy_years))

    # [2026-07 신설, v8 설계 우선순위 6: Retrospective Commentary] 은퇴
    # 시점 지식으로 과거를 재해석하는 문장 — "당시엔 평범해 보였지만,
    # 훗날 돌아보면 분기점이었다." 대상 선정에 새 로직이 필요 없다 —
    # 이미 챕터 경계를 정할 때 쓴 detect_turning_points() 결과(전환점)를
    # 그대로 재사용한다.
    # [2026-07 수정, v9 우선순위: 회고 확대, GPT 검토: "가장 아쉬운
    # 부분 — 미래를 알고 있는 서술자가 거의 없다"] 스토리 전체 사용
    # 횟수를 2→3으로 늘리고, 이 시즌 이후 실제 우승 연도를 알 수 있으면
    # (find_next로 이미 조회 가능) "밑거름이 됐다"처럼 그 우승과 직접
    # 연결하는 더 구체적인 문장을 쓴다 — 막연한 "분기점이었다"보다
    # 훨씬 전기다운 회고가 된다. 데뷔/은퇴 시즌은 이미 그 자체로
    # 극적으로 다뤄지므로 제외.
    # [주의] foreshadow(구체적 "N년 뒤 우승" 스포일러)와, 이미 이 시즌
    # 자체가 누가 봐도 중요한 경우(award/highlight/legend_reflection이
    # 있음)와는 겹치지 않게 한다 — "당시엔 평범해 보였다"는 회고 문장의
    # 전제 자체가, 이미 우승·수상·명장면으로 화려하게 다뤄진 시즌에는
    # 자기모순이 된다. question은 실제 데이터에서 전환점과 대부분 동시에
    # 발생해서 가드에 넣으면 이 기능이 사실상 거의 안 발동하므로 제외한다.
    # [2026-07 수정, v9 우선순위: 회고 확대] 예전엔 award도 충돌 목록에
    # 있었는데, 베스트11처럼 매 시즌 흔하게 나오는 수상까지 막으면(실제
    # 세이브에서 거의 매년 수상하는 선수가 드물지 않음) 회고가 사실상
    # 한 번도 안 나오는 문제가 있었다. award는 "당시엔 평범해 보였다"와
    # 완전히 모순되진 않으므로(리그 인정은 받았어도 커리어 전체에서
    # 보면 여전히 평범한 해일 수 있음) 빼고, 진짜 화려한 신호(하이라이트/
    # 레전드 회고)만 충돌로 남긴다.
    _retro_conflict_keys = {"foreshadow", "highlight", "legend_reflection"}
    # [2026-07 수정, 신민용 리포트: "돌이켜보면/훗날/이때가 변화의
    # 시작이었다 류가 거의 모든 챕터에 나온다 — AI가 미래를 다 아는
    # 느낌"] 이 회고 문장 하나만 따로 3번까지 허용했었는데, foreshadow·
    # 챕터 회고·브리지·챕터 콜백 등 비슷한 '회고 톤' 문장이 스토리
    # 곳곳에 흩어져 있어서 각각은 개별적으로 캡이 걸려 있어도 합쳐지면
    # 누적 빈도가 높았다. 이제 이 회고 톤 전체가 스토리 하나에서 공유하는
    # 예산(_global_retro_used, 최대 5)을 쓴다 — 여기서도 그 예산을
    # 소비한다.
    if (turning_indices and idx in turning_indices and 0 < idx < len(seasons) - 1
            and cat != CAT_CHAMPION
            and tracker.get("_global_retro_used", 0) < 3
            and not any(c["key"] in _retro_conflict_keys for c in candidates)):
        next_title = find_next(memory, "title", year) if memory else None
        # [2026-07 수정] next_title이 있으면 예전엔 항상 "N년 후 우승"을
        # 못박는 LINKED 문장을 썼는데, 이것도 결국 미래를 스포일링하는
        # 형태라 30%로만 쓰고 나머지는 결과를 언급하지 않는 일반 회고로
        # 돌린다.
        if next_title and next_title["year"] > year and rng.random() < 0.3:
            retro = _pick(rng, RETROSPECTIVE_LINKED_SENTENCES, tracker, "retrospective_linked").format(
                n=next_title["year"] - year)
        else:
            retro = _pick(rng, RETROSPECTIVE_SENTENCES, tracker, "retrospective")
        _add("retrospective", retro)
        tracker["_global_retro_used"] = tracker.get("_global_retro_used", 0) + 1

    # 브리지 — 다음 시즌으로 넘어가는 연결 문장(조건 안 맞으면 None).
    # [2026-07 수정, v9 우선순위: 중복표현 감소, GPT 검토: "상승세는
    # 멈추지 않았다/신뢰를 유지했다가 정말 많이 반복된다"] question
    # 후보(주전경쟁/우승도전 등)가 이미 이 시즌에 있으면, 그 문장이
    # 이미 "다음 시즌으로 이어지는 흐름"을 담고 있으므로 continue/
    # reverse 브리지는 생략한다(같은 역할을 두 번 안 함). turning은
    # 전환점 자체의 무게가 다르므로 예외로 계속 넣는다.
    if categories and turning_indices is not None:
        has_question = any(c["key"] == "question" for c in candidates)
        bridge = build_bridge_sentence(rng, tracker, categories, idx, turning_indices)
        is_turning_bridge = (idx + 1) in turning_indices if (idx + 1) < len(categories) else False
        if bridge and (is_turning_bridge or not has_question):
            _add("bridge", bridge)

    # [2026-07 신설, v9 우선순위 1(최종)] highlight 후보는 이미 그
    # 자체로 완결된 문장("그 시즌 최고의 순간은 ~였다")이라, 순서를
    # 앞으로 옮겨도 문법이 안 깨진다 — 국대/부상 리드가 이미 쓰인
    # 시즌이 아닐 때만, 일정 확률로 base_text보다 먼저 오도록 표시한다.
    lead_key = None
    if not used_intl_lead and not used_injury_lead:
        if any(c["key"] == "highlight" for c in candidates) and rng.random() < 0.35:
            lead_key = "highlight"

    return {
        "season": s, "category": cat, "profile": profile, "facts": facts,
        "theme": theme, "question_events": q_events, "lead_key": lead_key,
        "base_text": base_text, "candidates": candidates,
    }


# ══════════════════════════════════════════════════════════════════
# 5.6. Narrative Score + Top-N (editorial_filter의 실제 알고리즘)
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, v8 설계 우선순위 5, GPT 검토: "Budget(예산)보다 Score
# (점수) + Top-N이 더 직관적이다"] LEGEND 시즌은 하이라이트/수상/컵/
# 챔스/국대/질문까지 후보가 다 몰릴 수 있다 — 전부 문단에 욱여넣으면
# 오히려 읽기 피곤해진다. "내용"에 해당하는 후보(highlight/award/cup/
# cl/intl/question/legend_reflection)만 점수를 매겨 상위 3개만 채택하고
# 나머지는 버린다. 구조/연결용 후보(manager_clause/detail/trait/
# exit_tone/bridge/foreshadow/injury)는 채점 대상이 아니다 — 이미 각자
# 별도 조건으로 엄격히 게이팅돼 있어 항상 소량이고, 빼면 서사 흐름
# 자체가 끊기기 때문이다.

CONTENT_KEYS = {"highlight", "award", "cup", "cl", "intl", "question",
                 "legend_reflection", "club_arc", "injury"}
CANDIDATE_BASE_SCORE = {
    "club_arc": 75, "award": 70, "legend_reflection": 65, "highlight": 60,
    "intl": 55, "question": 50, "cl": 45, "cup": 40, "injury": 35,
}
NARRATIVE_TOP_N = 3


def _narrative_score(candidate, narrative):
    """[2026-07 신설] 후보 하나의 '이야기할 가치'를 점수화한다. payoff
    (NarrativeQuestion을 해소하는 사건인가)만 지금 반영한다 — novelty/
    emotion/career_impact는 Role/Status 등 아직 없는 축이 있어야 정확히
    계산되므로, 그 축들이 생기는 다음 단계에서 이어서 확장한다."""
    base = CANDIDATE_BASE_SCORE.get(candidate["key"], 30)
    if candidate["key"] == "question":
        events = narrative.get("question_events", [])
        if any(st == "RESOLVED" for st, _ in events):
            base += 20   # payoff — 열려있던 질문을 닫는 사건
        elif any(st == "OPEN" for st, _ in events):
            base += 10   # 새로 여는 것도 어느 정도는 가치가 있음
    return base


def editorial_filter(narrative):
    """2단계: '내용' 후보들을 Narrative Score로 채점해서 상위
    NARRATIVE_TOP_N개만 남긴다. 후보가 그 이하면 아무것도 안 자른다 —
    조용한 시즌은 원래도 후보가 적어서 이 필터가 사실상 작동하지 않고,
    LEGEND처럼 후보가 몰리는 시즌에서만 실제로 걸러낸다."""
    candidates = narrative["candidates"]
    content = [c for c in candidates if c["key"] in CONTENT_KEYS]
    if len(content) > NARRATIVE_TOP_N:
        scored = sorted(content, key=lambda c: -_narrative_score(c, narrative))
        keep_ids = {id(c) for c in scored[:NARRATIVE_TOP_N]}
        narrative["candidates"] = [c for c in candidates
                                    if c["key"] not in CONTENT_KEYS or id(c) in keep_ids]
    return narrative


def render_narrative_to_text(narrative):
    """3단계: 다듬어진 조각들을 그제서야 문장으로 이어붙인다.
    [2026-07 수정, v9 우선순위 1(최종)] lead_key가 지정돼 있으면 그
    후보를 base_text보다 앞에 놓는다 — "경기기록이 항상 먼저"라는
    고정 순서를 깨는 자리."""
    candidates = narrative["candidates"]
    lead_key = narrative.get("lead_key")
    if lead_key:
        lead_c = next((c for c in candidates if c["key"] == lead_key), None)
    else:
        lead_c = None
    rest = [c["text"] for c in candidates if c is not lead_c]
    if lead_c:
        return lead_c["text"] + "  " + narrative["base_text"] + (
            ("  " + " ".join(rest)) if rest else "")
    extras = rest
    return narrative["base_text"] + ("  " + " ".join(extras) if extras else "")


def render_season(rng, seasons, idx, awards_by_year, trophy_years, retire_age, player_age_at,
                   home_country="", cup_by_year=None, cl_by_year=None, intl_by_year=None,
                   template_tracker=None, player=None, is_chapter_start=False,
                   memory=None, chapter_character=None, categories=None, turning_indices=None):
    """공개 진입점 — render_chapter() 등 기존 호출부는 이 시그니처를 그대로
    쓴다. 내부적으로 위 3단계 파이프라인을 순서대로 호출하는 얇은 래퍼."""
    narrative = build_season_narrative(
        rng, seasons, idx, awards_by_year, trophy_years, retire_age, player_age_at,
        home_country=home_country, cup_by_year=cup_by_year, cl_by_year=cl_by_year,
        intl_by_year=intl_by_year, template_tracker=template_tracker, player=player,
        is_chapter_start=is_chapter_start, memory=memory, chapter_character=chapter_character,
        categories=categories, turning_indices=turning_indices)
    narrative = editorial_filter(narrative)
    return render_narrative_to_text(narrative)


# ══════════════════════════════════════════════════════════════════
# 6.5. ChapterFacts — 챕터 단위 Probe
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, S급 개선 2] Season에는 probe_season_facts()가 있었지만
# Chapter에는 없어서, 챕터 회고가 항상 "성격(character)"만 보고 고정된
# 2문장 뱅크에서 하나 고르는 걸로 끝났다 — 사건이 몰린 챕터든 조용한
# 챕터든 회고 분량이 똑같았다. build_chapter_facts()로 "이 챕터에서 가장
# 좋았던/힘들었던 시즌이 언제인지"만 이미 있는 avg_rating 값들을 비교해서
# 뽑아두고, build_chapter_expansion()이 그걸로 조건부 문장을 추가한다 —
# 여기서도 새 데이터를 만들지 않고 season들의 avg_rating을 비교만 한다.

def build_chapter_facts(chapter_seasons, chapter_categories, memory):
    """챕터 안 시즌들을 훑어 '이미 있는 값들의 비교 결과'만 요약한다."""
    playing = [s for s in chapter_seasons if s.get("matches", 0) > 0]
    rated = [(s, s.get("avg_rating", 0) or 0) for s in playing if s.get("avg_rating", 0)]
    best_season, best_rating = max(rated, key=lambda t: t[1]) if rated else (None, 0)
    worst_season, worst_rating = min(rated, key=lambda t: t[1]) if rated else (None, 0)
    avg_rating = round(sum(r for _, r in rated) / len(rated), 2) if rated else 0

    cnt = Counter(c for c in chapter_categories if c)
    dominant_category, dominant_n = cnt.most_common(1)[0] if cnt else (None, 0)
    density = sum(1 for c in chapter_categories
                  if c in (CAT_CHAMPION, CAT_ABROAD, CAT_HOMECOMING, CAT_VETERAN, CAT_RELEGATION))

    return {
        "season_count": len(chapter_seasons),
        "best_season": best_season, "best_rating": best_rating,
        "worst_season": worst_season, "worst_rating": worst_rating,
        "avg_rating": avg_rating,
        "dominant_category": dominant_category,
        "density": density,
    }


CHAPTER_BEST_SEASON_SENTENCES = [
    "이 시기의 정점은 {year}년 {team}에서였다 — 평점 {rating_ro} 이 챕터를 대표하는 시즌이었다.",
    "특히 {year}년 {team}에서의 활약(평점 {rating})이 이 시기를 상징한다.",
    "이 챕터 안에서만 놓고 보면, {year}년 {team}에서의 모습이 가장 선명하게 남는다.",
]
CHAPTER_CONTRAST_SENTENCES = [
    "같은 챕터 안에서도 {best_year}년의 정점과 {worst_year}년의 굴곡이 뚜렷하게 갈렸다.",
    "이 시기는 {best_year}년의 최고점과 {worst_year}년의 어려움을 모두 품고 있었다.",
]


def build_chapter_expansion(rng, tracker, chapter_facts, chapter_character):
    """ChapterFacts를 보고 조건이 맞을 때만 챕터 확장 문장을 만든다.
    시즌이 너무 적거나(2 미만) 사건 밀도가 낮으면 아무것도 추가하지
    않는다 — '사건이 많은 챕터일수록 자연스럽게 길어진다'는 원칙을
    챕터 단위에도 그대로 적용한다."""
    if not chapter_facts or chapter_facts["season_count"] < 2:
        return None
    if chapter_character in ("trophy", "final", "debut"):
        # 우승/데뷔/은퇴 챕터는 본문 자체가 이미 그 시즌을 강조하므로
        # 같은 내용을 또 요약하면 중복이 된다.
        return None

    lines = []
    best, worst = chapter_facts["best_season"], chapter_facts["worst_season"]
    if best and chapter_facts["best_rating"] and chapter_facts["density"] >= 1:
        tmpl = _pick(rng, CHAPTER_BEST_SEASON_SENTENCES, tracker, "chapter_best")
        lines.append(_fill(tmpl, best))

    if (best and worst and best is not worst
            and chapter_facts["best_rating"] - chapter_facts["worst_rating"] >= 0.5):
        tmpl = _pick(rng, CHAPTER_CONTRAST_SENTENCES, tracker, "chapter_contrast")
        lines.append(tmpl.format(best_year=best.get("start_year", ""),
                                  worst_year=worst.get("start_year", "")))

    return " ".join(lines) if lines else None


# ══════════════════════════════════════════════════════════════════
# 6.7. Pacing — Scene(자세히) / Summary(압축)
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, v8/v9 설계 최우선순위, GPT 검토: "좋은 시즌과 평범한
# 시즌의 밀도가 같다 — 2005(우승) 500자, 2006(중위권) 120자 정도가
# 되어야 진짜 전기다"] 지금까지(Narrative Score+Top-N)는 "한 시즌
# 안에서" 뭘 뺄지만 다뤘지, "여러 시즌을 통째로 압축"하는 건 없었다.
# 판정 재료는 전부 이미 있다 — 카테고리, 전환점, NarrativeQuestion
# 이벤트만 보고 "이 시즌이 자세히 다룰 가치가 있는지(Scene)"를 정하고,
# **연속으로 2개 이상** 조용한(Quiet) 시즌이 나오면 하나로 묶어 압축
# 문단으로 만든다. 새 데이터·새 판정 로직 없음 — 이미 계산해둔 것만
# 재사용.

def _is_quiet_season(cat, s, memory, idx_in_all, turning_indices, is_new_team=False):
    """Scene(False)인지 Summary 후보(True)인지. 우승/데뷔/은퇴/전환성
    카테고리(강등권·해외·귀국·새도전·임대·베테랑·벤치)는 항상 Scene —
    이미 그 자체로 서사적 무게가 있는 시즌들이다. 전환점이거나
    NarrativeQuestion 이벤트가 있는 시즌도 Scene(질문이 열리고/닫히는
    순간을 압축하면 서사가 끊긴다). 나머지(STEADY/RISING/DECLINING)만
    압축 후보.
    [2026-07 버그수정] classify_season()은 "그 해 수상이 있으면 무조건
    STEADY"를 팀 변경 체크보다 먼저 보기 때문에, 새 팀에 합류한 해에
    수상까지 겹치면 카테고리만으로는 "조용한 시즌"으로 오인된다(팀
    소개 기능에서 발견한 것과 동일한 원인). is_new_team을 직접 넘겨받아
    카테고리와 무관하게 진짜 팀이 바뀐 시즌은 항상 Scene으로 남긴다."""
    if cat is None:
        return False
    if is_new_team:
        return False
    if cat in (CAT_CHAMPION, CAT_DEBUT, CAT_FINAL, CAT_RELEGATION,
               CAT_ABROAD, CAT_HOMECOMING, CAT_NEW_CHALLENGE, CAT_LOAN,
               CAT_LOAN_RETURN, CAT_VETERAN, CAT_BENCH):
        return False
    if turning_indices and idx_in_all in turning_indices:
        return False
    # [2026-07 수정, 신민용 리포트: "시즌을 나열하고 있다 — 압축이 거의
    # 안 일어난다"] 압축 후보에서 제외되는 조건에 q_events가 하나라도
    # 있으면 무조건 Scene으로 잡았는데, NATIONAL_TEAM_CHASE는 국제대회가
    # 열리는 해마다 거의 매번 열리거나 진행되는 상시적인 배경 사건이라
    # 이 조건 하나 때문에 사실상 모든 시즌이 압축 대상에서 빠졌다.
    # STARTER_BATTLE/TITLE_CHASE/INJURY_COMEBACK처럼 실제 서사적 전환이
    # 있는 질문만 Scene을 강제하고, NATIONAL_TEAM_CHASE 단독 이벤트는
    # 압축을 막지 않는다(대신 요약 문단 쪽에서 대표로 한 번 언급한다).
    q_events = (memory or {}).get("questions", {}).get(id(s), [])
    significant_q = [e for e in q_events if e[1] != "NATIONAL_TEAM_CHASE"]
    if significant_q:
        return False
    return cat in (CAT_STEADY, CAT_RISING, CAT_DECLINING)


def build_pacing_groups(chapter_seasons, chap_categories, memory, turning_indices, idx_offset_map,
                         all_seasons=None):
    """챕터 안 시즌들을 ("scene",[i]) 또는 ("summary",[i,i+1,...]) 그룹
    리스트로 나눈다. Quiet 시즌이 연속 2개 이상일 때만 summary로 묶고,
    1개짜리는 굳이 묶지 않고 그냥 scene으로 둔다(압축의 이득이 없음)."""
    runs = []
    cur_idxs = []
    cur_quiet = None
    for i, s in enumerate(chapter_seasons):
        idx = idx_offset_map[id(s)]
        cat = chap_categories[i]
        is_new_team = False
        if all_seasons is not None:
            is_new_team = (idx == 0) or (all_seasons[idx - 1].get("team_name") != s.get("team_name"))
        quiet = _is_quiet_season(cat, s, memory, idx, turning_indices, is_new_team=is_new_team)
        if cur_idxs and quiet == cur_quiet:
            cur_idxs.append(i)
        else:
            if cur_idxs:
                runs.append((cur_quiet, cur_idxs))
            cur_idxs = [i]
            cur_quiet = quiet
    if cur_idxs:
        runs.append((cur_quiet, cur_idxs))

    groups = []
    for quiet, idxs in runs:
        if quiet and len(idxs) >= 2:
            groups.append(("summary", idxs))
        else:
            for j in idxs:
                groups.append(("scene", [j]))
    return groups


SUMMARY_TEMPLATES = [
    "이후 {n}시즌({y1}~{y2}) 동안 {team_clause}큰 굴곡 없이 흘러갔다 — 시즌마다 평균 {avg_apps}경기, "
    "평점 {avg_rating} 안팎의 담담한 시간이었다.",
    "{y1}년부터 {y2}년까지 {n}시즌은 {team_clause}조용히 흘러갔다. 특별한 사건 없이, "
    "평균 평점 {avg_rating}의 꾸준함만 쌓여갔다.",
    "그 뒤 {n}시즌간은 {team_clause}큰 부침 없는 시간이었다 — 평균 {avg_apps}경기씩 뛰며 자신의 자리를 지켰다.",
]


def render_summary_block(rng, tracker, group_seasons, memory=None):
    """압축 그룹 하나를 한 문단으로 요약한다. 새 계산이 아니라 이미
    있는 시즌별 값들(경기수/평점)의 평균만 낸다.
    [2026-07 수정] 압축된 시즌들 중 NATIONAL_TEAM_CHASE 이벤트가 있던
    시즌이 있으면, 그 흐름이 완전히 사라지지 않도록 대표로 한 번만
    언급한다(스토리 전체의 진행 카운터를 그대로 재사용해 톤을 맞춘다)."""
    n = len(group_seasons)
    y1 = group_seasons[0].get("start_year", "")
    y2 = group_seasons[-1].get("start_year", "")
    teams = []
    for s in group_seasons:
        t = s.get("team_name", "")
        if t and t not in teams:
            teams.append(t)
    team_clause = f"{teams[0]}에서 " if len(teams) == 1 else ""
    apps_vals = [s.get("matches", 0) or 0 for s in group_seasons]
    rating_vals = [s.get("avg_rating", 0) or 0 for s in group_seasons if s.get("avg_rating")]
    avg_apps = round(sum(apps_vals) / max(1, len(apps_vals)))
    avg_rating = round(sum(rating_vals) / len(rating_vals), 1) if rating_vals else 0
    tmpl = _pick(rng, SUMMARY_TEMPLATES, tracker, "pacing_summary")
    out = tmpl.format(n=n, y1=y1, y2=y2, team_clause=team_clause,
                       avg_apps=avg_apps, avg_rating=avg_rating)

    natl_events = []
    for s in group_seasons:
        natl_events += [e for e in (memory or {}).get("questions", {}).get(id(s), [])
                         if e[1] == "NATIONAL_TEAM_CHASE"]
    if natl_events:
        state = natl_events[-1][0]
        if state != "OPEN":
            _prog_key = "_qprog_used:NATIONAL_TEAM_CHASE"
            stage_n = tracker.get(_prog_key, 0) + 1
            tracker[_prog_key] = stage_n
            stage = "STAGE1" if stage_n <= 2 else ("STAGE2" if stage_n <= 4 else "STAGE3")
            bank = QUESTION_PROGRESS_STAGED["NATIONAL_TEAM_CHASE"].get(stage) \
                if state == "PROGRESS" else QUESTION_RESOLVED_SENTENCES.get("NATIONAL_TEAM_CHASE")
        else:
            bank = QUESTION_OPEN_SENTENCES.get("NATIONAL_TEAM_CHASE")
        if bank:
            out += " " + _pick(rng, bank, tracker, f"summary_natl:{state}")
    return out


def render_chapter(rng, chapter_seasons, all_seasons, awards_by_year, trophy_years,
                    retire_age, player_age_at, idx_offset_map, home_country="",
                    cup_by_year=None, cl_by_year=None, intl_by_year=None,
                    template_tracker=None, player=None, memory=None, chapter_character=None,
                    categories=None, turning_indices=None):
    tracker = template_tracker if template_tracker is not None else {}
    parts = []
    chap_categories = []
    for i, s in enumerate(chapter_seasons):
        idx = idx_offset_map[id(s)]
        chap_categories.append(categories[idx] if categories else None)

    # [2026-07 신설] Pacing — 연속된 조용한 시즌은 압축 문단 하나로.
    pacing_groups = build_pacing_groups(chapter_seasons, chap_categories, memory,
                                         turning_indices, idx_offset_map, all_seasons=all_seasons)

    for kind, idxs in pacing_groups:
        if kind == "summary":
            group_seasons = [chapter_seasons[j] for j in idxs]
            parts.append(render_summary_block(rng, tracker, group_seasons, memory=memory))
        else:
            i = idxs[0]
            s = chapter_seasons[i]
            idx = idx_offset_map[id(s)]
            parts.append(render_season(rng, all_seasons, idx, awards_by_year, trophy_years,
                                        retire_age, player_age_at, home_country=home_country,
                                        cup_by_year=cup_by_year, cl_by_year=cl_by_year,
                                        intl_by_year=intl_by_year, template_tracker=template_tracker,
                                        player=player, is_chapter_start=(i == 0),
                                        memory=memory, chapter_character=chapter_character,
                                        categories=categories, turning_indices=turning_indices))
            # [2026-08 신설, PHASE 5] 이 시즌의 연승/연패/로테이션/결장
            # 구간 문장, [2026-08 신설] 이적 시 팀 수준(부수/연봉) 비교
            # 문장이 있으면 시즌 문단 바로 뒤에 이어붙인다(이적 비교가
            # 먼저, 그다음 그 시즌 안의 경기 흐름).
            if memory:
                _key = (s.get("team_name", ""), s.get("start_year", 0))
                _tn = memory.get("transfer_narratives", {}).get(_key)
                if _tn:
                    parts.append(_tn)
                _mn = memory.get("match_narratives", {}).get(_key)
                if _mn:
                    parts.append(" ".join(_mn))

    chapter_facts = build_chapter_facts(chapter_seasons, chap_categories, memory)
    expansion = build_chapter_expansion(rng, tracker, chapter_facts, chapter_character)
    if expansion:
        parts.append(expansion)

    retro = build_chapter_retrospective(rng, tracker, chapter_character,
                                         chapter_facts=chapter_facts, chapter_seasons=chapter_seasons)
    if retro:
        parts.append(retro)

    # [2026-07 신설, v9 우선순위 4] 이 챕터에서 아직 안 닫힌 질문이
    # 있으면, 커리어 전체에서 그게 어떻게 됐는지 한 문장으로 잇는다.
    # 너무 자주 나오면 오히려 예측 가능해지므로 스토리 전체 최대 3번.
    if tracker.get("_debt_callback_used", 0) < 3:
        callback = build_chapter_debt_callback(rng, tracker, chapter_seasons, all_seasons, memory, idx_offset_map)
        if callback:
            parts.append(callback)
            tracker["_debt_callback_used"] = tracker.get("_debt_callback_used", 0) + 1

    return "\n\n".join(parts)


def generate_prologue(player, seasons, has_trophy, awards, rng, trajectory=None):
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
    # [2026-08 신설, PHASE 1] 데뷔 초반 벤치→주전 정착 서사가 있으면
    # body 앞에 한 문장 붙인다 — "이제 그 커리어를 처음부터 돌아본다"는
    # 마무리 문장 바로 앞이 "그 시작이 어땠는지"를 붙이기 자연스러운 자리.
    traj_sentence = build_starting_trajectory_sentence(rng, trajectory)
    if traj_sentence:
        body = traj_sentence + " " + body
    return rng.choice(openers) + "\n\n" + body


def _longest_stint(seasons):
    """가장 오래 몸담은 팀과 그 기간(시즌 수, 시작~끝 연도)을 찾는다.
    이미 있는 team_name 연속 재직 정보만 스캔한다 — 새 데이터 없음."""
    narrative = [s for s in seasons if s.get("team_name")]
    if not narrative:
        return None
    best = None
    cur_team, cur_start = None, None
    prev = None
    for s in narrative:
        t = s.get("team_name", "")
        if t != cur_team:
            if cur_team is not None:
                length = prev.get("start_year", 0) - cur_start + 1
                if best is None or length > best[2]:
                    best = (cur_team, cur_start, length, prev.get("start_year", 0))
            cur_team, cur_start = t, s.get("start_year", 0)
        prev = s
    if cur_team is not None:
        length = prev.get("start_year", 0) - cur_start + 1
        if best is None or length > best[2]:
            best = (cur_team, cur_start, length, prev.get("start_year", 0))
    return best  # (team, start_year, n_seasons, end_year)


_POSITION_EPILOGUE_FRAME = {
    "GK": "누구보다 든든하게 골문을 지킨 골키퍼",
    "DF": "묵묵히 자신의 자리를 지킨 수비수",
    "MF": "경기의 흐름을 조율한 미드필더",
    "FW": "끝까지 골을 쫓았던 공격수",
}

EPILOGUE_CLOSERS_TROPHY = [
    "우승의 순간들과 함께, 그는 자신의 자리에서 늘 제 몫을 해낸 선수로 기억될 것이다.",
    "화려함보다 꾸준함 — 그것이 {name}이라는 선수의 커리어를 가장 잘 설명하는 말이었다.",
    "트로피를 들어 올린 순간들도, 그렇지 못했던 시즌들도, 결국 같은 한 사람의 커리어였다.",
]
EPILOGUE_CLOSERS_NO_TROPHY = [
    "화려한 우승 트로피가 커리어를 채우진 않았지만, 그가 남긴 것은 어떤 무대에서도 흔들리지 않았던 꾸준함이었다.",
    "정상에 서진 못했지만, 그는 어느 팀에서든 감독이 믿고 맡길 수 있는 선수였다.",
    "우승이라는 결과보다, 끝까지 자리를 지켜낸 과정이 {name}의 커리어를 설명한다.",
]


def generate_epilogue(player, seasons, has_trophy, awards, intl_trophies, rng):
    name = player.get("name", "선수")
    total_m = player.get("total_matches", sum(s.get("matches", 0) for s in seasons))
    total_g = player.get("total_goals", sum(s.get("goals", 0) for s in seasons))
    total_a = player.get("total_assists", sum(s.get("assists", 0) for s in seasons))
    total_s = player.get("total_seasons", len(seasons))
    age = player.get("age", 0)
    pos = player.get("position", "")
    grp = POSITION_GROUP.get(pos, "MF")

    lines = [f"{with_josa(name, '은/는')} {age}세, {total_s}시즌 만에 유니폼을 벗었다.",
             f"통산 {total_m}경기 {total_g}골 {total_a}도움."]

    # [2026-08 신설, 신민용 요청: "커리어에 레드카드 기록 추가"] 통산
    # 레드카드가 한 번이라도 있으면 에필로그에 한 줄 남긴다 — 새로운
    # 사실을 지어내지 않고 이미 my_player에 누적된 값(total_red_cards_all,
    # 리그+컵+챔스+클럽월드컵+국가대표+승강PO 전 대회 합산)을 그대로 쓴다.
    total_rc = player.get("total_red_cards_all", 0)
    if total_rc > 0:
        lines.append(f"그라운드 위에서 뜨거웠던 만큼, 레드카드도 통산 {total_rc}회 받았다.")

    if awards:
        from constants import normalize_award_bucket
        cnt = Counter(normalize_award_bucket(a.get("award_type", "")) for a in awards)
        order = ["발롱도르", "MVP", "득점왕", "도움왕", "베스트11", "골든글러브", "영플레이어",
                 "올해의 수비수", "구단 올해의 선수",
                 "FIFA 푸스카스상", "대회 최고의 골", "리그 올해의 골"]
        parts = [f"{k} {cnt[k]}회" for k in order if cnt.get(k)]
        if parts:
            lines.append("개인 수상: " + " · ".join(parts) + ".")

    # [2026-07 신설, 신민용 리포트: "에필로그가 너무 짧다 — 21시즌을
    # 읽고 마지막이 숫자로만 끝난다"] 이미 계산 가능한 데이터(최장 재직
    # 팀, 거쳐간 팀 수, 포지션)를 한두 문장 더 얹어서 숫자 나열 이상의
    # 여운을 남긴다. 새로운 사실을 지어내지 않고, 이미 있는 season 데이터
    # 에서 직접 뽑는다.
    teams = [s.get("team_name", "") for s in seasons if s.get("team_name")]
    n_teams = len(set(teams))
    longest = _longest_stint(seasons)
    if longest and longest[2] >= 3:
        lt_team, lt_start, lt_n, lt_end = longest
        if n_teams > 1:
            lines.append(f"{n_teams}개 팀을 거치는 동안, 그중 가장 오래 몸담은 곳은 "
                          f"{lt_n}시즌({lt_start}~{lt_end})을 함께한 {with_josa(lt_team, '이었다/였다')}.")
    elif n_teams > 1:
        lines.append(f"{n_teams}개 팀의 유니폼을 입으며 커리어를 이어갔다.")

    frame = _POSITION_EPILOGUE_FRAME.get(grp)
    if frame:
        lines.append(f"화려한 스포트라이트를 받는 자리는 아니었지만, {with_josa(name, '은/는')} "
                      f"{with_josa(frame, '이었다/였다')}.")

    intl_called = any(t.get("league_name", "") not in _INTL_MISS_RESULTS for t in intl_trophies)
    if intl_trophies:
        if intl_called:
            lines.append("국가대표팀의 부름에도 응답하며 클럽을 넘어선 발자취를 남겼다.")
        else:
            lines.append("리그에서는 꾸준한 활약을 이어갔지만, 국가대표팀의 벽은 끝내 넘지 못했다.")

    bank = EPILOGUE_CLOSERS_TROPHY if has_trophy else EPILOGUE_CLOSERS_NO_TROPHY
    closer = rng.choice(bank).format(name=name)
    lines.append(closer)
    return "\n".join(lines)


def _compute_rival_team(seasons, match_rows, min_matches=3):
    """커리어 전체에서 가장 자주 맞붙은 상대팀을 찾는다. match_rows가
    없거나(구버전) 뚜렷한 상대가 없으면(min_matches 미만) None."""
    if not match_rows:
        return None
    own_teams = {s.get("team_name", "") for s in seasons if s.get("team_name")}
    counts = {}
    for m in match_rows:
        home, away = m.get("home_name", ""), m.get("away_name", "")
        opp = None
        if home in own_teams and away not in own_teams:
            opp = away
        elif away in own_teams and home not in own_teams:
            opp = home
        if opp:
            counts[opp] = counts.get(opp, 0) + 1
    if not counts:
        return None
    rival, n = max(counts.items(), key=lambda kv: kv[1])
    return (rival, n) if n >= min_matches else None


def generate_legacy_section(player, seasons, chapters, has_trophy, awards, home_country, rng,
                             match_rows=None, intl_matches=None):
    """[2026-07 신설, 리뷰 피드백: "선수 서사 키워드/그는 어떤 선수였는가
    섹션이 있으면 좋겠다"] 지어낸 사건 없이, 이미 계산해둔 데이터(최고
    평점 시즌, 거쳐간 국가 수, 트로피/개인상 유무, 라이벌 팀)만으로
    커리어 전체를 한 문단으로 요약한다.

    intl_matches: [2026-08 신설, PHASE 2: 상대 재평가] get_my_intl_matches()
    +get_my_qual_matches() 결과를 합쳐서 넘기면, opponent_context.py가
    "당시엔 그냥 한 경기였지만 그 상대가 나중에 그 대회에서 우승/준우승/
    4강까지 갔다" 같은 문장을 최대 2개까지 덧붙인다. None이면(구버전
    호출 등) 이 부분은 그냥 생략된다."""
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

    rival = _compute_rival_team(seasons, match_rows)
    if rival:
        rname, rn = rival
        lines.append(f"특히 {with_josa(rname, '을/를')} 상대로 유독 자주 맞부딪히며(통산 {rn}경기) "
                      "남다른 인연을 쌓았다.")

    if intl_matches:
        from opponent_context import build_opponent_context_sentences
        lines.extend(build_opponent_context_sentences(rng, intl_matches, limit=2))

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 7. Editorial Pass — 문장 흐름 후처리
# ══════════════════════════════════════════════════════════════════
# [2026-07 신설, S급 개선 3] 여러 문장 뱅크에서 각자 독립적으로 고른
# 문장들이 우연히 같은 접속사/도입구로 연달아 시작하는 경우가 생긴다
# (예: "이 시즌..." 문장 바로 다음에 다른 뱅크에서 고른 "이 시즌..."
# 문장이 또 나오는 식). 분량을 늘릴수록(S급 개선 1·2로 문장이 늘어날수록)
# 이 반복은 더 눈에 띄게 된다. 여기서는 절대 내용(사실/숫자/순서)을
# 건드리지 않는다 — 이미 조립된 최종 텍스트를 문단 단위로 훑어서, 바로
# 인접한 두 문장이 완전히 같은 접속사로 시작할 때만 뒤 문장의 접속사를
# 완곡한 표현으로 바꾸거나(뜻은 유지) 지운다. 챕터 제목·섹션 헤더 줄은
# 건드리지 않는다.

# 접속어 뒤에 조사(은/는/이/가/의/도/에서/을/를/엔)가 바로 붙어 나오는
# 경우가 많아서(예: "이 시기의 정점은...", "이 시즌엔 부상으로...") 문자열
# prefix 비교만 하면 조사만 뚝 떼어낸 뒤 지우면서 "그 무렵 는 ..." /
# "그해 엔 ..." 처럼 조사가 덜렁 남는 문제가 생긴다. [2026-07 버그수정,
# 신민용 리포트: "그해 엔 부상으로..."] "엔"(에는의 축약형)이 조사
# 목록에 없어서, "이 시즌"+"엔"이 만나면 "엔"만 안 지워지고 남았다.
# 정규식으로 (접속어)+(조사, optional)+공백까지 한 번에 매치해서 그
# 구간 전체를 통째로 교체한다.
_CONNECTOR_RE = re.compile(
    r'^(돌이켜보면|그러나|하지만|그런데|그리고|동시에|특히|이후|이 시즌|이 시기|이 무렵)'
    r'(?:은|는|이|가|의|도|에서|을|를|엔)?\s*'
)
# 반복될 때 대신 쓸 완곡한 표현 — 뜻은 유지하되 접속만 순화한다.
# 빈 문자열이면 접속어 자체를 지운다(뒤 문장이 그대로 이어지도록).
_CONNECTOR_FALLBACK = {
    "돌이켜보면": "지나고 보면", "그러나": "다만", "하지만": "다만", "그런데": "다만",
    "그리고": "", "동시에": "여기에", "특히": "무엇보다",
    "이후": "그다음", "이 시즌": "그해", "이 시기": "그 무렵", "이 무렵": "그즈음",
}

_SENT_SPLIT_RE = re.compile(r'(?<=다\.)\s+')
_SECTION_HEADER_RE = re.compile(r'^(\d+부\s*—|그는 어떤 선수였는가|에필로그|프롤로그)')
_MULTI_SPACE_RE = re.compile(r'[ \t]{2,}')


def _split_sentences(paragraph):
    """'다. ' 뒤에서만 문장을 자른다 — '평점 8.5'처럼 소수점 뒤에 오는
    마침표는 '다'로 끝나지 않으므로 잘못 잘릴 위험이 없다."""
    return [p for p in _SENT_SPLIT_RE.split(paragraph.strip()) if p]


def _dedupe_exact_repeats(sentences):
    """완전히 동일한 문장이 바로 인접해서 두 번 나오면(안전장치, 정상
    경로에서는 거의 발생하지 않음) 뒤의 것을 제거한다."""
    out = []
    prev = None
    for sent in sentences:
        if sent == prev:
            continue
        out.append(sent)
        prev = sent
    return out


def _dedupe_leading_connectors(sentences):
    """바로 인접한 두 문장이 같은 접속어(조사 포함)로 시작할 때만 뒤
    문장의 접속어 구간 전체를 완곡한 표현으로 바꾼다. 한 칸 건너 반복되는
    것은(진짜 '연속'이 아니므로) 건드리지 않는다."""
    out = []
    prev_base = None
    for sent in sentences:
        m = _CONNECTOR_RE.match(sent)
        cur_base = m.group(1) if m else None
        if cur_base and cur_base == prev_base:
            fallback = _CONNECTOR_FALLBACK.get(cur_base, "")
            rest = sent[m.end():]
            sent = f"{fallback} {rest}".strip() if fallback else rest
        out.append(sent)
        prev_base = cur_base
    return out


def editorial_pass(text):
    """생성된 스토리 전체를 한 번 훑어 흐름만 다듬는다. 문단(빈 줄로
    구분된 시즌/챕터 단락) 단위로 처리하며, 챕터 제목·섹션 헤더 줄과
    문장이 1개뿐인 문단(회고 한 줄 등)은 그대로 둔다. 마지막으로 연속된
    공백(예: 본문과 extras 사이의 이중 공백)만 한 칸으로 정리한다 —
    줄바꿈(챕터/문단 구조)은 건드리지 않는다."""
    paragraphs = text.split("\n\n")
    fixed = []
    for para in paragraphs:
        stripped = para.strip()
        if not stripped or _SECTION_HEADER_RE.match(stripped):
            fixed.append(para)
            continue
        sentences = _split_sentences(para)
        if len(sentences) < 2:
            fixed.append(para)
            continue
        sentences = _dedupe_exact_repeats(sentences)
        sentences = _dedupe_leading_connectors(sentences)
        fixed.append(" ".join(sentences))
    result = "\n\n".join(fixed)
    return _MULTI_SPACE_RE.sub(" ", result)


def generate_story(player, entries, trophies, awards, promos=None, intl_trophies=None, seed=None,
                    match_rows=None, absence_events=None, intl_matches=None, league_matches=None):
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
      match_rows    : [2026-07 신설, 커리어 메모리] match_details 테이블
                      전체 리스트(경기 단위 평점/골/도움/상대팀). 없어도
                      (None) 나머지 스토리 생성은 그대로 동작 — 명장면·
                      해트트릭 언급만 빠진다.
      absence_events: [2026-07 신설] cup_engine.get_my_cup_matches() /
                      champions_engine.get_my_cl_matches() /
                      intl_engine.get_my_intl_matches() 등에서 뽑은
                      {"year": int, "reason": "injury"|"suspension"|...}
                      리스트. 부상(injury)만 확정 근거로 반영한다 — 없으면
                      (None) 예전처럼 통계적 추정으로만 부상을 다룬다.
      intl_matches  : [2026-08 신설, PHASE 2: opponent_context_engine]
                      intl_engine.get_my_intl_matches()+get_my_qual_matches()
                      를 합친 리스트(tournament_id 포함). 있으면 "그는 어떤
                      선수였는가" 섹션에 "당시엔 그냥 한 경기였는데 그
                      상대가 나중에 그 대회에서 우승/준우승/4강까지
                      갔다"는 문장이 최대 2개까지 붙는다. 없으면(None)
                      이 부분은 생략된다(하위호환).
      league_matches: [2026-08 신설, PHASE 5: 경기 묶음 서술]
                      game_engine.get_my_league_matches() 결과. 있으면
                      시즌 문단마다 실제 연승/연패/로테이션(벤치 연속)/
                      결장(부상·징계로 스쿼드 자체에서 빠짐) 구간을 실제
                      상대·날짜·스코어로 인용해 덧붙인다 — "경기를 덜
                      뛰는 게 부상 때문인지 로테이션 때문인지"가 실제
                      데이터(played/benched)로 구분되어 나온다. 없으면
                      (None) 이 부분은 생략된다(하위호환).
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

    trophy_years, trophy_team_years, cup_by_year, cl_by_year = _build_trophy_maps(trophies, seasons=seasons)
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
    home_country = player.get("nationality", "") or player.get("origin_nat", "")
    template_tracker = {}

    # [2026-07 버그수정, 신민용 리포트: "2002년 삼성 FC가 실제로는 강등
    # 안 했는데 스토리엔 강등했다고 나온다"] promos 파라미터는 이미
    # 존재했지만 실제로는 어디서도 쓰이지 않는 죽은 값이었다 — CAT_
    # RELEGATION 판정이 순위 비율만으로 추측되고 있었다. game_engine.
    # get_my_promotions()(실제 promotion_log 기반 승강 기록)에서 만든
    # (연도, 팀명) 집합을 이제 classify_season에 실제로 넘긴다.
    relegation_years = None
    if promos is not None:
        relegation_years = {
            (p.get("year"), p.get("team_name", ""))
            for p in promos
            if (p.get("to_tier", 0) or 0) > (p.get("from_tier", 0) or 0)
        }

    # 챕터별 시즌 유형(카테고리)을 미리 한 번 계산해둔다 — 챕터 제목
    # 판정(_chapter_character)과 본문 렌더링이 같은 분류 결과를 쓰도록.
    all_categories = [
        classify_season(seasons, i, awards_by_year, trophy_years,
                         player.get("age", 0), player_age_at, home_country=home_country,
                         trophy_team_years=trophy_team_years, relegation_years=relegation_years)
        for i in range(len(seasons))
    ]

    # [2026-07 신설] 커리어 메모리 — 사실/이벤트/분석/부상추정을 한 번에
    # 만들어두고, Story Arc Builder로 챕터 경계를 정한다(팀 재직 기준의
    # group_eras 대신 전환점 기준으로).
    memory = build_career_memory(seasons, awards_by_year, trophy_years, intl_by_year,
                                  awards, intl_trophies, home_country, match_rows=match_rows,
                                  absence_events=absence_events, categories=all_categories)
    # [2026-08 신설, PHASE 5] (팀명,연도)별 연승/연패/로테이션/결장 구간
    # 문장을 미리 계산해 memory에 실어둔다 — render_chapter가 시즌 문단
    # 뒤에 바로 이어붙인다.
    memory["match_narratives"] = build_season_match_narratives(league_matches)
    # [2026-08 신설] 이적 시 팀 수준(부수/연봉) 비교 문장도 같은 방식으로
    # 미리 계산해 memory에 실어둔다.
    memory["transfer_narratives"] = build_season_transfer_narratives(seasons)
    chapters = build_story_arcs(seasons, all_categories, awards_by_year, trophy_years, intl_by_year)

    # [2026-07 신설, 분량 확장] 전환점 인덱스를 한 번만 계산해서 시즌
    # 렌더링(브리지 문장, Probe density) 전체에서 재사용한다 — 챕터
    # 경계 계산에 쓴 것과 동일한 계산이라 새로 만들 게 없다.
    turning_indices = set(detect_turning_points(
        seasons, all_categories, awards_by_year, trophy_years, intl_by_year))

    out = [generate_prologue(player, seasons, has_trophy, awards, rng,
                              trajectory=analyze_starting_trajectory(match_rows)), ""]

    for i, chap in enumerate(chapters):
        chap_categories = [all_categories[idx_offset_map[id(s)]] for s in chap]
        is_first_chapter = (i == 0)
        is_last_chapter = (i == len(chapters) - 1)
        chap_character = _chapter_character(chap, chap_categories, is_first_chapter, is_last_chapter)
        title, yr = _chapter_title(chap, chap_categories, is_first_chapter, is_last_chapter, rng,
                                    tracker=template_tracker)
        num = CHAPTER_NUMS[i] if i < len(CHAPTER_NUMS) else f"{i+1}부"
        out.append(f"{num} — {title} {yr}")
        out.append("")
        out.append(render_chapter(rng, chap, seasons, awards_by_year, trophy_years,
                                   player.get("age", 0), player_age_at, idx_offset_map,
                                   home_country=home_country, cup_by_year=cup_by_year,
                                   cl_by_year=cl_by_year, intl_by_year=intl_by_year,
                                   template_tracker=template_tracker, player=player,
                                   memory=memory, chapter_character=chap_character,
                                   categories=all_categories, turning_indices=turning_indices))
        out.append("")

    out.append("그는 어떤 선수였는가")
    out.append("")
    out.append(generate_legacy_section(player, seasons, chapters, has_trophy, awards, home_country, rng,
                                        match_rows=match_rows, intl_matches=intl_matches))
    out.append("")

    out.append("에필로그")
    out.append("")
    out.append(generate_epilogue(player, seasons, has_trophy, awards, intl_trophies, rng))

    # [2026-07 신설, S급 개선 3] Editorial Pass — 내용은 그대로, 인접
    # 문장의 접속어 반복만 완화한다.
    return editorial_pass("\n".join(out))