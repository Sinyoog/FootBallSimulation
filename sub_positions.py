# -*- coding: utf-8 -*-
"""sub_positions.py — 주포·부포(부포지션) 획득·저장·조회 공용 모듈.

[2026-10 신설, 신민용 확정 — 주포·부포 ③ 명세]
  · [2026-10 개정, 신민용 확정] 부포 = 주포 OVR −1(constants.SUB_POS_OVR_COST). OVR 97 ST의 부포가 LW이면
    LW에서는 OVR 96 LW와 같다. 예전 명세(감점 없이 100% 적합)는 폐기. 주발 적합도는 따로 그대로 적용한다.
  · 선발은 유효값만 본다 — 부포가 −1이라 같은 조건이면 주포 슬롯이 앞선다.
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
# [2026-11-25 SUB_POS_V2] {player_id: (부포1, 부포2...)} — 부포 순서(slot_order) 그대로. V2가 꺼져 있으면 쓰지 않는다.
_CACHE_ALL = None
TABLE_V2 = "ai_player_sub_positions_v2"
TABLE_V2_DONE = "ai_player_sub_innate_done"
_V2_META_KEY = "sub_pos_v2_migrated"


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
    global _CACHE, _CACHE_ALL
    _CACHE = None
    _CACHE_ALL = None


def _load():
    global _CACHE
    if _CACHE is None and v2():
        _load_v2()
        return _CACHE
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


# ── [2026-11-25 SUB_POS_V2] 부포 여러 개 조회 ─────────────────────────────
# V2가 꺼져 있으면 아래 함수들은 모두 예전 1개짜리와 같은 값을 돌려준다(get_all → (부포,), entry → 부포).
def v2():
    import constants
    return enabled() and bool(getattr(constants, "SUB_POS_V2", False))


def _load_v2():
    """새 표에서 읽어 _CACHE(첫 부포)·_CACHE_ALL(순서대로 전부)을 채운다. 표가 없으면 빈 값."""
    global _CACHE, _CACHE_ALL
    allm, first = {}, {}
    try:
        from database import get_conn
        conn = get_conn()
        try:
            for pid, pos in conn.execute(
                    f"SELECT player_id, position FROM {TABLE_V2} ORDER BY player_id, slot_order").fetchall():
                pid = int(pid)
                if pid in allm:
                    allm[pid] = allm[pid] + (pos,)
                else:
                    allm[pid] = (pos,)
                    first[pid] = pos
        finally:
            conn.close()
    except Exception:
        allm, first = {}, {}
    _CACHE, _CACHE_ALL = first, allm


def _load_all():
    """{선수: (부포...)} — V2면 순서대로 전부, 아니면 (예전 부포,)."""
    if not v2():
        return {p: (s,) for p, s in _load().items()}
    if _CACHE_ALL is None:
        _load_v2()
    return _CACHE_ALL


def _entry_of(subs):
    if not subs:
        return None
    return subs[0] if len(subs) == 1 else tuple(subs)


def _load_entries():
    """{선수: 장부·보충 목표용 부포 값} — 부포 1개면 문자열(예전과 같은 값), 2개면 튜플(V2만)."""
    if not v2():
        return _load()
    return {p: _entry_of(s) for p, s in _load_all().items()}


def get_all(player_id):
    """그 선수의 부포 튜플(부포 순서). 없으면 ()."""
    if not enabled():
        return ()
    if not v2():
        s = _load().get(player_id)
        return (s,) if s else ()
    return _load_all().get(player_id, ())


def entry(player_id):
    """장부·보충 목표용 값 — None / 부포(문자열) / (부포1, 부포2)(V2에서 2개일 때만)."""
    if not enabled():
        return None
    if not v2():
        return _load().get(player_id)
    return _entry_of(_load_all().get(player_id, ()))


def my_subs():
    return get_all(MY_ID)


def my_entry():
    return entry(MY_ID)


def sub_list(sub):
    """부포 값(None·문자열·튜플) → 튜플."""
    if not sub:
        return ()
    return sub if sub.__class__ is tuple else (sub,)


def is_sub_slot(sub, slot):
    """slot이 부포 값(문자열 또는 튜플) 안에 있는가."""
    if not sub:
        return False
    if sub.__class__ is tuple:
        return slot in sub
    return sub == slot


def my_sub():
    return get(MY_ID)


def attach(rows, id_key="id", first_only=False):
    """선수 dict 목록에 "sub_pos"를 붙인다(보유자만). 비활성이면 아무것도 안 한다.
    [2026-11-25 SUB_POS_V2] 부포가 2개면 "sub_pos2"도 붙인다 — first_only=True(대표팀)면 첫 부포만."""
    if not active():
        return rows
    cache = _CACHE
    if v2() and not first_only:
        allm = _load_all()
        for d in rows:
            if d is None:
                continue
            s = allm.get(d.get(id_key))
            if s:
                d["sub_pos"] = s[0]
                if len(s) > 1:
                    d["sub_pos2"] = s[1]
        return rows
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


def _tier_rule_on():
    import constants
    return bool(getattr(constants, "SUB_POS_TIER_ELIGIBILITY", False))


def _tier_of(registered, slot):
    """포지션 거리 단계(0~4) — constants.POSITION_TIER_PCT 안의 위치."""
    import formation_logic as _fl
    from constants import POSITION_TIER_PCT
    p = _fl._tier_penalty(registered, slot)
    return POSITION_TIER_PCT.index(p) if p in POSITION_TIER_PCT else 4


def eligible(registered, slot, foot):
    """획득 조건 2)~4) — 등록 포지션 registered인 선수가 slot을 부포로 얻을 수 있는 조합인가."""
    if not registered or not slot or registered == slot:
        return False
    if registered == "GK" or slot == "GK" or (registered, slot) in _EQUIV:
        return False
    if _tier_rule_on():
        # [2026-10 5번 ②, constants.SUB_POS_TIER_ELIGIBILITY] 호환표 1~2순위 대신 포지션 거리 단계
        # (formation_logic._tier_penalty — 선발 감점과 같은 표) T1~T2 자리. LW→ST처럼 호환표 3순위라
        # 막혀 있던 인접 자리도 얻을 수 있고, 예전 2순위였지만 거리가 먼(T3) 자리는 더는 못 얻는다.
        if _tier_of(registered, slot) not in (1, 2):
            return False
    else:
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
    if v2():
        return _acquire_v2(c, year)
    global _CACHE
    from database import history_drain, history_sync_for_read
    history_sync_for_read()  # [2026-11-01 비동기 기록 경합 수정] 커밋 + 대기(예전 history_drain)
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


def sub_cost(main=None, slot=None):
    """[2026-11-25 SUB_POS_V2] 부포 자리의 OVR 감점. A안(기본·V2 꺼짐) = SUB_POS_OVR_COST(예전과 같음).
    B안(SUB_POS_V2 + SUB_POS_COST_MODE "B") = 주포→부포 거리 단계별 SUB_POS_COST_B."""
    import constants as _C
    base = _C.SUB_POS_OVR_COST
    if main and slot and v2() and getattr(_C, "SUB_POS_COST_MODE", "A") == "B":
        t = _tier_of(main, slot)
        return float(getattr(_C, "SUB_POS_COST_B", {}).get(t, base))
    return base


def sub_mult_penalty(ovr, cap, main=None, slot=None):
    """[2026-10] 곱셈형 적합도에서 부포 슬롯의 미스매치 — OVR × (1 − 이 값) = OVR − SUB_POS_OVR_COST. cap(호환
    2순위 감점)보다 커지지 않게 한다(OVR이 아주 낮은 선수 방어). [2026-11-25] main·slot은 B안에서만 쓴다."""
    return min(cap, sub_cost(main, slot) / max(1.0, float(ovr or 0)))


def sub_abs_penalty(cap, main=None, slot=None):
    """[2026-10] 절대값형 점수(OVR − 감점)에서 부포 슬롯의 감점 — SUB_POS_OVR_COST, 호환 2순위 감점보다 작게."""
    return min(cap, sub_cost(main, slot))


def best_slot(primary, sub, slots):
    """내 선수 배치 슬롯 — 호환 순위가 가장 좋은 슬롯. [2026-10] 부포 슬롯은 SUB_POS_RANK(주포 0과 호환 2순위 1
    사이 — 부포 = OVR −1). sub가 없으면 예전 get_field_pos 루프와 같은 결과. 호환 슬롯이 하나도 없으면 primary."""
    from constants import SUB_POS_RANK
    compat = POSITION_COMPAT.get(primary, [primary])
    best, best_rank = primary, 999
    for slot in slots:
        if sub and is_sub_slot(sub, slot):      # [2026-11-25 SUB_POS_V2] sub가 튜플(부포 2개)이어도 된다
            rank = SUB_POS_RANK
        elif slot in compat:
            rank = compat.index(slot)
        else:
            continue
        if rank < best_rank:
            best_rank = rank
            best = slot
    return best


# ══ [2026-11-25 신설, 신민용 확정 — SUB_POS_V2: 부포 2개·생성 부포·교체 우선순위] ══════════════════
# 저장: TABLE_V2(player_id, slot_order 1·2, position, obtained_year, source, kind)
#   · slot_order는 실제 저장 순서 — 조회·표시·"첫 부포"가 항상 이 순서를 따른다. 교체는 같은 slot_order 자리에 덮어쓴다.
#   · source: 'gen'(생성) / 'play'(경기 출전 획득) / 'legacy'(예전 표에서 옮김)
#   · kind: 'cross'(장부상 다른 칸 — 선수단 인원으로 센다) / 'same'(주포와 같은 칸 — 경기 전술 활용만)
#   TABLE_V2_DONE(player_id): 생성 판정을 끝낸 선수(부포가 0개로 나왔어도 기록 — 교체된 생성 부포를 다시 뽑지 않게).
#   예전 표(TABLE)는 읽기만 하고 고치지 않는다 — V2를 끄면 예전 상태 그대로 돌아간다.

def _cell(pos):
    import squad_targets as _ST
    return _ST.SLOT_TO_TARGET.get(pos)


def kind_of(main, sub):
    """부포 1개 기준 종류 — 'same'(장부 칸이 주포와 같음 — LW→LM, LB→LWB 등) / 'cross'(다른 칸)."""
    import squad_targets as _ST
    a = _ST.REGISTERED_TO_TARGET.get(main)
    return "same" if (a and _cell(sub) == a) else "cross"


# 장부 칸 → 그 칸의 대표 포지션 이름(같은 칸 부포가 둘일 때 '다른 칸'을 어느 쪽에 줄지 — 대표 이름 우선)
_CANON = {"CB": "CB", "LB": "LB", "RB": "RB", "CDM": "CDM", "CM": "CM", "CAM": "CAM", "LW": "LW", "RW": "RW",
          "FW": "ST"}


def kinds_for(main, subs):
    """부포 목록(부포 순서)의 종류 — 주포 칸이나 이미 다른 부포가 맡은 칸과 같으면 'same'(장부 인원 추가 없음).
    예: CAM → ST·CF면 CF가 'same', LW → RW·LM이면 LM이 'same'. 같은 칸 둘 중에는 대표 이름(RW, LB, ST...)이
    'cross'를 갖고, 둘 다 대표 이름이 아니면 앞 순서가 갖는다. 부포 1개면 kind_of와 같다."""
    import squad_targets as _ST
    taken = {_ST.REGISTERED_TO_TARGET.get(main)}
    order = sorted(range(len(subs)), key=lambda i: (0 if _CANON.get(_cell(subs[i])) == subs[i] else 1, i))
    out = [None] * len(subs)
    for i in order:
        c = _cell(subs[i])
        out[i] = "same" if (c is None or c in taken) else "cross"
        taken.add(c)
    return out


def ensure_v2(c):
    c.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE_V2}(
        player_id INTEGER NOT NULL,
        slot_order INTEGER NOT NULL,
        position TEXT NOT NULL,
        obtained_year INTEGER NOT NULL,
        source TEXT NOT NULL,
        kind TEXT NOT NULL,
        PRIMARY KEY(player_id, slot_order),
        UNIQUE(player_id, position))""")
    c.execute(f"CREATE TABLE IF NOT EXISTS {TABLE_V2_DONE}(player_id INTEGER PRIMARY KEY)")


