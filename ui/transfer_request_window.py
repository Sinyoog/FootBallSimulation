"""ui/transfer_request_window.py — [2026-11-21 신설, 신민용 확정] 내 선수 이적 요청 창.

center_panel의 "📣 이적 요청" 버튼(임대 신청 옆)이 이 창을 연다. 임대 창(ui/loan_window.py)과 같은 모양 —
상태를 그대로 보여주고 제출만 전달한다. 규칙은 전부 엔진(game_engine.transfer_request_status / request_transfer):
  · 내가 수락한 오퍼를 원 소속 구단이 거절했을 때만, 그 오퍼 1건에 1번, 같은 오퍼 창 안에서만 낼 수 있다.
  · 구단은 거절했던 그 오퍼(금액·조건 그대로)를 다시 판단한다. 다시 거절되면 잔류.

어려움 난이도: 구단 최소 요구액은 숨긴다(정확한 판정 수치라서 — 다른 화면의 정보 제한과 같은 취지).
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
from PyQt6.QtCore import Qt

from game_engine import (get_player, is_hard_mode, fmt_money, transfer_request_status, request_transfer)
from constants import TRANSFER_REQUEST_REL_PENALTY


def transfer_button_label(p=None):
    """메인 화면 버튼 문구 — 볼 것이 있으면 표시(거절된 오퍼·요청 결과)."""
    st = transfer_request_status(p)
    r = st["refused"]
    if not r:
        return "📣 이적 요청"
    res = r.get("result") or ""
    if res == "approved":
        return "📣 이적 요청 (승인)"
    if res == "denied":
        return "📣 이적 요청 (거절됨)"
    return "📣 이적 요청 (구단 거절)"


def transfer_button_state(p=None):
    """(활성 여부, 툴팁). 무소속·복무 중·임대 중·판매 예약 중에는 누를 수 없다. 그 외에는 열 수 있고,
    실제 제출 가능 여부는 창 안에서 엔진 판정 그대로 보여준다(임대 버튼과 같은 방식)."""
    st = transfer_request_status(p)
    if st["block_reason"]:
        return False, st["block_reason"]
    if st["can_submit"]:
        return True, "구단이 거절한 오퍼에 대해 이적을 공식 요청할 수 있습니다."
    return True, st["reason"]


class TransferRequestDialog(QDialog):
    def __init__(self, parent=None, style=""):
        super().__init__(parent)
        self.setWindowTitle("📣 이적 요청")
        self.setMinimumWidth(480)
        if style:
            self.setStyleSheet(style)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 16, 18, 16)
        self.root.setSpacing(10)
        self._error = ""
        self._build()

    # ── 공통 위젯(임대 창과 같은 구성) ─────────────────────────
    def _clear(self):
        while self.root.count():
            it = self.root.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            elif it.layout() is not None:
                self._clear_layout(it.layout())

    def _clear_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
            elif it.layout() is not None:
                self._clear_layout(it.layout())

    def _header(self, text):
        h = QLabel(text)
        h.setObjectName("dlgHeader")
        self.root.addWidget(h)

    def _note(self, text, color="#888"):
        n = QLabel(text)
        n.setWordWrap(True)
        n.setStyleSheet(f"color:{color};font-size:11px;")
        self.root.addWidget(n)

    def _buttons(self, specs):
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, obj, cb, enabled in specs:
            b = QPushButton(text)
            if obj:
                b.setObjectName(obj)
            b.setEnabled(enabled)
            b.clicked.connect(cb)
            row.addWidget(b, 1)
        self.root.addLayout(row)

    def _offer_card(self, r, show_after=False):
        o = r.get("offer") or {}
        card = QFrame()
        card.setObjectName("dlgCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(4)
        cl.addWidget(QLabel(f"<b style='color:#fff;font-size:14px'>{o.get('flag', '')} {o.get('team_name', '')}</b>"))
        _lg = o.get("league_name") or ""
        cl.addWidget(QLabel(f"<span style='color:#bbb'>{_lg} ({o.get('country', '')}, {o.get('tier', '')}부)</span>"))
        if o.get("role"):
            cl.addWidget(QLabel(f"<span style='color:#bbb'>예상 역할</span> <b style='color:#00cc66'>{o['role']}</b>"))
        cl.addWidget(QLabel(f"<span style='color:#bbb'>이적료</span> <b style='color:#ffcc33'>"
                            f"{fmt_money(o.get('transfer_fee', 0))}</b>  "
                            f"<span style='color:#bbb'>연봉</span> <b style='color:#fff'>"
                            f"{fmt_money(o.get('salary', 0))} / 년</b>"))
        if not is_hard_mode():
            _ma = r.get("min_accept_after") if show_after and r.get("min_accept_after") else r.get("min_accept")
            if _ma:
                _lbl = "재검토 후 구단 최소 요구" if show_after and r.get("min_accept_after") else "구단 최소 요구"
                cl.addWidget(QLabel(f"<span style='color:#888;font-size:11px'>{_lbl} {fmt_money(_ma)}</span>"))
        return card

    # ── 상태별 화면 ────────────────────────────────────────────
    def _build(self):
        self._clear()
        p = get_player() or {}
        st = transfer_request_status(p)
        err, self._error = self._error, ""
        r = st["refused"]
        if st["block_reason"]:
            self._build_blocked(st["block_reason"])
        elif r and r.get("result"):
            self._build_result(r)
        elif r:
            self._build_refused(r, st)
        else:
            self._build_idle()
        if err:
            self._note(f"⚠ {err}", "#ff8866")

    def _build_blocked(self, why):
        self._header("📣 이적 요청")
        self._note(f"⚠ {why}", "#ff8866")
        self._buttons([("닫기", "", self.accept, True)])

    def _build_idle(self):
        self._header("📣 이적 요청")
        self._note("이적 요청은 내가 수락한 다른 구단의 오퍼를 지금 구단이 거절했을 때, 그 오퍼에 대해 "
                   "구단에 공식적으로 이적을 요구하는 행동입니다. 구단은 거절했던 오퍼를 같은 조건으로 다시 "
                   "판단합니다.", "#bbb")
        self._note("지금은 구단이 거절한 오퍼가 없습니다. 이적 기간에 오퍼를 수락했는데 구단이 거절하면 "
                   "이 창에서 요청할 수 있습니다.", "#bbb")
        self._buttons([("📣 이적 요청 제출", "dlgOk", lambda: None, False),
                       ("닫기", "", self.accept, True)])

    def _build_refused(self, r, st):
        self._header("🚫 구단이 이적을 거절했습니다")
        self.root.addWidget(self._offer_card(r))
        self._note(f"· 이적을 요청하면 감독 관계가 약 {TRANSFER_REQUEST_REL_PENALTY} 떨어집니다.", "#bbb")
        self._note("· 구단은 이 오퍼를 같은 조건으로 다시 판단합니다. 팀 내 비중이 클수록 잘 보내주지 않습니다.", "#bbb")
        self._note("· 다시 거절되면 현 소속팀에 남고, 이 오퍼로는 더 요청할 수 없습니다.", "#bbb")
        self._note("· 같은 구단에서 3시즌 안에 두 번째 요청이면 구단이 보유 의향을 재평가합니다.", "#bbb")
        self._note("※ 이번 이적 기간이 지나면 이 오퍼에 대한 요청은 사라집니다.")
        if not st["can_submit"] and st["reason"]:
            self._note(f"⚠ {st['reason']}", "#ff8866")

        def _submit():
            res = request_transfer()
            if not res.get("ok"):
                self._error = res.get("msg", "")
            self._build()
        self._buttons([("📣 이적 요청 제출", "dlgOk", _submit, st["can_submit"]),
                       ("닫기", "", self.accept, True)])

    def _build_result(self, r):
        if r.get("result") == "approved":
            self._header("✅ 구단이 이적 요청을 받아들였습니다")
            self.root.addWidget(self._offer_card(r, show_after=True))
            self._note("이적이 예약됐습니다. 다음 이동 시점(프리시즌 2주 / 겨울 27주)에 합류합니다.", "#bbb")
            self._note("※ 이 기간에 다른 오퍼를 수락하면 예약은 마지막에 고른 팀으로 바뀝니다.")
        else:
            self._header("🚫 구단이 이적 요청을 거절했습니다")
            self.root.addWidget(self._offer_card(r, show_after=True))
            self._note("현 소속팀에 남습니다. 이 오퍼로는 더 요청할 수 없습니다.", "#bbb")
        self._buttons([("닫기", "", self.accept, True)])