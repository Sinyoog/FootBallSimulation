"""
ui/agent_window.py
에이전트 오퍼 최대 3개 표시. 매달 진행(advance_4weeks) 후 새로 갱신됨.
"""
import random
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from game_engine import get_player, update_player, fmt_money, add_log
from constants import AGENT_GRADES, AGENT_FEE_RATE
from database import get_conn

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
#agCard { background:#252525; border:1px solid #333; border-radius:8px; }
#agCard:hover { border-color:#00cc44; }
#selectBtn { background:#2a6a2a; color:white; border:none; border-radius:4px; padding:6px 16px; }
#selectBtn:hover { background:#3a8a3a; }
#currentBtn { background:#333; color:#888; border:1px solid #444; border-radius:4px; padding:6px 16px; }
#hintLabel { color:#666; font-size:11px; }
"""

AGENT_INFO = {
    "F": ("🔴", "무명 에이전트",  "수수료 없음. 하위리그 오퍼만 제공."),
    "E": ("🟠", "신입 에이전트",   "수수료 3%. E~F등급 리그 위주."),
    "D": ("🟡", "로컬 에이전트",   "수수료 6%. D등급 리그까지."),
    "C": ("🟢", "중견 에이전트",   "수수료 10%. C등급 리그까지."),
    "B": ("🔵", "전문 에이전트",   "수수료 15%. B등급 리그. 상위리그 오퍼+1."),
    "A": ("🟣", "유명 에이전트",   "수수료 20%. A급 리그. 상위리그 오퍼+2."),
    "S": ("⭐", "슈퍼 에이전트",   "수수료 28%. S급 리그. 상위리그 오퍼+3."),
}

# 에이전트 기본 계약금 (천원 단위)
#  [익스플로잇 수정] 예전엔 '국가 등급'에 연동했더니, 약소국 출신은 S급
#  에이전트를 헐값에 잡은 뒤 강팀으로 이적해 비싼 연봉에 싼 수수료를 평생
#  적용받는 구멍이 있었다. 이제 '내 OVR(=시장가치)'로 비용을 책정한다.
#  컬럼: 내 OVR 구간 (T1>=85 / T2 78~84 / T3 70~77 / T4 62~69 / T5 54~61 / T6 <54)
_BASE_COST = {
    # agent_grade → {ovr_tier → 기본 계약금}
    "E": {"T1":8000,   "T2":6000,   "T3":4000,   "T4":2500,   "T5":1500,   "T6":800},
    "D": {"T1":30000,  "T2":20000,  "T3":12000,  "T4":7000,   "T5":3500,   "T6":1800},
    "C": {"T1":100000, "T2":70000,  "T3":40000,  "T4":20000,  "T5":9000,   "T6":4500},
    "B": {"T1":300000, "T2":200000, "T3":120000, "T4":60000,  "T5":25000,  "T6":10000},
    "A": {"T1":800000, "T2":500000, "T3":300000, "T4":150000, "T5":60000,  "T6":25000},
    "S": {"T1":2000000,"T2":1200000,"T3":700000, "T4":350000, "T5":150000, "T6":60000},
}

def _ovr_tier(ovr: int) -> str:
    """내 OVR을 계약금 구간(T1~T6)으로 변환. 높을수록 비싸다(시장가치 반영)."""
    if ovr >= 85: return "T1"
    if ovr >= 78: return "T2"
    if ovr >= 70: return "T3"
    if ovr >= 62: return "T4"
    if ovr >= 54: return "T5"
    return "T6"

def _calc_agent_cost(agent_grade: str, ovr_tier: str) -> int:
    """에이전트 계약금: 내 OVR(시장가치) 구간 기반 + ±30% 랜덤."""
    if agent_grade == "F":
        return 0
    base = _BASE_COST.get(agent_grade, {}).get(ovr_tier, 1000)
    # ±30% 랜덤 (50천원 단위 반올림)
    varied = int(base * random.uniform(0.70, 1.30))
    return max(10, round(varied / 50) * 50)

def _gen_agent_offers(cur_grade: str) -> list:
    """현재 등급 기준 최대 3개 오퍼. 각 오퍼는 (등급, 변형타입)."""
    cur_idx = AGENT_GRADES.index(cur_grade)
    pool = []
    for g in AGENT_GRADES:
        gi = AGENT_GRADES.index(g)
        diff = gi - cur_idx
        if -1 <= diff <= 2:
            weight = 3 if diff == 1 else (2 if diff == 0 else 1)
            pool.extend([g] * weight)
    if not pool:
        pool = [cur_grade]
    count = random.randint(1, 3)
    return [random.choice(pool) for _ in range(count)]


# 같은 등급 내 에이전트 '협상 스타일' 변형:
#  같은 등급이라도 계약금↑수수료↓ vs 계약금↓수수료↑ 의 트레이드오프.
#  → 장기 재직 계획이면 수수료 낮은(=계약금 높은) 쪽이 이득,
#    단기/저자산이면 계약금 낮은(=수수료 높은) 쪽이 이득. 무조건 싼 게 정답 아님.
AGENT_VARIANTS = [
    # (라벨, 계약금 배수, 수수료 가산)
    ("실속형",   0.55, +0.04),   # 계약금 싸지만 수수료 비쌈
    ("표준형",   1.00,  0.00),   # 등급 기본
    ("거물형",   1.85, -0.04),   # 계약금 비싸지만 수수료 쌈
]

def _make_variant(grade, ovr_tier):
    """에이전트 등급 + 내 OVR 구간 기준 (계약금, 수수료율, 라벨) 변형 1개 생성."""
    label, cost_mult, fee_add = random.choice(AGENT_VARIANTS)
    base_cost = _calc_agent_cost(grade, ovr_tier)
    cost = max(0, int(base_cost * cost_mult))
    base_fee = AGENT_FEE_RATE.get(grade, 0.0)
    fee = round(max(0.0, base_fee + fee_add), 3)
    return cost, fee, label


class AgentWindow(QDialog):
    def __init__(self, lang="ko", parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("에이전트 변경" if lang=="ko" else "Change Agent")
        self.setMinimumSize(460, 400)
        self.setStyleSheet(STYLE)
        self.lang = lang
        p = get_player()
        self.cur_grade    = p.get("agent_grade","F") if p else "F"
        self.ovr_tier = _ovr_tier(p.get("ovr", 40) if p else 40)
        self.offers = _gen_agent_offers(self.cur_grade)
        # 오퍼별 변형: (계약금, 수수료율, 라벨) — 같은 등급도 다르게
        self.variants = {i: _make_variant(g, self.ovr_tier)
                         for i, g in enumerate(self.offers)}
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        hdr = QLabel("👔 에이전트 오퍼" if self.lang=="ko" else "👔 Agent Offers")
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        root.addWidget(hdr)

        hint = QLabel("※ 오퍼는 이번 달 진행 후 새로 갱신됩니다")
        hint.setObjectName("hintLabel")
        root.addWidget(hint)

        cur_info = AGENT_INFO[self.cur_grade]
        cur_box = QFrame(); cur_box.setObjectName("agCard")
        cbl = QVBoxLayout(cur_box); cbl.setContentsMargins(12,8,12,8)
        cbl.addWidget(QLabel(f"현재: {cur_info[0]} [{self.cur_grade}] {cur_info[1]}"))
        root.addWidget(cur_box)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#1e1e1e;}")
        inner = QWidget(); inner_lay = QVBoxLayout(inner); inner_lay.setSpacing(8)
        scroll.setWidget(inner)

        if not self.offers:
            inner_lay.addWidget(QLabel("현재 오퍼가 없습니다."))
        else:
            for i, g in enumerate(self.offers):
                inner_lay.addWidget(self._make_card(i, g))
        inner_lay.addStretch()
        root.addWidget(scroll)

        # 토스트 레이블
        self._toast = QLabel("", self)
        self._toast.setStyleSheet(
            "background:#cc0000;color:white;border-radius:6px;"
            "padding:6px 14px;font-size:12px;font-weight:bold;")
        self._toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._toast.hide()
        root.addWidget(self._toast)

        close = QPushButton("닫기" if self.lang=="ko" else "Close")
        close.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                            "border-radius:4px;padding:6px;")
        close.clicked.connect(self.reject)
        root.addWidget(close)

    def _show_toast(self, msg, duration=1200):
        self._toast.setText(msg)
        self._toast.show()
        QTimer.singleShot(duration, self._toast.hide)

    def _make_card(self, idx, grade):
        from constants import AGENT_UPPER_LEAGUE_BONUS
        icon, name, desc = AGENT_INFO[grade]
        cost, fee, label = self.variants[idx]
        card = QFrame(); card.setObjectName("agCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(12,10,12,10)

        h1 = QHBoxLayout()
        nl = QLabel(f"{icon}  [{grade}] {name}  · {label}")
        nl.setStyleSheet("font-size:13px;font-weight:bold;color:#e0e0e0;")
        h1.addWidget(nl); h1.addStretch()
        cl.addLayout(h1)
        cl.addWidget(QLabel(desc))
        # 상위리그 오퍼 보너스 안내 (실제 효과)
        bonus = AGENT_UPPER_LEAGUE_BONUS.get(grade, 0)
        if bonus > 0:
            bl = QLabel(f"📈 실력보다 최대 +{bonus}등급 높은 리그 오퍼 가능")
            bl.setStyleSheet("color:#66aaff;font-size:11px;")
            cl.addWidget(bl)
        cost_lbl = QLabel(f"수수료 {fee*100:.0f}%  |  계약금 {fmt_money(cost)}")
        cost_lbl.setStyleSheet("color:#00cc44;" if grade != self.cur_grade else "color:#888;")
        cl.addWidget(cost_lbl)

        h2 = QHBoxLayout()
        if grade == self.cur_grade:
            btn = QPushButton("현재 등급 (재계약)"); btn.setObjectName("selectBtn")
            btn.clicked.connect(lambda _, g=grade, c=cost, f=fee: self._select(g, c, f))
        else:
            btn = QPushButton("✅ 선택" if self.lang=="ko" else "✅ Select")
            btn.setObjectName("selectBtn")
            btn.clicked.connect(lambda _, g=grade, c=cost, f=fee: self._select(g, c, f))
        h2.addWidget(btn); h2.addStretch()
        cl.addLayout(h2)
        return card

    def _select(self, grade, cost, fee):
        p = get_player()
        if not p: return
        assets = p.get("total_assets", 0)
        if assets < cost:
            self._show_toast(
                f"💸 자산 부족  필요 {fmt_money(cost)}  현재 {fmt_money(assets)}", 1500)
            return
        # 확인 없이 바로 계약 (토스트로 충분)
        update_player(agent_grade=grade, agent_fee_rate=fee,
                      total_assets=assets - cost)
        add_log(f"👔 에이전트 [{grade}] 계약  수수료 {fee*100:.0f}%  계약금 -{fmt_money(cost)}", "event")
        self.accept()