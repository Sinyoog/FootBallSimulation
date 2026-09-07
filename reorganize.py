# -*- coding: utf-8 -*-
"""프로젝트 루트 정리 — 도구/QA 스크립트를 tools/ 한 폴더로 모은다.

[왜 필요한가]
현재 루트에 게임 본체 17개와 도구/QA 22개가 같은 층에 섞여 있어서,
"이 파일이 게임에 필요한 건가 아니면 내가 언젠가 만든 진단 스크립트인가"를
파일명만 보고는 구분할 수 없다. 게다가 오타로 생긴 파일 2개
(`tash`, `h origin main --tags --force` — 각각 git diff/git log 출력이
파일로 리다이렉트된 것)가 실제로 git에 커밋돼 있고, .gitignore에는
PowerShell here-string 껍데기(`@"` / `"@ | Set-Content ...`)가 그대로
줄로 들어가 있다.

[이 스크립트가 하는 일]
  1) tools/ 를 만들고 도구/QA 파일을 전부 그리로 이동(git mv, 이력 보존)
  2) tools/_path.py 생성 — 프로젝트 루트를 sys.path에 넣어주는 부트스트랩
  3) 이동한 스크립트마다 `import _path` 한 줄 삽입(독스트링 바로 뒤)
  4) 이동한 스크립트 안의 `os.path.dirname(os.path.abspath(__file__))`
     (= 도구 스크립트에서는 전부 "프로젝트 루트"라는 뜻이었다)을
     한 단계 위로 보정
  5) 오타 파일 2개 git rm, .gitignore 정상화

[안전장치]
  - git 저장소면 git mv/git rm 을 써서 이력을 보존한다(아니면 일반 이동)
  - 이미 옮겨진 파일은 건너뛴다(멱등 — 여러 번 돌려도 안전)
  - --dry-run 으로 실제 변경 없이 계획만 출력
  - 게임 본체(main.py가 import로 도달하는 모듈)는 하나도 안 건드린다

사용:
    python3 reorganize.py --dry-run     # 계획만 확인
    python3 reorganize.py               # 실제 적용
"""
import ast
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "tools")
DRY = "--dry-run" in sys.argv

# ── 루트에 남길 게임 본체 (참고용 — 이 목록은 건드리지 않는다) ──────────
ENGINE = {
    "main.py", "game_engine.py", "database.py", "constants.py",
    "ai_lifecycle.py", "intl_engine.py", "economy.py", "formation_logic.py",
    "power_ranking.py", "world_browser.py", "story_generator.py",
    "goal_gen.py", "opponent_context.py", "promotion_playoff.py",
    "promotion_playoff_engine.py",
    # database.py가 직접 import 하므로 본체 취급
    "affiliate_classify.py",
}

# ── tools/ 로 옮길 것들 ────────────────────────────────────────────────
# 하위 폴더를 더 파지 않고 평평하게 둔다: qa_gk/·qa_awards/ 스크립트가
# 이미 `dirname(dirname(__file__))`로 루트를 찾고 있어서, 깊이가 그대로인
# tools/ 바로 밑으로 오면 그 줄을 손대지 않아도 그대로 맞는다.
MOVE_ROOT = [
    # 헤드리스 실행 / AB 테스트
    "headless_runner.py", "run_ab_test.py", "extended_ab_test.py",
    "multi_seed_validation.py", "long_run_15_3.py", "qa_custom_run.py",
    "wb_audit_run.py",
    # 진단 / 프로파일 / 재현성
    "det_probe.py", "verify_determinism.py", "time_probe.py",
    "scan_setorder.py", "rng_probe.py", "profile_season_transition.py",
    "perf_regression.py", "diagnose.py", "check_save.py",
    "salary_distribution_probe.py",
    # 분석
    "analyze_relegation.py", "analyze_promotion_streaks.py",
    # 산하팀(affiliate)
    "affiliate_integrity.py", "apply_affiliate_to_save.py",
    "validate_affiliate.py",
    # 기타
    "reset_nat.py",
]
MOVE_DIRS = ["qa_gk", "qa_awards"]      # 안의 .py를 tools/ 바로 밑으로

