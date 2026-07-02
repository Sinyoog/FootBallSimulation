"""
database.py - 전체 SQLite 기반. JSON 없음.
"""
import sqlite3, os, random
from data.countries import COUNTRY_DATA
from data.leagues import LEAGUE_DATA
from data.names import NAME_DATA

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game.db")

# ── 커넥션 풀(단일 영속 커넥션 재사용) ────────────────────────────
# 이 게임은 단일 스레드(UI 메인 스레드)에서만 DB를 쓰고, 커넥션을 함수 밖으로
# 넘기지 않는다(모두 함수 내부에서 열고 닫음). 따라서 매 get_conn()마다
# sqlite3.connect + close 하던 것을, 커넥션 하나를 만들어 계속 재사용한다.
#   - 프로파일 결과 connect/close/commit 오버헤드가 전체 실행시간의 ~90%였다.
#   - 반환 커넥션의 close()는 no-op으로 감싼다 → 기존 코드의 conn.close()
#     호출 73곳을 한 줄도 안 고치고 그대로 두면서, 실제로는 닫지 않게 한다.
#   - commit/execute/cursor 등은 실제 커넥션에 그대로 위임된다.
_pool_conn = None

class _PooledConn:
    """sqlite3.Connection 래퍼. close()만 무력화하고 나머진 전부 위임."""
    __slots__ = ("_real",)
    def __init__(self, real):
        object.__setattr__(self, "_real", real)
    def close(self):
        # 풀 커넥션은 닫지 않는다(재사용). 트랜잭션 정리는 commit이 담당.
        pass
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
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # synchronous=NORMAL 은 연결별 설정. WAL(영구 설정)과 함께 매 commit fsync를
    # 생략해 commit 비용을 크게 줄인다. WAL+NORMAL 은 SQLite 공식 권장 조합.
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def get_conn():
    global _pool_conn
    if _pool_conn is None:
        _pool_conn = _PooledConn(_new_raw_conn())
    return _pool_conn

def reset_conn_pool():
    """DB 파일이 교체되는 경우(세이브 로드/삭제 등) 풀 커넥션을 폐기."""
    global _pool_conn
    if _pool_conn is not None:
        try:
            object.__getattribute__(_pool_conn, "_real").close()
        except Exception:
            pass
        _pool_conn = None