def _meta_get(c, key):
    try:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None
    except Exception:
        return None


def _salt(c):
    """생성 부포 전용 난수에 섞는 판별 값 — 주발과 같은 meta.foot_salt(없으면 0). 읽기만 한다."""
    v = _meta_get(c, "foot_salt")
    try:
        return int(v) & 0xFFFFFFFF if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _rng_for(pid, name, pos, foot, salt):
    """선수 하나 전용 난수(전역 random을 건드리지 않는다). crc32라 PYTHONHASHSEED와 무관."""
    import random as _r
    import zlib
    key = f"SUBV2|{salt}|{pid}|{name or ''}|{pos or ''}|{foot or ''}".encode("utf-8")
    return _r.Random(zlib.crc32(key))


def _wpick(rng, weights, reg, foot, excl):
    cand = [(s, w) for s, w in weights.items() if s not in excl and eligible(reg, s, foot)]
    if not cand:
        return None
    tot = sum(w for _s, w in cand)
    x = rng.random() * tot
    for s, w in cand:
        x -= w
        if x <= 0:
            return s
    return cand[-1][0]


def _same_cands(reg, s1, foot):
    import constants as _C
    cells = {_cell(reg), _cell(s1)}
    out = []
    for sl in _C.SUB_INNATE_SAME_ORDER:
        if sl in (reg, s1) or _cell(sl) not in cells:
            continue
        if eligible(reg, sl, foot):
            out.append(sl)
    return out


