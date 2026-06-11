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
        is_gk = p.get("position","") == "GK"
        stat2_k = "총 선방" if is_gk else "총 골"
        stat2_v = f"{p.get('total_saves', p.get('total_goals',0))}선방" if is_gk else f"{p.get('total_goals',0)}골"
        stat3_k = "총 실점" if is_gk else "총 어시"
        stat3_v = f"{p.get('total_goals_against', p.get('total_assists',0))}실점" if is_gk else f"{p.get('total_assists',0)}A"
        for k, v in [("총 출전", f"{p.get('total_matches',0)}경기"),
                     (stat2_k, stat2_v),
                     (stat3_k, stat3_v),
                     ("총 시즌", f"{p.get('total_seasons',0)}시즌"),
                     ("총 자산", fmt_money(p.get('total_assets',0))),
                     ("누적 수입", fmt_money(p.get('total_earnings',0)))]:  # 이슈10
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
        # 내가 그 팀에 실제로 있던 기간의 승강 기록만 조회
        # year >= start_year AND year < end_year (end_year=0이면 현재 팀)
        promo_conditions = []
        promo_params = []
        for e in entries:
            tn = e["team_name"]
            sy = e.get("start_year", 0)
            ey = e.get("end_year", 0)
            if ey == 0:  # 현재 팀
                promo_conditions.append("(team_name=? AND year>=?)")
                promo_params.extend([tn, sy])
            else:
                # 시즌 종료(승강 처리)는 그 해 연말 → year가 재직 기간 내여야 함
                # start_year <= year < end_year (이적한 해는 새 팀 기록)
                promo_conditions.append("(team_name=? AND year>=? AND year<?)")
                promo_params.extend([tn, sy, ey])
        if promo_conditions:
            promos = [dict(r) for r in c.execute(
                f"SELECT * FROM promotion_log WHERE {' OR '.join(promo_conditions)} ORDER BY id",
                promo_params).fetchall()]
            # 중복 제거
            seen = set()
            unique_promos = []
            for p2 in promos:
                key = (p2["year"], p2["team_name"], p2["from_tier"], p2["to_tier"])
                if key not in seen:
                    seen.add(key)
                    unique_promos.append(p2)
            promos = unique_promos
        else:
            promos = []
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

        cols = ["기간","포지션","국가","리그","팀명","연봉","출전","골/선방","어시/실점","선방률","평균평점","팀순위","승무패","계약","이적"]

        # 이슈3: 1~4주차에 이적해서 경기 0인 단기 항목 제거
        def _is_empty_short(e):
            # 경기가 0이고 종료된 항목은 숨김
            if e.get("matches", 0) == 0 and e.get("end_year", 0) != 0:
                return True
            # 같은 연도 내 1주 미만 머문 빈 항목
            sw = e.get("start_week", 1)
            ey = e.get("end_year", 0)
            ew = e.get("end_week", 0)
            sy = e.get("start_year", 0)
            return (ey != 0 and sy == ey and abs(ew - sw) <= 1 and e.get("matches", 0) == 0)

        visible = [e for e in entries if not _is_empty_short(e)]
        tbl = self._make_table(len(visible), cols)
        prev_team = None
        for i, e in enumerate(visible):
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
                    period = f"{sy}  {sw}~{ew_disp}주"
                else:
                    period = f"{sy}/{sw}주~{ey}/{ew_disp}주"

            pos   = e.get("position","")
            is_gk = pos == "GK"
            # CB/CDM만 클린시트+평점 표시 (LB/RB는 어시 포함이라 일반 표시)
            DEF_POS = {"CB","CDM"}
            sv  = e.get("saves", 0)
            ga  = e.get("goals_against", 0)
            total_shots = sv + ga
            save_rate = f"{round(sv/total_shots*100,1)}%" if total_shots > 0 else "—"

            if is_gk:
                col_stat1 = f"{sv}선방"
                col_stat2 = f"{ga}실점"
                col_rate  = save_rate
            elif pos in DEF_POS:
                cs = e.get("clean_sheets", 0)
                ar = e.get("avg_rating", 0.0)
                col_stat1 = f"{cs}CS"
                col_stat2 = "—"
                col_rate  = f"{ar:.1f}평점" if ar else "—"
            else:
                col_stat1 = f"{e.get('goals',0)}골"
                col_stat2 = f"{e.get('assists',0)}A"
                col_rate  = "—"

            tn = e.get("team_name","")
            ln = e.get("league_name","")
            country_str = league_country.get(ln, "")
            league_str  = f"{ln} ({e.get('tier','')}부)"

            c_yrs  = e.get("contract_years", 0)
            t_type = e.get("transfer_type", "입단")
            cur_team = e.get("team_name", "")
            if cur_team == prev_team:
                c_str = "—"
            else:
                c_str = f"{c_yrs}년" if c_yrs else "—"
                prev_team = cur_team
            vals = [period, pos, country_str, league_str, tn,
                    fmt_money(e.get("salary",0)),
                    str(e.get("matches",0)), col_stat1, col_stat2, col_rate,
                    str(avg), f"{e.get('team_rank',0)}위", wdl, c_str, t_type]
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
            tier_t = t.get("tier", 0)
            tier_str = f" ({tier_t}부)" if tier_t else ""
            comp_str = t.get("competition","")
            result_str = t.get("league_name","") + tier_str
            for j, v in enumerate([period, t.get("team_name",""), comp_str, result_str]):
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
            ft = t.get("from_tier", 0)
            tt = t.get("to_tier", 0)
            if tt < ft:
                arrow = "🔼 승격"; color = "#00cc44"
            else:
                arrow = "🔽 강등"; color = "#ff6666"
            content = f"{ft}부 → {tt}부  {arrow}"
            league_str = t.get("league_name","")
            if ft: league_str = f"{league_str} ({ft}부)"
            vals = [str(t.get("year","")), t.get("team_name",""),
                    league_str, content]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == 3 else None)
        lay.addWidget(tbl)
        return w