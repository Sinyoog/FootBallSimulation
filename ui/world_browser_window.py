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
    QAbstractItemView, QScrollArea, QGridLayout, QSizePolicy,
    QStyledItemDelegate, QStyle, QMenu, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, QRect, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QShortcut, QKeySequence

import world_browser as wb

# [2026-08 신설, 신민용 리포트: "복사하면 국기/국가/부수까지 같이 복사된다,
# 팀명만 복사되게 해달라"] 셀 화면 텍스트("🇺🇸 토론토 FC (미국)", "보루시아
# 도르트문트 (1부)")와 실제로 클립보드에 복사할 "깨끗한" 텍스트를 분리해
# 저장하기 위한 전용 데이터 롤. 기존에 이미 UserRole(연도/시즌, team_id 등)을
# 여러 곳에서 쓰고 있어서 충돌을 피하려고 +50 오프셋을 둔다.
_CLEAN_TEXT_ROLE = Qt.ItemDataRole.UserRole + 50


def _enable_plain_copy(tbl):
    """[2026-08 신설, 신민용 리포트: "복사하면 국기/국가/부수까지 같이
    복사된다"] 이 테이블의 셀을 우클릭(복사 메뉴)하거나 Ctrl+C를 누르면,
    화면에 보이는 장식(국기·국가·부수)이 아니라 _CLEAN_TEXT_ROLE에
    저장해둔 팀명만 클립보드에 복사한다. 그 롤이 없는 셀(연도 등
    원래부터 장식이 없는 셀)은 item.text()를 그대로 쓴다 — 여러
    '역대 기록' 표(리그 우승팀·컵대회·챔스·클럽월드컵)가 전부 같은
    패턴(팀명 + 부가정보)을 쓰므로 한 헬퍼로 공유한다.
    [2026-08 확장, 신민용 리포트: "대회 상세 화면(리그 스테이지/조별리그)
    표도 복사하면 마찬가지로 국기/국가가 같이 붙는다"] 원래 WorldBrowserWindow
    안의 메서드였는데, TournamentDetailDialog(별개 클래스)의 표에도 같은
    기능이 필요해져서 모듈 레벨 함수로 옮겼다 — 두 클래스가 똑같이
    `_enable_plain_copy(tbl)`로 호출한다."""
    def _clean_text_of(item):
        if item is None:
            return ""
        v = item.data(_CLEAN_TEXT_ROLE)
        return v if v else item.text()

    def _copy_selected():
        items = tbl.selectedItems()
        if not items:
            return
        # 여러 셀이 선택돼 있으면 행/열 순서대로 탭·줄바꿈으로 묶어 복사.
        rows = sorted({it.row() for it in items})
        cols = sorted({it.column() for it in items})
        if len(rows) == 1 and len(cols) == 1:
            QGuiApplication.clipboard().setText(_clean_text_of(items[0]))
            return
        lines = []
        for r in rows:
            line = [_clean_text_of(tbl.item(r, c)) for c in cols if tbl.item(r, c) in items]
            lines.append("\t".join(line))
        QGuiApplication.clipboard().setText("\n".join(lines))

    tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _show_menu(pos):
        item = tbl.itemAt(pos)
        if item is None:
            return
        if item not in tbl.selectedItems():
            tbl.setCurrentItem(item)
        menu = QMenu(tbl)
        act = menu.addAction("복사")
        act.triggered.connect(_copy_selected)
        menu.exec(tbl.viewport().mapToGlobal(pos))

    tbl.customContextMenuRequested.connect(_show_menu)
    sc = QShortcut(QKeySequence.StandardKey.Copy, tbl)
    sc.setContext(Qt.ShortcutContext.WidgetShortcut)
    sc.activated.connect(_copy_selected)


def _attach_label_copy(label, clean_text):
    """[2026-08 신설, 신민용 리포트: "경기 상세 내역(대진표)에서도 국기/
    부수 없이 팀명만 복사되게 해달라"] _enable_plain_copy와 같은 원칙을
    QTableWidget이 아닌 QLabel(토너먼트 대진표의 팀명 라벨)에 적용하기
    위한 헬퍼. QLabel은 셀/선택 개념이 없어서 우클릭 "복사" 메뉴 하나만
    붙인다 — 클릭 자체는 아무 동작도 하지 않는다(신민용 요청: 클릭 시
    다른 화면으로 이동하는 동작은 넣지 않음)."""
    label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def _show_menu(pos):
        menu = QMenu(label)
        act = menu.addAction("복사")
        act.triggered.connect(lambda: QGuiApplication.clipboard().setText(clean_text))
        menu.exec(label.mapToGlobal(pos))

    label.customContextMenuRequested.connect(_show_menu)


def _clamp_and_resize(widget, w, h):
    """다이얼로그를 원하는 크기로 키우되, 화면(작업 영역) 밖으로 넘어가지
    않게 화면 크기 안으로 잘라 적용하고, 필요하면 창을 화면 안으로 다시
    당겨온다. [2026-08 신설, 신민용 리포트: "바로 열면 저렇게 창이
    (화면 밖으로) 넘어간다"] 예전엔 내용 기준으로 무조건 키우기만 해서
    (초기 resize(1600,700)도 포함) 화면이 작거나(예: 다른 창과 절반씩
    나눠 쓰는 모니터 배치) 목록/표 내용이 많은 리그를 열면 다이얼로그가
    화면보다 커져 아래·오른쪽이 화면 밖으로 잘려 보였다. 모든 자동 확대
    지점(_grow_to_fit, _ensure_list_fits, _grow_split_standing_to_fit,
    초기 크기)이 이 함수를 거치게 해서 항상 화면 안에 들어오게 한다."""
    screen = widget.screen() or QGuiApplication.primaryScreen()
    if screen:
        avail = screen.availableGeometry()
        w = min(w, avail.width() - 40)
        h = min(h, avail.height() - 60)
    widget.resize(w, h)
    if screen:
        avail = screen.availableGeometry()
        geo = widget.frameGeometry()
        x = min(max(geo.x(), avail.x()), max(avail.x(), avail.x() + avail.width() - geo.width()))
        y = min(max(geo.y(), avail.y()), max(avail.y(), avail.y() + avail.height() - geo.height()))
        widget.move(x, y)

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

# [2026-08 신설, 신민용 요청: "기록실(챔스/유로파/컨퍼런스) 필터 기본값은
# 전체, 최다 순위 팝업 필터 기본값은 유럽 — 이 둘은 기능적으로 별개의
# filter state로 분리해야 한다"] 이름 그대로 두 화면의 기본값을 각각의
# 상수로 명확히 나눈다. 하나를 바꿔도 다른 하나는 절대 영향받지 않는다.
RECORD_FILTER_DEFAULT = _ALL
RANKING_FILTER_DEFAULT = "유럽"

# [2026-08 신설, 신민용 리포트: "세계기록실 여전히 잠깐 멈추는 느낌"]
# offer_window.py의 등급 팔레트(#grade_SS 등, 위 STYLE과 동일한 값)를
# 델리게이트 paint()에서 그대로 쓰기 위해 하드코딩 — QSS 셀렉터 색상과
# 반드시 일치해야 하므로 값을 바꿀 땐 위 STYLE 블록도 같이 바꿀 것.
_GRADE_COLORS = {
    "SS": "#ff4488", "S": "#ff9900", "A": "#ffcc00", "B": "#00ccff",
    "C": "#00ff66", "D": "#888888", "E": "#888888", "F": "#888888",
}


class _GridRowDelegate(QStyledItemDelegate):
    """[2026-08 신설, 신민용 리포트: "세계기록실 여전히 잠깐 멈추는 느낌"]
    리그/팀/컵대회 검색 리스트는 행마다 실제 QWidget(+QHBoxLayout+QLabel
    여러 개)을 만들어 setItemWidget()으로 꽂는 방식이었다 — 실측 결과
    이 embed 비용 자체가 위젯 내부 복잡도와 무관하게 행당 ~0.55ms로
    고정이었다(300줄=0.15~0.2s). 폰트캐싱/N+1제거/setUpdatesEnabled/
    배치지연을 전부 시도했지만 이 비용 자체는 줄지 않았다 — 유일한
    실질적 해법은 QWidget을 아예 안 만드는 것.

    실제 자식 위젯을 만드는 대신, 각 QListWidgetItem에 "칸 스펙"(텍스트/
    폭/색상/굵기/정렬 리스트, _SPEC_ROLE)만 데이터로 저장해두고, 이
    델리게이트가 paint()에서 QPainter로 직접 그린다 — 진짜 위젯이 하나도
    안 생기므로 setItemWidget() 비용이 원천적으로 없다.

    [위험 관리] 기존 _league_row_widget/_team_row_widget/
    _cup_country_row_widget(실제 QWidget 버전)는 지우지 않고 그대로
    남겨뒀다 — 이 delegate를 리스트에서 setItemDelegate()로 빼기만 하면
    바로 예전 방식(위젯 기반)으로 되돌릴 수 있다.

    마진(10,6,16,6)·칸 간격(10)·칸 폭·폰트 크기는 기존 _col_label/
    _grade_chip과 동일한 값을 그대로 써서 시각적으로 똑같이 맞췄다.
    등급 색상은 이 파일 상단 STYLE(#grade_SS 등)과 정확히 일치하는
    _GRADE_COLORS를 쓴다."""
    _SPEC_ROLE = Qt.ItemDataRole.UserRole + 2
    _LEFT_MARGIN = 10
    _RIGHT_MARGIN = 16
    _SPACING = 10
    _V_MARGIN = 6

    def __init__(self, font_cache_owner, parent=None):
        super().__init__(parent)
        self._owner = font_cache_owner  # _col_label과 폰트 캐시 공유

    def _font_metrics(self, size, bold):
        """_col_label과 완전히 동일한 (size,bold)별 QFont/QFontMetrics 캐시를
        공유한다 — 위젯 기반 경로와 델리게이트 경로가 같은 폰트를 쓴다."""
        cache = getattr(self._owner, "_col_label_font_cache", None)
        if cache is None:
            cache = self._owner._col_label_font_cache = {}
        key = (size, bold)
        cached = cache.get(key)
        if cached is None:
            font = QFont()
            font.setPixelSize(size)
            font.setBold(bold)
            fm = QFontMetrics(font)
            cache[key] = (font, fm)
            return font, fm
        return cached

    def paint(self, painter, option, index):
        spec = index.data(self._SPEC_ROLE)
        if not spec:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
        x = rect.left() + self._LEFT_MARGIN
        for col in spec:
            text = col.get("text", "")
            width = col.get("width", 60)
            color = col.get("color", "#ccc")
            size = col.get("size", 12)
            bold = col.get("bold", False)
            align = col.get("align", Qt.AlignmentFlag.AlignLeft)
            bg = col.get("bg")
            font, fm = self._font_metrics(size, bold)
            if bg:
                # [배지형 칸] 컵대회 검색의 "기록 있음/없음" 배지 재현 —
                # 원본은 QLabel 스타일시트로 background+border-radius+
                # padding을 줬다(color/font-size/background/border-radius:
                # 3px/padding:2px 5px). QPainter로 같은 모양을 직접 그린다.
                pad_h = fm.height() + 4
                badge_rect = QRect(x, rect.top() + (rect.height() - pad_h) // 2,
                                   width, pad_h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(bg))
                painter.drawRoundedRect(badge_rect, 3, 3)
            painter.setPen(QColor(color))
            painter.setFont(font)
            col_rect = QRect(x, rect.top(), width, rect.height())
            elided = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, width - 6)
            painter.drawText(col_rect, int(align) | int(Qt.AlignmentFlag.AlignVCenter), elided)
            x += width + self._SPACING
        painter.restore()

    def sizeHint(self, option, index):
        spec = index.data(self._SPEC_ROLE)
        if not spec:
            return super().sizeHint(option, index)
        total_w = self._LEFT_MARGIN + self._RIGHT_MARGIN
        max_h = 0
        for col in spec:
            total_w += col.get("width", 60) + self._SPACING
            _, fm = self._font_metrics(col.get("size", 12), col.get("bold", False))
            max_h = max(max_h, fm.height())
        total_w -= self._SPACING  # 마지막 칸 뒤엔 spacing 없음(addStretch 자리)
        return QSize(total_w, max_h + self._V_MARGIN * 2)


class WorldBrowserWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("세계 축구 기록실")
        self.setStyleSheet(STYLE)
        # [2026-07 재수정, 신민용 리포트: "승급/강등 PO 표에 자체 가로
        # 스크롤이 생긴다"] 예전엔 resize(980,640)로 좁게 시작한 뒤
        # 나중에 setMinimumWidth(1200)만 걸었는데, 이미 만들어진 다이얼로그
        # 크기에 최소너비가 곧바로 반영되지 않아 여전히 좁게 뜬 채로 PO
        # 표 두 칸이 컬럼 내용에 맞춰 스스로 가로 스크롤을 만들었다.
        # 시작 크기 자체를 넓게 잡는다 — 승급/강등 두 표(각 4열: 단계/
        # 홈팀/스코어/원정팀)가 나란히 있어도 스크롤 없이 다 보이는 폭.
        # [2026-08 수정] 화면(작업 영역)보다 이 크기가 크면 그대로 화면
        # 밖으로 넘어가 버리므로, 화면 안에 들어오게 잘라서 적용한다.
        _clamp_and_resize(self, 1600, 700)

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
        self.tabs = tabs
        lay.addWidget(tabs, 1)

        # [2026-08 계측 추가, 신민용 리포트: "세계기록실도 클릭할 때 렉있어"]
        # 경기일정 창과 동일한 구조(탭 여러 개를 __init__에서 전부 동기로
        # 그린 뒤에야 창이 뜸) — 어느 탭이 무거운지 원인 확정 전이므로
        # 로직은 그대로 두고 구간별 시간만 찍는다.
        import time as _time_wb
        _wb_t0 = _time_wb.perf_counter()
        _wb_marks = []

        tabs.addTab(self._build_league_tab(), "🔍 리그 검색")
        _wb_marks.append(("리그검색", _time_wb.perf_counter()))
        tabs.addTab(self._build_team_tab(), "🏟 팀 검색")
        _wb_marks.append(("팀검색", _time_wb.perf_counter()))
        tabs.addTab(self._build_cup_tab(), "🎖 컵대회 검색")
        _wb_marks.append(("컵대회검색", _time_wb.perf_counter()))
        tabs.addTab(self._build_cl_tab(), "🏆 역대 챔피언스리그")
        tabs.addTab(self._build_el_tab(), "🥈 역대 유로파리그")
        tabs.addTab(self._build_ecl_tab(), "🥉 역대 컨퍼런스리그")
        _wb_marks.append(("역대챔스", _time_wb.perf_counter()))
        tabs.addTab(self._build_cwc_tab(), "🌍 역대 클럽 월드컵")
        _wb_marks.append(("역대CWC", _time_wb.perf_counter()))
        tabs.addTab(self._build_wc_tab(), "🌐 역대 월드컵")
        _wb_marks.append(("역대월드컵", _time_wb.perf_counter()))
        tabs.addTab(self._build_nc_tab(), "🎖 역대 네이션스컵")
        _wb_marks.append(("역대네이션스컵", _time_wb.perf_counter()))
        tabs.addTab(self._build_region_tab(), "🌏 역대 지역컵")
        _wb_marks.append(("역대지역컵", _time_wb.perf_counter()))
        tabs.addTab(self._build_country_tab(), "🌍 국가 검색")
        _wb_marks.append(("국가검색", _time_wb.perf_counter()))

        _wb_total = _wb_marks[-1][1] - _wb_t0
        if _wb_total >= 0.05:
            _prev = _wb_t0
            _parts = []
            for _name, _t in _wb_marks:
                _parts.append(f"{_name} {_t-_prev:.3f}s")
                _prev = _t
            print(f"[PERF-WORLD] WorldBrowserWindow 생성 총 {_wb_total:.3f}s — " + " | ".join(_parts))

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
            QTimer.singleShot(0, self._ensure_all_lists_fit)

    def _ensure_all_lists_fit(self):
        self._ensure_list_fits(self.league_list, self._league_split)
        self._ensure_list_fits(self.team_list, self._team_split)
        self._ensure_list_fits(self.cup_country_list, self._cup_split)

    

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
            _clamp_and_resize(self, new_w, new_h)

    def _col_label(self, text, width, color="#ccc", size=12, bold=False,
                   align=Qt.AlignmentFlag.AlignLeft, tooltip_extra=None):
        """[2026-08 신설, 신민용 리포트: "등급이 오른쪽 벽에 딱 붙어서 시선이
        너무 멀리 이동한다", "정보들이 열(그리드)로 안 맞춰져 있어 들쭉날쭉해
        보인다"] 리그/팀/컵대회 목록 각 줄의 셀 하나. 셀마다 폭을 고정해서
        —내용 길이와 무관하게— 같은 정보가 항상 같은 x좌표에 오도록 만든다
        (매 줄이 별개의 QWidget/QHBoxLayout이라도, 각 칸 폭이 똑같으면
        전체 목록이 표처럼 정렬되어 보인다). 텍스트가 칸보다 길면 끝을
        "…"으로 줄이고, 잘린 원문은 툴팁으로 확인 가능하게 남긴다.

        [2026-08 최적화, 신민용 리포트: "세계기록실도 클릭할 때 렉있어"]
        리그/팀 검색은 한 번에 최대 300줄, 줄마다 이 함수가 3~4번씩
        불려서 열 때마다 900~1200번 호출된다. (size,bold) 조합은 실제로
        몇 종류뿐인데 그때마다 QFont+QFontMetrics를 새로 만들고 있었다 —
        인스턴스에 (size,bold)별로 캐싱해서 재사용한다. 표시 결과(폰트,
        elide, 툴팁)는 완전히 동일하다."""
        lbl = QLabel()
        _cache = getattr(self, "_col_label_font_cache", None)
        if _cache is None:
            _cache = self._col_label_font_cache = {}
        _key = (size, bold)
        _cached = _cache.get(_key)
        if _cached is None:
            font = QFont()
            font.setPixelSize(size)
            font.setBold(bold)
            fm = QFontMetrics(font)
            _cache[_key] = (font, fm)
        else:
            font, fm = _cached
        lbl.setFont(font)
        elided = fm.elidedText(str(text), Qt.TextElideMode.ElideRight, width - 6)
        lbl.setText(elided)
        tip = str(text) if elided != str(text) else None
        if tooltip_extra:
            tip = (tip + "\n" + tooltip_extra) if tip else tooltip_extra
        if tip:
            lbl.setToolTip(tip)
        lbl.setFixedWidth(width)
        lbl.setStyleSheet(f"color:{color};")
        lbl.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def _grade_chip(self, grade, width=40):
        """[2026-08 신설, 신민용 확정: "등급을 팀 이름 바로 옆(국가명 앞쪽)으로
        옮겨라 — 가장 중요한 지표인데 맨 구석에 처박혀 있다"] 등급 배지를
        이름 바로 다음(국가/리그보다 먼저) 고정폭 칸에 둬서, 목록을 훑을 때
        가장 먼저 눈에 들어오게 한다. 색상은 offer_window와 같은 등급별
        팔레트(#grade_SS 등, STYLE에 정의됨)를 objectName으로 그대로 물려받는다."""
        lbl = QLabel(f"{grade}급")
        lbl.setObjectName(f"grade_{grade}")
        lbl.setFixedWidth(width)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet("font-size:11px;font-weight:bold;")
        return lbl

    def _list_header_row(self, cols):
        """[2026-08 신설, 신민용 확정: "리그 검색/팀 검색 맨 위에 고정으로
        리그명|등급|국가|부수 / 팀명|등급|국가|리그명(부수) 이렇게 표시"]
        목록 각 줄이 고정폭 그리드(_col_label/_grade_chip)로 정렬은 되지만,
        그게 무슨 칸인지 알려주는 표 헤더가 없었다. QTableWidget의
        QHeaderView::section과 같은 톤(배경 #252525, 회색 글자)으로,
        각 칸 폭을 그 목록의 row 위젯과 정확히 맞춘 헤더 한 줄을 만들어
        목록 위에 스크롤 없이 고정으로 붙인다.
        cols: [(라벨, 폭, 가운데정렬여부), ...] 순서 — 실제 row 위젯의
        칸 순서·폭과 반드시 일치해야 세로 정렬이 맞는다."""
        row = QWidget()
        row.setStyleSheet("background:#252525; border-bottom:1px solid #333;")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 5, 16, 5)
        h.setSpacing(10)
        for label, width, center in cols:
            lbl = QLabel(label)
            lbl.setFixedWidth(width)
            lbl.setStyleSheet("color:#888;font-size:10px;font-weight:bold;")
            lbl.setAlignment((Qt.AlignmentFlag.AlignCenter if center else Qt.AlignmentFlag.AlignLeft)
                              | Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(lbl)
        h.addStretch(1)
        return row

    def _wrap_list_with_header(self, list_widget, header_row):
        """리스트 위젯 위에 고정 헤더 한 줄을 얹은 컨테이너를 만들어
        반환한다(스플리터엔 리스트 대신 이 컨테이너를 넣는다). 헤더는
        QListWidget 밖에 별도 QWidget으로 둬서 리스트를 스크롤해도 헤더
        줄은 항상 맨 위에 고정으로 보인다."""
        holder = QWidget()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(header_row)
        v.addWidget(list_widget, 1)
        return holder

    # [2026-08 신설, 신민용 요청: "검색창 밑에 최근 검색 버튼들이 쌓이면
    # 좋겠다"] 리그/팀/국가 검색 탭 3곳이 전부 같은 모양(라벨 + 버튼들
    # + 우측 끝 초기화)이라 공용 메서드 하나로 만든다. kind는
    # world_browser.get/add/clear_recent_searches가 쓰는 것과 동일한
    # 키('league'/'team'/'country') — 3개는 서로 독립적으로 쌓인다.
    #
    # 배치: 리그명·국가명·팀명 검색창(각 탭의 filt 행) 바로 아래가 아니라,
    # 우측 상세 패널의 "← 왼쪽에서 OO을 선택하세요" 타이틀 바로 위에 둔다
    # (신민용 확인 요청).
    def _build_recent_search_row(self, kind, search_box, list_widget, name_from_item_fn, select_fn):
        """search_box: 이 탭의 QLineEdit(검색창). list_widget: 이 탭의
        QListWidget. name_from_item_fn(item): 리스트 항목에서 "깨끗한"
        이름(국기/등급 등 장식 없이)을 뽑아내는 함수. select_fn(item):
        그 항목을 실제로 선택했을 때 쓰는 기존 핸들러(_on_league_selected 등)
        — 최근 검색 버튼을 클릭하면 이 함수를 그대로 다시 호출해서 "그
        항목을 클릭해서 들어간 것"과 동일하게 동작하게 한다.

        [2026-08 수정, 신민용 리포트: "내가 입력한 것보다 클릭해서 들어간
        애들이 뜨는 게 맞다 — '치주'라고 쳐서 '치주물루 유나이티드 FC'를
        클릭해서 들어가면 최근 검색엔 '치주물루 유나이티드 FC'가 남아야지
        '치주'가 남으면 안 된다"] 예전엔 검색창에 타이핑을 멈춘 시점(디바운스
        만료)에 그 입력 문자열 자체를 기록했다 — 이제는 그 시점엔 아무것도
        기록하지 않고, 실제로 리스트에서 항목을 클릭해 들어갔을 때(각 탭의
        _on_*_selected 안)만 그 항목의 정식 이름을 기록한다. 리그/팀/국가
        3곳 다 같은 규칙."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 6)
        h.setSpacing(6)

        lbl = QLabel("최근 검색")
        lbl.setStyleSheet("color:#888;font-size:11px;")
        h.addWidget(lbl)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(4)
        h.addLayout(btn_box)
        h.addStretch(1)

        reset_btn = QPushButton("🗑 초기화")
        # [2026-08 버그수정, 신민용 리포트: "검색창에서 엔터 누르면 쌓인
        # 최근 검색이 강제로 초기화된다"] QPushButton은 기본적으로
        # autoDefault=True라, 다이얼로그 안 어딘가(검색창 포함)에서 Enter를
        # 누르면 Qt가 "기본 버튼"을 자동으로 클릭한다 — 이 초기화 버튼이
        # 그 기본 버튼으로 잡혀서 검색창 Enter만 눌러도 목록이 지워졌다.
        # Enter로 트리거되면 안 되는 버튼이라 autoDefault/default를 끈다.
        reset_btn.setAutoDefault(False)
        reset_btn.setDefault(False)
        reset_btn.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#888;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:2px 8px;font-size:11px;}"
            "QPushButton:hover{color:#cc4444;border-color:#cc4444;}")
        h.addWidget(reset_btn)

        def _pick(q):
            # 검색창에 그 이름을 채워 목록을 좁힌 뒤, 정확히 일치하는
            # 항목을 찾아 실제로 클릭해 들어간 것처럼 select_fn을 호출한다.
            search_box.setText(q)
            for i in range(list_widget.count()):
                it = list_widget.item(i)
                if name_from_item_fn(it) == q:
                    list_widget.setCurrentItem(it)
                    select_fn(it)
                    break

        def _refresh():
            while btn_box.count():
                item = btn_box.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
            items = wb.get_recent_searches(kind)
            if not items:
                empty_lbl = QLabel("(없음 — 항목을 클릭해보세요)")
                empty_lbl.setStyleSheet("color:#555;font-size:11px;")
                btn_box.addWidget(empty_lbl)
                return
            for q in items:
                b = QPushButton(q)
                b.setToolTip(q)
                # 같은 이유(Enter → autoDefault 버튼 오발동 방지)로 여기도 끈다.
                b.setAutoDefault(False)
                b.setDefault(False)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet(
                    "QPushButton{background:#232323;color:#aad4ff;border:1px solid #3a3a3a;"
                    "border-radius:10px;padding:2px 10px;font-size:11px;}"
                    "QPushButton:hover{border-color:#00cc44;color:#fff;}")
                b.clicked.connect(lambda _checked=False, qq=q: _pick(qq))
                btn_box.addWidget(b)

        def _reset():
            wb.clear_recent_searches(kind)
            _refresh()

        reset_btn.clicked.connect(_reset)
        row.refresh = _refresh
        _refresh()
        return row

    def _record_recent_selection(self, kind, name, recent_row_attr):
        """리스트에서 항목을 실제로 클릭해 들어갔을 때 호출 — 그 항목의
        정식 이름을 kind별 최근 검색 기록 맨 앞에 남긴다."""
        if not name:
            return
        wb.add_recent_search(kind, name)
        row = getattr(self, recent_row_attr, None)
        if row is not None:
            row.refresh()

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

        # [2026-08 신설, 신민용 요청] 등급 필터 옆에 부수(1부~N부) 필터.
        # 나라마다 리그 깊이가 달라서(4부까지인 나라도, 7부까지인 나라도
        # 있음) 고정 목록이 아니라 실제 DB에 존재하는 티어만 동적으로 채운다.
        lbl_tier = QLabel("부수"); lbl_tier.setStyleSheet("color:#888;font-size:11px;")
        self.tier_combo = QComboBox()
        self.tier_combo.addItem(_ALL)
        for t in wb.list_league_tiers():
            self.tier_combo.addItem(f"{t}부")
        self.tier_combo.currentTextChanged.connect(self._refresh_league_list)
        filt.addWidget(lbl_tier)
        filt.addWidget(self.tier_combo)

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
        # [2026-08 신설] QWidget 대신 paint()로 행을 그리는 델리게이트 —
        # setItemDelegate(None)으로 되돌리면 예전 위젯 기반 방식으로 복귀.
        self.league_list.setItemDelegate(_GridRowDelegate(self, self.league_list))
        league_header = self._list_header_row([
            ("리그명", self._NAME_COL_W, False),
            ("", 16, False),   # 🔎 매칭 표시 칸 자리(라벨 없음, row와 폭만 맞춤)
            ("등급", self._GRADE_COL_W, True),
            ("국가", self._COUNTRY_COL_W, False),
            ("부수", self._TIER_COL_W, True),
            ("팀 수", self._TEAM_COUNT_COL_W, True),
        ])
        split.addWidget(self._wrap_list_with_header(self.league_list, league_header))

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(10, 0, 0, 0)

        self._league_recent_row = self._build_recent_search_row(
            "league", self.search_box, self.league_list,
            lambda it: it.data(_CLEAN_TEXT_ROLE),
            self._on_league_selected)
        right_lay.addWidget(self._league_recent_row)

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
        # [2026-08 신설, 신민용 요청: "역대 우승팀/팀 순위 버튼 옆에 이
        # 리그에서 1등/2등을 가장 많이 한 팀 순위를 보여주는 창을 만들어달라"]
        # 별도 토글이 아니라 눌렀을 때 팝업(RankLeadersDialog)을 여는
        # 방식 — 순위표/역대 우승팀과 달리 이 창은 "같은 자리에서 전환"할
        # 필요 없이 잠깐 띄워서 보고 닫는 참고용 정보이기 때문.
        self.rank_leaders_btn = QPushButton("🥇🥈 최다 순위")
        self.rank_leaders_btn.setVisible(False)
        self.rank_leaders_btn.clicked.connect(self._on_rank_leaders_clicked)
        title_row.addWidget(self.rank_leaders_btn)
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
        _enable_plain_copy(self.standing_tbl)
        right_lay.addWidget(self.standing_tbl, 1)

        # [2026-08 신설, 신민용 리포트: "시즌 순위표가 한 번에 7팀 정도만
        # 보이는데 너무 적다"] 팀 수가 많은 리그(예: 24팀)는 세로 스크롤 없이
        # 한눈에 다 보려면 좌우 두 칸으로 나눠 보여주는 쪽이 스크롤보다 낫다
        # (신민용 검토 후 확정 — 1~n/2위는 왼쪽, 나머지는 오른쪽, 실제 서비스
        # 축구 경기 그래픽에서 20팀 넘는 리그표를 보여줄 때 흔히 쓰는 방식).
        # 팀 수가 적은 리그(예: 8팀)까지 굳이 반으로 쪼개면 오히려 어색하고
        # 허전해 보이므로, _STANDING_SPLIT_THRESHOLD를 넘는 리그만 이 좌우
        # 2단 표를 쓰고 그 이하는 기존 단일 표(self.standing_tbl)를 그대로
        # 쓴다 — 두 표는 서로 배타적으로 하나만 보인다.
        self.standing_split_holder = QWidget()
        split_row = QHBoxLayout(self.standing_split_holder)
        split_row.setContentsMargins(0, 0, 0, 0)
        split_row.setSpacing(10)
        self.standing_tbl_l = QTableWidget(0, 0)
        self.standing_tbl_l.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.standing_tbl_l.verticalHeader().setVisible(False)
        self.standing_tbl_r = QTableWidget(0, 0)
        self.standing_tbl_r.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.standing_tbl_r.verticalHeader().setVisible(False)
        split_row.addWidget(self.standing_tbl_l)
        split_row.addWidget(self.standing_tbl_r)
        self.standing_split_holder.setVisible(False)
        right_lay.addWidget(self.standing_split_holder, 1)
        # [2026-07 신설, 신민용 리포트: "시즌 상세 순위표 아래에 승강전
        # 어떻게 진행됐는지 안 뜬다" → 이어서: "그거 글로만 되어있는데
        # 일정처럼 표(UI)로 보여달라 했잖아"] 처음엔 QLabel에 줄바꿈
        # 텍스트로만 넣어서 "글로 기록"하는 수준이었다 — 다른 표들(순위표,
        # 역대 기록표)과 똑같이 QTableWidget으로 다시 만들어서 일정 화면
        # 느낌으로 통일했다. 시즌 상세(season_detail) 모드에서만, 그 리그가
        # 위/아래 어느 쪽 경계로든 PO에 걸렸을 때만(자동 이동만으로 안
        # 끝났을 때) 보인다.
        # [2026-07 신설, 신민용 확정: "중간 리그는 승급/강등 PO가 둘 다
        # 있으니 좌측엔 승급, 우측엔 강등을 나란히 보여달라"] 예전엔 이
        # 리그가 upper인 경계(강등 방향) 하나만 보여줬는데, 1부/최하위
        # 리그가 아닌 중간 리그(2부/3부 등)는 위쪽 경계(승급 PO)도 따로
        # 있다 — 2부 페이지에서 좌측="2부→1부"(승급), 우측="2부→3부"(강등)
        # 이렇게 같은 화면에 두 방향을 동시에 보여준다. 칸이 부족할 걸
        # 감안해 스플리터 크기도 같이 넓혔다(아래 split.setSizes 참고).
        po_row = QHBoxLayout()
        po_row.setSpacing(10)

        po_left = QVBoxLayout()
        self.po_promo_title = QLabel("⬆ 승급 플레이오프 결과")
        self.po_promo_title.setStyleSheet("color:#4da6ff;font-size:12px;font-weight:bold;padding-top:6px;")
        self.po_promo_title.setVisible(False)
        po_left.addWidget(self.po_promo_title)
        self.po_promo_tbl = QTableWidget(0, 4)
        self.po_promo_tbl.setHorizontalHeaderLabels(["단계", "홈팀", "스코어", "원정팀"])
        self.po_promo_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.po_promo_tbl.verticalHeader().setVisible(False)
        self.po_promo_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.po_promo_tbl.setVisible(False)
        po_left.addWidget(self.po_promo_tbl)
        po_row.addLayout(po_left)

        po_right = QVBoxLayout()
        self.po_results_title = QLabel("⬇ 강등 플레이오프 결과")
        self.po_results_title.setStyleSheet("color:#ffee55;font-size:12px;font-weight:bold;padding-top:6px;")
        self.po_results_title.setVisible(False)
        po_right.addWidget(self.po_results_title)
        self.po_results_tbl = QTableWidget(0, 4)
        self.po_results_tbl.setHorizontalHeaderLabels(["단계", "홈팀", "스코어", "원정팀"])
        self.po_results_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.po_results_tbl.verticalHeader().setVisible(False)
        self.po_results_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.po_results_tbl.setVisible(False)
        po_right.addWidget(self.po_results_tbl)
        po_row.addLayout(po_right)

        right_lay.addLayout(po_row, 0)
        split.addWidget(right)
        split.setSizes([440, 680])
        # [2026-08 버그수정, 신민용 리포트: "몇부 배지와 우측 패널 사이 공간이
        # 너무 넓다"] QSplitter는 기본적으로 전체 폭이 늘어나면(예: 표 내용이
        # 넓어서 다이얼로그가 자동으로 커질 때) 두 판을 "기존 비율대로"
        # 같이 늘린다. 리그 목록은 이미 고정폭 칸(그리드) 구성이라 그 이상
        # 넓어져 봐야 빈 여백만 늘어날 뿐이므로, 늘어나는 폭은 전부 오른쪽
        # 순위표 쪽으로만 가도록 스트레치 비율을 고정한다(왼쪽=0, 오른쪽=1).
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        # [2026-07 재수정] 표 자체의 가로 스크롤을 원천 차단 — 대신 마지막
        # 열(원정팀)이 남는 폭을 채우도록 늘어나게 해서, 창이 좁아져도
        # 표 안에서 스크롤이 생기는 대신 열 폭이 알아서 줄어들게 한다.
        for _po_tbl in (self.po_promo_tbl, self.po_results_tbl):
            _po_tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # [2026-08 버그수정, 신민용 리포트: "강등 플레이오프 표의 '원정팀'
            # 헤더가 가려져(짤려) 보인다"] Stretch 모드인 4번째 칸(원정팀)이
            # 패널이 좁을 때 헤더 텍스트 폭보다 더 줄어들면서 "원정팀"이
            # "정"처럼 잘려 보였다. 최소 칸 폭을 헤더 텍스트가 항상 온전히
            # 들어갈 크기로 못박아서, 아무리 좁아져도 글자가 잘리지 않게 한다.
            _po_tbl.horizontalHeader().setMinimumSectionSize(64)
            _po_tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.setMinimumWidth(1550)
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
        # [2026-08 계측 추가, 신민용 리포트: "폰트캐싱/N+1 고쳤는데도
        # 리그검색이 그대로 0.25s"] 추측성 최적화가 안 먹혔으니 DB조회 vs
        # 위젯 생성 루프 중 진짜 어느 쪽이 무거운지 직접 나눠서 찍는다.
        import time as _time_wl
        _wl_t0 = _time_wl.perf_counter()
        cont = None if self.cont_combo.currentText() == _ALL else self.cont_combo.currentText()
        cid = self._selected_country_id()
        grade = None if self.grade_combo.currentText() == _ALL else self.grade_combo.currentText()
        _tier_txt = self.tier_combo.currentText()
        tier = None if _tier_txt == _ALL else int(_tier_txt.rstrip("부"))
        q = self.search_box.text().strip() or None
        leagues = wb.search_leagues(continent=cont, country_id=cid, name_query=q, grade=grade, tier=tier)
        _wl_t1 = _time_wl.perf_counter()

        self.league_list.clear()
        # 검색 결과가 너무 많으면(대륙 전체 등) UI가 무거워지므로 상한을 둔다.
        #   (DB 조회 자체는 이미 다 끝난 뒤 리스트 위젯에 채우는 단계만 자름)
        MAX_SHOW = 300

        # [2026-08 재작성] _GridRowDelegate 도입으로 위젯 embed 비용이
        # 사라져서, 배치 지연 없이 다시 단순한 동기 루프로 충분히 빠르다
        # (실측: 배치분할해도 다른 창 타이머와 경쟁하면 오히려 더 오래
        # 걸리고 화면이 잠깐 텅 비어 보이는 부작용이 있었음 — 신민용
        # 확인). 위젯 생성 대신 "칸 스펙"(텍스트/폭/색상)만 item에 데이터로
        # 저장해두면 델리게이트가 그린다.
        for lg in leagues[:MAX_SHOW]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, lg["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1,
                        f"{lg['flag']} {lg['country']} · {lg['name']} ({lg['tier']}부)")
            # [2026-08 버그수정, 신민용 리포트: "'list' object has no attribute
            # 'strip'"] UserRole+2는 이미 _GridRowDelegate._SPEC_ROLE이 쓰고
            # 있어서(행 스펙 리스트), 뒤에서 setData(_SPEC_ROLE, ...)가 내가
            # 여기 넣은 순수 리그명을 그대로 덮어써버렸다 — 그래서 나중에
            # item.data(UserRole+2)를 읽으면 문자열이 아니라 스펙 리스트가
            # 나와서 add_recent_search의 .strip()에서 터졌다. 이미 이 파일
            # 상단에 있는, 정확히 이 용도(장식 없는 순수 이름 보관)의
            # _CLEAN_TEXT_ROLE(UserRole+50, _SPEC_ROLE과 안 겹침)을 대신 쓴다.
            item.setData(_CLEAN_TEXT_ROLE, lg["name"])
            item.setData(_GridRowDelegate._SPEC_ROLE, self._league_row_spec(lg))
            matched_team = lg.get("matched_team")
            if matched_team:
                item.setToolTip(f"🔎 검색된 팀: {matched_team}")
            self.league_list.addItem(item)
        if len(leagues) > MAX_SHOW:
            note = QListWidgetItem(f"...외 {len(leagues)-MAX_SHOW}개 더 있음 (검색어로 좁혀보세요)")
            note.setFlags(Qt.ItemFlag.NoItemFlags)
            note.setForeground(Qt.GlobalColor.darkGray)
            self.league_list.addItem(note)
        _wl_t2 = _time_wl.perf_counter()
        self._ensure_list_fits()
        _wl_t3 = _time_wl.perf_counter()
        if _wl_t3 - _wl_t0 >= 0.03:
            print(f"[PERF-WB-LEAGUE] 총 {_wl_t3-_wl_t0:.3f}s — "
                  f"DB조회(search_leagues,{len(leagues)}건) {_wl_t1-_wl_t0:.3f}s | "
                  f"행채우기({min(len(leagues),MAX_SHOW)}줄, delegate) {_wl_t2-_wl_t1:.3f}s | "
                  f"_ensure_list_fits {_wl_t3-_wl_t2:.3f}s")

    def _league_row_spec(self, lg):
        """[2026-08 신설] _league_row_widget과 동일한 칸 구성(이름/매칭표시/
        등급/국가/부수/팀 수)을 QWidget 없이 _GridRowDelegate가 그릴 수 있는
        스펙 리스트로 표현한다. 폭·색상·굵기 값은 _league_row_widget과
        1:1로 동일하게 맞춰서 시각적으로 동일하게 보이게 했다.

        [2026-08 추가, 신민용 요청: "부수 뒤에 참가 팀 수도 보여줘"]
        team_count는 search_leagues()가 이미 한 번의 쿼리로 같이 내려주므로
        (N+1 없음) 여기선 그대로 표시만 한다."""
        matched_team = lg.get("matched_team")
        return [
            {"text": lg["name"], "width": self._NAME_COL_W, "color": "#eee", "bold": True},
            {"text": "🔎" if matched_team else "", "width": 16, "color": "#ccc",
             "align": Qt.AlignmentFlag.AlignCenter},
            {"text": f"{lg['grade']}급", "width": self._GRADE_COL_W,
             "color": _GRADE_COLORS.get(lg["grade"], "#888888"),
             "size": 11, "bold": True, "align": Qt.AlignmentFlag.AlignCenter},
            {"text": f"{lg['flag']} {lg['country']}", "width": self._COUNTRY_COL_W, "color": "#aaddff"},
            {"text": f"{lg['tier']}부", "width": self._TIER_COL_W, "color": "#888",
             "align": Qt.AlignmentFlag.AlignCenter},
            {"text": f"{lg.get('team_count', 0)}팀", "width": self._TEAM_COUNT_COL_W, "color": "#888",
             "align": Qt.AlignmentFlag.AlignCenter},
        ]

    def _fill_list_deferred(self, list_widget, items, build_row, on_done, gen_key, batch_size=100):
        """[2026-08 신설, 신민용 리포트: "세계기록실 여전히 열 때 1초정도
        걸려"] setItemWidget()을 최대 300번 한 번에 몰아 부르면 그 시간만큼
        창이 멈춘 것처럼 보인다 — 폰트캐싱/N+1제거/setUpdatesEnabled를 다
        해봤지만 이 고정비용(위젯 하나 embed하는 데 걸리는 시간) 자체는
        줄지 않았다(실측: 위젯 개수와 무관하게 행당 ~0.55ms로 일정, 즉
        setItemWidget() 자체의 비용). 위젯/스타일은 그대로 두고, 채우는
        작업을 한 번에 몰아서 하지 않고 여러 배치로 나눠 QTimer.singleShot
        (0, ...)으로 이어붙인다 — 창은 즉시 뜨고 목록이 몇 프레임에 걸쳐
        차오른다(먹통처럼 안 보임). 최종 화면 결과(순서·내용·스타일)는
        100% 동일, "언제" 그려지느냐만 다르다.

        [2026-08 재조정, 신민용 리포트: "여전히 잠깐 멈추는 느낌이야"] 배치
        크기를 키워도 첫 배치(100줄)는 여전히 창이 뜨기 "전에" 동기로
        처리되고 있었다 — 리그+팀+컵대회 세 리스트의 첫 배치가 겹치면
        150~200ms가 쌓여 사람 눈엔 여전히 멈칫함으로 느껴진다(100ms
        넘으면 인지됨). 첫 배치조차 즉시 실행하지 않고 다음 이벤트루프
        틱으로 미뤄서, 창을 만드는 동안엔 행 위젯을 단 하나도 만들지
        않게 한다 — 창이 완전히 뜬 "다음" 프레임부터 채워지기 시작한다.

        gen_key: 이 리스트 전용 세대 카운터 속성 이름 — 채우는 도중
        검색어/필터가 바뀌어 새로 이 함수가 다시 불리면 세대가 올라가고,
        이전 배치는 자기 세대가 낡은 걸 확인하고 조용히 중단한다(오래된
        검색 결과가 새 목록 뒤에 섞여 붙는 것을 방지)."""
        gen = getattr(self, gen_key, 0) + 1
        setattr(self, gen_key, gen)
        list_widget.setUpdatesEnabled(False)
        queue = list(items)

        def _step():
            if getattr(self, gen_key, None) != gen:
                return  # 새 검색/필터로 이미 무효화된 배치 — 조용히 중단
            chunk, queue[:] = queue[:batch_size], queue[batch_size:]
            for it in chunk:
                build_row(it)
            if queue:
                QTimer.singleShot(0, _step)
            else:
                list_widget.setUpdatesEnabled(True)
                on_done()

        QTimer.singleShot(0, _step)

    def _ensure_list_fits(self, list_widget=None, splitter=None):
        """목록 행(국가·리그명+티어/등급 배지)이 리스트 폭보다 넓으면 가로
        스크롤로 잘리는 대신 창 자체를 키운다 — 표 쪽 _grow_to_fit과 같은
        '절대 줄이지 않는다' 원칙.
        [2026-08 버그수정, 신민용 리포트: "팀 검색에서 리그명이 가려져
        있다"] 원래 league_list만 검사해서, 팀 검색 탭(team_list)은 목록
        칸(그리드)이 실제로 더 넓은데도 이 보정을 전혀 못 받아 리그명/국가명이
        패널 밖으로 잘려나갔다. 인자로 어떤 목록·스플리터든 받게 일반화해서
        리그/팀/컵대회 세 탭 모두 같은 보정을 받게 한다."""
        list_widget = list_widget or self.league_list
        splitter = splitter or self._league_split
        max_w = 0
        delegate = list_widget.itemDelegate()
        for i in range(list_widget.count()):
            it = list_widget.item(i)
            w = list_widget.itemWidget(it)
            if w:
                max_w = max(max_w, w.sizeHint().width())
            elif isinstance(delegate, _GridRowDelegate):
                # [2026-08 신설] setItemWidget()이 없는 delegate 기반 행은
                # 스펙(칸 폭 리스트)에서 직접 필요한 폭을 계산한다 —
                # _GridRowDelegate.sizeHint()와 동일한 계산식.
                spec = it.data(_GridRowDelegate._SPEC_ROLE)
                if spec:
                    row_w = delegate._LEFT_MARGIN + delegate._RIGHT_MARGIN
                    for col in spec:
                        row_w += col.get("width", 60) + delegate._SPACING
                    row_w -= delegate._SPACING
                    max_w = max(max_w, row_w)
        if max_w == 0:
            return
        scrollbar_w = list_widget.verticalScrollBar().sizeHint().width()
        needed_list_w = max_w + scrollbar_w + 12
        cur_list_w = list_widget.width()
        if needed_list_w > cur_list_w:
            grow = needed_list_w - cur_list_w
            new_w = self.width() + grow
            if new_w > self.width():
                _clamp_and_resize(self, new_w, self.height())
            sizes = splitter.sizes()
            if len(sizes) == 2:
                sizes[0] += grow
                splitter.setSizes(sizes)

    # 리그/팀 목록 공통 칸 폭 — 모든 줄이 같은 폭을 쓰기 때문에 내용 길이와
    # 무관하게 세로로 칸이 맞춰진다("그리드처럼 보인다").
    _NAME_COL_W = 190
    _GRADE_COL_W = 42
    _COUNTRY_COL_W = 118
    _TIER_COL_W = 48
    # [2026-08 신설, 신민용 요청: "부수 뒤에 참가 팀 수도 보여줘"] 팀 수는
    # 많아야 두 자리 숫자(수십 개)라 넓은 칸이 필요 없다 — _TIER_COL_W와
    # 비슷한 좁은 폭으로 충분.
    _TEAM_COUNT_COL_W = 46
    _LEAGUE_COL_W = 168
    _TROPHY_COL_W = 140

    def _league_row_widget(self, lg):
        """리그 목록 한 줄 — 왼쪽부터 [리그명(고정폭)] [등급] [국가] [부수]
        [팀 수] 순서의 그리드. [2026-08 재정리, 신민용 리포트: "등급이 오른쪽 벽에
        딱 붙어 시선이 멀리 이동한다", "칸이 안 맞춰져 들쭉날쭉하다"]
        1) 가장 중요한 지표인 등급을 리그명 바로 옆(국가명보다 앞)으로
           당겨서 훑어보기 쉽게 하고,
        2) 칸마다 폭을 고정해 실제 표(그리드)처럼 세로 정렬을 맞추고,
        3) 마지막 칸 뒤에도 여백을 둬서 리스트 오른쪽 벽/스크롤바에
           바짝 붙어 보이지 않게 했다.
        [2026-07] 팀명 검색으로 뜬 결과면(lg['matched_team']이 있으면) 리그명
        칸 툴팁에 그 팀명을 함께 남겨 "왜 이 리그가 검색됐는지" 알 수 있게 한다.
        [2026-08 추가, 신민용 요청] 부수 다음 칸에 이 리그에 소속된 팀 수를
        보여준다(search_leagues()가 team_count로 이미 내려줌)."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 6, 16, 6)
        h.setSpacing(10)

        matched_team = lg.get("matched_team")
        tip_extra = f"🔎 검색된 팀: {matched_team}" if matched_team else None
        h.addWidget(self._col_label(lg["name"], self._NAME_COL_W, color="#eee",
                                     bold=True, tooltip_extra=tip_extra))
        # 칸 폭을 조건부로 바꾸면(팀명 매칭 여부에 따라) 줄마다 뒤 칸들이
        # 밀려서 그리드 정렬이 깨진다 — 매칭 여부와 무관하게 항상 같은 폭의
        # 표시 칸을 두고, 매칭 없을 땐 빈 채로 둔다.
        match_mark = QLabel("🔎" if matched_team else "")
        match_mark.setFixedWidth(16)
        if matched_team:
            match_mark.setToolTip(f"검색된 팀: {matched_team}")
        h.addWidget(match_mark)

        h.addWidget(self._grade_chip(lg["grade"], self._GRADE_COL_W))
        h.addWidget(self._col_label(f"{lg['flag']} {lg['country']}",
                                     self._COUNTRY_COL_W, color="#aaddff"))
        h.addWidget(self._col_label(f"{lg['tier']}부", self._TIER_COL_W,
                                     color="#888", align=Qt.AlignmentFlag.AlignCenter))
        h.addWidget(self._col_label(f"{lg.get('team_count', 0)}팀", self._TEAM_COUNT_COL_W,
                                     color="#888", align=Qt.AlignmentFlag.AlignCenter))
        h.addStretch(1)
        return row

    def _on_league_selected(self, item):
        lid = item.data(Qt.ItemDataRole.UserRole)
        if lid is None:
            return
        self._current_league_id = lid
        self.po_promo_title.setVisible(False)
        self.po_promo_tbl.setVisible(False)
        self.po_results_title.setVisible(False)
        self.po_results_tbl.setVisible(False)
        self.standing_title.setText("⏳ 불러오는 중...")
        self.standing_sub.setText("")
        self.history_btn.setVisible(False)
        self.rank_leaders_btn.setVisible(False)
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
        self.rank_leaders_btn.setVisible(True)
        self._fill_standing_table(standings)
        # [2026-08 신설] 최근 검색 기록은 "클릭해서 들어간 항목"의 정식
        # 이름으로 남긴다(검색창에 타이핑한 문자열이 아니라).
        clean_name = item.data(_CLEAN_TEXT_ROLE) or ""
        self._record_recent_selection("league", clean_name, "_league_recent_row")

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
                "경기가 진행된 시즌만 표시됩니다 · 연도 칸(파란색)을 클릭하면 그 시즌 전체 순위를 볼 수 있어요"
                if rows else "")
            self._fill_champions_table(rows, wb.league_has_lower_tier(lid))
        else:
            self._standing_view_mode = "current"
            self.history_btn.setText("🏆 역대 우승팀")
            self.standing_sub.setText("")
            self._fill_standing_table(getattr(self, "_current_standings", []))

    def _on_rank_leaders_clicked(self):
        """[2026-08 신설, 신민용 요청] '🥇🥈 최다 순위' 버튼 — 이 리그에서
        1~4위를 가장 많이 한 팀 순위를 별도 팝업(RankLeadersDialog)
        으로 띄운다. get_league_rank_leaders()가 이미 계산까지 다 끝낸
        결과를 주므로 여기선 그대로 다이얼로그에 넘기기만 한다.

        [2026-08 확장, 신민용 요청: "4위 옆에 가장 많이 승격한 팀/가장
        많이 강등한 팀도 넣어달라"] 4위 다음 열로 최다 승격/최다 강등을
        추가한다. 단, 1부 리그는 승격 자체가 없고(더 올라갈 리그가 없음)
        최하위 리그는 강등 자체가 없으므로(더 내려갈 리그가 없음), 해당
        없는 쪽 열은 아예 안 보여준다 — league_has_upper_tier/
        league_has_lower_tier로 판단(역대 우승팀 표에서 승격/강등팀
        목록을 보여줄지 판단하던 것과 동일한 기준)."""
        lid = getattr(self, "_current_league_id", None)
        if lid is None:
            return
        title = getattr(self, "_current_league_title", "") or ""
        data = wb.get_league_rank_leaders(lid)

        keys = ["first", "second", "third", "fourth"]
        key_labels = ["🥇 1위 팀", "🥈 2위 팀", "🥉 3위 팀", "4위 팀"]
        if wb.league_has_upper_tier(lid):   # 1부가 아니면 승격이 있을 수 있음
            keys.append("most_promoted")
            key_labels.append("⬆ 최다 승격")
        if wb.league_has_lower_tier(lid):   # 최하위가 아니면 강등이 있을 수 있음
            keys.append("most_relegated")
            key_labels.append("⬇ 최다 강등")

        dlg = RankLeadersDialog(title, data, keys=tuple(keys),
                                 key_labels=key_labels,
                                 empty_msg="아직 완료된 시즌 기록이 없습니다", parent=self)
        dlg.show()

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
        # [2026-08 신설, 신민용 요청: "아무 칸이나 클릭하면 이동하지 말고
        # 연도 칸만 클릭했을 때 이동하게, 대신 연도 칸을 색으로 표시해서
        # 클릭 지점을 알려달라"] 예전엔 행 전체가 클릭 대상이라, 다른
        # 셀 텍스트를 복사하려고 클릭만 해도 실수로 시즌 상세로 넘어갔다.
        if _col != 0:
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

        # [2026-08 신설, 신민용 요청: "승강 플레이오프 들어간 애들은 주황으로
        # 표시하고 싶다"] 승격/강등 PO(둘 다)에 참가한 팀 이름을 전부 모아
        # 둔다 — 이 중 실제로 승격/강등까지 간 팀은 기존처럼 파랑/빨강이
        # 우선(더 확정적인 정보이므로), PO에 나갔지만 결국 잔류한 팀만 주황이
        # 새로 보인다(예전엔 이 팀들이 아무 색도 안 붙어 PO에 나갔던
        # 사실 자체가 순위표에서 안 보였다).
        promo_po_rows = wb.get_po_results(lid, year, direction="promotion")
        releg_po_rows = wb.get_po_results(lid, year, direction="relegation")
        po_names = set()
        for pr in (promo_po_rows or []) + (releg_po_rows or []):
            if pr.get("home"): po_names.add(pr["home"])
            if pr.get("away"): po_names.add(pr["away"])

        _legend = []
        if promoted_names or relegated_names:
            _legend.append("🔵 파란색 = 승격  ·  🔴 빨간색 = 강등")
        if po_names:
            _legend.append("🟠 주황색 = 승강 플레이오프 진출(잔류)")
        self.standing_sub.setText("  ·  ".join(_legend))
        self._fill_standing_table(rows, promoted_names=promoted_names,
                                   relegated_names=relegated_names, po_names=po_names)

        self._fill_po_panel(self.po_promo_title, self.po_promo_tbl, promo_po_rows)
        self._fill_po_panel(self.po_results_title, self.po_results_tbl, releg_po_rows)

    def _fill_po_panel(self, title_widget, tbl_widget, po_rows):
        """[2026-07 신설] 승급/강등 PO 패널 채우기 — 두 방향(promotion/
        relegation)이 완전히 같은 표 형식이라 하나로 합쳤다."""
        if po_rows:
            title_widget.setVisible(True)
            tbl_widget.setVisible(True)
            tbl_widget.setColumnCount(4)
            tbl_widget.setHorizontalHeaderLabels(["단계", "홈팀", "스코어", "원정팀"])
            tbl_widget.setRowCount(len(po_rows))
            for i, pr in enumerate(po_rows):
                home_won = pr["home_won"]
                score_str = f"{pr['home_score']} - {pr['away_score']}"
                if pr["pso_score"]:
                    score_str += f"  (PSO {pr['pso_score']})"
                stage_item = QTableWidgetItem(pr["stage"])
                home_item = QTableWidgetItem(f"{pr['home']} ({pr['home_tier']}부)")
                score_item = QTableWidgetItem(score_str)
                away_item = QTableWidgetItem(f"{pr['away']} ({pr['away_tier']}부)")
                score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # 승자 쪽 팀명을 파란색으로 강조 — 순위표의 "파란색=승격" 색
                # 규칙과 통일. 예선(준결승 등)은 그 경기 자체의 승자만
                # 강조하고(다음 라운드 진출이지 승강 확정은 아님), 최종
                # 승강전(F)만 실제로 "위 리그로 가는 쪽"이라는 의미가 된다.
                if home_won:
                    home_item.setForeground(QColor("#4da6ff"))
                    away_item.setForeground(Qt.GlobalColor.red)
                else:
                    away_item.setForeground(QColor("#4da6ff"))
                    home_item.setForeground(Qt.GlobalColor.red)
                tbl_widget.setItem(i, 0, stage_item)
                tbl_widget.setItem(i, 1, home_item)
                tbl_widget.setItem(i, 2, score_item)
                tbl_widget.setItem(i, 3, away_item)
            tbl_widget.resizeRowsToContents()
            # [2026-07 재수정, 신민용 리포트: "위아래 스크롤도 없애고 다
            # 보이게 해달라"] 예전엔 "헤더 30 + 행마다 28"로 높이를
            # 어림잡았는데, 실제 렌더링된 행 높이(길게 줄바꿈된 팀명 등)가
            # 이 가정보다 크면 컨테이너 안에 다 안 들어가 표 자체에 세로
            # 스크롤이 생겼다. resizeRowsToContents() 이후의 실제 측정값
            # (헤더 높이 + 각 행의 실제 높이 합 + 여유분)으로 정확히
            # 계산하고, 세로 스크롤바 자체도 꺼서 넘치는 일이 없게 한다.
            tbl_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            _actual_h = tbl_widget.horizontalHeader().height()
            for _r in range(tbl_widget.rowCount()):
                _actual_h += tbl_widget.rowHeight(_r)
            tbl_widget.setFixedHeight(_actual_h + 6)  # 프레임 여유분
        else:
            title_widget.setVisible(False)
            tbl_widget.setVisible(False)

    def _on_season_back_clicked(self):
        """시즌 상세 순위표에서 '역대 우승팀' 목록으로 되돌아간다."""
        self.po_promo_title.setVisible(False)
        self.po_promo_tbl.setVisible(False)
        self.po_results_title.setVisible(False)
        self.po_results_tbl.setVisible(False)
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
            "경기가 진행된 시즌만 표시됩니다 · 연도 칸(파란색)을 클릭하면 그 시즌 전체 순위를 볼 수 있어요"
            if rows else "")
        self._fill_champions_table(rows, wb.league_has_lower_tier(lid))

    _STANDING_SPLIT_THRESHOLD = 12  # 이 팀 수를 넘으면 좌/우 2단 표로 전환

    def _fill_standing_table(self, rows, promoted_names=None, relegated_names=None, po_names=None):
        promoted_names = promoted_names or set()
        relegated_names = relegated_names or set()
        po_names = po_names or set()
        cols = ["순위", "팀명", "승", "무", "패", "득점", "실점", "득실", "승점"]

        def _row_color(name, rank0):
            # [2026-08 버그수정] 승격/강등/상위 4팀에 안 걸리는 나머지도
            # 반드시 명시적 색을 줘야 한다 — 안 그러면 배경색과 구분 안 되는
            # 기본(검정) 글자색으로 그려져 "존재하지만 안 보이는" 행이 된다.
            if name in relegated_names:
                return QColor("#ff5555")
            if name in promoted_names:
                return QColor("#4da6ff")
            # [2026-08 신설, 신민용 요청] 승격/강등까지는 안 갔지만 플레이
            # 오프에 나갔던(=잔류) 팀 — 위 두 색보다 우선순위 낮게(더 확정적인
            # 승격/강등 색이 항상 이김).
            if name in po_names:
                return QColor("#ffaa00")
            if rank0 < 4:
                return Qt.GlobalColor.white
            return QColor("#ccc")

        if len(rows) <= self._STANDING_SPLIT_THRESHOLD:
            self.standing_split_holder.setVisible(False)
            self.standing_tbl.setVisible(True)
            self._fill_one_standing_table(self.standing_tbl, cols, rows, 0, _row_color)
            self._grow_to_fit(self.standing_tbl, stretch_col=1)
        else:
            # [2026-08 신설] 팀이 많은 리그(예: 24팀)는 좌: 1~n/2위,
            # 우: n/2+1~n위로 나눠서 스크롤 없이 한 화면에 다 보여준다.
            self.standing_tbl.setVisible(False)
            self.standing_split_holder.setVisible(True)
            half = -(-len(rows) // 2)  # 올림 나눗셈 — 홀수면 왼쪽이 한 팀 더 많음
            self._fill_one_standing_table(self.standing_tbl_l, cols, rows[:half], 0, _row_color)
            self._fill_one_standing_table(self.standing_tbl_r, cols, rows[half:], half, _row_color)
            self._grow_split_standing_to_fit()

    def _fill_one_standing_table(self, tbl, cols, rows, rank_offset, row_color_fn):
        """표 하나에 순위표 행을 채운다. rank_offset은 2단 분할 시 오른쪽
        표의 순위 번호를 이어서 매기기 위한 시작 오프셋(왼쪽=0)."""
        tbl.clear()
        tbl.setRowCount(len(rows))
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i, r in enumerate(rows):
            rank0 = rank_offset + i
            vals = [str(rank0 + 1), r["name"], str(r["wins"]), str(r["draws"]), str(r["losses"]),
                    str(r["goals_for"]), str(r["goals_against"]),
                    str(r["goals_for"] - r["goals_against"]), str(r["pts"])]
            row_color = row_color_fn(r["name"], rank0)
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setForeground(row_color)
                tbl.setItem(i, j, cell)

    def _grow_split_standing_to_fit(self):
        """_grow_to_fit의 2단 표 버전 — 좌우 두 표를 한 줄에 놓고 봐야 하므로
        폭은 (왼쪽 표 폭 + 오른쪽 표 폭 + 표 사이 여백)의 합으로, 높이는
        (반으로 쪼갰으니) 더 적은 행 수 기준으로 계산한다. 역시 절대 줄이지
        않는다(_grow_to_fit과 같은 원칙)."""
        total_w = 20  # 두 표 사이 spacing
        max_rows = 0
        for tbl in (self.standing_tbl_l, self.standing_tbl_r):
            header = tbl.horizontalHeader()
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            tbl.resizeColumnsToContents()
            w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount()))
            w += tbl.verticalHeader().width()
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            total_w += w
            max_rows = max(max_rows, tbl.rowCount())
        total_w += 60  # extra_w, _grow_to_fit과 동일한 여유분
        needed_h = min(700, max_rows * 28 + 140)
        new_w = max(self.width(), total_w)
        new_h = max(self.height(), needed_h)
        if new_w != self.width() or new_h != self.height():
            _clamp_and_resize(self, new_w, new_h)

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
        [2026-08 추가, 신민용 요청] "이 리그로 승격해서 들어온 팀"도
        팀명 + 그때(출신 리그) 순위를 같이 보여준다. get_league_champions()가
        내려주는 promoted_in을 시즌별 개수가 다를 수 있으므로(승격 인원은
        리그 규모에 따라 다름) 강등 컬럼과 같은 방식으로, 등장하는 최대
        개수만큼 "승격① / 승격② ..." 컬럼을 동적으로 만든다.
        [2026-08 버그수정, 신민용 리포트: "K2 리그 기록실에 K1에서 강등된
        팀이 승격팀으로 뜬다"] promoted_in엔 원래 "이 리그로 들어온 팀"이
        방향 구분 없이 다 섞여 있었는데(위에서 강등돼 내려온 팀까지 포함),
        world_browser.get_league_champions()에서 from_tier로 방향을 갈라
        promoted_in(진짜 승격)/relegated_in(강등되어 옴)으로 분리했다.
        이 표에서도 신민용님이 제시한 배치(1~4위 → N위(강등) → 강등팀 →
        승격팀) 그대로 강등팀 컬럼을 승격팀 앞에 추가한다.
        """
        # [2026-08] 직전에 팀 수가 많은 리그의 시즌 상세(2단 분할 표)를 보고
        # 있었을 수 있다 — 역대 우승팀 목록은 항상 단일 표(standing_tbl)를
        # 쓰므로 전환 시 분할 표는 숨기고 단일 표를 다시 보여준다.
        self.standing_split_holder.setVisible(False)
        self.standing_tbl.setVisible(True)
        # 강등 순위 집합(같은 리그면 시즌마다 보통 동일하지만, 방어적으로
        # 전체 행에서 등장한 순위를 다 모아 오름차순으로 컬럼을 만든다).
        releg_ranks = sorted({item["rank"] for r in rows for item in (r.get("relegated") or [])})
        # 강등되어 들어온 팀 / 승격해서 들어온 팀 수의 시즌별 최댓값만큼 컬럼을 만든다.
        max_relegated_in = max([len(r.get("relegated_in") or []) for r in rows], default=0)
        max_promoted_in = max([len(r.get("promoted_in") or []) for r in rows], default=0)

        cols = (["연도", "🥇 1위", "🥈 2위", "🥉 3위", "🏅 4위"]
                + [f"{rank}위(강등)" for rank in releg_ranks]
                + [f"⬇ 강등팀{'' if max_relegated_in <= 1 else i+1}" for i in range(max_relegated_in)]
                + [f"⬆ 승격팀{'' if max_promoted_in <= 1 else i+1}" for i in range(max_promoted_in)])
        FIXED_COLS = 5  # 연도 + 1~4위 (강등 컬럼이 시작되는 인덱스 기준)
        RELEG_COLS = len(releg_ranks)
        RELEG_IN_COLS = max_relegated_in
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
            relegated_in = r.get("relegated_in") or []
            promoted_in = r.get("promoted_in") or []
            vals = [str(r["year"]), r["first"], r["second"], r["third"], r.get("fourth", "-")]
            clean_vals = [None] * len(vals)   # 이 칸들은 이미 팀명뿐이라 별도 clean 텍스트 불필요
            vals += [releg_by_rank.get(rank, "-") for rank in releg_ranks]
            clean_vals += [None] * len(releg_ranks)

            def _fmt_incoming(item):
                if item["from_rank"]:
                    return f"{item['name']} ({item['from_league']} {item['from_rank']}위)", item["name"]
                return item["name"], item["name"]

            for k in range(max_relegated_in):
                if k < len(relegated_in):
                    disp, clean = _fmt_incoming(relegated_in[k])
                else:
                    disp, clean = "-", None
                vals.append(disp); clean_vals.append(clean)
            for k in range(max_promoted_in):
                if k < len(promoted_in):
                    disp, clean = _fmt_incoming(promoted_in[k])
                else:
                    disp, clean = "-", None
                vals.append(disp); clean_vals.append(clean)
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if clean_vals[j] and clean_vals[j] != v:
                    cell.setData(_CLEAN_TEXT_ROLE, clean_vals[j])
                if j >= 1:
                    cell.setForeground(Qt.GlobalColor.white)
                if 1 <= j <= 4 and j in promoted_ranks_this_season:
                    cell.setForeground(QColor("#4da6ff"))   # 그 순위가 실제 승격됐으면 파란색
                if FIXED_COLS <= j < FIXED_COLS + RELEG_COLS and v != "-":
                    cell.setForeground(Qt.GlobalColor.red)  # 강등(나감) = 빨간색
                if FIXED_COLS + RELEG_COLS <= j < FIXED_COLS + RELEG_COLS + RELEG_IN_COLS and v != "-":
                    cell.setForeground(Qt.GlobalColor.red)  # 강등되어 들어옴 = 빨간색
                if j >= FIXED_COLS + RELEG_COLS + RELEG_IN_COLS and v != "-":
                    cell.setForeground(QColor("#4da6ff"))   # 승격해서 들어온 팀 = 파란색
                if j == 0:
                    # [2026-07 신설] 이 행(연도)을 클릭하면 그 시즌 전체 순위표로
                    # 들어갈 수 있도록 season/year를 셀 데이터에 실어둔다
                    # (_on_standing_row_clicked가 읽어서 조회).
                    cell.setData(Qt.ItemDataRole.UserRole, (r["season"], r["year"]))
                    # [2026-08 신설, 신민용 요청] 예전엔 행 아무 데나 클릭해도
                    # 시즌 상세로 넘어가서, 다른 셀 텍스트를 복사하려다 실수로
                    # 이동하는 경우가 있었다 — 이제 연도 칸만 클릭 가능하므로
                    # (아래 _on_standing_row_clicked에서 _col==0만 처리),
                    # 여기 색으로 "여기가 클릭 지점"임을 표시한다.
                    cell.setForeground(QColor("#66ccff"))
                    f = cell.font(); f.setBold(True); cell.setFont(f)
                    cell.setToolTip("클릭하면 이 시즌 전체 순위표를 볼 수 있어요")
                tbl.setItem(i, j, cell)
        self._grow_to_fit(tbl, stretch_col=None)

    # ─────────────────────────────────────────
    # 탭1.5: 팀 검색 (2026-07 신설, 신민용 확정: "팀 하나의 역대 기록을
    # 보고 싶다 — 우승/리그순위/승격강등을 연도별로")
    # 리그 검색 탭과 거의 같은 UX(대륙/국가/등급 필터 + 검색창 + 좌측
    # 리스트)를 그대로 따르되, 부수(tier) 필터가 하나 더 있고, 우측은
    # 순위표 대신 그 팀의 연도별 기록 목록을 보여준다.
    # ─────────────────────────────────────────
    def _build_team_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        info = QLabel("ℹ️ 팀 하나를 골라 그 팀의 연도별 기록(리그 순위·승격/강등·"
                      "컵대회·챔피언스리그)을 확인하세요.")
        info.setStyleSheet("color:#888;font-size:11px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        filt = QHBoxLayout()
        filt.setSpacing(8)
        lbl1 = QLabel("대륙"); lbl1.setStyleSheet("color:#888;font-size:11px;")
        self.team_cont_combo = QComboBox()
        self.team_cont_combo.addItem(_ALL)
        for cont in wb.list_continents():
            self.team_cont_combo.addItem(cont)
        self.team_cont_combo.currentTextChanged.connect(self._on_team_continent_changed)
        filt.addWidget(lbl1)
        filt.addWidget(self.team_cont_combo)

        lbl2 = QLabel("국가"); lbl2.setStyleSheet("color:#888;font-size:11px;")
        self.team_country_combo = QComboBox()
        self.team_country_combo.addItem(_ALL)
        self.team_country_combo.currentTextChanged.connect(self._refresh_team_list)
        filt.addWidget(lbl2)
        filt.addWidget(self.team_country_combo)

        lbl3 = QLabel("등급"); lbl3.setStyleSheet("color:#888;font-size:11px;")
        self.team_grade_combo = QComboBox()
        self.team_grade_combo.addItem(_ALL)
        for g in wb.list_grades():
            self.team_grade_combo.addItem(g)
        self.team_grade_combo.currentTextChanged.connect(self._refresh_team_list)
        filt.addWidget(lbl3)
        filt.addWidget(self.team_grade_combo)

        lbl4 = QLabel("부수"); lbl4.setStyleSheet("color:#888;font-size:11px;")
        self.team_tier_combo = QComboBox()
        self.team_tier_combo.addItem(_ALL)
        for t in range(1, wb.list_max_tier() + 1):
            self.team_tier_combo.addItem(f"{t}부")
        self.team_tier_combo.currentTextChanged.connect(self._refresh_team_list)
        filt.addWidget(lbl4)
        filt.addWidget(self.team_tier_combo)

        self.team_search_box = QLineEdit()
        self.team_search_box.setPlaceholderText("🔎 리그명 · 국가명 · 팀명 검색")
        self._team_search_debounce = QTimer(self)
        self._team_search_debounce.setSingleShot(True)
        self._team_search_debounce.setInterval(250)
        self._team_search_debounce.timeout.connect(self._refresh_team_list)
        self.team_search_box.textChanged.connect(lambda _text: self._team_search_debounce.start())
        filt.addWidget(self.team_search_box, 1)
        lay.addLayout(filt)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._team_split = split
        self.team_list = QListWidget()
        self.team_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.team_list.itemClicked.connect(self._on_team_selected)
        self.team_list.setItemDelegate(_GridRowDelegate(self, self.team_list))
        team_header = self._list_header_row([
            ("팀명", self._NAME_COL_W, False),
            ("등급", self._GRADE_COL_W, True),
            ("국가", self._COUNTRY_COL_W, False),
            ("리그명(부수)", self._LEAGUE_COL_W, False),
        ])
        split.addWidget(self._wrap_list_with_header(self.team_list, team_header))

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(10, 0, 0, 0)

        self._team_recent_row = self._build_recent_search_row(
            "team", self.team_search_box, self.team_list,
            lambda it: it.data(Qt.ItemDataRole.UserRole + 1),
            self._on_team_selected)
        right_lay.addWidget(self._team_recent_row)

        # [2026-08 신설, 신민용 요청: "팀 검색 상세에 복사하기 버튼을 만들어서
        # 누르면 그 팀의 연도별 기록을 GPT/제미나이가 알아들을 수 있는 텍스트로
        # 뽑아달라"] 표는 화면에서 보기용이고, 이 버튼은 같은 데이터(get_team_history
        # 결과)를 사람이 표를 다시 옮겨 적을 필요 없이 순수 텍스트로 클립보드에
        # 복사해준다 — _format_team_history_text 참고.
        title_row = QHBoxLayout()
        self.team_detail_title = QLabel("← 왼쪽에서 팀을 선택하세요")
        self.team_detail_title.setStyleSheet("color:#00cc44;font-size:14px;font-weight:bold;")
        title_row.addWidget(self.team_detail_title, 1)
        self.team_copy_btn = QPushButton("📋 기록 복사")
        self.team_copy_btn.setEnabled(False)
        self.team_copy_btn.setToolTip("이 팀의 연도별 기록을 텍스트로 복사합니다(GPT/제미나이 등에 붙여넣기용)")
        self.team_copy_btn.clicked.connect(self._on_copy_team_history_clicked)
        title_row.addWidget(self.team_copy_btn)
        right_lay.addLayout(title_row)

        self.team_detail_tbl = QTableWidget(0, 5)
        self.team_detail_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.team_detail_tbl.verticalHeader().setVisible(False)
        self.team_detail_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.team_detail_tbl.setHorizontalHeaderLabels(
            ["연도", "리그", "국내컵", "클럽 대항전", "클럽 월드컵"])
        self.team_detail_tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        for _c in (1, 2, 3, 4):
            self.team_detail_tbl.horizontalHeader().setSectionResizeMode(
                _c, QHeaderView.ResizeMode.Stretch)
        right_lay.addWidget(self.team_detail_tbl, 1)

        split.addWidget(right)
        split.setSizes([440, 900])
        # [2026-08] 리그 검색 탭과 같은 이유 — 팀 목록도 고정폭 그리드라
        # 다이얼로그가 커져도 목록 쪽엔 빈 여백만 늘어난다. 늘어나는 폭은
        # 오른쪽 상세 패널로만 가게 고정한다.
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        self._refresh_team_list()
        return w

    def _on_team_continent_changed(self, *_a):
        self._refresh_team_country_combo()
        self._refresh_team_list()

    def _refresh_team_country_combo(self):
        """[2026-07 신설] 대륙 필터가 바뀌면 국가 콤보도 그 대륙 국가만
        보이도록 다시 채운다 — 리그 검색 탭의 _refresh_country_list와
        동일한 목적, 다만 팀 탭 전용 위젯(self.team_country_combo)에 적용."""
        cont = None if self.team_cont_combo.currentText() == _ALL else self.team_cont_combo.currentText()
        cur = self.team_country_combo.currentText()
        self.team_country_combo.blockSignals(True)
        self.team_country_combo.clear()
        self.team_country_combo.addItem(_ALL)
        countries = wb.list_countries(continent=cont)
        for c in countries:
            self.team_country_combo.addItem(f"{c['flag']} {c['name']}")
        idx = self.team_country_combo.findText(cur)
        self.team_country_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.team_country_combo.blockSignals(False)
        self._team_country_cache = countries

    def _selected_team_country_id(self):
        txt = self.team_country_combo.currentText()
        if txt == _ALL:
            return None
        for c in getattr(self, "_team_country_cache", []):
            if f"{c['flag']} {c['name']}" == txt:
                return c["id"]
        return None

    def _refresh_team_list(self, *_a):
        import time as _time_wt
        _wt_t0 = _time_wt.perf_counter()
        cont = None if self.team_cont_combo.currentText() == _ALL else self.team_cont_combo.currentText()
        cid = self._selected_team_country_id()
        grade = None if self.team_grade_combo.currentText() == _ALL else self.team_grade_combo.currentText()
        tier_txt = self.team_tier_combo.currentText()
        tier = None if tier_txt == _ALL else int(tier_txt.replace("부", ""))
        q = self.team_search_box.text().strip() or None
        teams = wb.search_teams(name_query=q, continent=cont, country_id=cid,
                                grade=grade, tier=tier, limit=300)
        _wt_t1 = _time_wt.perf_counter()
        teams.sort(key=lambda t: t["name"])
        _wt_t2 = _time_wt.perf_counter()

        self.team_list.clear()
        # [2026-08 재작성] 리그검색과 동일한 이유로 delegate 기반 동기 루프로.
        for tm in teams:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, tm["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, tm["name"])
            item.setData(_GridRowDelegate._SPEC_ROLE, self._team_row_spec(tm))
            self.team_list.addItem(item)
        _wt_t3 = _time_wt.perf_counter()
        self._ensure_list_fits(self.team_list, self._team_split)
        _wt_t4 = _time_wt.perf_counter()
        if _wt_t4 - _wt_t0 >= 0.03:
            print(f"[PERF-WB-TEAM] 총 {_wt_t4-_wt_t0:.3f}s — "
                  f"DB조회(search_teams,{len(teams)}건) {_wt_t1-_wt_t0:.3f}s | "
                  f"정렬 {_wt_t2-_wt_t1:.3f}s | 행채우기(delegate) {_wt_t3-_wt_t2:.3f}s | "
                  f"_ensure_list_fits {_wt_t4-_wt_t3:.3f}s")

    def _team_row_spec(self, tm):
        """[2026-08 신설] _team_row_widget과 동일한 칸 구성(팀명/등급/국가/
        소속리그)을 QWidget 없이 그릴 수 있는 스펙으로 표현."""
        return [
            {"text": tm["name"], "width": self._NAME_COL_W, "color": "#eee", "bold": True},
            {"text": f"{tm['grade']}급", "width": self._GRADE_COL_W,
             "color": _GRADE_COLORS.get(tm["grade"], "#888888"),
             "size": 11, "bold": True, "align": Qt.AlignmentFlag.AlignCenter},
            {"text": f"{tm['flag']} {tm['country']}", "width": self._COUNTRY_COL_W, "color": "#aaddff"},
            {"text": f"{tm['league_name']}({tm['tier']}부)", "width": self._LEAGUE_COL_W, "color": "#888"},
        ]

    def _team_row_widget(self, tm):
        """팀 목록 한 줄 — 왼쪽부터 [팀명(고정폭)] [등급] [국가] [소속리그(부수)]
        순서의 그리드. 리그 검색 탭(_league_row_widget)과 같은 이유로 같은
        방식 적용: 등급을 팀명 바로 옆으로 당기고, 칸마다 고정폭을 둬서
        표처럼 정렬되게 하고, 오른쪽 끝에 여백을 남긴다.
        [2026-07 신설, 신민용 확정] search_teams()가 teams.league_id를
        그대로 JOIN해서 조회하므로(별도 캐시 아님), 승격/강등이 일어나면
        다음에 이 목록을 새로고칠 때 자동으로 최신 소속·부수가 반영된다
        — 별도 갱신 로직이 필요 없다."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 6, 16, 6)
        h.setSpacing(10)

        h.addWidget(self._col_label(tm["name"], self._NAME_COL_W, color="#eee", bold=True))
        h.addWidget(self._grade_chip(tm["grade"], self._GRADE_COL_W))
        h.addWidget(self._col_label(f"{tm['flag']} {tm['country']}",
                                     self._COUNTRY_COL_W, color="#aaddff"))
        h.addWidget(self._col_label(f"{tm['league_name']}({tm['tier']}부)",
                                     self._LEAGUE_COL_W, color="#888"))
        h.addStretch(1)
        return row

    def _two_line_cell(self, main_text, main_color, record=None, bold=False):
        """[2026-08 신설, 신민용 요청: "팀 검색에서 리그뿐 아니라 국내컵/
        챔스/클럽월드컵도 각자 승무패를 아래에 보여달라"] 표 칸 하나에
        본문(위) + 그 대회의 전적(아래, 작은 회색 글씨)을 세로로 쌓는
        범용 위젯. record가 없으면(그 대회에 출전 안 한 해 등) 본문만.
        [2026-08 수정, 신민용 리포트: "클럽 대항전/클럽월드컵은 우승해도
        금색(#ffd700)으로 안 바뀐다. 다만 전체를 금색으로 바꾸면 원래
        대회색(파랑/주황/초록/하늘색)을 못 알아보니, 텍스트에 붙는
        '[우승]' 부분만 금색으로 강조하고 나머지는 main_color 그대로"]
        [2026-08 확장, 신민용 리포트: "리그도 마찬가지 — 하위 부수에서
        1등해서 승격(파랑)/강등 경계 경기 등으로 색이 이미 정해져 있어도,
        '[1등]' 부분만 금색으로 강조해달라. 1부는 승격 자체가 없어서
        이 조건이 안 걸리니(else 분기), 거기는 이미 칸 전체가 금색으로
        따로 처리된다"] '[우승]'과 '[1등]' 둘 다 본문에 있으면 그 부분만
        #ffd700으로 감싼 리치 텍스트(HTML)로 렌더링한다 — 대회/리그
        종류를 나타내는 나머지 색(승격=파랑/강등=빨강/챔스=파랑/유로파=
        주황/컨퍼런스=초록/클럽월드컵=하늘색)은 그대로 유지된다."""
        import html as _html
        import re as _re
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(1)
        main_lbl = QLabel()
        main_lbl.setWordWrap(True)
        _weight = "bold" if bold else "normal"
        # [2026-08 수정, 신민용 요청: "[1등] 뒤에 [1등/12팀]처럼 팀 수도
        # 붙게 해달라"] 예전엔 "[1등]" 고정 문자열을 그대로 찾아 금색으로
        # 감쌌는데, 팀 수가 붙으면서 길이가 리그마다(팀 수마다) 달라져
        # 고정 토큰으로는 더 이상 못 찾는다 — "[1등"으로 시작해서 그
        # 칸의 "]"까지를 통째로(팀 수 붙어있든 없든) 금색으로 감싸도록
        # 정규식으로 바꾼다. "[우승]"은 팀 수 개념이 없어 그대로 고정.
        _GOLD_PATTERN = _re.compile(r"\[우승\]|\[1등(?:/\d+팀)?\]")
        _gold_matches = list(_GOLD_PATTERN.finditer(main_text)) if main_text else []
        if _gold_matches:
            _parts, _pos = [], 0
            for _m in _gold_matches:
                if _m.start() > _pos:
                    _parts.append((main_text[_pos:_m.start()], main_color))
                _parts.append((_m.group(), "#ffd700"))
                _pos = _m.end()
            if _pos < len(main_text):
                _parts.append((main_text[_pos:], main_color))
            _rich = "".join(
                f'<span style="color:{c};">{_html.escape(t)}</span>' for t, c in _parts)
            main_lbl.setStyleSheet(f"font-weight:{_weight};font-size:12px;")
            main_lbl.setText(_rich)
        else:
            main_lbl.setStyleSheet(f"color:{main_color};font-weight:{_weight};font-size:12px;")
            main_lbl.setText(main_text)
        lay.addWidget(main_lbl)
        if record:
            rec_lbl = QLabel(record)
            rec_lbl.setStyleSheet("color:#888;font-size:10px;")
            lay.addWidget(rec_lbl)
        return w

    def _cl_award_summary_cell(self, n_cl, n_el, n_ecl):
        """[2026-08 신설, 신민용 확정: "클럽 대항전 수상 합계는 하나로
        합치지 않고 파랑(챔스)/주황(유로파)/초록(컨퍼런스) 숫자를 한 칸 씩
        띄워서 따로 보여준다, 0회인 대회는 생략"] '수상' 요약 행의 클럽
        대항전 칸 전용 — 셀 하나에 색이 다른 숫자를 최대 3개까지 나란히
        배치한다. 전부 0이면 완전히 빈 칸.
        [2026-08 버그수정, 신민용 리포트: "클럽 대항전 칸만 비어 보인다"]
        setCellWidget으로 넣는 QWidget은 옆 칸들(QTableWidgetItem +
        setBackground("#2a2a2a"))과 달리 배경을 안 넣으면 기본 배경(투명/
        회색)이 그대로 드러나서, 값이 있든 없든 그 칸만 튀어 보였다.
        같은 배경색을 명시해서 통일한다."""
        w = QWidget()
        w.setStyleSheet("background:#2a2a2a;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(8)
        lay.addStretch()
        for n, color in ((n_cl, "#1E4DB7"), (n_el, "#F28C28"), (n_ecl, "#20A464")):
            if not n:
                continue   # 0회인 대회는 통째로 생략(칸 자체를 안 만듦)
            lbl = QLabel(str(n))
            f = lbl.font(); f.setBold(True); lbl.setFont(f)
            lbl.setStyleSheet(f"color:{color};font-size:13px;")
            lay.addWidget(lbl)
        lay.addStretch()
        return w

    def _on_team_selected(self, item):
        tid = item.data(Qt.ItemDataRole.UserRole)
        tname = item.data(Qt.ItemDataRole.UserRole + 1)
        if tid is None:
            return
        self._show_team_detail(tid, tname)
        # [2026-08 신설] 최근 검색 기록 — "팀 검색" 목록에서 실제로 클릭해
        # 들어간 팀명만 남긴다(검색창에 타이핑한 문자열이 아니라).
        self._record_recent_selection("team", tname or "", "_team_recent_row")

    def _show_team_detail(self, tid, tname):
        """[2026-08 신설, 신민용 리포트: "대회 상세 화면(리그 스테이지/
        조별리그)에서 팀을 클릭해도 아무 반응이 없다 — 클릭 가능하게
        해달라"] 기존 _on_team_selected(팀 검색 리스트 클릭)가 하던 일을
        team_id 기준으로 바로 부를 수 있게 분리했다 — TournamentDetailDialog
        (대회 상세 팝업)에서 팀명 칸을 클릭하면 이 메서드를 통해 "팀 검색"
        탭으로 전환하고 그 팀 상세를 띄운다(open_team_by_id 참고)."""
        self.team_detail_title.setText(f"📋 {tname}  역대 기록")

        tbl = self.team_detail_tbl
        tbl.setRowCount(0)
        # [2026-08 신설] 복사 버튼이 지금 표에 그릴 데이터를 그대로 재사용할 수
        # 있도록(별도 재조회 없이) 팀 이름 + get_team_history 결과를 인스턴스에
        # 저장해둔다 — 아래에서 hist를 구한 뒤(다음 줄들) 최신 값으로 갱신한다.
        self._team_copy_name = tname
        # [2026-08 버그수정, 신민용 리포트: "표에서 칸 하나가 그 행 전체를
        # 뒤덮는 깨짐 현상"] 이 표도 아래에서 "기록 없음"일 때 setSpan을
        # 쓰는데, 이전 팀 선택 때 그 span이 남아있으면(clear는 span을
        # 안 지움) 다음 팀의 실제 데이터에도 그대로 씌워진다 — 팀을 바꿀
        # 때마다 무조건 먼저 지운다(_show_empty_state와 동일한 원인).
        tbl.clearSpans()
        hist = wb.get_team_history(tid)
        self._team_copy_hist = hist
        awards, years = hist["awards"], hist["years"]
        if not years and not any(awards.values()):
            self.team_copy_btn.setEnabled(False)
            tbl.setRowCount(1)
            empty = QTableWidgetItem("기록 없음")
            empty.setForeground(QColor("#666"))
            tbl.setItem(0, 0, empty)
            tbl.setSpan(0, 0, 1, 5)
            return
        self.team_copy_btn.setEnabled(True)

        # [2026-08 신설, 신민용 요청: "연도별 기록 맨 위에 '수상' 칸을 만들어
        # 리그/컵/챔스/클럽WC 우승 횟수를 보여달라, 0회면 빈칸으로"]
        # [2026-08 확장, 신민용 확정: "클럽 대항전 수상은 하나로 합치지
        # 않고 왼쪽부터 파랑(챔스) 한 칸 띄고 주황(유로파) 한 칸 띄고
        # 초록(컨퍼런스), 0회인 대회는 그 자체를 생략"] 클럽 대항전 칸
        # 안에서 여러 색 숫자를 한 셀에 같이 넣어야 해서, 이 칸만
        # _two_line_cell이 아니라 직접 QLabel들을 가로로 배치한 위젯을 쓴다.
        tbl.setRowCount(len(years) + 1)
        award_labels = [
            ("수상", None),
            (str(awards["league"]) if awards["league"] else "", "#4da6ff"),
            (str(awards["cup"]) if awards["cup"] else "", "#c48aff"),
        ]
        for j, (text, color) in enumerate(award_labels):
            cell = QTableWidgetItem(text)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            f = cell.font(); f.setBold(True); cell.setFont(f)
            cell.setForeground(QColor(color) if color else QColor("#ffcc00"))
            cell.setBackground(QColor("#2a2a2a"))
            tbl.setItem(0, j, cell)

        # 클럽 대항전 수상 칸(3번 컬럼) — 파랑/주황/초록 숫자를 한 칸에 같이.
        tbl.setCellWidget(0, 3, self._cl_award_summary_cell(
            awards.get("cl_champions", 0), awards.get("el_champions", 0),
            awards.get("ecl_champions", 0)))

        cwc_cell = QTableWidgetItem(str(awards["cwc"]) if awards["cwc"] else "")
        cwc_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        f = cwc_cell.font(); f.setBold(True); cwc_cell.setFont(f)
        cwc_cell.setForeground(QColor("#4dd0e1"))
        cwc_cell.setBackground(QColor("#2a2a2a"))
        tbl.setItem(0, 4, cwc_cell)

        for i, entry in enumerate(years, start=1):
            # [2026-08 신설, 신민용 요청: "리그뿐 아니라 국내컵/챔스/클럽
            # 월드컵도 각자 승무패가 있으니 그것도 각 칸 아래에 보여달라"]
            # 국가 검색 탭(country_detail_tbl)처럼 "상세기록"을 별도 컬럼
            # 으로 오른쪽에 두는 대신, 각 대회 칸 안에 그 대회 결과 + 그
            # 대회 전적을 세로로 쌓는다(대회마다 전적이 다르므로 칸마다
            # 각자의 record를 넣는다) — _two_line_cell 참고.
            year_item = QTableWidgetItem(str(entry["year"]))
            year_item.setForeground(QColor("#ffcc00"))
            f = year_item.font(); f.setBold(True); year_item.setFont(f)
            tbl.setItem(i, 0, year_item)

            lg_txt = entry["league"] or "-"
            # [2026-08 신설, 신민용 확정: "승격색이 우선, 1부 1등만 금색"]
            # 우선순위: 승격(파랑) > 강등(빨강) > 1부 우승(금색) > 그 외(회백).
            lg_txt_l = lg_txt
            if "승격" in lg_txt_l:
                lg_color = "#4da6ff"
            elif "강등" in lg_txt_l:
                lg_color = "#ff5555"
            elif entry.get("league_champion"):
                lg_color = "#ffd700"
            else:
                lg_color = "#ddd"
            tbl.setCellWidget(i, 1, self._two_line_cell(lg_txt, lg_color, entry.get("league_record")))

            cup_txt = entry["cup"] or "-"
            # [2026-08 수정, 신민용 리포트: "국내컵도 우승해도 전체를
            # 금색으로 바꾸지 말고 [우승]만 금색, 컵 이름은 원래 보라색
            # 유지"] CL/CWC와 동일한 패턴 — 본문색은 항상 국내컵 고유색
            # (보라)/미출전(회색)으로 두고, "[우승]"만 _two_line_cell이
            # 자동으로 금색 강조한다.
            cup_color = "#c48aff" if entry["cup"] else "#555"
            tbl.setCellWidget(i, 2, self._two_line_cell(cup_txt, cup_color, entry.get("cup_record")))

            cl_txt = entry["cl"] or "-"
            # [2026-08 신설, 신민용 확정: "클럽 대항전"으로 통합 — 챔스는
            # 파랑(#1E4DB7), 유로파는 주황(#F28C28), 컨퍼런스는 초록
            # (#20A464). 워터폴 구조상 한 해엔 하나만 걸리므로 cl_kind
            # 하나로 색이 딱 정해진다(참가 자체가 없으면 회색).
            _CL_KIND_COLOR = {"champions": "#1E4DB7", "europa": "#F28C28", "conference": "#20A464"}
            cl_color = _CL_KIND_COLOR.get(entry.get("cl_kind"), "#555") if entry["cl"] else "#555"
            tbl.setCellWidget(i, 3, self._two_line_cell(cl_txt, cl_color, entry.get("cl_record")))

            cwc_txt = entry.get("cwc") or "-"
            # [2026-08 수정] 우승해도 전체를 금색으로 바꾸지 않는다 —
            # 본문색은 항상 클럽월드컵 고유색(하늘색)/미출전(회색)으로
            # 유지하고, "[우승]" 부분만 _two_line_cell이 자동으로 금색
            # 강조한다.
            cwc_color = "#4dd0e1" if entry.get("cwc") else "#555"
            tbl.setCellWidget(i, 4, self._two_line_cell(cwc_txt, cwc_color, entry.get("cwc_record")))
        self._finalize_team_detail_row_heights(tbl)

    # [2026-08 버그수정, 신민용 리포트: "팀 검색에서 승격/강등 문구처럼
    # 긴 텍스트가 있는 줄은 아래쪽 글자가 살짝 잘려 보인다 — game.db를
    # 새로 만들어야 하는 거냐?"] game.db와는 무관한 순수 화면(Qt) 문제다.
    # 이 표의 1~4번 칸은 Stretch 리사이즈 모드라(_build_team_tab) 실제 칸
    # 너비가 레이아웃이 끝나야 확정되는데, resizeRowsToContents()는 그
    # 전에(칸이 아직 좁은 스냅샷 기준) 각 _two_line_cell의 줄바꿈을 재서
    # 행 높이를 계산해버린다 — QTimer.singleShot(0, …)으로 한 프레임
    # 뒤에 다시 불러도, 폰트 렌더링 반올림 오차로 1~2px가 여전히 모자란
    # 경우가 남아있었다(신민용이 스크린샷으로 재확인). 그래서 이제는
    # "정확히 딱 맞추기"를 포기하고, 재계산 후 모든 행에 여유 높이를
    # 몇 px 더 얹어서 — 오차가 몇 px든 항상 남는 여백이 그걸 흡수하게
    # 한다(약간 헐렁해 보일 순 있어도 잘리는 것보단 낫다는 원칙).
    def _finalize_team_detail_row_heights(self, tbl):
        _ROW_HEIGHT_PAD = 6

        def _pad():
            tbl.resizeRowsToContents()
            for r in range(tbl.rowCount()):
                tbl.setRowHeight(r, tbl.rowHeight(r) + _ROW_HEIGHT_PAD)

        tbl.resizeRowsToContents()
        QTimer.singleShot(0, _pad)

    # [2026-08 신설, 신민용 요청: "팀 검색에 복사하기 버튼을 만들어서
    # 누르면 이 팀의 연도별 기록을 텍스트로 뽑고 싶다 — 지피티나 제미나이가
    # 알아들을 수 있는 형태로"] 화면 표(team_detail_tbl)와 같은 데이터
    # (self._team_copy_hist)를 사람이 다시 옮겨 적을 필요 없이, LLM에
    # 그대로 붙여넣어도 되는 평문 텍스트로 바꿔 클립보드에 복사한다.
    def _on_copy_team_history_clicked(self):
        hist = getattr(self, "_team_copy_hist", None)
        tname = getattr(self, "_team_copy_name", None)
        if not hist or not tname:
            return
        text = self._format_team_history_text(tname, hist)
        QGuiApplication.clipboard().setText(text)

        # 눌렀을 때 복사됐다는 걸 눈으로 확인할 수 있게 버튼 라벨을
        # 잠깐 바꿨다가 되돌린다(1.2초). 다른 팀을 고르는 등으로 버튼이
        # 다시 그려지면(= 이 위젯이 없어지는 게 아니라 그냥 다음 클릭까지
        # 남아있으므로) 딱히 꼬일 일은 없다 — QTimer.singleShot이 그
        # 시점에 라벨만 원래대로 되돌린다.
        self.team_copy_btn.setText("✅ 복사됨")
        QTimer.singleShot(1200, lambda: self.team_copy_btn.setText("📋 기록 복사"))

    def _format_team_history_text(self, tname, hist):
        """hist(get_team_history 반환값)를 사람이 읽어도, LLM에 그대로
        붙여넣어도 되는 평문으로 직렬화한다. 화면 표와 같은 정보(연도별
        리그/국내컵/클럽대항전/클럽월드컵 결과 + 각자 전적, 맨 위 통산
        수상 집계)를 담되, 색상 대신 "[승격]"/"[강등]"/"[우승]" 같은
        텍스트 표기만으로 뜻이 통하게 한다(이미 entry 문자열 안에 이런
        표기가 들어있으므로 대부분 그대로 옮기면 된다).
        """
        awards, years = hist["awards"], hist["years"]
        lines = [f"[{tname} 역대 기록]"]

        award_bits = []
        if awards.get("league"):
            award_bits.append(f"리그 우승 {awards['league']}회")
        if awards.get("cup"):
            award_bits.append(f"국내컵 우승 {awards['cup']}회")
        if awards.get("cl_champions"):
            award_bits.append(f"챔피언스리그(급) 우승 {awards['cl_champions']}회")
        if awards.get("el_champions"):
            award_bits.append(f"유로파리그(급) 우승 {awards['el_champions']}회")
        if awards.get("ecl_champions"):
            award_bits.append(f"컨퍼런스리그(급) 우승 {awards['ecl_champions']}회")
        if awards.get("cwc"):
            award_bits.append(f"클럽 월드컵 우승 {awards['cwc']}회")
        lines.append("통산 수상: " + (" · ".join(award_bits) if award_bits else "없음"))
        lines.append("")

        if not years:
            lines.append("(연도별 기록 없음)")
        else:
            for entry in years:
                parts = [f"{entry['year']}년"]
                if entry.get("league"):
                    rec = f" ({entry['league_record']})" if entry.get("league_record") else ""
                    parts.append(f"리그: {entry['league']}{rec}")
                if entry.get("cup"):
                    rec = f" ({entry['cup_record']})" if entry.get("cup_record") else ""
                    parts.append(f"국내컵: {entry['cup']}{rec}")
                if entry.get("cl"):
                    rec = f" ({entry['cl_record']})" if entry.get("cl_record") else ""
                    parts.append(f"클럽대항전: {entry['cl']}{rec}")
                if entry.get("cwc"):
                    rec = f" ({entry['cwc_record']})" if entry.get("cwc_record") else ""
                    parts.append(f"클럽월드컵: {entry['cwc']}{rec}")
                lines.append(" | ".join(parts))

        return "\n".join(lines)

    # ─────────────────────────────────────────
    # 탭1.6: 국가 검색 (2026-08 신설, 신민용 확정: "월드컵/대륙컵 우승
    # 기록실". 팀 검색 탭(_build_team_tab)과 거의 같은 UX — 대륙/등급
    # 필터 + 검색창 + 좌측 목록/우측 상세. 트로피 집계는 world_browser의
    # get_country_trophy_summary/get_country_title_list가 intl_tournaments.
    # kind로 GROUP BY 하므로, 나중에 국제대회 종류가 늘어나도(새 kind 값)
    # 이 탭은 코드 수정 없이 자동으로 새 대회 종류를 집계·표시한다 —
    # 예쁜 한글 라벨만 constants.INTL_TOURNAMENT_KIND_LABELS에 추가하면 됨.
    # ─────────────────────────────────────────
    def _build_country_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        info = QLabel("ℹ️ 국가 하나를 골라 월드컵·대륙컵 등 국제대회 우승 기록을 확인하세요.")
        info.setStyleSheet("color:#888;font-size:11px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        filt = QHBoxLayout()
        filt.setSpacing(8)
        lbl1 = QLabel("대륙"); lbl1.setStyleSheet("color:#888;font-size:11px;")
        self.country_cont_combo = QComboBox()
        self.country_cont_combo.addItem(_ALL)
        for cont in wb.list_continents():
            self.country_cont_combo.addItem(cont)
        self.country_cont_combo.currentTextChanged.connect(self._refresh_country_search_list)
        filt.addWidget(lbl1)
        filt.addWidget(self.country_cont_combo)

        lbl2 = QLabel("등급"); lbl2.setStyleSheet("color:#888;font-size:11px;")
        self.country_grade_combo = QComboBox()
        self.country_grade_combo.addItem(_ALL)
        # [2026-08 신설, 신민용 요청: "국가 검색 등급 필터는 국가대표
        # 등급으로"] 리그/팀 검색 탭과 달리 이 탭은 국제대회 우승 기록을
        # 보여주는 화면이라 클럽 리그 등급이 아니라 국가대표 등급(FIFA
        # 랭킹 기반) 기준으로 목록을 채운다 — wb.search_countries()도
        # 기본값이 grade_type="national"로 맞춰져 있어 실제 필터링도
        # 이 목록과 같은 기준을 쓴다.
        for g in wb.list_grades(grade_type="national"):
            self.country_grade_combo.addItem(g)
        self.country_grade_combo.currentTextChanged.connect(self._refresh_country_search_list)
        filt.addWidget(lbl2)
        filt.addWidget(self.country_grade_combo)

        # [2026-08 신설, 신민용 요청] 트로피 유무 필터 — 등급 필터 바로
        # 뒤에 위치. "전체 / 상 있는 국가 / 상 없는 국가" 3분류.
        lbl3 = QLabel("수상"); lbl3.setStyleSheet("color:#888;font-size:11px;")
        self.country_trophy_combo = QComboBox()
        self.country_trophy_combo.addItem(_ALL)
        self.country_trophy_combo.addItem("상 있는 국가")
        self.country_trophy_combo.addItem("상 없는 국가")
        self.country_trophy_combo.currentTextChanged.connect(self._refresh_country_search_list)
        filt.addWidget(lbl3)
        filt.addWidget(self.country_trophy_combo)

        # [2026-08 신설, 신민용 요청: "대륙컵/유로/월드컵/지역컵으로도 필터
        # 하나 더 만들어달라"] "수상" 필터(있음/없음)와 별개 축 — 이건
        # "어떤 종류의 대회에서 상을 받았는가"로 좁힌다. "전체"가 아니면
        # 선택한 종류의 우승 기록이 하나라도 있는 국가만 남긴다(수상
        # 필터와 동시에 걸면 둘 다 만족하는 국가만).
        lbl4 = QLabel("대회"); lbl4.setStyleSheet("color:#888;font-size:11px;")
        self.country_trophy_kind_combo = QComboBox()
        self.country_trophy_kind_combo.addItem(_ALL)
        for _label, _ek in wb.COUNTRY_TROPHY_KIND_OPTIONS:
            self.country_trophy_kind_combo.addItem(_label)
        self.country_trophy_kind_combo.currentTextChanged.connect(self._refresh_country_search_list)
        filt.addWidget(lbl4)
        filt.addWidget(self.country_trophy_kind_combo)

        # [2026-08 신설, 신민용 요청: "1등만 필터되는데 1~4등까지 나눠서
        # 필터하고 싶다, 4등을 얻은 국가가 어디인지 필터되게"] "수상"/"대회"
        # 필터와 별개 축 — 이건 "어느 순위까지 올라간 적 있는가"로 좁힌다.
        # "대회" 필터(우승 기록 유무)와 달리 준우승/3위/4위도 잡아낸다.
        lbl5 = QLabel("순위"); lbl5.setStyleSheet("color:#888;font-size:11px;")
        self.country_rank_combo = QComboBox()
        self.country_rank_combo.addItem(_ALL)
        for _label, _rank in wb.COUNTRY_PLACEMENT_RANK_OPTIONS:
            self.country_rank_combo.addItem(_label)
        self.country_rank_combo.currentTextChanged.connect(self._refresh_country_search_list)
        filt.addWidget(lbl5)
        filt.addWidget(self.country_rank_combo)

        self.country_search_box = QLineEdit()
        self.country_search_box.setPlaceholderText("🔎 국가명 검색")
        self._country_search_debounce = QTimer(self)
        self._country_search_debounce.setSingleShot(True)
        self._country_search_debounce.setInterval(250)
        self._country_search_debounce.timeout.connect(self._refresh_country_search_list)
        self.country_search_box.textChanged.connect(lambda _text: self._country_search_debounce.start())
        filt.addWidget(self.country_search_box, 1)
        lay.addLayout(filt)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._country_split = split
        self.country_list = QListWidget()
        self.country_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.country_list.itemClicked.connect(self._on_country_selected)
        self.country_list.setItemDelegate(_GridRowDelegate(self, self.country_list))
        country_header = self._list_header_row([
            ("국가", self._NAME_COL_W, False),
            ("등급", self._GRADE_COL_W, True),
            ("대륙", self._COUNTRY_COL_W, False),
            ("우승 기록", self._TROPHY_COL_W, False),
        ])
        split.addWidget(self._wrap_list_with_header(self.country_list, country_header))

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(10, 0, 0, 0)
        self._country_recent_row = self._build_recent_search_row(
            "country", self.country_search_box, self.country_list,
            lambda it: it.data(Qt.ItemDataRole.UserRole),
            self._on_country_selected)
        right_lay.addWidget(self._country_recent_row)
        title_row = QHBoxLayout()
        self.country_detail_title = QLabel("← 왼쪽에서 국가를 선택하세요")
        self.country_detail_title.setStyleSheet("color:#00cc44;font-size:14px;font-weight:bold;")
        title_row.addWidget(self.country_detail_title, 1)
        # [2026-08 신설, 신민용 요청: "팀 검색에 만든 것처럼 국가 검색에도
        # 모든 국가에 복사 버튼을 만들어달라"] 팀 탭의 team_copy_btn과 완전히
        # 같은 패턴 — 화면 표(country_detail_tbl)와 같은 데이터를 LLM에
        # 붙여넣기 좋은 평문으로 클립보드에 복사한다.
        self.country_copy_btn = QPushButton("📋 기록 복사")
        self.country_copy_btn.setEnabled(False)
        self.country_copy_btn.setToolTip("이 국가의 국제대회 우승/참가 기록을 텍스트로 복사합니다(GPT/제미나이 등에 붙여넣기용)")
        self.country_copy_btn.clicked.connect(self._on_copy_country_history_clicked)
        title_row.addWidget(self.country_copy_btn)
        right_lay.addLayout(title_row)

        # kind별 우승 횟수 요약 칩(예: 🌐 월드컵 2회  🎖 대륙컵 3회) — 대회
        # 종류가 몇 개든(현재 2종, 앞으로 늘어나도) 가로로 쭉 붙여서 보여준다.
        self.country_summary_row = QHBoxLayout()
        self.country_summary_row.setSpacing(14)
        summary_wrap = QWidget()
        summary_wrap.setLayout(self.country_summary_row)
        right_lay.addWidget(summary_wrap)

        # [2026-08 신설, 신민용 요청: "우측 기록에 종류(전체/지역컵/월드컵/
        # 네이션스컵) 필터를 만들어서, 선택하면 그 종류만 뜨게 해달라 —
        # 예선은 그 본선이랑 같이 묶이고, 유로는 지역컵으로 묶여야 한다"]
        # 이미 있는 상단 "대회" 필터(country_trophy_kind_combo)는 좌측
        # 국가 "목록"을 좁히는 필터라 이거랑 별개다 — 이건 선택된 국가
        # 한 명의 우측 상세 기록만 좁힌다. 매번 새로 쿼리하지 않고
        # _on_country_selected가 캐시해둔 self._country_all_results를
        # 로컬에서 effective_kind로 다시 걸러 표만 새로 그린다.
        result_filt = QHBoxLayout()
        result_filt.setSpacing(8)
        rf_lbl = QLabel("종류"); rf_lbl.setStyleSheet("color:#888;font-size:11px;")
        self.country_result_kind_combo = QComboBox()
        self.country_result_kind_combo.addItem(_ALL, None)
        self.country_result_kind_combo.addItem("지역컵", "region_group")
        self.country_result_kind_combo.addItem("월드컵", "world_group")
        self.country_result_kind_combo.addItem("네이션스컵", "continent_group")
        self.country_result_kind_combo.currentIndexChanged.connect(
            self._refresh_country_detail_table)
        result_filt.addWidget(rf_lbl)
        result_filt.addWidget(self.country_result_kind_combo)
        result_filt.addStretch()
        right_lay.addLayout(result_filt)

        self.country_detail_tbl = QTableWidget(0, 5)
        self.country_detail_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.country_detail_tbl.verticalHeader().setVisible(False)
        self.country_detail_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.country_detail_tbl.setHorizontalHeaderLabels(["연도", "대회", "종류", "결과", "상세기록"])
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents)
        self.country_detail_tbl.cellDoubleClicked.connect(self._open_country_title_detail)
        right_lay.addWidget(self.country_detail_tbl, 1)
        hint = QLabel("💡 우승 기록을 더블클릭하면 그 대회 상세를 볼 수 있어요")
        hint.setStyleSheet("color:#666;font-size:10px;")
        right_lay.addWidget(hint)

        split.addWidget(right)
        split.setSizes([440, 900])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        self._refresh_country_search_list()
        return w

    def _refresh_country_search_list(self, *_a):
        cont = None if self.country_cont_combo.currentText() == _ALL else self.country_cont_combo.currentText()
        grade = None if self.country_grade_combo.currentText() == _ALL else self.country_grade_combo.currentText()
        trophy_filter = self.country_trophy_combo.currentText()
        kind_filter_label = self.country_trophy_kind_combo.currentText()
        q = self.country_search_box.text().strip() or None
        countries = wb.search_countries(name_query=q, continent=cont, grade=grade)
        trophy_counts = wb.get_all_countries_trophy_counts()

        # [2026-08 신설] 수상 유무 필터 — trophy_counts에 그 국가 항목
        # 자체가 없거나 있어도 전부 0회면 "상 없는 국가"로 취급.
        if trophy_filter != _ALL:
            has_any = lambda name: bool(trophy_counts.get(name))
            want_has_trophy = (trophy_filter == "상 있는 국가")
            countries = [cn for cn in countries if has_any(cn["name"]) == want_has_trophy]

        # [2026-08 신설, 신민용 요청] 대회 종류 필터 — 선택한 종류(월드컵/
        # 유로/대륙컵/지역컵) 우승 기록이 하나라도 있는 국가만 남긴다.
        if kind_filter_label != _ALL:
            _ek_by_label = dict(wb.COUNTRY_TROPHY_KIND_OPTIONS)
            want_ek = _ek_by_label.get(kind_filter_label)
            has_kind = lambda name: any(
                g["effective_kind"] == want_ek for g in (trophy_counts.get(name) or []))
            countries = [cn for cn in countries if has_kind(cn["name"])]

        # [2026-08 신설, 신민용 요청: "1~4등 필터로 4등을 얻은 국가를 찾고
        # 싶다"] "대회" 필터와 독립적인 축 — 선택한 순위(1~4위)에 도달한
        # 적이 있는 국가만 남긴다. 우승(1위) 외 순위는 winner 컬럼만으로는
        # 못 잡아서 get_all_countries_placement_counts()(결승/3·4위전
        # 매치까지 보는 별도 집계)를 쓴다.
        rank_filter_label = self.country_rank_combo.currentText()
        if rank_filter_label != _ALL:
            _rank_by_label = dict(wb.COUNTRY_PLACEMENT_RANK_OPTIONS)
            want_rank = _rank_by_label.get(rank_filter_label)
            placement_counts = wb.get_all_countries_placement_counts()
            countries = [cn for cn in countries
                         if want_rank in (placement_counts.get(cn["name"]) or {})]

        self.country_list.clear()
        for cn in countries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, cn["name"])
            item.setData(_GridRowDelegate._SPEC_ROLE,
                         self._country_row_spec(cn, trophy_counts.get(cn["name"]) or []))
            self.country_list.addItem(item)
        self._ensure_list_fits(self.country_list, self._country_split)

    def _trophy_chip_text(self, groups: list) -> str:
        """[{kind,name,n,effective_kind}, ...] → "🌐2 ⚡1 🎖3" 같은 짧은
        요약 문자열(국가 리스트의 좁은 컬럼용이라 대회명 대신 아이콘만).
        같은 effective_kind가 여러 개(이론상 없어야 하지만 방어적으로)면
        합산한다. 라벨이 없는(=미래에 추가된) kind는
        INTL_TOURNAMENT_KIND_FALLBACK_LABEL(🏆)로 표시돼서 코드 수정
        없이도 안 깨지고 보인다."""
        from constants import INTL_TOURNAMENT_KIND_GLYPHS, INTL_TOURNAMENT_KIND_FALLBACK_LABEL
        if not groups:
            return "-"
        totals = {}
        for g in groups:
            totals[g["effective_kind"]] = totals.get(g["effective_kind"], 0) + g["n"]
        parts = []
        for ek, n in sorted(totals.items(), key=lambda kv: -kv[1]):
            glyph = INTL_TOURNAMENT_KIND_GLYPHS.get(ek, INTL_TOURNAMENT_KIND_FALLBACK_LABEL)
            parts.append(f"{glyph}{n}")
        return " ".join(parts)

    def _country_row_spec(self, cn, groups: list):
        return [
            {"text": f"{cn['flag']} {cn['name']}", "width": self._NAME_COL_W,
             "color": "#eee", "bold": True},
            {"text": f"{cn['grade']}급", "width": self._GRADE_COL_W,
             "color": _GRADE_COLORS.get(cn["grade"], "#888888"),
             "size": 11, "bold": True, "align": Qt.AlignmentFlag.AlignCenter},
            {"text": cn["continent"], "width": self._COUNTRY_COL_W, "color": "#aaddff"},
            {"text": self._trophy_chip_text(groups), "width": self._TROPHY_COL_W,
             "color": "#ffcc00" if groups else "#555"},
        ]

    def _on_country_selected(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        self.country_detail_title.setText(f"🌍 {name}  국제대회 우승 기록")
        self._country_copy_name = name
        # [2026-08 신설] 최근 검색 기록 — "국가 검색" 목록에서 실제로
        # 클릭해 들어간 국가명만 남긴다.
        self._record_recent_selection("country", name, "_country_recent_row")

        # 요약 칩 갱신
        while self.country_summary_row.count():
            child = self.country_summary_row.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        summary = wb.get_country_trophy_summary(name)
        self._country_copy_summary = summary
        if not summary:
            empty = QLabel("아직 우승한 국제대회가 없습니다")
            empty.setStyleSheet("color:#666;font-size:12px;")
            self.country_summary_row.addWidget(empty)
        else:
            for s in summary:
                chip = QLabel(f"{s['label']} {s['titles']}회")
                chip.setStyleSheet(
                    "background:#2a2a2a;color:#ffcc00;font-size:12px;font-weight:bold;"
                    "padding:4px 10px;border-radius:8px;")
                self.country_summary_row.addWidget(chip)
        self.country_summary_row.addStretch(1)

        # 연도별 전체 성적(우승~조별탈락) — [2026-08] 우승만 보여주던 것에서
        # get_country_tournament_results()로 교체, 결과(몇강 탈락) 컬럼 추가.
        results = wb.get_country_tournament_results(name)
        self._country_all_results = results
        self._country_copy_results = results
        self.country_copy_btn.setEnabled(bool(results) or bool(summary))
        self._refresh_country_detail_table()

    # [2026-08 신설, 신민용 요청: "국가 검색 우측 기록에 종류(전체/지역컵/
    # 월드컵/네이션스컵) 필터를 만들어달라 — 예선은 그 본선이랑 같이
    # 묶이고, 유로는 지역컵으로 묶여야 한다"] effective_kind(이미
    # get_country_tournament_results가 예선/유로까지 정확히 구분해서
    # 계산해둔 값)로 세 그룹을 나눈다:
    #   지역컵 그룹  = region(지역컵) + euro(유로 본선) + euro_qual(유로 예선)
    #   월드컵 그룹  = world(월드컵 본선) + wc_qual(월드컵 예선)
    #   네이션스컵 그룹 = continent(대륙컵/네이션스컵 본선, 유로 제외) + cont_qual(그 예선)
    # 화면 표기(종류/대회명 칸)는 그대로 두고 — 이 필터는 어떤 행을
    # "보여줄지"만 결정한다, "어떻게 보일지"는 안 바꾼다.
    _COUNTRY_RESULT_KIND_GROUPS = {
        "region_group": {"region", "euro", "euro_qual"},
        "world_group": {"world", "wc_qual"},
        "continent_group": {"continent", "cont_qual"},
    }

    def _refresh_country_detail_table(self, *_a):
        results = getattr(self, "_country_all_results", None) or []
        group_key = self.country_result_kind_combo.currentData()
        if group_key:
            allowed = self._COUNTRY_RESULT_KIND_GROUPS.get(group_key, set())
            results = [t for t in results if t.get("effective_kind") in allowed]

        _TIER_COLORS = {5: "#ffd700", 4: "#c0c0c0", 3: "#cd7f32",
                         2: "#aaddff", 1: "#999999", 0: "#555555"}
        tbl = self.country_detail_tbl
        tbl.setRowCount(len(results))
        from constants import INTL_TOURNAMENT_KIND_LABELS
        for i, t in enumerate(results):
            # [2026-08 방어코드, 신민용 리포트: "연도만 뜨고 나머지가 텅 빔"]
            # PyQt의 QTableWidgetItem(str)은 None이 들어오면 예외를 던지는데,
            # 이 예외가 슬롯 안에서 조용히 삼켜지면서(콘솔에만 트레이스백)
            # 그 시점 이후 컬럼이 전부 미설정 상태로 남는 증상이 있었다.
            # world_browser.get_country_tournament_results()에서 이미
            # None을 걸러주지만, 혹시 모를 다른 원인(레거시 데이터 등)에도
            # 안전하도록 한 행씩 try/except로 감싼다 — 한 행이 이상해도
            # 그 행만 건너뛰고 나머지 행은 정상 표시되게.
            try:
                year_item = QTableWidgetItem(str(t["year"]))
                year_item.setForeground(QColor("#ffcc00"))
                f = year_item.font(); f.setBold(True); year_item.setFont(f)
                year_item.setData(Qt.ItemDataRole.UserRole, t["id"])
                year_item.setData(Qt.ItemDataRole.UserRole + 1, t["kind"])
                tbl.setItem(i, 0, year_item)

                name_item = QTableWidgetItem(str(t["name"]) if t["name"] else "-")
                tbl.setItem(i, 1, name_item)

                kind_label = INTL_TOURNAMENT_KIND_LABELS.get(
                    t.get("effective_kind", t["kind"]), t["kind"])
                kind_item = QTableWidgetItem(str(kind_label) if kind_label else "-")
                tbl.setItem(i, 2, kind_item)

                result_item = QTableWidgetItem(str(t["result"]) if t["result"] else "-")
                result_item.setForeground(QColor(_TIER_COLORS.get(t["tier"], "#999999")))
                rf = result_item.font()
                rf.setBold(t["tier"] >= 3)
                result_item.setFont(rf)
                tbl.setItem(i, 3, result_item)

                record_item = QTableWidgetItem(str(t.get("record") or "-"))
                record_item.setForeground(QColor("#aaaaaa"))
                tbl.setItem(i, 4, record_item)
            except Exception as e:
                print(f"[국제대회기록] {t.get('year')}년 행 렌더링 오류(건너뜀): {e}")
        self._show_empty_state(tbl, results, "참가 기록 없음", 5)
        tbl.resizeRowsToContents()

    # [2026-08 신설, 신민용 요청: "국가 검색에도 팀 검색처럼 복사 버튼을
    # 만들어달라"] _on_copy_team_history_clicked/_format_team_history_text와
    # 완전히 같은 목적·패턴 — 다만 대상이 팀의 시즌별 성적이 아니라 국가의
    # 국제대회(월드컵/대륙컵/지역컵 등) 우승·참가 기록이라는 점만 다르다.
    def _on_copy_country_history_clicked(self):
        name = getattr(self, "_country_copy_name", None)
        results = getattr(self, "_country_copy_results", None)
        summary = getattr(self, "_country_copy_summary", None)
        if not name or (not results and not summary):
            return
        text = self._format_country_history_text(name, summary, results)
        QGuiApplication.clipboard().setText(text)

        self.country_copy_btn.setText("✅ 복사됨")
        QTimer.singleShot(1200, lambda: self.country_copy_btn.setText("📋 기록 복사"))

    def _format_country_history_text(self, name, summary, results):
        """국가 상세 화면(요약 칩 + 연도별 결과표)과 같은 데이터를 LLM에
        그대로 붙여넣어도 되는 평문으로 직렬화한다. 팀 쪽
        (_format_team_history_text)과 같은 구조 — 맨 위에 대회 종류별
        우승 횟수 요약, 그 아래 연도 내림차순 목록."""
        from constants import INTL_TOURNAMENT_KIND_LABELS
        lines = [f"[{name} 국제대회 우승 기록]"]

        if summary:
            bits = [f"{s['label']} {s['titles']}회" for s in summary]
            lines.append("통산 우승: " + " · ".join(bits))
        else:
            lines.append("통산 우승: 없음")
        lines.append("")

        if not results:
            lines.append("(참가 기록 없음)")
        else:
            for t in results:
                try:
                    tname_ = t.get("name") or "-"
                    kind_label = INTL_TOURNAMENT_KIND_LABELS.get(
                        t.get("effective_kind", t.get("kind")), t.get("kind") or "-")
                    result = t.get("result") or "-"
                    rec = t.get("record")
                    rec_txt = f" ({rec})" if rec else ""
                    lines.append(f"{t.get('year')}년 | {tname_} [{kind_label}] : {result}{rec_txt}")
                except Exception:
                    continue

        return "\n".join(lines)

    def _open_country_title_detail(self, row, _col):
        item = self.country_detail_tbl.item(row, 0)
        tid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tid is None:
            return
        wc = item.data(Qt.ItemDataRole.UserRole + 1) == "world"
        self._open_intl_detail(self.country_detail_tbl, row, wc=wc)

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

        # [2026-08 신설, 신민용 요청: "컵대회에도 대륙만 있는게 아니라
        # 등급 필터도 만들어달라"] 컵대회는 클럽 대항전이라 국가 검색
        # 탭과 달리 클럽 리그 등급(list_grades 기본값)을 쓴다.
        lbl_grade = QLabel("등급"); lbl_grade.setStyleSheet("color:#888;font-size:11px;")
        self.cup_grade_combo = QComboBox()
        self.cup_grade_combo.addItem(_ALL)
        for g in wb.list_grades():
            self.cup_grade_combo.addItem(g)
        self.cup_grade_combo.currentTextChanged.connect(self._refresh_cup_country_list)
        filt.addWidget(lbl_grade)
        filt.addWidget(self.cup_grade_combo)

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
        self.cup_country_list.setItemDelegate(_GridRowDelegate(self, self.cup_country_list))
        cup_header = self._list_header_row([
            ("나라명", self._NAME_COL_W, False),
            ("등급", 70, True),
        ])
        split.addWidget(self._wrap_list_with_header(self.cup_country_list, cup_header))

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(10, 0, 0, 0)

        title_row = QHBoxLayout()
        self.cup_title = QLabel("← 왼쪽에서 나라를 선택하세요")
        self.cup_title.setStyleSheet("color:#c48aff;font-size:14px;font-weight:bold;")
        title_row.addWidget(self.cup_title, 1)
        # [2026-08 신설, 신민용 요청: "컵대회에도 역대 1~4위를 가장 많이
        # 차지한 팀 순위를 보여달라"] 나라별로 이미 좌측에서 선택하는
        # 구조라 별도 필터는 필요 없다 — 지금 선택된 나라 기준으로 바로
        # 집계한다.
        self.cup_rank_btn = QPushButton("🥇 최다 순위")
        self.cup_rank_btn.setEnabled(False)
        self.cup_rank_btn.clicked.connect(self._on_cup_rank_leaders_clicked)
        title_row.addWidget(self.cup_rank_btn)
        right_lay.addLayout(title_row)

        self.cup_sub = QLabel("")
        self.cup_sub.setStyleSheet("color:#888;font-size:11px;")
        right_lay.addWidget(self.cup_sub)

        self.cup_tbl = QTableWidget(0, 0)
        self.cup_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cup_tbl.verticalHeader().setVisible(False)
        self.cup_tbl.cellDoubleClicked.connect(self._open_cup_detail)
        _enable_plain_copy(self.cup_tbl)
        right_lay.addWidget(self.cup_tbl)
        split.addWidget(right)
        split.setSizes([320, 620])
        # [2026-08] 리그/팀 검색 탭과 같은 이유로 나라 목록 칸도 고정폭 그리드라
        # 다이얼로그가 커져도 늘어나는 폭은 오른쪽 기록 패널로만 가게 한다.
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        self._cup_country_cache = []
        self._refresh_cup_country_list()
        return w

    def _refresh_cup_country_list(self, *_a):
        import time as _time_wc
        _wc_t0 = _time_wc.perf_counter()
        cont = None if self.cup_cont_combo.currentText() == _ALL else self.cup_cont_combo.currentText()
        grade = None if self.cup_grade_combo.currentText() == _ALL else self.cup_grade_combo.currentText()
        q = self.cup_search_box.text().strip().lower()
        countries = wb.list_countries(cont, grade)
        if q:
            countries = [c for c in countries if q in c["name"].lower()]
        self._cup_country_cache = countries
        _wc_t1 = _time_wc.perf_counter()

        # [2026-08 최적화] 나라마다 wb.has_cup_data()를 따로 부르던 N+1
        # 쿼리를 1회 배치 조회로 교체 — 표시되는 배지 결과는 동일하다.
        _cup_data_ids = wb.has_cup_data_bulk()
        _wc_t2 = _time_wc.perf_counter()

        self.cup_country_list.clear()
        # [2026-08 재작성] 리그검색과 동일한 이유로 delegate 기반 동기 루프로.
        for c in countries:
            has_data = c["id"] in _cup_data_ids
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, c["name"])
            item.setData(_GridRowDelegate._SPEC_ROLE, self._cup_row_spec(c, has_data))
            self.cup_country_list.addItem(item)
        _wc_t3 = _time_wc.perf_counter()
        self._ensure_list_fits(self.cup_country_list, self._cup_split)
        _wc_t4 = _time_wc.perf_counter()
        if _wc_t4 - _wc_t0 >= 0.03:
            print(f"[PERF-WB-CUP] 총 {_wc_t4-_wc_t0:.3f}s — "
                  f"list_countries({len(countries)}건) {_wc_t1-_wc_t0:.3f}s | "
                  f"has_cup_data_bulk {_wc_t2-_wc_t1:.3f}s | "
                  f"행채우기(delegate) {_wc_t3-_wc_t2:.3f}s | _ensure_list_fits {_wc_t4-_wc_t3:.3f}s")

    def _cup_row_spec(self, c, has_data):
        """[2026-08 신설] _cup_country_row_widget과 동일한 칸 구성(국가명/
        기록유무 배지)을 QWidget 없이 그릴 수 있는 스펙으로 표현.
        [2026-08 수정, 신민용 요청: "좌측 '기록 있음' 배지 필요없다, 등급이
        표시되게 해달라"] 기록 유무 배지 대신 그 나라의 클럽 리그 등급을
        보여준다 — 국가 검색 탭과 같은 색상 규칙(_GRADE_COLORS)을 쓴다.
        기록이 아예 없는 나라는 국가명을 흐리게(has_data=False) 둬서
        구분은 계속 유지한다."""
        return [
            {"text": f"{c['flag']} {c['name']}", "width": self._NAME_COL_W,
             "color": "#eee" if has_data else "#666", "bold": has_data},
            {"text": f"{c['grade']}급", "width": 70,
             "align": Qt.AlignmentFlag.AlignCenter, "size": 11, "bold": True,
             "color": _GRADE_COLORS.get(c["grade"], "#888888")},
        ]

    def _cup_country_row_widget(self, c, has_data):
        """컵대회 검색 탭 나라 목록 한 줄 — 리그/팀 검색 탭과 같은 그리드
        톤(고정폭 칸, 오른쪽 여백)으로 통일. [2026-08 신설, 신민용 요청:
        "컵대회 검색 UI도 좀 수정해줘"] 예전엔 그냥 QListWidgetItem 텍스트에
        "(기록 없음)"을 이어붙였는데, 다른 두 탭을 그리드로 정리한 김에
        여기도 같은 형태(국가명 고정폭 + 기록 유무 배지)로 맞춘다."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 6, 16, 6)
        h.setSpacing(10)

        h.addWidget(self._col_label(f"{c['flag']} {c['name']}", self._NAME_COL_W,
                                     color="#eee" if has_data else "#666",
                                     bold=has_data))
        badge = QLabel("기록 있음" if has_data else "기록 없음")
        badge.setFixedWidth(70)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        badge.setStyleSheet(
            "color:#00cc44;font-size:10px;background:#16301c;border-radius:3px;padding:2px 5px;"
            if has_data else
            "color:#666;font-size:10px;background:#262626;border-radius:3px;padding:2px 5px;")
        h.addWidget(badge)
        h.addStretch(1)
        return row

    def _on_cup_country_selected(self, item):
        cid = item.data(Qt.ItemDataRole.UserRole)
        if cid is None:
            return
        rows = wb.get_cup_history(cid)
        cname = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        self.cup_title.setText(f"🎖️ {cname} 역대 컵대회 기록")
        self._cup_copy_country_id = cid
        self._cup_copy_country_name = cname
        self.cup_rank_btn.setEnabled(bool(rows))
        self.cup_sub.setText(
            f"{rows[0]['name']}  ·  완료된 대회 {len(rows)}건" if rows
            else "이 나라에서 완료된 컵대회 기록이 없습니다")

        cols = ["연도", "대회명", "참여팀", "🏆 우승", "🥈 준우승", "🥉 3위", "4위"]
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
            names = [r["winner"], r["runner_up"], r["third"], r["fourth"]]
            tiers = [r.get("winner_tier"), r.get("runner_up_tier"), r.get("third_tier"), r.get("fourth_tier")]
            # [2026-08 신설] "참여팀" 열 — 그 시즌 컵대회에 실제로 등록된
            # 전체 참가팀 수. 대회 규모를 한눈에 보여줘서, 왜 어떤 대회는
            # 16강부터 시작하고 어떤 대회는 8강부터 시작하는지 바로 설명된다.
            n_teams_str = str(r["n_teams"]) if r.get("n_teams") else "-"
            vals = [str(r["year"]), r["name"], n_teams_str] + [_with_tier(n, t) for n, t in zip(names, tiers)]
            clean_vals = [None, None, None] + [n if n not in ("-", "?") else None for n in names]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if clean_vals[j] and clean_vals[j] != v:
                    cell.setData(_CLEAN_TEXT_ROLE, clean_vals[j])
                if j >= 3:
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

    def _on_cup_rank_leaders_clicked(self):
        cid = getattr(self, "_cup_copy_country_id", None)
        cname = getattr(self, "_cup_copy_country_name", "")
        if cid is None:
            return
        data = wb.get_cup_rank_leaders(cid)
        dlg = RankLeadersDialog(f"{cname} 컵대회", data,
                                 keys=("winner", "runner_up", "third", "fourth"),
                                 key_labels=["🥇 1위 팀", "🥈 2위 팀", "🥉 3위 팀", "4위 팀"],
                                 empty_msg="아직 완료된 컵대회 기록이 없습니다", parent=self)
        dlg.show()

    # ─────────────────────────────────────────
    # 탭3: 역대 챔피언스리그
    # ─────────────────────────────────────────
    def _build_cl_tab(self):
        return self._build_cl_style_tab(
            tbl_attr="cl_tbl", combo_attr="cl_cont_combo",
            history_fn=wb.get_cl_history, detail_fn=wb.get_cl_tournament_detail,
            winner_color=Qt.GlobalColor.yellow,
            rank_fn=wb.get_cl_style_rank_leaders, tab_title="챔피언스리그")

    def _build_el_tab(self):
        """[2026-08 신설] 역대 유로파리그 — 챔스와 완전히 같은 화면을
        재사용, 데이터 소스(el_*)와 우승 강조색만 다르다."""
        from PyQt6.QtGui import QColor
        return self._build_cl_style_tab(
            tbl_attr="el_tbl", combo_attr="el_cont_combo",
            history_fn=wb.get_el_history, detail_fn=wb.get_el_tournament_detail,
            winner_color=QColor("#F28C28"),
            rank_fn=wb.get_el_rank_leaders, tab_title="유로파리그")

    def _build_ecl_tab(self):
        """[2026-08 신설] 역대 컨퍼런스리그 — 위와 동일 패턴."""
        from PyQt6.QtGui import QColor
        return self._build_cl_style_tab(
            tbl_attr="ecl_tbl", combo_attr="ecl_cont_combo",
            history_fn=wb.get_ecl_history, detail_fn=wb.get_ecl_tournament_detail,
            winner_color=QColor("#20A464"),
            rank_fn=wb.get_ecl_rank_leaders, tab_title="컨퍼런스리그")

    def _build_cl_style_tab(self, tbl_attr, combo_attr, history_fn, detail_fn, winner_color,
                             rank_fn, tab_title):
        """[2026-08 신설] _build_cl_tab의 로직을 그대로 일반화 — 테이블/콤보
        위젯 속성명, 데이터 조회 함수, 우승 강조색만 매개변수로 뺐다.
        위젯 자체는 self.<tbl_attr>/<combo_attr>로 저장해서(예: self.el_tbl)
        기존 self.cl_tbl 패턴과 동일하게 다른 메서드에서도 접근 가능하다.

        [2026-08 확장, 신민용 요청: "대륙 선택 버튼 우측에 역대 1~4등을
        가장 많이 한 팀이 뜨는 창을 만들고, 기본값은 유럽으로"] rank_fn/
        tab_title을 추가로 받아 '🥇 최다 순위' 버튼을 필터 옆에 놓는다.

        [2026-08 수정, 신민용 요청: "일반 기록실 필터 기본값은 전체,
        최다 순위 팝업 필터 기본값은 유럽 — 별개의 filter state로
        분리해야 한다"] 이 탭(일반 기록실) 자체의 대륙 콤보 기본값은
        RECORD_FILTER_DEFAULT(전체)를 쓴다. '최다 순위' 팝업의 기본값
        (RANKING_FILTER_DEFAULT=유럽)은 _on_cl_style_rank_leaders_clicked
        쪽에서 완전히 독립적으로 관리 — 이 콤보를 안 읽는다."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        filt = QHBoxLayout()
        lbl = QLabel("대륙"); lbl.setStyleSheet("color:#888;font-size:11px;")
        combo = QComboBox()
        for cont in [_ALL, "유럽", "아시아", "아프리카", "북남미"]:
            combo.addItem(cont)
        combo.setCurrentText(RECORD_FILTER_DEFAULT)
        setattr(self, combo_attr, combo)
        combo.currentTextChanged.connect(
            lambda *_a: self._refresh_cl_style_table(tbl_attr, combo_attr, history_fn, winner_color))
        filt.addWidget(lbl)
        filt.addWidget(combo)
        filt.addStretch()
        rank_btn = QPushButton("🥇 최다 순위")
        rank_btn.clicked.connect(
            lambda: self._on_cl_style_rank_leaders_clicked(combo_attr, rank_fn, tab_title))
        filt.addWidget(rank_btn)
        lay.addLayout(filt)

        tbl = QTableWidget(0, 0)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.cellDoubleClicked.connect(
            lambda row, col: self._open_cl_style_detail(tbl_attr, detail_fn, row, col))
        _enable_plain_copy(tbl)
        setattr(self, tbl_attr, tbl)
        lay.addWidget(tbl)
        hint = QLabel("💡 대회를 더블클릭하면 리그 스테이지·토너먼트 상세를 볼 수 있어요")
        hint.setStyleSheet("color:#666;font-size:10px;")
        lay.addWidget(hint)

        self._refresh_cl_style_table(tbl_attr, combo_attr, history_fn, winner_color)
        return w

    def _on_cl_style_rank_leaders_clicked(self, combo_attr, rank_fn, tab_title):
        """[2026-08 수정, 신민용 리포트: "이거 최다 순위 구별하는 게 들어가기
        전에 있는 탭 필터로 구분한 후 들어가야 되잖아 — 역대 지역컵처럼
        별개로, 최다 순위 팝업 안에 필터를 넣어달라"] 예전엔 탭의 대륙
        콤보(combo_attr) 상태를 그대로 읽어서 그 대륙 기준으로만 열었다
        — 이제 네이션스컵/지역컵과 똑같이, 팝업 자체에 독립된 대륙
        필터(유럽/아시아/아프리카/북남미)를 두고 기본값을 탭의 현재
        선택과 무관하게 RANKING_FILTER_DEFAULT(유럽)로 고정한다. 필터를
        바꾸면 팝업 안에서 바로 다시 집계해서 보여준다."""
        self._open_cl_style_rank_dialog(tab_title, rank_fn, RANKING_FILTER_DEFAULT)

    # [2026-08 신설, 신민용 요청: "최다 순위 화면 상단에 [챔피언스][유로파]
    # [컨퍼런스][슈퍼컵] 이동 버튼 — 현재 화면은 제외"] 챔스/유로파/
    # 컨퍼런스/슈퍼컵 4개 대륙대회의 (버튼 라벨, tab_title, rank_fn) 목록.
    # 슈퍼컵은 아직 competition/super_cup_engine.py가 없어서 rank_fn=None
    # 으로 자리만 잡아둔다 — 그 파일이 생기면 이 한 줄만 채우면 된다.
    def _cl_style_rank_specs(self):
        return [
            ("챔피언스", "챔피언스리그", wb.get_cl_style_rank_leaders),
            ("유로파", "유로파리그", wb.get_el_rank_leaders),
            ("컨퍼런스", "컨퍼런스리그", wb.get_ecl_rank_leaders),
            ("슈퍼컵", "슈퍼컵", None),
        ]

    def _open_cl_style_rank_dialog(self, tab_title, rank_fn, continent_value):
        """대륙대회(챔스/유로파/컨퍼런스) '최다 순위' 팝업을 연다. 상단에
        같은 성격의 다른 대회로 바로 넘어가는 이동 버튼(현재 화면 제외)을
        같이 붙인다 — 클릭하면 이 팝업을 닫고 그 대회의 팝업을 새로 연다."""
        options = [(_ALL, None), ("유럽", "유럽"), ("아시아", "아시아"),
                   ("아프리카", "아프리카"), ("북남미", "북남미")]
        nav_buttons = []
        for label, other_title, other_fn in self._cl_style_rank_specs():
            if other_title == tab_title:
                continue   # 현재 보고 있는 화면은 이동 버튼에서 제외
            if other_fn is None:
                # 슈퍼컵 — 엔진 구현 전까지는 눌러도 안내만 뜬다.
                nav_buttons.append((label, self._show_super_cup_not_ready))
            else:
                nav_buttons.append((label, lambda t=other_title, f=other_fn:
                                     self._open_cl_style_rank_dialog(t, f, RANKING_FILTER_DEFAULT)))
        dlg = RankLeadersDialog(tab_title, rank_fn(continent=continent_value),
                                 keys=("winner", "runner_up", "third", "fourth"),
                                 key_labels=["🥇 1위 팀", "🥈 2위 팀", "🥉 3위 팀", "4위 팀"],
                                 filter_label="대륙", filter_options=options,
                                 filter_default=continent_value,
                                 fetch_fn=lambda cont: rank_fn(continent=cont),
                                 nav_buttons=nav_buttons,
                                 parent=self)
        dlg.show()

    def _show_super_cup_not_ready(self):
        """[2026-08 신설] 슈퍼컵은 아직 미구현 — competition/super_cup_engine.py
        (또는 super_cup.py)가 생기고 get_super_cup_rank_leaders 같은 조회
        함수가 world_browser.py에 추가되면, 위 _cl_style_rank_specs의
        마지막 항목 rank_fn만 채워 넣으면 이 자리에 자동으로 연결된다."""
        QMessageBox.information(self, "슈퍼컵",
                                 "슈퍼컵은 아직 준비 중입니다.")

    def _refresh_cl_style_table(self, tbl_attr, combo_attr, history_fn, winner_color):
        combo = getattr(self, combo_attr)
        tbl = getattr(self, tbl_attr)
        cont = None if combo.currentText() == _ALL else combo.currentText()
        rows = history_fn(continent=cont)
        cols = ["연도", "대회", "🥇 우승", "🥈 준우승", "🥉 3위", "4위"]
        tbl.clear()
        tbl.setRowCount(len(rows))
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        def _fmt_team(r, key):
            name = r.get(f"{key}_name") or ""
            if not name:
                return "-", None
            flag = r.get(f"{key}_flag") or ""
            country = r.get(f"{key}_country") or ""
            base = f"{flag} {name}".strip()
            return (f"{base} ({country})" if country else base), name

        for i, r in enumerate(rows):
            winner = _fmt_team(r, "winner")
            runner_up = _fmt_team(r, "runner_up")
            third = _fmt_team(r, "third")
            fourth = _fmt_team(r, "fourth")
            vals = [str(r["year"]), r["name"], winner[0], runner_up[0], third[0], fourth[0]]
            clean_vals = [None, None, winner[1], runner_up[1], third[1], fourth[1]]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if clean_vals[j] and clean_vals[j] != v:
                    cell.setData(_CLEAN_TEXT_ROLE, clean_vals[j])
                if j == 2:
                    cell.setForeground(winner_color)
                if j == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, r["id"])
                tbl.setItem(i, j, cell)
        self._show_empty_state(tbl, rows, "아직 완료된 대회가 없습니다", len(cols))
        self._grow_to_fit(tbl, stretch_col=1)

    def _open_cl_style_detail(self, tbl_attr, detail_fn, row, _col):
        tbl = getattr(self, tbl_attr)
        item = tbl.item(row, 0)
        tid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tid is None:
            return
        name_item = tbl.item(row, 1)
        title = f"{item.text()} {name_item.text() if name_item else ''}"
        detail = detail_fn(tid)
        dlg = TournamentDetailDialog(title, detail, team_based=True, parent=self)
        dlg.exec()

    # ─────────────────────────────────────────
    # 탭2.5: 역대 클럽 월드컵 (2026-07 신설)
    # ─────────────────────────────────────────
    def _build_cwc_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        info = QLabel("ℹ️ 국제대회가 없는 해(4년 주기)마다 대륙별 챔스 성적으로 32팀이 선발됩니다.")
        info.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(info)

        # [2026-08 신설, 신민용 요청: "클럽 월드컵에도 역대 1~4위를 가장
        # 많이 차지한 팀 순위를 보여달라"] 이 대회는 대륙 필터가 없으므로
        # (원래부터 여러 대륙이 섞여 참가) 필터 없이 버튼만 놓는다.
        tools = QHBoxLayout()
        tools.addStretch()
        cwc_rank_btn = QPushButton("🥇 최다 순위")
        cwc_rank_btn.clicked.connect(self._on_cwc_rank_leaders_clicked)
        tools.addWidget(cwc_rank_btn)
        lay.addLayout(tools)

        self.cwc_tbl = QTableWidget(0, 0)
        self.cwc_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cwc_tbl.verticalHeader().setVisible(False)
        self.cwc_tbl.cellDoubleClicked.connect(self._open_cwc_detail)
        _enable_plain_copy(self.cwc_tbl)
        lay.addWidget(self.cwc_tbl)
        hint = QLabel("💡 대회를 더블클릭하면 조별리그·토너먼트 상세를 볼 수 있어요")
        hint.setStyleSheet("color:#666;font-size:10px;")
        lay.addWidget(hint)

        self._refresh_cwc_table()
        return w

    def _refresh_cwc_table(self, *_a):
        rows = wb.get_cwc_history()
        cols = ["연도", "대회", "🥇 우승", "🥈 준우승", "🥉 3위", "4위"]
        self.cwc_tbl.clear()
        self.cwc_tbl.setRowCount(len(rows))
        self.cwc_tbl.setColumnCount(len(cols))
        self.cwc_tbl.setHorizontalHeaderLabels(cols)
        self.cwc_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.cwc_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        def _fmt_team(r, key):
            name = r.get(f"{key}_name") or ""
            if not name:
                return "-", None
            country = r.get(f"{key}_country") or ""
            return (f"{name} ({country})" if country else name), name

        for i, r in enumerate(rows):
            winner = _fmt_team(r, "winner")
            runner_up = _fmt_team(r, "runner_up")
            third = _fmt_team(r, "third")
            fourth = _fmt_team(r, "fourth")
            vals = [str(r["year"]), r["name"], winner[0], runner_up[0], third[0], fourth[0]]
            clean_vals = [None, None, winner[1], runner_up[1], third[1], fourth[1]]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if clean_vals[j] and clean_vals[j] != v:
                    cell.setData(_CLEAN_TEXT_ROLE, clean_vals[j])
                if j == 2:
                    cell.setForeground(Qt.GlobalColor.yellow)
                if j == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, r["id"])
                self.cwc_tbl.setItem(i, j, cell)
        self._show_empty_state(self.cwc_tbl, rows,
                                "아직 완료된 클럽 월드컵이 없습니다\n(4년 주기 대회라 초반엔 안 보이는 게 정상)",
                                len(cols))
        self._grow_to_fit(self.cwc_tbl, stretch_col=1)

    def _open_cwc_detail(self, row, _col):
        item = self.cwc_tbl.item(row, 0)
        tid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tid is None:
            return
        name_item = self.cwc_tbl.item(row, 1)
        title = f"{item.text()} {name_item.text() if name_item else ''}"
        detail = wb.get_cwc_tournament_detail(tid)
        dlg = TournamentDetailDialog(title, detail, team_based=True, parent=self)
        dlg.exec()

    def _on_cwc_rank_leaders_clicked(self):
        data = wb.get_cwc_rank_leaders()
        dlg = RankLeadersDialog("클럽 월드컵", data,
                                 keys=("winner", "runner_up", "third", "fourth"),
                                 key_labels=["🥇 1위 팀", "🥈 2위 팀", "🥉 3위 팀", "4위 팀"],
                                 empty_msg="아직 완료된 클럽 월드컵이 없습니다", parent=self)
        dlg.show()

    # ─────────────────────────────────────────
    # 탭3: 역대 월드컵
    # ─────────────────────────────────────────
    def _build_wc_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        # [2026-08 신설, 신민용 요청: "월드컵도 네이션스컵처럼 최다 순위가
        # 뜨게 해줘"] 월드컵은 대회명이 하나뿐이라(네이션스컵/지역컵과
        # 달리 여러 이름으로 안 갈림) 내부 필터 없이 버튼 하나로 바로 연다.
        tools = QHBoxLayout()
        tools.addStretch()
        wc_rank_btn = QPushButton("🥇 최다 순위")
        wc_rank_btn.clicked.connect(self._on_wc_rank_leaders_clicked)
        tools.addWidget(wc_rank_btn)
        lay.addLayout(tools)

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

    def _on_wc_rank_leaders_clicked(self):
        data = wb.get_wc_rank_leaders()
        dlg = RankLeadersDialog("월드컵", data,
                                 keys=("winner", "runner_up", "third", "fourth"),
                                 key_labels=["🥇 1위", "🥈 2위", "🥉 3위", "4위"],
                                 empty_msg="아직 완료된 월드컵이 없습니다", parent=self)
        dlg.show()

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
        # [2026-08 되돌림, 신민용 리포트: "나라끼리 붙는 국제대회(네이션스컵/
        # 지역컵)는 이 탭 필터 기본값이 전체가 맞다 — 유럽/코파를 기본으로
        # 하고 싶었던 건 '최다 순위' 팝업 안의 필터 얘기였다"] 탭 자체의
        # 기본 선택은 원래대로 전체(_ALL)로 되돌린다 — "유럽 기본" 요청은
        # 아래 _on_nc_rank_leaders_clicked이 여는 팝업 내부 필터로 옮겼다.
        self.nc_combo.currentTextChanged.connect(self._refresh_nc_table)
        filt.addWidget(lbl)
        filt.addWidget(self.nc_combo)
        filt.addStretch()
        nc_rank_btn = QPushButton("🥇 최다 순위")
        nc_rank_btn.clicked.connect(self._on_nc_rank_leaders_clicked)
        filt.addWidget(nc_rank_btn)
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

    def _on_nc_rank_leaders_clicked(self):
        """[2026-08 수정, 신민용 리포트: "나라끼리 붙는 국제대회는 탭
        필터가 전체가 맞고, 유럽을 기본으로 하고 싶었던 건 이 팝업 안의
        필터 얘기였다"] 탭의 현재 선택과 무관하게 항상 새로 연다 —
        팝업 내부에 자체 "대회" 필터를 두고(기본값 유럽 네이션스컵),
        그 필터를 바꾸면 팝업 안에서 바로 다시 집계해서 보여준다."""
        from constants import CONF_CUP_NAME
        options = [(_ALL, None)] + [(name, name) for name in wb.list_continental_cup_names()]
        default_name = CONF_CUP_NAME.get("유럽")
        default_value = default_name if any(v == default_name for _l, v in options) else None
        dlg = RankLeadersDialog("네이션스컵", wb.get_continental_cup_rank_leaders(name=default_value),
                                 keys=("winner", "runner_up", "third", "fourth"),
                                 key_labels=["🥇 1위", "🥈 2위", "🥉 3위", "4위"],
                                 empty_msg="아직 완료된 대회가 없습니다",
                                 filter_label="대회", filter_options=options,
                                 filter_default=default_value,
                                 fetch_fn=lambda name: wb.get_continental_cup_rank_leaders(name=name),
                                 parent=self)
        dlg.show()

    # ─────────────────────────────────────────
    # 탭4.5: 역대 지역컵 (2026-08 신설, 신민용 확정: "이건 챔스 탭처럼
    # 표 하나가 아니라, 리그 검색 탭처럼 좌측에 목록 두고 클릭하면
    # 우측에 그 대회 연도별 1~4위가 뜨게")
    # ─────────────────────────────────────────
    def _build_region_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        # [2026-08 신설] 왼쪽에서 실제로 클릭한 지역컵 대회명 — 아직 아무
        # 것도 안 골랐으면 None("← 왼쪽에서 지역대회를 선택하세요" 상태).
        self._region_selected_name = None

        info = QLabel("ℹ️ 월드컵/대륙컵/클럽월드컵 어느 것과도 겹치지 않는 해(4년 주기)에 "
                       "9개 지역(아시아 4·아프리카 3·북중미 2)에서 자동으로 열립니다.")
        info.setStyleSheet("color:#888;font-size:11px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        filt = QHBoxLayout()
        lbl = QLabel("대륙"); lbl.setStyleSheet("color:#888;font-size:11px;")
        self.region_cont_combo = QComboBox()
        self.region_cont_combo.addItem(_ALL)
        for cont in ["아시아", "아프리카", "북중미", "남미", "오세아니아"]:
            self.region_cont_combo.addItem(cont)
        # [2026-08 되돌림, 신민용 리포트: "나라끼리 붙는 국제대회는 이 탭
        # 필터 기본값이 전체가 맞다 — 코파를 기본으로 하고 싶었던 건
        # '최다 순위' 팝업 안의 필터 얘기였다"] 탭 자체 기본 선택은 원래
        # 대로 전체(_ALL)로 되돌린다.
        self.region_cont_combo.currentTextChanged.connect(self._refresh_region_list)
        filt.addWidget(lbl)
        filt.addWidget(self.region_cont_combo)
        filt.addStretch()
        # [2026-08 신설] '최다 순위' 버튼을 왼쪽 목록에서 뭘 선택했는지와
        # 무관한 위치(대륙 필터 옆)로 옮겼다 — 팝업 안에 자체 대회 필터가
        # 생겨서(기본값 코파 아메리카) 더 이상 좌측 목록 선택에 의존할
        # 필요가 없다.
        region_rank_btn = QPushButton("🥇 최다 순위")
        region_rank_btn.clicked.connect(self._on_region_rank_leaders_clicked)
        filt.addWidget(region_rank_btn)
        lay.addLayout(filt)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._region_split = split
        # [2026-08 수정, 신민용 리포트: "역대 지역컵 목록 글자가 다른 탭이랑
        # 안 맞는다"] 맨처음엔 그냥 QListWidgetItem(문자열)이라 왼쪽 여백도
        # 없고 다른 리스트(국가/리그/팀 검색)와 폰트·정렬이 안 맞았다 —
        # 그 탭들이 전부 쓰는 것과 완전히 같은 그리드 델리게이트
        # (_GridRowDelegate) + 고정 헤더 패턴으로 통일한다.
        self.region_list = QListWidget()
        self.region_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.region_list.itemClicked.connect(self._on_region_selected)
        self.region_list.setItemDelegate(_GridRowDelegate(self, self.region_list))
        region_header = self._list_header_row([
            ("대회명", self._NAME_COL_W, False),
            ("대륙", self._COUNTRY_COL_W, False),
        ])
        split.addWidget(self._wrap_list_with_header(self.region_list, region_header))

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(10, 0, 0, 0)
        self.region_title = QLabel("← 왼쪽에서 지역대회를 선택하세요")
        self.region_title.setStyleSheet("color:#00cc44;font-size:14px;font-weight:bold;")
        right_lay.addWidget(self.region_title)

        self.region_tbl = QTableWidget(0, 0)
        self.region_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.region_tbl.verticalHeader().setVisible(False)
        self.region_tbl.cellDoubleClicked.connect(
            lambda r, c: self._open_intl_detail(self.region_tbl, r, wc=False))
        right_lay.addWidget(self.region_tbl)
        hint = QLabel("💡 연도를 더블클릭하면 조별리그·토너먼트 상세를 볼 수 있어요")
        hint.setStyleSheet("color:#666;font-size:10px;")
        right_lay.addWidget(hint)

        split.addWidget(right)
        split.setSizes([440, 900])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        self._refresh_region_list()
        return w

    def _region_display_label(self, region, continent):
        """[2026-08 신설, 신민용 확정: "대륙 앞에 (동)아시아처럼 하위지역
        표시해줘, 근데 필터는 그대로 대륙 기준으로 동작해야"] 두 번째
        컬럼에 "(동)아시아"/"(동중부)아프리카"처럼 하위지역을 괄호로
        붙여서 보여준다. 지역명이 대륙명으로 끝나면(예: "동아시아".
        endswith("아시아")) 그 대륙명 부분을 떼고 남은 접두어만
        괄호에 넣고("동"), 안 끝나면(북중미의 "중앙아메리카"/"카리브"
        처럼) 지역명 전체를 그대로 괄호에 넣는다. 필터 자체는 이 표시
        문자열이 아니라 원본 continent 값으로 그대로 동작하므로(아래
        _refresh_region_list) 이 라벨을 바꿔도 필터링엔 영향 없다."""
        if continent and region.endswith(continent):
            prefix = region[:-len(continent)]
        else:
            prefix = region
        if not prefix:
            # [2026-08 신설] 남미(region="남미", continent="남미")처럼
            # 지역명이 대륙명과 완전히 같으면 접두어가 빈 문자열이 돼서
            # "()남미"라는 어색한 표시가 나온다 — 이 경우엔 괄호 없이
            # 대륙명만 보여준다.
            return continent
        return f"({prefix}){continent}"

    def _region_row_spec(self, name, label):
        return [
            {"text": name, "width": self._NAME_COL_W, "color": "#eee", "bold": True},
            {"text": label, "width": self._COUNTRY_COL_W, "color": "#aaddff"},
        ]

    def _refresh_region_list(self, *_a):
        from constants import REGION_CUP_NAME, REGION_TO_CONTINENT
        cont = self.region_cont_combo.currentText()
        names = wb.list_region_cup_names()
        # 대회명 -> 지역명/대륙 역매핑(REGION_CUP_NAME: 지역명->대회명, REGION_TO_CONTINENT: 지역명->대륙)
        cup_to_region = {cupname: region for region, cupname in REGION_CUP_NAME.items()}
        self.region_list.clear()
        for name in names:
            region = cup_to_region.get(name, "")
            name_cont = REGION_TO_CONTINENT.get(region, "")
            # 필터는 원본 대륙값으로만 동작 — 괄호 표시 라벨과 무관
            if cont != _ALL and name_cont != cont:
                continue
            label = self._region_display_label(region, name_cont)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setData(_GridRowDelegate._SPEC_ROLE, self._region_row_spec(name, label))
            self.region_list.addItem(item)
        self._ensure_list_fits(self.region_list, self._region_split)
        self.region_title.setText("← 왼쪽에서 지역대회를 선택하세요")
        self.region_tbl.setRowCount(0)
        self.region_tbl.setColumnCount(0)

    def _on_region_selected(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        # [2026-08 신설, 신민용 요청: "AFF 챔피언십을 클릭해서 화면에 떠있는
        # 상태에서 최다 순위를 누르면 코파가 아니라 AFF 챔피언십의 최다
        # 순위가 떠야 한다"] 왼쪽에서 실제로 클릭한 대회명을 기억해뒀다가
        # _on_region_rank_leaders_clicked에서 기본값으로 쓴다.
        self._region_selected_name = name
        self.region_title.setText(f"🌏 {name}")
        rows = wb.get_region_cup_history(name=name)
        self._fill_placement_table(self.region_tbl, rows,
                                    "아직 완료된 대회가 없습니다\n(대회 발생 연도가 되어야 기록이 쌓입니다)")

    def _on_region_rank_leaders_clicked(self):
        """[2026-08 수정, 신민용 리포트: "나라끼리 붙는 국제대회는 탭
        필터가 전체가 맞고, 코파를 기본으로 하고 싶었던 건 이 팝업 안의
        필터 얘기였다"] 왼쪽 목록에서 뭘 선택했는지와 무관하게 항상 열 수
        있다 — 팝업 내부에 자체 "대회" 필터를 두고(기본값 코파 아메리카),
        그 필터를 바꾸면 팝업 안에서 바로 다시 집계해서 보여준다.

        [2026-08 수정, 신민용 리포트: "왼쪽에서 아무것도 선택 안 했을 때는
        코파 아메리카가 기본이 맞지만, AFF 챔피언십처럼 왼쪽에서 실제로
        클릭해서 화면에 띄워둔 상태라면 최다 순위도 그 대회(AFF 챔피언십)
        기준으로 떠야 한다"] 왼쪽에서 클릭해 고른 대회(_region_selected_name)가
        있으면 그걸 최우선으로 쓰고, 아직 아무것도 안 골랐으면(맨 처음
        "← 왼쪽에서 지역대회를 선택하세요" 상태) 기존처럼 코파 아메리카로
        폴백한다.
        [2026-08 신설, 신민용 요청: "필터 목록에 대회명만 있는데 어느
        지역인지도 같이 보여달라"] REGION_CUP_NAME(지역명→대회명)을
        거꾸로 뒤져서 "AFF 챔피언십(동남아시아)"처럼 표시 라벨에만
        지역명을 괄호로 붙인다 — fetch_fn에 넘기는 실제 값(대회명)은
        그대로라 조회 로직엔 영향 없다."""
        from constants import REGION_CUP_NAME
        cup_to_region = {v: k for k, v in REGION_CUP_NAME.items()}

        def _labeled(name):
            region = cup_to_region.get(name)
            return f"{name}({region})" if region else name

        options = [(_ALL, None)] + [(_labeled(name), name) for name in wb.list_region_cup_names()]
        valid_values = {v for _l, v in options}
        selected = getattr(self, "_region_selected_name", None)
        if selected and selected in valid_values:
            default_value = selected
        elif any(v == "코파 아메리카" for _l, v in options):
            default_value = "코파 아메리카"
        else:
            default_value = None
        dlg = RankLeadersDialog("지역컵", wb.get_region_cup_rank_leaders(name=default_value),
                                 keys=("winner", "runner_up", "third", "fourth"),
                                 key_labels=["🥇 1위", "🥈 2위", "🥉 3위", "4위"],
                                 empty_msg="아직 완료된 대회가 없습니다",
                                 filter_label="대회", filter_options=options,
                                 filter_default=default_value,
                                 fetch_fn=lambda name: wb.get_region_cup_rank_leaders(name=name),
                                 parent=self)
        dlg.show()

    # ─────────────────────────────────────────
    # 공용 헬퍼
    # ─────────────────────────────────────────
    def _fill_placement_table(self, tbl, rows, empty_msg):
        """연도/대회명 + 1~4위(국기 포함) 공통 테이블 채우기.
        (역대 월드컵/네이션스컵/지역컵 탭이 동일한 형식이라 공용 헬퍼로 통합)"""
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
                    return "-", None
                return f"{r.get(f'{key}_flag','')} {nat}".strip(), nat
            w_disp, w_clean = _fmt("winner")
            ru_disp, ru_clean = _fmt("runner_up")
            th_disp, th_clean = _fmt("third")
            fo_disp, fo_clean = _fmt("fourth")
            vals = [str(r["year"]), r["name"], w_disp, ru_disp, th_disp, fo_disp]
            clean_vals = [None, None, w_clean, ru_clean, th_clean, fo_clean]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 2:
                    cell.setForeground(Qt.GlobalColor.yellow)
                if j == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, r["id"])
                # [2026-08 신설, 신민용 리포트: "복사하면 국기/국가명까지
                # 같이 복사된다 — 챔스처럼 이름만 복사되게"] 챔스/컵대회
                # 탭과 같은 패턴(_CLEAN_TEXT_ROLE + _enable_plain_copy)을
                # 이 공용 헬퍼에도 적용 — 월드컵/네이션스컵/지역컵 탭 전부
                # 한 번에 해결된다.
                if clean_vals[j]:
                    cell.setData(_CLEAN_TEXT_ROLE, clean_vals[j])
                tbl.setItem(i, j, cell)
        self._show_empty_state(tbl, rows, empty_msg, len(cols))
        self._grow_to_fit(tbl, stretch_col=1)
        _enable_plain_copy(tbl)

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
        # [2026-08 버그수정, 신민용 리포트: "역대 챔피언스리그 표에서 연도가
        # 셀 하나에 안 들어가고 그 행 전체를 가로로 뒤덮으면서 나머지
        # 칸이 안 보이는 깨짐 현상"] setSpan(0,0,1,n_cols)으로 "아직 완료된
        # 대회가 없습니다" 안내문을 한 행 전체에 걸쳐 표시하는데, 이 span은
        # QTableWidget.clear()로도 안 지워진다(clear()는 아이템/헤더만
        # 지우고 커스텀 span은 그대로 남김) — 그래서 필터를 "결과 없음" →
        # "결과 있음"으로 바꾸면, 이전에 걸어둔 그 span이 그대로 남아있는
        # 채로 새 데이터가 채워져서 0행 0열(연도 칸)이 여전히 전체 폭으로
        # 뻗어 나머지 5개 칸(대회/우승/준우승/3위/4위)을 가려버렸다.
        # 매번 무조건 먼저 지워서, 이전 렌더링 상태가 절대 새 렌더링에
        # 넘어오지 못하게 한다.
        tbl.clearSpans()
        if rows:
            return
        tbl.setRowCount(1)
        note = QTableWidgetItem(msg)
        note.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setForeground(Qt.GlobalColor.darkGray)
        tbl.setItem(0, 0, note)
        tbl.setSpan(0, 0, 1, n_cols)


class RankLeadersDialog(QDialog):
    """[2026-08 신설, 신민용 요청: "역대 우승팀/팀 순위 버튼 옆에 이 리그에서
    1등/2등을 가장 많이 한 팀 순위를 보여주는 창을 만들어달라" → 이후
    "챔스/유로파/컨퍼런스/클럽월드컵/네이션스컵/지역컵/컵대회에도 역대
    1~4위를 가장 많이 차지한 팀/국가 순위를 보여달라"] 처음엔 리그 전용
    (1등/2등 2칸)이었는데, 자리(key) 개수만 다를 뿐 구조가 완전히 같은
    요청이 6개 대회 탭에 더 필요해져서 범용 다이얼로그로 만들었다 —
    keys/key_labels 길이만큼 (팀명+횟수) 열 쌍이 옆으로 늘어난다(리그는
    2쌍, 챔스류는 4쌍). TournamentDetailDialog와 같은 톤(STYLE, 닫기
    버튼)을 따르되, 이 창은 표 하나뿐이라 스크롤 영역 없이 끝낸다.

    [팀명 (국가) 표시] data의 각 항목이 country를 갖고 있으면(클럽
    대항전 — 같은 팀명이 다른 나라에 있을 수 있어 구분 필요) "팀명
    (국가)"로 보여주되, 복사(Ctrl+C/우클릭)하면 국가 없이 팀명만
    복사되도록 _CLEAN_TEXT_ROLE에 원본 이름을 따로 저장해둔다
    (_enable_plain_copy가 이 롤을 우선 읽음). country가 없으면(국가대표
    대회 — 참가자 자체가 국가라 분리 불필요) 이름 그대로 표시.

    [2026-08 수정, 신민용 리포트: "나라끼리 붙는 국제대회(네이션스컵/
    지역컵)는 세계기록실 탭 자체 필터가 아니라, 이 팝업 안에 자체
    필터를 만들고 그 기본값을 유럽/코파로 하고 싶었다"] filter_options/
    filter_default/fetch_fn을 주면 상단에 자체 콤보가 생기고, 바꿀 때마다
    fetch_fn(선택값)을 다시 불러 표를 새로 그린다 — 다이얼로그를 새로
    열지 않고 같은 창 안에서 바로 갱신된다.

    [2026-08 버그수정, 신민용 리포트: "박스 클릭하고 복사하면 팀명만이
    아니라 표 전체가 복사된다"] 표 SelectionMode를 NoSelection으로 뒀던
    게 원인 — 선택 자체가 막혀 있어 클릭해도 셀이 선택되지 않고, 그
    상태에서 Ctrl+C를 누르면 _enable_plain_copy의 selectedItems()가
    아무것도 못 찾아 엉뚱하게 동작했다. 다른 '역대 기록' 표들과 동일하게
    셀 단위 선택이 되도록 기본 선택 모드(ExtendedSelection)를 그대로
    둔다 — 클릭 한 칸만 복사하면 그 칸만, 드래그로 여러 칸을 잡으면
    그만큼만 탭/줄바꿈으로 묶여 복사된다(다른 표들과 동일한 동작)."""
    _PLACE_COLORS = ["#ffd700", "#c0c0c0", "#cd7f32", "#aaddff"]
    # [2026-08 버그수정, 신민용 리포트: "최다 승격/최다 강등 색이 열 순서에
    # 따라 바뀐다"] 예전엔 색을 열 인덱스(ki)로만 정했다 — _PLACE_COLORS를
    # ki % 4로 순환시키다 보니, 1부 리그처럼 최다 승격 열 자체가 없어서
    # most_relegated가 4번째 자리(원래 4위 팀 자리)로 밀려 들어오면 순환
    # 규칙상 골드(1위색)를 받아버렸다 — "승격 없으면 강등이 노란색으로
    # 뜬다"는 리포트가 정확히 이 현상. 이제 열의 '자리'가 아니라 그 열의
    # key 자체로 색을 정한다 — most_promoted는 항상 파란색, most_relegated는
    # 항상 빨간색, 나머지(1~4위)는 기존 금/은/동/하늘색 순환을 그대로 쓴다.
    _FIXED_KEY_COLORS = {"most_promoted": "#4da6ff", "most_relegated": "#ff5555"}

    def __init__(self, title, data, keys, key_labels, empty_msg="아직 완료된 기록이 없습니다",
                 filter_label=None, filter_options=None, filter_default=None, fetch_fn=None,
                 nav_buttons=None, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle(f"{title} — 최다 순위")
        self.setStyleSheet(STYLE)
        n_pairs = len(keys)
        _clamp_and_resize(self, min(340 + n_pairs * 230, 1200), 560)

        self._keys = keys
        self._empty_msg = empty_msg
        self._fetch_fn = fetch_fn
        self._n_cols = 1 + n_pairs * 2

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)

        hdr = QLabel(f"🏆 {title}  최다 순위")
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        outer.addWidget(hdr)

        sub = QLabel("역대 순위별로 가장 많이 그 자리를 차지한 팀/국가 순위입니다.")
        sub.setStyleSheet("color:#888;font-size:11px;")
        outer.addWidget(sub)

        # [2026-08 신설, 신민용 요청: "최다 순위 화면 상단에 [챔피언스]
        # [유로파][컨퍼런스][슈퍼컵] 이동 버튼 — 현재 화면은 제외"]
        # nav_buttons: [(라벨, 클릭시콜백), ...] — 콜백은 인자 없이 호출된다.
        # 클릭하면 이 팝업을 닫고 콜백이 다음 팝업을 연다(그래서 항상
        # 팝업이 하나만 떠 있다).
        if nav_buttons:
            nav_row = QHBoxLayout()
            nav_lbl = QLabel("다른 대회 보기")
            nav_lbl.setStyleSheet("color:#666;font-size:11px;")
            nav_row.addWidget(nav_lbl)
            for nav_label, nav_cb in nav_buttons:
                nav_btn = QPushButton(nav_label)
                nav_btn.setAutoDefault(False)
                nav_btn.setDefault(False)
                nav_btn.setStyleSheet(
                    "QPushButton{background:#232323;color:#aad4ff;border:1px solid #3a3a3a;"
                    "border-radius:10px;padding:2px 12px;font-size:11px;}"
                    "QPushButton:hover{border-color:#00cc44;color:#fff;}")
                nav_btn.clicked.connect(lambda _checked=False, cb=nav_cb: self._on_nav_clicked(cb))
                nav_row.addWidget(nav_btn)
            nav_row.addStretch()
            outer.addLayout(nav_row)

        self._filter_combo = None
        if filter_options:
            filt = QHBoxLayout()
            flbl = QLabel(filter_label or "필터")
            flbl.setStyleSheet("color:#888;font-size:11px;")
            filt.addWidget(flbl)
            combo = QComboBox()
            for opt_label, opt_value in filter_options:
                combo.addItem(opt_label, opt_value)
            idx = combo.findData(filter_default)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(self._on_filter_changed)
            filt.addWidget(combo)
            filt.addStretch()
            outer.addLayout(filt)
            self._filter_combo = combo

        self._tbl = QTableWidget(0, self._n_cols)
        self._tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl.verticalHeader().setVisible(False)
        cols = ["순위"]
        for lbl in key_labels:
            cols += [lbl, "횟수"]
        self._tbl.setHorizontalHeaderLabels(cols)
        self._tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for ci in range(1, self._n_cols):
            mode = (QHeaderView.ResizeMode.Stretch if ci % 2 == 1
                    else QHeaderView.ResizeMode.ResizeToContents)
            self._tbl.horizontalHeader().setSectionResizeMode(ci, mode)
        _enable_plain_copy(self._tbl)
        outer.addWidget(self._tbl, 1)

        close_btn = QPushButton("닫기")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        outer.addWidget(close_btn)

        self._populate(data)

    def _on_nav_clicked(self, callback):
        """이동 버튼 클릭 — 이 팝업을 닫고(비모달이라 여러 개 안 겹치게)
        콜백에게 다음 팝업을 열도록 맡긴다."""
        self.close()
        callback()

    def _on_filter_changed(self, _idx):
        if not self._fetch_fn or not self._filter_combo:
            return
        value = self._filter_combo.currentData()
        self._populate(self._fetch_fn(value))

    def _populate(self, data):
        keys = self._keys
        tbl = self._tbl
        lists = [data.get(k) or [] for k in keys]
        n_rows = max((len(lst) for lst in lists), default=0)

        tbl.setRowCount(n_rows)
        tbl.clearSpans()

        for i in range(n_rows):
            rank_item = QTableWidgetItem(f"{i + 1}위")
            rank_item.setForeground(QColor("#ffcc00"))
            f = rank_item.font(); f.setBold(True); rank_item.setFont(f)
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tbl.setItem(i, 0, rank_item)

            for ki, lst in enumerate(lists):
                col_name = 1 + ki * 2
                col_cnt = col_name + 1
                key = keys[ki] if ki < len(keys) else None
                color = self._FIXED_KEY_COLORS.get(key) or self._PLACE_COLORS[ki % len(self._PLACE_COLORS)]
                if i < len(lst):
                    entry = lst[i]
                    country = entry.get("country")
                    disp = f"{entry['name']} ({country})" if country else entry["name"]
                    name_item = QTableWidgetItem(disp)
                    name_item.setForeground(QColor(color))
                    if disp != entry["name"]:
                        name_item.setData(_CLEAN_TEXT_ROLE, entry["name"])
                    cnt_item = QTableWidgetItem(f"{entry['count']}회")
                    cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    tbl.setItem(i, col_name, name_item)
                    tbl.setItem(i, col_cnt, cnt_item)
                else:
                    tbl.setItem(i, col_name, QTableWidgetItem("-"))
                    tbl.setItem(i, col_cnt, QTableWidgetItem(""))

        if n_rows == 0:
            tbl.setRowCount(1)
            empty = QTableWidgetItem(self._empty_msg)
            empty.setForeground(QColor("#666"))
            tbl.setItem(0, 0, empty)
            tbl.setSpan(0, 0, 1, self._n_cols)


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
        _clamp_and_resize(self, 760, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)

        hdr = QLabel(f"📋 {title}")
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        outer.addWidget(hdr)

        # [2026-07 신설, 신민용 요청] 참가국 요약 — 커리어창 "개인 수상"
        # 요약줄(종류별 N회)과 같은 톤으로, 이 대회에 어느 나라가 몇 팀
        # 참가했는지 많은 순으로 보여준다. team_based(팀 대항전)일 때만
        # 의미가 있고, 월드컵/네이션스컵(국가 자체가 참가자)일 땐 굳이
        # 안 보여준다 — 이미 그 자체가 "참가국 목록"이므로 중복.
        # [2026-07 신설, 신민용 요청: "클럽월드컵에 대륙별로 몇 개 올라왔는지
        # 맨 위에, 그 아래에 나라별로 몇 팀 나갔는지"] 대륙별 요약을 국가별
        # 요약(_build_country_summary, 기존 기능) 위에 추가로 보여준다.
        # 대륙 구분이 의미 있으려면 대회 자체가 여러 대륙을 섞어 참가시켜야
        # 하는데(클럽월드컵이 정확히 이 경우 — 유럽/아시아/아프리카/북남미
        # 4개 대륙에서 함께 뽑음), 챔피언스리그처럼 애초에 대회 자체가 한
        # 대륙으로 한정된 경우엔 대륙이 1개뿐이라 자동으로 안 뜬다(country
        # 요약과 동일한 "값이 2개 이상일 때만" 규칙 재사용).
        if team_based:
            continent_summary = self._build_continent_summary(detail)
            if continent_summary:
                outer.addWidget(continent_summary)
            country_summary = self._build_country_summary(detail)
            if country_summary:
                outer.addWidget(country_summary)

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
            lay.addWidget(self._build_groups_grid(groups, team_based, detail.get("qualified")))

        league_standings = detail.get("league_standings") or []
        if league_standings:
            lay.addWidget(self._section_label("⚽ 리그 스테이지"))
            lay.addWidget(self._build_league_standings_table(
                league_standings, detail.get("continent"), detail.get("comp_kind")))

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

    def _build_continent_summary(self, detail):
        """참가팀들의 대륙을 세어 많은 순으로 요약 라벨 생성 (클럽월드컵처럼
        여러 대륙이 한 대회에 섞여 참가할 때만 의미가 있음).
        _build_country_summary와 완전히 같은 구조 — 대륙(continent) 필드만
        다르게 센다. 대륙이 1개뿐이면(예: 챔피언스리그 — 대회 자체가 이미
        한 대륙으로 한정) 정보가 없으므로 None 반환."""
        from collections import Counter
        continents = []
        groups = detail.get("groups") or {}
        if groups:
            for rows in groups.values():
                continents.extend(r.get("continent", "") for r in rows if r.get("continent"))
        else:
            for r in (detail.get("league_standings") or []):
                if r.get("continent"):
                    continents.append(r["continent"])
        if not continents:
            return None
        cnt = Counter(continents)
        if len(cnt) <= 1:
            return None
        ranked = cnt.most_common()

        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 4, 0, 4); lay.setSpacing(2)

        top_n, top_c = ranked[0]
        head = QLabel(f"🌐 참가 대륙 {len(cnt)}곳  ·  최다 참가: {top_n} ({top_c}팀)")
        head.setStyleSheet("color:#4dd0e1;font-size:13px;font-weight:bold;")
        lay.addWidget(head)

        parts = [f"{name} {c}팀" for name, c in ranked]
        body = QLabel("  ·  ".join(parts))
        body.setStyleSheet("color:#aaaaaa;font-size:11px;")
        body.setWordWrap(True)
        lay.addWidget(body)
        return w

    def _build_country_summary(self, detail):
        """참가팀들의 국가를 세어 많은 순으로 요약 라벨 생성.
        groups(dict of list) 또는 league_standings(list) 아무 쪽이든
        채워진 쪽에서 country 필드를 모은다. 참가국이 1개뿐이면(예: 국내
        대회) 굳이 안 보여줄 만큼 정보가 없으므로 None 반환."""
        from collections import Counter
        countries = []
        groups = detail.get("groups") or {}
        if groups:
            for rows in groups.values():
                countries.extend(r.get("country", "") for r in rows if r.get("country"))
        else:
            for r in (detail.get("league_standings") or []):
                if r.get("country"):
                    countries.append(r["country"])
        if not countries:
            return None
        cnt = Counter(countries)
        if len(cnt) <= 1:
            return None
        ranked = cnt.most_common()

        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 4, 0, 4); lay.setSpacing(2)

        top_n, top_c = ranked[0]
        head = QLabel(f"🌍 참가국 {len(cnt)}개국  ·  최다 참가: {top_n} ({top_c}팀)")
        head.setStyleSheet("color:#ffcc00;font-size:13px;font-weight:bold;")
        lay.addWidget(head)

        # 개인수상 요약줄과 같은 톤(가운뎃점 구분)으로 국가별 참가팀 수 나열
        parts = [f"{name} {c}팀" for name, c in ranked]
        body = QLabel("  ·  ".join(parts))
        body.setStyleSheet("color:#aaaaaa;font-size:11px;")
        body.setWordWrap(True)
        lay.addWidget(body)
        return w

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

    def _build_league_standings_table(self, standings, continent=None, comp_kind="champions"):
        """[2026-07 신설] 챔스 스위스 방식 리그 스테이지 전체 순위표(단일 표).
        조별 카드 대신 순위·팀명·승무패·득실·승점을 한 표로 쭉 보여준다.

        [2026-07 추가, 신민용 요청: "경기 일정 화면처럼 직행/플레이오프
        색깔 구분이 역대 기록에도 있으면 좋겠다"] schedule_window.py의
        진행 중 화면과 완전히 같은 색상 체계(초록=직행/주황=플레이오프/
        회색=탈락권)를 그대로 재사용해서 통일감을 준다.
        [2026-08 버그수정, 신민용 리포트: "역대 기록 상세의 직행/플레이오프
        범례가 유로파/컨퍼런스도 챔스 컷(북남미 1~16/17~48)을 그대로
        쓰고 있다"] 챔스/유로파/컨퍼런스가 이 표를 공용으로 쓰는데,
        컷 라인은 항상 챔스 전용 대륙별 상수(CL_DIRECT_CUT_BY_CONTINENT
        등 — 북남미만 48팀 규모)에서 가져왔었다. 유로파/컨퍼런스는
        대륙 무관 고정값(8직행/16풀, europa_engine._el_direct_cut 등)을
        쓰므로, comp_kind로 어느 대회인지 구분해서 올바른 값을 쓴다."""
        from PyQt6.QtGui import QColor
        COLOR_ADVANCE = QColor("#00cc44")
        COLOR_THIRD   = QColor("#ffaa00")
        COLOR_ELIM    = QColor("#888888")

        direct_cut = playoff_cut = None
        if continent:
            if comp_kind == "champions":
                from competition.champions_engine import (
                    CL_DIRECT_CUT_BY_CONTINENT, CL_PLAYOFF_POOL_BY_CONTINENT)
                direct_cut = CL_DIRECT_CUT_BY_CONTINENT.get(continent)
                playoff_pool = CL_PLAYOFF_POOL_BY_CONTINENT.get(continent)
            else:
                # 유로파/컨퍼런스는 대륙 무관 고정값(8직행/16풀) — 둘 다
                # 값이 같아서 comp_kind 구분 없이 바로 써도 된다.
                direct_cut = 8
                playoff_pool = 16
            # [2026-07 버그수정, 신민용 리포트] playoff_cut은 direct_cut+playoff_pool이어야 하는데
            # playoff_pool을 그대로 컷 라인으로 쓴 버그(유럽 8+16=24위가 정확한데 16위까지로 잘물었음).
            playoff_cut = (direct_cut + playoff_pool) if (direct_cut is not None and playoff_pool is not None) else None

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
            "QTableWidget::item:hover{background:#2a3a2a;}"
            "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")
        # [2026-08 되돌림, 신민용 리포트: "클릭하면 갑자기 팀 검색으로
        # 가는데 이거 없애고, 원하는 건 복사만 되는 것"] 팀 검색 탭으로
        # 이동하는 클릭 동작은 제거했다 — 이 표는 이제 클릭해도 아무
        # 일도 안 일어나고, 셀 선택 후 복사(우클릭 메뉴 또는 Ctrl+C)만
        # 지원한다(아래 _enable_plain_copy).

        for i, r in enumerate(standings):
            gd = r["gf"] - r["ga"]
            vals = [str(i + 1), self._team_text(r["name"], r["flag"], r.get("country")),
                    str(r["wins"]), str(r["draws"]), str(r["losses"]),
                    f"{gd:+d}", str(r["pts"])]
            color = None
            if direct_cut is not None:
                if i < direct_cut:
                    color = COLOR_ADVANCE
                elif playoff_cut is not None and i < playoff_cut:
                    color = COLOR_THIRD
                else:
                    color = COLOR_ELIM
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color:
                    item.setForeground(color)
                if j == 1:
                    item.setData(_CLEAN_TEXT_ROLE, r["name"])
                tbl.setItem(i, j, item)
        tbl.setFixedHeight(tbl.verticalHeader().defaultSectionSize() * len(standings) + 32)
        _enable_plain_copy(tbl)
        lay.addWidget(tbl)

        if direct_cut is not None:
            hint = QLabel(f"🟢1~{direct_cut}위 직행  🟡{direct_cut+1}~{playoff_cut}위 플레이오프  "
                          f"⬜{playoff_cut+1}위 이하 광탈")
            hint.setStyleSheet("color:#888;font-size:10px;padding:4px 2px 0 2px;")
            lay.addWidget(hint)
        return box

    def _build_groups_grid(self, groups, team_based, qualified=None):
        # [2026-08 버그수정, 신민용 리포트: "3위 와일드카드로 진출한 팀도
        # 흰색으로 떠야 하는데 회색으로 뜬다"] qualified(실제 다음 라운드
        # 첫 대진에 등장한 팀 이름 집합, world_browser.get_intl_tournament_
        # detail이 계산해서 넘겨줌)가 있으면 그걸로 진출 여부를 판정하고,
        # 없으면(옛 호출부·조별탈락형 등) 예전처럼 순위<2로 폴백한다.
        # [2026-08 신설, 신민용 리포트: "복사하면 국기/국가까지 같이
        # 복사된다"] 조별 표를 QGridLayout+QLabel(선택/복사 전부 불가능한
        # 정적 텍스트)에서 QTableWidget(다른 '역대 기록' 표들과 동일한
        # 위젯)으로 바꿨다 — _team_text 장식은 화면 표시용으로만 쓰고,
        # _CLEAN_TEXT_ROLE에 순수 팀명을 같이 저장해서 _enable_plain_copy가
        # 복사 시 그것만 꺼내 쓰게 한다.
        # [2026-08 되돌림, 신민용 리포트: "클릭하면 팀 검색으로 가는데
        # 이거 없애달라"] 한때 팀명 클릭 시 "팀 검색" 탭으로 이동하는
        # 기능을 넣었었는데, 원치 않는 동작이라 제거 — 이 표는 클릭해도
        # 아무 일 없고 복사만 된다.
        qualified = qualified or set()
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
            clay.setSpacing(4)

            title = QLabel(f"{g}조")
            title.setStyleSheet("color:#00cc44;font-size:12px;font-weight:bold;")
            clay.addWidget(title)

            tbl = QTableWidget(len(teams), 7)
            tbl.setHorizontalHeaderLabels(["순위", "팀", "승", "무", "패", "득실", "승점"])
            tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tbl.verticalHeader().setVisible(False)
            tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            tbl.setStyleSheet(
                "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;font-size:11px;}"
                "QTableWidget::item:hover{background:#2a3a2a;}"
                "QHeaderView::section{background:#252525;color:#888;border:none;padding:2px;font-size:9px;}")

            for rank, t in enumerate(teams):
                name = t["name"] if team_based else t["country"]
                country = t.get("country") if team_based else None
                # [2026-08 버그수정, 신민용 리포트: "카보베르데가 조 2위인데
                # 흰색으로 뜨는데 정작 플레이오프 대진표엔 없다 — 반대로
                # 1위만 진출하는 방식인데 2위까지 흰색 칠해진 거 아니냐"]
                # 예전엔 "순위<2 OR qualified에 있으면 진출"이었다 — qualified
                # (실제 다음 라운드 대진표에 등장한 이름, 위에서 이미 정확히
                # 계산됨)가 있어도 rank<2 쪽이 OR로 걸려서 항상 이겨버렸다.
                # 그 결과 "1위만 진출"(이번 카보베르데 케이스)이나 "전체 조
                # 2위 중 상위 2팀만 플레이오프"(잉글랜드 32강 월드컵 예선
                # 같은 경우) 방식에서도 순위만 보고 무조건 상위 2명을 흰색
                # 칠해버려서, 실제로는 못 올라간 2위 팀들까지 죄다 진출한
                # 것처럼 보였다. 대진표가 이미 생성돼 qualified를 알 수
                # 있으면 그것만 근거로 삼고(순위는 무시), 아직 대진표가 없는
                # 진행중 상태(qualified가 비어있음 — knockout 자체가 없다는
                # 뜻)일 때만 순위<2로 잠정 표시한다.
                # [2026-08 재수정, 신민용 리포트: "유럽 예선처럼 조 1위가
                # 플레이오프 없이 곧장 자동 진출하는 방식에서는 1위도
                # qualified(다음 라운드 대진표 등장 팀)에 안 잡힌다"] 위
                # qualified 집합은 "다음 라운드 대진표에 실제로 등장한
                # 팀"만 담는데, 조 1위가 플레이오프 자체를 안 치르고 자동
                # 진출하는 대회(예: 유럽 월드컵 예선 — 조 1위 자동 진출 +
                # 일부 2위만 플레이오프)에서는 그 1위가 대진표에 아예 안
                # 나타나 흰색 표시를 못 받았다. 조 1위는 자동 진출이든
                # 플레이오프행이든 어떤 형태로든 조별리그는 반드시
                # 통과한 것이므로, 대진표 등장 여부와 무관하게 항상
                # 흰색으로 고정하고, 그 위에 qualified(플레이오프까지
                # 가는 2위 이하)를 더한다.
                advancing = (rank == 0) or ((name in qualified) if qualified else (rank < 2))
                color = QColor("#ffffff" if advancing else "#777777")
                gd = t["gf"] - t["ga"]
                vals = [str(rank + 1), self._team_text(name, t["flag"], country),
                        str(t["wins"]), str(t["draws"]), str(t["losses"]),
                        f"{gd:+d}", str(t["pts"])]
                for j, v in enumerate(vals):
                    item = QTableWidgetItem(v)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(QColor("#00cc44") if (j == 6 and advancing) else color)
                    if advancing:
                        f = item.font(); f.setBold(True); item.setFont(f)
                    if j == 1:
                        item.setData(_CLEAN_TEXT_ROLE, name)
                    tbl.setItem(rank, j, item)
            tbl.setFixedHeight(tbl.verticalHeader().defaultSectionSize() * len(teams) + 30)
            _enable_plain_copy(tbl)
            clay.addWidget(tbl)
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
                # [2026-08 신설] 국내컵(get_cup_tournament_detail)은 country
                # 대신 참가 당시 tier를 넘긴다 — CL/CWC는 country, 컵대회는
                # "N부"를 같은 위치(_team_text의 괄호 안)에 표시한다.
                hc = m["home_info"].get("country") or (
                    f"{m['home_info']['tier']}부" if m["home_info"].get("tier") else None)
                ac = m["away_info"].get("country") or (
                    f"{m['away_info']['tier']}부" if m["away_info"].get("tier") else None)
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
            # [2026-08 신설, 신민용 리포트: "경기 상세 내역 표에서도 복사할
            # 수 있게 해달라, 국기/부수 없이 팀명만"] QLabel은 QTableWidget과
            # 달리 셀 선택/클립보드 개념이 없어 우클릭 "복사" 메뉴를 직접
            # 붙인다 — 클릭으로 다른 화면 이동은 하지 않는다(신민용 요청).
            _attach_label_copy(hl, h)
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
            _attach_label_copy(al, a)
            grid.addWidget(al, ri, 2)

            if pso:
                pso_lbl = QLabel("⚽ 승부차기")
                pso_lbl.setStyleSheet("color:#666;font-size:9px;")
                pso_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(pso_lbl, ri, 3)

        lay.addLayout(grid)
        return box