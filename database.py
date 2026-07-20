"""
database.py - 전체 SQLite 기반. JSON 없음.
"""
import sqlite3, os, sys, random, time, threading
from data.countries import COUNTRY_DATA
from data.leagues import LEAGUE_DATA
from data.names import NAME_DATA
# [버그수정 2026-07] OVR_RANGES가 database.py와 constants.py에 각각 따로
# 정의돼 있었고 값도 서로 어긋나 있었다(예: S등급 tier1이 database=88~95,
# constants=85~96로 서로 다름). 게다가 둘 다 SS/S의 5부·6부가 빠져 있어서,
# 새로 추가한 부수의 선수 OVR이 엉뚱하게(예: 6부인데 1부와 비슷한 수치로)
# 생성되는 버그로 이어졌다. constants.py를 유일한 원본으로 삼아 여기서는
# 그대로 가져다 쓴다 — 더 이상 두 곳을 따로 수정할 필요가 없다.
from constants import OVR_RANGES

# [PyInstaller 대응] __file__ 기준 경로는 패키징 후 문제가 된다:
#   - onefile: __file__이 실행마다 새로 생기는 임시폴더(sys._MEIPASS)를 가리켜서,
#     거기 저장한 game.db가 앱 종료 시 임시폴더와 함께 삭제됨 → "저장 안 됨".
#   - onedir: __file__이 설치 폴더(Program Files 등)를 가리켜서 쓰기 권한이 없을 수 있음.
# sys.frozen이면 실행 파일(exe) 옆 폴더를 쓴다 — onefile/onedir 모두 exe 위치는
# 영구적이고 보통 쓰기 가능한 위치(사용자가 압축 푼 폴더 등)이기 때문.
if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(_APP_DIR, "game.db")

# ── [최적화] 인메모리 라이브 DB + 디스크 백업 ──────────────────────
# 실측 결과, 게임 진행 중(주간 tick·시즌종료 등)의 SQLite 비용 대부분이
# "매 commit마다 디스크에 fsync"하는 데서 나왔다(디스크 대비 인메모리가
# 주간 tick 2.5~3배, 팀 수를 늘린 시나리오에서는 절감폭이 더 커짐).
# 그래서 실행 중엔 인메모리 DB(SQLite 공유캐시 :memory:)를 실제 라이브 DB로
# 쓰고, DB_PATH(game.db)는 "세이브 파일"로만 쓴다.
#   - 시작 시: game.db가 있으면 그 내용을 인메모리로 복사(load_from_disk)
#   - 진행 중: 4주(한 달)마다 자동저장으로 인메모리 → game.db 백업(flush_to_disk)
#   - 종료 시: main_window closeEvent에서 마지막으로 한 번 더 flush_to_disk
# 문제가 생기면 아래 플래그 하나만 False로 내리면 기존 "디스크 파일 직결" 방식으로
# 즉시 되돌아간다(그 외 코드/쿼리는 전부 그대로 재사용됨).
USE_MEMORY_DB = True
_MEM_URI = "file:footballsim_live_db?mode=memory&cache=shared"
# 공유캐시 인메모리 DB는 "열려있는 커넥션이 0개가 되는 순간" 통째로 사라진다.
# 그래서 앱 생명주기 내내 살아있는 앵커 커넥션을 하나 별도로 붙잡아둔다
# (풀 커넥션이 reset_conn_pool() 등으로 닫혔다 다시 열려도 데이터가 안 날아가게).
_mem_anchor = None

def _ensure_mem_anchor():
    global _mem_anchor
    if USE_MEMORY_DB and _mem_anchor is None:
        _mem_anchor = sqlite3.connect(_MEM_URI, uri=True, timeout=30)

# ── 커넥션 풀(단일 영속 커넥션 재사용) ────────────────────────────
# 이 게임은 단일 스레드(UI 메인 스레드)에서만 DB를 쓰고, 커넥션을 함수 밖으로
# 넘기지 않는다(모두 함수 내부에서 열고 닫음). 따라서 매 get_conn()마다
# sqlite3.connect + close 하던 것을, 커넥션 하나를 만들어 계속 재사용한다.
#   - 프로파일 결과 connect/close/commit 오버헤드가 전체 실행시간의 ~90%였다.
#   - 반환 커넥션의 close()는 no-op으로 감싼다 → 기존 코드의 conn.close()
#     호출 73곳을 한 줄도 안 고치고 그대로 두면서, 실제로는 닫지 않게 한다.
#   - commit/execute/cursor 등은 실제 커넥션에 그대로 위임된다.
_pool_conn = None

# [2026-07 버그 수정, 3차] "not an error" / "cannot commit - no transaction is
# active" 크래시가 계속 재발했다. 1차 수정(flush_to_disk 커밋 흡수), 2차
# 수정(flush_to_disk를 별도 스냅샷 커넥션으로 분리)까지 했는데도 계속
# 나는 걸 보면, 원인이 backup() 하나가 아니라 더 근본적이다 — 이 게임은
# 무거운 처리(시즌 전환 등)를 QThread 워커에서 돌리고, UI 쪽에서
# "워커가 도는 동안 메인 스레드는 DB를 안 건드린다"는 규칙을 지키려고
# 팝업 타이머 몇 개를 수동으로 멈추는 식으로 방어해왔다 — 근데 그 목록에
# 없는 타이머/콜백이 하나라도 있으면(혹은 앞으로 새로 추가되면) 그 순간
# 풀 커넥션에 진짜 동시 접근이 생기고, 파이썬 sqlite3 모듈의 암묵적
# 트랜잭션 추적이 두 스레드 사이에서 꼬여버린다.
# 게다가 기존 래퍼는 close()만 감쌌지 cursor()는 진짜 커넥션의 원본
# Cursor를 그대로 반환했다 — 이 코드베이스 전역에서 흔히 쓰는
# "c = conn.cursor(); c.execute(...)" 패턴은 그 원본 커서로 바로
# 들어가서, 커넥션 래퍼에 방어 로직을 아무리 추가해도 다 우회됐다.
# 그래서 이번엔: (1) 커서도 래핑해서 우회를 막고, (2) execute/executemany/
# commit 전부 "일시적 스레드 경합"으로 보이는 특정 오류 시그니처만 아주
# 짧게 쉬었다 재시도하게 했다(진짜 다른 오류는 그대로 위로 올림 — 조용히
# 삼키지 않음). 근본적으로 스레드 경합 자체를 원천 차단하는 게 아니라
# '재발했을 때 자동으로 회복'하는 방어망이라, 위 UI 쪽 타이머 정지 로직은
# 그대로 유지하는 게 맞다(이건 마지막 안전망).
_TRANSIENT_SQLITE_ERRORS = ("not an error", "no transaction is active",
                            "cannot start a transaction within a transaction")

# [2026-07 버그 수정, 4차 — 근본 원인 차단] 지금까지의 3차례 수정은 전부
# "재발했을 때 감지해서 재시도/흡수"하는 사후 대응이었다(위 3차 수정 설명
# 참고). 그런데 사후 대응만으로는 UI 쪽에서 타이머를 하나라도 빠뜨리면
# 다시 재발할 수 있는 구조였다 — 실제로 world_browser_window(세계기록실)의
# 검색 디바운스 타이머가 이 방어 목록(center_panel._toggle_popup_timers)에서
# 빠져 있었고, 워커 스레드가 advance_days()로 DB를 쓰는 동안 그 창이 열려
# 있으면 검색창 타이핑 250ms 뒤 디바운스가 같은 풀 커넥션으로 SELECT를 던져
# 정확히 이 크래시 시그니처("not an error" 등)를 재현할 수 있었다(별도로
# 수정함). 근본 원인은 "풀 커넥션 하나(_pool_conn)를 두 스레드가 정말로
# 동시에 건드릴 수 있다"는 사실 자체다 — check_same_thread=False는 파이썬이
# 그 접근을 막지 않는다는 뜻일 뿐, 여러 스레드의 동시 호출을 자동으로
# 직렬화해주는 게 아니다. 그래서 이 락(RLock) 하나로 풀 커넥션에 대한 모든
# 진입점(execute/executemany/executescript/commit/cursor + fetch류)을 실제로
# 상호배제한다 — "UI 쪽에서 실수로 안 막았다"는 전제에 기대지 않고, DB 계층
# 자체가 스스로를 보호하게 한다. 두 스레드가 겹쳐도 이제 한쪽이 아주 짧게
# 대기할 뿐 데이터는 항상 정확하다(경합 자체가 사라지므로, 위 재시도 로직은
# 진짜 예외적인 상황에서만 쓰이는 마지막 안전망으로 남는다 — 그대로 유지).
_pool_lock = threading.RLock()

def _retry_sqlite_op(fn, *args, **kwargs):
    last_err = None
    for attempt in range(4):
        try:
            with _pool_lock:
                return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            msg = str(e)
            if any(sig in msg for sig in _TRANSIENT_SQLITE_ERRORS):
                last_err = e
                time.sleep(0.03 * (attempt + 1))
                continue
            raise
    raise last_err


class _PooledCursor:
    """sqlite3.Cursor 래퍼 — execute류에 재시도 방어를 건다.
    conn.cursor()가 이 래퍼를 반환해야 위 방어가 실제로 적용된다
    (원본 커서를 그대로 돌려주면 다 우회됨)."""
    __slots__ = ("_real",)
    def __init__(self, real):
        object.__setattr__(self, "_real", real)
    def execute(self, *a, **kw):
        _retry_sqlite_op(object.__getattribute__(self, "_real").execute, *a, **kw)
        return self
    def executemany(self, *a, **kw):
        _retry_sqlite_op(object.__getattribute__(self, "_real").executemany, *a, **kw)
        return self
    def executescript(self, *a, **kw):
        _retry_sqlite_op(object.__getattribute__(self, "_real").executescript, *a, **kw)
        return self
    # [2026-07 4차 수정] execute()는 락을 걸어도, 그 뒤에 이어지는
    # fetchone/fetchmany/fetchall(SELECT 결과를 실제로 SQLite에서 끌어오는
    # 단계)이 락 밖에서 돌면 "execute 끝~fetch 시작" 사이의 틈으로 다른
    # 스레드가 끼어들 수 있다. fetch류도 같은 락으로 감싸 그 틈을 없앤다.
    def fetchone(self, *a, **kw):
        with _pool_lock:
            return object.__getattribute__(self, "_real").fetchone(*a, **kw)
    def fetchmany(self, *a, **kw):
        with _pool_lock:
            return object.__getattribute__(self, "_real").fetchmany(*a, **kw)
    def fetchall(self, *a, **kw):
        with _pool_lock:
            return object.__getattribute__(self, "_real").fetchall(*a, **kw)
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)
    def __iter__(self):
        # 반복도 fetch와 같은 이유로 락 안에서 완전히 리스트로 뽑아둔 뒤 반환
        # (지연 반복으로 락 밖에서 한 행씩 끌어오면 그 사이 다른 스레드가
        #  같은 커넥션에 끼어들 여지가 생긴다).
        with _pool_lock:
            return iter(list(object.__getattribute__(self, "_real")))
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _PooledConn:
    """sqlite3.Connection 래퍼. close()는 무력화(재사용), execute/executemany/
    commit/cursor는 재시도 방어를 씌워서 위임."""
    __slots__ = ("_real",)
    def __init__(self, real):
        object.__setattr__(self, "_real", real)
    def close(self):
        # 풀 커넥션은 닫지 않는다(재사용). 트랜잭션 정리는 commit이 담당.
        pass
    def cursor(self, *a, **kw):
        real = object.__getattribute__(self, "_real")
        real_c = _retry_sqlite_op(real.cursor, *a, **kw)
        return _PooledCursor(real_c)
    def execute(self, *a, **kw):
        real = object.__getattribute__(self, "_real")
        real_c = _retry_sqlite_op(real.execute, *a, **kw)
        return _PooledCursor(real_c)
    def executemany(self, *a, **kw):
        real = object.__getattribute__(self, "_real")
        real_c = _retry_sqlite_op(real.executemany, *a, **kw)
        return _PooledCursor(real_c)
    def commit(self):
        real = object.__getattribute__(self, "_real")
        try:
            _retry_sqlite_op(real.commit)
        except sqlite3.OperationalError as e:
            if "no transaction is active" in str(e):
                return  # 이미 커밋된 것과 같은 상태 — 조용히 통과(데이터 손실 아님)
            raise
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)
    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_real"), name, value)
    # with 문 호환(혹시 쓰는 곳 대비): 진입/이탈 시 닫지 않음
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False

def _new_raw_conn():
    if USE_MEMORY_DB:
        _ensure_mem_anchor()
        # [백그라운드 처리 대비] check_same_thread=False: 시즌 전환처럼 무거운
        # 처리를 UI 메인 스레드가 아닌 QThread 워커에서 돌리기 위해 필요.
        # SQLite 자체는 (기본 빌드 기준) 스레드 간 커넥션 공유가 안전하지만,
        # 파이썬 sqlite3 모듈이 기본적으로 이를 막아둔 것뿐이라 이 플래그로 해제한다.
        # [전제] 이 앱은 '한 번에 한 스레드만 쓴다'(워커가 도는 동안 메인 스레드는
        # 진행 버튼이 비활성화돼 DB에 접근하지 않음) — 진짜 동시 쓰기는 없음을
        # UI 쪽에서 보장해야 한다. 여러 스레드가 동시에 write 하면 안전하지 않다.
        conn = sqlite3.connect(_MEM_URI, uri=True, timeout=30, check_same_thread=False)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # synchronous=NORMAL 은 연결별 설정. WAL(영구 설정)과 함께 매 commit fsync를
    # 생략해 commit 비용을 크게 줄인다. WAL+NORMAL 은 SQLite 공식 권장 조합.
    # (인메모리 DB는 애초에 디스크 fsync 자체가 없어 이 설정이 사실상 no-op이지만
    #  디스크 모드로 되돌렸을 때도 그대로 맞게 유지해둔다.)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    # [스케일 대비] 팀/경기 수가 늘어나도(20팀+ 리그, 일 단위 일정 등) 페이지 캐시를
    # 넉넉히 잡아 디스크 I/O를 줄인다. 파일 포맷과 무관한 연결별 설정이라 안전하게
    # 언제든 조절 가능. mmap은 읽기 위주 쿼리(리그 브라우저, 역대 기록 등)에 유리.
    # [2026-07 재조정, 신민용 리포트: "연도전환이 갈수록 느려진다"] match_results_archive는
    # 삭제 없이 매년 계속 쌓이기만 하는 테이블이라(수십 시즌 누적 시 DB 파일 자체가
    # 수백MB~GB 단위로 커짐), 예전 16MB 캐시/128MB mmap으로는 갈수록 캐시에 안 담기는
    # 비중이 늘어나 디스크 I/O가 계속 증가했다(연도전환 로그의 "아카이브이동"/
    # "완비판정조회" 단계가 해마다 조금씩 느려지던 원인 중 하나). 캐시/mmap을
    # 4배로 늘려 더 오래 캐시에 남아있게 한다.
    conn.execute("PRAGMA cache_size=-65536")   # 약 64MB 페이지 캐시
    conn.execute("PRAGMA temp_store=MEMORY")   # 정렬/임시 테이블을 메모리에서 처리
    conn.execute("PRAGMA mmap_size=536870912") # 512MB mmap I/O
    return conn

def get_conn():
    global _pool_conn
    if _pool_conn is None:
        _pool_conn = _PooledConn(_new_raw_conn())
    return _pool_conn

def reset_conn_pool():
    """DB 파일이 교체되는 경우(세이브 로드/삭제 등) 풀 커넥션을 폐기.
    [주의] 인메모리 모드에선 이걸 호출해도 _mem_anchor가 살아있는 한
    데이터는 사라지지 않는다(풀 커넥션만 새로 열릴 뿐, 공유캐시라 같은
    인메모리 DB를 다시 가리킨다)."""
    global _pool_conn
    if _pool_conn is not None:
        try:
            object.__getattribute__(_pool_conn, "_real").close()
        except Exception:
            pass
        _pool_conn = None

def load_from_disk() -> bool:
    """게임 시작 시 1회: DB_PATH(game.db)에 기존 세이브가 있으면 그 내용을
    라이브 인메모리 DB로 통째로 복사한다(SQLite backup API 사용).
    세이브 파일이 없으면(첫 실행) 아무 것도 안 하고 False를 반환 —
    이 경우 init_db()가 빈 인메모리 DB에 새 스키마를 만든다.
    디스크 직결 모드(USE_MEMORY_DB=False)에서는 항상 False(불필요)."""
    if not USE_MEMORY_DB or not os.path.exists(DB_PATH):
        return False
    _ensure_mem_anchor()
    src = sqlite3.connect(DB_PATH, timeout=30)
    try:
        dst_pooled = get_conn()
        dst_real = object.__getattribute__(dst_pooled, "_real")
        src.backup(dst_real)   # game.db → 인메모리로 전체 복사
        return True
    finally:
        src.close()

def flush_to_disk():
    """라이브 인메모리 DB 내용을 game.db 파일로 백업한다(자동저장·종료 시 호출).
    임시파일에 먼저 백업한 뒤 os.replace로 원자적 치환 — 백업 도중 앱이
    죽어도 기존 세이브 파일은 손상되지 않는다.
    디스크 직결 모드에서는 이미 매 commit이 곧 저장이므로 아무 것도 안 함.

    [2026-07 버그 수정, 2차] "cannot commit - no transaction is active" /
    "not an error" 크래시가 반복됐다.
    원인: _pool_conn은 앱 생명주기 내내 재사용되는 단일 실제 커넥션인데,
    sqlite3.Connection.backup()을 그 커넥션 위에서 직접 호출하면 파이썬의
    암묵적 트랜잭션 추적(BEGIN을 언제 실행했는지 기억하는 내부 상태)이
    backup()의 C-레벨 API 경로를 거치면서 틀어졌다. 1차 수정(backup 직후
    바로 commit해서 착각 상태를 지우는 방식)으로 commit() 크래시는
    없앴지만, 그 다음 execute() 자체가 "not an error"로 터지는 변종이
    또 나왔다 — 즉 이 커넥션을 backup()에 한 번이라도 관여시키는 이상
    상태 오염 가능성 자체가 근본적으로 남아있었다.
    진짜 수정: 애초에 게임 진행용 풀 커넥션(_pool_conn)을 backup()에
    아예 관여시키지 않는다. 인메모리 DB가 공유 캐시 모드
    (cache=shared)라서, 같은 URI로 새 커넥션을 하나 더 열면 그 커넥션도
    똑같은 라이브 데이터를 그대로 볼 수 있다 — 그 '별도 스냅샷 커넥션'
    으로만 backup()을 수행하고 끝나면 바로 닫아버리면, 게임 진행용
    풀 커넥션의 트랜잭션 상태는 이 함수 실행 전후로 단 1비트도 안 바뀐다.
    """
    if not USE_MEMORY_DB:
        return
    _ensure_mem_anchor()
    tmp_path = DB_PATH + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    # 게임 진행용 풀 커넥션(_pool_conn)은 절대 안 건드린다 — 공유 캐시
    # URI로 새 스냅샷 커넥션을 열어서 그걸로만 backup 하고 바로 닫는다.
    src_snapshot = sqlite3.connect(_MEM_URI, uri=True, timeout=30)
    dst = sqlite3.connect(tmp_path, timeout=30)
    try:
        src_snapshot.backup(dst)
    finally:
        dst.close()
        src_snapshot.close()
    os.replace(tmp_path, DB_PATH)

