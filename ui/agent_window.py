"""
ui/agent_window.py
에이전트 오퍼 최소 5개 표시. [2026-08 재설계, 신민용 리포트: "창을 열
때마다 후보가 바뀌는 게 비현실적이다"] 시즌 단위로 후보 풀을 고정해서
저장한다 — 같은 시즌 안에는(입단을 나갔다 다시 들어와도) 항상 같은
후보가 뜨고, 비시즌(새 시즌 시작) 전환 시점에만 새로 생성된다.
"""
import json
import random
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from game_engine import get_player, update_player, fmt_money, add_log, get_state
from constants import AGENT_GRADES, AGENT_FEE_RATE, AGENT_NONE, get_country_continent
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
    AGENT_NONE: ("⚪", "에이전트 없음", "수수료 없음. 하위리그 오퍼만 제공."),
    "F": ("🔴", "무명 에이전트",  "수수료 3%. E~F등급 리그 위주."),
    "E": ("🟠", "신입 에이전트",   "수수료 6%. D등급 리그까지."),
    "D": ("🟡", "로컬 에이전트",   "수수료 10%. C등급 리그까지."),
    "C": ("🟢", "중견 에이전트",   "수수료 15%. B등급 리그. 상위리그 오퍼+2."),
    "B": ("🔵", "전문 에이전트",   "수수료 20%. A급 리그. 상위리그 오퍼+3."),
    "A": ("🟣", "유명 에이전트",   "수수료 28%. S급 리그. 상위리그 오퍼+3."),
    "S": ("⭐", "슈퍼 에이전트",   "수수료 35%. 최상위 리그. 상위리그 오퍼+4."),
}

# [2026-08 신설, 신민용 요청: "대륙별 전문 에이전트 특성 추가 — 아시아
# 에이전트는 아시아 팀에 보정, 유럽 에이전트는 다른 대륙보다 뜰 확률이
# 적으며, 내 국적 대륙에 맞는 에이전트가 뜰 확률이 2배"]
AGENT_CONTINENTS = ["유럽", "아시아", "아프리카", "북미", "남미", "오세아니아"]
AGENT_CONTINENT_WEIGHT = {
    "유럽": 0.5,   # 유럽 에이전트는 세계 최대 시장을 다루는 만큼 희소성을 둬서
                   # 다른 대륙보다 덜 자주 등장하게 한다(신민용 확정).
}
_DEFAULT_CONTINENT_WEIGHT = 1.0
_MY_CONTINENT_WEIGHT_MULT = 2.0  # 내 국적 대륙 에이전트는 뜰 확률 2배
# 에이전트가 자기 전문 대륙 소속 팀 오퍼/입단 판정에 주는 마진 보너스
# (game_engine._team_fits_me에서 사용 — AGENT_CONTINENT_BONUS 참고)
AGENT_CONTINENT_BONUS = 2.0

_MIN_AGENT_OFFERS = 5

