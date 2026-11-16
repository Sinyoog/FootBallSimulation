"""
ui/offer_window.py  ─  이적 오퍼 선택 + 협상
"""
import random
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QWidget, QComboBox
)
from PyQt6.QtCore import Qt, QTimer
from game_engine import fmt_money, get_difficulty
from ui.center_panel import show_toast

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
#offerCard { background:#252525; border:1px solid #333; border-radius:8px; padding:8px; }
#grade_SS { color:#ff4488; font-weight:bold; font-size:13px; }
#grade_S  { color:#ff9900; font-weight:bold; }
#grade_A  { color:#ffcc00; font-weight:bold; }
#grade_B  { color:#00ccff; font-weight:bold; }
#grade_C  { color:#00ff66; }
#grade_D, #grade_E, #grade_F { color:#aaaaaa; }
#tier1  { color:#ff6600; }
#tier2  { color:#888888; }
#tier3  { color:#555555; }
#selectBtn { background:#2a6a2a; color:white; border:none; border-radius:4px;
             padding:6px 14px; font-size:12px; }
#selectBtn:hover { background:#3a8a3a; }
#selectBtn:disabled { background:#333; color:#555; }
#negBtn { background:#2a2a6a; color:white; border:none; border-radius:4px;
          padding:6px 14px; font-size:12px; }
#negBtn:hover { background:#3a3a8a; }
#negBtn:disabled { background:#333; color:#555; }
#infoBtn { background:transparent; color:#88aadd; border:1px solid #445566;
           border-radius:4px; padding:2px 8px; font-size:10px; }