# 오타로 생겨 커밋된 파일
JUNK = ["tash", "h origin main --tags --force"]

# `import _path` 를 넣어야 하는지 판단할 때 쓰는 "프로젝트 모듈" 이름
PROJECT_MODULES = {
    "database", "game_engine", "constants", "ai_lifecycle", "intl_engine",
    "economy", "formation_logic", "power_ranking", "world_browser",
    "story_generator", "goal_gen", "opponent_context", "promotion_playoff",
    "promotion_playoff_engine", "affiliate_classify", "affiliate_integrity",
    "headless_runner", "extended_ab_test",
    "data", "competition", "match_sim", "ui",
}

OLD_ROOT_EXPR = "os.path.dirname(os.path.abspath(__file__))"
NEW_ROOT_EXPR = "os.path.dirname(os.path.dirname(os.path.abspath(__file__)))"

PATH_BOOTSTRAP = '''# -*- coding: utf-8 -*-
"""tools/ 안의 스크립트가 프로젝트 루트 모듈(database, game_engine 등)을
그냥 import 할 수 있게 해주는 부트스트랩.

`python3 tools/foo.py` 로 실행하면 sys.path[0]이 tools/ 가 되기 때문에
`import database` 가 실패한다. 각 스크립트 맨 위에서
    import _path   # noqa: F401
한 줄만 넣어두면 이 모듈이 로드되면서 루트를 sys.path에 끼워 넣는다.

ROOT 를 직접 쓰고 싶을 때는 `import _path` 후 `_path.ROOT` 로 접근한다
(game.db 경로 등).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
'''

GITIGNORE = """# ── 세이브 파일 ──────────────────────────────
game.db
game.history.db
game.db-wal
game.db-shm
game.history.db-wal
game.history.db-shm

# ── 실행 산출물 / 로그 ───────────────────────
*.log
live_sim.log
qa_runs/
*.jsonl
!data/affiliate_raw.jsonl
diag_result.txt
profile_output.prof
salary_probe_*.csv

# ── 진단 스크립트 출력 ───────────────────────
det_*.json
h_*.json
timing_*.txt

# ── 파이썬 ───────────────────────────────────
__pycache__/
*.pyc
"""


