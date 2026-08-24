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
from constants import OVR_RANGES, get_ovr_range
# [2026-08 최적화] 아래 심볼들은 원래 _generate_team_players / _gen_ai_stats /
# _generate_all_ai_players 안에서 매 팀·매 선수마다(최대 11만+회) 함수 내부
# import로 다시 불러오고 있었다. sys.modules 캐시 덕에 모듈 재실행은 없지만,
# "함수 호출 → import 문 실행 → 속성 바인딩"이 호출 수만큼 반복되는 것 자체가
# 순수 오버헤드다. 신규 게임 생성(세계 선수단 최초 생성, 약 11.5만 명) 시
# 실측 결과 이 호출 오버헤드만으로 약 1초가 소요됨을 확인 — 여기로 한 번만
# 끌어올려서 없앤다. (동작은 완전히 동일, 그냥 매번 다시 안 할 뿐)
from constants import (BODY_TYPE_NAMES, BODY_TYPE_WEIGHTS_BY_POS, BODY_TYPES,
                       CONTINENT_OVR_BONUS, COUNTRY_OVR_ADJ, SUB_ROLES, get_country_league_grade,
                       COUNTRY_LEAGUE_OVR_OVERRIDE)
from data.prestige_clubs import is_prestige, PRESTIGE_OVR_BONUS, prestige_weight, weighted_team_order, prestige_level

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
                            "cannot start a transaction within a transaction",
                            "abort due to rollback")

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
            # [2026-08 버그수정, 신민용 리포트: "abort due to ROLLBACK로 크래시
            # 났다"] _TRANSIENT_SQLITE_ERRORS의 "abort due to rollback"이 전부
            # 소문자인데, SQLite가 실제로 던지는 메시지는 "abort due to
            # ROLLBACK"(SQL 키워드는 대문자로 남음)이다. 아래 `sig in msg`는
            # 대소문자를 구분하는 부분 문자열 비교라 이 시그니처가 단 한 번도
            # 안 걸렸고, 그 결과 이 함수가 지키려던 바로 그 케이스가 재시도
            # 없이 그대로 위로 튀어(raise) 크래시로 이어졌다 — 방어 코드가
            # 정작 자신이 막으려던 오류를 못 잡은 셈. 양쪽을 소문자로 맞춰
            # 비교해서 SQLite 버전/빌드별 대소문자 차이에도 안전하게 한다.
            msg = str(e).lower()
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
    # [2026-07 5차 수정, 신민용 리포트: "abort due to ROLLBACK으로
    # standings_window에서 크래시났다"] fetch류는 지금까지 락만 걸고
    # _retry_sqlite_op(재시도)는 안 타고 있었다 — execute/executemany/
    # executescript는 이미 재시도 방어가 있는데, 그 뒤에 이어지는 fetch
    # 단계만 방어가 없어서, 워커 스레드가 롤백하는 바로 그 타이밍에 UI
    # 타이머의 fetchall()이 걸리면 재시도 없이 그대로 크래시로 이어졌다.
    # 이제 fetch류도 execute류와 동일하게 _retry_sqlite_op를 타게 한다.
    def fetchone(self, *a, **kw):
        return _retry_sqlite_op(object.__getattribute__(self, "_real").fetchone, *a, **kw)
    def fetchmany(self, *a, **kw):
        return _retry_sqlite_op(object.__getattribute__(self, "_real").fetchmany, *a, **kw)
    def fetchall(self, *a, **kw):
        return _retry_sqlite_op(object.__getattribute__(self, "_real").fetchall, *a, **kw)
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

# [2026-08 신설, 신민용 리포트: "1986년으로 시작하면 국제대회가 하나도 안
# 열린다"] GAME_START_YEAR가 새 선수 생성 화면에서 선택 가능해졌는데,
# intl_engine.py는 WC_START_YEAR 등을 constants.py가 "임포트되는 그 순간"의
# GAME_START_YEAR(하드코딩된 기본값 2000)로 한 번 계산해서 고정값으로 갖고
# 있었다 — 실제로 플레이어가 1986년을 선택해도 이 계산엔 전혀 반영이 안 돼,
# 대회 개최년도 판정이 계속 "2000년 기준"으로만 이뤄졌다. 실제로 선택한
# 시작연도를 meta 테이블에 저장해두고, intl_engine.py가 매번 이 값을 기준으로
# 대회 시작년도를 다시 계산하게 한다. 세션당 1번만 DB에서 읽고 이후엔
# 캐시(_game_start_year_cache)로 재사용 — 메타 조회를 매주 반복하지 않는다.
_game_start_year_cache = None


def get_game_start_year():
    """실제로 이번 세이브가 선택한 시작 연도(meta.game_start_year). 없으면
    (예: 이 패치 이전 세이브) constants.GAME_START_YEAR로 폴백한다."""
    global _game_start_year_cache
    if _game_start_year_cache is not None:
        return _game_start_year_cache
    conn = get_conn()
    row = conn.execute("SELECT value FROM meta WHERE key='game_start_year'").fetchone()
    if row and row["value"]:
        try:
            _game_start_year_cache = int(row["value"])
            return _game_start_year_cache
        except (TypeError, ValueError):
            pass
    from constants import GAME_START_YEAR
    _game_start_year_cache = GAME_START_YEAR
    return _game_start_year_cache


def set_game_start_year(year: int):
    """새 선수 생성 시(create_player) 실제 선택된 시작 연도를 영구 저장하고
    캐시를 갱신한다."""
    global _game_start_year_cache
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('game_start_year', ?)",
                 (str(year),))
    conn.commit()
    _game_start_year_cache = year


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


# [2026-08 신설, 신민용 리포트: "진행 버튼 누를 때 4주마다 한 번씩 0.7~1초
# 멈춘다"] [PERF-DAY] 계측으로 확인됨 — flush_to_disk()는 전체 DB를
# 디스크에 backup하는 진짜 I/O라서, 게임 진행 흐름(advance_days) 안에서
# 동기로 부르면 그 시간만큼 화면이 그대로 멈춘다. 위 docstring에도
# 적혀있듯 이 백업은 이미 게임 진행용 풀 커넥션(_pool_conn)과 완전히
# 분리된 별도 스냅샷 커넥션만 건드리도록 설계돼 있다 — 그래서 백그라운드
# 스레드에서 돌려도 게임 진행 커넥션과 절대 충돌하지 않는다. 저장
# 자체(내용·주기·안전성)는 완전히 동일하게 유지하고, "어느 스레드에서
# 도느냐"만 바꾼다.
_flush_thread: "threading.Thread | None" = None
_flush_thread_lock = threading.Lock()


def flush_to_disk_async():
    """flush_to_disk()를 백그라운드 스레드에서 실행 — 게임 진행(advance_days)
    중 주기적 자동저장에 쓴다. 이미 백업이 진행 중이면(정상적으로는 겹칠
    일이 거의 없지만 안전하게) 이번 호출은 조용히 건너뛴다 — 다음
    자동저장 주기(몇 주 뒤)에 다시 시도되고, 앱 종료 시 closeEvent가
    항상 wait_for_pending_flush() + 동기 flush_to_disk()로 최종 저장을
    보장하므로 데이터 유실은 없다."""
    global _flush_thread
    if not USE_MEMORY_DB:
        return
    with _flush_thread_lock:
        if _flush_thread is not None and _flush_thread.is_alive():
            return  # 이미 진행 중 — 건너뜀
        def _worker():
            try:
                flush_to_disk()
            except Exception:
                pass
        _flush_thread = threading.Thread(target=_worker, daemon=True,
                                         name="flush_to_disk_async")
        _flush_thread.start()


