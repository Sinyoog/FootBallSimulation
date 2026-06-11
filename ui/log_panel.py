"""
ui/log_panel.py  ─  우측 로그 패널 (HTML 컬러 로그)
"""
import re
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextEdit
from PyQt6.QtCore import Qt
from game_engine import get_logs

# 로그 줄별 색상 규칙
# (패턴, 색상)
COLOR_RULES = [
    # 부정 (빨간)
    (r"(슬럼프|부상|결렬|강등|🔽|방출|레드카드|결장|최악|부진|실점|실수|미스|실패|오프사이드|턴오버|\-\d+)", "#ff4444"),
    # 긍정 (금색)
    (r"(승격|🔼|수상|입단|⭐|🎖|🌱|클린시트|훌륭|완벽|좋은|성공|골!|멀티골|어시스트|선방|해소|회복|✅)", "#ffcc00"),
    # 경기 헤더 (하늘색)
    (r"^⚽ 경기", "#44ccff"),
    # 경기 결과 승 (파랑)
    (r"\(승\)", "#4488ff"),
    # 경기 결과 패 (빨강)
    (r"\(패\)", "#ff4444"),
    # 경기 결과 무 (회색)
    (r"\(무\)", "#888888"),
    # 월급/자산 (금색)
    (r"💰", "#ffcc00"),
    # 슬럼프/부상 뱃지
    (r"(😰|🚑)", "#ff4444"),
    # 구분선
    (r"^─+$", "#2a2a2a"),
]

# 스탯 변화 색상: "+숫자"=파랑, "-숫자"=빨강
STAT_UP_RE   = re.compile(r'(\+\d+)')
STAT_DOWN_RE = re.compile(r'(?<![0-9])(-\d+)')


def _colorize(line: str) -> str:
    """줄 하나를 HTML span으로 변환"""
    escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 구분선
    if re.match(r'^─+$', line):
        return f'<span style="color:#2a2a2a;">{escaped}</span>'

    # 줄 전체 색 결정
    line_color = "#cccccc"  # 기본
    for pattern, color in COLOR_RULES:
        if re.search(pattern, line):
            line_color = color
            break

    # 스탯 +N/-N 인라인 색상 적용
    def repl_up(m):   return f'<span style="color:#4499ff;">{m.group(1)}</span>'
    def repl_down(m): return f'<span style="color:#ff4444;">{m.group(1)}</span>'

    # 스탯 줄인지 확인 (들여쓰기 + 스탯명 + 숫자)
    is_stat_line = re.match(r'^\s+\S+.*[+-]\d', line)
    if is_stat_line:
        escaped = STAT_UP_RE.sub(repl_up, escaped)
        escaped = STAT_DOWN_RE.sub(repl_down, escaped)

    return f'<span style="color:{line_color};">{escaped}</span>'


class LogPanel(QWidget):
    def __init__(self, main_win=None):
        super().__init__()
        self.main_win = main_win
        lay = QVBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(4)

        t = QLabel("로그")
        t.setStyleSheet("color:#888;font-size:11px;border-bottom:1px solid #2a2a2a;padding-bottom:2px;")
        lay.addWidget(t)

        self.te = QTextEdit()
        self.te.setReadOnly(True)
        self.te.setStyleSheet("""
            QTextEdit {
                background:#1a1a1a; color:#cccccc;
                font-size:12px;
                font-family:'Malgun Gothic','D2Coding',monospace;
                border:none;
                line-height:150%;
            }
            QScrollBar:vertical { background:#1a1a1a; width:6px; }
            QScrollBar::handle:vertical { background:#3a3a3a; border-radius:3px; }
        """)
        lay.addWidget(self.te)

    def refresh(self):
        lines = get_logs()
        html_lines = [_colorize(l) for l in lines]
        html = "<br>".join(html_lines)
        self.te.setHtml(f'<div style="font-family:\'Malgun Gothic\',monospace;font-size:12px;">{html}</div>')
        sb = self.te.verticalScrollBar()
        sb.setValue(sb.maximum())