# ─── 스키마 ───────────────────────────────────────────────────
def init_db():
    from constants import GAME_START_YEAR, PLAYER_START_AGE
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
        ovr INTEGER DEFAULT 50,
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
    # 오퍼 거절 기록 (기존 코드가 참조하나 생성 누락되어 있던 테이블)
    c.execute("""CREATE TABLE IF NOT EXISTS offer_refused(
        team_id INTEGER, year INTEGER)""")
    # 마이그레이션: 컬럼 추가
    for migration in [
        "ALTER TABLE career_entries ADD COLUMN position TEXT DEFAULT ''",
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
        # [예선] 예선에서 선택해 뛴 나라. 본선 해에 이 나라로 자동 출전(cap-tie 전).
        #  예선 시작 시 리셋되어, 21세 이하면 다음 예선 때 다른 나라 선택 가능.
        "ALTER TABLE my_player ADD COLUMN qual_pledged_nat TEXT DEFAULT ''",
        # [예선 대륙] 예선 대회(wc_qual)가 어느 대륙(연맹)의 예선인지 저장.
        "ALTER TABLE intl_tournaments ADD COLUMN continent TEXT DEFAULT ''",
        # [예선 entries] 내 국적 포함 여부 + 소속 대륙
        "ALTER TABLE intl_entries ADD COLUMN is_my INTEGER DEFAULT 0",
        "ALTER TABLE intl_entries ADD COLUMN continent TEXT DEFAULT ''",
        # [오퍼 토글] 재직 중 자동 이적 오퍼 팝업을 끌 수 있는 스위치.
        #  기본값 1(활성) → 기존 세이브도 지금까지와 동일하게 오퍼가 뜬다.
        #  0이어도 '팀 입단'(무소속 강제 입단)과 '이적 요청' 중인 경우는 영향 없음.
        "ALTER TABLE my_player ADD COLUMN offers_enabled INTEGER DEFAULT 1",
        # [전성기 OVR] 커리어 통산 최고 OVR. game_engine.update_player()가 ovr을
        #  갱신할 때마다 자동으로 함께 갱신된다(역대 최고치만 남도록 max 적용).
        #  은퇴 화면 등에서 '최종 OVR'(노쇠로 하락한 값) 대신 전성기 기록을 보여주기 위함.
        "ALTER TABLE my_player ADD COLUMN peak_ovr INTEGER DEFAULT 0",
    ]:
        try: c.execute(migration)
        except: pass

    # [전성기 OVR 보정] 기존 세이브는 peak_ovr 컬럼이 방금 0으로 추가됐거나,
    #  아직 한 번도 update_player(ovr=...)가 안 불려서 현재 ovr보다 낮을 수 있다.
    #  현재 ovr을 하한으로 보정 (peak_ovr < ovr 인 경우만) — 매 시작마다 실행되지만
    #  조건에 안 걸리면 UPDATE 0행이라 사실상 무비용.
    try:
        c.execute("UPDATE my_player SET peak_ovr = ovr WHERE peak_ovr < ovr")
    except: pass

    # ─── 성능 인덱스 ───────────────────────────────────────────
    # 매 주차 진행 시 AI 경기 시뮬·순위 집계가 ai_players / match_results를
    # team_id·week·league_id 조건으로 수없이 조회한다. 인덱스가 없으면
    # 매 호출이 전체 테이블 풀스캔(ai_players 2.6만행)이라 한 달 진행에
    # 수천 ms가 걸린다. 아래 인덱스로 호출당 비용을 O(N)→O(log N)로 낮춘다.
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_aiplayers_team   ON ai_players(team_id)",
        "CREATE INDEX IF NOT EXISTS idx_mr_week_season   ON match_results(week, season)",
        "CREATE INDEX IF NOT EXISTS idx_mr_league_season ON match_results(league_id, season)",
        "CREATE INDEX IF NOT EXISTS idx_teams_league     ON teams(league_id)",
        "CREATE INDEX IF NOT EXISTS idx_leagues_country  ON leagues(country_id)",
        # intl/cl 경기 조회: tournament_id+week 복합 (매 주차 process_*_week 호출마다 사용)
        "CREATE INDEX IF NOT EXISTS idx_intl_matches_tid_week ON intl_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_intl_entries_tid      ON intl_entries(tournament_id)",
        "CREATE INDEX IF NOT EXISTS idx_cl_matches_tid_week   ON cl_matches(tournament_id, week)",
        "CREATE INDEX IF NOT EXISTS idx_cl_entries_tid        ON cl_entries(tournament_id)",
        # _calc_clean_sheets: season+home_score 로 미완료 경기 필터링
        "CREATE INDEX IF NOT EXISTS idx_mr_season_score ON match_results(season, home_score)",
        # match_results: home/away team_id 조회 (클린시트, 팀 경기 조회)
        "CREATE INDEX IF NOT EXISTS idx_mr_home_team ON match_results(home_team_id, season)",
        "CREATE INDEX IF NOT EXISTS idx_mr_away_team ON match_results(away_team_id, season)",
    ]:
        try: c.execute(idx)
        except: pass

    conn.commit(); conn.close()
    # WAL 모드는 DB 파일에 영구 저장되는 설정. 여기서 1회만 보장하면
    # 이후 get_conn() 들은 매번 PRAGMA 를 실행할 필요가 없다.
    _conn = sqlite3.connect(DB_PATH, timeout=30)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.close()
    # WAL을 켠 뒤 풀 커넥션을 새로 만들게 리셋(이전 풀 커넥션은 WAL 인식 전일 수 있음).
    reset_conn_pool()
    remap_all_ovr()   # calc_ovr 정규화에 맞춰 기존 AI OVR 일괄 재계산 (1회성)
    migrate_money_to_thousand()   # 금액 단위 만원→천원 전환 (1회성)


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
        for r in rows:
            stats = {s: r[s] for s in ALL_STATS}
            new_ovr = calc_ovr(r["position"], stats)
            c.execute("UPDATE ai_players SET ovr=? WHERE id=?", (new_ovr, r["id"]))
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