def wait_for_pending_flush(timeout=10):
    """[2026-08 신설] 앱 종료 등 '저장이 반드시 끝나야 하는' 시점에 호출 —
    진행 중인 비동기 백업 스레드가 있으면 끝날 때까지 기다린다. 이후
    호출부가 마지막으로 동기 flush_to_disk()를 한 번 더 부르면, 그 사이
    바뀐 최신 상태까지 확실히 저장된다."""
    global _flush_thread
    t = _flush_thread
    if t is not None and t.is_alive():
        t.join(timeout=timeout)

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
        classification_status TEXT NOT NULL DEFAULT 'NORMAL',
        parent_team_id INTEGER,
        review_reason TEXT,
        FOREIGN KEY(league_id) REFERENCES leagues(id))""")
    # [2026-08 버그수정, 신민용 리포트: "no such column: parent_team_id"]
    # CREATE TABLE IF NOT EXISTS는 teams가 이미 있는 기존 세이브(산하팀
    # 컬럼 추가 이전에 생성된 DB)에서는 아무것도 안 하고 넘어간다 — 그런데
    # 바로 아래 CREATE INDEX는 무조건 실행돼서, 새 컬럼이 없는 기존 DB에서
    # "no such column" 에러로 죽었다. 인덱스를 걸기 전에 컬럼 존재 여부를
    # 직접 확인하고, 없으면 ALTER TABLE로 추가해 새 DB/기존 DB 양쪽 모두
    # 안전하게 만든다(재실행해도 안전 — 이미 있으면 건너뜀).
    _existing_team_cols = {row[1] for row in c.execute("PRAGMA table_info(teams)").fetchall()}
    if "classification_status" not in _existing_team_cols:
        c.execute("ALTER TABLE teams ADD COLUMN classification_status TEXT NOT NULL DEFAULT 'NORMAL'")
    if "parent_team_id" not in _existing_team_cols:
        c.execute("ALTER TABLE teams ADD COLUMN parent_team_id INTEGER")
    if "review_reason" not in _existing_team_cols:
        c.execute("ALTER TABLE teams ADD COLUMN review_reason TEXT")

    c.execute("CREATE INDEX IF NOT EXISTS idx_teams_parent_team_id ON teams(parent_team_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_teams_classification_status ON teams(classification_status)")
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
    # [2026-08 신설, "명문팀 lifecycle 조사" 요청] AI끼리의 이적을 기록하는
    # 로그 — 지금까지는 ai_players.last_transfer_year만 남아서 "언제"는
    # 알아도 "어디서 어디로, 어떤 OVR로, 명문도 몇 등급끼리" 오갔는지는
    # 전혀 추적이 안 됐다. 레알 마드리드 스쿼드가 1998년 95.64 → 2008년
    # 86.08로 떨어진 원인이 영입 부족인지 핵심선수 유출인지 구분하려면
    # 이 로그가 반드시 필요하다(GPT 분석 합의). 이 시점부터 새로 발생하는
    # 이적만 쌓이며, 과거 이적은 소급 기록되지 않는다.
    c.execute("""CREATE TABLE IF NOT EXISTS ai_transfer_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        season INTEGER, year INTEGER, player_id INTEGER,
        player_name TEXT DEFAULT '', player_position TEXT DEFAULT '',
        player_age INTEGER DEFAULT 0, player_ovr INTEGER DEFAULT 0,
        from_team_id INTEGER, to_team_id INTEGER,
        from_team_prestige INTEGER DEFAULT 0, to_team_prestige INTEGER DEFAULT 0,
        from_team_avg_ovr REAL DEFAULT 0, to_team_avg_ovr REAL DEFAULT 0,
        transfer_type TEXT DEFAULT '')""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_ai_transfer_log_year
        ON ai_transfer_log(year)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_ai_transfer_log_teams
        ON ai_transfer_log(from_team_id, to_team_id)""")

    # [2026-08 신설, 부상 시스템 확장 4단계 — GPT 검토 확정 스키마]
    # 부상 이력 — 커리어/은퇴 창 표시 + 재발(다음 단계) 판정용. history_id를
    # 별도 PK로 두는 이유: 같은 선수가 같은 injury_id(부상 종류)를 여러 번
    # 겪을 수 있어서 injury_id 자체는 고유키가 될 수 없음.
    # expected_days(부상 풀이 가진 원래 예상 회복기간) vs actual_days(실제
    # 결장일)를 분리 — 지금은 항상 같지만(재활 등으로 앞당기는 시스템이
    # 아직 없음), 나중에 그런 시스템이 생겨도 스키마를 안 바꿔도 되게.
    # return_date/actual_days는 NULL 허용 — 부상 중인 선수는 아직 복귀 전.
    c.execute("""CREATE TABLE IF NOT EXISTS injury_history(
        history_id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        injury_id TEXT, body_part TEXT, tier TEXT,
        start_date TEXT, expected_days INTEGER, expected_return_date TEXT,
        return_date TEXT, actual_days INTEGER,
        was_recurrence INTEGER DEFAULT 0)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_injury_history_player
        ON injury_history(player_id)""")
    c.execute(f"""CREATE TABLE IF NOT EXISTS my_player(
        id INTEGER PRIMARY KEY,
        name TEXT, nationality TEXT, flag TEXT,
        age INTEGER DEFAULT 16, birth_year INTEGER DEFAULT {GAME_START_YEAR - PLAYER_START_AGE},
        position TEXT DEFAULT 'CM', sub_role TEXT DEFAULT '박스투박스',
        personality TEXT DEFAULT '성실함', height INTEGER DEFAULT 175,
        weight INTEGER DEFAULT 70, peak_age INTEGER DEFAULT 25,
        difficulty TEXT DEFAULT 'easy',
        fame INTEGER DEFAULT 0, popularity INTEGER DEFAULT 0,
        fans INTEGER DEFAULT 0, agent_grade TEXT DEFAULT '없음',
        salary INTEGER DEFAULT 0, total_assets INTEGER DEFAULT 0,
        stress INTEGER DEFAULT 10, happiness INTEGER DEFAULT 10,
        injury_load INTEGER DEFAULT 0,
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
        season_injury_matches_missed INTEGER DEFAULT 0,
        season_suspension_matches_missed INTEGER DEFAULT 0,
        season_bench_matches_missed INTEGER DEFAULT 0,
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
        wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        transfer_fee INTEGER DEFAULT 0)""")
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
    # [2026-08 신설, 골 시상 시스템] 골 "자체"의 기록(누가/언제/어디서/어떤
    # 슛/몇 점)과 그 골이 받은 "상"(awards)을 완전히 분리한다 — 같은 골이
    # 원더골 태그 + 리그 올해의 골 + 대회 최고의 골 + 푸스카스상을 동시에
    # 가질 수 있는 구조를 자연스럽게 지원하기 위함(설계문서 v4 참고).
    # is_pseudo: AI가 시즌말에 역산 생성한 "잠재 골"인지(1) 실제 발생한
    # 골인지(0) 구분 — 절대 섞어서 통계 내면 안 됨(예: "내 원더골 개수"
    # 조회엔 pseudo가 절대 포함되면 안 되지만, "푸스카스 후보 풀"에는 포함).
    c.execute("""CREATE TABLE IF NOT EXISTS goal_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER NOT NULL,
        week INTEGER DEFAULT 0,
        round TEXT DEFAULT '',
        player_id INTEGER,
        team_id INTEGER,
        opponent_team_id INTEGER,
        competition_type TEXT NOT NULL,
        competition_id INTEGER,
        league_id INTEGER,
        league_name TEXT DEFAULT '',
        shot_type TEXT NOT NULL,
        goal_features TEXT DEFAULT '[]',
        shot_score INTEGER NOT NULL,
        context_score REAL NOT NULL,
        final_score REAL NOT NULL,
        is_wonder_goal INTEGER DEFAULT 0,
        is_mine INTEGER DEFAULT 0,
        is_pseudo INTEGER DEFAULT 0)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_goal_events_year_score
        ON goal_events(year, final_score DESC)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_goal_events_player_year
        ON goal_events(player_id, year)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_goal_events_scope
        ON goal_events(year, competition_type, competition_id)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_goal_events_mine
        ON goal_events(is_mine)""")
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
    # [2026-08 신설, 신민용 리포트: "20년대는 렉 때문에 플레이가 거의
    # 불가능"] match_results_archive가 시즌이 쌓일수록 무한정 커지는 게
    # (실측: 21시즌차 세이브에서 346만 행, 인덱스 포함 195MB — 전체
    # game.db의 70%) 자동저장(flush_to_disk, DB 전체 백업) 지연의 직접
    # 원인이었다. world_browser의 "역대 순위표" 조회는 사실 팀별 승/무/
    # 패/득실만 있으면 되고 경기 하나하나의 스코어는 안 보여준다 — 그래서
    # 시즌이 아카이브로 넘어갈 때 이 요약을 미리 계산해 여기 저장해두고,
    # 원본 경기 행은 (내 커리어의 "출전 X/Y" 분모 계산처럼 실제로 날짜
    # 단위 원본이 필요한 소수의 케이스만 남기고) 정리한다 — 자세한 정리
    # 로직은 database.py의 _summarize_and_prune_archive 참고.
    c.execute("""CREATE TABLE IF NOT EXISTS league_season_standings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        league_id INTEGER, season INTEGER, year INTEGER, team_id INTEGER,
        wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        goals_for INTEGER DEFAULT 0, goals_against INTEGER DEFAULT 0)""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_lss_league_season
               ON league_season_standings(league_id, season)""")
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_lss_unique
               ON league_season_standings(league_id, season, team_id)""")
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
    # [2026-07 신설, 국가대표 OVR 재설계 — 신민용+GPT 검토] 국가별 "세대
    # 계수"를 저장하는 테이블. 밴드(하/중/상)만으로는 매 호출마다 완전
    # 독립적으로 난수를 뽑기 때문에, 같은 나라가 올해 하한 근처였다가
    # 내년에 바로 상한 근처로 튀는 비현실적인 롤러코스터가 나올 수 있다.
    # 이 계수(0.97~1.03)가 8~12년 주기로 천천히 목표치를 향해 이동하면서
    # 밴드 값에 곱해져, "국가는 안 바뀌지만 세대는 바뀐다"는 느낌을 만든다.
    c.execute("""CREATE TABLE IF NOT EXISTS nat_generation(
        country TEXT PRIMARY KEY,
        coef REAL DEFAULT 1.0,
        target REAL DEFAULT 1.0,
        cycle_start_year INTEGER DEFAULT 0,
        cycle_len INTEGER DEFAULT 10,
        last_year INTEGER DEFAULT 0)""")
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
    # ── 유로파급/컨퍼런스급 대륙대항전 (2026-08 신설) ──
    # cl_tournaments/cl_entries/cl_matches/cl_history와 완전히 동일한
    # 최종 스키마(그동안의 ALTER TABLE 이력까지 전부 반영된 형태)를
    # el_*(유로파급)/ecl_*(컨퍼런스급) 두 세트로 그대로 복제한다 —
    # competition_common.py가 테이블명만 cfg로 받아 똑같은 쿼리를 쓰므로,
    # 컬럼 구성이 cl_*와 정확히 같아야 한다.
    for _prefix in ("el", "ecl"):
        c.execute(f"""CREATE TABLE IF NOT EXISTS {_prefix}_tournaments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER, continent TEXT, name TEXT,
            status TEXT DEFAULT 'ko', first_stage TEXT DEFAULT 'R32',
            winner_team_id INTEGER DEFAULT 0,
            my_in INTEGER DEFAULT 0, my_result TEXT DEFAULT '',
            my_team_id INTEGER DEFAULT 0, my_qualified INTEGER DEFAULT 0)""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS {_prefix}_entries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER, team_id INTEGER, team_name TEXT,
            flag TEXT, country TEXT, grade TEXT, ovr REAL,
            alive INTEGER DEFAULT 1, grp TEXT DEFAULT '')""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS {_prefix}_matches(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER, stage TEXT, week INTEGER,
            home_team_id INTEGER, away_team_id INTEGER,
            home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
            pso_winner INTEGER DEFAULT 0, pso_score TEXT DEFAULT '',
            is_my INTEGER DEFAULT 0, slot INTEGER DEFAULT 0,
            my_played INTEGER DEFAULT 0, my_position TEXT DEFAULT '',
            my_saves INTEGER DEFAULT 0, my_goals INTEGER DEFAULT 0,
            my_assists INTEGER DEFAULT 0, my_rating REAL DEFAULT 0,
            my_shots INTEGER DEFAULT 0, my_shots_on INTEGER DEFAULT 0,
            my_key_passes INTEGER DEFAULT 0, my_dribbles INTEGER DEFAULT 0,
            my_blocks INTEGER DEFAULT 0, my_pass_acc REAL DEFAULT 0,
            my_conceded INTEGER DEFAULT 0, grp TEXT DEFAULT '',
            day INTEGER DEFAULT 0, my_absence_reason TEXT DEFAULT NULL)""")
        c.execute(f"""CREATE TABLE IF NOT EXISTS {_prefix}_history(
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
    # ── 슈퍼컵 (super_cup_engine, 2026-08 신설) ──
    # [10순위] 대륙별 연 1회, 참가 4팀(챔스 우승/준우승 + 유로파급 우승 +
    # 컨퍼런스급 우승) → 준결승 2경기 + 결승 1경기(3/4위전 없음, 총 3경기).
    # el_matches/ecl_matches와 동일한 최종 컬럼 구성(day/my_absence_reason
    # 등 그동안의 ALTER TABLE 이력까지 이미 반영된 형태)을 새 테이블에
    # 처음부터 넣는다 — sim_ai_match 등 competition_common.py의 공용
    # 함수들이 이 컬럼들을 그대로 기대하므로, 나중에 마이그레이션으로
    # 따라잡을 필요 없이 애초에 맞춰서 만든다.
    # entries에만 있는 seed_role(cl_champion/cl_runner_up/el_champion/
    # ecl_champion)은 "이 팀이 왜 여기 있는지"를 기록해 상세 화면·복사
    # 기능에서 참가 자격을 보여줄 때 쓴다.
    c.execute("""CREATE TABLE IF NOT EXISTS sc_tournaments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, continent TEXT, name TEXT,
        status TEXT DEFAULT 'sf', first_stage TEXT DEFAULT 'SF',
        winner_team_id INTEGER DEFAULT 0,
        my_in INTEGER DEFAULT 0, my_result TEXT DEFAULT '',
        my_team_id INTEGER DEFAULT 0, my_qualified INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sc_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, team_id INTEGER, team_name TEXT,
        flag TEXT, country TEXT, grade TEXT, ovr REAL,
        alive INTEGER DEFAULT 1, seed_role TEXT DEFAULT '')""")
    c.execute("""CREATE TABLE IF NOT EXISTS sc_matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, stage TEXT, week INTEGER,
        home_team_id INTEGER, away_team_id INTEGER,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        pso_winner INTEGER DEFAULT 0, pso_score TEXT DEFAULT '',
        is_my INTEGER DEFAULT 0, slot INTEGER DEFAULT 0,
        my_played INTEGER DEFAULT 0, my_position TEXT DEFAULT '',
        my_saves INTEGER DEFAULT 0, my_goals INTEGER DEFAULT 0,
        my_assists INTEGER DEFAULT 0, my_rating REAL DEFAULT 0,
        my_shots INTEGER DEFAULT 0, my_shots_on INTEGER DEFAULT 0,
        my_key_passes INTEGER DEFAULT 0, my_dribbles INTEGER DEFAULT 0,
        my_blocks INTEGER DEFAULT 0, my_pass_acc REAL DEFAULT 0,
        my_conceded INTEGER DEFAULT 0, grp TEXT DEFAULT '',
        day INTEGER DEFAULT 0, my_absence_reason TEXT DEFAULT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS sc_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, competition TEXT, team_name TEXT, result TEXT,
        goals INTEGER DEFAULT 0, assists INTEGER DEFAULT 0,
        caps INTEGER DEFAULT 0, rating REAL DEFAULT 0)""")
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
        has_qualifying INTEGER DEFAULT 0,
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
    # ── 승강 플레이오프 (promotion_playoff_engine) ──────────────────
    # [2026-07 신설] _process_promotion_relegation이 43주에 순위를 확정할 때,
    # PO 대상(po_count 자리)에 걸린 팀은 즉시 이동시키지 않고 여기에
    # "보류" 상태로 적어둔다 — 자동 이동분(auto_count)은 기존처럼 그 자리에서
    # 바로 teams.league_id를 바꾸고 promotion_log에 남긴다. PO 대상 팀은
    # 44주(PLAYOFF_WEEK) 진입 시 promotion_playoff_engine.start_promotion_
    # playoffs()가 이 테이블을 읽어 실제 대진을 만들고, PO 결과가 나온 뒤에야
    # teams.league_id/promotion_log에 반영된다(그때 이 테이블에서 소비돼
    # 지워짐).
    c.execute("""CREATE TABLE IF NOT EXISTS po_pending_slots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, upper_league_id INTEGER, lower_league_id INTEGER,
        rule_id TEXT, side TEXT, offset_idx INTEGER,
        team_id INTEGER, team_name TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS po_tournaments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, upper_league_id INTEGER, lower_league_id INTEGER,
        rule_id TEXT, status TEXT DEFAULT 'pending',
        my_in INTEGER DEFAULT 0, my_team_id INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS po_matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, match_key TEXT, day INTEGER,
        home_team_id INTEGER DEFAULT 0, away_team_id INTEGER DEFAULT 0,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        pso_winner INTEGER DEFAULT 0, pso_score TEXT DEFAULT '',
        is_boundary INTEGER DEFAULT 0, finalized INTEGER DEFAULT 0,
        is_my INTEGER DEFAULT 0, my_played INTEGER DEFAULT 0,
        my_position TEXT DEFAULT '', my_saves INTEGER DEFAULT 0,
        my_goals INTEGER DEFAULT 0, my_assists INTEGER DEFAULT 0,
        my_rating REAL DEFAULT 0)""")
    # 커리어/은퇴창에서 "국제전"과 같은 톤으로 개인 PO 경기 기록을 보여주기
    # 위한 요약 테이블(get_my_intl_matches와 동일한 목적 — po_matches는
    # 대회 데이터라 지워질 수 있지만 이 표는 커리어 기록으로 영구 보존).
    c.execute("""CREATE TABLE IF NOT EXISTS po_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year INTEGER, team_name TEXT, opp_name TEXT, result TEXT,
        goals INTEGER DEFAULT 0, assists INTEGER DEFAULT 0,
        rating REAL DEFAULT 0)""")
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
        # [2026-08 신설, 난이도 시스템] 캐릭터 생성 시 한 번만 정해지고
        # 이후 변경 불가. 'easy'/'normal'/'hard' 3단계 — 기본값은 반드시
        # 'easy'로 둬서(신민용 확정) 이 컬럼이 없던 구버전 세이브가 마이그레이션
        # 되어도 자동으로 하위호환(기존과 동일하게 전부 공개)된다.
        "ALTER TABLE my_player ADD COLUMN difficulty TEXT DEFAULT 'easy'",
        "ALTER TABLE career_entries ADD COLUMN contract_years INTEGER DEFAULT 0",
        "ALTER TABLE career_entries ADD COLUMN transfer_type TEXT DEFAULT '입단'",
        # [2026-07 신설] 이적료(transfer_fee) — transfer_type/exit_type(어떻게
        # 왔는지)과는 별개 축(얼마에 왔는지). FA(계약만료)면 자연스럽게 0.
        # 과거 특정 거래 시점의 값을 그대로 고정 저장한다(현재 시장가치처럼
        # 매번 재계산하지 않음 — 나중에 OVR이 바뀌어도 그때 값 그대로 유지).
        "ALTER TABLE career_entries ADD COLUMN transfer_fee INTEGER DEFAULT 0",
        # [2026-07 신설, 신민용 지적: "임대(1년)/임대 종료(1년) 표기가
        # 상대팀을 안 보여줘서 어느 팀으로 임대 갔는지/어디로 복귀했는지
        # 알 수 없다"] exit_type='임대'인 행(원소속팀에서 떠나는 행)엔
        # 임대 '보낸 곳' 팀명을, exit_type='임대 종료'인 행(임대처에서
        # 떠나는 행)엔 '복귀할' 원소속팀명을 저장해 UI가 팀명을 함께
        # 보여줄 수 있게 한다.
        "ALTER TABLE career_entries ADD COLUMN loan_partner_team TEXT DEFAULT ''",
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
        # [2026-08 신설, 신민용 확정] 훈련 gain 진행률(%) 감속 커브의 기준선.
        # 스탯별 '커리어 시작값' 스냅샷을 JSON({"stamina":42,...} 형태)으로
        # 1회 저장하고 이후 절대 변경하지 않는다(aging_peak_max와 같은
        # 패턴 - 매일 다시 계산/조회하지 않고, 생성 시 한 번 박아두고
        # 끝까지 그대로 참조). ''(빈 값)이면 이 기능 추가 이전 세이브라
        # 아직 스냅샷 전 - game_engine._ensure_stat_start가 최초 훈련 시
        # 현재값으로 1회 채워 넣는다.
        "ALTER TABLE my_player ADD COLUMN stat_start TEXT DEFAULT ''",
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
        # [2026-07 신설, 신민용+GPT 검토: "계약 잔여기간 반영하면 현실성이
        # 크게 오른다 + 방금 이적한 선수는 최소 1시즌은 다시 안 나가야
        # 한다"] AI 선수는 지금까지 계약이라는 개념 자체가 없었다 —
        # 일괄 백필(5.9만 명 대상 대량 UPDATE) 대신, 이적/은퇴대체로 새로
        # 합류하는 시점에 지연 할당(lazy-init)한다. 기존 선수는 0(미설정)
        # 으로 남는데, 이건 "중립(대략 2~3년 남은 것처럼 취급)"으로 처리
        # 한다 — 그래서 대량 백필이 없어도 안전하다.
        "ALTER TABLE ai_players ADD COLUMN contract_end_year INTEGER DEFAULT 0",
        "ALTER TABLE ai_players ADD COLUMN last_transfer_year INTEGER DEFAULT 0",
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
        "ALTER TABLE my_player ADD COLUMN po_suspension INTEGER DEFAULT 0",
        # [2026-07 버그수정, 신민용 리포트: "승강 플레이오프 중 부상당했는데
        # 다른 대회처럼 (부상) 표시로도 기록이 안 남는다"] sim_my_po_match_as_ai
        # (부상 등으로 AI가 대신 뛸 때)가 po_history에 아예 아무것도 안
        # 남기고 있었다 — cup_matches.my_absence_reason과 동일한 목적의
        # 컬럼을 po_history(승강 PO는 커리어 영구 기록용 테이블)에 추가한다.
        "ALTER TABLE po_history ADD COLUMN absence_reason TEXT DEFAULT NULL",
        # [2026-08 추가, 신민용 리포트: "승강 플레이오프는 골/어시/평점/결과만
        # 뜨고 챔스·국제전처럼 슈팅/유효/기회창출/드리블(GK는 선방/실점)이
        # 안 뜬다"] po_history가 애초에 그 값을 저장할 컬럼 자체가 없었다
        # (goals/assists/rating뿐). cl_matches/intl_matches와 동일한 필드셋을
        # po_history에도 추가하고, simulate_my_po_match에서 _player_perf가
        # 이미 계산해두고 있던 detail(shots/shots_on/key_passes/dribbles/
        # blocks/pass_acc)과 saves/conceded를 그대로 채워 넣는다.
        "ALTER TABLE po_history ADD COLUMN shots INTEGER DEFAULT 0",
        "ALTER TABLE po_history ADD COLUMN shots_on INTEGER DEFAULT 0",
        "ALTER TABLE po_history ADD COLUMN key_passes INTEGER DEFAULT 0",
        "ALTER TABLE po_history ADD COLUMN dribbles INTEGER DEFAULT 0",
        "ALTER TABLE po_history ADD COLUMN blocks INTEGER DEFAULT 0",
        "ALTER TABLE po_history ADD COLUMN pass_acc REAL DEFAULT 0",
        "ALTER TABLE po_history ADD COLUMN saves INTEGER DEFAULT 0",
        "ALTER TABLE po_history ADD COLUMN conceded INTEGER DEFAULT 0",
        "ALTER TABLE po_history ADD COLUMN score TEXT DEFAULT ''",
        # [2026-08 버그수정, 신민용 리포트: "유로파/CWC/승강플옵/컨퍼런스 경기별
        # 기록에 그 경기 당시 포지션이 안 남는다"] cl_matches/el_matches/
        # ecl_matches/cwc_matches는 애초부터 my_position 컬럼이 있었는데
        # po_history(승강 PO 커리어 영구 기록용 테이블)만 이 컬럼 자체가
        # 없었다 — simulate_my_po_match에서 골/어시/평점 등은 다 저장하면서
        # 그 경기 당시 무슨 포지션으로 뛰었는지는 아예 저장할 곳이 없었다.
        "ALTER TABLE po_history ADD COLUMN position TEXT DEFAULT ''",
        # [2026-08 신설, 신민용 리포트: "시즌 중 이적하면 시즌 스탯이 0으로
        # 리셋돼서 이적 전 활약이 시상 계산에서 통째로 사라진다"] season_*는
        # join_team()에서 이적할 때마다 0으로 리셋된다(새 팀 "이번 시즌"
        # UI 표시용으로는 맞는 동작) — 그런데 _process_awards가 그 값을
        # 그대로 갖다 써서, 시즌 대부분을 뛴 원래 팀의 기록이 사라지고
        # 이적한 지 얼마 안 된 새 팀 기록(대개 최소 출전 기준도 못 채움)만
        # 남는 문제가 있었다. award_*는 이적해도 리셋되지 않고 진짜 시즌
        # 종료(_end_of_season) 시점에만 리셋되는 별도 누적치 — 시상 계산
        # 전용이다.
        "ALTER TABLE my_player ADD COLUMN award_matches INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN award_goals INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN award_assists INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN award_saves INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN award_goals_against INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN award_rating_sum REAL DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN award_rating_cnt INTEGER DEFAULT 0",
        # [2026-07 버그수정, 승강 플레이오프 도입 중 발견 — 신민용 세션]
        # promotion_log.league_name만으로는 어느 리그인지 특정할 수 없다
        # ("프리메라 디비시온"처럼 나라마다 이름이 겹치는 리그가 실제로
        # 여러 개 존재 — 아르헨티나/안도라/칠레 등). world_browser.
        # get_league_champions()가 이름 문자열로 promotion_log를 조회하면
        # 다른 나라의 승강 기록이 섞여 들어오는 버그가 있었다. league_id를
        # 직접 남겨서 이름 충돌과 무관하게 정확히 조회할 수 있게 한다.
        "ALTER TABLE promotion_log ADD COLUMN from_league_id INTEGER DEFAULT 0",
        "ALTER TABLE promotion_log ADD COLUMN to_league_id INTEGER DEFAULT 0",
        # [2026-08 신설, 신민용 리포트: "승격팀 강등팀 겹치는데?" 진단 중
        # 발견] promotion_log가 team_name만 저장해서, 이름이 같은 서로
        # 다른 팀(동명이팀 — 이 게임엔 흔함, 실측 230종류)이 같은 해에
        # 각각 승격/강등하면 "같은 팀이 승격+강등 둘 다"로 오판하게 됐다.
        # team_id를 추가해 이후로는 이름이 아니라 팀 자체로 정확히 추적한다.
        "ALTER TABLE promotion_log ADD COLUMN team_id INTEGER DEFAULT 0",
        # [2026-07 신설] 컵대회/챔스/국제대회에서 내가 결장한 이유(부상/출전정지
        # 등)를 기록 — 신민용 요청: 커리어 세부 기록·은퇴창·AI 요약에
        # "(부상)"/"(출전정지)" 식으로 표시하기 위함. NULL이면 정상 출전.
        "ALTER TABLE cl_matches ADD COLUMN my_absence_reason TEXT DEFAULT NULL",
        "ALTER TABLE cup_matches ADD COLUMN my_absence_reason TEXT DEFAULT NULL",
        "ALTER TABLE intl_matches ADD COLUMN my_absence_reason TEXT DEFAULT NULL",
        # [2026-08 신설, 신민용 설계 확정: "국내컵 단계적 합류 구조 재설계"]
        # 5부가 존재하는 나라만 "예선"(5부 단독) 단계를 갖고, 그 아래
        # "1라운드"부터는 다시 1부터 번호를 매긴다 — has_qualifying=1이면
        # 화면에 보이는 라운드 번호 계산에서 예선 1개 라운드분을 뺀다
        # (cup_engine._start_next_round 참고).
        "ALTER TABLE cup_tournaments ADD COLUMN has_qualifying INTEGER DEFAULT 0",
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
        # [2026-07 신설, 신민용 요청] 오퍼/입단 창 상태 영속화 — 예전엔
        # generate_offers()가 매번 완전히 새로 랜덤 생성돼서, 결정을 내리기
        # 전에 게임을 껐다 켜면(또는 창이 열린 채로 종료됐다 재실행하면)
        # 오퍼 목록이 통째로 새로 뽑혔다(직접 지원으로 확정한 팀도 사라짐).
        # 이러면 오히려 "맘에 드는 오퍼 나올 때까지 재접속"이 가능해지는
        # 셈이라, 오퍼가 처음 생성되는 시점에 이 컬럼에 JSON으로 저장해두고
        # 결정(입단 완료/전부 결렬)이 나기 전까지는 껐다 켜도 항상 같은
        # 오퍼 목록이 그대로 다시 뜨게 한다. 결정이 나면 빈 문자열로 비운다.
        "ALTER TABLE my_player ADD COLUMN pending_offer_state TEXT DEFAULT ''",
        # [2026-07 신설, 신민용+GPT 다회 설계 확정: 이적 강제판매/최소수용금액
        # 시스템] 거절 누적 카운터 — 3시즌 이상 지난 거절은 자연 감쇠시켜야
        # 해서(무한 누적 방지), "마지막 거절 연도"도 같이 저장해 매번 그
        # 차이를 계산한다.
        "ALTER TABLE my_player ADD COLUMN transfer_rejection_count INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN transfer_rejection_last_year INTEGER DEFAULT 0",
        # [2026-07 신설, 신민용+GPT 다회 설계 확정: "구단 판매 추진" 시스템]
        # 오퍼 ON/OFF는 이미 있던 offers_enabled 필드를 그대로 쓴다(처음에
        # receive_transfer_offers를 새로 만들었다가, 기존 토글이 있다는
        # 걸 나중에 발견해서 제거하고 통합함). 판매추진 ON/OFF만 새 필드.
        "ALTER TABLE my_player ADD COLUMN allow_club_sale_push INTEGER DEFAULT 1",
        # 판매추진 상태 — 시작일/마지막오퍼일/이 기간 전용 거절횟수(일반
        # transfer_rejection_count와 의미가 달라 분리)/종료판정용 저점수
        # 연속주차 카운터.
        "ALTER TABLE my_player ADD COLUMN sale_push_active INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN sale_push_start_year INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN sale_push_start_week INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN sale_push_last_offer_year INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN sale_push_refused_count INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN sale_push_low_score_weeks INTEGER DEFAULT 0",
        # [2026-07 신설, 신민용 리포트: "맨체스터 유나이티드가 오퍼를
        # 3461억 → 3343억으로 오히려 낮춰서 다시 제안한다 — 무슨 논리냐"]
        # 예전엔 오퍼 금액(offer_premium_mult)이 매번 완전히 새로 랜덤
        # 굴려져서, 같은 팀이 두 번째로 관심을 보일 때도 이전 제안액과
        # 전혀 무관하게(심지어 더 낮게) 나올 수 있었다 — 실제 이적 협상은
        # 거절당하면 보통 같거나 더 올려서 재접촉하지 낮춰 부르지 않는다.
        # 팀별 마지막 제안액을 기억해뒀다가, 같은 팀이 다시 제안할 때
        # 그 금액 밑으로는 안 내려가게 한다(game_engine._build_offer 참고).
        "ALTER TABLE my_player ADD COLUMN offer_history_json TEXT DEFAULT '{}'",
        # [2026-07 신설, 국제대회 일 단위 전환 Phase 1]
        # intl_matches/cl_matches/cup_matches는 이미 위쪽(862번 줄 부근)
        # 마이그레이션에서 day 컬럼이 DEFAULT 0으로 추가돼 있다 — 단,
        # 그건 "내 경기"의 표시용 날짜만 채우는 용도였고(_really_mine일
        # 때만 _week_intl_cl_day() 결과 저장, 그 외 AI-vs-AI 경기는 전부
        # 0으로 남음) 이번 국제대회 일정 시스템 전환 목적과는 다르다.
        # cwc_matches만 그 마이그레이션 대상에서 빠져 있었으므로 여기서
        # 추가한다 — 다른 셋과 동일하게 nullable로, 아직 NOT NULL 걸지 않음.
        # (Phase 1.5에서 기존 intl/cl/cup의 "0=미계산" 센티널을 NULL로
        # 재해석하고, Phase 2 생성기가 모든 경기에 실제 day를 채우는
        # 순서를 반드시 지킨다 — day 값 범위가 1~364라 0은 항상 무효값이므로
        # 0→NULL 재해석은 데이터 손실 없이 안전하다.)
        "ALTER TABLE cwc_matches ADD COLUMN day INTEGER",
        # [2026-08 신설, 신민용 확정: 동적 팀 강도(club_strength) 시스템]
        # "명문팀 = 영원히 명문"인 정적 하드코딩(data/prestige_clubs.py)
        # 대신, 시즌 성적에 따라 오르내리는 값을 팀마다 따로 저장한다.
        # 경기 계산에서만 쓰이고(_team_avg_ovr) DB에 저장된 개별 선수
        # OVR·화면 표시는 건드리지 않는다 — PRESTIGE_MATCH_BONUS와 같은
        # 성격의 '경기 시뮬 전용 보너스'인데, 고정값이 아니라 실제 성과로
        # 누적/쇠퇴한다는 점만 다르다. 초기값은 new_game 시 프레스티지
        # 등급으로 시딩(claude_seed_club_strength 참고), 이후 시즌마다
        # update_club_strength_after_season()이 갱신한다.
        "ALTER TABLE teams ADD COLUMN club_strength REAL DEFAULT 0",
        # [2026-08 신설, 신민용 확정: club_momentum 시스템] 강등 직후/국제대회
        # 우승 직후처럼 "이번 이벤트로 팀 체급이 갑자기 흔들리거나 급등하지
        # 않게" 완충하는 범용 장치. momentum_type이 어떤 스케줄
        # (constants.MOMENTUM_SCHEDULES)을 쓸지 정하고, momentum_seasons_left가
        # 카운트다운(매 시즌 1씩 감소, 0이면 스케줄 종료 → 정상 감쇠로 복귀).
        # 이벤트가 겹치면 가장 최근 이벤트가 이전 것을 덮어쓴다(단순화).
        "ALTER TABLE teams ADD COLUMN momentum_type TEXT DEFAULT ''",
        "ALTER TABLE teams ADD COLUMN momentum_seasons_left INTEGER DEFAULT 0",
        # [2026-08 v3.3 신설, 신민용+검토 확정: "연속 강등 가속 방지"]
        # 직전 시즌에도 강등당했는지를 추적해서, 연속으로 떨어지는 팀에게
        # 더 강한 회복 모멘텀을 준다(_process_promotion_relegation 참고).
        # 강등되면 +1, 강등을 면하면(잔류/승격) 0으로 리셋.
        "ALTER TABLE teams ADD COLUMN relegation_streak INTEGER DEFAULT 0",
        # [2026-08 v3.3 신설, 신민용 지적: "재계약 여부가 이번 시즌 OVR
        # 스냅샷 하나로만 기계적으로 결정된다 — 근속·과거 기여도도 봐야
        # 한다"] 이 클럽에서 연속으로 뛴 시즌 수. join_team()에서 새 팀
        # 합류 시 1로 리셋되고, _end_of_season에서 계약이 끊기지 않고
        # 그대로 남아있으면 +1(game_engine.py 참고).
        "ALTER TABLE my_player ADD COLUMN club_tenure_seasons INTEGER DEFAULT 1",
        # [2026-08 신설, 신민용 요청: "레드카드 기록 추가"] 레드카드(퇴장)는
        # 이미 경기별로 발생은 하지만(_roll_red_card/_apply_red_card_dismissal),
        # 누적 횟수를 어디에도 세어두지 않았다 — 커리어/은퇴창에 "전체 M회
        # (그중 리그 N회)"로 보여주기 위해 두 카운터를 추가한다.
        # total_red_cards_league: 리그 경기에서만 받은 누적 퇴장 횟수
        #   (career_entries.red_cards로 시즌/재직 단위로도 스냅샷됨 — 아래
        #   career_entries 마이그레이션 참고).
        # total_red_cards_all: 리그+컵+챔스+클럽월드컵+국가대표+승강PO를
        #   전부 합친 커리어 통산 퇴장 횟수 ("전체 기록" 표시용).
        "ALTER TABLE my_player ADD COLUMN total_red_cards_league INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_red_cards_all INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_red_cards_league INTEGER DEFAULT 0",
        # [2026-08 신설, 신민용 요청: "새 선수 생성 때 시작 연도/나이를 고를
        # 수 있게 — 입단은 무조건 선택한 나이+1부터"] MIN_JOIN_AGE가 그동안
        # 전역 고정 상수(17)라 모든 선수에게 똑같이 적용됐는데, 시작 나이가
        # 선택 가능해지면 선수마다 "입단 가능 나이"가 달라져야 한다(14세
        # 시작이면 15세부터, 28세 시작이면 29세부터). 이 값을 선수별로
        # 저장해서 ui/center_panel.py가 전역 상수 대신 이걸 쓰게 한다.
        # 기본값 17은 기존 상수(PLAYER_START_AGE 16 + 1)와 완전히 동일 —
        # 옛 세이브를 불러와도 동작이 안 바뀐다.
        "ALTER TABLE my_player ADD COLUMN min_join_age INTEGER DEFAULT 17",
        # career_entries(팀 재직 기간별 리그 기록)에도 같은 패턴(saves,
        # clean_sheets 등)으로 그 재직 기간 동안의 리그 레드카드 수를 남긴다.
        "ALTER TABLE career_entries ADD COLUMN red_cards INTEGER DEFAULT 0",
        # [2026-08 신설, 옐로카드 시스템] red_cards와 동일 패턴 — 재직
        # 기간 동안의 리그 전용 옐로카드 누적(season_yellow_league)을
        # 재직 종료/갱신 시점에 스냅샷.
        "ALTER TABLE career_entries ADD COLUMN yellow_cards INTEGER DEFAULT 0",
        # 대회별 개인 경기 기록에 "이 경기에서 옐로카드를 몇 장 받았는지"
        # (0/1/2) 저장 — my_absence_reason만으로는 "그냥 옐로 1장 받고
        # 계속 뛴 경기"를 구분할 수 없어서(퇴장이 아니라 결장 사유 자체가
        # 없음) 별도 컬럼 신설. '전체 이력' 탭(get_full_history_extras_
        # for_period)의 기간별 옐로 합산에 쓰인다.
        "ALTER TABLE cl_matches ADD COLUMN my_yellow_cards INTEGER DEFAULT 0",
        "ALTER TABLE el_matches ADD COLUMN my_yellow_cards INTEGER DEFAULT 0",
        "ALTER TABLE ecl_matches ADD COLUMN my_yellow_cards INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN my_yellow_cards INTEGER DEFAULT 0",
        "ALTER TABLE sc_matches ADD COLUMN my_yellow_cards INTEGER DEFAULT 0",
        "ALTER TABLE cwc_matches ADD COLUMN my_yellow_cards INTEGER DEFAULT 0",
        "ALTER TABLE intl_matches ADD COLUMN my_yellow_cards INTEGER DEFAULT 0",
        # [2026-08 신설, 신민용 요청: "승강PO도 팀 이력에는 아니더라도
        # 전체 이력에는 포함되어야지"] po_matches는 슛/드리블 등 세부
        # 스탯 컬럼 자체가 원래 없는 얕은 테이블이라(위 설계 코멘트 참고)
        # 다른 6개 대회 테이블과 동일한 컬럼 세트를 맞출 순 없지만, 카드
        # 기록만은 동일 패턴으로 추가해 get_full_history_extras_for_period가
        # PO도 집계할 수 있게 한다.
        "ALTER TABLE po_matches ADD COLUMN my_absence_reason TEXT DEFAULT NULL",
        "ALTER TABLE po_matches ADD COLUMN my_yellow_cards INTEGER DEFAULT 0",
        # [2026-08 신설, 신민용 설계 확정: "컵대회 본선은 실제 참가 가능
        # 팀 수 기준으로 통일한다"] 예전엔 "합류할 티어가 다 떨어진 시점에
        # 마침 몇 팀이 남아있는가"로 표준 강수(8/16/32/64강)가 정해졌다 —
        # 그런데 그 "마침 남은 인원"은 그 전까지 각 예선 라운드에서 그냥
        # 절반씩 걸러낸 결과일 뿐이라, 전체 참가 가능 팀 수(모든 참가
        # 티어 합계)가 충분히 많아도(예: 대한민국 4~5부까지 합쳐 수십 팀)
        # 마지막에 우연히 16 밑으로 떨어지면 8강부터 시작해버렸다. 이제
        # 대회 개막 시점에 "이 대회에 총 몇 팀이 참가할 것인가"(모든
        # 참가 티어의 팀 수 합계)를 미리 계산해 여기 저장하고, 그 값을
        # 기준으로 표준 강수(_cup_bye_count의 cap)를 한 번 정해서
        # 예선 라운드 내내 그 강수로 수렴시킨다(cup_engine.py 참고) —
        # "마지막에 몇 명 남았는지"가 아니라 "원래 몇 팀이 있었는지"로
        # 강수가 정해지게 한다.
        "ALTER TABLE cup_tournaments ADD COLUMN standard_bracket_size INTEGER DEFAULT 0",
        # [2026-08 신설, 골 시상 시스템] awards에 골 이벤트 연결 + 표시용
        # 캐시(week/match_info) — goal_events와 조인 안 하고 상 목록 화면을
        # 바로 그리기 위한 캐시(league_season_standings와 같은 사전계산
        # 철학). goal_event_id는 골 관련 상(원더골 계열)에서만 채워지고,
        # 발롱도르/득점왕 등 기존 상들은 0으로 그대로 남는다.
        "ALTER TABLE awards ADD COLUMN goal_event_id INTEGER DEFAULT 0",
        "ALTER TABLE awards ADD COLUMN week INTEGER DEFAULT 0",
        "ALTER TABLE awards ADD COLUMN match_info TEXT DEFAULT ''",
        # [2026-08 신설, 시즌 성적 기반 이적시장 평가 15-2-A] 결장 사유별
        # 시즌 누적 카운터 3개 — 부상/징계/벤치를 하나로 뭉쳐 세던 것을
        # 분리한다. 각각 의미가 다르다(부상=가용성 리스크, 징계=규율
        # 리스크, 벤치=전력외 판정용). _simulate_match()에서 매 경기
        # 결장 사유 판정 시(이미 _suspended/benched/injured 세 플래그로
        # 구분돼 있음) 누적하고, 시즌 시작 시 0으로 초기화한다.
        "ALTER TABLE my_player ADD COLUMN season_injury_matches_missed INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_suspension_matches_missed INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_bench_matches_missed INTEGER DEFAULT 0",
        # [2026-08 신설, 신민용 리포트: "에이전트 창을 열 때마다 후보가
        # 바뀌는 게 비현실적이다 — 비시즌 갱신 전까지는 같은 후보가
        # 유지돼야 한다"] 에이전트 오퍼 풀을 JSON으로 저장해두고, 갱신
        # 시즌(agent_offer_season)이 현재 시즌과 같으면 저장된 풀을 그대로
        # 재사용한다. 입단을 나갔다 다시 들어와도(계약만료→FA→재입단) 같은
        # 시즌 안이면 동일한 풀이 유지되고, 비시즌(오프시즌) 전환 시점에만
        # 새로 생성한다.
        "ALTER TABLE my_player ADD COLUMN agent_offer_pool_json TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN agent_offer_season INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN agent_offer_year INTEGER DEFAULT 0",
        # [2026-08 신설] 선택한 에이전트의 전문 대륙 — 그 대륙 소속 팀
        # 오퍼/입단 판정에 소폭 보너스를 준다(agent_window._select에서
        # 함께 저장).
        "ALTER TABLE my_player ADD COLUMN agent_continent TEXT DEFAULT ''",
        # [2026-08 신설, 2단계: 판매 압박 지표 분리, 신민용+GPT 검토 확정]
        # sale_push_refused_count는 "이번 판매추진 사이클 내" 카운터라
        # 사이클이 끝나면 0으로 리셋된다 — 반면 club_sale_pressure는
        # 사이클을 넘나들며 누적되는 "구단이 이 선수를 계속 거부당한
        # 것에 대해 얼마나 인내심을 잃었는가" 장기 지표. 둘을 하나의
        # 변수에 욱여넣으면 사이클 종료 시 리셋할 때 장기 압박까지 같이
        # 날아가버리는 문제가 생기므로 분리한다.
        "ALTER TABLE my_player ADD COLUMN club_sale_pressure INTEGER DEFAULT 0",
        # [2026-08 신설, 3단계: 판매추진 전용 UI + 예약이적, 신민용+GPT
        # 검토 확정] 판매추진은 이제 "구단이 판매안을 만들어 플레이어에게
        # 승인 요청하는" 완전히 별도 거래 형태 — 기존 pending_offer_state
        # (OfferWindow 전용 스키마)를 재활용하지 않고 독립 필드로 관리.
        "ALTER TABLE my_player ADD COLUMN sale_push_proposal_json TEXT DEFAULT ''",
        "ALTER TABLE my_player ADD COLUMN sale_push_next_proposal_year INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN sale_push_next_proposal_week INTEGER DEFAULT 0",
        # 수락 시 즉시 이적하지 않고 "예약" 상태로 저장 — 다음 비시즌
        # 전환 시점(_end_of_season)에 실제 이적을 실행한다.
        "ALTER TABLE my_player ADD COLUMN pending_sale_transfer_json TEXT DEFAULT ''",
        # [2026-08 신설, 신민용 요청: "클럽월드컵 도중 소속이 바뀌면 오류날
        # 수 있다 — 입단/오퍟 수락도 시즌 시작 순간(상반기 4주차/하반기
        # 시작 주차)에 실제로 반영되게 하자"] pending_sale_transfer_json과
        # 완전히 동일한 "예약 이적" 패턴 — 오퍟/입단을 수락해도 join_team()을
        # 즉시 부르지 않고 여기에 저장해뒀다가, _advance_week가 다음
        # 시즌구간 시작 주차에 진입하는 순간 실행한다. 같은 필드를 덮어쓰는
        # 구조라 그 사이 더 좋은 오퍟를 새로 수락하면 자연히 "가장 마지막
        # 선택"으로 교체된다.
        "ALTER TABLE my_player ADD COLUMN pending_join_transfer_json TEXT DEFAULT ''",
        # [2026-08 신설, 옐로카드 시스템] 레드카드와 동일한 "대회별 결장
        # 카운터" 패턴을 슈퍼컵/월드컵예선에도 추가 — 지금까지 슈퍼컵은
        # cl_suspension을 챔스/유로파/컨퍼런스와 그대로 같이 썼고(참가팀이
        # 안 겹친다는 가정이 슈퍼컵엔 안 맞음), 월드컵예선은 intl_suspension을
        # 본선과 같이 써서 "예선 마지막 경기 퇴장이 본선 첫 경기 결장으로
        # 이어지는" 버그가 있었다 — 둘 다 분리.
        "ALTER TABLE my_player ADD COLUMN super_cup_suspension INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN wc_qual_suspension INTEGER DEFAULT 0",
        # 대회 그룹별 "시즌(또는 대회 사이클) 누적 경고" — 5장 도달 시
        # 위 결장 카운터 필드를 1로 세팅하고 0으로 리셋한다. 클럽 계열
        # (league/cup/europe/super_cup/cwc/po)은 매 시즌 리셋, 국가대표
        # 계열(wc_qual/intl)은 "대회 사이클 종료" 시점에 리셋한다(연도
        # 기준 리셋이 아님 — intl_engine.py의 예선→본선 전환 지점 참고).
        "ALTER TABLE my_player ADD COLUMN season_yellow_league INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_yellow_cup INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_yellow_europe INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_yellow_super_cup INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_yellow_cwc INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_yellow_wc_qual INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_yellow_intl INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN season_yellow_po INTEGER DEFAULT 0",
        # [2026-08 버그수정, 신민용 리포트: "팀 이력에 옐로카드가 갑자기
        # 0이 됐다(8월인데 시즌 리셋도 아닌데)"] 원인: season_yellow_*를
        # "이번 시즌 총 받은 옐로 수"(표시용)와 "5장 채우면 다음경기
        # 결장+0리셋"(징계 판정용 진행 카운터) 두 용도로 같이 썼다 —
        # 5장 채워서 정지가 걸리는 순간 표시값까지 같이 0으로 날아갔다.
        # season_yellow_*는 이제 "표시 전용"(카드 받을 때마다 계속
        # 누적, 시즌 끝날 때만 리셋)으로 남기고, 5장 문턱 판정은 아래
        # 별도 진행 카운터(yellow_susp_progress_*)로 분리한다 — 이
        # 필드만 5장 도달 시 0으로 리셋된다.
        "ALTER TABLE my_player ADD COLUMN yellow_susp_progress_league INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN yellow_susp_progress_cup INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN yellow_susp_progress_europe INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN yellow_susp_progress_super_cup INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN yellow_susp_progress_cwc INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN yellow_susp_progress_wc_qual INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN yellow_susp_progress_intl INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN yellow_susp_progress_po INTEGER DEFAULT 0",
        # 통산(커리어) 누적 — total_red_cards_all/total_red_cards_league와
        # 동일 패턴. 2차 옐로(경고누적 퇴장)도 여기엔 정상적으로 +1씩
        # 반영된다(시즌 누적 징계 카운터에만 안 얹을 뿐).
        "ALTER TABLE my_player ADD COLUMN total_yellow_all INTEGER DEFAULT 0",
        "ALTER TABLE my_player ADD COLUMN total_yellow_league INTEGER DEFAULT 0",
        # [2026-08 신설, 부상 시스템 확장] 부상 부위를 이름 문자열에서 추측하지
        # 않고 실제로 저장한다 — injury_detail(부상명)/injury_type(등급)과
        # 별개 축. 값은 신체 실루엣 zone 키(예: 'l_knee', 'neck', 'r_hand')
        # 그대로 저장해 ui/player_panel.py가 그대로 갖다 쓸 수 있게 한다.
        "ALTER TABLE my_player ADD COLUMN injury_body_part TEXT DEFAULT ''",
        # [2026-08 신설, 부상 시스템 확장 2단계] 신체 부담(injury_load) —
        # 스트레스와 같은 원리(0~100)로 훈련/경기로 쌓이고 휴식으로
        # 줄어들지만, 스트레스보다 천천히 빠지는 별개의 장기 누적 축.
        # game_engine._process_training/_simulate_match 참고.
        "ALTER TABLE my_player ADD COLUMN injury_load INTEGER DEFAULT 0",
    ]:
        # [정리] bare except → sqlite3.OperationalError로 좁힘.
        # (ALTER TABLE 재실행 시 "duplicate column" 등 예상된 실패만 무시하고,
        #  그 외 진짜 버그로 인한 예외는 숨기지 않는다. 동작은 기존과 동일.)
        try: c.execute(migration)
        except sqlite3.OperationalError: pass

    # [2026-07 신설, 국제대회 일 단위 전환 Phase 1.5] intl_matches/cl_matches/
    # cup_matches의 day는 기존에 "내 경기가 아니면 0"으로 채워져 있었다
    # (AI vs AI 경기가 한 라운드에 수백~수천 건이라 _week_intl_cl_day() 호출
    # 자체를 스킵하는 성능 최적화였음 — intl_engine.py/champions_engine.py/
    # cup_engine.py의 _sim_ai_match 계열 함수 참고). 0은 day 범위(1~364)에서
    # 항상 무효값이라 "0번째 날"과 "미계산"을 구분 못 하는 센티널로 오용되고
    # 있었던 셈이다. 위 세 함수는 이제 0 대신 None을 쓰도록 고쳐서 앞으로는
    # 이 문제가 재발하지 않지만, 기존에 이미 저장된 행들은 여전히 0으로
    # 남아있으므로 여기서 한 번 NULL로 재해석한다. day=0인 행이 이미 없으면
    # UPDATE 0행이라 사실상 무비용 — 매번 실행해도 안전(멱등).
    for _tbl in ("intl_matches", "cl_matches", "cup_matches"):
        try:
            c.execute(f"UPDATE {_tbl} SET day = NULL WHERE day = 0")
        except sqlite3.OperationalError:
            pass

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
    # season_state.current_day가 방금 바뀌었을 수 있으므로 get_state() 캐시를
    # 비운다(초기화 시점이라 보통 비어있지만, 방어적으로).
    try:
        import game_engine
        game_engine._invalidate_state_cache()
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

    # ── [버그수정] teams.formation 스냅샷 테이블 (새 게임 리셋용) ─────────
    # _shuffle_formations()(ai_lifecycle.py)가 시즌마다 팀의 ~20%를 랜덤하게
    # 포메이션 변경하는데, reset_game_data()가 이를 안 건드려서 은퇴 후 새
    # 게임을 해도 직전 플레이 말미의 포메이션이 그대로 남아있던 문제가 있었다
    # (ai_players_seed와 같은 유형의 "리셋 함수가 일부만 리셋" 버그).
    # 최초 시딩 직후(변형되기 전) 상태를 여기 스냅샷해두고, 새 게임 시 여기서
    # 복원한다.
    c.execute("""CREATE TABLE IF NOT EXISTS team_formation_seed(
        team_id INTEGER PRIMARY KEY, formation TEXT)""")

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
        # [2026-07 신설, 신민용 리포트: "49~50주에 렉이 6~7초씩 걸린다"]
        # get_country_squad_players._fill()이 "WHERE nationality=? AND
        # position=? ORDER BY ovr DESC LIMIT 1" 쿼리를 국가당 포지션 수(최대
        # 11개)만큼, 그것도 국적 태그/자국리그/대륙 3단계로 최대 33번까지
        # 반복한다. 이 조합 인덱스가 없어서 매번 ai_players 전체(10만+ 행)를
        # 스캔+정렬하고 있었다 — cProfile로 실측한 결과 쿼리 1개당 평균
        # 24ms, 국제대회 예선 마감 주차(20개국 동시 처리)에 417개 쿼리가
        # 몰려 10초 이상 걸리는 게 확인됨. 이 인덱스로 조건에 맞는 행만
        # 바로 찾아 정렬 없이(ovr DESC를 인덱스 순서로 커버) 가져온다.
        "CREATE INDEX IF NOT EXISTS idx_aiplayers_nat_pos_ovr ON ai_players(nationality, position, ovr DESC)",
        "CREATE INDEX IF NOT EXISTS idx_mr_week_season   ON match_results(week, season)",
        "CREATE INDEX IF NOT EXISTS idx_mr_league_season ON match_results(league_id, season)",
        "CREATE INDEX IF NOT EXISTS idx_mr_day_season    ON match_results(day, season)",
        "CREATE INDEX IF NOT EXISTS idx_teams_league     ON teams(league_id)",
        "CREATE INDEX IF NOT EXISTS idx_leagues_country  ON leagues(country_id)",
        # intl/cl 경기 조회: tournament_id+week 복합 (매 주차 process_*_week 호출마다 사용)
        "CREATE INDEX IF NOT EXISTS idx_intl_matches_tid_week ON intl_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_intl_entries_tid      ON intl_entries(tournament_id)",
        "CREATE INDEX IF NOT EXISTS idx_intl_matches_my ON intl_matches(is_my)",
        "CREATE INDEX IF NOT EXISTS idx_cl_matches_tid_week   ON cl_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_cl_entries_tid        ON cl_entries(tournament_id)",
        "CREATE INDEX IF NOT EXISTS idx_cl_matches_my ON cl_matches(is_my)",
        # [2026-08 추가, 신민용 리포트: "1년씩 돌리는데 전보다 느려졌다"]
        # el_*(유로파급)/ecl_*(컨퍼런스급)는 cl_*와 완전히 동일한 스키마로
        # 복제됐고 competition_common.py가 cl_*와 똑같은 쿼리 패턴(tournament_id+week
        # 조회, tournament_id+team_id 조회, is_my=1 조회)을 그대로 쓰는데,
        # 정작 위 cl_matches/cl_entries 인덱스 3개가 el_/ecl_ 쪽엔 하나도
        # 안 만들어져 있었다 — 챔스만 있던 시절엔 안 보이던 문제가, 유로파/
        # 컨퍼런스로 범위가 넓어지면서(같은 코드 경로가 3배로 호출되는데
        # 그중 2/3는 인덱스가 없는 채로) 누적된 것으로 보인다.
        "CREATE INDEX IF NOT EXISTS idx_el_matches_tid_week   ON el_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_el_entries_tid        ON el_entries(tournament_id)",
        "CREATE INDEX IF NOT EXISTS idx_el_matches_my ON el_matches(is_my)",
        "CREATE INDEX IF NOT EXISTS idx_ecl_matches_tid_week  ON ecl_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_ecl_entries_tid       ON ecl_entries(tournament_id)",
        "CREATE INDEX IF NOT EXISTS idx_ecl_matches_my ON ecl_matches(is_my)",
        # [2026-08 신설, 10순위 슈퍼컵 — 위 el_/ecl_ 인덱스 누락 버그를
        # 처음부터 반복하지 않으려고 sc_*도 처음부터 같이 만든다.
        "CREATE INDEX IF NOT EXISTS idx_sc_matches_tid_week   ON sc_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_sc_entries_tid        ON sc_entries(tournament_id)",
        "CREATE INDEX IF NOT EXISTS idx_sc_matches_my ON sc_matches(is_my)",
        "CREATE INDEX IF NOT EXISTS idx_cup_matches_tid_week  ON cup_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_cup_entries_tid       ON cup_entries(tournament_id)",
        # [2026-08 추가, 신민용 리포트: "재능 좋은 선수로 오래 뛰면 은퇴/
        # 커리어창이 심하게 렉걸린다"] get_my_cl/cup/cwc/intl_matches()가
        # "WHERE is_my=1"로 각 대회 매치 테이블을 조회하는데 is_my 컬럼에
        # 인덱스가 없어 매번 테이블 전체(월드 전체 누적, cup_matches만도
        # 12시즌 만에 12만행+)를 풀스캔했다 — 이 테이블들은 match_results와
        # 달리 시즌마다 정리되지 않고 세이브 시작부터 계속 쌓이므로, 커리어가
        # 길어질수록(=재능이 좋아 오래 뛸수록) 이 풀스캔 비용이 계속 커지는
        # 구조였다. is_my는 선택도가 극히 높은 컬럼(수십만 행 중 내 경기
        # 수백 건)이라 단일 컬럼 인덱스만으로 SEARCH로 전환됨을 확인.
        "CREATE INDEX IF NOT EXISTS idx_cup_matches_my  ON cup_matches(is_my)",
        # get_my_cup_matches()에서 홈/원정 엔트리를 tournament_id+team_id로
        # 매번 개별 조회하는데(N+1), 기존엔 tournament_id만 인덱스가 있어
        # team_id 매칭은 그 안에서 선형탐색이었다 — 복합 인덱스로 완전
        # 인덱스 매칭이 되게 한다.
        "CREATE INDEX IF NOT EXISTS idx_cup_entries_tid_team  ON cup_entries(tournament_id, team_id)",
        # _calc_clean_sheets: season+home_score 로 미완료 경기 필터링
        "CREATE INDEX IF NOT EXISTS idx_mr_season_score ON match_results(season, home_score)",
        # match_results: home/away team_id 조회 (클린시트, 팀 경기 조회)
        "CREATE INDEX IF NOT EXISTS idx_mr_home_team ON match_results(home_team_id, season)",
        "CREATE INDEX IF NOT EXISTS idx_mr_away_team ON match_results(away_team_id, season)",

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
        "CREATE INDEX IF NOT EXISTS idx_cwc_matches_my ON cwc_matches(is_my)",
        # trophy_log: 승강제 처리(_process_promotion_relegation) 안에서
        # "WHERE year=? AND team_name=? AND tier=?"로 우승 중복 체크를 함 —
        # 작은 테이블이지만 저비용으로 미리 인덱싱.
        "CREATE INDEX IF NOT EXISTS idx_trophy_log_year_team ON trophy_log(year, team_name)",
        # [2026-08 신설, 신민용 리포트: "50살까지 하니(35시즌+) 렉걸린다"]
        # 실측(EXPLAIN QUERY PLAN)으로 확인된 풀스캔 3건 — 이 세 테이블은
        # 시즌마다 계속 불어나기만 하고(promotion_log 49,550행/35시즌,
        # po_matches 64,680행/35시즌, cup_matches 368,865행/35시즌) 인덱스가
        # 하나도 없어서, 매 조회가 테이블 전체를 훑었다. 시즌이 쌓일수록
        # 정확히 이 스캔 비용만큼 매년 조금씩 더 느려지는 구조.
        #   - promotion_log: _infer_ambition_from_last_season 등에서
        #     "WHERE team_name=? AND year=?"로 팀마다(이적시장 처리 중
        #     최대 10,000+ 팀) 조회 — 실측 50회 0.176s(인덱스 없음) →
        #     0.0005s(인덱스 후), 약 360배.
        "CREATE INDEX IF NOT EXISTS idx_promotion_log_team_year ON promotion_log(team_name, year)",
        #   - po_matches: process_po_week가 매주 활성 토너먼트마다(로그
        #     기준 400개 이상) "WHERE tournament_id IN (...)"로 조회 —
        #     실측 50회 0.123s → 0.0009s, 약 130배.
        "CREATE INDEX IF NOT EXISTS idx_po_matches_tournament ON po_matches(tournament_id)",
        # [2026-08 추가] po_tournaments만 위 cup/intl/cwc_tournaments와 달리
        # 인덱스가 하나도 없었다. process_po_week가 advance_days의 daily hook
        # 에서 "44주만"이 아니라 매일(연 364회) "WHERE year=? AND status!='done'"
        # 으로 조회하는데(promotion_playoff_engine.py process_po_week), po_matches와
        # 똑같이 시즌마다 계속 쌓이기만 하는 테이블이라(po_matches 64,680행/35시즌과
        # 동일 스케일) 쌓인 시즌 수에 정확히 비례해 이 매일 호출 비용이 커지는
        # 구조였다. 실측(35시즌 규모, daily-hook 364회 흉내): 인덱스 없음 0.198s →
        # 인덱스 후 0.031s, 약 6.3배(테이블이 더 쌓일수록 격차는 계속 벌어짐).
        "CREATE INDEX IF NOT EXISTS idx_po_tournaments_year_status ON po_tournaments(year, status)",
        #   - cup_matches: career_window/retire_window "전체 이력"
        #     탭(get_full_history_extras_for_period)이 재직 기간마다
        #     "WHERE home_team_id=? OR away_team_id=?"로 조회 — OR 조건은
        #     복합 인덱스 하나보다 컬럼별 단일 인덱스 2개를 따로 둬야
        #     SQLite가 MULTI-INDEX OR로 최적화한다(복합 인덱스 1개로
        #     테스트했을 때는 여전히 풀스캔이었음). 실측 50회 1.174s
        #     (인덱스 없음, 복합인덱스도 동일) → 0.021s(개별 인덱스 2개),
        #     약 55배.
        "CREATE INDEX IF NOT EXISTS idx_cup_matches_home ON cup_matches(home_team_id)",
        "CREATE INDEX IF NOT EXISTS idx_cup_matches_away ON cup_matches(away_team_id)",
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
    compact_existing_match_archive()   # 기존 세이브 match_results_archive 요약+정리 (1회성, 아래 참고)
    # [2026-08 신설] init_db()는 QA/AB테스트 스크립트 등이 DB_PATH를 바꿔가며
    # 같은 프로세스 안에서 여러 번 호출하기도 한다 — 그때마다 get_state()
    # 캐시가 이전 DB의 season_state를 그대로 들고 있으면 안 되므로 비운다.
    try:
        import game_engine
        game_engine._invalidate_state_cache()
    except Exception:
        pass


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


def _summarize_and_prune_archive(conn, seasons=None):
    """[2026-08 신설, 신민용 리포트: "게임.db가 너무 커져서 자동저장(2.3초)
    때문에 렉이 심하다"] match_results_archive의 (league_id,season) 조합별로
    팀 승/무/패/득실을 league_season_standings에 미리 계산해서 저장하고,
    "내 커리어에서 실제로 뛴 적 있는 팀"이 아닌 팀들의 원본 경기 행은
    지운다.

    - world_browser의 "역대 순위표"는 이제부터 league_season_standings를
      먼저 본다(game_engine.get_league_standings 참고) — 순위표 자체는
      원본 경기가 있든 없든 완전히 동일하게 보인다.
    - team_matches_played_in_window()처럼 "그 기간에 실제로 며칠에 경기가
      있었는지" 날짜 단위 원본이 필요한 건 career_entries.team_id(내가
      실제로 몸담았던 팀)에 한해서만이므로, 그 팀들의 원본 행은 그대로
      남긴다 — 이 함수가 지우는 건 "내 커리어와 무관한 나머지 9천여 개
      팀들"의 경기 원본뿐이다(대부분의 용량을 차지하지만 아무도 날짜
      단위로 다시 조회하지 않는 데이터).

    seasons를 넘기면 그 시즌들만(archive_old_seasons가 방금 옮긴 배치),
    None이면 아직 요약이 없는 모든 (league_id,season) 조합을 찾아 처리한다
    (기존에 이미 부풀어있는 세이브를 위한 1회성 전체 정리 — init_db에서
    meta 플래그로 딱 한 번만 실행)."""
    c = conn.cursor()

    if seasons is not None and not seasons:
        return
    season_filter = ""
    params: tuple = ()
    if seasons is not None:
        season_filter = f"AND season IN ({','.join('?' * len(seasons))})"
        params = tuple(seasons)

    # 1) 아직 요약이 없는 (league_id,season) 조합의 요약을 계산해 넣는다.
    # [2026-08 버그수정, 신민용 리포트: "요약INSERT루프만 갑자기 7초"]
    # 예전엔 이 단계를 리그별로 쪼개서 c.execute()를 (보통) 694번 따로
    # 호출했다 — 매번 (league_id,season) 두 값만 바뀌는 거의 동일한 쿼리를
    # 694번 반복 실행한 것. 실측 로그를 보면 같은 규모(리그 694개, 매 시즌
    # 거의 동일한 데이터량)인데도 어떤 시즌은 0.2~0.3초, 어떤 시즌은 7초대로
    # 튀는 등 데이터量과 무관하게 들쭉날쭉했다 — 694번의 개별 파라미터 쿼리를
    # SQLite 쿼리플래너가 그때그때 다르게(인덱스 사용 여부 등) 판단해서
    # 생기는 불안정성으로 보인다. 694번 호출 자체를 없애고 단일 INSERT...
    # SELECT...GROUP BY 쿼리 1번으로 합치면, 반복 호출에 의존하던 이 불안정성
    # 요인 자체가 사라진다(플래너가 한 번만 계획을 세우면 됨).
    _tp0 = time.perf_counter()
    todo = c.execute(
        f"""SELECT DISTINCT mra.league_id, mra.season FROM match_results_archive mra
            WHERE NOT EXISTS (
                SELECT 1 FROM league_season_standings lss
                WHERE lss.league_id=mra.league_id AND lss.season=mra.season)
            {season_filter}""", params).fetchall()
    _tp1 = time.perf_counter()
    if todo:
        not_exists_clause = """NOT EXISTS (
                SELECT 1 FROM league_season_standings lss
                WHERE lss.league_id=mra.league_id AND lss.season=mra.season)"""
        _bulk_params = (*params, *params) if params else ()
        c.execute(
            f"""INSERT OR IGNORE INTO league_season_standings
                   (league_id, season, year, team_id, wins, draws, losses, goals_for, goals_against)
               SELECT league_id, season, MAX(year), team_id,
                      SUM(CASE WHEN gf>ga THEN 1 ELSE 0 END),
                      SUM(CASE WHEN gf=ga THEN 1 ELSE 0 END),
                      SUM(CASE WHEN gf<ga THEN 1 ELSE 0 END),
                      SUM(gf), SUM(ga)
               FROM (
                   SELECT league_id, season, year, home_team_id AS team_id,
                          home_score AS gf, away_score AS ga
                   FROM match_results_archive mra
                   WHERE home_score>=0 {season_filter} AND {not_exists_clause}
                   UNION ALL
                   SELECT league_id, season, year, away_team_id AS team_id,
                          away_score AS gf, home_score AS ga
                   FROM match_results_archive mra
                   WHERE home_score>=0 {season_filter} AND {not_exists_clause}
               )
               GROUP BY league_id, season, team_id""",
            _bulk_params)
    _tp2 = time.perf_counter()

    # 2) 요약이 이미 있는 (league_id,season)에 한해, "내 커리어와 무관한 팀"의
    #    원본 경기 행을 지운다 — career_entries.team_id(내가 실제로 몸담았던
    #    팀들, 승강/이적과 무관하게 영구 불변값)만 원본을 보존한다.
    my_team_ids = {r["team_id"] for r in c.execute(
        "SELECT DISTINCT team_id FROM career_entries WHERE team_id != 0")}
    _tp3 = time.perf_counter()
    keep_clause = ""
    if my_team_ids:
        qmarks = ",".join("?" * len(my_team_ids))
        keep_clause = f"AND home_team_id NOT IN ({qmarks}) AND away_team_id NOT IN ({qmarks})"
    # [2026-08 버그수정, 신민용 리포트: "2년째부터 연도전환에 갑자기 7~8초"]
    # 아래 서브쿼리가 season_filter 없이 "SELECT league_id, season FROM
    # league_season_standings" 전체를 매번 다시 읽고 있었다 — 이 테이블은
    # 시즌이 지날 때마다 리그 수(694개)만큼 계속 불어나는데, 정작 이번
    # DELETE가 지우려는 대상은 방금 처리한 배치(seasons)뿐이다. 서브쿼리에도
    # 같은 season_filter를 걸어 "이번 배치 몫"만 비교하도록 좁힌다 — 시즌이
    # 쌓여도 서브쿼리 크기가 늘 일정해서 더 이상 해마다 느려지지 않는다.
    inner_season_filter = season_filter.replace("AND season", "WHERE season", 1) if season_filter else ""
    c.execute(
        f"""DELETE FROM match_results_archive
            WHERE (league_id, season) IN (
                SELECT league_id, season FROM league_season_standings {inner_season_filter})
            {keep_clause}
            {season_filter}""",
        (*params, *my_team_ids, *my_team_ids, *params) if my_team_ids else (*params, *params))
    _tp4 = time.perf_counter()
    conn.commit()
    _tp5 = time.perf_counter()
    print(f"[PERF]     _summarize_and_prune_archive 세부: todo조회 {_tp1-_tp0:.2f}s"
          f"({len(todo)}건) | 요약INSERT루프 {_tp2-_tp1:.2f}s | my_team조회 {_tp3-_tp2:.2f}s | "
          f"원본DELETE {_tp4-_tp3:.2f}s | commit {_tp5-_tp4:.2f}s")


def compact_existing_match_archive():
    """[2026-08 신설, 신민용 리포트: "20년대는 렉 때문에 플레이가 거의
    불가능"] archive_old_seasons()는 이제부터 시즌을 옮길 때마다 바로
    요약+정리하지만, 그건 '앞으로' 생기는 아카이브에만 적용된다 — 이미
    수백만 행이 쌓여있는 기존 세이브는 그대로다. 1회성으로 전체를 한 번
    훑어서 아직 요약이 없는 모든 (league_id,season)을 요약하고(1개월
    자동저장 배치가 아니라 한 번에 다 처리), 내 커리어와 무관한 팀들의
    원본 경기 행을 정리한다 — meta 플래그로 딱 한 번만 실행."""
    conn = get_conn()
    c = conn.cursor()
    try:
        row = c.execute("SELECT value FROM meta WHERE key='match_archive_compact_v1'").fetchone()
    except Exception:
        row = None
    if row:
        conn.close()
        return
    try:
        n_before = c.execute("SELECT COUNT(*) FROM match_results_archive").fetchone()[0]
        _summarize_and_prune_archive(conn, seasons=None)
        n_after = c.execute("SELECT COUNT(*) FROM match_results_archive").fetchone()[0]
        print(f"[MIGRATE] match_results_archive 1회성 정리: {n_before}행 → {n_after}행")
        # [2026-08] DELETE만으로는 페이지가 "재사용 가능"으로만 표시되고
        # 파일 자체는 안 줄어든다(그래서 flush_to_disk 백업 파일 크기도
        # 그대로였다) — VACUUM으로 실제 파일 크기까지 줄인다. 이 정리로
        # 지워지는 행 수가 워낙 커서(수백만 건) 여기서 한 번 VACUUM하는
        # 비용은 그만한 가치가 있다(실측: 3.46M행 정리 후 VACUUM 0.3초,
        # 이후 자동저장이 2.3초 → 0.3초로 빨라짐).
        conn.execute("VACUUM")
    except Exception as e:
        print("compact_existing_match_archive 오류:", e)
    c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('match_archive_compact_v1','1')")
    conn.commit()
    conn.close()



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
    멱등하게 동작한다.

    [2026-08 추가] 옮긴 직후 그 배치(seasons)만 바로 요약+정리
    (_summarize_and_prune_archive)한다 — 매 시즌 아주 조금씩만 처리하므로
    한 번에 몰아서 부담될 일이 없고, 그 덕분에 아카이브가 다시는 예전처럼
    무한정 부풀지 않는다."""
    conn = get_conn()
    c = conn.cursor()
    cols = "id,league_id,week,home_team_id,away_team_id,home_score,away_score,season,year,day"
    # [2026-08 계측 추가, 신민용 리포트: "연도전환 시 아카이브이동만 갑자기
    # 7~8초"] 여태 archive_old_seasons() 통짜 시간만 로그에 찍혀서, 그 안의
    # 어느 세부 단계(과거시즌 조회/이동INSERT/DELETE/요약+정리)가 실제
    # 병목인지 알 수 없었다. 기존 [PERF] 로그와 같은 스타일로 세부 타이머를
    # 추가한다 — 다음에 다시 느려지면 이 로그만으로 바로 원인 함수를 특정.
    _ta0 = time.perf_counter()
    seasons = [r["season"] for r in c.execute(
        "SELECT DISTINCT season FROM match_results WHERE season<?", (current_season,)).fetchall()]
    _ta1 = time.perf_counter()
    c.execute(
        f"""INSERT OR IGNORE INTO match_results_archive({cols})
            SELECT {cols} FROM match_results WHERE season<?""", (current_season,))
    _ta2 = time.perf_counter()
    c.execute("DELETE FROM match_results WHERE season<?", (current_season,))
    _ta3 = time.perf_counter()
    conn.commit()
    _ta4 = time.perf_counter()
    _tsp = 0.0
    if seasons:
        _tsp0 = time.perf_counter()
        _summarize_and_prune_archive(conn, seasons=seasons)
        _tsp = time.perf_counter() - _tsp0
    _tal = _prune_ai_transfer_log(conn, current_season)
    _tvac = _maybe_periodic_vacuum(conn)
    print(f"[PERF]   archive_old_seasons 세부: 과거시즌조회 {_ta1-_ta0:.2f}s | "
          f"아카이브INSERT {_ta2-_ta1:.2f}s | match_results DELETE {_ta3-_ta2:.2f}s | "
          f"commit {_ta4-_ta3:.2f}s | 요약+정리 {_tsp:.2f}s | ai_transfer_log정리 {_tal:.2f}s | "
          f"주기VACUUM {_tvac:.2f}s | (대상시즌 {seasons})")


# [2026-08 신설, 신민용 리포트: "50년 세이브 실측 — ai_transfer_log가
# 152만 행까지 쌓였다"] 이 테이블은 AI 이적 시장 처리(ai_lifecycle.py)가
# 매 시즌 계속 INSERT만 하는 로그성 테이블인데, 전체 코드베이스를 뒤져봐도
# 이걸 SELECT하는 곳이 단 한 곳도 없다 — UI/월드 기록실 어디에도 노출되지
# 않는 순수 기록용(디버그/추후 확장 대비)이라, match_results처럼 "역대
# 조회"를 위해 영구 보존해야 할 이유가 없다. 그렇다고 완전히 안 남기기엔
# 나중에 이적 시장 밸런스를 디버깅할 때 최근 몇 시즌 흐름은 참고할 수
# 있어야 하므로, 완전 삭제 대신 "최근 N시즌만" 유지하는 보존 정책으로
# 접근한다 — match_results/cup 계열과 달리 화면에 노출되는 값이 전혀
# 없으므로 요약 테이블 없이 오래된 행을 그냥 지워도 안전하다.
AI_TRANSFER_LOG_RETENTION_SEASONS = 5


def _prune_ai_transfer_log(conn, current_season) -> float:
    """ai_transfer_log에서 (current_season - 보존시즌수) 이전 행을 삭제.
    archive_old_seasons와 같은 타이밍(연도전환)에 호출되며, 반환값은
    소요 시간(초) — [PERF] 로그용."""
    _t0 = time.perf_counter()
    c = conn.cursor()
    _cutoff = current_season - AI_TRANSFER_LOG_RETENTION_SEASONS
    if _cutoff > 0:
        c.execute("DELETE FROM ai_transfer_log WHERE season<?", (_cutoff,))
        conn.commit()
    return time.perf_counter() - _t0


# [2026-08 신설, 신민용 리포트: "이거 길게 가면(한 세이브를 오래 플레이하면)
# 렉이 심해진다"] "새 게임" 반복 사이클(reset_game_data)만이 아니라, 한
# 세이브를 계속 이어가는 경우도 매 시즌 archive_old_seasons의 대량
# DELETE+INSERT가 쌓이면서 파일 내부 페이지 단편화가 생긴다 — 실측
# (10시즌 헤드리스 추적): match_results 단편화가 시즌1 14%→시즌4 43%까지
# 빠르게 오르고, 이후 시즌10까지는 34~40% 선에서 계속 오르내리며 정체된다
# (한 세이브 안에서는 "새 게임" 반복 때처럼 끝없이 계속 불어나진 않지만,
# 30~40%대에서 계속 머물러 있는 것 자체가 이미 상당한 성능 손실이다).
# 매 시즌 VACUUM을 돌리면 그 비용(이 DB 크기 기준 약 0.8~1초)이 매년
# 누적되어 배보다 배꼽이 커지므로, meta 테이블에 "마지막 VACUUM 이후
# 지난 시즌 수"를 세어뒀다가 VACUUM_SEASON_INTERVAL(5)시즌마다 한 번만
# 돌린다 — 단편화가 정체 구간(30~40%)에 도달하기 전에 주기적으로
# 털어내면서도, 매 시즌 비용을 추가하지 않는다.
VACUUM_SEASON_INTERVAL = 5


def _maybe_periodic_vacuum(conn) -> float:
    """archive_old_seasons()가 매 시즌 전환 시 호출. meta.seasons_since_vacuum
    카운터를 증가시키고, VACUUM_SEASON_INTERVAL에 도달하면 VACUUM을 돌리고
    카운터를 0으로 리셋한다. 반환값은 이번 호출에서 실제로 VACUUM을 도는 데
    걸린 시간(초) — 안 돌았으면 0.0(archive_old_seasons의 [PERF] 로그용)."""
    c = conn.cursor()
    row = c.execute("SELECT value FROM meta WHERE key='seasons_since_vacuum'").fetchone()
    try:
        n = int(row["value"]) + 1 if row and row["value"] else 1
    except (TypeError, ValueError):
        n = 1
    if n < VACUUM_SEASON_INTERVAL:
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('seasons_since_vacuum', ?)",
                  (str(n),))
        conn.commit()
        return 0.0
    _tv0 = time.perf_counter()
    conn.execute("VACUUM")
    _tv1 = time.perf_counter()
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('seasons_since_vacuum', '0')")
    conn.commit()
    return _tv1 - _tv0


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

    # [버그수정] 최초 시딩 직후(랜덤 배정 직후, 변형되기 전) 포메이션을
    # team_formation_seed에 스냅샷 — reset_game_data()가 이걸로 복원한다.
    # (원래도 랜덤 값이라 "정답"은 아니지만, ai_players_seed와 동일하게
    # "이번 세이브의 최초 상태"를 새 게임의 기준점으로 고정하는 목적)
    c.execute("DELETE FROM team_formation_seed")
    c.execute("INSERT INTO team_formation_seed(team_id, formation) SELECT id, formation FROM teams")

    # [2026-08 신설] 산하팀(리저브/B팀/유스팀) 분류 적용.
    # data/affiliate_raw.jsonl(GPT로 사전 분류·검증된 국가별 결과)을 읽어서
    # 방금 생성된 teams row들에 classification_status/parent_team_id를 채운다.
    # 이 시점에 해야 하는 이유: team_id가 실제로 배정된 직후라 이름 매칭이
    # 가장 정확하고, 아직 승강/시즌 진행이 전혀 없어 current_tier가
    # leagues.py 원본 tier와 100% 일치하는 상태이기 때문.
    _stage("산하팀 분류 적용", 1)
    try:
        from affiliate_classify import apply_classification
        apply_classification(c)
    except Exception as _e:
        print(f"[산하팀 분류 적용 오류] {_e}")
    if progress_cb: progress_cb("산하팀 분류 적용", 1, 1, "")

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

    # [2026-08 신설, 신민용 확정: 동적 팀 강도] 새 게임 최초 1회, 명문팀
    # 리스트를 club_strength 초기값으로 심는다. 이후로는 시즌마다
    # update_club_strength_after_season()이 실제 성적으로 이 값을
    # 계속 갱신하므로, 이 호출은 게임 시작 시 딱 한 번뿐이어야 한다.
    _stage("팀 위상 초기화", 1)
    try:
        seed_club_strength_from_prestige(conn)
    except Exception as _e:
        print(f"[club_strength 초기 시딩 오류] {_e}")
    if progress_cb: progress_cb("팀 위상 초기화", 1, 1, "")

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


