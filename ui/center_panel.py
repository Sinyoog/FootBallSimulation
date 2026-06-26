"""
ui/center_panel.py  ─  가운데 메인 패널
"""
import random
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFrame, QMessageBox,
    QGraphicsDropShadowEffect
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor

from game_engine import (
    get_player, get_state, set_state, advance_4weeks,
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

TRAIN_OPTS_KO = ["고강도","중강도","강점훈련","약점훈련","저강도","휴식"]
TRAIN_MAP_KO  = {"고강도":"고강도","중강도":"중강도",
                  "강점훈련":"강점훈련","약점훈련":"약점훈련","저강도":"저강도","휴식":"휴식"}
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
QComboBox:disabled { color:#666; background-color:#222; }
#stressHint { color: #888888; font-size: 10px; }
#advBtn     { background-color:#006622; color:white; font-size:14px;
              font-weight:bold; padding:10px; border-radius:6px; border:none; }
#advBtn:hover { background-color:#008833; }
#modeBtn    { background-color:#333; color:#cccccc; font-size:12px;
              font-weight:bold; padding:10px; border-radius:6px; border:1px solid #555; }
#modeBtn:hover  { background-color:#444; }
#modeBtn:checked { background-color:#664400; color:#ffdd88; border:1px solid #886600; }
/* 전환 가능할 때(묶음 시작 전) 파란색으로 강조 */
#modeBtn[switchable="true"] { background-color:#1a4d8f; color:#ffffff; border:1px solid #3a7fd5; }
#modeBtn[switchable="true"]:hover { background-color:#2360ad; }
#modeBtn:disabled { background-color:#2a2a2a; color:#555; border:1px solid #3a3a3a; }
#previewBox { background-color:#252525; border:1px solid #333; border-radius:6px; }
#actBtn     { background-color:#2a2a2a; color:#cccccc;
              border:1px solid #444; border-radius:4px; padding:6px; font-size:12px; }
#actBtn:hover { background-color:#383838; }
#actBtn:disabled { color:#444; }
#mgrLabel   { color: #888888; font-size: 12px; }
QFrame#div  { background-color: #2a2a2a; }
"""

# 팝업(재계약·대표팀 선택) 공용 다크 스타일 — offer_window 톤과 통일
_DIALOG_STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QLabel  { color:#cccccc; font-size:13px; }
#dlgHeader { color:#00cc44; font-size:15px; font-weight:bold; }
#dlgCard   { background:#252525; border:1px solid #333; border-radius:8px; }
#dlgSpin   { background:#2a2a2a; color:#fff; border:1px solid #444;
             border-radius:4px; padding:6px; font-size:13px; }
#dlgSpin::up-button, #dlgSpin::down-button { width:18px; background:#333; border:none; }
#dlgSpin::up-arrow   { image:none; border-left:4px solid transparent; border-right:4px solid transparent;
                       border-bottom:6px solid #aaa; }
#dlgSpin::down-arrow { image:none; border-left:4px solid transparent; border-right:4px solid transparent;
                       border-top:6px solid #aaa; }
#dlgOk   { background:#2a6a2a; color:white; border:none; border-radius:6px;
           padding:9px 14px; font-size:13px; font-weight:bold; }
#dlgOk:hover { background:#3a8a3a; }
#dlgNo   { background:#7a2222; color:white; border:none; border-radius:6px;
           padding:9px 14px; font-size:13px; font-weight:bold; }
#dlgNo:hover { background:#9a3030; }
#dlgChoice { background:#1a4d8f; color:white; border:1px solid #3a7fd5;
             border-radius:6px; padding:12px 14px; font-size:14px; font-weight:bold; }
#dlgChoice:hover { background:#2360ad; }
"""


class CenterPanel(QWidget):
    def __init__(self, main_win=None):
        super().__init__()
        self.main_win = main_win
        self.setStyleSheet(CENTER_STYLE)
        self._join_used        = False   # 이번 달 팀 입단 버튼 사용 여부
        self._skip_join_lock   = False   # 전부 결렬→1년 훈련 보류 플래그
        self._auto_offer_shown = False   # 이번 구간 자동 오퍼 표시 여부
        # ── 1주씩 보기 상태 ──
        # _step_mode : 1주씩 보기 on/off
        # _locked_sched : 1주씩 진행 시작 시 고정한 4주 일정 (4개)
        # _step_idx : 현재 묶음에서 진행한 주 수 (0~3). 0이면 묶음 시작 전.
        self._step_mode    = False
        self._locked_sched = None
        self._step_idx     = 0
        self._restoring    = False   # 복원 중 콤보 시그널이 저장을 되부르는 것 방지
        self._build()
        # 세이브에 저장된 메인 화면 상태(모드/묶음/콤보)를 복원한다.
        self._restore_ui_state()

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

            # "진행할 주" 강조용 형광 발광 효과 (평소엔 꺼둠)
            glow = QGraphicsDropShadowEffect(f)
            glow.setColor(QColor("#00ff88"))
            glow.setOffset(0, 0)
            glow.setBlurRadius(0)
            f.setGraphicsEffect(glow)
            glow.setEnabled(False)
            anim = QPropertyAnimation(glow, b"blurRadius", self)
            anim.setStartValue(10)
            anim.setEndValue(30)
            anim.setDuration(900)
            anim.setEasingCurve(QEasingCurve.Type.InOutSine)
            anim.setLoopCount(-1)      # 무한 반복 (숨쉬듯 펄스)
            f._glow = glow
            f._glow_anim = anim

            self.week_frames.append(f)
            self.week_combos.append(cb)
            self.week_hints.append(hl)
            sched_row.addWidget(f)

        self.lay.addLayout(sched_row)

        # 진행 버튼 + 모드 토글
        adv_row = QHBoxLayout(); adv_row.setSpacing(8)
        self.adv_btn = QPushButton("▶▶  이번 달 진행 (4주)")
        self.adv_btn.setObjectName("advBtn")
        self.adv_btn.clicked.connect(self._advance)
        adv_row.addWidget(self.adv_btn, 1)

        # 1주/4주 모드 토글
        self.btn_mode = QPushButton("📅 4주씩")
        self.btn_mode.setObjectName("modeBtn")
        self.btn_mode.setCheckable(True)
        self.btn_mode.setToolTip("클릭하면 1주씩 보기 / 4주씩 보기 전환")
        self.btn_mode.clicked.connect(self._toggle_mode)
        adv_row.addWidget(self.btn_mode)
        self.lay.addLayout(adv_row)

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

        # 팀 포메이션 (감독 관계는 위젯 내부에 표시)
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

    def _save_ui_state(self):
        """메인 화면의 진행 상태(모드/묶음/콤보 일정)를 세이브에 영속화한다.
        상태가 바뀌는 모든 지점(모드 토글·콤보 변경·진행)에서 호출한다.
        복원 도중(_restoring)에는 저장을 건너뛴다(자기 자신을 되부르지 않게)."""
        if getattr(self, "_restoring", False):
            return
        try:
            combos = [cb.currentText() for cb in self.week_combos]
        except Exception:
            combos = []
        # locked_sched 직렬화: 각 항목은 (week, type, match_info).
        #   match_info(dict/Row)는 진행 시점에 _get_match 로 다시 조회하면 되므로
        #   저장하지 않는다(직렬화 깨짐 방지). 주차·훈련타입만 보존한다.
        locked = ""
        if self._locked_sched is not None:
            try:
                slim = []
                for item in self._locked_sched:
                    w, ttype = item[0], item[1]
                    slim.append([w, ttype])
                locked = json.dumps(slim, ensure_ascii=False)
            except Exception:
                locked = ""
        try:
            set_state(
                step_mode    = 1 if self._step_mode else 0,
                step_idx     = int(self._step_idx),
                locked_sched = locked,
                week_combos  = json.dumps(combos, ensure_ascii=False),
            )
        except Exception:
            # season_state 컬럼이 아직 없는 구버전 등 — 저장 실패해도 게임은 계속.
            pass

    def _restore_ui_state(self):
        """세이브에 저장된 진행 상태를 위젯에 복원한다(__init__ 빌드 직후 1회).
        저장된 값이 없으면(신규/구버전 세이브) 안전한 기본값으로 둔다."""
        self._restoring = True
        try:
            st = get_state() or {}

            # 1) 콤보(훈련 선택) 복원
            combos_raw = st.get("week_combos") or ""
            if combos_raw:
                try:
                    combos = json.loads(combos_raw)
                    for i, cb in enumerate(self.week_combos):
                        if i < len(combos) and combos[i] in TRAIN_OPTS_KO:
                            cb.setCurrentText(combos[i])
                except Exception:
                    pass

            # 2) 모드 복원
            self._step_mode = bool(st.get("step_mode", 0))

            # 3) 묶음 진행 위치 / 고정 일정 복원
            locked_raw = st.get("locked_sched") or ""
            if self._step_mode and locked_raw:
                try:
                    slim = json.loads(locked_raw)   # [[week, ttype], ...] 4개
                    if isinstance(slim, list) and len(slim) == 4:
                        from game_engine import get_player
                        p = get_player() or {}
                        rebuilt = []
                        for w, ttype in slim:
                            mi = (self._get_match(w, p)
                                  if p.get("current_team_id") else None)
                            if mi:
                                rebuilt.append((w, "경기", mi))
                            else:
                                rebuilt.append((w, ttype, None))
                        self._locked_sched = rebuilt
                        idx = int(st.get("step_idx", 0))
                        self._step_idx = max(0, min(idx, 3))
                    else:
                        self._locked_sched = None
                        self._step_idx     = 0
                except Exception:
                    self._locked_sched = None
                    self._step_idx     = 0
            else:
                # 4주 모드이거나 진행 중 묶음이 없음 → 깨끗한 시작 상태
                self._locked_sched = None
                self._step_idx     = 0

            # 토글 버튼 라벨/체크 상태를 복원된 모드에 맞춘다.
            try:
                self.btn_mode.setChecked(self._step_mode)
                self.btn_mode.setText("📆 1주씩" if self._step_mode else "📅 4주씩")
            except Exception:
                pass
        finally:
            self._restoring = False

    def _set_glow(self, frame, on):
        """주차 프레임의 형광 발광 효과 on/off + 테두리 강조."""
        glow = getattr(frame, "_glow", None)
        anim = getattr(frame, "_glow_anim", None)
        if glow is None:
            return
        if on:
            if not glow.isEnabled():
                glow.setEnabled(True)
                # 형광 테두리로 박스 가장자리 자체도 강조
                frame.setStyleSheet(
                    "#weekFrame{background-color:#1f2a1f;"
                    "border:2px solid #00ff88;border-radius:6px;}")
                if anim:
                    anim.start()
        else:
            if glow.isEnabled():
                glow.setEnabled(False)
                if anim:
                    anim.stop()
                glow.setBlurRadius(0)
            # 기본 스타일 복귀 (전역 스타일시트에 위임)
            frame.setStyleSheet("")

    def refresh(self):
        p  = get_player()
        st = get_state()
        if not p or not st:
            return

        year   = st["current_year"]
        week   = st["current_week"]
        season = st["current_season"]
        lang   = p.get("language","ko")

        # 1주씩 모드인데 고정된 묶음 일정이 없으면 '묶음 시작 전' 상태다.
        #   (예전엔 (week-1)%4 로 위치를 추측했으나, 국제대회/강제진행 등으로
        #    4주 경계가 깨지면 엉뚱한 묶음으로 잘못 복원돼 일정이 어긋났다.
        #    이제 진행 상태는 _save/_restore_ui_state 로 정확히 영속화하므로,
        #    일정이 없으면 추측하지 말고 깨끗한 시작(idx=0)으로 둔다.)
        if self._step_mode and self._locked_sched is None:
            self._step_idx = 0

        # 표시 기준 묶음 시작 주차 = 현재주 - 진행한 주수
        bundle_start = week - self._step_idx if self._step_mode else week

        phase = _half(bundle_start, lang)

        if self._step_mode:
            done = self._step_idx
            self.lbl_phase.setText(
                f"{phase}  |  {year}년 {season}시즌  "
                f"{bundle_start}~{bundle_start+3}주  (1주씩 {done}/4)")
            self.adv_btn.setText(f"▶  1주 진행  ({done+1}/4주차)")
        else:
            self.lbl_phase.setText(
                f"{phase}  |  {year}년 {season}시즌  {week}~{week+3}주")
            self.adv_btn.setText("▶▶  이번 달 진행 (4주)")

        # 주차별 표시 (프레임 4칸은 항상 [묶음 시작 ~ +3])
        for i, (f, cb) in enumerate(zip(self.week_frames, self.week_combos)):
            w  = bundle_start + i
            ph = _phase_short(w, lang)
            labels = f.findChildren(QLabel)
            # labels[0]=주차타이틀, labels[1]=matchLabel, labels[2]=stressHint

            if self._step_mode:
                # 1주씩: 콤보 잠금(묶음 일정 고정), 진행 상태 표시
                cb.setEnabled(False)
                if i < self._step_idx:
                    tag = "✓ 완료"; f.setEnabled(False)
                    self._set_glow(f, False)
                elif i == self._step_idx:
                    tag = "▶ 진행할 주"; f.setEnabled(True)
                    self._set_glow(f, True)      # 진행할 주만 형광 발광
                else:
                    tag = "대기"; f.setEnabled(False)
                    self._set_glow(f, False)
                if labels: labels[0].setText(f"{w}주차 [{ph}]  {tag}")
            else:
                cb.setEnabled(True)
                f.setEnabled(True)
                self._set_glow(f, False)         # 4주 모드: 강조 없음
                if labels: labels[0].setText(f"{w}주차 [{ph}]")

            match_info = (self._get_match(w, p)
                          if p.get("current_team_id") else None)
            # matchLabel, stressHint 찾기
            ml = next((l for l in labels if l.objectName()=="matchLabel"), None)
            hl = self.week_hints[i]

            if match_info:
                cb.hide()
                if match_info.get("intl"):
                    # 국가대표 경기 (월드컵/대륙컵)
                    stage = match_info.get("stage_ko", "")
                    grp   = f" {match_info['grp']}조" if match_info.get("grp") else ""
                    opp   = f"{match_info.get('opp_flag','')}{match_info.get('opp','')}"
                    hl.setText("스트레스 +8")
                    if ml:
                        ml.setText(f"🌍 {match_info['league_name']} {stage}{grp}\nvs {opp}")
                        ml.setStyleSheet("color:#66ccff;font-weight:bold;font-size:12px;"
                                         "background:#1a2a3a;border-radius:4px;padding:4px;")
                        ml.show()
                elif match_info.get("cl"):
                    # 클럽 대륙 챔피언스리그
                    stage = match_info.get("stage_ko", "")
                    opp   = f"{match_info.get('opp_flag','')}{match_info.get('opp','')}"
                    loc   = "홈" if match_info.get("is_home") else "원정"
                    hl.setText("스트레스 +8")
                    if ml:
                        ml.setText(f"🏆 {match_info['league_name']} {stage} ({loc})\nvs {opp}")
                        ml.setStyleSheet("color:#ffd24d;font-weight:bold;font-size:12px;"
                                         "background:#3a2f1a;border-radius:4px;padding:4px;")
                        ml.show()
                else:
                    league_name = match_info.get("league_name", "")
                    loc = "홈" if match_info.get("is_home") else "원정"
                    stress_val = 5 if match_info.get("is_home") else 8
                    hl.setText(f"스트레스 +{stress_val}")
                    if ml:
                        ml.setText(f"⚽ {league_name}\n({loc})")
                        ml.setStyleSheet("color:#ffcc00;font-weight:bold;font-size:12px;"
                                         "background:#1a3a1a;border-radius:4px;padding:4px;")
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
        # [은퇴] 리그 경기(35주)가 끝나고 우승·수상이 확정 가능한 37~52주,
        #   그리고 새 시즌 시작 직후이자 계약 연장 거절 타이밍인 1~4주차에 허용.
        #   - 1~4주: 직전 시즌은 이미 시즌전환(_end_of_season)으로 우승·수상이
        #     확정됐고, 새 시즌은 아직 경기가 없어 누락 위험이 없다.
        #   - 12주·26~36주 등 리그 진행 중에는 여전히 은퇴 불가(우승 누락 방지).
        can_retire = (1 <= week <= 4) or (37 <= week <= 52)
        self.btn_retire.setEnabled(can_retire)
        has_team = bool(p.get("current_team_id"))
        self.btn_standing.setEnabled(has_team)
        self.btn_schedule.setEnabled(has_team)

        # 모드 토글 버튼: 묶음 진행 중(_step_idx>0)엔 전환 불가 → 회색 비활성.
        # 전환 가능할 때(묶음 시작 전)는 파란색으로 강조.
        switchable = (self._step_idx == 0)
        self.btn_mode.setEnabled(switchable)
        self.btn_mode.setProperty("switchable", "true" if switchable else "false")
        self.btn_mode.style().unpolish(self.btn_mode)
        self.btn_mode.style().polish(self.btn_mode)

        # 경기 있는지
        has_match = self._check_match(week, p)
        self.lbl_no_match.setVisible(not has_match)

        # 팀 있을 때만 감독/포메이션 표시
        self.formation.setVisible(has_team)
        if has_team:
            # 현재 대회 컨텍스트 감지 → 포메이션 위젯에 전달
            _ctx = self._get_formation_context(week, p)
            self.formation.load_team(
                p["current_team_id"],
                context=_ctx,
                manager_rel=p.get("manager_relation", 50))

        self._update_preview()

    def _get_formation_context(self, week, p):
        """현재 주차에 진행 중인 대회 컨텍스트를 반환. 리그면 None."""
        # 국가대표 대회 확인
        try:
            import intl_engine
            from game_engine import get_state
            st = get_state()
            t = intl_engine.get_my_tournament(st["current_year"]) if st else None
            if t and t.get("my_selected") == 1 and t.get("status") != "done":
                nat = t.get("my_nat") or p.get("nationality1", "")
                if not nat:
                    # _my_nat 헬퍼 없이 직접 추출
                    nat = p.get("fixed_nat") or p.get("nationality1", "")
                return {
                    "intl": True,
                    "tournament_id": t["id"],
                    "league_name": t["name"],
                    "my_nat": nat,
                    "stage": "group",   # get_my_match로 정확한 스테이지 알 수 있지만 group이 기본
                    "stage_ko": "",
                    "week": week,
                }
        except Exception:
            pass
        # 챔피언스리그 확인 (41~52주)
        try:
            import champions_engine
            cl_m = champions_engine.get_my_cl_match(week)
            if cl_m:
                return {
                    "cl": True,
                    "tournament_id": cl_m["tournament_id"],
                    "league_name": cl_m.get("league_name", ""),
                    "stage": cl_m.get("stage", "group"),
                    "stage_ko": cl_m.get("stage_ko", ""),
                    "week": week,
                }
        except Exception:
            pass
        return None

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
        if row["n"] > 0:
            return True
        import intl_engine
        if intl_engine.has_my_match_between(week, week+3):
            return True
        import champions_engine
        return champions_engine.has_my_cl_match_between(week, week+3)

    def _update_preview(self):
        total_stress = 0
        # 휴식 행복도는 실제로 random.randint(4,8) → 평균 6으로 추산하되,
        # 표시는 범위(+4~8)임을 알 수 있게 한다.
        rest_count = 0
        # 성격/신체특징 stress_mult 가 반영된 '실제 적용' 스트레스를 표시한다.
        from game_engine import effective_training_stress, get_player
        p = get_player() or {}
        for i, cb in enumerate(self.week_combos):
            # 경기 주차는 콤보가 숨겨지고(week_hints 에 경기 스트레스가 표시됨)
            # 훈련 대상이 아니므로 미리보기 합산/덮어쓰기에서 제외한다.
            if not cb.isVisible():
                continue
            sel   = cb.currentText()
            ttype = TRAIN_MAP_KO.get(sel, "중강도")
            s_chg = effective_training_stress(p, ttype)
            total_stress += s_chg
            if ttype == "휴식":
                rest_count += 1
            sign = "+" if s_chg >= 0 else ""
            self.week_hints[i].setText(f"스트레스 {sign}{s_chg}")

        ss = "+" if total_stress >= 0 else ""
        self.lbl_pv_stress.setText(f"예상 스트레스: {ss}{total_stress}")
        if rest_count:
            # 휴식 1회당 +4~8 → 합산 범위로 표시
            self.lbl_pv_happy.setText(
                f"예상 행복도: +{rest_count*4}~{rest_count*8}")
        else:
            self.lbl_pv_happy.setText("예상 행복도: +0")

        # 콤보(훈련 선택)가 바뀌었으니 세이브에 반영.
        self._save_ui_state()

    # ── 모드 토글 ────────────────────────────────

    def _toggle_mode(self):
        """1주씩 보기 ↔ 4주씩 보기 전환.
        단, 1주씩 진행 도중(묶음 일부만 진행)에는 전환 불가."""
        if self._step_idx > 0:
            # 묶음 진행 중 → 전환 막고 버튼 상태 원위치
            self.btn_mode.setChecked(self._step_mode)
            show_toast(self, "⚠  진행 중인 4주를 끝낸 뒤 전환할 수 있습니다", "#cc6600", 1600)
            return

        self._step_mode = self.btn_mode.isChecked()
        if self._step_mode:
            self.btn_mode.setText("📆 1주씩")
            show_toast(self, "🔍 1주씩 보기  —  4주 일정대로 한 주씩 진행", "#664400", 1500)
        else:
            self.btn_mode.setText("📅 4주씩")
            self._locked_sched = None
            self._step_idx     = 0
            show_toast(self, "📅 4주씩 보기  —  한 달씩 진행", "#006622", 1400)
        self._save_ui_state()   # 모드 전환 결과를 세이브에 반영
        self.refresh()

    # ── 진행 ─────────────────────────────────────

    def _advance(self):
        p  = get_player()
        st = get_state()
        if not p or not st: return

        # [복수국적] 대표팀 선택이 대기 중이면 그것부터 처리 (진행 차단)
        #   ※ 22세 1~4주차 강제확정은 '새해 진입 직후'에 띄운다(아래 advance_4weeks 뒤).
        #     일정을 짜기 전에 먼저 국적을 정하도록 하기 위함.
        import intl_engine
        forced = intl_engine.get_forced_commit()
        if forced:
            self._show_forced_commit(forced)
            return
        pend = intl_engine.get_pending_choice()
        if pend:
            show_toast(self, "⚠  먼저 대표팀을 선택해야 합니다!", "#cc6600", 1600)
            self._show_nat_choice(pend)
            return

        week = st["current_week"]
        from constants import MIN_JOIN_AGE

        # 17살 이상인데 팀이 없고 비시즌(1~4주)이면 입단 강제.
        #   단, 올해 모든 오퍼가 결렬돼 '1년 훈련'을 택한 경우(_skip_join_lock)는
        #   이번 시즌 동안 입단을 강제하지 않고 그대로 진행시킨다.
        if (p["age"] >= MIN_JOIN_AGE and not p.get("current_team_id")
                and 1 <= week <= 4 and not getattr(self, "_skip_join_lock", False)):
            show_toast(self, "⚠  먼저 팀에 입단해야 합니다!", "#cc6600", 1500)
            return

        # ── 진행할 일정 결정 ──
        # 4주씩 모드: 현재 콤보 4개로 일정 만들어 한 번에 진행.
        # 1주씩 모드: 묶음 시작 시 4주 일정을 확정·고정하고,
        #            누를 때마다 그 중 한 주만 진행. 4주 다 지나면 자동 4주 복귀.
        def _build_4week_sched():
            sched = []
            for i in range(4):
                cb    = self.week_combos[i]
                w     = week + i
                sel   = cb.currentText()
                ttype = TRAIN_MAP_KO.get(sel, "중강도")
                mi = self._get_match(w, p)
                if mi:
                    sched.append((w, "경기", mi))
                else:
                    # 강점/약점훈련은 엔진이 스탯을 자동 선별하므로 detail 불필요.
                    sched.append((w, ttype, None))
            return sched

        if not self._step_mode:
            # 4주 한 번에
            schedule = _build_4week_sched()
        else:
            # 1주씩: 묶음 시작이면 4주 일정 확정·고정.
            #   _step_idx 가 0이 아니어도 _locked_sched 가 비어 있으면(예: 화면
            #   갱신 시 (week-1)%4 로 재계산됐는데 일정은 미생성) 새로 만든다.
            #   그리고 묶음의 시작 주(week - _step_idx)부터 만들어야 인덱스가 맞음.
            if self._locked_sched is None:
                bundle_start = week - self._step_idx
                sched = []
                for i in range(4):
                    cb    = self.week_combos[i]
                    w     = bundle_start + i
                    sel   = cb.currentText()
                    ttype = TRAIN_MAP_KO.get(sel, "중강도")
                    mi = self._get_match(w, p)
                    if mi:
                        sched.append((w, "경기", mi))
                    else:
                        sched.append((w, ttype, None))
                self._locked_sched = sched
            # 인덱스 안전 클램프 (혹시라도 범위를 벗어나면 마지막 주로)
            idx = max(0, min(self._step_idx, len(self._locked_sched) - 1))
            schedule = [self._locked_sched[idx]]

        advance_4weeks(schedule)

        # ── 묶음 진행 상태 갱신 ──
        if self._step_mode:
            self._step_idx += 1
            if self._step_idx >= 4:
                # 4주 묶음 완료 → 잠금 해제, 다음 4주 묶음으로
                self._step_idx     = 0
                self._locked_sched = None

        # 묶음(4주)이 완전히 끝났는가? (4주 모드는 항상 True)
        bundle_done = (not self._step_mode) or (self._step_idx == 0)

        # 진행으로 바뀐 묶음 위치/고정 일정을 세이브에 반영.
        #   (advance_4weeks 가 current_week 등을 이미 갱신한 뒤이므로 충돌 없음)
        self._save_ui_state()

        # 입단 플래그는 묶음 완료 시에만 초기화
        if bundle_done:
            self._join_used = False

        p2 = get_player(); st2 = get_state()
        new_week = st2["current_week"]
        from constants import MIN_JOIN_AGE

        # 구간 경계 진입 시 자동 오퍼 플래그 리셋
        from constants import OFFER_ZONES

        def _which_zone(w):
            for s, e in OFFER_ZONES:
                if s <= w <= e:
                    return (s, e)
            return None

        in_zone  = _which_zone(new_week)
        # zone 판정: 묶음 완료면 한 달 전, 진행 중이면 직전 주 기준
        prev_week = (new_week - 4) if bundle_done else (new_week - 1)
        if prev_week < 1:
            prev_week += 52
        prev_zone = _which_zone(prev_week)
        if in_zone and in_zone != prev_zone:
            self._auto_offer_shown = False

        # 소속 없으면 입단 안내
        if 1 <= new_week <= 4 and p2.get("age",0) >= MIN_JOIN_AGE and not p2.get("current_team_id"):
            # 새 시즌 1~4주 진입 → 작년 '전부 결렬→1년 훈련' 보류를 해제하고
            #   올해 다시 입단(오퍼)에 도전하게 한다.
            self._skip_join_lock = False
            self._join_used = False
            self.btn_join.setEnabled(True)
            show_toast(self, f"⭐ {st2['current_year']}년 새 시즌!  팀 입단 기간입니다", "#006622", 2000)

        # [복수국적] ★새해 진입 직후★ 22세 1~4주차 미고정이면 '일정 짜기 전에'
        #   국적부터 강제 확정. 52주에서 진행 버튼을 눌러 1주차로 막 넘어온 이 시점에
        #   띄워야 사용자가 1~4주 훈련을 선택하기 전에 대표팀을 정한다.
        import intl_engine
        forced = intl_engine.get_forced_commit()
        if forced:
            self._show_forced_commit(forced)
            if self.main_win:
                self.main_win.refresh_all()
            return   # 강제확정 후 이번 진행은 여기서 종료 (다시 진행 버튼을 누르면 일정 진행)

        # [복수국적] 두 나라 다 본선 진출 → 대표팀 선택 팝업 (선택 전까지 차출 보류)
        pend = intl_engine.get_pending_choice()
        if pend:
            self._show_nat_choice(pend)

        # 재계약 팝업은 '새 시즌 진입 직후 즉시' 떠야 한다.
        #   오퍼 플래그(_contract_renew_offer)는 연말(52주) 처리에서 세팅되므로,
        #   1주차로 막 넘어온 이 시점에 이미 존재한다. bundle_done(4주 묶음 완료)을
        #   기다리면 '1~4주 진행을 누른 뒤에야' 떠서 타이밍이 어긋난다.
        #   다이얼로그에서 수락/거절 시 플래그가 0으로 리셋되므로 반복 노출도 없다.
        if p2.get("_contract_renew_offer", 0) > 0:
            self._show_renew_dialog(p2)

        # 자동 오퍼 팝업은 4주 묶음이 완료됐을 때만
        # (1주씩 본다고 매주 오퍼가 뜨지 않음)
        if bundle_done:
            if p2.get("current_team_id") and in_zone:
                self._show_auto_offer(new_week)

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
        if not row:
            # 클럽 경기 없음 → 국가대표 경기 확인 (월드컵/대륙컵 윈도우)
            import intl_engine
            im = intl_engine.get_my_match(week)
            if im:
                return im
            # 그래도 없으면 → 클럽 챔피언스리그 경기 확인 (41~52주)
            import champions_engine
            return champions_engine.get_my_cl_match(week)

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
        """재계약 팝업: 팀이 정한 연봉·기간(1~3년)을 제시 → 수락/거절만."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QPushButton, QFrame)
        from game_engine import join_team, update_player, get_state

        offer_sal = p.get("_contract_renew_offer", 0)
        offer_yrs = p.get("_contract_renew_years", 0) or 2   # 안전 기본값
        from database import get_conn
        _conn = get_conn()
        _row  = _conn.execute("SELECT name FROM teams WHERE id=?", (p.get("current_team_id",0),)).fetchone()
        _conn.close()
        team_name = _row["name"] if _row else "현재 팀"

        dlg = QDialog(self)
        dlg.setWindowTitle("📋 재계약 제안")
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(_DIALOG_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18,16,18,16); lay.setSpacing(10)

        hdr = QLabel("📋 재계약 제안"); hdr.setObjectName("dlgHeader")
        lay.addWidget(hdr)

        card = QFrame(); card.setObjectName("dlgCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(14,12,14,12); cl.setSpacing(6)
        cl.addWidget(QLabel(f"<b style='color:#fff'>{team_name}</b><span style='color:#bbb'>에서 재계약을 제안합니다.</span>"))
        cl.addWidget(QLabel(f"<span style='color:#bbb'>제시 조건</span>  "
                            f"<b style='color:#ffcc33'>{offer_yrs}년 계약</b>"))
        cl.addWidget(QLabel(f"<span style='color:#bbb'>제시 연봉</span>  "
                            f"<b style='color:#00cc66'>{fmt_money(offer_sal)} / 년</b>"))
        lay.addWidget(card)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        btn_accept = QPushButton(f"✅ {offer_yrs}년 계약 수락"); btn_accept.setObjectName("dlgOk")
        btn_reject = QPushButton("❌ 거절 (소속 없음)"); btn_reject.setObjectName("dlgNo")
        btn_row.addWidget(btn_accept, 1); btn_row.addWidget(btn_reject, 1)
        lay.addLayout(btn_row)

        def _accept():
            yrs = offer_yrs   # 팀이 정한 기간 (선택 불가)
            st  = get_state()
            # 만료 연도 = 입단 로직과 동일 규칙.
            #  - 재계약 팝업은 만료 다음 해 1~4주에 뜨므로 올해가 1년차 → -1 보정
            #  - 드물게 시즌 중(5주~) 수락이면 올해 미포함 → 보정 없음
            cur_y = st["current_year"]; cur_w = st["current_week"]
            end = (cur_y + yrs - 1) if cur_w <= 4 else (cur_y + yrs)
            from game_engine import update_player as upd
            upd(contract_years=yrs, contract_end_year=end,
                salary=offer_sal, _contract_renew_offer=0, _contract_renew_years=0)
            from game_engine import mark_contract_extension
            mark_contract_extension(yrs)
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

    def _show_forced_commit(self, forced):
        """[복수국적] 22세 1~4주차 — 평생 뛸 대표팀 국적을 강제로 확정.
        본선 진출 여부와 무관하게 보유 국적 전부 중에서 선택.
        선택해도 보유 국적은 사라지지 않고, '대표로 뛰는 국적'만 정해진다.
        닫기·취소 불가 — 반드시 하나를 골라야 진행된다."""
        if getattr(self, "_forced_commit_open", False):
            return
        self._forced_commit_open = True
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel,
                                      QPushButton, QFrame)
        import intl_engine

        opts = forced.get("options", [])

        dlg = QDialog(self)
        dlg.setWindowTitle("🌍 국가대표 국적 확정")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(_DIALOG_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16); lay.setSpacing(10)

        hdr = QLabel("🌍 국가대표 국적 확정 (만 22세)"); hdr.setObjectName("dlgHeader")
        lay.addWidget(hdr)

        nat_list = " / ".join(f"{o.get('flag','')}{o['nat']}" for o in opts)
        info = QLabel(
            f"<span style='color:#ddd; font-size:14px'>"
            f"만 22세가 되어 <b style='color:#ffcc66'>평생 뛸 국가대표 국적</b>을 "
            f"확정할 때입니다.<br><br>"
            f"보유 국적: <b style='color:#ffcc66'>{nat_list}</b><br><br>"
            f"이 중 어느 나라 대표로 뛸지 고르세요. "
            f"(본선 진출과 무관하게 선택 가능)<br>"
            f"<b style='color:#ff8866'>한 번 정하면 평생 그 나라 대표로만</b> 뛰게 됩니다.<br>"
            f"<span style='color:#88cc88'>※ 선택해도 보유 국적은 사라지지 않습니다.</span></span>")
        info.setWordWrap(True)
        card = QFrame(); card.setObjectName("dlgCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(14, 12, 14, 12)
        cl.addWidget(info)
        lay.addWidget(card)

        def _do_commit(nat):
            intl_engine.commit_nationality(nat)
            dlg.accept()
            show_toast(self, f"🌍 {nat} 대표로 국적을 확정했습니다!", "#1a4d8f", 2000)
            if self.main_win: self.main_win.refresh_all()

        # 국적 수만큼 버튼을 세로로 쌓아 글자 잘림/창 크기 문제 방지
        for opt in opts:
            b = QPushButton(f"✅ {opt.get('flag','')} {opt['nat']} 대표로 뛰겠습니다")
            b.setObjectName("dlgChoice")
            b.clicked.connect(lambda _=False, n=opt["nat"]: _do_commit(n))
            lay.addWidget(b)

        # 닫기·취소 불가 (반드시 선택)
        dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        dlg.exec()
        self._forced_commit_open = False

    def _show_nat_choice(self, pend):
        """[복수국적] 본선 진출국 대표팀 발탁 제안 팝업.
        - 진출국 1개: 그 나라로 뛸지 예/아니오 확인
        - 진출국 2~3개: 어느 나라로 뛸지 선택 (+ 이번엔 거절)
        선택해서 '예/국가'를 누르면 그 나라로 영구 고정(A매치 출전 = cap-tie)된다.
        거절하면 이번 대회만 출전하지 않고, 다음 대회에서 다시 제안된다."""
        # 이미 팝업이 떠 있으면 중복 생성 방지 (refresh가 여러 번 불려도 1개만)
        if getattr(self, "_nat_choice_open", False):
            return
        self._nat_choice_open = True
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QPushButton, QFrame)
        import intl_engine

        opts = pend.get("options", [])
        single = (len(opts) == 1)

        dlg = QDialog(self)
        dlg.setWindowTitle("🌍 대표팀 발탁")
        dlg.setMinimumWidth(400)
        dlg.setStyleSheet(_DIALOG_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18,16,18,16); lay.setSpacing(10)

        hdr = QLabel(f"🌍 {pend['name']} 대표팀 발탁"); hdr.setObjectName("dlgHeader")
        lay.addWidget(hdr)

        if single:
            opt = opts[0]
            info = QLabel(
                f"<span style='color:#ddd; font-size:14px'>"
                f"<b style='color:#ffcc66; font-size:16px'>{opt.get('flag','')} {opt['nat']}</b> "
                f"대표팀에서 발탁을 제안합니다.<br><br>"
                f"이 나라로 국가대표 경기를 뛰겠습니까?<br>"
                f"<b style='color:#ff8866'>한 번 출전하면 그 나라로 영구 고정</b>되어<br>"
                f"다른 나라 대표로는 뛸 수 없습니다.<br>"
                f"<span style='color:#aaa'>선택하면 선발·예선 결과가 공개됩니다.</span>"
                f"<br><span style='color:#88cc88'>※ 보유 국적 자체는 사라지지 않습니다.</span></span>")
        else:
            nat_list = " / ".join(f"{o.get('flag','')}{o['nat']}" for o in opts)
            info = QLabel(
                f"<span style='color:#ddd; font-size:14px'>"
                f"여러 나라가 당신을 대표로 원합니다.<br>"
                f"<b style='color:#ffcc66'>{nat_list}</b><br><br>"
                f"어느 대표팀으로 뛸지 선택하세요.<br>"
                f"<b style='color:#ff8866'>한 번 출전하면 그 나라로 영구 고정</b>됩니다.<br>"
                f"<span style='color:#aaa'>선택하면 선발·예선 결과가 공개됩니다.</span>"
                f"<br><span style='color:#88cc88'>※ 보유 국적 자체는 사라지지 않습니다.</span></span>")
        info.setWordWrap(True)
        card = QFrame(); card.setObjectName("dlgCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(14,12,14,12)
        cl.addWidget(info)
        lay.addWidget(card)

        def _do_choice(opt):
            # [복수대륙컵] 선택한 옵션의 대회로 출전. 옵션에 tournament_id가
            #   있으면 그것을(각 대륙컵), 없으면 pend 대표 tid를 사용(구버전 호환).
            tid = opt.get("tournament_id", pend["tournament_id"])
            res = intl_engine.choose_national_team(tid, opt["nat"])
            dlg.accept()
            if res:
                self._show_callup_result(opt["nat"], res)
            if self.main_win: self.main_win.refresh_all()

        def _do_decline():
            intl_engine.decline_national_team(pend["tournament_id"])
            dlg.accept()
            _nat_str = "/".join(o["nat"] for o in opts)
            show_toast(self, f"🚫 {_nat_str} 발탁을 거절했습니다 (기록에 남음)",
                       "#aa6633", 2000)
            if self.main_win: self.main_win.refresh_all()

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if single:
            opt = opts[0]
            b_yes = QPushButton(f"✅ 예, {opt['nat']}로 뛰겠습니다")
            b_yes.setObjectName("dlgChoice")
            b_yes.clicked.connect(lambda _=False, o=opt: _do_choice(o))
            b_no = QPushButton("❌ 아니오 (보류)")
            b_no.setObjectName("dlgNo")
            b_no.clicked.connect(lambda _=False: _do_decline())
            btn_row.addWidget(b_yes, 2); btn_row.addWidget(b_no, 1)
            lay.addLayout(btn_row)
        else:
            for opt in opts:
                # 복수 대륙컵이면 '국적 (대회명)'으로 어느 대회인지 명시
                _comp = opt.get("competition", "")
                _label = f"{opt.get('flag','')} {opt['nat']}"
                if _comp:
                    _label += f"\n({_comp})"
                b = QPushButton(_label)
                b.setObjectName("dlgChoice")
                b.clicked.connect(lambda _=False, o=opt: _do_choice(o))
                btn_row.addWidget(b, 1)
            lay.addLayout(btn_row)
            b_no = QPushButton("이번엔 어느 나라로도 뛰지 않음")
            b_no.setObjectName("dlgNo")
            b_no.clicked.connect(lambda _=False: _do_decline())
            lay.addWidget(b_no)

        # 선택을 강제 (닫기 버튼 비활성 — 예/아니오/거절 중 하나는 눌러야 함)
        dlg.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        dlg.exec()
        self._nat_choice_open = False

    def _show_callup_result(self, nat, res):
        """[복수국적] 대표 선택 직후 결과를 '순서대로' 공개하는 다이얼로그.
        순서:  ① 발탁(선택 확정)  →  ② 국가대표 선발 여부  →  ③ 예선 통과/본선 진출.
        선택한 뒤에야 선발·예선 결과가 단계적으로 드러난다."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QFrame

        _rs = res.get("result", "")
        _kind = res.get("kind", "")
        _is_qual = (_kind == "wc_qual")
        # 각 단계 라인 구성 (결과에 따라 ②③ 색/내용 분기)
        line1 = f"✅ <b style='color:#ffcc66'>{nat}</b> 대표로 확정했습니다."
        if _rs == "미선발":
            line2 = f"📋 <span style='color:#ff8866'>국가대표 미선발</span> — 이번엔 부름을 받지 못했습니다."
            line3 = ""
        elif _rs == "예선탈락":
            line2 = f"📣 <span style='color:#88cc88'>국가대표 선발!</span>"
            line3 = f"📋 <span style='color:#ff8866'>…하지만 {nat}은(는) 예선 탈락</span> — 이번 대회 출전 없음."
        elif _rs == "선발":
            line2 = f"📣 <span style='color:#88cc88'>국가대표 선발!</span>"
            if _is_qual:
                line3 = f"🌏 <span style='color:#66ccff'>{nat} 예선 소집!</span> — 예선 조별리그에 출전합니다."
            else:
                line3 = f"🌍 <span style='color:#66ccff'>{nat} 본선 진출!</span> — 조별리그에 소집됩니다."
        else:
            line2 = ""; line3 = ""

        dlg = QDialog(self)
        dlg.setWindowTitle("🌍 대표팀 발탁 결과")
        dlg.setMinimumWidth(400)
        dlg.setStyleSheet(_DIALOG_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16); lay.setSpacing(10)

        hdr = QLabel("🌍 대표팀 발탁 결과"); hdr.setObjectName("dlgHeader")
        lay.addWidget(hdr)

        body = "<br><br>".join(x for x in (line1, line2, line3) if x)
        info = QLabel(f"<span style='color:#ddd; font-size:14px'>{body}</span>")
        info.setWordWrap(True)
        card = QFrame(); card.setObjectName("dlgCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(14, 12, 14, 12)
        cl.addWidget(info)
        lay.addWidget(card)

        btn = QPushButton("확인"); btn.setObjectName("dlgChoice")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
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
        # 모달로 띄워 다이얼로그가 열려 있는 동안 진행(next day)을 차단.
        # 비모달(show)이면 오퍼창을 띄운 채 시간을 더 진행시킨 뒤 수락할 수 있어
        # join_team이 엉뚱한 주차/시즌 기준으로 실행되는 정합성 버그가 생긴다.
        dlg.exec()
        # [전부 결렬 → 1년 훈련] 모든 오퍼가 결렬되어 입단할 곳이 없으면,
        #   이번 시즌은 입단 강제를 풀고 그대로 훈련하며 보낸다. (다음 해 재도전)
        if not dlg.chosen and getattr(dlg, "all_failed", False):
            self._skip_join_lock = True
            self._join_used = True
            self.btn_join.setEnabled(False)
            show_toast(self, "📅 모든 협상 결렬 — 올해는 입단을 보류하고 1년 더 훈련합니다.",
                       "#cc6600", 2200)
            if self.main_win: self.main_win.refresh_all()
            return
        self._on_join_done(dlg)

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
        # 모달(exec)로 띄워 오퍼창이 열려 있는 동안 next day 진행을 차단.
        dlg.exec()
        self._on_auto_offer_done(dlg)

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
        # 창이 닫히면 핸들을 비워 둔다(진행 시 refresh_all 이 죽은 위젯을
        # 건드리지 않도록). 다시 열면 새로 만든다.
        def _clear_standings(*_a):
            self._standings_win = None
        self._standings_win.finished.connect(_clear_standings)
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
        # 창이 닫히면 핸들을 비워 둔다(진행 시 refresh_all 이 죽은 위젯을
        # 건드리지 않도록). 다시 열면 _do_schedule 이 새로 만든다.
        def _clear_handle(*_a):
            self._schedule_win = None
        self._schedule_win.finished.connect(_clear_handle)
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
        # 은퇴 가능 구간: 1~4주(새 시즌 직후·연장 거절 타이밍) 또는 37~52주(리그 종료 후).
        if not ((1 <= week <= 4) or (37 <= week <= 52)):
            show_toast(self, "⚠  은퇴는 시즌 종료 후(37주차~) 또는 새 시즌 1~4주차에 가능합니다", "#cc6600", 1900)
            return

        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
        p = get_player() or {}
        nm  = p.get("name", "선수")
        age = p.get("age", "")

        dlg = QDialog(self)
        dlg.setWindowTitle("은퇴 확인")
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(_DIALOG_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18,16,18,16); lay.setSpacing(12)

        hdr = QLabel("🏁 은퇴")
        hdr.setStyleSheet("color:#e0a020; font-size:16px; font-weight:bold;")
        lay.addWidget(hdr)

        card = QFrame(); card.setObjectName("dlgCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(14,14,14,14); cl.setSpacing(6)
        cl.addWidget(QLabel(
            f"<span style='color:#ddd; font-size:14px'>"
            f"<b style='color:#fff'>{nm}</b>{(' ('+str(age)+'세)') if age else ''} 선수의<br>"
            f"선수 생활을 여기서 마칠까요?</span>"))
        warn = QLabel("⚠ 은퇴하면 되돌릴 수 없으며, 커리어가 마감됩니다.")
        warn.setStyleSheet("color:#cc7766; font-size:12px;")
        warn.setWordWrap(True)
        cl.addWidget(warn)
        lay.addWidget(card)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        b_no  = QPushButton("계속 선수 생활")
        b_no.setObjectName("dlgOk")          # 안전한 선택을 초록(기본)으로
        b_yes = QPushButton("🏁 은퇴하기")
        b_yes.setObjectName("dlgNo")          # 되돌릴 수 없는 선택을 빨강으로
        b_no.clicked.connect(dlg.reject)
        b_yes.clicked.connect(dlg.accept)
        btn_row.addWidget(b_no, 1); btn_row.addWidget(b_yes, 1)
        lay.addLayout(btn_row)

        b_no.setDefault(True)   # 엔터 시 기본은 '계속'

        if dlg.exec() == QDialog.DialogCode.Accepted:
            # 리그가 끝난 시즌의 우승·개인수상을 trophy_log/awards에 먼저 확정한 뒤
            #   은퇴 창을 띄운다. (시즌전환 부작용 없이 성과만 기록)
            from game_engine import finalize_season_for_retire
            finalize_season_for_retire()
            from ui.retire_window import RetireWindow
            main_win = self.window()
            self._retire_win = RetireWindow(get_player().get("language", "ko"), main_win)
            self._retire_win.show()


# ── 헬퍼 ──────────────────────────────────────────────────────

def _half(week, lang):
    from constants import SEASON_PHASES
    fs, fe = SEASON_PHASES["first_half"]; ss, se = SEASON_PHASES["second_half"]
    if fs<=week<=fe: return "🏆 상반기"  if lang=="ko" else "🏆 First Half"
    if ss<=week<=se: return "🏆 하반기"  if lang=="ko" else "🏆 Second Half"
    return "☀ 비시즌" if lang=="ko" else "☀ Off-Season"

def _phase_short(week, lang):
    from constants import SEASON_PHASES
    fs, fe = SEASON_PHASES["first_half"]; ss, se = SEASON_PHASES["second_half"]
    if 1<=week<=4:   return "비시즌" if lang=="ko" else "Pre"
    if fs<=week<=fe: return "상반기" if lang=="ko" else "1st"
    if ss<=week<=se: return "하반기" if lang=="ko" else "2nd"
    return "비시즌" if lang=="ko" else "Off"