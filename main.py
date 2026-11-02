"""
main.py - 진입점
"""
import sys
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QProgressBar, QPushButton)
from PyQt6.QtCore import Qt
from database import init_db, seed_initial_data, sync_countries, get_conn, flush_to_disk
from ui.start_screen import StartScreen


# start_screen.py의 DARK_STYLE과 톤을 맞춘 진행률 창 전용 스타일.
_SEED_STYLE = """
QWidget { background-color: #1a1a1a; color: #e0e0e0;
          font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; }
QLabel  { color: #e0e0e0; }
QProgressBar {
    background-color: #2a2a2a; border: 1px solid #444; border-radius: 6px;
    height: 18px; text-align: center; color: #e0e0e0; font-size: 11px;
}
QProgressBar::chunk { background-color: #2a8a2a; border-radius: 5px; }
QPushButton#danger {
    background-color: #6a1a1a; color: white; border: none;
    border-radius: 6px; padding: 8px 18px; font-size: 13px; font-weight: bold;
}
QPushButton#danger:hover { background-color: #8a2a2a; }
"""


class _TableClickToggle:
    """[2026-10 신설, 신민용 요청: "그리드 상자(표 칸) 클릭하면 빛나는데, 같은 곳을 한 번 더
    누르면 꺼지게 — 경기 일정·순위표·세계 축구 기록실 전부"] 앱 전체 표(QTableView 계열 —
    QTableWidget 포함)에 한 번에 적용하는 이벤트 필터. 표마다 고치지 않는다.
    이미 선택돼 있던 칸을 왼쪽 버튼으로 눌렀다 같은 칸에서 떼면, 기존 클릭 처리(cellClicked 등)가
    다 끝난 다음 그 표의 선택과 현재 칸 표시를 지운다. 이벤트는 가로채지 않으므로 칸 클릭으로
    상세 창을 여는 기존 동작은 그대로다. Ctrl/Shift 다중 선택, 드래그, 오른쪽 클릭, 더블클릭의
    두 번째 누름(Qt가 MouseButtonDblClick으로 따로 보냄)에는 관여하지 않는다."""

    def __init__(self):
        from PyQt6.QtCore import QObject, QEvent

        outer = self

        class _F(QObject):
            def eventFilter(self, obj, ev):
                t = ev.type()
                if t == QEvent.Type.MouseButtonPress or t == QEvent.Type.MouseButtonRelease:
                    try:
                        outer._on_mouse(obj, ev, t == QEvent.Type.MouseButtonPress)
                    except Exception:
                        outer._pending = None   # 표시 편의 기능 — 어떤 경우에도 클릭 자체를 막지 않는다
                return False

        self._pending = None
        self.filter = _F()

    def _on_mouse(self, obj, ev, is_press):
        from PyQt6.QtWidgets import QTableView
        from PyQt6.QtCore import Qt, QTimer
        view = obj.parent() if obj is not None else None
        if not isinstance(view, QTableView) or obj is not view.viewport():
            return
        if (ev.button() != Qt.MouseButton.LeftButton or
                ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)):
            self._pending = None
            return
        sm = view.selectionModel()
        idx = view.indexAt(ev.position().toPoint())
        if is_press:
            # 누르기 직전(Qt가 선택을 바꾸기 전)에 "선택된 게 이 칸(행 선택 표면 이 행) 하나뿐"이었는지
            # 기억한다 — 여러 칸을 골라 둔 상태에서 그중 하나를 누르면 Qt 기본대로 그 칸만 남긴다.
            only_this = False
            if sm is not None and idx.isValid() and sm.isSelected(idx):
                sel = sm.selectedIndexes()
                if view.selectionBehavior() == QTableView.SelectionBehavior.SelectRows:
                    only_this = {i.row() for i in sel} == {idx.row()}
                else:
                    only_this = len(sel) == 1
            self._pending = (view, idx.row(), idx.column()) if only_this else None
            return
        pend, self._pending = self._pending, None
        if pend is None or not idx.isValid() or pend != (view, idx.row(), idx.column()):
            return

        def _clear():
            try:
                m = view.selectionModel()
                if m is not None:
                    m.clearSelection()
                    m.clearCurrentIndex()
            except RuntimeError:
                pass   # 그 사이 창이 닫혀 위젯이 사라진 경우
        QTimer.singleShot(0, _clear)


class SeedCancelled(Exception):
    """진행률 창에서 취소를 눌렀을 때 세계 생성 루프를 중단시키기 위한 신호."""
    pass


