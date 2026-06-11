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

# 국가 등급별 에이전트 기본 계약금 (만원)
# 같은 에이전트 등급이라도 국가 수준에 따라 다름
_BASE_COST = {
    # agent_grade → {country_grade → 기본 계약금}
    "E": {"S":800,  "A":600,  "B":400,  "C":250,  "D":150,  "E":80,   "F":40},
    "D": {"S":3000, "A":2000, "B":1200, "C":700,  "D":350,  "E":180,  "F":90},
    "C": {"S":10000,"A":7000, "B":4000, "C":2000, "D":900,  "E":450,  "F":200},
    "B": {"S":30000,"A":20000,"B":12000,"C":6000, "D":2500, "E":1000, "F":400},
    "A": {"S":80000,"A":50000,"B":30000,"C":15000,"D":6000, "E":2500, "F":1000},
    "S": {"S":200000,"A":120000,"B":70000,"C":35000,"D":15000,"E":6000,"F":2500},
}

def _get_country_grade(p) -> str:
    """내 선수의 국적 등급 조회."""
    if not p: return "F"
    conn = get_conn()
    row = conn.execute("SELECT grade FROM countries WHERE name=?",
                       (p.get("nationality",""),)).fetchone()
    conn.close()
    return row["grade"] if row else "F"

def _calc_agent_cost(agent_grade: str, country_grade: str) -> int:
    """에이전트 계약금: 국가 등급 기반 + ±30% 랜덤."""
    if agent_grade == "F":
        return 0
    base = _BASE_COST.get(agent_grade, {}).get(country_grade, 100)
    # ±30% 랜덤 (5만원 단위 반올림)
    varied = int(base * random.uniform(0.70, 1.30))
    return max(1, round(varied / 5) * 5)

def _gen_agent_offers(cur_grade: str) -> list:
    """현재 등급 기준 최대 3개 오퍼."""
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
        self.country_grade = _get_country_grade(p)
        self.offers = _gen_agent_offers(self.cur_grade)
        # 오퍼별 계약금 (같은 등급도 다르게)
        self.costs = {i: _calc_agent_cost(g, self.country_grade)
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
        icon, name, desc = AGENT_INFO[grade]
        cost = self.costs[idx]
        card = QFrame(); card.setObjectName("agCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(12,10,12,10)

        h1 = QHBoxLayout()
        nl = QLabel(f"{icon}  [{grade}] {name}")
        nl.setStyleSheet("font-size:13px;font-weight:bold;color:#e0e0e0;")
        h1.addWidget(nl); h1.addStretch()
        cl.addLayout(h1)
        cl.addWidget(QLabel(desc))
        cost_lbl = QLabel(f"수수료 {int(AGENT_FEE_RATE[grade]*100)}%  |  계약금 {fmt_money(cost)}")
        cost_lbl.setStyleSheet("color:#00cc44;" if grade != self.cur_grade else "color:#888;")
        cl.addWidget(cost_lbl)

        h2 = QHBoxLayout()
        if grade == self.cur_grade:
            btn = QPushButton("현재 에이전트"); btn.setObjectName("currentBtn")
            btn.setEnabled(False)
        else:
            btn = QPushButton("✅ 선택" if self.lang=="ko" else "✅ Select")
            btn.setObjectName("selectBtn")
            btn.clicked.connect(lambda _, g=grade, c=cost: self._select(g, c))
        h2.addWidget(btn); h2.addStretch()
        cl.addLayout(h2)
        return card

    def _select(self, grade, cost):
        p = get_player()
        if not p: return
        assets = p.get("total_assets", 0)
        if assets < cost:
            self._show_toast(
                f"💸 자산 부족  필요 {fmt_money(cost)}  현재 {fmt_money(assets)}", 1500)
            return
        # 확인 없이 바로 계약 (토스트로 충분)
        update_player(agent_grade=grade, total_assets=assets - cost)
        add_log(f"👔 에이전트 [{grade}]로 변경  계약금 -{fmt_money(cost)}", "event")
        self.accept()