"""
ui/world_browser_window.py — 세계 리그 검색 + 역대 챔피언스리그/월드컵/네이션스컵 기록.

[실시간 전환] 이제 모든 리그가 시즌 시작 시 일정을 미리 받고 매주 실시간으로
결과가 채워진다(game_engine._generate_all_league_schedules + 매주
_sim_all_ai_matches). 리그 검색 탭에서 순위표를 열 때 그 자리에서 시뮬레이션할
필요가 없어졌고, 그래서 예전에 있던 '● 라이브 / ○ 미시뮬' 배지와 '미시뮬로
되돌리기' 버튼도 함께 제거했다.

[스타일] 이 게임 UI 전반(offer_window/career_window/standings_window 등)의
  기존 톤 — 배경 #1e1e1e, 카드 #252525, 포인트 그린 #00cc44, 등급/티어 배지 색상 —
  을 그대로 따른다. 새 팔레트를 만들지 않고 기존 언어에 맞춤.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QTabWidget, QWidget, QSplitter, QFrame,
    QAbstractItemView, QScrollArea, QGridLayout, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

import world_browser as wb

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }

QTabWidget::pane { border:1px solid #333; background:#1e1e1e; }
QTabBar::tab { background:#252525; color:#888; padding:7px 18px; font-size:12px; }
QTabBar::tab:selected { background:#1e1e1e; color:#00cc44; border-bottom:2px solid #00cc44; }
QTabBar::tab:hover:!selected { color:#bbb; }

QComboBox, QLineEdit {
    background:#2a2a2a; color:#ccc; border:1px solid #444;
    border-radius:4px; padding:4px 6px; font-size:12px;
}
QComboBox QAbstractItemView {
    background:#2a2a2a; color:#ccc; selection-background-color:#3a6a3a;
}
QLineEdit:focus { border:1px solid #00cc44; }

QListWidget { background:#1e1e1e; color:#ccc; border:1px solid #2a2a2a; }
QListWidget::item { border-bottom:1px solid #242424; }
QListWidget::item:selected { background:#213321; }
QListWidget::item:hover { background:#242424; }

QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a;
               border:none; font-size:12px; }
QTableWidget::item { padding:3px 6px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:5px; }

QPushButton#closeBtn { background:#2a2a2a; color:#ccc; border:1px solid #444;
                        border-radius:4px; padding:7px; font-size:12px; }
QPushButton#closeBtn:hover { background:#383838; }

/* 리그 등급/티어 배지 — offer_window와 동일한 색상 언어 */
#grade_SS { color:#ff4488; font-weight:bold; }
#grade_S  { color:#ff9900; font-weight:bold; }
#grade_A  { color:#ffcc00; font-weight:bold; }
#grade_B  { color:#00ccff; font-weight:bold; }
#grade_C  { color:#00ff66; }
#grade_D, #grade_E, #grade_F { color:#888888; }

#countryPill { color:#aaddff; background:#1a2a3a; border-radius:3px;
               padding:1px 5px; font-size:10px; }
"""

_ALL = "전체"


class WorldBrowserWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("세계 축구 기록실")
        self.setStyleSheet(STYLE)
        # 창 크기를 강제로 고정하지 않고, 내용(테이블 컬럼 수 등)에 맞춰
        # 필요하면 커지도록 한다 — 시작 크기만 적당히 잡아둔다.
        self.resize(980, 640)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        hdr = QLabel("🌍 세계 축구 기록실")
        hdr.setStyleSheet("color:#00cc44;font-size:16px;font-weight:bold;")
        lay.addWidget(hdr)
        sub = QLabel("다른 나라 리그를 살펴보거나, 역대 대회 기록을 확인하세요.")
        sub.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(sub)

        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        tabs.addTab(self._build_league_tab(), "🔍 리그 검색")
        tabs.addTab(self._build_cup_tab(), "🎖 컵대회 검색")
        tabs.addTab(self._build_cl_tab(), "🏆 역대 챔피언스리그")
        tabs.addTab(self._build_wc_tab(), "🌐 역대 월드컵")
        tabs.addTab(self._build_nc_tab(), "🎖 역대 네이션스컵")

        close_btn = QPushButton("닫기")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        lay.addWidget(close_btn)
        self._first_show_done = False

    def showEvent(self, event):
        super().showEvent(event)
        # 최초 표시 시점엔 스플리터/리스트 실제 폭이 아직 안 잡혀 있어서
        # 생성자 안에서 계산한 _ensure_list_fits가 부정확할 수 있다.
        # 실제로 화면에 뜬 뒤 한 번 더 재확인한다(첫 표시 때만).
        if not self._first_show_done:
            self._first_show_done = True
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._ensure_list_fits)

    def _grow_to_fit(self, tbl, extra_w=60, extra_h=140, stretch_col=None):
        """테이블 내용(특히 컬럼 수·긴 텍스트)이 지금 창 폭보다 넓으면 그만큼
        창을 키운다. 이미 충분히 크면(사용자가 늘렸거나 내용이 작으면) 안 건드림
        — 절대 줄이지 않는다(다른 탭 보다가 다시 좁아지는 느낌 방지).
        stretch_col: Stretch 모드인 컬럼(있으면). 늘어난 상태로는 실제 내용
        폭을 잴 수 없으므로, 잠깐 내용기준으로 바꿔 재고 다시 Stretch로 되돌린다."""
        header = tbl.horizontalHeader()
        if stretch_col is not None:
            header.setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.ResizeToContents)
        tbl.resizeColumnsToContents()
        needed_w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount()))
        needed_w += tbl.verticalHeader().width() + extra_w
        needed_h = min(700, tbl.rowCount() * 28 + extra_h)
        if stretch_col is not None:
            header.setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)
        new_w = max(self.width(), needed_w)
        new_h = max(self.height(), needed_h)
        if new_w != self.width() or new_h != self.height():
            self.resize(new_w, new_h)

    # ─────────────────────────────────────────
    # 탭1: 리그 검색
    # ─────────────────────────────────────────
    def _build_league_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        info = QLabel(
            "ℹ️ 모든 리그가 시즌 내내 실시간으로 진행됩니다. 리그를 선택하면 "
            "현재까지의 순위표를 바로 보여줍니다.")
        info.setStyleSheet("color:#888;font-size:11px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        # 필터 행
        filt = QHBoxLayout()
        filt.setSpacing(8)
        lbl1 = QLabel("대륙"); lbl1.setStyleSheet("color:#888;font-size:11px;")
        self.cont_combo = QComboBox()
        self.cont_combo.addItem(_ALL)
        for cont in wb.list_continents():
            self.cont_combo.addItem(cont)
        self.cont_combo.currentTextChanged.connect(self._on_continent_changed)
        filt.addWidget(lbl1)
        filt.addWidget(self.cont_combo)

        lbl2 = QLabel("국가"); lbl2.setStyleSheet("color:#888;font-size:11px;")
        self.country_combo = QComboBox()
        self.country_combo.addItem(_ALL)
        self.country_combo.currentTextChanged.connect(self._refresh_league_list)
        filt.addWidget(lbl2)
        filt.addWidget(self.country_combo)

        lbl3 = QLabel("등급"); lbl3.setStyleSheet("color:#888;font-size:11px;")
        self.grade_combo = QComboBox()
        self.grade_combo.addItem(_ALL)
        for g in wb.list_grades():
            self.grade_combo.addItem(g)
        self.grade_combo.currentTextChanged.connect(self._on_grade_changed)
        filt.addWidget(lbl3)
        filt.addWidget(self.grade_combo)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔎 리그명 · 국가명 · 팀명 검색")
        # [최적화] 팀명까지 검색 대상에 들어가면서 "FC"처럼 흔한 문자열은
        # 매치되는 리그 수가 확 늘어난다(리그당 커스텀 위젯을 새로 만들어야
        # 하는 리스트 재구성이 무거움). 이 무거운 재구성이 글자 하나 칠 때마다
        # 매번 일어나던 걸, 타이핑이 잠깐(250ms) 멈췄을 때 한 번만 실행되도록
        # 디바운스한다 — 최종적으로 화면에 보이는 검색 결과는 기존과 동일.
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(250)
        self._search_debounce.timeout.connect(self._refresh_league_list)
        self.search_box.textChanged.connect(lambda _text: self._search_debounce.start())
        filt.addWidget(self.search_box, 1)
        lay.addLayout(filt)

        # 좌: 리그 목록 / 우: 순위표
        split = QSplitter(Qt.Orientation.Horizontal)
        self._league_split = split
        self.league_list = QListWidget()
        self.league_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.league_list.itemClicked.connect(self._on_league_selected)
        split.addWidget(self.league_list)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(10, 0, 0, 0)

        title_row = QHBoxLayout()
        self.standing_title = QLabel("← 왼쪽에서 리그를 선택하세요")
        self.standing_title.setStyleSheet("color:#00cc44;font-size:14px;font-weight:bold;")
        title_row.addWidget(self.standing_title, 1)
        # [2026-07 신설] '역대 우승팀' 표에서 특정 시즌(연도) 행을 클릭했을 때
        # 그 시즌 전체 순위표로 들어간 상태(season_detail)에서만 보이는 뒤로가기.
        # CL/월드컵/컵대회 탭은 더블클릭 시 별도 다이얼로그(TournamentDetailDialog)를
        # 띄우지만, 리그는 이미 있는 순위표 영역을 그대로 재사용하는 쪽이 다른
        # 필터(대륙/국가/등급)와의 UI 흐름상 자연스러워 같은 패널 안에서 전환한다.
        self.season_back_btn = QPushButton("← 역대 기록으로")
        self.season_back_btn.setVisible(False)
        self.season_back_btn.clicked.connect(self._on_season_back_clicked)
        title_row.addWidget(self.season_back_btn)
        self.history_btn = QPushButton("🏆 역대 우승팀")
        self.history_btn.setCheckable(True)
        self.history_btn.setVisible(False)
        self.history_btn.toggled.connect(self._on_history_toggled)
        title_row.addWidget(self.history_btn)
        right_lay.addLayout(title_row)

        self.standing_sub = QLabel("")
        self.standing_sub.setStyleSheet("color:#888;font-size:11px;")
        right_lay.addWidget(self.standing_sub)
        self.standing_tbl = QTableWidget(0, 0)
        self.standing_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.standing_tbl.verticalHeader().setVisible(False)
        # [2026-07 신설] '역대 우승팀' 모드일 때만 동작 — 연도 행을 클릭하면
        # 그 시즌의 전체 순위표를 보여준다(월드컵/챔스처럼 그 시기 기록을
        # 파고들 수 있게). 현재 순위표/시즌 상세 모드일 땐 아무 동작 없음
        # (_on_standing_row_clicked가 모드를 보고 알아서 무시함).
        self.standing_tbl.cellClicked.connect(self._on_standing_row_clicked)
        right_lay.addWidget(self.standing_tbl)
        split.addWidget(right)
        split.setSizes([440, 500])
        lay.addWidget(split, 1)

        self._country_cache = []  # [{id,name,flag,grade,continent}, ...] 현재 대륙 필터 기준
        self._refresh_country_list()
        self._refresh_league_list()
        return w

    def _refresh_country_list(self):
        cont = None if self.cont_combo.currentText() == _ALL else self.cont_combo.currentText()
        grade = None if self.grade_combo.currentText() == _ALL else self.grade_combo.currentText()
        self._country_cache = wb.list_countries(cont, grade)
        self.country_combo.blockSignals(True)
        self.country_combo.clear()
        self.country_combo.addItem(_ALL)
        for c in self._country_cache:
            self.country_combo.addItem(f"{c['flag']} {c['name']}")
        self.country_combo.blockSignals(False)

    def _on_continent_changed(self, *_a):
        self._refresh_country_list()
        self._refresh_league_list()

    def _on_grade_changed(self, *_a):
        self._refresh_country_list()
        self._refresh_league_list()

    def _selected_country_id(self):
        txt = self.country_combo.currentText()
        if txt == _ALL:
            return None
        for c in self._country_cache:
            if f"{c['flag']} {c['name']}" == txt:
                return c["id"]
        return None

    def pause_refresh(self):
        """[스레드 안전] 시즌/일자 진행 워커(QThread)가 도는 동안 검색 디바운스
        타이머를 멈춰둔다. 이 창은 center_panel._advance()가 여는 비모달
        QDialog라(main_win.setEnabled(False)로도 막히지 않음), 워커가 DB에
        쓰는 도중에도 사용자가 검색창에 계속 타이핑할 수 있다 — 그러면 250ms 뒤
        디바운스가 같은 풀 커넥션으로 SELECT를 던져 메인 스레드와 워커 스레드가
        동시에 DB에 접근하는 경합이 생긴다(schedule_window/standings_window를
        먼저 이렇게 방어해둔 것과 동일한 이유 — 이 창만 목록에서 빠져있었다).
        pause 중 눌린 키 입력 자체는 막지 않고, 그 결과로 예약된 새로고침만
        보류한다 — resume 후 사용자가 다시 타이핑하면 정상적으로 반영된다."""
        self._search_debounce.stop()

    def resume_refresh(self):
        """pause 동안 새로 시작된 타이머는 없으므로(stop만 호출) 되돌릴 상태가
        없다 — 다음 텍스트 변경 시 디바운스가 다시 정상적으로 예약된다."""
        pass

    def _refresh_league_list(self, *_a):
        cont = None if self.cont_combo.currentText() == _ALL else self.cont_combo.currentText()
        cid = self._selected_country_id()
        grade = None if self.grade_combo.currentText() == _ALL else self.grade_combo.currentText()
        q = self.search_box.text().strip() or None
        leagues = wb.search_leagues(continent=cont, country_id=cid, name_query=q, grade=grade)

        self.league_list.clear()
        # 검색 결과가 너무 많으면(대륙 전체 등) UI가 무거워지므로 상한을 둔다.
        #   (DB 조회 자체는 이미 다 끝난 뒤 리스트 위젯에 채우는 단계만 자름)
        MAX_SHOW = 300
        for lg in leagues[:MAX_SHOW]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, lg["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1,
                        f"{lg['flag']} {lg['country']} · {lg['name']} ({lg['tier']}부)")
            row_widget = self._league_row_widget(lg)
            item.setSizeHint(row_widget.sizeHint())
            self.league_list.addItem(item)
            self.league_list.setItemWidget(item, row_widget)
        if len(leagues) > MAX_SHOW:
            note = QListWidgetItem(f"...외 {len(leagues)-MAX_SHOW}개 더 있음 (검색어로 좁혀보세요)")
            note.setFlags(Qt.ItemFlag.NoItemFlags)
            note.setForeground(Qt.GlobalColor.darkGray)
            self.league_list.addItem(note)
        self._ensure_list_fits()

    def _ensure_list_fits(self):
        """리그 목록 행(국가·리그명+티어/등급 배지)이 리스트 폭보다 넓으면
        가로 스크롤로 잘리는 대신 창 자체를 키운다 — 표 쪽 _grow_to_fit과 같은
        '절대 줄이지 않는다' 원칙."""
        max_w = 0
        for i in range(self.league_list.count()):
            it = self.league_list.item(i)
            w = self.league_list.itemWidget(it)
            if w:
                max_w = max(max_w, w.sizeHint().width())
        if max_w == 0:
            return
        scrollbar_w = self.league_list.verticalScrollBar().sizeHint().width()
        needed_list_w = max_w + scrollbar_w + 12
        cur_list_w = self.league_list.width()
        if needed_list_w > cur_list_w:
            grow = needed_list_w - cur_list_w
            new_w = self.width() + grow
            if new_w > self.width():
                self.resize(new_w, self.height())
            sizes = self._league_split.sizes()
            if len(sizes) == 2:
                sizes[0] += grow
                self._league_split.setSizes(sizes)

    def _league_row_widget(self, lg):
        """리그 목록 한 줄: 국기+국가+리그명 / 등급배지 / 티어.
        offer_window의 카드-내부-라벨 조합과 같은 톤(작은 pill 배지)으로 통일.
        [2026-07] 팀명 검색으로 뜬 결과면(lg['matched_team']이 있으면) 그 팀명을
        작게 함께 보여줘서 "왜 이 리그가 검색됐는지" 바로 알 수 있게 한다."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 5, 8, 5)
        h.setSpacing(6)

        name_txt = f"{lg['flag']}  {lg['country']} · {lg['name']}"
        matched_team = lg.get("matched_team")
        if matched_team:
            name_txt += f"  (🔎 {matched_team})"
        name_lbl = QLabel(name_txt)
        name_lbl.setStyleSheet("color:#ddd;font-size:12px;")
        h.addWidget(name_lbl, 1)

        tier_lbl = QLabel(f"{lg['tier']}부")
        tier_lbl.setStyleSheet("color:#888;font-size:10px;background:#2a2a2a;"
                               "border-radius:3px;padding:1px 5px;")
        h.addWidget(tier_lbl)

        grade_lbl = QLabel(f"{lg['grade']}급")
        grade_lbl.setObjectName(f"grade_{lg['grade']}")
        grade_lbl.setStyleSheet("font-size:10px;")
        h.addWidget(grade_lbl)

        return row

    def _on_league_selected(self, item):
        lid = item.data(Qt.ItemDataRole.UserRole)
        if lid is None:
            return
        self._current_league_id = lid
        self.standing_title.setText("⏳ 불러오는 중...")
        self.standing_sub.setText("")
        self.history_btn.setVisible(False)
        self.season_back_btn.setVisible(False)
        self.standing_title.repaint()
        standings = wb.get_league_standings_for_browser(lid)
        self._current_standings = standings  # [신규] 역대 우승팀 토글 시 되돌아올 캐시
        title_text = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        self._current_league_title = title_text
        self.standing_title.setText(f"📊 {title_text}")
        # [신규] 새 리그를 열 때는 항상 순위표부터 보여준다(토글 초기화).
        self._standing_view_mode = "current"
        self.history_btn.blockSignals(True)
        self.history_btn.setChecked(False)
        self.history_btn.setText("🏆 역대 우승팀")
        self.history_btn.blockSignals(False)
        self.history_btn.setVisible(True)
        self._fill_standing_table(standings)

    def _on_history_toggled(self, checked):
        """[신규] 제목 옆 버튼 — 현재 화면에 맞춰 라벨이 서로 바뀌면서 같은
        표 영역을 이번 시즌 순위표(1~8위 전체) ↔ 시즌별 1~3위 기록으로 전환한다."""
        lid = getattr(self, "_current_league_id", None)
        if lid is None:
            return
        self.season_back_btn.setVisible(False)
        if checked:
            self._standing_view_mode = "history"
            self.history_btn.setText("📊 팀 순위")
            rows = wb.get_league_champions(lid)
            self._current_champions_rows = rows  # [신규] 시즌 상세에서 승격/강등 색상 표시용
            self.standing_sub.setText(
                "경기가 진행된 시즌만 표시됩니다 · 연도 행을 클릭하면 그 시즌 전체 순위를 볼 수 있어요"
                if rows else "")
            self._fill_champions_table(rows, wb.league_has_lower_tier(lid))
        else:
            self._standing_view_mode = "current"
            self.history_btn.setText("🏆 역대 우승팀")
            self.standing_sub.setText("")
            self._fill_standing_table(getattr(self, "_current_standings", []))

    def _on_standing_row_clicked(self, row, _col):
        """[2026-07 신설] '역대 우승팀' 표에서 연도 행을 클릭하면 그 시즌의
        전체 순위표(전 구단 승/무/패/득실/승점)를 같은 패널에 보여준다.
        월드컵/챔스/컵대회 탭이 더블클릭으로 그 시기 대진 상세를 보여주는 것과
        같은 맥락 — 리그는 '경기 목록'보다 '그 시즌 순위표'가 더 자연스러운
        상세 정보라 순위표를 그대로 재사용한다.
        [주의] '팀 순위'(현재 시즌) 모드나 이미 시즌 상세를 보는 중엔 이 클릭이
        아무 의미가 없으므로 무시한다(_standing_view_mode로 판별)."""
        if getattr(self, "_standing_view_mode", "current") != "history":
            return
        item = self.standing_tbl.item(row, 0)
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        season, year = data
        lid = getattr(self, "_current_league_id", None)
        if lid is None:
            return
        rows = wb.get_league_standings_for_browser(lid, season=season, year=year)
        # [2026-07 신설] 강등팀은 빨간색, 승격팀은 파란색으로 표시하기 위해
        # 같은 시즌의 승격/강등 팀 명단을 찾는다. _on_history_toggled에서
        # 캐시해둔 champions rows를 재사용해 별도 조회 없이 바로 매칭한다.
        promoted_names, relegated_names = set(), set()
        for champ_row in getattr(self, "_current_champions_rows", []):
            if champ_row.get("season") == season:
                promoted_names = {item["name"] for item in (champ_row.get("promoted") or [])}
                relegated_names = {item["name"] for item in (champ_row.get("relegated") or [])}
                break
        self._standing_view_mode = "season_detail"
        self.history_btn.setVisible(False)
        self.season_back_btn.setVisible(True)
        title_text = getattr(self, "_current_league_title", "")
        self.standing_title.setText(f"📊 {year}년 시즌 최종 순위 — {title_text}")
        self.standing_sub.setText(
            "🔵 파란색 = 승격  ·  🔴 빨간색 = 강등" if (promoted_names or relegated_names) else "")
        self._fill_standing_table(rows, promoted_names=promoted_names, relegated_names=relegated_names)

    def _on_season_back_clicked(self):
        """시즌 상세 순위표에서 '역대 우승팀' 목록으로 되돌아간다."""
        lid = getattr(self, "_current_league_id", None)
        if lid is None:
            return
        self._standing_view_mode = "history"
        self.season_back_btn.setVisible(False)
        self.history_btn.setVisible(True)
        self.history_btn.blockSignals(True)
        self.history_btn.setChecked(True)
        self.history_btn.setText("📊 팀 순위")
        self.history_btn.blockSignals(False)
        rows = wb.get_league_champions(lid)
        self._current_champions_rows = rows
        title_text = getattr(self, "_current_league_title", "")
        self.standing_title.setText(f"🏆 {title_text} 역대 기록")
        self.standing_sub.setText(
            "경기가 진행된 시즌만 표시됩니다 · 연도 행을 클릭하면 그 시즌 전체 순위를 볼 수 있어요"
            if rows else "")
        self._fill_champions_table(rows, wb.league_has_lower_tier(lid))

    def _fill_standing_table(self, rows, promoted_names=None, relegated_names=None):
        promoted_names = promoted_names or set()
        relegated_names = relegated_names or set()
        cols = ["순위", "팀명", "승", "무", "패", "득점", "실점", "득실", "승점"]
        tbl = self.standing_tbl
        tbl.clear()
        tbl.setRowCount(len(rows))
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i, r in enumerate(rows):
            vals = [str(i + 1), r["name"], str(r["wins"]), str(r["draws"]), str(r["losses"]),
                    str(r["goals_for"]), str(r["goals_against"]),
                    str(r["goals_for"] - r["goals_against"]), str(r["pts"])]
            # [2026-07 신설] 그 시즌 실제로 승격/강등된 팀을 색으로 표시
            # (season_detail 모드에서만 promoted_names/relegated_names가 채워짐).
            if r["name"] in relegated_names:
                row_color = QColor("#ff5555")
            elif r["name"] in promoted_names:
                row_color = QColor("#4da6ff")
            elif i < 4:
                row_color = Qt.GlobalColor.white
            else:
                row_color = None
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if row_color is not None:
                    cell.setForeground(row_color)
                tbl.setItem(i, j, cell)
        self._grow_to_fit(tbl, stretch_col=1)

    def _fill_champions_table(self, rows, has_lower_tier=True):
        """'🏆 역대 우승팀' 토글 시 표시되는 시즌별 1~4위 + 강등 순위별 컬럼.
        최신 시즌이 위로 오도록 이미 wb.get_league_champions()에서
        season DESC로 정렬돼 온다. 한 시즌의 성적 상세(승/무/패 등)가 필요하면
        '📊 팀 순위'로 되돌아가 현재 순위표에서 바로 확인할 수 있으므로,
        여기서는 여러 시즌을 한눈에 훑어보기 좋도록 순위 이름만 보여준다.
        [2026-07 추가] 예전엔 1~3위까지만 기록했는데, 모든 리그에 대해
        4위까지 기록하도록 확장(get_league_champions()가 이미 fourth를
        내려주므로 여기서 컬럼만 하나 늘리면 된다).
        [2026-07] 승강 인원이 리그 규모별로 달라져서(game_engine.
        _promo_releg_count) 강등 인원수도 리그마다 다르다. 한 셀에 다
        몰아넣지 않고 "18위(강등)", "19위(강등)", "20위(강등)"처럼
        실제 순위별로 컬럼을 나눠서 보여준다.
        [2026-07 재수정] 승격은 "N위(승격)"처럼 별도 컬럼을 또 만드는 대신,
        이미 있는 1~4위 컬럼 자체를 그 시즌 실제 승격 인원만큼만 파란색으로
        칠한다(신민용 요청) — 승격 인원이 리그마다/시즌마다 다르므로(2팀
        승격이면 1·2위만 파란색, 1팀만 승격이면 1위만 파란색, 나머지는
        흰색 그대로) 별도 컬럼 없이 그 순위 자체가 승격인지 아닌지를
        직관적으로 보여준다. 1부 리그는 애초에 상위 티어가 없어
        get_league_champions()가 promoted를 항상 빈 리스트로 내려주므로,
        자동으로 1~4위가 전부 흰색으로만 남는다(승격 개념 자체가 없음).
        """
        # 강등 순위 집합(같은 리그면 시즌마다 보통 동일하지만, 방어적으로
        # 전체 행에서 등장한 순위를 다 모아 오름차순으로 컬럼을 만든다).
        releg_ranks = sorted({item["rank"] for r in rows for item in (r.get("relegated") or [])})

        cols = (["연도", "🥇 1위", "🥈 2위", "🥉 3위", "🏅 4위"]
                + [f"{rank}위(강등)" for rank in releg_ranks])
        FIXED_COLS = 5  # 연도 + 1~4위 (강등 컬럼이 시작되는 인덱스 기준)
        tbl = self.standing_tbl
        tbl.clear()
        tbl.setRowCount(len(rows))
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for i, r in enumerate(rows):
            # 이 시즌 실제로 승격된 순위 집합 (예: {1,2}면 1·2위칸만 파란색)
            promoted_ranks_this_season = {item["rank"] for item in (r.get("promoted") or [])}
            releg_by_rank = {item["rank"]: item["name"] for item in (r.get("relegated") or [])}
            vals = [str(r["year"]), r["first"], r["second"], r["third"], r.get("fourth", "-")]
            vals += [releg_by_rank.get(rank, "-") for rank in releg_ranks]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j >= 1:
                    cell.setForeground(Qt.GlobalColor.white)
                if 1 <= j <= 4 and j in promoted_ranks_this_season:
                    cell.setForeground(QColor("#4da6ff"))   # 그 순위가 실제 승격됐으면 파란색
                if j >= FIXED_COLS and v != "-":
                    cell.setForeground(Qt.GlobalColor.red)  # 강등 = 빨간색
                if j == 0:
                    # [2026-07 신설] 이 행(연도)을 클릭하면 그 시즌 전체 순위표로
                    # 들어갈 수 있도록 season/year를 셀 데이터에 실어둔다
                    # (_on_standing_row_clicked가 읽어서 조회).
                    cell.setData(Qt.ItemDataRole.UserRole, (r["season"], r["year"]))
                    cell.setToolTip("클릭하면 이 시즌 전체 순위표를 볼 수 있어요")
                tbl.setItem(i, j, cell)
        self._grow_to_fit(tbl, stretch_col=None)

    # ─────────────────────────────────────────
    # 탭2: 컵대회 검색 (2026-07 신설)
    # ─────────────────────────────────────────
    def _build_cup_tab(self):
        """국내 컵대회(FA컵식) 검색 — 나라를 고르면 그 나라 컵대회의 역대
        우승/준우승/3·4위 기록을 보여준다. 리그 검색과 같은 필터 UX를
        쓰되, 컵대회는 나라당 하나뿐이라 목록은 '리그'가 아니라 '나라'
        단위다."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        info = QLabel(
            "💡 나라를 선택하면 역대 컵대회 우승/준우승/3·4위 기록이 뜹니다. "
            "대회 행을 더블클릭하면 라운드별 대진 상세를 볼 수 있어요.")
        info.setStyleSheet("color:#666;font-size:11px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        filt = QHBoxLayout()
        filt.setSpacing(8)
        lbl1 = QLabel("대륙"); lbl1.setStyleSheet("color:#888;font-size:11px;")
        self.cup_cont_combo = QComboBox()
        self.cup_cont_combo.addItem(_ALL)
        for cont in wb.list_continents():
            self.cup_cont_combo.addItem(cont)
        self.cup_cont_combo.currentTextChanged.connect(self._refresh_cup_country_list)
        filt.addWidget(lbl1)
        filt.addWidget(self.cup_cont_combo)

        self.cup_search_box = QLineEdit()
        self.cup_search_box.setPlaceholderText("🔎 나라명 검색 (예: 대한민국)")
        self.cup_search_box.textChanged.connect(self._refresh_cup_country_list)
        filt.addWidget(self.cup_search_box, 1)
        lay.addLayout(filt)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._cup_split = split
        self.cup_country_list = QListWidget()
        self.cup_country_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.cup_country_list.itemClicked.connect(self._on_cup_country_selected)
        split.addWidget(self.cup_country_list)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(10, 0, 0, 0)

        self.cup_title = QLabel("← 왼쪽에서 나라를 선택하세요")
        self.cup_title.setStyleSheet("color:#c48aff;font-size:14px;font-weight:bold;")
        right_lay.addWidget(self.cup_title)

        self.cup_sub = QLabel("")
        self.cup_sub.setStyleSheet("color:#888;font-size:11px;")
        right_lay.addWidget(self.cup_sub)

        self.cup_tbl = QTableWidget(0, 0)
        self.cup_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cup_tbl.verticalHeader().setVisible(False)
        self.cup_tbl.cellDoubleClicked.connect(self._open_cup_detail)
        right_lay.addWidget(self.cup_tbl)
        split.addWidget(right)
        split.setSizes([320, 620])
        lay.addWidget(split, 1)

        self._cup_country_cache = []
        self._refresh_cup_country_list()
        return w

    def _refresh_cup_country_list(self, *_a):
        cont = None if self.cup_cont_combo.currentText() == _ALL else self.cup_cont_combo.currentText()
        q = self.cup_search_box.text().strip().lower()
        countries = wb.list_countries(cont)
        if q:
            countries = [c for c in countries if q in c["name"].lower()]
        self._cup_country_cache = countries

        self.cup_country_list.clear()
        for c in countries:
            has_data = wb.has_cup_data(c["id"])
            item = QListWidgetItem(f"{c['flag']}  {c['name']}" + ("" if has_data else "  (기록 없음)"))
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            if not has_data:
                item.setForeground(Qt.GlobalColor.darkGray)
            self.cup_country_list.addItem(item)

    def _on_cup_country_selected(self, item):
        cid = item.data(Qt.ItemDataRole.UserRole)
        if cid is None:
            return
        rows = wb.get_cup_history(cid)
        cname = item.text().split("  (")[0]
        self.cup_title.setText(f"🎖️ {cname} 역대 컵대회 기록")
        self.cup_sub.setText(
            f"{rows[0]['name']}  ·  완료된 대회 {len(rows)}건" if rows
            else "이 나라에서 완료된 컵대회 기록이 없습니다")

        cols = ["연도", "대회명", "🏆 우승", "🥈 준우승", "🥉 3위", "4위"]
        tbl = self.cup_tbl
        tbl.clear()
        tbl.setRowCount(len(rows))
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i, r in enumerate(rows):
            # [2026-07 신설] 우승/준우승/3위/4위 팀 옆에 그 시즌 소속 부수를
            # "(N부)"로 함께 표시 — 하위 리그 팀이 이변으로 우승한 경우 등을
            # 한눈에 알아볼 수 있게. tier 정보가 없으면(팀 없음 "-") 그대로 둔다.
            def _with_tier(name, tier):
                return f"{name} ({tier}부)" if (name not in ("-", "?") and tier) else name
            vals = [str(r["year"]), r["name"],
                    _with_tier(r["winner"], r.get("winner_tier")),
                    _with_tier(r["runner_up"], r.get("runner_up_tier")),
                    _with_tier(r["third"], r.get("third_tier")),
                    _with_tier(r["fourth"], r.get("fourth_tier"))]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j >= 2:
                    cell.setForeground(Qt.GlobalColor.white)
                if j == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, r["id"])
                tbl.setItem(i, j, cell)
        self._grow_to_fit(tbl, stretch_col=1)

    def _open_cup_detail(self, row, _col):
        item = self.cup_tbl.item(row, 0)
        tid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tid is None:
            return
        name_item = self.cup_tbl.item(row, 1)
        title = f"{item.text()}년 {name_item.text() if name_item else ''}"
        detail = wb.get_cup_tournament_detail(tid)
        dlg = TournamentDetailDialog(title, detail, team_based=True, parent=self)
        dlg.exec()

    # ─────────────────────────────────────────
    # 탭3: 역대 챔피언스리그
    # ─────────────────────────────────────────
    def _build_cl_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        filt = QHBoxLayout()
        lbl = QLabel("대륙"); lbl.setStyleSheet("color:#888;font-size:11px;")
        self.cl_cont_combo = QComboBox()
        for cont in [_ALL, "유럽", "아시아", "아프리카", "북남미"]:
            self.cl_cont_combo.addItem(cont)
        self.cl_cont_combo.currentTextChanged.connect(self._refresh_cl_table)
        filt.addWidget(lbl)
        filt.addWidget(self.cl_cont_combo)
        filt.addStretch()
        lay.addLayout(filt)

        self.cl_tbl = QTableWidget(0, 0)
        self.cl_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cl_tbl.verticalHeader().setVisible(False)
        self.cl_tbl.cellDoubleClicked.connect(self._open_cl_detail)
        lay.addWidget(self.cl_tbl)
        hint = QLabel("💡 대회를 더블클릭하면 조별리그·토너먼트 상세를 볼 수 있어요")
        hint.setStyleSheet("color:#666;font-size:10px;")
        lay.addWidget(hint)

        self._refresh_cl_table()
        return w

    def _refresh_cl_table(self, *_a):
        cont = None if self.cl_cont_combo.currentText() == _ALL else self.cl_cont_combo.currentText()
        rows = wb.get_cl_history(continent=cont)
        cols = ["연도", "대회", "🥇 우승", "🥈 준우승", "🥉 3위", "4위"]
        self.cl_tbl.clear()
        self.cl_tbl.setRowCount(len(rows))
        self.cl_tbl.setColumnCount(len(cols))
        self.cl_tbl.setHorizontalHeaderLabels(cols)
        self.cl_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.cl_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        def _fmt_team(r, key):
            name = r.get(f"{key}_name") or ""
            if not name:
                return "-"
            flag = r.get(f"{key}_flag") or ""
            country = r.get(f"{key}_country") or ""
            base = f"{flag} {name}".strip()
            return f"{base} ({country})" if country else base

        for i, r in enumerate(rows):
            vals = [str(r["year"]), r["name"],
                    _fmt_team(r, "winner"), _fmt_team(r, "runner_up"),
                    _fmt_team(r, "third"), _fmt_team(r, "fourth")]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 2:
                    cell.setForeground(Qt.GlobalColor.yellow)
                if j == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, r["id"])
                self.cl_tbl.setItem(i, j, cell)
        self._show_empty_state(self.cl_tbl, rows, "아직 완료된 대회가 없습니다", len(cols))
        self._grow_to_fit(self.cl_tbl, stretch_col=1)

    def _open_cl_detail(self, row, _col):
        item = self.cl_tbl.item(row, 0)
        tid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tid is None:
            return
        name_item = self.cl_tbl.item(row, 1)
        title = f"{item.text()} {name_item.text() if name_item else ''}"
        detail = wb.get_cl_tournament_detail(tid)
        dlg = TournamentDetailDialog(title, detail, team_based=True, parent=self)
        dlg.exec()

    # ─────────────────────────────────────────
    # 탭3: 역대 월드컵
    # ─────────────────────────────────────────
    def _build_wc_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        self.wc_tbl = QTableWidget(0, 0)
        self.wc_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.wc_tbl.verticalHeader().setVisible(False)
        self.wc_tbl.cellDoubleClicked.connect(
            lambda r, c: self._open_intl_detail(self.wc_tbl, r, wc=True))
        lay.addWidget(self.wc_tbl)
        hint = QLabel("💡 대회를 더블클릭하면 예선·조별리그·토너먼트 상세를 볼 수 있어요")
        hint.setStyleSheet("color:#666;font-size:10px;")
        lay.addWidget(hint)

        rows = wb.get_wc_history()
        self._fill_placement_table(self.wc_tbl, rows,
                                    "아직 완료된 월드컵이 없습니다")
        return w

    # ─────────────────────────────────────────
    # 탭4: 역대 네이션스컵(대륙컵)
    # ─────────────────────────────────────────
    def _build_nc_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        info = QLabel("ℹ️ 대회 발생 연도(4년 주기)가 되면 4개 대륙 전부 자동 생성됩니다.")
        info.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(info)

        filt = QHBoxLayout()
        lbl = QLabel("대회"); lbl.setStyleSheet("color:#888;font-size:11px;")
        self.nc_combo = QComboBox()
        self.nc_combo.addItem(_ALL)
        for name in wb.list_continental_cup_names():
            self.nc_combo.addItem(name)
        self.nc_combo.currentTextChanged.connect(self._refresh_nc_table)
        filt.addWidget(lbl)
        filt.addWidget(self.nc_combo)
        filt.addStretch()
        lay.addLayout(filt)

        self.nc_tbl = QTableWidget(0, 0)
        self.nc_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.nc_tbl.verticalHeader().setVisible(False)
        self.nc_tbl.cellDoubleClicked.connect(
            lambda r, c: self._open_intl_detail(self.nc_tbl, r, wc=False))
        lay.addWidget(self.nc_tbl)
        hint = QLabel("💡 대회를 더블클릭하면 조별리그·토너먼트 상세를 볼 수 있어요")
        hint.setStyleSheet("color:#666;font-size:10px;")
        lay.addWidget(hint)

        self._refresh_nc_table()
        return w

    def _refresh_nc_table(self, *_a):
        name = None if self.nc_combo.currentText() == _ALL else self.nc_combo.currentText()
        rows = wb.get_continental_cup_history(name=name)
        self._fill_placement_table(self.nc_tbl, rows,
                                    "아직 완료된 대회가 없습니다\n(대회 발생 연도가 되어야 기록이 쌓입니다)")

    # ─────────────────────────────────────────
    # 공용 헬퍼
    # ─────────────────────────────────────────
    def _fill_placement_table(self, tbl, rows, empty_msg):
        """연도/대회명 + 1~4위(국기 포함) 공통 테이블 채우기.
        (역대 월드컵/네이션스컵 탭이 동일한 형식이라 공용 헬퍼로 통합)"""
        cols = ["연도", "대회", "🥇 우승", "🥈 준우승", "🥉 3위", "4위"]
        tbl.clear()
        tbl.setRowCount(len(rows))
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i, r in enumerate(rows):
            def _fmt(key):
                nat = r.get(key) or ""
                if not nat:
                    return "-"
                return f"{r.get(f'{key}_flag','')} {nat}".strip()
            vals = [str(r["year"]), r["name"],
                    _fmt("winner"), _fmt("runner_up"), _fmt("third"), _fmt("fourth")]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 2:
                    cell.setForeground(Qt.GlobalColor.yellow)
                if j == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, r["id"])
                tbl.setItem(i, j, cell)
        self._show_empty_state(tbl, rows, empty_msg, len(cols))
        self._grow_to_fit(tbl, stretch_col=1)

    def _open_intl_detail(self, tbl, row, wc):
        item = tbl.item(row, 0)
        tid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tid is None:
            return
        year = int(item.text())
        name_item = tbl.item(row, 1)
        title = f"{item.text()} {name_item.text() if name_item else ''}"
        detail = wb.get_intl_tournament_detail(tid)
        qualifiers = wb.get_wc_qualifier_summary(year) if wc else None
        dlg = TournamentDetailDialog(title, detail, team_based=False,
                                     qualifiers=qualifiers, parent=self)
        dlg.exec()

    def _show_empty_state(self, tbl, rows, msg, n_cols):
        if rows:
            return
        tbl.setRowCount(1)
        note = QTableWidgetItem(msg)
        note.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setForeground(Qt.GlobalColor.darkGray)
        tbl.setItem(0, 0, note)
        tbl.setSpan(0, 0, 1, n_cols)


