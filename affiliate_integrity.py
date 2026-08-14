"""
산하팀 관련 데이터 무결성 검사.

- 1부에 NORMAL이 아닌 팀이 있는지
- parent_team_id가 자기 자신을 가리키는지
- (나중에 승강 로직 붙으면) affiliate.tier <= parent.tier 충돌도 여기 추가 예정

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


def check_parent_tier_violations(c):
    """산하팀의 tier가 자기 모팀의 tier보다 낮지 않은(같거나 역전된) 경우를 찾는다.

    [2026-08 정책 확정] child_tier <= parent_tier인 경우라도, 자식이 이미
    그 나라의 실제 최하위 tier(country_max_tier)에 있다면 이건 결함이
    아니라 구조적으로 더 손쓸 수 없는 상태다 — 자식을 더 내릴 tier가
    없고(country_max_tier가 절대 상한), AFFILIATE/REVIEW는 승격도 막혀
    있어서 원천적으로 벗어날 방법이 없다(부모가 계속 강등되며 자식이
    이미 있던 바닥까지 따라잡는 경우가 실측으로 반복 확인됨 — 볼프스베르거
    AC B, 반 II, 리히텐슈타인 U21 등). 이런 경우는 위반이 아니라
    "구조적 tier 예외"로 별도 분류하고, 진짜 위반(자식을 더 내릴 여지가
    있었는데 방치된 경우)만 violations로 반환한다.

    반환: (violations, structural_exceptions) — 둘 다 (team_id, name,
    tier, parent_name, parent_tier) 튜플 리스트.
    """
    c.execute("""
        SELECT t1.id, t1.name, t1.current_tier, t1.country_id, t2.name, t2.current_tier
        FROM teams t1
        JOIN teams t2 ON t1.parent_team_id = t2.id
        WHERE t1.current_tier <= t2.current_tier
    """)
    rows = c.fetchall()
    violations = []
    structural_exceptions = []
    for team_id, name, tier, country_id, parent_name, parent_tier in rows:
        c.execute("SELECT MAX(tier) FROM leagues WHERE country_id=?", (country_id,))
        max_tier = c.fetchone()[0]
        if max_tier is not None and tier >= max_tier:
            structural_exceptions.append((team_id, name, tier, parent_name, parent_tier))
        else:
            violations.append((team_id, name, tier, parent_name, parent_tier))
    return violations, structural_exceptions


def run_all_checks(c, verbose=True):
    violations, excused = check_tier1_violations(c)
    self_parent = check_self_parent(c)
    parent_tier_violations, parent_tier_structural = check_parent_tier_violations(c)

    if verbose:
        print(f"[integrity] 1부 위반(예외 제외): {len(violations)}건")
        for row in violations:
            print("  ", row)
        print(f"[integrity] 1부 위반이지만 정책 예외로 허용됨: {len(excused)}건")
        for row in excused:
            print("  ", row)
        print(f"[integrity] 자기참조: {len(self_parent)}건")
        print(f"[integrity] 산하팀 tier <= 모팀 tier 위반(구조적 예외 제외): {len(parent_tier_violations)}건")
        for row in parent_tier_violations[:15]:
            print("  ", row)
        print(f"[integrity] 산하팀 tier <= 모팀 tier이지만 구조적 예외(둘 다 최하위 tier): {len(parent_tier_structural)}건")

    return {
        "tier1_violations": violations,
        "tier1_excused": excused,
        "self_parent": self_parent,
        "parent_tier_violations": parent_tier_violations,
        "parent_tier_structural_exceptions": parent_tier_structural,
        "passed": (len(violations) == 0 and len(self_parent) == 0
                   and len(parent_tier_violations) == 0),
    }