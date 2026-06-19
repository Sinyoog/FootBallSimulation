# constants.py

GAME_START_YEAR = 1990
PLAYER_START_AGE = 16
MIN_JOIN_AGE = 17
MAX_AGE = 50

# 시즌 구조 (주차 범위)
SEASON_PHASES = {
    "preseason1":  (1,  4),
    "first_half":  (5,  11),
    "midseason":   (12, 28),   # 국제대회 윈도우(17~24) 포함
    "second_half": (29, 35),
    "postseason":  (36, 52),
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
SECOND_HALF_START = 29

# 오퍼(이적시장) 구간: 겨울(대회 전/후) + 여름(시즌 후)
OFFER_ZONES = [(1,4),(13,16),(25,28),(37,40),(45,48)]

# 국제대회 윈도우 (월드컵/대륙컵 공용, 17~24주 = 턴 5~6)
INTL_CALLUP_WEEK  = 17          # 소집/조 추첨
INTL_GROUP_WEEKS  = (18, 20)    # 조별리그 3경기 (월드컵만)
INTL_KO_WEEKS     = (21, 24)    # 16강(21)~결승(24)

# 훈련 설정
# gain_min/max: 일반훈련(중/저/집중)은 소프트캡과 함께 점진 성장하도록 하향.
#               고강도(exceed_limit=True)는 max~talent_cap 돌파용이라 강하게 유지.
TRAINING_CONFIG = {
    "고강도":   {"stress":+20, "injury_chance":0.20, "gain_min":4,   "gain_max":6,   "exceed_limit":True},
    "집중훈련": {"stress":+16, "injury_chance":0.00, "gain_min":3.5, "gain_max":5.0, "exceed_limit":False},
    "중강도":   {"stress":+15, "injury_chance":0.00, "gain_min":1.8, "gain_max":2.8, "exceed_limit":False},
    "저강도":   {"stress":+ 8, "injury_chance":0.00, "gain_min":1.0, "gain_max":1.8, "exceed_limit":False},
    "휴식":     {"stress":-15, "injury_chance":0.00, "gain_min":-1,  "gain_max":-1,  "exceed_limit":False},
}

# 소프트캡: 일반훈련 시 max에 가까울수록 상승폭 둔화 (분모 클수록 완만)
SOFTCAP_DENOM = 40.0
SOFTCAP_FLOOR = 0.10

# 재능 등급별 고강도 돌파 상한 (talent_cap). 일반훈련 max와는 별개의 천장.
# 부상 없이 고강도를 꾸준히 하면 이 값까지 개별 스탯을 올릴 수 있음.
TALENT_TIERS = {
    "gifted": {"prob": 0.15, "cap_min": 100, "cap_max": 100},
    "mid":    {"prob": 0.35, "cap_min": 92,  "cap_max": 94},
    "normal": {"prob": 0.50, "cap_min": 86,  "cap_max": 88},
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
    # 성격 천재: 멘탈 스탯 성장 + 자연 성장 보너스 (재능이 '머리/정신'에서 옴)
    "천재":     {"natural_growth_bonus": 0.20, "mental_growth_mult": 1.25},
    # 멘탈 계열
    "강철멘탈": {"no_slump": True},                          # 슬럼프 면역
    "유리멘탈": {"slump_threshold_reduce": 20,
                 "slump_chance_add": 0.30},                  # 40 이상부터 발동, 60+ 확률+30%
    "훈련광":   {"train_eff": 1.20, "stress_mult": 1.10},    # 훈련효율+20%, 스트레스도 10% 더 쌓임
}
PERSONALITIES = list(PERSONALITY_EFFECTS.keys())

# ════════════════════════════════════════════════════════════════
# 신체 특징 (physical trait) — 성격과 별개. 선수는 특징 1개를 가진다.
#   '부상체질/강철체질'처럼 체질·신체 계열은 성격이 아니라 여기로 분리.
#   '무난함'은 특별한 특징이 없는 평범한 신체(가중치 높음).
# ════════════════════════════════════════════════════════════════
PHYSICAL_TRAIT_EFFECTS = {
    "무난함":     {},                                         # 특징 없음(평범)
    "부상체질":   {"injury_add": +0.10},                      # 부상 확률 +10%
    "강철체질":   {"injury_add": -1.0, "stamina_train": 1.15},# 부상 완전 면역 + 체력훈련+15%
    "지구력형":   {"stress_mult": 0.85},                      # 스트레스 덜 쌓임(체력 좋음)
    "스피드스타": {"phys_growth_mult": 1.20, "phys_stat": "speed"},  # 스피드 성장↑
    "피지컬몬스터":{"phys_growth_mult": 1.15},                 # 신체 스탯 전반 성장↑
    # 신체 천재: 초반 신체 스탯이 높게 생성 + 신체 성장 보너스
    "신체천재":   {"phys_start_bonus": +8, "phys_growth_mult": 1.20},
}
PHYSICAL_TRAITS = list(PHYSICAL_TRAIT_EFFECTS.keys())
# 등장 가중치: 무난함이 흔하고, 천재/몬스터는 희귀
PHYSICAL_TRAIT_WEIGHTS = [34, 12, 8, 14, 12, 10, 10]

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

# 노화 (나이 → 연간 한계스탯 감소). 전성기 황금기(peak~peak+5)를 보장하기 위해
#   노화 시작을 31세로 늦추고 곡선을 완만화. 본격 쇠퇴는 35세 이후.
AGING = [(31,1),(33,2),(35,4),(37,6)]

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

CONTINENTAL_START_YEAR = 1992
CONTINENTAL_INTERVAL   = 4

# ── 국제대회 본선 설정 ──────────────────────────────
WC_TEAMS   = 32   # 월드컵 본선 32개국 (8조 × 4팀)
WC_GROUPS  = 8
CONT_TEAMS = 16   # 대륙컵 본선 16개국 (4조 × 4팀)
CONT_GROUPS = 4

# 월드컵 대륙별 쿼터 (합 32, 개최국 포함 / 오세아니아는 아시아 편입)
WC_QUOTA = {"유럽": 13, "남미": 4, "북미": 4, "아프리카": 5, "아시아": 6}

# 대륙 연맹 구성 (대륙컵 참가 풀)
CONFEDERATIONS = {
    "유럽":       ["유럽"],
    "남미":       ["남미", "북미"],
    "북미":       ["남미", "북미"],
    "아프리카":   ["아프리카"],
    "아시아":     ["아시아", "오세아니아"],
    "오세아니아": ["아시아", "오세아니아"],
}
CONF_CUP_NAME = {
    "유럽": "유럽 챔피언십", "남미": "남북미 대륙컵", "북미": "남북미 대륙컵",
    "아프리카": "아프리카 네이션스컵", "아시아": "아시안컵", "오세아니아": "아시안컵",
}

# 국가 등급 → 대표팀 전력(OVR) / 예선 기본 점수
GRADE_TEAM_OVR  = {"S": 86, "A": 79, "B": 72, "C": 65, "D": 58, "E": 51, "F": 45}
GRADE_QUAL_BASE = {"S": 0.90, "A": 0.78, "B": 0.62, "C": 0.48,
                   "D": 0.36, "E": 0.26, "F": 0.16}
QUAL_NOISE = 0.12   # 예선 랜덤 노이즈 (±) → 강호 탈락/약체 진출 이변 발생

# 국가대표 선발 기준 (국가 등급별 최소 OVR / 최대 소속 리그 티어)
INTL_SELECTION_OVR = {"S": 75, "A": 65, "B": 55, "C": 48, "D": 42, "E": 37, "F": 32}
# 국가대표 선발 마진: 자국 등급평균(GRADE_TEAM_OVR) 대비 이만큼 낮아도 선발.
#   작을수록 엄격(톱권만). 베테랑 보너스(_vet_bonus)가 경계선 선수를 구제한다.
INTL_SELECTION_MARGIN = 5
INTL_MAX_TIER      = {"S": 1, "A": 1, "B": 2, "C": 2, "D": 3, "E": 3, "F": 3}
INTL_MIN_MATCHES   = 5

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

# ── 포지션군 (평점/오퍼 임계치 분리용) ──────────────────────
POS_GROUP = {
    "GK":"GK",
    "CB":"수비","LB":"수비","RB":"수비",
    "CDM":"미드","CM":"미드",
    "CAM":"공격","LW":"공격","RW":"공격","CF":"공격","ST":"공격",
}
DEF_POS = {"CB","LB","RB","CDM"}   # 수비 라인 평점 보너스 대상

# 재계약/오퍼 평점 기준선 (포지션군별). 공격수 편향 보정.
RENEW_RATING = {"공격":6.5, "미드":6.3, "수비":6.1, "GK":6.1}

# ── 리그 수준 적합성 / 도태 시스템 ──────────────────────────
# OVR 격차(gap = 팀평균OVR - 내OVR) 기반 벤치 확률
BENCH_BY_GAP = [(-5,0.02),(0,0.08),(5,0.20),(10,0.45),(15,0.70),(999,0.90)]

# 수준 미달 방출 임계치
RELEASE_GAP_SOFT      = 12   # 이만큼 부족 + 출전 부족하면 방출 후보
RELEASE_GAP_SOFT_MATCH = 8   # season_matches 이 미만이어야 (벤치 누적)
RELEASE_GAP_HARD      = 18   # 이만큼 부족하면 평점 무관 방출

# OVR 기반 오퍼 티어 가중치 (성장 시 상위 리그로 이동)
def tier_weights_by_ovr(ovr):
    if ovr >= 80:   return [70, 25, 5]
    elif ovr >= 70: return [45, 40, 15]
    elif ovr >= 60: return [20, 45, 35]
    elif ovr >= 50: return [8, 32, 60]
    else:           return [3, 22, 75]


# ── 개인 수상 시스템 ────────────────────────────────────────
# 포지션별 시즌 기대 득점 베이스 (OVR 70, 풀시즌 기준)
AWARD_POS_GOAL = {"ST":18,"CF":14,"LW":9,"RW":9,"CAM":7,"CM":4}
# 포지션별 시즌 기대 도움 베이스
AWARD_POS_ASSIST = {"CAM":11,"CM":8,"LW":8,"RW":8,"CF":6,"ST":5}
# 공격 가담 포지션 (수상 후보 풀)
ATTACK_POS = ("ST","CF","LW","RW","CAM","CM")
# 발롱도르 후보 리그 등급 (최상위 리그만)
BALLON_DOR_GRADES = ("S","A")

# 포지션 그룹 (베스트 11 포메이션용)
GK_POS = ("GK",)
DF_POS = ("CB", "LB", "RB", "LWB", "RWB")
MF_POS = ("CDM", "CM", "CAM")
FW_POS = ("LW", "RW", "CF", "ST")


# ── 리그 부유도(연봉 수준) 오버라이드 ───────────────────────
# FIFA 등급(국대 실력)과 별개로, 리그가 부유한 나라는 연봉이 높음.
# 예: 사우디는 국대 C급이지만 오일머니로 리그 연봉은 S급.
# 여기 없는 나라는 FIFA 등급을 그대로 부유도로 사용.
LEAGUE_WEALTH_OVERRIDE = {
    "사우디아라비아": "S",   # 호날두/네이마르 영입, 세계 최상위 연봉
    "독일": "S",            # 분데스리가 — 바이에른 등 톱클럽 (country grade A→연봉은 S급)
    "중국": "A",            # 슈퍼리그 머니파워
    "카타르": "A",          # 오일머니
    "아랍에미리트": "B",     # UAE 리그 고액
}

# ════════════════════════════════════════════════════════════════
# [기능1] 이적 오퍼 맥락 시스템 — 역할 / 감독 관심도 / 구단 야망 / 계약 옵션
# ════════════════════════════════════════════════════════════════

# 오퍼 역할: 입단 후 기대 출전 + 벤치 확률 보정 + 감독관계 초기값 보정
#   bench_mult : 벤치 확률에 곱(주전일수록 낮음)
#   rel_init   : 입단 시 manager_relation 초기값 (주전 보장일수록 높음)
#   ovr_gap_pref : 이 역할이 뜨기 위한 (팀평균OVR - 내OVR) 선호 구간
OFFER_ROLES = {
    "주전 보장":   {"bench_mult": 0.45, "rel_init": 62, "press": 1.20, "desc": "즉시 주전으로 기용"},
    "주전 경쟁":   {"bench_mult": 0.85, "rel_init": 50, "press": 1.05, "desc": "경쟁을 통한 주전 도전"},
    "로테이션":    {"bench_mult": 1.25, "rel_init": 48, "press": 0.85, "desc": "로테이션 자원"},
    "유망주 영입": {"bench_mult": 1.40, "rel_init": 55, "press": 0.70, "desc": "미래를 보고 육성"},
}

# 감독 관심도: 오퍼 카드에 표시 + 입단 시 감독관계 가산
OFFER_INTEREST = {
    "감독 직접 지명": {"rel_bonus": +12, "weight": 25, "desc": "감독이 당신을 콕 집어 원함"},
    "구단 추천":      {"rel_bonus": +4,  "weight": 45, "desc": "구단이 영입을 추천"},
    "명단 후보":      {"rel_bonus": 0,   "weight": 30, "desc": "영입 후보 명단에 포함"},
}

# 구단 야망: 입단 후 기대치(압박) 결정 → 방출 임계치에 영향
OFFER_AMBITION = {
    "우승 도전":     {"press": 1.35, "weight": 18, "desc": "리그 우승이 목표"},
    "상위권 도전":   {"press": 1.15, "weight": 30, "desc": "유럽대회 진출권 목표"},
    "중위권 안정":   {"press": 1.00, "weight": 34, "desc": "안정적인 시즌 운영"},
    "강등 회피":     {"press": 0.80, "weight": 18, "desc": "잔류가 최우선"},
}

# 계약 옵션(보너스): 입단 시 부여, 시즌 정산에 반영(가벼운 보상)
#   appearance_bonus_k : 경기당 출전 보너스(천원)
#   goal_bonus_k       : 골/도움당 보너스(천원)
# 티어/등급이 좋을수록 보너스 규모 ↑
def offer_bonus_by_tier(tier: int):
    base = {1: (40, 120), 2: (18, 55), 3: (6, 20)}.get(tier, (6, 20))
    return {"appearance_bonus_k": base[0], "goal_bonus_k": base[1]}


# ════════════════════════════════════════════════════════════════
# [기능2] 감독 성향 시스템 — 팀(감독)마다 타입 부여, 벤치/관계/방출 보정
# ════════════════════════════════════════════════════════════════
#   bench_mult       : 벤치 확률 곱
#   rel_gain_mult    : 좋은 평점 시 관계 상승 곱
#   rel_loss_mult    : 나쁜 평점 시 관계 하락 곱
#   release_relax    : 방출 임계치 완화(+면 잘 안 자름)
#   stress_mult      : 경기/훈련 스트레스 곱
#   youth_pref_age   : 이 나이 이하면 벤치 확률 추가 완화(유스 중시)
MANAGER_TYPES = {
    "뚝심형":     {"bench_mult": 0.75, "rel_gain_mult": 0.8, "rel_loss_mult": 0.5,
                  "release_relax": +0.15, "stress_mult": 1.0, "youth_pref_age": 0,
                  "desc": "한번 믿으면 부진해도 꾸준히 기용"},
    "성과주의":   {"bench_mult": 1.20, "rel_gain_mult": 1.3, "rel_loss_mult": 1.4,
                  "release_relax": -0.15, "stress_mult": 1.10, "youth_pref_age": 0,
                  "desc": "결과로 모든 걸 판단, 부진하면 가차없이"},
    "유스 중시":  {"bench_mult": 1.0, "rel_gain_mult": 1.1, "rel_loss_mult": 0.9,
                  "release_relax": +0.10, "stress_mult": 0.95, "youth_pref_age": 23,
                  "desc": "어린 유망주를 적극 기용·육성"},
    "베테랑 신뢰": {"bench_mult": 1.0, "rel_gain_mult": 1.0, "rel_loss_mult": 1.0,
                  "release_relax": 0.0, "stress_mult": 0.95, "youth_pref_age": -1,
                  "desc": "경험 많은 선수를 선호"},
    "엄격함":     {"bench_mult": 1.05, "rel_gain_mult": 0.9, "rel_loss_mult": 1.2,
                  "release_relax": -0.05, "stress_mult": 1.20, "youth_pref_age": 0,
                  "desc": "훈련·규율이 혹독해 스트레스가 크다"},
    "온화함":     {"bench_mult": 0.95, "rel_gain_mult": 1.2, "rel_loss_mult": 0.7,
                  "release_relax": +0.10, "stress_mult": 0.85, "youth_pref_age": 0,
                  "desc": "선수를 다독이며 분위기를 중시"},
}
MANAGER_TYPE_LIST = list(MANAGER_TYPES.keys())
# 등장 가중치 (현실감: 성과주의/뚝심형이 흔함)
MANAGER_TYPE_WEIGHTS = [22, 24, 12, 12, 15, 15]


# ════════════════════════════════════════════════════════════════
# [기능3] 능동 액션 — 이적 요청 / 재계약 협상
# ════════════════════════════════════════════════════════════════
# 이적 요청: 감독관계 하락 감수 → 다음 오퍼 창에서 오퍼 수/품질 ↑
TRANSFER_REQUEST_REL_PENALTY = 25     # 요청 시 감독관계 즉시 하락
TRANSFER_REQUEST_OFFER_BONUS = 2      # 다음 오퍼 창 오퍼 개수 +n

# 재계약 협상: 성공 시 연봉 인상 + 계약 연장, 실패 시 감독관계 소폭 하락
#   협상 성공 확률 = base + 평점보정 + 감독관계보정
RENEW_NEGOTIATE = {
    "base_prob": 0.45,
    "rating_per_point": 0.18,   # (평점-6.5) * 이 값
    "rel_per_10": 0.06,         # (관계-50)/10 * 이 값
    "raise_success": (0.12, 0.30),  # 성공 시 연봉 인상폭 범위
    "raise_fail_rel": -8,           # 실패 시 감독관계 변화
    "extend_years": 2,              # 성공 시 연장 연수
}