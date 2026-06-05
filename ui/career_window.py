"""
ui/career_window.py
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QTabWidget, QWidget, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from game_engine import get_player, fmt_money
from database import get_conn

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QTabWidget::pane { border:1px solid #333; background:#1e1e1e; }
QTabBar::tab { background:#252525; color:#888; padding:6px 16px; }
QTabBar::tab:selected { background:#1e1e1e; color:#00cc44; border-bottom:2px solid #00cc44; }
QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a; border:none; font-size:12px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:4px; }
QTableWidget::item { padding: 4px 8px; }
"""

class CareerWindow(QDialog):
    def __init__(self, lang="ko", parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("커리어 기록")
        self.setMinimumHeight(500)
        self.setStyleSheet(STYLE)
        self.lang = lang
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        p = get_player()
        if not p:
            root.addWidget(QLabel("선수 데이터 없음")); return

        hdr = QLabel(f"📋 {p['name']} 커리어 기록")
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        root.addWidget(hdr)

        summary = QHBoxLayout()
        for k, v in [("총 출전", f"{p.get('total_matches',0)}경기"),
                     ("총 골",   f"{p.get('total_goals',0)}골"),
                     ("총 어시", f"{p.get('total_assists',0)}A"),
                     ("총 시즌", f"{p.get('total_seasons',0)}시즌"),
                     ("총 자산", fmt_money(p.get('total_assets',0)))]:
            box = QFrame(); bl = QVBoxLayout(box); bl.setContentsMargins(12,8,12,8)
            kl = QLabel(k); kl.setStyleSheet("color:#888;font-size:11px;")
            vl = QLabel(v); vl.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
            bl.addWidget(kl); bl.addWidget(vl)
            box.setStyleSheet("background:#252525;border-radius:6px;")
            summary.addWidget(box)
        root.addLayout(summary)

        conn = get_conn(); c = conn.cursor()
        entries  = [dict(r) for r in c.execute("SELECT * FROM career_entries ORDER BY id").fetchall()]
        trophies = [dict(r) for r in c.execute("SELECT * FROM trophy_log ORDER BY id").fetchall()]
        promos   = [dict(r) for r in c.execute(
            "SELECT * FROM trophy_log WHERE competition LIKE '%우승%' ORDER BY id").fetchall()]
        conn.close()

        tabs = QTabWidget()
        tabs.addTab(self._team_tab(entries),  "팀 이력")
        tabs.addTab(self._trophy_tab(trophies), f"수상 ({len(trophies)})")
        tabs.addTab(self._promo_tab(promos),  f"승강 ({len(promos)})")
        root.addWidget(tabs)
        tabs.currentChanged.connect(lambda: self._fit_width())

        btn = QPushButton("닫기")
        btn.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                          "border-radius:4px;padding:6px;")
        btn.clicked.connect(self.close)
        root.addWidget(btn)
        self._tabs = tabs
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_width()

    def _fit_width(self):
        w = self._tabs.currentWidget()
        if not w: return
        tbls = w.findChildren(QTableWidget)
        if not tbls: return
        tbl = tbls[0]
        total_w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount())) + 60
        self.resize(max(700, min(1600, total_w)), self.height())

    def _make_table(self, rows, cols):
        tbl = QTableWidget(rows, len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        for i in range(len(cols)):
            tbl.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        return tbl

    def _set(self, tbl, r, c, v, color=None):
        item = QTableWidgetItem(str(v))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color: item.setForeground(QColor(color))
        tbl.setItem(r, c, item)

    def _team_tab(self, entries):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not entries:
            lay.addWidget(QLabel("기록 없음")); return w

        # league_name → 국가 flag 매핑 (팀명보다 리그명이 더 안정적)
        conn = get_conn()
        c = conn.cursor()
        league_country = {}
        for e in entries:
            ln = e.get("league_name","")
            if ln and ln not in league_country:
                row = c.execute("""SELECT cn.flag, cn.name as cname
                                   FROM leagues l JOIN countries cn ON l.country_id=cn.id
                                   WHERE l.name=? LIMIT 1""", (ln,)).fetchone()
                league_country[ln] = f"{row['flag']} {row['cname']}" if row else ""
        conn.close()

        cols = ["기간","포지션","국가","리그","팀명","연봉","출전","골/선방","어시/실점","선방률","평균평점","팀순위","승무패"]
        tbl = self._make_table(len(entries), cols)
        for i, e in enumerate(entries):
            rc  = e.get("season_rating_cnt", 0)
            rs  = e.get("season_rating_sum", 0) or e.get("avg_rating", 0)
            avg = round(rs/rc, 1) if rc > 0 else (round(float(rs), 1) if rs else "—")
            wdl = f"{e.get('wins',0)}승{e.get('draws',0)}무{e.get('losses',0)}패"

            sy = e.get("start_year", ""); sw = e.get("start_week", 1)
            ey = e.get("end_year", 0);    ew = e.get("end_week", 0)

            if ey == 0:
                period = f"{sy}  {sw}주~현재"
            else:
                ew_disp = 52 if ew >= 37 else ew
                if sy == ey:
                    if sw == ew_disp:
                        period = f"{sy}  {sw}주 (단기)"
                    else:
                        period = f"{sy}  {sw}~{ew_disp}주"
                else:
                    period = f"{sy}/{sw}주~{ey}/{ew_disp}주"

            pos = e.get("position","")
            is_gk = pos == "GK"
            sv  = e.get("saves", 0)
            ga  = e.get("goals_against", 0)
            total_shots = sv + ga
            save_rate = f"{round(sv/total_shots*100,1)}%" if total_shots > 0 else "—"

            col_stat1 = f"{sv}선방" if is_gk else f"{e.get('goals',0)}골"
            col_stat2 = f"{ga}실점" if is_gk else f"{e.get('assists',0)}A"
            col_rate  = save_rate if is_gk else "—"

            tn = e.get("team_name","")
            ln = e.get("league_name","")
            country_str = league_country.get(ln, "")
            league_str  = f"{e.get('league_name','')} ({e.get('tier','')}부)"

            vals = [period, pos, country_str, league_str, tn,
                    fmt_money(e.get("salary",0)),
                    str(e.get("matches",0)), col_stat1, col_stat2, col_rate,
                    str(avg), f"{e.get('team_rank',0)}위", wdl]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v)
        lay.addWidget(tbl)
        return w

    def _trophy_tab(self, trophies):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not trophies:
            lay.addWidget(QLabel("수상 기록 없음")); return w
        cols = ["기간","팀/국가","대회","결과"]
        tbl  = self._make_table(len(trophies), cols)
        for i, t in enumerate(trophies):
            yr  = str(t.get("year",""))
            sw  = t.get("start_week", "")
            ew  = t.get("end_week", "")
            period = f"{yr}/{sw}주~{ew}주" if sw and ew else yr
            for j, v in enumerate([period, t.get("team_name",""),
                                    t.get("competition",""), t.get("league_name","")]):
                self._set(tbl, i, j, v)
        lay.addWidget(tbl)
        return w

    def _promo_tab(self, promos):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not promos:
            lay.addWidget(QLabel("승강 기록 없음")); return w
        cols = ["연도","팀명","리그","내용"]
        tbl  = self._make_table(len(promos), cols)
        for i, t in enumerate(promos):
            for j, v in enumerate([str(t.get("year","")), t.get("team_name",""),
                                    t.get("league_name",""), t.get("competition","")]):
                self._set(tbl, i, j, v)
        lay.addWidget(tbl)
        return w