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
from constants import FORMATION_SLOTS, STAT_KO, ALL_STATS


# ─────────────────────────────────────────────
# 상대팀 데이터 조회
# ─────────────────────────────────────────────

def _players_for_team(team_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM ai_players WHERE team_id=? LIMIT 11", (team_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

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

def _fetch_intl_opponents(tournament_id, my_nat, grp=None):
    """국제대회 상대팀 목록.
    grp 지정 시 내 조(grp) 팀만 반환 (조별리그).
    grp 없으면 대회 전체 참가국 반환 (예: 대회 로비에서 볼 때 fallback).
    """
    conn = get_conn()
    if grp:
        # 내 그룹 팀만 (조별리그: 나 제외 상대 3팀)
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
    return [{
        "team_id":   None,
        "name":      r["country"],
        "flag":      r["flag"] or "",
        "avg_ovr":   round(r["ovr"]) if r["ovr"] else 0,
        "formation": "4-4-2",
        "players":   [],
    } for r in rows]

def _fetch_cl_opponents(tournament_id, my_team_id, grp=None):
    """챔피언스리그 상대팀 목록.
    grp 지정 시 내 조 팀만 반환 (조별리그).
    """
    conn = get_conn()
    if grp:
        rows = conn.execute(
            "SELECT team_id, team_name, flag, ovr FROM cl_entries "
            "WHERE tournament_id=? AND team_id!=? AND grp=?",
            (tournament_id, my_team_id, grp)).fetchall()
    else:
        rows = conn.execute(
            "SELECT team_id, team_name, flag, ovr FROM cl_entries "
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
    return [{"team_id": None, "name": opp,
             "flag": fr["flag"] if fr else "",
             "avg_ovr": round(fr["ovr"]) if fr and fr["ovr"] else 0,
             "formation": "4-4-2", "players": []}]

def _fetch_cl_ko_opp(tournament_id, my_team_id, week):
    conn = get_conn()
    m = conn.execute(
        "SELECT * FROM cl_matches WHERE tournament_id=? AND week=? "
        "AND home_score=-1 AND (home_team_id=? OR away_team_id=?)",
        (tournament_id, week, my_team_id, my_team_id)).fetchone()
    if not m:
        conn.close(); return None
    opp_id = m["away_team_id"] if m["home_team_id"] == my_team_id else m["home_team_id"]
    e = conn.execute(
        "SELECT team_name, flag, ovr FROM cl_entries WHERE tournament_id=? AND team_id=?",
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

    def _calc_avg_ovr(self):
        """현재 로드된 선수들의 평균 OVR."""
        if not self.players: return 0
        ovrs = []
        for p in self.players:
            v = p.get("ovr", 0)
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = 0
            if 1 <= v <= 100:   # 비정상값 제외
                ovrs.append(v)
        if not ovrs: return 0
        return round(sum(ovrs) / len(ovrs))

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

        if _cache is not None and _cache_key in _cache:
            self.formation, self.players = _cache[_cache_key]
            self._player_at = {}; self._positions_xy = []
            self.update()
            return

        if intl_nat:
            # ── 국제전: 내 국가대표팀 선수 구성 ──
            # 국제전은 ai_players 대신 국가대표 평균 OVR로만 구성되므로
            # 내 선수(나)를 포함한 가상 11명으로 포메이션 표시
            self.formation = "4-4-2"  # 국가대표 기본 포메이션
            players = []
            if p:
                me = {"id": -1, "name": p.get("name", "나"),
                      "position": p.get("position", "MF"),
                      "ovr": p.get("ovr", 40), "is_me": True,
                      **{s: p.get(s, 0) for s in ALL_STATS}}
                players.append(me)
            # 나머지 10명: intl_entries의 국가 OVR로 가상 선수 생성
            conn = get_conn()
            entry = conn.execute(
                "SELECT ovr FROM intl_entries WHERE country=? LIMIT 1",
                (intl_nat,)).fetchone()
            conn.close()
            avg_ovr = round(entry["ovr"]) if entry and entry["ovr"] else (p.get("ovr", 50) if p else 50)
            import random
            pos_list = ["GK", "CB", "CB", "LB", "RB", "CM", "CM", "CAM", "LW", "RW"]
            for i, pos in enumerate(pos_list[:10 - (len(players))]):
                ovr_v = max(30, min(99, avg_ovr + random.randint(-5, 5)))
                players.append({"id": -(i+2), "name": "", "position": pos,
                                 "ovr": ovr_v, "is_me": False,
                                 **{s: ovr_v for s in ALL_STATS}})
            self.players = players
        else:
            # ── 리그팀 ──
            conn = get_conn()
            row = conn.execute("SELECT formation FROM teams WHERE id=?", (team_id,)).fetchone()
            self.formation = row["formation"] if row else "4-4-2"
            my_tid = p.get("current_team_id", 0) if p else 0
            if my_tid == team_id and p:
                me = {"id": -1, "name": p.get("name", "나"),
                      "position": p.get("position", "MF"),
                      "ovr": p.get("ovr", 40), "is_me": True,
                      **{s: p.get(s, 0) for s in ALL_STATS}}
                ais = [dict(r) for r in conn.execute(
                    "SELECT * FROM ai_players WHERE team_id=? LIMIT 10", (team_id,)).fetchall()]
                self.players = [me] + ais
            else:
                self.players = [dict(r) for r in conn.execute(
                    "SELECT * FROM ai_players WHERE team_id=? LIMIT 11", (team_id,)).fetchall()]
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
        """상대팀 dict ({formation, players}) 로드."""
        self.formation = team.get("formation") or "4-4-2"
        self.players   = team.get("players") or []
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
        player_at = {}
        if self.players and self.players[0].get("is_me"):
            me = self.players[0]
            my_cat = _pos_category(me.get("position", "MF"))
            my_slot = next((i for i, (_, _, sp) in enumerate(positions_xy)
                            if _pos_category(sp) == my_cat), 0)
            player_at[my_slot] = me
            ai_idx = 0
            for si in range(len(positions_xy)):
                if si == my_slot: continue
                if ai_idx + 1 < len(self.players):
                    player_at[si] = self.players[ai_idx + 1]; ai_idx += 1
        else:
            for i, p in enumerate(self.players[:len(positions_xy)]):
                player_at[i] = p
        self._player_at = player_at

        for i, (px, py, pos) in enumerate(positions_xy):
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
            painter.drawText(px-24, py-24, 48, 48, Qt.AlignmentFlag.AlignCenter, pos[:2])
            if pl:
                f2 = QFont(); f2.setPointSize(9); f2.setBold(is_me); painter.setFont(f2)
                painter.setPen(QPen(QColor("#ffff00" if is_me else "#ddd")))
                painter.drawText(px-30, py+26, 60, 16,
                                 Qt.AlignmentFlag.AlignCenter, pl["name"][:4])
        painter.end()

    def _calc_positions(self, slots, w, h):
        rows = {}; row_order = []
        for pos in slots:
            k = _row_key(pos)
            if k not in rows: rows[k] = []; row_order.append(k)
            rows[k].append(pos)
        sorted_rows = sorted(row_order, key=lambda x: _row_priority(x))
        total = len(sorted_rows); result = []
        for ri, rk in enumerate(sorted_rows):
            poss = rows[rk]; cnt = len(poss)
            ry = 16 + int((ri + 0.5) * (h - 32) / total)
            for ci, pos in enumerate(poss):
                result.append((int((ci+1)*w/(cnt+1)), ry, pos))
        return result

    def mouseMoveEvent(self, event):
        mx, my = event.pos().x(), event.pos().y()
        new = next((i for i, (px, py, _) in enumerate(self._positions_xy)
                    if (mx-px)**2+(my-py)**2 < 400), -1)
        if new != self._hovered_slot:
            self._hovered_slot = new
            self.setCursor(Qt.CursorShape.PointingHandCursor if new >= 0
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        mx, my = event.pos().x(), event.pos().y()
        for i, (px, py, _) in enumerate(self._positions_xy):
            if (mx-px)**2+(my-py)**2 < 400:
                pl = self._player_at.get(i)
                if pl: PlayerStatPopup(pl, self).exec()
                break


# ─────────────────────────────────────────────
# 메인 위젯
# ─────────────────────────────────────────────

_CTX_STYLE = {
    "league": ("color:#aaffaa;", "⚽"),
    "intl":   ("color:#66ccff;", "🌍"),
    "cl":     ("color:#ffd24d;", "🏆"),
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
        hint_bar = QHBoxLayout()
        lh = QLabel("클릭 → 스탯"); lh.setStyleSheet(_HINT_STYLE)
        rh = QLabel("클릭 → 스탯"); rh.setStyleSheet(_HINT_STYLE)
        rh.setAlignment(Qt.AlignmentFlag.AlignRight)
        hint_bar.addWidget(lh); hint_bar.addStretch(); hint_bar.addWidget(rh)
        lay.addLayout(hint_bar)

    def load_team(self, team_id, context: dict = None, manager_rel: int = 50):
        is_intl = bool(context and context.get("intl"))
        my_nat  = context.get("my_nat", "") if is_intl else ""

        # ── 내 팀 캔버스 (국제전이면 국가대표 모드)
        self._my_canvas.load_my_team(team_id, intl_nat=my_nat)

        # ── 좌측 레이블: 국제전 → 국가명+OVR / 리그 → 팀명+OVR
        my_avg = self._my_canvas._calc_avg_ovr()
        if is_intl:
            # 국가 flag + 국가명 표시
            conn = get_conn()
            crow = conn.execute(
                "SELECT flag FROM countries WHERE name=?", (my_nat,)).fetchone()
            conn.close()
            flag = (crow["flag"] + " ") if crow and crow["flag"] else ""
            self.lbl_my.setText(f"{flag}{my_nat}  |  평균 OVR {my_avg}")
            self.lbl_my.setStyleSheet("color:#ffd700;font-weight:bold;")  # 금색으로 강조
        else:
            conn = get_conn()
            trow = conn.execute("SELECT name FROM teams WHERE id=?", (team_id,)).fetchone()
            conn.close()
            team_name = trow["name"] if trow else ""
            self.lbl_my.setText(f"내 팀: {team_name}  |  평균 OVR {my_avg}")
            self.lbl_my.setStyleSheet("color:#ffd700;font-weight:bold;")

        # ── 컨텍스트 레이블 (대회명 표시줄)
        if context:
            kind = "intl" if context.get("intl") else "cl" if context.get("cl") else "league"
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
        ctx_key = (team_id, repr(context))
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
            tid   = context["tournament_id"]
            stage = context.get("stage", "group")
            week  = context.get("week", 0)
            grp   = context.get("grp", "")
            if stage != "group":
                # 플레이오프/토너먼트: 이번 주 상대 1팀만
                res = _fetch_cl_ko_opp(tid, team_id, week)
                if res: return res
            # 조별리그: 내 그룹(grp)에 있는 팀만
            return _fetch_cl_opponents(tid, team_id, grp=grp or None)
        else:
            conn = get_conn()
            row = conn.execute("SELECT league_id FROM teams WHERE id=?", (team_id,)).fetchone()
            conn.close()
            if not row: return []
            return _fetch_league_opponents(team_id, row["league_id"])

    def _fill_combo(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        for t in self._opp_teams:
            flag = t["flag"] + " " if t["flag"] else ""
            self.combo.addItem(f"{flag}{t['name']}  OVR {t['avg_ovr']}")
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

def _row_key(pos):
    if pos == "GK": return "GK"
    if pos in ("CB","LB","RB","LWB","RWB"): return "DEF"
    if pos in ("CDM","CM","CAM","LM","RM","DM"): return "MID"
    return "ATK"

def _pos_category(pos):
    if pos == "GK": return "GK"
    if pos in ("CB","LB","RB","LWB","RWB","SW"): return "DEF"
    if pos in ("CDM","CM","CAM","LM","RM","DM","AM"): return "MID"
    return "ATK"

def _row_priority(k):
    return {"ATK":0,"MID":1,"DEF":2,"GK":3}.get(k,2)

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