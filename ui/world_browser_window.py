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
    QStyledItemDelegate, QStyle, QMenu
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
        _wb_marks.append(("역대챔스", _time_wb.perf_counter()))
        tabs.addTab(self._build_cwc_tab(), "🌍 역대 클럽 월드컵")
        _wb_marks.append(("역대CWC", _time_wb.perf_counter()))
        tabs.addTab(self._build_wc_tab(), "🌐 역대 월드컵")
        _wb_marks.append(("역대월드컵", _time_wb.perf_counter()))
        tabs.addTab(self._build_nc_tab(), "🎖 역대 네이션스컵")
        _wb_marks.append(("역대네이션스컵", _time_wb.perf_counter()))

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

    def _enable_plain_copy(self, tbl):
        """[2026-08 신설, 신민용 리포트: "복사하면 국기/국가/부수까지 같이
        복사된다"] 이 테이블의 셀을 우클릭(복사 메뉴)하거나 Ctrl+C를 누르면,
        화면에 보이는 장식(국기·국가·부수)이 아니라 _CLEAN_TEXT_ROLE에
        저장해둔 팀명만 클립보드에 복사한다. 그 롤이 없는 셀(연도 등
        원래부터 장식이 없는 셀)은 item.text()를 그대로 쓴다 — 여러
        '역대 기록' 표(리그 우승팀·컵대회·챔스·클럽월드컵)가 전부 같은
        패턴(팀명 + 부가정보)을 쓰므로 한 헬퍼로 공유한다."""
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
        # [2026-08 신설] QWidget 대신 paint()로 행을 그리는 델리게이트 —
        # setItemDelegate(None)으로 되돌리면 예전 위젯 기반 방식으로 복귀.
        self.league_list.setItemDelegate(_GridRowDelegate(self, self.league_list))
        league_header = self._list_header_row([
            ("리그명", self._NAME_COL_W, False),
            ("", 16, False),   # 🔎 매칭 표시 칸 자리(라벨 없음, row와 폭만 맞춤)
            ("등급", self._GRADE_COL_W, True),
            ("국가", self._COUNTRY_COL_W, False),
            ("부수", self._TIER_COL_W, True),
        ])
        split.addWidget(self._wrap_list_with_header(self.league_list, league_header))

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
        self._enable_plain_copy(self.standing_tbl)
        right_lay.addWidget(self.standing_tbl)

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
        right_lay.addWidget(self.standing_split_holder)
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

        right_lay.addLayout(po_row)
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
        q = self.search_box.text().strip() or None
        leagues = wb.search_leagues(continent=cont, country_id=cid, name_query=q, grade=grade)
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
        등급/국가/부수)을 QWidget 없이 _GridRowDelegate가 그릴 수 있는
        스펙 리스트로 표현한다. 폭·색상·굵기 값은 _league_row_widget과
        1:1로 동일하게 맞춰서 시각적으로 동일하게 보이게 했다."""
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
    _LEAGUE_COL_W = 168

    def _league_row_widget(self, lg):
        """리그 목록 한 줄 — 왼쪽부터 [리그명(고정폭)] [등급] [국가] [부수]
        순서의 그리드. [2026-08 재정리, 신민용 리포트: "등급이 오른쪽 벽에
        딱 붙어 시선이 멀리 이동한다", "칸이 안 맞춰져 들쭉날쭉하다"]
        1) 가장 중요한 지표인 등급을 리그명 바로 옆(국가명보다 앞)으로
           당겨서 훑어보기 쉽게 하고,
        2) 칸마다 폭을 고정해 실제 표(그리드)처럼 세로 정렬을 맞추고,
        3) 마지막 칸 뒤에도 여백을 둬서 리스트 오른쪽 벽/스크롤바에
           바짝 붙어 보이지 않게 했다.
        [2026-07] 팀명 검색으로 뜬 결과면(lg['matched_team']이 있으면) 리그명
        칸 툴팁에 그 팀명을 함께 남겨 "왜 이 리그가 검색됐는지" 알 수 있게 한다."""
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
                "경기가 진행된 시즌만 표시됩니다 · 연도 칸(파란색)을 클릭하면 그 시즌 전체 순위를 볼 수 있어요"
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
        self.standing_sub.setText(
            "🔵 파란색 = 승격  ·  🔴 빨간색 = 강등" if (promoted_names or relegated_names) else "")
        self._fill_standing_table(rows, promoted_names=promoted_names, relegated_names=relegated_names)

        self._fill_po_panel(self.po_promo_title, self.po_promo_tbl,
                             wb.get_po_results(lid, year, direction="promotion"))
        self._fill_po_panel(self.po_results_title, self.po_results_tbl,
                             wb.get_po_results(lid, year, direction="relegation"))

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

    def _fill_standing_table(self, rows, promoted_names=None, relegated_names=None):
        promoted_names = promoted_names or set()
        relegated_names = relegated_names or set()
        cols = ["순위", "팀명", "승", "무", "패", "득점", "실점", "득실", "승점"]

        def _row_color(name, rank0):
            # [2026-08 버그수정] 승격/강등/상위 4팀에 안 걸리는 나머지도
            # 반드시 명시적 색을 줘야 한다 — 안 그러면 배경색과 구분 안 되는
            # 기본(검정) 글자색으로 그려져 "존재하지만 안 보이는" 행이 된다.
            if name in relegated_names:
                return QColor("#ff5555")
            if name in promoted_names:
                return QColor("#4da6ff")
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
        for t in range(1, 7):
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
        self.team_detail_title = QLabel("← 왼쪽에서 팀을 선택하세요")
        self.team_detail_title.setStyleSheet("color:#00cc44;font-size:14px;font-weight:bold;")
        right_lay.addWidget(self.team_detail_title)

        self.team_detail_tbl = QTableWidget(0, 5)
        self.team_detail_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.team_detail_tbl.verticalHeader().setVisible(False)
        self.team_detail_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.team_detail_tbl.setHorizontalHeaderLabels(
            ["연도", "리그", "국내컵", "챔피언스리그", "클럽 월드컵"])
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

    def _on_team_selected(self, item):
        tid = item.data(Qt.ItemDataRole.UserRole)
        tname = item.data(Qt.ItemDataRole.UserRole + 1)
        if tid is None:
            return
        self.team_detail_title.setText(f"📋 {tname}  역대 기록")

        tbl = self.team_detail_tbl
        tbl.setRowCount(0)

        hist = wb.get_team_history(tid)
        if not hist:
            tbl.setRowCount(1)
            empty = QTableWidgetItem("기록 없음")
            empty.setForeground(QColor("#666"))
            tbl.setItem(0, 0, empty)
            tbl.setSpan(0, 0, 1, 5)
            return

        tbl.setRowCount(len(hist))
        for i, entry in enumerate(hist):
            year_item = QTableWidgetItem(str(entry["year"]))
            year_item.setForeground(QColor("#ffcc00"))
            f = year_item.font(); f.setBold(True); year_item.setFont(f)
            tbl.setItem(i, 0, year_item)

            lg_txt = entry["league"] or "-"
            lg_item = QTableWidgetItem(lg_txt)
            lg_item.setForeground(QColor(
                "#4da6ff" if "승격" in lg_txt else
                "#ff5555" if "강등" in lg_txt else "#ddd"))
            tbl.setItem(i, 1, lg_item)

            cup_item = QTableWidgetItem(entry["cup"] or "-")
            cup_item.setForeground(QColor("#c48aff" if entry["cup"] else "#555"))
            tbl.setItem(i, 2, cup_item)

            cl_item = QTableWidgetItem(entry["cl"] or "-")
            cl_item.setForeground(QColor("#ffd700" if entry["cl"] else "#555"))
            tbl.setItem(i, 3, cl_item)

            cwc_item = QTableWidgetItem(entry.get("cwc") or "-")
            cwc_item.setForeground(QColor("#4dd0e1" if entry.get("cwc") else "#555"))
            tbl.setItem(i, 4, cwc_item)
        tbl.resizeRowsToContents()

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
        self.cup_country_list.setItemDelegate(_GridRowDelegate(self, self.cup_country_list))
        cup_header = self._list_header_row([
            ("나라명", self._NAME_COL_W, False),
            ("기록", 70, True),
        ])
        split.addWidget(self._wrap_list_with_header(self.cup_country_list, cup_header))

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
        self._enable_plain_copy(self.cup_tbl)
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
        q = self.cup_search_box.text().strip().lower()
        countries = wb.list_countries(cont)
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
        기록유무 배지)을 QWidget 없이 그릴 수 있는 스펙으로 표현. 배지는
        원본과 동일하게 배경색+둥근모서리(bg 필드)로 재현한다."""
        return [
            {"text": f"{c['flag']} {c['name']}", "width": self._NAME_COL_W,
             "color": "#eee" if has_data else "#666", "bold": has_data},
            {"text": "기록 있음" if has_data else "기록 없음", "width": 70,
             "align": Qt.AlignmentFlag.AlignCenter, "size": 10,
             "color": "#00cc44" if has_data else "#666",
             "bg": "#16301c" if has_data else "#262626"},
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
            names = [r["winner"], r["runner_up"], r["third"], r["fourth"]]
            tiers = [r.get("winner_tier"), r.get("runner_up_tier"), r.get("third_tier"), r.get("fourth_tier")]
            vals = [str(r["year"]), r["name"]] + [_with_tier(n, t) for n, t in zip(names, tiers)]
            clean_vals = [None, None] + [n if n not in ("-", "?") else None for n in names]
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if clean_vals[j] and clean_vals[j] != v:
                    cell.setData(_CLEAN_TEXT_ROLE, clean_vals[j])
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
        self._enable_plain_copy(self.cl_tbl)
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
    # 탭2.5: 역대 클럽 월드컵 (2026-07 신설)
    # ─────────────────────────────────────────
    def _build_cwc_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        info = QLabel("ℹ️ 국제대회가 없는 해(4년 주기)마다 대륙별 챔스 성적으로 32팀이 선발됩니다.")
        info.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(info)

        self.cwc_tbl = QTableWidget(0, 0)
        self.cwc_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cwc_tbl.verticalHeader().setVisible(False)
        self.cwc_tbl.cellDoubleClicked.connect(self._open_cwc_detail)
        self._enable_plain_copy(self.cwc_tbl)
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
            lay.addWidget(self._build_groups_grid(groups, team_based))

        league_standings = detail.get("league_standings") or []
        if league_standings:
            lay.addWidget(self._section_label("⚽ 리그 스테이지"))
            lay.addWidget(self._build_league_standings_table(
                league_standings, detail.get("continent")))

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

    def _build_league_standings_table(self, standings, continent=None):
        """[2026-07 신설] 챔스 스위스 방식 리그 스테이지 전체 순위표(단일 표).
        조별 카드 대신 순위·팀명·승무패·득실·승점을 한 표로 쭉 보여준다.

        [2026-07 추가, 신민용 요청: "경기 일정 화면처럼 직행/플레이오프
        색깔 구분이 역대 기록에도 있으면 좋겠다"] schedule_window.py의
        진행 중 화면과 완전히 같은 색상 체계(초록=직행/주황=플레이오프/
        회색=탈락권)를 그대로 재사용해서 통일감을 준다."""
        from PyQt6.QtGui import QColor
        COLOR_ADVANCE = QColor("#00cc44")
        COLOR_THIRD   = QColor("#ffaa00")
        COLOR_ELIM    = QColor("#888888")

        direct_cut = playoff_cut = None
        if continent:
            from champions_engine import CL_DIRECT_CUT_BY_CONTINENT, CL_PLAYOFF_POOL_BY_CONTINENT
            direct_cut = CL_DIRECT_CUT_BY_CONTINENT.get(continent)
            playoff_pool = CL_PLAYOFF_POOL_BY_CONTINENT.get(continent)
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
            "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")

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
                tbl.setItem(i, j, item)
        tbl.setFixedHeight(tbl.verticalHeader().defaultSectionSize() * len(standings) + 32)
        lay.addWidget(tbl)

        if direct_cut is not None:
            hint = QLabel(f"🟢1~{direct_cut}위 직행  🟡{direct_cut+1}~{playoff_cut}위 플레이오프  "
                          f"⬜{playoff_cut+1}위 이하 광탈")
            hint.setStyleSheet("color:#888;font-size:10px;padding:4px 2px 0 2px;")
            lay.addWidget(hint)
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