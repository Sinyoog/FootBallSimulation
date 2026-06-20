"""
database.py - 전체 SQLite 기반. JSON 없음.
"""
import sqlite3, os, random

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # journal_mode=WAL 은 DB 파일에 영구 저장되는 설정이라 매 연결마다
    # 다시 지정할 필요가 없다(init_db 에서 1회 보장).
    # synchronous=NORMAL 은 연결별 설정이지만 비용이 거의 없고,
    # WAL 과 함께 쓰면 매 commit 의 디스크 fsync 를 생략해(체크포인트 때만 동기화)
    # commit 비용을 100배가량 줄인다. WAL+NORMAL 은 SQLite 공식 권장 조합으로
    # 전원 차단 시에도 DB 가 손상되지 않는다(최근 몇 트랜잭션만 유실 가능).
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

# ─── 스키마 ───────────────────────────────────────────────────
def init_db():
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
    c.execute("""CREATE TABLE IF NOT EXISTS my_player(
        id INTEGER PRIMARY KEY,
        name TEXT, nationality TEXT, flag TEXT,
        age INTEGER DEFAULT 16, birth_year INTEGER DEFAULT 1990,
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
        current_year INTEGER DEFAULT 1990,
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
    c.execute("""CREATE TABLE IF NOT EXISTS game_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry TEXT, log_type TEXT DEFAULT 'normal',
        year INTEGER DEFAULT 1990, week INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS match_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        league_id INTEGER, week INTEGER,
        home_team_id INTEGER, away_team_id INTEGER,
        home_score INTEGER DEFAULT -1, away_score INTEGER DEFAULT -1,
        season INTEGER, year INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS season_state(
        id INTEGER PRIMARY KEY,
        current_year INTEGER DEFAULT 1990,
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
        ovr REAL, grp TEXT, pot INTEGER, alive INTEGER DEFAULT 1)""")
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
        # [복수국적] 이 대회에서 내가 '어느 나라로' 뛰는지. ''=미정/해당없음.
        # my_selected=3 은 '둘 다 진출 → 대표팀 선택 대기' 상태를 뜻한다.
        "ALTER TABLE intl_tournaments ADD COLUMN my_nat TEXT DEFAULT ''",
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
    ]:
        try: c.execute(migration)
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
    ]:
        try: c.execute(idx)
        except: pass

    conn.commit(); conn.close()
    # WAL 모드는 DB 파일에 영구 저장되는 설정. 여기서 1회만 보장하면
    # 이후 get_conn() 들은 매번 PRAGMA 를 실행할 필요가 없다.
    _conn = sqlite3.connect(DB_PATH, timeout=30)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.close()
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
              "game_log","match_results","season_state",
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

def calc_ovr(position, stats):
    w = WEIGHTS.get(position, WEIGHTS["CM"])
    total = sum(stats.get(s,40)*w.get(s,5) for s in w) / sum(w.values())
    return min(100, max(1, int(round(total))))


# ─── 국가 데이터 (등급 자동 산정: fifa_rank 기준) ─────────────
def _grade_from_rank(rank):
    if rank <= 10: return "S"
    if rank <= 25: return "A"
    if rank <= 50: return "B"
    if rank <= 80: return "C"
    if rank <= 120: return "D"
    if rank <= 160: return "E"
    return "F"

COUNTRY_DATA = [
    # FIFA 랭킹 순. 리그(LEAGUE_DATA) 없는 국가는 자동으로 '이름만 국가'가 됨.
    # 1~20위
    ("아르헨티나","🇦🇷","남미","스페인어",1),
    ("프랑스","🇫🇷","유럽","프랑스어",2),
    ("스페인","🇪🇸","유럽","스페인어",3),
    ("잉글랜드","🏴󠁧󠁢󠁥󠁮󠁧󠁿","유럽","영어",4),
    ("브라질","🇧🇷","남미","포르투갈어",5),
    ("포르투갈","🇵🇹","유럽","포르투갈어",6),
    ("네덜란드","🇳🇱","유럽","네덜란드어",7),
    ("벨기에","🇧🇪","유럽","프랑스어",8),
    ("크로아티아","🇭🇷","유럽","크로아티아어",9),
    ("이탈리아","🇮🇹","유럽","이탈리아어",10),
    ("미국","🇺🇸","북미","영어",11),
    ("콜롬비아","🇨🇴","남미","스페인어",12),
    ("모로코","🇲🇦","아프리카","아랍어",13),
    ("우루과이","🇺🇾","남미","스페인어",14),
    ("멕시코","🇲🇽","북미","스페인어",15),
    ("독일","🇩🇪","유럽","독일어",16),
    ("세네갈","🇸🇳","아프리카","프랑스어",17),
    ("일본","🇯🇵","아시아","일본어",18),
    ("스위스","🇨🇭","유럽","독일어",19),
    ("이란","🇮🇷","아시아","페르시아어",20),
    # 21~40위
    ("덴마크","🇩🇰","유럽","덴마크어",21),
    ("대한민국","🇰🇷","아시아","한국어",22),
    ("호주","🇦🇺","오세아니아","영어",23),
    ("우크라이나","🇺🇦","유럽","우크라이나어",24),
    ("오스트리아","🇦🇹","유럽","독일어",25),
    ("터키","🇹🇷","유럽","터키어",26),
    ("에콰도르","🇪🇨","남미","스페인어",27),
    ("스웨덴","🇸🇪","유럽","스웨덴어",28),
    ("웨일스","🏴󠁧󠁢󠁷󠁬󠁳󠁿","유럽","영어",29),
    ("나이지리아","🇳🇬","아프리카","영어",30),
    ("헝가리","🇭🇺","유럽","헝가리어",31),
    ("폴란드","🇵🇱","유럽","폴란드어",32),
    ("튀니지","🇹🇳","아프리카","아랍어",33),
    ("카타르","🇶🇦","아시아","아랍어",34),
    ("세르비아","🇷🇸","유럽","세르비아어",35),
    ("이집트","🇪🇬","아프리카","아랍어",36),
    ("러시아","🇷🇺","유럽","러시아어",37),
    ("코트디부아르","🇨🇮","아프리카","프랑스어",38),
    ("스코틀랜드","🏴󠁧󠁢󠁳󠁣󠁴󠁿","유럽","영어",39),
    ("체코","🇨🇿","유럽","체코어",40),
    # 41~60위
    ("알제리","🇩🇿","아프리카","아랍어",41),
    ("칠레","🇨🇱","남미","스페인어",42),
    ("페루","🇵🇪","남미","스페인어",43),
    ("파나마","🇵🇦","북미","스페인어",44),
    ("루마니아","🇷🇴","유럽","루마니아어",45),
    ("노르웨이","🇳🇴","유럽","노르웨이어",46),
    ("말리","🇲🇱","아프리카","프랑스어",47),
    ("캐나다","🇨🇦","북미","영어",48),
    ("슬로바키아","🇸🇰","유럽","슬로바키아어",49),
    ("그리스","🇬🇷","유럽","그리스어",50),
    ("슬로베니아","🇸🇮","유럽","슬로베니아어",51),
    ("코스타리카","🇨🇷","북미","스페인어",52),
    ("사우디아라비아","🇸🇦","아시아","아랍어",53),
    ("베네수엘라","🇻🇪","남미","스페인어",54),
    ("자메이카","🇯🇲","북미","영어",55),
    ("파라과이","🇵🇾","남미","스페인어",56),
    ("이라크","🇮🇶","아시아","아랍어",57),
    ("카메룬","🇨🇲","아프리카","프랑스어",58),
    ("남아프리카공화국","🇿🇦","아프리카","영어",59),
    ("아일랜드","🇮🇪","유럽","영어",60),
    # 61~80위
    ("핀란드","🇫🇮","유럽","핀란드어",61),
    ("부르키나파소","🇧🇫","아프리카","프랑스어",62),
    ("알바니아","🇦🇱","유럽","알바니아어",63),
    ("우즈베키스탄","🇺🇿","아시아","우즈베크어",64),
    ("몬테네그로","🇲🇪","유럽","몬테네그로어",65),
    ("카보베르데","🇨🇻","아프리카","포르투갈어",66),
    ("아랍에미리트","🇦🇪","아시아","아랍어",67),
    ("가나","🇬🇭","아프리카","영어",68),
    ("북마케도니아","🇲🇰","유럽","마케도니아어",69),
    ("DR콩고","🇨🇩","아프리카","프랑스어",70),
    ("요르단","🇯🇴","아시아","아랍어",71),
    ("아이슬란드","🇮🇸","유럽","아이슬란드어",72),
    ("북아일랜드","🏴","유럽","영어",73),
    ("조지아","🇬🇪","유럽","조지아어",74),
    ("기니","🇬🇳","아프리카","프랑스어",75),
    ("오만","🇴🇲","아시아","아랍어",76),
    ("온두라스","🇭🇳","북미","스페인어",77),
    ("이스라엘","🇮🇱","유럽","히브리어",78),
    ("엘살바도르","🇸🇻","북미","스페인어",79),
    ("바레인","🇧🇭","아시아","아랍어",80),
    # 81~100위
    ("불가리아","🇧🇬","유럽","불가리아어",81),
    ("가봉","🇬🇦","아프리카","프랑스어",82),
    ("룩셈부르크","🇱🇺","유럽","룩셈부르크어",83),
    ("볼리비아","🇧🇴","남미","스페인어",84),
    ("잠비아","🇿🇲","아프리카","영어",86),
    ("중국","🇨🇳","아시아","중국어",87),
    ("시리아","🇸🇾","아시아","아랍어",88),
    ("아이티","🇭🇹","북미","프랑스어",89),
    ("퀴라소","🇨🇼","북미","네덜란드어",90),
    ("우간다","🇺🇬","아프리카","영어",91),
    ("앙골라","🇦🇴","아프리카","포르투갈어",92),
    ("베냉","🇧🇯","아프리카","프랑스어",93),
    ("팔레스타인","🇵🇸","아시아","아랍어",94),
    ("아르메니아","🇦🇲","유럽","아르메니아어",95),
    ("벨라루스","🇧🇾","유럽","벨라루스어",96),
    ("트리니다드 토바고","🇹🇹","북미","영어",97),
    ("타지키스탄","🇹🇯","아시아","타지크어",98),
    ("키르기스스탄","🇰🇬","아시아","러시아어",99),
    ("태국","🇹🇭","아시아","태국어",100),
    # 101~120위
    ("코소보","🇽🇰","유럽","알바니아어",101),
    ("카자흐스탄","🇰🇿","아시아","카자흐어",102),
    ("뉴질랜드","🇳🇿","오세아니아","영어",103),
    ("모리타니","🇲🇷","아프리카","아랍어",104),
    ("케냐","🇰🇪","아프리카","영어",105),
    ("과테말라","🇬🇹","북미","스페인어",106),
    ("마다가스카르","🇲🇬","아프리카","프랑스어",107),
    ("남수단","🇸🇸","아프리카","영어",108),
    ("조선민주주의인민공화국","🇰🇵","아시아","한국어",110),
    ("나미비아","🇳🇦","아프리카","영어",111),
    ("기니비사우","🇬🇼","아프리카","포르투갈어",112),
    ("레바논","🇱🇧","아시아","아랍어",113),
    ("베트남","🇻🇳","아시아","베트남어",114),
    ("콩고공화국","🇨🇬","아프리카","프랑스어",115),
    ("아제르바이잔","🇦🇿","유럽","아제르바이잔어",116),
    ("토고","🇹🇬","아프리카","프랑스어",117),
    ("리비아","🇱🇾","아프리카","아랍어",118),
    ("코모로","🇰🇲","아프리카","아랍어",119),
    ("인도","🇮🇳","아시아","영어",120),
    # 121~140위
    ("말라위","🇲🇼","아프리카","영어",121),
    ("지브롤터","🇬🇮","유럽","영어",122),
    ("에스토니아","🇪🇪","유럽","에스토니아어",123),
    ("키프로스","🇨🇾","유럽","그리스어",124),
    ("탄자니아","🇹🇿","아프리카","영어",125),
    ("짐바브웨","🇿🇼","아프리카","영어",126),
    ("중앙아프리카공화국","🇨🇫","아프리카","프랑스어",127),
    ("니제르","🇳🇪","아프리카","프랑스어",128),
    ("르완다","🇷🇼","아프리카","영어",129),
    ("감비아","🇬🇲","아프리카","영어",130),
    ("인도네시아","🇮🇩","아시아","인도네시아어",131),
    ("말레이시아","🇲🇾","아시아","말레이어",132),
    ("솔로몬 제도","🇸🇧","오세아니아","영어",133),
    ("쿠웨이트","🇰🇼","아시아","아랍어",134),
    ("라트비아","🇱🇻","유럽","라트비아어",135),
    ("리투아니아","🇱🇹","유럽","리투아니아어",136),
    ("페로 제도","🇫🇴","유럽","페로어",137),
    ("필리핀","🇵🇭","아시아","타갈로그어",138),
    ("부룬디","🇧🇮","아프리카","프랑스어",139),
    ("투르크메니스탄","🇹🇲","아시아","투르크멘어",140),
    # 141~160위
    ("안티가 바부다","🇦🇬","북미","영어",141),
    ("수리남","🇸🇷","남미","네덜란드어",142),
    ("에티오피아","🇪🇹","아프리카","암하라어",143),
    ("보츠와나","🇧🇼","아프리카","영어",144),
    ("세인트키츠 네비스","🇰🇳","북미","영어",145),
    ("레소토","🇱🇸","아프리카","영어",146),
    ("도미니카 공화국","🇩🇴","북미","스페인어",147),
    ("에스와티니","🇸🇿","아프리카","영어",148),
    ("몰도바","🇲🇩","유럽","루마니아어",149),
    ("라이베리아","🇱🇷","아프리카","영어",150),
    ("싱가포르","🇸🇬","아시아","영어",151),
    ("니카라과","🇳🇮","북미","스페인어",152),
    ("예멘","🇾🇪","아시아","아랍어",153),
    ("아프가니스탄","🇦🇫","아시아","페르시아어",154),
    ("홍콩","🇭🇰","아시아","중국어",155),
    ("중화 타이베이","🇹🇼","아시아","중국어",156),
    ("뉴칼레도니아","🇳🇨","오세아니아","프랑스어",157),
    ("안도라","🇦🇩","유럽","카탈루냐어",158),
    ("몰디브","🇲🇻","아시아","디베히어",159),
    ("가이아나","🇬🇾","남미","영어",160),
    # 161~180위
    ("타히티","🇵🇫","오세아니아","프랑스어",161),
    ("버뮤다","🇧🇲","북미","영어",162),
    ("미얀마","🇲🇲","아시아","미얀마어",163),
    ("세인트루시아","🇱🇨","북미","영어",164),
    ("파푸아뉴기니","🇵🇬","오세아니아","영어",165),
    ("쿠바","🇨🇺","북미","스페인어",166),
    ("바누아투","🇻🇺","오세아니아","영어",167),
    ("피지","🇫🇯","오세아니아","영어",168),
    ("그레나다","🇬🇩","북미","영어",169),
    ("몬트세랫","🇲🇸","북미","영어",170),
    ("몰타","🇲🇹","유럽","몰타어",171),
    ("벨리즈","🇧🇿","북미","스페인어",172),
    ("푸에르토리코","🇵🇷","북미","스페인어",173),
    ("네팔","🇳🇵","아시아","네팔어",174),
    ("캄보디아","🇰🇭","아시아","크메르어",175),
    ("세인트빈센트 그레나딘","🇻🇨","북미","영어",176),
    ("차드","🇹🇩","아프리카","프랑스어",177),
    ("모리셔스","🇲🇺","아프리카","영어",178),
    ("바베이도스","🇧🇧","북미","영어",179),
    ("마카오","🇲🇴","아시아","중국어",180),
    # 181~211위
    ("사모아","🇼🇸","오세아니아","영어",181),
    ("라오스","🇱🇦","아시아","라오어",182),
    ("상투메 프린시페","🇸🇹","아프리카","포르투갈어",183),
    ("도미니카 연방","🇩🇲","북미","영어",184),
    ("몽골","🇲🇳","아시아","몽골어",186),
    ("아메리칸사모아","🇦🇸","오세아니아","영어",187),
    ("쿠크 제도","🇨🇰","오세아니아","영어",188),
    ("방글라데시","🇧🇩","아시아","벵골어",189),
    ("브루나이","🇧🇳","아시아","말레이어",190),
    ("지부티","🇩🇯","아프리카","아랍어",191),
    ("케이맨 제도","🇰🇾","북미","영어",192),
    ("파키스탄","🇵🇰","아시아","우르두어",193),
    ("세이셸","🇸🇨","아프리카","프랑스어",194),
    ("동티모르","🇹🇱","아시아","포르투갈어",195),
    ("통가","🇹🇴","오세아니아","영어",196),
    ("리히텐슈타인","🇱🇮","유럽","독일어",197),
    ("아루바","🇦🇼","북미","네덜란드어",198),
    ("소말리아","🇸🇴","아프리카","소말리어",199),
    ("터크스 케이커스 제도","🇹🇨","북미","영어",200),
    ("괌","🇬🇺","아시아","영어",201),
    ("영국령 버진아일랜드","🇻🇬","북미","영어",202),
    ("모나코","🇲🇨","유럽","프랑스어",203),
    ("스리랑카","🇱🇰","아시아","싱할라어",204),
    ("미국령 버진아일랜드","🇻🇮","북미","영어",205),
    ("부탄","🇧🇹","아시아","종카어",206),
    ("바하마","🇧🇸","북미","영어",207),
    ("안귈라","🇦🇮","북미","영어",208),
    ("산마리노","🇸🇲","유럽","이탈리아어",209),
    ("에리트레아","🇪🇷","아프리카","티그리냐어",210),
]

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
LEAGUE_DATA = {
    '대한민국': {1: ('K리그1', ['전북현대', '울산HD', 'FC서울', '수원FC', '포항스틸러스', '인천유나이티드', '광주FC', '제주유나이티드']), 2: ('K리그2', ['부산아이파크', '대전하나', '수원삼성', '서울이랜드', '경남FC', 'FC안양', '부천FC1995', '전남드래곤즈']), 3: ('K3리그', ['화성FC', '천안시티', '김포FC', '메오티', '충북청주', '김천상무B', '대전코레일', '경주한수원'])},
    '브라질': {1: ('캄페오나투 세리에A', ['플라멩구', '팔메이라스', '상파울루', '코린치안스', '아틀레티코 미네이루', '플루미넨세', '보타포구', '그레미우']), 2: ('캄페오나투 세리에B', ['산토스', '스포르트 헤시피', '고이아스', '코리티바', '세아라', '아바이', 'CRB', '론드리나']), 3: ('캄페오나투 세리에C', ['파이산두', '나우티코', '피게이렌세', '볼타 레돈다', '이파팅가', '투피', '레모', '브라질 데 펠로타스'])},
    '아르헨티나': {1: ('프리메라 디비시온', ['리버 플레이트', '보카 주니어스', '라싱 클루브', '인디펜디엔테', '산 로렌소', '벨레스 사르스필드', '에스투디안테스', '뉴웰스 올드 보이스']), 2: ('프리메라 B 나시오날', ['콜론', '아르세날', '킬메스', '페로 카릴 오에스테', '차카리타 주니어스', '알도시비', '산마르틴', '아틀레티코 라파엘라']), 3: ('프리메라 B 메트로폴리타나', ['타예레스 B', '로스 안데스', '코무니카시오네스', '데포르티보 아르메니오', '아르헨티노 데 키르메스', '산 미겔', '피닉스', '메를로'])},
    '프랑스': {1: ('리그앙', ['PSG', '마르세유', '올랭피크 리옹', '모나코', '릴', '니스', '스타드 렌', '랑스']), 2: ('리그2', ['낭트', '스트라스부르', '르아브르', '보르도', '클레르몽 페랑', '오세르', '그르노블', '파리FC']), 3: ('샹피오나 나시오날', ['레드 스타', '소쇼', '니오르', '디종', '루앙', '낭시', '르망', '에피날'])},
    '독일': {1: ('분데스리가', ['바이에른 뮌헨', '도르트문트', '레버쿠젠', '라이프치히', '프랑크푸르트', '뢴헨글라트바흐', '슈투트가르트', '볼프스부르크']), 2: ('2 분데스리가', ['함부르크', '샬케04', '헤르타 베를린', '뒤셀도르프', '뉘른베르크', '카이저슬라우테른', '하노버96', '파더보른']), 3: ('3 리가', ['디나모 드레스덴', '산트하우젠', '아르미니아 빌레펠트', '사르브뤼켄', '뒤스부르크', '로스토크', '뮌헨1860', '잉골슈타트'])},
    '스페인': {1: ('라리가', ['레알 마드리드', 'FC 바르셀로나', '아틀레티코 마드리드', '세비야', '레알 소시에다드', '아틀레틱 빌바오', '발렌시아', '레알 베티스']), 2: ('세군다 디비시온', ['에스파뇰', '바야돌리드', '에이바르', '레반테', '엘체', '오비에도', '스포르팅 히혼', '사라고사']), 3: ('프리메라 페데라시온', ['데포르티보 라 코루냐', '말라가', '무르시아', '카스텔론', '폰페라디나', '이비사', '레크레아티보', '코르도바'])},
    '잉글랜드': {1: ('프리미어리그', ['맨체스터 시티', '리버풀', '첼시', '아스날', '맨체스터 유나이티드', '토트넘 홋스퍼', '뉴캐슬 유나이티드', '아스톤 빌라']), 2: ('EFL 챔피언십', ['레스터 시티', '리즈 유나이티드', '사우스햄튼', '선더랜드', '웨스트브롬위치', '미들즈브러', '코번트리 시티', '노리치 시티']), 3: ('EFL 리그 원', ['포츠머스', '더비 카운티', '볼턴 원더러스', '블랙풀', '반슬리', '찰튼 애슬레틱', '위건 애슬레틱', '레딩'])},
    '포르투갈': {1: ('프리메이라리가', ['벤피카', '스포르팅 CP', 'FC 포르투', '브라가', '비토리아 기마랑이스', '파말리캉', '에스토릴', '보아비스타']), 2: ('리가 포르투갈 2', ['마리티무', '산타 클라라', '파수스 드 페레이라', '샤베스', '아로카', '펜나피엘', '레이숑이스', '나시오날']), 3: ('테르세이라 리가', ['벨레넨세스', '아카데미카', '바르짐', '펠게이라스', '스포르팅 코빌랴', '브라가 B', '벤피카 B', '포르투 B'])},
    '이탈리아': {1: ('세리에A', ['인터 밀란', '유벤투스', 'AC 밀란', '나폴리', 'AS 로마', '라치오', '아탈란타', '피오렌티나']), 2: ('세리에B', ['파르마', '삼프도리아', '바리', '팔레르모', '크레모네세', '베네치아', '스페치아', '코모']), 3: ('세리에C', ['카타니아', '페스카라', '스팔', '페루자', '파도바', '체세나', '비첸차', '크로토네'])},
    '네덜란드': {1: ('에레디비시', ['아약스', 'PSV 에인트호번', '페예노르트', 'AZ 알크마르', '트벤터', '위트레흐트', '헤이렌베인', '비테세']), 2: ('에이르스터 디비시', ['그로닝겐', '빌럼 II', '캄뷔르', '에먼', '브레다', '덴 하흐', '로다 JC', '더 흐라프스하프']), 3: ('Tweede 디비시', ['용 아약스', '용 PSV', 'AFC 암스테르담', '카트베이크', '스파켄부르크', '리인스부르크', '퀵 보이즈', '하르덴베르크'])},
    '크로아티아': {1: ('HNL', ['디나모 자그레브', '하이두크 스플리트', '리예카', '오시예크', '로코모티바', '고리차', '이스트라 1961', '슬라벤 벨루포']), 2: ('프르바 NL', ['루데시', '시베니크', '부코바르 1991', '치바리아', '오리엔트', '두브라바', '조르나', '솔린']), 3: ('드루가 NL', ['자그레브', '마르소니아', '믈라도스트', '카를로바츠', '벨리셰', '크리제브치', '오파티야', '야드란'])},
    '덴마크': {1: ('수페르리가', ['코펜하겐', '미트윌란', '브뢴비', '노르셸란', '오르후스', '실케보르', '오덴세', '비보르']), 2: ('1. 디비시온', ['올보르', '호르센스', '바일레', '륑비', '쇠네르위스케', '프레데리시아', '벤시셀', '힐레뢰드']), 3: ('2. 디비시온', ['에스비에르', '코펜하겐 프레드', '티스테드', '네스트베드', '헬싱외르', '브라브란드', '로스킬데', '오르후스 프레드'])},
    '벨기에': {1: ('프로 리그', ['클뤼프 브뤼허', '안데를레흐트', '헹크', '앤트워프', '헨트', '루아얄 윙용', '스탠다드 리에주', '신트트로이던']), 2: ('챌린저 프로 리그', ['쥘터 바레험', '오스텐데', '코르트레이크', '베이르스호트', '베베렌', '롬멜', '리르스', '프랑 보라앵']), 3: ('내셔널 디비시온 1', ['안데를레흐트 B', '클뤼프 브뤼허 B', '샤를루아', '티엔엔', '데셀', '호그스트라텐', '카펠렌', '리에주 B'])},
    '일본': {1: ('J1리그', ['비셀 고베', '요코하마 F. 마리노스', '가와사키 프론탈레', '우라와 레즈', '가시마 앤틀러스', '산프레체 히로시마', '세레소 오사카', '나고야 그램퍼스']), 2: ('J2리그', ['시미즈 에스펄스', '주빌로 이와타', '도쿄 베르디', '몬테디오 야마가타', '제프 유나이티드', '베갈타 센다이', '오이타 트리니타', '요코하마 FC']), 3: ('J3리그', ['오미야 아르디자', '마쓰모토 야마가', '카탈레 도야마', 'FC 류큐', '이와테 그룰라', '가고시마 유나이티드', '나간 파르세이루', '아술 클라로 누마주'])},
    '이란': {1: ('걸프 프로 리그', ['페르세폴리스', '에스테글랄', '세파한', '트락토르', '골 고하르', '풀라드', '조브 아한', '페이칸']), 2: ('아라데간 리그', ['사이브', '파스 테헤란', '나프트 마스제드', '산아트 나프트', '메스 케르만', '에스테글랄 후제스탄', '파르스 자노비', '샤흐르 코드로']), 3: ('이란 2부 리그', ['테헤란 유나이티드', '시라즈 시티', '이스타반 B', '풀라드 B', '세파한 B', '메스 라프산잔 B', '카스피안', '다마시'])},
    '사우디아라비아': {1: ('사우디 프로페셔널리그', ['알 힐랄', '알 나스르', '알 이티하드', '알 아흘리', '알 에티파크', '알 샤밥', '알 타아운', '알 파티']), 2: ('사우디 1부 리그', ['알 가디시야', '알 파이살리', '알 하젬', '알 바틴', '알 알리', '알 웨흐다', '알 알라', '알 아달라']), 3: ('사우디 2부 리그', ['알 제이르', '알 타까둠', '알 나스르 B', '알 힐랄 B', '알 저우프', '알 나즈마', '알 라이얀', '알 쇼알라'])},
    '카타르': {1: ('카타르 스타스 리그', ['알 사드', '알 두하일', '알 가라파', '알 라얀', '알 아라비', '알 와크라', '카타르 SC', '알 아흘리']), 2: ('카타르 2부 리그', ['알 가리아', '알 샤한야', '알 메사이메르', '알 카리티야트', '알 무아이디르', '루사일', '알 비다', '알 와에브']), 3: ('카타르 아마추어 리그', ['알 사드 B', '알 두하일 B', '알 가라파 B', '도하 시티', '스피어스', '알 라얀 B', '펄 FC', '유니버시티 SC'])},
    '우즈베키스탄': {1: ('우즈베키스탄 수페르리가', ['파흐타코르', '나브바호르', '나사프', 'AGMK', '부뇨드코르', '올림픽 타슈켄트', '네프치 페르가나', '소그디아나']), 2: ('우즈베키스탄 프로리그', ['로코모티프 타슈켄트', '디나모 사마르칸트', '마샬 무바레크', '코칸드 1912', '투론', '부하라', '호레즘', '슈르탄']), 3: ('우즈베키스탄 비스트야 리그', ['파흐타코르 B', '나사프 B', '조민', '안디잔 II', '나브바호르 B', '치르치크', '기주반', '양기에르'])},
    '호주': {1: ('A리그 멘', ['시드니 FC', '멜버른 시티', '센트럴코스트 매리너스', '웨스턴 시드니', '멜버른 빅토리', '애들레이드 유나이티드', '브리즈번 로어', '퍼스 글로리']), 2: ('내셔널 프리미어리그', ['시드니 올림픽', '사우스 멜버른', '아피아 라이카르트', '시드니 유나이티드', '멜버른 나이트', '오클레이 캐논스', '캠벨타운', '브리즈번 시티']), 3: ('NPL 디비시온 2', ['시드니 FC B', '멜버른 시티 B', '노스브리지', '세인트 조지', '파라매타', '불린 라이온스', '킹스턴 시티', '이스트 시드니'])},
    '중국': {1: ('중국 슈퍼리그', ['상하이 포트', '산둥 타이산', '상하이 선화', '베이징 궈안', '청두 룽청', '제주앙 프로', '우한 쓰리타운스', '티안진 진먼 타이거']), 2: ('중국 갑급리그', ['광저우 FC', '다롄 프로', '선전 FC', '창춘 야타이', '시안 유나이티드', '스자좡 궁푸', '구앙시 핑궈', '칭다오 웨스트코스트']), 3: ('중국 을급리그', ['상하이 포트 B', '산둥 타이산 B', '베이징 궈안 B', '충칭 구롱', '후난 빌로우즈', '남통 지운 B', '광동 하이인', '하이난 스타'])},
    '인도': {1: ('인도 슈퍼리그', ['모훈 바간', '뭄바이 시티', '고아 FC', '케랄라 블래스터스', '벵갈루루 FC', '오디샤 FC', '동벵골 FC', '첸나이인 FC']), 2: ('I-리그', ['모함메단 SC', '스리니디 데칸', '고쿨람 케랄라', '아이자울 FC', '네로카 FC', '처칠 브라더스', '실롱 라종', '델리 FC']), 3: ('I-리그 2부', ['뭄바이 시티 B', '벵갈루루 B', '고아 B', '케랄라 B', '벵갈루루 유나이티드', '데포 FC', '골든 스레드', '아라 FC'])},
    '미국': {1: ('메이저 리그 사커', ['인터 마이애미', 'LA 갤럭시', 'LAFC', '뉴욕 시티 FC', '시애틀 사운더스', '애틀랜타 유나이티드', '뉴잉글랜드 레볼루션', '오스틴 FC']), 2: ('USL 챔피언십', ['피닉스 라이징', '루이빌 시티', '탬파베이 로우디스', '새크라멘토 리퍼블릭', '찰스턴 배터리', '샌안토니오 FC', '오렌지 카운티', '마이애미 FC']), 3: ('MLS 넥스트 프로', ['마이애미 II', 'LAFC II', '노스 캐롤라이나', '리치먼드 키커스', '그린빌 트라이엄프', '유니온 오마하', '포워드 매디슨', '시애틀 II'])},
    '멕시코': {1: ('리가 MX', ['클럽 아메리카', '티그레스 UANL', '몬테레이', '치바스 과달라하라', '크루스 아술', 'UNAM 푸마스', '레온', '파추카']), 2: ('리가 이스판시온 MX', ['아틀란테', '아틀레티코 모렐리아', '셀라야', '레오네스 네그로스', '시마로네스', '미네로스 사카테카스', '타파티오', '알레브리헤스']), 3: ('세군다 디비시온', ['클럽 아메리카 B', '티그레스 B', '몬테레이 B', '이라푸아토', '탐피코 마데로', '가빌라네스', '유카탄', '콜리마'])},
    '코스타리카': {1: ('리가 FPD', ['사프리사', '알라후엘렌세', '에레디아노', '카르타히네스', '과나카스테카', '산토스 데 구아필레스', '산카를로스', '스포르팅 산호세']), 2: ('리가 데 아센소', ['리베리아', '카르메리타', '푼타레나스', '에스카수세냐', '산타아나', '우루과이 데 코로나도', '투리알바', '골피토']), 3: ('코스타리카 3부 리그', ['사프리사 B', '알라후엘렌세 B', '림바', '과달루페 B', '파라소', '모라비아', '에레디아 B', '산호세 시티'])},
    '콜롬비아': {1: ('카테고리아 프리메라 A', ['아틀레티코 나시오날', '밀로나리오스', '주니어 바랑키야', '아메리카 데 칼리', '인데펜디엔테 메데인', '산타페', '데포르티보 칼리', '아길라스 도라다스']), 2: ('카테고리아 프리메라 B', ['쿠쿠타 데포르티보', '포르탈레자', '레알 카르타헤나', '코르툴루아', '바랑키야 FC', '라네로스', '킨디오', '아틀레티코 FC']), 3: ('콜롬비아 테르세이라 디비시온', ['나시오날 B', '밀로나리오스 B', '주니어 B', '칼리 유나이티드', '메데인 스타즈', '산타페 B', '엔비가도 B', '치코 B'])},
    '우루과이': {1: ('프리메라 디비시온', ['페냐롤', '나시오날', '데펜소르 스포르팅', '다누비오', '리버 플레이트 몬테비데오', '리버풀 몬테비데오', '몬테비데오 시티 토르케', '세로 라르고']), 2: ('세군다 디비시온', ['프로그레소', '미라마르 미시오네스', '렌티스타스', '라루스', '플라사 콜로니아', '알비온', '람플라 주니어스', '후벤투드']), 3: ('프리메라 디비시온 아마추어', ['페냐롤 B', '나시오날 B', '벨라 비스타', '센트랄 에스파뇰', '타쿠아렘보', '로차', '우라칸', '살토'])},
    '에콰도르': {1: ('리가 프로 세리에A', ['LDU 키토', '인데펜디엔테 델 바예', '바르셀로나 SC', '에멜레크', '엘 나시오날', '아우카스', '델핀', '쿠엔카']), 2: ('리가 프로 세리에B', ['마카라', '임바부라', '만타 FC', '9 데 옥투브레', '구아야킬 시티', '차카리타스', '쿠나이부로', '바르가스 토레스']), 3: ('세군다 카테고리아', ['키토 B', '바예 B', '에스메랄다스', '오렌세 B', '아소게스', '포르토비에호', '엠파도르', '리베르타드 B'])},
    '모로코': {1: ('보톨라 프로', ['위다드 AC', '라자 CA', 'FAR 라바트', '베르카네', 'FUS 라바트', '마그레브 페스', '이트하드 탄제르', '올림픽 사피']), 2: ('보톨라 2', ['디파 엘 자디디', 'CODM 메크네스', '스타드 마로캥', '카우캅 마라케시', '올림픽 쿠립가', '라싱 카사블랑카', '샤바브 아틀라스', '살레']), 3: ('국립 아마추어 리그', ['위다드 B', '라자 B', 'FAR B', '우지다', '베니 멜랄', '케니트라', '티투안', '아가디르 B'])},
    '세네갈': {1: ('세네갈 프리메라 디비시온', ['제네라시온 푸트', '디암바르스 FC', '자라프', '피킨', '카사 스포르츠', '렝게르', '테구에스 FC', '고레']), 2: ('세네갈 리그 2', ['다카르 사크레쾨르', 'US 우아캄', '왈리단', '니아리 탈리', '포르 오토놈', '디유포르', '아미티에 FC', '엠부르']), 3: ('세네갈 내셔널 1', ['제네라시온 B', '디암바르스 B', '자라프 B', '카올라크', '지긴쇼르', '티에스 FC', '생루이 스타드', '다카르 시티'])},
    '나이지리아': {1: ('나이지리아 프로페셔널 풋볼 리그', ['에님바', '리버스 유나이티드', '레모 스타스', '플라토 유나이티드', '로비 스타스', '레인저스 유나이티드', '카노 필라스', '콰라 유나이티드']), 2: ('나이지리아 내셔널 리그', ['하트랜드', '카치나 유나이티드', '슈팅 스타스', '선샤인 스타스', '곰베 유나이티드', '니제르 토네이도스', '아비아 워리어스', '바옐사 유나이티드']), 3: ('나이지리아 전국 리그', ['에님바 B', '리버스 B', '레모 B', '아부자 유나이티드', '라고스 시티', '카두나 스타스', '이바단 FC', '조스 유나이티드'])},
    '카메룬': {1: ('카메룬 엘리트 원', ['코톤 스포르', '마네무바', '바멘다', '캐논 야운데', '통네르 야운데', '유니온 두알라', '디나모 두알라', '레스 아스트레스']), 2: ('카메룬 엘리트 투', ['티코 유나이티드', '뉴 스타 두알라', '판테르 방강테', '라싱 바푸삼', '포브 바후삼', '에딩 스포르', '얄레 유나이티드', '에글 로얄']), 3: ('카메룬 리저널 리그', ['코톤 B', '캐논 B', '야운데 시티', '두알라 FC', '가루아 유나이티드', '마루아 FC', '부메아 스타즈', '크리비 FC'])},
    '이집트': {1: ('이집트 프리미어리그', ['알 아흘리', '자말렉', '피라미드 FC', '퓨처 FC', '알 마스리', '스무하', '이티하드 알렉산드리아', '알 이스마일리']), 2: ('이집트 2부 리그', ['가즐 엘 마할라', '아스완 FC', '엔피', '엘 가이시', '세라미카 클레오파트라', '파코 FC', '알 모카울룬', '알 이티하드 알렉산드리아 B']), 3: ('이집트 3부 리그', ['알 아흘리 B', '자말렉 B', '피라미드 B', '탄타 FC', '만수라', '올림픽 클럽', '수에즈 FC', '미니야 FC'])},
    '가나': {1: ('가나 프리미어리그', ['아산테 코토코', '하츠 오브 오크', '메데아마 SC', '아두아나 스타스', '베쳄 유나이티드', '그레이트 올림픽스', '레공 시티스', 'RTU']), 2: ('가나 디비시온 원 리그', ['킹 파이살', '베레쿰 첼시', '사마르텍스', '골드 스타스', '하츠 오브 라이온스', '나시오날 가나', '리버티 프로페셔널스', '테마 유나이티드']), 3: ('가나 2부 리그', ['코토코 B', '하츠 B', '아크라 시티', '쿠마시 스타즈', '타코라디 FC', '타말레 유나이티드', '케이프코스트', '오부아시'])},
    '튀니지': {1: ('튀니지 리그 프로페시엘 1', ['에스페랑스 드 튀니스', '에투알 뒤 사헬', '클럽 아프리캥', 'CS 스팍시엔', 'US 모나스티르', '스타드 튀니지앵', 'US 벤 게르단', '클럽 비제르탱']), 2: ('튀니지 리그 프로페시엘 2', ['CS 함맘-리프', 'AS 마르사', 'JS 카이루안', 'EGS 가프사', '올림픽 베자', 'ES 자르지스', 'AS 가베스', '스타드 가베시엔']), 3: ('튀니지 리그 3', ['에스페랑스 B', '사헬 B', '튀니스 시티', '스팍스 유나이티드', '카세린', '타타우인', '토죄르', '바디 FC'])},
    '알제리': {1: ('알제리 리그 프로페시엘 1', ['CR 벨루이즈다드', 'CS 콘스탄틴', 'USM 알제', 'JS 카빌리', 'MC 알제', 'ES 세티프', 'JS 사우라', 'Paradou AC']), 2: ('알제리 리그 2', ['AS오란', 'NA 후세인 데이', 'RC 렐리잔', 'WA 틀렘센', 'AS 보르드즈', 'MO 베자이아', 'USM 엘 하라치', 'JSM 베자이아']), 3: ('알제리 아마추어 인터-레전', ['벨루이즈다드 B', '콘스탄틴 B', '알제 시티', '오란 유나이티드', '블리다', '모스타가넴', '구엘마', '바트나'])},
    '말라위': {1: ('말라위 슈퍼리그', ['냐사 빅 불리츠', '실버 스트라이커스', '마이티 원더러스', '시빌 서비스 수페르스', '치티파 유나이티드', '에콰엔디 로버스', '캄주 바라크스', '모얄레 바라크스']), 2: ('말라위 리전 리그', ['블란타이어 유나이티드', '음주주 시티', '좀바 워리어스', '릴롱궤 스타스', '치카와 FC', '카롱가 유나이티드', '은코타코타', '데드자 다이내모스']), 3: ('말라위 디스트릭트 리그', ['빅 불리츠 B', '실버 B', '원더러스 B', '블란타이어 B', '릴롱궤 B', '치주물루 유나이티드 FC', '망고치 FC', '카상구'])},
    '스위스': {1: ('스위스 슈퍼리그', ['영 보이스', '바젤', '취리히', '루가노', '세르베트', '루체른', '장크트갈렌', '로잔 스포르']), 2: ('스위스 챌린지 리그', ['툰', '파두츠', '샤프하우젠', '빌', '아라우', '뇌샤텔 자막스', '스타드 로잔', '벨린조나']), 3: ('스위스 프로모션 리그', ['바젤 II', '취리히 II', '영 보이스 II', '라퍼스빌-조나', '샹플랭', '브릴', '바덴', '델레몽'])},
    '오스트리아': {1: ('오스트리아 분데스리가', ['레드불 잘츠부르크', '슈투름 그라츠', 'LASK 린츠', '라피드 빈', '아우스트리아 빈', '볼프스베르크', '하르트베르크', '아우스트리아 클라겐푸르트']), 2: ('오스트리아 2. 리가', ['리드', '티롤', '아우스트리아 루스테나우', '장크트푈텐', 'FC 리퍼링', '그라처 AK', '블라우바이스 린츠', '암스텐텐']), 3: ('리오날리가', ['잘츠부르크 B', '라피드 빈 II', '슈투름 그라츠 II', '비너 노이슈타트', '파르스도르프', '브레겐츠', '도르비른', '글라이스도르프'])},
    '우크라이나': {1: ('우크라이나 프리미어리그', ['샤흐타르 도네츠크', '디나모 키이우', '드니프로-1', '조랴 루한스크', '크리브바스', '폴리샤 지토미르', '보르스클라 폴타바', '루크 리비우']), 2: ('우크라이나 퍼스트 리그', ['메탈리스트 1925', '미나이', '베레스 로브노', '오볼론 키이우', '인굴레츠', '카르파티 리비우', '에피센테르', '레프트 뱅크']), 3: ('우크라이나 세컨드 리그', ['샤흐타르 B', '디나모 B', '드니프로 B', '니바 비니차', '스칼라', '레알 파르마', '차이카', '트로스탸네츠'])},
    '터키': {1: ('쉬페르리그', ['갈라타사라이', '페네르바체', '베식타스', '트라브존스포르', '바샥셰히르', '아다나 데미르스포르', '안탈리아스포르', '카슴파샤']), 2: ('TFF 1. 리그', ['앙카라귀쥐', '파티흐 카라귐뤼크', '이스탄불스포르', '펜디크스포르', '괴즈테페', '에유프스포르', '사카리아스포르', '코자엘리스포르']), 3: ('TFF 2. 리그', ['부르사스포르', '알타이', '기레순스포르', '데니즐리스포르', '카르시야카', '바투만', '반스포르', '메르신'])},
    '스웨덴': {1: ('알스벤스칸', ['말뫼 FF', '엘프스보리', '헤켄', '유르고르덴', '함마르뷔', 'AIK 솔나', '노르셰핑', 'IFK 예테보리']), 2: ('수페레탄', ['헬싱보리', '데게르포르스', '바르베르스', '외스테르스', '우치크텐스', '트렐레보리', '외레브로', '옌셰핑']), 3: ('에탄 디비시온 1', ['말뫼 B', '유르고르덴 B', 'AIK B', '팔켄베리', '융스킬레', '산드비켄', '바사룬드', '스톡홀름 인터'])},
    '웨일스': {1: ('웨일스 프리미어리그', ['더 뉴 세인츠', '코나스 키 노마즈', '발라 타운', '페니본트', '카디프 메트로폴리탄', '뉴타운 FC', '하버포드웨스트', '에버리스트위스']), 2: ('심루 노스 / 심루 사우스', ['플린트 타운', '에어버스 UK', '포트매도그', '바리 타운', '브리튼 페리', '카이르수스', '덴비 타운', '란디드노']), 3: ('웨일스 3부 리전 리그', ['TNS B', '코나스 B', '카디프 시티 B', '스완지 시티 B', '렉섬 B', '뱅거 시티', '릴 FC', '뉴포트 B'])},
    '폴란드': {1: ('엑스트라클라사', ['레기아 바르샤바', '레흐 포즈난', '라쿠프 쳉스토호바', '포곤 슈체친', '실롱스크 브로츠와프', '야기엘로니아 비아위스토크', '피아스트 글리비체', '자그뛔비에 루빈']), 2: ('I 리가', ['비스와 크라쿠프', '레치아 그단스크', '미에치 레그니차', '비제프 우치', '루흐 호주프', '바르타 포즈난', '아르카 그디니아', '브루크베트 테르말리카']), 3: ('II 리가', ['레기아 B', '레흐 B', '폴로니아 바르샤바', '스탈 스탈로바', '호이니찬카', '올림피아 엘블롱크', '자그뛔비에 소스노비에츠', 'GKS 카토비체'])},
    '헝가리': {1: ('넴제티 버이녹샤그 I', ['페렌츠바로시', '케치케메트', '데브레첸', '푸스카스 아카데미아', '비디(페헤르바르)', 'MTK 부다페스트', '우이페스트', '퍽시 FC']), 2: ('넴제티 버이녹샤그 II', ['부다페스트 혼베드', '바사스 FC', '디오스죄르', '죄르 ETO', '젬프렌', '체글레드', '쇼프론', '페치 AFC']), 3: ('넴제티 버이녹샤그 III', ['페렌츠바로시 B', '푸스카스 B', '부다페스트 B', '데브레첸 B', '세게드', '카포스바르', '베스프렘', '살고터랸'])},
    '세르비아': {1: ('수페르리가', ['츠르베나 즈베즈다', '파르티잔', 'TSC 바치카 토폴라', '추카리치키', '보이보디나', '노비 파자르', '라드니치키 1923', '보주도바츠']), 2: ('프르바 리가', ['야보르 이바니차', 'IMT 노비 베오그라드', '젤레즈니차르', '믈라도스트 GAT', '라드니치키 니시', '콜루바라', '나프레다크', '마치바']), 3: ('스릅스카 리가', ['즈베즈다 B', '파르티잔 B', '라드 베오그라드', '제문', '보라츠 차차크', '오프카 베오그라드', '스메데레보', '야고디나'])},
    '스코틀랜드': {1: ('스코티시 프리미어십', ['셀틱', '레인저스', '하트 오브 미들로시언', '하이버니언', '애버딘', '세인트 미렌', '킬마녹', '마더웰']), 2: ('스코티시 챔피언십', ['던디 유나이티드', '로스 카운티', '파틱 시슬', '레이스 로버스', '인버네스', '퀸스 파크', '그린녹 모턴', '던펌린']), 3: ('스코티시 리그 원', ['셀틱 B', '레인저스 B', '폴커크', '해밀턴 아카데미칼', '알로아 애슬레틱', '코브 레인저스', '퀸 오브 더 사우스', '켈티 하츠'])},
    '체코': {1: ('체코 퍼스트 리그', ['스파르타 프라하', '슬라비아 프라하', '빅토리아 플젠', '슬로바츠코', '시그마 올로모우츠', '오스트라바', '믈라다 볼레슬라프', '슬로반 리베레츠']), 2: ('체코 내셔널 풋볼 리그', ['즈브로요프카 브르노', '파르두비체', '카르비나', '테플리체', '바닉 오스트라바', '야블로네츠', '듀클라 프라하', '비소치나 이흘라바']), 3: ('ČFL/MSFL', ['스파르타 B', '슬라비아 B', '플젠 B', '보헤미안스 B', '프리드란트', '할루치', '블란스코', '즐린 B'])},
    '칠레': {1: ('프리메라 디비시온', ['콜로-콜로', '우니베르시다드 데 칠레', '우니베르시다드 카톨리카', '우아치파토', '팔레스티노', '에베르톤', '코브레살', '코킴보 우니도']), 2: ('프리메라 B', ['코브레로아', '산티아고 원더러스', '라 세레나', '데포르테스 안토파가스타', '이키케', '테무코', '산루이스', '레인저스']), 3: ('세군다 디비시온 아마추어', ['콜로콜로 B', 'U 칠레 B', 'U 카톨리카 B', '멜리피야', '산 안토니오 유나이티드', '페르난데스 비알', '리마체', '트란스안디노'])},
    '페루': {1: ('리가 1', ['우니베르시타리오', '알리안사 리마', '스포르팅 크리스탈', '멜가르', '시엔시아노', '쿠스코 FC', '세사르 바예호', 'FBC 멜가르']), 2: ('리가 2', ['데포르티보 무니시팔', '보이스', '후안 아우리치', '우니온 코메르시오', '카하마르카', '카를로스 만누치', '아틀레티코 그라우', '아야쿠초']), 3: ('코파 페루', ['우니베르시타리오 B', '알리안사 B', '크리스탈 B', '알리안사 우아누코', '스포르트 앙카시', '코로넬 볼로그네시', '멜가르 B', '타루마'])},
    '파라과이': {1: ('프리메라 디비시온', ['올림피아', '세로 포르테뇨', '리베르타드', '구아라니', '나시오날', '스포르티보 루케뇨', '아멜리아노', '타쿠아리']), 2: ('디비시온 인테르메디아', ['솔 데 아메리카', '헤네랄 카바예로', '과이레냐', '레시스텐시아', '산 로렌소 파라과이', '페르난도 데 라 모라', '루비오 뉴', '12 데 옥투브레']), 3: ('프리메라 디비시온 B', ['올림피아 B', '세로 포르테뇨 B', '리베르타드 B', '3 데 페브레로', '스포르티보 이테뇨', '콜레히알레스', '레꼬레따', '산 로케'])},
    '베네수엘라': {1: ('프리메라 디비시온', ['데포르티보 타치라', '카라카스 FC', '아카데미아 푸에르토 카베요', '포르투게사', '모나가스', '메트로폴리타노스', '라 가이아', '사모라']), 2: ('세군다 디비시온', ['에스투디안테스 데 메리다', '미네로스 데 과야나', '앙고스투라', '카라보보', '우니온 아틀레티코', '티타네스', '우레냐', '라라 FC']), 3: ('베네수엘라 테르세이라', ['타치라 B', '카라카스 B', '마라카이보', '발렌시아 스타즈', '푸에르토 락 크루스', '메리다 유나이티드', '바리나스', '볼리바르 FC'])},
    '볼리비아': {1: ('프리메라 디비시온', ['볼리바르', '더 스트롱기스트', '올웨이즈 레디', '호르헤 빌스테르만', '오리엔테 페트롤레로', '블루밍', '나시오날 포토시', '아우로라']), 2: ('코파 시몬 볼리바르', ['리얼 토마야포', '과비라', '인디펜디엔테 페트롤레로', '바카 디에스', '리얼 포토시', '데스트로이어스', '산 호세', '우니베르시타리오 수크레']), 3: ('볼리비아 리전 리그', ['볼리바르 B', '스트롱기스트 B', '라파스 FC', '산타크루스 유나이티드', '코차밤바 스타즈', '포토시 유나이티드', '베니 FC', '판도 FC'])},
    '캐나다': {1: ('캐나디안 프리미어리그', ['포지 FC', '카발리 FC', '퍼시픽 FC', '할리팩스 완더러스', '아틀레티코 오타와', '발루어 FC', '반쿠버 FC', '요크 유나이티드']), 2: ('리그1 캐나다', ['본 아줄', '시그마 FC', '블루 데빌스', '반쿠버 화이트캡스 리저브', 'TFC 아카데미', '시마로네스 캐나다', '셀틱 QC', '로버스 FC']), 3: ('캐나다 내셔널 아마추어 리그', ['포지 B', '카발리 B', '토론토 시티', '몬트리올 유나이티드', '캘거리 스타즈', '에드먼턴 유나이티드', '위니펙 시티', '빅토리아 킹스'])},
    '파나마': {1: ('리아 파나메냐 데 풋볼', ['CAI 파나마', '타우로 FC', 'CD 플라사 아마도르', '스포르팅 산 미겔리토', '클럽 데포르티보 우니베르시타리오', '산 프란시스코', '알리안사 파나마', '아라베 우니도']), 2: ('리가 나시오날 데 아센소', ['베라구아스 유나이티드', '에레라 FC', '아틀레티코 치리키', '코스타 델 에스테', '파나마 시티 FC', '콜론 C-3', '선트라크스', '리오 아바호']), 3: ('파나마 디스트릭트 리그', ['타우로 B', '플라사 아마도르 B', 'CAI B', '다비드 FC', '산티아고 스타즈', '치트레 FC', '라 초레라', '토쿠멘 FC'])},
    '자메이카': {1: ('자메이카 프리미어리그', ['하버 뷰', '마운트 플레전트', '카발리어 FC', '아넷 가든스', '워터하우스 FC', '티볼리 가든스', '험블 라이온', '포트모어 유나이티드']), 2: ('KSAFA 슈퍼리그', ['보이즈 타운', '리얼 모이네스', '던베홀든', '몰린스 유나이티드', '베어 패닉스', '몬테고 베이 유나이티드', '채플턴 마룬스', '포트 안토니오']), 3: ('자메이카 디스트릭트 리그', ['하버 뷰 B', '마운트 B', '킹스턴 시티', '세인트 안드레', '네그릴 FC', '오초 리오스', '스페니시 타운', '사바나'])},
    '아랍에미리트': {1: ('UAE 프로리그', ['알 아인', '샤바브 알 아흘리', '샤르자 FC', '알 알리', '알 와슬', '알 자지라', '알 나스르 UAE', '알 와흐다']), 2: ('UAE 1부 리그', ['디바 알 푸자이라', '하타 클럽', '알 오루바', '알 다프라', '푸자이라 FC', '에미레이트 클럽', '알 마스루프', '알 가리아']), 3: ('UAE 2부 리그', ['알 아인 B', '알 아흘리 B', '아부다비 시티', '두바이 유나이티드', '라스 알 카이마', '아즈만 B', '샤르자 B', '펄 두바이'])},
    '오만': {1: ('오만 프로리그', ['알 세에브', '도파르 클럽', '알 나흐다', '알 수와이크', '오만 클럽', '알 무스카트', '소하르 클럽', '수르 클럽']), 2: ('오만 1부 리그', ['알 랍사', '이브리 클럽', '알 샤바브 오만', '알 무디비', '바흘라 클럽', '니즈와 클럽', '살랄라 클럽', '미르바트']), 3: ('오만 2부 리그', ['알 세에브 B', '도파르 B', '무스카트 시티', '소하르 B', '마트라', '카사브', '부르카', '잘란'])},
    '태국': {1: ('타이 리그 1', ['부리람 유나이티드', '방콕 유나이티드', '포트 FC', 'BG 빠툼 유나이티드', '무앙통 유나이티드', '치앙라이 유나이티드', '촌부리 FC', '랏차부리 FC']), 2: ('타이 리그 2', ['농부아 피치야', '수판부리', '나콘랏차시마', '치앙마이 유나이티드', '라용 FC', '패 프레 유나이티드', '사뭇 프라칸', '아야타야 유나이티드']), 3: ('타이 리그 3', ['부리람 B', '방콕 시티', '치앙마이 FC', '송클라 FC', '파타야 유나이티드', '콘캔 유나이티드 B', '치앙라이 시티', '사뭇 사콘'])},
    '베트남': {1: ('V리그 1', ['남딘 FC', '하노이 FC', '공안 하노이', '비엣텔 FC', '하이퐁 FC', '베카멕스 빈즈엉', '호앙아인 잘라이', 'FC 호치민 시티']), 2: ('V리그 2', ['다낭 FC', 'PVF-CAND', '빈프억', '바리아 붕따우', '롱안 FC', '꽝남 FC', '후에 FC', '칸호아 FC']), 3: ('베트남 2부 리그', ['하노이 B', '호치민 B', '하이퐁 B', '동탑 FC', '람동 FC', '지아 딘', '빈 투안', '안장 FC'])},
    '코트디부아르': {1: ('코트디부아르 리그 1', ['ASEC 미모사', '산 페드로', 'AFAD 제카누', 'SC 가뇨아', '레이싱 클럽 아비장', '스텔라 클럽', '부아케 FC', '소아']), 2: ('코트디부아르 리그 2', ['스타드 다비장', '코로고', '리스 사산드라', '징구', '아상도 하이바', '아그보빌', '우메 FC', '인더스트리얼']), 3: ('코트디부아르 디비시온 3', ['ASEC B', '산 페드로 B', '아비장 시티', '부아케 유나이티드', '야무수크로 FC', '달로아 FC', '만 FC', '코르고 B'])},
    '말리': {1: ('말리 프리미어 디비시온', ['스타드 말리앵', '조리바 AC', '레알 바마코', '온즈 크레아퇴르', '블랙 스타즈', 'US페 부구니', '바카리잔', '예일렌 올림픽']), 2: ('말리 디비시온 1', ['CS 두구올로피라', 'AS 폴리스 바마코', '코로피나', '키타 FC', '라피드 카티', '시카소 FC', '세구 스타즈', '카이 FC']), 3: ('말리 리전 리그', ['스타드 B', '조리바 B', '바마코 시티', '팀북투 FC', '가오 스타즈', '모프티 FC', '쿨리코로', '키달 FC'])},
    '남아프리카공화국': {1: ('DSTV 프리미어십', ['마멜로디 선다운스', '올랜도 파이러츠', '카이저 치프스', '수퍼스포르트 유나이티드', '케이프타운 시티', '스텔렌보스 FC', '골든 애로우즈', '아마줄루 FC']), 2: ('모츠페 챔피언십', ['마루모 갈란츠', '세쿠쿠네 유나이티드', '리차즈 베이', '폴로콰네 시티', '바로카 FC', '치파 유나이티드', 'TS 갤럭시', '블랙 레오파즈']), 3: ('ABC 모츠페 리그', ['선다운스 B', '파이러츠 B', '치프스 B', '이스트 런던 시티', '더반 FC', '프리토리아 스타즈', '블룸폰테인 셀틱 B', '킴벌리'])},
    '뉴질랜드': {1: ('뉴질랜드 내셔널 리그', ['오클랜드 시티', '웰링턴 올림픽', '이스턴 서버브스', '크라이스트처치 유나이티드', '네이피어 시티', '해밀턴 원더러스', '오클랜드 유나이티드', '서스 AFC']), 2: ('노더스 / 센트럴 / 사우더스 리전 리그', ['노스 셔', '비라만', '웰링턴 피닉스 리저브', '미라마르 레인저스', '웨스턴 서버브스', '캐시미어 테크니컬', '그린 아일랜드', '시드넘']), 3: ('뉴질랜드 로컬 디비시온', ['오클랜드 B', '올림픽 B', '더네딘 시티', '넬슨 서브', '타우랑가', '인버카길', '파머스턴', '로토루아'])},
    '노르웨이': {1: ('엘리테세리엔', ['보되/글림트', '몰데 FK', '브란', '바이킹', '로센보르그', '릴레스트룀', '트롬쇠', '프레드릭스타드']), 2: ('OBOS-리에엔', ['볼레렝아', '오드', '올레순', '스타베크', '스타르트', '산네스 울프', '콩스빙에르', '소그달']), 3: ('포스트노르트-리에엔', ['글림트 B', '몰데 B', '린 오슬로', '스케이드', '회네포스', '아스케르', '브린', '플라로'])},
    '그리스': {1: ('수페르리가 엘라다', ['올림피아코스', 'PAOK', 'AEK 아테네', '파나시나이코스', '아리스', '아스테라스 트리폴리스', 'OFI 크레타', '아트로미토스']), 2: ('수페르리가 2', ['키피시아', 'PAS 야니나', '볼로스', '파네톨리코스', '라미아', '이오니코스', '레바디아코스', '라리사']), 3: ('감마 에스니키', ['올림피아코스 B', 'PAOK B', '파나시나이코스 B', '파나차이키', '에갈레오', '칼라마타', '아폴론 스미르니스', '카발라'])},
    '루마니아': {1: ('리가 1', ['FCSB', 'CFR 클루지', '우니베르시타테아 크라이오바', '라피드 부쿠레슈티', '파룰 콘스탄차', '셉시 OSK', '헤르만슈타트', '페트롤룰']), 2: ('리가 2', ['디나모 부쿠레슈티', 'FCU 크라이오바', '볼룬타리', 'FC 보토샤니', '미오베니', '키아지나', '슬로보지아', '체아을러울']), 3: ('리가 3', ['FCSB B', '클루지 B', '브라쇼브', '폴리테흐니카 티미쇼아라', '오라데아', '레시차', '바커우', '클린체니'])},
    '슬로바키아': {1: ('니케 리가', ['슬로반 브라티슬라바', '스파르타크 트르나바', '질리나', 'DAC 두나이스카 스트레다', '포드브레조바', '루조베록', '트렌친', '반스카 비스트리차']), 2: ('2. 리가', ['비온 즐라테 모라브체', '미할로브체', '타트란 프레쇼브', '코시체', '포흐로니에', '페트르잘카', '푸호브', '휴메네']), 3: ('3. 리가', ['슬로반 B', '질리나 B', '니트라', '인테르 브라티슬라바', '센첼', '바르데요브', '코마르노', '스피스카'])},
    '아일랜드': {1: ('LOI 프리미어 디비시온', ['샴록 로버스', '데리 시티', '세인트 패트릭스 애슬레틱', '보헤미안스', '던도크', '셸번', '드로에다 유나이티드', '슬라이고 로버스']), 2: ('LOI 퍼스트 디비시온', ['골웨이 유나이티드', '워터포드', '코브 램블러스', '웩스퍼드 FC', '브레이 원더러스', '롱퍼드 타운', '핀 하프스', '트리티 유나이티드']), 3: ('렌스터 / 먼스터 시니어 리그', ['샴록 B', '데리 B', '크럼린 유나이티드', '블루벨 유나이티드', '아본데일 유나이티드', '코크 시티 B', '더블린 유니버시티', '세인트 케빈스'])},
    '핀란드': {1: ('베이카우스리가', ['HJK 헬싱키', 'KuPS 쿠오피오', 'SJK 세이내요키', '일베스 탐페레', 'VPS 바사', 'FC 홍카', 'FC 인테르 투르쿠', 'AC 오울루']), 2: ('위쾨스리가', ['IFK 마리에함', 'FC 라티', '하카', 'KTP 코트카', 'EIF 에케내스', 'TPS 투르쿠', '야로', 'MP 미켈리']), 3: ('위쾨넨', ['HJK B', 'KuPS B', '일베스 B', 'SJK B', '그니스탄', 'JaPS', '살파', 'PK-35'])},
    '조지아': {1: ('에로브눌리 리가', ['디나모 트빌리시', '디나모 바투미', '사부르탈로 트빌리시', '디라 고리', '토르페도 쿠타이시', '삼구랄리', '가그라', '텔라비']), 2: ('에로브눌리 리가 2', ['슈쿠라 콥레티', '삼트레디아', '시오니 볼니시', '스파에리', '위트 조지아', '메라니 마르트빌리', '가레지 사가레조', '로코모티프 트빌리시']), 3: ('리가 3', ['디나모 트빌리시 B', '바투미 B', '메라니 트빌리시', '치쿠라 사치케레', '루스타비', '골리', '조지아 FC', '보르조미'])},
    '알바니아': {1: ('카테고리아 수페리오레', ['파르티자니 티라나', 'KF 티라나', '에그나티아', '블라즈니아 슈코더르', '디나모 시티', '스켄데르베우', '테우타 두러스', '라치']), 2: ('카테고리아 에 파르', ['에르제니', '쿠커시', '바이리스', '플라무르타리', '아폴로니아', '코라비', '토모리', '루쉬냐']), 3: ('카테고리아 에 디두', ['티라나 B', '파르티자니 B', '베사 카바여', '부트린티', '포그라데치', '엘바사니', '루프테타리', '테펠레나'])},
    '북마케도니아': {1: ('마케도니아 프르바 리그', ['스트루가 트림-럼', '스켄디야', '실렉스', '슈쿠피', '라보트니치키', '바르다르 스코페', '티크베시', '브레갈니차']), 2: ('마케도니아 브토라 리그', ['마케도니야 GP', '아카데미야 판데프', '벨라시차', '펠리스테르', '포베다', '고스티바르', '보스카 스포르트', '스코페 FC']), 3: ('마케도니아 트레타 리그', ['바르다르 B', '스켄디야 B', '티베리야', '코주프', '사사', '오흐리드', '테텍스', '쿠마노보'])},
    '아이슬란드': {1: ('베스타 데일드 나이아', ['비킹구르 레이캬비크', '발루르', '브레이다블리크', '스탸르난', 'FH 하프나르피외르두', 'KR 레이캬비크', 'KA 아쿠레이리', 'HK 코파보구르']), 2: ('렝주 데일딘', ['프람 레이캬비크', '필키르', 'IBV 베스트만나에이얄', '케플라비크', '그린다비크', '토르 아쿠레이리', '피외르니르', '레인니르']), 3: ('2. 데일드 카를라', ['비킹구르 B', '발루르 B', '하우카르', '볼룽가르비크', '흐로타르', '셀포스', '보가르', '달비크'])},
    '몬테네그로': {1: ('몬테네그로 프르바 리그', ['부두치노스트 포드고리차', '수체스카 닉시치', '데치치', '아르세날 티밧', '예디닌스트보', '페트로바츠', '모르나르 바르', '믈라도스트 DG']), 2: ('몬테네그로 브토라 리그', ['루다르 플레블랴', '이스크라 다닐로브그라드', '제타 고', '코모 포드고리차', '보켈리', '오트란트', '이가로', '베라네']), 3: ('몬테네그로 트레차 리그', ['부두치노스트 B', '수체스카 B', '체티네', '첼릭 닉시치', '자블랴크', '구신예', '바르 B', '티밧 B'])},
    '북아일랜드': {1: ('NIFL 프리미어십', ['린필드', '클리프턴빌', '란 FC', '크루세이더스', '글렌토런', '콜레인', '밸리메나 유나이티드', '글레나본']), 2: ('NIFL 챔피언십', ['포타다운', '뉴리 시티', '던개넌 스위프츠', '발리클레어', '뱅거 FC', '인스티튜트 FC', 'HW 웰더스', '던엘라']), 3: ('NIFL 프리미어 중간 리그', ['린필드 B', '글렌토런 B', '리마바디 유나이티드', '모욜라 파크', '토버모어', '리스번 디스틸러리', '퀸즈 유니버시티', '아마 시티'])},
    '이스라엘': {1: ('이스라엘 프리미어리그', ['마카비 하이파', '마카비 텔아비브', '하포엘 베르셰바', '하포엘 텔아비브', '마카비 네타냐', '하포엘 예루살렘', '베이타르 예루살렘', '마카비 페타티크바']), 2: ('리가 레우미트', ['하포엘 하이파', '마카비 브네이 라이나', '하포엘 하데라', 'MS 아슈도드', '사크닌', '하포엘 람마트간', '마카비 헤르츨리야', '하포엘 움 알-파헴']), 3: ('리가 알레프', ['마카비 하이파 B', '마카비 텔아비브 B', '하포엘 크파르 사바', '리가 이로니', '하포엘 아코', '마카비 자우라', '디모나', '티라'])},
    '불가리아': {1: ('파르바 리그', ['루도고레츠 라즈그라드', 'CSKA 소피아', '레프스키 소피아', '체르노 모레', '로코모티프 플로브디프', 'CSKA 1948', '보테프 플로브디프', '베로에']), 2: ('브토라 리그', ['피린 블라고에브그라드', '보테프 브라차', '로코모티프 소피아', '헤바르', '에타르', '스파르타크 플로브디프', '도브루드자', '몬타나']), 3: ('트레타 리그', ['루도고레츠 B', 'CSKA B', '레프스키 B', '스파르타크 가르나', '미뇨르 페르니크', '슬리벤', '하스코보', '두나브'])},
    '룩셈부르크': {1: ('룩셈부르크 내셔널 디비시온', ['F91 뒤들랑주', '스위프트 에스페랑주', '프로그레스 니더코른', '디페르당주 03', '죄네스 에슈', '레이싱 유니온', 'UNA 슈트라센', '몬도르프']), 2: ('룩셈부르크 에렌프롬오시온', ['폴라 에슈', '에첼라 에텔브루크', '유니온 티투스 페탕주', '마리스카 메르시', '빌츠 91', '빅토리아 로스포르트', '쉬플랑주', '케르옝']), 3: ('룩셈부르크 1. 디비시온', ['뒤들랑주 B', '스위프트 B', '그레벤마허', '로당주 91', '에슈 FC', '베거', '매메르', '루멜랑주'])},
    '아르메니아': {1: ('아르메니아 프리미어리그', ['퓨니크 예레반', '우라르투', '알라슈케르트', '아라라트-아르메니아', '아라라트 예레반', '노아 FC', '시라크', '반 FC']), 2: ('아르메니아 퍼스트 리그', ['BKMA 예레반', '웨스트 아르메니아', '퓨니크 II', '우라르투 II', '알라슈케르트 II', '아라라트 II', '간자사르', '시카 에레반']), 3: ('아르메니아 리전 리그', ['노아 II', '반 II', '예레반 시티', '규므리 FC', '바나조르', '로리 FC', '아르마비르', '세반 FC'])},
    '벨라루스': {1: ('벨라루스 프리미어리그', ['디나모 민스크', '네만 그로드노', '토르페도 조디노', 'BATE 보리소프', '샤흐타르 솔리그로스크', '디나모 브레스트', '이슬로치', '슬루츠크']), 2: ('벨라루스 퍼스트 리그', ['고멜', '민스크 FC', '슬라비아 모지르', '에네르게티크-BGU', '벨시나 보브루이스크', '아르세날 드제르진스크', '비텝스크', '로코모티프 고멜']), 3: ('벨라루스 세컨드 리그', ['디나모 민스크 B', 'BATE B', '샤흐타르 B', '리다', '오르샤', '핀스크', '바라노비치', '몰로데치노'])},
    '몰도바': {1: ('몰도바 수페르리가', ['셰리프 티라스폴', '페트로클루브 힌체슈티', '밀사미 오르헤이', '지브루 키시너우', '발차니', '스핀투 게오르기', '다시아 부이우카니', '스파르타 셀레메트']), 2: ('몰도바 리가 1', ['디나모-아우토', '스페란차', '플로레슈티', '빅토리아 키시너우', '셰리프 II', '수클리아', '레알 수치아', '팔레스티 몰도바']), 3: ('몰도바 리가 2', ['밀사미 B', '지브루 B', '티라스폴 시티', '카훌', '웅게니', '싱게레이', '에디네츠', '콤라트'])},
    '리투아니아': {1: ('A 리가', ['잘기리스 빌뉴스', 'FK 파네베지스', '헤겔만', '카우노 잘기리스', '수두바', '시아울리아이', '방가 가르주다이', '디나바 알리투스']), 2: ('I 리가', ['리테리아이', '네베지스', '요나바', '미니야', '네프투나스 클라이페다', '바브룽가스', '잘기리스 B', '샤울리아이 B']), 3: ('II 리가', ['파네베지스 B', '수두바 B', '우테니스', '스베이카타', '가르주다이 B', '타우라스', '실루테', '빌뉴스 시티'])},
    '라트비아': {1: ('라트비아 비르스리가', ['RFS', '리가 FC', '발미에라 FC', '아우다', '리에파야', '옐가바', '투쿠므스 2000', '메타']), 2: ('라트비아 1.리가', ['슈페르 노바', '삼구랄리 라트비아', '그로비냐', 'Skanste', '리가 FC II', 'RFS II', '레제크네', '디나모 리가']), 3: ('라트비아 2.리가', ['발미에라 II', '아우다 II', '살라스필스', '카로스타', '케카바', '오그레', '림바지', '스밀테네'])},
    '에스토니아': {1: ('메이스트릴리가', ['플로라 탈린', '레바디아 탈린', '넘메 칼류', '파이데 린나메스콘드', '칼레브 탈린', '쿠레사레', '타메카 타르투', '바프루스 패르누']), 2: ('에시리가', ['하리우', '레바디아 II', '플로라 II', '비임시', '엘바', '레바디아 U21', '탈린나 칼레브 II', '남메 유나이티드']), 3: ('에시리가 B', ['파이데 II', '타메카 II', '패르누 JK', '타르투 웰코', '라네마', '레바디아 U19', '칼류 B', '실라마에'])},
    '안도라': {1: ('프리메라 디비시온', ['아틀레틱 클럽 데스칼데스', '인터 클럽 데스칼데스', 'FC 산타콜로마', 'UE 산타콜로마', '페냐 엥카르나다', '오르디노', '파스 데 라 카사', '카릴']), 2: ('세군다 디비시온', ['산트 줄리아', '엔캄', '렝게르스 안도라', 'FC 안도라 B', '산타콜로마 B', '인터 B', '아틀레틱 B', '라 마사나']), 3: ('안도라 아마추어 리그', ['UE 산타콜로마 B', '오르디노 B', '카릴 B', '레스 보네스', '카니요', '엔캄 B', '레 에스칼데스', '도 이요'])},
    '산마리노': {1: ('캄페오나투 산마리네세', ['트레펜네', '코스모스', '라 피오리타', '비르투스', '리베르타스', '트레 피오리', '유베네스-도가나', '무리타']), 2: ('산마리노 A2 티어', ['피오렌티노', '산 조반니', '도마냐노', '페냐로사', '카일룽고', '폴고레', '파에타노', '산마리노 아카데미']), 3: ('산마리노 리저브 디비시온', ['트레펜네 B', '코스모스 B', '라 피오리타 B', '비르투스 B', '리베르타스 B', '트레 피오리 B', '도마냐노 B', '폴고레 B'])},
    '요르단': {1: ('요르단 프로리그', ['알 웨흐다트', '알 파이살리 암만', '알 후세인 이르비드', '알 가이시 요르단', '알 라므사', '샤바브 알 오르돈', '아카바 클럽', '알 마즈']), 2: ('요르단 1부 리그', ['알 자지라 암만', '알 사리흐', '알 아흘리 암만', '알 얄릴', '사합 클럽', '알 바카', '알 이티하드 요르단', '알 하워스']), 3: ('요르단 2부 리그', ['알 웨흐다트 B', '알 파이살리 B', '이르비드 시티', '자르카 FC', '암만 스타즈', '마프라끄', '아즐룬', '알 무로스'])},
    '바레인': {1: ('바레인 프리미어리그', ['알 킬디야', '알 무하라크', '알 리파', '알 아흘리 마나마', '마나마 클럽', '알 하이드', '알 샤바브 마나마', '싯트라 클럽']), 2: ('바레인 2부 리그', ['이스트 리파', '알 할라', '부사이틴', '바레인 클럽', '이싸 시티', '알 타다문 바레인', '말키야', '칼랄리']), 3: ('바레인 리저브 리그', ['무하라크 B', '리파 B', '킬디야 B', '마나마 시티', '무하라크 스타즈', '알 아흘리 B', '부사이틴 B', '하이드 B'])},
    '인도네시아': {1: ('리가 1', ['보르네오 FC', '페르시브 반둥', '발리 유나이티드', '페르시자 자카르타', 'PSM 마카사르', '페르세바야 수라바야', '스망 파당', 'PSIS 스마랑']), 2: ('리가 2', ['바크토 푸트라', '페르시크 케디리', '란스 누산타라', '아레마 FC', 'PSSI 슬레만', '페르시푸라 자야푸라', '델트라스', '그레시크 유나이티드']), 3: ('리가 3', ['반둥 유나이티드', '자카르타 시티', '수라바야 FC', '메단 스타즈', '마카사르 B', '발리 스타즈', '치레본 FC', '솔로 FC'])},
    '말레이시아': {1: ('말레이시아 슈퍼리그', ['조호르 다룰 탁짐', '셀랑오르 FC', '트렝가누 FC', '사바 FC', '쿠알라룸푸르 시티', '케다 다룰 아만', '페락 FC', '스리 파항']), 2: ('말레이시아 M3 리그', ['쿠칭 시티', '느그리 슴빌란', 'PDRM FC', '페낭 FC', '켈란탄 다룰 나임', '이미그레센 FC', '하리마우 FC', '사임 다비']), 3: ('말레이시아 M4 리그', ['JDT II', '셀랑오르 II', '쿠알라룸푸르 B', '말라카 시티', '이포 FC', '페낭 B', '쿠칭 유나이티드', '조호르 스타즈'])},
    '싱가포르': {1: ('싱가포르 프리미어리그', ['라이언 시티 세일러스', '알비렉스 니가타 싱가포르', '탐파인스 로버스', '게일랑 인터내셔널', '발레스티어 칼사', '후강 유나이티드', '탄종 파가', '영 라이온즈']), 2: ('싱가포르 풋볼 리그 1', ['워리어스 FC', '홈 유나이티드 B', '싱가포르 크리켓 클럽', '티옹 바루', '유슌 센트럴', '사우스우드', '프로젝트 5-O', '스포르팅 싱가포르']), 3: ('SFL 디비시온 2', ['라이언 시티 B', '탐파인스 B', '게일랑 B', '후강 B', '우드랜드 웰링턴', '주롱 FC', '셈바왕 레인저스', '클레멘티'])},
    '몽골': {1: ('몽골 내셔널 프리미어리그', ['FC 울란바토르', '데런 FC', '쿠룹 카간즈', 'SP 팔콘스', '울란바토르 시티', '호롬혼 FC', '바바리아라즈', '에르침']), 2: ('몽골 퍼스트 리그', ['BCH 라이온스', '투브 부가누드', '울란바토르 레전즈', '에르침 II', '셀렝게 프레스', '렝게르 몽골', '다르한 FC', '에르덴트']), 3: ('몽골 세컨드 리그', ['울란바토르 B', '데런 B', '고비 스타즈', '초이발산', '무룬 FC', '하라호름', '날라이흐', '바가누르'])},
    '홍콩': {1: ('홍콩 프리미어리그', ['키치 SC', '리만 FC', '동방 축구단', '홍콩 레인저스', '남구 축구단', '대포', '홍콩 FC', '자원']), 2: ('홍콩 갑조리그', ['남화 체육회', '유안랑', '윙이', '샤틴', '시티즌 FC', '골피토 홍콩', '해양', '키치 B']), 3: ('홍콩 을조리그', ['동방 B', '레인저스 B', '구룡성', '콰이청', '츈완', '툰문', '완차이', '북구 FC'])},
    '조선민주주의인민공화국': {1: ('최고급축구련맹전', ['4.25체육단', '압록강체육단', '평양체육단', '기관차체육단', '려명체육단', '소백수체육단', '선봉체육단', '월미도체육단']), 2: ('1부류축구련맹전', ['체비에체육단', '자강도체육단', '함경북도체육단', '황해남도체육단', '평안북도체육단', '양강도체육단', '4.25 B', '평양 B']), 3: ('2부류축구련맹전', ['압록강 B', '기관차 B', '원산체육단', '청진체육단', '함흥체육단', '신의주체육단', '남포체육단', '강계체육단'])},
    '피지': {1: ('디지셀 프리미어리그', ['라우토카 FC', '레바 FC', '수바 FC', '바 FC', '나디 FC', '라바사 FC', '나드로가', '타이레부 나이타시리']), 2: ('피지 시니어 리그', ['나부아', '나누쿠', '사부사부', '타베우니', '락락', '시가토카', '바누아 레부', '수바 리저브']), 3: ('피지 로컬 디스트릭트 리그', ['라우토카 B', '레바 B', '수바 B', '바 B', '코로보보', '타부아', '나부아 B', '로토카 스타즈'])},
    '슬로베니아': {1: ('프르바리가', ['NK 마리보르', '올림피야 류블랴나', '셀레', '코페르', '돔잘레', '무라', '브라보', '라도믈리에']), 2: ('2. SNL', ['고리차', '나프타 렌다바', '트리글라브 크란', '알루미니이', '타보르 세자나', '벨레녜', '비스트리차', '크르카']), 3: ('3. SNL', ['마리보르 B', '올림피야 B', '이졸라', '드라바 프투이', '톨민', '브레지체', '센추르', '크란 B'])},
    '카자흐스탄': {1: ('카자흐스탄 프리미어리그', ['오르다바시', '아스타나 FC', '악토베', '카이라트 알마티', '토볼', '키질자르', '아티라우', '샤흐타르 카라간디']), 2: ('카자흐스탄 퍼스트 리그', ['엘리스', '제티수', '옥제트페스', '카스피이', '투란', '악수', '카이라트 자스', '아스타나 II']), 3: ('카자흐스탄 세컨드 리그', ['오르다바시 B', '악토베 B', '알마티 스타즈', '심케트 FC', '파블로다르', '타라즈 B', '세메이 FC', '코크셰타우'])},
    '부르키나파소': {1: ('부르키나파소 프리미어리그', ['ASFA 옌넹가', 'RC 보보 디울라소', '에투알 EFO', '도안 와가두구', '살리타스 FC', '마제스티 FC', 'AS 소나벨', 'ASEC 쿠두구']), 2: ('부르키나파소 2부 리그', ['라요', '보보 스타즈', '와가두구 시티', '테네쿠루', '카야 FC', '방포라', '데두구', '도안 B']), 3: ('부르키나파소 리전 디비시온', ['ASFA B', 'RC 보보 B', '와가두구 레전즈', '쿠두구 B', '포고 FC', '도리', '파다 은구르마', '가우아'])},
    '케냐': {1: ('케냐 프리미어리그', ['고어 마히아', '투스커 FC', 'AFC 레오파드', '샤카라마 슛츠', '케냐 경찰청', '반다리 FC', '우지 FC', '카카메가 홈보이즈']), 2: ('케냐 슈퍼리그', ['소파카', '나이로비 시티 스타즈', '와지토', '비히가 불릿츠', '키수무 알루포', '모바사 스타즈', '나쿠루 FC', '엘도레트']), 3: ('케냐 디비시온 원', ['고어 마히아 B', '투스커 B', '레오파드 B', '키수무 시티', '티카 FC', '니에리 FC', '말린디', '로드와'])},
    '앙골라': {1: ('지라볼라', ['페트루 아틀레티쿠', '프리메이루 드 아고스투', '사그라다 에스페란사', '인테르클루브', '와하 도 우이헤', '브라보스 도 마키스', '데포르티보 루안다', '데포르티보 후일라']), 2: ('지라볼라 2부', ['아카데미카 도 로비토', '레크레아티보 도 리볼로', '스포르팅 드 카빈다', '프롱크 스포르트', '루안다 시티', '벵겔라 FC', '우암보', '말란헤']), 3: ('앙골라 프로빈셜 리그', ['페트루 B', '아고스투 B', '카빈다 스타즈', '로비토 B', '나미베 FC', '루방고', '소요 FC', '숨베'])},
    '잠비아': {1: ('잠비아 슈퍼리그', ['ZESCO 유나이티드', '파워 다이너모스', '엔카나 FC', '레드 애로우즈', '그린 버팔로스', '카브웨 워리어스', '자나코 FC', '무풀리라 원더러스']), 2: ('잠비아 내셔널 퍼스트 리그', ['포레스트 레인저스', '냅사 스타즈', '루무완스', '치파타 유나이티드', '은창가 레인저스', '리빙스턴 파이러츠', '키트웨 유나이티드', '콘콜라 블레이즈']), 3: ('잠비아 디비시온 원', ['ZESCO B', '파워 B', '엔카나 B', '루사카 시티', '은돌라 FC', '리빙스턴 시티', '칭골라', '카라마'])},
    '러시아': {'1부': ('러시아 프리미어리그', ['제니트 상트페테르부르크', '스파르타크 모스크바', 'CSKA 모스크바', '로코모티프 모스크바', '크라스노다르', '디나모 모스크바', '로스토프', '루빈 카잔']), '2부': ('러시아 퍼스트 리그', ['알라니아 블라디카프카스', '발티카 칼리닌그라드', '토르페도 모스크바', '힘키', '우랄 예카테린부르크', '소치', '시비르 노보시비르스크', '아르세날 툴라']), '3부': ('러시아 세컨드 리그', ['로토르 볼고그라드', '우파', '안지 마하치칼라', '톰 톰스크', '암카르 페름', '엔네르기야 하바롭스크', '볼가 니즈니노브고로드', '시니크 야로슬라블'])},
    '코소보': {'1부': ('코소보 수페르리가', ['발카니', '드리타', '프리슈티나', '질라니', '라피', '두카지니', '페로니켈리', '말리셰바']), '2부': ('코소보 퍼스트 리그', ['트렙차 89', '블라즈니아', '리리아', '우우카', '플라무르타리', '이스토구', '비티아', '코소바 VR']), '3부': ('코소보 세컨드 리그', ['베사 페야', '미나토리', '레페치', '샤리', '텔레코미', '더다니아', '오보릴리', '카스트리오티'])},
    '아제르바이잔': {'1부': ('아제르바이잔 프리미어리그', ['카라바흐', '네프트치 바쿠', '사바흐', '지라', '가발라', '투란 토부즈', '숨가이트', '사바일']), '2부': ('아제르바이잔 퍼스트 디비전', ['샤마키', '아라즈 나흐치반', '자가탈라', '가라다흐', '모익 바쿠', '밍게체비르', '임스리', '카파즈']), '3부': ('아제르바이잔 세컨드 디비전', ['샤흐다그 구사르', '바쿠 FC', '하자르 렌코란', '게벨레 유스', '고야잔', '에너지틱', '시르반', '밀 카라바흐'])},
    '지브롤터': {'1부': ('지브롤터 내셔널리그', ['링컨 레드 임프스', '유로파 FC', '세인트 조셉스', '브루노스 마그파이스', '몬스 칼페', '글라시스 유나이티드', '맨체스터 62', '리오스 FC']), '2부': ('지브롤터 디비전 2', ['리덕스 FC', '칼페 유나이티드', '유로파 포인트', '하운드 독스', '바스티온 FC', '록 시티', '캐슬 시티', '트레이드윈즈']), '3부': ('지브롤터 챌린지 리그', ['지브롤터 유스', '사우스 유나이티드', '노스 가드', '웨스트 가든', '이스트 포인트', '베이사이드 FC', '카탈란 FC', '가레 가드'])},
    '키프로스': {'1부': ('키프로스 1부 리그', ['APOEL', '아폴론 리마솔', '오모니아', 'AEK 라르나카', '아리스 리마솔', '파포스 FC', '아노르토시스', 'AEL 리마솔']), '2부': ('키프로스 2부 리그', ['에니시스 네온 파라림니', '도크사 카토코피아스', '네아 살라미스', '오텔로스 아티엔우', '카르미오티사', '에르미스 아라디푸', '아크리타스 클로라카스', '올림피아코스 니코시아']), '3부': ('키프로스 3부 리그', ['PAEEK', '알키 오로클리니', '아야 나파', '메이랍 유나이티드', '디게니스 아크리타스', '아실리아 FC', '에나드 폴리스', '오모니아 29M'])},
    '페로 제도': {'1부': ('페로 제도 프리미어리그', ['KI 클락스비크', 'HB 토르스하운', 'B36 토르스하운', 'VIK 이스투로이', 'SC 루나비크', 'B68 토프티르', '07 베스투르', 'IF 푸글라프요르두르']), '2부': ('페로 제도 1부 리그', ['TB 트보로위리', 'EB 스트레이무르', 'AB 아르기르', 'B71 산도이', '스칼라 IF', 'KI 클락스비크 B', 'HB 토르스하운 B', 'B36 토르스하운 B']), '3부': ('페로 제도 2부 리그', ['로이빅', 'FC 수두로이', '미바구르', '클락스비크 C', '룬 가드', '산두르 FC', '토르스하운 시티', '베스투르 유스'])},
    '몰타': {'1부': ('몰타 프리미어리그', ['햄룬 스파탄스', '하이버니언스', '발레타 FC', '플로리아나 FC', '비르키르카라', '그지라 유나이티드', '모스타 FC', '발잔 FC']), '2부': ('몰타 챌린지 리그', ['생드니 컵', '지툰 코린티안스', '나크사르 라이온즈', '슬리에마 원더러스', '타시엔 레인보우즈', '피에타 핫스퍼스', '즈베크 라이온즈', '루카 세인트 안드류스']), '3부': ('몰타 내셔널 아마추어리그', ['멜리하 FC', '음가르 유나이티드', '산환 FC', '자바르 세인트 패트릭', '시지위 FC', '구디아 유나이티드', '키르콥 유나이티드', '아타르드 FC'])},
    '리히텐슈타인': {'1부': ('리히텐슈타인 엘리트리그', ['FC 파두츠', 'FC 에셴 마우렌', 'FC 발처스', 'FC 트리센', 'FC 샨', 'FC 루겔', 'FC 트리센베르크', '파두츠 유나이티드']), '2부': ('리히텐슈타인 챌린지리그', ['에셴 마우렌 B', '발처스 B', '레지오 샨', '라인 탈 FC', '루겔 스파르타크', '알프스 FC', '오버란트 킹스', '트리센 블루스']), '3부': ('리히텐슈타인 아마추어리그', ['파두츠 C', '에셴 C', '말분 FC', '플랑켄 레인저스', '셀렌베르크', '벤더스 FC', '감프린 시티', '발처스 유스'])},
    '모나코': {'1부': ('모나코 프린시팔리테 리그', ['몬테카를로 FC', '포트 헤라클레스', '라 콘다민 스타즈', '레 레보티에', '폰트비에유 로얄', '모나코 빌 시티', '그리말디 에이스', '카지노 스퀘어']), '2부': ('모나코 챌린지 리그', ['에르퀼리스 FC', '자르댕 이그조티크', '라 루브 보이스', '팔라스 가드', '라 플라주', '생 데보트', '몬테카를로 로버스', '레보티에 유나이티드']), '3부': ('모나코 디스트릭트 리그', ['폰트비에유 B', '헤라클레스 유스', '프린스 스쿼드', '모나코 베이', '록 FC', '카지노 유나이티드', '아주르 블루스', '포트 시티'])},
    '이라크': {'1부': ('이라크 스타스 리그', ['알 쇼르타', '알 쿠와 알 자위야', '알 자우라아', '알 탈라바', '알 나자프', '에르빌 SC', '두호크 SC', '알 나프트']), '2부': ('이라크 퍼스트 디비전', ['알 미나아', '나프트 알 와사트', '나프트 마이산', '알 카르크', '알 시나아', '나프트 알 바스라', '알 카심', '알 자코']), '3부': ('이라크 세컨드 디비전', ['새마와 SC', '카르발라 SC', '알 디와니야', '알 수크', '바그다드 FC', '모술 SC', '술라이마니야', '키르쿠크 FC'])},
    '시리아': {'1부': ('시리아 프리미어리그', ['알 포투와', '알 자이시', '알 이티하드 알레포', '알 카라마', '알 와트바', '티스린 SC', '자블레 SC', '알 탈리아']), '2부': ('시리아 퍼스트 디비전', ['알 홋틴', '알 와흐다', '알 마즈드', '알 와스바', '후리야 SC', '자말레크 다마스쿠스', '알 나와이르', '아프린 SC']), '3부': ('시리아 세컨드 디비전', ['라타키아 FC', '홈스 스타즈', '하마 유나이티드', '데이르 에조르', '다라 SC', '하사카 킹스', '알 수와이드', '팔미라 FC'])},
    '팔레스타인': {'1부': ('팔레스타인 프리미어리그', ['자발 알 무카베르', '힐랄 알 쿠드스', '샤바브 알 칼릴', '샤바브 알 다히리야', '마르카즈 발라타', '타카피 툴카름', '샤바브 알 오베이디야', '아흘리 알 칼릴']), '2부': ('팔레스타인 퍼스트 디비전', ['타라지 와디 알 네스', '이슬라미 칼킬리아', '샤바브 아마리', '마르카즈 아스카르', '투바스 유나이티드', '젠인 SC', '베들레헴 시티', '예리코 유나이티드']), '3부': ('팔레스타인 세컨드 디비전', ['가자 스포츠 클럽', '하드마트 라파', '샤바브 칸 유니스', '이티하드 슈자이야', '나블루스 FC', '라말라 스타즈', '헤브론 로버스', '툴카름 유스'])},
    '타지키스탄': {'1부': ('타지키스탄 위스샤야 리가', ['이스티클롤', '라브샨 쿨롭', '에스하타', 'CSKA 파미르 두샨베', '후잔드', '레가르 타다즈', '호실롯 마르카르', '쿠크토시']), '2부': ('타지키스탄 퍼스트 리그', ['파이즈칸드', '이스타라브샨', '디나모 두샨베', '바르크치', '훌부크', '사로이카마르', '파니 유나이티드', '라브샨 하이랜드']), '3부': ('타지키스탄 세컨드 리그', ['무르고브 FC', '가르스트', '보크타르 스타즈', '판자켄트', '호로그 FC', '투르순조다', '바흐다트 로버스', '야반 킹스'])},
    '키르기스스탄': {'1부': ('키르기스스탄 프리미어리그', ['압디쉬 아타', '알라이 오시', '도르도이 비슈케크', '무라스 유나이티드', '알가 비슈케크', '네프트치 코치코르아타', '탈란트', '일비르스']), '2부': ('키르기스스탄 내셔널 리그', ['카라 발타', '리더 FC', '디나모 오시', '이시크쿨 유나이티드', '잘랄아바드 스타즈', '나린 FC', '탈라스 레인저스', '추이 프리미어']), '3부': ('키르기스스탄 세컨드 리그', ['비슈케크 로버스', '바트켄 FC', '우즈겐 유나이티드', '코치코르 시티', '키질키야', '카라콜 시티', '토크모크 FC', '알라이 유스'])},
    '레바논': {'1부': ('레바논 프리미어리그', ['알 안사르', '네즈메 SC', '알 아헤드', '샤바브 알 사헬', '알 사파', '부르즈 FC', '라싱 베이루트', '트리폴리 SC']), '2부': ('레바논 세컨드 디비전', ['샤바브 알 가지에', '살람 즈가르타', '아흘리 나바티에', '이티하드 아크마르', '티레 유나이티드', '사이다 SC', '베카 스타즈', '바알베크 FC']), '3부': ('레바논 써드 디비전', ['호멘멘 베이루트', '호멘에트멘', '샤바브 마즈달', '주니에 시티', '비블로스 FC', '슈프 레인저스', '알 안사르 유스', '네즈메 B'])},
    '쿠웨이트': {'1부': ('쿠웨이트 프리미어리그', ['알 쿠웨이트 SC', '알 카디시야', '알 아라비', '알 카즈마', '알 살미야', '알 파하힐', '알 나스르', '알 자하라']), '2부': ('쿠웨이트 디비전 1', ['알 야르무크', '알 샤바브', '알 타다몬', '카이탄 SC', '알 사헬', '수라이비카트', '알 부르간', '쿠웨이트 시티 유나이티드']), '3부': ('쿠웨이트 챌린지 리그', ['자하라 유스', '필라카 아일랜드', '나스르 킹스', '살미야 블루스', '아라비 로버스', '카디시야 스타즈', '사바 에이스', '알 와프라'])},
    '필리핀': {'1부': ('필리핀 풋볼 리그', ['유나이티드 시티', '카야 이로이로', '스탈리온 라구나', '멘디올라 1991', '마할리카 마닐라', '세부 FC', '필리핀 에어포스', '필리핀 아미']), '2부': ('필리핀 내셔널 디비전 2', ['다바오 아길라스', '그린 아처스 유나이티드', '마닐라 디거스', '로욜라 메랄코 스파크스', '파사이 시티', '바콜로드 유나이티드', '카가얀 킹스', '일로일로 스타즈']), '3부': ('필리핀 챌린지 리그', ['잠보앙가 FC', '타클로반 레인저스', '안티폴로 FC', '케존 시티 로버스', '민다나오 파이터스', '루손 일렉트릭', '비사야스 파이오니어스', '카야 유스'])},
    '투르크메니스탄': {'1부': ('투르크메니스탄 요카리 리가', ['알틴 아시르', '아할 FK', '코페트다그 아시가바트', '네비트치', '샤가담', '메르브 마리', '아르카다그 FC', '에네르게틱']), '2부': ('투르크메니스탄 비린지 리가', ['아시가바트 FC', '투란 다쇼구즈', '탈리플라리', '레바프 FC', '발칸 아바자', '마리 킹스', '세르헤다치', '바흐티야를리크']), '3부': ('투르크메니스탄 세컨드 리그', ['기질아르바트', '하 자르 로버스', '고크데페', '아르카다그 B', '알틴 유스', '아무다리야', '카라쿰 스타즈', '테젠 FC'])},
    '예멘': {'1부': ('예멘 프리미어리그', ['알 아흘리 사나', '알 와흐다 사나', '알 틸랄', '알 샤브 하드라마우트', '알 사크르', '파흐만 SC', '알 얄무크 사나', '알 힐랄 알 후다이다']), '2부': ('예멘 퍼스트 디비전', ['알 이티하드 이브', '알 샤브 이브', '알 오루바 사나', '사만 SC', '알 와흐다 아덴', '알 슈알라', '민타카 스타즈', '타이즈 유나이티드']), '3부': ('예멘 세컨드 디비전', ['모카 로버스', '마리브 에이스', '아덴 파이오니어스', '하드라마우트 이글스', '사나 스타즈', '시밤 유나이티드', '알 힐랄 유스', '사크르 블루스'])},
    '아프가니스탄': {'1부': ('아프가니스탄 챔피언스리그', ['샤힌 아스마예', '투판 하리로드', '시모르그 알보르즈', '마우웨이하이 아무', '데 스핀 가르 바잔', '오카반 힌두쿠시', '데 아바신 바페', '데 마이완드 아탈란']), '2부': ('아프가니스탄 퍼스트 리그', ['카불 스타즈', '헤라트 이글스', '칸다하르 라이온즈', '마자르샤리프FC', '잘랄아바드 로버스', '쿤두즈 워리어스', '바미안 하이랜더스', '가즈니 파이터스']), '3부': ('아프가니스탄 세컨드 디비전', ['판지시르 벨리', '바다흐샨 유나이티드', '팍티아 레인저스', '카불 시티 유스', '아무 파이오니어스', '힌두쿠시 에이스', '하리로드 보이스', '샤힌 유스'])},
    '중화 타이베이': {'1부': ('대만 프리미어리그', ['타이난 시티 (스틸)', '타이중 푸투로', '타이완 스틸', '타이페이 비킹스', '타이완 파워 컴퍼니', '타이페이 시티 타퉁', '항유엔 FC', '밍추안 대학']), '2부': ('대만 챌린지 리그', ['타이페이 로버스', '가오슝 스타즈', '신베이 유나이티드', '타오위안 레인저스', '신주 FC', '타이둥 파이오니어스', '하롄 FC', '체육대학 FC']), '3부': ('대만 디스트릭트 리그', ['킬룽 시티', '이란 에이스', '자이 로버스', '타이난 유스', '푸투로 B', '타이페이 타이탄스', '포모사 FC', '그린 아일랜드'])},
    '몰디브': {'1부': ('몰디브 디베히 리그', ['마지야 S&RC', '클럽 발렌시아', 'TC 스포츠 클럽', '유나이티드 비크토리', '에이글스', '수퍼 그린 스탠이', '클럽 그린 스트리트', '부루 스포츠']), '2부': ('몰디브 2부 리그', ['JJ 스포츠 클럽', '클럽 PK', '말레 시티 FC', '훌후말레 유나이티드', '비아두 로버스', '쿠라마티 킹스', '간 아일랜드', '디구 FC']), '3부': ('몰디브 3부 리그', ['마지야 유스', '바 아톨 레인저스', '아두 시티', '라 아톨 에이스', '말레 유스 스타즈', '발렌시아 B', '에이글스 유스', '파라다이스 FC'])},
    '미얀마': {'1부': ('미얀마 내셔널리그', ['양곤 유나이티드', '샤인 유나이티드', '한타와디 유나이티드', '야다나본 FC', '에야와디 유나이티드', '미야와디 FC', '이스턴 아웃사이더', '마하 유나이티드']), '2부': ('미얀마 MNL-2', ['네피도 FC', '가치 부르고스', '실버 스타즈', '만달레이 유나이티드', '바고 레인저스', '친 유나이티드', '카친 유나이티드', '라카인 유나이티드']), '3부': ('미얀마 아마추어리그', ['타운지 스타즈', '몰라먄 FC', '양곤 로버스', '델타 이글스', '샤인 유스', '한타와디 B', '모곡 루비스', '파안 킹스'])},
    '온두라스': {'1부': ('온두라스 리가 나시오날', ['올림피아', '모타구아', '레알 에스파냐', '마라톤', '오스피탈레트', '오란초 FC', '빅토리아', '유니버시다드']), '2부': ('온두라스 아센소 리그', ['플라텐세', '레알 유벤투스', '파리야스 원', '아틀레티코 촐로마', '유니온 사바', '론 가드', '수라 에이스', '요로 FC']), '3부': ('온두라스 리가 마요르', ['산페드로수라 로버스', '테구시갈파 스타즈', '라세이바 파이오니어스', '올림피아 유스', '모타구아 B', '코판 마야스', '촐루테카', '단리 유나이티드'])},
    '엘살바도르': {'1부': ('엘살바도르 프리메라 디비전', ['알리안사 FC', 'CD FAS', '아길라', '루이스 앙헬 피르포', '이시드로 메타판', '플라텐세 자카테콜루카', 'Fuerte San Francisco', 'Municipal Limeno']), '2부': ('엘살바도르 세군다 디비전', ['찰라테낭고', '아틀레티코 마르테', '손소나테 FC', '티탄', '카카우아티케', '산타 테클라', '이코스테페케', '유벤투드 인디펜디엔테']), '3부': ('엘살바도르 테르세라 디비전', ['산살바도르 로버스', '산타아나 에이스', '산미겔 파이터스', '아길라 B', '알리안사 유스', '우수루탄', '라 리베르타드', '아우아차판'])},
    '아이티': {'1부': ('아이티 상피오나 나시오날', ['정치적 안정 기원 FC', 'Arcahaie FC', 'Violette AC', 'Don Bosco FC', 'Cavaly AS', 'FICA', 'Capoise', 'Baltimore SC']), '2부': ('아이티 디비전 2', ['라싱 클럽 하이시엔', '유벤투스 레카이', '미라발레 FC', '자크멜 유나이티드', '고나이브 스타즈', '레오간 로버스', '제레미 에이스', '포르토프랭스 시티']), '3부': ('아이티 디비전 3', ['바페 유스', '돈보스코 B', '카프하이시엔 이글스', '레카이 워리어스', '아르카하이 B', '페티온빌 FC', '생마르크 레인저스', '레 코이프'])},
    '퀴라소': {'1부': ('퀴라소 프로메 디비시온', ['CRKSV 정 테이스트', '새 브라더스', 'SUBT', 'RKSV 센트로 도미니토', '새 네덜란드', '새 루마니아', '데포르티보 바르베', '시티 빌보드']), '2부': ('퀴라소 세군드 디비시온', ['RKSV 셰르펜후벨', '빅토리 보이스', '베스타', '인데펜디엔테 카르피', '빌렘스타트 로버스', '피스카데라', '블라우브그룬트', '바르베 유스']), '3부': ('퀴라소 테르세르 디비시온', ['정 테이스트 B', '새 브라더스 B', '서브트 레인저스', '센트로 유스', '풀톤 스타즈', '웨스트푼트 에이스', '잔타 카타리나', '마튄 FC'])},
    '트리니다드 토바고': {'1부': ('TT 프리미어 풋볼 리그', ['디펜스 포스', 'AC 포트오브스페인', '클럽 산도', '라 호르케타 레인저스', 'W 코네션', '센트럴 FC', '포인트 포틴 시빅', '카레니 에어로']), '2부': ('TT 티어 2 리그', ['산 후안 자블로테', '모란트 칼레도니아', '프리즌 서비스', '경찰 FC', '토바고 유나이티드', '스카보로 스타즈', '아리마 로버스', '샤구아나스 에이스']), '3부': ('TT 리조널 디비전', ['포트오브스페인 로버스', '산도 유스', '디펜스포스 B', '토바고 크루세이더스', '라브레아 킹스', '시파리아 이글스', '디에고 마틴', '튜나푸나 FC'])},
    '과테말라': {'1부': ('과테말라 리가 나시오날', ['코무니카시오네스', '무니시팔', '안티구아 GFC', '셀라후 MC', '말라카테코', '과스타토야', '코반 임페리알', '시오날레']), '2부': ('과테말라 프리메라 디비전', ['에스쿠인틀라', '사카치스파스', '마르켄세', '수치테페케스', '미클란', '우니베르시다드 SC', '유벤투드 피눌레카', '카르차']), '3부': ('과테말라 세군다 디비전', ['과테말라 시티 로버스', '안티구아 B', '코무니카시오네스 유스', '무니시팔 B', '페텐 이글스', '잘라파 스타즈', '티키사테', '솔롤라 FC'])},
    '안티가 바부다': {'1부': ('안티가 프리미어리그', ['그레네이즈 FC', '올 세인츠 유나이티드', '올드 로드', 'SAP FC', '파햄 FC', '호스포드 그린베이', '윌리스 FC', '스위츠 FC']), '2부': ('안티가 퍼스트 디비전', ['리베르타 블랙 호크스', '엠파이어 FC', '피고츠 불레츠', '벨라 비스타', '블루 자이언츠', '바부다 팔콘스', '세인트 존스 로버스', '골든 에이스']), '3부': ('안티가 세컨드 디비전', ['파햄 B', '올 세인츠 B', '레드 이글스', '세인트 피터스', '크랩스 시티', '잉글리시 하버', '볼란스 FC', '그레네이즈 유스'])},
    '세인트키츠 네비스': {'1부': ('SKN SKFA 프리미어리그', ['SL 호스포드 스타이리스', '가든 핫스퍼스', '네빌 유나이티드', '바스테르 소울 주니어', '카이온 제트스트리크스', '맨티스 FC', '올드 로드 제트', '빌리지 수퍼스타즈']), '2부': ('SKN 디비전 1', ['네비스 AC', '샤를스타운 파이터스', '세인트 폴스 유나이티드', '샌디 포인트', '스페셜 포스', '바스테르 로버스', '피셔맨 에이스', '블랙 호크스']), '3부': ('SKN 디스트릭트 리그', ['빌리지 유스', '가든 핫스퍼스 B', '카이온 유스', '네비스 킹스', '진저랜드', '새들러스 FC', '콩리 FC', '세인트 메리스'])},
    '도미니카 공화국': {'1부': ('리가 도미니카나 데 풋볼', ['치바오 FC', '클럽 아틀레티코 판토하', '모카 FC', '델피네스 델 에스테', '아틀레티코 베가 레알', '오야M FC', '산 크리스토발', '아틀레티코 산토도밍고']), '2부': ('LDF 세군다 디비전', ['라 로마나 FC', '산티아고 로버스', '푸에르토 플라타', '바라호나 이글스', '산 페드로 스타즈', '바바 로얄스', '푼타카나 FC', '치바오 B']), '3부': ('도미니카 아마추어리그', ['산토도밍고 파이오니어스', '판토하 유스', '모카 B', '바니 킹스', '아주아 FC', '나구아 유나이티드', '사마나 블루스', '에스테 에이스'])},
    '니카라과': {'1부': ('니카라과 리가 프리메라', ['리얼 에스텔리', '디리앙헨', '발터 페레티', '마나과 FC', 'ART 자라파', '디리앙헨 유니온', '오코탈', '세바코']), '2부': ('니카라과 세군다 디비전', ['유벤투스 마나과', '치난데가 FC', '레알 마드리스', '마타갈파 FC', '후이갈파', '블루필즈 로버스', '마사야 에이스', '에스텔리 B']), '3부': ('니카라과 테르세라 디비전', ['그라나다 FC', '레온 워리어스', '지노테가', '보아코 킹스', '리바스 스타즈', '디리앙헨 유스', '마나과 로버스', '티피타파'])},
    '버뮤다': {'1부': ('버뮤다 프리미어리그', ['PHC 제브라스', '노스 쇼어 레이더스', '서머셋 트로잔스', '데번셔 쿠거스', '해밀턴 파리시', '스위프트 풋볼', '세인트 조지스 콜트', '패짓 레인저스']), '2부': ('버뮤다 퍼스트 디비전', ['수퍼 스타즈', '부두 보이스', '아일랜드 로버스', '웨스트 엔드 에이스', '사우스햄튼 레인저스', '불스 FC', '킹스턴 에이스', '제브라스 B']), '3부': ('버뮤다 코로나 리그', ['로열 버뮤다', '도크야드 파이터스', '세인트 데이비스', '워윅 FC', '스미스 시티', '펨브로크 이글스', '서머셋 유스', '해밀턴 유스'])},
    '세인트루시아': {'1부': ('세인트루시아 SLFA 퍼스트 디비전', ['B1 FC', '플래티넘 FC', '포크 스타즈', '마리곳 유나이티드', '가이아 코린스', '수프리에르 스타즈', '초이셀 FC', '비외포르 테크']), '2부': ('세인트루시아 세컨드 디비전', ['캐스트리스 로버스', '그로스 아일렛', '데너리 유나이티드', '미쿠드 에이스', '라보리 킹스', '안스 라 레이', '카나리스 FC', '플래티넘 B']), '3부': ('세인트루시아 디스트릭트 리그', ['포크 주니어스', '캐스트리스 유스', '그로스아일렛 B', '수프리에르 유스', '모네포스', '마부야 벨리', '초이셀 유스', '사우스 에이스'])},
    '그레나다': {'1부': ('그레나다 GFA 프리미어리그', ['카리브 가든스', '파라다이스 FC 인티', '세인트 존스 스포츠', '하드 록 FC', '퀸스 파크 레인저스', '챈트미엘 FC', '이글스 수퍼 스트라이커스', '사보 유나이티드']), '2부': ('그레나다 퍼스트 디비전', ['세인트 조지스 로버스', '구야브 스타즈', '그레렌빌 레인저스', '카리아쿠 팔콘스', '쁘띠 마르티니크', '빅토리 보이스', '챈트미엘 B', '하드록 B']), '3부': ('그레나다 세컨드 디비전', ['파라다이스 유스', '카리브 유스', '스파이스 이글스', '넛메그 킹스', '그랜드 안세', '와블린 FC', '노스 스타즈', '사우스 에이스'])},
    '몬트세랫': {'1부': ('몬트세랫 챔피언십', ['이글스 풋볼 아카데미', '플리머스 로버스', '다이아몬드 보이스', '살렘 스타즈', '우드랜즈 킹스', '쿠도스 FC', '올드 로드', '브레이즈 FC']), '2부': ('몬트세랫 디비전 1', ['세인트 피터스 에이스', '리틀 베이 로버스', '화산 가드', '수프리에르 힐스', '해리스 FC', '다이아몬드 B', '살렘 유스', '올드로드 B']), '3부': ('몬트세랫 리조널 리그', ['이글스 유스', '플리머스 주니어', '노스 게이트', '사우스 이글', '킹스턴 파이오니어', '로얄 몬트세랫', '윈드워드 블루스', '베이사이드 익스프레스'])},
    '푸에르토리코': {'1부': ('리가 나시오날 데 푸에르토리코', ['바야몬 FC', '푸에르토리코 솔', '메트로폴리탄 FA', '콰타나 FC', '헤리카나 풋볼', '산후안 아틀레티코', '카구아스 스포츠', '마야구에스 FC']), '2부': ('푸에르토리코 프리메라 디비전', ['폰스 레오네스', '과야마 FC', '아레시보 로버츠', '캐롤라이나 자이언츠', '바야몬 B', '솔 주니어스', '도라도 이글스', '트루히요 알토']), '3부': ('푸에르토리코 아마추어리그', ['산후안 로버스', '메트로폴리탄 B', '보린켄 스타즈', '이사벨라 파이터스', '유나이티드 카리브', '파하르도 에이스', '과이나보 FC', '린콘 서퍼스'])},
    '세인트빈센트 그레나딘': {'1부': ('SVGFF 프리미어 디비전', ['호프 인터내셔널', '주에 FC', '아노스 벨리 스타즈', '시온 힐', '시스템 3 FC', '레이턴 아스날', '베키아 유나이티드', '파스토르 파이터스']), '2부': ('SVGFF 퍼스트 디비전', ['킹스타운 로버스', '조지타운 스타즈', '샤프 보이스', '그레나딘 팔콘스', '호프 주니어스', '시스템3 B', '카리아쿠 킹스', '바로우아리에 에이스']), '3부': ('SVGFF 코뮤니티 리그', ['레이오 가드', '시온힐 유스', '샤프 주니어스', '윈드워드 에이스', '리워드 이글스', '센트럴 로버스', '주에 B', '베키아 마린스'])},
    '바베이도스': {'1부': ('바베이도스 프리미어리그', ['웨이머스 웨일스', 'BDF 스포팅 클럽', '파라다이스 FC', '엠파이어 클럽', 'UWI 블랙버즈', '노트르담 SC', '브리태니카 FC', '일로이 FC']), '2부': ('바베이도스 디비전 1', ['브리지타운 로버스', '세인트 앤드류 라이온즈', '일로이 B', '웨일스 주니어', '화이트 이글스', '크레인 FC', '호리존 스타즈', '블랙버즈 B']), '3부': ('바베이도스 디비전 2', ['파라다이스 유스', 'BDF 아마추어', '세인트 필립 킹스', '오이스타인스', '스페이츠타운 에이스', '베이사이드 로버스', '가든 보이스', '엠파이어 주니어'])},
    '도미니카 연방': {'1부': ('도미니카 프리미어리그', ['남아프리카 하이랜드', '리가 도미니카나', '하부 유나이티드', '바스 이글스', '더블랙 에이스', '센트럴 SC', '포츠머스 보머스', '로조 시티']), '2부': ('도미니카 퍼스트 디비전', ['마오 FC', '로조 로버스', '그랜드 베이', '마리고 스타즈', '세인트 조셉', '사우프리 아틀레티코', '포츠머스 B', '리가 B']), '3부': ('도미니카 아마추어리그', ['하부 주니어', '센트럴 유스', '윈드워드 로버스', '칼리나고 이글스', '케인 필드', '에메랄드 스타즈', '웨스트 엔드 에이스', '로조 킹스'])},
    '케이맨 제도': {'1부': ('케이맨 제도 CIFA 프리미어리그', ['보덴 타운 FC', '스콜라스 인터내셔널', '엘리트 SC', '이스트 엔드 유나이티드', '조지 타운 SC', '퓨처 SC', '로열 케이맨', '선셋 FC']), '2부': ('케이맨 제도 퍼스트 디비전', ['리틀 케이맨 FC', '케이맨 브락', '선셋 B', '보덴타운 B', '스콜라스 주니어', '노스 사이드', '웨스트 베이 로버스', '사우스 사운드']), '3부': ('케이맨 제도 디스트릭트 리그', ['엘리트 유스', '조지타운 로버스', '브락 이글스', '파이오니어 FC', '선셋 에이스', '카리브 익스프레스', '세븐 마일', '럼 포인트'])},
    '아루바': {'1부': ('아루바 디비시온 디 오노르', ['RCA 아루바', 'SV 다코타', 'SV 데포르티보 나시오날', 'SV 라 파마', 'SV 에스트레야', 'SV 브리타니아', 'SV 리버 플레이트', 'SV 카케티오']), '2부': ('아루바 디비시온 우노', ['SV 유벤투스 아루바', 'SV 부바리', 'SV 스포르팅', 'SV 인데펜디엔테', 'RCA 아루바 B', '다코타 주니어', '오란스타드 로버스', '산니콜라스 에이스']), '3부': ('아루바 디비시온 도스', ['브리타니아 B', '라파마 유스', '에스트레야 B', '사바네타 킹스', '노르드 스타즈', '파라데라 에이스', '발렌시아 아루바', '카리브 블루스'])},
    '솔로몬 제도': {'1부': ('솔로몬 제도 S-리그', ['솔로몬 워리어스', '헨더슨 이글스', '코사 FK', '중앙 해안 FC', '라우구 유나이티드', '마리스트 FC', '레알 카카우', '호니아라 시티']), '2부': ('솔로몬 제도 디비전 1', ['말레이타 이글스', '과달카날 FC', '서부 연합', '이글스 유스', '워리어스 B', '마키라 스타즈', '이사벨 피닉스', '호니아라 로버스']), '3부': ('솔로몬 제도 디스트릭트 리그', ['초이셀 워리어스', '테모투 에이스', '렌벨 로버스', '마리스트 B', '코사 주니어', '코코넛 보이스', '베이사이드 FC', '스타즈 오션'])},
    '뉴칼레도니아': {'1부': ('뉴칼레도니아 수페르 리그', ['AS 마장타', 'AS 이엔겐 스포르', 'AS 가이차', '수퍼 리푸', 'AS 뢰시', '탕겐 스포츠', '누메아 시티', 'AS 몽도르']), '2부': ('뉴칼레도니아 프로모션 도뇌르', ['AS 쿤디에', 'AS 포야', 'AS 카날라', '마장타 B', '이엔겐 B', '부라일 로버스', '코네 스타즈', '둠베아 유나이티드']), '3부': ('뉴칼레도니아 디스트릭트 리그', ['우베아 에이스', '마레 킹스', '리푸 스타즈', '가이차 유스', '누메아 로버스', '몽도르 B', '파이타 FC', '티오 워리어스'])},
    '타히티': {'1부': ('타히티 리가 1', ['AS 피라에', 'AS 베뉘스', 'AS 드래곤', 'AS 테파나', 'AS 센트럴 스포츠', 'AS 푸나아우이아', 'AS 타하아', 'AS 마히나']), '2부': ('타히티 리가 2', ['AS 엑셀시오르', 'AS 아루에', 'AS 파페에테', '피라에 B', '베뉘스 B', '드래곤 주니어', '보라보라 FC', '라이아테아 스타즈']), '3부': ('타히티 리가 3', ['모오레아 유나이티드', '후아히네 에이스', '파파라 로버스', '테파나 B', '센트럴 유스', '마르키사스 이글스', '타히티 가드', '오션 블루스'])},
    '파푸아뉴기니': {'1부': ('파푸아뉴기니 내셔널 소커리그', ['헤카리 유나이티드', '라에 시티 FC', '포트모르즈비 스타즈', '걸프 코모로', '모로베 와우', '마당 FC', '포트모르즈비 아일런더스', '하이랜드 스타즈']), '2부': ('PNG 퍼스트 디비전', ['라바울 기아스', '키엠베 SC', '마누스 킹스', '라에 시티 B', '헤카리 주니어', '고로카 이글스', '마운트하겐', '웨와크 로버스']), '3부': ('PNG 리조널 리그', ['부카 워리어스', '바니모 에이스', '사마라이 블루스', '걸프 유스', '모로베 레인저스', '포트모르즈비 유스', '부게인빌 FC', '오션 시티'])},
    '바누아투': {'1부': ('바누아투 VFF 내셔널 수페르리그', ['이페라 체프', '타페아 FC', '아미칼레 FC', '겔럭시 FC', '시아라가 FC', '에라코르 골든 스타즈', '투푸카 FC', '말람파 리바이버스']), '2부': ('바누아투 포트빌라 프리미어리그', ['포트빌라 로버스', '산토 이글스', '루간빌 유나이티드', '타페아 B', '겔럭시 주니어', '펜테코스트 파이터스', '앰브림 스타즈', '말레쿨라 에이스']), '3부': ('바누아투 디스트릭트 리그', ['이페라 B', '에라코르 주니어', '타나 라이온즈', '에피 로버스', '가우아 킹스', '모타 라바', '포트빌라 유스', '산토 파이오니어스'])},
    '사모아': {'1부': ('사모아 내셔널리그', ['루페 올레 소아가', '키위 FC', '바이바세 타이', '바이텔레 우타', '레아우바아 FC', '아피아 스타즈', '토가푸아푸아', '바이알라 유나이티드']), '2부': ('사모아 퍼스트 디비전', ['모아우아 FC', '살레롤로가', '파가말로 에이스', '키위 B', '루페 주니어', '아피아 로버스', '바이바세 B', '우폴루 이글스']), '3부': ('사모아 디스트릭트 리그', ['사바이이 워리어스', '사포투', '물리파누아', '아피아 유스', '토가푸아푸아 B', '바이텔레 주니어', '오션 이글스', '사모아 킹스'])},
    '아메리칸사모아': {'1부': ('아메리칸사모아 FFAS 시니어 리그', ['파고 유스', '우툴레이 유스', '파사토아 FC', '파가토고 블루즈', '타푸나 하이아크', '릴리 시티', '일라오아 앤 투오티', '레오네 라이온즈']), '2부': ('아메리칸사모아 디비전 1', ['누우우일 FC', '타푸나 B', '파고 주니어', '우툴레이 B', '레오네 B', '파가토고 B', '알로파우 에이스', '아우아 스타즈']), '3부': ('아메리칸사모아 아마추어리그', ['타부 시티', '빌리지 보이스', '파고 C', '우툴레이 C', '일라오아 B', '레오네 유스', '마누아 이글스', '투투일라 로버스'])},
    '쿠크 제도': {'1부': ('쿠크 제도 라로통가 라운드 컵', ['투파파 마라에렝가', '니카오 소커탁', '푸아이쿠라 FC', '아바티우 FC', '마타베라 FC', '티티카베카 FC', '라로통가 로버스', '아바루아 스타즈']), '2부': ('쿠크 제도 퍼스트 디비전', ['투파파 B', '니카오 B', '아이투타키 FC', '아티우 킹스', '망가이아 로버츠', '푸아이쿠라 B', '마타베라 B', '티티카베카 B']), '3부': ('쿠크 제도 디스트릭트 리그', ['아바루아 유스', '라로통가 주니어', '펜린 이글스', '마니히키', '라카항가 에이스', '미티아로', '투파파 C', '니카오 C'])},
    '수리남': {'1부': ('수리남 SVB 최고리그', ['SV 로빈후드', '인터 문고타푸', 'SV 트란스발', '노트 카포에르', 'PVV 수리남', 'SV 보르오르더', '레오 빅토르', '산토스 니케리']), '2부': ('수리남 SVB 퍼스트 디비전', ['SV 보스캄프', 'SV 엑셀시오르', '로빈후드 B', '트란스발 B', '파라나마 FC', '파라마리보 로버스', '코로니 스타즈', '마로와인 에이스']), '3부': ('수리남 SVB 세컨드 디비전', ['문고타푸 B', 'PVV 주니어', '니케리 킹스', '브로코폰도', '사라마카 FC', '코메와인', '수리남 유스', '로빈후드 C'])},
    '가이아나': {'1부': ('가이아나 GFF 엘리트 리그', ['프란타이아 스타즈', '에이스 디텍티브', '조지타운 풋볼 클럽', '웨스턴 타이거스', '덴 암스텔', '부익스턴 유나이티드', '말라 페르디카', '산토스 가이아나']), '2부': ('가이아나 리조널 디비전 1', ['루피누니 레인저스', '린덴 스타즈', '바비체 킹스', '조지타운 로버스', '웨스턴 B', '에세키보 에이스', '프란타이아 B', '마하이카']), '3부': ('가이아나 리조널 디비전 2', ['디메하라', '포메룬 워리어스', '바티카 FC', '부익스턴 B', '조지타운 유스', '린덴 주니어', '루피누니 이글스', '가이아나 에이스'])},
    '쿠바': {'1부': ('쿠바 캄페오나토 나시오날', ['산티아고 데 쿠바', '시에고 데 아빌라', '빌라 클라라', '라 하바나', '피나르 델 리오', '아르테미사', '라투나스', '과르다라바카']), '2부': ('쿠바 토르네오 데 아센소', ['시엔푸에고스', '카마구에이', '구안타나모', '그란마 FC', '올긴 FC', '산크티 스피리투스', '마탄사스', '라하바나 B']), '3부': ('쿠바 아마추어리그', ['산티아고 B', '빌라클라라 B', '이슬라 데 라 유벤투드', '마이아미 탈출 기원 FC', '바라데로 스타즈', '트리니다드 로버스', '아르테미사 유스', '카마구에이 B'])},
    '카보베르데': {'1부': ('카보베르데 프리메이라 디비시온', ['CS 민델렌세', '스포르팅 프라이아', '아카데미카 도 민델로', '트라바도레스', '볼카니코스', '보아비스타 프라이아', '살 레이 FC', '울트라마리나']), '2부': ('카보베르데 세군다 디비시온', ['아카데미카 프라이아', '바투쿠 FC', '데포르티보 프라이아', '더비 FC', '산타 마리아 FC', '민델렌세 B', '프라이아 로버스', '산 비센테 에이스']), '3부': ('카보베르데 리조널 리그', ['산티아고 이글스', '포고 스타즈', '살 아일랜드', '마요 FC', '브라바 워리어스', '니콜라우 에이스', '스포르팅 프라이아 B', '트라바도레스 유스'])},
    'DR콩고': {'1부': ('DR콩고 리나풋 링 1', ['TP 마젬베', 'AS 비타 클럽', 'DC 모테마 펨베', 'FC 생 엘로이 루포포', 'AS 마니에마 유니온', 'SM 상가 발렌데', 'JS 킨샤사', 'FC 레노아즈']), '2부': ('DR콩고 리나풋 링 2', ['치니쿠 FC', '루붐바시 스포츠', '돈 보스코 루붐바시', 'AS 도팽 누아르', 'AC 쿠야', '마젬베 B', '비타 클럽 B', '킨샤사 로버스']), '3부': ('DR콩고 프로빈셜 리그', ['고마 유나이티드', '키상가니 스타즈', '음부지마이 이글스', '카낭가 파이터스', '바카우 킹스', '루포포 B', '모테마펨베 B', '콩고 리버 FC'])},
    '기니': {'1부': ('기니 리그 1 프로', ['호로야 AC', '하피아 FC', 'AS 칼룸 스타즈', 'CI 카임사르', 'SO아르메', '밀로 FC', '아샴티 골든 보이스', '미네르 보케']), '2부': ('기니 리그 2 프로', ['사텔리트 FC', '펠란 AC', '엘레방 FC', '호로야 B', '하피아 B', '코나크리 로버스', '킨디아 스타즈', '라베 에이스']), '3부': ('기니 내셔널 챔피언십', ['시기리 이글스', '캉칸 워리어스', '은제레코레', '마무 FC', '프리아 에이스', '칼룸 B', '카임사르 주니어', '기니 에어포스'])},
    '가봉': {'1부': ('가봉 상피오나 나시오날 D1', ['AS 망가스포르', 'CF 무나나', 'US 비탐', 'AO 센트라프릭', '스타드 망드지', 'FC 105 리브르빌', '부엥기디 스포츠', 'AS 디카이']), '2부': ('가봉 상피오나 나시오날 D2', ['리브르빌 로버스', '포르장틸 FC', '오예무 SC', '프랑스빌 스타즈', '망가스포르 B', '무나나 B', 'USM 리브르빌', '로앙다 에이스']), '3부': ('가봉 프로빈셜 리그', ['무일라 FC', '치방가', '마코쿠 이글스', '람바레네 킹스', '비탐 B', 'FC105 주니어', '에스추어리 블루스', '가봉 이글스'])},
    '우간다': {'1부': ('우간다 프리미어리그', ['캄팔라 시티 카운실 (KCCA)', '바이퍼스 SC', '비야 비야 스포츠', '익스프레스 FC', '우간다 레버뉴 오소리티 (URA)', '불 FC', '마룬스 FC', '킷타라 FC']), '2부': ('우간다 빅 리그', ['블랙스 파워', 'Mbarara City', 'Wakiso Giants', 'NEC FC', 'KCCA B', '바이퍼스 주니어', '엔테베 로버스', '진자 스타즈']), '3부': ('우간다 리조널 리그', ['굴루 유나이티드', '아루아 힐', '음발레 히어로즈', '리라 스타즈', '마사카 FC', '익스프레스 B', '토로 킹스', '우간다 아미'])},
    '베냉': {'1부': ('베냉 샹피오나 나시오날', ['코톤 FC', '로토-포포 FC', '아예마 FC', '다제 FC', 'AS 코토누', '레콰일 FC', 'BUFFLES 데 보르고우', '드래곤 드 루에메']), '2부': ('베냉 리그 2', ['포르토노보 로버스', '파라쿠 스타즈', '나티팅구', '코톤 B', '로토포포 B', '우이다 에이스', '자파 FC', '아브라쿠']), '3부': ('베냉 디스트릭트 리그', ['주구 워리어스', '보히콘 FC', '코토누 보이스', '드래곤 B', '아예마 주니어', '아틀란틱 블루스', '모노 킹스', '베냉 에이스'])},
    '모리타니': {'1부': ('모리타니 수페르 D1', ['누아디부 FC', '킹스 누악쇼트', '티지크자', 'ACS 가르드 나시오날', 'AS 아르메', 'AS 콩코드', '누악쇼트 킹스', '인터 누악쇼트']), '2부': ('모리타니 수페르 D2', ['누아디부 B', '로소 FC', '주에라트 스타즈', '카에디 FC', '네마 레인저스', '아타르 에이스', '가르드 B', '누악쇼트 로버스']), '3부': ('모리타니 디스트릭트 리그', ['셀리바비', '키파 FC', '티시트', '샹게티 파이오니어스', '콩코드 B', '아르메 주니어', '데저트 에이스', '오아시스 FC'])},
    '마다가스카르': {'1부': ('마다가스카르 프로 리그', ['CFFA 안타나나리보', '조나크 FC', 'AS 아데마', '포사 주니어스', '엘게코 플러스', '디시플스 FC', 'Ajesaia', 'COSFA']), '2부': ('마다가스카르 리그 2', ['투아마시나 로버스', '마하장가 스타즈', '안치라베 FC', '피아나란초아', '포사 B', '아데마 B', '톨리아라 에이스', '안치라나나']), '3부': ('마다가스카르 리조널 리그', ['바오바브 FC', '레무르 스타즈', '바닐라 킹스', '안타나나리보 시티', '엘게코 주니어', '조나크 B', '노시베 에이스', '하이랜드 FC'])},
    '남수단': {'1부': ('남수단 프리미어리그', ['아틀라바라 FC', '알 힐랄 주바', '살람 와우', '말라칼 유나이티드', '아마랏 유나이티드', '카토르 주바', '야이비 에이스', '공 시티']), '2부': ('남수단 퍼스트 디비전', ['주바 로버스', '와우 스타즈', '말라칼 에이스', '아틀라바라 B', '알힐랄 B', '보르 시티 유나이티드', '벤티우 워리어스', '토릳 FC']), '3부': ('남수단 코뮤니티 리그', ['룸베크 라이온즈', '아웨일 스타즈', '남수단 아미', '카토르 주니어', '야이 로버스', '주바 파이오니어스', '나일 블루스', '공 주니어'])},
    '나미비아': {'1부': ('나미비아 프리미어 풋볼 리그', ['아프리칸 스타즈', '블루 워터스', '올랜도 파이러츠 빈트후크', '시빅스 FC', '나미비아 마이티 건스', '유엔남 FC', '타이거스 빈트후크', '정 칼리']), '2부': ('나미비아 퍼스트 디비전', ['빈트후크 로버스', '월비스베이 에이스', '룬두 킹스', '아프리칸스타즈 B', '블루워터스 B', '오샤카티 시티', '마리엔탈', '스바코프문트']), '3부': ('나미비아 리조널 리그', ['고바비스 라이온즈', '켸트만스호프', '오티와롱고', '타이거스 주니어', '시빅스 B', '유엔남 주니어', '나미브 디저트', '칼라하리 에이스'])},
    '기니비사우': {'1부': ('기니비사우 캄페오나토 나시오날', ['스포르팅 클루브 드 비사우', '벤피카 드 비사우', 'UDIB 비사우', '소니 드 가부', 'FC 카체우', '카치오 유나이티드', '포르투 드 비사우', '발란타 데 만소아']), '2부': ('기니비사우 세군다 디비시온', ['바파타 FC', '콰이니아 SC', '비사우 로버스', '스포르팅 B', '벤피카 B', '만소아 주니어', '가부 에이스', '볼라마 FC']), '3부': ('기니비사우 테르세이라 디비시온', ['플라푸 FC', '비옴보 킹스', '오이오 워리어스', '루바 FC', 'UDIB 주니어', '카체우 B', '비사우 에이스', '포르투 주니어'])},
    '콩고공화국': {'1부': ('콩고 샹피오나 나시오날 MTN', ['오토호 FC', '디아블 누아르', 'AC 레오파드', '카라 브라자빌', '에투알 뒤 콩고', 'V.클럽 모캉다', 'JST 브라자빌', '인터 클럽']), '2부': ('콩고 퍼스트 디비전', ['포앵트누아르 로버스', '브라자빌 스타즈', '오토호 B', '디아블누아르 B', '아스 포토-포토', '니아리 에이스', '부엔자', '쿠일루 FC']), '3부': ('콩고 아마추어리그', ['레오파드 B', '카라 주니어', '에투알 B', '돌리시 워리어스', '오완도 킹스', '유나이티드 콩고', '브라자빌 보이스', '포앵트누아르 유스'])},
    '브루나이': {'1부': ('브루나이 수페르 리그', ['DPMM FC', '카스카 FC', '코타 레인저스', 'MS ABDB (군팀)', '인드라 프리미어', 'MS PDB (경찰팀)', '바브룰 스타즈', '룬 바방']), '2부': ('브루나이 프리미어리그', ['반다르 세리 베가완 FC', '투통 킹스', '벨라이트 로버스', '템부롱 이글스', 'DPMM B', '인드라 주니어', '코타 주니어', '림바 스타즈']), '3부': ('브루나이 디스트릭트 리그', ['가동 파이터스', '베라카스 에이스', '무아라 브리즈', '세리아 FC', '쿠알라 벨라이트', 'MS ABDB B', '브루나이 유스', '술탄 워리어스'])},
    '지부티': {'1부': ('지부티 프리미어리그', ['AS 아르타/솔라 7', 'AS 알리 사비에', 'GR/SIAF', '지부티 텔레콤', '포트 FC', 'AS 디킬', 'AS 오복', '하우들리']), '2부': ('지부티 퍼스트 디비전', ['지부티 시티 로버스', '타주라 스타즈', '도르할레', '아르타 B', '텔레콤 주니어', '알리사비에 B', '유니온 아프리카', '홍해 에이스']), '3부': ('지부티 코뮤니티 리그', ['로얄 지부티', '가바드', '발발라 파이터스', '디킬 주니어', '오복 에이스', '바브 엘 만데브', '포트 B', '솔라 유스'])},
    '파키스탄': {'1부': ('파키스탄 프리미어리그', ['칸 리서치 래보러토리 (KRL)', '파키스탄 아미', '파키스탄 WAPDA', '카라치 포트 트러스트', '수이 소던 가스 (SSGC)', '무슬림 FC', '파키스탄 네이비', '라이알푸르 FC']), '2부': ('파키스탄 PFF 축구 연맹리그', ['이슬라마바드 로버스', '라호르 시티 FC', '카라치 킹스', '페샤와르 스타즈', '퀘타 파이터스', 'KRL B', '아미 주니어', 'WAPDA B']), '3부': ('파키스탄 디스트릭트 리그', ['파이살라바드 에이스', '멀탄 유나이티드', '실알코트', '구즈란왈라', '하이데라바드', '지할람', '카슈미르 이글스', '카라치 유스'])},
    '세이셸': {'1부': ('세이셸 프리미어리그', ['포레스트 유나이티드', '라 파스 FC', '생 미셸 FC', '코트 도르 FC', '리베이르 스타즈', '바자 브라더스', '라이트 스타즈', '안세 레유니']), '2부': ('세이셸 챔피언십', ['빅토리아 시티 FC', '마헤 로버스', '프라슬린 이글스', '라디그 에이스', '포레스트 B', '생미셸 B', '벨 에어 FC', '글라시스']), '3부': ('세이셸 디스트릭트 리그', ['보 발롱', '안세 루아얄', '포드 글라우드', '카스케이드', '코트도르 B', '라파스 주니어', '세이셸 유스', '인디안 오션'])},
    '동티모르': {'1부': ('동티모르 리가 푸트볼 아마도라 프리메라', ['라릴라 FC', '카르케투 디리', '아틀레티코 울트라마르', '폰테 레스테', '스포르팅 디리', '아사 아카데미카', 'SLB 라이팔라', '디나모 디리']), '2부': ('동티모르 LFA 세군다', ['바우카우 스타즈', '말리아나 로버스', '리카이카 에이스', '에르메라 킹스', '라릴라 B', '카르케투 B', '나게르카', '로스팔로스']), '3부': ('동티모르 LFA 테르세라', ['오에쿠시 워리어스', '수아이 FC', '비케케 타운', '디리 유나이티드', '티모르 유스', '폰테레스테 B', '아사 주니어', '코코넛 보이스'])},
    '통가': {'1부': ('통가 메이저 리그', ['로토하아파이 유나이티드', '베이팅가 FC', '폴리오우아 FC', '하아파이 스타즈', '마리스트 통가', '누쿠알로파 에이스', '바바우 킹스', '에우아 파이오니어스']), '2부': ('통가 퍼스트 디비전', ['로토하아파이 B', '베이팅가 B', '무아 FC', '콜로포우', '라파하', '누쿠알로파 로버스', '마키 스타즈', '통가 탑']), '3부': ('통가 코뮤니티 리그', ['하아파이 주니어', '바바우 로버스', '에우아 이글스', '통가타푸 블루스', '누쿠알로파 유스', '마리스트 B', '오션 워리어스', '사우스 베이'])},
    '소말리아': {'1부': ('소말리아 프리미어리그', ['모가디슈 시티 클럽', '호르시드 FC', '데케데하 FC', '엘만 FC', '가디드카 FC', '모가디슈 유나이티드', '바나디르 SC', '소말리 유니온']), '2부': ('소말리아 퍼스트 디비전', ['키스마요 FC', '바이도아 스타즈', '하르게이사 에이스', '보사소 로버스', '호르시드 B', '엘만 B', '데케데하 주니어', '가르웨']), '3부': ('소말리아 디스트릭트 리그', ['조하르 FC', '발라드', '모가디슈 보이스', '바나디르 B', '나일 에이스', '소말리 가드', '소말리아 유스', '인디안 오션 스타즈'])},
    '터크스 케이커스 제도': {'1부': ('터크스 케이커스 프로보 프리미어리그', ['새 아카데미', '블루 힐스 킹스', '프로보 프리미어', '그랜드 터크 유나이티드', '수퍼 스타즈 TCI', '티타늄 FC', '레드 이글스', '플라밍고 FC']), '2부': ('TCI 퍼스트 디비전', ['사우스 케이커스 FC', '노스 케이커스', '미들 케이커스', '새 아카데미 B', '프로보 B', '그랜드터크 로버스', '블루힐스 주니어', '코코넛 보이스']), '3부': ('TCI 아마추어리그', ['솔트 케이', '파롯 케이', '파인 케이', '플라밍고 주니어', '레드이글스 B', '프로보 유스', '카리브 에이스', '티타늄 주니어'])},
    '괌': {'1부': ('괌 부도이저 수페르리그', ['나이키 스트라이커스', '괌 풋볼 클럽', '로버스 FC', '퀄리티 디스트리뷰터스', '아일랜더스 FC', '윙즈 FC', '사이드킥스 FC', 'NAPA 로버스']), '2부': ('괌 퍼스트 디비전', ['하갓냐 스타즈', '투몬 베이 FC', '데데도 에이스', '요나 로버스', '스트라이커스 B', '로버스 B', '괌 유나이티드', '타무닝']), '3부': ('괌 코뮤니티 리그', ['앤더슨 에어포스 베이스', '지하고 파이터스', '망길라오', '이글스 괌', '윙즈 주니어', '아일랜더스 B', '퍼시픽 블루스', '마리아나 에이스'])},
    '영국령 버진아일랜드': {'1부': ('BVIFA 내셔널리그', ['원 러브 유나이티드', '슈가 보이스 FC', '아일랜더스 FC', '이글스 FC BVI', '레벨스 FC', '월트 레인저스', '올드 매드리드', '포지티브 바이브스']), '2부': ('BVI 퍼스트 디비전', ['로드 타운 로버스', '토르톨라 스타즈', '버진 고르다 에이스', '요스트 반 다이크', '원러브 B', '슈가보이스 B', '아일랜더스 주니어', '카리브 브리즈']), '3부': ('BVI 코뮤니티 리그', ['아네가다 이글스', '비프 아일랜드', '포트 시티', '레벨스 B', '월트 주니어', '로드타운 보이스', 'BVI 유스', '블랙 호크스'])},
    '스리랑카': {'1부': ('스리랑카 챔피언스리그', ['블루 스타 SC', '디펜더스 FC', '콜롬보 FC', '레나운 SC', '라트남 SC', '아프 패커스', '시 시티', '마타라 시티']), '2부': ('스리랑카 퍼스트 디비전', ['네곰보 유스', '가골라 SC', '자프나 스타즈', '캔디 레인저스', '가일 워리어스', '블루스타 B', '콜롬보 B', '디펜더스 주니어']), '3부': ('스리랑카 디스트릭트 리그', ['트린코말리 에이스', '바티칼로아', '아누라다푸라', '쿠루네갈라', '라트나푸라', '레나운 B', '인디안 오션', '인도양 스타즈'])},
    '미국령 버진아일랜드': {'1부': ('USVIFA 프리미어리그', ['헬렌 라이츠', '리볼리 FC', 'LR 가드', '세인트 크로아 유나이티드', '세인트 토마스 FC', '라사 유나이티드', '웨스트 엔드 타이거스', '레이온 스타즈']), '2부': ('USVI 퍼스트 디비전', ['샬럿 아마리에 로버스', '크리스찬스테드 에이스', '프레데릭스테드', '세인트 존 이글스', '헬렌라이츠 B', '리볼리 주니어', '세인트토마스 B', '카리브 에이스']), '3부': ('USVI 코뮤니티 리그', ['크루즈 베이', '코랄 베이', '레드 후크', '서프 보이스', '타이거스 주니어', '세인트크로아 B', '라사 주니어', '버진 블루스'])},
    '부탄': {'1부': ('부탄 프리미어리그', ['팀푸 시티 FC', '파로 FC', '트랜스포트 유나이티드', 'Ugyen Academy', 'BFF 아마추어 (부탄축협 유스)', 'RTC FC', '나마 시티', '텐숭 FC']), '2부': ('부탄 퍼스트 디비전', ['푸엔초링 FC', '파로 유나이티드', '팀푸 로버스', '왕디 스타즈', '풍카 에이스', '팀푸시티 B', '파로 B', '트랜스포트 주니어']), '3부': ('부탄 디스트릭트 리그', ['자카르 이글스', '트롱사 파이터스', '몽가르', '삼드룹 종카르', '겔레푸 SC', '히말라얀 스타즈', '드래곤 킹스', '팀푸 보이스'])},
    '바하마': {'1부': ('바하마 BFA 시니어 리그', ['바하마스 유나이티드', '베어스 FC', '가든 힐스 라이온즈', '서부 연합 FC', '카발리어 FC', '다이너모스 FC', '레니게이즈 FC', '나소 시티 FC']), '2부': ('바하마 퍼스트 디비전', ['프리포트 로버스', '아바코 이글스', '엘루세라 에이스', '엑수마 스타즈', '베어스 B', '바하마스 B', '나소 로버스', '블루 바하마']), '3부': ('바하마 코뮤니티 리그', ['안드로스 파이터스', '파라다이스 아일랜드', '케이블 비치', '가든힐스 B', '카발리어 주니어', '오션 블루스', '카리브 킹스', '다이너모스 B'])},
    '안귈라': {'1부': ('안귈라 AFA 리그', ['닥 킹스 FC', '로링 라이온즈', '공격수 FC', '살사 보이스', 'ALH 가드', '업라이징 FC', '리틀 디치 FC', '다이아몬드 FC']), '2부': ('안귈라 퍼스트 디비전', ['더 벨리 로버스', '블로잉 포인트', '샌디 그라운드', '아일랜드 하버 에이스', '닥킹스 B', '로링라이온즈 B', '살사 주니어', '카리브 이글스']), '3부': ('안귈라 아마추어리그', ['크로커스 힐', '쇼얼 베이', '랑데부', '업라이징 B', '리틀디치 B', '벨리 보이스', '안귈라 유스', '스타즈 오션'])},
    '에리트레아': {'1부': ('에리트레아 프리미어리그', ['레드 시 FC', '아스마라 브루어리', '아도리스 FC', '안세바 체렌', '게자 반다', '덴덴 FC', '마이 테메나', '알 가드']), '2부': ('에리트레아 퍼스트 디비전', ['마사와 FC', '아싸브 스타즈', '테세네이 에이스', '바렌투', '레드시 B', '아스마라 B', '덴덴 주니어', '하일랜드 로버스']), '3부': ('에리트레아 코뮤니티 리그', ['나크파 워리어스', '케렌 로버스', '아디 케이', '마이테메나 B', '아도리스 주니어', '홍해 스타즈', '에리트레아 아미', '아스마라 보이스'])},
}

FORMATIONS = ["4-4-2","4-3-3","3-5-2","4-2-3-1","5-3-2","4-1-4-1","3-4-3"]

def _tier_to_int(tier):
    """LEAGUE_DATA의 tier 키를 정수로 정규화.
    기존 국가는 1/2/3 (int), 신규 국가는 '1부'/'2부'/'3부' (str)로 섞여 있다.
    '1部'(한자) 같은 오타도 방어적으로 흡수한다.
    챔스 출전팀 선발·승강 로직이 모두 tier=1(정수)로 조회하므로 반드시 정수여야 한다."""
    if isinstance(tier, int):
        return tier
    s = str(tier).strip()
    for n in ("1", "2", "3"):
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

NAME_DATA = {
    '대한민국': ['김민준', '조강현', '박준혁', '최태양', '정우성', '한지민', '서동현', '윤재원', '손흥민', '이강인', '황희찬', '조규성', '오현규', '배준호', '이재성', '황인범', '홍현석', '박용우', '정우영', '김민재', '김영권', '정승현', '설영우', '김진수', '김문환', '박진섭', '조현우', '송범근', '이창근', '고요한'],
    '브라질': ['가브리에우', '루카스', '마테우스', '펠리피', '하파에우', '브루누', '치아구', '안데르송', '비니시우스', '호드리구', '히샤를리송', '하피냐', '엔드릭', '마르티넬리', '카세미루', '파케타', '기마랑이스', '고메스', '안드레', '더글라스', '마르키뉴스', '밀리탕', '마갈량이스', '브레메르', '다닐루', '얀', '알리송', '에데르송', '벤투', '호베르투'],
    '아르헨티나': ['마테오', '산티아고', '라우타로', '파쿤도', '곤살로', '로드리고', '니콜라스', '아구스틴', '메시', '마르티네스', '알바레스', '디 마리아', '디발라', '가르나초', '곤살레스', '데 파울', '페르난데스', '맥 알리스터', '파레데스', '로 셀소', '로드리게스', '오타멘디', '로메로', '리산드로', '몰리나', '탈리아피코', '아쿠냐', '몬티엘', '아르마니', '룰리'],
    '프랑스': ['루카', '위고', '테오', '킬리안', '오렐리앙', '마르쿠스', '랑달', '윌리암', '음바페', '그리즈만', '뎀벨레', '지루', '튀람', '콜로 무아니', '바르콜라', '코망', '추아메니', '카마빙가', '캉테', '라비오', '포파나', '살리바', '우파메카노', '코나테', '에르난데스', '쿤데', '파바르', '메냥', '삼바', '아레올라'],
    '독일': ['레온', '카이', '플로리안', '자말', '요나스', '르로이', '토마스', '요주아', '하베르츠', '무시알라', '비르츠', '자네', '퓔크루크', '그나브리', '뮬러', '운다브', '키미히', '크로스', '안드리히', '귄도안', '고레츠카', '뤼디거', '타', '슐로터베크', '라움', '미텔슈테트', '헨리히스', '노이어', '테어 슈테겐', '바우만'],
    '스페인': ['파블로', '알레한드로', '카를로스', '가비', '페드리', '페란', '알바로', '마르코', '모라타', '야말', '윌리엄스', '올모', '호셀루', '오야르사발', '토레스', '로드리', '수비멘디', '루이스', '메리노', '바에나', '라포르트', '르 노르망', '카르바할', '그리말도', '쿠쿠레야', '비비안', '나바스', '시몬', '라야', '레미로'],
    '잉글랜드': ['잭', '해리', '메이슨', '마커스', '주드', '필', '부카요', '데클란', '케인', '사카', '포든', '벨링엄', '래시포드', '왓킨스', '팔머', '고든', '라이스', '마이누', '갤러거', '마두에케', '스톤스', '워커', '매과이어', '트리피어', '게히', '고메스', '콘사', '픽포드', '램스데일', '헨더슨'],
    '포르투갈': ['조앙', '디오구', '베르나르두', '하파엘', '후벵', '페드로', '곤살루', '비티냐', '호날두', '레앙', '조타', '펠릭스', '하무스', '네투', '콘세이상', '페르난데스', '실바', '네베스', '팔리냐', '오타비우', '디아스', '페페', '이나시우', '달로', '칸셀루', '멘데스', '세메두', '코스타', '사', '파트리시우'],
    '이탈리아': ['로렌초', '페데리코', '니콜로', '산드로', '마르코', '다비데', '알레산드로', '마테오', '키에사', '스카마카', '레테기', '라스파도리', '차카니', '바렐라', '조르지뉴', '크리스탄테', '프라테시', '펠레그리니', '로카텔리', '바스토니', '칼라피오리', '다르미안', '디마르코', '만치니', '부온조르노', '디 로렌초', '돈나룸마', '비카리오', '메레트'],
    '네덜란드': ['프렌키', '마테이스', '버질', '코디', '데이비', '라이언', '캘빈', '사비', '데파이', '각포', '말렌', '베호르스트', '시몬스', '베르바인', '지르크제이', '더 용', '코프메이너르스', '라인더스', '스하우텐', '베이날둠', '반 다이크', '아케', '더 리흐트', '둠프리스', '프림퐁', '더 브레이', '반 더 벤', '페르브뤼헌', '플레컨', '베일로우'],
    '크로아티아': ['루카', '이반', '마르첼로', '마리오', '로브로', '요시프', '니콜라', '안테', '모드리치', '페리시치', '크라마리치', '부디미르', '파샬리치', '블라시치', '브로조비치', '코바치치', '수치치', '마예르', '스타니시치', '그바르디올', '로브렌', '비다', '에를리치', '유리치', '찰레타차르', '소사', '리바야', '리바코비치', '그르비치', '이부시치'],
    '덴마크': ['크리스티안', '피에르', '마르틴', '토마스', '안드레아스', '시몬', '미켈', '카스페르', '에릭센', '회이룬', '안데르센', '딜레이니', '돌베르', '담스고르', '코르넬리우스', '린스트룀', '휼만', '크리스텐센', '스트뤼게르', '베스테르고르', '멜레', '바', '넬손', '보일레센', '니센', '슈마이첼', '올센', '뢰노브', '한센'],
    '벨기에': ['케빈', '에당', '로멜루', '유리', '악셀', '레안드로', '티모시', '샤를', '더 브라위너', '틸레만스', '루카쿠', '카라스코', '도쿠', '오펜다', '바카요코', '바나켄', '망갈라', '비첼', '베르통언', '알데르베이럴트', '카스타뉴', '테아테', '드바스트', '토마스', '파스', '뫼니에', '쿠르투아', '미뇰레', '카스틸스', '셀스'],
    '일본': ['타쿠미', '다이치', '리츠', '준야', '와타루', '아오', '카오루', '코키', '쿠보', '미토마', '미나미노', '도안', '마에다', '아사노', '우에다', '카마다', '엔도', '모리타', '타나카', '하타테', '토미야스', '이타쿠라', '마치다', '스가와라', '이토', '타니구치', '나가토모', '시온', '마에카와', '오사코'],
    '이란': ['메흐디', '사르다르', '알리레자', '사만', '오미드', '루즈베', '자바드', '에산', '타레미', '아즈문', '자한바크시', '고도스', '가예디', '골리자데', '모간루', '에자톨라히', '알리자데', '누라프칸', '모헤비', '체시미', '투라니', '하치사피', '모함마디', '칼릴자데', '카나니', '레자에이안', '모하라미', '유세피', '베이란반드', '호세이니'],
    '사우디아라비아': ['살렘', '술탄', '피라스', '압둘라만', '압둘렐라', '알리', '야세르', '사우드', '알도사리', '알부라이칸', '알셰흐리', '가리브', '라디프', '알나제이', '칸노', '알파라지', '알카이바리', '알감디', '알말키', '알불라이히', '압둘하미드', '알간남', '알샤흐라니', '탐바크티', '라자미', '카데시', '알아키디', '알오와이스', '알마유프', '알하산'],
    '카타르': ['아크람', '알모에즈', '유세프', '아흐메드', '칼리드', '이스마엘', '타밈', '하산', '압둘아지즈', '모하메드', '모스타파', '자셈', '알리', '카림', '루카스', '부알렘', '페드로', '알마흐디', '타레크', '술탄', '호맘', '바삼', '메샬', '살라', '사아드', '알하이도스', '부디아프', '바르샴', '마디보'],
    '우즈베키스탄': ['엘도르', '압보스베크', '오스톤', '이고르', '자수르', '코지마트', '아지즈베크', '오타베크', '잘롤리딘', '오딜존', '잠시드', '디요르', '루슬란베크', '압두코디르', '루스탐', '파루흐', '후스니딘', '우마르', '셰르조드', '자파르무로드', '우트키르', '압두보히드', '보티랄리', '샴시딘', '쇼무로도프', '수유노프', '에쉬무로도프', '알리쿨로프', '유스포프'],
    '호주': ['크레이그', '잭슨', '해리', '매튜', '미첼', '코너', '라일리', '아이딘', '듀크', '보일', '굿윈', '이란쿤다', '타가트', '옌기', '어바인', '멧칼프', '맥그리', '바쿠스', '흐루스티치', '오닐', '수타', '롤스', '버제스', '베히치', '아킨슨', '밀러', '보스', '라이언', '고치', '토마스'],
    '중국': ['우레이', '장위닝', '웨이스하오', '탄롱', '왕상위안', '리커', '가오톈이', '셰펑페이', '쉬신', '청진', '주천지에', '장성룡', '류양', '덩한원', '왕전아오', '옌쥔링', '왕대뢰', '아란', '엘케손', '리레이', '하오쥔민', '인훙보', '구차오', '장린펑', '류빈빈', '지샹', '진징다오', '우시', '쩡청'],
    '인도': ['수닐', '랄리안주알라', '만비르', '비크람', '라힘', '이샨', '리스톤', '아니루드', '사할', '브랜든', '잭슨', '아푸이아', '수레시', '나오렘', '산데시', '라훌', '수바시시', '안와르', '메흐탑', '아카시', '니킬', '아메이', '구르프리트', '암린더', '비샬', '체트리', '창테', '싱', '징간', '타파'],
    '미국': ['크리스티안', '지오', '타일러', '조시', '브렌든', '디안드레', '조던', '웨스턴', '풀리식', '발로건', '웨어', '페피', '레이나', '아론슨', '라이트', '맥케니', '무사', '아담스', '카르도소', '데 라 토레', '로빈슨', '리차즈', '카터비커스', '림', '데스트', '스칼리', '룬드', '터너', '호바스', '존슨'],
    '멕시코': ['이르빙', '라울', '카를로스', '디에고', '에드손', '알렉시스', '로베르토', '미겔', '로사노', '히메네스', '산티아고', '안투나', '마르틴', '키뇨네스', '베가', '알바레스', '차베스', '산체스', '로모', '피네다', '몬테스', '바스케스', '가야르도', '아르테아가', '오초아', '말라곤', '훌리오', '오란티아', '안굴로', '세풀베다'],
    '코스타리카': ['조엘', '만프레드', '앤서니', '케네스', '알바로', '브랜든', '호시마르', '올란도', '제퍼슨', '알레한드로', '아리엘', '엘리아스', '제랄드', '워렌', '프란시스코', '후안 파블로', '훌리오', '하셀', '조셉', '페르난', '야엘', '제일랜드', '패트릭', '케빈', '아론', '캠벨', '우갈데', '칼보', '모라', '차콘'],
    '콜롬비아': ['루이스', '하메스', '팔카오', '후안', '쿠초', '라파엘', '알프레도', '다니엘', '디아스', '아리아스', '보레', '우리에베', '킨테로', '무리에엘', '쿠에스타', '모히카', '미나', '산체스', '오스피나', '바르가스', '레르마', '카스타뇨', '시니스테라', '비달', '코르도바', '메디나', '카르보네로', '팔라시오', '수니가', '아르메로'],
    '우루과이': ['다르윈', '페데리코', '로드리고', '마티아스', '로날드', '세바스티안', '막시밀리아노', '가스톤', '누녜스', '발베르데', '벤탄쿠르', '아라우호', '펠리스트리', '데 아라스카에타', '수아레스', '카바니', '우가르테', '올리베라', '비냐', '히메네스', '고딘', '카세레스', '난데스', '무뇨스', '베시노', '토레이라', '마리찰', '에레라', '무스레라', '로체트'],
    '에콰도르': ['에네르', '케빈', '조르디', '모이세스', '켄드리', '제레미', '안헬', '알란', '존', '카를로스', '프랑코', '조앙', '호세', '하네르', '피에로', '윌리안', '펠릭스', '페르비스', '안헬로', '조엘', '잭슨', '알렉산더', '에르난', '발렌시아', '카이세도', '인카피에', '에스투피냔', '도밍게스'],
    '모로코': ['하킴', '아슈라프', '야신', '소피앙', '유세프', '아제딘', '아민', '브라힘', '수피앙', '아유브', '일리아스', '타릭', '빌랄', '이스마엘', '나예프', '로맹', '누사이르', '야히아', '샤디', '압델', '다리', '치비', '무니르', '엘 메흐디', '모티', '보노', '엔네시리', '아구에르드', '마즈라위', '암라바트'],
    '세네갈': ['사디오', '파페', '이스마일라', '칼리두', '이드리사', '셰이쿠', '무사', '밤바', '마네', '사르', '디아타', '쿨리발리', '게예', '쿠야테', '디아비', '발데', '멘디', '야콥스', '사발리', '디알로', '음바예', '시스', '고미스', '소우', '카마라', '엔디아이예', '디에디우', '디아', '니아가테', '단파카'],
    '나이지리아': ['빅터', '새뮤얼', '모세스', '켈레치', '알렉스', '엠마누엘', '존', '케네스', '오시멘', '추쿠에제', '시몬', '이헤아나초', '룩맨', '아워니이', '데설스', '엔디디', '아리보', '에제', '우고추쿠', '오비', '에콰', '토모리', '바시', '콜린스', '아이나', '발로건', '보니페이스', '느와발리', '우조호', '악페이'],
    '카메룬': ['뱅상', '에릭', '앙드레', '브라이언', '장', '파스칼', '니콜라', '크리스티안', '아부바카르', '추포모팅', '토코 에캄비', '음뵤모', '앙귀사', '은참', '가나고', '파이', '엔쿨루', '카스텔레토', '온도아', '에파시', '응가되', '톨로', '메부데', '마에', '제레미', '홍글라', '구에', '쿤데', '바소고그', '말롱'],
    '이집트': ['모하메드', '아흐메드', '카림', '오마르', '마흐무드', '라미', '모스타파', '타레크', '살라', '트레제게', '마르무시', '엘네니', '함디', '자키', '라마단', '하산', '함단', '와르다', '사미르', '엘셰나위', '헤가지', '카라바', '아슈르', '파티', '솝히', '이맘', '와흐비', '가바스키'],
    '가나': ['모하메드', '이냐키', '조던', '토마스', '앙투안', '에르네스트', '카말딘', '압둘', '오스만', '이브라힘', '살리스', '엘리샤', '이디리수', '마지드', '살리수', '아마티', '타릭', '기디언', '알리두', '알렉산더', '데니스', '무민', '로렌스', '리처드', '조조', '쿠두스', '파티', '아예우', '윌리엄스', '램프티'],
    '튀니지': ['유세프', '세이페딘', '엘리아스', '아니스', '아이사', '엘리스', '한니발', '사이팔라', '하이트헴', '바셈', '모하메드', '함자', '사미', '후셈', '몬타사르', '야신', '알리', '얀', '와지디', '딜런', '우사마', '베치르', '무에즈', '아이멘', '므사크니', '스히리', '라이두니', '탈비'],
    '알제리': ['리야드', '바그다드', '아민', '사이드', '이스마엘', '후셈', '소피앙', '모하메드', '아니스', '라미즈', '나빌', '히샴', '히마드', '라얀', '라미', '아이사', '유세프', '케빈', '지네딘', '아흐메드', '자우엔', '안토니', '무스타파', '우사마', '마레즈', '벤세바이니', '벤나세르', '아우아르', '부네자'],
    '말라위': ['제랄드', '가바디뇨', '리처드', '쿠다', '프랭크', '피터', '조셉', '패트릭', '야미카니', '스테인리', '찰스', '존', '폴', '제임스', '로버트', '토마스', '데이비드', '마이클', '스탠리', '모세스', '림비카니', '프레셔스', '치코티', '치우케포', '달릿소', '음한고', '음바바', '찰레라', '이도나', '음포니'],
    '스위스': ['그라니트', '제르단', '브릴', '데니스', '레모', '마누엘', '얀', '요시프', '샤카', '샤치리', '엠볼로', '자카리아', '프로일러', '아칸지', '조머', '드르미치', '암두니', '바르가스', '에비셔', '시에로', '엘베디', '로드리게스', '셰어', '위드머', '코벨', '음보고', '스토펜', '추베르'],
    '오스트리아': ['마르셀', '콘라트', '크리스토프', '미하엘', '마르코', '슈테판', '다비드', '막시밀리안', '자비처', '라이머', '바움가르트너', '그리고리치', '아르나우토비치', '포슈', '알라바', '뵈버', '자이발트', '그릴리치', '카인츠', '비머', '단소', '트라우너', '린하르트', '펜츠', '린드너', '헤들', '엔드룹', '바이드만'],
    '우크라이나': ['아르템', '미하일로', '빅토르', '올렉산드르', '루슬란', '일리아', '비탈리', '타라스', '도브비크', '무드릭', '치한코우', '진첸코', '말리노우스키', '자바르니', '미콜렌코', '스테파넨코', '샤파렌코', '수다코우', '야렘추크', '코노플리아', '마트비옌코', '본다르', '탈로비예로우', '팀치크', '루닌', '트루빈', '부샨', '시도르추크', '주브코우', '야르몰렌코'],
    '터키': ['하칸', '아르다', '케난', '바르투', '메리흐', '찰라르', '페르디', '우구르잔', '찰하놀루', '귀렐', '이을디즈', '일마즈', '데미랄', '쇠윈쥐', '카디오글루', '차키르', '토순', '야즈즈', '카흐베치', '악튀르코글루', '코크추', '외잔', '아야한', '바르닥치', '카바크', '첼리크', '뮬뒤르', '귀노크', '바인디르', '킬리치소이'],
    '스웨덴': ['알렉산데르', '빅토르', '데얀', '에밀', '이삭', '요아킴', '루드비그', '로빈', '요케레스', '쿨루셉스키', '포르스베리', '히엔', '닐손', '아우구스틴손', '올센', '엘란가', '카유스테', '스타르펠트', '린델뢰프', '크라프트', '바알라', '존슨', '노르트펠트', '세마', '구드문드손', '살레트로스', '스반베리', '홀름', '라르손'],
    '웨일스': ['아론', '다니엘', '브레넌', '해리', '에단', '네코', '코너', '조', '램지', '제임스', '존슨', '윌슨', '암파두', '윌리엄스', '로버츠', '로드', '메팜', '데이비스', '브룩스', '무어', '매투시', '새비지', '쿠퍼', '대시', '워드', '킹', '해리스'],
    '폴란드': ['로베르트', '표트르', '카롤', '아르카디우시', '세바스티안', '야쿠프', '니콜라', '프셰미스와프', '레반도프스키', '지엘린스키', '시비데르스키', '밀리크', '시만스키', '키비오르', '잘레프스키', '프란코프스키', '피오르테크', '스치샤', '피오트로프스키', '모데르', '베드나레크', '다비도비치', '발루키에비치', '베레신스키', '슈체스니', '스코룹스키', '부우카', '그로시츠키', '부크사', '푸하치'],
    '헝가리': ['도미니크', '버나바시', '롤란드', '더니엘', '러일로', '윌리', '페테르', '보통드', '소보슬라이', '바르가', '살라이', '가즈다그', '클라인하이스러', '오르반', '굴라치', '딜부스', '너기', '셰페르', '볼라', '케르케즈', '설러이', '보토', '랑', '다르다이', '네메트', '호르바트', '아담', '치보트'],
    '세르비아': ['두샨', '알렉산다르', '세르게이', '루카', '스트라히냐', '필립', '네마냐', '밀린코비치', '타디치', '미트로비치', '밀린코비치사비치', '요비치', '파블로비치', '코스티치', '구델', '블라호비치', '삼마르지치', '라디치', '루키치', '일라치', '미야일로비치', '밀렌코비치', '벨코비치', '바비치', '스파히치', '스토이지치', '믈라데노비치', '페트로비치'],
    '스코틀랜드': ['앤드류', '존', '스콧', '빌리', '라이언', '캘럼', '체', '키어런', '로버트슨', '맥긴', '맥토미니', '길모어', '크리스티', '맥그리거', '아담스', '티어니', '헨드리', '포티어스', '행리', '랄스턴', '테일러', '섕클랜드', '포레스트', '건', '켈리', '클라크', '맥켄지', '잭', '암스트롱'],
    '체코': ['토마시', '파트리크', '블라디미르', '안토닌', '라디슬라프', '다비드', '진드르지흐', '마테이', '소우체크', '시크', '초우팔', '바라크', '크레이치', '지마', '스타네크', '유라섹', '흘로제크', '쿠흐타', '치틸', '프로보드', '숄크', '린그르', '홀레스', '흐라나치', '블체크', '도우데라', '코바르지', '야로시', '초리', '세프치크'],
    '칠레': ['알렉시스', '에두아르도', '벤', '디에고', '에릭', '마르셀리노', '파울로', '기예르모', '산체스', '바르가스', '브레레톤', '발데스', '풀가르', '누녜스', '디아스', '마리판', '브라보', '아리아스', '코르테스', '카탈란', '리크노브스키', '쿠스체비치', '수아소', '이슬라', '에체베리아', '파베스', '오소리오', '다빌라', '볼라도스', '자발라'],
    '페루': ['루이스', '잔루카', '에디손', '안드레', '레나토', '피에로', '카를로스', '아드빈쿨라', '라파둘라', '플로레스', '카리요', '타피아', '기스페', '아브람', '삼브라노', '가예세', '카르바요', '카세스', '코르소', '아라우호', '산타마리아', '칼렌스', '로페스', '카르타헤나', '페냐', '카스티요', '그레레로', '폴로', '레이나'],
    '파라과이': ['미겔', '훌리오', '라몬', '디에고', '마티아스', '구스타보', '오마르', '카를로스', '알미론', '엔시소', '소사', '고메스', '로하스', '구스타보 고메스', '알데레테', '모리니고', '아길라르', '코로넬', '발부에나', '알론소', '벨라스케스', '쿠바스', '비야산티', '페랄타', '카쿠', '로메로', '바레이로', '아르세', '사나브리아', '피타'],
    '베네수엘라': ['살로몬', '예페르손', '탄크레디', '요시아스', '도미닉', '살바도르', '알레한드로', '윌커', '론돈', '소텔도', '마치스', '린콘', '카디스', '페라레시', '나바로', '안헬', '아람부루', '마르티네스', '에레라', '카세레스', '사바리노', '벨로', '안드라데', '세고비아', '코르도바', '라미레스', '로모', '바르호'],
    '볼리비아': ['마르셀로', '하우메', '라미로', '보리스', '루이스', '호세', '기예르모', '카를로스', '모레노', '쿠에야르', '바카', '세스페데스', '하킨', '사그레도', '비스카라', '람페', '알마다', '메디나', '후스티니아노', '토메', '빌라밀', '수아레스', '테르세로스', '알가라냐스', '미란다', '차베스', '로차'],
    '캐나다': ['알폰소', '조나단', '사일', '타존', '스티븐', '알레스테어', '카말', '막심', '데이비스', '데이비드', '라린', '뷰캐넌', '에우스타키오', '존스턴', '밀러', '크레포', '세인트클레어', '비토리아', '코넬리우스', '워터맨', '라리에아', '아데쿠그베', '피에트', '오소리오', '코네', '샤펠버그', '밀러', '밀라', '러셀-로우', '바이어'],
    '파나마': ['아달베르토', '이스마엘', '호세', '안니발', '에릭', '미카엘', '피델', '올란도', '카라스크라', '디아스', '파하르도', '고도이', '데이비스', '아밀카르', '에스코바르', '모스케라', '새미', '안드라데', '밀러', '코르도바', '하베이', '아이라', '마르티네스', '바르세나스', '로드리게스', '바테르', '게레로', '로페스', '메히아', '칼데론'],
    '자메이카': ['레온', '미카일', '데머레이', '바비', '이단', '조엘', '이선', '안드레', '베일리', '안토니오', '그레이', '데코르도바-리드', '피녹', '라티보디에르', '블레이크', '나이트', '로프', '헥터', '마리아파', '로렌스', '벨', '렘비키사', '버나드', '팔머', '존슨', '윌리엄스', '체', '시어러', '와이트', '스미스'],
    '아랍에미리트': ['알리', '파비오', '카이오', '하리브', '타흐눈', '칼리드', '할리드', '맙쿠트', '리마', '카네도', '압달라', '알자아비', '살민', '하셰미', '에이사', '라시드', '하마드', '압둘라', '압바스', '이드리스', '하산', '라만', '수하일', '살레', '나데르', '야히아', '알가사니'],
    '오만': ['사라', '자밀', '이삼', '압둘라', '하리브', '아흐메드', '칼리드', '파이즈', '알야흐야에이', '알야흐마디', '알소브히', '알파와지', '알사디', '알카비', '알브라이키', '알루셰이디', '알하브시', '알무크발리', '알가사니', '알라비', '알샤야디', '알하미디', '알무살라미', '알카미시', '알부사이디', '알샤와리', '알하이리'],
    '태국': ['티라실', '차나팁', '수파촉', '수파낫', '티라톤', '사라치', '파티왓', '엘리아', '당다', '송크라신', '사라차트', '무에안타', '분마탄', '요엔', '캄마이', '돌라', '미켈손', '수파차이', '위라텝', '보딘', '차로나삭', '루앙탄게이', '비어', '당다', '티라실', '송크라신', '사라차트', '무에안타', '분마탄'],
    '베트남': ['응우옌 티엔 린', '응우옌 꽝 하이', '응우옌 반 토안', '팜 뚜안 하이', '응우옌 호앙 둑', '도 훙 중', '부 반 탄', '당 반 람', '응우옌 꽁 푸엉', '부이 호앙 비엣 안', '응우옌 탄 빈', '판 반 도', '응우옌 뚜안 안', '쿠앗 반 캉', '부 반 부', '조 응옥 하이', '응우옌 필립'],
    '코트디부아르': ['프랭크', '세바스티앙', '니콜라', '윌프리드', '세코', '이브라힘', '오딜론', '야햐', '케시에', '할레', '페페', '자하', '포파나', '상가레', '코수누', '그라델', '보가', '디아키테', '아딩그라', '쿠아메', '코네', '볼리', '은디카', '디오망데', '오리에', '싱고', '바요', '그보우오'],
    '말리': ['이브스', '아마두', '하이다라', '지기', '하마리', '팔라예', '엘 빌랄', '무사', '비스마', '하이달라', '트라오레', '디아라', '사코', '사마세쿠', '투레', '두쿠레', '포파나', '디아니', '디아비', '시나요코', '무사', '말레', '코네', '시소코', '디앙', '캄보', '트라오레', '둠비아'],
    '남아프리카공화국': ['퍼시', '테보호', '롬포', '론웬', '타펠로', '오브리', '템바', '에비던스', '타우', '모코에나', '케카나', '윌리엄스', '모레나', '모디바', '즈와네', '맥고파', '레핀카', '모디세', '자일리', '므발라', '시비시', '무다우', '자이', '아담스', '시톨레', '모냐네', '고시', '모토아', '마요', '부테레지'],
    '뉴질랜드': ['크리스', '리베라토', '마르코', '새르프리트', '타일러', '조', '마이클', '올리버', '우드', '카카체', '스타메닉', '싱', '보이드', '벨', '박스올', '세일', '마리노비치', '페인', '스미스', '투이로마', '빈돈', '가베트', '하우이슨', '루이스', '저스트', '맥코왓', '베인', '마타'],
    '노르웨이': ['엘링', '마르틴', '알렉산데르', '외르얀', '레오', '율리안', '크리스티안', '산데르', '홀란드', '외데고르', '쇠를로트', '닐란', '외스티고르', '뤼에르', '토르스트베트', '베르게', '누사', '라르슨', '도눔', '보브', '파트릭', '아이에르', '하네올센', '울프', '페데르센', '셀비크', '디엔겔란드', '비에르칸', '구드문드손', '엘윤누시'],
    '그리스': ['콘스탄티노스', '요르고스', '아나스타시오스', '디미트리스', '반젤리스', '오디세아스', '파나요티스', '라자로스', '치미카스', '마브로파노스', '마수라스', '바카세타스', '펠카스', '파블리디스', '블라호디모스', '레츠오스', '하치디아코스', '쿨리에라키스', '로타', '야눌리스', '시오피스', '쿠르벨리스', '부할라키스', '만탈로스', '초리스', '이오아니디스', '야쿠마키스', '파스찰라키스', '초라키스', '바지아니디스'],
    '루마니아': ['라두', '데니스', '라즈반', '니콜라에', '에두아르드', '안드레이', '플로린', '호라치우', '드라구신', '만', '마린', '스탄추', '이아니스', '부르커', '코만', '몰도반', '니처', '알리베크', '푸스카스', '미하일라', '치클더우', '수트', '라코비찬', '네델체아루', '루스', '방쿠', '모고스', '타르노바누', '미트리차', '더글라스'],
    '슬로바키아': ['밀란', '스타니슬라프', '온드레이', '유라이', '로베르트', '다비드', '마르틴', '마레크', '슈크리니아르', '로보트카', '두다', '쿠츠카', '보제니크', '한츠코', '두브라브카', '함시크', '로닥', '바브로', '오베르트', '기욤베르', '페카리크', '리네티', '베네스', '베로', '하라스린', '수슬로프', '사우어', '스트렐레츠', '듀리스'],
    '아일랜드': ['에반', '셰이머스', '네이선', '맷', '조시', '제이슨', '패스칼', '퀴빈', '퍼거슨', '콜먼', '콜린스', '도허티', '쿨런', '나이트', '오모바미델레', '켈레허', '바주누', '트래버스', '오셰이', '스케일스', '브래디', '스몰본', '몰룸비', '오그베네', '존스턴', '아이다', '스모디치', '패럿', '맥클린'],
    '핀란드': ['테무', '루카스', '요엘', '글렌', '라스무스', '리하르트', '로베르트', '예레', '푸키', '흐라데츠키', '포흐얀팔로', '카마라', '슐러', '옌센', '이바노프', '우로넨', '요로넨', '시니살로', '배이새넨', '호스코넨', '알호', '니스카넨', '펠톨라', '수호넨', '안트만', '로드', '셸만', '포르스', '텐호', '갈베스'],
    '조지아': ['흐비차', '게오르기', '구람', '주리코', '자바', '오타르', '라샤', '기오르기', '크바라츠헬리아', '맘마다슈빌리', '카시아', '다비타슈빌리', '칸카바', '카카바제', '드발리', '차크베타제', '로리아', '구게샤슈빌리', '크베르크벨리아', '로초슈빌리', '고초레이슈빌리', '그벨레시아니', '지브지바제', '크빌리타이아', '미카우타제', '코초라슈빌리', '크베크베스키리', '알투나슈빌리', '메크바비슈빌리', '치타이슈빌리'],
    '알바니아': ['레이', '베라트', '엘세이드', '크리스찬', '네디미', '아르만도', '토마스', '에트리트', '마나이', '짐시티', '히사이', '아슬라니', '바이라미', '브로야', '스트라코샤', '베리샤', '카스트라티', '아예티', '미타이', '발리우', '이사마일리', '쿰불라', '알리치', '라마다니', '자술라', '무치', '호자', '세페리', '다쿠'],
    '북마케도니아': ['엘리프', '에니스', '에즈잔', '스톨레', '보얀', '다르코', '티호미르', '알렉산다르', '엘마스', '바르디', '알리오스키', '디미트리에프스키', '미오프스키', '벨코프스키', '코스타디노프', '트라이코프스키', '시스코프스키', '일리에프', '무슬리우', '자이코프', '세라피모프', '일랴조프스키', '디모스키', '아타나소프', '엘레지', '알리미', '바부스키', '추를리노프', '리스토프스키'],
    '아이슬란드': ['길피', '요한', '하콘', '오스카', '빅토르', '아르노르', '이삭', '룬나르', '시구르드손', '구드문드손', '하랄드손', '오스카르손', '팔손', '트라우스타손', '요하네손', '룬나르손', '발디마르손', '올라프손', '인기손', '헤르만손', '핀손', '샘스테드', '토르스테인손', '구드욘센', '빌룸손', '엘레르트손', '마그누손', '프리드리크손'],
    '몬테네그로': ['스테반', '스테판', '아담', '마티야', '니콜라', '마르코', '이고르', '밀란', '요베티치', '사비치', '마루시치', '샤르키치', '크르스토비치', '베치라이', '부야치치', '미야토비치', '페트코비치', '드라고예비치', '라드노비치', '투치', '바크라치', '얀코비치', '바키치', '요보비치', '바사', '무고샤', '하크샤바노비치', '차마이', '불라토비치'],
    '북아일랜드': ['조니', '자말', '코너', '셰어', '트래이', '알리', '패디', '베일리', '에반스', '루이스', '브래들리', '찰스', '흄', '맥캔', '맥네어', '피콕페렐', '사우스우드', '하자드', '발라드', '토아르', '스펜서', '사빌', '톰슨', '프라이스', '마샬', '레이버리', '리드', '마겐니스', '테일러', '던리'],
    '이스라엘': ['마카비', '하포엘', '에란', '타미르', '베니', '오렌', '오르', '기드온', '카쿠나', '아타트', '에야', '요아브', '이타이', '샤론', '옴리', '가이', '에이탄', '아비', '노아', '요나탄', '아단', '로넨', '아리', '길', '오메르', '아미르', '니르', '아탄', '요르암', '이갈'],
    '불가리아': ['키릴', '필립', '발렌틴', '일리야', '게오르기', '이바일로', '안톤', '디미타르', '데스포도프', '크라스테프', '안토프', '그루에프', '밀라노프', '초체프', '네디알코프', '미토프', '페트로프', '리스코프', '포포프', '투리초프', '유세인', '코스타디노프', '일리에프', '파나요토프', '디미트로프', '코롤레프', '요모프', '이바노프', '보루코프'],
    '룩셈부르크': ['다넬', '제르송', '레안드로', '크리스토퍼', '로랑', '안소니', '맥심', '시나니', '로드리게스', '바레이로', '마틴스', '얀스', '모리스', '샤노', '마르티네스', '올레센', '루필', '보르헤스', '쿠르치', '티엘', '보니', '플로리안'],
    '아르메니아': ['헨리크', '에두아르드', '바한', '티그란', '바라즈다트', '루카스', '노르베르토', '오그넨', '미키타리안', '스페르치안', '비차흐치안', '바르세기안', '하로얀', '젤라라얀', '브리아스코', '찬차레비치', '베글라리안', '부치네프', '가스파리안', '보스카니안', '므크르치안', '티크니지안', '호바니시안', '다시안', '이우', '이반', '세로비안', '미라니안', '샤고얀'],
    '벨라루스': ['비탈리', '일리야', '막심', '알렉산드르', '데니스', '세르게이', '에브게니', '파벨', '리사코비치', '슈쿠린', '스카비시', '하르바치크', '에본', '폴랴코프', '키릴', '폴리테비치', '밀카', '볼코프', '유젭추크', '플로트니코프', '쿠드라베츠', '라프테프', '바하르', '사비츠키', '클리모비치', '코발레프', '그레치호', '보체로프', '마르티노비치', '파블로베츠'],
    '몰도바': ['이온', '비탈리에', '바딤', '베체슬라프', '아르투르', '올레그', '도리안', '알렉세이', '니콜라에스쿠', '도마스칸', '라차', '포스마크', '이오니차', '레아브치우크', '라이리안', '코셸레프', '셀라드니크', '크래춘', '무드라크', '마란디치', '플래티카', '코조카루', '모츠판', '카이마코프', '포스토라키', '보가추크', '스치나', '클레셴코', '레벤코', '요산'],
    '리투아니아': ['페도르', '아르비다스', '유스타스', '에드가라스', '그비다스', '파울리우스', '리나스', '에밀리우스', '체르니흐', '노비코바스', '라시카스', '우트쿠스', '지네이티스', '골루비츠카스', '메겔라이티스', '주바스', '게르트모나스', '체르니아우스카스', '시르비스', '기르드바이니스', '레키아타스', '클리마비치우스', '보로브스키스', '슬리브카', '체르니스', '시몬쿠스', '파울라우스카스', '쿠시스', '세스플라우스키스'],
    '라트비아': ['야니스', '블라디슬라프', '로베르츠', '라이비스', '안드레아스', '카스파르스', '알렉세이스', '파벨스', '이카우니에크스', '구코프스키스', '울드리키스', '유르코프스키스', '치가니크스', '듀브라', '사발니에크스', '슈테인보르스', '마트레비치스', '스투글리스', '체르노모르디스', '발로디스', '토보르스', '사벨리에프스', '야운젬스', '다스케비치스', '카메시스', '레길라', '크롤리스', '오쉬스', '소로킨스'],
    '에스토니아': ['콘스탄틴', '헨리', '카롤', '요나스', '라그나르', '마르텐', '칼', '아르투르', '화시예프', '아니에르', '메츠', '태미', '클라반', '쿠스크', '헤인', '픽', '이곤옌', '발네르', '파스코치', '시니야프스키', '바실예프', '포옴', '소오메츠', '셰인', '미니야', '사피넨', '소르가', '레피크', '유르겐스', '투유'],
    '안도라': ['일데폰스', '마르시오', '마크', '예수스', '루비오', '막스', '이케르', '리마', '비에이라', '발레스', '푸욜', '요베라', '알바레스', '산 니콜라스', '가르시아', '레베스', '알라에스', '페르난데스', '쿠쿠', '마르티네스', '고메스', '피레스'],
    '산마리노': ['안디', '마테오', '다닐로', '필리포', '단테', '니콜라', '엘리아', '알도', '셀바', '비타이올리', '리날디', '베라르디', '로시', '난니', '베네데티니', '시몬치니'],
    '요르단': ['무사', '야잔', '마흐무드', '누르', '에산', '알리', '타레크', '야지드', '알타마리', '알나이마트', '알마르디', '알라왑데', '하다드', '올란', '하티브', '아부 라일라', '다르도르', '샤라라', '사미르', '하산', '사데', '하와리', '아얄렝', '나시브', '마리', '디브', '아부 할릴라'],
    '바레인': ['압둘라', '알리', '카밀', '모하메드', '왈리드', '아흐메드', '사예드', '이브라힘', '유수프', '마단', '알아스와드', '마르훈', '알하얌', '부감마르', '자파르', '루트팔라', '압둘라티프', '후마이단', '샤이호', '알하지아', '다야', '알샤무산', '벤아디', '바게르', '아델', '칼라스'],
    '인도네시아': ['라파엘', '라그나르', '마르셀리노', '위탄', '톰', '이바르', '리즈키', '에르난도', '스트라위크', '오라트망고엔', '페르디난', '술레만', '하예', '젠너', '리도', '아리', '아스나위', '아르한', '월시', '이체스', '아마트', '후브너', '네이단', '사유리', '자나카'],
    '말레이시아': ['아리프', '파이살', '로멜', '대런', '디오구', '사파위', '샤룰', '시한', '아이만', '할림', '모랄레스', '록', '조주', '라시드', '사드', '하즈미', '엔드릭', '샤메르', '윌킨', '브렌단', '디온', '코르빈-옹', '데이비스', '탄', '시한'],
    '싱가포르': ['이한', '일한', '이르판', '샤왈', '송의영', '하리스', '사푸완', '하산', '판디', '아누아르', '하룬', '바하루딘', '써니', '샤흐', '스튜어트', '쿠마르', '말러', '클리포드', '첸', '하룬'],
    '몽골': ['발진냠', '난딘-에르데네', '체덴발', '무른', '오윤바타르', '체렝바트', '엥흐자르갈', '아리운볼드', '바트볼드', '후렐바타르', '투무르오치르', '알탄수흐', '미지돌지', '바야르자르갈', '창요오', '바트사이한', '엥흐빌레그', '냥-오소르', '푸레브도르지', '바트-에르데네', '엥흐타이반', '자르갈사이한', '수흐바타르', '도르지', '도르지데렘', '바야스갈랑', '체렝바야르', '간볼드', '엥흐바야르', '바트사이한'],
    '홍콩': ['맷 오르', '탄춘록', '웡와이', '헬리오', '리이센', '얍훙파이', '체체센', '에벨톤', '수니치', '조우', '로', '챈', '유', '와이', '람', '은가오', '포', '로', '주', '폴', '훙'],
    '조선민주주의인민공화국': ['정일관', '한광성', '최주성', '리형진', '김국범', '장국철', '강주혁', '신태성', '박광룡', '리은철', '백충성', '문인주', '김유성', '안병준', '박현일', '명차현', '최옥철', '리영직', '김경훈', '조광명', '안대성', '리명국'],
    '피지': ['로이', '세타레키', '토마시', '나렌드라', '키티오네', '사미우라', '일리소니', '아쿠일라', '크리슈나', '휴스', '포니바카', '라오', '에랄라', '나부카', '로고와카', '마타이수바', '테마', '나이두', '투이부나', '와스', '베레보우', '라드리고', '와니가라', '쿠마르', '두나다무', '스미스', '알리', '라라발라부', '카타이'],
    '슬로베니아': ['얀', '벤야민', '요시프', '안드라주', '야카', '티미', '페타르', '비드', '오블락', '셰시코', '일리치치', '슈포라르', '비욜', '엘슈니크', '스토야노비치', '벨렉', '베드니흐', '블라지치', '드르쿠시치', '첼라르', '발코베츠', '카르니치니크', '브레칼로', '로브리치', '쿠르티치', '호르바트', '믈라카르', '비포트니크', '일리치'],
    '카자흐스탄': ['바흐티요르', '누랄리', '아스하트', '막심', '알렉산드르', '얀', '이고르', '람라잔', '자이누트디노프', '알리프', '태기베르겐', '사모로도프', '마로치킨', '보로고프스키', '샤츠키', '오라조프', '자루츠키', '샤이자다', '말리', '비스트로프', '에를라노프', '가비셰프', '베이세베코프', '쿠아트', '이슬람한', '체스노코프', '아스타노프', '아임베토프'],
    '부르키나파소': ['베르트랑', '에드몬드', '당고', '이사', '블라티', '에르베', '구스타보', '라시나', '트라오레', '탑소바', '와타라', '카보레', '투레', '코피', '상가레', '밴드', 'ou에드라오고', '바야라', '구이라', '니키에마', '네비에', '사누', '바소레', '데이수', '야고', '나갈로', '코난'],
    '케냐': ['마이클', '빅터', '조셉', '에릭', '아모스', '클리프', '패트릭', '조마', '올룽가', '완야마', '오쿠무', '오우마', '냥가', '미헤소', '마타시', '오몰로', '티모베', '마시카', '무구나', '아부드', '오마르', '오치엔', '마라가', '카릴', '뮤타이', '오티에노', '오몬디', '오디암보'],
    '앙골라': ['젤송', '지투', '마부룰루', '프레디', '쇼', '지니', '발두', '네를루', '달라', '루부보', '밀송', '벨라', '길베르투', '파프', '에스트렐라', '부아투', '발두', '아폰수', '카르모', '포르투나', '네를루', '지니', '네투', '네블루', '안토니오'],
    '잠비아': ['패트손', '패션', '킹스', '스토필라', '에녹', '클라투스', '케네디', '루반보', '다카', '사칼라', '캉와', '순주', '무에푸', '찰라', '무시시', '무숨다', '칠루피아', '반다', '카피라', '캡웨', '캄폴리', '체페시', '수투', '물렝가', '카송고'],
    '러시아': ['알렉산드르', '알렉세이', '안톤', '아르툠', '다닐', '드미트리', '이고르', '이반', '콘스탄틴', '막심', '미하일', '니키타', '세르게이', '블라디미르', '골로빈', '미란추크', '자하랸', '소볼레프', '찰로프', '모스토보이', '바리노프', '쿠자예프', '지키야', '디베예프', '카라바예프', '사포노프', '슈닌', '실랴코프', '포민', '글루셴코'],
    '코소보': ['베다트', '밀로트', '에돈', '플로렌트', '베르산트', '발론', '아르베르', '메르김', '아미르', '피단', '리림', '아리야네트', '무시키', '무를리치', '라시차', '제그로바', '무슬리야', '베리샤', '첼리나', '보이보다', '알리티', '드레셰비치', '크라스니치', '호자', '하데르조나이', '루다니', '로샤이', '라흐마니', '파차라다', '크릴레지우'],
    '아제르바이잔': ['에민', '라밀', '마히르', '레난', '가라', '바흐루즈', '샤흐루딘', '라히드', '마흐무도프', '셰이다예프', '에므레리', '코크추', '가라예프', '다다쇼프', '디니예프', '마마도프', '무스타파예프', '후세이노프', '크리보추크', '메드베데프', '하그베르디', '사파로프', '이사예프', '알리예프', '누리예프', '바이라모프', '발라예프', '마고메달리예프', '아흐메도프', '가시모프'],
    '지브롤터': ['리암', '치포', '리', '에단', '체이스', '초셉', '자이스', '데이얀', '워커', '올리베로', '로넌', '스틱스', '볼팅', '프리스틀리', '바르', '무엘히', '쿨링', '와이즈먼', '치폴리나', '케스치아로', '데 바르', '쿠움베스', '로페스', '헤르난데스', '산토스', '콜잉', '로페즈', '브리토', '모건', '몰트비'],
    '키프로스': ['피에로스', '요안니스', '그리고리스', '하랄람포스', '안드로니코스', '마리오스', '안드레아스', '코스타스', '소티리우', '피타스', '카스타노스', '쿠술로스', '카쿠리스', '일리아', '치오니스', '로이주', '스포야리치', '아르티마타스', '키리아쿠', '라아이피스', '카롤', '안토니우', '이오아누', '파나지오투', '디메트리우', '미하엘', '파나기', '안토니아데스', '고기치', '크리스토포루'],
    '페로 제도': ['클래민트', '요안', '한네스', '할루르', '군나르', '빌요르무르', '오드마르', '뢰그비', '올센', '에드문드손', '아그나르손', '한센', '헨드릭손', '요엔센', '다윗센', '발드빈손', '네External', '바튼하마르', '쇠렌센', '야콥센', '다니엘센', '프레데릭스베르크', '뵤르탈리드', '미켈센', '게스트손', '라임', '유스티누센', '크누센', '추카', '안드레아센'],
    '몰타': ['테디', '루크', '매튜', '주르겐', '테디', '조셉', '엔리코', '샘', '테우마', '감빈', '귀요미에', '음봉', '보르그', '쇼', '무스카트', '카멘줄리', '디메크', '사타리아노', '피사니', '그레치', '아팔라프', '데가브riele', '부하기아르', '부수틸', '페페', '자파', '보넬로', '알타드', '타보네', '크리스텐센'],
    '리히텐슈타인': ['데니스', '아론', '산드로', '옌스', '니클라스', '마르셀', '벤자민', '막시밀리안', '살라노비치', '셀레', '비제르', '호퍼', '프릭', '괴펠', '말린', '볼핑거', '옐레', '마이베르', '하슬러', '에르네', '베첼', '오스펠트', '마르크세르', '트라베르', '에브레', '가스너', '베커', '포페르', '루힝거', '치머만'],
    '모나코': ['루이', '위고', '장', '피에르', '마크', '폴', '뤼카', '앙투안', '카르발류', '마르티니', '비앙키', '로시', '메디치', '페라리', '도메니코', '그리말디', '뒤랑', '르클레르', '모네티', '발렌티니', '가스탈디', '베르토니', '루셀', '뒤부아', '파스토레', '파리시', '폰타나', '클레망', '레나르', '미셸'],
    '이라크': ['아이멘', '모하나드', '이브라힘', '알리', '지단', '아메드', '후세인', '유세프', '후세인', '알리', '바예시', '아드난', '이클발', '야신', '아티야', '하심', '나티크', '술라카', '도스키', '알리', '하산', '탈리브', '자심', '아민', '라산', '파이즈', '카짐', '카림', '후맘', '사드'],
    '시리아': ['오마르', '이브라힘', '마흐무드', '에세키엘', '오마르', '아마르', '파드', '타헤르', '알소마', '알와리', '알마와스', '함', '흐리빈', '라마단', '야센', '유세프', '쿠르다글리', '미도', '우에스', '크루마', '아잔', '알마드', '알라안', '오스만', '에사르', '알마드', '마투크', '아스카르', '아니스', '하즈'],
    '팔레스타인': ['오다이', '타메르', '마흐무드', '자이드', '무사브', '야세르', '라미', '아타', '다바그', '세얌', '와디', '알카바르', '바타트', '하마드', '하마다', '아부알리', '자베르', '하비샤', '살레', '테르마니니', '마요르', '칸틸라나', '주바이다', '파라위', '칼릴', '라시드', '카루브', '이술람', '하산', '나브한'],
    '타지키스탄': ['알리셰르', '샤흐롬', '에흐소니', '파르비존', '아미르베크', '바흐다트', '조이르', '마누체흐르', '잘릴로프', '사미에프', '판샨베', '우마르바예프', '주라보예프', '하노노프', '사파로프', '자릴로프', '라히모프', '야티모프', '바리에프', '나자로프', '이슬로모프', '다블라트미르', '슈쿠로프', '토히로프', '캄로쿨로프', '무하마드존', '사이드조프', '아흐메도프', '카리모프', '보보에프'],
    '키르기스스탄': ['조엘', '굴지깃', '에르네스트', '알리마르단', '오딜존', '발레리', '카이rat', '알렉산드르', '코조', '알리쿨로프', '바티르카노프', '슈쿠로프', '압두라흐마노프', '키친', '지르갈베크', '미샤첸코', '브라우즈만', '코주바예프', '토코타예프', '울루', '사기니바예프', '무르자예프', '아흐메도프', '카니베코프', '베른하르트', '메르크', '샤르셴베코프', '바이라모프', '이슬라쿨로프', '루스타모프'],
    '레바논': ['하산', '바셀', '모하마드', '소니', '다니엘', '오마르', '알렉산더', '무스타파', '마투크', '즈라디', '하이다르', '사드', '라후드', '부가일', '멜키', '마타르', '자인', '알리', '만수르', '사브라', '미셸', '시시', '크라니', '엘 헬웨', '타니치', '아야스', '주리', '안타르', '하미스', '사라야'],
    '쿠웨이트': ['샤바이브', '유세프', '모하메드', '에드', '레다', '아흐메드', '파하드', '할레드', '알할디', '나세르', '다함', '알라셰디', '하니', '알드페에리', '알하지리', '알이브라힘', '하제르', '알에네지', '알카디', '카밀', '알팡이니', '하산', '가리브', '알라흐만', '알무사위', '알술라이만', '알하르비', '사이드', '알무프리', '자이드'],
    '필리핀': ['패트릭', '하비에르', '게리트', '달토', '마니', '케빈', '마이크', '산티아고', '라이헬트', '가요소', '홀트만', '오트', '오트', '잉그레소', '오텐', '아기날도', '뢰트케', '데 무르가', '사토', '에더리지', '멘도사', '한센', '스트라우브', '케케넨', '커란', '란자리', '슈뢱', '영하즈번드', '우드랜드', '디존'],
    '투르크메니스탄': ['아르슬란명', '알티무라트', '엘만', '디다르', '밀란', '메칸', '베흐나자르', '구이치무라트', '아마노프', '안나두르디예프', '타가예프', '두르디예프', '라흐마노프', '사파로프', '바바로프', '안나예프', '호자예프', '할맘메도프', '안나오라조프', '아타예프', '차리예프', '바바로프', '바시모프', '겔디예프', '오라조프', '무하도프', '밍가조프', '치리예프', '야그시예프', '소유노프'],
    '예멘': ['압dul와세', '아흐메드', '가와스', '나세르', '함자', '아니스', '알라', '모함메드', '알마트리', '알사로리', '알마히', '알가흐와시', '한시', '알사시', '알다히', '알보조르', '자람', '만수르', '와지흐', '에사', '하일', '알소라히', '가지', '사이드', '바루이스', '알오마리', '알샤바니', '알하이피', '무나사르', '알라디'],
    '아프가니스탄': ['파이살', '주바이라', '오미드', '무스타파', '라마트', '파트샤', '모하마드', '마지아르', '샤예스테', '아미리', '포팔자이', '아지즈', '아코프', '하이다리', '아호자다', '샤리자', '인샬', '누르', '나자ary', '한', '사다트', '무사비', '자자이', '아민', '메흐르', '니아지', '왈리자다', '무함마드', '파티', '쿠르스탄'],
    '중화 타이베이': ['안이은', '첸팅양', '우춘칭', '야오민솅', '팡칭런', '코유팅', '첸포리앙', '추엔', '산디', '사무엘', '왕루이', '펑샤오치', '원치하오', '리마오', '린창룬', '첸차오안', '바이샤오우', '유치아후앙', '옌허황', '투샤오지에', '량멩신', '팡리포', '황치우린', '판원지에', '시웨이청', '치우위훙', '차오유에', '가오웨이', '린밍웨이', '창친'],
    '몰디브': ['알리', '함자', '아사드하우', '나이즈', '하산', '이브라힘', '이삼', '아흐메드', '아슈파크', '모하메드', '압dul라', '하산', '나이즈', '마후디', '파시르', '와히드', '사무흐', '누만', '시파우', '가니', '파이살', '샤리프', '라시드', '지한', '나시르', '모하메드', '알리', '아미르', '살레', '야민'],
    '미얀마': ['마웅마웅', '아웅', '윈나잉', '얀나잉', '한우', '헤인테트', '쪼민', '탄파잉', '륀', '카웅', '툰', '우', '아웅', '소', '르윈', '양', '나잉', '조', '우', '투', '혓', '아웅', '소', '륀', '쿄', '모', '윈', '민', '테트', '피에'],
    '온두라스': ['알베르트', '안토니', '루이스', '에드윈', '데이비', '데닐', '키오토', '브라이언', '엘리스', '로자노', '팔마', '로드리게스', '플로레스', '말도나도', '멜렌데스', '나하르', '알바레스', '아코스타', '로페스', '메히아', '베가', '가르시아', '벵구체', '카스티요', '아르리아가', '멘히바르', '후루타도', '누녜스', '피네다', '아길라르'],
    '엘살바도르': ['넬슨', '하이에', '브라이언', '다르윈', '알렉스', '나르시소', '크리스티안', '로베르토', '보니야', '엔리케스', '힐', '세렌', '롤단', '오렐라나', '마르티네스', '도밍게스', '클라벨', '사발레타', '타마카스', '로메로', '마리오', '곤살레스', '카르타헤나', '메히아', '푸엔테스', '아르구에타', '테하다', '오소리오', '바스케스', '피네다'],
    '아이티': ['프란츠디', '덕켄스', '프란츠', '칼', '알렉스', '데릭', '리카르도', '조니', '피에로', '나존', '피에르', '생트', '크리스티안', '에티엔', '아데', '프라디', '게리에', '알체우스', '메트루사유', '아르쿠스', '라부제', '플라시드', '듀베르거', '앙투안', '당베르', '도나디외', '캉타브', '장', '클레망', '시몬센'],
    '퀴라소': ['주니뇨', '란첼로', '자이로', '레안드로', '블라디미르', '켄지', '네가External', '안토니', '바쿠나', '얀가', '리데발트', '바쿠나', '쿠코', '마르티나', '호이', '고레', '루아', '가에리', '펠리디아', '카르멜리아', '쿠바스', '룸', '보드아크', '후이세', '조던', '마리아', '세베리나', '카스태니어', '지메르만', '콜론'],
    '트리니다드 토바고': ['리비', '알빈', '레온', '주다', '조빈', '네External', '쉘던', '덴실', '가르시아', '존스', '무어', '하킹', '존스', '하이랜드', '바토', '데이비드', '호지', '윌리엄스', '필립', '스미스', '코르네르', '모레이라', '윈체스터', '텔포드', '램버트', '고메즈', '포춘', '노엘', '폴', '해크쇼'],
    '과테말라': ['루비오', '다르윈', '오스카', '알레한드로', '호르헤', '로드리고', '헤라르도', '니콜라스', '루빈', '롬', '산티스', '가르디도', '아파리치오', '사라비아', '고도이', '핀토', '삼요아', '아르돈', '모랄레스', '헤헤External', '하겐', '헤레스', '멘데스', '카스테야노스', '알타탄', '세바요스', '구에라', '쿠에야르', '베가', '엔리케스'],
    '안티가 바부다': ['마일스', '피터', '조수아', '드숀', '유진', '카렌', '투제이', '자말', '웨스턴', '그리피스', '파커', '보우먼', '키르완', '토마스', '리처즈', '헨리', '벤자민', '조셉', '그린', '코드리턴', '사무엘', '알렌', '브라운', '마틴', '로빈슨', '스티븐스', '프랜시스', '체스터', '제임스', '존'],
    '세인트키츠 네비스': ['해리', '키스External', '로마인', '오마리', '자바리', '요한', '로완', '타이콴', '팬아유투', '스털링', '소이어스', '스털링', '헤이젤', '윌리엄스', '리버드', '메이너드', '테렐', '리차드', '아치발드', '프랭크', '에이머리', '로저스', '스프링거', '새뮤얼', '로버츠', '브랜틀리', '아이작', '미첨', '호지', '스미스'],
    '도미니카 공화국': ['마리아노', '주니어', '도니', '하인츠', '장 카를로스', '에디송', '크리스티안', '루이이', '디아스', '필포', '로메로', '머르첼', '로페스', '아스코나', '가르시아', '데 페냐', '바에스', '누녜스', '레예스', '벨트레', '산토스', '발데스', '로드리게스', '피멘텔', '펠리스', '헤르난데스', '로사리오', '우레냐', '구스만', '로렌소'],
    '니카라과': ['하이메', '마티아스', '후안', '아리안드', '비론', '조수에', '후안 파블로', '밀톤', '모레노', '베일리', '바레라', '보네', '보네', '키한오', '플로레스', '메디나', '푸엔테스', '코펜스', '아세베도', '구티에레스', '포르카리', '로페스', '폰세카', '테야스', '라요', '코로네스', '로드리게스', '모랄레스', '몬카다', '스미스'],
    '버뮤다': ['나키', '단테', '레External', '윌리엄', '레지', '제키', '존', '코리', '웰스', '레버록', '미첼', '화이트', '달람', '루이스', '클레멘트', '스미스', '민스', '터커', '에반스', '바스콤', '도나와', '스완', '시몬스', '버터필드', '브라운', '램', '로빈슨', '디실바', '하비', '라이트'],
    '세인트루시아': ['자밀', '도미닉', '커트', '말릭', '레스터', '멜빈', '샤반', '그렉', '조셉', '포레우스', '프레데릭', '생트 레미', '조셉', '돈세이', '폴', '데니얼', '발레스', '헨리', '피에르', '토마스', '찰스', '에마누엘', '존', '바티스트', '마이어스', '루이스', '니콜라스', '페르디난드', '개브리엘', '제임스'],
    '그레나다': ['자말', '세이던', '레이건', '크리스탈', '아서', '리카르도', '조슈아', '카이', '찰스', '루이스', '미첼', '벨폰', '피에르', '존', '찰스', '윌리엄스', '베이트슨', '프랭크', '토마스', '테오도르', '패터슨', '랑게인', '필립', '노엘', '가르시아', '브라운', '로버츠', '조셉', '베니', '마르티노'],
    '몬트세랫': ['라일', '라일', '네External', '브랜든', '도니', '제임스', '마이클', '크레이그', '테일러', '포스터', '다이어', '바External', '리치웨이', '윌리엄스', '브래드쇼', '그린', '미드', '커크패트릭', '볼튼', '루이스', '스미스', '클라크', '로빈슨', '존스', '우드', '헨리', '브라운', '아치발드', '앨런', '토마스'],
    '푸에르토리코': ['제레미', '헤랄도', '조엘', '시드니', '데빈', '윌프레도', '이안', '카를로스', '데 레온', '디아스', '버르고스', '리베라', '베가', '파도바', '니에베스', '안토네티', '수아레스', '로페스', '칼데론', '곤살레스', '산체스', '카르도나', '리오스', '라모스', '마르티네스', '가르시아', '토레스', '로페즈', '호르헤', '크루스'],
    '세인트빈센트 그레나딘': ['오렉스', '차벨', '코넬리우스', '아짐', '테빈', '나잘', '도리안', '자말', '앤더슨', '커닝햄', '스튜어트', '맥보우', '슬레이터', '샘플', '햄릿', '프랜시스', '마샬', '요크', '에드워즈', '존', '토마스', '윌리엄스', '로버츠', '찰스', '제임스', '조셉', '피에르', '브라운', '스미스', '데이비스'],
    '바베이도스': ['티에리', '할람', '조던', '나이얼', '커티스', '마리오', '라샤드', '키숀', '게일', '호프', '해리스', '리드', '하우', '윌리엄스', '줄스', '아타드', '스미스', '그리피스', '프라모드', '사이드', '블랙맨', '새뮤얼', '킹', '브라운', '토마스', '트로트먼', '브래스웨이트', '모리스', '해우드', '라일'],
    '도미니카 연방': ['줄리안', '트래비스', '브레이드', '채드', '아네트', '돈', '오드리', '클레온', '웨이드', '조셉', '버트rand', '토마스', '필립', '샤비에르', '찰스', '조지', '워커', '프레데릭', '라빌레', '펠릭스', '니콜라스', '로빈슨', '브라운', '스미스', '피에르', '로버츠', '존', '로렌스', '마틴', '헨리'],
    '케이맨 제도': ['마이클', '조나단', '콜비', '레이한', '엘리야', '크리스토퍼', '카메론', '매튜', '마르틴', '에뱅크스', '세이모르', '두발', '하이드', '웹', '리브스', '스미스', '우드', '로빈슨', '잭슨', '스코트', '밀러', '파월', '존슨', '데이비스', '토마스', '윌리엄스', '클라크', '브라운', '조지', '알렌'],
    '아루바': ['월터', '테렌스', '글렌버트', '호비', '하비에르', '에릭', '디온', '릭', '베네티에', '흐루텐후트', '크루덴', '치노 아 로이', '히메네스', '산토스', '콰드랜드', '펠레그림', '구에바라', '알베르토', '고메스', '마르티스', '리차드슨', '가르시아', '로페스', '헤르난데스', '트롬프', '크루스', '호지', '브라운', '더글라스', '헨리'],
    '솔로몬 제도': ['라파엘', '작카리아', '미카', '가기', '알빈', '에이턴', '자리', '존', '레아이', '카우아', '레아알라파', '수리', '후External', '호우', '바라', '타니토', '오로가이', '나워', '포일라', '카투아', '보소', '도니', '알릭', '와에타', '토티', '피피', '카우마', '켈레시', '마에우', '마니'],
    '뉴칼레도니아': ['조르주', '베르트rand', '로이', '시자르', '세드릭', '티티', '장 마르크', '에밀', '고페 페네페이', '카이', '와우카', '제올레', '산손', '나우네', '바이라', '데크레', '웨카', '아탈레', '마투', '살로몬', '카라', '루메', '이와', '베아루네', '조세포', '니에카', '사이코', '하우에코', '푸에다', '포포우'],
    '타히티': ['테아온이', '알방', '마타티아', '티아우레이', '마누아리', '에디', '마나리', '타우히티', '테하우', '루시부', '파마', '파티아', '부레바레', '케크', '로랑', '바이야르', '티니라우아리', '테하우', '데가에', '비비안', '아라니', '차우', '포로니', '와진니', '마우', '시용', '라바스테', '티아티아', '와미에', '헤이타라'],
    '파푸아뉴기니': ['토미', '레이몬드', '데이비드', '알윈', '엠마누엘', '콜루', '펠릭스', '나이절', '세미', '구넴바', '브라운', '코모롱', '시몬', '켑포', '포이', '다바니', '사블란', '카부', '야푸', '레파니', '티가나', '요기', '포스터', '헤일로', '조아', '라니', '찰리', '아우사', '바라', '엠손'],
    '바누아투': ['본지', '미치', '아잘레아', '토니', '제이슨', '브라이언', '조나단', '크리스토퍼', '칼로', '칠리아', '쿠트', '나타우', '사카마', '타산', '이와이', '탕기스', '몰리', '바투', '웰웰', '로로', '레넬', '필립', '피에르', '아루', '제임스', '에릭', '프레드', '우디', '조셉', '토마스'],
    '사모아': ['라파엘', '자비스', '존', '사무엘', '앤드류', '헨리', '루크', '윌리엄', '토아', '필리오', '마로', '세투', '폴라타이바오', '모하마드', '해밀턴', '레이놀즈', '말론', '테일러', '알라티니', '추손', '사이먼', '가오페', '마르코', '빌라미', '투일라에파', '살라메아', '로마노', '마이클', '조셉', '스미스'],
    '아메리칸사모아': ['라민', '하이메', '찰스', '루카', '로이', '다니엘', '조던', '마크', '오티에', '헤레라', '미첼', '타우아누우', '발라', '새뮤얼', '로저스', '아파타이', '펠레', '티오아', '시나파티', '마이아바', '솔로모나', '레알라피', '투아', '풀루', '페니', '라투', '파아티가', '시아메레', '타마세세', '포노티'],
    '쿠크 제도': ['테일러', '헤럴드', '그로버', '아루아', '리', '안토니', '파울', '자카리아', '사게비', '하몬', '비치', '마로아', '하몬', '티호레', '사무엘라', '에드워즈', '카루이', '마누', '헨리', '테이lor', '윌리엄스', '스미스', '맥도날드', '로버츠', '마틴', '존슨', '클라크', '킹', '데이비스', '로빈슨'],
    '수리남': ['셰랄도', '미첼', '글레이오필로', '히에로니무스', '티자니', '디오니', '케빈', '샤킬', '베커', '테 브레데', '블레이터', '블라이터', '레이단', '말론', '주아르든', '핀나스', '간테', '도르커', '단케를루이', '마르실리아', '콜와익', '한스', '아베나', '코르트람', '미시잔', '요제프존', '알베르크', '하우프', '체리', '피나스'],
    '가이아나': ['옴ari', '샘', '엘리엇', '칼럼', '네External', '케디아', '리엄', '카디엘', '글래스고', '콕스', '본즈', '하리어트', '고든', '다니엘', '무어', '존스', '매켄지 네External', '듀크 맥케나', '윌슨', '로버츠', '로빈슨', '스미스', '찰스', '웰시', '도버', '넬슨', '프레이저', '토마스', '잭슨', '바넷'],
    '쿠바': ['오니엘', '마이켈', '루이스', '다이론', '야스니엘', '요산디', '호르헤', '카를로스', '헤르난데스', '레예스', '파라델라', '에스피노', '마토스', '코랄레스', '바스케스', '피에드라', '모레혼', '포소', '칼보', '로페스', '페레스', '알바레스', '카사노바', '호르헤', '산체스', '로드리게스', '에르난데스', '고메스', '마르티네스', '가르시아'],
    '카보베르데': ['베베', '하얀', '가리', '자밀로', '더치', '로니', '스토피라', '로베르토', '멘데스', '가리 로드리게스', '몬테이로', '티바', '타바레스', '로페스', '보르헤스', '피코', '페르난데스', '안드라데', '두아르테', '세메도', '수아레스', '포르테스', '산투스', '보즈니치', '바렐라', '테이셰이라', '고메스', '로페즈', '피나', '디아스'],
    'DR콩고': ['세드리크', '요안', '샤르셀', '사무엘', '가엘', '테오', '아르튀르', '리External', '바캄부', '위사', '캉셀라', '무투사미', '카쿠타', '봉공다', '마쉬아크', '음벰바', '칼룰루', '바투빈시카', '이나onga', '음파시', '베르토', '시아디', '메샤크', '마예External', '카옘베', '무투아라', '음풀루', '방자', '음보카니', '볼라시에'],
    '기니': ['세루', '모하메드', '나비', '일리아스', '아기부', '아마두', '무크타르', '모리', '기라시', '바요', '케이타', '이치아레', '카마라', '디아와라', '디알로', '시야', '디아키테', '존테', '실라', '디아카비', '소', '콘테', '카마라', '코네', '카네', '상코', '길라보기', '시세', '트라오레', '방구라'],
    '가봉': ['피에르 에메릭', '데니스', '아론', '구일로르', '겔로르', '마리오', '안토니', '브루노', '오바메양', '부안가', '부펜자', '캉가', '레미나', '오요노', '에쿠엘레 망가', '무케투 무수사', '포코', '음파', '은동', '오비앙', '삼비사', '음파', '아메External', '응우에마', '바비카', '도아', '바야노', '미예 External', '은제', '누비'],
    '우간다': ['파하드', '에마누엘', '밀톤', '트래비스', '할리드', '보비스', '하지', '티모시', '바요', '오스위', '카리사', '무티아바', '아우초', '뵤루한가', '카요External', '아와니', '무가비', '루왈리와', '와티에가', '알리온지', '루쿠와고', '카토', '셈파투', '센탐무', '세루와다', '세캉고', '무카와', '오켈로', '미야', '마투부'],
    '베냉': ['스티브', '조델', '마르셀린', '주니어', '수쿠', '마테오', '세드릭', '올리비에', '무니에', '도수', '쿠포', '올라탄', '도수', '아를린', '하운토니', '우External', '베르동', '아데논', '치보조', '티자니', '두쿠', '알라가베', '단두누', '기욤', '아틀레', '호운토니', '세시오', '아구누', '조르주', '파시누'],
    '모리타니': ['아부바카르', '헤메야', '아부바카리', '이드리사', '게스External', '보드다', '노아', '라미네', '카마라', '탄지', '코이타', '티암', '포파나', '엘 하센', '바', '니아스', '아베이드', '알리 아베이드', '델라히', '디아우', '은디아예', '음바케', '쿠야테', '하우비브', '케타', '디아라', '소우', '바', '사르', '아민'],
    '마다가스카르': ['하키안', '치아나', '라얀', '마르코', '로아리오', '로맹', '토마', '제롬', '안드리아미치노', '라코토하리말라라', '라벨로손', '일라이마하리트라', '아마다', '메타니르', '모렐', '퐁테', '라자피자토니', '라코토니리나', '안드리아', '보아', '하파엘', '장', '베르트rand', '라두', '니리나', '미셸', '루이', '폴', '다비드', '앙리'],
    '남수단': ['티토', '바렌티노', '피터', '라시드', '윌리엄', '임마누엘', '다니엘', '조셉', '오켈로', '유엘', '메이커', '토고', '아쿠에이', '로키', '피에르', '무투르', '다타', '루알', '촐', '가디인', '다니엘', '젠마', '파울로', '벤자민', '루아크', '아구에르', '마욜', '울프', '말렉', '뎅'],
    '나미비아': ['피터', '디온', '프린스', '압살롬', '벤External', '페트루스', '리오', '라이언', '샬룰릴레', '호토', '티지에자', '임본디', '시템비', '냐무카', '시바제', '하우무', '카투아', '카산제', '하니암', '무제우', '카마투카', '룸푸디', '캄뵤루', '프란스', '엠바하', '카자푸아', '로이드', '에라스무스', '네External', '파울루스'],
    '기니비사우': ['마마', '피케티', '자키External', '달치오', '모레토', '알파', '오파', '마르셀로', '발데', '실바', '조르주', '고메스', '카사마', '세메도', '상간테', '잘로', '망데', '칸데', '나누', '카마라', '엔카다', '디알로', '멘디', '고메즈', '호드리게스', '마네', '발데', '자키 External', '토니', '치코'],
    '콩고공화국': ['가이', '실베레', '프린스', '가이우스', '앙투안', '브래들리', '브라이언', '크리스토퍼', '음벤자', '간불라', '이바라', '마쿠타', '마쿠앙투안', '로코', '마옌보', '포바', '은디External', '마푸타', '마통도', '마상가', '비푸마', '오보External', '마풀루', '은조이', '은구아카', '디카무나', '이투아', '무티아바', '바카', '모지'],
    '브루나이': ['아디', '라지미', '나지브', '샤피에', '하케메', '아즈완', '파이크', '샤프완', '사이디', '람리', '타리프', '에펜디', '야지드', '알리', '볼키아', '라만', '하산', '가니', '오스만', '술레이만', '살레', '이샤크', '다우드', '이마무딘', '타니', '함자', '아미루딘', '바사르', '아리프', '나시르'],
    '지부티': ['사무엘', '마하디', '아흐메드', '함자', '알리', '푸아드', '다우드', '모하메드', '아킨누', '마하메드', '엘미', '이드리스', '와이스', '모하메드', '와르사마', '압디', '파라', '바르카드', '사이드', '하산', '다히르', '자마', '오스만', '게디', '리반', '무세', '구External', '이브라힘', '유수프', '빌랄'],
    '파키스탄': ['오티스', '이툼', '하룬', '샤익', '알람', '압dul', '우메이르', '유수프', '칸', '아흐메드', '하미드', '레만', '가니', '샤', '사이드', '알리', '카르디', '하지', '술레이만', '바시르', '나비드', '이크발', '아사드', '라자', '마흐무드', '자파르', '라티프', '하산', '지아', '야쿠브'],
    '세이셸': ['브랜든', '라이언', '칼럼', '디노', '벤자민', '워런', '콜린', '엘리야', '라브로스', '앙리에트', '멜라니', '수잔', '파이에트', '리마', '마리', '에스더', '로렌스', '몬나이', '드 자크', '카스External', '스미스', '클라크', '로빈슨', '찰스', '존스', '테일러', '브라운', '윌리엄스', '조셉', '토마스'],
    '동티모르': ['주앙', '무알로', '구테레스', '루피노', '일리두', '엘리오', '프레기우', '알메이다', '다 실바', '페레이라', '소아레스', '도스 산토스', '가르시아', '멘도사', '피레스', '코스타', '마르틴스', '호드리게스', '히베이루', '로페스', '프레이타스', '올리베이라', '핀투', '누네스', '카르발류', '테이셰이라', '고메스', '안드라데', '수아레스', '바렐라'],
    '통가': ['헤마External', '모알라', '라투', '카이타푸', '팔라빌라', '킬리피', '피피타', '폴로빌리', '폴로빌리', '투이페External', '파케페External', '피페', '카우푸시', '바이External', '모아', '팔레', '피나우', '리코', '카이루아', '우하타페', '타우파', '마피', '빌라미', '우아세레', '팡가이', '헤마 External', '타우에파페', '라우시', '펠레키', '투포우'],
    '소말리아': ['후세인', '시아드', '이스마엘', '사디크', '모하메드', '압디', '파라', '이브라힘', '모하메드', '압디', '엘미', '샤르마르케', '아흐메드', '오스만', '하산', '자마', '유수프', '가라드', '게디', '다히르', '사이드', '알리', '무세', '바일레', '누르', '구External', '파라', '모하무드', '와르사마', '압둘라히'],
    '터크스 케이커스 제도': ['빌리', '마르코', '레이먼드', '알렉스', '크리스토퍼', '레External', '칼럼', '제임스', '포브스', '페네루스', '뷰External', '파크', '브라운', '스미스', '윌리엄스', '존슨', '브룩스', '그린', '맥나이트', '호웰', '토마스', '로빈슨', '클라크', '가르시아', '데이비스', '테일러', '존스', '밀러', '조지', '해리스'],
    '괌': ['제이슨', '마르쿠스', '트래비스', '이안', '숀', '데반', '딜런', '네External', '컨리프', '로페스', '멘디올라', '마리아노', '닉라우', '리드가External', '말콤', '기다', '카스트로', '팡겔리난', '차르고알라프', '나푸티', '테노리오', '보르하', '라구아나', '크루스', '블라스', '사블란', '게레로', '페레스', '카마초', '산토스'],
    '영국령 버진아일랜드': ['타일러', '크리스티안', '제이든', '로비', '루카', '리엄', '베일리', '조슈아', '포브스', '하이어우드', '사무엘', '버티', '피터스', '그린', '윌리엄스', '스미스', '갈리모어', '도널드슨', '토마스', '잭슨', '제임스', '존슨', '찰스', '데이비스', '테일러', '로빈슨', '브라운', '클라크', '조지', '마틴'],
    '스리랑카': ['카빈두', '딜론', '수잔', '주드', '모하메드', '샬라나', '차미라', '니레External', '이샨', '드 실바', '페레라', '페르난도', '파잘', '수만', '마두샨', '라자팍사', '쿠마라', '자야수리야', '구나라트네', '디사나야케', '헤라트', '반다라', '위라싱헤', '란지스', '치트라External', '자니카', '닐란카', '프라디프', '하르샤', '로샨'],
    '미국령 버진아일랜드': ['맥도날드', '제이비어', '라샤운', '재커리', '타일러', '카르디', '짐', '조슈아', '테일러', '해리스', '라모스', '브라운', '스미스', '윌리엄스', '토마스', '찰스', '데이비스', '로빈슨', '그린', '클라크', '존스', '헨리', '밀러', '조셉', '마틴', '알렌', '잭슨', '데이비드', '제임스', '리'],
    '부탄': ['첸초', '니마', '카르마', '치미', '체링', '왕디', '다와', '예셰이', '기엘첸', '왕축', '도르지', '체링', '덴두프', '토브가이', '펜조르', '남기엘', '린첸', '구룽', '프라단', '수바', '타망', '라이', '샤르마', '초펠', '가External', '텐진', '삼드룹', '상가이', '릭진', '트셰왕'],
    '바하마': ['레스완', '우드External', '카메론', '발론', '나이절', '레이한', '에반', '딜런', '베External', '세인트 플뢰르', '히튼', '조셉', '로버츠', '코리', '스미스', '러셀', '모스', '롤', '존슨', '밀러', '놀스', '퍼거슨', '애더리', '베External', '아키노', '핀더', '스튜어트', '라잉', '알베리', '쿠퍼'],
    '안귈라': ['에이단', '조나단', '칼럼', '자말', '케론', '마이클', '조수아', '사이러스', '스카이프', '구External', '휴즈', '리처드슨', '브라운', '로저스', '카티', '호지', '스미스', '윌리엄스', '토마스', '프랭클린', '로빈슨', '파리시', '기브스', '플레밍', '브라이언트', '레이크', '로버츠', '존슨', '찰스', '데이비스'],
    '에리트레아': ['알리', '요나탄', '헤녹', '에프렘', '롭에', '테스파르디', '센타예후', '아브라함', '고이톰', '테스파지온', '기르마이', '베르헤', '안데브르한', '메하리', '게브레메딘', '네가External', '멜라케', '솔로몬', '테클레', '하브테', '이드리스', '모하메드', '오스만', '하산', '아페워크', '시몬', '요하네스', '다윗', '에스라', '사무엘'],
}

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
    "S":{1:(90,100),2:(78,88),3:(65,75)},
    "A":{1:(82,90), 2:(70,78),3:(58,65)},
    "B":{1:(75,82), 2:(63,70),3:(52,58)},
    "C":{1:(65,73), 2:(55,62),3:(45,52)},
    "D":{1:(55,63), 2:(45,53),3:(35,43)},
    "E":{1:(45,53), 2:(35,43),3:(28,35)},
    "F":{1:(35,43), 2:(27,35),3:(20,27)},
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
    "S": {"ace_lo": 0.93, "spread": 0.10},
    "A": {"ace_lo": 0.90, "spread": 0.13},
    "B": {"ace_lo": 0.88, "spread": 0.16},
    "C": {"ace_lo": 0.86, "spread": 0.19},
    "D": {"ace_lo": 0.84, "spread": 0.22},
    "E": {"ace_lo": 0.82, "spread": 0.24},
    "F": {"ace_lo": 0.80, "spread": 0.26},
}


def _tier_top_ovr(grade, tier):
    """그 등급·tier 리그에서 도달 가능한 최고 OVR.
    기존 OVR_RANGES의 상단값을 재활용해 밸런스 연속성을 유지한다."""
    rng = OVR_RANGES.get(grade, {}).get(tier)
    if rng:
        return rng[1]
    return 45


def _target_ovr(grade, tier, team_strength, role_idx):
    """팀 강도(0~1) + 역할 순번(0=에이스 … 10=막내)으로 목표 OVR 산출."""
    prof = TEAM_ROLE_PROFILE.get(grade, TEAM_ROLE_PROFILE["F"])
    top = _tier_top_ovr(grade, tier)
    # 팀 에이스 목표: 강팀일수록 리그 top에 근접
    ace = top * (prof["ace_lo"] + (1.0 - prof["ace_lo"]) * team_strength)
    role_mult = 1.0 - prof["spread"] * (role_idx / 10.0)
    return ace * role_mult


def _generate_all_ai_players(c):
    # 리그 단위로 묶어 8팀에 강→약 강도를 분배해야 팀 간 위계가 생긴다.
    c.execute("""SELECT t.id AS tid, t.current_tier AS tier, cn.grade AS grade,
                        cn.id AS cid, t.league_id AS lid
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
        # 리그 내 팀들에 강도 부여 (랜덤 셔플 후 강→약). 시작 강약은 무작위.
        order = list(range(n))
        random.shuffle(order)
        for rank, team in zip(order, teams):
            # rank 0 = 최강 후보(강도 1.0) … rank n-1 = 최약(강도 0)
            team_strength = 1.0 - (rank / (n - 1)) if n > 1 else 1.0
            _generate_team_players(c, team, team_strength)


def _generate_team_players(c, team, team_strength):
    grade = team["grade"]; tier = team["tier"]
    c.execute("SELECT name FROM player_names WHERE country_id=? ORDER BY RANDOM() LIMIT 30",
              (team["cid"],))
    name_pool = [r["name"] for r in c.fetchall()]
    if not name_pool:
        name_pool = [f"선수{i}" for i in range(30)]
    used = set()
    for idx, pos in enumerate(TEAM_POSITIONS):
        available = [n for n in name_pool if n not in used]
        if not available:
            available = name_pool
        name = random.choice(available); used.add(name)
        target = _target_ovr(grade, tier, team_strength, idx)
        stats = _gen_ai_stats(pos, target)
        ovr = calc_ovr(pos, stats)
        c.execute("""INSERT INTO ai_players
            (team_id,name,position,stamina,speed,jump,strength,shooting,passing,
             dribbling,tackling,heading,positioning,setpiece,
             mental,confidence,leadership,concentration,ovr)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (team["tid"],name,pos,
             stats["stamina"],stats["speed"],stats["jump"],stats["strength"],
             stats["shooting"],stats["passing"],stats["dribbling"],
             stats["tackling"],stats["heading"],stats["positioning"],
             stats["setpiece"],stats["mental"],stats["confidence"],
             stats["leadership"],stats["concentration"],ovr))


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