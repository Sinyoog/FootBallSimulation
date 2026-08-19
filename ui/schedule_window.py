"""
ui/schedule_window.py  ─  모달리스, 실시간 갱신
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QTabWidget, QWidget, QMenu
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut

from game_engine import get_schedule, get_player, get_state
from database import get_conn

# [2026-08 신설, 신민용 리포트: "경기 일정에서 팀명 복사하면 국기/국가까지
# 같이 복사된다"] ui/world_browser_window.py의 _CLEAN_TEXT_ROLE +
# _enable_plain_copy와 완전히 동일한 패턴 — 화면엔 국기/팀명/국가를 같이
# 보여주되, 복사는 팀명(또는 국가명)만 되게 한다. 두 파일이 서로 import하는
# 관계가 아니라(순환 참조 방지) 여기 schedule_window.py에도 동일하게
# 복제해서 둔다.
_CLEAN_TEXT_ROLE = Qt.ItemDataRole.UserRole + 50


def _enable_plain_copy(tbl):
    """이 테이블 셀을 우클릭(복사) 또는 Ctrl+C 하면, 화면에 보이는
    장식(국기·국가)이 아니라 _CLEAN_TEXT_ROLE에 저장해둔 순수 이름만
    클립보드에 복사한다. 그 롤이 없는 셀은 item.text()를 그대로 쓴다."""
    def _clean_text_of(item):
        if item is None:
            return ""
        v = item.data(_CLEAN_TEXT_ROLE)
        return v if v else item.text()

    def _copy_selected():
        items = tbl.selectedItems()
        if not items:
            return
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

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QTabWidget::pane { border:1px solid #333; background:#1e1e1e; }
QTabBar::tab { background:#252525; color:#888; padding:6px 16px; }
QTabBar::tab:selected { background:#1e1e1e; color:#00cc44; border-bottom:2px solid #00cc44; }
QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a; border:none; font-size:12px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:4px; }
"""

# ── 색상 상수 ──────────────────────────────────────────────────
COLOR_ADVANCE   = QColor("#00cc44")   # 진출 확정 (초록) — 국제대회/챔스 통일
COLOR_MY        = QColor("#66ccff")   # 내 팀/내 국가 (청록)
COLOR_THIRD     = QColor("#ffaa00")   # 3위 진출권 경쟁 중 (주황) — 아직 미확정
COLOR_THIRD_OK  = QColor("#88dd44")   # 3위 중 진출 확정 (연두)
COLOR_ELIM      = QColor("#888888")   # 탈락/미진출 (회색)
COLOR_PENDING   = QColor("#555555")   # 경기 미진행


def _intl_advance_count(t):
    """대회 종류/시대에 따라 조별리그에서 직접 진출하는 팀 수(상위 N팀) 반환.
    반환: (직행팀수, 3위진출여부)
      - 직행팀수: 조 순위에서 이 순위 이하면 무조건 진출
      - 3위진출여부: True면 3위 팀들 중 일부도 진출 가능(주황 표시)
    """
    from constants import WC_EXPAND_YEAR
    kind = t.get("kind", "")
    year = t.get("year", 0)

    if kind == "world":
        # 48개국(2002~): 12조 → 조 1·2위 직행 + 3위 일부 진출
        if year >= WC_EXPAND_YEAR:
            return 2, True
        # 32개국: 8조 → 조 1·2위 직행
        return 2, False

    elif kind == "continent":
        # 대륙컵 24개국: 6조 → 조 1·2위 직행 + 3위 일부(CONT_BEST_THIRDS)
        return 2, True

    elif kind == "region":
        # [2026-08 버그수정, 신민용 리포트: "상위 2팀만 올라가는 지역컵인데
        # 3위 팀 순위표가 뜬다"] 이전엔 지역컵이면 무조건 has_thirds=True로
        # 고정해서, regional_cup_format의 best_thirds가 실제로 0인 대회
        # (예: 8개국 2조처럼 조 1·2위(4팀)만으로 브래킷이 딱 맞아 와일드
        # 카드 자리가 아예 없는 경우)에서도 "3위 팀 순위표"가 그려지고,
        # 그 안의 팀들이 실제로는 탈락인데도 색이 진출 확정(연두)으로
        # 잘못 표시됐다. 이 대회의 실제 참가국 수로 regional_cup_format을
        # 다시 계산해 best_thirds가 0보다 클 때만 has_thirds=True로 준다.
        from constants import regional_cup_format
        from database import get_conn as _gc2
        _conn3 = _gc2()
        _n_entries2 = _conn3.execute(
            "SELECT COUNT(*) n FROM intl_entries WHERE tournament_id=?", (t["id"],)).fetchone()["n"]
        _conn3.close()
        _best_thirds = regional_cup_format(_n_entries2)["best_thirds"]
        return 2, _best_thirds > 0

    elif kind in ("wc_qual", "cont_qual"):
        # 예선: 조 1위 직행(나머지는 성적순 탈락 또는 와일드카드)
        # UI상 1위만 초록, 나머지는 회색으로 표시
        return 1, False

    return 2, False


def _intl_third_qualified(t):
    """조별리그 종료 후 3위 중 실제 진출 확정된 국가 집합 반환.
    아직 조별리그 진행 중이면 빈 집합(주황으로 표시할 후보는 별도 처리).
    """
    from constants import WC_EXPAND_YEAR, CONT_BEST_THIRDS
    from database import get_conn as _gc
    import intl_engine

    if t.get("status") not in ("ko", "done"):
        return set()

    kind = t.get("kind", "")
    year = t.get("year", 0)
    tid  = t["id"]

    # 3위 진출이 없는 대회
    if kind == "world" and year < WC_EXPAND_YEAR:
        return set()
    if kind not in ("world", "continent", "region"):
        return set()

    # 3위 팀들 수집 후 성적순 정렬 → 상위 N팀
    conn = _gc()
    grps = [r["grp"] for r in conn.execute(
        "SELECT DISTINCT grp FROM intl_entries WHERE tournament_id=? ORDER BY grp",
        (tid,)).fetchall()]
    conn.close()

    thirds = []
    for g in grps:
        rows = intl_engine.get_group_standings(tid, g)
        if len(rows) >= 3:
            thirds.append(rows[2])

    if not thirds:
        return set()

    # 몇 팀이 진출하는가
    if kind == "world":
        # 48개국: 12조 × 3위 → 상위 8팀
        from constants import WC_BEST_THIRDS_BIG
        n_adv = WC_BEST_THIRDS_BIG
    elif kind == "region":
        # [2026-08 신설] 지역컵은 규모가 대회마다 달라서 CONT_BEST_THIRDS
        # 고정값이 아니라, 그 대회 실제 참가국 수로 regional_cup_format을
        # 다시 계산해 정확한 와일드카드 수를 구한다.
        from constants import regional_cup_format
        _conn2 = _gc()
        _n_entries = _conn2.execute(
            "SELECT COUNT(*) n FROM intl_entries WHERE tournament_id=?", (tid,)).fetchone()["n"]
        _conn2.close()
        n_adv = regional_cup_format(_n_entries)["best_thirds"]
    else:
        n_adv = CONT_BEST_THIRDS

    thirds.sort(key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r["ovr"]), reverse=True)
    return {r["country"] for r in thirds[:n_adv]}


