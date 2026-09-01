"""
ui/match_detail_dialog.py  ─  로그에서 경기 헤더 클릭 시 뜨는 상세 창

game_engine.get_match_detail(id) 가 돌려주는 dict 를 받아
전/후반 타임라인 · 평점 · 세부 지표 · 총평을 보기 좋게 펼쳐 보여준다.

[구조] "▶ 시뮬 보기" / "📊 경기 통계"는 예전엔 각각 새 창(QDialog.show())을
열었는데, 이제는 새 창을 띄우지 않고 이 다이얼로그 자체가 오른쪽으로
펼쳐지면서(가로 폭이 늘어나면서) 그 안에 인라인으로 들어간다. 시뮬 뷰어
(MatchSimViewer)는 QDialog로 만들어진 걸 windowFlags만 Widget으로 바꿔서
그대로 재사용한다 — 로직을 중복 구현하지 않기 위함.
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QScrollArea, QWidget, QFrame, QPushButton,
                             QSizePolicy)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont


def _fmt_min(m):
    """정렬용 분(정수) → 표시 문자열. 전반 추가시간 146~155=45+1~10, 후반 91~100=90+1~10."""
    try:
        m = int(m)
    except (ValueError, TypeError):
        return str(m)
    if 146 <= m <= 155:
        return f"45+{m-145}"
    if 91 <= m <= 100:
        return f"90+{m-90}"
    return str(m)


def _min_sortkey(m):
    """실제 경기 시간 정렬 키. 전반 추가시간→45.x, 후반 추가시간→90.x."""
    try:
        m = int(m)
    except (ValueError, TypeError):
        return 0.0
    if 146 <= m <= 155:
        return 45 + (m - 145) / 100.0
    if 91 <= m <= 100:
        return 90 + (m - 90) / 100.0
    return float(m)


def _is_first_half(m):
    """전반 여부. 1~45 + 전반 추가시간(146~155)."""
    return m <= 45 or (146 <= m <= 155)


def _row(label, value, vcolor="#ffffff"):
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    l = QLabel(label); l.setStyleSheet("color:#888;font-size:12px;")
    v = QLabel(str(value)); v.setStyleSheet(f"color:{vcolor};font-size:12px;font-weight:bold;")
    v.setAlignment(Qt.AlignmentFlag.AlignRight)
    h.addWidget(l); h.addStretch(); h.addWidget(v)
    return w


# ─────────────────────────────────────────
# 경기 통계 패널 (점유율/슈팅/코너/파울/패스성공률)
# game_engine._derive_match_stats()가 만든 payload["team_stats"]를 그린다.
# 그 값들은 랜덤이 아니라 최종 스코어 + 내 세부지표를 기준으로 역산된
# 결정론적 값이라, 여기서는 그냥 표시만 한다.
# ─────────────────────────────────────────
_HOME_COLOR = "#4488ff"
_AWAY_COLOR = "#ff5566"

# [2026-09 조정, 신민용 리포트: "포메이션에 따라 원 크기가 들쭉날쭉하고
# 이름도 잘린다 — 라인업 평점 칸 가로를 늘려달라"] 기존 440px는 한 줄에
# 5명(5백의 DEF행 등)이 들어가는 대형에서 원이 눌리는 원인이었다 —
# _open_lineup_panel/_resize_for_content가 전부 이 값 하나만 보고 폭을
# 맞추므로, 여기서만 바꾸면 다이얼로그 전체 너비 계산까지 한 번에 맞다.
_LINEUP_PANEL_WIDTH = 620


class _PossBar(QWidget):
    """점유율 좌우 비교 막대."""
    def __init__(self, home_pct, away_pct):
        super().__init__()
        self.home_pct = home_pct
        self.away_pct = away_pct
        self.setFixedHeight(20)

    def paintEvent(self, _ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        hw = round(w * self.home_pct / 100)
        p.fillRect(0, 0, hw, h, QColor(_HOME_COLOR))
        p.fillRect(hw, 0, w - hw, h, QColor(_AWAY_COLOR))
        p.end()


def _stat_compare_row(label, home_val, away_val):
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 3, 0, 3)
    hv = QLabel(str(home_val))
    hv.setStyleSheet(f"color:{_HOME_COLOR};font-size:13px;font-weight:bold;")
    hv.setFixedWidth(46)
    hv.setAlignment(Qt.AlignmentFlag.AlignLeft)
    lbl = QLabel(label)
    lbl.setStyleSheet("color:#999;font-size:11px;")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    av = QLabel(str(away_val))
    av.setStyleSheet(f"color:{_AWAY_COLOR};font-size:13px;font-weight:bold;")
    av.setFixedWidth(46)
    av.setAlignment(Qt.AlignmentFlag.AlignRight)
    h.addWidget(hv); h.addWidget(lbl, 1); h.addWidget(av)
    return w


class MatchStatsPanel(QWidget):
    """경기 통계 인라인 패널. team_stats가 없는(예전 저장분) 경기는 안내
    문구만 보여준다 — 억지로 랜덤 값을 만들어 채우지 않는다."""

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:#161616;")
        payload = data.get("payload", {}) or {}
        team_stats = payload.get("team_stats")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        hdr = QLabel("📊 경기 통계")
        hdr.setStyleSheet("color:#fff;font-size:14px;font-weight:bold;")
        root.addWidget(hdr)

        names = QWidget(); nh = QHBoxLayout(names); nh.setContentsMargins(0, 0, 0, 0)
        hn = QLabel(data.get("home_name", "홈팀"))
        hn.setStyleSheet(f"color:{_HOME_COLOR};font-size:12px;font-weight:bold;")
        an = QLabel(data.get("away_name", "원정팀"))
        an.setStyleSheet(f"color:{_AWAY_COLOR};font-size:12px;font-weight:bold;")
        an.setAlignment(Qt.AlignmentFlag.AlignRight)
        nh.addWidget(hn); nh.addStretch(); nh.addWidget(an)
        root.addWidget(names)

        if not team_stats:
            note = QLabel("이 경기는 통계 데이터가 없습니다\n(업데이트 이전 기록입니다).")
            note.setStyleSheet("color:#555;font-size:12px;")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            note.setWordWrap(True)
            root.addStretch()
            root.addWidget(note)
            root.addStretch()
            return

        h_st, a_st = team_stats["home"], team_stats["away"]

        poss_lbl = QLabel(f"점유율   {h_st['poss']}%  -  {a_st['poss']}%")
        poss_lbl.setStyleSheet("color:#ccc;font-size:11px;")
        poss_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(poss_lbl)
        root.addWidget(_PossBar(h_st["poss"], a_st["poss"]))

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#2a2a2a;")
        root.addWidget(line)

        root.addWidget(_stat_compare_row("슈팅", h_st["shots"], a_st["shots"]))
        root.addWidget(_stat_compare_row("유효 슈팅", h_st["shots_on"], a_st["shots_on"]))
        root.addWidget(_stat_compare_row("코너킥", h_st["corners"], a_st["corners"]))
        root.addWidget(_stat_compare_row("파울", h_st["fouls"], a_st["fouls"]))
        root.addWidget(_stat_compare_row(
            "패스 성공률", f"{h_st['pass_acc']*100:.0f}%", f"{a_st['pass_acc']*100:.0f}%"))

        root.addStretch()


def _rating_color(rating):
    """평점 배지 색 — FotMob류 매치센터와 같은 관례(초록=잘함, 노랑/주황=
    평범, 빨강=부진)를 그대로 따른다."""
    if rating >= 7.5:
        return "#2e9e4f"
    if rating >= 6.6:
        return "#4a8f3c"
    if rating >= 6.0:
        return "#c99a2e"
    return "#c0392b"


def _lineup_player_row(entry, accent):
    """라인업 평점 패널의 선수 한 줄 — 포지션/이름(+OVR) | 평점 배지.
    entry가 None이면(그 슬롯에 실제 선수가 안 잡힌 경우) 빈 자리로 표시."""
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(2, 2, 2, 2); h.setSpacing(6)
    if entry is None:
        pos_lbl = QLabel("-")
        pos_lbl.setStyleSheet("color:#444;font-size:10px;")
        pos_lbl.setFixedWidth(28)
        name_lbl = QLabel("(공석)")
        name_lbl.setStyleSheet("color:#444;font-size:11px;")
        h.addWidget(pos_lbl); h.addWidget(name_lbl, 1)
        return w

    is_me = bool(entry.get("is_me"))
    pos_lbl = QLabel(entry.get("position", ""))
    pos_lbl.setStyleSheet(f"color:{accent};font-size:10px;font-weight:bold;")
    pos_lbl.setFixedWidth(28)

    name_txt = entry.get("name", "") or "-"
    extra = []
    g, a = entry.get("goals", 0), entry.get("assists", 0)
    if entry.get("is_gk"):
        if entry.get("saves", 0):
            extra.append(f"선방{entry['saves']}")
    else:
        if g:
            extra.append(f"⚽{g}")
        if a:
            extra.append(f"🅰{a}")
    extra_txt = ("  " + " ".join(extra)) if extra else ""
    name_lbl = QLabel(f"{'⭐ ' if is_me else ''}{name_txt} ({entry.get('ovr', 0)}){extra_txt}")
    name_lbl.setStyleSheet(
        f"color:{'#ffe27a' if is_me else '#ddd'};font-size:11px;"
        f"{'font-weight:bold;' if is_me else ''}")
    name_lbl.setWordWrap(False)

    rating_lbl = QLabel(f"{entry.get('rating', 0):.1f}")
    rating_lbl.setFixedWidth(34)
    rating_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    rating_lbl.setStyleSheet(
        f"background:{_rating_color(entry.get('rating', 0))};color:#fff;"
        "font-size:11px;font-weight:bold;border-radius:4px;padding:2px 0;")

    h.addWidget(pos_lbl)
    h.addWidget(name_lbl, 1)
    h.addWidget(rating_lbl)
    return w


# ─────────────────────────────────────────
# 포메이션 시각화 (위/아래 배치)
# [2026-08 신설, 신민용 요청: "라인업 평점이 지금은 좌우로만 나뉘어
# 있는데, 실제 포메이션처럼 위/아래로 배치해서 필드 위에서 보고 싶다 —
# 선수는 원으로, 원 안엔 평점, 원 아래엔 이름. 기존 좌우 목록은 그
# 아래로 내려서 그대로 두면 된다"] tactical_engine._build_player_ratings가
# 22명 각각에 붙여준 "position"은 항상 ui/formation_widget._FormationCanvas가
# 쓰는 것과 같은 슬롯 라벨 체계이므로, 그 라벨을 그대로 재사용해 행(GK→
# DEF→MID→MID2→ATK)으로 묶고 각 행 안에서 좌→우로 배열한다 — 로직 자체는
# formation_widget._row_key/_row_priority/_pos_x_order와 동일한 원리지만,
# 이 파일은 그 모듈(스쿼드/이적 관련 무거운 DB 조회가 잔뜩 딸려 있음)을
# 통째로 import하지 않기 위해 필요한 부분만 이 파일 안에 작게 복제해둔다.
# ─────────────────────────────────────────
_FALLBACK_MATCH_SLOTS = ["GK", "CB", "CB", "LB", "RB", "LM", "CM", "CM", "RM", "ST", "ST"]

_FP_POS_X_ORDER = {
    "LW": 0, "CF": 1, "ST": 2, "SS": 2, "RW": 4,
    "LM": 0, "CAM": 2, "RM": 4,
    "CM": 2, "CDM": 2, "DM": 2,
    "LWB": 0, "LB": 1, "CB": 2, "RB": 3, "RWB": 4, "SW": 2,
    "GK": 2,
}


def _fp_pos_x_order(pos):
    return _FP_POS_X_ORDER.get(pos, 2)


def _fp_row_key(pos):
    if pos == "GK": return "GK"
    if pos in ("CB", "LB", "RB", "LWB", "RWB", "SW"): return "DEF"
    if pos in ("CDM", "CM", "DM"): return "MID"
    if pos in ("CAM", "LM", "RM"): return "MID2"
    return "ATK"


def _fp_row_priority(k):
    # 하프라인(0)에 가까울수록 공격진 — 위/아래 각 절반 안에서 하프라인
    # 쪽에 ATK가 오도록 하는 기준. 아래팀은 그대로, 위팀은 paintEvent에서
    # 이 순서를 뒤집어(mirrored) 하프라인 쪽에 ATK가 오게 만든다.
    return {"ATK": 0, "MID2": 1, "MID": 2, "DEF": 3, "GK": 4}.get(k, 2)


class _MatchFormationPitch(QWidget):
    """양 팀 11명을 하프라인 기준 위(top)/아래(bottom)에 실제 포진처럼
    그리는 캔버스. 선수는 원으로, 원 안엔 평점, 원 아래엔 이름을 표시한다.
    위쪽 팀은 하프라인 쪽에 공격진이 오도록 상하 반전해서 배치한다(실제
    중계 그래픽과 같은 관례). 폭·높이는 이 위젯이 실제로 차지한 크기에
    맞춰 매번 다시 계산한다 — 고정 좌표가 없어서 패널 폭이 얼마든 그
    안에 꽉 차게 그려진다."""

    # [2026-09] 캔버스 위/아래 가장자리에 확보해두는 여백 — 가장 바깥
    # 줄(보통 GK)의 원과 이름표가 위젯 경계에 잘리지 않게 하기 위함
    # (신민용 리포트: "아래 키퍼 이름까지 보여야 하니 경기장 가장자리를
    # 좀 늘리고").
    _EDGE_PAD = 26

    def __init__(self, top_list, top_name, top_accent,
                 bottom_list, bottom_name, bottom_accent, parent=None):
        super().__init__(parent)
        self._top = top_list or []
        self._bottom = bottom_list or []
        self._top_name = top_name or ""
        self._bottom_name = bottom_name or ""
        self._top_accent = top_accent
        self._bottom_accent = bottom_accent
        # [2026-09 재조정, 신민용 리포트: "전체 창을 키울 게 아니라 라인업
        # 평점 안에서 포메이션 영역 자체를 늘리면 되는거다"] 다이얼로그
        # 전체 높이는 다시 원래대로 되돌리고(_base_height), 이 캔버스만
        # 스크롤 가능한 패널 안에서 충분히 크게 그려지도록 최소 높이를
        # 넉넉히 잡는다 — 아래 라인업 목록은 그만큼 스크롤해서 보면 된다.
        self.setMinimumHeight(680)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def _group_rows(self, entries):
        """entries(11개, 빈 슬롯은 None) → (rows, sorted_rows, max_row_cnt).
        rows: {행키: [(idx,label,entry), ...]}, sorted_rows: 하프라인에
        가까운 행(ATK)부터 먼 행(GK) 순."""
        rows = {}
        row_order = []
        for idx, entry in enumerate(entries):
            label = entry.get("position") if entry else None
            if not label:
                label = _FALLBACK_MATCH_SLOTS[idx] if idx < len(_FALLBACK_MATCH_SLOTS) else "CM"
            k = _fp_row_key(label)
            if k not in rows:
                rows[k] = []; row_order.append(k)
            rows[k].append((idx, label, entry))
        sorted_rows = sorted(row_order, key=_fp_row_priority)
        max_row_cnt = max((len(v) for v in rows.values()), default=1)
        return rows, sorted_rows, max_row_cnt

    def _positions_for(self, rows, sorted_rows, w, row_h, halfway_y, direction):
        """halfway_y를 기준으로 direction(+1=아래팀, -1=위팀)쪽으로 행을
        하나씩 쌓는다. row_h가 양 팀 공통값이라 두 팀 모두 같은 '한 줄
        높이'를 쓰게 되고, 그래서 나중에 계산하는 원 지름도 자연스럽게
        같아진다(포메이션 줄 수가 달라도 원 크기가 달라지지 않음)."""
        positions = []
        for ri, rk in enumerate(sorted_rows):
            items = sorted(rows[rk], key=lambda t: _fp_pos_x_order(t[1]))
            cnt = len(items)
            ry = int(halfway_y + direction * (ri + 0.5) * row_h)
            for ci, (idx, label, entry) in enumerate(items):
                rx = int((ci + 1) * w / (cnt + 1))
                positions.append((rx, ry, entry))
        return positions

    def paintEvent(self, _ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(0, 0, w, h, QBrush(QColor("#123a1a")))
        painter.setPen(QPen(QColor("#2f6b34"), 1))
        painter.drawRect(6, 6, w - 12, h - 12)
        halfway_y = h / 2.0
        painter.drawLine(6, int(halfway_y), w - 6, int(halfway_y))
        cc_d = max(30, min(70, min(w, h) // 5))
        painter.drawEllipse(int(w / 2 - cc_d / 2), int(halfway_y - cc_d / 2), cc_d, cc_d)

        # [2026-09 신설] 양 팀의 포메이션 "줄 수"가 다르면(예: 5줄짜리
        # 3-4-1-2 vs 4줄짜리 4-3-3) 예전엔 같은 절반 높이를 각자 자기
        # 줄 수로 나눠서 한 줄 높이가 서로 달라졌고, 그 결과 원 지름도
        # 팀마다 달라 보였다(신민용 리포트: "위 선수 원이 아래보다
        # 1.5배 작다 — 현실엔 그런 거 없잖아"). 이제 두 팀 중 줄이 더
        # 많은 쪽 기준으로 "공통 한 줄 높이"를 하나 정해서 양쪽 다
        # 그대로 쓴다 — 줄이 적은 팀은 그만큼 하프라인 쪽/가장자리 쪽에
        # 살짝 여유 공간이 남을 뿐, 원 크기 자체는 항상 두 팀이 동일.
        top_rows, top_sorted, top_maxcnt = self._group_rows(self._top)
        bot_rows, bot_sorted, bot_maxcnt = self._group_rows(self._bottom)
        rows_top = len(top_sorted) or 1
        rows_bot = len(bot_sorted) or 1
        max_rows = max(rows_top, rows_bot, 1)

        usable_h = max(1.0, h - 2 * self._EDGE_PAD)
        half_h = usable_h / 2.0
        row_h = half_h / max_rows

        top_positions = self._positions_for(top_rows, top_sorted, w, row_h, halfway_y, direction=-1)
        bot_positions = self._positions_for(bot_rows, bot_sorted, w, row_h, halfway_y, direction=+1)

        max_row_cnt = max(top_maxcnt, bot_maxcnt, 1)
        col_w = w / (max_row_cnt + 1)
        d = int(max(30, min(84, row_h * 0.62, col_w * 0.68)))

        placements = [(x, y, entry, self._top_accent) for x, y, entry in top_positions]
        placements += [(x, y, entry, self._bottom_accent) for x, y, entry in bot_positions]

        for x, y, entry, accent in placements:
            r = d // 2
            is_me = bool(entry and entry.get("is_me"))
            painter.setBrush(QBrush(QColor("#ffcc00" if is_me else accent)))
            painter.setPen(QPen(QColor("#000"), 2 if is_me else 1))
            painter.drawEllipse(x - r, y - r, d, d)

            rating_txt = f"{entry.get('rating', 0):.1f}" if entry else "-"
            f = QFont(); f.setPointSize(max(7, min(15, d // 4))); f.setBold(True)
            painter.setFont(f)
            painter.setPen(QPen(QColor("#000" if is_me else "#fff")))
            painter.drawText(x - r, y - r, d, d, Qt.AlignmentFlag.AlignCenter, rating_txt)

            name_txt = "(공석)" if not entry else (("⭐" if is_me else "") + (entry.get("name") or "-"))
            f2 = QFont(); f2.setPointSize(max(7, min(11, d // 5))); f2.setBold(is_me)
            painter.setFont(f2)
            painter.setPen(QPen(QColor("#666" if not entry else ("#ffe27a" if is_me else "#eee"))))
            name_w = max(56, d + 28)
            painter.drawText(x - name_w // 2, y + r + 2, name_w, 16,
                             Qt.AlignmentFlag.AlignCenter, name_txt[:8])

        f3 = QFont(); f3.setPointSize(10); f3.setBold(True)
        painter.setFont(f3)
        painter.setPen(QPen(QColor(self._top_accent)))
        painter.drawText(10, 4, w - 20, 18,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._top_name)
        painter.setPen(QPen(QColor(self._bottom_accent)))
        painter.drawText(10, h - 20, w - 20, 18,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._bottom_name)
        painter.end()


class LineupRatingsPanel(QWidget):
    """[2026-08 신설, 신민용 요청: "경기 상세에서 22명 선수 평점을
    FotMob처럼 보여달라"] game_engine._save_match_detail이 저장한
    payload["player_ratings"](tactical_engine가 만든 22명 개인 기록+평점)를
    보여준다. 지금은 리그 경기(전술 엔진 사용)만 실제 값이 있고, 그 외
    대회는 비어 있어 안내 문구만 뜬다 — 억지로 채우지 않는다
    (MatchStatsPanel의 team_stats 없음 처리와 같은 원칙).

    [2026-08 개편, 신민용 요청: "포메이션이 지금은 좌우로만 나뉘어
    있는데, 위/아래로 실제 필드처럼 배치해서 전체 포메이션을 확인하고
    싶다 — 선수는 원으로, 원 안엔 평점, 원 아래엔 이름. 기존 좌우 목록은
    그 아래로 내려서 그대로 두면 된다"] 위쪽엔 _MatchFormationPitch로
    양팀 포진을 시각적으로 그리고, 그 아래에 기존 좌우 두 열 목록을
    그대로 둔다(구조·내용은 변경 없음, 위치만 아래로 이동). 전체를
    QScrollArea로 감싸 — 패널 폭은 고정(440px)이라도 포메이션 캔버스는
    항상 그 폭에 맞춰 그려지고(_MatchFormationPitch가 자기 크기를 그대로
    씀), 세로로 넘치는 만큼만 스크롤된다."""

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:#161616;")
        payload = data.get("payload", {}) or {}
        pr = payload.get("player_ratings") or {}
        home_list = pr.get("home") or []
        away_list = pr.get("away") or []

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        hdr = QLabel("⭐ 라인업 평점")
        hdr.setStyleSheet("color:#fff;font-size:14px;font-weight:bold;")
        root.addWidget(hdr)

        if not home_list and not away_list:
            note = QLabel("이 경기는 선수별 평점 데이터가 없습니다\n"
                          "(리그 경기가 아니거나, 업데이트 이전 기록입니다).")
            note.setStyleSheet("color:#555;font-size:12px;")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            note.setWordWrap(True)
            root.addStretch(); root.addWidget(note); root.addStretch()
            return

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                             "QScrollBar:vertical{background:#1a1a1a;width:6px;}"
                             "QScrollBar::handle:vertical{background:#3a3a3a;border-radius:3px;}")
        inner = QWidget(); inner.setStyleSheet("background:transparent;")
        iv = QVBoxLayout(inner); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(10)

        # ── 위: 포메이션 시각화 (원=선수, 원 안=평점, 원 아래=이름) ──
        pitch = _MatchFormationPitch(
            top_list=away_list, top_name=data.get("away_name", ""), top_accent=_AWAY_COLOR,
            bottom_list=home_list, bottom_name=data.get("home_name", ""), bottom_accent=_HOME_COLOR)
        iv.addWidget(pitch)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#2a2a2a;")
        iv.addWidget(line)

        # ── 아래: 기존 좌우 목록 (그대로) ──
        cols = QHBoxLayout(); cols.setSpacing(12)
        for name_key, side_list, accent in (
                ("home_name", home_list, _HOME_COLOR),
                ("away_name", away_list, _AWAY_COLOR)):
            col = QWidget()
            cv = QVBoxLayout(col); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(3)
            side_hdr = QLabel(data.get(name_key, ""))
            side_hdr.setStyleSheet(f"color:{accent};font-size:12px;font-weight:bold;")
            side_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cv.addWidget(side_hdr)
            for entry in side_list:
                cv.addWidget(_lineup_player_row(entry, accent))
            cv.addStretch()
            cols.addWidget(col, 1)
        iv.addLayout(cols)
        iv.addStretch()

        scroll.setWidget(inner)
        root.addWidget(scroll, 1)


class MatchDetailDialog(QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("경기 상세")
        self.setStyleSheet("QDialog{background:#161616;}")
        self._data = data
        self._right_widget = None      # 현재 오른쪽(시뮬 칸)에 펼쳐진 위젯(시뮬 전용)
        self._left_stats_widget = None  # [2026-08] 이름은 _left_*지만 실제로는 가운데 패널 "오른쪽"에 뜨는 통계 전용 위젯
        self._lineup_widget = None      # 맨 오른쪽 끝 — 라인업 평점 전용

        # ── 전체 레이아웃: [가운데=기존 상세 내용] [통계] [시뮬] [라인업 평점] ──
        #   [2026-08 변경, 신민용 요청: "경기 통계도 우측에 뜨게"] 예전엔
        #   통계만 가운데 칸의 왼쪽에 반대 방향으로 펼쳐졌는데, 이제 통계·
        #   시뮬·라인업 평점 셋 다 가운데 칸 오른쪽에 나란히 독립 패널로
        #   펼쳐진다(각자 고정폭, 서로 안 건드림).
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        left_widget = QWidget()
        left_widget.setFixedWidth(420)
        root = QVBoxLayout(left_widget)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)
        outer.addWidget(left_widget)

        # [2026-08 변경, 신민용 요청: "경기 통계도 우측에 뜨게, 얘만
        # 좌측이라 어색하다"] 예전엔 이 컨테이너가 가운데(420px) "왼쪽"에
        # 붙어서 시뮬/라인업 평점(둘 다 오른쪽)과 반대 방향으로 펼쳐졌다.
        # 변수명(_left_container 등)은 그대로 두되(다른 메서드들이 이미
        # 이 이름을 참조 중이라 이름 자체를 바꾸면 변경 범위만 커짐),
        # outer 레이아웃에 넣는 "위치"만 가운데 패널 다음(오른쪽)으로
        # 옮겨서 시뮬/라인업 평점과 같은 방향에 나란히 펼쳐지게 한다.
        self._left_container = QWidget()
        self._left_container.setStyleSheet("background:#101010;border-left:1px solid #2a2a2a;")
        self._left_layout = QVBoxLayout(self._left_container)
        self._left_layout.setContentsMargins(0, 0, 0, 0)
        self._left_container.setFixedWidth(0)  # 처음엔 접혀 있음
        outer.addWidget(self._left_container)

        self._right_container = QWidget()
        self._right_container.setStyleSheet("background:#101010;border-left:1px solid #2a2a2a;")
        self._right_layout = QVBoxLayout(self._right_container)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_container.setFixedWidth(0)  # 처음엔 접혀 있음
        outer.addWidget(self._right_container)

        # [2026-08 신설] 라인업 평점 전용 패널 — 통계/시뮬과 같은 패턴으로
        # 맨 끝에 독립된 칸을 하나 더 둔다. 시뮬 보기와 동시에 열어도
        # 서로 안 건드리게 완전히 분리.
        self._lineup_container = QWidget()
        self._lineup_container.setStyleSheet("background:#101010;border-left:1px solid #2a2a2a;")
        self._lineup_layout = QVBoxLayout(self._lineup_container)
        self._lineup_layout.setContentsMargins(0, 0, 0, 0)
        self._lineup_container.setFixedWidth(0)  # 처음엔 접혀 있음
        outer.addWidget(self._lineup_container)

        # [2026-09 재조정, 신민용 리포트: "전체 창을 굳이 안 키워도 되는데
        # — 라인업 평점 안에서 포메이션 영역만 늘리면 되는거다"] 처음엔
        # 다이얼로그 전체 높이를 키웠었는데, 그러면 원 크기 불일치 같은
        # 진짜 문제는 그대로인 채 창만 커져서 의미가 없었다. 다이얼로그
        # 기본 높이는 원래 값으로 되돌리고, 대신 라인업 평점 패널 안의
        # _MatchFormationPitch 자체의 최소 높이를 넉넉히 키운다 — 그
        # 패널은 이미 QScrollArea라 포메이션이 커진 만큼 아래 기존
        # 좌우 목록이 스크롤로 밀려 내려갈 뿐, 다이얼로그 자체는 커지지
        # 않는다.
        self._base_height = 620
        self.setMinimumSize(420, 560)
        self.resize(420, self._base_height)

        payload = data.get("payload", {}) or {}
        events  = payload.get("events", []) or []
        detail  = payload.get("detail", {}) or {}
        played  = payload.get("played", True)
        benched = payload.get("benched", False)
        pos     = payload.get("position", "")

        is_home = bool(data.get("is_home"))
        loc = "홈" if is_home else "원정"
        rs  = {"win": "승", "draw": "무", "loss": "패"}.get(data.get("result", ""), "")
        rs_color = {"승": "#4488ff", "무": "#888888", "패": "#ff4444"}.get(rs, "#ccc")

        # ── 헤더: 리그 / 주차 / 스코어 ──────────────────────────
        head = QLabel(f"⚽ {data.get('league_name','')}  ·  "
                      f"{data.get('year','')}년 {data.get('week','')}주차  ({loc})")
        head.setStyleSheet("color:#44ccff;font-size:13px;font-weight:bold;")
        root.addWidget(head)

        score = QLabel(f"{data.get('home_name','')}  "
                       f"{data.get('home_score',0)} - {data.get('away_score',0)}  "
                       f"{data.get('away_name','')}")
        score.setStyleSheet("color:#fff;font-size:18px;font-weight:bold;")
        score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(score)

        if played:
            btn_row = QWidget()
            bh = QHBoxLayout(btn_row); bh.setContentsMargins(0, 0, 0, 0); bh.setSpacing(6)

            sim_btn = QPushButton("▶ 시뮬 보기")
            sim_btn.setStyleSheet(
                "QPushButton{background:#1a4d8f;color:#fff;border:1px solid #3a7fd5;"
                "border-radius:6px;padding:6px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#2360ad;}")
            sim_btn.clicked.connect(lambda: self._show_sim())
            bh.addWidget(sim_btn)

            stats_btn = QPushButton("📊 경기 통계")
            stats_btn.setStyleSheet(
                "QPushButton{background:#2a2a2a;color:#ccc;border:1px solid #444;"
                "border-radius:6px;padding:6px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#3a3a3a;}")
            stats_btn.clicked.connect(lambda: self._show_stats())
            bh.addWidget(stats_btn)

            lineup_btn = QPushButton("⭐ 라인업 평점")
            lineup_btn.setStyleSheet(
                "QPushButton{background:#2a2a2a;color:#ccc;border:1px solid #444;"
                "border-radius:6px;padding:6px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#3a3a3a;}")
            lineup_btn.clicked.connect(lambda: self._show_lineup_ratings())
            bh.addWidget(lineup_btn)

            root.addWidget(btn_row)

        res = QLabel(f"({rs})")
        res.setStyleSheet(f"color:{rs_color};font-size:14px;font-weight:bold;")
        res.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(res)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#2a2a2a;")
        root.addWidget(line)

        # ── 출전하지 않은 경기 ────────────────────────────────
        if not played:
            msg = QLabel("🪑 벤치 대기" if benched else "🚑 부상 결장")
            msg.setStyleSheet("color:#888;font-size:14px;")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(msg)
            root.addStretch()
            self._add_close(root)
            return

        # ── 내 기록 요약 ──────────────────────────────────────
        if pos == "GK":
            summary = QLabel(f"평점 {data.get('rating',0)}   선방 {data.get('saves',0)}")
        else:
            summary = QLabel(f"평점 {data.get('rating',0)}   "
                             f"골 {data.get('goals',0)}   어시 {data.get('assists',0)}")
        summary.setStyleSheet("color:#ffcc00;font-size:14px;font-weight:bold;")
        summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(summary)

        # ── 타임라인 (스크롤) ─────────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:1px solid #2a2a2a;border-radius:6px;"
                             "background:#1a1a1a;}"
                             "QScrollBar:vertical{background:#1a1a1a;width:6px;}"
                             "QScrollBar::handle:vertical{background:#3a3a3a;border-radius:3px;}")
        inner = QWidget(); iv = QVBoxLayout(inner)
        iv.setContentsMargins(10, 8, 10, 8); iv.setSpacing(3)

        # 전반 = 1~45 + 전반 추가시간(146~155). 그 외는 후반. 각 반은 시간순 정렬.
        fh = sorted([(m, t) for m, t in events if _is_first_half(m)],
                    key=lambda x: _min_sortkey(x[0]))
        sh = sorted([(m, t) for m, t in events if not _is_first_half(m)],
                    key=lambda x: _min_sortkey(x[0]))

        def add_half(title, items):
            hdr = QLabel(title)
            hdr.setStyleSheet("color:#66aaff;font-size:11px;font-weight:bold;"
                              "padding-top:4px;")
            iv.addWidget(hdr)
            if not items:
                e = QLabel("   특별한 장면 없음")
                e.setStyleSheet("color:#555;font-size:11px;")
                iv.addWidget(e)
            for m, t in items:
                if any(x in t for x in ("⚽","🅰","🎩","🔥","🧤","🧱","🏆","🎯")):
                    color = "#ffcc00"
                elif any(x in t for x in ("🛡","🔑","🌪","↗","💪")):
                    color = "#44ccff"   # 수비/창조 활약 — 하늘색
                elif any(x in t for x in ("😞","🟥","🥅","⚠","😤")):
                    color = "#ff6666"
                else:
                    color = "#cccccc"
                row = QLabel(f"  {_fmt_min(m)}'  {t}")
                row.setStyleSheet(f"color:{color};font-size:12px;")
                row.setWordWrap(True)
                iv.addWidget(row)

        add_half("⏱ 전반", fh)
        add_half("⏱ 후반", sh)
        iv.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        # ── 세부 지표 ─────────────────────────────────────────
        stat_box = QWidget()
        sv = QVBoxLayout(stat_box); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(2)
        st_hdr = QLabel("📊 세부 지표")
        st_hdr.setStyleSheet("color:#888;font-size:11px;font-weight:bold;")
        sv.addWidget(st_hdr)
        pa = detail.get("pass_acc", 0.0)
        pa_str = f"{pa*100:.0f}%" if pa else "-"
        shots     = detail.get("shots", 0)
        shots_on  = detail.get("shots_on", 0)
        key_passes= detail.get("key_passes", 0)
        dribbles  = detail.get("dribbles", 0)
        blocks    = detail.get("blocks", 0)
        saves_det = data.get("saves", 0)

        from constants import position_group
        pos_grp = position_group(pos)

        if pos == "GK":
            # GK: 선방수 + 선방률 + 패스%
            tot_shots = saves_det + (data.get("away_score",0) if data.get("is_home") else data.get("home_score",0))
            sr_str = f"{saves_det}/{tot_shots} ({saves_det*100//tot_shots if tot_shots else 0}%)" if tot_shots else f"{saves_det}"
            sv.addWidget(_row("선방 (유효슈팅)", sr_str, "#44ccff"))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos in ("CB",):
            # CB: 차단 우선, 헤딩 클리어 개념, 패스%
            sv.addWidget(_row("차단 (태클·인터셉트)", str(blocks), "#44ff88" if blocks >= 3 else "#fff"))
            sv.addWidget(_row("패스 성공률", pa_str))
            sv.addWidget(_row("슈팅", str(shots)))

        elif pos in ("CDM",):
            # CDM: 차단 + 키패스(전방연결) + 패스%
            sv.addWidget(_row("차단 (태클·인터셉트)", str(blocks), "#44ff88" if blocks >= 3 else "#fff"))
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes)))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos in ("LB", "RB"):
            # LB/RB: 어시 창출(키패스) + 차단 + 패스%
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes), "#44ccff" if key_passes >= 2 else "#fff"))
            sv.addWidget(_row("차단 (태클·인터셉트)", str(blocks)))
            sv.addWidget(_row("드리블 성공", str(dribbles)))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos in ("CM",):
            # CM: 키패스 + 차단 + 드리블 + 패스%
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes)))
            sv.addWidget(_row("차단 (태클·인터셉트)", str(blocks)))
            sv.addWidget(_row("드리블 성공", str(dribbles)))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos == "CAM":
            # CAM: 키패스 우선, 드리블, 슈팅, 패스%
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes), "#44ccff" if key_passes >= 3 else "#fff"))
            sv.addWidget(_row("드리블 성공", str(dribbles)))
            sv.addWidget(_row("슈팅 (유효)", f"{shots} ({shots_on})"))
            sv.addWidget(_row("패스 성공률", pa_str))

        elif pos in ("LW", "RW"):
            # LW/RW: 드리블 + 키패스 + 슈팅 + 패스%
            sv.addWidget(_row("드리블 성공", str(dribbles), "#44ccff" if dribbles >= 4 else "#fff"))
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes)))
            sv.addWidget(_row("슈팅 (유효)", f"{shots} ({shots_on})"))
            sv.addWidget(_row("패스 성공률", pa_str))

        else:  # ST/CF 및 기타
            # ST/CF: 슈팅 우선, 키패스, 드리블
            sv.addWidget(_row("슈팅 (유효)", f"{shots} ({shots_on})", "#ffcc44" if shots >= 4 else "#fff"))
            sv.addWidget(_row("기회 창출 (키패스)", str(key_passes)))
            sv.addWidget(_row("드리블 성공", str(dribbles)))
            sv.addWidget(_row("패스 성공률", pa_str))
        root.addWidget(stat_box)

        # ── 총평 ──────────────────────────────────────────────
        verdict = payload.get("verdict", "")
        if verdict:
            v = QLabel(verdict)
            v.setStyleSheet("color:#fff;font-size:13px;font-weight:bold;"
                            "background:#222;border-radius:6px;padding:8px;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setWordWrap(True)
            root.addWidget(v)

        self._add_close(root)

    # ── 오른쪽 패널 펼치기/접기 ──────────────────────────────
    def _clear_right_panel(self):
        """오른쪽에 떠 있던 위젯을 치운다. 시뮬 뷰어였다면 QTimer부터 반드시
        멈춘다 — 안 그러면 화면에서 사라진 뒤에도 백그라운드에서 계속 돌면서
        불필요하게 CPU를 먹거나, 이미 지워진 위젯을 참조하다 에러가 날 수 있다.
        시뮬 뷰어는 결정론적 재생을 위해 열려있는 동안 전역 random 상태를
        고정해두는데(_pre_seed_rng_state), 패널 전환 시엔 closeEvent가 안
        불리므로 여기서도 직접 복원해줘야 한다."""
        if self._right_widget is not None:
            timer = getattr(self._right_widget, "timer", None)
            if timer is not None:
                timer.stop()
            pre_seed_state = getattr(self._right_widget, "_pre_seed_rng_state", None)
            if pre_seed_state is not None:
                import random
                random.setstate(pre_seed_state)
            self._right_layout.removeWidget(self._right_widget)
            # [2026-08] setParent(None)은 부모를 뗀 그 순간부터 deleteLater가
            # 실제로 도는 다음 이벤트 루프 틱까지 이 위젯을 "최상위 창"으로
            # 만든다(유령 흰 창의 원인 — ui/formation_widget._build_filter_row
            # 주석 참고). 부모를 그대로 둔 채 숨기고 삭제만 예약한다.
            self._right_widget.hide()
            self._right_widget.deleteLater()
            self._right_widget = None
        # [2026-08 버그수정] 예전엔 이 함수가 위젯만 치우고 컨테이너 폭은
        # 그대로 둬서(_open_right_panel이 바로 뒤이어 새 폭을 덮어씌우는
        # 경우에만 문제가 없었음), 토글로 닫기만 할 때는 위젯 없이 폭만
        # 760px로 남아 빈 회색 칸이 떠 있었다. _clear_left_stats/
        # _clear_lineup_panel과 동일하게 여기서도 폭을 0으로 되돌린다.
        self._right_container.setFixedWidth(0)

    def _open_right_panel(self, widget, width=760):
        """[시뮬 전용] 오른쪽 패널은 이제 시뮬 뷰어만 사용한다."""
        self._clear_right_panel()
        self._right_widget = widget
        self._right_layout.addWidget(widget)
        self._right_container.setFixedWidth(width)
        self._resize_for_content()

    def _clear_left_stats(self):
        """통계 패널 비우기 (가운데 패널 오른쪽에 뜬다 — 변수명은 옛 구조의
        흔적이라 '왼쪽'이지만 실제 표시 위치와는 무관)."""
        if self._left_stats_widget is not None:
            self._left_layout.removeWidget(self._left_stats_widget)
            # [2026-08] 위 _clear_right_panel과 같은 이유.
            self._left_stats_widget.hide()
            self._left_stats_widget.deleteLater()
            self._left_stats_widget = None
        self._left_container.setFixedWidth(0)

    def _open_left_panel(self, widget, width=300):
        """[통계 전용] 가운데 패널 오른쪽에 뜨는 통계 패널."""
        self._clear_left_stats()
        self._left_stats_widget = widget
        self._left_layout.addWidget(widget)
        self._left_container.setFixedWidth(width)
        self._resize_for_content()

    def _clear_lineup_panel(self):
        """[2026-08 신설] 라인업 평점 패널 비우기 — _clear_left_stats와
        동일한 이유(유령 흰 창 방지)로 부모를 떼지 않고 숨긴 뒤 삭제 예약."""
        if self._lineup_widget is not None:
            self._lineup_layout.removeWidget(self._lineup_widget)
            self._lineup_widget.hide()
            self._lineup_widget.deleteLater()
            self._lineup_widget = None
        self._lineup_container.setFixedWidth(0)

    def _open_lineup_panel(self, widget, width=_LINEUP_PANEL_WIDTH):
        self._clear_lineup_panel()
        self._lineup_widget = widget
        self._lineup_layout.addWidget(widget)
        self._lineup_container.setFixedWidth(width)
        self._resize_for_content()

    def _show_lineup_ratings(self):
        """[2026-08 버그수정, 신민용 리포트: "버튼 한 번 더 누르면 그
        창 닫히게 해줘"] 이미 열려 있으면 닫고(토글), 닫혀 있으면 연다."""
        if self._lineup_widget is not None:
            self._clear_lineup_panel()
            self._resize_for_content()
            return
        self._open_lineup_panel(LineupRatingsPanel(self._data, self), width=_LINEUP_PANEL_WIDTH)

    def _resize_for_content(self):
        """현재 왼쪽(통계 유무)·오른쪽(시뮬 유무)·라인업 평점 유무에 맞춰
        다이얼로그 너비를 다시 계산한다. 셋 다 각자 고정폭 패널로 독립돼
        있어서(기존 420px 칸은 안 건드림) 높이는 항상 기본값 그대로다."""
        left_w = 300 if self._left_stats_widget is not None else 0
        right_w = 760 if self._right_widget is not None else 0
        lineup_w = _LINEUP_PANEL_WIDTH if self._lineup_widget is not None else 0
        new_w = left_w + 420 + right_w + lineup_w
        self.setMinimumSize(420, self._base_height)
        self.resize(new_w, self._base_height)

    def _show_sim(self):
        """[2026-08 버그수정, 신민용 리포트: "버튼 한 번 더 누르면 그
        창 닫히게 해줘"] 이미 열려 있으면 닫고(토글), 닫혀 있으면 연다.
        닫을 때는 시뮬 패널만 접고, 자동으로 같이 열렸던 통계 패널은
        그대로 둔다(사용자가 통계 버튼으로 따로 껐다 켰다 할 수 있게)."""
        if self._right_widget is not None:
            self._clear_right_panel()
            self._resize_for_content()
            return
        from ui.match_sim_viewer import MatchSimViewer
        sim_widget = MatchSimViewer(self._data, self)
        # [핵심] 새 창(QDialog.show()) 대신 이 다이얼로그 오른쪽에 인라인으로
        # 붙인다 — windowFlags를 Widget으로 바꾸면 독립된 창 대신 평범한
        # 자식 위젯처럼 레이아웃에 들어간다. 내부 로직은 그대로 재사용.
        sim_widget.setWindowFlags(Qt.WindowType.Widget)
        self._open_right_panel(sim_widget, width=760)
        # [신규] 시뮬 보기가 켜지면 팀 경기 통계도 독립 패널로 같이 띄운다.
        #   (가운데 420px 다음에 통계 → 시뮬 → 라인업 평점 순으로 나란히)
        if self._left_stats_widget is None:
            self._open_left_panel(MatchStatsPanel(self._data, self), width=300)

    def _show_stats(self):
        """[2026-08 버그수정, 신민용 리포트: "버튼 한 번 더 누르면 그
        창 닫히게 해줘"] 이미 열려 있으면 닫고(토글), 닫혀 있으면 연다.
        오른쪽 패널은 시뮬 전용이라 여기서 건드리지 않는다."""
        if self._left_stats_widget is not None:
            self._clear_left_stats()
            self._resize_for_content()
            return
        self._open_left_panel(MatchStatsPanel(self._data, self), width=300)

    def closeEvent(self, event):
        self._clear_right_panel()
        self._clear_left_stats()
        self._clear_lineup_panel()
        super().closeEvent(event)

    def _add_close(self, root):
        btn = QPushButton("닫기")
        btn.setStyleSheet("QPushButton{background:#2a2a2a;color:#ccc;border:none;"
                          "border-radius:6px;padding:8px;font-size:12px;}"
                          "QPushButton:hover{background:#3a3a3a;}")
        btn.clicked.connect(self.accept)
        root.addWidget(btn)