def innate_roll(pid, name, reg, foot, salt=0):
    """생성 부포 판정(순수 함수) → 부포 튜플(0~2개). 주포 기준 T1~T2·발 규칙, GK 없음. 첫 부포는 다른 칸만,
    두 번째는 첫 부포에 따른 표(다른 칸) 또는 같은 칸(SUB_INNATE_SAME_P, 다른 칸이 막히면 같은 칸으로 대신)."""
    import constants as _C
    rate = _C.SUB_INNATE_RATE.get(reg)
    if not rate or reg == "GK":
        return ()
    rng = _rng_for(pid, name, reg, foot, salt)
    p_any, p_two = rate
    x = rng.random()
    n = 2 if x < p_two else (1 if x < p_any else 0)
    if not n:
        return ()
    s1 = _wpick(rng, _C.SUB_INNATE_FIRST.get(reg, {}), reg, foot, ())
    if not s1:
        return ()
    if n == 1:
        return (s1,)
    sc = _same_cands(reg, s1, foot)
    if sc and rng.random() < _C.SUB_INNATE_SAME_P:
        s2 = sc[int(rng.random() * len(sc))]
    else:
        s2 = _wpick(rng, _C.SUB_INNATE_SECOND.get(reg, {}).get(s1, {}), reg, foot, (s1,))
        if s2 is None and sc:
            s2 = sc[int(rng.random() * len(sc))]
    return (s1, s2) if s2 else (s1,)


