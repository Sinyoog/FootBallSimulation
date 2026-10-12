# -*- coding: utf-8 -*-
"""national_manager.py — 대표팀 감독 시장 (감독 시스템 ③-d, 2026-09 신설)

신민용 확정 파이프라인. 한 번 계산한 값을 아래 단계가 공유한다:

    national_power_snapshot → national_objective → tournament_result
    → objective_overachievement → manager_reputation → next-job market

[왜 이 순서인가 — 성능] "211개국 × 감독 후보 × 선수 계산" 같은 중첩을
피하는 게 핵심이다. 국가 전력과 목표를 **대회 시작 시점에 한 번** 굳혀
national_objectives에 박아두고, 이후 단계(결과 평가·명성·후보 평가)는
그 행을 읽기만 한다. 실측: 211개국 best-XI OVR 22ms vs 클럽 감독 시장
매 시즌 508ms — 굳혀두면 사실상 공짜다.

[대회 주기, 시즌 주기가 아니다] 신민용: "대표팀은 시즌마다 공석 판정하면
안 될 것 같아. 대회 주기 중심으로 보는 게 더 자연스러워." 그래서 이
모듈의 진입점은 두 개뿐이고 둘 다 intl_engine의 대회 생명주기에 붙는다:

    fix_national_objectives(tid)       ← 대회 생성 직후(경기 0개 시점)
    evaluate_national_tournament(tid)  ← _finish_tournament 직후

[teams에 가짜 행을 만들지 않는다] 신민용 확정: 대표팀 재임 이력은
national_team_managers에 따로 두고, 감독 **풀**(managers)은 클럽과
공유한다. 그래야 한 감독의 커리어가
    2024~2027 포르투갈 클럽 → 2028~2030 대한민국 대표팀 → 2031~ 일본 클럽
처럼 하나로 이어진다.

[한 감독은 한 자리만] 대표팀 감독으로 부임하면 managers.status='nation'이
되고, 클럽 시장(ai_lifecycle._manager_turnover)은 그 상태를 후보에서
제외한다. 물러나면 'free'가 되어 양쪽 시장의 후보가 된다.

[결정성] 클럽 시장과 같은 원칙 — 전역 random을 건드리지 않고
(world_salt, 연도, 국가)에서 파생한 random.Random 인스턴스만 쓴다.
"""
import random
import sqlite3
import zlib

from constants import (MANAGER_NAT_STAGE_RANK, MANAGER_NAT_CONTRACT_YEARS_MIN,
                       MANAGER_NAT_CONTRACT_YEARS_MAX, MANAGER_NAT_RENEW_BASE,
                       MANAGER_NAT_RENEW_SLOPE, MANAGER_NAT_RENEW_MIN,
                       MANAGER_NAT_RENEW_MAX, MANAGER_NAT_CARETAKER_CONTRACT,
                       MANAGER_NAT_CARETAKER_PROMOTE_BONUS,
                       MANAGER_NAT_CHANGE_MAX_SHARE, MANAGER_REP_RECENT_ALPHA,
                       MANAGER_HIRE_NOISE, MANAGER_ROOKIE_AGE_MIN,
                       MANAGER_ROOKIE_AGE_MAX, MANAGER_RETIRE_AGE_START,
                       MANAGER_RETIRE_AGE_SLOPE, MANAGER_RETIRE_AGE_HARD,
                       manager_retire_prob, manager_retire_hard_age,
                       get_country_grade, national_job_level,
                       national_objective_for, national_result_rep,
                       national_sack_pressure, manager_reputation,
                       manager_career_floor, manager_nt_domestic_prob,
                       manager_nt_foreign_nat_weight)

# [2026-10 D~F 국내 감독 선호] 마지막 D~F 공석(스위치 켜짐)의 후보 요약 — 관찰 러너가 _appoint
# 시점에 읽는다(자국 후보 최고 능력·특례 여부 기록용). 게임 로직은 읽지 않는다. 스위치가 꺼져
# 있거나 D~F가 아니면 None.
LAST_DF_PICK = None

# [2026-10 감독 시장 ② 관찰] 대표팀 부임 기록 — constants.MANAGER_NT_HIRE_TRACE가 True일 때만
# 쌓는다(러너가 연도전환 때 읽고 비운다). 순수 관찰: 난수·DB 쓰기 없음, 게임 로직은 읽지 않는다.
NT_HIRE_OBS = []
_NT_OBS_RANK = {}     # {연도: (팀 id → 팀 OVR 세계 순위)} — 관찰용 캐시


def _nt_obs_team_rank(conn, year):
    if int(year) not in _NT_OBS_RANK:
        _NT_OBS_RANK.clear()
        import manager_ability as _mab
        tovr = _mab.team_ovr_map(conn)
        order = sorted(tovr, key=lambda t: (-tovr[t], t))
        _NT_OBS_RANK[int(year)] = {t: i + 1 for i, t in enumerate(order)}
    return _NT_OBS_RANK[int(year)]


def _nt_obs_mgr(conn, mm, year, score=None, step=None, rank=None):
    d = {"id": int(mm["id"]), "status": mm.get("status"),
         "nat": mm.get("nationality") or "",
         "ab": int(mm.get("manager_ability") or 0),
         "age": (int(year) - int(mm["birth_year"])) if mm.get("birth_year") else None,
         "rep": round(float(mm.get("reputation") or 0.0), 2),
         "rlv": round(float(mm.get("recent_level") or 0.0), 1),
         "cbl": round(float(mm.get("career_best_level") or 0.0), 1),
         "bly": int(mm.get("best_level_year") or 0),
         "js": mm.get("jobless_since"),
         "exempt": bool(mm.get("_df_exempt"))}
    if score is not None:
        d["score"] = round(float(score), 3)
    if step is not None:
        d["step"] = round(float(step), 4)
    if mm.get("status") == "club":
        r = conn.execute(
            """SELECT tm.team_id, tm.start_year, tm.job_level, m.contract_until
               FROM team_managers tm JOIN managers m ON m.id = tm.manager_id
               WHERE tm.manager_id=? AND tm.end_year IS NULL AND tm.job_kind='club'
               ORDER BY tm.start_year DESC LIMIT 1""", (int(mm["id"]),)).fetchone()
        if r:
            d["team"] = int(r[0])
            d["tenure"] = int(year) - int(r[1] or year)
            d["club_lv"] = round(float(r[2] or 0.0), 1)
            d["c_until"] = int(r[3] or 0)
            if rank is not None:
                d["team_rank"] = rank.get(int(r[0]))
    return d

# 마지막 evaluate_national_tournament 호출의 통계 — QA가 읽는다.
# 게임 로직은 쓰지 않는다(클럽 쪽 _LAST_MANAGER_MARKET과 같은 용도).
LAST_NATIONAL_MARKET = {}

# [2026-10 실험 C 관찰 카운터] 대회 연도별 대표팀 평가 결과 — QA(러너)가 연도전환 때 읽고
# 비운다. 게임 로직은 읽지 않고 난수도 쓰지 않는다. {연도: {...}} — _nat_obs_add 참고.
NAT_OBS = {}


def _nat_obs(year):
    return NAT_OBS.setdefault(int(year), {
        "tournaments": 0, "cap_hit": 0, "cap_hit_list": [], "cap_skip_sack": 0,
        "cap_force_renew": 0, "caretaker_end": 0, "eval_full": 0,
        "sack_in_contract": 0, "sack_final_absent": 0, "sack_absent": 0,
        "stay_final_absent": 0, "buckets": {}, "evaluated_links": []})


def _miss_bucket(orank, rrank):
    """미달 폭(A안 보정 전 원래 결과 기준) 구간: 0 / 10~20 / 30~40 / 50+."""
    miss = float(orank) - float(rrank)
    if miss <= 0:
        return "0"
    if miss <= 20:
        return "10-20"
    if miss <= 40:
        return "30-40"
    return "50+"


NAT_LEN_OBS = {}   # [실험 D 관찰] 새로 맺은 정식 계약 길이(년) → 건수. 난수·판정 무관.


def _nat_eval_year(cont, yy):
    """yy가 이 나라의 평가 연도(월드컵·대륙컵, 유럽은 유로도)인가."""
    from constants import (WC_ANCHOR_YEAR, WC_INTERVAL, CONTINENTAL_ANCHOR_YEAR,
                           CONTINENTAL_INTERVAL, REGIONAL_CUP_ANCHOR_YEAR, REGIONAL_CUP_INTERVAL)
    if (yy - WC_ANCHOR_YEAR) % WC_INTERVAL == 0:
        return True
    if cont in ("유럽", "남미", "북미", "북중미", "아시아", "오세아니아", "아프리카"):
        # [2026-10 4단계] 네이션스 2년 주기면 대륙컵(네이션스) 평가 연도 = 본선 해(홀수 해)
        from constants import NATIONS_CYCLE_ENABLED, is_nations_finals_year
        if NATIONS_CYCLE_ENABLED:
            if is_nations_finals_year(yy):
                return True
        elif (yy - CONTINENTAL_ANCHOR_YEAR) % CONTINENTAL_INTERVAL == 0:
            return True
        if cont == "유럽" and (yy - REGIONAL_CUP_ANCHOR_YEAR) % REGIONAL_CUP_INTERVAL == 0:
            return True
    return False


def _nat_contract_until_v2(conn, country_id, year, rng):
    """[2026-10 실험 D ①] 2~4년 뒤 평가 연도 중 하나(둘 이상이면 균등, 실제로는 최대 둘).
    난수는 후보가 둘 이상일 때만 1회. constants MANAGER_NAT_CONTRACT_V2 주석 참고."""
    row = conn.execute("SELECT continent FROM countries WHERE id=?", (country_id,)).fetchone()
    cont = ((row[0] if row else "") or "")
    y = int(year)
    cands = [yy for yy in range(y + 2, y + 5) if _nat_eval_year(cont, yy)]
    if not cands:
        yy = y + 2
        while not _nat_eval_year(cont, yy) and yy < y + 12:
            yy += 1
        cands = [yy]
    nu = cands[0] if len(cands) == 1 else cands[int(rng.random() * len(cands))]
    NAT_LEN_OBS[nu - y] = NAT_LEN_OBS.get(nu - y, 0) + 1
    return nu


