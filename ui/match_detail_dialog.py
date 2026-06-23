"""
ui/match_detail_dialog.py  ─  로그에서 경기 헤더 클릭 시 뜨는 상세 창

game_engine.get_match_detail(id) 가 돌려주는 dict 를 받아
전/후반 타임라인 · 평점 · 세부 지표 · 총평을 보기 좋게 펼쳐 보여준다.
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QWidget, QFrame, QPushButton)
from PyQt6.QtCore import Qt


def _fmt_min(m):
    """정렬용 분(정수) → 표시 문자열. 전반 추가시간 146~152=45+1~7, 후반 91~98=90+1~8."""
    try:
        m = int(m)
    except (ValueError, TypeError):
        return str(m)
    if 146 <= m <= 152:
        return f"45+{m-145}"
    if 91 <= m <= 98:
        return f"90+{m-90}"
    return str(m)


def _min_sortkey(m):
    """실제 경기 시간 정렬 키. 전반 추가시간→45.x, 후반 추가시간→90.x."""
    try:
        m = int(m)
    except (ValueError, TypeError):
        return 0.0
    if 146 <= m <= 152:
        return 45 + (m - 145) / 10.0
    if 91 <= m <= 98:
        return 90 + (m - 90) / 10.0
    return float(m)


def _is_first_half(m):
    """전반 여부. 1~45 + 전반 추가시간(146~152)."""
    return m <= 45 or (146 <= m <= 152)


def _row(label, value, vcolor="#ffffff"):
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    l = QLabel(label); l.setStyleSheet("color:#888;font-size:12px;")
    v = QLabel(str(value)); v.setStyleSheet(f"color:{vcolor};font-size:12px;font-weight:bold;")
    v.setAlignment(Qt.AlignmentFlag.AlignRight)
    h.addWidget(l); h.addStretch(); h.addWidget(v)
    return w


class MatchDetailDialog(QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("경기 상세")
        self.setMinimumSize(420, 560)
        self.setStyleSheet("QDialog{background:#161616;}")

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

        root = QVBoxLayout(self); root.setContentsMargins(16, 16, 16, 12); root.setSpacing(10)

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

        # 전반 = 1~45 + 전반 추가시간(146~152). 그 외는 후반. 각 반은 시간순 정렬.
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
                color = "#ffcc00" if ("⚽" in t or "🅰" in t or "🎩" in t or
                                       "🔥" in t or "🧤" in t or "🧱" in t) else "#cccccc"
                if "😞" in t or "🟥" in t or "🥅" in t:
                    color = "#ff6666"
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
        if pos == "GK":
            sv.addWidget(_row("패스 성공률", pa_str))
        else:
            sv.addWidget(_row("슈팅 (유효)", f"{detail.get('shots',0)} ({detail.get('shots_on',0)})"))
            sv.addWidget(_row("기회 창출(키패스)", detail.get("key_passes", 0)))
            sv.addWidget(_row("드리블 성공", detail.get("dribbles", 0)))
            sv.addWidget(_row("차단(태클·인터셉트)", detail.get("blocks", 0)))
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

    def _add_close(self, root):
        btn = QPushButton("닫기")
        btn.setStyleSheet("QPushButton{background:#2a2a2a;color:#ccc;border:none;"
                          "border-radius:6px;padding:8px;font-size:12px;}"
                          "QPushButton:hover{background:#3a3a3a;}")
        btn.clicked.connect(self.accept)
        root.addWidget(btn)