"""
ui/player_panel.py  ─  좌측 선수 정보 패널
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QPushButton
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QBrush, QColor, QPen

from game_engine import (get_player, get_team_rank, get_team_rank_with_zone_color, fmt_money,
                         is_hard_mode, get_season_all_competition_appearances,
                         _get_season_total_matches, team_matches_played_in_window, get_state)
from constants import (ALL_STATS, STAT_KO, STAT_EN, _LEGACY_TALENT_ALIAS,
                       TALENT_TIER_KO, TALENT_TIER_EN, MANAGER_TYPES)

# [2026-08 수정, 신민용 확정: 9단계 확장] 예전엔 이 파일이 constants.py와
# 별개로 자기만의 5단계(worldclass~ordinary) 이름 복사본을 들고 있었다 —
# constants.py 쪽 주석("표시 문구가 여러 곳에서 따로 하드코딩되어 서로
# 어긋나는 걸 방지")이 무색하게, 실제로는 여기 하나가 안 맞춰져 있었다.
# 이름은 이제 TALENT_TIER_KO/EN을 그대로 가져다 쓰고, 색상만 이 파일
# 고유의 값이라 여기서 9단계 전부 채운다(신규 4개: god/superstar/
# amateur/untalented).
_TALENT_KO = TALENT_TIER_KO
_TALENT_EN = TALENT_TIER_EN
_TALENT_COLOR = {
    "god":         "#ff2266",  # 진한 핑크레드 — 최상위 GOAT급, 눈에 확 띄게
    "worldclass":  "#b8860b",  # 골드
    "superstar":   "#c9862f",  # 골드보다 살짝 옅은 브론즈골드
    "elite":       "#7a4fc9",  # 퍼플
    "pro":         "#2a6a9e",  # 블루
    "semipro":     "#3d7a5c",  # 그린 계열(세미프로~아마추어 사이 구분용)
    "amateur":     "#555555",  # 그레이
    "ordinary":    "#3a3a3a",  # 다크그레이
    "untalented":  "#2a2a2a",  # 가장 어두운 그레이
}

# ════════════════════════════════════════════════════════════════
# [2026-08 신설] 신체(부상) 탭 — 부상 부위 실루엣 표시용 매핑.
#
# 주의: game_engine._apply_injury()는 아직 "구체 부상명"만 고르고
# 좌/우 및 신체 zone은 별도로 저장하지 않는다(부상 시스템 설계 문서
# 6번 항목 "초기에는 단일 게이지에서 결정"과 15번 항목 "좌우 구분"이
# 아직 미확정 상태인 것과 동일 맥락). 그래서 이 매핑은 DB에 새 컬럼을
# 추가하지 않고, 이미 존재하는 injury_detail 문자열만으로 어느 부위
# [2026-08 신설] 등급별 강조 색상(부상 시스템 설계 문서 13번 항목:
# 경미=노랑, 중간=주황, 심각=빨강, 매우심각=강한빨강).
INJURY_TIER_COLOR = {
    "경미":     QColor("#d4b106"),
    "중간":     QColor("#e07b1a"),
    "심각":     QColor("#cc3333"),
    "매우 심각": QColor("#8b0000"),
}

# [2026-08] 부상 부위 zone 키 -> 한글 표시명. game_engine._apply_injury()가
# injury_body_part에 실제 zone(예: 'l_knee', 'neck', 'r_hand')을 직접 저장해
# 주므로, 예전처럼 부상명에서 부위를 추측하거나 좌/우를 해시로 지어낼 필요가
# 없어졌다 — 여기서는 그 zone 키를 화면에 보여줄 한글 문구로만 바꿔준다.
ZONE_KO = {
    "head": "머리", "neck": "목", "chest": "가슴", "abdomen": "복부",
    "back": "허리", "pelvis": "골반",
    "shoulder": "어깨", "upper_arm": "팔", "elbow": "팔꿈치",
    "forearm": "팔뚝", "hand": "손",
    "thigh": "허벅지", "knee": "무릎", "calf": "종아리",
    "ankle": "발목", "foot": "발",
}
_SIDE_KO = {"l": "왼쪽", "r": "오른쪽"}


def zone_label_ko(zone_key: str) -> str:
    """'l_knee' -> '왼쪽 무릎', 'neck' -> '목' 처럼 zone 키를 표시용
    한글로 바꾼다. 실제 데이터가 없던 시절엔 이 좌/우를 이름 해시로
    추측했지만, 이제 injury_body_part에 실제 값이 저장되므로 그대로
    보여주면 된다."""
    if not zone_key:
        return "몸통"
    if "_" in zone_key and zone_key.split("_", 1)[0] in _SIDE_KO:
        side, base = zone_key.split("_", 1)
        return f"{_SIDE_KO[side]} {ZONE_KO.get(base, base)}"
    return ZONE_KO.get(zone_key, zone_key)


PANEL_STYLE = """
QWidget { background-color: #1e1e1e; color: #cccccc; font-size: 12px; }
#pName  { color: #00ff66; font-size: 16px; font-weight: bold; }
#ovrBadge { background-color: #2a6a2a; color: white;
            padding: 2px 8px; border-radius: 4px; font-size: 12px; }
#talentBadge { color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
#injBadge { background-color: #8b0000; color: white;
            padding: 2px 6px; border-radius: 4px; font-size: 11px; }
#slumpBadge { background-color: #555500; color: #ffff00;
              padding: 2px 6px; border-radius: 4px; font-size: 11px; }
#rankLabel  { color: #00cc44; font-size: 13px; font-weight: bold; }
#secTitle   { color: #888888; font-size: 11px;
              border-bottom: 1px solid #2a2a2a; padding-bottom: 2px; }
