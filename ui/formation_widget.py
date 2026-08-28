"""
ui/formation_widget.py
좌측: 내 팀 포메이션 캔버스
우측: 상대팀 선택 + 포메이션 캔버스
"""
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QDialog,
    QTableWidget, QTableWidgetItem, QPushButton, QComboBox,
    QSizePolicy, QFrame, QScrollArea, QGridLayout, QLineEdit
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QFont, QFontMetrics

from database import get_conn
from constants import FORMATION_SLOTS, STAT_KO, ALL_STATS, POSITION_COMPAT
from game_engine import is_hard_mode

# 승강/리스케일 후 OVR 캐시 무효화 플래그 (game_engine._invalidate_team_ovr_cache가 세팅)
_ovr_cache_invalidated: bool = False


# ─────────────────────────────────────────────
# 상대팀 데이터 조회
# ─────────────────────────────────────────────

def _players_for_team(team_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM ai_players WHERE team_id=? ORDER BY ovr DESC LIMIT 11",
        (team_id,)).fetchall()
    conn.close()
    return _mask_ai_names([dict(r) for r in rows])

def _full_squad_for_team(team_id):
    """[2026-08 신설] 포메이션 화면 아래 "전체 명단" 패널 전용 — 기존
    _players_for_team은 평균 OVR·포메이션 배치용으로 상위 11명만
    (LIMIT 11) 가져오는데, 이건 그 팀의 스쿼드 전체(벤치 포함)가
    필요하다. 다른 곳에서 쓰는 평균 OVR/시작 라인업 계산 로직은 전혀
    건드리지 않기 위해 별도 함수로 분리했다. team_id가 없으면(국제대회
    가상 국가대표팀처럼 실제 스쿼드 자체가 없는 경우) None을 반환해
    호출부가 "명단 없음"으로 처리하게 한다."""
    if not team_id:
        return None
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM ai_players WHERE team_id=? ORDER BY ovr DESC",
        (team_id,)).fetchall()
    conn.close()
    if not rows:
        return None
    return _mask_ai_names([dict(r) for r in rows])

def _mask_ai_names(rows):
    """[2026-07] 포메이션 화면에 실제 개인 이름 대신 'AI'만 표시한다 — 이미
    국제대회(월드컵) 포메이션에서 상대/동료를 가상 선수로 만들 때 쓰던
    "name":"AI" 표기를 클럽팀 포메이션에도 동일하게 적용한 것. 실제 이름
    생성(data/names.py → player_names 테이블)은 계속 그대로 두고 다른
    화면(스쿼드/이적시장/월드브라우저 등)에는 영향 없음 — 여기 포메이션
    캔버스에 넘기기 직전에만 표시용으로 name을 덮어쓴다.

    [2026-08 재작업, 신민용 리포트: "포메이션 화면에서 AI 1이 두 자리에
    겹쳐 뜬다 + 전체 몇만 명 규모면 식별 코드를 달라"] 예전엔 이 함수가
    한 번에 받은 rows 안에서만 통하는 임시 순번(1,2,3...)을 매겼다 —
    문제가 두 가지였다. (1) 같은 선수라도 호출마다(포메이션 캔버스 vs
    전체 명단 패널처럼 서로 다른 rows 묶음) 번호가 달라져 화면 간
    식별자가 안 맞았다. (2) 두 자릿수 이상 번호("AI 10", "AI 11")가
    필드 원 라벨의 4자 잘림([:4])에 걸려 앞자리만 남으면서 진짜 "AI 1"과
    화면에 똑같이 겹쳐 보였다(직접 목격된 버그). 이제 순번 대신
    constants.ai_player_code()로 ai_players.id(전세계 유일 PK)를 직접
    변환해 "AI"+코드(항상 정확히 6자, 예: "AI0007")를 이름으로 쓴다 —
    조회 범위·화면과 무관하게 같은 선수는 항상 같은 코드이고, 길이가
    고정이라 어디서도 잘려서 다른 코드와 겹칠 일이 없다.

    [2026-08 추가 수정, 신민용 리포트: "좌측(이적 로그)엔 AI (331454)로
    뜨는데 포메이션엔 AI 73QU로 따로 뜬다"] 이 코드 변환 로직을
    constants.ai_player_code()로 옮겨 ai_lifecycle.py의 이적 로그 표시와
    공유한다 — 이제 어느 화면에서든 같은 선수는 항상 같은 코드로 보인다.

    [2026-08 확장, 신민용 요청: "AICD8C 이 식별코드로 뜨는 선수의 이름을
    내가 직접 입력할 수 있게 — 포메이션에도 내가 지은 이름으로 뜨지만
    코드로도 계속 검색은 가능해야 한다"] 사용자가 이름을 지어준 선수는
    (world_browser.py "선수 검색"의 이름 헤더 클릭 → 이름 변경) 코드
    대신 그 이름을 쓴다. rows 전체 id를 한 번에 모아 배치 조회하므로
    (get_ai_player_custom_names) 포메이션에 11~23명이 한 번에 뜨는
    상황에서도 선수마다 따로 쿼리하지 않는다 — 이름이 없는 선수만
    기존처럼 ai_player_code로 폴백.
    """
    from constants import ai_player_code
    from database import get_ai_player_custom_names
    ids = [r.get("id") for r in rows if r.get("id") is not None]
    custom_names = get_ai_player_custom_names(ids)
    for r in rows:
        pid = r.get("id")
        if pid is None:
            r["name"] = "AI"
        else:
            r["name"] = custom_names.get(pid) or ai_player_code(pid)
    return rows

def _avg_ovr(players):
    """[2026-08 수정, 신민용 리포트: "우리팀은 0.1단위인데 상대는 45처럼
    정수로 뜬다"] 예전엔 round()로 정수까지만 남겼는데, 내 팀 쪽
    (_FormationCanvas._calc_avg_ovr)은 이미 소수 1자리까지 보여주도록
    고쳐놔서 좌우 표시 정밀도가 어긋나 있었다. 이 함수의 결과는 상대팀
    목록(리그/컵/챔스/유로파/컨퍼런스/슈퍼컵/클럽월드컵 전부 이 함수
    하나를 공유)의 avg_ovr로 그대로 쓰이므로, 여기서 소수 1자리를
    유지하면 모든 대회의 상대팀 표시가 한 번에 통일된다."""
    if not players: return 0
    return round(sum(p["ovr"] for p in players) / len(players), 1)

def _fetch_league_opponents(my_team_id, league_id):
    conn = get_conn()
    teams = [dict(r) for r in conn.execute(
        "SELECT t.id, t.name, t.formation FROM teams t "
        "WHERE t.league_id=? AND t.id!=?", (league_id, my_team_id)).fetchall()]
    conn.close()
    result = []
    for t in teams:
        players = _players_for_team(t["id"])
        result.append({
            "team_id":   t["id"],
            "name":      t["name"],
            "flag":      "",
            "avg_ovr":   _avg_ovr(players),
            "formation": t.get("formation") or "4-4-2",
            "players":   players,
        })
    return result

def _make_intl_virtual_players(avg_ovr: float) -> list:
    """[폴백 전용] 실제 국적 선수 풀이 얇을 때만 쓰는 가상 선수 11명."""
    import random
    pos_list = ["GK", "CB", "CB", "LB", "RB", "CM", "CM", "CAM", "LW", "RW", "ST"]
    result = []
    for i, pos in enumerate(pos_list):
        ovr_v = max(30, min(99, round(avg_ovr) + random.randint(-5, 5)))
        result.append({"id": -(i+100), "name": f"AI {i+1}", "position": pos,
                        "ovr": ovr_v, "is_me": False, "club": "",
                        **{s: ovr_v for s in ALL_STATS}})
    return result


# [2026-08 신설, 신민용 리포트: "월드컵도 주전/후보가 있어야 하는데 왜
# 11명만 뽑히냐"] 4-4-2 주전 11명(GK1/DF4/MF4/FW2) 기준으로, 대표팀
# 23인(INTL_SQUAD_QUOTA: GK3/DF8/MF8/FW4) 규정을 채우는 데 부족한
# 후보 12명(GK2/DF4/MF4/FW2)의 포지션 목록. get_country_squad_players가
# 받는 positions 리스트 뒤에 그대로 이어붙여서 한 번에 23명을 뽑는다.
# [2026-08 버그수정, 신민용 리포트: "쿠바 국대 후보가 9명뿐이다"] 여기
# "RM"을 넣어뒀는데, 실제로 이 게임의 club 선수 생성 코드(TEAM_POSITIONS/
# _build_squad_positions)는 애초에 좌우 미드필더를 "LW"/"RW"로만 만들고
# "LM"/"RM" 포지션 자체를 절대 생성하지 않는다(실측: ai_players.position
# 값 전체를 세어보면 LM/RM이 0명 — CB 33987, ST/RB/LW/GK/CM/CAM 각
# 22658, RW/LB/CDM 각 11329뿐). 그래서 "RM" 후보 자리는 이 세상 어떤
# 나라로도 100% 항상 채워질 수 없는 슬롯이었다 — 나라가 약해서가
# 아니라 애초에 존재하지 않는 포지션을 요청한 게 원인. 실제 생성되는
# 포지션인 "CM"으로 교체한다.
# [2026-08 확장, 신민용 요청: "26명 엔트리 기준 GK3/DF8~9/MF8~9/FW5~6"]
# 예전 23명(GK3/DF8/MF9/FW3)은 GK·DF·MF는 이미 요청 범위 안이었지만
# FW가 크게 부족했다(3명) — 후보 쪽 FW를 2명→5명으로 늘려서 최종
# 스쿼드가 GK3/DF8/MF9/FW6=26명이 되게 한다.
_INTL_BENCH_POSITIONS = ["GK", "GK", "CB", "CB", "LB", "RB",
                         "CDM", "CM", "CAM", "CM", "ST", "ST", "ST", "ST", "ST"]
# [2026-08 신설, 신민용 요청: "예선 때 뽑은 26명 그대로 본선까지"] 대회
# 전체(주전+후보 26명)를 한 번에 뽑을 때 쓰는 포지션 리스트 — 아래
# get_or_create_intl_squad 계열 함수와 기존 _make_intl_real_players(폴백
# 경로용으로 계속 남겨둠)가 공유한다.
_INTL_STARTER_POSITIONS = ["GK", "CB", "CB", "LB", "RB", "CM", "CM", "CAM", "LW", "RW", "ST"]
_INTL_FULL_SQUAD_POSITIONS = _INTL_STARTER_POSITIONS + _INTL_BENCH_POSITIONS


def _intl_players_from_rows(picked):
    """[2026-08 신설] get_country_squad_players/get_or_create_intl_squad가
    돌려준 raw row들을 포메이션 화면이 쓰는 선수 dict 형태로 통일 변환
    (이름 커스텀 적용 포함) — _make_intl_real_players/_make_intl_persistent_
    players가 공유."""
    from database import get_ai_player_custom_names
    from constants import ai_player_code
    custom_names = get_ai_player_custom_names([r["id"] for r in picked])
    result = []
    for r in picked:
        result.append({"id": r["id"], "name": custom_names.get(r["id"]) or ai_player_code(r["id"]),
                        "position": r["position"],
                        "ovr": r["ovr"], "is_me": False, "club": r["club"],
                        "club_tier": r.get("club_tier"), "club_country": r.get("club_country"),
                        "age": r.get("age"), "appearances": r.get("appearances", 0),
                        **{s: r.get(s, r["ovr"]) for s in ALL_STATS}})
    return result


def _make_intl_persistent_players(tournament_id, country: str, avg_ovr: float):
    """[2026-08 신설, 신민용 요청: "예선전 때 뽑은 애들 그대로 본선까지
    가는거야 — 지금은 경기할 때마다 국대 26명이 매번 새로 뽑힌다, 이러면
    안돼"] database.get_or_create_intl_squad로 이 대회(tournament_id)의
    이 나라(country) 26인을 (있으면) 그대로 재사용, (없으면) 처음 한 번
    뽑아서 고정한다 — _make_intl_real_players(매번 새로 뽑음)를 국제대회
    문맥이 있는 모든 호출부에서 대체한다. tournament_id가 없는 예외적인
    경우(문맥 없이 단독 호출 등)에는 기존 _make_intl_real_players로
    자동 폴백."""
    if not tournament_id:
        return _make_intl_real_players(country, avg_ovr)
    from database import get_or_create_intl_squad
    picked = get_or_create_intl_squad(tournament_id, country, avg_ovr, _INTL_FULL_SQUAD_POSITIONS)
    if len(picked) < 8:
        return None
    return _intl_players_from_rows(picked)


