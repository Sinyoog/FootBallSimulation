# -*- coding: utf-8 -*-
"""
ui/story_book_window.py — AI 스토리 생성 결과를 '책을 펼친 모습'으로 보여주는 창.

[2026-07 신설, 신민용 요청] story_generator.py가 만든 장문 연대기 텍스트를
좌/우 두 페이지로 나눠서, 실제 책을 넘기듯 페이지 단위로 보여준다.

페이지 분할은 직접 글자 수를 세어 대충 자르는 방식이 아니라, Qt의
QTextDocument가 가진 실제 페이지 레이아웃 기능(setPageSize)을 그대로
활용한다 — 문서를 한 번만 만들어서 페이지 크기를 지정해주면 Qt가 문단/
줄바꿈을 실제로 계산해서 몇 페이지가 되는지, 각 페이지에 어떤 내용이
들어가는지 알아서 배치해준다. 그 결과를 페이지별로 다시 그려주기만 하면
된다.
"""

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGraphicsDropShadowEffect, QApplication
)
from PyQt6.QtGui import QTextDocument, QPainter, QColor, QFont, QAbstractTextDocumentLayout
from PyQt6.QtCore import Qt, QSizeF, QRectF, QTimer
import re

PAGE_W = 460
PAGE_H = 620
PAGE_MARGIN = 34


_YEAR_RE = re.compile(r'(\d{4}년)')


def _bold_first_year(line: str) -> str:
    """[2026-07 신설, 신민용 요청: "연도로 시작하는 부분은 굵게 표시해서
    알아보기 쉽게"] 문단 안에서 처음 나오는 'YYYY년' 패턴 하나만 굵게
    표시한다. 문단이 항상 연도로 '시작'하는 건 아니라서(팀명이나 묘사로
    시작하는 문단도 많음) 위치와 무관하게 그 문단을 대표하는 첫 연도를
    찾아 감싼다 — 두 번째 이후 언급(예: 챕터 콜백의 "2005년까지")은
    건드리지 않는다."""
    m = _YEAR_RE.search(line)
    if not m:
        return line
    start, end = m.span()
    return line[:start] + f"<b>{m.group(1)}</b>" + line[end:]


def story_text_to_html(story_text: str) -> str:
    """generate_story()가 반환한 평문 텍스트를 챕터 제목/문단이 구분되는
    HTML로 변환한다. "\\n\\n"으로 나뉜 블록 단위로 처리 — 챕터 헤더처럼
    보이는 한 줄짜리 블록("1부 — ...", "에필로그")은 <h2>로, 나머지는
    각 줄을 <p>로 감싼다."""
    blocks = [b for b in story_text.split("\n\n") if b.strip()]
    parts = []
    for b in blocks:
        lines = [l for l in b.split("\n") if l.strip()]
        is_header = (len(lines) == 1 and
                     (lines[0] in ("에필로그", "그는 어떤 선수였는가") or
                      any(lines[0].startswith(f"{n}부") for n in range(1, 11)) or
                      "부 —" in lines[0]))
        if is_header:
            parts.append(f'<h2 class="chapter">{lines[0]}</h2>')
        else:
            for l in lines:
                parts.append(f'<p>{_bold_first_year(l)}</p>')
    body = "\n".join(parts)
    return f"""
    <html><head><style>
        body {{ font-family: "Noto Serif KR", "Batang", Georgia, serif;
                color: #2a2118; font-size: 14px; line-height: 1.75; }}
        h2.chapter {{ font-size: 17px; color: #6b3f1d; margin-top: 22px;
                      margin-bottom: 10px; border-bottom: 1px solid #c9b98f;
                      padding-bottom: 6px; }}
        p {{ margin: 0 0 12px 0; text-indent: 1em; text-align: justify; }}
        p b {{ color: #6b3f1d; }}
    </style></head><body>{body}</body></html>
    """


class BookPageWidget(QWidget):
    """공유된 QTextDocument 중 한 페이지(page_index)만 그려서 보여주는
    위젯. 실제 텍스트 레이아웃은 document가 이미 다 계산해뒀으므로,
    여기서는 해당 페이지 영역만큼 painter를 이동/클리핑해서 그리기만
    한다 — 실제 인쇄 미리보기와 같은 원리."""

    def __init__(self, document: QTextDocument, parent=None):
        super().__init__(parent)
        self.document = document
        self.page_index = 0
        self.setFixedSize(PAGE_W, PAGE_H)
        self.setAutoFillBackground(False)

    def set_page(self, index: int):
        self.page_index = index
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # 종이 느낌 배경 (은은한 세로 그라데이션)
        painter.fillRect(self.rect(), QColor("#f8f2e4"))
        painter.setPen(QColor("#e4d8bc"))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        content_h = PAGE_H - 2 * PAGE_MARGIN
        content_w = PAGE_W - 2 * PAGE_MARGIN

        if self.document.pageCount() <= self.page_index:
            painter.end()
            return

        painter.save()
        painter.translate(PAGE_MARGIN, PAGE_MARGIN)
        painter.translate(0, -self.page_index * content_h)
        painter.setClipRect(QRectF(0, self.page_index * content_h, content_w, content_h))

        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(ctx.palette.ColorRole.Text, QColor("#2a2118"))
        self.document.documentLayout().draw(painter, ctx)
        painter.restore()
        painter.end()


