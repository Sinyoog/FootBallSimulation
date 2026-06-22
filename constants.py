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
    [(0,7),(1,6),(2,5),(3,4)],
    [(0,6),(7,5),(1,4),(2,3)],
    [(0,5),(6,4),(7,3),(1,2)],
    [(0,4),(5,3),(6,2),(7,1)],
    [(0,3),(4,2),(5,1),(6,7)],
    [(0,2),(3,1),(4,7),(5,6)],
    [(0,1),(2,7),(3,6),(4,5)],
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
    "고강도":   {"stress":+20, "injury_chance":0.20, "gain_min":4.0, "gain_max":5.5, "exceed_limit":True},
    "강점훈련": {"stress":+16, "injury_chance":0.00, "gain_min":3.3, "gain_max":4.6, "exceed_limit":False, "focus_mode":"strong"},
    "약점훈련": {"stress":+16, "injury_chance":0.00, "gain_min":3.3, "gain_max":4.6, "exceed_limit":False, "focus_mode":"weak"},
    "중강도":   {"stress":+15, "injury_chance":0.00, "gain_min":2.0, "gain_max":3.0, "exceed_limit":False},
    "저강도":   {"stress":+ 8, "injury_chance":0.00, "gain_min":1.1, "gain_max":1.8, "exceed_limit":False},
    "휴식":     {"stress":-15, "injury_chance":0.00, "gain_min":-1,  "gain_max":-1,  "exceed_limit":False},
}

# 소프트캡: 일반훈련 시 max에 가까울수록 상승폭 둔화 (분모 클수록 완만)
SOFTCAP_DENOM = 40.0
SOFTCAP_FLOOR = 0.10

# 강점/약점 집중훈련: max 도달 후 talent_cap까지 한계 돌파 확률.
# 고강도(상시 돌파)와 달리 가끔만 돌파한다. 두 모드 동일 — 차이는 '타겟 스탯'뿐.
#   - 강점훈련: 한계치(_max)가 높은 스탯을 집중해서 그 한계까지 채움
#   - 약점훈련: 한계치가 낮은 스탯을 집중해서 그 한계까지 채움
FOCUS_BREAK_PROB_STRONG = 0.05
FOCUS_BREAK_PROB_WEAK   = 0.05
FOCUS_BREAK_PROB        = 0.05

# 고강도 훈련: _max 도달 후 한 번 훈련 시 _max를 +1 끌어올릴 확률.
#   집중훈련(5%)보다 높게 둬서 고강도가 한계 돌파의 주력 트랙임을 분명히 한다.
HIGH_BREAK_PROB = 0.20

# 재능 등급별 고강도 돌파 상한 (talent_cap). 일반훈련 max와는 별개의 천장.
# 부상 없이 고강도를 꾸준히 하면 이 값까지 개별 스탯을 올릴 수 있음.
#   이 cap은 '개별 스탯이 고강도 돌파로 도달 가능한 평균적 천장'이자
#   전성기 OVR 의 목표 범위이기도 하다 (강점은 cap+α로 100 초과 가능,
#   약점은 cap 아래라 평균은 cap 부근에서 균형).
#   - 월클(worldclass): 전성기 OVR 96~100, 강점 스탯은 고강도로 100+ 가능
#   - 우수(elite):      전성기 OVR 88~93
#   - 평범(normal):     전성기 OVR 82~87
#   - 무재능(limited):  전성기 OVR 73~80 (아무리 갈아도 80 언저리)
TALENT_TIERS = {
    "worldclass": {"prob": 0.20, "cap_min": 96, "cap_max": 100},
    "elite":      {"prob": 0.30, "cap_min": 88, "cap_max": 93},
    "normal":     {"prob": 0.30, "cap_min": 82, "cap_max": 87},
    "limited":    {"prob": 0.20, "cap_min": 73, "cap_max": 80},
}

# (구버전 호환) 예전 티어명을 새 티어로 매핑
_LEGACY_TALENT_ALIAS = {"gifted": "worldclass", "mid": "elite"}

MATCH_STRESS = +20
MATCH_STAT_GAIN_MIN = 1
MATCH_STAT_GAIN_MAX = 2

# 슬럼프
SLUMP_STRESS_THRESHOLD   = 60
SLUMP_HAPPY_THRESHOLD     = 20
SLUMP_CHANCE             = 0.50
SLUMP_RECOVER_STRESS     = 40
SLUMP_TRAIN_PENALTY      = 0.50  # 슬럼프 시 모든 훈련 효율 50% 감소
SLUMP_RATING_PENALTY     = -1.0

