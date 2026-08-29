"""
ui/log_panel.py  ─  우측 로그 패널 (HTML 컬러 로그)
"""
import re
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QTextBrowser, QPushButton)
from PyQt6.QtCore import Qt, QUrl
from game_engine import get_logs, get_match_detail

# 로그 줄별 색상 규칙
# (패턴, 색상)
COLOR_RULES = [
    # 부정 (빨간)
    (r"(슬럼프|부상|결렬|강등|🔽|방출|레드카드|퇴장|결장|최악|부진|실점|실수|미스|실패|오프사이드|턴오버|\-\d+)", "#ff4444"),
    # 경고/옐로카드 (노란)
    (r"(🟨|경고누적|경고)", "#ffdd44"),
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

    # 경기 헤더는 클릭 앵커로 (손가락 커서 효과는 QTextBrowser가 처리).
    # [2026-08 신설, 신민용 요청: "경기 글자는 클릭하면 볼 수 있는데
    # 로그에 특별한 표시가 없으니 모를 수 있으니 상자로 감싸서 표시하고
    # 싶다"] 예전엔 밑줄도 없이 문장 끝에 🔎 하나만 붙어서 클릭 가능한
    # 줄이라는 게 잘 안 보였다 — 헤더 전체를 테두리 있는 상자(칩)로
    # 감싸 한눈에 "누르는 곳"으로 보이게 한다.
    if match_id:
        return (f'<a href="match:{match_id}" style="color:{line_color};'
                f'text-decoration:none; border:1px solid #3d7a99; border-radius:4px;'
                f'padding:1px 7px; background-color:#132733;">🔎 {escaped}</a>')

    return f'<span style="color:{line_color};">{escaped}</span>'


# [2026-08 신설, 로그 필터 버튼(전체/핵심/경기) 신설, 신민용 요청: "로그
# 글자 옆에 3개 버튼을 만들고 전체는 지금처럼, 핵심은 휴식·훈련 스탯 +
# 경기 기록만, 경기는 순수하게 경기 기록만"] "경기" 블록(log_type="match")
# 안에는 헤더/결과/평점 말고도 📊 리그 순위, 🪑 벤치대기·🚑 부상결장,
# 다득점 등 하이라이트 배너처럼 같은 log_type을 공유하는 줄이 여럿
# 섞여 있다 — 이것들은 log_type만으로는 못 걸러내서 텍스트 화이트리스트로
# "경기 기록 3줄"(헤더/결과/평점)만 남긴다. 하이라이트 배너는 고정
# 접두사가 없어(예: "🅰🔥 멀티 어시스트!", "극장골!" 등 매번 다름)
# 블랙리스트로는 새로 추가되는 문구를 놓칠 수 있으므로, 반대로 "남길
# 것"만 화이트리스트로 정의해 항상 안전하게 걸러지도록 했다.
_MATCH_RESULT_RE = re.compile(r'\d+\s*-\s*\d+.*\((승|무|패)\)\s*$')


def _is_match_core_line(stripped: str) -> bool:
    """경기 로그 한 줄이 "순수 경기 기록"(헤더/결과/평점)인지 판정."""
    if stripped.startswith("⚽ 경기"):
        return True
    if stripped.startswith("평점"):
        return True
    if _MATCH_RESULT_RE.search(stripped):
        return True
    return False


def _passes_filter(text: str, log_type: str, mode: str) -> bool:
    """mode: "all"(전체) | "core"(핵심) | "match"(경기) | "news"(뉴스).

    log_type은 add_log() 호출부가 이미 붙여서 game_log 테이블에 저장해온
    값(event/injury/match/normal/salary/sep/slump/training)을 그대로
    쓴다 — 신민용이 "쓸데없는 글"로 지목한 주급(salary)/대진·진출 등
    안내(event)/구분선(sep)은 log_type만으로 걸러진다. "휴식에서 얻는
    스탯, 훈련에서 얻는 스탯, 그리고 경기 기록만"이라는 확정에 따라
    injury(부상 발생 알림)·slump(슬럼프)·normal(구단 목표 변경 등)도
    "핵심"/"경기" 어느 쪽에도 포함하지 않는다 — 화이트리스트 방식이라
    이 목록에 없는 log_type이 새로 생겨도 기본적으로 숨겨진다."""
    if mode == "all":
        return True
    if mode == "news":
        return log_type == "news"
    if log_type == "training":
        return mode == "core"
    if log_type == "match":
        return _is_match_core_line(text.strip())
    return False


def _colorize_news(year, text: str) -> str:
    """[2026-08 신설, "뉴스" 필터 전용] 다른 탭과 달리 연도 경계에서
    화면이 안 비워지고 여러 해가 한 화면에 계속 쌓이므로, 각 줄이 몇
    년도 소식인지 앞에 회색으로 붙여준다. _colorize()가 쓰는 정규식
    중 일부(^⚽ 경기, ^─+$)는 줄 맨 앞을 기준으로 매칭하므로, 연도
    표시는 _colorize()가 처리한 결과 바깥에 별도로 붙여서 그 매칭에
    전혀 영향을 안 준다."""
    return f'<span style="color:#666;">[{year}년]</span> ' + _colorize(text)


# 필터 버튼 3종의 공통 스타일 — "로그" 라벨 옆 작은 토글 버튼.
_LOG_MODE_BTN_QSS = """
QPushButton {
    background:#232323; color:#999; border:1px solid #333;
    border-radius:4px; padding:1px 8px; font-size:10px;
}
QPushButton:checked {
    background:#2d4a6b; color:#eee; border:1px solid #4a7ab0;
}
QPushButton:hover:!checked { background:#2a2a2a; }
"""


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
        # [2026-08 신설, 로그 필터 버튼] 지금 화면에 쌓인(=올해 분량)
        # 원본 로그를 (text, log_type) 형태로 따로 캐시해둔다 — 필터
        # 버튼을 눌러 모드를 바꿀 때 DB를 다시 안 읽고 이 캐시만 다시
        # 걸러서 그리기 위함. 매년 초기화되므로(연도 경계에서 리셋)
        # "20년 쌓인 로그" 성능 문제와는 무관하게 항상 최대 1년치만 쌓인다.
        self._year_entries = []  # [(text, log_type), ...]
        self._filter_mode = "all"  # "all" | "core" | "match" | "news"
        # [2026-08 신설, 신민용 요청: "이적 뉴스가 뜨자마자 지워진다 —
        # 뉴스 탭은 연도별로 계속 쌓이고 1년 단위로 안 잘렸으면"] 다른
        # 탭(all/core/match)은 self._year_entries가 매년 통째로 갈아
        # 끼워지지만(위 refresh() 참고), 뉴스는 게임 세션 내내 전부
        # 누적한다 — [(year, text), ...]. log_type="news"인 줄만 담는다
        # (ai_lifecycle.py의 주요 이적/영입·방출/이적시장 마감 로그).
        self._news_entries = []  # [(year, text), ...]

        lay = QVBoxLayout(self); lay.setContentsMargins(8,8,8,8); lay.setSpacing(4)

        header_w = QWidget()
        header_w.setStyleSheet("border-bottom:1px solid #2a2a2a;")
        header_row = QHBoxLayout(header_w)
        header_row.setContentsMargins(0, 0, 0, 4)
        header_row.setSpacing(4)
        t = QLabel("로그")
        t.setStyleSheet("color:#888;font-size:11px;border:none;")
        header_row.addWidget(t)
        header_row.addStretch(1)
        self._mode_buttons = {}
        for mode_key, mode_label in (("all", "전체"), ("core", "핵심"), ("match", "경기"), ("news", "뉴스")):
            btn = QPushButton(mode_label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setFixedHeight(20)
            btn.setStyleSheet(_LOG_MODE_BTN_QSS)
            btn.clicked.connect(lambda _checked, m=mode_key: self._set_filter_mode(m))
            header_row.addWidget(btn)
            self._mode_buttons[mode_key] = btn
        self._mode_buttons["all"].setChecked(True)
        lay.addWidget(header_w)

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

    def _set_filter_mode(self, mode: str):
        """[2026-08 신설, 로그 필터 버튼] 전체/핵심/경기 버튼 클릭 시
        호출. DB를 다시 읽지 않고 self._year_entries 캐시를 새 모드로
        다시 걸러서 통째로 다시 그린다(버튼 클릭 시 1회뿐이라 연차가
        쌓여도 비용은 항상 "올해분"으로 고정 — refresh()의 증분 갱신
        성능 원칙과 무관)."""
        if mode == self._filter_mode:
            return
        self._filter_mode = mode
        self._render_full()

    def _render_full(self):
        """self._year_entries 전체를 현재 필터 모드로 걸러 setHtml로
        다시 그린다 — 연도 경계(새해)나 필터 모드 변경 시 사용.

        [2026-08 신설] "뉴스" 모드는 self._year_entries(매년 갈아 끼워짐)
        대신 self._news_entries(세션 내내 누적, 연도 포함)를 쓰고, 각
        줄 앞에 연도를 붙인다(_colorize_news) — 그 외 모드는 기존과
        동일."""
        if self._filter_mode == "news":
            html_lines = [_colorize_news(year, text) for year, text in self._news_entries]
        else:
            html_lines = [_colorize(text) for text, log_type in self._year_entries
                          if _passes_filter(text, log_type, self._filter_mode)]
        chunk_html = "<br>".join(html_lines)
        self.te.setHtml(f'<div style="font-family:\'Malgun Gothic\',monospace;'
                        f'font-size:12px;">{chunk_html}</div>')
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        # [2026-08 버그수정, 신민용 리포트: "패널을 나갔다 들어오면 로그가
        # 맨 아래가 아니라 위쪽에서 시작한다"] setHtml()/append() 직후
        # 곧바로 verticalScrollBar().setValue(maximum())을 부르면, 그
        # 시점엔 아직 QTextBrowser가 방금 넣은 내용의 레이아웃 계산을
        # 끝내지 않아 maximum()이 옛 값(갱신 전 스크롤 범위)을 돌려주는
        # 경우가 있었다 — QTimer.singleShot(0, …)으로 한 이벤트 루프 턴
        # 뒤로 미루면 그 시점엔 레이아웃이 이미 끝나 있어 maximum()이 정확하다.
        from PyQt6.QtCore import QTimer
        sb = self.te.verticalScrollBar()
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

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

        [2026-08 확장, 로그 필터 버튼(전체/핵심/경기) 신설] get_logs()가
        이제 log_type도 같이 준다. 새로 받아온 줄은 원본 그대로
        self._year_entries에 캐시해두고(필터 모드 전환용), 화면에는
        현재 필터 모드를 통과한 줄만 이어붙인다(_passes_filter)."""
        entries, new_last_id = get_logs(self._last_log_id)
        if not entries:
            return

        # [2026-08 신설] "뉴스"(log_type="news")는 연도 슬라이싱(아래
        # visible_entries) 전에, 이번에 새로 받아온 entries 전체에서
        # 뽑아 self._news_entries에 누적한다 — 슬라이싱 이후 값을 쓰면
        # "지난 연도 마지막 주(52주)에 찍힌 뉴스가, 같은 배치 안에서
        # 새해로 넘어가는 순간 그 지난 연도 몫과 함께 통째로 버려지는"
        # 문제가 그대로 재현된다(이게 바로 "뉴스가 뜨자마자 지워진다"던
        # 원인) — 뉴스만은 연도 경계와 무관하게 항상 전부 챙긴다.
        self._news_entries.extend(
            (year, text) for text, year, log_type in entries if log_type == "news")

        # 이 배치 안에서 마지막으로 연도가 바뀌는 지점을 찾는다.
        start_idx = 0
        year_changed = False
        last_year = self._last_year
        for i, (_text, year, _log_type) in enumerate(entries):
            if last_year is not None and year != last_year:
                start_idx = i
                year_changed = True
            last_year = year
        self._last_year = last_year

        visible_entries = entries[start_idx:]
        if year_changed:
            self._year_entries = [(text, log_type) for text, _year, log_type in visible_entries]
        else:
            self._year_entries.extend((text, log_type) for text, _year, log_type in visible_entries)
        self._last_log_id = new_last_id

        if not self._initialized or year_changed:
            self._render_full()
            self._initialized = True
            return

        # 증분 갱신: 이번에 새로 들어온 줄 중 현재 필터를 통과한 것만
        # append(기존 내용은 다시 그리지 않는다 — 위 refresh() docstring의
        # "다음 날" 성능 원칙 유지).
        # [2026-08 신설] "뉴스" 모드는 연도 표시가 붙은 별도 포맷을 쓴다.
        if self._filter_mode == "news":
            html_lines = [_colorize_news(year, text) for text, year, log_type in visible_entries
                          if log_type == "news"]
        else:
            html_lines = [_colorize(text) for text, _year, log_type in visible_entries
                          if _passes_filter(text, log_type, self._filter_mode)]
        if html_lines:
            chunk_html = "<br>".join(html_lines)
            # QTextBrowser.append()은 기존 내용을 다시 파싱/렌더링하지 않고
            # 끝에 새 블록만 덧붙인다 — 여기가 "증분" 갱신의 핵심.
            self.te.append(chunk_html)
            self._scroll_to_bottom()