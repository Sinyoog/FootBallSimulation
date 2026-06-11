"""
ui/offer_window.py  ─  이적 오퍼 선택 + 협상
"""
import random
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from game_engine import fmt_money
from ui.center_panel import show_toast

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
#offerCard { background:#252525; border:1px solid #333; border-radius:8px; padding:8px; }
#grade_S { color:#ff9900; font-weight:bold; }
#grade_A { color:#ffcc00; font-weight:bold; }
#grade_B { color:#00ccff; font-weight:bold; }
#grade_C { color:#00ff66; }
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
    def __init__(self, offers: list, lang="ko", parent=None, title="📋 이적 오퍼", force_select=False):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle(title)
        self.setMinimumSize(580, 500)
        self.setStyleSheet(STYLE)
        self.lang       = lang
        self.title_text = title
        self.chosen     = None
        self._force     = force_select
        self.offers     = offers
        self.neg_used: dict[int, int] = {}
        self.offer_salaries: list[int] = [o["salary"] for o in offers]
        self.neg_failed: set[int] = set()

        for i in range(len(offers)):
            self.neg_used[i] = random.randint(1, 3)

        self._build()

    def _build(self):
        root = QVBoxLayout(self)

        hdr = QLabel(self.title_text)
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        root.addWidget(hdr)

        if not self.offers:
            lbl = QLabel("오퍼가 없습니다." if self.lang=="ko" else "No offers available.")
            lbl.setObjectName("noOffer")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(lbl)
        else:
            scroll = QScrollArea(); scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
            inner  = QWidget(); self.cards_lay = QVBoxLayout(inner)
            self.cards_lay.setSpacing(8)
            scroll.setWidget(inner)
            root.addWidget(scroll)
            self._render_cards()

        close = QPushButton("닫기" if self.lang=="ko" else "Close")
        if self._force:
            close.setEnabled(False)
            close.setToolTip("팀을 선택해야 합니다")
        close.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                            "border-radius:4px;padding:6px;")
        close.clicked.connect(self.reject)
        root.addWidget(close)

    def _render_cards(self):
        while self.cards_lay.count():
            item = self.cards_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for i, offer in enumerate(self.offers):
            self.cards_lay.addWidget(self._make_card(i, offer))
        self.cards_lay.addStretch()

    def _make_card(self, idx, offer):
        card = QFrame(); card.setObjectName("offerCard")
        lay  = QVBoxLayout(card); lay.setContentsMargins(12,10,12,10); lay.setSpacing(5)

        # ── 행1: 팀명 + 등급 + 티어 ──────────────────────────
        h1 = QHBoxLayout(); h1.setSpacing(6)
        tl = QLabel(f"{offer['flag']}  {offer['team_name']}")
        tl.setStyleSheet("font-size:14px;font-weight:bold;color:#e0e0e0;")
        gl  = QLabel(f"[{offer['grade']}급]"); gl.setObjectName(f"grade_{offer['grade']}")
        trl = QLabel(f"{offer['tier']}부");    trl.setObjectName(f"tier{offer['tier']}")
        h1.addWidget(tl); h1.addWidget(gl); h1.addWidget(trl); h1.addStretch()
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

        # ── 행3: 연봉 + 계약 기간 ────────────────────────────
        from game_engine import _calc_contract_years, get_player
        p_now = get_player()
        age_now = p_now.get("age", 17) if p_now else 17
        c_yrs = _calc_contract_years(age_now, offer.get("tier", 3))
        h3 = QHBoxLayout(); h3.setSpacing(8)
        sl = QLabel(f"💰 연 {fmt_money(self.offer_salaries[idx])}"
                    f"  [월 {fmt_money(self.offer_salaries[idx]//12)}]")
        sl.setStyleSheet("color:#00cc44;")
        cl = QLabel(f"📋 {c_yrs}년 계약")
        cl.setStyleSheet("color:#ffcc44; font-size:11px;")
        h3.addWidget(sl); h3.addStretch(); h3.addWidget(cl)
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

        join_btn = QPushButton("✅ 입단" if self.lang=="ko" else "✅ Join")
        join_btn.setObjectName("selectBtn")
        join_btn.setEnabled(not failed)   # 결렬 시 입단도 비활성
        join_btn.clicked.connect(lambda _, i=idx: self._select(i))

        if failed:
            neg_btn = QPushButton("❌ 협상 결렬")
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

        # 카드 갱신 (연봉 수치 반영)
        QTimer.singleShot(100, self._render_cards)