"""
ui/bracket_widget.py  ─  토너먼트 대진표(브래킷) 위젯

챔피언스리그·국제대회(월드컵/대륙컵)의 녹아웃 단계를
텍스트 리스트가 아니라 '현실 대진표' 형태로 그린다.

라운드를 좌→우 컬럼으로 배치하고, 각 경기는 홈/원정 두 칸짜리
박스로 그린 뒤 승자를 다음 라운드 박스와 연결선으로 잇는다.
내용량(팀 수)에 따라 위젯 크기가 자동으로 커지며,
바깥을 QScrollArea로 감싸면 그만큼 스크롤된다.

데이터 형식 (build에 넘기는 rounds):
    rounds = [
        {
            "stage": "32강", "week": 42,
            "matches": [
                {
                    "home": "첼시", "away": "샬록 로버스",
                    "home_flag": "🏴", "away_flag": "🇮🇪",
                    "hs": 2, "as_": 1,          # 미진행이면 둘 다 -1
                    "winner": "첼시",            # 미정이면 ""
                    "pso": "",                   # 승부차기 스코어 문자열(있을 때만)
                    "my_side": "home",           # 내 팀이 home/away/None
                },
                ...
            ],
        },
        ...  # 다음 라운드
    ]
"""
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QSize, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QFontMetrics


# ── 색상 팔레트 ──
_C_BG        = QColor("#1e1e1e")
_C_BOX       = QColor("#252525")
_C_BOX_LINE  = QColor("#3a3a3a")
_C_TEXT      = QColor("#cccccc")
_C_DIM       = QColor("#777777")
_C_WIN       = QColor("#00cc44")   # 승자
_C_MINE      = QColor("#66ccff")   # 내 팀
_C_LINE      = QColor("#444444")   # 연결선
_C_STAGE     = QColor("#aaaaaa")   # 라운드 제목


