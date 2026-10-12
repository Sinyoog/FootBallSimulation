# competition/cup_plan.py
# [2026-10 신설, 신민용 확정: "리그 중간(후반기) A매치 기간 — 1안 A"]
# 시즌 시작 시점(리그 일정을 짜기 직전)에 나라별 "컵 계획"을 미리 계산한다.
#   - 국내컵(cup_engine): 후반기 라운드 주, 실제로 참가하는 부수
#   - 3·4부컵(lower_cup_engine): 라운드 주, 참가 부수(3+4 또는 2+3)
# 리그 일정 생성기(game_engine._build_league_schedule_rows)가 이 계획을 보고
# "그 리그가 실제로 참가하는 컵의 라운드 주"에만 리그 경기를 덜 넣는다
# (constants.LEAGUE_CUP_RELIEF_*). 컵 대진은 리그 일정보다 늦게(국내컵 5주차,
# 3·4부컵 하반기 개막) 만들어지므로, 리그 일정을 짤 때는 아직 실제 컵이 없다 —
# 그래서 cup_engine/lower_cup_engine의 대진 인원 계산을 경기 결과 없이 그대로
# 따라가서 미리 구한다(한 경기 = 한 팀 탈락이라 라운드 수·주차는 인원 수만으로
# 정해진다).
#
# 쓰는 정보는 시즌 시작 시점에 이미 확정된 것뿐이다: 나라별 부수 목록, 부수별
# 팀 수, 컵 설정 상수. 1부 중 챔스 참가팀(국내컵 "1b" 늦은 합류 물결)은 8주차
# 챔스 추첨 전엔 모르므로 0으로 둔다 — 2026-10 실측(212개국 × 시드 2 × 3시즌,
# 실제 런과 전 항목 비교)에서 이 값은 라운드 주·참가 부수에 영향이 없었다
# (표준 강수로 수렴하는 부전승 구조 때문).
import collections

from database import get_conn


def _week_of(rc):
    """cup_engine._start_next_round의 라운드 → 주차 규칙 그대로."""
    from competition import cup_engine as CE
    pool = CE.CUP_ROUND_WEEKS_POOL
    if rc < len(pool):
        return pool[rc]
    w = min(52, pool[-1] + (rc - (len(pool) - 1)))
    if CE.INTL_QUAL_WEEK <= w <= CE._CUP_BREAK_END_WEEK:
        w = CE._CUP_BREAK_END_WEEK + 1
    return w


def predict_cup_plan():
    """{str(country_id): {"dom_weeks": [...], "dom_tiers": [...],
                         "low_weeks": [...], "low_tiers": [...]}}
    dom_weeks는 후반기(29주 이상) 라운드 주만 담는다(전반기 5~7주 라운드는
    리그 배치 완화 대상이 아님)."""
    from competition import cup_engine as CE
    from competition import lower_cup_engine as LE
    conn = get_conn()
    rows = conn.execute("SELECT l.id, l.country_id, l.tier, COUNT(t.id) FROM leagues l "
                        "LEFT JOIN teams t ON t.league_id=l.id GROUP BY l.id").fetchall()
    cname = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM countries").fetchall()}
    conn.close()

    tier_count = collections.defaultdict(collections.Counter)
    first_lg = {}   # (cid, tier) -> (league_id, 팀 수) — 3·4부컵은 부수마다 첫 리그 하나만 쓴다
    for lid, cid, tier, n in rows:
        if n:
            tier_count[cid][tier] += n
        k = (cid, tier)
        if k not in first_lg or lid < first_lg[k][0]:
            first_lg[k] = (lid, n)

    plan = {}
    for cid, tc in tier_count.items():
        # ── 국내컵: _start_domestic_cup_for_country + _start_next_round 인원 계산 ──
        mx = min(CE.CUP_ABSOLUTE_MAX_TIER,
                 CE.CUP_MAX_TIER_BY_COUNTRY.get(cname.get(cid, ""), CE.DEFAULT_CUP_MAX_TIER))
        tiers = [t for t in sorted(tc, reverse=True) if t <= mx]
        dom_w, dom_t = [], set()
        entered, dom_wt = set(), {}   # [2026-10 L1e] 라운드 주마다 그때까지 합류한 부수
        if tiers:
            queue = [str(t) for t in tiers if t != 1] + (["1a", "1b"] if 1 in tiers else [])
            size = {tok: (tc[int(tok)] if tok not in ("1a", "1b") else (tc.get(1, 0) if tok == "1a" else 0))
                    for tok in queue}
            std = (CE._cup_target_bracket_size(sum(size.values()), cap=CE.CUP_STANDARD_BRACKET_CAP)
                   or CE.CUP_STANDARD_BRACKET_CAP)
            survivors, rc, q = 0, 0, list(queue)
            for _guard in range(60):
                tok = q.pop(0) if q else None
                new = size[tok] if tok else 0
                if tok and new:
                    dom_t.add(int(tok[0]))
                    entered.add(int(tok[0]))
                pool_n = survivors + new
                if pool_n < 2:
                    break
                if tok is None:
                    m = (pool_n - CE._cup_bye_count(pool_n, cap=std)) // 2
                else:
                    margin = pool_n + sum(size[t] for t in q) - std
                    m = 0 if margin <= 0 else min(pool_n // 2, max(1, max(0, margin - len(q))))
                if m == 0:
                    survivors = pool_n          # 경기 없는 라운드는 주를 소비하지 않음
                    continue
                dom_w.append(_week_of(rc))
                dom_wt.setdefault(_week_of(rc), set()).update(entered)
                rc += 1
                survivors = pool_n - m          # 한 경기 = 한 팀 탈락
        # ── 3·4부컵: start_lower_cup + 라운드 진행 규칙 ──
        ts = set(tc)
        lt = (3, 4) if 4 in ts else ((2, 3) if 3 in ts else None)
        low_w, low_t = [], []
        if lt:
            n = sum(first_lg.get((cid, t), (0, 0))[1] for t in lt)
            if n >= 8:
                T = CE._cup_target_bracket_size(n, cap=LE.LOWER_CUP_BRACKET_CAP)
                bye = CE._cup_bye_count(n, cap=LE.LOWER_CUP_BRACKET_CAP)
                pool_w, w = LE.LOWER_CUP_ROUND_WEEKS_POOL, None
                if LE.lower_cup_split_enabled():
                    # [2026-10 L1e] 분할 모드: lower_cup_engine._split_week 규칙 그대로
                    prev = None
                    if bye:
                        prev = LE._split_week(n, is_po=True)
                        low_w.append(prev)
                    s = T
                    while s >= 2:
                        prev = LE._split_week(s, prev_week=prev)
                        low_w.append(prev)
                        s //= 2
                else:
                    for i in range((1 if bye else 0) + (T.bit_length() - 1)):
                        w = pool_w[i] if i < len(pool_w) else w + 2
                        low_w.append(w)
                low_t = list(lt)
        plan[str(cid)] = {"dom_weeks": sorted({w for w in dom_w if w >= 29}), "dom_tiers": sorted(dom_t),
                          # [2026-10 L1e] 상반기 국내컵 주와 그 주에 실제로 뛰는 부수
                          # (constants.LEAGUE_CUP_RELIEF_FIRST_HALF_DOM이 켜졌을 때만 쓴다)
                          "dom_weeks_h1": sorted({w for w in dom_w if w < 29}),
                          "dom_week_tiers": {str(k): sorted(v) for k, v in dom_wt.items()},
                          "low_weeks": low_w, "low_tiers": low_t}
    return plan