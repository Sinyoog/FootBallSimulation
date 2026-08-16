"""
산하팀 관련 데이터 무결성 검사.

- 1부에 NORMAL이 아닌 팀이 있는지
- parent_team_id가 자기 자신을 가리키는지

[2026-08 정책 변경, 신민용 확정: "강등 자체를 막으면 안 된다"] 예전엔
"산하팀 tier는 항상 모팀보다 낮아야 한다(child_tier > parent_tier)"는
불변식을 여기서 위반으로 검사했다. 이제 game_engine._process_promotion_
relegation()과 enforce_affiliate_children_tier()가 이 충돌을 산하팀을
강제로 더 강등시키는 대신 모팀이 산하팀에서 선수를 콜업해 전력만 낮추는
방식으로 처리하도록 바뀌었다 — 즉 모팀과 산하팀이 같은 tier(같은 리그)에
그대로 공존하는 것이 이제 "버그"가 아니라 의도된 정상 상태다. 그래서
이 검사는 더 이상 pass/fail 위반이 아니라 "지금 공존 중인 쌍이 몇 개인지"
보여주는 참고 정보로만 남긴다(check_parent_tier_coexistence).

AFFILIATE_TIER_EXCEPTIONS: leagues.py 원본 데이터 자체의 한계로 인해
1부 위반이 불가피한 극소수 케이스를 국가 단위가 아니라 (국가, 리그명, 팀명)
단위로 정확히 지정한다. 국가 전체를 예외 처리하면 그 나라에 나중에 다른
산하팀이 잘못 들어가도 걸러지지 않으므로, 반드시 팀 단위로 좁혀서 관리한다.
"""

# (country_name, league_name, team_name) — 이 조합만 1부 위반 검사에서 제외.
# 사유: 리히텐슈타인은 실제로도 자국 리그 규모가 극히 작아(대부분 클럽이
# 스위스 리그에서 뜀) 1부(엘리트리그) 자체가 2팀(FC 파두츠, FC 파두츠 U21)
# 뿐이라 U21을 빼면 리그가 성립하지 않는다. leagues.py 원본 데이터의
# 구조적 한계이지 분류/승강 로직의 오류가 아니므로 명시적으로 예외 처리.
AFFILIATE_TIER_EXCEPTIONS = {
    ("리히텐슈타인", "리히텐슈타인 엘리트리그", "FC 파두츠 U21"),
}


def check_tier1_violations(c):
    """1부에 NORMAL이 아닌 팀 중, 명시적 예외에 없는 것만 위반으로 반환."""
    c.execute("""
        SELECT t.id, t.name, co.name AS country_name, l.name AS league_name,
               t.classification_status
        FROM teams t
        JOIN countries co ON co.id = t.country_id
        JOIN leagues l ON l.id = t.league_id
        WHERE t.current_tier = 1 AND t.classification_status != 'NORMAL'
    """)
    rows = c.fetchall()
    violations = []
    excused = []
    for team_id, name, country_name, league_name, status in rows:
        key = (country_name, league_name, name)
        if key in AFFILIATE_TIER_EXCEPTIONS:
            excused.append((team_id, name, country_name, league_name, status))
        else:
            violations.append((team_id, name, country_name, league_name, status))
    return violations, excused


def check_self_parent(c):
    c.execute("SELECT id, name FROM teams WHERE parent_team_id = id")
    return c.fetchall()


def check_parent_tier_coexistence(c):
    """산하팀의 tier가 자기 모팀의 tier와 같거나 역전된 경우를 찾는다.

    [2026-08 정책 변경] 예전엔 이 상태 자체가 "위반"이었고, 발견되면
    산하팀을 강제로 한 티어 더 내리거나(공간이 없으면 "구조적 예외"로
    봐줬다) 했다. 이제는 이런 충돌이 생기면 모팀이 산하팀에서 선수를
    콜업해 산하팀 전력만 낮추고, 산하팀은 그 tier에 그대로 남는다 —
    즉 이 상태 자체가 정상적으로 발생·유지될 수 있는 결과다. 그래서
    더 이상 위반으로 취급하지 않고, "지금 몇 쌍이 공존 중인지"만
    참고용으로 센다(위반/구조적 예외 구분 없음 — 강제 이동이 없으니
    "더 내려갈 자리가 없어서 봐준다"는 개념 자체가 필요 없어짐).

    반환: coexisting_pairs — (team_id, name, tier, parent_name, parent_tier)
    튜플 리스트.
    """
    c.execute("""
        SELECT t1.id, t1.name, t1.current_tier, t2.name, t2.current_tier
        FROM teams t1
        JOIN teams t2 ON t1.parent_team_id = t2.id
        WHERE t1.current_tier <= t2.current_tier
    """)
    return [tuple(row) for row in c.fetchall()]


def run_all_checks(c, verbose=True):
    violations, excused = check_tier1_violations(c)
    self_parent = check_self_parent(c)
    coexisting_pairs = check_parent_tier_coexistence(c)

    if verbose:
        print(f"[integrity] 1부 위반(예외 제외): {len(violations)}건")
        for row in violations:
            print("  ", row)
        print(f"[integrity] 1부 위반이지만 정책 예외로 허용됨: {len(excused)}건")
        for row in excused:
            print("  ", row)
        print(f"[integrity] 자기참조: {len(self_parent)}건")
        # [2026-08] 더 이상 위반이 아니라 참고 정보 — 콜업으로 처리된 뒤
        # 모팀·산하팀이 같은 tier에 공존하는 것 자체가 정상 상태다.
        print(f"[integrity] 모팀·산하팀 동일/역전 tier 공존(참고, 위반 아님): {len(coexisting_pairs)}건")
        for row in coexisting_pairs[:15]:
            print("  ", row)

    return {
        "tier1_violations": violations,
        "tier1_excused": excused,
        "self_parent": self_parent,
        "parent_tier_coexisting_pairs": coexisting_pairs,
        "passed": (len(violations) == 0 and len(self_parent) == 0),
    }