"""[2026-10-30] 포메이션 명단 정렬 기준(formation_logic.roster_line_sort_key) 단위 테스트.

메인 화면 포메이션 명단(ui/formation_widget._starter_sort_key)과 세계 축구 기록실 포메이션 창 명단
(ui/world_browser_window._build_squad_roster_panel)이 같이 쓰는 기준:
  GK → DF(CB·LB·RB·LWB·RWB) → MF(CDM·CM·CAM·LM·RM) → FW(ST·CF·LW·RW), 같은 줄은 OVR 내림차순,
  OVR이 같으면 선수 id 고정 난수(다시 그려도 같은 순서), 포지션·OVR 모름은 맨 뒤.

사용: python tools/test_roster_line_order.py   (DB 안 씀, 즉시 끝남)
"""
import _path  # noqa: F401
import random
import sys

from formation_logic import roster_line_sort_key

FAILS = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f" — {detail}" if detail else ""), flush=True)
    if not cond:
        FAILS.append(name)


LINE = {"GK": 0, "CB": 1, "LB": 1, "RB": 1, "LWB": 1, "RWB": 1, "CDM": 2, "CM": 2, "CAM": 2, "LM": 2, "RM": 2,
        "ST": 3, "CF": 3, "LW": 3, "RW": 3}


def order(rows):
    return sorted(rows, key=lambda r: roster_line_sort_key(r[0], r[1], r[2]))


# 사용자 리포트 모양: CB LB RB ST CAM CM CDM ST (+GK)
rows = [("CB", 70, 1), ("LB", 72, 2), ("RB", 65, 3), ("ST", 80, 4), ("CAM", 66, 5), ("CM", 75, 6),
        ("CDM", 71, 7), ("ST", 68, 8), ("GK", 60, 9), ("RW", 77, 10), ("LM", 69, 11)]
got = order(rows)
check("줄 순서 GK → DF → MF → FW", [LINE[r[0]] for r in got] == sorted(LINE[r[0]] for r in rows),
      " ".join(r[0] for r in got))
ok = all(got[i][1] >= got[i + 1][1] for i in range(len(got) - 1) if LINE[got[i][0]] == LINE[got[i + 1][0]])
check("같은 줄 안에서는 OVR 내림차순(포지션 글자와 무관)", ok, " ".join(f"{r[0]}{r[1]}" for r in got))
check("예시: MF 줄 = CM75 CDM71 LM69 CAM66", [f"{r[0]}{r[1]}" for r in got if LINE[r[0]] == 2]
      == ["CM75", "CDM71", "LM69", "CAM66"])

# 동점: 고정 난수 — 입력 순서를 섞어도 결과 같음, id 순서와는 다름(무작위)
tie = [("CM", 70, pid) for pid in range(1, 21)]
base = [r[2] for r in order(tie)]
same = True
rng = random.Random(3)
for _ in range(20):
    t2 = tie[:]
    rng.shuffle(t2)
    same &= [r[2] for r in order(t2)] == base
check("OVR 동점 순서는 다시 그려도 같다(입력 순서와 무관)", same)
check("OVR 동점 순서는 id 순서가 아니다(무작위)", base != sorted(base) and base != sorted(base, reverse=True), str(base))
st0 = random.getstate()
order(tie)
check("게임 난수(random)를 쓰지 않는다", random.getstate() == st0)

# 모름 처리
got = order([("-", 90, 1), ("ST", None, 2), ("ST", 50, 3), ("", 99, 4), ("GK", None, 5)])
check("OVR 모름은 그 줄 맨 뒤, 포지션 모름은 전체 맨 뒤",
      [r[2] for r in got][:3] == [5, 3, 2] and set(r[2] for r in got[3:]) == {1, 4}, str(got))

print()
print("실패 0건" if not FAILS else f"실패 {len(FAILS)}건: {FAILS}")
sys.exit(1 if FAILS else 0)