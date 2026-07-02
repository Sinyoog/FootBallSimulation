"""
ui/world_browser_window.py — 세계 리그 검색 + 역대 챔피언스리그/월드컵/네이션스컵 기록.

[성능 설계]
  리그 검색 탭에서 목록을 훑어보는 것 자체는 순수 DB 조회라 가볍다.
  특정 리그를 '선택'해서 순위표를 열 때만 world_browser.get_or_simulate_league_standings가
  그 리그 하나만 지연 시뮬레이션한다(이미 시뮬된 리그는 재시뮬 없이 즉시 반환).
  → 675개 리그를 상시로 다 돌리는 방식과 달리, 유저가 실제로 들여다본 리그만
    그때그때 살아나므로 주간 진행 성능에 영향이 없다.

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
from PyQt6.QtCore import Qt

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

QPushButton#resetBtn { background:#2a2a2a; color:#999; border:1px solid #444;
                        border-radius:4px; padding:4px 8px; font-size:11px; }
QPushButton#resetBtn:hover { background:#3a2222; color:#ff8888; border:1px solid #7a3030; }

/* 리그 등급/티어 배지 — offer_window와 동일한 색상 언어 */
#grade_SS { color:#ff4488; font-weight:bold; }
#grade_S  { color:#ff9900; font-weight:bold; }
#grade_A  { color:#ffcc00; font-weight:bold; }
#grade_B  { color:#00ccff; font-weight:bold; }
#grade_C  { color:#00ff66; }
#grade_D, #grade_E, #grade_F { color:#888888; }

#simBadgeOn  { color:#00cc44; background:#1a3a1a; border-radius:3px;
               padding:1px 6px; font-size:10px; font-weight:bold; }
#simBadgeOff { color:#777777; background:#2a2a2a; border-radius:3px;
               padding:1px 6px; font-size:10px; }
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
        self.resize(820, 600)

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
        tabs.addTab(self._build_cl_tab(), "🏆 역대 챔피언스리그")
        tabs.addTab(self._build_wc_tab(), "🌐 역대 월드컵")
        tabs.addTab(self._build_nc_tab(), "🎖 역대 네이션스컵")

        close_btn = QPushButton("닫기")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        lay.addWidget(close_btn)

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
            "ℹ️ 리그를 열면 그 자리에서 이번 시즌 경기를 시뮬레이션해 ● 라이브로 표시됩니다. "
            "다시 열어보면 결과는 그대로 유지되고, 되돌리려면 오른쪽의 '미시뮬로 되돌리기'를 누르세요.")
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
        self.search_box.setPlaceholderText("🔎 리그명 · 국가명 검색")
        self.search_box.textChanged.connect(self._refresh_league_list)
        filt.addWidget(self.search_box, 1)
        lay.addLayout(filt)

        # 좌: 리그 목록 / 우: 순위표
        split = QSplitter(Qt.Orientation.Horizontal)
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
        self.reset_btn = QPushButton("⏹ 미시뮬로 되돌리기")
        self.reset_btn.setObjectName("resetBtn")
        self.reset_btn.setVisible(False)
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        title_row.addWidget(self.reset_btn)
        right_lay.addLayout(title_row)

        self.standing_sub = QLabel("")
        self.standing_sub.setStyleSheet("color:#888;font-size:11px;")
        right_lay.addWidget(self.standing_sub)
        self.standing_tbl = QTableWidget(0, 0)
        self.standing_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.standing_tbl.verticalHeader().setVisible(False)
        right_lay.addWidget(self.standing_tbl)
        split.addWidget(right)
        split.setSizes([300, 480])
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

    def _league_row_widget(self, lg):
        """리그 목록 한 줄: 국기+국가+리그명 / 등급배지 / 티어 / 시뮬여부 배지.
        offer_window의 카드-내부-라벨 조합과 같은 톤(작은 pill 배지)으로 통일."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 5, 8, 5)
        h.setSpacing(6)

        name_lbl = QLabel(f"{lg['flag']}  {lg['country']} · {lg['name']}")
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

        sim_lbl = QLabel("● 라이브" if lg["simulated"] else "○ 미시뮬")
        sim_lbl.setObjectName("simBadgeOn" if lg["simulated"] else "simBadgeOff")
        h.addWidget(sim_lbl)
        return row

    def _on_league_selected(self, item):
        lid = item.data(Qt.ItemDataRole.UserRole)
        if lid is None:
            return
        self._current_league_id = lid
        self.standing_title.setText("⏳ 불러오는 중...")
        self.standing_sub.setText("")
        self.reset_btn.setVisible(False)
        self.standing_title.repaint()
        standings, newly = wb.get_or_simulate_league_standings(lid)
        title_text = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        self.standing_title.setText(f"📊 {title_text}")
        self.standing_sub.setText("방금 첫 시뮬레이션됨 (이후부터는 즉시 표시됩니다)" if newly else "")
        self._fill_standing_table(standings)
        # 리셋 버튼: 내 소속 리그는 다른 화면에서도 실시간으로 쓰이는 데이터라
        # 여기서 지우면 커리어 진행이 꼬일 수 있어 노출하지 않는다.
        self.reset_btn.setVisible(not wb.is_my_league(lid))
        if newly:
            self._refresh_league_list()  # 배지(○→●) 갱신

    def _on_reset_clicked(self):
        lid = getattr(self, "_current_league_id", None)
        if lid is None:
            return
        ok = wb.reset_league_simulation(lid)
        if not ok:
            return
        self.standing_title.setText("← 왼쪽에서 리그를 선택하세요")
        self.standing_sub.setText("시뮬레이션 결과를 초기화했습니다 (미시뮬 상태로 돌아감)")
        self.standing_tbl.setRowCount(0)
        self.reset_btn.setVisible(False)
        self._refresh_league_list()  # 배지(●→○) 갱신

    def _fill_standing_table(self, rows):
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
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if i < 4:
                    cell.setForeground(Qt.GlobalColor.white)
                tbl.setItem(i, j, cell)
        self._grow_to_fit(tbl, stretch_col=1)

    # ─────────────────────────────────────────
    # 탭2: 역대 챔피언스리그
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

        knockout = detail.get("knockout") or []
        if knockout:
            lay.addWidget(self._section_label("🏆 토너먼트"))
            for stage in knockout:
                lay.addWidget(self._build_stage_box(stage, team_based))

        if not groups and not knockout:
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