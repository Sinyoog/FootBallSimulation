"""
ui/standings_window.py  ─  모달리스, 실시간 갱신
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from game_engine import get_league_standings, get_player
from database import get_conn

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a; border:none; font-size:12px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:4px; }
QTableWidget::item:selected { background:#2a6a2a; }
"""

class StandingsWindow(QDialog):
    def __init__(self, league_id, my_team_id, lang="ko", parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("리그 순위표")
        self.setMinimumSize(620, 420)
        self.setStyleSheet(STYLE)
        self.league_id   = league_id
        self.my_team_id  = my_team_id
        self.lang        = lang
        self._build()
        # 5초마다 자동 갱신
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(5000)

    def _build(self):
        self._lay = QVBoxLayout(self)
        conn = get_conn()
        row  = conn.execute("SELECT name, tier FROM leagues WHERE id=?", (self.league_id,)).fetchone()
        conn.close()
        self._lname = f"{row['name']} ({row['tier']}부)" if row else "리그"

        self._lbl = QLabel(f"📊 {self._lname}")
        self._lbl.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        self._lay.addWidget(self._lbl)

        self._tbl_holder = QVBoxLayout()
        self._lay.addLayout(self._tbl_holder)
        self._tbl = None
        self._fill_table()

        btn = QPushButton("닫기"); btn.clicked.connect(self.close)
        btn.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                          "border-radius:4px;padding:6px;")
        self._lay.addWidget(btn)

    def refresh(self):
        # 내 팀 실제 리그 재조회
        p = get_player()
        if p and p.get("current_team_id"):
            conn = get_conn()
            row = conn.execute(
                "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (p["current_team_id"],)).fetchone()
            conn.close()
            if row: self.league_id = row["id"]
        self._fill_table()

    def _fill_table(self):
        if self._tbl:
            self._tbl_holder.removeWidget(self._tbl)
            self._tbl.deleteLater()

        rows = get_league_standings(self.league_id)
        cols = ["순위","팀명","승","무","패","득점","실점","득실","승점"]
        tbl  = QTableWidget(len(rows), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        # 팀명만 늘어나고 나머지는 내용에 맞게 고정
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setStyleSheet("""
            QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}
            QHeaderView::section{background:#252525;color:#888;border:none;padding:4px;}
        """)

        for i, r in enumerate(rows):
            vals = [str(i+1), r["name"], str(r["wins"]), str(r["draws"]), str(r["losses"]),
                    str(r["goals_for"]), str(r["goals_against"]),
                    str(r["goals_for"]-r["goals_against"]), str(r["pts"])]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if r["id"] == self.my_team_id:
                    item.setBackground(QColor("#1a3a1a"))
                    item.setForeground(QColor("#00ff66"))
                tbl.setItem(i, j, item)

        self._tbl = tbl
        self._tbl_holder.addWidget(tbl)

        # 창 너비를 테이블 전체 너비에 맞게 자동 조정
        tbl.resizeColumnsToContents()
        total_w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount()))
        total_w += tbl.verticalHeader().width() + 40  # 여백
        self.setMinimumWidth(max(620, total_w))
        self.resize(max(self.width(), total_w), self.height())