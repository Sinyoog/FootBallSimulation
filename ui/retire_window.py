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

from game_engine import get_player, fmt_money, add_log, get_state, _save_career_entry

def _game_confirm(parent, title: str, message: str) -> bool:
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
    from PyQt6.QtCore import Qt
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedWidth(320)
    dlg.setStyleSheet("""
        QDialog { background:#1a1a2e; border:1px solid #333; }
        QLabel  { color:#e0e0e0; font-size:13px; padding:8px; }
        QPushButton { padding:8px 28px; border-radius:4px; font-size:13px; font-weight:bold; }
    """)
    lay = QVBoxLayout(dlg); lay.setSpacing(16); lay.setContentsMargins(20,20,20,16)
    lbl = QLabel(message); lbl.setWordWrap(True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(lbl)
    btn_row = QHBoxLayout(); btn_row.setSpacing(12)
    yes = QPushButton("✅ 확인"); no = QPushButton("❌ 취소")
    yes.setStyleSheet("background:#005522;color:white;")
    no.setStyleSheet("background:#440000;color:white;")
    btn_row.addWidget(yes); btn_row.addWidget(no)
    lay.addLayout(btn_row)
    result = [False]
    yes.clicked.connect(lambda: (result.__setitem__(0,True), dlg.accept()))
    no.clicked.connect(dlg.reject)
    dlg.exec()
    return result[0]


def _game_warning(parent, title: str, message: str):
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QLabel
    from PyQt6.QtCore import Qt
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedWidth(280)
    dlg.setStyleSheet("""
        QDialog { background:#1a1a2e; border:1px solid #555; }
        QLabel  { color:#ffcc44; font-size:13px; padding:8px; }
        QPushButton { padding:7px 32px; border-radius:4px; font-size:13px;
                      background:#333; color:white; font-weight:bold; }
    """)
    lay = QVBoxLayout(dlg); lay.setSpacing(12); lay.setContentsMargins(20,20,20,16)
    lbl = QLabel(f"⚠  {message}"); lbl.setWordWrap(True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(lbl)
    ok = QPushButton("확인")
    ok.clicked.connect(dlg.accept)
    lay.addWidget(ok, alignment=Qt.AlignmentFlag.AlignCenter)
    dlg.exec()


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
        self.setMinimumSize(680, 820)
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

        # 은퇴 시 현재 주차로 마지막 커리어 항목 종료
        st = get_state()
        if st and p.get("current_team_id"):
            _save_career_entry(p, st["current_year"], st["current_week"])
        add_log(f"🎖 {p['name']} 선수 은퇴. {p['age']}세.", "event")

        # ── 스크롤 영역 ───────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget(); lay = QVBoxLayout(inner)
        lay.setSpacing(14); lay.setContentsMargins(16,16,16,16)
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

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
        pos = p.get("position","")
        if pos == "GK":
            ts = p.get("total_saves", 0)
            tga = p.get("total_goals_against", 0)
            tot_shots = ts + tga
            sr = f"{round(ts/tot_shots*100,1)}%" if tot_shots else "0%"
            stat2 = ("선방", f"{ts}회  {sr}")
            stat3 = ("실점", f"{tga}골")
        elif pos in {"CB","CDM"}:
            stat2 = ("골", f"{p.get('total_goals',0)}")
            stat3 = ("어시", f"{p.get('total_assists',0)}")
        else:
            stat2 = ("골", f"{p.get('total_goals',0)}")
            stat3 = ("어시", f"{p.get('total_assists',0)}")
        stats = [
            ("출전", f"{p.get('total_matches',0)}경기"),
            stat2, stat3,
            ("시즌", f"{p.get('total_seasons',0)}"),
            ("총자산", fmt_money(p.get('total_assets',0))),
            ("누적수입", fmt_money(p.get('total_earnings',0))),  # 이슈10
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
        try:
            awards = [dict(r) for r in c.execute(
                "SELECT * FROM awards WHERE is_mine=1 ORDER BY year").fetchall()]
        except Exception:
            awards = []
        from game_engine import get_my_promotions
        promos   = get_my_promotions()
        conn.close()

        # ── 개인 수상 하이라이트 (있을 때만, 최상단 강조) ──
        if awards:
            from collections import Counter
            cnt = Counter(a.get("award_type","") for a in awards)
            order = ["발롱도르","MVP","득점왕","도움왕","베스트11","골든글러브","영플레이어"]
            parts = [f"{k} {cnt[k]}회" for k in order if cnt.get(k)]
            hl = QLabel("🏅 " + "   ·   ".join(parts))
            hl.setWordWrap(True)
            hl.setStyleSheet("color:#ffcc00;font-size:15px;font-weight:bold;"
                             "padding:10px;background:#2a2a1a;border-radius:6px;")
            lay.addWidget(hl)

        # ── 팀 이력 ─────────────────────────────────
        t1 = QLabel("📋 팀 이력"); t1.setObjectName("secTitle")
        lay.addWidget(t1)
        lay.addWidget(self._team_table(entries))

        # ── 우승 경력 ────────────────────────────────
        t2 = QLabel(f"🏆 우승 경력  ({len(trophies)})")
        t2.setObjectName("secTitle")
        lay.addWidget(t2)
        lay.addWidget(self._trophy_table(trophies))

        # ── 승강 경험 ────────────────────────────────
        t3 = QLabel(f"🔼 승강 경험  ({len(promos)})")
        t3.setObjectName("secTitle")
        lay.addWidget(t3)
        lay.addWidget(self._promo_table(promos))

        # ── 국제전 기록 ──────────────────────────────
        import intl_engine
        intl_ms = intl_engine.get_my_intl_matches()
        t35 = QLabel(f"🌍 국제전 기록  ({len(intl_ms)})")
        t35.setObjectName("secTitle")
        lay.addWidget(t35)
        lay.addWidget(self._intl_table(intl_ms, p))

        # ── 개인 수상 ────────────────────────────────
        try:
            awards = [dict(r) for r in c.execute(
                "SELECT * FROM awards WHERE is_mine=1 ORDER BY year").fetchall()]
        except Exception:
            awards = []
        t4 = QLabel(f"🥇 개인 수상  ({len(awards)})")
        t4.setObjectName("secTitle")
        lay.addWidget(t4)
        lay.addWidget(self._award_table(awards))

        # ── AI 커리어 요약 ───────────────────────────
        t5 = QLabel("✨ AI 커리어 스토리")
        t5.setObjectName("secTitle")
        lay.addWidget(t5)

        self.story_box = QTextEdit()
        self.story_box.setObjectName("story")
        self.story_box.setReadOnly(True)
        self.story_box.setMinimumHeight(80)
        self.story_box.setMaximumHeight(180)
        self.story_box.setPlaceholderText("아래 버튼을 눌러 AI가 커리어 스토리를 만들어 드립니다...")
        lay.addWidget(self.story_box)
        lay.addStretch()

        # ── 하단 버튼: root에 고정 (화면 크기와 무관하게 항상 보임) ──
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 6, 16, 10)

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
        from PyQt6.QtWidgets import QTableWidget
        from PyQt6.QtGui import QGuiApplication
        max_w = 700
        for tbl in self.findChildren(QTableWidget):
            w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount())) + 40
            max_w = max(max_w, w)
        # 화면 크기 초과 방지
        screen = QGuiApplication.primaryScreen().availableGeometry()
        max_w = min(max_w, int(screen.width() * 0.95))
        # 높이도 화면의 90% 이하로 제한 (버튼이 잘리지 않도록)
        target_h = max(820, self.height())
        target_h = min(target_h, int(screen.height() * 0.90))
        self.resize(max_w, target_h)

    # ── 테이블 헬퍼 ──────────────────────────────────

    def _team_table(self, entries):
        if not entries:
            lbl = QLabel("기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl

        cols = ["기간","포지션","국가","리그","팀명","연봉","출전","골","어시","선방","실점","선방률","CS","평균평점","팀순위","승무패","계약","이적"]
        # 이슈3: 단기(1~4주차 이적, 0경기) 항목 제거
        visible = [e for e in entries if not (
            e.get("start_week", 1) <= 4
            and e.get("end_year", 0) != 0
            and e.get("start_year") == e.get("end_year")
            and e.get("end_week", 0) <= 4
            and e.get("matches", 0) == 0
        )]
        tbl  = self._make_table(len(visible), cols)
        
        prev_team = None
        for i, e in enumerate(visible):
            rc  = e.get("season_rating_cnt", 0)
            rs  = e.get("season_rating_sum", 0) or e.get("avg_rating", 0)
            avg = round(rs/rc, 1) if rc > 0 else (round(float(rs), 1) if rs else "—")
            # 출전 0이면 팀 순위·승무패는 본인 성적이 아니므로 — 표시
            if e.get("matches", 0) > 0:
                wdl       = f"{e.get('wins',0)}승{e.get('draws',0)}무{e.get('losses',0)}패"
                rank_disp = f"{e.get('team_rank',0)}위"
            else:
                wdl       = "—"
                rank_disp = "—"

            sy = e.get("start_year",""); sw = e.get("start_week", 1)
            ey = e.get("end_year","");   ew = e.get("end_week", 52)
            period = f"{sy} {sw}~{ew}주" if sy == ey else f"{sy} {sw}~{ey} {ew}주"

            pos   = e.get("position","")
            is_gk = pos == "GK"
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
            
            # 계약 컬럼: 팀 변경 또는 연장 시에만 년수 표시
            cur_team = e.get("team_name", "")
            c_yrs = e.get("contract_years", 0)
            exit_t = e.get("exit_type", "")
            in_type = e.get("transfer_type", "입단")
            t_type = exit_t if exit_t else in_type
            
            if i == 0 or cur_team != visible[i-1].get("team_name"):
                # 팀이 바뀌었거나 첫 행 → 입단 (년수 표시)
                c_str = f"{c_yrs}년" if c_yrs else "—"
                prev_team = cur_team
            elif in_type == "연장" or t_type == "연장":
                # 같은 팀에서 연장 (연장 년수 표시)
                c_str = f"{c_yrs}년" if c_yrs else "—"
            else:
                # 같은 팀 계속 (대시)
                c_str = "—"
            
            # 이적 컬럼
            tt_color = "#cc4444" if t_type in ("팔림", "방출", "계약만료") else None

            vals = [period, pos,
                    e.get("league_name", "")[:2] if e.get("league_name","") else "—",  # 국가 약자
                    e.get("league_name",""),
                    e.get("team_name",""),
                    fmt_money(e.get("salary",0)),
                    str(e.get("matches",0)),
                    col_goal, col_asst, col_save, col_conc, col_rate, col_cs,
                    str(avg), rank_disp, wdl, c_str, t_type]
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(visible), 7) * 28)
        return tbl

    def _trophy_table(self, trophies):
        if not trophies:
            lbl = QLabel("수상 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
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
                elif "탈락" in result: color = "#cc6666"
                else:                  color = None

            for j, v in enumerate([yr, team_str, comp_str, result]):
                self._set_item_colored(tbl, i, j, v, color if j == 3 else None)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(trophies), 7) * 28)
        return tbl
    
    def _country_of_league(self, league_name):
        """리그명에서 국가 정보 추출 (캐시됨)"""
        if not hasattr(self, '_lc_cache'):
            self._lc_cache = {}
        if league_name in self._lc_cache:
            return self._lc_cache[league_name]
        conn = get_conn()
        row = conn.execute("""SELECT cn.flag, cn.name as cname
                             FROM leagues l JOIN countries cn ON l.country_id=cn.id
                             WHERE l.name=? LIMIT 1""", (league_name,)).fetchone()
        conn.close()
        name = f"{row['flag']} {row['cname']}" if row else ""
        self._lc_cache[league_name] = name
        return name
    
    def _set_item_colored(self, tbl, row, col, val, color=None):
        """색상이 들어갈 수 있는 _set_item"""
        item = QTableWidgetItem(str(val))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color:
            item.setForeground(QColor(color))
        tbl.setItem(row, col, item)

    def _award_table(self, awards):
        """개인 수상 테이블"""
        if not awards:
            lbl = QLabel("개인 수상 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        
        # 수상 종류별 횟수 요약
        from collections import Counter
        cnt = Counter(a.get("award_type","") for a in awards)
        order = ["발롱도르","MVP","득점왕","도움왕","베스트11","골든글러브","영플레이어","신데렐라","푸스카스상","사모라상"]
        summary_parts = []
        for k in order:
            if cnt.get(k):
                summary_parts.append(f"{k} {cnt[k]}회")
        
        frame = QFrame()
        fl = QVBoxLayout(frame); fl.setContentsMargins(0,0,0,0)
        
        if summary_parts:
            sl = QLabel("  ·  ".join(summary_parts))
            sl.setStyleSheet("color:#ffcc00;font-size:14px;font-weight:bold;padding:6px;")
            fl.addWidget(sl)
        
        cols = ["연도","수상","리그","상세"]
        tbl  = self._make_table(len(awards), cols)
        icon = {"득점왕":"⚽","도움왕":"🎯","베스트11":"⭐","MVP":"🏅",
                "발롱도르":"🏆","영플레이어":"🌟","골든글러브":"🧤",
                "신데렐라":"✨","푸스카스상":"💥","사모라상":"🛡️"}
        
        for i, a in enumerate(awards):
            atype = a.get("award_type","")
            label = f"{icon.get(atype,'🏅')} {atype}"
            # 발롱도르/MVP는 황금색, 주요 상은 녹색
            color = "#ffcc00" if atype in ("발롱도르","MVP") else (
                    "#00cc44" if atype in ("득점왕","도움왕","베스트11") else None)
            vals = [str(a.get("year","")), label, a.get("league_name",""), a.get("detail","")]
            for j, v in enumerate(vals):
                self._set_item_colored(tbl, i, j, v, color if j == 1 else None)
        
        fl.addWidget(tbl)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(awards), 7) * 28)
        return frame

    def _promo_table(self, promos):
        if not promos:
            lbl = QLabel("승강 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        cols = ["기간","팀/국가","대회","결과"]
        tbl  = self._make_table(len(promos), cols)
        for i, t in enumerate(promos):
            ft = t.get("from_tier", 0); tt = t.get("to_tier", 0)
            result = f"{ft}부 → {tt}부"
            lname  = t.get("league_name","")
            comp   = f"{lname} ({ft}부)" if ft else lname
            for j, v in enumerate([str(t.get("year","")), t.get("team_name",""),
                                    comp, result]):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(promos), 5) * 28)
        return tbl

    def _intl_table(self, matches, p):
        """국제전(A매치) 경기별 기록 테이블."""
        if not matches:
            lbl = QLabel("국제전 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        cols = ["기간","포지션","국가","대회","상대","골/선방","어시/실점","평점","스코어","결과"]
        tbl  = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            is_gk = m["position"] == "GK"
            stat1 = f"{m['saves']}선방"   if is_gk else f"{m['goals']}골"
            stat2 = f"{m['conceded']}실점" if is_gk else f"{m['assists']}A"
            vals = [f"{m['year']} {m['week']}주차", m["position"],
                    f"{m['nat_flag']}{m['nat']}",
                    f"{m['comp']} {m['stage']}",
                    f"{m['opp_flag']}{m['opp']}",
                    stat1, stat2, str(m["rating"]), m["score"], m["result"]]
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(matches), 7) * 28)
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
        try:
            awards = [dict(r) for r in conn.execute(
                "SELECT * FROM awards WHERE is_mine=1 ORDER BY year").fetchall()]
        except Exception:
            awards = []
        conn.close()

        lines = []
        lines.append(f"【 {p['name']} 선수 커리어 요약 】")
        lines.append(f"국적: {p.get('flag','')} {p['nationality']}  |  포지션: {p['position']} ({p.get('sub_role','')})")
        lines.append(f"성격: {p.get('personality','')}  |  특징: {p.get('physical_trait','무난함')}  |  은퇴 나이: {p['age']}세  |  최종 OVR: {p.get('ovr',0)}")
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
                ar = e.get("avg_rating", 0)
                avg = round(ar, 1) if ar else "—"
                tier = e.get("tier","")
                lines.append(f"  • {period}  {e.get('team_name','')} ({e.get('league_name','')} / {tier}부)")
                if m > 0:
                    lines.append(f"    출전 {m}경기  {g}골  {a}어시  평점 {avg}  팀순위 {e.get('team_rank',0)}위")
                else:
                    lines.append(f"    출전 0경기 (입단만, 출전 없음)")
                # [커리어 보강] 역할/감독/구단야망 — 있으면 한 줄 더
                ctx = []
                if e.get("contract_role"):  ctx.append(f"역할 {e['contract_role']}")
                if e.get("manager_type"):   ctx.append(f"감독 {e['manager_type']}")
                if e.get("club_ambition"):  ctx.append(f"구단목표 {e['club_ambition']}")
                tt = e.get("transfer_type")
                if tt and tt not in ("입단",): ctx.append(f"({tt})")
                if ctx:
                    lines.append("    └ " + "  ·  ".join(ctx))
        else:
            lines.append("  기록 없음")
        lines.append("")

        # 팀 우승 (리그 우승 등 ─ 국제대회는 아래 국가대표 섹션에서)
        league_trophies = [t for t in trophies if t.get('tier', 0) != 0]
        lines.append(f"▶ 팀 우승  ({len(league_trophies)}건)")
        if league_trophies:
            for t in league_trophies:
                comp   = t.get('competition', '')
                nation = t.get('team_name', '')
                lines.append(f"  🏆 {t.get('year','')}년  {comp}  ({nation})")
        else:
            lines.append("  없음")
        lines.append("")

        # 개인 영예 (득점왕/베스트11/발롱도르 등)
        lines.append(f"▶ 개인 영예  ({len(awards)}건)")
        if awards:
            from collections import Counter
            cnt = Counter(a.get("award_type","") for a in awards)
            order = ["발롱도르","MVP","득점왕","도움왕","베스트11","골든글러브","영플레이어"]
            summ = [f"{k} {cnt[k]}회" for k in order if cnt.get(k)]
            if summ:
                lines.append("  ★ " + "  ·  ".join(summ))
            icon = {"득점왕":"⚽","도움왕":"🎯","베스트11":"⭐","MVP":"🏅",
                    "발롱도르":"🏆","영플레이어":"🌟","골든글러브":"🧤"}
            for a in awards:
                at = a.get("award_type","")
                lines.append(f"  {icon.get(at,'🏅')} {a.get('year','')}년  {at}  "
                             f"({a.get('league_name','')}, {a.get('detail','')})")
        else:
            lines.append("  없음")
        lines.append("")

        # 국가대표 경력 (월드컵/대륙컵 ─ 대회별 결과 + 활약상)
        intl_trophies = [t for t in trophies if t.get('tier', 0) == 0]
        lines.append(f"▶ 국가대표 경력  ({len(intl_trophies)}건)")
        if intl_trophies:
            conn_i = get_conn()
            hist = {(r["year"], r["competition"]): dict(r) for r in conn_i.execute(
                "SELECT * FROM intl_history").fetchall()}
            conn_i.close()
            for t in intl_trophies:
                yr, comp = t.get('year', 0), t.get('competition', '')
                result   = t.get('league_name', '')
                nation   = t.get('team_name', '')
                line = f"  🌍 {yr}년  {comp}  →  {result}  ({nation})"
                ih = hist.get((yr, comp))
                if ih and ih.get("caps", 0) > 0:
                    if p.get("position") == "GK":
                        line += f"  | {ih['caps']}경기 출전, 평점 {ih.get('rating', 0)}"
                    else:
                        line += (f"  | {ih['caps']}경기 {ih.get('goals',0)}골 "
                                 f"{ih.get('assists',0)}어시, 평점 {ih.get('rating', 0)}")
                lines.append(line)
        else:
            lines.append("  없음")
        lines.append("")

        # 국제전 기록 (A매치 경기 단위 ─ 상대/활약/스코어/결과)
        import intl_engine
        intl_ms = intl_engine.get_my_intl_matches()
        lines.append(f"▶ 국제전 기록  ({len(intl_ms)}경기)")
        if intl_ms:
            for im in intl_ms:
                if im["position"] == "GK":
                    stat = f"{im['saves']}선방 {im['conceded']}실점"
                else:
                    stat = f"{im['goals']}골 {im['assists']}어시"
                lines.append(f"  • {im['year']}년 {im['week']}주차  "
                             f"{im['comp']} {im['stage']}  vs {im['opp']}  ─  "
                             f"{stat}  평점 {im['rating']}  ({im['score']} {im['result']})")
        else:
            lines.append("  없음")
        lines.append("")

        # 승강 경험
        from game_engine import get_my_promotions
        promos_s = get_my_promotions()
        lines.append(f"▶ 승강 경험  ({len(promos_s)}건)")
        if promos_s:
            for pr in promos_s:
                ft = pr.get("from_tier", 0); tt = pr.get("to_tier", 0)
                kind = "승격" if tt < ft else "강등"
                icon = "🔼" if tt < ft else "🔽"
                lines.append(f"  {icon} {pr.get('year','')}년  {pr.get('team_name','')}"
                             f"  {pr.get('league_name','')}  {ft}부 → {tt}부 ({kind})")
        else:
            lines.append("  없음")
        lines.append("")

        # 통계 요약
        total_m = p.get("total_matches", 0)
        total_g = p.get("total_goals", 0)
        total_a = p.get("total_assists", 0)
        total_s = p.get("total_seasons", 0)
        pos_txt = p.get("position","")
        lines.append("▶ 통산 기록")
        if pos_txt == "GK":
            ts2 = p.get("total_saves",0); tga2 = p.get("total_goals_against",0)
            tot2 = ts2+tga2; sr2 = f"{round(ts2/tot2*100,1)}%" if tot2 else "0%"
            lines.append(f"  {total_s}시즌  {total_m}경기  선방 {ts2}회({sr2})  실점 {tga2}골")
        else:
            lines.append(f"  {total_s}시즌  {total_m}경기  {total_g}골  {total_a}어시스트")
        ic = p.get("intl_caps", 0)
        if ic > 0:
            if pos_txt == "GK":
                lines.append(f"  A매치 {ic}경기 출전")
            else:
                lines.append(f"  A매치 {ic}경기  {p.get('intl_goals',0)}골  {p.get('intl_assists',0)}어시스트")
        lines.append(f"  총 자산: {fmt_money(p.get('total_assets',0))}")

        self.story_box.setPlainText("\n".join(lines))
        self.gen_btn.setText("✨ 다시 생성")
        self.gen_btn.setEnabled(True)

    # ── 시작 화면으로 ─────────────────────────────────

    def _go_start(self):
        """데이터 초기화 후 MainWindow를 시작 화면으로 교체 (새 창 안 열림)."""
        if not _game_confirm(self, "시작 화면으로", "현재 게임 데이터가 삭제됩니다.\n시작 화면으로 이동하시겠습니까?"):
            return

        parent = self.parent_win
        self.close()  # 은퇴 창 닫기

        if parent and hasattr(parent, 'go_to_start'):
            parent.go_to_start()  # MainWindow를 시작 화면으로 교체