def _regenerate_ai_players(c, progress_cb=None, skip_generation=False):
    """[버그수정 2026-08, 신민용 리포트: "새 게임을 눌러도 같은 game.db에서는
    초기 파워랭킹(팀 순위·PS 수치)이 매번 완전히 똑같다"]

    원인: _reset_ai_players_from_seed()가 '재생성'이 아니라 '복사'였다 —
    ai_players_seed는 그 game.db가 최초 설치되던 딱 한 번의 순간에 뽑힌
    난수 결과(weighted_team_order의 팀 강도 순번, _gen_ai_stats의 가우시안
    노이즈, age 삼각분포 등)를 그대로 얼려둔 스냅샷이다. '새 게임'을 누를
    때마다 이 스냅샷을 그대로 복사만 해왔기 때문에, 선수단 OVR이 항상
    설치 시점과 완전히 동일했고 — power_ranking.py의 초기 PS 공식
    (PS = 1400 + (OVR-60)×30 + 리그등급보정)이 순수하게 이 OVR에서만
    나오므로, 파생되는 파워랭킹 1~20위 클럽·수치까지 소수점 단위로
    똑같이 반복됐다(신민용 실측 리포트와 일치).

    이 스냅샷 복사 방식 자체는 원래 다른 버그를 고치려고 도입됐다
    (아래 _reset_ai_players_from_seed 주석 참고 — '새 게임'을 눌러도
    이전 플레이에서 은퇴·성장으로 변형된 선수가 안 지워지던 문제).
    그 목적(선수단을 깨끗한 상태로 되돌리기)은 '복사' 대신 '재생성'으로도
    똑같이 달성되고, 매번 새 random 시퀀스가 뽑히므로 이번 리포트까지
    같이 해결된다 — 그래서 새 게임 리셋 경로를 이 함수로 교체한다.

    성능: _generate_all_ai_players는 이미 국가별 이름풀 캐싱(팀당 개별
    쿼리 10,423회 → 210회) + 팀당 executemany 배치(11번 INSERT → 1번)로
    최적화돼 있지만, 실측 결과 전세계 선수단(약 12만 명) 재생성 자체에
    약 4~5초가 걸린다(스냅샷 복사 방식은 거의 즉시였던 것과 대비됨) —
    "새 게임 버튼 반응성에 큰 영향 없다"던 최초 판단은 실측으로 정정한다.
    그래서 progress_cb를 그대로 _generate_all_ai_players에 전달해 UI가
    진행 상황을 보여줄 수 있게 한다(ui/start_screen.py의
    NewPlayerDialog._regenerate_world_with_progress 참고 — reset_game_data()
    호출 시점 자체도 '새 게임' 클릭이 아니라 '생성'/'랜덤 생성' 클릭으로
    옮겨서, 진행률 창이 실제로 오래 걸리는 지점에서만 뜨게 했다).
    ai_players_seed 스냅샷/구버전 세이브 폴백 로직(_reset_ai_players_from_seed)은
    혹시 모를 다른 용도를 위해 그대로 남겨두되, '새 게임' 경로에서는 더
    이상 쓰지 않는다.

    skip_generation: [2026-08 추가, 그런데 최초 구현에 버그 있었음 — 신민용
    리포트: "새 게임 하면 정상인데 나갔다 들어오니 모든 팀이 950.0으로
    뜬다"] MainWindow.go_to_start()(은퇴 화면의 "시작 화면으로" 버튼)처럼
    '지금 당장 화면을 시작 메뉴로 바꾸는 것'이 목적이고 실제 새 선수단은
    필요 없는 호출 지점을 위한 옵션이다.

    [버그 원인] 최초 구현은 skip_generation=True일 때도 "c.execute(DELETE
    FROM ai_players)"는 그대로 실행하고 재생성만 건너뛰어서, 시작 메뉴로
    돌아간 뒤 다음 "새 게임"→"생성"을 누르기 전까지 ai_players가 완전히
    빈 테이블로 남는 구간이 생겼다. 그런데 그 사이에 세계 파워랭킹 화면이
    열려 있거나(별도 창이라 시작메뉴로 안 닫힘) 다시 열리면
    ensure_initial_team_power_ranking()이 그 빈 테이블 기준으로 순위를
    계산해버린다 — _team_avg_ovr_seed()가 선수가 0명인 팀엔 폴백값 45.0을
    돌려주고, PS = 1400 + (45-60)×30 + 리그등급보정 = 950 + 리그등급보정
    인데 리그등급보정도 전 리그가 동일(45.0)해서 0이 되니 정확히 전 세계
    모든 팀이 950.0으로 동률 처리된다(신민용이 실측으로 보고한 수치와
    정확히 일치). 게다가 이 값이 team_power_rankings에 그대로 INSERT돼
    캐시되므로, 그 사이에 화면을 열어본 것만으로 오염된 값이 저장된다.

    [수정] "지금 당장 필요 없다"는 것과 "완전히 비워도 안전하다"는 것은
    다른 얘기였다 — 재생성(_generate_all_ai_players, ~5초)만 건너뛰고,
    DELETE도 하지 않는다. 대신 직전 플레이의 선수단이 그대로 남아있게
    되는데, 이건 화면이 시작 메뉴로 바뀐 뒤 실제로 "새 게임"→"생성"/
    "랜덤 생성"을 누르는 순간 어차피 (skip 없는) reset_game_data()가 다시
    호출되어 완전히 새로 재생성되므로 최종 결과에는 전혀 영향이 없다.
    그 사이 짧은 구간에 어쩌다 파워랭킹 화면이 열려도, 이제는 "완전히
    빈 테이블의 폴백값"이 아니라 "직전 플레이의 실제 OVR"을 기준으로
    계산되므로 최소한 950.0 균등 오염 같은 명백히 깨진 값은 나오지
    않는다."""
    if not skip_generation:
        c.execute("DELETE FROM ai_players")
        _generate_all_ai_players(c, progress_cb=progress_cb)


