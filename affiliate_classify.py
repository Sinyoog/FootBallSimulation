"""
data/affiliate_raw.jsonl 을 읽어서 teams.classification_status /
parent_team_id / review_reason 을 채우는 로직.

이 모듈은 database.py의 seed_initial_data()에서 새 게임 생성 시
자동으로 호출된다(_insert_leagues_and_teams 직후). 기존 커넥션/커서를
그대로 받아서 쓰므로, database.py의 인메모리 커넥션 풀과 별도의
sqlite3.connect()를 새로 열지 않는다 — 별도 커넥션을 열면 인메모리
모드에서 아직 디스크에 flush되지 않은 상태와 어긋날 수 있기 때문.

매칭 규칙 (country_id는 jsonl에 없음 — country_name만으로 매칭):
  1) countries.name == country_name 으로 country_id 조회
  2) 팀 매칭: (name, country_id, current_tier) 우선
     - 정확히 1개 매칭되면 사용
     - 0개면 tier 무시하고 (name, country_id)만으로 재시도
     - 그래도 0개/2개 이상이면 ambiguous로 보고 건드리지 않음
       (동명 팀 중복 케이스를 자동으로 아무거나 골라잡지 않음)
  3) parent_team_name도 동일 규칙으로 매칭 실패 시
     classification_status='REVIEW', review_reason='parent_not_found'로 강등
  4) jsonl에 없는 나머지 팀은 컬럼 기본값 그대로 'NORMAL' (손대지 않음)
"""
import json
import os
from collections import defaultdict

_DEFAULT_JSONL_PATH = os.path.join(os.path.dirname(__file__), "data", "affiliate_raw.jsonl")


def _load_jsonl_blocks(path: str):
    raw = open(path, encoding="utf-8").read()
    blocks = [b for b in raw.split("\n\n") if b.strip()]
    records = []
    for i, b in enumerate(blocks, 1):
        try:
            records.append(json.loads(b))
        except Exception as e:
            print(f"[affiliate_classify] 경고: 블록 {i} 파싱 실패, 건너뜀: {e}")
    return records


def _find_team_id(c, name, country_id, tier):
    c.execute(
        "SELECT id FROM teams WHERE name=? AND country_id=? AND current_tier=?",
        (name, country_id, tier),
    )
    rows = c.fetchall()
    if len(rows) == 1:
        return rows[0][0], "exact"
    if len(rows) > 1:
        return None, "ambiguous_with_tier"

    c.execute("SELECT id FROM teams WHERE name=? AND country_id=?", (name, country_id))
    rows = c.fetchall()
    if len(rows) == 1:
        return rows[0][0], "matched_ignoring_tier"
    if len(rows) == 0:
        return None, "not_found"
    return None, "ambiguous_no_tier"


def apply_classification(c, jsonl_path: str = None, verbose: bool = True):
    """c: sqlite3 cursor (이미 열려있는 커넥션의 커서를 그대로 받는다).
    반환: 통계 dict (호출부에서 로그로 남기거나 무시해도 됨)."""
    jsonl_path = jsonl_path or _DEFAULT_JSONL_PATH
    if not os.path.exists(jsonl_path):
        if verbose:
            print(f"[affiliate_classify] {jsonl_path} 없음 — 분류 적용 건너뜀")
        return {}

    c.execute("SELECT id, name FROM countries")
    country_name_to_id = {name: cid for cid, name in c.fetchall()}

    records = _load_jsonl_blocks(jsonl_path)

    stats = defaultdict(int)
    problems = []

    for rec in records:
        cname = rec.get("country_name")
        country_id = country_name_to_id.get(cname)
        if country_id is None:
            stats["country_not_found"] += 1
            problems.append(f"country_name '{cname}' 이(가) countries에 없음")
            continue

        for item in rec.get("affiliates", []):
            tname, tier, pname = item["team_name"], item["tier"], item["parent_team_name"]
            team_id, reason = _find_team_id(c, tname, country_id, tier)
            if team_id is None:
                stats["team_unmatched"] += 1
                problems.append(f"[{cname}] team 매칭 실패({reason}): {tname}(t{tier})")
                continue

            parent_id, preason = _find_team_id(c, pname, country_id, tier)
            if parent_id is None:
                c.execute(
                    "UPDATE teams SET classification_status='REVIEW', parent_team_id=NULL, "
                    "review_reason='parent_not_found' WHERE id=?",
                    (team_id,),
                )
                stats["downgraded_to_review"] += 1
                problems.append(f"[{cname}] parent 매칭 실패({preason}): {tname} -> {pname}")
                continue

            c.execute(
                "UPDATE teams SET classification_status='AFFILIATE', parent_team_id=?, "
                "review_reason=NULL WHERE id=?",
                (parent_id, team_id),
            )
            stats["affiliate_applied"] += 1

        for item in rec.get("review", []):
            tname, tier, rreason = item["team_name"], item["tier"], item["review_reason"]
            team_id, reason = _find_team_id(c, tname, country_id, tier)
            if team_id is None:
                stats["team_unmatched"] += 1
                problems.append(f"[{cname}] review team 매칭 실패({reason}): {tname}(t{tier})")
                continue
            c.execute(
                "UPDATE teams SET classification_status='REVIEW', parent_team_id=NULL, "
                "review_reason=? WHERE id=?",
                (rreason, team_id),
            )
            stats["review_applied"] += 1

    if verbose:
        print(f"[affiliate_classify] 적용 완료: {dict(stats)}")
        if problems:
            print(f"[affiliate_classify] 매칭 문제 {len(problems)}건 (요약):")
            for p in problems[:20]:
                print("  ", p)
            if len(problems) > 20:
                print(f"   ... 외 {len(problems)-20}건")

    return dict(stats)