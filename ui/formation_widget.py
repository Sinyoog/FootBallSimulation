"""
ui/formation_widget.py
"""
from PyQt6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QDialog,
    QTableWidget, QTableWidgetItem, QPushButton
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QFont

from database import get_conn
from constants import FORMATION_SLOTS, STAT_KO, ALL_STATS


class FormationWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.team_id   = 0
        self.formation = "4-4-2"
        self.players   = []  # list of dict
        self._player_at: dict = {}   # slot_index → player (paintEvent에서 채움)
        self._positions_xy: list = []  # (px, py, pos) 리스트
        self._hovered_slot: int = -1   # 마우스 올라간 슬롯
        self.setMinimumHeight(260)
        self.setStyleSheet("background-color:#1a3a1a; border-radius:8px;")
        self.setMouseTracking(True)  # hover 감지

        self.title = QLabel("팀 포메이션 [4-4-2]  (선수 클릭 → 스탯)")
        self.title.setStyleSheet("color:#00cc44;font-size:12px;font-weight:bold;")
        self.title.setParent(self)
        self.title.move(8, 4)

    def load_team(self, team_id):
        self.team_id = team_id
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT formation FROM teams WHERE id=?", (team_id,))
        row = c.fetchone()
        self.formation = row["formation"] if row else "4-4-2"

        # 내 선수 정보
        from game_engine import get_player
        p = get_player()
        my_tid = p.get("current_team_id", 0) if p else 0

        if my_tid == team_id and p:
            my_entry = {
                "id": -1,
                "name": p.get("name", "나"),
                "position": p.get("position", "MF"),
                "ovr": p.get("ovr", 40),
                "is_me": True,
                # 내 선수 스탯 전부 포함
                **{s: p.get(s, 0) for s in ALL_STATS},
            }
            c.execute("SELECT * FROM ai_players WHERE team_id=? LIMIT 10", (team_id,))
            ai_players = [dict(r) for r in c.fetchall()]
            self.players = [my_entry] + ai_players
        else:
            c.execute("SELECT * FROM ai_players WHERE team_id=? LIMIT 11", (team_id,))
            self.players = [dict(r) for r in c.fetchall()]

        conn.close()
        self.title.setText(f"팀 포메이션 [{self.formation}]  (선수 클릭 → 스탯)")
        self._player_at = {}
        self._positions_xy = []
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width(); h = self.height() - 24
        oy = 24  # title offset

        # 잔디
        painter.fillRect(0, oy, w, h, QBrush(QColor("#1a3a1a")))

        # 선
        painter.setPen(QPen(QColor("#2a5a2a"), 1))
        painter.drawRect(20, oy+10, w-40, h-20)
        painter.drawLine(20, oy+10+h//2-10, w-20, oy+10+h//2-10)
        painter.drawEllipse(w//2-30, oy+h//2-40, 60, 60)

        slots = FORMATION_SLOTS.get(self.formation, FORMATION_SLOTS["4-4-2"])
        positions_xy = self._calc_positions(slots, w, h, oy)
        self._positions_xy = positions_xy  # ← 저장

        # 슬롯 → 선수 매핑 계산
        player_at = {}
        if self.players and self.players[0].get("is_me"):
            me = self.players[0]
            my_pos = me.get("position", "MF")
            my_cat = _pos_category(my_pos)
            my_slot = None
            for idx, (px, py, slot_pos) in enumerate(positions_xy):
                if _pos_category(slot_pos) == my_cat:
                    my_slot = idx
                    break
            if my_slot is None:
                my_slot = 0
            player_at[my_slot] = me
            ai_idx = 0
            for slot_idx in range(len(positions_xy)):
                if slot_idx == my_slot: continue
                if ai_idx + 1 < len(self.players):
                    player_at[slot_idx] = self.players[ai_idx + 1]
                    ai_idx += 1
        else:
            for idx, p in enumerate(self.players[:len(positions_xy)]):
                player_at[idx] = p

        self._player_at = player_at  # ← 저장 (클릭 시 재사용)

        for i, (px, py, pos) in enumerate(positions_xy):
            pl = player_at.get(i)
            is_me = pl.get("is_me", False) if pl else False
            is_hovered = (i == self._hovered_slot)

            color = "#ffcc00" if is_me else _pos_color(pos)
            painter.setBrush(QBrush(QColor(color)))

            # 호버 시 형광 테두리
            if is_hovered:
                painter.setPen(QPen(QColor("#00ff88"), 3))
            elif is_me:
                painter.setPen(QPen(QColor("#000000"), 2))
            else:
                painter.setPen(QPen(QColor("#000000"), 1))

            painter.drawEllipse(px-18, py-18, 36, 36)

            # 호버 시 외곽 발광 효과 (한 겹 더)
            if is_hovered:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#00ff8880"), 5))
                painter.drawEllipse(px-22, py-22, 44, 44)

            painter.setPen(QPen(QColor("#000000" if is_me else "white")))
            f = QFont(); f.setPointSize(8); f.setBold(True)
            painter.setFont(f)
            painter.drawText(px-18, py-18, 36, 36,
                             Qt.AlignmentFlag.AlignCenter, pos[:2])

            if pl:
                f2 = QFont(); f2.setPointSize(7); f2.setBold(is_me)
                painter.setFont(f2)
                painter.setPen(QPen(QColor("#ffff00" if is_me else "#dddddd")))
                short = pl["name"][:4]
                painter.drawText(px-25, py+20, 50, 14,
                                 Qt.AlignmentFlag.AlignCenter, short)

        painter.end()

    def _calc_positions(self, slots, w, h, oy):
        rows = {}
        row_order = []
        for pos in slots:
            row_key = _row_key(pos)
            if row_key not in rows:
                rows[row_key] = []
                row_order.append(row_key)
            rows[row_key].append(pos)

        sorted_rows = sorted(row_order, key=lambda x: _row_priority(x))
        total_rows  = len(sorted_rows)
        result = []

        for ri, rkey in enumerate(sorted_rows):
            poss = rows[rkey]
            cnt  = len(poss)
            row_y = oy + 20 + int((ri+0.5) * (h-40) / total_rows)
            for ci, pos in enumerate(poss):
                col_x = int((ci+1) * w / (cnt+1))
                result.append((col_x, row_y, pos))

        return result

    def mouseMoveEvent(self, event):
        """마우스 이동 시 호버 슬롯 갱신 → 형광 테두리 repaint."""
        mx, my = event.pos().x(), event.pos().y()
        new_hovered = -1
        for i, (px, py, _) in enumerate(self._positions_xy):
            if (mx-px)**2 + (my-py)**2 < 484:  # 반지름 22px
                new_hovered = i
                break
        if new_hovered != self._hovered_slot:
            self._hovered_slot = new_hovered
            self.setCursor(Qt.CursorShape.PointingHandCursor if new_hovered >= 0
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        if not self.players: return
        mx, my = event.pos().x(), event.pos().y()
        for i, (px, py, _) in enumerate(self._positions_xy):
            if (mx-px)**2 + (my-py)**2 < 484:
                pl = self._player_at.get(i)   # ← paintEvent에서 저장한 매핑 사용
                if pl:
                    PlayerStatPopup(pl, self).exec()
                break


def _row_key(pos):
    if pos in ("GK",): return "GK"
    if pos in ("CB","LB","RB","LWB","RWB"): return "DEF"
    if pos in ("CDM","CM","CAM","LM","RM","DM"): return "MID"
    return "ATK"

def _pos_category(pos):
    """선수 포지션 → 슬롯 카테고리 매핑"""
    if pos == "GK": return "GK"
    if pos in ("CB","LB","RB","LWB","RWB","SW"): return "DEF"
    if pos in ("CDM","CM","CAM","LM","RM","DM","AM"): return "MID"
    return "ATK"  # ST, CF, LW, RW 등

def _row_priority(k):
    return {"ATK":0,"MID":1,"DEF":2,"GK":3}.get(k,2)

def _pos_color(pos):
    if pos in ("GK",): return "#2244aa"
    if pos in ("CB","LB","RB","LWB","RWB"): return "#22aa44"
    if pos in ("CDM","CM","CAM","LM","RM"): return "#8844aa"
    return "#cc2222"


class PlayerStatPopup(QDialog):
    def __init__(self, pl: dict, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle(f"{pl.get('name','')}  [{pl.get('position','')}]")
        self.setMinimumWidth(280)
        self.setStyleSheet("QDialog{background:#1e1e1e;color:#ccc;}")
        lay = QVBoxLayout(self)

        header = QLabel(f"{pl['name']}  [{pl['position']}]  OVR {pl.get('ovr',0)}")
        header.setStyleSheet("color:#00cc44;font-size:14px;font-weight:bold;")
        lay.addWidget(header)

        tbl = QTableWidget(len(ALL_STATS), 2)
        tbl.setHorizontalHeaderLabels(["스탯","수치"])
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setStyleSheet("QTableWidget{background:#1e1e1e;color:#ccc;"
                          "gridline-color:#2a2a2a;border:none;}"
                          "QHeaderView::section{background:#252525;color:#888;border:none;}")
        for i, s in enumerate(ALL_STATS):
            tbl.setItem(i,0,QTableWidgetItem(STAT_KO.get(s,s)))
            tbl.setItem(i,1,QTableWidgetItem(str(pl.get(s,0))))
        tbl.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(tbl)

        ok = QPushButton("닫기")
        ok.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                         "border-radius:4px;padding:6px;")
        ok.clicked.connect(self.close)
        lay.addWidget(ok)