def _rows_of(c, pid):
    """[(slot_order, position, obtained_year, source, kind)] — slot_order 순."""
    return [tuple(r) for r in c.execute(
        f"SELECT slot_order, position, obtained_year, source, kind FROM {TABLE_V2} WHERE player_id=? "
        "ORDER BY slot_order", (pid,)).fetchall()]


def plan_acquire(main, cur, new):
    """출전 획득 판정(순수 함수). cur: [(slot_order, position, obtained_year, source, kind)].
    반환: None(변화 없음) / ('add', slot_order) / ('replace', slot_order).
      · 이미 가진 포지션이거나 주포면 변화 없음.
      · 빈 자리가 있으면 가장 작은 빈 slot_order에 추가.
      · 꽉 찼을 때 새 부포가 같은 칸이면 같은 칸 부포 중 가장 오래된 것만 교체(없으면 변화 없음 — 다른 칸 부포를 밀어내지 않는다).
      · 새 부포가 다른 칸이면 같은 칸 부포 중 가장 오래된 것, 없으면 다른 칸 부포 중 가장 오래된 것을 교체.
      · '가장 오래된' = (obtained_year, slot_order)가 가장 작은 것."""
    import constants as _C
    mx = int(getattr(_C, "SUB_POS_V2_MAX", 2))
    if not new or new == main or any(r[1] == new for r in cur):
        return None
    used = {r[0] for r in cur}
    if len(cur) < mx:
        for k in range(1, mx + 1):
            if k not in used:
                return ("add", k)
    # 새 부포의 종류: 주포 칸이나 지금 가진 부포의 칸과 겹치면 같은 칸(장부 인원이 늘지 않는다)
    import squad_targets as _ST
    kn = "same" if (_cell(new) in ({_ST.REGISTERED_TO_TARGET.get(main)} | {_cell(r[1]) for r in cur})) else "cross"
    same = sorted((r for r in cur if r[4] == "same"), key=lambda r: (r[2], r[0]))
    if kn == "same":
        return ("replace", same[0][0]) if same else None
    if same:
        return ("replace", same[0][0])
    cross = sorted((r for r in cur if r[4] == "cross"), key=lambda r: (r[2], r[0]))
    return ("replace", cross[0][0]) if cross else None


