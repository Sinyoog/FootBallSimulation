# -*- coding: utf-8 -*-
"""sub_positions.py — 주포·부포(부포지션) 획득·저장·조회 공용 모듈.

[2026-10 신설, 신민용 확정 — 주포·부포 ③ 명세]
  · 부포는 감점 없이 100% 적합(미스매치 0). 주발 적합도는 따로 그대로 적용한다.
  · 주포를 부포보다 자동으로 우선하지 않는다(선발은 유효값만 본다).
  · 선수당 최대 1개, 한 번 얻으면 영구 유지. GK는 부포를 얻지 못하고, GK 슬롯을 부포로 얻지도 못한다.
  · 처음부터 정해지지 않고 시간이 지나며 생긴다 — 아래 획득 규칙(A안).
  · 적용 범위: AI 클럽(최적 배치·포메이션 선택 적합도·시즌 포지션 기록·경기 중 교체 판단),
    보충 목표표(squad_targets.held_assign), 내 선수 클럽 경기, UI 표시·검색.
    제외: 이적시장, 대표팀(내 선수 대표팀 배치 포함), 스쿼드 맞바꾸기.

── 획득 규칙(A안: 2시즌 연속) ───────────────────────────────────────
  AI 선수: Y-1·Y 두 시즌의 29주(hist.ai_player_position_history_half)·43주
    (hist.ai_player_position_history) 기록 4개가 모두 같은 슬롯 S이고
      1) S ≠ 등록 포지션 — 두 스냅샷 함수(_snapshot_season_positions/_snapshot_team_lineup_half)는
         선발이면 배치 슬롯명, 벤치면 등록 포지션을 기록하므로(ai_players.position은 바뀌지 않는다)
         "기록 ≠ 등록"이면 그 스냅샷에서 비주포 슬롯 선발이었다는 뜻이다.
      2) S가 호환표(POSITION_COMPAT[등록])의 1~2순위 대안
      3) S가 LB·LWB·RB·RWB면 주발 일치 또는 양발(주발 미배정 '' 포함)
      4) 등록·S 모두 GK 아님, ST↔CF(감점 없는 동등 관계) 아님
      5) 아직 부포가 없는 현역 선수
  내 선수: my_player_position_history는 시즌당 1개이고 오프시즌이 끝난 뒤(이적·포메이션 반영 후)
    "다음 시즌 배치"로 기록된다 — 오프시즌 Y의 판정 시점엔 Y-2·Y-1 라벨 두 개(= Y-1·Y 시즌 시작
    배치)가 있다. 이 두 개가 같은 슬롯 S이고 위 1)~5)를 만족하면 얻는다. AI(시즌당 2개 × 2시즌)와
    기록 개수·시점이 다르다 — 첫 획득도 AI보다 한 시즌 늦다.
  난수를 쓰지 않는다(결정론). 처리 순서는 player_id 순.

── 저장 ────────────────────────────────────────────────────────────
  main.ai_player_sub_positions(player_id PK, position, gained_year) — 내 선수는 player_id=-1
  (world_browser.MY_PLAYER_ID / ai_lifecycle._MY_LINEUP_ID와 같은 값). 은퇴해도 지우지 않는다.
  새 게임(database.reset_game_data)에서 전부 지운다.

── 조회 ────────────────────────────────────────────────────────────
  메모리 캐시 {player_id: 부포}. attach(rows)가 범위 안 호출부의 선수 dict에만 "sub_pos" 키를
  붙인다 — 스위치가 꺼져 있거나 부포가 하나도 없으면 아무것도 붙이지 않아 dict가 예전과 완전히
  같다(기존 경로 100%). 기존 SQL은 고치지 않는다(JOIN 추가는 행 순서→난수 소비를 바꿀 수 있다).
"""
from constants import POSITION_COMPAT, FOOT_SIDED_SLOTS, FOOT_BOTH

MY_ID = -1
TABLE = "ai_player_sub_positions"
_EQUIV = {("ST", "CF"), ("CF", "ST")}
_MAX_COMPAT_RANK = 2          # 호환표 1~2순위 대안만(0 = 주포 자신)

_CACHE = None                 # {player_id: position} — None이면 아직 안 읽음


def enabled():
    import constants
    return bool(getattr(constants, "SUB_POSITIONS_ENABLED", False))


