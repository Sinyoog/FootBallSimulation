"""
ui/center_panel.py  ─  가운데 메인 패널
"""
import random
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFrame, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from game_engine import (
    get_player, get_state, advance_4weeks,
    generate_offers, join_team, get_league_standings,
    get_schedule, fmt_money
)
from constants import TRAINING_CONFIG, FOCUS_TRAIN_STATS, ALL_STATS, MATCH_STRESS


def show_toast(parent, msg, color="#cc4400", duration=1200):
    """1초 뒤 사라지는 토스트 경고"""
    lbl = QLabel(msg, parent)
    lbl.setStyleSheet(f"""
        background:{color}; color:white; font-size:13px; font-weight:bold;
        border-radius:8px; padding:10px 20px;
    """)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.adjustSize()
    pw, ph = parent.width(), parent.height()
    lbl.move((pw - lbl.width())//2, ph//2 - 40)
    lbl.raise_()
    lbl.show()
    QTimer.singleShot(duration, lbl.deleteLater)

TRAIN_OPTS_KO = ["고강도","중강도","집중훈련","저강도","휴식"]
TRAIN_MAP_KO  = {"고강도":"고강도","중강도":"중강도",
                  "집중훈련":"집중훈련","저강도":"저강도","휴식":"휴식"}
# 기본값: 휴식/중강도/중강도/휴식
TRAIN_DEFAULTS = ["휴식","중강도","중강도","휴식"]

CENTER_STYLE = """
QWidget { background-color: #1e1e1e; color: #cccccc; font-size: 12px; }
#phaseLabel { color: #00cc44; font-size: 14px; font-weight: bold; }
#noMatch    { color: #666666; font-size: 12px; }
#weekFrame  { background-color: #252525; border:1px solid #333; border-radius:6px; }
#weekTitle  { color: #aaaaaa; font-size: 11px; }
QComboBox   { background-color:#2a2a2a; color:#cccccc;
              border:1px solid #444; border-radius:4px; padding:4px; }
QComboBox QAbstractItemView { background-color:#2a2a2a; color:#cccccc;
                               selection-background-color:#3a6a3a; }
#stressHint { color: #888888; font-size: 10px; }
#advBtn     { background-color:#006622; color:white; font-size:14px;
              font-weight:bold; padding:10px; border-radius:6px; border:none; }
#advBtn:hover { background-color:#008833; }
#previewBox { background-color:#252525; border:1px solid #333; border-radius:6px; }
#actBtn     { background-color:#2a2a2a; color:#cccccc;
              border:1px solid #444; border-radius:4px; padding:6px; font-size:12px; }
#actBtn:hover { background-color:#383838; }
#actBtn:disabled { color:#444; }
#mgrLabel   { color: #888888; font-size: 12px; }
QFrame#div  { background-color: #2a2a2a; }
"""


class CenterPanel(QWidget):
    def __init__(self, main_win=None):
        super().__init__()
        self.main_win = main_win
        self.setStyleSheet(CENTER_STYLE)
        self._join_used        = False   # 이번 달 팀 입단 버튼 사용 여부
        self._auto_offer_shown = False   # 이번 구간 자동 오퍼 표시 여부
        self._build()

    # ── 빌드 ─────────────────────────────────────

    def _build(self):
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12,12,12,12)
        self.lay.setSpacing(8)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 페이즈 라벨
        self.lbl_phase = QLabel("비시즌  |  1990년 1시즌  1~4주")
        self.lbl_phase.setObjectName("phaseLabel")
        self.lay.addWidget(self.lbl_phase)

        self.lbl_no_match = QLabel("이번 달 경기 없음")
        self.lbl_no_match.setObjectName("noMatch")
        self.lay.addWidget(self.lbl_no_match)

        # 4주 스케줄
        sched_row = QHBoxLayout(); sched_row.setSpacing(8)
        self.week_combos : list[QComboBox] = []
        self.week_hints  : list[QLabel]    = []
        self.week_frames : list[QFrame]    = []

        for i in range(4):
            f = QFrame(); f.setObjectName("weekFrame")
            fl = QVBoxLayout(f); fl.setContentsMargins(8,8,8,8); fl.setSpacing(4)

            wl = QLabel(f"{i+1}주차"); wl.setObjectName("weekTitle")
            wl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            cb = QComboBox(); cb.addItems(TRAIN_OPTS_KO)
            cb.setCurrentText(TRAIN_DEFAULTS[i])
            cb.currentTextChanged.connect(self._update_preview)

            # 경기 있을 때 대체 표시용 라벨
            ml = QLabel("⚽ 경기"); ml.setObjectName("matchLabel")
            ml.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ml.setStyleSheet("color:#ffcc00;font-weight:bold;font-size:12px;"
                             "background:#1a3a1a;border-radius:4px;padding:4px;")
            ml.hide()

            hl = QLabel(""); hl.setObjectName("stressHint")
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            fl.addWidget(wl); fl.addWidget(cb); fl.addWidget(ml); fl.addWidget(hl)
            self.week_frames.append(f)
            self.week_combos.append(cb)
            self.week_hints.append(hl)
            sched_row.addWidget(f)

        self.lay.addLayout(sched_row)

        # 진행 버튼
        self.adv_btn = QPushButton("▶▶  이번 달 진행 (4주)")
        self.adv_btn.setObjectName("advBtn")
        self.adv_btn.clicked.connect(self._advance)
        self.lay.addWidget(self.adv_btn)

        # 예상 변화 박스
        pvbox = QFrame(); pvbox.setObjectName("previewBox")
        pvlay = QVBoxLayout(pvbox); pvlay.setContentsMargins(12,8,12,8)
        pvlay.addWidget(QLabel("이번 달 예상 변화"))
        self.lbl_pv_stress = QLabel("예상 스트레스: 0")
        self.lbl_pv_happy  = QLabel("예상 행복도: +0")
        self.lbl_pv_match  = QLabel("경기 수: 0경기")
        for w in [self.lbl_pv_stress, self.lbl_pv_happy, self.lbl_pv_match]:
            pvlay.addWidget(w)
        self.lay.addWidget(pvbox)

        # 액션 버튼 행1
        row1 = QHBoxLayout()
        self.btn_join     = QPushButton("🏟 팀 입단");      self.btn_join.setObjectName("actBtn")
        self.btn_standing = QPushButton("📊 순위표");       self.btn_standing.setObjectName("actBtn")
        self.btn_schedule = QPushButton("📅 경기일정");     self.btn_schedule.setObjectName("actBtn")
        for b in [self.btn_join, self.btn_standing, self.btn_schedule]:
            row1.addWidget(b)
        self.lay.addLayout(row1)

        # 액션 버튼 행2
        row2 = QHBoxLayout()
        self.btn_agent  = QPushButton("👔 에이전트");  self.btn_agent.setObjectName("actBtn")
        self.btn_retire = QPushButton("🚪 은퇴");     self.btn_retire.setObjectName("actBtn")
        for b in [self.btn_agent, self.btn_retire]:
            row2.addWidget(b)
        row2.addStretch()
        self.lay.addLayout(row2)

        # 감독 관계
        self.lbl_mgr = QLabel("감독 관계: 50"); self.lbl_mgr.setObjectName("mgrLabel")
        self.lay.addWidget(self.lbl_mgr)

        # 팀 포메이션
        from ui.formation_widget import FormationWidget
        self.formation = FormationWidget()
        self.lay.addWidget(self.formation)

        # 버튼 연결
        self.btn_join.clicked.connect(self._do_join)
        self.btn_standing.clicked.connect(self._do_standings)
        self.btn_schedule.clicked.connect(self._do_schedule)
        self.btn_agent.clicked.connect(self._do_agent)
        self.btn_retire.clicked.connect(self._do_retire)

    # ── 갱신 ─────────────────────────────────────

    def refresh(self):
        p  = get_player()
        st = get_state()
        if not p or not st:
            return

        year   = st["current_year"]
        week   = st["current_week"]
        season = st["current_season"]
        lang   = p.get("language","ko")

        phase = _half(week, lang)
        self.lbl_phase.setText(f"{phase}  |  {year}년 {season}시즌  {week}~{week+3}주")

        # 주차별 경기 여부 확인 → 경기 있으면 라벨, 없으면 콤보
        for i, (f, cb) in enumerate(zip(self.week_frames, self.week_combos)):
            w = week + i
            ph = _phase_short(w, lang)
            labels = f.findChildren(QLabel)
            # labels[0]=주차타이틀, labels[1]=matchLabel, labels[2]=stressHint
            if labels: labels[0].setText(f"{w}주차 [{ph}]")

            match_info = self._get_match(w, p) if p.get("current_team_id") else None
            # matchLabel, stressHint 찾기
            ml = next((l for l in labels if l.objectName()=="matchLabel"), None)
            hl = self.week_hints[i]

            if match_info:
                league_name = match_info.get("league_name", "")
                loc = "홈" if match_info.get("is_home") else "원정"
                stress_val = 5 if match_info.get("is_home") else 8
                cb.hide()
                hl.setText(f"스트레스 +{stress_val}")
                if ml:
                    ml.setText(f"⚽ {league_name}\n({loc})")
                    ml.show()
            else:
                if ml: ml.hide()
                cb.show()

        # 버튼 활성/비활성
        is_pre  = 1  <= week <= 4
        is_mid  = 12 <= week <= 25
        is_post = 33 <= week <= 52
        is_off  = is_pre or is_mid or is_post

        from constants import MIN_JOIN_AGE
        age = p.get("age", 16)
        has_team  = bool(p.get("current_team_id"))
        can_join  = is_pre and age >= MIN_JOIN_AGE and not has_team and not self._join_used

        self.btn_join.setEnabled(can_join)
        self.btn_join.setVisible(not has_team)
        self.btn_agent.setEnabled(is_off)
        self.btn_retire.setEnabled(is_off)  # 비시즌에만 활성화
        has_team = bool(p.get("current_team_id"))
        self.btn_standing.setEnabled(has_team)
        self.btn_schedule.setEnabled(has_team)

        # 경기 있는지
        has_match = self._check_match(week, p)
        self.lbl_no_match.setVisible(not has_match)

        # 팀 있을 때만 감독/포메이션 표시
        self.lbl_mgr.setVisible(has_team)
        self.formation.setVisible(has_team)
        if has_team:
            self.lbl_mgr.setText(f"감독 관계: {p.get('manager_relation',50)}")
            self.formation.load_team(p["current_team_id"])

        self._update_preview()

    def _check_match(self, week, p):
        lid = p.get("current_league_id",0)
        tid = p.get("current_team_id",0)
        if not lid or not tid: return False
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as n FROM match_results WHERE league_id=? "
            "AND week BETWEEN ? AND ? AND (home_team_id=? OR away_team_id=?)",
            (lid, week, week+3, tid, tid)).fetchone()
        conn.close()
        return row["n"] > 0

    def _update_preview(self):
        total_stress = 0
        total_happy  = 0
        for i, cb in enumerate(self.week_combos):
            sel   = cb.currentText()
            ttype = TRAIN_MAP_KO.get(sel, "중강도")
            cfg   = TRAINING_CONFIG.get(ttype, TRAINING_CONFIG["중강도"])
            s_chg = cfg["stress"]
            total_stress += s_chg
            if ttype == "휴식": total_happy += 6
            sign = "+" if s_chg >= 0 else ""
            self.week_hints[i].setText(f"스트레스 {sign}{s_chg}")

        ss = "+" if total_stress >= 0 else ""
        hs = "+" if total_happy  >= 0 else ""
        self.lbl_pv_stress.setText(f"예상 스트레스: {ss}{total_stress}")
        self.lbl_pv_happy.setText(f"예상 행복도: {hs}{total_happy}")

    # ── 4주 진행 ─────────────────────────────────

    def _advance(self):
        p  = get_player()
        st = get_state()
        if not p or not st: return

        week = st["current_week"]
        from constants import MIN_JOIN_AGE

        # 17살 이상인데 팀이 없고 비시즌(1~4주)이면 입단 강제
        if p["age"] >= MIN_JOIN_AGE and not p.get("current_team_id") and 1 <= week <= 4:
            show_toast(self, "⚠  먼저 팀에 입단해야 합니다!", "#cc6600", 1500)
            return

        schedule = []
        for i, cb in enumerate(self.week_combos):
            w     = week + i
            sel   = cb.currentText()
            ttype = TRAIN_MAP_KO.get(sel, "중강도")
            match_info = self._get_match(w, p)
            if match_info:
                schedule.append((w, "경기", match_info))
            else:
                detail = None
                if ttype == "집중훈련":
                    pool = FOCUS_TRAIN_STATS.get(p["position"], ALL_STATS[:3])
                    detail = pool[0]
                schedule.append((w, ttype, detail))

        advance_4weeks(schedule)

        # 4주 진행 후 플래그 초기화
        self._join_used = False

        p2 = get_player(); st2 = get_state()
        new_week = st2["current_week"]
        from constants import MIN_JOIN_AGE

        # 구간 경계 진입 시 자동 오퍼 플래그 리셋
        OFFER_ZONES = [(1,4),(13,16),(17,20),(21,24)]

        def _which_zone(w):
            for s, e in OFFER_ZONES:
                if s <= w <= e:
                    return (s, e)
            return None

        in_zone  = _which_zone(new_week)
        prev_week = new_week - 4
        prev_zone = _which_zone(prev_week)
        # 새 구간에 진입했으면 리셋 (이전 구간과 다른 구간이거나 구간 밖→안)
        if in_zone and in_zone != prev_zone:
            self._auto_offer_shown = False

        # 소속 없으면 입단 안내
        if 1 <= new_week <= 4 and p2.get("age",0) >= MIN_JOIN_AGE and not p2.get("current_team_id"):
            show_toast(self, f"⭐ {st2['current_year']}년 새 시즌!  팀 입단 기간입니다", "#006622", 2000)

        # 재계약 오퍼 팝업 체크
        if p2.get("_contract_renew_offer", 0) > 0:
            self._show_renew_dialog(p2)

        # 자동 오퍼 타이밍 체크
        if p2.get("current_team_id") and in_zone:
            self._show_auto_offer(new_week)  # in_zone이 tuple이면 truthy

        if self.main_win:
            self.main_win.refresh_all()

    def _get_match(self, week, p):
        tid = p.get("current_team_id", 0)
        if not tid: return None

        from database import get_conn
        conn = get_conn()
        # 항상 DB에서 팀의 실제 league_id 재조회 (이적/승강 후 변경 반영)
        team_row = conn.execute(
            "SELECT l.id as lid, l.name as lname FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
            (tid,)).fetchone()
        if not team_row:
            conn.close(); return None
        lid = team_row["lid"]

        from game_engine import get_state
        st = get_state()
        cur_season = st["current_season"] if st else 1
        row = conn.execute(
            "SELECT * FROM match_results WHERE league_id=? AND week=? "
            "AND (home_team_id=? OR away_team_id=?) AND home_score=-1 AND season=?",
            (lid, week, tid, tid, cur_season)).fetchone()
        conn.close()
        if not row: return None

        return {
            "home_id":     row["home_team_id"],
            "away_id":     row["away_team_id"],
            "league_name": team_row["lname"],
            "league_id":   lid,
            "is_home":     row["home_team_id"] == tid,
            "season":      row["season"],
            "year":        row["year"],
        }

    # ── 액션 ─────────────────────────────────────

    def _show_renew_dialog(self, p):
        """재계약 팝업: 연봉 확인 + 계약 기간 선택 (1~5년)."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QSpinBox, QPushButton)
        from game_engine import join_team, update_player, get_state

        offer_sal = p.get("_contract_renew_offer", 0)
        from database import get_conn
        _conn = get_conn()
        _row  = _conn.execute("SELECT name FROM teams WHERE id=?", (p.get("current_team_id",0),)).fetchone()
        _conn.close()
        team_name = _row["name"] if _row else "현재 팀"

        dlg = QDialog(self)
        dlg.setWindowTitle("📋 재계약 제안")
        dlg.setMinimumWidth(320)
        lay = QVBoxLayout(dlg)

        lay.addWidget(QLabel(f"<b>{team_name}</b>에서 재계약을 제안합니다."))
        lay.addWidget(QLabel(f"제시 연봉: <span style='color:#00cc44'>{offer_sal:,}만원 / 년</span>"))
        lay.addWidget(QLabel("계약 기간을 선택하세요:"))

        spin = QSpinBox()
        spin.setRange(1, 5); spin.setValue(3)
        spin.setSuffix("년")
        lay.addWidget(spin)

        btn_row = QHBoxLayout()
        btn_accept = QPushButton("✅ 수락")
        btn_reject = QPushButton("❌ 거절 (소속 없음)")
        btn_accept.setStyleSheet("background:#006622;color:white;padding:6px 16px;")
        btn_reject.setStyleSheet("background:#660000;color:white;padding:6px 16px;")
        btn_row.addWidget(btn_accept); btn_row.addWidget(btn_reject)
        lay.addLayout(btn_row)

        def _accept():
            yrs = spin.value()
            st  = get_state()
            end = st["current_year"] + yrs
            from game_engine import update_player as upd
            upd(contract_years=yrs, contract_end_year=end,
                salary=offer_sal, _contract_renew_offer=0)
            from game_engine import add_log, fmt_money
            add_log(f"✅ 재계약 완료! {yrs}년 계약  |  연봉 {fmt_money(offer_sal)}", "event")
            dlg.accept()
            if self.main_win: self.main_win.refresh_all()

        def _reject():
            from game_engine import update_player as upd, _save_career_entry, get_player
            p3 = get_player()
            st  = get_state()
            if p3:
                # 연말 항목은 이미 닫혔으므로 allow_insert=False (유령 행 방지)
                _save_career_entry(p3, st["current_year"], st["current_week"],
                                   transfer_type="방출", allow_insert=False)
            upd(current_team_id=0, current_league_id=0,
                salary=0, contract_years=0, contract_end_year=0,
                _contract_renew_offer=0)
            from game_engine import add_log
            add_log("📋 재계약 거절. 소속 없음 상태가 됩니다.", "event")
            dlg.reject()
            if self.main_win: self.main_win.refresh_all()

        btn_accept.clicked.connect(_accept)
        btn_reject.clicked.connect(_reject)
        dlg.exec()

    def _do_join(self):
        """소속 없음일 때만 수동 팀 입단 (1~4주차)."""
        p = get_player()
        if not p: return
        if p.get("current_team_id"):
            show_toast(self, "⚠  소속 팀이 있을 때는 오퍼를 기다리세요")
            return
        from constants import MIN_JOIN_AGE
        if p["age"] < MIN_JOIN_AGE:
            show_toast(self, f"⚠  {MIN_JOIN_AGE}살부터 팀에 입단할 수 있습니다")
            return
        self._join_used = True
        self.btn_join.setEnabled(False)
        offers = generate_offers()
        is_first = (p.get("total_matches", 0) == 0)
        from ui.offer_window import OfferWindow
        dlg = OfferWindow(offers, p.get("language","ko"), self,
                          title="🏟 팀 입단", force_select=is_first)
        self._offer_dlg = dlg
        dlg.finished.connect(lambda: self._on_join_done(dlg))
        dlg.show()

    def _on_join_done(self, dlg):
        if dlg.chosen:
            join_team(dlg.chosen["team_id"], dlg.chosen["salary"], transfer_type="입단")
            if self.main_win: self.main_win.refresh_all()

    def _show_auto_offer(self, week: int):
        """소속 있을 때 자동 오퍼 팝업 (1~4, 13~16, 17~20, 21~24주차)."""
        from game_engine import _offer_probability
        p = get_player()
        if not p or not p.get("current_team_id"): return
        if self._auto_offer_shown: return

        prob = _offer_probability(p, week)
        import random
        if random.random() > prob:
            return  # 이번 구간 오퍼 없음

        self._auto_offer_shown = True
        offers = generate_offers()
        if not offers: return

        from ui.offer_window import OfferWindow
        dlg = OfferWindow(offers, p.get("language","ko"), self, title="✈ 오퍼")
        self._offer_dlg = dlg
        dlg.finished.connect(lambda: self._on_auto_offer_done(dlg))
        dlg.show()

    def _on_auto_offer_done(self, dlg):
        if dlg.chosen:
            join_team(dlg.chosen["team_id"], dlg.chosen["salary"], transfer_type="오퍼")
            if self.main_win: self.main_win.refresh_all()

    def _do_standings(self):
        p = get_player()
        if not p or not p.get("current_league_id"):
            show_toast(self, "⚠  소속 팀이 없습니다"); return
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
            (p["current_team_id"],)).fetchone()
        conn.close()
        lid = row["id"] if row else p["current_league_id"]
        from ui.standings_window import StandingsWindow
        self._standings_win = StandingsWindow(lid, p.get("current_team_id", 0),
                                              p.get("language", "ko"), self)
        self._standings_win.show()

    def _do_schedule(self):
        p  = get_player()
        st = get_state()
        if not p or not p.get("current_league_id"):
            show_toast(self, "⚠  소속 팀이 없습니다"); return
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
            (p["current_team_id"],)).fetchone()
        conn.close()
        lid = row["id"] if row else p["current_league_id"]
        from ui.schedule_window import ScheduleWindow
        self._schedule_win = ScheduleWindow(lid, p.get("current_team_id", 0),
                                            st["current_season"], p.get("language", "ko"), self)
        self._schedule_win.show()

    def _do_agent(self):
        p = get_player()
        if not p: return
        st = get_state()
        week = st["current_week"]
        is_off = not (5 <= week <= 11 or 26 <= week <= 32)
        if not is_off:
            show_toast(self, "⚠  에이전트 변경은 비시즌에만 가능합니다")
            return
        from ui.agent_window import AgentWindow
        self._agent_dlg = AgentWindow(p.get("language", "ko"), self)
        self._agent_dlg.finished.connect(lambda: self._on_agent_done(self._agent_dlg))
        self._agent_dlg.show()

    def _on_agent_done(self, dlg):
        if self.main_win: self.main_win.refresh_all()

    def _do_retire(self):
        st = get_state()
        week = st["current_week"]
        is_off = not (5 <= week <= 11 or 26 <= week <= 32)
        if not is_off:
            show_toast(self, "⚠  은퇴는 비시즌에만 가능합니다", "#cc6600", 1500)
            return
        reply = QMessageBox.question(self, "은퇴 확인", "정말 은퇴하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            from ui.retire_window import RetireWindow
            # parent를 MainWindow로 전달 (go_to_start 호출용)
            main_win = self.window()
            self._retire_win = RetireWindow(get_player().get("language", "ko"), main_win)
            self._retire_win.show()


# ── 헬퍼 ──────────────────────────────────────────────────────

def _half(week, lang):
    if 5<=week<=11:  return "🏆 상반기"  if lang=="ko" else "🏆 First Half"
    if 26<=week<=32: return "🏆 하반기"  if lang=="ko" else "🏆 Second Half"
    return "☀ 비시즌" if lang=="ko" else "☀ Off-Season"

def _phase_short(week, lang):
    if 1<=week<=4:   return "비시즌" if lang=="ko" else "Pre"
    if 5<=week<=11:  return "상반기" if lang=="ko" else "1st"
    if 12<=week<=25: return "비시즌" if lang=="ko" else "Mid"
    if 26<=week<=32: return "하반기" if lang=="ko" else "2nd"
    return "비시즌" if lang=="ko" else "Off"