"""
ui/retire_window.py  ─  은퇴 화면
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea, QWidget
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor

from game_engine import get_player, fmt_money, add_log, get_state, _save_career_entry
from constants import format_result_with_absence


def _fmt_loan_months(total_weeks):
    """주 단위 기간을 '1년', '1년 3개월', '3개월'처럼 사람이 읽는 형태로."""
    months = max(1, round(total_weeks / 4.33))
    if months >= 12:
        yrs, rem = divmod(months, 12)
        return f"{yrs}년" if rem == 0 else f"{yrs}년 {rem}개월"
    return f"{months}개월"


def _loan_out_duration_str(entry_list, idx, partner, fallback_sy, fallback_sw):
    """[2026-08 버그수정, 신민용 리포트: "임대 기간이 실제(1년)와 다르게
    2개월로 뜬다"] 원소속팀 행(exit_type='임대')의 시작~종료는 '임대를
    보내기 전까지 원소속팀에 있었던 기간'일 뿐 실제 임대 기간이 아니다 —
    실제 임대 기간은 목적지 팀(partner)에서 뛴 기간이므로, 이 행 뒤에
    나오는 행들 중 팀명이 partner와 일치하는 행을 찾아 그 행의 시작~종료로
    계산한다. 못 찾거나 아직 안 끝났으면 '진행중'."""
    for fut in entry_list[idx + 1:]:
        if fut.get("team_name") == partner:
            fsy = fut.get("start_year", fallback_sy); fsw = fut.get("start_week", 1)
            fey = fut.get("end_year"); few = fut.get("end_week", 52)
            if fey:
                total_weeks = max(1, (fey - fsy) * 52 + (few - fsw))
                return _fmt_loan_months(total_weeks)
            return "진행중"
    return "진행중"


def _game_confirm(parent, title: str, message: str) -> bool:
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
    from PyQt6.QtCore import Qt
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedWidth(320)
    dlg.setStyleSheet("""
        QDialog { background:#1a1a2e; border:1px solid #333; }
        QLabel  { color:#e0e0e0; font-size:13px; padding:8px; }
        QPushButton { padding:8px 28px; border-radius:4px; font-size:13px; font-weight:bold; }
    """)
    lay = QVBoxLayout(dlg); lay.setSpacing(16); lay.setContentsMargins(20,20,20,16)
    lbl = QLabel(message); lbl.setWordWrap(True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(lbl)
    btn_row = QHBoxLayout(); btn_row.setSpacing(12)
    yes = QPushButton("✅ 확인"); no = QPushButton("❌ 취소")
    yes.setStyleSheet("background:#005522;color:white;")
    no.setStyleSheet("background:#440000;color:white;")
    btn_row.addWidget(yes); btn_row.addWidget(no)
    lay.addLayout(btn_row)
    result = [False]
    yes.clicked.connect(lambda: (result.__setitem__(0,True), dlg.accept()))
    no.clicked.connect(dlg.reject)
    dlg.exec()
    return result[0]


def _game_warning(parent, title: str, message: str):
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QLabel
    from PyQt6.QtCore import Qt
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setFixedWidth(280)
    dlg.setStyleSheet("""
        QDialog { background:#1a1a2e; border:1px solid #555; }
        QLabel  { color:#ffcc44; font-size:13px; padding:8px; }
        QPushButton { padding:7px 32px; border-radius:4px; font-size:13px;
                      background:#333; color:white; font-weight:bold; }
    """)
    lay = QVBoxLayout(dlg); lay.setSpacing(12); lay.setContentsMargins(20,20,20,16)
    lbl = QLabel(f"⚠  {message}"); lbl.setWordWrap(True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(lbl)
    ok = QPushButton("확인")
    ok.clicked.connect(dlg.accept)
    lay.addWidget(ok, alignment=Qt.AlignmentFlag.AlignCenter)
    dlg.exec()


from database import get_conn


# 개인 수상으로 분류할 키워드 (trophy_log에 섞여 들어온 발롱도르·MVP 행 식별)
_PERSONAL_AWARD_KEYWORDS = (
    "발롱도르", "MVP", "득점왕", "도움왕", "베스트11",
    "골든글러브", "영플레이어", "푸스카스", "사모라",
    "올해의 수비수", "구단 올해의 선수",
)


def _is_personal_award(trophy):
    """trophy_log 한 행이 '개인 수상'인지 판별 (우승 집계에서 제외 용도)."""
    comp = (trophy.get("competition") or "")
    return any(k in comp for k in _PERSONAL_AWARD_KEYWORDS)


def _match_stat_str(m):
    """경기 1건(국제전/챔스)의 활약을 포지션별 핵심 지표 문자열로.
       팀 이력 표와 동일한 기준: GK=선방/실점, DEF=차단/패스%,
       MF=기회창출/패스%/차단, 공격수=골/어시/슈팅/드리블."""
    from constants import position_group
    pos = m.get("position", "")
    grp = position_group(pos)
    _pac = m.get("pass_acc", 0)
    pac  = f"{round(_pac*100)}%" if _pac else "-"
    if grp == "GK":
        return f"{m.get('saves',0)}선방 {m.get('conceded',0)}실점"
    if grp == "DEF":
        return (f"{m.get('goals',0)}골 {m.get('assists',0)}어시 "
                f"{m.get('blocks',0)}차단 패스 {pac}")
    if pos in ("CM", "CDM", "CAM"):
        return (f"{m.get('goals',0)}골 {m.get('assists',0)}어시 "
                f"{m.get('key_passes',0)}기회창출 패스 {pac} {m.get('blocks',0)}차단")
    # 공격수/윙어
    return (f"{m.get('goals',0)}골 {m.get('assists',0)}어시 "
            f"슈팅 {m.get('shots',0)}({m.get('shots_on',0)}유효) "
            f"{m.get('dribbles',0)}드리블")


_ABSENCE_LABEL = {
    "injury": "부상", "suspension": "출전정지",
}
_FULL_ABSENCE_REASONS = ("injury", "suspension")   # 완전 결장 — 스탯 자체가 없음


def _match_line_str(m):
    """[2026-07 재수정, 신민용 리포트: "red_card가 원문 그대로 노출되고,
    실제로는 뛴 경기(스탯도 진짜)인데 결장 취급된다"] absence_reason이
    "injury"/"suspension"이면 진짜 결장(스탯 자체가 없음)이라 사유만
    보여주지만, "red_card"는 그 경기 안에서 퇴장당하기 전까지는 실제로
    뛴 경기라 스탯·평점이 진짜다(my_played=1) — 결장 취급하지 않고
    정상 스탯을 보여주되 "(퇴장)"만 덧붙인다."""
    reason = m.get("absence_reason")
    if reason in _FULL_ABSENCE_REASONS:
        return _ABSENCE_LABEL.get(reason, reason)
    if not m.get("my_played", 1) and not reason:
        return "벤치"
    line = f"{_match_stat_str(m)}  평점 {m.get('rating', 0)}"
    if reason == "red_card":
        line += " (퇴장)"
    return line


STYLE = """
QDialog { background:#1e1e1e; color:#ccc; }
QScrollArea { border:none; background:#1e1e1e; }
#heroName { color:#00ff66; font-size:26px; font-weight:bold; }
#secTitle { color:#00cc44; font-size:13px; font-weight:bold;
            border-bottom:1px solid #2a2a2a; padding-bottom:3px; }
#statBox  { background:#252525; border-radius:6px; }
#story    { background:#252525; color:#dddddd; font-size:12px;
            border:1px solid #333; border-radius:6px; padding:10px; }
QTableWidget { background:#1e1e1e; color:#ccc; gridline-color:#2a2a2a;
               border:none; font-size:12px; }
QHeaderView::section { background:#252525; color:#888; border:none; padding:4px; }
#genBtn  { background:#2a2a6a; color:white; border:none; border-radius:6px;
           padding:10px 20px; font-size:13px; font-weight:bold; }
#genBtn:hover  { background:#3a3a8a; }
#genBtn:disabled { background:#333; color:#555; }
#backBtn { background:#2a6a2a; color:white; border:none; border-radius:6px;
           padding:10px 20px; font-size:13px; }
#backBtn:hover { background:#3a8a3a; }
#leftPanel  { background:#1e1e1e; }
#rightPanel { background:#181818; border-left:2px solid #2a2a2a; }
#storyTitle { color:#00ff88; font-size:18px; font-weight:bold;
              padding:14px 18px 8px 18px; }
#storyBig   { background:#181818; color:#e6e6e6; font-size:14px;
              line-height:1.6; border:none; padding:6px 18px 18px 18px; }
"""


class RetireWindow(QDialog):
    def _get_career_matches(self):
        """[2026-08 성능 수정, 신민용 리포트: "재능 좋은 선수로 오래 뛰면
        은퇴/커리어창이 심하게 렉걸린다"] intl/cl/cup/cwc/po 기록 조회
        (get_my_*_matches)는 전 세계 누적 테이블(cup_matches 등)을 스캔하는
        무거운 호출인데, 이 창 안에서 __init__ / _gen_story() /
        _gather_story_inputs()가 각각 따로 다시 불러서 최대 3번 중복
        조회하고 있었다 — 한 번 계산해서 인스턴스에 캐싱해두고 재사용한다.
        (이 창은 열려있는 동안 커리어 데이터가 바뀌지 않으므로 캐시가
        stale해질 걱정은 없다.)"""
        if getattr(self, "_career_match_cache", None) is None:
            from competition import champions_engine
            from competition import cup_engine
            import intl_engine
            from competition import club_world_cup_engine
            import promotion_playoff_engine
            from competition import europa_engine
            from competition import conference_engine
            from competition import super_cup_engine
            self._career_match_cache = {
                "intl_ms": intl_engine.get_my_intl_matches(),
                "qual_ms": intl_engine.get_my_qual_matches(),
                "cl_ms": champions_engine.get_my_cl_matches(),
                "el_ms": europa_engine.get_my_el_matches(),
                "ecl_ms": conference_engine.get_my_ecl_matches(),
                "sc_ms": super_cup_engine.get_my_sc_matches(),
                "cup_ms": cup_engine.get_my_cup_matches(),
                "cwc_ms": club_world_cup_engine.get_my_cwc_matches(),
                "po_ms": promotion_playoff_engine.get_my_po_matches(),
            }
        return self._career_match_cache

    def __init__(self, lang="ko", parent=None):
        self._career_match_cache = None
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("은퇴")
        self.setMinimumSize(1100, 820)
        self.setStyleSheet(STYLE)
        self.lang   = lang
        self.parent_win = parent
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0,0,0,0)

        p = get_player()
        if not p:
            root.addWidget(QLabel("선수 데이터 없음")); return

        # 은퇴 시 현재 주차로 마지막 커리어 항목 종료
        # [버그수정 2026-07, 신민용 리포트: "은퇴창을 여러 번 열었다 닫았다
        # 하니 팀 이력에 똑같은 행이 여러 개 생긴다"] allow_insert 기본값이
        # True라서, 창을 다시 열 때마다 "이미 방금 닫힌 항목"을 또 새로
        # INSERT하고 있었다(_find_open_entry가 이미 닫힌 항목은 못 찾으니
        # allow_insert=True 경로가 매번 새 행을 만듦). allow_insert=False로
        # 넘겨서, 이미 마감된 항목이면 새로 만들지 않고 그대로 둔다.
        st = get_state()
        if st and p.get("current_team_id"):
            _save_career_entry(p, st["current_year"], st["current_week"], allow_insert=False)
        add_log(f"🎖 {p['name']} 선수 은퇴. {p['age']}세.", "event")

        # ── 좌측 패널 (이력/성적 + 하단 버튼) ─────────────
        left_panel = QWidget(); left_panel.setObjectName("leftPanel")
        self.left_panel = left_panel
        left_v = QVBoxLayout(left_panel)
        left_v.setSpacing(0); left_v.setContentsMargins(0,0,0,0)
        root.addWidget(left_panel, stretch=0)

        # ── 스크롤 영역 ───────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner  = QWidget(); lay = QVBoxLayout(inner)
        lay.setSpacing(14); lay.setContentsMargins(16,16,16,16)
        scroll.setWidget(inner)
        left_v.addWidget(scroll, stretch=1)

        # 이름 헤더
        nm = QLabel(f"⭐  {p['name']}  ⭐"); nm.setObjectName("heroName")
        nm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(nm)

        from intl_engine import fmt_nationalities, fmt_rep_nationality
        _nats = fmt_nationalities(p) or f"{p.get('flag','')}{p['nationality']}"
        _rep  = fmt_rep_nationality(p)
        sub = QLabel(f"{_nats}  |  ⚽대표: {_rep}  |  {p['age']}세 은퇴  |  {p['position']}")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color:#888;font-size:13px;")
        lay.addWidget(sub)

        # 통계 박스
        box = QFrame(); box.setObjectName("statBox")
        bl  = QHBoxLayout(box); bl.setContentsMargins(12,10,12,10); bl.setSpacing(6)
        pos = p.get("position","")
        from constants import position_group
        _grp = position_group(pos)
        if _grp == "GK":
            ts = p.get("total_saves", 0)
            tga = p.get("total_goals_against", 0)
            tot_shots = ts + tga
            sr = f"{round(ts/tot_shots*100,1)}%" if tot_shots else "0%"
            stat2 = ("선방", f"{ts}회  {sr}")
            stat3 = ("실점", f"{tga}골")
        elif _grp == "DEF":
            # 수비수: 무실점 경기 수(커리어 합산)를 핵심 지표로
            try:
                _cs = sum(e.get("clean_sheets",0) for e in
                          [dict(r) for r in get_conn().execute(
                              "SELECT clean_sheets FROM career_entries").fetchall()])
            except Exception:
                _cs = 0
            stat2 = ("무실점", f"{_cs}경기")
            stat3 = ("공격P", f"{p.get('total_goals',0)}골 {p.get('total_assists',0)}A")
        else:
            stat2 = ("골", f"{p.get('total_goals',0)}")
            stat3 = ("어시", f"{p.get('total_assists',0)}")
        # [2026-07 신설] 최고 이적료 — career_entries.transfer_fee 중 최댓값.
        # 이적료가 아예 없었으면(전부 0, FA/유스승격만) 카드 자체를 생략한다.
        try:
            _max_fee = max((e.get("transfer_fee", 0) for e in
                            [dict(r) for r in get_conn().execute(
                                "SELECT transfer_fee FROM career_entries").fetchall()]),
                           default=0)
        except Exception:
            _max_fee = 0
        stats = [
            ("출전", f"{p.get('total_matches',0)}경기"),
            stat2, stat3,
            ("시즌", f"{p.get('total_seasons',0)}"),
            ("총자산", fmt_money(p.get('total_assets',0))),
            ("누적수입", fmt_money(p.get('total_earnings',0))),  # 이슈10
        ]
        if _max_fee > 0:
            stats.append(("최고이적료", fmt_money(_max_fee)))
        # [2026-08 신설, 신민용 요청: "커리어에 레드카드 기록 추가"]
        # 통산 레드카드가 한 번이라도 있으면 통계 박스에 함께 보여준다
        # (전 대회 합산 — 리그만의 수치는 아래 팀 이력 표의 "🟥" 컬럼 참고).
        _trc = p.get("total_red_cards_all", 0)
        if _trc > 0:
            stats.append(("🟥레드카드", f"{_trc}회"))
        for k, v in stats:
            sw = QFrame(); sl = QVBoxLayout(sw); sl.setContentsMargins(4,4,4,4)
            kl = QLabel(k); kl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            kl.setStyleSheet("color:#888;font-size:11px;")
            vl = QLabel(v); vl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.setStyleSheet("color:#00cc44;font-size:15px;font-weight:bold;")
            sl.addWidget(kl); sl.addWidget(vl)
            bl.addWidget(sw)
        lay.addWidget(box)

        # DB 데이터
        conn = get_conn(); c = conn.cursor()
        entries  = [dict(r) for r in c.execute("SELECT * FROM career_entries ORDER BY id").fetchall()]
        # trophy_log에는 발롱도르·MVP 같은 개인 수상도 함께 적재되므로
        # 우승 경력에는 '진짜 우승'만 남기고 개인 수상 행은 제외한다.
        # [2026-07 버그+성능 수정] career_window.py와 동일한 이유로
        # get_my_trophies() 사용 (전 세계 AI 팀 우승 혼입 방지 + 연차별 성능 저하 방지).
        from game_engine import get_my_trophies
        all_trophies = get_my_trophies()
        trophies = [t for t in all_trophies if not _is_personal_award(t)]
        try:
            awards = [dict(r) for r in c.execute(
                "SELECT * FROM awards WHERE is_mine=1 ORDER BY year").fetchall()]
        except Exception:
            awards = []
        from game_engine import get_my_promotions
        promos   = get_my_promotions()
        conn.close()

        # ── 개인 수상 하이라이트 (있을 때만, 최상단 강조) ──
        if awards:
            from collections import Counter
            from constants import normalize_award_bucket
            cnt = Counter(normalize_award_bucket(a.get("award_type","")) for a in awards)
            order = ["발롱도르","MVP","득점왕","도움왕","베스트11","골든글러브","영플레이어",
                     "올해의 수비수","구단 올해의 선수",
                     "FIFA 푸스카스상","대회 최고의 골","리그 올해의 골"]
            parts = [f"{k} {cnt[k]}회" for k in order if cnt.get(k)]
            hl = QLabel("🏅 " + "   ·   ".join(parts))
            hl.setWordWrap(True)
            hl.setStyleSheet("color:#ffcc00;font-size:15px;font-weight:bold;"
                             "padding:10px;background:#2a2a1a;border-radius:6px;")
            lay.addWidget(hl)

        # ── 팀 이력 ─────────────────────────────────
        t1 = QLabel("📋 팀 이력"); t1.setObjectName("secTitle")
        lay.addWidget(t1)
        lay.addWidget(self._team_table(entries))

        # ── 전체 이력 ────────────────────────────────
        # [2026-07 신설, 신민용 요청 → 재수정] 팀 이력은 리그만, 여기는
        # 그 기간에 있었던 경기 전체(리그+컵+챔스+클럽WC+국가대표)를
        # 다 합친 진짜 전체 하나로 보여준다.
        t1b = QLabel("🗂️ 전체 이력 (리그+컵+챔스+클럽월드컵+국가대표 합계)")
        t1b.setObjectName("secTitle")
        lay.addWidget(t1b)
        lay.addWidget(self._club_totals_table(entries))

        # ── 우승 경력 ────────────────────────────────
        t2 = QLabel(f"🏆 성적  ({len(trophies)})")
        t2.setObjectName("secTitle")
        lay.addWidget(t2)
        lay.addWidget(self._trophy_table(trophies))

        # ── 승강 경험 ────────────────────────────────
        t3 = QLabel(f"🔼 승강 경험  ({len(promos)})")
        t3.setObjectName("secTitle")
        lay.addWidget(t3)
        lay.addWidget(self._promo_table(promos))

        # ── 국제전 기록 ──────────────────────────────
        # [2026-08 성능 수정] get_my_*_matches() 6개를 한 번씩만 조회해
        # 인스턴스에 캐싱한 결과를 재사용 (_get_career_matches 참고).
        _cm = self._get_career_matches()
        intl_ms = _cm["intl_ms"]
        t35 = QLabel(f"🌍 국제전 기록  ({len(intl_ms)})")
        t35.setObjectName("secTitle")
        lay.addWidget(t35)
        lay.addWidget(self._intl_table(intl_ms, p))

        # ── 국제전(예선) 기록 ─────────────────────────
        qual_ms = _cm["qual_ms"]
        if qual_ms:
            t35q = QLabel(f"🌏 국제전(예선) 기록  ({len(qual_ms)})")
            t35q.setObjectName("secTitle")
            lay.addWidget(t35q)
            lay.addWidget(self._intl_table(qual_ms, p))

        # ── 챔피언스리그 기록 ────────────────────────
        cl_ms = _cm["cl_ms"]
        t36 = QLabel(f"🏆 챔피언스리그 기록  ({len(cl_ms)})")
        t36.setObjectName("secTitle")
        lay.addWidget(t36)
        lay.addWidget(self._champions_table(cl_ms, p))

        # ── 유로파리그 기록 (2026-08 신설) ────────────
        el_ms = _cm["el_ms"]
        t36e = QLabel(f"🥈 유로파리그 기록  ({len(el_ms)})")
        t36e.setObjectName("secTitle")
        lay.addWidget(t36e)
        lay.addWidget(self._champions_table(el_ms, p, label="유로파리그"))

        # ── 컨퍼런스리그 기록 (2026-08 신설) ──────────
        ecl_ms = _cm["ecl_ms"]
        t36c = QLabel(f"🥉 컨퍼런스리그 기록  ({len(ecl_ms)})")
        t36c.setObjectName("secTitle")
        lay.addWidget(t36c)
        lay.addWidget(self._champions_table(ecl_ms, p, label="컨퍼런스리그"))

        # ── 슈퍼컵 기록 (2026-08 신설, 14순위) ────────
        sc_ms = _cm["sc_ms"]
        t36s = QLabel(f"🏵 슈퍼컵 기록  ({len(sc_ms)})")
        t36s.setObjectName("secTitle")
        lay.addWidget(t36s)
        lay.addWidget(self._champions_table(sc_ms, p, label="슈퍼컵"))

        # ── 컵대회 기록 ──────────────────────────────
        cup_ms = _cm["cup_ms"]
        t37 = QLabel(f"🎖️ 컵대회 기록  ({len(cup_ms)})")
        t37.setObjectName("secTitle")
        lay.addWidget(t37)
        lay.addWidget(self._cup_table(cup_ms))

        # ── 클럽 월드컵 기록 (있을 때만 — 4년에 한 번뿐이라 없는 게 정상) ──
        cwc_ms = _cm["cwc_ms"]
        if cwc_ms:
            t37b = QLabel(f"🌍 클럽 월드컵 기록  ({len(cwc_ms)})")
            t37b.setObjectName("secTitle")
            lay.addWidget(t37b)
            lay.addWidget(self._cwc_table(cwc_ms))

        # ── 승강 플레이오프 기록 ──────────────────────
        # [2026-07 버그수정] 컵대회와 같은 급으로(매년 누구에게나 열릴 수
        # 있음) 0건이어도 항상 표시 — 클럽월드컵(4년 주기)만 조건부로 둔다.
        po_ms = _cm["po_ms"]
        t37c = QLabel(f"⚖ 승강 플레이오프 기록  ({len(po_ms)})")
        t37c.setObjectName("secTitle")
        lay.addWidget(t37c)
        lay.addWidget(self._po_table(po_ms, p))

        # ── 개인 수상 ────────────────────────────────
        # (awards는 위에서 conn이 열려 있을 때 이미 로드했다. conn.close() 이후
        #  c.execute를 다시 호출하면 예외가 나서 0개로 표시되던 버그 수정 →
        #  앞서 로드한 리스트를 그대로 재사용한다.)
        t4 = QLabel(f"🥇 개인 수상  ({len(awards)})")
        t4.setObjectName("secTitle")
        lay.addWidget(t4)
        lay.addWidget(self._award_table(awards))

        lay.addStretch()

        # ── 하단 버튼: 좌측 패널 바닥에 고정 (화면 크기와 무관하게 항상 보임) ──
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 6, 16, 10)

        self.gen_btn = QPushButton("✨ AI 커리어 요약")
        self.gen_btn.setObjectName("genBtn")
        self.gen_btn.clicked.connect(self._gen_story)

        self.book_btn = QPushButton("📖 스토리 생성")
        self.book_btn.setObjectName("genBtn")
        self.book_btn.clicked.connect(self._open_story_book)

        back_btn = QPushButton("🏠 시작 화면으로")
        back_btn.setObjectName("backBtn")
        back_btn.clicked.connect(self._go_start)

        btn_row.addWidget(self.gen_btn)
        btn_row.addWidget(self.book_btn)
        btn_row.addWidget(back_btn)
        left_v.addLayout(btn_row)

        # ── 우측 패널: AI 커리어 요약 (전체 화면 사용) ──────
        right_panel = QWidget(); right_panel.setObjectName("rightPanel")
        right_v = QVBoxLayout(right_panel)
        right_v.setSpacing(0); right_v.setContentsMargins(0,0,0,0)
        root.addWidget(right_panel, stretch=1)

        t5 = QLabel("✨ AI 커리어 요약")
        t5.setObjectName("storyTitle")
        right_v.addWidget(t5)

        self.story_box = QTextEdit()
        self.story_box.setObjectName("storyBig")
        self.story_box.setReadOnly(True)
        self.story_box.setPlaceholderText(
            "좌측 하단의 'AI 커리어 요약' 버튼을 누르면 기록 요약이,\n"
            "'AI 스토리 생성' 버튼을 누르면 책 형태의 연대기 창이 뜹니다...")
        right_v.addWidget(self.story_box, stretch=1)


    def showEvent(self, event):
        super().showEvent(event)
        self.showMaximized()

        # 은퇴 화면은 좌측(이력) + 우측(AI 스토리)을 한 화면에 모두 보여준다.
        # 커리어 창(career_window.py)과 똑같이 표가 가로 스크롤 없이 다
        # 보이는 게 최우선이고, 우측 AI 스토리 패널은 화면이 넉넉할 때만
        # 그 나머지를 차지한다 (최소 폭만 보장).
        from PyQt6.QtWidgets import QTableWidget
        from PyQt6.QtGui import QGuiApplication
        min_w = 720
        right_min = 460  # 우측 AI 스토리 패널에 최소한 남겨줄 폭
        tables = self.findChildren(QTableWidget)
        max_w = min_w
        for tbl in tables:
            w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount())) + 50
            max_w = max(max_w, w)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        # 화면이 넉넉하면 표 전체가 보이는 폭(max_w) 그대로 사용하고,
        # 화면이 좁아서 우측 최소 폭을 침범할 때만 그만큼만 줄인다.
        left_w = max(min_w, min(max_w, screen.width() - right_min))
        self.left_panel.setFixedWidth(left_w)
        # 좌측 패널 폭 안에 들어오는 표는 가로 스크롤바를 꺼서 깔끔하게,
        # 그래도 넘치는(화면이 아주 좁을 때) 표만 스크롤 가능하게 남긴다.
        for tbl in tables:
            w = sum(tbl.columnWidth(i) for i in range(tbl.columnCount())) + 50
            if w <= left_w - 20:
                tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            else:
                tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    # ── 테이블 헬퍼 ──────────────────────────────────

    def _team_table(self, entries):
        if not entries:
            lbl = QLabel("기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl

        # [2026-07 재수정, 신민용 리포트: "세리에 A인데 국가가 브라질로
        # 뜬다"] league_name(문자열)만으로 국가를 조회하면, 이탈리아
        # 세리에 A와 브라질 세리에 A(브라질레이랑 통칭)처럼 리그명이
        # 같은 나라가 있을 때 엉뚱한 나라가 캐시에 박혀버린다 — team_id로
        # teams→leagues→countries를 직접 조회하면 이름 충돌과 무관하게
        # 항상 정확한 나라가 나온다(팀은 나라를 안 바꾸므로 승강으로
        # league_id/tier가 바뀌어도 국가 조회는 안전하다).
        conn = get_conn()
        c = conn.cursor()
        team_country = {}
        for e in entries:
            tid = e.get("team_id")
            if tid and tid not in team_country:
                row = c.execute("""SELECT cn.flag, cn.name as cname
                                   FROM teams t JOIN leagues l ON t.league_id=l.id
                                   JOIN countries cn ON l.country_id=cn.id
                                   WHERE t.id=? LIMIT 1""", (tid,)).fetchone()
                team_country[tid] = f"{row['flag']} {row['cname']}" if row else ""
        conn.close()

        from constants import position_group
        _mypos = get_player().get("position", "")
        _grp = position_group(_mypos)
        if _grp == "GK":
            stat_cols = ["골","어시","선방","실점","선방률","CS"]
        elif _grp == "DEF":
            stat_cols = ["골","어시","무실점","차단","패스%","평점기여"]
        elif _mypos in ("CM","CDM","CAM"):
            stat_cols = ["골","어시","기회창출","패스%","차단","드리블"]
        else:
            stat_cols = ["골","어시","슈팅","유효","기회창출","드리블"]
        cols = (["기간","나이","포지션","국가","리그","팀명","연봉","출전"]
                + stat_cols
                + ["평균평점","팀순위","승무패","🟥","계약","이적"])
        # 이슈3: '스퓨리어스 중복 행'(이벤트 없이 잔류만 하는데 실수로
        # 새 행이 또 생기는 버그, transfer_type='')만 숨긴다. 진짜
        # 이적/입단/오퍼 이벤트(transfer_type이 채워짐)는 기간이 짧고
        # 0경기여도 실제 기록이므로 항상 보여준다.
        # [2026-07 버그수정, 신민용 리포트: "베어스 FC 입단 기록이 은퇴창
        # 팀 이력에 아예 안 보인다"] 예전엔 transfer_type을 확인하지 않고
        # 무조건 걸러서, career_window.py(_is_empty_short)에는 정상적으로
        # 뜨는 "2001-01-01 입단 → 곧바로 이적" 같은 실제 기록이 은퇴창
        # 에서만 사라졌다. career_window.py와 동일한 기준으로 맞춘다.
        def _is_empty_short(e):
            if e.get("transfer_type"):
                return False
            if e.get("end_year", 0) == 0:  return False
            if e.get("matches", 0) != 0:   return False
            sy = e.get("start_year", 0); ey = e.get("end_year", 0)
            sw = e.get("start_week", 1); ew = e.get("end_week", 0)
            return sy == ey and (ew - sw) <= 4
        visible = [e for e in entries if not _is_empty_short(e)]
        tbl  = self._make_table(len(visible), cols)
        # [2026-08 신설, 15순위 연장 — 신민용 리포트: "career_window.py엔
        # 나이가 적혀있는데 retire_window.py엔 안 적혀있다"] career_window
        # 의 _team_tab과 완전히 같은 계산(birth_year 기준) — 은퇴한 선수라
        # p["age"]가 은퇴 시점 나이로 고정돼 있어도, birth_year는 그대로
        # 남아있어 과거 각 재직 연도의 나이를 그대로 역산할 수 있다.
        _birth_year = get_player().get("birth_year")

        prev_team = None
        for i, e in enumerate(visible):
            rc  = e.get("season_rating_cnt", 0)
            rs  = e.get("season_rating_sum", 0) or e.get("avg_rating", 0)
            avg = round(rs/rc, 1) if rc > 0 else (round(float(rs), 1) if rs else "—")
            # 출전 0이면 팀 순위·승무패는 본인 성적이 아니므로 — 표시
            if e.get("matches", 0) > 0:
                wdl       = f"{e.get('wins',0)}승{e.get('draws',0)}무{e.get('losses',0)}패"
                rank_disp = f"{e.get('team_rank',0)}위"
            else:
                wdl       = "—"
                rank_disp = "—"

            sy = e.get("start_year",""); sw = e.get("start_week", 1)
            ey = e.get("end_year","");   ew = e.get("end_week", 52)
            from constants import week_to_iso_date_str, week_to_iso_date_str_end
            start_str = week_to_iso_date_str(sy, sw) if sy else ""
            end_str = week_to_iso_date_str_end(ey, ew) if ey else ""
            period = f"{start_str} ~ {end_str}"

            # [2026-08 신설, 15순위 연장, 신민용 리포트: "career_window.py엔
            # 나이가 적혀있는데 retire_window.py엔 안 적혀있다"] career_window
            # 의 _team_tab과 완전히 같은 계산(birth_year 기준) — 은퇴한
            # 선수라 대부분 재직 기간이 이미 닫혀있지만(ey 있음), 혹시 안
            # 닫힌 마지막 스틴트가 있으면 은퇴 시점 나이(p["age"])까지로
            # 계산한다.
            if _birth_year and sy:
                _age_end_year = ey if ey else (sy + (get_player().get("age", 0) - (sy - _birth_year)))
                age_start = sy - _birth_year
                age_end = _age_end_year - _birth_year
                age_str = f"{age_start}세" if age_start == age_end else f"{age_start}~{age_end}세"
            else:
                age_str = "—"

            pos   = e.get("position","")
            sv  = e.get("saves", 0)
            ga  = e.get("goals_against", 0)
            total_shots = sv + ga
            save_rate = f"{round(sv/total_shots*100,1)}%" if total_shots > 0 else "—"
            _pac = e.get("pass_acc", 0)
            pac_str = f"{round(_pac*100)}%" if _pac else "—"

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
            # [2026-07 신설, 신민용 요청] 0경기 항목은 세부 스탯 전부 "—".
            if e.get("matches", 0) == 0:
                stat_vals = ["—"] * len(stat_vals)
            
            # 계약 컬럼: 팀 변경 또는 연장 시에만 년수 표시
            cur_team = e.get("team_name", "")
            c_yrs = e.get("contract_years", 0)
            exit_t = e.get("exit_type", "")
            in_type = e.get("transfer_type", "입단")
            # [2026-07 신설, career_window.py와 동일한 신민용 리포트 반영]
            # 들어온 경로가 강제이적(팔림)이면 "구매"로 구분 표시 — 나간
            # 경로 쪽 "팔림" 표기(빨간 강조 포함)는 그대로 둔다.
            if in_type == "팔림":
                in_type = "구매"
            # [2026-08 신설, career_window.py와 동일한 신민용 지적 반영: "2001년
            # 팔림 표시는 안 뜨고 2002년 구매만 뜨는게 맞는듯"] 나가는 쪽 exit_t가
            # "팔림"이면 표시하지 않고(들어오는 쪽 "구매"만 남김), 그 행 자체의
            # in_type으로 대체한다. 방출/이적/계약만료는 그대로 우선 표시.
            _exit_disp = "" if exit_t == "팔림" else exit_t
            # [2026-08 버그수정, 신민용 리포트: "유료로 들어왔다가 곧장 임대
            # 나간 경우, 임대 표시가 이적료 표시를 덮어써서 이적료가 아예
            # 안 보인다"] exit_t가 "임대"인데 이 행 자체가 유료로 들어온
            # 행(이적/오퍼/구매 + 실제 이적료 있음)이면, 들어온 이유(이적료
            # 포함)를 우선한다 — 임대 나간 사실은 받는 팀 행에 "임대(진행중)"
            # 으로 이미 뜨니 여기서 또 보여줄 필요가 없다.
            if (exit_t == "임대" and in_type in ("이적", "오퍼", "구매")
                    and e.get("transfer_fee", 0)):
                _exit_disp = ""
                exit_t = ""
            t_type = _exit_disp if _exit_disp else in_type

            # [2026-07 재수정, 신민용 지적: "은퇴창도 career_window.py와 동일하게
            # 떠야지"] 계약 컬럼은 원소속팀 계약년수를 그대로 보여주고(임대는
            # 새 계약이 아니라 원소속팀 계약 유지), 이적 컬럼에 실제 임대
            # 기간을 "임대(N개월)"처럼 붙인다 — career_window.py와 동일 로직.
            if t_type in ("임대", "임대 종료"):
                # [2026-07 재수정, 신민용 지적: "임대(1년)/임대 종료(1년)만
                # 뜨면 어느 팀으로 갔는지/어디로 복귀했는지 안 보인다"]
                # career_window.py와 동일하게 상대팀명을 함께 표시한다.
                _partner = e.get("loan_partner_team", "") or ""
                if t_type == "임대 종료":
                    # [2026-08 버그수정, 신민용 리포트: "임대 온 것도 표시해야
                    # 한다"] 이 행 자체가 임대처에서 뛴 기간이라 이 행의
                    # 시작~종료가 곧 실제 임대 기간이다 — "OO 복귀"만 보여주지
                    # 말고 어디서 얼마나 임대로 왔었는지도 같이 보여준다.
                    if ey:
                        dur = _fmt_loan_months(max(1, (ey - sy) * 52 + (ew - sw)))
                        t_type = f"{_partner}에서 임대({dur}) 후 복귀" if _partner else f"임대({dur}) 후 복귀"
                    else:
                        t_type = f"{_partner} 복귀" if _partner else "복귀"
                else:
                    # [2026-08 버그수정, 신민용 리포트: "임대 기간이 실제(1년)와
                    # 다르게 2개월로 뜬다"] 이 행(원소속팀)의 시작~종료는
                    # 임대 가기 전 원소속팀 재직 기간일 뿐이라 부정확하다 —
                    # 목적지 팀에서 뛴 실제 기간(다음에 나오는 그 팀 행)으로
                    # 계산한다.
                    dur = _loan_out_duration_str(visible, i, _partner, sy, sw)
                    t_type = f"{_partner}에 임대({dur})" if _partner else f"임대({dur})"


            if in_type == "임대" or i == 0 or cur_team != visible[i-1].get("team_name"):
                # 임대, 또는 팀이 바뀌었거나 첫 행 → 계약년수 표시
                c_str = f"{c_yrs}년" if c_yrs else "—"
                prev_team = cur_team
            elif in_type == "연장" or t_type == "연장":
                # 같은 팀에서 연장 (연장 년수 표시)
                c_str = f"{c_yrs}년" if c_yrs else "—"
            else:
                # 같은 팀 계속 (대시)
                c_str = "—"
            
            # [2026-07 재수정, 신민용 지적: "파는 쪽(팔림)엔 표시 안 하고
            # 사는 쪽(구매/오퍼)에만 금액을 붙여야 한다"] exit_t가 채택된
            # 경우("이 팀을 떠난" 이벤트)는 표시하지 않는다 — 그 이적료는
            # 다음 팀 행(구매 쪽)에 이미 붙는다.
            if not exit_t:
                _fee = e.get("transfer_fee", 0)
                if _fee:
                    t_type = f"{t_type} ({fmt_money(_fee)})"

            # 이적 컬럼
            tt_color = "#cc4444" if t_type in ("팔림", "방출", "계약만료") else None

            from game_engine import team_matches_played_in_window, league_total_teams_by_name
            _total_g2 = team_matches_played_in_window(
                e.get("team_id", 0), e.get("league_name", ""), sy, sw, ey, ew)
            _apps_str2 = f"{e.get('matches',0)}/{_total_g2}" if _total_g2 else str(e.get("matches", 0))

            ln = e.get("league_name", "")
            country_str = team_country.get(e.get("team_id"), "")
            league_str = f"{ln} ({e.get('tier','')}부)" if ln else ""
            # [2026-07 신설, 신민용 요청] "12위" 대신 "12위/18팀"으로 —
            # rank_disp가 이미 "—"(안 뛴 경우)면 그대로 둔다.
            if rank_disp != "—":
                _total_teams = league_total_teams_by_name(ln)
                if _total_teams:
                    rank_disp = f"{e.get('team_rank',0)}위/{_total_teams}팀"

            vals = ([period, age_str, pos,
                     country_str,
                     league_str,
                     e.get("team_name",""),
                     fmt_money(e.get("salary",0)),
                     _apps_str2]
                    + stat_vals
                    + [str(avg), rank_disp, wdl,
                       (str(e.get("red_cards", 0)) if e.get("red_cards", 0) else "—"),
                       c_str, t_type])
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(visible), 7) * 28)
        return tbl

    def _club_totals_table(self, entries):
        """career_window.py의 _club_totals_tab과 동일 — 팀 재직 기간별
        클럽대회(컵+챔스+클럽월드컵) 합계와 국가대표까지 합친 전체 합계.
        [2026-07 재작성, 신민용 지적: "포지션마다 다르게, 출전도 분모
        포함해서" — career_window.py의 _club_totals_tab과 동일 로직]"""
        if not entries:
            lbl = QLabel("기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl

        def _is_empty_short(e):
            # [버그수정 2026-07, 신민용 리포트: "시즌 시작 전에 바로
            # 이적하면 원래 있던 팀이 커리어에서 아예 안 보인다"] 이
            # 필터는 원래 '같은 재직의 스퓨리어스 중복 행'(이벤트 없이
            # 잔류만 하는데 실수로 새 행이 또 생기는 버그, transfer_type=
            # '')을 숨기려는 목적이었다. 그런데 진짜 이적/입단/오퍼
            # 이벤트(transfer_type이 채워짐)로 생긴 항목도 우연히 기간이
            # 짧으면(0경기) 똑같이 걸러져서, "2001-01-01 입단 → 01-15
            # 오퍼로 즉시 이적" 같은 정상적인 실제 기록까지 함께
            # 사라졌다. transfer_type이 실제로 채워져 있으면(진짜
            # 이벤트) 기간과 무관하게 항상 보여준다.
            if e.get("transfer_type"):
                return False
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
        cols = ["기간", "나이", "팀명", "리그", "출전"] + stat_cols + ["평균평점", "승무패", "🟥"]

        visible = [e for e in entries if not _is_empty_short(e)]
        tbl = self._make_table(len(visible), cols)

        from game_engine import get_full_history_extras_for_period, team_matches_played_in_window
        _nat = get_player().get("nationality", "")
        # [2026-08 신설, 15순위 연장] career_window.py의 _club_totals_tab과 동일.
        _birth_year = get_player().get("birth_year")
        for i, e in enumerate(visible):
            sy = e.get("start_year", ""); sw = e.get("start_week", 1)
            ey = e.get("end_year", "");   ew = e.get("end_week", 52)
            from constants import week_to_iso_date_str, week_to_iso_date_str_end
            start_str = week_to_iso_date_str(sy, sw) if sy else ""
            end_str = week_to_iso_date_str_end(ey, ew) if ey else ""
            period = f"{start_str} ~ {end_str}"

            if _birth_year and sy:
                _age_end_year = ey if ey else (sy + (get_player().get("age", 0) - (sy - _birth_year)))
                age_start = sy - _birth_year
                age_end = _age_end_year - _birth_year
                age_str = f"{age_start}세" if age_start == age_end else f"{age_start}~{age_end}세"
            else:
                age_str = "—"

            # [2026-07 버그수정, career_window.py와 동일 버그 발견/수정] ey가 0
            # (진행 중)일 때 "ey or sy or 0"은 end_year를 start_year 그 해
            # 하나로 뭉개버려서 실제로 열린 컵/챔스/클럽WC/국가대표 경기 대부분을
            # 놓쳤다. 진행 중이면 현재 연도까지로 맞춘다.
            _extras_end_year = ey if ey else get_state().get("current_year", sy or 0)
            extras = get_full_history_extras_for_period(
                e.get("team_id", 0), _nat, sy or 0, _extras_end_year)

            _league_total = team_matches_played_in_window(
                e.get("team_id", 0), e.get("league_name", ""), sy, sw, ey, ew) or 0
            grand_played = e.get("matches", 0) + extras["matches_played"]
            grand_avail = _league_total + extras["matches_available"]
            apps_str = f"{grand_played}/{grand_avail}" if grand_avail else str(grand_played)

            g = e.get("goals", 0) + extras["goals"]
            a = e.get("assists", 0) + extras["assists"]
            sv = e.get("saves", 0) + extras["saves"]
            ga = e.get("goals_against", 0) + extras["goals_against"]
            cs = e.get("clean_sheets", 0) + extras["clean_sheets"]
            save_rate = f"{round(sv/(sv+ga)*100,1)}%" if (sv + ga) > 0 else "—"

            # [2026-07 버그수정, career_window.py와 동일 버그 발견/수정]
            # career_entries엔 season_rating_cnt/season_rating_sum 컬럼이 없어서
            # (avg_rating만 있음) _rc가 항상 0으로 잡히고, avg_rating*_rc가
            # 무조건 0이 되어 실제 평점이 지워지던 버그. 가중치를 실제 존재하는
            # matches(출전 경기수) 컬럼으로 바꾼다.
            _rc = e.get("matches", 0)
            _av = e.get("avg_rating", 0)
            _rs = (_av * _rc) if (_av and _rc) else 0
            _tot_rs = _rs + extras["rating_sum"]
            _tot_rc = _rc + extras["rating_cnt"]
            avg = f"{round(_tot_rs/_tot_rc, 1)}" if _tot_rc else "—"

            # [2026-07 신설, 신민용 요청: "테이블 컬럼에 추가해서 ㄱㄱ"]
            shots = e.get("shots", 0) + extras["shots"]
            shots_on = e.get("shots_on", 0) + extras["shots_on"]
            key_passes = e.get("key_passes", 0) + extras["key_passes"]
            dribbles = e.get("dribbles", 0) + extras["dribbles"]
            blocks = e.get("blocks", 0) + extras["blocks"]
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

            _tw = e.get("wins", 0) + extras["wins"]
            _td = e.get("draws", 0) + extras["draws"]
            _tl = e.get("losses", 0) + extras["losses"]
            wdl_str = f"{_tw}승{_td}무{_tl}패"

            # [2026-08 신설, 신민용 리포트: "전체 이력엔 그 해 컵대회/챔스/
            # 월드컵 등 대회 레드카드가 안 잡힌다"] career_window.py의
            # _club_totals_tab과 동일 수정 — 리그 전용 누적값(e["red_cards"])
            # + 컵/챔스/클럽월드컵/국가대표 합산(extras["red_cards"]).
            red_cards_str = str(e.get("red_cards", 0) + extras["red_cards"])

            vals = ([period, age_str, e.get("team_name", ""),
                     f"{e.get('league_name','')} ({e.get('tier','')}부)",
                     apps_str]
                    + stat_vals + [avg, wdl_str, red_cards_str])
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(visible), 7) * 28)
        return tbl

    def _trophy_table(self, trophies):
        if not trophies:
            lbl = QLabel("수상 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
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
                if "우승" in result:    color = "#00cc44"
                elif "준우승" in result: color = "#aaddff"
                elif "3위" in result:   color = "#ffd700"
                elif "4위" in result:   color = "#cc9944"
                elif "거절" in result:  color = "#cc8844"
                elif "탈락" in result:  color = "#cc6666"
                else:                   color = None

            for j, v in enumerate([yr, team_str, comp_str, result]):
                self._set_item_colored(tbl, i, j, v, color if j == 3 else None)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(trophies), 7) * 28)
        return tbl
    
    def _country_of_league(self, league_name):
        """리그명에서 국가 정보 추출 (캐시됨)"""
        if not hasattr(self, '_lc_cache'):
            self._lc_cache = {}
        if league_name in self._lc_cache:
            return self._lc_cache[league_name]
        conn = get_conn()
        row = conn.execute("""SELECT cn.flag, cn.name as cname
                             FROM leagues l JOIN countries cn ON l.country_id=cn.id
                             WHERE l.name=? LIMIT 1""", (league_name,)).fetchone()
        conn.close()
        name = f"{row['flag']} {row['cname']}" if row else ""
        self._lc_cache[league_name] = name
        return name
    
    def _set_item_colored(self, tbl, row, col, val, color=None):
        """색상이 들어갈 수 있는 _set_item"""
        item = QTableWidgetItem(str(val))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if color:
            item.setForeground(QColor(color))
        tbl.setItem(row, col, item)

    def _award_table(self, awards):
        """개인 수상 테이블"""
        if not awards:
            lbl = QLabel("개인 수상 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        
        # 수상 종류별 횟수 요약
        from collections import Counter
        from constants import normalize_award_bucket, award_icon
        cnt = Counter(normalize_award_bucket(a.get("award_type","")) for a in awards)
        order = ["발롱도르","MVP","득점왕","도움왕","베스트11","골든글러브","영플레이어","사모라상",
                 "올해의 수비수","구단 올해의 선수",
                 "FIFA 푸스카스상","대회 최고의 골","리그 올해의 골"]
        summary_parts = []
        for k in order:
            if cnt.get(k):
                summary_parts.append(f"{k} {cnt[k]}회")
        
        frame = QFrame()
        fl = QVBoxLayout(frame); fl.setContentsMargins(0,0,0,0)
        
        if summary_parts:
            sl = QLabel("  ·  ".join(summary_parts))
            sl.setStyleSheet("color:#ffcc00;font-size:14px;font-weight:bold;padding:6px;")
            fl.addWidget(sl)
        
        cols = ["연도","수상","리그","상세"]
        tbl  = self._make_table(len(awards), cols)
        icon = {"득점왕":"⚽","도움왕":"🎯","베스트11":"⭐","MVP":"🏅",
                "발롱도르":"🏆","영플레이어":"🌟","골든글러브":"🧤","사모라상":"🛡️",
                "올해의 수비수":"🛡️","구단 올해의 선수":"🎖️"}
        
        for i, a in enumerate(awards):
            atype = a.get("award_type","")
            label = f"{award_icon(atype, icon)} {atype}"
            # 발롱도르/MVP는 황금색, 주요 상은 녹색
            color = "#ffcc00" if atype in ("발롱도르","MVP") else (
                    "#00cc44" if atype in ("득점왕","도움왕","베스트11") else None)
            vals = [str(a.get("year","")), label, a.get("league_name",""), a.get("detail","")]
            for j, v in enumerate(vals):
                self._set_item_colored(tbl, i, j, v, color if j == 1 else None)
        
        fl.addWidget(tbl)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(awards), 7) * 28)
        return frame

    def _promo_table(self, promos):
        if not promos:
            lbl = QLabel("승강 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        cols = ["기간","팀/국가","대회","결과"]
        tbl  = self._make_table(len(promos), cols)
        for i, t in enumerate(promos):
            ft = t.get("from_tier", 0); tt = t.get("to_tier", 0)
            result = f"{ft}부 → {tt}부"
            lname  = t.get("league_name","")
            comp   = f"{lname} ({ft}부)" if ft else lname
            for j, v in enumerate([str(t.get("year","")), t.get("team_name",""),
                                    comp, result]):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(promos), 5) * 28)
        return tbl

    def _intl_table(self, matches, p):
        """국제전(A매치) 경기별 기록 테이블 (포지션별 세부 지표)."""
        if not matches:
            lbl = QLabel("국제전 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        from constants import position_group
        _pos = p.get("position", "")
        _grp = position_group(_pos)
        if _grp == "GK":
            extra_cols = ["선방", "실점"]
        elif _grp == "DEF":
            extra_cols = ["차단", "패스%"]
        elif _pos in ("CM", "CDM", "CAM"):
            extra_cols = ["기회창출", "패스%", "차단"]
        else:
            extra_cols = ["슈팅", "유효", "기회창출", "드리블"]
        cols = (["기간", "포지션", "국가", "대회", "상대", "골", "어시"]
                + extra_cols + ["평점", "스코어", "결과"])
        tbl  = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            _pac = m.get("pass_acc", 0)
            pac  = f"{round(_pac*100)}%" if _pac else "—"
            _emap = {
                "선방": str(m.get("saves", 0)),       "실점": str(m.get("conceded", 0)),
                "차단": str(m.get("blocks", 0)),       "패스%": pac,
                "기회창출": str(m.get("key_passes", 0)), "드리블": str(m.get("dribbles", 0)),
                "슈팅": str(m.get("shots", 0)),        "유효": str(m.get("shots_on", 0)),
            }
            vals = ([m['date'], m["position"],
                     f"{m['nat_flag']}{m['nat']}",
                     f"{m['comp']} {m['stage']}",
                     f"{m['opp_flag']}{m['opp']}",
                     str(m["goals"]), str(m["assists"])]
                    + [_emap.get(c, "—") for c in extra_cols]
                    + [str(m["rating"]), m["score"], format_result_with_absence(m)])
            # [2026-07 재수정, 신민용 지적: "부상이랑 벤치는 다른 상황이다"]
            # absence_reason이 있으면 부상/출전정지, 없는데 my_played=0이면
            # 벤치 — 스탯 칸들을 전부 "—"로, 평점 칸은 사유로 덮어쓴다.
            # [2026-07 재수정, 신민용 리포트: "red_card는 실제로 뛴 경기라
            # 결장 취급하면 안 된다"] injury/suspension만 완전 결장으로
            # 스탯을 가리고, red_card는 실제 스탯이 있으니 그대로 둔다.
            _reason_label = None
            if m.get("absence_reason") in _FULL_ABSENCE_REASONS:
                _reason_label = _ABSENCE_LABEL.get(m["absence_reason"], m["absence_reason"])
            elif not m.get("my_played", 1) and not m.get("absence_reason"):
                _reason_label = "벤치"
            if _reason_label:
                vals = list(vals)
                vals[5] = "—"; vals[6] = "—"                    # 골/어시
                for _k in range(7, 7 + len(extra_cols)):
                    vals[_k] = "—"
                vals[7 + len(extra_cols)] = _reason_label        # 평점 칸
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(matches), 7) * 28)
        return tbl

    def _champions_table(self, matches, p, label="챔피언스리그"):
        """챔피언스리그 경기별 기록 테이블 (포지션별 세부 지표).
        [2026-08 확장] label만 매개변수화 — 유로파/컨퍼런스도 이 함수를
        그대로 재사용한다(matches 자체가 이미 각 엔진의 get_my_*_matches()로
        만들어진 동일 형식 dict라 나머지 로직은 손댈 필요가 없다)."""
        if not matches:
            lbl = QLabel(f"{label} 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        from constants import position_group
        _pos = p.get("position", "")
        _grp = position_group(_pos)
        if _grp == "GK":
            extra_cols = ["선방", "실점"]
        elif _grp == "DEF":
            extra_cols = ["차단", "패스%"]
        elif _pos in ("CM", "CDM", "CAM"):
            extra_cols = ["기회창출", "패스%", "차단"]
        else:
            extra_cols = ["슈팅", "유효", "기회창출", "드리블"]
        cols = (["기간", "포지션", "소속팀", "대회", "상대", "골", "어시"]
                + extra_cols + ["평점", "스코어", "결과"])
        tbl  = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            _pac = m.get("pass_acc", 0)
            pac  = f"{round(_pac*100)}%" if _pac else "—"
            _emap = {
                "선방": str(m.get("saves", 0)),       "실점": str(m.get("conceded", 0)),
                "차단": str(m.get("blocks", 0)),       "패스%": pac,
                "기회창출": str(m.get("key_passes", 0)), "드리블": str(m.get("dribbles", 0)),
                "슈팅": str(m.get("shots", 0)),        "유효": str(m.get("shots_on", 0)),
            }
            vals = ([m['date'], m["position"],
                     f"{m['team_flag']}{m['team']}",
                     f"{m['comp']} {m['stage']}",
                     f"{m['opp_flag']}{m['opp']}",
                     str(m["goals"]), str(m["assists"])]
                    + [_emap.get(c, "—") for c in extra_cols]
                    + [str(m["rating"]), m["score"], format_result_with_absence(m)])
            # [2026-07 신설, 신민용 지적: "부상이랑 벤치는 다른 상황이다"]
            # [2026-07 재수정, 신민용 리포트: "red_card는 실제로 뛴 경기라
            # 결장 취급하면 안 된다"] injury/suspension만 완전 결장으로
            # 스탯을 가리고, red_card는 실제 스탯이 있으니 그대로 둔다.
            _reason_label = None
            if m.get("absence_reason") in _FULL_ABSENCE_REASONS:
                _reason_label = _ABSENCE_LABEL.get(m["absence_reason"], m["absence_reason"])
            elif not m.get("my_played", 1) and not m.get("absence_reason"):
                _reason_label = "벤치"
            if _reason_label:
                vals = list(vals)
                vals[5] = "—"; vals[6] = "—"
                for _k in range(7, 7 + len(extra_cols)):
                    vals[_k] = "—"
                vals[7 + len(extra_cols)] = _reason_label
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(matches), 7) * 28)
        return tbl

    def _cwc_table(self, matches):
        """[2026-07 신설] 클럽 월드컵 경기별 기록 테이블 (cup_table과 동일 톤)."""
        if not matches:
            lbl = QLabel("클럽 월드컵 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        cols = ["기간", "포지션", "대회", "상대", "골", "어시", "선방", "실점", "평점", "스코어", "결과"]
        tbl = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            vals = [m['date'], m.get("position", ""), f"{m['comp']} {m['stage']}", m["opp"],
                    str(m["goals"]), str(m["assists"]), str(m["saves"]), str(m["conceded"]),
                    str(m["rating"]), m["score"], format_result_with_absence(m)]
            # [2026-07 재수정, 신민용 리포트: "red_card는 실제로 뛴 경기라
            # 결장 취급하면 안 된다"] injury/suspension만 완전 결장으로
            # 스탯을 가리고, red_card는 실제 스탯이 있으니 그대로 둔다.
            _reason_label = None
            if m.get("absence_reason") in _FULL_ABSENCE_REASONS:
                _reason_label = _ABSENCE_LABEL.get(m["absence_reason"], m["absence_reason"])
            elif not m.get("my_played", 1) and not m.get("absence_reason"):
                _reason_label = "벤치"
            if _reason_label:
                vals[4] = "—"; vals[5] = "—"; vals[6] = "—"; vals[7] = "—"
                vals[8] = _reason_label
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(matches), 7) * 28)
        return tbl

    def _po_table(self, matches, p):
        """[2026-08 수정, 신민용 리포트: "승강 플레이오프도 챔스처럼 슈팅/
        유효/기회창출/드리블이 떠야 한다"] po_history에 스탯 컬럼이 채워지므로
        _champions_table과 동일한 포지션별 extra_cols 패턴을 쓴다."""
        if not matches:
            lbl = QLabel("승강 플레이오프 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        from constants import position_group
        _pos = p.get("position", "")
        _grp = position_group(_pos)
        if _grp == "GK":
            extra_cols = ["선방", "실점"]
        elif _grp == "DEF":
            extra_cols = ["차단", "패스%"]
        elif _pos in ("CM", "CDM", "CAM"):
            extra_cols = ["기회창출", "패스%", "차단"]
        else:
            extra_cols = ["슈팅", "유효", "기회창출", "드리블"]
        cols = (["연도", "포지션", "우리 팀", "상대", "골", "어시"]
                + extra_cols + ["평점", "스코어", "결과"])
        tbl = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            _pac = m.get("pass_acc", 0)
            pac = f"{round(_pac*100)}%" if _pac else "—"
            _emap = {
                "선방": str(m.get("saves", 0)), "실점": str(m.get("conceded", 0)),
                "차단": str(m.get("blocks", 0)), "패스%": pac,
                "기회창출": str(m.get("key_passes", 0)), "드리블": str(m.get("dribbles", 0)),
                "슈팅": str(m.get("shots", 0)), "유효": str(m.get("shots_on", 0)),
            }
            vals = ([str(m["year"]), m.get("position", ""), m["team_name"], m["opp_name"],
                    str(m["goals"]), str(m["assists"])]
                    + [_emap.get(c, "—") for c in extra_cols]
                    + [str(m["rating"]), m.get("score", "") or "—", m["result"]])
            if m.get("absence_reason"):
                _reason_label = _ABSENCE_LABEL.get(m["absence_reason"], m["absence_reason"])
                vals = list(vals)
                vals[4] = "—"; vals[5] = "—"
                for _k in range(6, 6 + len(extra_cols)):
                    vals[_k] = "—"
                vals[6 + len(extra_cols)] = _reason_label
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(matches), 7) * 28)
        return tbl

    def _cup_table(self, matches):
        """[2026-07 신설] 국내 컵대회 경기별 기록 테이블. cup_matches는
        슈팅/패스% 등 세부 스탯이 없어 골/어시/선방/평점 중심으로 표시한다."""
        if not matches:
            lbl = QLabel("컵대회 기록 없음"); lbl.setStyleSheet("color:#555;")
            return lbl
        cols = ["기간", "대회", "상대", "골", "어시", "선방", "실점", "평점", "스코어", "결과"]
        tbl = self._make_table(len(matches), cols)
        for i, m in enumerate(matches):
            opp = m["opp"] + (f" ({m['opp_tier']}부)" if m.get("opp_tier") else "")
            vals = [m['date'], f"{m['comp']} {m['stage']}", opp,
                    str(m["goals"]), str(m["assists"]), str(m["saves"]), str(m["conceded"]),
                    str(m["rating"]), m["score"], format_result_with_absence(m)]
            # [2026-07 재수정, 신민용 리포트: "red_card는 실제로 뛴 경기라
            # 결장 취급하면 안 된다"] injury/suspension만 완전 결장으로
            # 스탯을 가리고, red_card는 실제 스탯이 있으니 그대로 둔다.
            _reason_label = None
            if m.get("absence_reason") in _FULL_ABSENCE_REASONS:
                _reason_label = _ABSENCE_LABEL.get(m["absence_reason"], m["absence_reason"])
            elif not m.get("my_played", 1) and not m.get("absence_reason"):
                _reason_label = "벤치"
            if _reason_label:
                vals[3] = "—"; vals[4] = "—"; vals[5] = "—"; vals[6] = "—"
                vals[7] = _reason_label
            for j, v in enumerate(vals):
                self._set_item(tbl, i, j, v)
        tbl.resizeColumnsToContents()
        tbl.resizeRowsToContents()
        tbl.setFixedHeight(30 + min(len(matches), 7) * 28)
        return tbl

    def _make_table(self, rows, cols):
        tbl = QTableWidget(rows, len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        # 모든 컬럼 내용에 맞게 자동 조정
        for i in range(len(cols)):
            tbl.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        tbl.setStyleSheet("QTableWidget{background:#1e1e1e;color:#ccc;"
                          "gridline-color:#2a2a2a;border:none;}"
                          "QHeaderView::section{background:#252525;color:#888;border:none;padding:4px;}"
                          "QTableWidget::item{padding:4px 8px;}")
        return tbl

    def _set_item(self, tbl, row, col, val):
        item = QTableWidgetItem(str(val))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        tbl.setItem(row, col, item)

    # ── AI 스토리 ─────────────────────────────────────

    def _gen_story(self):
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText("⏳ 생성 중...")

        p = get_player()
        conn = get_conn()
        entries  = [dict(r) for r in conn.execute("SELECT * FROM career_entries ORDER BY id").fetchall()]
        # [2026-07 버그+성능 수정] 위 _open()과 동일한 이유로 get_my_trophies()
        # 사용. 개인 수상은 별도의 awards 리스트로 이미 전달되므로 story
        # 생성 내용엔 영향 없다.
        from game_engine import get_my_trophies
        trophies = get_my_trophies()
        try:
            awards = [dict(r) for r in conn.execute(
                "SELECT * FROM awards WHERE is_mine=1 ORDER BY year").fetchall()]
        except Exception:
            awards = []
        conn.close()

        lines = []
        lines.append(f"【 {p['name']} 선수 커리어 요약 】")
        from intl_engine import fmt_nationalities, fmt_rep_nationality
        _nats = fmt_nationalities(p) or f"{p.get('flag','')} {p['nationality']}"
        _rep  = fmt_rep_nationality(p)
        # [출생국적] 태어난 고향 — 대표국적과 다르면 별도 표시(디에고 코스타 케이스)
        _origin_nat  = p.get("origin_nat", "") or p.get("nationality", "")
        _origin_flag = p.get("origin_flag", "") or p.get("flag", "")
        lines.append(f"국적: {_nats}  |  🏠출생: {_origin_flag}{_origin_nat}  "
                     f"|  ⚽대표: {_rep}  |  포지션: {p['position']} ({p.get('sub_role','')})")
        _final_ovr = p.get('ovr', 0)
        lines.append(f"성격: {p.get('personality','')}  |  특징: {p.get('physical_trait','무난함')}  |  은퇴 나이: {p['age']}세  |  최종 OVR: {_final_ovr}")
        lines.append("")

        # ── [국적 연혁] 출생 → 귀화 → 대표선택 시간순 이력 ──────────
        try:
            from game_engine import get_nat_history
            _nat_hist = get_nat_history(p)
        except Exception:
            _nat_hist = []
        if _nat_hist:
            lines.append("▶ 국적 연혁")
            # birth(출생) 먼저, 그 뒤 시간순(year,week)으로 귀화/대표선택
            _births = [h for h in _nat_hist if h.get("type") == "birth"]
            _events = sorted(
                [h for h in _nat_hist if h.get("type") != "birth"],
                key=lambda h: (h.get("year", 0), h.get("week", 0)))
            if _births:
                _start = _births[0]
                _born_str = f"{_start.get('flag','')}{_start.get('nat','')}"
                if len(_births) > 1:
                    _extra = " / ".join(f"{b.get('flag','')}{b.get('nat','')}"
                                        for b in _births[1:])
                    lines.append(f"  🏠 출생 국적: {_born_str}  (복수국적 보유: {_extra})")
                else:
                    lines.append(f"  🏠 출생 국적: {_born_str}")
            for h in _events:
                _t = h.get("type")
                _ns = f"{h.get('flag','')}{h.get('nat','')}"
                _yr = h.get("year", "")
                if _t == "naturalize":
                    lines.append(f"  🛂 {_yr}년  {_ns} 귀화 국적 획득")
                elif _t == "commit":
                    lines.append(f"  ⚽ {_yr}년  {_ns} 대표팀 선택 (평생 대표국 확정)")
            lines.append("")

        # 팀 이력 — 화면 테이블의 셀 값을 그대로 나열 (산문체 대신 정형 데이터)
        lines.append("▶ 팀 이력")
        if entries:
            from constants import position_group
            for idx, e in enumerate(entries):
                sy = e.get("start_year",""); sw = e.get("start_week",1)
                ey = e.get("end_year","");   ew = e.get("end_week",52)
                from constants import week_to_iso_date_str, week_to_iso_date_str_end
                start_str = week_to_iso_date_str(sy, sw) if sy else ""
                end_str = week_to_iso_date_str_end(ey, ew) if ey else ""
                period = f"{start_str} ~ {end_str}"
                pos = e.get("position","")
                grp = position_group(pos)
                m   = e.get("matches",0)
                sv  = e.get("saves",0); ga = e.get("goals_against",0)
                cs  = e.get("clean_sheets",0); blk = e.get("blocks",0)
                kp  = e.get("key_passes",0); drb = e.get("dribbles",0)
                sh  = e.get("shots",0); sho = e.get("shots_on",0)
                g   = e.get("goals",0); a = e.get("assists",0)
                _pac = e.get("pass_acc",0)
                pac  = f"{round(_pac*100)}%" if _pac else "—"
                save_rate = f"{round(sv/(sv+ga)*100,1)}%" if (sv+ga) > 0 else "—"
                ar  = e.get("avg_rating",0)
                avg = round(ar,1) if ar else "—"
                rc  = e.get("season_rating_cnt",0); rs = e.get("season_rating_sum",0) or ar
                avg2 = round(rs/rc,1) if rc > 0 else avg
                rank_disp = f"{e.get('team_rank',0)}위" if m > 0 else "—"
                wdl = f"{e.get('wins',0)}승{e.get('draws',0)}무{e.get('losses',0)}패" if m > 0 else "—"
                lg  = e.get("league_name","")
                # [버그수정] 예전엔 리그명 앞 2글자(예: "K4리그"→"K4")를 국가처럼
                # 잘못 표시했다 — 실제 국가명 조회로 교체하고, 리그명에도
                # career_window.py와 동일하게 (n부) 접미사를 붙인다.
                from game_engine import league_total_teams_by_name
                # [2026-07 재수정, 신민용 리포트: "세리에 A인데 국가가
                # 브라질로 뜬다"] 리그명만으로 조회하면 이탈리아 세리에 A와
                # 브라질 세리에 A(브라질레이랑 통칭)처럼 리그명이 같은
                # 나라가 있을 때 엉뚱한 나라가 나온다 — team_id로 직접
                # 조회(팀은 나라를 안 바꾸므로 승강으로 tier가 바뀌어도
                # 국가 조회는 항상 안전하다).
                _tid_for_country = e.get("team_id")
                _country_row = get_conn().execute(
                    """SELECT cn.flag, cn.name as cname FROM teams t
                       JOIN leagues l ON t.league_id=l.id
                       JOIN countries cn ON l.country_id=cn.id WHERE t.id=? LIMIT 1""",
                    (_tid_for_country,)).fetchone() if _tid_for_country else None
                nation = f"{_country_row['flag']} {_country_row['cname']}" if _country_row else "—"
                lg_disp = f"{lg} ({e.get('tier','')}부)" if lg else "—"
                if m > 0:
                    _total_teams = league_total_teams_by_name(lg)
                    if _total_teams:
                        rank_disp = f"{e.get('team_rank',0)}위/{_total_teams}팀"
                salary = fmt_money(e.get("salary",0))
                # 계약/이적
                c_yrs = e.get("contract_years",0)
                exit_t = e.get("exit_type",""); in_type = e.get("transfer_type","입단")
                # [2026-07 신설, career_window.py와 동일한 신민용 리포트 반영]
                # 들어온 경로가 강제이적(팔림)이면 "구매"로 구분 표시.
                if in_type == "팔림":
                    in_type = "구매"
                # [2026-08 신설, career_window.py/팀이력 탭과 동일한 신민용
                # 지적 반영] AI요약도 팀이력 탭과 똑같이 나가는 쪽 "팔림"
                # 표시는 없애고 들어오는 쪽 "구매"만 남긴다.
                _exit_disp = "" if exit_t == "팔림" else exit_t
                # [2026-08 버그수정, 신민용 리포트: "유료로 들어왔다가 곧장
                # 임대 나간 경우, 임대 표시가 이적료 표시를 덮어써서 이적료가
                # 아예 안 보인다 — 임대는 받는 팀 행에만 표시하면 되는데"]
                # exit_t가 "임대"(원소속팀에서 나가는 행)인데 이 행 자체가
                # 유료로 들어온 행(이적/오퍼/구매 + 실제 이적료 있음)이면,
                # "여기로 들어온 이유(이적료 포함)"가 "여기서 나간 이유
                # (임대)"보다 더 중요한 정보다 — 임대 나간 사실 자체는
                # 어차피 받는 팀(다음 행)에서 "임대(진행중)"으로 보이므로
                # 여기서 또 보여줄 필요가 없다.
                if (exit_t == "임대" and in_type in ("이적", "오퍼", "구매")
                        and e.get("transfer_fee", 0)):
                    _exit_disp = ""
                    exit_t = ""
                t_type = _exit_disp if _exit_disp else in_type
                # [2026-07 재수정, 신민용 지적: "은퇴창이랑 AI요약도 마찬가지로
                # 떠야지"] career_window.py와 동일하게 계약 컬럼은 원소속팀
                # 계약년수를 그대로 보여주고, 이적란에 실제 임대 기간을
                # "임대(N개월)"처럼 붙인다.
                if t_type in ("임대", "임대 종료"):
                    # [2026-07 재수정, 신민용 지적: "임대(1년)/임대 종료(1년)만
                    # 뜨면 어느 팀으로 갔는지/어디로 복귀했는지 안 보인다"]
                    _partner = e.get("loan_partner_team", "") or ""
                    if t_type == "임대 종료":
                        # [2026-08 버그수정, 신민용 리포트: "성남 FC에 임대
                        # 왔다는 표시를 2002년 행에도 해줘야 한다"] 이 행
                        # 자체가 임대처에서 뛴 기간이라 이 행의 시작~종료가
                        # 곧 실제 임대 기간이다 — "OO 복귀"만 보여주지 말고
                        # 어디서 얼마나 임대로 왔었는지도 같이 보여준다.
                        if ey:
                            dur = _fmt_loan_months(max(1, (ey - sy) * 52 + (ew - sw)))
                            t_type = f"{_partner}에서 임대({dur}) 후 복귀" if _partner else f"임대({dur}) 후 복귀"
                        else:
                            t_type = f"{_partner} 복귀" if _partner else "복귀"
                    else:
                        # [2026-08 버그수정, 신민용 리포트: "임대 기간이
                        # 실제(1년)와 다르게 2개월로 뜬다"] 이 행(원소속팀)의
                        # 시작~종료는 임대 가기 전 원소속팀 재직 기간일 뿐이라
                        # 부정확하다 — 목적지 팀에서 뛴 실제 기간(뒤에 나오는
                        # 그 팀 행)으로 계산한다.
                        dur = _loan_out_duration_str(entries, idx, _partner, sy, sw)
                        t_type = f"{_partner}에 임대({dur})" if _partner else f"임대({dur})"
                # [2026-07 신설, 신민용 지적: "구매/팔림에 금액이 안 보인다 —
                # 파는 쪽(팔림)엔 표시 안 하고 사는 쪽(구매/오퍼)에만
                # 금액을 붙여야 한다"] 이 행이 "내가 이 팀에 들어온"
                # 이벤트를 보여줄 때만(=exit_t가 비어서 in_type이 최종
                # 채택됐을 때만) 이적료를 붙인다 — exit_t가 채택된 경우
                # (팔림/방출/계약만료 등, "이 팀을 떠난" 이벤트)는 그
                # 팀에서의 이적료가 아니라 다음 팀 행에 이미 붙으므로
                # 여기서는 표시하지 않는다.
                if not exit_t:
                    _fee_disp = e.get("transfer_fee", 0)
                    if _fee_disp:
                        t_type = f"{t_type} ({fmt_money(_fee_disp)})"
                if in_type == "임대" or idx == 0 or e.get("team_name") != entries[idx-1].get("team_name"):
                    c_str = f"{c_yrs}년" if c_yrs else "—"
                elif in_type == "연장" or t_type == "연장":
                    c_str = f"{c_yrs}년" if c_yrs else "—"
                else:
                    c_str = "—"

                # 포지션별 스탯 컬럼 (테이블과 동일)
                if grp == "GK":
                    stat_pairs = [("골",g),("어시",a),("선방",sv),("실점",ga),
                                  ("선방률",save_rate),("무실점",cs)]
                elif grp == "DEF":
                    stat_pairs = [("골",g),("어시",a),("무실점",cs),("차단",blk),
                                  ("패스%",pac),("평점기여",avg)]
                elif pos in ("CM","CDM","CAM"):
                    stat_pairs = [("골",g),("어시",a),("기회창출",kp),("패스%",pac),
                                  ("차단",blk),("드리블",drb)]
                else:
                    stat_pairs = [("골",g),("어시",a),("슈팅",sh),("유효",sho),
                                  ("기회창출",kp),("드리블",drb)]

                # 한 줄: 기간 | 포지션 | 국가 | 리그 | 팀명 | 연봉 | 출전 | [스탯] | 평점 | 순위 | 승무패 | 계약 | 이적
                # [2026-07 추가] 리그마다 풀시즌 경기 수가 다르니 분모도 같이 표시.
                # [버그수정 2026-07] 분모를 그 리그 풀시즌 전체 경기 수로 쓰면
                # 시즌 중 이적한 스탠트는 실제로 그 팀에 없던 기간의 경기까지
                # 분모에 들어가 출전율이 왜곡된다 — 그 팀 소속 기간 동안
                # 실제로 열린 경기 수로 바꾼다(team_matches_played_in_window).
                from game_engine import team_matches_played_in_window
                _total_g = team_matches_played_in_window(e.get("team_id", 0), lg, sy, sw, ey, ew)
                _apps_str = f"{m}/{_total_g}" if _total_g else str(m)
                head = (f"  • {period} | {pos} | {nation} | {lg_disp} | "
                        f"{e.get('team_name','')} | {salary} | 출전 {_apps_str}")
                lines.append(head)
                if m > 0:
                    stat_str = "  ".join(f"{k} {v}" for k, v in stat_pairs)
                    lines.append(f"    {stat_str}  | 평점 {avg2} | {rank_disp} | {wdl} | 계약 {c_str} | {t_type}")
                else:
                    lines.append(f"    출전 0경기 (입단만, 출전 없음)  | 계약 {c_str} | {t_type}")
                # [2026-07 신설 → 재작성, 신민용 지적: "전체 이력도 포지션
                # 마다 다르게, 출전도 분모 포함해서" — career_window.py/
                # retire_window.py의 "전체 이력" 표와 동일 로직.]
                from game_engine import get_full_history_extras_for_period, team_matches_played_in_window
                # [2026-07 버그수정, 위와 동일한 원인/수정]
                _extras3_end_year = ey if ey else get_state().get("current_year", sy or 0)
                _extras3 = get_full_history_extras_for_period(
                    e.get("team_id", 0), p.get("nationality", ""), sy or 0, _extras3_end_year)
                _league_total3 = team_matches_played_in_window(
                    e.get("team_id", 0), lg, sy, sw, ey, ew) or 0
                _grand_played = m + _extras3["matches_played"]
                _grand_avail = _league_total3 + _extras3["matches_available"]
                _apps_str3 = f"{_grand_played}/{_grand_avail}" if _grand_avail else str(_grand_played)
                _grand_g = g + _extras3["goals"]
                _grand_a = a + _extras3["assists"]
                # [2026-08 신설, 신민용 리포트: "전체 이력엔 그 해 컵대회/
                # 챔스/월드컵 등 대회 레드카드가 안 잡힌다"] 이 팀 재직기간의
                # career_entries.red_cards(리그 전용) + extras3["red_cards"]
                # (컵+챔스+클럽월드컵+국가대표) 합산.
                _grand_rc = e.get("red_cards", 0) + _extras3["red_cards"]
                if grp == "GK":
                    _grand_sv = sv + _extras3["saves"]
                    _grand_ga = ga + _extras3["goals_against"]
                    _grand_cs = cs + _extras3["clean_sheets"]
                    lines.append(f"    └ 전체 이력(리그+컵+챔스+클럽WC+국가대표): "
                                 f"출전 {_apps_str3}  {_grand_g}골 {_grand_a}어시  "
                                 f"선방 {_grand_sv}  실점 {_grand_ga}  CS {_grand_cs}"
                                 + (f"  🟥{_grand_rc}" if _grand_rc else ""))
                else:
                    # [2026-07 신설, 신민용 요청: "테이블 컬럼에 추가해서 ㄱㄱ"]
                    # cup/cwc에도 세부 스탯이 저장되게 고쳐서 이제 실제 값 표시.
                    _grand_sh = sh + _extras3["shots"]
                    _grand_sho = sho + _extras3["shots_on"]
                    _grand_kp = kp + _extras3["key_passes"]
                    _grand_drb = drb + _extras3["dribbles"]
                    lines.append(f"    └ 전체 이력(리그+컵+챔스+클럽WC+국가대표): "
                                 f"출전 {_apps_str3}  {_grand_g}골 {_grand_a}어시  "
                                 f"슈팅 {_grand_sh}  유효 {_grand_sho}  "
                                 f"기회창출 {_grand_kp}  드리블 {_grand_drb}"
                                 + (f"  🟥{_grand_rc}" if _grand_rc else ""))
                # 역할/감독/구단야망
                ctx = []
                if e.get("contract_role"):  ctx.append(f"역할 {e['contract_role']}")
                if e.get("manager_type"):   ctx.append(f"감독 {e['manager_type']}")
                if e.get("club_ambition"):  ctx.append(f"구단목표 {e['club_ambition']}")
                if ctx:
                    lines.append("    └ " + "  ·  ".join(ctx))
        else:
            lines.append("  기록 없음")
        lines.append("")

        # 팀 우승 (리그 우승 ─ tier>0). 챔스(tier=-1)/국가대표(tier=0)는 별도.
        league_trophies = [t for t in trophies if t.get('tier', 0) > 0]
        lines.append(f"▶ 성적  ({len(league_trophies)}건)")
        if league_trophies:
            for t in league_trophies:
                comp   = t.get('competition', '')
                nation = t.get('team_name', '')
                lines.append(f"  🏆 {t.get('year','')}년  {comp}  ({nation})")
        else:
            lines.append("  없음")
        lines.append("")

        # 클럽 대항전 경력 (챔스/유로파/컨퍼런스 전부 tier=-1로 저장 ─ 대회별 결과 + 활약)
        cl_trophies = [t for t in trophies if t.get('tier', 0) == -1]
        lines.append(f"▶ 클럽 대항전 경력  ({len(cl_trophies)}건)")
        if cl_trophies:
            conn_c = get_conn()
            clhist = {}
            for _htbl in ("cl_history", "el_history", "ecl_history"):
                try:
                    clhist.update({(r["year"], r["competition"]): dict(r) for r in conn_c.execute(
                        f"SELECT * FROM {_htbl}").fetchall()})
                except Exception:
                    pass
            conn_c.close()
            for t in cl_trophies:
                yr, comp = t.get('year', 0), t.get('competition', '')
                result   = t.get('league_name', '')   # league_name 자리에 결과 저장됨
                team     = t.get('team_name', '')
                _ic = ("🏆" if result == "우승" else
                       "🥈" if result == "준우승" else
                       "🥉" if result == "3위" else
                       "4️⃣" if result == "4위" else
                       "⚔️" if result == "8강" else
                       "🔵" if result == "16강" else
                       "🟣" if result == "32강" else
                       "❌" if result in ("국가대표 미선발","예선 탈락","예선 진출 실패") else "▫")
                line = f"  {_ic} {yr}년  {comp}  →  {result}  ({team})"
                ch = clhist.get((yr, comp))
                if ch and ch.get("caps", 0) > 0:
                    if p.get("position") == "GK":
                        line += f"  | {ch['caps']}경기 출전, 평점 {ch.get('rating', 0)}"
                    else:
                        line += (f"  | {ch['caps']}경기 {ch.get('goals',0)}골 "
                                 f"{ch.get('assists',0)}어시, 평점 {ch.get('rating', 0)}")
                lines.append(line)
        else:
            lines.append("  없음")
        lines.append("")

        # 컵대회 경력 (국내 컵대회 ─ tier=-2, 대회별 결과 + 활약)
        cup_trophies = [t for t in trophies if t.get('tier', 0) == -2]
        lines.append(f"▶ 컵대회 경력  ({len(cup_trophies)}건)")
        if cup_trophies:
            conn_u = get_conn()
            try:
                cuphist = {(r["year"], r["team_name"]): dict(r) for r in conn_u.execute(
                    "SELECT * FROM cup_history").fetchall()}
            except Exception:
                cuphist = {}
            conn_u.close()
            for t in cup_trophies:
                yr, comp = t.get('year', 0), t.get('competition', '')
                result   = t.get('league_name', '')   # league_name 자리에 결과 저장됨
                team     = t.get('team_name', '')
                _ic = ("🏆" if result == "우승" else
                       "🥈" if result == "준우승" else
                       "🥉" if result == "3위" else
                       "4️⃣" if result == "4위" else "🎖️")
                line = f"  {_ic} {yr}년  {comp}  →  {result}  ({team})"
                ch = cuphist.get((yr, team))
                if ch and ch.get("caps", 0) > 0:
                    line += (f"  | {ch['caps']}경기 {ch.get('goals',0)}골 "
                             f"{ch.get('assists',0)}어시, 평점 {ch.get('rating', 0)}")
                lines.append(line)
        else:
            lines.append("  없음")
        lines.append("")

        # 개인 영예 (득점왕/베스트11/발롱도르 등)
        lines.append(f"▶ 개인 영예  ({len(awards)}건)")
        if awards:
            from collections import Counter
            from constants import normalize_award_bucket, award_icon
            cnt = Counter(normalize_award_bucket(a.get("award_type","")) for a in awards)
            order = ["발롱도르","MVP","득점왕","도움왕","베스트11","골든글러브","영플레이어",
                     "올해의 수비수","구단 올해의 선수",
                     "FIFA 푸스카스상","대회 최고의 골","리그 올해의 골"]
            summ = [f"{k} {cnt[k]}회" for k in order if cnt.get(k)]
            if summ:
                lines.append("  ★ " + "  ·  ".join(summ))
            icon = {"득점왕":"⚽","도움왕":"🎯","베스트11":"⭐","MVP":"🏅",
                    "발롱도르":"🏆","영플레이어":"🌟","골든글러브":"🧤",
                    "올해의 수비수":"🛡️","구단 올해의 선수":"🎖️"}
            for a in awards:
                at = a.get("award_type","")
                lines.append(f"  {award_icon(at, icon)} {a.get('year','')}년  {at}  "
                             f"({a.get('league_name','')}, {a.get('detail','')})")
        else:
            lines.append("  없음")
        lines.append("")

        # 국가대표 경력 (월드컵/대륙컵 ─ 대회별 결과 + 활약상)
        intl_trophies = [t for t in trophies if t.get('tier', 0) == 0]
        lines.append(f"▶ 국가대표 경력  ({len(intl_trophies)}건)")
        if intl_trophies:
            conn_i = get_conn()
            hist = {(r["year"], r["competition"]): dict(r) for r in conn_i.execute(
                "SELECT * FROM intl_history").fetchall()}
            conn_i.close()
            for t in intl_trophies:
                yr, comp = t.get('year', 0), t.get('competition', '')
                result   = t.get('league_name', '')
                nation   = t.get('team_name', '')
                # [거절 기록] '발탁 거절'은 출전 기록이 아니라 거절 이력이므로
                #   별도 아이콘(🚫)으로 구분해 표시한다.
                if result == "발탁 거절":
                    lines.append(f"  🚫 {yr}년  {comp}  →  발탁 거절  ({nation})")
                    continue
                _ic_i = ("🏆" if result == "우승" else
                         "🥈" if result == "준우승" else
                         "🥉" if result == "3위" else
                         "4️⃣" if result == "4위" else
                         "⚔️" if result == "8강" else
                         "🔵" if result == "16강" else
                         "🟣" if result == "32강" else
                       "❌" if result in ("국가대표 미선발","예선 탈락","예선 진출 실패") else "🌍")
                line = f"  {_ic_i} {yr}년  {comp}  →  {result}  ({nation})"
                ih = hist.get((yr, comp))
                if ih and ih.get("caps", 0) > 0:
                    if p.get("position") == "GK":
                        line += f"  | {ih['caps']}경기 출전, 평점 {ih.get('rating', 0)}"
                    else:
                        line += (f"  | {ih['caps']}경기 {ih.get('goals',0)}골 "
                                 f"{ih.get('assists',0)}어시, 평점 {ih.get('rating', 0)}")
                lines.append(line)
        else:
            lines.append("  없음")
        lines.append("")

        # 국제전 기록 (A매치 경기 단위 ─ 상대/활약/스코어/결과)
        # [2026-08 성능 수정] 캐시된 결과 재사용 (_get_career_matches 참고).
        _cm2 = self._get_career_matches()
        intl_ms = _cm2["intl_ms"]
        lines.append(f"▶ 국제전 기록  ({len(intl_ms)}경기)")
        if intl_ms:
            for im in intl_ms:
                lines.append(f"  • {im['date']}  "
                             f"{im['comp']} {im['stage']}  vs {im['opp']}  ─  "
                             f"{_match_line_str(im)}  ({im['score']} {format_result_with_absence(im)})")
        else:
            lines.append("  없음")
        lines.append("")

        # 국제전(예선) 기록
        qual_ms2 = _cm2["qual_ms"]
        if qual_ms2:
            lines.append(f"▶ 국제전(예선) 기록  ({len(qual_ms2)}경기)")
            for qm in qual_ms2:
                lines.append(f"  • {qm['date']}  "
                             f"{qm['comp']} {qm['stage']}  vs {qm['opp']}  ─  "
                             f"{_match_line_str(qm)}  ({qm['score']} {format_result_with_absence(qm)})")
            lines.append("")

        # 챔피언스리그 기록 (클럽 대륙 대회 경기 단위 ─ A매치 아님, 클럽 출전)
        cl_ms2 = _cm2["cl_ms"]
        lines.append(f"▶ 챔피언스리그 기록  ({len(cl_ms2)}경기)  ※ 클럽 대항전 (A매치 아님)")
        if cl_ms2:
            for cm in cl_ms2:
                lines.append(f"  • {cm['date']}  "
                             f"{cm['comp']} {cm['stage']}  ({cm['team']}) vs {cm['opp']}  ─  "
                             f"{_match_line_str(cm)}  ({cm['score']} {format_result_with_absence(cm)})")
        else:
            lines.append("  없음")
        lines.append("")

        # 유로파리그 기록 (2026-08 신설)
        el_ms2 = _cm2["el_ms"]
        lines.append(f"▶ 유로파리그 기록  ({len(el_ms2)}경기)  ※ 클럽 대항전 (A매치 아님)")
        if el_ms2:
            for em in el_ms2:
                lines.append(f"  • {em['date']}  "
                             f"{em['comp']} {em['stage']}  ({em['team']}) vs {em['opp']}  ─  "
                             f"{_match_line_str(em)}  ({em['score']} {format_result_with_absence(em)})")
        else:
            lines.append("  없음")
        lines.append("")

        # 컨퍼런스리그 기록 (2026-08 신설)
        ecl_ms2 = _cm2["ecl_ms"]
        lines.append(f"▶ 컨퍼런스리그 기록  ({len(ecl_ms2)}경기)  ※ 클럽 대항전 (A매치 아님)")
        if ecl_ms2:
            for cm in ecl_ms2:
                lines.append(f"  • {cm['date']}  "
                             f"{cm['comp']} {cm['stage']}  ({cm['team']}) vs {cm['opp']}  ─  "
                             f"{_match_line_str(cm)}  ({cm['score']} {format_result_with_absence(cm)})")
        else:
            lines.append("  없음")
        lines.append("")

        # 슈퍼컵 기록 (2026-08 신설, 14순위)
        sc_ms2 = _cm2["sc_ms"]
        lines.append(f"▶ 슈퍼컵 기록  ({len(sc_ms2)}경기)  ※ 클럽 대항전 (A매치 아님)")
        if sc_ms2:
            for sm in sc_ms2:
                lines.append(f"  • {sm['date']}  "
                             f"{sm['comp']} {sm['stage']}  ({sm['team']}) vs {sm['opp']}  ─  "
                             f"{_match_line_str(sm)}  ({sm['score']} {format_result_with_absence(sm)})")
        else:
            lines.append("  없음")
        lines.append("")

        # 컵대회 기록 (경기 단위 ─ A매치 아님, 클럽 출전)
        cup_ms2 = _cm2["cup_ms"]
        lines.append(f"▶ 컵대회 기록  ({len(cup_ms2)}경기)  ※ 국내 컵대회 (A매치 아님)")
        if cup_ms2:
            for um in cup_ms2:
                opp = um["opp"] + (f" ({um['opp_tier']}부)" if um.get("opp_tier") else "")
                if um.get("absence_reason") or not um.get("my_played", 1):
                    line_body = _match_line_str(um)
                else:
                    line_body = f"{um['goals']}골 {um['assists']}어시  평점 {um['rating']}"
                lines.append(f"  • {um['date']}  "
                             f"{um['comp']} {um['stage']}  vs {opp}  ─  "
                             f"{line_body}  "
                             f"({um['score']} {format_result_with_absence(um)})")
        else:
            lines.append("  없음")
        lines.append("")

        # 클럽 월드컵 기록 (4년에 한 번뿐인 대회 — 경기 단위, A매치 아님)
        cwc_ms2 = _cm2["cwc_ms"]
        if cwc_ms2:
            lines.append(f"▶ 클럽 월드컵 기록  ({len(cwc_ms2)}경기)  ※ 4년 주기 클럽 대항전 (A매치 아님)")
            for wm in cwc_ms2:
                lines.append(f"  • {wm['date']}  "
                             f"{wm['comp']} {wm['stage']}  vs {wm['opp']}  ─  "
                             f"{_match_line_str(wm)}  ({wm['score']} {format_result_with_absence(wm)})")
            lines.append("")

        # 승강 플레이오프 기록 (경기 단위)
        po_ms2 = _cm2["po_ms"]
        lines.append(f"▶ 승강 플레이오프 기록  ({len(po_ms2)}경기)")
        if po_ms2:
            for pm in po_ms2:
                if pm.get("absence_reason"):
                    _reason = _ABSENCE_LABEL.get(pm["absence_reason"], pm["absence_reason"])
                    lines.append(f"  • {pm['year']}년  {pm['team_name']} vs {pm['opp_name']}  ─  {_reason}")
                else:
                    lines.append(f"  • {pm['year']}년  {pm['team_name']} vs {pm['opp_name']}  ─  "
                                 f"{pm['result']}  (골 {pm['goals']} 어시 {pm['assists']} 평점 {pm['rating']})")
        else:
            lines.append("  없음")
        lines.append("")

        # 승강 경험
        from game_engine import get_my_promotions
        promos_s = get_my_promotions()
        lines.append(f"▶ 승강 경험  ({len(promos_s)}건)")
        if promos_s:
            for pr in promos_s:
                ft = pr.get("from_tier", 0); tt = pr.get("to_tier", 0)
                kind = "승격" if tt < ft else "강등"
                icon = "🔼" if tt < ft else "🔽"
                lines.append(f"  {icon} {pr.get('year','')}년  {pr.get('team_name','')}"
                             f"  {pr.get('league_name','')}  {ft}부 → {tt}부 ({kind})")
        else:
            lines.append("  없음")
        lines.append("")

        # 통계 요약
        total_m = p.get("total_matches", 0)
        total_g = p.get("total_goals", 0)
        total_a = p.get("total_assists", 0)
        total_s = p.get("total_seasons", 0)
        pos_txt = p.get("position","")
        lines.append("▶ 통산 기록")
        from constants import position_group
        _grp = position_group(pos_txt)
        # 통산 무실점: my_player에 누적 컬럼이 없으므로 커리어 항목에서 합산
        _total_cs = sum(e.get("clean_sheets", 0) for e in entries)
        if _grp == "GK":
            ts2 = p.get("total_saves",0); tga2 = p.get("total_goals_against",0)
            tot2 = ts2+tga2; sr2 = f"{round(ts2/tot2*100,1)}%" if tot2 else "0%"
            lines.append(f"  {total_s}시즌  {total_m}경기  선방 {ts2}회({sr2})  실점 {tga2}골  무실점 {_total_cs}경기")
        elif _grp == "DEF":
            # 수비수: 무실점 경기 수를 핵심 지표로, 공격 포인트는 보조
            lines.append(f"  {total_s}시즌  {total_m}경기  무실점 {_total_cs}경기  (공격P {total_g}골 {total_a}어시)")
        else:
            lines.append(f"  {total_s}시즌  {total_m}경기  {total_g}골  {total_a}어시스트")
        ic = p.get("intl_caps", 0)
        if ic > 0:
            if pos_txt == "GK":
                lines.append(f"  A매치 {ic}경기 출전")
            else:
                lines.append(f"  A매치 {ic}경기  {p.get('intl_goals',0)}골  {p.get('intl_assists',0)}어시스트")
        lines.append(f"  총 자산: {fmt_money(p.get('total_assets',0))}")

        self.story_box.setPlainText("\n".join(lines))
        self.gen_btn.setText("✨ 다시 요약")
        self.gen_btn.setEnabled(True)

    # ── 스토리(책) 생성 — 로컬 렌더러 / 실제 AI(제미나이) 공용 ──────

    def _gather_story_inputs(self):
        """두 버튼(로컬/AI)이 공유하는 데이터 수집 로직. player, entries,
        trophies, awards, intl_trophies, match_rows를 튜플로 반환한다."""
        p = get_player()
        conn = get_conn(); c = conn.cursor()
        entries = [dict(r) for r in c.execute(
            "SELECT * FROM career_entries ORDER BY id").fetchall()]

        from game_engine import get_my_trophies
        all_trophies = get_my_trophies()
        trophies = [t for t in all_trophies if not _is_personal_award(t)]
        try:
            awards = [dict(r) for r in c.execute(
                "SELECT * FROM awards WHERE is_mine=1 ORDER BY year").fetchall()]
        except Exception:
            awards = []

        # entries에 실제 국가 정보를 채워준다 — story_generator가 리그명
        # 문자열 추측이 아니라 이 country 필드로 '해외 진출/귀국'을
        # 정확히 구분한다 (K리그2 같은 국내 리그명엔 국가 접두어가
        # 없어서 문자열 추측만으로는 오탐이 났었다).
        # [2026-07 버그수정, 신민용 리포트: "10위/12팀처럼 자세히 표시가
        # 안 된다"] story_generator._fill()이 _total_teams 필드를 참조해
        # "{rank}위/{total}팀" 형태로 표시하는데, career_entries 테이블
        # 자체엔 그 리그의 '전체 팀 수'가 저장돼 있지 않다(순위만 저장) —
        # UI(career_window 등)는 화면에 그릴 때마다 그때그때
        # league_total_teams_by_name()으로 따로 계산해서 붙였는데,
        # story_generator로 넘기는 이 경로엔 그 계산이 빠져 있어서 항상
        # "10위"까지만 나오고 "/12팀"이 안 붙었다. country와 같은 자리에서
        # 함께 채운다.
        from game_engine import league_total_teams_by_name
        league_total = {}
        team_country = {}
        for e in entries:
            ln = e.get("league_name", "")
            tid = e.get("team_id")
            if tid and tid not in team_country:
                row = c.execute("""SELECT cn.name as cname FROM teams t
                                    JOIN leagues l ON t.league_id=l.id
                                    JOIN countries cn ON l.country_id=cn.id
                                    WHERE t.id=? LIMIT 1""", (tid,)).fetchone()
                team_country[tid] = row["cname"] if row else ""
            e["country"] = team_country.get(tid, "")
            if ln and ln not in league_total:
                league_total[ln] = league_total_teams_by_name(ln) or 0
            e["_total_teams"] = league_total.get(ln, 0)
        conn.close()

        intl_trophies = [t for t in trophies if t.get("tier", 0) == 0]

        # [2026-07 신설, 커리어 메모리] 경기 단위 기록도 함께 넘긴다 —
        # story_generator가 해트트릭/고평점 경기 같은 걸
        # 참고할 수 있게. 실패해도(구버전 세이브 등) 계속 진행되도록 방어.
        try:
            conn3 = get_conn()
            match_rows = [dict(r) for r in conn3.execute(
                "SELECT * FROM match_details ORDER BY id").fetchall()]
            conn3.close()
        except Exception:
            match_rows = []

        # [2026-07 신설] 컵/챔스/국대 결장 기록에서 "부상으로 결장한 진짜
        # 사유"만 뽑아 story_generator에 넘긴다 — 예전엔 story_generator가
        # '평소보다 적게 뛴 시즌'을 통계로 추측만 했는데, 이 대회들은 이미
        # absence_reason(injury/suspension/...)을 정확히 매겨서 갖고 있으므로
        # 그 확정 근거를 그대로 쓴다. 대회별 조회가 실패해도(구버전 세이브
        # 등) 나머지는 계속 진행되도록 각각 방어한다.
        # [2026-08 성능 수정] 캐시된 결과 재사용 (_get_career_matches 참고).
        absence_events = []
        try:
            _cm3 = self._get_career_matches()
            absence_events += [{"year": m.get("year"), "reason": m.get("absence_reason")}
                                for m in _cm3["cup_ms"] if m.get("absence_reason")]
            absence_events += [{"year": m.get("year"), "reason": m.get("absence_reason")}
                                for m in _cm3["cl_ms"] if m.get("absence_reason")]
            absence_events += [{"year": m.get("year"), "reason": m.get("absence_reason")}
                                for m in _cm3["intl_ms"] if m.get("absence_reason")]
        except Exception:
            pass

        # [2026-07 버그수정, 신민용 리포트: "2002년 삼성 FC가 실제로는
        # 강등 안 했는데 스토리엔 강등했다고 나온다"] story_generator가
        # 강등 여부를 순위 비율만으로 추측하고 있었다 — 실제 승강 기록
        # (promotion_log)을 넘겨서 정확히 판정하게 한다.
        from game_engine import get_my_promotions
        promos = get_my_promotions()

        return p, entries, trophies, awards, intl_trophies, match_rows, absence_events, promos

    def _open_story_book(self):
        """story_generator.py(로컬 문장 뱅크 기반, API 비사용)로 장문
        연대기를 만들어 책 형태의 새 창(StoryBookWindow)으로 띄운다."""
        # [2026-08 신설, 신민용 요청: "같은 종류의 창은 하나만"] 이미 열려
        # 있으면 새로 생성하지 않고(비용이 드는 문장 생성도 건너뛰고)
        # 기존 창을 앞으로 가져온다.
        if getattr(self, "_book_win", None) is not None:
            self._book_win.raise_(); self._book_win.activateWindow()
            return
        self.book_btn.setEnabled(False)
        self.book_btn.setText("⏳ 생성 중...")
        try:
            p, entries, trophies, awards, intl_trophies, match_rows, absence_events, promos = \
                self._gather_story_inputs()

            import story_generator
            story_text = story_generator.generate_story(
                p, entries, trophies, awards, promos=promos, intl_trophies=intl_trophies,
                match_rows=match_rows, absence_events=absence_events)

            from ui.story_book_window import StoryBookWindow
            self._book_win = StoryBookWindow(p.get("name", "선수"), story_text, parent=self)

            def _clear_book(*_a):
                self._book_win = None
            self._book_win.finished.connect(_clear_book)
            self._book_win.show()
        finally:
            self.book_btn.setEnabled(True)
            self.book_btn.setText("📖 스토리 생성")

    # ── 시작 화면으로 ─────────────────────────────────

    def _go_start(self):
        """데이터 초기화 후 MainWindow를 시작 화면으로 교체 (새 창 안 열림)."""
        if not _game_confirm(self, "시작 화면으로", "현재 게임 데이터가 삭제됩니다.\n시작 화면으로 이동하시겠습니까?"):
            return

        parent = self.parent_win
        self.close()  # 은퇴 창 닫기

        if parent and hasattr(parent, 'go_to_start'):
            parent.go_to_start()  # MainWindow를 시작 화면으로 교체