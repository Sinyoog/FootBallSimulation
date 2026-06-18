"""
ui/start_screen.py
"""
import random
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QFrame,
    QDialog, QMessageBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from database import reset_game_data, get_conn
from game_engine import create_player, get_player
from constants import POSITIONS, SUB_ROLES, PERSONALITIES

DARK_STYLE = """
QWidget { background-color: #1a1a1a; color: #e0e0e0;
          font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; }
QLabel  { color: #e0e0e0; }
QPushButton {
    background-color: #2a6a2a; color: white;
    border: none; border-radius: 6px; padding: 10px 20px;
    font-size: 14px; font-weight: bold;
}
QPushButton:hover  { background-color: #3a8a3a; }
QPushButton:disabled { background-color: #333333; color: #666666; }
QPushButton#danger {
    background-color: #6a1a1a;
}
QPushButton#danger:hover { background-color: #8a2a2a; }
QPushButton#gray {
    background-color: #3a3a3a;
}
QPushButton#gray:hover { background-color: #4a4a4a; }
QLineEdit {
    background-color: #2a2a2a; color: #e0e0e0;
    border: 1px solid #444; border-radius: 4px; padding: 6px;
    font-size: 13px;
}
QComboBox {
    background-color: #2a2a2a; color: #e0e0e0;
    border: 1px solid #444; border-radius: 4px; padding: 6px;
    font-size: 13px;
}
QComboBox QAbstractItemView {
    background-color: #2a2a2a; color: #e0e0e0;
    selection-background-color: #3a6a3a;
}
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


class StartScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("축구 선수 커리어 시뮬레이션")
        self.setMinimumSize(500, 400)
        self.setStyleSheet(DARK_STYLE)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(16)

        # 타이틀
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

        # 새 게임
        new_btn = QPushButton("새 게임")
        new_btn.setFixedWidth(200)
        new_btn.clicked.connect(self._new_game)
        lay.addWidget(new_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 이어하기
        self.cont_btn = QPushButton("이어하기")
        self.cont_btn.setObjectName("gray")
        self.cont_btn.setFixedWidth(200)
        self.cont_btn.clicked.connect(self._continue)
        lay.addWidget(self.cont_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 종료
        quit_btn = QPushButton("종료")
        quit_btn.setObjectName("danger")
        quit_btn.setFixedWidth(200)
        quit_btn.clicked.connect(self.close)
        lay.addWidget(quit_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 이어하기 버튼 활성 여부
        p = get_player()
        self.cont_btn.setEnabled(p is not None)

    def _new_game(self):
        if not _game_confirm(self, "새 게임", "기존 저장 데이터가 삭제됩니다.\n계속하시겠습니까?"):
            return

        reset_game_data()
        dlg = NewPlayerDialog(self)
        if dlg.exec():
            self._open_main()

    def _continue(self):
        self._open_main()

    def _open_main(self):
        from ui.main_window import MainWindow
        self.main_win = MainWindow()
        self.main_win.show()
        self.close()


class NewPlayerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("새 선수 생성")
        self.setMinimumWidth(400)
        self.setStyleSheet(DARK_STYLE)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        # 헤더
        h = QHBoxLayout()
        h.addWidget(QLabel("⚽", self))
        t = QLabel("새 선수 생성")
        t.setFont(QFont("Malgun Gothic", 16, QFont.Weight.Bold))
        t.setStyleSheet("color: #00cc44;")
        h.addWidget(t)
        h.addStretch()
        lay.addLayout(h)

        # 이름
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("이름"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("선수 이름 입력")
        name_row.addWidget(self.name_edit)
        lay.addLayout(name_row)

        # 포지션
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("포지션"))
        self.pos_combo = QComboBox()
        self.pos_combo.addItems(POSITIONS)
        self.pos_combo.currentTextChanged.connect(self._update_roles)
        pos_row.addWidget(self.pos_combo)
        lay.addLayout(pos_row)

        # 세부역할
        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("세부역할"))
        self.role_combo = QComboBox()
        role_row.addWidget(self.role_combo)
        lay.addLayout(role_row)
        self._update_roles(POSITIONS[0])

        note = QLabel("※ 국적 · 신체 · 성격 · 특징 · 스탯은 자동 랜덤")
        note.setStyleSheet("color: #666666; font-size: 11px;")
        lay.addWidget(note)

        # 버튼
        btn_row = QHBoxLayout()
        rand_btn = QPushButton("🎲 랜덤 생성")
        rand_btn.setObjectName("gray")
        rand_btn.clicked.connect(self._random_all)

        ok_btn = QPushButton("✅ 생성")
        ok_btn.clicked.connect(self._create)

        cancel_btn = QPushButton("취소")
        cancel_btn.setObjectName("danger")
        cancel_btn.clicked.connect(self.reject)

        btn_row.addWidget(rand_btn)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _update_roles(self, pos):
        self.role_combo.clear()
        self.role_combo.addItems(SUB_ROLES.get(pos, ["기본"]))

    def _random_all(self):
        """랜덤 생성 → 바로 인게임 진입"""
        conn = get_conn()
        c = conn.cursor()
        c.execute("""SELECT id, name, flag FROM countries
                     WHERE id IN (SELECT DISTINCT country_id FROM leagues)
                     ORDER BY RANDOM() LIMIT 1""")  # 이름만 국가 제외
        crow = c.fetchone()
        cid, cname, cflag = crow["id"], crow["name"], crow["flag"]
        c.execute("SELECT name FROM player_names WHERE country_id=? ORDER BY RANDOM() LIMIT 1",
                  (cid,))
        nrow = c.fetchone()
        conn.close()

        rname = nrow["name"] if nrow else cname + "선수"
        rpos  = random.choice(POSITIONS)
        rrole = random.choice(SUB_ROLES.get(rpos, ["기본"]))

        create_player(rname, rpos, rrole, cname, cflag)
        self.accept()

    def _create(self):
        name = self.name_edit.text().strip()
        if not name:
            _game_warning(self, "입력 오류", "이름을 입력해주세요.")
            return
        pos  = self.pos_combo.currentText()
        role = self.role_combo.currentText()
        create_player(name, pos, role)
        self.accept()