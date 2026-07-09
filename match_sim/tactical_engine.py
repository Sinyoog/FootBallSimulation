# -*- coding: utf-8 -*-
"""match_sim/tactical_engine.py — 포메이션 매치업 기반 경기 결과 시뮬레이션.

[왜 필요한가]
기존 game_engine._gen_score()는 "홈-원정 OVR 차이 → 확률표 조회"로 스코어를
결정했다. 승/무/패 확률과 골 차이가 전부 OVR 차이 하나로만 정해지고, 그
경기에서 실제로 어느 구역을 누가 장악했는지, 포메이션끼리 어디서 수적/능력치
우위가 나는지는 전혀 반영되지 않았다 — "3-5-2가 중원에서 4-4-2를 5:4로
압도한다" 같은 전술적 사실이 결과에 개입할 여지가 구조적으로 없었다.

이 모듈은 그 자리를 대체한다. 실제로 피치를 3레인(좌/중/우) x 3서드(수비/
중원/공격)로 나누고, 각 팀의 포메이션이 그 구역에 배치하는 선수들의 실제
스탯(슈팅/패스/드리블/태클/포지셔닝 등)으로 "이 구역은 어느 팀이 우세한가"를
계산한 뒤, 그 우세를 따라 볼이 흘러가는 것을 분 단위(90분)로 시뮬레이션해서
슈팅/코너/파울/골이 그 결과로 자연스럽게 "발생"하게 만든다. 스코어와 팀
통계(슈팅/유효슈팅/코너/파울/점유율)는 사후에 역산되는 게 아니라 이
시뮬레이션의 직접적인 산출물이다.

[적용 범위 — 중요]
이건 사용자가 실제로 관전하는 "내 경기"(game_engine._simulate_match)에만
쓰인다. 리그의 나머지 수십~수백 경기(AI vs AI, _sim_all_ai_matches 등)는
이 정밀 시뮬레이션을 돌릴 필요가 없고(성능 낭비 + 안 보는 경기라 의미도
없음), 기존 OVR 차이 기반 확률표(_match_win_probs/_gen_score)를 그대로
쓴다 — 이 모듈은 그 함수들을 건드리지 않는다.

[개인 서사와의 관계]
내 선수 개인의 골/도움/선방/평점(game_engine._player_perf)은 이 모듈이
건드리지 않는다. 그건 여전히 "확정된 팀 스코어에 맞춰 내 개인 기록을
그럴듯하게 만드는" 별개 로직이고, 이 모듈이 만든 스코어를 입력으로 그대로
받는다. 대신 이 모듈이 만든 팀 통계(슈팅 수 등)는 game_engine._derive_match_stats
에서 "내 개인 기록이 하한선"이라는 기존 원칙과 합쳐져 최종 팀 통계가 된다
(engine이 만든 진짜 값을 기준점으로 쓰되, 내 개인 슈팅이 그보다 많으면
그쪽을 존중 — 모순이 안 생기게).
"""
import math
import random

LANES = ("L", "C", "R")

# 포지션 라벨 -> 기준 좌표. x: 0(자기 골문)~1(상대 골문), y: 0(왼쪽)~1(오른쪽).
# match_sim_viewer._POS_XY와 같은 세계관을 공유하되(같은 좌표계 감각), 이
# 모듈은 UI 레이어에 의존하면 안 되므로 별도로 갖고 있는 값이다.
_POS_XY = {
    "GK":  (0.05, 0.50),
    "CB":  (0.16, 0.50), "LB": (0.18, 0.14), "RB": (0.18, 0.86),
    "LWB": (0.28, 0.12), "RWB": (0.28, 0.88),
    "CDM": (0.34, 0.50), "CM": (0.44, 0.50), "CAM": (0.48, 0.50),
    "LM":  (0.44, 0.16), "RM": (0.44, 0.84),
    "LW":  (0.49, 0.14), "RW": (0.49, 0.86),
    "CF":  (0.50, 0.50), "ST": (0.50, 0.50),
}
_FALLBACK_SLOTS = ["GK", "CB", "CB", "LB", "RB", "LM", "CM", "CM", "RM", "ST", "ST"]