class BracketWidget(QWidget):
    # 레이아웃 상수
    BOX_W      = 176      # 경기 박스 너비
    SLOT_H     = 38       # 한 칸(팀 한 줄) 높이 — 팀명+국가명 2줄 표기용
    MATCH_H    = SLOT_H * 2          # 경기 박스 높이(홈+원정)
    V_GAP_MIN  = 22       # 같은 라운드 경기 사이 최소 세로 간격
    COL_GAP    = 56       # 라운드(컬럼) 사이 가로 간격
    PAD        = 16       # 바깥 여백
    HDR_H      = 30       # 라운드 제목 높이

    def __init__(self, rounds, parent=None):
        super().__init__(parent)
        self._rounds = rounds or []
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # 경기 박스들의 중심 y좌표를 라운드별로 계산해 둔다.
        self._layout_done = False
        self._centers = []   # _centers[round_idx] = [match center_y, ...]
        self._compute_layout()

    # ── 레이아웃 계산 ───────────────────────────────
    def _compute_layout(self):
        self._centers = []
        if not self._rounds:
            self._w = 200; self._h = 80
            return

        n_rounds = len(self._rounds)
        first_n  = max(1, len(self._rounds[0]["matches"]))

        # 1라운드 경기들을 일정 간격으로 배치 → 이후 라운드는
        # '두 경기의 중점'에 위치시켜 현실 대진표처럼 좁혀진다.
        unit = self.MATCH_H + self.V_GAP_MIN          # 1라운드 경기 1칸이 차지하는 세로
        top  = self.PAD + self.HDR_H

        # 1라운드 중심들
        r0 = []
        for i in range(first_n):
            cy = top + i * unit + self.MATCH_H / 2
            r0.append(cy)
        self._centers.append(r0)

        # 이후 라운드: 직전 라운드 경기와 이름으로 대응시켜 배치
        # [버그수정 2026-07, 신민용 지적: "16강 칸끼리 겹쳐서 팀명이 안
        # 보인다" — 1차 수정 후에도 재현됨] 처음엔 "다음 라운드 경기 수 =
        # 직전 라운드의 절반"이라는 고정 가정이 문제라고 보고 이름 매칭으로
        # 바꿨는데, 그것만으론 부족했다 — 이 게임의 16강 대진은 직행팀과
        # 플레이오프 승자를 "1:1로만" 섞는 게 아니라 전체 진출팀을 OVR로
        # 정렬해 상위/하위 절반을 매칭한다(_start_knockout). 그 결과 "직행팀
        # 끼리"(예: 맨체스터 유나이티드 vs 종 헨크, 둘 다 플레이오프 전적 없음)
        # 매치업도 실제로 생길 수 있어서, 직전 라운드에서 이름을 하나도 못
        # 찾는 매치가 여럿 나온다. 이런 매치들이 전부 독립적으로
        # "top부터 균등 배치" 폴백을 쓰면 서로 겹칠 수 있다(폴백끼리도
        # 폴백-아닌-매치와도 좌표 공간을 공유하니까).
        # 그래서 2단계로 처리한다:
        #   1) 이름 매칭 가능한 매치는 그 직전 라운드 매치의 y를 그대로/평균.
        #   2) 매칭 안 되는 매치(둘 다 bye)는 앞뒤로 가장 가까운 매칭된
        #      매치 사이를 선형보간(둘 다 없으면 unit 간격 외삽)해 채운다.
        #   3) 마지막으로 위→아래 순서를 훑으며 최소 간격(MATCH_H+V_GAP_MIN)
        #      을 강제한다 — 1)/2) 결과가 어떻게 나오든 최종적으로 겹치는
        #      두 매치가 하나도 없다고 보장된다.
        for ri in range(1, n_rounds):
            n_cur = len(self._rounds[ri]["matches"])
            raw = []
            for m in self._rounds[ri]["matches"]:
                home, away = m.get("home"), m.get("away")
                ys = []
                # [버그수정 2026-07, 신민용 리포트: "3/4위전이 결승 옆에
                # 붙어야 하는데 위에 있어"] 3/4위전(TP)은 '직전 라운드'가
                # 아니라 '두 라운드 전'(4강)의 참가팀(4강 패자)과 이름이
                # 겹친다 — 결승(F)이 4강 바로 다음 컬럼을 차지하고 3/4위전은
                # 그 다음 컬럼에 놓이는 경우, 3/4위전 기준 '직전 라운드'는
                # 4강이 아니라 결승이라 이름 매칭이 하나도 안 됐다. 매칭
                # 실패 시 캔버스 맨 위로 배치하는 폴백(아래 141줄)을 타서,
                # 3/4위전 박스가 결승과 나란히 있어야 할 위치가 아니라
                # 화면 맨 위쪽에 붙어버렸다. 직전 라운드부터 시작해 매칭될
                # 때까지 점점 더 이전 라운드를 훑도록 고쳐서, 3/4위전도
                # 4강(그 경기의 진짜 부모 라운드)과 올바르게 정렬된다.
                for back in range(1, ri + 1):
                    prev = self._centers[ri - back]
                    prev_matches = self._rounds[ri - back]["matches"]
                    for k, pm in enumerate(prev_matches):
                        if k >= len(prev):
                            continue
                        pteam = (pm.get("home"), pm.get("away"))
                        if home and home in pteam:
                            ys.append(prev[k])
                        if away and away in pteam:
                            ys.append(prev[k])
                    if ys:
                        break
                raw.append(sum(ys) / len(ys) if ys else None)

            # 2) 매칭 안 된 자리는 앞뒤 매칭값 사이 선형보간(또는 외삽)으로 채운다.
            for j in range(n_cur):
                if raw[j] is not None:
                    continue
                prev_known = next(((k, raw[k]) for k in range(j - 1, -1, -1)
                                    if raw[k] is not None), None)
                next_known = next(((k, raw[k]) for k in range(j + 1, n_cur)
                                    if raw[k] is not None), None)
                if prev_known and next_known:
                    pk, pv = prev_known; nk, nv = next_known
                    raw[j] = pv + (nv - pv) * (j - pk) / (nk - pk)
                elif prev_known:
                    raw[j] = prev_known[1] + (j - prev_known[0]) * unit
                elif next_known:
                    raw[j] = next_known[1] - (next_known[0] - j) * unit
                else:
                    raw[j] = top + j * unit + self.MATCH_H / 2

            # 3) 최소 간격 강제 — 위에서부터 훑으며 겹치면 아래로 밀어낸다.
            min_gap = self.MATCH_H + self.V_GAP_MIN
            for j in range(1, n_cur):
                if raw[j] < raw[j - 1] + min_gap:
                    raw[j] = raw[j - 1] + min_gap

            cur = raw
            self._centers.append(cur)

        # 전체 크기
        self._w = self.PAD * 2 + n_rounds * self.BOX_W + (n_rounds - 1) * self.COL_GAP
        max_cy = top + self.MATCH_H / 2
        for col in self._centers:
            if col:
                max_cy = max(max_cy, max(col))
        self._h = int(max_cy + self.MATCH_H / 2 + self.PAD)
        self._w = int(self._w)
        self._layout_done = True

    def _col_x(self, round_idx):
        return self.PAD + round_idx * (self.BOX_W + self.COL_GAP)

    def sizeHint(self):
        return QSize(self._w, self._h)

    def minimumSizeHint(self):
        return QSize(self._w, self._h)

    # ── 그리기 ──────────────────────────────────────
    def paintEvent(self, ev):
        if not self._rounds:
            return
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        f_team  = QFont(); f_team.setPointSize(9)
        f_stage = QFont(); f_stage.setPointSize(9); f_stage.setBold(True)
        f_score = QFont(); f_score.setPointSize(9); f_score.setBold(True)
        f_country = QFont(); f_country.setPointSize(10)   # 국가명(아랫줄)용 작은 폰트
        self._f_country = f_country

        # 1) 연결선 먼저 (박스 뒤에 깔리게)
        # [버그수정 2026-07] _compute_layout과 동일한 이유로, 여기도
        # (2j, 2j+1) 고정 페어링 대신 팀 이름으로 직전 라운드 매치를 찾아
        # 그 매치에서만 선을 긋는다 — bye가 섞인 라운드(챔스 플레이오프→
        # 16강 등)에서 엉뚱한 매치끼리 잘못 이어지는 걸 방지한다.
        qp.setPen(QPen(_C_LINE, 1.4))
        for ri in range(len(self._rounds) - 1):
            cur_x_right = self._col_x(ri) + self.BOX_W
            nxt_x_left  = self._col_x(ri + 1)
            mid_x = (cur_x_right + nxt_x_left) / 2
            cur_centers = self._centers[ri]
            cur_matches = self._rounds[ri]["matches"]
            nxt_centers = self._centers[ri + 1]
            nxt_matches = self._rounds[ri + 1]["matches"]
            for j, ncy in enumerate(nxt_centers):
                nm = nxt_matches[j] if j < len(nxt_matches) else {}
                home, away = nm.get("home"), nm.get("away")
                pair = []
                for k, cm in enumerate(cur_matches):
                    if k >= len(cur_centers):
                        continue
                    cteam = (cm.get("home"), cm.get("away"))
                    if (home and home in cteam) or (away and away in cteam):
                        pair.append(k)
                for c in pair:
                    cy = cur_centers[c]
                    # ┐ 모양: 박스 오른쪽 → 중간 수직 → 다음 박스 왼쪽
                    qp.drawLine(int(cur_x_right), int(cy), int(mid_x), int(cy))
                    qp.drawLine(int(mid_x), int(cy), int(mid_x), int(ncy))
                qp.drawLine(int(mid_x), int(ncy), int(nxt_x_left), int(ncy))

        # 2) 라운드 제목 + 경기 박스
        for ri, rnd in enumerate(self._rounds):
            x = self._col_x(ri)
            # 제목
            qp.setFont(f_stage)
            qp.setPen(_C_STAGE)
            wk = rnd.get("week")
            title = rnd.get("stage", "")
            if wk:
                title += f"  ({wk}주차)"
            qp.drawText(QRectF(x, self.PAD, self.BOX_W, self.HDR_H - 6),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)

            centers = self._centers[ri]
            for mi, m in enumerate(rnd["matches"]):
                if mi >= len(centers):
                    break
                cy = centers[mi]
                top_y = cy - self.MATCH_H / 2
                self._draw_match(qp, x, top_y, m, f_team, f_score)

        qp.end()

    def _draw_match(self, qp, x, top_y, m, f_team, f_score):
        hs, as_ = m.get("hs", -1), m.get("as_", -1)
        played  = hs is not None and hs >= 0
        winner  = m.get("winner", "")
        my_side = m.get("my_side")

        rows = [
            ("home", m.get("home_flag", ""), m.get("home", "?"), hs),
            ("away", m.get("away_flag", ""), m.get("away", "?"), as_),
        ]

        # 박스 배경
        qp.setPen(QPen(_C_BOX_LINE, 1))
        qp.setBrush(QBrush(_C_BOX))
        qp.drawRoundedRect(QRectF(x, top_y, self.BOX_W, self.MATCH_H), 4, 4)
        # 가운데 구분선
        qp.setPen(QPen(_C_BOX_LINE, 1))
        qp.drawLine(int(x), int(top_y + self.SLOT_H),
                    int(x + self.BOX_W), int(top_y + self.SLOT_H))

        score_w = 26
        name_pad = 8
        for k, (side, flag, name, sc) in enumerate(rows):
            ry = top_y + k * self.SLOT_H
            is_winner = bool(winner) and (name == winner)
            is_mine   = (my_side == side)

            if is_mine:
                col = _C_MINE
            elif is_winner:
                col = _C_WIN
            elif played:
                col = _C_DIM
            else:
                col = _C_TEXT

            # 팀명 + 국가명 2줄 표기.
            #   name 형식: "팀명 (국가명)" → 윗줄=국기+팀명, 아랫줄=(국가명)
            #   국가명이 길어 옆으로 잘리던 문제를 줄바꿈으로 해결한다.
            country = ""
            team_only = name
            if name.endswith(")") and " (" in name:
                base, _, ctry = name.rpartition(" (")
                team_only = base
                country = ctry[:-1]   # 끝 ')' 제거

            avail = self.BOX_W - name_pad - score_w
            fm = QFontMetrics(f_team)

            if country:
                # 윗줄: 국기+팀명 (위쪽 절반), 아랫줄: 국가명 (작고 흐리게)
                line1 = fm.elidedText(f"{flag}{team_only}",
                                      Qt.TextElideMode.ElideRight, avail)
                qp.setFont(f_team)
                qp.setPen(col)
                qp.drawText(QRectF(x + name_pad, ry + 2, avail, self.SLOT_H / 2),
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, line1)

                fm2 = QFontMetrics(self._f_country)
                line2 = fm2.elidedText(f"({country})",
                                       Qt.TextElideMode.ElideRight, avail)
                qp.setFont(self._f_country)
                qp.setPen(_C_DIM)
                qp.drawText(QRectF(x + name_pad, ry + self.SLOT_H / 2 - 2,
                                   avail, self.SLOT_H / 2),
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, line2)
            else:
                # 국가명 없으면 기존처럼 한 줄 중앙 정렬
                qp.setFont(f_team)
                qp.setPen(col)
                label = fm.elidedText(f"{flag}{name}",
                                      Qt.TextElideMode.ElideRight, avail)
                qp.drawText(QRectF(x + name_pad, ry, avail, self.SLOT_H),
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

            # 스코어
            if played:
                qp.setFont(f_score)
                qp.setPen(_C_WIN if is_winner else col)
                qp.drawText(QRectF(x + self.BOX_W - score_w, ry, score_w - 4, self.SLOT_H),
                            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                            str(sc))

        # 승부차기 표기 (박스 우상단 작게)
        pso = m.get("pso", "")
        if pso:
            qp.setFont(f_team)
            qp.setPen(_C_DIM)
            qp.drawText(QRectF(x, top_y - 1, self.BOX_W - 4, 14),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                        f"PSO {pso}")


def build_rounds_from_matches(matches, stage_order, my_names=None):
    """평면 경기 리스트 → 라운드별로 그룹화한 rounds 구조로 변환.

    matches: dict 리스트. 각 dict는 최소
        stage(한글), week, home, away, home_flag, away_flag,
        hs, as_, winner, pso, my_side  키를 가진다.
    stage_order: {"32강":0, "16강":1, ...} 정렬 기준.
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    weeks   = {}
    for m in matches:
        st = m["stage"]
        buckets[st].append(m)
        weeks.setdefault(st, m.get("week"))

    rounds = []
    for st in sorted(buckets.keys(), key=lambda s: stage_order.get(s, 99)):
        rounds.append({
            "stage": st,
            "week": weeks.get(st),
            "matches": buckets[st],
        })
    return rounds