# 에이전트 기본 계약금 (천원 단위)
#  [익스플로잇 수정] 예전엔 '국가 등급'에 연동했더니, 약소국 출신은 S급
#  에이전트를 헐값에 잡은 뒤 강팀으로 이적해 비싼 연봉에 싼 수수료를 평생
#  적용받는 구멍이 있었다. 이제 '내 OVR(=시장가치)'로 비용을 책정한다.
#  컬럼: 내 OVR 구간 (T1>=85 / T2 78~84 / T3 70~77 / T4 62~69 / T5 54~61 / T6 <54)
# [2026-08 재설계, 신민용 확정: "F급도 이제 돈 받고, F→E→D...로 한 칸씩
# 밀어올리고 S는 100억 단위로 올리자"] AGENT_NONE("없음")이 새 무료 기본
# 상태를 맡고, F부터는 전부 이 표에 값이 있는 유료 등급이다 — old E의
# 계약금표를 F가, old D를 E가, old C를 D가, old B를 C가, old A를 B가,
# old S를 A가 그대로 물려받는다(사다리 한 칸씩 밀림). S는 old S의 5배로
# 새로 책정해 T1(최상위 OVR)이 10,000,000(천원단위)=100억원 수준이
# 되도록 잡았다.
_BASE_COST = {
    # agent_grade → {ovr_tier → 기본 계약금}
    "F": {"T1":8000,   "T2":6000,   "T3":4000,   "T4":2500,   "T5":1500,   "T6":800},
    "E": {"T1":30000,  "T2":20000,  "T3":12000,  "T4":7000,   "T5":3500,   "T6":1800},
    "D": {"T1":100000, "T2":70000,  "T3":40000,  "T4":20000,  "T5":9000,   "T6":4500},
    "C": {"T1":300000, "T2":200000, "T3":120000, "T4":60000,  "T5":25000,  "T6":10000},
    "B": {"T1":800000, "T2":500000, "T3":300000, "T4":150000, "T5":60000,  "T6":25000},
    "A": {"T1":2000000,"T2":1200000,"T3":700000, "T4":350000, "T5":150000, "T6":60000},
    "S": {"T1":10000000,"T2":6000000,"T3":3500000,"T4":1750000,"T5":750000,"T6":300000},
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
    if agent_grade == AGENT_NONE:
        return 0
    base = _BASE_COST.get(agent_grade, {}).get(ovr_tier, 1000)
    # ±30% 랜덤 (50천원 단위 반올림)
    varied = int(base * random.uniform(0.70, 1.30))
    return max(10, round(varied / 50) * 50)

def _gen_agent_offers(cur_grade: str, count=None) -> list:
    """현재 등급 기준 오퍼 등급 목록. [2026-08 재조정, 신민용 요청:
    "최대 3개였는데 이제 최소 5개로"] count 미지정 시 _MIN_AGENT_OFFERS
    (5개).
    [2026-08 재설계, 신민용 확정: "내가 등급을 사면 그 등급 이상만 뜨고
    그 아래는 안 뜨게"] 예전엔 diff==-1(현재보다 한 단계 낮은 등급)도
    후보에 포함했는데, 이제 diff>=0(현재 등급 이상)만 후보로 남긴다 —
    S급을 사면 더 위가 없으니 자연히 S만 뜬다. AGENT_NONE("없음")은
    AGENT_GRADES에 없는 사다리 맨 밑(가상 인덱스 -1)이라, 여기서만
    없어도 F~E 오퍼는 뜨도록 별도 처리한다."""
    cur_idx = -1 if cur_grade == AGENT_NONE else AGENT_GRADES.index(cur_grade)
    pool = []
    for g in AGENT_GRADES:
        gi = AGENT_GRADES.index(g)
        diff = gi - cur_idx
        if 0 <= diff <= 2:
            weight = 3 if diff == 1 else (2 if diff == 0 else 1)
            pool.extend([g] * weight)
    if not pool:
        pool = [cur_grade if cur_grade != AGENT_NONE else "F"]
    if count is None:
        count = _MIN_AGENT_OFFERS
    return [random.choice(pool) for _ in range(count)]


def _my_continent(p) -> str:
    return get_country_continent(p.get("nationality", "")) or ""


def _pick_agent_continent(my_continent: str) -> str:
    """[2026-08 신설] 대륙별 가중 랜덤 — 유럽은 절반 확률, 내 국적 대륙은
    2배 확률(둘 다 해당하면 곱연산으로 함께 적용)."""
    weights = []
    for cont in AGENT_CONTINENTS:
        w = AGENT_CONTINENT_WEIGHT.get(cont, _DEFAULT_CONTINENT_WEIGHT)
        if my_continent and cont == my_continent:
            w *= _MY_CONTINENT_WEIGHT_MULT
        weights.append(w)
    return random.choices(AGENT_CONTINENTS, weights)[0]


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


def _gen_one_offer(cur_grade: str, ovr_tier: str, my_cont: str) -> dict:
    """오퍼 풀 한 칸(하나의 에이전트 후보)을 새로 뽑는다. 초기 풀 생성과
    슬롯 교체(계약 체결 시/등급 상승으로 하위 등급 슬롯 정리 시) 양쪽에서
    재사용한다."""
    g = _gen_agent_offers(cur_grade, count=1)[0]
    cont = _pick_agent_continent(my_cont)
    cost, fee, label = _make_variant(g, ovr_tier)
    return {"grade": g, "continent": cont, "cost": cost, "fee": fee, "label": label}


def _load_or_generate_offer_pool(p) -> list:
    """[2026-08 신설, 신민용 요청: "6개월마다 뜨는 것도 입단을 나갔다
    들어올 때 똑같은 게 뜨는 것처럼 저장하며, 비시즌이 올 때마다 다른
    에이전트로 변경돼야 한다"] 시즌 단위(agent_offer_year==현재 연도)로
    후보 풀을 고정한다. 같은 시즌 안이면(계약만료→FA→재입단 포함) 저장된
    풀을 그대로 반환하고, 연도가 바뀌면(=비시즌 전환) 새로 생성해 저장.
    각 오퍼 항목: {"grade", "continent", "cost", "fee", "label"}."""
    st = get_state() or {}
    cur_year = st.get("current_year", 0)
    saved_year = p.get("agent_offer_year", 0)
    pool_json = p.get("agent_offer_pool_json", "")
    if pool_json and cur_year and saved_year == cur_year:
        try:
            data = json.loads(pool_json)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass

    cur_grade = p.get("agent_grade", AGENT_NONE)
    ovr_tier = _ovr_tier(p.get("ovr", 40))
    my_cont = _my_continent(p)
    data = [_gen_one_offer(cur_grade, ovr_tier, my_cont) for _ in range(_MIN_AGENT_OFFERS)]

    if cur_year:
        update_player(agent_offer_pool_json=json.dumps(data),
                      agent_offer_year=cur_year,
                      agent_offer_season=st.get("current_season", 0))
    return data


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
        self.cur_grade    = p.get("agent_grade", AGENT_NONE) if p else AGENT_NONE
        # [2026-08 신설, 신민용 리포트: "F 무명 에이전트여도 이게 아시아
        # 전문인지 뭔지 모른다"] update_player가 이미 agent_continent를
        # 저장해두므로(_select 참고, game_engine._team_fits_me의
        # AGENT_CONTINENT_BONUS에도 쓰임) 여기서 같이 읽어 표시만 하면 됨.
        self.cur_continent = (p.get("agent_continent", "") or "") if p else ""
        self.ovr_tier = _ovr_tier(p.get("ovr", 40) if p else 40)
        self.offer_pool = _load_or_generate_offer_pool(p) if p else []
        self.offers = [o["grade"] for o in self.offer_pool]
        # 오퍼별 (계약금, 수수료율, 라벨, 대륙) — 풀에 저장된 값 그대로 사용
        self.variants = {i: (o["cost"], o["fee"], o["label"]) for i, o in enumerate(self.offer_pool)}
        self.continents = {i: o["continent"] for i, o in enumerate(self.offer_pool)}
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        hdr = QLabel("👔 에이전트 오퍼" if self.lang=="ko" else "👔 Agent Offers")
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        root.addWidget(hdr)

        hint = QLabel("※ 오퍼는 다음 시즌 시작 시 새로 갱신됩니다")
        hint.setObjectName("hintLabel")
        root.addWidget(hint)

        cur_info = AGENT_INFO[self.cur_grade]
        cur_box = QFrame(); cur_box.setObjectName("agCard")
        cbl = QVBoxLayout(cur_box); cbl.setContentsMargins(12,8,12,8)
        _cont_txt = f"  ({self.cur_continent} 전문)" if self.cur_continent else ""
        cbl.addWidget(QLabel(f"현재: {cur_info[0]} [{self.cur_grade}] {cur_info[1]}{_cont_txt}"))
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
        continent = self.continents.get(idx, "")
        card = QFrame(); card.setObjectName("agCard")
        cl = QVBoxLayout(card); cl.setContentsMargins(12,10,12,10)

        h1 = QHBoxLayout()
        _cont_tag = f" · {continent} 전문" if continent else ""
        nl = QLabel(f"{icon}  [{grade}] {name}  · {label}{_cont_tag}")
        nl.setStyleSheet("font-size:13px;font-weight:bold;color:#e0e0e0;")
        h1.addWidget(nl); h1.addStretch()
        cl.addLayout(h1)
        cl.addWidget(QLabel(desc))
        if continent:
            cb = QLabel(f"🌍 {continent} 소속 팀 오퍼/입단에 보정 적용")
            cb.setStyleSheet("color:#ffaa55;font-size:11px;")
            cl.addWidget(cb)
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
            btn.clicked.connect(lambda _, i=idx, g=grade, c=cost, f=fee, ct=continent: self._select(g, c, f, ct, i))
        else:
            btn = QPushButton("✅ 선택" if self.lang=="ko" else "✅ Select")
            btn.setObjectName("selectBtn")
            btn.clicked.connect(lambda _, i=idx, g=grade, c=cost, f=fee, ct=continent: self._select(g, c, f, ct, i))
        h2.addWidget(btn); h2.addStretch()
        cl.addLayout(h2)
        return card

    def _select(self, grade, cost, fee, continent="", idx=None):
        p = get_player()
        if not p: return
        assets = p.get("total_assets", 0)
        if assets < cost:
            self._show_toast(
                f"💸 자산 부족  필요 {fmt_money(cost)}  현재 {fmt_money(assets)}", 1500)
            return
        # 확인 없이 바로 계약 (토스트로 충분)
        update_player(agent_grade=grade, agent_fee_rate=fee,
                      total_assets=assets - cost, agent_continent=continent or "")
        _cont_txt = f"  ({continent} 전문)" if continent else ""
        add_log(f"👔 에이전트 [{grade}] 계약  수수료 {fee*100:.0f}%  계약금 -{fmt_money(cost)}{_cont_txt}", "event")

        # [2026-08 신설, 신민용 확정: "계약한 자리엔 새 에이전트가 뜨고,
        # 등급을 올렸으면(F→E 등) 원래 있던 하위 등급 슬롯들도 이제 안
        # 뜨는 등급이니 다 같이 바뀌어야 한다 — 그 외(현재 등급 이상)
        # 슬롯은 6개월(다음 시즌) 갱신 때까지 그대로 유지"] 계약 슬롯 +
        # 새 등급보다 낮은 슬롯만 즉시 새 후보로 교체하고, 나머지는 손대지
        # 않은 채 그대로 DB에 다시 저장한다.
        new_grade_idx = AGENT_GRADES.index(grade) if grade in AGENT_GRADES else -1
        ovr_tier = self.ovr_tier
        my_cont = _my_continent(p)
        new_pool = list(self.offer_pool)
        for i, o in enumerate(new_pool):
            og = o.get("grade")
            og_idx = AGENT_GRADES.index(og) if og in AGENT_GRADES else -1
            if i == idx or og_idx < new_grade_idx:
                new_pool[i] = _gen_one_offer(grade, ovr_tier, my_cont)
        update_player(agent_offer_pool_json=json.dumps(new_pool))

        self.accept()