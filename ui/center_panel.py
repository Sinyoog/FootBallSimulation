"""
ui/center_panel.py  ─  가운데 메인 패널
"""
import random
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QFrame, QMessageBox,
    QGraphicsDropShadowEffect, QMenu, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QAction

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

    [2026-08 확장, 신민용 요청: "1년 넘기기 로딩 UI가 이상하다"] 처음엔
    1년 넘기기 전용으로 별도 오버레이 클래스(_YearProgressOverlay)를 새로
    만들었는데, 화면 전체를 덮지 못하고 엉뚱한 위치/크기로 뜨는 문제가
    있었다 — 원인을 오래 추적하는 대신, 이미 정상 동작이 검증된 이
    클래스에 진행률 바(옵션)만 추가해서 재사용하는 쪽으로 바꿨다. 같은
    부모(target)·같은 geometry 계산 경로를 그대로 타므로 위치 문제가
    구조적으로 재발할 수 없다."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("background: rgba(10,10,10,0.72);")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        box = QFrame()
        box.setStyleSheet("""
            background: rgba(30,30,30,0.9); border:1px solid #555;
            border-radius:10px;
        """)
        # [2026-08 버그수정, 신민용 리포트: "로딩창 바가 왼쪽 끝에 붙어있고
        # 박스도 그거에 맞춰 줄여달라"] 실제 메인윈도우 구조(QSplitter +
        # QScrollArea 3분할)로 재현해보니, lay.setAlignment(AlignCenter)를
        # 줬는데도 이 box가 sizeHint(~378px)를 무시하고 overlay 거의 전체
        # 너비(예: 1400px 창에서 1382px)까지 늘어나는 현상이 실제로
        # 있었다 — 그 결과 안에 있는 고정폭(320px) 진행률 바가 커진 박스의
        # 왼쪽에 쏠려 보였다. 최대 너비를 명시적으로 못박아 늘어남 자체를
        # 막는다(내용물 기준 380px면 여유 있게 들어감).
        box.setMaximumWidth(380)
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(28, 20, 28, 20)
        box_lay.setSpacing(10)

        self._label = QLabel("⏳ 처리 중...")
        self._label.setStyleSheet("color:white; font-size:16px; font-weight:bold; background:transparent; border:none;")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_lay.addWidget(self._label)

        # 진행률 바 + 보조 라벨(예: "3 / 52주") — 기본은 숨김, 1년 넘기기
        # 처럼 여러 단계를 밟는 작업일 때만 show_progress()로 켠다.
        self._bar = QProgressBar()
        self._bar.setMinimum(0)
        self._bar.setFixedWidth(320)
        self._bar.setTextVisible(True)
        self._bar.setStyleSheet("""
            QProgressBar { background:#222; border:1px solid #555; border-radius:6px;
                            color:white; font-weight:bold; text-align:center; height:22px; }
            QProgressBar::chunk { background-color:#006622; border-radius:5px; }
        """)
        box_lay.addWidget(self._bar, 0, Qt.AlignmentFlag.AlignHCenter)
        self._bar.hide()

        self._sub_label = QLabel("")
        self._sub_label.setStyleSheet("color:#999; font-size:11px; background:transparent; border:none;")
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_lay.addWidget(self._sub_label)
        self._sub_label.hide()

        lay.addWidget(box, 0, Qt.AlignmentFlag.AlignCenter)
        self.hide()

    def _reposition_and_show(self):
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.raise_()
        self.show()
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

    def show_message(self, text):
        """단순 텍스트만 보여주는 기존 방식(진행률 바는 숨김)."""
        self._label.setText(text)
        self._bar.hide()
        self._sub_label.hide()
        self._reposition_and_show()

    def show_progress(self, text, done: int, total: int):
        """[2026-08 신설] 진행률 바가 있는 버전 — 1년 넘기기처럼 여러 단계로
        나뉘는 작업에 쓴다. 이미 떠 있는 상태에서 done/total만 바꿔가며
        반복 호출해도 된다(매번 geometry를 다시 잡으므로 창 크기 변경에도
        안전)."""
        self._label.setText(text)
        self._bar.show()
        self._sub_label.show()
        self._bar.setMaximum(max(1, total))
        self._bar.setValue(done)
        self._sub_label.setText(f"{done} / {total}주")
        self._reposition_and_show()

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
#dlgOk:disabled { background:#2a2a2a; color:#666; }
#dlgNo   { background:#7a2222; color:white; border:none; border-radius:6px;
           padding:9px 14px; font-size:13px; font-weight:bold; }
#dlgNo:hover { background:#9a3030; }
#dlgNo:disabled { background:#2a2a2a; color:#666; }
#dlgChoice { background:#1a4d8f; color:white; border:1px solid #3a7fd5;
             border-radius:6px; padding:12px 14px; font-size:14px; font-weight:bold; }
#dlgChoice:hover { background:#2360ad; }
#negBtn  { background:#2a2a6a; color:white; border:none; border-radius:4px;
           padding:6px 14px; font-size:12px; }
#negBtn:hover { background:#3a3a8a; }
#negBtn:disabled { background:#2a2a2a; color:#555; }
QPushButton { background:#333; color:#ccc; border:none; border-radius:6px;
              padding:9px 14px; font-size:13px; }