# ─── 스키마 ───────────────────────────────────────────────────
def init_db():
    from constants import GAME_START_YEAR, PLAYER_START_AGE
    # [최적화] 인메모리 모드: 기존 세이브(game.db)가 있으면 먼저 인메모리로
    # 통째로 복사해온다. 그 뒤 CREATE TABLE IF NOT EXISTS들은 전부 멱등이라
    # 이미 로드된 데이터를 건드리지 않고 안전하게 지나간다.
    load_from_disk()
    conn = get_conn(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS countries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, flag TEXT, continent TEXT, language TEXT,
        fifa_rank INTEGER DEFAULT 100, grade TEXT DEFAULT 'F')""")
    c.execute("""CREATE TABLE IF NOT EXISTS leagues(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        country_id INTEGER, tier INTEGER, name TEXT,
        FOREIGN KEY(country_id) REFERENCES countries(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS teams(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        league_id INTEGER, country_id INTEGER, name TEXT,
        formation TEXT DEFAULT '4-4-2', current_tier INTEGER,
        wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0, goals_for INTEGER DEFAULT 0,
        goals_against INTEGER DEFAULT 0,
        FOREIGN KEY(league_id) REFERENCES leagues(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS player_names(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        country_id INTEGER, name TEXT,
        FOREIGN KEY(country_id) REFERENCES countries(id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_players(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id INTEGER, name TEXT, position TEXT,
        stamina INTEGER DEFAULT 50, speed INTEGER DEFAULT 50,
        jump INTEGER DEFAULT 50, strength INTEGER DEFAULT 50,
        shooting INTEGER DEFAULT 50,
        passing INTEGER DEFAULT 50, dribbling INTEGER DEFAULT 50,
        tackling INTEGER DEFAULT 50, heading INTEGER DEFAULT 50,
        positioning INTEGER DEFAULT 50, setpiece INTEGER DEFAULT 50,
        mental INTEGER DEFAULT 50, confidence INTEGER DEFAULT 50,
        leadership INTEGER DEFAULT 50, concentration INTEGER DEFAULT 50,
        ovr INTEGER DEFAULT 50, nationality TEXT DEFAULT '',
        FOREIGN KEY(team_id) REFERENCES teams(id))""")
    c.execute(f"""CREATE TABLE IF NOT EXISTS my_player(
        id INTEGER PRIMARY KEY,
        name TEXT, nationality TEXT, flag TEXT,
        age INTEGER DEFAULT 16, birth_year INTEGER DEFAULT {GAME_START_YEAR - PLAYER_START_AGE},
        position TEXT DEFAULT 'CM', sub_role TEXT DEFAULT '박스투박스',
        personality TEXT DEFAULT '성실함', height INTEGER DEFAULT 175,
        weight INTEGER DEFAULT 70, peak_age INTEGER DEFAULT 25,
        fame INTEGER DEFAULT 0, popularity INTEGER DEFAULT 0,
        fans INTEGER DEFAULT 0, agent_grade TEXT DEFAULT 'F',
        salary INTEGER DEFAULT 0, total_assets INTEGER DEFAULT 0,
        stress INTEGER DEFAULT 10, happiness INTEGER DEFAULT 10,
        slump INTEGER DEFAULT 0, injured INTEGER DEFAULT 0,
        injury_weeks INTEGER DEFAULT 0, injury_type TEXT DEFAULT '',
        current_team_id INTEGER DEFAULT 0,
        current_league_id INTEGER DEFAULT 0,
        loan_from_team_id INTEGER DEFAULT 0,
        loan_from_league_id INTEGER DEFAULT 0,
        loan_from_tier INTEGER DEFAULT 0,
        loan_end_year INTEGER DEFAULT 0,
        manager_relation INTEGER DEFAULT 50,
        current_year INTEGER DEFAULT {GAME_START_YEAR},
        current_week INTEGER DEFAULT 1,
        current_season INTEGER DEFAULT 1,
        total_matches INTEGER DEFAULT 0, total_goals INTEGER DEFAULT 0,
        total_assists INTEGER DEFAULT 0, total_seasons INTEGER DEFAULT 0,
        season_matches INTEGER DEFAULT 0, season_goals INTEGER DEFAULT 0,
        season_assists INTEGER DEFAULT 0, season_saves INTEGER DEFAULT 0,
        season_rating_sum REAL DEFAULT 0, season_rating_cnt INTEGER DEFAULT 0,
        language TEXT DEFAULT 'ko',
        stamina INTEGER DEFAULT 40, stamina_max INTEGER DEFAULT 75,
        speed INTEGER DEFAULT 40, speed_max INTEGER DEFAULT 75,
        jump INTEGER DEFAULT 40, jump_max INTEGER DEFAULT 75,
        strength INTEGER DEFAULT 40, strength_max INTEGER DEFAULT 75,
        shooting INTEGER DEFAULT 40, shooting_max INTEGER DEFAULT 75,
        passing INTEGER DEFAULT 40, passing_max INTEGER DEFAULT 75,
        dribbling INTEGER DEFAULT 40, dribbling_max INTEGER DEFAULT 75,
        tackling INTEGER DEFAULT 40, tackling_max INTEGER DEFAULT 75,
        heading INTEGER DEFAULT 40, heading_max INTEGER DEFAULT 75,
        positioning INTEGER DEFAULT 40, positioning_max INTEGER DEFAULT 75,
        setpiece INTEGER DEFAULT 40, setpiece_max INTEGER DEFAULT 75,
        mental INTEGER DEFAULT 40, mental_max INTEGER DEFAULT 75,
        confidence INTEGER DEFAULT 40, confidence_max INTEGER DEFAULT 75,
        leadership INTEGER DEFAULT 40, leadership_max INTEGER DEFAULT 75,
        concentration INTEGER DEFAULT 40, concentration_max INTEGER DEFAULT 75,
        ovr INTEGER DEFAULT 40)""")
    c.execute("""CREATE TABLE IF NOT EXISTS career_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        age INTEGER, position TEXT DEFAULT '', team_name TEXT, league_name TEXT, tier INTEGER,
        salary INTEGER, start_year INTEGER, start_week INTEGER,
        end_year INTEGER DEFAULT 0, end_week INTEGER DEFAULT 0,
        matches INTEGER DEFAULT 0, goals INTEGER DEFAULT 0,
        assists INTEGER DEFAULT 0, saves INTEGER DEFAULT 0,
        goals_against INTEGER DEFAULT 0,
        avg_rating REAL DEFAULT 0,
        team_rank INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS promotion_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, team_name TEXT, from_tier INTEGER,
        to_tier INTEGER, league_name TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trophy_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, team_name TEXT, league_name TEXT,
        tier INTEGER, competition TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS awards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER,
        award_type TEXT,
        league_name TEXT,
        detail TEXT,
        is_mine INTEGER DEFAULT 1)""")
    c.execute(f"""CREATE TABLE IF NOT EXISTS game_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry TEXT, log_type TEXT DEFAULT 'normal',
        year INTEGER DEFAULT {GAME_START_YEAR}, week INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS match_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        league_id INTEGER, week INTEGER,
        home_team_id INTEGER, away_team_id INTEGER,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        season INTEGER, year INTEGER)""")
    # [2026-07 신설, 성능] 시즌 전환(52→1주) 시마다 match_results가 시즌당
    # ~17만 행씩 영구히 쌓여서(지우는 로직이 없었음), 시즌이 쌓일수록 다음
    # 시즌 일정 INSERT 비용이 계속 늘어나는 문제가 있었다(6개 인덱스 갱신
    # 비용이 테이블 크기에 비례). 완료된 과거 시즌은 이 아카이브 테이블로
    # 옮기고 match_results는 '진행 중인 이번 시즌'만 유지한다 — world_browser의
    # 역대 순위/우승팀 조회는 두 테이블을 함께 보도록 수정했으므로(get_league_
    # standings 등) 화면에 보이는 결과는 완전히 동일하다. 스키마는 match_results
    # 와 동일(신설 테이블이라 day 컬럼도 처음부터 포함).
    c.execute("""CREATE TABLE IF NOT EXISTS match_results_archive(
        id INTEGER PRIMARY KEY,
        league_id INTEGER, week INTEGER,
        home_team_id INTEGER, away_team_id INTEGER,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        season INTEGER, year INTEGER, day INTEGER)""")
    # 경기 상세(클릭 시 펼쳐보는 데이터)를 JSON으로 보관.
    #   game_log 의 헤더 줄에 <a href="match:{id}"> 앵커로 연결된다.
    #   detail_json 안에 전/후반 이벤트·평점·세부지표·총평이 모두 들어간다.
    c.execute("""CREATE TABLE IF NOT EXISTS match_details(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, week INTEGER, season INTEGER,
        league_name TEXT, is_home INTEGER,
        home_name TEXT, away_name TEXT,
        home_score INTEGER, away_score INTEGER,
        result TEXT, rating REAL,
        goals INTEGER, assists INTEGER, saves INTEGER,
        detail_json TEXT)""")
    c.execute(f"""CREATE TABLE IF NOT EXISTS season_state(
        id INTEGER PRIMARY KEY,
        current_year INTEGER DEFAULT {GAME_START_YEAR},
        current_week INTEGER DEFAULT 1,
        current_season INTEGER DEFAULT 1,
        phase TEXT DEFAULT 'preseason')""")
    c.execute("""CREATE TABLE IF NOT EXISTS intl_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, competition TEXT, team_name TEXT,
        result TEXT, goals INTEGER DEFAULT 0, assists INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS intl_tournaments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, kind TEXT, name TEXT,
        status TEXT DEFAULT 'group', winner TEXT DEFAULT '',
        my_selected INTEGER DEFAULT 0, my_result TEXT DEFAULT '')""")
    c.execute("""CREATE TABLE IF NOT EXISTS intl_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, country TEXT, flag TEXT, grade TEXT,
        ovr REAL, grp TEXT, pot INTEGER, alive INTEGER DEFAULT 1,
        is_my INTEGER DEFAULT 0, continent TEXT DEFAULT '')""")
    c.execute("""CREATE TABLE IF NOT EXISTS intl_matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, stage TEXT, grp TEXT DEFAULT '',
        week INTEGER, home TEXT, away TEXT,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        pso_winner TEXT DEFAULT '', pso_score TEXT DEFAULT '',
        is_my INTEGER DEFAULT 0, slot INTEGER DEFAULT 0,
        my_played INTEGER DEFAULT 0, my_nat TEXT DEFAULT '',
        my_position TEXT DEFAULT '', my_saves INTEGER DEFAULT 0,
        my_goals INTEGER DEFAULT 0, my_assists INTEGER DEFAULT 0,
        my_rating REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS qual_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_year INTEGER, kind TEXT, continent TEXT DEFAULT '',
        country TEXT, flag TEXT, grade TEXT, ovr REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS meta(
        key TEXT PRIMARY KEY, value TEXT)""")
    # ── 클럽 대륙 챔피언스리그 (champions_engine) ──
    c.execute("""CREATE TABLE IF NOT EXISTS cl_tournaments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, continent TEXT, name TEXT,
        status TEXT DEFAULT 'ko', first_stage TEXT DEFAULT 'R32',
        winner_team_id INTEGER DEFAULT 0,
        my_in INTEGER DEFAULT 0, my_result TEXT DEFAULT '',
        my_team_id INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cl_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, team_id INTEGER, team_name TEXT,
        flag TEXT, country TEXT, grade TEXT, ovr REAL,
        alive INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cl_matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, stage TEXT, week INTEGER,
        home_team_id INTEGER, away_team_id INTEGER,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        pso_winner INTEGER DEFAULT 0, pso_score TEXT DEFAULT '',
        is_my INTEGER DEFAULT 0, slot INTEGER DEFAULT 0,
        my_played INTEGER DEFAULT 0, my_position TEXT DEFAULT '',
        my_saves INTEGER DEFAULT 0, my_goals INTEGER DEFAULT 0,
        my_assists INTEGER DEFAULT 0, my_rating REAL DEFAULT 0)""")
    # 챔스 대회별 내 성적 (월드컵 intl_history와 동일 구조: 몇강/우승/탈락 + 활약)
    c.execute("""CREATE TABLE IF NOT EXISTS cl_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, competition TEXT, team_name TEXT, result TEXT,
        goals INTEGER DEFAULT 0, assists INTEGER DEFAULT 0,
        caps INTEGER DEFAULT 0, rating REAL DEFAULT 0)""")
    # ── 클럽 월드컵 (club_world_cup_engine, 2026-07 신설) ──
    # cl_tournaments/cl_entries/cl_matches와 동일한 구조를 그대로 따른다
    # (다른 화면/함수들이 그 패턴에 익숙하므로 재사용성을 최대화하기 위함).
    # winner_team_id는 대회 하나(그 해 클럽월드컵 전체, 대륙별 아님)의 우승팀.
    c.execute("""CREATE TABLE IF NOT EXISTS cwc_tournaments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, name TEXT DEFAULT '클럽 월드컵',
        status TEXT DEFAULT 'group', winner_team_id INTEGER DEFAULT 0,
        my_in INTEGER DEFAULT 0, my_result TEXT DEFAULT '',
        my_team_id INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cwc_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, team_id INTEGER, team_name TEXT,
        flag TEXT, country TEXT, continent TEXT, grp TEXT DEFAULT '',
        grade TEXT, ovr REAL, alive INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cwc_matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, stage TEXT, week INTEGER,
        home_team_id INTEGER, away_team_id INTEGER,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        pso_winner INTEGER DEFAULT 0, pso_score TEXT DEFAULT '',
        is_my INTEGER DEFAULT 0, slot INTEGER DEFAULT 0,
        my_played INTEGER DEFAULT 0, my_position TEXT DEFAULT '',
        my_saves INTEGER DEFAULT 0, my_goals INTEGER DEFAULT 0,
        my_assists INTEGER DEFAULT 0, my_rating REAL DEFAULT 0,
        my_absence_reason TEXT DEFAULT NULL)""")
    # [2026-07 신설] 국내 컵대회(FA컵식) — 1~2부 팀 전부 참가하는 단판
    # 토너먼트(무승부는 즉시 승부차기). 선수 소속 국가 하나에 대해서만
    # 지연 생성한다(전 세계 100개국 넘는 나라마다 만들면 성능 부담이
    # 크고 의미도 없음 — 챔스가 '내 대륙', 월드컵이 '내 국가대표'로
    # 범위를 좁힌 것과 같은 원칙).
    c.execute("""CREATE TABLE IF NOT EXISTS cup_tournaments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, country_id INTEGER, name TEXT,
        status TEXT DEFAULT 'active',
        total_rounds INTEGER DEFAULT 0,
        round_counter INTEGER DEFAULT 0,
        pending_tiers TEXT DEFAULT '',
        winner_team_id INTEGER DEFAULT 0,
        my_in INTEGER DEFAULT 0, my_result TEXT DEFAULT '',
        my_team_id INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cup_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, team_id INTEGER, team_name TEXT,
        tier INTEGER, ovr REAL, alive INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cup_matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, round_name TEXT, round_idx INTEGER, week INTEGER,
        home_team_id INTEGER, away_team_id INTEGER,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        pso_winner INTEGER DEFAULT 0, pso_score TEXT DEFAULT '',
        is_my INTEGER DEFAULT 0, slot INTEGER DEFAULT 0,
        my_played INTEGER DEFAULT 0, my_goals INTEGER DEFAULT 0,
        my_assists INTEGER DEFAULT 0, my_saves INTEGER DEFAULT 0,
        my_rating REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cup_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, team_name TEXT, result TEXT,
        goals INTEGER DEFAULT 0, assists INTEGER DEFAULT 0,
        caps INTEGER DEFAULT 0, rating REAL DEFAULT 0)""")
    # 오퍼 거절 기록 (기존 코드가 참조하나 생성 누락되어 있던 테이블)
    c.execute("""CREATE TABLE IF NOT EXISTS offer_refused(
        team_id INTEGER, year INTEGER)""")
    # 마이그레이션: 컬럼 추가
    for migration in [
        "ALTER TABLE career_entries ADD COLUMN position TEXT DEFAULT ''",
        # [신규] 포제션 로그(match_flow.generate_possession_log 결과)를 담을
        # 컬럼. 경기당 한 번 통째로 쓰고 통째로 읽는 구조화 데이터라서,
        # detail_json과 같은 성격 — JSON 텍스트 컬럼 하나면 충분하고,
        # 별도 정규화 테이블보다 이 쪽이 쓰기/읽기 비용이 훨씬 적다.
        # 은퇴 후 새 게임 시작 시 reset_game_data()가 match_details 테이블을
        # DELETE FROM으로 통째로 비우므로, 이 컬럼도 별도 처리 없이 자동으로
        # 같이 삭제된다(새 테이블로 만들었다면 저 리스트에 수동으로 추가하는
        # 걸 깜빡할 위험이 있었다).
        "ALTER TABLE match_details ADD COLUMN possession_log TEXT DEFAULT ''",
        # [신규] 그 경기에 실제로 뛴 것으로 간주할 11명(포메이션 슬롯 순서)의
        # 최소 스탯 스냅샷. possession_log와 같은 이유로 컬럼 하나면 충분
        # (경기당 한 번 통째로 쓰고 통째로 읽음). reset_game_data()가
        # match_details를 통째로 지우므로 이것도 자동으로 같이 삭제된다.
        "ALTER TABLE match_details ADD COLUMN lineup_stats TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN field_pos TEXT DEFAULT ''",   # 배치 포지션
        "ALTER TABLE my_player ADD COLUMN mismatch_rank INTEGER DEFAULT 0", # 포지션 불일치 단계
        "ALTER TABLE career_entries ADD COLUMN saves INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN goals_against INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_goals_against INTEGER DEFAULT 0",
        "ALTER TABLE intl_history ADD COLUMN competition TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN total_saves INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_goals_against INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_earnings INTEGER DEFAULT 0",  # 이슈10
        # ── [세부 지표] 포지션별 활약을 보여줄 경기 누적 스탯 ──────────
        #   shots=슈팅, shots_on=유효슈팅, key_passes=기회창출(키패스),
        #   dribbles=드리블 성공, pass_acc_sum/pass_acc_cnt=패스성공률 누적(평균용),
        #   blocks=차단(태클+인터셉트). season_=이번 시즌, total_=통산.
        "ALTER TABLE my_player ADD COLUMN season_shots INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_shots_on INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_key_passes INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_dribbles INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_blocks INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_pass_acc_sum REAL DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_pass_acc_cnt INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_shots INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_shots_on INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_key_passes INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_dribbles INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_blocks INTEGER DEFAULT 0",
        # career_entries: 시즌(팀별) 단위 세부 지표 + 패스성공률(저장 시점 평균)
        "ALTER TABLE career_entries ADD COLUMN shots INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN shots_on INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN key_passes INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN dribbles INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN blocks INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN pass_acc REAL DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN contract_years INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN contract_end_year INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN first_half_rating REAL DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN current_tier INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN _contract_renew_offer INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN _contract_renew_years INTEGER DEFAULT 0",
        # [에이전트] 실제 계약한 에이전트의 수수료율 (같은 등급도 개별 차등).
        #  0이면 미설정 → AGENT_FEE_RATE[grade] 기본값 사용 (구버전 호환).
        "ALTER TABLE my_player ADD COLUMN agent_fee_rate REAL DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN contract_years INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN transfer_type TEXT DEFAULT '입단'",
        "ALTER TABLE career_entries ADD COLUMN clean_sheets INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN team_id INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN intl_caps INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN intl_goals INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN intl_assists INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_goals INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_assists INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_rating REAL DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_played INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_nat TEXT DEFAULT ''",
        "ALTER TABLE intl_matches ADD COLUMN my_position TEXT DEFAULT ''",
        "ALTER TABLE intl_matches ADD COLUMN my_saves INTEGER DEFAULT 0",
        "ALTER TABLE intl_history ADD COLUMN caps INTEGER DEFAULT 0",
        "ALTER TABLE intl_history ADD COLUMN rating REAL DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN talent_cap INTEGER DEFAULT 88",
        "ALTER TABLE my_player ADD COLUMN talent_tier TEXT DEFAULT 'normal'",
        # [기능1] 이적 오퍼 맥락 — 입단 시 확정된 계약 조건 저장
        "ALTER TABLE my_player ADD COLUMN contract_role TEXT DEFAULT '주전 경쟁'",
        "ALTER TABLE my_player ADD COLUMN club_ambition TEXT DEFAULT '중위권 안정'",
        "ALTER TABLE my_player ADD COLUMN appearance_bonus_k INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN goal_bonus_k INTEGER DEFAULT 0",
        # [기능2] 감독 성향 — 현재 소속팀 감독 타입
        "ALTER TABLE my_player ADD COLUMN manager_type TEXT DEFAULT '베테랑 신뢰'",
        # [기능3] 능동 액션 — 이적 요청 플래그(다음 오퍼 창에 반영)
        "ALTER TABLE my_player ADD COLUMN transfer_requested INTEGER DEFAULT 0",
        # [커리어 보강] 각 소속의 역할·감독성향·구단야망 기록 → AI 요약 서사 재료
        "ALTER TABLE career_entries ADD COLUMN contract_role TEXT DEFAULT ''",
        "ALTER TABLE career_entries ADD COLUMN manager_type TEXT DEFAULT ''",
        "ALTER TABLE career_entries ADD COLUMN club_ambition TEXT DEFAULT ''",
        # [나간 경로] 그 팀에서 어떻게 떠났는지: ''(재직중/정상) / '팔림' / '방출' / '이적' / '계약만료'
        "ALTER TABLE career_entries ADD COLUMN exit_type TEXT DEFAULT ''",
        # [신체 특징] 성격과 별개의 신체 특성 (부상체질/강철체질/신체천재 등)
        "ALTER TABLE my_player ADD COLUMN physical_trait TEXT DEFAULT '무난함'",
        # [복수국적] 두 번째 국적/국기, 그리고 A매치 출전으로 '고정'된 대표팀.
        #  nationality2='' 이면 단일국적(기존과 동일 동작).
        #  intl_committed='' 이면 아직 어느 대표팀에도 묶이지 않아 자유 선택 가능.
        "ALTER TABLE my_player ADD COLUMN nationality2 TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN flag2 TEXT DEFAULT ''",
        # [복수국적 확장] 세 번째 국적까지 지원 (최대 3개).
        "ALTER TABLE my_player ADD COLUMN nationality3 TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN flag3 TEXT DEFAULT ''",
        # [복수국적 확장 2026-07] 네 번째 국적까지 지원 (최대 4개).
        # 시작 국적(1개, 무작위 부여 없음) + 귀화로 최대 3개까지 추가 가능.
        "ALTER TABLE my_player ADD COLUMN nationality4 TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN flag4 TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN intl_committed TEXT DEFAULT ''",
        # [귀화] 같은 나라(리그)에서 누적 거주 연수 추적. 3년 채우면 그 나라
        #  귀화 국적 획득 자격(21세 이전 + 본선 미경험 조건과 함께).
        #  residency_country: 현재 거주 중인 리그의 소속 국가
        #  residency_years:   그 나라에서 연속 채운 연수 (나라 바뀌면 리셋)
        "ALTER TABLE my_player ADD COLUMN residency_country TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN residency_years INTEGER DEFAULT 0",
        # [귀화] 이미 귀화로 획득한 국적 목록(쉼표구분) — 중복 획득 방지용
        "ALTER TABLE my_player ADD COLUMN naturalized_nats TEXT DEFAULT ''",
        # [cap-tie] A대표 '본선' 무대를 밟았는지. 본선 출전 시 1 → 국적 영구고정.
        #  예선만 뛴 것은 0 유지(예선은 cap-tie 아님, 현실 FIFA 규칙).
        "ALTER TABLE my_player ADD COLUMN intl_capped INTEGER DEFAULT 0",
        # [출생국적] 태어난 고향 국적(=1차 국적). 귀화/대표선택과 무관하게 절대 불변.
        #  은퇴 AI요약에서 '디에고 코스타: 브라질 출생→스페인 대표'처럼 출생지를 보존.
        "ALTER TABLE my_player ADD COLUMN origin_nat TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN origin_flag TEXT DEFAULT ''",
        # [국적 연혁] 국적 취득/대표선택 이력 JSON(list of dict).
        #  각 항목: {"type": "birth|naturalize|commit", "nat","flag","year","week"}
        #  - birth     : 출생 시 보유 국적 (시작국적 + 시작 복수국적)
        #  - naturalize: 귀화로 새 국적 획득
        #  - commit    : 평생 뛸 대표 국적 확정
        "ALTER TABLE my_player ADD COLUMN nat_history TEXT DEFAULT ''",
        # [복수국적] 이 대회에서 내가 '어느 나라로' 뛰는지. ''=미정/해당없음.
        # my_selected=3 은 '둘 다 진출 → 대표팀 선택 대기' 상태를 뜻한다.
        "ALTER TABLE intl_tournaments ADD COLUMN my_nat TEXT DEFAULT ''",
        # [선택 우선] 21세 이하 미고정 선수가 '선발은 통과했지만 아직 본인이
        #  대표 출전을 고르지 않은' 후보 국적들(CSV). 선택창은 이 목록으로 띄운다.
        #  → 선택을 먼저 받고, 그 다음에 예선 통과/탈락 결과를 공개하기 위함.
        "ALTER TABLE intl_tournaments ADD COLUMN cand_nats TEXT DEFAULT ''",
        # [챔스 출전자격 고정] 대회 생성(41주) 시점의 내 소속팀 ID.
        #  시즌 중 다른 팀으로 이적하면 current_team_id와 달라지므로,
        #  이 값과 비교해 '등록 마감 후 합류'는 그 시즌 챔스에 못 뛰게 한다.
        "ALTER TABLE cl_tournaments ADD COLUMN my_team_id INTEGER DEFAULT 0",
        # [신체 아키타입] 체형 유형 + 몸싸움(strength) 스탯
        "ALTER TABLE my_player ADD COLUMN body_type TEXT DEFAULT '인간 발전기형'",
        "ALTER TABLE my_player ADD COLUMN strength INTEGER DEFAULT 50",
        "ALTER TABLE my_player ADD COLUMN strength_max INTEGER DEFAULT 75",
        "ALTER TABLE ai_players ADD COLUMN strength INTEGER DEFAULT 50",
        # [세부 지표] 국제전·챔스 경기에도 클럽과 동일한 활약 수치를 기록.
        #   shots/shots_on/key_passes/dribbles/blocks/pass_acc
        "ALTER TABLE intl_matches ADD COLUMN my_shots INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_shots_on INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_key_passes INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_dribbles INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_blocks INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_pass_acc REAL DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_conceded INTEGER DEFAULT 0",
        "ALTER TABLE cl_matches ADD COLUMN my_shots INTEGER DEFAULT 0",
        "ALTER TABLE cl_matches ADD COLUMN my_shots_on INTEGER DEFAULT 0",
        "ALTER TABLE cl_matches ADD COLUMN my_key_passes INTEGER DEFAULT 0",
        "ALTER TABLE cl_matches ADD COLUMN my_dribbles INTEGER DEFAULT 0",
        "ALTER TABLE cl_matches ADD COLUMN my_blocks INTEGER DEFAULT 0",
        "ALTER TABLE cl_matches ADD COLUMN my_pass_acc REAL DEFAULT 0",
        "ALTER TABLE cl_matches ADD COLUMN my_conceded INTEGER DEFAULT 0",
        # [2026-07 신설, 신민용 요청: "전체 이력에 슈팅/드리블도 채워달라"]
        # cl_matches/intl_matches는 이미 세부 지표를 저장하고 있었는데,
        # cup_matches/cwc_matches는 _player_perf가 detail을 계산까지는
        # 해놓고 저장을 안 하고 있었다(버려지고 있었음) — 나머지 둘도
        # 동일하게 컬럼을 맞춰서 cup_engine.py/club_world_cup_engine.py가
        # 저장할 수 있게 한다.
        "ALTER TABLE cup_matches ADD COLUMN my_shots INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN my_shots_on INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN my_key_passes INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN my_dribbles INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN my_blocks INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN my_pass_acc REAL DEFAULT 0",
        "ALTER TABLE cwc_matches ADD COLUMN my_shots INTEGER DEFAULT 0",
        "ALTER TABLE cwc_matches ADD COLUMN my_shots_on INTEGER DEFAULT 0",
        "ALTER TABLE cwc_matches ADD COLUMN my_key_passes INTEGER DEFAULT 0",
        "ALTER TABLE cwc_matches ADD COLUMN my_dribbles INTEGER DEFAULT 0",
        "ALTER TABLE cwc_matches ADD COLUMN my_blocks INTEGER DEFAULT 0",
        "ALTER TABLE cwc_matches ADD COLUMN my_pass_acc REAL DEFAULT 0",
        # [2026-07 버그수정, 신민용 리포트: "클럽월드컵 경기 일정 여니
        # 'no such column: grp' 에러"] cl_matches엔 grp 컬럼이 있는데
        # cwc_matches엔 애초에 빠져있었다 — club_world_cup_engine.py의
        # get_cwc_group_standings/_group_standings, ui/schedule_window.py의
        # _make_cwc_tab이 이미 cwc_matches.grp를 조회하고 있었는데(다른
        # 대회처럼 조 배정 정보가 매치 테이블에도 있을 거라 가정하고 작성),
        # 정작 테이블에 그 컬럼 자체가 없었다.
        "ALTER TABLE cwc_matches ADD COLUMN grp TEXT DEFAULT ''",
        # [챔스 조별리그] 그룹 라벨(A~H). 토너먼트 경기는 ''.
        "ALTER TABLE cl_matches ADD COLUMN grp TEXT DEFAULT ''",
        # [챔스 조별] entries에 조 배정 저장.
        "ALTER TABLE cl_entries ADD COLUMN grp TEXT DEFAULT ''",
        # [챔스 진출권] 내가 그 해 리그 1위로 '출전 자격'을 얻었는지(1) 아닌지(0).
        #  자격이 없으면(2위 이하) 그 대회와 무관 → '본선 진출 실패'도 안 뜬다.
        "ALTER TABLE cl_tournaments ADD COLUMN my_qualified INTEGER DEFAULT 0",
        # [노화] 전성기(peak) 시점의 각 스탯 _max 스냅샷 JSON. 노화 하한선(floor)
        #  계산의 기준값. 노화가 처음 시작될 때 1회 기록되며 이후 불변.
        #  ''(빈값)이면 아직 스냅샷 전(전성기 이전).
        "ALTER TABLE my_player ADD COLUMN aging_peak_max TEXT DEFAULT ''",
        # [UI 진행 상태 영속화] 메인 화면의 1주/4주 모드, 진행 중인 묶음 위치,
        #  고정된 4주 일정, 4개 콤보(훈련) 선택값을 세이브에 저장한다.
        #  → 나갔다 들어와도 화면이 그대로 복원되어 일정/모드가 어긋나지 않음.
        #  step_mode   : 0=4주씩, 1=1주씩
        #  step_idx    : 1주씩 모드에서 현재 묶음 진행 위치(0~3)
        #  locked_sched: 1주씩 진행 중 고정된 4주 일정 JSON (없으면 '')
        #  week_combos : 4개 주차 콤보의 선택값 JSON 리스트 (없으면 '')
        "ALTER TABLE season_state ADD COLUMN step_mode INTEGER DEFAULT 0",
        "ALTER TABLE season_state ADD COLUMN step_idx INTEGER DEFAULT 0",
        "ALTER TABLE season_state ADD COLUMN locked_sched TEXT DEFAULT ''",
        "ALTER TABLE season_state ADD COLUMN week_combos TEXT DEFAULT ''",
        # [AI 선수 생애] 나이 컬럼. 시즌 종료 시 +1 되며 성장/노화/은퇴의 기준.
        #  기존 세이브엔 없으므로 추가 후 NULL인 행은 _ensure_ai_ages()가 랜덤 채움.
        "ALTER TABLE ai_players ADD COLUMN age INTEGER DEFAULT 0",
        # [세부역할 2026-07] AI 선수도 세부역할(SUB_ROLES)을 갖도록 컬럼 추가.
        #  기존엔 이 컬럼 자체가 없어서 sub_role은 내 선수(my_player)에만
        #  있었다 — 세부역할별 매치 가중치(_SUB_ROLE_MATCH_MOD)를 AI 시즌
        #  추정(_estimate_ai_season)에도 적용하려면 AI도 값이 있어야 한다.
        #  기존 세이브의 빈 값은 _ensure_ai_sub_roles()가 포지션에 맞는
        #  값으로 한 번에 채운다.
        "ALTER TABLE ai_players ADD COLUMN sub_role TEXT DEFAULT ''",
        # [예선] 예선에서 선택해 뛴 나라. 본선 해에 이 나라로 자동 출전(cap-tie 전).
        #  예선 시작 시 리셋되어, 21세 이하면 다음 예선 때 다른 나라 선택 가능.
        "ALTER TABLE my_player ADD COLUMN qual_pledged_nat TEXT DEFAULT ''",
        # [예선 대륙] 예선 대회(wc_qual)가 어느 대륙(연맹)의 예선인지 저장.
        "ALTER TABLE intl_tournaments ADD COLUMN continent TEXT DEFAULT ''",
        # [예선 entries] 내 국적 포함 여부 + 소속 대륙
        "ALTER TABLE intl_entries ADD COLUMN is_my INTEGER DEFAULT 0",
        "ALTER TABLE intl_entries ADD COLUMN continent TEXT DEFAULT ''",
        # [일 단위 캘린더] 팀 수가 리그마다 8~30(짝수)로 달라지면서 라운드 수도
        # 달라져, 이제 각 라운드가 정확히 무슨 '일자'인지 별도로 저장한다.
        # week 컬럼은 그대로 두고(day로부터 항상 역산 가능하게 유지 —
        # constants.day_to_week 참고) 이 컬럼은 순수 추가 정보다. 기존
        # week 기반 쿼리(예: _sim_all_ai_matches)는 전혀 손대지 않아도
        # 계속 정상 동작한다. 기존 세이브의 과거 경기는 NULL로 남아도 무방
        # (day는 표시/간격 계산용이라 이미 끝난 경기엔 의미 없음).
        "ALTER TABLE match_results ADD COLUMN day INTEGER",
        # [오퍼 토글] 재직 중 자동 이적 오퍼 팝업을 끌 수 있는 스위치.
        #  기본값 1(활성) → 기존 세이브도 지금까지와 동일하게 오퍼가 뜬다.
        #  0이어도 '팀 입단'(무소속 강제 입단)과 '이적 요청' 중인 경우는 영향 없음.
        "ALTER TABLE my_player ADD COLUMN offers_enabled INTEGER DEFAULT 1",
        # [전성기 OVR] 커리어 통산 최고 OVR. game_engine.update_player()가 ovr을
        #  갱신할 때마다 자동으로 함께 갱신된다(역대 최고치만 남도록 max 적용).
        #  은퇴 화면 등에서 '최종 OVR'(노쇠로 하락한 값) 대신 전성기 기록을 보여주기 위함.
        "ALTER TABLE my_player ADD COLUMN peak_ovr INTEGER DEFAULT 0",
        # [일 단위 진행] 진행의 실제 기준값. 1~364 (년중 일자, DAYS_PER_WEEK=7 기준).
        #   current_week/current_year는 계속 이 값에서 파생돼 함께 갱신되므로
        #   (advance_days 참고), 기존 수백 곳의 'current_week'/'WHERE week=?'
        #   참조 코드는 전혀 손대지 않아도 계속 정상 동작한다.
        "ALTER TABLE my_player ADD COLUMN current_day INTEGER DEFAULT 1",
        "ALTER TABLE season_state ADD COLUMN current_day INTEGER DEFAULT 1",
        # [2026-07 추가] 부상 세부 명칭 — injury_type(경미/중간/심각 등급)과
        #  별개로, "왼쪽 햄스트링 부분 파열" 같은 구체적 부상명을 저장한다.
        #  등급별로 여러 구체 부상이 있고 회복 기간도 그 안에서 갈리므로
        #  등급 컬럼은 그대로 두고 이름만 추가 — 기존 injury_type을 읽는
        #  코드가 없어서 안전하게 병행 가능.
        "ALTER TABLE my_player ADD COLUMN injury_detail TEXT DEFAULT ''",
        # [2026-07 추가] 국제전/챔스/컵대회 경기의 '실제 진행 날짜'.
        #   이 세 대회는 원래 week 컬럼만 있고, 커리어/은퇴창 등에 표시할 땐
        #   그때그때 '내 현재 소속팀 기준으로' 요일을 재계산했다(_week_intl_cl_day) —
        #   그런데 그건 그 경기가 실제로 열린 시점이 아니라 '지금 시점 기준
        #   추정'이라, 과거 시즌 기록에 적용하면 시즌/소속팀이 달라져 엉뚱한
        #   날짜가 나올 수 있었다(신민용 지적: 커리어/은퇴창 기간이 정확한
        #   날짜로 안 뜸). 이제 경기가 실제로 시뮬레이션되는 순간(그 시점의
        #   진짜 소속팀·시즌 기준)에 날짜를 한 번 계산해서 이 컬럼에 그대로
        #   저장한다 — 이후 조회는 재계산 없이 저장된 값을 그대로 쓴다.
        #   기존 세이브의 과거 경기는 0으로 남으며, 표시할 땐 week 기반
        #   추정치로 안전하게 폴백한다.
        "ALTER TABLE intl_matches ADD COLUMN day INTEGER DEFAULT 0",
        "ALTER TABLE cl_matches ADD COLUMN day INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN day INTEGER DEFAULT 0",
        # [2026-07 신설] 직접 지원(팀 검색 후 지원하기) 시도 횟수. 무소속
        # 기간(첫 입단/계약종료·방출 후) 동안 최대 4회 — 팀에 재입단하면
        # 다음에 다시 무소속이 될 때(계약종료/방출) 0으로 리셋된다.
        "ALTER TABLE my_player ADD COLUMN apply_attempts_used INTEGER DEFAULT 0",
        # [2026-07 버그 수정] 3/4위전 유무 불일치 — 그 라운드에 들어온 팀 수가
        # 딱 4(=이름 "4강")일 때만 3/4위전을 만들었는데, 부전승 등으로 3팀이나
        # 5팀이 들어와도 결승 진출자 2명을 정하는 라운드인 건 똑같다. 라운드
        # 이름 대신 이 값(그 라운드에 실제로 들어온 팀 수, 부전승 포함)으로
        # "결승 직전 라운드인지"를 구조적으로 판별한다.
        "ALTER TABLE cup_matches ADD COLUMN pool_entering INTEGER DEFAULT 0",
        # [2026-07 신설] 퇴장(레드카드) → 다음 경기 출전정지 시스템.
        # 0이면 정상, N(>=1)이면 앞으로 내 경기 N번을 강제로 결장한다
        # (경기가 진행될 때마다 1씩 차감). '폭력적' 성격의 red_card_chance
        # 효과를 실제로 반영하기 위해 신설.
        "ALTER TABLE my_player ADD COLUMN red_card_suspension INTEGER DEFAULT 0",
        # [2026-07 신설, 신민용 확정] AI 선수 국적 시스템 — 지금까지
        # ai_players는 team_id(소속 클럽)만 있고 국적이 없었다. 국가대표
        # 스쿼드를 실제 선수로 선발하려면(월드컵 골든볼 등) 국적이 필요.
        "ALTER TABLE ai_players ADD COLUMN nationality TEXT DEFAULT ''",
        # [2026-07 버그수정, 신민용 리포트: "챔스 출전정지가 다음 리그경기에서
        # 소진되고, 정작 다음 챔스 경기는 뛸 수 있게 됨"] 예전엔 대회 구분 없이
        # red_card_suspension 카운터 하나를 리그/챔스/컵/국제전/클럽월드컵이
        # 전부 같이 썼다 — 그래서 어느 대회에서 받은 퇴장이든 "다음에 열리는
        # 아무 경기"에서 소진돼버렸다. 월드컵처럼 4년에 한 번 열리는 대회는
        # 특히 심각(퇴장당해도 사실상 다음 클럽경기 한 번만 쉬면 그만이었음).
        # 대회별로 카운터를 완전히 분리한다.
        "ALTER TABLE my_player ADD COLUMN cl_suspension INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN cup_suspension INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN intl_suspension INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN cwc_suspension INTEGER DEFAULT 0",
        # [2026-07 신설] 컵대회/챔스/국제대회에서 내가 결장한 이유(부상/출전정지
        # 등)를 기록 — 신민용 요청: 커리어 세부 기록·은퇴창·AI 요약에
        # "(부상)"/"(출전정지)" 식으로 표시하기 위함. NULL이면 정상 출전.
        "ALTER TABLE cl_matches ADD COLUMN my_absence_reason TEXT DEFAULT NULL",
        "ALTER TABLE cup_matches ADD COLUMN my_absence_reason TEXT DEFAULT NULL",
        "ALTER TABLE intl_matches ADD COLUMN my_absence_reason TEXT DEFAULT NULL",
        # [2026-07 버그수정] cwc_matches는 CREATE TABLE에 처음부터 이 컬럼을
        # 넣어놨지만, 이미 그 전 버전으로 한 번이라도 게임을 실행해서
        # cwc_matches 테이블이 컬럼 없이 먼저 만들어진 세이브(CREATE TABLE
        # IF NOT EXISTS라 기존 테이블은 안 바뀜)를 위한 마이그레이션.
        "ALTER TABLE cwc_matches ADD COLUMN my_absence_reason TEXT DEFAULT NULL",
        # [2026-07 신설, 신민용 요청] 임대(Loan) 시스템 — 원소속팀 계약(연봉/
        # 계약년수)은 그대로 둔 채 다른 팀에서 뛰는 기간을 추적하기 위한 필드.
        # loan_from_team_id=0이면 임대 아님(평소 상태).
        "ALTER TABLE my_player ADD COLUMN loan_from_team_id INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN loan_from_league_id INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN loan_from_tier INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN loan_end_year INTEGER DEFAULT 0",
    ]:
        # [정리] bare except → sqlite3.OperationalError로 좁힘.
        # (ALTER TABLE 재실행 시 "duplicate column" 등 예상된 실패만 무시하고,
        #  그 외 진짜 버그로 인한 예외는 숨기지 않는다. 동작은 기존과 동일.)
        try: c.execute(migration)
        except sqlite3.OperationalError: pass

    # [일 단위 진행 전환] 기존 세이브는 current_day가 이번에 막 1로 추가됐을 뿐
    #   실제 진행 상황(current_week)과 안 맞을 수 있다 — current_week 그대로인데
    #   current_day만 1이면 '연초로 되돌아간 것'처럼 보이므로, 한 번만
    #   current_week 기준으로 역산해 맞춰준다((week-1)*7+1 = 그 주 첫째 날).
    #   이후로는 advance_days()가 current_day를 진짜 기준으로 계속 갱신하므로
    #   이 보정은 최초 1회만 의미 있다(멱등: 이미 맞으면 그대로 둠).
    for _tbl in ("my_player", "season_state"):
        try:
            c.execute(f"""UPDATE {_tbl} SET current_day = (current_week - 1) * 7 + 1
                          WHERE current_day IS NULL OR current_day <= 1""")
        except Exception:
            pass

    # ── [버그수정] ai_players 스냅샷 테이블 (새 게임 리셋용) ──────────────
    # reset_game_data()가 teams(리그/tier)는 원본으로 되돌리면서 ai_players는
    # 안 건드려서, "새 게임"을 눌러도 이전 플레이에서 은퇴/성장으로 변형된
    # AI선수 5.9만 명이 그대로 남는 문제가 있었다(teams.league_id 리셋 안 되던
    # 버그와 같은 유형 — 리셋 함수가 "일부만" 리셋). 최초 시딩 직후 상태를
    # ai_players_seed에 스냅샷해두고, 새 게임 시 거기서 벌크 복원한다
    # (재생성 대신 순수 테이블 복사라 개별 INSERT/RANDOM() 쿼리 비용이 없음).
    # ai_players에 나중에 컬럼이 추가되는 마이그레이션이 있을 수 있으므로,
    # ai_players_seed는 고정 스키마로 안 박고 매번 ai_players 컬럼 구성에
    # 맞춰 동적으로 동기화한다.
    c.execute("CREATE TABLE IF NOT EXISTS ai_players_seed(id INTEGER PRIMARY KEY)")
    ai_cols = [r["name"] for r in c.execute("PRAGMA table_info(ai_players)").fetchall()]
    seed_cols = {r["name"] for r in c.execute("PRAGMA table_info(ai_players_seed)").fetchall()}
    for col in ai_cols:
        if col not in seed_cols:
            try: c.execute(f"ALTER TABLE ai_players_seed ADD COLUMN {col}")
            except sqlite3.OperationalError: pass

    # [전성기 OVR 보정] 기존 세이브는 peak_ovr 컬럼이 방금 0으로 추가됐거나,
    #  아직 한 번도 update_player(ovr=...)가 안 불려서 현재 ovr보다 낮을 수 있다.
    #  현재 ovr을 하한으로 보정 (peak_ovr < ovr 인 경우만) — 매 시작마다 실행되지만
    #  조건에 안 걸리면 UPDATE 0행이라 사실상 무비용.
    try:
        c.execute("UPDATE my_player SET peak_ovr = ovr WHERE peak_ovr < ovr")
    except sqlite3.OperationalError: pass

    # ─── 성능 인덱스 ───────────────────────────────────────────
    # 매 주차 진행 시 AI 경기 시뮬·순위 집계가 ai_players / match_results를
    # team_id·week·league_id 조건으로 수없이 조회한다. 인덱스가 없으면
    # 매 호출이 전체 테이블 풀스캔(ai_players 2.6만행)이라 한 달 진행에
    # 수천 ms가 걸린다. 아래 인덱스로 호출당 비용을 O(N)→O(log N)로 낮춘다.
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_aiplayers_team   ON ai_players(team_id)",
        "CREATE INDEX IF NOT EXISTS idx_mr_week_season   ON match_results(week, season)",
        "CREATE INDEX IF NOT EXISTS idx_mr_league_season ON match_results(league_id, season)",
        "CREATE INDEX IF NOT EXISTS idx_mr_day_season    ON match_results(day, season)",
        "CREATE INDEX IF NOT EXISTS idx_teams_league     ON teams(league_id)",
        "CREATE INDEX IF NOT EXISTS idx_leagues_country  ON leagues(country_id)",
        # intl/cl 경기 조회: tournament_id+week 복합 (매 주차 process_*_week 호출마다 사용)
        "CREATE INDEX IF NOT EXISTS idx_intl_matches_tid_week ON intl_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_intl_entries_tid      ON intl_entries(tournament_id)",
        "CREATE INDEX IF NOT EXISTS idx_cl_matches_tid_week   ON cl_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_cl_entries_tid        ON cl_entries(tournament_id)",
        "CREATE INDEX IF NOT EXISTS idx_cup_matches_tid_week  ON cup_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_cup_entries_tid       ON cup_entries(tournament_id)",
        # _calc_clean_sheets: season+home_score 로 미완료 경기 필터링
        "CREATE INDEX IF NOT EXISTS idx_mr_season_score ON match_results(season, home_score)",
        # match_results: home/away team_id 조회 (클린시트, 팀 경기 조회)
        "CREATE INDEX IF NOT EXISTS idx_mr_home_team ON match_results(home_team_id, season)",
        "CREATE INDEX IF NOT EXISTS idx_mr_away_team ON match_results(away_team_id, season)",
        # match_results_archive: 역대 순위/우승팀 조회(league_id+season 등치 검색)
        # 전용 — 쓰기는 시즌 전환 시 1회 벌크 INSERT뿐이라 인덱스 개수 부담이 없다.
        "CREATE INDEX IF NOT EXISTS idx_mra_league_season ON match_results_archive(league_id, season)",
        # [2026-07 추가, 신민용 리포트: "연도전환이 갈수록 느려진다"]
        # _generate_all_league_schedules()의 "완비판정조회" 단계가
        # "SELECT league_id, COUNT(*) FROM match_results_archive WHERE
        # season=? GROUP BY league_id"로 순수 season 단일 조건 조회를 하는데,
        # 위 idx_mra_league_season은 league_id가 선두 컬럼이라 이 조회엔
        # 못 쓰이고 매번 아카이브 테이블 전체를 풀스캔했다 — 아카이브가
        # 매년 커지므로 이 단계가 해마다 계속 느려지는 원인이었다.
        # season을 선두로 둔 인덱스를 추가해 O(전체 아카이브) → O(log N)로.
        "CREATE INDEX IF NOT EXISTS idx_mra_season ON match_results_archive(season, league_id)",
        # [2026-07 추가, 신민용 리포트: "월드컵/대륙컵 등 열릴 때 렉이 심하다"]
        # cup_tournaments/intl_tournaments/cwc_tournaments엔 인덱스가 아예
        # 하나도 없었다. cup_engine.get_cup_tournament()의 "SELECT * FROM
        # cup_tournaments WHERE year=? AND country_id=?"가 매 시즌 나라마다
        # (최대 209개국) 호출되는데, 인덱스가 없어 매번 테이블 전체를
        # 풀스캔했다 — cup_tournaments는 시즌마다 국가 수만큼 계속 쌓이는
        # 테이블(실측 9시즌차에 이미 1,881행)이라, 이 풀스캔 비용이 매
        # 시즌 계속 커지는 구조였다(신민용 리포트의 "갈수록 렉이 심해진다"
        # 와 정확히 일치). intl_tournaments/cwc_tournaments도 같은 문제라
        # 함께 인덱스를 추가한다.
        "CREATE INDEX IF NOT EXISTS idx_cup_tournaments_year_country ON cup_tournaments(year, country_id)",
        "CREATE INDEX IF NOT EXISTS idx_cup_tournaments_status ON cup_tournaments(status)",
        "CREATE INDEX IF NOT EXISTS idx_intl_tournaments_year ON intl_tournaments(year)",
        "CREATE INDEX IF NOT EXISTS idx_cwc_tournaments_year ON cwc_tournaments(year)",
        "CREATE INDEX IF NOT EXISTS idx_cwc_matches_tid_week ON cwc_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_cwc_entries_tid ON cwc_entries(tournament_id)",
        # trophy_log: 승강제 처리(_process_promotion_relegation) 안에서
        # "WHERE year=? AND team_name=? AND tier=?"로 우승 중복 체크를 함 —
        # 작은 테이블이지만 저비용으로 미리 인덱싱.
        "CREATE INDEX IF NOT EXISTS idx_trophy_log_year_team ON trophy_log(year, team_name)",
    ]:
        try: c.execute(idx)
        except sqlite3.OperationalError: pass

    conn.commit()
    if not USE_MEMORY_DB:
        # WAL 모드는 DB 파일에 영구 저장되는 설정(디스크 직결 모드에서만 의미 있음).
        # 인메모리 DB는 애초에 디스크 파일이 아니라 WAL 저널이 필요 없어 스킵한다.
        conn.close()
        _conn = sqlite3.connect(DB_PATH, timeout=30)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.close()
        # WAL을 켠 뒤 풀 커넥션을 새로 만들게 리셋(이전 풀 커넥션은 WAL 인식 전일 수 있음).
        reset_conn_pool()
    remap_all_ovr()   # calc_ovr 정규화에 맞춰 기존 AI OVR 일괄 재계산 (1회성)
    migrate_money_to_thousand()   # 금액 단위 만원→천원 전환 (1회성)
    repair_duplicate_season_schedules()   # 유령 중복 시즌 일정 정리 (1회성, 아래 참고)
    repair_stray_intl_is_my_flags()   # 복수국적 미선택국 is_my 오염 정리 (1회성, 아래 참고)
    repair_cwc_match_groups()   # 클럽월드컵 매치 grp 백필 (1회성, 아래 참고)


# [2026-07 최적화, 신민용 리포트: "연도전환 최적화 더 해봐"] match_results엔
# 인덱스가 6개 걸려있는데(아래 리스트), archive_old_seasons()의 벌크 DELETE
# (직전 시즌 17만 행)와 _generate_all_league_schedules()의 벌크 INSERT(새
# 시즌 17만 행)가 항상 연달아 일어난다. 인덱스를 유지한 채 이 두 벌크
# 작업을 하면 매 행마다 6개 인덱스를 갱신해야 해서(SQLite 실측: 두 작업
# 합쳐 아카이브이동 0.58s + INSERT 0.73~0.81s ≈ 1.3~1.4s), 인덱스 유지비용이
# 두 번 청구되는 셈이다. 이 두 작업을 감싸는 동안만 인덱스를 통째로 DROP했다가
# 끝난 뒤 한 번에 CREATE INDEX로 재생성하면, SQLite가 내부적으로 정렬 스캔
# 1회로 인덱스를 만들어(벌크 빌드가 건별 갱신보다 훨씬 빠름) 같은 결과를
# 더 싸게 얻는다. 호출부(game_engine._generate_all_league_schedules)가
# drop→(archive_old_seasons + INSERT)→rebuild 순서로 감싸 쓴다.
MATCH_RESULTS_INDEXES = [
    ("idx_mr_week_season",   "match_results(week, season)"),
    ("idx_mr_league_season", "match_results(league_id, season)"),
    ("idx_mr_day_season",    "match_results(day, season)"),
    ("idx_mr_season_score",  "match_results(season, home_score)"),
    ("idx_mr_home_team",     "match_results(home_team_id, season)"),
    ("idx_mr_away_team",     "match_results(away_team_id, season)"),
]


def drop_match_results_indexes(c):
    """연도전환의 벌크 DELETE+INSERT 구간 동안 match_results 인덱스 6개를
    임시로 제거한다. 반드시 rebuild_match_results_indexes()와 짝으로 써야
    하며, 그 사이 구간에서 match_results를 쿼리하는 코드는 인덱스 없이
    돈다는 점을 감안해야 한다(이 구간에 걸리는 SELECT들은 이미 archive로
    걸러져 테이블이 거의 비어있는 상태라 문제 없음 — 설계 노트 참고)."""
    for name, _ in MATCH_RESULTS_INDEXES:
        try:
            c.execute(f"DROP INDEX IF EXISTS {name}")
        except sqlite3.OperationalError:
            pass


def rebuild_match_results_indexes(c):
    """drop_match_results_indexes()로 제거했던 인덱스 6개를 재생성한다."""
    for name, spec in MATCH_RESULTS_INDEXES:
        try:
            c.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")
        except sqlite3.OperationalError:
            pass


def archive_old_seasons(current_season):
    """[2026-07 신설, 성능] 진행 중인 시즌(current_season) 이전의 모든
    match_results 행을 match_results_archive로 옮긴다.

    새 시즌 일정을 match_results에 INSERT할 때마다 인덱스 6개가 테이블
    전체 크기에 비례해 느려지는데(실측: 3시즌 누적 52만 행 상태에서 새
    시즌 17만 행 삽입에 2.6초, 시즌이 쌓일수록 계속 나빠짐), 완료된 과거
    시즌을 이 아카이브로 옮겨 match_results를 '이번 시즌 것만' 유지하면
    삽입 비용이 시즌 수와 무관하게 항상 일정해진다.

    world_browser의 역대 순위/우승팀 조회(get_league_standings,
    get_league_standings_for_browser, get_league_champions)는 이미 이
    아카이브 테이블도 함께 보도록 수정되어 있으므로, 화면에 보이는 결과는
    바뀌지 않는다 — 데이터 삭제가 아니라 이동이며, id 값도 그대로 보존한다.

    _process_promotion_relegation이 '방금 끝난 시즌'의 match_results를
    이미 다 읽어들인 뒤(그 함수가 이 호출보다 항상 먼저 실행됨) 호출되므로
    안전하다. INSERT OR IGNORE라 중복 호출돼도(예: 재시도) 에러 없이
    멱등하게 동작한다."""
    conn = get_conn()
    c = conn.cursor()
    cols = "id,league_id,week,home_team_id,away_team_id,home_score,away_score,season,year,day"
    c.execute(
        f"""INSERT OR IGNORE INTO match_results_archive({cols})
            SELECT {cols} FROM match_results WHERE season<?""", (current_season,))
    c.execute("DELETE FROM match_results WHERE season<?", (current_season,))
    conn.commit()


def repair_duplicate_season_schedules():
    """[2026-07 버그 수정, 신민용 리포트: "2001년엔 14팀인데 2002년엔
    17팀으로 뜬다"] _generate_all_league_schedules()가 예전엔 완료돼
    아카이브로 넘어간 과거 시즌을 '이번 시즌 것만 있다'고 잘못 가정해
    완비 여부를 판정했다 — 그 버그 때문에 이미 끝난 시즌인데도 다른 팀
    구성으로 새 일정을 통째로 또 깔아버린 적이 있었다(그 원인 자체는
    _generate_all_league_schedules에서 이미 고쳐짐 — 아카이브+라이브
    합산 카운트로 완비 여부를 판정하도록 수정됨). 하지만 그 버그가
    고쳐지기 전에 이미 생겨버린 '유령 일정'(같은 league_id+season에
    결과가 하나도 안 채워진(-1,-1) 채로 남아있는, 실제 팀 구성과 다른
    중복 스케줄 뭉치)은 세이브 파일에 이미 저장돼 있어서, 코드를
    고쳐도 역대 기록 화면의 팀 수 집계는 계속 부풀어 보인다.

    이 함수는 1회성으로 그런 '유령 뭉치'만 정확히 골라 삭제한다:
      - 같은 (league_id, season) 안에서 같은 날짜(day)에 같은 팀이
        두 번 이상 등장하면(정상 라운드로빈이면 불가능) 중복 스케줄이
        겹쳐 있다는 확실한 신호.
      - 그중 결과가 전혀 기록되지 않은(all -1,-1) 쪽과, 팀 구성 자체가
        완료된(결과가 있는) 쪽과 다른 경우에만 유령으로 판정해 삭제한다.
      - 실제로 경기가 하나라도 진행된 데이터, 혹은 팀 구성이 동일해서
        그냥 '진행 중인 정상 시즌'인 경우는 절대 건드리지 않는다.
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        row = c.execute("SELECT value FROM meta WHERE key='dup_season_repair_v1'").fetchone()
    except Exception:
        row = None
    if row:
        conn.close()
        return
    removed_total = 0
    try:
        for table in ("match_results", "match_results_archive"):
            groups = c.execute(
                f"SELECT DISTINCT league_id, season FROM {table} WHERE day IS NOT NULL"
            ).fetchall()
            for g in groups:
                lid, season = g["league_id"], g["season"]
                rows = c.execute(
                    f"SELECT id, day, home_team_id, away_team_id, home_score FROM {table} "
                    f"WHERE league_id=? AND season=? AND day IS NOT NULL",
                    (lid, season)).fetchall()
                if not rows:
                    continue
                day_team_count: dict = {}
                for r in rows:
                    for tid in (r["home_team_id"], r["away_team_id"]):
                        key = (r["day"], tid)
                        day_team_count[key] = day_team_count.get(key, 0) + 1
                if not any(v > 1 for v in day_team_count.values()):
                    continue   # 중복 신호 없음 — 정상 데이터, 스킵
                unplayed_ids = [r["id"] for r in rows if r["home_score"] == -1]
                played_ids   = [r["id"] for r in rows if r["home_score"] != -1]
                if not played_ids or not unplayed_ids:
                    continue   # 한쪽이 아예 없으면 그냥 진행중/미시작 시즌
                played_teams = {t for r in rows if r["home_score"] != -1
                                for t in (r["home_team_id"], r["away_team_id"])}
                unplayed_teams = {t for r in rows if r["home_score"] == -1
                                  for t in (r["home_team_id"], r["away_team_id"])}
                if played_teams == unplayed_teams:
                    continue   # 팀 구성 동일 = 그냥 진행 중인 정상 시즌
                ph = ",".join("?" * len(unplayed_ids))
                c.execute(f"DELETE FROM {table} WHERE id IN ({ph})", unplayed_ids)
                removed_total += len(unplayed_ids)
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('dup_season_repair_v1',?)",
                  (str(removed_total),))
        conn.commit()
        if removed_total:
            print(f"[repair] 중복 유령 시즌 일정 {removed_total}건 정리 완료")
    except Exception as e:
        print("repair_duplicate_season_schedules 실패:", e)
        conn.rollback()
    finally:
        conn.close()


