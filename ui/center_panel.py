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
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QThread, pyqtSignal
from PyQt6.QtGui import QColor

from game_engine import (
    get_player, get_state, set_state, advance_4weeks, advance_days,
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


class _ProcessingOverlay(QWidget):
    """[2026-07 추가] 진행 버튼 클릭 시 무거운 처리(advance_days, 특히
    52→1주 시즌전환)가 도는 동안 화면 전체를 덮는 반투명 오버레이.

    기존엔 main_win.setEnabled(False) + WaitCursor(커서만 모래시계로 바뀜)뿐이라,
    시즌 전환처럼 몇 초 걸리는 처리 중엔 사용자 눈엔 그냥 '앱이 멈춘 것'과
    구분이 안 갔다(마우스를 안 움직이면 커서 모양 변화조차 못 봄). 실제 처리
    시간 자체를 줄이는 것과 별개로, "지금 뭘 하고 있는지"를 화면에 명시해서
    같은 대기시간이라도 고장으로 오인하지 않게 한다.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("background: rgba(10,10,10,0.72);")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel("⏳ 처리 중...")
        self._label.setStyleSheet("""
            color:white; font-size:16px; font-weight:bold;
            background: rgba(30,30,30,0.9); border:1px solid #555;
            border-radius:10px; padding:18px 28px;
        """)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._label)
        self.hide()

    def show_message(self, text):
        self._label.setText(text)
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.raise_()
        self.show()
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

TRAIN_OPTS_KO = ["고강도","중강도","강점훈련","약점훈련","저강도","휴식"]
TRAIN_MAP_KO  = {"고강도":"고강도","중강도":"중강도",
                  "강점훈련":"강점훈련","약점훈련":"약점훈련","저강도":"저강도","휴식":"휴식"}
# [2026-07 변경, 신민용 요청] 기존엔 월~금 중강도 위주 + 토 저강도 + 일 휴식
#   이었는데, 격일로 고강도 훈련 후 하루 쉬는 패턴(월 고강도-화 휴식-수 고강도-
#   목 휴식-금 고강도-토 휴식-일 휴식)으로 기본값을 변경. 실전처럼 하루
#   빡세게 훈련하고 다음날 회복하는 루틴을 기본으로 삼되, 사용자가 각 요일
#   콤보박스에서 언제든 자유롭게 바꿀 수 있는 건 그대로다(이건 어디까지나
#   초기 기본값일 뿐).
TRAIN_DEFAULTS = ["고강도","휴식","고강도","휴식","고강도","휴식","휴식"]
# [일 단위 전환] 진행 묶음 크기 = 7일(1주). 기존엔 4주(월 단위) 묶음이었다.
DAY_BUNDLE_SIZE = 7

CENTER_STYLE = """
QWidget { background-color: #1e1e1e; color: #cccccc; font-size: 12px; }
#phaseLabel { color: #00cc44; font-size: 14px; font-weight: bold; }
#noMatch    { color: #666666; font-size: 12px; }
#weekFrame  { background-color: #252525; border:1px solid #333; border-radius:6px; }
#weekFrame[weekend="true"] { background-color: #3a1a1a; border:1px solid #7a3030; border-radius:6px; }
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


class _AdvanceWorker(QThread):
    """일자/시즌 진행(advance_days)을 백그라운드 스레드에서 처리.

    52→1주 시즌전환 시 _end_of_season → run_ai_offseason(AI 생애주기,
    수만 명 규모) 등 무거운 DB 작업이 한꺼번에 일어나는데, 이걸 메인(UI)
    스레드에서 그대로 부르면 그 시간만큼 화면이 완전히 멈춘다(체감 렉).
    실제 계산 시간 자체는 줄이지 않지만, 별도 스레드에서 돌려 이벤트 루프가
    막히지 않게 하면 사용자 입장에서 "멈춤"은 사라진다.

    [스레드 안전] database.py의 풀 커넥션은 check_same_thread=False로 열려
    있어 이 워커 스레드에서도 그대로 재사용 가능하다. 단, SQLite 커넥션을
    여러 스레드가 '동시에' 건드리는 건 안전하지 않으므로, 워커가 도는 동안
    메인 스레드가 DB에 접근하지 않도록 UI 쪽(CenterPanel._advance)에서
    main_win 전체를 비활성화해 직렬화를 보장한다."""
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, schedule, parent=None):
        super().__init__(parent)
        self._schedule = schedule

    def run(self):
        try:
            advance_days(self._schedule)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.failed.emit(str(e))
            return
        self.finished_ok.emit()


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
        # _locked_sched : 하루씩 진행 시작 시 고정한 1주(7일) 일정 (7개)
        # _step_idx : 현재 묶음에서 진행한 주 수 (0~3). 0이면 묶음 시작 전.
        self._step_mode    = False
        self._locked_sched = None
        self._step_idx     = 0
        self._restoring    = False   # 복원 중 콤보 시그널이 저장을 되부르는 것 방지
        # [2026-07 추가] 경기 전날 강제휴식이 원래 선택을 덮어쓴 뒤, 그 경기가
        # 없어졌을 때(일정 재생성 등) 원래 선택으로 되돌리기 위한 저장소.
        # {day(정수, 연중 일자): 사용자가 마지막으로 '직접' 고른 문자열}
        self._day_prefs    = {}
        self._proc_overlay = None   # 진행 중 오버레이(지연 생성)
        self._build()
        # 세이브에 저장된 메인 화면 상태(모드/묶음/콤보)를 복원한다.
        self._restore_ui_state()

    # ── 빌드 ─────────────────────────────────────

    def _build(self):
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12,12,12,12)
        self.lay.setSpacing(8)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 페이즈 라벨 + [2026-07 신설] 우측에 다음 주 미리보기(작은 박스 7개).
        #   신민용 요청: "중앙 화면 우측에 다음주 일정이 간단하게 표시되는건
        #   어떨까? 네모 7개, 대회 종류에 따라 색이 다르게" — 리그=초록,
        #   컵=보라, 챔스=황금, 국대(월드컵/대륙컵)=주황, 국대(그 외)=빨강,
        #   경기 없는 날=회색. 4번 색상 규칙과 동일하게 맞춘다.
        phase_row = QHBoxLayout(); phase_row.setSpacing(8)
        self.lbl_phase = QLabel("비시즌  |  1990년 1시즌  1일차")
        self.lbl_phase.setObjectName("phaseLabel")
        phase_row.addWidget(self.lbl_phase, 1)

        self.nwp_boxes: list[QLabel] = []
        nwp_row = QHBoxLayout(); nwp_row.setSpacing(3)
        for _ in range(DAY_BUNDLE_SIZE):
            b = QLabel("")
            b.setFixedSize(14, 14)
            b.setStyleSheet("background:#333;border-radius:3px;")
            self.nwp_boxes.append(b)
            nwp_row.addWidget(b)
        nwp_wrap = QWidget(); nwp_wrap.setLayout(nwp_row)
        nwp_wrap.setToolTip("다음 주 일정 미리보기 (초록=리그, 보라=컵, 황금=챔스, "
                            "주황=월드컵/대륙컵, 빨강=국대, 회색=경기 없음)")
        phase_row.addWidget(nwp_wrap, 0)
        self.lay.addLayout(phase_row)

        self.lbl_no_match = QLabel("이번 주 경기 없음")
        self.lbl_no_match.setObjectName("noMatch")
        self.lay.addWidget(self.lbl_no_match)

        # [일 단위 전환] 1주(7일) 스케줄 — 하루에 콤보박스 1개(그날의 훈련/휴식,
        #   경기 있는 날은 자동으로 "⚽ 경기" 표시로 대체).
        sched_row = QHBoxLayout(); sched_row.setSpacing(6)
        self.week_combos : list[QComboBox] = []   # 이제 '주'가 아니라 '일' 7개를 담음
        self.week_hints  : list[QLabel]    = []
        self.week_frames : list[QFrame]    = []

        day_labels_kr = ["월", "화", "수", "목", "금", "토", "일"]
        for i in range(DAY_BUNDLE_SIZE):
            f = QFrame(); f.setObjectName("weekFrame")
            is_weekend = i >= 5   # 토(5), 일(6)
            # [2026-07 수정] 예전엔 주말 콤보박스 자체를 빨갛게 칠했는데,
            # 원하는 모습은 "선택창(콤보박스)은 평일과 똑같은 회색이고, 그
            # 바깥 박스(프레임) 테두리만 빨간색"이었다. 그래서 색을
            # 콤보박스가 아니라 f(QFrame)에 dynamic property로 표시하고,
            # #weekFrame[weekend="true"] 스타일시트 규칙이 그걸 읽어서
            # 배경/테두리만 빨갛게 바꾼다 — 진행 중인 날(글로우 효과) 표시와
            # 겹쳐도 _set_glow()가 setStyleSheet("")로 되돌릴 때 이 규칙이
            # 그대로 다시 적용되므로 서로 안 부딪힌다.
            f.setProperty("weekend", True if is_weekend else False)
            fl = QVBoxLayout(f); fl.setContentsMargins(6,8,6,8); fl.setSpacing(4)

            wl = QLabel(day_labels_kr[i]); wl.setObjectName("weekTitle")
            wl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            cb = QComboBox(); cb.addItems(TRAIN_OPTS_KO)
            cb.setCurrentText(TRAIN_DEFAULTS[i])
            cb.currentTextChanged.connect(self._update_preview)
            # [2026-07 추가] 경기 전날 강제 휴식(_get_match_for_day 로직)이
            # 콤보를 "휴식"으로 덮어쓰는데, 이걸 currentText로만 관리하면
            # 나중에 그 경기가 사라졌을 때(일정 재생성 등) 원래 사용자가
            # 골라뒀던 훈련으로 못 돌아가고 "휴식"에 그대로 눌러앉는 버그가
            # 있었다. cb.isEnabled()가 False일 때(=강제 잠금 중일 때)는
            # 이 시그널이 내가 프로그램적으로 setCurrentText한 것이지 실제
            # 사용자 입력이 아니므로 저장하지 않는다 — 그래서 항상
            # setEnabled(False)를 setCurrentText보다 먼저 호출해야 한다
            # (아래 refresh()의 강제 잠금 코드도 그 순서를 지킨다).
            cb.currentTextChanged.connect(
                lambda text, idx=i: self._on_day_combo_changed(idx, text))

            # 경기 있을 때 대체 표시용 라벨
            ml = QLabel("⚽ 경기"); ml.setObjectName("matchLabel")
            ml.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ml.setStyleSheet("color:#ffcc00;font-weight:bold;font-size:12px;"
                             "background:#1a3a1a;border-radius:4px;padding:4px;")
            ml.hide()

            hl = QLabel(""); hl.setObjectName("stressHint")
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            fl.addWidget(wl); fl.addWidget(cb); fl.addWidget(ml); fl.addWidget(hl)

            # "진행할 날" 강조용 형광 발광 효과 (평소엔 꺼둠)
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
        self.adv_btn = QPushButton("▶▶  이번 주 진행")
        self.adv_btn.setObjectName("advBtn")
        self.adv_btn.clicked.connect(self._advance)
        adv_row.addWidget(self.adv_btn, 1)

        # 하루씩/1주씩 모드 토글
        self.btn_mode = QPushButton("📅 1주씩")
        self.btn_mode.setObjectName("modeBtn")
        self.btn_mode.setCheckable(True)
        self.btn_mode.setToolTip("클릭하면 하루씩 보기 / 1주씩 보기 전환")
        self.btn_mode.clicked.connect(self._toggle_mode)
        adv_row.addWidget(self.btn_mode)
        self.lay.addLayout(adv_row)

        # 예상 변화 박스
        pvbox = QFrame(); pvbox.setObjectName("previewBox")
        pvlay = QVBoxLayout(pvbox); pvlay.setContentsMargins(12,8,12,8)
        pvlay.addWidget(QLabel("이번 주 예상 변화"))
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
        self.btn_offer_toggle = QPushButton("🔔 오퍼 ON"); self.btn_offer_toggle.setObjectName("actBtn")
        self.btn_retire = QPushButton("🚪 은퇴");     self.btn_retire.setObjectName("actBtn")
        self.btn_world  = QPushButton("🌍 세계 기록실"); self.btn_world.setObjectName("actBtn")
        for b in [self.btn_agent, self.btn_offer_toggle, self.btn_retire, self.btn_world]:
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
        self.btn_offer_toggle.clicked.connect(self._do_toggle_offers)
        self.btn_retire.clicked.connect(self._do_retire)
        self.btn_world.clicked.connect(self._do_world_browser)

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
                    slim = json.loads(locked_raw)   # [[day, ttype], ...] 7개
                    if isinstance(slim, list) and len(slim) == DAY_BUNDLE_SIZE:
                        from game_engine import get_player
                        p = get_player() or {}
                        rebuilt = []
                        for d, ttype in slim:
                            mi = (self._get_match_for_day(d, p)
                                  if p.get("current_team_id") else None)
                            if mi:
                                rebuilt.append((d, "경기", mi))
                            else:
                                rebuilt.append((d, ttype, None))
                        self._locked_sched = rebuilt
                        idx = int(st.get("step_idx", 0))
                        self._step_idx = max(0, min(idx, DAY_BUNDLE_SIZE - 1))
                    else:
                        self._locked_sched = None
                        self._step_idx     = 0
                except Exception:
                    self._locked_sched = None
                    self._step_idx     = 0
            else:
                # 1주 모드이거나 진행 중 묶음이 없음 → 깨끗한 시작 상태
                self._locked_sched = None
                self._step_idx     = 0

            # 토글 버튼 라벨/체크 상태를 복원된 모드에 맞춘다.
            try:
                self.btn_mode.setChecked(self._step_mode)
                self.btn_mode.setText("📆 하루씩" if self._step_mode else "📅 1주씩")
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

        from constants import day_to_week, DAYS_PER_WEEK, day_to_date_str, day_to_full_date_str
        year   = st["current_year"]
        week   = st["current_week"]
        season = st["current_season"]
        lang   = p.get("language","ko")
        day    = st.get("current_day") or ((week - 1) * DAYS_PER_WEEK + 1)

        # 하루씩 모드인데 고정된 묶음 일정이 없으면 '묶음 시작 전' 상태다.
        #   진행 상태는 _save/_restore_ui_state 로 정확히 영속화하므로,
        #   일정이 없으면 추측하지 말고 깨끗한 시작(idx=0)으로 둔다.
        if self._step_mode and self._locked_sched is None:
            self._step_idx = 0

        # 표시 기준 묶음 시작 일자 = 현재일 - 진행한 일수
        bundle_start = day - self._step_idx if self._step_mode else day

        phase = _half(day_to_week(bundle_start), lang)

        if self._step_mode:
            done = self._step_idx
            self.lbl_phase.setText(
                f"{phase}  |  {season}시즌  "
                f"{day_to_full_date_str(year, bundle_start)} ({day_to_week(bundle_start)}주차)  (하루씩 {done}/{DAY_BUNDLE_SIZE})")
            self.adv_btn.setText(f"▶  하루 진행  ({day_to_full_date_str(year, day)}, {done+1}/{DAY_BUNDLE_SIZE}일차)")
        else:
            self.lbl_phase.setText(
                f"{phase}  |  {season}시즌  "
                f"{day_to_full_date_str(year, day)} ({week}주차)")
            self.adv_btn.setText("▶▶  이번 주 진행")

        # 일자별 표시 (프레임 7칸은 항상 [묶음 시작 ~ +6])
        # [2026-07 버그수정, 신민용 리포트: "16세에 국대 발탁됐는데 일정
        # 화면엔 안 뜨고 로그에만 결과가 찍힘"] 예전엔 아래 _has_team이
        # False(소속 클럽 없음 — 어린 나이라 아직 입단 전인 경우 등)이면
        # _get_match_for_day() 호출 자체를 건너뛰고 무조건 None으로
        # 취급했다. 그런데 국가대표 경기는 클럽 소속과 무관하게 열릴 수
        # 있다(_get_match_for_day 내부는 이미 tid=0이어도 국제전/챔스/
        # 컵대회를 정상적으로 조회한다) — 그래서 실제로는 경기가 잡혀서
        # 진행까지 됐는데(로그엔 남음) 화면 미리보기에서만 빠져 보였다.
        # 이제 소속팀 유무와 무관하게 항상 조회한다.
        if self._step_mode and self._locked_sched is not None and len(self._locked_sched) == DAY_BUNDLE_SIZE:
            # [2026-07 버그수정] 하루씩 모드에서 미래 요일(아직 진행 안 한 날)의
            # 경기 정보가 화면에서 사라지던 문제. 원인: 하루씩 모드는 묶음 시작
            # 시점에 self._locked_sched(그 주 7일 확정 일정)를 이미 만들어두고
            # 그걸로 실제 진행을 하는데, 화면 표시는 이 확정본을 안 쓰고 매
            # 새로고침마다 _get_match_for_day()를 다시 호출해 재조회했다. 그런데
            # 하루씩 진행 중 앞선 날짜가 처리되면서(경기 결과 기록, AI 스캔 등)
            # DB 상태가 바뀌고, 그 여파로 재조회 결과가 확정 당시와 달라질 수
            # 있어(예: home_score 갱신 타이밍) 아직 오지 않은 날의 경기 라벨이
            # 통째로 빠져 보였다. 이제 하루씩 모드에서 묶음이 이미 고정된
            # 상태라면 그 확정본(self._locked_sched)을 그대로 화면에 반영해
            # "실제로 진행될 내용 = 화면에 보이는 내용"을 항상 일치시킨다.
            _match_cache = {
                item[0]: (item[2] if item[1] == "경기" else None)
                for item in self._locked_sched
            }
        else:
            _match_cache = {
                bundle_start + i: self._get_match_for_day(bundle_start + i, p)
                for i in range(DAY_BUNDLE_SIZE)
            }

        self._update_next_week_preview(bundle_start, p)

        day_labels_kr = ["월", "화", "수", "목", "금", "토", "일"]
        for i, (f, cb) in enumerate(zip(self.week_frames, self.week_combos)):
            d  = bundle_start + i
            w_of_d = day_to_week(d)
            ph = _phase_short(w_of_d, lang)
            dow = day_labels_kr[(d - 1) % DAYS_PER_WEEK]
            date_str = day_to_date_str(d)
            labels = f.findChildren(QLabel)
            # labels[0]=요일타이틀, labels[1]=matchLabel, labels[2]=stressHint

            if self._step_mode:
                # 하루씩: 콤보 잠금(묶음 일정 고정), 진행 상태 표시
                cb.setEnabled(False)
                if i < self._step_idx:
                    tag = "✓ 완료"; f.setEnabled(False)
                    self._set_glow(f, False)
                elif i == self._step_idx:
                    tag = "▶ 진행할 날"; f.setEnabled(True)
                    self._set_glow(f, True)      # 진행할 날만 형광 발광
                else:
                    tag = "대기"; f.setEnabled(False)
                    self._set_glow(f, False)
                if labels: labels[0].setText(f"{dow} {date_str}  {tag}")
            else:
                cb.setEnabled(True)
                f.setEnabled(True)
                self._set_glow(f, False)         # 1주 모드: 강조 없음
                if labels: labels[0].setText(f"{dow} {date_str}")

            match_info = _match_cache.get(d)
            # matchLabel, stressHint 찾기
            ml = next((l for l in labels if l.objectName()=="matchLabel"), None)
            hl = self.week_hints[i]

            # [2026-07 추가] 부상 중이면 그 부상이 남아있는 날짜만큼은 무슨
            # 요일이든(경기 예정일이었어도) 훈련 선택 콤보 대신 "🚑 부상"을
            # 보여준다 — 실제 진행 로직(advance_days)도 부상 중엔 그날
            # 예정이 뭐였든 무시하고 부상 휴식으로 처리하므로, 화면도 그와
            # 똑같이 보여줘야 "왜 훈련이 안 먹히지" 하는 혼란이 없다.
            # injury_weeks는 이제(버그 수정 후) '남은 일수'를 담고 있어서,
            # 오늘(day)부터 d까지 며칠 지났는지로 그날도 부상 중인지 정확히
            # 계산할 수 있다.
            _inj_days_left = p.get("injury_weeks", 0) if p.get("injured") else 0
            if _inj_days_left > 0 and (d - day) < _inj_days_left:
                cb.hide()
                _idetail3 = p.get("injury_detail") or "부상"
                _days_left_that_day = _inj_days_left - (d - day)
                hl.setText(f"🚑 {_days_left_that_day}일 남음")
                if ml:
                    ml.setText(f"🚑 부상\n{_idetail3}")
                    ml.setStyleSheet("color:#ff6666;font-weight:bold;font-size:12px;"
                                     "background:#3a1a1a;border-radius:4px;padding:4px;")
                    ml.show()
                continue

            if match_info:
                cb.hide()
                if match_info.get("intl"):
                    # 국가대표 경기 (월드컵/대륙컵/예선)
                    # [2026-07 색상 규칙 개편, 신민용 요청] 예전엔 국대 경기
                    # 전부(월드컵/대륙컵/예선) 파란색 하나로 뭉뚱그려 표시됐다.
                    # 리그=초록, 컵=보라, 챔스=황금과 같은 급으로 나누되,
                    # 국대 안에서도 월드컵·대륙컵 본선은 주황, 그 외(예선 등)는
                    # 빨강으로 구분한다 — schedule_window.py의 국제전 탭
                    # 헤더 색상 규칙과 동일하게 맞춘다.
                    stage = match_info.get("stage_ko", "")
                    grp   = f" {match_info['grp']}조" if match_info.get("grp") else ""
                    opp   = f"{match_info.get('opp_flag','')}{match_info.get('opp','')}"
                    hl.setText("스트레스 +8")
                    if ml:
                        _is_main = match_info.get("kind") in ("world", "continent")
                        _txt_c = "#ffaa33" if _is_main else "#ff6666"
                        _bg_c  = "#3a2a1a" if _is_main else "#3a1a1a"
                        ml.setText(f"🌍 {match_info['league_name']} {stage}{grp}\nvs {opp}")
                        ml.setStyleSheet(f"color:{_txt_c};font-weight:bold;font-size:12px;"
                                         f"background:{_bg_c};border-radius:4px;padding:4px;")
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
                elif match_info.get("cup"):
                    # [2026-07 신설] 국내 컵대회(FA컵식)
                    rname = match_info.get("round_name", "")
                    opp   = match_info.get("opp", "")
                    _otier = match_info.get("opp_tier")
                    opp_disp = f"{opp} ({_otier}부)" if _otier else opp
                    loc   = "홈" if match_info.get("is_home") else "원정"
                    hl.setText("스트레스 +8")
                    if ml:
                        ml.setText(f"🎖️ {match_info['league_name']} {rname} ({loc})\nvs {opp_disp}")
                        ml.setStyleSheet("color:#c48aff;font-weight:bold;font-size:12px;"
                                         "background:#2a1a3a;border-radius:4px;padding:4px;")
                        ml.show()
                else:
                    league_name = match_info.get("league_name", "")
                    loc = "홈" if match_info.get("is_home") else "원정"
                    stress_val = 5 if match_info.get("is_home") else 8
                    hl.setText(f"스트레스 +{stress_val}")
                    if ml:
                        ml.setText(f"⚽ {league_name}\n({loc})")
                        # [2026-07 색상 규칙, 신민용 요청] 리그=초록. 예전엔
                        # 배경만 초록이고 글자색은 노란(#ffcc00)이라 다른
                        # 대회(컵=보라/챔스=황금) 색상 규칙과 안 맞았다.
                        ml.setStyleSheet("color:#66ff99;font-weight:bold;font-size:12px;"
                                         "background:#1a3a1a;border-radius:4px;padding:4px;")
                        ml.show()
            else:
                # [2026-07 재설계] 예전엔 경기 전날이면 콤보를 비활성화하고
                # 텍스트 자체를 "휴식"으로 덮어썼다 — 근데 이러면 사용자가
                # 원래 그날 뭘 골라놨었는지(예: 고강도)가 화면에서 아예
                # 사라지고, 진짜 사용자가 "휴식"을 고른 것처럼 보였다.
                # 이제는 경기 매치 라벨(ml)과 똑같은 방식으로 콤보 자체를
                # 숨기고 "🛌 대회 전 휴식" 전용 라벨을 보여준다 — 콤보의
                # currentText는 절대 안 건드리므로 사용자의 원래 선택이
                # 화면 밑에 그대로 보존되고, 그 경기가 없어지면 콤보가
                # 다시 나타나면서 원래 선택이 그대로 드러난다(별도 복원
                # 로직이 필요 없어짐). 실제 스트레스/휴식 효과는 이 표시와
                # 무관하게 _advance의 스케줄 빌더가 "내일 경기 있으면 오늘
                # 무조건 휴식 처리"로 그대로 적용한다.
                next_mi = _match_cache.get(d + 1)
                if next_mi is None and d + 1 not in _match_cache:
                    next_mi = self._get_match_for_day(d + 1, p)
                if next_mi:
                    cb.hide()
                    loc_txt = "원정 이동" if next_mi.get("is_home") is False else "경기 하루 전"
                    hl.setText(f"🚌 {loc_txt} (스트레스 -15)")
                    if ml:
                        ml.setText("🛌 대회 전 휴식")
                        ml.setStyleSheet("color:#99ccff;font-weight:bold;font-size:12px;"
                                         "background:#1a2a3a;border-radius:4px;padding:4px;")
                        ml.show()
                else:
                    if ml: ml.hide()
                    cb.setEnabled(True)
                    hl.setText("")
                    cb.show()

        # 버튼 활성/비활성
        from constants import SEASON_PHASES
        _ps_s, _ps_e = SEASON_PHASES["preseason1"]      # 1~3주
        _os_s, _os_e = SEASON_PHASES["postseason"]       # 44~52주 (국제대회 전용 비시즌)
        is_pre  = _ps_s <= week <= _ps_e
        is_post = _os_s <= week <= _os_e
        is_off  = is_pre or is_post

        from constants import MIN_JOIN_AGE
        age = p.get("age", 16)
        has_team  = bool(p.get("current_team_id"))
        can_join  = is_pre and age >= MIN_JOIN_AGE and not has_team and not self._join_used

        self.btn_join.setEnabled(can_join)
        self.btn_join.setVisible(not has_team)
        # [2026-07] 에이전트는 이제 비시즌 제한 없이 언제든 변경 가능하므로
        # 버튼도 항상 활성화한다(예전엔 is_off일 때만 눌렸음).
        # [은퇴] 리그 경기(신규 캘린더: 43주)가 끝나고 우승·수상이 확정 가능한
        #   국제대회 비시즌(44~52주), 그리고 새 시즌 시작 직후이자 계약 연장
        #   거절 타이밍인 프리시즌(1~3주)에 허용.
        #   - 프리시즌: 직전 시즌은 이미 시즌전환(_end_of_season)으로 우승·수상이
        #     확정됐고, 새 시즌은 아직 경기가 없어 누락 위험이 없다.
        #   - 그 외(4~43주) 리그 진행 중에는 여전히 은퇴 불가(우승 누락 방지).
        # [2026-07 요청 반영] 예전엔 리그 진행 중(4~43주)엔 은퇴가 막혀 있었다
        # (우승·수상 확정 전에 은퇴해서 누락되는 걸 막기 위함) — 신민용 요청으로
        # 항상 활성화한다. 트로피/수상 누락 위험은 여전히 존재할 수 있으니 참고.
        can_retire = True
        self.btn_retire.setEnabled(can_retire)
        has_team = bool(p.get("current_team_id"))
        self.btn_standing.setEnabled(has_team)
        self.btn_schedule.setEnabled(has_team)

        # [오퍼 토글] 시즌 구간과 무관하게 언제든 켜고 끌 수 있다.
        #   OFF여도 '이적 요청' 중이면 오퍼는 계속 뜨므로 라벨로 안내한다.
        offers_on = bool(p.get("offers_enabled", 1))
        if offers_on:
            self.btn_offer_toggle.setText("🔔 오퍼 ON")
            self.btn_offer_toggle.setToolTip("클릭하면 자동 이적 오퍼 알림을 끕니다")
        elif p.get("transfer_requested"):
            self.btn_offer_toggle.setText("🔕 오퍼 OFF*")
            self.btn_offer_toggle.setToolTip("이적 요청 중이라 오퍼는 계속 옵니다. 클릭하면 다시 켭니다")
        else:
            self.btn_offer_toggle.setText("🔕 오퍼 OFF")
            self.btn_offer_toggle.setToolTip("클릭하면 자동 이적 오퍼 알림을 켭니다 (팀 입단에는 영향 없음)")

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
                    nat = p.get("fixed_nat") or p.get("nationality1", "")
                # get_my_match로 정확한 stage/grp 파악 (조별리그면 내 그룹만 표시용)
                _im = intl_engine.get_my_match(week)
                _stage    = _im["stage"] if _im else (t.get("status") or "group")
                _stage_ko = _im.get("stage_ko", "") if _im else ""
                if _im:
                    _grp = _im["grp"]
                else:
                    # 비경기 주차: intl_entries에서 내 조 직접 조회
                    try:
                        from database import get_conn as _gc
                        _c = _gc()
                        _er = _c.execute(
                            "SELECT grp FROM intl_entries WHERE tournament_id=? AND country=?",
                            (t["id"], nat)).fetchone()
                        _c.close()
                        _grp = _er["grp"] if _er and _er["grp"] else ""
                    except Exception:
                        _grp = ""
                return {
                    "intl": True,
                    "tournament_id": t["id"],
                    "league_name": t["name"],
                    "my_nat": nat,
                    "stage": _stage,
                    "stage_ko": _stage_ko,
                    "grp": _grp,
                    "week": week,
                }
        except Exception:
            pass
        # 챔피언스리그 확인 (41~52주)
        try:
            import champions_engine
            from game_engine import get_state as _gs
            _st = _gs()
            cl_m = champions_engine.get_my_cl_match(week)
            if cl_m:
                return {
                    "cl": True,
                    "tournament_id": cl_m["tournament_id"],
                    "league_name": cl_m.get("league_name", ""),
                    "stage": cl_m.get("stage", "group"),
                    "stage_ko": cl_m.get("stage_ko", ""),
                    "grp": cl_m.get("grp", ""),
                    "week": week,
                }
            # 경기 없는 주차에도 대회 진행 중이면 조별리그 context 유지
            # (포메이션 위젯이 내 조 팀 목록을 표시하기 위해)
            if _st:
                cl_gi = champions_engine.get_my_cl_group_info(_st["current_year"])
                if cl_gi:
                    # _my_cl_tournament로 대회 정보 가져오기
                    from champions_engine import _my_cl_tournament
                    _cp = p  # center_panel의 p
                    _ct = _my_cl_tournament(_cp, _st["current_year"])
                    if _ct and _ct.get("status") != "done":
                        return {
                            "cl": True,
                            "tournament_id": _ct["id"],
                            "league_name": _ct["name"],
                            "stage": "group",
                            "stage_ko": "",
                            "grp": cl_gi["grp"],
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
            "AND week=? AND (home_team_id=? OR away_team_id=?)",
            (lid, week, tid, tid)).fetchone()
        conn.close()
        if row["n"] > 0:
            return True
        import intl_engine
        if intl_engine.has_my_match_between(week, week):
            return True
        import champions_engine
        return champions_engine.has_my_cl_match_between(week, week)

    def _on_day_combo_changed(self, idx, text):
        """[2026-07 추가] 콤보 idx번(0~6)이 바뀌었을 때, 지금 그게 실제로
        가리키는 연중 일자(day)를 계산해서 '사용자가 직접 고른 값'으로
        저장한다. 단, 지금 그 콤보가 비활성 상태(강제 휴식 잠금 중)라면
        이건 내가 setCurrentText로 프로그램적으로 바꾼 것뿐이라 저장하지
        않는다 — 그래야 나중에 강제 휴식이 풀렸을 때 사용자의 원래 선택을
        복원할 수 있다."""
        if self._restoring:
            return
        cb = self.week_combos[idx]
        if not cb.isEnabled():
            return
        st = get_state()
        if not st:
            return
        from constants import DAYS_PER_WEEK
        week = st["current_week"]
        day  = st.get("current_day") or ((week - 1) * DAYS_PER_WEEK + 1)
        bundle_start = day - self._step_idx if self._step_mode else day
        self._day_prefs[bundle_start + idx] = text

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
        """하루씩 보기 ↔ 1주씩 보기 전환.
        단, 하루씩 진행 도중(묶음 일부만 진행)에는 전환 불가."""
        if self._step_idx > 0:
            # 묶음 진행 중 → 전환 막고 버튼 상태 원위치
            self.btn_mode.setChecked(self._step_mode)
            show_toast(self, "⚠  진행 중인 1주를 끝낸 뒤 전환할 수 있습니다", "#cc6600", 1600)
            return

        self._step_mode = self.btn_mode.isChecked()
        if self._step_mode:
            self.btn_mode.setText("📆 하루씩")
            show_toast(self, "🔍 하루씩 보기  —  1주 일정대로 하루씩 진행", "#664400", 1500)
        else:
            self.btn_mode.setText("📅 1주씩")
            self._locked_sched = None
            self._step_idx     = 0
            show_toast(self, "📅 1주씩 보기  —  한 주씩 진행", "#006622", 1400)
        self._save_ui_state()   # 모드 전환 결과를 세이브에 반영
        self.refresh()

    # ── 진행 ─────────────────────────────────────

    def _toggle_popup_timers(self, pause: bool):
        """[스레드 안전] schedule_window/standings_window의 5초 자동갱신,
        world_browser_window의 검색 디바운스 등 QTimer는 메인 스레드에서
        돈다 — main_win.setEnabled(False)로는 안 막힌다(그건 사용자 입력만
        차단). 워커가 DB에 쓰는 동안 이 타이머들이 같은 커넥션으로 SELECT를
        던지면 진짜 동시 접근이 되므로, 워커 시작 전에 명시적으로 멈추고
        끝나면 되돌린다. 창이 이미 닫혀 C++ 객체가 삭제된 경우도 있어
        RuntimeError는 조용히 무시한다.
        [2026-07 버그 수정] world_browser_window(세계기록실)가 이 목록에
        빠져있었다 — 그 창은 비모달 QDialog라 main_win.setEnabled(False)로도
        안 막히는데, 검색창 디바운스 타이머(250ms)가 워커와 같은 풀 커넥션을
        건드려서 "not an error"/"no transaction is active" 류 크래시의
        실제 원인 중 하나였다(다른 타이머는 이미 다 막아뒀는데 이 창만 누락)."""
        for win in (getattr(self, "_schedule_win", None), getattr(self, "_standings_win", None),
                    getattr(self, "_world_win", None)):
            if win is None:
                continue
            try:
                win.pause_refresh() if pause else win.resume_refresh()
            except RuntimeError:
                pass  # 창이 이미 닫혀 C++ 객체가 삭제된 경우

    def _show_processing_overlay(self, text):
        target = self.main_win if self.main_win else self
        if self._proc_overlay is None or self._proc_overlay.parent() is not target:
            self._proc_overlay = _ProcessingOverlay(target)
        self._proc_overlay.show_message(text)

    def _hide_processing_overlay(self):
        if self._proc_overlay is not None:
            self._proc_overlay.hide()

    def _advance(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt as _Qt
        # [UX] 진행 중 버튼 비활성화 + 로딩 커서 → 처리 완료 후 즉시 복원
        self.adv_btn.setEnabled(False)
        QApplication.setOverrideCursor(_Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        p  = get_player()
        st = get_state()
        if not p or not st:
            QApplication.restoreOverrideCursor()
            self.adv_btn.setEnabled(True)
            return

        # [복수국적] 대표팀 선택이 대기 중이면 그것부터 처리 (진행 차단)
        #   ※ 22세 1~4주차 강제확정은 '새해 진입 직후'에 띄운다(아래 advance_days 뒤).
        #     일정을 짜기 전에 먼저 국적을 정하도록 하기 위함.
        import intl_engine
        forced = intl_engine.get_forced_commit()
        if forced:
            self._show_forced_commit(forced)
            from PyQt6.QtWidgets import QApplication
            QApplication.restoreOverrideCursor()
            self.adv_btn.setEnabled(True)
            return
        pend = intl_engine.get_pending_choice()
        if pend:
            show_toast(self, "⚠  먼저 대표팀을 선택해야 합니다!", "#cc6600", 1600)
            self._show_nat_choice(pend)
            from PyQt6.QtWidgets import QApplication
            QApplication.restoreOverrideCursor()
            self.adv_btn.setEnabled(True)
            return

        week = st["current_week"]
        from constants import DAYS_PER_WEEK
        day  = st.get("current_day") or ((week - 1) * DAYS_PER_WEEK + 1)
        from constants import MIN_JOIN_AGE, SEASON_PHASES
        _ps_s, _ps_e = SEASON_PHASES["preseason1"]

        # 17살 이상인데 팀이 없고 프리시즌(1~3주)이면 입단 강제.
        #   단, 올해 모든 오퍼가 결렬돼 '1년 훈련'을 택한 경우(_skip_join_lock)는
        #   이번 시즌 동안 입단을 강제하지 않고 그대로 진행시킨다.
        if (p["age"] >= MIN_JOIN_AGE and not p.get("current_team_id")
                and _ps_s <= week <= _ps_e and not getattr(self, "_skip_join_lock", False)):
            show_toast(self, "⚠  먼저 팀에 입단해야 합니다!", "#cc6600", 1500)
            from PyQt6.QtWidgets import QApplication
            QApplication.restoreOverrideCursor()
            self.adv_btn.setEnabled(True)
            return

        # ── 진행할 일정 결정 ──
        # 1주씩 모드: 현재 콤보 7개(하루하루)로 일정 만들어 한 번에 진행.
        # 하루씩 모드: 묶음 시작 시 7일 일정을 확정·고정하고,
        #            누를 때마다 그 중 하루만 진행. 7일 다 지나면 자동 1주 복귀.
        def _build_week_sched():
            sched = []
            for i in range(DAY_BUNDLE_SIZE):
                cb    = self.week_combos[i]
                d     = day + i
                sel   = cb.currentText()
                ttype = TRAIN_MAP_KO.get(sel, "중강도")
                mi = self._get_match_for_day(d, p)
                if mi:
                    sched.append((d, "경기", mi))
                else:
                    # [2026-07 확장] 경기 하루 전엔(홈/원정 무관) 이동/컨디션
                    # 관리 목적으로 무조건 휴식을 강제한다(실제 프로팀 루틴과
                    # 동일, 이틀 연속 경기 방지) — 사용자가 그날 다른 훈련을
                    # 골라놨어도 경기 전날이면 덮어쓴다.
                    next_mi = self._get_match_for_day(d + 1, p)
                    if next_mi:
                        ttype = "휴식"
                    # 강점/약점훈련은 엔진이 스탯을 자동 선별하므로 detail 불필요.
                    sched.append((d, ttype, None))
            return sched

        if not self._step_mode:
            # 1주(7일) 한 번에
            schedule = _build_week_sched()
        else:
            # 하루씩: 묶음 시작이면 7일 일정 확정·고정.
            #   _step_idx 가 0이 아니어도 _locked_sched 가 비어 있으면 새로 만든다.
            #   그리고 묶음의 시작일(day - _step_idx)부터 만들어야 인덱스가 맞음.
            if self._locked_sched is None:
                bundle_start = day - self._step_idx
                sched = []
                for i in range(DAY_BUNDLE_SIZE):
                    cb    = self.week_combos[i]
                    d     = bundle_start + i
                    sel   = cb.currentText()
                    ttype = TRAIN_MAP_KO.get(sel, "중강도")
                    mi = self._get_match_for_day(d, p)
                    if mi:
                        sched.append((d, "경기", mi))
                    else:
                        # 경기 전날 휴식 강제(홈/원정 무관) — 위 _build_week_sched 주석 참고.
                        next_mi = self._get_match_for_day(d + 1, p)
                        if next_mi:
                            ttype = "휴식"
                        sched.append((d, ttype, None))
                self._locked_sched = sched
            # 인덱스 안전 클램프 (혹시라도 범위를 벗어나면 마지막 날로)
            idx = max(0, min(self._step_idx, len(self._locked_sched) - 1))
            schedule = [self._locked_sched[idx]]

        # ── 여기까지는 UI/검증 로직이라 가볍다. 무거운 처리(advance_days,
        #    특히 52→1주 시즌전환의 AI 생애주기 계산)만 백그라운드로 뺀다. ──
        # [스레드 안전] 워커가 도는 동안 메인 윈도우 전체를 비활성화해서, 다른
        #   버튼(오퍼/입단/세계기록실 등)이 같은 DB 커넥션을 동시에 건드리는
        #   걸 막는다. SQLite 커넥션은 여러 스레드가 '동시에' 쓰면 안전하지
        #   않으므로, 처리 중엔 오직 워커 스레드만 DB에 접근하도록 보장해야 한다.
        if self.main_win:
            self.main_win.setEnabled(False)
        self._toggle_popup_timers(pause=True)

        # [UX] 처리 중 화면 전체가 그냥 멈춘 것처럼 보이던 문제 수정 —
        #   WaitCursor만으론 신호가 약해서(마우스 안 움직이면 못 봄) 오버레이로
        #   명시적으로 "지금 뭘 처리 중인지" 보여준다. 52주차 마지막 날(시즌
        #   전환이 걸리는 그 날)이면 별도 문구로 왜 좀 더 걸리는지 알려준다.
        from constants import DAYS_PER_WEEK, day_to_week
        _last_day = schedule[-1][0]
        _is_season_transition = (
            _last_day % DAYS_PER_WEEK == 0 and day_to_week(_last_day) == 52)
        if _is_season_transition:
            self._show_processing_overlay(
                "⏳ 시즌 전환 처리 중...\n(전세계 이적시장 · 신인 영입 · 승강제 반영)")
        else:
            self._show_processing_overlay("⏳ 진행 중...")

        self._advance_worker = _AdvanceWorker(schedule, self)
        self._advance_worker.finished_ok.connect(
            lambda: self._on_advance_finished())
        self._advance_worker.failed.connect(self._on_advance_failed)
        self._advance_worker.start()

    def _on_advance_finished(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.restoreOverrideCursor()
        self._hide_processing_overlay()
        self.adv_btn.setEnabled(True)
        if self.main_win:
            self.main_win.setEnabled(True)
        self._toggle_popup_timers(pause=False)

        # ── 묶음 진행 상태 갱신 ──
        if self._step_mode:
            self._step_idx += 1
            if self._step_idx >= DAY_BUNDLE_SIZE:
                # 1주(7일) 묶음 완료 → 잠금 해제, 다음 1주 묶음으로
                self._step_idx     = 0
                self._locked_sched = None

        # 묶음(7일=1주)이 완전히 끝났는가? (1주 모드는 항상 True)
        bundle_done = (not self._step_mode) or (self._step_idx == 0)

        # 진행으로 바뀐 묶음 위치/고정 일정을 세이브에 반영.
        #   (advance_days 가 current_week 등을 이미 갱신한 뒤이므로 충돌 없음)
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
        from constants import SEASON_PHASES as _SP4
        _pss, _pse = _SP4["preseason1"]
        if _pss <= new_week <= _pse and p2.get("age",0) >= MIN_JOIN_AGE and not p2.get("current_team_id"):
            # 새 시즌 프리시즌 진입 → 작년 '전부 결렬→1년 훈련' 보류를 해제하고
            #   올해 다시 입단(오퍼)에 도전하게 한다.
            self._skip_join_lock = False
            self._join_used = False
            self.btn_join.setEnabled(True)
            show_toast(self, f"⭐ {st2['current_year']}년 새 시즌!  팀 입단 기간입니다", "#006622", 2000)

        # [복수국적] ★새해 진입 직후★ 22세 프리시즌(1~3주) 미고정이면 '일정 짜기 전에'
        #   국적부터 강제 확정. 52주에서 진행 버튼을 눌러 1주차로 막 넘어온 이 시점에
        #   띄워야 사용자가 프리시즌 훈련을 선택하기 전에 대표팀을 정한다.
        import intl_engine
        forced = intl_engine.get_forced_commit()
        if forced:
            # [타이밍] 1주차 화면 먼저 갱신 후 팝업 표시
            if self.main_win:
                self.main_win.refresh_all()
            QApplication.processEvents()
            self._show_forced_commit(forced)
        # [복수국적] 두 나라 다 본선 진출 → 대표팀 선택 팝업 (선택 전까지 차출 보류)
        pend = intl_engine.get_pending_choice()
        if pend:
            self._show_nat_choice(pend)

        # 재계약 팝업은 '새 시즌 진입 직후 즉시' 떠야 한다.
        #   오퍼 플래그(_contract_renew_offer)는 연말(52주) 처리에서 세팅되므로,
        #   1주차로 막 넘어온 이 시점에 이미 존재한다. bundle_done(1주 묶음 완료)을
        #   기다리면 '프리시즌 진행을 누른 뒤에야' 떠서 타이밍이 어긋난다.
        #   다이얼로그에서 수락/거절 시 플래그가 0으로 리셋되므로 반복 노출도 없다.
        if p2.get("_contract_renew_offer", 0) > 0:
            self._show_renew_dialog(p2)

        # 자동 오퍼 팝업은 1주 묶음이 완료됐을 때만
        # (1주씩 본다고 매주 오퍼가 뜨지 않음)
        if bundle_done:
            if p2.get("current_team_id") and in_zone:
                self._show_auto_offer(new_week)

        if self.main_win:
            self.main_win.refresh_all()

    def _on_advance_failed(self, msg):
        from PyQt6.QtWidgets import QApplication
        QApplication.restoreOverrideCursor()
        self._hide_processing_overlay()
        self.adv_btn.setEnabled(True)
        if self.main_win:
            self.main_win.setEnabled(True)
        self._toggle_popup_timers(pause=False)
        QMessageBox.critical(self, "진행 중 오류",
                              f"시즌/주차 진행 중 오류가 발생했습니다:\n{msg}")


    def _update_next_week_preview(self, bundle_start, p):
        """[2026-07 신설] 우측 상단 작은 박스 7개 — 다음 주(현재 표시 중인
        7일 묶음의 바로 다음 7일) 일정을 대회 종류별 색으로 간단히 미리
        보여준다. 4번 색상 규칙(리그=초록/컵=보라/챔스=황금/국대=주황·빨강)과
        동일한 배색을 쓰며, 경기 없는 날(훈련/휴식)은 회색으로 둔다.
        [2026-07 버그수정] 소속 클럽이 없어도(국대만 있는 어린 선수 등)
        국제전은 뜰 수 있으므로 has_team 게이트 없이 항상 조회한다."""
        if not hasattr(self, "nwp_boxes"):
            return
        next_start = bundle_start + DAY_BUNDLE_SIZE
        for i, box in enumerate(self.nwp_boxes):
            d = next_start + i
            mi = self._get_match_for_day(d, p)
            if not mi:
                color = "#333"
            elif mi.get("intl"):
                color = "#ffaa33" if mi.get("kind") in ("world", "continent") else "#ff6666"
            elif mi.get("cl"):
                color = "#ffd24d"
            elif mi.get("cup"):
                color = "#c48aff"
            else:
                color = "#66ff99"
            box.setStyleSheet(f"background:{color};border-radius:3px;")

    def _get_match_for_day(self, day, p):
        """그 날짜(day)에 내 경기가 있는지 확인.
        클럽 리그 경기는 match_results.day로 정확한 날짜가 있어 그대로 대조.
        국제대회/챔스는 day 컬럼이 없어(주 단위 대회) game_engine의
        _week_intl_cl_day()가 정한 '그 주의 정확한 날'에 배정된 것으로
        취급한다 — advance_days의 실제 처리 시점과 반드시 같은 함수를
        써서 화면 표시와 실제 진행이 어긋나지 않게 한다(예전엔 화면은
        '주 마지막 날'로 보여주면서 실제 처리는 그 주 아무 날에나
        조용히 일어나던 불일치가 있었다)."""
        from constants import day_to_week, DAYS_PER_WEEK
        week = day_to_week(day)
        tid = p.get("current_team_id", 0)
        if tid:
            from database import get_conn
            conn = get_conn()
            # 항상 DB에서 팀의 실제 league_id 재조회 (이적/승강 후 변경 반영)
            team_row = conn.execute(
                "SELECT l.id as lid, l.name as lname FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (tid,)).fetchone()
            if team_row:
                lid = team_row["lid"]
                from game_engine import get_state
                st = get_state()
                cur_season = st["current_season"] if st else 1
                row = conn.execute(
                    "SELECT * FROM match_results WHERE league_id=? AND week=? AND day=? "
                    "AND (home_team_id=? OR away_team_id=?) AND home_score=-1 AND season=?",
                    (lid, week, day, tid, tid, cur_season)).fetchone()
                conn.close()
                if row:
                    return {
                        "home_id":     row["home_team_id"],
                        "away_id":     row["away_team_id"],
                        "league_name": team_row["lname"],
                        "league_id":   lid,
                        "is_home":     row["home_team_id"] == tid,
                        "season":      row["season"],
                        "year":        row["year"],
                    }
            else:
                conn.close()

        # 클럽 경기 없음 → _week_intl_cl_day가 정한 그 날에만 국가대표/챔스/컵대회 확인.
        from game_engine import _week_intl_cl_day
        if day == _week_intl_cl_day(week, p):
            import intl_engine
            im = intl_engine.get_my_match(week)
            if im:
                return im
            import champions_engine
            cm = champions_engine.get_my_cl_match(week)
            if cm:
                return cm
            import cup_engine
            return cup_engine.get_my_cup_match(week)
        return None

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
            #  - 재계약 팝업은 만료 다음 해 프리시즌(1~3주)에 뜨므로 올해가 1년차 → -1 보정
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
                _contract_renew_offer=0, apply_attempts_used=0)
            from game_engine import add_log
            add_log("📋 재계약 거절. 소속 없음 상태가 됩니다.", "event")
            dlg.reject()
            if self.main_win: self.main_win.refresh_all()

        btn_accept.clicked.connect(_accept)
        btn_reject.clicked.connect(_reject)
        dlg.exec()

    def _show_forced_commit(self, forced):
        """[복수국적] 22세 프리시즌(1~3주) — 평생 뛸 대표팀 국적을 강제로 확정.
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
        순서:  ① 국가 선택(어느 나라에 도전할지)  →  ② 국가대표 선발 여부
        →  ③ 예선 통과/본선 진출.
        ①은 스쿼드 확정이 아니라 "이번엔 이 나라 쪽으로 도전한다"는
        선택일 뿐이고, 실제 승선 여부는 ②에서 갈린다(미선발일 수 있음).
        선택한 뒤에야 선발·예선 결과가 단계적으로 드러난다."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QFrame

        _rs = res.get("result", "")
        _kind = res.get("kind", "")
        _is_qual = (_kind == "wc_qual")
        # 각 단계 라인 구성 (결과에 따라 ②③ 색/내용 분기)
        # [버그 수정] line1이 "확정했습니다"라고 단독으로 뜨는 순간, 아직
        # 실제 선발 여부(line2)가 공개되기도 전인데 마치 대표팀 승선이
        # 이미 확정된 것처럼 읽혔다. 실제로는 복수 국적 중 "이번엔 이
        # 나라 쪽으로 도전해보겠다"를 고른 것뿐이고, 진짜 확정(선발 여부)
        # 은 바로 다음 줄(line2)에서 갈린다 — "확정이라 뜨면 헷갈린다"는
        # 지적 그대로. "확정" 대신 "선택"으로 바꿔서 이건 후보 등록일
        # 뿐임을 분명히 한다.
        line1 = f"🏳️ <b style='color:#ffcc66'>{nat}</b> 대표팀에 도전합니다."
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

    def _do_toggle_offers(self):
        """오퍼 알림 ON/OFF 토글. 팀 입단(무소속 강제 입단)에는 영향 없음."""
        p = get_player()
        if not p: return
        from game_engine import update_player
        cur = bool(p.get("offers_enabled", 1))
        new_val = 0 if cur else 1
        update_player(offers_enabled=new_val)
        if new_val:
            show_toast(self, "🔔 오퍼 알림을 켰습니다", "#006622", 1500)
        else:
            msg = "🔕 오퍼 알림을 껐습니다  (팀 입단은 계속 가능)"
            if p.get("transfer_requested"):
                msg = "🔕 오퍼 알림을 껐습니다  (단, 이적 요청 중이라 오퍼는 계속 옵니다)"
            show_toast(self, msg, "#666666", 2000)
        if self.main_win: self.main_win.refresh_all()

    def _do_join(self):
        """소속 없음일 때만 수동 팀 입단 (프리시즌 1~3주차)."""
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
        offers = generate_offers()  # [2026-07] 개수는 이제 함수 내부 고정값(자국10+타국6)이 결정
        from ui.offer_window import OfferWindow
        # 이 창은 소속 팀이 없을 때만 뜨므로(위에서 이미 체크) 첫 입단이든
        # 퇴출/계약종료 후 재입단이든 항상 강제 입단 모드로 띄운다.
        # force_select=False면 닫기로 그냥 빠져나갈 수 있는데, 그러면 입단할
        # 곳이 없는 채로 진행이 막히거나 강제 은퇴로 이어질 수 있다.
        dlg = OfferWindow(offers, p.get("language","ko"), self,
                          title="🏟 팀 입단", force_select=True, grid=True, apply_slots=4)
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
        """소속 있을 때 자동 오퍼 팝업 (이적시장: 여름 1~3주, 겨울 28~29주)."""
        from game_engine import _offer_probability
        p = get_player()
        if not p or not p.get("current_team_id"): return
        if self._auto_offer_shown: return

        # [오퍼 토글] 꺼져 있으면 자동 오퍼 팝업을 건너뛴다.
        #   단, '이적 요청' 중이면 사용자가 명시적으로 이적을 원한다는 뜻이므로
        #   토글과 무관하게 오퍼를 계속 보여준다.
        if not p.get("offers_enabled", 1) and not p.get("transfer_requested"):
            return

        prob = _offer_probability(p, week)
        import random
        if random.random() > prob:
            return  # 이번 구간 오퍼 없음

        self._auto_offer_shown = True
        offers = generate_offers()
        if not offers: return

        from ui.offer_window import OfferWindow
        dlg = OfferWindow(offers, p.get("language","ko"), self, title="✈ 오퍼", grid=True)
        self._offer_dlg = dlg
        # 모달(exec)로 띄워 오퍼창이 열려 있는 동안 next day 진행을 차단.
        dlg.exec()
        self._on_auto_offer_done(dlg)

    def _on_auto_offer_done(self, dlg):
        if dlg.chosen:
            join_team(dlg.chosen["team_id"], dlg.chosen["salary"], transfer_type="오퍼")
            if self.main_win: self.main_win.refresh_all()

    def _do_world_browser(self):
        from ui.world_browser_window import WorldBrowserWindow
        self._world_win = WorldBrowserWindow(self)

        def _clear_world(*_a):
            self._world_win = None
        self._world_win.finished.connect(_clear_world)
        self._world_win.show()

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
        # [2026-07 요청 반영] 예전엔 비시즌(5~11주/26~32주)에만 에이전트를
        # 바꿀 수 있었는데, 그 제한을 없애고 언제든 변경 가능하게 한다.
        from ui.agent_window import AgentWindow
        self._agent_dlg = AgentWindow(p.get("language", "ko"), self)
        self._agent_dlg.finished.connect(lambda: self._on_agent_done(self._agent_dlg))
        self._agent_dlg.show()

    def _on_agent_done(self, dlg):
        if self.main_win: self.main_win.refresh_all()

    def _do_retire(self):
        st = get_state()
        week = st["current_week"]
        from constants import SEASON_PHASES as _SP5
        _pss5, _pse5 = _SP5["preseason1"]
        _oss5, _ose5 = _SP5["postseason"]
        # 은퇴 가능 구간: 프리시즌(1~3주, 새 시즌 직후·연장 거절 타이밍) 또는
        #   국제대회 비시즌(44~52주, 리그 종료 후).
        if not ((_pss5 <= week <= _pse5) or (_oss5 <= week <= _ose5)):
            show_toast(self, f"⚠  은퇴는 시즌 종료 후({_oss5}주차~) 또는 새 시즌 {_pss5}~{_pse5}주차에 가능합니다", "#cc6600", 1900)
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