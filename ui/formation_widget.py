"""
ui/formation_widget.py
좌측: 내 팀 포메이션 캔버스
우측: 상대팀 선택 + 포메이션 캔버스
"""
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QDialog,
    QTableWidget, QTableWidgetItem, QPushButton, QComboBox,
    QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QFont

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

def _mask_ai_names(rows):
    """[2026-07] 포메이션 화면에 실제 개인 이름 대신 'AI'만 표시한다 — 이미
    국제대회(월드컵) 포메이션에서 상대/동료를 가상 선수로 만들 때 쓰던
    "name":"AI" 표기를 클럽팀 포메이션에도 동일하게 적용한 것. 실제 이름
    생성(data/names.py → player_names 테이블)은 계속 그대로 두고 다른
    화면(스쿼드/이적시장/월드브라우저 등)에는 영향 없음 — 여기 포메이션
    캔버스에 넘기기 직전에만 표시용으로 name을 덮어쓴다.
    """
    for r in rows:
        r["name"] = "AI"
    return rows

def _avg_ovr(players):
    if not players: return 0
    return round(sum(p["ovr"] for p in players) / len(players))

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
        result.append({"id": -(i+100), "name": "AI", "position": pos,
                        "ovr": ovr_v, "is_me": False, "club": "",
                        **{s: ovr_v for s in ALL_STATS}})
    return result


