"""et_score_binding_qa.py — 대회 경기 UPDATE의 "? 개수 == 바인딩 개수" 검사
(2026-09 신설)

[왜 필요한가] 연장 스코어 컬럼(home_score_90/away_score_90/went_extra_time)을
추가하면서 각 엔진의 "내 경기" UPDATE에 SET 3개와 값 3개를 같이 끼워넣었다.
여기서 한쪽만 틀리면 sqlite3.ProgrammingError("Incorrect number of bindings")가
나는데, 이 코드 경로는 **그 대회에서 내가 실제로 그 경기를 뛸 때만** 실행된다
— 예를 들어 클럽월드컵 KO에 내 팀이 올라간 시즌에만. 헤드리스 스모크로도
잘 안 걸리고, 걸릴 때는 이미 세이브를 몇 시즌 굴린 뒤다.

그래서 실행 대신 소스를 직접 읽어 검사한다(AST):
  · SQL 문자열 안의 '?' 개수  (f-string 안의 {ET_SCORE_SET_SQL}은 3으로 환산)
  · execute()에 넘긴 파라미터 튜플의 원소 개수
    (*et_score_values(...) 같은 언팩은 3으로 환산)
두 값이 다르면 실패로 보고한다.

사용법: python tools/et_score_binding_qa.py
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 검사 대상: 연장 스코어 SET을 넣은 "내 경기" UPDATE가 있는 파일들.
TARGET_FILES = [
    "intl_engine.py",
    "competition/champions_engine.py",
    "competition/cup_engine.py",
    "competition/club_world_cup_engine.py",
    "competition/lower_cup_engine.py",
    "competition/competition_common.py",
    "competition/domestic_super_cup_engine.py",
    "promotion_playoff_engine.py",
]

# SQL 조각 이름 → 그 조각이 품고 있는 '?' 개수. database.py의 정의와
# 어긋나면 안 되므로 실제 모듈에서 읽어온다(하드코딩 금지).
def _fragment_placeholders():
    sys.path.insert(0, _ROOT)
    import database as d
    return {"ET_SCORE_SET_SQL": d.ET_SCORE_SET_SQL.count("?")}


# 언팩 호출 이름 → 그 호출이 돌려주는 원소 개수.
UNPACK_ARITY = {
    "et_score_values": 3,
}


def _sql_placeholders(node, fragments):
    """execute()의 첫 인자(문자열 또는 f-string)에서 '?' 개수를 센다.
    치환식이 우리가 아는 SQL 조각이면 그 조각의 '?' 개수를 더하고,
    모르는 치환식이면 (테이블명 등) 0으로 본다 — '?'를 품은 조각은
    fragments에 등록돼 있어야 한다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.count("?"), []
    if isinstance(node, ast.JoinedStr):
        total = 0
        unknown = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                total += part.value.count("?")
            elif isinstance(part, ast.FormattedValue):
                name = None
                if isinstance(part.value, ast.Name):
                    name = part.value.id
                elif isinstance(part.value, ast.Attribute):
                    name = part.value.attr
                if name in fragments:
                    total += fragments[name]
                elif name is not None:
                    unknown.append(name)
        return total, unknown
    return None, []


def _binding_count(node):
    """execute()의 두 번째 인자(튜플/리스트)의 원소 개수. 언팩(*f(...))은
    UNPACK_ARITY로 환산한다. 셀 수 없으면 None."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    total = 0
    for el in node.elts:
        if isinstance(el, ast.Starred):
            fn = el.value
            name = None
            if isinstance(fn, ast.Call):
                if isinstance(fn.func, ast.Name):
                    name = fn.func.id
                elif isinstance(fn.func, ast.Attribute):
                    name = fn.func.attr
            if name in UNPACK_ARITY:
                total += UNPACK_ARITY[name]
            else:
                return None   # 알 수 없는 언팩 — 판정 불가
        else:
            total += 1
    return total


def check_file(rel_path, fragments):
    path = os.path.join(_ROOT, rel_path)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, filename=rel_path)

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("execute", "executemany")):
            continue
        if len(node.args) < 1:
            continue
        n_ph, unknown = _sql_placeholders(node.args[0], fragments)
        if n_ph is None:
            continue
        # UPDATE ... SET 중에서도 연장 스코어를 다루는 문장만 본다.
        raw = ast.get_source_segment(src, node.args[0]) or ""
        if "ET_SCORE_SET_SQL" not in raw and "home_score_90" not in raw:
            continue
        n_bind = _binding_count(node.args[1]) if len(node.args) > 1 else None
        results.append((node.lineno, n_ph, n_bind, unknown))
    return results


def main():
    fragments = _fragment_placeholders()
    print(f"SQL 조각 '?' 개수: {fragments}")
    print()

    total_sites = 0
    failures = []
    for rel in TARGET_FILES:
        try:
            sites = check_file(rel, fragments)
        except FileNotFoundError:
            print(f"{rel}: 파일 없음 — 건너뜀")
            continue
        if not sites:
            print(f"{rel}: 연장 스코어 UPDATE 없음")
            continue
        for lineno, n_ph, n_bind, unknown in sites:
            total_sites += 1
            if n_bind is None:
                status = "판정불가(바인딩 형태를 셀 수 없음)"
                failures.append(f"{rel}:{lineno} {status}")
            elif n_ph == n_bind:
                status = f"OK  ? {n_ph} == 바인딩 {n_bind}"
            else:
                status = f"불일치  ? {n_ph} != 바인딩 {n_bind}"
                failures.append(f"{rel}:{lineno} {status}")
            extra = f"  (미등록 치환식: {unknown})" if unknown else ""
            print(f"{rel}:{lineno}  {status}{extra}")

    print()
    print(f"검사한 UPDATE {total_sites}곳")
    if failures:
        print("=== 실패 ===")
        for f in failures:
            print(f"  {f}")
        return 1
    print("=== 전체 통과: 모든 연장 스코어 UPDATE의 ? 개수와 바인딩 개수가 일치 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())