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
    QStyledItemDelegate, QStyle, QMenu, QMessageBox, QSpinBox, QCompleter,
    QButtonGroup
)
from PyQt6.QtCore import Qt, QTimer, QRect, QSize
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QPainter,
                          QBrush, QPen, QShortcut, QKeySequence, QIntValidator)

import world_browser as wb
from database import (get_conn, get_game_start_year, TEAM_POSITIONS,
                       get_ai_player_custom_name, set_ai_player_custom_name,
                       get_ai_player_custom_names)
from constants import ai_player_code, FORMATION_SLOTS
from game_engine import is_hard_mode
import power_ranking as pr
# [2026-08 신설, 신민용 요청: "내가 분명 포메이션 형태로 보내달라 했는데
# 왜 없어?"] 국가대표 스쿼드/팀 시즌 라인업을 실제 포메이션 화면과 똑같은
# 초록 피치 배치로 그리기 위해, 그 화면(_FormationCanvas)이 쓰는 순수
# 배치 헬퍼만 가져다 쓴다 — 이 함수들은 Qt 위젯 상태에 의존하지 않는
# 순수 함수(포지션 문자열/좌표 계산만)라 그대로 재사용해도 안전하다.
# formation_widget.py는 이 모듈을 최상단에서 import하지 않으므로(내부
# 함수 안에서만 지연 import) 순환 임포트 문제가 없다.
from ui.formation_widget import (
    _row_key, _row_priority, _pos_x_order, _pos_color, open_bulk_rename_dialog)

# [2026-08 신설, 신민용 리포트: "복사하면 국기/국가/부수까지 같이 복사된다,
# 팀명만 복사되게 해달라"] 셀 화면 텍스트("🇺🇸 토론토 FC (미국)", "보루시아
# 도르트문트 (1부)")와 실제로 클립보드에 복사할 "깨끗한" 텍스트를 분리해
# 저장하기 위한 전용 데이터 롤. 기존에 이미 UserRole(연도/시즌, team_id 등)을
# 여러 곳에서 쓰고 있어서 충돌을 피하려고 +50 오프셋을 둔다.
_CLEAN_TEXT_ROLE = Qt.ItemDataRole.UserRole + 50
# [2026-08 신설, 최적화] 선수 검색 목록의 각 줄이 어떤 검색 결과
# (dict)에서 만들어졌는지 그대로 보관하는 롤 — 이름만 바뀐 경우
# 목록 전체를 다시 조회하지 않고 그 줄만 다시 그리기 위해 쓴다.
_PLAYER_ROW_DATA_ROLE = Qt.ItemDataRole.UserRole + 51


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

    # [2026-08 확장, 신민용 리포트: "포메이션 화면 선수 정보 표는 클릭하고
    # Ctrl+C 해도 복사가 안 된다"] ui/formation_widget.py.PlayerStatPopup의
    # 표들은 WASD 단축키가 표의 "타이핑해서 셀 찾기" 기능에 가로채이지
    # 않도록 setFocusPolicy(NoFocus)를 일부러 걸어둔다 — 그러면 위
    # WidgetShortcut(표 자신이 포커스를 가져야 작동)은 그 표에서 절대
    # 발동하지 않는다. 그런 화면은 표에 단축키를 못 걸고 대신 다이얼로그
    # 레벨에 하나만 걸어 "지금 선택된 셀이 있는 표"를 찾아 복사해야
    # 하는데, 그러려면 이 함수가 쓰는 것과 같은 클린텍스트 복사 로직이
    # 필요하다 — _copy_selected를 반환해서 그쪽에서 그대로 재사용하게
    # 한다(기존 호출부는 반환값을 안 받아도 그만이라 하위 호환 그대로).
    return _copy_selected


def _calc_static_pitch_positions(slots, w, h):
    """[2026-08 신설] ui/formation_widget.py의 _FormationCanvas._calc_positions와
    완전히 동일한 배치 알고리즘을 그대로 복제한 것 — 라이브 포메이션 화면과
    똑같이 위(공격)→아래(GK) 세로 행으로 쌓는다.

    [2026-08 좌우로 바꿨다가 재수정, 신민용 리포트: "비율을 1번(라이브
    포메이션 화면)처럼 만들어달라니까 왜 2번(좌우로 눕힌 버전)처럼 만든거?"]
    한 번은 "좌우로 바꿔달라"는 요청을 받아 축 자체를 스왑했었는데, 실제
    라이브 화면(_FormationCanvas)은 세로(위=공격~아래=GK)로 그려서 그
    스왑이 오히려 실제 화면과 달라지는 결과였다 — 축은 원래 방식(세로)
    그대로 되돌리고, 실제 문제였던 "가로로 길쭉하게 눌린 비율"은
    _StaticPitchView 쪽 위젯 크기 제약(최대 폭 고정)으로 따로 고쳤다.
    그 메서드는 self._circle_d에 결과를 저장하는데 여기는 인스턴스가 없는
    정적 위젯이라 (positions, circle_d) 튜플로 함께 반환한다. 반환 튜플의
    4번째 값(slot_idx)은 원본 slots 리스트에서의 인덱스 — 화면 표시용으로
    행(GK/DEF/MID/MID2/ATK)별로 재정렬된 순서와는 다르므로, 선수 매칭은
    항상 이 slot_idx로 해야 한다."""
    rows = {}; row_order = []
    for idx, pos in enumerate(slots):
        k = _row_key(pos)
        if k not in rows: rows[k] = []; row_order.append(k)
        rows[k].append((idx, pos))
    sorted_rows = sorted(row_order, key=lambda x: _row_priority(x))
    total = len(sorted_rows); result = []
    max_row_cnt = max((len(v) for v in rows.values()), default=1)
    row_h = (h - 32) / max(1, total)
    col_w = w / (max_row_cnt + 1)
    circle_d = int(max(16, min(48, row_h * 0.82, col_w * 0.78)))
    for ri, rk in enumerate(sorted_rows):
        poss = sorted(rows[rk], key=lambda t: _pos_x_order(t[1]))
        cnt = len(poss)
        ry = 16 + int((ri + 0.5) * (h - 32) / total)
        for ci, (idx, pos) in enumerate(poss):
            result.append((int((ci + 1) * w / (cnt + 1)), ry, pos, idx))
    return result, circle_d


