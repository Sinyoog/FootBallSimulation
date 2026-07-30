"""
ui/offer_window.py  ─  이적 오퍼 선택 + 협상
"""
import random
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from game_engine import fmt_money
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
#noOffer { color:#666666; font-size:13px; }
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
        else:
            self.offer_salaries: list[int] = [o["salary"] for o in offers]
            self.neg_used: dict[int, int] = {}
            for i in range(len(offers)):
                self.neg_used[i] = random.randint(1, 3)
            self.neg_failed: set[int] = set()
            self._applied_count = 0

        self._build()
        # 창이 만들어지는 즉시(사용자가 아무것도 안 눌러도) 저장해둔다 —
        # 그래야 이 창을 띄운 직후 바로 꺼져도 다음에 같은 상태로 복원된다.
        self._persist()

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

        if not self.offers and not self.apply_slots:
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
                self.cards_lay.setColumnStretch(0, 1)
                self.cards_lay.setColumnStretch(1, 1)
            else:
                self.cards_lay = QVBoxLayout(inner)
                self.cards_lay.setSpacing(8)
            scroll.setWidget(inner)
            root.addWidget(scroll)
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

    def _render_cards(self):
        while self.cards_lay.count():
            item = self.cards_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        empty_slots = max(0, self.apply_slots - self._applied_count)
        if self.grid:
            for i, offer in enumerate(self.offers):
                row, col = divmod(i, 2)
                self.cards_lay.addWidget(self._make_card(i, offer), row, col)
            base = len(self.offers)
            for j in range(empty_slots):
                row, col = divmod(base + j, 2)
                self.cards_lay.addWidget(self._make_apply_slot_card(), row, col)
            self.cards_lay.setRowStretch(self.cards_lay.rowCount(), 1)
        else:
            for i, offer in enumerate(self.offers):
                self.cards_lay.addWidget(self._make_card(i, offer))
            for j in range(empty_slots):
                self.cards_lay.addWidget(self._make_apply_slot_card())
            self.cards_lay.addStretch()

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
        return card

    def _open_apply_search(self):
        from ui.apply_window import ApplyWindow
        dlg = ApplyWindow(self.lang, self)
        dlg.exec()
        if dlg.chosen:
            offer = dlg.chosen
            idx = len(self.offers)
            self.offers.append(offer)
            self.offer_salaries.append(offer["salary"])
            self.neg_used[idx] = random.randint(1, 3)
            self._applied_count += 1
            self._persist()
        self._render_cards()

    def _make_card(self, idx, offer):
        card = QFrame(); card.setObjectName("offerCard")
        lay  = QVBoxLayout(card); lay.setContentsMargins(12,10,12,10); lay.setSpacing(5)

        # ── 행1: 팀명 + 등급 + 티어 ──────────────────────────
        h1 = QHBoxLayout(); h1.setSpacing(6)
        tl = QLabel(f"{offer['flag']}  {offer['team_name']}")
        tl.setStyleSheet("font-size:14px;font-weight:bold;color:#e0e0e0;")
        gl  = QLabel(f"[{offer['grade']}급]"); gl.setObjectName(f"grade_{offer['grade']}")
        trl = QLabel(f"{offer['tier']}부");    trl.setObjectName(f"tier{offer['tier']}")
        h1.addWidget(tl); h1.addWidget(gl); h1.addWidget(trl)
        _zone = offer.get("_zone")
        _zone_txt = {"domestic": "🏠 자국", "prev_league": "🏟 직전리그",
                     "hometown": "🌍 고향", "foreign": "✈ 해외",
                     "applied": "🔎 직접 지원", "youth_scout": "🌟 유스 스카우트"}.get(_zone)
        if _zone_txt:
            zl = QLabel(_zone_txt)
            zl.setStyleSheet("color:#888; font-size:10px; background:#2a2a2a;"
                             "border-radius:3px; padding:1px 5px;")
            h1.addWidget(zl)
        h1.addStretch()
        lay.addLayout(h1)

        # ── 행2: 국가 | 리그명 (분리) ────────────────────────
        h2 = QHBoxLayout(); h2.setSpacing(0)
        country_flag = offer.get("flag", "")
        country_name = offer.get("country", "")
        league_name  = offer.get("league_name", "")

        country_lbl = QLabel(f"{country_flag} {country_name}")
        country_lbl.setStyleSheet(
            "color:#aaddff; font-size:11px; font-weight:bold;"
            "background:#1a2a3a; border-radius:3px; padding:1px 5px;")
        sep_lbl = QLabel("  ›  ")
        sep_lbl.setStyleSheet("color:#555555; font-size:11px;")
        league_lbl = QLabel(f"🏆 {league_name}")
        league_lbl.setStyleSheet("color:#cccccc; font-size:11px;")
        h2.addWidget(country_lbl); h2.addWidget(sep_lbl); h2.addWidget(league_lbl)
        h2.addStretch()
        lay.addLayout(h2)

        # ── 행3: 연봉 + 이적료 + 계약 기간 ────────────────────
        from game_engine import _calc_contract_years, get_player
        p_now = get_player()
        age_now = p_now.get("age", 17) if p_now else 17
        c_yrs = _calc_contract_years(age_now, offer.get("tier", 3))
        h3 = QHBoxLayout(); h3.setSpacing(8)
        _sal = self.offer_salaries[idx]
        _sal_txt = "💰 무급" if _sal == 0 else (
            f"💰 연 {fmt_money(_sal)}  [주 {fmt_money(max(1, _sal // 52))}]")
        sl = QLabel(_sal_txt)
        sl.setStyleSheet("color:#00cc44;")
        h3.addWidget(sl)
        # [2026-07 신설] 이적료 표시 — 무소속(FA) 지원이면 0이라 아예 안
        # 보여주고, 계약 중인데 오퍼가 왔으면(=유료 이적) 표시한다.
        _fee = offer.get("transfer_fee", 0)
        if _fee:
            fl = QLabel(f"🔄 이적료 {fmt_money(_fee)}")
            fl.setStyleSheet("color:#66aaff; font-size:11px;")
            h3.addWidget(fl)
        cl = QLabel(f"📋 {c_yrs}년 계약")
        cl.setStyleSheet("color:#ffcc44; font-size:11px;")
        h3.addStretch(); h3.addWidget(cl)
        lay.addLayout(h3)

        # ── 행4: 성적 ─────────────────────────────────────────
        rank_info = offer.get("rank_info", "")
        if rank_info:
            lines = rank_info.split("\n")
            rank_lbl = QLabel(f"📊 {lines[0]}")
            rank_lbl.setStyleSheet("color:#aaaaaa; font-size:11px;")
            lay.addWidget(rank_lbl)
            if len(lines) > 1:
                promo_lbl = QLabel(f"   {lines[1].strip()}")
                promo_lbl.setStyleSheet("color:#888866; font-size:10px;")
                lay.addWidget(promo_lbl)

        h3 = QHBoxLayout()
        failed   = idx in self.neg_failed
        neg_left = self.neg_used.get(idx, 0)
        is_safe  = bool(offer.get("safe")) and self._force

        join_btn = QPushButton(("✅ 입단" if self.lang=="ko" else "✅ Join")
                                + (" (보장)" if is_safe and self.lang=="ko" else
                                   " (Guaranteed)" if is_safe else ""))
        join_btn.setObjectName("selectBtn")
        join_btn.setEnabled(is_safe or not failed)   # 안전망 오퍼는 결렬돼도 입단 가능
        join_btn.clicked.connect(lambda _, i=idx: self._select(i))

        if failed:
            neg_btn = QPushButton("❌ 협상 결렬 (연봉 유지)" if is_safe else "❌ 협상 결렬")
            neg_btn.setObjectName("negBtn"); neg_btn.setEnabled(False)
        else:
            neg_btn = QPushButton(f"💬 협상 ({neg_left}회)")
            neg_btn.setObjectName("negBtn")
            neg_btn.setEnabled(neg_left > 0)
            neg_btn.clicked.connect(lambda _, i=idx: self._negotiate(i))

        h3.addWidget(join_btn); h3.addWidget(neg_btn); h3.addStretch()
        lay.addLayout(h3)
        return card

    def _select(self, idx):
        self.chosen = dict(self.offers[idx])
        self.chosen["salary"] = self.offer_salaries[idx]
        self.accept()

    def _negotiate(self, idx):
        if self.neg_used[idx] <= 0 or idx in self.neg_failed:
            return
        self.neg_used[idx] -= 1
        old_sal = self.offer_salaries[idx]
        delta   = random.randint(10, 30)
        success = random.random() < 0.55

        if success:
            new_sal = int(old_sal * (1 + delta/100))
            self.offer_salaries[idx] = new_sal
            # 팝업 없이 토스트만
            show_toast(self, f"✅ +{delta}%  {fmt_money(old_sal)} → {fmt_money(new_sal)}", "#006622", 1400)
        else:
            if self.neg_used[idx] == 0:
                self.neg_failed.add(idx)
                show_toast(self, "❌ 협상 결렬  입단 불가", "#cc0000", 1400)
            else:
                show_toast(self, f"협상 실패  남은 기회: {self.neg_used[idx]}회", "#cc4400", 1200)

        # [전부 결렬 구제] 모든 오퍼가 결렬되면 입단할 곳이 없으므로,
        #   첫 입단(force_select)이라도 닫기를 풀어 1년 더 훈련하도록 빠져나가게 한다.
        if self.offers and len(self.neg_failed) >= len(self.offers):
            self.all_failed = True
            if self._force and self._close_btn is not None:
                self._close_btn.setEnabled(True)
                self._close_btn.setText("1년 더 훈련 (전부 결렬)"
                                        if self.lang == "ko" else "Train 1 more year")
                self._close_btn.setToolTip("")
            show_toast(self, "⚠ 모든 협상이 결렬되었습니다. 1년 더 훈련합니다.",
                       "#cc6600", 1800)

        self._persist()
        # 카드 갱신 (연봉 수치 반영)
        QTimer.singleShot(100, self._render_cards)