def seed_initial_data():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT value FROM meta WHERE key='seeded'")
    if c.fetchone(): conn.close(); return
    print("초기 데이터 삽입 중...")
    _insert_countries(c)
    _insert_leagues_and_teams(c)
    _insert_player_names(c)
    _generate_all_ai_players(c)
    c.execute("INSERT INTO meta VALUES('seeded','1')")
    conn.commit(); conn.close()
    print("완료")

def reset_game_data():
    init_db()  # 마이그레이션 적용
    conn = get_conn(); c = conn.cursor()
    for t in ["my_player","career_entries","promotion_log","trophy_log","awards",
              "game_log","match_results","match_details","season_state",
              "intl_history","intl_tournaments","intl_entries","intl_matches",
              "cl_tournaments","cl_entries","cl_matches","cl_history"]:
        c.execute(f"DELETE FROM {t}")
    c.execute("UPDATE teams SET wins=0,draws=0,losses=0,goals_for=0,goals_against=0")
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

        cur = conn.cursor()
        for r in rows:
            new_stats = {}
            for s in ALL_STATS:
                new_stats[s] = min(99, max(1, int(r[s]) + delta))
            new_ovr = calc_ovr(r["position"], new_stats)
            cur.execute(
                """UPDATE ai_players SET
                   stamina=?,speed=?,jump=?,strength=?,shooting=?,passing=?,
                   dribbling=?,tackling=?,heading=?,positioning=?,setpiece=?,
                   mental=?,confidence=?,leadership=?,concentration=?,ovr=?
                   WHERE id=?""",
                (new_stats["stamina"], new_stats["speed"], new_stats["jump"],
                 new_stats["strength"], new_stats["shooting"], new_stats["passing"],
                 new_stats["dribbling"], new_stats["tackling"], new_stats["heading"],
                 new_stats["positioning"], new_stats["setpiece"], new_stats["mental"],
                 new_stats["confidence"], new_stats["leadership"],
                 new_stats["concentration"], new_ovr, r["id"]))
        if own:
            conn.commit()

        after = conn.execute(
            "SELECT AVG(ovr) AS v FROM ai_players WHERE team_id=?",
            (team_id,)).fetchone()
        after_avg = float(after["v"]) if after and after["v"] is not None else before_avg
        return (delta, before_avg, after_avg)
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
    for (name,flag,cont,lang,rank) in COUNTRY_DATA:
        grade = _grade_from_rank(rank)
        c.execute("INSERT INTO countries(name,flag,continent,language,fifa_rank,grade) VALUES(?,?,?,?,?,?)",
                  (name,flag,cont,lang,rank,grade))


def sync_countries():
    """COUNTRY_DATA 기준 동기화 (멱등, 매 실행 시 호출).
    - 신규 국가: INSERT (LEAGUE_DATA에 없으면 자동으로 '이름만 국가')
    - 기존 국가: fifa_rank/grade/flag/continent/language 갱신
    기존 세이브에도 새 국가가 반영되도록 seed 가드 바깥에서 실행."""
    conn = get_conn(); c = conn.cursor()
    for (name, flag, cont, lang, rank) in COUNTRY_DATA:
        grade = _grade_from_rank(rank)
        row = c.execute("SELECT id FROM countries WHERE name=?", (name,)).fetchone()
        if row:
            c.execute("""UPDATE countries SET flag=?, continent=?, language=?,
                         fifa_rank=?, grade=? WHERE id=?""",
                      (flag, cont, lang, rank, grade, row["id"]))
        else:
            c.execute("""INSERT INTO countries(name,flag,continent,language,fifa_rank,grade)
                         VALUES(?,?,?,?,?,?)""",
                      (name, flag, cont, lang, rank, grade))
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


