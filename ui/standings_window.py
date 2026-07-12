"""
ui/standings_window.py  ─  모달리스, 실시간 갱신
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from game_engine import get_league_standings, get_player, get_state
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
        # [최적화] main_window.refresh_all()이 부르는 self.refresh()는 항상
        #   그대로 즉시 다시 그린다(기존 동작 100% 유지 — 이적/승강/국가대표
        #   선발 등 즉시 반영돼야 하는 명시적 갱신 경로).
        #   반대로 5초짜리 배경 타이머는 "창을 그냥 열어두고 보고 있는" 동안
        #   불필요하게 테이블을 통째로 부수고 다시 그리는 게 렉의 원인이었다.
        #   순위표는 하루가 실제로 진행되기 전까진 절대 안 바뀌므로, 타이머
        #   폴링에서만 "직전과 조건이 같으면 건너뛰기"를 적용한다 —
        #   사용자가 보는 결과는 항상 기존과 동일하게 유지된다.
        self._last_sig = self._compute_sig()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_refresh)
        self._timer.start(5000)

    def pause_refresh(self):
        """[스레드 안전] 백그라운드 워커가 DB에 쓰는 동안 5초 타이머가
        같은 커넥션에 SELECT를 던지지 않도록 잠시 멈춘다."""
        self._timer.stop()

    def resume_refresh(self):
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

    def _compute_sig(self, league_id=None, my_team_id=None):
        """순위표가 달라질 수 있는 최소 조건 스냅샷(리그/내팀/진행일자).
        타이머 폴링 전용 — 이 값이 안 바뀌면 하루가 진행되지 않았다는
        뜻이라 순위표 내용은 100% 그대로다."""
        st = get_state()
        return (self.league_id if league_id is None else league_id,
                self.my_team_id if my_team_id is None else my_team_id,
                st.get("current_day") if st else None,
                st.get("current_season") if st else None)

    def _poll_refresh(self):
        """5초 배경 타이머 전용 갱신. refresh()와 같은 저비용 조회(내 팀/리그
        재확인)만 먼저 해보고, 그 결과로 만든 시그니처가 직전과 같으면
        무거운 테이블 재조회·재렌더링을 건너뛴다(성능 최적화). 조건이
        하나라도 바뀌었으면 refresh()를 그대로 호출해 완전히 다시 그린다
        — 즉 사용자가 보는 결과는 항상 기존과 동일하다."""
        p = get_player()
        league_id = self.league_id
        my_team_id = self.my_team_id
        if p and p.get("current_team_id"):
            my_team_id = p["current_team_id"]
            conn = get_conn()
            row = conn.execute(
                "SELECT l.id AS lid FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (my_team_id,)).fetchone()
            conn.close()
            if row:
                league_id = row["lid"]
        sig = self._compute_sig(league_id, my_team_id)
        if sig == self._last_sig:
            return
        self.refresh()

    def refresh(self):
        # 내 팀의 '현재' 소속 리그와 팀 ID를 재조회한다.
        #   - 이적: current_team_id 가 바뀜 → my_team_id 갱신(하이라이트 정확)
        #   - 승강: 같은 팀이라도 소속 league_id 가 바뀜 → league_id 갱신
        p = get_player()
        if p and p.get("current_team_id"):
            tid = p["current_team_id"]
            self.my_team_id = tid
            conn = get_conn()
            row = conn.execute(
                "SELECT l.id AS lid, l.name AS lname, l.tier AS tier "
                "FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (tid,)).fetchone()
            conn.close()
            if row:
                self.league_id = row["lid"]
                # 리그가 바뀌었으면 제목도 갱신(예: 2부→1부 승격)
                new_name = f"{row['lname']} ({row['tier']}부)"
                if new_name != self._lname:
                    self._lname = new_name
                    self._lbl.setText(f"📊 {self._lname}")
        self._fill_table()
        self._last_sig = self._compute_sig()

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