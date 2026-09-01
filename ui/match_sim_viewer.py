"""
ui/match_sim_viewer.py — 경기 상세의 "▶ 시뮬 보기" 버튼으로 여는 2D 시뮬 뷰어.

[중요 - 데이터의 한계]
  이 게임의 매치 엔진은 확률 기반 통계 시뮬레이션이라, 22명 선수의 실제 좌표나
  패스 궤적 같은 데이터는 애초에 존재하지 않는다. 있는 건:
    - 내 개인 이벤트 타임라인 (몇 분에 내가 골/도움/선방/실점했는지, 텍스트+분)
    - 양팀 포메이션(포지션 배치)
    - 최종 스코어
  그래서 이 뷰어는 "실제 시뮬을 재생"하는 게 아니라, 이 진짜 데이터(스코어가
  언제 나왔는지)에 맞춰 포메이션 기준으로 그럴듯한 움직임을 절차적으로
  연출하는 것이다. 평상시엔 대략적인 점유율 흐름(공이 이리저리 움직이고
  선수들이 포메이션 주변에서 반응)을 보여주다가, 실제로 골/선방 이벤트가
  있었던 그 분(分)이 되면 그 결과에 맞는 짧은 장면(공격 전개→골 또는 막힘)을
  연출한다.

[성능] 점 23개(양팀 22 + 공) 정도를 60~200ms 간격 QTimer로 갱신하는 수준이라
  실측해도 CPU 부담이 거의 없다. 창을 닫으면(closeEvent) 타이머를 확실히
  멈춰서 백그라운드에 남지 않게 처리했다.
"""

# ══════════════════════════════════════════════════════════════════
# [구조 변경] 경기 시뮬 엔진은 match_sim/sim_engine.py로 분리됐다.
# 이 파일에는 이제 "그리는 코드"만 남는다 — 22명 상태 계산, 대형/역할,
# 볼/패스, 데드볼 상태머신, 씬 연출은 전부 MatchSimEngine이 갖고 있고,
# 여기서는 엔진이 구워놓은 self._frames를 재생/보간해서 화면에 칠한다.
#
# 두 층 사이의 유일한 계약은 프레임 dict(sim_engine._snapshot_frame 참조)다.
# 렌더러가 엔진의 내부 상태를 직접 읽는 곳은 포지션 라벨/팀 이름 같은
# 정적 메타데이터뿐이다 — 그 외에는 프레임만 본다.
# ══════════════════════════════════════════════════════════════════
import random
import time
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton, QComboBox,
    QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont

from match_sim.sim_engine import MatchSimEngine, _GOAL_HALF_HEIGHT
# [하위호환] 예전엔 이 모듈이 이 이름들을 직접 정의했다. 외부(테스트/프로브
# 등)에서 ui.match_sim_viewer 경유로 참조하던 코드가 깨지지 않도록 재수출한다.
from match_sim.sim_engine import (  # noqa: F401
    layout_formation, _lookup_formation, _classify_event, _detect_style,
    _stable_seed, _tactical_dx, _smooth_damp, _steer_toward, _find_my_slot,
    _corner_slots, _penalty_arc_slots, _spread, _clamp_shot_start_x,
    _POS_XY, _ATTACK_ROLES, _SUPPORT_ROLES, _DEFENSE_ROLES, _BACKLINE_ROLES,
    _MIN_DEFENSIVE_DEPTH, _SHOT_ZONE_NORMAL, _SHOT_ZONE_THROUGH,
    _WIDE_ROLES, _MAX_SPEED, _SMOOTH_TIME, _TACTICAL_DX,
)


