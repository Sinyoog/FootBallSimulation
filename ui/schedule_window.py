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
        intl_w = self._make_intl_tab("groups")
        if intl_w:
            self._tab.addTab(intl_w, "🌍 국제대회")
        intl_ko = self._make_intl_tab("ko")
        if intl_ko:
            self._tab.addTab(intl_ko, "🌍 국제대회(본선)")

        # 챔피언스리그 탭
        champs_w = self._make_champions_tab("groups")
        if champs_w:
            self._tab.addTab(champs_w, "🏆 챔피언스리그")
        champs_ko = self._make_champions_tab("ko")
        if champs_ko:
            self._tab.addTab(champs_ko, "🏆 챔피언스리그(본선)")

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

    def _make_intl_tab(self, mode="groups"):
        """mode='groups': 조별 순위표만 / mode='ko': 토너먼트 브래킷만."""
        import intl_engine
        from game_engine import get_state, get_player
        st = get_state()
        if not st:
            return None
        t = intl_engine.get_my_tournament(st["current_year"])
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

        # 본선(ko) 탭인데 아직 토너먼트 대진이 없으면 탭 자체를 만들지 않음
        if mode == "ko" and not ko_rows:
            return None

        # ── 조별리그 순위표 ── (groups 모드에서만)
        if mode == "groups":
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

        # ── 토너먼트 대진 → 브래킷(대진표) ── (ko 모드에서만)
        if mode == "ko" and ko_rows:
            from ui.bracket_widget import BracketWidget, build_rounds_from_matches
            lbl_k = QLabel("◼ 토너먼트")
            lbl_k.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;margin-top:6px;")
            lay.addWidget(lbl_k)

            # 등장하는 스테이지 순서를 데이터 등장 순으로 잡아 정렬 기준 생성
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
        """mode='groups': 조별 순위표만 / mode='ko': 토너먼트 브래킷만."""
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
        
        # 내 팀이 속한 리그와 국가 정보 조회
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
        
        my_continent = team_info["continent"]
        my_team_id = team_info["id"]
        
        # 내 팀의 챔피언스리그 경기 조회
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
        
        # 챔피언스리그 이름
        league_names = {
            "유럽": "유럽 챔피언스리그",
            "아시아": "아시안 챔피언스리그",
            "아프리카": "아프리카 챔피언스리그",
            "남미": "코파 리베르타도레스"
        }
        league_name = league_names.get(my_continent, f"{my_continent} 챔피언스리그")
        
        # 헤더
        hdr = QLabel(f"🏆 {st['current_year']}년 {league_name}")
        hdr.setStyleSheet("color:#ffcc00;font-size:14px;font-weight:bold;")
        lay.addWidget(hdr)
        
        sub = QLabel(f"팀: {team_info['name']} ({team_info['league_name']})")
        sub.setStyleSheet("color:#888;font-size:11px;")
        lay.addWidget(sub)

        # ── 조별리그 순위표 (전체 조) ── (groups 모드에서만)
        all_groups = champions_engine.get_my_cl_all_groups(st["current_year"])
        if mode == "groups" and all_groups and all_groups.get("groups"):
            my_tid_g = all_groups["my_team_id"]
            lbl_g = QLabel("◼ 조별리그")
            lbl_g.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
            lay.addWidget(lbl_g)
            for grp in all_groups["groups"]:
                rows = grp["standings"]
                gt = QTableWidget(len(rows), 7)
                gt.setHorizontalHeaderLabels([f"{grp['grp']}조", "경기", "승", "무", "패", "득실", "승점"])
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
                    ctry = r.get("country", "")
                    nm = f"{r.get('flag','')}{r['team_name']}"
                    if ctry:
                        nm = f"{nm} ({ctry})"
                    vals = [nm, str(r["p"]), str(r["w"]),
                            str(r["d"]), str(r["l"]), f"{'+' if gd>0 else ''}{gd}", str(r["pts"])]
                    if r["team_id"] == my_tid_g:   color = QColor("#66ccff")
                    elif i < 2:                    color = QColor("#00cc44")
                    else:                          color = QColor("#888888")
                    for j, v in enumerate(vals):
                        item = QTableWidgetItem(v)
                        if j > 0: item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        item.setForeground(color)
                        gt.setItem(i, j, item)
                gt.setFixedHeight(gt.verticalHeader().defaultSectionSize() * len(rows) + 28)
                lay.addWidget(gt)
            hint = QLabel("초록=16강 진출권(상위 2팀), 청록=내 팀")
            hint.setStyleSheet("color:#666;font-size:10px;")
            lay.addWidget(hint)
        
        # 토너먼트 매치업 → 브래킷(대진표) (ko 모드에서만)
        from ui.bracket_widget import BracketWidget, build_rounds_from_matches
        stage_order = {"32강": 0, "16강": 1, "8강": 2, "4강": 3, "결승": 4}

        bracket_matches = []
        for m in matches:
            # 조별리그 경기는 브래킷에서 제외 (위 순위표로 따로 표시)
            if m.get("stage_raw") == "group" or m["stage"] == "조별리그":
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
            # 팀명 옆에 소속 국가 표기 — 어느 나라 클럽인지 한눈에.
            #   home_league/away_league = cl_entries.country (국가명)
            home_nm = m["home_name"]
            away_nm = m["away_name"]
            h_ctry = m.get("home_league", "")
            a_ctry = m.get("away_league", "")
            if h_ctry:
                home_nm = f"{home_nm} ({h_ctry})"
            if a_ctry:
                away_nm = f"{away_nm} ({a_ctry})"
            # winner 는 위에서 bare name 으로 계산됐으므로, 표기명(국가 포함)에 맞춰
            # 다시 매핑해야 BracketWidget 의 승자 하이라이트(name==winner)가 동작한다.
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

        # 본선(ko) 탭인데 아직 토너먼트 대진이 없으면 탭 자체를 만들지 않음
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
        """대진표 크기에 맞춰 창을 자동으로 키운다 (가로/세로).

        브래킷은 라운드 수·팀 수에 따라 크기가 달라지므로,
        스크롤 없이 한눈에 보이도록 가능한 범위에서 창을 넓힌다.
        화면 밖으로 나가지 않게 사용 가능한 화면 크기로 상한을 둔다.
        """
        from PyQt6.QtWidgets import QApplication
        sh = bracket.sizeHint()
        # 탭/여백 등 크롬을 감안한 여유
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