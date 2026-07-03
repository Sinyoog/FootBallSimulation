"""
ui/match_detail_dialog.py  ─  로그에서 경기 헤더 클릭 시 뜨는 상세 창

game_engine.get_match_detail(id) 가 돌려주는 dict 를 받아
전/후반 타임라인 · 평점 · 세부 지표 · 총평을 보기 좋게 펼쳐 보여준다.

[구조] "▶ 시뮬 보기" / "📊 경기 통계"는 예전엔 각각 새 창(QDialog.show())을
열었는데, 이제는 새 창을 띄우지 않고 이 다이얼로그 자체가 오른쪽으로
펼쳐지면서(가로 폭이 늘어나면서) 그 안에 인라인으로 들어간다. 시뮬 뷰어
(MatchSimViewer)는 QDialog로 만들어진 걸 windowFlags만 Widget으로 바꿔서
그대로 재사용한다 — 로직을 중복 구현하지 않기 위함.
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QWidget, QFrame, QPushButton)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor


def _fmt_min(m):
    """정렬용 분(정수) → 표시 문자열. 전반 추가시간 146~155=45+1~10, 후반 91~100=90+1~10."""
    try:
        m = int(m)
    except (ValueError, TypeError):
        return str(m)
    if 146 <= m <= 155:
        return f"45+{m-145}"
    if 91 <= m <= 100:
        return f"90+{m-90}"
    return str(m)


def _min_sortkey(m):
    """실제 경기 시간 정렬 키. 전반 추가시간→45.x, 후반 추가시간→90.x."""
    try:
        m = int(m)
    except (ValueError, TypeError):
        return 0.0
    if 146 <= m <= 155:
        return 45 + (m - 145) / 100.0
    if 91 <= m <= 100:
        return 90 + (m - 90) / 100.0
    return float(m)


def _is_first_half(m):
    """전반 여부. 1~45 + 전반 추가시간(146~155)."""
    return m <= 45 or (146 <= m <= 155)


def _row(label, value, vcolor="#ffffff"):
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    l = QLabel(label); l.setStyleSheet("color:#888;font-size:12px;")
    v = QLabel(str(value)); v.setStyleSheet(f"color:{vcolor};font-size:12px;font-weight:bold;")
    v.setAlignment(Qt.AlignmentFlag.AlignRight)
    h.addWidget(l); h.addStretch(); h.addWidget(v)
    return w


# ─────────────────────────────────────────
# 경기 통계 패널 (점유율/슈팅/코너/파울/패스성공률)
# game_engine._derive_match_stats()가 만든 payload["team_stats"]를 그린다.
# 그 값들은 랜덤이 아니라 최종 스코어 + 내 세부지표를 기준으로 역산된
# 결정론적 값이라, 여기서는 그냥 표시만 한다.
# ─────────────────────────────────────────
_HOME_COLOR = "#4488ff"
_AWAY_COLOR = "#ff5566"


class _PossBar(QWidget):
    """점유율 좌우 비교 막대."""
    def __init__(self, home_pct, away_pct):
        super().__init__()
        self.home_pct = home_pct
        self.away_pct = away_pct
        self.setFixedHeight(20)

    def paintEvent(self, _ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        hw = round(w * self.home_pct / 100)
        p.fillRect(0, 0, hw, h, QColor(_HOME_COLOR))
        p.fillRect(hw, 0, w - hw, h, QColor(_AWAY_COLOR))
        p.end()


def _stat_compare_row(label, home_val, away_val):
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 3, 0, 3)
    hv = QLabel(str(home_val))
    hv.setStyleSheet(f"color:{_HOME_COLOR};font-size:13px;font-weight:bold;")
    hv.setFixedWidth(46)
    hv.setAlignment(Qt.AlignmentFlag.AlignLeft)
    lbl = QLabel(label)
    lbl.setStyleSheet("color:#999;font-size:11px;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    av = QLabel(str(away_val))
    av.setStyleSheet(f"color:{_AWAY_COLOR};font-size:13px;font-weight:bold;")
    av.setFixedWidth(46)
    av.setAlignment(Qt.AlignmentFlag.AlignRight)
    h.addWidget(hv); h.addWidget(lbl, 1); h.addWidget(av)
    return w


class MatchStatsPanel(QWidget):
    """경기 통계 인라인 패널. team_stats가 없는(예전 저장분) 경기는 안내
    문구만 보여준다 — 억지로 랜덤 값을 만들어 채우지 않는다."""

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:#161616;")
        payload = data.get("payload", {}) or {}
        team_stats = payload.get("team_stats")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        hdr = QLabel("📊 경기 통계")
        hdr.setStyleSheet("color:#fff;font-size:14px;font-weight:bold;")
        root.addWidget(hdr)

        names = QWidget(); nh = QHBoxLayout(names); nh.setContentsMargins(0, 0, 0, 0)
        hn = QLabel(data.get("home_name", "홈팀"))
        hn.setStyleSheet(f"color:{_HOME_COLOR};font-size:12px;font-weight:bold;")
        an = QLabel(data.get("away_name", "원정팀"))
        an.setStyleSheet(f"color:{_AWAY_COLOR};font-size:12px;font-weight:bold;")
        an.setAlignment(Qt.AlignmentFlag.AlignRight)
        nh.addWidget(hn); nh.addStretch(); nh.addWidget(an)
        root.addWidget(names)

        if not team_stats:
            note = QLabel("이 경기는 통계 데이터가 없습니다\n(업데이트 이전 기록입니다).")
            note.setStyleSheet("color:#555;font-size:12px;")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            note.setWordWrap(True)
            root.addStretch()
            root.addWidget(note)
            root.addStretch()
            return

        h_st, a_st = team_stats["home"], team_stats["away"]

        poss_lbl = QLabel(f"점유율   {h_st['poss']}%  -  {a_st['poss']}%")
        poss_lbl.setStyleSheet("color:#ccc;font-size:11px;")
        poss_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(poss_lbl)
        root.addWidget(_PossBar(h_st["poss"], a_st["poss"]))

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#2a2a2a;")
        root.addWidget(line)

        root.addWidget(_stat_compare_row("슈팅", h_st["shots"], a_st["shots"]))
        root.addWidget(_stat_compare_row("유효 슈팅", h_st["shots_on"], a_st["shots_on"]))
        root.addWidget(_stat_compare_row("코너킥", h_st["corners"], a_st["corners"]))
        root.addWidget(_stat_compare_row("파울", h_st["fouls"], a_st["fouls"]))
        root.addWidget(_stat_compare_row(
            "패스 성공률", f"{h_st['pass_acc']*100:.0f}%", f"{a_st['pass_acc']*100:.0f}%"))

        root.addStretch()


class MatchDetailDialog(QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("경기 상세")
        self.setStyleSheet("QDialog{background:#161616;}")
        self._data = data
        self._right_widget = None      # 현재 오른쪽에 펼쳐진 위젯(시뮬 전용)
        self._left_stats_widget = None  # 현재 왼쪽에 펼쳐진 위젯(통계 전용)

        # ── 전체 레이아웃: [왼쪽=통계 패널] [가운데=기존 상세 내용] [오른쪽=시뮬 패널] ──
        #   시뮬(오른쪽)과 완전히 대칭되는 구조 — 통계도 기존 420px 칸
        #   안에 욱여넣지 않고, 독립된 패널로 왼쪽에 펼쳐진다.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._left_container = QWidget()
        self._left_container.setStyleSheet("background:#101010;border-right:1px solid #2a2a2a;")
        self._left_layout = QVBoxLayout(self._left_container)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_container.setFixedWidth(0)  # 처음엔 접혀 있음
        outer.addWidget(self._left_container)

        left_widget = QWidget()
        left_widget.setFixedWidth(420)
        root = QVBoxLayout(left_widget)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)
        outer.addWidget(left_widget)

        self._right_container = QWidget()
        self._right_container.setStyleSheet("background:#101010;border-left:1px solid #2a2a2a;")
        self._right_layout = QVBoxLayout(self._right_container)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_container.setFixedWidth(0)  # 처음엔 접혀 있음
        outer.addWidget(self._right_container)

        self._base_height = 620
        self.setMinimumSize(420, 560)
        self.resize(420, self._base_height)

        payload = data.get("payload", {}) or {}
        events  = payload.get("events", []) or []
        detail  = payload.get("detail", {}) or {}
        played  = payload.get("played", True)
        benched = payload.get("benched", False)
        pos     = payload.get("position", "")

        is_home = bool(data.get("is_home"))
        loc = "홈" if is_home else "원정"
        rs  = {"win": "승", "draw": "무", "loss": "패"}.get(data.get("result", ""), "")
        rs_color = {"승": "#4488ff", "무": "#888888", "패": "#ff4444"}.get(rs, "#ccc")

        # ── 헤더: 리그 / 주차 / 스코어 ──────────────────────────
        head = QLabel(f"⚽ {data.get('league_name','')}  ·  "
                      f"{data.get('year','')}년 {data.get('week','')}주차  ({loc})")
        head.setStyleSheet("color:#44ccff;font-size:13px;font-weight:bold;")
        root.addWidget(head)

        score = QLabel(f"{data.get('home_name','')}  "
                       f"{data.get('home_score',0)} - {data.get('away_score',0)}  "
                       f"{data.get('away_name','')}")
        score.setStyleSheet("color:#fff;font-size:18px;font-weight:bold;")
        score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(score)

        if played:
            btn_row = QWidget()
            bh = QHBoxLayout(btn_row); bh.setContentsMargins(0, 0, 0, 0); bh.setSpacing(6)

            sim_btn = QPushButton("▶ 시뮬 보기")
            sim_btn.setStyleSheet(
                "QPushButton{background:#1a4d8f;color:#fff;border:1px solid #3a7fd5;"
                "border-radius:6px;padding:6px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#2360ad;}")
            sim_btn.clicked.connect(lambda: self._show_sim())
            bh.addWidget(sim_btn)

            stats_btn = QPushButton("📊 경기 통계")
            stats_btn.setStyleSheet(
                "QPushButton{background:#2a2a2a;color:#ccc;border:1px solid #444;"
                "border-radius:6px;padding:6px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#3a3a3a;}")
            stats_btn.clicked.connect(lambda: self._show_stats())
            bh.addWidget(stats_btn)

            root.addWidget(btn_row)

        res = QLabel(f"({rs})")
        res.setStyleSheet(f"color:{rs_color};font-size:14px;font-weight:bold;")
        res.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(res)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#2a2a2a;")
        root.addWidget(line)

        # ── 출전하지 않은 경기 ────────────────────────────────
        if not played:
            msg = QLabel("🪑 벤치 대기" if benched else "🚑 부상 결장")
            msg.setStyleSheet("color:#888;font-size:14px;")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(msg)
            root.addStretch()
            self._add_close(root)
            return

        # ── 내 기록 요약 ──────────────────────────────────────
        if pos == "GK":
            summary = QLabel(f"평점 {data.get('rating',0)}   선방 {data.get('saves',0)}")
        else:
            summary = QLabel(f"평점 {data.get('rating',0)}   "
                             f"골 {data.get('goals',0)}   어시 {data.get('assists',0)}")
        summary.setStyleSheet("color:#ffcc00;font-size:14px;font-weight:bold;")
        summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(summary)

        # ── 타임라인 (스크롤) ─────────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:1px solid #2a2a2a;border-radius:6px;"
                             "background:#1a1a1a;}"
                             "QScrollBar:vertical{background:#1a1a1a;width:6px;}"
                             "QScrollBar::handle:vertical{background:#3a3a3a;border-radius:3px;}")
        inner = QWidget(); iv = QVBoxLayout(inner)
        iv.setContentsMargins(10, 8, 10, 8); iv.setSpacing(3)

        # 전반 = 1~45 + 전반 추가시간(146~155). 그 외는 후반. 각 반은 시간순 정렬.
        fh = sorted([(m, t) for m, t in events if _is_first_half(m)],
                    key=lambda x: _min_sortkey(x[0]))
        sh = sorted([(m, t) for m, t in events if not _is_first_half(m)],
                    key=lambda x: _min_sortkey(x[0]))

        def add_half(title, items):
            hdr = QLabel(title)
            hdr.setStyleSheet("color:#66aaff;font-size:11px;font-weight:bold;"
                              "padding-top:4px;")
            iv.addWidget(hdr)
            if not items:
                e = QLabel("   특별한 장면 없음")
                e.setStyleSheet("color:#555;font-size:11px;")
                iv.addWidget(e)
            for m, t in items:
                if any(x in t for x in ("⚽","🅰","🎩","🔥","🧤","🧱","🏆","🎯")):
                    color = "#ffcc00"
                elif any(x in t for x in ("🛡","🔑","🌪","↗","💪")):
                    color = "#44ccff"   # 수비/창조 활약 — 하늘색
                elif any(x in t for x in ("😞","🟥","🥅","⚠","😤")):
                    color = "#ff6666"
                else:
                    color = "#cccccc"
                row = QLabel(f"  {_fmt_min(m)}'  {t}")
                row.setStyleSheet(f"color:{color};font-size:12px;")
                row.setWordWrap(True)
                iv.addWidget(row)

        add_half("⏱ 전반", fh)
        add_half("⏱ 후반", sh)
        iv.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # ── 세부 지표 ─────────────────────────────────────────
        stat_box = QWidget()
        sv = QVBoxLayout(stat_box); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(2)
        st_hdr = QLabel("📊 세부 지표")
        st_hdr.setStyleSheet("color:#888;font-size:11px;font-weight:bold;")
        sv.addWidget(st_hdr)
        pa = detail.get("pass_acc", 0.0)
        pa_str = f"{pa*100:.0f}%" if pa else "-"
        shots     = detail.get("shots", 0)
        shots_on  = detail.get("shots_on", 0)
        key_passes= detail.get("key_passes", 0)
        dribbles  = detail.get("dribbles", 0)
        blocks    = detail.get("blocks", 0)
        saves_det = data.get("saves", 0)

        from constants import position_group
        pos_grp = position_group(pos)

        if pos == "GK":
            # GK: 선방수 + 선방률 + 패스%
            tot_shots = saves_det + (data.get("away_score",0) if data.get("is_home") else data.get("home_score",0))
            sr_str = f"{saves_det}/{tot_shots} ({saves_det*100//tot_shots if tot_shots else 0}%)" if tot_shots else f"{saves_det}"
            sv.addWidget(_row("선방 (유효슈팅)", sr_str, "#44ccff"))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos in ("CB",):
            # CB: 차단 우선, 헤딩 클리어 개념, 패스%
            sv.addWidget(_row("차단 (태클·인터셉트)", str(blocks), "#44ff88" if blocks >= 3 else "#fff"))
            sv.addWidget(_row("패스 성공률", pa_str))
            sv.addWidget(_row("슈팅", str(shots)))

        elif pos in ("CDM",):
            # CDM: 차단 + 키패스(전방연결) + 패스%
            sv.addWidget(_row("차단 (태클·인터셉트)", str(blocks), "#44ff88" if blocks >= 3 else "#fff"))
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes)))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos in ("LB", "RB"):
            # LB/RB: 어시 창출(키패스) + 차단 + 패스%
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes), "#44ccff" if key_passes >= 2 else "#fff"))
            sv.addWidget(_row("차단 (태클·인터셉트)", str(blocks)))
            sv.addWidget(_row("드리블 성공", str(dribbles)))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos in ("CM",):
            # CM: 키패스 + 차단 + 드리블 + 패스%
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes)))
            sv.addWidget(_row("차단 (태클·인터셉트)", str(blocks)))
            sv.addWidget(_row("드리블 성공", str(dribbles)))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos == "CAM":
            # CAM: 키패스 우선, 드리블, 슈팅, 패스%
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes), "#44ccff" if key_passes >= 3 else "#fff"))
            sv.addWidget(_row("드리블 성공", str(dribbles)))
            sv.addWidget(_row("슈팅 (유효)", f"{shots} ({shots_on})"))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos in ("LW", "RW"):
            # LW/RW: 드리블 + 키패스 + 슈팅 + 패스%
            sv.addWidget(_row("드리블 성공", str(dribbles), "#44ccff" if dribbles >= 4 else "#fff"))
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes)))
            sv.addWidget(_row("슈팅 (유효)", f"{shots} ({shots_on})"))
            sv.addWidget(_row("패스 성공률", pa_str))

        else:  # ST/CF 및 기타
            # ST/CF: 슈팅 우선, 키패스, 드리블
            sv.addWidget(_row("슈팅 (유효)", f"{shots} ({shots_on})", "#ffcc44" if shots >= 4 else "#fff"))
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes)))
            sv.addWidget(_row("드리블 성공", str(dribbles)))
            sv.addWidget(_row("패스 성공률", pa_str))
        root.addWidget(stat_box)

        # ── 총평 ──────────────────────────────────────────────
        verdict = payload.get("verdict", "")
        if verdict:
            v = QLabel(verdict)
            v.setStyleSheet("color:#fff;font-size:13px;font-weight:bold;"
                            "background:#222;border-radius:6px;padding:8px;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setWordWrap(True)
            root.addWidget(v)

        self._add_close(root)

    # ── 오른쪽 패널 펼치기/접기 ──────────────────────────────
    def _clear_right_panel(self):
        """오른쪽에 떠 있던 위젯을 치운다. 시뮬 뷰어였다면 QTimer부터 반드시
        멈춘다 — 안 그러면 화면에서 사라진 뒤에도 백그라운드에서 계속 돌면서
        불필요하게 CPU를 먹거나, 이미 지워진 위젯을 참조하다 에러가 날 수 있다.
        시뮬 뷰어는 결정론적 재생을 위해 열려있는 동안 전역 random 상태를
        고정해두는데(_pre_seed_rng_state), 패널 전환 시엔 closeEvent가 안
        불리므로 여기서도 직접 복원해줘야 한다."""
        if self._right_widget is not None:
            timer = getattr(self._right_widget, "timer", None)
            if timer is not None:
                timer.stop()
            pre_seed_state = getattr(self._right_widget, "_pre_seed_rng_state", None)
            if pre_seed_state is not None:
                import random
                random.setstate(pre_seed_state)
            self._right_layout.removeWidget(self._right_widget)
            self._right_widget.setParent(None)
            self._right_widget.deleteLater()
            self._right_widget = None

    def _open_right_panel(self, widget, width=760):
        """[시뮬 전용] 오른쪽 패널은 이제 시뮬 뷰어만 사용한다."""
        self._clear_right_panel()
        self._right_widget = widget
        self._right_layout.addWidget(widget)
        self._right_container.setFixedWidth(width)
        self._resize_for_content()

    def _clear_left_stats(self):
        """왼쪽 통계 패널 비우기."""
        if self._left_stats_widget is not None:
            self._left_layout.removeWidget(self._left_stats_widget)
            self._left_stats_widget.setParent(None)
            self._left_stats_widget.deleteLater()
            self._left_stats_widget = None
        self._left_container.setFixedWidth(0)

    def _open_left_panel(self, widget, width=300):
        """[통계 전용] 왼쪽 패널 — 오른쪽 시뮬 패널과 완전히 대칭 구조."""
        self._clear_left_stats()
        self._left_stats_widget = widget
        self._left_layout.addWidget(widget)
        self._left_container.setFixedWidth(width)
        self._resize_for_content()

    def _resize_for_content(self):
        """현재 왼쪽(통계 유무)·오른쪽(시뮬 유무) 상태에 맞춰 다이얼로그
        너비를 다시 계산한다. 통계·시뮬 모두 각자 고정폭 패널로 독립돼
        있어서(기존 420px 칸은 안 건드림) 높이는 항상 기본값 그대로다."""
        left_w = 300 if self._left_stats_widget is not None else 0
        right_w = 760 if self._right_widget is not None else 0
        new_w = left_w + 420 + right_w
        self.setMinimumSize(420, self._base_height)
        self.resize(new_w, self._base_height)

    def _show_sim(self):
        from ui.match_sim_viewer import MatchSimViewer
        sim_widget = MatchSimViewer(self._data, self)
        # [핵심] 새 창(QDialog.show()) 대신 이 다이얼로그 오른쪽에 인라인으로
        # 붙인다 — windowFlags를 Widget으로 바꾸면 독립된 창 대신 평범한
        # 자식 위젯처럼 레이아웃에 들어간다. 내부 로직은 그대로 재사용.
        sim_widget.setWindowFlags(Qt.WindowType.Widget)
        self._open_right_panel(sim_widget, width=760)
        # [신규] 시뮬 보기가 켜지면 팀 경기 통계도 왼쪽에 독립 패널로 띄운다.
        #   (통계=왼쪽 별도 패널, 상세 정보=가운데 420px, 시뮬=오른쪽 별도 패널)
        if self._left_stats_widget is None:
            self._open_left_panel(MatchStatsPanel(self._data, self), width=300)

    def _show_stats(self):
        """"경기 통계"는 시뮬 표시 여부와 무관하게 항상 왼쪽 독립 패널로
        뜬다 — 오른쪽 패널은 시뮬 전용이라 여기서 건드리지 않는다. 시뮬이
        이미 오른쪽에 떠 있다면 그대로 유지된 채 왼쪽에 통계만 추가/갱신."""
        self._open_left_panel(MatchStatsPanel(self._data, self), width=300)

    def closeEvent(self, event):
        self._clear_right_panel()
        self._clear_left_stats()
        super().closeEvent(event)

    def _add_close(self, root):
        btn = QPushButton("닫기")
        btn.setStyleSheet("QPushButton{background:#2a2a2a;color:#ccc;border:none;"
                          "border-radius:6px;padding:8px;font-size:12px;}"
                          "QPushButton:hover{background:#3a3a3a;}")
        btn.clicked.connect(self.accept)
        root.addWidget(btn)