#divider    { background-color: #2a2a2a; }
QPushButton#tabBtn {
    background-color: rgba(255,255,255,18); color:#888888;
    border: 1px solid rgba(255,255,255,30); border-radius: 4px;
    padding: 4px 0px; font-size: 11px; font-weight: bold;
}
QPushButton#tabBtn:hover { background-color: rgba(255,255,255,35); color:#bbbbbb; }
QPushButton#tabBtn:checked {
    background-color: #2d5a2d; color: #ffffff; border: 1px solid #4caf50;
}
QProgressBar { background-color: #2a2a2a; border-radius: 3px; border: none; }
QProgressBar#stressBar::chunk { background-color: #cc4400; border-radius:3px; }
QProgressBar#happyBar::chunk  { background-color: #00aa44; border-radius:3px; }

/* ── 정보 행: 라벨칸 + 값칸을 테두리 박스로 구분 ── */
#infoRow  { background-color: transparent; }
#infoKey  { color: #9aa0a6; font-size: 11px; font-weight: bold;
            background-color: #262626; border: 1px solid #3a3a3a;
            border-right: none; border-top-left-radius: 4px;
            border-bottom-left-radius: 4px; padding: 4px 4px; }
#infoVal  { color: #e0e0e0; font-size: 12px;
            background-color: #1c1c1c; border: 1px solid #3a3a3a;
            border-top-right-radius: 4px; border-bottom-right-radius: 4px;
            padding: 4px 6px; }
/* [2026-08 신설] 감독관계처럼 값칸을 2개(숫자+성향)로 나눠 보여줄 때 —
   첫 번째 값칸은 오른쪽도 각지게(다음 칸과 맞닿음), 구분선만 얇게. */
#infoValMid { color: #e0e0e0; font-size: 12px;
              background-color: #1c1c1c; border: 1px solid #3a3a3a;
              border-right: 1px solid #2a2a2a; padding: 4px 6px; }