def _third_of(bx):
    """이 팀 자신의 포메이션 상 역할(수비/미드필더/공격수) 분류.
    [버그 수정] 이 파일의 _POS_XY는 "한 팀이 스스로의 골문(0)을 기준으로
    갖는 기본 포메이션 형태"만 담고 있어서 x값 범위가 0.05~0.50 정도로
    좁다(공격수도 하프라인 부근인 0.50이 최댓값 — 원정팀은 이 값이
    1-bx로 뒤집혀 0.50~0.95가 됨). 그런데 예전 임계값(0.34/0.67)은 피치
    전체(0~1)를 3등분하는 값이라, 홈팀 공격수는 절대 "ATT"에 못
    들어가고(bx가 0.67을 못 넘음) 원정팀 수비수는 절대 "DEF"에 못
    들어갔다(뒤집힌 x가 0.34 밑으로 안 내려감) — 그 결과 _TeamModel의
    공격/수비 퀄리티가 실제 선수 스탯을 거의 반영하지 못했다.
    이제 이 함수는 뒤집히지 않은 원래 bx(각 팀 자신의 대형 좌표)만
    받는다 — "이 선수가 자기 팀 안에서 수비수/미드필더/공격수 중
    무엇에 가까운가"라는 팀 내부적 역할 분류이지, 피치의 고정된 절대
    구역이 아니기 때문이다. 임계값도 실제 _POS_XY 값 분포(수비 라인
    0.05~0.28, 중원 0.34~0.48, 최전방 0.49~0.50)에 맞게 재보정했다."""
    if bx < 0.30:
        return "DEF"
    if bx < 0.485:
        return "MID"
    return "ATT"


def _sigmoid(x):
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _assign_zones(lineup, is_home):
    """[{"player":dict, "lane":..., "third":...}, ...]. lineup은
    FORMATION_SLOTS[formation] 순서와 대응하는 선수 dict 리스트(None 허용).
    원정팀은 홈팀과 정반대 방향을 보고 뛰므로, 전후좌표(x)뿐 아니라
    좌우좌표(by)도 함께 뒤집어야(1-by) 홈팀 시점 고정좌표계에서 물리적으로
    맞는 위치가 된다.
    [버그 수정] 예전엔 x만 뒤집고 by는 그대로 둬서, 원정팀 왼쪽 수비수가
    실제로는 홈팀 시점 오른쪽 측면에 있어야 하는데도 그대로 "L" 레인에
    잡혔다. 매치업 계산(simulate_tactical_match의 atk.att[lane] vs
    dfn_opp.dfn[lane])이 두 팀을 같은 레인 라벨끼리 비교하는 구조라서,
    좌우 능력치가 비대칭인 스쿼드(예: 왼쪽 윙어는 강한데 오른쪽 풀백은
    약한 팀)를 상대할 때 실제로는 안 맞붙어야 할 반대편 선수와 매치업이
    계산되는 원인이었다."""
    out = []
    for i, pl in enumerate(lineup):
        if pl is None:
            continue
        label = _FALLBACK_SLOTS[i] if i < len(_FALLBACK_SLOTS) else pl.get("position", "CM")
        bx, by = _POS_XY.get(label, (0.44, 0.5))
        x = bx if is_home else (1.0 - bx)
        y = by if is_home else (1.0 - by)
        lane = "L" if y < 0.34 else ("C" if y < 0.67 else "R")
        # [버그 수정] third(수비/미드필더/공격수 역할 분류)는 팀 내부적
        # 역할이라 뒤집힌 x가 아니라 항상 원래 bx로 판정해야 한다(자세한
        # 이유는 _third_of 문서 참고) — 안 그러면 홈팀 공격수/원정팀
        # 수비수의 실제 스탯이 att/dfn 계산에 전혀 반영되지 않는다.
        out.append({"player": pl, "pos": label, "lane": lane, "third": _third_of(bx)})
    return out


def _avg(vals, default=50.0):
    vals = list(vals)
    return sum(vals) / len(vals) if vals else default


def _attack_quality(players):
    return _avg((p.get("shooting", 50) * 0.35 + p.get("dribbling", 50) * 0.30
                 + p.get("passing", 50) * 0.35) for p in players)


def _defense_quality(players):
    return _avg((p.get("tackling", 50) * 0.45 + p.get("positioning", 50) * 0.35
                 + p.get("strength", 50) * 0.20) for p in players)


def _midfield_quality(players):
    return _avg((p.get("passing", 50) * 0.35 + p.get("positioning", 50) * 0.30
                 + p.get("dribbling", 50) * 0.20 + p.get("stamina", 50) * 0.15)
                for p in players)


def _gk_quality(gk):
    if not gk:
        return 50.0
    return (gk.get("positioning", 50) * 0.5 + gk.get("concentration", 50) * 0.3
            + gk.get("jump", 50) * 0.2)