QPushButton:hover { background:#444; }
QPushButton:disabled { background:#2a2a2a; color:#555; }
QComboBox {
    background:#2a2a2a; color:#eee; border:1px solid #444;
    border-radius:4px; padding:4px 6px; font-size:13px;
}
QComboBox:disabled { color:#666; }
QComboBox QAbstractItemView {
    background:#2a2a2a; color:#ccc; selection-background-color:#3a6a3a;
}
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
        self._join_reminder_shown_week = None   # 팀 없음 알림 중복 방지(주차별 1회)
        # ── 1주씩 보기 상태 ──
        # _step_mode : 1주씩 보기 on/off
        # _locked_sched : 하루씩 진행 시작 시 고정한 1주(7일) 일정 (7개)
        # _step_idx : 현재 묶음에서 진행한 주 수 (0~3). 0이면 묶음 시작 전.
        self._step_mode    = False
        self._locked_sched = None
        self._step_idx     = 0
        # ── 1년 넘기기 상태 ── [2026-08 신설, 신민용 요청: "팀 없을 때 1년
        # 넘기기". 팀이 없을 때만 선택 가능한 별도 모드 — 켜져 있으면
        # _advance()가 1주씩 52번을 자동으로 이어서 진행하며, 매 주마다
        # 처음 설정해둔 7일 패턴을 그대로 반복한다. day/week 모드와 달리
        # 세이브에 영속화하지 않는다(한 번 눌러 끝까지 도는 일회성 동작).
        self._year_mode        = False   # 모드 선택 상태(메뉴에서 "1년" 선택함)
        self._year_active      = False   # 실제로 52주 루프가 도는 중인지
        self._year_pattern     = None    # 반복할 7일 패턴(콤보 텍스트 리스트)
        self._year_weeks_total = 0
        self._year_weeks_done  = 0
        self._year_paused      = False   # 중단됐지만 이어서 재개 가능한 상태
        self._restoring    = False   # 복원 중 콤보 시그널이 저장을 되부르는 것 방지
        # [2026-07 추가] 경기 전날 강제휴식이 원래 선택을 덮어쓴 뒤, 그 경기가
        # 없어졌을 때(일정 재생성 등) 원래 선택으로 되돌리기 위한 저장소.
        # {day(정수, 연중 일자): 사용자가 마지막으로 '직접' 고른 문자열}
        self._day_prefs    = {}
        self._proc_overlay = None   # 진행 중 오버레이(지연 생성)
        self._build()
        # 세이브에 저장된 메인 화면 상태(모드/묶음/콤보)를 복원한다.
        self._restore_ui_state()
        # [2026-07 신설, 신민용 요청] 오퍼/입단 창을 결정 내리기 전에 껐다
        # 켰다면(또는 창이 열린 채로 앱이 종료됐다면), 새로 랜덤 생성하지
        # 않고 저장된 상태 그대로 창을 다시 띄운다(재접속 오퍼 리롤 방지).
        # 위젯이 완전히 표시된 뒤 뜨도록 한 틱 지연시킨다.
        QTimer.singleShot(0, self._restore_pending_offer_window)

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

        # 하루씩/1주씩/1년 모드 선택
        self.btn_mode = QPushButton("📅 1주씩")
        self.btn_mode.setObjectName("modeBtn")
        self.btn_mode.setToolTip("클릭하면 하루씩 / 1주씩 / 1년 넘기기 중 선택합니다")
        self.btn_mode.clicked.connect(self._show_mode_menu)
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

        # [2026-07 신설, 신민용 요청: "8강(예정)/4강(예정)처럼 참가팀이
        # 아직 안 정해진 다음 라운드도 메인 화면에 미리 보이면 좋겠다"]
        # 이번 주 그리드엔 '오늘' 기준 7일치만 보이는데, 국제대회 토너먼트는
        # 이제 라운드 전체가 대회 시작 시점에 이미 day가 확정돼 있으므로
        # (intl_engine._precreate_ko_shell), 아직 그 주가 오지 않았어도
        # "다음 라운드가 언제인지"는 미리 알 수 있다 — 내 국가가 아직
        # 대회에 살아있다면, 그 다음 라운드의 예정일을 별도 줄로 보여준다.
        self.lbl_next_intl = QLabel("")
        self.lbl_next_intl.setObjectName("nextIntlPreview")
        self.lbl_next_intl.setStyleSheet(
            "color:#ffaa33;font-weight:bold;font-size:12px;padding:2px 4px;")
        self.lbl_next_intl.hide()
        self.lay.addWidget(self.lbl_next_intl)

        # [2026-07 신설, 신민용 리포트: "이것도 월드컵처럼 다음 일정
        # 표시가 있어야 하는거 아냐?"] 승강 플레이오프도 intl과 동일한
        # 패턴 — 44주 전체에 걸쳐 내 팀 매치가 이미 day까지 확정된
        # 상태라 미리 보여줄 수 있다.
        self.lbl_next_po = QLabel("")
        self.lbl_next_po.setObjectName("nextPoPreview")
        self.lbl_next_po.setStyleSheet(
            "color:#ffee55;font-weight:bold;font-size:12px;padding:2px 4px;")
        self.lbl_next_po.hide()
        self.lay.addWidget(self.lbl_next_po)

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
        # [2026-07 신설, 신민용+GPT 다회 설계 확정: "구단 판매 추진" 시스템]
        # 기존 "오퍼 ON/OFF" 버튼 바로 옆에 배치 — 오퍼 토글(외부 구단의
        # 자발적 관심)과 판매추진 토글(내 구단이 나를 시장에 내놓는 것)은
        # 서로 다른 층위라 별개 버튼으로 둔다.
        self.btn_sale_push_toggle = QPushButton("🏟 판매추진 ON"); self.btn_sale_push_toggle.setObjectName("actBtn")
        self.btn_retire = QPushButton("🚪 은퇴");     self.btn_retire.setObjectName("actBtn")
        self.btn_world  = QPushButton("🌍 세계 기록실"); self.btn_world.setObjectName("actBtn")
        for b in [self.btn_agent, self.btn_offer_toggle, self.btn_sale_push_toggle, self.btn_retire, self.btn_world]:
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
        self.btn_sale_push_toggle.clicked.connect(self._do_toggle_sale_push)
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
                            if mi and mi.get("pending"):
                                mi = None
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

            # 버튼 라벨을 복원된 모드에 맞춘다(더 이상 체크형 토글이 아님).
            try:
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
                # 기본 스타일 복귀 (전역 스타일시트에 위임) — [2026-07 성능
                # 수정, 신민용 리포트: "일 단위 전환 후 전체적으로 렉"] 원래
                # 이 setStyleSheet("")가 if 블록 밖에 있어서, 이미 꺼져있는
                # (아무 것도 안 바뀐) 프레임도 새로고침마다 매번 다시
                # 스타일시트를 재적용했다(Qt CSS 파싱은 결코 공짜가
                # 아니다 — 하루 셀 7개 x 매 새로고침마다 반복되며 누적).
                # "꺼짐 → 꺼짐"은 상태 변화가 없으니 그대로 둬도 이미 ""
                # 상태다 — 실제로 "켜짐 → 꺼짐"으로 전환되는 순간에만
                # 되돌리면 충분하다.
                frame.setStyleSheet("")

    def _match_stress_preview(self, p, is_home: bool) -> int:
        """[2026-07 버그수정, 신민용 리포트] 경기 스트레스 미리보기가
        game_engine._simulate_match의 실제 공식과 따로 놀아서(하드코딩된
        옛날 값 +5/+8/+8 을 그대로 표시), 실제 진행 시 적용되는 값(18/22,
        30세 이상은 13/16)과 화면 표시가 어긋났다. 실제 공식과 동일하게
        맞춘다 — 리그/챔스/컵/국제전 전부 이 함수 하나로 통일."""
        age = p.get("age", 0) or 0
        if age >= 30:
            return 10 if is_home else 14
        if age >= 25:
            return 16 if is_home else 20
        return 18 if is_home else 22

    def refresh(self):
        p  = get_player()
        st = get_state()
        if not p or not st:
            return

        # [2026-08 버그수정, 신민용 리포트: "1년씩 돌린 후 팀 입단하면 이제
        # 1년씩 돌리진 못하는데, 우측 버튼엔 여전히 1년으로 남아있다"]
        # 1년 넘기기는 팀이 없을 때만 쓸 수 있는 모드(_set_mode_year 진입
        # 가드 참고)인데, 그 가드는 "새로 1년 모드를 선택하려는 시점"만
        # 막아서 이미 1년 모드였던 상태로 입단해버리면(오퍼/직접지원 성공)
        # 그 이후로도 버튼 표시가 "🗓 1년"에 그대로 멈춰있었다. refresh()는
        # 입단 처리 직후를 포함해 거의 모든 액션 뒤에 호출되므로, 여기서
        # "1년 모드인데 팀이 생겼다"를 감지해 1주씩 모드로 자동 전환한다.
        if self._year_mode and p.get("current_team_id"):
            self._year_mode    = False
            self._step_mode    = False
            self._locked_sched = None
            self._step_idx     = 0
            self._year_paused      = False
            self._year_pattern     = None
            self._year_weeks_done  = 0
            self._year_weeks_total = 0
            self.btn_mode.setText("📅 1주")
            self.adv_btn.setText("▶▶  이번 주 진행")
            show_toast(self, "⚠  입단으로 1년 넘기기가 해제되어 1주씩 모드로 전환합니다",
                       "#cc6600", 2200)
            self._save_ui_state()

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
        elif self._year_mode:
            # [2026-08 신설] 1년 넘기기 모드에서는 adv_btn 라벨을
            # _set_mode_year/_pause_year_mode가 이미 "1년 넘기기 (이어하기)"
            # 형태로 맞춰뒀다 — 여기서 "이번 주 진행"으로 되돌리면 안 된다.
            # 날짜 표시줄만 갱신하고 버튼 텍스트는 건드리지 않는다.
            self.lbl_phase.setText(
                f"{phase}  |  {season}시즌  "
                f"{day_to_full_date_str(year, day)} ({week}주차)  (🗓 1년 넘기기 모드)")
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
            #
            # [2026-07 추가 버그수정, 신민용 리포트: "8강전이 화면엔 안
            # 뜨는데 로그엔 결과가 있다 — 말이 안 된다"] 위 '고정'이 너무
            # 완고했다 — 묶음이 월요일에 확정될 때(16강도 아직 안 뛴
            # 시점)는 8강 대진이 존재조차 안 해서 그 요일이 그냥 "고강도
            # 훈련"으로 고정돼버렸다. 그런데 game_engine.advance_days는
            # 실제 진행 시점엔 그날 다시 살아있는 조회(get_my_match)를
            # 하므로, 16강을 이겨 8강이 확정되면 표시(고정된 "훈련")와
            # 실제 결과(8강 승부)가 어긋났다 — 정확히 이 어긋남이 신고된
            # 증상이다. '아직 진행 안 한 날'만 매 새로고침마다 살아있는
            # 조회로 다시 확인해서, 그사이 새로 확정된 경기/미정 라운드를
            # 고정본에 반영(패치)한다 — 이미 지난 날은 절대 건드리지 않아
            # (원래 버그 재발 방지) "화면=실제" 원칙은 그대로 유지된다.
            for _i, (_d, _ttype, _detail) in enumerate(self._locked_sched):
                if _d < day:
                    continue   # 이미 지난 날 — 원래 버그 재발 방지를 위해 손대지 않음
                if _ttype == "경기":
                    # [2026-08 버그수정, 신민용 리포트: "경기 일정엔 상대가
                    # 뜨는데 메인 화면 카드는 계속 '?'"] 승강 플레이오프는
                    # 대진이 아직 안 풀린 시점(하위 브래킷 결과 대기 중)에도
                    # get_my_po_match가 "po":True 딕셔너리를 opp="?"로
                    # 채워 반환한다 — 그래서 이 값이 "?"인 채로 그대로
                    # (_d, "경기", mi) 형태로 락돼버리면, 바로 위
                    # "이미 실제 경기로 고정됨" 스킵 때문에 나중에 하위
                    # 브래킷 결과가 나와 실제 상대가 확정돼도 화면은 영원히
                    # 락 당시의 "?"만 보여줬다. PO이면서 상대가 아직 "?"인
                    # 경우만 예외로 라이브 재확인해서 갱신한다 — 다른 모든
                    # "경기" 타입(국제대회/챔스/컵대회 등)은 원래대로 손대지
                    # 않는다.
                    if isinstance(_detail, dict) and _detail.get("po") and _detail.get("opp") == "?":
                        _live = self._get_match_for_day(_d, p, st=st)
                        if _live and _live.get("opp") and _live.get("opp") != "?":
                            self._locked_sched[_i] = (_d, "경기", _live)
                    continue   # 이미 실제 경기로 고정돼 있음 — 그대로 둠
                _live = self._get_match_for_day(_d, p, st=st)
                if _live and _live.get("pending"):
                    # 아직 대진 미확정 — 훈련 스케줄 자체는 건드리지 않고
                    # 표시만 살아있는 정보로 갱신(아래 _match_cache가 사용).
                    self._locked_sched[_i] = (_d, _ttype, {"__pending_overlay__": _live})
                elif _live:
                    # 그사이 대진이 확정됐다 — 이제 진짜 경기이므로 고정본을
                    # 승격시킨다(advance_days도 라이브 조회로 어차피 이렇게
                    # 처리하니, 화면과 실제를 다시 일치시키는 것뿐).
                    self._locked_sched[_i] = (_d, "경기", _live)
                elif isinstance(_detail, dict) and "__pending_overlay__" in _detail:
                    # [2026-07 버그수정, 신민용 리포트: "3/4위전이면 결승전은
                    # 11월 29일처럼 훈련 선택 창으로 바뀌어야 하는데 계속
                    # '결승전 (미정)'으로 뜬다"] 위 두 분기는 '지금 라이브로
                    # 봤을 때 여전히 pending이거나(overlay 갱신) 이미 실제
                    # 경기로 확정된(승격) 경우만 다뤘다 — '한때는 내 경기가
                    # 될 수도 있어서 미정으로 표시됐지만, 그 사이 4강 결과가
                    # 나오면서 반대쪽 대진(결승/3-4위전)으로 확정돼 더 이상
                    # 내 경기가 아니게 된' 경우는 처리하지 않았다. 이땐
                    # get_my_pending_stage(intl/cwc 공통)가 이제 None을
                    # 돌려주므로 _live가 None인데, 예전 새로고침 때 붙여둔
                    # __pending_overlay__가 지워지지 않고 그대로 남아 "미정"
                    # 표시가 굳어버렸다. 더 이상 내 경기가 아님이 확인됐으니
                    # 낡은 오버레이를 지우고 원래 훈련일 상태로 되돌린다.
                    self._locked_sched[_i] = (_d, _ttype, None)

            # [2026-07 추가 버그수정, 신민용 리포트: "결승 진출이 확정됐는데
            # 그 전날(3/4위전 날)이 강제 휴식으로 안 바뀌고 그냥 고강도로
            # 뜬다"] 위 패치는 '오늘 경기가 생겼는지'만 확인했지, '내일
            # 경기가 새로 확정돼서 오늘이 경기 전날 강제휴식이 돼야 하는지'는
            # 다시 안 봤다. 대진이 잠금 시점 이후에 결정되는 경우(4강 결과에
            # 따라 결승/3-4위전 중 하나가 확정)엔 이 관계도 매 새로고침마다
            # 다시 확인해야 한다 — _build_week_sched가 처음 잠글 때 쓰던
            # 것과 동일한 규칙("내일이 진짜 경기면 오늘은 무조건 휴식")을
            # 여기서도 그대로 적용한다.
            _by_day = {x[0]: x for x in self._locked_sched}
            for _i, (_d, _ttype, _detail) in enumerate(self._locked_sched):
                if _d < day or _ttype == "경기":
                    continue
                _next_item = _by_day.get(_d + 1)
                if _next_item and _next_item[1] == "경기" and _ttype != "휴식":
                    self._locked_sched[_i] = (_d, "휴식", None)

            _match_cache = {}
            for item in self._locked_sched:
                if item[1] == "경기":
                    _match_cache[item[0]] = item[2]
                elif isinstance(item[2], dict) and "__pending_overlay__" in item[2]:
                    _match_cache[item[0]] = item[2]["__pending_overlay__"]
                else:
                    _match_cache[item[0]] = None
        else:
            _match_cache = {
                bundle_start + i: self._get_match_for_day(bundle_start + i, p, st=st)
                for i in range(DAY_BUNDLE_SIZE)
            }

        self._update_next_week_preview(bundle_start, p, st=st)
        self._update_next_intl_preview(bundle_start, p)
        self._update_next_po_preview(bundle_start, p)

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
            # [2026-08 버그수정, 신민용 리포트: "11/5~11/11 주에 11/7에
            # 부상당했는데 11/5부터 부상인 것처럼 뜬다"] 예전 조건은
            # (d-day) < 남은일수만 봐서 하한이 없었다 — 오늘(day)보다
            # 과거인 그 주의 앞쪽 날짜(d<day)는 (d-day)가 음수라 항상
            # 조건을 만족해버려서, 실제로 부상당하기 전 날짜까지 전부
            # "부상 중"으로 소급 표시됐다. d가 오늘 이후여야 한다는 하한을
            # 추가한다.
            _inj_days_left = p.get("injury_weeks", 0) if p.get("injured") else 0
            if _inj_days_left > 0 and day <= d < day + _inj_days_left:
                cb.hide()
                _idetail3 = p.get("injury_detail") or "부상"
                _days_left_that_day = _inj_days_left - (d - day)
                hl.setText(f"🚑 {_days_left_that_day}일 남음")
                if ml:
                    ml.setText(f"🚑 부상\n{_idetail3}")
                    # [2026-08 색상 확정, 신민용 최종 승인] 국대 메이저(#ff3333)와
                    # 겹치지 않게 배경을 더 어두운 적갈색으로 분리.
                    ml.setStyleSheet("color:#ff6666;font-weight:bold;font-size:12px;"
                                     "background:#3a2525;border-radius:4px;padding:4px;")
                    ml.show()
                continue

            if match_info:
                cb.hide()
                if match_info.get("pending"):
                    # [2026-07 신설, 신민용 요청: "8강 날짜가 되면 이기기
                    # 전까지는 미정으로 떠야 한다"] 아직 대진이 안 정해진
                    # 미래 라운드(intl_engine.get_my_pending_stage) —
                    # 상대/스트레스 등 실제 경기 정보가 아직 없으므로
                    # 전용 문구만 보여준다.
                    stage = match_info.get("stage_ko", "")
                    hl.setText("")
                    if ml:
                        ml.setText(f"🌍 {match_info['league_name']} {stage}\n(미정)")
                        ml.setStyleSheet("color:#999999;font-weight:bold;font-size:12px;"
                                         "background:#2a2a2a;border-radius:4px;padding:4px;")
                        ml.show()
                elif match_info.get("intl"):
                    # 국가대표 경기 (월드컵/대륙컵/예선/지역컵/유로)
                    # [2026-08 색상 확정, 신민용 최종 승인] 월드컵·대륙
                    # 네이션스컵 본선(kind: world/continent)은 강렬한
                    # 레드, 그 외(예선/지역컵/유로 등)는 핑크로 분리한다
                    # — 예전엔 "그 외"가 전부 부상(#ff6666/#3a1a1a)과
                    # 똑같은 빨강이라 헷갈렸던 문제를 이 색 개편으로 해결.
                    stage = match_info.get("stage_ko", "")
                    grp   = f" {match_info['grp']}조" if match_info.get("grp") else ""
                    opp   = f"{match_info.get('opp_flag','')}{match_info.get('opp','')}"
                    hl.setText(f"스트레스 +{self._match_stress_preview(p, match_info.get('is_home', False))}")
                    if ml:
                        _is_main = match_info.get("kind") in ("world", "continent")
                        _txt_c = "#ff3333" if _is_main else "#ff66b2"
                        _bg_c  = "#3a1a1a" if _is_main else "#3a1a2b"
                        ml.setText(f"🌍 {match_info['league_name']} {stage}{grp}\nvs {opp}")
                        ml.setStyleSheet(f"color:{_txt_c};font-weight:bold;font-size:12px;"
                                         f"background:{_bg_c};border-radius:4px;padding:4px;")
                        ml.show()
                elif match_info.get("cl"):
                    # [2026-08 색상 개편, 신민용 최종 승인: "챔스/유로파/
                    # 컨퍼런스/슈퍼컵이 전부 같은 황금색이라 안 구분된다"]
                    # cl_kind별로 색을 완전히 분리한다 —
                    #   챔피언스: 블루, 유로파: 오렌지,
                    #   컨퍼런스: 리그(초록)와 정반대 톤(짙은 그린 글자 +
                    #   밝은 연두 배경)으로 의도적으로 확 튀게, 슈퍼컵: 골드.
                    stage = match_info.get("stage_ko", "")
                    opp   = f"{match_info.get('opp_flag','')}{match_info.get('opp','')}"
                    loc   = "홈" if match_info.get("is_home") else "원정"
                    hl.setText(f"스트레스 +{self._match_stress_preview(p, match_info.get('is_home', False))}")
                    if ml:
                        _CL_KIND_STYLE = {
                            "champions":  ("#4466ff", "#1a2b3a"),
                            "europa":     ("#ff7700", "#3a1f1a"),
                            "conference": ("#215131", "#b8e6c1"),
                            "super_cup":  ("#ffd700", "#3a321a"),
                        }
                        _txt_c, _bg_c = _CL_KIND_STYLE.get(
                            match_info.get("cl_kind"), ("#ffd24d", "#3a2f1a"))
                        ml.setText(f"🏆 {match_info['league_name']} {stage} ({loc})\nvs {opp}")
                        ml.setStyleSheet(f"color:{_txt_c};font-weight:bold;font-size:12px;"
                                         f"background:{_bg_c};border-radius:4px;padding:4px;")
                        ml.show()
                elif match_info.get("cup"):
                    # [2026-07 신설] 국내 컵대회(FA컵식)
                    rname = match_info.get("round_name", "")
                    opp   = match_info.get("opp", "")
                    _otier = match_info.get("opp_tier")
                    opp_disp = f"{opp} ({_otier}부)" if _otier else opp
                    loc   = "홈" if match_info.get("is_home") else "원정"
                    hl.setText(f"스트레스 +{self._match_stress_preview(p, match_info.get('is_home', False))}")
                    if ml:
                        ml.setText(f"🎖️ {match_info['league_name']} {rname} ({loc})\nvs {opp_disp}")
                        ml.setStyleSheet("color:#c48aff;font-weight:bold;font-size:12px;"
                                         "background:#2a1a3a;border-radius:4px;padding:4px;")
                        ml.show()
                elif match_info.get("cwc"):
                    # [2026-07 신설, 신민용 리포트: "클럽월드컵이 안 뜬다/색이
                    # 이상하다"] 분기 자체가 없어서 일반 리그 경기(초록 ⚽)로
                    # 뭉뚱그려 표시되고 있었다 — 전용 아이콘/색으로 분리.
                    stage = match_info.get("stage_ko", "")
                    opp   = match_info.get("opp", "")
                    opp_country = match_info.get("opp_country", "")
                    opp_disp = f"{opp}({opp_country})" if opp_country else opp
                    loc   = "홈" if match_info.get("is_home") else "원정"
                    hl.setText(f"스트레스 +{self._match_stress_preview(p, match_info.get('is_home', False))}")
                    if ml:
                        ml.setText(f"🌍 클럽 월드컵 {stage} ({loc})\nvs {opp_disp}")
                        # [2026-08 색상 확정, 신민용 최종 승인] 챔피언스가
                        # 블루(#4466ff)로 바뀌면서 헷갈리지 않게 클럽월드컵은
                        # 더 밝은 하늘색으로 확실히 구분한다.
                        ml.setStyleSheet("color:#00bfff;font-weight:bold;font-size:12px;"
                                         "background:#1a2a3a;border-radius:4px;padding:4px;")
                        ml.show()
                elif match_info.get("po"):
                    # [2026-07 신설, 승강 플레이오프] 다른 대회들과 동일한
                    # 패턴 — 색은 "이 경기 결과로 리그가 갈린다"는 긴장감을
                    # 주려고 경고성 노란색 계열로 골랐다(다른 어떤 대회
                    # 색과도 안 겹침: 리그=초록/컵=보라/챔스=황금/CWC=하늘/
                    # 국대=주황·빨강).
                    stage = match_info.get("stage_ko", "")
                    opp   = match_info.get("opp", "")
                    loc   = "홈" if match_info.get("is_home") else "원정"
                    hl.setText(f"스트레스 +{self._match_stress_preview(p, match_info.get('is_home', False))}")
                    if ml:
                        ml.setText(f"⚖ 승강 플레이오프 {stage} ({loc})\nvs {opp}")
                        ml.setStyleSheet("color:#ffee55;font-weight:bold;font-size:12px;"
                                         "background:#3a3315;border-radius:4px;padding:4px;")
                        ml.show()
                else:
                    league_name = match_info.get("league_name", "")
                    loc = "홈" if match_info.get("is_home") else "원정"
                    stress_val = self._match_stress_preview(p, match_info.get("is_home", False))
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
                if not next_mi:
                    # [2026-07 버그수정, 신민용 리포트: "클럽 월드컵 16강이
                    # 11월 19일인데 그 전날(18일)에 파란색 '경기 전 휴식'
                    # 표시가 안 뜬다"] 예전엔 d+1이 캐시에 아예 없을 때만
                    # (사실상 번들 마지막 날 등 예외 상황) 라이브로 다시
                    # 확인했다 — 그런데 하루씩 모드에서 캐시는 번들이
                    # 잠긴 시점 기준이라, R16 같은 조별리그 이후 셸이 그
                    # 뒤에(같은 주 안에서) 확정돼도 이미 지나간 날짜의
                    # 패치 루프는 더는 그 앞날(d)까지 되짚어 갱신하지
                    # 않는다 — 그 결과 d+1에 실제 경기가 생겼는데도 캐시엔
                    # 여전히 None으로 남아 이 미리보기가 낡은 채로 굳어있을
                    # 수 있었다. 캐시가 "경기 없음"(None)이라고 할 때는
                    # 항상 가볍게 한 번 더 살아있는 조회로 재확인한다 —
                    # 실제 경기가 있으면 캐시보다 라이브가 항상 옳고,
                    # 없으면 조회 결과도 그대로 None이라 손해가 없다.
                    next_mi = self._get_match_for_day(d + 1, p, st=st)
                # [2026-07 안전장치] "미정" placeholder(아직 대진 미확정
                # 미래 라운드)는 실제 경기가 아니라 advance_days의 강제
                # 휴식 로직도 이걸 모른다 — 여기서 "내일 경기 있음"으로
                # 취급해 "대회 전 휴식"을 미리 보여주면, 실제로 진행했을 때
                # 적용되는 훈련(사용자가 고른 값)과 화면 미리보기가
                # 어긋난다. pending은 이 미리보기 트리거에서 제외한다.
                if next_mi and next_mi.get("pending"):
                    next_mi = None
                if next_mi:
                    cb.hide()
                    loc_txt = "원정 이동" if next_mi.get("is_home") is False else "경기 하루 전"
                    hl.setText(f"🚌 {loc_txt} (스트레스 -15)")
                    if ml:
                        ml.setText("🛌 대회 전 휴식")
                        # [2026-08 색상 확정, 신민용 최종 승인]
                        ml.setStyleSheet("color:#88bbff;font-weight:bold;font-size:12px;"
                                         "background:#1a243a;border-radius:4px;padding:4px;")
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
        _min_join_age = p.get("min_join_age") or MIN_JOIN_AGE
        age = p.get("age", 16)
        has_team  = bool(p.get("current_team_id"))
        can_join  = is_pre and age >= _min_join_age and not has_team and not self._join_used

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

        # [2026-07 신설] 판매추진 토글 — 꺼도 조건이 매우 심각해지면
        # 구단이 최종 결정을 내릴 수 있다는 걸 항상 툴팁으로 알려준다.
        sale_push_on = bool(p.get("allow_club_sale_push", 1))
        if sale_push_on:
            self.btn_sale_push_toggle.setText("🏟 판매추진 ON")
            self.btn_sale_push_toggle.setToolTip(
                "구단이 이적을 추진할 수 있습니다. 클릭하면 억제합니다.")
        else:
            self.btn_sale_push_toggle.setText("🏟 판매추진 OFF")
            self.btn_sale_push_toggle.setToolTip(
                "구단의 판매 추진을 억제합니다(리스트 등재·오퍼 증가 없음). "
                "단, 매우 심각한 상황(강등+계약임박+불화 등 다수 겹침)에서는 "
                "그래도 구단이 최종 결정을 내릴 수 있습니다.")

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
                _im = intl_engine.get_my_match(week, p=p)
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
            from competition import champions_engine
            from game_engine import get_state as _gs
            _st = _gs()
            cl_m = champions_engine.get_my_cl_match(week, p=p)
            if cl_m:
                return {
                    "cl": True,
                    "cl_kind": "champions",
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
                    from competition.champions_engine import _my_cl_tournament
                    _cp = p  # center_panel의 p
                    _ct = _my_cl_tournament(_cp, _st["current_year"])
                    if _ct and _ct.get("status") != "done":
                        return {
                            "cl": True,
                            "cl_kind": "champions",
                            "tournament_id": _ct["id"],
                            "league_name": _ct["name"],
                            "stage": "group",
                            "stage_ko": "",
                            "grp": cl_gi["grp"],
                            "week": week,
                        }
        except Exception:
            pass
        # 유로파리그급/컨퍼런스리그급 확인 (2026-08 신설, 챔스와 동일 주차)
        try:
            from competition import europa_engine
            el_m = europa_engine.get_my_el_match(week, p=p)
            if el_m:
                return {
                    "cl": True,
                    "cl_kind": "europa",
                    "tournament_id": el_m["tournament_id"],
                    "league_name": el_m.get("league_name", ""),
                    "stage": el_m.get("stage", "league"),
                    "stage_ko": el_m.get("stage_ko", ""),
                    "grp": el_m.get("grp", ""),
                    "week": week,
                }
        except Exception:
            pass
        try:
            from competition import conference_engine
            ecl_m = conference_engine.get_my_ecl_match(week, p=p)
            if ecl_m:
                return {
                    "cl": True,
                    "cl_kind": "conference",
                    "tournament_id": ecl_m["tournament_id"],
                    "league_name": ecl_m.get("league_name", ""),
                    "stage": ecl_m.get("stage", "league"),
                    "stage_ko": ecl_m.get("stage_ko", ""),
                    "grp": ecl_m.get("grp", ""),
                    "week": week,
                }
        except Exception:
            pass
        # [2026-08 신설, 11순위] 슈퍼컵 확인 — 챔스/유로파/컨퍼런스와 같은
        # 방식(공용 get_my_match)이지만, 연 1회·4팀뿐이라 사실상 결승까지
        # 오른 팀만 이 분기를 탄다.
        try:
            from competition import super_cup_engine
            sc_m = super_cup_engine.get_my_super_cup_match(week, p=p)
            if sc_m:
                return {
                    "cl": True,
                    "cl_kind": "super_cup",
                    "tournament_id": sc_m["tournament_id"],
                    "league_name": sc_m.get("league_name", ""),
                    "stage": sc_m.get("stage", "SF"),
                    "stage_ko": sc_m.get("stage_ko", ""),
                    "grp": sc_m.get("grp", ""),
                    "week": week,
                }
        except Exception:
            pass
        # 클럽 월드컵 확인 (43~52주, 4년에 한 번)
        try:
            from competition import club_world_cup_engine
            cwc_m = club_world_cup_engine.get_my_cwc_match(week, p=p)
            if cwc_m:
                return {
                    "cwc": True,
                    "tournament_id": cwc_m["tournament_id"],
                    "league_name": cwc_m.get("league_name", "클럽 월드컵"),
                    "stage": cwc_m.get("stage", "group"),
                    "stage_ko": cwc_m.get("stage_ko", ""),
                    "grp": cwc_m.get("grp", ""),
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
        from competition import champions_engine
        if champions_engine.has_my_cl_match_between(week, week):
            return True
        from competition import europa_engine
        if europa_engine.has_my_el_match_between(week, week):
            return True
        from competition import conference_engine
        if conference_engine.has_my_ecl_match_between(week, week):
            return True
        from competition import super_cup_engine
        if super_cup_engine.has_my_super_cup_match_between(week, week):
            return True
        from competition import cup_engine
        if cup_engine.has_my_cup_match_between(week, week):
            return True
        from competition import club_world_cup_engine
        return club_world_cup_engine.has_my_cwc_match_between(week, week)

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
            if not cb.isVisible():
                # [2026-07 버그수정, 신민용 리포트: "경기일엔 스트레스 +8이
                # 뜨는데 주간 합계는 +0으로 나옴"] 경기일은 훈련 콤보가
                # 아니라서 여기서 다시 계산은 못 하지만(부상/경기 등 종류가
                # 다양함), week_hints[i]에 이미 표시된 값을 그대로 파싱해서
                # 합계에 포함시킨다 — 표시값과 합계가 항상 일치하게.
                import re
                _hint_txt = self.week_hints[i].text()
                if "스트레스" in _hint_txt:   # 부상("N일 남음") 등은 제외
                    m = re.search(r"([+-]?\d+)", _hint_txt)
                    if m:
                        total_stress += int(m.group(1))
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

    _MODE_MENU_STYLE = """
        QMenu {
            background-color:#2a2a2a; color:#eee;
            border:1px solid #444; border-radius:6px; padding:4px;
        }
        QMenu::item {
            background-color:transparent; color:#eee;
            padding:8px 16px; border-radius:4px; margin:1px;
        }
        QMenu::item:selected { background-color:#3a6a3a; color:white; }
        QMenu::item:disabled { color: rgba(238,238,238,70); background-color:transparent; }
        QMenu::separator { height:1px; background:#444; margin:4px 6px; }
    """

    def _show_mode_menu(self):
        """[2026-08 신설] 진행 모드 선택 메뉴 — 하루씩/1주씩/1년 넘기기.
        기존엔 버튼 클릭이 곧바로 하루씩↔1주씩 토글이었는데, "1년 넘기기"
        옵션이 추가되면서 세 개 중 하나를 고르는 메뉴 방식으로 바꿨다.
        "1년 넘기기"는 팀이 없을 때만 활성화된다 — 있으면 기본 회색이 아니라
        앱 전체 다크 테마에 맞춘 반투명한 글자색으로 흐리게 표시된다."""
        if self._step_idx > 0 or self._year_active:
            show_toast(self, "⚠  진행 중인 1주를 끝낸 뒤 전환할 수 있습니다", "#cc6600", 1600)
            return

        p = get_player()
        has_team = bool(p and p.get("current_team_id"))

        menu = QMenu(self)
        menu.setStyleSheet(self._MODE_MENU_STYLE)
        act_day  = QAction("📆 하루씩", self)
        act_week = QAction("📅 1주씩", self)
        act_year = QAction("🗓 1년 넘기기 (팀 없을 때만)", self)
        act_year.setEnabled(not has_team)
        menu.addAction(act_day)
        menu.addAction(act_week)
        menu.addSeparator()
        menu.addAction(act_year)

        act_day.triggered.connect(self._set_mode_day)
        act_week.triggered.connect(self._set_mode_week)
        act_year.triggered.connect(self._set_mode_year)

        # [2026-08] 커서 위치가 아니라 버튼 바로 아래에 뜨도록 — 다른 앱
        # 메뉴/콤보박스와 동일한 위치 관례를 따른다(임의 커서 위치보다
        # 예측 가능함).
        menu.exec(self.btn_mode.mapToGlobal(self.btn_mode.rect().bottomLeft()))

    def _set_mode_day(self):
        """하루씩 보기로 전환. 대기 중인 1년 넘기기 재개 상태는 버린다."""
        self._year_mode        = False
        self._year_paused      = False
        self._year_pattern     = None
        self._year_weeks_done  = 0
        self._year_weeks_total = 0
        self._step_mode = True
        self.btn_mode.setText("📆 하루씩")
        self.adv_btn.setText("▶▶  이번 주 진행")
        show_toast(self, "🔍 하루씩 보기  —  1주 일정대로 하루씩 진행", "#664400", 1500)
        self._save_ui_state()
        self.refresh()

    def _set_mode_week(self):
        """1주씩 보기로 전환. 대기 중인 1년 넘기기 재개 상태는 버린다."""
        self._year_mode        = False
        self._year_paused      = False
        self._year_pattern     = None
        self._year_weeks_done  = 0
        self._year_weeks_total = 0
        self._step_mode    = False
        self._locked_sched = None
        self._step_idx     = 0
        self.btn_mode.setText("📅 1주씩")
        self.adv_btn.setText("▶▶  이번 주 진행")
        show_toast(self, "📅 1주씩 보기  —  한 주씩 진행", "#006622", 1400)
        self._save_ui_state()
        self.refresh()

    def _set_mode_year(self):
        """[2026-08 신설] 1년 넘기기 모드 선택. 지금 화면에 짜여 있는 7일
        패턴을 그대로 52번(=364일=1년) 반복할 준비만 여기서 하고, 실제
        진행은 여전히 진행 버튼(adv_btn)을 눌러야 시작된다 — 메뉴에서
        고르자마자 바로 진행되면 실수로 1년을 통째로 날릴 수 있어서다.

        [2026-08 재개 기능 추가] 국가대표 선택 등으로 중단됐던 진행이
        있으면(_year_paused) 그 진행 상황(_year_weeks_done/_year_pattern)을
        그대로 두고 "이어하기" 라벨만 보여준다 — 처음부터 다시 시작하지
        않는다."""
        p = get_player()
        if p and p.get("current_team_id"):
            show_toast(self, "⚠  팀이 있으면 1년 넘기기를 쓸 수 없습니다", "#cc6600", 1600)
            return
        self._year_mode    = True
        self._step_mode    = False
        self._locked_sched = None
        self._step_idx     = 0
        self.btn_mode.setText("🗓 1년")
        if self._year_paused and self._year_weeks_total:
            self.adv_btn.setText(
                f"▶▶  1년 넘기기 이어하기 ({self._year_weeks_done}/{self._year_weeks_total}주)")
            show_toast(self, f"🗓 1년 넘기기 이어서 진행합니다 "
                             f"({self._year_weeks_done}/{self._year_weeks_total}주 완료)",
                       "#664400", 2200)
        else:
            self.adv_btn.setText("▶▶  1년 넘기기 (지금 일정 반복)")
            show_toast(self, "🗓 1년 넘기기 모드  —  지금 짜둔 일정이 52주 동안 반복됩니다",
                       "#664400", 2200)
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
        if self._year_mode:
            self._advance_year_start()
            return
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
        _min_join_age = p.get("min_join_age") or MIN_JOIN_AGE
        _ps_s, _ps_e = SEASON_PHASES["preseason1"]

        # [2026-08 변경, 신민용 요청: "팀 없을 때는 진행 못 하는데 이때도
        # 진행할 수 있게 해줘"] 예전엔 여기서 17살 이상+팀 없음+프리시즌
        # (1~3주)이면 진행 자체를 강제로 막았다 — "1년 넘기기" 기능을 만들며
        # 팀 없이도 시간을 보낼 수 있게 하는 방향으로 정책이 바뀌었으므로,
        # 1주씩/하루씩 모드에서도 똑같이 막을 이유가 없다. 완전히 막는 대신
        # 입단을 잊지 않도록 알림만 띄우고 그대로 진행시킨다(버튼/오퍼는
        # 여전히 정상적으로 뜬다).
        if (p["age"] >= _min_join_age and not p.get("current_team_id")
                and _ps_s <= week <= _ps_e and not getattr(self, "_skip_join_lock", False)
                and self._join_reminder_shown_week != week):
            self._join_reminder_shown_week = week
            show_toast(self, "💡 아직 팀이 없습니다 — 입단하지 않고 계속 진행합니다", "#664400", 1800)

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
                mi = self._get_match_for_day(d, p, st=st)
                # [2026-07 안전장치] "미정"(get_my_pending_stage) placeholder는
                # 화면 표시 전용이다 — 실제 상대/스탯이 없는데 이걸 "경기"로
                # 스케줄에 넣으면 advance_days의 매치 시뮬 경로가 받는 mi에
                # opp 등 필수 필드가 없어 오작동/크래시할 수 있다. 진짜 대진이
                # 잡히기 전까지는 평범한 훈련일로 취급한다.
                if mi and mi.get("pending"):
                    mi = None
                if mi:
                    sched.append((d, "경기", mi))
                else:
                    # [2026-07 확장] 경기 하루 전엔(홈/원정 무관) 이동/컨디션
                    # 관리 목적으로 무조건 휴식을 강제한다(실제 프로팀 루틴과
                    # 동일, 이틀 연속 경기 방지) — 사용자가 그날 다른 훈련을
                    # 골라놨어도 경기 전날이면 덮어쓴다.
                    next_mi = self._get_match_for_day(d + 1, p, st=st)
                    if next_mi and next_mi.get("pending"):
                        next_mi = None
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
                    mi = self._get_match_for_day(d, p, st=st)
                    if mi and mi.get("pending"):
                        mi = None
                    if mi:
                        sched.append((d, "경기", mi))
                    else:
                        # 경기 전날 휴식 강제(홈/원정 무관) — 위 _build_week_sched 주석 참고.
                        next_mi = self._get_match_for_day(d + 1, p, st=st)
                        if next_mi and next_mi.get("pending"):
                            next_mi = None
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
        # [2026-07 버그수정, 신민용 리포트: "QThread: Destroyed while thread
        # still running" 콘솔 경고 + 진행 스피너가 안 사라지고 멈춤]
        # 원인: _on_advance_finished 안에서 시즌전환 직후 국적 강제확정 체크
        # 시 self.main_win.refresh_all() + QApplication.processEvents()를
        # 수동 호출하는데, 이 재진입성 이벤트 처리 도중 self._advance_worker
        # 파이썬 참조가 아직 완전히 안 끝난(Qt 내부적으로 isRunning()이 아직
        # True인) 스레드 객체를 가리키는 채로 재사용되면서, 파이썬 GC가
        # 이 객체를 너무 일찍 정리해버릴 수 있었다. Qt의 자체 스레드 수명
        # 관리(finished 시그널→deleteLater)에 맡기면, 파이썬 쪽 참조 타이밍과
        # 무관하게 Qt가 안전한 시점에 알아서 정리해준다.
        self._advance_worker.finished.connect(self._advance_worker.deleteLater)
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
        if _pss <= new_week <= _pse and p2.get("age",0) >= (p2.get("min_join_age") or MIN_JOIN_AGE) and not p2.get("current_team_id"):
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

        # [2026-08 최적화, 신민용 리포트: "하루씩 진행이 1주씩보다 더 렉걸린다"]
        #   예전엔 여기서 무조건 refresh_all()을 불렀다 — 1주씩 모드는 버튼
        #   1클릭=1주 진행이라 딱 1번만 무거운 갱신(일정 창/순위표 창 재계산)이
        #   발생하는데, 하루씩 모드는 같은 1주를 진행해도 버튼을 7번 나눠 눌러
        #   그 무거운 갱신을 7번 반복하고 있었다([PERF-STAND] 로그 기준 순위표
        #   창이 열려있으면 회당 0.1~0.5s+). 실제 시뮬레이션(advance_days)
        #   비용은 동일한데 이 화면 갱신 오버헤드만 7배로 쌓였던 것.
        #   묶음(7일=1주) 도중(bundle_done=False)엔 날짜/선수패널/로그만
        #   갱신하는 refresh_light()로 충분하고, 묶음이 실제로 끝난 날에만
        #   기존처럼 refresh_all()(일정 창·순위표 창까지 포함)을 부른다 —
        #   1주씩 모드는 항상 bundle_done=True라 동작이 기존과 완전히 동일.
        if self.main_win:
            if bundle_done:
                self.main_win.refresh_all()
            else:
                self.main_win.refresh_light()

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

    # ── 1년 넘기기 ─────────────────────────────────
    # [2026-08 신설, 신민용 요청: "팀 없을 때는 1주씩 진행 못 하는데, 이때
    # 1주 일정을 정해두고 1년(52주) 자동 반복하는 버튼을 만들어줘"]
    #
    # 설계:
    #   - 팀이 없을 때만 켤 수 있다(_set_mode_year에서 이미 막지만, 실제
    #     시작 시점에도 한 번 더 확인한다 — 메뉴를 연 뒤 팀이 생겼을 수도
    #     있으므로).
    #   - 팀 입단 신청은 원래처럼 "1주차 일정 정하는 창"(=지금 이 화면,
    #     프리시즌 진입 시점)에서만 가능하고, 그 주가 지나면(=1년 넘기기를
    #     시작하면) 내년 1주가 될 때까지 입단 강제/자동 오퍼가 뜨지 않는다
    #     — 기존에 "모든 오퍼 결렬 시 1년 훈련" 케이스에 쓰던 _skip_join_lock
    #     플래그를 그대로 재사용한다(이 플래그는 이미 다음 시즌 프리시즌
    #     진입 시 자동으로 풀리도록 구현돼 있다).
    #   - 52주 전체를 advance_days() 한 번에 통째로 던지면 그동안 화면이
    #     그냥 멈춘 것처럼 보인다(체감 렉) — 대신 1주(7일)씩 나눠 기존
    #     _AdvanceWorker를 52번 이어서 돌리고, 매 주가 끝날 때마다
    #     진행률 바를 갱신한다. DB 접근은 항상 워커 스레드 하나만 하도록
    #     보장되는 기존 스레드 안전 규칙을 그대로 유지한다(워커가 끝나야
    #     다음 워커를 시작 — 동시에 두 개를 돌리지 않는다).
    #   - [2026-08 변경, 신민용 요청] 대표팀 발탁(call-up) 선택은 더 이상
    #     루프를 멈추지 않는다 — 1년 넘기기 동안은 전부 자동 거절하고
    #     계속 진행한다(headless_runner.py의 자동 테스트 루프와 동일한
    #     방식). 단, 복수국적 22세 영구 국적 확정(get_forced_commit)은
    #     되돌릴 수 없는 정체성 선택이라 자동으로 대신 고를 수 없으므로
    #     여전히 루프를 멈추고 사용자에게 맡긴다 — 처리 후 진행 버튼을
    #     다시 누르면 남은 주부터 이어갈 수 있다(처음부터 다시 시작 아님).

    def _auto_decline_all_pending(self, intl_engine):
        """[2026-08 신설] 대기 중인 대표팀 발탁 선택(get_pending_choice)이
        있으면 전부 자동 거절한다. headless_runner.py가 무인 테스트를 돌릴
        때 쓰는 것과 완전히 동일한 방식(intl_engine.decline_national_team)
        이라, 커리어 기록에도 정상적으로 '발탁 거절'로 남는다(트로피 로그
        중복 방지 로직까지 그대로 재사용됨 — decline_national_team 참고).
        같은 해에 선택 대기가 여러 건 겹쳐 있을 수 있어 while로 다 빌 때까지
        반복한다(한 번에 최대 3개까지만 반환되는 get_pending_choice 특성상)."""
        for _ in range(10):   # 안전 상한 — 정상적으로는 1~2회면 끝남
            pend = intl_engine.get_pending_choice()
            if not pend:
                break
            for opt in pend.get("options", []):
                intl_engine.decline_national_team(opt["tournament_id"])

    def _build_pattern_week_sched(self, day, p, st, pattern):
        """_build_week_sched와 동일한 규칙(경기 있는 날은 자동으로 "경기",
        경기 전날은 강제 휴식)으로 7일 일정을 만들되, 콤보박스를 실시간으로
        읽는 대신 고정된 pattern(7개 문자열)을 반복 소스로 쓴다 — 1년
        넘기기 동안 매주 똑같은 패턴을 재사용하기 위함."""
        sched = []
        for i in range(DAY_BUNDLE_SIZE):
            d     = day + i
            sel   = pattern[i % len(pattern)] if pattern else "휴식"
            ttype = TRAIN_MAP_KO.get(sel, "중강도")
            mi = self._get_match_for_day(d, p, st=st)
            if mi and mi.get("pending"):
                mi = None
            if mi:
                sched.append((d, "경기", mi))
            else:
                next_mi = self._get_match_for_day(d + 1, p, st=st)
                if next_mi and next_mi.get("pending"):
                    next_mi = None
                if next_mi:
                    ttype = "휴식"
                sched.append((d, ttype, None))
        return sched

    def _advance_year_start(self):
        from PyQt6.QtWidgets import QApplication
        p  = get_player()
        st = get_state()
        if not p or not st:
            return
        if p.get("current_team_id"):
            # 메뉴를 연 뒤~진행 버튼을 누른 사이 팀이 생긴 경우(입단 등) —
            # 1년 넘기기를 취소하고 평소 1주씩 모드로 되돌린다.
            self._finalize_year_mode(refresh=False)
            self._step_mode    = False
            self._locked_sched = None
            self._step_idx     = 0
            self._save_ui_state()
            show_toast(self, "⚠  팀이 생겨 1년 넘기기를 취소했습니다", "#cc6600", 1800)
            return

        # 강제확정(복수국적 22세 영구 선택)은 되돌릴 수 없는 정체성 선택이라
        # 자동으로 대신 골라줄 수 없다 — 이건 그대로 멈추고 사용자에게 맡긴다.
        # (재개든 새 시작이든 매번 다시 확인 — 그 사이 새로 떴을 수 있음)
        import intl_engine
        forced = intl_engine.get_forced_commit()
        if forced:
            self._show_forced_commit(forced)
            return

        # [2026-08 변경, 신민용 요청: "1년 돌리면 대표팀 발탁도 다 자동으로
        # 거부"] 예전엔 여기서 대기 중인 발탁 선택(get_pending_choice)이
        # 있으면 멈추고 선택창을 띄웠다 — 이제는 1년 넘기기 동안엔 국가대표
        # 발탁 자체를 전부 자동 거절하고 계속 진행한다. headless_runner.py의
        # 자동 테스트 루프와 동일한 방식(decline_national_team)을 써서,
        # 커리어 기록에도 정상적으로 "발탁 거절"로 남는다.
        self._auto_decline_all_pending(intl_engine)

        if self._year_paused and self._year_weeks_total:
            # ── 재개: 남아있던 패턴/진행률을 그대로 이어서 쓴다 ──
            self._year_paused = False
        else:
            # ── 새 시작: 지금 화면에 짜여 있는 7일 패턴을 반복 소스로 고정 ──
            try:
                self._year_pattern = [cb.currentText() for cb in self.week_combos]
            except Exception:
                self._year_pattern = list(TRAIN_DEFAULTS)
            self._year_weeks_total = 52
            self._year_weeks_done  = 0

        # 올해 남은 기간 동안 입단 강제/자동 오퍼를 막는다 — "모든 오퍼
        # 결렬 시 1년 훈련"과 동일한 플래그라, 다음 시즌 프리시즌 진입
        # 시 자동으로 풀린다(_on_year_step_finished 참고).
        self._skip_join_lock = True
        self._join_used = True
        self.btn_join.setEnabled(False)

        self._year_active = True

        self.adv_btn.setEnabled(False)
        self.btn_mode.setEnabled(False)
        if self.main_win:
            self.main_win.setEnabled(False)
        self._toggle_popup_timers(pause=True)

        target = self.main_win if self.main_win else self
        if self._proc_overlay is None or self._proc_overlay.parent() is not target:
            self._proc_overlay = _ProcessingOverlay(target)
        self._proc_overlay.show_progress("🗓 1년 진행 중...", self._year_weeks_done, self._year_weeks_total)

        self._advance_year_step()

    def _advance_year_step(self):
        """1년 넘기기 중 한 주(7일)를 진행하는 워커를 시작한다. 이전 워커가
        끝난 뒤에만 다음 워커를 시작하므로(체이닝) 동시에 두 스레드가
        DB를 건드릴 일은 없다."""
        p  = get_player()
        st = get_state()
        if not p or not st:
            self._finalize_year_mode(refresh=True)
            return
        from constants import DAYS_PER_WEEK
        day = st.get("current_day") or ((st["current_week"] - 1) * DAYS_PER_WEEK + 1)
        schedule = self._build_pattern_week_sched(day, p, st, self._year_pattern)

        self._advance_worker = _AdvanceWorker(schedule, self)
        self._advance_worker.finished_ok.connect(self._on_year_step_finished)
        self._advance_worker.failed.connect(self._on_year_step_failed)
        self._advance_worker.finished.connect(self._advance_worker.deleteLater)
        self._advance_worker.start()

    def _on_year_step_finished(self):
        from PyQt6.QtWidgets import QApplication
        self._year_weeks_done += 1
        if self._proc_overlay is not None:
            self._proc_overlay.show_progress("🗓 1년 진행 중...", self._year_weeks_done, self._year_weeks_total)

        # ── 중단 조건 확인 ──
        p2 = get_player()
        if not p2:
            self._finalize_year_mode(refresh=True)
            return
        if p2.get("current_team_id"):
            # 정상적으로는 _skip_join_lock 때문에 일어나지 않지만, 방어적으로.
            self._finalize_year_mode(refresh=True)
            show_toast(self, "⚽ 팀에 입단해서 1년 넘기기를 마쳤습니다", "#006622", 2000)
            return

        # [2026-08 버그수정, 신민용 리포트: "22살 1주차에 국적 선택 후 1년
        # 넘기기를 이어하면 딱 1주만 더 진행되고(2주차) 바로 완료 처리돼버림"]
        # 원인: 이 시점(52주째 진행이 방금 끝나 새해 1주차로 넘어온 바로 그
        # 순간)에 "52주 완료"와 "22세 국적 강제확정 필요"가 동시에 걸리면,
        # 예전엔 아래 forced_commit 분기가 완료 판정보다 먼저 실행돼서
        # _year_weeks_done=52(이미 총량 도달)인 채로 "일시정지"됐다. 그러면
        # 사용자가 국적을 고르고 "이어하기"를 눌렀을 때 실제로는 이미 끝난
        # 해인데 한 주를 더 실행해버리고(그래서 1주차→2주차로만 넘어감),
        # 그다음에야 53>=52로 완료 판정이 떨어져 즉시 "1년 넘기기 완료!"로
        # 종료됐다 — 사용자 입장에선 "1주만 진행되고 멈춘 것"처럼 보였다.
        # 완료 판정(weeks_done>=weeks_total)을 forced_commit 판정보다 먼저
        # 검사하도록 순서를 바꾼다 — 이번 해는 정상적으로 딱 52주에서 완료
        # 처리하고, 국적 확정은 그 직후(다음 해 몫으로) 별도로 띄운다.
        import intl_engine
        self._auto_decline_all_pending(intl_engine)

        # 새 시즌 프리시즌으로 들어왔으면(정상적으로는 52주를 다 돌았을
        # 때 딱 여기 도달) 입단 잠금을 풀어준다 — 이 리셋을 안 하면 1년
        # 넘기기 후에도 입단 버튼이 계속 잠긴 채로 남는다
        # (_on_advance_finished의 동일 로직 참고).
        from constants import SEASON_PHASES as _SP_Y, MIN_JOIN_AGE as _MJA_Y
        _min_join_age_y = p2.get("min_join_age") or _MJA_Y
        _pss_y, _pse_y = _SP_Y["preseason1"]
        st2 = get_state()
        new_week = st2.get("current_week", 0) if st2 else 0
        if (_pss_y <= new_week <= _pse_y and p2.get("age", 0) >= _min_join_age_y
                and not p2.get("current_team_id")):
            self._skip_join_lock = False
            self._join_used = False
            self.btn_join.setEnabled(True)
            show_toast(self, f"⭐ {st2.get('current_year')}년 새 시즌!  팀 입단 기간입니다",
                       "#006622", 2000)

        if self._year_weeks_done >= self._year_weeks_total:
            self._finalize_year_mode(refresh=True, revert_mode=False)
            show_toast(self, "🗓 1년 넘기기 완료!", "#006622", 2000)
            # 이번 해는 정상적으로 완료 처리했으니, 새해 국적 강제확정이
            # 걸려 있으면 (완료 토스트와는 별개로) 바로 이어서 띄운다.
            forced = intl_engine.get_forced_commit()
            if forced:
                self._show_forced_commit(forced)
            return

        forced = intl_engine.get_forced_commit()
        if forced:
            # [2026-08] 진행률(_year_weeks_done/_year_pattern)은 보존하고
            # "일시정지"만 한다 — 처리 후 진행 버튼을 다시 누르면 남은
            # 주부터 이어간다(처음부터 다시 시작하지 않는다).
            self._pause_year_mode(refresh=True)
            show_toast(self, "⚠  대표팀 국적을 정해야 해서 1년 넘기기를 일시 중단했습니다.\n"
                             f"({self._year_weeks_done}/{self._year_weeks_total}주 완료 — "
                             "처리 후 진행 버튼을 다시 누르면 이어집니다)",
                       "#cc6600", 3000)
            self._show_forced_commit(forced)
            return

        # 계속 다음 주로.
        QApplication.processEvents()
        self._advance_year_step()

    def _on_year_step_failed(self, msg):
        # [2026-08] 오류가 나도 그때까지의 진행률은 보존한다 — 원인을
        # 해결한 뒤(세이브 백업 등) 진행 버튼을 다시 누르면 실패했던 그
        # 주부터 다시 시도할 수 있다(완전히 처음으로 되돌리지 않는다).
        self._pause_year_mode(refresh=False)
        QMessageBox.critical(
            self, "진행 중 오류",
            f"1년 넘기기 진행 중 오류가 발생했습니다 "
            f"({self._year_weeks_done}/{self._year_weeks_total}주까지 완료):\n{msg}\n\n"
            "진행 버튼을 다시 누르면 이어서 시도합니다.")

    def _pause_year_mode(self, refresh: bool):
        """[2026-08 신설] 완료가 아니라 '일시 중단'만 한다 — _year_mode/
        _year_weeks_done/_year_pattern은 그대로 유지해서 나중에 진행
        버튼을 다시 누르면 _advance_year_start가 재개 경로를 타게 한다.
        UI만 평소대로(버튼 활성화, 오버레이 숨김) 되돌린다."""
        from PyQt6.QtWidgets import QApplication
        QApplication.restoreOverrideCursor()
        if self._proc_overlay is not None:
            self._hide_processing_overlay()
        self._year_active = False
        self._year_paused = True
        self.adv_btn.setText(
            f"▶▶  1년 넘기기 이어하기 ({self._year_weeks_done}/{self._year_weeks_total}주)")
        self.adv_btn.setEnabled(True)
        self.btn_mode.setEnabled(True)
        if self.main_win:
            self.main_win.setEnabled(True)
        self._toggle_popup_timers(pause=False)
        if refresh and self.main_win:
            self.main_win.refresh_all()

    def _finalize_year_mode(self, refresh: bool, revert_mode: bool = True):
        """[2026-08] 진짜로 끝난 경우(52주 완료) 또는 더 이상 이어갈 이유가
        없는 경우(팀이 생김 등)에만 호출 — _pause_year_mode와 달리 진행
        상태(주차 카운트/패턴)는 항상 초기화한다.

        [2026-08 수정, 신민용 지적: "1주일이 기본인 건 처음 켰을 때뿐이고,
        내가 1일/1년을 고르면 그 후엔 진행해도 다시 1주일로 안 바뀌어야
        한다"] revert_mode=True(기본값)일 때만 모드 선택 자체를 1주씩으로
        되돌린다 — 팀이 생겨서 더 이상 1년 넘기기를 쓸 수 없게 된 경우처럼
        정말 강제로 되돌려야 할 때만 그렇게 하고, 52주를 다 채워 정상
        완료된 경우(revert_mode=False로 호출)엔 "🗓 1년" 선택 상태 자체는
        그대로 유지한다 — 진행 버튼을 또 누르면 바로 다음 1년을 새로
        시작할 수 있다."""
        from PyQt6.QtWidgets import QApplication
        QApplication.restoreOverrideCursor()
        if self._proc_overlay is not None:
            self._hide_processing_overlay()
        self._year_active      = False
        self._year_paused      = False
        self._year_pattern     = None
        self._year_weeks_done  = 0
        self._year_weeks_total = 0
        if revert_mode:
            self._year_mode = False
            self.btn_mode.setText("📅 1주씩")
            self.adv_btn.setText("▶▶  이번 주 진행")
        else:
            # 모드 유지 — "1년 넘기기" 선택 상태 그대로, 바로 다음 1년을
            # 다시 시작할 수 있는 문구로 되돌린다(진행률 문구만 초기화).
            self.adv_btn.setText("▶▶  1년 넘기기 (지금 일정 반복)")
        self.adv_btn.setEnabled(True)
        self.btn_mode.setEnabled(True)
        if self.main_win:
            self.main_win.setEnabled(True)
        self._toggle_popup_timers(pause=False)
        if refresh and self.main_win:
            self.main_win.refresh_all()


    def _update_next_intl_preview(self, bundle_start, p):
        """[2026-07 신설, 신민용 요청: "8강(예정)/4강(예정)처럼 아직 상대가
        안 정해진 다음 라운드도 메인 화면에 미리 보이면 좋겠다"] 국제대회
        토너먼트(월드컵/대륙컵)는 이제 대회 시작 시점에 라운드 전체의
        day가 이미 확정돼 있다(intl_engine._precreate_ko_shell 참고) —
        아직 그 주가 안 왔어도 "다음 라운드가 언제인지"는 미리 알 수
        있다. 지금 보이는 7일 안에 이미 그 경기가 있으면(오늘 뛰는 중)
        중복 표시하지 않고, 그보다 뒤에 있는 라운드만 미리 보여준다.
        내 국가가 이미 탈락했으면(alive=0) 표시하지 않는다."""
        if not hasattr(self, "lbl_next_intl"):
            return
        self.lbl_next_intl.hide()
        try:
            import intl_engine
            from game_engine import get_state
            from database import get_conn
            from constants import day_to_date_str
            st = get_state()
            if not st:
                return
            t = intl_engine.get_my_tournament(st["current_year"])
            if not t or t.get("kind") not in ("world", "continent") or t.get("my_selected") != 1:
                return
            if t.get("status") not in ("group", "ko"):
                return
            conn = get_conn()
            nat = intl_engine._my_nat(t, p)
            if not nat:
                conn.close()
                return
            alive_row = conn.execute(
                "SELECT alive FROM intl_entries WHERE tournament_id=? AND country=?",
                (t["id"], nat)).fetchone()
            if alive_row and alive_row["alive"] == 0:
                conn.close()
                return
            # 아직 안 지난(지금 보이는 7일 범위보다 뒤인) 가장 가까운 미래
            # day를 먼저 찾는다. 조별리그는 제외(그건 이미 매주 정상 표시됨).
            window_end = bundle_start + DAY_BUNDLE_SIZE - 1
            day_row = conn.execute(
                """SELECT MIN(day) AS d FROM intl_matches
                   WHERE tournament_id=? AND stage!='group' AND day IS NOT NULL
                     AND day > ?""",
                (t["id"], window_end)).fetchone()
            if not day_row or day_row["d"] is None:
                conn.close()
                return
            next_day = day_row["d"]
            # 그 day에 속한 행들 중 내 국가가 이미 배정된 행이 있으면 그걸
            # 쓰고(상대까지 확정), 없으면(아직 미배정) 아무 행이나 하나
            # 골라 라운드 이름/날짜만 보여준다(같은 라운드는 전부 같은
            # day라 어느 행이든 정보는 동일).
            rows = [dict(r) for r in conn.execute(
                "SELECT stage, home, away FROM intl_matches WHERE tournament_id=? AND day=?",
                (t["id"], next_day)).fetchall()]
            conn.close()
            if not rows:
                return
            mine = next((r for r in rows if r["home"] == nat or r["away"] == nat), None)
            ref = mine or rows[0]
            stage_ko = intl_engine.STAGE_KO.get(ref["stage"], ref["stage"])
            date_str = day_to_date_str(next_day)
            if mine:
                opp = mine["away"] if mine["home"] == nat else mine["home"]
                txt = f"🌍 다음 일정: {t['name']} {stage_ko} ({date_str} 예정) vs {opp}"
            else:
                txt = f"🌍 다음 일정: {t['name']} {stage_ko} ({date_str} 예정) · 상대 미정"
            self.lbl_next_intl.setText(txt)
            self.lbl_next_intl.show()
        except Exception:
            # 미리보기는 부가 정보라 실패해도 메인 화면 동작에 영향 주면 안 됨.
            self.lbl_next_intl.hide()

    def _update_next_po_preview(self, bundle_start, p):
        """[2026-07 신설, 신민용 리포트: "이것도 월드컵처럼 다음 일정
        표시가 있어야 하는거 아냐?"] 위 _update_next_intl_preview와
        완전히 같은 패턴 — 승강 플레이오프도 44주 전체에 걸쳐 매치의
        day가 이미 확정돼 있으므로, 지금 보이는 7일 범위보다 뒤에 있는
        내 다음 경기를 미리 보여준다.

        [탈락 처리, 신민용 확인: "떨어지면 미리보기 표시가 사라져야
        한다"] intl은 alive 플래그로 명시적으로 판정하지만, PO는 그럴
        필요가 없다 — 브래킷 다음 라운드 매치는 "이전 매치 승자"로만
        채워지는 구조라(promotion_playoff.py의 winner 참조 방식), 내가
        졌으면 애초에 나를 참조하는 미래 매치 자체가 안 생긴다. 그래서
        "내 팀이 낀 미해결 매치가 남아있는가"만 확인하면 탈락 여부가
        자동으로 반영된다."""
        if not hasattr(self, "lbl_next_po"):
            return
        self.lbl_next_po.hide()
        try:
            import promotion_playoff_engine as ppe
            from game_engine import get_state
            from database import get_conn
            from constants import day_to_date_str
            st = get_state()
            if not st or not p or not p.get("current_team_id"):
                return
            tid = p["current_team_id"]
            t = ppe.get_my_po_tournament(tid, st["current_year"])
            if not t or t.get("status") != "pending":
                return   # PO가 아예 없거나 이미 끝남(승패 무관 더 보여줄 게 없음)
            window_end = bundle_start + DAY_BUNDLE_SIZE - 1
            conn = get_conn()
            row = conn.execute(
                """SELECT * FROM po_matches WHERE tournament_id=? AND home_score=-1
                   AND (home_team_id=? OR away_team_id=?) AND day>?
                   ORDER BY day LIMIT 1""",
                (t["id"], tid, tid, window_end)).fetchone()
            if not row:
                conn.close()
                return
            opp_id = row["away_team_id"] if row["home_team_id"] == tid else row["home_team_id"]
            opp_row = conn.execute("SELECT name FROM teams WHERE id=?", (opp_id,)).fetchone() \
                if opp_id else None
            conn.close()
            date_str = day_to_date_str(row["day"])
            if opp_row:
                txt = f"⚖ 다음 일정: 승강 플레이오프 ({date_str} 예정) vs {opp_row['name']}"
            else:
                txt = f"⚖ 다음 일정: 승강 플레이오프 ({date_str} 예정) · 상대 미정"
            self.lbl_next_po.setText(txt)
            self.lbl_next_po.show()
        except Exception:
            self.lbl_next_po.hide()

    def _update_next_week_preview(self, bundle_start, p, st=None):
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
            mi = self._get_match_for_day(d, p, st=st)
            if not mi:
                color = "#333"
            elif mi.get("intl"):
                color = "#ffaa33" if mi.get("kind") in ("world", "continent") else "#ff6666"
            elif mi.get("cl"):
                color = "#ffd24d"
            elif mi.get("cup"):
                color = "#c48aff"
            elif mi.get("po"):
                color = "#ffee55"
            else:
                color = "#66ff99"
            box.setStyleSheet(f"background:{color};border-radius:3px;")

    def _get_match_for_day(self, day, p, st=None):
        """그 날짜(day)에 내 경기가 있는지 확인.
        클럽 리그 경기는 match_results.day로 정확한 날짜가 있어 그대로 대조.
        국제대회/챔스는 day 컬럼이 없어(주 단위 대회) game_engine의
        _week_intl_cl_day()가 정한 '그 주의 정확한 날'에 배정된 것으로
        취급한다 — advance_days의 실제 처리 시점과 반드시 같은 함수를
        써서 화면 표시와 실제 진행이 어긋나지 않게 한다(예전엔 화면은
        '주 마지막 날'로 보여주면서 실제 처리는 그 주 아무 날에나
        조용히 일어나던 불일치가 있었다).

        [2026-07 최적화, 신민용 리포트: "일 단위 전환 후 전체적으로 렉"]
        p와 마찬가지로 st(게임 상태)도 호출부가 이미 조회해둔 게 있으면
        넘겨서 get_state() 재조회를 생략한다 — 하루 셀 하나당 이 함수가
        내부에서 부르는 get_my_match/get_my_cl_match 등이 전부 각자
        get_state()를 다시 했었다."""
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
                if st is None:
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

        # [2026-07 재수정] intl_matches(예선/본선)와 cwc_matches는 이제 둘 다
        # Phase 2로 실제 day가 채워져 있어서 day로 정확히 조회해도 되는데,
        # cl/cup_matches는 아직 day가 전부 0(스키마 기본값)이다 — day=0을
        # "미설정"으로 봐주는 폴백 때문에, 이번 주 어느 요일에 물어봐도
        # 전부 조건이 참이 되어 같은 미완료 경기가 그 주 내내(실제 처리되기
        # 전까지) 반복 표시됐다. 그래서 day가 진짜 있는 intl/cwc만 day로
        # 직접 조회하고, 아직 day가 없는 챔스/컵은 예전 방식대로
        # _week_intl_cl_day가 정한 '그 주의 딱 하루'에만 확인한다.
        import intl_engine
        im = intl_engine.get_my_match(week, day=day, p=p, st=st)
        if im:
            return im

        # [2026-07 신설, 신민용 요청: "8강 날짜가 되면 이기기 전까지는
        # 메인 화면에 미정으로 떠야 한다"] 실제 대진이 아직 안 정해진
        # 미래 라운드(_precreate_ko_shell placeholder)라도, 오늘이 바로
        # 그 라운드 예정일이면 "미정" 상태로라도 보여준다.
        pend = intl_engine.get_my_pending_stage(week, day=day, p=p, st=st)
        if pend:
            return pend

        from competition import club_world_cup_engine
        cw = club_world_cup_engine.get_my_cwc_match(week, day=day, p=p, st=st)
        if cw:
            return cw

        # [2026-07 신설] CWC도 intl과 동일한 사전생성 셸 구조라 똑같이
        # "미정" placeholder 확인이 필요하다 — club_world_cup_engine.
        # get_my_pending_stage 참고.
        cw_pend = club_world_cup_engine.get_my_pending_stage(week, day=day, p=p, st=st)
        if cw_pend:
            return cw_pend

        # [2026-07 버그수정, 신민용 리포트: "43주 마지막날에 44주 월요일
        # PO 경기 전 휴식이 안 뜰 수도 있잖아"] PO도 intl/cwc와 동일하게
        # po_matches.day가 실제 날짜로 채워져 있는데, 예전엔 이 조회가
        # 아래의 _week_intl_cl_day 게이트(챔스/컵처럼 아직 day가 없는
        # 대회 전용 — "그 주 딱 하루"에만 확인) *뒤에* 있어서, 그 게이트
        # 조건이 안 맞는 날엔 아예 PO 조회 자체가 호출되지도 않았다 —
        # intl/cwc처럼 이 게이트보다 앞에 둬야 day 기반 조회가 매일
        # 정상적으로 동작한다.
        import promotion_playoff_engine
        po = promotion_playoff_engine.get_my_po_match(week, day=day, p=p, st=st)
        if po:
            return po

        from game_engine import _week_intl_cl_day
        if day != _week_intl_cl_day(week, p, st=st):
            return None

        from competition import champions_engine
        cm = champions_engine.get_my_cl_match(week, day=day, p=p, st=st)
        if cm:
            cm["cl_kind"] = "champions"
            return cm
        from competition import europa_engine
        elm = europa_engine.get_my_el_match(week, day=day, p=p, st=st)
        if elm:
            elm["cl_kind"] = "europa"
            return elm
        from competition import conference_engine
        eclm = conference_engine.get_my_ecl_match(week, day=day, p=p, st=st)
        if eclm:
            eclm["cl_kind"] = "conference"
            return eclm
        from competition import cup_engine
        return cup_engine.get_my_cup_match(week, day=day, p=p, st=st)

    # ── 액션 ─────────────────────────────────────

    def _show_renew_dialog(self, p):
        """재계약 팝업.
        [2026-08 재설계, 신민용 확정] 예전엔 "팀이 정한 연봉·기간을 그대로
        제시 → 수락/거절만" 이었는데, 오퍼 창(OfferWindow)과 똑같은 방식의
        연봉 협상 + 기간 협상(1~6년, 콤보박스로 직접 선택) 버튼을 추가했다.
        연봉·기간 둘 다 결렬 없이 끝나야 재계약이 성사되고, 둘 중 하나라도
        마지막 시도에서 실패하면 이 재계약 제안 자체가 결렬되어(수락 버튼
        비활성화) 거절(방출/소속 없음)만 남는다 — OfferWindow와 동일한
        규칙."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QPushButton, QFrame, QComboBox)
        from game_engine import (join_team, update_player, get_state, get_conn,
                                  _contract_years_neg_delta, _record_team_offer_cooldown)
        from constants import (CONTRACT_YEARS_NEG_MAX_ATTEMPTS, CONTRACT_YEARS_NEG_SUCCESS_PROB,
                               CONTRACT_YEARS_MIN, CONTRACT_YEARS_MAX)
        import random

        _conn = get_conn()
        _row  = _conn.execute(
            "SELECT t.name, t.id, l.tier FROM teams t JOIN leagues l ON t.league_id=l.id "
            "WHERE t.id=?", (p.get("current_team_id",0),)).fetchone()
        _conn.close()
        team_name = _row["name"] if _row else "현재 팀"
        team_id   = _row["id"] if _row else p.get("current_team_id", 0)
        team_tier = _row["tier"] if _row else 3

        # [2026-08 신설] 협상 상태 — 클로저 안에서 갱신할 수 있게 dict로.
        state = {
            "sal": p.get("_contract_renew_offer", 0),
            "yrs": p.get("_contract_renew_years", 0) or 2,   # 안전 기본값
            "sal_used": random.randint(1, 3),
            "yrs_used": CONTRACT_YEARS_NEG_MAX_ATTEMPTS,
            "sal_failed": False,
            "yrs_failed": False,
        }
        state["target_yrs"] = state["yrs"]   # 콤보박스 기본값 = 초기 제시와 동일

        dlg = QDialog(self)
        dlg.setWindowTitle("📋 재계약 제안")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(_DIALOG_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18,16,18,16); lay.setSpacing(10)

        hdr = QLabel("📋 재계약 제안"); hdr.setObjectName("dlgHeader")
        lay.addWidget(hdr)

        card = QFrame(); card.setObjectName("dlgCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(14,12,14,12); cl.setSpacing(6)
        cl.addWidget(QLabel(f"<b style='color:#fff'>{team_name}</b><span style='color:#bbb'>에서 재계약을 제안합니다.</span>"))
        yrs_lbl = QLabel()
        sal_lbl = QLabel()
        cl.addWidget(yrs_lbl); cl.addWidget(sal_lbl)
        lay.addWidget(card)

        # ── 연봉 협상 행 ──────────────────────────────
        sal_row = QHBoxLayout(); sal_row.setSpacing(6)
        sal_btn = QPushButton(); sal_btn.setObjectName("negBtn")
        sal_row.addWidget(sal_btn); sal_row.addStretch()
        lay.addLayout(sal_row)

        # ── 기간 협상 행 (목표 콤보박스 + 버튼) ──────────
        yrs_row = QHBoxLayout(); yrs_row.setSpacing(6)
        yrs_combo = QComboBox()
        for y in range(CONTRACT_YEARS_MIN, CONTRACT_YEARS_MAX + 1):
            yrs_combo.addItem(f"{y}년", y)
        yrs_combo.setCurrentIndex(state["target_yrs"] - CONTRACT_YEARS_MIN)
        yrs_btn = QPushButton(); yrs_btn.setObjectName("negBtn")
        yrs_row.addWidget(QLabel("🎯 희망:")); yrs_row.addWidget(yrs_combo)
        yrs_row.addWidget(yrs_btn); yrs_row.addStretch()
        lay.addLayout(yrs_row)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        btn_accept = QPushButton(); btn_accept.setObjectName("dlgOk")
        btn_reject = QPushButton("❌ 거절 (소속 없음)"); btn_reject.setObjectName("dlgNo")
        btn_row.addWidget(btn_accept, 1); btn_row.addWidget(btn_reject, 1)
        lay.addLayout(btn_row)

        def _refresh():
            yrs_lbl.setText(f"<span style='color:#bbb'>제시 조건</span>  "
                             f"<b style='color:#ffcc33'>{state['yrs']}년 계약</b>")
            sal_lbl.setText(f"<span style='color:#bbb'>제시 연봉</span>  "
                             f"<b style='color:#00cc66'>{fmt_money(state['sal'])} / 년</b>")

            if state["sal_failed"]:
                sal_btn.setText("❌ 연봉 협상 결렬"); sal_btn.setEnabled(False)
            else:
                sal_btn.setText(f"💬 연봉 협상 ({state['sal_used']}회)")
                sal_btn.setEnabled(state["sal_used"] > 0)

            yrs_combo.setEnabled(not state["yrs_failed"])
            if state["yrs_failed"]:
                yrs_btn.setText("❌ 기간 협상 결렬"); yrs_btn.setEnabled(False)
            elif state["yrs"] == state["target_yrs"]:
                yrs_btn.setText("✅ 기간 합의 완료"); yrs_btn.setEnabled(False)
            else:
                yrs_btn.setText(f"📋 기간 협상 ({state['yrs_used']}회)")
                yrs_btn.setEnabled(state["yrs_used"] > 0)

            dead = state["sal_failed"] or state["yrs_failed"]
            btn_accept.setText(f"✅ {state['yrs']}년 계약 수락")
            # [2026-08 버그수정, 신민용 리포트: "결렬되면 입단할 수 없는데
            # 수락 버튼이 눌리지는 않지만 표시를 안 해서 헷갈린다"] 비활성
            # 처리(setEnabled(False)) 자체는 원래도 하고 있었다 — 진짜
            # 원인은 _DIALOG_STYLE에 "#dlgOk:disabled" 규칙이 없어서,
            # 비활성 상태에서도 눌리는 것처럼 계속 초록색 그대로 보였던
            # 것. 스타일시트에 그 규칙을 추가해서(회색으로 바뀜) 이제
            # 비활성 상태가 눈으로도 명확히 구분된다.
            btn_accept.setEnabled(not dead)

        def _on_target_changed(_):
            state["target_yrs"] = yrs_combo.currentData()
            _refresh()

        def _negotiate_sal():
            if state["sal_used"] <= 0 or state["sal_failed"]:
                return
            state["sal_used"] -= 1
            old_sal = state["sal"]
            delta = random.randint(10, 30)
            if random.random() < 0.55:
                state["sal"] = int(old_sal * (1 + delta/100))
                show_toast(self, f"✅ +{delta}%  {fmt_money(old_sal)} → {fmt_money(state['sal'])}",
                          "#006622", 1400)
            else:
                if state["sal_used"] == 0:
                    state["sal_failed"] = True
                    show_toast(self, "❌ 연봉 협상 결렬", "#cc0000", 1400)
                else:
                    show_toast(self, f"협상 실패  남은 기회: {state['sal_used']}회", "#cc4400", 1200)
            _refresh()

        def _negotiate_yrs():
            if state["yrs_used"] <= 0 or state["yrs_failed"]:
                return
            state["yrs_used"] -= 1
            old_yrs = state["yrs"]
            target  = state["target_yrs"]
            delta   = _contract_years_neg_delta(team_tier)
            success = random.random() < CONTRACT_YEARS_NEG_SUCCESS_PROB
            if success and old_yrs != target:
                direction = 1 if target > old_yrs else -1
                step = min(delta, abs(target - old_yrs))
                new_yrs = old_yrs + direction * step
                new_yrs = max(CONTRACT_YEARS_MIN, min(CONTRACT_YEARS_MAX, new_yrs))
                state["yrs"] = new_yrs
                show_toast(self, f"✅ 기간 협상 성공  {old_yrs}년 → {new_yrs}년", "#006622", 1400)
            else:
                if state["yrs_used"] == 0:
                    state["yrs_failed"] = True
                    show_toast(self, "❌ 기간 협상 결렬", "#cc0000", 1400)
                else:
                    show_toast(self, f"기간 협상 실패  남은 기회: {state['yrs_used']}회",
                              "#cc4400", 1200)
            _refresh()

        def _accept():
            yrs = state["yrs"]
            offer_sal = state["sal"]
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
            # [2026-08 신설] 협상 결렬로 거절한 것도 팀 오퍼 냉각기 대상.
            if state["sal_failed"] or state["yrs_failed"]:
                _record_team_offer_cooldown(team_id)
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

        sal_btn.clicked.connect(_negotiate_sal)
        yrs_btn.clicked.connect(_negotiate_yrs)
        yrs_combo.currentIndexChanged.connect(_on_target_changed)
        btn_accept.clicked.connect(_accept)
        btn_reject.clicked.connect(_reject)
        _refresh()
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
            # [단일 후보 전용] 후보가 1개뿐일 때의 "아니오" — 그 대회 전체를 닫는다.
            intl_engine.decline_national_team(pend["tournament_id"])
            dlg.accept()
            _nat_str = "/".join(o["nat"] for o in opts)
            show_toast(self, f"🚫 {_nat_str} 발탁을 거절했습니다 (기록에 남음)",
                       "#aa6633", 2000)
            if self.main_win: self.main_win.refresh_all()

        # [2026-08 재설계, 신민용 요청: "그레나다에서 아니요를 누르면 그
        # 나라 버튼만 회색으로 비활성화되고, 나머지 나라는 같은 창에 그대로
        # 남아있어야 한다 — 마지막 하나까지 전부 아니오면 그때 창이
        # 닫히는 거고, 그 전에 어느 쪽이든 예를 누르면 그 나라가 선택되는
        # 거다"] 예전엔 "아니오" 한 번마다 dlg.accept()로 창을 통째로 닫고
        # 남은 후보만으로 새 다이얼로그를 재귀적으로 다시 띄웠다 — 동작은
        # 맞았지만 화면상 매번 새 창이 뜨는 것처럼 보였다. 이제 버튼 쌍을
        # opt_buttons에 보관해두고, "아니오"를 누르면 그 나라의 버튼 쌍만
        # 그 자리에서 비활성화(회색 처리)한다 — 창은 안 닫힌다. 남은 후보가
        # 하나도 없을 때만(전부 아니오) 실제로 창을 닫는다.
        opt_buttons = {}

        def _do_decline_option(opt):
            tid = opt.get("tournament_id", pend["tournament_id"])
            intl_engine.decline_national_team_option(tid, opt["nat"])
            show_toast(self, f"🚫 {opt['nat']} 발탁을 거절했습니다 (기록에 남음)",
                       "#aa6633", 1800)
            pair = opt_buttons.get(opt["nat"])
            if pair:
                for b in pair:
                    b.setEnabled(False)
                    b.setStyleSheet("color:#777; background-color:#2a2a2a;")
            remaining = intl_engine.get_pending_choice()
            if not remaining:
                # 마지막 후보까지 전부 거절됐으면 그제서야 창을 닫는다.
                dlg.accept()
                if self.main_win:
                    self.main_win.refresh_all()

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
            # [2026-08 변경] 나라마다 독립된 "예/아니오" 쌍으로 표시 —
            # 하나를 "예" 하면 choose_national_team이 같은 해 나머지 대회를
            # 이미 자동으로 닫아주고(기존 로직), 하나를 "아니오" 하면 그
            # 나라 버튼 쌍만 이 자리에서 회색으로 비활성화되고 나머지는
            # 그대로 같은 창에 남는다(위 _do_decline_option).
            for opt in opts:
                _comp = opt.get("competition", "")
                _label = f"{opt.get('flag','')} {opt['nat']}"
                if _comp:
                    _label += f" ({_comp})"
                col = QFrame(); col_l = QVBoxLayout(col)
                col_l.setContentsMargins(0,0,0,0); col_l.setSpacing(4)
                nat_lbl = QLabel(_label); nat_lbl.setWordWrap(True)
                nat_lbl.setStyleSheet("color:#ffcc66; font-weight:bold;")
                col_l.addWidget(nat_lbl)
                pair = QHBoxLayout(); pair.setSpacing(4)
                b_yes = QPushButton("✅ 예")
                b_yes.setObjectName("dlgChoice")
                b_yes.clicked.connect(lambda _=False, o=opt: _do_choice(o))
                b_no = QPushButton("❌ 아니오")
                b_no.setObjectName("dlgNo")
                b_no.clicked.connect(lambda _=False, o=opt: _do_decline_option(o))
                pair.addWidget(b_yes); pair.addWidget(b_no)
                col_l.addLayout(pair)
                btn_row.addWidget(col, 1)
                opt_buttons[opt["nat"]] = (b_yes, b_no)
            lay.addLayout(btn_row)

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
        # [2026-08 버그수정, 신민용 리포트: "유로 예선 관련 국제전 표시가
        # 이상하다"] 같은 wc_qual-only 필터 버그 — 유로 예선(cont_qual)으로
        # 선발됐을 때도 "본선 진출!"이 아니라 "예선 소집!"으로 떠야 한다.
        _is_qual = (_kind in ("wc_qual", "cont_qual"))
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

    def _do_toggle_sale_push(self):
        """판매추진 ON/OFF 토글. 꺼도 매우 심각한 상황(4개+ 조건 겹침)에서는
        구단이 최종 결정을 내릴 수 있다 — 설계 확정 사항, 토글로 완전히
        막을 수 있는 건 아니다."""
        p = get_player()
        if not p: return
        from game_engine import update_player
        cur = bool(p.get("allow_club_sale_push", 1))
        new_val = 0 if cur else 1
        update_player(allow_club_sale_push=new_val)
        if new_val:
            show_toast(self, "🏟 구단 판매 추진을 허용합니다", "#006622", 1500)
        else:
            show_toast(self, "🏟 구단 판매 추진을 억제합니다  (단, 매우 심각한 상황에선 최종 결정 가능)",
                      "#666666", 2500)
        if self.main_win: self.main_win.refresh_all()

    def _do_join(self):
        """소속 없음일 때만 수동 팀 입단 (프리시즌 1~3주차)."""
        p = get_player()
        if not p: return
        if p.get("current_team_id"):
            show_toast(self, "⚠  소속 팀이 있을 때는 오퍼를 기다리세요")
            return
        from constants import MIN_JOIN_AGE
        _min_join_age = p.get("min_join_age") or MIN_JOIN_AGE
        if p["age"] < _min_join_age:
            show_toast(self, f"⚠  {_min_join_age}살부터 팀에 입단할 수 있습니다")
            return
        self._join_used = True
        self.btn_join.setEnabled(False)
        # [2026-07 신설, 신민용 요청] 결정 전에 껐다 켰을 때 오퍼가 새로
        # 랜덤 생성되는(=재접속 리롤) 걸 막기 위해, 저장된 상태가 있으면
        # 그걸 그대로 복원해서 쓰고 없을 때만 새로 생성한다.
        from game_engine import load_pending_offer_state
        restore = load_pending_offer_state(kind="join")
        # [2026-07 버그수정, 신민용 리포트: "계약만료 이후 팀 입단할 때 왜
        # 아무것도 안떠?"] force=True — 이 창은 "🔔 오퍼 ON/OFF" 토글과
        # 무관하게 항상 떠야 한다(버튼 자체 툴팁에도 "팀 입단에는 영향
        # 없음"이라고 이미 써있었다). 토글을 꺼둔 채로 계약이 만료되면
        # 강제 입단 창인데도 오퍼가 0개로 떠서 "직접 지원" 슬롯만 덩그러니
        # 남는 문제가 있었다.
        offers = restore.get("offers", []) if restore else generate_offers(force=True)
        if restore:
            from game_engine import refresh_offer_rank_info
            refresh_offer_rank_info(offers)
        # [2026-07] 개수는 이제 함수 내부 고정값(자국10+타국6)이 결정
        from ui.offer_window import OfferWindow
        # 이 창은 소속 팀이 없을 때만 뜨므로(위에서 이미 체크) 첫 입단이든
        # 퇴출/계약종료 후 재입단이든 항상 강제 입단 모드로 띄운다.
        # force_select=False면 닫기로 그냥 빠져나갈 수 있는데, 그러면 입단할
        # 곳이 없는 채로 진행이 막히거나 강제 은퇴로 이어질 수 있다.
        dlg = OfferWindow(offers, p.get("language","ko"), self,
                          title="🏟 팀 입단", force_select=True, grid=True, apply_slots=4,
                          kind="join", restore_state=restore)
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
        if not dlg.chosen:
            # [2026-07 신설, 신민용 지적: "왜 닫기를 잠궈버렸어"] 팀을 안
            # 고르고 그냥 닫은 경우 — 오퍼 상태는 저장돼 있으니(dlg.reject()
            # 참고) 버튼을 다시 눌러 열면 완전히 같은 목록이 그대로 뜬다.
            self._join_used = False
            self.btn_join.setEnabled(True)
            return
        self._on_join_done(dlg)

    def _on_join_done(self, dlg):
        if dlg.chosen:
            # [2026-08 수정, 신민용 확정] offer=dlg.chosen을 넘겨야
            # join_team이 기간 협상 결과(contract_years)를 실제 계약에
            # 반영한다 — transfer_type="입단"이라 이적료 승인 게이트
            # (transfer_type=="오퍼" 전용)는 여전히 안 걸린다.
            join_team(dlg.chosen["team_id"], dlg.chosen["salary"],
                      transfer_type="입단", offer=dlg.chosen)
            if self.main_win: self.main_win.refresh_all()

    def _show_auto_offer(self, week: int):
        """소속 있을 때 자동 오퍼 팝업 (이적시장: 여름 1~3주, 겨울 28~29주)."""
        from game_engine import _offer_probability, load_pending_offer_state
        p = get_player()
        if not p or not p.get("current_team_id"): return
        if self._auto_offer_shown: return

        # [2026-07 신설, 신민용 요청] 저장된 오퍼 상태가 있으면(결정 전에
        # 껐다 켠 경우) 확률/토글 체크를 다시 하지 않고 그대로 복원한다 —
        # 이미 한 번 통과해서 뜬 오퍼이므로 재판정하면 안 된다.
        restore = load_pending_offer_state(kind="auto_offer")
        if restore:
            # [버그수정 2026-07] 저장된 오퍼가 있어도 그 사이 사용자가 토글을
            #   껐다면(이적 요청 중이 아닌 한) 무시하고 보여주지 않아야 한다.
            #   기존엔 이 체크가 없어서 "토글을 꺼도 오퍼가 뜬다"는 문제가 있었다.
            if not p.get("offers_enabled", 1) and not p.get("transfer_requested"):
                from game_engine import clear_pending_offer_state
                clear_pending_offer_state()
                return
            offers = restore.get("offers", [])
            from game_engine import refresh_offer_rank_info
            refresh_offer_rank_info(offers)
        else:
            # [오퍼 토글] 꺼져 있으면 자동 오퍼 팝업을 건너뛴다.
            #   단, '이적 요청' 중이면 사용자가 명시적으로 이적을 원한다는 뜻이므로
            #   토글과 무관하게 오퍼를 계속 보여준다.
            if not p.get("offers_enabled", 1) and not p.get("transfer_requested"):
                return

            prob = _offer_probability(p, week)
            import random
            if random.random() > prob:
                return  # 이번 구간 오퍼 없음

            offers = generate_offers()
            if not offers: return

        self._auto_offer_shown = True
        from ui.offer_window import OfferWindow
        dlg = OfferWindow(offers, p.get("language","ko"), self, title="✈ 오퍼", grid=True,
                          kind="auto_offer", restore_state=restore)
        self._offer_dlg = dlg
        # 모달(exec)로 띄워 오퍼창이 열려 있는 동안 next day 진행을 차단.
        dlg.exec()
        self._on_auto_offer_done(dlg)

    def _on_auto_offer_done(self, dlg):
        if dlg.chosen:
            join_team(dlg.chosen["team_id"], dlg.chosen["salary"], transfer_type="오퍼", offer=dlg.chosen)
            if self.main_win: self.main_win.refresh_all()

    def _restore_pending_offer_window(self):
        """[2026-07 신설, 신민용 요청] 앱 시작 시 저장된 오퍼 상태가 있으면
        (결정을 내리기 전에 게임을 껐다 켠 경우) 새로 뽑지 않고 그 상태
        그대로 오퍼/입단 창을 다시 띄운다 — _do_join/_show_auto_offer와
        각각 동일한 흐름을 그대로 재현한다."""
        from game_engine import load_pending_offer_state, clear_pending_offer_state
        restore = load_pending_offer_state()
        if not restore:
            return
        p = get_player()
        if not p:
            return
        kind = restore.get("kind")
        from ui.offer_window import OfferWindow

        if kind == "join":
            if p.get("current_team_id"):
                # 이미 소속 팀이 생긴 비정상 상태 — 남은 상태만 정리.
                clear_pending_offer_state()
                return
            self._join_used = True
            self.btn_join.setEnabled(False)
            _restored_offers = restore.get("offers", [])
            from game_engine import refresh_offer_rank_info
            refresh_offer_rank_info(_restored_offers)
            dlg = OfferWindow(_restored_offers, p.get("language", "ko"), self,
                              title=restore.get("title", "🏟 팀 입단"),
                              force_select=restore.get("force_select", True),
                              grid=restore.get("grid", True),
                              apply_slots=restore.get("apply_slots", 4),
                              kind="join", restore_state=restore)
            self._offer_dlg = dlg
            dlg.exec()
            if not dlg.chosen and getattr(dlg, "all_failed", False):
                self._skip_join_lock = True
                self._join_used = True
                self.btn_join.setEnabled(False)
                show_toast(self, "📅 모든 협상 결렬 — 올해는 입단을 보류하고 1년 더 훈련합니다.",
                           "#cc6600", 2200)
                if self.main_win: self.main_win.refresh_all()
                return
            if not dlg.chosen:
                # 팀을 안 고르고 그냥 닫은 경우 — 상태는 저장돼 있으니 버튼을
                # 다시 눌러 열면 완전히 같은 목록이 그대로 뜬다.
                self._join_used = False
                self.btn_join.setEnabled(True)
                return
            self._on_join_done(dlg)

        elif kind == "auto_offer":
            if not p.get("current_team_id"):
                # 소속이 없어진(방출 등) 비정상 상태 — 남은 상태만 정리.
                clear_pending_offer_state()
                return
            # [버그수정 2026-07] 오퍼 창을 띄운 채(또는 뜨기 직전 상태로) 게임을
            #   종료했다가, 재시작 전/후 사이에 토글을 껐다면 — 이적 요청 중이
            #   아닌 한 그대로 복원하지 않고 정리한다. (기존엔 무조건 복원)
            if not p.get("offers_enabled", 1) and not p.get("transfer_requested"):
                clear_pending_offer_state()
                return
            self._auto_offer_shown = True
            _restored_offers2 = restore.get("offers", [])
            from game_engine import refresh_offer_rank_info
            refresh_offer_rank_info(_restored_offers2)
            dlg = OfferWindow(_restored_offers2, p.get("language", "ko"), self,
                              title=restore.get("title", "✈ 오퍼"),
                              grid=restore.get("grid", True),
                              kind="auto_offer", restore_state=restore)
            self._offer_dlg = dlg
            dlg.exec()
            self._on_auto_offer_done(dlg)

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
        from game_engine import _team_league_id_for_season
        st = get_state()
        conn = get_conn()
        c = conn.cursor()
        # [버그수정 2026-08, 신민용 리포트: "승강전에서 승급/강등되면 바로
        # 반영되는거 같은데, 다음 연도 1주차에 반영돼야 하지 않나 — 경기
        # 일정 다 사라지고 좌측에 정보 없음 뜨더라"] teams.league_id는
        # 43주 승강 처리 즉시 새 리그를 가리키도록 바뀌는데(사이드바에
        # 승격/강등을 바로 보여주려는 의도적 설계), 44~52주 국제대회
        # 기간엔 아직 "이번 시즌"이 진행 중이라 실제 경기는 전부 옛
        # 리그에 남아있다 — 그 상태로 새 리그+이번 시즌을 조회하면 내
        # 팀이 없는 리그를 보게 돼 순위표가 텅 비거나 "정보 없음"이 뜬다.
        # career_entries와 동일하게 _team_league_id_for_season으로 이번
        # 시즌에 실제로 뛴 리그를 먼저 찾고, 없으면(막 이적 직후 등)
        # 지금 소속 리그로 폴백한다.
        lid = _team_league_id_for_season(c, p["current_team_id"], st["current_season"])
        if lid is None:
            row = c.execute(
                "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (p["current_team_id"],)).fetchone()
            lid = row["id"] if row else p["current_league_id"]
        conn.close()
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
        from game_engine import _team_league_id_for_season
        conn = get_conn()
        c = conn.cursor()
        # [버그수정 2026-08, 신민용 리포트: "승강전에서 승급/강등되면 바로
        # 반영되는거 같은데, 다음 연도 1주차에 반영돼야 하지 않나 — 경기
        # 일정 다 사라지고 좌측에 정보 없음 뜨더라"] _do_standings와 동일한
        # 원인 — teams.league_id는 43주 승강 처리 즉시 새 리그를 가리키게
        # 바뀌지만, 44~52주 국제대회 기간엔 이번 시즌 실제 경기가 전부
        # 옛 리그에 남아있다. 그 상태로 새 리그+이번 시즌을 조회하면
        # get_schedule()이 빈 결과를 반환해 일정이 통째로 사라져 보인다.
        lid = _team_league_id_for_season(c, p["current_team_id"], st["current_season"])
        if lid is None:
            row = c.execute(
                "SELECT l.id FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (p["current_team_id"],)).fetchone()
            lid = row["id"] if row else p["current_league_id"]
        conn.close()
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
        p = get_player() or {}
        has_team = bool(p.get("current_team_id"))
        # 은퇴 가능 구간: 프리시즌(1~3주, 새 시즌 직후·연장 거절 타이밍) 또는
        #   국제대회 비시즌(44~52주, 리그 종료 후).
        # [2026-08 신설, 신민용 요청: "팀이 없으면 언제든 은퇴 가능하게"]
        #   이 주차 제한은 애초에 "리그 진행 중에 은퇴하면 그 시즌 우승·
        #   개인수상이 확정되기 전이라 누락될 수 있다"는 이유로 있었다 —
        #   그런데 팀이 없는 선수는애초에 리그 성적·수상 자체가 없으니
        #   이 위험이 성립하지 않는다. 팀이 없으면 주차와 무관하게 상시
        #   은퇴를 허용하고, 팀이 있을 때만 기존 제한을 그대로 유지한다.
        if not has_team:
            pass  # 팀 없음 — 언제든 은퇴 가능
        elif not ((_pss5 <= week <= _pse5) or (_oss5 <= week <= _ose5)):
            show_toast(self, f"⚠  은퇴는 시즌 종료 후({_oss5}주차~) 또는 새 시즌 {_pss5}~{_pse5}주차에 가능합니다", "#cc6600", 1900)
            return

        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
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