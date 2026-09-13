# -*- coding: utf-8 -*-
"""match_flow.py — 경기 "포제션 로그" 생성기.

## 배경 (왜 이 파일이 필요한가)

예전 구조: `game_engine.py`가 최종 스코어 + 팀 통계(team_stats) + 내 개인
이벤트 텍스트만 만들어서 저장하고, `match_sim_viewer.py`가 재생 시점에
"이랬을 것 같다"를 사후 추측으로 채워 넣었다. 이 둘이 완전히 분리돼 있어서
구조적으로 계속 어긋났다 — 실제로 반복됐던 문제들:
  * 파울이 나면 "누가 반칙했는지" 텍스트를 안 보고 공 위치로 재개팀을
    추측 → 방향이 뒤죽박죽
  * team_stats["shots"]=13인데 실제 재생 화면엔 2~3번만 슈팅 장면이 나옴
  * 코너킥 개수가 통계랑 화면이 안 맞음

이 모듈은 경기를 시뮬레이션하는 바로 그 순간(`_derive_match_stats()` 직후)에
"언제 어느 팀이 어느 구역에서 무슨 상황이었는지"를 담은 코스-그레인
(coarse-grained) 포제션 체인을 team_stats와 함께 만들어서 같이 저장한다.
22명의 실제 좌표까지는 담지 않는다 — 그건 여전히 `match_sim_viewer.py`가
"이 포제션이 슈팅이다/파울이다"라는 사실을 보고 그 순간의 22명 움직임을
그린다(프레서/커버/서포트런/코너 슬롯 등 기존 로직 전부 그대로 재사용).
달라지는 건 딱 하나 — "언제 무슨 일이 있었는지"를 더 이상 뷰어가 추측하지
않고, 이 로그를 그대로 순서대로 따라가기만 하면 된다는 것.

## 핵심 설계 원칙

1. **불변식(반드시 지켜져야 함, 아래 테스트로 검증됨)**:
   - outcome이 슈팅류(goal/save/shot_on/shot_off/shot_blocked)인 레코드
     개수 총합 == team_stats[side]["shots"] (오차 없이 정확히)
   - 그중 "온타깃" 취급(goal + save) 개수 == team_stats[side]["shots_on"]
   - outcome=="goal" 개수 == 그 팀의 실제 득점 수
   - outcome=="corner" 개수 == team_stats[side]["corners"]
   - outcome=="foul" 개수 == team_stats[side]["fouls"]
   - 내 개인 이벤트(정확한 분이 이미 배정된 실제 텍스트)는 반드시 그
     정확한 분에, 정확한 outcome으로 로그에 그대로 존재한다.

2. **`"team"` 필드의 의미는 항상 "그 통계 버킷의 주체"다** — 슛이면 슛한
   팀, 코너면 코너를 얻어서 차는 팀, 파울이면 "파울을 범한" 팀(=
   team_stats[side]["fouls"]가 세는 대상과 동일). 이렇게 통일해두면
   뷰어는 "다음엔 누가 재개하나"를 따로 계산할 필요가 없다 — 로그의
   다음 레코드를 그냥 그대로 재생하면 자동으로 맞다. (예전엔 파울 텍스트
   안의 "우리 팀"/"상대 팀"을 파싱해서 재개팀을 추론해야 했는데, 그
   추론 코드 자체가 버그의 원인이었다 — 이제는 추론이 필요 없다.)

3. **완전히 결정론적** — 같은 입력이면 항상 같은 로그. 전역 `random` 모듈
   상태를 전혀 건드리지 않도록 로컬 `random.Random` 인스턴스만 쓴다
   (게임 엔진의 다른 난수 소비 순서에 영향을 주지 않기 위함).

## 사용법

    from match_sim.match_flow import generate_possession_log
    log = generate_possession_log(is_home, team_stats, timed_events,
                                   my_score, opp_score)
    # log를 JSON으로 직렬화해서 match_details.possession_log 컬럼에 저장

`match_sim_viewer.py`는 이 로그가 있으면(신규 경기) 그대로 재생하고,
없으면(구버전 세이브로 저장된 옛날 경기) 기존 사후-추측 로직으로
자동 폴백한다 — 하위호환 보장.
"""

import hashlib
import random

