# constants.py

GAME_START_YEAR = 1990
PLAYER_START_AGE = 16
MIN_JOIN_AGE = 17
MAX_AGE = 50

# 시즌 구조 (주차 범위)
SEASON_PHASES = {
    "preseason1":  (1,  4),
    "first_half":  (5,  11),
    "midseason":   (12, 25),
    "second_half": (26, 32),
    "postseason":  (33, 52),
}

# 상/하반기 라운드 매칭 (8팀, 인덱스 기반)
ROUND_MATCHES = [
    [(0,1),(2,3),(4,5),(6,7)],
    [(0,2),(1,4),(3,6),(5,7)],
    [(0,3),(1,5),(2,7),(4,6)],
    [(0,4),(1,6),(2,5),(3,7)],
    [(0,5),(1,7),(2,6),(3,4)],
    [(0,6),(1,3),(2,4),(5,7)],
    [(0,7),(1,2),(3,5),(4,6)],
]
FIRST_HALF_START  = 5
SECOND_HALF_START = 26

# 훈련 설정
TRAINING_CONFIG = {
    "고강도":   {"stress":+20, "injury_chance":0.20, "gain_min":4, "gain_max":6, "exceed_limit":True},
    "집중훈련": {"stress":+16, "injury_chance":0.00, "gain_min":4, "gain_max":5, "exceed_limit":False},
    "중강도":   {"stress":+15, "injury_chance":0.02, "gain_min":3, "gain_max":4, "exceed_limit":False},
    "저강도":   {"stress":+ 8, "injury_chance":0.00, "gain_min":2, "gain_max":3, "exceed_limit":False},
    "휴식":     {"stress":-15, "injury_chance":0.00, "gain_min":-1,"gain_max":-1,"exceed_limit":False},
}

MATCH_STRESS = +20
MATCH_STAT_GAIN_MIN = 1
MATCH_STAT_GAIN_MAX = 2

# 슬럼프
SLUMP_STRESS_THRESHOLD   = 60
SLUMP_HAPPY_THRESHOLD    = 20
SLUMP_CHANCE             = 0.50
SLUMP_RECOVER_STRESS     = 40
SLUMP_TRAIN_PENALTY      = 0.80  # 효율 80%
SLUMP_RATING_PENALTY     = -1.0

# 부상
INJURY_TYPES = {
    "경미": (1, 2),
    "중간": (3, 4),
    "심각": (5, 6),
}

# 성격
PERSONALITY_EFFECTS = {
    "성실함":   {"train_eff": 1.20},
    "게으름":   {"train_eff": 0.80},
    "냉철함":   {"stress_mult": 0.90},
    "긍정적":   {"happy_gain_mult": 1.15},
    "소심함":   {"big_match_rating": -0.3},
    "승부욕":   {"losing_rating": +0.3},
    "리더십":   {"team_win_bonus": 0.03},
    "폭력적":   {"red_card_chance": 0.05},
    "완벽주의": {"high_train_bonus": 1.10, "low_train_penalty": 0.90},
    "멘탈갑":   {"slump_chance_mult": 0.70},
    "겁쟁이":   {"cup_rating": -0.5},
    "천재":     {"natural_growth_bonus": 0.20},
    "부상체질": {"injury_add": +0.10},
    "강철체질": {"injury_add": -1.0, "stamina_train": 1.15},  # 부상 완전 면역
    # 신규
    "강철멘탈": {"no_slump": True},                          # 슬럼프 면역
    "유리멘탈": {"slump_threshold_reduce": 20,
                 "slump_chance_add": 0.30},                  # 40 이상부터 발동, 60+ 확률+30%
    "훈련광":   {"train_eff": 1.20, "stress_mult": 1.10},    # 훈련효율+20%, 스트레스도 10% 더 쌓임
}
PERSONALITIES = list(PERSONALITY_EFFECTS.keys())

# 에이전트
AGENT_GRADES = ["F","E","D","C","B","A","S"]
AGENT_FEE_RATE = {"F":0.00,"E":0.03,"D":0.06,"C":0.10,"B":0.15,"A":0.20,"S":0.28}
AGENT_UPPER_LEAGUE_BONUS = {"F":0,"E":1,"D":1,"C":2,"B":2,"A":3,"S":3}

# 포지션
POSITIONS = ["GK","CB","LB","RB","CDM","CM","CAM","LW","RW","CF","ST"]

# 세부역할
SUB_ROLES = {
    "GK":  ["스위퍼킵퍼","전통형"],
    "CB":  ["볼플레잉","수비형"],
    "LB":  ["공격형","수비형"],
    "RB":  ["공격형","수비형"],
    "CDM": ["홀딩","박스투박스"],
    "CM":  ["박스투박스","플레이메이커"],
    "CAM": ["섀도우","클래식"],
    "LW":  ["인버티드","클래식윙어"],
    "RW":  ["인버티드","클래식윙어"],
    "CF":  ["딥라잉","타깃형"],
    "ST":  ["포처","타깃형"],
}

