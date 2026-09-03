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


def _select_lineup(team_id, formation):
    """그 팀 로스터에서 포메이션 슬롯 순서대로 11명을 뽑는다.
    POSITION_COMPAT(선수 등록 포지션 → 배치 가능 슬롯 우선순위)을 그대로
    재사용해서, "이 슬롯에 이 포지션 선수가 얼마나 자연스러운지"를 판단
    한다 — 실제 게임 성과 계산 로직과 같은 기준이라 일관성이 있다."""
    from constants import FORMATION_SLOTS, POSITION_COMPAT
    slots = FORMATION_SLOTS.get(formation, FORMATION_SLOTS["4-4-2"])
    roster = _fetch_roster(team_id)
    used_ids = set()
    lineup = []
    for slot in slots:
        pool = [p for p in roster if p.get("id") not in used_ids]
        if not pool:
            lineup.append(None)
            continue

        def _rank(p):
            compat = POSITION_COMPAT.get(p.get("position"), [p.get("position")])
            try:
                idx = compat.index(slot)
            except ValueError:
                idx = len(compat) + 1
            return (idx, -(p.get("ovr") or 50))

        pool.sort(key=_rank)
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
    return random.Random(hash(key) & 0xffffffff)