def merge_innate(main, cur, subs):
    """생성 부포를 빈 자리에만 넣는다(기존 부포는 절대 지우지 않는다). 반환 [(slot_order, position)]."""
    import constants as _C
    mx = int(getattr(_C, "SUB_POS_V2_MAX", 2))
    have = {r[1] for r in cur}
    used = {r[0] for r in cur}
    out = []
    for s in subs:
        if s in have or s == main:
            continue
        free = [k for k in range(1, mx + 1) if k not in used]
        if not free:
            break
        out.append((free[0], s))
        used.add(free[0])
        have.add(s)
    return out


def migrate_v2(c, year):
    """처음 V2를 켤 때 한 번: 예전 표의 부포를 부포 1(출처 legacy, 종류 판정)로 옮긴다. 주포는 바꾸지 않는다.
    완료는 meta에 표시 — 두 번 불러도 같은 결과. 반환: 옮긴 수(이미 했으면 0)."""
    if not v2():
        return 0
    ensure_v2(c)
    if _meta_get(c, _V2_META_KEY):
        return 0
    n = 0
    try:
        legacy = c.execute(f"SELECT player_id, position, gained_year FROM {TABLE} ORDER BY player_id").fetchall()
    except Exception:
        legacy = []
    if legacy:
        mains = {}
        try:   # 은퇴 선수의 예전 부포도 옮긴다 — 종류(같은 칸/다른 칸) 판정에 은퇴 당시 주포를 쓴다
            mains.update({int(r[0]): r[1] for r in c.execute("SELECT id, position FROM ai_players_retired").fetchall()})
        except Exception:
            pass
        mains.update({int(r[0]): r[1] for r in c.execute("SELECT id, position FROM ai_players").fetchall()})
        try:
            r = c.execute("SELECT position FROM my_player WHERE id=1").fetchone()
            if r:
                mains[MY_ID] = r[0]
        except Exception:
            pass
        rows = []
        for pid, pos, gy in legacy:
            pid = int(pid)
            main = mains.get(pid)
            rows.append((pid, 1, pos, int(gy or year), "legacy", kinds_for(main, [pos])[0] if main else "cross"))
        c.executemany(f"INSERT OR IGNORE INTO {TABLE_V2}(player_id, slot_order, position, obtained_year, source, kind) "
                      "VALUES(?,?,?,?,?,?)", rows)
        n = len(rows)
    c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (_V2_META_KEY, str(int(year))))
    clear()
    return n


def grant_innate(c, year):
    """생성 판정이 안 된 선수(AI 전원 + 내 선수) 모두에게 생성 부포를 판정해 빈 자리에 넣는다. 같은 선수는
    한 번만(TABLE_V2_DONE). 처음이면 migrate_v2를 먼저 한다. 반환: 판정한 선수 수. V2가 꺼져 있으면 0."""
    if not v2():
        return 0
    migrate_v2(c, year)
    salt = _salt(c)
    todo = c.execute(
        f"SELECT ap.id, ap.name, ap.position, COALESCE(ap.foot,'') FROM ai_players ap "
        f"LEFT JOIN {TABLE_V2_DONE} d ON d.player_id = ap.id WHERE d.player_id IS NULL ORDER BY ap.id").fetchall()
    todo = [tuple(r) for r in todo]
    try:
        if not c.execute(f"SELECT 1 FROM {TABLE_V2_DONE} WHERE player_id=?", (MY_ID,)).fetchone():
            me = c.execute("SELECT name, position, COALESCE(foot,'') FROM my_player WHERE id=1").fetchone()
            if me:
                todo.append((MY_ID, me[0], me[1], me[2]))
    except Exception:
        pass
    if not todo:
        return 0
    cur_all = {}
    for pid, so, pos, oy, src, kd in c.execute(
            f"SELECT player_id, slot_order, position, obtained_year, source, kind FROM {TABLE_V2}").fetchall():
        cur_all.setdefault(int(pid), []).append((so, pos, oy, src, kd))
    ins, done, upd = [], [], []
    for pid, name, pos, foot in todo:
        pid = int(pid)
        done.append((pid,))
        subs = innate_roll(pid, name, pos, foot, salt)
        if not subs:
            continue
        cur = sorted(cur_all.get(pid, []))
        add = merge_innate(pos, cur, subs)
        if not add:
            continue
        full = sorted([(r[0], r[1]) for r in cur] + list(add))
        kd = dict(zip([so for so, _s in full], kinds_for(pos, [s for _so, s in full])))
        for so, s in add:
            ins.append((pid, so, s, int(year), "gen", kd[so]))
        for r in cur:
            if r[4] != kd[r[0]]:
                upd.append((kd[r[0]], pid, r[0]))
    if ins:
        c.executemany(f"INSERT INTO {TABLE_V2}(player_id, slot_order, position, obtained_year, source, kind) "
                      "VALUES(?,?,?,?,?,?)", ins)
    if upd:
        c.executemany(f"UPDATE {TABLE_V2} SET kind=? WHERE player_id=? AND slot_order=?", upd)
    c.executemany(f"INSERT OR IGNORE INTO {TABLE_V2_DONE}(player_id) VALUES(?)", done)
    clear()
    return len(todo)