def repair_stray_intl_is_my_flags():
    """[2026-07 버그 수정, 신민용 리포트: "호주/앙골라 복수국적인데 호주를
    선택했는데 커리어에 앙골라 대륙컵 경기도 같이 기록됨"]

    복수국적 대회는 발탁창을 띄우기 전(선택 대기, my_selected=3)에도
    intl_matches.is_my=1이 후보국 경기에 미리 찍혀 있다. 실제 선택 시
    다른 후보 대회는 my_selected=2로 닫히는데, 그 대회의 is_my는 예전
    코드에선 그대로 1로 남았다(원인 자체는 intl_engine.choose_national_team
    / _close_other_pending_when_committed에서 이미 고쳐짐 — 이제 선택
    시점에 is_my도 함께 0으로 정리된다). 하지만 그 버그가 고쳐지기
    전에 이미 선택을 마친 기존 세이브에는 '선택 안 한 나라' 대회의
    is_my=1이 그대로 남아있어, 계속 커리어 로그에 잘못 기록된다.

    이 함수는 1회성으로 my_selected가 1이 아닌(출전 확정 안 된) 대회에
    남아있는 is_my=1을 전부 0으로 되돌린다 — 실제 출전 확정(my_selected=1)
    대회는 절대 건드리지 않는다."""
    conn = get_conn()
    c = conn.cursor()
    try:
        row = c.execute("SELECT value FROM meta WHERE key='intl_ismy_repair_v1'").fetchone()
    except Exception:
        row = None
    if row:
        conn.close()
        return
    removed = 0
    try:
        stray_ids = [r["id"] for r in c.execute(
            """SELECT id FROM intl_tournaments WHERE my_selected != 1"""
        ).fetchall()]
        if stray_ids:
            ph = ",".join("?" * len(stray_ids))
            cur = c.execute(
                f"SELECT COUNT(*) as n FROM intl_matches WHERE tournament_id IN ({ph}) AND is_my=1",
                stray_ids)
            removed = cur.fetchone()["n"]
            c.execute(
                f"UPDATE intl_matches SET is_my=0 WHERE tournament_id IN ({ph}) AND is_my=1",
                stray_ids)
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('intl_ismy_repair_v1',?)",
                  (str(removed),))
        conn.commit()
        if removed:
            print(f"[repair] 복수국적 미선택국 is_my 오염 {removed}건 정리 완료")
    except Exception as e:
        print("repair_stray_intl_is_my_flags 실패:", e)
        conn.rollback()
    finally:
        conn.close()