class _TeamModel:
    """한 팀의 레인별 공격/수비 퀄리티 + 중원 퀄리티 + GK 퀄리티를 미리
    계산해 담아두는 그릇. boost는 '내 에이스가 팀을 끌어올리는 효과'를
    수비/공격/중원 전역에 고르게 얹기 위한 값(game_engine._simulate_match가
    이미 계산해둔 bonus를 그대로 받는다)."""

    def __init__(self, lineup, is_home, boost=0.0):
        zoned = _assign_zones(lineup, is_home)
        self.gk = next((z["player"] for z in zoned if z["pos"] == "GK"), None)
        self.att = {}
        self.dfn = {}
        for ln in LANES:
            att_players = [z["player"] for z in zoned if z["lane"] == ln and z["third"] == "ATT"]
            def_players = [z["player"] for z in zoned if z["lane"] == ln and z["third"] == "DEF"]
            self.att[ln] = _attack_quality(att_players) + boost * 0.6
            self.dfn[ln] = _defense_quality(def_players) + boost * 0.15
        mid_players = [z["player"] for z in zoned if z["third"] == "MID"]
        self.mid = _midfield_quality(mid_players) + boost * 0.5
        self.gk_q = _gk_quality(self.gk) + boost * 0.1


def _resolve_shot(rng, side, lane, minute, shooter_pool, opp_gk_q, home_stats, away_stats, plog):
    """슈팅 하나를 판정해서 team_stats/possession_log를 갱신한다.
    반환값: 골이 들어갔으면 "home"/"away", 아니면 None."""
    stats = home_stats if side == "home" else away_stats
    stats["shots"] += 1

    if shooter_pool:
        weights = [max(1.0, p.get("shooting", 50)) for p in shooter_pool]
        shooter = rng.choices(shooter_pool, weights=weights, k=1)[0]
    else:
        shooter = {}
    shot_stat = shooter.get("shooting", 50)

    on_target_p = max(0.15, min(0.78, 0.30 + (shot_stat - 50) / 150.0))
    if rng.random() >= on_target_p:
        plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                     "outcome": "shot_off", "me": False, "text": None})
        return None

    stats["shots_on"] += 1
    save_p = max(0.08, min(0.85, 0.5 + (opp_gk_q - shot_stat) / 120.0))
    if rng.random() < save_p:
        plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                     "outcome": "save", "me": False, "text": None})
        return None

    plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                 "outcome": "goal", "me": False, "text": None})
    return side


