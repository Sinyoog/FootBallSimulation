# -*- coding: utf-8 -*-
"""manager_ability.py — 감독 능력치(1~100) · 숨은 상한 (6번 감독 능력치 1단계, 2026-10 신설)

신민용 확정 원칙:
  ① 감독 능력치 1~100, 눈금은 팀 OVR과 같다.
  ② 평판(reputation)은 그대로 유지 — 능력과 별개 축.
  ③ 게임 시작 때는 과거 데이터가 없으므로 능력치를 직접 생성한다.
  ④ 이후 실제 커리어로 변한다(3단계 — 아직 없음).
  ⑤ 나이는 능력 감쇠 요소가 아니다. → [2026-10 ④ 개정] 60세부터 노화(apply_yearly_progression).
  + 숨은 상한(manager_potential) — 선수 potential_ovr처럼 소수만 높게.
  + 목표 분포: 90+ ≈ 세계 25명, 95+ ≈ 2~4명. 월드클래스는 명문팀에서 시작.

[언제 도는가] assign_missing_manager_abilities() 하나다 — 능력치가 비어 있는
(0) 감독만 채운다.
  · 대량(새 게임 시작 / 기존 세이브 첫 실행): 지금 자리·최근/최고 경력 수준·
    명성·우승으로 "원점수"를 만들고, 현역 감독 전체 순위로 상위권만 눌러 담아
    목표 분포에 맞춘다(아래 _build_transform). 기존 세이브는 이미 쌓인 커리어를
    역산하므로 무작위 재추첨이 아니다.
  · 소량(시즌 중 신인 감독·임시 감독 생성): 첫 자리 팀 OVR 근처에서 직접 만든다.

[결정성] 전역 random을 쓰지 않는다 — (월드 salt, 감독 id)에서 파생한 Random만.
"""
import math
import random
import zlib

from constants import (MANAGER_ABILITY_RANK_ANCHORS, MANAGER_ABILITY_IDENTITY_BELOW,
                       MANAGER_ABILITY_MIN, MANAGER_ABILITY_MAX,
                       MANAGER_ABILITY_W_RECENT, MANAGER_ABILITY_W_BEST,
                       MANAGER_ABILITY_PRESTIGE_BONUS, MANAGER_ABILITY_REP_SLOPE,
                       MANAGER_ABILITY_TITLE_BONUS_MAX, MANAGER_ABILITY_JOBLESS_DROP,
                       MANAGER_ABILITY_NOISE, MANAGER_POTENTIAL_SIGMA_BY_AGE,
                       MANAGER_ROOKIE_ABILITY_OFFSET, MANAGER_ROOKIE_ABILITY_RANGE,
                       MANAGER_ROOKIE_ABILITY_SD, MANAGER_ROOKIE_TALENT_TIERS,
                       MANAGER_ROOKIE_BLOOM_TIERS, MANAGER_POTENTIAL_SOFTCAP,
                       MANAGER_POTENTIAL_TOP_REF, manager_job_level,
                       get_country_league_grade, national_job_level, get_country_grade)

# 이만큼 이상 비어 있으면 "대량" 경로(분포 맞춤)로 처리한다.
_MASS_MIN = 300
LAST_ABILITY_ASSIGN = {}


def _rng(salt, mid, tag):
    return random.Random(zlib.crc32(f"{tag}:{salt}:{mid}".encode("utf-8")))


def team_ovr_map(conn):
    """팀 id → 상위 11명 평균 OVR(팀 전력 눈금)."""
    out = {}
    for tid, v in conn.execute(
            """SELECT team_id, AVG(ovr) FROM (
                   SELECT team_id, ovr, ROW_NUMBER() OVER (
                       PARTITION BY team_id ORDER BY ovr DESC) AS rn
                   FROM ai_players WHERE team_id IS NOT NULL)
               WHERE rn <= 11 GROUP BY team_id"""):
        if v is not None:
            out[int(tid)] = float(v)
    return out


def _team_meta(conn):
    """팀 id → (나라, 자리 수준, 명문 등급)."""
    from data.prestige_clubs import prestige_level
    meta = {}
    for r in conn.execute(
            """SELECT t.id, t.name, cn.name AS country, lg.tier AS tier
               FROM teams t
               LEFT JOIN countries cn ON cn.id = t.country_id
               LEFT JOIN leagues lg ON lg.id = t.league_id"""):
        country = r["country"] or ""
        pr = (prestige_level(country, r["name"]) or 0) if country else 0
        lvl = manager_job_level(get_country_league_grade(country), r["tier"], pr)
        meta[int(r["id"])] = (country, lvl, pr)
    return meta


