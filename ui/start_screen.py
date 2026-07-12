"""
ui/start_screen.py
"""
import random
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QFrame,
    QDialog, QMessageBox, QScrollArea, QGridLayout
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from database import reset_game_data, get_conn
from game_engine import create_player, get_player
from constants import (POSITIONS, SUB_ROLES, PERSONALITIES, GAME_START_YEAR,
                       PLAYER_START_AGE, TALENT_TIER_KO, TALENT_TIER_ORDER, PHYSICAL_TRAITS)

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


class CountryPickerDialog(QDialog):
    """검색 + 다열 그리드 형태의 국적 선택 다이얼로그.

    국가가 매우 많으므로(180+) 일반 콤보박스 대신 검색창 + 4열 버튼 그리드로
    제공한다. 선택 결과는 self.selected = (name, flag) 또는 None(랜덤)으로 보관.
    리그가 있는 '실제' 국가만 노출한다(이름만 있는 국가 제외).
    """
    COLS = 4
    # 필터에 노출할 대륙 순서 (리그 보유 국가 기준 6개)
    CONTINENTS = ["유럽", "남미", "아프리카", "아시아", "북미", "오세아니아"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("국가 선택")
        # 4열 국가 버튼(고정폭)이 가로로 안 잘리도록 창 너비를 넉넉히.
        self.setMinimumSize(740, 540)
        self.setStyleSheet(DARK_STYLE)
        self.selected = None          # (name, flag) | None(=랜덤)
        self._active_continents = set()   # 활성화된 대륙들(복수 선택). 비어있으면 '전체'
        self._cont_buttons = {}           # 대륙명 -> QPushButton (형광 토글용)
        self._all = self._load_countries()
        self._build()

    def _load_countries(self):
        """리그가 있는 국가만 (grade 높은 순 → 이름 순). 대륙 정보 포함."""
        conn = get_conn()
        rows = conn.execute(
            """SELECT name, flag, grade, continent FROM countries
               WHERE id IN (SELECT DISTINCT country_id FROM leagues)
               ORDER BY
                 CASE grade WHEN 'S' THEN 0 WHEN 'A' THEN 1 WHEN 'B' THEN 2
                            WHEN 'C' THEN 3 WHEN 'D' THEN 4 WHEN 'E' THEN 5
                            ELSE 6 END,
                 name""").fetchall()
        conn.close()
        return [(r["name"], r["flag"], r["continent"] or "") for r in rows]

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        # 헤더
        t = QLabel("🌍  국가 선택")
        t.setFont(QFont("Malgun Gothic", 15, QFont.Weight.Bold))
        t.setStyleSheet("color: #00cc44;")
        lay.addWidget(t)

        # 검색창
        self.search = QLineEdit()
        self.search.setPlaceholderText("국가명 검색…  (예: 대한, 브라, 잉글)")
        self.search.textChanged.connect(self._refilter)
        # 엔터: 현재 검색 결과가 정확히 1개면 그 국가를 자동 선택.
        self.search.returnPressed.connect(self._on_search_enter)
        lay.addWidget(self.search)

        # ── 상단 필터 버튼 (4열 × 2줄 = 8개: 랜덤 · 전체 · 대륙6) ──
        filt_host = QWidget()
        filt = QGridLayout(filt_host)
        filt.setSpacing(6)
        filt.setContentsMargins(0, 0, 0, 0)

        # (0,0) 랜덤 — 즉시 랜덤 자동 선택
        rand = QPushButton("🎲 랜덤")
        rand.setObjectName("gray")
        rand.clicked.connect(self._pick_random)
        filt.addWidget(rand, 0, 0)

        # (0,1) 전체 — 모든 대륙 해제 + 전체 표시. 기본 활성(형광).
        self.btn_all = QPushButton("🌐 전체")
        self.btn_all.setObjectName("gray")
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)
        self.btn_all.clicked.connect(self._select_all)
        filt.addWidget(self.btn_all, 0, 1)

        # 나머지 6칸: 대륙 버튼 (토글, 복수 선택 가능)
        #   배치 순서: (0,2)(0,3)(1,0)(1,1)(1,2)(1,3)
        slots = [(0, 2), (0, 3), (1, 0), (1, 1), (1, 2), (1, 3)]
        for cont, (r, c) in zip(self.CONTINENTS, slots):
            b = QPushButton(cont)
            b.setObjectName("gray")
            b.setCheckable(True)
            b.clicked.connect(lambda _checked, name=cont: self._toggle_continent(name))
            self._cont_buttons[cont] = b
            filt.addWidget(b, r, c)

        lay.addWidget(filt_host)
        self._sync_filter_styles()

        # 스크롤 가능한 그리드 영역
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { border: 1px solid #444; border-radius: 4px; }")
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(6)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.scroll.setWidget(self.grid_host)
        lay.addWidget(self.scroll, 1)

        # 하단 취소
        cancel = QPushButton("취소")
        cancel.setObjectName("danger")
        cancel.setFixedWidth(120)
        cancel.clicked.connect(self.reject)
        lay.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignRight)

        self._refilter("")

    def _refilter(self, text):
        # 기존 버튼 제거
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        q = text.strip()
        conts = self._active_continents   # 비어있으면 전체
        items = []
        for (n, f, cont) in self._all:
            if q and q not in n:
                continue
            if conts and cont not in conts:
                continue
            items.append((n, f))
        # 현재 화면에 표시 중인 결과 보관 (엔터 자동선택용)
        self._filtered = items

        if not items:
            empty = QLabel("검색 결과가 없습니다.")
            empty.setStyleSheet("color: #888; padding: 20px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(empty, 0, 0, 1, self.COLS)
            return

        for idx, (name, flag) in enumerate(items):
            r, c = divmod(idx, self.COLS)
            btn = QPushButton(f"{flag} {name}")
            btn.setObjectName("gray")
            # 고정폭: 가장 긴 국가명(11자)+플래그가 안 잘리도록. 가로 스크롤 방지.
            btn.setMinimumWidth(160)
            btn.setStyleSheet(
                "QPushButton { text-align: left; padding: 8px 10px; font-size: 12px; }"
                "QPushButton:hover { background-color: #3a8a3a; }")
            btn.clicked.connect(lambda _, n=name, f=flag: self._pick(n, f))
            self.grid.addWidget(btn, r, c)

    def _on_search_enter(self):
        # 검색 결과가 정확히 1개일 때만 엔터로 자동 선택한다.
        #   여러 개거나 0개면 아무 동작도 하지 않음(오선택 방지).
        items = getattr(self, "_filtered", [])
        if len(items) == 1:
            name, flag = items[0]
            self._pick(name, flag)

    def _toggle_continent(self, name):
        # 대륙 버튼 토글(복수 선택). 하나라도 켜지면 '전체'는 해제된다.
        if name in self._active_continents:
            self._active_continents.discard(name)
        else:
            self._active_continents.add(name)
        self._sync_filter_styles()
        self._refilter(self.search.text())

    def _select_all(self):
        # '전체': 모든 대륙 선택 해제 → 전체 국가 표시. 전체만 형광.
        self._active_continents.clear()
        self._sync_filter_styles()
        self._refilter(self.search.text())

    def _sync_filter_styles(self):
        # 활성 대륙은 형광, 비활성은 회색. 대륙이 하나도 없으면 '전체' 형광.
        on = ("QPushButton { text-align:center; padding:8px 10px; font-size:12px;"
              " background-color:#00cc44; color:#10210f; font-weight:bold;"
              " border:1px solid #00ff55; border-radius:4px; }")
        off = ("QPushButton { text-align:center; padding:8px 10px; font-size:12px; }"
               "QPushButton:hover { background-color:#3a8a3a; }")
        none_active = not self._active_continents
        self.btn_all.setChecked(none_active)
        self.btn_all.setStyleSheet(on if none_active else off)
        for cont, btn in self._cont_buttons.items():
            active = cont in self._active_continents
            btn.setChecked(active)
            btn.setStyleSheet(on if active else off)

    def _pick(self, name, flag):
        self.selected = (name, flag)
        self.accept()

    def _pick_random(self):
        self.selected = None
        self.accept()


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

        sub = QLabel(f"{GAME_START_YEAR}년, {PLAYER_START_AGE}살의 당신. 전설이 되어보세요.")
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
        # 캐릭터 생성 후 뜨는 커리어 시뮬레이션 창은 기본 전체화면(최대화)으로.
        self.main_win.showMaximized()
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

        # 국적 (포지션 위) — 국가가 많아 검색+그리드 다이얼로그로 선택
        self._nat = None    # (name, flag) | None(=랜덤)
        nat_row = QHBoxLayout()
        nat_row.addWidget(QLabel("국적"))
        self.nat_btn = QPushButton("🌍 국가 선택  (미선택 시 랜덤)")
        self.nat_btn.setObjectName("gray")
        self.nat_btn.setStyleSheet("QPushButton { text-align: left; padding: 6px 10px; }")
        self.nat_btn.clicked.connect(self._pick_country)
        nat_row.addWidget(self.nat_btn)
        lay.addLayout(nat_row)

        # 포지션 — 국적 선택과 같은 패턴으로 "🎲 랜덤"을 기본값으로 둔다.
        # 이름만 짓고 나머진 전부 랜덤에 맡기고 싶을 때, 이 콤보들을 그냥
        # 안 건드리기만 하면 되게 하기 위함.
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("주요 포지션"))
        self.pos_combo = QComboBox()
        self.pos_combo.addItem("🎲 랜덤", None)
        for _pos in POSITIONS:
            self.pos_combo.addItem(_pos, _pos)
        self.pos_combo.currentIndexChanged.connect(
            lambda _i: self._update_roles(self.pos_combo.currentData()))
        pos_row.addWidget(self.pos_combo)
        lay.addLayout(pos_row)

        # 세부역할 — 포지션이 랜덤이면 세부역할도 "🎲 랜덤" 하나만 남는다
        # (포지션이 정해져야 세부역할 목록 자체가 정해지므로).
        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("세부역할"))
        self.role_combo = QComboBox()
        role_row.addWidget(self.role_combo)
        lay.addLayout(role_row)
        self._update_roles(None)

        # [신규] 재능 등급 선택 — 기본값은 월드클래스(가장 앞)지만, 원하면
        # 다른 콤보들처럼 "🎲 랜덤"(맨 뒤)을 골라 확률 추첨에 맡길 수도
        # 있다. talent_tier=None을 넘기면 game_engine.create_player가
        # 알아서 확률 추첨으로 처리한다.
        talent_row = QHBoxLayout()
        talent_row.addWidget(QLabel("재능 등급"))
        self.talent_combo = QComboBox()
        self.talent_combo.addItem("🎲 랜덤", None)
        for _tier in TALENT_TIER_ORDER:
            self.talent_combo.addItem(TALENT_TIER_KO[_tier], _tier)
        talent_row.addWidget(self.talent_combo)
        lay.addLayout(talent_row)

        # [2026-07 신규] 성격 선택 — 재능 등급과 같은 패턴("🎲 랜덤"이 기본,
        # 맨 앞에 둬서 안 고르면 알아서 확률 추첨). personality=None을
        # 넘기면 game_engine.create_player가 알아서 처리한다.
        personality_row = QHBoxLayout()
        personality_row.addWidget(QLabel("성격"))
        self.personality_combo = QComboBox()
        self.personality_combo.addItem("🎲 랜덤", None)
        for _p in PERSONALITIES:
            self.personality_combo.addItem(_p, _p)
        personality_row.addWidget(self.personality_combo)
        lay.addLayout(personality_row)

        # [2026-07 신규] 신체 특징 선택 — 위 성격과 동일한 패턴.
        trait_row = QHBoxLayout()
        trait_row.addWidget(QLabel("신체 특징"))
        self.trait_combo = QComboBox()
        self.trait_combo.addItem("🎲 랜덤", None)
        for _t in PHYSICAL_TRAITS:
            self.trait_combo.addItem(_t, _t)
        trait_row.addWidget(self.trait_combo)
        lay.addLayout(trait_row)

        note = QLabel("※ 신체(체형·키·몸무게) · 스탯은 자동 랜덤")
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
        """pos가 None(랜덤 포지션)이면 세부역할도 "🎲 랜덤" 하나만 두고
        고정한다 — 포지션이 정해지기 전엔 세부역할 목록 자체를 알 수
        없으므로."""
        self.role_combo.clear()
        if pos is None:
            self.role_combo.addItem("🎲 랜덤", None)
            self.role_combo.setEnabled(False)
            return
        self.role_combo.setEnabled(True)
        self.role_combo.addItem("🎲 랜덤", None)
        for _role in SUB_ROLES.get(pos, ["기본"]):
            self.role_combo.addItem(_role, _role)

    def _pick_country(self):
        dlg = CountryPickerDialog(self)
        if dlg.exec():
            self._nat = dlg.selected          # (name, flag) | None(=랜덤)
            if self._nat:
                name, flag = self._nat
                self.nat_btn.setText(f"{flag} {name}")
            else:
                self.nat_btn.setText("🎲 랜덤 (자동 선택)")

    def _random_all(self):
        """랜덤 생성 → 바로 인게임 진입.

        [2026-07 버그 수정] 이 버튼은 '내가 뭘 골랐든 상관없이 완전
        랜덤'이어야 하는데, 예전엔 국적/포지션/세부역할만 무작위로 뽑고
        재능 등급·성격·신체 특징은 콤보에서 골라둔 값을 그대로 반영해서
        일관성이 없었다(신민용 지적). 이제 이름을 포함한 모든 항목을
        폼의 현재 선택과 무관하게 매번 새로 굴린다 — 특정 항목만 미리
        고정하고 싶으면 '✅ 생성' 버튼을 쓰면 된다(그쪽은 선택한 값은
        그대로, 안 고른 값만 랜덤으로 채운다)."""
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

        # talent_tier/personality/physical_trait를 전부 None으로 넘겨서
        # (콤보 선택과 무관하게) create_player가 알아서 확률 추첨하게 한다.
        create_player(rname, rpos, rrole, cname, cflag,
                      talent_tier=None, personality=None, physical_trait=None)
        self.accept()

    def _create(self):
        name = self.name_edit.text().strip()
        if not name:
            _game_warning(self, "입력 오류", "이름을 입력해주세요.")
            return
        # [신규] 포지션/세부역할이 "🎲 랜덤"(콤보 데이터 None)이면 여기서
        # 실제 값을 뽑는다 — 국적 선택과 같은 패턴: 안 고르면 랜덤.
        pos = self.pos_combo.currentData()
        if pos is None:
            pos = random.choice(POSITIONS)
        role = self.role_combo.currentData()
        if role is None:
            role = random.choice(SUB_ROLES.get(pos, ["기본"]))
        tier = self.talent_combo.currentData()  # None이면 create_player가 알아서 확률 추첨
        personality = self.personality_combo.currentData()
        trait = self.trait_combo.currentData()
        if self._nat:
            nat_name, nat_flag = self._nat
            create_player(name, pos, role, nat_name, nat_flag, talent_tier=tier,
                          personality=personality, physical_trait=trait)
        else:
            create_player(name, pos, role, talent_tier=tier,
                          personality=personality, physical_trait=trait)
        self.accept()