def _insert_leagues_and_teams(c):
    c.execute("SELECT id, name FROM countries")
    cmap = {r["name"]: r["id"] for r in c.fetchall()}
    for country_name, tiers in LEAGUE_DATA.items():
        cid = cmap.get(country_name)
        if cid is None: continue
        for tier_key, (league_name, teams) in tiers.items():
            tier = _tier_to_int(tier_key)
            c.execute("INSERT INTO leagues(country_id,tier,name) VALUES(?,?,?)",
                      (cid, tier, league_name))
            lid = c.lastrowid
            for team_name in teams:
                c.execute("INSERT INTO teams(league_id,country_id,name,formation,current_tier) VALUES(?,?,?,?,?)",
                          (lid, cid, team_name, random.choice(FORMATIONS), tier))


# ─── 이름 데이터 ──────────────────────────────────────────────
def _clean(n):
    # "이름(Romanization)" 형태에서 앞부분만 추출
    return n.split("(")[0].strip()



def _insert_player_names(c):
    c.execute("SELECT id, name FROM countries")
    cmap = {r["name"]: r["id"] for r in c.fetchall()}
    for country, names in NAME_DATA.items():
        cid = cmap.get(country)
        if cid is None: continue
        for n in names:
            clean = _clean(n)
            if clean:
                c.execute("INSERT INTO player_names(country_id,name) VALUES(?,?)",
                          (cid, clean))


# ─── AI 선수 생성 ──────────────────────────────────────────────
OVR_RANGES = {
    "SS":{1:(93,100),2:(83,92),3:(70,80),4:(55,65)},   # EPL 단독 최상위
    "S": {1:(88,95), 2:(76,86),3:(63,73),4:(48,58),5:(33,47)},
    "A": {1:(82,90), 2:(68,78),3:(53,63),4:(38,48)},
    "B": {1:(75,82), 2:(63,70),3:(52,58),4:(35,45)},
    "C": {1:(65,73), 2:(55,62),3:(45,52),4:(30,40)},
    "D": {1:(55,63), 2:(45,53),3:(35,43)},
    "E": {1:(45,53), 2:(35,43),3:(28,35)},
    "F": {1:(35,43), 2:(27,35),3:(20,27)},
}
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
TEAM_ROLE_PROFILE = {
    # ace_lo: 최약팀 에이스 = tier_top * ace_lo (강팀은 *1.0까지)
    # spread: 에이스 대비 11번째 선수(벤치) 하락폭.
    # ── 6대 리그 기준 목표 ──
    # SS(EPL):  리그avg 93, 탑팀avg 96.5, 최약팀avg 89.7, 최약벤치 86.5
    # S(빅4):   리그avg 90, 탑팀avg 93.6, 최약팀avg 87.1, 최약벤치 83.9
    # A(에레디): 리그avg 85.7, 탑팀avg 88.8, 최약팀avg 82.6, 최약벤치 78.7
    "SS": {"ace_lo": 0.93, "spread": 0.07},
    "S":  {"ace_lo": 0.93, "spread": 0.07},
    "A":  {"ace_lo": 0.93, "spread": 0.09},
    "B":  {"ace_lo": 0.91, "spread": 0.11},
    "C":  {"ace_lo": 0.89, "spread": 0.13},
    "D":  {"ace_lo": 0.87, "spread": 0.16},
    "E":  {"ace_lo": 0.85, "spread": 0.18},
    "F":  {"ace_lo": 0.83, "spread": 0.20},
}


def _tier_top_ovr(grade, tier, continent_bonus=0):
    """그 등급·tier 리그에서 도달 가능한 최고 OVR.
    continent_bonus: 대륙별 OVR 보정치 (유럽+1, 아시아-3 등)"""
    rng = OVR_RANGES.get(grade, {}).get(tier)
    if rng:
        return min(100, rng[1] + continent_bonus)
    return 45