def _make_intl_real_players(country: str, avg_ovr: float):
    """[2026-07 재조정, 신민용 지적: "8명 미만 나라는 자국 1부나 남의 나라
    2부에서도 채울 수 있다"] database.get_country_squad_players()의
    3단계 폴백(국적태그→자국리그→해외 하위리그 대륙우선)을 그대로
    쓴다 — 클릭 시 재쿼리하는 구조가 아니라 화면 로드 시 1회만 조회.

    [2026-08 버그수정, 신민용 리포트: "리그 선수는 스탯이 들쭉날쭉한데
    국제대회 선수는 전 스탯이 OVR과 똑같이 뜬다"] get_country_squad_players
    가 이제 개별 스탯 컬럼을 실제로 SELECT해서 주므로(예전엔 ovr 하나뿐이라
    아래서 전 스탯을 ovr로 채워 넣었었다), 그 실제 값을 그대로 쓴다.

    [2026-08 신설, 신민용 리포트: "월드컵도 주전/후보가 있어야 하는데
    11명만 뽑힌다"] 4-4-2 주전 11명 자리에 _INTL_BENCH_POSITIONS(15명)를
    이어붙여 한 번에 26명(GK3/DF8/MF9/FW6)을 뽑는다 — 앞 11개가
    주전, 나머지가 후보로 반환된다(호출부가 순서 그대로 나눠 씀).
    min_count는 여전히 8 그대로(주전 자리 최소 보장 기준) — 후보 자리는
    부족해도 그냥 그만큼만 덜 채워져서 반환되며, 이 경우 라도 주전
    11명은 이미 확보돼 있으므로 화면이 깨지지 않는다.

    [2026-08 신설] tournament_id가 있는 문맥에서는 이제 이 함수 대신
    _make_intl_persistent_players(대회 내내 26인 고정)를 쓴다 — 이
    함수는 그 폴백 경로 및 tournament_id 없는 예외적 단독 호출용으로만
    남는다."""
    from database import get_country_squad_players
    picked = get_country_squad_players(country, positions=_INTL_FULL_SQUAD_POSITIONS,
                                        min_count=8, target_ovr=round(avg_ovr))
    if len(picked) < 8:
        return None
    return _intl_players_from_rows(picked)


def _fetch_intl_opponents(tournament_id, my_nat, grp=None):
    """국제대회 상대팀 목록.
    grp 지정 시 내 조(grp) 팀만 반환 (조별리그).
    grp 없으면 대회 전체 참가국 반환 (fallback).
    [2026-07 수정] players는 이제 실제 국적 선수(_make_intl_real_players)를
    우선 쓰고, 실제 선수 풀이 얇은 나라만 가상 선수로 폴백한다.
    """
    conn = get_conn()
    if grp:
        rows = conn.execute(
            "SELECT country, flag, ovr FROM intl_entries "
            "WHERE tournament_id=? AND country!=? AND grp=?",
            (tournament_id, my_nat, grp)).fetchall()
    else:
        rows = conn.execute(
            "SELECT country, flag, ovr FROM intl_entries "
            "WHERE tournament_id=? AND country!=?",
            (tournament_id, my_nat)).fetchall()
    conn.close()
    result = []
    for r in rows:
        avg = r["ovr"] or 50
        players = _make_intl_persistent_players(tournament_id, r["country"], avg) or _make_intl_virtual_players(avg)
        # [2026-08 재수정, 신민용 명확화: "국대는 합을 맞춰본 선수들이
        # 아니니 팀 전체 OVR은 계산치(포메이션/케미 반영)로 가는 게
        # 맞고, 대신 실제 11명은 각자 소속팀에서 잘하는 진짜 선수여야
        # 한다 — 내가 뽑히면 거기에 보너스가 붙는 개념"] 바로 전 수정에서
        # 헤더를 실제 명단 평균으로 바꿨었는데, 그건 이 설계 의도와
        # 반대 방향이었다 — 국가대표 헤더의 "평균 OVR"은 원래도 개인
        # 능력의 단순 평균이 아니라 팀 전체 완성도(케미) 개념이라, 실제
        # 개별 선수가 그보다 강해도(잘하는 선수를 뽑아왔으니) 문제가
        # 아니다. 헤더는 다시 intl_entries.ovr(계산치) 그대로 쓰고, 지난
        # 수정에서 손댄 "명단 자체가 국적과 안 맞고 아무 OVR나 뽑히는"
        # 문제만 target_ovr 매칭(get_country_squad_players)으로 계속
        # 잡는다 — 그건 여전히 유효한 수정이다.
        result.append({
            "team_id":   None,
            "name":      r["country"],
            "flag":      r["flag"] or "",
            "avg_ovr":   round(avg, 1),
            "formation": "4-4-2",
            "players":   players,
        })
    return result

def _fetch_intl_ko_opp(tournament_id, my_nat, week):
    conn = get_conn()
    m = conn.execute(
        "SELECT * FROM intl_matches WHERE tournament_id=? AND week=? "
        "AND home_score=-1 AND (home=? OR away=?)",
        (tournament_id, week, my_nat, my_nat)).fetchone()
    if not m:
        conn.close(); return None
    opp = m["away"] if m["home"] == my_nat else m["home"]
    fr = conn.execute(
        "SELECT flag, ovr FROM intl_entries WHERE tournament_id=? AND country=?",
        (tournament_id, opp)).fetchone()
    conn.close()
    avg = fr["ovr"] if fr and fr["ovr"] else 50
    # [2026-08 버그수정, 신민용 리포트: "토너먼트(1대1)일 때 상대 국가
    # 선수단이 전부 AI 1~11 가짜 선수로만 뜬다 — 실제 존재하는 AI가
    # 아니고 스탯도 전부 OVR 그대로(예: OVR90이면 전 스탯 90), 후보도
    # 없다"] 조별리그용 _fetch_intl_opponents는 진작에 "실제 국적
    # 선수 우선, 얇은 나라만 가상 폴백"(_make_intl_real_players(...) or
    # _make_intl_virtual_players(...))으로 고쳐져 있었는데, 토너먼트(1대1)
    # 대진용인 이 함수만 그 수정이 안 들어가 있어서 항상 가상 선수
    # (_make_intl_virtual_players, 정확히 11명만·후보 없음·전 스탯이
    # OVR과 동일)로 직행하고 있었다 — 같은 패턴으로 통일한다.
    players = _make_intl_persistent_players(tournament_id, opp, avg) or _make_intl_virtual_players(avg)
    return [{"team_id": None, "name": opp,
             "flag": fr["flag"] if fr else "",
             "avg_ovr": round(avg),
             "formation": "4-4-2",
             "players": players}]

# [2026-08 신설, 신민용 리포트: "우측 상대팀 포메이션 확인 기능이 월드컵
# 외 국제대회(클럽 월드컵)에서는 안 뜬다"] _resolve_opponents가 intl/cl만
# 처리하고 cwc는 분기 자체가 없어서 else(리그 상대팀) 폴백으로 빠져
# 엉뚱한(내 리그) 상대팀 목록이 뜨고 있었다 — cl_entries/cl_matches와
# 완전히 동일한 패턴으로 cwc_entries/cwc_matches용을 추가한다.
# [2026-08 v3.3 신설, 신민용 리포트: "챔스랑 리그가 겹치면 우측 포메이션이
# 바뀌는 것처럼, 유로파/컨퍼런스/슈퍼컵/국내컵도 똑같이 대회 일정에 맞춰
# 상대팀 선택지가 나와야 한다"] cl/el/ecl/cwc 4개 대회는 entries 테이블
# 스키마가 완전히 동일(team_id, team_name, flag, ovr, grp)해서 조별리그
# 상대 목록도 테이블 접두사만 바꾸는 공용 함수로 처리할 수 있다. 슈퍼컵은
# sc_entries에 grp 컬럼이 아예 없어(4팀·준결승부터 시작이라 조별리그
# 자체가 없음) 이 그룹 목록 함수는 안 쓰고 KO 조회만 쓴다.
_CLUB_COMP_PREFIX = {"champions": "cl", "europa": "el", "conference": "ecl", "super_cup": "sc"}


def _fetch_club_group_opponents(prefix, tournament_id, my_team_id, grp=None):
    """cl/el/ecl/cwc 공용 — 조별(리그페이즈) 상대 목록. grp 지정 시 내 조만."""
    conn = get_conn()
    table = f"{prefix}_entries"
    if grp:
        rows = conn.execute(
            f"SELECT team_id, team_name, flag, ovr FROM {table} "
            f"WHERE tournament_id=? AND team_id!=? AND grp=?",
            (tournament_id, my_team_id, grp)).fetchall()
    else:
        rows = conn.execute(
            f"SELECT team_id, team_name, flag, ovr FROM {table} "
            f"WHERE tournament_id=? AND team_id!=?",
            (tournament_id, my_team_id)).fetchall()
    conn.close()
    result = []
    for r in rows:
        players = _players_for_team(r["team_id"])
        avg = _avg_ovr(players) or round(r["ovr"] or 0)
        result.append({
            "team_id":   r["team_id"],
            "name":      r["team_name"],
            "flag":      r["flag"] or "",
            "avg_ovr":   avg,
            "formation": "4-4-2",
            "players":   players,
        })
    return result


def _fetch_club_ko_opp(prefix, tournament_id, my_team_id, week):
    """cl/el/ecl/cwc/sc 공용 — 이번 주(토너먼트/스위스리그 페이즈 등)
    상대 1팀만. 스키마가 전부 동일(team_id/team_name/flag/ovr)해서
    슈퍼컵(sc)도 그대로 쓸 수 있다."""
    conn = get_conn()
    mtable, etable = f"{prefix}_matches", f"{prefix}_entries"
    m = conn.execute(
        f"SELECT * FROM {mtable} WHERE tournament_id=? AND week=? "
        f"AND home_score=-1 AND (home_team_id=? OR away_team_id=?)",
        (tournament_id, week, my_team_id, my_team_id)).fetchone()
    if not m:
        conn.close(); return None
    opp_id = m["away_team_id"] if m["home_team_id"] == my_team_id else m["home_team_id"]
    e = conn.execute(
        f"SELECT team_name, flag, ovr FROM {etable} WHERE tournament_id=? AND team_id=?",
        (tournament_id, opp_id)).fetchone()
    conn.close()
    if not e: return None
    players = _players_for_team(opp_id)
    avg = _avg_ovr(players) or round(e["ovr"] or 0)
    return [{"team_id": opp_id, "name": e["team_name"],
             "flag": e["flag"] or "", "avg_ovr": avg,
             "formation": "4-4-2", "players": players}]


def _fetch_cup_ko_opp(tournament_id, my_team_id, week):
    """[2026-08 v3.3 신설] 국내컵 — 조별리그 자체가 없는 순수 토너먼트라
    항상 이번 주 상대 1팀만. cup_entries엔 flag 컬럼이 없다(국내컵이라
    전부 같은 나라라 애초에 불필요)."""
    conn = get_conn()
    m = conn.execute(
        "SELECT * FROM cup_matches WHERE tournament_id=? AND week=? "
        "AND home_score=-1 AND (home_team_id=? OR away_team_id=?)",
        (tournament_id, week, my_team_id, my_team_id)).fetchone()
    if not m:
        conn.close(); return None
    opp_id = m["away_team_id"] if m["home_team_id"] == my_team_id else m["home_team_id"]
    e = conn.execute(
        "SELECT team_name, ovr FROM cup_entries WHERE tournament_id=? AND team_id=?",
        (tournament_id, opp_id)).fetchone()
    conn.close()
    if not e: return None
    players = _players_for_team(opp_id)
    avg = _avg_ovr(players) or round(e["ovr"] or 0)
    return [{"team_id": opp_id, "name": e["team_name"],
             "flag": "", "avg_ovr": avg,
             "formation": "4-4-2", "players": players}]


def _fetch_cwc_opponents(tournament_id, my_team_id, grp=None):
    """클럽 월드컵 상대팀 목록. grp 지정 시 내 조 팀만 반환(조별리그)."""
    conn = get_conn()
    if grp:
        rows = conn.execute(
            "SELECT team_id, team_name, flag, ovr FROM cwc_entries "
            "WHERE tournament_id=? AND team_id!=? AND grp=?",
            (tournament_id, my_team_id, grp)).fetchall()
    else:
        rows = conn.execute(
            "SELECT team_id, team_name, flag, ovr FROM cwc_entries "
            "WHERE tournament_id=? AND team_id!=?",
            (tournament_id, my_team_id)).fetchall()
    conn.close()
    result = []
    for r in rows:
        players = _players_for_team(r["team_id"])
        avg = _avg_ovr(players) or round(r["ovr"] or 0)
        result.append({
            "team_id":   r["team_id"],
            "name":      r["team_name"],
            "flag":      r["flag"] or "",
            "avg_ovr":   avg,
            "formation": "4-4-2",
            "players":   players,
        })
    return result

def _fetch_cwc_ko_opp(tournament_id, my_team_id, week):
    conn = get_conn()
    m = conn.execute(
        "SELECT * FROM cwc_matches WHERE tournament_id=? AND week=? "
        "AND home_score=-1 AND (home_team_id=? OR away_team_id=?)",
        (tournament_id, week, my_team_id, my_team_id)).fetchone()
    if not m:
        conn.close(); return None
    opp_id = m["away_team_id"] if m["home_team_id"] == my_team_id else m["home_team_id"]
    e = conn.execute(
        "SELECT team_name, flag, ovr FROM cwc_entries WHERE tournament_id=? AND team_id=?",
        (tournament_id, opp_id)).fetchone()
    conn.close()
    if not e: return None
    players = _players_for_team(opp_id)
    avg = _avg_ovr(players) or round(e["ovr"] or 0)
    return [{"team_id": opp_id, "name": e["team_name"],
             "flag": e["flag"] or "", "avg_ovr": avg,
             "formation": "4-4-2", "players": players}]


# ─────────────────────────────────────────────
# 포메이션 캔버스 (내 팀 / 상대팀 공용)
# ─────────────────────────────────────────────

