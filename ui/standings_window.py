"""
ui/standings_window.py  ─  모달리스, 실시간 갱신
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from game_engine import get_league_standings, get_player, get_state
from database import get_conn

STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a; border:none; font-size:12px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:4px; }
QTableWidget::item:selected { background:#2a6a2a; }
"""

# [2026-08 신설] 순위 칸을 구역 색으로 채울 때(_fill_table 참고) 그 배경
# 위에서 숫자가 확실히 튀어 보이도록 배경색별 대비 글자색을 지정한다 —
# 밝은 배경(옐로)은 검정, 어둡거나 채도 높은 배경(블루/오렌지/그린/레드)은
# 흰색이 가장 잘 읽힌다(실측 대비 확인).
_ZONE_TEXT_COLOR = {
    "#4466ff": "#ffffff",   # 챔스권 — 블루 배경 + 흰 글자
    "#ff7700": "#000000",   # 유로파권 — 오렌지 배경 + 검정 글자
    "#215131": "#ffffff",   # 컨퍼런스권 — 어두운 그린 배경 + 흰 글자
    "#ffee55": "#000000",   # 승강 PO권 — 옐로 배경 + 검정 글자
    "#ff3333": "#ffffff",   # 강등 확정 — 레드 배경 + 흰 글자
}

