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


def _load_jsonl_blocks(path: str, verbose: bool = True):
    """affiliate_raw.jsonl을 읽어 국가별 레코드(dict) 리스트로 돌려준다.

    [2026-09 버그수정, 신민용 리포트: "블록 96/97/98 파싱 실패가 뭐고
    중요하냐"] 예전 구현은 파일을 **빈 줄 기준으로 잘라서**(raw.split("\n\n"))
    각 조각이 완전한 JSON 객체 하나라고 가정했다. 그런데 이 파일은 사람이
    읽기 좋게 들여쓴 pretty-print JSON이 이어붙은 형태라, 객체 **안에**
    빈 줄이 들어가는 순간 그 국가 하나가 여러 조각으로 찢어지고 조각마다
    JSON이 깨져 전부 버려졌다 — 실제로 벨라루스 레코드가 그렇게 3조각
    (로그의 블록 96/97/98)으로 갈라져 통째로 누락됐고, 산하팀 16개가
    분류되지 않은 채 일반 팀으로 남아 있었다(210개국/816팀 → 정상은
    211개국/832팀).

    데이터의 빈 줄만 지우면 당장은 고쳐지지만, 이 파일을 다시 편집하다
    빈 줄이 하나 들어가는 순간 그 나라가 또 조용히 사라진다 — 구분자를
    공백에 의존하는 것 자체가 원인이므로 파서를 고친다. json.JSONDecoder.
    raw_decode로 "객체 하나를 읽고, 끝난 위치부터 이어서 다음 객체를 읽는"
    방식이라 객체 사이/안에 공백·줄바꿈이 몇 개 있든 상관이 없다. 진짜
    JSON 문법 오류일 때만 경고를 내고, 그 경우에도 다음 줄머리 '{'를
    찾아 재동기화해서 나머지 국가까지 통째로 잃지 않는다.
    (한 줄에 객체 하나인 정통 JSONL 형식도 그대로 읽힌다 — 하위호환.)"""
    raw = open(path, encoding="utf-8").read()
    dec = json.JSONDecoder()
    records = []
    idx, n = 0, len(raw)
    n_bad = 0
    while idx < n:
        while idx < n and raw[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = dec.raw_decode(raw, idx)
        except ValueError as e:
            n_bad += 1
            if verbose:
                line_no = raw.count("\n", 0, idx) + 1
                print(f"[affiliate_classify] 경고: {line_no}번째 줄 부근 JSON 오류 "
                      f"— 이 레코드만 건너뜀: {e}")
            # 다음 줄머리 '{'로 재동기화 (없으면 종료)
            nxt = raw.find("\n{", idx)
            if nxt < 0:
                break
            idx = nxt + 1
            continue
        records.append(obj)
        idx = end

    if verbose:
        # [2026-09 신설] 같은 나라가 두 번 들어 있으면 뒤엣것이 앞엣것의
        # 분류를 덮어써도 조용히 지나간다 — 데이터 실수를 잡기 위한 경고.
        seen, dup = set(), []
        for r in records:
            cn = r.get("country_name")
            if cn in seen:
                dup.append(cn)
            seen.add(cn)
        if dup:
            print(f"[affiliate_classify] 경고: 중복 국가 레코드 {sorted(set(dup))}")
        _aff = sum(len(r.get("affiliates") or []) for r in records)
        _rev = sum(len(r.get("review") or []) for r in records)
        print(f"[affiliate_classify] 로드: 국가 {len(records)}개 / "
              f"산하팀 {_aff}건 / review {_rev}건"
              + (f" / 파싱 실패 {n_bad}건" if n_bad else ""))
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

    records = _load_jsonl_blocks(jsonl_path, verbose=verbose)

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