# [행복도 연동] 행복도가 낮으면 스트레스가 60에 못 미쳐도(40 이상) 슬럼프 가능.
#   - 행복도가 LOW_HAPPY 이하이면 슬럼프 스트레스 임계치를 60 → 40으로 낮춘다.
#   - 단 이 저행복 구간 발동 확률은 정규 구간보다 낮게(스트레스 60+ 만큼 흔하진 않게).
SLUMP_LOW_HAPPY          = 35   # 행복도 이 값 이하면 '저행복 슬럼프' 구간 진입
SLUMP_LOW_HAPPY_STRESS   = 40   # 저행복일 때 적용되는 낮춘 스트레스 임계치
SLUMP_LOW_HAPPY_CHANCE   = 0.30 # 저행복 구간(스트레스 40~59)에서의 슬럼프 발동 확률

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

# ──────────────────────────────────────────────────────────────
# [신체 아키타입] 체형 유형. PHYSICAL_TRAIT(부상/성장 특성)와는 별개의 축.
#   - 선수의 키/체중을 결정하고, 일부 스탯을 ±로 보정한다(현실적 ±5~8).
#   - 포지션이 어떤 타입이 나올지 '확률을 기울이되' 고정하진 않는다.
#     → 윙어인데 포켓로켓(메시형)이 나오거나, 작은데 종결자 체급(트라오레)도
#       드물게 가능. 현실의 다양성을 재현.
#
# 스탯 보정(stat_bias)은 '시작 스탯 + 잠재(max) 양쪽'에 더해진다.
#   양수 = 그 스탯이 또래보다 높게 시작/성장, 음수 = 낮게.
#   몸싸움 계열(strength/heading/jump)은 크게, 부차 스탯은 작게 둬서
#   "키 작으면 몸싸움 밀린다"가 분명히 체감되되 개성은 유지되게 한다.
# ──────────────────────────────────────────────────────────────
BODY_TYPES = {
    "하드웨어 종결자형": {
        "desc": "압도적 체격으로 육체적으로 제압. 포스트플레이·제공권.",
        "height": (186, 196),
        "weight": (84, 100),
        "stat_bias": {
            "strength": +8, "heading": +7, "jump": +6,
            "speed": -5, "dribbling": -5, "stamina": -2,
        },
    },
    "음속 지배자형": {
        "desc": "폭발적인 속도로 측면을 파괴. 치고 달리기·역습.",
        "height": (172, 186),
        "weight": (66, 76),
        "stat_bias": {
            "speed": +8, "stamina": +4, "dribbling": +3,
            "strength": -5, "heading": -4, "jump": -2,
        },
    },
    "포켓 로켓형": {
        "desc": "작지만 단단하고 민첩. 좁은 공간 탈압박·방향전환.",
        "height": (165, 175),
        "weight": (63, 73),
        "stat_bias": {
            "dribbling": +8, "speed": +4, "setpiece": +2,
            "strength": -6, "heading": -7, "jump": -5,
        },
    },
    "인간 발전기형": {
        "desc": "공수 양면 활동량과 밸런스. 육각형 미드필더.",
        "height": (175, 185),
        "weight": (70, 80),
        "stat_bias": {
            "stamina": +7, "passing": +3, "tackling": +2,
            "confidence": +1,   # 큰 약점 없이 골고루(보정폭 작게)
        },
    },
}
BODY_TYPE_NAMES = list(BODY_TYPES.keys())

# 포지션별 아키타입 등장 확률(가중치). 합이 100이 아니어도 됨(상대 비율).
#   정석 타입에 무게를 싣되, 다른 타입도 0이 아니게 둬서 이질적 선수를 허용한다.
#   순서: [종결자, 음속, 포켓로켓, 발전기]
BODY_TYPE_WEIGHTS_BY_POS = {
    "GK":  [55, 10,  5, 30],   # 키 큰 편
    "CB":  [60, 10,  3, 27],   # 종결자 다수
    "LB":  [10, 55, 20, 15],   # 측면=음속
    "RB":  [10, 55, 20, 15],
    "CDM": [30, 10, 10, 50],   # 발전기 다수
    "CM":  [12, 13, 20, 55],   # 발전기 중심
    "CAM": [ 6, 14, 50, 30],   # 포켓로켓(창의형) 많음
    "LW":  [ 8, 52, 30, 10],   # 음속/포켓로켓
    "RW":  [ 8, 52, 30, 10],
    "CF":  [40, 18, 17, 25],   # 타깃맨~섀도우 다양
    "ST":  [48, 22, 12, 18],   # 종결자(타깃맨) 우세하되 발빠른 9번도
}