def _reset_formations_from_seed(c):
    """[버그수정] '새 게임' 시 teams.formation을 최초 시딩 상태로 복원.
    _shuffle_formations()가 시즌마다 팀의 ~20%를 랜덤 변경해도 reset_game_data()가
    이를 되돌리지 않아, 은퇴 후 새 게임을 해도 직전 플레이 말미의 포메이션이
    그대로 남아있던 문제를 고친다.
    [구버전 세이브 폴백] team_formation_seed가 비어있으면(이 패치 이전 세이브라
    스냅샷을 못 떠둔 상태) 복원할 데이터가 없으므로, 지금의 teams.formation 상태를
    그대로 시드로 확정해둔다 — ai_players_seed 폴백과 동일한 패턴. 그 판의
    '새 게임'은 1회에 한해 기존 동작(리셋 안 됨)과 같지만, 다음 '새 게임'부터는
    정상적으로 이번에 확정된 시드로 복원된다.
    """
    seed_cnt = c.execute("SELECT COUNT(*) c FROM team_formation_seed").fetchone()["c"]
    if seed_cnt == 0:
        c.execute("INSERT INTO team_formation_seed(team_id, formation) SELECT id, formation FROM teams")
        return
    c.execute("""UPDATE teams SET formation = (
                    SELECT formation FROM team_formation_seed
                    WHERE team_formation_seed.team_id = teams.id)
                 WHERE id IN (SELECT team_id FROM team_formation_seed)""")