def nat_renew_prob(orank, rrank):
    """[2026-10 실험 D ②] 계약 만료 재계약 확률. 스위치를 끄면 예전 식."""
    from constants import MANAGER_NAT_CONTRACT_V2 as _v2, MANAGER_NAT_RENEW_BY_DIFF as _tbl
    if not _v2:
        p = MANAGER_NAT_RENEW_BASE + (rrank - orank) * MANAGER_NAT_RENEW_SLOPE
        return max(MANAGER_NAT_RENEW_MIN, min(MANAGER_NAT_RENEW_MAX, p))
    d = int(round((float(rrank) - float(orank)) / 10.0)) * 10
    d = max(min(_tbl), min(max(_tbl), d))
    return _tbl[d]


def nat_contract_until(conn, country_id, year, rng):
    """[2026-10 실험 C ①] 대표팀 정식 계약의 만료 연도. 스위치를 끄면 예전처럼
    year + randint(MIN, MAX). 켜면 그 나라의 다음 대륙컵 해와 다음 월드컵 해 중 50:50 —
    둘 다 올해보다 뒤라 계약은 1~4년. 대륙컵 해 = 대륙컵 주기(모든 연맹) + 유럽은 유로 주기도.
    대륙이 없는 나라(가상 국가 등)는 월드컵 해. 난수는 어느 쪽이든 1회."""
    from constants import MANAGER_NAT_CONTRACT_EVAL_ALIGN as _align
    from constants import MANAGER_NAT_CONTRACT_V2 as _v2
    if _v2:
        return _nat_contract_until_v2(conn, country_id, year, rng)
    if not _align:
        return year + rng.randint(MANAGER_NAT_CONTRACT_YEARS_MIN, MANAGER_NAT_CONTRACT_YEARS_MAX)
    from constants import (get_next_tournament_year, WC_ANCHOR_YEAR, WC_INTERVAL,
                           CONTINENTAL_ANCHOR_YEAR, CONTINENTAL_INTERVAL,
                           REGIONAL_CUP_ANCHOR_YEAR, REGIONAL_CUP_INTERVAL)
    y1 = int(year) + 1
    wc = get_next_tournament_year(y1, WC_ANCHOR_YEAR, WC_INTERVAL)
    row = conn.execute("SELECT continent FROM countries WHERE id=?", (country_id,)).fetchone()
    cont = ((row[0] if row else "") or "")
    if cont in ("유럽", "남미", "북미", "북중미", "아시아", "오세아니아", "아프리카"):
        from constants import NATIONS_CYCLE_ENABLED, is_nations_finals_year
        if NATIONS_CYCLE_ENABLED:   # [2026-10 4단계] 다음 네이션스 본선 해(홀수 해)
            cy = y1 if is_nations_finals_year(y1) else y1 + 1
        else:
            cy = get_next_tournament_year(y1, CONTINENTAL_ANCHOR_YEAR, CONTINENTAL_INTERVAL)
        if cont == "유럽":
            cy = min(cy, get_next_tournament_year(y1, REGIONAL_CUP_ANCHOR_YEAR,
                                                  REGIONAL_CUP_INTERVAL))
    else:
        cy = wc
    return cy if rng.random() < 0.5 else wc


# ════════════════════════════════════════════════════════════════
# 국가 전력 스냅샷
# ════════════════════════════════════════════════════════════════
def world_squad_percentiles(conn=None):
    """전 세계 국가의 best-XI OVR을 재서 {국가명: (squad_ovr, world_pct)}를
    돌려준다. world_pct는 0.0(최강)~1.0(최약).

    [백분위를 쓰는 이유] 신민용: "한국의 선수 OVR이 세계 20위 수준이면
    16강 목표가 자연스럽고, 황금세대가 와서 10위권으로 올라가면 목표도
    올라갈 수 있음." 절대 OVR 값에 밴드를 걸면 세계 전체 OVR이 인플레될 때
    목표가 같이 밀려버린다. 순위(백분위)로 재면 그게 자동으로 보정된다.

    [전력의 근거] database.get_country_best_xi_ovr — ai_players.
    true_nationality 기준 상위 11명 평균이다. 이게 intl_engine._nat_team_ovr가
    쓰는 값이고, 따라서 경기 시뮬(_match_outcome)이 쓰는 값과 같다.
    목표를 FIFA 등급 같은 다른 지표로 잡으면 구조적으로 달성 불가능한
    목표가 생긴다(③의 자기실현적 목표 함정과 같은 계열)."""
    from database import get_conn, get_country_best_xi_ovr
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        names = [r["name"] for r in conn.execute("SELECT name FROM countries")]
        from military_service import MILITARY_COUNTRY as _MILC   # [2026-10] 가상 국가 제외
        if _MILC in names:
            names = [n for n in names if n != _MILC]
    finally:
        if close:
            conn.close()
    vals = []
    for cn in names:
        v = get_country_best_xi_ovr(cn)
        if v:
            vals.append((float(v), cn))
    if not vals:
        return {}
    vals.sort(reverse=True)            # 강한 순
    n = len(vals)
    out = {}
    for i, (v, cn) in enumerate(vals, 1):
        out[cn] = (v, i / n)
    return out


def _country_ids(conn):
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM countries")}


# [2026-10 신설, 신민용 리포트: "동아시아컵은 10팀이 나가고 대한민국은 최소
# 준우승이 목표인데 8강으로 돼 있다 / 모로코는 5팀뿐인 북아프리카컵에서 4강"]
# 예전엔 세계 백분위 밴드(national_objective_for — 월드컵 기준 눈금)를 모든
# 대회에 그대로 써서, 지역컵에 존재하지도 않는 단계(8강·16강)가 목표로 박히고
# SAFF처럼 약소국끼리 하는 대회는 전원 "예선 경쟁력"이 됐다(3시즌 실측: EAFF
# 일본 8강/한국 16강, 북아프리카컵 모로코 4강·3개국 16강, 유로 우승 목표 5팀).
# 이제 본선 목표는 **그 대회 참가팀 안에서의 전력 순위**와 **그 대회에 실제로
# 있는 토너먼트 단계**로 정한다 — "시드 순위만큼은 가야 한다":
#   1위 우승 · 2위 준우승(결승) · 3~4위 4강 · 5~8위 8강 · 9~16위 16강 ·
#   17~32위 32강 · 그 아래나 토너먼트 자리가 없으면 "본선 진출"(참가 자체)
# 예선 탈락국 평가도 같은 규칙을 쓴다 — 같은 대륙 안 순위가 그 대륙 본선
# 티켓 수 안이었는지로 '본선에 왔어야 했나'를 정한다(_evaluate 참고).
_FIELD_SLOTS = (            # (그 단계가 대회에 있어야 하는 stage, 순위 상한, 목표)
    ("F", 1, "우승"),
    ("F", 2, "준우승"),
    ("SF", 4, "4강"),
    ("QF", 8, "8강"),
    ("R16", 16, "16강"),
    ("R32", 32, "32강"),
)


def national_objective_in_field(field_rank, stages):
    """참가팀 중 전력 순위(1=최강) + 그 대회 토너먼트 단계 집합 → (목표, rank)."""
    for stage, upto, name in _FIELD_SLOTS:
        if stage in stages and field_rank <= upto:
            return name, MANAGER_NAT_STAGE_RANK[name]
    return "본선 진출", MANAGER_NAT_STAGE_RANK["조별리그"]


