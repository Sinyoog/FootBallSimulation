"""
ui/apply_window.py — [2026-07 신설] 직접 지원 (팀 검색 후 지원하기)

패시브 오퍼(오퍼/입단 창에서 자동으로 뜨는 목록)와 별개로, 플레이어가 직접
팀을 검색해서 골라 지원하는 능동적 채널. 무소속(첫 입단/계약종료·방출 후)
기간에만 열 수 있고, 시도 횟수가 제한된다(game_engine.DIRECT_APPLY_MAX).

성공하면 game_engine.apply_to_team()이 만든 오퍼 dict를 self.chosen에 담아
다이얼로그를 accept()로 닫는다 — 호출부(center_panel._do_join 등)는 기존
OfferWindow와 동일하게 `dlg.chosen`을 보고 join_team()을 호출하면 된다.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

import world_browser as wb
from game_engine import (
    get_apply_attempts_left, get_apply_player_context, calc_apply_prob_with_context,
    apply_to_team, DIRECT_APPLY_MAX,
)
from ui.center_panel import show_toast

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QLineEdit, QComboBox { background:#2a2a2a; color:#eee; border:1px solid #444;
                        border-radius:4px; padding:4px 6px; }
QTableWidget { background:#232323; color:#ddd; gridline-color:#3a3a3a;
               border:1px solid #333; }
QHeaderView::section { background:#2a2a2a; color:#aaa; border:none; padding:4px; }
#applyBtn { background:#2a6a2a; color:white; border:none; border-radius:4px;
            padding:8px 18px; font-size:13px; font-weight:bold; }
#applyBtn:hover { background:#3a8a3a; }
#applyBtn:disabled { background:#333; color:#555; }
#attemptsLbl { color:#ffcc00; font-size:13px; font-weight:bold; }
#hintLbl { color:#888; font-size:11px; }
"""

# 확률 구간별 표시 라벨/색상.
_PROB_BANDS = [
    (0.70, "🟢 유력", "#3ad13a"),
    (0.40, "🟡 가능성 있음", "#e0c030"),
    (0.15, "🟠 쉽지 않음", "#e08030"),
    (0.0,  "🔴 거의 불가능", "#e04040"),
]


def _prob_label(prob: float, blocked: bool):
    if blocked:
        return "⛔ 재능 부족 (불가)", "#888888"
    for th, label, color in _PROB_BANDS:
        if prob >= th:
            return label, color
    return _PROB_BANDS[-1][1], _PROB_BANDS[-1][2]


