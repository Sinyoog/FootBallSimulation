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

        # 챔피언스리그 탭
        champs_w = self._make_champions_tab("groups")
        if champs_w:
            self._tab.addTab(champs_w, "🏆 챔피언스리그")
        champs_ko = self._make_champions_tab("ko")
        if champs_ko:
            self._tab.addTab(champs_ko, "🏆 챔피언스리그(본선)")

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
            status_txt = "예선 진행 중"
            if t["status"] == "done":
                status_txt = f"예선 종료  |  결과: {t.get('my_result','') or '─'}"
            icon = "🌏"
        else:
            status_txt = {"group": "조별리그 진행 중", "ko": "토너먼트 진행 중"}.get(t["status"], "")
            if t["status"] == "done":
                status_txt = f"종료  |  🏆 우승: {t['winner']}"
            icon = "🌍"
        hdr = QLabel(f"{icon} {t['name']}  ─  {status_txt}")
        hdr.setStyleSheet("color:#66ccff;font-size:14px;font-weight:bold;")
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
        if _is_qual:
            ko_rows = []
        else:
            ko_rows = [dict(r) for r in conn.execute(
                """SELECT * FROM intl_matches WHERE tournament_id=? AND stage!='group'
                   ORDER BY week, slot""", (t["id"],)).fetchall()]
        flags = {r["country"]: r["flag"] for r in conn.execute(
            "SELECT country, flag FROM intl_entries WHERE tournament_id=?",
            (t["id"],)).fetchall()}
        conn.close()

        if mode == "ko" and not ko_rows:
            return None

        # ── 조별리그 순위표 ──
        if mode == "groups":
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

            # 범례
            hint_parts = ["🟢진출확정", "🔵내 국가"]
            if has_thirds:
                hint_parts.append("🟡3위진출경쟁")
                hint_parts.append("🟩3위진출확정")
            hint_parts.append("⬜탈락")
            hint = QLabel("  ".join(hint_parts))
            hint.setStyleSheet("color:#666;font-size:10px;margin-top:4px;")
            lay.addWidget(hint)

        # ── 토너먼트 브래킷 ──
        if mode == "ko" and ko_rows:
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

        # ── 조별리그 순위표 ──
        all_groups = champions_engine.get_my_cl_all_groups(st["current_year"])
        if mode == "groups" and all_groups and all_groups.get("groups"):
            my_tid_g = all_groups["my_team_id"]
            lbl_g = QLabel("◼ 조별리그")
            lbl_g.setStyleSheet("color:#00cc44;font-weight:bold;font-size:12px;")
            lay.addWidget(lbl_g)

            # 챔스 조별리그 종료 여부 확인 → 진출 확정 팀 수집
            t_cl = champions_engine._my_cl_tournament(p, st["current_year"])
            cl_done = t_cl and t_cl.get("status") in ("ko", "done")

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

                    # 챔스도 동일한 색상 규칙: 초록=진출, 청록=내 팀, 회색=탈락
                    if r["team_id"] == my_tid_g:
                        color = COLOR_MY
                    elif i < 2:
                        # 조별리그 종료 후면 진출 확정, 진행 중이면 진출권 표시
                        color = COLOR_ADVANCE
                    else:
                        color = COLOR_ELIM

                    for j, v in enumerate(vals):
                        item = QTableWidgetItem(v)
                        if j > 0: item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                        item.setForeground(color)
                        gt.setItem(i, j, item)

                gt.setFixedHeight(gt.verticalHeader().defaultSectionSize() * len(rows) + 28)
                lay.addWidget(gt)

            hint = QLabel("🟢진출확정(상위 2팀)  🔵내 팀  ⬜탈락")
            hint.setStyleSheet("color:#666;font-size:10px;margin-top:4px;")
            lay.addWidget(hint)

        # ── 토너먼트 브래킷 ──
        from ui.bracket_widget import BracketWidget, build_rounds_from_matches
        stage_order = {"32강": 0, "16강": 1, "8강": 2, "4강": 3, "결승": 4}

        bracket_matches = []
        for m in matches:
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

    def _make_table(self, data, my_view=True):
        w   = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
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

        tbl.resizeColumnsToContents()
        total_w = sum(tbl.columnWidth(j) for j in range(tbl.columnCount())) + 60
        self.setMinimumWidth(max(700, total_w))
        if self.width() < total_w:
            self.resize(total_w, self.height())

        return w