def _reset_club_strength(c):
    """[버그수정] '새 게임' 시 teams.club_strength(동적 팀 강도)를 최초 명문팀
    시딩 상태로 복원. update_club_strength_after_season()이 매 시즌 실제 성적으로
    이 값을 계속 갱신하는데, reset_game_data()가 이를 안 건드려서 은퇴 후 새
    게임을 해도 직전 플레이에서 여러 시즌 누적된 club_strength가 그대로 남아
    매치 가중치 계산에 영향을 주고 있었다.
    seed_club_strength_from_prestige()는 "이미 0이 아닌 팀은 건드리지 않는다"는
    가드가 있어(기존 세이브에 안전하게 재적용하기 위함), 재시딩 전에 전부 0으로
    비워야 한다 — 그러면 최초 설치 때와 동일하게 명문팀 리스트에 있는 팀만
    시드 값으로 채워지고, 나머지는 0(=시즌 진행 전 초기 상태)으로 남는다.
    """
    c.execute("UPDATE teams SET club_strength = 0")
    seed_club_strength_from_prestige(c.connection)


def reset_game_data(progress_cb=None, skip_ai_regen=False):
    """progress_cb(done:int, total:int, detail:str) — 선수단 재생성
    (_regenerate_ai_players, 전체 소요시간의 대부분을 차지) 단계에서만
    호출된다. 콜백이 없으면(None) 기존과 동일하게 동작한다.

    skip_ai_regen: [2026-08 추가, 신민용 리포트: "은퇴 후 '시작 화면으로'를
    누르면 진행률 창도 없이 5초 정도 멈춘다"] MainWindow.go_to_start()는
    NewPlayerDialog를 거치지 않고 곧바로 reset_game_data()를 불렀는데,
    _regenerate_ai_players 도입 이후 이 호출도 ~5초짜리 전세계 선수단
    재생성을 포함하게 돼버렸다 — "새 게임" 버튼과 똑같은 문제가 여기서도
    재발한 것. 이 지점의 진짜 목적은 my_player/career_entries 등 현재
    세이브를 지워서 '이어하기'를 비활성화하고 시작 메뉴로 돌아가는 것뿐,
    새 선수단이 그 순간 당장 필요한 게 아니다 — 그래서 True를 넘기면
    ai_players 재생성만 건너뛴다(그 외 teams/my_player/career_entries 등
    삭제는 그대로 다 수행). 실제 선수단 재생성은 그 다음 사용자가
    "새 게임"→"생성"/"랜덤 생성"을 눌러 reset_game_data()가 (skip 없이)
    다시 호출되는 시점에 진행률 창과 함께 정식으로 일어난다."""
    init_db()  # 마이그레이션 적용
    conn = get_conn(); c = conn.cursor()
    # [2026-08 버그수정, 신민용 리포트: "no such table: team_power_rating"
    # (완전히 새 설치본 등 파워랭킹 화면을 한 번도 연 적 없는 DB에서 새
    # 게임 시작 시 크래시)] power_ranking.py의 8개 테이블은 init_db()가
    # 아니라 power_ranking.ensure_power_ranking_tables()가 처음 쓰일 때
    # (파워랭킹 화면 조회, 시즌 종료 집계 등) 지연 생성한다 — 그래서 그
    # 시점이 한 번도 없었던 DB에는 테이블 자체가 없는 채로 아래 DELETE
    # 목록에 걸려 그대로 크래시났다. 지우기 전에 먼저 만들어서(없으면
    # CREATE, 있으면 그대로) DELETE가 항상 안전하게 돌도록 한다.
    try:
        from power_ranking import ensure_power_ranking_tables
        ensure_power_ranking_tables(conn)
    except Exception:
        pass
    # [2026-07 버그수정, 신민용 리포트: "새 게임 하면 이전 데이터가 다 안
    # 사라지는 거 아니냐"] 팀/리그 ID가 새 게임에서도 그대로 재사용되는데
    # (팀 row는 새로 안 만들고 UPDATE만 함), match_results_archive/
    # qual_results/offer_refused 3개가 이 삭제 목록에서 빠져 있었다 —
    # 그래서 이전 플레이의 과거 시즌 결과·예선 순위·오퍼 거절 이력이 새
    # 게임에도 그대로 남아, 같은 league_id에 "있어야 할 리 없는" 과거
    # 시즌 데이터가 섞여 보이는 버그(예: 방금 고친 "일정이 안 뜬다" 건도
    # 이게 원인의 일부였을 가능성이 높다)로 이어질 수 있었다.
    # [2026-08 버그수정, 신민용 리포트: "은퇴 후 새로 시작하면 팀이 있는데
    # 없는 것처럼 오류가 난다"] league_season_standings(이번 세션에 성능
    # 개선용으로 새로 추가한 시즌 요약 테이블)이 이 삭제 목록에서 빠져
    # 있었다 — 그래서 새 게임을 시작해도 이전 플레이의 시즌 요약이
    # 그대로 남아있었고, league_id/team_id가 새 게임에서도 재사용되는
    # 구조라 이전 플레이 때 그 자리에 있던 "다른 팀"의 승/무/패 기록이
    # 새 게임의 순위표 조회(get_league_standings)에 잘못 섞여 나왔다.
    # [2026-08 버그수정, 신민용 리포트: "은퇴 이후 새 게임을 시작해도
    # 부상 이력이 남아있다"] injury_history는 my_player(id=1)에만 종속되는
    # 테이블인데(위 스키마 주석 참고, ai_players 쪽 부상은 여기 기록 안 됨)
    # my_player 자체는 DELETE 후 새 id=1로 재생성되면서도 이 테이블은 삭제
    # 목록에서 빠져 있었다 — 그래서 이전 캐릭터의 과거 부상 이력이 새로
    # 시작한 캐릭터의 player_id=1과 그대로 매칭돼 커리어/은퇴 창에 이전
    # 플레이의 부상 기록이 섞여 나왔다.
    for t in ["my_player","injury_history","career_entries","promotion_log","trophy_log","awards",
              "game_log","match_results","match_results_archive","match_details",
              "season_state","qual_results","offer_refused","league_season_standings",
              "intl_history","intl_tournaments","intl_entries","intl_matches","nat_generation",
              "cl_tournaments","cl_entries","cl_matches","cl_history",
              "goal_events",
              # [2026-08 버그수정, 신민용 리포트: "새 게임 시작하면 챔스는 새로
              # 시작하는데 세계 축구 기록실의 역대 유로파/컨퍼런스는 안 지워진다"]
              # el_*(유로파급)/ecl_*(컨퍼런스급)는 cl_*와 완전히 동일한 구조로
              # 2026-08에 새로 추가됐는데, 이 삭제 목록엔 반영이 안 돼있었다 —
              # 그래서 챔스는 새 게임 때 정상적으로 비워지는데 유로파/컨퍼런스만
              # 이전 플레이 기록이 그대로 남아 세계 축구 기록실에 미래 연도까지
              # 쌓여있는 것처럼 보였다.
              "el_tournaments","el_entries","el_matches","el_history",
              "ecl_tournaments","ecl_entries","ecl_matches","ecl_history",
              # [2026-08 신설, 10순위 슈퍼컵 시스템 구축 — el_*/ecl_*가 겪었던
              # 것과 완전히 같은 버그를 처음부터 막는다] sc_*도 el_*/ecl_*와
              # 동일한 구조라 새 게임 시작 시 반드시 같이 비워야 한다.
              "sc_tournaments","sc_entries","sc_matches","sc_history",
              "cwc_tournaments","cwc_entries","cwc_matches",
              "cup_tournaments","cup_entries","cup_matches","cup_history",
              "po_pending_slots","po_tournaments","po_matches","po_history",
              # [2026-08 버그수정, 신민용 리포트: "새 게임(2000년) 시작했는데
              # 2001년 파워랭킹이 남아있다"] power_ranking.py의 8개 테이블
              # (레이팅 원본 2개 + 연도별 스냅샷 2개 + 연속우승 카운터 2개 +
              # 리그파워 캐시 + 팀별 레이어B 이력)이 이 삭제 목록에서 통째로
              # 빠져 있었다 — team_id/league_id가 새 게임에서도 재사용되는
              # 구조라, 이전 플레이 때 쌓인 파워랭킹 데이터가 새 게임에도
              # 그대로 남아 아직 오지도 않은 미래 연도 순위까지 보이는
              # 버그로 이어졌다.
              "team_power_rating","country_power_rating",
              "team_power_rankings","country_power_rankings",
              "team_league_streak","country_regional_streak",
              "league_power","team_b_history",
              # [2026-08 v3.2 신설] 리그 상대강도 임시 테이블도 새 게임
              # 시작 시 당연히 비워야 한다(어차피 시즌 종료마다 자체
              # 정리되지만, 게임 중간에 리셋할 수도 있으므로 안전하게 포함).
              "team_season_opp_strength"]:
        c.execute(f"DELETE FROM {t}")
    c.execute("UPDATE teams SET wins=0,draws=0,losses=0,goals_for=0,goals_against=0")
    _reset_teams_to_league_data(c)
    _regenerate_ai_players(c, progress_cb=progress_cb, skip_generation=skip_ai_regen)
    _reset_formations_from_seed(c)
    _reset_club_strength(c)
    conn.commit()
    # season_state가 방금 통째로 지워졌으므로 get_state() 캐시도 반드시
    # 비워야 한다 — 안 그러면 새 게임 시작 직후에도 이전 플레이의 연도/
    # 주차가 캐시에 남아 화면에 계속 보이는 버그가 생긴다.
    try:
        import game_engine
        game_engine._invalidate_state_cache()
    except Exception:
        pass

    # [2026-08 신설, 신민용 리포트: "은퇴 이후 새 게임을 반복할수록 렉이
    # 쌓이는 것 같다"] 실제 세이브 4개(설치 직후/1트/2트/3트)를 dbstat으로
    # 비교 검증한 결과, 행 개수·파일 크기는 사이클마다 거의 그대로였지만
    # (reset 자체는 깔끔하게 잘 지움) match_results/ai_players/
    # league_season_standings 같은 핵심 테이블의 "페이지 단편화율"이
    # 사이클을 반복할 때마다 계속 나빠지고 있었다(예: match_results
    # 35%→62%→72%). 원인: 위의 대량 DELETE + _reset_*_from_seed의 대량
    # UPDATE/INSERT가 반복되면서, SQLite가 지워진 페이지를 재활용은 하되
    # 테이블별로 순서 있게 재배치는 안 해준다 — 그 결과 한 테이블의 행들이
    # 파일 전체에 점점 더 흩어져 저장되고, 이게 인메모리 DB↔game.db
    # 백업(backup() API, load_from_disk/flush_to_disk)에도 그대로
    # 복제되어 앱을 껐다 켜도 안 없어지고 계속 누적된다. VACUUM으로
    # 테이블을 파일 안에서 다시 순서대로 재배치하면 이 단편화가 즉시
    # 해소된다(실측: match_results 단편화 71.7%→0.1%, 파일도 약간 축소).
    # 새 게임을 누를 때만 1회 도는 작업이라(인메모리 DB 기준 1초 내외)
    # 실제 플레이 중 버튼 반응성에는 영향이 없다.
    conn.execute("VACUUM")
    conn.close()

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