def ensure_table(c):
    c.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
        player_id INTEGER PRIMARY KEY,
        position TEXT NOT NULL,
        gained_year INTEGER NOT NULL)""")


def clear():
    """캐시를 비운다(새 게임·DB 재로드 때). 다음 조회 때 다시 읽는다."""
    global _CACHE
    _CACHE = None


def _load():
    global _CACHE
    if _CACHE is None:
        try:
            from database import get_conn
            conn = get_conn()
            try:
                _CACHE = {int(r[0]): r[1] for r in conn.execute(
                    f"SELECT player_id, position FROM {TABLE}").fetchall()}
            finally:
                conn.close()
        except Exception:
            _CACHE = {}
    return _CACHE


def active():
    """부포가 실제로 쓰이는 상태인가 — 스위치 ON이고 부포 보유자가 1명 이상."""
    return enabled() and bool(_load())


def get(player_id):
    if not enabled():
        return None
    return _load().get(player_id)


def my_sub():
    return get(MY_ID)


def attach(rows, id_key="id"):
    """선수 dict 목록에 "sub_pos"를 붙인다(보유자만). 비활성이면 아무것도 안 한다."""
    if not active():
        return rows
    cache = _CACHE
    for d in rows:
        if d is None:
            continue
        s = cache.get(d.get(id_key))
        if s:
            d["sub_pos"] = s
    return rows


def compat_rank(registered, slot):
    c = POSITION_COMPAT.get(registered, [registered])
    return c.index(slot) if slot in c else None


def eligible(registered, slot, foot):
    """획득 조건 2)~4) — 등록 포지션 registered인 선수가 slot을 부포로 얻을 수 있는 조합인가."""
    if not registered or not slot or registered == slot:
        return False
    if registered == "GK" or slot == "GK" or (registered, slot) in _EQUIV:
        return False
    r = compat_rank(registered, slot)
    if r is None or r < 1 or r > _MAX_COMPAT_RANK:
        return False
    want = FOOT_SIDED_SLOTS.get(slot)
    if want is not None and foot and foot != FOOT_BOTH and foot != want:
        return False
    return True


def decide_ai(registered, foot, records):
    """records: [29주(Y-1), 43주(Y-1), 29주(Y), 43주(Y)] 기록 포지션(없으면 None).
    4개가 모두 같은 비주포 슬롯이고 eligible이면 그 슬롯, 아니면 None. (순수 함수 — 단위 테스트용)"""
    if len(records) != 4 or any(not r for r in records):
        return None
    s = records[0]
    if any(r != s for r in records):
        return None
    return s if eligible(registered, s, foot) else None


def decide_my(registered, foot, records):
    """records: [Y-2 라벨, Y-1 라벨] 내 선수 기록(= Y-1·Y 시즌 시작 배치). 2개가 같은 비주포 슬롯이고
    eligible이면 그 슬롯."""
    if len(records) != 2 or any(not r for r in records) or records[0] != records[1]:
        return None
    return records[0] if eligible(registered, records[0], foot) else None


def acquire(c, year):
    """오프시즌 Y 획득 판정(run_ai_offseason — 은퇴·임대 복귀 직후, 이적시장 직전). 반환: 새로 얻은 수.
    스위치가 꺼져 있으면 아무것도 읽거나 쓰지 않는다. 난수 미사용."""
    if not enabled():
        return 0
    global _CACHE
    from database import history_drain
    history_drain()
    have = {int(r[0]): r[1] for r in c.execute(f"SELECT player_id, position FROM {TABLE}").fetchall()}
    # 4개 기록이 모두 같은 슬롯이고 등록 포지션과 다른(= 비주포 슬롯 선발) 현역 선수만 SQL로 먼저
    # 추린 뒤, 호환 순위·주발·GK·ST↔CF는 decide_ai(단위 테스트와 같은 함수)로 판정한다.
    # hist 두 표의 키는 (player_id, year)이고 year 인덱스가 없다 — 현역 선수(ai_players)를 앞에 두고
    # CROSS JOIN(SQLite가 조인 순서를 그대로 지킴)으로 키 조회만 하게 해서, 기록이 쌓여도 비용이
    # 늘지 않게 한다(year로 hist를 먼저 훑으면 해마다 전체 기록을 스캔한다).
    new_rows = []
    for pid, reg, foot, h0, f0, h1, f1 in c.execute(
            """SELECT ap.id, ap.position, COALESCE(ap.foot,''),
                      h0.position, f0.position, h1.position, f1.position
               FROM ai_players ap
               CROSS JOIN hist.ai_player_position_history f1
                    ON f1.player_id = ap.id AND f1.year = ? AND f1.position != ap.position
               CROSS JOIN hist.ai_player_position_history_half h1
                    ON h1.player_id = ap.id AND h1.year = ? AND h1.position = f1.position
               CROSS JOIN hist.ai_player_position_history f0
                    ON f0.player_id = ap.id AND f0.year = ? AND f0.position = f1.position
               CROSS JOIN hist.ai_player_position_history_half h0
                    ON h0.player_id = ap.id AND h0.year = ? AND h0.position = f1.position""",
            (year, year, year - 1, year - 1)).fetchall():
        if pid in have:
            continue
        s = decide_ai(reg, foot, [h0, f0, h1, f1])
        if s:
            new_rows.append((pid, s, year))
    new_rows.sort()
    try:
        me = c.execute("SELECT position, COALESCE(foot,'') FROM my_player WHERE id=1").fetchone()
        if me and MY_ID not in have:
            mr = {r[0]: r[1] for r in c.execute(
                "SELECT year, position FROM my_player_position_history WHERE year IN (?,?)",
                (year - 2, year - 1)).fetchall()}
            s = decide_my(me[0], me[1], [mr.get(year - 2), mr.get(year - 1)])
            if s:
                new_rows.append((MY_ID, s, year))
    except Exception:
        pass
    if new_rows:
        c.executemany(f"INSERT OR IGNORE INTO {TABLE}(player_id, position, gained_year) VALUES(?,?,?)",
                      new_rows)
    have.update({pid: s for pid, s, _y in new_rows})
    _CACHE = have
    return len(new_rows)


def best_slot(primary, sub, slots):
    """내 선수 배치 슬롯 — 호환 순위가 가장 좋은 슬롯(부포 슬롯은 순위 0). 동점이면 슬롯 순서(주포 우선 없음).
    sub가 없으면 예전 get_field_pos 루프와 같은 결과. 호환 슬롯이 하나도 없으면 primary."""
    compat = POSITION_COMPAT.get(primary, [primary])
    best, best_rank = primary, 999
    for slot in slots:
        if sub and slot == sub:
            rank = 0
        elif slot in compat:
            rank = compat.index(slot)
        else:
            continue
        if rank < best_rank:
            best_rank = rank
            best = slot
    return best