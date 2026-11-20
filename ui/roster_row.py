# -*- coding: utf-8 -*-
"""ui/roster_row.py — 포메이션 명단 줄의 칸 나누기 (2026-11 신설, 신민용 요청)

신민용 요청: "포메이션 명단 한 줄을 3칸으로 — 포지션 | 이름 | OVR. 포지션 칸은 CAM 등 3글자,
OVR 칸은 최대 100(3자리)이 들어가는 크기에 맞추고, 이름은 그 두 칸을 빼고 남는 공간 전부.
감독도 이름 | OVR | 국적으로 나눈다. 다만 글자만 나눠 보이는 것이고, 선택은 한 줄이 하나로 묶여서."

구현 원칙
  · 줄 위젯(host — 메인 화면은 QPushButton, 세계 기록실은 QLabel) 자체는 그대로 두고, 그 안에 칸 라벨을
    가로로 얹는다. 칸 라벨은 마우스를 통과시키므로(WA_TransparentForMouseEvents) 클릭·선택·하이라이트·
    WASD 이동은 예전처럼 줄 전체 하나로 동작한다. host의 글자(setText)는 비워 두고 칸에만 쓴다.
  · 색·글자 크기·굵기는 host 스타일시트(선택자 없는 선언 → 자식에게도 적용)를 그대로 따른다 — 선택
    하이라이트(파란 글자)·나·부상 색이 칸 글자에도 똑같이 먹는다. 칸 라벨은 배경·테두리·여백만 지운다.
  · 고정 칸 폭은 그 줄 글꼴(굵게) 기준으로 견본 글자("CAM", "100")가 딱 들어가는 폭.
  · 이름 칸이 모자라면 끝을 "…"로 줄인다.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QApplication

POS_SAMPLE = "CAM"     # 포지션 칸 기준 글자(가장 긴 포지션 약어 3자)
OVR_SAMPLE = "100"     # OVR 칸 기준 글자(최대값)
_CHILD_QSS = "background:transparent;border:none;padding:0px;margin:0px;"


class _ElidedLabel(QLabel):
    """폭이 모자라면 끝을 …로 줄이는 라벨(전체 글자는 full_text로 보관)."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def full_text(self):
        return self._full

    def set_full_text(self, text):
        self._full = text or ""
        self._refit()

    def _refit(self):
        w = self.width()
        fm = self.fontMetrics()
        if w <= 0 or fm.horizontalAdvance(self._full) <= w:
            QLabel.setText(self, self._full)
        else:
            QLabel.setText(self, fm.elidedText(self._full, Qt.TextElideMode.ElideRight, w))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refit()

    def changeEvent(self, e):
        super().changeEvent(e)
        self._refit()

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        s.setWidth(0)
        return s


def _fixed_width(sample, font_px, bold=True):
    f = QFont(QApplication.font())
    f.setPixelSize(int(font_px))
    f.setBold(bool(bold))
    # 견본 글자 폭 + 약간의 여유(굵은 글꼴 반올림 오차)
    return QFontMetrics(f).horizontalAdvance(sample) + 2


def install_columns(host, specs, font_px, margins=(8, 4, 8, 4), spacing=8):
    """host 위젯 안에 칸 라벨을 가로로 놓는다.
    specs: [(키, 고정폭 견본 글자 또는 None(=남는 폭 전부), 정렬)] — 남는 폭 칸은 하나만.
    반환: {키: 라벨}. host._roster_cols에도 보관한다(set_columns가 쓴다)."""
    lay = QHBoxLayout(host)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    cols = {}
    for key, sample, align in specs:
        if sample is None:
            lbl = _ElidedLabel(parent=host)
            lay.addWidget(lbl, 1)
        else:
            lbl = QLabel(parent=host)
            lbl.setFixedWidth(_fixed_width(sample, font_px))
            lay.addWidget(lbl, 0)
        lbl.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet(_CHILD_QSS)
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        cols[key] = lbl
    host._roster_cols = cols
    return cols


def set_columns(host, **values):
    """칸 글자를 바꾼다(없는 칸은 무시). 남는 폭 칸(이름)은 모자라면 …로 줄여 보인다."""
    cols = getattr(host, "_roster_cols", None) or {}
    for k, v in values.items():
        lbl = cols.get(k)
        if lbl is None:
            continue
        v = "" if v is None else str(v)
        if isinstance(lbl, _ElidedLabel):
            lbl.set_full_text(v)
        else:
            lbl.setText(v)


def set_column_color(host, key, color=None):
    """[2026-11] 칸 하나의 글자색만 따로 정한다(None이면 줄 색을 그대로 따른다).
    칸 자신의 스타일시트가 줄 스타일시트보다 우선하므로, 줄이 선택돼 파랗게 바뀌어도 이 색은 유지된다."""
    lbl = (getattr(host, "_roster_cols", None) or {}).get(key)
    if lbl is None:
        return
    lbl.setStyleSheet(_CHILD_QSS + (f"color:{color};" if color else ""))


def column_text(host, key):
    lbl = (getattr(host, "_roster_cols", None) or {}).get(key)
    if lbl is None:
        return ""
    return lbl.full_text() if isinstance(lbl, _ElidedLabel) else lbl.text()


# ── 표준 줄 두 가지 ───────────────────────────────────────────────
_L = Qt.AlignmentFlag.AlignLeft
_R = Qt.AlignmentFlag.AlignRight
_C = Qt.AlignmentFlag.AlignHCenter

PLAYER_SPECS = (("pos", POS_SAMPLE, _L), ("name", None, _L), ("ovr", OVR_SAMPLE, _R))
MANAGER_SPECS = (("name", None, _L), ("ovr", OVR_SAMPLE, _R), ("nat", "", _L))


def install_player_columns(host, pos, name, ovr, font_px, tooltip=None):
    """선수 줄: 포지션 | 이름 | OVR."""
    install_columns(host, PLAYER_SPECS, font_px)
    set_player_columns(host, pos, name, ovr)
    if tooltip is not None:
        host.setToolTip(tooltip)


def set_player_columns(host, pos=None, name=None, ovr=None):
    vals = {}
    if pos is not None:
        vals["pos"] = pos
    if name is not None:
        vals["name"] = name
    if ovr is not None:
        vals["ovr"] = ovr
    set_columns(host, **vals)


def install_manager_columns(host, name, ovr, nat, font_px):
    """감독 줄: 이름 | OVR | 국적. 국적 칸은 그 글자 폭에 맞춘다(이름이 남는 폭)."""
    cols = install_columns(host, MANAGER_SPECS, font_px)
    set_manager_columns(host, name, ovr, nat, font_px)
    return cols


def set_manager_columns(host, name, ovr, nat, font_px):
    cols = getattr(host, "_roster_cols", None) or {}
    nat_lbl = cols.get("nat")
    if nat_lbl is not None:
        nat_lbl.setFixedWidth(_fixed_width(nat or "", font_px) if nat else 0)
    set_columns(host, name=name, ovr=ovr, nat=nat)