# [일관성] match_sim_viewer.py의 _classify_event가 쓰는 마커 상수와 완전히
# 동일하다 — 두 파일이 서로 다른 기준으로 텍스트를 판정하면 다시 어긋나기
# 때문에, 이 목록이 유일한 출처(source of truth)다.
_GOAL_MARKERS = ("⚽", "🎯 페널티킥 골", "세트피스", "프리킥 골")
_CONCEDE_MARKERS = ("🥅",)
_SAVE_MARKERS = ("🧤",)
_MISS_MARKERS = ("페널티킥 실축", "🚫")
_FOUL_MARKERS = ("파울",)
_CORNER_MARKERS = ("코너킥",)

# 포제션 로그 스키마 버전 — 나중에 필드를 추가/변경하면 올린다. 뷰어는
# 모르는 버전이면 안전하게 사후-추측 폴백으로 넘어갈 수 있다.
SCHEMA_VERSION = 1

# [신규] 뷰어(match_sim_viewer.py)가 움직임에 바로 쓸 수 있는 최소 스탯
# 집합. ai_players 테이블엔 더 많은 컬럼이 있지만, 지금 당장 22명 움직임
# 로직이 실제로 소비할 수 있는 것만 추린다 — 안 쓰는 스탯까지 다 저장해
# 봐야 사이즈만 커지고 아무 의미 없다.
_LINEUP_STAT_KEYS = ("speed", "dribbling", "tackling", "positioning",
                     "jump", "heading", "stamina")