class _StaticPitchView(QWidget):
    """[2026-08 신설, 신민용 요청: "내가 분명 포메이션 형태로 보내달라
    했는데 왜 없어?"] 국가대표 스쿼드(그 대회 주전)와 팀 시즌 라인업을
    라이브 포메이션 화면(_FormationCanvas)과 같은 초록 피치 + 원형 마커
    스타일로 그리는 읽기 전용 정적 위젯. 라이브 캔버스는 현재 게임
    상태(부상/선택 하이라이트/드래그/패널 연동 등)에 강하게 결합돼 있어
    그대로 재사용하기 어려워, 배치 계산 알고리즘만 그대로 복제한 얇은
    전용 위젯을 새로 만들었다. 신민용이 명시적으로 "OVR은 필요없어"라고
    했으므로 이름과 포지션만 표시한다.

    slot_players: slots와 같은 길이의 리스트, 각 원소는
    (slot_position_str, display_name, player_id_or_None).

    slots: [2026-08 확장] 국제대회 스쿼드는 FORMATION_SLOTS에 등록된
    이름 있는 포메이션이 아니라 intl_engine._INTL_MATCHDAY_STARTER_POS
    같은 고정 11자리 포지션 리스트를 쓰므로, formation 이름 대신 슬롯
    포지션 리스트를 직접 넘길 수 있게 했다. slots를 넘기면 formation은
    화면에 표시되는 라벨 용도로만 쓰이고 실제 배치는 slots 기준."""

    def __init__(self, formation, slot_players, on_click=None, parent=None, slots=None):
        super().__init__(parent)
        self.formation = formation if formation in FORMATION_SLOTS else "4-4-2"
        self._explicit_slots = slots
        self.slot_players = slot_players
        self._on_click = on_click
        self._positions_xy = []
        self._circle_d = 40
        # [2026-08 신설, 신민용 리포트: "비율을 1번(라이브 포메이션
        # 화면)처럼 만들어달라니까 왜 2번처럼 만든거?"] 이 위젯이 놓이는
        # 곳(국가/팀 검색의 펼침 카드)은 라이브 포메이션 화면과 달리 옆에
        # 명단 패널이 없어 위젯이 카드 전체 폭(다이얼로그 폭만큼)으로
        # 쭉 늘어나 버린다 — 그러면 세로 행 간격이 극단적으로 눌려 얇고
        # 긴 띠처럼 보인다. 라이브 화면(대략 세로가 가로보다 긴 비율)과
        # 비슷하게 보이도록 폭에 상한을 걸어 늘어나지 않게 한다.
        self.setMinimumSize(360, 420)
        self.setMaximumWidth(460)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(420, 460)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width(); h = self.height()

        painter.fillRect(0, 0, w, h, QBrush(QColor("#1a3a1a")))
        painter.setPen(QPen(QColor("#2a5a2a"), 1))
        painter.drawRect(12, 8, w - 24, h - 16)
        painter.drawLine(12, h // 2, w - 12, h // 2)
        cc_d = max(20, min(48, min(w, h) // 7))
        painter.drawEllipse(w // 2 - cc_d // 2, h // 2 - cc_d // 2, cc_d, cc_d)

        slots = self._explicit_slots or FORMATION_SLOTS.get(self.formation, FORMATION_SLOTS["4-4-2"])
        positions_xy, circle_d = _calc_static_pitch_positions(slots, w, h)
        self._positions_xy = positions_xy
        self._circle_d = circle_d

        for i, (px, py, pos, slot_idx) in enumerate(positions_xy):
            sp = self.slot_players[slot_idx] if slot_idx < len(self.slot_players) else None
            pid = sp[2] if sp else None
            name = (sp[1] if sp else None) or "(공석)"
            d = circle_d; r = d // 2
            painter.setBrush(QBrush(QColor(_pos_color(pos))))
            painter.setPen(QPen(QColor("#000"), 1))
            painter.drawEllipse(px - r, py - r, d, d)
            painter.setPen(QPen(QColor("#fff")))
            f = QFont(); f.setPointSize(max(6, min(10, d // 5))); f.setBold(True)
            painter.setFont(f)
            painter.drawText(px - r, py - r, d, d, Qt.AlignmentFlag.AlignCenter, pos[:2])
            f2 = QFont(); f2.setPointSize(max(6, min(9, d // 6)))
            painter.setFont(f2)
            painter.setPen(QPen(QColor("#ddd" if pid is not None else "#666")))
            name_w = max(48, d + 16)
            painter.drawText(px - name_w // 2, py + r + 2, name_w, 16,
                              Qt.AlignmentFlag.AlignCenter, name[:6])
        painter.end()

    def mousePressEvent(self, event):
        if not self._on_click:
            return
        mx, my = event.position().x(), event.position().y()
        hit_r2 = max(144, int(self._circle_d * 0.55) ** 2)
        for px, py, _pos, slot_idx in self._positions_xy:
            if (mx - px) ** 2 + (my - py) ** 2 < hit_r2:
                sp = self.slot_players[slot_idx] if slot_idx < len(self.slot_players) else None
                pid = sp[2] if sp else None
                if pid is not None:
                    self._on_click(pid)
                return


def _build_squad_roster_panel(starters, bench, on_click=None, height=460):
    """[2026-08 재작업, 신민용 리포트: "좌측에 포메이션을 저렇게 박으면
    우측에 후보 선수들의 이름을 나열해야지 — 주전들은 초록색으로, 후보들은
    아래에 색이 없는 원래 상태로 나열"] 예전엔 후보만 피치 아래에 칩으로
    나열했는데(_build_bench_chip_row), 이제 라이브 포메이션 화면(ui/
    formation_widget.py의 _TeamPanel._make_player_button/_make_group_
    header)과 완전히 같은 구성 — 주전 헤더+녹색 목록, 후보 헤더+무채색
    목록 — 을 피치 오른쪽에 배치한다. 색상 값도 그 화면과 정확히 동일한
    값(#1e4a1e 녹색/#1c1c1c 무채색)을 그대로 가져왔다.

    [2026-08 재작업, 신민용 리포트: "칸이 넓은데 좌측엔 주전, 우측엔
    후보로 2줄(2칸)로 나눠서 표시해줘"] 처음엔 주전 헤더+목록, 후보
    헤더+목록을 세로로 하나씩 쌓았는데(폭이 넓은 카드에서 선수 한 명당
    한 줄씩만 차지해 공간 낭비) — 이제 왼쪽 칸엔 주전, 오른쪽 칸엔
    후보를 나란히 두 칼럼으로 배치해 같은 폭을 두 배 효율적으로 쓴다.

    starters/bench의 각 원소는 position/slot/pos, display_name/name,
    id 키 중 있는 걸 유연하게 찾는다(국가 스쿼드·팀 라인업 두 데이터
    형태를 모두 받기 위함). 스크롤 영역으로 감싸 피치와 높이를 맞춘다
    (후보가 많으면 카드 전체가 한없이 길어지는 것 방지)."""
    panel = QWidget()
    cols_lay = QHBoxLayout(panel)
    cols_lay.setContentsMargins(0, 0, 4, 0)
    cols_lay.setSpacing(10)

    def _hdr(text, count):
        lbl = QLabel(f"{text} ({count})")
        lbl.setStyleSheet(
            "color:#5aa9ff;font-size:13px;font-weight:bold;"
            "padding:4px 2px 2px 2px;border-bottom:1px solid #2a2a2a;")
        return lbl

    def _row(p, is_starter):
        pos = p.get("position") or p.get("slot") or p.get("pos") or "-"
        name = p.get("display_name") or p.get("name") or "-"
        pid = p.get("id")
        lbl = QLabel(f"{pos}  {name}")
        if is_starter:
            style = ("background:#1e4a1e;color:#eaffea;border:1px solid #3fae3f;"
                      "border-radius:4px;padding:5px 8px;font-size:11px;font-weight:bold;")
        else:
            style = ("background:#1c1c1c;color:#aaa;border:1px solid #333;"
                      "border-radius:4px;padding:5px 8px;font-size:11px;")
        lbl.setStyleSheet(style)
        if pid is not None and on_click:
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.mousePressEvent = lambda _e, _pid=pid: on_click(_pid)
        return lbl

    def _build_column(items, is_starter, label):
        col = QWidget()
        vlay = QVBoxLayout(col)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(4)
        if items:
            vlay.addWidget(_hdr(label, len(items)))
            for p in items:
                vlay.addWidget(_row(p, is_starter))
        vlay.addStretch(1)
        return col

    cols_lay.addWidget(_build_column(starters, True, "주전"), 1)
    cols_lay.addWidget(_build_column(bench, False, "후보"), 1)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(panel)
    scroll.setFixedHeight(height)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setStyleSheet("QScrollArea{background:transparent;} QWidget{background:transparent;}")
    return scroll


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

# [2026-08 신설, 11순위] 슈퍼컵 전용 색 — "경기 일정에 슈퍼컵이 버건디
# 색상으로 표시되어야 한다"(신민용) — 세계기록실 탭 우승 강조색과
# schedule_window.py의 일정 표시색이 둘 다 이 상수를 참조하게 해서,
# 나중에 색을 바꿔야 하면 여기 한 곳만 고치면 된다.
BURGUNDY = "#800020"

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


def apply_custom_name_live_to_browser(player_id: int):
    """[2026-08 신설, 신민용 요청: "포메이션에서 선수 이름을 바꾸면
    선수 검색에도 바로 반영되게"] _open_ai_rename_dialog가 "선수
    검색"에서 이름을 바꿨을 때 formation_widget.apply_custom_name_live로
    열려 있는 포메이션 화면에 즉시 반영하던 것의 반대 방향 — 포메이션
    (PlayerStatPopup)에서 이름을 바꿨을 때 지금 열려 있는 모든
    세계 축구 기록실 창의 "선수 검색" 목록/상세 패널에 새 이름을
    즉시 반영한다. DB(set_ai_player_custom_name)는 formation_widget
    쪽에서 이미 저장을 끝낸 뒤 호출하므로, 여기서는 화면 갱신만 한다."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        if not isinstance(w, WorldBrowserWindow):
            continue
        try:
            # [2026-08 최적화] 예전엔 무조건 _refresh_player_list()로 최대
            # 300명을 다시 검색했다 — 선수 검색 탭을 아직 열어본 적도
            # 없는 창에서까지 그랬다. 그 줄 하나만 갱신하고, 이름 관련
            # 필터가 걸려 있어 목록 구성 자체가 바뀔 수 있을 때만 예전
            # 방식으로 전체를 다시 조회한다.
            if not w._apply_rename_to_player_list(player_id):
                w._refresh_player_list()
            if getattr(w, "_player_detail_pid", None) == player_id:
                w._show_player_detail(player_id)
            _rr = getattr(w, "_player_recent_row", None)
            if _rr is not None:
                _rr.refresh()   # [2026-08 신설] 최근 검색 버튼 글자도 새 이름으로
        except (AttributeError, RuntimeError):
            pass  # 선수 검색 탭 미생성 / 창이 이미 닫혀 C++ 객체가 삭제된 경우


class WorldBrowserWindow(QDialog):
    def __init__(self, parent=None, open_player_id=None, nav=None, panel=None):
        """open_player_id: [2026-08 신설, 신민용 요청: "포메이션에서 선수를
        누르면 세계 기록실에서 그 선수를 눌렀을 때 뜨는 우측 패널이 바로
        떠야 한다"] 넘기면 창이 뜨자마자 "선수 검색" 탭으로 전환하고 그
        선수의 상세를 바로 연다(open_to_player 참고) — 검색해서 찾아
        들어가는 과정을 생략한다.

        nav/panel: [2026-08 재추가, 신민용 요청: "그렇게 세계기록실이
        뜨는 건 유지하되, WASD로 옆 선수/반대팀으로 넘어가는 기능은
        되살려달라"] 포메이션 화면(ui/formation_widget.py의
        FormationWidget/_TeamPanel)에서 열렸을 때만 넘어온다 —
        formation_widget.py._open_world_browser_for_player 참고. 독립적
        으로(메인 메뉴, 팀 검색 화면 등에서) 연 창은 둘 다 None이라
        keyPressEvent가 그냥 평범한 다이얼로그 기본 동작으로 남는다."""
        super().__init__(parent)
        self._fm_nav = nav
        self._fm_panel = panel
        self._fm_player_id = None
        # [2026-08 신설] 아래 keyPressEvent가 W/A/S/D를 받으려면 이 창
        # 자신이 포커스를 들고 있어야 한다(내부 표/입력칸에 초점이 있으면
        # 거기로 먼저 감) — PlayerStatPopup(StrongFocus)과 동일한 정책.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        # [2026-08 재수정, 신민용 요청: "가로 화면을 모니터에 맞춰서
        # 커지게 해달라"] 예전엔 요청 크기 자체가 1600×700 고정이라,
        # _clamp_and_resize는 "화면이 이보다 작을 때만" 줄여줬을 뿐
        # 화면이 훨씬 큰 모니터에서도 항상 1600×700 그대로 떠서 화면
        # 한복판에 작게 떠 있었다 — 요청 크기 자체를 작업 영역 비율
        # (가로 92%, 세로 88%)로 계산해서, 최소 1600×700은 보장하되
        # (작은 화면은 기존처럼 _clamp_and_resize가 그 밑으로 잘라줌)
        # 큰 모니터에서는 그만큼 더 커지게 한다.
        _screen = self.screen() or QGuiApplication.primaryScreen()
        if _screen:
            _avail = _screen.availableGeometry()
            _target_w = max(1600, int(_avail.width() * 0.92))
            _target_h = max(700, int(_avail.height() * 0.88))
        else:
            _target_w, _target_h = 1600, 700
        _clamp_and_resize(self, _target_w, _target_h)

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

        # [2026-08 v3.5 재수정, 신민용 리포트: "세계기록실 열 때 끊기면서
        # 열린다"] 원인이 계측(위 주석의 [PERF-WORLD] 로그)으로 이미
        # 잡혀 있었다 — 탭 13개를 전부 __init__ 안에서 동기로 그린 뒤에야
        # 창이 화면에 뜨는 구조라, 그 시간만큼 앱 전체가 얼어붙어 보인다.
        # 리그검색/팀검색/컵대회검색 3개는 showEvent 직후 _ensure_all_
        # lists_fit()이 self.league_list/team_list/cup_country_list를
        # 바로 참조하므로 그대로 즉시(eager) 빌드하고, 나머지 10개
        # (역대 대회 기록·국가검색·파워랭킹 — 처음 열 때 바로 안 봐도
        # 되는 탭들)는 플레이스홀더만 넣어뒀다가 사용자가 그 탭을 실제로
        # 클릭하는 순간에만 빌드한다(_lazy_show_wb_tab). 창이 뜨는 데
        # 걸리는 시간이 "탭 13개 빌드"에서 "탭 3개 빌드"로 줄어든다.
        import time as _time_wb
        _wb_t0 = _time_wb.perf_counter()
        _wb_marks = []

        tabs.addTab(self._build_league_tab(), "🔍 리그 검색")
        _wb_marks.append(("리그검색", _time_wb.perf_counter()))
        tabs.addTab(self._build_team_tab(), "🏟 팀 검색")
        _wb_marks.append(("팀검색", _time_wb.perf_counter()))
        tabs.addTab(self._build_cup_tab(), "🎖 컵대회 검색")
        _wb_marks.append(("컵대회검색", _time_wb.perf_counter()))

        self._wb_lazy_builders = {}   # {tab_index: (builder_fn, label)}
        _lazy_tabs = [
            (self._build_cl_tab,           "🏆 역대 챔피언스리그"),
            (self._build_el_tab,           "🥈 역대 유로파리그"),
            (self._build_ecl_tab,          "🥉 역대 컨퍼런스리그"),
            (self._build_sc_tab,           "🏵 역대 슈퍼컵"),
            (self._build_cwc_tab,          "🌍 역대 클럽 월드컵"),
            (self._build_wc_tab,           "🌐 역대 월드컵"),
            (self._build_nc_tab,           "🎖 역대 네이션스컵"),
            (self._build_region_tab,       "🌏 역대 지역컵"),
            (self._build_country_tab,      "🌍 국가 검색"),
            (self._build_power_ranking_tab,"📊 파워랭킹"),
            (self._build_player_search_tab,"🔎 선수 검색"),
        ]
        self._player_search_tab_idx = None
        for builder, label in _lazy_tabs:
            placeholder = QWidget()
            idx = tabs.addTab(placeholder, label)
            self._wb_lazy_builders[idx] = (builder, label)
            # [버그수정] `builder is self._build_player_search_tab`는 항상
            # False였다 — self.xxx로 바운드 메서드에 접근할 때마다 매번
            # 새 MethodType 객체가 생겨서, 같은 메서드를 가리켜도 `is`
            # 동일성 비교는 성립하지 않는다(값은 같아도 다른 객체 —
            # `a == b`는 True, `a is b`는 False). 그래서
            # _player_search_tab_idx가 한 번도 안 채워졌고, open_to_player가
            # "선수 검색" 탭으로 전환을 못 해서 그 탭이 아직 한 번도 안
            # 열린 상태에서 부르면(_show_player_detail이 참조하는
            # player_detail_tbl 등 위젯 자체가 아직 없음) AttributeError로
            # 죽었다. 라벨 문자열로 비교하면 이 문제가 없다.
            if label == "🔎 선수 검색":
                self._player_search_tab_idx = idx
        tabs.currentChanged.connect(self._lazy_show_wb_tab)
        _wb_marks.append(("(나머지 10개 지연 배치)", _time_wb.perf_counter()))

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

        if open_player_id is not None:
            self.open_to_player(open_player_id)

    def open_to_player(self, player_id):
        """[2026-08 신설] 포메이션 화면(선수 클릭 → 세계 기록실 열기)과
        "스쿼드 전원 기록 복사" 기능이 공유하는 진입점 — "선수 검색"
        탭으로 전환하고(아직 안 지어졌으면 이 시점에 지어짐,
        _lazy_show_wb_tab) 그 선수의 상세를 바로 연다. 창을 보여주지
        않고(show() 없이) 이 메서드만 반복 호출해도 안전하므로, 화면에
        띄우지 않는 "헤더리스" 인스턴스를 만들어 여러 선수를 순회하며
        _player_copy_*(복사 버튼이 쓰는 것과 같은 값)만 뽑아내는 용도로도
        쓸 수 있다."""
        if self._player_search_tab_idx is not None:
            self.tabs.setCurrentIndex(self._player_search_tab_idx)
        self._show_player_detail(player_id)
        self._fm_player_id = player_id   # [2026-08 신설] keyPressEvent의 W/S 기준값

    def keyPressEvent(self, event):
        """[2026-08 신설, 신민용 요청: "포메이션 화면 선수 클릭 시 세계
        기록실이 뜨는 건 유지하되, WASD로 옆 선수/반대팀으로 빠르게
        넘어가는 기능은 되살려달라"] ui/formation_widget.py.
        PlayerStatPopup.keyPressEvent와 완전히 동일한 규칙 — 이 창이
        포메이션 화면에서 열렸을 때(self._fm_nav/self._fm_panel 있음)만
        동작하고, 독립적으로 연 창은 기존 QDialog 기본 동작 그대로다.

        - W/S: self._fm_panel의 "주전(N)→후보(M)" 전체 순서에서 한 칸
          위/아래 선수로 전환(맨 끝에서 반대쪽 끝으로 순환).
        - A: 지금 상대팀(우측)을 보고 있을 때만 내 팀(좌측) 주전 맨 위
          선수로 전환.
        - D: 지금 내 팀(좌측)을 보고 있을 때만 상대팀(우측) 주전 맨 위
          선수로 전환.
        모든 전환은 nav.open_player_popup()을 다시 거친다 — 다음 선수도
        실제 AI 레코드(id>=-1)면 이 창이 그대로 재사용되고(open_to_player
        가 다시 불려 _fm_player_id도 같이 갱신됨), 국제대회 가상 폴백
        선수(id<-1)면 자연스럽게 PlayerStatPopup으로 넘어간다."""
        if self._fm_nav is None or self._fm_panel is None:
            return super().keyPressEvent(event)

        key = event.key()
        if key in (Qt.Key.Key_W, Qt.Key.Key_S):
            roster = self._fm_panel.get_full_roster_ordered()
            ids = [p.get("id") for p in roster]
            if not roster or self._fm_player_id not in ids:
                return super().keyPressEvent(event)
            idx = ids.index(self._fm_player_id)
            new_idx = (idx - 1) % len(roster) if key == Qt.Key.Key_W else (idx + 1) % len(roster)
            self._fm_nav.open_player_popup(roster[new_idx], self._fm_panel)
            return

        if key == Qt.Key.Key_A and self._fm_panel.is_opponent:
            target = self._fm_nav.my_panel
            starters = target.get_starters_ordered()
            if starters:
                self._fm_nav.open_player_popup(starters[0], target)
            return

        if key == Qt.Key.Key_D and not self._fm_panel.is_opponent:
            target = self._fm_nav.opp_panel
            starters = target.get_starters_ordered()
            if starters:
                self._fm_nav.open_player_popup(starters[0], target)
            return

        super().keyPressEvent(event)

    def _lazy_show_wb_tab(self, idx):
        """[2026-08 v3.5 신설] 지연 배치해둔 탭을 처음 클릭하는 순간에만
        실제로 빌드해서 그 자리(같은 인덱스)에 끼워 넣는다 — 이미 빌드된
        탭이면 아무 것도 안 한다."""
        pending = self._wb_lazy_builders.get(idx)
        if not pending:
            return
        builder, label = pending
        del self._wb_lazy_builders[idx]
        import time as _time_wb
        _t0 = _time_wb.perf_counter()
        real_widget = builder()
        _elapsed = _time_wb.perf_counter() - _t0
        if _elapsed >= 0.05:
            print(f"[PERF-WORLD] 지연 탭 '{label}' 빌드 {_elapsed:.3f}s")
        old_widget = self.tabs.widget(idx)
        self.tabs.blockSignals(True)
        self.tabs.removeTab(idx)
        self.tabs.insertTab(idx, real_widget, label)
        self.tabs.setCurrentIndex(idx)
        self.tabs.blockSignals(False)
        old_widget.deleteLater()

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
        self._ensure_tab_bar_fits()

    def _ensure_tab_bar_fits(self):
        """[2026-08 신설, 신민용 리포트 3번째: "국가 검색 탭 옆에 스크롤
        화살표(◀▶)가 왜 있어야 하냐 — 모니터가 넓으면 창을 그만큼 키워서
        탭이 다 보이게 해야지, 저 화살표는 창 작은 컴퓨터를 위해 남겨두는
        용도지 화면 넓은데도 뜰 이유가 없잖아"] QTabWidget은 탭 라벨을
        전부 늘어놓은 폭(tabBar().sizeHint())이 지금 탭 영역 폭보다 넓으면
        자동으로 스크롤 화살표를 띄운다 — 파워랭킹 탭 안 내용(스플리터)
        폭만 맞추던 기존 _pr_ensure_window_width와는 별개로, '탭 제목들
        전체가 한 줄에 다 들어가는 폭'도 창 크기에 반영해야 화살표가
        안 뜬다. 화면(작업 영역)보다 커지면 그 지점부터는 화살표가
        남아있는 게 맞다(신민용이 명시적으로 "작은 컴퓨터를 위해 남겨야
        한다"고 확인) — _clamp_and_resize와 동일하게 화면 안으로 잘라
        적용한다."""
        bar = self.tabs.tabBar()
        bar_needed = bar.sizeHint().width()
        bar_have = self.tabs.width()
        if bar_needed <= bar_have:
            return  # 이미 화살표 없이 다 보임
        extra = bar_needed - bar_have
        needed_w = self.width() + extra + 20
        screen = QGuiApplication.primaryScreen()
        max_w = screen.availableGeometry().width() - 40 if screen else needed_w
        new_w = min(needed_w, max_w)
        if new_w > self.width():
            self.resize(new_w, self.height())

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
    def _build_recent_search_row(self, kind, search_box, list_widget, name_from_item_fn, select_fn,
                                  refresh_fn=None, debounce_timer=None, label_fn=None):
        """search_box: 이 탭의 QLineEdit(검색창). list_widget: 이 탭의
        QListWidget. name_from_item_fn(item): 리스트 항목에서 "깨끗한"
        이름(국기/등급 등 장식 없이)을 뽑아내는 함수. select_fn(item):
        그 항목을 실제로 선택했을 때 쓰는 기존 핸들러(_on_league_selected 등)
        — 최근 검색 버튼을 클릭하면 이 함수를 그대로 다시 호출해서 "그
        항목을 클릭해서 들어간 것"과 동일하게 동작하게 한다.
        refresh_fn/debounce_timer: 이 탭의 검색창 디바운스 타이머와, 타이머가
        만료됐을 때 호출하는 리스트 재구성 함수(예: _refresh_country_search_list).

        [2026-08 수정, 신민용 리포트: "내가 입력한 것보다 클릭해서 들어간
        애들이 뜨는 게 맞다 — '치주'라고 쳐서 '치주물루 유나이티드 FC'를
        클릭해서 들어가면 최근 검색엔 '치주물루 유나이티드 FC'가 남아야지
        '치주'가 남으면 안 된다"] 예전엔 검색창에 타이핑을 멈춘 시점(디바운스
        만료)에 그 입력 문자열 자체를 기록했다 — 이제는 그 시점엔 아무것도
        기록하지 않고, 실제로 리스트에서 항목을 클릭해 들어갔을 때(각 탭의
        _on_*_selected 안)만 그 항목의 정식 이름을 기록한다. 리그/팀/국가
        3곳 다 같은 규칙.

        [2026-08 재수정, 신민용 리포트: "최근 검색을 한 번 누르면 검색창엔
        이름이 채워지고 왼쪽 목록도 그 이름만 남는데, 오른쪽 상세 화면은
        그대로다 — 한 번 더 눌러야 오른쪽이 바뀐다"] 원인: search_box.setText(q)는
        textChanged를 거쳐 250ms 디바운스 타이머만 재시작할 뿐, list_widget은
        그 타이머가 만료돼야 실제로 다시 채워진다 — 그런데 바로 다음 줄의
        "이름이 일치하는 항목 찾기" 루프는 그 250ms를 기다리지 않고 곧바로
        (아직 안 걸러진, 즉 이전 필터 상태 그대로인) list_widget을 뒤지다 보니
        찾는 이름이 그 안에 없어 select_fn이 아예 호출되지 않는 경우가
        흔했다(그래서 오른쪽이 안 바뀜) — 그 뒤 250ms가 지나 디바운스가
        list_widget을 갱신해서 왼쪽엔 정상적으로 그 이름만 남았던 것.
        이제 refresh_fn이 주어지면 대기 중인 디바운스를 멈추고 그 자리에서
        즉시 동기적으로 리스트를 다시 채운 뒤에 이름을 찾으므로, 첫 클릭
        만으로 왼쪽 목록도 오른쪽 상세도 한 번에 정확히 갱신된다."""
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
            # 검색창에 그 이름을 채우고, 대기 중이던 디바운스를 멈춘 뒤
            # 목록을 그 자리에서 즉시 다시 채운다(비동기 250ms를 기다리지
            # 않음) — 그래야 바로 아래에서 정확히 일치하는 항목을 찾아
            # 실제로 클릭해 들어간 것처럼 select_fn을 호출할 수 있다.
            search_box.setText(q)
            if debounce_timer is not None:
                debounce_timer.stop()
            if refresh_fn is not None:
                refresh_fn()
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
                # [2026-08 신설, 신민용 요청: "AI7PTQ를 클릭해 둔 뒤 그 선수
                # 이름을 카린으로 바꾸면 최근 검색의 AI7PTQ도 카린으로
                # 바뀌어야 한다"] 선수 탭은 이제 "그때 화면에 뜬 글자"가
                # 아니라 선수 id("#123" 형태)를 저장한다 — 여기서 그 id를
                # 지금의 표시 이름으로 풀어서 버튼에 쓴다(label_fn). 다른
                # 탭이나 예전에 문자열로 저장된 항목은 label_fn이 그대로
                # 돌려주므로 예전과 동일하게 보인다.
                label = label_fn(q) if label_fn is not None else q
                if not label:
                    continue   # 더 이상 풀 수 없는 항목(삭제된 선수 등)은 조용히 건너뜀
                b = QPushButton(label)
                b.setToolTip(label)
                # 같은 이유(Enter → autoDefault 버튼 오발동 방지)로 여기도 끈다.
                b.setAutoDefault(False)
                b.setDefault(False)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet(
                    "QPushButton{background:#232323;color:#aad4ff;border:1px solid #3a3a3a;"
                    "border-radius:10px;padding:2px 10px;font-size:11px;}"
                    "QPushButton:hover{border-color:#00cc44;color:#fff;}")
                b.clicked.connect(lambda _checked=False, qq=label: _pick(qq))
                btn_box.addWidget(b)

        def _reset():
            wb.clear_recent_searches(kind)
            _refresh()

        reset_btn.clicked.connect(_reset)
        row.refresh = _refresh
        _refresh()
        return row

    def _record_recent_selection(self, kind, name, recent_row_attr, stored=None):
        """리스트에서 항목을 실제로 클릭해 들어갔을 때 호출 — 그 항목의
        정식 이름을 kind별 최근 검색 기록 맨 앞에 남긴다.

        [2026-08 신설] stored를 주면 그 값을 대신 저장한다 — 선수 탭이
        표시 이름 대신 선수 id("#123")를 남기기 위한 것. 이렇게 해야
        (1) 나중에 이름을 바꿔도 최근 검색 버튼 글자가 같이 바뀌고,
        (2) 같은 선수를 이름 바꾸기 전/후에 각각 클릭해도 칸을 두 개
        차지하지 않는다(예전엔 "AI7PTQ"와 "카린"이 서로 다른 문자열이라
        8칸짜리 목록을 둘이서 잡아먹었다)."""
        if not name:
            return
        wb.add_recent_search(kind, stored if stored else name)
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
            self._on_league_selected,
            refresh_fn=self._refresh_league_list, debounce_timer=self._search_debounce)
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
    # [2026-08 신설] "선수 검색" 탭 전용 칸 폭 — 이름/등급/국가/리그는 팀
    # 검색과 같은 값을 그대로 재사용하고, 선수 고유 항목(포지션/국적/OVR)만
    # 새로 정의한다.
    _POS_COL_W = 46
    _NAT_COL_W = 110
    _OVR_COL_W = 44
    # [2026-08 신설, 신민용 리포트: "선수 경력(연도별 기록) 표의 포지션
    # 칸에서 CDM만 잘려서 CD...로 보인다"] 선수 목록의 "포지션" 칸(delegate
    # 커스텀 페인트, _POS_COL_W)과 이 표(일반 QTableWidgetItem, Qt가 넘치는
    # 텍스트를 그냥 자름)는 렌더링 방식이 달라서 같은 폭이 여기선 부족했다
    # — 3글자 포지션(CDM/CAM/CDM 등)이 잘리지 않도록 이 표 전용으로 살짝
    # 더 넓힌 폭을 따로 둔다(다른 화면의 _POS_COL_W는 그대로 유지).
    _POS_COL_W_WIDE = 58
    # [2026-08 신설, 신민용 요청: "소속팀 대회 기록에 연도별로 주전/
    # 로테이션/대기/유망주 표시"] "로테이션"(4글자)까지 안 잘리게 포지션
    # 칸보다 살짝 넓게.
    _ROLE_COL_W = 64

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
                      "컵대회·챔피언스리그)을 확인하세요. 💡 연도를 클릭하면 그 해 포메이션이 펼쳐져요.")
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
            self._on_team_selected,
            refresh_fn=self._refresh_team_list, debounce_timer=self._team_search_debounce)
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

        self.team_detail_tbl = QTableWidget(0, 7)
        self.team_detail_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.team_detail_tbl.verticalHeader().setVisible(False)
        self.team_detail_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # [2026-08 확장, 13순위] "슈퍼컵" 컬럼을 클럽 대항전과 클럽 월드컵
        # 사이에 추가(신민용 확정 순서: 리그|국내컵|클럽대항전|슈퍼컵|
        # 클럽월드컵).
        # [2026-08 확장, 신민용 요청] 연도와 리그 사이에 "순위"(그 해
        # 전체/대륙 파워랭킹, 2줄) 컬럼 추가.
        self.team_detail_tbl.setHorizontalHeaderLabels(
            ["연도", "순위", "리그", "국내컵", "클럽 대항전", "슈퍼컵", "클럽 월드컵"])
        # [2026-08 버그수정, 신민용 리포트: "클럽 대항전 수상 상자만 크기가
        # 다르다"] 예전엔 0번 컬럼(연도)이 ResizeToContents라 이 표는
        # "2004" 같은 4자리 숫자 기준으로, team_award_tbl은 "수상"이라는
        # 2글자 기준으로 각자 따로 폭을 쟀다 — 두 표가 독립적으로 계산하니
        # 완전히 같은 값이 나온다는 보장이 없었다(글자 폭 계산은 폰트
        # 렌더링에 좌우돼서 미묘하게 다를 수 있음). 그 어긋남이 리그/
        # 국내컵/클럽월드컵처럼 배경이 빈 칸에서는 안 보이다가, 클럽
        # 대항전처럼 자체 배경 있는 위젯 칸에서만 사각형 경계로 드러났다.
        # 두 표 모두 같은 고정폭(_YEAR_COL_W)을 쓰게 해서 애초에 어긋날
        # 여지를 없앤다.
        self._YEAR_COL_W = 64
        self.team_detail_tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed)
        self.team_detail_tbl.setColumnWidth(0, self._YEAR_COL_W)
        for _c in (1, 2, 3, 4, 5, 6):
            self.team_detail_tbl.horizontalHeader().setSectionResizeMode(
                _c, QHeaderView.ResizeMode.Stretch)

        # [2026-08 버그수정, 신민용 스크린샷 리포트: "위에 연도 리그 국내컵
        # 이런 헤더는 고정돼 있는데 '수상' 행은 스크롤하면 같이 올라가서
        # 사라진다"] "수상" 행은 예전에 team_detail_tbl의 진짜 row 0으로
        # 들어가 있어서, 표 본문(연도별 행)과 함께 스크롤되는 게 당연했다
        # — QTableWidget은 특정 "행"만 얼리는 기능이 따로 없다(컬럼 헤더는
        # 원래 뷰포트에 안 속해서 자동 고정이지만, row 0은 그냥 평범한
        # 본문 행이다). 그래서 "수상" 행을 아예 표 밖으로 빼서, 진짜 헤더
        # 바로 아래 별도의 1행짜리 고정 표(team_award_tbl)로 만든다 —
        # 이건 스크롤 영역(team_detail_tbl)이 아니라 right_lay의 고정
        # 형제 위젯이라 연도 행을 아무리 내려도 항상 그대로 보인다.
        # 컬럼 폭은 진짜 표(team_detail_tbl)가 기준 — sectionResized를
        # 그대로 따라가게 연결해서, Stretch 모드로 창 크기에 따라 폭이
        # 바뀌어도(그리고 세로 스크롤바가 생겨 뷰포트가 좁아져도) 항상
        # 완전히 같은 폭으로 맞춰진다.
        self.team_award_tbl = QTableWidget(1, 7)
        self.team_award_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.team_award_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.team_award_tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.team_award_tbl.horizontalHeader().setVisible(False)
        self.team_award_tbl.verticalHeader().setVisible(False)
        self.team_award_tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.team_award_tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.team_award_tbl.setFrameShape(QFrame.Shape.NoFrame)
        # [2026-08 버그수정] team_detail_tbl과 완전히 같은 리사이즈 모드를
        # 독립적으로도 걸어둔다 — 아래 sectionResized 연결·강제 동기화가
        # 아직 한 번도 안 일어난 시점(예: 창 뜨자마자 첫 팀 클릭)에도,
        # Stretch 모드 자체가 "이 표 자신의 뷰포트 폭"을 기준으로 알아서
        # 계산해주기 때문에 sync 타이밍에 의존하지 않고 항상 올바른 폭이
        # 나온다. 0번 컬럼은 위에서 정한 같은 고정폭(_YEAR_COL_W)을 그대로.
        self.team_award_tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed)
        self.team_award_tbl.setColumnWidth(0, self._YEAR_COL_W)
        for _c in (1, 2, 3, 4, 5, 6):
            self.team_award_tbl.horizontalHeader().setSectionResizeMode(
                _c, QHeaderView.ResizeMode.Stretch)
        # [2026-08 신설, 신민용 리포트: "클럽 대항전 칸만 혼자 상자처럼 튀어
        # 보인다 — 리그/국내컵/클럽대항전/클럽월드컵 사이에 검은 선으로
        # 통일해서 표시해달라"] 전역 STYLE의 gridline-color(#2a2a2a)가
        # 이 행의 셀 배경색(역시 #2a2a2a, 아래 _show_team_detail의
        # setBackground)과 완전히 같아서, 일반 칸(QTableWidgetItem)끼리는
        # 격자선이 배경에 파묻혀 안 보였다 — 그런데 "클럽 대항전" 칸만
        # setCellWidget(원시 QWidget)이라 이 블렌딩이 그대로 안 먹혀서
        # 혼자 경계가 도드라져 보였다. 격자선 자체를 눈에 보이는 검은
        # 색으로 바꿔서, 4칸(리그/국내컵/클럽대항전/클럽월드컵) 사이가
        # 전부 똑같이 검은 선으로 구분되게 통일한다.
        self.team_award_tbl.setShowGrid(True)
        self.team_award_tbl.setStyleSheet(
            "QTableWidget{gridline-color:#000; border:none;}")
        _row_h = self.team_award_tbl.verticalHeader().defaultSectionSize()
        self.team_award_tbl.setFixedHeight(_row_h + 4)
        # [2026-08] 위 Stretch 모드로 이미 스스로 정확한 폭을 계산하지만,
        # 그래도 창 크기 변화에 두 표가 "같은 프레임"에서 같이 움직이도록
        # sectionResized를 계속 따라가게 연결해둔다(중복 안전장치 — 둘 다
        # 옳은 값으로 수렴하므로 서로 덮어써도 결과는 항상 동일하다).
        self.team_detail_tbl.horizontalHeader().sectionResized.connect(
            lambda idx, _old, new: self.team_award_tbl.setColumnWidth(idx, new))
        right_lay.addWidget(self.team_award_tbl)
        # [2026-08] 표 본문(연도 행)이 뷰포트를 넘으면 세로 스크롤바가 생겨
        # team_detail_tbl의 실제 표시 폭이 그만큼 줄어든다 — 위 award_tbl은
        # 스크롤바가 없어서 그 폭을 그대로 두면 어긋난다. 스크롤바 폭만큼
        # 항상 예약해서 두 표의 뷰포트 폭을 애초에 똑같이 만든다.
        self.team_detail_tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        # [2026-08 신설, 신민용 요청: "팀 검색에서 연도를 클릭하면 그 해
        # 이 팀의 포메이션(이름만)이 떠야 한다"] 국가 검색의 연도/대회
        # 인라인 펼치기(_on_country_detail_cell_clicked)와 같은 패턴 —
        # "연도"(0번) 칸을 클릭하면 바로 아래에 그 해 포메이션 카드를
        # 펼친다.
        self.team_detail_tbl.cellClicked.connect(self._on_team_detail_cell_clicked)
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

        # [2026-08 신설] 다른 팀을 고르면 표를 통째로 다시 그리므로, 이전
        # 팀에서 펼쳐둔 연도별 포메이션 카드가 있으면(있던 행 인덱스를
        # 그대로 들고 있는 채로) 먼저 접어서 상태를 깨끗하게 정리한다 —
        # 국가 검색의 _refresh_country_detail_table과 동일한 이유.
        self._collapse_team_detail_row()

        tbl = self.team_detail_tbl
        tbl.setRowCount(0)
        # [2026-08 신설] 복사 버튼이 지금 표에 그릴 데이터를 그대로 재사용할 수
        # 있도록(별도 재조회 없이) 팀 이름 + get_team_history 결과를 인스턴스에
        # 저장해둔다 — 아래에서 hist를 구한 뒤(다음 줄들) 최신 값으로 갱신한다.
        self._team_copy_name = tname
        self._team_copy_tid = tid
        # [2026-08 버그수정, 신민용 리포트: "표에서 칸 하나가 그 행 전체를
        # 뒤덮는 깨짐 현상"] 이 표도 아래에서 "기록 없음"일 때 setSpan을
        # 쓰는데, 이전 팀 선택 때 그 span이 남아있으면(clear는 span을
        # 안 지움) 다음 팀의 실제 데이터에도 그대로 씌워진다 — 팀을 바꿀
        # 때마다 무조건 먼저 지운다(_show_empty_state와 동일한 원인).
        tbl.clearSpans()
        # [2026-08] "수상" 행이 team_award_tbl로 분리되면서, 팀을 바꿀 때
        # 이전 팀의 award_tbl 내용이 남아있지 않도록 항상 먼저 지운다.
        self.team_award_tbl.clearContents()
        hist = wb.get_team_history(tid)
        self._team_copy_hist = hist
        awards, years = hist["awards"], hist["years"]
        if not years and not any(awards.values()):
            self.team_copy_btn.setEnabled(False)
            tbl.setRowCount(1)
            empty = QTableWidgetItem("기록 없음")
            empty.setForeground(QColor("#666"))
            tbl.setItem(0, 0, empty)
            tbl.setSpan(0, 0, 1, 7)
            return
        self.team_copy_btn.setEnabled(True)

        # [2026-08 신설, 신민용 요청: "연도별 기록 맨 위에 '수상' 칸을 만들어
        # 리그/컵/챔스/클럽WC 우승 횟수를 보여달라, 0회면 빈칸으로"]
        # [2026-08 확장, 신민용 확정: "클럽 대항전 수상은 하나로 합치지
        # 않고 왼쪽부터 파랑(챔스) 한 칸 띄고 주황(유로파) 한 칸 띄고
        # 초록(컨퍼런스), 0회인 대회는 그 자체를 생략"] 클럽 대항전 칸
        # 안에서 여러 색 숫자를 한 셀에 같이 넣어야 해서, 이 칸만
        # _two_line_cell이 아니라 직접 QLabel들을 가로로 배치한 위젯을 쓴다.
        # [2026-08 버그수정, 신민용 스크린샷 리포트: "수상 행이 스크롤하면
        # 같이 사라진다"] 이 행은 이제 tbl(연도별 스크롤 표)이 아니라
        # 항상 고정으로 보이는 self.team_award_tbl에 그린다.
        award_tbl = self.team_award_tbl
        award_labels = [
            ("수상", None),
            ("", None),  # [2026-08 신설] 순위 칸 — 요약행이라 해당 없음, 빈칸
            (str(awards["league"]) if awards["league"] else "", "#4da6ff"),
            (str(awards["cup"]) if awards["cup"] else "", "#c48aff"),
        ]
        for j, (text, color) in enumerate(award_labels):
            cell = QTableWidgetItem(text)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            f = cell.font(); f.setBold(True); cell.setFont(f)
            cell.setForeground(QColor(color) if color else QColor("#ffcc00"))
            cell.setBackground(QColor("#2a2a2a"))
            award_tbl.setItem(0, j, cell)

        # 클럽 대항전 수상 칸(4번 컬럼) — 파랑/주황/초록 숫자를 한 칸에 같이.
        award_tbl.setCellWidget(0, 4, self._cl_award_summary_cell(
            awards.get("cl_champions", 0), awards.get("el_champions", 0),
            awards.get("ecl_champions", 0)))

        # [2026-08 신설, 13순위] 슈퍼컵 수상 칸(5번 컬럼) — 버건디색.
        sc_cell = QTableWidgetItem(str(awards.get("sc_champions", 0)) if awards.get("sc_champions") else "")
        sc_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        f = sc_cell.font(); f.setBold(True); sc_cell.setFont(f)
        sc_cell.setForeground(QColor(BURGUNDY))
        sc_cell.setBackground(QColor("#2a2a2a"))
        award_tbl.setItem(0, 5, sc_cell)

        cwc_cell = QTableWidgetItem(str(awards["cwc"]) if awards["cwc"] else "")
        cwc_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        f = cwc_cell.font(); f.setBold(True); cwc_cell.setFont(f)
        cwc_cell.setForeground(QColor("#4dd0e1"))
        cwc_cell.setBackground(QColor("#2a2a2a"))
        award_tbl.setItem(0, 6, cwc_cell)
        # [2026-08] sectionResized 연결만으로는 "폭이 실제로 바뀔 때"만
        # 동기화된다 — 이 팀 선택 시점에 처음으로 표가 그려질 때도(아직
        # 리사이즈 이벤트가 한 번도 안 났을 수 있음) 확실히 맞춰두기
        # 위해 매번 명시적으로 한 번 더 폭을 그대로 복사한다.
        for _c in range(7):
            award_tbl.setColumnWidth(_c, tbl.columnWidth(_c))

        # [2026-08 신설, 신민용 요청] 이 팀의 연도별 파워랭킹(전체순위,
        # 대륙순위)을 한 번만 조회해 캐시 — 대륙순위는 6개 원시 대륙
        # 기준(get_team_power_ranking_grouped의 새 6탭 구조와 동일 원칙,
        # _continent_group_for가 이제 병합 없이 단일 대륙만 돌려주므로
        # get_team_power_history가 그대로 "그 대륙 안에서의 순위"를 준다).
        # [2026-08 확장, 신민용 요청: "전체 순위/대륙 순위에 국가 순위도
        # 추가해달라"] get_team_power_history가 이제 4개(연도,전체,대륙,
        # 국가)를 주므로, 국가 이름도 같이 조회해 라벨에 쓴다.
        continent_row = get_conn().execute(
            """SELECT cn.continent, cn.name FROM teams t JOIN countries cn ON t.country_id = cn.id
               WHERE t.id=?""", (tid,)).fetchone()
        team_continent = continent_row[0] if continent_row else ""
        team_country = continent_row[1] if continent_row else ""
        rank_by_year = {y: (r, cr, ctr) for y, r, cr, ctr in pr.get_team_power_history(get_conn(), tid)}
        self._team_copy_continent = team_continent
        self._team_copy_country = team_country
        self._team_copy_rank_by_year = rank_by_year

        tbl.setRowCount(len(years))
        for i, entry in enumerate(years):
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

            # [2026-08 신설, 신민용 요청] 연도-리그 사이 순위 칸 — 전체
            # 순위/대륙 순위/국가 순위 3줄. get_team_power_history의
            # ranking_year는 "그 시즌 성적이 발표된 연도(evaluation_year+1)"
            # 라서 이 표의 연도(entry["year"]=실제 뛴 시즌)와 다르다 —
            # 발표 시점 기준으로 보이는 게 자연스러우므로 evaluation_year+1로
            # 조회한다.
            rp = rank_by_year.get(entry["year"] + 1)
            if rp:
                rank_main = f"전체 순위: {rp[0]}"
                continent_label = f"{team_continent} 순위: {rp[1]}" if team_continent else f"대륙 순위: {rp[1]}"
                country_label = f"{team_country} 순위: {rp[2]}" if team_country else f"국가 순위: {rp[2]}"
                rank_record = f"{continent_label}\n{country_label}"
            else:
                rank_main, rank_record = "-", None
            tbl.setCellWidget(i, 1, self._two_line_cell(rank_main, "#88ddaa", rank_record))

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
            tbl.setCellWidget(i, 2, self._two_line_cell(lg_txt, lg_color, entry.get("league_record")))

            cup_txt = entry["cup"] or "-"
            # [2026-08 수정, 신민용 리포트: "국내컵도 우승해도 전체를
            # 금색으로 바꾸지 말고 [우승]만 금색, 컵 이름은 원래 보라색
            # 유지"] CL/CWC와 동일한 패턴 — 본문색은 항상 국내컵 고유색
            # (보라)/미출전(회색)으로 두고, "[우승]"만 _two_line_cell이
            # 자동으로 금색 강조한다.
            cup_color = "#c48aff" if entry["cup"] else "#555"
            tbl.setCellWidget(i, 3, self._two_line_cell(cup_txt, cup_color, entry.get("cup_record")))

            cl_txt = entry["cl"] or "-"
            # [2026-08 신설, 신민용 확정: "클럽 대항전"으로 통합 — 챔스는
            # 파랑(#1E4DB7), 유로파는 주황(#F28C28), 컨퍼런스는 초록
            # (#20A464). 워터폴 구조상 한 해엔 하나만 걸리므로 cl_kind
            # 하나로 색이 딱 정해진다(참가 자체가 없으면 회색).
            _CL_KIND_COLOR = {"champions": "#1E4DB7", "europa": "#F28C28", "conference": "#20A464"}
            cl_color = _CL_KIND_COLOR.get(entry.get("cl_kind"), "#555") if entry["cl"] else "#555"
            tbl.setCellWidget(i, 4, self._two_line_cell(cl_txt, cl_color, entry.get("cl_record")))

            # [2026-08 신설, 13순위] 슈퍼컵 칸 — 버건디색, 미참가는 회색.
            sc_txt = entry.get("sc") or "-"
            sc_color = BURGUNDY if entry.get("sc") else "#555"
            tbl.setCellWidget(i, 5, self._two_line_cell(sc_txt, sc_color, entry.get("sc_record")))

            cwc_txt = entry.get("cwc") or "-"
            # [2026-08 수정] 우승해도 전체를 금색으로 바꾸지 않는다 —
            # 본문색은 항상 클럽월드컵 고유색(하늘색)/미출전(회색)으로
            # 유지하고, "[우승]" 부분만 _two_line_cell이 자동으로 금색
            # 강조한다.
            cwc_color = "#4dd0e1" if entry.get("cwc") else "#555"
            tbl.setCellWidget(i, 6, self._two_line_cell(cwc_txt, cwc_color, entry.get("cwc_record")))
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

    # [2026-08 신설, 신민용 요청: "팀 검색에서 연도를 클릭하면 그 해
    # 이 팀의 포메이션(이름만, OVR은 필요없음)이 떠야 한다"] 국가 검색의
    # "연도 클릭 → 인라인 펼치기"(_on_country_detail_cell_clicked)와
    # 완전히 같은 패턴 — 한 번에 하나만 펼치고, 같은 연도를 다시 누르면
    # 접는다.
    def _collapse_team_detail_row(self):
        exp = getattr(self, "_team_expanded", None)
        if not exp:
            return
        tbl = self.team_detail_tbl
        detail_row = exp.get("detail_row")
        if detail_row is not None and 0 <= detail_row < tbl.rowCount():
            tbl.removeRow(detail_row)
        self._team_expanded = None

    def _on_team_detail_cell_clicked(self, row, col):
        if col != 0:
            return
        tbl = self.team_detail_tbl
        item = tbl.item(row, 0)
        if not item:
            return
        try:
            year = int(item.text())
        except (TypeError, ValueError):
            return
        tid = getattr(self, "_team_copy_tid", None)
        if tid is None:
            return

        exp = getattr(self, "_team_expanded", None)
        same_year = bool(exp) and exp.get("year") == year
        # 이미 다른 연도가 펼쳐져 있으면 먼저 접는다 — 접으면 그 아래
        # 행들이 위로 당겨져 인덱스가 바뀌므로, 이후 목표 행은 연도
        # 텍스트로 다시 찾아낸다(국가 검색과 동일한 이유).
        self._collapse_team_detail_row()
        if same_year:
            return

        target_row = None
        for r in range(tbl.rowCount()):
            it = tbl.item(r, 0)
            if it and it.text() == str(year):
                target_row = r
                break
        if target_row is None:
            return

        tname = getattr(self, "_team_copy_name", None) or ""
        widget = self._build_team_year_lineup_widget(tid, year, f"{year}년 {tname} 포메이션")

        detail_row = target_row + 1
        tbl.insertRow(detail_row)
        tbl.setSpan(detail_row, 0, 1, 7)
        tbl.setCellWidget(detail_row, 0, widget)
        tbl.resizeRowToContents(detail_row)
        h = widget.sizeHint().height()
        if h > tbl.rowHeight(detail_row):
            tbl.setRowHeight(detail_row, h + 8)
        self._team_expanded = {"year": year, "detail_row": detail_row}
        year_item = tbl.item(target_row, 0)
        if year_item:
            tbl.scrollToItem(year_item)

    def _build_team_year_lineup_widget(self, tid, year, header_title):
        """[2026-08 신설] wb.get_team_season_lineup()이 돌려주는 그 해
        슬롯별 선수(이름만, 신민용 요청대로 OVR 없음)를 국가 검색 스쿼드
        카드(_build_country_squad_detail_widget)와 같은 스타일로 그린다.
        데이터가 없으면(이 기능 신설 이전 과거 시즌 등) 그 사실을 그대로
        안내한다.

        [2026-08 재작업, 신민용 리포트: "내가 분명 포메이션 형태로
        보내달라 했는데 왜 없어?" / "팀도 주전 후보가 있는데 왜 안떠?"]
        표(QTableWidget) 대신 국가 스쿼드와 같은 _StaticPitchView로
        주전을 그리고, 아래에 후보(bench_json — ai_lifecycle이 새로
        같이 저장하기 시작함) 칩 목록을 추가한다. starters는 이미
        ai_lifecycle._snapshot_season_positions이 저장 시점에
        _greedy_fill_slots로 슬롯 배정을 끝내둔 상태라("slot" 필드가
        FORMATION_SLOTS[formation] 원본 순서와 1:1 대응) 여기서 다시
        배정할 필요가 없다."""
        data = wb.get_team_season_lineup(tid, year)
        box = QFrame()
        box.setStyleSheet(
            "background:#262626;border:1px solid #3a3a3a;border-left:3px solid #4da6ff;"
            "border-radius:6px;")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)

        starters = data.get("starters") or []
        bench = data.get("bench") or []

        # [2026-08 신설, 신민용 요청: "이 대회명(여기서는 연도) 써진 줄
        # 우측에 복사하기 버튼을 놔줘"] 국가 스쿼드 카드와 동일한 위치·
        # 스타일로 헤더 행 오른쪽 끝에 배치.
        header_row = QHBoxLayout()
        title = QLabel(f"🧩 {header_title}")
        title.setStyleSheet("color:#4da6ff;font-size:13px;font-weight:bold;")
        header_row.addWidget(title)
        header_row.addStretch(1)
        _btn_qss = (
            "QPushButton{background:#333;color:#ddd;border:1px solid #4a4a4a;"
            "border-radius:4px;padding:3px 10px;font-size:11px;}"
            "QPushButton:hover{background:#3d3d3d;}")
        if starters:
            # [2026-08 신설, 신민용 요청: "복사하기 버튼을 2개 만들건데
            # 1번째는 주전만, 2번째는 지금처럼 스쿼드 전체 — 주전 복사는
            # 주전으로 뛰었던 선수 11명만"] 후보 없이 slot에 실제로 배정된
            # (id가 있는) 선수만 골라 별도 버튼으로 복사한다.
            starter_copy_btn = QPushButton("📋 주전 기록 복사")
            starter_copy_btn.setStyleSheet(_btn_qss)
            _starter_ids = [s.get("id") for s in starters]
            starter_copy_btn.clicked.connect(
                lambda: self._copy_squad_player_records(
                    _starter_ids, starter_copy_btn, "📋 주전 기록 복사"))
            header_row.addWidget(starter_copy_btn)
        if starters or bench:
            squad_copy_btn = QPushButton("📋 스쿼드 기록 복사")
            squad_copy_btn.setStyleSheet(_btn_qss)
            _ids = [s.get("id") for s in (starters + bench)]
            squad_copy_btn.clicked.connect(
                lambda: self._copy_squad_player_records(_ids, squad_copy_btn))
            header_row.addWidget(squad_copy_btn)
            # [2026-08 신설, 신민용 요청: "요약 복사 — 이 연도 스쿼드를
            # 뽑으면, 선수 기록에서 그 연도에 해당하는 부분만 남기고
            # 나머지 연도는 빼줘"] 이 위젯은 이미 특정 "연도"(year 인자)
            # 단위이므로 그 값 하나만 target_years로 넘기면 된다.
            summary_copy_btn = QPushButton("📋 요약 복사")
            summary_copy_btn.setStyleSheet(_btn_qss)
            _summary_years = {year}
            summary_copy_btn.clicked.connect(
                lambda: self._copy_squad_player_records(
                    _ids, summary_copy_btn, "📋 요약 복사", target_years=_summary_years))
            header_row.addWidget(summary_copy_btn)
        lay.addLayout(header_row)

        if not starters:
            empty = QLabel(
                "이 연도는 포메이션 기록이 없습니다 — 이 기능이 생기기 전 과거 시즌은 소급 조회가 안 됩니다.")
            empty.setStyleSheet("color:#666;font-size:11px;")
            empty.setWordWrap(True)
            lay.addWidget(empty)
            return box

        formation = data.get("formation") or "4-4-2"
        if data.get("formation"):
            flabel = QLabel(f"포메이션: {data['formation']}")
            flabel.setStyleSheet("color:#888;font-size:11px;")
            lay.addWidget(flabel)

        slot_players = [(s.get("slot") or "", s.get("display_name"), s.get("id")) for s in starters]
        pitch = _StaticPitchView(formation=formation, slot_players=slot_players,
                                  on_click=self.open_to_player)

        # [2026-08 재작업, 신민용 리포트: "좌측에 포메이션을 저렇게 박으면
        # 우측에 후보 선수들의 이름을 나열해야지 — 주전들은 초록색으로,
        # 후보들은 아래에 색이 없는 원래 상태로 나열"] 피치(좌)와 명단
        # 패널(우, 주전 초록/후보 무채색)을 가로로 나란히 배치. 명단에는
        # 실제 배정된 선수만(공석/선수없음 제외) 넣는다.
        content_row = QHBoxLayout()
        content_row.setSpacing(14)
        content_row.addWidget(pitch)
        _real_starters = [s for s in starters if s.get("id") is not None]
        roster = _build_squad_roster_panel(_real_starters, bench, on_click=self.open_to_player)
        content_row.addWidget(roster, 1)
        lay.addLayout(content_row)
        return box

    def _open_world_browser_from_team_lineup(self, starters, row):
        """[2026-08 신설] 팀 검색의 그 해 포메이션 표에서 이름을 클릭하면
        그 선수의 세계 기록실 상세를 이 창 안에서 바로 연다 — 국가 검색
        스쿼드 표의 _open_world_browser_from_country_squad와 동일한 패턴."""
        if row < 0 or row >= len(starters):
            return
        pid = starters[row].get("id")
        if pid is None:
            return
        self.open_to_player(pid)

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
        rank_by_year = getattr(self, "_team_copy_rank_by_year", {})
        continent = getattr(self, "_team_copy_continent", "")
        country = getattr(self, "_team_copy_country", "")
        text = self._format_team_history_text(tname, hist, rank_by_year, continent, country)
        QGuiApplication.clipboard().setText(text)

        # 눌렀을 때 복사됐다는 걸 눈으로 확인할 수 있게 버튼 라벨을
        # 잠깐 바꿨다가 되돌린다(1.2초). 다른 팀을 고르는 등으로 버튼이
        # 다시 그려지면(= 이 위젯이 없어지는 게 아니라 그냥 다음 클릭까지
        # 남아있으므로) 딱히 꼬일 일은 없다 — QTimer.singleShot이 그
        # 시점에 라벨만 원래대로 되돌린다.
        self.team_copy_btn.setText("✅ 복사됨")
        QTimer.singleShot(1200, lambda: self.team_copy_btn.setText("📋 기록 복사"))

    def _format_team_history_text(self, tname, hist, rank_by_year=None, continent="", country=""):
        """hist(get_team_history 반환값)를 사람이 읽어도, LLM에 그대로
        붙여넣어도 되는 평문으로 직렬화한다. 화면 표와 같은 정보(연도별
        순위/리그/국내컵/클럽대항전/클럽월드컵 결과 + 각자 전적, 맨 위 통산
        수상 집계)를 담되, 색상 대신 "[승격]"/"[강등]"/"[우승]" 같은
        텍스트 표기만으로 뜻이 통하게 한다(이미 entry 문자열 안에 이런
        표기가 들어있으므로 대부분 그대로 옮기면 된다).
        [2026-08 신설, 신민용 리포트: "기록 복사에 순위가 안 뜬다"] 화면
        표(team_detail_tbl)에 이미 있는 전체/대륙 순위 컬럼을 복사 텍스트
        에도 똑같이 넣는다 — rank_by_year는 {evaluation_year+1: (전체순위,
        대륙순위)} 형태(_show_team_detail에서 이미 계산해둔 것 재사용).
        [2026-08 확장, 신민용 요청: "전체 순위/대륙 순위에 국가 순위도
        추가해달라"] rank_by_year 튜플이 (전체,대륙,국가) 3개로 늘어나서
        복사 텍스트에도 국가 순위를 같이 넣는다.
        """
        rank_by_year = rank_by_year or {}
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
        if awards.get("sc_champions"):
            award_bits.append(f"슈퍼컵 우승 {awards['sc_champions']}회")
        if awards.get("cwc"):
            award_bits.append(f"클럽 월드컵 우승 {awards['cwc']}회")
        lines.append("통산 수상: " + (" · ".join(award_bits) if award_bits else "없음"))
        lines.append("")

        if not years:
            lines.append("(연도별 기록 없음)")
        else:
            for entry in years:
                parts = [f"{entry['year']}년"]
                rp = rank_by_year.get(entry["year"] + 1)
                if rp:
                    cont_label = f"{continent} " if continent else ""
                    country_label = f"{country} " if country else ""
                    parts.append(f"순위: 전체 {rp[0]}위 / {cont_label}대륙 {rp[1]}위 / {country_label}국가 {rp[2]}위")
                if entry.get("league"):
                    rec = f" ({entry['league_record']})" if entry.get("league_record") else ""
                    parts.append(f"리그: {entry['league']}{rec}")
                if entry.get("cup"):
                    rec = f" ({entry['cup_record']})" if entry.get("cup_record") else ""
                    parts.append(f"국내컵: {entry['cup']}{rec}")
                if entry.get("cl"):
                    rec = f" ({entry['cl_record']})" if entry.get("cl_record") else ""
                    parts.append(f"클럽대항전: {entry['cl']}{rec}")
                if entry.get("sc"):
                    rec = f" ({entry['sc_record']})" if entry.get("sc_record") else ""
                    parts.append(f"슈퍼컵: {entry['sc']}{rec}")
                if entry.get("cwc"):
                    rec = f" ({entry['cwc_record']})" if entry.get("cwc_record") else ""
                    parts.append(f"클럽월드컵: {entry['cwc']}{rec}")
                lines.append(" | ".join(parts))

        return "\n".join(lines)

    # [2026-08 신설, 신민용 요청: "선수 검색에도 복사하기 버튼을 만들어서
    # 이름/국적/나이/포지션/OVR/소속팀 같은 기본 정보와, 몇 년에 몇 살이며
    # 어떤 팀이었고 그때 OVR가 몇이며 그때 팀 성적이 어땠는지를 연도별로
    # 전부 복사해달라"] 팀 검색의 _on_copy_team_history_clicked와 완전히
    # 같은 패턴 — self._player_copy_*(_show_player_detail/
    # _populate_player_team_box/_populate_player_intl_box가 화면을 그리며
    # 이미 채워둔 값)를 재조회 없이 그대로 재사용한다.
    def _on_copy_player_history_clicked(self):
        name = getattr(self, "_player_copy_name", None)
        d = getattr(self, "_player_copy_d", None)
        if not name or not d:
            return
        team_hist = getattr(self, "_player_copy_team_hist", None)
        rows = getattr(self, "_player_copy_rows", [])
        intl_records = getattr(self, "_player_copy_intl_records", [])
        text = self._format_player_history_text(name, d, team_hist, rows, intl_records)
        QGuiApplication.clipboard().setText(text)

        # 눌렀을 때 복사됐다는 걸 눈으로 확인할 수 있게 버튼 라벨을
        # 잠깐 바꿨다가 되돌린다(팀 검색 복사 버튼과 동일한 방식).
        self.player_copy_btn.setText("✅ 복사됨")
        QTimer.singleShot(1200, lambda: self.player_copy_btn.setText("📋 기록 복사"))

    def _format_player_history_text(self, name, d, team_hist, rows, intl_records):
        """d(get_ai_player_detail 반환값) + rows(_populate_player_team_box가
        화면 렌더링과 동시에 쌓아둔 연도별 해석 결과: 그 해 나이·소속팀·
        OVR·entry) + intl_records(get_player_intl_records)를 사람이 읽어도,
        LLM에 그대로 붙여넣어도 되는 평문으로 직렬화한다. 화면과 다른
        계산을 새로 하지 않고 이미 화면에 쓴 값만 그대로 옮긴다(어긋남
        방지) — 팀 쪽 _format_team_history_text와 같은 원칙."""
        lines = [f"[{name} 선수 기록]"]

        # ── 기본 정보 한 줄 (player_detail_tbl 맨 위 요약 행과 동일 로직) ──
        nat_text = f"{d.get('nat_flag') or ''} {d.get('nationality') or ''}".strip() or "국적 미상"
        if d.get("is_retired"):
            team_text = f"은퇴함 ({d.get('retirement_year', '-')}년 은퇴, 당시 {d.get('age', '-')}세)"
            if d.get("last_team_name"):
                team_text += f" — 마지막 소속: {d['last_team_name']}"
        elif d.get("team_id"):
            country_txt = f"{d.get('flag') or ''} {d.get('country') or ''}".strip()
            team_text = f"{d['team_name']} ({d['league_name']} {d['tier']}부, {country_txt or '-'})"
        else:
            team_text = "소속팀 없음"
        ovr_text = "-" if is_hard_mode() else str(d.get("ovr", "-"))
        lines.append(
            f"국적: {nat_text} | 나이: {d.get('age', '-')}세 | 포지션: {d.get('position') or '-'} | "
            f"OVR: {ovr_text} | 소속: {team_text}")
        lines.append("")

        # ── 소속팀 기준 통산 수상 (팀 검색 쪽과 같은 포맷) ──
        awards = (team_hist or {}).get("awards") or {}
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
        if awards.get("sc_champions"):
            award_bits.append(f"슈퍼컵 우승 {awards['sc_champions']}회")
        if awards.get("cwc"):
            award_bits.append(f"클럽 월드컵 우승 {awards['cwc']}회")
        lines.append("소속팀 기준 통산 수상: " + (" · ".join(award_bits) if award_bits else "없음"))
        lines.append("")

        # ── 연도별 기록: 몇 년에 몇 살, 어느 팀, 그때 OVR, 그때 팀 성적 ──
        lines.append("[연도별 기록]")
        if not rows:
            lines.append("(연도별 기록 없음)")
        else:
            for row in rows:
                age_txt = f"{row['age']}세" if row.get("age") is not None else "나이 미상"
                if row["is_retired_row"]:
                    lines.append(f"{row['year']}년 ({age_txt}) | 소속팀 없음 (은퇴)")
                    continue
                entry = row["entry"] or {}
                parts = [f"{row['year']}년 ({age_txt})", f"소속팀: {row['team_name']}"]
                parts.append(f"포지션: {row['position']}" if row.get("position") else "포지션: -")
                parts.append(f"OVR: {row['ovr']}" if row.get("ovr") else "OVR: -")
                # [2026-08 신설, 신민용 요청: "복사할 때 년도별로 얘가
                # 주전인지 아닌지 뜨는거지"] role은 이 기능 신설 이전
                # 시즌엔 없을 수 있어(row.get("role") None) "역할: -"로.
                parts.append(f"역할: {row['role']}" if row.get("role") else "역할: -")
                if entry.get("league"):
                    rec = f" ({entry['league_record']})" if entry.get("league_record") else ""
                    parts.append(f"리그: {entry['league']}{rec}")
                if entry.get("cup"):
                    rec = f" ({entry['cup_record']})" if entry.get("cup_record") else ""
                    parts.append(f"국내컵: {entry['cup']}{rec}")
                if entry.get("cl"):
                    rec = f" ({entry['cl_record']})" if entry.get("cl_record") else ""
                    parts.append(f"클럽대항전: {entry['cl']}{rec}")
                if entry.get("sc"):
                    rec = f" ({entry['sc_record']})" if entry.get("sc_record") else ""
                    parts.append(f"슈퍼컵: {entry['sc']}{rec}")
                if entry.get("cwc"):
                    rec = f" ({entry['cwc_record']})" if entry.get("cwc_record") else ""
                    parts.append(f"클럽월드컵: {entry['cwc']}{rec}")
                lines.append(" | ".join(parts))
        lines.append("")

        # ── 국가대표 기록 ──
        lines.append("[국가대표 기록]")
        if not intl_records:
            lines.append("(국가대표 출전 기록 없음)")
        else:
            for rec in intl_records:
                apps = rec.get("appearances", 0)
                total = rec.get("total_games", 0)
                apps_text = f"{apps}/{total}" if total else str(apps)
                lines.append(
                    f"{rec.get('year')}년 | {rec.get('name') or '?'} ({rec.get('country') or '?'}) | "
                    f"출전 {apps_text} | 결과: {rec.get('result') or '?'}")

        return "\n".join(lines)

    # ─────────────────────────────────────────
    # 탭: 선수 검색 (2026-08 신설) — "파워랭킹" 탭 옆에 위치. 팀 검색 탭
    # (_build_team_tab)과 완전히 같은 UX(대륙/국가/등급/부수 필터 + 검색창
    # + 좌측 목록/우측 상세). 현재는 우측에 "지금 소속팀 + 그 팀의 최신
    # 파워랭킹(전체/대륙)"만 보여준다 — 골/도움/경기수 같은 시즌별 커리어
    # 스탯은 세계 축구 기록실 설계 논의에서 확인된 것처럼 시즌 아카이브
    # 테이블이 먼저 있어야 하므로 지금은 대상 밖(차후 확장 예정). 은퇴
    # 선수는 은퇴 시 ai_players 행 자체가 삭제되는 기존 설계상 자동으로
    # 검색 대상에서 빠진다(현재 데이터가 있는 현역 선수만).
    # ─────────────────────────────────────────
    def _build_player_search_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        info = QLabel("ℹ️ 선수 하나를 골라 소속팀 기록과 국가대표 기록을 확인하세요. "
                      "(현재 데이터가 있는 현역 선수만 — 은퇴 선수는 제외됩니다. "
                      "포메이션 화면과 동일하게 식별코드로 표시됩니다)")
        info.setStyleSheet("color:#888;font-size:11px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        # [2026-08 신설] 필터 전용 디바운스 — 아래 모든 필터 위젯(콤보/
        # 스핀박스/검색창)이 공유한다. 예전엔 콤보 선택이 바뀔 때마다
        # _refresh_player_list를 즉시 호출했는데, 실제 게임 DB(선수
        # 수만~수십만 명) 기준으로 목록 재구성(delegate 300행 렌더링+
        # _ensure_list_fits 스플리터 재계산)이 매번 값싸지 않아 필터를
        # 연달아 건드리면(콤보 드롭다운 스크롤, 나이 스핀박스 화살표
        # 연타 등) 그때마다 쌓여 버벅였다 — 검색창과 동일한 250ms
        # 디바운스로 통일해, 짧은 시간 안의 연속 조작은 마지막 것만
        # 실제로 반영되게 한다.
        self._player_filter_debounce = QTimer(self)
        self._player_filter_debounce.setSingleShot(True)
        self._player_filter_debounce.setInterval(250)
        self._player_filter_debounce.timeout.connect(self._refresh_player_list)

        def _debounced_refresh(*_a):
            self._player_filter_debounce.start()

        filt = QHBoxLayout()
        filt.setSpacing(8)
        # [2026-08 수정, 신민용 요청: "국가와 국적을 나눠야 한다, 대륙은
        # 국적과 연관되어 있게"] 대륙/국적은 선수의 실제 국적 기준(용병도
        # 정확히 찾을 수 있게), 국가는 기존처럼 '현재 뛰는 리그의 나라'
        # 기준(팀 검색 탭과 동일 UX) — 서로 독립적이라 대륙을 바꿔도
        # "국가" 목록은 그대로 전체 유지된다.
        # [2026-08 신설, 신민용 요청: "필터 길이를 늘린 김에 직접 입력하는
        # 칸들도 만들어도 될듯"] 211개국 드롭다운을 매번 스크롤하는 대신
        # 타이핑으로 좁힐 수 있게 setEditable(True)+QCompleter(부분일치)를
        # 붙인다 — 값은 여전히 목록에 있는 실제 항목으로만 확정되므로
        # (완전 자유 텍스트 필터가 아니라 "타이핑 가능한 드롭다운"),
        # id 역매칭 로직(_selected_player_*_id)은 그대로 재사용 가능하다.
        # [2026-08 신설, 신민용 요청: "위에 대륙/전체 이런게 띄어져 있으니
        # UI적으로 불편하니 옆으로 붙이고"] setEditable(True)로 바뀐(직접
        # 입력 가능) 콤보들은 기본 크기정책이 가로로 늘어나는 쪽이라,
        # 창이 넓어질수록 라벨-콤보 사이 간격이 벌어져 보였다 — 최대
        # 폭을 고정해 늘어나지 않게 하고, 아래 filt 끝에 addStretch를
        # 둬서 남는 공간은 오른쪽 끝으로만 몰리게 한다.
        _COMBO_MAX_W = 150
        lbl1 = QLabel("대륙(국적)"); lbl1.setStyleSheet("color:#888;font-size:11px;")
        self.player_nat_cont_combo = QComboBox()
        self.player_nat_cont_combo.setMaximumWidth(_COMBO_MAX_W)
        self.player_nat_cont_combo.addItem(_ALL)
        for cont in wb.list_continents():
            self.player_nat_cont_combo.addItem(cont)
        self._make_combo_typable(self.player_nat_cont_combo)
        self.player_nat_cont_combo.currentTextChanged.connect(self._on_player_nat_continent_changed)
        filt.addWidget(lbl1)
        filt.addWidget(self.player_nat_cont_combo)

        lbl2 = QLabel("국적"); lbl2.setStyleSheet("color:#888;font-size:11px;")
        self.player_nat_combo = QComboBox()
        self.player_nat_combo.setMaximumWidth(_COMBO_MAX_W)
        self.player_nat_combo.addItem(_ALL)
        self._make_combo_typable(self.player_nat_combo)
        self.player_nat_combo.currentTextChanged.connect(_debounced_refresh)
        filt.addWidget(lbl2)
        filt.addWidget(self.player_nat_combo)

        # [2026-08 신설, 신민용 요청: "국가(소속리그)부터 팀 기준: 경력
        # 포함까지 하나의 묶음으로 알아볼 수 있게 겉에 상자로 감싸달라"]
        # 국가(소속리그)→리그→팀 3단계 필터 + 팀 직접입력 + 경력 포함
        # 토글까지 전부 한 그룹이라는 걸 시각적으로 드러내려고 이 다섯
        # 위젯 묶음만 별도 QFrame(테두리 박스)에 담아 filt에 하나로
        # 얹는다 — 배치/시그널 연결 로직 자체는 기존과 동일, 어디에
        # addWidget하는지만 filt에서 club_filter_lay로 바뀐다.
        club_filter_box = QFrame()
        club_filter_box.setObjectName("player_club_filter_box")
        club_filter_box.setStyleSheet(
            "QFrame#player_club_filter_box{border:1px solid #3a3a3a;"
            "border-radius:6px;background:transparent;}")
        club_filter_lay = QHBoxLayout(club_filter_box)
        club_filter_lay.setContentsMargins(8, 3, 8, 3)
        club_filter_lay.setSpacing(8)

        lbl2b = QLabel("국가(소속리그)"); lbl2b.setStyleSheet("color:#888;font-size:11px;")
        self.player_country_combo = QComboBox()
        self.player_country_combo.setMaximumWidth(_COMBO_MAX_W)
        self.player_country_combo.addItem(_ALL)
        # [2026-08 수정, 신민용 리포트: "🇰🇷 대한민국처럼 국기 이모지가
        # 앞에 KR 같은 글자로 깨져 보인다 — 없애서 대한민국 이렇게만
        # 뜨게 해달라"] Windows 등 일부 환경에서 국기 이모지(유니코드
        # regional indicator 두 글자 조합)가 실제 국기 아이콘 대신 알파벳
        # 두 글자 그대로 렌더링돼서, 국가명 앞에 "KR " 같은 게 붙어 보이는
        # 문제였다 — 이 필터 콤보들은 국기 프리픽스 없이 국가명만 쓴다.
        for c in wb.list_countries():
            self.player_country_combo.addItem(c["name"])
        self._make_combo_typable(self.player_country_combo)
        self.player_country_combo.currentTextChanged.connect(self._on_player_club_country_changed)
        club_filter_lay.addWidget(lbl2b)
        club_filter_lay.addWidget(self.player_country_combo)

        # [2026-08 신설, 신민용 요청: "국가(소속리그) → 리그 → 팀 3단계
        # 필터... 리그 선택(많아봤자 7개니 직접 입력 없음, 기본 전체)...
        # 선택하면 그 년도 당시 그 리그에 있는 팀들이 필터로 뜸"]
        # 리그 콤보는 country_combo가 바뀔 때마다(_on_player_club_country_
        # changed) list_leagues_for_country()로 다시 채워지고, 팀 콤보는
        # 리그 콤보가 바뀔 때마다(_on_player_league_changed) list_teams_
        # in_league()로 다시 채워진다 — 국가/리그 미선택 상태에선 둘 다
        # "전체" 하나만 있고 비활성화.
        # [2026-08 신설] 아래 natteam/상태 버튼 쪽에서도 같은 스타일을
        # 쓰므로 여기서 먼저 정의해 "경력 포함" 토글에 재사용한다(뒤에서
        # 같은 이름으로 다시 정의해도 무해 — 완전히 같은 문자열).
        _STATUS_BTN_STYLE = (
            "QPushButton{background:#2a2a2a;color:#888;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:4px 10px;font-size:11px;}"
            "QPushButton:checked{background:#0d3d1a;color:#00cc44;border-color:#00cc44;}")
        lbl_league = QLabel("리그"); lbl_league.setStyleSheet("color:#888;font-size:11px;")
        self.player_league_combo = QComboBox()
        self.player_league_combo.setMaximumWidth(_COMBO_MAX_W)
        self.player_league_combo.addItem(_ALL)
        self.player_league_combo.setEnabled(False)
        self.player_league_combo.currentTextChanged.connect(self._on_player_league_changed)
        club_filter_lay.addWidget(lbl_league)
        club_filter_lay.addWidget(self.player_league_combo)

        lbl_team = QLabel("팀"); lbl_team.setStyleSheet("color:#888;font-size:11px;")
        self.player_team_combo = QComboBox()
        self.player_team_combo.setMaximumWidth(_COMBO_MAX_W)
        self.player_team_combo.addItem(_ALL)
        self.player_team_combo.setEnabled(False)
        self.player_team_combo.currentTextChanged.connect(self._on_player_team_combo_changed)
        club_filter_lay.addWidget(lbl_team)
        club_filter_lay.addWidget(self.player_team_combo)

        # [2026-08 신설, 신민용 요청: "팀 옆에 직접입력을 하나 만들어서
        # 거기에 입력하면 앞의 국가(소속리그)/리그가 그 팀이 지금 있는
        # 곳에 맞춰 자동으로 채워지게 — 예를 들어 헬퍼FC가 K3리그에
        # 있으면 헬퍼FC를 치는 순간 국가는 대한민국, 리그는 K3로 자동
        # 세팅. 팀 콤보 자체는 뒤에 입력한 이 텍스트가 곧 팀 필터가
        # 되므로 안 바뀌어도 무방. 팀 기준: 경력 포함 토글도 그대로
        # 적용돼야 함"] 국가(소속리그)/리그 콤보처럼 매번 전체 목록을
        # 스크롤하지 않고 팀명을 바로 타이핑할 수 있는 자유 입력 칸.
        # [최적화] 매 키 입력마다 DB 조회+콤보 재구성+목록 재조회가
        # 겹치면 버벅이므로(위 player_filter_debounce와 같은 이유),
        # 이 칸 전용 디바운스(_player_team_direct_debounce)를 따로 두고
        # 타이핑이 잠시 멈췄을 때만 실제 조회(_apply_player_team_direct_
        # input)가 실행되게 한다 — 팀 수가 수천 단위라 QCompleter로
        # 매번 후보를 띄우는 방식은 일부러 쓰지 않았다(입력 즉시 콤보
        # 자동완성 목록을 갱신하는 비용이 오히려 더 큼).
        lbl_team_direct = QLabel("직접입력"); lbl_team_direct.setStyleSheet("color:#888;font-size:11px;")
        self.player_team_direct_edit = QLineEdit()
        self.player_team_direct_edit.setMaximumWidth(_COMBO_MAX_W)
        self.player_team_direct_edit.setPlaceholderText("팀명 직접입력")
        self.player_team_direct_edit.setToolTip(
            "팀명을 직접 입력하면 그 팀이 지금 있는 국가(소속리그)/리그가\n"
            "자동으로 채워지고, 그 팀 자체가 검색 기준이 됩니다.\n"
            "(왼쪽 '팀' 콤보 선택과는 별개 — 이 칸에 값이 있으면 이 칸이 우선)")
        self._player_team_direct_match = None
        self._player_team_direct_debounce = QTimer(self)
        self._player_team_direct_debounce.setSingleShot(True)
        self._player_team_direct_debounce.setInterval(300)
        self._player_team_direct_debounce.timeout.connect(self._apply_player_team_direct_input)
        self.player_team_direct_edit.textChanged.connect(
            lambda *_a: self._player_team_direct_debounce.start())
        club_filter_lay.addWidget(lbl_team_direct)
        club_filter_lay.addWidget(self.player_team_direct_edit)

        # [2026-08 신설, 신민용 요청: "팀 기준: 현재 소속 / 경력 포함...
        # 기본값은 현재 소속으로 하고, 사용자가 경력 포함을 켜면 현역도
        # 과거 팀 경험까지 검색"]
        # [2026-08 수정, 신민용 요청: "은퇴 검색도 이 토글을 따르게 —
        # 꺼져 있으면 마지막 소속팀(은퇴 직전 팀) 기준으로, 켜면 경력에
        # 그 팀이 있으면 다 뜨게"] 예전엔 은퇴 상태에선 이 토글과 무관
        # 하게 백엔드가 항상 "경력(뛴 적 있으면)"으로 고정 검색했는데,
        # 이제 현역/은퇴 모두 이 버튼 하나로 통일해서 따른다.
        # [2026-08 신설] 팀 직접입력 칸으로 찾은 팀에도 동일하게 적용된다
        # (_refresh_player_list에서 team_id를 직접입력 매치로 덮어써도
        # team_mode는 그대로 이 버튼 값을 따름).
        self.player_team_career_btn = QPushButton("팀 기준: 경력 포함")
        self.player_team_career_btn.setCheckable(True)
        self.player_team_career_btn.setChecked(False)
        self.player_team_career_btn.setAutoDefault(False)
        self.player_team_career_btn.setStyleSheet(_STATUS_BTN_STYLE)
        self.player_team_career_btn.setToolTip(
            "꺼짐(기본): 현역은 '현재 소속', 은퇴는 '마지막 소속팀(은퇴 직전 팀)'만 검색.\n"
            "켜짐: 과거에 그 팀(국가(소속리그)/리그로 좁혔다면 그 범위의 팀들)에서\n"
            "뛴 적이 있으면 포함 — 현역/은퇴 모두 동일하게 적용.")
        self.player_team_career_btn.toggled.connect(_debounced_refresh)
        club_filter_lay.addWidget(self.player_team_career_btn)

        filt.addWidget(club_filter_box)

        # [2026-08 신설, 신민용 요청: "국가(소속리그)랑 상태(현역/은퇴)
        # 사이에 국가대표 유무 표시를 넣어달라, 기본은 꺼짐(전부 보임),
        # 켜면 기본은 '전체'(어느 연도든 한 번이라도 뽑힌 적), 연도를
        # 입력하면 그 연도에 뽑혔던 선수만"] 체크 가능한 버튼 하나 +
        # 그 옆에 연도 입력칸(버튼이 꺼져 있으면 비활성화, 켜지면 활성화
        # 되고 비워두면 '전체'로 동작). _STATUS_BTN_STYLE(바로 아래
        # 정의됨)과 톤을 맞추기 위해 버튼 스타일을 먼저 만들어 공유한다.
        _STATUS_BTN_STYLE = (
            "QPushButton{background:#2a2a2a;color:#888;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:4px 10px;font-size:11px;}"
            "QPushButton:checked{background:#0d3d1a;color:#00cc44;border-color:#00cc44;}")
        self.player_natteam_btn = QPushButton("🌍 국가대표")
        self.player_natteam_btn.setCheckable(True)
        self.player_natteam_btn.setChecked(False)
        self.player_natteam_btn.setAutoDefault(False)
        self.player_natteam_btn.setStyleSheet(_STATUS_BTN_STYLE)
        filt.addWidget(self.player_natteam_btn)

        self.player_natteam_year_edit = QLineEdit()
        self.player_natteam_year_edit.setPlaceholderText("전체")
        self.player_natteam_year_edit.setMaximumWidth(56)
        self.player_natteam_year_edit.setValidator(QIntValidator(1900, 2200, self))
        self.player_natteam_year_edit.setEnabled(False)
        self.player_natteam_year_edit.setToolTip(
            "비워두면 어느 연도든 국가대표로 한 번이라도 뽑힌 선수 전체.\n"
            "연도를 입력하면(예: 2002) 그 해에 국가대표였던 선수만.")

        def _on_natteam_toggled(checked):
            self.player_natteam_year_edit.setEnabled(checked)
            # [2026-08 신설, 신민용 요청: "국가대표 버튼을 비활성화하면
            # 입력한 연도가 사라지고 전체로 바뀌게 해달라"] 꺼질 때
            # 입력칸 값을 같이 지운다 — 안 지우면 다음에 다시 켰을 때
            # 예전 연도가 그대로 남아 있어 "전체"가 아니라 그 연도로
            # 바로 좁혀진 채 시작돼 버린다. textChanged가 _debounced_
            # refresh에도 연결돼 있어 clear() 한 번으로 아래 refresh와
            # 별개로 한 번 더 트리거되지만 디바운스라 실질 비용 없음.
            if not checked:
                self.player_natteam_year_edit.clear()
            _debounced_refresh()
        self.player_natteam_btn.toggled.connect(_on_natteam_toggled)
        self.player_natteam_year_edit.textChanged.connect(_debounced_refresh)
        filt.addWidget(self.player_natteam_year_edit)

        # [2026-08 신설, 신민용 요청: "필터에 현역이랑 은퇴 버튼을 만들고
        # 은퇴를 누르면 은퇴한 선수들만... 현역을 누르면 현역만... 기본
        # 상태는 현역"] 체크 가능한 버튼 2개를 QButtonGroup으로 묶어
        # 라디오처럼 배타적으로 동작시킨다.
        # [2026-08 신설, 신민용 요청: "내가 이름 바꾼 선수만 보고 싶다 —
        # 다른 필터와 같이 켜서 그 안에서만 걸러지게(AND)"] 체크 토글 —
        # 다른 라디오 버튼(현역/은퇴)과 달리 배타적이지 않은 독립 On/Off.
        _STATUS_BTN_STYLE = (
            "QPushButton{background:#2a2a2a;color:#888;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:4px 10px;font-size:11px;}"
            "QPushButton:checked{background:#0d3d1a;color:#00cc44;border-color:#00cc44;}")
        self.player_custom_named_btn = QPushButton("✏ 이름 변경만")
        self.player_custom_named_btn.setCheckable(True)
        self.player_custom_named_btn.setAutoDefault(False)
        self.player_custom_named_btn.setStyleSheet(_STATUS_BTN_STYLE)
        self.player_custom_named_btn.toggled.connect(_debounced_refresh)
        filt.addWidget(self.player_custom_named_btn)

        status_lbl = QLabel("상태"); status_lbl.setStyleSheet("color:#888;font-size:11px;")
        filt.addWidget(status_lbl)
        self.player_status_group = QButtonGroup(self)
        self.player_status_group.setExclusive(True)
        self.player_status_active_btn = QPushButton("현역")
        self.player_status_active_btn.setCheckable(True)
        self.player_status_active_btn.setChecked(True)
        self.player_status_active_btn.setAutoDefault(False)
        self.player_status_active_btn.setStyleSheet(_STATUS_BTN_STYLE)
        self.player_status_retired_btn = QPushButton("은퇴")
        self.player_status_retired_btn.setCheckable(True)
        self.player_status_retired_btn.setAutoDefault(False)
        self.player_status_retired_btn.setStyleSheet(_STATUS_BTN_STYLE)
        self.player_status_group.addButton(self.player_status_active_btn)
        self.player_status_group.addButton(self.player_status_retired_btn)
        self.player_status_active_btn.toggled.connect(_debounced_refresh)
        self.player_status_retired_btn.toggled.connect(_debounced_refresh)
        filt.addWidget(self.player_status_active_btn)
        filt.addWidget(self.player_status_retired_btn)
        filt.addStretch(1)
        lay.addLayout(filt)

        filt2 = QHBoxLayout()
        filt2.setSpacing(8)
        lbl3 = QLabel("등급"); lbl3.setStyleSheet("color:#888;font-size:11px;")
        self.player_grade_combo = QComboBox()
        self.player_grade_combo.addItem(_ALL)
        for g in wb.list_grades():
            self.player_grade_combo.addItem(g)
        self.player_grade_combo.currentTextChanged.connect(_debounced_refresh)
        filt2.addWidget(lbl3)
        filt2.addWidget(self.player_grade_combo)

        # [2026-08 제거, 신민용 리포트: "국가(소속리그)→리그 3단계 필터가
        # 생기면서 리그 콤보 자체가 이미 부수를 확정한다(레이블에 '(1부)'
        # 식으로 같이 뜬다) — 그런데 부수 콤보가 그대로 남아있어서, 리그를
        # 고른 뒤 부수가 그 리그의 실제 부수와 다르면(예: 이전 선택이
        # 남아있거나 실수로 다르게 골랐을 때) l.id=?와 l.tier=?가 동시에
        # AND로 걸려 결과가 0건으로 사라지는 충돌이 났다. 리그 선택이
        # 부수를 이미 포함하므로 별도 부수 필터는 중복이라 아예 제거."]
        # [2026-08 신설, 신민용 요청: "필터에 포지션 필터도 넣어야 한다"]
        # database.TEAM_POSITIONS(선수 생성 시 실제로 쓰이는 포지션 표기)를
        # 그대로 재사용 — 순서 유지 중복제거만 해서 GK부터 ST까지 나열한다.
        lbl5 = QLabel("포지션"); lbl5.setStyleSheet("color:#888;font-size:11px;")
        self.player_pos_combo = QComboBox()
        self.player_pos_combo.addItem(_ALL)
        for pos in dict.fromkeys(TEAM_POSITIONS):
            self.player_pos_combo.addItem(pos)
        self.player_pos_combo.currentTextChanged.connect(_debounced_refresh)
        filt2.addWidget(lbl5)
        filt2.addWidget(self.player_pos_combo)

        # [2026-08 신설, 신민용 요청: "나이도 필터에 포함하고 싶다"] 최소~
        # 최대 스핀박스 2개 — 기본값(0/60)은 사실상 "전체"를 뜻하고, 사용자가
        # 기본값에서 벗어나야만 실제 필터로 적용된다(_refresh_player_list
        # 참고). 축구 선수 실제 연령대(10대 후반~40대)를 넉넉히 덮는 범위.
        # [2026-08 수정, 신민용 요청: "기본 세팅을 0~99로 해야 클릭해서
        # 바로 숫자 변경이 가능하다"] "전체"라는 플레이스홀더 텍스트가
        # 앞에 붙어있으면 그것부터 지우고 숫자를 입력해야 했다 — 이제
        # 범위를 0~99로 넓히고 처음부터 실제 숫자(0, 99)가 그대로 보이게
        # 해서 클릭 즉시 숫자만 바꾸면 된다. setSpecialValueText 제거.
        lbl6 = QLabel("나이"); lbl6.setStyleSheet("color:#888;font-size:11px;")
        self.player_age_min_spin = QSpinBox()
        self.player_age_min_spin.setRange(0, 99)
        self.player_age_min_spin.setValue(0)
        self.player_age_min_spin.valueChanged.connect(_debounced_refresh)
        age_sep = QLabel("~"); age_sep.setStyleSheet("color:#888;")
        self.player_age_max_spin = QSpinBox()
        self.player_age_max_spin.setRange(0, 99)
        self.player_age_max_spin.setValue(99)
        self.player_age_max_spin.valueChanged.connect(_debounced_refresh)
        filt2.addWidget(lbl6)
        filt2.addWidget(self.player_age_min_spin)
        filt2.addWidget(age_sep)
        filt2.addWidget(self.player_age_max_spin)

        # [2026-08 신설, 신민용 요청: "선수 기간(경력)도 나이처럼 0~99
        # 필터를 만들고 싶다 — 기본 상태는 전체"] 경력 기준은 ai_player_
        # ovr_history(매 시즌 종료 시 한 줄씩 쌓이는 아카이브)에 이 선수
        # id로 쌓인 행 수 — "2017, 2018, 2019 이렇게 3개면 3년"(신민용
        # 확정). 나이 필터와 완전히 같은 패턴(0~99, 기본값 그대로면
        # 무필터, wb.search_ai_players의 min/max_career_years로 전달).
        lbl7 = QLabel("경력"); lbl7.setStyleSheet("color:#888;font-size:11px;")
        self.player_career_min_spin = QSpinBox()
        self.player_career_min_spin.setRange(0, 99)
        self.player_career_min_spin.setValue(0)
        self.player_career_min_spin.setSuffix("년")
        self.player_career_min_spin.valueChanged.connect(_debounced_refresh)
        career_sep = QLabel("~"); career_sep.setStyleSheet("color:#888;")
        self.player_career_max_spin = QSpinBox()
        self.player_career_max_spin.setRange(0, 99)
        self.player_career_max_spin.setValue(99)
        self.player_career_max_spin.setSuffix("년")
        self.player_career_max_spin.valueChanged.connect(_debounced_refresh)
        filt2.addWidget(lbl7)
        filt2.addWidget(self.player_career_min_spin)
        filt2.addWidget(career_sep)
        filt2.addWidget(self.player_career_max_spin)

        # [2026-08 신설, 신민용 요청: "이름 검색이 팀/국적 등과 겹쳐서
        # 충돌나는게 불편, 앞에 필터를 달아서 전체/이름으로 나눠달라 —
        # 필터 초기화하면 이름이 기본값"] "이름"이면 이 선수 자신의
        # 식별용 필드(내가 지어준 이름 + 코드)만 매칭하고, "전체"면
        # 기존처럼 팀명/국적/리그명/국가명까지 다 같이 매칭한다.
        self.player_name_mode_combo = QComboBox()
        self.player_name_mode_combo.addItems([_ALL, "이름"])
        self.player_name_mode_combo.setCurrentIndex(1)
        self.player_name_mode_combo.currentIndexChanged.connect(_debounced_refresh)
        filt2.addWidget(self.player_name_mode_combo)

        self.player_search_box = QLineEdit()
        self.player_search_box.setPlaceholderText("🔎 식별코드(AI0001) · 국적 · 팀명 검색")
        self.player_search_box.textChanged.connect(_debounced_refresh)
        filt2.addWidget(self.player_search_box, 1)

        # [2026-08 신설, 신민용 요청: "우측 끝에 필터 초기화 버튼 — 누르면
        # 필터 전체가 초기화"] 최근 검색 초기화 버튼(_build_recent_search_row)
        # 과 동일한 톤으로 통일.
        self.player_filter_reset_btn = QPushButton("🔄 필터 초기화")
        self.player_filter_reset_btn.setAutoDefault(False)
        self.player_filter_reset_btn.setDefault(False)
        self.player_filter_reset_btn.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#888;border:1px solid #3a3a3a;"
            "border-radius:4px;padding:4px 10px;font-size:11px;}"
            "QPushButton:hover{color:#cc4444;border-color:#cc4444;}")
        self.player_filter_reset_btn.clicked.connect(self._on_player_filter_reset)
        filt2.addWidget(self.player_filter_reset_btn)
        lay.addLayout(filt2)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._player_split = split
        self.player_list = QListWidget()
        self.player_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.player_list.itemClicked.connect(self._on_player_selected)
        self.player_list.setItemDelegate(_GridRowDelegate(self, self.player_list))
        player_header = self._list_header_row([
            ("식별코드", self._NAME_COL_W, False),
            ("포지션", self._POS_COL_W, True),
            ("국적", self._NAT_COL_W, False),
            ("OVR", self._OVR_COL_W, True),
            ("등급", self._GRADE_COL_W, True),
            ("소속팀 · 리그(부수)", self._LEAGUE_COL_W, False),
        ])
        split.addWidget(self._wrap_list_with_header(self.player_list, player_header))

        # ── 우측 상세: 팀 검색 탭의 "연도별 기록" 박스와 같은 톤으로,
        # (1) 지금 소속팀의 대회별 기록 박스 (2) 국적 국가대표팀의 국제대회
        # 기록 박스 두 개를 세로로 쌓는다. 둘 다 내용에 맞춰 스스로 높이를
        # 잡고(내부 스크롤바 없음), 전체를 QScrollArea 하나로 감싸 필요할
        # 때만 바깥쪽이 스크롤된다.
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(10, 0, 0, 0)

        self._player_recent_row = self._build_recent_search_row(
            "player", self.player_search_box, self.player_list,
            lambda it: it.data(Qt.ItemDataRole.UserRole + 1),
            self._on_player_selected,
            refresh_fn=self._refresh_player_list, debounce_timer=self._player_filter_debounce,
            label_fn=self._recent_player_label)
        right_lay.addWidget(self._player_recent_row)

        # [2026-08 신설, 신민용 요청: "선수 검색에도 팀 검색처럼 복사하기
        # 버튼을 만들어서 이름/국적/나이/포지션/OVR/소속팀 같은 기본 정보와
        # 연도별(몇 살에 어느 팀, 그때 OVR, 그때 팀 성적) 기록을 전부
        # GPT/제미나이가 알아들을 수 있는 텍스트로 뽑아달라"] 팀 검색 탭의
        # team_copy_btn/_on_copy_team_history_clicked/_format_team_history_text와
        # 완전히 같은 패턴 — 대상만 선수로 바뀐다. 표는 화면 보기용이고,
        # 이 버튼은 _show_player_detail·_populate_player_team_box·
        # _populate_player_intl_box가 각자 채워두는 self._player_copy_*
        # 값들을 재조회 없이 그대로 재사용해 클립보드에 복사한다.
        player_title_row = QHBoxLayout()
        self.player_copy_btn = QPushButton("📋 기록 복사")
        self.player_copy_btn.setEnabled(False)
        self.player_copy_btn.setToolTip(
            "이 선수의 기본 정보와 연도별(나이·소속팀·그때 OVR·그때 팀 성적) 기록을 "
            "텍스트로 복사합니다(GPT/제미나이 등에 붙여넣기용)")
        self.player_copy_btn.clicked.connect(self._on_copy_player_history_clicked)
        player_title_row.addStretch(1)
        player_title_row.addWidget(self.player_copy_btn)
        right_lay.addLayout(player_title_row)

        # [2026-08 재수정, 신민용 요청: "왜 아직도 태그형으로 표시하는거?
        # 아래 연도별 기록처럼 그리드/테이블 형태로, 챔스 표시가 그렇게
        # 되어있잖아"] 개별 상자(_sized_copyable_field)를 가로로 늘어놓은
        # "태그형" 대신, 이 탭의 다른 표들(소속팀 대회 기록 등)과 완전히
        # 같은 QTableWidget 1행짜리 그리드로 바꾼다 — 이 파일 최상단의
        # _enable_plain_copy(tbl)를 그대로 재사용해 셀 선택 후 Ctrl+C나
        # 우클릭 "복사"로 복사되게 한다(챔스/리그/컵 등 "역대 기록" 표들과
        # 동일한 복사 방식).
        self.player_detail_placeholder = QLabel("← 왼쪽에서 선수를 선택하세요")
        self.player_detail_placeholder.setStyleSheet("color:#888;font-size:13px;")
        right_lay.addWidget(self.player_detail_placeholder)

        # [2026-08 재수정, 신민용 리포트: "클릭이 안 되고, 글자 길이에
        # 따라 상자 크기가 달라야 하는데 다 똑같다"] _make_self_sizing_table
        # 는 읽기전용 기록표용으로 설계돼 setSelectionMode(NoSelection)이
        # 박혀있어서(아래 소속팀 대회 기록 표처럼 클릭으로 선택할 일이
        # 없는 표들 전용) 셀을 아예 선택할 수가 없었다 — 그래서 복사도
        # 안 됐던 것. standing_tbl 등 실제로 선택·복사가 되는 "역대 기록"
        # 표들과 동일하게 plain QTableWidget으로 새로 만들고, 컬럼폭도
        # Stretch(균등분배) 대신 ResizeToContents(내용 길이만큼)로 바꾼다.
        self.player_detail_tbl = QTableWidget(0, 7)
        self.player_detail_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.player_detail_tbl.verticalHeader().setVisible(False)
        self.player_detail_tbl.setShowGrid(True)
        # [2026-08 버그수정, 신민용 리포트: "이름 헤더를 파란색으로
        # 표시해달라 했는데 안 된다"] 이 창 전체에 걸린 전역 스타일시트
        # (이 파일 상단, "QHeaderView::section { ... color:#888; ... }")가
        # 모든 표의 모든 헤더 칸 색을 강제로 회색 고정해서, 아래
        # setHorizontalHeaderItem에 준 개별 칸 foreground 색(파란색)이
        # 안 먹혔던 게 원인 — Qt 스타일시트는 QSS 규칙이 아이템 단위
        # foreground 데이터보다 항상 우선한다. 이 표 자신의 스타일시트에
        # "::section:first"(0번째 칸 전용 의사 상태, Qt가 지원)로 배경/
        # 패딩은 전역과 동일하게 맞추고 색만 덮어써서 "이름" 칸 하나만
        # 파란색으로 뜨게 한다.
        self.player_detail_tbl.setStyleSheet(
            "QTableWidget{gridline-color:#000; border:none;}"
            "QHeaderView::section{background:#252525;color:#888;border:none;padding:5px;}"
            "QHeaderView::section:first{color:#4da6ff;font-weight:bold;}")
        self.player_detail_tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # [2026-08 수정, 신민용 리포트: "모니터가 작아지면 글자가 잘린다"]
        # 이 표는 ResizeToContents라 칸 폭 자체는 내용 길이대로 정해지는데
        # (Stretch처럼 억지로 안 눌림), 표 위젯 자신은 Expanding이라 창이
        # 좁아지면 위젯 폭이 칸들의 합보다 작아진다 — 그런데 가로
        # 스크롤바가 꺼져 있어서 넘친 칸이 그냥 잘려 보이기만 하고 볼
        # 방법이 없었다. AsNeeded로 켜서 좁을 때만 스크롤바가 나타나게 한다.
        self.player_detail_tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.player_detail_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        # [2026-08 신설, 신민용 요청: "AICD8C 같은 식별코드로 뜨는 선수
        # 이름을 내가 직접 지을 수 있게, '이름' 헤더를 클릭하면 이름
        # 변경 창이 뜨고 그 헤더는 파란색으로 표시해달라"] setHorizontalHeaderLabels
        # 대신 QTableWidgetItem을 직접 넣어야 "이름" 칸에 클릭 툴팁을
        # 붙일 수 있다(색 자체는 위 ::section:first QSS가 담당 — 아이템
        # foreground는 QSS에 가려 무시되므로 굳이 다시 안 건다).
        _detail_headers = ["이름", "국적", "나이", "포지션", "OVR", "소속팀", "소속팀 국가"]
        for _col, _label in enumerate(_detail_headers):
            _hitem = QTableWidgetItem(_label)
            if _col == 0:
                _hitem.setToolTip("클릭하면 이 선수의 이름을 직접 지을 수 있습니다")
            self.player_detail_tbl.setHorizontalHeaderItem(_col, _hitem)
        self.player_detail_tbl.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.player_detail_tbl.horizontalHeader().setCursor(Qt.CursorShape.PointingHandCursor)
        self.player_detail_tbl.horizontalHeader().sectionClicked.connect(
            self._on_player_detail_header_clicked)
        _enable_plain_copy(self.player_detail_tbl)
        self.player_detail_tbl.hide()
        right_lay.addWidget(self.player_detail_tbl)

        # [2026-08 신설, 신민용 요청: "은퇴했으면... 그 아래에 표시를
        # 해서 몇 년도에 은퇴했는지 표시하는거야"] 아래 "소속팀 대회
        # 기록" 표는 은퇴 후에도 마지막 소속팀 기준 실제 연도별 기록을
        # 그대로 보여주므로(별도 삭제 안 함), 이 라벨은 그 표 바로
        # 아래에서 "이 선수는 은퇴했다"는 사실 자체만 짧게 보강해준다.
        self.player_retirement_note = QLabel("")
        self.player_retirement_note.setStyleSheet(
            "color:#ff8844;font-size:12px;font-weight:bold;")
        self.player_retirement_note.hide()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_body = QWidget()
        scroll_lay = QVBoxLayout(scroll_body)
        scroll_lay.setContentsMargins(0, 6, 0, 0)
        scroll_lay.setSpacing(14)

        # 박스1: 소속팀 대회별 기록 (팀 검색 탭과 완전히 같은 렌더링 재사용)
        team_box_title = QLabel("🏟 소속팀 대회 기록")
        team_box_title.setStyleSheet("color:#eee;font-size:13px;font-weight:bold;")
        scroll_lay.addWidget(team_box_title)
        # [2026-08 확장, 신민용 요청: "소속팀일 때 포지션이 뭐였는지도
        # 적어야 한다 — 팀마다 포지션이 다르다, 위(상단 요약행)의 주포와
        # 다르게 여기 아래는 그때그때의 세부 포지션. OVR이랑 소속팀 사이에
        # 넣어달라"] 컬럼을 8→9개로 늘리고 "소속팀"과 "OVR" 사이에 "포지션"
        # 을 끼워 넣는다(연도,소속팀,포지션,OVR,리그,국내컵,클럽대항전,
        # 슈퍼컵,클럽월드컵). 수상 요약 상자(player_team_award_tbl)는 항상
        # 이 표와 같은 컬럼 수·폭이어야 어긋나지 않으므로(팀 검색 탭과 동일
        # 원칙) 같이 늘린다.
        # [2026-08 재확장, 신민용 요청: "OVR과 소속팀 사이(요약행)에 있던
        # 주전/로테/대기/유망주 표시는 없애고, 대신 여기 연도별 기록에
        # 그 해 기준으로 표시해야 한다"] 9→10개로 다시 늘리고 "OVR" 바로
        # 뒤에 "역할"을 끼워 넣는다(연도,소속팀,포지션,OVR,역할,리그,
        # 국내컵,클럽대항전,슈퍼컵,클럽월드컵).
        self.player_team_award_tbl = self._make_self_sizing_table(10, no_scroll=True)
        self.player_team_award_tbl.horizontalHeader().setVisible(False)
        # [2026-08 버그수정, 신민용 리포트: "수상 상자가 소속팀/OVR 표시를
        # 인식 못 해서 아래 표와 폭이 안 맞고 잘려 보인다"] 아래
        # player_team_tbl은 포지션·OVR·역할 세 컬럼만 별도로 Fixed 폭을
        # 주는데(바로 아래), 이 award_tbl은 _make_self_sizing_table 기본값
        # 그대로(나머지 전부 Stretch)라 그 두 칸이 아래 표보다 넓게 계산돼
        # 버렸다 — 그 차이만큼 나머지 Stretch 칸들(소속팀·리그·국내컵 등)
        # 폭이 밀려 두 표 경계선이 어긋나 보였던 것. team_detail_tbl/
        # team_award_tbl 쌍과 동일한 원칙(두 표는 항상 같은 리사이즈 모드를
        # 써야 어긋나지 않는다)에 따라 이 표에도 똑같이 맞춘다.
        # [2026-08 신설] "소속팀" 칸(1)은 실제로는 _col_label이 만드는
        # 라벨 위젯(_LEAGUE_COL_W 폭)을 그대로 담는 자리라, 위 Interactive
        # 기본폭(130)만으로는 좁을 수 있다 — 그 라벨과 같은 폭으로 맞춘다.
        self.player_team_award_tbl.setColumnWidth(1, self._LEAGUE_COL_W)
        self.player_team_award_tbl.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed)
        self.player_team_award_tbl.setColumnWidth(2, self._POS_COL_W_WIDE)
        self.player_team_award_tbl.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Fixed)
        self.player_team_award_tbl.setColumnWidth(3, self._OVR_COL_W)
        self.player_team_award_tbl.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Fixed)
        self.player_team_award_tbl.setColumnWidth(4, self._ROLE_COL_W)
        scroll_lay.addWidget(self.player_team_award_tbl)
        self.player_team_tbl = self._make_self_sizing_table(10, no_scroll=True)
        self.player_team_tbl.setHorizontalHeaderLabels(
            ["연도", "소속팀", "포지션", "OVR", "역할", "리그", "국내컵", "클럽 대항전", "슈퍼컵", "클럽 월드컵"])
        # [2026-08 신설, 신민용 요청: "소속팀과 리그 사이에 어차피 최대
        # 100의 자리니 작은 상자칸 하나 넣고 OVR 표시"] 다른 칸은 폭을
        # 늘려 채우는(Stretch) 칸인데 이 칸만 숫자 3자리면 충분해서 고정폭.
        # [2026-08 확장] 포지션 칸도 "CM"/"ST" 같은 짧은 텍스트라 같은
        # 이유로 고정폭(선수 목록 포지션 칸과 같은 폭 재사용).
        self.player_team_tbl.setColumnWidth(1, self._LEAGUE_COL_W)
        self.player_team_tbl.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed)
        self.player_team_tbl.setColumnWidth(2, self._POS_COL_W_WIDE)
        self.player_team_tbl.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Fixed)
        self.player_team_tbl.setColumnWidth(3, self._OVR_COL_W)
        # [2026-08 신설] "역할"(주전/로테이션/대기/유망주) 칸도 짧은
        # 텍스트라 같은 이유로 고정폭.
        self.player_team_tbl.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Fixed)
        self.player_team_tbl.setColumnWidth(4, self._ROLE_COL_W)
        # [2026-08 버그수정] 창 크기 변화 등으로 Stretch 폭이 다시 계산될
        # 때 두 표가 계속 같은 값으로 맞춰지도록, team_detail_tbl/
        # team_award_tbl 쌍과 동일하게 sectionResized를 따라가게 연결
        # (중복 안전장치 — 이미 같은 리사이즈 모드라 보통은 저절로
        # 일치하지만, 최초 렌더 타이밍 차이에 대비).
        self.player_team_tbl.horizontalHeader().sectionResized.connect(
            lambda idx, _old, new: self.player_team_award_tbl.setColumnWidth(idx, new))
        # [2026-08 신설, 신민용 요청: "가로 스크롤을 만들어달라"] 두 표가
        # 이제 각자 자기 가로 스크롤바를 가질 수 있게 됐는데(_make_self_
        # sizing_table이 AsNeeded로 켰음), "수상" 요약 행(award_tbl)은
        # player_team_tbl과 같은 칸 폭을 그대로 따라가는 "얼어붙은 헤더
        # 아래 한 줄"일 뿐이라 자기 스크롤바가 따로 나타나면 스크롤바가
        # 두 줄로 겹쳐 보이고, 사용자가 아래 표만 옆으로 밀면 위 수상
        # 행은 그대로 있어서 칸이 어긋나 보인다 — award_tbl 자신의
        # 가로 스크롤바는 꺼두고, 대신 player_team_tbl을 옆으로 밀 때마다
        # 그 스크롤 위치를 그대로 따라가게 연결해서 항상 같이 움직인다.
        self.player_team_award_tbl.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.player_team_tbl.horizontalScrollBar().valueChanged.connect(
            self.player_team_award_tbl.horizontalScrollBar().setValue)
        scroll_lay.addWidget(self.player_team_tbl)
        scroll_lay.addWidget(self.player_retirement_note)

        # 박스2: 국가대표 출전 기록.
        # [2026-08 재도입, 신민용 요청: "'예선전 탈락' 같은 개인 기록도
        # 표시해줘"] 예전엔 이 자리에 있던 박스를 뺐었다(2026-08 제거,
        # 신민용 리포트: "나간 애들만 떠야 하는데 안 나간 애들도 자기
        # 나라 기록이 다 뜬다") — 그때는 AI 선수가 실제로 어느 국제대회
        # 출전 명단에 뽑혔는지를 어디에도 저장하지 않아서, "이 선수의
        # 국적 국가" 전체 기록을 선수 개인 기록인 것처럼 보여줄 수밖에
        # 없었다(선수 개인 출전 여부와 무관해 오해를 줌). 그 사이 대회
        # 내내 고정되는 26인 명단 테이블(intl_squad)이 새로 생기면서
        # "이 선수가 실제로 이 대회 명단에 뽑혔었는가"가 정확히 기록되기
        # 시작했다 — wb.get_player_intl_records가 그 명단 기록만 걸러서
        # 돌려주므로 이제 다시 정확하게 보여줄 수 있다.
        intl_box_title = QLabel("🌍 국가대표 출전 기록")
        intl_box_title.setStyleSheet("color:#eee;font-size:13px;font-weight:bold;")
        scroll_lay.addWidget(intl_box_title)
        self.player_intl_tbl = self._make_self_sizing_table(5, no_scroll=True)
        self.player_intl_tbl.setHorizontalHeaderLabels(
            ["연도", "대회", "국가", "출전", "결과"])
        # [2026-08 신설] "대회" 칸은 "2000 유럽 네이션스컵 예선"처럼 길게
        # 나올 수 있어 Interactive 기본폭(130)으로는 좁을 수 있다 — 넓힌다.
        self.player_intl_tbl.setColumnWidth(1, 210)
        self.player_intl_tbl.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Fixed)
        self.player_intl_tbl.setColumnWidth(3, self._OVR_COL_W)
        scroll_lay.addWidget(self.player_intl_tbl)

        # [2026-08 신설] intl_squad는 2026-08부터 생긴 테이블이라 그 전에
        # 이미 끝났거나 그 시점에 진행 중이던 대회는 이 선수가 그때
        # 명단에 뽑혔었는지 자체가 기록에 없다 — 정확도의 한계를 짧게
        # 안내(위 박스가 비어 보이거나 최근 대회만 있어도 버그가 아님).
        future_note = QLabel("ℹ️ 위 출전 기록은 (2026-08 기준) 대회 내내 고정되는 명단이 "
                             "도입된 이후 실제로 명단에 뽑혔던 대회만 표시됩니다 — "
                             "그 이전에 이미 끝났거나 진행 중이던 대회는 기록이 없을 수 있습니다.")
        future_note.setStyleSheet("color:#666;font-size:11px;")
        future_note.setWordWrap(True)
        scroll_lay.addWidget(future_note)
        scroll_lay.addStretch(1)

        scroll.setWidget(scroll_body)
        right_lay.addWidget(scroll, 1)

        split.addWidget(right)
        split.setSizes([440, 900])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        self._refresh_player_nat_combo()
        self._refresh_player_list()
        return w

    def _make_self_sizing_table(self, n_cols, no_scroll=False):
        """[2026-08 신설] "선수 검색" 우측 두 박스(소속팀 기록/국가대표
        기록)용 — team_detail_tbl과 같은 톤(어두운 배경, 격자선)이지만
        스플리터 안에서 독립 스크롤 없이 내용 높이만큼만 차지하도록
        만든 QTableWidget. no_scroll=True면 자체 세로 스크롤바를 끄고
        바깥 QScrollArea 하나에만 맡긴다(중첩 스크롤 방지 — 세로만
        해당, 아래 가로 스크롤바는 no_scroll과 무관하게 항상 켠다).

        [2026-08 수정, 신민용 리포트: "모니터가 작아지면 글자가 잘린다
        — 가로 스크롤을 만드는 게 낫다"] Stretch 칸은 창이 좁아지면
        내용 길이와 무관하게 끝없이 눌려서 텍스트가 잘렸는데(예:
        "22승 5무 11패"), 가로 스크롤바 자체도 꺼져 있어서(ScrollBarAlwaysOff)
        잘린 부분을 볼 방법이 없었다. Stretch(칸들이 뷰포트 폭에 맞춰
        끝없이 늘었다 줄었다 함) 대신 Interactive(칸마다 고정폭으로
        시작하고, 창이 좁아져도 그 밑으로 안 줄어듦 — 사용자가 드래그로
        직접 조절하는 것만 반영)로 바꾸고 가로 스크롤바를 AsNeeded로
        켠다. 창이 넓을 땐 남는 오른쪽 여백이 예전 Stretch만큼 꽉 안
        채워질 수 있지만(트레이드오프), 창이 좁아져도 글자가 잘리는 대신
        표 전체가 뷰포트보다 넓어지면서 가로 스크롤바가 나타나 옆으로
        움직여 볼 수 있다 — "잘려서 안 보임" → "스크롤해서 다 보임"으로
        바뀌는 것이 이번 수정의 핵심. 호출부(player_team_tbl 등)가 특정
        칸(포지션/OVR/역할처럼 원래도 짧은 텍스트)을 Fixed로 다시
        덮어쓰는 것은 그대로 유지된다(이 루프보다 나중에 실행되므로)."""
        tbl = QTableWidget(0, n_cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tbl.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tbl.setShowGrid(True)
        tbl.setStyleSheet("QTableWidget{gridline-color:#000; border:none;}")
        if no_scroll:
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(0, self._YEAR_COL_W if hasattr(self, "_YEAR_COL_W") else 64)
        for c in range(1, n_cols):
            tbl.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            tbl.setColumnWidth(c, 130)
        tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        return tbl

    def _resize_self_sizing_table(self, tbl):
        """행이 다 채워진 뒤 호출 — 내용에 맞춰 표 자체의 높이를 고정한다
        (내부 스크롤 없이, 바깥 QScrollArea가 전체를 스크롤하게 하기 위함).
        Stretch 컬럼 폭은 레이아웃이 실제로 자리잡은 뒤에야 확정되므로,
        팀 검색 탭(_finalize_team_detail_row_heights)과 같은 이유로 한 프레임
        뒤에 다시 계산한다."""
        _PAD = 6

        def _fix():
            tbl.resizeRowsToContents()
            total = tbl.horizontalHeader().height() + 2
            for r in range(tbl.rowCount()):
                tbl.setRowHeight(r, tbl.rowHeight(r) + _PAD)
                total += tbl.rowHeight(r)
            tbl.setFixedHeight(max(total, tbl.horizontalHeader().height() + 24))

        tbl.resizeRowsToContents()
        QTimer.singleShot(0, _fix)

    def _make_combo_typable(self, combo):
        """[2026-08 신설, 신민용 요청: "직접 입력하는 칸들도 만들어도
        될듯"] 콤보를 편집 가능하게 하고, 콤보 자신의 항목 목록(model)을
        그대로 소스로 쓰는 QCompleter를 붙여 부분일치(MatchContains) 자동
        완성이 뜨게 한다 — 211개국 드롭다운을 매번 스크롤하는 대신 몇 글자
        입력해서 좁힐 수 있다. InsertPolicy를 NoInsert로 둬서 목록에 없는
        임의 문자열을 새 항목으로 추가하진 않는다(값은 항상 실제 존재하는
        항목 중 하나로만 확정 — 기존 id 역매칭 로직이 그대로 통한다).

        [2026-08 신설, 신민용 요청: "대한민국 치고 엔터 누르면 자동으로
        🇰🇷 대한민국 이렇게 붙게 하고 싶어"] 항목 표시 텍스트는 항상
        "국기 대한민국"처럼 국기 이모지가 붙어있어서, 완성 목록 팝업에서
        직접 클릭하지 않고 그냥 타이핑 후 엔터만 치면 findText가 정확히
        일치하는 항목을 못 찾아 필터가 적용 안 됐다 — 엔터 시 타이핑한
        텍스트를 포함하는 첫 항목으로 자동 스냅시킨다.

        [2026-08 신설, 신민용 요청: "화살표가 안 보이니 파란색으로"]
        setEditable(True)로 바뀌면서 드롭다운 화살표가 입력칸과 같은
        톤이라 잘 안 보였다 — QComboBox::down-arrow를 실제 이미지 리소스
        없이 CSS 테두리 삼각형 트릭으로 그려서 파란색으로 눈에 띄게 한다."""
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        completer = QCompleter(combo.model(), combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        combo.setCompleter(completer)
        combo.setStyleSheet(
            "QComboBox::drop-down{border:none;width:20px;}"
            "QComboBox::down-arrow{image:none;width:0;height:0;"
            "border-left:4px solid transparent;border-right:4px solid transparent;"
            "border-top:6px solid #4da6ff;margin-right:6px;}")
        combo.lineEdit().returnPressed.connect(lambda c=combo: self._resolve_typed_combo(c))

    def _resolve_typed_combo(self, combo):
        """엔터를 눌렀을 때, 타이핑한 텍스트를 포함하는 첫 항목으로
        스냅(국기+국가명 형태로 자동 완성). 이미 정확히 일치하는 항목을
        타이핑했으면(팝업에서 골랐거나 국기까지 직접 쳤으면) 그대로 둔다.
        [2026-08 확장, 신민용 요청: "파워랭킹 국가 필터도 선수 검색
        국적처럼 직접 입력되게"] 예전엔 이 함수가 "선수 검색" 전용
        디바운스 타이머(self._player_filter_debounce)를 무조건 호출했다
        — 다른 탭(파워랭킹 등)의 콤보에 이 헬퍼를 재사용하면 정작 그
        탭의 목록은 안 갱신되고 엉뚱하게 선수 검색만 다시 조회되는
        부작용이 있었다. blockSignals 없이 자연스럽게 setCurrentIndex/
        setEditText를 호출해서, 그 콤보에 실제로 연결된
        currentTextChanged 핸들러(어느 탭이든)가 알아서 반응하게 한다."""
        typed = combo.currentText().strip()
        if not typed:
            return
        if combo.findText(typed) >= 0:
            return
        needle = typed.lower()
        for i in range(combo.count()):
            item_text = combo.itemText(i)
            if needle in item_text.lower():
                combo.setCurrentIndex(i)
                combo.setEditText(item_text)
                break

    def _on_player_filter_reset(self):
        """[2026-08 신설, 신민용 요청: "우측 끝에 필터 초기화 버튼 —
        누르면 필터 전체가 초기화"] 모든 필터 위젯을 기본값으로 되돌리고
        딱 한 번만 재조회한다(각 위젯 리셋마다 개별 신호가 튀지 않도록
        blockSignals로 막아둔 채 값만 바꾼 뒤 마지막에 한 번에 반영)."""
        for combo in (self.player_nat_cont_combo, self.player_nat_combo, self.player_country_combo,
                      self.player_grade_combo, self.player_pos_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.player_age_min_spin.blockSignals(True)
        self.player_age_min_spin.setValue(0)
        self.player_age_min_spin.blockSignals(False)
        self.player_age_max_spin.blockSignals(True)
        self.player_age_max_spin.setValue(99)
        self.player_age_max_spin.blockSignals(False)
        # [2026-08 신설] 경력(년) 필터도 기본값(0/99)으로 되돌린다.
        self.player_career_min_spin.blockSignals(True)
        self.player_career_min_spin.setValue(0)
        self.player_career_min_spin.blockSignals(False)
        self.player_career_max_spin.blockSignals(True)
        self.player_career_max_spin.setValue(99)
        self.player_career_max_spin.blockSignals(False)
        self.player_search_box.blockSignals(True)
        self.player_search_box.clear()
        self.player_search_box.blockSignals(False)
        # [2026-08 신설] 이름 검색 필터도 기본값("이름")으로 되돌린다 —
        # "전체"가 아니라 "이름"이 기본이라는 점에 주의(요청 확정 사항).
        self.player_name_mode_combo.blockSignals(True)
        self.player_name_mode_combo.setCurrentIndex(1)
        self.player_name_mode_combo.blockSignals(False)
        # [2026-08 신설] "이름 변경만" 토글도 꺼짐으로 되돌린다.
        self.player_custom_named_btn.blockSignals(True)
        self.player_custom_named_btn.setChecked(False)
        self.player_custom_named_btn.blockSignals(False)
        self.player_status_active_btn.blockSignals(True)
        self.player_status_retired_btn.blockSignals(True)
        self.player_status_active_btn.setChecked(True)
        self.player_status_active_btn.blockSignals(False)
        self.player_status_retired_btn.blockSignals(False)
        # [2026-08 신설] 국가대표 필터도 기본(꺼짐, 연도칸 비움+비활성화)
        # 으로 되돌린다 — "필터 초기화를 하면 국대 유무 표시를 안 하고
        # 전부 보이는 형태"(신민용 확정).
        self.player_natteam_btn.blockSignals(True)
        self.player_natteam_btn.setChecked(False)
        self.player_natteam_btn.blockSignals(False)
        self.player_natteam_year_edit.blockSignals(True)
        self.player_natteam_year_edit.clear()
        self.player_natteam_year_edit.setEnabled(False)
        self.player_natteam_year_edit.blockSignals(False)
        # [2026-08 신설] 국가(소속리그)가 "전체"로 리셋됐으니 리그/팀
        # 콤보도 "전체" 하나만 남기고 비활성화로 되돌린다 — 안 그러면
        # 이전 국가 선택으로 좁혀진 리그/팀 목록이 그대로 남아있게 된다.
        self.player_league_combo.blockSignals(True)
        self.player_league_combo.clear()
        self.player_league_combo.addItem(_ALL)
        self.player_league_combo.setEnabled(False)
        self.player_league_combo.blockSignals(False)
        self._player_league_cache = []
        self.player_team_combo.blockSignals(True)
        self.player_team_combo.clear()
        self.player_team_combo.addItem(_ALL)
        self.player_team_combo.setEnabled(False)
        self.player_team_combo.blockSignals(False)
        self._player_team_cache = []
        self.player_team_career_btn.blockSignals(True)
        self.player_team_career_btn.setChecked(False)
        self.player_team_career_btn.blockSignals(False)
        # [2026-08 신설, 신민용 요청: "필터 초기화 기본값은 (팀 직접입력에)
        # 아무것도 입력되지 않은 것"] 직접입력 칸/매치/전용 디바운스까지
        # 전부 비운다 — _clear_player_team_direct_input()이 정확히 이 일을
        # 하므로 그대로 재사용.
        self._clear_player_team_direct_input()
        self._player_filter_debounce.stop()
        # 대륙(국적)이 "전체"로 리셋됐으니 "국적" 콤보도 전체 국가 목록으로
        # 다시 채워야 한다(단순 setCurrentIndex(0)만으로는 목록 자체가
        # 이전 대륙 선택으로 좁혀진 채 남아있음).
        self._refresh_player_nat_combo()
        self._refresh_player_list()

    def _on_player_nat_continent_changed(self, *_a):
        self._refresh_player_nat_combo()
        self._player_filter_debounce.start()

    def _refresh_player_nat_combo(self):
        """[2026-08 수정] "국적" 콤보 — 이제 "대륙(국적)" 콤보 선택에 따라
        후보 국가가 바뀐다(둘 다 선수의 실제 국적 기준). "국가(소속리그)"
        콤보는 이제 이 대륙 선택과 무관하게 항상 전체 국가를 보여준다
        (독립 필터가 됐으므로 _build_player_search_tab에서 전체 목록으로
        한 번만 채워두고 여기서 다시 안 건드림)."""
        cont = None if self.player_nat_cont_combo.currentText() == _ALL else self.player_nat_cont_combo.currentText()
        cur = self.player_nat_combo.currentText()
        self.player_nat_combo.blockSignals(True)
        self.player_nat_combo.clear()
        self.player_nat_combo.addItem(_ALL)
        countries = wb.list_countries(continent=cont)
        # [2026-08 수정, 신민용 요청: "국기 이모지 앞에 KR 같은 글자
        # 없애서 대한민국 이렇게 뜨게"] 국적 콤보도 소속리그 콤보와
        # 동일하게 국기 프리픽스 제거.
        for c in countries:
            self.player_nat_combo.addItem(c["name"])
        idx = self.player_nat_combo.findText(cur)
        self.player_nat_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.player_nat_combo.blockSignals(False)
        self._player_nat_cache = countries

    def _selected_player_nat_country_id(self):
        txt = self.player_nat_combo.currentText()
        if txt == _ALL:
            return None
        for c in getattr(self, "_player_nat_cache", []):
            if c["name"] == txt:
                return c["id"]
        return None

    def _selected_player_club_country_id(self):
        """"국가(소속리그)" 콤보 — 대륙 선택과 무관하게 항상 전체 국가
        목록이므로, wb.list_countries()를 다시 조회해 이름으로 역매칭한다
        (국적 콤보처럼 대륙 변경 때마다 다시 채워지는 캐시가 없어서, 매번
        전체 목록에서 찾는다 — 211개국 규모라 비용은 무시할 수준)."""
        txt = self.player_country_combo.currentText()
        if txt == _ALL:
            return None
        for c in wb.list_countries():
            if c["name"] == txt:
                return c["id"]
        return None

    # [2026-08 신설, 신민용 요청: "국가(소속리그) → 리그 → 팀 3단계
    # 필터"] 국가(소속리그) 콤보가 바뀔 때마다 리그 콤보를 그 나라의
    # 부수별 리그 목록으로 다시 채운다(전체로 돌아가면 리그/팀 둘 다
    # "전체" 하나만 남기고 비활성화). 리그가 바뀌므로 팀 콤보도 항상
    # 같이 리셋한다.
    def _on_player_club_country_changed(self, *_a):
        # [2026-08 신설] 팀 직접입력으로 자동 채워진 상태에서 사용자가
        # 국가(소속리그)를 손으로 바꾸면, 그 직접입력 값이 방금 세팅한
        # 국가와 어긋나 버리므로(계속 예전 팀 기준으로 조회) 직접입력
        # 칸을 비워 "다시 일반 필터 모드"로 되돌린다. 이 콤보를 직접입력
        # 로직 스스로 세팅할 때는 항상 blockSignals로 감싸므로, 여기까지
        # 신호가 도달했다는 건 사용자가 실제로 건드렸다는 뜻이다.
        self._clear_player_team_direct_input()
        self._refresh_player_league_combo()
        self._refresh_player_team_combo()
        self._player_filter_debounce.start()

    def _refresh_player_league_combo(self):
        club_cid = self._selected_player_club_country_id()
        self.player_league_combo.blockSignals(True)
        self.player_league_combo.clear()
        self.player_league_combo.addItem(_ALL)
        leagues = wb.list_leagues_for_country(club_cid) if club_cid else []
        _TIER_SUFFIX = {1: "1부", 2: "2부", 3: "3부", 4: "4부", 5: "5부", 6: "6부", 7: "7부"}
        for lg in leagues:
            suffix = _TIER_SUFFIX.get(lg["tier"], f"{lg['tier']}부")
            self.player_league_combo.addItem(f"{lg['name']} ({suffix})")
        self.player_league_combo.setCurrentIndex(0)
        self.player_league_combo.setEnabled(bool(leagues))
        self.player_league_combo.blockSignals(False)
        self._player_league_cache = leagues

    def _selected_player_league_id(self):
        idx = self.player_league_combo.currentIndex()
        cache = getattr(self, "_player_league_cache", [])
        # 콤보 0번은 항상 "전체"라 idx-1이 캐시 인덱스와 맞물린다
        # (표시 문자열엔 "(1부)" 같은 접미사가 붙어서 이름으로 역매칭할
        # 수 없으므로, 채워 넣은 순서를 그대로 따르는 인덱스 매칭을 쓴다).
        if idx <= 0 or idx - 1 >= len(cache):
            return None
        return cache[idx - 1]["id"]

    # [2026-08 신설] 리그 콤보가 바뀔 때마다 팀 콤보를 그 리그 소속
    # 팀 목록으로 다시 채운다("전체"로 돌아가면 팀도 "전체" 하나만
    # 남기고 비활성화 — 국가만 고르고 리그를 안 고른 상태에서 팀까지
    # 고르게 하면 어느 부수 팀인지 모호해지므로 막는다).
    def _on_player_league_changed(self, *_a):
        # [2026-08 신설] 위 _on_player_club_country_changed와 같은 이유 —
        # 리그를 손으로 바꾸면 팀 직접입력 값이 어긋나므로 비운다.
        self._clear_player_team_direct_input()
        self._refresh_player_team_combo()
        self._player_filter_debounce.start()

    # [2026-08 신설] "팀" 콤보를 사용자가 직접 고르는 경우도 팀 직접입력
    # 값과 어긋날 수 있으므로(직접입력이 우선 적용되던 상태에서 콤보를
    # 또 손으로 바꾸면 어느 쪽을 따라야 할지 모호해짐) 마찬가지로 비운
    # 뒤 기존과 동일하게 디바운스 재조회.
    def _on_player_team_combo_changed(self, *_a):
        self._clear_player_team_direct_input()
        self._player_filter_debounce.start()

    def _clear_player_team_direct_input(self):
        """[2026-08 신설] 팀 직접입력 칸/매치 상태를 비운다. 텍스트가
        이미 비어 있으면 아무 것도 안 함(불필요한 신호 방지). textChanged
        가 다시 디바운스를 돌리지 않도록 blockSignals로 감싼다 — 이 함수는
        국가/리그/팀 콤보를 "사용자가 직접" 바꿨을 때만 호출되므로, 그
        직후 이어지는 _player_filter_debounce.start()가 정확한 한 번의
        재조회를 담당한다."""
        self._player_team_direct_debounce.stop()
        self._player_team_direct_match = None
        if self.player_team_direct_edit.text():
            self.player_team_direct_edit.blockSignals(True)
            self.player_team_direct_edit.clear()
            self.player_team_direct_edit.blockSignals(False)
        self.player_team_direct_edit.setStyleSheet("")

    def _apply_player_team_direct_input(self):
        """[2026-08 신설, 신민용 요청: "팀 직접입력에 입력하면 앞의 국가
        (소속리그)/리그가 그 팀이 지금 있는 곳에 맞춰 자동으로 채워지게 —
        팀 콤보 자체는 뒤에 입력한 텍스트가 곧 팀 필터가 되니 안 바뀌어도
        됨"] player_team_direct_edit.textChanged가 시작시킨 전용 디바운스
        (_player_team_direct_debounce, 300ms)가 끝난 뒤에만 실행된다 —
        타이핑 중간중간 매번 DB 조회 + 국가/리그 콤보 재구성이 겹치면
        버벅이는 걸 막기 위함(신민용이 직접 짚은 최적화 우려 대응).
        찾은 팀은 self._player_team_direct_match에 저장해 두고
        _refresh_player_list가 팀/리그/국가 필터로 그대로 재사용한다."""
        text = self.player_team_direct_edit.text().strip()
        self._player_team_direct_match = None
        if not text:
            self.player_team_direct_edit.setStyleSheet("")
            self._player_filter_debounce.start()
            return
        match = wb.find_team_by_name(text)
        if not match:
            # 일치하는 팀이 없으면 조용히 무시하지 않고 테두리를 빨갛게
            # 표시해 "이 이름의 팀을 못 찾았다"는 걸 바로 알 수 있게 한다.
            self.player_team_direct_edit.setStyleSheet(
                "QLineEdit{border:1px solid #cc4444;}")
            self._player_filter_debounce.start()
            return
        self.player_team_direct_edit.setStyleSheet("")
        self._player_team_direct_match = match
        # 아래 두 콤보는 blockSignals로 감싼 채 세팅한다 — 그래야
        # _on_player_club_country_changed/_on_player_league_changed가
        # "사용자가 직접 바꿨다"고 오인해 방금 채운 이 직접입력 값을
        # 스스로 지워버리는 걸(위 두 핸들러 맨 앞 참고) 막을 수 있다.
        self.player_country_combo.blockSignals(True)
        idx = self.player_country_combo.findText(match["country_name"])
        if idx >= 0:
            self.player_country_combo.setCurrentIndex(idx)
        self.player_country_combo.blockSignals(False)
        self._refresh_player_league_combo()
        cache = getattr(self, "_player_league_cache", [])
        target_idx = 0
        for i, lg in enumerate(cache):
            if lg["id"] == match["league_id"]:
                target_idx = i + 1  # 콤보 0번은 항상 "전체"
                break
        self.player_league_combo.blockSignals(True)
        self.player_league_combo.setCurrentIndex(target_idx)
        self.player_league_combo.blockSignals(False)
        # 팀 콤보는 목록만 그 리그 소속으로 새로 채워 넣고("전체"인
        # 상태로) 선택 자체는 건드리지 않는다 — 실제 팀 필터는 아래
        # _refresh_player_list에서 이 직접입력 매치를 우선 사용한다.
        self._refresh_player_team_combo()
        self._player_filter_debounce.start()

    def _refresh_player_team_combo(self):
        league_id = self._selected_player_league_id()
        self.player_team_combo.blockSignals(True)
        self.player_team_combo.clear()
        self.player_team_combo.addItem(_ALL)
        teams = wb.list_teams_in_league(league_id) if league_id else []
        for t in teams:
            self.player_team_combo.addItem(t["name"])
        self.player_team_combo.setCurrentIndex(0)
        self.player_team_combo.setEnabled(bool(teams))
        self.player_team_combo.blockSignals(False)
        self._player_team_cache = teams

    def _selected_player_team_id(self):
        txt = self.player_team_combo.currentText()
        if txt == _ALL:
            return None
        for t in getattr(self, "_player_team_cache", []):
            if t["name"] == txt:
                return t["id"]
        return None

    def _refresh_player_list(self, *_a):
        cont = None if self.player_nat_cont_combo.currentText() == _ALL else self.player_nat_cont_combo.currentText()
        nat_cid = self._selected_player_nat_country_id()
        club_cid = self._selected_player_club_country_id()
        grade = None if self.player_grade_combo.currentText() == _ALL else self.player_grade_combo.currentText()
        # [2026-08 제거] 부수 필터 삭제 — 리그 필터가 이미 부수를 확정하므로
        # search_ai_players에는 항상 tier=None(무필터)을 넘긴다.
        tier = None
        pos = None if self.player_pos_combo.currentText() == _ALL else self.player_pos_combo.currentText()
        # [2026-08 수정] 나이 스핀박스 기본값이 (0/99)로 바뀌었으므로
        # "전체" 판정 기준도 60→99로 맞춘다.
        min_age = self.player_age_min_spin.value() or None
        max_age_v = self.player_age_max_spin.value()
        max_age = max_age_v if max_age_v < 99 else None
        # [2026-08 신설] 경력(년) 필터 — 나이 필터와 동일한 "기본값(0/99)
        # 이면 무필터" 규칙.
        min_career_years = self.player_career_min_spin.value() or None
        max_career_years_v = self.player_career_max_spin.value()
        max_career_years = max_career_years_v if max_career_years_v < 99 else None
        q = self.player_search_box.text().strip() or None
        name_mode = "code" if self.player_name_mode_combo.currentText() == "이름" else "all"
        custom_named_only = self.player_custom_named_btn.isChecked()
        status = "retired" if self.player_status_retired_btn.isChecked() else "active"
        # [2026-08 신설] 국가대표 유무 필터 — 버튼이 꺼져 있으면 natteam=False
        # (필터 없음, 전부 보임). 켜져 있으면 natteam=True + 연도칸이
        # 비어있으면 natteam_year=None("전체" — 어느 연도든), 채워져
        # 있으면 그 연도로 좁힌다.
        natteam = self.player_natteam_btn.isChecked()
        _nt_year_txt = self.player_natteam_year_edit.text().strip()
        natteam_year = int(_nt_year_txt) if (natteam and _nt_year_txt) else None
        # [2026-08 신설] 국가(소속리그) → 리그 → 팀 3단계 필터. team_mode는
        # "경력 포함" 토글에 따라 현재/마지막 소속만 볼지, 과거 팀 경력
        # 까지 볼지를 가른다 — [2026-08 수정, 신민용 요청: "은퇴 검색도
        # 이 토글을 따르게(꺼짐=은퇴 직전 팀 기준, 켜짐=경력에 있으면
        # 전부)"] 예전엔 은퇴 검색은 이 토글과 무관하게 항상 "경력"으로
        # 고정돼 있었는데, 이제 현역과 똑같이 이 값을 그대로 따른다.
        # league_id도 [2026-08 버그수정, 신민용 리포트: "은퇴 상태에서
        # 국가(소속리그)/리그만 고르고 팀은 '전체'로 두면 그 나라·리그와
        # 전혀 무관한 선수가 뜬다"] 대응으로 새로 넘긴다 — 팀까지 구체적
        # 으로 안 고른 채 리그만 골라도 그 리그 안에서 걸러지도록.
        team_id = self._selected_player_team_id()
        league_id = self._selected_player_league_id()
        # [2026-08 신설, 신민용 요청: "팀 직접입력에 값을 넣으면 그게 곧
        # 팀 필터 — 팀 콤보는 뒤에 입력한 게 있으니 안 바뀌어도 됨"] 팀
        # 콤보는 직접입력을 써도 일부러 "전체"로 남겨두므로(위 _apply_
        # player_team_direct_input 참고) 여기서 위 _selected_player_team_id()
        # 결과(None)를 그 매치된 팀 id로 덮어쓴다. 국가(소속리그)/리그는
        # 이미 그 함수가 콤보 자체를 팀 기준으로 세팅해 두었으므로 위
        # club_cid/league_id도 자연히 그 팀의 국가/리그와 일치한다.
        _direct_match = getattr(self, "_player_team_direct_match", None)
        if self.player_team_direct_edit.text().strip() and _direct_match:
            team_id = _direct_match["team_id"]
        team_mode = "career" if self.player_team_career_btn.isChecked() else "current"
        players = wb.search_ai_players(name_query=q, continent=cont, country_id=club_cid,
                                        nat_country_id=nat_cid, grade=grade, tier=tier,
                                        position=pos, min_age=min_age, max_age=max_age,
                                        status=status, limit=300,
                                        natteam=natteam, natteam_year=natteam_year,
                                        team_id=team_id, team_mode=team_mode,
                                        league_id=league_id, name_mode=name_mode,
                                        custom_named_only=custom_named_only,
                                        min_career_years=min_career_years,
                                        max_career_years=max_career_years)

        self.player_list.clear()
        for pl in players:
            # [2026-08 신설, 신민용 요청: "내 이름도 떠야 하고 다른
            # 선수들과 다르게 이름으로 찾아지지만 내용은 똑같다"] my_player
            # (MY_PLAYER_ID)만 실명을 그대로 표시하고, 나머지는 기존처럼
            # ai_player_code로 가린다.
            # [2026-08 확장, 신민용 요청: "AICD8C 식별코드로 뜨는 선수의
            # 이름을 내가 지을 수 있게"] search_ai_players/
            # search_retired_ai_players가 이제 custom_name도 같이
            # 주므로, 지정된 이름이 있으면 코드 대신 그 이름을 쓴다.
            code = pl["name"] if pl["player_id"] == wb.MY_PLAYER_ID else (
                pl.get("custom_name") or ai_player_code(pl["player_id"]))
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, pl["player_id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, code)
            item.setData(_GridRowDelegate._SPEC_ROLE, self._player_row_spec(pl, code))
            item.setData(_PLAYER_ROW_DATA_ROLE, pl)   # [2026-08 신설] 이름만 바뀌었을 때 이 줄만 다시 그리기 위함
            self.player_list.addItem(item)
        self._ensure_list_fits(self.player_list, self._player_split)

    def _apply_rename_to_player_list(self, player_id) -> bool:
        """[2026-08 신설, 최적화] AI 선수 이름을 바꿨을 때, 좌측 검색
        목록에서 그 선수 줄 하나만 새 이름으로 다시 그린다.

        예전엔 이름을 하나 바꿀 때마다 _refresh_player_list()로 최대
        300명을 DB에서 다시 검색하고 목록 위젯을 통째로 지웠다 다시
        만들었다 — 이름을 100명 넘게 바꾸면 그 비용이 그대로 100번
        반복된다(스크롤 위치와 선택도 매번 초기화됐다).

        단, "이름 때문에 목록에 뜨고 안 뜨고가 갈리는" 필터가 걸려
        있으면 이름을 바꾼 결과 그 선수가 목록에서 빠져야 할 수도
        있으므로 이 빠른 경로를 쓰지 않고 False를 돌려준다(호출부가
        예전처럼 전체 재조회로 폴백). 대상: 이름 검색어가 들어 있을
        때 / "✏ 이름 변경만" 필터가 켜져 있을 때.
        """
        try:
            if self.player_search_box.text().strip():
                return False
            if self.player_custom_named_btn.isChecked():
                return False
            lst = self.player_list
        except (AttributeError, RuntimeError):
            return False   # 선수 검색 탭이 아직 만들어지지 않았거나 창이 닫힌 경우
        for i in range(lst.count()):
            item = lst.item(i)
            if item.data(Qt.ItemDataRole.UserRole) != player_id:
                continue
            pl = item.data(_PLAYER_ROW_DATA_ROLE)
            if not pl:
                return False
            # 지정 이름을 지웠으면(빈 문자열 저장) 원래 식별코드로 되돌아간다 —
            # _refresh_player_list가 목록을 만들 때 쓰는 규칙과 완전히 동일.
            new_code = get_ai_player_custom_name(player_id) or ai_player_code(player_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, new_code)
            item.setData(_GridRowDelegate._SPEC_ROLE, self._player_row_spec(pl, new_code))
            item.setData(_PLAYER_ROW_DATA_ROLE, pl)
            return True
        # 지금 보이는 목록에 없는 선수(다른 필터로 걸러진 상태 등) —
        # 목록에 변화가 없으므로 다시 조회할 이유도 없다.
        return True

    def _player_row_spec(self, pl, code):
        # [2026-08 수정, 신민용 확정: "포메이션에 뜨는 것처럼 식별코드로
        # 떠야 한다"] ai_players.name(data/names.py 기반 실제 이름)은 더
        # 이상 표시하지 않고, 포메이션/이적 로그와 완전히 같은 규칙
        # (constants.ai_player_code)으로 만든 코드를 그대로 쓴다 — 같은
        # 선수는 화면이 달라도 항상 같은 코드로 보인다.
        nat_text = f"{pl.get('nat_flag') or ''} {pl.get('nationality') or ''}".strip()
        # [2026-08 신설, 신민용 요청: "은퇴한 선수도 검색할 수 있어야 해"]
        # 은퇴 선수는 소속팀/등급이 전부 None이라 "None급"/"None · None"처럼
        # 깨져 보이지 않게 별도로 처리 — 목록에서부터 "은퇴"로 바로 티나게.
        # [2026-08 수정, 신민용 요청: "등급 칸은 폭이 좁아서 기간(2006~2010
        # 같은)을 넣으면 잘려 보인다 — 등급엔 은퇴 나이를, 소속팀·리그
        # (폭이 넓은 칸) 쪽에 뛰었던 기간을 표시하는 게 맞다"] 첫 시도는
        # 등급 자리에 기간을 넣었는데 그 칸이 좁아 "2000..."처럼 잘려서
        # 오히려 안 보였다 — 짧은 값(나이)은 좁은 등급 칸에, 긴 값(기간)은
        # 넓은 소속팀 칸에 배치하도록 자리를 맞바꿨다. 등급 칸: 은퇴 당시
        # 나이(ai_players_retired.age). 소속팀 칸: wb._annotate_career_span
        # 이 채운 career_start_year~career_end_year(ai_player_ovr_history
        # 행 수 기준) + 은퇴 직전 팀 — 기록이 아예 없는 구세이브 선수는
        # "20XX년 은퇴"로 폴백.
        if pl.get("is_retired"):
            grade_text = f"{pl['age']}세" if pl.get("age") is not None else "은퇴"
            grade_color = "#888888"
            c_start, c_end = pl.get("career_start_year"), pl.get("career_end_year")
            if c_start and c_end:
                span_text = f"{c_start}~{c_end}" if c_start != c_end else f"{c_start}"
            else:
                span_text = f"{pl.get('retirement_year', '-')}년 은퇴"
            team_text = f"{span_text} · {pl.get('last_team_name') or '소속 정보 없음'}"
        else:
            grade_text = f"{pl['grade']}급" if pl.get("grade") else "-"
            grade_color = _GRADE_COLORS.get(pl.get("grade"), "#888888")
            team_text = (f"{pl['team_name']} · {pl['league_name']}({pl['tier']}부)"
                         if pl.get("team_id") else "소속팀 없음")
        # [2026-08 신설, 신민용 요청: "국대를 한 번이라도 뽑힌 선수들은
        # 은퇴든 현역이든 이름(식별코드)가 파란색으로 뜨게"] wb.search_
        # ai_players/search_retired_ai_players가 _annotate_natteam으로
        # 채워둔 has_natteam을 그대로 읽는다 — 필터(국가대표 버튼)와
        # 무관하게 항상 적용되는 표시.
        _name_color = "#4da6ff" if pl.get("has_natteam") else "#eee"
        return [
            {"text": code, "width": self._NAME_COL_W, "color": _name_color, "bold": True},
            {"text": pl.get("position") or "", "width": self._POS_COL_W,
             "color": "#aaddff", "size": 11, "bold": True, "align": Qt.AlignmentFlag.AlignCenter},
            {"text": nat_text, "width": self._NAT_COL_W, "color": "#aaddff"},
            # [2026-08 신설, 신민용 요청: "어려움 모드일 때는 세계 축구
            # 기록실에서 OVR 표시를 없애야 해 — 좌측에 뜨는 OVR"]
            {"text": "-" if is_hard_mode() else str(pl.get("ovr", "")), "width": self._OVR_COL_W,
             "color": "#ffcc00", "bold": True, "align": Qt.AlignmentFlag.AlignCenter},
            {"text": grade_text, "width": self._GRADE_COL_W, "color": grade_color,
             "size": 11, "bold": True, "align": Qt.AlignmentFlag.AlignCenter},
            {"text": team_text, "width": self._LEAGUE_COL_W, "color": "#888"},
        ]

    def _recent_player_label(self, stored):
        """[2026-08 신설] 최근 검색에 저장된 값 → 버튼에 쓸 표시 이름.
        "#123"처럼 id로 저장된 항목은 지금 지정된 이름(없으면 식별코드)
        으로 그때그때 풀어서 보여주므로, 이름을 바꾸면 버튼 글자도
        자동으로 따라 바뀐다. 예전 방식으로 저장된 문자열 항목
        (이 기능 이전에 쌓인 것)은 그대로 돌려줘서 기존 목록이
        갑자기 사라지지 않게 한다 — 그 항목들은 계속 쓰다 보면
        자연히 뒤로 밀려 없어진다."""
        if not isinstance(stored, str):
            return ""
        if stored.startswith("#") and stored[1:].isdigit():
            return get_ai_player_custom_name(int(stored[1:])) or ai_player_code(int(stored[1:]))
        return stored

    def _on_player_selected(self, item):
        pid = item.data(Qt.ItemDataRole.UserRole)
        code = item.data(Qt.ItemDataRole.UserRole + 1)
        if pid is None:
            return
        self._show_player_detail(pid)
        # [2026-08 수정] AI 선수는 표시 이름 대신 id를 저장한다(위
        # _recent_player_label 주석 참고). my_player는 이름 변경
        # 대상이 아니고 id도 음수(MY_PLAYER_ID=-1)라 예전처럼 이름
        # 문자열을 그대로 남긴다.
        _stored = f"#{pid}" if isinstance(pid, int) and pid >= 0 else None
        self._record_recent_selection("player", code or "", "_player_recent_row", stored=_stored)

    def _show_player_detail(self, player_id):
        d = wb.get_ai_player_detail(player_id)
        if not d:
            self.player_detail_placeholder.setText("← 왼쪽에서 선수를 선택하세요")
            self.player_detail_placeholder.show()
            self.player_detail_tbl.hide()
            self.player_retirement_note.hide()
            self.player_team_tbl.setRowCount(0)
            self.player_team_award_tbl.setRowCount(0)
            self.player_intl_tbl.setRowCount(0)
            self._player_detail_pid = None
            self.player_copy_btn.setEnabled(False)
            self._player_copy_name = None
            self._player_copy_d = None
            return
        # [2026-08 신설, 신민용 요청: "이름 헤더 클릭하면 이름 변경"]
        # 헤더 클릭 핸들러가 "지금 화면에 뜬 선수가 누구인지" 알아야
        # 하므로 저장해둔다. my_player는 원래 실명이 그대로 표시되고
        # 이름 변경 기능 대상이 아니므로 그대로 None 취급해 클릭해도
        # 아무 일도 안 일어나게 한다(아래 핸들러에서 분기).
        self._player_detail_pid = player_id if player_id != wb.MY_PLAYER_ID else None
        # [2026-08 신설, 신민용 요청: "AICD8C 이 식별코드로 뜨는 선수의
        # 이름을 내가 입력할 수 있게"] custom_name이 저장돼 있으면 그
        # 이름을, 없으면 기존처럼 ai_player_code(id)를 표시한다.
        code = d["name"] if player_id == wb.MY_PLAYER_ID else (d.get("custom_name") or ai_player_code(player_id))
        self._fill_player_detail_row(code, d)
        # [2026-08 신설] 복사 버튼이 재조회 없이 쓸 수 있도록 기본 정보를
        # 인스턴스에 저장해둔다 — 연도별 기록/국가대표 기록은 아래에서
        # 각각 _populate_player_team_box/_populate_player_intl_box가 채운다.
        self._player_copy_name = code
        self._player_copy_d = d
        self._player_copy_team_hist = None
        self._player_copy_rows = []

        # [2026-08 수정, 신민용 요청: "은퇴해도 이전 커리어는 남아야 한다"]
        # get_ai_player_detail이 이제 은퇴 선수도 team_id를 "마지막
        # 소속팀"으로 채워 반환하므로(위 world_browser.py 참고), 은퇴
        # 여부와 무관하게 team_id가 있으면 항상 _populate_player_team_box를
        # 그대로 태운다 — 은퇴 전 실제로 뛰었던 연도들의 기록이 그대로
        # 나온다. "은퇴했다"는 사실 자체는 그 표 바로 아래 별도 라벨로
        # 보강한다(표를 통째로 대체하지 않음).
        if d.get("team_id"):
            self._populate_player_team_box(player_id, d["team_id"], d["team_name"],
                                            retirement_year=d.get("retirement_year"),
                                            current_age=d.get("age"),
                                            final_ovr=d.get("ovr") if d.get("is_retired") else None,
                                            current_position=d.get("position"))
        else:
            self.player_team_tbl.clearSpans()
            self.player_team_award_tbl.clearContents()
            self.player_team_award_tbl.setRowCount(0)
            self._resize_self_sizing_table(self.player_team_award_tbl)
            self.player_team_tbl.setRowCount(1)
            empty = QTableWidgetItem("소속팀 없음")
            empty.setForeground(QColor("#666"))
            self.player_team_tbl.setItem(0, 0, empty)
            self.player_team_tbl.setSpan(0, 0, 1, 10)
            self._resize_self_sizing_table(self.player_team_tbl)

        if d.get("is_retired"):
            self.player_retirement_note.setText(
                f"🏳 은퇴했습니다 — {d.get('retirement_year', '-')}년 은퇴, "
                f"당시 {d.get('age', '-')}세"
                + (f" (마지막 소속: {d['last_team_name']})" if d.get("last_team_name") else ""))
            self.player_retirement_note.show()
        else:
            self.player_retirement_note.hide()

        # [2026-08 신설] 국가대표 출전 기록 박스 — 소속팀 유무·은퇴 여부와
        # 무관하게(국적은 은퇴해도 안 바뀜) 항상 채운다.
        self._populate_player_intl_box(player_id)
        # 기본 정보(이름/국적/나이/포지션/OVR/소속팀)만 있어도 복사할
        # 가치가 있으므로, 연도별·국가대표 기록 유무와 무관하게 여기서
        # 활성화한다(각 기록이 비어 있으면 포맷 함수가 "기록 없음"으로 채움).
        self.player_copy_btn.setEnabled(True)

    def _populate_player_intl_box(self, player_id):
        """[2026-08 신설, 신민용 요청: "'예선전 탈락' 같은 개인 기록도
        표시해줘"] wb.get_player_intl_records로 이 선수가 실제로 대회
        명단(intl_squad)에 뽑혔던 대회만 가져와 연도/대회/국가/출전/결과
        표로 채운다. player_team_tbl과 같은 톤 — 기록이 없으면(intl_squad
        도입 이전 대회뿐이거나, 애초에 대표팀에 뽑힌 적이 없으면) 안내
        문구 한 줄만 표시한다."""
        tbl = self.player_intl_tbl
        tbl.setRowCount(0)
        tbl.clearSpans()
        records = wb.get_player_intl_records(player_id)
        # [2026-08 신설] 복사 버튼용 — 화면과 같은 국가대표 기록 원본 저장.
        self._player_copy_intl_records = records
        if not records:
            tbl.setRowCount(1)
            empty = QTableWidgetItem("국가대표 출전 기록 없음")
            empty.setForeground(QColor("#666"))
            tbl.setItem(0, 0, empty)
            tbl.setSpan(0, 0, 1, 5)
            self._resize_self_sizing_table(tbl)
            return
        tbl.setRowCount(len(records))
        for row, rec in enumerate(records):
            # [2026-08 신설, 신민용 요청: "출전/전체 경기 형식으로 — 전체
            # 경기는 대회 규정 경기 수가 아니라 이 팀이 거기까지 가며
            # 실제로 치른 경기 수"] 예: 예선 조별 6경기 중 3경기 출전이면
            # "3/6", 본선에서 조별 3경기 + 16강 1경기까지 갔으면 분모가 4.
            _apps = rec.get("appearances", 0)
            _total = rec.get("total_games", 0)
            _apps_text = f"{_apps}/{_total}" if _total else str(_apps)
            cells = [str(rec["year"]), rec.get("name") or "?", rec.get("country") or "?",
                     _apps_text, rec.get("result") or "?"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (0, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                tbl.setItem(row, col, item)
        self._resize_self_sizing_table(tbl)

    def _on_player_detail_header_clicked(self, section):
        """[2026-08 신설, 신민용 요청: "'이름' 헤더를 클릭하면 이 선수의
        이름을 변경할 수 있는 창이 뜨게"] 0번 칸("이름")을 눌렀을 때만
        반응한다 — 나머지 칸(국적/나이/포지션/OVR/소속팀/소속팀 국가)은
        읽기 전용 그대로. my_player(사용자 본인)는 _show_player_detail에서
        self._player_detail_pid를 None으로 남겨두므로 여기서 자동으로
        무시된다(이름 변경은 AI 선수 전용 기능 — 본인 이름은 캐릭터
        생성 화면에서 이미 실명으로 정한 것이라 대상이 아님)."""
        if section != 0:
            return
        pid = getattr(self, "_player_detail_pid", None)
        if pid is None:
            return
        self._open_ai_rename_dialog(pid)

    def _open_ai_rename_dialog(self, player_id):
        """AI 선수 이름 변경 창. 현재 지정된 이름(없으면 빈칸 — placeholder에
        지금 표시 중인 식별코드를 보여줘서 "비워두면 이 코드로 돌아간다"는
        걸 알 수 있게 한다)을 입력칸에 채워서 띄운다. 저장을 누르면
        set_ai_player_custom_name으로 저장하고, 화면(상세 표 + 좌측
        검색 목록)을 즉시 다시 그려 새 이름이 바로 반영되게 한다."""
        current = get_ai_player_custom_name(player_id)
        code = ai_player_code(player_id)

        dlg = QDialog(self)
        dlg.setWindowTitle("선수 이름 변경")
        dlg.setStyleSheet("QDialog{background:#1e1e1e;color:#ccc;}")
        dlg.setMinimumWidth(300)
        v = QVBoxLayout(dlg)

        info_lbl = QLabel(f"식별코드: {code}\n이 선수에게 부를 이름을 지어주세요.")
        info_lbl.setStyleSheet("color:#888;font-size:11px;")
        v.addWidget(info_lbl)

        edit = QLineEdit(current)
        edit.setPlaceholderText(f"비워두면 다시 \"{code}\"로 표시됩니다")
        edit.setStyleSheet(
            "QLineEdit{background:#161616;color:#eee;font-size:13px;"
            "border:1px solid #333;border-radius:4px;padding:6px 8px;}"
            "QLineEdit:focus{border:1px solid #4da6ff;}")
        edit.selectAll()
        v.addWidget(edit)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("저장")
        save_btn.setStyleSheet(
            "background:#2d4a6b;color:#eee;border:1px solid #4a7ab0;"
            "border-radius:4px;padding:6px 14px;")
        cancel_btn = QPushButton("취소")
        cancel_btn.setStyleSheet(
            "background:#2a2a2a;color:#ccc;border:1px solid #444;"
            "border-radius:4px;padding:6px 14px;")
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        v.addLayout(btn_row)

        save_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        edit.returnPressed.connect(dlg.accept)

        _accepted = dlg.exec() == QDialog.DialogCode.Accepted
        _new_text = edit.text()
        # [2026-08 최적화/누수수정] QDialog는 부모(self=이 창)가 있으면
        # 파이썬 참조가 사라져도 C++ 객체가 부모에 매달린 채 계속
        # 살아남는다 — 이름을 100명 넘게 바꾸면 그만큼의 숨은
        # 다이얼로그(각각 입력칸·버튼 여러 개)가 이 창 밑에 쌓이고,
        # 앱 전체 위젯을 훑는 코드(apply_custom_name_live 등)가 그만큼
        # 계속 느려진다. 다 쓴 즉시 삭제 예약.
        dlg.deleteLater()
        if not _accepted:
            return
        set_ai_player_custom_name(player_id, _new_text)
        # [2026-08 버그수정, 신민용 리포트: "이름 수정했는데 포메이션에는
        # AIAXS2로 예전 코드가 그대로 뜬다" → 후속: "나갔다 들어와야
        # 바뀐다, 실시간으로 안 되나? 어차피 한 번에 하나씩만 바꾸는데"]
        # _ovr_cache_invalidated 플래그는 "다음에 이 팀이 다시 로드될
        # 때"만 적용되는 예약이라, 지금 이미 화면에 떠 있는 포메이션은
        # 그때까지(주 진행, 팀 재선택 등) 안 바뀐다 — 그래서 나갔다
        # 들어와야만 반영됐다. apply_custom_name_live가 지금 열려 있는
        # 모든 포메이션 화면(내 팀/상대팀 둘 다)을 뒤져 이 선수 id를
        # 찾아 그 자리에서 바로 이름을 바꾸고 다시 그린다 — 한 명만
        # 바꾸는 가벼운 작업이라 이 정도 즉시 패치로 충분하다.
        # _ovr_cache_invalidated는 그래도 안전장치로 같이 세워둔다(이후
        # 어떤 경로로든 캐시가 다시 로드될 때도 새 이름이 확실히 반영
        # 되도록).
        try:
            import ui.formation_widget as _fw
            _fw._ovr_cache_invalidated = True
            _new_display = get_ai_player_custom_name(player_id) or ai_player_code(player_id)
            _fw.apply_custom_name_live(player_id, _new_display)
        except Exception:
            pass
        # 저장 직후 상세 표를 새로 그려 새 이름을 바로 반영하고, 좌측
        # 검색 목록도 다시 조회해 목록에 뜬 이름도 같이 갱신한다.
        self._show_player_detail(player_id)
        # [2026-08 최적화] 목록 전체 재조회 대신 그 줄만 갱신(불가능한
        # 필터 상황이면 _apply_rename_to_player_list가 False를 돌려주고
        # 예전과 똑같이 전체 재조회로 폴백한다).
        if not self._apply_rename_to_player_list(player_id):
            self._refresh_player_list()
        # [2026-08 신설] 최근 검색 버튼도 새 이름으로 다시 그린다
        # (id로 저장돼 있으므로 다시 그리기만 하면 새 이름이 나온다).
        _rr = getattr(self, "_player_recent_row", None)
        if _rr is not None:
            _rr.refresh()

    def _fill_player_detail_row(self, name_text, d):
        """[2026-08 재수정, 신민용 요청: "왜 아직도 태그형으로 표시하는거?
        아래 연도별 기록처럼 그리드/테이블 형태로"] 이름/국적/나이/포지션/
        OVR/소속팀/소속팀 국가를 QTableWidget 1행에 채운다 — 이 탭의 다른
        표(소속팀 대회 기록 등)와 완전히 같은 방식, 셀 선택 후 Ctrl+C나
        우클릭 "복사"로 복사된다(_enable_plain_copy, 이 파일 상단 참고).
        소속팀 파워랭킹은 표시하지 않는다(신민용 요청으로 제외)."""
        tbl = self.player_detail_tbl
        nat_text = f"{d.get('nat_flag') or ''} {d.get('nationality') or ''}".strip() or "국적 미상"
        if d.get("is_retired"):
            # [2026-08 신설, 신민용 요청: "은퇴하면 소속팀에 '은퇴했습니다'
            # 라고 뜨고 이때 나이가 몇살인지 써줘"] — 이 상단 요약줄에서는
            # 여전히 "은퇴했습니다"로 보여준다(아래 큰 표는 실제 연도별
            # 기록을 그대로 보여주는 것과 별개로, 여기 요약칸엔 "지금
            # 상태"를 짧게 담는 게 맞다).
            team_text = f"은퇴했습니다 ({d.get('age', '-')}세, {d.get('retirement_year', '-')}년)"
            team_country_text = "-"
        elif d.get("team_id"):
            team_text = f"{d['team_name']} ({d['league_name']} {d['tier']}부)"
            team_country_text = f"{d.get('flag') or ''} {d.get('country') or ''}".strip()
        else:
            team_text = "소속팀 없음"
            team_country_text = "-"

        cells = [
            (name_text, "#00cc44", True),
            (nat_text, "#aaddff", False),
            (f"{d.get('age', '-')}세", "#cccccc", False),
            (d.get("position") or "-", "#aaddff", False),
            # [2026-08 신설, 신민용 요청: "어려움 모드일 때... 그 선수를
            # 클릭할 때 우측 위에 뜨는 OVR"도 없애야 해]
            ("OVR -" if is_hard_mode() else f"OVR {d.get('ovr', '-')}", "#ffcc00", True),
            (team_text, "#88ddaa", False),
            (team_country_text, "#aaddff", False),
        ]
        tbl.setRowCount(1)
        for col, (text, color, bold) in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(QColor(color))
            if bold:
                f = item.font(); f.setBold(True); item.setFont(f)
            # [2026-08 버그수정, 신민용 리포트: "소속팀 칸 복사하면 리그/
            # 부수까지 같이 복사된다 — 챔스칸처럼 팀명만 복사되게 해달라"]
            # 이 함수 docstring은 처음부터 _enable_plain_copy를 쓴다고
            # 적혀 있었지만, 정작 team_text 셀에 _CLEAN_TEXT_ROLE을 채우는
            # 코드가 빠져 있어서(다른 셀들처럼 원래 장식이 없는 텍스트라
            # 문제가 없었을 뿐) 실제로는 화면에 보이는 전체 문자열이
            # 그대로 복사되고 있었다 — 팀명만 별도로 채워준다.
            if col == 5:  # 소속팀 칸
                _clean = d.get("team_name") if d.get("team_id") and not d.get("is_retired") else None
                if _clean:
                    item.setData(_CLEAN_TEXT_ROLE, _clean)
            tbl.setItem(0, col, item)
        self._resize_self_sizing_table(tbl)
        self.player_detail_placeholder.hide()
        tbl.show()

    def _populate_player_team_box(self, player_id, tid, tname, retirement_year=None,
                                   current_age=None, final_ovr=None, current_position=None):
        """[2026-08 신설, 2026-08 수정: "순위가 아니라 이 선수가 그 해에
        실제로 속한 팀이 떠야 한다"] 팀 검색 탭 _show_team_detail의
        "연도별 기록" 렌더링(수상 요약 행 + 연도별 리그/국내컵/클럽대항전/
        슈퍼컵/클럽월드컵)을 "선수 검색" 탭 우측 박스에서도 재사용하되,
        예전엔 여기 있던 "순위"(팀 파워랭킹) 칸을 없애고 그 자리에
        "소속팀"(그 해에 이 선수가 실제로 있었던 팀, wb.get_ai_player_
        team_timeline 재구성)을 넣는다 — 팀 순위는 이 표의 다른 대회
        칸들과 성격이 달라(선수 개인과 무관한 팀 지표) 혼란을 줬었다.
        같은 헬퍼(_two_line_cell/_cl_award_summary_cell/BURGUNDY)를 쓰되
        대상 위젯만 self.player_team_tbl/self.player_team_award_tbl로
        바꾼 것 — 기존 _show_team_detail은 손대지 않는다(회귀 위험 최소화)."""
        tbl, award_tbl = self.player_team_tbl, self.player_team_award_tbl
        tbl.setRowCount(0)
        tbl.clearSpans()
        award_tbl.clearContents()
        # [2026-08 버그수정, 신민용 리포트: "리그 우승 5회로 보이는데
        # 통산 수상엔 18회로 뜬다 — 팀 검색 데이터를 그대로 쓰는거
        # 아니냐"] 정확한 지적이었다 — 예전엔 get_team_history(tid)로
        # "현재/마지막 소속팀의 전체 역사"를 그대로 이 선수 기록인 것처럼
        # 썼다. get_ai_player_career_history가 연도별 실제 소속팀
        # (timeline)마다 그 팀의 그 해 기록만 병합해서, 팀을 옮긴
        # 이력이 있으면 상반기/하반기가 아니라 "그 해에 실제로 있던
        # 팀"의 성적만 붙고, 통산 수상도 이 선수가 실제로 그 팀에
        # 있었던 연도만 재집계된다. current_age를 같이 넘겨서 출생 이전
        # 연도(이적기록 없는 선수의 무기한 소급 추정 구간이 출생보다
        # 앞서가며 나이가 음수로 뜨던 원인)도 잘라낸다.
        hist = (wb.get_my_player_career_history() if player_id == wb.MY_PLAYER_ID else
                wb.get_ai_player_career_history(player_id, tid, retirement_year=retirement_year,
                                                current_age=current_age))
        # [2026-08 신설] 복사 버튼(_format_player_history_text)이 화면과
        # 완전히 같은 값을 쓰도록, 아래에서 화면에 그리는 것과 같은 hist를
        # 그대로 저장해둔다.
        self._player_copy_team_hist = hist
        awards, years = hist["awards"], hist["years"]
        if not years and not any(awards.values()):
            award_tbl.setRowCount(0)
            self._resize_self_sizing_table(award_tbl)
            tbl.setRowCount(1)
            empty = QTableWidgetItem("기록 없음")
            empty.setForeground(QColor("#666"))
            tbl.setItem(0, 0, empty)
            tbl.setSpan(0, 0, 1, 10)
            self._resize_self_sizing_table(tbl)
            self._player_copy_rows = []
            return

        award_tbl.setRowCount(1)
        # [2026-08 확장] 포지션 컬럼이 소속팀(1)과 OVR(이제 3) 사이(2)에
        # 끼어들면서, 이 요약 행도 그 자리에 빈칸을 하나 더 넣어야
        # 아래 표와 컬럼이 어긋나지 않는다.
        # [2026-08 재확장] "역할"(4) 칸이 새로 끼어들면서 빈칸 하나 더 추가.
        award_labels = [
            ("수상", None),
            ("", None),
            ("", None),
            ("", None),
            ("", None),
            (str(awards["league"]) if awards["league"] else "", "#4da6ff"),
            (str(awards["cup"]) if awards["cup"] else "", "#c48aff"),
        ]
        for j, (text, color) in enumerate(award_labels):
            cell = QTableWidgetItem(text)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            f = cell.font(); f.setBold(True); cell.setFont(f)
            cell.setForeground(QColor(color) if color else QColor("#ffcc00"))
            cell.setBackground(QColor("#2a2a2a"))
            award_tbl.setItem(0, j, cell)
        award_tbl.setCellWidget(0, 7, self._cl_award_summary_cell(
            awards.get("cl_champions", 0), awards.get("el_champions", 0),
            awards.get("ecl_champions", 0)))
        sc_cell = QTableWidgetItem(str(awards.get("sc_champions", 0)) if awards.get("sc_champions") else "")
        sc_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        f = sc_cell.font(); f.setBold(True); sc_cell.setFont(f)
        sc_cell.setForeground(QColor(BURGUNDY))
        sc_cell.setBackground(QColor("#2a2a2a"))
        award_tbl.setItem(0, 8, sc_cell)
        cwc_cell = QTableWidgetItem(str(awards["cwc"]) if awards["cwc"] else "")
        cwc_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        f = cwc_cell.font(); f.setBold(True); cwc_cell.setFont(f)
        cwc_cell.setForeground(QColor("#4dd0e1"))
        cwc_cell.setBackground(QColor("#2a2a2a"))
        award_tbl.setItem(0, 9, cwc_cell)
        self._resize_self_sizing_table(award_tbl)

        timeline = wb.get_ai_player_team_timeline(player_id, tid)
        # [2026-08 신설, 신민용 요청: "년도별로 선수 OVR도 표시... 그때
        # 뛸 때 OVR을 표시해줘"] 매 시즌 OVR 아카이브가 없어서 "그 해의
        # 정확한 OVR"은 원칙적으로 모른다 — ai_transfer_log가 이적이
        # 일어난 그 순간의 OVR만 기록해두므로, 그 연도에 한해서만 실제
        # 값을 보여준다(없는 연도는 빈칸 — 부정확한 값을 정확한 척
        # 보여주지 않는다). player_id가 MY_PLAYER_ID/은퇴 아카이브면
        # 이적 로그가 없을 수 있어 빈 dict가 올 수 있음(정상).
        # [2026-08 수정, 신민용 요청: "챔스 클릭하면 네모 안에 글자가
        # 있고 네모를 클릭해서 복사하는 그 상자를 말한 거다"] 값이 있는
        # 연도는 플레인 텍스트가 아니라 _sized_copyable_field(테두리
        # [2026-08 수정, 신민용 요청: "OVR을 그리드로 맞춰서, 상자(패널)
        # 말고 다른 칸들처럼"] 예전엔 여기서 클릭 복사용 상자 위젯
        # (_sized_copyable_field)을 썼는데, 이제 아래 렌더링 루프에서
        # 일반 QTableWidgetItem으로 통일해서 이 import/스타일은 더 이상
        # 필요 없다.
        ovr_checkpoints = (wb.get_my_player_ovr_checkpoints() if player_id == wb.MY_PLAYER_ID
                           else wb.get_ai_player_ovr_checkpoints(player_id) if player_id > 0
                           else {})
        # [2026-08 신설, 신민용 요청: "이 시즌에 얘가 어디 포지션을
        # 갔는지가 중요한거야"] ai_player_position_history(시즌 전환마다
        # 그 시즌 포메이션 기준 실제 슬롯을 기록해둔 것) — 있으면 최우선,
        # 없는 연도(이 기능 신설 이전 과거 시즌)는 아래에서 기존처럼
        # 이적 시점 등록 포지션으로 대체한다.
        # [2026-08 수정, 신민용 리포트: "AI는 연도별 주전/로테이션/대기/
        # 유망주가 뜨는데 나(my_player)는 왜 안 뜨냐 — 나도 판수 대비
        # 출전 기록은 이미 저장되잖아"] 예전엔 my_player가 이 아카이브
        # 대상이 아니라며 항상 빈 dict를 썼는데, 이제 ai_lifecycle.
        # snapshot_my_player_position()이 매 시즌 my_player 전용
        # 아카이브(my_player_position_history)를 남기므로 그걸 읽는다.
        position_checkpoints = (wb.get_my_player_position_checkpoints() if player_id == wb.MY_PLAYER_ID
                                 else wb.get_ai_player_position_checkpoints(player_id) if player_id > 0
                                 else {})
        # [2026-08 신설, 신민용 요청: "그 해 주전/로테이션/대기/유망주였는지
        # 연도별로 표시 — 위(요약행)의 지금 스냅샷 하나가 아니라 여기
        # 연도별 기록에"] position_checkpoints와 완전히 같은 패턴(같은
        # 테이블·같은 신설 시점 제약)으로 role을 읽는다. my_player도
        # 위와 동일한 이유로 이제 전용 아카이브에서 읽는다.
        role_checkpoints = (wb.get_my_player_role_checkpoints() if player_id == wb.MY_PLAYER_ID
                             else wb.get_ai_player_role_checkpoints(player_id) if player_id > 0
                             else {})
        # [2026-08 신설, 신민용 요청: "그 당시 OVR 스탯이 떠야해"] 은퇴
        # 시점의 정확한 최종 OVR은 ai_players_retired.ovr에 그대로 남아
        #있다(추정이 아니라 실제 기록값) — 혹시라도 그 해 아카이브가
        # 비어있는 예외적인 경우(과거 세이브 이어하기 등) 이 값으로 채운다.
        if retirement_year and final_ovr:
            ovr_checkpoints[retirement_year] = final_ovr

        # [2026-08 신설, 신민용 요청: "연도 2001(이때 나이) 이렇게도
        # 뜨고"] 그 해 나이를 역산 — 은퇴 선수는 은퇴 연도/그때 나이를
        # 정확히 알고 있으니(ai_players_retired) 그 기준으로, 현역
        # 선수는 지금(현재 게임 연도)/현재 나이 기준으로 1년=1살 단순
        # 역산한다(이 게임 나이 증가 규칙과 일치 — 매 시즌 정확히 1살).
        age_ref_year = retirement_year if retirement_year else wb.get_current_game_year()

        def _age_at(year):
            if current_age is None or age_ref_year is None:
                return None
            return current_age - (age_ref_year - year)

        # [2026-08 신설, 신민용 요청: "2004년 소속팀 없음이 뜨면 2004년
        # 까지 기록되며 2005년부터는 아예 칸조차 없어야 해"] 은퇴 다음
        # 해(소속팀 없음을 알리는 딱 한 줄) 이후로는 그 팀 역사가 계속
        # 있어도 더 이상 이 선수와 무관하므로 행 자체를 아예 안 만든다
        # (예전엔 팀이 존재하는 모든 연도를 계속 나열해 은퇴 후 몇 년치
        # "소속팀 없음"이 줄줄이 나왔었다).
        if retirement_year:
            years = [y for y in years if y["year"] <= retirement_year + 1]

        def _team_name_for_year(year):
            # [2026-08 신설, 신민용 리포트: "2001년에 은퇴했으면 2002년
            # 부터는 소속팀 없음(은퇴)이어야 하는데 첼시로 계속 뜬다"]
            # get_ai_player_team_timeline의 마지막 구간은 end_year=None
            # (그 이후 전체)이라 은퇴 후 연도까지 마지막 소속팀으로
            # 그대로 잡혔다 — 은퇴 연도를 넘긴 해는 여기서 먼저 걸러
            # None(=은퇴 표시)을 반환한다.
            if retirement_year and year > retirement_year:
                return None
            for seg in timeline:
                if (seg["start_year"] is None or year >= seg["start_year"]) and \
                   (seg["end_year"] is None or year < seg["end_year"]):
                    return seg["team_name"]
            return tname

        # [2026-08 신설, 신민용 요청: "소속팀일 때 포지션이 뭐였는지도
        # 적어야 하는거 아니야 — 팀마다 포지션이 다르잖아, 위(상단 요약행)
        # 는 주포고 여기 아래는 세부 포지션"] _team_name_for_year와 완전히
        # 같은 구조 — timeline 세그먼트에 실려온 포지션(ai_transfer_log.
        # player_position, 이적 시점 스냅샷)을 그 해에 매칭한다. 세그먼트에
        # 값이 없으면(첫 이적 이전 소급 구간 등) 지금 알고 있는 최신
        # 포지션(current_position)으로 근사한다.
        def _position_for_year(year):
            if retirement_year and year > retirement_year:
                return None
            for seg in timeline:
                if (seg["start_year"] is None or year >= seg["start_year"]) and \
                   (seg["end_year"] is None or year < seg["end_year"]):
                    return seg.get("position")
            return current_position

        tbl.setRowCount(len(years))
        # [2026-08 신설] 복사 버튼용 — 화면에 그리는 것과 완전히 같은
        # 값(그 해 나이·소속팀·OVR·retired 여부·entry 원본)을 행마다
        # 그대로 쌓아둔다. 화면 렌더링 로직(나이 역산/소속팀 타임라인
        # 조회/은퇴 이후 절단 등)을 복사 텍스트용으로 다시 짜면 둘이
        # 미묘하게 어긋날 위험이 있어, 아예 같은 루프 안에서 같이 채운다.
        self._player_copy_rows = []
        for i, entry in enumerate(years):
            age = _age_at(entry["year"])
            year_text = f"{entry['year']} ({age}세)" if age is not None else str(entry["year"])
            year_item = QTableWidgetItem(year_text)
            year_item.setForeground(QColor("#ffcc00"))
            f = year_item.font(); f.setBold(True); year_item.setFont(f)
            tbl.setItem(i, 0, year_item)

            # [2026-08 수정] 예전엔 여기가 팀 파워랭킹(전체/대륙 순위)
            # 두 줄이었는데, "선수가 그 해에 실제로 속한 팀"으로 교체.
            # [2026-08 확장, 상반기/하반기 이적 기록 분리 기능] entry가
            # _half_season_league_entry가 만든 "상반기 스냅샷" 줄이면
            # _team_name_for_year(그 연도의 최종/하반기 소속팀을 돌려줌)
            # 대신 entry 자신에 적힌 원래(상반기) 팀 이름을 그대로 쓴다 —
            # 같은 연도 두 줄이 서로 다른 팀임을 정확히 구분해야 하므로.
            if entry.get("_is_half"):
                player_team_name = entry.get("_half_team_name")
                player_position = entry.get("_half_position") or current_position
            elif entry.get("_main_team_name") is not None:
                # [2026-08 신설] get_my_player_career_history가 만든
                # entry — 자기 자신이 이미 그 해의 정확한 소속팀을
                # 알고 있으므로(_team_name_for_year는 ai_transfer_log
                # 기반이라 my_player에는 안 맞음) 그대로 쓴다.
                player_team_name = entry.get("_main_team_name")
                player_position = entry.get("_main_position") or current_position
            else:
                player_team_name = _team_name_for_year(entry["year"])
                # [2026-08 신설] 시즌별 실제 포메이션 슬롯 아카이브가 있으면
                # 그걸 최우선으로 — 이적 시점 등록 포지션(_position_for_year)
                # 보다 정확하다(예: 등록은 CB인데 그 시즌 스쿼드 사정상
                # LB를 봤을 경우). 아카이브가 없는 과거 연도만 기존 방식으로.
                player_position = (position_checkpoints.get(entry["year"])
                                   or _position_for_year(entry["year"]) or current_position)
            is_retired_row = player_team_name is None
            if is_retired_row:
                team_cell = self._col_label(
                    "소속팀 없음 (은퇴)", self._LEAGUE_COL_W, color="#666", bold=False)
            else:
                is_current = (player_team_name == tname)
                team_cell = self._col_label(
                    player_team_name, self._LEAGUE_COL_W,
                    color="#88ddaa" if is_current else "#aaddff", bold=is_current)
            tbl.setCellWidget(i, 1, team_cell)

            # [2026-08 신설, 신민용 요청: "소속팀일 때 포지션이 뭐였는지도"]
            # 소속팀(1)과 OVR(3) 사이(2)에 세부 포지션. 은퇴 이후 행은
            # 바로 아래에서 전부 "-"로 덮어쓰므로 여기선 신경 안 써도 된다.
            if not is_retired_row:
                pos_item = QTableWidgetItem(player_position or "-")
                pos_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                pos_item.setForeground(QColor("#aaddff") if player_position else QColor("#555"))
                tbl.setItem(i, 2, pos_item)

            # [2026-08 신설, 신민용 요청: "소속팀 없음이 뜬 그 줄은 리그든
            # 국내컵이든 다 -로 떠야해"] 그 팀의 실제 대회 결과(entry)는
            # 이 선수와 무관해진 뒤의 데이터이므로, 이 행에서만 전부 "-"로
            # 강제하고 그 해 팀 실제 기록은 참조하지 않는다.
            if is_retired_row:
                tbl.setItem(i, 2, self._dim_dash_item())
                tbl.setItem(i, 3, self._dim_dash_item())
                tbl.setItem(i, 4, self._dim_dash_item())
                for col in (5, 6, 7, 8, 9):
                    tbl.setCellWidget(i, col, self._two_line_cell("-", "#555", None))
                self._player_copy_rows.append({
                    "year": entry["year"], "age": age, "is_retired_row": True,
                    "team_name": None, "position": None, "ovr": None, "role": None, "entry": None})
                continue

            # [2026-08 수정, 신민용 요청: "OVR 수치가 패널로 묶여 있는데
            # 이거 다른 UI처럼 그리드로 맞춰서 만들어야 해"] 예전엔
            # _sized_copyable_field(테두리 있는 상자)를 QHBoxLayout으로
            # 가운데 정렬해 넣는 별도 위젯이었다 — "연도"/"소속팀" 등
            # 이 표의 나머지 칸과 다르게 튀어 보였다. 다른 칸과 똑같이
            # 일반 QTableWidgetItem으로 통일한다.
            # [2026-08 확장, "OVR을 1년 단위로 다 저장해줘"] ovr_checkpoints
            # 가 이제 ai_player_ovr_history(매년 아카이브)에서 오므로,
            # 이적이 없었던 해도 정확한 값이 채워진다.
            # [2026-08 신설, 신민용 요청: "어려움 모드일 때... 소속팀
            # 대회 기록에 뜨는 OVR"도 없애야 해] 어려움이면 항상 "-".
            ovr_at_year = None if is_hard_mode() else ovr_checkpoints.get(entry["year"])
            if ovr_at_year:
                ovr_item = QTableWidgetItem(str(ovr_at_year))
                ovr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                ovr_item.setForeground(QColor("#ffcc00"))
                tbl.setItem(i, 3, ovr_item)
            else:
                tbl.setItem(i, 3, self._dim_dash_item())

            # [2026-08 신설, 신민용 요청: "그 해 주전/로테이션/대기/유망주
            # 였는지"] 이 기능 신설 이전 시즌은 role_checkpoints에 값이
            # 없으므로(빈 dict) "-"로 대체 — 다른 이 기능 이전 과거 데이터
            # (position_checkpoints 등)와 동일한 폴백 방식.
            # [2026-08 버그수정, 신민용 리포트: "중간 이적한 해 상반기
            # 팀엔 역할이 안 뜬다 — 뜨더라도 하반기 팀이랑 완전히 똑같이
            # 뜨는데 실제로는 상반기 팀에서 후보였다"] role_checkpoints는
            # (player_id, year) 키라 그 해 "최종/하반기" 소속팀 스냅샷
            # 하나뿐이다 — 위 player_position과 같은 이유로, 상반기 줄
            # (entry["_is_half"])은 그 값 대신 entry["_half_role"](이적
            # 나가기 직전 상반기 팀 로스터 기준으로 이미 계산해둔 값 —
            # world_browser.get_ai_player_career_history/_half_season_
            # league_entry 참고)을 써야 한다.
            _ROLE_COLORS = {"주전": "#4da6ff", "로테이션": "#88ddaa",
                            "대기": "#cccc66", "유망주": "#cc88ff"}
            role_at_year = (entry.get("_half_role") if entry.get("_is_half")
                             else role_checkpoints.get(entry["year"]))
            if role_at_year:
                role_item = QTableWidgetItem(role_at_year)
                role_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                role_item.setForeground(QColor(_ROLE_COLORS.get(role_at_year, "#ccc")))
                f = role_item.font(); f.setBold(True); role_item.setFont(f)
                tbl.setItem(i, 4, role_item)
            else:
                tbl.setItem(i, 4, self._dim_dash_item())

            self._player_copy_rows.append({
                "year": entry["year"], "age": age, "is_retired_row": False,
                "team_name": player_team_name, "position": player_position,
                "ovr": ovr_at_year, "role": role_at_year, "entry": entry})

            lg_txt = entry["league"] or "-"
            if "승격" in lg_txt:
                lg_color = "#4da6ff"
            elif "강등" in lg_txt:
                lg_color = "#ff5555"
            elif entry.get("league_champion"):
                lg_color = "#ffd700"
            else:
                lg_color = "#ddd"
            tbl.setCellWidget(i, 5, self._two_line_cell(lg_txt, lg_color, entry.get("league_record")))

            cup_txt = entry["cup"] or "-"
            cup_color = "#c48aff" if entry["cup"] else "#555"
            tbl.setCellWidget(i, 6, self._two_line_cell(cup_txt, cup_color, entry.get("cup_record")))

            cl_txt = entry["cl"] or "-"
            _CL_KIND_COLOR = {"champions": "#1E4DB7", "europa": "#F28C28", "conference": "#20A464"}
            cl_color = _CL_KIND_COLOR.get(entry.get("cl_kind"), "#555") if entry["cl"] else "#555"
            tbl.setCellWidget(i, 7, self._two_line_cell(cl_txt, cl_color, entry.get("cl_record")))

            sc_txt = entry.get("sc") or "-"
            sc_color = BURGUNDY if entry.get("sc") else "#555"
            tbl.setCellWidget(i, 8, self._two_line_cell(sc_txt, sc_color, entry.get("sc_record")))

            cwc_txt = entry.get("cwc") or "-"
            cwc_color = "#4dd0e1" if entry.get("cwc") else "#555"
            tbl.setCellWidget(i, 9, self._two_line_cell(cwc_txt, cwc_color, entry.get("cwc_record")))
        self._resize_self_sizing_table(tbl)

    def _dim_dash_item(self):
        item = QTableWidgetItem("-")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor("#555"))
        return item

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
            self._on_country_selected,
            refresh_fn=self._refresh_country_search_list, debounce_timer=self._country_search_debounce)
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

        # [2026-08 신설, 신민용 요청: "대한민국 국제대회 기록 — 우승 대회는
        # 연도까지 같이(아시안컵 5회 [1956, 1960, ...]), 그 아래엔 우승
        # 못한 대회 중 최고 성적(월드컵 4강 2회 [2002, 2018])을 따로
        # 보여달라"] 우승 칩 줄 바로 아래, 별도의 두 번째 줄로 놓는다.
        self.country_best_others_label = QLabel("우승하지 못한 대회 중 최고 성적")
        self.country_best_others_label.setStyleSheet("color:#888;font-size:11px;")
        self.country_best_others_label.setVisible(False)
        right_lay.addWidget(self.country_best_others_label)
        self.country_best_others_row = QHBoxLayout()
        self.country_best_others_row.setSpacing(14)
        best_others_wrap = QWidget()
        best_others_wrap.setLayout(self.country_best_others_row)
        right_lay.addWidget(best_others_wrap)

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

        # [2026-08 신설, 신민용 요청] 연도와 대회 사이에 '순위'(그 해
        # 파워랭킹) 컬럼 추가 — 같은 연도에 기록이 여러 줄 있어도(예:
        # 2000년에 월드컵+지역컵 둘 다) 전부 같은 2000년 순위가 표시된다.
        self.country_detail_tbl = QTableWidget(0, 6)
        self.country_detail_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.country_detail_tbl.verticalHeader().setVisible(False)
        self.country_detail_tbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.country_detail_tbl.setHorizontalHeaderLabels(["연도", "순위", "대회", "종류", "결과", "상세기록"])
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents)
        self.country_detail_tbl.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents)
        # [2026-08 신설, 신민용 요청: "연도를 클릭하면 그 대회의 실제 경기
        # 기록(조 순위표+라운드별 상대·스코어)이 바로 아래에 펼쳐지게
        # 해달라"] 기존엔 더블클릭으로 별도 팝업(TournamentDetailDialog,
        # 전체 참가국 기준)을 열었는데, 이 표는 특정 국가 하나를 보는
        # 화면이라 그 국가 관점의 경기 로그를 표 안에서 바로 펼쳐 보여주는
        # 쪽이 요청에 맞다 — 팝업 대신 인라인 확장으로 교체.
        self.country_detail_tbl.cellClicked.connect(self._on_country_detail_cell_clicked)
        right_lay.addWidget(self.country_detail_tbl, 1)
        hint = QLabel("💡 연도를 클릭하면 경기 기록이, 대회명을 클릭하면 그 당시 주전/후보 명단이 펼쳐지고, 종류를 클릭하면 대회 전체 일정을 볼 수 있어요")
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

        # [2026-08 버그수정, 신민용 리포트: "수상=상 없음 + 대회=월드컵을
        # 고르면 그건 필터가 안 된다"] 예전엔 "수상"과 "대회" 두 필터를
        # 완전히 독립적인 조건으로 순서대로 AND 걸었다 — "상 없음"이
        # "어떤 대회든 우승 0회"를 뜻했는데, 그 뒤에 "대회=월드컵"이
        # "월드컵 우승 있음"을 또 요구하니 항상 모순(우승 0회이면서
        # 월드컵은 우승했다는 건 불가능)이라 결과가 늘 텅 비었다.
        #
        # 올바른 규칙(신민용 확정): "대회" 필터가 특정 대회로 좁혀지면,
        # "수상" 필터의 기준도 "그 대회 하나"로 같이 좁혀져야 한다.
        #   수상=상 없음 + 대회=월드컵 → 월드컵 우승은 없지만 다른 대회
        #                                우승은 있을 수 있는 국가
        #   수상=상 있음 + 대회=월드컵 → 월드컵 우승 경험 국가(기존 그대로)
        #   수상=상 없음 + 대회=전체   → 어떤 국제대회도 우승한 적 없는
        #                                국가(기존 그대로)
        if kind_filter_label != _ALL:
            _ek_by_label = dict(wb.COUNTRY_TROPHY_KIND_OPTIONS)
            want_ek = _ek_by_label.get(kind_filter_label)
            has_kind = lambda name: any(
                g["effective_kind"] == want_ek for g in (trophy_counts.get(name) or []))
            if trophy_filter == "상 없는 국가":
                countries = [cn for cn in countries if not has_kind(cn["name"])]
            else:
                # 수상 필터가 "전체"거나 "상 있는 국가"면 둘 다 "그 대회
                # 우승 있음"으로 수렴 — "상 있는 국가"만 따로 처리할
                # 필요가 없다(대회 필터 자체가 이미 그 조건이므로).
                countries = [cn for cn in countries if has_kind(cn["name"])]
        elif trophy_filter != _ALL:
            # [2026-08 신설] 수상 유무 필터(대회="전체"일 때만 적용) —
            # trophy_counts에 그 국가 항목 자체가 없거나 있어도 전부
            # 0회면 "상 없는 국가"로 취급.
            has_any = lambda name: bool(trophy_counts.get(name))
            want_has_trophy = (trophy_filter == "상 있는 국가")
            countries = [cn for cn in countries if has_any(cn["name"]) == want_has_trophy]

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
        # [2026-08 수정, 신민용 요청: "대한민국 국제대회 우승 기록 →
        # 대한민국 국제대회 기록으로 — 이제 우승 못한 대회 최고 성적도
        # 같이 보여주니까 '우승'만으로 한정하는 제목은 안 맞다"]
        self.country_detail_title.setText(f"🌍 {name}  국제대회 기록")
        self._country_copy_name = name
        # [2026-08 신설] 최근 검색 기록 — "국가 검색" 목록에서 실제로
        # 클릭해 들어간 국가명만 남긴다.
        self._record_recent_selection("country", name, "_country_recent_row")

        # 요약 칩 갱신 — [2026-08 확장] 우승 연도 목록도 같이 보여준다.
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
                years_txt = f" [{', '.join(str(y) for y in s['years'])}]" if s.get("years") else ""
                chip = QLabel(f"{s['label']} {s['titles']}회{years_txt}")
                chip.setStyleSheet(
                    "background:#2a2a2a;color:#ffcc00;font-size:12px;font-weight:bold;"
                    "padding:4px 10px;border-radius:8px;")
                self.country_summary_row.addWidget(chip)
        self.country_summary_row.addStretch(1)

        # [2026-08 신설] "우승하지 못한 대회 중 최고 성적" 줄 — 우승
        # 경험이 아예 없는 대회(name 기준)만 대상으로, 그 대회에서 낸
        # 역대 최고 성적(rank 최대값) 하나를 몇 번 냈는지·어느 연도인지
        # 같이 보여준다. 규칙: "가장 최근"이 아니라 "가장 좋은 등급"
        # 기준(get_country_best_non_winning_results 참고).
        while self.country_best_others_row.count():
            child = self.country_best_others_row.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        best_others = wb.get_country_best_non_winning_results(name)
        self._country_copy_best_others = best_others
        self.country_best_others_label.setVisible(bool(best_others))
        for b in best_others:
            years_txt = f" [{', '.join(str(y) for y in b['years'])}]" if b.get("years") else ""
            chip = QLabel(f"{b['label']} {b['result']} {b['count']}회{years_txt}")
            chip.setStyleSheet(
                "background:#232323;color:#aaddff;font-size:12px;font-weight:bold;"
                "padding:4px 10px;border-radius:8px;")
            self.country_best_others_row.addWidget(chip)
        self.country_best_others_row.addStretch(1)

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
        # [2026-08 신설] 필터가 바뀌거나 다른 국가를 선택하면 표를 통째로
        # 다시 채우므로, 펼쳐져 있던 경기 기록 행이 있으면 먼저 접어서
        # (span/cellWidget이 낡은 행 인덱스를 참조한 채 남아있지 않도록)
        # 상태를 깨끗하게 정리한 뒤 다시 그린다.
        self._collapse_country_detail_row()
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
        # [2026-08 신설, 신민용 요청] 연도별 파워랭킹 순위를 한 번만
        # 조회해서 캐시해둔다 — 같은 연도 기록이 여러 줄이어도 전부 같은
        # 값을 참조(신민용: "같은 연도면 같은 순위가 표시되는거고").
        year_rank = {y: r for y, r in pr.get_country_power_history(get_conn(), self._country_copy_name)}
        self._country_copy_year_rank = year_rank
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

                rank = year_rank.get(t["year"])
                rank_item = QTableWidgetItem(f"{rank}위" if rank else "-")
                rank_item.setForeground(QColor("#88ddaa"))
                tbl.setItem(i, 1, rank_item)

                name_item = QTableWidgetItem(str(t["name"]) if t["name"] else "-")
                tbl.setItem(i, 2, name_item)

                kind_label = INTL_TOURNAMENT_KIND_LABELS.get(
                    t.get("effective_kind", t["kind"]), t["kind"])
                kind_item = QTableWidgetItem(str(kind_label) if kind_label else "-")
                tbl.setItem(i, 3, kind_item)

                result_item = QTableWidgetItem(str(t["result"]) if t["result"] else "-")
                result_item.setForeground(QColor(_TIER_COLORS.get(t["tier"], "#999999")))
                rf = result_item.font()
                rf.setBold(t["tier"] >= 3)
                result_item.setFont(rf)
                tbl.setItem(i, 4, result_item)

                record_item = QTableWidgetItem(str(t.get("record") or "-"))
                record_item.setForeground(QColor("#aaaaaa"))
                tbl.setItem(i, 5, record_item)
            except Exception as e:
                print(f"[국제대회기록] {t.get('year')}년 행 렌더링 오류(건너뜀): {e}")
        self._show_empty_state(tbl, results, "참가 기록 없음", 6)
        tbl.resizeRowsToContents()

    # [2026-08 신설, 신민용 요청: "국가 검색에도 팀 검색처럼 복사 버튼을
    # 만들어달라"] _on_copy_team_history_clicked/_format_team_history_text와
    # 완전히 같은 목적·패턴 — 다만 대상이 팀의 시즌별 성적이 아니라 국가의
    # 국제대회(월드컵/대륙컵/지역컵 등) 우승·참가 기록이라는 점만 다르다.
    def _on_copy_country_history_clicked(self):
        name = getattr(self, "_country_copy_name", None)
        results = getattr(self, "_country_copy_results", None)
        summary = getattr(self, "_country_copy_summary", None)
        best_others = getattr(self, "_country_copy_best_others", None)
        if not name or (not results and not summary):
            return
        year_rank = getattr(self, "_country_copy_year_rank", {})
        text = self._format_country_history_text(name, summary, results, best_others, year_rank)
        QGuiApplication.clipboard().setText(text)

        self.country_copy_btn.setText("✅ 복사됨")
        QTimer.singleShot(1200, lambda: self.country_copy_btn.setText("📋 기록 복사"))

    def _format_country_history_text(self, name, summary, results, best_others=None, year_rank=None):
        """국가 상세 화면(요약 칩 + 우승 못한 대회 최고 성적 + 연도별
        결과표)과 같은 데이터를 LLM에 그대로 붙여넣어도 되는 평문으로
        직렬화한다. 팀 쪽(_format_team_history_text)과 같은 구조 — 맨 위에
        대회 종류별 우승 횟수 요약(연도 포함), 그 아래 우승 못한 대회
        중 최고 성적, 그 아래 연도 내림차순 전체 목록.
        [2026-08 신설, 신민용 리포트: "기록 복사에 순위가 안 뜬다"] 화면
        표(country_detail_tbl)에 이미 있는 순위 컬럼을 복사 텍스트에도
        똑같이 넣는다 — year_rank는 {ranking_year: 순위} 형태."""
        from constants import INTL_TOURNAMENT_KIND_LABELS
        year_rank = year_rank or {}
        lines = [f"[{name} 국제대회 기록]"]

        if summary:
            bits = []
            for s in summary:
                years_txt = f" [{', '.join(str(y) for y in s['years'])}]" if s.get("years") else ""
                bits.append(f"{s['label']} {s['titles']}회{years_txt}")
            lines.append("통산 우승: " + " · ".join(bits))
        else:
            lines.append("통산 우승: 없음")

        if best_others:
            bits = []
            for b in best_others:
                years_txt = f" [{', '.join(str(y) for y in b['years'])}]" if b.get("years") else ""
                bits.append(f"{b['label']} {b['result']} {b['count']}회{years_txt}")
            lines.append("우승하지 못한 대회 중 최고 성적: " + " · ".join(bits))
        lines.append("")

        if not results:
            lines.append("(참가 기록 없음)")
        else:
            # [2026-08 신설, 신민용 요청: "기록복사를 하면 (연도를 펼쳐보지
            # 않았어도) 조 순위표·라운드별 상대·스코어까지 포함되게 해달라"]
            # 화면에서 펼쳐야 보이는 _build_country_year_detail_widget과
            # 같은 데이터(get_country_intl_match_log)를 모든 기록에 대해
            # 무조건 조회해서, 복사 텍스트에는 펼침 여부와 무관하게 항상
            # 전부 들어가게 한다.
            for t in results:
                try:
                    tname_ = t.get("name") or "-"
                    kind_label = INTL_TOURNAMENT_KIND_LABELS.get(
                        t.get("effective_kind", t.get("kind")), t.get("kind") or "-")
                    result = t.get("result") or "-"
                    rec = t.get("record")
                    rec_txt = f" ({rec})" if rec else ""
                    rank = year_rank.get(t.get("year"))
                    rank_txt = f" | 순위: {rank}위" if rank else ""
                    lines.append(f"{t.get('year')}년{rank_txt} | {tname_} [{kind_label}] : {result}{rec_txt}")

                    tid = t.get("id")
                    if tid is None:
                        continue
                    log = wb.get_country_intl_match_log(tid, name)
                    gs = log.get("group_standings")
                    if gs and gs.get("rows"):
                        lines.append(f"  ㄴ {gs['group']}조 순위표:")
                        for rank_i, gr in enumerate(gs["rows"]):
                            mark = " ★" if gr["country"] == name else ""
                            lines.append(
                                f"     {rank_i+1}. {gr['country']} "
                                f"{gr['wins']}승{gr['draws']}무{gr['losses']}패 "
                                f"(득실 {gr['gd']:+d}, 승점 {gr['pts']}){mark}")
                    for stage in (log.get("stages") or []):
                        for m in stage["matches"]:
                            score_txt = f"{m['my_score']}:{m['opp_score']}"
                            if m.get("pso"):
                                pso_score = m.get("pso_score") or ""
                                score_txt += f"(승부차기 {pso_score})" if pso_score else "(승부차기)"
                            lines.append(
                                f"  ㄴ {stage['stage_ko']} vs {m['opponent']} "
                                f"{m['result']} {score_txt}")
                except Exception:
                    continue

        return "\n".join(lines)

    def _open_country_title_detail(self, row, _col):
        item = self.country_detail_tbl.item(row, 0)
        tid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tid is None:
            return
        wc = item.data(Qt.ItemDataRole.UserRole + 1) == "world"
        # [2026-08 신설, 신민용 요청: "국가 검색으로 들어와서 대회명을
        # 클릭해 전체 팝업을 열면, 그 안에서도 지금 보고 있는 국가 이름이
        # 금색으로 표시돼야 한다"] 지금 국가 검색에서 선택된 국가
        # (_country_copy_name)를 넘겨서 팝업의 조 순위표에도 하이라이트를
        # 적용한다 — 월드컵/네이션스컵 탭 등 다른 진입 경로에서는 이
        # 파라미터를 안 넘기므로(highlight_country=None) 기존처럼 아무
        # 하이라이트 없이 그대로 뜬다.
        country = getattr(self, "_country_copy_name", None)
        self._open_intl_detail(self.country_detail_tbl, row, wc=wc,
                                highlight_country=country)

    # [2026-08 신설, 신민용 요청] 국가 검색 "연도" 칸 클릭 → 그 대회의
    # 실제 경기 기록(조 순위표 + 라운드별 상대·스코어)을 표 안에 바로 아래
    # 행으로 펼쳐 보여준다. 이미 펼쳐진 연도를 다시 클릭하면 접힌다.
    def _collapse_country_detail_row(self):
        exp = getattr(self, "_country_expanded", None)
        if not exp:
            return
        tbl = self.country_detail_tbl
        detail_row = exp.get("detail_row")
        if detail_row is not None and 0 <= detail_row < tbl.rowCount():
            tbl.removeRow(detail_row)
        self._country_expanded = None

    def _on_country_detail_cell_clicked(self, row, col):
        # [2026-08 신설, 신민용 요청: "대회명을 클릭하면 그 대회 전체 일정
        # (예전에 보여주던 팝업)이 떠야 한다"] 연도(0번)는 이 국가 관점의
        # 인라인 경기기록 토글, 대회 전체 팝업(전 참가국 조편성+토너먼트
        # 대진)은 별도 컬럼으로 분리한다.
        # [2026-08 재수정, 신민용 요청: "대회명 말고 옆의 종류(대륙컵 등)를
        # 눌러야 그 대회 전체 일정이 뜨게 해달라"] 대회명(2번) 칸은 이제
        # 그냥 텍스트일 뿐이고, 종류(3번) 칸을 눌러야 팝업이 열린다.
        if col == 3:
            self._open_country_title_detail(row, col)
            return
        # [2026-08 신설, 신민용 요청: "대회를 클릭하면 그 대회 당시 주전들과
        # 후보들이 떠야 한다"] 연도(0번)는 경기 기록, 대회명(2번)은 스쿼드
        # (주전/후보) — 같은 "표 안에 한 줄 펼치기" 자리를 공유하되 종류가
        # 다르면 서로 다른 내용을 그린다(_country_expanded의 "kind"로 구분).
        # 한 번에 하나만 펼치는 규칙은 그대로 유지 — 다른 걸 클릭하면 먼저
        # 펼쳐진 걸 접는다.
        if col not in (0, 2):
            return
        kind = "match" if col == 0 else "squad"
        tbl = self.country_detail_tbl
        item = tbl.item(row, 0)
        if not item:
            return
        tid = item.data(Qt.ItemDataRole.UserRole)
        if tid is None:
            return
        exp = getattr(self, "_country_expanded", None)
        same = bool(exp) and exp.get("tid") == tid and exp.get("kind") == kind
        # 이미 다른 게 펼쳐져 있으면(연도든 대회든) 먼저 접는다(한 번에
        # 하나만 펼침) — 접으면 그 아래 행들이 위로 당겨져 인덱스가 바뀔
        # 수 있으므로, 이후 목표 행은 tid로 다시 찾아낸다(아래 참고).
        self._collapse_country_detail_row()
        if same:
            return

        target_row = None
        for r in range(tbl.rowCount()):
            it = tbl.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == tid:
                target_row = r
                break
        if target_row is None:
            return

        country = getattr(self, "_country_copy_name", None)
        if not country:
            return
        name_item = tbl.item(target_row, 2)
        kind_item = tbl.item(target_row, 3)
        year_item = tbl.item(target_row, 0)
        year_txt = year_item.text() if year_item else ""
        header = (f"{year_txt} {name_item.text() if name_item else ''} "
                  f"({kind_item.text() if kind_item else ''})").strip()
        widget = (self._build_country_year_detail_widget(tid, country, header) if kind == "match"
                  else self._build_country_squad_detail_widget(tid, country, header, year_txt))

        detail_row = target_row + 1
        tbl.insertRow(detail_row)
        tbl.setSpan(detail_row, 0, 1, 6)
        tbl.setCellWidget(detail_row, 0, widget)
        tbl.resizeRowToContents(detail_row)
        # [방어코드] resizeRowToContents가 cellWidget의 실제 sizeHint를
        # 못 따라가는 경우가 있어(특히 위젯 안에 표가 여러 개일 때) 한 번
        # 더 위젯 자체 sizeHint로 보정한다.
        h = widget.sizeHint().height()
        if h > tbl.rowHeight(detail_row):
            tbl.setRowHeight(detail_row, h + 8)
        self._country_expanded = {"tid": tid, "orig_row": target_row,
                                   "detail_row": detail_row, "kind": kind}
        if year_item:
            tbl.scrollToItem(year_item)

    def _build_country_year_detail_widget(self, tid, country, header_title):
        """국가 검색 표에서 연도를 펼쳤을 때 보여줄 내용 — 이 국가가 속한
        조의 순위표(있으면) + 라운드별(조별리그 포함) 상대·스코어·승패.
        world_browser.get_country_intl_match_log()가 계산해준 데이터를
        그대로 그린다. [2026-08 신설]
        [2026-08 재수정, 신민용 리포트: "전체적으로 UI가 잘 안 보인다" +
        "조 순위표에서 진출/탈락 표시(반투명)가 안 보인다, 이 국가 이름
        칸만 금색으로 하고 나머지 칸은 원래대로(진출=흰색굵게/탈락=회색
        반투명) 둬야 겹치지 않는다"] 조 순위표를 _build_groups_grid와 같은
        진출/탈락 배색(흰색 굵게 vs 회색)으로 되돌리고, "이 국가"만 이름
        칸에 한정해서 금색으로 덧칠한다(다른 칸은 그대로 진출/탈락 색을
        따름) — 숫자 칸까지 전부 금색으로 칠하면 탈락 표시(회색)가 묻혀서
        구분이 안 됐던 문제. 카드 자체도 좌측 초록 강조선+더 밝은 배경으로
        표 사이에서 잘 눈에 띄게, 경기 한 줄 한 줄도 옅은 배경 스트라이프를
        줘서 읽기 쉽게 했다."""
        log = wb.get_country_intl_match_log(tid, country)
        box = QFrame()
        box.setStyleSheet(
            "background:#262626;border:1px solid #3a3a3a;border-left:3px solid #00cc44;"
            "border-radius:6px;")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)

        title = QLabel(f"📋 {header_title}")
        title.setStyleSheet("color:#00cc44;font-size:13px;font-weight:bold;")
        lay.addWidget(title)

        gs = log.get("group_standings")
        if gs and gs.get("rows"):
            glabel = QLabel(f"⚽ {gs['group']}조 순위표")
            glabel.setStyleSheet("color:#ffcc00;font-size:12px;font-weight:bold;")
            lay.addWidget(glabel)
            rows = gs["rows"]
            qualified = gs.get("qualified") or set()
            gtbl = QTableWidget(len(rows), 7)
            gtbl.setHorizontalHeaderLabels(["순위", "국가", "승", "무", "패", "득실", "승점"])
            gtbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            gtbl.verticalHeader().setVisible(False)
            gtbl.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            gtbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            gtbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            gtbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            gtbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            gtbl.setStyleSheet(
                "QTableWidget{background:#1a1a1a;color:#ccc;gridline-color:#2f2f2f;border:none;font-size:11px;}"
                "QHeaderView::section{background:#232323;color:#888;border:none;padding:3px;font-size:9px;}")
            for rank, t in enumerate(rows):
                is_me = (t["country"] == country)
                # _build_groups_grid와 동일한 진출/탈락 판정(qualified가
                # 있으면 그걸로, 없으면 순위<2 폴백) — 진출=흰색 굵게,
                # 탈락=회색.
                advancing = (rank == 0) or ((t["country"] in qualified) if qualified else (rank < 2))
                base_color = QColor("#ffffff" if advancing else "#777777")
                vals = [str(rank + 1), f"{t.get('flag', '')} {t['country']}".strip(),
                        str(t["wins"]), str(t["draws"]), str(t["losses"]),
                        f"{t['gd']:+d}", str(t["pts"])]
                for j, v in enumerate(vals):
                    it = QTableWidgetItem(v)
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    # 이름 칸(1번)만 "이 국가"면 금색으로 덧칠, 나머지 칸은
                    # 진출/탈락 색을 그대로 유지 — 숫자칸까지 금색으로
                    # 덮으면 탈락(회색) 표시가 안 보이게 되는 문제 방지.
                    if j == 1 and is_me:
                        it.setForeground(QColor("#ffcc00"))
                        f = it.font(); f.setBold(True); it.setFont(f)
                    else:
                        it.setForeground(base_color)
                        f = it.font(); f.setBold(advancing); it.setFont(f)
                    gtbl.setItem(rank, j, it)
            gtbl.setFixedHeight(gtbl.verticalHeader().defaultSectionSize() * len(rows) + 32)
            _enable_plain_copy(gtbl)
            lay.addWidget(gtbl)

        stages = log.get("stages") or []
        _RESULT_COLOR = {"승": "#4caf50", "무": "#ffcc00", "패": "#ff5555"}
        if not stages:
            empty = QLabel("경기 기록이 없습니다.")
            empty.setStyleSheet("color:#666;font-size:11px;")
            lay.addWidget(empty)
        for stage in stages:
            slabel = QLabel(f"🏆 {stage['stage_ko']}")
            slabel.setStyleSheet("color:#aaddff;font-size:12px;font-weight:bold;"
                                  "padding-top:4px;")
            lay.addWidget(slabel)
            for m in stage["matches"]:
                row_w = QWidget()
                row_w.setStyleSheet("background:#1c1c1c;border-radius:5px;")
                row_lay = QHBoxLayout(row_w)
                row_lay.setContentsMargins(10, 5, 10, 5)
                row_lay.setSpacing(10)
                opp_lbl = QLabel(f"vs {m['opponent']}")
                opp_lbl.setStyleSheet("color:#eee;font-size:12px;")
                row_lay.addWidget(opp_lbl, 1)
                res_lbl = QLabel(m["result"])
                res_lbl.setStyleSheet(
                    f"color:{_RESULT_COLOR.get(m['result'], '#ccc')};font-size:11px;"
                    "font-weight:bold;padding:2px 10px;background:#101010;border-radius:6px;")
                row_lay.addWidget(res_lbl)
                score_txt = f"{m['my_score']} : {m['opp_score']}"
                if m.get("pso"):
                    pso_score = m.get("pso_score") or ""
                    score_txt += f" (승부차기 {pso_score})" if pso_score else " (승부차기)"
                score_lbl = QLabel(score_txt)
                score_lbl.setStyleSheet("color:#aaa;font-size:12px;font-weight:bold;min-width:80px;")
                row_lay.addWidget(score_lbl)
                lay.addWidget(row_w)
        return box

    def _build_country_squad_detail_widget(self, tid, country, header_title, year_txt=None):
        """[2026-08 신설, 신민용 요청: "국가 검색에서 대회를 클릭하면
        그 대회 당시 주전들과 후보들이 떠야 한다"] _build_country_year_
        detail_widget(연도 클릭 → 경기 기록)과 완전히 같은 카드 스타일로,
        wb.get_country_tournament_squad()가 돌려주는 이 대회의 26인 풀을
        주전(11)/후보로 나눠 보여준다. 데이터 자체는 intl_squad에 이미
        저장돼 있는 걸 그대로 읽는 것뿐이라 새로 계산하지 않는다.

        [2026-08 재작업, 신민용 리포트: "내가 분명 포메이션 형태로
        보내달라 했는데 왜 없어?"] 표(QTableWidget)였던 주전 목록을
        _StaticPitchView(라이브 포메이션 화면과 같은 초록 피치 스타일)로
        바꿨다. starters는 position 문자열로 대략 정렬돼 있을 뿐 11개
        슬롯에 1:1로 배정돼 있진 않으므로(과거 버그로 9~10명만 기록된
        대회도 있음), 클럽팀 포메이션과 똑같이 formation_logic.
        _greedy_fill_slots(POSITION_COMPAT 기반)로 화면 배치만 다시
        계산한다 — 저장된 주전 명단 자체(누가 주전인지)는 그대로,
        "어느 자리에 그리느냐"만 시각화를 위해 재계산.

        year_txt: [2026-08 신설, 신민용 요청: "요약 복사 — 이 스쿼드에
        해당하는 연도만 남기고 선수 기록 복사"] 호출부(_on_country_
        detail_cell_clicked)가 이미 표에서 읽어둔 "연도" 칸 텍스트를
        그대로 넘겨준다 — "요약 복사" 버튼의 target_years로 쓴다."""
        from intl_engine import _INTL_MATCHDAY_STARTER_POS
        from formation_logic import _greedy_fill_slots
        squad = wb.get_country_tournament_squad(tid, country)
        box = QFrame()
        box.setStyleSheet(
            "background:#262626;border:1px solid #3a3a3a;border-left:3px solid #ffcc00;"
            "border-radius:6px;")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)

        starters, bench = squad.get("starters") or [], squad.get("bench") or []

        # [2026-08 신설, 신민용 요청: "이 대회명 써진 줄 우측에 복사하기
        # 버튼을 놔줘"] 헤더 행에 제목 라벨과 나란히(오른쪽 끝) 배치.
        header_row = QHBoxLayout()
        _btn_qss = (
            "QPushButton{background:#333;color:#ddd;border:1px solid #4a4a4a;"
            "border-radius:4px;padding:3px 10px;font-size:11px;}"
            "QPushButton:hover{background:#3d3d3d;}")
        title = QLabel(f"👥 {header_title}")
        title.setStyleSheet("color:#ffcc00;font-size:13px;font-weight:bold;")
        header_row.addWidget(title)
        # [2026-08 신설, 신민용 요청: "대회명 바로 옆에 이름 한번에
        # 변경 버튼을 만들어서, 쉼표로 구분해 주전 1번째부터 순서대로
        # 배정하고 싶다"] 대회명 라벨 바로 옆(복사 버튼들보다 앞쪽)에
        # 배치 — 공용 open_bulk_rename_dialog(formation_widget.py)를
        # 그대로 재사용한다(포메이션 화면의 좌/우 버튼과 동일 함수).
        if starters or bench:
            rename_btn = QPushButton("✏ 이름 일괄변경")
            rename_btn.setStyleSheet(_btn_qss)
            rename_btn.clicked.connect(
                lambda: self._on_bulk_rename_country_squad(tid, country, header_title, year_txt))
            header_row.addWidget(rename_btn)
        header_row.addStretch(1)
        if starters:
            # [2026-08 신설, 신민용 요청: "복사하기 버튼을 2개 만들건데
            # 1번째는 주전만, 2번째는 지금처럼 스쿼드 전체 — 주전 복사는
            # 주전으로 뛰었던 선수 11명만"]
            starter_copy_btn = QPushButton("📋 주전 기록 복사")
            starter_copy_btn.setStyleSheet(_btn_qss)
            _starter_ids = [r.get("id") for r in starters]
            starter_copy_btn.clicked.connect(
                lambda: self._copy_squad_player_records(
                    _starter_ids, starter_copy_btn, "📋 주전 기록 복사"))
            header_row.addWidget(starter_copy_btn)
        if starters or bench:
            squad_copy_btn = QPushButton("📋 스쿼드 기록 복사")
            squad_copy_btn.setStyleSheet(_btn_qss)
            _ids = [r.get("id") for r in (starters + bench)]
            squad_copy_btn.clicked.connect(
                lambda: self._copy_squad_player_records(_ids, squad_copy_btn))
            header_row.addWidget(squad_copy_btn)
            # [2026-08 신설, 신민용 요청: "요약 복사 — 이 대회 연도에
            # 해당하는 선수 기록 부분만 남기고 복사"] year_txt(표의
            # "연도" 칸 텍스트, 예: "2000")를 정수로 파싱해 target_years로
            # 넘긴다 — 못 읽으면(과거 데이터 등) 버튼 자체를 만들지 않는다
            # (걸러줄 기준 연도가 없으면 "요약"이라는 이름이 무의미하므로).
            _year_int = None
            try:
                _year_int = int(str(year_txt).strip()) if year_txt else None
            except (TypeError, ValueError):
                _year_int = None
            if _year_int is not None:
                summary_copy_btn = QPushButton("📋 요약 복사")
                summary_copy_btn.setStyleSheet(_btn_qss)
                _summary_years = {_year_int}
                summary_copy_btn.clicked.connect(
                    lambda: self._copy_squad_player_records(
                        _ids, summary_copy_btn, "📋 요약 복사", target_years=_summary_years))
                header_row.addWidget(summary_copy_btn)
        lay.addLayout(header_row)

        if not starters and not bench:
            empty = QLabel("스쿼드 기록이 없습니다.")
            empty.setStyleSheet("color:#666;font-size:11px;")
            lay.addWidget(empty)
            return box

        if squad.get("approx"):
            note = QLabel("⚠ 이 대회는 주전/후보 구분 기록이 없어(출전 횟수 기준) 상위 11명을 주전으로 표시합니다.")
            note.setStyleSheet("color:#888;font-size:10px;")
            note.setWordWrap(True)
            lay.addWidget(note)

        # [2026-08 재작업, 신민용 리포트: "좌측에 포메이션을 저렇게 박으면
        # 우측에 후보 선수들의 이름을 나열해야지 — 주전들은 초록색으로,
        # 후보들은 아래에 색이 없는 원래 상태로 나열"] 피치(좌)와 명단
        # 패널(우, 주전 초록/후보 무채색)을 가로로 나란히 배치.
        content_row = QHBoxLayout()
        content_row.setSpacing(14)
        if starters:
            cands = [dict(r) for r in starters]
            placed = _greedy_fill_slots(cands, _INTL_MATCHDAY_STARTER_POS)
            slot_players = [
                (_INTL_MATCHDAY_STARTER_POS[i],
                 (p.get("display_name") if p else None),
                 (p.get("id") if p else None))
                for i, p in enumerate(placed)]
            pitch = _StaticPitchView(
                formation="", slot_players=slot_players, slots=_INTL_MATCHDAY_STARTER_POS,
                on_click=self.open_to_player)
            content_row.addWidget(pitch)
        roster = _build_squad_roster_panel(starters, bench, on_click=self.open_to_player)
        content_row.addWidget(roster, 1)
        lay.addLayout(content_row)
        return box

    def _on_bulk_rename_country_squad(self, tid, country, header_title, year_txt):
        """[2026-08 신설, 신민용 요청] 대회명 옆 "이름 일괄변경" 버튼의
        진입점. open_bulk_rename_dialog(formation_widget.py와 공유하는
        함수)로 이 대회의 주전/후보 이름을 한 번에 바꾼 뒤, 지금 펼쳐진
        이 카드를 최신 데이터로 다시 지어 새 이름이 접었다 펴지 않아도
        바로 보이게 한다(_on_country_detail_cell_clicked이 처음 펼칠 때
        쓰는 것과 완전히 같은 셀 위젯 교체 절차)."""
        squad = wb.get_country_tournament_squad(tid, country)
        starters, bench = squad.get("starters") or [], squad.get("bench") or []
        changed = open_bulk_rename_dialog(self, starters, bench)
        if not changed:
            return
        exp = getattr(self, "_country_expanded", None)
        if not exp or exp.get("tid") != tid:
            return
        tbl = self.country_detail_tbl
        detail_row = exp.get("detail_row")
        if detail_row is None or not (0 <= detail_row < tbl.rowCount()):
            return
        new_widget = self._build_country_squad_detail_widget(tid, country, header_title, year_txt)
        tbl.setCellWidget(detail_row, 0, new_widget)
        tbl.resizeRowToContents(detail_row)
        h = new_widget.sizeHint().height()
        if h > tbl.rowHeight(detail_row):
            tbl.setRowHeight(detail_row, h + 8)

    def _open_world_browser_from_country_squad(self, players, row):
        """[2026-08 신설] 국가 검색의 대회 스쿼드 표에서 선수 이름을
        클릭하면(포지션/OVR 칸이어도 무방 — 행 전체 클릭) 그 선수의
        세계 기록실 상세를 바로 연다. 이 창 자체가 이미 세계 기록실이므로
        새 창을 띄우지 않고 "선수 검색" 탭으로 전환해서 그 자리에서 보여준다."""
        if row < 0 or row >= len(players):
            return
        pid = players[row].get("id")
        if pid is None:
            return
        self.open_to_player(pid)

    def _copy_squad_player_records(self, ids, btn, reset_label="📋 스쿼드 기록 복사", target_years=None):
        """[2026-08 신설, 신민용 요청: "이 대회명 써진 줄 우측에 복사하기
        버튼을 놔줘 — 여기 들어간 선수들의 선수 기록을 한꺼번에 복사하는
        용도"] ui/formation_widget.py의 _on_copy_squad_clicked와 완전히
        같은 하베스터 패턴을 쓴다 — 지금 이 창(self)을 그대로 써서 선수
        마다 open_to_player를 부르면 그때마다 "선수 검색" 탭으로 화면이
        전환돼버려, 사용자가 보고 있던 국가/팀 검색 화면이 계속 다른
        탭으로 튀는 문제가 생긴다. 그래서 화면에 띄우지 않는 별도
        WorldBrowserWindow 인스턴스를 하나 더 만들어 그 안에서만 조회하고
        끝나면 버린다. 기존 country_copy_btn/team_copy_btn(그 나라/팀
        자체의 트로피·시즌 기록 텍스트를 복사)과는 전혀 다른 기능 —
        여기서는 이 스쿼드에 속한 선수 개개인의 [선수 기록] 텍스트를
        모아서 한 번에 복사한다.

        [2026-08 확장, 신민용 요청: "복사하기 버튼을 2개 만들건데 하나는
        주전만, 하나는 스쿼드 전체"] 같은 창(주전만/전체 스쿼드)에 버튼이
        2개가 되면서 눌렀을 때 되돌아갈 라벨도 버튼마다 달라야 하므로
        reset_label을 인자로 받는다.

        [2026-08 확장, 신민용 요청: "요약 복사 — 2006년 월드컵 주전을
        뽑으면, 선수 기록 중 그 2006년에 해당하는 [연도별 기록]/
        [국가대표 기록]만 남기고 나머지 연도는 빼줘"] target_years(연도
        집합)를 넘기면 rows(연도별 기록)·intl_records(국가대표 기록)를
        그 연도에 속한 항목만 남기고 걸러서 넘긴다 — 기본 정보 한 줄과
        "소속팀 기준 통산 수상"(원래도 연도 무관한 커리어 총합) 줄은
        그대로 둔다. None이면(기본값) 기존처럼 전체 연도를 다 넣는다."""
        ids = [i for i in dict.fromkeys(ids) if i is not None]
        if not ids:
            return
        harvester = WorldBrowserWindow(self)
        texts = []
        try:
            for pid in ids:
                try:
                    harvester.open_to_player(pid)
                except Exception:
                    continue
                name = getattr(harvester, "_player_copy_name", None)
                d = getattr(harvester, "_player_copy_d", None)
                if not name or not d:
                    continue
                team_hist = getattr(harvester, "_player_copy_team_hist", None)
                rows = getattr(harvester, "_player_copy_rows", [])
                intl_records = getattr(harvester, "_player_copy_intl_records", [])
                if target_years is not None:
                    rows = [r for r in rows if r.get("year") in target_years]
                    intl_records = [r for r in intl_records if r.get("year") in target_years]
                texts.append(harvester._format_player_history_text(
                    name, d, team_hist, rows, intl_records))
        finally:
            harvester.deleteLater()
        if not texts:
            return
        QGuiApplication.clipboard().setText("\n".join(texts))
        btn.setText("✅ 복사됨")
        QTimer.singleShot(1200, lambda: btn.setText(reset_label))

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
        재사용, 데이터 소스(el_*)만 다르다.
        [2026-08 수정, 신민용 리포트: "슈퍼컵/컨퍼런스/유로파는 1등이
        다른 색이라 잘 안 보인다 — 월드컵/챔스처럼 노란색으로 통일해달라"]
        winner_color를 대회 고유색(주황) 대신 CL/월드컵/클럽월드컵과
        똑같은 Qt.GlobalColor.yellow로 통일."""
        return self._build_cl_style_tab(
            tbl_attr="el_tbl", combo_attr="el_cont_combo",
            history_fn=wb.get_el_history, detail_fn=wb.get_el_tournament_detail,
            winner_color=Qt.GlobalColor.yellow,
            rank_fn=wb.get_el_rank_leaders, tab_title="유로파리그")

    def _build_ecl_tab(self):
        """[2026-08 신설] 역대 컨퍼런스리그 — 위와 동일 패턴.
        [2026-08 수정, 신민용 리포트: 위 _build_el_tab과 동일] 우승 강조색을
        노란색으로 통일."""
        return self._build_cl_style_tab(
            tbl_attr="ecl_tbl", combo_attr="ecl_cont_combo",
            history_fn=wb.get_ecl_history, detail_fn=wb.get_ecl_tournament_detail,
            winner_color=Qt.GlobalColor.yellow,
            rank_fn=wb.get_ecl_rank_leaders, tab_title="컨퍼런스리그")

    def _build_sc_tab(self):
        """[2026-08 신설, 10순위/11순위] 역대 슈퍼컵 — 위와 동일 패턴.
        [2026-08 수정, 신민용 리포트: 위 _build_el_tab과 동일] 예전엔
        "경기 일정 화면과 색을 맞추자"는 이유로 버건디를 썼지만, 세계
        기록실의 "우승" 강조는 다른 대회들과 일관되게 노란색으로
        바꿔달라는 요청이 우선 — 경기 일정 화면(버건디)과는 별개다."""
        return self._build_cl_style_tab(
            tbl_attr="sc_tbl", combo_attr="sc_cont_combo",
            history_fn=wb.get_super_cup_history, detail_fn=wb.get_super_cup_tournament_detail,
            winner_color=Qt.GlobalColor.yellow,
            rank_fn=wb.get_super_cup_rank_leaders, tab_title="슈퍼컵")

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
    # 컨퍼런스/슈퍼컵 4개 대륙대회의 (버튼 라벨, tab_title, rank_fn,
    # keys, key_labels) 목록. 슈퍼컵은 3/4위전이 없는 대회(총 3경기:
    # 준결승2+결승)라 keys가 다른 셋과 다르게 winner/runner_up 2개뿐이다
    # — 그래서 각 항목에 keys/key_labels를 따로 들고 다닌다.
    # [2026-08 수정, 신민용 리포트: "슈퍼컵도 3/4위전이 생겼으니 최다 순위도
    # 1~4위까지 다 떠야 한다"] 3/4위전 추가 전에는 winner/runner_up 2개뿐
    # 이었는데, 이제 CL/EL/ECL과 완전히 같은 4자리 구조가 됐다.
    def _cl_style_rank_specs(self):
        _four = ("winner", "runner_up", "third", "fourth")
        _four_labels = ["🥇 1위 팀", "🥈 2위 팀", "🥉 3위 팀", "4위 팀"]
        return [
            ("챔피언스", "챔피언스리그", wb.get_cl_style_rank_leaders, _four, _four_labels),
            ("유로파", "유로파리그", wb.get_el_rank_leaders, _four, _four_labels),
            ("컨퍼런스", "컨퍼런스리그", wb.get_ecl_rank_leaders, _four, _four_labels),
            ("슈퍼컵", "슈퍼컵", wb.get_super_cup_rank_leaders, _four, _four_labels),
        ]

    def _open_cl_style_rank_dialog(self, tab_title, rank_fn, continent_value,
                                    keys=("winner", "runner_up", "third", "fourth"),
                                    key_labels=("🥇 1위 팀", "🥈 2위 팀", "🥉 3위 팀", "4위 팀")):
        """대륙대회(챔스/유로파/컨퍼런스/슈퍼컵) '최다 순위' 팝업을 연다.
        상단에 같은 성격의 다른 대회로 바로 넘어가는 이동 버튼(현재 화면
        제외)을 같이 붙인다 — 클릭하면 이 팝업을 닫고 그 대회의 팝업을
        새로 연다."""
        options = [(_ALL, None), ("유럽", "유럽"), ("아시아", "아시아"),
                   ("아프리카", "아프리카"), ("북남미", "북남미")]
        nav_buttons = []
        for label, other_title, other_fn, other_keys, other_labels in self._cl_style_rank_specs():
            if other_title == tab_title:
                continue   # 현재 보고 있는 화면은 이동 버튼에서 제외
            nav_buttons.append((label, lambda t=other_title, f=other_fn, k=other_keys, kl=other_labels:
                                 self._open_cl_style_rank_dialog(
                                     t, f, RANKING_FILTER_DEFAULT, keys=k, key_labels=kl)))
        dlg = RankLeadersDialog(tab_title, rank_fn(continent=continent_value),
                                 keys=keys, key_labels=list(key_labels),
                                 filter_label="대륙", filter_options=options,
                                 filter_default=continent_value,
                                 fetch_fn=lambda cont: rank_fn(continent=cont),
                                 nav_buttons=nav_buttons,
                                 parent=self)
        dlg.show()

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

    def _open_intl_detail(self, tbl, row, wc, highlight_country=None):
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
                                     qualifiers=qualifiers, parent=self,
                                     highlight_country=highlight_country)
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

    # ── [2026-08 신설] 파워랭킹 탭 ──────────────────────────────────
    # power_ranking.py(팀/국가 Elo 레이팅 + 연도별 스냅샷)를 그대로 읽어
    # 보여주기만 하는 화면 — 계산 자체는 연도 전환 시(game_engine._advance_week)
    # 이미 끝나 있으므로 여기선 SELECT만 한다. 좌: 팀 파워랭킹(대륙 탭),
    # 우: 국가 파워랭킹. 신민용 설계 mockup(전체/아시아/유럽/아프리카/
    # 아메리카 탭 + 순위 클릭 시 이전 순위 이력)을 그대로 따른다.
    def _build_power_ranking_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)

        top = QHBoxLayout()
        self.pr_year_label = QLabel("파워랭킹")
        self.pr_year_label.setStyleSheet("color:#00cc44;font-size:14px;font-weight:bold;")
        top.addWidget(self.pr_year_label)
        top.addStretch(1)
        # [2026-08 수정, 신민용 리포트: "연도 필터를 콤보박스 목록으로 두면
        # 나중에 50년 넘게 쌓였을 때 목록이 너무 길어진다"] 스크롤해야 하는
        # 드롭다운 대신, 숫자를 직접 입력하거나 화살표로 1년씩 넘기는
        # QSpinBox로 바꾼다. 최소값은 GAME_START_YEAR — 게임 시작 연도의
        # 국가 파워랭킹(초기 시드)부터 조회 가능해야 하므로.
        lbl_year = QLabel("연도"); lbl_year.setStyleSheet("color:#888;font-size:11px;")
        self.pr_year_spin = QSpinBox()
        # [2026-08 v3.3 버그수정, 신민용 리포트: "1998년으로 시작했는데
        # 파워랭킹 창은 2000년 고정으로 뜨고 데이터가 없다"] GAME_START_YEAR는
        # constants.py의 고정 상수(항상 2000)라 커스텀 시작 연도를 반영 못
        # 한다 — database.get_game_start_year()(플레이어가 실제로 고른
        # 시작 연도)로 교체. power_ranking.py의 시드 생성 함수들도 이미
        # 같은 이유로 이걸 쓰도록 고쳐뒀다(둘이 일치해야 시드가 실제로
        # 이 스핀박스 범위 안에서 조회됨).
        _gsy = get_game_start_year()
        self.pr_year_spin.setRange(_gsy, _gsy + 300)
        self.pr_year_spin.setValue(_gsy)
        self.pr_year_spin.valueChanged.connect(self._refresh_power_ranking_tables)
        top.addWidget(lbl_year)
        top.addWidget(self.pr_year_spin)
        self.pr_year_latest_btn = QPushButton("최신")
        self.pr_year_latest_btn.setToolTip("가장 최근에 계산된 파워랭킹 연도로 이동")
        self.pr_year_latest_btn.clicked.connect(self._on_pr_jump_to_latest_year)
        top.addWidget(self.pr_year_latest_btn)
        lay.addLayout(top)

        info = QLabel("ℹ️ 순위를 더블클릭하면 그 팀/국가의 연도별 순위 이력을 볼 수 있습니다.")
        info.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(info)

        split = QSplitter(Qt.Orientation.Horizontal)

        # ── 왼쪽: 팀 파워랭킹 ──
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_title = QLabel("🏟 팀 파워랭킹")
        left_title.setStyleSheet("color:#eee;font-size:13px;font-weight:bold;")
        left_lay.addWidget(left_title)

        tab_row = QHBoxLayout()
        self.pr_team_tab_group = []
        for tab_name in pr.TEAM_POWER_RANKING_TABS:
            btn = QPushButton(tab_name)
            btn.setCheckable(True)
            btn.setChecked(tab_name == "전체")
            # [2026-08 버그수정, 신민용 리포트: "버튼이 추가된 만큼 창이
            # 안 넓어져서 좁아 보인다"] 대륙 탭 버튼 5개가 QHBoxLayout
            # 기본 크기로는 서로 눌려서 텍스트가 잘릴 수 있다 — 버튼마다
            # 최소 폭을 보장해 항상 읽히게 하고, 창 쪽도 아래에서
            # _pr_ensure_window_width()로 이 최소 폭 합계가 들어갈
            # 공간을 스스로 확보한다.
            btn.setMinimumWidth(76)
            btn.clicked.connect(lambda _checked, t=tab_name: self._on_pr_team_tab_clicked(t))
            tab_row.addWidget(btn)
            self.pr_team_tab_group.append(btn)
        left_lay.addLayout(tab_row)
        self._pr_current_team_tab = "전체"

        # [2026-08 신설, 신민용 요청] 국가 쪽과 동일한 검색바 — 이름이
        # 겹치면(예: "FC") 순위 높은 순(=순위 숫자가 작은 순, 이미
        # team_entries가 그 순서로 정렬돼 있어 필터링만 하면 자동으로
        # 유지됨)으로 나열. "전체" 탭이면 전체 팀 중에서, "아프리카" 탭이
        # 선택된 상태면 아프리카 팀 중에서만 검색되고, 이때 순위 칸은
        # 검색 결과 안에서 다시 매긴 번호가 아니라 '지금 선택된 범위(전체
        # 또는 그 대륙) 안에서 이 팀의 실제 순위'를 그대로 보여준다 —
        # 국가 검색과 완전히 같은 원칙(_apply_pr_country_search 참고).
        # 검색창 우측엔 국가 필터를 하나 더 둔다(신민용 요청) — 이 콤보의
        # 선택지 자체가 지금 선택된 대륙 탭에 종속된다: "아시아" 탭이면
        # 아시아(+오세아니아) 국가만, "유럽" 탭이면 유럽 국가만 뜬다.
        # 탭이 바뀔 때마다 _pr_refresh_team_country_filter_options()가
        # 다시 채운다.
        team_search_row = QHBoxLayout()
        self.pr_team_search_box = QLineEdit()
        self.pr_team_search_box.setPlaceholderText("🔎 팀명 검색")
        self.pr_team_search_box.textChanged.connect(self._on_pr_team_search_changed)
        team_search_row.addWidget(self.pr_team_search_box, 1)
        self.pr_team_country_combo = QComboBox()
        self.pr_team_country_combo.addItem("전체 국가")
        # [2026-08 신설, 신민용 요청: "국가 필터에서 선수 검색 국적
        # 입력처럼 내가 직접 입력할 수도 있으면 좋겠다 — '대'라고 치면
        # 대한민국이 완성되는 것처럼"] 선수 검색 탭과 같은 헬퍼
        # (_make_combo_typable) 재사용 — 타이핑하면 부분일치 자동완성이
        # 뜨고 엔터로 확정된다. 대륙 탭이 바뀌어 이 콤보가 다시 채워져도
        # (_pr_refresh_team_country_filter_options의 clear()+addItem())
        # 컴플리터는 콤보 모델을 그대로 참조하므로 자동으로 최신 목록을
        # 따라간다.
        self._make_combo_typable(self.pr_team_country_combo)
        self.pr_team_country_combo.currentTextChanged.connect(self._on_pr_team_search_changed)
        team_search_row.addWidget(self.pr_team_country_combo)
        left_lay.addLayout(team_search_row)

        # [2026-08 신설] 팀 명과 대륙 사이에 '부'(현재 소속 리그 등급)를
        # 넣어 분류를 한 단계 더 세분화(신민용 요청) —
        # 순위/전년/팀/부/대륙/국가/점수 7열.
        self.pr_team_tbl = QTableWidget(0, 7)
        self.pr_team_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.pr_team_tbl.verticalHeader().setVisible(False)
        self.pr_team_tbl.setHorizontalHeaderLabels(["순위", "전년", "팀", "부", "대륙", "국가", "점수"])
        self.pr_team_tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.pr_team_tbl.cellDoubleClicked.connect(self._on_pr_team_row_double_clicked)
        _enable_plain_copy(self.pr_team_tbl)
        left_lay.addWidget(self.pr_team_tbl, 1)
        split.addWidget(left)

        # ── 오른쪽: 국가 파워랭킹 (211개국 전체) ──
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_title = QLabel("🌍 국가 파워랭킹 (211개국)")
        right_title.setStyleSheet("color:#eee;font-size:13px;font-weight:bold;")
        right_lay.addWidget(right_title)

        # [2026-08 신설, 신민용 요청] "국가 파워랭킹" 글자 바로 아래 검색바 —
        # 나라 이름을 치면 그 나라(들)만 남기고, 순위 칸은 필터링된 목록
        # 안에서의 순번이 아니라 '전체 211개국 기준 실제 순위'를 그대로
        # 보여준다(예: "중" 검색 시 중국·중화 타이베이·중앙아프리카공화국이
        # 각각 자기 실제 순위(예: 91위)를 달고 나옴 — 검색 결과 안에서
        # 1,2,3위로 다시 매기지 않는다). 검색창 좌측엔 대륙 필터를 하나 더
        # 둔다(신민용 요청) — 이 콤보는 팀 쪽 5탭(오세아니아→아시아,
        # 북미+남미→아메리카로 합침)과 달리 countries.continent 실제 값을
        # 그대로 쓴다(국가 쪽은 합쳐 보여달라는 요청이 없었음).
        search_row = QHBoxLayout()
        self.pr_country_continent_combo = QComboBox()
        self.pr_country_continent_combo.addItem("전체 대륙")
        for cont in ["아시아", "유럽", "아프리카", "북미", "남미", "오세아니아"]:
            self.pr_country_continent_combo.addItem(cont)
        self.pr_country_continent_combo.currentTextChanged.connect(self._on_pr_country_search_changed)
        search_row.addWidget(self.pr_country_continent_combo)
        self.pr_country_search_box = QLineEdit()
        self.pr_country_search_box.setPlaceholderText("🔎 국가명 검색")
        self.pr_country_search_box.textChanged.connect(self._on_pr_country_search_changed)
        search_row.addWidget(self.pr_country_search_box, 1)
        right_lay.addLayout(search_row)

        self.pr_country_tbl = QTableWidget(0, 5)
        self.pr_country_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.pr_country_tbl.verticalHeader().setVisible(False)
        self.pr_country_tbl.setHorizontalHeaderLabels(["순위", "전년", "국가", "대륙", "점수"])
        self.pr_country_tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.pr_country_tbl.cellDoubleClicked.connect(self._on_pr_country_row_double_clicked)
        _enable_plain_copy(self.pr_country_tbl)
        right_lay.addWidget(self.pr_country_tbl, 1)
        split.addWidget(right)
        # 팀 쪽이 대륙 탭 버튼 5개 + 6열 표라 국가 쪽(검색바 + 5열)보다
        # 가로 공간이 더 필요 — 5.5 : 4.5 비율로 시작 폭을 나눠준다.
        split.setSizes([620, 520])

        lay.addWidget(split, 1)

        self._pr_team_entries_cache = []    # 검색 필터링용 — 순위는 여기 원본(범위 내) 값을 그대로 씀
        self._pr_country_entries_cache = []  # 검색 필터링용 — 순위는 여기 원본 값을 그대로 씀
        self._pr_tab_widget = w
        # [2026-08 버그수정, 신민용 리포트: "처음 들어가면 순위 10위부터
        # '...'으로 깨져 뜨고, 아무 버튼이나 한 번 누르면 그제서야 제대로
        # 뜬다"] 다이얼로그가 실제로 화면에 show()되어 스타일시트 폰트가
        # 최종 확정(polish)되기 전에 resizeColumnsToContents()로 열 폭을
        # 재는 게 원인이었다 — "1"~"9"(한 자리 숫자)는 그 시점의(아직 최종
        # 폰트로 안 굳은) 좁은 폭에도 어쩌다 들어맞지만, "10" 이상(두 자리)
        # 이나 "신규"(두 글자) 같은 조금 더 넓은 텍스트는 그 폭에 안 맞아
        # 말줄임(…)으로 잘렸다. 사용자가 버튼을 누르면 그 시점엔 이미
        # 창이 화면에 떠서 폰트가 확정된 뒤라 다시 재면 정확히 나왔던 것.
        # 해결: 최초 채우기를 이 생성자 호출 스택이 끝나고 이벤트 루프가
        # 한 바퀴 돈 뒤(=창이 실제로 show()된 뒤)로 미룬다.
        QTimer.singleShot(0, lambda: self._pr_load_years(initial=True))
        return w

    def _pr_ensure_window_width(self, tab_widget):
        """[2026-08 신설, 신민용 리포트: "버튼 추가되면 창도 그에 맞춰
        늘려달라"] 파워랭킹 탭(대륙 버튼 5개 + 검색바 + 대륙 필터)이
        요구하는 최소 폭을 계산해서, 지금 창이 그보다 좁으면 넓혀준다.
        화면보다 커지면 안 되므로 기존 _clamp_and_resize와 동일하게
        화면 안으로 잘라 적용한다. _pr_load_years(initial=True)가 실제
        데이터를 다 채운 뒤 호출해야 sizeHint가 정확하므로, 그 뒤에서
        불린다(생성자 시점엔 아직 표가 비어 있어 폭이 작게 잡힘)."""
        needed = tab_widget.sizeHint().width() + 80
        cur = self.width()
        if needed > cur:
            screen = QGuiApplication.primaryScreen()
            max_w = screen.availableGeometry().width() - 40 if screen else needed
            self.resize(min(needed, max_w), self.height())

    def _on_pr_jump_to_latest_year(self):
        latest = pr.get_latest_ranking_year(get_conn())
        if latest is not None:
            self.pr_year_spin.setValue(latest)  # setValue 자체가 valueChanged를 쏴서 갱신됨

    def _pr_load_years(self, initial=False):
        """[2026-08 수정, 신민용 요청: "연도 필터도 게임 시작년도부터",
        "50년 넘게 쌓이면 콤보 목록이 너무 길다 — 직접 입력하는 방식도"]
        예전엔 계산된 연도만 콤보에 채워 넣는 방식이었는데, 이제 연도는
        QSpinBox 직접 입력이라 목록을 채울 필요가 없다 — 대신 스핀박스
        기본값을 '최신 계산 연도'로 맞춰주기만 하면 된다. 국가는
        ensure_initial_country_power_ranking() 덕분에 최소 GAME_START_YEAR
        스냅샷이 항상 있으므로 get_latest_ranking_year()는 절대 None이
        아니다."""
        conn = get_conn()
        latest = pr.get_latest_ranking_year(conn)
        if initial:
            self.pr_year_spin.blockSignals(True)
            self.pr_year_spin.setValue(latest if latest is not None else get_game_start_year())
            self.pr_year_spin.blockSignals(False)
            self._pr_refresh_team_country_filter_options()
        self._refresh_power_ranking_tables()
        if initial:
            self._pr_ensure_window_width(self._pr_tab_widget)

    def _on_pr_team_tab_clicked(self, tab_name):
        for btn in self.pr_team_tab_group:
            btn.setChecked(btn.text() == tab_name)
        self._pr_current_team_tab = tab_name
        self._pr_refresh_team_country_filter_options()
        self._refresh_power_ranking_tables()

    def _refresh_power_ranking_tables(self, *_a):
        ranking_year = self.pr_year_spin.value()
        conn = get_conn()
        self.pr_year_label.setText(f"📊 {ranking_year}년 파워랭킹 (평가 시즌: {ranking_year - 1}년)")

        # [2026-08 수정, 신민용 리포트: "100위 안에 없는 팀은 검색해도
        # 안 뜬다 — 물루 치면 100위 밖이라도 FC 물루즈 같은 팀들이
        # 순위 그대로 떠야 한다"] 캐시 자체는 이 범위(전체/그 대륙)의
        # 전체 팀을 다 담아두고(limit을 사실상 무제한으로), 화면에
        # '기본으로' 보여줄 때만(검색어가 비어 있을 때만) 상위 100위로
        # 자른다 — _apply_pr_team_search()에서 처리.
        team_entries = pr.get_team_power_ranking_grouped(
            conn, ranking_year, tab=self._pr_current_team_tab, limit=100000)
        # [2026-08 신설, 신민용 요청] 검색창을 넣기 전엔 "전체 목록에서
        # 몇 번째 줄인가"(local_rank=True → i+1)로 순위를 매겼는데, 검색으로
        # 목록이 걸러지면 그 i+1이 검색 결과 안에서의 번호로 바뀌어 버려
        # "지금 이 팀의 실제 순위"가 아니게 된다(국가 검색 때와 같은 문제).
        # 그래서 지금(=검색 필터 적용 전, 선택된 대륙 범위 전체) 시점에
        # e.rank를 '이 범위 안에서의 실제 순위'로 한 번 확정해서 캐시해두고,
        # 이후 검색은 이 캐시를 필터링만 할 뿐 순위를 다시 매기지 않는다.
        for i, e in enumerate(team_entries):
            e.rank = i + 1
        self._pr_team_entries_cache = team_entries
        self._apply_pr_team_search()

        # 211개국 전체 — get_country_power_ranking의 limit 기본값(250)이
        # 이미 다 커버하므로 별도 조정 불필요.
        self._pr_country_entries_cache = pr.get_country_power_ranking(conn, ranking_year)
        self._apply_pr_country_search()

    def _on_pr_team_search_changed(self, _text):
        self._apply_pr_team_search()

    def _apply_pr_team_search(self):
        """[2026-08 신설, 신민용 요청] "이름이 겹치면 나열을 순위가 높은
        순으로" — _pr_team_entries_cache가 이미 순위(=rating) 내림차순으로
        정렬돼 있으므로 부분일치로 필터링만 하면 자동으로 그 순서가
        유지된다. 순위 칸은 검색 결과 안에서 다시 매긴 번호가 아니라
        '지금 선택된 대륙 범위 안에서 이 팀의 실제 순위'(_refresh_power_
        ranking_tables에서 미리 확정해둔 e.rank)를 그대로 보여준다.

        [2026-08 수정, 신민용 리포트: "100위 밖 팀도 검색하면 순위
        그대로 떠야 한다"] 검색어/국가 필터가 둘 다 기본값(빈 검색어 +
        '전체 국가')일 때만 기본 노출 상위 100위로 자른다 — 둘 중 하나라도
        활성화되면 캐시(이 범위 전체 팀)에서 필터링하므로 1만위짜리
        팀이어도, 또는 특정 국가를 골랐을 때 그 나라 팀 전부가 실제
        순위를 달고 나온다."""
        query = self.pr_team_search_box.text().strip()
        country = self.pr_team_country_combo.currentText()
        country_active = bool(country) and country != "전체 국가"
        if not query and not country_active:
            entries = self._pr_team_entries_cache[:100]
        else:
            entries = self._pr_team_entries_cache
            if country_active:
                entries = [e for e in entries if e.country == country]
            if query:
                entries = [e for e in entries if query in e.team_name]
        self._render_team_power_table(entries)

    def _pr_refresh_team_country_filter_options(self):
        """[2026-08 신설, 신민용 요청] "아시아를 선택하면 아시아 국가들만
        필터에 뜨고 유럽은 유럽 국가들만" — 지금 선택된 대륙 탭에 맞춰
        국가 필터 콤보의 선택지 자체를 다시 채운다. 탭 전환 시(_on_pr_team_
        tab_clicked)와 최초 로드 시 호출된다."""
        combo = self.pr_team_country_combo
        prev_selected = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("전체 국가")
        for name in pr.get_countries_in_tab_group(get_conn(), self._pr_current_team_tab):
            combo.addItem(name)
        # 대륙 탭이 바뀌어도 이전에 골랐던 국가가 새 목록에 여전히 있으면
        # 선택을 유지한다(예: "아시아"에서 "한국" 고른 채로 "전체"로
        # 돌아가도 "한국"이 그대로 남아 있어야 자연스러움).
        idx = combo.findText(prev_selected)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _render_team_power_table(self, entries):
        # local_rank=False → 이미 확정해둔(범위 내 실제) e.rank를 그대로 쓴다.
        self._fill_power_ranking_table(
            self.pr_team_tbl, entries,
            name_fn=lambda e: e.team_name, group_fn=lambda e: e.country,
            continent_fn=lambda e: e.continent, tier_fn=lambda e: e.tier,
            id_role_fn=lambda e: e.team_id, local_rank=False)

    def _on_pr_country_search_changed(self, _text):
        self._apply_pr_country_search()

    def _apply_pr_country_search(self):
        """[2026-08 신설, 신민용 요청] 검색어로 필터링해도 순위 숫자는
        '211개국 전체 기준 실제 순위'를 그대로 보여준다 — 필터링된 목록
        안에서 1위부터 다시 매기지 않는다("중" 검색 시 중국이 91위면
        그대로 91위로 표시). 대륙 필터(신민용 요청, 검색바 좌측)와
        국가명 검색은 AND로 같이 적용된다."""
        query = self.pr_country_search_box.text().strip()
        continent = self.pr_country_continent_combo.currentText()
        entries = self._pr_country_entries_cache
        if continent and continent != "전체 대륙":
            entries = [e for e in entries if e.continent == continent]
        if query:
            entries = [e for e in entries if query in e.country]
        self._render_country_power_table(entries)

    def _render_country_power_table(self, entries):
        # local_rank=False → 저장된(또는 seed) e.rank를 그대로 쓴다.
        self._fill_power_ranking_table(
            self.pr_country_tbl, entries,
            name_fn=lambda e: e.country, group_fn=lambda e: e.continent,
            id_role_fn=lambda e: e.country, local_rank=False)

    def _fill_power_ranking_table(self, tbl, entries, name_fn, group_fn, id_role_fn,
                                   local_rank, continent_fn=None, tier_fn=None):
        # continent_fn이 있으면(팀 표) 팀명과 국가 사이에 대륙 칸을 하나 더
        # 넣는다(신민용 요청: "국가와 팀명 사이에 대륙명도 넣어서 분류를
        # 좀 더 세부적으로"). 국가 표는 이미 group_fn 자체가 대륙이라
        # continent_fn 없이 기존 5열 그대로 쓴다. tier_fn이 있으면(팀 표만)
        # 팀명과 대륙 사이에 '부'(현재 소속 리그 등급) 칸을 하나 더
        # 넣는다(신민용 요청: "팀이랑 대륙 사이에 부란 단어 넣고").
        n_cols = 5 + (1 if continent_fn else 0) + (1 if tier_fn else 0)
        tbl.setRowCount(len(entries))
        for i, e in enumerate(entries):
            # local_rank=True인 팀 탭은 대륙별로 걸러진 목록이라, 저장된
            # e.rank(전체 기준 글로벌 순위)가 아니라 이 목록 안에서의 순번을
            # "순위" 칸에 보여준다 — "아시아 1~100위" 같은 표기와 맞추기 위함.
            # 국가 표는 항상 e.rank(211개국 전체 기준 실제 순위)를 그대로 쓴다.
            display_rank = (i + 1) if local_rank else e.rank
            prev_arrow = ""
            if e.prev_rank is not None:
                diff = e.prev_rank - e.rank
                if diff > 0:
                    prev_arrow = f"▲{diff}"
                elif diff < 0:
                    prev_arrow = f"▼{-diff}"
                else:
                    prev_arrow = "—"
            else:
                prev_arrow = "신규"
            vals = [str(display_rank), prev_arrow, name_fn(e)]
            if tier_fn:
                tier = tier_fn(e)
                vals.append(f"{tier}부" if tier else "-")
            if continent_fn:
                vals.append(continent_fn(e))
            vals.append(group_fn(e))
            vals.append(f"{e.rating:.1f}")
            for j, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 1:
                    # [2026-08 버그수정, 신민용 리포트: "순위가 내려갔는데
                    # 빨간색이고 올라갔는데 파란색이다 — 색 반대로 해야돼"]
                    # 국내 증시/스포츠 순위 표기 관례(상승=빨강, 하락=파랑)에
                    # 맞춰 반전한다 — 서구 UI 관례(상승=파랑/초록, 하락=빨강)와
                    # 반대라는 점에 주의.
                    if prev_arrow.startswith("▲"):
                        cell.setForeground(QColor("#ff5555"))
                    elif prev_arrow.startswith("▼"):
                        cell.setForeground(QColor("#4da6ff"))
                    else:
                        cell.setForeground(QColor("#888"))
                else:
                    cell.setForeground(Qt.GlobalColor.white if i < 3 else QColor("#ccc"))
                if j == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, id_role_fn(e))
                tbl.setItem(i, j, cell)
        self._show_empty_state(tbl, entries, "아직 계산된 파워랭킹이 없습니다", n_cols)
        self._grow_to_fit(tbl, stretch_col=2)

    def _on_pr_team_row_double_clicked(self, row, _col):
        item = self.pr_team_tbl.item(row, 0)
        team_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        name_item = self.pr_team_tbl.item(row, 2)
        if team_id is None:
            return
        history = pr.get_team_power_history(get_conn(), team_id)
        self._show_power_history_dialog(name_item.text() if name_item else "팀", history,
                                         columns=["연도", "전체 순위", "대륙 순위", "국가 순위"])

    def _on_pr_country_row_double_clicked(self, row, _col):
        item = self.pr_country_tbl.item(row, 0)
        country = item.data(Qt.ItemDataRole.UserRole) if item else None
        name_item = self.pr_country_tbl.item(row, 2)
        if country is None:
            return
        history = pr.get_country_power_history(get_conn(), country)
        self._show_power_history_dialog(name_item.text() if name_item else "국가", history,
                                         columns=["연도", "순위"])

    def _show_power_history_dialog(self, title, history, columns):
        """이전 순위 조회 창 — 신민용 mockup 그대로 "2002 | 5등\n2001 | 9등..."
        형태를 표로 보여준다(최신 연도부터). [2026-08 확장, 신민용 요청:
        "연도, 전체 순위, 대륙 순위 이렇게 3개로 뜨게"] columns가 3개면
        팀용(연도/전체순위/대륙순위), 2개면 국가용(연도/순위) — history의
        각 행 튜플 길이도 그에 맞춰 (연도,전체,대륙) 또는 (연도,순위)로
        들어온다.
        [2026-08 확장, 신민용 요청: "팀 클릭하면 뜨는 전체 순위/대륙 순위에
        국가 순위도 추가해달라"] 팀용 columns가 4개(연도/전체/대륙/국가)로
        늘어났다 — get_team_power_history가 이제 4-튜플을 주므로 이 표는
        columns 길이만 보고 그대로 그려서 별도 분기 없이 자동으로 4열이
        된다."""
        n_cols = len(columns)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{title} — 이전 순위")
        # [2026-08 확장] 팀용 열이 3개→4개로 늘어난 만큼 창 폭도 넓힌다 —
        # 국가용(2열)은 기존 그대로, 팀용은 열 개수에 비례해 계산.
        dlg.resize(280 + 80 * max(n_cols - 2, 0), 360)
        v = QVBoxLayout(dlg)
        tbl = QTableWidget(0, n_cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.setHorizontalHeaderLabels(columns)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.setRowCount(len(history))
        for i, row_vals in enumerate(history):
            year = row_vals[0]
            ranks = row_vals[1:]
            cells = [str(year)] + [f"{r}등" for r in ranks]
            for j, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                it.setForeground(Qt.GlobalColor.white if i == 0 else QColor("#ccc"))
                tbl.setItem(i, j, it)
        self._show_empty_state(tbl, history, "이력이 없습니다", n_cols)
        v.addWidget(tbl)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(dlg.accept)
        v.addWidget(close_btn)
        dlg.exec()


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
        # [2026-08 신설, 신민용 요청: "최다 순위 창은 모니터 한가운데에
        # 뜨게"] _clamp_and_resize는 화면 밖으로 나가지만 않게 보정할 뿐
        # 딱히 중앙에 두진 않는다 — 이 창만 크기를 잡은 직후 화면(작업
        # 영역) 정중앙으로 옮긴다.
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            geo = self.frameGeometry()
            self.move(avail.center().x() - geo.width() // 2,
                      avail.center().y() - geo.height() // 2)

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
    def __init__(self, title, detail, team_based, qualifiers=None, parent=None,
                 highlight_country=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle(title)
        self.setStyleSheet(STYLE)
        _clamp_and_resize(self, 760, 560)
        # [2026-08 신설, 신민용 요청: "국가 검색으로 들어와서 대회 전체
        # 팝업을 열면 지금 보고 있는 국가 이름이 금색으로 표시돼야 한다"]
        # 국가 검색(country_detail_tbl)에서 열었을 때만 채워지고, 월드컵/
        # 네이션스컵 탭 등 다른 진입 경로에서는 None으로 넘어와 아무
        # 효과가 없다.
        self._highlight_country = highlight_country

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
            lay.addWidget(self._build_groups_grid(groups, team_based, detail.get("qualified"),
                                                    highlight_country=self._highlight_country))

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

    def _build_groups_grid(self, groups, team_based, qualified=None, highlight_country=None):
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
                        # [2026-08 신설, 신민용 요청: "국가 검색으로 들어와서
                        # 대회 전체 팝업을 열면 지금 보고 있는 국가 이름이
                        # 금색으로 떠야 한다"] 이름 칸(1번)만 하이라이트
                        # 대상 국가일 때 금색으로 덧칠 — 진출/탈락 배색을
                        # 그대로 보여줘야 하는 나머지 칸(승/무/패/득실/승점)
                        # 은 건드리지 않는다. team_based(팀 대항전)에서는
                        # highlight_country가 팀명이 아니라 국가명이라
                        # 매칭 대상이 다르므로 적용하지 않는다.
                        if not team_based and highlight_country and name == highlight_country:
                            item.setForeground(QColor("#ffcc00"))
                            f = item.font(); f.setBold(True); item.setFont(f)
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
                # [2026-08 버그수정, 신민용 리포트: "승부차기 몇 대 몇으로
                # 이겼는지가 안 보인다 — 경기 일정 화면엔 뜨는데?"] 실제
                # 승부차기 스코어(m["pso_score"], DB엔 이미 저장돼 있고
                # get_intl_tournament_detail 등 백엔드도 이미 이 컬럼을
                # 조회해서 넘겨주고 있었는데, 이 화면만 그 값을 안 쓰고
                # "⚽ 승부차기"라는 고정 문구만 띄우고 있었다 — 실제로
                # 데이터가 없던 게 아니라 여기서 안 읽고 있던 것. 경기
                # 일정 화면(schedule_window.py)과 동일하게 "5-4" 형식
                # 그대로 붙여서 보여준다.
                pso_score = m.get("pso_score") or ""
                pso_txt = f"⚽ 승부차기 {pso_score}" if pso_score else "⚽ 승부차기"
                pso_lbl = QLabel(pso_txt)
                pso_lbl.setStyleSheet("color:#999;font-size:9px;")
                pso_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(pso_lbl, ri, 3)

        lay.addLayout(grid)
        return box