# 에이전트
AGENT_GRADES = ["F","E","D","C","B","A","S"]
AGENT_FEE_RATE = {"F":0.00,"E":0.03,"D":0.06,"C":0.10,"B":0.15,"A":0.20,"S":0.28}
AGENT_UPPER_LEAGUE_BONUS = {"F":0,"E":1,"D":1,"C":2,"B":2,"A":3,"S":3}

# 포지션
POSITIONS = ["GK","CB","LB","RB","CDM","CM","CAM","LW","RW","CF","ST"]

# 포지션 그룹: 커리어/은퇴 지표를 포지션 성격에 맞게 보여주기 위한 분류.
#   - GK: 선방/실점/선방률/무실점이 핵심 (골·어시 무의미)
#   - 수비수(DEF): 무실점·실점·평점이 핵심, 골·어시는 보조
#   - 그 외(미드/공격): 골·어시·평점이 핵심
GK_POSITIONS  = ["GK"]
DEF_POSITIONS = ["CB", "LB", "RB", "CDM"]   # 중앙·측면 수비 + 수비형 미드
ATK_POSITIONS = ["CM", "CAM", "LW", "RW", "CF", "ST"]

def position_group(pos):
    """포지션 → 'GK' / 'DEF' / 'ATK' 그룹 반환."""
    if pos in GK_POSITIONS:
        return "GK"
    if pos in DEF_POSITIONS:
        return "DEF"
    return "ATK"

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
    "stamina","speed","jump","strength","shooting","passing","dribbling",
    "tackling","heading","positioning","setpiece",
    "mental","confidence","leadership","concentration"
]

# 훈련으로 오르는 스탯 분류
PHYSICAL_STATS  = ["stamina","speed","jump","strength"]
TECHNICAL_STATS = ["shooting","passing","dribbling","tackling","heading","positioning","setpiece"]
MENTAL_STATS    = ["mental","confidence","leadership","concentration"]