def _make_intl_real_players(country: str, avg_ovr: float):
    """[2026-07 재조정, 신민용 지적: "8명 미만 나라는 자국 1부나 남의 나라
    2부에서도 채울 수 있다"] database.get_country_squad_players()의
    3단계 폴백(국적태그→자국리그→해외 하위리그 대륙우선)을 그대로
    쓴다 — 클릭 시 재쿼리하는 구조가 아니라 화면 로드 시 1회만 조회."""
    from database import get_country_squad_players
    picked = get_country_squad_players(country, min_count=8)
    if len(picked) < 8:
        return None
    result = []
    for r in picked:
        result.append({"id": r["id"], "name": r["name"], "position": r["position"],
                        "ovr": r["ovr"], "is_me": False, "club": r["club"],
                        **{s: r["ovr"] for s in ALL_STATS}})
    return result


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
        players = _make_intl_real_players(r["country"], avg) or _make_intl_virtual_players(avg)
        result.append({
            "team_id":   None,
            "name":      r["country"],
            "flag":      r["flag"] or "",
            "avg_ovr":   round(avg),
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
    return [{"team_id": None, "name": opp,
             "flag": fr["flag"] if fr else "",
             "avg_ovr": round(avg),
             "formation": "4-4-2",
             "players": _make_intl_virtual_players(avg)}]

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
        self.setMinimumHeight(300)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color:#1a3a1a;border-radius:6px;")
        self.setMouseTracking(True)

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

    def load_my_team(self, team_id, intl_nat: str = ""):
        """리그팀 또는 국가대표팀 로드.
        intl_nat이 있으면 그 국가 intl_entries 기준으로 포메이션을 그린다.
        [최적화] (team_id, intl_nat) 키로 캐시 — refresh()마다 동일 팀 재쿼리 방지.
        캐시는 FormationWidget 레벨(_my_team_cache)에서 관리.
        """
        from game_engine import get_player
        p = get_player()

        # 캐시 키: 내 선수 OVR/포지션도 반영 (레벨업 시 캐시 무효화)
        _p_sig = (p.get("ovr", 0), p.get("position", "")) if p else (0, "")
        _cache_key = (team_id, intl_nat, _p_sig)
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
            self.formation, self.players = _cache[_cache_key]
            self._player_at = {}; self._positions_xy = []
            self.update()
            return

        if intl_nat:
            # ── 국제전: 내 국가대표팀 선수 구성 ──
            # nationality1 기준으로 ai_players를 국가별로 뽑을 수 없으므로
            # intl_entries OVR로 가상 11명 생성. 나(my_player)는 실제 스탯 사용.
            self.formation = "4-4-2"
            import random

            conn = get_conn()
            entry = conn.execute(
                "SELECT ovr FROM intl_entries WHERE country=? LIMIT 1",
                (intl_nat,)).fetchone()
            conn.close()
            avg_ovr = round(entry["ovr"]) if entry and entry["ovr"] else (p.get("ovr", 50) if p else 50)

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
                      **{s: p.get(s, 0) for s in ALL_STATS}}
                players.append(me)
            for i, sp in enumerate(slots_only):
                if p and i == my_slot_idx:
                    continue
                ovr_v = max(30, min(99, avg_ovr + random.randint(-4, 4)))
                # 모든 스탯을 ovr_v로 채우되 포지션별 편차 부여
                base = {s: ovr_v for s in ALL_STATS}
                players.append({"id": -(i+2), "name": "AI", "position": sp,
                                 "_slot_idx": i, "ovr": ovr_v, "is_me": False, **base})
            self.players = players
        else:
            # ── 리그팀 ──
            conn = get_conn()
            row = conn.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
            self.formation = row["formation"] if row else "4-4-2"
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
                      **{s: p.get(s, 0) for s in ALL_STATS}}

                if rival is None or me["ovr"] > rival["ovr"]:
                    # 내가 그 자리 주전보다 낫다 (또는 빈 자리) → 선발 출전
                    rest = [pl for pl in slot_filled if pl is not None and pl is not rival]
                    self.players = [me] + rest
                else:
                    # 그 자리 주전이 나보다 낫다 → 벤치 (베스트11 그대로 표시, 나는 제외)
                    self.players = [pl for pl in slot_filled if pl is not None]
            else:
                self.players = _mask_ai_names([dict(r) for r in conn.execute(
                    "SELECT * FROM ai_players WHERE team_id=? ORDER BY ovr DESC LIMIT 11",
                    (team_id,)).fetchall()])
            conn.close()

        # 캐시 저장
        if _cache is not None:
            _cache[_cache_key] = (self.formation, list(self.players))
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
        painter.drawEllipse(w//2-24, h//2-24, 48, 48)

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
            is_hov = (i == self._hovered_slot)
            color = "#ffcc00" if is_me else _pos_color(pos)
            painter.setBrush(QBrush(QColor(color)))
            pen_color = "#00ff88" if is_hov else ("#000" if is_me else "#000")
            pen_w = 3 if is_hov else (2 if is_me else 1)
            painter.setPen(QPen(QColor(pen_color), pen_w))
            painter.drawEllipse(px-24, py-24, 48, 48)
            if is_hov:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#00ff8860"), 4))
                painter.drawEllipse(px-28, py-28, 56, 56)
            painter.setPen(QPen(QColor("#000" if is_me else "#fff")))
            f = QFont(); f.setPointSize(10); f.setBold(True); painter.setFont(f)
            # 내 선수는 배치 포지션(field_pos) 표시, AI는 슬롯 포지션
            _disp_pos = pl.get("field_pos", pos) if (pl and is_me) else pos
            painter.drawText(px-24, py-24, 48, 48, Qt.AlignmentFlag.AlignCenter, _disp_pos[:2])
            if pl:
                f2 = QFont(); f2.setPointSize(9); f2.setBold(is_me); painter.setFont(f2)
                painter.setPen(QPen(QColor("#ffff00" if is_me else "#ddd")))
                painter.drawText(px-30, py+26, 60, 16,
                                 Qt.AlignmentFlag.AlignCenter, pl["name"][:4])
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
        찾아가게 한다."""
        rows = {}; row_order = []
        for idx, pos in enumerate(slots):
            k = _row_key(pos)
            if k not in rows: rows[k] = []; row_order.append(k)
            rows[k].append((idx, pos))
        sorted_rows = sorted(row_order, key=lambda x: _row_priority(x))
        total = len(sorted_rows); result = []
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
        new = next((i for i, (px, py, _, _s) in enumerate(self._positions_xy)
                    if (mx-px)**2+(my-py)**2 < 400), -1)
        if new != self._hovered_slot:
            self._hovered_slot = new
            self.setCursor(Qt.CursorShape.PointingHandCursor if new >= 0
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        # [2026-08 신설, 난이도 시스템] 어려움 난이도는 선수를 클릭해도
        # 스탯 팝업이 뜨지 않는다(신민용 확정: "그냥 포메이션만 표시" —
        # 내 선수/상대 선수 구분 없이 예외 없음).
        if is_hard_mode():
            return
        mx, my = event.pos().x(), event.pos().y()
        for i, (px, py, _, _s) in enumerate(self._positions_xy):
            if (mx-px)**2+(my-py)**2 < 400:
                pl = self._player_at.get(i)
                if pl: PlayerStatPopup(pl, self).exec()
                break


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
        self._my_team_cache: dict = {}   # {(team_id, intl_nat): (formation, players)}
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

        # ── 3행: 캔버스 좌우
        split = QHBoxLayout()
        split.setSpacing(4)
        lay.addLayout(split)

        self._my_canvas = _FormationCanvas(is_opponent=False)
        split.addWidget(self._my_canvas, 5)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet("QFrame{color:#333;}")
        split.addWidget(div)

        self._opp_canvas = _FormationCanvas(is_opponent=True)
        split.addWidget(self._opp_canvas, 5)

        # 힌트 바
        # [2026-08 신설, 난이도 시스템] 어려움 난이도에선 클릭해도 스탯이
        # 안 뜨므로("그냥 포메이션만 표시") 안내 문구도 그에 맞게 바꾼다
        # — 안 그러면 "클릭 → 스탯"이라고 해놓고 아무 반응이 없어 버그처럼
        # 보일 수 있다.
        _hint_text = "포메이션만 표시" if is_hard_mode() else "클릭 → 스탯"
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
        self._build_filter_row(options or [], context)
        self._apply_context(team_id, context, manager_rel)

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
        self._my_canvas.load_my_team(team_id, intl_nat=my_nat)

        # ── 좌측 레이블: 국제전 → 국가명+OVR / 리그 → 팀명+OVR
        # [2026-08 신설, 난이도 시스템] 어려움 난이도는 "평균 OVR 76" 같은
        # 수치를 아예 안 붙인다(신민용 확정).
        my_avg = self._my_canvas._calc_avg_ovr()
        _ovr_suffix = "" if is_hard_mode() else f"  |  평균 OVR {my_avg}"
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
            suffix = "" if _hard else f"  OVR {t['avg_ovr']}"
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


class PlayerStatPopup(QDialog):
    def __init__(self, pl: dict, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle(f"{pl.get('name','')}  [{pl.get('position','')}]")
        self.setMinimumWidth(260)
        self.setStyleSheet("QDialog{background:#1e1e1e;color:#ccc;}")
        lay = QVBoxLayout(self)

        hdr = QLabel(f"{pl.get('name','')}  [{pl.get('position','')}]  OVR {pl.get('ovr',0)}")
        hdr.setStyleSheet("color:#00cc44;font-size:13px;font-weight:bold;")
        lay.addWidget(hdr)

        # [2026-07 신설, 신민용 요청] 국적 표시
        nat = pl.get("nationality", "")
        if nat:
            nat_lbl = QLabel(f"🌍 {nat}")
            nat_lbl.setStyleSheet("color:#888;font-size:11px;")
            lay.addWidget(nat_lbl)

        # [2026-07 신설, 신민용 요청] 소속팀 표시 (국제대회 화면에서 상대국
        # 선수 클릭 시 — "어느 클럽 소속인지"가 국적보다 새 정보이므로 표시)
        club = pl.get("club", "")
        if club:
            club_lbl = QLabel(f"🏟️ {club}")
            club_lbl.setStyleSheet("color:#888;font-size:11px;")
            lay.addWidget(club_lbl)

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