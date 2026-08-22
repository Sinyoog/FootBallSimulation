"""
ui/start_screen.py
"""
import random
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QComboBox, QFrame,
    QDialog, QMessageBox, QScrollArea, QGridLayout, QTextEdit,
    QProgressBar, QApplication
)
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QFont, QIntValidator

from database import reset_game_data, get_conn, KEY_STATS_BY_POS
from game_engine import create_player, get_player, _SUB_ROLE_MATCH_MOD
from constants import (POSITIONS, SUB_ROLES, PERSONALITIES, GAME_START_YEAR,
                       PLAYER_START_AGE, TALENT_TIER_KO, TALENT_TIER_ORDER, PHYSICAL_TRAITS,
                       TALENT_TIERS, PERSONALITY_EFFECTS, PHYSICAL_TRAIT_EFFECTS, STAT_KO,
                       PLAYER_START_YEAR_MIN, PLAYER_START_YEAR_MAX,
                       PLAYER_START_AGE_MIN, PLAYER_START_AGE_MAX)

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
        # [2026-08 수정, 신민용 요청: "진행률 창은 새 게임 버튼이 아니라
        # '생성'/'랜덤 생성'을 누른 후에 떠야 하고, 새 게임 누르는 시점엔
        # 바가 안 보여야 한다"] reset_game_data()(선수단 재생성, database.py
        # _regenerate_ai_players 참고 — 약 5초 소요)를 여기서 더 이상 부르지
        # 않는다. 예전엔 "새 게임"을 누르자마자(캐릭터 생성 창이 뜨기도
        # 전에) 이게 실행돼서 아무 피드백 없이 몇 초간 멈춘 것처럼 보였다.
        # 이제 NewPlayerDialog의 "생성"/"랜덤 생성" 버튼을 눌렀을 때
        # (_regenerate_world_with_progress) 진행률 창과 함께 실행된다.
        if not _game_confirm(self, "새 게임", "기존 저장 데이터가 삭제됩니다.\n계속하시겠습니까?"):
            return

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


