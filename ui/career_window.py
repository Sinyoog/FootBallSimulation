"""
ui/career_window.py
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QTabWidget, QWidget, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from game_engine import get_player, fmt_money, get_my_promotions, get_state
from database import get_conn
from constants import format_result_with_absence


# 개인 수상으로 분류할 키워드 (trophy_log에 섞여 들어온 발롱도르·MVP 행 식별)
_PERSONAL_AWARD_KEYWORDS = (
    "발롱도르", "MVP", "득점왕", "도움왕", "베스트11",
    "골든글러브", "영플레이어", "신데렐라", "푸스카스", "사모라",
)


def _is_personal_award(trophy):
    """trophy_log 한 행이 '개인 수상'인지 판별.

    리그/국제대회 우승은 competition이 '... 우승' / '... 32강 탈락' 형태이고,
    개인 수상은 'MVP (...)' / '발롱도르 (...)' 형태로 적재된다.
    competition 문자열에 개인 수상 키워드가 들어 있으면 개인 수상으로 본다.
    """
    comp = (trophy.get("competition") or "")
    return any(k in comp for k in _PERSONAL_AWARD_KEYWORDS)


STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QTabWidget::pane { border:1px solid #333; background:#1e1e1e; }
QTabBar::tab { background:#252525; color:#888; padding:6px 16px; }
QTabBar::tab:selected { background:#1e1e1e; color:#00cc44; border-bottom:2px solid #00cc44; }
QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a; border:none; font-size:12px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:4px; }
QTableWidget::item { padding: 4px 8px; }
"""