#infoBtn:hover { background:#2a3a4a; }
#noOffer { color:#666666; font-size:13px; }
QComboBox {
    background:#2a2a2a; color:#ccc; border:1px solid #444;
    border-radius:4px; padding:4px 6px; font-size:12px;
}
QComboBox:disabled { color:#666; }
QComboBox QAbstractItemView {
    background:#2a2a2a; color:#ccc; selection-background-color:#3a6a3a;
}
"""

class OfferWindow(QDialog):
    def __init__(self, offers: list, lang="ko", parent=None, title="📋 이적 오퍼",
                 force_select=False, grid=False, apply_slots=0,
                 kind="", restore_state=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.lang       = lang
        self.chosen     = None
        self.all_failed = False          # 모든 오퍼 결렬 여부 (1년 훈련 분기용)
        self._close_btn = None           # 닫기 버튼 참조 (전부 결렬 시 활성화)
        # [2026-08 신설, 신민용 요청: "입단/오퍼 창이 떠 있는 동안에도 다른
        # 창(팀 성적 등)을 열어서 비교하고 싶다"] 카드마다 "📊 최근 성적"
        # 버튼으로 WorldBrowserWindow(비모달)를 띄우는데, 참조를 안 들고
        # 있으면 파이썬 GC가 바로 회수해서 창이 뜨자마자 닫혀버린다 — 열린
        # 창들을 여기 붙잡아둔다(닫히면 finished 시그널로 알아서 목록에서 제거).
        self._team_info_wins = []
        # [2026-07 신설, 신민용 요청] kind("join"/"auto_offer")가 주어지면
        # 이 창의 상태(오퍼 목록·협상 진행도·직접지원 결과)를 DB에 저장해서,
        # 결정을 내리기 전에 게임을 껐다 켜도 새로 랜덤 생성하지 않고 같은
        # 상태 그대로 다시 뜨게 한다(재접속 오퍼 리롤 방지). restore_state가
        # 주어지면(재시작 후 복원) 그 값으로 초기 상태를 그대로 복원한다.
        self._kind = kind
        if restore_state:
            title        = restore_state.get("title", title)
            force_select = restore_state.get("force_select", force_select)
            grid         = restore_state.get("grid", grid)
            apply_slots  = restore_state.get("apply_slots", apply_slots)
            offers       = restore_state.get("offers", offers)

        self.setWindowTitle(title)
        self.grid = grid
        self.setMinimumSize(980, 600) if grid else self.setMinimumSize(580, 500)
        self.setStyleSheet(STYLE)
        self.title_text = title
        self._force     = force_select
        self.offers     = offers
        # [2026-07 신설] 직접 지원 슬롯 — 무소속(팀 입단) 창에서만 켜짐.
        # 패시브 오퍼 카드들 뒤에 빈 슬롯을 두고, 그 안의 "지원하기" 버튼으로
        # ApplyWindow(팀 검색)를 열어 성공하면 그 오퍼가 일반 오퍼 카드와
        # 똑같이(협상/입단 가능) self.offers 뒤쪽에 추가된다.
        self.apply_slots = apply_slots

        if restore_state:
            self.offer_salaries = restore_state.get(
                "offer_salaries", [o["salary"] for o in self.offers])
            self.neg_used = {int(k): v for k, v in
                              restore_state.get("neg_used", {}).items()}
            self.neg_failed = set(restore_state.get("neg_failed", []))
            self._applied_count = restore_state.get("applied_count", 0)
            _default_years = self._calc_initial_years()
            _saved_years = restore_state.get("offer_years", [])
            self.offer_years = (_saved_years if len(_saved_years) == len(self.offers)
                                 else _default_years)
            self.years_used = {int(k): v for k, v in
                                restore_state.get("years_used", {}).items()}
            if not self.years_used:
                self.years_used = self._default_years_used()
            self.years_failed = set(restore_state.get("years_failed", []))
            # [2026-08 신설] 플레이어가 직접 고른 희망 계약기간(1~6년,
            # 콤보박스 선택) — 없으면 지금 오퍼 값으로 기본 채움("아직
            # 안 바꿨다" 상태).
            _saved_target = restore_state.get("years_target", {})
            self.years_target = {int(k): v for k, v in _saved_target.items()}
            for i in range(len(self.offers)):
                self.years_target.setdefault(i, self.offer_years[i])
        else:
            self.offer_salaries: list[int] = [o["salary"] for o in offers]
            self.neg_used: dict[int, int] = {}
            for i in range(len(offers)):
                self.neg_used[i] = self._roll_attempts(i)
            self.neg_failed: set[int] = set()
            self._applied_count = 0
            # [2026-08 신설, 신민용 확정] 계약 기간도 연봉처럼 오퍼 생성
            # 시점에 딱 1번만 굴려서 고정한다. (예전엔 카드를 다시 그릴
            # 때마다 _calc_contract_years를 매번 새로 호출해서, 연봉
            # 협상을 한 번 누를 때마다 계약 기간 표시가 조용히 다시
            # 랜덤으로 바뀌는 버그가 있었다 — 이제 self.offer_years에
            # 고정해두고 카드 렌더링은 이 값만 읽는다.)
            self.offer_years: list[int] = self._calc_initial_years()
            self.years_used: dict[int, int] = self._default_years_used()
            self.years_failed: set[int] = set()
            # [2026-08 재설계, 신민용 확정: "내가 기간을 직접 입력(선택)
            # 하고 협상해야 맞지"] 나이 기반 자동 '희망 연도' 추정을
            # 없애고, 플레이어가 1~6년 중 콤보박스로 직접 고른 값을
            # 목표로 협상한다. 기본값은 지금 오퍼와 같게(=아직 안 바꾼
            # 상태) 시작.
            self.years_target: dict[int, int] = {i: self.offer_years[i] for i in range(len(offers))}

        # [2026-10 신설, 신민용 요청: "중간 비시즌에 이적하면 2년이라 떠도 실제론 2년 6개월"] 계약
        # N년에 실제로 더 붙는 개월 수(그 시즌 남은 기간) — 소속팀이 있으면 예약 이적, 없으면 즉시 입단.
        try:
            from game_engine import contract_extra_months, get_player as _gp_cm
            _p_cm = _gp_cm() or {}
            self._reserved = bool(_p_cm.get("current_team_id"))   # [2026-11-13] 계약 만료 연도 계산에도 쓴다
            self._extra_months = contract_extra_months(self._reserved)
        except Exception:
            self._reserved = False
            self._extra_months = 0
        # [2026-11-13 신민용: "기회 1번 남으면 무조건 실패하는 것 같다"] 협상 굴림은 이 창 전용 난수를 쓴다 —
        # 게임 전역 random(시뮬레이션이 같이 쓰는 흐름)과 섞이지 않게. 확률 자체는 예전과 같다(매 시도 동일).
        self._neg_rng = random.Random()
        self._build()
        # 창이 만들어지는 즉시(사용자가 아무것도 안 눌러도) 저장해둔다 —
        # 그래야 이 창을 띄운 직후 바로 꺼져도 다음에 같은 상태로 복원된다.
        self._persist()

    def _yrs_text(self, yrs) -> str:
        """계약 N년 표시 — 시즌 도중 합류면 "2년 6개월"처럼 실제 기간으로."""
        from game_engine import contract_period_text
        return contract_period_text(yrs, getattr(self, "_extra_months", 0))

    def _calc_initial_years(self) -> list:
        """오퍼별 구단 측 초기 제시 계약 기간(연봉의 offer_salaries와 같은
        역할) — 오퍼 생성 시점에 1회만 굴리고 이후 협상으로만 바뀐다."""
        from game_engine import _calc_contract_years, get_player
        p_now = get_player()
        age_now = p_now.get("age", 17) if p_now else 17
        return [_calc_contract_years(age_now, o.get("tier", 3), o.get("country"))
                for o in self.offers]

    def _offer_join_prob(self, idx) -> float:
        """[2026-08 재설계] 오퍼 idx의 실제 입단 성공확률(join_prob) —
        offer_window.apply_window의 "성공 가능성"(유력/가능성있음/쉽지않음/
        거의불가능)과 정확히 같은 값. 연봉/기간 협상 둘 다 이 값으로 시도
        횟수·성공확률을 정한다(game_engine.roll_negotiation_attempts/
        negotiation_success_prob). 예전 저장분 등 값이 없으면 즉석에서
        다시 계산해 채워둔다."""
        offer = self.offers[idx]
        prob = offer.get("join_prob")
        if prob is None:
            from game_engine import calc_apply_success_prob
            try:
                prob, _blocked = calc_apply_success_prob(offer.get("team_id"))
            except Exception:
                prob = 0.5
            offer["join_prob"] = prob
        return prob

    def _roll_attempts(self, idx) -> int:
        """[2026-08 신설] 오퍼 idx에 대해 협상(연봉·기간 공통) 시도 횟수를
        실제 입단 성공확률 기반으로 굴린다. 연봉/기간은 각각 독립적으로
        호출해서 별개의 시도 횟수를 갖는다."""
        from game_engine import roll_negotiation_attempts
        return roll_negotiation_attempts(self._offer_join_prob(idx))

    def _default_years_used(self) -> dict:
        return {i: self._roll_attempts(i) for i in range(len(self.offers))}

    def _persist(self):
        """현재 오퍼/협상 상태를 DB에 저장(kind가 없으면 아무것도 안 함)."""
        if not self._kind:
            return
        from game_engine import save_pending_offer_state
        save_pending_offer_state(
            kind=self._kind, title=self.title_text, force_select=self._force,
            grid=self.grid, apply_slots=self.apply_slots, offers=self.offers,
            offer_salaries=self.offer_salaries, neg_used=self.neg_used,
            neg_failed=self.neg_failed, applied_count=self._applied_count,
            offer_years=self.offer_years, years_used=self.years_used,
            years_failed=self.years_failed, years_target=self.years_target,
        )

    def _clear_persisted(self):
        """결정이 끝나면(입단 확정/전부 결렬 등) 저장된 상태를 지운다."""
        if not self._kind:
            return
        from game_engine import clear_pending_offer_state
        clear_pending_offer_state()

    def accept(self):
        # 팀을 실제로 골라 입단하는 경우 — 결정이 끝났으니 저장된 상태를 지운다.
        self._clear_persisted()
        super().accept()

    def reject(self):
        # [2026-07 재수정, 신민용 지적: "왜 아예 닫기 버튼을 잠궈버렸어?"]
        # 이전엔 force_select면 닫기/X 자체를 막아버렸는데, 그럴 필요가
        # 없었다 — 무소속인데 진행을 계속하면 center_panel의 별도 체크
        # ("17살 이상인데 팀이 없고 프리시즌이면 입단 강제" 안내)가 이미
        # 다음 날 진행을 막아주므로, 이 다이얼로그 자체를 못 닫게 잠글
        # 이유가 없다.
        #
        # [버그수정 2026-07, 신민용 지적: "오퍼는 취소하면 다음에 뜰 때
        # 새로 떠야지, 저장된 게 그대로 뜨면 안 된다 — 저장 유지는 입단
        # 창에서만 쓰는 거다"] '팀을 안 고르고 닫아도 상태를 지우지 않고
        # 다음에 다시 열면 같은 목록이 뜬다'는 건 입단(kind="join")에만
        # 맞는 동작이다 — 입단은 어차피 언젠가 팀을 반드시 골라야 해서,
        # 재접속/재오픈으로 계속 새 오퍼를 리롤하는 걸 막을 필요가 있다.
        # 반면 오퍼(kind="auto_offer")는 이번 오퍼를 그냥 닫는 것 자체가
        # '이번 건 거절'이라는 확정된 결정이다 — 다음 이적시장에 새로
        # 뜨는 오퍼는 이번에 거절한 그 목록이 아니라 새로 뽑힌 목록이어야
        # 한다. 그래서 오퍼는 all_failed 여부와 무관하게 닫으면 항상
        # 저장된 상태를 지운다.
        if self._kind == "auto_offer" or self.all_failed:
            self._clear_persisted()
        super().reject()

    def _build(self):
        root = QVBoxLayout(self)

        hdr = QLabel(self.title_text)
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        root.addWidget(hdr)

        # [2026-10 버그수정, 신민용 리포트: "1주차에 군대 오퍼가 무조건 와야 하는데 안 온다"] 오퍼 0건 +
        # 직접지원 칸 0이면 "오퍼가 없습니다"만 띄우고 군팀 카드(_render_cards)를 아예 그리지 않았다.
        if not self.offers and not self.apply_slots and not self._military_offers():
            lbl = QLabel("오퍼가 없습니다." if self.lang=="ko" else "No offers available.")
            lbl.setObjectName("noOffer")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(lbl)
        else:
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
            inner  = QWidget()
            if self.grid:
                self.cards_lay = QGridLayout(inner)
                self.cards_lay.setSpacing(8)
                # [2026-11-13] 카드는 폭 고정(_card_width) — 남는 가로는 오른쪽 빈 칸이 먹는다.
                self.cards_lay.setColumnStretch(self._GRID_COLS, 1)
            else:
                self.cards_lay = QVBoxLayout(inner)
                self.cards_lay.setSpacing(8)
            scroll.setWidget(inner)
            root.addWidget(scroll)
            self._scroll, self._inner = scroll, inner   # [2026-11] _fit_width가 내용 폭을 잰다
            self._render_cards()

        close = QPushButton("닫기" if self.lang=="ko" else "Close")
        self._close_btn = close
        # [2026-07 재수정, 신민용 지적: "왜 아예 닫기 버튼을 잠궈버렸어?"]
        # force_select여도 닫기 자체는 항상 눌리게 둔다 — 결정 없이 닫아도
        # 오퍼 상태는 그대로 저장돼 있으니(위 reject() 참고) 나중에 똑같은
        # 목록으로 다시 열 수 있고, 무소속으로 방치되는 것도 center_panel의
        # 별도 진행 차단 안내가 막아준다. 안내 툴팁만 남겨 "아직 안 골랐다"는
        # 걸 알려준다.
        if self._force:
            close.setToolTip("아직 팀을 선택하지 않았습니다 — 나중에 다시 열면 같은 목록이 뜹니다")
        close.setStyleSheet(
            "QPushButton{background:#2a2a2a;color:#ccc;border:1px solid #444;"
            "border-radius:4px;padding:6px;}"
            "QPushButton:disabled{background:#222;color:#555;border:1px solid #333;}")
        close.clicked.connect(self.reject)
        root.addWidget(close)

    # [2026-11-13 재설계, 신민용 요청: "오퍼창이든 입단창이든 좀 더럽다 — 세로 카드로, 1줄에 6개씩, 창은
    # 가장 긴 카드에 맞추고 너무 길면 2줄로"] 한 줄 카드 수와 카드 폭 범위. 카드 폭은 모든 카드 중 "줄바꿈
    # 없이 한 줄로 그렸을 때" 가장 넓은 줄에 맞추되(_card_need) 이 범위로 자르고, 그보다 긴 글자는 줄바꿈.
    _GRID_COLS = 6
    _CARD_W_MIN = 200
    _CARD_W_MAX = 270

    def _render_cards(self):
        while self.cards_lay.count():
            item = self.cards_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        empty_slots = max(0, self.apply_slots - self._applied_count)
        # [2026-10 병역 7단계, 신민용 요청] 군팀 입대 카드는 팀 입단·오퍼 창 모두 맨 위에.
        mil = self._military_offers()
        cards = []   # (widget, 칸 번호) — 칸 번호는 grid에서만 쓴다
        n = self._GRID_COLS
        for k, mo in enumerate(mil):
            cards.append((self._make_military_card(mo), k))
        start = -(-len(mil) // n) * n   # 군팀 줄은 따로 한 줄(이상) 차지
        for i, offer in enumerate(self.offers):
            cards.append((self._make_card(i, offer), start + i))
        base = start + len(self.offers)
        for j in range(empty_slots):
            cards.append((self._make_apply_slot_card(), base + j))
        w = self._card_width([c for c, _k in cards])
        top = Qt.AlignmentFlag.AlignTop
        for c, k in cards:
            c.setFixedWidth(w)
            if self.grid:
                row, col = divmod(k, n)
                self.cards_lay.addWidget(c, row, col, top | Qt.AlignmentFlag.AlignLeft)
            else:
                self.cards_lay.addWidget(c, 0, Qt.AlignmentFlag.AlignHCenter)
        if self.grid:
            self.cards_lay.setRowStretch(self.cards_lay.rowCount(), 1)
        else:
            self.cards_lay.addStretch()
        self._fit_width()

    def _card_width(self, cards) -> int:
        """모든 카드 공통 폭 — 가장 긴 카드 줄(한 줄로 그렸을 때 폭)에 맞추고 _CARD_W_MIN~MAX로 자른다.
        화면이 좁으면 6장이 한 화면에 들어가는 폭까지 줄이되 _CARD_W_MIN 아래로는 안 내린다(그땐 가로 스크롤)."""
        need = max([getattr(c, "_card_need", 0) for c in cards] or [0])
        cap = self._CARD_W_MAX
        try:
            scr = self.screen() or (self.parentWidget().screen() if self.parentWidget() else None)
            if scr is not None and self.grid:
                sp = self.cards_lay.spacing() if hasattr(self, "cards_lay") else 8
                cap = min(cap, (scr.availableGeometry().width() - 110 - sp * (self._GRID_COLS - 1))
                          // self._GRID_COLS)
        except RuntimeError:
            pass
        return int(max(self._CARD_W_MIN, min(cap, need)))

    def _fit_width(self):
        """[2026-11 신설, 신민용 리포트: "오퍼 창 가로 길이가 안의 글자에 맞춰 커지게 — 지금은 카드 오른쪽이 잘린다"]
        카드 줄 전체 폭(카드 폭은 _card_width로 모두 같다)만큼 창 가로를 넓힌다 — 내용 영역의 최소 폭
        (minimumSizeHint)에 세로 스크롤바·여백만큼 더한다. [2026-11-13] 세로 카드가 되면서 세로도 같은 식으로
        (화면 높이까지) 넓힌다. 넓히기만 하고 줄이지는 않는다(사용자가 키운 창, 협상 뒤 다시 그릴 때 들쭉날쭉
        방지). 화면보다 커야 하면 화면 크기까지만 키우고 나머지는 스크롤."""
        scroll = getattr(self, "_scroll", None)
        inner = getattr(self, "_inner", None)
        if scroll is None or inner is None:
            return
        try:
            inner.layout().activate()
            need = inner.minimumSizeHint().width()
            vbar = scroll.verticalScrollBar().sizeHint().width()
            m = self.layout().contentsMargins() if self.layout() is not None else None
            pad = (m.left() + m.right()) if m is not None else 22
            want = need + vbar + pad + 2 * scroll.frameWidth() + 4
            scr = self.screen() or (self.parentWidget().screen() if self.parentWidget() else None)
            if scr is not None:
                want = min(want, scr.availableGeometry().width() - 40)
            want_h = self.height()
            _hdr = self.height() - scroll.height()   # 제목·닫기 버튼 등 스크롤 밖 높이
            _need_h = inner.sizeHint().height() + _hdr + 2 * scroll.frameWidth() + 4
            if scr is not None:
                _need_h = min(_need_h, scr.availableGeometry().height() - 60)
            if _need_h > want_h and self.isVisible():
                want_h = _need_h
            if want > self.width() or want_h > self.height():
                self.resize(max(want, self.width()), want_h)
        except RuntimeError:
            pass

    def showEvent(self, ev):
        super().showEvent(ev)
        # 처음 뜰 때 세로 크기는 레이아웃이 실제 높이를 가진 뒤에야 잴 수 있다.
        QTimer.singleShot(0, self._fit_width)

    # ── [2026-10 병역 시스템 7단계] 20세부터 오퍼 아래에 군팀 2곳 ──────────────
    def _military_offers(self):
        """미필 한국 국적 + 20세 이상일 때만 군팀 2곳(K1 이상 수준이면 1부 우선)."""
        try:
            from game_engine import my_player_military_offer_teams, military_choice_week_open
            if not military_choice_week_open():   # 1주차·12월 마지막 주에만
                return []
            return my_player_military_offer_teams()
        except Exception:
            return []

    def _make_military_card(self, mo):
        from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QPushButton, QMessageBox
        card = QFrame()
        card.setStyleSheet("QFrame{border:1px solid #6b8e23;border-radius:8px;background:#1f2a1a;}")
        lay = QVBoxLayout(card)
        div = "1부" if mo.get("tier") == 1 else "2부"
        title = QLabel(f"🪖 {mo['team_name']}")
        title.setStyleSheet("color:#c5e17a;font-weight:bold;font-size:14px;border:none;")
        # [2026-10 신민용 확정] 계약 처리 — 국내 구단은 계약 유지·복귀, 해외 구단은 계약 해지(제대 후 FA).
        _plan_txt = {
            "keep": "원소속 계약 유지 — 제대 후 원소속팀 복귀\n(계약 기간은 복무 중에도 흐릅니다)",
            "abroad": "해외 구단 소속 — 입대하면 구단과 계약 해지,\n제대 후 FA",
            "short": "남은 계약이 복무 기간보다 짧아 계약 해지,\n제대 후 FA",
            "fa": "소속 없음 — 제대 후 FA",
        }.get(mo.get("contract_plan") or "",
              "원소속 계약이 복무 기간보다 길면 제대 후 원소속팀 복귀,\n짧으면 계약 해지 → 제대 후 FA")
        info = QLabel(f"군대 · {mo.get('league_name', '')} ({div})\n"
                      "복무 2년 · 연봉 0원 · 복무 중 이적/임대 불가\n"
                      f"{_plan_txt}\n"
                      "※ 누르면 바로 입대합니다")
        info.setStyleSheet("color:#cccccc;border:none;")
        info.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(info)
        btn = QPushButton("🪖 입대")

        def _go(_=None, mo=mo):
            from game_engine import enlist_my_player_now
            if enlist_my_player_now(mo["team_id"]):   # 안내 팝업 없이 바로 입대(신민용 요청)
                self.chosen = None
                self.reject()
        btn.clicked.connect(_go)
        lay.addWidget(btn)
        return card

    def _make_apply_slot_card(self):
        """[2026-07 신설] 빈 '직접 지원' 슬롯 카드 — 원하는 팀을 검색해서
        지원하기 버튼. 성공하면 이 카드가 사라지고 그 자리에 일반 오퍼
        카드(협상/입단 가능)가 self.offers 뒤쪽에 추가되어 나타난다."""
        from game_engine import get_apply_attempts_left
        card = QFrame(); card.setObjectName("offerCard")
        lay = QVBoxLayout(card); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(8)

        left = get_apply_attempts_left()
        title = QLabel("🔎 직접 지원")
        title.setStyleSheet("font-size:14px;font-weight:bold;color:#888;")
        lay.addWidget(title)

        hint = QLabel("원하는 팀을 검색해서 직접 지원해보세요.")
        hint.setStyleSheet("color:#666;font-size:11px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch()

        btn = QPushButton(f"🔎 팀 검색 · 지원하기 (남은 {left}회)")
        btn.setObjectName("selectBtn")
        btn.setEnabled(left > 0)
        btn.clicked.connect(self._open_apply_search)
        lay.addWidget(btn)
        btn.ensurePolished()
        card._card_need = btn.sizeHint().width() + 26   # [2026-11-13] 카드 공통 폭 재료
        return card

    def _open_team_info(self, tid, tname):
        """[2026-08 신설] 카드의 "📊 최근 성적" 버튼 — 이 창(exec()로 열려
        있음)을 닫지 않고도 그 팀의 세계 기록실(연도별 성적/수상)을 별도
        비모달 창으로 띄운다. 이미 그 팀 창이 열려 있으면 새로 만들지
        않고 앞으로 가져온다(center_panel._do_world_browser와 동일 패턴)."""
        if tid is None:
            return
        for w in self._team_info_wins:
            if getattr(w, "_info_team_id", None) == tid:
                w.raise_(); w.activateWindow()
                return
        from ui.world_browser_window import WorldBrowserWindow
        win = WorldBrowserWindow(self)
        win._info_team_id = tid
        win.tabs.setCurrentIndex(1)          # "🏟 팀 검색" 탭
        win._show_team_detail(tid, tname)
        self._team_info_wins.append(win)

        def _cleanup(*_a, w=win):
            if w in self._team_info_wins:
                self._team_info_wins.remove(w)
        win.finished.connect(_cleanup)
        win.show()
        win.raise_(); win.activateWindow()

    def _open_apply_search(self):
        from ui.apply_window import ApplyWindow
        dlg = ApplyWindow(self.lang, self)
        dlg.exec()
        if dlg.chosen:
            offer = dlg.chosen
            idx = len(self.offers)
            self.offers.append(offer)
            self.offer_salaries.append(offer["salary"])
            self.neg_used[idx] = self._roll_attempts(idx)
            # [2026-08 신설] 기간 협상 상태도 새 오퍼에 맞춰 같이 확장.
            from game_engine import _calc_contract_years, get_player
            p_now = get_player()
            age_now = p_now.get("age", 17) if p_now else 17
            self.offer_years.append(
                _calc_contract_years(age_now, offer.get("tier", 3), offer.get("country")))
            self.years_used[idx] = self._roll_attempts(idx)
            self.years_target[idx] = self.offer_years[idx]
            self._applied_count += 1
            self._persist()
        self._render_cards()

    # ── [2026-11-13 재설계] 세로 오퍼 카드 ─────────────────────────────────────
    # 신민용 요청 순서: 팀 이름 | 리그명 | 최근 성적 버튼 | 팀 평균 OVR | 연봉 | 이적료 | 계약 날짜 | 팀에서
    # 원하는 역할 | 지난 시즌 성적 | 연봉 협상 | 기간 협상 | 기간 합의 완료 | 입단. 맨 위 국기 이모지는 뺀다.
    _GRADE_COLOR = {"SS": "#ff4488", "S": "#ff9900", "A": "#ffcc00", "B": "#00ccff", "C": "#00ff66"}
    # 지난 시즌 결과 → (표시 글자, 색). 색은 세계 기록실(world_browser.league_text_color)과 같은 계열.
    _SEASON_RESULT = {
        "promo":      ("직행 승격",     "#4da6ff"),
        "po_promo":   ("승강 PO 승격",  "#4da6ff"),
        "releg":      ("직행 강등",     "#ff5555"),
        "po_releg":   ("승강 PO 강등",  "#ff5555"),
        "po_fail":    ("승격 PO 탈락",  "#ffa040"),
        "po_survive": ("강등 PO 생존",  "#ff9e9e"),
    }

    @staticmethod
    def _measured(widget, need: list, extra: int = 0):
        """줄바꿈을 켜기 전(한 줄일 때) 폭을 재서 need에 넣는다 — 카드 공통 폭(_card_width)의 재료."""
        widget.ensurePolished()
        need.append(widget.sizeHint().width() + extra)
        return widget

    def _wrap_label(self, text, style, need, align=None, rich=False, extra=0):
        lb = QLabel(text)
        lb.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        lb.setStyleSheet(style)
        if align is not None:
            lb.setAlignment(align)
        self._measured(lb, need, extra)
        lb.setWordWrap(True)
        return lb

    def _game_week_year(self):
        try:
            from game_engine import get_state
            st = get_state() or {}
            return int(st.get("current_week") or 1), int(st.get("current_year") or 0)
        except Exception:
            return 1, 0

    def _contract_end_year(self, yrs: int):
        """계약 N년의 만료 연도 — game_engine.join_team 규칙과 같다(그 해 시즌이 끝날 때 만료).
          무소속(즉시 입단): 프리시즌(1~3주) 체결이면 올해가 1년차 → 올해+N-1, 그 뒤면 올해+N.
          소속팀 있음(예약 이적): 2주 이동이면 올해+N-1, 27주 이동이면 올해+N, 시즌 뒤(45주~)에
          수락하면 다음 해 2주 이동 → 다음 해+N-1 = 올해+N."""
        week, year = self._game_week_year()
        if not year:
            return None
        from constants import SEASON_PHASES, MY_PRESEASON_MOVE_WEEK, MY_WINTER_MOVE_WEEK
        if getattr(self, "_reserved", False):
            if week < MY_PRESEASON_MOVE_WEEK:
                return year + yrs - 1
            if week < MY_WINTER_MOVE_WEEK:
                return year + yrs
            return year + yrs
        return year + yrs - 1 if week <= SEASON_PHASES["preseason1"][1] else year + yrs

    def _last_season_result(self, team_id):
        """지난(끝난) 시즌 리그 성적 — 리그가 끝난 뒤(44주~)면 올해, 아니면 작년. 창마다 팀별로 1번만 계산."""
        cache = self.__dict__.setdefault("_season_res_cache", {})
        if team_id in cache:
            return cache[team_id]
        res = None
        week, year = self._game_week_year()
        if team_id is not None and year:
            from constants import SEASON_PHASES
            y = year if week >= SEASON_PHASES["postseason"][0] else year - 1
            try:
                from world_browser import get_team_season_result
                res = get_team_season_result(team_id, y)
                if res is not None:
                    res["year"] = y
            except Exception:
                res = None
        cache[team_id] = res
        return res

    def _kv_row(self, grid, r, key, value, need, val_style="color:#dddddd;font-size:12px;",
                rich=False, tip=""):
        # [2026-11-13 신민용: "좌측 칸 가운데 정렬, 색을 굳이 회색으로 할 필요 없다"] 키 칸은 값(두 줄일 수
        # 있음) 높이 기준 가운데, 글자색은 본문과 같은 밝은 색.
        k = QLabel(key)
        k.setStyleSheet("color:#d8d8d8;font-size:11px;font-weight:bold;")
        k.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._measured(k, [])
        kw = k.sizeHint().width()
        v = self._wrap_label(value, val_style, need, rich=rich, extra=kw + 10)
        v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        if tip:
            k.setToolTip(tip); v.setToolTip(tip)
        grid.addWidget(k, r, 0)
        grid.addWidget(v, r, 1)

    def _make_card(self, idx, offer):
        card = QFrame(); card.setObjectName("offerCard")
        lay  = QVBoxLayout(card); lay.setContentsMargins(12, 12, 12, 12); lay.setSpacing(6)
        need: list = []
        center = Qt.AlignmentFlag.AlignHCenter

        # ── 팀 이름 (국기 이모지 없이) + 등급·구분 배지 ──────────────────────
        lay.addWidget(self._wrap_label(offer.get("team_name", ""),
                                       "font-size:15px;font-weight:bold;color:#f0f0f0;", need, center))
        _g = offer.get("grade") or ""
        _zone_txt = {"domestic": "자국", "prev_league": "직전 리그", "hometown": "고향", "foreign": "해외",
                     "applied": "직접 지원", "youth_scout": "유스 스카우트"}.get(offer.get("_zone"))
        _badge = []
        if _g:
            _badge.append(f"<span style='color:{self._GRADE_COLOR.get(_g, '#aaaaaa')};font-weight:bold'>"
                          f"{_g}급</span>")
        if _zone_txt:
            _badge.append(f"<span style='color:#888888'>{_zone_txt}</span>")
        if _badge:
            lay.addWidget(self._wrap_label("<span style='color:#555'> · </span>".join(_badge),
                                           "font-size:10px;", need, center, rich=True))

        # ── 리그명 (나라 › 리그 (N부)) — 길면 두 줄 ─────────────────────────
        from html import escape as _esc
        _lg = (f"<span style='color:#aaddff;font-weight:bold'>{_esc(offer.get('country', ''))}</span>"
               f"<span style='color:#555555'> › </span>"
               f"<span style='color:#cccccc'>{_esc(offer.get('league_name', ''))}</span>"
               f"<span style='color:#888888'> ({offer.get('tier', '?')}부)</span>")
        lay.addWidget(self._wrap_label(_lg, "font-size:11px;", need, center, rich=True))

        # ── 📊 최근 성적 — 이 창을 띄운 채 그 팀 세계 기록실(비모달)을 연다 ────────
        info_btn = QPushButton("📊 최근 성적")
        info_btn.setObjectName("infoBtn")
        info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        info_btn.clicked.connect(
            lambda _, tid=offer.get("team_id"), tname=offer.get("team_name"):
                self._open_team_info(tid, tname))
        lay.addWidget(self._measured(info_btn, need))

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#333333;background:#333333;max-height:1px;border:none;")
        lay.addWidget(sep)

        # ── 키: 값 줄들 ────────────────────────────────────────────────────
        kv = QGridLayout(); kv.setHorizontalSpacing(10); kv.setVerticalSpacing(5)
        kv.setColumnStretch(1, 1)
        r = 0
        # [2026-08 신설] 쉬움 난이도 전용 — 팀 평균 OVR(오퍼에 이미 계산돼 있음).
        if get_difficulty() == "easy" and offer.get("team_avg_ovr") is not None:
            self._kv_row(kv, r, "OVR", f"{offer['team_avg_ovr']:.1f}", need,
                         "color:#ffd700;font-size:12px;font-weight:bold;"); r += 1
        _sal = self.offer_salaries[idx]
        # [2026-11-13 신민용] "연 5.1천만원 / (주 98만원)" 두 줄로 — 저절로 줄바꿈되면 괄호 중간에서 끊겼다.
        _sal_txt = "무급" if _sal == 0 else f"연 {fmt_money(_sal)}\n(주 {fmt_money(max(1, _sal // 52))})"
        self._kv_row(kv, r, "연봉", _sal_txt, need, "color:#00cc44;font-size:12px;font-weight:bold;"); r += 1
        # [2026-07] 이적료 — 무소속(FA)이면 0이라 줄 자체를 안 그린다.
        _fee = offer.get("transfer_fee", 0)
        if _fee:
            self._kv_row(kv, r, "이적료", fmt_money(_fee), need, "color:#66aaff;font-size:12px;"); r += 1
        # [2026-08] 계약 기간은 self.offer_years[idx](오퍼 생성 때 1회 고정, 기간 협상으로만 바뀜).
        # [2026-11-13 신민용 요청: "계약 날짜 — 기간 합의 완료하면 위에 표시된 날짜도 바뀌어야"] 만료 연도를
        # 같이 보여주고, 카드는 협상마다 다시 그려지므로 합의된 기간이 곧바로 반영된다.
        c_yrs = self.offer_years[idx]
        _end = self._contract_end_year(c_yrs)
        _ctr = self._yrs_text(c_yrs) + (f" · {_end}년 만료" if _end else "")
        _ctip = f"{_end}년 시즌이 끝나면 계약이 끝납니다" if _end else ""
        if self._extra_months:
            _ctip += (f"\n이번 시즌 남은 기간({self._extra_months}개월) + {c_yrs}년 — "
                      "시즌 도중 합류라 올해 남은 기간이 계약에 더해집니다")
        self._kv_row(kv, r, "계약", _ctr, need, "color:#ffcc44;font-size:12px;", tip=_ctip.strip()); r += 1
        # [2026-10] 팀에서 원하는 역할 — 입단 시 기준(활약에 따라 바뀔 수 있음).
        _role = offer.get("role")
        if _role:
            from constants import OFFER_ROLES
            self._kv_row(kv, r, "역할", _role, need, "color:#88ccff;font-size:12px;",
                         tip=(OFFER_ROLES.get(_role, {}).get("desc") or "")
                         + " — 입단 시 기준이며, 팀에서의 활약에 따라 달라질 수 있습니다"); r += 1
        lay.addLayout(kv)

        # ── 지난 시즌 성적 — 순위 + 직행/PO 승격·강등 색 ─────────────────────────
        # [2026-11-13 신민용 요청: "승격 (EFL 챔피언십)만 뜨는데 성적도 — 직행이면 파란색 순위, 승강 플레이오프면
        # 그에 맞게, 강등도"] 예전 rank_info 글자(오퍼 생성 때 만든 것)는 이 팀이 그 시즌 다른 리그에 있었으면
        # 승격/강등 사실만 남았다. 이제 세계 기록실과 같은 계산으로 그 시즌 실제로 뛴 리그의 순위를 직접 구한다.
        res = self._last_season_result(offer.get("team_id"))
        ttl = QLabel("지난 시즌" if not res else f"지난 시즌 ({res['year']})")
        ttl.setStyleSheet("color:#888888;font-size:11px;")
        lay.addWidget(ttl)
        if res:
            _tag, _col = self._SEASON_RESULT.get(res.get("result") or "", ("", "#dddddd"))
            if res.get("champion"):
                _tag, _col = "리그 우승", "#ffd700"
            lay.addWidget(self._wrap_label(
                f"{_esc(res.get('league_name') or '')} ({res.get('league_tier')}부)",
                "color:#aaaaaa;font-size:11px;", need))
            _line = (f"<span style='color:{_col};font-weight:bold;font-size:13px'>{res['rank']}위</span>"
                     f"<span style='color:#777777'> / {res.get('teams') or '?'}팀</span>")
            _rec = res.get("record")
            if _rec:
                _line += f"<span style='color:#aaaaaa'>  ·  {_esc(_rec)}"
                if res.get("pts") is not None:
                    _line += f" {res['pts']}점"
                _line += "</span>"
            lay.addWidget(self._wrap_label(_line, "font-size:11px;", need, rich=True))
            if _tag:
                _dest = res.get("dest")
                lay.addWidget(self._wrap_label(
                    f"<span style='color:{_col};font-weight:bold'>{_tag}</span>"
                    + (f"<span style='color:#888888'> → {_esc(_dest)}</span>" if _dest else ""),
                    "font-size:11px;", need, rich=True))
        else:
            _ri = (offer.get("rank_info") or "").split("\n")[0].strip()
            lay.addWidget(self._wrap_label(_ri or "기록 없음", "color:#888888;font-size:11px;", need))
        # 겨울(시즌 도중) 오퍼면 이번 시즌 지금까지 성적(오퍼 생성 때 만든 rank_info)도 아래에 작게.
        _wk, _yr = self._game_week_year()
        from constants import FIRST_HALF_START, SEASON_PHASES as _SP
        if FIRST_HALF_START < _wk <= _SP["second_half"][1]:
            _cur = (offer.get("rank_info") or "").split("\n")[0].strip()
            if _cur and "작년" not in _cur:
                lay.addWidget(self._wrap_label(f"이번 시즌  {_cur}", "color:#777777;font-size:10px;", need))

        lay.addStretch(1)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color:#333333;background:#333333;max-height:1px;border:none;")
        lay.addWidget(sep2)

        # ── 협상·입단 버튼 ─────────────────────────────────────────────────
        from constants import CONTRACT_YEARS_MIN, CONTRACT_YEARS_MAX
        failed   = idx in self.neg_failed
        neg_left = self.neg_used.get(idx, 0)
        is_safe  = bool(offer.get("safe")) and self._force
        # [2026-08] 기간 협상은 연봉 협상과 완전히 독립된 트랙 — 목표는 콤보박스로 직접 고른 years_target.
        years_failed = idx in self.years_failed
        years_left   = self.years_used.get(idx, 0)
        target_yrs   = self.years_target.get(idx, c_yrs)
        years_agreed = (c_yrs == target_yrs)
        # [2026-11-13 신민용: "기회 1번 남으면 무조건 실패하는 것 같다 — 성공 확률은 이전이랑 같아야"] 코드상
        # 마지막 기회도 똑같은 확률로 굴린다(_negotiate). 쉬움 난이도에선 회당 성공 확률을 버튼에 같이 보여준다.
        _easy = get_difficulty() == "easy"
        _pct = ""
        if _easy:
            from game_engine import negotiation_success_prob
            _pct = f" · {round(negotiation_success_prob(self._offer_join_prob(idx)) * 100)}%"
        _ptip = "회당 성공 확률은 매 시도 같습니다(마지막 기회도 동일)." if _easy else ""
        # [2026-11-13 신민용: "어려움은 협상 남은 기회도 안 뜨게 — 기간 협상도"] 어려움에선 남은 횟수를 숨긴다.
        _hard = get_difficulty() == "hard"
        _neg_cnt = "" if _hard else f" ({neg_left}회{_pct})"
        _yrs_cnt = "" if _hard else f" ({years_left}회{_pct})"

        if failed:
            neg_btn = QPushButton("❌ 연봉 협상 결렬 (연봉 유지)" if is_safe else "❌ 연봉 협상 결렬")
            neg_btn.setObjectName("negBtn"); neg_btn.setEnabled(False)
        else:
            neg_btn = QPushButton(f"💬 연봉 협상{_neg_cnt}")
            neg_btn.setObjectName("negBtn")
            neg_btn.setEnabled(neg_left > 0)
            neg_btn.clicked.connect(lambda _, i=idx: self._negotiate(i))
            if _ptip:
                neg_btn.setToolTip(_ptip)
        lay.addWidget(self._measured(neg_btn, need))

        # 희망 기간(콤보) — [2026-10] 병역 미필 + 해외 구단 → 30세 시즌까지만.
        h4 = QHBoxLayout(); h4.setSpacing(6)
        target_combo = QComboBox()
        target_combo.setObjectName("yearsCombo")
        _ymax = CONTRACT_YEARS_MAX
        try:
            from game_engine import _my_contract_years_cap
            _ycap = _my_contract_years_cap(None, offer.get("country"))
        except Exception:
            _ycap = None
        if _ycap is not None:
            _ymax = max(CONTRACT_YEARS_MIN, min(CONTRACT_YEARS_MAX, _ycap))
        for y in range(CONTRACT_YEARS_MIN, _ymax + 1):
            target_combo.addItem(self._yrs_text(y), y)
        target_combo.setCurrentIndex(max(0, min(target_yrs, _ymax) - CONTRACT_YEARS_MIN))
        if _ycap is not None:
            target_combo.setToolTip("병역 미필 — 해외 구단과는 30세 시즌까지만 계약할 수 있습니다")
        target_combo.setEnabled(not years_failed)   # 결렬됐으면 목표를 바꿔도 소용없다
        target_combo.currentIndexChanged.connect(
            lambda _, i=idx, cb=target_combo: self._on_years_target_changed(i, cb))
        _hl = QLabel("희망 기간")
        _hl.setStyleSheet("color:#888888;font-size:11px;")
        h4.addWidget(_hl); h4.addWidget(target_combo, 1)
        self._measured(_hl, [])
        self._measured(target_combo, [])
        need.append(_hl.sizeHint().width() + target_combo.sizeHint().width() + 6)
        lay.addLayout(h4)

        if years_failed:
            yrs_btn = QPushButton("❌ 기간 협상 결렬 (기간 유지)" if is_safe else "❌ 기간 협상 결렬")
            yrs_btn.setObjectName("negBtn"); yrs_btn.setEnabled(False)
        else:
            yrs_btn = QPushButton(f"📋 기간 협상{_yrs_cnt}")
            yrs_btn.setObjectName("negBtn")
            yrs_btn.setEnabled(years_left > 0 and not years_agreed)
            yrs_btn.clicked.connect(lambda _, i=idx: self._negotiate_years(i))
            if _ptip:
                yrs_btn.setToolTip(_ptip)
        lay.addWidget(self._measured(yrs_btn, need))

        # 기간 합의 상태 — 합의되면 초록, 아니면 지금 제시/희망 기간을 흐리게.
        if years_failed:
            _st, _sc = "기간 협상 결렬", "color:#cc5555;background:#2a1a1a;"
        elif years_agreed:
            _st, _sc = "✅ 기간 합의 완료", "color:#66dd88;background:#1a2a1e;"
        else:
            _st = f"기간 합의 전 (제시 {self._yrs_text(c_yrs)} / 희망 {self._yrs_text(target_yrs)})"
            _sc = "color:#777777;background:#222222;"
        st_lbl = self._wrap_label(_st, _sc + "border:1px solid #333;border-radius:4px;padding:4px;"
                                  "font-size:11px;", need, center)
        lay.addWidget(st_lbl)

        # 최종 계약은 "연봉·기간 둘 다 결렬 없이" 끝나야 성사(안전망 오퍼는 결렬돼도 입단 가능).
        join_btn = QPushButton(("✅ 입단" if self.lang == "ko" else "✅ Join")
                               + (" (보장)" if is_safe and self.lang == "ko" else
                                  " (Guaranteed)" if is_safe else ""))
        join_btn.setObjectName("selectBtn")
        join_btn.setEnabled(is_safe or not (failed or years_failed))
        join_btn.clicked.connect(lambda _, i=idx: self._select(i))
        lay.addWidget(self._measured(join_btn, need))

        card._card_need = max(need or [0]) + 24 + 2
        return card

    def _on_years_target_changed(self, idx, combo):
        """[2026-08 신설] 콤보박스로 희망 계약기간을 바꾸면 그 값을 목표로
        저장하고 카드를 다시 그린다(합의 완료 상태였다면 새 목표와 다시
        비교해 버튼이 재활성화될 수 있음 — 단, 시도 횟수/결렬 여부는
        그대로라 이미 결렬된 오퍼는 재협상 불가)."""
        new_target = combo.currentData()
        if new_target is None:
            return
        self.years_target[idx] = new_target
        self._persist()
        QTimer.singleShot(0, self._render_cards)

    def _select(self, idx):
        self.chosen = dict(self.offers[idx])
        self.chosen["salary"] = self.offer_salaries[idx]
        # [2026-08 신설] 기간 협상으로 합의된 값을 최종 계약에 그대로
        # 넘긴다(join_team이 offer.get("contract_years")를 우선 사용).
        self.chosen["contract_years"] = self.offer_years[idx]
        self.accept()

    def _negotiate(self, idx):
        if self.neg_used[idx] <= 0 or idx in self.neg_failed:
            return
        self.neg_used[idx] -= 1
        old_sal = self.offer_salaries[idx]
        delta   = self._neg_rng.randint(10, 30)
        # [2026-08 재설계] 성공확률도 고정값이 아니라 실제 입단 성공확률
        # (join_prob) 기반 — 직접 지원 화면의 "성공 가능성"과 같은 기준.
        from game_engine import negotiation_success_prob
        success = self._neg_rng.random() < negotiation_success_prob(self._offer_join_prob(idx))

        if success:
            new_sal = int(old_sal * (1 + delta/100))
            self.offer_salaries[idx] = new_sal
            # 팝업 없이 토스트만
            show_toast(self, f"✅ +{delta}%  {fmt_money(old_sal)} → {fmt_money(new_sal)}", "#006622", 1400)
        else:
            if self.neg_used[idx] == 0:
                self.neg_failed.add(idx)
                # [2026-08 신설, 신민용 확정] 협상 결렬이 뜨면 그 팀은
                # 연도 기준 냉각기(거절연도+2년차까지 차단)에 들어간다.
                # join(입단 강제)/auto_offer(일반 오퍼) 양쪽 다 이 창을
                # 같이 쓰므로 여기 한 곳만 훅해도 두 경로 모두 커버된다.
                from game_engine import _record_team_offer_cooldown
                _record_team_offer_cooldown(self.offers[idx]["team_id"])
                show_toast(self, "❌ 협상 결렬  입단 불가", "#cc0000", 1400)
            else:
                show_toast(self, "협상 실패" if get_difficulty() == "hard"   # 어려움: 남은 횟수 숨김
                           else f"협상 실패  남은 기회: {self.neg_used[idx]}회", "#cc4400", 1200)

        self._after_negotiate_round()

    def _negotiate_years(self, idx):
        """[2026-08 재설계, 신민용 확정] 계약 '기간' 협상 — 연봉 협상과
        완전히 독립된 트랙(독립 시도 횟수, 독립 성공/실패). 나이 기반
        자동 추정이 아니라, 플레이어가 카드의 콤보박스로 직접 고른
        self.years_target[idx]를 향해서만 움직인다. 이미 목표와 같으면
        애초에 버튼이 비활성화된다(_make_card 참고)."""
        from game_engine import (_contract_years_neg_delta, _record_team_offer_cooldown,
                                  negotiation_success_prob)
        from constants import CONTRACT_YEARS_MIN, CONTRACT_YEARS_MAX
        if self.years_used[idx] <= 0 or idx in self.years_failed:
            return
        self.years_used[idx] -= 1

        target  = self.years_target.get(idx, self.offer_years[idx])
        old_yrs = self.offer_years[idx]
        tier    = self.offers[idx].get("tier", 3)
        delta   = _contract_years_neg_delta(tier)
        # [2026-08 재설계] 연봉 협상과 동일하게 실제 입단 성공확률 기반.
        success = self._neg_rng.random() < negotiation_success_prob(self._offer_join_prob(idx))

        if success and old_yrs != target:
            direction = 1 if target > old_yrs else -1
            step = min(delta, abs(target - old_yrs))
            new_yrs = old_yrs + direction * step
            new_yrs = max(CONTRACT_YEARS_MIN, min(CONTRACT_YEARS_MAX, new_yrs))
            try:   # [2026-10] 병역 미필 + 해외 구단 → 30세 시즌까지
                from game_engine import _my_contract_years_cap
                _ycap = _my_contract_years_cap(None, self.offers[idx].get("country"))
                if _ycap is not None:
                    new_yrs = max(CONTRACT_YEARS_MIN, min(new_yrs, _ycap))
            except Exception:
                pass
            self.offer_years[idx] = new_yrs
            show_toast(self, f"✅ 기간 협상 성공  {self._yrs_text(old_yrs)} → {self._yrs_text(new_yrs)}",
                       "#006622", 1400)
        else:
            if self.years_used[idx] == 0:
                self.years_failed.add(idx)
                _record_team_offer_cooldown(self.offers[idx]["team_id"])
                show_toast(self, "❌ 기간 협상 결렬  입단 불가", "#cc0000", 1400)
            else:
                show_toast(self, "기간 협상 실패" if get_difficulty() == "hard"   # 어려움: 남은 횟수 숨김
                           else f"기간 협상 실패  남은 기회: {self.years_used[idx]}회",
                           "#cc4400", 1200)

        self._after_negotiate_round()

    def _after_negotiate_round(self):
        """[2026-08 신설] 연봉/기간 두 협상 메서드가 공유하는 뒷정리 —
        '전부 결렬' 판정(안전망 오퍼가 아닌데 연봉·기간 둘 중 하나라도
        결렬된 오퍼는 입단 불가로 친다), 저장, 카드 갱신."""
        dead = 0
        for i in range(len(self.offers)):
            is_safe_i = bool(self.offers[i].get("safe")) and self._force
            if is_safe_i:
                continue
            if i in self.neg_failed or i in self.years_failed:
                dead += 1
        # [전부 결렬 구제] 입단 가능한 오퍼가 하나도 안 남으면, 첫 입단
        #   (force_select)이라도 닫기를 풀어 1년 더 훈련하도록 빠져나가게 한다.
        if self.offers and dead >= len(self.offers):
            self.all_failed = True
            if self._force and self._close_btn is not None:
                self._close_btn.setEnabled(True)
                self._close_btn.setText("1년 더 훈련 (전부 결렬)"
                                        if self.lang == "ko" else "Train 1 more year")
                self._close_btn.setToolTip("")
            show_toast(self, "⚠ 모든 협상이 결렬되었습니다. 1년 더 훈련합니다.",
                       "#cc6600", 1800)

        self._persist()
        # 카드 갱신 (연봉/기간 수치 반영)
        QTimer.singleShot(100, self._render_cards)