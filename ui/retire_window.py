"""
ui/retire_window.py  ─  은퇴 화면
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea, QWidget
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from game_engine import get_player, fmt_money, add_log
from database import get_conn

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QScrollArea { border:none; background:#1e1e1e; }
#heroName { color:#00ff66; font-size:26px; font-weight:bold; }
#secTitle { color:#00cc44; font-size:13px; font-weight:bold;
            border-bottom:1px solid #2a2a2a; padding-bottom:3px; }
#statBox  { background:#252525; border-radius:6px; }
#story    { background:#252525; color:#dddddd; font-size:12px;
            border:1px solid #333; border-radius:6px; padding:10px; }
QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a;
               border:none; font-size:12px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:4px; }
#genBtn  { background:#2a2a6a; color:white; border:none; border-radius:6px;
           padding:10px 20px; font-size:13px; font-weight:bold; }
#genBtn:hover  { background:#3a3a8a; }
#genBtn:disabled { background:#333; color:#555; }
#backBtn { background:#2a6a2a; color:white; border:none; border-radius:6px;
           padding:10px 20px; font-size:13px; }
#backBtn:hover { background:#3a8a3a; }
"""


class RetireWindow(QDialog):
    def __init__(self, lang="ko", parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("은퇴")
        self.setMinimumSize(680, 750)
        self.setStyleSheet(STYLE)
        self.lang   = lang
        self.parent_win = parent
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0,0,0,0)

        p = get_player()
        if not p:
            root.addWidget(QLabel("선수 데이터 없음")); return

        add_log(f"🎖 {p['name']} 선수 은퇴. {p['age']}세.", "event")

        # ── 스크롤 영역 ───────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget(); lay = QVBoxLayout(inner)
        lay.setSpacing(14); lay.setContentsMargins(16,16,16,16)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # 이름 헤더
        nm = QLabel(f"⭐  {p['name']}  ⭐"); nm.setObjectName("heroName")
        nm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(nm)

        sub = QLabel(f"{p.get('flag','')} {p['nationality']}  |  {p['age']}세 은퇴  |  {p['position']}")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color:#888;font-size:13px;")
        lay.addWidget(sub)

        # 통계 박스
        box = QFrame(); box.setObjectName("statBox")
        bl  = QHBoxLayout(box); bl.setContentsMargins(12,10,12,10); bl.setSpacing(6)
        stats = [
            ("출전", f"{p.get('total_matches',0)}경기"),
            ("골",   f"{p.get('total_goals',0)}"),
            ("어시", f"{p.get('total_assists',0)}"),
            ("시즌", f"{p.get('total_seasons',0)}"),
            ("총자산", fmt_money(p.get('total_assets',0))),
            ("최종OVR", str(p.get('ovr',0))),
        ]
        for k, v in stats:
            sw = QFrame(); sl = QVBoxLayout(sw); sl.setContentsMargins(4,4,4,4)
            kl = QLabel(k); kl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            kl.setStyleSheet("color:#888;font-size:11px;")
            vl = QLabel(v); vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
            sl.addWidget(kl); sl.addWidget(vl)
            bl.addWidget(sw)
        lay.addWidget(box)

        # DB 데이터
        conn = get_conn(); c = conn.cursor()
        entries  = [dict(r) for r in c.execute("SELECT * FROM career_entries ORDER BY id").fetchall()]
        trophies = [dict(r) for r in c.execute("SELECT * FROM trophy_log ORDER BY id").fetchall()]
        promos   = [dict(r) for r in c.execute("SELECT * FROM trophy_log WHERE competition LIKE '%우승%' ORDER BY id").fetchall()]
        conn.close()

        # ── 팀 이력 ─────────────────────────────────
        t1 = QLabel("📋 팀 이력"); t1.setObjectName("secTitle")
        lay.addWidget(t1)
        lay.addWidget(self._team_table(entries))

        # ── 수상 경력 ────────────────────────────────
        t2 = QLabel(f"🏆 수상 경력  ({len(trophies)})")
        t2.setObjectName("secTitle")
        lay.addWidget(t2)
        lay.addWidget(self._trophy_table(trophies))

        # ── 승강 경험 ────────────────────────────────
        t3 = QLabel(f"🔼 승강 경험  ({len(promos)})")
        t3.setObjectName("secTitle")
        lay.addWidget(t3)
        lay.addWidget(self._promo_table(promos))

        # ── AI 커리어 요약 ───────────────────────────
        t4 = QLabel("✨ AI 커리어 스토리")
        t4.setObjectName("secTitle")
        lay.addWidget(t4)

        self.story_box = QTextEdit()
        self.story_box.setObjectName("story")
        self.story_box.setReadOnly(True)
        self.story_box.setMinimumHeight(160)
        self.story_box.setPlaceholderText("아래 버튼을 눌러 AI가 커리어 스토리를 만들어 드립니다...")
        lay.addWidget(self.story_box)

        lay.addStretch()

        # ── 하단 버튼 ────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16,8,16,12)

        self.gen_btn = QPushButton("✨ AI 스토리 생성")
        self.gen_btn.setObjectName("genBtn")
        self.gen_btn.clicked.connect(self._gen_story)

        back_btn = QPushButton("🏠 시작 화면으로")
        back_btn.setObjectName("backBtn")
        back_btn.clicked.connect(self._go_start)

        btn_row.addWidget(self.gen_btn)
        btn_row.addWidget(back_btn)
        root.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        # 모든 테이블 컬럼 너비 합산 → 창 가로 자동 확대
        from PyQt6.QtWidgets import QTableWidget
        max_w = 700
        for tbl in self.findChildren(QTableWidget):
            w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount())) + 40
            max_w = max(max_w, w)
        max_w = min(max_w, 1400)
        self.resize(max_w, self.height())

    # ── 테이블 헬퍼 ──────────────────────────────────

    def _team_table(self, entries):
        if not entries:
            lbl = QLabel("기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl

        cols = ["기간","포지션","팀명","리그","연봉","출전","골/선방","어시/실점","선방률","평균평점","팀순위","승무패"]
        tbl  = self._make_table(len(entries), cols)
        for i, e in enumerate(entries):
            rc  = e.get("season_rating_cnt", 0)
            rs  = e.get("season_rating_sum", 0) or e.get("avg_rating", 0)
            avg = round(rs/rc, 1) if rc > 0 else (round(float(rs), 1) if rs else "—")
            wdl = f"{e.get('wins',0)}승{e.get('draws',0)}무{e.get('losses',0)}패"

            sy = e.get("start_year",""); sw = e.get("start_week", 1)
            ey = e.get("end_year","");   ew = e.get("end_week", 52)
            period = f"{sy}/{sw}주~{ey}/{ew}주" if sy != ey else f"{sy}  {sw}~{ew}주"

            pos = e.get("position","")
            is_gk = pos == "GK"
            sv  = e.get("saves", 0)
            ga  = e.get("goals_against", 0)
            total_shots = sv + ga
            save_rate = f"{round(sv/total_shots*100,1)}%" if total_shots > 0 else "—"

            col_stat1 = f"{sv}선방" if is_gk else f"{e.get('goals',0)}골"
            col_stat2 = f"{ga}실점" if is_gk else f"{e.get('assists',0)}A"

            vals = [period, pos,
                    e.get("team_name",""),
                    f"{e.get('league_name','')} ({e.get('tier','')}부)",
                    fmt_money(e.get("salary",0)),
                    str(e.get("matches",0)), col_stat1, col_stat2,
                    save_rate if is_gk else "—",
                    str(avg), f"{e.get('team_rank',0)}위", wdl]
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(entries), 7) * 28)
        return tbl

    def _trophy_table(self, trophies):
        if not trophies:
            lbl = QLabel("수상 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        cols = ["기간","팀/국가","대회","결과"]
        tbl  = self._make_table(len(trophies), cols)
        for i, t in enumerate(trophies):
            yr = str(t.get("year",""))
            for j, v in enumerate([yr, t.get("team_name",""),
                                    t.get("competition",""), t.get("league_name","")]):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(trophies), 5) * 28)
        return tbl

    def _promo_table(self, promos):
        if not promos:
            lbl = QLabel("승강 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        cols = ["연도","팀명","리그","내용"]
        tbl  = self._make_table(len(promos), cols)
        for i, t in enumerate(promos):
            for j, v in enumerate([str(t.get("year","")), t.get("team_name",""),
                                    t.get("league_name",""), t.get("competition","")]):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(promos), 5) * 28)
        return tbl

    def _make_table(self, rows, cols):
        tbl = QTableWidget(rows, len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        # 모든 컬럼 내용에 맞게 자동 조정
        for i in range(len(cols)):
            tbl.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        tbl.setStyleSheet("QTableWidget{background:#1e1e1e;color:#ccc;"
                          "gridline-color:#2a2a2a;border:none;}"
                          "QHeaderView::section{background:#252525;color:#888;border:none;padding:4px;}"
                          "QTableWidget::item{padding:4px 8px;}")
        return tbl

    def _set_item(self, tbl, row, col, val):
        item = QTableWidgetItem(str(val))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        tbl.setItem(row, col, item)

    # ── AI 스토리 ─────────────────────────────────────

    def _gen_story(self):
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText("⏳ 생성 중...")

        p = get_player()
        conn = get_conn()
        entries  = [dict(r) for r in conn.execute("SELECT * FROM career_entries ORDER BY id").fetchall()]
        trophies = [dict(r) for r in conn.execute("SELECT * FROM trophy_log ORDER BY id").fetchall()]
        conn.close()

        lines = []
        lines.append(f"【 {p['name']} 선수 커리어 요약 】")
        lines.append(f"국적: {p.get('flag','')} {p['nationality']}  |  포지션: {p['position']} ({p.get('sub_role','')})")
        lines.append(f"성격: {p.get('personality','')}  |  은퇴 나이: {p['age']}세  |  최종 OVR: {p.get('ovr',0)}")
        lines.append("")

        # 팀 이력
        lines.append("▶ 팀 이력")
        if entries:
            for e in entries:
                sy = e.get("start_year",""); sw = e.get("start_week",1)
                ey = e.get("end_year","");   ew = e.get("end_week",52)
                if sy == ey:
                    period = f"{sy}년 {sw}~{ew}주"
                else:
                    period = f"{sy}년 {sw}주 ~ {ey}년 {ew}주"
                m = e.get("matches",0); g = e.get("goals",0); a = e.get("assists",0)
                rc = e.get("season_rating_cnt",0); rs = e.get("season_rating_sum",0)
                avg = round(rs/rc,1) if rc else "—"
                tier = e.get("tier","")
                lines.append(f"  • {period}  {e.get('team_name','')} ({e.get('league_name','')} / {tier}부)")
                lines.append(f"    출전 {m}경기  {g}골  {a}어시  평점 {avg}  팀순위 {e.get('team_rank',0)}위")
        else:
            lines.append("  기록 없음")
        lines.append("")

        # 수상
        lines.append(f"▶ 수상 경력  ({len(trophies)}건)")
        if trophies:
            for t in trophies:
                comp   = t.get('competition', '')
                nation = t.get('team_name', '')
                result = t.get('league_name', '')  # 결과 (우승/준우승/국가대표 탈락 등)
                tier   = t.get('tier', 0)
                if tier == 0:
                    # 국제대회 (월드컵, 대륙컵 등)
                    lines.append(f"  🌍 {t.get('year','')}년  {comp}  →  {result}  ({nation})")
                else:
                    # 리그 승강전
                    lines.append(f"  🏆 {t.get('year','')}년  {comp}  ({nation})")
        else:
            lines.append("  없음")
        lines.append("")

        # 통계 요약
        total_m = p.get("total_matches", 0)
        total_g = p.get("total_goals", 0)
        total_a = p.get("total_assists", 0)
        total_s = p.get("total_seasons", 0)
        lines.append("▶ 통산 기록")
        lines.append(f"  {total_s}시즌  {total_m}경기  {total_g}골  {total_a}어시스트")
        lines.append(f"  총 자산: {fmt_money(p.get('total_assets',0))}")

        self.story_box.setPlainText("\n".join(lines))
        self.gen_btn.setText("✨ 다시 생성")
        self.gen_btn.setEnabled(True)

    # ── 시작 화면으로 ─────────────────────────────────

    def _go_start(self):
        self.accept()
        from PyQt6.QtCore import QTimer
        parent = self.parent_win

        def _open():
            if parent:
                # closeEvent의 closeAllWindows를 우회하기 위해 직접 hide
                parent.hide()
            from ui.start_screen import StartScreen
            self._start = StartScreen()
            self._start.show()
            if parent:
                parent.destroy()

        QTimer.singleShot(100, _open)