class StandingsWindow(QDialog):
    def __init__(self, league_id, my_team_id, lang="ko", parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("리그 순위표")
        self.setMinimumSize(620, 420)
        self.setStyleSheet(STYLE)
        self.league_id   = league_id
        self.my_team_id  = my_team_id
        self.lang        = lang
        self._build()
        # [최적화] main_window.refresh_all()이 부르는 self.refresh()는 항상
        #   그대로 즉시 다시 그린다(기존 동작 100% 유지 — 이적/승강/국가대표
        #   선발 등 즉시 반영돼야 하는 명시적 갱신 경로).
        #   반대로 5초짜리 배경 타이머는 "창을 그냥 열어두고 보고 있는" 동안
        #   불필요하게 테이블을 통째로 부수고 다시 그리는 게 렉의 원인이었다.
        #   순위표는 하루가 실제로 진행되기 전까진 절대 안 바뀌므로, 타이머
        #   폴링에서만 "직전과 조건이 같으면 건너뛰기"를 적용한다 —
        #   사용자가 보는 결과는 항상 기존과 동일하게 유지된다.
        self._last_sig = self._compute_sig()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_refresh)
        self._timer.start(5000)

    def pause_refresh(self):
        """[스레드 안전] 백그라운드 워커가 DB에 쓰는 동안 5초 타이머가
        같은 커넥션에 SELECT를 던지지 않도록 잠시 멈춘다."""
        self._timer.stop()

    def resume_refresh(self):
        self._timer.start(5000)

    def _build(self):
        self._lay = QVBoxLayout(self)
        conn = get_conn()
        row  = conn.execute("SELECT name, tier, country_id FROM leagues WHERE id=?",
                             (self.league_id,)).fetchone()
        conn.close()
        self._lname = f"{row['name']} ({row['tier']}부)" if row else "리그"
        self._tier = row["tier"] if row else None
        self._country_id = row["country_id"] if row else None

        self._lbl = QLabel(f"📊 {self._lname}")
        self._lbl.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        self._lay.addWidget(self._lbl)

        self._tbl_holder = QVBoxLayout()
        self._lay.addLayout(self._tbl_holder)
        self._tbl = None
        self._fill_table()

        btn = QPushButton("닫기"); btn.clicked.connect(self.close)
        btn.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                          "border-radius:4px;padding:6px;")
        self._lay.addWidget(btn)

    def _compute_sig(self, league_id=None, my_team_id=None):
        """순위표가 달라질 수 있는 최소 조건 스냅샷(리그/내팀/진행일자).
        타이머 폴링 전용 — 이 값이 안 바뀌면 하루가 진행되지 않았다는
        뜻이라 순위표 내용은 100% 그대로다."""
        st = get_state()
        return (self.league_id if league_id is None else league_id,
                self.my_team_id if my_team_id is None else my_team_id,
                st.get("current_day") if st else None,
                st.get("current_season") if st else None)

    def _poll_refresh(self):
        """5초 배경 타이머 전용 갱신. refresh()와 같은 저비용 조회(내 팀/리그
        재확인)만 먼저 해보고, 그 결과로 만든 시그니처가 직전과 같으면
        무거운 테이블 재조회·재렌더링을 건너뛴다(성능 최적화). 조건이
        하나라도 바뀌었으면 refresh()를 그대로 호출해 완전히 다시 그린다
        — 즉 사용자가 보는 결과는 항상 기존과 동일하다."""
        p = get_player()
        st = get_state()
        league_id = self.league_id
        my_team_id = self.my_team_id
        if p and p.get("current_team_id"):
            my_team_id = p["current_team_id"]
            # [버그수정 2026-08, 신민용 리포트: "승강전에서 승급/강등되면
            # 바로 반영되는거 같은데, 다음 연도 1주차에 반영돼야 하지
            # 않나 — 경기 일정 다 사라지고 좌측에 정보 없음 뜨더라"]
            # teams.league_id는 43주 승강 처리 즉시 새 리그로 바뀌지만,
            # 44~52주 국제대회 기간엔 이번 시즌 실제 경기가 전부 옛
            # 리그에 남아있다 — _team_league_id_for_season으로 이번
            # 시즌에 실제로 뛴 리그를 먼저 찾고, 없으면(막 이적 직후 등)
            # 지금 소속 리그로 폴백한다.
            from game_engine import _team_league_id_for_season
            conn = get_conn()
            c = conn.cursor()
            season = st.get("current_season") if st else None
            _season_lid = _team_league_id_for_season(c, my_team_id, season) if season else None
            if _season_lid is not None:
                league_id = _season_lid
            else:
                row = c.execute(
                    "SELECT l.id AS lid FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                    (my_team_id,)).fetchone()
                if row:
                    league_id = row["lid"]
            conn.close()
        sig = self._compute_sig(league_id, my_team_id)
        if sig == self._last_sig:
            return
        self.refresh()

    def refresh(self):
        # 내 팀의 '현재' 소속 리그와 팀 ID를 재조회한다.
        #   - 이적: current_team_id 가 바뀜 → my_team_id 갱신(하이라이트 정확)
        #   - 승강: 같은 팀이라도 소속 league_id 가 바뀜 → league_id 갱신
        # [버그수정 2026-08] _poll_refresh와 동일한 이유로, teams.league_id
        # (지금 소속, 승강 반영 후)가 아니라 이번 시즌에 실제로 뛴 리그를
        # 우선 쓴다. 이번 시즌 경기가 아직 없으면(막 이적 직후) 기존처럼
        # teams.league_id로 폴백한다.
        p = get_player()
        st = get_state()
        if p and p.get("current_team_id"):
            tid = p["current_team_id"]
            self.my_team_id = tid
            from game_engine import _team_league_id_for_season
            conn = get_conn()
            c = conn.cursor()
            season = st.get("current_season") if st else None
            _season_lid = _team_league_id_for_season(c, tid, season) if season else None
            if _season_lid is not None:
                row = c.execute(
                    "SELECT name AS lname, tier, country_id FROM leagues WHERE id=?",
                    (_season_lid,)).fetchone()
                if row:
                    self.league_id = _season_lid
                    self._tier = row["tier"]
                    self._country_id = row["country_id"]
                    new_name = f"{row['lname']} ({row['tier']}부)"
                    if new_name != self._lname:
                        self._lname = new_name
                        self._lbl.setText(f"📊 {self._lname}")
            else:
                row = c.execute(
                    "SELECT l.id AS lid, l.name AS lname, l.tier AS tier, l.country_id AS country_id "
                    "FROM teams t JOIN leagues l ON t.league_id=l.id WHERE t.id=?",
                    (tid,)).fetchone()
                if row:
                    self.league_id = row["lid"]
                    self._tier = row["tier"]
                    self._country_id = row["country_id"]
                    new_name = f"{row['lname']} ({row['tier']}부)"
                    if new_name != self._lname:
                        self._lname = new_name
                        self._lbl.setText(f"📊 {self._lname}")
            conn.close()
        self._fill_table()
        self._last_sig = self._compute_sig()

    def _compute_zone_colors(self, rows) -> dict:
        """[2026-08 v3.5 신설, 신민용 요청] 리그 순위표 각 팀의 글자색을
        승격/강등/대륙대항전 진출 구간에 맞춰 정한다 — 상자(배경) 색이
        아니라 글자색만 바꾼다.

        규칙:
          - 1부 리그: 챔스(블루)/유로파(오렌지)/컨퍼런스(연두) 진출 구간
            — center_panel 주간 일정 카드와 같은 색. 국내 순위표에서
            "지금 이대로 시즌이 끝나면 어디 나가는지" 실시간 미리보기다
            (continental_qualification.allocate_continental_slots를 그대로
            재사용 — 실제 대회 배정과 완전히 같은 워터폴 로직).
          - 승격 가능 리그(자기보다 위 부가 있는 리그): 자동 승격(블루) +
            승강 플레이오프권(옐로).
          - 강등 가능 리그(자기보다 아래 부가 있는 리그): 자동 강등(레드) +
            승강 플레이오프권(옐로) — game_engine._get_promotion_policy/
            _get_po_bracket_size를 그대로 재사용해서 실제 승강 로직과
            정확히 같은 인원수를 쓴다.
          - 겹치는 경우(작은 나라라 대륙대항전 슬롯이 강등권까지 파고드는
            드문 케이스): 대륙대항전 색이 항상 우선(더 특수한 상태라서) —
            그 팀의 대륙대항전 진출이 실제로 끝나면(시즌 종료 시점에
            워터폴 배정이 최종 확정되면) 자연히 다음 시즌부턴 강등 표시로
            넘어간다.
        """
        colors: dict = {}
        if not self._tier or not self._country_id:
            return colors
        conn = get_conn()
        c = conn.cursor()

        # 1) 대륙대항전 진출 구간 (1부 리그만)
        # [2026-08 v3.5 재수정, 신민용 확정: "원래대로 다음 년도 챔스 등이
        # 나갈 것을 표시하게" — 즉 실제 프리미어리그 순위표처럼 "지금
        # 이대로 시즌이 끝나면 다음 시즌 챔스/유로파/컨퍼런스에 나갈
        # 팀"을 라이브로 보여주는 게 맞다.] 직전 시즌 최종 순위로
        # 실제 대회 참가팀이 확정된다는 사실 자체는 맞지만(그건 이미
        # 배정되어 지금 뛰고 있는 대회 얘기), 이 순위표가 보여줘야 하는
        # 건 "그 사실"이 아니라 "지금 순위가 이대로 굳으면 다음 시즌엔
        # 누가 나가는가"라는 실시간 예측이다 — 실제 축구 중계에서
        # 순위표에 챔스권을 색칠하는 것과 같은 개념. continental_
        # qualification.allocate_continental_slots를 현재 시즌 순위로
        # 그대로 재사용(실제 대회 배정과 완전히 같은 워터폴 로직 — 나라별
        # 슬롯 수도 champions_engine.get_cl_slots/유로파·컨퍼런스 순위
        # 기반 배정을 그대로 따르므로, 잉글랜드처럼 슬롯이 많은 나라와
        # 튀르키예처럼 적은 나라 차이도 자동으로 반영된다).
        if self._tier == 1:
            try:
                crow = c.execute("SELECT continent FROM countries WHERE id=?",
                                  (self._country_id,)).fetchone()
                if crow and crow["continent"]:
                    from competition.champions_engine import CONTINENT_MAP
                    cont_bucket = CONTINENT_MAP.get(crow["continent"])
                    if cont_bucket:
                        st = get_state()
                        season = st.get("current_season") if st else None
                        year = st.get("current_year") if st else None
                        if season and year:
                            from competition.continental_qualification import (
                                allocate_continental_slots)
                            alloc = allocate_continental_slots(cont_bucket, season, year)
                            _CONT_COLOR = {"champions": "#4466ff", "europa": "#ff7700",
                                           "conference": "#215131"}
                            for comp_name, color in _CONT_COLOR.items():
                                for entry in alloc.get(comp_name, []):
                                    colors[entry["team_id"]] = color
            except Exception:
                pass

        from game_engine import _get_promotion_policy, _get_po_bracket_size
        team_count = len(rows)

        # 2) 승격 구간(위 부가 존재할 때만)
        upper = c.execute("SELECT id FROM leagues WHERE country_id=? AND tier=?",
                           (self._country_id, self._tier - 1)).fetchone() if self._tier else None
        if upper:
            upper_count = c.execute("SELECT COUNT(*) FROM teams WHERE league_id=?",
                                     (upper["id"],)).fetchone()[0]
            policy = _get_promotion_policy(upper_count)
            auto_n = policy["auto"]
            po_n = _get_po_bracket_size(team_count) if policy["po"] else 0
            for i, r in enumerate(rows, start=1):
                if i <= auto_n:
                    colors.setdefault(r["id"], "#4466ff")   # 승격 확정 — 블루
                elif i <= auto_n + po_n:
                    colors.setdefault(r["id"], "#ffee55")   # 승강 PO권 — 옐로

        # 3) 강등 구간(아래 부가 존재할 때만)
        lower = c.execute("SELECT id FROM leagues WHERE country_id=? AND tier=?",
                           (self._country_id, self._tier + 1)).fetchone()
        if lower:
            policy = _get_promotion_policy(team_count)   # 이 리그가 "위 리그" 기준
            auto_n = policy["auto"]
            # [2026-08 v3.5 버그수정, 신민용 리포트: "강등은 확정강등 위에
            # 한 팀만 PO에 참여하는데 왜 4팀이 노란색이야?"] 정확한
            # 지적 — _get_promotion_policy 설계 자체가 "PO는 항상 4팀
            # 브래킷이 마지막 생존권 1장을 놓고 경쟁하지만, 상위 리그는
            # 항상 1팀만 위태로움"이다. 4팀 브래킷은 전부 "아래" 리그
            # 쪽 몫(_get_po_bracket_size)이고, 이 리그(위 리그 역할)는
            # policy["po"] 그대로(0 또는 1)가 PO권 인원이다 — 승격 쪽
            # (아래 리그 자기 자신을 보는 코드, 위 2번 블록)과 혼동해서
            # 여기도 _get_po_bracket_size를 잘못 썼던 게 원인.
            po_n = policy["po"]
            n = team_count
            for i, r in enumerate(rows, start=1):
                if i > n - auto_n:
                    colors.setdefault(r["id"], "#ff3333")   # 강등 확정 — 레드
                elif i > n - auto_n - po_n:
                    colors.setdefault(r["id"], "#ffee55")   # 승강 PO권 — 옐로

        conn.close()
        return colors

    def _fill_table(self):
        # [2026-08 계측 추가, 신민용 리포트: "순위표 버튼도 혹시 모르고"]
        # 구조가 단순해서 크게 안 느릴 걸로 예상되지만 확인 차원에서 추가.
        import time as _time_st
        _st_t0 = _time_st.perf_counter()

        if self._tbl:
            self._tbl_holder.removeWidget(self._tbl)
            self._tbl.deleteLater()

        rows = get_league_standings(self.league_id)
        _st_t1 = _time_st.perf_counter()
        zone_colors = self._compute_zone_colors(rows)
        cols = ["순위","팀명","승","무","패","득점","실점","득실","승점"]
        tbl  = QTableWidget(len(rows), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        # 팀명만 늘어나고 나머지는 내용에 맞게 고정
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tbl.setStyleSheet("""
            QTableWidget{background:#1e1e1e;color:#ccc;gridline-color:#2a2a2a;border:none;}
            QHeaderView::section{background:#252525;color:#888;border:none;padding:4px;}
        """)

        for i, r in enumerate(rows):
            vals = [str(i+1), r["name"], str(r["wins"]), str(r["draws"]), str(r["losses"]),
                    str(r["goals_for"]), str(r["goals_against"]),
                    str(r["goals_for"]-r["goals_against"]), str(r["pts"])]
            _zone_color = zone_colors.get(r["id"])
            # [2026-08 신설, 신민용 요청: "챔스/강등 등 구역 표시가 팀명까지
            # 통째로 물들여서 가독성이 떨어진다 — 순위 칸만 색 상자로
            # 채우고, 내 팀 형광 표시는 그 순위 칸에는 적용하지 말아 달라"]
            # 예전엔 이 구역 색을 행 전체(모든 칸)의 글자색으로 칠했고, 내 팀
            # 강조(연두 형광)도 행 전체(배경+글자색)에 걸었다 — 그래서 내 팀이
            # 승격/강등권에 걸리면 두 표시가 뒤섞여 어느 쪽인지 알아보기
            # 힘들었다. 이제 역할을 분리한다: 순위(0번 칸)는 구역 색이 있으면
            # "배경을 그 색으로 채우고 글자는 그 배경 위에서 튀는 색"으로
            # 표시하는 전용 배지가 되고(내 팀이어도 이 칸은 형광 대신 항상
            # 구역 색 배지를 우선한다), 나머지 칸(팀명 포함)은 구역 색을
            # 더 이상 입히지 않고 내 팀 형광(연두 글자+어두운 연두 배경)만
            # 그대로 적용한다.
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if j == 0 and _zone_color:
                    item.setBackground(QColor(_zone_color))
                    item.setForeground(QColor(_ZONE_TEXT_COLOR.get(_zone_color, "#ffffff")))
                elif j != 0 and r["id"] == self.my_team_id:
                    item.setBackground(QColor("#1a3a1a"))
                    item.setForeground(QColor("#00ff66"))
                tbl.setItem(i, j, item)

        self._tbl = tbl
        self._tbl_holder.addWidget(tbl)

        # 창 너비를 테이블 전체 너비에 맞게 자동 조정
        tbl.resizeColumnsToContents()
        total_w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount()))
        total_w += tbl.verticalHeader().width() + 40  # 여백
        self.setMinimumWidth(max(620, total_w))
        self.resize(max(self.width(), total_w), self.height())
        _st_t2 = _time_st.perf_counter()
        _st_total = _st_t2 - _st_t0
        if _st_total >= 0.05:
            print(f"[PERF-STAND] _fill_table 총 {_st_total:.3f}s — "
                  f"get_league_standings {_st_t1-_st_t0:.3f}s | "
                  f"테이블렌더링 {_st_t2-_st_t1:.3f}s ({len(rows)}팀)")