def grant_innate_safe(c, year, tag=""):
    """호출부용 — V2가 꺼져 있으면 아무것도 안 하고, 실패해도 게임 진행을 막지 않는다."""
    if not v2():
        return 0
    try:
        return grant_innate(c, year)
    except Exception as e:
        print(f"[SUB-POS V2] 생성 부포 판정 실패({tag}): {e}")
        return 0


def pending_innate(c):
    """생성 판정이 아직 안 된 AI 선수 수(검증용)."""
    return c.execute(f"SELECT COUNT(*) FROM ai_players ap LEFT JOIN {TABLE_V2_DONE} d ON d.player_id = ap.id "
                     "WHERE d.player_id IS NULL").fetchone()[0]


def _apply_acquire(c, pid, main, new, year):
    """plan_acquire를 표에 적용. 반환: 바뀌었으면 True."""
    cur = _rows_of(c, pid)
    act = plan_acquire(main, cur, new)
    if act is None:
        return False
    op, so = act
    if op == "replace":
        c.execute(f"DELETE FROM {TABLE_V2} WHERE player_id=? AND slot_order=?", (pid, so))
    c.execute(f"INSERT INTO {TABLE_V2}(player_id, slot_order, position, obtained_year, source, kind) "
              "VALUES(?,?,?,?,?,?)", (pid, so, new, int(year), "play", kind_of(main, new)))
    _sync_kinds(c, pid, main)
    return True


def _sync_kinds(c, pid, main):
    """그 선수 부포 전체의 종류를 kinds_for로 다시 맞춘다(교체·추가 뒤 — 겹치는 칸이 바뀔 수 있다)."""
    rows = _rows_of(c, pid)
    kd = kinds_for(main, [r[1] for r in rows])
    for r, k in zip(rows, kd):
        if r[4] != k:
            c.execute(f"UPDATE {TABLE_V2} SET kind=? WHERE player_id=? AND slot_order=?", (k, pid, r[0]))


def _acquire_v2(c, year):
    """acquire의 V2판 — 판정 조건(2시즌 4기록, decide_ai/decide_my)은 같고, 넣는 규칙만 plan_acquire."""
    from database import history_sync_for_read
    history_sync_for_read()
    migrate_v2(c, year)
    cand = []
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
        s = decide_ai(reg, foot, [h0, f0, h1, f1])
        if s:
            cand.append((int(pid), reg, s))
    cand.sort()
    try:
        me = c.execute("SELECT position, COALESCE(foot,'') FROM my_player WHERE id=1").fetchone()
        if me:
            mr = {r[0]: r[1] for r in c.execute(
                "SELECT year, position FROM my_player_position_history WHERE year IN (?,?)",
                (year - 2, year - 1)).fetchall()}
            s = decide_my(me[0], me[1], [mr.get(year - 2), mr.get(year - 1)])
            if s:
                cand.append((MY_ID, me[0], s))
    except Exception:
        pass
    n = 0
    for pid, reg, s in cand:
        if _apply_acquire(c, pid, reg, s, year):
            n += 1
    clear()
    return n


