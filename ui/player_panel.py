"""
ui/player_panel.py  ─  좌측 선수 정보 패널
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QBrush, QColor, QPen

from game_engine import get_player, get_team_rank, fmt_money
from constants import ALL_STATS, STAT_KO, STAT_EN

PANEL_STYLE = """
QWidget { background-color: #1e1e1e; color: #cccccc; font-size: 12px; }
#pName  { color: #00ff66; font-size: 16px; font-weight: bold; }
#ovrBadge { background-color: #2a6a2a; color: white;
            padding: 2px 8px; border-radius: 4px; font-size: 12px; }
#injBadge { background-color: #8b0000; color: white;
            padding: 2px 6px; border-radius: 4px; font-size: 11px; }
#slumpBadge { background-color: #555500; color: #ffff00;
              padding: 2px 6px; border-radius: 4px; font-size: 11px; }
#rankLabel  { color: #00cc44; font-size: 13px; font-weight: bold; }
#secTitle   { color: #888888; font-size: 11px;
              border-bottom: 1px solid #2a2a2a; padding-bottom: 2px; }
#divider    { background-color: #2a2a2a; }
QProgressBar { background-color: #2a2a2a; border-radius: 3px; border: none; }
QProgressBar#stressBar::chunk { background-color: #cc4400; border-radius:3px; }
QProgressBar#happyBar::chunk  { background-color: #00aa44; border-radius:3px; }
"""


class PlayerPanel(QWidget):
    def __init__(self, main_win=None):
        super().__init__()
        self.main_win = main_win
        self.setStyleSheet(PANEL_STYLE)
        self._build()

    # ── 빌드 ─────────────────────────────────────

    def _build(self):
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(8,8,8,8)
        self.lay.setSpacing(4)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 이름 + OVR + 상태 뱃지
        name_row = QHBoxLayout()
        self.lbl_name  = QLabel("—"); self.lbl_name.setObjectName("pName")
        self.lbl_ovr   = QLabel("OVR 0"); self.lbl_ovr.setObjectName("ovrBadge")
        self.lbl_state = QLabel(""); self.lbl_state.setObjectName("injBadge")
        name_row.addWidget(self.lbl_name)
        name_row.addStretch()
        name_row.addWidget(self.lbl_ovr)
        name_row.addWidget(self.lbl_state)
        self.lay.addLayout(name_row)
        self._div()

        # 기본 정보 영역 (동적)
        self.info_frame = QWidget()
        self.info_lay   = QVBoxLayout(self.info_frame)
        self.info_lay.setSpacing(2); self.info_lay.setContentsMargins(0,0,0,0)
        self.lay.addWidget(self.info_frame)
        self._div()

        # 팀 순위
        self.lbl_rank = QLabel(""); self.lbl_rank.setObjectName("rankLabel")
        self.lay.addWidget(self.lbl_rank)
        self._div()

        # 스트레스 / 행복도
        self.lbl_stress = QLabel("스트레스  0")
        self.bar_stress = StatBar(); self.bar_stress.setFixedHeight(8)
        self.lbl_happy  = QLabel("행복도  0")
        self.bar_happy  = StatBar(); self.bar_happy.setFixedHeight(8)
        for w in [self.lbl_stress, self.bar_stress, self.lbl_happy, self.bar_happy]:
            self.lay.addWidget(w)
        self._div()

        # 이번 시즌
        lbl = QLabel("이번 시즌"); lbl.setObjectName("secTitle")
        self.lay.addWidget(lbl)
        self.season_frame = QWidget()
        self.season_lay   = QVBoxLayout(self.season_frame)
        self.season_lay.setSpacing(2); self.season_lay.setContentsMargins(0,0,0,0)
        self.lay.addWidget(self.season_frame)
        self._div()

        # 스탯
        self.stat_rows: dict[str, StatRow] = {}
        for section, stats in [
            ("신체", ["stamina","speed","jump"]),
            ("기술", ["shooting","passing","dribbling","tackling",
                      "heading","positioning","setpiece"]),
            ("정신", ["mental","confidence","leadership","concentration"]),
        ]:
            sl = QLabel(section); sl.setObjectName("secTitle")
            self.lay.addWidget(sl)
            for s in stats:
                row = StatRow(s)
                self.stat_rows[s] = row
                self.lay.addWidget(row)
            self._div()

    def _div(self):
        f = QFrame(); f.setObjectName("divider"); f.setFixedHeight(1)
        self.lay.addWidget(f)

    # ── 갱신 ─────────────────────────────────────

    def refresh(self):
        p = get_player()
        if not p:
            return
        lang = p.get("language","ko")
        sn   = STAT_KO if lang=="ko" else STAT_EN

        self.lbl_name.setText(p["name"])
        self.lbl_ovr.setText(f"OVR {p['ovr']}")

        if p.get("injured"):
            self.lbl_state.setText(f"🩹 부상({p['injury_weeks']}주)")
            self.lbl_state.setObjectName("injBadge"); self.lbl_state.show()
        elif p.get("slump"):
            self.lbl_state.setText("😰 슬럼프")
            self.lbl_state.setObjectName("slumpBadge"); self.lbl_state.show()
        else:
            self.lbl_state.hide()

        # 기본 정보 재구성
        _clear_layout(self.info_lay)

        team_name   = "없음"
        league_name = "—"
        tier        = 0
        if p.get("current_team_id"):
            from database import get_conn
            conn = get_conn()
            row = conn.execute(
                "SELECT t.name,l.name as lname,l.tier FROM teams t "
                "JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                (p["current_team_id"],)).fetchone()
            conn.close()
            if row:
                team_name   = row["name"]
                league_name = f"{row['lname']}({row['tier']}부)"
                tier        = row["tier"]

        fame_lbl = _fame(p.get("fame",0), lang)
        salary   = p.get("salary",0)
        monthly  = salary // 12

        # 국적 표시 (복수국적: 본 국적 맨 앞 + ★, 나머지 병기)
        _nats = []
        for _nk, _fk in (("nationality","flag"),("nationality2","flag2"),("nationality3","flag3")):
            _n = p.get(_nk, "") or ""
            if _n:
                _nats.append((_n, p.get(_fk, "") or ""))
        _committed = p.get("intl_committed", "") or ""
        if _committed and any(n == _committed for n, f in _nats):
            _nats.sort(key=lambda nf: 0 if nf[0] == _committed else 1)
        if _nats:
            _parts = []
            for _n, _f in _nats:
                _mark = "★" if (_committed and _n == _committed) else ""
                _parts.append(f"{_f} {_n}{_mark}")
            _nat_str = "  /  ".join(_parts)
        else:
            _nat_str = f"{p.get('flag','')} {p.get('nationality','')}"

        rows = [
            ("나이",   f"{p['age']}세 ({p['current_year']}년)"),
            ("국적",   _nat_str),
            ("소속",   team_name),
            ("리그",   league_name),
            ("포지션", p["position"]),
            ("성격",   p["personality"]),
            ("특징",   p.get("physical_trait", "무난함")),
            ("신체",   f"{p['height']}cm / {p['weight']}kg"),
            ("명성",   f"{p.get('fame',0)} [{fame_lbl}]"),
            ("인기도", str(p.get("popularity",0))),
            ("팬수",   f"{p.get('fans',0):,}명"),
            ("에이전트", f"[{p.get('agent_grade','F')}등급]"),
            ("연봉",   f"연 {fmt_money(salary)}  [월 {fmt_money(monthly)}]"),
            ("총자산", fmt_money(p.get("total_assets",0))),
            ("감독관계", str(p.get("manager_relation",50))),
        ]
        for k, v in rows:
            self.info_lay.addWidget(_info_row(k, v))

        # 순위
        if p.get("current_team_id"):
            self.lbl_rank.setText(get_team_rank(p["current_team_id"]))
        else:
            self.lbl_rank.setText("팀 없음" if lang=="ko" else "No Team")

        # 스트레스/행복도
        self.lbl_stress.setText(f"스트레스  {p['stress']}")
        self.lbl_happy.setText(f"행복도  {p['happiness']}")
        self.bar_stress.set_values(p['stress'], 100)
        self.bar_stress._cur_color = QColor("#cc4400")
        self.bar_happy.set_values(p['happiness'], 100)
        self.bar_happy._cur_color = QColor("#00aa44")

        # 이번 시즌
        _clear_layout(self.season_lay)
        sm = p.get("season_matches",0)
        sg = p.get("season_goals",0)
        sa = p.get("season_assists",0)
        ss = p.get("season_saves",0)
        sga = p.get("season_goals_against",0)
        rc = p.get("season_rating_cnt",0)
        rs = p.get("season_rating_sum",0.0)
        avg_r = round(rs/rc,1) if rc else 0.0

        if p["position"] == "GK":
            total_shots = ss + sga  # 선방 + 실점 = 총 슈팅
            save_rate = round(ss / total_shots * 100, 1) if total_shots > 0 else 0.0
            save_str = f"{ss}회 ({ss}/{total_shots})  {save_rate}%"
            s_rows = [
                ("출전",     f"{sm}경기"),
                ("선방",     save_str),
                ("실점",     f"{sga}골"),
                ("평균평점", str(avg_r)),
            ]
        elif p.get("position") in {"CB","CDM"}:
            # 수비수: 골 대신 평점 강조, 어시 유지
            s_rows = [("출전",f"{sm}경기"),("어시",f"{sa}A"),("평균평점",str(avg_r))]
        else:
            s_rows = [("출전",f"{sm}경기"),("골",f"{sg}골"),("어시",f"{sa}A"),("평균평점",str(avg_r))]
        for k,v in s_rows:
            self.season_lay.addWidget(_info_row(k, v))

        # 스탯 바
        for s, row in self.stat_rows.items():
            cur = p.get(s,40)
            mx  = p.get(f"{s}_max",80)
            row.update(sn.get(s,s), cur, mx)


def _info_row(key, val):
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0,0,0,0); h.setSpacing(4)
    kl = QLabel(key); kl.setFixedWidth(55); kl.setStyleSheet("color:#888888;font-size:11px;")
    vl = QLabel(val); vl.setStyleSheet("color:#cccccc;font-size:12px;"); vl.setWordWrap(True)
    h.addWidget(kl); h.addWidget(vl); h.addStretch()
    return w


def _clear_layout(lay):
    while lay.count():
        item = lay.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def _fame(fame, lang):
    if fame >= 90: return "레전드"   if lang=="ko" else "Legend"
    if fame >= 70: return "월드클래스" if lang=="ko" else "World Class"
    if fame >= 50: return "유명"     if lang=="ko" else "Famous"
    if fame >= 30: return "알려짐"   if lang=="ko" else "Known"
    if fame >= 10: return "신인"     if lang=="ko" else "Rookie"
    return "무명" if lang=="ko" else "Unknown"


class StatRow(QWidget):
    def __init__(self, stat_key):
        super().__init__()
        lay = QHBoxLayout(self); lay.setContentsMargins(0,1,0,1); lay.setSpacing(4)

        self.lbl_name = QLabel(stat_key); self.lbl_name.setFixedWidth(55)
        self.lbl_name.setStyleSheet("color:#888888;font-size:11px;")

        self.bar_widget = StatBar()
        self.bar_widget.setFixedHeight(10)

        self.lbl_val = QLabel("0/0"); self.lbl_val.setFixedWidth(58)
        self.lbl_val.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lbl_val.setStyleSheet("color:#aaaaaa;font-size:11px;")

        lay.addWidget(self.lbl_name); lay.addWidget(self.bar_widget); lay.addWidget(self.lbl_val)

    def update(self, name, cur, mx):
        self.lbl_name.setText(name)
        self.bar_widget.set_values(cur, mx)
        self.lbl_val.setText(f"{cur}/{mx}")


class StatBar(QWidget):
    """0~100 기준 바. 노란색=현재스탯, 회색반투명=한계스탯 위치 표시"""
    def __init__(self):
        super().__init__()
        self._cur = 0
        self._mx  = 80
        self._cur_color = None  # None이면 비율로 자동 결정
        self.setMinimumWidth(60)

    def set_values(self, cur, mx):
        self._cur = max(0, min(100, cur))
        self._mx  = max(0, min(100, mx))
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QBrush, QColor, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = 3  # border-radius

        # 배경 (0~100)
        p.setBrush(QBrush(QColor("#2a2a2a")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, w, h, r, r)

        # 한계스탯 영역 (반투명 회색)
        mx_w = int(w * self._mx / 100)
        if mx_w > 0:
            p.setBrush(QBrush(QColor(120, 120, 120, 60)))
            p.drawRoundedRect(0, 0, mx_w, h, r, r)

        # 현재 스탯 바 (색상)
        cur_w = int(w * self._cur / 100)
        if cur_w > 0:
            if self._cur_color:
                color = self._cur_color
            else:
                ratio = self._cur / self._mx if self._mx > 0 else 0
                if ratio >= 0.85:
                    color = QColor("#00cc44")
                elif ratio >= 0.60:
                    color = QColor("#ccaa00")
                else:
                    color = QColor("#cc4400")
            p.setBrush(QBrush(color))
            p.drawRoundedRect(0, 0, cur_w, h, r, r)

        # 한계스탯 경계선 (흰색 세로줄)
        mx_x = int(w * self._mx / 100)
        if 0 < mx_x < w:
            p.setPen(QPen(QColor(200, 200, 200, 140), 1))
            p.drawLine(mx_x, 0, mx_x, h)

        p.end()