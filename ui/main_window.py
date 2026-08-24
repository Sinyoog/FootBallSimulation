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
    # [2026-08 버그수정, 신민용 리포트: "국가 선택이든 뭐든, 확인 버튼
    # 눌러서 창 닫히면 흰 창이 우다다닥 떴다가 한번에 사라진다"] 지난
    # 시도들(다이얼로그 순서 조정, WaitCursor 등)은 전부 헛다리였다 —
    # 진짜 원인은 이 앱의 QDialog(self)/QDialog(parent) 생성부 전체가
    # (center_panel.py 7곳, formation_widget.py의 PlayerStatPopup —
    # 선수 클릭할 때마다 뜨는 가장 빈번한 팝업, 이 파일과
    # retire_window.py의 공용 헬퍼까지) dlg.exec() 이후 한 번도
    # deleteLater()를 안 불렀다는 것 — Qt 객체 소멸을 파이썬 GC에게
    # 통째로 맡긴 셈이다. 참조 순환(시그널 연결이 dlg↔버튼↔람다를 서로
    # 물고 있음) 때문에 단순 참조카운트로는 안 지워지고 GC 사이클이 돌
    # 때까지 미뤄지는데, 그 사이클이 한 번에 여러 개를 몰아서 정리하면
    # 그때마다 각 다이얼로그의 네이티브 창이 파괴되면서 잠깐씩
    # 깜빡이다 한꺼번에 사라지는 것으로 보인다 — "여러 개가 우다다닥
    # 나왔다 한번에 사라진다"는 목격담과 정확히 일치. dlg.exec()
    # 직후 deleteLater()를 명시로 호출해 즉시(다음 이벤트 루프 틱)
    # 파괴되도록 고친다.
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
    dlg.deleteLater()
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
    dlg.deleteLater()


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

        # 좌측 선수 패널: 박스 레이아웃 + 복수국적/긴 팀명 등 긴 텍스트를 수용하도록
        #   최소/최대폭을 넉넉히. (글자가 길면 이 범위 안에서 스플리터로 넓힐 수 있음)
        splitter.addWidget(scroll(self.player_panel, 250, 380))
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
        # [2026-08 버그수정, 신민용 리포트: "오퍼든 입단이든 국가대표든
        # 뭐든 창이 뜨면 흰 작은 창이 깜빡인다"] 국가대표 발탁 다이얼로그는
        # "닫히기 전에 다음 모달을 또 여는" 중첩 타이밍이 원인이라 그
        # 지점만 따로 고쳤지만(QTimer.singleShot), 오퍼/입단 등 다른 흐름은
        # 그 패턴이 아니라 단순히 "다이얼로그가 막 닫힌 직후 refresh_all()
        # 이 무거운 작업(일정 창 13탭 재구성 등)을 곧바로 동기 실행"하는
        # 구조였다 — 즉 다이얼로그가 실제로 화면에서 사라지는 페인트
        # 이벤트가 처리되기도 전에 다음 무거운 작업이 이어져서, 그 동안
        # OS가 "응답 없음"으로 보고 유령 창을 잠깐 띄우는 것으로 보인다.
        # refresh_all()은 다이얼로그가 닫힌 뒤 거의 모든 흐름(오퍼 수락/
        # 입단/국가대표 확정 등)이 공통으로 부르는 지점이라, 여기 맨 앞에서
        # 한 번 이벤트 루프에 제어권을 돌려주면(processEvents) 밀린 페인트/
        # 창닫힘 이벤트부터 먼저 처리되고 나서 무거운 갱신이 시작된다 —
        # 개별 다이얼로그 호출부를 하나하나 찾아 고치는 대신 한 곳에서
        # 공통으로 막는다. advance_days의 매일/매주 반복 호출(hot path)은
        # 그대로 두되(성능 이슈로 이미 세심하게 조율된 구간), 여기 진입
        # 시점의 processEvents() 자체는 대기 이벤트가 없으면 사실상 즉시
        # 반환되어 그 경로 성능에 미치는 영향은 미미하다.
        from PyQt6.QtWidgets import QApplication
        import time as _time_dbg
        _t0 = _time_dbg.perf_counter()
        print(f"[GHOST-DEBUG] refresh_all() 진입 {_t0:.3f}")
        QApplication.processEvents()
        print(f"[GHOST-DEBUG] processEvents() 완료, 소요 {(_time_dbg.perf_counter()-_t0)*1000:.1f}ms")
        _t1 = _time_dbg.perf_counter()
        self.refresh_light()
        print(f"[GHOST-DEBUG] refresh_light() 완료, 소요 {(_time_dbg.perf_counter()-_t1)*1000:.1f}ms")
        # 진행(NEXT DAY) 직후 열려 있는 보조 창들을 함께 갱신한다.
        #   - 순위표 창은 이미 refresh_light()에서 매일 갱신되므로 여기선
        #     일정 창만 추가로 갱신한다(묶음/1주 끝에만 — 13개 탭 동기
        #     렌더링 비용이 있어 매일 부르면 안 됨).
        #   - 창이 닫혔거나 파괴됐으면 조용히 건너뛴다(비용 0).
        _t2 = _time_dbg.perf_counter()
        self._refresh_aux_window("_schedule_win")
        print(f"[GHOST-DEBUG] _refresh_aux_window(일정창) 완료, 소요 {(_time_dbg.perf_counter()-_t2)*1000:.1f}ms")
        print(f"[GHOST-DEBUG] refresh_all() 전체 종료, 총 소요 {(_time_dbg.perf_counter()-_t0)*1000:.1f}ms")

    def refresh_light(self):
        """[2026-08 신설, 신민용 리포트: "하루씩 진행이 1주씩보다 더 렉걸린다"]
        [2026-08 수정, 신민용 리포트: "순위표가 하루씩 모드에서 실시간
        반영이 안 됨"] 하루씩(스텝) 모드에서 묶음(7일) 도중 매일 부르기
        위한 갱신 — 순위표 창은 매일, 일정 창은 묶음(1주) 끝에만.

        원인 재실측 결과: 애초에 순위표 창(get_league_standings())은
        실제 세이브 50팀 리그 기준 1.5~4.5ms로 원래도 가벼웠다 —
        "0.1~0.5s+"라던 예전 추정은 부정확했다. 진짜 무거운 쪽은 일정
        창(_schedule_win) 하나였다 — 13개 탭(내경기/전체일정/국제대회
        예선·본선/챔스/컵대회 등)을 매번 동기적으로 다 그리는 고정비용이
        실측 0.05초 이상으로 이미 별도 진단된 바 있다(관련 계측 코드가
        ui/schedule_window.py._fill_tabs()에 남아있음). 하루씩 모드가
        1주씩보다 느렸던 진짜 원인은 이 일정 창 갱신을 매일 반복한
        것이었다 — 순위표는 애초에 범인이 아니었다.

        그래서 "일정 창/순위표 창을 묶어서 둘 다 주 1회만" 갱신하던
        기존 방식을 풀어, 순위표만 따로 매일 갱신한다(가벼우니 매일
        갱신해도 체감 렉이 없다) — 일정 창은 여전히 묶음 끝(refresh_all,
        bundle_done=True)에만 갱신해 렉 재발을 막는다.

        날짜/진행률(center_panel의 phase 라벨·요일 콤보 표시)·선수
        패널·로그는 하루하루 실제로 바뀌므로 step_mode 중에도 매일
        갱신해야 화면이 밀리지 않는다."""
        self._update_top()
        self.player_panel.refresh()
        self.center_panel.refresh()
        self.log_panel.refresh()
        # [2026-08 신설] 순위표만 매일 갱신 — _refresh_aux_window가 창이
        # 닫혀있으면 알아서 스킵하므로(비용 0), 열려있을 때만 실제로
        # get_league_standings()가 매일 다시 도는 구조다(1.5~4.5ms 수준).
        self._refresh_aux_window("_standings_win")

    def _refresh_aux_window(self, attr):
        """center_panel 에 보관된 보조 창(attr) 이 열려 있으면 refresh() 한다.
        닫힘/파괴된 창이면 핸들을 비워 둔다."""
        win = getattr(self.center_panel, attr, None)
        if win is None:
            return
        try:
            if win.isVisible():
                win.refresh()
        except RuntimeError:
            # 닫기/파괴된 QDialog 접근 → 핸들 정리
            setattr(self.center_panel, attr, None)
        except Exception:
            pass

    def _nationality_html(self, p):
        """국적 표시 HTML. 본 국적(국제경기 출전국=intl_committed, 없으면 1국적)을
        맨 앞에 크고 밝게, 나머지 국적은 뒤에 작고 흐리게. 최대 3개."""
        nats = []
        for nk, fk in (("nationality","flag"),
                       ("nationality2","flag2"),
                       ("nationality3","flag3")):
            n = p.get(nk, "") or ""
            if n:
                nats.append((n, p.get(fk, "") or ""))
        if not nats:
            return ""
        committed = (p.get("intl_committed", "") or "")
        primary = None
        if committed:
            for n, f in nats:
                if n == committed:
                    primary = (n, f); break
        if primary is None:
            primary = nats[0]
        rest = [(n, f) for (n, f) in nats if (n, f) != primary]

        pn, pf = primary
        star = "★" if committed else ""
        html = (f"<span style='font-size:15px; font-weight:bold; color:#ffd24d'>"
                f"{pf} {pn}{star}</span>")
        if rest:
            extra = " · ".join(f"{f}{n}" for (n, f) in rest)
            html += f"<span style='font-size:11px; color:#9aa3ad'>  ({extra})</span>"
        return html

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
            nat_html = self._nationality_html(p)
            if nat_html:
                txt += f"  |  {nat_html}"
            if p.get("current_team_id"):
                from database import get_conn
                conn = get_conn()
                row = conn.execute("SELECT name FROM teams WHERE id=?",
                                   (p["current_team_id"],)).fetchone()
                conn.close()
                if row: txt += f"  |  {row['name']}"
            txt += f"  |  {p['name']} {p['age']}세"
        else:
            txt = f"{year}  |  S{season} W{week}  |  [{phase}]"
            txt += f"  |  {p['name']} {p['age']}"

        self.top_label.setText(txt)

    # ── 액션 ──────────────────────────────────────

    def _toggle_lang(self):
        self.lang = "en" if self.lang == "ko" else "ko"
        self.lang_btn.setText("KO" if self.lang == "en" else "EN")
        from game_engine import update_player
        update_player(language=self.lang)
        self.refresh_all()

    def _show_career(self):
        # [2026-08 신설, 신민용 요청: "같은 종류의 창은 하나만"]
        if getattr(self, "_career_win", None) is not None:
            self._career_win.raise_(); self._career_win.activateWindow()
            return
        from ui.career_window import CareerWindow
        self._career_win = CareerWindow(self.lang, self)

        def _clear_career(*_a):
            self._career_win = None
        self._career_win.finished.connect(_clear_career)
        self._career_win.show()

    def go_to_start(self):
        """데이터 초기화 후 현재 창을 시작 화면 UI로 완전 교체.
        [2026-08 버그수정, 신민용 리포트: "은퇴 후 '시작 화면으로'를 누르면
        진행률 창도 없이 5초 정도 멈춘다"] reset_game_data()가
        _regenerate_ai_players(전세계 선수단 재생성, ~5초)를 포함하게 된
        뒤로 이 호출도 같이 느려졌는데, 여기선 아직 새 선수단이 필요
        없다(그냥 시작 메뉴로 돌아갈 뿐) — skip_ai_regen=True로 그 부분만
        건너뛴다. 실제 재생성은 사용자가 "새 게임"→"생성"/"랜덤 생성"을
        누르는 시점에 NewPlayerDialog._regenerate_world_with_progress가
        진행률 창과 함께 정식으로 수행한다."""
        from database import reset_game_data
        reset_game_data(skip_ai_regen=True)
        self._show_start_ui()

    def _show_start_ui(self):
        """현재 MainWindow 안에 StartScreen UI를 직접 그림."""
        from PyQt6.QtWidgets import (
            QWidget, QVBoxLayout, QLabel, QPushButton, QDialog,
            QMessageBox, QApplication
        )
        from PyQt6.QtGui import QFont
        from PyQt6.QtCore import Qt
        from database import reset_game_data, get_conn
        from game_engine import get_player
        from constants import GAME_START_YEAR, PLAYER_START_AGE

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

        sub = QLabel(f"{GAME_START_YEAR}년, {PLAYER_START_AGE}살의 당신. 전설이 되어보세요.")
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
        # [버그수정] 게임 화면(showMaximized 상태)에서 넘어올 때 최대화 플래그가
        #   안 풀려서 창이 좌측 위에 눌려붙고, 드래그하면 최대화 크기로
        #   복원돼버리는 문제. 최대화 해제 → 크기 조정 → 화면 중앙 배치 순서로 처리.
        self.showNormal()
        self.setMinimumSize(500, 400)
        self.resize(600, 450)
        screen = self.screen() if self.screen() else QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(geo.center().x() - self.width() // 2,
                  geo.center().y() - self.height() // 2)

        def do_new_game():
            # [2026-08 수정, 신민용 요청: "진행률 창은 새 게임 버튼이 아니라
            # '생성'/'랜덤 생성'을 누른 후에 떠야 하고, 새 게임 누르는
            # 시점엔 바가 안 보여야 한다"] reset_game_data()를 여기서
            # 더 이상 부르지 않는다 — NewPlayerDialog의 "생성"/"랜덤 생성"
            # 버튼을 눌렀을 때(_regenerate_world_with_progress) 진행률
            # 창과 함께 실행된다. start_screen.py의 StartScreen._new_game()과
            # 완전히 동일한 패턴.
            if not _game_confirm(self, "새 게임", "기존 저장 데이터가 삭제됩니다.\n계속하시겠습니까?"):
                return
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
        # 게임 화면은 기본 전체화면(최대화)으로 — 좌측/중앙/우측 패널이 넓게 보이도록.
        self.showMaximized()

    def closeEvent(self, event):
        # [최적화] 인메모리 라이브 DB를 쓰는 경우, 앱 종료 시 마지막으로
        # 한 번 더 game.db에 백업해서 마지막 자동저장(4주 주기) 이후 진행분이
        # 유실되지 않게 한다.
        # [2026-08 수정, 신민용 리포트: "진행 버튼 누를 때 4주마다 멈춘다"]
        # 게임 진행 중 자동저장을 백그라운드 스레드(flush_to_disk_async)로
        # 옮기면서, 앱을 닫는 바로 그 순간 그 백그라운드 백업이 아직 안
        # 끝났을 수 있다 — 같은 임시파일(game.db.tmp)에 두 저장이 동시에
        # 접근하는 걸 막기 위해, 먼저 진행 중인 백업을 기다린 뒤 마지막
        # 동기 저장을 한 번 더 실행해 최신 상태를 확실히 남긴다.
        try:
            from database import flush_to_disk, wait_for_pending_flush
            wait_for_pending_flush()
            flush_to_disk()
        except Exception:
            pass
        event.accept()


# ── 유틸 ──────────────────────────────────────────────────────

def _phase_label(week, lang):
    """[2026-07 수정, 신민용 요청: "여름/겨울에 비시즌이 한 번씩 있으니
    상반기·하반기·비시즌 이렇게 1년에 비시즌이 2번 뜨면 좋겠다"]
    예전엔 여름(프리시즌, 1~3주)은 '비시즌'으로 뜨는데 겨울(국제대회
    구간, 44~52주)은 '국제대회 시즌'이라는 다른 문구로 떠서, 사용자
    입장에선 겨울 비시즌 표시가 아예 안 되는 것처럼 보였다. 이제 두
    구간 모두 동일하게 '비시즌'으로 표시하고, 국제대회가 실제로 열리는
    구간이라는 건 괄호로 부기해 정보 손실 없이 통일한다."""
    ps, pe = SEASON_PHASES["preseason1"]
    fs, fe = SEASON_PHASES["first_half"]; ss, se = SEASON_PHASES["second_half"]
    if   ps <= week <= pe:  return "비시즌"  if lang=="ko" else "Off-Season"
    elif fs <= week <= fe:  return "상반기"  if lang=="ko" else "First Half"
    elif week < ss:         return "비시즌"  if lang=="ko" else "Mid-Season"
    elif week <= se:        return "하반기"  if lang=="ko" else "Second Half"
    else:                   return "비시즌 (국제대회)"  if lang=="ko" else "Off-Season (International)"