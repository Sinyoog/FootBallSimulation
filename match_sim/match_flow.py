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


def generate_possession_log(is_home, team_stats, events, my_score, opp_score,
                             seed=None):
    """경기 하나의 포제션 로그를 만든다.

    Args:
        is_home: 내 팀이 홈인지.
        team_stats: `_derive_match_stats()`가 만든
            {"home": {...}, "away": {...}} (각각 poss/shots/shots_on/
            corners/fouls/pass_acc).
        events: [(분, 텍스트), ...] — 이미 시간순 정렬/분 배정이 끝난
            원본 이벤트 목록(`_save_match_detail`의 `timed`를 그대로 넘김).
        my_score, opp_score: 최종 스코어(득점 레코드 개수 검증용).
        seed: 명시하면 그 시드로, 아니면 events+스코어에서 결정론적으로 유도.

    Returns:
        분(min) 순 정렬된 레코드 리스트. 각 레코드:
            {"min": float, "team": "home"|"away", "zone": "def"|"mid"|"att",
             "outcome": str, "me": bool, "text": str|None}
        team_stats가 비어있으면(구버전 데이터 등) 빈 리스트를 반환한다.
    """
    if not team_stats:
        return []

    my_side = "home" if is_home else "away"
    opp_side = "away" if is_home else "home"
    events = [(float(m), str(t)) for m, t in events]
    rng = _make_rng(seed, events, my_score, opp_score)

    log = []
    used_minutes = set()
    _LO, _HI = 1.5, 89.5

    def _zone_for(outcome):
        if outcome in ("goal", "save", "shot_on", "shot_off", "shot_blocked", "corner"):
            return "att"
        if outcome == "foul":
            return rng.choices(["def", "mid", "att"], weights=[3, 5, 2])[0]
        return rng.choices(["def", "mid", "att"], weights=[3, 4, 3])[0]

    def _rough_spread(n, min_gap):
        """[1단계: 대략적인 분산] [1.5, 89.5] 구간을 n등분한 격자에 지터를
        줘서 대략 고르게 퍼뜨린다. 완벽한 간격 보장은 이 함수의 목표가
        아니다(아래 _enforce_spacing이 마지막에 수학적으로 보장한다) —
        여기서는 그냥 "대충 자연스럽게 흩어진 느낌"만 만든다."""
        if n <= 0:
            return []
        span = _HI - _LO
        step = span / n
        jitter = max(0.0, min(step, min_gap) / 2.0)
        out = []
        t = _LO + step / 2.0
        for _ in range(n):
            m = round(t + rng.uniform(-jitter, jitter), 1)
            out.append(max(_LO, min(_HI, m)))
            t += step
        rng.shuffle(out)
        return out

    def _enforce_spacing(minutes, min_gap, protected):
        """[버그 수정 — 근본 원인] 예전엔 "일단 랜덤/격자로 찍고, 겹치면
        몇 번 재시도해서 피해본다"는 확률적 방식이었다. 문제는 이미 채워진
        분이 많아질수록(=경기 통계가 많을수록) 재시도가 실패할 확률이
        올라간다는 것 — 실측 확인됨(파울 처리 직후 코너킥이 같은 표시
        분으로 겹쳐 보인 원인, 심지어 완전히 같은 시각이 두 번 나온
        적도 있었음). 확률에 맡기는 대신, 정렬한 뒤 앞에서부터 훑으며
        "이전 항목·보호 대상(protected, 실제 이벤트나 이미 배치된 더
        중요한 카테고리)과 min_gap 이상 벌어지도록" 순차적으로 강제
        조정한다 — 몇 개가 들어오든 간격이 지켜지는 게 수학적으로
        보장된다(공간이 부족하면 뒤쪽으로 조금씩 밀리기만 할 뿐, 절대
        겹치지 않는다)."""
        if not minutes:
            return []
        order = sorted(range(len(minutes)), key=lambda i: minutes[i])
        protected_sorted = sorted(protected)
        placed = []
        last = _LO - min_gap - 1.0
        pi = 0
        result = [None] * len(minutes)
        for idx in order:
            m = minutes[idx]
            # 이 근처의 protected(고정) 시각을 반영
            while pi < len(protected_sorted) and protected_sorted[pi] < m - min_gap:
                pi += 1
            if pi < len(protected_sorted) and abs(protected_sorted[pi] - m) <= min_gap:
                m = max(m, protected_sorted[pi] + min_gap)
            if m - last < min_gap:
                m = last + min_gap
            m = round(min(94.9, m), 1)
            result[idx] = m
            last = m
            placed.append(m)
        return result

    _MIN_GAP = 1.05      # 이 이상이면 표시되는 정수 분이 절대 안 겹친다(수학적 보장)
    _GOAL_GAP = 3.5       # 골끼리는 훨씬 더 여유 있게(너무 잦은 연속 골 방지)

    # ── 1단계: 실제 개인 이벤트를 정확한 분에 먼저 박아 넣는다. 동시에
    #    사이드별로 "실제로 몇 개나 있었는지"(real_*)를 집계해둔다 —
    #    이 집계값은 뒤에서 team_stats와 비교해 하한선을 정하는 데 쓴다.
    real = {side: {"goal": 0, "save": 0, "foul": 0, "corner": 0} for side in ("home", "away")}

    for m, text in events:
        kind = _classify_personal(text)
        if kind is None:
            continue

        if kind == "goal_for":
            side, outcome = my_side, "goal"
        elif kind == "goal_against":
            side, outcome = opp_side, "goal"
        elif kind == "miss_for":
            # PK 실축·노골도 "골대 안/근처로 간 유효슈팅이 막힌 것" 취급.
            side, outcome = my_side, "save"
        elif kind == "save":
            side, outcome = opp_side, "save"
        elif kind == "foul":
            # "team" = 파울을 범한 팀. 텍스트의 "우리 팀"/"상대 팀"으로
            # 실제 범한 쪽을 그대로 판정한다.
            side, outcome = (my_side if "우리 팀" in text else opp_side), "foul"
        elif kind == "corner":
            # "team" = 코너킥을 얻어서 차는 팀.
            side, outcome = (my_side if "우리 팀" in text else opp_side), "corner"
        else:
            continue

        real[side][outcome] += 1
        log.append({
            "min": m, "team": side, "zone": _zone_for(outcome), "outcome": outcome,
            "me": kind in ("goal_for", "miss_for", "goal_against", "save"),
            "text": text,
        })
        used_minutes.add(m)

    # ── 2단계: team_stats는 "하한선"이다(게임 엔진 자체도 이미 이 철학 —
    #    _derive_match_stats가 detail 기록을 max()로 하한선 보장하는
    #    방식과 동일하다). 실제 개인 이벤트가 team_stats보다 많으면(예: 내
    #    선방이 실제로 3번 있었는데 통계상 shots_on은 1이었던 경우) 실제
    #    쪽을 존중해서 목표치를 끌어올린다 — 절대 실제로 있었던 사건을
    #    누락시키지 않는다.
    #
    # [구조 변경] 예전엔 "필요 개수 계산"과 "분 배정"을 같은 루프에서
    # 한 사이드씩 순서대로 처리했다. 이제는 먼저 양쪽 사이드의 필요
    # 개수를 전부 다 센 다음(분 배정은 아직 안 함), 종류별로 한꺼번에
    # 격자를 깔아서 배정한다.
    #
    # [버그 수정 — 간격 예산을 실제로 중요한 곳에 집중] 처음엔 골/선방/
    # 코너/파울/오프타깃슈팅/빌드업을 전부 똑같이 취급해서 간격을
    # 나눴는데, 뷰어에서 실제로 배너(텍스트+정지)를 띄우는 건 골/선방/
    # 코너/파울뿐이고, 오프타깃 슈팅과 빌드업은 배너 없이 조용히 지나간다
    # ("파울 처리하자마자 바로 코너킥"처럼 화면이 겹쳐 보이는 건 배너끼리
    # 부딪힐 때만 체감된다). 그래서 배너류(goal/save/corner/foul)를
    # "silent"류(shot_off/shot_blocked/buildup)보다 훨씬 넉넉한 간격으로
    # 먼저 배치하고, silent류는 그보다는 좁은 간격으로 나머지 공간에
    # 채운다 — 90분이라는 물리적 한계 안에서 간격 예산을 실제로 눈에
    # 띄는 곳에 우선 배분하는 것.
    scores = {my_side: max(0, int(my_score)), opp_side: max(0, int(opp_score))}
    pending_goal = []      # [side, ...]
    pending_banner = []    # [(side, outcome), ...] — save/corner/foul (배너 O)
    pending_silent = []    # [(side, outcome), ...] — shot_off/shot_blocked (배너 X)

    for side in ("home", "away"):
        st = team_stats.get(side) or {}
        r = real[side]

        target_goal = max(scores.get(side, 0), r["goal"])
        # [버그 수정] target_on은 반드시 (최종 target_goal) + (실제 선방
        # 횟수)를 담을 수 있어야 한다. 예전엔 r["goal"](부풀리기 전 실제
        # 골 수)로만 계산해서, target_goal이 실제 스코어 쪽으로 더 커지면
        # (예: 내가 직접 넣은 골은 1개인데 실제 스코어는 2골) 그 추가된
        # 1골 자리를 담을 공간이 target_on에 없어서 save 채움 개수가
        # 음수로 계산되고, 그만큼 총 슈팅 개수가 어긋났다.
        target_on = max(int(st.get("shots_on", 0) or 0), target_goal + r["save"])
        target_shots = max(int(st.get("shots", 0) or 0), target_on)
        target_corner = max(int(st.get("corners", 0) or 0), r["corner"])
        target_foul = max(int(st.get("fouls", 0) or 0), r["foul"])

        pending_goal += [side] * (target_goal - r["goal"])
        pending_banner += [(side, "save")] * ((target_on - target_goal) - r["save"])
        n_off = target_shots - target_on
        for _ in range(n_off):
            pending_silent.append((side, "shot_blocked" if rng.random() < 0.4 else "shot_off"))
        pending_banner += [(side, "corner")] * (target_corner - r["corner"])
        pending_banner += [(side, "foul")] * (target_foul - r["foul"])

    rng.shuffle(pending_goal)
    rng.shuffle(pending_banner)
    rng.shuffle(pending_silent)

    # [버그 수정] 우선순위가 높은 카테고리(골 → 배너 → 사일런트 순)를
    # 배치할 때마다 그 결과를 "보호 대상"에 누적해서, 다음 카테고리가
    # 이미 정해진 시각과 안 겹치게 한다. _enforce_spacing이 각 단계에서
    # 간격을 수학적으로 보장하므로, 이렇게 순서대로 쌓아 올려도 앞
    # 단계의 배치가 절대 흐트러지지 않는다.
    goal_minutes = _enforce_spacing(
        _rough_spread(len(pending_goal), _GOAL_GAP), _GOAL_GAP, used_minutes)
    for side, m in zip(pending_goal, goal_minutes):
        log.append({"min": m, "team": side, "zone": "att",
                    "outcome": "goal", "me": False, "text": None})
    used_minutes.update(goal_minutes)

    banner_minutes = _enforce_spacing(
        _rough_spread(len(pending_banner), _MIN_GAP), _MIN_GAP, used_minutes)
    for (side, outcome), m in zip(pending_banner, banner_minutes):
        log.append({"min": m, "team": side,
                    "zone": ("att" if outcome != "foul" else _zone_for("foul")),
                    "outcome": outcome, "me": False, "text": None})
    used_minutes.update(banner_minutes)

    # silent류(오프타깃 슈팅)는 배너가 없어 화면이 겹쳐 보일 위험이 훨씬
    # 적으므로, 간격 요구치를 낮춰서(0.4) 남은 공간에 부담 없이 채운다.
    silent_minutes = _enforce_spacing(
        _rough_spread(len(pending_silent), 0.4), 0.4, used_minutes)
    for (side, outcome), m in zip(pending_silent, silent_minutes):
        log.append({"min": m, "team": side, "zone": "att",
                    "outcome": outcome, "me": False, "text": None})
    used_minutes.update(silent_minutes)

    # ── 3단계: 페이싱용 일반 포제션(그냥 공 돌리는 장면)을 최소한만
    #    채워서, 슈팅/코너/파울만 연속으로 튀어나오는 부자연스러움을
    #    줄인다. 점유율(poss%)에 비례해서 팀을 배분한다. ────────────────
    filler_count = max(0, 24 - len(log))
    home_poss = (team_stats.get("home") or {}).get("poss", 50)
    filler_sides = ["home" if rng.random() * 100 < home_poss else "away"
                    for _ in range(filler_count)]
    filler_minutes = _enforce_spacing(
        _rough_spread(filler_count, 0.4), 0.4, used_minutes)
    for side, m in zip(filler_sides, filler_minutes):
        log.append({"min": m, "team": side,
                    "zone": _zone_for("buildup"), "outcome": "buildup",
                    "me": False, "text": None})

    log.sort(key=lambda r: r["min"])

    # [버그 수정 — 최종 안전장치] 위 격자 배치는 대부분의 경기에서 충분히
    # 잘 작동하지만, 7-7처럼 스코어가 크게 벌어지는 극단적인 경기는 실제
    # _derive_match_stats 공식으로도 양팀 합쳐 100개 넘는 레코드가 나올 수
    # 있다(실측 확인됨). 90분짜리 타임라인에 100개를 욱여넣으면 아무리
    # 격자+로컬 보정을 해도 물리적으로 완전히 안 겹치게 만드는 게
    # 불가능한 경우가 생긴다. 마지막에 한 번 더 확실하게 훑으면서 겹치는
    # 시각을 밀어내되, **실제 개인 이벤트(text가 있는 레코드)의 분은
    # 절대 건드리지 않는다** — 그건 이미 확정된 진짜 사실이라 옮기면
    # "내가 실제로 골 넣은 분"이 달라져 버린다. 필러(text가 None)끼리,
    # 그리고 필러 vs 실제 이벤트 충돌만 필러 쪽을 밀어서 해소한다.
    _real_mins = sorted(r["min"] for r in log if r["text"] is not None)

    def _blocked(m, exclude_idx):
        if any(abs(m - rm) < 1e-6 for rm in _real_mins):
            return True
        return any(abs(m - log[j]["min"]) < 1e-6 for j in range(len(log)) if j != exclude_idx)

    for i, rec in enumerate(log):
        if rec["text"] is not None:
            continue  # 실제 이벤트는 절대 이동 금지
        guard = 0
        while _blocked(rec["min"], i) and guard < 2000:
            rec["min"] = round(rec["min"] + 0.1, 1)
            if rec["min"] > 94.9:
                rec["min"] = 94.9
                break
            guard += 1

    log.sort(key=lambda r: r["min"])
    return log