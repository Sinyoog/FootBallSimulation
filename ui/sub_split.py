"""[2026-11-26 신설, 신민용 요청: "부포 표시하는 상자를 절반으로 나눠서 좌측부터 채우고"]
부포 2개를 한 칸 안에 반반으로 보여주는 공용 도우미.

· 표(QTableWidget) 칸: apply(item, subs)로 칸에 두 절반 값을 실어 두고, 그 열(또는 표)에 SubSplitDelegate를
  걸면 칸을 세로선으로 반 나눠 왼쪽 = 부포 1, 오른쪽 = 부포 2로 그린다. 부포가 1개면 왼쪽만 차고 오른쪽은 "-",
  없으면 둘 다 "-". 마우스를 올린 쪽 절반의 설명이 툴팁으로 뜬다(다른 칸 부포 / 같은 칸 부포).
· 같은 칸 부포(전술 활용, 예 LW 주포의 LM)는 반 칸에 "(전술 활용)"까지 넣으면 잘려서 "LM*"로 줄이고,
  설명은 툴팁에 넣는다.
· 칸 텍스트(DisplayRole)는 "LM | RB"로 그대로 둔다 — 복사(Ctrl+C)·정렬·폭 계산은 예전처럼 이 글자를 쓴다.
SPLIT_ROLE 값이 없는 칸은 기본 그리기 그대로라, 열 전체에 걸어도 다른 줄에는 영향이 없다.
"""
from PyQt6.QtCore import Qt, QRect, QSize, QEvent
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication, QToolTip

SPLIT_ROLE = Qt.ItemDataRole.UserRole + 731
SAME_MARK = "*"
_DIVIDER = "#3a3a3a"
_PAD = 4

TIP_NONE = "부포 없음"
TIP_PLAIN = "이 자리에서 뛸 수 있는 부포지션 — 부포에서는 OVR이 1 낮은 것과 같습니다"


def _tips():
    try:
        import sub_positions as _sp
        return getattr(_sp, "TIP_CROSS", TIP_PLAIN), getattr(_sp, "TIP_SAME", TIP_PLAIN)
    except Exception:
        return TIP_PLAIN, TIP_PLAIN


def half_text(pos, kind):
    """반 칸에 넣을 글자 — 없으면 "-", 같은 칸 부포는 "LM*"."""
    if not pos:
        return "-"
    return f"{pos}{SAME_MARK}" if kind == "same" else pos


def half_tip(pos, kind):
    if not pos:
        return TIP_NONE
    cross, same = _tips()
    if kind == "same":
        return f"{pos} — " + same
    if kind == "cross":
        return f"{pos} — " + cross
    return f"{pos} — " + TIP_PLAIN


def halves(subs):
    """subs: [(포지션, 종류)] (sub_positions.display_subs 형식) → [(글자, 툴팁, 있음?)] 2개."""
    subs = list(subs or [])[:2]
    out = []
    for i in range(2):
        p, k = subs[i] if i < len(subs) else (None, None)
        out.append((half_text(p, k), half_tip(p, k), bool(p)))
    return out


def joined(subs):
    """복사·폭 계산용 한 줄 글자 — "LM | RB", 하나면 "LM", 없으면 "-"."""
    hs = [h[0] for h in halves(subs) if h[2]]
    return " | ".join(hs) if hs else "-"


def subs_of(player_id, main=None, me=False, fallback=None):
    """선수의 부포 [(포지션, 종류)] — 새 sub_positions(display_subs, 부포 2개)면 그걸, 예전 버전이면 부포 1개(get·my_sub)를
    쓴다. 어느 쪽도 없으면 fallback(이미 가진 부포 값) 하나. UI만 먼저 바꿔도 예전·새 게임 코드 둘 다에서 돈다."""
    try:
        import sub_positions as _sp
        key = _sp.MY_ID if me else player_id
        if hasattr(_sp, "display_subs"):
            if me or isinstance(key, int):
                r = _sp.display_subs(key, main or "")
                if r:
                    return list(r)
        else:
            s = _sp.my_sub() if me else (_sp.get(player_id) if isinstance(player_id, int) else None)
            if s:
                return [(s, None)]
    except Exception:
        pass
    return [(fallback, None)] if fallback else []


def apply(item, subs, color="#cccccc", empty_color="#666666"):
    """표 칸(QTableWidgetItem)에 부포 두 절반 값을 싣는다. 그리기는 SubSplitDelegate가 한다."""
    data = [(t, (color if has else empty_color), tip) for t, tip, has in halves(subs)]
    item.setData(SPLIT_ROLE, data)
    item.setText(joined(subs))
    item.setToolTip("")
    return item


class SubSplitDelegate(QStyledItemDelegate):
    """SPLIT_ROLE이 있는 칸만 반으로 나눠 그린다(없으면 기본 그리기)."""

    def paint(self, painter, option, index):
        data = index.data(SPLIT_ROLE)
        if not data:
            return super().paint(painter, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        w = opt.widget
        style = w.style() if w is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, w)   # 배경·선택 표시
        r = option.rect
        half = r.width() // 2
        al = index.data(Qt.ItemDataRole.TextAlignmentRole)
        try:
            al = Qt.AlignmentFlag(int(al)) if al is not None else None
        except Exception:
            al = None
        if al is None:
            al = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(QColor(_DIVIDER))
        painter.drawLine(r.left() + half, r.top() + 3, r.left() + half, r.bottom() - 3)
        fm = opt.fontMetrics
        for i, (txt, col, _tip) in enumerate(data[:2]):
            x0 = r.left() + (0 if i == 0 else half)
            wid = half if i == 0 else r.width() - half
            hr = QRect(x0 + _PAD, r.top(), max(0, wid - 2 * _PAD), r.height())
            painter.setPen(QColor(col))
            painter.drawText(hr, al, fm.elidedText(txt, Qt.TextElideMode.ElideRight, hr.width()))
        painter.restore()

    def sizeHint(self, option, index):
        base = super().sizeHint(option, index)
        data = index.data(SPLIT_ROLE)
        if not data:
            return base
        fm = option.fontMetrics
        w = 2 * (max(fm.horizontalAdvance(t) for t, _c, _tp in data[:2]) + 2 * _PAD + 6)
        return QSize(max(base.width(), w), base.height())

    def helpEvent(self, event, view, option, index):
        data = index.data(SPLIT_ROLE) if index.isValid() else None
        if not data or event.type() != QEvent.Type.ToolTip:
            return super().helpEvent(event, view, option, index)
        r = option.rect
        i = 0 if event.pos().x() < r.left() + r.width() // 2 else 1
        tip = data[i][2] if i < len(data) else ""
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
        else:
            QToolTip.hideText()
        return True