class StoryBookWindow(QDialog):
    """좌/우 두 페이지가 펼쳐진 책 모양으로 스토리를 보여주는 창."""

    def __init__(self, player_name: str, story_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"📖 {player_name}의 연대기")
        self.setStyleSheet("QDialog { background:#141414; }")
        self._spread = 0  # 0 = 1,2페이지 / 2 = 3,4페이지 ...
        self.story_text = story_text  # [2026-07 신설] 복사 버튼에서 원본 평문을 그대로 쓴다

        self.document = QTextDocument()
        content_w = PAGE_W - 2 * PAGE_MARGIN
        content_h = PAGE_H - 2 * PAGE_MARGIN
        self.document.setHtml(story_text_to_html(story_text))
        self.document.setTextWidth(content_w)
        self.document.setPageSize(QSizeF(content_w, content_h))

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel(f"✨ {player_name}의 축구 인생")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color:#e8d9b0; font-size:20px; font-weight:bold;")
        root.addWidget(title)

        # ── 책 몸체 (좌/우 페이지 + 가운데 책등) ──────────────
        book_frame = QFrame()
        book_frame.setStyleSheet("background:#0f0f0f; border-radius:10px;")
        shadow = QGraphicsDropShadowEffect(blurRadius=40, xOffset=0, yOffset=8)
        shadow.setColor(QColor(0, 0, 0, 180))
        book_frame.setGraphicsEffect(shadow)
        book_row = QHBoxLayout(book_frame)
        book_row.setContentsMargins(18, 18, 18, 18)
        book_row.setSpacing(0)

        self.left_page = BookPageWidget(self.document)
        spine = QFrame(); spine.setFixedWidth(10)
        spine.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #00000000, stop:0.5 #00000090, stop:1 #00000000);")
        self.right_page = BookPageWidget(self.document)

        book_row.addWidget(self.left_page)
        book_row.addWidget(spine)
        book_row.addWidget(self.right_page)
        root.addWidget(book_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        # ── 하단 내비게이션 ─────────────────────────────
        nav = QHBoxLayout()
        nav.setSpacing(16)
        self.prev_btn = QPushButton("◀ 이전")
        self.next_btn = QPushButton("다음 ▶")
        self.page_lbl = QLabel("")
        for b in (self.prev_btn, self.next_btn):
            b.setStyleSheet(
                "QPushButton { background:#2a2a2a; color:#e8d9b0; border:none;"
                " border-radius:6px; padding:8px 22px; font-size:13px; font-weight:bold; }"
                "QPushButton:hover { background:#3a3a3a; }"
                "QPushButton:disabled { background:#1c1c1c; color:#555; }")
        self.page_lbl.setStyleSheet("color:#aaa; font-size:12px;")
        self.page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        close_btn = QPushButton("닫기")
        close_btn.setStyleSheet(
            "QPushButton { background:#402020; color:#eee; border:none;"
            " border-radius:6px; padding:8px 18px; font-size:13px; }"
            "QPushButton:hover { background:#552828; }")
        close_btn.clicked.connect(self.close)

        # [2026-07 신설, 신민용 요청] 스토리 전체를 클립보드로 복사하는 버튼.
        # HTML(챕터 제목 태그 등)이 아니라 generate_story()가 만든 원본
        # 평문(self.story_text)을 그대로 복사한다 — 다른 곳(메모장, 커뮤니티
        # 글쓰기창 등)에 붙여넣었을 때 태그가 섞여 나오지 않게.
        self.copy_btn = QPushButton("📋 복사")
        self.copy_btn.setStyleSheet(
            "QPushButton { background:#2a2a2a; color:#e8d9b0; border:none;"
            " border-radius:6px; padding:8px 18px; font-size:13px; font-weight:bold; }"
            "QPushButton:hover { background:#3a3a3a; }")
        self.copy_btn.clicked.connect(self._copy_story)

        self.prev_btn.clicked.connect(self._go_prev)
        self.next_btn.clicked.connect(self._go_next)

        nav.addStretch()
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_lbl)
        nav.addWidget(self.next_btn)
        nav.addStretch()
        nav.addWidget(self.copy_btn)
        nav.addWidget(close_btn)
        root.addLayout(nav)

        self._refresh()
        self.setFixedSize(PAGE_W * 2 + 90, PAGE_H + 150)

    def _total_pages(self):
        return max(1, self.document.pageCount())

    def _refresh(self):
        total = self._total_pages()
        self.left_page.set_page(self._spread)
        self.right_page.set_page(self._spread + 1)
        left_no = self._spread + 1
        right_no = min(self._spread + 2, total)
        self.page_lbl.setText(f"{left_no}-{right_no} / {total}")
        self.prev_btn.setEnabled(self._spread > 0)
        self.next_btn.setEnabled(self._spread + 2 < total)

    def _go_prev(self):
        if self._spread > 0:
            self._spread -= 2
            self._refresh()

    def _go_next(self):
        if self._spread + 2 < self._total_pages():
            self._spread += 2
            self._refresh()

    def _copy_story(self):
        """[2026-07 신설] 스토리 전체 텍스트를 클립보드에 복사하고,
        버튼 텍스트를 잠깐 "복사됨!"으로 바꿔서 피드백을 준다(팝업 없이
        가볍게 — 책 읽는 흐름을 방해하지 않도록)."""
        QApplication.clipboard().setText(self.story_text)
        self.copy_btn.setText("✅ 복사됨!")
        self.copy_btn.setEnabled(False)
        QTimer.singleShot(1200, self._reset_copy_btn)

    def _reset_copy_btn(self):
        self.copy_btn.setText("📋 복사")
        self.copy_btn.setEnabled(True)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self._go_prev()
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self._go_next()
        elif event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)