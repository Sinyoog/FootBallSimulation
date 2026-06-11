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



def _game_confirm(parent, title: str, message: str) -> bool:
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
    from PyQt6.QtCore import Qt
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedWidth(320)
    dlg.setStyleSheet("""
        QDialog { background:#1a1a2e; border:1px solid #333; }
        QLabel  { color:#e0e0e0; font-size:13px; padding:8px; }
        QPushButton { padding:8px 28px; border-radius:4px; font-size:13px; font-weight:bold; }
    """)
    lay = QVBoxLayout(dlg); lay.setSpacing(16); lay.setContentsMargins(20,20,20,16)
    lbl = QLabel(message); lbl.setWordWrap(True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(lbl)
    btn_row = QHBoxLayout(); btn_row.setSpacing(12)
    yes = QPushButton("✅ 확인"); no = QPushButton("❌ 취소")
    yes.setStyleSheet("background:#005522;color:white;")
    no.setStyleSheet("background:#440000;color:white;")
    btn_row.addWidget(yes); btn_row.addWidget(no)
    lay.addLayout(btn_row)
    result = [False]
    yes.clicked.connect(lambda: (result.__setitem__(0,True), dlg.accept()))
    no.clicked.connect(dlg.reject)
    dlg.exec()
    return result[0]


def _game_warning(parent, title: str, message: str):
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QLabel
    from PyQt6.QtCore import Qt
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedWidth(280)
    dlg.setStyleSheet("""
        QDialog { background:#1a1a2e; border:1px solid #555; }
        QLabel  { color:#ffcc44; font-size:13px; padding:8px; }
        QPushButton { padding:7px 32px; border-radius:4px; font-size:13px;
                      background:#333; color:white; font-weight:bold; }
    """)
    lay = QVBoxLayout(dlg); lay.setSpacing(12); lay.setContentsMargins(20,20,20,16)
    lbl = QLabel(f"⚠  {message}"); lbl.setWordWrap(True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(lbl)
    ok = QPushButton("확인")
    ok.clicked.connect(dlg.accept)
    lay.addWidget(ok, alignment=Qt.AlignmentFlag.AlignCenter)
    dlg.exec()


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

    def go_to_start(self):
        """데이터 초기화 후 현재 창을 시작 화면 UI로 완전 교체."""
        from database import reset_game_data
        reset_game_data()
        self._show_start_ui()

    def _show_start_ui(self):
        """현재 MainWindow 안에 StartScreen UI를 직접 그림."""
        from PyQt6.QtWidgets import (
            QWidget, QVBoxLayout, QLabel, QPushButton, QDialog,
            QMessageBox
        )
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import Qt
        from database import reset_game_data, get_conn
        from game_engine import get_player

        DARK_STYLE = """
        QWidget { background-color: #1a1a1a; color: #e0e0e0;
                  font-family: 'Malgun Gothic', sans-serif; }
        QPushButton {
            background-color: #2a6a2a; color: white;
            border: none; border-radius: 6px; padding: 10px 20px;
            font-size: 14px; font-weight: bold; }
        QPushButton:hover  { background-color: #3a8a3a; }
        QPushButton:disabled { background-color: #333333; color: #666666; }
        QPushButton#danger { background-color: #6a1a1a; }
        QPushButton#danger:hover { background-color: #8a2a2a; }
        QPushButton#gray   { background-color: #3a3a3a; }
        QPushButton#gray:hover { background-color: #4a4a4a; }
        """

        root = QWidget()
        root.setStyleSheet(DARK_STYLE)
        lay = QVBoxLayout(root)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(16)

        ico = QLabel("⚽")
        ico.setFont(QFont("Arial", 40))
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ico)

        title = QLabel("축구 선수 커리어 시뮬레이션")
        title.setFont(QFont("Malgun Gothic", 22, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #00cc44;")
        lay.addWidget(title)

        sub = QLabel("1990년, 16살의 당신. 전설이 되어보세요.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #888888; font-size: 13px;")
        lay.addWidget(sub)
        lay.addSpacing(20)

        new_btn = QPushButton("새 게임")
        new_btn.setFixedWidth(200)
        lay.addWidget(new_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        cont_btn = QPushButton("이어하기")
        cont_btn.setObjectName("gray")
        cont_btn.setFixedWidth(200)
        p = get_player()
        cont_btn.setEnabled(p is not None)
        lay.addWidget(cont_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        quit_btn = QPushButton("종료")
        quit_btn.setObjectName("danger")
        quit_btn.setFixedWidth(200)
        quit_btn.clicked.connect(self.close)
        lay.addWidget(quit_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setCentralWidget(root)
        if hasattr(self, 'top_bar'):
            self.top_bar.hide()
        self.setMinimumSize(500, 400)
        self.resize(600, 450)

        def do_new_game():
            if not _game_confirm(self, "새 게임", "기존 저장 데이터가 삭제됩니다.\n계속하시겠습니까?"):
                return
            reset_game_data()
            from ui.start_screen import NewPlayerDialog
            dlg = NewPlayerDialog(self)
            if dlg.exec():
                self._rebuild_main()

        def do_continue():
            self._rebuild_main()

        new_btn.clicked.connect(do_new_game)
        cont_btn.clicked.connect(do_continue)

    def _rebuild_main(self):
        """게임 창 UI를 다시 빌드해서 게임 화면으로 전환."""
        self.lang = "ko"
        self.setMinimumSize(1280, 720)
        self.resize(1280, 720)
        self._build()
        self.refresh_all()

    def closeEvent(self, event):
        event.accept()


# ── 유틸 ──────────────────────────────────────────────────────

def _phase_label(week, lang):
    if   1  <= week <= 4:  return "비시즌"  if lang=="ko" else "Pre-Season"
    elif 5  <= week <= 11: return "상반기"  if lang=="ko" else "First Half"
    elif 12 <= week <= 25: return "비시즌"  if lang=="ko" else "Mid-Season"
    elif 26 <= week <= 32: return "하반기"  if lang=="ko" else "Second Half"
    else:                  return "비시즌"  if lang=="ko" else "Off-Season"