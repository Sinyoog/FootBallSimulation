# -*- coding: utf-8 -*-
"""
실제 세이브(game.db, 시즌5 진행중)를 그대로 이어서 여러 시즌을 헤드리스로
굴리고, 매 시즌 종료 시점마다:
  1) prestige_clubs.py 등재 팀들의 tier 이력
  2) 산하팀-모팀 tier 역전 위반 건수
  3) 최근 저티어에서 승격한 팀의 승격 직후 스쿼드 평균 OVR
을 기록한다. headless_runner와 달리 schedule을 '현재 날짜'부터 시작해서
day_mismatch로 즉시 종료되는 문제를 피한다.
"""
import _path  # noqa: F401  (tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)
import datetime
import os
import shutil
import random
import sys
import json

import database
import game_engine as ge
from affiliate_integrity import run_all_checks

SEED = 12345
N_SEASONS = int(sys.argv[1]) if len(sys.argv) > 1 else 15
SRC_DB = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.abspath("game.db")

_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = os.path.abspath(os.path.join("qa_runs", f"custom_n{N_SEASONS}_{_ts}"))
os.makedirs(out_dir, exist_ok=True)
os.chdir(out_dir)
db_path = os.path.join(out_dir, "custom.db")
shutil.copy(SRC_DB, db_path)
database.DB_PATH = db_path
print(f"[custom] out_dir={out_dir}")

random.seed(SEED)
database.init_db()
conn = database.get_conn()
c = conn.cursor()

import constants
constants.AFFILIATE_PROMOTION_RESTRICTION = True

p = ge.get_player()
if not p:
    ge.create_player(name="Headless Dummy", position="CM", sub_role="")
    p = ge.get_player()

import intl_engine

from data.prestige_clubs import PRESTIGE_TEAMS, prestige_level

# country_name -> team_name -> level
_level_lookup = {}
for country, levels in PRESTIGE_TEAMS.items():
    for lvl, names in levels.items():
        for nm in names:
            _level_lookup[(country, nm)] = lvl


def snapshot_prestige_tiers():
    rows = c.execute("""
        SELECT t.id, t.name, t.current_tier, cn.name as cname
        FROM teams t JOIN countries cn ON t.country_id = cn.id
    """).fetchall()
    out = []
    for r in rows:
        lvl = _level_lookup.get((r["cname"], r["name"]))
        if lvl:
            out.append({"id": r["id"], "name": r["name"], "country": r["cname"],
                        "level": lvl, "tier": r["current_tier"]})
    return out


def snapshot_squad_ovr_by_tier():
    """tier별 팀 평균 스쿼드 OVR 분포(승격 직후 OVR 점프 체크용)."""
    rows = c.execute("""
        SELECT t.id, t.current_tier, AVG(ap.ovr) as avg_ovr, COUNT(*) as n
        FROM ai_players ap JOIN teams t ON ap.team_id = t.id
        WHERE ap.team_id IS NOT NULL
        GROUP BY t.id
    """).fetchall()
    return {r["id"]: {"tier": r["current_tier"], "avg_ovr": r["avg_ovr"], "n": r["n"]} for r in rows}


def snapshot_recent_promotions(year_from, year_to):
    """[2026-08 신설] year_from < year <= year_to 구간에 승격한 팀들
    (promotion_log 기준)의 승격 직후 스쿼드 평균 OVR과, 비교 기준으로
    같은 리그에 원래 있던(승격 안 한) 팀들의 평균 OVR을 같이 기록한다 —
    "승격팀 OVR" 튜닝(4번 작업)에 필요한 핵심 데이터. history.json에
    시즌별로 쌓인다. 구간 방식(직전 호출 이후 ~ 지금까지)이라 시즌
    경계에서 누락되거나 중복 집계되지 않는다."""
    promo_rows = c.execute(
        "SELECT team_name, from_tier, to_tier, to_league_id, year FROM promotion_log "
        "WHERE year > ? AND year <= ? AND to_tier < from_tier", (year_from, year_to)).fetchall()
    out = []
    for pr in promo_rows:
        team_row = c.execute(
            "SELECT id, current_tier, league_id FROM teams WHERE name=?",
            (pr["team_name"],)).fetchone()
        if not team_row:
            continue
        tid = team_row["id"]
        ovr_row = c.execute(
            "SELECT AVG(ovr) as avg_ovr, COUNT(*) as n FROM ai_players WHERE team_id=?",
            (tid,)).fetchone()
        if not ovr_row or ovr_row["avg_ovr"] is None:
            continue
        # 같은 리그(승격해 들어온 곳) 기존 팀들 평균 OVR — 비교 기준
        league_avg_row = c.execute("""
            SELECT AVG(ap.ovr) as avg_ovr FROM ai_players ap
            JOIN teams t ON ap.team_id = t.id
            WHERE t.league_id=? AND t.id != ?
        """, (team_row["league_id"], tid)).fetchone()
        out.append({
            "team": pr["team_name"], "year": pr["year"],
            "from_tier": pr["from_tier"], "to_tier": pr["to_tier"],
            "promoted_team_ovr": round(ovr_row["avg_ovr"], 2),
            "league_avg_ovr": round(league_avg_row["avg_ovr"], 2) if league_avg_row and league_avg_row["avg_ovr"] else None,
        })
    return out