class CareerWindow(QDialog):
    def __init__(self, lang="ko", parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("커리어 기록")
        self.setMinimumHeight(500)
        self.setStyleSheet(STYLE)
        self.lang = lang
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        p = get_player()
        if not p:
            root.addWidget(QLabel("선수 데이터 없음")); return

        hdr = QLabel(f"📋 {p['name']} 커리어 기록")
        hdr.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
        root.addWidget(hdr)

        from intl_engine import fmt_nationalities, fmt_rep_nationality
        _nats = fmt_nationalities(p) or f"{p.get('flag','')}{p.get('nationality','')}"
        _rep  = fmt_rep_nationality(p)
        nat_lbl = QLabel(f"🌍 국적: {_nats}    ⚽ 대표: {_rep}")
        nat_lbl.setStyleSheet("color:#aaa;font-size:12px;")
        root.addWidget(nat_lbl)

        summary = QHBoxLayout()
        from constants import position_group
        _grp = position_group(p.get("position",""))
        if _grp == "GK":
            stat2_k, stat2_v = "총 선방", f"{p.get('total_saves',0)}선방"
            stat3_k, stat3_v = "총 실점", f"{p.get('total_goals_against',0)}실점"
        elif _grp == "DEF":
            # 수비수: 무실점 경기 수를 핵심 지표로 (커리어 항목에서 합산)
            try:
                _cs = sum(e.get("clean_sheets",0) for e in
                          [dict(r) for r in get_conn().execute(
                              "SELECT clean_sheets FROM career_entries").fetchall()])
            except Exception:
                _cs = 0
            stat2_k, stat2_v = "무실점", f"{_cs}경기"
            stat3_k, stat3_v = "공격P", f"{p.get('total_goals',0)}골 {p.get('total_assists',0)}A"
        else:
            stat2_k, stat2_v = "총 골", f"{p.get('total_goals',0)}골"
            stat3_k, stat3_v = "총 어시", f"{p.get('total_assists',0)}A"
        for k, v in [("총 출전", f"{p.get('total_matches',0)}경기"),
                     (stat2_k, stat2_v),
                     (stat3_k, stat3_v),
                     ("총 시즌", f"{p.get('total_seasons',0)}시즌"),
                     ("총 자산", fmt_money(p.get('total_assets',0))),
                     ("누적 수입", fmt_money(p.get('total_earnings',0)))]:  # 이슈10
            box = QFrame(); bl = QVBoxLayout(box); bl.setContentsMargins(12,8,12,8)
            kl = QLabel(k); kl.setStyleSheet("color:#888;font-size:11px;")
            vl = QLabel(v); vl.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
            bl.addWidget(kl); bl.addWidget(vl)
            box.setStyleSheet("background:#252525;border-radius:6px;")
            summary.addWidget(box)
        root.addLayout(summary)

        conn = get_conn(); c = conn.cursor()
        entries  = [dict(r) for r in c.execute("SELECT * FROM career_entries ORDER BY id").fetchall()]
        # trophy_log에는 리그/국제대회 우승뿐 아니라 발롱도르·MVP 같은 개인 수상도
        # 함께 적재된다. 우승 탭에는 '진짜 우승'만 보여야 하므로 개인 수상 행은 제외한다.
        # (개인 수상은 아래 awards 테이블 기반으로 '개인 수상' 탭에서 따로 표시됨)
        # [2026-07 버그+성능 수정] trophy_log를 필터 없이 통째로 읽으면 전 세계
        # 모든 AI 팀의 우승까지 섞여 보이고(기능 버그), 연차가 쌓일수록 창을
        # 여는 속도도 느려진다(log_panel의 game_log와 같은 유형의 문제).
        # get_my_trophies()가 내 재직 기간 기준으로 미리 걸러서 반환한다.
        from game_engine import get_my_trophies
        all_trophies = get_my_trophies()
        trophies = [t for t in all_trophies if not _is_personal_award(t)]
        try:
            awards = [dict(r) for r in c.execute(
                "SELECT * FROM awards WHERE is_mine=1 ORDER BY year").fetchall()]
        except Exception:
            awards = []
        # 내가 그 팀에 실제로 있던 기간의 승강 기록 (공용 헬퍼)
        promos = get_my_promotions()
        conn.close()

        tabs = QTabWidget()
        tabs.addTab(self._team_tab(entries),  "팀 이력")
        tabs.addTab(self._club_totals_tab(entries), "전체 이력")
        tabs.addTab(self._trophy_tab(trophies), f"성적 ({len(trophies)})")
        tabs.addTab(self._award_tab(awards), f"개인 수상 ({len(awards)})")
        tabs.addTab(self._promo_tab(promos),  f"승강 ({len(promos)})")

        import intl_engine
        intl_ms = intl_engine.get_my_intl_matches()
        tabs.addTab(self._intl_tab(intl_ms, p), f"국제전 ({len(intl_ms)})")

        qual_ms = intl_engine.get_my_qual_matches()
        if qual_ms:
            tabs.addTab(self._intl_tab(qual_ms, p), f"국제전(예선) ({len(qual_ms)})")

        import champions_engine
        cl_ms = champions_engine.get_my_cl_matches()
        tabs.addTab(self._champions_tab(cl_ms, p), f"챔피언스 ({len(cl_ms)})")

        import cup_engine
        cup_ms = cup_engine.get_my_cup_matches()
        tabs.addTab(self._cup_tab(cup_ms), f"컵대회 ({len(cup_ms)})")

        import club_world_cup_engine
        cwc_ms = club_world_cup_engine.get_my_cwc_matches()
        if cwc_ms:
            tabs.addTab(self._cwc_tab(cwc_ms), f"클럽 월드컵 ({len(cwc_ms)})")
        root.addWidget(tabs)
        tabs.currentChanged.connect(lambda: self._fit_width())

        btn = QPushButton("닫기")
        btn.setStyleSheet("background:#2a2a2a;color:#ccc;border:1px solid #444;"
                          "border-radius:4px;padding:6px;")
        btn.clicked.connect(self.close)
        root.addWidget(btn)
        self._tabs = tabs
        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_width()

    def _fit_width(self):
        w = self._tabs.currentWidget()
        if not w: return
        tbls = w.findChildren(QTableWidget)
        if not tbls: return
        tbl = tbls[0]
        total_w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount())) + 60
        self.resize(max(700, min(1600, total_w)), self.height())

    def _make_table(self, rows, cols):
        tbl = QTableWidget(rows, len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        for i in range(len(cols)):
            tbl.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        return tbl

    def _set(self, tbl, r, c, v, color=None):
        item = QTableWidgetItem(str(v))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color: item.setForeground(QColor(color))
        tbl.setItem(r, c, item)

    def _team_tab(self, entries):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not entries:
            lay.addWidget(QLabel("기록 없음")); return w

        # league_name → 국가 flag 매핑 (팀명보다 리그명이 더 안정적)
        conn = get_conn()
        c = conn.cursor()
        league_country = {}
        for e in entries:
            ln = e.get("league_name","")
            if ln and ln not in league_country:
                row = c.execute("""SELECT cn.flag, cn.name as cname
                                   FROM leagues l JOIN countries cn ON l.country_id=cn.id
                                   WHERE l.name=? LIMIT 1""", (ln,)).fetchone()
                league_country[ln] = f"{row['flag']} {row['cname']}" if row else ""
        conn.close()

        # 선수의 현재 포지션 그룹에 맞춰 '지표 칸'을 다르게 구성한다.
        #   공통: 기간/포지션/국가/리그/팀명/연봉/출전 ... 평균평점/팀순위/승무패/계약/이적
        #   중간 지표 칸만 그룹별로 교체.
        from constants import position_group
        _mypos = get_player().get("position", "")
        _grp = position_group(_mypos)
        if _grp == "GK":
            stat_cols = ["골","어시","선방","실점","선방률","CS"]
        elif _grp == "DEF":
            stat_cols = ["골","어시","무실점","차단","패스%","평점기여"]
        elif _mypos in ("CM","CDM","CAM"):
            stat_cols = ["골","어시","기회창출","패스%","차단","드리블"]
        else:  # 공격수/윙어
            stat_cols = ["골","어시","슈팅","유효","기회창출","드리블"]
        cols = (["기간","포지션","국가","리그","팀명","연봉","출전"]
                + stat_cols
                + ["평균평점","팀순위","승무패","계약","이적"])

        # 이슈3: 1~4주차 이적 노이즈만 숨김 (4주 이하 머문 0경기 항목)
        # 여름 이적시장(37주~) 입단처럼 경기 없이 보낸 정상 재직 기간은 표시
        def _is_empty_short(e):
            if e.get("end_year", 0) == 0:  return False  # 현재 팀
            if e.get("matches", 0) != 0:   return False
            sy = e.get("start_year", 0); ey = e.get("end_year", 0)
            sw = e.get("start_week", 1); ew = e.get("end_week", 0)
            return sy == ey and (ew - sw) <= 4

        visible = [e for e in entries if not _is_empty_short(e)]
        tbl = self._make_table(len(visible), cols)
        prev_team = None
        for i, e in enumerate(visible):
            rc  = e.get("season_rating_cnt", 0)
            rs  = e.get("season_rating_sum", 0) or e.get("avg_rating", 0)
            avg = round(rs/rc, 1) if rc > 0 else (round(float(rs), 1) if rs else "—")
            # 출전이 없으면(여름 이적시장 입단 등) 팀 순위·승무패는 그 선수의
            # 성적이 아니므로 — 로 표시 (안 뛴 경기의 팀 기록을 본인 기록처럼
            # 보여주지 않도록)
            if e.get("matches", 0) > 0:
                wdl       = f"{e.get('wins',0)}승{e.get('draws',0)}무{e.get('losses',0)}패"
                # [2026-07 신설, 신민용 요청] "12위" 대신 "12위/18팀"으로.
                from game_engine import league_total_teams_by_name
                _total_teams = league_total_teams_by_name(e.get("league_name", ""))
                rank_disp = (f"{e.get('team_rank',0)}위/{_total_teams}팀" if _total_teams
                             else f"{e.get('team_rank',0)}위")
            else:
                wdl       = "—"
                rank_disp = "—"

            sy = e.get("start_year", ""); sw = e.get("start_week", 1)
            ey = e.get("end_year", 0);    ew = e.get("end_week", 0)

            from constants import week_to_iso_date_str
            start_str = week_to_iso_date_str(sy, sw) if sy else ""
            if ey == 0:
                period = f"{start_str} ~ 현재"
            else:
                # 실제 종료 주차를 그대로 표시. (예전엔 37주 이상이면 무조건 52로
                # 뭉개서, 44주에 이적해도 '52주'로 잘못 보였다.) 50주 이상만
                # 시즌 끝까지 채운 것으로 보고 52로 정리.
                ew_disp = 52 if ew >= 50 else ew
                end_str = week_to_iso_date_str(ey, ew_disp)
                period = f"{start_str} ~ {end_str}"

            pos   = e.get("position","")
            sv  = e.get("saves", 0)
            ga  = e.get("goals_against", 0)
            total_shots = sv + ga
            save_rate = f"{round(sv/total_shots*100,1)}%" if total_shots > 0 else "—"
            _pac = e.get("pass_acc", 0)
            pac_str = f"{round(_pac*100)}%" if _pac else "—"

            # 테이블 컬럼 세트(stat_cols)에 맞춰 각 지표 칸 값을 매핑.
            # 그 행 선수가 안 하는 지표는 "—".
            _val_map = {
                "골":      str(e.get("goals", 0)),
                "어시":    str(e.get("assists", 0)),
                "선방":    str(sv) if pos == "GK" else "—",
                "실점":    str(ga) if pos == "GK" else "—",
                "선방률":  save_rate if pos == "GK" else "—",
                "CS":      str(e.get("clean_sheets", 0)),
                "무실점":  str(e.get("clean_sheets", 0)),
                "차단":    str(e.get("blocks", 0)),
                "패스%":   pac_str,
                "평점기여": str(round(e.get("avg_rating", 0), 1)) if e.get("avg_rating") else "—",
                "기회창출": str(e.get("key_passes", 0)),
                "드리블":  str(e.get("dribbles", 0)),
                "슈팅":    str(e.get("shots", 0)),
                "유효":    str(e.get("shots_on", 0)),
            }
            stat_vals = [_val_map.get(sc, "—") for sc in stat_cols]

            tn = e.get("team_name","")
            ln = e.get("league_name","")
            country_str = league_country.get(ln, "")
            league_str  = f"{ln} ({e.get('tier','')}부)"

            c_yrs  = e.get("contract_years", 0)
            in_type  = e.get("transfer_type", "입단")   # 들어온 경로
            exit_t   = e.get("exit_type", "")            # 나간 경로
            # 이적란: 나간 경로가 있으면 그걸 우선 표시(팔림/방출/이적/계약만료),
            # 없으면(재직 중이거나 정상) 들어온 경로 표시
            t_type = exit_t if exit_t else in_type
            # [2026-07 재수정, 신민용 지적: "임대(2003)이 아니라 임대(임대기간)로,
            # 계약엔 임대보낸 팀 계약 기간을 그대로 가져가는 게 맞다"]
            # 계약 컬럼은 다시 원소속팀 계약년수(c_yrs)를 그대로 보여주고
            # (임대는 새 계약이 아니라 원소속팀 계약을 유지한 채 팀만 옮기는
            # 것이므로), 대신 "이적" 컬럼 쪽에 실제 임대 기간(개월/년)을
            # "임대(N개월)"처럼 표기한다.
            if t_type in ("임대", "임대 종료"):
                _label = t_type
                if ey:
                    total_weeks = max(1, (ey - sy) * 52 + (ew - sw))
                    months = max(1, round(total_weeks / 4.33))
                    if months >= 12:
                        _yrs, _rem = divmod(months, 12)
                        dur = f"{_yrs}년" if _rem == 0 else f"{_yrs}년 {_rem}개월"
                    else:
                        dur = f"{months}개월"
                else:
                    dur = "진행중"
                t_type = f"{_label}({dur})"
            cur_team = e.get("team_name", "")

            # 계약 컬럼: 팀 변경 또는 연장 시에만 년수 표시
            # [2026-07 신설, 신민용 지적: "커리어 UI에도 임대 처리했나"]
            # 임대는 원소속팀 계약(연봉·계약년수·contract_end_year)을 그대로
            # 유지한 채 팀만 임시로 옮기는 것이므로, 계약 컬럼엔 다른 팀
            # 이적/입단과 동일하게 원소속팀 계약 년수를 그대로 보여준다
            # (신민용 지적: "계약에는 임대보낸 팀 계약 기간을 그대로 가는
            # 게 맞다" — 임대처와 새로 계약을 맺은 것처럼 보이지 않도록
            # 별도 문구로 덮어쓰지 않는다).
            if in_type == "임대" or i == 0 or cur_team != entries[i-1].get("team_name"):
                # 임대, 또는 팀이 바뀌었거나 첫 행 → 계약년수 표시
                # (임대는 원소속팀 계약을 그대로 유지하므로 동일하게 취급)
                c_str = f"{c_yrs}년" if c_yrs else "—"
                prev_team = cur_team
            elif in_type == "연장" or t_type == "연장":
                # 같은 팀에서 연장 (연장 년수 표시)
                c_str = f"{c_yrs}년" if c_yrs else "—"
            else:
                # 같은 팀 계속 (대시)
                c_str = "—"
            # [2026-07 추가] 리그마다 팀 수·다전제가 달라 풀시즌 경기 수가
            # 14~58경기로 다 다르다 — "출전 26"만 보면 시즌을 거의 다 뛴
            # 건지 절반만 뛴 건지 알 수 없어서, 그 리그의 풀시즌 경기 수를
            # 분모로 같이 보여준다("26/38"). 못 찾으면(리그명 매칭 실패 등)
            # 그냥 숫자만 표시.
            from game_engine import team_matches_played_in_window
            _total_g = team_matches_played_in_window(tn, ln, sy, sw, ey, ew)
            _apps_str = f"{e.get('matches',0)}/{_total_g}" if _total_g else str(e.get("matches", 0))
            vals = ([period, pos, country_str, league_str, tn,
                     fmt_money(e.get("salary",0)),
                     _apps_str]
                    + stat_vals
                    + [str(avg), rank_disp, wdl, c_str, t_type])
            # 팔림/방출/계약만료는 빨간색 강조
            tt_color = "#cc4444" if t_type in ("팔림", "방출", "계약만료") else None
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, tt_color if j == len(vals)-1 else None)
        lay.addWidget(tbl)
        return w

    def _club_totals_tab(self, entries):
        """[2026-07 신설 → 재수정 → 재작성 → 재확장, 신민용 지적/요청]
        팀 이력과 동일한 포지션별 상세 컬럼 구조로 리그 외(컵+챔스+
        클럽월드컵+국가대표) 기록까지 합산해서 보여준다. 처음엔 슈팅/
        드리블 등 세부 스탯이 컵대회·클럽월드컵엔 저장 안 된다고 판단했지만
        (챔스/국제전은 이미 저장하고 있었음), cup_matches/cwc_matches에
        컬럼을 추가하고 cup_engine.py/club_world_cup_engine.py가 이미
        _player_perf로 계산해두고 버리던 detail을 저장하도록 고쳐서
        이제 4개 대회 전부 실제 값으로 합산된다."""
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0)
        if not entries:
            lay.addWidget(QLabel("기록 없음")); return w

        def _is_empty_short(e):
            if e.get("end_year", 0) == 0:  return False
            if e.get("matches", 0) != 0:   return False
            sy = e.get("start_year", 0); ey = e.get("end_year", 0)
            sw = e.get("start_week", 1); ew = e.get("end_week", 0)
            return sy == ey and (ew - sw) <= 4

        from constants import position_group
        _mypos = get_player().get("position", "")
        _grp = position_group(_mypos)
        if _grp == "GK":
            stat_cols = ["골", "어시", "선방", "실점", "선방률", "CS"]
        elif _grp == "DEF":
            stat_cols = ["골", "어시", "무실점", "차단", "패스%", "평점기여"]
        elif _mypos in ("CM", "CDM", "CAM"):
            stat_cols = ["골", "어시", "기회창출", "패스%", "차단", "드리블"]
        else:
            stat_cols = ["골", "어시", "슈팅", "유효", "기회창출", "드리블"]
        cols = ["기간", "팀명", "리그", "출전"] + stat_cols + ["평균평점", "승무패"]

        visible = [e for e in entries if not _is_empty_short(e)]
        tbl = self._make_table(len(visible), cols)

        from game_engine import get_full_history_extras_for_period, team_matches_played_in_window
        _nat = get_player().get("nationality", "")
        for i, e in enumerate(visible):
            sy = e.get("start_year", ""); sw = e.get("start_week", 1)
            ey = e.get("end_year", "");   ew = e.get("end_week", 0)
            from constants import week_to_iso_date_str
            start_str = week_to_iso_date_str(sy, sw) if sy else ""
            if ey == 0:
                period = f"{start_str} ~ 현재"
            else:
                ew_disp = 52 if ew >= 50 else ew
                period = f"{start_str} ~ {week_to_iso_date_str(ey, ew_disp)}"

            # [2026-07 버그수정, 위 평균평점 버그를 추적하다 같이 발견]
            # ey가 0(진행 중인 현재 스틴트)일 때 "ey or sy or 0"으로 넘기면
            # end_year가 시작 연도(sy) 그 자체가 되어버려서, 실제로는
            # "start_year ~ 현재"까지 열린 컵/챔스/클럽WC/국가대표 경기를
            # "start_year ~ start_year"(그 해 단 1년) 범위로 뭉개버렸다 —
            # 진행 중인 스틴트일수록 이 창(extras)이 놓치는 기간이 커진다.
            # team_matches_played_in_window와 동일하게, 진행 중이면 현재
            # 연도까지로 맞춘다.
            _extras_end_year = ey if ey else get_state().get("current_year", sy or 0)
            extras = get_full_history_extras_for_period(
                e.get("team_id", 0), _nat, sy or 0, _extras_end_year)

            # 출전: 리그 출전/분모 + 그 외 대회 출전/분모 (팀 이력의 '22/24'와 동일 원리)
            _league_total = team_matches_played_in_window(
                e.get("team_name", ""), e.get("league_name", ""), sy, sw, ey, ew or 52) or 0
            grand_played = e.get("matches", 0) + extras["matches_played"]
            grand_avail = _league_total + extras["matches_available"]
            apps_str = f"{grand_played}/{grand_avail}" if grand_avail else str(grand_played)

            g = e.get("goals", 0) + extras["goals"]
            a = e.get("assists", 0) + extras["assists"]
            sv = e.get("saves", 0) + extras["saves"]
            ga = e.get("goals_against", 0) + extras["goals_against"]
            cs = e.get("clean_sheets", 0) + extras["clean_sheets"]
            save_rate = f"{round(sv/(sv+ga)*100,1)}%" if (sv + ga) > 0 else "—"

            # 평점: 리그 평점 합계·횟수 + 그 외 대회 평점 합계·횟수를 합쳐 가중평균
            # [2026-07 버그수정, 신민용 리포트: "전체 이력 평균평점이 빈칸(-)으로
            # 나온다"] career_entries 테이블엔 애초에 season_rating_cnt/
            # season_rating_sum 컬럼이 없다(avg_rating만 있음) — 그런데 이 줄은
            # "e.get('season_rating_cnt', 0)"으로 항상 0을 얻어놓고, 그 0을
            # "_rc"(가중치)로 써서 "avg_rating * _rc"를 계산하니 실제 평점이
            # 9.9든 뭐든 상관없이 무조건 0 * 어떤값 = 0으로 지워져버렸다(심지어
            # 삼항식이 _rc==0이면 곱셈 자체를 안 하고 그냥 0을 반환하도록 짜여
            # 있어서 더 명확한 버그). 리그 실적을 다른 대회(컵/챔스/클럽WC)
            # 실적과 가중평균으로 합치려면 "이 스틴트에서 몇 경기를 뛰었는가"가
            # 가중치여야 하는데, 그 값은 career_entries에 실제로 존재하는
            # "matches" 컬럼을 쓰면 된다.
            _rc = e.get("matches", 0)
            _av = e.get("avg_rating", 0)
            _rs = (_av * _rc) if (_av and _rc) else 0
            _tot_rs = _rs + extras["rating_sum"]
            _tot_rc = _rc + extras["rating_cnt"]
            avg = f"{round(_tot_rs/_tot_rc, 1)}" if _tot_rc else "—"

            # [2026-07 신설, 신민용 요청: "테이블 컬럼에 추가해서 ㄱㄱ"]
            # cup_matches/cwc_matches에도 my_shots 등 컬럼을 추가하고
            # cup_engine.py/club_world_cup_engine.py가 이미 계산해뒀던
            # detail을 저장하도록 고쳐서, 이제 진짜로 합산 가능해졌다.
            shots = e.get("shots", 0) + extras["shots"]
            shots_on = e.get("shots_on", 0) + extras["shots_on"]
            key_passes = e.get("key_passes", 0) + extras["key_passes"]
            dribbles = e.get("dribbles", 0) + extras["dribbles"]
            blocks = e.get("blocks", 0) + extras["blocks"]
            # 패스%: 리그는 스틴트당 평균값 하나만 저장돼 있어 출전 경기수로
            # 가중치를 줘서(그 경기수만큼의 표본으로 취급) 대회별 평균과 합친다.
            _lg_pa = e.get("pass_acc", 0); _lg_m = e.get("matches", 0)
            _pa_sum = (_lg_pa * _lg_m if _lg_pa and _lg_m else 0) + extras["pass_acc_sum"]
            _pa_cnt = (_lg_m if _lg_pa and _lg_m else 0) + extras["pass_acc_cnt"]
            pass_acc_str = f"{round(_pa_sum/_pa_cnt*100)}%" if _pa_cnt else "—"

            _val_map = {
                "골": str(g), "어시": str(a),
                "선방": str(sv), "실점": str(ga), "선방률": save_rate, "CS": str(cs),
                "무실점": str(cs),
                "차단": str(blocks), "패스%": pass_acc_str, "평점기여": avg,
                "기회창출": str(key_passes), "드리블": str(dribbles),
                "슈팅": str(shots), "유효": str(shots_on),
            }
            stat_vals = [_val_map.get(sc, "—") for sc in stat_cols]

            # [2026-07 신설, 신민용 리포트: "전체 이력에 승패 표시가 사라졌어"]
            _tw = e.get("wins", 0) + extras["wins"]
            _td = e.get("draws", 0) + extras["draws"]
            _tl = e.get("losses", 0) + extras["losses"]
            wdl_str = f"{_tw}승{_td}무{_tl}패"

            vals = ([period, e.get("team_name", ""),
                     f"{e.get('league_name','')} ({e.get('tier','')}부)",
                     apps_str]
                    + stat_vals + [avg, wdl_str])
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v)
        lay.addWidget(tbl)
        hint = QLabel("리그 + 컵대회 + 챔피언스리그 + 클럽월드컵 + 국가대표(예선 포함) 합산")
        hint.setStyleSheet("color:#666;font-size:10px;padding:4px;")
        lay.addWidget(hint)
        return w

    def _country_of_league(self, league_name):
        """리그명 → 국가명. 조회 결과 캐싱."""
        if not hasattr(self, "_lc_cache"):
            self._lc_cache = {}
        if league_name in self._lc_cache:
            return self._lc_cache[league_name]
        conn = get_conn()
        row = conn.execute("""SELECT cn.name as cname
                              FROM leagues l JOIN countries cn ON l.country_id=cn.id
                              WHERE l.name=? LIMIT 1""", (league_name,)).fetchone()
        conn.close()
        name = row["cname"] if row else ""
        self._lc_cache[league_name] = name
        return name

    def _trophy_tab(self, trophies):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not trophies:
            lay.addWidget(QLabel("수상 기록 없음")); return w
        cols = ["기간","팀/국가","대회","결과"]
        tbl  = self._make_table(len(trophies), cols)
        for i, t in enumerate(trophies):
            yr     = str(t.get("year",""))
            tier_t = t.get("tier", 0)
            tname  = t.get("team_name","")
            lname  = t.get("league_name","")

            if tier_t and tier_t > 0 and not _is_personal_award(t):
                # 리그 우승: 팀 (국가) / 리그 (N부) / 우승
                country  = self._country_of_league(lname)
                team_str = f"{tname} ({country})" if country else tname
                comp_str = f"{lname} ({tier_t}부)"
                result   = "우승"
                color    = "#00cc44"
            else:
                # 국제대회: 국가 / 대회 / 결과
                team_str = tname
                comp_str = t.get('competition','')
                result   = lname  # league_name 자리에 결과 저장됨
                if "우승" in result:     color = "#00cc44"
                elif "준우승" in result: color = "#aaddff"
                elif "3위" in result:   color = "#ffd700"
                elif "4위" in result:   color = "#cc9944"
                elif "8강" in result:   color = "#aaaaff"
                elif "16강" in result:  color = "#8888cc"
                elif "32강" in result:  color = "#666699"
                elif "탈락" in result:   color = "#ff6666"
                elif "미선발" in result:  color = "#ff9944"
                elif "진출 실패" in result: color = "#cc6600"
                elif "진출실패" in result:  color = "#cc6600"
                else:                    color = None

            for j, v in enumerate([yr, team_str, comp_str, result]):
                self._set(tbl, i, j, v, color if j == 3 else None)
        lay.addWidget(tbl)
        return w

    def _award_tab(self, awards):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not awards:
            lay.addWidget(QLabel("개인 수상 기록 없음")); return w

        # 수상 종류별 횟수 요약
        from collections import Counter
        cnt = Counter(a.get("award_type","") for a in awards)
        order = ["발롱도르","MVP","득점왕","도움왕","베스트11","골든글러브","영플레이어"]
        summary_parts = []
        for k in order:
            if cnt.get(k):
                summary_parts.append(f"{k} {cnt[k]}회")
        if summary_parts:
            sl = QLabel("  ·  ".join(summary_parts))
            sl.setStyleSheet("color:#ffcc00;font-size:14px;font-weight:bold;padding:6px;")
            lay.addWidget(sl)

        cols = ["연도","수상","리그","상세"]
        tbl  = self._make_table(len(awards), cols)
        icon = {"득점왕":"⚽","도움왕":"🎯","베스트11":"⭐","MVP":"🏅",
                "발롱도르":"🏆","영플레이어":"🌟","골든글러브":"🧤"}
        for i, a in enumerate(awards):
            atype = a.get("award_type","")
            label = f"{icon.get(atype,'🏅')} {atype}"
            # 발롱도르/MVP/득점왕은 강조색
            color = "#ffcc00" if atype in ("발롱도르","MVP") else (
                    "#00cc44" if atype in ("득점왕","도움왕") else None)
            vals = [str(a.get("year","")), label, a.get("league_name",""), a.get("detail","")]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == 1 else None)
        lay.addWidget(tbl)
        return w

    def _promo_tab(self, promos):
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not promos:
            lay.addWidget(QLabel("승강 기록 없음")); return w
        cols = ["기간","팀/국가","대회","결과"]
        tbl  = self._make_table(len(promos), cols)
        for i, t in enumerate(promos):
            ft = t.get("from_tier", 0)
            tt = t.get("to_tier", 0)
            color  = "#00cc44" if tt < ft else "#ff6666"
            result = f"{ft}부 → {tt}부"
            lname  = t.get("league_name","")
            tname  = t.get("team_name","")
            country  = self._country_of_league(lname)
            team_str = f"{tname} ({country})" if country else tname
            comp_str = f"{lname} ({ft}부)" if ft else lname
            vals = [str(t.get("year","")), team_str, comp_str, result]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == 3 else None)
        lay.addWidget(tbl)
        return w

    def _intl_tab(self, matches, p):
        """국제전(A매치) 경기별 기록: 기간/포지션/국가/대회/상대/스탯/평점/스코어/결과."""
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        if not matches:
            lay.addWidget(QLabel("국제전 기록 없음")); return w

        # 통산 A매치 요약
        caps = p.get("intl_caps", 0)
        if p.get("position") == "GK":
            sv = sum(m["saves"] for m in matches)
            ga = sum(m["conceded"] for m in matches)
            summary = f"통산 A매치 {caps}경기  |  선방 {sv}  실점 {ga}"
        else:
            summary = (f"통산 A매치 {caps}경기  |  {p.get('intl_goals',0)}골 "
                       f"{p.get('intl_assists',0)}어시")
        ratings = [m["rating"] for m in matches if m["rating"]]
        if ratings:
            summary += f"  |  평균 평점 {sum(ratings)/len(ratings):.1f}"
        sl = QLabel(f"🌍 {summary}")
        sl.setStyleSheet("color:#66ccff;font-size:12px;font-weight:bold;padding:4px;")
        lay.addWidget(sl)

        from constants import position_group
        _pos = p.get("position", "")
        _grp = position_group(_pos)
        if _grp == "GK":
            extra_cols = ["선방","실점"]
        elif _grp == "DEF":
            extra_cols = ["차단","패스%"]
        elif _pos in ("CM","CDM","CAM"):
            extra_cols = ["기회창출","패스%","차단"]
        else:
            extra_cols = ["슈팅","유효","기회창출","드리블"]
        cols = (["기간","포지션","국가","대회","상대","골","어시"]
                + extra_cols + ["평점","스코어","결과"])
        tbl = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            res   = m["result"]
            color = ("#00cc44" if res.startswith("승")
                     else "#888888" if res == "무" else "#cc4444")
            _pac = m.get("pass_acc", 0)
            pac = f"{round(_pac*100)}%" if _pac else "—"
            _emap = {
                "선방": str(m.get("saves",0)), "실점": str(m.get("conceded",0)),
                "차단": str(m.get("blocks",0)), "패스%": pac,
                "기회창출": str(m.get("key_passes",0)), "드리블": str(m.get("dribbles",0)),
                "슈팅": str(m.get("shots",0)), "유효": str(m.get("shots_on",0)),
            }
            vals = ([m['date'], m["position"],
                    f"{m['nat_flag']}{m['nat']}",
                    f"{m['comp']} {m['stage']}",
                    f"{m['opp_flag']}{m['opp']}",
                    str(m["goals"]), str(m["assists"])]
                    + [_emap.get(c, "—") for c in extra_cols]
                    + [str(m["rating"]), m["score"], format_result_with_absence(m)])
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == len(vals) - 1 else None)
        lay.addWidget(tbl)
        return w

    def _champions_tab(self, matches, p):
        """챔피언스리그 경기별 기록: 기간/포지션/팀/대회/상대/스탯/평점/스코어/결과."""
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)

        # 대회별 성적(우승/몇강) 요약 (cl_history)
        # [2026-07 주의] cl_history는 클럽월드컵 기록도 competition="클럽 월드컵"으로
        # 같이 저장되므로(재사용), 이 챔스 탭에서는 반드시 제외해야 섞이지 않는다.
        conn = get_conn()
        try:
            hist = [dict(r) for r in conn.execute(
                "SELECT * FROM cl_history WHERE competition!=? ORDER BY year",
                ("클럽 월드컵",)).fetchall()]
        except Exception:
            hist = []
        conn.close()

        if hist:
            parts = [f"{h['year']}년 {h['result']}" for h in hist]
            hl = QLabel("🏆 " + "   ·   ".join(parts))
            hl.setStyleSheet("color:#ffd24d;font-size:12px;font-weight:bold;padding:4px;")
            hl.setWordWrap(True)
            lay.addWidget(hl)

        if not matches:
            lay.addWidget(QLabel("챔피언스리그 출전 기록 없음"))
            return w

        from constants import position_group
        _pos = p.get("position", "")
        _grp = position_group(_pos)
        if _grp == "GK":
            extra_cols = ["선방","실점"]
        elif _grp == "DEF":
            extra_cols = ["차단","패스%"]
        elif _pos in ("CM","CDM","CAM"):
            extra_cols = ["기회창출","패스%","차단"]
        else:
            extra_cols = ["슈팅","유효","기회창출","드리블"]
        cols = (["기간","포지션","소속팀","대회","상대","골","어시"]
                + extra_cols + ["평점","스코어","결과"])
        tbl = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            res   = m["result"]
            color = ("#00cc44" if res.startswith("승")
                     else "#888888" if res == "무" else "#cc4444")
            _pac = m.get("pass_acc", 0)
            pac = f"{round(_pac*100)}%" if _pac else "—"
            _emap = {
                "선방": str(m.get("saves",0)), "실점": str(m.get("conceded",0)),
                "차단": str(m.get("blocks",0)), "패스%": pac,
                "기회창출": str(m.get("key_passes",0)), "드리블": str(m.get("dribbles",0)),
                "슈팅": str(m.get("shots",0)), "유효": str(m.get("shots_on",0)),
            }
            vals = ([m['date'], m["position"],
                    f"{m['team_flag']}{m['team']}",
                    f"{m['comp']} {m['stage']}",
                    f"{m['opp_flag']}{m['opp']}",
                    str(m["goals"]), str(m["assists"])]
                    + [_emap.get(c, "—") for c in extra_cols]
                    + [str(m["rating"]), m["score"], format_result_with_absence(m)])
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == len(vals) - 1 else None)
        lay.addWidget(tbl)
        return w

    def _cwc_tab(self, matches):
        """[2026-07 신설] 클럽 월드컵 경기별 기록. cup_tab과 같은 톤(세부 스탯 없이
        골/어시/선방/평점 중심)으로 보여준다. cl_history를 재사용하므로
        competition='클럽 월드컵'으로 반드시 필터링해서 챔스 기록과 안 섞이게 한다."""
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0)

        conn = get_conn()
        try:
            hist = [dict(r) for r in conn.execute(
                "SELECT * FROM cl_history WHERE competition=? ORDER BY year",
                ("클럽 월드컵",)).fetchall()]
        except Exception:
            hist = []
        conn.close()

        if hist:
            parts = [f"{h['year']}년 {h['result']}" for h in hist]
            hl = QLabel("🌍 " + "   ·   ".join(parts))
            hl.setStyleSheet("color:#4dd2ff;font-size:12px;font-weight:bold;padding:4px;")
            hl.setWordWrap(True)
            lay.addWidget(hl)

        if not matches:
            lay.addWidget(QLabel("클럽 월드컵 출전 기록 없음"))
            return w

        cols = ["기간", "대회", "상대", "골", "어시", "선방", "실점", "평점", "스코어", "결과"]
        tbl = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            res = m["result"]
            color = ("#00cc44" if res.startswith("승")
                     else "#888888" if res == "무" else "#cc4444")
            vals = [m['date'], f"{m['comp']} {m['stage']}", m["opp"],
                    str(m["goals"]), str(m["assists"]), str(m["saves"]), str(m["conceded"]),
                    str(m["rating"]), m["score"], format_result_with_absence(m)]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == len(vals) - 1 else None)
        lay.addWidget(tbl)
        return w

    def _cup_tab(self, matches):
        """[2026-07 신설] 국내 컵대회 경기별 기록: 기간/라운드/상대/스탯/평점/스코어/결과.
        cup_matches는 챔스처럼 슈팅·패스% 같은 세부 스탯이 없어(모듈 스코프가
        더 작다), 골/어시/선방/평점 중심으로 국제전·챔스와 같은 톤으로 보여준다."""
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0)

        conn = get_conn()
        try:
            hist = [dict(r) for r in conn.execute(
                "SELECT * FROM cup_history ORDER BY year").fetchall()]
        except Exception:
            hist = []
        conn.close()

        if hist:
            parts = [f"{h['year']}년 {h['result']}" for h in hist]
            hl = QLabel("🎖️ " + "   ·   ".join(parts))
            hl.setStyleSheet("color:#c48aff;font-size:12px;font-weight:bold;padding:4px;")
            hl.setWordWrap(True)
            lay.addWidget(hl)

        if not matches:
            lay.addWidget(QLabel("컵대회 출전 기록 없음"))
            return w

        cols = ["기간", "대회", "상대", "골", "어시", "선방", "실점", "평점", "스코어", "결과"]
        tbl = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            res = m["result"]
            color = ("#00cc44" if res.startswith("승")
                     else "#888888" if res == "무" else "#cc4444")
            opp = m["opp"] + (f" ({m['opp_tier']}부)" if m.get("opp_tier") else "")
            vals = [m['date'], f"{m['comp']} {m['stage']}", opp,
                    str(m["goals"]), str(m["assists"]), str(m["saves"]), str(m["conceded"]),
                    str(m["rating"]), m["score"], format_result_with_absence(m)]
            for j, v in enumerate(vals):
                self._set(tbl, i, j, v, color if j == len(vals) - 1 else None)
        lay.addWidget(tbl)
        return w