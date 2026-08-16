"""
ui/log_panel.py  ─  우측 로그 패널 (HTML 컬러 로그)
"""
import re
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTextBrowser
from PyQt6.QtCore import Qt, QUrl
from game_engine import get_logs, get_match_detail

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
    # 주급/자산 (금색)
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
    # 경기 헤더의 [match:{id}] 마커 → 클릭 앵커로 분리 추출.
    #   마커는 표시에서 제거하고, 헤더 전체를 <a>로 감싸 클릭 가능하게 만든다.
    m_match = re.search(r'\[match:(\d+)\]', line)
    match_id = m_match.group(1) if m_match else None
    if m_match:
        line = line.replace(m_match.group(0), "").rstrip()

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

    is_stat_line = re.match(r'^\s+\S+.*[+-]\d', line)
    if is_stat_line:
        escaped = STAT_UP_RE.sub(repl_up, escaped)
        escaped = STAT_DOWN_RE.sub(repl_down, escaped)

    # 경기 헤더는 클릭 앵커로 (밑줄 + 손가락 커서 효과는 QTextBrowser가 처리)
    if match_id:
        return (f'<a href="match:{match_id}" style="color:{line_color};'
                f'text-decoration:none;">{escaped}  🔎</a>')

    return f'<span style="color:{line_color};">{escaped}</span>'


class LogPanel(QWidget):
    def __init__(self, main_win=None):
        super().__init__()
        self.main_win = main_win
        # [2026-07 성능 수정] 아래 refresh() 참고 — 마지막으로 읽은 로그
        # id를 기억해서 다음부터는 새로 생긴 줄만 가져오기 위한 상태.
        # 이 위젯은 새 게임/이어하기(_rebuild_main)마다 새로 만들어지므로
        # (main_window.py에서 매번 LogPanel(self)로 재생성) 0으로 시작해도
        # 이전 세이브의 로그 id와 섞일 걱정이 없다.
        self._last_log_id = 0
        self._initialized = False
        # [2026-08 신설, 신민용 요청: "로그를 1년 단위로, 새해 시작하면
        # 깨끗해지고 다시 쌓이게"] 지금 화면에 쌓여 있는 내용이 어느
        # 연도(year) 소속인지 기억한다 — refresh()에서 새로 들어온 줄의
        # year가 이거랑 다르면 그 지점에서 화면을 비우고 새로 시작한다.
        self._last_year = None
        lay = QVBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(4)

        t = QLabel("로그")
        t.setStyleSheet("color:#888;font-size:11px;border-bottom:1px solid #2a2a2a;padding-bottom:2px;")
        lay.addWidget(t)

        self.te = QTextBrowser()
        self.te.setReadOnly(True)
        self.te.setOpenLinks(False)              # 링크 클릭을 직접 처리(외부 브라우저 X)
        self.te.setOpenExternalLinks(False)
        self.te.anchorClicked.connect(self._on_anchor)
        self.te.setStyleSheet("""
            QTextBrowser {
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

    def _on_anchor(self, url: QUrl):
        """경기 헤더 클릭 → 상세 다이얼로그."""
        s = url.toString()
        if not s.startswith("match:"):
            return
        try:
            mid = int(s.split(":", 1)[1])
        except (ValueError, IndexError):
            return
        data = get_match_detail(mid)
        if not data:
            return
        from ui.match_detail_dialog import MatchDetailDialog
        dlg = MatchDetailDialog(data, self)
        dlg.exec()

    def refresh(self):
        """[2026-07 성능 수정, 신민용 리포트: "20년 쌓였을 때랑 방금
        시작했을 때랑 next day 속도가 같아야 하는데 다른 것 같다"]

        예전엔 매번 get_logs()로 game_log 테이블 전체(계속 쌓이기만
        하는 테이블)를 처음부터 다시 읽고, 그 전부를 다시 색칠해서
        setHtml()로 통째로 다시 그렸다 — 즉 "다음 날" 한 번의 비용이
        지금까지 쌓인 전체 로그 양에 비례해서 계속 커졌다(플레이 연차가
        쌓일수록 매일 느려짐).

        이제 마지막으로 읽은 로그 id(self._last_log_id) 이후에 새로
        생긴 줄만 가져와서 기존 내용 뒤에 append한다 — 하루치 새로
        생기는 로그 줄 수는 연차와 무관하게 거의 일정하므로, "다음 날"
        1회당 처리 비용도 항상 일정해진다. 최초 1회(패널이 막 만들어져
        내용이 하나도 없을 때)만 그때까지의 전체 로그를 한 번에 채운다.

        [2026-08 확장, 신민용 요청: "로그를 1년 단위로 보이게, 새해
        시작하면 깨끗해지고 다시 쌓이는 걸로"] get_logs()가 이제 각 줄의
        year도 같이 준다. 이번에 새로 받아온 줄들 중 마지막으로 연도가
        바뀌는 지점을 찾아, 그 지점 이전(지난 연도) 줄들은 애초에
        색칠(_colorize)조차 하지 않고 버린다 — 화면엔 어차피 최신 연도
        분량만 남기면 되므로, 지난 연도 몫을 굳이 그렸다 지울 필요가
        없다. 1년 넘기기처럼 한 번에 여러 주(심하면 여러 해)치 로그가
        몰려 들어와도 이 처리는 그대로 안전하다 — 배치 안에서 연도가
        몇 번을 바뀌든 마지막 전환 지점 하나만 찾으면 되기 때문.

        [2026-08 버그수정, 신민용 리포트: "패널을 나갔다 들어오면 로그가
        맨 아래가 아니라 위쪽에서 시작한다"] setHtml()/append() 직후
        곧바로 verticalScrollBar().setValue(maximum())을 부르면, 그
        시점엔 아직 QTextBrowser가 방금 넣은 내용의 레이아웃 계산을
        끝내지 않아 maximum()이 옛 값(갱신 전 스크롤 범위)을 돌려주는
        경우가 있었다 — 그 결과 "맨 아래로 보냈다고 생각했지만 실제로는
        새로 늘어난 부분만큼 못 미친 위치"에 멈췄다. QTimer.singleShot(0, …)
        으로 한 이벤트 루프 턴 뒤로 미루면 그 시점엔 레이아웃이 이미
        끝나 있어 maximum()이 정확하다."""
        entries, new_last_id = get_logs(self._last_log_id)
        if not entries:
            return

        # 이 배치 안에서 마지막으로 연도가 바뀌는 지점을 찾는다.
        start_idx = 0
        year_changed = False
        last_year = self._last_year
        for i, (_text, year) in enumerate(entries):
            if last_year is not None and year != last_year:
                start_idx = i
                year_changed = True
            last_year = year
        self._last_year = last_year

        visible_entries = entries[start_idx:]
        html_lines = [_colorize(text) for text, _year in visible_entries]
        chunk_html = "<br>".join(html_lines)
        if not self._initialized or year_changed:
            self.te.setHtml(f'<div style="font-family:\'Malgun Gothic\',monospace;'
                            f'font-size:12px;">{chunk_html}</div>')
            self._initialized = True
        else:
            # QTextBrowser.append()은 기존 내용을 다시 파싱/렌더링하지 않고
            # 끝에 새 블록만 덧붙인다 — 여기가 "증분" 갱신의 핵심.
            self.te.append(chunk_html)
        self._last_log_id = new_last_id

        from PyQt6.QtCore import QTimer
        sb = self.te.verticalScrollBar()
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))