def repair_cwc_match_groups():
    """[2026-07 버그 수정, 신민용 리포트: "클럽월드컵 경기 일정 여니
    'no such column: grp' 에러"] cwc_matches 테이블에 애초에 grp 컬럼이
    빠져 있었다(cl_matches엔 있었는데 클럽월드컵만 놓침) — 컬럼은 이번에
    ALTER TABLE로 추가했고 새로 생성되는 매치부터는 club_world_cup_engine.py
    가 정상적으로 채운다. 하지만 이 버그가 고쳐지기 전에 이미 생성된
    클럽월드컵 조별리그 매치는 grp가 빈 문자열로 남아있어 조별 순위표/
    일정 화면에서 계속 그룹 구분이 안 된다.

    이 함수는 1회성으로, 이미 grp가 채워져 있는 cwc_entries(팀별 조 배정)를
    기준으로 cwc_matches.grp를 역으로 채운다 — 조별리그 매치는 항상 같은
    조 안에서만 열리므로 home_team_id가 속한 조를 그대로 매치에 옮겨 적으면
    된다."""
    conn = get_conn()
    c = conn.cursor()
    try:
        row = c.execute("SELECT value FROM meta WHERE key='cwc_grp_backfill_v1'").fetchone()
    except Exception:
        row = None
    if row:
        conn.close()
        return
    fixed = 0
    try:
        blank_tids = [r["tournament_id"] for r in c.execute(
            """SELECT DISTINCT tournament_id FROM cwc_matches
               WHERE stage='group' AND (grp IS NULL OR grp='')""").fetchall()]
        for tid in blank_tids:
            entry_grp = {r["team_id"]: r["grp"] for r in c.execute(
                "SELECT team_id, grp FROM cwc_entries WHERE tournament_id=?", (tid,)).fetchall()}
            rows = c.execute(
                """SELECT id, home_team_id FROM cwc_matches
                   WHERE tournament_id=? AND stage='group' AND (grp IS NULL OR grp='')""",
                (tid,)).fetchall()
            for r in rows:
                g = entry_grp.get(r["home_team_id"])
                if g:
                    c.execute("UPDATE cwc_matches SET grp=? WHERE id=?", (g, r["id"]))
                    fixed += 1
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('cwc_grp_backfill_v1',?)",
                  (str(fixed),))
        conn.commit()
        if fixed:
            print(f"[repair] 클럽월드컵 매치 grp 백필 {fixed}건 완료")
    except Exception as e:
        print("repair_cwc_match_groups 실패:", e)
        conn.rollback()
    finally:
        conn.close()