class _SeedProgressWindow(QWidget):
    """[2026-07 추가] 최초 1회(새 game.db) 실행 시 전세계 리그·팀·선수단을
    통째로 생성하는 seed_initial_data()가 도는 동안 보여줄 진행률 창.

    이전엔 콘솔에 "초기 데이터 삽입 중..." → "완료"만 찍혔는데, --windowed로
    빌드한 배포판은 콘솔 자체가 안 보여서 사용자 입장에선 창도 안 뜨고
    그냥 몇 초~몇십 초씩 멈춘 것처럼 보였다. 실제 진행 상황(어느 단계인지,
    몇 팀 중 몇 팀째인지)을 그대로 화면에 보여주고, 취소도 가능하게 한다.

    [취소 처리] 취소를 누르면 report() 콜백이 SeedCancelled를 던져서 생성
    루프를 즉시 중단시킨다. seed_initial_data()는 맨 마지막에야 commit()을
    부르므로, 그 전에 예외로 빠져나오면 지금까지의 INSERT는 아직 커밋 전
    트랜잭션 안에 있다 — main()에서 conn.rollback()으로 통째로 버리면
    DB엔 아무 흔적도 안 남고, meta.seeded도 안 찍히므로 다음 실행 시
    seed_initial_data()가 처음부터 다시 돈다.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("게임 세계 생성 중...")
        self.setFixedSize(440, 160)
        self.setStyleSheet(_SEED_STYLE)
        self.cancelled = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        title = QLabel("⚽ 게임 세계를 만들고 있습니다")
        title.setStyleSheet("font-size:15px; font-weight:bold;")
        lay.addWidget(title)

        self._stage_lbl = QLabel("준비 중...")
        self._stage_lbl.setStyleSheet("font-size:13px;")
        lay.addWidget(self._stage_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        lay.addWidget(self._bar)

        self._detail_lbl = QLabel("")
        self._detail_lbl.setStyleSheet("font-size:11px; color:#888;")
        lay.addWidget(self._detail_lbl)

        lay.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("취소")
        self._cancel_btn.setMinimumSize(90, 34)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # objectName 셀렉터 상속에 기대지 않고 버튼 자체에 직접 스타일을 걸어
        # 확실히 흰 글씨가 보이게 한다.
        self._cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #a02020; color: white; border: none;
                border-radius: 6px; padding: 8px 18px;
                font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: #c03030; }
        """)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._cancel_btn)
        lay.addLayout(btn_row)

    def _on_cancel_clicked(self):
        self.cancelled = True

    def closeEvent(self, event):
        # X 버튼으로 닫아도 확인 없이 바로 취소 처리.
        self.cancelled = True
        event.accept()

    def report(self, stage, done, total, detail):
        if self.cancelled:
            raise SeedCancelled()
        self._stage_lbl.setText(f"{stage} ({done}/{total})")
        self._bar.setValue(int(done / total * 100) if total else 0)
        self._detail_lbl.setText(detail or "")
        QApplication.processEvents()
        if self.cancelled:   # 콜백 중 processEvents()로 취소 버튼이 눌렸을 수도 있음
            raise SeedCancelled()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 표 칸 다시 누르면 선택 해제(_TableClickToggle 주석 참고). app에 붙여 두어 수명을 같이 한다.
    app._table_click_toggle = _TableClickToggle()
    app.installEventFilter(app._table_click_toggle.filter)

    # DB 초기화 (최초 1회 데이터 삽입)
    init_db()

    # [최초 1회 세계 생성 시에만] 진행률 창을 띄운다. 이미 seed된 세이브는
    # seed_initial_data()가 바로 리턴하므로 창이 뜨자마자 사라져 눈에 안 띈다.
    seed_win = _SeedProgressWindow()
    seed_win.show()
    QApplication.processEvents()
    try:
        seed_initial_data(progress_cb=seed_win.report, skip_ai_regen=True)
    except SeedCancelled:
        # 아직 commit() 전이므로 지금까지의 INSERT를 통째로 되돌린다.
        # → DB엔 아무것도 안 남고, 다음 실행 시 처음부터 다시 생성된다.
        get_conn().rollback()
        seed_win.close()
        sys.exit(0)
    seed_win.close()

    # [버그수정 2026-07] 인메모리 모드에서는 flush_to_disk()가 게임 진행 중
    # (advance_days 등)에만 조건부로 호출됐다 — 그래서 최초 실행으로 전
    # 세계 데이터를 생성한 직후, 새 게임을 시작하지 않고 바로 앱을 종료하면
    # game.db에 아무것도 저장되지 않았다. 다음 실행 시 meta.seeded 플래그를
    # 못 찾아 "초기 데이터 삽입 중..."이 처음부터 반복되는 원인이었다.
    # 세계 생성 직후 여기서 1회 명시적으로 디스크에 백업해 이 문제를 없앤다.
    flush_to_disk()

    sync_countries()   # COUNTRY_DATA 변경분 기존 세이브에 반영

    win = StartScreen()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()