class _WorldRegenProgressWindow(QDialog):
    """[2026-08 신설, 신민용 요청: "진행률 창은 새 게임 이후 '생성'과
    '랜덤 생성'을 누른 후에 뜨게 해야지, 새 게임 누르는 시점엔 바가 안
    뜨게 해야지"] NewPlayerDialog의 "✅ 생성"/"🎲 랜덤 생성" 버튼을 누른
    직후에만 뜨는 진행률 창. reset_game_data()가 실제로 전세계 선수단을
    재생성하는 약 5초 동안(database.py _regenerate_ai_players 참고)
    화면이 멈춘 것처럼 보이지 않도록 진행 상황만 보여준다.

    [2026-08 재수정, 신민용 요청: "game.db 삭제 후 최초 실행할 때 뜨는
    바처럼 보이게 해달라"] main.py의 _SeedProgressWindow(최초 설치 전용)와
    똑같은 크기(440×160)·스타일(_SEED_STYLE_MIRROR, 다크 배경+초록 진행바)
    ·레이아웃(제목/단계 라벨/진행바/디테일 라벨)을 그대로 맞췄다 — 색상
    수치가 어긋나면 눈에 바로 띄므로 main.py의 원본 값과 1:1로 동일하게
    유지한다.

    다만 취소 버튼만은 의도적으로 넣지 않았다 — 여기서는 이미 캐릭터
    생성 버튼까지 누른 뒤라, 중간에 취소하면 "선수단만 절반 재생성된"
    애매한 상태가 남을 수 있다(_SeedProgressWindow는 seed_initial_data
    자체가 맨 끝에만 commit()해서 취소 시 rollback 한 번으로 깨끗이
    되돌아가지만, 여기서는 reset_game_data()가 이미 팀 리셋 등 여러
    커밋 단위를 거친 뒤라 안전하게 되돌릴 방법이 없다) — 그래서 여기는
    끝까지 돌게 두고(닫기 버튼도 숨김), 시각적 스타일만 통일한다."""

    # main.py._SEED_STYLE과 동일 — 별도 모듈로 빼면 main.py -> ui.start_screen
    # 임포트 순서(main.py가 이미 start_screen을 임포트) 때문에 역참조가
    # 생겨 순환 임포트가 되므로, 작은 문자열이라 그대로 복제해서 쓴다.
    _SEED_STYLE_MIRROR = """
QWidget { background-color: #1a1a1a; color: #e0e0e0;
          font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; }
QLabel  { color: #e0e0e0; }
QProgressBar {
    background-color: #2a2a2a; border: 1px solid #444; border-radius: 6px;
    height: 18px; text-align: center; color: #e0e0e0; font-size: 11px;
}
QProgressBar::chunk { background-color: #2a8a2a; border-radius: 5px; }
"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("새 게임 준비 중...")
        self.setFixedSize(440, 160)
        self.setStyleSheet(self._SEED_STYLE_MIRROR)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowType.CustomizeWindowHint)
            & ~Qt.WindowType.WindowCloseButtonHint
            & ~Qt.WindowType.WindowContextHelpButtonHint)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        title = QLabel("⚽ 새로운 세계를 만들고 있습니다")
        title.setStyleSheet("font-size:15px; font-weight:bold;")
        lay.addWidget(title)

        self._stage_lbl = QLabel("전세계 선수단 생성 중...")
        self._stage_lbl.setStyleSheet("font-size:13px;")
        lay.addWidget(self._stage_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        lay.addWidget(self._bar)

        self._detail_lbl = QLabel("")
        self._detail_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(self._detail_lbl)

        lay.addStretch()

    def report(self, done, total, detail):
        self._stage_lbl.setText(f"전세계 선수단 생성 중... ({done}/{total})")
        self._bar.setValue(int(done / total * 100) if total else 0)
        self._detail_lbl.setText(detail or "")
        QApplication.processEvents()


class NewPlayerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("새 선수 생성")
        self.setMinimumWidth(720)
        self.setStyleSheet(DARK_STYLE)
        self._build()

    def _regenerate_world_with_progress(self):
        """[2026-08 신설] "생성"/"랜덤 생성" 버튼을 누른 시점에 실제로
        reset_game_data()(팀 전력·전세계 선수단 재생성)를 실행한다 — 예전엔
        StartScreen._new_game()/MainWindow.do_new_game()이 이 창을 띄우기도
        전에 미리 실행해서, 사용자 입장에선 "새 게임" 버튼을 누르자마자
        아무 피드백 없이 몇 초간 멈춘 것처럼 보였다. 진행률 창은 이 함수가
        도는 동안에만 뜬다."""
        win = _WorldRegenProgressWindow(self)
        win.show()
        QApplication.processEvents()
        reset_game_data(progress_cb=win.report)
        win.close()

    def _build(self):
        # [2026-08 신설, 신민용 요청: "재능/신체특징/포지션/세부역할 클릭하면
        # 우측에 메모장처럼 설명이 뜨게"] 다이얼로그를 좌(입력폼)/우(설명
        # 노트) 2열로 나눈다. 기존 코드는 전부 self에 직접 QVBoxLayout을
        # 붙였는데, 그걸 그대로 왼쪽 폼 위젯(form_w) 안으로 옮기고 바깥은
        # QHBoxLayout으로 감싼다 — 아래 내용(row 구성)은 하나도 안 바뀜.
        outer = QHBoxLayout(self)
        outer.setSpacing(14)

        form_w = QWidget()
        lay = QVBoxLayout(form_w)
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
        self.name_edit.setPlaceholderText("선수 이름 입력  (비워두면 국적에 맞는 이름 랜덤 생성)")
        name_row.addWidget(self.name_edit)
        lay.addLayout(name_row)

        # [2026-08 신설, 난이도 시스템] 이름 칸 바로 아래에 난이도 선택.
        # 생성 이후엔 절대 변경 불가능하므로(신민용 확정) 여기서만 고른다.
        # 쉬움(기본값)=지금과 동일 / 보통=재능등급·성격·신체특징 랜덤
        # 배정(선택창 자체를 숨김) / 어려움=보통 조건 + 게임 내내 내 선수
        # 포함 전원의 스탯·재능등급·성격·감독관계·포메이션 OVR 수치가
        # 전부 비공개로 진행.
        diff_row = QHBoxLayout()
        diff_row.addWidget(QLabel("난이도"))
        self.diff_easy_btn = QPushButton("쉬움")
        self.diff_normal_btn = QPushButton("보통")
        self.diff_hard_btn = QPushButton("어려움")
        # [2026-08 재수정, 신민용 리포트: "우측에 설명 안만들었는데 우측에
        # 설명칸 있다고 했잖아 — 쉬움/보통/어려움이 뭔지 설명을 저기다
        # 해달라 했잖아"] 처음엔 버튼 툴팁(마우스 올리면 뜨는 창)으로
        # 처리했는데, 이 다이얼로그는 이미 우측에 전용 "설명 노트 패널"
        # (self.note_panel, 아래 601번째 줄 부근)이 있고 재능등급/성격/
        # 신체특징 등 다른 필드들은 전부 그 패널로 설명을 띄운다 — 난이도만
        # 툴팁으로 따로 놀면 안 되고 같은 패널을 써야 한다. 그래서 버튼
        # 클릭 시(_on_difficulty_clicked) note_panel을 직접 갱신하도록
        # 바꿨다(아래 _note_for_difficulty 참고). 선택되지 않은 버튼은
        # objectName+:checked QSS로 회색 비활성 느낌, 선택된 버튼만
        # 눈에 띄게 강조.
        _DIFF_BTN_QSS = """
            QPushButton#diffBtn {
                background-color:#2a2a2a; color:#888888; border:1px solid #444444;
                padding:6px 16px; border-radius:4px;
            }
            QPushButton#diffBtn:hover { border:1px solid #777777; color:#bbbbbb; }
            QPushButton#diffBtn:checked {
                background-color:#2d5a2d; color:#ffffff; border:1px solid #4caf50;
                font-weight:bold;
            }
        """
        for btn in (self.diff_easy_btn, self.diff_normal_btn, self.diff_hard_btn):
            btn.setCheckable(True)
            btn.setObjectName("diffBtn")
            btn.setStyleSheet(_DIFF_BTN_QSS)
            btn.clicked.connect(self._on_difficulty_clicked)
            diff_row.addWidget(btn)
        self.diff_easy_btn.setChecked(True)
        self._difficulty = "easy"
        lay.addLayout(diff_row)

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

        # [2026-08 신설, 신민용 요청] 시작 연도/나이 — 이름칸처럼 비워두면
        # 기본값(GAME_START_YEAR/PLAYER_START_AGE)을 쓴다. 키보드로 직접
        # 입력하는 형태(스핀박스 화살표 대신 QLineEdit+숫자검증)로 만들고,
        # placeholder에 "기본값: N"을 띄워 비워도 뭐가 되는지 바로 보이게 함.
        year_row = QHBoxLayout()
        year_row.addWidget(QLabel("시작 연도"))
        self.year_edit = QLineEdit()
        self.year_edit.setPlaceholderText(f"기본값: {GAME_START_YEAR}년  (비워두면 기본값)")
        self.year_edit.setValidator(QIntValidator(PLAYER_START_YEAR_MIN, PLAYER_START_YEAR_MAX))
        year_row.addWidget(self.year_edit)
        lay.addLayout(year_row)

        age_row = QHBoxLayout()
        age_row.addWidget(QLabel("시작 나이"))
        self.age_edit = QLineEdit()
        self.age_edit.setPlaceholderText(f"기본값: {PLAYER_START_AGE}세  (비워두면 기본값)")
        self.age_edit.setValidator(QIntValidator(PLAYER_START_AGE_MIN, PLAYER_START_AGE_MAX))
        age_row.addWidget(self.age_edit)
        lay.addLayout(age_row)

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
        # [2026-08 신설, 난이도 시스템] 보통/어려움에서는 이 행 자체를
        # 숨긴다(신민용 확정: "재능 등급 성격 신체 특성은 선택할 수 없고
        # ... 나머지는 아예 선택창에 안뜨며") — QWidget으로 감싸서
        # setVisible() 하나로 통째로 껐다 켤 수 있게 한다.
        self.talent_row_w = QWidget()
        talent_row = QHBoxLayout(self.talent_row_w)
        talent_row.setContentsMargins(0, 0, 0, 0)
        talent_row.addWidget(QLabel("재능 등급"))
        self.talent_combo = QComboBox()
        self.talent_combo.addItem("🎲 랜덤", None)
        for _tier in TALENT_TIER_ORDER:
            self.talent_combo.addItem(TALENT_TIER_KO[_tier], _tier)
        talent_row.addWidget(self.talent_combo)
        lay.addWidget(self.talent_row_w)

        # [2026-07 신규] 성격 선택 — 재능 등급과 같은 패턴("🎲 랜덤"이 기본,
        # 맨 앞에 둬서 안 고르면 알아서 확률 추첨). personality=None을
        # 넘기면 game_engine.create_player가 알아서 처리한다.
        self.personality_row_w = QWidget()
        personality_row = QHBoxLayout(self.personality_row_w)
        personality_row.setContentsMargins(0, 0, 0, 0)
        personality_row.addWidget(QLabel("성격"))
        self.personality_combo = QComboBox()
        self.personality_combo.addItem("🎲 랜덤", None)
        for _p in PERSONALITIES:
            self.personality_combo.addItem(_p, _p)
        personality_row.addWidget(self.personality_combo)
        lay.addWidget(self.personality_row_w)

        # [2026-07 신규] 신체 특징 선택 — 위 성격과 동일한 패턴.
        self.trait_row_w = QWidget()
        trait_row = QHBoxLayout(self.trait_row_w)
        trait_row.setContentsMargins(0, 0, 0, 0)
        trait_row.addWidget(QLabel("신체 특징"))
        self.trait_combo = QComboBox()
        self.trait_combo.addItem("🎲 랜덤", None)
        for _t in PHYSICAL_TRAITS:
            self.trait_combo.addItem(_t, _t)
        trait_row.addWidget(self.trait_combo)
        lay.addWidget(self.trait_row_w)

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
        self.ok_btn = ok_btn
        # [2026-08 버그수정, 신민용 리포트: "시작 연도/나이가 범위를 벗어나도
        # 생성이 눌린다"] QIntValidator는 입력 도중(Intermediate) 상태를
        # 완전히 막아주지 못해서(자리수가 이미 다 찼는데도 범위 밖 값이
        # 통과하는 경우가 있었음), 확정적으로 텍스트가 바뀔 때마다 직접
        # 범위를 재검사해서 벗어나면 "생성" 버튼 자체를 비활성화한다.
        # "🎲 랜덤 생성" 버튼은 이 필드들을 아예 무시하고 만드므로 그대로
        # 항상 활성 상태로 둔다.
        self.year_edit.textChanged.connect(self._update_ok_enabled)
        self.age_edit.textChanged.connect(self._update_ok_enabled)
        self.ok_btn = ok_btn

        cancel_btn = QPushButton("취소")
        cancel_btn.setObjectName("danger")
        cancel_btn.clicked.connect(self.reject)

        btn_row.addWidget(rand_btn)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        outer.addWidget(form_w, 3)

        # ── 우측: 설명 노트 패널 (2026-08 신설) ────────────────────
        # 재능 등급/성격/신체 특징/주요 포지션/세부역할 콤보 중 하나를
        # 클릭(포커스)하면 그 필드의 실제 게임 효과를 여기 보여준다.
        # 다른 필드를 클릭하면 그 필드 설명으로 바뀐다(패널이 하나뿐이라
        # 이전 설명은 자연히 사라짐) — 값을 바꾸면(currentIndexChanged)
        # 지금 포커스된 패널이면 그 즉시 갱신된다.
        note_w = QWidget()
        note_lay = QVBoxLayout(note_w)
        note_lay.setContentsMargins(0, 0, 0, 0)
        note_title = QLabel("📋 설명")
        note_title.setStyleSheet("color:#888;font-size:12px;font-weight:bold;")
        note_lay.addWidget(note_title)
        self.note_panel = QTextEdit()
        self.note_panel.setReadOnly(True)
        self.note_panel.setStyleSheet(
            "QTextEdit { background-color:#111318; color:#ccc; border:1px solid #333; "
            "border-radius:6px; padding:10px; font-size:12px; }")
        self.note_panel.setHtml(self._note_default())
        note_lay.addWidget(self.note_panel, 1)
        outer.addWidget(note_w, 2)

        self._note_handlers = {}
        self._active_note_widget = None
        for combo, handler in (
            (self.year_edit, self._note_for_year),
            (self.age_edit, self._note_for_age),
            (self.pos_combo, self._note_for_position),
            (self.role_combo, self._note_for_role),
            (self.talent_combo, self._note_for_talent),
            (self.personality_combo, self._note_for_personality),
            (self.trait_combo, self._note_for_trait),
        ):
            combo.installEventFilter(self)
            self._note_handlers[combo] = handler
            if isinstance(combo, QLineEdit):
                combo.textChanged.connect(
                    lambda _t, cb=combo: self._refresh_note_if_active(cb))
            else:
                combo.currentIndexChanged.connect(
                    lambda _i, cb=combo: self._refresh_note_if_active(cb))

    # ── 난이도 선택 (2026-08 신설) ──────────────────────────────
    def _on_difficulty_clicked(self):
        sender = self.sender()
        for btn, key in ((self.diff_easy_btn, "easy"),
                          (self.diff_normal_btn, "normal"),
                          (self.diff_hard_btn, "hard")):
            btn.setChecked(btn is sender)
            if btn is sender:
                self._difficulty = key
        # 보통/어려움이면 재능·성격·신체특징 선택창을 아예 숨기고, 안에
        # 남아있을 수 있는 선택값도 "🎲 랜덤"으로 되돌려 create_player가
        # 항상 알아서 추첨하게 한다(숨겨진 콤보에 값이 남아있어도 UI에
        # 안 보이니 상관없지만, 명시적으로 리셋해두는 편이 안전하다).
        show_choice = (self._difficulty == "easy")
        for row_w, combo in ((self.talent_row_w, self.talent_combo),
                              (self.personality_row_w, self.personality_combo),
                              (self.trait_row_w, self.trait_combo)):
            row_w.setVisible(show_choice)
            if not show_choice:
                combo.setCurrentIndex(0)
        # [2026-08 신설] 다른 필드들과 동일하게 우측 설명 노트 패널에 표시.
        self._active_note_widget = None
        self.note_panel.setHtml(self._note_for_difficulty())

    # ── 설명 노트 콘텐츠 ─────────────────────────────────────────
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusIn and obj in getattr(self, "_note_handlers", {}):
            self._active_note_widget = obj
            self.note_panel.setHtml(self._note_handlers[obj]())
        return super().eventFilter(obj, event)

    def _refresh_note_if_active(self, combo):
        if self._active_note_widget is combo:
            self.note_panel.setHtml(self._note_handlers[combo]())

    def _note_default(self):
        return ("<span style='color:#666;'>난이도 / 시작 연도 / 시작 나이 / 재능 등급 / 성격 / "
                "신체 특징 / 주요 포지션 / <br>세부역할 중 하나를 클릭하면<br>"
                "여기에 실제 게임 효과가 표시됩니다.</span>")

    def _note_html(self, title, lines):
        body = "<br>".join(lines)
        return (f"<b style='color:#00cc44;font-size:13px;'>{title}</b><br><br>"
                f"<span style='line-height:150%;'>{body}</span>")

    # [2026-08 신설] 난이도 버튼 클릭 시 우측 설명 노트 패널에 표시할 내용.
    def _note_for_difficulty(self):
        content = {
            "easy": ("쉬움", [
                "지금과 동일하게 모든 정보가 공개됩니다.",
                "",
                "내 선수를 포함한 모든 선수의 재능 등급·현재 OVR·성격·",
                "감독 관계·상세 스탯을 언제든 확인할 수 있습니다.",
            ]),
            "normal": ("보통", [
                "재능 등급·성격·신체 특징을 직접 고를 수 없고",
                "무작위로 배정됩니다(위 선택창 자체가 숨겨집니다).",
                "",
                "정보 공개 범위는 쉬움과 동일합니다.",
            ]),
            "hard": ("어려움", [
                "보통 조건(재능 등급·성격·신체 특징 무작위 배정)에 더해,",
                "게임 내내 내 선수를 포함한 모든 선수의 재능 등급·현재 OVR·",
                "성격·감독 관계·상세 스탯이 표시되지 않습니다",
                "(현실적인 정보 제한).",
                "",
                "<b style='color:#ff6666;'>⚠ 생성 후에는 난이도를 바꿀 수 없습니다.</b>",
            ]),
        }
        title, lines = content.get(self._difficulty, content["easy"])
        return self._note_html(f"난이도 — {title}", lines)

    def _note_for_year(self):
        lines = [
            f"게임을 시작하는 연도를 정합니다. {PLAYER_START_YEAR_MIN}년~{PLAYER_START_YEAR_MAX}년",
            "사이에서 직접 골라 선택할 수 있습니다.",
            "",
            f"비워두면 기본값({GAME_START_YEAR}년)으로 시작합니다.",
            "",
            "월드컵/네이션스컵/대륙컵/클럽월드컵 등 대회 개최 연도는",
            "선택한 시작 연도를 기준으로 자동으로 다시 배정됩니다.",
        ]
        return self._note_html(f"시작 연도 — {PLAYER_START_YEAR_MIN}~{PLAYER_START_YEAR_MAX}년", lines)

    def _note_for_age(self):
        lines = [
            f"게임을 시작하는 내 선수의 나이를 정합니다. {PLAYER_START_AGE_MIN}세~{PLAYER_START_AGE_MAX}세",
            "사이에서 직접 골라 선택할 수 있습니다.",
            "",
            f"비워두면 기본값({PLAYER_START_AGE}세)으로 시작합니다.",
            "",
            "팀 입단은 선택한 나이 다음 해(선택한 나이+1세)부터 가능합니다.",
            "국가대표 발탁은 나이와 무관하게 17세부터 가능합니다.",
        ]
        return self._note_html(f"시작 나이 — {PLAYER_START_AGE_MIN}~{PLAYER_START_AGE_MAX}세", lines)

    def _note_for_talent(self):
        cur = self.talent_combo.currentData()
        lines = ["선수 스탯이 훈련으로 도달할 수 있는 '숨겨진 성장 상한'을 정합니다.",
                 "고강도 훈련을 꾸준히 하면 개별 스탯이 이 범위 안에서 자리잡습니다",
                 "(강점 스탯은 상한+12까지 추가 돌파 가능, 약점은 상한보다 낮게 형성돼",
                 "평균 OVR은 대체로 상한 근방에서 균형을 이룹니다).", ""]
        for t in TALENT_TIER_ORDER:
            cfg = TALENT_TIERS[t]
            mark = " ← 현재 선택" if cur == t else ""
            lines.append(f"· {TALENT_TIER_KO[t]}: {cfg['cap_min']}~{cfg['cap_max']}{mark}")
        if cur is None:
            lines.append("<br>🎲 랜덤 선택 시 9개 등급 중 균등 확률(각 1/9)로 추첨됩니다.")
        return self._note_html("재능 등급 — 숨겨진 스탯 성장 상한", lines)

    def _note_for_position(self):
        pos = self.pos_combo.currentData()
        if pos is None:
            return self._note_html("주요 포지션",
                ["뛸 위치를 정합니다 — OVR 계산에서 어떤 스탯을 우선하는지가 포지션마다 다릅니다.",
                 "🎲 랜덤 선택 시 11개 포지션 중 무작위로 배정됩니다."])
        keys = KEY_STATS_BY_POS.get(pos, [])
        stat_lines = [f"{i+1}. {STAT_KO.get(s, s)}" for i, s in enumerate(keys)]
        lines = [f"'{pos}' 포지션에서 OVR 계산에 가장 크게 반영되는 핵심 스탯 순서:", ""]
        lines += [f"· {s}" for s in stat_lines]
        lines.append("")
        lines.append("실제 경기에서도 이 스탯들의 비중이 높게 반영됩니다")
        lines.append("(예: 공격수는 슈팅/헤딩, 수비수는 태클/헤딩 위주).")
        return self._note_html(f"주요 포지션 — {pos}", lines)

    _ROLE_EFFECT_LABELS = {
        "g_mult": "득점 반영 배율", "a_mult": "도움 반영 배율",
        "gp_mult": "골 결정력(찬스 전환) 배율", "blk_mult": "수비 기여(차단) 배율",
        "sp_add": "세이브율 가산", "pa_add": "패스 정확도 가산",
        # [2026-08 신설] shot/keypass/dribble mult 추가에 맞춰 라벨도 등록 —
        # 안 넣어도 값 자체는 뜨지만(아래 루프가 mod.items() 전체를 도니까)
        # 라벨 없이 영문 키 이름이 그대로 노출된다.
        "shot_mult": "슈팅 시도 배율", "keypass_mult": "키패스 시도 배율",
        "dribble_mult": "드리블 시도 배율",
    }

    def _note_for_role(self):
        pos = self.pos_combo.currentData()
        role = self.role_combo.currentData()
        if pos is None:
            return self._note_html("세부역할",
                ["세부역할은 주요 포지션이 정해져야 목록이 정해집니다.",
                 "먼저 '주요 포지션'을 선택해주세요(또는 🎲 랜덤으로 두면 둘 다 자동 배정)."])
        if role is None:
            avail = SUB_ROLES.get(pos, [])
            return self._note_html(f"세부역할 — {pos}",
                [f"'{pos}'의 세부역할 후보: {', '.join(avail)}",
                 "", "🎲 랜덤 선택 시 이 중 하나가 무작위로 배정됩니다."])
        mod = _SUB_ROLE_MATCH_MOD.get((pos, role))
        lines = [f"'{pos} - {role}'가 실제 경기 결과(골/도움/세이브 등)에 주는 보정:", ""]
        if not mod:
            lines.append("· 이 조합은 별도 보정이 없습니다(기본값 그대로 반영).")
        else:
            for k, v in mod.items():
                label = self._ROLE_EFFECT_LABELS.get(k, k)
                if k.endswith("_mult"):
                    lines.append(f"· {label}: ×{v}")
                else:
                    sign = "+" if v >= 0 else ""
                    lines.append(f"· {label}: {sign}{v}")
        lines.append("")
        lines.append("배율 1.0 초과=강화, 미만=약화입니다. 예를 들어 득점 배율이 높고")
        lines.append("도움 배율이 낮으면 '해결사형', 반대면 '어시스트형' 역할입니다.")
        return self._note_html(f"세부역할 — {pos} · {role}", lines)

    _EFFECT_LABELS = {
        "train_eff": ("훈련 효율", "x"), "stress_mult": ("스트레스 축적 속도", "x"),
        "happy_gain_mult": ("행복도 상승폭", "x"), "big_match_rating": ("빅매치 평점 보정", "pt"),
        "losing_rating": ("열세 상황 평점 보정", "pt"), "team_win_bonus": ("팀 승리 기여 보너스", "pt"),
        "red_card_chance": ("퇴장 확률", "addpct"), "high_train_bonus": ("고강도 훈련 효과", "x"),
        "low_train_penalty": ("저강도 훈련 효과", "x"), "slump_chance_mult": ("슬럼프 확률", "x"),
        "cup_rating": ("컵대회 평점 보정", "pt"), "natural_growth_bonus": ("자연 성장 보너스", "addpct"),
        "mental_growth_mult": ("멘탈 스탯 성장 속도", "x"), "no_slump": ("슬럼프 면역", "flag"),
        "slump_threshold_reduce": ("슬럼프 발동 임계치 감소", "pt"),
        "slump_chance_add": ("슬럼프 확률", "addpct"), "injury_add": ("부상 확률", "addpct"),
        "stamina_train": ("체력 훈련 효과", "x"), "phys_growth_mult": ("신체 스탯 성장 속도", "x"),
        "phys_stat": ("집중 성장 스탯", "raw"), "phys_start_bonus": ("초기 신체 스탯 보너스", "addpt"),
    }

    def _fmt_effect(self, key, value):
        label, unit = self._EFFECT_LABELS.get(key, (key, "raw"))
        if unit == "x":
            return f"· {label}: ×{value}"
        if unit == "pt":
            sign = "+" if value >= 0 else ""
            return f"· {label}: {sign}{value}"
        if unit == "addpt":
            return f"· {label}: +{value}"
        if unit == "addpct":
            pct = value * 100 if abs(value) < 1 else value
            return f"· {label}: +{pct:.0f}%p"
        if unit == "flag":
            return f"· {label}" if value else ""
        if key == "phys_stat":
            return f"· {label}: {STAT_KO.get(value, value)}"
        return f"· {label}: {value}"

    def _note_for_personality(self):
        p = self.personality_combo.currentData()
        if p is None:
            return self._note_html("성격",
                ["훈련 효율·스트레스·평점 등에 영향을 주는 선수 성향입니다.",
                 "🎲 랜덤 선택 시 확률 추첨으로 배정됩니다."])
        effects = PERSONALITY_EFFECTS.get(p, {})
        lines = [f"'{p}' 성격의 실제 효과:", ""]
        if not effects:
            lines.append("· 특별한 효과 없음")
        else:
            lines += [self._fmt_effect(k, v) for k, v in effects.items()]
        return self._note_html(f"성격 — {p}", lines)

    def _note_for_trait(self):
        t = self.trait_combo.currentData()
        if t is None:
            return self._note_html("신체 특징",
                ["부상 확률·신체 스탯 성장 등에 영향을 주는 타고난 체질입니다.",
                 "🎲 랜덤 선택 시 확률 추첨으로 배정됩니다(무난함이 가장 흔함)."])
        effects = PHYSICAL_TRAIT_EFFECTS.get(t, {})
        lines = [f"'{t}' 신체 특징의 실제 효과:", ""]
        if not effects:
            lines.append("· 특별한 효과 없음(평범한 신체)")
        else:
            lines += [self._fmt_effect(k, v) for k, v in effects.items()]
        return self._note_html(f"신체 특징 — {t}", lines)

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
        self._regenerate_world_with_progress()
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
        # [2026-08 수정, 신민용 확정] "완전 랜덤 생성"은 난이도까지 포함해서
        # 전부 무작위여야 한다 — 화면에서 고른 난이도를 그대로 쓰지 않고
        # 매번 쉬움/보통/어려움 중 하나를 새로 뽑는다. ("✅ 생성" 버튼은
        # 화면에서 고른 난이도를 그대로 쓰는 쪽 — 그쪽만 "선택 안 하면
        # 쉬움 기본값" 규칙이 적용된다.)
        rand_difficulty = random.choice(["easy", "normal", "hard"])
        create_player(rname, rpos, rrole, cname, cflag,
                      talent_tier=None, personality=None, physical_trait=None,
                      difficulty=rand_difficulty)
        self.accept()

    def _update_ok_enabled(self):
        """시작 연도/나이가 빈 값(=기본값 사용)이거나 허용 범위 안이면
        활성, 범위를 벗어나면(잘못 입력했으면) 비활성화한다."""
        ok = True
        year_txt = self.year_edit.text().strip()
        if year_txt:
            try:
                y = int(year_txt)
                if not (PLAYER_START_YEAR_MIN <= y <= PLAYER_START_YEAR_MAX):
                    ok = False
            except ValueError:
                ok = False
        age_txt = self.age_edit.text().strip()
        if age_txt:
            try:
                a = int(age_txt)
                if not (PLAYER_START_AGE_MIN <= a <= PLAYER_START_AGE_MAX):
                    ok = False
            except ValueError:
                ok = False
        self.ok_btn.setEnabled(ok)
        self.ok_btn.setToolTip("" if ok else
            f"시작 연도는 {PLAYER_START_YEAR_MIN}~{PLAYER_START_YEAR_MAX}년, "
            f"시작 나이는 {PLAYER_START_AGE_MIN}~{PLAYER_START_AGE_MAX}세 범위여야 합니다")

    def _create(self):
        # 방어적 안전장치 — 버튼이 비활성화돼있어야 정상이지만, 혹시라도
        # 우회 경로(엔터키 등)로 호출되면 여기서 한 번 더 막는다.
        if not self.ok_btn.isEnabled():
            return
        self._regenerate_world_with_progress()
        name = self.name_edit.text().strip()
        # 국적 먼저 확정 — 이름 자동생성(국적에 맞는 이름 뽑기)에 필요하므로,
        # 국적 선택 안 했으면(랜덤) 여기서 미리 하나 뽑아 이후 create_player
        # 호출에도 그대로 재사용한다(생성된 이름과 실제 배정 국적이
        # 어긋나지 않도록 한 번만 뽑아 둘 다에 쓴다).
        if self._nat:
            nat_name, nat_flag = self._nat
        else:
            conn = get_conn(); c = conn.cursor()
            c.execute("""SELECT name, flag FROM countries
                         WHERE id IN (SELECT DISTINCT country_id FROM leagues)
                         ORDER BY RANDOM() LIMIT 1""")
            crow = c.fetchone()
            conn.close()
            nat_name, nat_flag = (crow["name"], crow["flag"]) if crow else (None, None)

        if not name:
            # [2026-07 신설, 신민용 요청: "이름 입력 안 하면 국적에 맞는
            # 랜덤 이름을 만들 수 있냐"] 예전엔 이름을 비워두면 그냥 에러로
            # 막았다 — 국적/포지션처럼 이름도 "안 정하면 랜덤"이 되도록,
            # _random_all()과 동일한 player_names 풀에서 위에서 정한
            # 국적에 맞는 이름을 하나 뽑아 자동으로 채운다.
            rname = None
            if nat_name:
                conn = get_conn(); c = conn.cursor()
                cid_row = c.execute("SELECT id FROM countries WHERE name=?", (nat_name,)).fetchone()
                if cid_row:
                    nrow = c.execute(
                        "SELECT name FROM player_names WHERE country_id=? ORDER BY RANDOM() LIMIT 1",
                        (cid_row["id"],)).fetchone()
                    if nrow:
                        rname = nrow["name"]
                conn.close()
            name = rname or (f"{nat_name}선수" if nat_name else "무명선수")
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
        # [2026-08 신설, 방어적 안전장치] 보통/어려움은 이 콤보들이 화면에서
        # 숨겨져 있어야 정상이지만(_on_difficulty_clicked), 혹시라도 숨김
        # 처리 전에 값이 남아있을 가능성에 대비해 여기서 한 번 더
        # 강제로 None 처리한다 — "보통/어려움에서는 절대 직접 선택 불가"
        # 원칙을 확실히 지키기 위함.
        if self._difficulty != "easy":
            tier = personality = trait = None
        # [2026-08 신설] 비워두면(빈 문자열) None → create_player가 기본값
        # (GAME_START_YEAR/PLAYER_START_AGE) 사용. 입력했으면 QIntValidator가
        # 이미 범위(1986~2020 / 14~28)를 강제해뒀으므로 그대로 정수 변환.
        year_txt = self.year_edit.text().strip()
        age_txt = self.age_edit.text().strip()
        start_year = int(year_txt) if year_txt else None
        start_age = int(age_txt) if age_txt else None
        create_player(name, pos, role, nat_name, nat_flag, talent_tier=tier,
                      personality=personality, physical_trait=trait,
                      start_year=start_year, start_age=start_age,
                      difficulty=self._difficulty)
        self.accept()