# ──────────────────────────────────────────────────────────────────────
def sh(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


IS_GIT = os.path.isdir(os.path.join(ROOT, ".git"))
log = []


def say(msg):
    log.append(msg)
    print(msg)


def move(src_abs, dst_abs):
    rel_s = os.path.relpath(src_abs, ROOT)
    rel_d = os.path.relpath(dst_abs, ROOT)
    if DRY:
        say(f"  [계획] mv {rel_s} -> {rel_d}")
        return True
    if IS_GIT:
        r = sh("git", "mv", "-f", rel_s, rel_d)
        if r.returncode == 0:
            say(f"  git mv {rel_s} -> {rel_d}")
            return True
        # git이 추적하지 않는 파일이면 일반 이동으로 처리
    shutil.move(src_abs, dst_abs)
    say(f"  mv {rel_s} -> {rel_d}")
    return True


def needs_bootstrap(src):
    """이 스크립트가 프로젝트 루트 모듈을 import 하는가?"""
    try:
        tree = ast.parse(open(src, encoding="utf-8").read())
    except Exception:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in PROJECT_MODULES:
                    return True
        elif isinstance(n, ast.ImportFrom):
            if n.module and n.module.split(".")[0] in PROJECT_MODULES:
                return True
    return False


def insert_bootstrap(path):
    """독스트링 바로 뒤에 `import _path` 를 끼워 넣는다(이미 있으면 skip)."""
    src = open(path, encoding="utf-8").read()
    if "import _path" in src:
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        say(f"  ! 파싱 실패라 부트스트랩 삽입 건너뜀: {path}")
        return False
    lineno = 0
    if tree.body and isinstance(tree.body[0], ast.Expr) \
            and isinstance(tree.body[0].value, ast.Constant) \
            and isinstance(tree.body[0].value.value, str):
        lineno = tree.body[0].end_lineno       # 독스트링 마지막 줄
    lines = src.splitlines(keepends=True)
    stub = ("import _path  # noqa: F401  "
            "(tools/ 에서 루트 모듈을 import 하기 위한 sys.path 부트스트랩)\n")
    lines.insert(lineno, stub)
    if not DRY:
        open(path, "w", encoding="utf-8", newline="").write("".join(lines))
    return True


def fix_root_expr(path):
    """도구 스크립트의 `dirname(abspath(__file__))`(= 예전 루트)을 보정."""
    src = open(path, encoding="utf-8").read()
    if OLD_ROOT_EXPR not in src:
        return 0
    if NEW_ROOT_EXPR in src:
        # [멱등성] 이미 보정된 파일. OLD_ROOT_EXPR 은 NEW_ROOT_EXPR 의
        # 부분문자열이라, 가드 없이 두 번 돌리면 dirname 이 3중으로 겹쳐
        # 루트보다 한 단계 위를 가리키게 된다.
        return 0
    n = src.count(OLD_ROOT_EXPR)
    if not DRY:
        open(path, "w", encoding="utf-8", newline="").write(
            src.replace(OLD_ROOT_EXPR, NEW_ROOT_EXPR))
    return n


def main():
    if DRY:
        say("=== DRY RUN — 실제 파일은 건드리지 않는다 ===")
    say(f"프로젝트 루트: {ROOT}  (git={'예' if IS_GIT else '아니오'})")

    if not DRY:
        os.makedirs(TOOLS, exist_ok=True)

    # 1) 루트 도구 파일 이동 (+ 경로식 보정 대상)
    say("\n[1] 루트 도구/QA 스크립트 -> tools/")
    moved_from_root = []
    for fn in MOVE_ROOT:
        src = os.path.join(ROOT, fn)
        dst = os.path.join(TOOLS, fn)
        if not os.path.exists(src):
            if os.path.exists(dst):
                say(f"  (이미 이동됨) {fn}")
                moved_from_root.append(dst)
            else:
                say(f"  ! 없음: {fn}")
            continue
        move(src, dst)
        moved_from_root.append(dst)

    # 2) qa_gk / qa_awards 안의 .py 를 tools/ 바로 밑으로
    #    (깊이가 같으므로 이 파일들은 경로식 보정이 필요 없다)
    say("\n[2] qa_gk/ · qa_awards/ 스크립트 -> tools/")
    for d in MOVE_DIRS:
        dpath = os.path.join(ROOT, d)
        if not os.path.isdir(dpath):
            say(f"  (없음) {d}/")
            continue
        for fn in sorted(os.listdir(dpath)):
            if not fn.endswith(".py"):
                continue
            move(os.path.join(dpath, fn), os.path.join(TOOLS, fn))
        if not DRY:
            shutil.rmtree(dpath, ignore_errors=True)

    # 3) 부트스트랩 모듈
    say("\n[3] tools/_path.py 생성")
    p = os.path.join(TOOLS, "_path.py")
    if os.path.exists(p):
        say("  (이미 있음)")
    elif DRY:
        say("  [계획] tools/_path.py 생성")
    else:
        open(p, "w", encoding="utf-8").write(PATH_BOOTSTRAP)
        say("  생성 완료")

    # 4) 경로식 보정 + import _path 삽입
    say("\n[4] 이동한 스크립트 보정")
    for dst in moved_from_root:
        if not os.path.exists(dst):
            continue
        name = os.path.basename(dst)
        n = fix_root_expr(dst)
        if n:
            say(f"  {name}: 루트 경로식 {n}곳 보정")
        if needs_bootstrap(dst) and insert_bootstrap(dst):
            say(f"  {name}: import _path 삽입")

    # 4-b) 이동 후 의미가 없어진/원래 깨져 있던 import 정리
    say("\n[4-b] 잔여 import 정리")
    # qa_awards/new_awards_qa.py 가 갖고 있던 "루트/qa_gk 를 sys.path에 추가"
    # 줄은, qa_gk 스크립트가 같은 tools/ 로 평평하게 들어오면서 필요 없어졌다
    # (오히려 존재하지 않는 경로를 sys.path에 넣는다).
    dead = ('sys.path.insert(0, os.path.join('
            'os.path.dirname(os.path.dirname(os.path.dirname('
            'os.path.abspath(__file__)))), "qa_gk"))')
    nap = os.path.join(TOOLS, "new_awards_qa.py")
    if os.path.exists(nap):
        src = open(nap, encoding="utf-8").read()
        for cand in (dead, dead.replace(
                'os.path.dirname(os.path.dirname(os.path.dirname('
                'os.path.abspath(__file__))))',
                'os.path.dirname(os.path.dirname(os.path.abspath(__file__)))')):
            if cand in src:
                if not DRY:
                    open(nap, "w", encoding="utf-8", newline="").write(
                        "\n".join(l for l in src.splitlines()
                                  if cand not in l) + "\n")
                say("  new_awards_qa.py: 죽은 qa_gk 경로 추가 줄 제거")
                break
        else:
            say("  new_awards_qa.py: (해당 줄 없음)")

    # validate_affiliate.py 는 원래부터 `from leagues import LEAGUE_DATA` 라
    # 루트에서 실행해도 ImportError 였다(실제 모듈은 data/leagues.py).
    vap = os.path.join(TOOLS, "validate_affiliate.py")
    if os.path.exists(vap):
        src = open(vap, encoding="utf-8").read()
        if "from leagues import" in src:
            if not DRY:
                open(vap, "w", encoding="utf-8", newline="").write(
                    src.replace("from leagues import",
                                "from data.leagues import"))
            say("  validate_affiliate.py: from leagues -> from data.leagues "
                "(이동 전부터 있던 ImportError 수정)")
        else:
            say("  validate_affiliate.py: (이미 정상)")
        src = open(vap, encoding="utf-8").read()
        if "open('affiliate_raw.jsonl'" in src:
            # 이것도 이동 전부터 깨져 있던 부분 — 현재 작업 디렉터리에
            # 의존하는 맨 상대경로였다(실제 파일은 data/ 밑).
            if not DRY:
                open(vap, "w", encoding="utf-8", newline="").write(
                    src.replace(
                        "open('affiliate_raw.jsonl', encoding='utf-8')",
                        "open(os.path.join(_path.ROOT, 'data', "
                        "'affiliate_raw.jsonl'), encoding='utf-8')")
                    .replace("import _path", "import os\nimport _path", 1))
            say("  validate_affiliate.py: affiliate_raw.jsonl 경로를 "
                "data/ 절대경로로 고정")

    # 5) 오타 파일 제거
    say("\n[5] 오타로 커밋된 파일 제거")
    for j in JUNK:
        jp = os.path.join(ROOT, j)
        if not os.path.exists(jp):
            say(f"  (없음) {j}")
            continue
        if DRY:
            say(f"  [계획] rm {j}")
        elif IS_GIT and sh("git", "rm", "-f", "--", j).returncode == 0:
            say(f"  git rm {j}")
        else:
            os.remove(jp)
            say(f"  rm {j}")

    # 6) .gitignore 정상화
    say("\n[6] .gitignore 재작성 (PowerShell here-string 껍데기 제거)")
    gi = os.path.join(ROOT, ".gitignore")
    if DRY:
        say("  [계획] .gitignore 덮어쓰기")
    else:
        open(gi, "w", encoding="utf-8", newline="\n").write(GITIGNORE)
        say("  완료")

    say("\n=== 끝 ===")
    say("확인: python3 -c \"import compileall,sys;"
        "sys.exit(0 if compileall.compile_dir('tools',quiet=1) else 1)\"")
    say("실행 예: python3 tools/headless_runner.py 1")


if __name__ == "__main__":
    main()