def simulate_tactical_match(home_lineup, away_lineup, home_boost=0.0, away_boost=0.0,
                             home_adv=3.0, seed=None):
    """포메이션 매치업을 실제로 계산해서 90분(+추가시간) 경기를 시뮬레이션한다.

    Args:
        home_lineup/away_lineup: FORMATION_SLOTS 순서의 선수 dict 리스트
            (match_sim.match_flow._select_lineup()의 반환값 그대로 넣으면 됨).
            None 슬롯 허용(그 자리는 그냥 빈 것으로 취급).
        home_boost/away_boost: 그 팀에 얹을 전역 보정(내 에이스 효과 등).
        home_adv: 홈 이점(중원 퀄리티에 가산).
        seed: 지정하면 결정론적 재현.

    Returns:
        {"home_score", "away_score", "home_stats", "away_stats", "possession_log"}
        home_stats/away_stats: {"poss","shots","shots_on","corners","fouls"}
        possession_log: match_flow.generate_possession_log()와 같은 레코드
            형식([{"min","team","zone","outcome","me","text"}, ...]) — 이번
            단계에서는 팀 결과(스코어/통계)만 이 로그의 골/슈팅 합계와
            일치시키고, 화면 재생은 여전히 match_flow가 만드는 필러로
            채운다(시각화까지 이 로그를 직접 쓰는 건 다음 단계 작업).
    """
    rng = random.Random(seed) if seed is not None else random

    home = _TeamModel(home_lineup, True, boost=home_boost)
    away = _TeamModel(away_lineup, False, boost=away_boost)

    # [신규 — 경기 당일 컨디션] 매 분마다 실력 평균으로 수렴하는 구조라,
    # 분 단위 시뮬레이션만으로는 실제 축구의 "약팀이 어쩌다 강팀을 잡는"
    # 이변이 거의 안 나왔다(실측: OVR 15 차이에도 패배 확률 3%로, 기존
    # 확률표의 16%보다 훨씬 낮았음). 경기 시작 전에 딱 한 번 양팀에
    # "그날의 컨디션" 오차를 부여해서, 그 경기 내내 일관되게 유지되는
    # 변동성을 추가한다 — 매 분 독립적으로 흔들리는 잡음과 달리, 이건
    # "그 팀이 그날 유독 잘 풀리거나 안 풀리는" 것과 같아서 이변 가능성을
    # 만들어준다.
    home_form = rng.gauss(0, 7.0)
    away_form = rng.gauss(0, 7.0)
    for ln in LANES:
        home.att[ln] += home_form * 0.6
        home.dfn[ln] += home_form * 0.6
        away.att[ln] += away_form * 0.6
        away.dfn[ln] += away_form * 0.6
    home.mid += home_form
    away.mid += away_form
    home.gk_q += home_form * 0.5
    away.gk_q += away_form * 0.5

    home_stats = {"shots": 0, "shots_on": 0, "corners": 0, "fouls": 0}
    away_stats = {"shots": 0, "shots_on": 0, "corners": 0, "fouls": 0}
    home_score = away_score = 0
    plog = []

    home_mid_total = home.mid + home_adv
    away_mid_total = away.mid
    home_poss_minutes = 0

    # 부상시간 포함 대략 96분 정도로.
    total_minutes = 96

    home_zoned = _assign_zones(home_lineup, True)
    away_zoned = _assign_zones(away_lineup, False)

    # [최적화] home.att/dfn, away.att/dfn(레인별 공격/수비 퀄리티)은 이
    # 시점 이후로 루프 안에서 전혀 바뀌지 않는다(경기당일 컨디션 보정도
    # 이미 루프 진입 전에 다 반영돼 있음). 그런데 예전엔 p_home_poss,
    # 레인 가중치, 레인별 quality, 레인별 슈터 풀을 분마다(최대 96회) 매번
    # 다시 계산했다 — 매번 같은 입력으로 같은 값을 다시 뽑는 것이라
    # math.exp 호출(_sigmoid)과 리스트 컴프리헨션만 반복해서 낭비였다.
    # 여기서 팀당 한 번(레인 3개 기준)만 계산해서 캐시해두고, 루프
    # 안에서는 조회만 한다. rng.random()/rng.choices() 호출 횟수와 순서는
    # 그대로라 시드 고정 시 결과는 동일하다(순수 캐싱, 로직 변경 없음).
    p_home_poss = _sigmoid((home_mid_total - away_mid_total) / 16.0)

    def _prep_side(atk, dfn_opp, zoned_atk):
        lane_scores = {ln: max(1.0, atk.att[ln] - dfn_opp.dfn[ln] + 50.0) for ln in LANES}
        lanes, weights = zip(*lane_scores.items())
        quality_by_lane = {ln: _sigmoid((atk.att[ln] - dfn_opp.dfn[ln]) / 11.0) for ln in LANES}
        att_pool_all = [z["player"] for z in zoned_atk if z["third"] == "ATT"]
        pool_by_lane = {}
        for ln in LANES:
            pool = [z["player"] for z in zoned_atk if z["lane"] == ln and z["third"] == "ATT"]
            pool_by_lane[ln] = pool if pool else att_pool_all
        return lanes, weights, quality_by_lane, pool_by_lane

    home_lanes, home_weights, home_quality_by_lane, home_pool_by_lane = \
        _prep_side(home, away, home_zoned)
    away_lanes, away_weights, away_quality_by_lane, away_pool_by_lane = \
        _prep_side(away, home, away_zoned)

    for minute in range(1, total_minutes + 1):
        poss_home = rng.random() < p_home_poss
        if poss_home:
            home_poss_minutes += 1
            side, dfn_opp = "home", away
            lanes, weights = home_lanes, home_weights
            quality_by_lane, pool_by_lane = home_quality_by_lane, home_pool_by_lane
        else:
            side, dfn_opp = "away", home
            lanes, weights = away_lanes, away_weights
            quality_by_lane, pool_by_lane = away_quality_by_lane, away_pool_by_lane

        lane = rng.choices(lanes, weights=weights, k=1)[0]
        quality = quality_by_lane[lane]

        roll = rng.random()
        shot_chance = 0.075 + quality * 0.23             # 대략 7.5~30.5%
        corner_chance = 0.02 + quality * 0.035          # 걷어낸 공이 라인 밖으로
        foul_chance = 0.035 + (1.0 - quality) * 0.03    # 밀릴 때 거칠게 끊는 경우

        shooter_pool = pool_by_lane[lane]

        if roll < shot_chance:
            scorer_side = _resolve_shot(
                rng, side, lane, minute, shooter_pool, dfn_opp.gk_q, home_stats, away_stats, plog)
            if scorer_side == "home":
                home_score += 1
            elif scorer_side == "away":
                away_score += 1
        elif roll < shot_chance + corner_chance:
            (home_stats if side == "home" else away_stats)["corners"] += 1
            plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                         "outcome": "corner", "me": False, "text": None})
        elif roll < shot_chance + corner_chance + foul_chance:
            fouling_side = "away" if side == "home" else "home"
            (home_stats if fouling_side == "home" else away_stats)["fouls"] += 1
            plog.append({"min": float(minute), "team": fouling_side, "zone": "mid", "lane": lane,
                         "outcome": "foul", "me": False, "text": None})
        else:
            # [신규 — 필러 없는 진짜 로그] 예전엔 이 "특별한 일 없는" 분들이
            # match_flow의 무작위 필러(최대 24개, 실제 우세와 무관하게
            # 대충 배분)로 채워졌다. 이제는 이 시뮬레이션이 실제로 계산한
            # "이번 분에 어느 팀이 어느 레인/서드에서 우세했는가"를 그대로
            # 기록한다 — 90분 전체가 진짜 매치업 계산의 산출물이 된다.
            # zone(서드)은 quality(공격측이 그 레인에서 얼마나 우세했는지)
            # 로 판정: 크게 우세하면 상대 진영 깊숙이(att), 팽팽하면
            # 중원(mid), 밀리면 자기 진영(def)에 머문 것으로 본다.
            if quality > 0.62:
                zone = "att"
            elif quality < 0.38:
                zone = "def"
            else:
                zone = "mid"
            plog.append({"min": float(minute), "team": side, "zone": zone, "lane": lane,
                         "outcome": "buildup", "me": False, "text": None})

    home_poss_pct = round(100.0 * home_poss_minutes / total_minutes)
    home_poss_pct = max(28, min(72, home_poss_pct))
    home_stats["poss"] = home_poss_pct
    away_stats["poss"] = 100 - home_poss_pct

    plog.sort(key=lambda r: r["min"])
    return {
        "home_score": home_score, "away_score": away_score,
        "home_stats": home_stats, "away_stats": away_stats,
        "possession_log": plog,
    }