class TournamentDetailDialog(QDialog):
    """대회 하나(월드컵/네이션스컵/챔피언스리그)의 조별리그 순위 + 토너먼트
    대진을 보여주는 상세 창. [성능] 이미 끝난 대회의 기존 경기기록을
    읽기만 하므로(재시뮬레이션 없음) 여는 데 드는 비용은 무시할 수 있는
    수준이다 — 대회당 매치 수가 많아야 수십 개로 고정돼 있다.
    """
    def __init__(self, title, detail, team_based, qualifiers=None, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle(title)
        self.setStyleSheet(STYLE)
        self.resize(760, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)

        hdr = QLabel(f"📋 {title}")
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        outer.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(14)

        if qualifiers:
            lay.addWidget(self._section_label("🌍 예선 통과국"))
            lay.addWidget(self._build_qualifiers_box(qualifiers))

        groups = detail.get("groups") or {}
        if groups:
            lay.addWidget(self._section_label("⚽ 조별리그"))
            lay.addWidget(self._build_groups_grid(groups, team_based))

        league_standings = detail.get("league_standings") or []
        if league_standings:
            lay.addWidget(self._section_label("⚽ 리그 스테이지"))
            lay.addWidget(self._build_league_standings_table(league_standings))

        knockout = detail.get("knockout") or []
        if knockout:
            lay.addWidget(self._section_label("🏆 토너먼트"))
            for stage in knockout:
                lay.addWidget(self._build_stage_box(stage, team_based))

        if not groups and not league_standings and not knockout:
            empty = QLabel("표시할 대진 기록이 없습니다.")
            empty.setStyleSheet("color:#888;font-size:12px;")
            lay.addWidget(empty)

        lay.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        close_btn = QPushButton("닫기")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        outer.addWidget(close_btn)

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#ccc;font-size:13px;font-weight:bold;"
                          "border-bottom:1px solid #333;padding-bottom:4px;")
        return lbl

    def _card(self):
        f = QFrame()
        f.setStyleSheet("background:#252525;border:1px solid #333;border-radius:8px;")
        return f

    def _team_text(self, name, flag, country=None):
        """'🇰🇷 대한민국' 또는 (CL의 경우) '🇰🇷 팀명 (대한민국)' 형식으로 통일.
        CL 팀은 국가가 따로 있어 팀명만으론 어느 나라 소속인지 안 보였던 문제를 보완."""
        base = f"{flag} {name}".strip()
        return f"{base} ({country})" if country else base

    def _build_qualifiers_box(self, qualifiers):
        box = self._card()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        for conf, teams in qualifiers.items():
            row = QHBoxLayout()
            conf_lbl = QLabel(conf)
            conf_lbl.setStyleSheet(
                "color:#00cc44;font-size:11px;font-weight:bold;min-width:56px;")
            conf_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
            row.addWidget(conf_lbl)
            names = QLabel("   ".join(f"{t['flag']} {t['country']}" for t in teams))
            names.setStyleSheet("color:#ccc;font-size:11px;")
            names.setWordWrap(True)
            row.addWidget(names, 1)
            lay.addLayout(row)
        return box

    def _build_league_standings_table(self, standings):
        """[2026-07 신설] 챔스 스위스 방식 리그 스테이지 전체 순위표(단일 표).
        조별 카드 대신 순위·팀명·승무패·득실·승점을 한 표로 쭉 보여준다."""
        box = self._card()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(0)

        tbl = QTableWidget(len(standings), 7)
        tbl.setHorizontalHeaderLabels(["순위", "팀", "승", "무", "패", "득실", "승점"])
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl.setStyleSheet(
            "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}"
            "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")

        for i, r in enumerate(standings):
            gd = r["gf"] - r["ga"]
            vals = [str(i + 1), self._team_text(r["name"], r["flag"], r.get("country")),
                    str(r["wins"]), str(r["draws"]), str(r["losses"]),
                    f"{gd:+d}", str(r["pts"])]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                tbl.setItem(i, j, item)
        tbl.setFixedHeight(tbl.verticalHeader().defaultSectionSize() * len(standings) + 32)
        lay.addWidget(tbl)
        return box

    def _build_groups_grid(self, groups, team_based):
        box = QWidget()
        grid = QGridLayout(box)
        grid.setSpacing(10)
        n_cols = 2
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for idx, (g, teams) in enumerate(sorted(groups.items())):
            card = self._card()
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            clay = QVBoxLayout(card)
            clay.setContentsMargins(10, 8, 10, 8)
            clay.setSpacing(0)

            title = QLabel(f"{g}조")
            title.setStyleSheet("color:#00cc44;font-size:12px;font-weight:bold;"
                                "padding-bottom:4px;")
            clay.addWidget(title)

            table = QGridLayout()
            table.setHorizontalSpacing(8)
            table.setVerticalSpacing(3)
            headers = ["", "", "승", "무", "패", "득실", "승점"]
            for ci, htxt in enumerate(headers):
                hl = QLabel(htxt)
                hl.setStyleSheet("color:#666;font-size:9px;")
                hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                table.addWidget(hl, 0, ci)

            for rank, t in enumerate(teams):
                name = t["name"] if team_based else t["country"]
                country = t.get("country") if team_based else None
                advancing = rank < 2  # 보통 조 1·2위가 다음 라운드 진출
                text_color = "#fff" if advancing else "#777"
                weight = "bold" if advancing else "normal"

                rank_lbl = QLabel(str(rank + 1))
                rank_lbl.setStyleSheet(f"color:{text_color};font-size:11px;")
                rank_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                table.addWidget(rank_lbl, rank + 1, 0)

                name_lbl = QLabel(self._team_text(name, t["flag"], country))
                name_lbl.setStyleSheet(
                    f"color:{text_color};font-size:11px;font-weight:{weight};")
                # 팀명(+국가)이 길어도 카드 폭을 넘기지 않고 줄바꿈되게.
                # (이게 없으면 긴 이름이 카드 최소폭을 늘려서 2열이 옆으로 밀려나가
                #  가로 스크롤이 생기는 원인이 됨)
                name_lbl.setWordWrap(True)
                name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Preferred)
                table.addWidget(name_lbl, rank + 1, 1)

                for ci, key in [(2, "wins"), (3, "draws"), (4, "losses")]:
                    v = QLabel(str(t[key]))
                    v.setStyleSheet(f"color:{text_color};font-size:11px;")
                    v.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.addWidget(v, rank + 1, ci)

                gd = t["gf"] - t["ga"]
                gd_lbl = QLabel(f"{gd:+d}")
                gd_lbl.setStyleSheet(f"color:{text_color};font-size:11px;")
                gd_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                table.addWidget(gd_lbl, rank + 1, 5)

                pts_lbl = QLabel(str(t["pts"]))
                pts_lbl.setStyleSheet(
                    f"color:{'#00cc44' if advancing else text_color};"
                    f"font-size:11px;font-weight:bold;")
                pts_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                table.addWidget(pts_lbl, rank + 1, 6)

                # 진출권(1·2위)과 탈락권 사이에 얇은 구분선
                if rank == 1 and len(teams) > 2:
                    sep = QFrame()
                    sep.setFixedHeight(1)
                    sep.setStyleSheet("background:#3a3a3a;")
                    table.addWidget(sep, rank + 2, 0, 1, 7)

            table.setColumnStretch(1, 1)
            clay.addLayout(table)
            grid.addWidget(card, idx // n_cols, idx % n_cols)
        return box

    def _build_stage_box(self, stage, team_based):
        box = self._card()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        title = QLabel(stage["stage_ko"])
        title.setStyleSheet("color:#ffcc00;font-size:12px;font-weight:bold;"
                            "padding-bottom:2px;")
        lay.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 5)

        for ri, m in enumerate(stage["matches"]):
            if team_based:
                h = m["home_info"].get("team_name", "?")
                a = m["away_info"].get("team_name", "?")
                hf = m["home_info"].get("flag", "")
                af = m["away_info"].get("flag", "")
                hc = m["home_info"].get("country")
                ac = m["away_info"].get("country")
                pso = m.get("pso_winner") or 0
                pso_win_home = pso and pso == m["home_info"].get("team_id")
                pso_win_away = pso and pso == m["away_info"].get("team_id")
            else:
                h, a = m["home"], m["away"]
                hf = af = ""
                hc = ac = None
                pso = m.get("pso_winner") or ""
                pso_win_home = pso == h
                pso_win_away = pso == a

            hs, aS = m["home_score"], m["away_score"]
            h_won = hs > aS or pso_win_home
            a_won = aS > hs or pso_win_away
            h_style = "color:#fff;font-size:12px;font-weight:bold;" if h_won \
                else "color:#888;font-size:12px;"
            a_style = "color:#fff;font-size:12px;font-weight:bold;" if a_won \
                else "color:#888;font-size:12px;"

            hl = QLabel(self._team_text(h, hf, hc))
            hl.setStyleSheet(h_style)
            hl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hl.setWordWrap(True)
            grid.addWidget(hl, ri, 0)

            score_lbl = QLabel(f"{hs} : {aS}")
            score_lbl.setStyleSheet(
                "color:#ddd;font-size:12px;font-weight:bold;background:#1a1a1a;"
                "border-radius:4px;padding:2px 8px;")
            score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(score_lbl, ri, 1)

            al = QLabel(self._team_text(a, af, ac))
            al.setStyleSheet(a_style)
            al.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            al.setWordWrap(True)
            grid.addWidget(al, ri, 2)

            if pso:
                pso_lbl = QLabel("⚽ 승부차기")
                pso_lbl.setStyleSheet("color:#666;font-size:9px;")
                pso_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(pso_lbl, ri, 3)

        lay.addLayout(grid)
        return box