def remap_all_ovr():
    """calc_ovr 정규화(÷sum) 변경에 맞춰 기존 ai_players OVR을 전부 재계산.
    meta 플래그로 1회만 실행."""
    conn = get_conn(); c = conn.cursor()
    try:
        row = c.execute("SELECT value FROM meta WHERE key='ovr_remapped_v2'").fetchone()
    except Exception:
        row = None
    if row:
        conn.close(); return
    try:
        rows = c.execute(
            "SELECT id, position, " + ",".join(ALL_STATS) + " FROM ai_players"
        ).fetchall()
        # [최적화] 행마다 execute()를 개별 호출하던 것을 executemany()로 배치
        #  처리. 1회성 마이그레이션이지만 ai_players가 2.6만+ 행이라 배치로
        #  묶으면 첫 실행 시 버벅임을 줄일 수 있다. 계산 결과(new_ovr)는 동일.
        updates = [
            (calc_ovr(r["position"], {s: r[s] for s in ALL_STATS}), r["id"])
            for r in rows
        ]
        c.executemany("UPDATE ai_players SET ovr=? WHERE id=?", updates)
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('ovr_remapped_v2','1')")
        conn.commit()
        # ai_players OVR이 바뀌었으므로 엔진의 팀 평균 OVR 캐시를 비운다.
        try:
            import game_engine
            game_engine._invalidate_team_ovr_cache()
        except Exception:
            pass
    except Exception as e:
        print("remap_all_ovr 실패:", e)
    finally:
        conn.close()


def migrate_money_to_thousand():
    """금액 저장 단위를 만원→천원(×10)으로 일괄 전환. meta 플래그로 1회만.
    기존 세이브의 salary/total_assets/total_earnings 및 커리어 salary를 보정."""
    conn = get_conn(); c = conn.cursor()
    try:
        row = c.execute("SELECT value FROM meta WHERE key='money_unit_thousand'").fetchone()
    except Exception:
        row = None
    if row:
        conn.close(); return
    try:
        # my_player 금액 컬럼
        c.execute("""UPDATE my_player SET
                        salary = salary * 10,
                        total_assets = total_assets * 10,
                        total_earnings = total_earnings * 10
                     WHERE id = 1""")
        # 커리어 기록의 연봉
        c.execute("UPDATE career_entries SET salary = salary * 10")
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('money_unit_thousand','1')")
        conn.commit()
    except Exception as e:
        print("migrate_money_to_thousand 실패:", e)
    finally:
        conn.close()


def seed_initial_data(progress_cb=None):
    """progress_cb(stage:str, done:int, total:int, detail:str)로 진행 상황을 알려준다.
    콜백이 없으면(None) 기존과 완전히 동일하게 동작 — 하위호환."""
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT value FROM meta WHERE key='seeded'")
    if c.fetchone(): conn.close(); return
    print("초기 데이터 삽입 중...")

    def _stage(name, total):
        if progress_cb: progress_cb(name, 0, total, "")

    _stage("국가 정보 생성", 1)
    _insert_countries(c)
    if progress_cb: progress_cb("국가 정보 생성", 1, 1, "")

    _stage("리그·팀 생성", len(LEAGUE_DATA))
    _insert_leagues_and_teams(
        c, progress_cb=(lambda d, t, name: progress_cb("리그·팀 생성", d, t, name)) if progress_cb else None)

    _stage("선수 이름 데이터 로딩", 1)
    _insert_player_names(c)
    if progress_cb: progress_cb("선수 이름 데이터 로딩", 1, 1, "")

    # 팀 수를 미리 세어 진행률 total로 사용 (실제 처리 순서/개수와 100% 동일)
    _team_total = c.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    _stage("전세계 선수단 생성", _team_total)
    _generate_all_ai_players(
        c, progress_cb=(lambda d, t, name: progress_cb("전세계 선수단 생성", d, t, name)) if progress_cb else None)

    # [버그수정] 최초 시딩 직후(변형되기 전) ai_players 상태를 스냅샷으로 보관.
    # reset_game_data()가 이걸로 벌크 복원한다 — teams가 LEAGUE_DATA 원본으로
    # 결정론적으로 돌아가는 것과 동일하게, 선수단도 "최초 시딩 상태로 결정론적
    # 복귀"가 되도록 통일.
    ai_cols = [r["name"] for r in c.execute("PRAGMA table_info(ai_players)").fetchall()]
    col_list = ", ".join(ai_cols)
    c.execute(f"DELETE FROM ai_players_seed")
    c.execute(f"INSERT INTO ai_players_seed({col_list}) SELECT {col_list} FROM ai_players")
    c.execute("INSERT INTO meta VALUES('seeded','1')")
    conn.commit(); conn.close()
    print("완료")

def _reset_teams_to_league_data(c):
    """[버그수정] '새 게임'을 눌러도 teams.league_id/current_tier가
    이전 플레이의 승강 결과 그대로 남아있던 문제를 고친다.
    reset_game_data()는 promotion_log 등 시즌 기록은 싹 지우면서도
    teams 테이블 자체(league_id, current_tier)는 건드리지 않았기 때문에,
    이전 판에서 승격/강등된 팀이 새 판에서도 엉뚱한 리그에서 시작했다.
    이 함수는 LEAGUE_DATA(leagues.py) 원본 배치를 기준으로 모든 팀의
    league_id/current_tier를 되돌린다. 팀 id(및 그에 딸린 선수단 등)는
    그대로 유지한 채 소속 리그 정보만 바로잡으므로 안전하다.

    [버그수정 2026-07, 신민용 리포트] 팀명이 같은 나라 안에서 중복되면
    (예: 데이터 오류로 '전북 현대 모터스'가 K리그1과 K3리그 양쪽에 잘못
    들어간 경우) (country_id, team_name)을 키로 쓰는 딕셔너리에서 나중에
    처리된 등급의 값이 먼저 값을 덮어써버려, 두 팀(서로 다른 team_id)이
    전부 같은(나중 값) 리그로 리셋되는 버그가 있었다 — 그 결과 한쪽 등급은
    팀이 하나 모자라고 다른 쪽은 하나 남는 현상이 생겼다(실측: 1부 11개/
    3부 15개, 원래는 12개/14개여야 함). 이제 이름이 같은 팀들을 id 오름차순
    으로 모아서, LEAGUE_DATA에 그 이름이 등장하는 순서와 1:1로 매칭한다 —
    이름 중복이 있어도 각 team_id가 자기 몫의 등급으로 정확히 돌아간다."""
    c.execute("SELECT id, name FROM countries")
    cid_by_name = {r["name"]: r["id"] for r in c.fetchall()}
    c.execute("SELECT id, country_id, tier FROM leagues")
    league_id_by_country_tier = {(r["country_id"], r["tier"]): r["id"] for r in c.fetchall()}

    # (country_id, team_name) -> [(league_id, tier), ...] — 이름이 중복되면
    # 리스트에 여러 항목이 쌓인다(등장 순서 그대로).
    target_lists = {}
    for country_name, tiers in LEAGUE_DATA.items():
        cid = cid_by_name.get(country_name)
        if cid is None:
            continue
        for tier_key, (league_name, team_names) in tiers.items():
            tier = _tier_to_int(tier_key)
            lid = league_id_by_country_tier.get((cid, tier))
            if lid is None:
                continue
            for team_name in team_names:
                target_lists.setdefault((cid, team_name), []).append((lid, tier))

    # DB의 팀들을 (country_id, name)별로 id 오름차순 그룹핑 — 최초 시딩 시
    # INSERT 순서(=LEAGUE_DATA 등장 순서)와 id 오름차순이 일치하므로, 이렇게
    # 모으면 이름이 중복돼도 각 team_id가 자기 원래 등급과 정확히 짝지어진다.
    c.execute("SELECT id, name, country_id FROM teams ORDER BY id")
    grouped = {}
    for r in c.fetchall():
        grouped.setdefault((r["country_id"], r["name"]), []).append(r["id"])

    updates = []
    for key, team_ids in grouped.items():
        dests = target_lists.get(key)
        if not dests:
            continue
        # 팀 추가/삭제로 개수가 어긋나는 예외적인 경우를 대비해 짧은 쪽 기준으로.
        n = min(len(team_ids), len(dests))
        for i in range(n):
            lid, tier = dests[i]
            updates.append((lid, tier, team_ids[i]))
    c.executemany("UPDATE teams SET league_id=?, current_tier=? WHERE id=?", updates)