def _level_to_ovr_fn(meta, tovr):
    """자리 수준(감독 시장 눈금) → 팀 OVR 눈금 환산 함수. 지금 세계의 팀들로
    수준 구간별 중앙값을 구해 선형 보간한다 — 따로 손으로 정한 표가 없다."""
    bins = {}
    for tid, (_c, lvl, _p) in meta.items():
        ov = tovr.get(tid)
        if ov is None:
            continue
        bins.setdefault(int(lvl // 5), []).append(ov)
    pts = []
    for b in sorted(bins):
        xs = sorted(bins[b])
        if len(xs) >= 3:
            pts.append((b * 5 + 2.5, xs[len(xs) // 2]))
    if not pts:
        return lambda lv: float(lv) * 0.7

    def f(lv):
        lv = float(lv or 0.0)
        if lv <= pts[0][0]:
            return pts[0][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if lv <= x1:
                return y0 + (y1 - y0) * (lv - x0) / max(1e-9, x1 - x0)
        # 맨 위 구간 밖 — 마지막 기울기로 조금만 늘린다.
        (x0, y0), (x1, y1) = pts[-2] if len(pts) > 1 else pts[-1], pts[-1]
        slope = (y1 - y0) / max(1e-9, x1 - x0) if len(pts) > 1 else 0.0
        return y1 + slope * (lv - x1)
    return f


def _build_transform(raws_active):
    """현역 감독 원점수 목록 → 원점수를 능력치로 바꾸는 단조 함수.
    상위권은 순위 기준점(MANAGER_ABILITY_RANK_ANCHORS)에 맞추고, 그 아래는
    IDENTITY_BELOW까지 선형으로 이어 붙인 뒤 그 아래는 원점수 그대로."""
    xs = sorted(raws_active, reverse=True)
    n = len(xs)
    knots = []      # (원점수, 능력) — 원점수 내림차순
    for rank, ab in MANAGER_ABILITY_RANK_ANCHORS:
        if rank <= n:
            knots.append((xs[rank - 1], ab))
    if not knots:
        return lambda r: r
    low_raw, low_ab = knots[-1]
    ident = MANAGER_ABILITY_IDENTITY_BELOW

    def tr(r):
        if r >= knots[0][0]:
            # 1위보다 위(은퇴자 등) — 1위 능력에서 살짝만 더.
            return min(MANAGER_ABILITY_MAX, knots[0][1] + (r - knots[0][0]) * 0.2)
        for (r0, a0), (r1, a1) in zip(knots, knots[1:]):
            if r1 <= r <= r0:
                return a1 + (a0 - a1) * (r - r1) / max(1e-9, r0 - r1)
        if r <= ident:
            return r
        if low_raw <= ident:
            return min(r, low_ab)
        # ident ~ low_raw 사이: (ident → ident) ~ (low_raw → low_ab) 직선
        return ident + (low_ab - ident) * (r - ident) / (low_raw - ident)
    return tr


def ability_fit_bonus(ability, job_ability):
    """[6번 ① 감독 시장] (감독 능력 − 자리 요구 능력) → 후보 점수 보정.
    능력치가 아직 없으면(0) 보정 없음. 표는 constants.MANAGER_ABILITY_FIT_POINTS."""
    import constants as _C
    if not getattr(_C, "MANAGER_ABILITY_FIT_ENABLED", False):
        return 0.0
    try:
        ab = float(ability or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if ab <= 0.0 or job_ability is None:
        return 0.0
    d = ab - float(job_ability)
    pts = _C.MANAGER_ABILITY_FIT_POINTS
    if d <= pts[0][0]:
        return float(pts[0][1])
    if d >= pts[-1][0]:
        return float(pts[-1][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if d <= x1:
            return y0 + (y1 - y0) * (d - x0) / max(1e-9, x1 - x0)
    return float(pts[-1][1])


def nation_job_ability(conn, country, lv2ovr=None):
    """[2026-10 대표팀 기준 통일] 대표팀 자리 요구 능력 = 국적 선수 베스트11 평균
    (database.get_country_best_xi_ovr — 대표팀 경기 전력과 같은 값). 값이 없으면
    (국적 선수 8명 미만) 예전 기준(국대 등급 자리 수준 환산)으로 폴백."""
    try:
        from database import get_country_best_xi_ovr
        v = get_country_best_xi_ovr(country, min_count=8)
    except Exception:
        v = None
    if v is not None:
        return float(v)
    if lv2ovr is None:
        lv2ovr = _level_to_ovr_fn(_team_meta(conn), team_ovr_map(conn))
    return lv2ovr(national_job_level(get_country_grade(country)))


class JobAbility:
    """[6번 ① 감독 시장] 자리 요구 능력(job_ability) — 감독 능력과 같은 팀 OVR
    눈금. 시장 한 번에 한 번만 만든다(팀 OVR 집계 ≈ 0.3s).
      · club(team_id, level): 그 팀 상위 11명 평균 OVR. 선수가 없는 팀은 자리
        수준을 OVR 눈금으로 환산(능력치 생성과 같은 환산 함수).
      · nation(country): 국대 등급 자리 수준 → 팀 OVR 눈금(능력치 생성 때 대표팀
        감독의 "지금 자리"를 잰 식과 같다)."""

    def __init__(self, conn):
        self.tovr = team_ovr_map(conn)
        self._lv2ovr = _level_to_ovr_fn(_team_meta(conn), self.tovr)
        self._nat = {}
        self._conn = conn

    def club(self, team_id, level=None):
        v = self.tovr.get(int(team_id)) if team_id is not None else None
        if v is None:
            v = self._lv2ovr(level or 0.0)
        return v

    def nation(self, country):
        v = self._nat.get(country)
        if v is None:
            v = nation_job_ability(self._conn, country, self._lv2ovr)   # [2026-10] 베스트11
            self._nat[country] = v
        return v


def _potential(rng, ability, age):
    sigma = MANAGER_POTENTIAL_SIGMA_BY_AGE[-1][1]
    for upto, sg in MANAGER_POTENTIAL_SIGMA_BY_AGE:
        if age < upto:
            sigma = sg
            break
    head = abs(rng.gauss(0.0, sigma)) * max(0.15, min(1.0, (MANAGER_POTENTIAL_TOP_REF - ability) / 20.0))
    return int(max(ability, min(MANAGER_ABILITY_MAX, round(ability + head))))


def _pick_tier(rng, tiers):
    """(확률, 최소, 최대) 표에서 누적 확률로 한 칸을 고르고 그 범위의 값을, 안
    걸리면 None. 난수는 항상 random() 1회(+걸리면 uniform 1회)."""
    x = rng.random()
    acc = 0.0
    for p, lo, hi in tiers:
        acc += p
        if x < acc:
            return rng.uniform(lo, hi)
    return None


def _rookie_ability(rng, req):
    """[6번 ②] 신인 능력 — 첫 자리 요구 능력 근처 + 드문 재능 꼬리."""
    lo, hi = MANAGER_ROOKIE_ABILITY_RANGE
    v = float(req) + rng.gauss(MANAGER_ROOKIE_ABILITY_OFFSET, MANAGER_ROOKIE_ABILITY_SD)
    t = _pick_tier(rng, MANAGER_ROOKIE_TALENT_TIERS)
    if t is not None:
        v += t
    return int(max(lo, min(hi, round(v))))


def _rookie_potential(rng, ability, age):
    """[6번 ②] 신인 잠재력 — 대부분은 기존 _potential 식, 소수만 늦게 피는 꼬리.
    90 위는 SOFTCAP으로 눌러 담는다."""
    head = _pick_tier(rng, MANAGER_ROOKIE_BLOOM_TIERS)
    if head is None:
        return _potential(rng, ability, age)
    pot = float(ability) + head
    cap, mult = MANAGER_POTENTIAL_SOFTCAP
    if pot > cap:
        pot = cap + (pot - cap) * mult
    return int(max(ability, min(MANAGER_ABILITY_MAX, round(pot))))


def _clip_ability(v):
    return int(max(MANAGER_ABILITY_MIN, min(MANAGER_ABILITY_MAX, round(v))))


def assign_missing_manager_abilities(conn, year=None, verbose=False, mode=None):
    """능력치가 비어 있는 감독(manager_ability=0)을 채운다. 반환: 채운 수.

    mode: None = 예전처럼 인원으로 판단(세계 생성·구세이브 첫 실행용, init_db 경로)
          "rookie" = 인원과 무관하게 신인 규칙(감독 시장·대표팀 공석에서 새로 만든
          감독). [6번 ②] 시즌 시장 신인은 한 해 400~800명이라 인원 기준이면 대량
          경로(세계 생성용 순위 변환)로 잘못 빠졌다."""
    from database import get_world_salt
    try:
        missing = conn.execute(
            "SELECT COUNT(*) FROM managers WHERE COALESCE(manager_ability,0)=0").fetchone()[0]
    except Exception:
        return 0            # 컬럼이 없는 구세이브 — init_db의 ALTER가 먼저 돈다
    if not missing:
        return 0
    if year is None:
        r = conn.execute("SELECT current_year FROM season_state WHERE id=1").fetchone()
        year = int(r[0]) if r and r[0] else 2000
    salt = get_world_salt()
    tovr = team_ovr_map(conn)
    meta = _team_meta(conn)
    lv2ovr = _level_to_ovr_fn(meta, tovr)

    # 지금 자리 — 클럽(team_managers 현직) / 대표팀(national_team_managers 현직)
    club_job = {int(r[0]): int(r[1]) for r in conn.execute(
        "SELECT manager_id, team_id FROM team_managers WHERE end_year IS NULL")}
    nat_job = {}
    try:
        for r in conn.execute(
                """SELECT n.manager_id, c.name FROM national_team_managers n
                   JOIN countries c ON c.id = n.country_id WHERE n.end_year IS NULL"""):
            nat_job[int(r[0])] = r[1]
    except Exception:
        pass
    def _nat_ovr(cn):
        # [2026-10 개정 — 대표팀 기준을 클럽과 통일] 대표팀 자리 = 그 나라 국적 선수
        # OVR 상위 11명 평균(대표팀 경기 전력과 같은 값, 클럽 팀 OVR과 같은 규칙).
        # 예전엔 국대 등급 자리 수준을 환산해 F급 감독이 70대로 만들어졌다(실제
        # F급 베스트11은 45~53). 선수가 모자라 값이 없을 때만 예전 환산값.
        return nation_job_ability(conn, cn, lv2ovr)

    rows = [dict(r) for r in conn.execute(
        """SELECT id, birth_year, retired, status, jobless_since, reputation,
                  recent_level, career_best_level, titles_league, titles_cup,
                  titles_cont, titles_intl, COALESCE(manager_ability,0) AS ab
           FROM managers""")]
    todo = [m for m in rows if not m["ab"]]
    mass = (mode != "rookie") and len(todo) >= _MASS_MIN

    def _raw(m):
        mid = m["id"]
        best = lv2ovr(m.get("career_best_level") or 0.0)
        cur, pr = None, 0
        if mid in club_job:
            tid = club_job[mid]
            cur = tovr.get(tid)
            pr = meta.get(tid, ("", 0, 0))[2]
        elif mid in nat_job:
            cur = _nat_ovr(nat_job[mid])
        if cur is None:
            cur = lv2ovr(m.get("recent_level") or m.get("career_best_level") or 0.0)
        best = max(best, cur)
        v = MANAGER_ABILITY_W_RECENT * cur + MANAGER_ABILITY_W_BEST * best
        v += MANAGER_ABILITY_PRESTIGE_BONUS[max(0, min(3, int(pr or 0)))]
        v += (float(m.get("reputation") or 0.0) - 50.0) * MANAGER_ABILITY_REP_SLOPE
        tb = (0.3 * int(m.get("titles_league") or 0) + 0.15 * int(m.get("titles_cup") or 0)
              + 0.6 * int(m.get("titles_cont") or 0) + 0.8 * int(m.get("titles_intl") or 0))
        v += min(MANAGER_ABILITY_TITLE_BONUS_MAX, tb)
        js = m.get("jobless_since")
        if js and (m.get("status") or "") == "free":
            v -= MANAGER_ABILITY_JOBLESS_DROP * min(4, max(0, int(year) - int(js)))
        v += _rng(salt, mid, "mab").gauss(0.0, MANAGER_ABILITY_NOISE)
        return v

    updates = []
    if mass:
        raws = {m["id"]: _raw(m) for m in todo}
        # 분포는 "현역"(은퇴 아님) 기준으로 맞춘다 — 은퇴자는 같은 변환만 쓴다.
        # 이미 능력치가 있는 현역(부분 마이그레이션)은 그 능력치를 원점수 자리에
        # 넣어 순위에 같이 센다.
        act = [raws[m["id"]] for m in todo if not int(m.get("retired") or 0)]
        act += [float(m["ab"]) for m in rows if m["ab"] and not int(m.get("retired") or 0)]
        tr = _build_transform(act)
        for m in todo:
            ab = _clip_ability(tr(raws[m["id"]]))
            age = int(year) - int(m.get("birth_year") or year)
            pot = _potential(_rng(salt, m["id"], "mpot"), ab, age)
            updates.append((ab, pot, m["id"]))
    else:
        # [6번 ②] 신인 — 첫 자리 요구 능력(클럽 팀 OVR / 대표팀 환산값, 시장의
        # JobAbility와 같은 기준) 근처 + 재능 꼬리, 잠재력은 늦게 피는 꼬리.
        for m in todo:
            mid = m["id"]
            r = _rng(salt, mid, "mab")
            cur = None
            if mid in club_job:
                cur = tovr.get(club_job[mid])
                if cur is None:
                    cur = lv2ovr(meta.get(club_job[mid], ("", 0.0, 0))[1])
            elif mid in nat_job:
                cur = _nat_ovr(nat_job[mid])
            if cur is None:
                cur = lv2ovr(m.get("recent_level") or 0.0)
            ab = _rookie_ability(r, cur)
            age = int(year) - int(m.get("birth_year") or year)
            updates.append((ab, _rookie_potential(_rng(salt, mid, "mpot"), ab, age), mid))
    conn.executemany(
        "UPDATE managers SET manager_ability=?, manager_potential=? WHERE id=?", updates)
    LAST_ABILITY_ASSIGN.clear()
    LAST_ABILITY_ASSIGN.update({"filled": len(updates), "mass": mass})
    if verbose and updates:
        print(f"[MGR-ABILITY] 감독 능력치 {len(updates)}명 채움 ({'대량' if mass else '신규'})")
    return len(updates)


# ════════════════════════════════════════════════════════════════
# [2026-10 신설 — 6번 ④ 시즌별 성장/하락] 규칙은 constants의 같은 블록 주석.
# ════════════════════════════════════════════════════════════════
def _interp(points, x):
    if x <= points[0][0]:
        return float(points[0][1])
    if x >= points[-1][0]:
        return float(points[-1][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(1e-9, x1 - x0)
    return float(points[-1][1])


def growth_base(age):
    """60세 미만 나이 → 자리 압력 1.0일 때의 한 해 성장. 60세 이상은 0."""
    import constants as _C
    for upto, g in _C.MANAGER_GROWTH_BASE_BY_AGE:
        if age < upto:
            return float(g)
    return 0.0


def aging_drop(age):
    """나이 → 한 해 노화(음수). 60세 미만은 0."""
    import constants as _C
    for frm, d in _C.MANAGER_AGING_BY_AGE:
        if age >= frm:
            return float(d)
    return 0.0


def progression_delta(age, ability, potential, job_req=None, perf=None, jobless_years=0):
    """한 감독의 한 해 능력 변화(반올림 전 실수). 반환: (변화량, 성장분, 노화분).

    · 60세 미만 + 재직: 성장 = 나이 기본값 × 자리 압력((요구 능력 − 능력) 차이)
    · 60세 이상: 성장 0, 노화
    · 재직자는 그 시즌 성적(−1~+1) × MANAGER_GROWTH_PERF_W
    · 장기 무직(MANAGER_GROWTH_JOBLESS_YEARS 이상)은 MANAGER_GROWTH_JOBLESS_DROP
    · 60세 이상은 합계가 0을 넘지 않는다. 오르는 쪽은 잠재력까지만.
    """
    import constants as _C
    ab = float(ability or 0.0)
    pot = float(potential or 0.0)
    grow = 0.0
    if age < _C.MANAGER_GROWTH_AGE_LIMIT and job_req is not None:
        grow = growth_base(age) * _interp(_C.MANAGER_GROWTH_PRESSURE_POINTS,
                                          float(job_req) - ab)
    age_d = aging_drop(age)
    d = grow + age_d
    if perf is not None:
        d += _C.MANAGER_GROWTH_PERF_W * max(-1.0, min(1.0, float(perf)))
    if jobless_years >= _C.MANAGER_GROWTH_JOBLESS_YEARS:
        d += _C.MANAGER_GROWTH_JOBLESS_DROP
    if age >= _C.MANAGER_GROWTH_AGE_LIMIT:
        d = min(0.0, d)
    if d > 0:
        d = min(d, max(0.0, pot - ab))
    return d, grow, age_d


def stochastic_round(v, rng):
    """+1.3 → 70% +1 / 30% +2, −0.8 → 80% −1 / 20% 0. 난수 random() 1회."""
    lo = math.floor(v)
    frac = v - lo
    return int(lo + (1 if rng.random() < frac else 0))


def apply_yearly_progression(conn, year, club_season=None, mgr_rows=None, job_ab=None):
    """[6번 ④] 시즌 종료 시 한 번 — 은퇴 안 한 감독 전원의 능력을 갱신한다.

    club_season: {감독 id: (팀 id, 성적 −1~+1)} — 그 시즌 클럽을 맡은 감독. 성적은
                 MANAGER_GROWTH_PERF_BASIS("recent" = 누적 recent_perf, "season" = 그 시즌)
                 (_manager_turnover 평가 루프가 만든다. 이번 오프시즌에 경질·만료된
                 감독도 그 시즌을 치렀으므로 포함).
    mgr_rows:    {감독 id: dict} — 있으면 manager_ability를 같이 갱신한다(같은 시장
                 안에서 후보 점수가 새 능력을 보게).
    반환: 요약 dict. MANAGER_GROWTH_ENABLED가 False면 아무것도 안 한다."""
    import constants as _C
    if not getattr(_C, "MANAGER_GROWTH_ENABLED", False):
        return {}
    from database import get_world_salt
    year = int(year)
    club_season = club_season or {}
    salt = get_world_salt()
    if job_ab is None:
        job_ab = JobAbility(conn)
    _basis = getattr(_C, "MANAGER_GROWTH_PERF_BASIS", "recent")
    rows = conn.execute(
        """SELECT id, birth_year, status, jobless_since,
                  COALESCE(manager_ability,0), COALESCE(manager_potential,0),
                  COALESCE(recent_perf,0)
           FROM managers WHERE retired=0 AND COALESCE(manager_ability,0)>0""").fetchall()
    club_team = {int(r[0]): int(r[1]) for r in conn.execute(
        "SELECT manager_id, team_id FROM team_managers WHERE end_year IS NULL AND job_kind='club'")}
    nat_country = {}
    try:
        for r in conn.execute(
                """SELECT n.manager_id, c.name FROM national_team_managers n
                   JOIN countries c ON c.id = n.country_id WHERE n.end_year IS NULL"""):
            nat_country[int(r[0])] = r[1]
    except Exception:
        pass
    # 대표팀 성적 — 그 해 평가된 대회의 (결과 − 목표)/40 평균(national_manager와 같은 축).
    nat_perf = {}
    try:
        for r in conn.execute(
                """SELECT manager_id, result_rank, objective_rank FROM national_objectives
                   WHERE year=? AND result_rank>=0 AND manager_id>0""", (year,)):
            nat_perf.setdefault(int(r[0]), []).append(
                max(-1.0, min(1.0, (int(r[1]) - int(r[2] or 0)) / 40.0)))
    except Exception:
        pass

    ups = []
    summ = {"n": 0, "up": 0, "down": 0, "same": 0, "sum_delta": 0}
    for mid, by, status, js, ab, pot, rperf in rows:
        mid = int(mid)
        age = year - int(by or year)
        job_req, perf, jl = None, None, 0
        status = status or ""
        if mid in club_season:
            tid, perf = club_season[mid]
            job_req = job_ab.club(tid)
        elif status == "nation" and mid in nat_country:
            job_req = job_ab.nation(nat_country[mid])
            if _basis == "season":
                ps = nat_perf.get(mid)
                perf = (sum(ps) / len(ps)) if ps else None
            else:
                perf = float(rperf)         # 대회 평가 때 갱신된 누적 최근 성적
        elif status == "club" and mid in club_team:
            job_req = job_ab.club(club_team[mid])
        elif js and status == "free":
            jl = max(0, year - int(js))
        d, _g, _a = progression_delta(age, ab, pot, job_req, perf, jl)
        rng = random.Random(zlib.crc32(f"mgrow:{salt}:{mid}:{year}".encode("utf-8")))
        step = stochastic_round(d, rng)
        new = int(ab) + step
        if step > 0:
            new = min(new, max(int(ab), int(pot)))
        new = max(MANAGER_ABILITY_MIN, min(MANAGER_ABILITY_MAX, new))
        summ["n"] += 1
        if new != int(ab):
            ups.append((new, mid))
            summ["up" if new > ab else "down"] += 1
            summ["sum_delta"] += new - int(ab)
            if mgr_rows is not None and mid in mgr_rows:
                mgr_rows[mid]["manager_ability"] = new
        else:
            summ["same"] += 1
    if ups:
        conn.executemany("UPDATE managers SET manager_ability=? WHERE id=?", ups)
    LAST_PROGRESSION.clear()
    LAST_PROGRESSION.update(summ)
    return summ


LAST_PROGRESSION = {}