# 집중훈련 가능 스탯 (포지션별)
FOCUS_TRAIN_STATS = {
    "GK":  ["stamina","jump","positioning"],
    "CB":  ["tackling","heading","jump","stamina","positioning"],
    "LB":  ["tackling","speed","passing","stamina","positioning"],
    "RB":  ["tackling","speed","passing","stamina","positioning"],
    "CDM": ["tackling","passing","positioning","stamina"],
    "CM":  ["passing","dribbling","positioning","stamina","shooting"],
    "CAM": ["passing","dribbling","shooting","positioning","setpiece"],
    "LW":  ["dribbling","speed","shooting","passing","positioning"],
    "RW":  ["dribbling","speed","shooting","passing","positioning"],
    "CF":  ["shooting","heading","dribbling","positioning","passing"],
    "ST":  ["shooting","heading","jump","speed","positioning"],
}

# 포지션별 핵심(우선순위 높은) 기술 스탯 — 훈련 시 tech_pool에서 이 스탯 먼저 선택
PRIORITY_TECH_STATS = {
    "GK":  [],
    "CB":  ["tackling","heading"],
    "LB":  ["tackling","passing"],
    "RB":  ["tackling","passing"],
    "CDM": ["tackling","passing"],
    "CM":  ["passing","dribbling"],
    "CAM": ["passing","dribbling","shooting"],
    "LW":  ["dribbling","shooting"],
    "RW":  ["dribbling","shooting"],
    "CF":  ["shooting","heading"],
    "ST":  ["shooting","heading"],
}

ALL_STATS = [
    "stamina","speed","jump","shooting","passing","dribbling",
    "tackling","heading","positioning","setpiece",
    "mental","confidence","leadership","concentration"
]

# 훈련으로 오르는 스탯 분류
PHYSICAL_STATS  = ["stamina","speed","jump"]
TECHNICAL_STATS = ["shooting","passing","dribbling","tackling","heading","positioning","setpiece"]
MENTAL_STATS    = ["mental","confidence","leadership","concentration"]

STAT_KO = {
    "stamina":"체력","speed":"스피드","jump":"점프력",
    "shooting":"슈팅","passing":"패스","dribbling":"드리블",
    "tackling":"태클","heading":"헤딩","positioning":"포지셔닝",
    "setpiece":"세트피스","mental":"멘탈","confidence":"자신감",
    "leadership":"리더십","concentration":"집중력"
}
STAT_EN = {
    "stamina":"Stamina","speed":"Speed","jump":"Jump",
    "shooting":"Shooting","passing":"Passing","dribbling":"Dribbling",
    "tackling":"Tackling","heading":"Heading","positioning":"Positioning",
    "setpiece":"Set Piece","mental":"Mental","confidence":"Confidence",
    "leadership":"Leadership","concentration":"Concentration"
}

# 포메이션 포지션 목록
FORMATION_SLOTS = {
    "4-4-2":   ["GK","CB","CB","LB","RB","LM","CM","CM","RM","ST","ST"],
    "4-3-3":   ["GK","CB","CB","LB","RB","CDM","CM","CM","LW","RW","ST"],
    "3-5-2":   ["GK","CB","CB","CB","LWB","CDM","CM","CM","RWB","ST","ST"],
    "4-2-3-1": ["GK","CB","CB","LB","RB","CDM","CDM","LW","CAM","RW","ST"],
    "5-3-2":   ["GK","CB","CB","CB","LWB","RWB","CM","CM","CM","ST","ST"],
    "4-1-4-1": ["GK","CB","CB","LB","RB","CDM","LM","CM","CM","RM","ST"],
    "3-4-3":   ["GK","CB","CB","CB","LM","CM","CM","RM","LW","RW","ST"],
}

# 감독 관계 벤치 확률
BENCH_BY_RELATION = [(15,0.30),(10,0.50),(5,0.70),(0,0.90)]

# 노화 (나이 → 연간 한계스탯 감소)
AGING = [(30,1),(32,2),(34,3),(36,5),(38,7)]

# 팬수 기본값
BASE_FANS = {
    "S":{1:500000,2:50000,3:500},
    "A":{1:300000,2:30000,3:300},
    "B":{1:200000,2:20000,3:200},
    "C":{1:100000,2:10000,3:100},
    "D":{1:50000, 2:5000, 3:50},
    "E":{1:20000, 2:2000, 3:20},
    "F":{1:50000, 2:25000,3:500},
}
AFRICA_FAN_MULT = 10

# 월드컵
WC_START_YEAR     = 1994
WC_INTERVAL       = 4
WC_WEEKS          = (16, 20)

# 대륙 대회
CONTINENTAL_START_YEAR = 1992
CONTINENTAL_INTERVAL   = 4
CONTINENTAL_WEEKS      = (13, 17)

CONTINENT_NAMES = ["유럽","아시아","아프리카","북미+남미"]

# OVR 범위 (등급별)
OVR_RANGES = {
    "S":{1:(90,100),2:(78,88),3:(65,75)},
    "A":{1:(82,90), 2:(70,78),3:(58,65)},
    "B":{1:(75,82), 2:(63,70),3:(52,58)},
    "C":{1:(65,73), 2:(55,62),3:(45,52)},
    "D":{1:(55,63), 2:(45,53),3:(35,43)},
    "E":{1:(45,53), 2:(35,43),3:(28,35)},
    "F":{1:(35,43), 2:(27,35),3:(20,27)},
}