def _reset_ai_players_from_seed(c):
    """[버그수정] '새 게임' 시 ai_players를 최초 시딩 상태로 벌크 복원.
    개별 재생성(_generate_all_ai_players, 5.9만 명 개별 INSERT + 팀당
    RANDOM() 조회) 대신, 미리 떠둔 스냅샷을 DELETE+INSERT SELECT 두 문장으로
    복사만 한다 — 랜덤 재계산이 없어 사실상 즉시 끝난다(인메모리 DB라 더더욱).
    [구버전 세이브 폴백] ai_players_seed가 비어있으면(이 패치 이전에 만든
    세이브 — 스냅샷을 못 떠둔 상태) 복원할 데이터가 없으므로, 지금의
    ai_players 상태를 그대로 시드로 확정해둔다. 그 판의 '새 게임'은
    1회에 한해 기존 동작(리셋 안 됨)과 같지만, 그 다음 '새 게임'부터는
    정상적으로 이번에 확정된 시드로 복원된다."""
    seed_cnt = c.execute("SELECT COUNT(*) c FROM ai_players_seed").fetchone()["c"]
    ai_cols = [r["name"] for r in c.execute("PRAGMA table_info(ai_players)").fetchall()]
    col_list = ", ".join(ai_cols)
    if seed_cnt == 0:
        c.execute(f"INSERT INTO ai_players_seed({col_list}) SELECT {col_list} FROM ai_players")
        return
    c.execute("DELETE FROM ai_players")
    c.execute(f"INSERT INTO ai_players({col_list}) SELECT {col_list} FROM ai_players_seed")


def reset_game_data():
    init_db()  # 마이그레이션 적용
    conn = get_conn(); c = conn.cursor()
    for t in ["my_player","career_entries","promotion_log","trophy_log","awards",
              "game_log","match_results","match_details","season_state",
              "intl_history","intl_tournaments","intl_entries","intl_matches",
              "cl_tournaments","cl_entries","cl_matches","cl_history",
              "cwc_tournaments","cwc_entries","cwc_matches",
              "cup_tournaments","cup_entries","cup_matches","cup_history"]:
        c.execute(f"DELETE FROM {t}")
    c.execute("UPDATE teams SET wins=0,draws=0,losses=0,goals_for=0,goals_against=0")
    _reset_teams_to_league_data(c)
    _reset_ai_players_from_seed(c)
    conn.commit(); conn.close()

# ─── OVR 가중치 ───────────────────────────────────────────────
WEIGHTS = {
    "GK":  dict(stamina=8,speed=3,jump=10,strength=4,shooting=1,passing=3,dribbling=1,tackling=2,heading=3,positioning=15,setpiece=2,mental=8,confidence=5,leadership=5,concentration=15),
    "CB":  dict(stamina=8,speed=5,jump=10,strength=12,shooting=1,passing=5,dribbling=2,tackling=15,heading=12,positioning=10,setpiece=3,mental=5,confidence=5,leadership=5,concentration=10),
    "LB":  dict(stamina=8,speed=10,jump=3,strength=6,shooting=1,passing=8,dribbling=5,tackling=12,heading=5,positioning=8,setpiece=3,mental=5,confidence=5,leadership=5,concentration=8),
    "RB":  dict(stamina=8,speed=10,jump=3,strength=6,shooting=1,passing=8,dribbling=5,tackling=12,heading=5,positioning=8,setpiece=3,mental=5,confidence=5,leadership=5,concentration=8),
    "CDM": dict(stamina=8,speed=3,jump=3,strength=10,shooting=2,passing=8,dribbling=3,tackling=15,heading=5,positioning=12,setpiece=3,mental=8,confidence=5,leadership=5,concentration=10),
    "CM":  dict(stamina=8,speed=5,jump=3,strength=6,shooting=5,passing=12,dribbling=8,tackling=8,heading=3,positioning=10,setpiece=5,mental=5,confidence=5,leadership=5,concentration=8),
    "CAM": dict(stamina=5,speed=5,jump=3,strength=4,shooting=10,passing=12,dribbling=10,tackling=3,heading=3,positioning=12,setpiece=8,mental=5,confidence=5,leadership=5,concentration=8),
    "LW":  dict(stamina=5,speed=12,jump=3,strength=3,shooting=10,passing=8,dribbling=12,tackling=0,heading=3,positioning=10,setpiece=5,mental=5,confidence=5,leadership=5,concentration=8),
    "RW":  dict(stamina=5,speed=12,jump=3,strength=3,shooting=10,passing=8,dribbling=12,tackling=0,heading=3,positioning=10,setpiece=5,mental=5,confidence=5,leadership=5,concentration=8),
    "CF":  dict(stamina=5,speed=8,jump=8,strength=8,shooting=12,passing=10,dribbling=10,tackling=0,heading=10,positioning=10,setpiece=5,mental=5,confidence=5,leadership=3,concentration=8),
    "ST":  dict(stamina=5,speed=10,jump=10,strength=10,shooting=15,passing=3,dribbling=5,tackling=0,heading=15,positioning=13,setpiece=5,mental=5,confidence=5,leadership=3,concentration=8),
}
ALL_STATS = ["stamina","speed","jump","strength","shooting","passing","dribbling",
             "tackling","heading","positioning","setpiece",
             "mental","confidence","leadership","concentration"]

# 포지션별 가중치 합은 상수 → 1회만 계산해 재사용(calc_ovr 핫루프 분모 재계산 제거).
_WEIGHT_SUMS = {pos: sum(w.values()) for pos, w in WEIGHTS.items()}
# [최적화] w.items()를 튜플로 1회만 캐싱 → calc_ovr 핫루프(AI 5.9만명 시즌마다 호출)에서
# 매번 제너레이터+dict.get() 이중 호출을 하던 것을 단순 for문 + 단일 get()으로 대체.
# (동일 입력에 대해 완전히 동일한 결과를 반환함 — 순수 계산 방식만 최적화, 로직/수치 변경 없음)
_WEIGHT_ITEMS = {pos: tuple(w.items()) for pos, w in WEIGHTS.items()}
# ALL_STATS 이름→인덱스 맵 (리스트 기반 고속 경로용)
STAT_IDX = {s: i for i, s in enumerate(ALL_STATS)}
# [최적화] w를 (인덱스, 가중치) 튜플로도 캐싱 → calc_ovr_from_list에서 이름 조회 없이 처리.
_WEIGHT_IDX_ITEMS = {pos: tuple((STAT_IDX[s], wt) for s, wt in w.items())
                     for pos, w in WEIGHTS.items()}

def calc_ovr(position, stats):
    items = _WEIGHT_ITEMS.get(position, _WEIGHT_ITEMS["CM"])
    wsum = _WEIGHT_SUMS.get(position, _WEIGHT_SUMS["CM"])
    g = stats.get
    total = 0
    for s, wt in items:
        total += g(s, 40) * wt
    total /= wsum
    return min(100, max(1, int(round(total))))


def calc_ovr_from_list(position, vals):
    """calc_ovr과 완전히 동일한 공식/결과를, dict 대신 ALL_STATS 순서의
    리스트(vals)를 직접 받아 계산한다 (dict 생성/조회 비용 제거).
    vals는 반드시 ALL_STATS와 같은 순서·같은 길이여야 하며 값이 이미 채워져
    있어야 한다(=원래 calc_ovr의 stats.get(s,40) 기본값이 필요 없는 경우 전용).
    핫루프(ai_lifecycle의 5.9만 AI 선수 시즌 처리) 전용 내부 함수."""
    items = _WEIGHT_IDX_ITEMS.get(position, _WEIGHT_IDX_ITEMS["CM"])
    wsum = _WEIGHT_SUMS.get(position, _WEIGHT_SUMS["CM"])
    total = 0
    for idx, wt in items:
        total += vals[idx] * wt
    total /= wsum
    return min(100, max(1, int(round(total))))


def get_league_avg_ovr(league_id, conn=None, exclude_team_id=None):
    """해당 리그 소속 ai_players 전체의 평균 OVR. 경기 데이터 무관, 명단 기준.
    exclude_team_id: 이 팀은 평균 계산에서 제외 (승강 직후 목표치 산정용)."""
    own = False
    if conn is None:
        conn = get_conn(); own = True
    try:
        if exclude_team_id:
            row = conn.execute(
                """SELECT AVG(ap.ovr) AS v FROM ai_players ap
                   JOIN teams t ON ap.team_id=t.id
                   WHERE t.league_id=? AND t.id!=?""",
                (league_id, exclude_team_id)).fetchone()
        else:
            row = conn.execute(
                """SELECT AVG(ap.ovr) AS v FROM ai_players ap
                   JOIN teams t ON ap.team_id=t.id WHERE t.league_id=?""",
                (league_id,)).fetchone()
        return float(row["v"]) if row and row["v"] is not None else None
    finally:
        if own:
            conn.close()


def get_league_strong_ovr(league_id, pct=0.75, conn=None, exclude_team_id=None):
    """리그 '상위권' 팀 평균 OVR 추정치.
    팀별 평균 OVR을 구해 정렬한 뒤, 상위 분위(pct)에 해당하는 값을 반환한다.
    exclude_team_id: 이 팀은 계산에서 제외 (강등팀 본인 제외용)."""
    own = False
    if conn is None:
        conn = get_conn(); own = True
    try:
        if exclude_team_id:
            rows = conn.execute(
                """SELECT t.id AS tid, AVG(ap.ovr) AS v FROM teams t
                   JOIN ai_players ap ON ap.team_id=t.id
                   WHERE t.league_id=? AND t.id!=?
                   GROUP BY t.id HAVING v IS NOT NULL""",
                (league_id, exclude_team_id)).fetchall()
        else:
            rows = conn.execute(
                """SELECT t.id AS tid, AVG(ap.ovr) AS v FROM teams t
                   JOIN ai_players ap ON ap.team_id=t.id
                   WHERE t.league_id=? GROUP BY t.id HAVING v IS NOT NULL""",
                (league_id,)).fetchall()
        vals = sorted(r["v"] for r in rows)
        if not vals:
            return None
        # pct 분위(상위권). 예: pct=0.75 → 상위 25% 지점 팀 평균.
        idx = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * pct))))
        return float(vals[idx])
    finally:
        if own:
            conn.close()


def rescale_team_to_target_ovr(team_id, target_ovr, conn=None):
    """팀 소속 ai_players 전원의 능력치를 동일 델타로 평행이동시켜
    팀 평균 OVR을 target_ovr 부근으로 맞춘다.

    - 모든 스탯에 같은 정수 델타를 더하므로 선수 간 강약·개성(분포)은 유지된다.
    - 각 스탯은 1~99로 클램프, OVR은 calc_ovr로 재계산해 저장.
    - 승격: 2부 명단을 1부 평균까지 끌어올림. 강등: 새 리그 상위권으로 조정.
    - [중요] 대상은 ai_players 뿐. 플레이어 본인(my_player 테이블)은
      구조적으로 분리되어 있어 절대 변경되지 않는다(내 팀 승격 시 동료 AI만 강화).

    반환: (적용된 delta:int, before_avg:float, after_avg:float) — 변경 없으면 delta=0.
    """
    own = False
    if conn is None:
        conn = get_conn(); own = True
    try:
        rows = conn.execute(
            "SELECT * FROM ai_players WHERE team_id=?", (team_id,)).fetchall()
        if not rows:
            return (0, 0.0, 0.0)

        before_avg = sum(r["ovr"] for r in rows) / len(rows)
        gap = target_ovr - before_avg
        # 평균 OVR 차이 ≈ 스탯 평행이동량. 소수점 반올림해 정수 델타로.
        delta = int(round(gap))
        if delta == 0:
            return (0, before_avg, before_avg)

        # [최적화] 선수마다 개별 execute() 대신 executemany()로 일괄 UPDATE.
        # 승강 시즌 전환 시 이 함수가 팀 수십~수백 개에 대해 호출되므로
        # (팀당 25~30명) 개별 쿼리 누적 시 수천 건까지 늘어날 수 있었음.
        # 계산 로직과 결과값은 기존과 완전히 동일 — 배치 방식만 바뀜.
        update_rows = []
        ovr_sum = 0
        for r in rows:
            new_stats = {}
            for s in ALL_STATS:
                new_stats[s] = min(99, max(1, int(r[s]) + delta))
            new_ovr = calc_ovr(r["position"], new_stats)
            ovr_sum += new_ovr
            update_rows.append((
                new_stats["stamina"], new_stats["speed"], new_stats["jump"],
                new_stats["strength"], new_stats["shooting"], new_stats["passing"],
                new_stats["dribbling"], new_stats["tackling"], new_stats["heading"],
                new_stats["positioning"], new_stats["setpiece"], new_stats["mental"],
                new_stats["confidence"], new_stats["leadership"],
                new_stats["concentration"], new_ovr, r["id"]))

        conn.executemany(
            """UPDATE ai_players SET
               stamina=?,speed=?,jump=?,strength=?,shooting=?,passing=?,
               dribbling=?,tackling=?,heading=?,positioning=?,setpiece=?,
               mental=?,confidence=?,leadership=?,concentration=?,ovr=?
               WHERE id=?""", update_rows)
        if own:
            conn.commit()

        # [최적화] 방금 계산해 저장한 new_ovr 합계로 after_avg를 바로 구해
        # 추가 SELECT(AVG) 왕복을 없앰. DB에 저장된 값과 동일하므로 결과는 같음.
        after_avg = ovr_sum / len(update_rows) if update_rows else before_avg
        return (delta, before_avg, after_avg)
    finally:
        if own:
            conn.close()


def rescale_teams_to_target_ovr_batch(jobs, conn=None):
    """rescale_team_to_target_ovr을 여러 팀에 대해 한 번에 처리하는 배치 버전.

    [최적화 배경] 승강제 시즌 전환 시 이동한 팀 수만큼(리그 수가 많은 세이브에선
    실측 1,000팀 이상) rescale_team_to_target_ovr이 팀마다 개별
    "SELECT * FROM ai_players WHERE team_id=?"를 날렸다 — 계산 자체는 가볍지만
    쿼리 왕복 횟수가 팀 수만큼 쌓여 시즌 전환 지연의 한 축이었다(실측 약 0.4초/
    1,308팀). 이 함수는 대상 팀 전체를 "team_id IN (...)" 단 1회 SELECT로 읽어
    파이썬에서 팀별로 묶은 뒤, 계산은 원본과 완전히 동일한 로직으로 수행하고
    UPDATE도 전체를 단 1회 executemany로 모아 실행한다 — 결과값·판정 로직은
    rescale_team_to_target_ovr과 100% 동일, 쿼리 횟수만 팀 수 → 1회로 감소.

    jobs: [(team_id, target_ovr), ...]
    반환: {team_id: (delta, before_avg, after_avg)} — 팀에 선수가 없으면 항목 생략.
    """
    if not jobs:
        return {}
    own = False
    if conn is None:
        conn = get_conn(); own = True
    try:
        team_ids = [tid for tid, _ in jobs]
        placeholders = ",".join("?" * len(team_ids))
        rows = conn.execute(
            f"SELECT * FROM ai_players WHERE team_id IN ({placeholders})",
            team_ids).fetchall()

        by_team: dict = {}
        for r in rows:
            by_team.setdefault(r["team_id"], []).append(r)

        results: dict = {}
        update_rows = []   # 전체 팀 통합 executemany용
        for team_id, target_ovr in jobs:
            team_rows = by_team.get(team_id)
            if not team_rows:
                continue

            before_avg = sum(r["ovr"] for r in team_rows) / len(team_rows)
            gap = target_ovr - before_avg
            delta = int(round(gap))
            if delta == 0:
                results[team_id] = (0, before_avg, before_avg)
                continue

            ovr_sum = 0
            for r in team_rows:
                new_stats = {}
                for s in ALL_STATS:
                    new_stats[s] = min(99, max(1, int(r[s]) + delta))
                new_ovr = calc_ovr(r["position"], new_stats)
                ovr_sum += new_ovr
                update_rows.append((
                    new_stats["stamina"], new_stats["speed"], new_stats["jump"],
                    new_stats["strength"], new_stats["shooting"], new_stats["passing"],
                    new_stats["dribbling"], new_stats["tackling"], new_stats["heading"],
                    new_stats["positioning"], new_stats["setpiece"], new_stats["mental"],
                    new_stats["confidence"], new_stats["leadership"],
                    new_stats["concentration"], new_ovr, r["id"]))
            after_avg = ovr_sum / len(team_rows)
            results[team_id] = (delta, before_avg, after_avg)

        if update_rows:
            conn.executemany(
                """UPDATE ai_players SET
                   stamina=?,speed=?,jump=?,strength=?,shooting=?,passing=?,
                   dribbling=?,tackling=?,heading=?,positioning=?,setpiece=?,
                   mental=?,confidence=?,leadership=?,concentration=?,ovr=?
                   WHERE id=?""", update_rows)
        if own:
            conn.commit()
        return results
    finally:
        if own:
            conn.close()


# ─── 국가 데이터 (등급 자동 산정: fifa_rank 기준) ─────────────
def _grade_from_rank(rank):
    if rank <= 10: return "S"
    if rank <= 25: return "A"
    if rank <= 50: return "B"
    if rank <= 80: return "C"
    if rank <= 120: return "D"
    if rank <= 160: return "E"
    return "F"


def _insert_countries(c):
    # [최적화] 국가 수만큼 개별 execute() → executemany() 1회. 신규 게임
    # 생성(1회성) 시 초기 로딩 시간을 줄여준다. 삽입 데이터·순서는 동일.
    rows = [(name, flag, cont, lang, rank, _grade_from_rank(rank))
            for (name, flag, cont, lang, rank) in COUNTRY_DATA]
    c.executemany(
        "INSERT INTO countries(name,flag,continent,language,fifa_rank,grade) VALUES(?,?,?,?,?,?)",
        rows)


