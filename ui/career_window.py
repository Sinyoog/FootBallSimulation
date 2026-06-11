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

from game_engine import get_player, fmt_money, get_my_promotions
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
        # 내가 그 팀에 실제로 있던 기간의 승강 기록 (공용 헬퍼)
        promos = get_my_promotions()
        conn.close()

        tabs = QTabWidget()
        tabs.addTab(self._team_tab(entries),  "팀 이력")
        tabs.addTab(self._trophy_tab(trophies), f"수상 ({len(trophies)})")
        tabs.addTab(self._promo_tab(promos),  f"승강 ({len(promos)})")

        import intl_engine
        intl_ms = intl_engine.get_my_intl_matches()
        tabs.addTab(self._intl_tab(intl_ms, p), f"국제전 ({len(intl_ms)})")
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

        cols = ["기간","포지션","국가","리그","팀명","연봉","출전","골","어시","선방","실점","선방률","CS","평균평점","팀순위","승무패","계약","이적"]

        # 이슈3: 1~4주차 이적 노이즈만 숨김 (4주 이하 머문 0경기 항목)
        # 여름 이적시장(37주~) 입단처럼 경기 없이 보낸 정상 재직 기간은 표시
        def _is_empty_short(e):
            if e.get("end_year", 0) == 0:  return False  # 현재 팀
            if e.get("matches", 0) != 0:   return False
            sy = e.get("start_year", 0); ey = e.get("end_year", 0)
            sw = e.get("start_week", 1); ew = e.get("end_week", 0)
            return sy == ey and (ew - sw) <= 4

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
            # CS(클린시트)는 GK + 중앙 수비 라인(CB/CDM)만 표시
            CS_POS = {"GK","CB","CDM"}
            sv  = e.get("saves", 0)
            ga  = e.get("goals_against", 0)
            total_shots = sv + ga
            save_rate = f"{round(sv/total_shots*100,1)}%" if total_shots > 0 else "—"

            if is_gk:
                col_goal, col_asst = "—", "—"
                col_save, col_conc = str(sv), str(ga)
                col_rate = save_rate
            else:
                col_goal = str(e.get("goals", 0))
                col_asst = str(e.get("assists", 0))
                col_save, col_conc, col_rate = "—", "—", "—"
            col_cs = str(e.get("clean_sheets", 0)) if pos in CS_POS else "—"

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
                    str(e.get("matches",0)),
                    col_goal, col_asst, col_save, col_conc, col_rate, col_cs,
                    str(avg), f"{e.get('team_rank',0)}위", wdl, c_str, t_type]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v)
        lay.addWidget(tbl)
        return w

    def _country_of_league(self, league_name):
        """리그명 → 국가명. 조회 결과 캐싱."""
        if not hasattr(self, "_lc_cache"):
            self._lc_cache = {}
        if league_name in self._lc_cache:
            return self._lc_cache[league_name]
        conn = get_conn()
        row = conn.execute("""SELECT cn.name as cname
                              FROM leagues l JOIN countries cn ON l.country_id=cn.id
                              WHERE l.name=? LIMIT 1""", (league_name,)).fetchone()
        conn.close()
        name = row["cname"] if row else ""
        self._lc_cache[league_name] = name
        return name

    def _trophy_tab(self, trophies):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not trophies:
            lay.addWidget(QLabel("수상 기록 없음")); return w
        cols = ["기간","팀/국가","대회","결과"]
        tbl  = self._make_table(len(trophies), cols)
        for i, t in enumerate(trophies):
            yr     = str(t.get("year",""))
            tier_t = t.get("tier", 0)
            tname  = t.get("team_name","")
            lname  = t.get("league_name","")

            if tier_t and tier_t > 0:
                # 리그 우승: 팀 (국가) / 리그 (N부) / 우승
                country  = self._country_of_league(lname)
                team_str = f"{tname} ({country})" if country else tname
                comp_str = f"{lname} ({tier_t}부)"
                result   = "우승"
                color    = "#00cc44"
            else:
                # 국제대회: 국가 / 대회 / 결과
                team_str = tname
                comp_str = t.get('competition','')
                result   = lname  # league_name 자리에 결과 저장됨
                if "우승" in result:   color = "#00cc44"
                elif "탈락" in result: color = "#ff6666"
                else:                  color = None

            for j, v in enumerate([yr, team_str, comp_str, result]):
                self._set(tbl, i, j, v, color if j == 3 else None)
        lay.addWidget(tbl)
        return w

    def _promo_tab(self, promos):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not promos:
            lay.addWidget(QLabel("승강 기록 없음")); return w
        cols = ["기간","팀/국가","대회","결과"]
        tbl  = self._make_table(len(promos), cols)
        for i, t in enumerate(promos):
            ft = t.get("from_tier", 0)
            tt = t.get("to_tier", 0)
            color  = "#00cc44" if tt < ft else "#ff6666"
            result = f"{ft}부 → {tt}부"
            lname  = t.get("league_name","")
            tname  = t.get("team_name","")
            country  = self._country_of_league(lname)
            team_str = f"{tname} ({country})" if country else tname
            comp_str = f"{lname} ({ft}부)" if ft else lname
            vals = [str(t.get("year","")), team_str, comp_str, result]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == 3 else None)
        lay.addWidget(tbl)
        return w

    def _intl_tab(self, matches, p):
        """국제전(A매치) 경기별 기록: 기간/포지션/국가/대회/상대/스탯/평점/스코어/결과."""
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not matches:
            lay.addWidget(QLabel("국제전 기록 없음")); return w

        # 통산 A매치 요약
        caps = p.get("intl_caps", 0)
        if p.get("position") == "GK":
            sv = sum(m["saves"] for m in matches)
            ga = sum(m["conceded"] for m in matches)
            summary = f"통산 A매치 {caps}경기  |  선방 {sv}  실점 {ga}"
        else:
            summary = (f"통산 A매치 {caps}경기  |  {p.get('intl_goals',0)}골 "
                       f"{p.get('intl_assists',0)}어시")
        ratings = [m["rating"] for m in matches if m["rating"]]
        if ratings:
            summary += f"  |  평균 평점 {sum(ratings)/len(ratings):.1f}"
        sl = QLabel(f"🌍 {summary}")
        sl.setStyleSheet("color:#66ccff;font-size:12px;font-weight:bold;padding:4px;")
        lay.addWidget(sl)

        cols = ["기간","포지션","국가","대회","상대","골/선방","어시/실점","평점","스코어","결과"]
        tbl = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            is_gk = m["position"] == "GK"
            stat1 = f"{m['saves']}선방"   if is_gk else f"{m['goals']}골"
            stat2 = f"{m['conceded']}실점" if is_gk else f"{m['assists']}A"
            res   = m["result"]
            color = ("#00cc44" if res.startswith("승")
                     else "#888888" if res == "무" else "#cc4444")
            vals = [f"{m['year']} {m['week']}주차", m["position"],
                    f"{m['nat_flag']}{m['nat']}",
                    f"{m['comp']} {m['stage']}",
                    f"{m['opp_flag']}{m['opp']}",
                    stat1, stat2, str(m["rating"]), m["score"], res]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == len(vals) - 1 else None)
        lay.addWidget(tbl)
        return w