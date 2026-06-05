"""
ui/main_window.py
"""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QFrame, QSplitter, QScrollArea
)
from PyQt6.QtCore import Qt

from game_engine import get_player, get_state, fmt_money
from constants import SEASON_PHASES

STYLE = """
QMainWindow, QWidget { background-color: #1a1a1a; color: #e0e0e0;
    font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; }
#topBar { background-color: #111111; border-bottom: 1px solid #2a2a2a; }
#topInfo { color: #00cc44; font-size: 13px; font-weight: bold; }
#topBtn  { background-color: #2a2a2a; color: #00cc44;
           border: 1px solid #3a3a3a; border-radius: 4px;
           padding: 2px 10px; font-size: 12px; }
#topBtn:hover { background-color: #3a3a3a; }
QScrollArea { border: none; background-color: #1a1a1a; }
QScrollBar:vertical { background: #1a1a1a; width: 6px; }
QScrollBar::handle:vertical { background: #3a3a3a; border-radius: 3px; }
QSplitter::handle { background-color: #2a2a2a; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.lang = "ko"
        self.setWindowTitle("축구 선수 커리어 시뮬레이션")
        self.setMinimumSize(1280, 720)
        self.setStyleSheet(STYLE)
        self._build()
        self.refresh_all()

    # ── 빌드 ──────────────────────────────────────

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        vlay = QVBoxLayout(root)
        vlay.setContentsMargins(0,0,0,0)
        vlay.setSpacing(0)

        # 상단 바
        self.top_bar = self._make_top_bar()
        vlay.addWidget(self.top_bar)

        # 3패널
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)

        from ui.player_panel  import PlayerPanel
        from ui.center_panel  import CenterPanel
        from ui.log_panel     import LogPanel

        self.player_panel = PlayerPanel(self)
        self.center_panel = CenterPanel(self)
        self.log_panel    = LogPanel(self)

        def scroll(w, minw, maxw):
            s = QScrollArea()
            s.setWidgetResizable(True)
            s.setWidget(w)
            s.setMinimumWidth(minw)
            s.setMaximumWidth(maxw)
            return s

        splitter.addWidget(scroll(self.player_panel, 220, 290))
        splitter.addWidget(scroll(self.center_panel, 400, 9999))
        splitter.addWidget(scroll(self.log_panel,    240, 340))
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 3)

        vlay.addWidget(splitter)

    def _make_top_bar(self):
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(40)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10,0,10,0)

        self.top_label = QLabel("")
        self.top_label.setObjectName("topInfo")
        lay.addWidget(self.top_label)
        lay.addStretch()

        self.lang_btn = QPushButton("EN")
        self.lang_btn.setObjectName("topBtn")
        self.lang_btn.setFixedSize(40,28)
        self.lang_btn.clicked.connect(self._toggle_lang)
        lay.addWidget(self.lang_btn)

        career_btn = QPushButton("📋 지금까지")
        career_btn.setObjectName("topBtn")
        career_btn.setFixedHeight(28)
        career_btn.clicked.connect(self._show_career)
        lay.addWidget(career_btn)

        return bar

    # ── 갱신 ──────────────────────────────────────

    def refresh_all(self):
        self._update_top()
        self.player_panel.refresh()
        self.center_panel.refresh()
        self.log_panel.refresh()

    def _update_top(self):
        p  = get_player()
        st = get_state()
        if not p or not st:
            return

        year   = st["current_year"]
        week   = st["current_week"]
        season = st["current_season"]
        phase  = _phase_label(week, self.lang)

        if self.lang == "ko":
            txt = f"{year}년  |  {season}시즌 {week}주차  |  [{phase}]"
            if p.get("nationality"):
                txt += f"  |  {p.get('flag','')} {p['nationality']}"
            if p.get("current_team_id"):
                from database import get_conn
                conn = get_conn()
                row = conn.execute("SELECT name FROM teams WHERE id=?",
                                   (p["current_team_id"],)).fetchone()
                conn.close()
                if row: txt += f"  |  {row['name']}"
            txt += f"  |  {p['name']} {p['age']}세  |  OVR {p['ovr']}"
            txt += f"  |  에이전트[{p.get('agent_grade','F')}]"
        else:
            txt = f"{year}  |  S{season} W{week}  |  [{phase}]"
            txt += f"  |  {p['name']} {p['age']}  |  OVR {p['ovr']}"

        self.top_label.setText(txt)

    # ── 액션 ──────────────────────────────────────

    def _toggle_lang(self):
        self.lang = "en" if self.lang == "ko" else "ko"
        self.lang_btn.setText("KO" if self.lang == "en" else "EN")
        from game_engine import update_player
        update_player(language=self.lang)
        self.refresh_all()

    def _show_career(self):
        from ui.career_window import CareerWindow
        self._career_win = CareerWindow(self.lang, self)
        self._career_win.show()

    def closeEvent(self, event):
        """메인 창 닫힐 때 열린 자식 창 모두 닫고 앱 종료."""
        from PyQt6.QtWidgets import QApplication
        QApplication.closeAllWindows()
        event.accept()


# ── 유틸 ──────────────────────────────────────────────────────

def _phase_label(week, lang):
    if   1  <= week <= 4:  return "비시즌"  if lang=="ko" else "Pre-Season"
    elif 5  <= week <= 11: return "상반기"  if lang=="ko" else "First Half"
    elif 12 <= week <= 25: return "비시즌"  if lang=="ko" else "Mid-Season"
    elif 26 <= week <= 32: return "하반기"  if lang=="ko" else "Second Half"
    else:                  return "비시즌"  if lang=="ko" else "Off-Season"