def sync_countries():
    """COUNTRY_DATA 기준 동기화 (멱등, 매 실행 시 호출).
    - 신규 국가: INSERT (LEAGUE_DATA에 없으면 자동으로 '이름만 국가')
    - 기존 국가: fifa_rank/grade/flag/continent/language 갱신
    기존 세이브에도 새 국가가 반영되도록 seed 가드 바깥에서 실행."""
    conn = get_conn(); c = conn.cursor()
    # [최적화] 국가마다 SELECT 1회씩(존재 확인) 날리던 것을 없애고,
    # 기존 국가명→id를 1회 SELECT로 미리 읽어 메모리에서 분기.
    # UPDATE/INSERT 묶음은 각각 executemany()로 일괄 처리 — 결과는 기존과 동일.
    existing = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM countries").fetchall()}
    to_update = []
    to_insert = []
    for (name, flag, cont, lang, rank) in COUNTRY_DATA:
        grade = _grade_from_rank(rank)
        if name in existing:
            to_update.append((flag, cont, lang, rank, grade, existing[name]))
        else:
            to_insert.append((name, flag, cont, lang, rank, grade))
    if to_update:
        c.executemany(
            """UPDATE countries SET flag=?, continent=?, language=?,
               fifa_rank=?, grade=? WHERE id=?""", to_update)
    if to_insert:
        c.executemany(
            """INSERT INTO countries(name,flag,continent,language,fifa_rank,grade)
               VALUES(?,?,?,?,?,?)""", to_insert)
    conn.commit(); conn.close()


# ─── 리그/팀 데이터 ───────────────────────────────────────────

FORMATIONS = ["4-4-2","4-3-3","3-5-2","4-2-3-1","5-3-2","4-1-4-1","3-4-3"]

def _tier_to_int(tier):
    """LEAGUE_DATA의 tier 키를 정수로 정규화.
    기존 국가는 1/2/3 (int), 신규 국가는 '1부'/'2부'/'3부' (str)로 섞여 있다.
    '1部'(한자) 같은 오타도 방어적으로 흡수한다.
    챔스 출전팀 선발·승강 로직이 모두 tier=1(정수)로 조회하므로 반드시 정수여야 한다."""
    if isinstance(tier, int):
        return tier
    s = str(tier).strip()
    for n in ("1", "2", "3", "4", "5"):
        if s.startswith(n):
            return int(n)
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else 1


def _insert_leagues_and_teams(c, progress_cb=None):
    # [최적화] 리그 INSERT는 lastrowid가 필요해 개별 execute()를 유지하되,
    # 그 리그 소속 팀들은 executemany()로 한 번에 넣는다(기존: 팀마다 execute()).
    # 삽입 순서·데이터·formation 랜덤 선택 순서는 원본과 동일하게 유지.
    c.execute("SELECT id, name FROM countries")
    cmap = {r["name"]: r["id"] for r in c.fetchall()}
    _total = len(LEAGUE_DATA)
    for _i, (country_name, tiers) in enumerate(LEAGUE_DATA.items(), 1):
        cid = cmap.get(country_name)
        if cid is None:
            if progress_cb: progress_cb(_i, _total, country_name)
            continue
        for tier_key, (league_name, teams) in tiers.items():
            tier = _tier_to_int(tier_key)
            c.execute("INSERT INTO leagues(country_id,tier,name) VALUES(?,?,?)",
                      (cid, tier, league_name))
            lid = c.lastrowid
            team_rows = [(lid, cid, team_name, random.choice(FORMATIONS), tier)
                         for team_name in teams]
            if team_rows:
                c.executemany(
                    "INSERT INTO teams(league_id,country_id,name,formation,current_tier) VALUES(?,?,?,?,?)",
                    team_rows)
        if progress_cb: progress_cb(_i, _total, country_name)


# ─── 이름 데이터 ──────────────────────────────────────────────
def _clean(n):
    # "이름(Romanization)" 형태에서 앞부분만 추출
    return n.split("(")[0].strip()



def _insert_player_names(c):
    # [최적화] 이름 수만큼(수만 건) 개별 execute() → executemany() 1회.
    # 신규 게임 생성 시 초기 로딩 지연의 큰 비중을 차지하던 부분.
    c.execute("SELECT id, name FROM countries")
    cmap = {r["name"]: r["id"] for r in c.fetchall()}
    rows = []
    for country, names in NAME_DATA.items():
        cid = cmap.get(country)
        if cid is None: continue
        for n in names:
            clean = _clean(n)
            if clean:
                rows.append((cid, clean))
    if rows:
        c.executemany("INSERT INTO player_names(country_id,name) VALUES(?,?)", rows)


# ─── AI 선수 국적 배정 (2026-07 신설, 신민용 확정) ──────────────────
# 실제 축구처럼 리그마다 외국인 비율이 다르고(EPL은 외국인 많고 하위
# 리그일수록 자국 위주), 포지션별로도 다르며(공격수는 해외 스카우팅이
# 많고 GK는 자국 선호), 일부 아시아 리그는 외국인 등록 인원 자체를
# 제한한다(K리그 등). 월드컵 골든볼처럼 "실제 선수" 기반 국가대표 상을
# 만들기 위한 선행 작업.
DOMESTIC_PROB_BY_GRADE = {
    "SS": 0.45, "S": 0.55, "A": 0.70, "B": 0.80,
    "C": 0.88, "D": 0.93, "E": 0.96, "F": 0.98,
}
POS_FOREIGN_MULT = {
    "GK": 0.6, "CB": 0.8, "LB": 0.9, "RB": 0.9,
    "CDM": 1.0, "CM": 1.0, "CAM": 1.1,
    "LW": 1.2, "RW": 1.2, "CF": 1.2, "ST": 1.3,
}
# 팀당 외국인 등록 상한 (없는 나라는 무제한 — 확률만 적용).
FOREIGN_QUOTA_CAP = {"대한민국": 4, "일본": 5, "중국": 5, "카타르": 4, "사우디아라비아": 8}
# 스타 슬롯(월드클래스/엘리트)이 해외 출신일 때 우선적으로 뽑히는
# "축구 수출 강국" — 실제로 빅클럽 스타 영입은 이 나라들 출신이 압도적.
FOOTBALL_POWERHOUSES = ["브라질", "아르헨티나", "프랑스", "잉글랜드", "스페인",
                        "독일", "포르투갈", "네덜란드"]

_COUNTRY_CONTINENT = {}   # 나라명 -> 대륙 (지연 초기화)
_CONTINENT_COUNTRIES = {}  # 대륙 -> [(나라명, fifa_rank), ...] fifa_rank 오름차순(강한 순)


