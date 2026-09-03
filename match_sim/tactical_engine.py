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


def _assign_zones(lineup, is_home, slot_labels=None):
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
    계산되는 원인이었다.

    [2026-09 버그수정, 신민용 리포트: "우리팀 포메이션은 5-2-3인데
    라인업 화면엔 무조건 4-2-2-2로 뜬다 — 상대팀도 실제(4-2-1-3)와
    다르게 뜬다"] slot_labels(그 라인업을 실제로 뽑을 때 쓴
    FORMATION_SLOTS[formation])를 안 받으면 이 함수가 항상 하드코딩된
    4-4-2 라벨(_FALLBACK_SLOTS)로 매 선수의 위치를 다시 칠했다 —
    lineup 자체는 _select_lineup()이 그 팀의 실제 포메이션 슬롯 순서로
    뽑아주는데, 라벨만 여기서 딴 걸로 덮어써서 실제 배치와 표시/구역
    계산이 어긋났다. 이 어긋남은 화면 표시뿐 아니라 _third_of(공격/
    수비/중원 역할 분류)에도 그대로 들어가 매치업 계산 자체에 영향을
    준다. 이제 호출부(simulate_tactical_match)가 그 라인업을 만들 때
    쓴 실제 슬롯 리스트를 넘겨주면 그걸 쓰고, 안 넘기면(기존 호출부·
    국제대회처럼 원래 4-4-2뿐인 경우) 예전과 동일하게 _FALLBACK_SLOTS로
    폴백한다 — 하위 호환 100% 유지."""
    labels = slot_labels if slot_labels else _FALLBACK_SLOTS
    out = []
    for i, pl in enumerate(lineup):
        if pl is None:
            continue
        label = labels[i] if i < len(labels) else pl.get("position", "CM")
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


# [2026-07 신설] 포지션별 boost(캐리 보너스) 채널 분배. 예전엔 포지션 구분
# 없이 모든 포지션이 공격 0.6/수비 0.15/중원 0.5/GK 0.1로 똑같이 받아서,
# 골키퍼가 아무리 OVR이 높아도 보너스의 90%가 공격·중원(본인이 관여 안 함)
# 으로 새고 정작 세이브 능력(gk_q)엔 10%만 반영되는 문제가 있었다(사용자
# 실측: OVR92 골키퍼가 팀평균 30후반 리그에서 전혀 안 먹힘). 이제 그
# 포지션이 실제로 경기에 관여하는 영역에 보너스가 집중되도록 나눈다.
# 각 값은 서로 다른 채널에 독립적으로 곱해지는 계수라 합이 1일 필요는
# 없다(기존 방식과 동일한 구조) — 다만 그 포지션의 실제 영향력 분포를
# 반영해 채널별 비중을 다르게 뒀다.
POSITION_BOOST_WEIGHTS = {
    "GK":  {"att": 0.00, "dfn": 0.15, "mid": 0.00, "gk": 0.90},
    "CB":  {"att": 0.05, "dfn": 0.80, "mid": 0.15, "gk": 0.00},
    "LB":  {"att": 0.15, "dfn": 0.65, "mid": 0.25, "gk": 0.00},
    "RB":  {"att": 0.15, "dfn": 0.65, "mid": 0.25, "gk": 0.00},
    "LWB": {"att": 0.25, "dfn": 0.55, "mid": 0.30, "gk": 0.00},
    "RWB": {"att": 0.25, "dfn": 0.55, "mid": 0.30, "gk": 0.00},
    "CDM": {"att": 0.10, "dfn": 0.35, "mid": 0.55, "gk": 0.00},
    "CM":  {"att": 0.25, "dfn": 0.15, "mid": 0.65, "gk": 0.00},
    "CAM": {"att": 0.45, "dfn": 0.05, "mid": 0.50, "gk": 0.00},
    "LM":  {"att": 0.35, "dfn": 0.15, "mid": 0.55, "gk": 0.00},
    "RM":  {"att": 0.35, "dfn": 0.15, "mid": 0.55, "gk": 0.00},
    "LW":  {"att": 0.70, "dfn": 0.05, "mid": 0.30, "gk": 0.00},
    "RW":  {"att": 0.70, "dfn": 0.05, "mid": 0.30, "gk": 0.00},
    "CF":  {"att": 0.85, "dfn": 0.00, "mid": 0.20, "gk": 0.00},
    "ST":  {"att": 0.85, "dfn": 0.00, "mid": 0.20, "gk": 0.00},
}
# 포지션을 모르거나 표에 없을 때 쓰는 폴백 — 기존(2026-07 이전) 분배값 그대로.
_DEFAULT_BOOST_WEIGHTS = {"att": 0.6, "dfn": 0.15, "mid": 0.5, "gk": 0.1}


def _boost_weights_for(position):
    return POSITION_BOOST_WEIGHTS.get(position, _DEFAULT_BOOST_WEIGHTS)


class _TeamModel:
    """한 팀의 레인별 공격/수비 퀄리티 + 중원 퀄리티 + GK 퀄리티를 미리
    계산해 담아두는 그릇. boost는 '내 에이스가 팀을 끌어올리는 효과'를
    수비/공격/중원 전역에 고르게 얹기 위한 값(game_engine._simulate_match가
    이미 계산해둔 bonus를 그대로 받는다)."""

    def __init__(self, lineup, is_home, boost=0.0, boost_position=None, slot_labels=None):
        zoned = _assign_zones(lineup, is_home, slot_labels)
        self.gk = next((z["player"] for z in zoned if z["pos"] == "GK"), None)
        w = _boost_weights_for(boost_position) if boost else _DEFAULT_BOOST_WEIGHTS
        self.att = {}
        self.dfn = {}
        for ln in LANES:
            att_players = [z["player"] for z in zoned if z["lane"] == ln and z["third"] == "ATT"]
            def_players = [z["player"] for z in zoned if z["lane"] == ln and z["third"] == "DEF"]
            self.att[ln] = _attack_quality(att_players) + boost * w["att"]
            self.dfn[ln] = _defense_quality(def_players) + boost * w["dfn"]
        mid_players = [z["player"] for z in zoned if z["third"] == "MID"]
        self.mid = _midfield_quality(mid_players) + boost * w["mid"]
        self.gk_q = _gk_quality(self.gk) + boost * w["gk"]


def _pstat(player_stats, p):
    """[2026-08 신설, 신민용 요청: "경기 시뮬레이션에 다른 선수들의 OVR·
    스탯도 계산해서 정교한 결과를 뽑고, 경기 상세에서 22명 전원 평점을
    보여달라"] player_stats(선수 id -> 개인 기록 누적 dict)에서 이 선수의
    항목을 찾아 반환 — 없으면 0으로 초기화해 새로 만든다. id가 없는(가상
    폴백) 선수는 객체 자체의 파이썬 id()를 키로 대신 써서 최소한 이번
    한 경기 안에서는 같은 객체가 같은 기록으로 누적되게 한다."""
    key = p.get("id")
    if key is None:
        key = id(p)
    rec = player_stats.get(key)
    if rec is None:
        rec = {"shots": 0, "shots_on": 0, "goals": 0, "assists": 0,
               "saves": 0, "goals_conceded": 0}
        player_stats[key] = rec
    return rec


def _new_stats_detail():
    """[2026-09 신설] "표시용" team_stats(점유율/슈팅/유효슈팅/코너/파울/
    패스성공률/오프사이드/카드/세이브 — 10개, 실제 중계화면 느낌)와 별도로
    엔진 내부에서 계산해두는 세부 통계 그릇. 화면엔 기본적으로 안 뿌리고
    (나중에 선수 통계·분석 등에 재활용하기 위해) 같이 저장만 해둔다.

    전부 기존 분당 시뮬레이션 루프(shot_chance/corner_chance/foul_chance로
    스코어를 결정하는 그 로직)는 손대지 않고, 그 결과에 병렬로 얹어서
    누적한다 — 경기 결과에 영향을 주지 않으므로 속도·밸런스 둘 다 그대로."""
    return {
        "passes": 0, "passes_ok": 0,
        "crosses": 0, "crosses_ok": 0,
        "tackles": 0, "tackles_ok": 0,
        "interceptions": 0, "clearances": 0, "blocks": 0,
        "aerial_duels": 0, "aerial_duels_ok": 0,
        "dribbles": 0, "dribbles_ok": 0,
        "turnovers_won": 0, "turnovers_lost": 0,
        "final_third_entries": 0, "box_entries": 0,
        "big_chances": 0, "big_chances_missed": 0,
        "xg": 0.0, "xa": 0.0,
        "woodwork": 0, "free_kicks": 0, "penalties": 0,
        "save_pct": 0.0,
    }


def _resolve_shot(rng, side, lane, minute, shooter_pool, opp_gk, opp_gk_q,
                   home_stats, away_stats, home_player_stats, away_player_stats, plog,
                   home_detail, away_detail):
    """슈팅 하나를 판정해서 team_stats/possession_log/선수별 개인 기록
    (슈팅·유효슈팅·골·도움·선방·실점)을 함께 갱신한다.
    반환값: 골이 들어갔으면 "home"/"away", 아니면 None.

    [2026-08 확장] 예전엔 shooter_pool에서 슈팅 스탯 가중으로 슈터를
    뽑아놓고도 그 신원을 결과에 전혀 남기지 않았다(팀+결과만 기록) —
    이제 그 선수 개인 기록에 직접 누적한다. 어시스트는 완전한 패스체인
    시뮬레이션 대신, 골이 들어갔을 때 같은 레인 공격 풀에서 슈터를 뺀
    나머지를 패스 능력 가중으로 뽑아 일정 확률로 붙이는 근사치다.

    [2026-09 확장, xG/xA] on_target_p(유효슈팅 확률)*(1-save_p)(안 막힐
    확률)를 "이 슈팅의 결과가 나오기 전 기대 득점(xG)"으로 그대로 쓴다 —
    이미 계산해두는 값이라 추가 비용이 없다. xA는 신민용 확정 설계대로
    "그 슈팅의 xG를 마지막 기여자에게 배분": 실제 어시스트가 붙으면 그
    도움 선수에게, 골로 안 이어진 유효슈팅도 일정 확률로 "키패스"를 굴려
    같은 방식으로 배분한다(어시스트 기록 없이도 창조적 기여를 평가할 수
    있게 하기 위함, 향후 개인 스탯 확장 대비)."""
    stats = home_stats if side == "home" else away_stats
    detail = home_detail if side == "home" else away_detail
    opp_detail = away_detail if side == "home" else home_detail
    my_pstats = home_player_stats if side == "home" else away_player_stats
    opp_pstats = away_player_stats if side == "home" else home_player_stats
    stats["shots"] += 1
    detail["final_third_entries"] += 1
    detail["box_entries"] += 1

    if shooter_pool:
        weights = [max(1.0, p.get("shooting", 50)) for p in shooter_pool]
        shooter = rng.choices(shooter_pool, weights=weights, k=1)[0]
    else:
        shooter = None
    shot_stat = shooter.get("shooting", 50) if shooter else 50
    if shooter is not None:
        _pstat(my_pstats, shooter)["shots"] += 1

    on_target_p = max(0.15, min(0.78, 0.30 + (shot_stat - 50) / 150.0))
    save_p = max(0.08, min(0.90, 0.63 + (opp_gk_q - shot_stat) / 120.0))
    shot_xg = round(on_target_p * (1.0 - save_p), 4)
    detail["xg"] += shot_xg
    is_big_chance = shot_xg >= 0.35

    if rng.random() >= on_target_p:
        # [세분화] 빗나간 슈팅 중 일부는 "완전히 벗어남"이 아니라 "골대를
        # 맞고 나감"으로 표시만 더 얹는다 — 유효슈팅/스코어 판정은 그대로.
        if rng.random() < 0.05:
            detail["woodwork"] += 1
        if is_big_chance:
            detail["big_chances"] += 1
            detail["big_chances_missed"] += 1
        plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                     "outcome": "shot_off", "me": False, "text": None})
        return None

    stats["shots_on"] += 1
    if shooter is not None:
        _pstat(my_pstats, shooter)["shots_on"] += 1
    if rng.random() < save_p:
        # 유효슈팅이 막힘 — 그중 일부는 "수비수 블록"으로 더 세분화하고
        # (상대 수비 기여 스탯), 골로 이어지지 않았어도 그 장면을 만든
        # 선수에게 키패스/xA를 확률적으로 인정한다.
        if rng.random() < 0.30:
            opp_detail["blocks"] += 1
        if opp_gk is not None:
            _pstat(opp_pstats, opp_gk)["saves"] += 1
        if is_big_chance:
            detail["big_chances"] += 1
            detail["big_chances_missed"] += 1
        if shooter_pool and len(shooter_pool) > 1 and rng.random() < 0.35:
            detail["xa"] += shot_xg
        plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                     "outcome": "save", "me": False, "text": None})
        return None

    # 골.
    if is_big_chance:
        detail["big_chances"] += 1
    plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                 "outcome": "goal", "me": False, "text": None,
                 "scorer_id": (shooter.get("id") if shooter is not None else None)})
    if shooter is not None:
        _pstat(my_pstats, shooter)["goals"] += 1
    if opp_gk is not None:
        _pstat(opp_pstats, opp_gk)["goals_conceded"] += 1
    if shooter_pool and len(shooter_pool) > 1 and rng.random() < 0.62:
        creator_pool = [p for p in shooter_pool if p is not shooter]
        if creator_pool:
            weights2 = [max(1.0, p.get("passing", 50)) for p in creator_pool]
            creator = rng.choices(creator_pool, weights=weights2, k=1)[0]
            _pstat(my_pstats, creator)["assists"] += 1
            detail["xa"] += shot_xg
    return side


def _lineup_avg_ovr(lineup):
    vals = [p.get("ovr", 50) for p in lineup if p is not None]
    return sum(vals) / len(vals) if vals else 50.0


def _build_player_ratings(lineup, player_stats, gf, ga, gk, rng, slot_labels=None):
    """[2026-08 신설] 경기 종료 후 이 팀 11명 전원(라인업 슬롯 순서 그대로,
    빈 슬롯은 None)의 개인 기록 + 평점을 만든다.

    평점 공식은 game_engine._player_perf(내 선수 전용 평점)와 같은
    발상 — 기본값에서 시작해 팀 결과/개인 기여/포지션별 특성을 더하고
    빼는 방식 — 을 22명 전체로 일반화한 것이다. 다만 이 엔진은 개별
    수비 액션(태클 성공/실패 등)까지는 분 단위로 추적하지 않으므로,
    비GK 필드 플레이어의 평점은 "팀 결과 + 그 선수의 골/도움 기여 +
    그 선수 OVR이 이 라인업 평균보다 얼마나 높은가(에이스는 골이 없어도
    경기를 지배한 것으로 봄)"를 근거로 삼는다 — 완전한 개인 이벤트
    로그가 아니라 근사치라는 점은 명확히 해둔다.

    [2026-08 버그수정, 신민용 리포트: "라인업 평점에 뜨는 이름이
    AI0JP8이 아니라 names.py에서 뽑힌 실제 이름이다"] ai_players.name은
    data/names.py에서 뽑은 내부 시드값일 뿐, 화면에는 항상 마스킹된
    표시명(ui/formation_widget._mask_ai_names와 완전히 동일한 규칙 —
    사용자가 직접 지어준 커스텀 이름이 있으면 그걸, 없으면
    constants.ai_player_code로 만든 "AI"+코드)을 써야 한다. 이 값은
    실제로 존재하는 그 선수(가상으로 지어낸 게 아니라 그 팀 로스터에서
    _select_lineup이 실제로 뽑은 ai_players 레코드)이고, 문제는 이름
    '표시' 단계뿐이었다.

    [2026-09 버그수정, 신민용 리포트: "포메이션이 5-2-3인데 라인업
    화면엔 4-2-2-2로 뜬다"] _assign_zones와 같은 이유로, 여기 출력되는
    각 선수의 "position"도 실제 포메이션과 무관하게 항상 _FALLBACK_SLOTS
    (4-4-2 라벨)로 찍히고 있었다 — ui/match_detail_dialog.py의 라인업
    평점·포메이션 시각화가 이 값을 그대로 보여주므로 화면에 실제
    포메이션과 다른 모양이 떴다. slot_labels(호출부가 이 lineup을 뽑을
    때 쓴 실제 FORMATION_SLOTS[formation])를 받으면 그걸 쓰고, 안
    받으면 예전처럼 _FALLBACK_SLOTS로 폴백한다."""
    labels = slot_labels if slot_labels else _FALLBACK_SLOTS
    real_ids = [p.get("id") for p in lineup if p is not None and p.get("id") is not None]
    try:
        from database import get_ai_player_custom_names
        custom_names = get_ai_player_custom_names(real_ids) if real_ids else {}
    except Exception:
        custom_names = {}
    from constants import ai_player_code

    def _display_name(pl):
        pid = pl.get("id")
        if pid is None:
            return pl.get("name", "") or "AI"
        return custom_names.get(pid) or ai_player_code(pid)

    avg_ovr = _lineup_avg_ovr(lineup)
    clean_sheet = (ga == 0)
    result_mod = 0.45 if gf > ga else (-0.35 if gf < ga else 0.0)
    out = []
    for i, p in enumerate(lineup):
        if p is None:
            out.append(None)
            continue
        is_gk = (p is gk)
        key = p.get("id")
        if key is None:
            key = id(p)
        pstat = player_stats.get(key, {})
        base = 6.3 + result_mod
        base += max(-0.6, min(0.6, (p.get("ovr", 50) - avg_ovr) / 40.0))
        base += pstat.get("goals", 0) * 0.75
        base += pstat.get("assists", 0) * 0.4
        base += max(0, pstat.get("shots_on", 0) - pstat.get("goals", 0)) * 0.05
        if is_gk:
            base += pstat.get("saves", 0) * 0.12
            conceded = pstat.get("goals_conceded", 0)
            if clean_sheet:
                base += 0.5
            base -= max(0, conceded - 1) * 0.15
        elif clean_sheet:
            # 필드 플레이어도 완봉승엔 소폭 가산(수비 기여를 개인 이벤트
            # 없이도 어느 정도 반영 — 실제 수비 기여도는 못 따로 추적함).
            base += 0.15
        base += rng.gauss(0, 0.25)
        label = labels[i] if i < len(labels) else p.get("position", "CM")
        out.append({
            "id": p.get("id"), "name": _display_name(p), "position": label,
            "ovr": p.get("ovr", 50),
            "goals": pstat.get("goals", 0), "assists": pstat.get("assists", 0),
            "shots": pstat.get("shots", 0), "shots_on": pstat.get("shots_on", 0),
            "saves": pstat.get("saves", 0) if is_gk else 0,
            "is_gk": is_gk,
            "rating": round(max(3.0, min(10.0, base)), 1),
        })
    return out


def simulate_tactical_match(home_lineup, away_lineup, home_boost=0.0, away_boost=0.0,
                             home_boost_position=None, away_boost_position=None,
                             home_adv=3.0, seed=None,
                             home_formation=None, away_formation=None):
    """포메이션 매치업을 실제로 계산해서 90분(+추가시간) 경기를 시뮬레이션한다.

    Args:
        home_lineup/away_lineup: FORMATION_SLOTS 순서의 선수 dict 리스트
            (match_sim.match_flow._select_lineup()의 반환값 그대로 넣으면 됨).
            None 슬롯 허용(그 자리는 그냥 빈 것으로 취급).
        home_boost/away_boost: 그 팀에 얹을 전역 보정(내 에이스 효과 등).
        home_boost_position/away_boost_position: [2026-07 신설] 그 보정을
            받는 선수(=나)의 포지션. POSITION_BOOST_WEIGHTS로 보정이 실제
            그 포지션이 영향력을 행사하는 채널(공격/수비/중원/GK)에 집중
            되도록 분배한다. None이면 기존 방식(포지션 무관 균등 분배)으로
            폴백.
        home_adv: 홈 이점(중원 퀄리티에 가산).
        seed: 지정하면 결정론적 재현.
        home_formation/away_formation: [2026-09 신설, 신민용 리포트: "포메이션이
            5-2-3인데 라인업 화면엔 4-2-2-2로 뜬다"] home_lineup/away_lineup을
            실제로 뽑을 때 쓴 FORMATION_SLOTS 키(예: "5-2-3"). 넘기면 구역
            계산(_TeamModel/_assign_zones)과 라인업 평점의 포지션 라벨이
            전부 이 실제 포메이션 기준으로 맞춰진다. None이면(기존 호출부·
            항상 4-4-2뿐인 국제대회 등) 예전과 동일하게 4-4-2 라벨로
            폴백한다 — 하위 호환 100% 유지.

    Returns:
        {"home_score", "away_score", "home_stats", "away_stats",
         "home_stats_detail", "away_stats_detail", "possession_log", ...}
        home_stats/away_stats: 실제 중계화면에 보여줄 "표시용" 10개 —
            {"poss","shots","shots_on","corners","fouls","pass_acc",
             "offsides","yellow_cards","red_cards","saves"}. pass_acc는
             실제 패스 시도/성공 집계(0으로 나뉠 일 없이 항상 0.0~1.0
             사이 값)로 항상 채워진다.
        home_stats_detail/away_stats_detail: [2026-09 신설] 화면엔 기본
            노출 안 하고 저장만 해두는 세부 통계 — _new_stats_detail()의
            키 그대로(총패스/성공패스, 크로스, 태클, 가로채기, 클리어링,
            블록, 공중볼, 드리블, 볼탈취/상실, 서드·박스 진입, 빅찬스,
            xG/xA, 골대, 프리킥, PK, 선방률).
        possession_log: match_flow.generate_possession_log()와 같은 레코드
            형식([{"min","team","zone","outcome","me","text"}, ...]) — 이번
            단계에서는 팀 결과(스코어/통계)만 이 로그의 골/슈팅 합계와
            일치시키고, 화면 재생은 여전히 match_flow가 만드는 필러로
            채운다(시각화까지 이 로그를 직접 쓰는 건 다음 단계 작업).
    """
    rng = random.Random(seed) if seed is not None else random

    home_slots = away_slots = None
    if home_formation or away_formation:
        from constants import FORMATION_SLOTS
        if home_formation:
            home_slots = FORMATION_SLOTS.get(home_formation)
        if away_formation:
            away_slots = FORMATION_SLOTS.get(away_formation)

    home = _TeamModel(home_lineup, True, boost=home_boost, boost_position=home_boost_position,
                       slot_labels=home_slots)
    away = _TeamModel(away_lineup, False, boost=away_boost, boost_position=away_boost_position,
                       slot_labels=away_slots)

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

    home_stats = {"shots": 0, "shots_on": 0, "corners": 0, "fouls": 0,
                  "offsides": 0, "yellow_cards": 0, "red_cards": 0, "saves": 0}
    away_stats = {"shots": 0, "shots_on": 0, "corners": 0, "fouls": 0,
                  "offsides": 0, "yellow_cards": 0, "red_cards": 0, "saves": 0}
    # [2026-09 신설] "표시용" 10개 옆에 별도로 두는 세부 통계 그릇 —
    # _new_stats_detail() 참고.
    home_detail = _new_stats_detail()
    away_detail = _new_stats_detail()
    # [2026-08 신설] 선수 id(또는 id 없는 폴백 선수는 파이썬 id()) ->
    # {"shots","shots_on","goals","assists","saves","goals_conceded"} 누적.
    # _resolve_shot이 채우고, 경기 종료 후 아래에서 평점으로 환산한다.
    home_player_stats = {}
    away_player_stats = {}
    home_score = away_score = 0
    plog = []

    home_mid_total = home.mid + home_adv
    away_mid_total = away.mid
    home_poss_minutes = 0

    # 부상시간 포함 대략 96분 정도로.
    total_minutes = 96

    home_zoned = _assign_zones(home_lineup, True, home_slots)
    away_zoned = _assign_zones(away_lineup, False, away_slots)

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
        atk_detail = home_detail if side == "home" else away_detail
        dfn_detail = away_detail if side == "home" else home_detail

        # [2026-09 신설] 패스는 슈팅/코너/파울/빌드업과 무관하게 "이번 분에
        # 볼을 가진 팀"이면 항상 몇 번씩 오간다고 보고 매 분 누적한다(기존
        # shot_chance/corner_chance/foul_chance 판정과는 완전히 별개 —
        # 결과 결정 로직은 안 건드림). 성공률은 이 레인의 quality(공격측이
        # 수비를 얼마나 압도하는가)에 연동 — 밀어붙이는 팀일수록 패스가
        # 더 잘 이어진다는 감각.
        _pass_n = 3 + rng.randrange(0, 4)          # 분당 3~6회
        _pass_p = max(0.55, min(0.95, 0.68 + (quality - 0.5) * 0.35))
        _pass_ok = int(round(_pass_n * _pass_p + rng.uniform(-0.4, 0.4)))
        _pass_ok = max(0, min(_pass_n, _pass_ok))
        atk_detail["passes"] += _pass_n
        atk_detail["passes_ok"] += _pass_ok

        roll = rng.random()
        shot_chance = 0.075 + quality * 0.23             # 대략 7.5~30.5%
        corner_chance = 0.02 + quality * 0.035          # 걷어낸 공이 라인 밖으로
        foul_chance = 0.035 + (1.0 - quality) * 0.03    # 밀릴 때 거칠게 끊는 경우

        shooter_pool = pool_by_lane[lane]

        if roll < shot_chance:
            scorer_side = _resolve_shot(
                rng, side, lane, minute, shooter_pool, dfn_opp.gk, dfn_opp.gk_q,
                home_stats, away_stats, home_player_stats, away_player_stats, plog,
                home_detail, away_detail)
            if scorer_side == "home":
                home_score += 1
            elif scorer_side == "away":
                away_score += 1
        elif roll < shot_chance + corner_chance:
            (home_stats if side == "home" else away_stats)["corners"] += 1
            atk_detail["final_third_entries"] += 1
            plog.append({"min": float(minute), "team": side, "zone": "att", "lane": lane,
                         "outcome": "corner", "me": False, "text": None})
        elif roll < shot_chance + corner_chance + foul_chance:
            fouling_side = "away" if side == "home" else "home"
            (home_stats if fouling_side == "home" else away_stats)["fouls"] += 1
            fouled_detail = atk_detail    # 파울을 당한(=프리킥/PK를 얻는) 쪽
            # [2026-09 신설] 파울 하나를 프리킥/PK와 카드 유무로 세분화한다.
            # 공격측이 이미 그 레인을 크게 압도(quality 높음)하던 중 끊긴
            # 파울만 낮은 확률로 PK(박스 안 파울)로 승격 — 나머지는 프리킥.
            if quality > 0.68 and rng.random() < 0.10:
                fouled_detail["penalties"] += 1
            else:
                fouled_detail["free_kicks"] += 1
            _card_roll = rng.random()
            if _card_roll < 0.006:
                (home_stats if fouling_side == "home" else away_stats)["red_cards"] += 1
            elif _card_roll < 0.11:
                (home_stats if fouling_side == "home" else away_stats)["yellow_cards"] += 1
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

            # [2026-09 신설] 이 "특별한 일 없는" 국면도 실제로 무슨 장면
            # 이었는지 세부 통계로 나눠 둔다 — possession_log 텍스트/필러
            # 로직도, 위 shot/corner/foul 판정도 안 건드리고 병렬로만
            # 누적한다.
            if zone == "att":
                atk_detail["final_third_entries"] += 1
                sub = rng.random()
                if lane != "C" and sub < 0.22:
                    atk_detail["crosses"] += 1
                    if rng.random() < (0.35 + quality * 0.25):
                        atk_detail["crosses_ok"] += 1
                elif sub < 0.40:
                    atk_detail["dribbles"] += 1
                    if rng.random() < (0.40 + quality * 0.30):
                        atk_detail["dribbles_ok"] += 1
                elif sub < 0.45:
                    # 침투 시도가 오프사이드로 끊김
                    (home_stats if side == "home" else away_stats)["offsides"] += 1
            elif zone == "def":
                # 공격측이 밀리는 국면 — 수비측이 볼을 따낸다.
                sub = rng.random()
                if sub < 0.30:
                    dfn_detail["tackles"] += 1
                    if rng.random() < (0.45 + (1.0 - quality) * 0.30):
                        dfn_detail["tackles_ok"] += 1
                        dfn_detail["turnovers_won"] += 1
                        atk_detail["turnovers_lost"] += 1
                elif sub < 0.50:
                    dfn_detail["interceptions"] += 1
                    dfn_detail["turnovers_won"] += 1
                    atk_detail["turnovers_lost"] += 1
                elif sub < 0.65:
                    dfn_detail["clearances"] += 1
            if rng.random() < 0.06:
                dfn_detail["aerial_duels"] += 1
                atk_detail["aerial_duels"] += 1
                if rng.random() < 0.5:
                    dfn_detail["aerial_duels_ok"] += 1
                else:
                    atk_detail["aerial_duels_ok"] += 1

            plog.append({"min": float(minute), "team": side, "zone": zone, "lane": lane,
                         "outcome": "buildup", "me": False, "text": None})

    home_poss_pct = round(100.0 * home_poss_minutes / total_minutes)
    home_poss_pct = max(28, min(72, home_poss_pct))
    home_stats["poss"] = home_poss_pct
    away_stats["poss"] = 100 - home_poss_pct

    # [2026-09 신설] pass_acc는 team_stats(표시용)에 반드시 존재해야 하는
    # 값이라(MatchStatsPanel이 h_st['pass_acc']를 그대로 참조) 0으로
    # 나누는 경우까지 여기서 안전하게 처리해 확정한다. saves(표시용)는
    # 이 경기에서 뛴 GK들의 개인 saves 합으로 집계 — GK가 둘 이상 나올
    # 일은 없지만(교체 미시뮬레이션) 합으로 두면 어떤 경우에도 안전하다.
    home_stats["pass_acc"] = round(
        home_detail["passes_ok"] / home_detail["passes"], 3) if home_detail["passes"] else 0.0
    away_stats["pass_acc"] = round(
        away_detail["passes_ok"] / away_detail["passes"], 3) if away_detail["passes"] else 0.0
    home_stats["saves"] = sum(v.get("saves", 0) for v in home_player_stats.values())
    away_stats["saves"] = sum(v.get("saves", 0) for v in away_player_stats.values())

    # [선방률] 이 팀 GK가 막은 슈팅 / (막은 슈팅 + 실점) — 실점은 상대
    # 스코어와 동일(우리 골문에 들어간 공 = 상대가 넣은 골).
    home_conceded = away_score
    away_conceded = home_score
    home_detail["save_pct"] = round(
        home_stats["saves"] / (home_stats["saves"] + home_conceded), 3
    ) if (home_stats["saves"] + home_conceded) else 0.0
    away_detail["save_pct"] = round(
        away_stats["saves"] / (away_stats["saves"] + away_conceded), 3
    ) if (away_stats["saves"] + away_conceded) else 0.0

    plog.sort(key=lambda r: r["min"])
    # [2026-08 신설] 22명(양팀 라인업 슬롯 순서, 빈 슬롯은 None) 전원의
    # 개인 기록 + 평점 — ui/match_detail_dialog.py가 이 값이 있으면
    # FotMob 스타일 라인업+평점 화면을 보여준다(없으면 기존처럼 팀 단위
    # 통계만 보여주는 화면으로 자동 폴백).
    home_player_ratings = _build_player_ratings(
        home_lineup, home_player_stats, home_score, away_score, home.gk, rng, home_slots)
    away_player_ratings = _build_player_ratings(
        away_lineup, away_player_stats, away_score, home_score, away.gk, rng, away_slots)
    return {
        "home_score": home_score, "away_score": away_score,
        "home_stats": home_stats, "away_stats": away_stats,
        "home_stats_detail": home_detail, "away_stats_detail": away_detail,
        "possession_log": plog,
        "home_player_ratings": home_player_ratings,
        "away_player_ratings": away_player_ratings,
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
                       home_boost=0.0, away_boost=0.0,
                       home_boost_position=None, away_boost_position=None,
                       home_adv=3.0, seed=None):
    """team_id 두 개만 받아서 로스터/포메이션 조회부터 시뮬레이션까지 전부
    처리하는 편의 함수. game_engine._simulate_match에서 이걸 하나만 호출하면
    된다."""
    from match_sim.match_flow import _select_lineup

    home_lineup = _select_lineup(home_team_id, home_formation)
    away_lineup = _select_lineup(away_team_id, away_formation)
    return simulate_tactical_match(home_lineup, away_lineup, home_boost=home_boost,
                                    away_boost=away_boost,
                                    home_boost_position=home_boost_position,
                                    away_boost_position=away_boost_position,
                                    home_adv=home_adv, seed=seed,
                                    home_formation=home_formation,
                                    away_formation=away_formation)