"""


class PlayerPanel(QWidget):
    def __init__(self, main_win=None):
        super().__init__()
        self.main_win = main_win
        self.setStyleSheet(PANEL_STYLE)
        self._build()

    # ── 빌드 ─────────────────────────────────────

    # [2026-08 신설, 신민용 확정: 탭 버튼 UI] 좌측 패널이 신체(부상 부위)
    # 이미지 추가로 인해 세로로 너무 길어지는 걸 막기 위해, OVR 뱃지 위에
    # "전체 | 기본 | 시즌 | 신체 | 스탯" 5개 토글 버튼을 두고 원하는
    # 섹션만 눌러서 볼 수 있게 한다.
    # - "전체": 지금까지 누른 개별 선택을 무시하고 기본→시즌→신체→스탯
    #   고정 순서로 전부 표시.
    # - 개별 4개 버튼: 다중 선택 가능(토글). 안 누르면 반투명, 누르면
    #   초록색으로 표시되며, 표시 순서는 "누른 순서" 그대로 위에서부터
    #   쌓인다(예: 신체→기본 순으로 누르면 신체 섹션이 기본보다 위).
    DEFAULT_ORDER = ["기본", "시즌", "신체", "스탯"]

    def _build(self):
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(8,8,8,8)
        self.lay.setSpacing(4)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 이름 + 재능 뱃지(같은 줄) / 그 아래 줄에 OVR + 부상·슬럼프 상태
        self.lbl_name  = QLabel("—"); self.lbl_name.setObjectName("pName")
        self.lbl_name.setWordWrap(True)          # 긴 이름 자동 줄바꿈
        self.lbl_ovr   = QLabel("OVR 0"); self.lbl_ovr.setObjectName("ovrBadge")
        self.lbl_talent = QLabel(""); self.lbl_talent.setObjectName("talentBadge")
        self.lbl_state = QLabel(""); self.lbl_state.setObjectName("injBadge")
        self.lbl_state.setWordWrap(True)         # 부상 상세 텍스트가 길어도 잘리지 않고 줄바꿈

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.addWidget(self.lbl_name, 1)     # 이름이 늘어나면 이 쪽이 먼저 넓어짐
        name_row.addWidget(self.lbl_talent, 0)
        self.lay.addLayout(name_row)

        # 탭 버튼 행 (OVR 뱃지 바로 위)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 3, 0, 3)
        tab_row.setSpacing(3)
        self.tab_buttons: dict[str, QPushButton] = {}
        self.btn_all = QPushButton("전체")
        self.btn_all.setObjectName("tabBtn")
        self.btn_all.setCheckable(True)
        self.btn_all.clicked.connect(self._on_click_all)
        tab_row.addWidget(self.btn_all)
        for key in self.DEFAULT_ORDER:
            b = QPushButton(key)
            b.setObjectName("tabBtn")
            b.setCheckable(True)
            b.clicked.connect(lambda _checked, k=key: self._on_click_tab(k))
            tab_row.addWidget(b)
            self.tab_buttons[key] = b
        self.lay.addLayout(tab_row)

        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(0, 2, 0, 0)
        badge_row.addWidget(self.lbl_ovr, 0)
        badge_row.addWidget(self.lbl_state, 1)   # 부상 텍스트가 길면 이 라벨이 줄바꿈되며 늘어남
        self.lay.addLayout(badge_row)
        self._div()

        # ── 섹션별 콘텐츠(내용은 refresh()에서 채움) ──

        # 기본 정보 영역 (동적)
        self.info_frame = QWidget()
        self.info_lay   = QVBoxLayout(self.info_frame)
        self.info_lay.setSpacing(3); self.info_lay.setContentsMargins(0,0,0,0)

        # 시즌 섹션: 팀 순위 + 스트레스/행복도 + 이번 시즌 기록을 하나로 묶는다.
        self.sec_season = QWidget()
        season_v = QVBoxLayout(self.sec_season)
        season_v.setSpacing(4); season_v.setContentsMargins(0,0,0,0)

        # [2026-08 신설, 신민용 요청: "시즌 탭만 봐도 소속팀/리그/연봉이
        # 보이면 좋겠다 — 이것만 보려고 기본 탭까지 켜지 않게"] 등수 위에
        # 소속팀·리그(몇부)·연봉·감독관계를 요약해서 보여준다. "기본" 탭의
        # info_lay와 별개 위젯(season_top_lay)이라 refresh()에서 따로
        # 채운다.
        self.season_top_frame = QWidget()
        self.season_top_lay   = QVBoxLayout(self.season_top_frame)
        self.season_top_lay.setSpacing(3); self.season_top_lay.setContentsMargins(0,0,0,4)
        season_v.addWidget(self.season_top_frame)

        self.lbl_rank = QLabel(""); self.lbl_rank.setObjectName("rankLabel")
        season_v.addWidget(self.lbl_rank)
        season_v.addWidget(self._mk_div())

        self.vitals_frame = QWidget()
        vitals_v = QVBoxLayout(self.vitals_frame)
        vitals_v.setSpacing(4); vitals_v.setContentsMargins(0,0,0,0)
        self.lbl_stress = QLabel("스트레스  0")
        self.bar_stress = StatBar(bar_max=100); self.bar_stress.setFixedHeight(8)
        self.lbl_happy  = QLabel("행복도  0")
        self.bar_happy  = StatBar(bar_max=100); self.bar_happy.setFixedHeight(8)
        # [2026-08 신설, 부상 시스템 확장 2단계] 신체 부담(injury_load) —
        # 스트레스와 같은 원리(0~100, 훈련/경기로 증가·휴식으로 감소)로
        # 동작하지만 별개 축이다: 스트레스는 "지금 당장 얼마나 지쳤는가"
        # (짧은 주기로 오르내림), 신체 부담은 "장기간 얼마나 혹사됐는가"
        # (휴식으로도 절반만 풀림 — game_engine._process_training 참고).
        self.lbl_load = QLabel("신체 부담  0")
        self.bar_load = StatBar(bar_max=100); self.bar_load.setFixedHeight(8)
        for w in [self.lbl_stress, self.bar_stress, self.lbl_happy, self.bar_happy,
                  self.lbl_load, self.bar_load]:
            vitals_v.addWidget(w)
        season_v.addWidget(self.vitals_frame)
        season_v.addWidget(self._mk_div())

        season_title = QLabel("이번 시즌"); season_title.setObjectName("secTitle")
        season_v.addWidget(season_title)
        self.season_frame = QWidget()
        self.season_lay   = QVBoxLayout(self.season_frame)
        self.season_lay.setSpacing(2); self.season_lay.setContentsMargins(0,0,0,0)
        season_v.addWidget(self.season_frame)

        # 신체 섹션(NEW): 부상 부위를 신체 실루엣으로 표시.
        # (부상 시스템 설계 문서 10~13번 항목 — 해부학 도감이 아니라
        #  "부상 위치만 알려주는 자연스러운 인체 실루엣" 컨셉)
        self.sec_body = QWidget()
        body_v = QVBoxLayout(self.sec_body)
        body_v.setContentsMargins(0,0,0,0); body_v.setSpacing(6)

        self.body_silhouette = BodySilhouette()
        body_v.addWidget(self.body_silhouette)

        self.lbl_body_detail = QLabel("🩹 부상 없음")
        self.lbl_body_detail.setWordWrap(True)
        self.lbl_body_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_body_detail.setStyleSheet("color:#aaaaaa; font-size:12px; padding:2px 4px;")
        body_v.addWidget(self.lbl_body_detail)

        # 스탯 섹션: 신체(체력/스피드/점프/몸싸움) + 기술 + 정신 스탯 바.
        self.sec_stat = QWidget()
        stat_v = QVBoxLayout(self.sec_stat)
        stat_v.setSpacing(4); stat_v.setContentsMargins(0,0,0,0)
        self.stat_rows: dict[str, StatRow] = {}
        self.stat_section_labels = []
        _first = True
        for section, stats in [
            ("신체", ["stamina","speed","jump","strength"]),
            ("기술", ["shooting","passing","dribbling","tackling",
                      "heading","positioning","setpiece"]),
            ("정신", ["mental","confidence","leadership","concentration"]),
        ]:
            if not _first:
                stat_v.addWidget(self._mk_div())
            _first = False
            sl = QLabel(section); sl.setObjectName("secTitle")
            stat_v.addWidget(sl)
            self.stat_section_labels.append(sl)
            for s in stats:
                row = StatRow(s)
                self.stat_rows[s] = row
                stat_v.addWidget(row)

        self.sections = {
            "기본": self.info_frame,
            "시즌": self.sec_season,
            "신체": self.sec_body,
            "스탯": self.sec_stat,
        }

        # 선택된 섹션들이 "누른 순서"대로 쌓이는 콘텐츠 영역
        self.content_frame = QWidget()
        self.content_lay   = QVBoxLayout(self.content_frame)
        self.content_lay.setSpacing(4); self.content_lay.setContentsMargins(0,0,0,0)
        self.lay.addWidget(self.content_frame)

        self._order = list(self.DEFAULT_ORDER)   # 기본값: 전체 보기(기존 화면과 동일)
        self._sync_tab_buttons()
        self._rebuild_content()

    def _div(self):
        self.lay.addWidget(self._mk_div())

    def _mk_div(self):
        f = QFrame(); f.setObjectName("divider"); f.setFixedHeight(1)
        return f

    # ── 탭 버튼 로직 ─────────────────────────────

    def _on_click_all(self):
        # [2026-08 수정, 신민용 요청: "전체를 한번 더 누르면 다 꺼진 걸로"]
        # 이미 전체(4개 다) 선택된 상태에서 다시 누르면 토글로 전부 해제.
        if set(self._order) == set(self.DEFAULT_ORDER):
            self._order = []
        else:
            self._order = list(self.DEFAULT_ORDER)
        self._sync_tab_buttons()
        self._rebuild_content()

    def _on_click_tab(self, key):
        if key in self._order:
            self._order.remove(key)
        else:
            self._order.append(key)   # 누른 순서대로 맨 뒤(= 맨 아래)에 추가
        self._sync_tab_buttons()
        self._rebuild_content()

    def _sync_tab_buttons(self):
        for k, b in self.tab_buttons.items():
            b.blockSignals(True)
            b.setChecked(k in self._order)
            b.blockSignals(False)
        self.btn_all.blockSignals(True)
        self.btn_all.setChecked(set(self._order) == set(self.DEFAULT_ORDER))
        self.btn_all.blockSignals(False)

    def _rebuild_content(self):
        """content_lay를 self._order 순서대로 다시 채운다.
        섹션 위젯은 deleteLater가 아니라 setParent(None)으로만 떼어내
        (재사용을 위해) 다음 rebuild에서 다시 붙일 수 있게 한다."""
        while self.content_lay.count():
            item = self.content_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        _first = True
        for key in self._order:
            sec = self.sections.get(key)
            if sec is None:
                continue
            if not _first:
                self.content_lay.addWidget(self._mk_div())
            _first = False
            self.content_lay.addWidget(sec)

    # ── 갱신 ─────────────────────────────────────

    def refresh(self):
        p = get_player()
        try:
            from game_engine import get_field_pos as _gfp
            _cur_field_pos = _gfp(p) if p else "—"
        except Exception:
            _cur_field_pos = p.get("position", "—") if p else "—"
        if not p:
            return
        lang = p.get("language","ko")
        sn   = STAT_KO if lang=="ko" else STAT_EN

        self.lbl_name.setText(p["name"])

        # [2026-08 신설, 난이도 시스템] 어려움 난이도는 내 선수 포함 전원의
        # OVR·재능등급을 숨긴다(신민용 확정: "선수를 클릭해도 스탯이
        # 뜨지 않아야 하며" — 예외 없이 좌측 패널도 동일 원칙 적용).
        _hard = is_hard_mode(p)
        if _hard:
            self.lbl_ovr.hide()
            self.lbl_talent.hide()
        else:
            self.lbl_ovr.show()
            self.lbl_talent.show()
            self.lbl_ovr.setText(f"OVR {p['ovr']}")

            # [신규] 재능 등급 뱃지. 구버전 세이브의 예전 티어명은
            # _LEGACY_TALENT_ALIAS로 새 이름으로 변환한 뒤 표시한다.
            _tier = p.get("talent_tier", "pro") or "pro"
            _tier = _LEGACY_TALENT_ALIAS.get(_tier, _tier)
            _tname = _TALENT_KO.get(_tier, _tier) if lang == "ko" else _TALENT_EN.get(_tier, _tier)
            _tcolor = _TALENT_COLOR.get(_tier, "#555555")
            self.lbl_talent.setText(f"★ {_tname}")
            self.lbl_talent.setStyleSheet(f"background-color: {_tcolor};")

        # [2026-08 수정, 신민용 요청: "OVR 옆 빨간 부상 표시는 없애자,
        # 어차피 신체 탭에 뜨니까"] 부상 상세는 이제 신체 탭 전용 — 여기
        # 배지는 슬럼프 상태만 계속 담당한다.
        if p.get("slump") and not p.get("injured"):
            self.lbl_state.setText("😰 슬럼프")
            self.lbl_state.setObjectName("slumpBadge"); self.lbl_state.show()
        else:
            self.lbl_state.hide()

        # 신체 탭: 부상 부위 실루엣 강조 + 상세 텍스트
        # [2026-08] injury_body_part는 이제 game_engine._apply_injury()가
        # 실제로 저장하는 값(추측 아님) — 그대로 갖다 쓰면 된다.
        if p.get("injured"):
            _bdetail = p.get("injury_detail") or "부상"
            _btier   = p.get("injury_type") or "경미"
            _zone = p.get("injury_body_part") or "abdomen"
            _zone_ko = zone_label_ko(_zone)
            _bcolor = INJURY_TIER_COLOR.get(_btier, INJURY_TIER_COLOR["경미"])
            self.body_silhouette.set_highlight(_zone, _bcolor)
            self.lbl_body_detail.setText(
                f"🩹 {_zone_ko}\n{_bdetail}\n{_btier} · 회복까지 {p.get('injury_weeks',0)}일"
            )
        else:
            self.body_silhouette.set_highlight(None, None)
            self.lbl_body_detail.setText("🩹 부상 없음")

        # 기본 정보 재구성
        _clear_layout(self.info_lay)

        team_name   = "없음"
        league_name = "—"
        tier        = 0
        _raw_league_name = ""  # [2026-08 신설] team_matches_played_in_window에 넘길 원본 리그명
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
                _raw_league_name = row["lname"]
                tier        = row["tier"]

        fame_lbl = _fame(p.get("fame",0), lang)
        salary   = p.get("salary",0)
        # [2026-07 수정, 신민용 지적: "축구는 월급이 아니라 주급으로 얘기하지
        # 않나"] 실제 축구는 유럽 기준 관례상 항상 '주급'으로 얘기한다
        # (손흥민 주급 X억 식) — 월급 표기를 주급으로 바꾼다.
        weekly = max(1, salary // 52) if salary > 0 else 0

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
            ("나이",   f"{p['age']}세 ({p.get('birth_year', p['current_year'] - p['age'])}년생)"),
            ("국적",   _nat_str),
            ("소속",   team_name),
            ("리그",   league_name),
            ("주요 포지션", p["position"]),
            ("현 포지션",   _cur_field_pos),
        ]
        # [2026-08 신설, 난이도 시스템] 성격/신체특징(부상·성장 관련 숨겨진
        # 특성)/감독관계는 어려움 난이도에서 비표시 — 키/몸무게/체형처럼
        # 겉으로 관찰 가능한 정보가 아니라 원래 내부 수치이기 때문에
        # OVR·재능등급과 같은 취급을 한다.
        if not _hard:
            rows.append(("성격",   p["personality"]))
            rows.append(("특징",   p.get("physical_trait", "무난함")))
        rows.append(("체형",   p.get("body_type", "-")))
        rows.append(("신체",   f"{p['height']}cm / {p['weight']}kg"))
        rows.append(("명성",   f"{p.get('fame',0)} [{fame_lbl}]"))
        rows.append(("인기도", str(p.get("popularity",0))))
        rows.append(("팬수",   f"{p.get('fans',0):,}명"))
        # [2026-08 수정, 신민용 리포트: "에이전트가 등급만 뜨고 어디 전문인지
        # 안 뜬다"] agent_window.py가 에이전트 계약 시 저장하는
        # agent_continent(대륙 전문 분야)를 같이 표시한다. 없으면(대륙
        # 배정이 안 된 예전 세이브 등) 등급만 그대로 표시.
        _ag_cont = p.get("agent_continent", "") or ""
        _ag_txt = f"[{p.get('agent_grade','없음')}등급 · {_ag_cont} 전문]" if _ag_cont \
                  else f"[{p.get('agent_grade','없음')}등급]"
        rows.append(("에이전트", _ag_txt))
        rows.append(("연봉",   "무급" if salary == 0 else
                     f"연 {fmt_money(salary)}  [주 {fmt_money(weekly)}]"))
        rows.append(("총자산", fmt_money(p.get("total_assets",0))))
        for k, v in rows:
            self.info_lay.addWidget(_info_row(k, v))
        # [2026-08 수정, 신민용 리포트: "감독관계 성향 설명 문구가 쓸데없다 +
        # 팀이 없는데도 감독관계가 표시된다"] 감독 성향은 이름만 표시(desc
        # 문구 제거)하고, 소속팀이 없으면(current_team_id 없음) 애초에 감독이
        # 없는 상태이므로 호감도/성향 모두 "-"로 표시한다.
        _has_team = bool(p.get("current_team_id"))
        if not _hard:
            if _has_team:
                _mt = p.get("manager_type", "베테랑 신뢰")
                _rel_txt, _mt_txt = str(p.get("manager_relation", 50)), _mt
            else:
                _rel_txt, _mt_txt = "-", "-"
            self.info_lay.addWidget(_info_row_2val(
                "감독관계", _rel_txt, _mt_txt))
        # [2026-08 신설, 신민용 요청: "감독관계 아래에 구단 목표도
        # 표시해달라 — 구단 목표: 중위권 안정 이런 식으로"] club_ambition은
        # 이미 "중위권 안정"/"우승 도전" 같은 한글 문구 그대로 저장돼
        # 있어서(_infer_team_ambition/입단 시 offer의 ambition을 그대로
        # 옮겨 씀) 별도 라벨 매핑 없이 값 그대로 쓴다. 소속팀이 없으면
        # 감독관계와 동일하게 "-"로 표시(어려움 난이도에서는 감독관계와
        # 같은 취급으로 같이 숨김).
        if not _hard:
            _amb_txt = p.get("club_ambition", "") if _has_team else "-"
            self.info_lay.addWidget(_info_row("구단 목표", _amb_txt or "-"))

        # [2026-08 신설] 시즌 탭 상단 요약(소속팀/리그(몇부)/연봉/감독관계).
        _clear_layout(self.season_top_lay)
        self.season_top_lay.addWidget(_info_row("소속", team_name))
        self.season_top_lay.addWidget(_info_row("리그", league_name))
        self.season_top_lay.addWidget(_info_row(
            "연봉", "무급" if salary == 0 else
            f"연 {fmt_money(salary)}  [주 {fmt_money(weekly)}]"))
        if not _hard:
            if _has_team:
                _mt2 = p.get("manager_type", "베테랑 신뢰")
                _rel2_txt, _mt2_txt = str(p.get("manager_relation", 50)), _mt2
            else:
                _rel2_txt, _mt2_txt = "-", "-"
            self.season_top_lay.addWidget(_info_row_2val(
                "감독관계", _rel2_txt, _mt2_txt))
        # [2026-08 신설, 신민용 요청] 기본 탭과 동일하게 시즌 탭 요약에도
        # 감독관계 바로 아래에 구단 목표를 추가 — season_top_frame이
        # lbl_rank(순위, "공동 10위/14팀") 바로 위에 배치돼 있으므로
        # (season_v.addWidget 순서), 여기 마지막 줄로 추가하면 자연스럽게
        # "감독관계 아래 / 순위 위" 자리에 들어간다.
        if not _hard:
            _amb2_txt = p.get("club_ambition", "") if _has_team else "-"
            self.season_top_lay.addWidget(_info_row("구단 목표", _amb2_txt or "-"))


        # 순위
        # [2026-08 수정, 신민용 요청: "확정 강등권이면 빨간색, 확정
        # 승격권이면 파란색으로"] 정적 QSS(#rankLabel 항상 초록)로는
        # 상태별 색을 못 바꾸므로, 매번 인라인 스타일시트로 덮어쓴다.
        if p.get("current_team_id"):
            _rank_text, _rank_color = get_team_rank_with_zone_color(p["current_team_id"])
            self.lbl_rank.setText(_rank_text)
            self.lbl_rank.setStyleSheet(
                f"color:{_rank_color}; font-size:13px; font-weight:bold;")
        else:
            self.lbl_rank.setText("팀 없음" if lang=="ko" else "No Team")
            self.lbl_rank.setStyleSheet("")

        # 스트레스/행복도/신체 부담
        # [2026-08 신설, 신민용 확정: "스트레스나 행복도 그리고 신체 부담
        # 얘네가 어려움 모드에서는 안보여야해"] OVR·재능등급·스탯바와 같은
        # 취급 — 하드모드에서는 내부 컨디션 수치를 전부 숨긴다.
        self.vitals_frame.setVisible(not _hard)
        if p.get("injured"):
            _idetail2 = p.get("injury_detail") or "부상"
            self.lbl_stress.setText(f"스트레스  {p['stress']}   🚑 {_idetail2} {p['injury_weeks']}일 남음")
        else:
            self.lbl_stress.setText(f"스트레스  {p['stress']}")
        self.lbl_happy.setText(f"행복도  {p['happiness']}")
        self.lbl_load.setText(f"신체 부담  {p.get('injury_load', 0)}")
        self.bar_stress.set_values(p['stress'], 100)
        self.bar_stress._cur_color = QColor("#cc4400")
        self.bar_happy.set_values(p['happiness'], 100)
        self.bar_happy._cur_color = QColor("#00aa44")
        self.bar_load.set_values(p.get('injury_load', 0), 100)
        self.bar_load._cur_color = QColor("#a0522d")

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
        # 세부 지표
        d_sh  = p.get("season_shots",0)
        d_sho = p.get("season_shots_on",0)
        d_kp  = p.get("season_key_passes",0)
        d_drb = p.get("season_dribbles",0)
        d_blk = p.get("season_blocks",0)
        _pac_c = p.get("season_pass_acc_cnt",0)
        d_pac = round(p.get("season_pass_acc_sum",0.0)/_pac_c*100) if _pac_c else 0
        try:
            from game_engine import _calc_clean_sheets_for_player
            _cs = _calc_clean_sheets_for_player(p)
        except Exception:
            _cs = 0

        # [2026-08 수정, 신민용 리포트: "5주차인데 리그 경기가 1경기밖에
        # 없는데 왜 출전이 0/22로 뜨냐, 0/1이어야 하고 리그가 진행되면서
        # 커리어의 팀 이력 출전처럼 늘어나야 한다"] 예전 요청("N/전체
        # 형식으로")을 분모=시즌 전체 예정 경기수(_get_season_total_matches,
        # 예: 22)로 구현했었는데, 그러면 시즌 초반엔 실제로 열린 경기보다
        # 분모가 훨씬 커서 "0/22"처럼 아직 일어나지도 않은 경기까지 이미
        # 다 센 것처럼 보였다 — 커리어 탭의 "팀 이력" 출전(team_matches_
        # played_in_window)은 애초에 "그 기간 동안 실제로 열린 경기 수"만
        # 세는 방식이라 이 문제가 없다(진행 중인 시즌은 자동으로 현재
        # 주차까지만). 같은 함수를 그대로 재사용해 "이번 시즌 시작(1주차)
        # ~ 지금"까지 실제로 열린 리그 경기 수로 분모를 바꾼다 — 시즌이
        # 끝나면 자연히 전체 경기수(22 등)와 같아진다.
        _cur_year = get_state().get("current_year") or 0
        _league_total = None
        if p.get("current_team_id") and _raw_league_name and _cur_year:
            _league_total = team_matches_played_in_window(
                p["current_team_id"], _raw_league_name, _cur_year, 1, 0, 0)
        if not _league_total:
            _league_total = _get_season_total_matches(p.get("current_team_id"))
        # [2026-08 안전장치, 신민용 리포트: "출전이 137%로 뜬다"] 근본 원인은
        # game_engine._end_of_season의 season_matches 리셋이 함수 뒷부분
        # 로직 실패 시 통째로 스킵되던 버그였고 그건 별도로 고쳤다(리셋을
        # 스냅샷 직후로 이동) — 다만 화면 표시 자체도, 승격/강등으로 리그
        # 팀 수(=분모)가 바뀌는 등의 드문 경계 상황에서 실제 출전(sm)이
        # 분모를 넘어서더라도 100% 초과로 보이지 않게 표시만 클램프한다
        # (실제 경기 수 sm/_league_total 값 자체는 그대로 보여줌 — 숨기는
        # 건 %뿐).
        _league_rate = min(100.0, round(sm / _league_total * 100, 1)) if _league_total else 0.0
        _all_played, _all_total = get_season_all_competition_appearances(p)
        _appear_text = f"{sm}/{_league_total} ({_league_rate}%) · 총 {_all_played}경기"

        from constants import position_group
        pos = p.get("position","")
        grp = position_group(pos)

        if grp == "GK":
            total_shots = ss + sga
            save_rate = round(ss / total_shots * 100, 1) if total_shots > 0 else 0.0
            s_rows = [
                ("출전",     _appear_text),
                ("선방",     f"{ss}회 ({save_rate}%)"),
                ("실점",     f"{sga}골"),
                ("무실점",   f"{_cs}경기"),
                ("패스성공", f"{d_pac}%"),
                ("평균평점", str(avg_r)),
            ]
        elif grp == "DEF":
            # 수비수: 무실점·차단·패스성공이 핵심. 골/어시는 보조.
            s_rows = [
                ("출전",     _appear_text),
                ("무실점",   f"{_cs}경기"),
                ("차단",     f"{d_blk}회"),
                ("패스성공", f"{d_pac}%"),
                ("평균평점", str(avg_r)),
                ("공격P",    f"{sg}골 {sa}A"),
            ]
        elif pos in ("CM", "CDM", "CAM"):
            # 미드필더: 골/어시 + 기회창출·패스·차단
            s_rows = [
                ("출전",     _appear_text),
                ("골/어시",  f"{sg}골 {sa}A"),
                ("기회창출", f"{d_kp}회"),
                ("패스성공", f"{d_pac}%"),
                ("차단",     f"{d_blk}회"),
                ("평균평점", str(avg_r)),
            ]
        else:
            # 공격수/윙어: 골/어시 + 슈팅·유효슈팅·기회창출·드리블
            s_rows = [
                ("출전",     _appear_text),
                ("골/어시",  f"{sg}골 {sa}A"),
                ("슈팅",     f"{d_sh} (유효 {d_sho})"),
                ("기회창출", f"{d_kp}회"),
                ("드리블",   f"{d_drb}회"),
                ("평균평점", str(avg_r)),
            ]
        for k,v in s_rows:
            self.season_lay.addWidget(_info_row(k, v))

        # 스탯 바
        # [2026-08 신설, 난이도 시스템] 어려움 난이도는 이 신체/기술/정신
        # 스탯바 섹션 전체를 숨긴다(신민용 확정: "좌측 player_panel 아래에
        # 신체 스탯도 안떠야 해") — 상대 선수든 내 선수든 예외 없음.
        for sl in self.stat_section_labels:
            sl.setVisible(not _hard)
        for s, row in self.stat_rows.items():
            row.setVisible(not _hard)
            if not _hard:
                cur = p.get(s,40)
                mx  = p.get(f"{s}_max",80)
                row.update(sn.get(s,s), cur, mx)

        # [2026-08] 어려움 난이도에서는 스탯 바가 전부 숨겨지므로 "스탯" 탭
        # 버튼 자체도 숨긴다. 이미 선택돼 있었다면 표시 목록에서도 뺀다.
        self.tab_buttons["스탯"].setVisible(not _hard)
        if _hard and "스탯" in self._order:
            self._order.remove("스탯")
            self._sync_tab_buttons()
            self._rebuild_content()


def _info_row(key, val):
    """이미지처럼 '라벨칸 + 값칸'을 테두리 박스로 감싼 한 행.
    - 라벨칸: 고정폭(키 텍스트), 값칸: 남는 공간 전부 차지(글자 길어도 줄바꿈).
    - 값이 길어 잘릴 일은 WordWrap으로 처리하고, 패널 폭 자체는
      MainWindow의 스플리터 최소폭으로 확보한다.
    """
    w = QFrame()
    w.setObjectName("infoRow")
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(0)

    kl = QLabel(key)
    kl.setObjectName("infoKey")
    kl.setFixedWidth(64)
    kl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)

    vl = QLabel(val)
    vl.setObjectName("infoVal")
    vl.setWordWrap(True)
    vl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

    h.addWidget(kl)
    h.addWidget(vl, 1)   # 값칸이 남는 폭을 모두 차지
    return w


def _info_row_2val(key, val1, val2, ratio=(1, 2)):
    """[2026-08 신설, 신민용 요청: "감독 호감도 표시하는 상자를 1대2로
    나눠서 2에 감독의 목표(성향)를 넣는 게 좋을 거 같은데"] _info_row와
    같은 라벨칸 + 그 옆에 값칸을 2개(좁은 쪽=수치, 넓은 쪽=성향 텍스트)
    1:2 비율로 배치. 감독관계(숫자)+감독 성향 표시 전용."""
    w = QFrame()
    w.setObjectName("infoRow")
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(0)

    kl = QLabel(key)
    kl.setObjectName("infoKey")
    kl.setFixedWidth(64)
    kl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)

    vl1 = QLabel(val1)
    vl1.setObjectName("infoValMid")
    vl1.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)

    vl2 = QLabel(val2)
    vl2.setObjectName("infoVal")
    vl2.setWordWrap(True)
    vl2.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

    h.addWidget(kl)
    h.addWidget(vl1, ratio[0])
    h.addWidget(vl2, ratio[1])
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
    """0~125 기준 바. 노란색=현재스탯, 회색반투명=한계스탯 위치 표시.
    스페셜리스트의 100 초과 스탯도 막대 길이에 반영되도록 상한을 125로 둔다."""
    BAR_MAX = 125
    def __init__(self, bar_max=None):
        super().__init__()
        self._cur = 0
        self._mx  = 80
        self._cur_color = None  # None이면 비율로 자동 결정
        # 스탯바는 0~125(스페셜리스트 100 초과 반영), 스트레스/행복도 등
        # 0~100이 최대인 값은 bar_max=100을 줘서 100에서 바가 꽉 차게 한다.
        if bar_max is not None:
            self.BAR_MAX = bar_max
        self.setMinimumWidth(60)

    def set_values(self, cur, mx):
        self._cur = max(0, min(self.BAR_MAX, cur))
        self._mx  = max(0, min(self.BAR_MAX, mx))
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QBrush, QColor, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = 3  # border-radius

        # 배경 (0~BAR_MAX)
        p.setBrush(QBrush(QColor("#2a2a2a")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, w, h, r, r)

        # 한계스탯 영역 (반투명 회색)
        mx_w = int(w * self._mx / self.BAR_MAX)
        if mx_w > 0:
            p.setBrush(QBrush(QColor(120, 120, 120, 60)))
            p.drawRoundedRect(0, 0, mx_w, h, r, r)

        # 현재 스탯 바 (색상)
        cur_w = int(w * self._cur / self.BAR_MAX)
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

        # 한계스탯 경계선 (흰색 세로줄) — 반투명 한계바 끝에 정확히 일치시킨다.
        #   (버그수정) 기존엔 /100 으로 그려 BAR_MAX(125) 기준인 반투명바와
        #   스케일이 어긋나 선이 오른쪽으로 밀려 있었다.
        mx_x = int(w * self._mx / self.BAR_MAX)
        if 0 < mx_x < w:
            p.setPen(QPen(QColor(200, 200, 200, 140), 1))
            p.drawLine(mx_x, 0, mx_x, h)

        p.end()

class BodySilhouette(QWidget):
    """부상 부위를 강조해서 보여주는 신체 실루엣 위젯.

    부상 시스템 설계 문서 10~13번 항목 컨셉대로, 해부학 도감이 아니라
    "부상 위치만 알려주는 자연스러운 실루엣"으로 그린다. 별도 SVG 에셋
    파일 없이 코드베이스의 StatBar와 동일하게 QPainter로 직접 그려서
    파일 하나만 옮겨도 바로 동작하게 했다(추후 진짜 삽화로 바꾸고
    싶으면 paintEvent만 QSvgRenderer로 교체하면 됨).

    [2026-08 확장, 신민용 요청] 부상 데이터 풀이 145종(목/어깨/골반/
    허리/손목/발 등)으로 늘어나면서 예전 구조(가슴/배/팔/다리만 존재)로는
    표시가 안 되는 부위가 많아졌다 — 목·어깨를 별도 zone으로 새로 쪼개고,
    허리(back)·골반(pelvis) zone을 추가했고, 다리 끝에 발(foot) zone도
    새로 붙였다(발가락 부상은 요청대로 별도 zone 없이 발에 포함). 손가락/
    손목도 마찬가지로 손(hand) zone 하나로 통합.
    """
    # 디자인 좌표계(위→아래 170×336) 기준 각 부위 사각형: (x, y, w, h, 모서리반지름)
    # [2026-08 수정, 신민용 리포트: "우측 팔이 엉덩이랑 붙어있다"] 팔(손/팔뚝)과
    # 다리(허벅지 이하) 열의 중심이 캔버스 중심(x=85)을 기준으로 좌우 정확히
    # 대칭이 아니었다 — 특히 pelvis가 85가 아니라 88 중심으로 3만큼 밀려있고,
    # 다리 전체(허벅지~발)가 좌측은 중심에서 11만큼, 우측은 19만큼 떨어져
    # 있어(비대칭) 우측 팔뚝/손이 골반·허벅지와 실제로 겹쳤다. 모든 좌우 쌍을
    # 캔버스 중심 기준 정확히 같은 거리로 재배치하고, 인접하지 않은 부위끼리
    # 겹치는 곳이 없는지 좌표 검증까지 마쳤다(팔/다리/골반/어깨 전 쌍 대칭
    # 확인 + 전체 zone 쌍 겹침 0건 확인).
    ZONE_RECTS = {
        "neck":        (78, 42, 14, 10, 3),
        "l_shoulder":  (40, 52, 26, 23, 9),
        "r_shoulder":  (104, 52, 26, 23, 9),
        "chest":       (66, 52, 38, 38, 8),
        "abdomen":     (68, 90, 34, 35, 8),
        "back":        (68, 125, 34, 18, 6),
        "pelvis":      (59, 143, 52, 24, 10),
        "l_upper_arm": (36, 75, 22, 44, 10),
        "l_elbow":     (37, 119, 20, 14, 6),
        "l_forearm":   (38, 133, 18, 44, 8),
        "l_hand":      (35, 177, 24, 24, 10),
        "r_upper_arm": (112, 75, 22, 44, 10),
        "r_elbow":     (113, 119, 20, 14, 6),
        "r_forearm":   (114, 133, 18, 44, 8),
        "r_hand":      (111, 177, 24, 24, 10),
        "l_thigh": (61, 167, 22, 64, 10),
        "r_thigh": (87, 167, 22, 64, 10),
        "l_knee":  (63, 231, 18, 16, 6),
        "r_knee":  (89, 231, 18, 16, 6),
        "l_calf":  (62, 247, 20, 52, 8),
        "r_calf":  (88, 247, 20, 52, 8),
        "l_ankle": (64, 299, 16, 14, 5),
        "r_ankle": (90, 299, 16, 14, 5),
        "l_foot":  (61, 313, 22, 16, 6),
        "r_foot":  (87, 313, 22, 16, 6),
    }
    HEAD_RECT = (68, 6, 34, 36)   # 원(머리) — x,y,w,h
    DESIGN_W, DESIGN_H = 170, 336
    DEFAULT_COLOR = QColor("#3a3a3a")

    def __init__(self):
        super().__init__()
        self.setMinimumSize(95, 190)
        self._zone = None
        self._color = None

    def set_highlight(self, zone: str | None, color):
        """zone=None이면 부상 없음(전부 기본색). zone은 ZONE_RECTS 키거나 'head'."""
        self._zone = zone
        self._color = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        avail_w, avail_h = self.width(), self.height()
        scale = max(0.01, min(avail_w / self.DESIGN_W, avail_h / self.DESIGN_H))
        off_x = (avail_w - self.DESIGN_W * scale) / 2
        off_y = (avail_h - self.DESIGN_H * scale) / 2
        p.translate(off_x, off_y)
        p.scale(scale, scale)
        p.setPen(Qt.PenStyle.NoPen)

        # 머리
        hx, hy, hw, hh = self.HEAD_RECT
        p.setBrush(QBrush(self._color if self._zone == "head" else self.DEFAULT_COLOR))
        p.drawEllipse(hx, hy, hw, hh)

        # 목 / 어깨 / 몸통 / 팔 / 다리
        for zone, (x, y, w, h, r) in self.ZONE_RECTS.items():
            p.setBrush(QBrush(self._color if zone == self._zone else self.DEFAULT_COLOR))
            p.drawRoundedRect(x, y, w, h, r, r)

        # 강조 부위 하이라이트 테두리 (한 번 더 눈에 띄게)
        if self._zone:
            p.setPen(QPen(QColor(255, 255, 255, 170), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            if self._zone == "head":
                p.drawEllipse(hx, hy, hw, hh)
            elif self._zone in self.ZONE_RECTS:
                x, y, w, h, r = self.ZONE_RECTS[self._zone]
                p.drawRoundedRect(x, y, w, h, r, r)

        p.end()