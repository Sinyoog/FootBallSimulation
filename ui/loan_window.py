"""ui/loan_window.py — [2026-10 신설, 신민용 확정] 내 선수 임대 창.

center_panel의 "🔄 임대 신청" 버튼(팀 입단과 순위표 사이)과, 구단 임대 추진 제안이 올 때
뜨는 팝업이 모두 이 창 하나를 쓴다. 엔진 규칙은 my_loan.py가 전부 정하고, 이 창은
그 상태를 그대로 보여주고 수락/거부/제출/거두기만 전달한다(UI와 엔진 판정 일치).

어려움 난이도: 임대처 팀 평균 OVR은 숨긴다(다른 화면들의 OVR 비표시 규칙과 같음).
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QFrame, QScrollArea, QWidget)
from PyQt6.QtCore import Qt

import my_loan as ml
from game_engine import get_player, get_state, is_hard_mode


_PURPOSE_TEXT = {
    "육성": "출전 경험을 쌓게 하려는 육성 임대",
    "정리": "다음 시즌 계획에서 비중이 낮은 선수의 임대",
    "희망": "선수 본인이 원한 임대",
}


def _return_text(o):
    ry, rh = o.get("return_year"), o.get("return_half")
    if not ry:
        return ""
    return f"{ry}년 {'하반기 시작(29주)' if rh == 1 else '시즌 끝'} 복귀"


def _move_text(o):
    ey, ew = o.get("exec_year"), o.get("exec_week")
    if not ey:
        return ""
    return f"{ey}년 {ew}주 합류({'상반기 시작' if ew == 4 else '하반기 시작'})"


class LoanDialog(QDialog):
    """mode="status": 버튼으로 연 창(현재 상태 전부), mode="club": 구단 제안 팝업."""

    def __init__(self, parent=None, style="", mode="status"):
        super().__init__(parent)
        self.mode = mode
        self.setWindowTitle("🔄 임대")
        self.setMinimumWidth(480)
        if style:
            self.setStyleSheet(style)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 16, 18, 16)
        self.root.setSpacing(10)
        self._build()

    # ── 공통 위젯 ──────────────────────────────────────────────
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

    def _offer_card(self, o, accept_cb=None):
        card = QFrame()
        card.setObjectName("dlgCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(4)
        cl.addWidget(QLabel(f"<b style='color:#fff;font-size:14px'>{o.get('flag', '')} {o['team_name']}</b>"))
        cl.addWidget(QLabel(f"<span style='color:#bbb'>{o['league_name']} ({o['country']}, {o['tier']}부)</span>"))
        avg_txt = "" if is_hard_mode() else f" · 팀 평균 OVR {o.get('team_avg', 0):.0f}"
        cl.addWidget(QLabel(f"<span style='color:#bbb'>예상 역할</span> <b style='color:#00cc66'>{o.get('role', '')}</b>"
                            f"<span style='color:#888;font-size:11px'>{avg_txt}</span>"))
        cl.addWidget(QLabel(f"<span style='color:#bbb'>기간</span> <b style='color:#fff'>"
                            f"{ml.halves_text(o.get('halves', 2))}</b>  "
                            f"<span style='color:#888;font-size:11px'>{_move_text(o)} · {_return_text(o)}</span>"))
        # [2026-10 신민용 확정] 연봉은 원 소속팀 계약 그대로 — 누가 얼마를 내는지만 표시
        _split = ((int(o["wage_host_pct"]), 100 - int(o["wage_host_pct"]))
                  if o.get("wage_host_pct") is not None else ml.wage_split(o.get("purpose"), o.get("role")))
        if _split:
            cl.addWidget(QLabel(f"<span style='color:#bbb'>연봉</span> <b style='color:#fff'>원 소속팀 계약 그대로</b>  "
                                f"<span style='color:#888;font-size:11px'>{ml.wage_split_text(_split)}</span>"))
        if accept_cb is not None:
            b = QPushButton("✅ 이 팀으로 임대")
            b.setObjectName("dlgOk")
            b.clicked.connect(accept_cb)
            cl.addWidget(b)
        return card

    def _offer_list(self, offers, make_cb):
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        for i, o in enumerate(offers):
            bl.addWidget(self._offer_card(o, make_cb(i) if make_cb else None))
        if len(offers) > 2:
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setWidget(box)
            sc.setMinimumHeight(360)
            sc.setStyleSheet("QScrollArea{border:none;background:transparent;}")
            self.root.addWidget(sc)
        else:
            self.root.addWidget(box)

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

    def _msg(self, ok, why):
        if not ok and why:
            self._error = why
        self._build()

    # ── 상태별 화면 ────────────────────────────────────────────
    def _build(self):
        self._clear()
        p = get_player() or {}
        st = ml.status(p)
        err = getattr(self, "_error", "")
        self._error = ""

        if st["club_proposal"]:
            self._build_club(st["club_proposal"])
        elif st["pending"]:
            self._build_pending(st["pending"])
        elif st["on_loan"]:
            self._build_on_loan(p)
        elif st["wish"]:
            self._build_wish(st["wish"], st["wish_offers"])
        else:
            self._build_idle(st)
        if err:
            self._note(f"⚠ {err}", "#ff8866")

    def _build_club(self, prop):
        self._header("📋 구단이 임대를 추진합니다")
        self._note(_PURPOSE_TEXT.get(prop.get("purpose"), ""), "#bbb")
        offers = prop.get("offers") or []

        def make_cb(i):
            def _cb():
                ok, why = ml.accept_club_loan_offer(i)
                if ok:
                    self.accept()
                else:
                    self._msg(ok, why)
            return _cb
        self._offer_list(offers, make_cb)
        if prop.get("purpose") == "정리":
            self._note("※ 거부하면 팀에 남지만, 구단은 당신을 계획 밖으로 보고 있어 출전 기회가 줄어들 수 "
                       "있습니다.")
        else:
            self._note("※ 거부하면 팀에 남고, 출전은 지금처럼 실력과 경쟁 상황에 따라 정해집니다.")
        self._note("※ 답하지 않으면 이동 시점이 지나면서 제안은 사라집니다.")

        def _reject():
            ml.reject_club_loan_proposal()
            self.accept()
        self._buttons([("❌ 거부하고 남기", "dlgNo", _reject, True),
                       ("나중에 결정", "", self.reject, True)])

    def _build_pending(self, o):
        self._header("✅ 임대가 예약됐습니다")
        self.root.addWidget(self._offer_card(o))
        self._note("※ 다른 팀 오퍼를 수락해 이적을 예약하면 이 임대 예약은 취소됩니다.")
        self._buttons([("닫기", "", self.accept, True)])

    def _build_on_loan(self, p):
        self._header("🔄 임대 중")
        conn_txt = ""
        try:
            from database import get_conn
            c = get_conn()
            r = c.execute("SELECT name FROM teams WHERE id=?", (p.get("loan_from_team_id"),)).fetchone()
            conn_txt = r["name"] if r else ""
        except Exception:
            pass
        ry = p.get("loan_end_year") or 0
        half = p.get("loan_end_half") or 0
        when = f"{ry}년 {'하반기 시작(29주)' if half == 1 else '시즌 끝'}" if ry else "-"
        self._note(f"원 소속팀: {conn_txt}  ·  복귀: {when}", "#bbb")
        _plan = ml.planned_loan_text(p)
        _split = ml.current_wage_split(p)
        if _plan or _split:
            self._note("  ·  ".join(x for x in (f"기간: {_plan}" if _plan else "",
                                                 "연봉: 원 소속팀 계약 그대로",
                                                 ml.wage_split_text(_split)) if x), "#bbb")
        self._note("※ 임대 중에는 새 임대를 낼 수 없습니다.")
        self._buttons([("닫기", "", self.accept, True)])

    def _build_wish(self, wish, visible):
        stt = wish.get("status")
        if stt == "submitted":
            self._header("📝 임대 희망을 전달했습니다")
            self._note("구단의 답을 기다리는 중입니다(다음 주).", "#bbb")
            self._buttons([("↩ 임대 희망 거두기", "dlgNo", self._cancel_wish, True),
                           ("닫기", "", self.accept, True)])
            return
        if stt == "denied":
            self._header("🚫 임대 희망이 거절됐습니다")
            self._note(wish.get("reason", ""), "#bbb")
            tgt = wish.get("target") or []
            if tgt:
                self._note(f"※ {tgt[0]}년 {tgt[1]}주(다음 이동 시점)가 지나면 다시 낼 수 있습니다.")
            self._buttons([("닫기", "", self.accept, True)])
            return
        # approved
        self._header("✅ 구단이 임대를 허락했습니다")
        total = len(wish.get("offers") or [])
        tgt = wish.get("target") or []
        if visible:
            def make_cb(i):
                def _cb():
                    ok, why = ml.accept_wish_offer(i)
                    if ok:
                        self.accept()
                    else:
                        self._msg(ok, why)
                return _cb
            self._offer_list(visible, make_cb)
        waiting = total - len(visible)
        if waiting > 0:
            self._note(f"관심을 보이는 구단의 오퍼를 기다리는 중입니다.", "#bbb")
        elif not total:
            self._note("지금은 임대를 원하는 구단이 없습니다.", "#bbb")
        if tgt:
            self._note(f"※ {tgt[0]}년 {tgt[1]}주(다음 이동 시점)까지 수락하지 않으면 임대 희망은 마감됩니다.")
        self._buttons([("↩ 임대 희망 거두기", "dlgNo", self._cancel_wish, True),
                       ("닫기", "", self.accept, True)])

    def _build_idle(self, st):
        self._header("🔄 임대 희망")
        self._note("출전 기회를 얻기 위해 구단에 임대를 요청합니다. 구단이 허락하면 관심 구단의 "
                   "임대 오퍼가 들어오고, 수락하면 다음 이동 시점(상반기 시작 4주 / 하반기 시작 29주)에 "
                   "합류합니다. 연봉·계약은 원 소속팀 그대로입니다.", "#bbb")
        if not st["can_submit"]:
            self._note(f"⚠ {st['block_reason']}", "#ff8866")

        def _submit():
            ok, why = ml.submit_loan_wish()
            self._msg(ok, why)
        self._buttons([("📝 임대 희망 제출", "dlgOk", _submit, st["can_submit"]),
                       ("닫기", "", self.accept, True)])

    def _cancel_wish(self):
        ml.cancel_loan_wish()
        self._build()


def loan_button_label(p=None):
    """버튼 문구 — 볼 것이 있으면 표시(오퍼 수·제안·예약·임대 중)."""
    p = p or get_player() or {}
    st = ml.status(p)
    if st["club_proposal"]:
        return "🔄 임대 (구단 제안)"
    if st["pending"]:
        return "🔄 임대 (예약됨)"
    if st["on_loan"]:
        return "🔄 임대 (임대 중)"
    if st["wish"]:
        n = len(st["wish_offers"])
        stt = st["wish"].get("status")
        if stt == "approved" and n:
            return f"🔄 임대 (오퍼 {n})"
        if stt == "submitted":
            return "🔄 임대 (답변 대기)"
        if stt == "denied":
            return "🔄 임대 (거절됨)"
        return "🔄 임대 (오퍼 대기)"
    return "🔄 임대 신청"


def loan_button_state(p=None):
    """(활성 여부, 툴팁). 무소속·임대 중·복무 중·이적 예약(오퍼/판매) 상태에서는 누를 수 없다.
    그 외에는 열 수 있고, 새 제출 가능 여부는 창 안에서 엔진 판정(my_loan) 그대로 보여준다."""
    p = p or get_player() or {}
    st = ml.status(p)
    if not p.get("current_team_id"):
        return False, "소속팀이 있을 때만 임대할 수 있습니다."
    if st["serving"]:
        return False, "복무 중에는 임대할 수 없습니다."
    if st["on_loan"]:
        return False, "이미 임대 중입니다."
    if p.get("pending_join_transfer_json") or p.get("pending_sale_transfer_json"):
        return False, "이적이 예약돼 있어 임대할 수 없습니다."
    return True, "구단에 임대를 요청하거나, 임대 제안·오퍼·예약 상태를 확인합니다."