def fix_national_objectives(tournament_id, conn=None):
    """[진입점 1 — 대회 생성 직후] 참가국의 전력·목표를 굳혀
    national_objectives에 쓴다. 이미 있으면 아무것도 하지 않는다.

    신민용 강조: "목표는 대회 시작 전에 고정. 대회 도중 선수 OVR이 변했다고
    목표까지 바뀌면 이상해짐." 그래서 이 함수는 **멱등**이고, 한 번 쓴
    행은 결과 평가 때 말고는 절대 다시 안 건드린다.

    반환: 새로 쓴 목표 행 수."""
    from database import get_conn
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        t = conn.execute(
            "SELECT id, year, kind, name FROM intl_tournaments WHERE id=?",
            (tournament_id,)).fetchone()
        if not t:
            return 0
        # 예선은 목표 대상이 아니다 — 본선 진출 여부 자체가 결과라
        # 본선 대회 쪽에서 평가한다(_finish_tournament도 예선에선 안 돈다).
        if (t["kind"] or "") in ("wc_qual", "cont_qual", "power_eval",
                                 "power_eval_extra"):
            return 0
        have = conn.execute(
            "SELECT COUNT(*) FROM national_objectives WHERE tournament_id=?",
            (tournament_id,)).fetchone()[0]
        if have:
            return 0                   # 이미 굳었다 — 다시 계산하지 않는다
        entries = [r["country"] for r in conn.execute(
            "SELECT country FROM intl_entries WHERE tournament_id=?",
            (tournament_id,)).fetchall()]
        if not entries:
            return 0
        pct = world_squad_percentiles(conn)
        cids = _country_ids(conn)
        cur_mgr = {r["country_id"]: r["manager_id"] for r in conn.execute(
            "SELECT country_id, manager_id FROM national_team_managers "
            "WHERE end_year IS NULL")}
        # 이 대회에 실제로 있는 토너먼트 단계(대회 생성 때 KO 틀이 먼저 만들어진다
        # — intl_engine에서 _precreate_ko_shell이 이 함수보다 먼저 돈다).
        stages = {r[0] for r in conn.execute(
            "SELECT DISTINCT stage FROM intl_matches WHERE tournament_id=?",
            (tournament_id,)).fetchall()}
        # 참가팀 안에서의 전력 순위(스쿼드 OVR 높은 순, 같으면 세계 백분위 순)
        _order = sorted(entries, key=lambda cn: (-pct.get(cn, (0.0, 1.0))[0],
                                                 pct.get(cn, (0.0, 1.0))[1], cn))
        field_rank = {cn: i for i, cn in enumerate(_order, 1)}
        rows = []
        for cn in entries:
            ovr, wp = pct.get(cn, (0.0, 1.0))
            name, rank = national_objective_in_field(field_rank[cn], stages)
            cid = cids.get(cn, 0)
            rows.append((tournament_id, int(t["year"]), cn, cid, ovr, wp,
                         name, rank, cur_mgr.get(cid, 0)))
        conn.executemany(
            """INSERT OR IGNORE INTO national_objectives(
                   tournament_id, year, country, country_id, squad_ovr,
                   world_pct, objective, objective_rank, manager_id)
               VALUES(?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        return len(rows)
    finally:
        if close:
            conn.close()


# ════════════════════════════════════════════════════════════════
# 대회 결과 → 국가별 도달 단계
# ════════════════════════════════════════════════════════════════
def country_stage_results(conn, tournament_id):
    """참가국별 도달 단계를 {국가명: (결과키, rank)}로 돌려준다.
    결과키는 MANAGER_NAT_STAGE_RANK의 키와 같다.

    [자체 구현한 이유] world_browser.get_country_tournament_results는
    한 번에 한 나라만 보고 호출마다 5질의쯤 쓴다 — 참가국 32~48개면
    150~250질의다. intl_engine._intl_country_stage_weights는 배치지만
    float 가중치만 주고 3·4위를 0.96으로 합친다. 그래서 같은 눈금
    (world_browser._STAGE_ORDER / _PLACEMENT_RANK / _STAGE_RANK)을 쓰면서
    질의 2회로 끝내는 버전을 여기 둔다."""
    winner = conn.execute(
        "SELECT winner FROM intl_tournaments WHERE id=?",
        (tournament_id,)).fetchone()
    winner = (winner["winner"] if winner else "") or ""
    _ORDER = {"R32": 0, "R16": 1, "QF": 2, "SF": 3}
    _KEY = {0: "32강", 1: "16강", 2: "8강", 3: "4강"}
    furthest, runner, third, fourth = {}, None, None, None
    for m in conn.execute(
            "SELECT stage, home, away, home_score, away_score, pso_winner "
            "FROM intl_matches WHERE tournament_id=? "
            "AND stage IN ('R32','R16','QF','SF','F','TP') AND home_score>=0",
            (tournament_id,)):
        stg = m["stage"]
        if stg == "F":
            runner = m["away"] if m["home"] == winner else m["home"]
            continue
        if stg == "TP":
            w = _tp_winner(m)
            third = w
            fourth = m["away"] if w == m["home"] else m["home"]
            continue
        idx = _ORDER.get(stg)
        if idx is None:
            continue
        for nat in (m["home"], m["away"]):
            if nat:
                furthest[nat] = max(furthest.get(nat, -1), idx)
    out = {}
    for r in conn.execute(
            "SELECT country FROM intl_entries WHERE tournament_id=?",
            (tournament_id,)):
        cn = r["country"]
        if cn == winner:
            key = "우승"
        elif cn == runner:
            key = "준우승"
        elif cn == third:
            key = "3위"
        elif cn == fourth:
            key = "4위"
        else:
            key = _KEY.get(furthest.get(cn, -1), "조별리그")
        out[cn] = (key, MANAGER_NAT_STAGE_RANK.get(key, 20))
    return out


def _tp_winner(m):
    """3/4위전 승자 — 승부차기까지 본다(intl_engine._winner_of와 같은 규칙,
    import 순환을 피하려고 여기 최소 구현만 둔다)."""
    if m["pso_winner"]:
        return m["pso_winner"]
    if (m["home_score"] or 0) >= (m["away_score"] or 0):
        return m["home"]
    return m["away"]


# ════════════════════════════════════════════════════════════════
# 대회 종료 평가 + 시장
# ════════════════════════════════════════════════════════════════
def evaluate_national_tournament(tournament_id, conn=None):
    """[진입점 2 — _finish_tournament 직후] 목표 대비 성적을 평가해서
    감독 명성을 갱신하고, 거취(재계약/계약종료/경질)를 정하고, 공석을
    채운다.

    신민용이 못박은 두 가지를 그대로 지킨다:
      · "계약 종료 = 경질로 만들지 않는 게 중요" — 월드컵 8강 후 계약
        만료로 물러나는 건 실패가 아니다. 계약이 남아 있는데 목표를 크게
        밑돌았을 때만 경질이다.
      · "국가 목표는 감독 개인의 경질 여부와 1:1로 연결하면 안 돼" —
        목표 미달은 **확률(압력)**만 올리고, 계약·명성·재임 기간이 함께
        판정에 들어간다.

    반환: 감독이 바뀐 country_id 집합."""
    from database import get_conn
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        return _evaluate(conn, tournament_id)
    finally:
        if close:
            conn.close()


def _evaluate(conn, tid):
    global LAST_NATIONAL_MARKET
    t = conn.execute(
        "SELECT id, year, kind, name, winner FROM intl_tournaments WHERE id=?",
        (tid,)).fetchone()
    if not t:
        return set()
    year = int(t["year"])
    is_wc = (t["kind"] or "") == "world"
    objs = {r["country"]: dict(r) for r in conn.execute(
        "SELECT * FROM national_objectives WHERE tournament_id=?", (tid,))}
    if not objs:
        # 목표가 안 굳어 있으면(구세이브에서 대회가 이미 진행 중이었던 경우)
        # 지금 굳혀서 평가한다 — 목표 없이 평가하면 전원 미달이 된다.
        fix_national_objectives(tid, conn=conn)
        objs = {r["country"]: dict(r) for r in conn.execute(
            "SELECT * FROM national_objectives WHERE tournament_id=?", (tid,))}
        if not objs:
            return set()
    stages = country_stage_results(conn, tid)

    # [예선 탈락국도 평가한다] 신민용이 든 경질 사례가 바로 이것이다:
    # "월드컵 예선 탈락 → 계약기간 남음 → 중도 경질은 경질." 그런데 예선에서
    # 떨어진 나라는 본선 intl_entries에 없어서 위 objs에 안 들어온다 —
    # 그대로 두면 **예선 탈락 감독은 평생 평가를 안 받는다**. 그래서 본선에
    # 못 온 나라를 '예선탈락'(rank 10)으로 같이 평가한다.
    #   · 월드컵(kind='world')은 전 세계가 대상이다.
    #   · 대륙컵/지역컵은 그 대륙 나라만 대상이다(유럽 국가를 아시안컵에
    #     못 왔다고 평가하면 안 된다).
    absent = []
    kind = (t["kind"] or "")
    if kind in ("world", "continent", "region"):
        if kind == "world":
            q = ("SELECT c.id, c.name FROM countries c "
                 "WHERE c.id IN (SELECT country_id FROM national_team_managers "
                 "               WHERE end_year IS NULL)")
            args = ()
        else:
            cont = conn.execute(
                "SELECT continent FROM intl_tournaments WHERE id=?", (tid,)
            ).fetchone()
            cont = (cont["continent"] if cont else "") or ""
            # [2026-10 버그수정 — 실험 C 사전 수정, 기준선·실험군 공통] 대회 continent는 연맹 키
            # ('아메리카' = 남미+북미, '아시아' = 아시아+오세아니아)인데 나라의 continent와 바로
            # 비교해서 남북미 대륙컵은 예선 탈락 평가 대상이 0개국, 아시안컵은 오세아니아가 빠졌다
            # (2000년 실측: 정식 감독 211명 중 31명이 대륙컵 해에 평가 없음). constants.CONFEDERATIONS
            # 매핑으로 대상 나라를 찾는다 — 유럽·아프리카는 예전과 같고, 지역컵(continent NULL)은
            # 예전처럼 대상 없음.
            from constants import CONFEDERATIONS as _CONF
            _confs = list(_CONF.get(cont, [cont])) if cont else [""]
            q = ("SELECT c.id, c.name FROM countries c "
                 "WHERE c.continent IN (%s) AND c.id IN "
                 "  (SELECT country_id FROM national_team_managers "
                 "   WHERE end_year IS NULL)" % ",".join("?" * len(_confs)))
            args = tuple(_confs)
        # [2026-10, 신민용: "각 나라별로 OVR 상위권마다 목표가 달라야"] 예선
        # 탈락국의 기대치도 세계 백분위(월드컵 눈금)가 아니라 **그 예선을 같이
        # 치른 나라들 안에서의 순위**로 잡는다:
        #   · 같은 대륙 안 전력 순위가 그 대륙 본선 티켓 수 안이면 → 본선에
        #     갔어야 했다. 기대 단계는 본선 목표와 같은 규칙
        #     (national_objective_in_field)으로 — 대륙컵은 대륙 안 순위,
        #     월드컵은 전 세계 순위로 이 대회에 있는 단계 중에서 정한다.
        #     (대륙 1위가 예선 탈락하면 '우승 목표 미달'만큼 크게 깎인다)
        #   · 티켓 수 밖이면 → "예선 경쟁력"(rank 10) — 떨어져도 목표 미달 아님.
        # 대륙 티켓 수 = 본선 참가국 중 그 대륙 나라 수(대륙컵이면 참가국 전체).
        pct_all = world_squad_percentiles(conn)
        cont_of = {r["name"]: (r["continent"] or "") for r in conn.execute(
            "SELECT name, continent FROM countries")}

        def _order(names_):
            return sorted(names_, key=lambda cn: (-pct_all.get(cn, (0.0, 1.0))[0],
                                                  pct_all.get(cn, (0.0, 1.0))[1], cn))
        main_stages = {r[0] for r in conn.execute(
            "SELECT DISTINCT stage FROM intl_matches WHERE tournament_id=?", (tid,))}
        world_rank = {cn: i for i, cn in enumerate(_order(list(pct_all.keys())), 1)}
        cont_rank, cont_slots = {}, {}
        for _ct in {cont_of.get(cn, "") for cn in pct_all}:
            _mem = [cn for cn in pct_all if cont_of.get(cn, "") == _ct]
            for i, cn in enumerate(_order(_mem), 1):
                cont_rank[cn] = i
            cont_slots[_ct] = sum(1 for cn in objs if cont_of.get(cn, "") == _ct)
        for r in conn.execute(q, args):
            if r["name"] in objs:
                continue
            cn = r["name"]
            ovr, wp = pct_all.get(cn, (0.0, 1.0))
            _ct = cont_of.get(cn, "")
            _crank = cont_rank.get(cn, 10 ** 6)
            if _crank <= cont_slots.get(_ct, 0):
                _est = world_rank.get(cn, 10 ** 6) if kind == "world" else _crank
                oname, orank = national_objective_in_field(_est, main_stages)
            else:
                oname, orank = "예선 경쟁력", MANAGER_NAT_STAGE_RANK["예선탈락"]
            absent.append({"id": 0, "country": cn, "country_id": r["id"],
                           "world_pct": wp, "objective": oname,
                           "objective_rank": orank, "_absent": True,
                           "_cont_rank": _crank, "_cont_slots": cont_slots.get(_ct, 0)})

    salt = _world_salt()
    stats = {"evaluated": 0, "renewed": 0, "contract_end": 0, "sacked": 0,
             "resigned": 0, "appointed": 0, "caretaker": 0, "promoted": 0,
             "rep_up": 0, "rep_down": 0, "retired": 0, "caretaker_created": 0}

    cur = {}       # country_id -> national_team_managers 행
    for r in conn.execute(
            """SELECT ntm.*, m.reputation, m.recent_perf, m.recent_level,
                      m.career_best_level, m.best_level_year, m.titles_intl,
                      m.birth_year, m.retired
               FROM national_team_managers ntm
               JOIN managers m ON m.id = ntm.manager_id
               WHERE ntm.end_year IS NULL"""):
        cur[r["country_id"]] = dict(r)

    # ── 은퇴 / 정합성 정리 ───────────────────────────────────────
    # [왜 여기서 하나] 클럽 시장(_manager_turnover)은 대표팀 감독을 은퇴
    # 대상에서 제외한다 — 거기서 retired=1을 박으면 재임 행의 end_year가
    # NULL로 남아 "은퇴한 사람이 대표팀 감독으로 등재"되는 상태가 된다
    # (실측 45건). 그래서 대표팀 감독의 은퇴는 이 함수가 맡는다: 재임을
    # 닫고 공석으로 올려서, 같은 패스의 공석 채우기가 후임을 세운다.
    _salt_r = _world_salt()
    # [2026-10 신설, 신민용 확정: "새로 부임한 감독은 최소 다음 평가 시점까지
    # 은퇴 판정에서 제외"] 이 함수는 대회가 하나 끝날 때마다 **전 세계** 대표팀
    # 감독에게 은퇴 판정을 돈다 — 그래서 유로가 끝나 부임한 감독이 같은 오프시즌에
    # 끝나는 다른 대회(골드컵 등) 평가 때 은퇴할 수 있었다(MG09EM: 2001시즌 뒤
    # 스위스 부임 → 한 경기도 지휘하기 전에 은퇴). 올해 부임한 재임(세계 최초
    # 배정 제외)은 올해 판정 대상이 아니다 — 다음 해 첫 평가 때부터 판정한다.
    # 같은 이유로 성적 평가(명성·경질·재계약)도 그 대회를 실제로 지휘한 감독만
    # 받는다(아래 _fresh 참고).
    from database import get_game_start_year
    try:
        _gsy = int(get_game_start_year())
    except Exception:
        _gsy = None
    _first_ids = {r[0]: r[1] for r in conn.execute(
        "SELECT country_id, MIN(id) FROM national_team_managers GROUP BY country_id")}

    def _fresh(link):
        """올해 시즌 후에 부임한 재임인가(세계 최초 배정은 아님)."""
        if int(link.get("start_year") or 0) != year:
            return False
        return not (_first_ids.get(link["country_id"]) == link["id"] and _gsy == year)

    gone = []          # 이 대회 시점에 자리를 비우는 (재임id, country_id, 은퇴여부)
    for cid, link in list(cur.items()):
        if int(link.get("retired") or 0):
            # 어딘가에서 이미 은퇴 처리된 사람 — 재임 행만 열려 있던 상태.
            gone.append((link["id"], cid, False))
            continue
        if _fresh(link):
            continue
        age = year - int(link.get("birth_year") or year)
        # [2026-10 6번 ④] 클럽과 같은 나이 은퇴 함수.
        p_ret = manager_retire_prob(age)
        if p_ret <= 0:
            continue
        rr = random.Random(zlib.crc32(
            f"natret:{_salt_r}:{link['manager_id']}:{year}".encode("utf-8")))
        if rr.random() < p_ret:
            gone.append((link["id"], cid, True))
    for link_id, cid, do_retire in gone:
        conn.execute("UPDATE national_team_managers SET end_year=?, "
                     "end_reason='retired' WHERE id=?", (year, link_id))
        if do_retire:
            mid = cur[cid]["manager_id"]
            conn.execute(
                "UPDATE managers SET retired=1, status='retired', retired_year=? WHERE id=?",
                (year, mid))   # [2026-10] 은퇴 감독 나이 동결용
        cur.pop(cid, None)
    retired_cids = [cid for (_l, cid, _r) in gone]
    stats["retired"] = sum(1 for (_l, _c, r) in gone if r)
    obj_result_updates, mgr_updates, tenure_updates = [], [], []
    ends, vacancies = [], []
    changed = set()

    # 본선 참가국 + 예선 탈락국을 한 흐름으로 평가한다. 이탈 상한은
    # 참가국 수가 아니라 실제 평가 대상 수로 잡는다.
    eval_list = list(objs.values()) + absent
    leave_budget = max(1, int(len(eval_list) * MANAGER_NAT_CHANGE_MAX_SHARE))
    leaving = 0
    _ob = _nat_obs(year)            # [실험 C 관찰] 난수·판정에 영향 없음
    _ob["tournaments"] += 1
    from constants import MANAGER_NAT_CONTRACT_EVAL_ALIGN as _align_c

    for ob in eval_list:
        cn = ob["country"]
        if ob.get("_absent"):
            key, rrank = "예선탈락", MANAGER_NAT_STAGE_RANK["예선탈락"]
        else:
            key, rrank = stages.get(cn, ("조별리그", 20))
            obj_result_updates.append((key, rrank, ob["id"]))
        cid = int(ob["country_id"] or 0)
        link = cur.get(cid)
        if not link:
            if cid:
                vacancies.append(cid)
            continue
        # [2026-10] 그 대회를 지휘한 사람이 지금 감독이 아니면(대회 도중·직후
        # 다른 대회 평가로 교체돼 막 부임한 경우) 평가하지 않는다 — 본선은
        # 대회 시작 때 기록된 감독(national_objectives.manager_id)과 비교하고,
        # 예선탈락 평가(_absent)는 올해 부임한 감독이면 예선을 지휘하지 않았다.
        _ob_mid = int(ob.get("manager_id") or 0)
        if (_ob_mid and _ob_mid != int(link["manager_id"])) or (ob.get("_absent") and _fresh(link)):
            stats["skipped_fresh"] = stats.get("skipped_fresh", 0) + 1
            continue
        stats["evaluated"] += 1
        orank = int(ob["objective_rank"] or 0)
        wp = float(ob["world_pct"] or 1.0)
        rng = random.Random(zlib.crc32(f"nat:{salt}:{cid}:{year}:{tid}".encode("utf-8")))

        # ── 명성 갱신 ────────────────────────────────────────────
        delta = national_result_rep(key, orank, rrank, wp, is_world_cup=is_wc)
        rep = max(0.0, min(100.0, float(link["reputation"] or 0.0) + delta))
        if delta > 0:
            stats["rep_up"] += 1
        elif delta < 0:
            stats["rep_down"] += 1
        # 최근 성적 EWMA — 목표 대비 편차를 -1~+1로 눌러 넣는다. 클럽과
        # 같은 축이라야 클럽 복귀 판정(상한)에 그대로 쓰인다.
        perf = max(-1.0, min(1.0, (rrank - orank) / 40.0))
        rperf = ((1.0 - MANAGER_REP_RECENT_ALPHA) * float(link["recent_perf"] or 0.0)
                 + MANAGER_REP_RECENT_ALPHA * perf)
        titles_intl = int(link["titles_intl"] or 0) + (1 if key == "우승" else 0)
        mgr_updates.append((rep, rperf, titles_intl, link["manager_id"]))
        tenure_updates.append((rrank, link["id"]))

        # ── 거취 ─────────────────────────────────────────────────
        contract_until = int(link["contract_until"] or 0)
        tenure_years = max(0, year - int(link["start_year"] or year))
        role = (link["role"] or "full")
        if role == "caretaker":
            # 임시 감독은 대회를 치르면 임기 종료. 성과가 좋았으면 정식
            # 후보 평가에서 가산점을 받는다(아래 _fill_vacancy).
            ends.append((year, "caretaker_end", link["id"]))
            vacancies.append(cid)
            changed.add(cid)
            _ob["caretaker_end"] += 1
            continue
        # [실험 C 관찰] 분모 = 실제 평가된 정식 감독(skipped_fresh·공석·임시 제외)
        _ob["eval_full"] += 1
        _ob["evaluated_links"].append(int(link["id"]))
        _bk = _ob["buckets"].setdefault(_miss_bucket(orank, rrank),
                                        {"n": 0, "sacked": 0, "contract_end": 0})
        _bk["n"] += 1
        # [실험 C ②] 예선 탈락국은 계약 마지막 해에도 경질 판정 경로로.
        _final_absent = bool(_align_c and ob.get("_absent") and contract_until
                             and contract_until == year)
        if contract_until and (contract_until > year or _final_absent):
            # 계약 중 → 경질 판정만. 목표 미달이 압력을 올리지만 1:1이 아니다.
            # [2026-10 대표팀 경질 확정 — constants.MANAGER_NAT_QUAL_FAIL_EXTRA] 가야 했던 나라의
            # 예선 탈락은 경질 압력에서만 결과를 한 단계 더 낮게 본다(목표·기록·명성·재계약은 그대로).
            _rr_sack = rrank
            if ob.get("_absent") and (ob.get("objective") or "") != "예선 경쟁력":
                from constants import MANAGER_NAT_QUAL_FAIL_EXTRA as _qfx
                _rr_sack = rrank - float(_qfx)
            prob = national_sack_pressure(orank, _rr_sack, rep, tenure_years)
            if leaving >= leave_budget:
                _ob["cap_skip_sack"] += 1
            if leaving < leave_budget and rng.random() < prob:
                ends.append((year, "sacked", link["id"]))
                vacancies.append(cid)
                stats["sacked"] += 1
                leaving += 1
                changed.add(cid)
                _bk["sacked"] += 1
                if _final_absent:
                    _ob["sack_final_absent"] += 1
                else:
                    _ob["sack_in_contract"] += 1
                if ob.get("_absent"):
                    _ob["sack_absent"] += 1
            elif _final_absent:
                _ob["stay_final_absent"] += 1
            continue
        # 계약 만료 → 재계약 협상. **경질이 아니다.** 신민용 강조: "월드컵
        # 8강 후 계약 종료로 재계약하지 않고 무직이 되는 건 실패가 아니야."
        rprob = nat_renew_prob(orank, rrank)
        if leaving >= leave_budget:
            _ob["cap_force_renew"] += 1
        # [실험 D 관찰] 만료 판정을 받은 감독 — 단계 차이별 판정 수·재계약 수(상한 강제 재계약 제외)
        _dk = int(round((float(rrank) - float(orank)) / 10.0)) * 10
        _ex = _ob.setdefault("expiry", {}).setdefault(str(_dk), [0, 0])
        if leaving < leave_budget:
            _ex[0] += 1
        if leaving >= leave_budget or rng.random() < rprob:
            if leaving < leave_budget:
                _ex[1] += 1
            nu = nat_contract_until(conn, cid, year, rng)
            conn.execute(
                "UPDATE national_team_managers SET contract_until=?, "
                "contract_log = COALESCE(contract_log,'') || ? WHERE id=?",
                (nu, f";{year}:{nu}", link["id"]))
            stats["renewed"] += 1
        else:
            ends.append((year, "contract_end", link["id"]))
            vacancies.append(cid)
            stats["contract_end"] += 1
            leaving += 1
            changed.add(cid)
            _bk["contract_end"] += 1

    if leaving >= leave_budget:
        _ob["cap_hit"] += 1
        _ob["cap_hit_list"].append([kind, t["name"], leave_budget, len(eval_list)])
    if obj_result_updates:
        conn.executemany(
            "UPDATE national_objectives SET result=?, result_rank=? WHERE id=?",
            obj_result_updates)
    if mgr_updates:
        conn.executemany(
            "UPDATE managers SET reputation=?, recent_perf=?, titles_intl=? "
            "WHERE id=?", mgr_updates)
    if tenure_updates:
        conn.executemany(
            "UPDATE national_team_managers SET tournaments=COALESCE(tournaments,0)+1, "
            "best_rank=CASE WHEN COALESCE(best_rank,0) < ? THEN ? ELSE best_rank END "
            "WHERE id=?", [(r, r, i) for (r, i) in tenure_updates])
    if ends:
        conn.executemany(
            "UPDATE national_team_managers SET end_year=?, end_reason=? WHERE id=?",
            ends)
        # 물러난 감독은 무직이 된다 — 이제 클럽/대표팀 양쪽 시장의 후보다.
        left_ids = [r["manager_id"] for r in conn.execute(
            "SELECT manager_id FROM national_team_managers WHERE id IN (%s)"
            % ",".join("?" * len(ends)), [e[2] for e in ends])]
        conn.executemany(
            "UPDATE managers SET status='free', jobless_since=COALESCE(jobless_since,?) "
            "WHERE id=? AND retired=0", [(year, m) for m in left_ids])

    # ── 공석 채우기 ──────────────────────────────────────────────
    # 상한은 위에서 **이탈**에만 걸었다. 여기서는 생긴 공석을 전부 채운다 —
    # 부임 쪽에 상한을 걸었더니 감독 없는 대표팀이 9개국 남았다.
    _fill_ids = list(dict.fromkeys(list(retired_cids) + vacancies))
    # [2026-10 6번 ① 감독 시장] 자리 요구 능력은 공석이 있을 때 한 번만 집계.
    _job_ab = None
    if _fill_ids:
        import manager_ability
        _job_ab = manager_ability.JobAbility(conn)
    for cid in _fill_ids:
        if _fill_vacancy(conn, cid, year, salt, stats, job_ab=_job_ab):
            changed.add(cid)
    # [2026-10 6번 1단계] 공석 채우기에서 새로 만든 감독(드묾)의 능력치.
    try:
        import manager_ability
        manager_ability.assign_missing_manager_abilities(conn, year=year, mode="rookie")
    except Exception as _e:
        print(f"[NAT] 감독 능력치 채움 실패(계속 진행): {_e}")
    conn.commit()
    stats["vacancies"] = len(set(vacancies))
    LAST_NATIONAL_MARKET = stats
    if changed:
        # [2026-10 6번 ③-1] 감독이 바뀌었으니 대표팀 감독 경기 보정을 다시 계산하게.
        try:
            import intl_engine
            intl_engine._nat_mgr_bonus_cache.clear()
        except Exception:
            pass
    return changed


def _world_salt():
    from database import get_world_salt
    return get_world_salt()


def nt_expect_accept(mm, job_level, year):
    """[2026-10 감독 시장 ①] B~F 대표팀 제안을 이 감독이 받아들일 확률(기대 수준 대비) —
    constants.MANAGER_NT_EXPECT_* 주석. mm: managers 행(dict, recent_level·career_best_level·
    best_level_year·status·jobless_since). 순수 함수(난수 없음)."""
    from constants import (manager_best_decay, MANAGER_NT_EXPECT_ACCEPT,
                           MANAGER_NT_EXPECT_JOBLESS_DROP, MANAGER_NT_EXPECT_JOBLESS_MIN)
    rlv = float(mm.get("recent_level") or 0.0)
    cbl = float(mm.get("career_best_level") or 0.0)
    bly = int(mm.get("best_level_year") or 0)
    dec = manager_best_decay(max(0, int(year) - bly)) if bly else 1.0
    exp = max(rlv, cbl * dec)
    js = mm.get("jobless_since")
    if (mm.get("status") or "") == "free" and js:
        jl = max(0, int(year) - int(js))
        exp *= max(MANAGER_NT_EXPECT_JOBLESS_MIN, 1.0 - MANAGER_NT_EXPECT_JOBLESS_DROP * jl)
    if exp <= 0.0:
        return 1.0
    r = float(job_level) / exp
    for thr, p_ in MANAGER_NT_EXPECT_ACCEPT:
        if r >= thr:
            return float(p_)
    return float(MANAGER_NT_EXPECT_ACCEPT[-1][1])


def _fill_vacancy(conn, country_id, year, salt, stats, job_ab=None):
    """대표팀 공석 하나를 채운다. 같은 managers 풀에서 뽑고, 정식 감독을
    못 구하면 임시 감독을 세운다.

    신민용:
      · "대표팀 감독 선임에서 국적을 강한 제한 조건으로 쓰면 안 됨. 대신
        국적은 선호도 정도의 가중치로만" — _manager_job_fit의 약한 가산점.
      · "새로운 감독을 하나 생성하는 게 아니라 기존 무직 감독을 임시로
        배정하는 게 좋음" — 임시 감독도 풀에서 뽑는다.
      · "국가 티어가 낮다 = 감독 수준도 낮아야 한다는 식으로 만들면 안 돼"
        — 대표팀 축(MANAGER_NATIONAL_GRADE_LEVEL)이 이미 폭이 좁다."""
    import ai_lifecycle as al
    row = conn.execute("SELECT name FROM countries WHERE id=?",
                       (country_id,)).fetchone()
    if not row:
        return False
    cname = row["name"]
    lv = national_job_level(get_country_grade(cname))
    vr = random.Random(zlib.crc32(f"natfill:{salt}:{country_id}:{year}".encode("utf-8")))
    # [2026-10 6번 ① 감독 시장] 대표팀 요구 능력 — 국대 등급 자리 수준을 팀 OVR
    # 눈금으로 환산(manager_ability.JobAbility.nation). 후보 점수의 보조축.
    import manager_ability as _mab
    if job_ab is None:
        job_ab = _mab.JobAbility(conn)
    _req_ab = job_ab.nation(cname)
    # [2026-10 ⑤-4-② 2차 실험, 신민용 확정] 국대 등급이 높을수록 능력 적합도 보정의 영향력을
    # 키운다(S 매우 강함 → E/F 매우 약함). 확률(점수) 기울기만 — 능력 컷 없음, 국적 확률·
    # ③-3 점수 구조·잡음은 그대로. 꺼져 있으면 곱하지 않는다(예전과 비트 단위로 같다).
    from constants import (MANAGER_NT_GRADE_FIT_ENABLED as _gf_on,
                           MANAGER_NT_ABILITY_FIT_MULT_BY_GRADE as _gf_tbl)
    _gf_mult = (float(_gf_tbl.get(get_country_grade(cname), 1.0)) if _gf_on else None)
    # [2026-10 D~F 국내 감독 선호] constants.MANAGER_NT_DOMPREF_* 주석. 꺼져 있으면 _dp=False라
    # 아래 모든 분기가 건너뛰어진다(예전과 비트 단위로 같다).
    global LAST_DF_PICK
    LAST_DF_PICK = None
    from constants import (MANAGER_NT_DOMPREF_ENABLED as _dp_on, MANAGER_NT_DOMPREF_BONUS as _dp_tbl,
                           MANAGER_NT_DOMPREF_EXEMPT_GRADES as _dp_exg,
                           MANAGER_NT_DOMPREF_EXEMPT_TOPN as _dp_topn)
    _grade = get_country_grade(cname)
    _dp = bool(_dp_on) and _grade in _dp_tbl
    _dp_ex = _dp and _grade in _dp_exg and int(_dp_topn or 0) > 0
    _ex_cand = []          # 자격에서 빠진 자국 감독(특례 후보)

    # [2026-10 재설계, 신민용 확정 — 대표팀 공석 우선순위]
    #   1순위 무직 감독 → 2순위 클럽 현직(맞는 무직 감독이 하나도 없을 때만,
    #   클럽 감독직은 종료) → 3순위 무직 임시 감독 → 4순위 새 감독 생성.
    #   현직 대표팀 감독은 언제나 제외(한 감독은 한 자리, 동시 겸임 없음).
    # 예전엔 무직과 클럽 현직을 같은 점수로 섞어 뽑아서, 3시즌 실측 신규 부임
    # 36건 중 28건(78%)이 클럽 현직을 빼 온 경우였다(무직 풀 3천여 명이 있는데도).
    # 또 후보 조회가 "LIMIT 4000"을 정렬 없이 걸어 감독 번호가 늦은 무직 감독은
    # 아예 후보에 못 오를 수 있었다 — 무직/클럽을 따로 조회한다.
    # 지금 대표팀 감독 교체는 리그가 끝난 오프시즌(대회 종료)에만 일어나므로 클럽
    # 현직을 데려가도 "1년에 한 자리"는 지켜진다. 나중에 시즌 중 경질을 넣는다면
    # 그때는 2순위(클럽 현직)를 꺼야 한다.
    _min_lv = max(0.0, lv * 0.55)
    _rows_free = conn.execute(
        """SELECT m.id, m.nationality, m.reputation, m.recent_level,
                  m.career_best_level, m.best_level_year, m.recent_perf,
                  m.status, m.jobless_since, m.birth_year,
                  COALESCE(m.manager_ability, 0) AS manager_ability
           FROM managers m
           WHERE m.retired=0 AND m.status='free'
             AND COALESCE(m.recent_level,0) >= ?""", (_min_lv,)).fetchall()
    _rows_club = conn.execute(
        """SELECT m.id, m.nationality, m.reputation, m.recent_level,
                  m.career_best_level, m.best_level_year, m.recent_perf,
                  m.status, m.jobless_since, m.birth_year,
                  COALESCE(m.manager_ability, 0) AS manager_ability
           FROM managers m
           WHERE m.retired=0 AND m.status='club'
             AND COALESCE(m.recent_level,0) >= ?
           ORDER BY m.reputation DESC, m.id LIMIT 4000""", (_min_lv,)).fetchall()
    # [2026-10 — 감독 모드 대비] 사용자 감독은 AI 대표팀 후보가 아니다(지금은 빈 집합).
    import team_plan as _tplan
    _u_mids = _tplan.user_manager_ids(conn)
    if _u_mids:
        _rows_free = [r for r in _rows_free if int(r["id"]) not in _u_mids]
        _rows_club = [r for r in _rows_club if int(r["id"]) not in _u_mids]
    # [2026-10 U-23 올림픽 2단계] 올림픽 성과로 "승진 후보"가 된 U-23 감독 — 클럽 현직과 같은 2순위
    # 후보로 넣는다(같은 하한·점수·국적 규칙을 그대로 통과해야 부임). 꺼져 있거나 없으면 빈 목록.
    from competition.oly_manager import promo_candidate_rows as _u23_promo_rows
    from constants import OLY_MGR_PROMO_MIN_GAIN as _u23_gain   # 지금 U-23 자리보다 확실히 높은 자리만
    _u23_rows = [r for r in _u23_promo_rows(conn, year)
                 if float(r.get("recent_level") or 0.0) >= _min_lv and int(r["id"]) not in _u_mids
                 and lv >= float(r.get("recent_level") or 0.0) + _u23_gain]
    if _u23_rows:
        _rows_club = list(_rows_club) + _u23_rows
    free_c, club_c = [], []
    # [2026-10] 같은 나라 재부임 금지 — 경질 5년 / 계약 만료 2년(constants
    # .MANAGER_REHIRE_BAN_YEARS). 방금 이 평가에서 끝난 재임도 end_year=year라 걸린다.
    from constants import MANAGER_REHIRE_BAN_YEARS as _BAN
    _banned = set()
    if _BAN:
        for r in conn.execute(
                "SELECT manager_id, end_year, end_reason FROM national_team_managers "
                "WHERE country_id=? AND end_year IS NOT NULL AND end_year > ?",
                (country_id, year - max(_BAN.values()))).fetchall():
            _b = _BAN.get(r[2] or "")
            if _b and year - int(r[1]) < _b:
                _banned.add(int(r[0]))
    for r in list(_rows_free) + list(_rows_club):
        mm = dict(r)
        if int(mm["id"]) in _banned:
            continue
        # 은퇴 강제 나이에 닿은 감독은 후보에서 뺀다 — 부임하자마자 물러난다.
        if mm.get("birth_year") and year - int(mm["birth_year"]) >= manager_retire_hard_age() - 1:
            continue
        mm["_job_kind"] = "club" if mm["status"] == "club" else "free"
        js = mm.get("jobless_since")
        jl = max(0, year - int(js)) if (js and mm["status"] == "free") else 0
        ok, step, bonus = al._manager_job_fit(
            lv, "nation", mm, year, country=cname,
            base_level=float(mm.get("recent_level") or 0.0), jobless_years=jl)
        if not ok:
            if _dp_ex and (mm.get("nationality") or "") == cname:
                _ex_cand.append(mm)          # [D~F] 특례 후보(상·하한 검사 탈락 자국 감독)
            continue
        if _dp and (mm.get("nationality") or "") == cname:
            bonus = float(_dp_tbl[_grade])   # [D~F] 자국 가산점(기존 4점 대체)
        # 임시 감독으로 성과를 낸 이력이 있으면 가산점.
        promo = 0.0
        if conn.execute(
                "SELECT 1 FROM national_team_managers WHERE manager_id=? "
                "AND role='caretaker' AND best_rank >= 40 LIMIT 1",
                (mm["id"],)).fetchone():
            promo = MANAGER_NAT_CARETAKER_PROMOTE_BONUS
        # [6번 ①] 능력 적합도 — 보조축(스위치 off면 0.0이라 예전과 같다).
        _afit = _mab.ability_fit_bonus(mm.get("manager_ability"), _req_ab)
        if _gf_mult is not None:
            _afit = _afit * _gf_mult
        score = (float(mm.get("reputation") or 0.0) * 0.5 + bonus + promo
                 - abs(float(mm.get("recent_level") or 0.0) - lv) * 0.2
                 + _afit
                 + vr.uniform(0, MANAGER_HIRE_NOISE))
        (free_c if mm["status"] == "free" else club_c).append((score, step, mm))

    if _dp_ex:
        # [D~F 특례] 하한(최근 수준 < 자리×0.55)이라 조회에서 빠진 자국 감독도 모은다 — 무직·클럽 현직.
        _seen = {int(x[2]["id"]) for x in free_c + club_c} | {int(m["id"]) for m in _ex_cand}
        for r in conn.execute(
                """SELECT m.id, m.nationality, m.reputation, m.recent_level,
                          m.career_best_level, m.best_level_year, m.recent_perf,
                          m.status, m.jobless_since, m.birth_year,
                          COALESCE(m.manager_ability, 0) AS manager_ability
                   FROM managers m
                   WHERE m.retired=0 AND m.status IN ('free', 'club') AND m.nationality=?
                     AND COALESCE(m.recent_level,0) < ?
                   ORDER BY m.id""", (cname, _min_lv)).fetchall():
            mm = dict(r)
            if int(mm["id"]) in _seen or int(mm["id"]) in _banned or int(mm["id"]) in _u_mids:
                continue
            if mm.get("birth_year") and year - int(mm["birth_year"]) >= manager_retire_hard_age() - 1:
                continue
            mm["_job_kind"] = "club" if mm["status"] == "club" else "free"
            _ex_cand.append(mm)
        from constants import MANAGER_KIND_SWITCH_MULT as _ksw
        _ex_scored = []
        for mm in _ex_cand:
            promo = 0.0
            if conn.execute(
                    "SELECT 1 FROM national_team_managers WHERE manager_id=? "
                    "AND role='caretaker' AND best_rank >= 40 LIMIT 1",
                    (mm["id"],)).fetchone():
                promo = MANAGER_NAT_CARETAKER_PROMOTE_BONUS
            _afit = _mab.ability_fit_bonus(mm.get("manager_ability"), _req_ab)
            if _gf_mult is not None:
                _afit = _afit * _gf_mult
            score = (float(mm.get("reputation") or 0.0) * 0.5 + float(_dp_tbl[_grade]) + promo
                     - abs(float(mm.get("recent_level") or 0.0) - lv) * 0.2
                     + _afit
                     + vr.uniform(0, MANAGER_HIRE_NOISE))
            mm["_df_exempt"] = True
            # 아래 자리에서 올라가는 것이라 하향 감가 없음 — 종류 전환(클럽/무직→대표팀) 배율만.
            _ex_scored.append((score, float(_ksw), mm))
        _ex_scored.sort(key=lambda x: (-x[0], int(x[2]["id"])))
        for x in _ex_scored[:int(_dp_topn)]:
            (free_c if x[2]["status"] == "free" else club_c).append(x)

    from constants import MANAGER_NT_HIRE_TRACE as _trace_on
    _obs_ctx = {"path": None, "pool": None}

    def _hire(mm):
        if _trace_on:
            # [② 관찰] 부임 직전 상태 — 난수·DB 쓰기 없음.
            _rk = _nt_obs_team_rank(conn, year)
            _pool = _obs_ctx.get("pool")
            NT_HIRE_OBS.append({
                "year": int(year), "country": cname, "grade": get_country_grade(cname),
                "lv": lv, "req_ab": round(float(_req_ab or 0.0), 2), "path": _obs_ctx.get("path"),
                "pick": _nt_obs_mgr(conn, mm, year, rank=_rk),
                "pool": ([_nt_obs_mgr(conn, x[2], year, score=x[0], step=x[1], rank=_rk)
                          for x in _pool] if _pool is not None else None)})
        _appoint(conn, country_id, mm["id"], year, lv, "full",
                 nat_contract_until(conn, country_id, year, vr),
                 vacate_club=(mm["status"] == "club"))
        stats["appointed"] = stats.get("appointed", 0) + 1
        if mm["status"] == "club":
            stats["promoted"] = stats.get("promoted", 0) + 1
        else:
            stats["from_free"] = stats.get("from_free", 0) + 1
        return True

    # [2026-10 감독 국적 시장 — ④ 대표팀] 먼저 자국/외국 감독을 정한다
    # (constants.manager_nt_domestic_prob — 클럽과 다른 표). 외국인이면 후보에
    # 있는 국적들을 수출력 × 연결 × 수준 적합도 가중치(대표팀은 지역·언어·특정
    # 경로를 클럽보다 강하게 — constants.manager_nt_foreign_nat_weight)로 순서를 뽑아, 앞 순서
    # 국적의 후보부터 본다. 정해진 쪽에 맞는 후보가 없으면 반대쪽으로 넘어간다.
    # 그 안에서는 기존 우선순위(무직 → 클럽 현직) 그대로다.
    _side_dom = vr.random() < manager_nt_domestic_prob(cname)
    stats["nt_side_domestic" if _side_dom else "nt_side_foreign"] = \
        stats.get("nt_side_domestic" if _side_dom else "nt_side_foreign", 0) + 1

    def _side(pool, dom):
        return [x for x in pool if ((x[2].get("nationality") or "") == cname) == dom]

    def _foreign_order(pools):
        nats = sorted({x[2].get("nationality") or "" for p in pools for x in p} - {"", cname})
        wl = [(n, manager_nt_foreign_nat_weight(cname, n, lv)) for n in nats]
        wl = [(n, w) for n, w in wl if w > 0]
        order = {}
        while wl:
            tot = sum(w for _n, w in wl)
            x = vr.random() * tot
            for i, (n, w) in enumerate(wl):
                x -= w
                if x <= 0:
                    break
            order[wl.pop(i)[0]] = len(order)
        return order

    # [2026-10 ⑤-4 2차 실험, 신민용 확정] S/A급 대표팀에서만 무직과 클럽 현직을 한 후보
    # 풀에서 같은 점수로 비교한다(무직 우선 해제). 점수·국적 순서·하향 굴림·상한은 그대로,
    # 현직에 별도 수락 확률은 없다. 굴림에서 전부 빠지면 예전처럼 무직 중 맨 앞(없으면 다음
    # 쪽으로). B급 이하·클럽 시장은 그대로. 스위치가 꺼져 있으면 예전과 비트 단위로 같다.
    from constants import (MANAGER_NT_MIXED_POOL_ENABLED as _mix_on,
                           MANAGER_NT_MIXED_POOL_GRADES as _mix_grades,
                           MANAGER_NT_MIXED_CLUB_CAP as _mix_cap,
                           MANAGER_NT_MIXED_SKIP_DOWN_ROLL as _mix_noroll)
    _mixed = bool(_mix_on) and get_country_grade(cname) in _mix_grades
    # [2026-10 감독 시장 ② — 채택(walk)] S/A 혼합 풀의 클럽 현직 후보가 대표팀 제안을
    # 받아들이는지 — constants.MANAGER_NT_CLUB_ACCEPT_* 주석. 꺼져 있으면(None) 아무것도 안 한다.
    from constants import (MANAGER_NT_CLUB_ACCEPT_MODE as _ca_mode,
                           MANAGER_NT_CLUB_ACCEPT_TABLE as _ca_tbl,
                           MANAGER_NT_CLUB_ACCEPT_EARLY_YEARS as _ca_early)
    _ca = _ca_mode if _mixed else None
    _ca_lv = {}
    _ca_y0 = None
    if _ca:
        _ca_lv = {int(r[0]): (float(r[1] or 0.0), int(r[2] or 0)) for r in conn.execute(
            "SELECT manager_id, job_level, start_year FROM team_managers "
            "WHERE end_year IS NULL AND job_kind='club'")}
        # 게임 시작 때 배정된 재임(부임 연도 = 시작 연도)은 "막 부임"으로 보지 않는다.
        _ca_y0 = conn.execute("SELECT MIN(start_year) FROM team_managers").fetchone()[0]

    def _club_accepts(mm):
        """현직 감독의 수락 여부 — 대표팀 자리 수준 ÷ 지금 클럽 자리 수준 구간표. 감독마다
        고정된 난수(공석·연도·감독 id)라 정원 앞/뒤 어디서 물어도 같은 답이고 vr을 쓰지 않는다."""
        jl, sy = _ca_lv.get(int(mm["id"]), (0.0, 0))
        jl = jl or float(mm.get("recent_level") or 0.0)
        if jl <= 0.0:
            return True
        r_ = float(lv) / jl
        p_ = float(_ca_tbl[-1][1])
        for thr, pp in _ca_tbl:
            if r_ >= thr:
                p_ = float(pp)
                break
        if p_ >= 1.0:
            return True
        if (_ca_early and sy and sy != _ca_y0 and int(year) - int(sy) < int(_ca_early)):
            stats["nt_club_declined_early"] = stats.get("nt_club_declined_early", 0) + 1
            return False      # 막 부임한 클럽을 내려가는 자리 때문에 떠나지 않는다
        rr = random.Random(zlib.crc32(
            f"ntclubacc:{salt}:{country_id}:{year}:{int(mm['id'])}".encode("utf-8")))
        ok_ = rr.random() < p_
        if not ok_:
            stats["nt_club_declined"] = stats.get("nt_club_declined", 0) + 1
        return ok_
    if _dp:
        # [2026-10 D~F 국내 감독 선호 — 2차 설계, 신민용 확정] 1차(한 풀 비교)는 외국 국적 순서를
        # 없애 D 대표팀에 세계 최상위 무직 명장이 몰렸다(P2 0.41 → 1.86). 그래서:
        #   ① 외국 대표 1명 = 예전 외국 쪽 절차 그대로(국적·지역 연결 가중치 순서, 무직 → 클럽 현직,
        #      ① 수락·하향 굴림·무직 폴백) — "원래라면 뽑혔을 외국인".
        #   ② 자국 대표 1명 = 자국 후보(기존 자격 + E/F 특례 상위 N명, 자국 클럽 현직도 무직과 같은
        #      첫 순서)를 점수순으로 같은 수락·하향 굴림 → 폴백(거절하지 않은 맨 앞).
        #   ③ 두 대표의 점수(기존 점수 + 잡음, 자국 쪽만 등급별 가산점 포함)를 비교해 높은 쪽 선임.
        #      한쪽이 없으면 다른 쪽, 둘 다 없으면 임시 감독. 능력 상한 같은 강제 제한 없음.
        from constants import (MANAGER_NT_EXPECT_ENABLED as _ex_on2,
                               MANAGER_NT_EXPECT_GRADES as _ex_grades2)
        _exp_on2 = bool(_ex_on2) and _grade in _ex_grades2

        def _is_dom(x):
            return (x[2].get("nationality") or "") == cname

        def _walk(pools):
            """pools를 차례로 — 상위 24명에서 ① 수락·하향 굴림, 첫 pool만 무직 폴백.
            B~F 기존 경로와 같은 규칙. 고른 후보 (점수, 확률계수, mm) 또는 None."""
            for i_, pool in enumerate(pools):
                if not pool:
                    continue
                _declined = set()
                for x in pool[:24]:
                    _s, step, mm = x
                    if _exp_on2:
                        _pa = nt_expect_accept(mm, lv, year)
                        if _pa < 1.0 and vr.random() >= _pa:
                            _declined.add(int(mm["id"]))
                            stats["nt_expect_declined"] = stats.get("nt_expect_declined", 0) + 1
                            continue
                    if step < 1.0 and vr.random() >= step:
                        continue
                    return x
                if i_ == 0:
                    _fb = next((x for x in pool[:24] if int(x[2]["id"]) not in _declined), None)
                    if _fb is not None:
                        return _fb
                    stats["nt_expect_all_declined"] = stats.get("nt_expect_all_declined", 0) + 1
            return None

        # ① 외국 대표 — 예전 _dom=False 분기와 같은 정렬(국적 순서 → 점수).
        _ff = [x for x in free_c if not _is_dom(x)]
        _fc = [x for x in club_c if not _is_dom(x)]
        _ord = _foreign_order((_ff, _fc))
        _fkey = lambda x: (_ord.get(x[2].get("nationality") or "", 10 ** 6), -x[0])
        _ff.sort(key=_fkey)
        _fc.sort(key=_fkey)
        _obs_ctx["path"] = "dp"
        f_pick = _walk((_ff, _fc))
        # ② 자국 대표 — 무직 + 클럽 현직 한 줄, 점수순(가산점은 후보 점수에 이미 들어 있다).
        _dd = [x for x in free_c + club_c if _is_dom(x)]
        _dd.sort(key=lambda x: (-x[0], int(x[2]["id"])))
        d_pick = _walk((_dd,))
        _doms = [x[2] for x in _dd]
        if d_pick is not None and (f_pick is None or d_pick[0] >= f_pick[0]):
            _win = d_pick
        else:
            _win = f_pick
        LAST_DF_PICK = {
            "country": cname, "grade": _grade, "year": int(year),
            "n_dom": len(_doms), "n_dom_regular": sum(1 for m in _doms if not m.get("_df_exempt")),
            "dom_best_ab": max((int(m.get("manager_ability") or 0) for m in _doms), default=None),
            "dom_best_ab_regular": max((int(m.get("manager_ability") or 0) for m in _doms
                                        if not m.get("_df_exempt")), default=None),
            "exempt_ids": [int(m["id"]) for m in _doms if m.get("_df_exempt")],
            "d_score": (round(d_pick[0], 2) if d_pick else None),
            "f_score": (round(f_pick[0], 2) if f_pick else None),
            "d_ab": (int(d_pick[2].get("manager_ability") or 0) if d_pick else None),
            "f_ab": (int(f_pick[2].get("manager_ability") or 0) if f_pick else None),
            "winner": ("dom" if (_win is not None and _win is d_pick) else
                       ("frn" if _win is not None else None))}
        stats["df_vacancy"] = stats.get("df_vacancy", 0) + 1
        if _win is not None:
            stats["df_dom" if _win is d_pick else "df_frn"] = \
                stats.get("df_dom" if _win is d_pick else "df_frn", 0) + 1
            return _hire(_win[2])
    for _dom in (() if _dp else (_side_dom, not _side_dom)):
        f_pool, c_pool = _side(free_c, _dom), _side(club_c, _dom)
        if _dom:
            f_pool.sort(key=lambda x: -x[0])
            c_pool.sort(key=lambda x: -x[0])
        else:
            _ord = _foreign_order((f_pool, c_pool))
            _key = lambda x: (_ord.get(x[2].get("nationality") or "", 10 ** 6), -x[0])
            f_pool.sort(key=_key)
            c_pool.sort(key=_key)
        if _mixed:
            # [2026-10 ⑤-4-② 확정, 신민용] 현직 후보 정원 — 같은 국적 무리(자국 쪽은 한 무리)
            # 안에서 현재 능력 상위 K명만 혼합 풀에 넣는다(같은 능력이면 점수 순). 국적 순서를
            # 정한 뒤에 자르므로 국적 확률은 그대로다. 무직은 자르지 않는다.
            if _ca == "precap":
                # [② precap] 거절한 현직은 정원(K명)을 고르기 전에 뺀다 — 다음 능력 순 현직이 그 칸을 채운다.
                c_pool = [x for x in c_pool if _club_accepts(x[2])]
            if _mix_cap and int(_mix_cap) > 0:
                _byn = {}
                for x in c_pool:
                    _byn.setdefault(x[2].get("nationality") or "", []).append(x)
                _keep = set()
                for _lst in _byn.values():
                    _lst.sort(key=lambda x: (-int(x[2].get("manager_ability") or 0), -x[0]))
                    _keep.update(id(x) for x in _lst[:int(_mix_cap)])
                c_pool = [x for x in c_pool if id(x) in _keep]
            m_pool = f_pool + c_pool
            m_pool.sort(key=(lambda x: -x[0]) if _dom else _key)
            _obs_ctx["path"] = "mixed_dom" if _dom else "mixed_frn"
            _obs_ctx["pool"] = m_pool[:24]
            if m_pool:
                for _s, step, mm in m_pool[:24]:
                    # [2026-10 ⑤-4-② 확정] S/A 대표팀은 하향 굴림(내려가는 자리 수락 확률)을
                    # 건너뛴다 — 지금 눈금에서 S급 대표팀이 유력 후보 전원에게 "내려가는 자리"
                    # (수락 0.45 안팎)라, 점수 1등이 절반 넘게 빠지고 머릿수 많은 쪽이 가져갔다.
                    if step < 1.0 and not _mix_noroll and vr.random() >= step:
                        continue
                    if _ca == "walk" and mm["status"] == "club" and not _club_accepts(mm):
                        continue          # [② walk] 정원 안에서 차례가 왔을 때 거절하면 다음 후보로
                    stats["mixed_" + ("club" if mm["status"] == "club" else "free")] = \
                        stats.get("mixed_" + ("club" if mm["status"] == "club" else "free"), 0) + 1
                    return _hire(mm)
                _f0 = next((x for x in m_pool if x[2]["status"] != "club"), None)
                if _f0 is not None:
                    return _hire(_f0[2])
            continue
        # [2026-10 감독 시장 ①] B~F 대표팀 기대 수준 수락(constants.MANAGER_NT_EXPECT_* 주석).
        # 꺼져 있으면 아래 루프는 예전과 비트 단위로 같다.
        from constants import (MANAGER_NT_EXPECT_ENABLED as _ex_on,
                               MANAGER_NT_EXPECT_GRADES as _ex_grades)
        _exp_on = bool(_ex_on) and get_country_grade(cname) in _ex_grades
        for pool in (f_pool, c_pool):
            if not pool:
                continue
            _obs_ctx["path"] = ("free_" if pool is f_pool else "club_") + ("dom" if _dom else "frn")
            _declined = set()
            for _s, step, mm in pool[:24]:
                if _exp_on:
                    _pa = nt_expect_accept(mm, lv, year)
                    if _pa < 1.0 and vr.random() >= _pa:
                        _declined.add(int(mm["id"]))
                        stats["nt_expect_declined"] = stats.get("nt_expect_declined", 0) + 1
                        continue
                if step < 1.0 and vr.random() >= step:
                    continue
                return _hire(mm)
            # 맞는 무직 감독이 있는데 하향 확률 굴림에서 전부 빠졌다 — 그래도
            # 1순위는 무직이므로 클럽 현직으로 넘어가지 않고 무직 중 맨 앞을 쓴다.
            # [①] 기대 수준으로 거절한 감독은 이 폴백에서도 빠진다 — 전원 거절이면 다음 순위로.
            if pool is f_pool:
                if not _exp_on:
                    return _hire(pool[0][2])
                _fb = next((x for x in pool[:24] if int(x[2]["id"]) not in _declined), None)
                if _fb is not None:
                    return _hire(_fb[2])
                stats["nt_expect_all_declined"] = stats.get("nt_expect_all_declined", 0) + 1

    # 정식 감독을 못 구했다 → 임시 감독. 신민용: "새로운 감독을 하나
    # 생성하는 게 아니라 기존 무직 감독을 임시로 배정하는 게 좋음."
    # [2026-10] 임시 감독도 정해진 쪽(자국/외국) 무직 감독을 먼저 찾는다.
    ct = conn.execute(
        """SELECT id FROM managers WHERE retired=0 AND status='free'
             AND ((nationality = ?) = ?)
           ORDER BY ABS(COALESCE(recent_level,0) - ?) LIMIT 1""",
        (cname, 1 if _side_dom else 0, lv)).fetchone() or conn.execute(
        """SELECT id FROM managers WHERE retired=0 AND status='free'
           ORDER BY ABS(COALESCE(recent_level,0) - ?) LIMIT 1""", (lv,)).fetchone()
    if ct:
        _appoint(conn, country_id, ct["id"], year, lv, "caretaker",
                 year + MANAGER_NAT_CARETAKER_CONTRACT, vacate_club=False)
        stats["caretaker"] = stats.get("caretaker", 0) + 1
        return True
    # 무직 풀이 통째로 비었을 때만(세계 최초 시즌 등) 새로 만든다 — 대표팀이
    # 감독 없이 남는 것보다는 낫다. 평상시엔 위에서 끝난다.
    from database import build_manager_row, MANAGER_INSERT_SQL
    row = build_manager_row(vr, cname, year,
                            age_range=(MANAGER_ROOKIE_AGE_MIN + 6,
                                       MANAGER_ROOKIE_AGE_MAX + 8))
    base = conn.execute("SELECT COALESCE(MAX(id),0) FROM managers").fetchone()[0]
    conn.execute(MANAGER_INSERT_SQL, row)
    from database import assign_manager_codes
    assign_manager_codes(conn)   # [2026-09] 감독 코드(MG…) 백필
    new = conn.execute("SELECT id FROM managers WHERE id > ? ORDER BY id LIMIT 1",
                       (base,)).fetchone()
    if not new:
        return False
    _appoint(conn, country_id, new["id"], year, lv, "caretaker",
             year + MANAGER_NAT_CARETAKER_CONTRACT, vacate_club=False)
    stats["caretaker"] = stats.get("caretaker", 0) + 1
    stats["caretaker_created"] = stats.get("caretaker_created", 0) + 1
    return True


def _appoint(conn, country_id, manager_id, year, job_level, role,
             contract_until, vacate_club=False):
    """부임 기록 + managers.status='nation'. 클럽 현직이었으면 그 자리를
    비운다 — 신민용 확정: "대표팀 감독으로 부임하면 클럽 감독직은 비워야
    함. 한 감독의 active job은 하나만 존재하게."

    비워진 클럽 자리는 end_reason='moved_to_nation'으로 남고, 다음 시즌
    클럽 시장(_manager_turnover)이 감독 없는 팀으로 보고 채운다."""
    if vacate_club:
        conn.execute(
            "UPDATE team_managers SET end_year=?, end_reason='moved_to_nation' "
            "WHERE manager_id=? AND end_year IS NULL AND job_kind='club'",
            (year, manager_id))
    conn.execute(
        """INSERT INTO national_team_managers(
               country_id, manager_id, start_year, end_year, end_reason,
               role, contract_until, job_level, contract_log)
           VALUES(?,?,?,NULL,'',?,?,?,?)""",
        (country_id, manager_id, year, role, contract_until, job_level,
         f"{year}:{int(contract_until)}" if contract_until else ""))
    conn.execute(
        """UPDATE managers SET status='nation', jobless_since=NULL,
                  recent_level=?,
                  best_level_year=CASE WHEN ? > COALESCE(career_best_level,0)
                                       THEN ? ELSE best_level_year END,
                  career_best_level=MAX(COALESCE(career_best_level,0), ?),
                  career_floor=?
           WHERE id=?""",
        (job_level, job_level, year, job_level,
         manager_career_floor(job_level, 0, 0), manager_id))


# ════════════════════════════════════════════════════════════════
# 최초 시드
# ════════════════════════════════════════════════════════════════
def seed_national_managers(conn=None, verbose=True):
    """대표팀 감독이 없는 나라에 초기 감독을 만든다(클럽 쪽
    database._migrate_managers와 같은 역할).

    [왜 새로 만드는가] 세계 최초 시점엔 무직 풀이 비어 있다(전원이 클럽에
    취업 중). 여기서 클럽 감독을 빼오면 첫 시즌에 클럽 211자리가 동시에
    비어버린다. 그래서 시드만 새로 만들고, 이후 교체는 공유 풀에서 뽑는다.

    반환: 새로 부임시킨 수."""
    from database import (get_conn, build_manager_row, MANAGER_INSERT_SQL,
                         get_world_salt, get_game_start_year)
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        row = conn.execute(
            "SELECT current_year FROM season_state WHERE id=1").fetchone()
        year = int(row["current_year"]) if (row and row["current_year"]) \
            else get_game_start_year()
        todo = [dict(r) for r in conn.execute(
            """SELECT c.id, c.name FROM countries c
               WHERE NOT EXISTS (SELECT 1 FROM national_team_managers n
                                 WHERE n.country_id = c.id AND n.end_year IS NULL)""")]
        # [2026-10 병역 시스템] 가상 국가 "군대"는 대표팀이 없다 — 대표팀 감독도 없음.
        from military_service import drop_military_countries
        todo = drop_military_countries(todo, "name")
        if not todo:
            return 0
        salt = get_world_salt()
        mgr_rows, extras = [], []
        # [2026-10 감독 국적 — 세계 최초 생성] 대표팀 시장과 같은 자국/외국 규칙
        # (manager_nt_domestic_prob + manager_nt_foreign_nat_weight).
        from database import seed_manager_nationality
        from constants import manager_nt_foreign_nat_weight
        _nat_cache = {}
        for r in todo:
            rng = random.Random(zlib.crc32(f"natmgr:{salt}:{r['id']}".encode("utf-8")))
            _jl = national_job_level(get_country_grade(r["name"]))
            _nat = seed_manager_nationality(
                conn, r["name"], _jl, salt, f"nat:{r['id']}",
                manager_nt_domestic_prob(r["name"]), manager_nt_foreign_nat_weight, _nat_cache)
            mgr_rows.append(build_manager_row(
                rng, _nat, year,
                age_range=(MANAGER_ROOKIE_AGE_MIN + 6, MANAGER_ROOKIE_AGE_MAX + 12)))
            extras.append((r["id"], _jl, nat_contract_until(conn, r["id"], year, rng)))
        cur = conn.cursor()
        base = cur.execute("SELECT COALESCE(MAX(id),0) FROM managers").fetchone()[0]
        cur.executemany(MANAGER_INSERT_SQL, mgr_rows)
        from database import assign_manager_codes
        assign_manager_codes(cur)   # [2026-09] 감독 코드(MG…) 백필
        new_ids = [r[0] for r in cur.execute(
            "SELECT id FROM managers WHERE id > ? ORDER BY id", (base,)).fetchall()]
        if len(new_ids) != len(extras):
            conn.rollback()
            if verbose:
                print(f"[NAT] 대표팀 감독 시드 중단 — 생성 {len(new_ids)} vs "
                      f"국가 {len(extras)} 불일치")
            return 0
        cur.executemany(
            """INSERT INTO national_team_managers(
                   country_id, manager_id, start_year, end_year, end_reason,
                   role, contract_until, job_level, contract_log)
               VALUES(?,?,?,NULL,'','full',?,?,?)""",
            [(extras[i][0], new_ids[i], year, extras[i][2], extras[i][1],
              f"{year}:{int(extras[i][2])}" if extras[i][2] else "")
             for i in range(len(new_ids))])
        cur.executemany(
            "UPDATE managers SET status='nation', career_best_level=?, "
            "recent_level=?, best_level_year=?, career_floor=? WHERE id=?",
            [(extras[i][1], extras[i][1], year,
              manager_career_floor(extras[i][1], 0, 0), new_ids[i])
             for i in range(len(new_ids))])
        conn.commit()
        if verbose:
            print(f"[NAT] 대표팀 감독 시드 {len(new_ids)}개국")
        return len(new_ids)
    except sqlite3.OperationalError as e:
        if verbose:
            print(f"[NAT] 대표팀 감독 시드 건너뜀({e})")
        return 0
    finally:
        if close:
            conn.close()


def get_national_manager(country_id, conn=None):
    """현재 그 나라 대표팀 감독 행(없으면 None). UI/조회용."""
    from database import get_conn
    close = conn is None
    if conn is None:
        conn = get_conn()
    try:
        return conn.execute(
            """SELECT ntm.*, m.name, m.nationality, m.reputation, m.manager_type,
                      m.style_attack, m.style_buildup, m.style_press
               FROM national_team_managers ntm
               JOIN managers m ON m.id = ntm.manager_id
               WHERE ntm.country_id=? AND ntm.end_year IS NULL LIMIT 1""",
            (country_id,)).fetchone()
    finally:
        if close:
            conn.close()