def _stable_seed(*parts):
    """[안정 시드] match_sim_viewer.py의 동명 함수와 완전히 동일한 알고리즘
    (md5 기반)이다 — 팀의 포메이션이 DB에 없어서 폴백을 써야 할 때, 뷰어가
    재생 시점에 찾는 포메이션과 반드시 똑같은 값이 나와야 한다. 여기서
    다른 해시를 쓰면 "라인업은 4-3-3 기준으로 뽑았는데 재생은 4-2-3-1로
    그려서 슬롯이 하나도 안 맞는" 사고가 난다."""
    key_str = "|".join(str(p) for p in parts)
    digest = hashlib.md5(key_str.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _lookup_team(name):
    """팀 이름 → (team_id, formation). match_sim_viewer.py의
    _lookup_formation과 동일한 폴백 규칙(DB에 없으면 이름 해시로 결정론적
    선택)을 그대로 따른다."""
    team_id, formation = None, None
    try:
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT id, formation FROM teams WHERE name=? LIMIT 1", (name,)).fetchone()
        conn.close()
        if row:
            team_id = row["id"]
            formation = row["formation"] or None
    except Exception:
        pass
    if not formation:
        formation = ["4-4-2", "4-3-3", "4-2-3-1"][_stable_seed(name) % 3]
    return team_id, formation


def _fetch_roster(team_id):
    if team_id is None:
        return []
    try:
        from database import get_conn
        conn = get_conn()
        rows = conn.execute("SELECT * FROM ai_players WHERE team_id=?", (team_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# [2026-09 신설, 신민용 리포트: "17살 91 OVR 유망주가 빅클럽 주전으로
# 뜬다"] 헤드리스 96팀 실측(빅5리그 1부 전체) 결과 93%(89/96)팀이 스쿼드
# top11 평균보다 5점 이상 낮은 선수를 실제 선발로 썼고, 56%(54/96)팀은
# 19세 이하를 선발에 포함했다 — 원인은 옛 _rank가 (포지션적합도, -OVR)
# 튜플로 완전 사전식 정렬돼 있어서, 포지션 적합도가 1순위 절대 기준이고
# OVR은 "같은 적합도 등급 안에서만" 타이브레이커였기 때문. 그 슬롯에 꼭
# 맞는 포지션이 어린 유망주 한 명뿐이면, 인접 포지션에 OVR 97짜리
# 베테랑이 있어도 절대 안 쓰였다(적합도 등급을 건너뛰는 트레이드오프 자체가
# 없었음). 아래 두 상수로 "포지션 적합도 + OVR + 나이"를 하나의 점수로
# 합쳐서, OVR 격차가 충분히 크면 적합도 열세를 뒤집을 수 있게 한다.
#
# [기존 POSITION_MISMATCH_PENALTY(0.05~0.15)를 재사용하지 않는 이유]
# 그 상수는 my_player의 경기당 실질 OVR을 아주 살짝만 깎는 용도로 튜닝된
# 값(주석상 "-0.5~-5.0의 작은 절대 차감")이라, 여기서처럼 "포지션 적합도
# vs 실제 실력"을 맞먹는 저울에 올리기엔 너무 작다(그대로 쓰면 사실상
# 무의미). 실제 OVR 스케일(점 단위)로 새로 잡는다.
_LINEUP_POSITION_PENALTY = [0, 5, 9, 13, 13]  # idx 0(완벽)~4(카테고리 폴백/완전 불일치)


def _lineup_age_penalty(age):
    """어린 유망주가 '포지션이 정확하다'는 이유 하나로 베테랑보다 과도하게
    선발되는 문제 대응 — OVR 자체를 깎는 게 아니라 "이 선수를 선발로 쓸 때의
    실질 경쟁력"만 낮춘다(연봉/훈련/성장 등 다른 계산엔 전혀 영향 없음,
    age 컬럼도 그대로). 18세 이하 -5 / 20세 이하 -3 / 22세 이하 -1 / 그 외 0."""
    if age is None:
        return 0
    if age <= 18:
        return 5
    if age <= 20:
        return 3
    if age <= 22:
        return 1
    return 0


def _select_lineup(team_id, formation):
    """그 팀 로스터에서 포메이션 슬롯 순서대로 11명을 뽑는다.
    POSITION_COMPAT(선수 등록 포지션 → 배치 가능 슬롯 우선순위)을 "포지션
    적합도 절대 1순위" 규칙이 아니라, 적합도·OVR·나이를 하나의 실질
    경쟁력 점수로 합쳐서 판단한다 — 적합도가 살짝 밀려도 실력 격차가
    충분히 크면(또는 상대가 너무 어리면) 뒤집힐 수 있다.

    [GK는 별도 처리] 위 점수제를 GK에도 그대로 적용하면 '포지션 폴백
    페널티가 primary position의 compat 리스트 길이에 좌우되던 옛 버그'
    (GK는 compat 길이가 1이라 폴백 idx가 낮게 나와, 벤치 GK가 엉뚱하게
    RM 같은 필드 슬롯에 배정되는 사례가 실측에서 확인됨)와는 무관하게도,
    골키퍼는 필드 플레이어와 아예 다른 스탯 체계라 실제 축구에서 절대
    바꿔 쓰지 않는 포지션이다 — GK 슬롯은 GK 등록 선수만, 필드 슬롯은
    GK 등록 선수를 제외하고 고른다(둘 다 후보가 없을 때만 예외적으로
    전체 풀로 폴백해 빈 자리를 막는다)."""
    from constants import FORMATION_SLOTS, POSITION_COMPAT
    slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
    roster = _fetch_roster(team_id)
    used_ids = set()
    lineup = []
    for slot in slots:
        if slot == "GK":
            pool = [p for p in roster if p.get("id") not in used_ids and p.get("position") == "GK"]
        else:
            pool = [p for p in roster if p.get("id") not in used_ids and p.get("position") != "GK"]
        if not pool:
            pool = [p for p in roster if p.get("id") not in used_ids]
        if not pool:
            lineup.append(None)
            continue

        def _score(p):
            compat = POSITION_COMPAT.get(p.get("position"), [p.get("position")])
            try:
                idx = compat.index(slot)
            except ValueError:
                idx = len(_LINEUP_POSITION_PENALTY) - 1
            idx = min(idx, len(_LINEUP_POSITION_PENALTY) - 1)
            eff = (p.get("ovr") or 50) - _LINEUP_POSITION_PENALTY[idx] - _lineup_age_penalty(p.get("age"))
            return -eff  # 오름차순 정렬 → 실질 경쟁력이 높은 순

        pool.sort(key=_score)
        best = pool[0]
        used_ids.add(best.get("id"))
        lineup.append(best)
    return lineup


def generate_lineup_stats(home_name, away_name):
    """홈/원정팀 로스터에서 포메이션에 맞는 11명을 선발하고, 움직임에 바로
    쓸 최소 스탯만 뽑아 반환한다.

    반환: {"home": [stat_dict 또는 None, ...11개], "away": [...]}
    포메이션 슬롯과 같은 순서이므로, match_sim_viewer.py의
    home_players[i]/away_players[i]와 인덱스가 그대로 대응한다 — 둘 다
    같은 FORMATION_SLOTS[formation]을 같은 순서로 쓰기 때문이다(뷰어의
    layout_formation도 이 슬롯 리스트를 그대로 enumerate해서 좌표를
    만든다). 로스터가 없거나(팀 데이터 없음) 자리가 안 채워지면 그
    슬롯은 None — 뷰어가 기본값(평균치)으로 안전하게 폴백한다.
    """
    result = {}
    for side, name in (("home", home_name), ("away", away_name)):
        team_id, formation = _lookup_team(name)
        lineup = _select_lineup(team_id, formation)
        entries = []
        for p in lineup:
            if p is None:
                entries.append(None)
            else:
                entries.append({k: p.get(k, 50) for k in _LINEUP_STAT_KEYS})
        result[side] = entries
    return result


def _classify_personal(text):
    """개인 이벤트 텍스트 한 줄 → 포제션 outcome 종류. 못 알아보면 None."""
    if any(m in text for m in _MISS_MARKERS):
        return "miss_for"
    if any(m in text for m in _GOAL_MARKERS):
        return "goal_for"
    if any(m in text for m in _CONCEDE_MARKERS):
        return "goal_against"
    if any(m in text for m in _SAVE_MARKERS):
        return "save"
    if any(m in text for m in _FOUL_MARKERS):
        return "foul"
    if any(m in text for m in _CORNER_MARKERS):
        return "corner"
    return None


def _make_rng(seed, events, my_score, opp_score):
    if seed is not None:
        return random.Random(seed)
    # events(텍스트)+스코어로부터 결정론적 시드를 유도한다 — 같은 경기
    # 데이터면 항상 같은 로그가 나오게(재현성), 그러면서도 게임 엔진의
    # 전역 random 상태와는 완전히 분리되게.
    key = "|".join(f"{m}:{t}" for m, t in events) + f"|{my_score}-{opp_score}"
    # [2026-09 재현성 버그수정] 내장 hash()는 문자열에 대해 PYTHONHASHSEED로
    # 랜덤화되므로 프로세스마다 값이 달라진다 — 위 주석이 말하는 "같은 경기
    # 데이터면 항상 같은 로그"가 실제로는 한 프로세스 안에서만 성립했다.
    # 시드에 무관하게 항상 같은 값을 주는 crc32로 교체(분포는 동일).
    import zlib
    return random.Random(zlib.crc32(key.encode("utf-8")) & 0xffffffff)


def generate_possession_log(is_home, team_stats, timed_events, my_score, opp_score):
    """[구현부 — 이 파일 맨 위 모듈 독스트링에서 이미 계약을 문서화해뒀지만
    실제 함수 본체가 빠져 있었다. game_engine._save_match_detail이
    engine_plog 없이(=새 전술 엔진을 안 쓰는 챔스/컵/국제대회/승강
    플레이오프 등) 이 함수를 직접 호출하는데, 정의가 없어서 항상
    AttributeError로 크래시했다.]

    tactical_engine.simulate_tactical_match이 만드는 진짜 시뮬레이션
    plog와 완전히 같은 레코드 스키마({"min","team","zone","lane","outcome",
    "me","text"})를 쓴다.

    설계:
      1. team_stats에서 사이드별 목표 개수(골/선방/빗나감/코너/파울)를
         뽑아낸다. 슈팅=온타깃+빗나감, 골=min(온타깃, 실제 스코어)로
         고정해서 "슈팅류 총합==shots", "온타깃(goal+save)==shots_on",
         "골 개수==실제 득점 수" 세 불변식이 항상 정확히 맞는다
         (game_engine._derive_match_stats가 이미 shots>=shots_on>=score를
         보장하므로 음수가 나올 일이 없다).
      2. 90분을 1분 단위로 돌면서 그 분의 점유팀을 poss(점유율) 가중치로
         뽑고, 그 팀에 아직 안 배정한 목표 이벤트가 남아있으면 그걸 쓰고
         없으면 "buildup"(특별한 일 없는 필러)을 채운다 — 로그 길이가
         항상 90 근처가 되어 뷰어가 끊김 없이 재생할 수 있다. 아주 드물게
         목표 이벤트 합이 90분보다 많으면(양 팀 슈팅+코너+파울 합이 90
         초과) 남는 만큼 경기 후반부에 이어 붙인다 — "총합 일치"가
         "정확히 90개"보다 우선하는 불변식이라서다.
      3. 실제 개인 이벤트(골/도움/실점/선방/파울/코너 텍스트, 이미 정확한
         분이 배정됨)를 같은 team+outcome 필러 슬롯 중 그 분과 가장 가까운
         것 하나에 병합한다 — tactical_engine.merge_personal_events와 같은
         원칙(발생 분은 그대로 유지, 슬롯의 시각만 그 쪽으로 당겨온다).
         대응하는 필러 슬롯이 하나도 없으면(극단적 케이스) 새 레코드를
         만들어서라도 실제 이벤트는 반드시 로그에 남긴다 — "총합 일치"
         불변식보다 "실제 이벤트는 로그에 존재해야 한다" 불변식을 우선
         한다(merge_personal_events는 반대로 조용히 버리는데, 그쪽은 엔진
         자신이 만든 stats라 애초에 안 맞을 일이 거의 없어서 그래도 되지만
         여긴 stats가 별도 공식으로 만들어지는 폴백 경로라 안전하게 둔다).

    완전히 결정론적 — _make_rng()로 만든 로컬 Random만 쓰고 전역 random
    상태는 건드리지 않는다(같은 스코어·같은 개인 이벤트면 항상 같은 로그).
    """
    my_side = "home" if is_home else "away"
    opp_side = "away" if is_home else "home"
    my_stats = team_stats.get(my_side) or {}
    opp_stats = team_stats.get(opp_side) or {}

    rng = _make_rng(None, timed_events, my_score, opp_score)

    def _side_targets(stats, score):
        shots = max(0, int(stats.get("shots", 0)))
        shots_on = max(0, min(shots, int(stats.get("shots_on", 0))))
        goals = max(0, min(shots_on, int(score)))
        return {
            "goal": goals,
            "save": shots_on - goals,
            "shot_off": shots - shots_on,
            "corner": max(0, int(stats.get("corners", 0))),
            "foul": max(0, int(stats.get("fouls", 0))),
        }

    def _zone_for(outcome):
        if outcome == "foul":
            return "mid"
        if outcome == "buildup":
            return rng.choices(("def", "mid", "att"), weights=(3, 4, 3), k=1)[0]
        return "att"  # goal/save/shot_off/corner

    my_queue = [o for o, n in _side_targets(my_stats, my_score).items() for _ in range(n)]
    opp_queue = [o for o, n in _side_targets(opp_stats, opp_score).items() for _ in range(n)]
    rng.shuffle(my_queue)
    rng.shuffle(opp_queue)

    my_poss_pct = max(1, min(99, int(my_stats.get("poss", 50))))
    total_minutes = 90

    log = []
    for minute in range(1, total_minutes + 1):
        if rng.random() * 100 < my_poss_pct:
            side, queue = my_side, my_queue
        else:
            side, queue = opp_side, opp_queue
        outcome = queue.pop() if queue else "buildup"
        log.append({"min": float(minute), "team": side, "zone": _zone_for(outcome),
                    "lane": rng.choice(("L", "C", "R")), "outcome": outcome,
                    "me": False, "text": None})

    # 90분 배정에서 다 못 채운 목표 이벤트(극단적으로 슈팅+코너+파울 합이
    # 90을 넘는 케이스)는 후반부에 이어 붙여서라도 개수를 맞춘다.
    leftover = [(my_side, o) for o in my_queue] + [(opp_side, o) for o in opp_queue]
    for i, (side, outcome) in enumerate(leftover):
        log.append({"min": total_minutes + (i + 1) * 0.01, "team": side,
                    "zone": _zone_for(outcome), "lane": rng.choice(("L", "C", "R")),
                    "outcome": outcome, "me": False, "text": None})

    # 실제 개인 이벤트 병합 — tactical_engine.merge_personal_events와 동일한
    # 원칙(발생 분은 유지, 같은 team+outcome 필러 슬롯 중 가장 가까운 것
    # 하나만 당겨와서 text/me를 채운다).
    kind_map = {
        "goal_for": (my_side, "goal", True),
        "goal_against": (opp_side, "goal", False),
        "miss_for": (my_side, "save", True),
        "save": (opp_side, "save", True),
    }
    used_idx = set()
    for m, text in timed_events:
        kind = _classify_personal(text)
        if kind in kind_map:
            side, outcome, me_flag = kind_map[kind]
        elif kind == "foul":
            side = my_side if "우리 팀" in text else opp_side
            outcome, me_flag = "foul", False
        elif kind == "corner":
            side = my_side if "우리 팀" in text else opp_side
            outcome, me_flag = "corner", False
        else:
            continue

        candidates = [i for i, r in enumerate(log)
                      if i not in used_idx and r["team"] == side and r["outcome"] == outcome
                      and r["text"] is None]
        if candidates:
            best = min(candidates, key=lambda i: abs(log[i]["min"] - float(m)))
            used_idx.add(best)
            log[best]["min"] = float(m)
            log[best]["text"] = text
            log[best]["me"] = me_flag
        else:
            log.append({"min": float(m), "team": side, "zone": _zone_for(outcome),
                        "lane": rng.choice(("L", "C", "R")), "outcome": outcome,
                        "me": me_flag, "text": text})

    log.sort(key=lambda r: r["min"])
    return log