def cur_year(c):
    """현재 시즌 연도(season_state) — 없으면 게임 시작 연도, 그것도 없으면 0."""
    try:
        r = c.execute("SELECT current_year FROM season_state").fetchone()
        if r and r[0]:
            return int(r[0])
    except Exception:
        pass
    try:
        from database import get_game_start_year
        return int(get_game_start_year())
    except Exception:
        return 0


def grant_innate_now(c, tag=""):
    """[2026-11-25 SUB_POS_V2] 호출부용 한 줄 — V2가 꺼져 있으면 아무것도 읽거나 쓰지 않는다."""
    if not v2():
        return 0
    return grant_innate_safe(c, cur_year(c), tag)


def reset_v2(c):
    """새 게임: V2 표·완료 표·변환 표시를 비운다(표가 없으면 무동작)."""
    for t in (TABLE_V2, TABLE_V2_DONE):
        try:
            c.execute(f"DELETE FROM {t}")
        except Exception:
            pass
    try:
        c.execute("DELETE FROM meta WHERE key=?", (_V2_META_KEY,))
    except Exception:
        pass
    clear()


def dict_entry(p):
    """[2026-11-25 SUB_POS_V2] 선수 dict의 부포 값 — "sub_pos2"가 있으면 (부포1, 부포2), 아니면 "sub_pos"(예전 값)."""
    s2 = p.get("sub_pos2")
    if s2:
        return (p.get("sub_pos"), s2)
    return p.get("sub_pos")


def set_my_sub2(d):
    """내 선수 dict에 두 번째 부포를 붙인다(V2에서 2개일 때만). "sub_pos"는 호출부가 이미 붙였다."""
    if not v2():
        return d
    s = my_subs()
    if len(s) > 1:
        d["sub_pos2"] = s[1]
    return d


# ── [2026-11-25 SUB_POS_V2] 기록실·화면용 ─────────────────────────────────
SAME_LABEL = "전술 활용"
TIP_CROSS = "다른 칸 부포 — 선수단 장부에서 그 포지션 인원으로 인정한다. 경기에서는 주포 OVR −1로 뛴다."
TIP_SAME = ("같은 칸 부포(전술 활용) — 주포와 같은 칸이라 선수단 장부 인원으로는 추가로 세지 않는다. "
            "경기에서 그 자리에 설 때 주포 OVR −1로 뛴다.")


def search_table():
    """포지션 검색 SQL이 볼 부포 표 이름(둘 다 player_id·position 열이 있다)."""
    return TABLE_V2 if v2() else TABLE


def hist_subs(conn, ids, year):
    """그 해에 이미 갖고 있던 부포 {선수: (부포...)} — 예전 표는 gained_year, V2 표는 obtained_year < 그 해."""
    out = {}
    if v2():
        sql = (f"SELECT player_id, position FROM {TABLE_V2} WHERE obtained_year < ? AND player_id IN ({{ph}}) "
               "ORDER BY player_id, slot_order")
    else:
        sql = f"SELECT player_id, position FROM {TABLE} WHERE gained_year < ? AND player_id IN ({{ph}})"
    for k in range(0, len(ids), 500):
        part = ids[k:k + 500]
        for r in conn.execute(sql.format(ph=",".join("?" * len(part))), [int(year)] + part).fetchall():
            p = int(r[0])
            out[p] = out.get(p, ()) + (r[1],)
    return out


def sub_for_cell(sub, cell):
    """부포 값(문자열·튜플) 중 장부 칸 cell에 해당하는 부포(없으면 None)."""
    for s in sub_list(sub):
        if _cell(s) == cell:
            return s
    return None


def display_subs(player_id, main=None):
    """화면 표시용 [(부포, 종류)] — 부포 순서대로. 종류는 V2에서만('same'/'cross'), 아니면 None."""
    if not enabled():
        return []
    if not v2():
        s = get(player_id)
        return [(s, None)] if s else []
    subs = get_all(player_id)
    return list(zip(subs, kinds_for(main, list(subs)))) if (main and subs) else [(s, None) for s in subs]


def sub_label(pos, kind):
    """"LM" 또는 "LM (전술 활용)"(같은 칸 부포)."""
    if not pos:
        return "-"
    return f"{pos} ({SAME_LABEL})" if kind == "same" else pos