class _FormationCanvas(QWidget):
    def __init__(self, is_opponent=False):
        super().__init__()
        self._is_opp  = is_opponent
        self.formation = "4-4-2"
        self.players   = []
        self._player_at: dict = {}
        self._positions_xy: list = []
        self._hovered_slot = -1
        # [2026-08 신설] 명단 패널 추가로 캔버스 자체는 절반 크기로
        # 줄어들 수 있으므로, 최소 높이도 그에 맞춰 낮춘다(기존 300 →
        # 160). 원/글자 크기는 paintEvent에서 실제 폭·높이에 맞춰
        # 동적으로 계산한다(_calc_positions가 self._circle_d에 저장).
        self.setMinimumHeight(160)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color:#1a3a1a;border-radius:6px;")
        self.setMouseTracking(True)
        self._circle_d = 48          # paintEvent가 실제 크기에 맞춰 갱신
        self._roster: list = []      # [2026-08 신설] 전체 스쿼드(명단 패널용)
        self._starter_ids: set = set()  # 그 중 지금 포메이션에 들어간 선수 id
        # [2026-08 신설] 국제전일 때만 채워지는 "팀 전체 계산치(케미 반영)"
        # OVR — 헤더 표시용. club 매치 땐 None(그때는 실제 로스터 평균 사용).
        self._intl_formula_ovr = None

    def _calc_avg_ovr(self, ndigits=0):
        """현재 로드된 선수들의 평균 OVR.
        [2026-07 재수정, 신민용 지적] "나"를 다시 평균에 포함시킨다.
        이전엔 나를 제외했는데(약한 내가 무조건 라인업에 꽂히던 옛 버그
        때문에 그렇게 했었음), 이제 load_my_team()이 "내가 그 자리 기존
        선수보다 나을 때만" 나를 넣도록 바뀌었으므로 — 내가 리스트에
        있다는 것 자체가 이미 "그 자리 최선의 선택"이라는 뜻이라 평균에
        넣어도 더 이상 실제 팀 수준을 왜곡하지 않는다(오히려 빼면 내
        업그레이드 효과가 화면에 전혀 반영되지 않는 문제가 생김).
        ndigits: 반환 소수 자릿수. 계산 자체는 항상 2자리까지 유지하고,
        표시용으로 호출하는 쪽에서 반올림 자릿수를 지정한다(기본 0=정수 표시)."""
        if not self.players: return 0
        ovrs = []
        for p in self.players:
            v = p.get("ovr", 0)
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = 0
            if 1 <= v <= 100:   # 비정상값 제외
                ovrs.append(v)
        if not ovrs: return 0
        precise = round(sum(ovrs) / len(ovrs), 2)   # 내부 계산은 항상 소수 2자리
        return round(precise, ndigits) if ndigits else round(precise)

    def load_my_team(self, team_id, intl_nat: str = "", tournament_id=None):
        """리그팀 또는 국가대표팀 로드.
        intl_nat이 있으면 그 국가 intl_entries 기준으로 포메이션을 그린다.
        [최적화] (team_id, intl_nat, tournament_id) 키로 캐시 — refresh()마다
        동일 팀 재쿼리 방지. 캐시는 FormationWidget 레벨(_my_team_cache)에서 관리.

        [2026-08 신설, 신민용 요청: "상대팀(AI 국가)은 대회 내내 26인이
        고정되는데, 내 대표팀 나머지 25명은 여전히 화면 열 때마다 새로
        뽑힌다 — 이것도 고정해야 한다"] tournament_id가 있으면(국제대회
        문맥) database.get_or_create_intl_squad로 이 대회에서 이미 고정된
        (또는 처음이면 새로 고정하는) 26인 풀에서 내 슬롯을 뺀 나머지를
        채운다 — 상대팀이 쓰는 것과 완전히 동일한 영구 명단 소스라, 나를
        뺀 25명은 이 대회가 끝날 때까지 항상 같다. tournament_id가 없는
        예외적인 경우(문맥 없이 단독 호출 등)에는 기존처럼 매번 새로
        뽑는 get_country_squad_players로 폴백한다.
        """
        from game_engine import get_player
        p = get_player()

        # 캐시 키: 내 선수 OVR/포지션/부상 상태도 반영 (레벨업·부상/회복 시 캐시 무효화)
        # [2026-08 버그수정, 신민용 리포트: "부상 한번 걸리고 나면 부상이
        # 없어도 포메이션이 빨갛게 뜰 때가 있다"] 예전엔 (ovr, position)만
        # 서명에 들어가 있었다 — 부상이 생기거나 나아도 보통 OVR·포지션은
        # 그대로라서, 부상 상태(빨간색)로 한 번 캐싱된 화면이 회복 이후에도
        # 캐시 키가 안 바뀌어 그대로 재사용됐다(회복해도 계속 빨간 화면).
        # injured를 서명에 포함시켜 부상/회복 전환마다 캐시가 갈리게 한다.
        _p_sig = (p.get("ovr", 0), p.get("position", ""), bool(p.get("injured"))) if p else (0, "", False)
        _cache_key = (team_id, intl_nat, tournament_id, _p_sig)
        # 부모(FormationWidget)의 캐시에 접근
        _widget = self.parent()
        while _widget and not hasattr(_widget, "_my_team_cache"):
            _widget = _widget.parent() if hasattr(_widget, "parent") else None
        _cache = getattr(_widget, "_my_team_cache", None)

        # [최적화] 승강/리스케일 후 OVR이 바뀌면 캐시 전체 무효화
        import ui.formation_widget as _self_mod
        if _self_mod._ovr_cache_invalidated:
            if _cache is not None:
                _cache.clear()
            _self_mod._ovr_cache_invalidated = False

        if _cache is not None and _cache_key in _cache:
            self.formation, self.players, self._roster, self._starter_ids, self._intl_formula_ovr = _cache[_cache_key]
            self._player_at = {}; self._positions_xy = []
            self.update()
            return

        if intl_nat:
            # ── 국제전: 내 국가대표팀 선수 구성 ──
            # [2026-08 수정] 예전엔 "nationality1 기준으로 ai_players를
            # 국가별로 뽑을 수 없다"는 이유로 무조건 가상 11명을 만들었는데,
            # 지금은 get_country_squad_players로 실제 그 국적(또는 폴백)
            # 선수를 뽑는다 — 아래 실제 채움 로직 참고. 나(my_player)는
            # 항상 실제 스탯 사용.
            self.formation = "4-4-2"
            import random

            conn = get_conn()
            entry = conn.execute(
                "SELECT ovr FROM intl_entries WHERE country=? LIMIT 1",
                (intl_nat,)).fetchone()
            conn.close()
            avg_ovr = round(entry["ovr"]) if entry and entry["ovr"] else (p.get("ovr", 50) if p else 50)
            # [2026-08 신설, 신민용 요청: "국대 팀 전체 OVR은 합을 맞춰본
            # 적 없는 임시 소집이니 계산치(케미 반영)로, 내가 뽑히면 거기에
            # 보너스"] 헤더에 뜨는 "평균 OVR"은 아래에서 채우는 실제 선수
            # 명단의 단순 평균이 아니라 이 계산치를 그대로 써야 하므로,
            # FormationWidget._apply_context가 헤더 텍스트를 만들 때 쓸 수
            # 있도록 캔버스에 보관해둔다(club 매치 땐 None — 그때는 기존대로
            # _calc_avg_ovr()의 실제 로스터 평균을 쓴다).
            self._intl_formula_ovr = avg_ovr

            # [2026-08 버그수정, 신민용 리포트: "ST → AI [GK]"처럼 포메이션
            # 슬롯과 실제 선수 표시가 어긋난다] 예전엔 "GK 항상 포함 + 나머지
            # 고정 순서(others)" 리스트를 그냥 만들어서 paintEvent가 화면
            # 순서대로 대충 짝짓게 맡겼다 — 그 리스트가 실제 4-4-2 슬롯
            # 순서/구성과 일치한다는 보장이 없었다(게다가 paintEvent가 쓰는
            # 화면 표시 순서와도 다시 어긋남). 이제 실제 FORMATION_SLOTS의
            # "4-4-2"를 그대로 순회하며, 내 슬롯(_best_slot_for_player로
            # 결정)만 비우고 나머지 슬롯 각각에 그 슬롯의 실제 포지션을
            # 가진 AI를 만든다 — 슬롯 인덱스(_slot_idx)를 그대로 태그해서
            # paintEvent가 순서에 의존하지 않고 원본 슬롯으로 직접 매칭한다.
            slots_only = FORMATION_SLOTS["4-4-2"]
            my_pos = p.get("position", "CM") if p else "CM"
            my_slot_idx, _field_pos, _mismatch = _best_slot_for_player(my_pos, slots_only)

            players = []
            if p:
                me = {"id": -1, "name": p.get("name", "나"),
                      "position": my_pos, "_slot_idx": my_slot_idx,
                      "ovr": p.get("ovr", 40), "is_me": True,
                      "injured": bool(p.get("injured")),
                      "age": p.get("age", 0), "nationality": p.get("nationality", ""),
                      **{s: p.get(s, 0) for s in ALL_STATS}}
                players.append(me)

            # [2026-08 버그수정, 신민용 리포트: "국대 가면 가상의 선수를
            # 새로 창조하는데, 현실은 다른 팀에 나가있는 우리나라 선수들
            # 중 잘하는 사람들이 뽑혀야 한다 — 전체 OVR(팀 케미 반영,
            # 오래 손발 안 맞춰본 임시 소집이라는 의미)은 계산치를 그대로
            # 쓰고, 대신 실제로 보여주는 11명은 진짜 그 나라 선수여야
            # 한다"] 예전엔 이 자리에 "ai_players를 국가별로 뽑을 방법이
            # 없다"는 이유로 순수 가상 생성만 있었는데, 지난 국제대회
            # 상대팀 수정 때 만든 get_country_squad_players(국적 태그 →
            # 자국 리그 → 해외 하위리그 순 폴백, target_ovr로 팀 평균과
            # 동떨어지지 않게 매칭)가 지금은 있다 — 상대팀에만 쓰고 내
            # 팀에는 안 옮겨놨던 게 이 버그였다. 이제 내 슬롯을 뺀 나머지
            # 포지션들을 그 함수로 채우고, 부족할 때만(8명 미만) 예전
            # 가상 생성으로 폴백한다.
            # [2026-08 신설, 신민용 리포트: "월드컵도 주전/후보가 있어야
            # 하는데 11명만 뽑힌다"] remaining_slots(주전 자리) 뒤에
            # _INTL_BENCH_POSITIONS(후보 12명 자리)를 이어붙여 한 번에
            # 조회한다 — 앞부분은 주전(포메이션에 배치), 뒷부분은 후보
            # (명단 패널에만 표시, 필드엔 안 나감)로 나눠서 쓴다.
            remaining_slots = [(i, sp) for i, sp in enumerate(slots_only)
                                if not (p and i == my_slot_idx)]
            from database import get_country_squad_players, get_or_create_intl_squad, get_ai_player_custom_names
            from constants import ai_player_code
            if intl_nat and tournament_id:
                # [2026-08 신설] 대회 내내 고정되는 26인 풀(상대팀과 동일
                # 소스) — 나(me)는 이 풀에 아예 없으므로(ai_players가
                # 아니라 별도 존재) 그대로 써도 "나를 뺀 25명 고정"이
                # 자연히 성립한다. 아래 매칭 로직은 이 26명 중 내 슬롯
                # 포지션을 요구하지 않으므로(remaining_slots가 이미 내
                # 자리를 뺐음) 그 자리에 맞는 선수는 그냥 후보로 남는다.
                picked = get_or_create_intl_squad(
                    tournament_id, intl_nat, avg_ovr, _INTL_FULL_SQUAD_POSITIONS)
            else:
                picked = get_country_squad_players(
                    intl_nat, positions=[sp for _, sp in remaining_slots] + _INTL_BENCH_POSITIONS,
                    min_count=8, target_ovr=avg_ovr) if intl_nat else []
            bench_players = []
            if len(picked) >= 8:
                # [2026-08 버그수정, 신민용 리포트: "국대 후보가 10명인데
                # 12명이어야 한다 — 상대팀(프랑스)은 12명 다 맞는데 내
                # 나라(조지아)만 부족하다"] get_country_squad_players는
                # 못 채운 자리를 조용히 건너뛰고 반환한다([None 제거]) —
                # 그래서 반환된 리스트는 "요청한 순서 그대로"가 아니라
                # "채워진 것만 압축"된 상태다. 예전엔 이걸 모르고
                # picked[:주전수]/picked[주전수:]로 단순히 앞/뒤를 잘랐는데,
                # 조지아처럼 약한 나라라 중간에 한두 자리가 못 채워지면
                # (자국 풀이 얇아 특정 포지션 후보가 소진됨) 뒤쪽 항목들이
                # 전부 한 칸씩 당겨져서 원래 "후보"였을 선수가 "주전"
                # 칸으로 잘못 들어가고, 그만큼 후보 목록에서 통째로 사라졌다
                # — 프랑스처럼 풀이 넉넉한 나라는 애초에 다 채워지니 이
                # 어긋남이 안 보였을 뿐, 실제로는 어느 나라든 하나라도
                # 못 채우면 발생하는 구조적 버그였다. 이제 순서에 의존하지
                # 않고, "포지션이 일치하는 선수를 하나씩 꺼내 쓰는" 방식으로
                # 주전/후보를 나눈다 — 못 채운 자리가 있어도 나머지가
                # 밀리지 않는다.
                pool = list(picked)
                # [2026-08 신설, 신민용 요청: "여기도 내가 지은 이름으로
                # 뜨게"] 배치 조회로 N+1 방지(_mask_ai_names와 동일 원칙).
                custom_names = get_ai_player_custom_names([r["id"] for r in pool])
                starter_picked = []
                for i, sp in remaining_slots:
                    match_idx = next((k for k, r in enumerate(pool) if r["position"] == sp), None)
                    starter_picked.append(pool.pop(match_idx) if match_idx is not None else None)
                bench_picked = pool  # 주전 배정에 안 쓰인 나머지 전부가 후보
                for (i, sp), r in zip(remaining_slots, starter_picked):
                    if r is None:
                        continue
                    players.append({"id": r["id"], "name": custom_names.get(r["id"]) or ai_player_code(r["id"]),
                                     "position": sp, "_slot_idx": i,
                                     "ovr": r["ovr"], "is_me": False,
                                     "club": r["club"], "club_tier": r.get("club_tier"),
                                     "club_country": r.get("club_country"),
                                     "age": r.get("age"),
                                     **{s: r.get(s, r["ovr"]) for s in ALL_STATS}})
                for r in bench_picked:
                    bench_players.append({"id": r["id"], "name": custom_names.get(r["id"]) or ai_player_code(r["id"]),
                                     "position": r["position"], "ovr": r["ovr"], "is_me": False,
                                     "club": r["club"], "club_tier": r.get("club_tier"),
                                     "club_country": r.get("club_country"),
                                     "age": r.get("age"),
                                     **{s: r.get(s, r["ovr"]) for s in ALL_STATS}})
            else:
                for i, sp in remaining_slots:
                    ovr_v = max(30, min(99, avg_ovr + random.randint(-4, 4)))
                    base = {s: ovr_v for s in ALL_STATS}
                    players.append({"id": -(i+2), "name": f"AI {i+1}", "position": sp,
                                     "_slot_idx": i, "ovr": ovr_v, "is_me": False, **base})
            self.players = players
            # [2026-08 신설] 명단 패널용 — 주전(players) + 후보(bench_players).
            # bench_players는 실제 선수 풀이 8명 미만이라 가상 폴백을 탄
            # 경우엔 비어있다(가상 필러는 벤치 데이터 자체가 없다는 뜻).
            self._roster = players + bench_players
            self._starter_ids = {pl.get("id") for pl in players}
        else:
            # ── 리그팀 ──
            conn = get_conn()
            row = conn.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
            self.formation = row["formation"] if row else "4-4-2"
            self._intl_formula_ovr = None
            my_tid = p.get("current_team_id", 0) if p else 0
            if my_tid == team_id and p:
                # [2026-07 수정] "나를 빼고 베스트11을 먼저 짠 뒤, 내 자리에 있던
                # 선수와 비교해서 내가 더 나을 때만 들어간다"는 원칙으로 재설계.
                # 기존엔 내 실력과 무관하게 무조건 [나]+AI10명으로 라인업을 짰음.
                slots_only = FORMATION_SLOTS.get(self.formation, FORMATION_SLOTS["4-4-2"])
                all_ai = _mask_ai_names([dict(r) for r in conn.execute(
                    "SELECT * FROM ai_players WHERE team_id=? ORDER BY ovr DESC",
                    (team_id,)).fetchall()])

                # 1) 나를 제외한 베스트11: 포지션 호환 우선순위로 그리디 배정
                #    (OVR 높은 순으로 훑으며 자신에게 가장 잘 맞는 빈 슬롯을 차지)
                # [2026-08 버그수정] AI 11명 슬롯 배정은 _greedy_fill_slots가
                # 담당한다 — 포지션이 몰린 스쿼드(예: GK 2명)에서도 진짜
                # 맞는 선수가 먼저 자기 자리를 차지하고, 잉여만 마지막에
                # 남는 자리로 밀리게 한다(자세한 설명은 그 함수 주석 참고).
                slot_filled = _greedy_fill_slots(all_ai, slots_only)

                # 2) 내 포지션에 맞는 슬롯을 찾아, 그 자리의 기존 선수와 OVR 비교
                my_slot_idx, field_pos, mismatch_rank = _best_slot_for_player(
                    p.get("position", "MF"), slots_only)
                rival = slot_filled[my_slot_idx] if my_slot_idx < len(slot_filled) else None

                me = {"id": -1, "name": p.get("name", "나"),
                      "position": p.get("position", "MF"), "_slot_idx": my_slot_idx,
                      "ovr": p.get("ovr", 40), "is_me": True,
                      "injured": bool(p.get("injured")),
                      "age": p.get("age", 0), "nationality": p.get("nationality", ""),
                      **{s: p.get(s, 0) for s in ALL_STATS}}

                # [2026-08 신설, 신민용 리포트: "39/44경기 뛰었는데 화면엔
                # 벤치로 나온다"] 예전엔 "그 순간 이 자리 최고 OVR 선수 1명과
                # 비교"만으로 주전/벤치를 정했는데, 실제 경기 출전 여부는
                # 이거랑 완전히 다른 확률 판정(_check_bench, 팀평균OVR격차+
                # 감독관계 등 종합)을 따른다 — 그래서 실제로는 시즌 내내
                # 거의 다 뛴 선수도 화면 여는 순간의 미세한 OVR 역전 하나로
                # 벤치로 표시되는 불일치가 있었다. 이번 시즌 실제 출전율이
                # 뚜렷하게 높으면(과반 이상, 최소 5경기 이상 표본) OVR
                # 스냅샷 비교와 무관하게 주전으로 표시한다 — 화면이 "지금
                # 이 순간의 가정"이 아니라 "실제로 뛰고 있는 선수"를 보여
                # 주는 게 맞다는 원칙.
                _played = p.get("season_matches", 0)
                _total_sn = (_played + p.get("season_bench_matches_missed", 0)
                             + p.get("season_injury_matches_missed", 0)
                             + p.get("season_suspension_matches_missed", 0))
                _actual_starter = _total_sn >= 5 and (_played / _total_sn) >= 0.5

                if rival is None or me["ovr"] > rival["ovr"] or _actual_starter:
                    # 내가 그 자리 주전보다 낫다 (또는 빈 자리, 또는 실제로
                    # 이번 시즌 주전급으로 뛰어왔다) → 선발 출전
                    rest = [pl for pl in slot_filled if pl is not None and pl is not rival]
                    self.players = [me] + rest
                else:
                    # 그 자리 주전이 나보다 낫다 → 벤치 (베스트11 그대로 표시, 나는 제외)
                    self.players = [pl for pl in slot_filled if pl is not None]
                # [2026-08 신설] 명단 패널용 — 전체 스쿼드(all_ai) + 나(me)를
                # 합친 게 "우리 팀 전체 명단"이다. 주전 여부는 self.players
                # (방금 확정된 최종 출전 11명)의 id 집합으로 판정한다 —
                # 내가 벤치든 선발이든 이 집합엔 항상 정확히 반영돼 있다.
                self._roster = all_ai + [me]
                self._starter_ids = {pl.get("id") for pl in self.players}
            else:
                self.players = _mask_ai_names([dict(r) for r in conn.execute(
                    "SELECT * FROM ai_players WHERE team_id=? ORDER BY ovr DESC LIMIT 11",
                    (team_id,)).fetchall()])
                self._roster = list(self.players)
                self._starter_ids = {pl.get("id") for pl in self.players}
            conn.close()

        if intl_nat and not self._roster:
            # [2026-08 수정] 위 intl_nat 분기에서 이미 주전+후보로 _roster를
            # 채워뒀다 — 여기서 무조건 self.players로 덮어쓰면 후보가
            # 사라진다. 혹시 위에서 못 채워진 예외적인 경우(가상 폴백 등)만
            # 최후 안전장치로 채운다.
            self._roster = list(self.players)
            self._starter_ids = {pl.get("id") for pl in self.players}

        # 캐시 저장
        if _cache is not None:
            _cache[_cache_key] = (self.formation, list(self.players),
                                   list(self._roster), set(self._starter_ids),
                                   self._intl_formula_ovr)
            # 캐시 크기 제한 (오래된 항목 제거)
            if len(_cache) > 30:
                oldest = next(iter(_cache))
                del _cache[oldest]

        self._player_at = {}; self._positions_xy = []
        self.update()

    def load_opp_team(self, team: dict):
        """상대팀 dict ({formation, players}) 로드.
        [2026-08 버그수정, 신민용 리포트: "다른 곳에도 문제 있는거 아니냐"]
        확인해보니 있었다 — 상대팀 쪽(_fetch_league_opponents/_fetch_intl_
        opponents/_fetch_cl_opponents 등)은 애초에 선수를 포메이션 슬롯에
        맞춰 배정하는 로직 자체가 없었다. _players_for_team은 그냥
        OVR 내림차순 top11이고, _make_intl_virtual_players의 포지션
        목록조차 실제 FORMATION_SLOTS["4-4-2"] 구성과 다르다(CAM/LW/RW
        vs 실제 LM/RM/ST/ST) — 즉 내 팀 쪽 버그를 고치기 전부터도
        상대팀 포메이션 화면은 슬롯-선수가 맞을 가능성이 낮았다.
        load_my_team의 그리디 슬롯 배정(_best_slot_for_player 기반)을
        여기서도 그대로 적용해 각 선수에 _slot_idx를 태깅한다 — 이러면
        _fetch_* 쪽 함수들을 하나씩 안 고쳐도 이 한 곳에서 상대팀 전체
        경로가 다 같이 고쳐진다(paintEvent는 _slot_idx가 있으면 그걸로
        직접 매칭하므로)."""
        self.formation = team.get("formation") or "4-4-2"
        raw_players = team.get("players") or []
        slots_only = FORMATION_SLOTS.get(self.formation, FORMATION_SLOTS["4-4-2"])
        # [2026-08 버그수정] 배정은 _greedy_fill_slots가 담당 — 자세한 설명은
        # 그 함수 주석 참고(포지션 몰린 스쿼드에서의 연쇄 오배치 수정).
        slot_filled = _greedy_fill_slots(raw_players, slots_only)
        self.players = [pl for pl in slot_filled if pl is not None]
        # [2026-08 신설] 명단 패널용 — 상대팀도 전체 스쿼드를 따로 가져온다
        # (team_id가 있는 실제 클럽팀만 가능했었다).
        # [2026-08 버그수정, 신민용 리포트: "월드컵도 주전/후보가 있어야
        # 하는데 11명만 뽑힌다"] team_id가 없는 국제대회 쪽은 이제
        # _make_intl_real_players가 raw_players 자체에 23명(주전11+후보12)을
        # 담아서 준다 — 예전엔 이 경우 "벤치 데이터 자체가 없다"고 보고
        # 화면에 보이는 11명을 곧 전체 명단으로 취급했는데, 이제 그 폴백보다
        # raw_players 쪽이 더 크면(23명 vs 11명) raw_players 전체를 명단
        # 패널에 쓴다 — 화면에 안 뜨는 나머지 12명이 "후보"로 표시된다.
        full_squad = _full_squad_for_team(team.get("team_id"))
        if full_squad:
            self._roster = full_squad
        elif len(raw_players) > len(self.players):
            self._roster = raw_players
        else:
            self._roster = list(self.players)
        self._starter_ids = {pl.get("id") for pl in self.players}
        self._player_at = {}; self._positions_xy = []
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width(); h = self.height()

        painter.fillRect(0, 0, w, h, QBrush(QColor("#1a3a1a")))
        painter.setPen(QPen(QColor("#2a5a2a"), 1))
        painter.drawRect(12, 8, w-24, h-16)
        painter.drawLine(12, h//2, w-12, h//2)
        _cc_d = max(20, min(48, min(w, h)//7))
        painter.drawEllipse(w//2-_cc_d//2, h//2-_cc_d//2, _cc_d, _cc_d)

        slots = FORMATION_SLOTS.get(self.formation, FORMATION_SLOTS["4-4-2"])
        positions_xy = self._calc_positions(slots, w, h)
        self._positions_xy = positions_xy

        # 슬롯→선수 매핑
        # [2026-08 버그수정, 신민용 리포트: "ST → AI [GK]"처럼 슬롯과 선수가
        # 뒤섞여 표시된다] 예전엔 "내 슬롯을 뺀 나머지를 배열 순서대로
        # positions_xy에 순서대로 채운다"는 방식이었는데, self.players의
        # 순서(load_my_team이 배정한 원본 FORMATION_SLOTS 순서)와
        # positions_xy의 순서(화면 표시용으로 행/좌우 재정렬된 순서)가
        # 서로 다르다 — 그래서 배열 인덱스로 그냥 짝지으면 슬롯과 선수가
        # 뒤섞인다. load_my_team()이 각 선수에 실제로 배정된 원본 슬롯
        # 인덱스를 "_slot_idx"로 함께 저장해두므로, 그 값으로 직접
        # 찾아가면 순서와 무관하게 항상 맞는 슬롯에 배치된다.
        player_at = {}
        me = None
        if self.players and self.players[0].get("is_me"):
            me = self.players[0]
            primary_pos = me.get("position", "CM")
            # POSITION_COMPAT 기반으로 가장 자연스러운 슬롯 결정
            slots_only = [sp for (_, _, sp, _) in positions_xy]
            my_slot, field_pos, mismatch_rank = _best_slot_for_player(primary_pos, slots_only)
            # field_pos를 me에 저장 → 경기 퍼포먼스·커리어 기록에 활용
            me["field_pos"] = field_pos
            me["mismatch_rank"] = mismatch_rank
            # field_pos·mismatch_rank를 DB에 저장 (경기 퍼포먼스·커리어 기록에 활용)
            try:
                from game_engine import update_player
                update_player(field_pos=field_pos, mismatch_rank=mismatch_rank)
            except Exception:
                pass
            # my_slot은 positions_xy(행 재정렬) 기준 인덱스이므로, 그 슬롯의
            # 원본 slot_idx로 변환해서 저장한다 — 아래 매칭 기준을 하나로 통일.
            me["_slot_idx"] = positions_xy[my_slot][3]

        _have_slot_idx = bool(self.players) and all(
            pl.get("_slot_idx") is not None for pl in self.players)
        if _have_slot_idx:
            by_slot = {pl["_slot_idx"]: pl for pl in self.players}
            for i, (px, py, pos, slot_idx) in enumerate(positions_xy):
                if slot_idx in by_slot:
                    player_at[i] = by_slot[slot_idx]
        elif me is not None:
            # [폴백] _slot_idx가 없는 예전 경로(캐시된 옛 데이터 등) 전용 —
            # 슬롯 매칭 없이 순서대로 채운다. 정상 경로에서는 위 분기로 빠진다.
            my_slot_visual = next(i for i, (_, _, _, sidx) in enumerate(positions_xy)
                                   if sidx == me["_slot_idx"])
            player_at[my_slot_visual] = me
            ai_idx = 0
            for si in range(len(positions_xy)):
                if si == my_slot_visual: continue
                if ai_idx + 1 < len(self.players):
                    player_at[si] = self.players[ai_idx + 1]; ai_idx += 1
        else:
            for i, p in enumerate(self.players[:len(positions_xy)]):
                player_at[i] = p
        self._player_at = player_at

        for i, (px, py, pos, _slot_idx) in enumerate(positions_xy):
            pl = player_at.get(i)
            is_me = pl.get("is_me", False) if pl else False
            # [2026-08 신설, 신민용 요청: "부상당해서 못 나갈 때는 금색
            # 말고 빨간색으로"] 부상 여부는 load_my_team()에서 me dict에
            # "injured"로 태깅해둔다.
            is_injured_me = is_me and bool(pl and pl.get("injured"))
            is_hov = (i == self._hovered_slot)
            d = self._circle_d
            r = d // 2
            if is_injured_me:
                color = "#cc2222"
            elif is_me:
                color = "#ffcc00"
            else:
                color = _pos_color(pos)
            painter.setBrush(QBrush(QColor(color)))
            pen_color = "#00ff88" if is_hov else ("#000" if is_me else "#000")
            pen_w = 3 if is_hov else (2 if is_me else 1)
            painter.setPen(QPen(QColor(pen_color), pen_w))
            painter.drawEllipse(px-r, py-r, d, d)
            if is_hov:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#00ff8860"), 4))
                painter.drawEllipse(px-r-4, py-r-4, d+8, d+8)
            painter.setPen(QPen(QColor("#000" if is_me else "#fff")))
            f = QFont(); f.setPointSize(max(6, min(10, d // 5))); f.setBold(True); painter.setFont(f)
            # 내 선수는 배치 포지션(field_pos) 표시, AI는 슬롯 포지션
            _disp_pos = pl.get("field_pos", pos) if (pl and is_me) else pos
            painter.drawText(px-r, py-r, d, d, Qt.AlignmentFlag.AlignCenter, _disp_pos[:2])
            if pl:
                f2 = QFont(); f2.setPointSize(max(6, min(9, d // 6))); f2.setBold(is_me); painter.setFont(f2)
                _name_color = "#ff6666" if is_injured_me else ("#ffff00" if is_me else "#ddd")
                painter.setPen(QPen(QColor(_name_color)))
                # [2026-08 수정] _mask_ai_names가 이제 항상 정확히 6자
                # 코드("AI"+4자리 36진수)를 주므로 6자까지 그대로 보여줘도
                # 잘릴 일이 없다 — 예전 [:4]는 두 자릿수 순번("AI 10")을
                # "AI 1"로 잘라 다른 선수와 겹쳐 보이게 하던 버그의 원인.
                _name_w = max(48, d + 16)
                painter.drawText(px-_name_w//2, py+r+2, _name_w, 16,
                                 Qt.AlignmentFlag.AlignCenter, pl["name"][:6])
        painter.end()

    def _calc_positions(self, slots, w, h):
        """[2026-08 버그수정, 신민용 리포트: "ST → AI [GK]", "GK → AI [LW]"처럼
        슬롯과 선수가 완전히 뒤섞여 표시된다"] 반환 튜플에 slot_idx(=원본
        FORMATION_SLOTS 리스트에서의 인덱스)를 추가한다. 이 함수는 화면
        표시용으로 슬롯을 행(GK/DEF/MID/MID2/ATK)별로 묶고 각 행 안에서
        좌→우로 재정렬하므로, 반환 순서는 원본 slots 리스트 순서와 다르다
        (예: 4-4-2 원본은 [GK,CB,CB,LB,RB,LM,CM,CM,RM,ST,ST]인데 여기서는
        ATK행이 맨 앞으로 옴). load_my_team()이 선수를 배정할 때 쓰는
        슬롯 인덱스는 원본 순서 기준이라, 두 순서를 슬롯 라벨만 보고
        같은 것으로 착각해 배열 인덱스로 그냥 zip하면(과거 paintEvent가
        하던 방식) 완전히 다른 슬롯끼리 짝지어진다 — slot_idx를 함께
        내려줘서 어느 코드도 순서에 의존하지 않고 원본 인덱스로 직접
        찾아가게 한다.

        [2026-08 신설] 명단 패널이 추가되면서 캔버스 자체가 절반 크기로
        줄어들 수 있는데, 원 지름이 예전처럼 48px 고정이면 세로(행 간격)
        든 가로(한 행 안에 5명 붙는 백파이브 등)든 좁아진 자리에 원이
        겹칠 수 있다 — 세로/가로 두 제약 중 더 빡빡한 쪽에 맞춰 지름을
        동적으로 정하고(self._circle_d), paintEvent/마우스 히트박스가
        전부 이 값을 그대로 따라간다. 원래 크기(축소 전)에서는 여전히
        48px 그대로 나오도록 상한을 48로 고정 — 기존 화면은 완전히 동일."""
        rows = {}; row_order = []
        for idx, pos in enumerate(slots):
            k = _row_key(pos)
            if k not in rows: rows[k] = []; row_order.append(k)
            rows[k].append((idx, pos))
        sorted_rows = sorted(row_order, key=lambda x: _row_priority(x))
        total = len(sorted_rows); result = []
        max_row_cnt = max((len(v) for v in rows.values()), default=1)
        row_h = (h - 32) / max(1, total)
        col_w = w / (max_row_cnt + 1)
        self._circle_d = int(max(16, min(48, row_h * 0.82, col_w * 0.78)))
        for ri, rk in enumerate(sorted_rows):
            # 같은 행 안에서 _pos_x_order 기준 좌→우 정렬 (원본 인덱스는 유지)
            poss = sorted(rows[rk], key=lambda t: _pos_x_order(t[1]))
            cnt = len(poss)
            ry = 16 + int((ri + 0.5) * (h - 32) / total)
            for ci, (idx, pos) in enumerate(poss):
                result.append((int((ci+1)*w/(cnt+1)), ry, pos, idx))
        return result

    def mouseMoveEvent(self, event):
        mx, my = event.pos().x(), event.pos().y()
        hit_r2 = max(144, int(self._circle_d * 0.42) ** 2)
        new = next((i for i, (px, py, _, _s) in enumerate(self._positions_xy)
                    if (mx-px)**2+(my-py)**2 < hit_r2), -1)
        if new != self._hovered_slot:
            self._hovered_slot = new
            self.setCursor(Qt.CursorShape.PointingHandCursor if new >= 0
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        # [2026-08 수정, 신민용 요청: "어려움 모드일 때는 포메이션에서
        # 선수 상세 설정이 아예 안 뜨잖아 — 이름/국적/포지션/나이는
        # 뜨게 해줘"] 예전엔 어려움 난이도에서 클릭 자체를 막아 팝업이
        # 아예 안 떴다 — 이제 클릭은 항상 허용하고, PlayerStatPopup
        # 내부에서 어려움 모드일 때 OVR·스탯·소속팀 표시만 숨긴다(신원
        # 식별용 4개 항목만 남김).
        mx, my = event.pos().x(), event.pos().y()
        hit_r2 = max(144, int(self._circle_d * 0.42) ** 2)
        for i, (px, py, _, _s) in enumerate(self._positions_xy):
            if (mx-px)**2+(my-py)**2 < hit_r2:
                pl = self._player_at.get(i)
                if pl:
                    _popup = PlayerStatPopup(pl, self)
                    _popup.exec()
                    _popup.deleteLater()
                break


# ─────────────────────────────────────────────
# 전체 명단 패널 (2026-08 신설)
# ─────────────────────────────────────────────

class _RosterPanel(QScrollArea):
    """[2026-08 신설, 신민용 요청: "본선 11명 말고 각 팀 전체 선수가
    누구누구인지 떠야하잖아 — 주전은 상자 녹색, 이름 클릭하면 스탯"]
    _FormationCanvas 오른쪽에 붙는 스크롤 가능한 전체 스쿼드 명단(2026-08
    수정: 아래가 아니라 오른쪽 배치로 변경 — 세로 공간이 좁아 열을 2개로
    줄임). 이름 자체는(포메이션 화면 전체가 이미 그렇듯) 나 자신만
    실명이고 나머지는 전부 "AI"로 표시되므로, 포지션+OVR을 이름 옆에
    같이 붙여서 서로 구분되게 한다."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumWidth(150)
        self.setStyleSheet(
            "QScrollArea{background:#161616;border:1px solid #2a2a2a;border-radius:4px;}")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;")
        self._grid = QGridLayout(self._body)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(3)
        self.setWidget(self._body)
        self._cols = 1

    def _make_group_header(self, text: str, count: int) -> QLabel:
        """[2026-08 신설, 신민용 요청: "이름 표시하는 곳을 파란색으로"]
        "주전"/"후보" 구분 라벨 — 개별 선수 행이 아니라 이 헤더 텍스트
        자체가 파란색이다. 개별 선수 행 색상과는 완전히 별개.
        [2026-08 수정, 신민용 요청: "파란 글자 크기 키우고 옆에 몇 명
        있는지 숫자로 표시"] 글자 크기 10px→13px, 텍스트 뒤에 인원수를
        괄호로 붙인다(예: "주전 (11)")."""
        lbl = QLabel(f"{text} ({count})")
        lbl.setStyleSheet(
            "color:#5aa9ff;font-size:13px;font-weight:bold;"
            "padding:4px 2px 2px 2px;border-bottom:1px solid #2a2a2a;")
        return lbl

    def _make_player_button(self, pl: dict, is_starter: bool) -> QPushButton:
        is_me = bool(pl.get("is_me"))
        is_injured_me = is_me and bool(pl.get("injured"))
        # [2026-08 수정, 신민용 리포트: "어려움 난이도인데 이름 옆에 OVR
        # 숫자가 그대로 뜬다"] 포메이션 캔버스(클릭 시 스탯 팝업 차단)·
        # 상대팀 선택 콤보(OVR 수치 제거)는 이미 어려움 난이도에서 OVR을
        # 숨기고 있었는데, 이 명단 패널 버튼 라벨만 빠져 있었다. 어려움
        # 난이도에서는 OVR 대신 포지션/나이/국적을 보여준다(신민용 확정
        # 요청 포맷).
        if is_hard_mode():
            _age = pl.get("age") or 0
            _nat = pl.get("nationality") or ""
            _detail = f"{pl.get('position','')}" + (f", {_age}세" if _age else "") + (f", {_nat}" if _nat else "")
            label = f"{pl.get('name','')[:6]} ({_detail})"
        else:
            label = f"{pl.get('name','')[:6]} ({pl.get('position','')} {pl.get('ovr',0)})"
        btn = QPushButton(label)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # [2026-08 신설, 신민용 요청: "우측 상자들 크기를 최대한 우측
        # 크기에 맞춰서 키우고 세로 길이도 1.2배 정도"] 가로는 부모
        # 컨테이너(스크롤영역) 폭에 맞춰 늘어나도록 Expanding, 세로는
        # 기존(~24px) 대비 약 1.2배(29px)로 명시 지정.
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setMinimumHeight(29)
        if is_injured_me:
            # [2026-08 신설, 신민용 요청: "부상당해서 못 나갈 때는
            # 금색 말고 빨간색으로"] 포메이션 캔버스와 동일한 규칙.
            style = ("background:#4a1414;color:#ffb0b0;border:1px solid #cc2222;"
                     "border-radius:4px;padding:5px 8px;font-size:10px;font-weight:bold;")
        elif is_me:
            # 나는 주전/벤치와 무관하게 항상 노란색으로 — 포메이션
            # 캔버스에서 "나"를 표시하는 색과 통일.
            style = ("background:#4a3a00;color:#ffe066;border:1px solid #ffcc00;"
                     "border-radius:4px;padding:5px 8px;font-size:10px;font-weight:bold;")
        elif is_starter:
            # 주전 — 상자를 녹색으로.
            style = ("background:#1e4a1e;color:#eaffea;border:1px solid #3fae3f;"
                     "border-radius:4px;padding:5px 8px;font-size:10px;font-weight:bold;")
        else:
            # [2026-08 수정, 신민용 요청: "후보 선수는 색 없이"] 그룹 구분은
            # 위 "후보" 헤더가 이미 담당하므로, 개별 벤치 선수 행은 주전
            # (녹색)과 구분되는 무채색 스타일로 — 파란 글자로 강조하던
            # 기존 스타일을 폐기.
            style = ("background:#1c1c1c;color:#aaa;border:1px solid #333;"
                     "border-radius:4px;padding:5px 8px;font-size:10px;")
        btn.setStyleSheet(style + "text-align:left;")
        # [2026-08 수정, 신민용 요청: "포메이션에서 좌측(캔버스)뿐 아니라
        # 우측 주전/후보 버튼들 눌러도 (이름/포지션/나이/국적) 떠야 해"]
        # 예전엔 어려움 난이도에서 이 버튼들만 클릭 자체를 막았다(캔버스는
        # 이미 클릭 허용 + 팝업 내부에서 정보 제한으로 바뀌었는데, 이
        # 패널만 그 수정이 안 들어가 있었다) — 이제 항상 클릭 가능하고,
        # PlayerStatPopup 내부가 어려움 난이도일 때 신원 4개 항목만
        # 보여주는 건 이미 처리돼 있으므로 여기서는 그냥 항상 연결한다.
        def _open_popup(_=False, p=pl):
            _popup = PlayerStatPopup(p, self)
            _popup.exec()
            _popup.deleteLater()
        btn.clicked.connect(_open_popup)
        return btn

    def set_roster(self, players: list, starter_ids: set):
        # 기존 버튼/라벨 정리
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None); w.deleteLater()
        if not players:
            empty = QLabel("명단 없음")
            empty.setStyleSheet("color:#666;font-size:10px;")
            self._grid.addWidget(empty, 0, 0)
            return
        # [2026-08 수정, 신민용 요청: "주전들 위에, 그런 식으로" → 이후
        # "주전"/"후보" 구분 헤더로 재요청] 정렬 우선순위는 그대로 "주전
        # 여부 먼저, 그 안에서 포지션·OVR"이지만, 이제 그 경계에 실제
        # 헤더 라벨 행을 끼워 넣는다(스케치 참고: 주전 헤더 → 주전 목록
        # → 후보 헤더 → 후보 목록).
        _cat_order = {"GK": 0, "DEF": 1, "MID": 2, "ATK": 3}
        def _sort_key(pl):
            return (_cat_order.get(_pos_category(pl.get("position", "")), 4),
                    -(pl.get("ovr", 0) or 0))
        starters = sorted((pl for pl in players if pl.get("id") in starter_ids), key=_sort_key)
        bench = sorted((pl for pl in players if pl.get("id") not in starter_ids), key=_sort_key)

        row = 0
        if starters:
            self._grid.addWidget(self._make_group_header("주전", len(starters)), row, 0); row += 1
            for pl in starters:
                self._grid.addWidget(self._make_player_button(pl, is_starter=True), row, 0)
                row += 1
        if bench:
            self._grid.addWidget(self._make_group_header("후보", len(bench)), row, 0); row += 1
            for pl in bench:
                self._grid.addWidget(self._make_player_button(pl, is_starter=False), row, 0)
                row += 1


class _TeamPanel(QWidget):
    """[2026-08 신설] 포메이션 캔버스 + 전체 명단 패널을 가로로 묶는
    컨테이너(좌: 캔버스, 우: 명단). FormationWidget이 좌/우 각각 하나씩
    가진다.
    [2026-08 수정, 신민용 요청: "위아래가 아니라 좌측 포메이션/우측
    선수들로"] 세로(QVBoxLayout, 캔버스 위·명단 아래)에서 가로
    (QHBoxLayout, 캔버스 좌·명단 우)로 변경. 캔버스 쪽은 이제 세로로는
    안 눌리고 가로로만 좁아지므로, _FormationCanvas._calc_positions의
    원 지름 계산(세로/가로 두 제약 중 더 빡빡한 쪽)이 자동으로 가로
    제약(col_w) 쪽을 따라간다 — 별도 수정 없이 그대로 작동."""
    def __init__(self, is_opponent=False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.canvas = _FormationCanvas(is_opponent=is_opponent)
        self.roster = _RosterPanel(self)
        lay.addWidget(self.canvas, 3)
        lay.addWidget(self.roster, 2)

    def refresh_roster(self):
        self.roster.set_roster(self.canvas._roster, self.canvas._starter_ids)


def apply_custom_name_live(player_id: int, new_name: str):
    """[2026-08 신설, 신민용 요청: "이름 변경했는데 나갔다 들어와야
    바뀐다 — 실시간으로 안 되나? 한 번에 하나씩만 바꾸는 건데"]
    _ovr_cache_invalidated 플래그는 "다음에 이 팀을 다시 불러올 때"만
    적용되는 예약이라, 지금 이미 화면에 떠 있는 포메이션(내 팀/상대팀
    양쪽 다)은 그 시점이 올 때까지(주 진행, 팀 재선택 등) 안 바뀐다.
    한 번에 선수 한 명만 바꾸는 가벼운 작업이므로, 그 대신 지금 열려
    있는 모든 _TeamPanel(내 팀·상대팀 캔버스+명단 패널 둘 다 이
    클래스 하나를 씀)을 뒤져서 이 선수 id가 보이면 그 자리에서 바로
    이름을 바꾸고 다시 그린다 — DB 재조회 없이 화면만 즉시 갱신되는
    가벼운 패치라, 몇 명이 떠 있든 순식간에 끝난다."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        if not isinstance(w, _TeamPanel):
            continue
        changed = False
        for pl in w.canvas.players:
            if pl.get("id") == player_id:
                pl["name"] = new_name
                changed = True
        for pl in w.canvas._roster:
            if pl.get("id") == player_id:
                pl["name"] = new_name
                changed = True
        if changed:
            w.canvas.update()
            w.refresh_roster()

# ─────────────────────────────────────────────
# 메인 위젯
# ─────────────────────────────────────────────

_CTX_STYLE = {
    "league":    ("color:#66ff99;", "⚽"),
    "intl_main": ("color:#ffaa33;", "🌍"),   # 월드컵/대륙컵 본선
    "intl_qual": ("color:#ff6666;", "🌍"),   # 그 외 국대(예선 등)
    # [2026-08 v3.3 수정, 신민용 리포트: "챔스/유로파/컨퍼런스/슈퍼컵이
    # 다 'cl'로 뭉뚱그려져서 챔스 색만 나온다"] center_panel.py 주간
    # 일정 카드에 이미 적용된 cl_kind별 배색(챔피언스=블루/유로파=오렌지/
    # 컨퍼런스=진초록+연두/슈퍼컵=골드)과 동일하게 맞춘다.
    "champions": ("color:#4466ff;", "🏆"),
    "europa":    ("color:#ff7700;", "🥈"),
    "conference":("color:#215131;", "🥉"),
    "super_cup": ("color:#ffd700;", "⭐"),
    "cwc":       ("color:#66d9ff;", "🌐"),
    "cup":       ("color:#c48aff;", "🎖️"),
}

# actBtn과 동일한 다크 박스 스타일
_BOX_STYLE  = "background:#2a2a2a;border:1px solid #444;border-radius:4px;padding:4px 8px;"
_LABEL_STYLE = f"color:#cccccc;font-size:11px;{_BOX_STYLE}"
_HINT_STYLE  = "color:#555;font-size:9px;"

class FormationWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._last_ctx = None
        self._opp_teams = []
        # [최적화] load_my_team 캐시: (team_id, intl_nat) → (formation, players)
        # refresh()마다 동일 팀을 재쿼리하지 않도록 캐시. team_id/intl_nat 변경 시 자동 갱신.
        self._my_team_cache: dict = {}   # {(team_id, intl_nat, sig): (formation, players, roster, starter_ids)}
        self._my_team_cache_key = None   # 마지막으로 로드한 캐시 키

        self.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(4)

        # ── 0행: 대회 필터 버튼(2026-08 v3.3 신설, 신민용 요청: "리그랑
        # 챔스가 겹치는 주엔 리그를 보고 싶을 때도 있는데, 1주 단위로
        # 돌리면 챔스 처리하는 동안 리그를 확인할 방법이 없다 — 우측에
        # 필터를 만들어서 필터에 따라 다른 게 뜨게 해달라") 이번 주 내가
        # 동시에 걸쳐 있는 대회가 여러 개면(예: 리그+챔스) 이 줄에 버튼이
        # 여러 개 뜨고, 하나뿐이면(겹치는 대회가 없으면) 아예 숨겨서 예전과
        # 화면이 동일하게 유지된다. center_panel.py가 넘겨주는 options
        # (label, context) 목록 그대로 버튼화 — 실제 대회 판정/그날 상대가
        # 누구인지는 여전히 center_panel이 계산해서 넘겨주고, 여긴 그 중
        # "지금 화면에 보여줄 것"만 고르는 순수 표시 필터다(경기 진행 자체엔
        # 영향 없음 — 어떤 대회 경기를 실제로 뛰는지는 일정이 그대로 정함).
        self._filter_row_w = QWidget()
        self._filter_row = QHBoxLayout(self._filter_row_w)
        self._filter_row.setContentsMargins(0, 0, 0, 0)
        self._filter_row.setSpacing(4)
        self._filter_row.addStretch()   # 버튼들을 우측으로 몰아서 배치
        self._filter_row_w.setVisible(False)
        lay.addWidget(self._filter_row_w)
        self._filter_btns = []   # [(QPushButton, context_dict), ...]
        self._filter_team_id = None
        self._filter_manager_rel = 50
        self._filter_context = None

        # ── 1행: 대회명 구분선 (에이전트/은퇴 버튼과 캔버스 사이)
        self.lbl_ctx = QLabel()
        self.lbl_ctx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_ctx.setStyleSheet(
            f"color:#aaffaa;font-size:11px;font-weight:bold;{_BOX_STYLE}")
        self.lbl_ctx.setFixedHeight(26)
        lay.addWidget(self.lbl_ctx)

        # ── 2행: 내 팀 정보(좌) + 상대팀 콤보(우)
        info_row = QHBoxLayout()
        info_row.setSpacing(6)
        lay.addLayout(info_row)

        self.lbl_my = QLabel("내 팀")
        self.lbl_my.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_my.setStyleSheet(
            f"color:#ffcc00;font-size:11px;font-weight:bold;{_BOX_STYLE}")
        self.lbl_my.setFixedHeight(26)
        info_row.addWidget(self.lbl_my, 5)

        self.combo = QComboBox()
        self.combo.setFixedHeight(26)
        self.combo.setStyleSheet(
            "QComboBox{background:#2a2a2a;color:#cccccc;border:1px solid #444;"
            "border-radius:4px;padding:2px 8px;font-size:11px;}"
            "QComboBox:hover{border:1px solid #888;background:#383838;}"
            "QComboBox::drop-down{border:none;width:18px;}"
            "QComboBox QAbstractItemView{background:#1e1e1e;color:#ccc;"
            "selection-background-color:#3a6a3a;border:1px solid #444;outline:none;}")
        self.combo.currentIndexChanged.connect(self._on_opp_select)
        info_row.addWidget(self.combo, 5)

        # ── 3행: 캔버스(절반 크기) + 전체 명단 패널, 좌우
        # [2026-08 신설, 신민용 요청: "본선 11명 말고 전체 선수 명단도
        # 봐야 하고, 그러려면 포메이션 캔버스 크기를 절반으로 줄여야
        # 한다"] 캔버스를 직접 넣는 대신 _TeamPanel(캔버스+명단 세로 묶음)
        # 을 넣는다 — 기존에 self._my_canvas/self._opp_canvas를 참조하던
        # 코드는 그대로 두기 위해 별칭(alias)으로 canvas를 그대로 연결.
        split = QHBoxLayout()
        split.setSpacing(4)
        lay.addLayout(split)

        self._my_panel  = _TeamPanel(is_opponent=False)
        self._my_canvas = self._my_panel.canvas
        split.addWidget(self._my_panel, 5)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("QFrame{color:#333;}")
        split.addWidget(div)

        self._opp_panel  = _TeamPanel(is_opponent=True)
        self._opp_canvas = self._opp_panel.canvas
        split.addWidget(self._opp_panel, 5)

        # 힌트 바
        # [2026-08 신설, 난이도 시스템] 어려움 난이도에선 클릭해도 스탯이
        # 안 뜨므로("그냥 포메이션만 표시") 안내 문구도 그에 맞게 바꾼다
        # — 안 그러면 "클릭 → 스탯"이라고 해놓고 아무 반응이 없어 버그처럼
        # 보일 수 있다.
        # [2026-08 수정, 신민용 요청] 어려움 모드에서도 이제 클릭하면
        # 신원(이름/포지션/나이/국적)은 뜬다 — 힌트 문구도 그에 맞춰 수정.
        _hint_text = "클릭 → 신원만 표시" if is_hard_mode() else "클릭 → 스탯"
        hint_bar = QHBoxLayout()
        lh = QLabel(_hint_text); lh.setStyleSheet(_HINT_STYLE)
        rh = QLabel(_hint_text); rh.setStyleSheet(_HINT_STYLE)
        rh.setAlignment(Qt.AlignmentFlag.AlignRight)
        hint_bar.addWidget(lh); hint_bar.addStretch(); hint_bar.addWidget(rh)
        lay.addLayout(hint_bar)

    def load_team(self, team_id, context: dict = None, options: list = None, manager_rel: int = 50):
        """options: [(label, context_dict_or_None), ...] — center_panel.py가
        "이번 주 내가 걸쳐 있는 모든 대회"를 순서대로 넘겨준다(예: 리그+챔스가
        겹치는 주엔 [("리그", None), ("챔피언스", {...})]). 2개 이상이면
        필터 버튼을 보여주고, context는 그 중 "지금 자동으로 골라진 기본
        선택"(예전 동작과 동일하게 우선순위상 가장 중요한 대회)이다."""
        self._filter_team_id = team_id
        self._filter_manager_rel = manager_rel
        self._filter_context = context
        self._build_filter_row(options or [], context)
        self._apply_context(team_id, context, manager_rel)

    def refresh_now(self):
        """[2026-08 신설, 신민용 요청: "이름 변경했는데 실시간으로 반영이
        안 되고 나갔다 들어와야 바뀐다"] _ovr_cache_invalidated 플래그를
        세워두는 것만으로는 부족하다 — 이 플래그는 "다음에 누가
        load_my_team()을 부르면 그때 캐시를 비워라"는 예약일 뿐이라,
        포메이션 화면이 이미 화면에 떠 있는 동안엔 아무도 다시 그
        메서드를 안 불러서(주 진행 등 다른 트리거가 있어야 함) 반영이
        미뤄졌다. 지금 로드돼 있던 team_id/context/manager_rel 그대로
        _apply_context를 즉시 재호출해서(캐시는 호출부가 이미 무효화해둔
        상태이므로 자동으로 DB에서 새로 읽는다) 화면에 바로 반영한다."""
        if self._filter_team_id is not None:
            self._apply_context(self._filter_team_id, self._filter_context, self._filter_manager_rel)

    def _build_filter_row(self, options: list, active_context: dict):
        # 기존 버튼 정리
        for btn, _ctx in self._filter_btns:
            btn.setParent(None)
        self._filter_btns = []
        if len(options) < 2:
            self._filter_row_w.setVisible(False)
            return
        self._filter_row_w.setVisible(True)
        for label, ctx in options:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("fmFilterBtn")
            btn.setStyleSheet("""
                QPushButton#fmFilterBtn {
                    background-color:#2a2a2a; color:#888888; border:1px solid #444444;
                    padding:3px 10px; border-radius:4px; font-size:11px;
                }
                QPushButton#fmFilterBtn:hover { border:1px solid #777777; color:#bbbbbb; }
                QPushButton#fmFilterBtn:checked {
                    background-color:#2d5a2d; color:#ffffff; border:1px solid #4caf50;
                    font-weight:bold;
                }
            """)
            btn.setChecked(repr(ctx) == repr(active_context))
            btn.clicked.connect(lambda _checked, c=ctx: self._on_filter_clicked(c))
            self._filter_row.addWidget(btn)
            self._filter_btns.append((btn, ctx))

    def _on_filter_clicked(self, context):
        for btn, ctx in self._filter_btns:
            btn.setChecked(ctx is context or repr(ctx) == repr(context))
        self._apply_context(self._filter_team_id, context, self._filter_manager_rel)

    def _apply_context(self, team_id, context: dict = None, manager_rel: int = 50):
        is_intl = bool(context and context.get("intl"))
        my_nat  = context.get("my_nat", "") if is_intl else ""

        # ── 내 팀 캔버스 (국제전이면 국가대표 모드)
        # [2026-08 신설] context["tournament_id"]를 그대로 실어 보내서
        # load_my_team이 나를 뺀 25명도 대회 내내 고정된 명단을 쓰게 한다.
        _tid = context.get("tournament_id") if is_intl and context else None
        self._my_canvas.load_my_team(team_id, intl_nat=my_nat, tournament_id=_tid)
        self._my_panel.refresh_roster()

        # ── 좌측 레이블: 국제전 → 국가명+OVR / 리그 → 팀명+OVR
        # [2026-08 신설, 난이도 시스템] 어려움 난이도는 "평균 OVR 76" 같은
        # 수치를 아예 안 붙인다(신민용 확정).
        # [2026-08 수정, 신민용 요청: "국대 팀 전체 OVR은 계산치(케미
        # 반영)로 가고 실제 명단은 진짜 잘하는 선수들로"] 국제전이면
        # load_my_team이 채워둔 _intl_formula_ovr(계산치)을 쓰고, club
        # 매치면 기존대로 실제 로스터 평균(_calc_avg_ovr)을 쓴다.
        if is_intl and self._my_canvas._intl_formula_ovr is not None:
            my_avg = self._my_canvas._intl_formula_ovr
        else:
            my_avg = self._my_canvas._calc_avg_ovr(ndigits=1)
        _ovr_suffix = "" if is_hard_mode() else f"  |  평균 OVR {my_avg:.1f}"
        if is_intl:
            # 국가 flag + 국가명 표시
            conn = get_conn()
            crow = conn.execute(
                "SELECT flag FROM countries WHERE name=?", (my_nat,)).fetchone()
            conn.close()
            flag = (crow["flag"] + " ") if crow and crow["flag"] else ""
            self.lbl_my.setText(f"{flag}{my_nat}{_ovr_suffix}")
            self.lbl_my.setStyleSheet("color:#ffd700;font-weight:bold;")  # 금색으로 강조
        else:
            conn = get_conn()
            trow = conn.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
            conn.close()
            team_name = trow["name"] if trow else ""
            self.lbl_my.setText(f"내 팀: {team_name}{_ovr_suffix}")
            self.lbl_my.setStyleSheet("color:#ffd700;font-weight:bold;")

        # ── 컨텍스트 레이블 (대회명 표시줄)
        if context:
            if context.get("intl"):
                # [2026-07 색상 규칙 개편] 국대 경기도 월드컵/대륙컵 본선(주황)과
                # 그 외(예선 등, 빨강)를 구분한다 — center_panel/schedule_window와
                # 동일한 규칙.
                kind = "intl_main" if context.get("kind") in ("world", "continent") else "intl_qual"
            elif context.get("cl"):
                # [2026-08 v3.3 수정] cl_kind(champions/europa/conference/
                # super_cup)별로 색을 분리 — center_panel 주간 일정 카드와
                # 동일한 배색 기준.
                kind = context.get("cl_kind", "champions")
            elif context.get("cwc"):
                kind = "cwc"
            elif context.get("cup"):
                kind = "cup"
            else:
                kind = "league"
        else:
            kind = "league"
        style, icon = _CTX_STYLE.get(kind, _CTX_STYLE["league"])
        lname = context.get("league_name", "") if context else ""
        stage = context.get("stage_ko", "") if context else ""
        if not lname:
            conn = get_conn()
            row = conn.execute(
                "SELECT l.name FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (team_id,)).fetchone()
            conn.close()
            lname = row["name"] if row else "리그"
        txt = f"{icon} {lname}"
        if stage: txt += f"  {stage}"
        self.lbl_ctx.setText(txt)
        self.lbl_ctx.setStyleSheet(
            f"{style}font-size:11px;font-weight:bold;{_BOX_STYLE}")

        # ── 상대팀 목록 (캐시)
        # [버그수정] 캐시 키에 league_id 포함: 승강 후 같은 team_id라도
        #   리그가 바뀌면 상대팀 목록을 새로 조회한다.
        _cur_league_id = 0
        if not (context and (context.get("intl") or context.get("cl") or context.get("cwc"))):
            try:
                _cl = get_conn()
                _lr = _cl.execute("SELECT league_id FROM teams WHERE id=?", (team_id,)).fetchone()
                _cl.close()
                _cur_league_id = _lr["league_id"] if _lr else 0
            except Exception:
                _cur_league_id = 0
        ctx_key = (team_id, repr(context), _cur_league_id)
        if ctx_key != self._last_ctx:
            self._last_ctx = ctx_key
            self._opp_teams = self._resolve_opponents(team_id, context)
            self._fill_combo()

    def _resolve_opponents(self, team_id, context):
        if context and context.get("intl"):
            tid   = context["tournament_id"]
            nat   = context.get("my_nat", "")
            stage = context.get("stage", "group")
            week  = context.get("week", 0)
            grp   = context.get("grp", "")
            if stage != "group":
                # 플레이오프/토너먼트: 이번 주 상대 1팀만
                res = _fetch_intl_ko_opp(tid, nat, week)
                if res: return res
            # 조별리그: 내 그룹(grp)에 있는 팀만
            return _fetch_intl_opponents(tid, nat, grp=grp or None)
        elif context and context.get("cl"):
            # [2026-08 v3.3 버그수정, 신민용 리포트: "챔스랑 리그가 겹치면
            # 우측 포메이션이 바뀌는데, 유로파/컨퍼런스/슈퍼컵도 마찬가지로
            # 일정에 맞게 상대가 떠야 한다"] 예전엔 cl_kind와 무관하게
            # 무조건 챔피언스리그 테이블(cl_entries/cl_matches)만 조회해서,
            # 실제로는 유로파/컨퍼런스/슈퍼컵 경기인데도 챔스 데이터를
            # 잘못 보여주거나(우연히 tournament_id가 겹치면) 빈 목록이
            # 떴다. cl_kind로 올바른 테이블 접두사를 골라서 조회한다.
            cl_kind = context.get("cl_kind", "champions")
            prefix  = _CLUB_COMP_PREFIX.get(cl_kind, "cl")
            tid   = context["tournament_id"]
            stage = context.get("stage", "group")
            week  = context.get("week", 0)
            grp   = context.get("grp", "")
            if stage != "group" or prefix == "sc":
                # 토너먼트/스위스리그 페이즈(유로파·컨퍼런스는 애초에
                # "group"이 아니라 "league"로 넘어옴)나 슈퍼컵(조별리그
                # 자체가 없음): 이번 주 상대 1팀만.
                res = _fetch_club_ko_opp(prefix, tid, team_id, week)
                if res: return res
            # 챔피언스리그의 실제 조별리그 단계: 내 조에 있는 팀만.
            return _fetch_club_group_opponents(prefix, tid, team_id, grp=grp or None)
        elif context and context.get("cwc"):
            tid   = context["tournament_id"]
            stage = context.get("stage", "group")
            week  = context.get("week", 0)
            grp   = context.get("grp", "")
            if stage != "group":
                res = _fetch_cwc_ko_opp(tid, team_id, week)
                if res: return res
            return _fetch_cwc_opponents(tid, team_id, grp=grp or None)
        elif context and context.get("cup"):
            # [2026-08 v3.3 신설] 예전엔 국내컵 context 분기 자체가 없어서
            # else(리그 상대팀) 폴백으로 빠져 엉뚱한(내 리그) 상대팀 목록이
            # 떴다 — 국내컵은 조별리그가 없는 순수 토너먼트라 항상 이번 주
            # 상대 1팀만 보여준다.
            tid  = context["tournament_id"]
            week = context.get("week", 0)
            return _fetch_cup_ko_opp(tid, team_id, week) or []
        else:
            conn = get_conn()
            row = conn.execute("SELECT league_id FROM teams WHERE id=?", (team_id,)).fetchone()
            conn.close()
            if not row: return []
            return _fetch_league_opponents(team_id, row["league_id"])

    def _fill_combo(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        # [2026-08 신설, 난이도 시스템] 상대팀 선택 목록도 "OVR 77" 같은
        # 수치를 어려움 난이도에서는 빼서, 상대팀 콤보만 보고 강팀을
        # 골라내는 걸 막는다.
        _hard = is_hard_mode()
        for t in self._opp_teams:
            flag = t["flag"] + " " if t["flag"] else ""
            suffix = "" if _hard else f"  OVR {t['avg_ovr']:.1f}"
            self.combo.addItem(f"{flag}{t['name']}{suffix}")
        self.combo.blockSignals(False)
        self.combo.setCurrentIndex(0)
        self._render_opp(0)

    def _on_opp_select(self, idx):
        self._render_opp(idx)

    def _render_opp(self, idx):
        if not self._opp_teams or idx < 0 or idx >= len(self._opp_teams):
            return
        t = self._opp_teams[idx]
        self._opp_canvas.load_opp_team(t)
        self._opp_panel.refresh_roster()


# ─────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────

# 같은 행 안에서 포지션의 좌→우 정렬 순서 (낮을수록 왼쪽)
_POS_X_ORDER = {
    # 공격 라인
    "LW": 0, "CF": 1, "ST": 2, "SS": 2, "RW": 4,
    # 공격형 미드
    "LM": 0, "CAM": 2, "RM": 4,
    # 중앙 미드
    "CM": 2, "CDM": 2, "DM": 2,
    # 수비 라인
    "LWB": 0, "LB": 1, "CB": 2, "RB": 3, "RWB": 4, "SW": 2,
    # GK
    "GK": 2,
}

def _pos_x_order(pos):
    return _POS_X_ORDER.get(pos, 2)

def _row_key(pos):
    if pos == "GK": return "GK"
    if pos in ("CB","LB","RB","LWB","RWB","SW"): return "DEF"
    if pos in ("CDM","CM","DM"): return "MID"
    if pos in ("CAM","LM","RM"): return "MID2"  # 공격형 미드/윙미드 = 별도 행
    return "ATK"

def _pos_category(pos):
    if pos == "GK": return "GK"
    if pos in ("CB","LB","RB","LWB","RWB","SW"): return "DEF"
    if pos in ("CDM","CM","CAM","LM","RM","DM","AM"): return "MID"
    return "ATK"


def _best_slot_for_player(primary_pos, slots):
    """주요 포지션에서 포메이션 슬롯 중 가장 자연스러운 슬롯 인덱스 반환.
    POSITION_COMPAT 우선순위 리스트 기준: 앞에 있는 슬롯일수록 높은 우선순위.
    반환: (slot_index, field_pos, mismatch_rank)
      mismatch_rank=0: 완벽 매치 / 1: 2순위 / 2: 3순위 ...
    [주의] '나(me)' 한 명의 슬롯을 결정할 때만 쓴다. 여러 후보를 한꺼번에
    슬롯에 채울 때는(AI 11명 등) 아래 _greedy_fill_slots를 쓴다 — 이
    함수의 최종 폴백(호환/카테고리 매치가 전혀 없을 때 slots[0]을 그냥
    반환)은 후보가 하나뿐일 때는 무해하지만, 여러 명을 순서대로 돌리면서
    쓰면 포지션이 몰린(예: GK 2명) 잉여 후보가 엉뚱한 자리를 가로채고
    그 여파로 뒤 후보들이 줄줄이 밀리는 문제가 있었다(2026-08 버그수정,
    신민용 리포트: "RB 의 주포가 GK로 뜨고 ST가 CB으로 간다").
    """
    compat = POSITION_COMPAT.get(primary_pos, [primary_pos])
    # 이미 할당된 슬롯 제외 없이 최적 슬롯만 찾음 (호출 시 이미 할당된 슬롯 제외 처리)
    best_idx, best_rank = 0, 999
    for si, slot_pos in enumerate(slots):
        if slot_pos in compat:
            rank = compat.index(slot_pos)
            if rank < best_rank:
                best_rank = rank
                best_idx = si
    if best_rank == 999:
        # 호환 없으면 카테고리로 fallback
        my_cat = _pos_category(primary_pos)
        for si, slot_pos in enumerate(slots):
            if _pos_category(slot_pos) == my_cat:
                return si, slot_pos, 4
        return 0, slots[0] if slots else primary_pos, 4
    return best_idx, slots[best_idx], best_rank


def _greedy_fill_slots(candidates, slots_only):
    """여러 후보 선수를 포메이션 슬롯(slots_only, 원본 순서)에 배정한다.
    각 배정된 선수 dict에 원본 슬롯 인덱스를 "_slot_idx"로 태깅하고,
    slots_only와 같은 길이의 리스트(빈 자리는 None)를 반환한다.

    [2026-08 버그수정, 신민용 리포트: "상대팀 포메이션에서 RB 의 주포가
    GK로 뜨고 ST가 CB으로 가며 GK는 클릭해도 스탯이 안 떠"] 예전엔 후보
    전원을 OVR 내림차순으로 한 번에 훑으면서, 각자 차례가 오면 그 자리에서
    바로 "정확한 포지션 매치 → 카테고리 매치 → 그냥 첫 빈 슬롯" 순으로
    즉시 확정지었다. 그런데 스쿼드에 같은 포지션이 몰려있으면(예: GK가
    상위 OVR 11명 안에 2명 다 들어옴) 두 번째 GK는 정확한 매치도 카테고리
    매치도 없다(GK 슬롯은 하나뿐이고 이미 찼으므로) — 그래서 "그냥 첫
    빈 슬롯"으로 강제 확정되는데, 하필 그 자리가 아직 자기 차례가 안 온
    진짜 RB 선수의 자리였다. 그러면 진짜 RB 선수는 이후 순서에서 밀려나
    엉뚱한 자리(ST 등)로 떠밀리고, 그 자리에 있던 선수도 또 밀리는 식으로
    연쇄 오배치가 일어난다. 또한 진짜 GK 슬롯 자체가 다른 잉여 선수에게
    가로채여 아예 안 채워지는 경우도 있었다(클릭해도 스탯 안 뜨는 원인).

    이제 3단계로 나눠서, 포지션이 정확히/카테고리로 맞는 선수가 항상
    먼저 자기 자리를 차지하게 하고, 그렇게도 안 맞는 잉여 선수만 맨
    마지막에 진짜 남는 자리로 보낸다:
      1) 정확한 포지션 호환(POSITION_COMPAT) 매치만 전원 시도
      2) 1)에서 못 찾은 선수만 카테고리(GK/DEF/MID/ATK) 매치 시도
      3) 그래도 남은 선수만 마지막 수단으로 아무 빈 슬롯에 순서대로
    """
    slot_filled = [None] * len(slots_only)
    remaining = sorted(candidates, key=lambda x: -(x.get("ovr", 0) or 0))

    def _find_compat_slot(pl):
        pos = pl.get("position", "CM")
        compat = POSITION_COMPAT.get(pos, [pos])
        best_i, best_rank = None, 999
        for i, sp in enumerate(slots_only):
            if slot_filled[i] is None and sp in compat:
                r = compat.index(sp)
                if r < best_rank:
                    best_rank = r; best_i = i
        return best_i

    def _find_category_slot(pl):
        cat = _pos_category(pl.get("position", "CM"))
        for i, sp in enumerate(slots_only):
            if slot_filled[i] is None and _pos_category(sp) == cat:
                return i
        return None

    for phase_fn in (_find_compat_slot, _find_category_slot):
        still_left = []
        for pl in remaining:
            i = phase_fn(pl)
            if i is not None:
                slot_filled[i] = pl
                pl["_slot_idx"] = i
            else:
                still_left.append(pl)
        remaining = still_left

    # 3) 진짜 마지막 수단 — 위 두 단계로도 못 채운 선수는 남은 빈 슬롯에 순서대로.
    open_idx = [i for i in range(len(slot_filled)) if slot_filled[i] is None]
    for i, pl in zip(open_idx, remaining):
        slot_filled[i] = pl
        pl["_slot_idx"] = i

    return slot_filled

def _row_priority(k):
    # 위(공격)→아래(GK) 순서: ATK=0, MID2=1, MID=2, DEF=3, GK=4
    return {"ATK":0,"MID2":1,"MID":2,"DEF":3,"GK":4}.get(k,2)

def _pos_color(pos):
    if pos == "GK": return "#2244aa"
    if pos in ("CB","LB","RB","LWB","RWB"): return "#22aa44"
    if pos in ("CDM","CM","CAM","LM","RM"): return "#8844aa"
    return "#cc2222"


class _CopyableField(QLineEdit):
    """[2026-08 신설, 신민용 요청: "선수 클릭했을 때 뜨는 팀명, 클릭하면
    복붙할 수 있는 상자로"] 읽기 전용 QLineEdit — 클릭(포커스)하면 전체
    텍스트가 자동 선택돼 바로 Ctrl+C로 복사할 수 있다. 라벨과 달리
    박스 테두리가 있어 "복사 가능한 상자"라는 게 시각적으로도 드러난다."""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setReadOnly(True)
        self.setCursorPosition(0)
        self.setStyleSheet(
            "QLineEdit{background:#161616;color:#888;font-size:11px;"
            "border:1px solid #333;border-radius:3px;padding:3px 6px;}"
            "QLineEdit:focus{color:#ccc;border:1px solid #555;}")

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selectAll()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.selectAll()


def _sized_copyable_field(text: str, stylesheet: str) -> _CopyableField:
    """[2026-08 신설] _CopyableField는 QLineEdit 기본 sizeHint를 그대로
    쓰면 실제 글자 수와 무관하게 폭이 너무 넓게 잡힌다(원래는 club_field
    처럼 한 줄에 하나만 두고 stretch=1로 늘려서 이 문제가 안 보였음) —
    이름/포지션/OVR 세 개를 한 줄에 나란히 붙일 땐 내용 길이만큼만
    차지해야 자연스럽다. QFontMetrics로 실제 텍스트 폭을 재서 고정폭으로
    맞춘다(패딩+커서 여유분 포함)."""
    field = _CopyableField(text)
    field.setStyleSheet(stylesheet)
    fm = QFontMetrics(field.font())
    field.setFixedWidth(fm.horizontalAdvance(text) + 24)
    return field


class PlayerStatPopup(QDialog):
    def __init__(self, pl: dict, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle(f"{pl.get('name','')}  [{pl.get('position','')}]")
        self.setMinimumWidth(260)
        self.setStyleSheet("QDialog{background:#1e1e1e;color:#ccc;}")
        lay = QVBoxLayout(self)

        # [2026-08 수정, 신민용 요청: "이름/포지션/OVR가 테두리 있는 상자
        # (필드 위젯)로 되어 있는데, 아래 스탯처럼 일반 테이블 뷰(텍스트/
        # 그리드)로 통일해달라 — 나이/국적도 '항목 : 값' 형태로 같은
        # 표 안에 나열"] 예전엔 이름/포지션/OVR을 _CopyableField(테두리
        # 있는 QLineEdit 상자) 세 개로, 나이/국적은 또 별개의 QLabel
        # 줄로 따로 보여줬다 — 이제 아래 스탯 표(tbl, "스탯"/"수치" 2열
        # QTableWidget)와 완전히 같은 스타일의 작은 표 하나로 통일해서
        # 이름/포지션/OVR/나이/국적을 전부 "항목 | 값" 행으로 나열한다.
        # age/nationality는 기존과 동일하게 값이 있을 때만 행을 추가한다
        # (국제대회 가상 AI처럼 age 자체가 없는 경우가 있어서).
        # [2026-08 확장, 신민용 요청: "어려움 모드일 때 포메이션에서 선수
        # 상세 설정이 아예 안 뜨는데, 이름/국적/포지션/나이는 뜨게 해줘"]
        # 어려움 난이도는 OVR 행 자체를 아예 안 넣는다(신원 식별용 4개
        # 항목 — 이름/포지션/나이/국적 — 만 남긴다).
        _hard = is_hard_mode()
        info_rows = [
            ("이름", pl.get('name', '')),
            ("포지션", pl.get('position', '')),
        ]
        if not _hard:
            info_rows.append(("OVR", str(pl.get('ovr', 0))))
        age = pl.get("age")
        if age:
            info_rows.append(("나이", f"{age}세"))
        nat = pl.get("nationality", "")
        if nat:
            info_rows.append(("국적", nat))

        info_tbl = QTableWidget(len(info_rows), 2)
        info_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        info_tbl.horizontalHeader().setVisible(False)
        info_tbl.verticalHeader().setVisible(False)
        info_tbl.verticalHeader().setDefaultSectionSize(22)
        info_tbl.setStyleSheet(
            "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}")
        for i, (label, value) in enumerate(info_rows):
            label_item = QTableWidgetItem(label)
            label_item.setForeground(QColor("#888"))
            info_tbl.setItem(i, 0, label_item)
            value_item = QTableWidgetItem(value)
            # 이름 행만 기존 hdr(초록 굵은 글씨) 색을 그대로 유지.
            if label == "이름":
                f = value_item.font(); f.setBold(True); value_item.setFont(f)
                value_item.setForeground(QColor("#00cc44"))
            else:
                value_item.setForeground(QColor("#ccc"))
            info_tbl.setItem(i, 1, value_item)
        info_tbl.horizontalHeader().setStretchLastSection(True)
        info_tbl.setFixedHeight(22 * len(info_rows) + 4)
        lay.addWidget(info_tbl)

        # [2026-07 신설, 신민용 요청] 소속팀 표시 (국제대회 화면에서 상대국
        # 선수 클릭 시 — "어느 클럽 소속인지"가 국적보다 새 정보이므로 표시)
        # [2026-08 수정, 신민용 요청: "클릭하면 복붙할 수 있는 상자로"]
        # 팀명이 길거나 특이한 표기라 그대로 검색해보고 싶을 때가 있어,
        # 읽기 전용 QLabel 대신 클릭 시 전체 선택되는 _CopyableField로 바꿨다.
        # [2026-08 확장, 신민용 요청] 어려움 난이도는 소속팀도 숨긴다 —
        # 이름/포지션/나이/국적 4개 항목 외엔 아무것도 안 보여준다는
        # 원칙에 맞춰, 클럽 정보(간접적으로 상대 실력을 짐작하게 함)도
        # 제외.
        club = pl.get("club", "") if not _hard else ""
        if club:
            club_row = QHBoxLayout(); club_row.setSpacing(4)
            club_icon = QLabel("🏟️"); club_icon.setStyleSheet("font-size:11px;")
            club_row.addWidget(club_icon)
            club_field = _CopyableField(club)
            club_row.addWidget(club_field, 1)
            lay.addLayout(club_row)

            # [2026-08 신설, 신민용 요청: "소속팀명 옆에 상자 하나 더
            # 만들어서 어느나라 (몇부)인지 표시해줘"] 국제대회 화면에서
            # 실제 선수를 뽑아오면서 그 선수가 뛰는 클럽의 국가/tier도
            # 같이 가져오게 해뒀다(get_country_squad_players) — 그 값을
            # 팀명 옆 별도 복붙 가능 상자에 "국가명 (N부)" 형식으로 보여준다.
            club_country = pl.get("club_country", "")
            club_tier = pl.get("club_tier")
            if club_country and club_tier:
                league_row = QHBoxLayout(); league_row.setSpacing(4)
                league_icon = QLabel("🏆"); league_icon.setStyleSheet("font-size:11px;")
                league_row.addWidget(league_icon)
                league_field = _CopyableField(f"{club_country} ({club_tier}부)")
                league_row.addWidget(league_field, 1)
                lay.addLayout(league_row)

        # [2026-08 확장, 신민용 요청] 어려움 난이도는 세부 스탯 표 자체를
        # 안 그린다 — 이름/포지션/나이/국적 4개 항목만 남는다.
        if not _hard:
            tbl = QTableWidget(len(ALL_STATS), 2)
            tbl.setHorizontalHeaderLabels(["스탯","수치"])
            tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tbl.setStyleSheet(
                "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}"
                "QHeaderView::section{background:#252525;color:#888;border:none;padding:2px;}")
            tbl.verticalHeader().setVisible(False)
            tbl.verticalHeader().setDefaultSectionSize(22)
            for i, s in enumerate(ALL_STATS):
                tbl.setItem(i, 0, QTableWidgetItem(STAT_KO.get(s, s)))
                tbl.setItem(i, 1, QTableWidgetItem(str(pl.get(s, 0))))
            tbl.horizontalHeader().setStretchLastSection(True)
            lay.addWidget(tbl)

        ok = QPushButton("닫기")
        ok.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                         "border-radius:4px;padding:5px;")
        ok.clicked.connect(self.close)
        lay.addWidget(ok)