def merge_personal_events(plog, personal_events, my_side):
    """엔진이 만든 possession_log(전부 text=None)에 내 개인 서사(실제 골/
    도움/선방/파울/코너 텍스트)를 끼워 넣는다.

    설계 원칙: 실제 개인 이벤트가 벌어진 "분(minute)"은 이미 확정된
    사실이라 절대 옮기지 않는다 — 대신 같은 team/outcome을 가진 필러
    레코드 중 그 분에 가장 가까운 것 하나를 그 실제 시각으로 당겨와서
    text/me를 채운다. 그러면 팀 통계(그 outcome 총 개수)는 그대로 유지
    되면서, 실제로 있었던 사건은 정확한 순간에 표시된다.

    personal_events: [(minute, text), ...] — game_engine._player_perf가
    만든 개인 이벤트 목록. match_flow._classify_personal로 분류되는
    것만 처리하고(골/도움/실점/선방/파울/코너), 그 외 텍스트(부상,
    카드 등 possession과 무관한 것)는 이 함수가 손대지 않는다 —
    호출자가 그 텍스트를 timeline에 그대로 유지해야 한다.
    """
    from match_sim.match_flow import _classify_personal

    opp_side = "away" if my_side == "home" else "home"
    out = [dict(r) for r in plog]
    used_idx = set()

    kind_map = {
        "goal_for": (my_side, "goal", True),
        "goal_against": (opp_side, "goal", False),
        "miss_for": (my_side, "save", True),
        "save": (opp_side, "save", True),
    }
    for m, text in personal_events:
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

        candidates = [i for i, r in enumerate(out)
                      if i not in used_idx and r["team"] == side and r["outcome"] == outcome
                      and r["text"] is None]
        if not candidates:
            continue
        best = min(candidates, key=lambda i: abs(out[i]["min"] - float(m)))
        used_idx.add(best)
        out[best]["min"] = float(m)
        out[best]["text"] = text
        out[best]["me"] = me_flag

    out.sort(key=lambda r: r["min"])
    return out


def simulate_my_match(home_team_id, away_team_id, home_formation, away_formation,
                       home_boost=0.0, away_boost=0.0, home_adv=3.0, seed=None):
    """team_id 두 개만 받아서 로스터/포메이션 조회부터 시뮬레이션까지 전부
    처리하는 편의 함수. game_engine._simulate_match에서 이걸 하나만 호출하면
    된다."""
    from match_sim.match_flow import _select_lineup

    home_lineup = _select_lineup(home_team_id, home_formation)
    away_lineup = _select_lineup(away_team_id, away_formation)
    return simulate_tactical_match(home_lineup, away_lineup, home_boost=home_boost,
                                    away_boost=away_boost, home_adv=home_adv, seed=seed)