class ScheduleWindow(QDialog):
    def __init__(self, league_id, my_team_id, season, lang="ko", parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("경기 일정")
        self.setMinimumSize(680, 500)
        self.setStyleSheet(STYLE)
        self.league_id  = league_id
        self.my_team_id = my_team_id
        self.season     = season
        self.lang       = lang
        self._build()
        # [최적화] main_window.refresh_all()이 부르는 self.refresh()는 항상
        #   그대로 즉시 전체 탭을 다시 그린다(기존 동작 100% 유지 — 이적/
        #   승강/국가대표 선발 등 즉시 반영돼야 하는 명시적 갱신 경로).
        #   반대로 5초짜리 배경 타이머는 "창을 그냥 열어두고 보고 있는" 동안
        #   최대 9개 탭(국제대회/챔스/컵대회 브래킷 포함)을 통째로 부수고
        #   다시 그리는 게 렉의 주요 원인이었다. 이 창의 모든 표시 내용은
        #   하루가 실제로 진행되기 전까진 절대 안 바뀌므로, 타이머 폴링에서만
        #   "직전과 조건이 같으면 건너뛰기"를 적용한다 — 사용자가 보는 결과는
        #   항상 기존과 동일하게 유지된다.
        self._last_sig = self._compute_sig()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_refresh)
        self._timer.start(5000)

    def pause_refresh(self):
        """[스레드 안전] 백그라운드 워커(시즌/주차 진행)가 DB에 쓰는 동안
        이 5초 타이머가 같은 커넥션으로 SELECT를 던지지 않도록 잠시 멈춘다.
        setEnabled(False)는 사용자 입력만 막을 뿐 QTimer 콜백은 그대로
        돌기 때문에, 이 메서드로 명시적으로 멈춰야 한다."""
        self._timer.stop()

    def resume_refresh(self):
        self._timer.start(5000)

    def _build(self):
        self._root = QVBoxLayout(self)

        conn = get_conn()
        row  = conn.execute("SELECT name, tier FROM leagues WHERE id=?", (self.league_id,)).fetchone()
        conn.close()
        lname = f"{row['name']} ({row['tier']}부)" if row else "리그"
        self._lbl = QLabel(f"📅 {lname}")
        self._lbl.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        self._root.addWidget(self._lbl)

        self._tab = QTabWidget()
        self._root.addWidget(self._tab)
        self._fill_tabs()

        btn = QPushButton("닫기"); btn.clicked.connect(self.close)
        btn.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                          "border-radius:4px;padding:6px;")
        self._root.addWidget(btn)

    def _compute_sig(self, league_id=None, my_team_id=None, season=None):
        """탭에 표시되는 모든 내용(국내 일정 + 국제대회/챔스/컵대회)이
        달라질 수 있는 최소 조건 스냅샷. 타이머 폴링 전용 — 이 값이
        안 바뀌면 advance_days()가 한 번도 안 불렸다는 뜻이라 모든 탭의
        내용이 기존과 완전히 동일함이 보장된다."""
        st = get_state()
        return (self.league_id if league_id is None else league_id,
                self.my_team_id if my_team_id is None else my_team_id,
                self.season if season is None else season,
                st.get("current_day") if st else None)

    def _poll_refresh(self):
        """5초 배경 타이머 전용 갱신. refresh()와 같은 저비용 조회(내 팀/리그
        재확인)만 먼저 해보고, 그 결과로 만든 시그니처가 직전과 같으면
        무거운 _fill_tabs()를 건너뛴다(성능 최적화). 조건이 하나라도
        바뀌었으면 refresh()를 그대로 호출해 완전히 다시 그린다 — 즉
        사용자가 보는 결과는 항상 기존과 동일하다."""
        p = get_player(); st = get_state()
        league_id = self.league_id
        my_team_id = self.my_team_id
        season = st["current_season"] if st else self.season
        if p and p.get("current_team_id"):
            my_team_id = p["current_team_id"]
            # [버그수정 2026-08, 신민용 리포트: "승강전에서 승급/강등되면
            # 바로 반영되는거 같은데, 다음 연도 1주차에 반영돼야 하지
            # 않나 — 경기 일정 다 사라지고 정보 없음 뜨더라"] teams.
            # league_id는 43주 승강 처리 즉시 새 리그로 바뀌지만, 44~52주
            # 국제대회 기간엔 이번 시즌 실제 경기가 전부 옛 리그에
            # 남아있다. _team_league_id_for_season으로 이번 시즌에 실제로
            # 뛴 리그를 먼저 찾고, 없으면(막 이적 직후 등) 지금 소속
            # 리그로 폴백한다.
            from game_engine import _team_league_id_for_season
            conn = get_conn()
            c = conn.cursor()
            _season_lid = _team_league_id_for_season(c, my_team_id, season)
            if _season_lid is not None:
                league_id = _season_lid
            else:
                row = c.execute(
                    "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                    (my_team_id,)).fetchone()
                if row:
                    league_id = row["id"]
            conn.close()
        sig = self._compute_sig(league_id, my_team_id, season)
        if sig == self._last_sig:
            return
        self.refresh()

    def refresh(self):
        p = get_player(); st = get_state()
        if p and p.get("current_team_id"):
            season = st["current_season"] if st else self.season
            from game_engine import _team_league_id_for_season
            conn = get_conn()
            c = conn.cursor()
            _season_lid = _team_league_id_for_season(c, p["current_team_id"], season)
            if _season_lid is not None:
                row = c.execute("SELECT name, tier FROM leagues WHERE id=?",
                                (_season_lid,)).fetchone()
                if row:
                    self.league_id = _season_lid
                    lname = f"{row['name']} ({row['tier']}부)"
                    self._lbl.setText(f"📅 {lname}")
            else:
                row = c.execute(
                    "SELECT l.id, l.name, l.tier FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                    (p["current_team_id"],)).fetchone()
                if row:
                    self.league_id = row["id"]
                    lname = f"{row['name']} ({row['tier']}부)"
                    self._lbl.setText(f"📅 {lname}")
            conn.close()
            self.my_team_id = p["current_team_id"]
        if st: self.season = st["current_season"]
        self._fill_tabs()
        self._last_sig = self._compute_sig()

    def _fill_tabs(self):
        # [2026-08 계측 추가, 신민용 리포트: "경기 일정 클릭할 때 약간
        # 렉이 있거든"] 경기일정 버튼 클릭 → ScheduleWindow.__init__ →
        # _build() → _fill_tabs()가 탭 13개(내경기/전체일정/국제대회
        # 본선·예선·예선PO/챔스 그룹·본선/CWC 그룹·본선/승강PO/컵대회
        # 내경기·전체·브래킷)를 전부 동기적으로 그린 뒤에야 창이 보인다 —
        # 어느 탭이 실제로 무거운지 원인 확정 전이므로 로직은 그대로 두고
        # 구간별 시간만 찍는다.
        import time as _time_sw
        _sw_t0 = _time_sw.perf_counter()
        _sw_marks = []
        # [2026-08 최적화] 이번 _fill_tabs() 호출 1회 동안만 유효한 캐시 —
        # _make_champions_tab의 groups/ko 중복 호출 제거용. 자세한 설명은
        # _make_champions_tab 내부 주석 참고.
        self._champ_fetch_cache = {}

        cur = self._tab.currentIndex()

        from PyQt6.QtWidgets import QAbstractScrollArea
        scroll_pos = {}
        for i in range(self._tab.count()):
            w = self._tab.widget(i)
            sa = w if isinstance(w, QAbstractScrollArea) else \
                 (w.findChild(QAbstractScrollArea) if w else None)
            if sa:
                scroll_pos[i] = sa.verticalScrollBar().value()

        while self._tab.count():
            w = self._tab.widget(0)
            self._tab.removeTab(0)
            if w: w.deleteLater()

        all_data = get_schedule(self.league_id, self.season)
        my_data  = [r for r in all_data
                    if r["home_team_id"]==self.my_team_id or r["away_team_id"]==self.my_team_id]
        _sw_marks.append(("get_schedule", _time_sw.perf_counter()))

        self._tab.addTab(self._make_table(my_data, my_view=True),  "내 경기")
        self._tab.addTab(self._make_table(all_data, my_view=False), "전체 일정")
        _sw_marks.append(("내경기+전체일정 테이블", _time_sw.perf_counter()))

        # 국제대회(본선) 탭
        intl_w = self._make_intl_tab("groups", qual=False)
        if intl_w:
            self._tab.addTab(intl_w, "🌍 국제대회")
        intl_ko = self._make_intl_tab("ko", qual=False)
        if intl_ko:
            self._tab.addTab(intl_ko, "🌍 국제대회(본선)")
        _sw_marks.append(("국제대회 본선", _time_sw.perf_counter()))

        # 국제대회(예선) 탭
        qual_w = self._make_intl_tab("groups", qual=True)
        if qual_w:
            self._tab.addTab(qual_w, "🌏 국제대회(예선)")
        _sw_marks.append(("국제대회 예선", _time_sw.perf_counter()))

        # 국제대회(예선 플레이오프) 탭 — PO 경기가 생성된 시점부터 표시
        qual_po_w = self._make_intl_tab("qual_po", qual=True)
        if qual_po_w:
            self._tab.addTab(qual_po_w, "🌏 국제대회(예선 플레이오프)")
        _sw_marks.append(("국제대회 예선PO", _time_sw.perf_counter()))

        # 챔피언스리그 탭
        champs_w = self._make_champions_tab("groups")
        if champs_w:
            self._tab.addTab(champs_w, "🏆 챔피언스리그")
        champs_ko = self._make_champions_tab("ko")
        if champs_ko:
            self._tab.addTab(champs_ko, "🏆 챔피언스리그(본선)")
        _sw_marks.append(("챔피언스리그", _time_sw.perf_counter()))

        # 유로파리그 탭 (2026-08 신설)
        from competition import europa_engine
        el_w = self._make_champions_tab("groups", engine=europa_engine,
                                         comp_title="유로파리그", header_color="#F28C28")
        if el_w:
            self._tab.addTab(el_w, "🥈 유로파리그")
        el_ko = self._make_champions_tab("ko", engine=europa_engine,
                                          comp_title="유로파리그", header_color="#F28C28")
        if el_ko:
            self._tab.addTab(el_ko, "🥈 유로파리그(본선)")

        # 컨퍼런스리그 탭 (2026-08 신설)
        from competition import conference_engine
        ecl_w = self._make_champions_tab("groups", engine=conference_engine,
                                          comp_title="컨퍼런스리그", header_color="#20A464")
        if ecl_w:
            self._tab.addTab(ecl_w, "🥉 컨퍼런스리그")
        ecl_ko = self._make_champions_tab("ko", engine=conference_engine,
                                           comp_title="컨퍼런스리그", header_color="#20A464")
        if ecl_ko:
            self._tab.addTab(ecl_ko, "🥉 컨퍼런스리그(본선)")
        _sw_marks.append(("클럽대항전(유로파/컨퍼런스)", _time_sw.perf_counter()))

        # [2026-07 신설, 신민용 리포트: "클럽월드컵이 경기 일정에 안 뜬다"]
        cwc_w = self._make_cwc_tab()
        if cwc_w:
            self._tab.addTab(cwc_w, "🌍 클럽 월드컵")
        cwc_bracket_w = self._make_cwc_bracket_tab()
        if cwc_bracket_w:
            self._tab.addTab(cwc_bracket_w, "🌍 클럽 월드컵(본선)")
        _sw_marks.append(("클럽월드컵", _time_sw.perf_counter()))

        # [2026-07 신설, 신민용 리포트: "경기 일정 창에 승강전 탭이 안 뜬다"]
        po_w = self._make_po_tab()
        if po_w:
            self._tab.addTab(po_w, "⚖ 승강 플레이오프")
        _sw_marks.append(("승강PO", _time_sw.perf_counter()))

        # [2026-07 신설] 국내 컵대회 탭 — 예전엔 이 탭 자체가 없어서 컵
        # 경기가 로그에만 남고 일정 화면 어디에도 안 보였다.
        # [2026-07 수정, 신민용 리포트: "컵대회도 내 경기/전체일정처럼
        # 나누는 게 시각적으로 더 좋지 않아?"] 리그 일정 탭과 동일하게
        # '내 경기' 탭을 먼저, '전체 일정' 탭을 뒤에 둔다.
        cup_my_w = self._make_cup_tab(my_view=True)
        if cup_my_w:
            self._tab.addTab(cup_my_w, "🎖️ 컵대회(내 경기)")
        cup_all_w = self._make_cup_tab(my_view=False)
        if cup_all_w:
            self._tab.addTab(cup_all_w, "🎖️ 컵대회(전체 일정)")
        _sw_marks.append(("컵대회", _time_sw.perf_counter()))
        # [2026-07 신설] 챔피언스리그·국제대회처럼 컵대회도 토너먼트
        # 대진표(브래킷)로 보여주는 탭 — 4강 이후 결승/3·4위전이 생기면서
        # 다른 대회들과 같은 방식으로 표시할 수 있게 됐다.
        cup_bracket_w = self._make_cup_bracket_tab()
        if cup_bracket_w:
            self._tab.addTab(cup_bracket_w, "🎖️ 컵대회(본선)")
        _sw_marks.append(("컵대회 브래킷", _time_sw.perf_counter()))

        _sw_total = _sw_marks[-1][1] - _sw_t0
        # [2026-08 재계측, 신민용 리포트: "경기 일정 창 켜놓고 진행하면
        # 렉, 팀 많을수록 심해짐"] 이전 계측(위 주석)은 "탭 렌더링 고정비용"
        # 결론까지만 냈고, 팀 수 증가에 비례해서 어느 탭이 커지는지는 아직
        # 실측하지 않았다 — 국내 컵대회 '전체 일정' 탭은 대회 참가팀 수(=
        # 사실상 그 나라 전체 팀 수)만큼 행이 생기는 유일한 탭이라 유력한
        # 용의자지만, 감으로 고치지 않고 여기서 행 수까지 같이 찍어서
        # 확인한다. 0.03초 이상일 때만 찍어 평소엔 조용하다.
        if _sw_total >= 0.03:
            _prev = _sw_t0
            _parts = []
            for _name, _t in _sw_marks:
                _parts.append(f"{_name} {_t-_prev:.3f}s")
                _prev = _t
            _extra = f" | 내경기={len(my_data)}행 전체일정={len(all_data)}행"
            if getattr(self, "_last_cup_all_rows", None) is not None:
                _extra += f" 컵대회(전체)={self._last_cup_all_rows}행"
            print(f"[PERF-SCHED] _fill_tabs 총 {_sw_total:.3f}s — "
                  + " | ".join(_parts) + _extra)

        if 0 <= cur < self._tab.count():
            self._tab.setCurrentIndex(cur)

        if scroll_pos:
            def _restore():
                for i, v in scroll_pos.items():
                    if i >= self._tab.count():
                        continue
                    w = self._tab.widget(i)
                    sa = w if isinstance(w, QAbstractScrollArea) else \
                         (w.findChild(QAbstractScrollArea) if w else None)
                    if sa:
                        sa.verticalScrollBar().setValue(v)
            QTimer.singleShot(0, _restore)

    # ── 국제대회 탭 ──────────────────────────────

    def _make_intl_tab(self, mode="groups", qual=False):
        import intl_engine
        from game_engine import get_state, get_player
        st = get_state()
        if not st:
            return None
        t = intl_engine.get_my_tournament(st["current_year"], qual=qual)
        if not t:
            return None
        if qual and mode == "ko":
            return None
        if mode == "qual_po" and not qual:
            return None
        _is_qual = t.get("kind") in ("wc_qual", "cont_qual")
        _grp_stage = "qual_group" if _is_qual else "group"

        from PyQt6.QtWidgets import QScrollArea, QFrame
        p   = get_player()
        nat = intl_engine._my_nat(t, p)

        # [2026-08 계측 추가, 신민용 리포트: "43→44주, 44→45주도 렉걸리네"]
        # [PERF-SCHED] 로그에서 국제대회 탭이 0.9초까지 튀는 게 확인돼서
        # (국제대회 기간=43주 이후와 시점이 겹침), DB조회/그룹순위계산/
        # 위젯렌더링 중 어느 게 무거운지 원인 확정 전이므로 로직은 그대로
        # 두고 시간만 찍는다. 아래 그룹 순위 계산(intl_engine 호출)은 코드상
        # "조별 순위표"용과 "3위 팀 순위표"용 두 곳에서 같은 그룹을 두 번
        # 계산하고 있어 — 이것도 실제로 무거우면 캐싱 여지가 있다.
        import time as _time_it
        _it_t0 = _time_it.perf_counter()
        _it_standings_calc = 0.0

        outer = QScrollArea(); outer.setWidgetResizable(True)
        outer.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
        body  = QWidget(); lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(10)

        # 헤더
        if _is_qual:
            if mode == "qual_po":
                status_txt = "플레이오프 진행 중" if t["status"] == "qual_po" else \
                             f"플레이오프 종료  |  결과: {t.get('my_result','') or '─'}"
            else:
                status_txt = "예선 진행 중"
                if t["status"] in ("done", "qual_po"):
                    status_txt = f"예선 종료  |  결과: {t.get('my_result','') or '─'}"
            icon = "🌏"
        else:
            status_txt = {"group": "조별리그 진행 중", "ko": "토너먼트 진행 중"}.get(t["status"], "")
            if t["status"] == "done":
                status_txt = f"종료  |  🏆 우승: {t['winner']}"
            icon = "🌍"
        hdr = QLabel(f"{icon} {t['name']}  ─  {status_txt}")
        # [2026-07 색상 규칙 개편, 신민용 요청] 예전엔 국제대회 전부(월드컵/
        # 대륙컵/예선)가 파란색 하나로 뭉뚱그려 표시됐다. 이제 리그=초록,
        # 컵=보라, 챔스=황금과 같은 급으로 국제대회도 종류별로 나눈다:
        #   - 월드컵·대륙컵(본선, kind in world/continent) → 주황
        #   - 그 외 국가대표 대회(예선 wc_qual 등) → 빨강
        _hdr_color = "#ff9933" if t.get("kind") in ("world", "continent", "region") else "#ff5555"
        hdr.setStyleSheet(f"color:{_hdr_color};font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)
        if t["my_selected"] == 1:
            sub = QLabel(f"📣 {nat} 국가대표 소집")
        elif t["my_selected"] == 0:
            sub = QLabel(f"📋 {nat} 국가대표 미선발")
        elif _is_qual:
            sub = QLabel(f"📋 {nat} 예선 참가")
        else:
            sub = QLabel(f"📋 {nat} 예선 탈락")
        sub.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(sub)

        conn = get_conn()
        groups = [r["grp"] for r in conn.execute(
            "SELECT DISTINCT grp FROM intl_entries WHERE tournament_id=? ORDER BY grp",
            (t["id"],)).fetchall()]
        if _is_qual and mode == "qual_po":
            ko_rows = [dict(r) for r in conn.execute(
                """SELECT * FROM intl_matches WHERE tournament_id=? AND stage='qual_po'
                   ORDER BY week, id""", (t["id"],)).fetchall()]
        elif _is_qual:
            ko_rows = []
        else:
            ko_rows = [dict(r) for r in conn.execute(
                """SELECT * FROM intl_matches WHERE tournament_id=? AND stage!='group'
                   ORDER BY week, slot""", (t["id"],)).fetchall()]
        # [2026-07 신설] 조별리그 화면 우측에 보여줄 '누가 언제 붙는지' 일정.
        grp_match_rows = [dict(r) for r in conn.execute(
            """SELECT * FROM intl_matches WHERE tournament_id=? AND stage=?
               ORDER BY week, grp, id""", (t["id"], _grp_stage)).fetchall()]
        flags = {r["country"]: r["flag"] for r in conn.execute(
            "SELECT country, flag FROM intl_entries WHERE tournament_id=?",
            (t["id"],)).fetchall()}
        conn.close()
        _it_t_dbdone = _time_it.perf_counter()

        if mode == "ko" and not ko_rows:
            return None
        if mode == "qual_po" and not ko_rows:
            return None

        # ── 조별리그 순위표 (좌: 조별 순위 / 우: 경기 일정, 2026-07 2단 레이아웃) ──
        if mode == "groups":
            split_row = QHBoxLayout()
            split_row.setSpacing(14)
            left_widget = QWidget()
            lay_orig = lay          # 아래 기존 코드가 'lay.addWidget(...)'를
            lay = QVBoxLayout(left_widget)   # 그대로 쓰도록 lay를 잠시 왼쪽 컬럼으로 바꿔치기
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)
            _max_left_w = 0   # [2026-07 버그수정] 국가명 길이만큼 필요한 폭 추적용

            # 진출 기준 계산
            advance_n, has_thirds = _intl_advance_count(t)
            # 조별리그 종료 후 3위 진출 확정팀
            third_ok = _intl_third_qualified(t) if has_thirds else set()
            # 아직 진행 중이면 3위 후보 전체를 주황으로 표시
            thirds_in_progress = (has_thirds and t.get("status") == "group")

            # [2026-08 버그수정, 신민용 리포트: "국제대회(예선) 탭에서
            # 실제로 본선 올라가는 팀들이 초록색으로 안 뜨고 조 1위만
            # 뜬다"] 예선(wc_qual/cont_qual)은 위 _intl_advance_count가
            # 무조건 "조 1위만 직행"으로 뭉뚱그렸다 — 실제로는 유로처럼
            # 2위도 전원 직행하거나, 2위 중 성적순 와일드카드/플레이오프가
            # 있는 대회도 있다. intl_engine.get_qual_advance_status가
            # 실제 대회 설정을 그대로 재현해 국가별 상태를 계산해준다.
            _qual_status = intl_engine.get_qual_advance_status(t) if _is_qual else {}

            lbl_g = QLabel("◼ 조별리그")
            lbl_g.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
            lay.addWidget(lbl_g)

            # [2026-08 최적화, 신민용 리포트: "43→44→45주, 52→1주 렉 없애자"]
            # 예전엔 이 아래 루프와 "3위 팀 순위표" 루프가 같은 그룹의 순위를
            # 각각 따로 계산했다(그룹당 2번) — 결과는 똑같은데 계산만 중복.
            # 여기서 한 번 계산해서 캐싱해두고 아래 3위 팀 루프에서 재사용한다.
            _group_rows_cache = {}
            for g in groups:
                _it_gs0 = _time_it.perf_counter()
                if _is_qual:
                    rows = intl_engine._qual_group_standings(t["id"], g)
                    for r in rows:
                        r.setdefault("w", 0); r.setdefault("d", 0); r.setdefault("l", 0)
                else:
                    rows = intl_engine.get_group_standings(t["id"], g)
                _it_standings_calc += _time_it.perf_counter() - _it_gs0
                _group_rows_cache[g] = rows

                gt = QTableWidget(len(rows), 7)
                gt.setHorizontalHeaderLabels([f"{g}조", "경기", "승", "무", "패", "득실", "승점"])
                gt.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                gt.verticalHeader().setVisible(False)
                gt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                gt.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                # [2026-08 최적화, 신민용 리포트: "43→44→45주, 52→1주 렉
                # 없애자"] ResizeToContents 모드를 행 채우기 "전"에 걸어두면
                # setItem() 호출마다(조당 최대 수십 회) Qt가 전체 컬럼 폭을
                # 매번 다시 계산한다 — 아래에서 행을 다 채운 뒤 어차피
                # resizeColumnsToContents()를 명시적으로 한 번 더 부르므로,
                # 모드 적용을 그 뒤로 옮겨서 이 반복 재계산을 없앤다. 최종
                # 컬럼 폭·화면은 완전히 동일하다.
                # [2026-07 버그수정, 신민용 리포트: "이름이 너무 길어서
                # 잘리는 경우가 있는데 이름 크기만큼 창이 늘어나서 다
                # 보이게 하고 싶다"] 0번 컬럼(국가명)이 Stretch라 left_widget
                # 고정폭(기존 420)에 맞춰 긴 이름이 잘렸다. Stretch를 빼서
                # 실제 필요한 폭만큼 컬럼이 넓어지게 하고, 그 폭을 아래에서
                # 창 크기 계산에 반영한다.
                gt.setStyleSheet(
                    "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:1px solid #2a2a2a;}"
                    "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")

                for i, r in enumerate(rows):
                    country = r.get("country", "")
                    gd = r["gf"] - r["ga"]
                    vals = [f"{r['flag']}{country}", str(r["p"]), str(r["w"]),
                            str(r["d"]), str(r["l"]), f"{'+' if gd>0 else ''}{gd}", str(r["pts"])]

                    # 색상 결정
                    if country == nat:
                        color = COLOR_MY
                    elif _is_qual:
                        # 예선: 대회 설정(직행/와일드카드/플레이오프)을
                        # 실제로 반영한 상태를 그대로 색으로 매핑한다.
                        qstat = _qual_status.get(country, "eliminated")
                        if qstat == "direct":
                            color = COLOR_ADVANCE       # 직행 확정 (초록)
                        elif qstat == "po_ok":
                            color = COLOR_THIRD_OK      # 플레이오프 승리로 진출 확정 (연두)
                        elif qstat == "po_bubble":
                            color = COLOR_THIRD         # 플레이오프 경쟁 중 (주황)
                        else:
                            color = COLOR_ELIM          # 탈락 (회색)
                    elif i < advance_n:
                        color = COLOR_ADVANCE       # 직접 진출 (초록)
                    elif i == advance_n and has_thirds:
                        # 3위 자리
                        if country in third_ok:
                            color = COLOR_THIRD_OK  # 3위 진출 확정 (연두)
                        elif thirds_in_progress:
                            color = COLOR_THIRD     # 3위 진출 경쟁 중 (주황)
                        else:
                            color = COLOR_ELIM      # 탈락
                    else:
                        color = COLOR_ELIM          # 탈락 (회색)

                    for j, v in enumerate(vals):
                        item = QTableWidgetItem(v)
                        if j > 0: item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        item.setForeground(color)
                        if j == 0:
                            item.setData(_CLEAN_TEXT_ROLE, country)
                        gt.setItem(i, j, item)

                gt.setFixedHeight(gt.verticalHeader().defaultSectionSize() * len(rows) + 28)
                gt.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                gt.resizeColumnsToContents()
                _need_w = sum(gt.columnWidth(j) for j in range(gt.columnCount())) + 24
                gt.setMinimumWidth(_need_w)
                _max_left_w = max(_max_left_w, _need_w)
                _enable_plain_copy(gt)
                lay.addWidget(gt)

            # ── PO/와일드카드 경쟁팀 순위표 (예선 전용) ──
            # [2026-08 신설, 신민용 리포트: "예선 3등이여도 점수 높아서
            # 토너먼트 가는 경우가 있는데 이것도 표시해줘"] 국제대회
            # 본선의 "3위 팀 순위표"와 같은 발상을, 조 2위(또는 조
            # 1위)끼리 성적순으로 경쟁하는 예선 체제(와일드카드/플레이오프)
            # 에도 적용한다.
            #
            # [2026-08 버그수정, 신민용 리포트: "아프리카는 1등팀들끼리
            # 플레이오프 하는 거 아니야?"] 처음엔 이 표가 무조건 조 2위
            # (rows[1])만 모았는데, direct=0인 체제(32팀 예선 아시아/
            # 아프리카)는 실제로 조 1위 전원이 직행 없이 바로 플레이오프로
            # 가는 방식이라(_finalize_qual 참고) 조 2위가 아니라 조 1위가
            # 경쟁 대상이다 — 대회 설정을 봐서 어느 순위가 실제 PO/와일드
            # 카드 후보인지 판단한다.
            if _is_qual:
                from constants import WC_QUAL_32, WC_QUAL_48, EURO_QUAL, WC_EXPAND_YEAR
                _qcontinent = intl_engine._conf_key((t.get("continent") or "").strip() or "유럽")
                if t.get("kind") == "cont_qual":
                    _qcfg = EURO_QUAL.get(_qcontinent, {})
                else:
                    _qbig = t.get("year", 0) >= WC_EXPAND_YEAR
                    _qcfg = (WC_QUAL_48 if _qbig else WC_QUAL_32).get(_qcontinent, {})
                _direct_n_for_race = _qcfg.get("direct", len(groups))
                _race_rank_idx = 0 if _direct_n_for_race == 0 else 1
                _race_label = "1위" if _race_rank_idx == 0 else "2위"

                runner_rows = []
                for g in groups:
                    rows = _group_rows_cache.get(g)
                    if rows and len(rows) >= _race_rank_idx + 1:
                        r2 = dict(rows[_race_rank_idx])
                        r2["grp"] = g
                        runner_rows.append(r2)

                # 경쟁이 실제로 존재하는 경우에만 표를 그린다 — 전원이
                # 그냥 직행(status가 전부 'direct')이면 굳이 안 보여준다.
                _has_runner_race = any(
                    _qual_status.get(r["country"], "eliminated") != "direct" for r in runner_rows)

                if runner_rows and _has_runner_race:
                    runner_rows.sort(
                        key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r.get("ovr", 0)),
                        reverse=True)

                    n_direct_runners = sum(
                        1 for r in runner_rows if _qual_status.get(r["country"]) == "direct")
                    n_po = sum(
                        1 for r in runner_rows
                        if _qual_status.get(r["country"]) in ("po_bubble", "po_ok"))

                    if n_po > 0:
                        lbl_r_title = f"◼ {_race_label} 팀 순위 (상위 {n_direct_runners}팀 직행, 이후 {n_po}팀 PO행)"
                    else:
                        lbl_r_title = f"◼ {_race_label} 팀 순위 (상위 {n_direct_runners}팀 와일드카드 직행)"
                    lbl_r = QLabel(lbl_r_title)
                    lbl_r.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;margin-top:6px;")
                    lay.addWidget(lbl_r)

                    # 컷라인(직행 경계) 표시용 구분 행
                    cut_at_r = n_direct_runners if 0 < n_direct_runners < len(runner_rows) else None
                    total_rows_r = len(runner_rows) + (1 if cut_at_r is not None else 0)

                    rt = QTableWidget(total_rows_r, 8)
                    rt.setHorizontalHeaderLabels(
                        ["순위", "조", "국가", "경기", "승", "무", "패", "득실/승점"])
                    rt.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                    rt.verticalHeader().setVisible(False)
                    rt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                    rt.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                    rt.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                    rt.setStyleSheet(
                        "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:1px solid #2a2a2a;}"
                        "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")

                    row_i_r = 0
                    for rank, r in enumerate(runner_rows, start=1):
                        country = r.get("country", "")
                        gd = r["gf"] - r["ga"]
                        vals = [str(rank), f"{r['grp']}조", f"{r['flag']}{country}",
                                str(r["p"]), str(r["w"]), str(r["d"]), str(r["l"]),
                                f"{'+' if gd > 0 else ''}{gd} / {r['pts']}점"]

                        if country == nat:
                            color = COLOR_MY
                        else:
                            qstat = _qual_status.get(country, "eliminated")
                            if qstat == "direct":
                                color = COLOR_ADVANCE
                            elif qstat == "po_ok":
                                color = COLOR_THIRD_OK
                            elif qstat == "po_bubble":
                                color = COLOR_THIRD
                            else:
                                color = COLOR_ELIM

                        for j, v in enumerate(vals):
                            item = QTableWidgetItem(v)
                            if j > 0:
                                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                            item.setForeground(color)
                            if j == 2:
                                item.setData(_CLEAN_TEXT_ROLE, country)
                            rt.setItem(row_i_r, j, item)
                        row_i_r += 1

                        if cut_at_r is not None and rank == cut_at_r:
                            rt.setSpan(row_i_r, 0, 1, 8)
                            cut_item_r = QTableWidgetItem("▲ 직행 컷라인 (이후는 PO/탈락) ▲")
                            cut_item_r.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                            cut_item_r.setForeground(QColor("#666666"))
                            rt.setItem(row_i_r, 0, cut_item_r)
                            rt.setRowHeight(row_i_r, 18)
                            row_i_r += 1

                    rt.setFixedHeight(rt.verticalHeader().defaultSectionSize() * total_rows_r + 28)
                    rt.resizeColumnsToContents()
                    _need_w_rt = sum(rt.columnWidth(j) for j in range(rt.columnCount())) + 24
                    rt.setMinimumWidth(_need_w_rt)
                    _max_left_w = max(_max_left_w, _need_w_rt)
                    _enable_plain_copy(rt)
                    lay.addWidget(rt)

            # ── 3위 팀 순위표 (3위 진출 대회만: 48개국 월드컵/대륙컵) ──
            # 실제 2026 월드컵 중계처럼, 각 조 3위끼리 성적순으로 줄 세워서
            # 상위 N팀만 진출/나머지는 탈락인지 한눈에 보여준다.
            if has_thirds:
                third_rows = []
                for g in groups:
                    rows = _group_rows_cache.get(g)
                    if rows is None:
                        _it_gs0b = _time_it.perf_counter()
                        if _is_qual:
                            rows = intl_engine._qual_group_standings(t["id"], g)
                            for r in rows:
                                r.setdefault("w", 0); r.setdefault("d", 0); r.setdefault("l", 0)
                        else:
                            rows = intl_engine.get_group_standings(t["id"], g)
                        _it_standings_calc += _time_it.perf_counter() - _it_gs0b
                    if len(rows) >= 3:
                        r3 = dict(rows[2])
                        r3["grp"] = g
                        third_rows.append(r3)

                if third_rows:
                    third_rows.sort(
                        key=lambda r: (r["pts"], r["gf"] - r["ga"], r["gf"], r.get("ovr", 0)),
                        reverse=True)

                    if t.get("kind") == "world":
                        from constants import WC_BEST_THIRDS_BIG
                        n_adv3 = WC_BEST_THIRDS_BIG
                    elif t.get("kind") == "region":
                        # [2026-08 버그수정, 신민용 리포트: "상위 2팀만
                        # 올라가는 지역컵인데 3위 팀 순위표에 '상위 4팀
                        # 진출'로 뜬다"] 지역컵은 대회마다 참가국 규모가
                        # 달라 CONT_BEST_THIRDS(대륙컵 24개국 고정값)를
                        # 그대로 쓰면 틀린다 — _intl_third_qualified와
                        # 동일하게 이 대회의 실제 참가국 수로
                        # regional_cup_format을 다시 계산한다.
                        from constants import regional_cup_format
                        from database import get_conn as _gc3
                        _conn4 = _gc3()
                        _n_entries3 = _conn4.execute(
                            "SELECT COUNT(*) n FROM intl_entries WHERE tournament_id=?",
                            (t["id"],)).fetchone()["n"]
                        _conn4.close()
                        n_adv3 = regional_cup_format(_n_entries3)["best_thirds"]
                    else:
                        from constants import CONT_BEST_THIRDS
                        n_adv3 = CONT_BEST_THIRDS

                    lbl_t = QLabel(f"◼ 3위 팀 순위 (상위 {n_adv3}팀 진출)")
                    lbl_t.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;margin-top:6px;")

                    # [2026-08 버그수정] n_adv3<=0(진출 가능한 3위 자리 자체가
                    # 없는 대회 — 예: 지역컵 8개국 2조처럼 조 1·2위 4팀만
                    # 으로 브래킷이 딱 맞아 와일드카드 자리가 아예 없는
                    # 경우)이면 예전엔 그대로 컷라인이 None이 되면서 "컷
                    # 라인 없음=전원 진출확정"으로 잘못 해석해 아무도 실제
                    # 진출 못 하는 3위 팀들을 연두(진출확정)로 표시했다.
                    # n_adv3<=0이면 이 표 자체를 아예 그리지 않는다.
                    if n_adv3 <= 0:
                        third_rows = []
                    else:
                        lay.addWidget(lbl_t)

                    # 컷라인(진출/탈락 경계) 표시용 구분 행을 진출팀 수만큼 뒤에 끼워 넣는다.
                    if third_rows:
                        cut_at = n_adv3 if 0 < n_adv3 < len(third_rows) else None
                        total_rows = len(third_rows) + (1 if cut_at is not None else 0)

                        tt = QTableWidget(total_rows, 8)
                        tt.setHorizontalHeaderLabels(
                            ["순위", "조", "국가", "경기", "승", "무", "패", "득실/승점"])
                        tt.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                        tt.verticalHeader().setVisible(False)
                        tt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                        tt.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                        tt.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                        # [2026-07 버그수정] 위 조별 순위표와 동일한 이유로 국가명
                        # Stretch 제거 — 이름 잘림 방지.
                        tt.setStyleSheet(
                            "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:1px solid #2a2a2a;}"
                            "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")

                        row_i = 0
                        for rank, r in enumerate(third_rows, start=1):
                            country = r.get("country", "")
                            gd = r["gf"] - r["ga"]
                            vals = [str(rank), f"{r['grp']}조", f"{r['flag']}{country}",
                                    str(r["p"]), str(r["w"]), str(r["d"]), str(r["l"]),
                                    f"{'+' if gd > 0 else ''}{gd} / {r['pts']}점"]

                            if country == nat:
                                color = COLOR_MY
                            elif cut_at is not None and rank <= cut_at:
                                color = COLOR_THIRD_OK if not thirds_in_progress else COLOR_THIRD
                            elif cut_at is None and thirds_in_progress:
                                color = COLOR_THIRD
                            elif cut_at is None:
                                color = COLOR_THIRD_OK
                            else:
                                color = COLOR_ELIM

                            for j, v in enumerate(vals):
                                item = QTableWidgetItem(v)
                                if j > 0:
                                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                                item.setForeground(color)
                                if j == 2:
                                    item.setData(_CLEAN_TEXT_ROLE, country)
                                tt.setItem(row_i, j, item)
                            row_i += 1

                            # 진출 컷라인 — 진출 확정 인원 바로 뒤에 구분선 행 삽입
                            if cut_at is not None and rank == cut_at:
                                tt.setSpan(row_i, 0, 1, 8)
                                cut_item = QTableWidgetItem(
                                    "▲ 진출 컷라인 (여기까지 진출) ▲" if not thirds_in_progress
                                    else "▲ 현재 컷라인 — 남은 경기에 따라 바뀔 수 있음 ▲")
                                cut_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                                cut_item.setForeground(QColor("#666666"))
                                tt.setItem(row_i, 0, cut_item)
                                tt.setRowHeight(row_i, 18)
                                row_i += 1

                        tt.setFixedHeight(tt.verticalHeader().defaultSectionSize() * total_rows + 28)
                        tt.resizeColumnsToContents()
                        _need_w_tt = sum(tt.columnWidth(j) for j in range(tt.columnCount())) + 24
                        tt.setMinimumWidth(_need_w_tt)
                        _max_left_w = max(_max_left_w, _need_w_tt)
                        _enable_plain_copy(tt)
                        lay.addWidget(tt)

            # 범례
            hint_parts = ["🟢진출확정", "🔵내 국가"]
            if has_thirds:
                hint_parts.append("🟡3위진출경쟁")
                hint_parts.append("🟩3위진출확정")
            elif _is_qual:
                hint_parts.append("🟡플레이오프경쟁")
                hint_parts.append("🟩플레이오프진출확정")
            hint_parts.append("⬜탈락")
            hint = QLabel("  ".join(hint_parts))
            hint.setStyleSheet("color:#666;font-size:10px;margin-top:4px;")
            lay.addWidget(hint)
            lay.addStretch()

            # 우측: 조별 경기 일정('누가 언제 붙는지') — 창이 넓어져도 왼쪽
            # 조별 순위표만 늘어나 잉여 공간이 생기던 문제를 2단 분할로 해소.
            right_widget = QWidget()
            right_lay = QVBoxLayout(right_widget)
            right_lay.setContentsMargins(0, 0, 0, 0)
            right_lay.setSpacing(6)
            lbl_sched = QLabel("◼ 경기 일정")
            lbl_sched.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
            right_lay.addWidget(lbl_sched)
            right_lay.addWidget(self._build_grouped_fixture_column(
                grp_match_rows, flags, nat, p, t["year"],
                group_key="grp", group_label_fmt="{}조 일정"))
            right_lay.addStretch()

            left_widget.setMinimumWidth(_max_left_w)
            split_row.addWidget(left_widget, 0)
            split_row.addWidget(right_widget, 1)
            lay = lay_orig
            lay.addLayout(split_row)

            # [2026-07 버그수정] left_widget 고정폭(기존 420) 캡을 없앤 대신,
            # 실제 필요한 폭(_max_left_w)을 창 크기 계산에 반영 — CL 탭과
            # 동일한 이유(_build_grouped_fixture_column의 자체 resize는
            # 왼쪽 폭을 고정값으로 가정하므로, 이름이 길어 그보다 넓어지면
            # 여기서 다시 한번 정확히 계산해줘야 한다).
            _want_total = _max_left_w + getattr(self, "_max_fixture_w_seen", 0) + 60
            if _want_total > self.width():
                from PyQt6.QtWidgets import QApplication
                scr = QApplication.primaryScreen()
                max_w = (scr.availableGeometry().width() - 40) if scr else 1600
                self.resize(min(_want_total, max_w), self.height())

        # ── 토너먼트 브래킷 (본선 KO / 예선 PO 공용) ──
        if mode in ("ko", "qual_po") and ko_rows:
            from ui.bracket_widget import BracketWidget, build_rounds_from_matches
            lbl_k = QLabel("◼ 토너먼트")
            lbl_k.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;margin-top:6px;")
            lay.addWidget(lbl_k)

            stage_order = {}
            for m in ko_rows:
                stg = intl_engine.STAGE_KO.get(m["stage"], m["stage"])
                if stg not in stage_order:
                    stage_order[stg] = len(stage_order)

            bracket_matches = []
            for m in ko_rows:
                hs, as_ = m["home_score"], m["away_score"]
                played  = hs is not None and hs >= 0
                if played:
                    winner = m["pso_winner"] or (m["home"] if hs > as_ else m["away"])
                else:
                    winner = ""
                if nat and nat == m["home"]:
                    my_side = "home"
                elif nat and nat == m["away"]:
                    my_side = "away"
                else:
                    my_side = None
                # [2026-07 신설, 신민용 리포트: "8강 칸이 그냥 텅 비어
                # 보인다"] intl_engine._precreate_ko_shell로 대회 시작
                # 시점에 미리 만들어둔 placeholder 행은 home/away가 빈
                # 문자열이다 — BracketWidget에 그대로 넘기면 팀명 칸이
                # 완전히 공백으로 그려져서(원래 텍스트가 "" 자체) 아무
                # 정보도 없어 보인다. 아직 안 정해진 자리는 "미정"으로
                # 명시해서, 최소한 "이 라운드가 존재하고 대진만 안 정해진
                # 상태"라는 게 눈에 보이게 한다.
                _home_disp = m["home"] or "미정"
                _away_disp = m["away"] or "미정"
                bracket_matches.append({
                    "stage": intl_engine.STAGE_KO.get(m["stage"], m["stage"]),
                    "week": m["week"],
                    "home": _home_disp, "away": _away_disp,
                    "home_flag": flags.get(m["home"], ""),
                    "away_flag": flags.get(m["away"], ""),
                    "hs": hs if played else -1, "as_": as_ if played else -1,
                    "winner": winner,
                    "pso": m["pso_score"] if m["pso_winner"] else "",
                    "my_side": my_side,
                })

            rounds = build_rounds_from_matches(bracket_matches, stage_order)
            bracket = BracketWidget(rounds)
            lay.addWidget(bracket)
            self._fit_to_bracket(bracket)

        lay.addStretch()
        outer.setWidget(body)
        _it_total = _time_it.perf_counter() - _it_t0
        # [2026-08 정리] PERF-SCHED와 동일한 이유로 로그 제거(원인 진단 완료).
        # if _it_total >= 0.05:
        #     print(f"[PERF-INTLTAB] _make_intl_tab(mode={mode}, qual={qual}) 총 "
        #           f"{_it_total:.3f}s — DB조회 {_it_t_dbdone-_it_t0:.3f}s | "
        #           f"그룹순위계산누적({len(groups)}개조×2회) {_it_standings_calc:.3f}s | "
        #           f"나머지(위젯렌더링) {_it_total-(_it_t_dbdone-_it_t0)-_it_standings_calc:.3f}s")
        return outer

    # ── 챔피언스리그 탭 ──────────────────────────

    def _make_champions_tab(self, mode="groups", engine=None, comp_title="챔피언스리그",
                             header_color="#ffcc00", cup_name_fallback=None):
        """[2026-08 확장, 신민용 요청: 유로파/컨퍼런스도 같은 화면 재사용]
        engine: champions_engine/europa_engine/conference_engine 모듈을 그대로
        받는다 — get_my_champions_matches/get_my_cl_league_standings에 해당하는
        각 엔진의 함수(get_my_europa_matches/get_my_el_league_standings 등)를
        engine 모듈에서 동적으로 찾아 호출한다. engine=None(기본값)이면 기존
        챔스 그대로 동작(하위 호환)."""
        if engine is None:
            try:
                from competition import champions_engine as engine
            except ImportError:
                return None
        from game_engine import get_state, get_player

        st = get_state()
        if not st:
            return None

        p = get_player()
        if not p or not p.get("current_team_id"):
            return None

        conn = get_conn()
        team_info = conn.execute("""
            SELECT t.id, t.name, l.id as league_id, l.name as league_name,
                   cn.id as country_id, cn.name as country_name, cn.continent
            FROM teams t
            JOIN leagues l ON t.league_id = l.id
            JOIN countries cn ON l.country_id = cn.id
            WHERE t.id = ?
        """, (p["current_team_id"],)).fetchone()
        conn.close()

        if not team_info:
            return None

        my_team_id = team_info["id"]
        # [2026-08 최적화, 신민용 리포트: "경기 일정 창 켜놓고 진행하면 렉"]
        # 이 탭은 "groups"/"ko" 두 모드로 각각 호출되는데(챔스/유로파/
        # 컨퍼런스 3개 대회 × 2모드 = 6번), 예전엔 모드가 달라도 매번
        # get_my_*_matches/get_my_*_standings를 처음부터 다시 계산했다 —
        # 같은 _fill_tabs() 호출 안에서 같은 대회(engine)·같은 연도의
        # 결과는 완전히 동일하므로, _fill_tabs 시작 시 초기화하는
        # self._champ_fetch_cache에 담아 두 번째 호출(ko 모드)에서
        # 재사용한다. 데이터/화면 결과는 기존과 100% 동일하다.
        _cache_key = (id(engine), st["current_year"])
        _cache = getattr(self, "_champ_fetch_cache", None)
        if _cache is not None and _cache_key in _cache:
            matches = _cache[_cache_key]
        else:
            _get_matches_fn = getattr(engine, "get_my_champions_matches", None) or \
                               getattr(engine, "get_my_europa_matches", None) or \
                               getattr(engine, "get_my_conference_matches", None)
            matches = _get_matches_fn(st["current_year"])
            if _cache is not None:
                _cache[_cache_key] = matches
        if not matches:
            return None

        from PyQt6.QtWidgets import QScrollArea

        outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        cont = team_info["continent"]
        from competition.champions_engine import CONTINENT_MAP
        cl_cont = CONTINENT_MAP.get(cont, cont)
        _name_map = getattr(engine, "CL_CUP_NAME", None) or \
                    getattr(engine, "EL_CUP_NAME", None) or \
                    getattr(engine, "ECL_CUP_NAME", None) or {}
        league_name = _name_map.get(cl_cont, f"{cl_cont} {comp_title}")

        hdr = QLabel(f"🏆 {st['current_year']}년 {league_name}")
        hdr.setStyleSheet(f"color:{header_color};font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)

        sub = QLabel(f"팀: {team_info['name']} ({team_info['league_name']})")
        sub.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(sub)

        # ── 리그 스테이지 순위표 (2026-07 스위스 방식 개편 - 조별리그 폐지) ──
        # [2026-07] 좌: 순위표 / 우: 경기 일정 2단 분할 — 국제대회 탭과 동일한 개편.
        _standings_cache_key = (id(engine), st["current_year"], "standings")
        if _cache is not None and _standings_cache_key in _cache:
            league_info = _cache[_standings_cache_key]
        else:
            _get_standings_fn = getattr(engine, "get_my_cl_league_standings", None) or \
                                getattr(engine, "get_my_el_league_standings", None) or \
                                getattr(engine, "get_my_ecl_league_standings", None)
            league_info = _get_standings_fn(st["current_year"])
            if _cache is not None:
                _cache[_standings_cache_key] = league_info
        if mode == "groups" and league_info:
            split_row = QHBoxLayout()
            split_row.setSpacing(14)
            left_widget = QWidget()
            left_lay = QVBoxLayout(left_widget)
            left_lay.setContentsMargins(0, 0, 0, 0)
            left_lay.setSpacing(10)

            my_tid_g = league_info["my_team_id"]
            direct_cut = league_info["direct_cut"]
            playoff_cut = league_info["playoff_cut"]
            lbl_g = QLabel("◼ 리그 스테이지")
            lbl_g.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
            left_lay.addWidget(lbl_g)

            rows = league_info["standings"]
            gt = QTableWidget(len(rows), 8)
            gt.setHorizontalHeaderLabels(["순위", "팀", "경기", "승", "무", "패", "득실", "승점"])
            gt.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            gt.verticalHeader().setVisible(False)
            gt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # [2026-07 버그수정, 신민용 리포트: "바르셀로나... 처럼 이름이
            # 잘린다"] 팀명 컬럼(1번)이 Stretch라 left_widget의 고정 최대폭
            # (기존 460)에 맞춰 긴 이름이 그냥 잘렸다. ResizeToContents로
            # 바꿔서 테이블이 이름 길이만큼 필요한 폭을 요구하게 하고,
            # 그 폭에 맞춰 왼쪽 컬럼·창을 늘린다(아래 결과 채운 뒤 처리).
            gt.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            gt.setStyleSheet(
                "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:1px solid #2a2a2a;}"
                "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")

            for i, r in enumerate(rows):
                gd = r["gf"] - r["ga"]
                ctry = r.get("country", "")
                nm = f"{r.get('flag','')}{r['team_name']}"
                if ctry:
                    nm = f"{nm} ({ctry})"
                vals = [str(i + 1), nm, str(r["p"]), str(r["w"]),
                        str(r["d"]), str(r["l"]), f"{'+' if gd>0 else ''}{gd}", str(r["pts"])]

                # 색상: 초록=직행권, 주황=플레이오프권, 청록=내 팀, 회색=광탈권
                if r["team_id"] == my_tid_g:
                    color = COLOR_MY
                elif i < direct_cut:
                    color = COLOR_ADVANCE
                elif i < playoff_cut:
                    color = COLOR_THIRD
                else:
                    color = COLOR_ELIM

                for j, v in enumerate(vals):
                    item = QTableWidgetItem(v)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(color)
                    # [2026-08 신설, 신민용 리포트: "복사하면 국기/국가까지
                    # 같이 복사된다"] 팀명 칸(1번)만 순수 팀명으로 복사되게.
                    if j == 1:
                        item.setData(_CLEAN_TEXT_ROLE, r["team_name"])
                    gt.setItem(i, j, item)

            gt.setFixedHeight(gt.verticalHeader().defaultSectionSize() * len(rows) + 28)
            gt.resizeColumnsToContents()
            _need_w = sum(gt.columnWidth(j) for j in range(gt.columnCount())) + 24
            gt.setMinimumWidth(_need_w)
            left_widget.setMinimumWidth(_need_w)
            _enable_plain_copy(gt)
            left_lay.addWidget(gt)

            hint = QLabel(f"🟢1~{direct_cut}위 직행  🟡{direct_cut+1}~{playoff_cut}위 플레이오프  "
                          f"🔵내 팀  ⬜{playoff_cut+1}위 이하 광탈")
            hint.setStyleSheet("color:#666;font-size:10px;margin-top:4px;")
            left_lay.addWidget(hint)
            left_lay.addStretch()

            # 우측: 리그 스테이지 경기 일정('누가 언제 붙는지')
            right_widget = QWidget()
            right_lay = QVBoxLayout(right_widget)
            right_lay.setContentsMargins(0, 0, 0, 0)
            right_lay.setSpacing(6)
            lbl_sched = QLabel("◼ 경기 일정")
            lbl_sched.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
            right_lay.addWidget(lbl_sched)

            fixture_rows = []
            for m in matches:
                if m.get("stage_raw") != "league":
                    continue
                home_nm = m["home_name"] + (f" ({m['home_league']})" if m.get("home_league") else "")
                away_nm = m["away_name"] + (f" ({m['away_league']})" if m.get("away_league") else "")
                fixture_rows.append({
                    "week": m["week"], "day": m.get("day"), "home": home_nm, "away": away_nm,
                    "home_score": m["home_score"], "away_score": m["away_score"],
                    "pso_winner": m["pso_winner"],
                    "is_my": m["home_id"] == my_team_id or m["away_id"] == my_team_id,
                })
            right_lay.addWidget(self._build_grouped_fixture_column(
                fixture_rows, {}, None, p, st["current_year"],
                group_key="week", group_label_fmt="{}주차"))
            right_lay.addStretch()

            split_row.addWidget(left_widget, 0)
            split_row.addWidget(right_widget, 1)
            lay.addLayout(split_row)

            # [2026-07 버그수정] left_widget의 고정 maxWidth(460)를 없앤 대신,
            # 실제로 필요한 폭(_need_w, 팀명 길이 반영)을 창 크기 계산에
            # 반영한다 — _build_grouped_fixture_column 내부의 자체 resize는
            # 왼쪽 폭을 고정 360으로 가정해서, 이름이 길어 왼쪽이 그보다
            # 넓어지면 그 가정이 틀려 창이 다시 좁게 잡힐 수 있다.
            _want_total = _need_w + getattr(self, "_max_fixture_w_seen", 0) + 60
            if _want_total > self.width():
                from PyQt6.QtWidgets import QApplication
                scr = QApplication.primaryScreen()
                max_w = (scr.availableGeometry().width() - 40) if scr else 1600
                self.resize(min(_want_total, max_w), self.height())

        # ── 토너먼트 브래킷 (플레이오프 포함) ──
        from ui.bracket_widget import BracketWidget, build_rounds_from_matches
        # [2026-07 버그수정, 신민용 리포트: "북남미 챔피언스리그 대진표가
        # 뒤죽박죽이다 — 32강이 8강 뒤에 와있다"] 이 표에 "32강"이 아예
        # 빠져있었다 — build_rounds_from_matches가 정렬 기준을
        # stage_order.get(stage, 99)로 찾는데, "32강"이 여기 없어서 항상
        # 99(맨 뒤)로 밀려나 실제로는 제일 먼저 열리는 라운드인데 화면상
        # 결승보다도 뒤에(사실상 맨 끝에) 그려졌다. 북남미/남미처럼 참가
        # 규모가 커서 32강부터 시작하는 대회도 있으므로 여기 추가한다.
        stage_order = {"플레이오프": 0, "32강": 1, "16강": 2, "8강": 3, "4강": 4,
                        "결승": 5, "3/4위전": 5}

        bracket_matches = []
        for m in matches:
            # 리그 스테이지는 위에서 이미 순위표로 보여줬으니 브래킷에서는 제외.
            # 플레이오프(PO)부터는 진짜 토너먼트라 브래킷에 포함한다.
            if m.get("stage_raw") == "league" or m["stage"] == "리그 스테이지":
                continue
            hs, as_ = m["home_score"], m["away_score"]
            played  = hs is not None and hs >= 0
            if played:
                winner = m["pso_winner"] or (m["home_name"] if hs > as_ else m["away_name"])
            else:
                winner = ""
            if m["home_id"] == my_team_id:
                my_side = "home"
            elif m["away_id"] == my_team_id:
                my_side = "away"
            else:
                my_side = None
            home_nm = m["home_name"]
            away_nm = m["away_name"]
            h_ctry = m.get("home_league", "")
            a_ctry = m.get("away_league", "")
            if h_ctry:
                home_nm = f"{home_nm} ({h_ctry})"
            if a_ctry:
                away_nm = f"{away_nm} ({a_ctry})"
            if played:
                if winner == m["away_name"]:
                    winner = away_nm
                else:
                    winner = home_nm
            bracket_matches.append({
                "stage": m["stage"], "week": m["week"],
                "home": home_nm, "away": away_nm,
                "home_flag": "", "away_flag": "",
                "hs": hs if played else -1, "as_": as_ if played else -1,
                "winner": winner, "pso": m["pso_score"] if m["pso_winner"] else "",
                "my_side": my_side,
            })

        if mode == "ko" and not bracket_matches:
            return None

        if mode == "ko" and bracket_matches:
            lbl_t = QLabel("◼ 토너먼트")
            lbl_t.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
            lay.addWidget(lbl_t)
            rounds = build_rounds_from_matches(bracket_matches, stage_order)
            bracket = BracketWidget(rounds)
            lay.addWidget(bracket)
            self._fit_to_bracket(bracket)

        lay.addStretch()
        outer.setWidget(body)
        return outer

    def _fit_to_bracket(self, bracket):
        from PyQt6.QtWidgets import QApplication
        sh = bracket.sizeHint()
        want_w = sh.width()  + 80
        want_h = sh.height() + 200
        scr = QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        max_w = avail.width()  - 40 if avail else 1600
        max_h = avail.height() - 80 if avail else 1000
        target_w = min(max(self.width(),  want_w), max_w)
        target_h = min(max(self.height(), want_h), max_h)
        if target_w > self.width() or target_h > self.height():
            self.resize(target_w, target_h)

    def _make_cwc_tab(self):
        """[2026-07 신설, 신민용 리포트: "클럽월드컵이 경기 일정에 안 뜬다"]
        [2026-07 개편, 신민용 리포트: "클럽월드컵이 컵대회처럼 단순 표로
        뜨는데, 국제대회 예선처럼 좌측 조별 순위표 / 우측 조별 일정으로
        나눠서 보고 싶다"] 조별리그 8개조×4팀 구조는 국제대회 조별리그와
        똑같은 형태이므로, 컵대회식 단순 표 대신 _make_intl_tab의 "groups"
        모드와 같은 2단 분할 레이아웃(좌: 조별 순위표 / 우: 조별 일정,
        _build_grouped_fixture_column 재사용)으로 통일한다. 토너먼트 단계는
        여전히 _make_cwc_bracket_tab에서 따로 그린다."""
        from competition import club_world_cup_engine as cwe
        from game_engine import get_state, get_player
        from PyQt6.QtWidgets import QScrollArea

        st = get_state()
        p = get_player()
        if not st or not p or not p.get("current_team_id"):
            return None

        conn = get_conn()
        t = conn.execute(
            "SELECT * FROM cwc_tournaments WHERE year=? AND my_in=1",
            (st["current_year"],)).fetchone()
        if not t:
            conn.close()
            return None
        t = dict(t)
        groups = [r["grp"] for r in conn.execute(
            "SELECT DISTINCT grp FROM cwc_entries WHERE tournament_id=? ORDER BY grp",
            (t["id"],)).fetchall()]
        grp_match_rows = [dict(r) for r in conn.execute(
            """SELECT * FROM cwc_matches WHERE tournament_id=? AND stage='group'
               ORDER BY week, grp, id""", (t["id"],)).fetchall()]
        entries = {e["team_id"]: dict(e) for e in conn.execute(
            "SELECT team_id, team_name, country FROM cwc_entries WHERE tournament_id=?",
            (t["id"],)).fetchall()}
        conn.close()
        if not groups:
            return None

        my_tid = p["current_team_id"]

        outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        hdr = QLabel(f"🌍 {t['year']}년 클럽 월드컵 (조별리그)" +
                     (f"  —  현재 성적: {t['my_result']}" if t.get("my_result") else ""))
        hdr.setStyleSheet("color:#4dd2ff;font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)

        # ── 좌: 조별 순위표 / 우: 조별 경기 일정 (2026-07 국제대회 탭과 동일한 2단 레이아웃) ──
        split_row = QHBoxLayout()
        split_row.setSpacing(14)
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        lbl_g = QLabel("◼ 조별리그")
        lbl_g.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
        left_lay.addWidget(lbl_g)

        # [2026-07] 팀명 컬럼은 국가명까지 붙어 길어질 수 있어(예: "레알
        # 마드리드(스페인)") Stretch 대신 ResizeToContents로 실제 필요한
        # 폭을 요구하게 하고, 그 폭에 맞춰 왼쪽 컬럼과 창을 넓힌다 —
        # _build_grouped_fixture_column에서 이미 쓰던 것과 동일한 패턴.
        _max_left_w = 0
        for g in groups:
            rows = cwe.get_cwc_group_standings(t["id"], g)
            gt = QTableWidget(len(rows), 7)
            gt.setHorizontalHeaderLabels([f"{g}조", "경기", "승", "무", "패", "득실", "승점"])
            gt.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            gt.verticalHeader().setVisible(False)
            gt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            gt.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            gt.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            gt.setStyleSheet(
                "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:1px solid #2a2a2a;}"
                "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")

            for i, r in enumerate(rows):
                name = cwe.team_display(r["team_name"], r["country"])
                gd = r["gf"] - r["ga"]
                vals = [name, str(r["p"]), str(r["w"]), str(r["d"]), str(r["l"]),
                        f"{'+' if gd>0 else ''}{gd}", str(r["pts"])]
                is_my = r["team_id"] == my_tid
                color = COLOR_MY if is_my else (COLOR_ADVANCE if i < 2 else COLOR_ELIM)
                for j, v in enumerate(vals):
                    item = QTableWidgetItem(v)
                    if j > 0: item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(color)
                    gt.setItem(i, j, item)

            gt.setFixedHeight(gt.verticalHeader().defaultSectionSize() * max(len(rows), 1) + 28)
            gt.resizeColumnsToContents()
            _need_w = sum(gt.columnWidth(j) for j in range(gt.columnCount())) + 24
            gt.setMinimumWidth(_need_w)
            _max_left_w = max(_max_left_w, _need_w)
            left_lay.addWidget(gt)

        hint = QLabel("🟢조 1~2위(16강 진출)  🔵내 팀  ⬜조 3~4위(탈락)")
        hint.setStyleSheet("color:#666;font-size:10px;margin-top:4px;")
        left_lay.addWidget(hint)
        left_lay.addStretch()
        left_widget.setMinimumWidth(_max_left_w)

        # 우측: 조별 경기 일정
        right_widget = QWidget()
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)
        lbl_sched = QLabel("◼ 경기 일정")
        lbl_sched.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
        right_lay.addWidget(lbl_sched)

        fixture_rows = []
        for m in grp_match_rows:
            he = entries.get(m["home_team_id"])
            ae = entries.get(m["away_team_id"])
            fixture_rows.append({
                "week": m["week"], "day": m.get("day"), "grp": m["grp"],
                "home": cwe.team_display(he["team_name"], he["country"]) if he else "?",
                "away": cwe.team_display(ae["team_name"], ae["country"]) if ae else "?",
                "home_score": m["home_score"], "away_score": m["away_score"],
                "pso_winner": m["pso_winner"],
                "is_my": m["home_team_id"] == my_tid or m["away_team_id"] == my_tid,
            })
        right_lay.addWidget(self._build_grouped_fixture_column(
            fixture_rows, {}, None, p, t["year"],
            group_key="grp", group_label_fmt="{}조 일정"))
        right_lay.addStretch()

        split_row.addWidget(left_widget, 0)
        split_row.addWidget(right_widget, 1)
        lay.addLayout(split_row)
        lay.addStretch()

        _want_total = _max_left_w + getattr(self, "_max_fixture_w_seen", 0) + 60
        if _want_total > self.width():
            from PyQt6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
            max_w = (scr.availableGeometry().width() - 40) if scr else 1600
            self.resize(min(_want_total, max_w), self.height())

        outer.setWidget(body)
        return outer

    def _make_cwc_bracket_tab(self):
        """[2026-07 신설, 신민용 확정: "클럽월드컵도 기본탭/본선으로 나눠야"]
        16강~결승/3·4위전을 챔스·컵대회와 같은 토너먼트 대진표로 보여준다."""
        from competition import club_world_cup_engine as cwe
        from game_engine import get_state, get_player

        st = get_state()
        p = get_player()
        if not st or not p or not p.get("current_team_id"):
            return None

        conn = get_conn()
        t = conn.execute(
            "SELECT * FROM cwc_tournaments WHERE year=? AND my_in=1",
            (st["current_year"],)).fetchone()
        if not t:
            conn.close()
            return None
        t = dict(t)

        my_tid = p["current_team_id"]
        rows = [dict(r) for r in conn.execute(
            """SELECT * FROM cwc_matches WHERE tournament_id=?
               AND stage IN ('R16','QF','SF','TP','F')
               ORDER BY CASE stage WHEN 'R16' THEN 0 WHEN 'QF' THEN 1
                        WHEN 'SF' THEN 2 WHEN 'TP' THEN 3 WHEN 'F' THEN 3 END,
               id ASC""", (t["id"],)).fetchall()]
        if not rows:
            conn.close()
            return None

        entries = {r["team_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM cwc_entries WHERE tournament_id=?", (t["id"],)).fetchall()}
        conn.close()

        def _nm(team_id):
            if not team_id:
                return "미정"   # _precreate_cwc_ko_shell의 placeholder(0) — 아직 진출팀 미정
            e = entries.get(team_id)
            return cwe.team_display(e["team_name"], e["country"]) if e else "?"

        stage_ko = {"R16": "16강", "QF": "8강", "SF": "4강", "F": "결승", "TP": "3/4위전"}
        stage_order = {"R16": 0, "QF": 1, "SF": 2, "F": 3, "TP": 3}

        bracket_matches = []
        for m in rows:
            hs, as_ = m["home_score"], m["away_score"]
            played = hs is not None and hs >= 0
            home_nm, away_nm = _nm(m["home_team_id"]), _nm(m["away_team_id"])
            if played:
                winner_id = m["pso_winner"] or (m["home_team_id"] if hs > as_ else m["away_team_id"])
                winner = home_nm if winner_id == m["home_team_id"] else away_nm
            else:
                winner = ""
            if m["home_team_id"] == my_tid: my_side = "home"
            elif m["away_team_id"] == my_tid: my_side = "away"
            else: my_side = None
            bracket_matches.append({
                "stage": stage_ko.get(m["stage"], m["stage"]), "week": m["week"],
                "home": home_nm, "away": away_nm, "home_flag": "", "away_flag": "",
                "hs": hs if played else -1, "as_": as_ if played else -1,
                "winner": winner, "pso": m["pso_score"] if m["pso_winner"] else "",
                "my_side": my_side,
            })

        from PyQt6.QtWidgets import QScrollArea
        from ui.bracket_widget import BracketWidget, build_rounds_from_matches

        outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        hdr = QLabel(f"🌍 {t['year']}년 클럽 월드컵  (본선: 16강~)" +
                     (f"  —  현재 성적: {t['my_result']}" if t.get("my_result") else ""))
        hdr.setStyleSheet("color:#4dd2ff;font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)

        lbl_t = QLabel("◼ 토너먼트")
        lbl_t.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
        lay.addWidget(lbl_t)

        stage_order_named = {stage_ko.get(k, k): v for k, v in stage_order.items()}
        rounds = build_rounds_from_matches(bracket_matches, stage_order_named)
        bracket = BracketWidget(rounds)
        lay.addWidget(bracket)
        self._fit_to_bracket(bracket)

        lay.addStretch()
        outer.setWidget(body)
        return outer

    def _make_po_tab(self):
        """[2026-07 신설, 신민용 리포트: "경기 일정 창에 승강전 탭이 안
        뜬다"] 클럽월드컵 본선 탭과 동일한 패턴 — 44주에 내 팀이 승강
        플레이오프에 걸렸으면(브래킷 어느 자리든) 대진표로 보여준다."""
        import promotion_playoff_engine as ppe
        from game_engine import get_state, get_player

        st = get_state()
        p = get_player()
        if not st or not p or not p.get("current_team_id"):
            return None

        t = ppe.get_my_po_tournament(p["current_team_id"], st["current_year"])
        if not t:
            return None

        bracket_matches = ppe.get_po_bracket_matches(t["id"])
        if not bracket_matches:
            return None

        from PyQt6.QtWidgets import QScrollArea
        from ui.bracket_widget import BracketWidget, build_rounds_from_matches

        outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        hdr = QLabel(f"⚖ {t['year']}년 승강 플레이오프  (44주)")
        hdr.setStyleSheet("color:#ffee55;font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)

        lbl_t = QLabel("◼ 대진표")
        lbl_t.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
        lay.addWidget(lbl_t)

        # bracket_matches의 stage는 이미 한글("예선"/"준결승"/"하위리그
        # 결승"/"최종 승강전")이라 stage_order도 한글 키로 바로 준다 —
        # promotion_playoff_engine.get_po_bracket_matches 참고.
        stage_order_named = {"예선": 0, "준결승": 0, "하위리그 결승": 1, "최종 승강전": 2}
        rounds = build_rounds_from_matches(bracket_matches, stage_order_named)
        bracket = BracketWidget(rounds)
        lay.addWidget(bracket)
        self._fit_to_bracket(bracket)

        lay.addStretch()
        outer.setWidget(body)
        return outer

    def _make_cup_tab(self, my_view=False):
        """[2026-07 신설] 국내 컵대회(FA컵식) 일정/결과 탭. 챔스처럼 별도
        브래킷 위젯 대신, 라운드가 유동적(팀 수에 따라 N라운드로 이름이
        달라짐)이라 간단한 표로 보여준다 — 라운드/홈팀/스코어/원정팀/결과.

        [2026-07 수정, 신민용 리포트: "컵대회도 내 경기 전체일정처럼 나누는
        게 시각적으로 더 좋지 않아?"] 예전엔 탭 하나에 그 대회 전체 경기를
        (초반 라운드는 팀이 많아 수십 줄) 다 우겨넣고 내 경기만 보라색으로
        구분했는데, 리그 일정 탭처럼 '내 경기'/'전체 일정'으로 아예 탭을
        나눈다 — my_view=True면 내가 낀 경기만 필터링해서 보여준다."""
        from competition import cup_engine
        from game_engine import get_state, get_player, day_to_full_date_str

        st = get_state()
        p = get_player()
        if not st or not p or not p.get("current_team_id"):
            return None

        t = cup_engine._my_cup_tournament(p, st["current_year"])
        if not t:
            return None

        conn = get_conn()
        rows = conn.execute(
            """SELECT * FROM cup_matches WHERE tournament_id=?
               ORDER BY round_idx ASC, slot ASC""", (t["id"],)).fetchall()
        conn.close()
        if not rows:
            return None

        # [2026-08 계측 추가] '전체 일정'(my_view=False) 행 수를 _fill_tabs의
        # [PERF-SCHED] 로그에 같이 찍기 위해 저장해둔다 — 팀 수 증가가 실제로
        # 이 탭 크기와 비례하는지 실측으로 확인하기 위함(추측 금지).
        if not my_view:
            self._last_cup_all_rows = len(rows)

        my_tid = p["current_team_id"]
        if my_view:
            rows = [r for r in rows
                    if r["home_team_id"] == my_tid or r["away_team_id"] == my_tid]
            if not rows:
                return None

        outer = QWidget()
        lay = QVBoxLayout(outer)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        hdr = QLabel(f"🎖️ {t['year']}년 {t['name']}" +
                     (f"  —  현재 성적: {t['my_result']}" if t.get("my_result") else "") +
                     ("  (내 경기)" if my_view else "  (전체 일정)"))
        hdr.setStyleSheet("color:#c48aff;font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)

        cols = ["라운드", "날짜", "홈팀", "스코어", "원정팀", "결과"]
        tbl = QTableWidget(len(rows), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        tbl.setStyleSheet(
            "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}"
            "QHeaderView::section{background:#252525;color:#888;border:none;padding:4px;}")

        conn = get_conn()
        # [2026-08 최적화, 신민용 요청] 경기 행마다 cup_entries를 2번씩(홈/원정)
        # 개별 조회하던 N+1 쿼리를, 대회 참가팀 전체를 1회 조회해 team_id→
        # (team_name,tier) 딕셔너리로 캐싱하는 방식으로 바꿨다. 순수 UI
        # 렌더링(화면 표시용 문자열 조립)이라 RNG나 시뮬레이션 결과와는
        # 전혀 무관해 순서를 신경 쓸 필요가 없다.
        entry_map = {
            er["team_id"]: (er["team_name"], er["tier"])
            for er in conn.execute(
                "SELECT team_id, team_name, tier FROM cup_entries WHERE tournament_id=?",
                (t["id"],)).fetchall()
        }
        for i, r in enumerate(rows):
            he = entry_map.get(r["home_team_id"])
            ae = entry_map.get(r["away_team_id"])
            hn = f"{he[0]} ({he[1]}부)" if he else "?"
            an = f"{ae[0]} ({ae[1]}부)" if ae else "?"
            played = r["home_score"] != -1
            score = f"{r['home_score']}-{r['away_score']}" if played else "예정"
            if played and r["pso_winner"]:
                score += f" (승부차기 {r['pso_score']})"
            is_my = r["home_team_id"] == my_tid or r["away_team_id"] == my_tid
            if not played:
                result = ""
            elif r["pso_winner"]:
                result = "승" if r["pso_winner"] == my_tid else "패"
            else:
                w = r["home_team_id"] if r["home_score"] > r["away_score"] else r["away_team_id"]
                result = "승" if w == my_tid else ("무" if r["home_score"] == r["away_score"] else "패")
            from game_engine import _week_intl_cl_day
            # [2026-07 버그 수정] 저장된 day가 있으면 그대로 쓰고, 없을
            # 때만(컵대회는 아직 Phase 2 생성 시점 day 배정 전이라 대부분
            # 이 경로) 예전 방식으로 근사.
            # [2026-07 버그 수정] rows가 sqlite3.Row라 .get()이 없음
            # (AttributeError). "day" in r.keys()로 존재 여부 확인 후 접근.
            _stored_day = r["day"] if ("day" in r.keys() and r["day"]) else None
            _cup_day = _stored_day or (_week_intl_cl_day(r["week"], p) if r["week"] is not None else 1)
            date_str = day_to_full_date_str(t["year"], _cup_day)
            # my_view 탭은 어차피 전부 내 경기라서 결과를 항상 보여주고,
            # 전체 일정 탭은 예전처럼 내 경기가 아니면 결과란을 비운다.
            vals = [r["round_name"], date_str, hn, score, an, result if (my_view or is_my) else ""]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if j in (0, 1, 3, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if is_my:
                    item.setForeground(QColor("#c48aff"))
                tbl.setItem(i, j, item)
        conn.close()
        lay.addWidget(tbl)
        return outer

    def _make_cup_bracket_tab(self):
        """[2026-07 신설, 개편] 국내 컵대회를 챔피언스/국제대회와 같은
        토너먼트 대진표(브래킷)로 보여준다. 컵대회는 하위 티어부터
        시작해 라운드가 진행될수록 상위 티어가 합류하는 '단계적 합류'
        구조라, 초반 라운드는 참가팀 수가 뒤죽박죽이고 라운드명도
        32강/16강처럼 정형화되지 않는다(_round_name 참고) — 이 상태로
        전체 라운드를 브래킷 하나에 다 우겨넣으면 화면이 지저분해진다는
        지적을 받아, 이 탭은 대진이 안정된 이후인 '8강부터'만(=본선)
        그린다. 그 이전 라운드는 기존 '🎖️ 컵대회' 표 탭에서 계속 볼 수 있다."""
        from competition import cup_engine
        from game_engine import get_state, get_player

        st = get_state()
        p = get_player()
        if not st or not p or not p.get("current_team_id"):
            return None

        t = cup_engine._my_cup_tournament(p, st["current_year"])
        if not t:
            return None

        my_tid = p["current_team_id"]
        conn = get_conn()
        rows = [dict(r) for r in conn.execute(
            """SELECT * FROM cup_matches WHERE tournament_id=?
               AND round_name IN ('8강', '4강', '결승', '3·4위전')
               ORDER BY round_idx ASC, id ASC""", (t["id"],)).fetchall()]
        if not rows:
            conn.close()
            return None

        entries = {r["team_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM cup_entries WHERE tournament_id=?", (t["id"],)).fetchall()}
        conn.close()

        def _nm(team_id):
            e = entries.get(team_id)
            if not e:
                return "?"
            return f"{e['team_name']} ({e['tier']}부)"

        # 라운드 순서 고정(8강→4강→결승, 3·4위전은 결승과 같은 주차라 옆에 배치).
        stage_order = {"8강": 0, "4강": 1, "결승": 2, "3·4위전": 2}

        bracket_matches = []
        for m in rows:
            hs, as_ = m["home_score"], m["away_score"]
            played = hs is not None and hs >= 0
            home_nm = _nm(m["home_team_id"])
            away_nm = _nm(m["away_team_id"])
            if played:
                winner_id = m["pso_winner"] or (m["home_team_id"] if hs > as_ else m["away_team_id"])
                winner = home_nm if winner_id == m["home_team_id"] else away_nm
            else:
                winner = ""
            if m["home_team_id"] == my_tid:
                my_side = "home"
            elif m["away_team_id"] == my_tid:
                my_side = "away"
            else:
                my_side = None
            bracket_matches.append({
                "stage": m["round_name"], "week": m["week"],
                "home": home_nm, "away": away_nm,
                "home_flag": "", "away_flag": "",
                "hs": hs if played else -1, "as_": as_ if played else -1,
                "winner": winner, "pso": m["pso_score"] if m["pso_winner"] else "",
                "my_side": my_side,
            })

        from PyQt6.QtWidgets import QScrollArea
        from ui.bracket_widget import BracketWidget, build_rounds_from_matches

        outer = QScrollArea()
        outer.setWidgetResizable(True)
        outer.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(10)

        hdr = QLabel(f"🎖️ {t['year']}년 {t['name']}  (본선: 8강~)" +
                     (f"  —  현재 성적: {t['my_result']}" if t.get("my_result") else ""))
        hdr.setStyleSheet("color:#c48aff;font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)

        lbl_t = QLabel("◼ 토너먼트")
        lbl_t.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
        lay.addWidget(lbl_t)

        rounds = build_rounds_from_matches(bracket_matches, stage_order)
        bracket = BracketWidget(rounds)
        lay.addWidget(bracket)
        self._fit_to_bracket(bracket)

        lay.addStretch()
        outer.setWidget(body)
        return outer

    def _build_grouped_fixture_column(self, rows, flags, nat, p, year,
                                       group_key="grp", group_label_fmt="{}조"):
        """[2026-07 개편] '경기 일정'을 좌측 조별 순위표와 같은 톤으로,
        그룹(또는 라운드)별 카드로 나눠서 보여준다. 예전엔 표 하나에
        모든 그룹이 뒤섞여 있어서 좌측과 스타일이 안 맞고 알아보기
        어려웠다. 날짜도 'N주차' 대신 실제 달력 날짜로 표시한다
        (컵대회 탭에서 쓰던 _week_intl_cl_day + day_to_full_date_str 조합
        재사용 — 그 주 국제/컵 경기가 실제로 열리는 요일을 그대로 따름)."""
        from game_engine import _week_intl_cl_day, day_to_full_date_str

        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        by_group = {}
        for m in rows:
            by_group.setdefault(m.get(group_key), []).append(m)

        for key in sorted(by_group.keys(), key=lambda k: (k is None, k)):
            group_rows = by_group[key]
            label_txt = group_label_fmt.format(key) if key not in (None, "") else "일정"
            lbl = QLabel(label_txt)
            lbl.setStyleSheet("color:#888;font-size:11px;font-weight:bold;")
            col.addWidget(lbl)

            tbl = QTableWidget(len(group_rows), 4)
            tbl.setHorizontalHeaderLabels(["날짜", "홈", "스코어", "원정"])
            tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tbl.verticalHeader().setVisible(False)
            tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            # [2026-07 버그수정, 신민용 리포트] 예전엔 홈/원정 컬럼이 Stretch라
            # 창 폭에 맞춰 팀명이 그냥 잘렸다("...") — ResizeToContents로 바꿔서
            # 테이블 자체가 팀명 길이만큼 필요한 폭을 요구하게 하고, 아래에서
            # 창 크기를 그 폭에 맞춰 늘려준다.
            tbl.setStyleSheet(
                "QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:1px solid #2a2a2a;}"
                "QHeaderView::section{background:#252525;color:#888;border:none;padding:3px;}")

            for i, m in enumerate(group_rows):
                home, away = m["home"], m["away"]
                home_flag = flags.get(home, "")
                away_flag = flags.get(away, "")
                hs, as_ = m["home_score"], m["away_score"]
                played = hs is not None and hs >= 0
                score = f"{hs}-{as_}" if played else "예정"
                if played and m.get("pso_winner"):
                    score += "(PSO)"
                is_my = m["is_my"] if "is_my" in m else bool(nat and nat in (home, away))
                # [2026-07 버그 수정] 예전엔 m["day"](Phase 2에서 생성 시점에
                # 미리 계산해둔 실제 날짜)를 무시하고 매번 _week_intl_cl_day로
                # 새로 계산했다 — 여러 라운드가 같은 week 번호를 공유하는
                # 경우(예선처럼 라운드 간격이 7일 미만) 서로 다른 라운드가
                # 같은 날짜로 뭉쳐 보이는 원인이었다. 이제 저장된 day가
                # 있으면 그대로 쓰고, 없을 때(옛 세이브 등)만 예전 방식으로
                # 근사한다.
                day = m.get("day") or _week_intl_cl_day(m["week"], p)
                date_str = day_to_full_date_str(year, day)
                vals = [date_str, f"{home_flag}{home}", score, f"{away_flag}{away}"]
                for j, v in enumerate(vals):
                    item = QTableWidgetItem(v)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    if is_my:
                        item.setForeground(COLOR_MY)
                    elif not played:
                        item.setForeground(COLOR_PENDING)
                    tbl.setItem(i, j, item)
            tbl.setFixedHeight(tbl.verticalHeader().defaultSectionSize() * max(len(group_rows), 1) + 28)
            tbl.resizeColumnsToContents()
            _need_w = sum(tbl.columnWidth(j) for j in range(tbl.columnCount())) + 40
            tbl.setMinimumWidth(_need_w)
            col.addWidget(tbl)
            _max_fixture_w = max(getattr(self, "_max_fixture_w_seen", 0), _need_w)
            self._max_fixture_w_seen = _max_fixture_w

        # [2026-07 버그수정] 위에서 계산한 '팀명이 안 잘리는 최소 폭'만큼
        # 창이 좁으면 늘려준다 — 왼쪽 순위표 폭(대략 320)까지 감안.
        _want_total = getattr(self, "_max_fixture_w_seen", 0) + 360
        if _want_total > self.width():
            from PyQt6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
            max_w = (scr.availableGeometry().width() - 40) if scr else 1600
            self.resize(min(_want_total, max_w), self.height())

        return box

    def _make_table(self, data, my_view=True):
        w   = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        cols = ["날짜", "홈팀", "스코어", "원정팀", "승패"]
        tbl  = QTableWidget(len(data), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setStyleSheet("""
            QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}
            QHeaderView::section{background:#252525;color:#888;border:none;padding:4px;}
        """)

        # [2026-08 최적화, 신민용 리포트: "43→44→45주, 52→1주 렉 없애자"]
        # 예전엔 이 아래 for문 안에서 매 행마다 `from constants import ...`
        # (그리고 심지어 get_state는 game_engine에서 또)를 실행하고 있었다 —
        # "전체 일정" 탭은 리그 시즌 전체 경기(수백 건)를 담으므로 행마다
        # import 문을 도는 오버헤드가 그대로 쌓였다. get_state는 이미 이
        # 파일 맨 위에서 import돼 있어 그대로 재사용하면 되고, constants
        # 쪽 3개만 루프 밖으로 한 번만 꺼내둔다. 결과는 완전히 동일하다.
        from constants import day_to_full_date_str, day_to_week, DAYS_PER_WEEK

        for i, r in enumerate(data):
            hs  = r["home_score"]; as_ = r["away_score"]
            score   = f"{hs} - {as_}" if hs >= 0 else "예정"
            is_my   = r["home_team_id"]==self.my_team_id or r["away_team_id"]==self.my_team_id
            played  = hs >= 0

            if not played or not is_my:
                col_wdl = ""
                wdl_color = "#555555"
            else:
                if r["home_team_id"] == self.my_team_id:
                    col_wdl = "승" if hs>as_ else ("무" if hs==as_ else "패")
                else:
                    col_wdl = "패" if hs>as_ else ("무" if hs==as_ else "승")
                wdl_color = "#00cc44" if col_wdl=="승" else ("#888888" if col_wdl=="무" else "#cc4444")

            if not played:
                row_color = QColor("#555555")
            elif is_my:
                if col_wdl == "승":   row_color = QColor("#00cc44")
                elif col_wdl == "무": row_color = QColor("#888888")
                else:                  row_color = QColor("#cc4444")
            else:
                row_color = QColor("#aaaaaa")

            _day = r["day"] if r["day"] else (r["week"] - 1) * DAYS_PER_WEEK + 1
            if r["year"]:
                _base_year = r["year"]
            else:
                _st = get_state()
                _base_year = _st["current_year"] if _st else 2000
            vals = [day_to_full_date_str(_base_year, _day), r.get("home_name",""), score,
                    r.get("away_name",""), col_wdl]

            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 4:
                    item.setForeground(QColor(wdl_color))
                else:
                    item.setForeground(row_color)
                tbl.setItem(i, j, item)

        # [2026-08 최적화, 신민용 리포트: "43→44→45주, 52→1주 렉 없애자"]
        # ResizeToContents 모드를 행 채우기 "전"에 걸어두면 setItem() 호출
        # (전체 일정이면 시즌 전체 경기 수만큼, 수백 회)마다 Qt가 컬럼 폭을
        # 매번 다시 계산했다 — 행을 다 채운 뒤 모드를 적용하면 Qt가 그
        # 시점에 한 번만 계산해서 최종 폭·화면은 완전히 동일하다.
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(tbl)

        tbl.resizeColumnsToContents()
        total_w = sum(tbl.columnWidth(j) for j in range(tbl.columnCount())) + 60
        self.setMinimumWidth(max(700, total_w))
        if self.width() < total_w:
            self.resize(total_w, self.height())

        return w