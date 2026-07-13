"""
ui/schedule_window.py  ─  모달리스, 실시간 갱신
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QTabWidget, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from game_engine import get_schedule, get_player, get_state
from database import get_conn

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
    if kind not in ("world", "continent"):
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
        if p and p.get("current_team_id"):
            my_team_id = p["current_team_id"]
            conn = get_conn()
            row = conn.execute(
                "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (my_team_id,)).fetchone()
            conn.close()
            if row:
                league_id = row["id"]
        season = st["current_season"] if st else self.season
        sig = self._compute_sig(league_id, my_team_id, season)
        if sig == self._last_sig:
            return
        self.refresh()

    def refresh(self):
        p = get_player(); st = get_state()
        if p and p.get("current_team_id"):
            conn = get_conn()
            row = conn.execute(
                "SELECT l.id, l.name, l.tier FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (p["current_team_id"],)).fetchone()
            conn.close()
            if row:
                self.league_id = row["id"]
                lname = f"{row['name']} ({row['tier']}부)"
                self._lbl.setText(f"📅 {lname}")
            self.my_team_id = p["current_team_id"]
        if st: self.season = st["current_season"]
        self._fill_tabs()
        self._last_sig = self._compute_sig()

    def _fill_tabs(self):
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

        self._tab.addTab(self._make_table(my_data, my_view=True),  "내 경기")
        self._tab.addTab(self._make_table(all_data, my_view=False), "전체 일정")

        # 국제대회(본선) 탭
        intl_w = self._make_intl_tab("groups", qual=False)
        if intl_w:
            self._tab.addTab(intl_w, "🌍 국제대회")
        intl_ko = self._make_intl_tab("ko", qual=False)
        if intl_ko:
            self._tab.addTab(intl_ko, "🌍 국제대회(본선)")

        # 국제대회(예선) 탭
        qual_w = self._make_intl_tab("groups", qual=True)
        if qual_w:
            self._tab.addTab(qual_w, "🌏 국제대회(예선)")

        # 국제대회(예선 플레이오프) 탭 — PO 경기가 생성된 시점부터 표시
        qual_po_w = self._make_intl_tab("qual_po", qual=True)
        if qual_po_w:
            self._tab.addTab(qual_po_w, "🌏 국제대회(예선 플레이오프)")

        # 챔피언스리그 탭
        champs_w = self._make_champions_tab("groups")
        if champs_w:
            self._tab.addTab(champs_w, "🏆 챔피언스리그")
        champs_ko = self._make_champions_tab("ko")
        if champs_ko:
            self._tab.addTab(champs_ko, "🏆 챔피언스리그(본선)")

        # [2026-07 신설] 국내 컵대회 탭 — 예전엔 이 탭 자체가 없어서 컵
        # 경기가 로그에만 남고 일정 화면 어디에도 안 보였다.
        cup_w = self._make_cup_tab()
        if cup_w:
            self._tab.addTab(cup_w, "🎖️ 컵대회")
        # [2026-07 신설] 챔피언스리그·국제대회처럼 컵대회도 토너먼트
        # 대진표(브래킷)로 보여주는 탭 — 4강 이후 결승/3·4위전이 생기면서
        # 다른 대회들과 같은 방식으로 표시할 수 있게 됐다.
        cup_bracket_w = self._make_cup_bracket_tab()
        if cup_bracket_w:
            self._tab.addTab(cup_bracket_w, "🎖️ 컵대회(본선)")

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
        _hdr_color = "#ff9933" if t.get("kind") in ("world", "continent") else "#ff5555"
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

        if mode == "ko" and not ko_rows:
            return None
        if mode == "qual_po" and not ko_rows:
            return None

        # ── 조별리그 순위표 (좌: 조별 순위 / 우: 경기 일정, 2026-07 2단 레이아웃) ──
        if mode == "groups":
            split_row = QHBoxLayout()
            split_row.setSpacing(14)
            left_widget = QWidget()
            left_widget.setMaximumWidth(420)
            lay_orig = lay          # 아래 기존 코드가 'lay.addWidget(...)'를
            lay = QVBoxLayout(left_widget)   # 그대로 쓰도록 lay를 잠시 왼쪽 컬럼으로 바꿔치기
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)

            # 진출 기준 계산
            advance_n, has_thirds = _intl_advance_count(t)
            # 조별리그 종료 후 3위 진출 확정팀
            third_ok = _intl_third_qualified(t) if has_thirds else set()
            # 아직 진행 중이면 3위 후보 전체를 주황으로 표시
            thirds_in_progress = (has_thirds and t.get("status") == "group")

            lbl_g = QLabel("◼ 조별리그")
            lbl_g.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
            lay.addWidget(lbl_g)

            for g in groups:
                if _is_qual:
                    rows = intl_engine._qual_group_standings(t["id"], g)
                    for r in rows:
                        r.setdefault("w", 0); r.setdefault("d", 0); r.setdefault("l", 0)
                else:
                    rows = intl_engine.get_group_standings(t["id"], g)

                gt = QTableWidget(len(rows), 7)
                gt.setHorizontalHeaderLabels([f"{g}조", "경기", "승", "무", "패", "득실", "승점"])
                gt.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                gt.verticalHeader().setVisible(False)
                gt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                gt.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                gt.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                gt.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
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
                        gt.setItem(i, j, item)

                gt.setFixedHeight(gt.verticalHeader().defaultSectionSize() * len(rows) + 28)
                lay.addWidget(gt)

            # ── 3위 팀 순위표 (3위 진출 대회만: 48개국 월드컵/대륙컵) ──
            # 실제 2026 월드컵 중계처럼, 각 조 3위끼리 성적순으로 줄 세워서
            # 상위 N팀만 진출/나머지는 탈락인지 한눈에 보여준다.
            if has_thirds:
                third_rows = []
                for g in groups:
                    if _is_qual:
                        rows = intl_engine._qual_group_standings(t["id"], g)
                        for r in rows:
                            r.setdefault("w", 0); r.setdefault("d", 0); r.setdefault("l", 0)
                    else:
                        rows = intl_engine.get_group_standings(t["id"], g)
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
                    else:
                        from constants import CONT_BEST_THIRDS
                        n_adv3 = CONT_BEST_THIRDS

                    lbl_t = QLabel(f"◼ 3위 팀 순위 (상위 {n_adv3}팀 진출)")
                    lbl_t.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;margin-top:6px;")
                    lay.addWidget(lbl_t)

                    # 컷라인(진출/탈락 경계) 표시용 구분 행을 진출팀 수만큼 뒤에 끼워 넣는다.
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
                    tt.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
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
                    lay.addWidget(tt)

            # 범례
            hint_parts = ["🟢진출확정", "🔵내 국가"]
            if has_thirds:
                hint_parts.append("🟡3위진출경쟁")
                hint_parts.append("🟩3위진출확정")
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

            split_row.addWidget(left_widget, 0)
            split_row.addWidget(right_widget, 1)
            lay = lay_orig
            lay.addLayout(split_row)

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
                bracket_matches.append({
                    "stage": intl_engine.STAGE_KO.get(m["stage"], m["stage"]),
                    "week": m["week"],
                    "home": m["home"], "away": m["away"],
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
        return outer

    # ── 챔피언스리그 탭 ──────────────────────────

    def _make_champions_tab(self, mode="groups"):
        try:
            import champions_engine
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
        matches = champions_engine.get_my_champions_matches(st["current_year"])
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

        league_names = {
            "유럽": "유럽 챔피언스리그",
            "아시아": "아시안 챔피언스리그",
            "아프리카": "아프리카 챔피언스리그",
            "남미": "코파 리베르타도레스",
            "북남미": "북남미 챔피언스리그",
        }
        cont = team_info["continent"]
        from champions_engine import CONTINENT_MAP
        cl_cont = CONTINENT_MAP.get(cont, cont)
        league_name = league_names.get(cl_cont, f"{cl_cont} 챔피언스리그")

        hdr = QLabel(f"🏆 {st['current_year']}년 {league_name}")
        hdr.setStyleSheet("color:#ffcc00;font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)

        sub = QLabel(f"팀: {team_info['name']} ({team_info['league_name']})")
        sub.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(sub)

        # ── 리그 스테이지 순위표 (2026-07 스위스 방식 개편 - 조별리그 폐지) ──
        # [2026-07] 좌: 순위표 / 우: 경기 일정 2단 분할 — 국제대회 탭과 동일한 개편.
        league_info = champions_engine.get_my_cl_league_standings(st["current_year"])
        if mode == "groups" and league_info:
            split_row = QHBoxLayout()
            split_row.setSpacing(14)
            left_widget = QWidget()
            left_widget.setMaximumWidth(460)
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
            gt.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            gt.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
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
                    gt.setItem(i, j, item)

            gt.setFixedHeight(gt.verticalHeader().defaultSectionSize() * len(rows) + 28)
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
                    "week": m["week"], "home": home_nm, "away": away_nm,
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

        # ── 토너먼트 브래킷 (플레이오프 포함) ──
        from ui.bracket_widget import BracketWidget, build_rounds_from_matches
        stage_order = {"플레이오프": 0, "16강": 1, "8강": 2, "4강": 3, "결승": 4}

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

    def _make_cup_tab(self):
        """[2026-07 신설] 국내 컵대회(FA컵식) 일정/결과 탭. 챔스처럼 별도
        브래킷 위젯 대신, 라운드가 유동적(팀 수에 따라 N라운드로 이름이
        달라짐)이라 간단한 표로 보여준다 — 라운드/홈팀/스코어/원정팀/결과."""
        import cup_engine
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

        my_tid = p["current_team_id"]

        outer = QWidget()
        lay = QVBoxLayout(outer)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        hdr = QLabel(f"🎖️ {t['year']}년 {t['name']}" +
                     (f"  —  현재 성적: {t['my_result']}" if t.get("my_result") else ""))
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
        for i, r in enumerate(rows):
            he = conn.execute(
                "SELECT team_name, tier FROM cup_entries WHERE tournament_id=? AND team_id=?",
                (t["id"], r["home_team_id"])).fetchone()
            ae = conn.execute(
                "SELECT team_name, tier FROM cup_entries WHERE tournament_id=? AND team_id=?",
                (t["id"], r["away_team_id"])).fetchone()
            hn = f"{he['team_name']} ({he['tier']}부)" if he else "?"
            an = f"{ae['team_name']} ({ae['tier']}부)" if ae else "?"
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
            _cup_day = _week_intl_cl_day(r["week"], p) if r["week"] is not None else 1
            date_str = day_to_full_date_str(t["year"], _cup_day)
            vals = [r["round_name"], date_str, hn, score, an, result if is_my else ""]
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
        import cup_engine
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
            tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
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
                day = _week_intl_cl_day(m["week"], p)
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
            col.addWidget(tbl)

        return box

    def _make_table(self, data, my_view=True):
        w   = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        cols = ["날짜", "홈팀", "스코어", "원정팀", "승패"]
        tbl  = QTableWidget(len(data), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setStyleSheet("""
            QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}
            QHeaderView::section{background:#252525;color:#888;border:none;padding:4px;}
        """)

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

            from constants import day_to_full_date_str, day_to_week, DAYS_PER_WEEK
            _day = r["day"] if r["day"] else (r["week"] - 1) * DAYS_PER_WEEK + 1
            if r["year"]:
                _base_year = r["year"]
            else:
                from game_engine import get_state as _gs
                _st = _gs()
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

        lay.addWidget(tbl)

        tbl.resizeColumnsToContents()
        total_w = sum(tbl.columnWidth(j) for j in range(tbl.columnCount())) + 60
        self.setMinimumWidth(max(700, total_w))
        if self.width() < total_w:
            self.resize(total_w, self.height())

        return w