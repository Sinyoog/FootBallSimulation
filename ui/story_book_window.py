"""
ui/story_book_window.py — 은퇴창의 "📖 스토리 생성" 버튼이 여는 커리어
연대기 낭독 창.

[2026-08 신설, 신민용 리포트: "StoryBookWindow를 못 찾는다"] retire_window.py
_open_story_book()이 처음부터 `from ui.story_book_window import StoryBookWindow`
를 호출하고 있었는데, 실제 이 파일에는 그 클래스가 없고 world_browser_window.py
내용이 통째로(파일명만 다르게, docstring까지) 잘못 복제되어 들어가 있었다 —
즉 이 기능은 처음부터 한 번도 동작한 적이 없었다. world_browser_window.py는
따로 정상 존재하므로, 이 파일을 실제 StoryBookWindow 구현으로 새로 채운다.

story_generator.generate_story()가 반환하는 긴 텍스트(문단은 "\n\n"로 구분)를
받아서, 책처럼 문단 단위로 페이지를 나눠 넘겨볼 수 있는 낭독 창을 띄운다.
어두운 게임 UI 톤(#1e1e1e) 대신 실제 종이책 느낌의 밝은 팔레트를 따로 쓴다 —
장문 텍스트를 다크 테마로 오래 읽으면 눈이 피로하기도 하고, "책"이라는
메타포 자체가 종이 질감과 더 잘 맞는다.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QWidget, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

_BOOK_STYLE = """
QDialog { background:#2a2620; }
#bookPage {
    background:#f6f1e3; border:1px solid #cfc4a3; border-radius:4px;
}
#bookText { color:#2b2418; }
#bookTitle { color:#f0e6c8; font-size:18px; font-weight:bold; }
#bookPageNum { color:#a89a70; font-size:12px; }
#bookNavBtn {
    background:#3a3428; color:#e8dcb8; border:1px solid #5a5238;
    border-radius:6px; padding:8px 16px; font-size:13px; font-weight:bold;
}
#bookNavBtn:hover { background:#4a4436; }
#bookNavBtn:disabled { background:#2a2620; color:#5a5240; }
#bookCloseBtn {
    background:#5a2828; color:#f0e0e0; border:none; border-radius:6px;
    padding:7px 14px; font-size:12px;
}
#bookCloseBtn:hover { background:#7a3838; }
#bookSaveBtn {
    background:#2a4a2a; color:#e0f0e0; border:none; border-radius:6px;
    padding:7px 14px; font-size:12px;
}
#bookSaveBtn:hover { background:#3a6a3a; }
"""


class StoryBookWindow(QDialog):
    """커리어 연대기를 책처럼 페이지 넘겨가며 보여주는 창.

    Args:
        player_name: 선수 이름 (창 제목/표지에 표시)
        story_text : story_generator.generate_story()가 반환한 전체 텍스트.
                     문단은 "\n\n"로 구분되어 있다고 가정.
        parent     : 부모 위젯 (retire_window의 self)
    """

    # 한 페이지에 넣을 문단 수 — 너무 많으면 스크롤이 길어져 "책장 넘기기"
    # 느낌이 사라지고, 너무 적으면 페이지 수만 늘어나 번거로워진다.
    PARAGRAPHS_PER_PAGE = 4

    def __init__(self, player_name: str, story_text: str, parent=None):
        super().__init__(parent)
        self._player_name = player_name or "선수"
        self._story_text = story_text or ""
        self.setWindowTitle(f"📖 {self._player_name}의 이야기")
        self.setMinimumSize(720, 640)
        self.setStyleSheet(_BOOK_STYLE)

        self._pages = self._paginate(self._story_text)
        self._page_idx = 0

        self._build()
        self._render_page()

    # ── 페이지 나누기 ──────────────────────────────
    def _paginate(self, text: str):
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return ["기록된 이야기가 없습니다."]
        pages = []
        step = self.PARAGRAPHS_PER_PAGE
        for i in range(0, len(paragraphs), step):
            pages.append(paragraphs[i:i + step])
        return pages

    # ── UI 구성 ────────────────────────────────────
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        hdr_row = QHBoxLayout()
        title = QLabel(f"📖 {self._player_name}의 이야기")
        title.setObjectName("bookTitle")
        hdr_row.addWidget(title)
        hdr_row.addStretch()
        save_btn = QPushButton("💾 텍스트로 저장")
        save_btn.setObjectName("bookSaveBtn")
        save_btn.clicked.connect(self._save_to_file)
        hdr_row.addWidget(save_btn)
        close_btn = QPushButton("✕ 닫기")
        close_btn.setObjectName("bookCloseBtn")
        close_btn.clicked.connect(self.close)
        hdr_row.addWidget(close_btn)
        lay.addLayout(hdr_row)

        # 페이지(종이 질감 카드) — 스크롤 가능(문단이 길면 페이지 안에서도 스크롤)
        self._page_frame = QFrame()
        self._page_frame.setObjectName("bookPage")
        page_lay = QVBoxLayout(self._page_frame)
        page_lay.setContentsMargins(28, 24, 28, 24)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._text_label = QLabel()
        self._text_label.setObjectName("bookText")
        self._text_label.setWordWrap(True)
        self._text_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        font = QFont("Georgia" if _font_available("Georgia") else "Serif")
        font.setPointSize(12)
        self._text_label.setFont(font)
        self._text_label.setStyleSheet("background: transparent;")
        scroll.setWidget(self._text_label)
        page_lay.addWidget(scroll)
        lay.addWidget(self._page_frame, 1)

        # 페이지 넘김 컨트롤
        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self._prev_btn = QPushButton("◀ 이전 페이지")
        self._prev_btn.setObjectName("bookNavBtn")
        self._prev_btn.clicked.connect(self._go_prev)
        self._page_num_lbl = QLabel("")
        self._page_num_lbl.setObjectName("bookPageNum")
        self._page_num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._next_btn = QPushButton("다음 페이지 ▶")
        self._next_btn.setObjectName("bookNavBtn")
        self._next_btn.clicked.connect(self._go_next)
        nav_row.addWidget(self._prev_btn, 1)
        nav_row.addWidget(self._page_num_lbl, 1)
        nav_row.addWidget(self._next_btn, 1)
        lay.addLayout(nav_row)

    # ── 페이지 렌더링/이동 ─────────────────────────
    def _render_page(self):
        paragraphs = self._pages[self._page_idx]
        html = "".join(f"<p style='margin:0 0 14px 0; line-height:1.6;'>{p}</p>"
                        for p in paragraphs)
        self._text_label.setText(html)
        self._page_num_lbl.setText(f"{self._page_idx + 1} / {len(self._pages)}")
        self._prev_btn.setEnabled(self._page_idx > 0)
        self._next_btn.setEnabled(self._page_idx < len(self._pages) - 1)

    def _go_prev(self):
        if self._page_idx > 0:
            self._page_idx -= 1
            self._render_page()

    def _go_next(self):
        if self._page_idx < len(self._pages) - 1:
            self._page_idx += 1
            self._render_page()

    def keyPressEvent(self, event):
        # 좌우 화살표로도 페이지 넘김 (책 읽기 경험 개선)
        if event.key() == Qt.Key.Key_Left:
            self._go_prev()
        elif event.key() == Qt.Key.Key_Right:
            self._go_next()
        else:
            super().keyPressEvent(event)

    # ── 저장 ───────────────────────────────────────
    def _save_to_file(self):
        default_name = f"{self._player_name}_커리어_이야기.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "이야기 저장", default_name, "텍스트 파일 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._story_text)
            QMessageBox.information(self, "저장 완료", f"저장했습니다:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "저장 실패", f"저장 중 오류가 발생했습니다:\n{e}")


def _font_available(name: str) -> bool:
    try:
        from PyQt6.QtGui import QFontDatabase
        return name in QFontDatabase.families()
    except Exception:
        return False