"""
ui/schedule_window.py  ─  모달리스, 실시간 갱신
"""
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
        # 5초마다 자동 갱신
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
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

    def refresh(self):
        p = get_player(); st = get_state()
        if p and p.get("current_team_id"):
            conn = get_conn()
            row = conn.execute(
                "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (p["current_team_id"],)).fetchone()
            conn.close()
            if row: self.league_id = row["id"]
            self.my_team_id = p["current_team_id"]
        if st: self.season = st["current_season"]
        self._fill_tabs()

    def _fill_tabs(self):
        cur = self._tab.currentIndex()

        # 자동 갱신(5초)으로 탭을 재구성할 때 스크롤 위치가 0으로
        # 초기화되는 문제 방지: 탭별 스크롤 위치 저장 → 재구성 후 복원
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

        # 국제대회 탭 (해당 연도에 월드컵/대륙컵이 열렸으면 표시)
        intl_w = self._make_intl_tab()
        if intl_w:
            self._tab.addTab(intl_w, "🌍 국제대회")

        if 0 <= cur < self._tab.count():
            self._tab.setCurrentIndex(cur)

        # 레이아웃 계산이 끝난 뒤 스크롤 복원 (즉시 호출하면 0으로 클램프됨)
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

    def _make_intl_tab(self):
        import intl_engine
        from game_engine import get_state, get_player
        st = get_state()
        if not st:
            return None
        t = intl_engine.get_tournament(st["current_year"])
        if not t:
            return None

        from PyQt6.QtWidgets import QScrollArea, QFrame
        p   = get_player()
        nat = p.get("nationality", "") if p else ""

        outer = QScrollArea(); outer.setWidgetResizable(True)
        outer.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
        body  = QWidget(); lay = QVBoxLayout(body)
        lay.setContentsMargins(8, 8, 8, 8); lay.setSpacing(10)

        # 헤더
        status_txt = {"group": "조별리그 진행 중", "ko": "토너먼트 진행 중"}.get(t["status"], "")
        if t["status"] == "done":
            status_txt = f"종료  |  🏆 우승: {t['winner']}"
        hdr = QLabel(f"🌍 {t['year']}년 {t['name']}  ─  {status_txt}")
        hdr.setStyleSheet("color:#66ccff;font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)
        if t["my_selected"] == 1:
            sub = QLabel(f"📣 {nat} 국가대표 소집")
        elif t["my_selected"] == 0:
            sub = QLabel(f"📋 {nat} 본선 진출 (국가대표 미선발)")
        else:
            sub = QLabel(f"📋 {nat} 예선 탈락")
        sub.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(sub)

        conn = get_conn()
        groups = [r["grp"] for r in conn.execute(
            "SELECT DISTINCT grp FROM intl_entries WHERE tournament_id=? ORDER BY grp",
            (t["id"],)).fetchall()]
        ko_rows = [dict(r) for r in conn.execute(
            """SELECT * FROM intl_matches WHERE tournament_id=? AND stage!='group'
               ORDER BY week, slot""", (t["id"],)).fetchall()]
        flags = {r["country"]: r["flag"] for r in conn.execute(
            "SELECT country, flag FROM intl_entries WHERE tournament_id=?",
            (t["id"],)).fetchall()}
        conn.close()

        # ── 조별리그 순위표 ──
        lbl_g = QLabel("◼ 조별리그")
        lbl_g.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
        lay.addWidget(lbl_g)
        for g in groups:
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
                gd = r["gf"] - r["ga"]
                vals = [f"{r['flag']}{r['country']}", str(r["p"]), str(r["w"]),
                        str(r["d"]), str(r["l"]), f"{'+' if gd>0 else ''}{gd}", str(r["pts"])]
                # 상위 2팀(진출권) 강조, 내 국가는 청록
                if r["country"] == nat:       color = QColor("#66ccff")
                elif i < 2:                    color = QColor("#00cc44")
                else:                          color = QColor("#888888")
                for j, v in enumerate(vals):
                    item = QTableWidgetItem(v)
                    if j > 0: item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(color)
                    gt.setItem(i, j, item)
            gt.setFixedHeight(gt.verticalHeader().defaultSectionSize() * len(rows) + 28)
            lay.addWidget(gt)

        # ── 토너먼트 대진 ──
        if ko_rows:
            lbl_k = QLabel("◼ 토너먼트")
            lbl_k.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;margin-top:6px;")
            lay.addWidget(lbl_k)
            stage_seen = None
            for m in ko_rows:
                if m["stage"] != stage_seen:
                    stage_seen = m["stage"]
                    sl = QLabel(f"  {intl_engine.STAGE_KO.get(m['stage'], m['stage'])}  ({m['week']}주차)")
                    sl.setStyleSheet("color:#aaaaaa;font-size:11px;font-weight:bold;")
                    lay.addWidget(sl)
                hf, af = flags.get(m["home"], ""), flags.get(m["away"], "")
                if m["home_score"] >= 0:
                    pso = f"  (PSO {m['pso_score']})" if m["pso_winner"] else ""
                    winner = m["pso_winner"] or (m["home"] if m["home_score"] > m["away_score"] else m["away"])
                    txt = f"    {hf}{m['home']}  {m['home_score']} - {m['away_score']}  {af}{m['away']}{pso}   →  {flags.get(winner,'')}{winner} 진출"
                else:
                    txt = f"    {hf}{m['home']}  vs  {af}{m['away']}   (예정)"
                ml = QLabel(txt)
                if nat and nat in (m["home"], m["away"]):
                    ml.setStyleSheet("color:#66ccff;font-size:12px;")
                elif m["home_score"] >= 0:
                    ml.setStyleSheet("color:#cccccc;font-size:12px;")
                else:
                    ml.setStyleSheet("color:#777777;font-size:12px;")
                lay.addWidget(ml)

        lay.addStretch()
        outer.setWidget(body)
        return outer

    def _make_table(self, data, my_view=True):
        w   = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        # 결과 컬럼: 전체=홈/무/원, 내경기=승/무/패  +  승패(내 경기용) 분리
        cols = ["주차", "홈팀", "스코어", "원정팀", "승패"]
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

            # 승패 컬럼 (내 팀 기준)
            if not played or not is_my:
                col_wdl = ""
                wdl_color = "#555555"
            else:
                if r["home_team_id"] == self.my_team_id:
                    col_wdl = "승" if hs>as_ else ("무" if hs==as_ else "패")
                else:
                    col_wdl = "패" if hs>as_ else ("무" if hs==as_ else "승")
                wdl_color = "#00cc44" if col_wdl=="승" else ("#888888" if col_wdl=="무" else "#cc4444")

            # 행 전체 색상
            if not played:
                row_color = QColor("#555555")
            elif is_my:
                if col_wdl == "승":   row_color = QColor("#00cc44")
                elif col_wdl == "무": row_color = QColor("#888888")
                else:                  row_color = QColor("#cc4444")
            else:
                row_color = QColor("#aaaaaa")

            vals = [str(r["week"]), r.get("home_name",""), score,
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

        # 창 너비 자동 조정
        tbl.resizeColumnsToContents()
        total_w = sum(tbl.columnWidth(j) for j in range(tbl.columnCount())) + 60
        self.setMinimumWidth(max(700, total_w))
        if self.width() < total_w:
            self.resize(total_w, self.height())

        return w