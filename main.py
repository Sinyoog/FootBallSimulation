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

    # DB 초기화 (최초 1회 데이터 삽입)
    init_db()

    # [최초 1회 세계 생성 시에만] 진행률 창을 띄운다. 이미 seed된 세이브는
    # seed_initial_data()가 바로 리턴하므로 창이 뜨자마자 사라져 눈에 안 띈다.
    seed_win = _SeedProgressWindow()
    seed_win.show()
    QApplication.processEvents()
    try:
        seed_initial_data(progress_cb=seed_win.report)
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