TRACK_TEAM_IDS = set()


def snapshot_tracked():
    out = {}
    for tid in TRACK_TEAM_IDS:
        r = c.execute("SELECT id,name,current_tier,classification_status,parent_team_id FROM teams WHERE id=?", (tid,)).fetchone()
        if r:
            out[tid] = dict(r)
    return out




# 이번 시즌은 현재 날짜부터 시작, 이후 시즌은 1일부터.
st0 = ge.get_state()
start_day = st0["current_day"]
print(f"[custom] 시작 시즌={st0['current_season']} day={start_day}")

history = []
_last_captured_year = -1

for i in range(N_SEASONS):
    st_before = ge.get_state()
    season_before = st_before["current_season"]
    day0 = st_before["current_day"] if i == 0 else 1
    schedule = [(d, "휴식", {}) for d in range(day0, 365)]

    remaining = list(schedule)
    for _guard in range(20):
        ge.advance_days(remaining)
        pending = intl_engine.get_pending_choice()
        if not pending:
            break
        for opt in pending.get("options", []):
            intl_engine.decline_national_team(opt["tournament_id"])
        cur_day = ge.get_state().get("current_day")
        remaining = [item for item in schedule if item[0] >= cur_day]
        if not remaining:
            break

    st_after = ge.get_state()
    season_after = st_after["current_season"]
    print(f"[custom] {i+1}/{N_SEASONS} season {season_before}->{season_after} day={st_after['current_day']}")

    # 무결성 체크 (매 시즌 후)
    result = run_all_checks(c)
    tier1_viol = len(result.get("tier1_violations", []))
    # [2026-08 정책 변경] 모팀·산하팀 동일/역전 tier는 이제 콜업으로
    # 처리되는 정상 상태라 더 이상 "위반"이 아니다 — 참고용 카운트로만 추적.
    parent_coexist = len(result.get("parent_tier_coexisting_pairs", []))
    self_parent = len(result.get("self_parent", []))

    parent_coexist_rows = result.get("parent_tier_coexisting_pairs", [])
    for row in parent_coexist_rows:
        # row 형태: (team_id, name, tier, parent_name, parent_tier) 로 추정 -> 방어적으로 처리
        try:
            TRACK_TEAM_IDS.add(row[0])
        except Exception:
            pass

    ptiers = snapshot_prestige_tiers()
    _cur_year = st_after.get("current_year")
    recent_promos = []
    if _cur_year is not None:
        # [2026-08 버그수정] 시즌이 day364->1로 넘어가면서 st_after의
        # current_year는 이미 "방금 진입한 새해"를 가리킨다 — 실제로 이번
        # 반복 중에 승강 처리(promotion_log 기록)가 일어난 해는 그 전해
        # (_cur_year-1)다. 이 구분 없이 _cur_year를 그대로 상한으로 쓰면
        # 방금 끝난 해의 승격 기록이 "아직 상한을 안 채웠다"는 이유로
        # 이번 반복에서 아예 안 잡히고, 다음 반복에서도 하한(_last_captured_
        # year)이 이미 그 해로 올라가 있어 영원히 빠지는 버그가 있었다
        # (실측: 3회 반복 중 1회차만 잡히고 2·3회차는 전부 0건).
        _completed_year = _cur_year - 1
        if _completed_year > _last_captured_year:
            recent_promos = snapshot_recent_promotions(_last_captured_year, _completed_year)
            _last_captured_year = _completed_year
    history.append({
        "season": season_after,
        "tier1_violations": tier1_viol,
        "parent_tier_coexisting_pairs": parent_coexist,
        "self_parent": self_parent,
        "prestige_snapshot": ptiers,
        "recent_promotions": recent_promos,
    })
    print(f"[custom]   integrity: tier1_viol={tier1_viol} parent_coexist={parent_coexist} "
          f"self_parent={self_parent}")
    print(f"[custom]   tracked: {snapshot_tracked()}")
    print(f"[custom]   승격팀 OVR 샘플: {recent_promos[:5]}")

    database.flush_to_disk()

    if season_after == season_before and i > 0:
        print(f"[custom] 경고: 시즌이 진행되지 않음 ({i+1}번째 반복) — 중단")
        break

with open("history.json", "w", encoding="utf-8") as f:
    json.dump(history, f, ensure_ascii=False, indent=1)

print("[custom] 완료. history.json 저장됨:", os.path.join(out_dir, "history.json"))