def calc_ovr(position, stats, cap=100):
    """[2026-08 버그수정, 신민용 리포트: "신 등급을 100~105로 늘렸는데
    화면엔 여전히 100으로 뜬다"] TALENT_TIERS["god"]의 cap_max를 105까지
    올렸어도, 정작 OVR을 실제로 계산/저장하는 이 함수 자체가 항상
    min(100, ...)로 고정 클램프하고 있어서 그 위 talent_cap 설정이
    통째로 무효화되고 있었다. cap 파라미터를 받아 호출부(신 등급이면
    개인별 talent_cap을, 그 외/AI는 기존과 동일하게 100을 넘김)에서
    실제 상한을 결정하게 바꾼다 — 기본값 100이라 cap을 안 넘기는
    기존 모든 호출(AI 5.9만 명 포함)은 완전히 동일하게 동작한다."""
    items = _WEIGHT_ITEMS.get(position, _WEIGHT_ITEMS["CM"])
    wsum = _WEIGHT_SUMS.get(position, _WEIGHT_SUMS["CM"])
    g = stats.get
    total = 0
    for s, wt in items:
        total += g(s, 40) * wt
    total /= wsum
    return min(cap, max(1, int(round(total))))


def calc_ovr_from_list(position, vals, cap=100):
    """calc_ovr과 완전히 동일한 공식/결과를, dict 대신 ALL_STATS 순서의
    리스트(vals)를 직접 받아 계산한다 (dict 생성/조회 비용 제거).
    vals는 반드시 ALL_STATS와 같은 순서·같은 길이여야 하며 값이 이미 채워져
    있어야 한다(=원래 calc_ovr의 stats.get(s,40) 기본값이 필요 없는 경우 전용).
    핫루프(ai_lifecycle의 5.9만 AI 선수 시즌 처리) 전용 내부 함수.
    cap: calc_ovr과 동일 — 기본 100, AI 선수는 그대로 두면 됨."""
    items = _WEIGHT_IDX_ITEMS.get(position, _WEIGHT_IDX_ITEMS["CM"])
    wsum = _WEIGHT_SUMS.get(position, _WEIGHT_SUMS["CM"])
    total = 0
    for idx, wt in items:
        total += vals[idx] * wt
    total /= wsum
    return min(cap, max(1, int(round(total))))


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


def update_club_strength_after_season(season: int, year: int, conn=None):
    """[2026-08 신설, 신민용 확정: 동적 팀 강도] 방금 끝난 시즌의
    league_season_standings를 리그별로 순위 매겨, 그 결과로 teams.club_strength를
    갱신한다. 하드코딩 명문팀 리스트(prestige_clubs.py)와 달리 그 세이브
    안에서 실제로 잘한/못한 팀만 강해지거나 약해진다.

    - 리그마다 승점(승*3+무) → 득실차 순으로 순위를 매기고,
      club_strength_delta_for_rank(순위, 참가팀수)로 델타를 구한다.
    - 새 값 = 이전 값 * CLUB_STRENGTH_DECAY + 델타, [MIN,MAX] 클램프.
    - 리그 수가 많은 세이브(수백 개)를 고려해 SELECT 1회 + UPDATE 1회
      executemany로 처리한다(rescale_teams_to_target_ovr_batch와 동일 패턴).

    반환: 갱신된 team_id 수.
    """
    from constants import (club_strength_delta_for_rank, CLUB_STRENGTH_DECAY,
                            CLUB_STRENGTH_MIN, CLUB_STRENGTH_MAX)
    own = False
    if conn is None:
        conn = get_conn(); own = True
    try:
        rows = conn.execute(
            """SELECT team_id, league_id, wins, draws, losses,
                      goals_for, goals_against
               FROM league_season_standings WHERE season=? AND year=?""",
            (season, year)).fetchall()
        if not rows:
            return 0

        by_league: dict = {}
        for r in rows:
            by_league.setdefault(r["league_id"], []).append(r)

        team_ids = [r["team_id"] for r in rows]
        placeholders = ",".join("?" * len(team_ids))
        cur_rows = conn.execute(
            f"SELECT id, club_strength FROM teams WHERE id IN ({placeholders})",
            team_ids).fetchall()
        cur_strength = {r["id"]: (r["club_strength"] or 0.0) for r in cur_rows}

        update_rows = []
        for league_id, teams_in_league in by_league.items():
            n = len(teams_in_league)

            def _key(r):
                pts = r["wins"] * 3 + r["draws"]
                gd = r["goals_for"] - r["goals_against"]
                return (-pts, -gd)

            ranked = sorted(teams_in_league, key=_key)
            for idx, r in enumerate(ranked, start=1):
                delta = club_strength_delta_for_rank(idx, n)
                old = cur_strength.get(r["team_id"], 0.0)
                new = old * CLUB_STRENGTH_DECAY + delta
                new = max(CLUB_STRENGTH_MIN, min(CLUB_STRENGTH_MAX, new))
                update_rows.append((new, r["team_id"]))

        if update_rows:
            conn.executemany(
                "UPDATE teams SET club_strength=? WHERE id=?", update_rows)
        if own:
            conn.commit()
        return len(update_rows)
    finally:
        if own:
            conn.close()


def seed_club_strength_from_prestige(conn=None):
    """[2026-08 신설] new_game 1회성 시딩 전용. 정적 명문팀 리스트
    (data/prestige_clubs.py)의 등급을 초기 club_strength 값으로 환산해
    깔아준다 — 이후로는 update_club_strength_after_season()이 실제 성적으로
    이 값을 계속 갱신하므로, 이 함수는 게임 시작 시 딱 한 번만 불러야 한다.
    (기존 세이브에 뒤늦게 적용해도 안전 — club_strength가 이미 0이 아닌
    팀은 실적으로 이미 갱신된 것이므로 덮어쓰지 않고 건너뛴다.)
    """
    from constants import CLUB_STRENGTH_SEED_BY_PRESTIGE_LEVEL
    from data.prestige_clubs import PRESTIGE_TEAMS
    own = False
    if conn is None:
        conn = get_conn(); own = True
    try:
        rows = conn.execute(
            """SELECT t.id AS id, t.name AS tname, t.club_strength AS cs,
                      cn.name AS cname
               FROM teams t JOIN leagues l ON t.league_id = l.id
               JOIN countries cn ON l.country_id = cn.id""").fetchall()
        update_rows = []
        for r in rows:
            if r["cs"]:   # 이미 0이 아니면(실적 반영 시작됨) 건드리지 않음
                continue
            country_map = PRESTIGE_TEAMS.get(r["cname"], {})
            level = None
            for lvl, names in country_map.items():
                if r["tname"] in names:
                    level = lvl
                    break
            if level is None:
                continue
            seed = CLUB_STRENGTH_SEED_BY_PRESTIGE_LEVEL.get(level)
            if seed:
                update_rows.append((seed, r["id"]))
        if update_rows:
            conn.executemany(
                "UPDATE teams SET club_strength=? WHERE id=?", update_rows)
        if own:
            conn.commit()
        return len(update_rows)
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
    # [2026-07 조정, 신민용 지적: "국대 GK 경쟁 풀에 해외파가 있을 수
    # 있다는 전제로 봐야 한다"] 0.6은 다른 포지션 대비 너무 낮아서
    # 해외파 골키퍼 자체가 거의 안 나왔다 — 클럽이 골키퍼는 검증된
    # 자원을 선호한다는 현실은 유지하되(여전히 최저), 극단적으로
    # 낮추진 않도록 0.75로 완화.
    "GK": 0.75, "CB": 0.8, "LB": 0.9, "RB": 0.9,
    "CDM": 1.0, "CM": 1.0, "CAM": 1.1,
    "LW": 1.2, "RW": 1.2, "CF": 1.2, "ST": 1.3,
}
# [2026-08 전면 재설계, 신민용 확정: "18명 로스터 기준 국가별 외국인 보유
# 범위표"로 교체] 예전엔 5개국만 고정 상한(단일 숫자)을 가졌고 나머지는
# 전부 무제한이었다. 이제 (하한, 상한) 범위로 바꾸고, 표에 없는 나라도
# 대륙 기본값으로 전부 범위가 생긴다(더 이상 "무제한"인 나라 없음).
# 상한(hi)은 기존과 동일하게 _pick_nationality의 하드 캡으로 계속 쓰고,
# 하한(lo)은 팀 생성 시점에만 별도로 맞춰준다(_generate_team_players의
# _topup_foreign_floor 참고) — 이적/은퇴교체 등 세이브 진행 중에는 하한을
# 강제하지 않는다(실제 축구단도 시즌마다 외국인 비율이 자연스럽게
# 오르내리므로, 상한 위반만 계속 막고 하한은 그대로 흘러가게 둔다).
FOREIGN_QUOTA_RANGE = {
    "사우디아라비아": (6, 9), "카타르": (6, 9), "아랍에미리트": (5, 7),
    "일본": (4, 7), "태국": (5, 8), "호주": (4, 6),
    "대한민국": (3, 5), "중국": (3, 5), "인도네시아": (4, 7),
    "말레이시아": (5, 8), "베트남": (2, 4), "인도": (4, 6),
    "이란": (1, 3), "우즈베키스탄": (2, 4), "모로코": (2, 4),
    "이집트": (3, 5), "알제리": (1, 3), "튀니지": (2, 4),
    "남아프리카공화국": (3, 5), "나이지리아": (0, 2), "가나": (0, 2),
    "세네갈": (0, 2),
    "잉글랜드": (9, 13), "포르투갈": (8, 12), "벨기에": (7, 11),
    "네덜란드": (6, 10), "독일": (7, 11), "이탈리아": (8, 12),
    "스페인": (6, 10), "프랑스": (6, 10), "튀르키예": (7, 11),
    "그리스": (5, 9), "스위스": (6, 10), "오스트리아": (5, 9),
    "체코": (4, 7), "폴란드": (4, 7), "세르비아": (3, 5),
    "크로아티아": (3, 6), "루마니아": (3, 5),
    "브라질": (3, 6), "아르헨티나": (2, 4), "우루과이": (2, 4),
    "칠레": (3, 5), "콜롬비아": (2, 4), "에콰도르": (3, 5),
    "파라과이": (2, 4), "페루": (2, 4),
    "멕시코": (5, 8), "미국": (5, 8), "캐나다": (4, 6),
    "코스타리카": (2, 4), "자메이카": (1, 3), "뉴질랜드": (2, 4),
}

# 위 표에 없는 나라는 대륙 기본값으로 처리한다(북중미는 이 게임의 대륙
# 분류상 "북미"에 이미 포함돼 있다 — data/countries.py 참고: 코스타리카/
# 파나마/자메이카 등이 전부 "북미"로 태깅됨).
FOREIGN_QUOTA_RANGE_BY_CONTINENT = {
    "아시아": (1, 2), "아프리카": (1, 2), "남미": (1, 3),
    "북미": (1, 3), "오세아니아": (1, 3), "유럽": (3, 5),
}


def get_foreign_quota_range(country, continent=None):
    """국가별 외국인 보유 목표 범위(lo, hi) 반환. 표에 등록된 나라는 그
    값을, 없으면 대륙 기본값을, 대륙 정보조차 없으면 안전 기본값(1,3)을
    반환한다 — 이제 어떤 나라도 "무제한"으로 남지 않는다."""
    rng = FOREIGN_QUOTA_RANGE.get(country)
    if rng:
        return rng
    if continent:
        rng = FOREIGN_QUOTA_RANGE_BY_CONTINENT.get(continent)
        if rng:
            return rng
    return (1, 3)
# [2026-07 리팩터] 예전엔 스타 슬롯 해외파 국가를 이 고정 목록에서만
# 뽑았는데, 그러면 목록 밖 나라(한국 등 대부분)는 빅클럽 스타 해외파가
# 사실상 나올 수 없었다. 이제 _pick_nationality()는 전세계 국가를 피파
# 랭킹 가중 추첨하는 방식으로 바뀌어 이 목록은 더 이상 직접 쓰이지 않는다
# (강국이 자연히 랭크 가중치로 우대받음) — 참고용으로 남겨둔다.
FOOTBALL_POWERHOUSES = ["브라질", "아르헨티나", "프랑스", "잉글랜드", "스페인",
                        "독일", "포르투갈", "네덜란드"]

_COUNTRY_CONTINENT = {}   # 나라명 -> 대륙 (지연 초기화)
_CONTINENT_COUNTRIES = {}  # 대륙 -> [(나라명, fifa_rank), ...] fifa_rank 오름차순(강한 순)
_ALL_COUNTRIES_BY_RANK = []  # [2026-07 신설] 대륙 무관 전세계 (나라명, fifa_rank) 목록 —
                              # 스타 슬롯 해외파 국가를 대륙 상관없이 가중 추첨할 때 사용.