def _target_ovr(grade, tier, team_strength, role_idx, continent_bonus=0):
    """팀 강도(0~1) + 역할 순번(0=에이스 … 10=막내)으로 목표 OVR 산출."""
    prof = TEAM_ROLE_PROFILE.get(grade, TEAM_ROLE_PROFILE["F"])
    top = _tier_top_ovr(grade, tier, continent_bonus)
    # 팀 에이스 목표: 강팀일수록 리그 top에 근접
    ace = top * (prof["ace_lo"] + (1.0 - prof["ace_lo"]) * team_strength)
    role_mult = 1.0 - prof["spread"] * (role_idx / 10.0)
    return ace * role_mult


def _generate_all_ai_players(c):
    # 리그 단위로 묶어 8팀에 강→약 강도를 분배해야 팀 간 위계가 생긴다.
    # [리그등급 분리] cn.grade는 국대 등급 → 리그 OVR/연봉엔 COUNTRY_LEAGUE_GRADE 사용
    c.execute("""SELECT t.id AS tid, t.current_tier AS tier, cn.grade AS grade,
                        cn.id AS cid, t.league_id AS lid, cn.name AS cname,
                        cn.continent AS continent
                 FROM teams t JOIN leagues l ON t.league_id=l.id
                 JOIN countries cn ON l.country_id=cn.id
                 ORDER BY t.league_id, t.id""")
    rows = [dict(r) for r in c.fetchall()]

    # 리그별 그룹핑
    leagues: dict = {}
    for r in rows:
        leagues.setdefault(r["lid"], []).append(r)

    for lid, teams in leagues.items():
        n = len(teams)
        order = list(range(n))
        random.shuffle(order)
        league_used: set = set()
        for rank, team in zip(order, teams):
            team_strength = 1.0 - (rank / (n - 1)) if n > 1 else 1.0
            # [리그등급 분리] 국대 등급(grade) 대신 리그 전용 등급 사용
            from constants import get_league_grade
            league_grade = get_league_grade(team.get("cname", ""), team["grade"])
            team_with_lg = dict(team)
            team_with_lg["grade"] = league_grade
            _generate_team_players(c, team_with_lg, team_strength, league_used)


def _generate_team_players(c, team, team_strength, league_used: set = None):
    grade = team["grade"]; tier = team["tier"]
    continent = team.get("continent", "유럽")
    if league_used is None:
        league_used = set()

    # 대륙별 OVR 보정치
    from constants import CONTINENT_OVR_BONUS
    continent_bonus = CONTINENT_OVR_BONUS.get(continent, 0)
    # SS는 이미 상한(100)에 근접 → 보정 축소 (초과 방지)
    if grade == "SS":
        continent_bonus = min(continent_bonus, 0)

    # 해당 국가 이름풀 전체를 가져온다 (리그 8팀 × 11명 = 최대 88개 필요)
    c.execute("SELECT name FROM player_names WHERE country_id=? ORDER BY RANDOM()",
              (team["cid"],))
    name_pool = [r["name"] for r in c.fetchall()]
    if not name_pool:
        name_pool = [f"선수{i}" for i in range(100)]

    for idx, pos in enumerate(TEAM_POSITIONS):
        # 리그 전체에서 아직 안 쓴 이름 우선 사용
        available = [n for n in name_pool if n not in league_used]
        if not available:
            available = name_pool
        name = random.choice(available)
        league_used.add(name)
        target = _target_ovr(grade, tier, team_strength, idx, continent_bonus)
        stats = _gen_ai_stats(pos, target)
        ovr = calc_ovr(pos, stats)
        # [AI 생애] 초기 나이: 16~34 삼각분포(25 봉우리). 시즌마다 +1 되며 성장/노화.
        age = int(round(random.triangular(16, 34, 25)))
        c.execute("""INSERT INTO ai_players
            (team_id,name,position,stamina,speed,jump,strength,shooting,passing,
             dribbling,tackling,heading,positioning,setpiece,
             mental,confidence,leadership,concentration,ovr,age)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (team["tid"],name,pos,
             stats["stamina"],stats["speed"],stats["jump"],stats["strength"],
             stats["shooting"],stats["passing"],stats["dribbling"],
             stats["tackling"],stats["heading"],stats["positioning"],
             stats["setpiece"],stats["mental"],stats["confidence"],
             stats["leadership"],stats["concentration"],ovr,age))


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