def get_country_squad_players(country, positions=None, min_count=8):
    """[2026-07 신설, 신민용 지적: "8명 미만인 나라는 자국 1부나 남의 나라
    2부에서도 채울 수 있지 않나 — 실제 카보베르데 키퍼가 터키 2부"]
    국적 태그된 선수만으론 소국의 스쿼드가 너무 얇을 수 있어서 3단계로
    폭을 넓힌다:
      1) nationality=country인 선수 (포지션별 최고 OVR)
      2) 그래도 부족하면: country의 자국 리그 소속 팀 선수를 국적 태그와
         무관하게 채움 (자국 리그 뛰는 선수는 사실상 그 나라 국적일
         가능성이 높다는 전제 — 애초에 국적 배정 자체가 자국 비율이
         높게 설계돼 있어서 태그 누락분을 보정하는 성격)
      3) 그래도 부족하면(자국 리그 자체가 게임에 없는 나라): 다른 나라
         2부 이하 리그에서 대륙 우선 → 전체 순으로 채움("해외 하위리그
         진출" 실제 패턴 반영)
    반환: 포지션 슬롯 순서(positions 인자 순서)대로 채워진 선수 dict 리스트
    (부족하면 그만큼 짧게 반환 — 호출부가 len()으로 판단)."""
    positions = positions or ["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
    conn = get_conn()
    slots = [None] * len(positions)
    used_ids: set = set()

    def _fill(where_sql, params, randomize=False):
        for i, pos in enumerate(positions):
            if slots[i] is not None:
                continue
            ph = ",".join(str(x) for x in used_ids) or "0"
            order_by = "RANDOM()" if randomize else "ap.ovr DESC"
            row = conn.execute(
                f"""SELECT ap.id, ap.name, ap.position, ap.ovr, t.name AS club
                    FROM ai_players ap JOIN teams t ON ap.team_id=t.id
                    JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id
                    WHERE {where_sql} AND ap.position=? AND ap.id NOT IN ({ph})
                    ORDER BY {order_by} LIMIT 1""",
                (*params, pos)).fetchone()
            if row:
                slots[i] = dict(row)
                used_ids.add(row["id"])

    _fill("ap.nationality=?", (country,))
    if sum(1 for s in slots if s) < min_count:
        _fill("cn.name=?", (country,))
    if sum(1 for s in slots if s) < min_count:
        _init_nationality_tables()
        cont = _COUNTRY_CONTINENT.get(country, "")
        _fill("t.current_tier>=2 AND cn.continent=? AND cn.name!=?", (cont, country), randomize=True)
    if sum(1 for s in slots if s) < min_count:
        _fill("t.current_tier>=2 AND cn.name!=?", (country,), randomize=True)
    conn.close()
    return [s for s in slots if s]


def _init_nationality_tables():
    if _COUNTRY_CONTINENT:
        return
    by_cont = {}
    for name, _flag, cont, _lang, rank in COUNTRY_DATA:
        _COUNTRY_CONTINENT[name] = cont
        by_cont.setdefault(cont, []).append((name, rank))
    for cont, lst in by_cont.items():
        lst.sort(key=lambda x: x[1])   # fifa_rank 낮을수록(=강할수록) 앞
        _CONTINENT_COUNTRIES[cont] = lst


def _weighted_country_pick(candidates):
    """[(나라, fifa_rank), ...] 중 랭크가 좋을수록(숫자가 작을수록) 더 잘
    뽑히게 가중 추첨. 후보가 비어있으면 None."""
    if not candidates:
        return None
    weights = [1.0 / (rank + 5) for _, rank in candidates]
    return random.choices([n for n, _ in candidates], weights=weights, k=1)[0]


def _pick_nationality(team_country, team_continent, grade, pos, is_star, foreign_count, quota):
    """이 슬롯의 국적을 정한다. 반환: (nationality, new_foreign_count)."""
    _init_nationality_tables()
    if quota is not None and foreign_count >= quota:
        return team_country, foreign_count   # 쿼터 다 찼으면 강제 자국

    domestic_base = DOMESTIC_PROB_BY_GRADE.get(grade, 0.85)
    foreign_prob = min(0.95, (1 - domestic_base) * POS_FOREIGN_MULT.get(pos, 1.0))
    if random.random() >= foreign_prob:
        return team_country, foreign_count   # 자국 선수

    # 해외 출신 — 스타 슬롯은 축구 강국 우선
    if is_star and random.random() < 0.6:
        cand = [c for c in FOOTBALL_POWERHOUSES if c != team_country]
        nat = random.choice(cand) if cand else team_country
        return nat, foreign_count + 1

    if random.random() < 0.7:
        # 같은 대륙 다른 나라 (FIFA랭크 가중)
        pool = [(n, r) for n, r in _CONTINENT_COUNTRIES.get(team_continent, []) if n != team_country]
    else:
        # 다른 대륙 (FIFA랭크 가중, "축구 수출국" 위주로 자연스럽게 쏠림)
        pool = [(n, r) for cont, lst in _CONTINENT_COUNTRIES.items() if cont != team_continent
                for n, r in lst]
    nat = _weighted_country_pick(pool) or team_country
    return nat, foreign_count + 1


# ─── AI 선수 생성 ──────────────────────────────────────────────
# OVR_RANGES는 이제 파일 상단에서 constants.py로부터 가져온다 (단일 소스).
TEAM_POSITIONS = ["GK","CB","CB","LB","RB","CDM","CM","CAM","LW","RW","ST"]
KEY_STATS_BY_POS = {
    "GK":  ["positioning","concentration","mental","jump","stamina"],
    "CB":  ["tackling","heading","jump","positioning","concentration"],
    "LB":  ["tackling","speed","passing","stamina","positioning"],
    "RB":  ["tackling","speed","passing","stamina","positioning"],
    "CDM": ["tackling","passing","positioning","stamina","concentration"],
    "CM":  ["passing","dribbling","positioning","stamina","shooting"],
    "CAM": ["passing","dribbling","shooting","positioning","setpiece"],
    "LW":  ["dribbling","speed","shooting","passing","positioning"],
    "RW":  ["dribbling","speed","shooting","passing","positioning"],
    "CF":  ["shooting","dribbling","passing","positioning","heading"],
    "ST":  ["shooting","heading","jump","speed","positioning"],
}

# 등급별 팀내 역할 위계 프로파일.
#   ace_lo : 팀에이스 목표 = (tier top) * (ace_lo ~ 1.00)  (팀 강도에 따라)
#   spread : 에이스 대비 11번째(벤치) 하락폭. 상위 등급일수록 층이 얇음(다 잘함).
#   상위 리그는 선수층이 고르고(작은 spread), 하위 리그는 편차가 큼.
# [편차 축소] 기존 값은 A등급 기준 같은 K리그1 안에서도 최약팀 벤치가
# 최강팀 에이스보다 최대 -13(84*0.93*0.91=71 vs 84) 가까이 벌어져,
# 신민용이 "같은 1부인데 팀간 OVR 격차가 너무 크다"고 지적해 전 등급
# 공통으로 ace_lo를 올리고 spread를 줄여 팀간 편차를 좁혔다.
# (전 세계 모든 리그가 이 등급 중 하나를 쓰므로 국가 구분 없이 전부 적용됨)
#
# [2026-07 재조정 — 팀 "내" 편차 확대] 위 조정이 팀 간(강팀 vs 약팀) 편차는
# 잘 좁혔지만, 그 여파로 팀 "내" 편차(에이스 vs 막내)까지 SS/S/A에서
# 5~6%로 지나치게 좁아져 — 실측: EPL 최약팀도 11명 전원이 91~96 OVR로
# 몰림. 이러면 "이 팀에 월드클래스가 몇 명"이라는 개념 자체가 사라지고
# 다 고르게 최상급이 되어버린다(신민용 지적: SS/S는 팀에 월클 2~3명 —
# 강팀은 최대 4명 — 정도가 현실적이고, 나머지는 그보다 확실히 낮아야
# 한다). SS/S/A만 spread를 큰 폭으로 넓혀 팀 내 상~하위 격차를 되살렸다
# (B~F는 기존값 유지 — 그쪽은 지적 대상이 아니었음). 팀 간 편차(ace_lo)는
# 그대로 둬서 앞서 고친 부분은 유지된다 — 스타 몇 명은 여전히 리그
# 최상위에 근접하되(아래 STAR_COUNT_BY_GRADE로 명시적으로 보장), 나머지
# 다수는 확실히 그보다 낮은 지점으로 벌어진다.
TEAM_ROLE_PROFILE = {
    # ace_lo: 최약팀 에이스 = tier_top * ace_lo (강팀은 *1.0까지)
    # spread: 에이스 대비 11번째 선수(벤치) 하락폭.
    "SS": {"ace_lo": 0.96, "spread": 0.22},
    "S":  {"ace_lo": 0.96, "spread": 0.25},
    "A":  {"ace_lo": 0.95, "spread": 0.28},
    "B":  {"ace_lo": 0.94, "spread": 0.07},
    "C":  {"ace_lo": 0.93, "spread": 0.08},
    "D":  {"ace_lo": 0.92, "spread": 0.09},
    "E":  {"ace_lo": 0.91, "spread": 0.10},
    "F":  {"ace_lo": 0.90, "spread": 0.11},
}

# [2026-07 신설, 2차 개편] 등급별 '스타 슬롯' 개수 — 팀마다 실제로 월드클래스/
# 엘리트 선수가 몇 명인지 명시적으로 정해서 배치한다(스타는 위 spread에 따른
# 완만한 하락 곡선을 무시하고 리그 최상위권 OVR로 직접 꽂아 넣는다).
# team_strength(0~1, 1=리그 최강팀)가 높을수록 스타 수가 늘어난다.
#
# [2차 개편 — 신민용 지적] "SS/S 1부는 월드클래스+엘리트로만 구성돼야 한다
# (그냥 그런 선수가 없어야 함)" — 그래서 SS/S는 월드클래스를 뺀 나머지
# 11자리 전부를 엘리트로 채운다(el_fill_rest=True, el_base/el_bonus 무시).
# 반면 A등급은 "엘리트가 상위권뿐 아니라 하위권도 있고, 아예 엘리트가 없는
# 팀도 있다"는 지적대로 엘리트 슬롯 수 자체를 적게 두고 나머지는 기존
# 완만한 곡선(_target_ovr, 넓은 spread)에 맡긴다 — 그 결과 A는 최상위 몇
# 자리만 엘리트/월클이고 나머지는 자연스럽게 쭉 낮아지는 분포가 된다.
#
# el_offset: 그 등급의 '엘리트'가 리그 상한에서 얼마나 아래(오프셋 범위)에
# 형성되는지. SS/S는 상한 바로 아래(대부분 90 초중반) — "엘리트 대부분
# 상위권". A는 오프셋을 더 크게 둬서 상한보다 확실히 아래(하위권 엘리트,
# 88~91 안팎)로 형성되게 한다 — S와 A가 둘 다 "엘리트"를 갖더라도 실제
# OVR대가 다르게 나오는 이유.
STAR_COUNT_BY_GRADE = {
    "SS": {"wc_base": 2, "wc_bonus": 2, "el_fill_rest": True,  "el_offset": (4, 9)},
    "S":  {"wc_base": 2, "wc_bonus": 1, "el_fill_rest": True,  "el_offset": (4, 9)},
    "A":  {"wc_base": 0, "wc_bonus": 1, "el_fill_rest": False, "el_offset": (8, 16),
           "el_base": 1, "el_bonus": 2},
}
_MAX_WORLDCLASS_PER_TEAM = 4
# SS/S 1부는 "월클+엘리트로만" 구성이므로 엘리트에 상한을 두지 않는다
# (el_fill_rest=True면 남는 자리 전부). A처럼 el_fill_rest=False인 등급만
# 아래 상한이 적용된다.
_MAX_ELITE_PER_TEAM = 5
# [2026-07] SS/S 1부는 baseline(_target_ovr) 경로로 떨어지는 자리가 없어야
# 하지만(el_fill_rest=True라 전원 스타 배정), 방어적으로 혹시 남는 자리가
# 생기면 이 바닥 밑으로는 절대 안 내려가게 한다(TALENT_TIERS elite 하한과
# 동일한 88).
ELITE_FLOOR_BY_GRADE = {"SS": 88.0, "S": 88.0}


def _star_counts(grade, team_strength, continent_bonus=0, n_slots=11, tier=1):
    """(월드클래스 슬롯 수, 엘리트 슬롯 수) 반환. SS/S/A 외 등급은 (0,0) —
    B급 이하는 이번 조정 대상이 아니라 기존 완만한 곡선 그대로 쓴다.

    [버그수정 2026-07] "SS/S는 월클+엘리트로만 구성"이라는 설계는 원래
    1부만을 의도한 것이었는데(코드 주석에도 '1부'라고 명시돼 있었음), 정작
    이 함수엔 tier 구분이 전혀 없어서 SS/S 등급 나라의 모든 부수(2~6부까지)
    팀 전원이 거의 엘리트/월드클래스로 채워지고 있었다 — 그 결과 6부 아마추어
    팀도 1부 수준(평균 89 이상) OVR이 나오는 심각한 버그로 이어졌다. 이제
    tier에 따라 스타 슬롯 배정을 계단식으로 줄인다: 1부만 원래 설계(거의
    전원 스타) 그대로, 2부는 대폭 축소, 3부 이상은 스타 슬롯 자체가 없어
    전원 _target_ovr(그 tier의 낮은 상한 기준)로만 결정된다."""
    cfg = STAR_COUNT_BY_GRADE.get(grade)
    if not cfg:
        return 0, 0
    if tier >= 3:
        return 0, 0   # 3부 이상은 스타 취급 없음 — 전원 일반 곡선(_target_ovr)

    n_world = cfg["wc_base"] + round(cfg["wc_bonus"] * team_strength)
    if tier == 2:
        # [2026-07 재조정, 신민용 지적: "잉글랜드 2부가 스페인 2부랑
        # 비슷하거나 낮다 — 챔피언십은 80후반~90대로 맞춰져야 한다"]
        # SS는 전 세계에서 잉글랜드 하나뿐이라(get_league_grade 참고),
        # 여기서 SS만 따로 후하게 줘도 다른 나라에 영향이 없다. 챔피언십은
        # 강등 팀들의 낙하산 지원금(parachute payment)·이적료 여력 덕에
        # 실제로도 유럽 5~6위권 리그 평가를 받는 이례적인 2부 리그이므로,
        # S급(세군다·세리에B 등, 평범한 2부) 대비 스타 슬롯을 훨씬 덜 깎는다.
        n_world = max(0, n_world - (1 if grade == "SS" else 2))
    if grade == "A" and continent_bonus < 0:
        # [신민용 요청] A등급 "상위" 리그(포르투갈/네덜란드 등 국가보정 양수)만
        # 월드클래스가 나오고, "중하위" 리그(한국/일본 등 국가보정 음수)는
        # 월드클래스 자체가 안 나온다 — 같은 A등급이라도 실질 수준이 다름을 반영.
        n_world = 0
    n_world = min(n_world, _MAX_WORLDCLASS_PER_TEAM)

    if cfg.get("el_fill_rest"):
        # SS/S 1부: 월클을 뺀 나머지 전부를 엘리트로 — "월클+엘리트로만 구성".
        # 2부는 그 설계를 적용하지 않고 소수 엘리트 슬롯만 남긴다.
        # [2026-07 재조정] SS(잉글랜드 챔피언십)만 예외 — 2부인데도 대부분
        # 엘리트급으로 채워지게(강등팀 스쿼드 그대로 유지 + 낙하산 지원금).
        if tier == 1:
            n_elite = max(0, n_slots - n_world)
        elif tier == 2 and grade == "SS":
            n_elite = min(8, max(0, n_slots - n_world))
        else:
            n_elite = min(3, max(0, n_slots - n_world))
    else:
        n_elite = cfg.get("el_base", 0) + round(cfg.get("el_bonus", 0) * team_strength)
        if grade == "A" and continent_bonus < 0:
            # [신민용 요청] "엘리트 유무도 있고" — 중하위 A리그는 엘리트 자체가
            # 없는 팀도 나오도록 카운트를 깎는다(완전히 0이 될 수도 있음).
            n_elite = max(0, n_elite - 2)
        n_elite = min(n_elite, _MAX_ELITE_PER_TEAM, max(0, n_slots - n_world))
    return n_world, n_elite


def _star_target_ovr(tier_top, kind, el_offset=(6, 14)):
    """스타 슬롯 하나의 목표 OVR. 리그 상한(tier_top) 바로 아래에서 결정 —
    월드클래스는 거의 상한 그 자체, 엘리트는 등급별 el_offset만큼 그 아래
    (SS/S는 좁은 오프셋 = 상위권 엘리트, A는 넓은 오프셋 = 하위권 엘리트)."""
    if kind == "worldclass":
        return tier_top - random.uniform(0, 4)
    lo, hi = el_offset
    return tier_top - random.uniform(lo, hi)  # elite


def _tier_top_ovr(grade, tier, continent_bonus=0):
    """그 등급·tier 리그에서 도달 가능한 최고 OVR.
    continent_bonus: 대륙별 OVR 보정치 (유럽+1, 아시아-3 등)

    [버그수정 2026-07] 예전엔 OVR_RANGES에 그 등급의 tier가 정의 안 돼
    있으면(예: SS 5부, S 6부처럼 나중에 부수가 늘었는데 표를 못 채운 경우)
    무조건 45로 떨어졌는데, 이게 등급별 실제 최상단 값(SS는 90~100대)과
    무관한 고정값이라 자칫 "정의 안 된 tier가 tier1과 비슷해지는" 것보다는
    낫지만, 반대로 "SS/S처럼 원래 높은 등급인데 갑자기 뚝 떨어지는" 부자연스러운
    단절이 생겼다. 이제는 그 등급 안에서 정의된 가장 깊은 부수를 기준으로,
    한 부수당 일정폭(STEP)씩 자연스럽게 더 깎아 내려가도록 한다 — 등급표에
    없는 부수가 나와도(향후 부수를 더 늘려도) 항상 "한 단계 위보다는 낮고,
    급격한 단절은 없는" 값이 나온다."""
    grade_ranges = OVR_RANGES.get(grade, {})
    rng = grade_ranges.get(tier)
    if rng:
        return min(100, rng[1] + continent_bonus)
    if grade_ranges:
        deepest_tier = max(grade_ranges)
        deepest_top = grade_ranges[deepest_tier][1]
        STEP = 8   # 부수 하나 내려갈 때마다 대략적인 감쇠폭
        extra_tiers = tier - deepest_tier
        return min(100, max(15, deepest_top - extra_tiers * STEP) + continent_bonus)
    return 45


def _target_ovr(grade, tier, team_strength, role_idx, continent_bonus=0):
    """팀 강도(0~1) + 역할 순번(0=에이스 … 10=막내)으로 목표 OVR 산출."""
    prof = TEAM_ROLE_PROFILE.get(grade, TEAM_ROLE_PROFILE["F"])
    top = _tier_top_ovr(grade, tier, continent_bonus)
    # 팀 에이스 목표: 강팀일수록 리그 top에 근접
    ace = top * (prof["ace_lo"] + (1.0 - prof["ace_lo"]) * team_strength)
    role_mult = 1.0 - prof["spread"] * (role_idx / 10.0)
    return ace * role_mult


def _generate_all_ai_players(c, progress_cb=None):
    # 리그 단위로 묶어 8팀에 강→약 강도를 분배해야 팀 간 위계가 생긴다.
    # [리그등급 분리] cn.grade는 국대 등급 → 리그 OVR/연봉엔 COUNTRY_LEAGUE_GRADE 사용
    # [2026-07 신설, 신민용 지적: "네임드 팀들이 너무 쉽게 강등당한다"]
    # team_strength(팀 강도) 배정에 팀 이름(t.name)이 필요해져서 SELECT에 추가.
    c.execute("""SELECT t.id AS tid, t.name AS tname, t.current_tier AS tier,
                        cn.grade AS grade, cn.id AS cid, t.league_id AS lid,
                        cn.name AS cname, cn.continent AS continent
                 FROM teams t JOIN leagues l ON t.league_id=l.id
                 JOIN countries cn ON l.country_id=cn.id
                 ORDER BY t.league_id, t.id""")
    rows = [dict(r) for r in c.fetchall()]

    # 리그별 그룹핑
    leagues: dict = {}
    for r in rows:
        leagues.setdefault(r["lid"], []).append(r)

    _total_teams = len(rows)
    _done = 0
    from data.prestige_clubs import is_prestige, weighted_team_order
    for lid, teams in leagues.items():
        n = len(teams)
        # [2026-07 신설] 완전 무작위 셔플 대신 명문팀 가중 셔플 — 명문팀
        # (data/prestige_clubs.py)은 강한 team_strength 슬롯을 뽑을 확률이
        # 훨씬 높지만(PRESTIGE_WEIGHT), 0은 아니라서 가끔 하위권으로도
        # 떨어질 수 있다("토트넘도 가끔 강등권까지 간다"를 재현).
        teams_info = [{"prestige": is_prestige(t.get("cname",""), t.get("tier",1), t.get("tname",""))}
                      for t in teams]
        perm = weighted_team_order(teams_info)   # perm[0]=이번 시즌 최강팀 인덱스, ...
        league_used: set = set()
        for rank, team_idx in enumerate(perm):
            team = teams[team_idx]
            team_strength = 1.0 - (rank / (n - 1)) if n > 1 else 1.0
            # [리그등급 분리] 국대 등급(grade) 대신 리그 전용 등급 사용
            from constants import get_league_grade
            league_grade = get_league_grade(team.get("cname", ""), team["grade"])
            team_with_lg = dict(team)
            team_with_lg["grade"] = league_grade
            _generate_team_players(c, team_with_lg, team_strength, league_used)
            _done += 1
            if progress_cb and (_done % 20 == 0 or _done == _total_teams):
                progress_cb(_done, _total_teams, team.get("cname", ""))


def _generate_team_players(c, team, team_strength, league_used: set = None):
    grade = team["grade"]; tier = team["tier"]
    continent = team.get("continent", "유럽")
    if league_used is None:
        league_used = set()

    # 대륙별 OVR 보정치 + [신규] 나라별 미세조정(COUNTRY_OVR_ADJ)
    from constants import CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ
    continent_bonus = CONTINENT_OVR_BONUS.get(continent, 0)
    continent_bonus += COUNTRY_OVR_ADJ.get(team.get("cname", ""), 0)
    # SS는 이미 상한(100)에 근접 → 보정 축소 (초과 방지)
    if grade == "SS":
        continent_bonus = min(continent_bonus, 0)

    # 해당 국가 이름풀 전체를 가져온다 (리그 8팀 × 11명 = 최대 88개 필요)
    c.execute("SELECT name FROM player_names WHERE country_id=? ORDER BY RANDOM()",
              (team["cid"],))
    name_pool = [r["name"] for r in c.fetchall()]
    if not name_pool:
        name_pool = [f"선수{i}" for i in range(100)]

    # [2026-07 신설] 스타 슬롯(월드클래스/엘리트) 명시적 배정 — 완만한 곡선
    # (_target_ovr)만으로는 "이 팀에 월클이 몇 명"이 보장되지 않아서, 소수
    # 슬롯을 뽑아 리그 상한 근처 OVR로 직접 꽂아 넣는다.
    tier_top = _tier_top_ovr(grade, tier, continent_bonus)
    n_world, n_elite = _star_counts(grade, team_strength, continent_bonus, tier=tier)
    star_slot_idx = list(range(len(TEAM_POSITIONS)))
    random.shuffle(star_slot_idx)
    star_kind_by_slot = {}
    for i in star_slot_idx[:n_world]:
        star_kind_by_slot[i] = "worldclass"
    for i in star_slot_idx[n_world:n_world + n_elite]:
        star_kind_by_slot[i] = "elite"

    # [2026-07 신설, 버그수정] 역할 순번(role_idx) 랜덤화 — 어느 팀은
    # 스트라이커가, 어느 팀은 센터백이 에이스일 수 있으므로 팀마다 0~10을
    # 섞어서 배정한다(포지션 자체의 스탯 계산(_gen_ai_stats)은 그대로 pos
    # 기준이라 "센터백인데 슈팅 위주"처럼 어긋나지 않는다 — target OVR만
    # 랜덤한 포지션에 높게 배정될 뿐).
    # [버그수정] 원래는 스타 슬롯 포함 11자리 전체에 0~10을 셔플해 배정하고
    # 그 중 스타 슬롯에 떨어진 값은 그냥 버렸다 — 그 결과 스타 슬롯이 하필
    # 낮은(막내급) 값을 가져가면, 비스타 포지션들이 반대로 에이스급(0~2)
    # role_idx를 받아버려 "스타 제외 나머지는 확실히 낮은 지점" 설계 의도가
    # 깨지는 경우가 있었다. 이제 스타 인원수(n_star)만큼의 상위 랭크(0~n_star-1,
    # 에이스 쪽)는 스타 슬롯 몫으로 아예 비워두고, 비스타 포지션은 그 아래
    # 구간(n_star~10)의 role_idx만 셔플해서 나눠 갖는다 — _target_ovr의
    # role_idx 해석(0=에이스…10=막내)과 스케일(role_idx/10.0)은 그대로다.
    n_star = len(star_kind_by_slot)
    non_star_positions = [i for i in range(len(TEAM_POSITIONS)) if i not in star_kind_by_slot]
    remaining_ranks = list(range(n_star, len(TEAM_POSITIONS)))
    random.shuffle(remaining_ranks)
    role_indices = dict(zip(non_star_positions, remaining_ranks))

    _star_cfg = STAR_COUNT_BY_GRADE.get(grade, {})
    _el_offset = _star_cfg.get("el_offset", (6, 14))
    # [버그수정 2026-07] 이 88 하한은 "SS/S 1부는 절대 엘리트 미만 없음"이라는
    # 의도였는데 tier 구분이 없어 하위 부수까지 적용되던 것 — 1부에서만
    # 걸리도록 한정한다. 2부 이하의 스타 슬롯(있다면)은 tier_top 기준으로
    # 자연스럽게 낮게 계산된 값을 그대로 쓴다.
    _elite_floor = ELITE_FLOOR_BY_GRADE.get(grade) if tier == 1 else None
    _quota = FOREIGN_QUOTA_CAP.get(team.get("cname", ""))
    _foreign_count = 0

    for idx, pos in enumerate(TEAM_POSITIONS):
        # 리그 전체에서 아직 안 쓴 이름 우선 사용
        available = [n for n in name_pool if n not in league_used]
        if not available:
            available = name_pool
        name = random.choice(available)
        league_used.add(name)
        if idx in star_kind_by_slot:
            target = _star_target_ovr(tier_top, star_kind_by_slot[idx], _el_offset)
            if _elite_floor is not None:
                # [2026-07 버그 수정] 엘리트 오프셋의 랜덤 폭(uniform 상한) 때문에
                # 국가보정이 낮은 S급 나라(예: 대륙보정 0인 브라질)에서 드물게
                # 87대까지 내려가 "SS/S는 절대 엘리트 미만 없음" 원칙이 깨질 수
                # 있었다 — 스타 슬롯에도 동일한 바닥을 걸어 항상 88 이상 보장.
                target = max(target, _elite_floor)
        else:
            target = _target_ovr(grade, tier, team_strength, role_indices[idx], continent_bonus)
            # [방어적 안전장치] SS/S 1부는 el_fill_rest=True라 이 분기(baseline)를
            # 정상적으로는 타지 않지만(전원 스타 배정), 혹시라도 남는 자리가
            # 생기면 "월클+엘리트로만 구성"이 깨지지 않도록 바닥을 걸어둔다.
            if _elite_floor is not None and tier == 1:
                target = max(target, _elite_floor)
        stats = _gen_ai_stats(pos, target)
        ovr = calc_ovr(pos, stats)
        # [AI 생애] 초기 나이: 16~34 삼각분포(25 봉우리). 시즌마다 +1 되며 성장/노화.
        age = int(round(random.triangular(16, 34, 25)))
        # [세부역할 2026-07] 포지션에 맞는 SUB_ROLES 중 하나를 무작위 배정.
        from constants import SUB_ROLES
        sub_role = random.choice(SUB_ROLES.get(pos, ["기본"]))
        nationality, _foreign_count = _pick_nationality(
            team.get("cname", ""), continent, grade, pos,
            idx in star_kind_by_slot, _foreign_count, _quota)
        c.execute("""INSERT INTO ai_players
            (team_id,name,position,stamina,speed,jump,strength,shooting,passing,
             dribbling,tackling,heading,positioning,setpiece,
             mental,confidence,leadership,concentration,ovr,age,sub_role,nationality)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (team["tid"],name,pos,
             stats["stamina"],stats["speed"],stats["jump"],stats["strength"],
             stats["shooting"],stats["passing"],stats["dribbling"],
             stats["tackling"],stats["heading"],stats["positioning"],
             stats["setpiece"],stats["mental"],stats["confidence"],
             stats["leadership"],stats["concentration"],ovr,age,sub_role,nationality))


def _gen_ai_stats(pos, target):
    """목표 OVR을 받아 그 값에 수렴하도록 스탯을 역산 생성.
    키스탯은 가중치가 높으므로 목표보다 약간 높게, 비키스탯은 약간 낮게 둔다.
    + 신체 아키타입(체형)에 따른 stat_bias 를 더해 종결자/음속/포켓로켓/발전기
      유형의 개성을 부여한다(포지션이 확률을 기울이되 고정하지 않음)."""
    from constants import (BODY_TYPE_NAMES, BODY_TYPE_WEIGHTS_BY_POS,
                           BODY_TYPES, BODY_TYPE_WEIGHTS_BY_POS as _BW)
    keys = KEY_STATS_BY_POS.get(pos, ALL_STATS[:5])
    adj = target + 1.0   # calc_ovr 하향편향(가중분산) 보정

    # 아키타입 추첨 (포지션 가중치 기반, 예외 허용)
    _w = _BW.get(pos, [25, 25, 25, 25])
    body_type = random.choices(BODY_TYPE_NAMES, _w)[0]
    bias = BODY_TYPES[body_type]["stat_bias"]

    stats = {}
    for s in ALL_STATS:
        if s in keys:
            val = random.gauss(adj + 2, 3)
        else:
            val = random.gauss(adj - 3, 4)
        val += bias.get(s, 0)   # 아키타입 보정
        stats[s] = min(99, max(15, int(round(val))))
    return stats