def get_country_avg_squad_ovr(country, positions=None, min_count=8, top_n=3):
    """[2026-07 신설, 신민용 리포트: "국대 실제값이 밴드 상한을 훨씬
    넘어선다"] get_country_squad_players()(포지션당 '전 세계 태그 선수 중
    1등'만 픽)를 국가 평균 OVR 계산에 그대로 재사용했더니, 단 한 명의
    극단적 이상치에 전체 평균이 휘둘리는 문제가 있었다 — 실측: 대한민국
    국적 태그 CB 707명 중 우연히 잉글랜드 프리미어리그 소속인 1명이
    OVR97까지 찍혀서, 그 한 명 때문에 '국대 평균'이 88.6까지 치솟았다
    (반면 707명 전체 평균은 61.7). 선수 개인 OVR은 국적과 무관하게 순수히
    소속 클럽(리그 등급·팀 명성)으로 정해지므로, 태그된 선수 수가 많은
    나라일수록 이런 통계적 이상치가 나올 확률 자체가 높아진다.

    get_country_squad_players()는 그대로 둔다(포메이션 화면에서 "실제
    보유한 최고의 선수로 라인업을 짠다"는 목적엔 이 방식이 맞다 —
    손흥민이 토트넘 소속이면 당연히 그 선수로 스쿼드를 짜야 한다). 이
    함수는 "그 나라 수준을 대표하는 평균값" 계산 전용으로, 포지션당
    1명이 아니라 상위 top_n명을 뽑아 평균 내서 단일 이상치의 영향력을
    줄인다 — 여전히 '준수한 선수들' 수준을 반영하지만(전체 707명 평균
    보다는 높게, 그게 국가대표 선발 개념에 맞음), 극단값 하나가 전체를
    끌어올리지는 않는다. 3단계 폴백은 get_country_squad_players와 동일한
    구조를 그대로 따른다.
    """
    positions = positions or ["GK", "CB", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
    conn = get_conn()
    slot_groups: list = [[] for _ in positions]
    used_ids: set = set()

    def _fill(where_sql, params, randomize=False):
        for i, pos in enumerate(positions):
            if slot_groups[i]:
                continue
            ph = ",".join(str(x) for x in used_ids) or "0"
            order_by = "RANDOM()" if randomize else "ap.ovr DESC"
            rows = conn.execute(
                f"""SELECT ap.id, ap.ovr
                    FROM ai_players ap JOIN teams t ON ap.team_id=t.id
                    JOIN leagues l ON t.league_id=l.id JOIN countries cn ON l.country_id=cn.id
                    WHERE {where_sql} AND ap.position=? AND ap.id NOT IN ({ph})
                    ORDER BY {order_by} LIMIT ?""",
                (*params, pos, top_n)).fetchall()
            if rows:
                slot_groups[i] = [r["ovr"] for r in rows]
                used_ids.update(r["id"] for r in rows)

    _fill("ap.nationality=?", (country,))
    if sum(1 for s in slot_groups if s) < min_count:
        _fill("cn.name=?", (country,))
    if sum(1 for s in slot_groups if s) < min_count:
        _init_nationality_tables()
        cont = _COUNTRY_CONTINENT.get(country, "")
        _fill("t.current_tier>=2 AND cn.continent=? AND cn.name!=?", (cont, country), randomize=True)
    if sum(1 for s in slot_groups if s) < min_count:
        _fill("t.current_tier>=2 AND cn.name!=?", (country,), randomize=True)
    conn.close()

    filled = [g for g in slot_groups if g]
    if len(filled) < min_count:
        return None
    # 포지션별 상위 top_n 평균 → 그 값들을 다시 포지션 간 평균.
    return sum(sum(g) / len(g) for g in filled) / len(filled)


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
        _ALL_COUNTRIES_BY_RANK.append((name, rank))
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
    # [2026-07 재설계, 신민용 지적: "국대 GK 선발 풀이 국적 기준으로 전세계를
    # 보는데, 스타 슬롯 해외파는 8개국 고정 목록에서만 나와서 그 목록 밖
    # 나라(한국 포함 대다수)는 빅클럽 스타 해외파가 사실상 나올 수 없었다"]
    # 고정 목록 대신 전세계 국가를 피파랭킹 가중 추첨한다 — 강국(랭크
    # 1~9위)은 가중치가 압도적으로 높아 여전히 대부분의 스타 해외파를
    # 차지하지만, 그 외 나라도 실력(랭크)에 비례한 실질적 확률을 갖는다.
    if is_star and random.random() < 0.6:
        cand = [(n, r) for n, r in _ALL_COUNTRIES_BY_RANK if n != team_country]
        nat = _weighted_country_pick(cand) or team_country
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
# [2026-08 신설, 신민용 확정: 벤치 인원 확장 1단계] 주전 11자리 뒤에 후보
# 7자리(GK1/DF2/MF2/FW2)를 추가. 아래 TEAM_STARTER_COUNT(=11)로 두 구간을
# 나눠서 쓴다 — 스타 슬롯 추첨(_star_counts)/역할 순번(role_idx, 0~10 고정
# 스케일)은 기존 그대로 앞 11자리(주전)에만 적용하고, 후보 7자리는 별도
# 감쇠 곡선(_bench_target_ovr)으로 생성한다(_generate_team_players 참고).
# 포지션 구성은 실제 벤치 뎁스 관례(GK 1명, 수비 2명, 미드필더 2명,
# 공격수 2명)를 따름 — DF는 CB+RB, MF는 CM+CAM, FW는 ST+LW로 분산.
TEAM_STARTER_COUNT = 11
TEAM_POSITIONS = ["GK","CB","CB","LB","RB","CDM","CM","CAM","LW","RW","ST",
                   "GK","CB","RB","CM","CAM","ST","LW"]
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
# [2026-08 신설, 신민용+GPT 교차검토 합의] B/C 등급 국가라도 그 나라의
# 진짜 간판 클럽(prestige_level 높은 팀)만 예외적으로 소규모 스타 슬롯을
# 받게 하는 화이트리스트. min_level 미만인(또는 등록 안 된) 팀은 전혀
# 영향 없음 — 그 나라의 일반 팀들은 기존 등급 그대로다. 남아공은 격차가
# 가장 커서(실측 68.56 → 목표 82) el_base/el_bonus를 가장 크게 뒀다.
GLOBAL_PRESTIGE_STAR_CFG = {
    # [2026-08 1차 실측 후 재조정] el_base=2/el_bonus=3(최대 5개 엘리트)로는
    # 11자리 중 나머지 6자리가 여전히 B급 완만한 곡선(ace_lo 0.94)에 깔려
    # 평균이 목표(86)에 크게 못 미쳤다(실측 80.44/81.14) — S등급의
    # el_fill_rest에 가깝게 슬롯 수를 대폭 늘린다.
    # [2026-08 2차 실측 후 재조정] slot 수만으로는 2차 실측(84.11/83.92)이
    # 여전히 목표(86)에 2점 가까이 못 미쳤다 — el_offset을 기본(6,14)보다
    # 좁혀서(4,9) 엘리트 슬롯 자체의 목표 OVR도 같이 끌어올린다.
    "이집트":       {"wc_base": 1, "wc_bonus": 1, "el_base": 7, "el_bonus": 3, "min_level": 2, "el_offset": (4, 9)},
    "모로코":       {"wc_base": 1, "wc_bonus": 1, "el_base": 7, "el_bonus": 3, "min_level": 2, "el_offset": (4, 9)},
    # [2026-08 2차 실측 후 재조정] 격차가 가장 커서(2차 실측 78.98, 목표
    # 81~82) 이집트/모로코보다도 더 크게 밀어올린다.
    "남아프리카공화국": {"wc_base": 1, "wc_bonus": 1, "el_base": 8, "el_bonus": 3, "min_level": 2, "el_offset": (3, 8)},
    # [2026-08 신설, 신민용 요청: "튀니지도 등급 B로, 1부 OVR 83대로"]
    # 에스페랑스 드 튀니스/클럽 아프리캥(레벨3) — 이집트·모로코와 동일 패턴,
    # 목표가 그보다 약간 낮아(83 vs 86) el_base/offset을 살짝 보수적으로.
    "튀니지":       {"wc_base": 0, "wc_bonus": 1, "el_base": 5, "el_bonus": 3, "min_level": 2, "el_offset": (5, 10)},
    # [2026-08 신설, 신민용 요청: "캐나다도 평균 OVR 82쯤으로, 등급도 B로"]
    # 포지 FC(레벨3)만 명문 등록돼 있어 min_level=2로 둬도 사실상 레벨3만
    # 해당 — 목표가 이집트/모로코/튀니지보다 낮아(82) 슬롯을 더 보수적으로.
    "캐나다":       {"wc_base": 0, "wc_bonus": 1, "el_base": 4, "el_bonus": 2, "min_level": 2, "el_offset": (5, 10)},
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


def _star_counts(grade, team_strength, continent_bonus=0, n_slots=11, tier=1,
                  country=None, prestige_level=0):
    """(월드클래스 슬롯 수, 엘리트 슬롯 수) 반환. SS/S/A 외 등급은 기본
    (0,0) — B급 이하는 이번 조정 대상이 아니라 기존 완만한 곡선 그대로 쓴다.

    [버그수정 2026-07] "SS/S는 월클+엘리트로만 구성"이라는 설계는 원래
    1부만을 의도한 것이었는데(코드 주석에도 '1부'라고 명시돼 있었음), 정작
    이 함수엔 tier 구분이 전혀 없어서 SS/S 등급 나라의 모든 부수(2~6부까지)
    팀 전원이 거의 엘리트/월드클래스로 채워지고 있었다 — 그 결과 6부 아마추어
    팀도 1부 수준(평균 89 이상) OVR이 나오는 심각한 버그로 이어졌다. 이제
    tier에 따라 스타 슬롯 배정을 계단식으로 줄인다: 1부만 원래 설계(거의
    전원 스타) 그대로, 2부는 대폭 축소, 3부 이상은 스타 슬롯 자체가 없어
    전원 _target_ovr(그 tier의 낮은 상한 기준)로만 결정된다.

    [2026-08 신설, 신민용+GPT 교차검토 합의 — "국가 리그 등급(연봉·뎁스
    등)은 그대로 두고, 그 나라의 진짜 간판 클럽(prestige_level 높은 팀)만
    별도로 강화한다"] B/C 등급은 위 STAR_COUNT_BY_GRADE에 항목이 아예
    없어서 스타 슬롯 자체가 안 생겼다 — 그 결과 알 아흘리(이집트)·마멜로디
    선다운즈(남아공)처럼 prestige_level=3(세계적 초명문) 태그가 붙어 있어도
    등급이 B/C면 그 태그가 사실상 무효화되고 있었다(실측: 세계 순위 900~
    2100위권). GLOBAL_PRESTIGE_STAR_CFG에 등록된 나라는, 그 나라의
    prestige_level이 등록된 min_level 이상인 팀에 한해서만(일반 팀은 전혀
    영향 없음) B/C 등급이어도 소규모 스타 슬롯을 받는다."""
    cfg = STAR_COUNT_BY_GRADE.get(grade)
    if not cfg:
        gp = GLOBAL_PRESTIGE_STAR_CFG.get(country) if country else None
        if gp and tier == 1 and prestige_level >= gp.get("min_level", 2):
            n_world = min(gp.get("wc_base", 0) + round(gp.get("wc_bonus", 0) * team_strength),
                          _MAX_WORLDCLASS_PER_TEAM)
            n_elite = min(gp.get("el_base", 0) + round(gp.get("el_bonus", 0) * team_strength),
                          _MAX_ELITE_PER_TEAM, max(0, n_slots - n_world))
            return n_world, n_elite
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
            # [2026-07 재수정, 신민용 리포트: "잉글랜드 2부가 1부랑 OVR
            # 차이가 아예 안 난다"] 예전엔 8개(11자리 중 최대 8개)까지
            # 엘리트로 채워서 사실상 팀 전체가 스타였다 — 그래서 OVR_RANGES
            # 상한을 낮춰도 대부분의 선수가 여전히 스타 슬롯 값(상한 근처)에
            # 몰려 체감상 1부와 구분이 안 됐다. 5개로 줄여 나머지 절반
            # 가까이는 일반 곡선(_target_ovr, tier2 상한 기준)을 따르게 해
            # "에이스급 몇 명은 확실히 강하지만 전체 스쿼드는 확연히 아래"
            # 라는 현실적인 챔피언십 느낌을 되살린다.
            # [2026-08 재조정, 신민용 확정: "챔피언십 우승권도 89~90은
            # 되어야 한다"] 5개로는 STAR_STRENGTH_PENALTY_MAX_BY_GRADE
            # 조정만으론 최상위권 목표(89~90)에 못 미쳤다(실측) — 엘리트
            # 슬롯을 늘려 그 팀의 더 많은 자리가 일반 곡선(_target_ovr, 벤치
            # 쪽으로 갈수록 낮게 깎이는 완만한 커브) 대신 스타 슬롯값을
            # 받게 한다. SS 2부 전용 분기라 다른 나라 2부에는 영향 없음.
            n_elite = min(7, max(0, n_slots - n_world))
        elif tier == 2 and grade == "S":
            # [2026-07 신설, 신민용 지적: "세군다·세리에B·분데스2·리그2는
            # 현실에서도 상당히 강한 2부 리그인데 엘리트 슬롯이 3개로 확
            # 깎여서 S급 1→2부 하락폭(9.9)이 A/B급(7.8~7.9)보다도 커지는
            # 역전이 있었다" — SS(챔피언십)만큼은 아니어도 A/B보다는 완만하게
            # 떨어지도록 6개까지 허용한다.
            n_elite = min(9, max(0, n_slots - n_world))
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


def _star_target_ovr(tier_top, kind, el_offset=(6, 14), prestige_bonus=0.0, team_strength=1.0,
                      penalty_max=None):
    """스타 슬롯 하나의 목표 OVR. 리그 상한(tier_top) 바로 아래에서 결정 —
    월드클래스는 거의 상한 그 자체, 엘리트는 등급별 el_offset만큼 그 아래
    (SS/S는 좁은 오프셋 = 상위권 엘리트, A는 넓은 오프셋 = 하위권 엘리트).
    [2026-08 버그수정, 신민용 리포트: "S등급(레알/바르사 등) 명문팀이 SS등급
    (잉글랜드) 명문팀한테 밀리면 안 된다"] 기존엔 이 함수에 prestige_bonus
    인자 자체가 없었다. SS/S 1부는 el_fill_rest=True라 스쿼드 전원이 여기
    (스타 슬롯)로만 채워지는데, PRESTIGE_OVR_BONUS는 _target_ovr(일반
    곡선)에만 들어가고 있었다 - SS/S 1부 명문팀은 명문팀 보정이 적용될
    자리가 아예 없었던 것. 실측(맨유 94.1 vs 레알 93.3)에서 SS 명문팀이
    S 명문팀보다 높게 나온 원인이 정확히 이거였다. 이제 worldclass/elite
    목표치에도 prestige_bonus를 더한다.
    [2026-08 재수정, 신민용 확정 — 국가별 OVR 분포 실측 결과 반영: "SS/S
    1부가 el_fill_rest=True라 team_strength(순위)가 스타 슬롯 OVR에 전혀
    반영이 안 되고 있었다"] ace_lo/spread(_target_ovr 쪽 곡선)를 아무리
    조정해도 SS/S 1부는 스쿼드 전원이 이 함수(스타 슬롯)로만 채워져서
    그 조정이 무의미했다(실측으로 확인). 최소 변경으로 team_strength를
    연결한다 — team_strength가 낮을수록(하위권 팀일수록)
    STAR_STRENGTH_PENALTY_MAX에 비례한 추가 감쇠를 뺀다. 1위급(team_strength≈1)은
    감쇠가 거의 0, 최하위(team_strength≈0)는 감쇠가 STAR_STRENGTH_PENALTY_MAX
    그대로 적용된다. wc_base/wc_bonus/el_fill_rest/el_offset 등 스타 슬롯
    '개수' 구조는 이번엔 건드리지 않고, 가장 작은 변경(OVR 값 자체에만
    team_strength 연동)으로 먼저 실측한다."""
    # [2026-08 수정] 이 함수가 STAR_STRENGTH_PENALTY_MAX(모듈 하단에 정의)
    # 보다 파일상 앞에 있어서, 함수 정의 시점 기본값으로 그 상수를 직접
    # 참조하면 NameError가 난다 — None 기본값으로 받고 여기서 늦게 해석한다.
    if penalty_max is None:
        penalty_max = STAR_STRENGTH_PENALTY_MAX
    strength_penalty = (1.0 - team_strength) * penalty_max
    if kind == "worldclass":
        return tier_top - random.uniform(0, 4) - strength_penalty + prestige_bonus
    lo, hi = el_offset
    return tier_top - random.uniform(lo, hi) - strength_penalty + prestige_bonus  # elite


# [2026-08 신설, 신민용 확정 — 국가별 OVR 분포 재조정 2차 실험] 스타 슬롯
# (worldclass/elite) OVR에 team_strength 기반으로 추가로 빼는 감쇠의 상한
# (최하위팀 기준). 0이면 기존과 완전히 동일(하위호환 기본값). 실측으로
# 4/7/10 세 후보를 13개국×15회 비교한 결과, 4(후보 A)가 프랑스/스페인
# 목표 밴드(1~3위 95~98, 4~6위 93~95, 7~10위 91~93, 11+ 88~91)에 가장
# 정확히 들어맞았고, 7/10은 과도하게 깎여 목표 밴드를 이탈했다. 독일은
# 4로도 11+가 목표(87~90)보다 0.3 높게 나왔지만, 이건 감쇠를 나라마다
# 다르게 주는 대신 독일 override 하한을 미세조정하는 쪽으로 흡수하기로
# 확정(감쇠는 전 국가 공통값 유지가 원칙) — COUNTRY_LEAGUE_OVR_OVERRIDE 참고.
STAR_STRENGTH_PENALTY_MAX = 4.0

# [2026-08 신설, 신민용 확정: "잉글랜드 하위권도 91~92는 되게, 챔피언십
# 우승권은 89~90은 되어야 한다"] 위 4.0은 S등급(스페인/프랑스/독일/
# 이탈리아/브라질 공용) 목표 밴드에 맞춰 이미 정밀 튜닝된 값이라(바로 위
# 주석 참고) 여기서 건드리면 그 나라들까지 같이 움직인다 — 그런데 SS등급은
# "전 세계에서 잉글랜드 하나뿐"(STAR_COUNT_BY_GRADE 주석 참고)이라, SS만
# 별도로 낮은 감쇠를 줘도 다른 나라엔 전혀 영향이 없다. 등급별 오버라이드가
# 없으면(S 등 나머지 전부) 위 전역값 그대로 쓴다.
STAR_STRENGTH_PENALTY_MAX_BY_GRADE = {"SS": 1.0}


def _tier_top_ovr(grade, tier, continent_bonus=0, country=None):
    """그 등급·tier 리그에서 도달 가능한 최고 OVR.
    continent_bonus: 대륙별 OVR 보정치 (유럽+1, 아시아-3 등)
    country: [2026-08 신설] COUNTRY_LEAGUE_OVR_OVERRIDE에 등록된 나라면
        상한을 그 오버라이드 기반 값으로 대체한다(등급 문자는 그대로 두고
        실제 스쿼드 OVR 범위만 별도 조정하는 용도). 오버라이드가 적용되면
        continent_bonus는 더하지 않는다 — 오버라이드 값 자체가 이미 그
        나라에 특화된 값이라, 대륙 단위 보정을 얹으면 이중 보정이 된다.
        [2026-08 버그수정, 신민용 리포트: "K1 OVR을 내렸더니 K2랑 겹친다"]
        예전엔 이 대체가 tier==1일 때만 일어나고 tier2 이하는 대륙보정만
        더한 grade 기본표를 그대로 썼다 — get_ovr_range()가 이미
        "오버라이드가 tier1 기본값 대비 이동한 만큼(delta)을 tier2 이하에도
        동일 적용"하도록 고쳐졌으니, 여기서도 tier에 상관없이
        get_ovr_range()를 거쳐 그 값을 그대로 쓴다(로직 이원화 방지 —
        이번 버그 자체가 이 함수와 get_ovr_range가 따로 놀아서 생겼었다).

    [버그수정 2026-07] 예전엔 OVR_RANGES에 그 등급의 tier가 정의 안 돼
    있으면(예: SS 5부, S 6부처럼 나중에 부수가 늘었는데 표를 못 채운 경우)
    무조건 45로 떨어졌는데, 이게 등급별 실제 최상단 값(SS는 90~100대)과
    무관한 고정값이라 자칫 "정의 안 된 tier가 tier1과 비슷해지는" 것보다는
    낫지만, 반대로 "SS/S처럼 원래 높은 등급인데 갑자기 뚝 떨어지는" 부자연스러운
    단절이 생겼다. 이제는 그 등급 안에서 정의된 가장 깊은 부수를 기준으로,
    한 부수당 일정폭(STEP)씩 자연스럽게 더 깎아 내려가도록 한다 — 등급표에
    없는 부수가 나와도(향후 부수를 더 늘려도) 항상 "한 단계 위보다는 낮고,
    급격한 단절은 없는" 값이 나온다."""
    if country and country in COUNTRY_LEAGUE_OVR_OVERRIDE:
        _rng = get_ovr_range(grade, tier, country)
        if _rng:
            return min(100, _rng[1])
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


def _target_ovr(grade, tier, team_strength, role_idx, continent_bonus=0, prestige_bonus=0, country=None):
    """팀 강도(0~1) + 역할 순번(0=에이스 … 10=막내)으로 목표 OVR 산출.
    [2026-07 신설] prestige_bonus: 명문팀 전용 추가 보너스. continent_bonus처럼
    _tier_top_ovr()의 top 자체에 더하면 SS등급 클램프("if grade=='SS':
    continent_bonus=min(continent_bonus,0)")에 걸려 사라져버린다(하필 이
    보너스가 정작 필요한 EPL 등 SS등급 명문팀에서 무효화되는 것) — 그래서
    top 계산 이후, ace 산출 단계에서 따로 더한다. role_mult를 곱하기 전에
    더해서 에이스일수록 보너스를 온전히 받고 벤치로 갈수록 비례해 줄어들게
    한다(스카우팅/인프라 우위가 에이스급에서 가장 크게 드러난다는 설계).
    [2026-08 신설] country: COUNTRY_LEAGUE_OVR_OVERRIDE 조회용, _tier_top_ovr로
    그대로 전달.
    [2026-08 재수정] 위 클램프(하한 고정)를 실측해보니 부작용이 있었다 —
    약체팀(team_strength=0)과 최강팀(team_strength=1) 둘 다 벤치 포지션
    다수가 같은 고정 하한(84)에 몰려버려서, 정작 "팀간 실력 격차"가
    거의 사라졌다(신민용 리포트: "포르투갈 1부가 85~87로만 뜬다" — 실측:
    최약체 평균 85.0, 최강팀 평균 86.6, 격차 겨우 1.6점). 하한 자체를
    team_strength에 비례해서 올린다 — 약체팀은 여전히 lo 근처까지 내려갈
    수 있지만, 강팀은 그보다 확실히 높은 지점을 하한으로 삼는다. 그
    결과 "선수 개인이 lo 밑으로는 절대 안 내려간다"는 보장은 유지하면서,
    "약체팀 벤치 << 강팀 벤치"라는 팀간 격차도 되살아난다.
    """
    prof = TEAM_ROLE_PROFILE.get(grade, TEAM_ROLE_PROFILE["F"])
    top = _tier_top_ovr(grade, tier, continent_bonus, country)
    # 팀 에이스 목표: 강팀일수록 리그 top에 근접
    ace = top * (prof["ace_lo"] + (1.0 - prof["ace_lo"]) * team_strength) + prestige_bonus
    role_mult = 1.0 - prof["spread"] * (role_idx / 10.0)
    result = ace * role_mult
    ovr_rng = get_ovr_range(grade, tier, country)
    if ovr_rng:
        lo, hi = ovr_rng
        # [2026-08] 하한 자체를 team_strength로 보간 — 0(최약체)이면 lo
        # 그대로, 1(최강팀)이면 lo와 hi 사이 BENCH_FLOOR_GROWTH만큼 올라간
        # 지점까지. 이렇게 하면 두 팀 다 "그 팀 나름의 하한" 밑으로는 안
        # 내려가면서도, 강팀 하한이 약체팀 하한보다 확실히 높아 팀간 격차가
        # 유지된다.
        BENCH_FLOOR_GROWTH = 0.5
        effective_lo = lo + (hi - lo) * BENCH_FLOOR_GROWTH * team_strength
        result = max(result, effective_lo)
    return result


# [2026-08 신설, 벤치 인원 확장 1단계] 후보(벤치) 슬롯 하나가 그 앞
# 슬롯보다 추가로 더 깎이는 폭. _target_ovr의 role_idx(0~10, "0=에이스…
# 10=막내") 스케일은 11자리(주전) 전제로 이미 정밀 튜닝돼 있어 그대로
# 두고, 벤치는 "막내(role_idx=10)" 지점을 출발선으로 삼아 거기서부터
# 서열(bench_rank)만큼 선형으로 더 깎는 별도 곡선을 쓴다.
BENCH_OVR_DECAY_PER_SLOT = 2.5


def _bench_target_ovr(grade, tier, team_strength, bench_rank, continent_bonus=0,
                       prestige_bonus=0, country=None):
    """벤치(후보) 선수 목표 OVR. bench_rank: 0=벤치 1번째(GK) … 6=벤치
    마지막(LW). 기존 _target_ovr(role_idx=10, 주전 중 가장 낮은 지점)을
    출발선으로 삼아 bench_rank가 깊어질수록 BENCH_OVR_DECAY_PER_SLOT만큼
    추가로 뺀다. _target_ovr 내부의 하한 클램프(effective_lo)를 그대로
    거친 뒤에 감쇠를 얹으므로, 주전 하한 로직(팀간 격차 유지용)에 영향을
    주지 않고 벤치만 그보다 확실히 낮게 내려간다. 절대 하한 30은 극단적인
    약체팀에서도 음수/비현실적 값이 나오지 않도록 하는 안전장치.
    """
    base = _target_ovr(grade, tier, team_strength, 10, continent_bonus, prestige_bonus, country)
    return max(30.0, base - BENCH_OVR_DECAY_PER_SLOT * (bench_rank + 1))


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
    # [2026-08 최적화] 팀마다 "SELECT name FROM player_names WHERE country_id=?
    # ORDER BY RANDOM()"을 새로 날렸었다 — 팀 수(10,423)만큼 쿼리가 나가는데,
    # 정작 국가 수는 210개뿐이라 대부분 같은 나라를 위해 매번 다시 조회하는
    # 중복 쿼리였다. 실측 결과 이 쿼리 하나가 전체 선수단 생성 시간의 40%
    # 가까이를 차지 — DB 쓰기(INSERT)보다 훨씬 큰 병목이었다.
    # ORDER BY RANDOM()의 "정렬"은 아래에서 random.choice()로 매번 무작위
    # 선택하는 로직상 순서에 아무 영향이 없으므로(균등 추출은 입력 순서와
    # 무관), 국가별로 딱 한 번만 조회해 캐싱하고 이후 같은 나라의 모든
    # 팀은 캐시를 재사용한다. 결과물(어떤 이름이 어떤 확률로 배정되는지)은
    # 기존과 동일 — 그냥 같은 조회를 10,423번이 아니라 210번만 한다.
    _name_pool_cache: dict = {}
    # [2026-07 신설, 신민용 확정] 명문팀 전용 team_strength 보너스. ace_lo
    # (팀 간 OVR 격차의 전체 폭)는 예전 지적("같은 1부인데 팀간 격차가
    # 너무 크다")대로 좁게 유지한다 — 이걸 다시 넓히면 명문팀 문제는
    # 고쳐져도 관련 없는 일반 팀들 간의 격차 문제가 되살아난다.
    # [2026-07 정리] 명문팀 보정은 _target_ovr의 prestige_bonus 파라미터
    # (SS등급 클램프를 피해가도록 설계된 쪽)로 일원화한다 — 한때 여기서도
    # team_strength 자체를 올리는 별도 보너스를 썼는데, 그러면 두 메커니즘이
    # 동시에 적용돼 이중 보정이 된다. team_strength 분포/곡선 자체는 순위
    # 뽑기 결과 그대로 두고 건드리지 않는다.
    for lid, teams in leagues.items():
        n = len(teams)
        # [2026-07 v2 확장, 신민용 제안: "명문도 위상이 다른데 이분법이면
        # 아쉽다 — 3단계(세계적 초명문/빅클럽/전통 강호) 체계가 활용도가
        # 높다"] data/prestige_clubs.py의 PRESTIGE_TEAMS를 3단계 등급
        # 구조로 재편했다 — 등급별 가중치는 prestige_weight()가
        # PRESTIGE_WEIGHT_BY_LEVEL에서 찾아 반환한다(3급=뮌헨/PSG/레알
        # 등, 2급=첼시/도르트문트 등, 1급=토트넘/레버쿠젠 등, 비명문=1.0).
        teams_info = [{"weight": prestige_weight(t.get("cname", ""), t.get("tname", ""))}
                      for t in teams]
        perm = weighted_team_order(teams_info)   # perm[0]=이번 시즌 최강팀 인덱스, ...
        league_used: set = set()
        for rank, team_idx in enumerate(perm):
            team = teams[team_idx]
            team_strength = 1.0 - (rank / (n - 1)) if n > 1 else 1.0
            # [리그등급 분리] 국대 등급(grade) 대신 리그 전용 등급 사용
            league_grade = get_country_league_grade(team.get("cname", ""), team["grade"])
            team_with_lg = dict(team)
            team_with_lg["grade"] = league_grade
            _generate_team_players(c, team_with_lg, team_strength, league_used,
                                    name_pool_cache=_name_pool_cache)
            _done += 1
            if progress_cb and (_done % 20 == 0 or _done == _total_teams):
                progress_cb(_done, _total_teams, team.get("cname", ""))


def _topup_foreign_floor(_rows, star_kind_by_slot, team_country, team_continent,
                          quota_lo, foreign_count):
    """[2026-08 신설] 팀 생성 직후 실제 외국인 수가 국가별 목표 범위
    하한(quota_lo)에 못 미치면, 벤치(후보) 자리부터 우선해서 자국 선수
    일부를 외국인으로 바꿔 하한을 맞춘다. 스타 슬롯(star_kind_by_slot)은
    "축구 강국 우선" 로직으로 이미 국적이 확정된 자리라 건드리지 않는다.
    _rows는 (팀id,이름,포지션,...,국적) 튜플 리스트 — 국적이 마지막
    원소라 인덱스 -1로 바로 수정한다."""
    if foreign_count >= quota_lo:
        return foreign_count
    _init_nationality_tables()
    deficit = quota_lo - foreign_count
    domestic_idx = [i for i in range(len(_rows))
                    if i not in star_kind_by_slot and _rows[i][-1] == team_country]
    # 벤치(TEAM_STARTER_COUNT 이상)부터 우선 — bool 정렬(False가 먼저)로
    # "벤치인가(i>=TEAM_STARTER_COUNT)"가 True인 항목을 앞으로 보낸다.
    domestic_idx.sort(key=lambda i: i < TEAM_STARTER_COUNT)
    for i in domestic_idx[:deficit]:
        pool = [(n, r) for n, r in _CONTINENT_COUNTRIES.get(team_continent, [])
                if n != team_country]
        nat = _weighted_country_pick(pool) or team_country
        if nat == team_country:
            continue
        row = list(_rows[i])
        row[-1] = nat
        _rows[i] = tuple(row)
        foreign_count += 1
    return foreign_count


def _generate_team_players(c, team, team_strength, league_used: set = None, name_pool_cache: dict = None):
    grade = team["grade"]; tier = team["tier"]
    continent = team.get("continent", "유럽")
    if league_used is None:
        league_used = set()

    # 대륙별 OVR 보정치 + [신규] 나라별 미세조정(COUNTRY_OVR_ADJ)
    continent_bonus = CONTINENT_OVR_BONUS.get(continent, 0)
    continent_bonus += COUNTRY_OVR_ADJ.get(team.get("cname", ""), 0)
    # SS는 이미 상한(100)에 근접 → 보정 축소 (초과 방지)
    if grade == "SS":
        continent_bonus = min(continent_bonus, 0)

    # [2026-07 신설] 명문팀 전용 OVR 보너스 — ace_lo(팀간 전반 격차)는 건드리지
    # 않고, 명문팀에만 별도로 얹는다. continent_bonus와 달리 SS등급 클램프의
    # 영향을 안 받도록 _target_ovr 안에서 top 계산 이후 단계에 더해진다.
    prestige_bonus = PRESTIGE_OVR_BONUS if is_prestige(
        team.get("cname", ""), tier, team.get("tname", "")) else 0

    # [2026-08 순서 변경] GLOBAL_PRESTIGE_STAR_CFG(아래 _star_counts 호출)가
    # prestige_level을 알아야 해서, 원래 _star_prestige_bonus 계산 때 하던
    # prestige_level() 조회를 여기로 앞당긴다 — 계산 자체는 기존과 동일.
    _plevel = prestige_level(team.get("cname", ""), team.get("tname", ""))

    # [2026-08 신설, 신민용 요청: "S등급(레알/바르사 등) 명문팀이 SS등급
    # (잉글랜드) 명문팀한테 밀리면 안 된다 — 단, 브라질은 예외"] 위
    # prestige_bonus는 _target_ovr(일반 곡선)용이라 SS/S 1부(el_fill_rest=
    # True, 스쿼드 전원이 스타 슬롯)에는 적용될 자리가 없었다. 스타 슬롯
    # 전용 보정을 레벨별로 따로 둔다. 브라질은 이미 CONTINENT_OVR_BONUS
    # (남미=0 vs 유럽=+1)와 COUNTRY_OVR_ADJ(-1)로 tier_top 자체가 유럽
    # S등급보다 2점 낮게 잡혀 있어서, 이 보정을 브라질에 따로 빼지 않아도
    # 실측상 유럽 S등급 명문팀보다 확실히 아래에 남는다(검증 완료).
    _star_prestige_bonus = {3: 3.0, 2: 2.0, 1: 1.5}.get(_plevel, 0.0)
    # [2026-08 추가 → 2026-08 재수정, 신민용 리포트: "잉글랜드 명문팀들이
    # 파워랭킹 안에 들어갈 수 있는데 안 들어간다 — 다른 나라 최상위권보다
    # OVR가 낮은 듯하다"] 위 +2.0 보정은 "SS의 구조적 우위: tier_top 100
    # vs S 96~97"를 상쇄하려고 도입됐는데, 그 근거였던 tier_top 계산식
    # 자체가 이후(COUNTRY_LEAGUE_OVR_OVERRIDE 도입)에 바뀌었다 —
    # _tier_top_ovr()는 tier==1이고 그 나라가 오버라이드 표에 있으면 이제
    # OVR_RANGES 공식이 아니라 오버라이드 상한을 그대로 쓴다. 그 표를 보면
    # 잉글랜드(88~98)·스페인(89~98)·프랑스(88~98)의 tier_top은 이미
    # 전부 98로 동률이고, 독일·이탈리아(87~97)만 97로 한 단계 아래다 —
    # 즉 "SS가 100이라 압도적으로 유리하다"는 이 보정의 전제 자체가 더
    # 이상 사실이 아닌데, 보정만 그대로 남아서 이제는 반대로 S등급
    # (레알/바르사/PSG 등)이 잉글랜드보다 더 세게 나오는 원인이 됐다
    # (실측 5회 평균: 잉글랜드 레벨3 95.53 vs S(비브라질) 레벨3 96.10,
    # 잉글랜드가 오히려 0.56점 낮음 — 신민용 리포트와 일치). tier_top이
    # 이미 오버라이드로 동률(98=98) 맞춰져 있으므로, 이 보정은 더 이상
    # 필요 없어 제거한다 — 제거 후엔 잉글랜드/스페인/프랑스가 동일한
    # tier_top(98) + 동일한 레벨3 기본 보너스(3.0)로 사실상 동급이 되고,
    # 독일/이탈리아(tier_top 97)는 자연스럽게 한 단계 아래로 남는다.

    # 해당 국가 이름풀 전체를 가져온다 (리그 8팀 × 11명 = 최대 88개 필요)
    # [2026-08 최적화] 국가당 한 번만 SELECT, 이후 팀들은 캐시 재사용.
    _cid = team["cid"]
    if name_pool_cache is not None and _cid in name_pool_cache:
        name_pool = name_pool_cache[_cid]
    else:
        c.execute("SELECT name FROM player_names WHERE country_id=?", (_cid,))
        name_pool = [r["name"] for r in c.fetchall()]
        if not name_pool:
            name_pool = [f"선수{i}" for i in range(100)]
        if name_pool_cache is not None:
            name_pool_cache[_cid] = name_pool

    # [2026-07 신설] 스타 슬롯(월드클래스/엘리트) 명시적 배정 — 완만한 곡선
    # (_target_ovr)만으로는 "이 팀에 월클이 몇 명"이 보장되지 않아서, 소수
    # 슬롯을 뽑아 리그 상한 근처 OVR로 직접 꽂아 넣는다.
    tier_top = _tier_top_ovr(grade, tier, continent_bonus, team.get("cname", ""))
    n_world, n_elite = _star_counts(grade, team_strength, continent_bonus, tier=tier,
                                     country=team.get("cname", ""), prestige_level=_plevel)
    # [2026-08 수정, 벤치 인원 확장] 스타 슬롯은 후보(벤치) 자리엔 절대
    # 배정하지 않는다 — 주전 11자리(TEAM_STARTER_COUNT) 안에서만 추첨.
    star_slot_idx = list(range(TEAM_STARTER_COUNT))
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
    # [2026-08 수정, 벤치 인원 확장] 이 role_idx(0~10) 셔플도 주전 11자리
    # 안에서만 이뤄진다 — _target_ovr의 role_mult 스케일(role_idx/10.0)이
    # "11자리 중 순번"을 전제로 튜닝돼 있어, 후보 7자리를 여기 섞으면 그
    # 스케일이 깨진다(후보는 아래에서 _bench_target_ovr로 별도 처리).
    non_star_positions = [i for i in range(TEAM_STARTER_COUNT) if i not in star_kind_by_slot]
    remaining_ranks = list(range(n_star, TEAM_STARTER_COUNT))
    random.shuffle(remaining_ranks)
    role_indices = dict(zip(non_star_positions, remaining_ranks))

    _star_cfg = STAR_COUNT_BY_GRADE.get(grade, {})
    # [2026-08 신설] GLOBAL_PRESTIGE_STAR_CFG로 스타 슬롯을 받는 B/C 등급
    # 간판팀은 STAR_COUNT_BY_GRADE에 없어 el_offset이 기본값(6,14)으로
    # 넓게 깎였다 — 이 나라들이 등록한 el_offset이 있으면 그걸 우선한다.
    _gp_cfg = GLOBAL_PRESTIGE_STAR_CFG.get(team.get("cname", ""), {})
    _el_offset = _star_cfg.get("el_offset") or _gp_cfg.get("el_offset", (6, 14))
    # [버그수정 2026-07] 이 88 하한은 "SS/S 1부는 절대 엘리트 미만 없음"이라는
    # 의도였는데 tier 구분이 없어 하위 부수까지 적용되던 것 — 1부에서만
    # 걸리도록 한정한다. 2부 이하의 스타 슬롯(있다면)은 tier_top 기준으로
    # 자연스럽게 낮게 계산된 값을 그대로 쓴다.
    # [2026-08 재수정, 신민용 확정 — 국가별 OVR 분포 실측: "브라질 override
    # 하한(84)을 줘도 이 88 하드플로어에 막혀 84~87대가 아예 안 나온다"]
    # 이 등급 공용 88 하한은 그대로 안전장치로 남기되, 그 나라에
    # COUNTRY_LEAGUE_OVR_OVERRIDE가 명시돼 있으면 그 나라의 override 하한을
    # 우선 쓴다 — override가 없는 S/SS 나라는 기존 88 그대로, override로
    # 국가별 하위권 수준을 세분화한 나라는 그 세분화된 하한이 실제로
    # 의미를 갖도록(그렇지 않으면 override 하한을 아무리 낮춰도 무용지물).
    # [2026-08 버그수정, 신민용 리포트: "KeyError: 0" — 빅5/브라질/대한민국
    # 등 일부 나라를 COUNTRY_LEAGUE_OVR_OVERRIDE에서 딕셔너리 형식({tier:
    # (lo,hi)})으로 바꾸면서, 여기서 튜플 인덱싱(_country_override[0])을
    # 그대로 가정하던 코드가 깨졌다 — 딕셔너리는 정수 0으로 인덱싱할 수
    # 없다. get_ovr_range()가 튜플/딕셔너리 두 형식을 이미 다 처리하므로
    # 그걸 거쳐서 하한을 꺼내도록 통일한다(로직 이원화 방지 — _tier_top_ovr/
    # _target_ovr도 이미 같은 이유로 get_ovr_range를 거친다).
    _country_override = COUNTRY_LEAGUE_OVR_OVERRIDE.get(team.get("cname", ""))
    if tier == 1 and _country_override:
        _elite_floor = get_ovr_range(grade, 1, team.get("cname", ""))[0]
    else:
        _elite_floor = ELITE_FLOOR_BY_GRADE.get(grade) if tier == 1 else None
    _quota_lo, _quota_hi = get_foreign_quota_range(team.get("cname", ""), continent)
    _quota = _quota_hi
    _foreign_count = 0
    # [2026-08 최적화] 선수 11명치 INSERT를 한 명씩 execute()하는 대신 모아뒀다가
    # 팀 끝에서 executemany() 한 번으로 묶는다. 매 execute() 호출마다 발생하는
    # 파이썬↔SQLite 오가는 고정비용(재시도 래퍼/락 진입 등 포함)을 11번 대신 1번만
    # 치르게 된다. 삽입되는 데이터·순서·트랜잭션 범위는 기존과 완전히 동일.
    _rows = []

    for idx, pos in enumerate(TEAM_POSITIONS):
        # 리그 전체에서 아직 안 쓴 이름 우선 사용
        available = [n for n in name_pool if n not in league_used]
        if not available:
            available = name_pool
        name = random.choice(available)
        league_used.add(name)
        if idx >= TEAM_STARTER_COUNT:
            # [2026-08 신설, 벤치 인원 확장] 후보(벤치) 자리 — 스타 슬롯
            # 대상이 아니고, elite_floor(SS/S 1부 "전원 스타" 방어선)도
            # 적용하지 않는다(벤치는 그 방어선보다 확실히 아래여야 함).
            target = _bench_target_ovr(grade, tier, team_strength, idx - TEAM_STARTER_COUNT,
                                       continent_bonus, prestige_bonus, team.get("cname", ""))
        elif idx in star_kind_by_slot:
            # [2026-08 신설] 등급별 감쇠 오버라이드(현재 SS만) — 없으면
            # STAR_STRENGTH_PENALTY_MAX(전역값) 그대로 사용.
            _penalty_max = STAR_STRENGTH_PENALTY_MAX_BY_GRADE.get(grade, STAR_STRENGTH_PENALTY_MAX)
            target = _star_target_ovr(tier_top, star_kind_by_slot[idx], _el_offset, _star_prestige_bonus,
                                      team_strength, penalty_max=_penalty_max)
            if _elite_floor is not None:
                # [2026-07 버그 수정] 엘리트 오프셋의 랜덤 폭(uniform 상한) 때문에
                # 국가보정이 낮은 S급 나라(예: 대륙보정 0인 브라질)에서 드물게
                # 87대까지 내려가 "SS/S는 절대 엘리트 미만 없음" 원칙이 깨질 수
                # 있었다 — 스타 슬롯에도 동일한 바닥을 걸어 항상 88 이상 보장.
                target = max(target, _elite_floor)
        else:
            target = _target_ovr(grade, tier, team_strength, role_indices[idx], continent_bonus,
                                 prestige_bonus, team.get("cname", ""))
            # [방어적 안전장치] SS/S 1부는 el_fill_rest=True라 이 분기(baseline)를
            # 정상적으로는 타지 않지만(전원 스타 배정), 혹시라도 남는 자리가
            # 생기면 "월클+엘리트로만 구성"이 깨지지 않도록 바닥을 걸어둔다.
            if _elite_floor is not None and tier == 1:
                target = max(target, _elite_floor)
        stats = _gen_ai_stats(pos, target)
        ovr = calc_ovr(pos, stats)
        # [2026-08 버그수정, 신민용 리포트: "새 게임으로 확인해도 여전히
        # 84 밑으로 새는 선수가 있다"] _target_ovr가 목표치는 하한 이상으로
        # 잘 잡아도, _gen_ai_stats가 그 목표 주변에 가우시안 노이즈(표준편차
        # 3~4)를 섞어 실제 스탯을 생성하다 보니, calc_ovr로 재계산한 최종
        # OVR이 우연히 목표보다 몇 점 낮게 나올 수 있었다(실측: 목표 84인데
        # 실제 78까지 하락). 최종 OVR이 그 등급/티어/나라의 절대 하한보다
        # 낮으면, 부족한 만큼 전체 스탯에 균등하게 더해 다시 맞춘다 — 스탯
        # 간 상대적 개성(강점/약점 분포)은 그대로 유지하면서 최종 OVR만
        # 하한 이상으로 끌어올린다(rescale_team_to_target_ovr과 동일 원리).
        _abs_rng = get_ovr_range(grade, tier, team.get("cname", ""))
        if _abs_rng and ovr < _abs_rng[0]:
            _deficit = _abs_rng[0] - ovr
            for _s in ALL_STATS:
                stats[_s] = min(99, max(1, int(round(stats[_s] + _deficit))))
            ovr = calc_ovr(pos, stats)
        # [AI 생애] 초기 나이: 16~34 삼각분포(25 봉우리). 시즌마다 +1 되며 성장/노화.
        age = int(round(random.triangular(16, 34, 25)))
        # [세부역할 2026-07] 포지션에 맞는 SUB_ROLES 중 하나를 무작위 배정.
        sub_role = random.choice(SUB_ROLES.get(pos, ["기본"]))
        nationality, _foreign_count = _pick_nationality(
            team.get("cname", ""), continent, grade, pos,
            idx in star_kind_by_slot, _foreign_count, _quota)
        _rows.append((team["tid"],name,pos,
             stats["stamina"],stats["speed"],stats["jump"],stats["strength"],
             stats["shooting"],stats["passing"],stats["dribbling"],
             stats["tackling"],stats["heading"],stats["positioning"],
             stats["setpiece"],stats["mental"],stats["confidence"],
             stats["leadership"],stats["concentration"],ovr,age,sub_role,nationality))

    # [2026-08 신설, 외국인 보유 범위표 하한 보장] 확률 기반 배정만으로는
    # 상한(quota_hi)은 정확히 지켜지지만 하한(quota_lo)은 못 미칠 수 있다
    # (특히 잉글랜드처럼 하한이 높은 나라). 벤치(후보) 자리부터 우선해서
    # 자국 선수 일부를 외국인으로 바꿔 하한을 맞춘다 — 팀 생성 시점 1회만
    # 적용, 이후 이적/은퇴교체는 자연스러운 변동을 그대로 둔다.
    _foreign_count = _topup_foreign_floor(
        _rows, star_kind_by_slot, team.get("cname", ""), continent,
        _quota_lo, _foreign_count)

    c.executemany("""INSERT INTO ai_players
        (team_id,name,position,stamina,speed,jump,strength,shooting,passing,
         dribbling,tackling,heading,positioning,setpiece,
         mental,confidence,leadership,concentration,ovr,age,sub_role,nationality)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", _rows)


def _gen_ai_stats(pos, target):
    """목표 OVR을 받아 그 값에 수렴하도록 스탯을 역산 생성.
    키스탯은 가중치가 높으므로 목표보다 약간 높게, 비키스탯은 약간 낮게 둔다.
    + 신체 아키타입(체형)에 따른 stat_bias 를 더해 종결자/음속/포켓로켓/발전기
      유형의 개성을 부여한다(포지션이 확률을 기울이되 고정하지 않음)."""
    keys = KEY_STATS_BY_POS.get(pos, ALL_STATS[:5])
    adj = target + 1.0   # calc_ovr 하향편향(가중분산) 보정

    # 아키타입 추첨 (포지션 가중치 기반, 예외 허용)
    _w = BODY_TYPE_WEIGHTS_BY_POS.get(pos, [25, 25, 25, 25])
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