class ApplyWindow(QDialog):
    def __init__(self, lang="ko", parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("🔎 직접 지원")
        self.setMinimumSize(720, 520)
        self.setStyleSheet(STYLE)
        self.lang = lang
        self.chosen = None
        self._rows = []          # 현재 검색 결과 (search_teams 반환값)
        self._selected_team_id = None
        self._build()
        self._do_search()

    def _build(self):
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.attempts_lbl = QLabel()
        self.attempts_lbl.setObjectName("attemptsLbl")
        top.addWidget(self.attempts_lbl)
        top.addStretch()
        root.addLayout(top)

        hint = QLabel("팀을 검색해서 원하는 곳에 직접 지원해보세요. "
                      "무리한 도전일수록 성공 확률이 낮아지지만 완전히 불가능하진 않습니다.")
        hint.setObjectName("hintLbl")
        hint.setWordWrap(True)
        root.addWidget(hint)

        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("팀명 · 리그명 · 국가명 검색")
        self.search_edit.textChanged.connect(self._do_search)
        search_row.addWidget(self.search_edit, 3)

        self.grade_combo = QComboBox()
        self.grade_combo.addItem("전체 등급", None)
        for g in ["SS", "S", "A", "B", "C", "D", "E", "F"]:
            self.grade_combo.addItem(g, g)
        self.grade_combo.currentIndexChanged.connect(self._on_grade_changed)
        search_row.addWidget(self.grade_combo, 1)

        # [2026-07 추가] 국가 필터 — 등급 하나 골라도 그 등급 나라가 여러 개
        # 나와서(예: S급이면 스페인·독일·프랑스·이탈리아·브라질) 특정 나라
        # 하나로 더 좁히고 싶을 때를 위함. 등급을 바꾸면 그 등급에 속한
        # 나라만 이 드롭다운에 다시 채워진다(전체 200여 개국을 매번 다
        # 보여주면 오히려 찾기 불편해서).
        self.country_combo = QComboBox()
        self._refresh_country_options(grade=None)
        self.country_combo.currentIndexChanged.connect(self._do_search)
        search_row.addWidget(self.country_combo, 1)

        # [2026-07 추가] 부수(tier) 필터 — 예전엔 검색어 없이 훑어보면 1부
        # 팀만 잔뜩 보이고 하위리그는 사실상 안 보였다(1부 팀 수가 워낙
        # 많아 LIMIT을 다 채워버림). 현실엔 3부·5부에 직접 지원하는 경우도
        # 흔하니, 원하는 부수를 콕 집어 볼 수 있게 한다.
        self.tier_combo = QComboBox()
        self.tier_combo.addItem("전체 부", None)
        for t in range(1, 7):
            self.tier_combo.addItem(f"{t}부", t)
        self.tier_combo.currentIndexChanged.connect(self._do_search)
        search_row.addWidget(self.tier_combo, 1)
        root.addLayout(search_row)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["국가", "팀", "부", "등급", "평균OVR", "성공 가능성"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_select)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.detail_lbl = QLabel("팀을 선택하세요")
        bottom.addWidget(self.detail_lbl, 1)
        self.apply_btn = QPushButton("🔎 이 팀에 지원하기")
        self.apply_btn.setObjectName("applyBtn")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._do_apply)
        bottom.addWidget(self.apply_btn)
        root.addLayout(bottom)

        close_row = QHBoxLayout()
        close_row.addStretch()
        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.reject)
        close_row.addWidget(self.close_btn)
        root.addLayout(close_row)

        self._refresh_attempts()

    def _refresh_country_options(self, grade):
        """등급이 바뀔 때마다 그 등급에 속한 나라만 국가 드롭다운에 다시 채운다.
        직전에 골라둔 나라가 새 목록에도 있으면 선택을 유지한다."""
        prev_id = self.country_combo.currentData() if self.country_combo.count() else None
        self.country_combo.blockSignals(True)
        self.country_combo.clear()
        self.country_combo.addItem("전체 국가", None)
        for c in wb.list_countries(grade=grade):
            self.country_combo.addItem(f"{c.get('flag','')} {c['name']}", c["id"])
        idx = self.country_combo.findData(prev_id)
        self.country_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.country_combo.blockSignals(False)

    def _on_grade_changed(self, *_):
        self._refresh_country_options(grade=self.grade_combo.currentData())
        self._do_search()

    def _refresh_attempts(self):
        left = get_apply_attempts_left()
        self.attempts_lbl.setText(f"남은 직접 지원 횟수: {left} / {DIRECT_APPLY_MAX}")
        if left <= 0:
            self.apply_btn.setEnabled(False)
            self.search_edit.setEnabled(False)
            self.grade_combo.setEnabled(False)

    def _do_search(self, *_):
        name_query = self.search_edit.text().strip() or None
        grade = self.grade_combo.currentData()
        country_id = self.country_combo.currentData()
        tier = self.tier_combo.currentData()
        self._rows = wb.search_teams(name_query=name_query, grade=grade, country_id=country_id,
                                      tier=tier, limit=30)
        self._populate_table()

    def _populate_table(self):
        self.table.setRowCount(len(self._rows))
        ctx = get_apply_player_context()
        for i, r in enumerate(self._rows):
            avg_ovr = r.get("avg_ovr") or 0
            prob, blocked = calc_apply_prob_with_context(r["id"], ctx)
            label, color = _prob_label(prob, blocked)
            vals = [
                f"{r.get('flag','')} {r['country']}", r["name"],
                f"{r['tier']}부", r["grade"],
                f"{avg_ovr:.1f}", label,
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 5:
                    item.setForeground(QColor(color))
                item.setData(Qt.ItemDataRole.UserRole, r["id"])
                self.table.setItem(i, j, item)
        self.table.clearSelection()
        self._selected_team_id = None
        self.detail_lbl.setText("팀을 선택하세요")
        self.apply_btn.setEnabled(False)

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._selected_team_id = None
            self.apply_btn.setEnabled(False)
            return
        idx = rows[0].row()
        r = self._rows[idx]
        self._selected_team_id = r["id"]
        ctx = get_apply_player_context()
        prob, blocked = calc_apply_prob_with_context(r["id"], ctx)
        label, _ = _prob_label(prob, blocked)
        self.detail_lbl.setText(f"{r['name']} ({r['country']}, {r['grade']}급 {r['tier']}부) "
                                 f"— 예상 성공 가능성: {label} (약 {prob*100:.0f}%)")
        self.apply_btn.setEnabled(get_apply_attempts_left() > 0)

    def _do_apply(self):
        if self._selected_team_id is None:
            return
        team_name = next((r["name"] for r in self._rows if r["id"] == self._selected_team_id), "그 팀")
        success, prob, offer = apply_to_team(self._selected_team_id)
        self._refresh_attempts()

        if success and offer:
            self.chosen = offer
            show_toast(self, f"🎉 {team_name} 지원 성공! 협상을 진행합니다.", "#3ad13a", 2000)
            self.accept()
            return

        left = get_apply_attempts_left()
        if left <= 0:
            # [2026-07 수정] 클릭해서 닫아야 하는 QMessageBox 대신, 다른 토스트처럼
            # 0.5초만 표시되고 자동으로 사라지는 짧은 알림으로 교체(신민용 요청).
            show_toast(self, f"😢 {team_name} 지원 결렬 — 직접 지원 시도를 모두 사용했습니다",
                       "#cc4400", 500)
            QTimer.singleShot(500, self.reject)
        else:
            show_toast(self, f"😢 {team_name} 지원 결렬 (성공확률 {prob*100:.0f}%였습니다)",
                       "#e04040", 2200)
            # 같은 팀 재지원은 의미 없으니(결과 이미 소비) 다시 선택하도록 유도.
            self.table.clearSelection()
            self._selected_team_id = None
            self.apply_btn.setEnabled(False)
            self._populate_table()