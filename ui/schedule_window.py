"""
ui/schedule_window.py  ─  모달리스, 실시간 갱신
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QTabWidget, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from game_engine import get_schedule, get_player, get_state
from database import get_conn

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QTabWidget::pane { border:1px solid #333; background:#1e1e1e; }
QTabBar::tab { background:#252525; color:#888; padding:6px 16px; }
QTabBar::tab:selected { background:#1e1e1e; color:#00cc44; border-bottom:2px solid #00cc44; }
QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a; border:none; font-size:12px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:4px; }
"""

class ScheduleWindow(QDialog):
    def __init__(self, league_id, my_team_id, season, lang="ko", parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("경기 일정")
        self.setMinimumSize(680, 500)
        self.setStyleSheet(STYLE)
        self.league_id  = league_id
        self.my_team_id = my_team_id
        self.season     = season
        self.lang       = lang
        self._build()
        # 5초마다 자동 갱신
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(5000)

    def _build(self):
        self._root = QVBoxLayout(self)

        conn = get_conn()
        row  = conn.execute("SELECT name, tier FROM leagues WHERE id=?", (self.league_id,)).fetchone()
        conn.close()
        lname = f"{row['name']} ({row['tier']}부)" if row else "리그"
        self._lbl = QLabel(f"📅 {lname}")
        self._lbl.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        self._root.addWidget(self._lbl)

        self._tab = QTabWidget()
        self._root.addWidget(self._tab)
        self._fill_tabs()

        btn = QPushButton("닫기"); btn.clicked.connect(self.close)
        btn.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                          "border-radius:4px;padding:6px;")
        self._root.addWidget(btn)

    def refresh(self):
        p = get_player(); st = get_state()
        if p and p.get("current_team_id"):
            conn = get_conn()
            row = conn.execute(
                "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (p["current_team_id"],)).fetchone()
            conn.close()
            if row: self.league_id = row["id"]
            self.my_team_id = p["current_team_id"]
        if st: self.season = st["current_season"]
        self._fill_tabs()

    def _fill_tabs(self):
        cur = self._tab.currentIndex()
        while self._tab.count(): self._tab.removeTab(0)

        all_data = get_schedule(self.league_id, self.season)
        my_data  = [r for r in all_data
                    if r["home_team_id"]==self.my_team_id or r["away_team_id"]==self.my_team_id]

        self._tab.addTab(self._make_table(my_data, my_view=True),  "내 경기")
        self._tab.addTab(self._make_table(all_data, my_view=False), "전체 일정")
        if 0 <= cur < self._tab.count():
            self._tab.setCurrentIndex(cur)

    def _make_table(self, data, my_view=True):
        w   = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        # 결과 컬럼: 전체=홈/무/원, 내경기=승/무/패  +  승패(내 경기용) 분리
        cols = ["주차", "홈팀", "스코어", "원정팀", "승패"]
        tbl  = QTableWidget(len(data), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setStyleSheet("""
            QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}
            QHeaderView::section{background:#252525;color:#888;border:none;padding:4px;}
        """)

        for i, r in enumerate(data):
            hs  = r["home_score"]; as_ = r["away_score"]
            score   = f"{hs} - {as_}" if hs >= 0 else "예정"
            is_my   = r["home_team_id"]==self.my_team_id or r["away_team_id"]==self.my_team_id
            played  = hs >= 0

            # 승패 컬럼 (내 팀 기준)
            if not played or not is_my:
                col_wdl = ""
                wdl_color = "#555555"
            else:
                if r["home_team_id"] == self.my_team_id:
                    col_wdl = "승" if hs>as_ else ("무" if hs==as_ else "패")
                else:
                    col_wdl = "패" if hs>as_ else ("무" if hs==as_ else "승")
                wdl_color = "#00cc44" if col_wdl=="승" else ("#888888" if col_wdl=="무" else "#cc4444")

            # 행 전체 색상
            if not played:
                row_color = QColor("#555555")
            elif is_my:
                if col_wdl == "승":   row_color = QColor("#00cc44")
                elif col_wdl == "무": row_color = QColor("#888888")
                else:                  row_color = QColor("#cc4444")
            else:
                row_color = QColor("#aaaaaa")

            vals = [str(r["week"]), r.get("home_name",""), score,
                    r.get("away_name",""), col_wdl]

            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 4:
                    item.setForeground(QColor(wdl_color))
                else:
                    item.setForeground(row_color)
                tbl.setItem(i, j, item)

        lay.addWidget(tbl)

        # 창 너비 자동 조정
        tbl.resizeColumnsToContents()
        total_w = sum(tbl.columnWidth(j) for j in range(tbl.columnCount())) + 60
        self.setMinimumWidth(max(700, total_w))
        if self.width() < total_w:
            self.resize(total_w, self.height())

        return w