class _Pitch(QWidget):
    """피치 배경 + 점(선수)+공을 그리는 캔버스. 상태는 부모(MatchSimViewer)가
    들고 있고, 이 위젯은 매 프레임 그 상태를 읽어서 그리기만 한다."""

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.setMinimumSize(640, 420)
        # [신규] 유튜브처럼 화면 가운데(피치 아무 곳)를 클릭하면 재생/일시정지 토글.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        self.viewer._toggle_play()
        super().mousePressEvent(ev)

    def paintEvent(self, _ev):
        v = self.viewer
        w, h = self.width(), self.height()
        pad = 20
        pw, ph = w - pad * 2, h - pad * 2

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 피치
        p.fillRect(0, 0, w, h, QColor("#0d2b12"))
        p.setPen(QPen(QColor("#2f7a3f"), 2))
        p.drawRect(pad, pad, pw, ph)
        p.drawLine(pad + pw // 2, pad, pad + pw // 2, pad + ph)
        p.drawEllipse(pad + pw // 2 - 45, pad + ph // 2 - 45, 90, 90)
        box_w, box_h = int(pw * 0.12), int(ph * 0.5)
        p.drawRect(pad, pad + (ph - box_h) // 2, box_w, box_h)
        p.drawRect(pad + pw - box_w, pad + (ph - box_h) // 2, box_w, box_h)

        # [현실성 보정] 예전엔 페널티박스만 그려서, 박스 전체가 마치 골대인
        # 것처럼 보였다(실제 골대 폭은 박스 폭의 약 1/5밖에 안 됨). 골라인
        # 위에 진짜 골대 크기(_GOAL_HALF_HEIGHT 기준, 득점/노골 판정에
        # 쓰는 값과 동일)로 별도의 골문을 짧게 튀어나오도록 그린다.
        goal_depth = 6
        goal_h = int(ph * _GOAL_HALF_HEIGHT * 2)
        goal_y = pad + (ph - goal_h) // 2
        p.setPen(QPen(QColor("#eaffea"), 3))
        p.drawRect(pad - goal_depth, goal_y, goal_depth, goal_h)
        p.drawRect(pad + pw, goal_y, goal_depth, goal_h)
        p.setPen(QPen(QColor("#2f7a3f"), 2))

        def to_px(x, y):
            return pad + x * pw, pad + y * ph

        # 선수 점
        label_font = QFont()
        label_font.setPixelSize(8)
        label_font.setBold(True)
        for team_players, color, my_idx in (
                (v.home_players, QColor("#4488ff"), v.my_slot if v.is_home else -1),
                (v.away_players, QColor("#ff5555"), v.my_slot if not v.is_home else -1)):
            for i, pl in enumerate(team_players):
                x, y = to_px(pl["x"], pl["y"])
                r = 8
                if i == my_idx:
                    p.setPen(QPen(QColor("#ffee55"), 2))
                    p.setBrush(QBrush(color))
                    r = 10
                else:
                    p.setPen(QPen(QColor("#000000"), 1))
                    p.setBrush(QBrush(color))
                p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)
                # [신규] 레퍼런스(피파 온라인 모바일 감독모드)처럼 원 안에
                # 식별 텍스트를 넣는다. 실제 등번호 데이터는 없어서(스쿼드
                # 번호 필드 자체가 없음) 대신 포지션 라벨(GK/CB/ST 등)을
                # 축약해서 넣는다 — 그냥 색깔 점이었던 예전보다 "이게
                # 누구인지" 훨씬 읽기 쉬워진다.
                p.setPen(QPen(QColor("#ffffff")))
                p.setFont(label_font)
                p.drawText(QRectF(x - r, y - r, r * 2, r * 2),
                           Qt.AlignmentFlag.AlignCenter, pl["pos"][:2])

        # 패스 궤적 잔상(공이 날아온 경로를 옅어지는 선으로 표시)
        if len(v.ball_trail) >= 2:
            pts = [to_px(x, y) for x, y, _a in v.ball_trail]
            for k in range(1, len(pts)):
                alpha = v.ball_trail[k][2]
                if alpha <= 0:
                    continue
                p.setPen(QPen(QColor(255, 255, 255, int(alpha * 0.7)), 2))
                p.drawLine(int(pts[k - 1][0]), int(pts[k - 1][1]),
                           int(pts[k][0]), int(pts[k][1]))

        # 공
        bx, by = to_px(v.ball["x"], v.ball["y"])
        p.setPen(QPen(QColor("#222"), 1))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(int(bx - 5), int(by - 5), 10, 10)

        # 배너(주요 장면 텍스트)
        if v.banner_text and v.banner_alpha > 0:
            font = QFont()
            font.setPointSize(15)
            font.setBold(True)
            p.setFont(font)
            col = QColor(v.banner_color)
            col.setAlpha(int(v.banner_alpha))
            p.setPen(col)
            p.drawText(QRectF(0, h * 0.08, w, 40),
                      Qt.AlignmentFlag.AlignCenter, v.banner_text)

        p.end()


class _SeekBar(QWidget):
    """[재생바] 클릭/드래그로 경기의 아무 시점이나 바로 이동할 수 있는
    커스텀 시크바. 원하는 순간에 멈추려면 일시정지 타이밍을 정확히 맞춰야
    했던 불편함을 없애준다. 전/후반 경계 지점(하프타임)에 세로선을 그려서
    지금 보고 있는 게 전반인지 후반인지 한눈에 알 수 있게 한다."""

    def __init__(self, viewer):
        super().__init__()
        self.viewer = viewer
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _ev):
        v = self.viewer
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track_y = h // 2 - 2
        track_h = 4

        # 배경 트랙
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2a2a2a"))
        p.drawRoundedRect(0, track_y, w, track_h, 2, 2)

        match_end = max(1e-6, v.match_end)
        frac = max(0.0, min(1.0, v.clock / match_end))

        # 진행도
        p.setBrush(QColor("#3a7fd5"))
        p.drawRoundedRect(0, track_y, round(w * frac), track_h, 2, 2)

        # 전/후반 경계선(하프타임 지점) — 전반 종료+전반 추가시간 위치
        half_frac = max(0.0, min(1.0, (45 + v.stoppage1) / match_end))
        hx = round(w * half_frac)
        p.setPen(QPen(QColor("#888888"), 1))
        p.drawLine(hx, 1, hx, h - 1)

        # 핸들(현재 위치)
        knob_x = round(w * frac)
        p.setPen(QPen(QColor("#0a0a0a"), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(knob_x - 6, h // 2 - 6, 12, 12)
        p.end()

    def _seek_from_x(self, x):
        frac = max(0.0, min(1.0, x / max(1, self.width())))
        self.viewer._seek_to(frac * self.viewer.match_end)

    def mousePressEvent(self, ev):
        self._seek_from_x(ev.position().x())

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self._seek_from_x(ev.position().x())


class MatchSimViewer(QDialog):
    """엔진이 구워놓은 궤적 로그를 재생하는 뷰어.

    [주의] 시뮬레이션 로직을 여기에 다시 추가하지 말 것 — 그러면 UI 없이
    검증할 수 없는 코드가 또 생긴다. 움직임/전술과 관련된 변경은 전부
    match_sim/sim_engine.py에서 한다.
    """

    # 화면 갱신 주기(ms). 시뮬 해상도(_FRAME_DT)와는 별개다 — 프레임 사이를
    # _apply_frame_at이 보간해서 채운다.
    TICK_MS = MatchSimEngine.TICK_MS
    _FRAME_DT = MatchSimEngine._FRAME_DT
    _SEC_PER_MIN = MatchSimEngine._SEC_PER_MIN

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("경기 시뮬 보기")
        self.setStyleSheet("QDialog{background:#161616;color:#ccc;}")
        self.resize(760, 560)

        # ── 경기 시뮬 실행 (Qt와 무관, 여기서 프레임이 전부 구워진다) ──
        self.sim = MatchSimEngine(data)
        self.sim.simulate()

        # [프록시] 렌더링 코드가 예전처럼 self.<이름>으로 읽을 수 있게
        # 엔진의 정적 메타데이터/상태를 그대로 참조시킨다. 값을 복사하지
        # 않고 같은 객체를 가리키므로, _apply_frame이 선수 dict의 x/y를
        # 갱신하면 엔진 쪽 객체가 그대로 갱신된다(재생 전용 상태이므로
        # 시뮬 결과에는 영향이 없다 — 프레임은 이미 다 구워졌다).
        eng = self.sim
        self._frames = eng._frames
        self._true_match_end = eng._true_match_end
        # closeEvent에서 되돌리기 위해 엔진이 보관 중인 원본 RNG 상태
        self._pre_seed_rng_state = eng._pre_seed_rng_state
        self.home_players = eng.home_players
        self.away_players = eng.away_players
        self.ball = eng.ball
        self.home_name = eng.home_name
        self.away_name = eng.away_name
        self.home_formation = eng.home_formation
        self.away_formation = eng.away_formation
        self.is_home = eng.is_home
        self.my_slot = eng.my_slot
        self.final_home = eng.final_home
        self.final_away = eng.final_away
        self.timeline = eng.timeline
        self.match_end = eng.match_end
        self.stoppage1 = eng.stoppage1
        self.stoppage2 = eng.stoppage2
        self.clock = 0.0
        self.score_home = 0
        self.score_away = 0
        self.banner_text = ""
        self.banner_color = "#ffffff"
        self.banner_alpha = 0
        self.ball_trail = []
        self.playing = True
        self.speed = 1.0

        self._build_ui()
        self._frame_idx = 0
        self._apply_frame(0)
        # [버그 수정] 실제 경과시간 측정용. 아래 _tick()에서 "TICK_MS만큼
        # 지났다"고 가정하는 대신 실측한다 — 렌더링/시스템 부하로 콜백이
        # 늦게 불려도 재생 속도(페이싱)가 밀리지 않게 하기 위함.
        self._last_tick_perf = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(self.TICK_MS)

    def _apply_frame(self, idx):
        """[재생바 시크 전용] 미리 계산된 self._frames[idx]를 화면 표시
        상태에 그대로(보간 없이) 반영한다. 드래그 중엔 정확히 그 프레임을
        보여주는 게 맞아서 보간하지 않는다."""
        idx = max(0, min(len(self._frames) - 1, idx))
        f = self._frames[idx]
        for pl, (x, y) in zip(self.home_players, f["home"]):
            pl["x"], pl["y"] = x, y
        for pl, (x, y) in zip(self.away_players, f["away"]):
            pl["x"], pl["y"] = x, y
        self.ball["x"], self.ball["y"] = f["ball"]
        self.clock = f["clock"]
        self._finish_apply(idx)

    def _apply_frame_at(self, clock_value):
        """[재생 전용] 임의의 연속적인 clock 값에 대해, 인접한 두 사전계산
        프레임 사이를 선형보간해서 표시한다. 프레임을 굽는 해상도
        (_FRAME_DT)는 그대로 두고(내부 튜닝 상수들과 얽혀 있어 안 건드림),
        화면 갱신 주기(TICK_MS)만 낮춰서 그 사이 경유점을 보간으로 채워
        넣는 방식 — 실제 재생 속도(전체 소요시간)는 전혀 바뀌지 않으면서
        움직임만 더 매끄럽게 보인다. 스코어/배너처럼 불연속적인 값은
        보간하지 않고 앞쪽(idx0) 프레임 값을 그대로 쓴다(득점 반영 시점이
        어긋나면 안 되므로)."""
        clock_value = max(0.0, min(self._true_match_end, clock_value))
        self.clock = clock_value
        float_idx = clock_value / self._FRAME_DT
        last = len(self._frames) - 1
        idx0 = max(0, min(last, int(float_idx)))
        idx1 = min(last, idx0 + 1)
        frac = 0.0 if idx1 == idx0 else (float_idx - idx0)
        f0, f1 = self._frames[idx0], self._frames[idx1]

        for pl, (x0, y0), (x1, y1) in zip(self.home_players, f0["home"], f1["home"]):
            pl["x"], pl["y"] = x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac
        for pl, (x0, y0), (x1, y1) in zip(self.away_players, f0["away"], f1["away"]):
            pl["x"], pl["y"] = x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac
        bx0, by0 = f0["ball"]
        bx1, by1 = f1["ball"]
        self.ball["x"] = bx0 + (bx1 - bx0) * frac
        self.ball["y"] = by0 + (by1 - by0) * frac
        self._finish_apply(idx0)

    def _finish_apply(self, idx):
        """[재생/시크 공용 마무리] 스코어·배너·패스잔상·시계 라벨처럼
        보간하지 않는(또는 보간할 필요 없는) 상태들을 idx 프레임 기준으로
        갱신한다. _apply_frame과 _apply_frame_at이 공유한다."""
        self._frame_idx = idx
        f = self._frames[idx]
        self.score_home = f["score_home"]
        self.score_away = f["score_away"]
        new_score_text = (f"⚽ {self.home_name}  {self.score_home} - "
                           f"{self.score_away}  {self.away_name}")
        if self.score_lbl.text() != new_score_text:
            self.score_lbl.setText(new_score_text)
        self.banner_text = f["banner_text"]
        self.banner_color = f["banner_color"]
        self.banner_alpha = f["banner_alpha"]
        # 패스 궤적 잔상: 직전 몇 프레임의 공 위치를 옅어지는 흔적으로
        # 재구성한다(예전엔 매 틱 누적/감쇠시키는 별도 상태였지만, 이제
        # 프레임 자체가 기록이므로 과거 프레임에서 그때그때 다시 뽑아내면
        # 된다).
        trail = []
        for back in range(8, 0, -1):
            j = idx - back
            if j < 0:
                continue
            bx, by = self._frames[j]["ball"]
            alpha = 255 - back * 28
            if alpha > 0:
                trail.append([bx, by, alpha])
        self.ball_trail = trail
        self.clock_lbl.setText(
            "전반 {}   후반 {}".format(*self._display_halves(self.clock)))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)

        hdr = QLabel(f"⚽ {self.home_name}  {self.score_home} - {self.score_away}  {self.away_name}")
        hdr.setStyleSheet("color:#fff;font-size:15px;font-weight:bold;")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.score_lbl = hdr
        root.addWidget(hdr)

        self.clock_lbl = QLabel("전반 0'   후반 0'")
        self.clock_lbl.setStyleSheet("color:#888;font-size:12px;")
        self.clock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.clock_lbl)

        self.pitch = _Pitch(self)
        root.addWidget(self.pitch, 1)

        self.seek_bar = _SeekBar(self)
        root.addWidget(self.seek_bar)

        ctrl = QHBoxLayout()
        self.play_btn = QPushButton("▶ 재생")
        self.play_btn.clicked.connect(self._toggle_play)
        ctrl.addWidget(self.play_btn)

        self.speed_combo = QComboBox()
        for s in ["1x", "2x", "4x"]:
            self.speed_combo.addItem(s)
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)
        ctrl.addWidget(self.speed_combo)

        # [신규] 디버그 캡처 — 재생 중 "이거 이상한데?" 싶은 순간 누르면,
        # 그 앞뒤 몇 초 구간의 22명+공 좌표/배너를 통째로 JSON 파일로
        # 저장한다. 말로 "전반 O분쯤 이상했다"고 설명하는 것보다 훨씬
        # 정확하게, 정확한 프레임 단위로 재현/진단할 수 있다.
        debug_btn = QPushButton("🐛 디버그 캡처")
        debug_btn.setToolTip("방금 화면이 이상해 보였다면 눌러주세요 — "
                              "현재 시점 앞뒤 몇 초 구간을 파일로 저장합니다.")
        debug_btn.clicked.connect(self._export_debug_capture)
        ctrl.addWidget(debug_btn)

        ctrl.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        ctrl.addWidget(close_btn)
        root.addLayout(ctrl)

        note = QLabel("※ 실제 좌표 데이터가 없는 통계 시뮬이라, 득점/선방 '시점'은 실제 기록대로이고 "
                      "움직임 자체는 포메이션 기준 연출입니다.  (화면을 클릭해도 재생/일시정지됩니다)")
        note.setStyleSheet("color:#555;font-size:10px;")
        note.setWordWrap(True)
        root.addWidget(note)

    def _toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            # [버그 수정] 일시정지해뒀다가 다시 재생하면, 그 사이(정지해
            # 있던 실제 시간)가 "경과시간"으로 한꺼번에 잡혀서 시계가 확
            # 튀는 문제를 막는다 — 재생 재개 시점을 기준으로 다시 잰다.
            self._last_tick_perf = time.perf_counter()
        self.play_btn.setText("⏸ 일시정지" if self.playing else "▶ 재생")

    def _on_speed_changed(self, text):
        self.speed = float(text.replace("x", ""))

    def closeEvent(self, event):
        self.timer.stop()
        # 이 창이 열려있는 동안 시드를 고정해뒀던 전역 random 상태를 원래대로
        # 되돌린다 — 창을 닫은 뒤엔 앱의 다른 랜덤 로직에 영향이 없어야 한다.
        random.setstate(self._pre_seed_rng_state)
        super().closeEvent(event)

    def _tick(self):
        # [버그 수정] 예전엔 여기서 매 틱 시뮬레이션을 직접 진행시켰다.
        # 실시간 QTimer 틱 수가 실행마다 미세하게 달라질 수 있어서(시스템
        # 성능/렌더링 부하), 그 안에서 소비되는 난수도 매번 달라져 "같은
        # 실제 시간이지만 다른 장면"이 나오는 원인이 됐다. 이제는 전체
        # 경기가 __init__에서 이미 다 계산되어 있으므로, 여기서는 그냥
        # "다음에 보여줄 시점"만 계산해서 읽어 보여준다 — 배속이나
        # 프레임 드랍과 무관하게 내용 자체는 항상 동일하다.
        #
        # [버그 수정 — 핵심] 예전엔 "이 콜백은 항상 TICK_MS(20ms)만큼 지난
        # 뒤에 불린다"고 가정했다. 근데 실제로는 시스템/렌더링 부하로 콜백이
        # 그보다 훨씬 늦게(느린 환경에서는 70~80ms씩) 불릴 수 있는데, 그래도
        # "20ms만 지났다"고 착각하고 그만큼만 시계를 전진시켰다. 그 결과
        # "1x=4초/분"으로 설정해놔도 실제로는 렌더링이 느린 만큼 체감 배속이
        # 밀려서 "15초나 걸린다"는 증상이 났다(제보 내용 그대로 재현되는
        # 원인). 이제 time.perf_counter()로 실제 경과시간을 재서 그 값을
        # 쓴다 — 프레임이 얼마나 자주 그려지든, 실제 흐른 시간만큼만
        # 정확하게 전진하므로 페이싱이 렌더링 성능과 무관해진다.
        now = time.perf_counter()
        real_elapsed = now - self._last_tick_perf
        self._last_tick_perf = now
        # 앱이 오래 멈췄다 돌아온 경우(창 최소화 등)처럼 극단적으로 큰
        # 값만 막는다. 너무 타이트하게(예: 0.5초) 잡으면 정작 이 함수가
        # 고치려는 "렌더링이 느려서 콜백이 늦게 불리는" 정상적인 보정
        # 상황까지 잘라버려서 배속이 다시 밀리는 원인이 된다.
        real_elapsed = max(0.0, min(2.0, real_elapsed))

        if self.playing:
            speed_mult = self.speed
            target_clock = self.clock + speed_mult * real_elapsed / self._SEC_PER_MIN
            if target_clock >= self._true_match_end:
                target_clock = self._true_match_end
                self.playing = False
                self.play_btn.setText("▶ 재생")
            self._apply_frame_at(target_clock)

        self.pitch.update()
        self.seek_bar.update()

    def _seek_to(self, new_clock):
        """[재생바] 원하는 시점으로 즉시 이동. 전체 경기가 이미 __init__에서
        고정 시드로 단 한 번에 미리 계산되어 self._frames에 저장되어
        있으므로, 그 시점에 해당하는 프레임을 찾아 그대로 보여주기만 하면
        된다. 재생/시크/몇 번을 다시 열어보든 전부 같은 소스(같은 프레임
        배열)를 읽으므로 100% 같은 화면이 나온다. (예전엔 이 함수가 매번
        처음부터 다시 빨리감기 시뮬레이션을 돌리는 방식이라 무거웠고,
        실시간 재생과 미묘하게 어긋나는 경우도 있었다 — 이제는 단순 배열
        인덱싱이라 더 빠르고 항상 정확히 일치한다.)"""
        target = max(0.0, min(self.match_end, new_clock))
        idx = int(round(target / self._FRAME_DT))
        self._apply_frame(idx)
        self.pitch.update()
        self.seek_bar.update()

    def _display_halves(self, elapsed):
        return self.sim._display_halves(elapsed)

    def _export_debug_capture(self):
        """엔진에 저장을 시키고, 결과를 사용자에게 알리는 것만 여기서 한다."""
        res = self.sim._export_debug_capture()
        if not isinstance(res, dict):
            return res
        if res.get("ok"):
            QMessageBox.information(
                self, "디버그 캡처 저장됨",
                f"저장 완료:\n{res['path']}\n\n"
                "이 파일을 그대로 보내주시면 정확히 이 순간을 재현해서 확인할게요.")
        else:
            QMessageBox.warning(self, "디버그 캡처 실패",
                                f"저장 중 오류가 발생했습니다:\n{res.get('error')}")
        return res