STAT_KO = {
    "stamina":"체력","speed":"스피드","jump":"점프력","strength":"몸싸움",
    "shooting":"슈팅","passing":"패스","dribbling":"드리블",
    "tackling":"태클","heading":"헤딩","positioning":"포지셔닝",
    "setpiece":"세트피스","mental":"멘탈","confidence":"자신감",
    "leadership":"리더십","concentration":"집중력"
}
STAT_EN = {
    "stamina":"Stamina","speed":"Speed","jump":"Jump","strength":"Strength",
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

# 노화 (나이 → 연간 한계스탯 감소). 28세부터 현실적으로 쇠퇴 시작.
#   28~29세는 거의 체감 안 되는 미세 하락(0~1)으로 시작해,
#   30대 초반부터 누적이 쌓이고 30대 후반에 급격해지는 '점진 가속' 곡선.
#   현실의 신체능력(스피드/체력)은 28세 무렵부터 먼저 빠지기 시작한다.
#   ※ 실제 적용량은 아래 AGING_TIER_MULT(재능 등급별 배수)로 차등화된다.
#     예) normal(×1.0)은 그대로, worldclass(×0.45)는 28세에 거의 무풍,
#         limited(×1.30)는 30대 초반에 확연히 빠진다.
AGING = [(28,1),(30,2),(32,3),(34,4),(36,5),(38,6),(40,8)]

# ── 재능 등급별 노화 차등 ──────────────────────────────────────
# 같은 나이라도 월드클래스는 천천히, 무재능은 빨리 늙는다.
#   (메시·모드리치처럼 톱티어는 30대 후반에도 기량 유지, 범선수는 30대 초중반에 급락)
#   decay 에 곱하는 배수 → 1.0보다 작으면 노화 저항, 크면 가속.
AGING_TIER_MULT = {
    "worldclass": 0.45,   # 노화 매우 느림 — 30대 후반에도 정상급 유지
    "elite":      0.70,   # 느림
    "normal":     1.00,   # 표준
    "limited":    1.30,   # 빠름 — 30대 초중반에 확연히 하락
}

# ── 재능 등급별 노화 하한선(OVR floor) ─────────────────────────
# 노화로 스탯이 깎여도 '전성기 talent_cap 대비 이 비율'까지만 떨어진다.
#   톱티어는 아무리 늙어도 일정 수준 밑으론 안 내려가게(60까지 추락 방지),
#   범선수는 더 깊이 떨어질 수 있게 한다. (실제 적용: 각 스탯 _max 하한)
#   비율은 'peak 시점 _max' 기준. 예: worldclass 0.86 → 전성기 95면 최저 ~82 부근.
#   ※ 28세 시작 곡선에 맞춰 floor를 소폭 낮춰, 초반 감소가 바닥에 막히지 않고
#     실제로 체감되도록 했다(특히 평범/무재능 등급).
AGING_FLOOR_RATIO = {
    "worldclass": 0.86,   # 전성기의 86%까지 하락 (예: 96 → 최저 ~83)
    "elite":      0.79,
    "normal":     0.70,
    "limited":    0.58,   # 깊게 하락 (예: 78 → 최저 ~45)
}

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
CONT_TEAMS = 24   # 대륙컵 본선 24개국 (6조 × 4팀)
CONT_GROUPS = 6
# 24개국 포맷: 각 조 1·2위(12팀) + 성적 좋은 3위 중 상위 4팀 = 16강
CONT_BEST_THIRDS = 4

# 월드컵 대륙별 쿼터 (합 32, 개최국 포함 / 오세아니아는 아시아 편입)
WC_QUOTA = {"유럽": 13, "남미": 4, "북미": 4, "아프리카": 5, "아시아": 6}

# [2002년 확대] 이 해부터 본선 64개국·16조 → 조별 후 32강 시작.
WC_EXPAND_YEAR  = 2002
WC_TEAMS_BIG    = 64
WC_GROUPS_BIG   = 16
# 64개국 대륙별 쿼터 (합 64, 개최국 포함)
WC_QUOTA_BIG = {"유럽": 24, "남미": 8, "북미": 8, "아프리카": 12, "아시아": 12}

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

    # ── [부유도 하향] 국대는 강하지만 자국 리그는 가난한 나라들 ──────
    # (국대 등급 grade를 그대로 부유도로 쓰면 연봉이 비현실적으로 부풀어,
    #  현실 연봉 수준에 맞춰 리그 부유도만 따로 낮춘다. 국대 실력은 grade 유지.)
    #
    # 아프리카 — 대부분 스타가 유럽 진출, 자국 리그 급여는 낮음.
    #   현실 기준: 이집트 알아흘리 ~10-15억(C급1부 OVR75≈10.7억),
    #             남아공 선다운스 ~5-8억(D급), 콩고민주공화국 최고 ~1억(E급).
    "이집트":            "C",   # 알아흘리/피라미드 = 아프리카 최상위 부유 리그
    "모로코":            "C",   # 보톨라 — 중동자본 유입, 비교적 부유 (국대는 S 유지)
    "남아프리카공화국":   "D",   # 선다운스 등 일부 부유, 평균은 중하위
    "콩고 민주 공화국":   "E",   # 리나푸트 — 최상위팀만 ~1억, 대다수 1천만원대
    "나이지리아":        "E",   # 국대 A급이나 리그 급여 낮음
    "세네갈":            "E",
    "알제리":            "D",   # 자국 리그 비교적 운영, 그래도 유럽 대비 낮음
    "코트디부아르":      "E",
    "카메룬":            "E",
    "튀니지":            "D",
    #
    # 남미 — 브라질/아르헨티나도 자국 리그 급여는 유럽 빅리그보다 한참 낮다.
    #   (게임상 OVR이 EPL급으로 높게 생성되므로, 부유도를 낮춰 절대 수치를 맞추되
    #    OVR 프리미엄은 살린다 → 같은 리그 안에선 OVR 높을수록 더 받음.)
    #   현실: 브라질 1부 평균 ~9억, 아르헨티나 ~5억.
    "브라질":            "B",   # 세리에A — 톱클럽은 OVR 프리미엄으로 더 받음
    "아르헨티나":        "C",   # 보카/리버 외엔 급여 낮음
    "콜롬비아":          "D",
    "우루과이":          "D",
    "에콰도르":          "E",
    "파라과이":          "E",
    "베네수엘라":        "F",
    "칠레":              "D",
    "페루":              "E",
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